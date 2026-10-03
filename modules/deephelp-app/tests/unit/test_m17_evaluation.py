import copy
import json

import pytest

from deephelp_app.corpus import digest
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation import (
    DATA,
    MANIFEST,
    MODES,
    audit,
    expected,
    metrics,
    quantile,
    ratio,
    release_gate,
    score,
)
from deephelp_app.evaluation_cli import compare_reports

pytestmark = pytest.mark.unit


def rewritten(tmp_path, change):
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    frozen = json.loads(MANIFEST.read_text(encoding="utf-8"))
    change(rows)
    frozen["data_sha256"] = digest(rows)
    frozen["case_ids"] = [c["case_id"] for c in rows]
    data, manifest = tmp_path / "data.json", tmp_path / "manifest.json"
    data.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    manifest.write_text(json.dumps(frozen), encoding="utf-8")
    return data, manifest


def test_freeze_counts_and_rule_verified_business_gold():
    cases, summary = audit()
    assert summary["cases"] == 16 and summary["turns"] == 20
    assert summary["split_counts"] == {"test": 12, "regression": 4}
    assert summary["gold_verification_coverage"] == 1 and not summary["scale_500_complete"]
    gold = expected(cases[0].turns[0])
    assert gold["facts"]["paid"] == {"amount": "99.90", "currency": "CNY"}
    assert gold["facts"]["discount"] == {"amount": "10.00", "currency": "CNY"}


@pytest.mark.parametrize("mutation", ["known", "group", "label", "entity", "followup", "id"])
def test_gold_or_leakage_rejected_even_with_rehashed_manifest(tmp_path, mutation):
    def change(rows):
        turn = rows[0]["turns"][0]
        if mutation == "known":
            turn["text"] = "订单000031未享受优惠"
        elif mutation == "group":
            rows[0]["source_group"] = rows[-1]["source_group"]
        elif mutation == "label":
            turn["intent"] = turn["label"] = "COUPON_UNUSABLE"
        elif mutation == "entity":
            turn["entities"]["order_id"] = "00999999"
        elif mutation == "followup":
            turn["expectation"] = "followup"
        else:
            rows[1]["case_id"] = rows[0]["case_id"]

    with pytest.raises(ConfigurationError):
        audit(*rewritten(tmp_path, change))


def test_frozen_hash_catches_modified_data(tmp_path):
    data = tmp_path / "data.json"
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    rows[0]["turns"][0]["text"] += "改变"
    data.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="manifest mismatch"):
        audit(data, MANIFEST)


def test_empty_metrics_never_manufacture_zero_usage_or_perfect_scores():
    result = metrics([], live=False, event_enabled=False)
    assert result["accuracy"]["value"] is None
    assert result["macro_f1"] is None
    assert result["event_wrong_merge"]["value"] is None
    assert result["tool_parameter_accuracy"]["value"] is None
    assert result["real_tokens"] is None and result["estimated_cost_cny"] is None
    assert ratio(0, 0, "not run")["reason"] == "not run"
    assert metrics([], live=True, event_enabled=True)["real_model_attempts"] is None


def test_shared_network_attempts_are_not_model_calls():
    turn = next(c.turns[0] for c in audit()[0] if c.scenario == "missing-discount")
    response = response_for(turn)
    response["call_counts"] = dict(intent_calls=1, model_calls=2, retrieval_calls=1, tool_calls=0)
    response["budget_used"] = dict(attempts=9, tokens=25, cost="0.01")
    observation = dict(entities={}, tools=[], retrievals=[])
    row = dict(
        case_id="x",
        turn_id="x-1",
        event_id="a",
        response=response,
        observation=observation,
        elapsed_ms=1,
        score=score(turn, response, observation),
    )
    result = metrics([row], live=True, event_enabled=False)
    assert result["real_model_attempts"] == 2
    assert result["shared_budget_attempts"] == 9


def test_nearest_rank_p95_on_small_and_empty_sets():
    assert quantile([], 0.95) is None
    assert quantile([8], 0.95) == 8
    assert quantile([4, 1, 2, 3], 0.5) == 2
    assert quantile([4, 1, 2, 3], 0.95) == 4


def response_for(turn):
    gold = expected(turn)
    return {
        "intent_decision": {"final_code": turn.intent.value if turn.intent else None},
        "outcome": gold["outcome"],
        "facts": [],
        "evidence_refs": [],
        "tool_call_ids": [],
        "missing_slots": [],
        "call_counts": {"intent_calls": 1},
    }


def test_missing_slot_dispatch_is_hard_failure_even_if_intent_correct():
    turn = next(c.turns[0] for c in audit()[0] if c.scenario == "missing-discount")
    response = response_for(turn)
    response["tool_call_ids"] = ["rogue-call"]
    response["missing_slots"] = ["order_id"]
    result = score(turn, response, {"entities": {}, "tools": [], "retrievals": []})
    assert result["checks"]["intent"]
    assert "missing_slots_called_tool" in result["hard_failures"]
    assert not result["completed"]


def test_ownership_fact_leak_cannot_be_offset_by_accuracy():
    turn = next(c.turns[0] for c in audit()[0] if c.scenario == "baseline-ownership")
    response = response_for(turn)
    response["facts"] = [{"name": "order_id", "value": "DEMO-D05", "evidence_ids": []}]
    result = score(turn, response, {"entities": turn.entities, "tools": [], "retrievals": []})
    assert "ownership_fact_leak" in result["hard_failures"]


def test_success_without_verified_facts_or_evidence_rejected():
    turn = audit()[0][0].turns[0]
    response = response_for(turn)
    assert (
        "unsupported_answer"
        in score(turn, response, {"entities": turn.entities, "tools": [], "retrievals": []})[
            "hard_failures"
        ]
    )


def routes():
    row = {"turn_id": "x", "multi_turn": False, "score": {"completed": True, "hard_failures": []}}
    return {
        mode: {
            "rows": [copy.deepcopy(row)],
            "checks": {"persistence": True},
            "metrics": {
                "event_wrong_merge": {"numerator": 0},
                "case_completion_rate": {"numerator": 1},
            },
        }
        for mode in MODES
    }


@pytest.mark.parametrize("failure", ["partial", "hard", "regression", "merge", "persist"])
def test_gate_rejects_each_independent_failure(failure):
    data = routes()
    if failure == "hard":
        data["hybrid"]["rows"][0]["score"]["hard_failures"] = ["unsafe"]
    if failure == "regression":
        data["fasttext"]["rows"][0]["score"]["completed"] = False
    if failure == "merge":
        data["fasttext"]["metrics"]["event_wrong_merge"]["numerator"] = 1
    if failure == "persist":
        data["hybrid"]["checks"]["persistence"] = False
    assert not release_gate(data, complete=failure != "partial")["accepted"]


def test_baseline_comparison_requires_exact_experiment_and_coverage():
    baseline = {
        "format": "m17-evaluation-v1",
        "status": "PASS",
        "experiment_fingerprint": "a",
        "live": True,
        "selected_case_ids": ["x"],
        "routes": routes(),
    }
    current = copy.deepcopy(baseline)
    assert compare_reports(current, baseline)["accepted"]
    current["experiment_fingerprint"] = "b"
    with pytest.raises(ConfigurationError):
        compare_reports(current, baseline)
    current = copy.deepcopy(baseline)
    current["routes"]["hybrid"]["rows"] = []
    assert not compare_reports(current, baseline)["accepted"]
