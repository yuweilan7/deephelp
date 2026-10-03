"""Explicit real-service synthetic demonstration, including review conflict and rollback."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.app import create_app
from deephelp_app.dense import atomic_json
from deephelp_app.domain.models import ErrorCode, IntentCode, ResponseEnvelope
from deephelp_app.errors import ConfigurationError
from deephelp_app.flywheel import ROUTES, RouteReview
from deephelp_app.flywheel_assets import DEMO_DATA, activate, build, read_data, verify_remote
from deephelp_app.flywheel_store import FeedbackStore
from deephelp_app.flywheel_validation import cleanup_event_collection, validate
from deephelp_app.live_probe import local_path
from deephelp_app.mvp_acceptance import api_client
from deephelp_app.mvp_runtime import BudgetSession, LiveAssembly, LocalAuth
from deephelp_app.settings import Settings
from deephelp_app.tool_gateway import process_alive


async def collect(
    store: FeedbackStore, gate: BudgetSession, args: argparse.Namespace, nonce: str
) -> tuple[list[str], dict[str, Any]]:
    auth = LocalAuth(local_path(args.auth))
    token = auth.rows[0][0]
    key = await asyncio.to_thread(local_path(args.redaction_key).read_bytes)
    runtime = LiveAssembly(
        Path.cwd(),
        Path(args.providers),
        local_path(args.dense_pointer),
        fasttext_pointer=local_path(args.fasttext_pointer),
        event_collection="dh_m10_events_m18_collect_" + nonce,
    )
    app = create_app(
        Settings(mode="mvp", request_timeout=90, child_timeout=30, max_attempts=40, retry_limit=2),
        identity_provider=auth,
        conversation_factory=runtime.open,
        budget_factory=gate.request,
    )
    result: dict[str, Any] = dict(candidates=[], checks={})
    ids = []
    async with app.router.lifespan_context(app):
        async with api_client(app, http=True) as client:
            for sample in read_data(DEMO_DATA)["collect"]:
                body = dict(
                    channel="m18-collect",
                    session_id="m18-source-" + uuid4().hex,
                    message_id=uuid4().hex,
                    raw_text=sample["text"],
                    occurred_at=datetime.now(UTC).isoformat(),
                )
                raw = await client.post(
                    "/converse", json=body, headers={"Authorization": "Bearer " + token}
                )
                response = ResponseEnvelope.model_validate(raw.json())
                result.setdefault("source_responses", []).append(response.model_dump(mode="json"))
                atomic_json(local_path(args.output) / "capture.json", result)
                if (
                    not response.run_id
                    or response.error
                    and response.error.code
                    in {
                        ErrorCode.UPSTREAM_UNAVAILABLE,
                        ErrorCode.UNAUTHENTICATED,
                        ErrorCode.TIMEOUT,
                        ErrorCode.BUDGET_EXHAUSTED,
                        ErrorCode.INTERNAL_ERROR,
                        ErrorCode.MODEL_OUTPUT_INVALID,
                    }
                ):
                    raise ConfigurationError("Real source run failed; inspect durable run")
                candidate = await store.capture(response.run_id, key, sample["id"], sample["id"])
                duplicate = await store.capture(response.run_id, key, sample["id"], sample["id"])
                result["checks"][sample["id"] + ":duplicate"] = candidate == duplicate
                result["checks"][sample["id"] + ":candidate_only"] = not candidate.reviews
                if sample["label"]:
                    # Source mistakes are feedback, never evidence of its gold label.
                    for route in ROUTES:
                        review = RouteReview(
                            state="approved",
                            label=IntentCode(sample["label"]),
                            reviewer="synthetic-semantic-evidence-v1",
                            method="deterministic_evidence",
                            reason="Registered source semantics; heldout groups audited",
                            independent_from_heldout=True,
                            phrases=tuple(sample["phrases"]) if route == "rule" else (),
                        )
                        candidate = await store.review(
                            candidate.candidate_id, candidate.revision, route, review
                        )
                    ids.append(candidate.candidate_id)
                else:
                    review = RouteReview(
                        state="rejected",
                        reviewer="synthetic-rejection-audit",
                        method="deterministic_evidence",
                        reason="Logistics is outside the three supported intents",
                    )
                    # Two real MySQL writers use one observed revision; exactly one may commit.
                    attempts = await asyncio.gather(
                        store.review(candidate.candidate_id, candidate.revision, "corpus", review),
                        store.review(candidate.candidate_id, candidate.revision, "corpus", review),
                        return_exceptions=True,
                    )
                    result["checks"]["concurrent_review_cas"] = (
                        sum(not isinstance(v, BaseException) for v in attempts) == 1
                        and sum(isinstance(v, ConfigurationError) for v in attempts) == 1
                    )
                    candidate = await store.get(candidate.candidate_id)
                    result["checks"]["rejected_no_task"] = not await store.tasks(
                        candidate.candidate_id
                    )
                result["candidates"].append(candidate.model_dump(mode="json"))
                print("Captured and audited " + sample["id"], flush=True)
        result["diagnostics"] = runtime.gateway.diagnostics
        assert runtime.event_index
        index = runtime.event_index
        await index.rpc(
            lambda: index.client.drop_collection(index.collection, timeout=None, retry_times=0)
        )
        result["checks"]["collection_deleted"] = not await index.rpc(
            lambda: index.client.has_collection(index.collection, timeout=None, retry_times=0)
        )
    result["checks"]["mcp_exited"] = bool(
        runtime.tools and runtime.tools.pid and not process_alive(runtime.tools.pid)
    )
    return ids, result


async def probe(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("Synthetic demo uses real dependencies and requires --live")
    output = local_path(args.output)
    if await asyncio.to_thread(output.exists):
        raise ConfigurationError("Retain earlier evidence; use a new output directory")
    await asyncio.to_thread(output.mkdir, parents=True)
    gate = BudgetSession(local_path(args.budget_state))
    gate.open()
    store = await FeedbackStore.open(Path.cwd())
    report: dict[str, Any] = dict(status="PENDING", stage=args.stage, synthetic=True, checks={})
    try:
        async with asyncio.timeout(1800):
            await store.migrate()
            nonce = uuid4().hex[:16]
            report["capture_collection"] = "dh_m10_events_m18_collect_" + nonce
            atomic_json(output / "report.json", report)
            ids, capture = await collect(store, gate, args, nonce)
            report["capture"] = capture
            report["checks"]["capture"] = all(capture["checks"].values())
            atomic_json(output / "report.json", report)
            versions = []
            for name, selected in (("baseline", []), ("reviewed", ids)):
                print("Building " + name, flush=True)
                folder = output / name
                data = await build(
                    store,
                    selected,
                    folder,
                    "m18-" + nonce + "-" + name,
                    gate,
                    Path(args.providers),
                    local_path(args.fasttext_pointer),
                )
                report[name + "_counts"] = {
                    k: data[k] for k in ("corpus_rows", "training_rows", "rule_rows")
                }
                versions.append(folder / "assets.json")
            report["checks"]["three_routes"] = report["reviewed_counts"] == dict(
                corpus_rows=15, training_rows=83, rule_rows=3
            )
            jobs = [j for cid in ids for j in await store.tasks(cid) if j["revision"] == 3]
            report["tasks"] = jobs
            report["checks"]["unique_completed_tasks"] = len(jobs) == 9 and all(
                j["state"] == "DONE" and j["attempts"] == 1 for j in jobs
            )
            old = await validate(
                store,
                versions[0],
                output / "baseline-validation.json",
                gate,
                Path(args.providers),
                local_path(args.auth),
            )
            report["checks"]["baseline_validation"] = old["status"] == "PASS"
            new = await validate(
                store,
                versions[1],
                output / "reviewed-validation.json",
                gate,
                Path(args.providers),
                local_path(args.auth),
                baseline=output / "baseline-validation.json",
            )
            report["checks"]["reviewed_validation"] = new["status"] == "PASS"
            if args.baseline:
                previous = read_data(local_path(args.baseline))
                report["checks"]["main_same_inputs"] = all(
                    previous[k] == new[k]
                    for k in ("frozen_m17_digest", "release_data_digest", "providers_digest")
                )
                report["checks"]["main_no_regression"] = (
                    previous["status"] == "PASS"
                    and previous["metrics"]["completion_rate"] == new["metrics"]["completion_rate"]
                    and previous["metrics"]["case_completion_rate"]
                    == new["metrics"]["case_completion_rate"]
                )
            if not all(report["checks"].values()):
                raise ConfigurationError(
                    "Demo content gate rejected; active pointer was not changed"
                )
            pointer = local_path(args.pointer)
            await activate(store, versions[0], pointer, gate)
            await activate(store, versions[1], pointer, gate)
            active = await asyncio.to_thread(pointer.read_bytes)
            await activate(store, versions[1], pointer, gate)
            report["checks"]["activation_idempotent"] = active == await asyncio.to_thread(
                pointer.read_bytes
            )
            report["checks"]["previous_complete"] = read_data(pointer)["previous"] == str(
                versions[0].resolve()
            )
            await activate(store, versions[0], pointer, gate)
            await verify_remote(read_data(versions[0]), gate)
            report["checks"]["rollback_readback"] = read_data(pointer)["active"] == str(
                versions[0].resolve()
            )
            # Correct then withdraw the source; retain all three derivative references.
            first = await store.get(ids[0])
            changed = await store.review(
                first.candidate_id,
                first.revision,
                "corpus",
                first.reviews["corpus"].model_copy(
                    update={"reason": "Correction audit; derivative must be rebuilt"}
                ),
            )
            await store.review(
                changed.candidate_id,
                changed.revision,
                None,
                None,
                reviewer="synthetic-withdrawal-audit",
                reason="Rollback demonstration source withdrawn",
            )
            report["lineage"] = await store.lineage(first.candidate_id)
            report["checks"]["correction_lineage"] = {r["route"] for r in report["lineage"]} == set(
                ROUTES
            )
            stable = await asyncio.to_thread(pointer.read_bytes)
            try:
                await activate(store, versions[1], pointer, gate)
            except ConfigurationError:
                report["checks"]["stale_source_blocked"] = stable == await asyncio.to_thread(
                    pointer.read_bytes
                )
            else:
                report["checks"]["stale_source_blocked"] = False
            report["checks"]["withdrawn_absent"] = all(
                r.state == "withdrawn"
                for r in (await store.get(first.candidate_id)).reviews.values()
            )
            report["status"] = "PASS" if all(report["checks"].values()) else "FAILED"
            report["active"] = read_data(pointer)
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = dict(type=type(exc).__name__, message=str(exc))
        raise
    finally:
        report["cumulative_usage"] = gate.state
        try:
            if "capture_collection" in report:
                report["checks"]["capture_cleanup"] = await cleanup_event_collection(
                    report["capture_collection"]
                )
        finally:
            atomic_json(output / "report.json", report)
            gate.close()
            await store.aclose()
    print(json.dumps({k: report[k] for k in ("status", "stage", "checks")}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=("feature", "main"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--pointer", default=".local/m18/active.json")
    parser.add_argument("--dense-pointer", default=".local/m09/active.json")
    parser.add_argument("--fasttext-pointer", default=".local/m13/active.json")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--auth", default=".local/m17/auth.json")
    parser.add_argument("--budget-state", default=".local/m18/session-budget.json")
    parser.add_argument("--redaction-key", default=".local/m18/redaction.key")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
