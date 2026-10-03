"""M17 frozen business gold plus separately frozen M18 release inputs, using real HTTP."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from deephelp_app.app import create_app
from deephelp_app.corpus import digest
from deephelp_app.dense import atomic_json
from deephelp_app.domain.models import ErrorCode, IntentCode, ResponseEnvelope
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation.core import EvalCase, EvalTurn, audit, file_digest, metrics, score
from deephelp_app.evaluation.mvp_acceptance import api_client
from deephelp_app.flywheel_assets import DEMO_DATA, assembly, verify_files, verify_remote
from deephelp_app.flywheel_assets import read_data as load_json
from deephelp_app.flywheel_store import FeedbackStore
from deephelp_app.milvus_dense import create_client
from deephelp_app.mvp_runtime import BudgetSession, LocalAuth
from deephelp_app.settings import Settings
from deephelp_app.sop_governance import SOPRegistry
from deephelp_app.tool_gateway import process_alive
from deephelp_app.trace import MemoryTrace


async def cleanup_event_collection(collection: str) -> bool:
    if not collection.startswith("dh_m10_events_m18_") or not collection.replace("_", "").isalnum():
        raise ConfigurationError("Only this M18 demonstration's event projection can be removed")
    client = create_client(await asyncio.to_thread(Path.cwd))
    try:
        async with asyncio.timeout(30):
            if await client.has_collection(collection, timeout=None, retry_times=0):
                await client.drop_collection(collection, timeout=None, retry_times=0)
            return not await client.has_collection(collection, timeout=None, retry_times=0)
    finally:
        await client.close()


def validation_cases() -> list[EvalCase]:
    cases = [c for c in audit()[0] if c.live_sample]
    for row in load_json(DEMO_DATA)["release"]:
        turn = EvalTurn(
            turn_id=row["id"] + "-1",
            text=row["text"],
            label=row["label"],
            intent=IntentCode(row["label"]),
            event_id="a",
            entities=row["entities"],
            expectation="query",
            basis="Independent frozen synthetic release semantics + business.json",
        )
        cases.append(
            EvalCase(
                case_id=row["id"],
                source_group=row["id"],
                variant_group=row["id"],
                synthetic=True,
                split="test",
                scenario="m18-release",
                live_sample=True,
                turns=(turn,),
            )
        )
    return cases


async def validate(
    store: FeedbackStore,
    assets: Path,
    output: Path,
    gate: BudgetSession,
    providers: Path,
    auth_path: Path,
    *,
    baseline: Path | None = None,
    registry: SOPRegistry | None = None,
    persist_assets_validation: bool = True,
) -> dict[str, Any]:
    if await asyncio.to_thread(output.exists):
        raise ConfigurationError("Use a new immutable validation report")
    data = verify_files(assets, validation_inputs=True)
    await store.require_current(data["source"])
    await verify_remote(data, gate)
    nonce = uuid4().hex[:16]
    collection = "dh_m10_events_m18_" + nonce
    runtime = assembly(data, providers, collection)
    if registry:
        runtime.sop_registry, runtime.sop_snapshots = registry, (registry,)
    from deephelp_app.asset_integrity import evaluation_provenance as provenance

    auth = LocalAuth(auth_path)
    token, identity = auth.rows[0]
    trace = MemoryTrace()
    app = create_app(
        Settings(mode="mvp", request_timeout=90, child_timeout=30, max_attempts=40, retry_limit=2),
        trace=trace,
        identity_provider=auth,
        conversation_factory=runtime.open,
        budget_factory=gate.request,
    )
    cases = validation_cases()
    code = provenance()
    report: dict[str, Any] = dict(
        format="m18-validation-v1",
        status="PENDING",
        assets_digest=digest(data),
        frozen_m17_digest=audit()[1]["data_digest"],
        release_data_digest=file_digest(DEMO_DATA),
        providers_digest=file_digest(providers),
        judge_providers_digest=file_digest(runtime.judge_providers),
        sop_snapshot=runtime.sop_registry.snapshot_hash,
        code_digest=code["package_digest"],
        code_integrity_scope=code["integrity_scope"],
        tooling_digest=code["tooling_digest"],
        rows=[],
        checks={},
        synthetic=True,
        interpretation="real services and SDK tools; synthetic business only",
        event_collection=collection,
    )
    try:
        async with asyncio.timeout(1800), app.router.lifespan_context(app):
            assert runtime.ledger and runtime.tools
            async with api_client(app, http=True) as client:
                for case in cases:
                    session = "m18-validation-" + nonce + "-" + case.case_id
                    source_ids: dict[str, str] = {}
                    for turn in case.turns:
                        body = dict(
                            channel="m18-validation",
                            session_id=session,
                            message_id=uuid4().hex,
                            raw_text=turn.text,
                            occurred_at=datetime.now(UTC).isoformat(),
                        )
                        source_ids[turn.turn_id] = body["message_id"]
                        started = perf_counter()
                        raw = await client.post(
                            "/converse", json=body, headers={"Authorization": "Bearer " + token}
                        )
                        response = ResponseEnvelope.model_validate(raw.json())
                        if response.error and response.error.code in {
                            ErrorCode.UPSTREAM_UNAVAILABLE,
                            ErrorCode.UNAUTHENTICATED,
                            ErrorCode.TIMEOUT,
                            ErrorCode.BUDGET_EXHAUSTED,
                            ErrorCode.INTERNAL_ERROR,
                        }:
                            report["failed_response"] = response.model_dump(mode="json")
                            report["diagnostics"] = runtime.gateway.diagnostics
                            raise ConfigurationError("Real validation dependency/runtime failed")
                        question = await runtime.ledger.question(
                            identity, session, response.question_id or "none"
                        )
                        records = [
                            r.model_dump(mode="json")
                            for r in await runtime.tools.ledger()
                            if r.call_id in response.tool_call_ids
                        ]
                        observation = dict(
                            entities={e.name.value: e.value for e in question.entities}
                            if question
                            else {},
                            tools=records,
                            retrievals=[],
                            entity_sources={
                                e.name.value: e.source.model_dump(mode="json")
                                for e in question.entities
                            }
                            if question
                            else {},
                            allowed_sources={
                                name: [
                                    source_ids[t.turn_id]
                                    for t in case.turns[: case.turns.index(turn) + 1]
                                    if t.event_id == turn.event_id and value in t.text
                                ]
                                for name, value in turn.entities.items()
                            },
                        )
                        row: dict[str, Any] = dict(
                            case_id=case.case_id,
                            turn_id=turn.turn_id,
                            event_id=turn.event_id,
                            split="release" if case.scenario == "m18-release" else case.split,
                            expectation=turn.expectation,
                            multi_turn=len(case.turns) > 1,
                            text=turn.text,
                            response=response.model_dump(mode="json"),
                            observation=observation,
                            elapsed_ms=(perf_counter() - started) * 1000,
                            http_status=raw.status_code,
                        )
                        row["score"] = score(turn, row["response"], observation)
                        report["rows"].append(row)
                        replay = ResponseEnvelope.model_validate(
                            (
                                await client.post(
                                    "/converse",
                                    json=body,
                                    headers={"Authorization": "Bearer " + token},
                                )
                            ).json()
                        )
                        report["checks"][turn.turn_id + ":replay"] = (
                            replay.replayed
                            and replay.run_id == response.run_id
                            and replay.call_counts.model_calls == replay.call_counts.tool_calls == 0
                        )
                        atomic_json(output, report)
                    print(f"Validated {case.case_id}", flush=True)
                report["metrics"] = metrics(report["rows"], live=True, event_enabled=True)
                reader = await FeedbackStore.open(Path.cwd())
                try:
                    report["checks"]["new_mysql_pool"] = bool(
                        await reader.question(identity, session, response.question_id or "none")
                    )
                finally:
                    await reader.aclose()
                report["diagnostics"] = dict(
                    primary=runtime.gateway.diagnostics, strong=runtime.judge_gateway.diagnostics
                )
            if runtime.event_index:
                index = runtime.event_index
                await index.rpc(
                    lambda: index.client.drop_collection(
                        index.collection, timeout=None, retry_times=0
                    )
                )
                report["checks"]["event_collection_deleted"] = not await index.rpc(
                    lambda: index.client.has_collection(
                        index.collection, timeout=None, retry_times=0
                    )
                )
        report["checks"]["mcp_exited"] = bool(
            runtime.tools and runtime.tools.pid and not process_alive(runtime.tools.pid)
        )
        report["checks"]["complete_coverage"] = len(report["rows"]) == sum(
            len(c.turns) for c in cases
        )
        report["checks"]["all_content_and_safety"] = all(
            r["score"]["completed"] and not r["score"]["hard_failures"] for r in report["rows"]
        )
        report["checks"]["all_sessions"] = report["metrics"]["case_completion_rate"]["value"] == 1
        if baseline:
            old = load_json(baseline)
            if old.get("status") != "PASS" or any(
                old.get(k) != report[k]
                for k in ("frozen_m17_digest", "release_data_digest", "providers_digest")
            ):
                raise ConfigurationError(
                    "Baseline must be successful with the same frozen gold/providers"
                )
            report["checks"]["baseline_coverage"] = {r["turn_id"] for r in old["rows"]} == {
                r["turn_id"] for r in report["rows"]
            }
            report["checks"]["no_regression"] = sum(
                r["score"]["completed"] for r in report["rows"]
            ) >= sum(r["score"]["completed"] for r in old["rows"])
            report["baseline_digest"] = digest(old)
        await store.require_current(data["source"])
        report["status"] = "PASS" if all(report["checks"].values()) else "REJECTED"
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = dict(type=type(exc).__name__)
        raise
    finally:
        if not report["checks"].get("event_collection_deleted"):
            report["checks"]["event_collection_deleted"] = await cleanup_event_collection(
                collection
            )
        atomic_json(output, report)
    if report["status"] == "PASS" and persist_assets_validation:
        atomic_json(assets.parent / "validation.json", report)
    return report
