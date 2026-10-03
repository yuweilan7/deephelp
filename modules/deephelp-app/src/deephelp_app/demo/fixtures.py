"""Synthetic downstream facts; independent of gold cases and evaluation."""

from importlib.resources import files
from typing import Literal

from pydantic import model_validator

from deephelp_app.domain.models import DTO, Identifier, Money, VerifiedIdentity


class SyntheticOrder(DTO):
    order_id: Identifier
    identity: VerifiedIdentity
    paid: Money
    discount: Money
    discount_status: Literal["applied", "not_eligible", "missing"]
    activity_ids: tuple[Identifier, ...]
    sku_id: Identifier


class SyntheticCoupon(DTO):
    coupon_id: Identifier
    identity: VerifiedIdentity
    order_id: Identifier
    status: Literal[
        "usable",
        "expired",
        "threshold_not_met",
        "already_used",
        "not_started",
        "scope_mismatch",
        "frozen",
        "revoked",
    ]
    minimum_spend: Money


class SyntheticActivity(DTO):
    activity_id: Identifier
    label: str


class BusinessFixtures(DTO):
    synthetic: Literal[True]
    version: Literal["business-fixtures-v1"]
    orders: tuple[SyntheticOrder, ...]
    coupons: tuple[SyntheticCoupon, ...]
    activities: tuple[SyntheticActivity, ...]

    @model_validator(mode="after")
    def references(self) -> BusinessFixtures:
        orders = {order.order_id: order for order in self.orders}
        coupons = {coupon.coupon_id: coupon for coupon in self.coupons}
        activities = {activity.activity_id for activity in self.activities}
        if (
            len(orders) != len(self.orders)
            or len(coupons) != len(self.coupons)
            or len(activities) != len(self.activities)
        ):
            raise ValueError("Duplicate business fixture ID")
        for order in self.orders:
            if not set(order.activity_ids) <= activities:
                raise ValueError("Order references an unknown activity")
        for coupon in self.coupons:
            if coupon.order_id not in orders or orders[coupon.order_id].identity != coupon.identity:
                raise ValueError("Coupon must reference an order of the same owner")
        return self


def load_business_fixtures() -> BusinessFixtures:
    text = files("deephelp_app").joinpath("assets/demo/business.json").read_text(encoding="utf-8")
    return BusinessFixtures.model_validate_json(text)
