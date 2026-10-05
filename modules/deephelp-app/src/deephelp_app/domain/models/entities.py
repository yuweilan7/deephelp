"""Canonical entities values and validation; no runtime resources."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import (
    model_validator,
)

from .common import DTO, Identifier, RawText


class EntityName(StrEnum):
    ORDER_ID = "order_id"
    COUPON_ID = "coupon_id"
    SKU_ID = "sku_id"
    ACTIVITY_ID = "activity_id"


class EntitySource(DTO):
    message_id: Identifier
    excerpt: RawText


class Entity(DTO):
    name: EntityName
    value: Identifier
    source: EntitySource


class EntityConflict(DTO):
    previous: Entity
    replacement: Entity
    resolution: Literal["unresolved", "explicit_correction"]

    @model_validator(mode="after")
    def distinct_values(self) -> EntityConflict:
        if self.previous.name != self.replacement.name:
            raise ValueError("A conflict concerns one entity slot")
        if self.previous.value == self.replacement.value:
            raise ValueError("A conflict must retain different values")
        if (
            self.resolution == "explicit_correction"
            and self.previous.source.message_id == self.replacement.source.message_id
        ):
            raise ValueError("A correction must retain the original and new message sources")
        return self
