"""两个独立会话沿原Conversation/SOP工具运行，展示历史引用不会确认当前槽位。

OfflineLongMemory只替换存储，生成/授权/选择用真正LongMemoryService；远程模型0次。
"""

import httpx

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.application.long_memory import LongMemoryService
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import Outcome, QuestionStatus
from deephelp_app.domain.models.long_memory import MemoryChange, MemoryReference, MemoryUse
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
        assert missing.memory_selection and missing.memory_selection.status == "REJECTED"
        assert missing.memory_selection.decisions[0].reason == "current_unconfirmed"
        changed = await service.change(
            first.identity,
            memory.memory_id,
            MemoryChange(
                expected_revision=1,
                action="retain",
                edit_id="course-user-retain",
                text="请先说明结果，再说明依据。",
            ),
        )
        corrected = await service.change(
            first.identity,
            memory.memory_id,
            MemoryChange(
                expected_revision=changed.revision,
                action="correct",
                edit_id="course-user-correct",
                text="请先列出依据，再给出结果。",
            ),
        )
        forgotten = await service.change(
            first.identity,
            memory.memory_id,
            MemoryChange(
                expected_revision=corrected.revision, action="forget", edit_id="course-user-forget"
            ),
        )
        # 晚到保留不复活同一来源；业务原receipt仍保留，遗忘只关闭后续检索。
        assert (
            await service.preserve(first.identity, first.session_id, old.question_id)
        ).state == "FORGOTTEN"
        forgotten_use = use.model_copy(
            update={
                "references": (
                    MemoryReference(memory_id=memory.memory_id, revision=forgotten.revision),
                )
            }
        )
        assert (await service.select(first.identity, current.session_id, forgotten_use)).decisions[
            0
        ].reason == "memory_forgotten"
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
            "correction_versions": [changed.revision, corrected.revision, forgotten.revision],
            "forget_did_not_revive": True,
            "replay_calls": replay.call_counts.model_dump(),
        }
