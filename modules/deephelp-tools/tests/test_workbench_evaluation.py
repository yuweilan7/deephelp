import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_tools.learning.workbench_evaluation import (
    EvaluationCatalog,
    evaluation_router,
    report_view,
)
from deephelp_tools.learning.workbench_jobs import LearningJobs

pytestmark = pytest.mark.integration


async def test_cancel_holds_slot_cleanup_and_explicit_bounded_recovery(tmp_path):
    started, finishing, cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def run(row, output):
        assert output.name == f"attempt-{row['attempt']}"
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finishing.set()
            await cleanup.wait()

    jobs = LearningJobs(tmp_path, run)
    row = await jobs.start({"action": "offline"})
    await started.wait()
    cancel = asyncio.create_task(jobs.cancel(row["id"]))
    await finishing.wait()
    # The slot stays reserved while adapters are closing, even during cancellation.
    assert row["state"] == "CANCELLING"
    with pytest.raises(AppError):
        jobs.available()
    # A disconnected cancellation waiter must not cancel adapter cleanup a second time.
    cancel.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancel
    cancel = asyncio.create_task(jobs.cancel(row["id"]))
    await asyncio.sleep(0)
    assert not jobs.worker.done()
    cleanup.set()
    await cancel
    assert row["state"] == "CANCELLED" and row["result"] is None
    for attempt in (2, 3):
        await jobs.start({}, resume=row["id"])
        assert row["attempt"] == attempt
        await jobs.cancel(row["id"])
    with pytest.raises(AppError, match="exhausted"):
        await jobs.start({}, resume=row["id"])
    assert len(row["history"]) == 2


async def test_restart_never_runs_automatically_and_success_is_not_pass(tmp_path):
    calls = []

    async def run(row, output):
        calls.append(output)
        return {"evidence_status": "REJECTED"}

    jobs = LearningJobs(tmp_path, run)
    row = dict(
        id="persisted",
        state="RUNNING",
        attempt=1,
        options={"action": "offline"},
        history=[],
        created_at="2026",
        result=None,
    )
    jobs.save(row)
    restarted = LearningJobs(tmp_path, run)
    assert restarted.get("persisted")["state"] == "INTERRUPTED" and not calls
    resumed = await restarted.start({}, resume="persisted")
    await restarted.worker
    assert resumed["state"] == "SUCCEEDED" and resumed["result"]["evidence_status"] == "REJECTED"
    assert calls[0].name == "attempt-2"


async def test_task_timeout_and_immediate_cancel(tmp_path):
    async def run(row, output):
        await asyncio.Event().wait()

    jobs = LearningJobs(tmp_path, run, timeout=0.01)
    row = await jobs.start({"action": "audit"})
    await jobs.worker
    assert row["state"] == "FAILED" and row["error"] == "TimeoutError"
    immediate = await jobs.start({"action": "audit"})
    await jobs.cancel(immediate["id"])
    assert immediate["state"] == "CANCELLED"


def test_report_projects_real_nested_errors_and_denominators(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(
            dict(
                status="PASS",
                live=True,
                cumulative_usage={"secret": "not exposed"},
                routes={
                    "hybrid": dict(
                        metrics={"accuracy": {"numerator": 1, "denominator": 2}},
                        rows=[
                            dict(
                                case_id="x",
                                text="synthetic",
                                score={"checks": {"facts": False}},
                                response={"reply": "bad fact", "outcome": "ANSWERED"},
                            )
                        ],
                    )
                },
            )
        )
    )
    view = report_view(path)
    assert view["error_count"] == 1 and view["errors"][0]["score"]["facts"] is False
    assert view["metrics"]["hybrid"]["metrics"]["accuracy"]["denominator"] == 2
    assert "secret" not in str(view) and view["model_calls"] == 0
    path.write_text(
        json.dumps(
            dict(
                status="REJECTED",
                rows=[dict(case_id="m18", score={"checks": {"intent": False}})],
                metrics={"completion_rate": {"numerator": 0, "denominator": 1}},
            )
        )
    )
    view = report_view(path)
    assert view["error_count"] == 1 and view["errors"][0]["mode"] == "validation"


async def test_catalog_owner_all_routes_and_no_browser_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    key = tmp_path / "key"
    key.write_bytes(b"k" * 32)
    config = EvaluationCatalog(
        owner=VerifiedIdentity(tenant_id="owner", user_id="a"),
        task_root=Path(".local/jobs"),
        channel="learning",
        redaction_key=key,
        providers=key,
        auth=key,
        budget_state=key,
        pointer=key,
        fasttext_pointer=key,
    )
    foreign = VerifiedIdentity(tenant_id="owner", user_id="b")
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(
        evaluation_router(SimpleNamespace(ledger=None), lambda r: foreign, config, lambda: None)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        paths = [
            "",
            "/reports/trusted",
            "/imports/trusted",
            "/assets/trusted",
            "/plan",
            "/tasks/task",
            "/candidates/" + "a" * 64,
            "/candidates/" + "a" * 64 + "/platform",
            "/releases/" + "a" * 64,
        ]
        for path in paths:
            assert (await c.get("/learning/evaluation" + path)).status_code == 404
        for path, body in [
            ("/capture", {"run_id": "a" * 32}),
            ("/tasks", {"action": "offline", "confirmed": True}),
            ("/tasks", {"action": "platform_upload", "import_id": "trusted", "confirmed": True}),
            (
                "/tasks",
                {
                    "action": "platform_candidate",
                    "candidate_ids": ["a" * 64],
                    "candidate_revision": 0,
                    "confirmed": True,
                },
            ),
            ("/tasks/task/resume", None),
            (
                "/release",
                {"action": "publish", "target": "a" * 64, "revision": 1, "confirmed": True},
            ),
        ]:
            assert (await c.post("/learning/evaluation" + path, json=body)).status_code == 404
        for action in ("shell", "train-arbitrary"):
            assert (
                await c.post(
                    "/learning/evaluation/tasks", json={"action": action, "confirmed": True}
                )
            ).status_code == 422
        assert (
            await c.post(
                "/learning/evaluation/tasks",
                json={
                    "action": "offline",
                    "confirmed": True,
                    "output": "../../anything",
                },
            )
        ).status_code == 422


async def test_heavy_routes_reuse_budget_registry_and_original_gate(tmp_path, monkeypatch):
    import deephelp_tools.learning.workbench_evaluation as module
    from deephelp_app.domain.errors import ConfigurationError
    from deephelp_app.domain.execution import ExecutionBudget

    monkeypatch.chdir(tmp_path)
    key = tmp_path / "key"
    key.write_bytes(b"k" * 32)
    identity = VerifiedIdentity(tenant_id="owner", user_id="a")
    config = EvaluationCatalog(
        owner=identity,
        task_root=Path(".local/jobs"),
        channel="learning",
        redaction_key=key,
        providers=key,
        auth=key,
        budget_state=key,
        pointer=key,
        fasttext_pointer=key,
        assets={"known": key},
    )
    seen = []
    budget = ExecutionBudget.start(90, 20, 2)
    registry = object()
    passing = False
    validation_paths = []

    async def build(store, ids, output, version, gate, providers, original_fasttext):
        assert ids == [] and output.name == "assets" and providers == key
        assert original_fasttext == key and gate.request() is budget
        seen.append("build")
        return {"status": "PREPARED", "version": version}

    async def validate(
        store,
        assets,
        output,
        gate,
        providers,
        auth,
        *,
        registry,
        prompt_artifact,
        persist_assets_validation,
    ):
        assert assets == key and output.name == "validation.json" and gate.request() is budget
        assert registry is assembly.sop_registry and auth == providers == key
        assert prompt_artifact is config.prompt_artifact
        assert persist_assets_validation is False
        validation_paths.append(output)
        seen.append("validate")
        return {"status": "PASS" if passing else "REJECTED", "checks": {"facts": passing}}

    def prepare(*args, **kwargs):
        assert kwargs["prompt_artifact"] is config.prompt_artifact
        assert kwargs["validation_path"] == validation_paths[-1]
        assert len(args[3]) == 36 and args[3].startswith("m22-")
        seen.append("prepare")
        raise ConfigurationError("Exact current byte gate rejected")

    monkeypatch.setattr(module, "build", build)
    monkeypatch.setattr(module, "validate", validate)
    monkeypatch.setattr(module, "prepare_manifest", prepare)
    assembly = SimpleNamespace(ledger=SimpleNamespace(pool=object()), sop_registry=registry)
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(evaluation_router(assembly, lambda r: identity, config, lambda: budget))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        for action, expected in (
            ("build", "PREPARED"),
            ("validate", "REJECTED"),
            ("prepare", None),
            ("validate", "PASS"),
            ("prepare", None),
        ):
            if expected == "PASS":
                passing = True
            r = await c.post(
                "/learning/evaluation/tasks",
                json={
                    "action": action,
                    "asset_id": "known" if action != "build" else None,
                    "confirmed": True,
                },
            )
            assert r.status_code == 200
            job_id = r.json()["id"]
            for _ in range(100):
                row = (await c.get("/learning/evaluation/tasks/" + job_id)).json()
                if row["state"] != "RUNNING":
                    break
                await asyncio.sleep(0.001)
            if expected:
                assert row["state"] == "SUCCEEDED" and row["result"]["evidence_status"] == expected
            else:
                assert row["state"] == "FAILED"
                expected_error = (
                    "Exact current byte gate rejected"
                    if passing
                    else "Latest validation task is not PASS; cannot prepare"
                )
                assert row["diagnostic"]["message"] == expected_error
        assert seen == ["build", "validate", "validate", "prepare"]
        assert key.read_bytes() == b"k" * 32


async def test_platform_worker_cancel_closes_child_before_partial_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    key = tmp_path / "key"
    key.write_bytes(b"k" * 32)
    identity = VerifiedIdentity(tenant_id="owner", user_id="a")
    config = EvaluationCatalog(
        owner=identity,
        task_root=Path(".local/jobs"),
        channel="learning",
        redaction_key=key,
        providers=key,
        auth=key,
        budget_state=key,
        pointer=key,
        fasttext_pointer=key,
        platform_config=key,
        imports={
            "trusted": dict(report=key, data=key, manifest=key, cases=["x"], modes=["hybrid"])
        },
    )
    started, closed = asyncio.Event(), asyncio.Event()
    child = SimpleNamespace(returncode=None)

    async def wait():
        await closed.wait()
        child.returncode = -1
        return -1

    def kill():
        closed.set()

    child.wait, child.kill = wait, kill

    async def spawn(*args, **kwargs):
        assert args[1:4] == ("-m", "deephelp_tools.evaluation.langfuse_cli", "--live")
        path = Path(args[args.index("--state") + 1])
        path.parent.mkdir(parents=True)
        await asyncio.to_thread(
            path.write_text, json.dumps(dict(status="RUNNING", rows={"one": "VERIFIED"}))
        )
        started.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(
        evaluation_router(
            SimpleNamespace(ledger=object()), lambda r: identity, config, lambda: None
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        response = await client.post(
            "/learning/evaluation/tasks",
            json=dict(action="platform_upload", import_id="trusted", confirmed=True),
        )
        assert response.status_code == 200
        row = response.json()
        await started.wait()
        cancelled = await client.post(f"/learning/evaluation/tasks/{row['id']}/cancel")
        assert cancelled.json()["state"] == "CANCELLED" and closed.is_set()
        state = json.loads((tmp_path / ".local/jobs/imports/trusted.json").read_text())
        assert state["status"] == "CANCELLED" and len(state["rows"]) == 1


async def test_platform_annotation_still_requires_confirmation_label_isolation_and_cas(
    tmp_path, monkeypatch
):
    import deephelp_tools.learning.workbench_evaluation as module
    from deephelp_app.domain.errors import ConfigurationError
    from deephelp_app.domain.feedback import FeedbackCandidate
    from deephelp_tools.evaluation.flywheel import reviewed
    from deephelp_tools.evaluation.langfuse_prompt_review import SCORE_NAME, candidate_binding

    monkeypatch.chdir(tmp_path)
    key = tmp_path / "key"
    key.write_bytes(b"k" * 32)
    owner = VerifiedIdentity(tenant_id="owner", user_id="a")
    config = EvaluationCatalog(
        owner=owner,
        task_root=Path(".local/jobs"),
        channel="learning",
        redaction_key=key,
        providers=key,
        auth=key,
        budget_state=key,
        pointer=key,
        fasttext_pointer=key,
        platform_config=key,
    )
    candidate = FeedbackCandidate(
        candidate_id="a" * 64,
        run_id="b" * 32,
        source_group="m29-fresh",
        variant_group="m29-fresh",
        text="订单参加什么活动",
        observation={},
    )
    binding = candidate_binding(candidate)
    evidence = dict(
        status="EXPORTED",
        binding=binding,
        scores=[
            dict(
                id="annotation",
                source="ANNOTATION",
                authorUserId="learner",
                name=SCORE_NAME,
                dataType="CATEGORICAL",
                stringValue="ORDER_ACTIVITY_QUERY",
                traceId=binding["trace_id"],
                observationId=binding["observation_id"],
            )
        ],
    )
    commits = []
    race = False

    class Store:
        def __init__(self, pool):
            pass

        async def get(self, identity):
            return candidate

        async def review(self, identity, expected, route, review, **kwargs):
            nonlocal candidate
            if race or expected != candidate.revision:
                raise ConfigurationError("Candidate revision CAS rejected")
            result = reviewed(candidate, route, review)
            commits.append(result)
            candidate = result
            return result

    async def read(platform, current):
        return evidence

    monkeypatch.setattr(module, "FeedbackStore", Store)
    monkeypatch.setattr(module, "annotations", read)
    monkeypatch.setattr("deephelp_app.adapters.native_observation.configuration", lambda p: {})
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(
        evaluation_router(
            SimpleNamespace(ledger=SimpleNamespace(pool=object())),
            lambda r: owner,
            config,
            lambda: None,
        )
    )
    body = dict(
        revision=0,
        route="corpus",
        review=dict(
            state="approved",
            label="ORDER_ACTIVITY_QUERY",
            method="human",
            reviewer="untrusted-browser-name",
            reason="synthetic UI protocol evidence",
            independent_from_heldout=True,
        ),
        reason="synthetic UI protocol evidence",
        confirmed=True,
        platform_score_id="annotation",
    )
    path = "/learning/evaluation/candidates/" + candidate.candidate_id + "/review"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.post(path, json={**body, "confirmed": False})).status_code == 422
        for label, independent in (("DISCOUNT_MISSING", True), ("ORDER_ACTIVITY_QUERY", False)):
            altered = {
                **body,
                "review": {
                    **body["review"],
                    "label": label,
                    "independent_from_heldout": independent,
                },
            }
            assert (await client.post(path, json=altered)).status_code == 409
        race = True
        assert (await client.post(path, json=body)).status_code == 409
        race = False
        assert not commits and candidate.revision == 0
        result = await client.post(path, json=body)
        assert result.status_code == 200 and result.json()["revision"] == 1
        review = commits[0].reviews["corpus"]
        assert review.reviewer == "owner/a" and "annotation" in review.reason
        assert "candidate r0" in review.reason and "sha256=" in review.reason
        assert (await client.post(path, json=body)).status_code == 409 and len(commits) == 1
