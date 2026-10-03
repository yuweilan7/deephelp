"""Explicit offline case adapter. Tests of this adapter do not prove MySQL durability."""

from datetime import UTC, datetime, timedelta

from deephelp_app.cases import CLOSED, OPEN, bounded_history, summary, validate_transition
from deephelp_app.domain.models import (
    ErrorCode,
    LifecycleCommand,
    MemoryMessage,
    MemoryWindow,
    Question,
    RequestEnvelope,
    ResponseEnvelope,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError
from deephelp_app.ledger import MemoryLedger, Receipt, message_key, new_receipt, payload_hash


class MemoryCaseRepository(MemoryLedger):
    supports_continuation = True

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[tuple[RequestEnvelope, str]] = []
        self.generations: dict[tuple[str, str, str], int] = {}
        self.events: list[Question] = []

    def record(self, question: Question) -> None:
        key = (question.identity.tenant_id, question.identity.user_id, question.session_id)
        self.generations[key] = self.generations.get(key, 0) + 1
        self.events.append(question)

    def busy(self, qid: str) -> bool:
        return any(
            receipt.question.question_id == qid and receipt.response is None
            for _, receipt in self.rows.values()
        )

    async def accept(
        self, request: RequestEnvelope, *, attribution: tuple[str, int] | None = None
    ) -> Receipt:
        key = message_key(request)
        if key in self.rows:
            return await super().accept(request)
        fresh = new_receipt(request)
        if attribution and request.question_hint not in {None, attribution[0]}:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Explicit hint conflicts with attribution")
        if attribution and request.expected_question_version not in {None, attribution[1]}:
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Explicit version conflicts with attribution"
            )
        hint = attribution[0] if attribution else request.question_hint
        expected = attribution[1] if attribution else request.expected_question_version
        if hint:
            q = await self.question(request.identity, request.session_id, hint)
            if q is None:
                raise AppError(ErrorCode.FORBIDDEN, "Object access denied", 403)
            if q.status not in OPEN:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "Closed question requires explicit reopen", 409
                )
            if (expected is not None and expected != q.version) or self.busy(q.question_id):
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Question changed or has unfinished run", 409
                )
            latest = max(r.occurred_at for r, qid in self.messages if qid == q.question_id)
            if request.occurred_at < latest:
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Out-of-order continuation requires review", 409
                )
            q = Question.model_validate(
                q.model_copy(
                    update={
                        "version": q.version + 1,
                        "updated_at": datetime.now(UTC),
                        "member_message_ids": (*q.member_message_ids, request.message_id),
                    }
                ).model_dump()
            )
            fresh = Receipt(True, fresh.run_id, q)
        self.rows[key] = (payload_hash(request), fresh)
        self.questions[fresh.question.question_id] = fresh.question
        self.messages.append((request, fresh.question.question_id))
        self.record(fresh.question)
        return fresh

    async def finish(
        self, receipt: Receipt, question: Question, response: ResponseEnvelope
    ) -> None:
        if self.questions[question.question_id].version != receipt.question.version:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Question version changed")
        await super().finish(receipt, question, response)
        self.record(question)

    async def transition(
        self, identity: VerifiedIdentity, qid: str, command: LifecycleCommand
    ) -> Question:
        q = await self.question(identity, command.session_id, qid)
        if q is None:
            raise AppError(ErrorCode.FORBIDDEN, "Object access denied", 403)
        validate_transition(q, command)
        if self.busy(qid):
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Unfinished run cannot be resumed or closed", 409
            )
        q = Question.model_validate(
            q.model_copy(
                update={
                    "version": q.version + 1,
                    "status": command.target,
                    "updated_at": datetime.now(UTC),
                }
            ).model_dump()
        )
        self.questions[qid] = q
        self.record(q)
        return q

    async def snapshot(
        self,
        identity: VerifiedIdentity,
        session: str,
        *,
        count: int = 30,
        token_limit: int = 8192,
        age_seconds: int = 86400,
        case_limit: int = 32,
    ) -> MemoryWindow:
        if min(count, token_limit, age_seconds, case_limit) <= 0:
            raise ValueError("Positive memory bounds required")
        cases = [
            q for q in self.questions.values() if q.identity == identity and q.session_id == session
        ]
        active = sorted(
            (q for q in cases if q.status in OPEN), key=lambda q: q.updated_at, reverse=True
        )
        closed = sorted(
            (q for q in cases if q.status in CLOSED), key=lambda q: q.version, reverse=True
        )
        cutoff = datetime.now(UTC) - timedelta(seconds=age_seconds)
        rows = sorted(
            (
                MemoryMessage(
                    channel=r.channel,
                    message_id=r.message_id,
                    question_id=qid,
                    occurred_at=r.occurred_at,
                    text=r.raw_text,
                )
                for r, qid in self.messages
                if r.identity == identity and r.session_id == session and r.occurred_at >= cutoff
            ),
            key=lambda r: (r.occurred_at, r.channel, r.message_id),
            reverse=True,
        )
        history, trimmed = bounded_history(rows, count=count, token_limit=token_limit)
        return MemoryWindow(
            history=history,
            active_questions=tuple(active[:case_limit]),
            closed_summaries=tuple(summary(q) for q in closed[:case_limit]),
            generation=self.generations.get((identity.tenant_id, identity.user_id, session), 0),
            trimmed=trimmed or len(active) > case_limit or len(closed) > case_limit,
        )
