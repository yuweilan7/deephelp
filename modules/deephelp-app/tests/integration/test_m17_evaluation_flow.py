import argparse
import json

import pytest

from deephelp_app.evaluation.core import DATA, MANIFEST, MODES
from deephelp_app.evaluation.evaluation_cli import run

pytestmark = pytest.mark.integration


async def test_full_offline_http_stdio_evaluation_and_baseline(tmp_path, monkeypatch):
    import deephelp_app.evaluation.evaluation_cli as cli

    monkeypatch.setattr(cli, "local_path", lambda value: tmp_path / value)
    monkeypatch.setattr(
        cli,
        "provenance",
        lambda: {"package_digest": "fixed-offline-test", "tooling_digest": "fixed-offline-tools"},
    )
    args = argparse.Namespace(
        data=str(DATA),
        manifest=str(MANIFEST),
        live=False,
        all_cases=False,
        output="first.json",
        stage="feature",
        auth="auth.json",
        budget_state="budget.json",
        pointer="pointer.json",
        providers="provider.json",
        fasttext_pointer="fasttext.json",
        baseline=None,
        timeout=120,
    )
    assert await run(args) == 0
    report = json.loads((tmp_path / "first.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS" and not report["live"]
    for mode in MODES:
        route = report["routes"][mode]
        assert len(route["rows"]) == 20 and all(route["checks"].values())
        assert route["metrics"]["real_model_attempts"] is None
        assert not any(row["score"]["hard_failures"] for row in route["rows"])
    assert report["routes"]["fasttext"]["capabilities"]["fasttext"] == "not_run_offline"
    assert report["routes"]["memory_event"]["metrics"]["event_wrong_split"]["numerator"] == 1
    args.output = "second.json"
    args.baseline = str(tmp_path / "first.json")
    assert await run(args) == 0
    second = json.loads((tmp_path / "second.json").read_text(encoding="utf-8"))
    assert second["baseline_comparison"]["accepted"]


@pytest.mark.parametrize("modes", [["memory_event"], list(MODES)])
async def test_targeted_offline_mode_runs_only_selected_whole_conversation(
    tmp_path, monkeypatch, modes
):
    import deephelp_app.evaluation.evaluation_cli as cli

    monkeypatch.setattr(cli, "local_path", lambda value: tmp_path / value)
    monkeypatch.setattr(
        cli,
        "provenance",
        lambda: {"package_digest": "offline-scope", "tooling_digest": "offline-scope"},
    )
    args = argparse.Namespace(
        data=str(DATA),
        manifest=str(MANIFEST),
        live=False,
        all_cases=False,
        case=["m17-continuation"],
        mode=modes,
        output="targeted.json",
        stage="feature",
        auth="auth.json",
        budget_state="budget.json",
        pointer="pointer.json",
        providers="provider.json",
        fasttext_pointer="fasttext.json",
        baseline=None,
        timeout=60,
    )
    # The existing deterministic continuation miss must remain a content failure.
    assert await run(args) == 1
    report = json.loads((tmp_path / "targeted.json").read_text(encoding="utf-8"))
    assert report["status"] == "REJECTED"
    assert not report["release_gate"]["checks"]["selected_content"]
    assert report["selected_case_ids"] == ["m17-continuation"]
    assert report["acceptance_scope"] == "targeted"
    assert list(report["routes"]) == modes
    assert len(report["routes"]["memory_event"]["rows"]) == 2
    assert report["release_gate"]["ablation_comparison"] is None
    assert report["routes"]["memory_event"]["checks"]["zero_call_replay"]
