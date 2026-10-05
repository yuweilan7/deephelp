"""固定优惠/补槽/拒识/重投；实际执行原13段Conversation与stdio工具。"""

import httpx

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.application.cascade import CascadePolicy
from deephelp_app.application.conversation.tracking import STAGES
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import Outcome, QuestionStatus
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_tools.learning.fakes.cases_fake import MemoryCaseRepository
from deephelp_tools.learning.replay.event_replay import ReplayJudge, envelope
from deephelp_tools.learning.replay.mvp_replay import ReplayAssembly


def summary(response: ResponseEnvelope) -> dict[str, object]:
    return {
        "outcome": response.outcome.value,
        "reply": response.reply,
        "run_id": response.run_id,
        "question_id": response.question_id,
        "missing_slots": [s.value for s in response.missing_slots],
        "counts": response.call_counts.model_dump(),
        "stages": [{"stage": s.stage, "status": s.status} for s in response.stages],
        "facts": [f.model_dump(mode="json") for f in response.facts],
        "evidence": [e.model_dump(mode="json") for e in response.evidence_refs],
        "replayed": response.replayed,
    }


async def request_demo() -> dict[str, object]:
    repo, judge, trace = MemoryCaseRepository(), ReplayJudge(), MemoryTrace()
    assembly = ReplayAssembly(ledger=repo, event_judge=judge, policy=CascadePolicy())
    # client仅满足原装配签名，不发HTTP；stdio子进程由assembly.open拥有并关闭。
    async with httpx.AsyncClient() as client, assembly.open(client, trace) as conversation:
        missing = await conversation.run(
            envelope("我的订单未享受优惠", "course-request"), ExecutionBudget.start(20, 40, 0)
        )
        assert missing.question_status == QuestionStatus.WAITING_SLOT
        assert missing.call_counts.tool_calls == 0
        judge.target, judge.relation = missing.question_id, "supplement"
        request = envelope("补充订单000031", "course-request")
        answered = await conversation.run(request, ExecutionBudget.start(20, 40, 0))
        assert answered.question_id == missing.question_id
        assert answered.outcome == Outcome.ANSWERED
        assert "99.90" in answered.reply and "10.00" in answered.reply
        assert tuple(s.stage for s in answered.stages) == STAGES
        assert answered.call_counts.intent_calls == 1
        ledger_size = len(await assembly.tools.ledger())
        replay = await conversation.run(request, ExecutionBudget.start(20, 40, 0))
        assert replay.replayed and replay.run_id == answered.run_id
        assert sum(replay.call_counts.model_dump().values()) == 0
        assert len(await assembly.tools.ledger()) == ledger_size
        judge.target = None
        unknown = await conversation.run(
            envelope("我的包裹物流一直没更新", "course-unknown"), ExecutionBudget.start(20, 40, 0)
        )
        assert unknown.outcome == Outcome.HANDOFF and not unknown.tool_call_ids
        assert len(await assembly.tools.ledger()) == ledger_size
        return {
            "evidence_kind": "synthetic_mechanism_replay",
            "remote_model_calls": 0,
            "missing": summary(missing),
            "answered": summary(answered),
            "replay": summary(replay),
            "unknown": summary(unknown),
        }
