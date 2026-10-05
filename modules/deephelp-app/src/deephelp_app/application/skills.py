"""版本化业务Skill的登记与受控执行，复用SOP的唯一工具派发链。

SkillDefinition描述可复用输入/输出/许可；SOP仍负责分支、事实合并和审批。
机器schema只允许现有只读工具，说明文本不执行Python/shell，也不增加授权。
SkillSession属于一次execute；await使用父预算/原适配器，退出不关闭共享客户端。
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from deephelp_app.adapters.mcp.business.mcp_protocol import (
    canonical,
    tool_schema,
    validate_arguments,
    validate_observation,
)
from deephelp_app.application.sop_governance import FACT_TYPES, TOOL_GRANTS
from deephelp_app.domain.checks import validate_question_access, validate_tool_result
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import DTO, ErrorCode, ErrorDetail, Identifier, RawText
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.skills import SkillReceipt
from deephelp_app.domain.models.tools import (
    ToolName,
    ToolRequest,
    ToolResult,
    ToolStatus,
    tool_parameters_hash,
)


class SkillDefinition(DTO):
    """不可变机器描述；意图许可只能取现有代码授权的交集。"""

    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,63}$")
    goal: RawText
    applicable_intents: tuple[IntentCode, ...] = Field(min_length=1)
    tool: ToolName
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    missing_behavior: Literal["provide_slots"] = "provide_slots"
    failure_behavior: Literal["stop"] = "stop"
    max_steps: int = Field(default=1, ge=1, le=1)

    @model_validator(mode="after")
    def machine_contract(self) -> SkillDefinition:
        if self.tool not in FACT_TYPES or any(
            self.tool not in TOOL_GRANTS.get(code, ()) for code in self.applicable_intents
        ):
            raise ValueError("Skill cannot extend intent tool grants")
        expected_output = {"type": "object", "properties": FACT_TYPES[self.tool]}
        if self.input_schema != tool_schema(self.tool) or self.output_schema != expected_output:
            raise ValueError("Skill schema must match the registered tool/fact contract")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()


class SkillStep(DTO):
    """精确子版本与来源映射；来源只可能是原槽位或前一实际观察的字段。"""

    step_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    skill_id: Identifier
    version: Identifier
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    inputs: dict[str, str]


class SkillComposition(DTO):
    """有限只读组合；当前只支持直接叶子，递归组合与写步骤明确拒绝。"""

    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,63}$")
    goal: RawText
    applicable_intents: tuple[IntentCode, ...] = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_tools: tuple[ToolName, ...] = Field(min_length=1)
    steps: tuple[SkillStep, ...] = Field(min_length=1, max_length=4)
    max_depth: Literal[2] = 2
    max_steps: int = Field(default=2, ge=1, le=4)
    max_attempts: int = Field(default=16, ge=1, le=16)
    total_seconds: int = Field(default=80, ge=1, le=80)
    missing_behavior: Literal["provide_slots"] = "provide_slots"
    failure_behavior: Literal["stop"] = "stop"

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()


class SkillRegistry(DTO):
    """一个完整、只读注册表快照；稳定ID/version的字节不得原位替换。"""

    version: Identifier
    definitions: tuple[SkillDefinition, ...] = Field(min_length=1, max_length=16)
    compositions: tuple[SkillComposition, ...] = Field(default=(), max_length=8)

    def entries(self) -> tuple[SkillDefinition | SkillComposition, ...]:
        return (*self.definitions, *self.compositions)

    @model_validator(mode="after")
    def unique(self) -> SkillRegistry:
        keys = [(d.skill_id, d.version) for d in self.entries()]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate Skill identity/version")
        for code in TOOL_GRANTS:
            tools = [d.tool for d in self.definitions if code in d.applicable_intents]
            if len(set(tools)) != len(tools):
                raise ValueError("Ambiguous deterministic Skill selection")
            if sum(code in d.applicable_intents for d in self.compositions) > 1:
                raise ValueError("Ambiguous composition selection")
        for composition in self.compositions:
            if composition.input_schema != tool_schema(ToolName.CHECK_COUPON):
                raise ValueError("Composition needs the registered confirmed order/coupon schema")
            if len(composition.steps) > composition.max_steps or len(
                {s.step_id for s in composition.steps}
            ) != len(composition.steps):
                raise ValueError("Composition step limit or duplicate step")
            available = {"slot." + key: "text" for key in composition.input_schema["required"]}
            output: dict[str, str] = {}
            seen_tools: set[ToolName] = set()
            for step in composition.steps:
                if any(d.skill_id == step.skill_id for d in self.compositions):
                    raise ValueError("Recursive composition/cycle exceeds supported depth")
                try:
                    child = self.resolve(step.skill_id, step.version, step.digest)
                except AppError:
                    raise ValueError("Missing or changed child Skill closure") from None
                if child.tool in seen_tools:
                    raise ValueError("Repeated readonly query in composition")
                seen_tools.add(child.tool)
                if not set(composition.applicable_intents) <= set(child.applicable_intents):
                    raise ValueError("Child Skill cannot extend parent intent scope")
                if child.tool not in composition.allowed_tools or any(
                    child.tool not in TOOL_GRANTS.get(code, ())
                    for code in composition.applicable_intents
                ):
                    raise ValueError("Child cannot extend parent tool grants")
                if set(step.inputs) != set(child.input_schema["required"]) or any(
                    available.get(source) != "text" for source in step.inputs.values()
                ):
                    raise ValueError(
                        "Input map must use earlier compatible facts or confirmed slots"
                    )
                fields = {
                    step.step_id + "." + key: value for key, value in FACT_TYPES[child.tool].items()
                }
                available.update(fields)
                output.update(fields)
            if composition.output_schema != {"type": "object", "properties": output}:
                raise ValueError("Composition output must match its exact dependency closure")
            if any(
                t not in TOOL_GRANTS.get(code, ())
                for t in composition.allowed_tools
                for code in composition.applicable_intents
            ):
                raise ValueError("Composition cannot extend code grants")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.content())).hexdigest()

    def content(self) -> dict[str, Any]:
        """空组合不进入旧v1摘要，保留F1不可变版本身份。"""
        return self.model_dump(
            mode="json", exclude={"compositions"} if not self.compositions else set()
        )

    def resolve(self, skill_id: str, version: str, digest: str) -> SkillDefinition:
        """精确三元引用；模型/用户不能用未知版本代替已登记内容。"""
        for definition in self.definitions:
            if (definition.skill_id, definition.version, definition.digest) == (
                skill_id,
                version,
                digest,
            ):
                return definition
        raise AppError(ErrorCode.VERSION_CONFLICT, "Unknown or changed Skill reference")

    def select(self, code: IntentCode, tool: ToolName) -> SkillDefinition | None:
        return next(
            (d for d in self.definitions if code in d.applicable_intents and d.tool == tool), None
        )

    def composition_for(self, code: IntentCode) -> SkillComposition | None:
        return next((d for d in self.compositions if code in d.applicable_intents), None)


def load_skills(path: Path | None = None) -> SkillRegistry:
    """加载受控本地/包内机器JSON，不访问远程站点或执行说明正文。"""
    content = (
        path.read_text(encoding="utf-8")
        if path
        else files("deephelp_app.resources")
        .joinpath("sop_data/skills-v2.json")
        .read_text(encoding="utf-8")
    )
    return SkillRegistry.model_validate_json(content)


def skill_history() -> tuple[SkillRegistry, ...]:
    """F1旧问题继续读取原机器字节；不重新绑定到当前组合。"""
    return (
        SkillRegistry.model_validate_json(
            files("deephelp_app.resources")
            .joinpath("sop_data/skills-v1.json")
            .read_text(encoding="utf-8")
        ),
    )


def register_skills(directory: Path, registry: SkillRegistry) -> Path:
    """单writer不可变登记；相同Skill/version不同内容拒绝，重复相同登记幂等。"""
    directory.mkdir(parents=True, exist_ok=True)
    # 先检查所有冲突，避免后一个版本冲突时留下前一个新登记。
    for definition in registry.entries():
        path = directory / (definition.skill_id + "-" + definition.version + ".json")
        content = canonical(definition.model_dump(mode="json")).decode()
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ValueError("Same Skill version cannot be replaced")
    for definition in registry.entries():
        path = directory / (definition.skill_id + "-" + definition.version + ".json")
        if not path.exists():
            with path.open("x", encoding="utf-8") as stream:
                stream.write(canonical(definition.model_dump(mode="json")).decode())
    snapshot = directory / (registry.digest + ".json")
    if not snapshot.exists():
        with snapshot.open("x", encoding="utf-8") as stream:
            stream.write(canonical(registry.content()).decode())
    return snapshot


class SkillSession:
    """一次SOP的受控Skill调用拥有者；每步共享同一ExecutionBudget和工具派发口。"""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        tool_version: str = "m06-readonly-v1",
        evidence_version: str = "business-fixtures-v1",
    ) -> None:
        self.registry = SkillRegistry.model_validate(registry.model_dump())
        self.tool_version = tool_version
        self.evidence_version = evidence_version
        self.receipts: list[SkillReceipt] = []

    async def lookup(
        self,
        question: Question,
        context: RequestEnvelope,
        run_id: str,
        node_id: str,
        definition: SkillDefinition,
        arguments: dict[str, Any],
        budget: ExecutionBudget,
        execute: Callable[[], Awaitable[ToolResult]],
        *,
        parent_id: str | None = None,
        input_sources: dict[str, str] | None = None,
    ) -> ToolResult:
        """核对版本/输入后await原执行链；失败/取消不重跑，不创造新deadline。"""
        validate_question_access(context, question)
        self.registry.resolve(definition.skill_id, definition.version, definition.digest)
        if question.versions.skills != self.registry.digest:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Pinned Skill registry differs")
        if question.active_intent not in definition.applicable_intents:
            raise AppError(ErrorCode.FORBIDDEN, "Skill not granted to current intent")
        required = tool_schema(definition.tool)["required"]
        slots = tuple(e for e in question.entities if e.name.value in required)
        expected = {e.name.value: e.value for e in slots}
        if (
            set(expected) != set(required)
            or arguments != expected
            or any(
                c.previous.name.value in required and c.resolution == "unresolved"
                for c in question.conflicts
            )
        ):
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Skill requires unchanged verified slots")
        budget.remaining_seconds()
        receipt = SkillReceipt(
            execution_id="skill-"
            + hashlib.sha256((run_id + ":" + node_id).encode()).hexdigest()[:32],
            run_id=run_id,
            question_id=question.question_id,
            node_id=node_id,
            registry_digest=self.registry.digest,
            skill_id=definition.skill_id,
            version=definition.version,
            digest=definition.digest,
            slots=slots,
            parent_id=parent_id,
            input_sources=input_sources or {key: "slot." + key for key in expected},
            status="RUNNING",
            started_at=datetime.now(UTC),
        )
        if any(r.execution_id == receipt.execution_id for r in self.receipts):
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Completed or stopped Skill cannot be rerun")
        position = len(self.receipts)
        self.receipts.append(receipt)
        try:
            result = await execute()
            parameters = validate_arguments(definition.tool.value, arguments)
            request = ToolRequest(
                operation_id=result.operation_id,
                run_id=run_id,
                question_id=question.question_id,
                identity=context.identity,
                tool_name=definition.tool,
                tool_version=self.tool_version,
                parameters=parameters,
                parameters_hash=tool_parameters_hash(parameters),
                budget=budget.snapshot(),
            )
            validate_tool_result(request, result)
            if result.status == ToolStatus.SUCCEEDED:
                try:
                    validate_observation(request, result, evidence_version=self.evidence_version)
                except ValueError, IndexError:
                    raise AppError(
                        ErrorCode.MODEL_OUTPUT_INVALID, "Unverified Skill output"
                    ) from None
            status = "COMPLETED" if result.status == ToolStatus.SUCCEEDED else "FAILED"
            self.receipts[position] = SkillReceipt.model_validate(
                {
                    **receipt.model_dump(),
                    "status": status,
                    "observation": result,
                    "error": result.error,
                    "finished_at": datetime.now(UTC),
                }
            )
            return result
        except asyncio.CancelledError:
            self.receipts[position] = receipt.model_copy(
                update={"status": "CANCELLED", "finished_at": datetime.now(UTC)}
            )
            raise

        except AppError as exc:
            self.receipts[position] = receipt.model_copy(
                update={
                    "status": "UNKNOWN" if exc.code == ErrorCode.TIMEOUT else "FAILED",
                    "error": ErrorDetail(code=exc.code, message=exc.safe_message),
                    "finished_at": datetime.now(UTC),
                }
            )
            raise

    async def compose(
        self,
        question: Question,
        context: RequestEnvelope,
        run_id: str,
        composition: SkillComposition,
        budget: ExecutionBudget,
        execute: Callable[[SkillDefinition, dict[str, Any]], Awaitable[ToolResult]],
    ) -> dict[ToolName, ToolResult]:
        """顺序执行有限叶子并回收原观察；第一步失败/取消即停止，不能重跑作恢复。

        原请求拥有budget和连接；limit_attempts/timeout只收窄原计量，不复制额度。
        映射引用前一步实际Fact，同时叶子仍核对原槽位，所以输出不能改已确认订单。
        """
        validate_question_access(context, question)
        if (
            question.versions.skills != self.registry.digest
            or composition not in self.registry.compositions
        ):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Composition is not pinned")
        if question.active_intent not in composition.applicable_intents:
            raise AppError(ErrorCode.FORBIDDEN, "Composition intent not allowed")
        values: dict[str, Any] = {"slot." + e.name.value: e.value for e in question.entities}
        if any("slot." + key not in values for key in composition.input_schema["required"]):
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Composition lacks confirmed slots")
        parent = SkillReceipt(
            execution_id="skill-"
            + hashlib.sha256((run_id + ":" + composition.skill_id).encode()).hexdigest()[:32],
            run_id=run_id,
            question_id=question.question_id,
            node_id=composition.skill_id,
            registry_digest=self.registry.digest,
            skill_id=composition.skill_id,
            version=composition.version,
            digest=composition.digest,
            slots=question.entities,
            status="RUNNING",
            started_at=datetime.now(UTC),
        )
        if any(r.execution_id == parent.execution_id for r in self.receipts):
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Composition cannot be rerun as recovery")
        position = len(self.receipts)
        self.receipts.append(parent)
        children: list[str] = []
        evidence: list[str] = []
        results: dict[ToolName, ToolResult] = {}
        try:
            with budget.limit_attempts(composition.max_attempts):
                async with asyncio.timeout(
                    min(composition.total_seconds, budget.remaining_seconds())
                ):
                    for step in composition.steps:
                        child = self.registry.resolve(step.skill_id, step.version, step.digest)
                        arguments = {key: values[source] for key, source in step.inputs.items()}
                        # partial把本步定义/参数固定，不把下一轮变量绑定给尚未结束的await。
                        result = await self.lookup(
                            question,
                            context,
                            run_id,
                            step.step_id,
                            child,
                            arguments,
                            budget,
                            partial(execute, child, arguments),
                            parent_id=parent.execution_id,
                            input_sources=step.inputs,
                        )
                        children.append(self.receipts[-1].execution_id)
                        results[child.tool] = result
                        evidence.extend(e.evidence_id for e in result.evidence_refs)
                        if result.status != ToolStatus.SUCCEEDED:
                            raise AppError(
                                result.error.code
                                if result.error
                                else ErrorCode.MODEL_OUTPUT_INVALID,
                                result.error.message if result.error else "Child Skill stopped",
                            )
                        values.update({step.step_id + "." + f.name: f.value for f in result.facts})
            self.receipts[position] = SkillReceipt.model_validate(
                {
                    **parent.model_dump(),
                    "status": "COMPLETED",
                    "children": children,
                    "evidence_ids": evidence,
                    "finished_at": datetime.now(UTC),
                }
            )
            return results
        except asyncio.CancelledError:
            self.receipts[position] = parent.model_copy(
                update={
                    "status": "CANCELLED",
                    "children": tuple(children),
                    "evidence_ids": tuple(evidence),
                    "finished_at": datetime.now(UTC),
                }
            )
            raise
        except (AppError, TimeoutError) as exc:
            code = exc.code if isinstance(exc, AppError) else ErrorCode.TIMEOUT
            self.receipts[position] = parent.model_copy(
                update={
                    "status": "UNKNOWN" if code == ErrorCode.TIMEOUT else "FAILED",
                    "children": tuple(children),
                    "evidence_ids": tuple(evidence),
                    "error": ErrorDetail(
                        code=code,
                        message=exc.safe_message
                        if isinstance(exc, AppError)
                        else "Composition deadline exhausted",
                    ),
                    "finished_at": datetime.now(UTC),
                }
            )
            raise
