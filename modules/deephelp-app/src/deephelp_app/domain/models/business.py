"""订单、券、活动及维护版本的值，Business服务用仓储事务读写。

金额、有效期、关系去重在边界校验；BusinessUpdate带expected_version防止覆盖并发修改。
业务记录能是合成内容，是否持久/真实协议由调用路径决定。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    Field,
    model_validator,
)

from .common import DTO, Count, Identifier, Money, PositiveCount, VerifiedIdentity


class BusinessOrderInput(DTO):
    order_id: Identifier
    paid: Money
    discount: Money
    discount_status: Literal["applied", "not_eligible", "missing"]
    sku_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=50)
    status: Literal["active", "inactive"] = "active"

    @model_validator(mode="after")
    def business_amounts(self) -> BusinessOrderInput:
        if self.discount.amount > self.paid.amount or self.paid.amount >= Decimal("1e18"):
            raise ValueError("Invalid business amount")
        if self.discount_status != "applied" and self.discount.amount != 0:
            raise ValueError("Unapplied discount must be zero")
        if len(set(self.sku_ids)) != len(self.sku_ids):
            raise ValueError("Duplicate product")
        return self


class BusinessCouponInput(DTO):
    coupon_id: Identifier
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
    valid_from: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def validity(self) -> BusinessCouponInput:
        if self.valid_until <= self.valid_from or self.minimum_spend.amount >= Decimal("1e18"):
            raise ValueError("Invalid coupon period/amount")
        return self


class BusinessActivityInput(DTO):
    activity_id: Identifier
    label: Annotated[str, Field(strict=True, min_length=1, max_length=200)]
    rules: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
    scope: Literal["owner"] = "owner"
    status: Literal["active", "inactive"] = "active"
    valid_from: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def validity(self) -> BusinessActivityInput:
        if self.valid_until <= self.valid_from:
            raise ValueError("Invalid activity period")
        return self


class BusinessEligibilityInput(DTO):
    """当前主体的活动资格记录；缺失名额为未知，不等于零。

    rules由关联活动提供；维护事务必须确认同主体活动存在。
    资格与名额是两种事实，eligible且remaining_slots=0也不能承诺报名成功。
    """

    activity_id: Identifier
    eligibility: Literal["eligible", "ineligible", "unknown"]
    remaining_slots: Count | None = None
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]


class BusinessAttemptInput(DTO):
    """下单尝试的来源记录，不能把一次失败当成已创建的订单。

    原因与后续指引均由维护者提供；unknown明确缺证据，不由模型补写。
    查询不会创建订单或重新执行尝试。
    """

    attempt_id: Identifier
    status: Literal["failed", "succeeded", "unknown"]
    failure_code: Annotated[str, Field(strict=True, min_length=1, max_length=200)]
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
    follow_up: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]


class BusinessRefundInput(DTO):
    """同主体订单/券的退款记录，用户陈述不能改变其完成状态。

    关联只能引用已维护且同主体的订单和券；维护事务再次核对券所属订单。
    """

    refund_id: Identifier
    order_id: Identifier
    coupon_id: Identifier
    status: Literal["pending", "completed", "failed", "unknown"]
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]


class BusinessCouponReturnInput(DTO):
    """退款对应的券回退来源记录，以refund_id唯一维护并保留CAS版本。

    同主体但券号与退款记录不一致允许保存为原始矛盾，查询必须报告conflict。
    这样不会为了通过校验覆盖来源差异；外主体关联仍由事务拒绝。
    """

    refund_id: Identifier
    coupon_id: Identifier
    status: Literal["pending", "returned", "failed", "unknown"]
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]


BusinessInput = (
    BusinessOrderInput
    | BusinessCouponInput
    | BusinessActivityInput
    | BusinessEligibilityInput
    | BusinessAttemptInput
    | BusinessRefundInput
    | BusinessCouponReturnInput
)


class BusinessRecord(DTO):
    identity: VerifiedIdentity
    version: PositiveCount
    data: BusinessInput


class BusinessUpdate(DTO):
    expected_version: PositiveCount
    data: BusinessInput


class BusinessRelations(DTO):
    expected_version: PositiveCount
    activity_ids: tuple[Identifier, ...] = Field(max_length=50)

    @model_validator(mode="after")
    def unique(self) -> BusinessRelations:
        if len(set(self.activity_ids)) != len(self.activity_ids):
            raise ValueError("Duplicate activity relation")
        return self
