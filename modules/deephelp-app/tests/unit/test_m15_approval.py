"""Offline authority guards and query-before-write recovery, separate from the live matrix."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from deephelp_app.adapters.approval_store import ApprovalRepository, validate_binding
from deephelp_app.adapters.synthetic_rights import (
    RightsClient,
    RightsObservation,
    SyntheticEffect,
    binding_hash,
)
from deephelp_app.application.approval import ApprovalService, effect_response
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    ApprovalCommand,
    ApprovalStatus,
    Entity,
    EntityName,
    EntitySource,
    ErrorCode,
    EvidenceRef,
    EvidenceSource,
    Fact,
    FactKind,
    IntentCode,
    NextAction,
    OperationRecord,
    OperationStatus,
    Outcome,
    Question,
    QuestionStatus,
    ResponseEnvelope,
    ResumeCommand,
    RunStatus,
    SOPPlan,
    ToolParameters,
    VerifiedIdentity,
    VersionManifest,
    tool_parameters_hash,
)


@pytest.fixture
def pair():
    now = datetime.now(UTC)
    identity = VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a")
    params = ToolParameters(order_id="DEMO-D01")
    plan = SOPPlan(
        operation_id="operation-a",
        action="simulate_discount_adjustment",
        identity=identity,
        question_id="question-a",
        question_version=1,
        parameters=params,
        parameters_hash=tool_parameters_hash(params),
        sop_version="sop-v1",
        snapshot_hash="snapshot-v1",
        evidence_ids=("read-evidence",),
    )
    versions = VersionManifest(registry="complaints-v1", sop="sop-v1", sop_snapshot="snapshot-v1")
    response = ResponseEnvelope(
        request_id="request-a",
        trace_id="trace-a",
        run_id="run-a",
        question_id="question-a",
        outcome=Outcome.PENDING_APPROVAL,
        question_status=QuestionStatus.WAITING_APPROVAL,
        run_status=RunStatus.WAITING_APPROVAL,
        reply="等待此计划明确审批。",
        next_action=NextAction.REQUEST_APPROVAL,
        sop_plan=plan,
        approval_operation_id=plan.operation_id,
        versions=versions,
        facts=[
            Fact(
                name="discount_status",
                kind=FactKind.TEXT,
                value="missing",
                evidence_ids=("read-evidence",),
            )
        ],
        evidence_refs=[
            EvidenceRef(
                evidence_id="read-evidence", source=EvidenceSource.TOOL, record_id="read-call"
            )
        ],
        tool_call_ids=("read-call",),
    )
    op = OperationRecord(
        plan=plan,
        run_id="run-a",
        session_id="session-a",
        question_version=2,
        expires_at=now + timedelta(minutes=15),
        pending_response=response,
    )
    q = Question(
        question_id="question-a",
        identity=identity,
        session_id="session-a",
        status=QuestionStatus.WAITING_APPROVAL,
        version=2,
        entities=(
            Entity(
                name=EntityName.ORDER_ID,
                value="DEMO-D01",
                source=EntitySource(message_id="message-a", excerpt="DEMO-D01"),
            ),
        ),
        member_message_ids=("message-a",),
        active_intent=IntentCode.DISCOUNT_MISSING,
        versions=versions,
        created_at=now,
        updated_at=now,
        approval_operation_id=plan.operation_id,
    )
    return op, q


@pytest.mark.parametrize(
    "change",
    [
        {"version": 3},
        {"question_id": "other"},
        {"session_id": "other"},
        {"status": QuestionStatus.ACTIVE},
        {"approval_operation_id": "other"},
        {"unresolved_fields": (EntityName.ORDER_ID,)},
        {"identity": VerifiedIdentity(tenant_id="foreign", user_id="foreign")},
    ],
)
def test_current_case_binding_rejects_drift(pair, change):
    op, q = pair
    with pytest.raises(AppError) as error:
        validate_binding(op, q.model_copy(update=change), "snapshot-v1")
    assert error.value.code == ErrorCode.VERSION_CONFLICT


def test_snapshot_and_order_change_require_replan(pair):
    op, q = pair
    validate_binding(op, q, "snapshot-v1")
    with pytest.raises(AppError):
        validate_binding(op, q, "published-snapshot-v2")
    changed = q.model_copy(
        update={"entities": (q.entities[0].model_copy(update={"value": "000031"}),)}
    )
    with pytest.raises(AppError):
        validate_binding(op, changed, "snapshot-v1")


class DecisionAuthority(ApprovalRepository):
    """Exercise repository decision guards without substituting their logic."""

    def __init__(self, op, question):
        self.op, self.question = op, question

    async def get(self, _):
        return self.op

    @asynccontextmanager
    async def locked(self, _):
        yield None, self.op, self.question, (None, None)

    async def cancel(self, cursor, op, question, status):
        self.op = op.model_copy(
            update={"approval_status": status, "status": OperationStatus.CANCELLED}
        )
        return self.op


@pytest.mark.parametrize("kind", ["reject", "revoke", "expired_decide", "expired_resume"])
async def test_sop_publication_does_not_strand_unexecuted_plan(pair, kind):
    op, q = pair
    if kind == "revoke":
        op = op.model_copy(update={"approval_status": ApprovalStatus.APPROVED})
    if kind.startswith("expired"):
        op = op.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    repo = DecisionAuthority(op, q)
    if kind == "expired_resume":
        final, token = await repo.acquire(op.plan.operation_id, "published-snapshot-v2")
        assert token is None
    else:
        final = await repo.decide(
            op.plan.identity,
            op.plan.operation_id,
            ApprovalCommand(
                session_id=op.session_id,
                run_id=op.run_id,
                expected_question_version=op.question_version,
                parameters_hash=op.plan.parameters_hash,
                sop_version=op.plan.sop_version,
                snapshot_hash=op.plan.snapshot_hash,
                decision="approve" if kind == "expired_decide" else kind,
            ),
            "published-snapshot-v2",
        )
    assert final.status == OperationStatus.CANCELLED and final.dispatch_attempts == 0
    assert (
        final.approval_status
        == {
            "reject": ApprovalStatus.REJECTED,
            "revoke": ApprovalStatus.REVOKED,
            "expired_decide": ApprovalStatus.EXPIRED,
            "expired_resume": ApprovalStatus.EXPIRED,
        }[kind]
    )


async def test_new_sop_cannot_authorize_old_plan(pair):
    op, q = pair
    repo = DecisionAuthority(op, q)
    command = ApprovalCommand(
        session_id=op.session_id,
        run_id=op.run_id,
        expected_question_version=op.question_version,
        parameters_hash=op.plan.parameters_hash,
        sop_version=op.plan.sop_version,
        snapshot_hash=op.plan.snapshot_hash,
        decision="approve",
    )
    with pytest.raises(AppError) as error:
        await repo.decide(op.plan.identity, op.plan.operation_id, command, "published-snapshot-v2")
    assert error.value.code == ErrorCode.VERSION_CONFLICT


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "other"},
        {"question_version": 3},
        {"pending_response": None},
    ],
)
def test_operation_rejects_unbound_saved_records(pair, change):
    op, _ = pair
    with pytest.raises(ValidationError):
        OperationRecord.model_validate(op.model_dump() | change)


def test_pending_status_requires_complete_plan_binding(pair):
    op, _ = pair
    for change in (
        {"run_status": RunStatus.SUCCEEDED},
        {"sop_plan": None},
        {"approval_operation_id": "foreign"},
        {"question_status": QuestionStatus.ACTIVE},
    ):
        with pytest.raises(ValidationError):
            ResponseEnvelope.model_validate(op.pending_response.model_dump() | change)


def effect(op):
    return SyntheticEffect(
        operation_id=op.plan.operation_id,
        binding_hash=binding_hash(op),
        call_id="effect-call",
        order_id=op.plan.parameters.order_id,
    )


def test_effect_reply_requires_matching_durable_evidence(pair):
    op, _ = pair
    good = effect(op)
    response = effect_response(op, good)
    assert response.outcome == Outcome.ANSWERED and response.sop_plan is None
    assert response.facts[-1].value == "applied" and response.tool_call_ids[-1] == good.call_id
    for change in (
        {"binding_hash": "different"},
        {"order_id": "different"},
        {"operation_id": "different"},
    ):
        with pytest.raises(AppError):
            effect_response(op, good.model_copy(update=change))


class OfflineAuthority:
    pool = None

    def __init__(self, op):
        self.op = op
        self.events = []

    async def get(self, _):
        return self.op

    async def scoped(self, identity, session, run, operation):
        if (identity, session, run, operation) != (
            self.op.plan.identity,
            self.op.session_id,
            self.op.run_id,
            self.op.plan.operation_id,
        ):
            raise AppError(ErrorCode.NOT_FOUND, "Unavailable", 404)
        return self.op

    async def acquire(self, *_):
        if self.op.status in {OperationStatus.SUCCEEDED, OperationStatus.CANCELLED}:
            return self.op, None
        if self.op.approval_status != ApprovalStatus.APPROVED:
            raise AppError(ErrorCode.FORBIDDEN, "Explicit approval required", 403)
        return self.op, "lease"

    async def dispatch(self, *_):
        self.events.append("dispatch")
        self.op = self.op.model_copy(update={"status": OperationStatus.IN_FLIGHT})
        return self.op

    async def query_dispatched(self, *_):
        self.events.append("query")
        return self.op

    async def unknown(self, *_):
        self.events.append("unknown")
        self.op = self.op.model_copy(update={"status": OperationStatus.UNKNOWN})

    async def complete(self, operation, token, response):
        self.events.append("commit")
        self.op = self.op.model_copy(
            update={"status": OperationStatus.SUCCEEDED, "response": response}
        )
        return self.op

    async def release(self, *_):
        self.events.append("release")


@pytest.mark.parametrize("observation", ["SUCCEEDED", "ABSENT", "unavailable"])
async def test_unknown_queries_before_any_redispatch(pair, observation):
    op, _ = pair
    repo = OfflineAuthority(
        op.model_copy(
            update={"status": OperationStatus.UNKNOWN, "approval_status": ApprovalStatus.APPROVED}
        )
    )
    calls = []

    async def call(kind, current, budget):
        calls.append(kind)
        if observation == "unavailable":
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Unavailable", 503)
        if kind == "query" and observation == "ABSENT":
            return RightsObservation(status="ABSENT")
        return RightsObservation(status="SUCCEEDED", effect=effect(current))

    service = ApprovalService(repo, SimpleNamespace(call=call), "snapshot-v1")
    service.saver = InMemorySaver()
    result = await service.resume(
        op.plan.identity,
        op.plan.operation_id,
        ResumeCommand(session_id=op.session_id, run_id=op.run_id),
        ExecutionBudget.start(30, 8, 0),
    )
    assert calls == (["query", "execute"] if observation == "ABSENT" else ["query"])
    if observation == "unavailable":
        assert result.status == OperationStatus.UNKNOWN and "commit" not in repo.events
    else:
        assert result.status == OperationStatus.SUCCEEDED
        before = list(calls)
        await service.resume(
            op.plan.identity,
            op.plan.operation_id,
            ResumeCommand(session_id=op.session_id, run_id=op.run_id),
            ExecutionBudget.start(30, 8, 0),
        )
        assert calls == before


async def test_external_cancellation_keeps_unknown_and_releases_claim(pair):
    op, _ = pair
    repo = OfflineAuthority(op.model_copy(update={"approval_status": ApprovalStatus.APPROVED}))

    entered = asyncio.Event()

    async def call(*_):
        entered.set()
        await asyncio.Event().wait()

    service = ApprovalService(repo, SimpleNamespace(call=call), "snapshot-v1")
    service.saver = InMemorySaver()
    task = asyncio.create_task(
        service.resume(
            op.plan.identity,
            op.plan.operation_id,
            ResumeCommand(session_id=op.session_id, run_id=op.run_id),
            ExecutionBudget.start(30, 8, 0),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repo.op.status == OperationStatus.UNKNOWN and repo.events[-1] == "release"


@pytest.mark.parametrize(
    "status",
    [
        ApprovalStatus.PENDING,
        ApprovalStatus.REJECTED,
        ApprovalStatus.REVOKED,
        ApprovalStatus.EXPIRED,
    ],
)
async def test_resume_without_approval_never_calls_downstream(pair, status):
    op, _ = pair
    repo = OfflineAuthority(op.model_copy(update={"approval_status": status}))
    service = ApprovalService(repo, SimpleNamespace(), "snapshot-v1")
    service.saver = InMemorySaver()
    with pytest.raises(AppError):
        await service.resume(
            op.plan.identity,
            op.plan.operation_id,
            ResumeCommand(session_id=op.session_id, run_id=op.run_id),
            ExecutionBudget.start(30, 8, 0),
        )
    assert repo.events == []


async def test_downstream_result_binding_failure_is_not_success(pair):
    op, _ = pair
    wrong = effect(op).model_copy(update={"binding_hash": "foreign"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json=RightsObservation(status="SUCCEEDED", effect=wrong).model_dump()
            )
        )
    ) as client:
        rights = RightsClient(client, 12345, b"k" * 32)
        with pytest.raises(AppError):
            await rights.call("execute", op, ExecutionBudget.start(30, 3, 0))


async def test_committed_terminal_ledger_repairs_stale_checkpoint_without_effect(pair):
    op, _ = pair
    repo = OfflineAuthority(op)
    service = ApprovalService(repo, SimpleNamespace(), "snapshot-v1")
    service.saver = InMemorySaver()
    await service.pause(op.plan.operation_id, ExecutionBudget.start(30, 8, 0))
    graph = service.graph(ExecutionBudget.start(30, 8, 0))
    assert (await graph.aget_state(service.config(op))).next
    repo.op = op.model_copy(
        update={"status": OperationStatus.CANCELLED, "approval_status": ApprovalStatus.REJECTED}
    )
    result = await service.resume(
        op.plan.identity,
        op.plan.operation_id,
        ResumeCommand(session_id=op.session_id, run_id=op.run_id),
        ExecutionBudget.start(30, 8, 0),
    )
    assert result.status == OperationStatus.CANCELLED and repo.events == []
    assert not (await graph.aget_state(service.config(op))).next
