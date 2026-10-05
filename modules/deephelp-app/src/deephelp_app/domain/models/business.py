"""Canonical business values and validation; no runtime resources."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    Field,
    model_validator,
)

from .common import DTO, Identifier, Money, PositiveCount, VerifiedIdentity


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


BusinessInput = BusinessOrderInput | BusinessCouponInput | BusinessActivityInput


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
