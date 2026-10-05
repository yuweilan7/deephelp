"""Advance question entities and provenance before main-intent recognition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from deephelp_app.adapters.ledger import Receipt
from deephelp_app.application.text_entity import reconcile_conflicts
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.entities import Entity
from deephelp_app.domain.models.events import EventClusterResult
from deephelp_app.domain.models.memory import MemoryWindow
from deephelp_app.domain.models.text import TextEntityResult

from .tracking import STAGES, PreparedState, RunTrace

if TYPE_CHECKING:
    from .orchestrator import Conversation


@dataclass
class QuestionState:
    """One request owns partial preparation; Question remains an immutable domain value."""

    question: Question
    text: TextEntityResult | None
    window: MemoryWindow | None
    entities: tuple[Entity, ...]
    previous_question: Question


async def advance_question(
    conversation: Conversation,
    request: RequestEnvelope,
    receipt: Receipt,
    state: QuestionState,
    prepared: PreparedState | None,
    event: EventClusterResult | None,
    budget: ExecutionBudget,
    stats: RunTrace,
) -> None:
    if prepared:
        assert prepared.text is not None
        state.text = prepared.text
        state.previous_question = state.question
        context = event.current_event_context if event else None
        state.question = state.question.model_copy(
            update={
                "entities": context.entities if context else (),
                "conflicts": context.conflicts if context else (),
                "unresolved_fields": context.unresolved_fields if context else (),
                "event_summary": context.summary if context else request.raw_text[:2000],
                "aggregation_pending": bool(event and event.disposition == "clarify"),
                "versions": state.question.versions
                if state.question.active_intent
                else conversation.versions,
            }
        )
        state.text = state.text.model_copy(
            update={
                "entities": list(state.question.entities),
                "conflicts": list(state.question.conflicts),
                "unresolved_fields": list(state.question.unresolved_fields),
                "demand_type": event.window[-1].demand_type
                if event and event.window
                else state.text.demand_type,
            }
        )
        state.entities = tuple(
            e for e in state.question.entities if e.name not in state.question.unresolved_fields
        )
    else:
        async with conversation.stage(STAGES[2], request, receipt, budget, stats):
            state.text = await conversation.text.process(
                request,
                budget,
                confirmed=state.question.entities,
                fields=state.question.active_intent.required_slots
                if state.question.active_intent
                else None,
            )
            confirmed_names = {
                e.name
                for e in state.text.observations
                if e.disposition != "negated"
                and (
                    e.disposition == "correction"
                    or any(
                        e.name == old.name and e.value == old.value
                        for old in state.question.entities
                    )
                    or e.name not in {old.name for old in state.question.entities}
                )
            }
            unresolved = set(state.text.unresolved_fields) | (
                set(state.question.unresolved_fields) - confirmed_names
            )
            state.text = state.text.model_copy(
                update={"unresolved_fields": sorted(unresolved, key=lambda n: n.value)}
            )
        async with conversation.stage(STAGES[3], request, receipt, budget, stats):
            if conversation.memory:
                state.window = await conversation.memory.load(
                    request.identity, request.session_id, budget
                )
            state.entities = tuple(
                e for e in state.text.entities if e.name not in state.text.unresolved_fields
            )
        async with conversation.stage(STAGES[4], request, receipt, budget, stats):
            state.previous_question = state.question
            # The confirming message clears its waiting field and old conflict together;
            # retain rejected values and provenance so the SOP sees one grounded value.
            history = reconcile_conflicts(
                state.question.conflicts,
                state.text.entities,
                confirmed_names,
                state.text.unresolved_fields,
            )
            state.question = state.question.model_copy(
                update={
                    "entities": tuple(state.text.entities),
                    "conflicts": (*history, *state.text.conflicts),
                    "unresolved_fields": tuple(state.text.unresolved_fields),
                    "versions": state.question.versions
                    if state.question.active_intent
                    else conversation.versions,
                }
            )
