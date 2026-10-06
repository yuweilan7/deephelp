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


COMPLAINT_CASES = (
    ("attempt", "下单失败，尝试编号{attempt}，请查记录原因。", "ANSWERED", None, None),
    ("attempt-missing", "下单失败，请查询尝试记录。", "CLARIFY", None, None),
    ("attempt-absent", "下单失败，尝试编号{absent}，查原因。", "ERROR", None, None),
    ("refund-pending", "退款编号{pending}，优惠券{coupon}没退回，请核对。", "ANSWERED", None, None),
    (
        "refund-returned",
        "退款编号{returned}，优惠券{coupon}没退回，请核对。",
        "ANSWERED",
        None,
        None,
    ),
    (
        "refund-conflict",
        "退款编号{conflict}，优惠券{coupon}没退回，请核对。",
        "HANDOFF",
        None,
        None,
    ),
    (
        "refund-unknown",
        "退款编号{untracked}，优惠券{coupon}没退回，请核对。",
        "HANDOFF",
        None,
        None,
    ),
    (
        "refund-foreign",
        "退款编号{returned}，优惠券{foreign_coupon}没退回，请核对。",
        "ERROR",
        None,
        None,
    ),
    ("refund-missing", "退款后优惠券没退回，请查询记录。", "CLARIFY", None, None),
    *[
        c
        for c in ELIGIBILITY_CASES
        if c[0] in {"eligible", "order-adjacent", "coupon-adjacent", "discount-adjacent"}
    ],
)

# 完整发布增加冻结未知边界，不因召回到相似叶子就授权执行工具。
RELEASE_CASES = (
    *COMPLAINT_CASES,
    ("unsupported-logistics", "只想知道物流什么时候到", "HANDOFF", None, None),
    ("unsupported-refund", "只查询退款进度，不查优惠券回退。", "HANDOFF", None, None),
)

# 新反馈样本在独立盲测之外预先声明；仍走原Conversation/PG事实，不复制测试措辞。
CORPUS_FEEDBACK_CASES = (
    (
        "order-adjacent",
        "订单{order}是不是挂了本轮促销活动关联，帮我核对这次关联。",
        "ANSWERED",
        None,
        None,
    ),
)


def reusable_cases(
    report: dict[str, Any], expected: dict[str, str], requested: list[str]
) -> list[str]:
    """只复用同运行字节/资产/审核的完整成功行；未完成断言或失败行必须重验。

    采集器修正不改变运行时：原FAIL原件保留，最终合并报告绑定其hash。
    每行必须已有原receipt重投及PG历史断言，不能把HTTP200当成内容成功。
    """
    if report.get("phase") != "release" or any(report.get(k) != v for k, v in expected.items()):
        return []
    checks = report.get("checks", {})
    result = []
    for row in report.get("rows", []):
        name = row["case_id"]
        scoped = {k: v for k, v in checks.items() if k.startswith(name + ":")}
        if (
            name in requested
            and scoped
            and all(v is True for v in scoped.values())
            and (
                checks.get(name + ":history") is True
                and checks.get(name + ":zero_call_replay") is True
            )
        ):
            result.append(name)
    return result


def merge_content(
    previous: Path, partial: Path, completed: list[str], requested: list[str]
) -> dict[str, Any]:
    """合并原成功行与本次修复行；不改两份原件，不把旧失败断言变成成功。

    全部来源hash进入发布清单，复用行计量属于原报告，当前writer只新增本次调用。
    """
    old = json.loads(previous.read_text(encoding="utf-8"))
    new: dict[str, Any] = json.loads(partial.read_text(encoding="utf-8"))
    signatures = (
        "code_digest",
        "providers_digest",
        "pointer_digest",
        "assets_digest",
        "corpus_binding_digest",
        "sop_review_digest",
        "skills_review_digest",
    )
    if reusable_cases(old, {k: new[k] for k in signatures}, requested) != completed:
        raise ConfigurationError("Reused content target or completed rows changed")
    checks = {
        k: v
        for k, v in old["checks"].items()
        if any(k.startswith(name + ":") for name in completed)
    } | new["checks"]
    rows = [r for r in old["rows"] if r["case_id"] in completed] + new["rows"]
    passed = (
        new["status"] == "PASS"
        and all(v is True for v in checks.values())
        and len(rows) == len(requested)
        and {r["case_id"] for r in rows} == set(requested)
    )
    return new | dict(
        status="PASS" if passed else "FAIL",
        rows=rows,
        checks=checks,
        reused_cases=completed,
        observed_source=dict(path=str(previous.resolve()), sha256=file_digest(previous)),
        content_sources={str(partial.resolve()): file_digest(partial)},
        usage_scope="Current repaired rows only; reused API usage remains in original bound report",
    )


def reuse_full_content(
    previous: Path, expected: dict[str, str], requested: list[str], tooling: str
) -> dict[str, Any]:
    """管理路由/采集器等tools变化不重跑未改变的业务运行；逐行严格复核相同合同。

    这是零模型的证据复核，当前工具版本说明本次复核者，原模型请求仍归原报告。
    原FAIL、补验part及完整报告都沿来源hash保留，不声称重新执行了模型业务。
    """
    old: dict[str, Any] = json.loads(previous.read_text(encoding="utf-8"))
    completed = reusable_cases(old, expected, requested)
    if (
        old["status"] != "PASS"
        or set(completed) != set(requested)
        or not all(v is True for v in old["checks"].values())
    ):
        raise ConfigurationError("Full content reuse requires complete exact PASS evidence")
    sources = dict(old.get("content_sources", {}))
    if origin := old.get("observed_source"):
        sources[origin["path"]] = origin["sha256"]
    return old | dict(
        tooling_digest=tooling,
        reused_cases=completed,
        api_observations=[],
        observed_source=dict(path=str(previous.resolve()), sha256=file_digest(previous)),
        content_sources=sources,
        usage=summarize_usage([]),
        execution="Read-only exact runtime/content verification; no new business/model execution",
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


def content_checks(
    response: ResponseEnvelope,
    name: str,
    ids: dict[str, str],
    catalog_version: str = "complaints-v2",
) -> dict[str, bool]:
    """核对独立业务预期的600/事实/节点，可只读复核已采集响应而不重调模型。"""
    if name.startswith("unsupported-"):
        return dict(
            rejected=response.intent_decision is not None
            and response.intent_decision.final_code is None,
            no_tool=not response.tool_call_ids and not response.facts and response.sop_plan is None,
            catalog=response.versions.registry == catalog_version,
            stage600=response.call_counts.intent_calls == 1,
        )
    if name.startswith("attempt") or name.startswith("refund"):
        return complaint_checks(response, name, ids, catalog_version)
    expected = {
        "order-adjacent": "ORDER_ACTIVITY_QUERY",
        "coupon-adjacent": "COUPON_UNUSABLE",
        "discount-adjacent": "DISCOUNT_MISSING",
    }.get(name, "ACTIVITY_ELIGIBILITY_QUERY")
    facts = {f.name: f.model_dump(mode="json")["value"] for f in response.facts}
    checks = {
        "intent": response.intent_decision is not None
        and response.intent_decision.final_code == expected
        and response.intent_decision.registry_version == catalog_version,
        "catalog": response.versions.registry == catalog_version,
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


def complaint_checks(
    response: ResponseEnvelope, name: str, ids: dict[str, str], catalog_version: str
) -> dict[str, bool]:
    """独立固定预期核对尝试/退款状态及闭环，模型回复不能作为业务事实源。"""
    attempt = name.startswith("attempt")
    code = "ORDER_CREATION_FAILED" if attempt else "REFUND_COUPON_NOT_RETURNED"
    facts = {f.name: f.value for f in response.facts}
    checks = dict(
        intent=response.intent_decision is not None
        and response.intent_decision.final_code == code
        and response.intent_decision.registry_version == catalog_version,
        catalog=response.versions.registry == catalog_version,
        stage600=response.call_counts.intent_calls == 1
        and sum(s.intent_calls for s in response.stages if s.stage.startswith("600")) == 1,
        no_write=response.sop_plan is None,
    )
    if name.endswith("missing"):
        checks["missing_slots"] = (
            set(response.missing_slots)
            == ({"attempt_id"} if attempt else {"refund_id", "coupon_id"})
            and not facts
            and not response.tool_call_ids
        )
    elif name in {"attempt-absent", "refund-foreign"}:
        checks["not_found"] = (
            response.error is not None and response.error.code == "NOT_FOUND" and not facts
        )
    elif attempt:
        checks["record_cause"] = (
            facts.get("attempt_id") == ids["attempt"]
            and facts.get("attempt_status") == "failed"
            and facts.get("failure_code") == "STOCK_CHANGED"
            and facts.get("failure_reason") == "合成库存变更导致创建失败。"
            and facts.get("follow_up") == "核对库存后由用户另行发起下单。"
            and response.sop_node_path[-1] == "explained"
        )
    else:
        key, state, refund, node = {
            "refund-pending": ("pending", "unknown", "pending", "waiting"),
            "refund-returned": ("returned", "returned", "completed", "returned"),
            "refund-conflict": ("conflict", "conflict", "completed", "conflict"),
            "refund-unknown": ("untracked", "unknown", "completed", "unknown"),
        }[name]
        checks["association_state"] = (
            facts.get("refund_id") == ids[key]
            and facts.get("coupon_id") == ids["coupon"]
            and facts.get("refund_status") == refund
            and facts.get("return_status") == state
            and response.sop_node_path[-1] == node
        )
    return checks


async def maintain_complaints(
    client: BusinessClient,
    owner: Any,
    other: Any,
    ids: dict[str, str],
    activity: dict[str, Any],
    check: Any,
) -> None:
    """真实维护三种新记录，固定CAS竞争/归属反例不经过模型。

    原业务API和PG事务拥有写入；这里仅发合成资料请求。每张新表独立核验CAS，
    两个请求共享expected_version，只允许一个提交；冲突不能计成成功。
    """
    order = dict(
        paid={"amount": "173.45"},
        discount={"amount": "0.00"},
        discount_status="not_eligible",
        sku_ids=["M42-SKU"],
    )
    await client.request(other, "POST", "orders", dict(order_id=ids["foreign_order"], **order))
    coupon = dict(
        status="usable",
        minimum_spend={"amount": "200.00"},
        valid_from=activity["valid_from"],
        valid_until=activity["valid_until"],
    )
    await client.request(
        other,
        "POST",
        "coupons",
        dict(coupon_id=ids["foreign_coupon"], order_id=ids["foreign_order"], **coupon),
    )
    await client.request(
        owner,
        "POST",
        "coupons",
        dict(coupon_id=ids["different_coupon"], order_id=ids["order"], **coupon),
    )
    attempt = dict(
        attempt_id=ids["attempt"],
        status="failed",
        failure_code="STOCK_CHANGED",
        reason="合成库存变更导致创建失败。",
        follow_up="核对库存后由用户另行发起下单。",
    )
    await client.request(owner, "POST", "attempts", attempt)
    for key in ("pending", "returned", "conflict", "untracked"):
        await client.request(
            owner,
            "POST",
            "refunds",
            dict(
                refund_id=ids[key],
                order_id=ids["order"],
                coupon_id=ids["coupon"],
                status="pending" if key == "pending" else "completed",
                reason="合成退款来源记录。",
            ),
        )
    returned = dict(
        refund_id=ids["returned"],
        coupon_id=ids["coupon"],
        status="returned",
        reason="同退款同券的合成回退记录。",
    )
    await client.request(owner, "POST", "coupon_returns", returned)
    await client.request(
        owner,
        "POST",
        "coupon_returns",
        dict(returned, refund_id=ids["conflict"], coupon_id=ids["different_coupon"]),
    )
    refund = dict(
        refund_id=ids["returned"],
        order_id=ids["order"],
        coupon_id=ids["coupon"],
        status="completed",
        reason="合成退款来源记录。",
    )
    for kind, data, key in [
        ("attempts", attempt, "attempt_id"),
        ("refunds", refund, "refund_id"),
        ("coupon_returns", returned, "refund_id"),
    ]:
        current = await client.request(owner, "GET", f"{kind}/{data[key]}")
        same = await client.request(owner, "POST", kind, data)
        check(kind + ":idempotent_create", current == same)
        rows = await asyncio.gather(
            *(
                client.request(
                    owner,
                    "PUT",
                    f"{kind}/{data[key]}",
                    dict(expected_version=current["version"], data=data),
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        check(
            kind + ":cas_one_winner",
            sum(isinstance(r, dict) for r in rows) == 1
            and sum(getattr(r, "code", None) == "VERSION_CONFLICT" for r in rows) == 1,
        )
    denied = await asyncio.gather(
        client.request(other, "GET", f"attempts/{ids['attempt']}"),
        client.request(
            owner,
            "POST",
            "refunds",
            dict(refund, refund_id=ids["absent"], coupon_id=ids["foreign_coupon"]),
        ),
        client.request(other, "POST", "coupon_returns", returned),
        return_exceptions=True,
    )
    check(
        "complaint_owner_relations_denied",
        all(getattr(r, "code", None) == "NOT_FOUND" for r in denied),
    )


async def probe(args: argparse.Namespace, *, borrowed_gate: BudgetSession | None = None) -> int:
    """一次有限内容运行；服务维护和模型共享原引擎，却使用各自明确证据。

    新建合成资料归属于可信auth主体；其他主体不能读/关联或改写原记录。
    每条响应先落报告再检查，外部失败保留诊断；不重复已经接收的message_id。
    """
    output = await asyncio.to_thread(Path(args.output).resolve)
    gate = borrowed_gate or BudgetSession(Path(args.budget_state))
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
    if args.phase == "corpus-feedback" and (
        owner.tenant_id != "synthetic-tenant" or owner.user_id != "synthetic-user-a"
    ):
        raise ConfigurationError(
            "Original feedback collection requires its isolated synthetic owner"
        )
    other = auth.rows[1][1]
    if owner == other:
        raise ConfigurationError("Catalog ownership counterexample needs a distinct owner")
    catalog_version = "complaints-v2" if args.phase == "eligibility" else "complaints-v3"
    credentials = ServiceCredentials.load(Path(args.service_credentials))
    code = evaluation_provenance()
    nonce = uuid4().hex[:12]
    ids = {
        k: "M42-" + k + "-" + nonce
        for k in (
            "activity",
            "unknown",
            "foreign",
            "order",
            "coupon",
            "attempt",
            "absent",
            "pending",
            "returned",
            "conflict",
            "untracked",
            "foreign_coupon",
            "foreign_order",
            "different_coupon",
        )
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

    if borrowed_gate is None:
        gate.open()
    runtime: LiveAssembly | None = None
    try:
        if args.assets:
            from deephelp_app.adapters.releases.catalog_binding import catalog_binding
            from deephelp_app.application.artifact_paths import resolve_artifact
            from deephelp_app.application.collaboration import policy_release_binding
            from deephelp_app.application.corpus import digest
            from deephelp_app.application.intent_prompt import default_system, fallback_contract
            from deephelp_tools.evaluation.feedback.flywheel_assets import verify_files

            data = verify_files(Path(args.assets))
            binding = catalog_binding(data, catalog_version)
            assert binding is not None
            from deephelp_app.adapters.releases.corpus_binding import corpus_binding

            if authored := corpus_binding(data):
                report["corpus_binding_digest"] = digest(authored)
            if any(
                resolve_artifact(actual) != resolve_artifact(expected)
                for actual, expected in (
                    (args.pointer, data["dense_pointer"]),
                    (args.corpus, binding["corpus"]),
                    (args.cascade_policy, binding["cascade_policy"]),
                )
            ):
                raise ConfigurationError("Content probe differs from complete catalog assets")
            report.update(
                assets_digest=digest(data),
                catalog_binding_digest=digest(binding),
                code_integrity_scope="runtime-v2",
                judge_providers_digest=file_digest(
                    Path(args.providers).parent / "event-judge.example.json"
                ),
                collaboration_policy_digest=policy_release_binding()["policy_digest"],
                intent_prompt_request_digest=digest(
                    [default_system(catalog_version), fallback_contract(catalog_version)]
                ),
            )
        business = create_business_app(Path.cwd(), credentials, catalog_version=catalog_version)
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
            if args.phase != "eligibility":
                await maintain_complaints(client, owner, other, ids, activity, check)
            runtime = LiveAssembly(
                Path.cwd(),
                Path(args.providers),
                Path(args.pointer),
                business_mcp=config,
                corpus_path=Path(args.corpus),
                sop_directory=Path(args.sop_directory),
                cascade_policy=Path(args.cascade_policy),
                event_collection="dh_m10_events_m42_" + args.phase.replace("-", "_"),
                reviewed_rules=Path(data["rules"]) if args.assets else None,
                skill_review=Path(args.skill_review) if args.skill_review else None,
            )
            if getattr(args, "sop_review", None):
                from deephelp_app.adapters.releases.sop_drafts import read_sop_review

                reviewed_sop, sop_record = read_sop_review(Path(args.sop_review))
                # 本次任务以已冻结审核为准；另一个草稿发布到候选索引不能改换本任务目标。
                # 旧快照仍供原问题回读，新问题只得到这份完整候选。
                runtime.sop_registry = reviewed_sop
                runtime.sop_snapshots = (reviewed_sop, *runtime.sop_snapshots)
                report["sop_review_digest"] = file_digest(Path(args.sop_review))
                report["reviewed_sop"] = next(
                    d for d in reviewed_sop.definitions if d.sop_id == sop_record["sop_id"]
                ).model_dump(mode="json")
                report["acceptance_scope"] = (
                    "Selected changed SOP and adjacent fixed content; not full historical quality"
                )
            report["sop_snapshot"] = runtime.sop_registry.snapshot_hash
            report["catalog_version"] = runtime.sop_registry.catalog_version
            if args.skill_review:
                report["skills_digest"] = runtime.skills_registry.digest
                report["skills_review_digest"] = file_digest(Path(args.skill_review))
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
                for name, text, expected, state, remaining in (
                    CORPUS_FEEDBACK_CASES
                    if args.phase == "corpus-feedback"
                    else RELEASE_CASES
                    if args.phase == "release"
                    else COMPLAINT_CASES
                    if args.phase == "complaints"
                    else ELIGIBILITY_CASES
                ):
                    if args.cases and name not in args.cases:
                        continue
                    body = dict(
                        channel="m18-collect" if args.phase == "corpus-feedback" else "m42-catalog",
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
                    for key, passed in content_checks(response, name, ids, catalog_version).items():
                        check(name + ":" + key, passed)
                    check(name + ":outcome", response.outcome == expected)
                    if edited := report.get("reviewed_sop"):
                        # 已识别叶子在缺槽之前就固定SOP版本/快照；澄清没有节点路径，
                        # 但仍有合法绑定。未知意图没有SOP版本，不能据节点是否执行判断。
                        check(
                            name + ":candidate_snapshot",
                            response.versions.dataset == runtime.scope.dataset_version
                            and response.versions.embedding_signature
                            == runtime.scope.signature.fingerprint
                            and (
                                response.versions.sop_snapshot == runtime.sop_registry.snapshot_hash
                                if response.versions.sop is not None
                                else response.versions.sop_snapshot is None
                            ),
                        )
                        if (
                            response.intent_decision
                            and response.intent_decision.final_code == edited["intent_code"]
                        ):
                            visited = set(response.sop_node_path)
                            ends = [
                                n
                                for n in edited["nodes"]
                                if n["kind"] == "end" and n["node_id"] in visited
                            ]
                            check(
                                name + ":edited_version", response.versions.sop == edited["version"]
                            )
                            check(
                                name + ":edited_conclusion",
                                bool(ends) and any(n["conclusion"] in response.reply for n in ends),
                            )
                    if state:
                        check(
                            name + ":facts",
                            facts.get("eligibility") == state
                            and facts.get("remaining_slots") == remaining,
                        )
                    if name in {"missing", "attempt-missing", "refund-missing"}:
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
        if borrowed_gate is None:
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
    parser.add_argument(
        "--phase",
        choices=["eligibility", "complaints", "release", "corpus-feedback"],
        default="eligibility",
    )
    parser.add_argument(
        "--assets", help="Exact immutable catalog assets for complete release content"
    )
    parser.add_argument("--skill-review", help="Retained approved Skill snapshot for the candidate")
    parser.add_argument("--sop-review", help="Immutable approved SOP snapshot for the candidate")
    parser.add_argument(
        "--cases", nargs="+", choices=sorted({c[0] for c in (*ELIGIBILITY_CASES, *RELEASE_CASES)})
    )
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
