"""Frozen synthetic SOP mechanisms; shared offline/live cases, never enterprise data."""

from pydantic import Field

from deephelp_app.demo.fixtures import BusinessFixtures, load_business_fixtures
from deephelp_app.demo.tool_config import FaultSpec
from deephelp_app.domain.models import (
    DTO,
    EntityName,
    ErrorCode,
    IntentCode,
    Money,
    SOPStatus,
    ToolName,
)
from deephelp_app.sop_governance import SOPRegistry, bundled_registry


class SOPScenario(DTO):
    case_id: str
    intent: IntentCode
    order: str
    coupon: str | None = None
    missing_slots: tuple[EntityName, ...] = ()
    raw_text: str = "查询合成业务，按已确认编号处理。"
    status: SOPStatus
    tools: tuple[ToolName, ...] = ()
    end_node: str | None = None
    expected_facts: dict[str, str | bool | Money] = Field(default_factory=dict)
    error: ErrorCode | None = None
    fault: FaultSpec | None = None
    fault_tool: ToolName | None = None
    proposal: bool = False


class SOPScenarioSet(DTO):
    version: str
    synthetic: bool
    cases: tuple[SOPScenario, ...]


def fixtures() -> BusinessFixtures:
    """The extra object deliberately contradicts the coupon threshold flag."""
    base = load_business_fixtures().model_dump(mode="json")
    order = next(o for o in base["orders"] if o["order_id"] == "DEMO-C01").copy()
    order.update(order_id="M14-CONFLICT", paid={"amount": "120.00", "currency": "CNY"})
    coupon = next(c for c in base["coupons"] if c["coupon_id"] == "COUPON-C01").copy()
    coupon.update(coupon_id="M14-COUPON", order_id="M14-CONFLICT")
    base["orders"].append(order)
    base["coupons"].append(coupon)
    return BusinessFixtures.model_validate(base)


def proposal_registry() -> SOPRegistry:
    data = bundled_registry().model_dump(mode="json")
    data["registry_version"] = "sop-registry-m14-proposal-v1"
    for definition in data["definitions"]:
        if definition["intent_code"] == "DISCOUNT_MISSING":
            definition["version"] = "discount-missing-m14-proposal-v1"
            for node in definition["nodes"]:
                if node["node_id"] == "manual":
                    node.update(
                        status="NEEDS_APPROVAL",
                        proposal="simulate_discount_adjustment",
                        conclusion="已查询未到账事实。变更仅形成绑定计划，需要审批；尚未申请、批准或执行。",
                    )
    return SOPRegistry.model_validate(data)
