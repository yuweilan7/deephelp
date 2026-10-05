"""Skill机器边界与实际stdio事实独立核对；记录/描述不能冒充执行通过。"""

import pytest
from pydantic import ValidationError

from deephelp_app.application.skills import (
    SkillDefinition,
    SkillSession,
    load_skills,
    register_skills,
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
