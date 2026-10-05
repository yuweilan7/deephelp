"""实体与更正冲突的值；编号保留字符串，来源指向实际消息与原文片段。

Entity是可用槽位，EntityConflict保留被替换值与当前值。
显式更正必须来自不同消息，不能把同条含两个编号的歧义伪装成已解决更正。
"""

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
