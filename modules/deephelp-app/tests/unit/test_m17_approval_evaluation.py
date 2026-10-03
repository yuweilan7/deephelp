import copy

import pytest

from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation.evaluation_approval import (
    approval_cases,
    approval_metrics,
    evaluate_approvals,
)
from deephelp_app.evaluation.evaluation_cli import compare_reports

pytestmark = pytest.mark.unit


def evidence():
    frozen = approval_cases()
    rows = []
    for gold in frozen["operations"]:
        rows.append(
            dict(
                gold,
                query_calls=int(gold.get("query_required", False)),
                replay_unchanged=True,
                plan_unchanged=True,
                facts_verified=True,
            )
        )
    return dict(
        status="PASS",
        matrix=rows,
        observations=[],
        checks=dict.fromkeys(frozen["required_checks"], True),
        unauthorized=[
            dict(
                case=name,
                rejected=True,
                before=dict(effects=0, execute_calls=0, query_calls=0),
                after=dict(effects=0, execute_calls=0, query_calls=0),
            )
            for name in frozen["unauthorized_cases"]
        ],
    )


def test_exact_frozen_protocol_effects_and_replay_denominators():
    result = approval_metrics(evidence())
    assert result["release_gate"]["accepted"]
    assert result["metrics"]["operation_completion"]["numerator"] == 17
    assert result["metrics"]["approval_replay_unchanged"]["denominator"] == 9
    assert result["metrics"]["business_effect_counts"]["reject"] == 0
    assert result["metrics"]["execute_counts"]["definite_absence"] == 2


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "effect",
        "dispatch",
        "query",
        "replay",
        "facts",
        "plan",
        "boundary",
        "unauthorized",
        "no_denominator",
    ],
)
def test_average_completion_cannot_hide_approval_safety_failure(damage):
    data = evidence()
    if damage == "missing":
        data["matrix"].pop()
    elif damage == "duplicate":
        data["matrix"].append(copy.deepcopy(data["matrix"][0]))
    elif damage in {"effect", "dispatch", "query", "replay", "facts", "plan"}:
        case, field, value = {
            "effect": ("reject", "effects", 1),
            "dispatch": ("real_http", "execute_calls", 2),
            "query": ("lost_tool_response", "query_calls", 0),
            "replay": ("real_http", "replay_unchanged", False),
            "facts": ("real_http", "facts_verified", False),
            "plan": ("revoke", "plan_unchanged", False),
        }[damage]
        next(row for row in data["matrix"] if row["case"] == case)[field] = value
    elif damage == "boundary":
        data["checks"].pop("ordinary_message_does_not_approve")
    elif damage == "unauthorized":
        data["unauthorized"][0]["after"]["execute_calls"] = 1
    else:
        data["unauthorized"] = []
    assert not approval_metrics(data)["release_gate"]["accepted"]


def test_not_run_effect_metrics_remain_null_and_rejected():
    result = approval_metrics({})
    assert result["metrics"]["unauthorized_business_effects"] is None
    assert result["metrics"]["approval_replay_unchanged"]["value"] is None
    assert not result["release_gate"]["accepted"]


def test_approval_baseline_scope_and_hard_gate_are_mandatory():
    report = dict(
        format="m17-evaluation-v1",
        status="PASS",
        live=True,
        experiment_fingerprint="same",
        selected_case_ids=["approval"],
        routes={},
        approvals=approval_metrics(evidence()),
    )
    assert compare_reports(report, copy.deepcopy(report))["accepted"]
    old = copy.deepcopy(report)
    old.pop("approvals")
    assert not compare_reports(report, old)["accepted"]
    bad = copy.deepcopy(report)
    bad["approvals"]["release_gate"]["accepted"] = False
    assert not compare_reports(bad, report)["accepted"]


async def test_offline_does_not_run_live_approval_matrix(tmp_path):
    import argparse

    with pytest.raises(ConfigurationError, match="requires --live"):
        await evaluate_approvals(argparse.Namespace(live=False), tmp_path / "unused.json")
