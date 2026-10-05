"""显式原PG子任务协议检查：固定建议/真实stdio，远程模型0次。

只创建本探针唯一合成消息，不覆盖旧业务/审批；原run表与任务表用同一个池。
连接重开证明完成/未完成状态持久；领取竞争/归属/CAS/取消分别独立断言。
失败保留原账本，不通过清库、重发未知任务或内存模拟换通过。
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import cast
from uuid import uuid4

from deephelp_app.adapters.persistence.agent_tasks import PostgresAgentTasks
from deephelp_app.adapters.persistence.ledger import PostgresLedger
from deephelp_app.adapters.persistence.postgres import PostgresConfig, PostgresPool
from deephelp_app.application.collaboration import load_agent_policy
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import ChatRequest, ChatResult
from deephelp_tools.demo.assembly import DemoToolGateway
from deephelp_tools.learning.course.collaboration import RecordedAgentModel
from deephelp_tools.probes.conversation.sop_probe import sop_question


async def check(config: Path, report: dict[str, object]) -> None:
    pool = await PostgresPool.open(PostgresConfig.read(config))
    try:
        ledger, journal = PostgresLedger(pool), PostgresAgentTasks(pool)
        await ledger.migrate()
        await journal.migrate()
        model, gateway = RecordedAgentModel(), DemoToolGateway()
        executor = SOPExecutor(
            model,
            gateway,
            registry=bundled_registry(),
            agent_policy=load_agent_policy(),
            agent_journal=journal,
        )
        budget = ExecutionBudget.start(60, 30, 0)
        question, context = sop_question(
            budget, IntentCode.COUPON_UNUSABLE, order="DEMO-C01", coupon="COUPON-C01"
        )
        context = context.model_copy(
            update={"execution_mode": "collaboration", "message_id": "m40-pg-" + uuid4().hex}
        )
        # 槽位来源来自本消息；父问题只使用PG实际领取的qid/version。
        question = question.model_copy(
            update={
                "entities": tuple(
                    e.model_copy(
                        update={
                            "source": e.source.model_copy(update={"message_id": context.message_id})
                        }
                    )
                    for e in question.entities
                )
            }
        )
        claim = await ledger.accept(context)
        question = question.model_copy(
            update={
                "question_id": claim.question.question_id,
                "version": claim.question.version,
                "versions": executor.pin_versions(question.versions, IntentCode.COUPON_UNUSABLE),
            }
        )
        async with gateway.open():
            result = await executor.execute(question, budget, context=context, run_id=claim.run_id)
            assert result.status == "RESOLVED" and len(result.agent_receipts) == 2
            before = budget.usage()
            repeated = await executor.execute(
                question, budget, context=context, run_id=claim.run_id
            )
            assert repeated.agent_receipts == result.agent_receipts and budget.usage() == before
            assert len(await gateway.ledger()) == 2 and len(model.requests) == 6
        # 工具观察已提交、回收等待时取消：PG必须保留观察但不声称已回收成功。
        entered = asyncio.Event()

        class WaitReturn(RecordedAgentModel):
            async def chat(
                self, request: ChatRequest, shared_budget: ExecutionBudget
            ) -> ChatResult:
                if request.tools[0].name == "return_evidence":
                    entered.set()
                    await asyncio.Event().wait()
                return await super().chat(request, shared_budget)

        cancel_context = context.model_copy(update={"message_id": "m40-cancel-" + uuid4().hex})
        cancel_claim = await ledger.accept(cancel_context)
        cancel_question = question.model_copy(
            update={
                "question_id": cancel_claim.question.question_id,
                "member_message_ids": (cancel_context.message_id,),
                "entities": tuple(
                    e.model_copy(
                        update={
                            "source": e.source.model_copy(
                                update={"message_id": cancel_context.message_id}
                            )
                        }
                    )
                    for e in question.entities
                ),
            }
        )
        cancel_gateway = DemoToolGateway()
        cancel_executor = SOPExecutor(
            WaitReturn(),
            cancel_gateway,
            registry=bundled_registry(),
            agent_policy=load_agent_policy(),
            agent_journal=journal,
        )
        async with cancel_gateway.open():
            owned = asyncio.create_task(
                cancel_executor.execute(
                    cancel_question, budget, context=cancel_context, run_id=cancel_claim.run_id
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=15)
            observed = await journal.records(
                context.identity, context.session_id, cancel_claim.run_id
            )
            assert len(observed) == 1 and observed[0].state == "OBSERVED"
            owned.cancel()
            try:
                await owned
                raise AssertionError("Cancellation swallowed")
            except asyncio.CancelledError:
                pass
            cancelled = await journal.records(
                context.identity, context.session_id, cancel_claim.run_id
            )
            assert (
                cancelled[0].state == "CANCELLED"
                and cancelled[0].observation == observed[0].observation
            )
            assert cancelled[0].receipt is not None and not cancelled[0].receipt.evidence_ids
            no_retry = await cancel_executor.execute(
                cancel_question, budget, context=cancel_context, run_id=cancel_claim.run_id
            )
            assert no_retry.error is not None and no_retry.error.code == ErrorCode.OPERATION_UNKNOWN
            assert len(await cancel_gateway.ledger()) == 1
        rows = await journal.records(context.identity, context.session_id, claim.run_id)
        assert len(rows) == 2 and all(r.state == "COMPLETED" for r in rows)
        assert not await journal.records(
            VerifiedIdentity(tenant_id="other", user_id="other"), context.session_id, claim.run_id
        )
        for candidate in [rows[0], rows[0].model_copy(update={"claim_token": "forged"})]:
            try:
                await journal.write(candidate)
                raise AssertionError("Terminal/forged CAS accepted")
            except AppError as exc:
                assert exc.code == ErrorCode.VERSION_CONFLICT
        # 新消息只用于领取竞争：一个事务成功，其余保持UNKNOWN而不重领。
        next_context = context.model_copy(update={"message_id": "m40-race-" + uuid4().hex})
        next_claim = await ledger.accept(next_context)
        source_task = rows[0].task
        import hashlib

        task_id = (
            "agent-"
            + hashlib.sha256(f"{next_claim.run_id}:{source_task.node_id}".encode()).hexdigest()[:32]
        )
        task = source_task.model_copy(
            update={
                "task_id": task_id,
                "parent_run_id": next_claim.run_id,
                "budget_ref": next_claim.run_id,
                "parent_question_id": next_claim.question.question_id,
            }
        )
        winners = await asyncio.gather(
            journal.begin(task, next_context, max_tasks=2),
            journal.begin(task, next_context, max_tasks=2),
            return_exceptions=True,
        )
        assert sum(not isinstance(r, BaseException) for r in winners) == 1
        assert (
            sum(isinstance(r, AppError) and r.code == ErrorCode.OPERATION_UNKNOWN for r in winners)
            == 1
        )
        try:
            await journal.begin(
                task.model_copy(
                    update={"identity": VerifiedIdentity(tenant_id="other", user_id="other")}
                ),
                next_context,
                max_tasks=2,
            )
            raise AssertionError("Foreign task owner accepted")
        except AppError as foreign_error:
            assert foreign_error.code == ErrorCode.FORBIDDEN
        report.update(
            run_id=claim.run_id,
            race_run_id=next_claim.run_id,
            original_receipts=[r.model_dump(mode="json", exclude={"claim_token"}) for r in rows],
            checks=[
                "two-source",
                "completed-readback-zero-calls",
                "terminal-CAS",
                "foreign-ownership",
                "single-claim-race",
                "OBSERVED-cancel-durable-no-retry",
            ],
        )
    finally:
        await pool.aclose()
    # 关闭所有原连接后用新池读；不能把进程内对象当持久证据。
    pool = await PostgresPool.open(PostgresConfig.read(config))
    try:
        journal = PostgresAgentTasks(pool)
        restored = await journal.records(context.identity, context.session_id, claim.run_id)
        assert restored == rows
        running = await journal.records(context.identity, context.session_id, next_claim.run_id)
        assert len(running) == 1 and running[0].state == "RUNNING"
        try:
            await journal.begin(task, next_context, max_tasks=2)
            raise AssertionError("RUNNING re-claimed after restart")
        except AppError as running_error:
            assert running_error.code == ErrorCode.OPERATION_UNKNOWN
        report["checks"] = [
            *cast(list[str], report["checks"]),
            "new-pool-persistence",
            "RUNNING-no-reclaim",
        ]
        report["status"] = "PASS"
    finally:
        await pool.aclose()


async def probe(config: Path, output: Path) -> int:
    if await asyncio.to_thread(output.exists):
        raise ValueError("Use a new immutable report path")
    report: dict[str, object] = {"status": "FAIL", "remote_model_calls": 0}
    try:
        await check(config, report)
    except (AppError, AssertionError) as exc:
        report["error"] = exc.code.value if isinstance(exc, AppError) else "protocol_assertion"
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            output.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({"status": report["status"], "output": str(output)}))
    return 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", required=True, action="store_true")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return asyncio.run(probe(args.config, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
