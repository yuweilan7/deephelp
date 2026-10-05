"""Capture bounded redacted diagnostics after the authoritative commit."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from deephelp_app.adapters.observability.telemetry import trace_id as otel_trace_id
from deephelp_app.adapters.observability.trace import TraceEvent
from deephelp_app.application.debug import sanitize, scope_hash
from deephelp_app.application.sop_config import SOPDefinition
from deephelp_app.application.sop_governance import GovernedSOP
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.memory import MemoryWindow
from deephelp_app.domain.models.responses import (
    DebugSnapshot,
    ResponseEnvelope,
)
from deephelp_app.domain.models.text import TextEntityResult

from .tracking import RunTrace

if TYPE_CHECKING:
    from .orchestrator import Conversation


async def capture_debug(
    self: Conversation,
    request: RequestEnvelope,
    response: ResponseEnvelope,
    question: Question,
    text: TextEntityResult | None,
    window: MemoryWindow | None,
    definition: SOPDefinition | GovernedSOP | None,
    stats: RunTrace,
    *,
    input_question_version: int,
) -> None:
    key = getattr(self.trace, "debug_key", None)
    if not isinstance(key, bytes) or response.run_id is None:
        return
    data: dict[str, object] = {
        "input": {
            **request.model_dump(mode="json"),
            "accepted_question_version": input_question_version,
        },
        "text": text.model_dump(mode="json") if text else None,
        "intent": response.intent_decision.model_dump(mode="json")
        if response.intent_decision
        else None,
        "memory": window.model_dump(mode="json") if window else None,
        "memory_activity": {
            "fact_source": "postgresql" if self.cases else "configured_ledger",
            "read_generation": window.generation if window else None,
            "terminal_question_version": question.version,
            "outbox": "queued_by_terminal_commit" if self.cases else "not_configured",
            "projection_completion": "not_observed_by_this_request",
        },
        "event": response.event_cluster.model_dump(mode="json") if response.event_cluster else None,
        "question": question.model_dump(mode="json"),
        "versions": response.versions.model_dump(mode="json"),
        "stages": [s.model_dump(mode="json") for s in response.stages],
        "tools": stats.tools,
        "retrievals": stats.retrievals,
        "sop": definition.model_dump(mode="json") if definition else None,
        "sop_nodes": stats.sop_nodes,
        "response": response.model_dump(
            mode="json", exclude={"event_cluster", "intent_decision", "stages"}
        ),
        "budget": response.budget_used.model_dump(mode="json"),
    }
    data = sanitize(data, key)
    gaps: list[str] = []
    if getattr(self.trace, "dropped", 0):
        gaps.append("trace_events_dropped")
    if any(row.get("model_calls") is None for row in stats.retrievals):
        gaps.append("retrieval_usage_unknown")
    # Diagnostics can omit oversized sections; authoritative ledger facts are untouched.
    for field_name in (
        "memory",
        "event",
        "text",
        "input",
        "question",
        "sop",
        "retrievals",
        "tools",
        "intent",
    ):
        if len(json.dumps(data, ensure_ascii=False).encode()) <= 128 * 1024:
            break
        data[field_name] = None
        gaps.append(field_name + "_size_limit")
    if len(json.dumps(data, ensure_ascii=False).encode()) > 128 * 1024:
        data["response"] = {
            "outcome": response.outcome.value,
            "run_status": response.run_status.value if response.run_status else None,
            "fact_count": len(response.facts),
            "evidence_count": len(response.evidence_refs),
        }
        gaps.append("response_size_limit")
    try:
        await self.trace.emit(
            TraceEvent(
                event="debug_snapshot",
                request_id=request.request_id,
                trace_id=request.trace_id,
                run_id=response.run_id,
                question_id=question.question_id,
                debug=DebugSnapshot(
                    otel_trace_id=otel_trace_id(),
                    scope_hash=scope_hash(key, request.identity, request.session_id),
                    run_id=response.run_id,
                    question_id=question.question_id,
                    trace_id=request.trace_id,
                    data=data,
                    incomplete=tuple(gaps),
                ),
            )
        )
    except Exception:
        # Optional diagnostics never reverse a committed business result.
        pass
