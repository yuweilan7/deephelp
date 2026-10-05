"""Real fixed-action repository acceptance for M25; no remote model or cutover."""

import argparse
import asyncio
import json
import os
import platform
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx

from deephelp_app.adapters.approval_store import ApprovalRepository
from deephelp_app.adapters.history import PostgresHistory
from deephelp_app.adapters.postgres import PostgresConfig
from deephelp_app.adapters.release_assets import release_hash
from deephelp_app.adapters.release_store import ReleaseStore
from deephelp_app.adapters.synthetic_rights import RightsClient, binding_hash, signature
from deephelp_app.application.approval import ApprovalService
from deephelp_app.application.corpus import digest
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.feedback import RouteReview
from deephelp_app.domain.models.approval import ApprovalCommand
from deephelp_app.domain.models.business import BusinessOrderInput
from deephelp_app.domain.models.common import (
    ErrorCode,
    Money,
    NextAction,
    Outcome,
    QuestionStatus,
    RunStatus,
    VerifiedIdentity,
)
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.release import ReleaseManifest
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_services.business_store import BusinessRepository
from deephelp_tools.assets import asset_path
from deephelp_tools.demo.approval_prepare import prepare
from deephelp_tools.demo.scenarios import proposal_registry
from deephelp_tools.demo.synthetic_rights import create_rights_app
from deephelp_tools.evaluation.flywheel_store import FeedbackStore
from deephelp_tools.evaluation.legacy_checkpoints import LegacyCheckpointArchive
from deephelp_tools.evaluation.postgres_migration import canonical, read_snapshot, untyped, verify
from deephelp_tools.learning.event_replay import envelope
from deephelp_tools.probes.postgres_probe import require_isolated


async def rejected(operation: Any, code: ErrorCode | None = None) -> None:
    try:
        await operation
    except AppError as exc:
        if code is not None:
            assert exc.code == code
    except ConfigurationError:
        assert code is None
    else:
        raise AssertionError("Expected guarded rejection")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config = PostgresConfig.read(args.config)
    require_isolated(config)
    os.environ["DEEPHELP_POSTGRES_CONFIG"] = str(args.config.resolve())
    header, raw_rows, summary, snapshot_hash = read_snapshot(args.snapshot)
    prefix = "m25-" + uuid4().hex
    checks: dict[str, Any] = {}
    repo = await ApprovalRepository.open(args.root)
    owned_runs: list[str] = []
    owned_questions: list[str] = []
    operations: list[str] = []
    candidate_ids: list[str] = []
    protocol_releases: list[str] = []
    releases = ReleaseStore(repo.pool)
    business = BusinessRepository(repo)
    business_owner = VerifiedIdentity(tenant_id=prefix, user_id="u")

    async def query(sql: str, params: tuple[Any, ...] = (), *, write: bool = False) -> Any:
        async with repo.pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            result = await cur.fetchall() if cur.description else []
            if write:
                await conn.commit()
            return result

    async def close_request(req: RequestEnvelope, *, concurrency: bool = False) -> Any:
        receipts = await asyncio.gather(*(repo.accept(req) for _ in range(8 if concurrency else 1)))
        assert sum(r.acquired for r in receipts) == 1 and len({r.run_id for r in receipts}) == 1
        owner = next(r for r in receipts if r.acquired)
        owned_runs.append(owner.run_id)
        owned_questions.append(owner.question.question_id)
        q = owner.question.model_copy(
            update={
                "version": owner.question.version + 1,
                "status": QuestionStatus.HANDED_OFF,
                "updated_at": datetime.now(UTC),
            }
        )
        response = ResponseEnvelope(
            request_id=req.request_id,
            trace_id=req.trace_id,
            run_id=owner.run_id,
            run_status=RunStatus.SUCCEEDED,
            question_id=q.question_id,
            question_status=q.status,
            outcome=Outcome.HANDOFF,
            reply="固定持久协议动作",
            next_action=NextAction.CONTACT_SUPPORT,
        )
        if concurrency:
            finished = await asyncio.gather(
                *(repo.finish(owner, q, response) for _ in range(8)), return_exceptions=True
            )
            assert sum(value is None for value in finished) == 1
            assert all(
                value is None
                or isinstance(value, AppError)
                and value.code == ErrorCode.VERSION_CONFLICT
                for value in finished
            )
        else:
            await repo.finish(owner, q, response)
        replay = await repo.accept(req)
        assert not replay.acquired and replay.response == response and replay.run_id == owner.run_id
        return owner, q

    try:
        # Original receipts read through the actual repository; no model/tool assembly is opened.
        messages = {
            "columns": next(
                t["columns"] for t in header["tables"] if t["name"] == "dh_m08_messages"
            )
        }
        body_index = next(i for i, c in enumerate(messages["columns"]) if c[0] == "body")
        req = RequestEnvelope.model_validate_json(
            untyped(raw_rows["dh_m08_messages"][0][body_index])
        )
        before = await repo.lookup(req)
        replay = await repo.accept(req)
        assert before == replay and not replay.acquired
        page = await PostgresHistory(repo.pool).messages(req.identity, req.session_id, 0, 100)
        assert any(item.run_id == replay.run_id for item in page.items)
        await rejected(
            PostgresHistory(repo.pool).messages(
                VerifiedIdentity(tenant_id=prefix, user_id="other"), req.session_id, 0, 10
            ),
            ErrorCode.NOT_FOUND,
        )
        checks["original_receipt_history_replay"] = {
            "status": "PASS",
            "new_model_calls": 0,
            "new_tool_calls": 0,
            "same_run_and_response": True,
        }

        # Read every migrated business entity with amounts/identity/version from the original
        # snapshot.
        for kind in ("orders", "coupons", "activities"):
            table = next(t for t in header["tables"] if t["name"] == "dh_m20_" + kind)
            for row in raw_rows[table["name"]]:
                data = dict(
                    zip((c[0] for c in table["columns"]), (untyped(v) for v in row), strict=True)
                )
                identity = VerifiedIdentity(tenant_id=data["tenant_id"], user_id=data["user_id"])
                record = await business.get(kind, identity, data["record_id"])
                assert record.version == data["version"] and record.data.model_dump(
                    mode="json"
                ) == json.loads(data["body"])
        checks["all_business_amounts_coupons_activities"] = "PASS"

        # Native Windows Proactor subprocess and the database pool coexist in the same entry loop.
        child = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "print('m25-child')", stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await child.communicate()
        assert child.returncode == 0 and stdout.strip() == b"m25-child"
        assert await query("SELECT 1") == [(1,)]
        checks["subprocess_and_pool_loop"] = "PASS"

        fixture = json.loads(asset_path("m18_demo.json").read_text(encoding="utf-8"))["collect"][0]
        req = envelope(fixture["text"], prefix, channel="m18-collect")
        owner, q = await close_request(req, concurrency=True)
        await rejected(
            repo.accept(req.model_copy(update={"raw_text": "冲突输入"})),
            ErrorCode.IDEMPOTENCY_CONFLICT,
        )
        assert await repo.question(business_owner, req.session_id, q.question_id) is None
        window = await repo.snapshot(req.identity, req.session_id, count=10, age_seconds=3600)
        assert any(item.message_id == req.message_id for item in window.history)
        checks["eight_accept_finish_cas_scope_session"] = "PASS"

        event_id = (
            await query(
                "SELECT event_id FROM dh_m10_outbox WHERE question_id=%s AND version=%s",
                (q.question_id, q.version),
            )
        )[0][0]
        first = await repo.claim_event(event_id=event_id)
        assert first and await repo.acknowledge(
            event_id, first[1], error="fixed_projection_failure"
        )
        second = await repo.claim_event(event_id=event_id)
        assert second and second[1] != first[1]
        assert not await repo.acknowledge(event_id, first[1])
        assert await repo.acknowledge(event_id, second[1])
        assert await repo.claim_event(event_id=event_id) is None
        checks["outbox_failure_repair_stale_token"] = "PASS"

        feedback = FeedbackStore(repo.pool)
        candidate = await feedback.capture(
            owner.run_id, b"m25-fixed-private-key", fixture["id"], fixture["id"]
        )
        candidate_ids.append(candidate.candidate_id)
        review = RouteReview(
            state="approved",
            label=IntentCode(fixture["label"]),
            reviewer="synthetic-semantic-evidence-v1",
            method="deterministic_evidence",
            reason="固定协议资格",
            independent_from_heldout=True,
        )
        reviewed = await feedback.review(candidate.candidate_id, 0, "fasttext", review)
        await rejected(feedback.review(candidate.candidate_id, 0, "fasttext", review))
        token, jobs = await feedback.claim([reviewed])
        assert len(jobs) == 1
        await rejected(feedback.claim([reviewed]))
        await rejected(feedback.complete("stale", jobs, []))
        await feedback.complete(token, jobs, [])
        checks["feedback_review_cas_lease"] = "PASS"

        order = BusinessOrderInput(
            order_id="000031",
            paid=Money(amount=Decimal("173.45")),
            discount=Money(amount=Decimal("12.30")),
            discount_status="applied",
            sku_ids=("SKU-M25",),
        )
        assert (await business.put("orders", business_owner, order)).version == 1
        updated = order.model_copy(
            update={"paid": order.paid.model_copy(update={"amount": order.paid.amount + 1})}
        )
        changed = await asyncio.gather(
            *(business.put("orders", business_owner, updated, 1) for _ in range(8)),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, BaseException) for value in changed) == 1
        assert (await business.get("orders", business_owner, "000031")).data == updated
        checks["business_decimal_leading_zero_version_cas"] = "PASS"

        # Historical manifests retain bytes; the new public gate rejects the old runtime.
        rows = await query(
            "SELECT release_hash,body FROM dh_m18_releases "
            "WHERE state='READY' ORDER BY release_hash"
        )
        assert rows
        await rejected(releases.switch(prefix, rows[0][0], 0))
        assert (await releases.active(prefix)).revision == 0
        # Explicit protocol fixtures never claim full validation or activate a real channel.
        manifests = []
        for suffix in ("a", "b"):
            body = json.loads(rows[0][1])
            body.update(
                version=prefix + "-" + suffix,
                review_snapshot={"candidates": {}, "digest": digest({})},
                validation_path="m25-protocol-only",
                validation_digest="m25-protocol-only",
            )
            manifest = ReleaseManifest.model_validate(body)
            release_identity = release_hash(manifest)
            await query(
                "INSERT INTO dh_m18_releases(release_hash,version,body) VALUES (%s,%s,%s)",
                (release_identity, manifest.version, manifest.model_dump_json()),
                write=True,
            )
            protocol_releases.append(release_identity)
            manifests.append(manifest)
        a, b = protocol_releases
        manifest_a, manifest_b = manifests
        switched = await asyncio.gather(
            *(releases._commit_switch(prefix, a, 0, manifest_a) for _ in range(8)),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, BaseException) for value in switched) == 1
        pointer = await releases._commit_switch(prefix, b, 1, manifest_b)
        assert pointer.active == b and pointer.previous == a and pointer.revision == 2

        def fault(stage: str) -> None:
            raise RuntimeError("fixed_switch_rollback")

        try:
            await releases._commit_switch(prefix, a, 2, manifest_a, fault=fault)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected switch rollback")
        assert await releases.active(prefix) == pointer
        checks["release_transaction_cas_rollback_previous"] = {
            "status": "PASS",
            "scope": "database commit only; old complete release rejected by public gate",
        }

        # Real approval repository and real downstream handler, without compiling/resuming a graph.
        op = await prepare(repo, prefix + "-approval")
        operations.append(op.plan.operation_id)
        owned_runs.append(op.run_id)
        owned_questions.append(op.plan.question_id)
        command = ApprovalCommand(
            decision="approve",
            session_id=op.session_id,
            run_id=op.run_id,
            expected_question_version=op.question_version,
            parameters_hash=op.plan.parameters_hash,
            sop_version=op.plan.sop_version,
            snapshot_hash=op.plan.snapshot_hash,
        )
        op = await repo.decide(
            op.plan.identity, op.plan.operation_id, command, proposal_registry().snapshot_hash
        )
        claims = await asyncio.gather(
            *(repo.acquire(op.plan.operation_id, op.plan.snapshot_hash) for _ in range(8)),
            return_exceptions=True,
        )
        accepted_claims = [value for value in claims if not isinstance(value, BaseException)]
        assert len(accepted_claims) == 1 and accepted_claims[0][1]
        claim_token = accepted_claims[0][1]
        op = await repo.dispatch(op.plan.operation_id, claim_token, op.plan.snapshot_hash)
        key = b"x" * 32
        app = create_rights_app(args.root, key)
        app.state.repo = repo
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://m25"
        ) as client:

            def body(kind: str) -> dict[str, str]:
                digest = binding_hash(op)
                return {
                    "operation_id": op.plan.operation_id,
                    "binding_hash": digest,
                    "signature": signature(key, kind, op.plan.operation_id, digest),
                }

            effects = await asyncio.gather(
                *(client.post("/execute", json=body("execute")) for _ in range(8))
            )
            assert all(value.status_code == 200 for value in effects)
            ids = {value.json()["effect"]["call_id"] for value in effects}
            assert len(ids) == 1
            response = await client.post("/query", json=body("query"))
            assert response.json()["status"] == "SUCCEEDED"
        await repo.unknown(op.plan.operation_id, claim_token, "fixed_query_recovery")
        assert (await repo.get(op.plan.operation_id)).status == "UNKNOWN"
        try:
            ApprovalService(repo, cast(RightsClient, None), op.plan.snapshot_hash)
        except AppError as exc:
            assert exc.code == ErrorCode.NOT_IMPLEMENTED
        else:
            raise AssertionError("Missing explicit official saver was accepted")
        archive = LegacyCheckpointArchive(repo.pool)
        await rejected(archive.adelete_thread("unused"), ErrorCode.NOT_IMPLEMENTED)
        thread = (
            await query(
                "SELECT thread_id FROM m25_legacy.dh_m15_checkpoints ORDER BY thread_id LIMIT 1"
            )
        )[0][0]
        assert await archive.aget_tuple({"configurable": {"thread_id": thread}})
        checks["approval_claim_effect_unknown_explicit_saver"] = {
            "status": "PASS",
            "effects": 1,
            "graph_executions": 0,
            "legacy_read": "PASS",
            "explicit_saver_required": True,
        }
    except Exception as exc:
        args.output.write_bytes(
            canonical(
                {
                    "status": "FAIL",
                    "error": type(exc).__name__,
                    "owned_prefix": prefix,
                    "completed_checks": checks,
                    "snapshot_sha256": snapshot_hash,
                }
            )
            + b"\n"
        )
        raise
    finally:
        # Exact IDs from this run only. Interrupted failure keeps the source snapshot and reports.
        pending = await query(
            "SELECT r.run_id,q.question_id FROM dh_m08_runs r "
            "JOIN dh_m08_questions q USING(question_id) "
            "WHERE q.session_id IN (%s,%s)",
            (prefix, prefix + "-approval"),
        )
        owned_runs = list(set(owned_runs) | {row[0] for row in pending})
        owned_questions = list(set(owned_questions) | {row[1] for row in pending})
        for run_id in owned_runs:
            pending_operations = await query(
                "SELECT operation_id FROM dh_m15_operations WHERE run_id=%s", (run_id,)
            )
            operations = list(set(operations) | {row[0] for row in pending_operations})
        for candidate_id in candidate_ids:
            for table in (
                "dh_m18_derivatives",
                "dh_m18_tasks",
                "dh_m18_reviews",
                "dh_m18_candidates",
            ):
                await query(
                    f"DELETE FROM {table} WHERE candidate_id=%s", (candidate_id,), write=True
                )
        for operation in operations:
            for table in (
                "dh_m15_synthetic_effects",
                "dh_m15_synthetic_calls",
                "dh_m15_audit",
                "dh_m15_operations",
            ):
                await query(f"DELETE FROM {table} WHERE operation_id=%s", (operation,), write=True)
        for question in owned_questions:
            for table in ("dh_m10_outbox", "dh_m10_members", "dh_m08_questions"):
                await query(f"DELETE FROM {table} WHERE question_id=%s", (question,), write=True)
        for run_id in owned_runs:
            for table in ("dh_m18_run_releases", "dh_m08_messages", "dh_m08_runs"):
                await query(f"DELETE FROM {table} WHERE run_id=%s", (run_id,), write=True)
        await query(
            "DELETE FROM dh_m10_sessions WHERE session_id IN (%s,%s)",
            (prefix, prefix + "-approval"),
            write=True,
        )
        for table in ("dh_m18_release_events", "dh_m18_active"):
            await query(f"DELETE FROM {table} WHERE channel=%s", (prefix,), write=True)
        for release_id in protocol_releases:
            await query(
                "DELETE FROM dh_m18_releases WHERE release_hash=%s", (release_id,), write=True
            )
        await query(
            "DELETE FROM dh_m20_orders WHERE tenant_id=%s AND user_id=%s", (prefix, "u"), write=True
        )
        await repo.aclose()
    # A new pool proves persisted reads after connection/process ownership is released.
    fresh = await ApprovalRepository.open(args.root)
    try:
        verified = await verify(fresh.pool, header, summary)
    finally:
        await fresh.aclose()
    return {
        "status": "PASS",
        "platform": platform.system(),
        "python": platform.python_version(),
        "checks": checks,
        "cleanup": verified["original_bytes"],
        "snapshot_sha256": snapshot_hash,
        "model_calls": 0,
        "model_tokens": 0,
        "cutover": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 1800 or args.output.exists():
        parser.error("Finite timeout and a new report are required")

    async def bounded() -> dict[str, Any]:
        async with asyncio.timeout(args.timeout):
            return await run(args)

    result = asyncio.run(bounded())
    args.output.write_bytes(canonical(result) + b"\n")


if __name__ == "__main__":
    main()
