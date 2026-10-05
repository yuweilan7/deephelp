"""版本化业务Skill的登记与受控执行，复用SOP的唯一工具派发链。

SkillDefinition描述可复用输入/输出/许可；SOP仍负责分支、事实合并和审批。
机器schema只允许现有只读工具，说明文本不执行Python/shell，也不增加授权。
SkillSession属于一次execute；await使用父预算/原适配器，退出不关闭共享客户端。
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
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
            self.tool not in TOOL_GRANTS[code] for code in self.applicable_intents
        ):
            raise ValueError("Skill cannot extend intent tool grants")
        expected_output = {"type": "object", "properties": FACT_TYPES[self.tool]}
        if self.input_schema != tool_schema(self.tool) or self.output_schema != expected_output:
            raise ValueError("Skill schema must match the registered tool/fact contract")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()


class SkillRegistry(DTO):
    """一个完整、只读注册表快照；稳定ID/version的字节不得原位替换。"""

    version: Identifier
    definitions: tuple[SkillDefinition, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique(self) -> SkillRegistry:
        keys = [(d.skill_id, d.version) for d in self.definitions]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate Skill identity/version")
        for code in TOOL_GRANTS:
            tools = [d.tool for d in self.definitions if code in d.applicable_intents]
            if len(set(tools)) != len(tools):
                raise ValueError("Ambiguous deterministic Skill selection")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()

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


def load_skills(path: Path | None = None) -> SkillRegistry:
    """加载受控本地/包内机器JSON，不访问远程站点或执行说明正文。"""
    content = (
        path.read_text(encoding="utf-8")
        if path
        else files("deephelp_app.resources")
        .joinpath("sop_data/skills-v1.json")
        .read_text(encoding="utf-8")
    )
    return SkillRegistry.model_validate_json(content)


def register_skills(directory: Path, registry: SkillRegistry) -> Path:
    """单writer不可变登记；相同Skill/version不同内容拒绝，重复相同登记幂等。"""
    directory.mkdir(parents=True, exist_ok=True)
    for definition in registry.definitions:
        path = directory / (definition.skill_id + "-" + definition.version + ".json")
        content = canonical(definition.model_dump(mode="json")).decode()
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ValueError("Same Skill version cannot be replaced")
        else:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
    snapshot = directory / (registry.digest + ".json")
    if not snapshot.exists():
        with snapshot.open("x", encoding="utf-8") as stream:
            stream.write(canonical(registry.model_dump(mode="json")).decode())
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
            status="RUNNING",
            started_at=datetime.now(UTC),
        )
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
