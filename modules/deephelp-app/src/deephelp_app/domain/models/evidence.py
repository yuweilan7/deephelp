"""Canonical evidence values and validation; no runtime resources."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    Field,
    StrictBool,
    StrictStr,
    model_validator,
)

from .common import DTO, Identifier, Money, RawText


class EvidenceSource(StrEnum):
    MESSAGE = "message"
    TOOL = "tool"
    DATASET = "dataset"


class EvidenceRef(DTO):
    evidence_id: Identifier
    source: EvidenceSource
    record_id: Identifier
    version: Identifier | None = None
    locator: Identifier | None = None
    summary: RawText | None = None
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class FactKind(StrEnum):
    TEXT = "text"
    MONEY = "money"
    FLAG = "flag"


class Fact(DTO):
    name: Identifier
    kind: FactKind
    value: StrictStr | StrictBool | Money
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def typed_value(self) -> Fact:
        expected = {FactKind.TEXT: str, FactKind.MONEY: Money, FactKind.FLAG: bool}[self.kind]
        if type(self.value) is not expected:
            raise ValueError("Fact value must match its declared kind")
        return self


def validate_facts(
    facts: tuple[Fact, ...] | list[Fact], refs: tuple[EvidenceRef, ...] | list[EvidenceRef]
) -> None:
    ids = [ref.evidence_id for ref in refs]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate evidence id")
    if any(not set(fact.evidence_ids) <= set(ids) for fact in facts):
        raise ValueError("Facts must reference supplied evidence")
