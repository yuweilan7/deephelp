"""Bounded ReAct selection around deterministic parameters, evidence and closeout."""

import asyncio
import json
from uuid import uuid4

from pydantic import ValidationError

from deephelp_app.adapters.mcp_protocol import tool_schema, validate_arguments, validate_observation
from deephelp_app.adapters.trace import observe_sop_node
from deephelp_app.application.sop_config import SOPBranch, SOPDefinition, load_prompt, load_sops
from deephelp_app.application.sop_governance import GovernedSOP, SOPRegistry
from deephelp_app.domain.checks import validate_question_access, validate_tool_result
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    ChatMessage,
    ChatRequest,
    ErrorCode,
    ErrorDetail,
    Fact,
    FactKind,
    IntentCode,
    ModelTool,
    NextAction,
    Question,
    QuestionStatus,
    RequestEnvelope,
    SOPPlan,
    SOPResult,
    SOPStatus,
    ToolName,
    ToolParameters,
    ToolRequest,
    ToolResult,
    ToolStatus,
    VersionManifest,
    tool_parameters_hash,
)
from deephelp_app.domain.ports import ChatPort, ToolPort

CONTROL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class SOPExecutor:
    def __init__(
        self,
        model: ChatPort,
        tools: ToolPort,
        *,
        definitions: dict[IntentCode, SOPDefinition] | None = None,
        registry: SOPRegistry | None = None,
        history: tuple[SOPRegistry, ...] = (),
    ) -> None:
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
        validate_question_access(context, question)
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
        try:
            async with asyncio.timeout(
                min(definition.limits.total_seconds, budget.remaining_seconds())
            ):
                if isinstance(definition, GovernedSOP):
                    return await self._flow(definition, question, context, run_id, budget, calls)
                return await self._loop(definition, question, context, run_id, budget, calls)
        except TimeoutError:
            return self._failed(
                definition, calls, ErrorCode.BUDGET_EXHAUSTED, "SOP deadline exhausted"
            )
        except AppError as exc:
            return self._failed(definition, calls, exc.code, exc.safe_message)

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
                        request, question, budget, context=context, on_dispatch=remember
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
                    validate_observation(request, result)
                except ValueError:
                    raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Unverified SOP facts") from None
            if result.status != ToolStatus.FAILED or not result.error or not result.error.retryable:
                return result
            # Only a definitely classified read-only failure can be retried, and never
            # unknown/timeout, auth, schema, or budget failures. No model-directed retry.
            if result.error.code not in {ErrorCode.RATE_LIMITED, ErrorCode.UPSTREAM_UNAVAILABLE}:
                return result
        return result

    async def _flow(
        self,
        d: GovernedSOP,
        question: Question,
        context: RequestEnvelope,
        run_id: str,
        budget: ExecutionBudget,
        calls: list[str],
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
        path: list[str] = []
        current = d.entry
        failure: ToolResult | None = None
        seen: set[str] = set()
        retries_at_start = budget.retries_used
        for _ in range(d.limits.max_steps):
            budget.remaining_seconds()
            node = index[current]
            path.append(current)
            with observe_sop_node(current, node.kind, calls) as node_observation:
                if node.kind == "lookup":
                    assert node.tool and node.success and node.failure
                    expected = {slot: values[slot] for slot in tool_schema(node.tool)["required"]}
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
                                ),
                            ),
                        ],
                        tools=[ModelTool(name=node.tool.value, parameters=tool_schema(node.tool))],
                        tool_choice=node.tool.value,
                        max_output_tokens=4096,
                    )
                    try:
                        async with asyncio.timeout(
                            min(d.limits.model_seconds, budget.remaining_seconds())
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
                    if action.name != node.tool.value or action.arguments != expected:
                        raise AppError(
                            ErrorCode.INVALID_ARGUMENT,
                            "Action differs from ready node or verified slots",
                        )
                    fingerprint = action.name + ":" + json.dumps(expected, sort_keys=True)
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
                    result = await self._lookup(
                        bounded,
                        question,
                        context,
                        run_id,
                        budget,
                        node.tool,
                        validate_arguments(action.name, expected),
                        calls,
                    )
                    if result.status != ToolStatus.SUCCEEDED:
                        node_observation["status"] = "failed"
                        node_observation["error_code"] = (
                            result.error.code.value if result.error else "MODEL_OUTPUT_INVALID"
                        )
                        node_observation["next_node"] = node.failure
                        failure = result
                        current = node.failure
                        continue
                    observed[current] = result
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

    @staticmethod
    def _has_evidence(d: SOPDefinition, result: ToolResult | None) -> bool:
        if result is None or result.status != ToolStatus.SUCCEEDED or not result.facts:
            return False
        names = {f.name for f in result.facts}
        return (
            len(names) == len(result.facts)
            and set(d.required_facts) <= names
            and bool(result.evidence_refs)
            and all(
                r.version == d.evidence_version and r.content_hash and r.record_id == result.call_id
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
