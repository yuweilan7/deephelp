"""Skill机器边界与实际stdio事实独立核对；记录/描述不能冒充执行通过。"""

import asyncio
from decimal import Decimal

import pytest
from pydantic import ValidationError

from deephelp_app.application.skills import (
    SkillDefinition,
    SkillRegistry,
    SkillSession,
    load_skills,
    register_skills,
    skill_history,
)
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.intents import IntentCode
from deephelp_tools.demo.assembly import DemoToolGateway
from deephelp_tools.learning.course.skills import RecordedSkillModel
from deephelp_tools.probes.conversation.sop_probe import sop_question

pytestmark = pytest.mark.integration


def setup():
    budget = ExecutionBudget.start(30, 8, 0)
    gateway = DemoToolGateway()
    executor = SOPExecutor(RecordedSkillModel(), gateway, registry=bundled_registry())
    question, context = sop_question(budget, IntentCode.DISCOUNT_MISSING)
    question = question.model_copy(
        update={"versions": executor.pin_versions(question.versions, IntentCode.DISCOUNT_MISSING)}
    )
    return budget, gateway, executor, question, context


async def test_skill_is_actual_original_lookup_with_source_and_fact_ids():
    budget, gateway, executor, question, context = setup()
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="skill-test")
        ledger = await gateway.ledger()
    receipt = result.skill_receipts[0]
    assert result.status == "RESOLVED" and len(ledger) == 1
    assert receipt.slots == question.entities
    assert receipt.observation.call_id == ledger[0].call_id == result.tool_call_ids[0]
    assert {e.evidence_id for e in receipt.observation.evidence_refs} == set(ledger[0].evidence_ids)
    assert receipt.registry_digest == question.versions.skills
    assert budget.attempts_used == 2 and budget.tool_steps_used == 1


async def test_registered_goal_reaches_original_model_without_replacing_ready_input():
    seen = []

    class Capture(RecordedSkillModel):
        async def chat(self, request, budget):
            seen.append(request)
            return await super().chat(request, budget)

    budget, gateway, executor, question, context = setup()
    executor.model = Capture()
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="goal-test")
    assert result.status == "RESOLVED"
    assert any(
        executor.skills.definitions[0].goal in m.content for m in seen[0].messages if m.content
    )
    assert '"ready_node"' in seen[0].messages[-1].content
    assert [t.name for t in seen[0].tools] == ["get_order_benefits"]


async def test_missing_slots_never_start_model_tool_or_skill():
    budget, _, executor, question, context = setup()
    result = await executor.execute(
        question.model_copy(update={"entities": ()}), budget, context=context, run_id="missing"
    )
    assert result.status == "WAITING_SLOT" and not result.skill_receipts
    assert not result.tool_call_ids and budget.attempts_used == 0


@pytest.mark.parametrize(
    "field,value", [("skill_id", "unknown"), ("version", "2"), ("digest", "0" * 64)]
)
def test_unknown_exact_reference_rejected(field, value):
    registry = load_skills()
    d = registry.definitions[0]
    reference = dict(skill_id=d.skill_id, version=d.version, digest=d.digest)
    reference[field] = value
    with pytest.raises(AppError):
        registry.resolve(**reference)


@pytest.mark.parametrize("change", ["extra", "schema", "write", "path"])
def test_untrusted_machine_contract_cannot_extend_permission(change):
    raw = load_skills().definitions[0].model_dump(mode="json")
    if change == "extra":
        raw["python"] = "print('execute')"
    elif change == "schema":
        raw["input_schema"]["additionalProperties"] = True
    elif change == "write":
        raw["tool"] = "simulate_discount_adjustment"
    else:
        raw["skill_id"] = "../outside"
    with pytest.raises(ValidationError):
        SkillDefinition.model_validate(raw)


def test_same_version_cannot_change_content(tmp_path):
    registry = load_skills()
    path = register_skills(tmp_path, registry)
    assert load_skills(path) == registry
    assert register_skills(tmp_path, registry) == path
    altered = registry.model_copy(
        update={"definitions": (registry.definitions[0].model_copy(update={"goal": "新指令"}),)}
    )
    with pytest.raises(ValueError, match="cannot be replaced"):
        register_skills(tmp_path, altered)
    assert load_skills(path) == registry


@pytest.mark.parametrize(
    "arguments", [{"order_id": "other"}, {"order_id": "000031", "refund": True}, {}]
)
async def test_skill_input_cannot_replace_confirmed_slots(arguments):
    budget, _, executor, question, context = setup()
    session = SkillSession(executor.skills)

    async def forbidden():
        pytest.fail("Invalid arguments reached the dispatcher")

    with pytest.raises(AppError):
        await session.lookup(
            question,
            context,
            "input-test",
            "order",
            executor.skills.definitions[0],
            arguments,
            budget,
            forbidden,
        )
    assert budget.attempts_used == 0 and not session.receipts


async def test_pinned_registry_drift_fails_before_missing_slots():
    budget, _, executor, question, context = setup()
    question = question.model_copy(
        update={
            "entities": (),
            "versions": question.versions.model_copy(update={"skills": "other"}),
        }
    )
    with pytest.raises(AppError):
        await executor.execute(question, budget, context=context, run_id="drift")
    assert budget.attempts_used == 0


def coupon_setup():
    budget, gateway, executor, _, _ = setup()
    question, context = sop_question(
        budget, IntentCode.COUPON_UNUSABLE, order="DEMO-C01", coupon="COUPON-C01"
    )
    question = question.model_copy(
        update={"versions": executor.pin_versions(question.versions, IntentCode.COUPON_UNUSABLE)}
    )
    return budget, gateway, executor, question, context


async def test_composition_order_then_coupon_original_sop_no_repeat():
    budget, gateway, executor, question, context = coupon_setup()
    deadline = budget.deadline
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="compose")
        ledger = await gateway.ledger()
    assert result.status == "RESOLVED" and result.node_path[-1] == "threshold"
    assert [r.tool_name.value for r in ledger] == ["get_order_benefits", "check_coupon"]
    assert result.tool_call_ids == tuple(r.call_id for r in ledger)
    parent, order, coupon = result.skill_receipts
    assert parent.children == (order.execution_id, coupon.execution_id)
    assert coupon.input_sources == {"order_id": "order.order_id", "coupon_id": "slot.coupon_id"}
    assert order.parent_id == coupon.parent_id == parent.execution_id
    facts = {f.name: f.value for f in result.facts}
    assert facts["paid"].amount == Decimal("99.90") < facts["minimum_spend"].amount
    assert budget.attempts_used == 4 and budget.tool_steps_used == 2 and budget.deadline == deadline


@pytest.mark.parametrize(
    "change", ["cycle", "depth", "steps", "hash", "future", "money", "tool", "scope"]
)
def test_composition_closure_mapping_and_permission_guard(change):
    raw = load_skills().model_dump(mode="json")
    c = raw["compositions"][0]
    if change == "cycle":
        c["steps"][0]["skill_id"] = c["skill_id"]
    elif change == "depth":
        c["max_depth"] = 3
    elif change == "steps":
        c["max_steps"] = 1
    elif change == "hash":
        c["steps"][1]["digest"] = "0" * 64
    elif change == "future":
        c["steps"][0]["inputs"]["order_id"] = "coupon.order_id"
    elif change == "money":
        c["steps"][1]["inputs"]["order_id"] = "order.paid"
    elif change == "tool":
        c["allowed_tools"] = ["get_order_benefits"]
    else:
        c["applicable_intents"] = ["DISCOUNT_MISSING"]
    with pytest.raises(ValidationError):
        SkillRegistry.model_validate(raw)


async def test_partial_budget_failure_keeps_first_call_never_refreshes_budget():
    budget, gateway, executor, question, context = coupon_setup()
    budget.remaining_attempts = 2
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="partial")
        ledger = await gateway.ledger()
    assert result.status == "FAILED" and result.error.code == "BUDGET_EXHAUSTED"
    assert len(ledger) == 1 and result.tool_call_ids == (ledger[0].call_id,)
    assert result.skill_receipts[1].status == "COMPLETED"
    assert result.skill_receipts[0].status == "FAILED" and not result.facts
    assert budget.attempts_used == 2 and not budget._attempt_caps


async def test_cancellation_propagates_and_original_gateway_releases():
    budget, gateway, executor, question, context = coupon_setup()
    ready = asyncio.Event()

    class PauseSecond(RecordedSkillModel):
        async def chat(self, request, budget):
            if request.tools[0].name == "check_coupon":
                ready.set()
                await asyncio.Event().wait()
            return await super().chat(request, budget)

    executor.model = PauseSecond()
    async with gateway.open():
        task = asyncio.create_task(
            executor.execute(question, budget, context=context, run_id="cancel-compose")
        )
        await asyncio.wait_for(ready.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ledger = await gateway.ledger()
    assert len(ledger) == 1 and budget.tool_steps_used == 1
    assert not budget._attempt_caps and gateway.pid
    from deephelp_app.adapters.mcp.business.tool_gateway import process_alive

    assert not process_alive(gateway.pid)


async def test_old_skill_snapshot_keeps_original_leaf_hash():
    budget, gateway, executor, question, context = setup()
    old = skill_history()[0]
    question = question.model_copy(
        update={"versions": question.versions.model_copy(update={"skills": old.digest})}
    )
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="old")
    assert result.skill_receipts[0].registry_digest == old.digest != executor.skills.digest
    assert result.skill_receipts[0].digest == old.definitions[0].digest


@pytest.mark.parametrize("change", ["tool", "run", "evidence"])
async def test_skill_output_cannot_bypass_actual_observation_guard(change):
    budget, gateway, executor, question, context = setup()
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="output-guard")
    observation = result.skill_receipts[0].observation
    updates = {
        "tool": {"tool_name": "check_coupon"},
        "run": {"run_id": "foreign"},
        "evidence": {"evidence_refs": ()},
    }
    bad = observation.model_copy(update=updates[change])
    session = SkillSession(executor.skills)

    async def wrong():
        return bad

    with pytest.raises(AppError):
        await session.lookup(
            question,
            context,
            "output-guard",
            "order",
            executor.skills.definitions[0],
            {"order_id": "000031"},
            budget,
            wrong,
        )
    assert session.receipts[0].status == "FAILED" and session.receipts[0].observation is None


async def test_completed_leaf_cannot_be_rerun_in_same_execution():
    budget, gateway, executor, question, context = setup()
    async with gateway.open():
        result = await executor.execute(question, budget, context=context, run_id="same-leaf")
    observation = result.skill_receipts[0].observation
    session = SkillSession(executor.skills)
    callbacks = 0

    async def original():
        nonlocal callbacks
        callbacks += 1
        return observation

    args = (
        question,
        context,
        "same-leaf",
        "order",
        executor.skills.definitions[0],
        {"order_id": "000031"},
        budget,
        original,
    )
    await session.lookup(*args)
    with pytest.raises(AppError, match="cannot be rerun"):
        await session.lookup(*args)
    assert callbacks == 1 and budget.attempts_used == 2
