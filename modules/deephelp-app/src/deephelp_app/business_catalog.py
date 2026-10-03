"""Frozen business outcomes shared by SOP acceptance and end-to-end evaluation.

Each entry has actual synthetic downstream facts, tool order and an expected terminal
node. Language variants alone do not define a new business scenario.
"""

import json
from pathlib import Path

from deephelp_app.demo.fixtures import BusinessFixtures
from deephelp_app.demo.scenarios import SOPScenario
from deephelp_app.demo.tool_config import MockConfig
from deephelp_app.errors import ConfigurationError
from deephelp_app.sop_governance import SOPRegistry, bundled_registry

CATALOG = Path(__file__).parent / "assets/evaluation/business-catalog-v2.json"
FIXTURES = Path(__file__).parent / "assets/demo/business-fixtures-v2.json"
REGISTRY = Path(__file__).parent / "sop_data/business-registry-v2.json"


def business_registry() -> SOPRegistry:
    return SOPRegistry.model_validate_json(REGISTRY.read_text(encoding="utf-8"))


def business_fixtures() -> BusinessFixtures:
    return BusinessFixtures.model_validate_json(FIXTURES.read_text(encoding="utf-8"))


def business_cases() -> tuple[SOPScenario, ...]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    if data.get("version") != "m14-business-catalog-v2" or not data.get("synthetic"):
        raise ConfigurationError("Only the frozen synthetic business catalog is supported")
    cases = tuple(SOPScenario.model_validate(row) for row in data["cases"])
    if len({c.case_id for c in cases}) != len(cases):
        raise ConfigurationError("Business scenario identity is duplicated")
    return cases


def business_config() -> MockConfig:
    return MockConfig(fixtures=business_fixtures(), max_invocations=4096)


def business_case(identity: str) -> SOPScenario:
    for case in business_cases():
        if case.case_id == identity:
            return case
    raise ConfigurationError("Unknown frozen business scenario")


def validate_catalog() -> dict[str, int]:
    """Reject cosmetic duplicates; allow distinct source/boundary outcomes at one node."""
    registry, fixtures, cases = business_registry(), business_fixtures(), business_cases()
    assert registry.tool_schema_hash == bundled_registry().tool_schema_hash
    geometries = set()
    for case in cases:
        definition = registry.definition(case.intent)
        if case.end_node and case.end_node not in {n.node_id for n in definition.nodes}:
            raise ConfigurationError("Business scenario has no published terminal node")
        order = next((o for o in fixtures.orders if o.order_id == case.order), None)
        coupon = next((c for c in fixtures.coupons if c.coupon_id == case.coupon), None)
        signature = (
            case.intent,
            case.status,
            case.tools,
            case.end_node,
            case.error,
            order.discount_status if order else None,
            str(order.paid.amount) if order and coupon else None,
            len(order.activity_ids) if order else None,
            coupon.status if coupon else None,
            str(coupon.minimum_spend.amount) if coupon else None,
            bool(order and order.identity.user_id != "synthetic-user-a"),
            bool(coupon and coupon.order_id != case.order),
        )
        if signature in geometries:
            raise ConfigurationError("Changing identifiers is not a new business scenario")
        geometries.add(signature)
    return dict(scenarios=len(cases), business_geometries=len(geometries))
