"""Frozen approval evaluation uses fresh business-ledger observations, never old PASS files."""

import argparse
import json
from pathlib import Path
from typing import Any

from deephelp_app.corpus import digest
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation import ratio

APPROVAL_DATA = Path(__file__).parent / "sample_data/m17_approval.json"


def approval_cases() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(APPROVAL_DATA.read_text(encoding="utf-8"))
    if data.get("format") != "m17-approval-freeze-v1":
        raise ConfigurationError("Unknown frozen approval dataset")
    ids = [row["case"] for row in data["operations"]]
    if len(ids) != len(set(ids)) or not ids or not data["required_checks"]:
        raise ConfigurationError("Approval cases must be nonempty and uniquely identified")
    return data


def approval_metrics(report: dict[str, Any]) -> dict[str, Any]:
    frozen = approval_cases()
    rows = report.get("matrix", []) + report.get("observations", [])
    observed = {row["case"]: row for row in rows}
    unauthorized = report.get("unauthorized", [])
    expected_ids = {row["case"] for row in frozen["operations"]}
    observed_ids = [row["case"] for row in rows]
    checks = {
        "fresh_probe_passed": report.get("status") == "PASS",
        "exact_operation_coverage": set(observed_ids) == expected_ids
        and len(observed_ids) == len(expected_ids),
        "exact_unauthorized_coverage": sorted(row["case"] for row in unauthorized)
        == sorted(frozen["unauthorized_cases"]),
        "required_boundaries": all(
            report.get("checks", {}).get(name) is True for name in frozen["required_checks"]
        ),
    }
    failures: list[dict[str, Any]] = []
    for gold in frozen["operations"]:
        row = observed.get(gold["case"], {})
        fields = ("status", "effects", "execute_calls")
        correct = all(row.get(key) == gold[key] for key in fields)
        if gold.get("query_required"):
            correct = correct and row.get("query_calls", 0) >= 1
        if gold.get("replay_required"):
            correct = correct and row.get("replay_unchanged") is True
        if gold["status"] == "CANCELLED":
            correct = correct and row.get("plan_unchanged") is True
        if gold["case"] == "real_http":
            correct = correct and row.get("facts_verified") is True
        checks[gold["case"]] = correct
        if not correct:
            failures.append({"case": gold["case"], "expected": gold, "actual": row})
    unauthorized_writes = sum(
        max(0, row["after"]["effects"] - row["before"]["effects"]) for row in unauthorized
    )
    unauthorized_dispatches = sum(
        max(0, row["after"]["execute_calls"] - row["before"]["execute_calls"])
        for row in unauthorized
    )
    checks["no_unauthorized_dispatch_or_effect"] = bool(unauthorized) and all(
        row.get("rejected") is True and row["before"] == row["after"] for row in unauthorized
    )
    replay_ids = {row["case"] for row in frozen["operations"] if row.get("replay_required")}
    replays = [row for row in rows if row["case"] in replay_ids]
    return {
        "dataset_digest": digest(frozen),
        "selected_case_ids": [row["case"] for row in frozen["operations"]],
        "interpretation": (
            "fresh real MySQL/HTTP/process recovery; fixed-action fault cases "
            "and separately real-model HTTP"
        ),
        "metrics": {
            "operation_completion": ratio(
                sum(checks.get(x, False) for x in expected_ids), len(expected_ids), "not run"
            ),
            "unauthorized_write_attempts": len(unauthorized),
            "unauthorized_business_effects": unauthorized_writes if unauthorized else None,
            "unauthorized_dispatches": unauthorized_dispatches if unauthorized else None,
            "approval_replay_unchanged": ratio(
                sum(row["replay_unchanged"] is True for row in replays), len(replays), "not run"
            ),
            "business_effect_counts": {row["case"]: row["effects"] for row in rows},
            "execute_counts": {row["case"]: row["execute_calls"] for row in rows},
            "query_counts": {row["case"]: row["query_calls"] for row in rows},
        },
        "rows": rows,
        "unauthorized": unauthorized,
        "release_gate": {
            "accepted": all(checks.values()),
            "checks": checks,
            "hard_failures": failures,
        },
    }


async def evaluate_approvals(args: argparse.Namespace, output: Path) -> dict[str, Any]:
    if not args.live:
        raise ConfigurationError(
            "Approval evaluation requires --live; offline is not live recovery"
        )
    from deephelp_app.approval_probe import matrix

    evidence = output.with_name(output.stem + "-approval-evidence.json")
    probe_args = argparse.Namespace(
        live=True,
        stage=args.stage,
        output=evidence,
        budget_state=Path(args.approval_budget_state),
        key=Path(args.rights_key),
        auth=args.auth,
        pointer=args.pointer,
        providers=args.providers,
        port=0,
    )
    report = await matrix(probe_args)
    result = approval_metrics(report)
    result["evidence_path"] = str(evidence)
    return result
