"""M20 explicit real-content acceptance: independent processes, PostgreSQL records and HTTP MCP."""

import argparse
import asyncio
import json
import secrets
import socket
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.application.dense import load_json
from deephelp_app.bootstrap.runtime.auth import LocalAuth
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models.business import BusinessOrderInput, BusinessRecord
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_tools.assets import asset_path

SUBPROCESS_FLAGS = 0
if sys.platform == "win32":
    SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class Processes:
    def __init__(self, logs: Path) -> None:
        self.logs = logs
        self.running: dict[str, tuple[Any, Any]] = {}
        self.pids: dict[str, list[int]] = {}

    async def start(self, name: str, args: list[str], url: str) -> None:
        stream = (self.logs / (name + ".log")).open("ab")
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            *args,
            stdout=stream,
            stderr=stream,
            creationflags=SUBPROCESS_FLAGS,
        )
        self.running[name] = child, stream
        self.pids.setdefault(name, []).append(child.pid)
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            async with asyncio.timeout(60):
                while True:
                    if child.returncode is not None:
                        raise ConfigurationError(
                            f"{name} exited before startup; inspect retained diagnostics"
                        )
                    try:
                        response = await client.get(url)
                        if response.status_code in {200, 401}:
                            return
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.1)

    async def stop(self, name: str) -> None:
        child, stream = self.running[name]
        if child.returncode is None:
            if sys.platform == "win32":
                # Windows venv redirectors may own a second interpreter: close our exact tree.
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(child.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=SUBPROCESS_FLAGS,
                )
                async with asyncio.timeout(10):
                    await killer.wait()
            else:
                child.terminate()
        try:
            async with asyncio.timeout(10):
                await child.wait()
        finally:
            stream.close()
        del self.running[name]

    async def close(self) -> None:
        for name in list(self.running):
            await self.stop(name)


async def reclaim_owned_lock(path: Path) -> None:
    # Only after all owned application processes have exited; Windows can lag handle release.
    async with asyncio.timeout(5):
        while await asyncio.to_thread(path.exists):
            try:
                await asyncio.to_thread(path.unlink)
            except PermissionError:
                await asyncio.sleep(0.05)


def cleanup_logs(path: Path) -> bool:
    if any(p.is_symlink() or p.is_junction() for p in [path, *path.parents]):
        return False
    for name in ("application.log", "business.log", "retrieval.log", "actual-embedding.json"):
        item = path / name
        if item.is_symlink() or item.is_junction():
            return False
        item.unlink(missing_ok=True)
    path.rmdir()
    return True


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("M20 content acceptance requires explicit --live")
    output = await asyncio.to_thread(Path(args.output).resolve)
    if output.exists():
        raise ConfigurationError("Use a new business acceptance report path")
    controls = [Path(args.auth), Path(args.budget_state), Path(args.service_credentials)]
    if output in [p.resolve() for p in controls]:
        raise ConfigurationError("Report cannot overwrite control files")
    output.parent.mkdir(parents=True, exist_ok=True)
    auth = LocalAuth(Path(args.auth))
    if len(auth.rows) < 2:
        raise ConfigurationError("M20 needs two explicitly mapped test identities")
    token, owner = auth.rows[0]
    other_token = auth.rows[1][0]
    nonce = uuid4().hex[:12]
    order_id, coupon_id, activity_id = (
        "M20-" + nonce + "1",
        "M20-" + nonce + "2",
        "M20-" + nonce + "3",
    )
    event_collection = "dh_m10_events_m20_" + nonce
    report: dict[str, Any] = {
        "status": "RUNNING",
        "checks": {},
        "responses": [],
        "identity": owner.model_dump(),
        "objects": [order_id, coupon_id, activity_id],
        "event_collection": event_collection,
        "errors": [],
    }
    checks = report["checks"]

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name: str, condition: bool) -> None:
        checks[name] = bool(condition)
        save()
        if not condition:
            raise ConfigurationError("M20 acceptance failed: " + name)

    logs = Path(tempfile.mkdtemp(prefix="deephelp-m20-"))
    processes = Processes(logs)
    report["pids"] = processes.pids
    report["diagnostics_directory"] = str(logs)
    service_port, app_port = port(), port()
    service_url, app_url = f"http://127.0.0.1:{service_port}", f"http://127.0.0.1:{app_port}"
    service_args = [
        "-m",
        "deephelp_services.business.api",
        "serve",
        "--credentials",
        args.service_credentials,
        "--port",
        str(service_port),
    ]
    app_args = [
        "-m",
        "deephelp_app",
        "serve",
        "--providers",
        args.providers,
        "--pointer",
        args.pointer,
        "--corpus",
        args.corpus,
        "--auth",
        args.auth,
        "--budget-state",
        args.budget_state,
        "--business-mcp-url",
        service_url + "/mcp",
        "--service-credentials",
        args.service_credentials,
        "--port",
        str(app_port),
        "--event-collection",
        event_collection,
        "--trace-path",
        str(output.with_suffix(".trace.jsonl")),
    ]
    try:
        async with (
            asyncio.timeout(900),
            httpx.AsyncClient(base_url=app_url, timeout=110, trust_env=False) as client,
        ):
            await processes.start("business", service_args, service_url + "/records/orders")
            await processes.start("application", app_args, app_url + "/health")
            headers = {"Authorization": "Bearer " + token}
            other = {"Authorization": "Bearer " + other_token}

            async def maintain(
                method: str,
                path: str,
                body: Any = None,
                *,
                expected: int = 200,
                other_user: bool = False,
            ) -> Any:
                response = await client.request(
                    method,
                    "/business/" + path,
                    headers=other if other_user else headers,
                    **({"json": body} if body is not None else {}),
                )
                if response.status_code != expected:
                    report["errors"].append(
                        {
                            "action": method,
                            "path": path,
                            "status": response.status_code,
                            "response": response.json(),
                        }
                    )
                    save()
                    raise ConfigurationError("M20 maintenance response differs")
                return response.json()

            start = (datetime.now(UTC) - timedelta(days=1)).isoformat()
            end = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            order = {
                "order_id": order_id,
                "paid": {"amount": "173.45"},
                "discount": {"amount": "12.30"},
                "discount_status": "applied",
                "sku_ids": ["SKU-M20-1"],
                "status": "active",
            }
            coupon = {
                "coupon_id": coupon_id,
                "order_id": order_id,
                "minimum_spend": {"amount": "200.00"},
                "status": "threshold_not_met",
                "valid_from": start,
                "valid_until": end,
            }
            activity = {
                "activity_id": activity_id,
                "label": "M20新建周末活动",
                "rules": "个人合成规则：指定订单参与。",
                "valid_from": start,
                "valid_until": end,
            }
            fixture = asset_path("business.json").read_text(encoding="utf-8")
            check(
                "new_objects_absent_from_demo",
                all(value not in fixture for value in [order_id, coupon_id, activity_id]),
            )
            created = await maintain("POST", "orders", order)
            await maintain("POST", "coupons", coupon)
            await maintain("POST", "activities", activity)
            again = await maintain("POST", "orders", order)
            check("create_replay_is_idempotent", again == created)
            changed_order = {**order, "paid": {"amount": "173.46"}}
            await maintain("POST", "orders", changed_order, expected=409)
            await maintain(
                "PUT",
                f"orders/{order_id}/activities",
                {"expected_version": 1, "activity_ids": [activity_id]},
            )
            record = BusinessRecord.model_validate(await maintain("GET", f"orders/{order_id}"))
            assert isinstance(record.data, BusinessOrderInput)
            check(
                "postgresql_record_money_and_relation",
                record.version == 2
                and str(record.data.paid.amount) == "173.45"
                and (await maintain("GET", f"orders/{order_id}/activities"))["activity_ids"]
                == [activity_id],
            )
            check(
                "pagination_and_details",
                any(
                    item["data"]["order_id"] == order_id
                    for item in (await maintain("GET", "orders?offset=0&limit=100"))["items"]
                ),
            )
            # Expected-version CAS: the same version has exactly one winner.
            updates = await asyncio.gather(
                *(
                    client.put(
                        f"/business/orders/{order_id}",
                        headers=headers,
                        json={"expected_version": 2, "data": order},
                    )
                    for _ in range(2)
                )
            )
            check(
                "concurrent_expected_version_one_winner",
                sorted(r.status_code for r in updates) == [200, 409],
            )
            record = BusinessRecord.model_validate(await maintain("GET", f"orders/{order_id}"))

            async def converse(
                label: str, text: str, *, body: Any = None, other_user: bool = False
            ) -> tuple[ResponseEnvelope, Any]:
                message = body or {
                    "channel": "m20-probe",
                    "session_id": "m20-" + nonce + "-" + label,
                    "message_id": nonce + "-" + label,
                    "raw_text": text,
                    "occurred_at": datetime.now(UTC).isoformat(),
                }
                response = await client.post(
                    "/converse", headers=other if other_user else headers, json=message
                )
                data = response.json()
                report["responses"].append(
                    {"label": label, "http_status": response.status_code, "response": data}
                )
                save()
                result = ResponseEnvelope.model_validate(data)
                if response.status_code != 200:
                    raise ConfigurationError("M20 business content request failed")
                return result, message

            discount, original = await converse(
                "discount", f"订单 {order_id} 结算时原本应享受的折扣，实际扣款和减免分别是多少？"
            )

            def facts(result: ResponseEnvelope) -> dict[str, Any]:
                return {
                    f.name: f.value.model_dump(mode="json")
                    if hasattr(f.value, "model_dump")
                    else f.value
                    for f in result.facts
                }

            check(
                "new_order_real_retrieval_and_mcp",
                sum(s.retrieval_calls for s in discount.stages) > 0
                and len(discount.tool_call_ids) > 0
                and facts(discount).get("paid", {}).get("amount") == "173.45"
                and facts(discount).get("discount", {}).get("amount") == "12.30",
            )
            snapshot = await maintain("GET", "evidence/" + discount.evidence_refs[0].evidence_id)
            check(
                "evidence_snapshot_is_versioned_and_owned",
                snapshot["snapshot"]["records"][0]["version"] == record.version
                and snapshot["snapshot"]["records"][0]["identity"] == owner.model_dump(),
            )
            coupon_reply, _ = await converse(
                "coupon", f"订单 {order_id} 的优惠券 {coupon_id} 不能用，帮我查状态和门槛。"
            )
            check(
                "coupon_status_and_exact_threshold",
                facts(coupon_reply).get("coupon_status") == "threshold_not_met"
                and facts(coupon_reply).get("minimum_spend", {}).get("amount") == "200.00",
            )
            activity_reply, _ = await converse(
                "activity", f"查询订单 {order_id} 参加的活动有哪些。"
            )
            check(
                "activity_committed_fact",
                activity_id in facts(activity_reply).get("activity_ids", "")
                and "M20新建周末活动" in facts(activity_reply).get("activity_labels", ""),
            )

            new_order = {**order, "paid": {"amount": "188.88"}, "discount": {"amount": "15.55"}}
            await maintain(
                "PUT", f"orders/{order_id}", {"expected_version": record.version, "data": new_order}
            )
            await maintain(
                "PUT",
                f"orders/{order_id}",
                {"expected_version": record.version, "data": order},
                expected=409,
            )
            old_budget = load_json(Path(args.budget_state))
            replay, _ = await converse("replay", "", body=original)
            check(
                "old_message_old_receipt_zero_calls",
                replay.replayed
                and replay.run_id == discount.run_id
                and replay.facts == discount.facts
                and replay.evidence_refs == discount.evidence_refs
                and replay.budget_used.attempts == 0
                and load_json(Path(args.budget_state)) == old_budget,
            )
            updated, _ = await converse(
                "updated", f"订单 {order_id} 没优惠，核对当前实付和优惠金额。"
            )
            check(
                "new_message_reads_updated_committed_money",
                facts(updated).get("paid", {}).get("amount") == "188.88"
                and facts(updated).get("discount", {}).get("amount") == "15.55",
            )
            check(
                "old_evidence_immutable_after_update",
                await maintain("GET", "evidence/" + discount.evidence_refs[0].evidence_id)
                == snapshot,
            )

            for kind, identifier in [
                ("orders", order_id),
                ("coupons", coupon_id),
                ("activities", activity_id),
            ]:
                await maintain("GET", f"{kind}/{identifier}", expected=404, other_user=True)
            for kind, identifier, data in [
                ("coupons", coupon_id, coupon),
                ("activities", activity_id, activity),
            ]:
                await maintain(
                    "PUT",
                    f"{kind}/{identifier}",
                    {"expected_version": 1, "data": data},
                    expected=404,
                    other_user=True,
                )
            await maintain(
                "POST",
                "coupons",
                {**coupon, "coupon_id": "M20-OTHER-" + nonce},
                expected=404,
                other_user=True,
            )
            await maintain(
                "PUT",
                f"orders/{order_id}",
                {"expected_version": 4, "data": new_order},
                expected=404,
                other_user=True,
            )
            await maintain(
                "PUT",
                f"orders/{order_id}/activities",
                {"expected_version": 4, "activity_ids": [activity_id]},
                expected=404,
                other_user=True,
            )
            await maintain(
                "GET",
                "evidence/" + discount.evidence_refs[0].evidence_id,
                expected=404,
                other_user=True,
            )
            denied = await client.get(
                f"/debug/runs/{discount.run_id}/flow",
                params={"session_id": original["session_id"]},
                headers=other,
            )
            check(
                "other_identity_records_relations_evidence_and_session_denied",
                denied.status_code in {403, 404},
            )

            # Independent Business restart; a new SDK initialization reconnects the running app.
            await processes.stop("business")
            unavailable = await client.get(f"/business/orders/{order_id}", headers=headers)
            check("business_down_fails_closed", unavailable.status_code == 503)
            await processes.start("business", service_args, service_url + "/records/orders")
            after_service, _ = await converse(
                "service-restart", f"查询订单 {order_id} 参加的活动有哪些。"
            )
            check(
                "independent_business_restart_recovers",
                facts(after_service).get("activity_ids") == activity_id
                and processes.pids["business"][0] != processes.pids["business"][1],
            )
            await processes.stop("application")
            # Forced process exit leaves the budget lock; only reclaim our confirmed-exited owner.
            lock = Path(args.budget_state).with_suffix(".lock")
            await reclaim_owned_lock(lock)
            await processes.start("application", app_args, app_url + "/health")
            stored = await maintain("GET", f"orders/{order_id}")
            check(
                "independent_application_restart_preserves_records",
                stored["data"]["paid"]["amount"] == "188.88"
                and processes.pids["application"][0] != processes.pids["application"][1],
            )
            before = load_json(Path(args.budget_state))
            final_replay, _ = await converse("restart-replay", "", body=original)
            check(
                "restart_replay_zero_calls_and_original_evidence",
                final_replay.replayed
                and final_replay.facts == discount.facts
                and final_replay.evidence_refs == discount.evidence_refs
                and load_json(Path(args.budget_state)) == before,
            )
            final_query, _ = await converse(
                "app-restart", f"订单 {order_id} 没优惠，核对当前实付和优惠金额。"
            )
            check(
                "restarted_application_new_message_reads_current_fact",
                facts(final_query).get("paid", {}).get("amount") == "188.88",
            )
        report["status"] = "PASS"
    except AppError as exc:
        report["status"] = "FAIL"
        report["errors"].append(
            {
                "code": exc.code,
                "provider_code": exc.provider_code,
                "request_id": exc.provider_request_id,
            }
        )
    except ConfigurationError as exc:
        report["status"] = "FAIL"
        report["errors"].append({"diagnostic": str(exc)})
    except Exception as exc:
        report["status"] = "FAIL"
        report["errors"].append({"type": type(exc).__name__})
    finally:
        try:
            await processes.close()
            await reclaim_owned_lock(Path(args.budget_state).with_suffix(".lock"))
            if report["status"] == "PASS" and cleanup_logs(logs):
                report["diagnostics_directory"] = None
        except Exception as exc:
            report["status"] = "FAIL"
            report["errors"].append({"cleanup_error": type(exc).__name__})
        finally:
            report["processes_exited"] = not processes.running
            state = load_json(Path(args.budget_state))
            report["cumulative_budget"] = {
                key: state[key]
                for key in (
                    "attempts",
                    "tokens",
                    "charged_tokens",
                    "cost_upper_cny",
                    "uncertain_attempts",
                )
            }
            save()
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": checks,
                "errors": report["errors"],
                "report": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1


async def resume_restart(args: argparse.Namespace) -> int:
    """Recheck restart actions, reusing receipts and the original cumulative budget."""
    from deephelp_app.adapters.mcp.business.tool_gateway import ToolGateway
    from deephelp_app.adapters.mcp.shared.network_mcp import HTTPMCPConfig, NetworkMCPClient
    from deephelp_app.adapters.persistence.ledger import PostgresLedger
    from deephelp_app.domain.execution import ExecutionBudget
    from deephelp_app.domain.models.conversation import ConverseInput, RequestEnvelope
    from deephelp_tools.probes.protocol.mcp_smoke import synthetic_request

    source = Path(args.resume_report)
    output = Path(args.output)
    if await asyncio.to_thread(output.exists) or not args.live:
        raise ConfigurationError("Resume requires --live and a new report path")
    report: Any = load_json(source)
    if not report["checks"].get("independent_business_restart_recovers"):
        raise ConfigurationError(
            "Resume requires prior business/content acceptance through service restart"
        )
    report.update(status="RUNNING", resume_source=str(source), errors=[])
    auth = LocalAuth(Path(args.auth))
    token, owner = auth.rows[0]
    first = next(row["response"] for row in report["responses"] if row["label"] == "discount")
    old = ResponseEnvelope.model_validate(first)
    ledger = await PostgresLedger.open(Path.cwd())
    try:
        async with ledger.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m08_messages WHERE tenant_id=%s AND user_id=%s AND run_id=%s",
                (owner.tenant_id, owner.user_id, old.run_id),
            )
            row = await cursor.fetchone()
            original = RequestEnvelope.model_validate_json(row[0])
            await conn.rollback()
    finally:
        await ledger.aclose()
    message = ConverseInput.model_validate(
        original.model_dump(
            mode="json",
            include={
                "channel",
                "session_id",
                "message_id",
                "raw_text",
                "occurred_at",
                "question_hint",
                "expected_question_version",
            },
        )
    ).model_dump(mode="json")
    order_id = report["objects"][0]
    logs = Path(tempfile.mkdtemp(prefix="deephelp-m20-"))
    processes = Processes(logs)
    report["recovery_pids"] = processes.pids
    report["recovery_diagnostics"] = str(logs)
    service_port, app_port = port(), port()
    service_url, app_url = f"http://127.0.0.1:{service_port}", f"http://127.0.0.1:{app_port}"
    service_args = [
        "-m",
        "deephelp_services.business.api",
        "serve",
        "--credentials",
        args.service_credentials,
        "--port",
        str(service_port),
    ]
    app_args = [
        "-m",
        "deephelp_app",
        "serve",
        "--providers",
        args.providers,
        "--pointer",
        args.pointer,
        "--corpus",
        args.corpus,
        "--auth",
        args.auth,
        "--budget-state",
        args.budget_state,
        "--business-mcp-url",
        service_url + "/mcp",
        "--service-credentials",
        args.service_credentials,
        "--port",
        str(app_port),
        "--event-collection",
        report["event_collection"],
        "--trace-path",
        str(output.with_suffix(".trace.jsonl")),
    ]

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name: str, condition: bool) -> None:
        report["checks"][name] = bool(condition)
        save()
        if not condition:
            raise ConfigurationError("M20 recovery failed: " + name)

    try:
        await reclaim_owned_lock(Path(args.budget_state).with_suffix(".lock"))
        await processes.start("business", service_args, service_url + "/records/orders")
        await processes.start("application", app_args, app_url + "/health")
        await processes.stop("business")
        await processes.start("business", service_args, service_url + "/records/orders")
        protocol_budget = ExecutionBudget.start(20, 4, 0)
        async with ToolGateway(
            http=HTTPMCPConfig(
                url=service_url + "/mcp",
                credentials_path=Path(args.service_credentials),
            )
        ).open() as gateway:
            observations = []
            for coupon in (None, report["objects"][1]):
                req, question, context = synthetic_request(
                    protocol_budget,
                    order_id=order_id,
                    coupon_id=coupon,
                    user=owner.user_id,
                    tenant=owner.tenant_id,
                )
                tool_result = await gateway.execute(req, question, protocol_budget, context=context)
                observations.append(tool_result.model_dump(mode="json"))
                check(
                    "initialize_both_tools_content_and_schema",
                    tool_result.status.value == "SUCCEEDED"
                    and tool_result.evidence_refs[0].version == "business-postgres-v1",
                )
            network = gateway._connected()
            assert isinstance(network, NetworkMCPClient)
            report["protocol"] = {
                "handshakes": network.handshakes,
                "fixed_tool_attempts": protocol_budget.attempts_used,
                "observations": observations,
            }
            check(
                "actual_initialize_2025_11_25",
                all(
                    h["initialized"] and h["protocol_version"] == "2025-11-25"
                    for h in report["protocol"]["handshakes"]
                ),
            )
            check("independent_business_restart_recovers", len(processes.pids["business"]) == 2)
        async with httpx.AsyncClient(
            base_url=app_url,
            timeout=110,
            trust_env=False,
            headers={"Authorization": "Bearer " + token},
        ) as client:
            for iteration in range(2):
                stored = await client.get(f"/business/orders/{order_id}")
                check(
                    "independent_application_restart_preserves_records",
                    stored.status_code == 200
                    and stored.json()["data"]["paid"]["amount"] == "188.88",
                )
                before = load_json(Path(args.budget_state))
                replay = await client.post("/converse", json=message)
                result = ResponseEnvelope.model_validate(replay.json())
                check(
                    "restart_replay_zero_calls_and_original_evidence",
                    result.replayed
                    and result.run_id == old.run_id
                    and result.facts == old.facts
                    and result.evidence_refs == old.evidence_refs
                    and result.budget_used.attempts == 0
                    and load_json(Path(args.budget_state)) == before,
                )
                if iteration == 0:
                    await processes.stop("application")
                    await reclaim_owned_lock(Path(args.budget_state).with_suffix(".lock"))
                    await processes.start("application", app_args, app_url + "/health")
            fresh = {
                **message,
                "session_id": "m20-recovery-" + uuid4().hex,
                "message_id": uuid4().hex,
                "raw_text": f"订单 {order_id} 结算时原本应享受的折扣，实际扣款和减免分别是多少？",
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            response = await client.post("/converse", json=fresh)
            result = ResponseEnvelope.model_validate(response.json())
            report["responses"].append(
                {
                    "label": "app-restart-recovery",
                    "http_status": response.status_code,
                    "response": result.model_dump(mode="json"),
                }
            )
            check(
                "restarted_application_new_message_reads_current_fact",
                any(
                    f.name == "paid" and getattr(f.value, "amount", None) == Decimal("188.88")
                    for f in result.facts
                ),
            )
            check(
                "initialize_main_real_retrieval_and_mcp",
                sum(stage.retrieval_calls for stage in result.stages) > 0
                and bool(result.tool_call_ids),
            )
            check(
                "two_actual_application_processes",
                processes.pids["application"][0] != processes.pids["application"][1],
            )
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["errors"].append(
            {
                "type": type(exc).__name__,
                "diagnostic": str(exc) if isinstance(exc, ConfigurationError) else None,
            }
        )
    finally:
        try:
            await processes.close()
            await reclaim_owned_lock(Path(args.budget_state).with_suffix(".lock"))
            if report["status"] == "PASS" and cleanup_logs(logs):
                report["recovery_diagnostics"] = None
        except Exception as exc:
            report["status"] = "FAIL"
            report["errors"].append({"cleanup_error": type(exc).__name__})
        state = load_json(Path(args.budget_state))
        report["cumulative_budget"] = {
            key: state[key]
            for key in (
                "attempts",
                "tokens",
                "charged_tokens",
                "cost_upper_cny",
                "uncertain_attempts",
            )
        }
        report["processes_exited"] = not processes.running
        save()
    print(
        json.dumps(
            {"status": report["status"], "checks": report["checks"], "errors": report["errors"]},
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1


def initialize(args: argparse.Namespace) -> int:
    from deephelp_app.bootstrap.commands.mvp_cli import initialize as app_initialize

    app_initialize(args)
    auth: Any = load_json(Path(args.auth))
    auth["tokens"][0]["identity"] = {"tenant_id": "personal-m20", "user_id": "user-a"}
    auth["tokens"].append(
        {
            "token": secrets.token_urlsafe(32),
            "identity": {"tenant_id": "personal-m20", "user_id": "user-b"},
        }
    )
    Path(args.auth).write_text(json.dumps(auth), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicit M20 PostgreSQL/independent HTTP MCP acceptance"
    )
    parser.add_argument("command", choices=["init", "run"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--auth", default=".local/m20/auth.json")
    parser.add_argument("--budget-state", default=".local/m20/budget.json")
    parser.add_argument("--service-credentials", default=".local/m20/service.json")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--corpus", default="demo/data/m09_corpus.jsonl")
    parser.add_argument("--output", default=".local/m20/business-final.json")
    parser.add_argument(
        "--resume-report", help="Prior successful content report needing restart-only recovery"
    )
    parser.add_argument("--max-calls", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=500000)
    parser.add_argument("--max-cost", default="30")
    args = parser.parse_args()
    return (
        initialize(args)
        if args.command == "init"
        else asyncio.run(resume_restart(args) if args.resume_report else run(args))
    )


if __name__ == "__main__":
    raise SystemExit(main())
