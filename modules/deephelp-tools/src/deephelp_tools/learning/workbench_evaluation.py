"""Explicit owner-only M17/M18 extension, with a server-owned artifact catalog."""

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.routing import APIRoute
from pydantic import Field

from deephelp_app.adapters.asset_integrity import file_digest
from deephelp_app.adapters.release_assets import verify_release
from deephelp_app.adapters.release_store import ReleaseStore
from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.application.artifact_paths import resolve_artifact, state_root
from deephelp_app.application.dense import atomic_json
from deephelp_app.application.intent_prompt import read_prompt
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, LiveAssembly
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.feedback import Route, RouteReview
from deephelp_app.domain.models.common import DTO, ErrorCode, Identifier, VerifiedIdentity
from deephelp_tools.evaluation.core import DATA, MANIFEST, audit
from deephelp_tools.evaluation.evaluation_cli import run as evaluate
from deephelp_tools.evaluation.flywheel_assets import build, exports, verify_files
from deephelp_tools.evaluation.flywheel_store import FeedbackStore
from deephelp_tools.evaluation.flywheel_validation import validate, validation_cases
from deephelp_tools.evaluation.langfuse_mapping import Platform, plan_import
from deephelp_tools.evaluation.langfuse_prompt_review import annotations, require_annotation
from deephelp_tools.evaluation.release_prepare import prepare_manifest
from deephelp_tools.learning.workbench_jobs import LearningJobs

Key = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class HistoricalImport(DTO):
    report: Path
    data: Path
    manifest: Path
    cases: tuple[Key, ...] = Field(min_length=1, max_length=20)
    modes: tuple[Key, ...] = Field(min_length=1, max_length=4)


class EvaluationCatalog(DTO):
    owner: VerifiedIdentity
    task_root: Path
    reports: dict[Key, Path] = Field(default_factory=dict, max_length=16)
    assets: dict[Key, Path] = Field(default_factory=dict, max_length=16)
    channel: Key
    providers: Path
    auth: Path
    budget_state: Path
    pointer: Path
    fasttext_pointer: Path
    redaction_key: Path
    platform_config: Path | None = None
    imports: dict[Key, HistoricalImport] = Field(default_factory=dict, max_length=16)
    prompt_artifact: Path | None = None


class Capture(DTO):
    run_id: Identifier


class Review(DTO):
    revision: Annotated[int, Field(strict=True, ge=0)]
    route: Route | None
    review: RouteReview | None
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    confirmed: Literal[True]
    platform_score_id: Key | None = None


class JobInput(DTO):
    action: Literal[
        "audit", "offline", "build", "validate", "prepare", "platform_upload", "platform_candidate"
    ]
    candidate_ids: tuple[Hash, ...] = Field(default=(), max_length=10)
    asset_id: Key | None = None
    import_id: Key | None = None
    candidate_revision: Annotated[int, Field(strict=True, ge=0)] | None = None
    confirmed: Literal[True]


class Publish(DTO):
    action: Literal["publish", "rollback"]
    target: Hash
    revision: Annotated[int, Field(strict=True, ge=0)]
    confirmed: Literal[True]


class SharedGate(BudgetSession):
    """The original build/validation APIs consume only request(); reuse the open writer."""

    def __init__(self, factory: Callable[[], ExecutionBudget]):
        self.factory = factory

    def request(self) -> ExecutionBudget:
        return self.factory()


class LearningRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Any:
            try:
                return await original(request)
            except ConfigurationError as exc:
                raise AppError(ErrorCode.VERSION_CONFLICT, str(exc), 409) from exc

        return handler


def report_view(path: Path, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    path = resolve_artifact(path)
    if path.stat().st_size > 8_000_000:
        raise ConfigurationError("Report exceeds browser catalog bound; use its summary index")
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = {
        k: data[k]
        for k in (
            "format",
            "status",
            "live",
            "acceptance_scope",
            "interpretation",
            "scope",
            "dataset",
            "selected_case_ids",
            "release_gate",
            "checks",
            "experiment_fingerprint",
            "code_digest",
            "assets_digest",
            "frozen_m17_digest",
            "synthetic",
        )
        if k in data
    }
    errors = []
    metrics = {}
    routes = data.get("routes", {})
    if "rows" in data:
        routes = routes | {
            "validation": {k: data[k] for k in ("rows", "metrics", "checks") if k in data}
        }
    for mode, route in routes.items():
        metrics[mode] = {k: route[k] for k in ("metrics", "capabilities", "checks") if k in route}
        for row in route.get("rows", []):
            scoring = row.get("score", {}).get("checks", row.get("checks", {}))
            if any(value is False for value in scoring.values()) or row.get("error"):
                errors.append(
                    dict(
                        mode=mode,
                        case_id=row.get("case_id"),
                        turn_id=row.get("turn_id"),
                        text=row.get("text"),
                        score=scoring,
                        outcome=row.get("response", {}).get("outcome"),
                        reply=row.get("response", {}).get("reply"),
                    )
                )
    return dict(
        sha256=file_digest(path),
        summary=summary,
        metrics=metrics,
        errors=errors[offset : offset + limit],
        error_count=len(errors),
        next_offset=offset + limit if offset + limit < len(errors) else None,
        note="Historical evidence; not current runtime acceptance or enterprise generalization",
        model_calls=0,
    )


def evaluation_router(
    assembly: LiveAssembly,
    auth: BrowserAuth,
    config: EvaluationCatalog,
    factory: Callable[[], ExecutionBudget],
) -> APIRouter:
    if len(config.redaction_key.read_bytes()) != 32:
        raise ConfigurationError("Feedback requires an existing stable 32-byte redaction key")
    root = config.task_root.resolve()
    if not root.is_relative_to(state_root()):
        raise ConfigurationError("Learning task storage must be inside the state root")
    jobs: LearningJobs

    def owner(request: Request) -> None:
        if auth(request) != config.owner:
            raise AppError(ErrorCode.NOT_FOUND, "Learning catalog unavailable", 404)
        if assembly.ledger is None:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "PostgreSQL runtime unavailable", 503)

    def feedback() -> FeedbackStore:
        assert assembly.ledger is not None
        return FeedbackStore(assembly.ledger.pool)

    def release() -> ReleaseStore:
        assert assembly.ledger is not None
        return ReleaseStore(assembly.ledger.pool)

    def asset_path(identity: str | None) -> Path:
        if identity in config.assets:
            return resolve_artifact(config.assets[identity])
        if identity and identity.startswith("task-"):
            row = jobs.get(identity[5:])
            path = jobs.output(row) / "assets" / "assets.json"
            if row["options"]["action"] == "build" and path.is_file():
                return path
        raise AppError(ErrorCode.NOT_FOUND, "Candidate asset unavailable", 404)

    async def run_job(row: dict[str, Any], output: Path) -> dict[str, Any]:
        output.mkdir(parents=True, exist_ok=False)  # noqa: ASYNC240 -- bounded durable job path
        options, action = row["options"], row["options"]["action"]
        gate = SharedGate(factory)
        if action in {"platform_upload", "platform_candidate"}:
            assert config.platform_config is not None
            if action == "platform_upload":
                source = config.imports[options["import_id"]]
                path = root / "imports" / (options["import_id"] + ".json")
                import_args = [
                    sys.executable,
                    "-m",
                    "deephelp_tools.evaluation.langfuse_cli",
                    "--live",
                ]
                for key, value in {
                    "report": source.report,
                    "data": source.data,
                    "manifest": source.manifest,
                    "config": config.platform_config,
                    "state": path,
                }.items():
                    import_args.extend(["--" + key, str(value)])
                for key, values in (("case", source.cases), ("mode", source.modes)):
                    for value in values:
                        import_args.extend(["--" + key, value])
            else:
                path = output / "candidate.json"
                import_args = [
                    sys.executable,
                    "-m",
                    "deephelp_tools.evaluation.langfuse_prompt_review",
                    "candidate",
                    "--live",
                    "--config",
                    str(config.platform_config),
                    "--candidate-id",
                    options["candidate_ids"][0],
                    "--revision",
                    str(options["candidate_revision"]),
                    "--output",
                    str(path),
                ]
            # SDK resources are keyed by project public key. A child owns its queues
            # so shutting them down cannot close the application's native observer.
            child = await asyncio.create_subprocess_exec(
                *import_args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            try:
                async with asyncio.timeout(660):
                    code = await child.wait()
                if code:
                    raise ConfigurationError(
                        "Historical importer failed; durable partial state retained"
                    )
            except BaseException as exc:
                if child.returncode is None:
                    child.kill()
                    await asyncio.shield(child.wait())
                if path.is_file():
                    partial = json.loads(path.read_text(encoding="utf-8"))
                    partial.update(
                        status="CANCELLED" if isinstance(exc, asyncio.CancelledError) else "PARTIAL"
                    )
                    atomic_json(path, partial)
                raise
            state = json.loads(path.read_text(encoding="utf-8"))
            if action == "platform_candidate":
                return dict(
                    evidence_status=state["status"],
                    model_calls=0,
                    url=state["url"],
                    candidate_id=state["binding"]["metadata"]["candidate_id"],
                    revision=state["binding"]["metadata"]["revision"],
                )
            return dict(
                evidence_status=state["status"],
                model_calls=0,
                expected_rows=state["expected_rows"],
                source_report_sha256=state["source_report_sha256"],
            )
        if action == "audit":
            cases, summary = await asyncio.to_thread(audit)
            return dict(
                evidence_status="PASS",
                model_calls=0,
                dataset=summary,
                cases=[
                    dict(case_id=c.case_id, split=c.split, messages=len(c.turns)) for c in cases
                ],
            )
        if action == "offline":
            report = output / "evaluation.json"
            args = argparse.Namespace(
                data=str(DATA),
                manifest=str(MANIFEST),
                output=str(report),
                live=False,
                stage="feature",
                mode=None,
                case=None,
                all_cases=False,
                split=None,
                full_evaluation=False,
                approvals=False,
                baseline=None,
                timeout=900,
                auth=str(config.auth),
                budget_state=str(config.budget_state),
                pointer=str(config.pointer),
                providers=str(config.providers),
                fasttext_pointer=str(config.fasttext_pointer),
            )
            await evaluate(args)
            view = await asyncio.to_thread(report_view, report)
            return dict(
                evidence_status=view["summary"]["status"],
                model_calls=0,
                report_sha256=view["sha256"],
            )
        if action == "build":
            data = await build(
                feedback(),
                options["candidate_ids"],
                output / "assets",
                "m22-" + row["id"] + "-" + str(row["attempt"]),
                gate,
                config.providers,
                config.fasttext_pointer,
            )
            return dict(
                evidence_status=data["status"],
                asset_id="task-" + row["id"],
                version=data["version"],
                active_changed=False,
            )
        assets = asset_path(options["asset_id"])
        if action == "validate":
            validation_report = await validate(
                feedback(),
                assets,
                output / "validation.json",
                gate,
                config.providers,
                config.auth,
                registry=assembly.sop_registry,
                prompt_artifact=config.prompt_artifact,
                persist_assets_validation=False,
            )
            return dict(
                evidence_status=validation_report["status"],
                checks=validation_report["checks"],
                active_changed=False,
            )
        validations = sorted(
            (
                task
                for task in jobs.rows.values()
                if task["options"]["action"] == "validate"
                and task["options"]["asset_id"] == options["asset_id"]
            ),
            key=lambda task: (task["updated_at"], task["id"]),
            reverse=True,
        )
        validation_path = None
        if validations:
            latest = validations[0]
            if (
                latest["state"] != "SUCCEEDED"
                or (latest.get("result") or {}).get("evidence_status") != "PASS"
            ):
                raise ConfigurationError("Latest validation task is not PASS; cannot prepare")
            validation_path = jobs.output(latest) / "validation.json"
        manifest = await asyncio.to_thread(
            prepare_manifest,
            assets,
            config.providers,
            assembly.sop_registry,
            "m22-" + row["id"],
            prompt_artifact=config.prompt_artifact,
            validation_path=validation_path,
        )
        # This registers an immutable full release only after the original M18 exact gate.
        identity = await release().register(manifest)
        return dict(
            evidence_status="READY",
            release_hash=identity,
            version=manifest.version,
            active_changed=False,
            model_calls=0,
        )

    jobs = LearningJobs(root, run_job)

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await jobs.close()

    router = APIRouter(prefix="/learning/evaluation", lifespan=lifespan, route_class=LearningRoute)

    @router.get("")
    async def catalog(request: Request) -> dict[str, Any]:
        owner(request)
        _, dataset = await asyncio.to_thread(audit)
        rows = await feedback().fresh_rows(
            "SELECT candidate_id,revision,body FROM dh_m18_candidates "
            "ORDER BY candidate_id LIMIT 50",
            (),
        )
        versions = await release().fresh_rows(
            "SELECT release_hash,version,state FROM dh_m18_releases ORDER BY version LIMIT 50",
            (),
        )
        pointer = await release().active(config.channel)
        sources = await feedback().fresh_rows(
            "SELECT r.run_id FROM dh_m08_runs r JOIN dh_m08_messages m ON m.run_id=r.run_id "
            "LEFT JOIN dh_m18_candidates c ON c.run_id=r.run_id "
            "WHERE m.tenant_id='synthetic-tenant' AND m.user_id='synthetic-user-a' "
            "AND (m.body->>'channel')='m18-collect' "
            "AND r.response IS NOT NULL AND r.status<>'RUNNING' "
            "AND c.candidate_id IS NULL LIMIT 20",
            (),
        )
        return dict(
            dataset=dataset,
            reports=list(config.reports),
            platform_imports=list(config.imports) if config.platform_config else [],
            platform_review_enabled=config.platform_config is not None,
            prompt=read_prompt(config.prompt_artifact).model_dump(mode="json")
            if config.prompt_artifact
            else None,
            assets=list(config.assets)
            + [
                "task-" + r["id"]
                for r in jobs.rows.values()
                if r["options"]["action"] == "build"
                and (jobs.output(r) / "assets" / "assets.json").is_file()
            ],
            candidates=[dict(id=r[0], revision=r[1], text=json.loads(r[2])["text"]) for r in rows],
            capture_sources=[r[0] for r in sources],
            releases=[dict(hash=r[0], version=r[1], state=r[2]) for r in versions],
            pointer=pointer.model_dump(mode="json"),
            tasks=[
                {k: r[k] for k in ("id", "options", "state", "attempt", "updated_at")}
                for r in sorted(jobs.rows.values(), key=lambda r: r["created_at"], reverse=True)[
                    :20
                ]
            ],
            model_calls=0,
            owner=config.owner.model_dump(mode="json"),
            code=[
                "evaluation/evaluation_cli.py:run",
                "evaluation/flywheel_store.py:review",
                "evaluation/flywheel_assets.py:build",
                "evaluation/flywheel_validation.py:validate",
                "evaluation/release_prepare.py:prepare_manifest",
                "adapters/release_store.py:switch",
            ],
        )

    @router.get("/reports/{identity}")
    async def report(
        request: Request, identity: Key, offset: Annotated[int, Query(ge=0)] = 0
    ) -> dict[str, Any]:
        owner(request)
        if identity not in config.reports:
            raise AppError(ErrorCode.NOT_FOUND, "Frozen report unavailable", 404)
        return await asyncio.to_thread(report_view, config.reports[identity], offset)

    @router.get("/candidates/{identity}")
    async def candidate(request: Request, identity: Hash) -> dict[str, Any]:
        owner(request)
        store = feedback()
        return dict(
            candidate=(await store.get(identity)).model_dump(mode="json"),
            derivatives=await store.lineage(identity),
            tasks=await store.tasks(identity),
        )

    @router.get("/imports/{identity}")
    async def imported(request: Request, identity: Key) -> dict[str, Any]:
        owner(request)
        if identity not in config.imports or config.platform_config is None:
            raise AppError(ErrorCode.NOT_FOUND, "Historical import unavailable", 404)
        source = config.imports[identity]
        plan = await asyncio.to_thread(
            plan_import,
            source.report,
            source.data,
            source.manifest,
            list(source.cases),
            list(source.modes),
        )
        path = root / "imports" / (identity + ".json")
        state = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.is_file()
            else {"status": "NOT_UPLOADED"}
        )
        if state.get("identity") not in {None, plan["identity"]}:
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Import source changed; use a new catalog identity", 409
            )
        return dict(
            source="langfuse",
            summary=plan["summary"],
            expected_rows=len(plan["rows"]),
            state=state,
            model_calls=0,
            note="Historical mapping; platform scores do not publish or approve",
        )

    async def platform_evidence(identity: str) -> tuple[Any, dict[str, Any]]:
        from deephelp_app.adapters.native_observation import configuration

        if config.platform_config is None:
            raise AppError(ErrorCode.NOT_FOUND, "Platform review is not configured", 404)
        current_candidate = await feedback().get(identity)
        async with httpx.AsyncClient() as client:
            evidence = await annotations(
                Platform(client, configuration(str(config.platform_config))), current_candidate
            )
        return current_candidate, evidence

    @router.get("/candidates/{identity}/platform")
    async def platform_candidate(request: Request, identity: Hash) -> dict[str, Any]:
        owner(request)
        current_candidate, evidence = await platform_evidence(identity)
        return dict(
            candidate_id=identity, revision=current_candidate.revision, **evidence, model_calls=0
        )

    @router.post("/capture")
    async def capture(request: Request, body: Capture) -> dict[str, Any]:
        owner(request)
        row = await feedback().capture(
            body.run_id,
            config.redaction_key.read_bytes(),
            "m22-source-" + body.run_id,
            "m22-source-" + body.run_id,
        )
        return row.model_dump(mode="json")

    @router.post("/candidates/{identity}/review")
    async def review(request: Request, identity: Hash, body: Review) -> dict[str, Any]:
        owner(request)
        reason = body.reason
        if body.platform_score_id:
            if not body.review or not body.route:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Platform evidence requires an explicit route review",
                    422,
                )
            current_candidate, evidence = await platform_evidence(identity)
            score = require_annotation(
                current_candidate,
                body.revision,
                evidence,
                body.platform_score_id,
                body.review.label,
            )
            from deephelp_app.application.corpus import digest

            reason += (
                f"\nLangfuse annotation {score['id']} sha256={digest(score)} "
                f"candidate r{body.revision}"
            )
            if len(reason) > 1000:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Review reason plus platform evidence exceeds 1000 characters",
                    422,
                )
        if (body.route is None) != (body.review is None):
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Review route and value must agree", 422)
        if body.review and body.review.method != "human":
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Browser review must be explicit human review", 422
            )
        row = await feedback().review(
            identity,
            body.revision,
            body.route,
            body.review.model_copy(
                update={
                    "reviewer": f"{config.owner.tenant_id}/{config.owner.user_id}",
                    "reason": reason,
                }
            )
            if body.review
            else None,
            reviewer=f"{config.owner.tenant_id}/{config.owner.user_id}",
            reason=reason,
        )
        return row.model_dump(mode="json")

    @router.get("/assets/{identity}")
    async def assets(request: Request, identity: Key) -> dict[str, Any]:
        owner(request)
        data = await asyncio.to_thread(verify_files, asset_path(identity), validation_inputs=False)
        current, error = True, None
        try:
            await feedback().require_current(data["source"])
        except ConfigurationError:
            current, error = False, "Reviewed source changed"
        return {
            k: data[k]
            for k in ("version", "status", "source", "corpus_rows", "training_rows", "rule_rows")
        } | dict(
            files_verified=True,
            reviews_current=current,
            error=error,
            published=False,
            note="Candidate only; prepare/publish recheck exact M18 gates",
        )

    @router.get("/plan")
    async def plan(request: Request) -> dict[str, Any]:
        owner(request)
        cases, _ = audit()
        validation = validation_cases()
        return dict(
            offline=dict(
                conversations=len(cases),
                messages=sum(len(c.turns) for c in cases),
                modes=4,
                remote_calls=0,
            ),
            build=dict(
                base_corpus_rows=len(exports([])["corpus"]),
                max_candidates=10,
                embedding_calls_before_retry=[2, 3],
                embedding_input_tokens_estimate=[100, 50000],
                mode="Original DenseImporter + optional approved FastText training; shared budget",
            ),
            validate=dict(
                conversations=len(validation),
                messages=sum(len(c.turns) for c in validation),
                remote_calls_estimate=[1, 1 + 8 * sum(len(c.turns) for c in validation)],
                per_message_tokens_estimate=dict(input=[500, 18000], output=[256, 7000]),
                mode="Original M17/M18 frozen live content; shared budget; no automatic execution",
            ),
            timeout_seconds=1800,
            max_task_attempts=3,
            concurrency=1,
        )

    @router.post("/tasks")
    async def start(request: Request, body: JobInput) -> dict[str, Any]:
        owner(request)
        if body.action == "platform_candidate":
            if config.platform_config is None:
                raise AppError(ErrorCode.NOT_FOUND, "Platform review is not configured", 404)
            if len(body.candidate_ids) != 1 or body.candidate_revision is None:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "Export one exact candidate revision", 422
                )
            selected = await feedback().get(body.candidate_ids[0])
            if selected.revision != body.candidate_revision:
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Candidate revision changed before export", 409
                )
        elif body.candidate_revision is not None:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Only candidate export binds a candidate revision", 422
            )
        if body.action == "platform_upload":
            if body.import_id not in config.imports or config.platform_config is None:
                raise AppError(ErrorCode.NOT_FOUND, "Historical import unavailable", 404)
        elif body.import_id is not None:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Only platform upload consumes an import", 422
            )
        if body.action in {"validate", "prepare"}:
            asset_path(body.asset_id)
        elif body.asset_id is not None:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "This action does not consume an asset", 422)
        if body.action not in {"build", "platform_candidate"} and body.candidate_ids:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Only build consumes reviewed candidates", 422
            )
        if len(set(body.candidate_ids)) != len(body.candidate_ids):
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Duplicate candidate", 422)
        if body.action == "build":
            rows = await feedback().get_many(list(body.candidate_ids)) if body.candidate_ids else []
            minimum = (len(exports(rows)["corpus"]) + 7) // 8
            if factory().remaining_attempts < minimum:
                raise AppError(
                    ErrorCode.BUDGET_EXHAUSTED,
                    "Shared budget cannot cover the candidate embedding batches",
                    503,
                )
        return await jobs.start(body.model_dump(mode="json"))

    @router.get("/tasks/{identity}")
    async def task(request: Request, identity: Key) -> dict[str, Any]:
        owner(request)
        row = jobs.get(identity)
        partial = jobs.output(row) / "evaluation.json"
        return row | dict(
            partial_evidence=await asyncio.to_thread(report_view, partial)
            if partial.is_file()
            else None
        )

    @router.post("/tasks/{identity}/{action}")
    async def control(
        request: Request, identity: Key, action: Literal["cancel", "resume"]
    ) -> dict[str, Any]:
        owner(request)
        if action == "cancel":
            return await jobs.cancel(identity)
        row = jobs.get(identity)
        if row["options"]["action"] in {"build", "validate", "prepare"}:
            # Reconcile completed or uncertain committed derivatives before recovery.
            if row["options"]["action"] == "prepare" or any(
                (jobs.output(row) / name).is_file()
                for name in ("assets/assets.json", "validation.json")
            ):
                raise AppError(
                    ErrorCode.VERSION_CONFLICT,
                    "Existing artifact requires reconciliation; read it first",
                    409,
                )
            for key in row["options"]["candidate_ids"]:
                if any(t["state"] == "DONE" for t in await feedback().tasks(key)):
                    raise AppError(
                        ErrorCode.VERSION_CONFLICT,
                        "Committed derivative requires reconciliation",
                        409,
                    )
        return await jobs.start({}, resume=identity)

    @router.post("/release")
    async def publish(request: Request, body: Publish) -> dict[str, Any]:
        owner(request)
        store = release()
        pointer = await store.active(config.channel)
        if body.action == "rollback" and pointer.previous != body.target:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Previous release changed", 409)
        result = await store.switch(config.channel, body.target, body.revision)
        return result.model_dump(mode="json")

    @router.get("/releases/{identity}")
    async def release_status(request: Request, identity: Hash) -> dict[str, Any]:
        owner(request)
        manifest = await release().manifest(identity, ready=False)
        eligible, error = True, None
        try:
            await release().manifest(identity)
            await asyncio.to_thread(verify_release, manifest)
            await feedback().require_current(manifest.review_snapshot)
        except ConfigurationError as exc:
            eligible, error = False, str(exc)
        return dict(
            hash=identity,
            version=manifest.version,
            eligible=eligible,
            error=error,
            runtime_digest=manifest.code_digest,
            validation_digest=manifest.validation_digest,
            note="Publish/rollback recheck runtime, validation, reviews, assets and revision CAS",
        )

    return router
