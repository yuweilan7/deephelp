import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from deephelp_app.application.corpus import digest
from deephelp_app.domain.errors import ConfigurationError
from deephelp_tools.evaluation.core import DATA, MANIFEST, audit, expected
from deephelp_tools.evaluation.langfuse_mapping import ImportIds, import_lock, plan_import, upload

pytestmark = pytest.mark.unit


@pytest.fixture
def historical(tmp_path):
    cases, summary = audit()
    chosen = cases[:2]
    rows = [
        dict(
            case_id=c.case_id,
            turn_id=t.turn_id,
            text=t.text,
            split=c.split,
            response={"run_id": "historical", "reply": "synthetic"},
            score=dict(
                gold=expected(t),
                checks={"facts": False, "empty": None},
                hard_failures=["amount"],
                completed=False,
            ),
        )
        for c in chosen
        for t in c.turns
    ]
    report = dict(
        format="m17-evaluation-v1",
        status="REJECTED",
        dataset=summary,
        selected_case_ids=[c.case_id for c in chosen],
        routes={
            "hybrid": dict(
                rows=rows,
                capabilities={"events": False},
                metrics={"empty": {"value": None, "denominator": 0}},
            )
        },
        provenance={"package_digest": "historical-code"},
        configuration={"models": "old"},
        experiment_fingerprint="old",
        release_gate={"accepted": False},
    )
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path, report, [c.case_id for c in chosen]


def test_complete_frozen_subset_keeps_hard_failure_and_null_denominator(historical):
    path, report, ids = historical
    plan = plan_import(path, DATA, MANIFEST, ids, ["hybrid"])
    assert plan == plan_import(path, DATA, MANIFEST, ids, ["hybrid"])
    assert plan["summary"]["release_gate"] == {"accepted": False}
    metrics = plan["summary"]["original_route_metrics"]["hybrid"]
    assert metrics["capabilities"] == {"events": False}
    assert metrics["metrics"]["empty"] == {"value": None, "denominator": 0}
    assert all(r["scores"]["facts"] is False and r["scores"]["empty"] is None for r in plan["rows"])
    assert (
        json.loads(plan["rows"][0]["output"]["source_row_json"])
        == report["routes"]["hybrid"]["rows"][0]
    )
    assert len({r["trace_id"] for r in plan["rows"]}) == len(plan["rows"])


@pytest.mark.parametrize("change", ["frozen", "missing", "duplicate", "text", "gold", "pending"])
def test_changed_frozen_or_incomplete_source_rejected(historical, change):
    path, original, ids = historical
    report = copy.deepcopy(original)
    rows = report["routes"]["hybrid"]["rows"]
    if change == "frozen":
        report["dataset"]["data_digest"] = "wrong"
    elif change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "text":
        rows[0]["text"] = "different"
    elif change == "gold":
        rows[0]["score"]["gold"] = {"amount": 999}
    else:
        report["status"] = "RUNNING"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        plan_import(path, DATA, MANIFEST, ids, ["hybrid"])


def test_os_lock_and_ids_are_owned_until_exit(tmp_path):
    from deephelp_tools.evaluation.langfuse_mapping import _ids

    token = _ids.set(("a" * 32, "b" * 16))
    try:
        assert ImportIds().generate_trace_id() == int("a" * 32, 16)
        assert ImportIds().generate_span_id() == int("b" * 16, 16)
    finally:
        _ids.reset(token)
    with import_lock(tmp_path / "state.json"), pytest.raises(ConfigurationError):
        with import_lock(tmp_path / "state.json"):
            pass
    with import_lock(tmp_path / "state.json"):
        pass


async def test_partial_and_cancel_resume_readback_without_replaying_business(
    historical, tmp_path, monkeypatch
):
    import deephelp_tools.evaluation.langfuse_mapping as module

    path, _, ids = historical
    plan = plan_import(path, DATA, MANIFEST, ids, ["hybrid"])

    class SDK:
        def __init__(self, **kwargs):
            raise AssertionError("Verified recovery must not initialize a writer SDK")

        def shutdown(self):
            pass

    monkeypatch.setattr(module, "Langfuse", SDK)
    verified = set()
    interrupted = True
    extra = False

    class Fake:
        config = {"host": "http://127.0.0.1", "public_key": "test", "secret_key": "private"}

        async def request(self, *args, **kwargs):
            if args[1] in {"dataset-items", "experiment-items"}:
                field = "item_id" if args[1] == "dataset-items" else "observation_id"
                return {
                    "data": [{"id": r[field]} for r in plan["rows"]]
                    + ([{"id": "foreign"}] if extra else [])
                }
            return {
                "id": "dataset",
                "projectId": "project",
                "metadata": module.dataset_metadata(plan),
            }

        async def verify(self, row, source):
            nonlocal interrupted
            assert source is plan
            if interrupted and verified:
                raise asyncio.CancelledError
            verified.add(row["item_id"])
            return True

    state = tmp_path / "state.json"
    with pytest.raises(asyncio.CancelledError):
        await upload(plan, state, Fake())
    partial = json.loads(state.read_text())
    assert partial["status"] == "CANCELLED" and len(partial["rows"]) == 1
    interrupted = False
    complete = await upload(plan, state, Fake())
    assert complete["status"] == "COMPLETE" and len(complete["rows"]) == len(plan["rows"])
    assert complete["model_calls"] == 0
    bad = copy.deepcopy(plan)
    bad["identity"] = digest("changed")
    with pytest.raises(ConfigurationError, match="another source"):
        await upload(bad, state, Fake())
    extra = True
    with pytest.raises(ConfigurationError, match="coverage differs"):
        await upload(plan, state, Fake())
    assert json.loads(state.read_text())["status"] == "PARTIAL"


async def test_readback_requires_exact_observation_all_scores_and_null_is_not_zero(historical):
    from deephelp_tools.evaluation.langfuse_mapping import Platform

    path, _, ids = historical
    plan = plan_import(path, DATA, MANIFEST, ids, ["hybrid"])
    row = plan["rows"][0]
    item = {
        "input": row["input"],
        "expectedOutput": row["expected"],
        "metadata": row["metadata"],
        "datasetName": plan["dataset_name"],
    }
    observation = {
        "id": row["observation_id"],
        "traceId": row["trace_id"],
        "endTime": "done",
        "input": json.dumps(row["input"]),
        "output": json.dumps(row["output"]),
        "expectedOutput": json.dumps(row["expected"]),
        "experimentItemId": row["item_id"],
    }
    scores = [
        {"id": digest([plan["identity"], row["key"], k]), "name": k, "value": float(v)}
        for k, v in row["scores"].items()
        if v is not None
    ]

    async def request(method, endpoint, **kwargs):
        if endpoint == "v3/scores":
            assert kwargs["params"]["traceId"] == row["trace_id"]
            assert kwargs["params"]["observationId"] == row["observation_id"]
        return (
            item
            if endpoint.startswith("dataset-items/")
            else {"data": [observation] if endpoint == "experiment-items" else scores}
        )

    platform = Platform(SimpleNamespace(), {})
    monkey = platform.request
    platform.request = request
    assert await platform.verify(row, plan)
    removed = scores.pop()
    assert not await platform.verify(row, plan)
    scores.append(removed)
    observation["output"] = {"changed": True}
    assert not await platform.verify(row, plan)
    platform.request = monkey
