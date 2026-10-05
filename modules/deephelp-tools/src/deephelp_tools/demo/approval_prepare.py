"""Fixed synthetic preparation shared by learning UI and recovery acceptance."""

from datetime import UTC, datetime
from uuid import uuid4

from deephelp_app.adapters.approval_store import ApprovalRepository
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.domain.checks import response_from_sop
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.approval import OperationRecord
from deephelp_app.domain.models.common import Outcome, QuestionStatus, RunStatus
from deephelp_app.domain.models.entities import Entity, EntityName, EntitySource
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import ModelToolCall
from deephelp_app.domain.models.release import VersionManifest
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_tools.demo.assembly import DemoToolGateway as ToolGateway
from deephelp_tools.demo.scenarios import proposal_registry
from deephelp_tools.learning.event_replay import envelope
from deephelp_tools.learning.sop_replay import ReplayModel


async def prepare(repo: ApprovalRepository, session: str) -> OperationRecord:
    request = envelope("订单 DEMO-D01 优惠未到账，请查询", session)
    receipt = await repo.accept(request)
    registry = proposal_registry()
    q = receipt.question.model_copy(
        update={
            "active_intent": IntentCode.DISCOUNT_MISSING,
            "versions": registry.pin(
                VersionManifest(registry="complaints-v1"), IntentCode.DISCOUNT_MISSING
            ),
            "entities": (
                Entity(
                    name=EntityName.ORDER_ID,
                    value="DEMO-D01",
                    source=EntitySource(message_id=request.message_id, excerpt="DEMO-D01"),
                ),
            ),
        }
    )
    budget = ExecutionBudget.start(60, 12, 0)
    model = ReplayModel(
        [
            ModelToolCall(
                call_id=uuid4().hex, name="get_order_benefits", arguments={"order_id": "DEMO-D01"}
            )
        ]
    )
    gateway = ToolGateway()
    async with gateway.open():
        result = await SOPExecutor(model, gateway, registry=registry).execute(
            q,
            budget,
            context=request,
            run_id=receipt.run_id,
        )
        ledger = await gateway.ledger()
    assert result.plan and ledger[0].status == "succeeded"
    response = response_from_sop(
        request,
        run_id=receipt.run_id,
        question_id=q.question_id,
        result=result,
        reply="合成预检等待明确审批。",
        versions=q.versions,
        budget_used=budget.usage(),
    ).model_copy(
        update={
            "outcome": Outcome.PENDING_APPROVAL,
            "question_status": QuestionStatus.WAITING_APPROVAL,
            "run_status": RunStatus.WAITING_APPROVAL,
            "approval_operation_id": result.plan.operation_id,
        }
    )
    q = q.model_copy(
        update={
            "status": QuestionStatus.WAITING_APPROVAL,
            "version": q.version + 1,
            "updated_at": datetime.now(UTC),
            "approval_operation_id": result.plan.operation_id,
        }
    )
    await repo.finish(receipt, q, ResponseEnvelope.model_validate(response.model_dump()))
    return await repo.get(result.plan.operation_id)
