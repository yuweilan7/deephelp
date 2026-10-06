"""两个独立会话沿原Conversation/SOP工具运行，展示历史引用不会确认当前槽位。

OfflineLongMemory只替换存储，生成/授权/选择用真正LongMemoryService；远程模型0次。
"""

import httpx

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.application.long_memory import LongMemoryService
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import Outcome, QuestionStatus
from deephelp_app.domain.models.long_memory import MemoryReference, MemoryUse
from deephelp_tools.learning.fakes.cases_fake import MemoryCaseRepository
from deephelp_tools.learning.fakes.long_memory import OfflineLongMemory
from deephelp_tools.learning.replay.event_replay import ReplayJudge, envelope
from deephelp_tools.learning.replay.mvp_replay import ReplayAssembly


async def long_memory_demo() -> dict[str, object]:
    cases = MemoryCaseRepository()
    service = LongMemoryService(OfflineLongMemory(cases))
    assembly = ReplayAssembly(ledger=cases, event_judge=ReplayJudge())
    async with httpx.AsyncClient() as client, assembly.open(client, MemoryTrace()) as conversation:
        conversation.long_memory = service
        first = envelope("订单000031未享受优惠", "course-history-source")
        old = await conversation.run(first, ExecutionBudget.start(20, 40, 0))
        assert old.question_id and old.outcome == Outcome.ANSWERED
        memory = await service.preserve(first.identity, first.session_id, old.question_id)
        scope = await service.configure(
            first.identity, "course-history-target", 0, True, (first.session_id,)
        )
        use = MemoryUse(
            scope_revision=scope.revision,
            references=(
                MemoryReference(
                    memory_id=memory.memory_id,
                    revision=memory.revision,
                ),
            ),
        )
        current = envelope("订单000031未享受优惠", "course-history-target").model_copy(
            update={"history_memory": use},
        )
        answer = await conversation.run(current, ExecutionBudget.start(20, 40, 0))
        assert answer.outcome == Outcome.ANSWERED and answer.question_id != old.question_id
        assert answer.memory_selection and answer.memory_selection.status == "SELECTED"
        assert "99.90" in answer.reply and "历史参考" in answer.reply
        missing = await conversation.run(
            envelope("我的订单未享受优惠", "course-history-target").model_copy(
                update={"history_memory": use}
            ),
            ExecutionBudget.start(20, 40, 0),
        )
        assert missing.missing_slots and not missing.tool_call_ids
        assert cases.questions[old.question_id].status == QuestionStatus.RESOLVED
        replay = await conversation.run(current, ExecutionBudget.start(20, 40, 0))
        assert replay.replayed and sum(replay.call_counts.model_dump().values()) == 0
        return {
            "evidence_kind": "offline_original_conversation",
            "remote_model_calls": 0,
            "selection": answer.memory_selection.model_dump(mode="json"),
            "current_reply": answer.reply,
            "history_did_not_fill_slots": bool(missing.missing_slots),
            "source_stayed_ended": True,
            "replay_calls": replay.call_counts.model_dump(),
        }
