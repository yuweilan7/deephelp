"""Frozen synthetic sequence oracle for offline replay; not a real model substitute."""

import json
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any, cast
from uuid import uuid4

from deephelp_app.domain.models import (
    ClusterCitation,
    ClusterJudgement,
    EventCandidate,
    EventMessage,
    RequestEnvelope,
    VerifiedIdentity,
)
from deephelp_app.execution import ExecutionBudget


def sequences() -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        json.loads(
            files("deephelp_app")
            .joinpath("assets/learning/event_sequences.json")
            .read_text(encoding="utf-8")
        ),
    )


def envelope(text: str, session: str, **changes: Any) -> RequestEnvelope:
    now = datetime.now(UTC)
    return RequestEnvelope(
        identity=VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a"),
        channel="m11-demo",
        session_id=session,
        message_id=uuid4().hex,
        raw_text=text,
        request_id=uuid4().hex,
        trace_id=uuid4().hex,
        occurred_at=now,
        received_at=now,
    ).model_copy(update=changes)


class ReplayJudge:
    def __init__(self) -> None:
        self.target: str | None = None
        self.relation: str = "new_topic"
        self.calls = 0

    async def judge(
        self,
        current: EventMessage,
        candidates: tuple[EventCandidate, ...],
        messages: tuple[EventMessage, ...],
        budget: ExecutionBudget,
    ) -> ClusterJudgement:
        self.calls += 1
        if self.target is None:
            return ClusterJudgement(
                decision="new",
                target_id=None,
                confidence="high",
                relation="new_topic",
                citations=(),
            )
        old = next(m for m in messages if m.question_id == self.target)
        return ClusterJudgement.model_validate(
            dict(
                decision="attach",
                target_id=self.target,
                confidence="high",
                relation=self.relation,
                citations=(
                    ClusterCitation(
                        message_id=current.message_id, quote=current.cleaned_text[:100]
                    ),
                    ClusterCitation(message_id=old.message_id, quote=old.cleaned_text[:100]),
                ),
            )
        )
