"""目录版本/执行许可/来源的独立反例；固定观察不冒充真实PG或模型内容。"""

import hashlib
import json
from importlib.resources import files

import pytest
from pydantic import ValidationError

from deephelp_app.adapters.mcp.business.mcp_protocol import (
    canonical,
    registered_tools,
    validate_arguments,
)
from deephelp_app.application.cascade import CascadePolicy
from deephelp_app.application.intent import IntentService
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import SOPRegistry, bundled_registry, schema_signature
from deephelp_app.application.text_entity import TextEntityProcessor
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.intent_decisions import IntentDecision
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.retrieval import CorpusRecord, DenseCandidate, DenseHit, DenseResult
from deephelp_app.domain.models.tools import ToolParameters, tool_parameters_hash
from deephelp_app.domain.registry import IntentRegistry, complaint_registry
from deephelp_tools.learning.course.catalog import (
    RecordedCatalogFacts,
    catalog_question,
    catalog_registry,
)
from deephelp_tools.learning.course.skills import RecordedSkillModel
from deephelp_tools.learning.replay.event_replay import envelope
from deephelp_tools.learning.replay.mvp_replay import REPLAY_SCOPE

pytestmark = pytest.mark.integration


def test_v1_discovery_and_sop_hash_are_unchanged():
    tools = registered_tools()
    assert [t.name for t in tools] == ["get_order_benefits", "check_coupon"]
    assert schema_signature() == bundled_registry().tool_schema_hash
    original = json.loads(
        files("deephelp_app")
        .joinpath("resources/sop_data/registry-v1.json")
        .read_text(encoding="utf-8")
    )
    assert (
        bundled_registry().snapshot_hash
        == "sha256:" + hashlib.sha256(canonical(original)).hexdigest()
    )
    assert (
        tool_parameters_hash(ToolParameters(order_id="000031"))
        == hashlib.sha256(canonical({"order_id": "000031", "coupon_id": None})).hexdigest()
    )
    assert len(complaint_registry().actionable_codes) == 3
    assert len(complaint_registry("complaints-v2").actionable_codes) == 4


@pytest.mark.parametrize(
    "change", ["duplicate", "parent", "slot", "version", "unknown", "parent_leaf"]
)
def test_catalog_rejects_invalid_topology_and_versions(change):
    data = complaint_registry("complaints-v2").model_dump(mode="json")
    leaf = data["intents"][-1]
    if change == "duplicate":
        data["intents"].append(leaf)
    elif change == "parent":
        leaf["parent_code"] = "BENEFIT_ISSUE"
    elif change == "slot":
        leaf["required_slots"] = ["order_id"]
    elif change == "version":
        leaf["version"] = "complaints-v1"
    elif change == "unknown":
        leaf["code"] = "UNKNOWN"
    else:
        leaf["parent_code"] = "COUPON_UNUSABLE"
    with pytest.raises(ValidationError):
        IntentRegistry.model_validate(data)


@pytest.mark.parametrize(
    "change", ["missing", "extra_grant", "wrong_catalog", "wrong_signature", "parent"]
)
def test_registry_requires_catalog_sop_tool_closed_binding(change):
    data = catalog_registry().model_dump(mode="json")
    if change == "missing":
        data["definitions"].pop()
    elif change == "extra_grant":
        data["definitions"][-1]["allowed_tools"].append("get_order_benefits")
    elif change == "wrong_catalog":
        data["catalog_version"] = "complaints-v1"
    elif change == "wrong_signature":
        data["tool_schema_hash"] = schema_signature()
    else:
        data["definitions"][-1]["intent_code"] = "ORDER_QUERY"
    with pytest.raises(ValidationError):
        SOPRegistry.model_validate(data)


@pytest.mark.parametrize(
    "arguments",
    [{}, {"activity_id": "A", "order_id": "O"}, {"activity_id": "A", "user_id": "other"}],
)
def test_new_tool_rejects_missing_slot_and_extra_authority(arguments):
    with pytest.raises(AppError):
        validate_arguments("check_activity_eligibility", arguments)


@pytest.mark.parametrize(
    "state,remaining,expected,node",
    [
        ("eligible", "7", "RESOLVED", "eligible"),
        ("eligible", "0", "RESOLVED", "full"),
        ("eligible", "unknown", "HANDED_OFF", "unknown"),
        ("unknown", "7", "HANDED_OFF", "unknown"),
        ("ineligible", "7", "RESOLVED", "ineligible"),
        ("expired", "7", "RESOLVED", "unavailable"),
    ],
)
async def test_recorded_facts_follow_original_sop_state_machine(state, remaining, expected, node):
    registry = catalog_registry()
    question, context = catalog_question(registry)
    port = RecordedCatalogFacts(eligibility=state, remaining_slots=remaining)
    result = await SOPExecutor(
        RecordedSkillModel(), port, registry=registry, skills_enabled=False
    ).execute(question, ExecutionBudget.start(10, 4, 0), context=context, run_id="m42-fixed")
    assert result.status == expected and result.node_path[-1] == node
    assert port.calls == 1
    assert result.evidence_refs[0].locator == "ACT-42"
    assert result.tool_call_ids == (result.evidence_refs[0].record_id,)


async def test_missing_activity_never_calls_model_or_tool():
    registry = catalog_registry()
    question, context = catalog_question(registry, missing=True)
    port = RecordedCatalogFacts()
    budget = ExecutionBudget.start(10, 4, 0)
    result = await SOPExecutor(
        RecordedSkillModel(), port, registry=registry, skills_enabled=False
    ).execute(question, budget, context=context, run_id="missing")
    assert result.status == "WAITING_SLOT" and port.calls == budget.attempts_used == 0


async def test_explicit_missing_activity_does_not_extract_an_impossible_identifier():
    class NoModel:
        async def chat(self, *args, **kwargs):
            raise AssertionError("No digit exists for a grounded activity identifier")

    processor = TextEntityProcessor(NoModel(), catalog_version="complaints-v2")
    budget = ExecutionBudget.start(10, 4, 0)
    result = await processor.process(envelope("活动参与资格和名额能查询吗", "missing"), budget)
    assert not result.entities and budget.attempts_used == 0
    assert result.rule_matches[0].candidate_code == "ACTIVITY_ELIGIBILITY_QUERY"


def test_v1_question_decision_and_corpus_reject_new_leaf():
    registry = catalog_registry()
    question, _ = catalog_question(registry)
    versions = question.versions.model_copy(update={"registry": "complaints-v1"})
    with pytest.raises(ValidationError):
        type(question).model_validate(
            question.model_copy(update={"versions": versions}).model_dump()
        )
    with pytest.raises(ValidationError):
        IntentDecision(
            decision="CLARIFY",
            final_code=IntentCode.ACTIVITY_ELIGIBILITY_QUERY,
            candidates=[
                dict(code=IntentCode.ACTIVITY_ELIGIBILITY_QUERY, rank=1, score=1, score_kind="rule")
            ],
            reason_code="candidate",
        )
    with pytest.raises(ValidationError):
        CorpusRecord(
            doc_id="a",
            content="我有资格参加活动吗",
            intent_code=IntentCode.ACTIVITY_ELIGIBILITY_QUERY,
            split="reference",
            source_group="s",
            variant_group="v",
            synthetic=True,
        )
    # 独立拦住检索端返回的新标签；不能只靠最终600再拒绝混入旧scope的证据。
    with pytest.raises(ValidationError):
        DenseHit(
            doc_id="new",
            intent_code=IntentCode.ACTIVITY_ELIGIBILITY_QUERY,
            content="活动资格",
            raw_score=0.8,
            metadata={},
            scope=REPLAY_SCOPE,
        )
    with pytest.raises(ValidationError):
        DenseResult(
            scope=REPLAY_SCOPE,
            hits=(),
            candidates=(
                DenseCandidate(
                    intent_code=IntentCode.ACTIVITY_ELIGIBILITY_QUERY,
                    raw_score=0.8,
                    rank=1,
                    evidence_doc_id="new",
                ),
            ),
        )


async def test_new_rule_is_selected_only_in_pinned_catalog_and_old_activity_is_distinct():
    registry = catalog_registry()
    scope = REPLAY_SCOPE.model_copy(update={"registry_version": "complaints-v2"})

    class NoRetrieval:
        async def retrieve(self, *args, **kwargs):
            raise AssertionError("Unique rule should not retrieve")

    model = RecordedSkillModel()
    service = IntentService(model, NoRetrieval(), scope, policy=CascadePolicy())
    for raw, expected in [
        ("活动编号ACT-42，查询我有参与资格和名额吗", "ACTIVITY_ELIGIBILITY_QUERY"),
        ("订单000031查询参加的活动", "ORDER_ACTIVITY_QUERY"),
    ]:
        context = envelope(raw, "rules")
        text = await TextEntityProcessor(catalog_version=registry.catalog_version).process(
            context, ExecutionBudget.start(10, 4, 0)
        )
        decision = await service.recognize(text, ExecutionBudget.start(10, 4, 0))
        assert decision.final_code == expected and decision.registry_version == "complaints-v2"
    old = await TextEntityProcessor().process(
        envelope("活动编号ACT-42，参与资格", "old"), ExecutionBudget.start(10, 4, 0)
    )
    assert all(m.candidate_code != IntentCode.ACTIVITY_ELIGIBILITY_QUERY for m in old.rule_matches)
