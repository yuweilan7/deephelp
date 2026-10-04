"""Explicit live M10 acceptance with dedicated identities, keys and event collection."""

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.adapters.cases import MySQLCaseRepository
from deephelp_app.adapters.ledger import Receipt
from deephelp_app.adapters.milvus_dense import create_client
from deephelp_app.adapters.milvus_memory import MilvusEventIndex
from deephelp_app.adapters.providers import ProviderConfig, create_gateway
from deephelp_app.application.memory import MemoryService, ProjectionWorker, RedisMemory
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.mvp_runtime import (
    BudgetSession,
    LocalAuth,
    live_app,
    validate_control_paths,
)
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import AsyncCalls
from deephelp_app.domain.models import (
    LifecycleCommand,
    NextAction,
    Outcome,
    Question,
    QuestionStatus,
    RequestEnvelope,
    ResponseEnvelope,
    RunStatus,
    VerifiedIdentity,
)
from deephelp_tools.demo.assembly import DemoAssembly as LiveAssembly
from deephelp_tools.evaluation.mvp_acceptance import api_client


def envelope(identity: VerifiedIdentity, session: str, **changes: Any) -> RequestEnvelope:
    now = datetime.now(UTC)
    return RequestEnvelope(
        identity=identity,
        session_id=session,
        channel="m10-probe",
        message_id=uuid4().hex,
        raw_text="专用合成恢复样本",
        occurred_at=now,
        received_at=now,
        request_id=uuid4().hex,
        trace_id=uuid4().hex,
    ).model_copy(update=changes)


async def terminal(
    repo: MySQLCaseRepository, receipt: Receipt, request: RequestEnvelope
) -> Question:
    q = receipt.question.model_copy(
        update={"version": receipt.question.version + 1, "status": QuestionStatus.ACTIVE}
    )
    response = ResponseEnvelope(
        request_id=request.request_id,
        trace_id=request.trace_id,
        run_id=receipt.run_id,
        question_id=q.question_id,
        question_status=q.status,
        run_status=RunStatus.FAILED,
        outcome=Outcome.ERROR,
        next_action=NextAction.CONTACT_SUPPORT,
        reply="专用存储验证未调用业务工具",
    )
    await repo.finish(receipt, q, response)
    return q


def command(q: Question, target: QuestionStatus, source: str) -> LifecycleCommand:
    return LifecycleCommand.model_validate(
        dict(
            session_id=q.session_id,
            expected_version=q.version,
            target=target,
            reason="专用合成生命周期证据",
            evidence_source=source,
            evidence_ref="m10-synthetic-evidence",
        )
    )


async def restarted(path: Path) -> dict[str, Any]:
    state = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    identity = VerifiedIdentity.model_validate(state["identity"])
    async with AsyncExitStack() as stack:
        repo = await MySQLCaseRepository.open(Path.cwd())
        stack.push_async_callback(repo.aclose)
        cache = RedisMemory.open(Path.cwd(), namespace=state["namespace"])
        stack.push_async_callback(cache.aclose)
        window = await repo.snapshot(identity, state["session"])
        q = await repo.question(identity, state["session"], state["question_id"])
        cached = await cache.client.get(cache.key(identity, state["session"], window.generation))
        return {
            "status": "PASS"
            if q
            and q.version == state["version"]
            and cached
            and window.generation == state["generation"]
            else "FAIL",
            "history_count": len(window.history),
            "generation": window.generation,
            "version": q.version if q else None,
        }


async def run(args: argparse.Namespace) -> int:
    output = local_path(args.output)
    validate_control_paths(
        [
            local_path(args.auth),
            local_path(args.budget_state),
            local_path(args.pointer),
            Path(args.providers),
        ],
        output=output,
    )
    if output.exists():
        raise ConfigurationError("Use a new M10 report path")
    gate = BudgetSession(local_path(args.budget_state))
    gate.open()
    nonce = uuid4().hex
    session = "m10-" + nonce
    auth = LocalAuth(local_path(args.auth))
    identity = auth.rows[0][1]
    namespace = "deephelp:m10:probe:" + nonce
    report: dict[str, Any] = {
        "status": "PENDING",
        "stage": args.stage,
        "session": session,
        "checks": {},
    }
    checks = report["checks"]

    def checkpoint(phase: str) -> None:
        report["completed_phase"] = phase
        output.with_suffix(".checkpoint.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"phase": phase}), flush=True)

    config = ProviderConfig.load(Path(args.providers))
    collection = "dh_m10_events_probe_" + nonce[:16]
    report["collection"] = collection
    try:
        async with asyncio.timeout(900), AsyncExitStack() as stack:
            repo = await MySQLCaseRepository.open(Path.cwd())
            stack.push_async_callback(repo.aclose)
            await repo.migrate()
            cache = RedisMemory.open(Path.cwd(), namespace=namespace)
            stack.push_async_callback(cache.aclose)
            assert await cache.client.ping()
            client = await stack.enter_async_context(httpx.AsyncClient(trust_env=False, timeout=30))
            gateway = create_gateway(client, config, AsyncCalls(2, 30))
            milvus = create_client(Path.cwd())
            stack.push_async_callback(milvus.close)
            index = MilvusEventIndex(milvus, gateway, config.signature(), collection)
            await index.initialize()
            memory = MemoryService(repo, cache)
            worker = ProjectionWorker(repo, memory, index)

            # The same public HTTP pipeline uses real models, MySQL and native MCP protocol.
            assembly = LiveAssembly(Path.cwd(), Path(args.providers), local_path(args.pointer))
            app = live_app(assembly, auth, gate, str(output.with_suffix(".trace.jsonl")))
            async with app.router.lifespan_context(app), api_client(app, http=True) as api:
                headers = {"Authorization": "Bearer " + auth.rows[0][0]}

                async def send(
                    text: str,
                    hint: str | None = None,
                    version: int | None = None,
                    body: dict[str, Any] | None = None,
                ) -> tuple[httpx.Response, dict[str, Any]]:
                    data = body or dict(
                        channel="m10-live",
                        session_id=session,
                        message_id=uuid4().hex,
                        raw_text=text,
                        occurred_at=datetime.now(UTC).isoformat(),
                        question_hint=hint,
                        expected_question_version=version,
                    )
                    response = await api.post("/converse", json=data, headers=headers)
                    return response, response.json()

                _, a = await send("我的订单未享受优惠，请查一下")
                _, b = await send("订单 000042 的券不能用")
                checks["two_open_questions"] = a.get("question_status") == b.get(
                    "question_status"
                ) == "WAITING_SLOT" and a.get("question_id") != b.get("question_id")
                missing = await api.get("/memory", params={"session_id": session}, headers=headers)
                checks["http_bounded_memory"] = (
                    missing.status_code == 200 and len(missing.json()["active_questions"]) == 2
                )
                _, conflict = await send("订单 000053", b["question_id"], 2)
                checks["conflict_blocks_tools"] = conflict.get(
                    "question_status"
                ) == "WAITING_SLOT" and not conflict.get("tool_call_ids")
                _, correction = await send("更正：订单 000031", b["question_id"], 4)
                bq = await repo.question(identity, session, b["question_id"])
                checks["correction_retains_provenance"] = bool(
                    bq
                    and bq.entities[0].value == "000031"
                    and bq.conflicts[-1].resolution == "explicit_correction"
                    and bq.conflicts[-1].previous.source.message_id in bq.member_message_ids
                )
                assert bq is not None
                _, same_correction = await send("更正：订单 000031", b["question_id"], bq.version)
                checks["same_value_correction_keeps_audit"] = (
                    same_correction.get("question_status") == "WAITING_SLOT"
                    and same_correction.get("error") is None
                )
                complete_body = dict(
                    channel="m10-live",
                    session_id=session,
                    message_id=uuid4().hex,
                    raw_text="订单 000031",
                    occurred_at=datetime.now(UTC).isoformat(),
                    question_hint=a["question_id"],
                    expected_question_version=2,
                )
                response, completed = await send("", body=complete_body)
                checks["real_slot_continuation_facts"] = (
                    response.status_code == 200
                    and completed.get("question_status") == "RESOLVED"
                    and "99.90" in completed.get("reply", "")
                    and "10.00" in completed.get("reply", "")
                    and bool(completed.get("tool_call_ids"))
                )
                _, replay = await send("", body=complete_body)
                checks["duplicate_no_model_or_tools"] = (
                    replay.get("replayed") is True
                    and replay.get("budget_used", {}).get("attempts") == 0
                    and replay.get("run_id") == completed.get("run_id")
                )
                closed, _ = await send("订单000031", a["question_id"])
                checks["closed_hint_rejected"] = closed.status_code == 409
                _, fresh = await send("我的订单未享受优惠，请查一下")
                checks["similar_message_creates_new_question"] = fresh.get("question_id") not in {
                    a["question_id"],
                    b["question_id"],
                }
                report["http_results"] = {
                    "a": a,
                    "b": b,
                    "conflict": conflict,
                    "correction": correction,
                    "completed": completed,
                    "fresh": fresh,
                }
            checkpoint("http")

            # Dedicated raw ledger operations verify short transactions and storage invariants.
            r = envelope(identity, session)
            receipts = await asyncio.gather(*(repo.accept(r) for _ in range(8)))
            receipt = next(row for row in receipts if row.acquired)
            checks["concurrent_message_one_receipt"] = (
                sum(row.acquired for row in receipts) == 1
                and len({row.run_id for row in receipts}) == 1
            )
            q = await terminal(repo, receipt, r)
            other_identity = identity.model_copy(update={"user_id": "m10-other-" + nonce[:12]})
            other_request = envelope(other_identity, session, raw_text="订单 000031")
            other = await repo.accept(other_request)
            await terminal(repo, other, other_request)
            checks["same_order_other_user_isolated"] = (
                await repo.question(other_identity, session, q.question_id) is None
                and other.question.question_id != q.question_id
            )
            checks["scope_window_isolated"] = all(
                row.question_id != other.question.question_id
                for row in (await repo.snapshot(identity, session)).history
            )
            stale = envelope(
                identity,
                session,
                question_hint=q.question_id,
                occurred_at=r.occurred_at - timedelta(seconds=1),
            )
            try:
                await repo.accept(stale)
                checks["out_of_order_rejected"] = False
            except AppError as exc:
                checks["out_of_order_rejected"] = exc.code == "VERSION_CONFLICT"
            races = await asyncio.gather(
                *(
                    repo.transition(
                        identity,
                        q.question_id,
                        command(q, QuestionStatus.WAITING_SLOT, "slot_check"),
                    )
                    for _ in range(6)
                ),
                return_exceptions=True,
            )
            checks["cas_one_winner"] = sum(isinstance(row, Question) for row in races) == 1
            q = next(row for row in races if isinstance(row, Question))
            pending = envelope(
                identity, session, question_hint=q.question_id, expected_question_version=q.version
            )
            owner = await repo.accept(pending)
            try:
                await repo.transition(
                    identity,
                    q.question_id,
                    command(owner.question, QuestionStatus.CANCELLED, "operator_cancel"),
                )
                checks["running_not_resumed"] = False
            except AppError as exc:
                checks["running_not_resumed"] = exc.code == "VERSION_CONFLICT"
            q = await terminal(repo, owner, pending)
            checkpoint("mysql_concurrency")

            # A deliberately real refused Redis connection cannot lose the committed outbox.
            async with repo.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT event_id FROM dh_m10_outbox WHERE question_id=%s ORDER BY version",
                    (q.question_id,),
                )
                events = [int(row[0]) for row in await cursor.fetchall()]
                await conn.rollback()
            failed_cache = RedisMemory.open(Path.cwd(), namespace=namespace)
            stack.push_async_callback(failed_cache.aclose)
            failed_cache.client = type(cache.client)(
                host="127.0.0.1",
                port=1,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            failing = ProjectionWorker(repo, MemoryService(repo, failed_cache), index)
            try:
                await failing.once(gate.request(), event_id=events[-1])
                checks["projection_failure_recorded"] = False
            except Exception:
                async with repo.pool.acquire() as conn, conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT done,attempts,last_error FROM dh_m10_outbox WHERE event_id=%s",
                        (events[-1],),
                    )
                    row = await cursor.fetchone()
                    await conn.rollback()
                checks["projection_failure_recorded"] = not row[0] and row[1] == 1 and bool(row[2])
            checks["retry_latest_event"] = (
                await worker.once(gate.request(), event_id=events[-1]) == "projected"
            )
            old_window = await repo.snapshot(identity, session)
            for eid in reversed(events[:-1]):
                assert await worker.once(gate.request(), event_id=eid) == "projected"
            checks["duplicate_and_reverse_outbox_safe"] = (
                await worker.once(gate.request(), event_id=events[-1]) == "idle"
                and (await repo.snapshot(identity, session)).generation == old_window.generation
            )
            checkpoint("outbox")

            # Vector ACTIVE payload is actually present, but current MySQL state rejects it.
            current = await repo.question(identity, session, q.question_id)
            assert current is not None
            q = current
            await index.put(q, gate.request())
            closed_q = await repo.transition(
                identity, q.question_id, command(q, QuestionStatus.RESOLVED, "user_confirmation")
            )
            related = MemoryService(repo, cache, index)
            raw_hits = await index.search(identity, session, "专用合成恢复样本", gate.request())
            filtered = await related.load(
                identity, session, gate.request(), query="专用合成恢复样本"
            )
            checks["real_stale_active_hit"] = any(
                hit.question_id == q.question_id and hit.status == QuestionStatus.ACTIVE
                for hit in raw_hits
            )
            checks["stale_active_rechecked_in_mysql"] = all(
                hit.question_id != q.question_id for hit in filtered.closed_summaries
            )
            await index.put(closed_q, gate.request())
            verified = await related.load(
                identity, session, gate.request(), query="专用合成恢复样本"
            )
            checks["closed_summary_content"] = any(
                hit.question_id == q.question_id
                and hit.version == closed_q.version
                and hit.status == QuestionStatus.RESOLVED
                and hit.message_ids == closed_q.member_message_ids
                for hit in verified.closed_summaries
            )
            reopened = await repo.transition(
                identity, q.question_id, command(closed_q, QuestionStatus.ACTIVE, "explicit_reopen")
            )
            checks["explicit_reopen_audited"] = (
                reopened.version == closed_q.version + 1
                and reopened.member_message_ids == closed_q.member_message_ids
            )
            # A projection suspended in remote I/O must not hold a business row lock.
            async with repo.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT MAX(event_id) FROM dh_m10_outbox WHERE question_id=%s",
                    (reopened.question_id,),
                )
                slow_event = int((await cursor.fetchone())[0])
                await conn.rollback()
            entered, released = asyncio.Event(), asyncio.Event()

            class SlowIndex:
                async def put(self, question: Question, budget: Any) -> None:
                    entered.set()
                    await released.wait()
                    await index.put(question, budget)

                async def search(self, *values: Any) -> Any:
                    return await index.search(*values)

            slow = ProjectionWorker(repo, memory, SlowIndex())
            pending_projection = asyncio.create_task(slow.once(gate.request(), event_id=slow_event))
            try:
                async with asyncio.timeout(5):
                    await entered.wait()
                    newer = await repo.transition(
                        identity,
                        reopened.question_id,
                        command(reopened, QuestionStatus.WAITING_SLOT, "slot_check"),
                    )
                checks["remote_projection_does_not_lock_facts"] = (
                    newer.version == reopened.version + 1
                )
            finally:
                released.set()
            checks["older_inflight_projection_safe"] = await pending_projection == "projected"
            reopened = newer
            # Expired worker token is fenced out after another claimant takes the lease.
            async with repo.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT MAX(event_id) FROM dh_m10_outbox WHERE question_id=%s",
                    (reopened.question_id,),
                )
                lease_event = int((await cursor.fetchone())[0])
                await conn.rollback()
            first_claim = await repo.claim_event(event_id=lease_event)
            assert first_claim
            async with repo.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE dh_m10_outbox SET lease_until=DATE_SUB(CURRENT_TIMESTAMP(6),"
                    "INTERVAL 1 SECOND) WHERE event_id=%s AND lease_token=%s",
                    (lease_event, first_claim[1]),
                )
                await conn.commit()
            second_claim = await repo.claim_event(event_id=lease_event)
            assert second_claim
            checks["expired_lease_fenced"] = (
                not await repo.acknowledge(lease_event, first_claim[1])
                and second_claim[1] != first_claim[1]
            )
            await index.put(second_claim[2], gate.request())
            await memory.load(identity, session, gate.request())
            checks["new_claim_recovers_projection"] = await repo.acknowledge(
                lease_event, second_claim[1]
            )
            checkpoint("vector_authority_and_leases")

            # Text byte, count and age bounds; all originals remain in the fact store.
            history_session = session + "-history"
            for _ in range(35 if args.stage == "feature" else 4):
                hr = envelope(identity, history_session, raw_text="合成历史" * 120)
                rr = await repo.accept(hr)
                await terminal(repo, rr, hr)
            old = envelope(
                identity, history_session, occurred_at=datetime.now(UTC) - timedelta(days=2)
            )
            rr = await repo.accept(old)
            await terminal(repo, rr, old)
            window = await repo.snapshot(
                identity, history_session, count=3, token_limit=1700, case_limit=2
            )
            checks["bounded_history_preserves_refs"] = (
                window.trimmed
                and len(window.history) <= 3
                and sum(len(row.text.encode()) for row in window.history) <= 1700
                and all(
                    row.message_id and row.question_id and row.message_id != old.message_id
                    for row in window.history
                )
            )

            recovery = await memory.load(identity, session, gate.request())
            sentinel = f"deephelp:m10:probe:sentinel:{nonce}"
            await cache.client.set(sentinel, "keep", ex=300)
            keys = [key async for key in cache.client.scan_iter(match=namespace + ":*")]
            if keys:
                await cache.client.delete(*keys)
            checks["scoped_cache_loss"] = (
                await cache.client.get(cache.key(identity, session, recovery.generation)) is None
                and await cache.client.get(sentinel) == "keep"
            )
            restored = await memory.load(identity, session, gate.request())
            checks["cache_rebuilt_from_facts"] = (
                restored == recovery
                and json.loads(
                    await cache.client.get(cache.key(identity, session, restored.generation))
                )["generation"]
                == restored.generation
            )
            keywords = json.loads(
                await cache.client.get(
                    cache.key(identity, session, restored.generation) + ":keywords"
                )
            )
            checks["ttl_keywords_and_active_content"] = 0 < await cache.client.ttl(
                cache.key(identity, session, restored.generation)
            ) <= cache.ttl and "order_id=000031" in keywords.get(b["question_id"], [])
            await index.rpc(lambda: milvus.drop_collection(collection, timeout=None, retry_times=0))
            await index.initialize()
            latest = await repo.latest_questions(identity, session, limit=100)
            for record in latest:
                await index.put(record, gate.request())
            rebuilt_vectors = await related.load(
                identity, session, gate.request(), query="订单优惠问题"
            )
            checks["milvus_deleted_and_rebuilt_from_facts"] = any(
                row.question_id == a["question_id"]
                and row.status == QuestionStatus.RESOLVED
                and row.version == 4
                for row in rebuilt_vectors.closed_summaries
            )
            state_path = output.with_suffix(".restart-state.json")
            state_path.write_text(
                json.dumps(
                    {
                        "identity": identity.model_dump(),
                        "session": session,
                        "namespace": namespace,
                        "question_id": reopened.question_id,
                        "version": reopened.version,
                        "generation": restored.generation,
                    }
                ),
                encoding="utf-8",
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "deephelp_tools.probes.memory_probe",
                "--restart-state",
                str(state_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(30):
                    stdout, stderr = await process.communicate()
                restart = json.loads(stdout)
                checks["new_process_mysql_and_redis"] = (
                    process.returncode == 0 and restart["status"] == "PASS"
                )
                report["restart"] = restart
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()
            async with repo.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) FROM dh_m10_outbox WHERE question_id=%s "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(reason,'$.evidence_source'))='explicit_reopen'",
                    (reopened.question_id,),
                )
                checks["reopen_evidence_in_outbox"] = (await cursor.fetchone())[0] == 1
                await conn.rollback()
            # Retain synthetic MySQL facts/events for replay; remove this probe's projections.
            await index.rpc(lambda: milvus.drop_collection(collection, timeout=None, retry_times=0))
            keys = [key async for key in cache.client.scan_iter(match=namespace + ":*")]
            if keys:
                await cache.client.delete(*keys)
            await cache.client.delete(sentinel)
            checks["scoped_projection_cleanup"] = not await index.rpc(
                lambda: milvus.has_collection(collection, timeout=None, retry_times=0)
            )
            report["embedding_signature"] = config.signature().fingerprint
            report["budget_state"] = gate.state
            report["status"] = "PASS" if all(checks.values()) else "FAIL"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = {
            "type": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "message": str(exc)
            if isinstance(exc, (AppError, ConfigurationError))
            else "Private diagnostic saved",
        }
        output.with_suffix(".error.txt").write_text(repr(exc), encoding="utf-8")
    finally:
        gate.close()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": checks}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="M10 dedicated real content acceptance")
    parser.add_argument("--restart-state")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=["feature", "main"], default="feature")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--auth", default=".local/m08/auth.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--budget-state", default=".local/m10/session-budget.json")
    parser.add_argument("--output", default=".local/m10/feature.json")
    args = parser.parse_args()
    if args.restart_state:
        result = asyncio.run(restarted(Path(args.restart_state)))
        print(json.dumps(result))
        return 0 if result["status"] == "PASS" else 1
    if not args.live:
        parser.error("Explicit --live required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
