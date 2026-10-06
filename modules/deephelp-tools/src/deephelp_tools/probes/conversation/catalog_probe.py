"""M42实际PG维护、正式HTTP MCP和原Conversation内容的针对性验收。

固定合成样本在运行前冻结；维护/CAS/归属/证据不经模型，内容才使用原预算writer。
api_client拥有本机HTTP服务生命周期，LiveAssembly拥有模型/工具/PG/索引连接。
finally先关闭借用者再保存预算/报告，未完成或失败不能记PASS；原receipt重投零调用。
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.adapters.mcp.business.business_client import BusinessClient
from deephelp_app.adapters.mcp.shared.network_mcp import HTTPMCPConfig, ServiceCredentials
from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.adapters.persistence.history import PostgresHistory
from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.api.app import create_app
from deephelp_app.application.dense import atomic_json
from deephelp_app.bootstrap.runtime.assembly import LiveAssembly
from deephelp_app.bootstrap.runtime.auth import LocalAuth
from deephelp_app.bootstrap.runtime.budget import BudgetSession, validate_control_paths
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models.business import BusinessRecord
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_services.business.api import create_business_app
from deephelp_tools.evaluation.quality.mvp_acceptance import api_client
from deephelp_tools.integrity import evaluation_provenance
from deephelp_tools.probes.usage import api_records, summarize_usage

# 固定语义与状态不在响应之后改变；编号只加本次namespace以隔离合成资料。
ELIGIBILITY_CASES = (
    ("eligible", "活动编号{activity}，查询我的参与资格与名额。", "ANSWERED", "eligible", "7"),
    (
        "unknown",
        "活动编号{unknown}，我还未参加，请核对参与资格与名额。",
        "HANDOFF",
        "unknown",
        "unknown",
    ),
    ("missing", "活动参与资格和名额能查询吗？", "CLARIFY", None, None),
    (
        "semantic",
        "活动编号{activity}，还没报名，我能报名吗，还有几个位置？",
        "ANSWERED",
        "eligible",
        "7",
    ),
    ("order-adjacent", "查询订单{order}参加的活动。", "ANSWERED", None, None),
    ("coupon-adjacent", "订单{order}的优惠券{coupon}不能用，请核对。", "ANSWERED", None, None),
    ("discount-adjacent", "订单{order}没有优惠，请核对。", "ANSWERED", None, None),
    ("foreign", "活动编号{foreign}，查我的参与资格和名额。", "ERROR", None, None),
)


def safe_failures(error: BaseException) -> list[dict[str, Any]]:
    """展开异步生命周期异常组，只保留安全错误码/请求ID及代码位置。"""
    if isinstance(error, BaseExceptionGroup):
        return [row for child in error.exceptions for row in safe_failures(child)]
    stack = []
    current = error.__traceback__
    while current:
        frame = current.tb_frame
        stack.append({"function": frame.f_code.co_name, "line": current.tb_lineno})
        current = current.tb_next
    return [
        dict(
            type=type(error).__name__,
            stack=stack,
            message=error.safe_message
            if isinstance(error, AppError)
            else str(error)
            if isinstance(error, ConfigurationError)
            else None,
            code=error.code.value if isinstance(error, AppError) else None,
            provider_request_id=error.provider_request_id if isinstance(error, AppError) else None,
            provider_code=error.provider_code if isinstance(error, AppError) else None,
        )
    ]


def content_checks(response: ResponseEnvelope, name: str, ids: dict[str, str]) -> dict[str, bool]:
    """核对独立业务预期的600/事实/节点，可只读复核已采集响应而不重调模型。"""
    expected = {
        "order-adjacent": "ORDER_ACTIVITY_QUERY",
        "coupon-adjacent": "COUPON_UNUSABLE",
        "discount-adjacent": "DISCOUNT_MISSING",
    }.get(name, "ACTIVITY_ELIGIBILITY_QUERY")
    facts = {f.name: f.model_dump(mode="json")["value"] for f in response.facts}
    checks = {
        "intent": response.intent_decision is not None
        and response.intent_decision.final_code == expected
        and response.intent_decision.registry_version == "complaints-v2",
        "catalog": response.versions.registry == "complaints-v2",
        "600": response.call_counts.intent_calls == 1
        and sum(s.intent_calls for s in response.stages if s.stage.startswith("600")) == 1,
    }
    if name in {"eligible", "semantic", "unknown"}:
        checks["activity_binding"] = facts.get("activity_id") == ids[
            "unknown" if name == "unknown" else "activity"
        ] and facts.get("activity_rules") == ("仅当前合成主体，维护资格与名额；报名结果另行核实。")
    elif name == "missing":
        checks["missing_activity"] = response.missing_slots == ("activity_id",) and not facts
    elif name == "foreign":
        checks["owner_failure"] = (
            response.error is not None and response.error.code == "NOT_FOUND" and not facts
        )
    else:
        checks["order_binding"] = facts.get("order_id") == ids["order"]
        checks["paid"] = facts.get("paid") == {"amount": "173.45", "currency": "CNY"}
        if name == "order-adjacent":
            checks["joined_activity"] = facts.get("activity_ids") == ids["activity"]
        elif name == "coupon-adjacent":
            checks["coupon_threshold"] = (
                facts.get("coupon_id") == ids["coupon"]
                and facts.get("coupon_status") == "threshold_not_met"
                and facts.get("minimum_spend") == {"amount": "200.00", "currency": "CNY"}
                and facts.get("usable") is False
                and response.sop_node_path[-1] == "threshold"
            )
        elif name == "discount-adjacent":
            checks["discount_ineligible"] = facts.get("discount_status") == "not_eligible"
    return checks


async def probe(args: argparse.Namespace) -> int:
    """一次有限内容运行；服务维护和模型共享原引擎，却使用各自明确证据。

    新建合成资料归属于可信auth主体；其他主体不能读/关联或改写原记录。
    每条响应先落报告再检查，外部失败保留诊断；不重复已经接收的message_id。
    """
    output = await asyncio.to_thread(Path(args.output).resolve)
    gate = BudgetSession(Path(args.budget_state))
    validate_control_paths(
        [gate.path, Path(args.providers), Path(args.auth), Path(args.service_credentials)],
        output=output,
    )
    if output.exists():
        raise ConfigurationError("Use a new immutable catalog report path")
    auth = LocalAuth(Path(args.auth))
    if len(auth.rows) < 2:
        raise ConfigurationError("Catalog probe needs two distinct synthetic owners")
    token, owner = auth.rows[0]
    other = auth.rows[1][1]
    if owner == other:
        raise ConfigurationError("Catalog ownership counterexample needs a distinct owner")
    credentials = ServiceCredentials.load(Path(args.service_credentials))
    code = evaluation_provenance()
    nonce = uuid4().hex[:12]
    ids = {
        k: "M42-" + k + "-" + nonce for k in ("activity", "unknown", "foreign", "order", "coupon")
    }
    report: dict[str, Any] = dict(
        format="m42-catalog-content-v1",
        status="FAIL",
        synthetic=True,
        phase=args.phase,
        code_digest=code["package_digest"],
        tooling_digest=code["tooling_digest"],
        providers_digest=file_digest(Path(args.providers)),
        pointer_digest=file_digest(Path(args.pointer)),
        rows=[],
        checks={},
        api_observations=[],
        records=ids,
    )

    def check(key: str, passed: bool) -> None:
        report["checks"][key] = passed
        atomic_json(output, report)
        if not passed:
            raise ConfigurationError("Catalog fixed check failed: " + key)

    gate.open()
    runtime: LiveAssembly | None = None
    try:
        business = create_business_app(Path.cwd(), credentials, catalog_version="complaints-v2")
        async with (
            business.router.lifespan_context(business),
            api_client(business, http=True) as business_http,
        ):
            await business.state.repository.migrate()
            config = HTTPMCPConfig(
                url=str(business_http.base_url).rstrip("/") + "/mcp",
                credentials_path=Path(args.service_credentials),
            )
            client = BusinessClient(config)
            now = datetime.now(UTC)
            activity = dict(
                activity_id=ids["activity"],
                label="M42个人合成报名活动",
                rules="仅当前合成主体，维护资格与名额；报名结果另行核实。",
                valid_from=(now - timedelta(days=1)).isoformat(),
                valid_until=(now + timedelta(days=90)).isoformat(),
            )
            for key, identity in [("activity", owner), ("unknown", owner), ("foreign", other)]:
                await client.request(
                    identity, "POST", "activities", {**activity, "activity_id": ids[key]}
                )
            eligibility = dict(
                activity_id=ids["activity"],
                eligibility="eligible",
                remaining_slots=7,
                reason="合成主体已通过手工资格维护。",
            )
            first = BusinessRecord.model_validate(
                await client.request(owner, "POST", "eligibilities", eligibility)
            )
            again = BusinessRecord.model_validate(
                await client.request(owner, "POST", "eligibilities", eligibility)
            )
            check("idempotent_create", first == again)
            await client.request(
                owner,
                "POST",
                "orders",
                dict(
                    order_id=ids["order"],
                    paid={"amount": "173.45"},
                    discount={"amount": "0.00"},
                    discount_status="not_eligible",
                    sku_ids=["M42-SKU"],
                ),
            )
            await client.request(
                owner,
                "POST",
                "coupons",
                dict(
                    coupon_id=ids["coupon"],
                    order_id=ids["order"],
                    status="usable",
                    minimum_spend={"amount": "200.00"},
                    valid_from=activity["valid_from"],
                    valid_until=activity["valid_until"],
                ),
            )
            await client.request(
                owner,
                "PUT",
                f"orders/{ids['order']}/activities",
                dict(expected_version=1, activity_ids=[ids["activity"]]),
            )
            # 两个相同expected_version竞争，独立实库事务只能一个成功；不经模型。
            outcomes = await asyncio.gather(
                *(
                    client.request(
                        owner,
                        "PUT",
                        f"eligibilities/{ids['activity']}",
                        dict(expected_version=first.version, data=eligibility),
                    )
                    for _ in range(2)
                ),
                return_exceptions=True,
            )
            check(
                "cas_one_winner",
                sum(isinstance(r, dict) for r in outcomes) == 1
                and sum(getattr(r, "code", None) == "VERSION_CONFLICT" for r in outcomes) == 1,
            )
            denied = await asyncio.gather(
                client.request(other, "GET", f"eligibilities/{ids['activity']}"),
                client.request(
                    owner, "POST", "eligibilities", {**eligibility, "activity_id": ids["foreign"]}
                ),
                return_exceptions=True,
            )
            check(
                "owner_read_and_relation_denied",
                all(getattr(r, "code", None) == "NOT_FOUND" for r in denied),
            )
            runtime = LiveAssembly(
                Path.cwd(),
                Path(args.providers),
                Path(args.pointer),
                business_mcp=config,
                corpus_path=Path(args.corpus),
                sop_directory=Path(args.sop_directory),
                cascade_policy=Path(args.cascade_policy),
                event_collection="dh_m10_events_m42_eligibility",
            )
            report["sop_snapshot"] = runtime.sop_registry.snapshot_hash
            report["catalog_version"] = runtime.sop_registry.catalog_version
            app = create_app(
                Settings(
                    mode="mvp", request_timeout=90, child_timeout=45, max_attempts=40, retry_limit=2
                ),
                trace=MemoryTrace(),
                identity_provider=auth,
                conversation_factory=runtime.open,
                budget_factory=gate.request,
            )
            async with app.router.lifespan_context(app), api_client(app, http=True) as http:
                for name, text, expected, state, remaining in ELIGIBILITY_CASES:
                    if args.cases and name not in args.cases:
                        continue
                    body = dict(
                        channel="m42-catalog",
                        session_id="m42-" + uuid4().hex,
                        message_id=uuid4().hex,
                        raw_text=text.format(**ids),
                        occurred_at=datetime.now(UTC).isoformat(),
                    )
                    before = dict(gate.state)
                    old_chat = list(runtime.gateway.diagnostics) if runtime.gateway else []
                    old_judge = (
                        list(runtime.judge_gateway.diagnostics) if runtime.judge_gateway else []
                    )
                    raw = await http.post(
                        "/converse", json=body, headers={"Authorization": "Bearer " + token}
                    )
                    response = ResponseEnvelope.model_validate(raw.json())
                    report["rows"].append(
                        dict(case_id=name, response=response.model_dump(mode="json"))
                    )
                    # 先保存实际API诊断，再验内容；失败也保留请求ID/usage，修复只重跑相关样本。
                    if runtime.gateway and runtime.judge_gateway:
                        report["api_observations"].extend(
                            api_records(
                                runtime.gateway.diagnostics,
                                phase="chat",
                                sample=name,
                                previous=old_chat,
                            )
                            + api_records(
                                runtime.judge_gateway.diagnostics,
                                phase="judge",
                                sample=name,
                                previous=old_judge,
                            )
                        )
                    atomic_json(output, report)
                    facts = {f.name: f.value for f in response.facts}
                    for key, passed in content_checks(response, name, ids).items():
                        check(name + ":" + key, passed)
                    check(name + ":outcome", response.outcome == expected)
                    if state:
                        check(
                            name + ":facts",
                            facts.get("eligibility") == state
                            and facts.get("remaining_slots") == remaining,
                        )
                    if name == "missing":
                        check(
                            "missing:zero_calls",
                            gate.state == before and not response.tool_call_ids,
                        )
                    ref = next((r for r in response.evidence_refs if r.source == "tool"), None)
                    if ref:
                        snapshot = await client.request(owner, "GET", "evidence/" + ref.evidence_id)
                        check(
                            name + ":pg_snapshot",
                            snapshot["result"]["call_id"] in response.tool_call_ids
                            and all(
                                r["identity"] == owner.model_dump(mode="json")
                                for r in snapshot["snapshot"]["records"]
                            ),
                        )
                    stable = dict(gate.state)
                    replay = ResponseEnvelope.model_validate(
                        (
                            await http.post(
                                "/converse", json=body, headers={"Authorization": "Bearer " + token}
                            )
                        ).json()
                    )
                    check(
                        name + ":zero_call_replay",
                        replay.replayed
                        and replay.run_id == response.run_id
                        and replay.facts == response.facts
                        and gate.state == stable,
                    )
                    assert runtime.ledger is not None and response.run_id is not None
                    history = await PostgresHistory(runtime.ledger.pool).run(
                        owner, body["session_id"], response.run_id
                    )
                    check(
                        name + ":history",
                        history.response is not None
                        and history.response.facts == response.facts
                        and gate.state == stable,
                    )
                    print(
                        json.dumps(
                            dict(
                                case=name,
                                outcome=response.outcome,
                                model_calls=response.call_counts.model_calls,
                                tool_calls=len(response.tool_call_ids),
                            ),
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            report["status"] = "PASS"
    except Exception as exc:
        report["failures"] = safe_failures(exc)
        report["error"] = {
            "type": type(exc).__name__,
            "message": getattr(exc, "safe_message", None) or str(exc)
            if isinstance(exc, ConfigurationError)
            else "Catalog probe failed; retain fixed evidence",
        }
    finally:
        gate.close()
        report["budget"] = gate.state
        report["usage"] = summarize_usage(report["api_observations"])
        atomic_json(output, report)
    return 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M42 PG/HTTP MCP/original Conversation targeted content"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--phase", choices=["eligibility"], default="eligibility")
    parser.add_argument("--cases", nargs="+", choices=[c[0] for c in ELIGIBILITY_CASES])
    for field in (
        "auth",
        "service-credentials",
        "providers",
        "pointer",
        "corpus",
        "sop-directory",
        "cascade-policy",
        "budget-state",
        "output",
    ):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("Actual catalog content requires explicit --live")
    return asyncio.run(probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
