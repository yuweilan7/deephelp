import copy
import json
from datetime import UTC, datetime

import pytest

from deephelp_app.app import create_app
from deephelp_app.business_catalog import business_cases, validate_catalog
from deephelp_app.corpus import digest
from deephelp_app.domain.models import DemandType, ResponseEnvelope
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation.core import DATA, audit
from deephelp_app.evaluation.evaluation_runtime import EvaluationAssembly
from deephelp_app.evaluation.mvp_acceptance import api_client
from deephelp_app.event_cluster import fragments, function_of
from deephelp_app.flywheel import FeedbackCandidate, isolation
from deephelp_app.learning.event_replay import envelope
from deephelp_app.settings import Settings

pytestmark = pytest.mark.unit
SCALE = DATA.with_name("m17_scale_cases.json")
FREEZE = DATA.with_name("m17_scale_manifest.json")


def test_full_scale_counts_real_conversations_and_business_geometries():
    cases, summary = audit(SCALE, FREEZE)
    assert len(cases) == summary["independent_cases"] == 548
    assert summary["turns"] == 1820 and summary["split_counts"] == {"regression": 526, "test": 22}
    assert summary["scale_500_conversations_complete"] and summary["scenario_scale_complete"]
    assert summary["unique_normalized_messages"] + summary["repeated_message_occurrences"] == 1820
    assert validate_catalog() == {"scenarios": 22, "business_geometries": 22}


def test_identifier_only_duplicate_cannot_inflate_evaluation_scale(tmp_path):
    raw = json.loads(SCALE.read_text(encoding="utf-8"))
    duplicate = copy.deepcopy(raw[0])
    duplicate["case_id"] = "invented-independent-case"
    for i, turn in enumerate(duplicate["turns"]):
        turn["turn_id"] = f"invented-independent-turn-{i}"
    raw.append(duplicate)
    manifest = json.loads(FREEZE.read_text(encoding="utf-8"))
    manifest.update(data_sha256=digest(raw), case_ids=[c["case_id"] for c in raw])
    data, freeze = tmp_path / "data.json", tmp_path / "manifest.json"
    data.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    freeze.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="duplicate conversation"):
        audit(data, freeze)


@pytest.mark.parametrize(
    "text,separate",
    [
        ("优惠未到账，背景是订单001优惠未到账，需要核查。", False),
        ("优惠未到账，同时优惠券用不了，请分条处理。", True),
        ("优惠未到账，同时优惠券无法使用，请分条处理。", True),
        ("同时优惠未到账以及优惠券无法使用，需要分成两件独立咨询。", True),
        ("优惠未到账，订单001以及订单002是我补充的编号。", False),
        ("订单001优惠未到账，订单002优惠未到账。", True),
        ("订单001优惠未到账，订单001优惠未到账。", False),
        ("更正订单001为订单002，优惠未到账。", False),
    ],
)
def test_background_is_not_multiple_but_different_concerns_and_objects_are(text, separate):
    assert (len(fragments(envelope(text, "fragment"))) > 1) == separate


def test_only_providing_slots_is_a_supplement():
    assert function_of("仅补充资料：订单001，优惠券002", history=True) == DemandType.SUPPLEMENT


@pytest.mark.parametrize("split", ["regression", "test"])
def test_expanded_frozen_text_and_source_cannot_enter_feedback_training(split):
    case = next(c for c in audit(SCALE, FREEZE)[0] if c.split == split)
    candidate = FeedbackCandidate(
        candidate_id="new-feedback",
        run_id="new-run",
        source_group="new-source",
        variant_group="new-variant",
        text=case.turns[0].text,
        observation={},
    )
    with pytest.raises(ConfigurationError, match="cannot be recycled"):
        isolation(candidate)
    candidate = candidate.model_copy(
        update={"text": "独立的新反馈文字", "source_group": case.source_group}
    )
    with pytest.raises(ConfigurationError, match="frozen dataset"):
        isolation(candidate)


@pytest.mark.parametrize("mode", ["rule_dense", "memory_event"])
async def test_confirmed_conflict_does_not_keep_blocking_sop(mode):
    identity = envelope("x", "x").identity
    assembly = EvaluationAssembly(mode, business=True)
    app = create_app(
        Settings(mode="test"),
        conversation_factory=assembly.open,
        identity_provider=lambda request: identity,
    )
    case = next(
        c
        for c in audit(SCALE, FREEZE)[0]
        if c.case_id == "scale-discount-applied-conflict-correction"
    )
    async with app.router.lifespan_context(app), api_client(app, http=True) as client:
        question = None
        for turn in case.turns:
            body = dict(
                channel="unit",
                session_id="confirm-" + mode,
                message_id=turn.turn_id,
                raw_text=turn.text,
                occurred_at=datetime.now(UTC).isoformat(),
            )
            if turn.hint_event:
                body.update(
                    question_hint=question.question_id, expected_question_version=question.version
                )
            response = ResponseEnvelope.model_validate(
                (await client.post("/converse", json=body)).json()
            )
            question = await assembly.ledger.question(
                identity, body["session_id"], response.question_id
            )
            if turn.conflicts:
                assert response.outcome == "CLARIFY" and not response.tool_call_ids
        assert response.outcome == "ANSWERED" and len(response.tool_call_ids) == 1
        assert question.conflicts and all(
            c.resolution == "explicit_correction" for c in question.conflicts
        )
        assert question.conflicts[-1].replacement.source.message_id == case.turns[-1].turn_id


def test_new_business_catalog_preserves_three_main_intents_and_all_terminal_kinds():
    cases = business_cases()
    assert len({c.intent for c in cases}) == 3
    assert {c.status.value for c in cases} == {"RESOLVED", "HANDED_OFF", "FAILED"}
    assert sum(c.tools == ("check_coupon", "get_order_benefits") for c in cases) == 4
