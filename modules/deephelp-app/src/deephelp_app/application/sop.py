"""围绕固定SOP做有界工具选择，参数、分支、事实与收口均由代码守卫。

Conversation传入已绑定意图/版本的问题、可信请求和原预算。execute先拒绝缺槽或错版本。
_loop承接简单只读SOP，_flow执行已发布无环节点；_lookup统一工具派发、证据校验与有限重试。
模型只提议当前允许的动作，不能自由改变已确认编号、下一分支或审批状态。
"""

import asyncio
import json
from functools import partial
from uuid import uuid4

from pydantic import ValidationError

from deephelp_app.adapters.mcp.business.mcp_protocol import (
    tool_schema,
    validate_arguments,
    validate_observation,
)
from deephelp_app.adapters.observability.trace import observe_sop_node
from deephelp_app.application.collaboration import AgentPolicy, CollaborationSession
from deephelp_app.application.skills import (
    SkillDefinition,
    SkillRegistry,
    SkillSession,
    load_skills,
    skill_history,
)
from deephelp_app.application.sop_config import SOPBranch, SOPDefinition, load_prompt, load_sops
from deephelp_app.application.sop_governance import GovernedSOP, SOPRegistry
from deephelp_app.domain.checks import validate_question_access, validate_tool_result
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode, ErrorDetail, NextAction, QuestionStatus
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.evidence import Fact, FactKind
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import ChatMessage, ChatRequest, ModelTool, ModelToolCall
from deephelp_app.domain.models.release import VersionManifest
from deephelp_app.domain.models.sop import SOPPlan, SOPResult, SOPStatus
from deephelp_app.domain.models.tools import (
    ToolName,
    ToolParameters,
    ToolRequest,
    ToolResult,
    ToolStatus,
    tool_parameters_hash,
)
from deephelp_app.domain.ports import AgentTaskJournalPort, ChatPort, ToolPort

CONTROL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class SOPExecutor:
    # 模型只建议动作；本用例强制身份/参数/版本/事实和有界退出。
    def __init__(
        self,
        model: ChatPort,
        tools: ToolPort,
        *,
        definitions: dict[IntentCode, SOPDefinition] | None = None,
        registry: SOPRegistry | None = None,
        history: tuple[SOPRegistry, ...] = (),
        evidence_version: str = "business-fixtures-v1",
        agent_policy: AgentPolicy | None = None,
        agent_journal: AgentTaskJournalPort | None = None,
        skills: SkillRegistry | None = None,
        skills_history: tuple[SkillRegistry, ...] = (),
        skills_enabled: bool = True,
    ) -> None:
        if evidence_version not in {"business-fixtures-v1", "business-postgres-v1"}:
            raise ValueError("Unknown business evidence source")
        self.evidence_version = evidence_version
        self.agent_policy = (
            AgentPolicy.model_validate(agent_policy.model_dump()) if agent_policy else None
        )
        self.agent_journal = agent_journal
        self.skills = SkillRegistry.model_validate(skills.model_dump()) if skills else load_skills()
        self.skills_history = (self.skills, *skills_history, *skill_history())
        self.skills_enabled = skills_enabled
        self.model = model
        self.tools = tools
        registry = SOPRegistry.model_validate(registry.model_dump()) if registry else None
        history = tuple(SOPRegistry.model_validate(s.model_dump()) for s in history)
        self.registry = registry
        self.history = tuple(
            {s.snapshot_hash: s for s in ((registry,) if registry else ()) + history}.values()
        )
        self.definitions: dict[IntentCode, SOPDefinition | GovernedSOP] = dict(
            load_sops()
            if definitions is None
            else {
                code: SOPDefinition.model_validate(d.model_dump())
                for code, d in definitions.items()
            }
        )
        if registry:
            if definitions is not None:
                raise ValueError("Choose legacy definitions or a governed registry")
            self.definitions = {d.intent_code: d for d in registry.definitions}
        if any(code != d.intent_code for code, d in self.definitions.items()):
            raise ValueError("SOP index does not match configured intent")
        self.prompt = load_prompt()

    def definition_for(self, question: Question) -> SOPDefinition | GovernedSOP | None:
        if question.active_intent is None:
            return None
        if self.registry is None:
            return self.definitions.get(question.active_intent)
        if question.versions.sop_snapshot:
            for snapshot in self.history:
                if snapshot.snapshot_hash == question.versions.sop_snapshot:
                    d = snapshot.definition(question.active_intent)
                    if snapshot.pin(question.versions, question.active_intent) != question.versions:
                        raise AppError(
                            ErrorCode.VERSION_CONFLICT, "Pinned SOP snapshot fields differ"
                        )
                    return d
            raise AppError(ErrorCode.VERSION_CONFLICT, "Pinned SOP snapshot is unavailable")
        if question.versions.sop:
            old = load_sops().get(question.active_intent)
            if old and old.version == question.versions.sop:
                return old
        return self.definitions.get(question.active_intent)

    def pin_versions(self, versions: VersionManifest, code: IntentCode) -> VersionManifest:
        if self.registry and self.skills_enabled:
            versions = versions.model_copy(update={"skills": self.skills.digest})
        if self.agent_policy:
            versions = versions.model_copy(update={"collaboration": self.agent_policy.digest})
        return (
            self.registry.pin(versions, code)
            if self.registry
            else versions.model_copy(update={"sop": self.definitions[code].version})
        )

    async def execute(
        self,
        question: Question,
        budget: ExecutionBudget,
        *,
        context: RequestEnvelope,
        run_id: str,
    ) -> SOPResult:
        # Authorize before missing-slot reporting or model I/O. The entry point owns login auth.
        """校验问题、版本与槽位后执行其固定SOP，返回已验证的观察与结论。

        context是可信请求，run_id是原领取标识；调用和重试共用budget。
        缺槽立即返回WAITING_SLOT，不消耗工具；超时或领域失败转换成失败结果，取消仍抛出。
        """
        validate_question_access(context, question)
        skill_registry = next(
            (s for s in self.skills_history if s.digest == question.versions.skills), None
        )
        if question.versions.skills and skill_registry is None:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Pinned Skill registry is unavailable")
        definition = self.definition_for(question)
        if definition is None:
            raise AppError(ErrorCode.NO_SOP, "No configured SOP for the selected intent")
        if question.versions.sop != definition.version:
            raise AppError(ErrorCode.VERSION_CONFLICT, "SOP version differs from pinned question")
        if isinstance(definition, GovernedSOP) and not question.versions.sop_snapshot:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Governed SOP requires a pinned registry")
        if question.status not in {QuestionStatus.ACTIVE, QuestionStatus.WAITING_SLOT}:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Question cannot enter SOP execution")
        values = {e.name.value: e.value for e in question.entities}
        # expected来自已确认槽位，模型建议不能覆盖订单号或引入其他主体参数。
        unresolved = {c.previous.name for c in question.conflicts if c.resolution == "unresolved"}
        missing = tuple(
            s for s in definition.required_slots if s.value not in values or s in unresolved
        )
        if missing:
            return SOPResult(
                status=SOPStatus.WAITING_SLOT,
                sop_id=definition.sop_id,
                sop_version=definition.version,
                missing_slots=missing,
                next_action=NextAction.PROVIDE_SLOTS,
                reason="missing_or_conflicting_slots",
            )
        if question.status != QuestionStatus.ACTIVE:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Complete question must be active before tools"
            )
        calls: list[str] = []
        skills = (
            SkillSession(
                skill_registry,
                tool_version=definition.tool_version,
                evidence_version=self.evidence_version,
            )
            if skill_registry
            else None
        )
        collaboration = None
        if context.execution_mode == "collaboration":
            if self.agent_policy is None or not isinstance(definition, GovernedSOP):
                raise AppError(
                    ErrorCode.FORBIDDEN, "Collaboration requires registered governed roles"
                )
            collaboration = CollaborationSession(self.model, self.agent_policy, self.agent_journal)
        try:
            async with asyncio.timeout(
                min(
                    definition.limits.total_seconds,
                    budget.remaining_seconds(),
                    collaboration.policy.total_seconds
                    if collaboration
                    else definition.limits.total_seconds,
                )
            ):
                if isinstance(definition, GovernedSOP):
                    result = await self._flow(
                        definition, question, context, run_id, budget, calls, collaboration, skills
                    )
                    return result.model_copy(
                        update={
                            "agent_receipts": tuple(collaboration.receipts)
                            if collaboration
                            else (),
                            "skill_receipts": tuple(skills.receipts) if skills else (),
                        }
                    )
                return await self._loop(definition, question, context, run_id, budget, calls)
        except TimeoutError:
            result = self._failed(
                definition, calls, ErrorCode.BUDGET_EXHAUSTED, "SOP deadline exhausted"
            )
            return result.model_copy(
                update={
                    "agent_receipts": tuple(collaboration.receipts) if collaboration else (),
                    "skill_receipts": tuple(skills.receipts) if skills else (),
                }
            )
        except AppError as exc:
            result = self._failed(definition, calls, exc.code, exc.safe_message)
            return result.model_copy(
                update={
                    "agent_receipts": tuple(collaboration.receipts) if collaboration else (),
                    "skill_receipts": tuple(skills.receipts) if skills else (),
                }
            )

    @staticmethod
    def _failed(
        d: SOPDefinition | GovernedSOP, calls: list[str], code: ErrorCode, message: str
    ) -> SOPResult:
        return SOPResult(
            status=SOPStatus.FAILED,
            sop_id=d.sop_id,
            sop_version=d.version,
            tool_call_ids=tuple(calls),
            next_action=NextAction.CONTACT_SUPPORT,
            reason="execution_stopped",
            error=ErrorDetail(code=code, message=message),
        )

    async def _loop(
        self,
        d: SOPDefinition | GovernedSOP,
        question: Question,
        context: RequestEnvelope,
        run_id: str,
        budget: ExecutionBudget,
        calls: list[str],
    ) -> SOPResult:
        assert isinstance(d, SOPDefinition)
        values = {e.name.value: e.value for e in question.entities}
        expected = {slot.value: values[slot.value] for slot in d.required_slots}
        tools = [ModelTool(name=t.value, parameters=tool_schema(t)) for t in d.allowed_tools]
        tools.extend(
            [
                ModelTool(
                    name="finish_sop",
                    description="End after verified lookup; code evaluates SOP.",
                    parameters=CONTROL_SCHEMA,
                ),
                ModelTool(
                    name="handoff_sop",
                    description="Stop and request human support.",
                    parameters=CONTROL_SCHEMA,
                ),
            ]
        )
        messages = [
            ChatMessage(
                role="system",
                content=self.prompt
                + "\nTRUSTED SOP:\n"
                + d.model_dump_json()
                + "\nVERIFIED SLOTS:\n"
                + json.dumps(expected),
            ),
            ChatMessage(
                role="user",
                content=json.dumps({"untrusted_user_text": context.raw_text}, ensure_ascii=False),
            ),
        ]
        observed: ToolResult | None = None
        seen: set[str] = set()
        for _ in range(d.limits.max_steps):
            try:
                async with asyncio.timeout(min(d.limits.model_seconds, budget.remaining_seconds())):
                    selected = await self.model.chat(
                        # 等模型建议，当前请求Task让出loop；返回后仍逐项验证动作与参数。
                        ChatRequest(
                            messages=messages,
                            tools=tools,
                            max_output_tokens=1024,
                            tool_choice="finish_sop" if observed is not None else None,
                        ),
                        budget,
                    )
            except TimeoutError:
                raise AppError(ErrorCode.TIMEOUT, "SOP model selection timed out") from None
            if selected.finish_reason == "length" or len(selected.tool_calls) != 1:
                raise AppError(
                    ErrorCode.MODEL_OUTPUT_INVALID, "SOP requires exactly one complete action"
                )
            action = selected.tool_calls[0]
            if action.name in {"finish_sop", "handoff_sop"}:
                if action.arguments:
                    raise AppError(
                        ErrorCode.INVALID_ARGUMENT, "SOP control signal takes no arguments"
                    )
                if action.name == "handoff_sop":
                    return self._handoff(d, calls, observed, "model_handoff")
                return self._closeout(d, calls, observed)
            try:
                name = ToolName(action.name)
            except ValueError:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "Tool is outside SOP whitelist"
                ) from None
            # 工具名来自白名单，参数必须与代码从已确认实体生成的expected完全相同。
            # 模型多传、改号或猜号都在派发前拒绝；tool_choice只是请求约束，返回仍须检查。
            if name not in d.allowed_tools or action.arguments != expected:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "Tool or parameters differ from verified SOP"
                )
            parameters = validate_arguments(action.name, action.arguments)
            fingerprint = name.value + ":" + tool_parameters_hash(parameters)
            if fingerprint in seen:
                raise AppError(ErrorCode.INVALID_ARGUMENT, "Repeated SOP tool invocation blocked")
            seen.add(fingerprint)
            observed = await self._lookup(
                d, question, context, run_id, budget, name, parameters, calls
            )
            if observed.status != ToolStatus.SUCCEEDED:
                code = observed.error.code if observed.error else ErrorCode.MODEL_OUTPUT_INVALID
                return self._failed(
                    d, calls, code, "SOP lookup failed without a verified conclusion"
                )
            # Validate even alternate ToolPort implementations before presenting observations.
            try:
                observed = ToolResult.model_validate(observed.model_dump())
            except ValidationError:
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid SOP observation") from None
            messages.extend(
                [
                    ChatMessage(role="assistant", tool_calls=[action]),
                    ChatMessage(
                        role="tool", tool_call_id=action.call_id, content=observed.model_dump_json()
                    ),
                ]
            )
        raise AppError(ErrorCode.BUDGET_EXHAUSTED, "SOP max_steps exhausted")

    async def _lookup(
        self,
        d: SOPDefinition | GovernedSOP,
        question: Question,
        context: RequestEnvelope,
        run_id: str,
        budget: ExecutionBudget,
        name: ToolName,
        parameters: ToolParameters,
        calls: list[str],
    ) -> ToolResult:
        """执行一个只读工具并绑定实际派发ID、请求参数和证据。

        ToolPort负责调用预留；此处只额外扣共享重试额度，避免同一次调用计费两次。
        只重试明确限流/不可用的只读失败，未知或超时不通过模型决定重发。
        """
        retries_at_start = budget.retries_used
        for retry in range(d.limits.retries_per_call + 1):
            if len(calls) >= d.limits.max_tool_calls:
                raise AppError(ErrorCode.BUDGET_EXHAUSTED, "SOP max_tool_calls exhausted")
            if retry:
                if (
                    budget.retries_used - retries_at_start >= d.limits.total_retries
                    or budget.retry_remaining <= 0
                ):
                    break
                # ToolPort owns call reservation; consume only the shared retry allowance here.
                budget.remaining_seconds()
                budget.retry_remaining -= 1
                budget.retries_used += 1
                if budget.on_change:
                    budget.on_change()
            request = ToolRequest(
                operation_id="sop-operation-" + uuid4().hex,
                run_id=run_id,
                question_id=question.question_id,
                identity=context.identity,
                tool_name=name,
                tool_version=d.tool_version,
                parameters=parameters,
                parameters_hash=tool_parameters_hash(parameters),
                budget=budget.snapshot(),
                timeout_seconds=d.limits.tool_seconds,
            )
            dispatched_id: str | None = None

            def remember(call_id: str) -> None:
                nonlocal dispatched_id
                if dispatched_id is not None or call_id in calls:
                    raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Duplicate actual tool call ID")
                dispatched_id = call_id
                calls.append(call_id)

            # The gateway supplies the ID before its send await: even total-deadline
            # cancellation retains the attempted invocation for ledger reconciliation.
            try:
                async with asyncio.timeout(
                    min(d.limits.tool_seconds + 0.1, budget.remaining_seconds())
                ):
                    result = await self.tools.execute(
                        # 等正式工具观察值；不能将模型正文当工具事实，也不能跳过结果守卫。
                        request,
                        question,
                        budget,
                        context=context,
                        on_dispatch=remember,
                    )
            except TimeoutError:
                raise AppError(ErrorCode.TIMEOUT, "SOP tool timed out") from None
            if result.call_id:
                if dispatched_id is None:
                    remember(result.call_id)
                elif dispatched_id != result.call_id:
                    raise AppError(
                        ErrorCode.MODEL_OUTPUT_INVALID, "Tool dispatch/result ID mismatch"
                    )
            validate_tool_result(request, result)
            if result.status == ToolStatus.SUCCEEDED:
                try:
                    validate_observation(request, result, evidence_version=self.evidence_version)
                except ValueError:
                    raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Unverified SOP facts") from None
            if result.status != ToolStatus.FAILED or not result.error or not result.error.retryable:
                return result
            # Only a definitely classified read-only failure can be retried, and never
            # unknown/timeout, auth, schema, or budget failures. No model-directed retry.
            if result.error.code not in {ErrorCode.RATE_LIMITED, ErrorCode.UPSTREAM_UNAVAILABLE}:
                return result
        return result

    async def _ready_lookup(
        self,
        bounded: GovernedSOP,
        question: Question,
        context: RequestEnvelope,
        run_id: str,
        node_id: str,
        tool: ToolName,
        expected: dict[str, object],
        budget: ExecutionBudget,
        calls: list[str],
        collaboration: CollaborationSession | None,
        request: ChatRequest,
        actions: list[ModelToolCall],
    ) -> ToolResult:
        """当前节点的唯一建议/派发入口；Skill和协作都只能包装此链，不另建查询。"""
        if collaboration:
            result = await collaboration.lookup(
                question,
                context,
                run_id,
                node_id,
                tool,
                validate_arguments(tool.value, expected),
                budget,
                partial(
                    self._lookup,
                    bounded,
                    question,
                    context,
                    run_id,
                    budget,
                    tool,
                    validate_arguments(tool.value, expected),
                    calls,
                ),
                model_seconds=bounded.limits.model_seconds,
            )
        else:
            if question.versions.skills:
                registry = next(
                    s for s in self.skills_history if s.digest == question.versions.skills
                )
                selected_skill = (
                    registry.select(question.active_intent, tool)
                    if question.active_intent
                    else None
                )
                if selected_skill:
                    # 已审核机器指令进入当前请求；仍只有原工具/schema与原槽位可执行。
                    request = request.model_copy(
                        update={
                            "messages": [
                                *request.messages[:-1],
                                ChatMessage(
                                    role="system",
                                    content="TRUSTED SKILL:\n" + selected_skill.model_dump_json(),
                                ),
                                request.messages[-1],
                            ]
                        }
                    )
            try:
                async with asyncio.timeout(
                    min(bounded.limits.model_seconds, budget.remaining_seconds())
                ):
                    selected = await self.model.chat(request, budget)
            except TimeoutError:
                raise AppError(ErrorCode.TIMEOUT, "SOP model selection timed out") from None
            if selected.finish_reason == "length" or len(selected.tool_calls) != 1:
                raise AppError(
                    ErrorCode.MODEL_OUTPUT_INVALID,
                    "Flow requires one complete ready action",
                )
            action = selected.tool_calls[0]
            actions.append(action)
            if action.name != tool.value or action.arguments != expected:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Action differs from ready node or verified slots",
                )
            result = await self._lookup(
                bounded,
                question,
                context,
                run_id,
                budget,
                tool,
                validate_arguments(action.name, expected),
                calls,
            )
        # 任务只读回收的call也属于原运行，先保留实际ID再让Skill/流程核验观察。
        if result.call_id and result.call_id not in calls:
            calls.append(result.call_id)
        return result

    async def _flow(
        self,
        d: GovernedSOP,
        question: Question,
        context: RequestEnvelope,
        run_id: str,
        budget: ExecutionBudget,
        calls: list[str],
        collaboration: CollaborationSession | None = None,
        skills: SkillSession | None = None,
    ) -> SOPResult:
        snapshot = next(
            s for s in self.history if s.snapshot_hash == question.versions.sop_snapshot
        )
        index = {n.node_id: n for n in d.nodes}
        values = {e.name.value: e.value for e in question.entities}
        messages = [
            ChatMessage(
                role="system", content=snapshot.prompt + "\nTRUSTED FLOW:\n" + d.model_dump_json()
            ),
            ChatMessage(
                role="user",
                content=json.dumps({"untrusted_user_text": context.raw_text}, ensure_ascii=False),
            ),
        ]
        observed: dict[str, ToolResult] = {}
        retries_at_start = budget.retries_used
        prefetched: dict[ToolName, ToolResult] = {}
        composition = (
            skills.registry.composition_for(question.active_intent)
            if skills and question.active_intent and not collaboration
            else None
        )
        if skills and composition:
            # 组合是原SOP的实际消费者：先订单后券，原节点只读回收完成观察。
            # 只允许本SOP真实存在的唯一lookup，不能把Skill许可扩大成任意工具派发。
            lookup_nodes = {
                tool: [n for n in d.nodes if n.kind == "lookup" and n.tool == tool]
                for tool in composition.allowed_tools
            }
            if any(len(nodes) != 1 for nodes in lookup_nodes.values()):
                raise AppError(
                    ErrorCode.FORBIDDEN, "Composition tools must match original SOP nodes"
                )

            async def dispatch(child: SkillDefinition, arguments: dict[str, object]) -> ToolResult:
                node = lookup_nodes[child.tool][0]
                request = ChatRequest(
                    messages=[
                        *messages,
                        ChatMessage(
                            role="system",
                            content=json.dumps(
                                {
                                    "ready_node": node.node_id,
                                    "required_action": child.tool.value,
                                    "verified_parameters": arguments,
                                },
                                ensure_ascii=False,
                            )
                            + "\nInvoke exactly one native tool_calls function with the "
                            "verified parameters. Do not describe the action in content "
                            "or return a JSON/code block instead of a function call.",
                        ),
                    ],
                    tools=[ModelTool(name=child.tool.value, parameters=tool_schema(child.tool))],
                    tool_choice=child.tool.value,
                    max_output_tokens=4096,
                    repair_once=True,
                )
                remaining = max(
                    0, d.limits.total_retries - (budget.retries_used - retries_at_start)
                )
                bounded = d.model_copy(
                    update={"limits": d.limits.model_copy(update={"total_retries": remaining})}
                )
                with observe_sop_node("skill." + child.skill_id, "skill", calls) as observation:
                    observation.update(
                        {
                            "skill_id": child.skill_id,
                            "skill_version": child.version,
                            "skill_digest": child.digest,
                            "skill_registry": skills.registry.digest,
                        }
                    )
                    return await self._ready_lookup(
                        bounded,
                        question,
                        context,
                        run_id,
                        node.node_id,
                        child.tool,
                        arguments,
                        budget,
                        calls,
                        None,
                        request,
                        [],
                    )

            with observe_sop_node(composition.skill_id, "skill_composition", calls):
                prefetched = await skills.compose(
                    question, context, run_id, composition, budget, dispatch
                )
            observed = {
                n.node_id: prefetched[n.tool]
                for n in d.nodes
                if n.kind == "lookup" and n.tool in prefetched
            }
        path: list[str] = []
        current = d.entry
        failure: ToolResult | None = None
        seen: set[str] = set()
        for _ in range(d.limits.max_steps):
            budget.remaining_seconds()
            node = index[current]
            path.append(current)
            with observe_sop_node(current, node.kind, calls) as node_observation:
                if node.kind == "lookup":
                    assert node.tool and node.success and node.failure
                    expected: dict[str, object] = {
                        slot: values[slot] for slot in tool_schema(node.tool)["required"]
                    }
                    request = ChatRequest(
                        messages=[
                            *messages,
                            ChatMessage(
                                role="system",
                                content=json.dumps(
                                    {
                                        "ready_node": current,
                                        "required_action": node.tool.value,
                                        "verified_parameters": expected,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\nInvoke exactly one native tool_calls function with the "
                                "verified parameters. Do not describe the action in content "
                                "or return a JSON/code block instead of a function call.",
                            ),
                        ],
                        tools=[ModelTool(name=node.tool.value, parameters=tool_schema(node.tool))],
                        tool_choice=node.tool.value,
                        max_output_tokens=4096,
                        # 一次格式纠正仍由原共享预算计费；JSON正文不进入工具执行。
                        repair_once=True,
                    )
                    fingerprint = node.tool.value + ":" + json.dumps(expected, sort_keys=True)
                    if fingerprint in seen:
                        raise AppError(ErrorCode.INVALID_ARGUMENT, "Repeated flow lookup blocked")
                    seen.add(fingerprint)
                    # All lookups share the SOP retry allowance, not one allowance per source.
                    remaining_retries = max(
                        0, d.limits.total_retries - (budget.retries_used - retries_at_start)
                    )
                    bounded = d.model_copy(
                        update={
                            "limits": d.limits.model_copy(
                                update={"total_retries": remaining_retries}
                            )
                        }
                    )
                    actions: list[ModelToolCall] = []
                    perform = partial(
                        self._ready_lookup,
                        bounded,
                        question,
                        context,
                        run_id,
                        current,
                        node.tool,
                        expected,
                        budget,
                        calls,
                        collaboration,
                        request,
                        actions,
                    )
                    skill = (
                        skills.registry.select(question.active_intent, node.tool)
                        if skills and question.active_intent
                        else None
                    )
                    result = (
                        prefetched[node.tool]
                        if node.tool in prefetched
                        else (
                            await skills.lookup(
                                question, context, run_id, current, skill, expected, budget, perform
                            )
                            if skills and skill
                            else await perform()
                        )
                    )
                    if node.tool in prefetched:
                        node_observation["source"] = "completed_skill_observation"
                        node_observation["original_tool_call_id"] = result.call_id
                    action = actions[0] if actions else None
                    if result.status != ToolStatus.SUCCEEDED:
                        node_observation["status"] = "failed"
                        node_observation["error_code"] = (
                            result.error.code.value if result.error else "MODEL_OUTPUT_INVALID"
                        )
                        node_observation["next_node"] = node.failure
                        failure = result
                        current = node.failure
                        continue
                    # 同run已完成子任务只读回收其原调用ID；不再派发工具或重复结算。
                    if result.call_id and result.call_id not in calls:
                        calls.append(result.call_id)
                    # 缓存观察也重新过当前请求/参数/版本/证据守卫，不因来自账本跳过事实校验。
                    cached_request = ToolRequest(
                        operation_id=result.operation_id,
                        run_id=run_id,
                        question_id=question.question_id,
                        identity=context.identity,
                        tool_name=node.tool,
                        tool_version=d.tool_version,
                        parameters=validate_arguments(node.tool.value, expected),
                        parameters_hash=tool_parameters_hash(
                            validate_arguments(node.tool.value, expected)
                        ),
                        budget=budget.snapshot(),
                    )
                    validate_tool_result(cached_request, result)
                    try:
                        validate_observation(
                            cached_request, result, evidence_version=self.evidence_version
                        )
                    except ValueError:
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Cached facts differ from bound task"
                        ) from None
                    observed[current] = result
                    if action is not None:
                        messages.extend(
                            [
                                ChatMessage(role="assistant", tool_calls=[action]),
                                ChatMessage(
                                    role="tool",
                                    tool_call_id=action.call_id,
                                    content=result.model_dump_json(),
                                ),
                            ]
                        )
                    current = node.success
                    node_observation["next_node"] = current
                # 分支只读取已经成功工具产生的Fact，按发布条件确定下一节点。
                # 失败边直接收口，模型不能在分支中编造事实或跳到尚未准备的lookup。
                elif node.kind == "branch":
                    facts = {
                        key + "." + f.name: f.value
                        for key, result in observed.items()
                        for f in result.facts
                    }
                    assert node.default
                    try:
                        current = next(
                            (r.target for r in node.routes if r.condition.matches(facts)),
                            node.default,
                        )
                        node_observation["next_node"] = current
                    except KeyError, ValueError:
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Flow condition lacks compatible facts"
                        ) from None
                else:
                    assert node.status and node.conclusion
                    if node.status == SOPStatus.FAILED:
                        error = (
                            failure.error
                            if failure and failure.error
                            else ErrorDetail(
                                code=ErrorCode.MODEL_OUTPUT_INVALID,
                                message="Flow stopped before a verified conclusion",
                            )
                        )
                        return self._failed(d, calls, error.code, error.message).model_copy(
                            update={"node_path": tuple(path)}
                        )
                    evidence = tuple(
                        ref for result in observed.values() for ref in result.evidence_refs
                    )
                    merged: dict[str, Fact] = {}
                    for result in observed.values():
                        for fact in result.facts:
                            old = merged.get(fact.name)
                            if old:
                                if old.kind != fact.kind or old.value != fact.value:
                                    raise AppError(
                                        ErrorCode.MODEL_OUTPUT_INVALID, "Conflicting source facts"
                                    )
                                fact = fact.model_copy(
                                    update={"evidence_ids": (*old.evidence_ids, *fact.evidence_ids)}
                                )
                            merged[fact.name] = fact
                    if evidence:
                        merged["sop_conclusion"] = Fact(
                            name="sop_conclusion",
                            kind=FactKind.TEXT,
                            value=node.conclusion,
                            evidence_ids=tuple(r.evidence_id for r in evidence),
                        )
                    plan = None
                    # 这里只形成绑定版本/参数/证据的提案。
                    # 明确审批、唯一领取和下游执行由ApprovalService负责。
                    if node.status == SOPStatus.NEEDS_APPROVAL:
                        assert node.proposal and question.versions.sop_snapshot
                        parameters = ToolParameters(order_id=values["order_id"])
                        plan = SOPPlan(
                            operation_id="proposal-" + uuid4().hex,
                            action=node.proposal,
                            identity=context.identity,
                            question_id=question.question_id,
                            question_version=question.version,
                            parameters=parameters,
                            parameters_hash=tool_parameters_hash(parameters),
                            sop_version=d.version,
                            snapshot_hash=question.versions.sop_snapshot,
                            skills_digest=question.versions.skills,
                            evidence_ids=tuple(r.evidence_id for r in evidence),
                        )
                    return SOPResult(
                        status=node.status,
                        sop_id=d.sop_id,
                        sop_version=d.version,
                        facts=tuple(merged.values()),
                        evidence_refs=evidence,
                        tool_call_ids=tuple(calls),
                        next_action=NextAction.NONE
                        if node.status == SOPStatus.RESOLVED
                        else NextAction.REQUEST_APPROVAL
                        if plan
                        else NextAction.CONTACT_SUPPORT,
                        reason=node.conclusion,
                        plan=plan,
                        node_path=tuple(path),
                    )
        raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Flow max_steps exhausted")

    @staticmethod
    def _branch(d: SOPDefinition, observation: ToolResult) -> SOPBranch | None:
        facts = {f.name: f.value for f in observation.facts}
        for branch in d.branches:
            condition = branch.condition
            value = facts.get(condition.fact)
            if (
                (
                    condition.op == "eq"
                    and type(value) is type(condition.value)
                    and value == condition.value
                )
                or (condition.op == "empty" and value == "")
                or (condition.op == "nonempty" and isinstance(value, str) and bool(value))
            ):
                return branch
        return None

    def _has_evidence(self, d: SOPDefinition, result: ToolResult | None) -> bool:
        if result is None or result.status != ToolStatus.SUCCEEDED or not result.facts:
            return False
        names = {f.name for f in result.facts}
        return (
            len(names) == len(result.facts)
            and set(d.required_facts) <= names
            and bool(result.evidence_refs)
            and all(
                r.version == self.evidence_version
                and r.content_hash
                and r.record_id == result.call_id
                for r in result.evidence_refs
            )
        )

    def _closeout(
        self, d: SOPDefinition, calls: list[str], observed: ToolResult | None
    ) -> SOPResult:
        if not self._has_evidence(d, observed):
            return self._handoff(d, calls, None, "insufficient_evidence")
        assert observed is not None
        branch = self._branch(d, observed)
        if branch is None:
            return self._handoff(d, calls, observed, "unmatched_branch")
        conclusion = Fact(
            name="sop_conclusion",
            kind=FactKind.TEXT,
            value=branch.conclusion,
            evidence_ids=tuple(r.evidence_id for r in observed.evidence_refs),
        )
        return SOPResult(
            status=branch.status,
            sop_id=d.sop_id,
            sop_version=d.version,
            facts=(*observed.facts, conclusion),
            evidence_refs=observed.evidence_refs,
            tool_call_ids=tuple(calls),
            next_action=(
                NextAction.NONE
                if branch.status == SOPStatus.RESOLVED
                else NextAction.CONTACT_SUPPORT
            ),
            reason=branch.conclusion,
        )

    @staticmethod
    def _handoff(
        d: SOPDefinition,
        calls: list[str],
        observed: ToolResult | None,
        reason: str,
    ) -> SOPResult:
        return SOPResult(
            status=SOPStatus.HANDED_OFF,
            sop_id=d.sop_id,
            sop_version=d.version,
            facts=observed.facts if observed else (),
            evidence_refs=observed.evidence_refs if observed else (),
            tool_call_ids=tuple(calls),
            next_action=NextAction.CONTACT_SUPPORT,
            reason=reason,
        )
