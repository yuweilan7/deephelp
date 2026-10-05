"""同一原SOP的委托/回收守卫，独立核对实际MCP账本而非仅断言Prompt。"""

import asyncio
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.adapters.persistence.ledger import payload_hash
from deephelp_app.application.collaboration import load_agent_policy, validate_agent_task
from deephelp_app.application.conversation.tracking import STAGES, CountedModel, TrackedTools
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.sop import SOPStatus
from deephelp_tools.demo.assembly import DemoToolGateway
from deephelp_tools.learning.course.collaboration import RecordedAgentModel
from deephelp_tools.learning.replay.event_replay import envelope
from deephelp_tools.learning.replay.mvp_replay import RecordedModel, ReplayAssembly
from deephelp_tools.probes.conversation.sop_probe import sop_question

pytestmark = pytest.mark.integration


def setup(gateway, model=None):
    budget = ExecutionBudget.start(30, 12, 0)
    executor = SOPExecutor(
        model or RecordedAgentModel(),
        gateway,
        registry=bundled_registry(),
        agent_policy=load_agent_policy(),
    )
    question, context = sop_question(budget, IntentCode.DISCOUNT_MISSING)
    question = question.model_copy(
        update={"versions": executor.pin_versions(question.versions, IntentCode.DISCOUNT_MISSING)}
    )
    context = context.model_copy(update={"execution_mode": "collaboration"})
    return executor, budget, question, context


async def test_order_receipt_closes_against_actual_tool_and_original_sop():
    gateway = DemoToolGateway()
    executor, budget, question, context = setup(gateway)
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="test-agent")
        ledger = await gateway.ledger()
    assert result.status == SOPStatus.RESOLVED and result.node_path == (
        "order",
        "decide",
        "applied",
    )
    receipt = result.agent_receipts[0]
    assert receipt.status == "COMPLETED" and len(ledger) == 1
    assert receipt.observation.call_id == ledger[0].call_id
    assert set(receipt.evidence_ids) == set(ledger[0].evidence_ids)
    assert receipt.task.parent_question_id == question.question_id
    assert receipt.task.slots == question.entities
    assert receipt.task.identity == ledger[0].identity == context.identity
    assert receipt.task.parameters == ledger[0].parameters
    assert [d.phase for d in receipt.decisions] == ["delegate", "select_tool", "return_evidence"]
    assert budget.attempts_used == 4 and budget.tool_steps_used == 1
    assert {f.name: f.value for f in result.facts}["paid"].amount == Decimal("99.90")


@pytest.mark.parametrize(
    "mutation", ["role", "tool", "parent", "owner", "version", "source", "budget_ref", "depth"]
)
async def test_child_boundary_rejects_forged_task(mutation):
    gateway = DemoToolGateway()
    executor, budget, question, context = setup(gateway)
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="test-agent")
    task = result.agent_receipts[0].task
    updates = {
        "role": {"role_id": "arbitrary-agent"},
        "tool": {"allowed_tools": ("check_coupon",)},
        "parent": {"parent_run_id": "another-run"},
        "owner": {"identity": VerifiedIdentity(tenant_id="other", user_id="other")},
        "version": {"question_version": question.version + 1},
        "source": {
            "slots": (
                task.slots[0].model_copy(
                    update={
                        "source": task.slots[0].source.model_copy(
                            update={"message_id": "foreign-message"}
                        )
                    }
                ),
            )
        },
        "budget_ref": {"budget_ref": "new-budget"},
        "depth": {"depth": 2},
    }
    with pytest.raises(AppError):
        validate_agent_task(
            task.model_copy(update=updates[mutation]),
            question,
            context,
            "test-agent",
            load_agent_policy(),
        )


class MaliciousModel(RecordedAgentModel):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase

    async def chat(self, request, budget):
        result = await super().chat(request, budget)
        if request.tools[0].name == self.phase:
            action = result.tool_calls[0]
            changes = (
                {"role_id": "root"}
                if self.phase == "delegate_task"
                else {"order_id": "000099"}
                if self.phase == "get_order_benefits"
                else {"evidence_ids": ["invented-proof"]}
            )
            result = result.model_copy(
                update={
                    "tool_calls": [
                        action.model_copy(update={"arguments": {**action.arguments, **changes}})
                    ]
                }
            )
        return result


@pytest.mark.parametrize(
    "phase, tool_calls", [("delegate_task", 0), ("get_order_benefits", 0), ("return_evidence", 1)]
)
async def test_model_cannot_escalate_slots_or_fabricate_evidence(phase, tool_calls):
    gateway = DemoToolGateway()
    executor, budget, question, context = setup(gateway, MaliciousModel(phase))
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="test-agent")
        ledger = await gateway.ledger()
    assert result.status == SOPStatus.FAILED and not result.facts
    assert len(ledger) == tool_calls and result.agent_receipts[0].status == "FAILED"
    assert not result.agent_receipts[0].evidence_ids


async def test_missing_slot_does_not_delegate_and_policy_drift_stops_before_model():
    gateway = DemoToolGateway()
    executor, budget, question, context = setup(gateway)
    missing = await executor.execute(
        question.model_copy(update={"entities": ()}), budget, context=context, run_id="test-agent"
    )
    assert missing.status == SOPStatus.WAITING_SLOT and not missing.agent_receipts
    changed = question.model_copy(
        update={"versions": question.versions.model_copy(update={"collaboration": "sha256:other"})}
    )
    failed = await executor.execute(changed, budget, context=context, run_id="test-agent")
    assert failed.error.code == ErrorCode.VERSION_CONFLICT and budget.attempts_used == 0


async def test_cancellation_propagates_without_starting_business_tool():
    entered = asyncio.Event()

    class WaitingModel(RecordedAgentModel):
        async def chat(self, request, budget):
            entered.set()
            await asyncio.Event().wait()

    gateway = DemoToolGateway()
    executor, budget, question, context = setup(gateway, WaitingModel())
    task = asyncio.create_task(
        executor.execute(question, budget, context=context, run_id="test-agent")
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert budget.tool_steps_used == 0


def test_policy_cannot_register_extra_tools_or_unknown_role():
    data = load_agent_policy().model_dump()
    data["roles"][0]["tool"] = "check_coupon"
    with pytest.raises(ValidationError):
        type(load_agent_policy()).model_validate(data)


async def test_execution_mode_is_bound_to_message_hash_with_old_default_compatibility():
    _, _, _, context = setup(DemoToolGateway())
    single = context.model_copy(update={"execution_mode": "single"})
    assert payload_hash(single) != payload_hash(context)
    assert payload_hash(single) == payload_hash(
        single.model_copy(update={"request_id": "other-request"})
    )


async def test_original_conversation_has_one_600_and_receipt_replay_has_no_new_calls():
    """原13段消费真实委托结果；重投只读同run，不能再问专业角色或重复查工具。"""
    assembly = ReplayAssembly()
    async with httpx.AsyncClient() as client, assembly.open(client, MemoryTrace()) as conversation:
        executor = SOPExecutor(
            CountedModel(RecordedAgentModel(), RecordedModel()),
            TrackedTools(assembly.tools),
            registry=bundled_registry(),
            agent_policy=load_agent_policy(),
        )
        conversation.sop = executor
        conversation.definitions = dict(executor.definitions)
        request = envelope("订单 000031 显示优惠少了，请核对，前导零要保留。", "m40-original")
        request = request.model_copy(update={"execution_mode": "collaboration"})
        result = await conversation.run(request, ExecutionBudget.start(30, 30, 0))
        assert result.outcome == "ANSWERED" and len(result.agent_receipts) == 1
        assert tuple(s.stage for s in result.stages) == STAGES
        assert result.call_counts.intent_calls == 1
        assert sum(s.intent_calls for s in result.stages if not s.stage.startswith("600_")) == 0
        count = len(await assembly.tools.ledger())
        replay = await conversation.run(request, ExecutionBudget.start(30, 30, 0))
        assert replay.replayed and replay.run_id == result.run_id
        assert replay.agent_receipts == result.agent_receipts
        assert sum(replay.call_counts.model_dump().values()) == 0
        assert len(await assembly.tools.ledger()) == count


class TaskJournal:
    """离线只测用例时序；真正唯一锁/CAS/连接重开由显式PG探针独立证明。"""

    def __init__(self):
        self.rows = {}
        self.states = []

    async def begin(self, task, context, *, max_tasks):
        from datetime import UTC, datetime

        from deephelp_app.domain.models.collaboration import AgentTaskRecord

        old = self.rows.get(task.task_id)
        if old:
            if old.state != "COMPLETED":
                raise AppError(ErrorCode.OPERATION_UNKNOWN, "Reconcile only")
            return old
        row = AgentTaskRecord(
            task=task,
            claim_token="owned",
            state="RUNNING",
            phase="delegate",
            updated_at=datetime.now(UTC),
        )
        self.rows[task.task_id] = row
        return row

    async def write(self, record):
        self.rows[record.task.task_id] = record
        self.states.append(record.state)


def coupon_setup(
    gateway, *, model=None, journal=None, policy=None, order="DEMO-C01", coupon="COUPON-C01"
):
    budget = ExecutionBudget.start(30, 20, 0)
    executor = SOPExecutor(
        model or RecordedAgentModel(),
        gateway,
        registry=bundled_registry(),
        agent_policy=policy or load_agent_policy(),
        agent_journal=journal,
    )
    question, context = sop_question(budget, IntentCode.COUPON_UNUSABLE, order=order, coupon=coupon)
    question = question.model_copy(
        update={"versions": executor.pin_versions(question.versions, IntentCode.COUPON_UNUSABLE)}
    )
    return (
        executor,
        budget,
        question,
        context.model_copy(update={"execution_mode": "collaboration"}),
    )


async def test_coupon_two_sources_keep_original_amount_comparison_and_cached_calls():
    journal, model, gateway = TaskJournal(), RecordedAgentModel(), DemoToolGateway()
    executor, budget, question, context = coupon_setup(gateway, model=model, journal=journal)
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="coupon-run")
        before = budget.usage()
        replay = await executor.execute(question, budget, context=context, run_id="coupon-run")
        ledger = await gateway.ledger()
    assert result.status == replay.status == SOPStatus.RESOLVED
    assert [r.task.role_id for r in result.agent_receipts] == ["coupon-status", "order-benefits"]
    assert "合成口径比对一致" in result.reason
    facts = {f.name: f.value for f in result.facts}
    assert (
        facts["paid"].amount
        == Decimal("99.90")
        < facts["minimum_spend"].amount
        == Decimal("100.00")
    )
    assert len(ledger) == 2 and len(model.requests) == 6
    assert before == budget.usage() and replay.agent_receipts == result.agent_receipts
    assert replay.tool_call_ids == result.tool_call_ids
    assert journal.states == ["RUNNING", "RUNNING", "OBSERVED", "COMPLETED"] * 2


@pytest.mark.parametrize("order,coupon", [("000042", "000009"), ("DEMO-C04", "COUPON-C04")])
async def test_coupon_short_branch_does_not_delegated_order(order, coupon):
    gateway = DemoToolGateway()
    executor, budget, question, context = coupon_setup(gateway, order=order, coupon=coupon)
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="coupon-run")
        ledger = await gateway.ledger()
    assert result.status == SOPStatus.RESOLVED and len(ledger) == 1
    assert [r.task.role_id for r in result.agent_receipts] == ["coupon-status"]


async def test_partial_completion_cannot_return_success_or_restart_failed_task():
    class FailSecond(RecordedAgentModel):
        async def chat(self, request, budget):
            if len(self.requests) == 3:
                raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Second role stopped")
            return await super().chat(request, budget)

    journal, model, gateway = TaskJournal(), FailSecond(), DemoToolGateway()
    executor, budget, question, context = coupon_setup(gateway, model=model, journal=journal)
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="coupon-run")
        replay = await executor.execute(question, budget, context=context, run_id="coupon-run")
        ledger = await gateway.ledger()
    assert result.status == replay.status == SOPStatus.FAILED and not result.facts
    assert [r.status for r in result.agent_receipts] == ["COMPLETED", "FAILED"]
    assert replay.error.code == ErrorCode.OPERATION_UNKNOWN and len(ledger) == 1
    assert len(model.requests) == 3


async def test_cancel_after_observation_saves_cancelled_receipt_without_returned_evidence():
    entered = asyncio.Event()

    class WaitReturn(RecordedAgentModel):
        async def chat(self, request, budget):
            if request.tools[0].name == "return_evidence":
                entered.set()
                await asyncio.Event().wait()
            return await super().chat(request, budget)

    journal, gateway = TaskJournal(), DemoToolGateway()
    executor, budget, question, context = coupon_setup(gateway, model=WaitReturn(), journal=journal)
    async with gateway.open():
        task = asyncio.create_task(
            executor.execute(question, budget, context=context, run_id="coupon-run")
        )
        await entered.wait()
        observed = next(iter(journal.rows.values()))
        assert observed.state == "OBSERVED" and observed.observation.status == "SUCCEEDED"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        row = next(iter(journal.rows.values()))
        assert row.state == "CANCELLED" and row.observation == observed.observation
        assert not row.receipt.evidence_ids
        retry = await executor.execute(question, budget, context=context, run_id="coupon-run")
        assert retry.error.code == ErrorCode.OPERATION_UNKNOWN
        assert len(await gateway.ledger()) == 1


async def test_nested_attempt_cap_prevents_model_repair_from_bypassing_task_budget():
    class SpendUntilStopped(RecordedAgentModel):
        async def chat(self, request, budget):
            for _ in range(4):
                budget.claim_attempt(retry=False)
            return await super().chat(request, budget)

    gateway = DemoToolGateway()
    policy = load_agent_policy().model_copy(update={"max_task_attempts": 2})
    executor, budget, question, context = coupon_setup(
        gateway, model=SpendUntilStopped(), policy=policy
    )
    result = await executor.execute(question, budget, context=context, run_id="coupon-run")
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED and budget.attempts_used == 2
    budget.claim_attempt(retry=False)
    assert budget.attempts_used == 3 and budget.tool_steps_used == 0


async def test_task_timeout_stays_unknown_and_no_next_role():
    class Wait(RecordedAgentModel):
        async def chat(self, request, budget):
            await asyncio.Event().wait()

    gateway, journal = DemoToolGateway(), TaskJournal()
    policy = load_agent_policy().model_copy(update={"task_seconds": 0.02})
    executor, budget, question, context = coupon_setup(
        gateway, model=Wait(), journal=journal, policy=policy
    )
    result = await executor.execute(question, budget, context=context, run_id="coupon-run")
    assert result.status == SOPStatus.FAILED and result.agent_receipts[0].status == "UNKNOWN"
    assert len(journal.rows) == 1 and budget.tool_steps_used == 0


async def test_cancel_while_terminal_writer_commits_does_not_downgrade_completed():
    """短保存Task拥有提交，父取消等待其退出；已经完成的事实不能改写为取消。"""
    entered, release = asyncio.Event(), asyncio.Event()

    class GatedJournal(TaskJournal):
        async def write(self, record):
            if record.state == "COMPLETED":
                entered.set()
                await release.wait()
            await super().write(record)

    journal, gateway = GatedJournal(), DemoToolGateway()
    executor, budget, question, context = coupon_setup(
        gateway, journal=journal, order="000042", coupon="000009"
    )
    async with gateway.open():
        owned = asyncio.create_task(
            executor.execute(question, budget, context=context, run_id="coupon-run")
        )
        await entered.wait()
        owned.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owned
    row = next(iter(journal.rows.values()))
    assert row.state == "COMPLETED" and row.receipt.status == "COMPLETED"
    assert journal.states[-1] == "COMPLETED" and "CANCELLED" not in journal.states


async def test_cached_observation_still_revalidates_bound_parameters_without_new_calls():
    """故障适配器返回同task却串入别单观察，不能因缓存而绕过原事实守卫。"""
    journal, model, gateway = TaskJournal(), RecordedAgentModel(), DemoToolGateway()
    executor, budget, question, context = coupon_setup(gateway, journal=journal, model=model)
    async with gateway.open():
        original = await executor.execute(question, budget, context=context, run_id="coupon-run")
        row = next(iter(journal.rows.values()))
        observation = row.observation.model_copy(
            update={
                "facts": tuple(
                    f.model_copy(update={"value": "foreign-order"}) if f.name == "order_id" else f
                    for f in row.observation.facts
                )
            }
        )
        journal.rows[row.task.task_id] = row.model_copy(
            update={
                "observation": observation,
                "receipt": row.receipt.model_copy(update={"observation": observation}),
            }
        )
        before = budget.usage()
        failed = await executor.execute(question, budget, context=context, run_id="coupon-run")
        assert len(await gateway.ledger()) == 2
    assert failed.status == SOPStatus.FAILED and not failed.facts
    assert failed.error.code == ErrorCode.MODEL_OUTPUT_INVALID
    assert failed.tool_call_ids == original.tool_call_ids[:1]
    assert budget.usage() == before and len(model.requests) == 6
