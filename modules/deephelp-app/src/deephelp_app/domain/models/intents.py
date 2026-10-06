"""版本目录中的业务意图、所需槽位与目录条目。

RuleMatch只是当前原文中的候选，不授权执行工具；required_slots是代码统一的业务契约。
目录层级与是否有SOP须一致，未注册标签不能变成新的业务能力。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import (
    model_validator,
)

from .common import DTO, REGISTRY_VERSION, Count, Identifier, PositiveCount, RawText
from .entities import EntityName, EntitySource


class IntentCode(StrEnum):
    SERVICE = "SERVICE"
    BENEFIT_ISSUE = "BENEFIT_ISSUE"
    ORDER_QUERY = "ORDER_QUERY"
    DISCOUNT_MISSING = "DISCOUNT_MISSING"
    COUPON_UNUSABLE = "COUPON_UNUSABLE"
    ORDER_ACTIVITY_QUERY = "ORDER_ACTIVITY_QUERY"
    ACTIVITY_ELIGIBILITY_QUERY = "ACTIVITY_ELIGIBILITY_QUERY"

    ORDER_CREATION_FAILED = "ORDER_CREATION_FAILED"
    REFUND_COUPON_NOT_RETURNED = "REFUND_COUPON_NOT_RETURNED"

    @property
    def required_slots(self) -> tuple[EntityName, ...]:
        if self == IntentCode.ORDER_CREATION_FAILED:
            return (EntityName.ATTEMPT_ID,)
        if self == IntentCode.REFUND_COUPON_NOT_RETURNED:
            return (EntityName.REFUND_ID, EntityName.COUPON_ID)
        if self == IntentCode.ACTIVITY_ELIGIBILITY_QUERY:
            return (EntityName.ACTIVITY_ID,)
        if self == IntentCode.COUPON_UNUSABLE:
            return (EntityName.ORDER_ID, EntityName.COUPON_ID)
        if self in {IntentCode.DISCOUNT_MISSING, IntentCode.ORDER_ACTIVITY_QUERY}:
            return (EntityName.ORDER_ID,)
        return ()

    @property
    def actionable(self) -> bool:
        return bool(self.required_slots)


class RuleMatch(DTO):
    rule_id: Identifier
    rule_version: Identifier
    candidate_code: IntentCode
    priority: Count
    evidence: EntitySource
    start: Count
    end: PositiveCount

    @model_validator(mode="after")
    def candidate_only(self) -> RuleMatch:
        if not self.candidate_code.actionable or self.end <= self.start:
            raise ValueError("Rule output requires a leaf candidate and raw evidence")
        return self


class IntentDefinition(DTO):
    code: IntentCode
    label: RawText
    l1: Identifier
    l2: Identifier | None = None
    l3: Identifier | None = None
    l4: Identifier | None = None
    parent_code: IntentCode | None = None
    is_actionable: bool
    required_slots: tuple[EntityName, ...] = ()
    sop_id: Identifier | None = None
    version: Literal["complaints-v1", "complaints-v2", "complaints-v3"] = REGISTRY_VERSION

    @model_validator(mode="after")
    def current_catalog(self) -> IntentDefinition:
        levels = (self.l1, self.l2, self.l3, self.l4)
        seen_null = False
        for level in levels:
            if level is None:
                seen_null = True
            elif seen_null:
                raise ValueError("Explicit hierarchy cannot have gaps")
        if self.is_actionable != self.code.actionable:
            raise ValueError("Actionability must agree with the published code")
        if self.required_slots != self.code.required_slots:
            raise ValueError("Slots must agree with the published code")
        if self.is_actionable != (self.sop_id is not None):
            raise ValueError("Only actionable intents bind a SOP")
        return self
