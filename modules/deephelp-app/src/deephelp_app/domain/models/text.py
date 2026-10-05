"""300阶段原文、分段、实体观察和规则候选的结果契约。

ExtractedEntity偏移指向raw_text，清洗不改变原证据；否定与更正也保留观察。
实体、规则片段与当前message_id逐项绑定，模型schema合法后仍需这些证据校验。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import (
    Field,
    model_validator,
)

from .common import DTO, Count, ErrorCode, Identifier, PositiveCount, RawText
from .entities import Entity, EntityConflict, EntityName
from .intents import RuleMatch


class TextSegment(DTO):
    start: Count
    end: PositiveCount
    text: RawText

    @model_validator(mode="after")
    def valid_span(self) -> TextSegment:
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("Segment offsets must cover its text")
        return self


class TextCleanResult(DTO):
    raw_text: RawText
    cleaned_text: RawText
    segments: list[TextSegment] = Field(min_length=1, max_length=125)
    normalization_version: Literal["width-whitespace-v1"] = "width-whitespace-v1"
    truncated: Literal[False] = False

    @model_validator(mode="after")
    def complete_segments(self) -> TextCleanResult:
        position = 0
        for segment in self.segments:
            if segment.start != position:
                raise ValueError("Clean segments must be contiguous")
            position = segment.end
        if (
            position != len(self.cleaned_text)
            or "".join(segment.text for segment in self.segments) != self.cleaned_text
        ):
            raise ValueError("Clean segments must retain the whole cleaned text")
        return self


class ExtractedEntity(Entity):
    """A current-message observation; offsets address raw_text, never cleaned_text."""

    start: Count
    end: PositiveCount
    layer: Literal["regex", "api", "strong"]
    confidence_kind: Literal["deterministic", "model_grounded"]
    disposition: Literal["observed", "negated", "correction"] = "observed"

    @model_validator(mode="after")
    def valid_observation(self) -> ExtractedEntity:
        if self.end <= self.start:
            raise ValueError("Entity requires a nonempty raw span")
        if (self.layer == "regex") != (self.confidence_kind == "deterministic"):
            raise ValueError("Confidence kind must describe the extraction method")
        normalized = "".join(
            " " if c == "\u3000" else chr(ord(c) - 0xFEE0) if "\uff01" <= c <= "\uff5e" else c
            for c in self.source.excerpt
        )
        if normalized != self.value or self.end - self.start != len(self.source.excerpt):
            raise ValueError("Entity value must equal its width-normalized raw evidence")
        return self

    def as_entity(self) -> Entity:
        return Entity(name=self.name, value=self.value, source=self.source)


class DemandType(StrEnum):
    MAIN = "main"
    SUPPLEMENT = "supplement"
    NEW_TOPIC = "new_topic"
    UNKNOWN = "unknown"


class ExtractionLayerStat(DTO):
    layer: Literal["regex", "api", "strong"]
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    model_calls: Count = 0
    accepted: Count = 0
    rejected: Count = 0
    error_code: ErrorCode | None = None
    provider_request_id: Identifier | None = None
    input_start: Count | None = None
    input_end: PositiveCount | None = None


class TextEntityResult(DTO):
    message_id: Identifier
    clean: TextCleanResult
    entities: list[Entity] = Field(default_factory=list)
    observations: list[ExtractedEntity] = Field(default_factory=list)
    conflicts: list[EntityConflict] = Field(default_factory=list)
    unresolved_fields: list[EntityName] = Field(default_factory=list)
    demand_type: DemandType
    rule_matches: list[RuleMatch] = Field(default_factory=list)
    layers: list[ExtractionLayerStat] = Field(min_length=1, max_length=17)
    model_coverage_complete: bool | None = None
    extraction_version: Literal["text-entity-v1"] = "text-entity-v1"
    prompt_version: Literal["grounded-entities-v1"] = "grounded-entities-v1"

    @model_validator(mode="after")
    def validate_evidence(self) -> TextEntityResult:
        if len({entity.name for entity in self.entities}) != len(self.entities):
            raise ValueError("Resolved entity slots must be unique")
        if len(set(self.unresolved_fields)) != len(self.unresolved_fields):
            raise ValueError("Unresolved fields must be unique")
        for observation in self.observations:
            if (
                observation.source.message_id != self.message_id
                or self.clean.raw_text[observation.start : observation.end]
                != observation.source.excerpt
            ):
                raise ValueError("Observation must reference the exact raw message span")
        for match in self.rule_matches:
            if (
                match.evidence.message_id != self.message_id
                or self.clean.raw_text[match.start : match.end] != match.evidence.excerpt
            ):
                raise ValueError("Rule evidence must reference the exact raw message span")
        return self
