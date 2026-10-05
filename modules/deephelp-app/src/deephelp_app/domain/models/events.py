"""消息归属的候选、引用、关系边与结果；不包含600最终分类。

attach必须指向目标且关系为同诉求/补充/更正；new和clarify不能偷偷带目标。
类型只约束结构，引用原文及目标归属仍由aggregation和账本复验。
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    Field,
    model_validator,
)

from .common import DTO, Count, Identifier, PositiveCount, RawText
from .entities import Entity, EntityConflict, EntityName
from .intents import IntentCode
from .text import DemandType


class EventMessage(DTO):
    node_id: Identifier
    message_id: Identifier
    channel: Identifier
    question_id: Identifier | None = None
    cleaned_text: RawText
    entities: tuple[Entity, ...] = ()
    demand_type: DemandType
    start: Count = 0
    end: PositiveCount


class EventCandidate(DTO):
    question_id: Identifier
    version: PositiveCount
    message_ids: tuple[Identifier, ...]
    text: str = Field(max_length=4000)
    entities: tuple[Entity, ...] = ()
    raw_score: float | None = Field(default=None, allow_inf_nan=False)
    score_kind: Literal["cosine"] | None = None
    entity_matches: tuple[EntityName, ...] = ()
    excluded_reason: str | None = None
    active_intent: IntentCode | None = None


class ClusterCitation(DTO):
    message_id: Identifier
    quote: str = Field(min_length=1, max_length=512)


class ClusterJudgement(DTO):
    decision: Literal["attach", "new", "uncertain"]
    target_id: Identifier | None
    confidence: Literal["high", "low"]
    relation: Literal["same_complaint", "supplement", "correction", "new_topic", "ambiguous"]
    citations: tuple[ClusterCitation, ...] = Field(max_length=6)

    @model_validator(mode="after")
    def coherent_relation(self) -> ClusterJudgement:
        if self.decision == "attach":
            if self.target_id is None or self.relation not in {
                "same_complaint",
                "supplement",
                "correction",
            }:
                raise ValueError("Attachment requires a target and membership relation")
        elif self.target_id is not None or self.relation != (
            "new_topic" if self.decision == "new" else "ambiguous"
        ):
            raise ValueError("Non-attachment decision and relation must agree")
        return self


class EventContext(DTO):
    event_id: Identifier
    question_id: Identifier | None = None
    message_ids: tuple[Identifier, ...]
    node_ids: tuple[Identifier, ...]
    summary: str = Field(max_length=4000)
    entities: tuple[Entity, ...] = ()
    conflicts: tuple[EntityConflict, ...] = ()
    unresolved_fields: tuple[EntityName, ...] = ()


class ClusterEdge(DTO):
    left: Identifier
    right: Identifier
    accepted: bool
    reason: Identifier


class EventClusterResult(DTO):
    disposition: Literal["new", "attached", "clarify"]
    target_question_id: Identifier | None = None
    expected_version: PositiveCount | None = None
    current_event_context: EventContext | None = None
    events: tuple[EventContext, ...]
    window: tuple[EventMessage, ...]
    candidates: tuple[EventCandidate, ...] = ()
    judgement: ClusterJudgement | None = None
    edges: tuple[ClusterEdge, ...] = ()
    diagnostics: tuple[str, ...] = ()
    graph_text: str = Field(max_length=24000)
    generation: Count = 0
    persisted: bool = False
    policy_version: Literal["event-cluster-v1"] = "event-cluster-v1"
