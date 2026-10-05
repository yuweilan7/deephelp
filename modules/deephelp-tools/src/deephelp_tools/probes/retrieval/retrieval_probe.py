"""显式同向量检索协议对照、独立进程恢复和跨两个MCP内容验收。

ActualObservedEmbedding共享一次实际向量，parity比较同候选/排序/来源；不是分别调用后凑相同。
进程与专用数据由本次probe回收，断连未知usage仍保留原预算，失败不转本地demo。
"""

import argparse
import asyncio
import hashlib
import json
import math
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.adapters.mcp.business.business_client import BusinessClient
from deephelp_app.adapters.mcp.business.mcp_protocol import canonical
from deephelp_app.adapters.mcp.retrieval.retrieval_mcp import RemoteRetriever
from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import binding_from_files
from deephelp_app.adapters.mcp.shared.network_mcp import HTTPMCPConfig
from deephelp_app.adapters.model_gateway.providers import ProviderConfig
from deephelp_app.adapters.retrieval.milvus_dense import create_client
from deephelp_app.adapters.retrieval.milvus_hybrid import MilvusHybridStore
from deephelp_app.application.dense import load_json
from deephelp_app.application.hybrid import HybridRetriever
from deephelp_app.bootstrap.runtime.auth import LocalAuth
from deephelp_app.bootstrap.runtime.budget import BudgetSession
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import request_execution
from deephelp_app.domain.models.business import BusinessOrderInput, BusinessRecord
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.model_io import EmbeddingResult
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_tools.probes.protocol.business_probe import (
    Processes,
    cleanup_logs,
    initialize,
    port,
    reclaim_owned_lock,
)


def observed_service(path: str, argv: list[str]) -> int:
    """Probe-only observer: call the original gateway, retain its exact real vector locally."""
    from deephelp_app.adapters.model_gateway.gateway import QianwenGateway
    from deephelp_services.retrieval.api import main as service_main

    original = QianwenGateway.embed

    async def observed(self: Any, texts: list[str], budget: Any) -> EmbeddingResult:
        result = await original(self, texts, budget)
        await asyncio.to_thread(
            Path(path).write_text,
            json.dumps({"texts": texts, "result": result.model_dump(mode="json")}),
            encoding="utf-8",
        )
        return result

    QianwenGateway.embed = observed  # type: ignore[method-assign]  # Isolated probe process only.
    import sys

    sys.argv = ["deephelp_services.retrieval.api", *argv]
    return service_main()


class ActualObservedEmbedding:
    """Same real observation for the local algorithm; this issues no second provider request."""

    def __init__(self, path: Path, text: str) -> None:
        captured = load_json(path)
        if captured["texts"] != [text]:
            raise ConfigurationError("Observed embedding belongs to another query")
        self.result = EmbeddingResult.model_validate(captured["result"])

    async def embed(self, texts: list[str], budget: Any) -> EmbeddingResult:
        return self.result


def parity(local: Any, remote: Any) -> float:
    maximum = 0.0
    assert set(local) == set(remote), "retrieval modes differ"
    for mode, expected in local.items():
        observed = remote[mode]
        assert expected.scope == observed.scope, mode + ": scope differs"
        assert expected.policy_version == observed.policy_version, mode + ": policy differs"
        assert expected.retrieval_mode == observed.retrieval_mode, mode + ": mode differs"
        assert expected.dense_weight == observed.dense_weight, mode + ": weight differs"
        assert expected.candidate_budget == observed.candidate_budget, mode + ": K differs"
        for field in ("hits", "candidates"):
            before, after = getattr(expected, field), getattr(observed, field)
            assert len(before) == len(after), mode + ": " + field + " count differs"
            for a, b in zip(before, after, strict=True):
                left, right = a.model_dump(mode="json"), b.model_dump(mode="json")
                for score in ("raw_score", "raw_dense", "raw_sparse", "fusion_score"):
                    x, y = left.pop(score, None), right.pop(score, None)
                    assert (x is None) == (y is None), mode + ": " + score + " presence differs"
                    if x is not None:
                        assert math.isclose(x, y, abs_tol=2e-5), mode + ": " + score + " differs"
                        maximum = max(maximum, abs(x - y))
                assert left == right, mode + ": " + field + " metadata/order differs"
    return maximum


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("Retrieval content acceptance requires --live")
    output = await asyncio.to_thread(Path(args.output).resolve)
    controls = [
        await asyncio.to_thread(Path(getattr(args, key)).resolve)
        for key in ("auth", "budget_state", "service_credentials", "retrieval_credentials")
    ]
    if output.exists() or output in controls:
        raise ConfigurationError("Use a new report path distinct from controls")
    config = ProviderConfig.load(Path(args.providers))
    binding = binding_from_files(Path(args.pointer), config, Path(args.corpus))
    identities = LocalAuth(Path(args.auth))
    token, owner = identities.rows[0]
    other_token = identities.rows[1][0]
    nonce = uuid4().hex[:12]
    report: dict[str, Any] = {
        "status": "RUNNING",
        "checks": {},
        "profile_id": binding.profile_id,
        "comparisons": [],
        "responses": [],
        "errors": [],
    }
    if args.resume_report:
        report = load_json(Path(args.resume_report))
        if (
            report.get("status") != "PROTOCOL_PASS"
            or report.get("profile_id") != binding.profile_id
        ):
            raise ConfigurationError("Resume requires an applicable successful protocol report")
        report["completed_protocol_pids"] = report["pids"]
        report.update(status="RUNNING", errors=[], resume_source=str(args.resume_report))
    output.parent.mkdir(parents=True, exist_ok=True)
    logs = Path(tempfile.mkdtemp(prefix="deephelp-m20-retrieval-"))
    processes = Processes(logs)
    report["diagnostics_directory"], report["pids"] = str(logs), processes.pids
    service_port, retrieval_port, app_port = port(), port(), port()
    service_url, retrieval_url, app_url = (
        f"http://127.0.0.1:{p}" for p in (service_port, retrieval_port, app_port)
    )
    retrieval_args = [
        "-m",
        "deephelp_services.retrieval.api",
        "serve",
        "--credentials",
        args.retrieval_credentials,
        "--providers",
        args.providers,
        "--pointer",
        args.pointer,
        "--corpus",
        args.corpus,
        "--auth",
        args.auth,
        "--port",
        str(retrieval_port),
    ]
    observation = logs / "actual-embedding.json"
    if not args.resume_report:
        retrieval_args = [
            "-c",
            "import sys; "
            "from deephelp_tools.probes.retrieval.retrieval_probe import observed_service; "
            "raise SystemExit(observed_service(sys.argv[1], sys.argv[2:]))",
            str(observation),
            *retrieval_args[2:],
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
        "--retrieval-mcp-url",
        retrieval_url + "/mcp",
        "--retrieval-credentials",
        args.retrieval_credentials,
        "--port",
        str(app_port),
        "--event-collection",
        "dh_m10_events_m20_retrieval",
        "--trace-path",
        str(output.with_suffix(".trace.jsonl")),
    ]
    remote_config = HTTPMCPConfig(
        url=retrieval_url + "/mcp",
        credentials_path=Path(args.retrieval_credentials),
        audience="retrieval",
    )
    business_config = HTTPMCPConfig(
        url=service_url + "/mcp", credentials_path=Path(args.service_credentials)
    )
    gate: BudgetSession | None = None

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name: str, passed: bool) -> None:
        report["checks"][name] = bool(passed)
        save()
        if not passed:
            raise ConfigurationError("Retrieval acceptance failed: " + name)

    def context(query: str) -> RequestEnvelope:
        return RequestEnvelope(
            channel="m20-retrieval-probe",
            session_id="protocol-" + nonce,
            message_id=uuid4().hex,
            raw_text=query,
            occurred_at=datetime.now(UTC),
            received_at=datetime.now(UTC),
            identity=owner,
            request_id=uuid4().hex,
            trace_id=uuid4().hex,
        )

    try:
        async with asyncio.timeout(900), httpx.AsyncClient(trust_env=False, timeout=110) as client:
            await processes.start("retrieval", retrieval_args, retrieval_url + "/mcp")
            async with RemoteRetriever(remote_config, binding).open() as remote:
                if not args.resume_report:
                    gate = BudgetSession(Path(args.budget_state))
                    gate.open()
                    milvus = create_client(Path.cwd())
                    try:
                        for query in (
                            "订单承诺优惠但结账实付不一致",
                            "券暂不可用但订单已经达到门槛",
                        ):
                            running = gate.request()
                            bound = request_execution.set(context(query))
                            try:
                                observed = await remote.compare(
                                    query, binding.scope, running, top_k=binding.top_k
                                )
                                captured = ActualObservedEmbedding(observation, query)
                                expected = await HybridRetriever(
                                    captured,
                                    MilvusHybridStore(milvus, running),
                                    dense_weight=binding.weight,
                                ).compare(query, binding.scope, running, top_k=binding.top_k)
                            finally:
                                request_execution.reset(bound)
                            report["failed_parity"] = {
                                "local": {
                                    k: v.model_dump(mode="json") for k, v in expected.items()
                                },
                                "remote": {
                                    k: v.model_dump(mode="json") for k, v in observed.items()
                                },
                            }
                            save()
                            difference = parity(expected, observed)
                            del report["failed_parity"]
                            report["comparisons"].append(
                                {
                                    "query": query,
                                    "max_score_difference": difference,
                                    "same_actual_vector_hash": hashlib.sha256(
                                        canonical(captured.result.vectors)
                                    ).hexdigest(),
                                    "results": {
                                        k: v.model_dump(mode="json") for k, v in observed.items()
                                    },
                                    "local_result_hash": hashlib.sha256(
                                        canonical(
                                            {
                                                k: v.model_dump(mode="json", exclude={"elapsed_ms"})
                                                for k, v in expected.items()
                                            }
                                        )
                                    ).hexdigest(),
                                    "receipt": remote.diagnostics[-1]["receipt"],
                                }
                            )
                            check(
                                "actual_dense_bm25_hybrid_parity_"
                                + str(len(report["comparisons"])),
                                True,
                            )
                        handshake_before = len(remote.network.handshakes)
                        await processes.stop("retrieval")
                        running = gate.request()
                        bound = request_execution.set(context("服务断连"))
                        try:
                            try:
                                await remote.retrieve("服务断连", binding.scope, running)
                            except AppError as error:
                                check(
                                    "disconnect_fails_closed_and_retains_reservation",
                                    error.code.value == "UPSTREAM_UNAVAILABLE"
                                    and running.uncertain_attempts == 1,
                                )
                            else:
                                check("disconnect_fails_closed_and_retains_reservation", False)
                        finally:
                            request_execution.reset(bound)
                        await processes.start("retrieval", retrieval_args, retrieval_url + "/mcp")
                        bound = request_execution.set(context("订单优惠和结算金额"))
                        try:
                            result = await remote.retrieve(
                                "订单优惠和结算金额", binding.scope, gate.request()
                            )
                        finally:
                            request_execution.reset(bound)
                        check(
                            "independent_retrieval_restart_reconnects_current_profile",
                            bool(result.candidates)
                            and len(remote.network.handshakes) > handshake_before
                            and len(set(processes.pids["retrieval"])) == 2,
                        )
                        report["protocol"] = {
                            "handshakes": remote.network.handshakes,
                            "recovery_receipt": remote.diagnostics[-1]["receipt"],
                            "diagnostics": remote.diagnostics[-2:],
                        }
                    finally:
                        await milvus.close()
                        gate.close()
                        gate = None
                    check(
                        "actual_initialize_protocol",
                        all(
                            h["initialized"] and h["protocol_version"] == "2025-11-25"
                            for h in remote.network.handshakes
                        ),
                    )
                if args.phase == "protocol":
                    report["status"] = "PROTOCOL_PASS"
            if args.phase != "protocol":
                await processes.start(
                    "business",
                    [
                        "-m",
                        "deephelp_services.business.api",
                        "serve",
                        "--credentials",
                        args.service_credentials,
                        "--port",
                        str(service_port),
                    ],
                    service_url + "/records/orders",
                )
                await processes.start("application", app_args, app_url + "/health")
                business = BusinessClient(business_config)
                order_id, coupon_id, activity_id = ("M20R-" + nonce + str(n) for n in (1, 2, 3))
                order = {
                    "order_id": order_id,
                    "paid": {"amount": "173.45"},
                    "discount": {"amount": "12.30"},
                    "discount_status": "applied",
                    "sku_ids": ["SKU-M20-RETRIEVAL"],
                }
                now = datetime.now(UTC)
                period = {
                    "valid_from": (now - timedelta(days=1)).isoformat(),
                    "valid_until": (now + timedelta(days=1)).isoformat(),
                }
                await business.request(owner, "POST", "orders", order)
                await business.request(
                    owner,
                    "POST",
                    "coupons",
                    {
                        "coupon_id": coupon_id,
                        "order_id": order_id,
                        "status": "usable",
                        "minimum_spend": {"amount": "200.00"},
                        **period,
                    },
                )
                await business.request(
                    owner,
                    "POST",
                    "activities",
                    {
                        "activity_id": activity_id,
                        "label": "M20跨服务活动",
                        "rules": "学习样例活动规则",
                        **period,
                    },
                )
                await business.request(
                    owner,
                    "PUT",
                    f"orders/{order_id}/activities",
                    {"expected_version": 1, "activity_ids": [activity_id]},
                )
                report["objects"] = [order_id, coupon_id, activity_id]
                check(
                    "new_objects_committed_through_business_service",
                    BusinessRecord.model_validate(
                        await business.request(owner, "GET", f"orders/{order_id}")
                    ).data
                    == BusinessOrderInput.model_validate(order),
                )
                headers = {"Authorization": "Bearer " + token}

                async def converse(label: str, text: str, original: Any = None) -> tuple[Any, Any]:
                    body = original or {
                        "channel": "m20-retrieval",
                        "session_id": "m20r-" + nonce + "-" + label,
                        "message_id": nonce + "-" + label,
                        "raw_text": text,
                        "occurred_at": datetime.now(UTC).isoformat(),
                    }
                    response = await client.post(app_url + "/converse", headers=headers, json=body)
                    result = ResponseEnvelope.model_validate(response.json())
                    if response.status_code != 200 or result.outcome.value != "ANSWERED":
                        report["responses"].append(
                            {"label": label, "response": result.model_dump(mode="json")}
                        )
                        raise ConfigurationError("Cross-MCP business content failed")
                    return result, body

                first, original = await converse(
                    "discount",
                    f"订单 {order_id} 结算时原本应享受的折扣，实际扣款和减免分别是多少？",
                )
                fact = {f.name: f.value for f in first.facts}
                check(
                    "cross_mcp_real_retrieval_business_and_committed_money",
                    sum(s.retrieval_calls for s in first.stages) > 0
                    and bool(first.tool_call_ids)
                    and str(fact["paid"].amount) == "173.45"
                    and str(fact["discount"].amount) == "12.30"
                    and activity_id in fact["activity_ids"],
                )
                view = await client.get(
                    app_url + f"/debug/runs/{first.run_id}/flow",
                    headers=headers,
                    params={"session_id": original["session_id"]},
                )
                rows = view.json().get("data", {}).get("retrievals") or []
                check(
                    "application_records_both_validations_and_actual_server_actions",
                    view.status_code == 200
                    and bool(rows)
                    and rows[0]["client_validation"] == "PASS"
                    and rows[0]["server_validation"] == "PASS"
                    and {a["action"] for a in rows[0]["receipt"]["actions"]}
                    >= {"embedding", "dense", "bm25", "fusion"},
                )
                report["responses"].append(
                    {
                        "label": "discount",
                        "response": first.model_dump(mode="json"),
                        "retrievals": rows,
                    }
                )
                before = load_json(Path(args.budget_state))
                await processes.stop("application")
                await reclaim_owned_lock(Path(args.budget_state).with_suffix(".lock"))
                await processes.start("application", app_args, app_url + "/health")
                replay, _ = await converse("replay", "", original)
                check(
                    "application_restart_replay_preserves_original_receipt_zero_calls",
                    replay.replayed
                    and replay.run_id == first.run_id
                    and replay.facts == first.facts
                    and replay.evidence_refs == first.evidence_refs
                    and replay.budget_used.attempts == 0
                    and load_json(Path(args.budget_state)) == before
                    and len(set(processes.pids["application"])) == 2,
                )
                coupon, body = await converse(
                    "coupon",
                    f"订单 {order_id} 的券 {coupon_id} 暂时不能抵扣，结算门槛和记录状态是什么？",
                )
                facts = {f.name: f.value for f in coupon.facts}
                check(
                    "new_coupon_message_reads_current_postgresql_fact",
                    facts.get("coupon_status") == "threshold_not_met"
                    and str(facts["minimum_spend"].amount) == "200.00"
                    and bool(coupon.tool_call_ids),
                )
                report["responses"].append(
                    {"label": "coupon", "response": coupon.model_dump(mode="json")}
                )
                denied = await client.get(
                    app_url + f"/business/orders/{order_id}",
                    headers={"Authorization": "Bearer " + other_token},
                )
                check("other_identity_cannot_read_persistent_order", denied.status_code == 404)
                report["status"] = "PASS"
    except (AppError, ConfigurationError) as exc:
        report["status"] = "FAIL"
        report["errors"].append(
            {
                "component": "retrieval/business",
                "code": getattr(exc, "code", None),
                "diagnostic": str(exc),
                "provider_request_id": getattr(exc, "provider_request_id", None),
            }
        )
    except Exception as exc:
        report["status"] = "FAIL"
        report["errors"].append(
            {
                "type": type(exc).__name__,
                "diagnostic": str(exc)[:500] if isinstance(exc, AssertionError) else None,
            }
        )
    finally:
        if gate and gate.lock:
            gate.close()
        try:
            await processes.close()
            await reclaim_owned_lock(Path(args.budget_state).with_suffix(".lock"))
            if report["status"] in {"PASS", "PROTOCOL_PASS"} and cleanup_logs(logs):
                report["diagnostics_directory"] = None
        except Exception as exc:
            report["status"] = "FAIL"
            report["errors"].append({"cleanup_error": type(exc).__name__})
        report["processes_exited"] = not processes.running
        state = load_json(Path(args.budget_state))
        report["cumulative_budget"] = {
            k: state[k]
            for k in (
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
                    "checks": report["checks"],
                    "errors": report["errors"],
                },
                ensure_ascii=False,
                default=str,
            )
        )
    return 0 if report["status"] in {"PASS", "PROTOCOL_PASS"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicit M20 retrieval parity and cross-MCP content"
    )
    parser.add_argument("command", choices=["init", "run"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--phase", choices=["protocol", "all"], default="all")
    parser.add_argument("--resume-report")
    parser.add_argument("--auth", default=".local/m20/retrieval-auth.json")
    parser.add_argument("--budget-state", default=".local/m20/retrieval-budget.json")
    parser.add_argument("--service-credentials", default=".local/m20/service.json")
    parser.add_argument("--retrieval-credentials", default=".local/m20/retrieval-service.json")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--corpus", default="demo/data/m09_corpus.jsonl")
    parser.add_argument("--output", default=".local/m20/retrieval-final.json")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-cost")
    args = parser.parse_args()
    if args.command == "init":
        if None in (args.max_calls, args.max_tokens, args.max_cost):
            raise ConfigurationError("Initialization requires this task's shared limits")
        return initialize(args)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
