"""Explicit, versioned intent hierarchy; no hierarchy inferred from code spelling."""

from pydantic import model_validator

from deephelp_app.domain.models import (
    DTO,
    REGISTRY_VERSION,
    EntityName,
    IntentCode,
    IntentDefinition,
)


class IntentRegistry(DTO):
    version: str
    intents: tuple[IntentDefinition, ...]

    @model_validator(mode="after")
    def hierarchy(self) -> IntentRegistry:
        if self.version != REGISTRY_VERSION:
            raise ValueError("Unsupported registry version")
        entries = {item.code: item for item in self.intents}
        if len(entries) != len(self.intents) or set(entries) != set(IntentCode):
            raise ValueError("Registry must contain every published code exactly once")
        for item in self.intents:
            levels = tuple(x for x in (item.l1, item.l2, item.l3, item.l4) if x is not None)
            if item.parent_code is None:
                if len(levels) != 1:
                    raise ValueError("Root must have exactly one level")
            else:
                parent = entries[item.parent_code]
                parent_levels = tuple(
                    x for x in (parent.l1, parent.l2, parent.l3, parent.l4) if x is not None
                )
                if levels[:-1] != parent_levels or parent.is_actionable:
                    raise ValueError("Child must extend its explicit non-actionable parent")
        return self

    def get(self, code: IntentCode) -> IntentDefinition:
        return next(item for item in self.intents if item.code == code)

    @property
    def actionable_codes(self) -> tuple[IntentCode, ...]:
        return tuple(item.code for item in self.intents if item.is_actionable)


def complaint_registry() -> IntentRegistry:
    """Construct on demand; importing the package performs no file or service I/O."""
    return IntentRegistry(
        version=REGISTRY_VERSION,
        intents=(
            IntentDefinition(
                code=IntentCode.SERVICE, label="合成客诉服务", l1="service", is_actionable=False
            ),
            IntentDefinition(
                code=IntentCode.BENEFIT_ISSUE,
                label="优惠问题",
                l1="service",
                l2="benefit",
                parent_code=IntentCode.SERVICE,
                is_actionable=False,
            ),
            IntentDefinition(
                code=IntentCode.ORDER_QUERY,
                label="订单查询",
                l1="service",
                l2="order",
                parent_code=IntentCode.SERVICE,
                is_actionable=False,
            ),
            IntentDefinition(
                code=IntentCode.DISCOUNT_MISSING,
                label="优惠未享受",
                l1="service",
                l2="benefit",
                l3="discount_missing",
                parent_code=IntentCode.BENEFIT_ISSUE,
                is_actionable=True,
                required_slots=(EntityName.ORDER_ID,),
                sop_id="discount-missing-v1",
            ),
            IntentDefinition(
                code=IntentCode.COUPON_UNUSABLE,
                label="券不可用",
                l1="service",
                l2="benefit",
                l3="coupon_unusable",
                parent_code=IntentCode.BENEFIT_ISSUE,
                is_actionable=True,
                required_slots=(EntityName.ORDER_ID, EntityName.COUPON_ID),
                sop_id="coupon-unusable-v1",
            ),
            IntentDefinition(
                code=IntentCode.ORDER_ACTIVITY_QUERY,
                label="订单活动查询",
                l1="service",
                l2="order",
                l3="activity_query",
                parent_code=IntentCode.ORDER_QUERY,
                is_actionable=True,
                required_slots=(EntityName.ORDER_ID,),
                sop_id="order-activity-v1",
            ),
        ),
    )
