"""明确live时验证原PG历史与新会话的当前HTTP MCP事实；保存精确输入/回执。

source_report是已结束原内容证据，来源仍回PG，不把报告文字当权威。
预算writer由本次进程拥有，依赖/取消finally退出；重新读取报告不执行模型。
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.adapters.mcp.shared.network_mcp import HTTPMCPConfig
from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.adapters.persistence.cases import PostgresCaseRepository
from deephelp_app.adapters.persistence.long_memory import PostgresLongMemory
from deephelp_app.application.dense import atomic_json
from deephelp_app.application.long_memory import LongMemoryService
from deephelp_app.bootstrap.runtime.assembly import LiveAssembly
from deephelp_app.bootstrap.runtime.budget import BudgetSession
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import Outcome, QuestionStatus, VerifiedIdentity
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.long_memory import MemoryChange, MemoryReference, MemoryUse


def assembly_from_config(config: dict[str, Any]) -> LiveAssembly:
    """所有记忆实测共用原装配；可选审核SOP/Skill沿现有批准原件装配。"""
    runtime = LiveAssembly(
        Path.cwd(),
        Path(config["providers"]),
        Path(config["pointer"]),
        business_mcp=HTTPMCPConfig(
            url=config["business_url"], credentials_path=Path(config["business_credentials"])
        ),
        corpus_path=Path(config["corpus"]),
        cascade_policy=Path(config["cascade"]),
        reply_polish=True,
        sop_directory=Path(config["sop_directory"]),
        reviewed_rules=Path(config["rules"]) if config.get("rules") else None,
        skill_review=Path(config["skill_review"]) if config.get("skill_review") else None,
    )
    if config.get("sop_review"):
        from deephelp_app.adapters.releases.sop_drafts import read_sop_review

        reviewed, _ = read_sop_review(Path(config["sop_review"]))
        runtime.sop_registry = reviewed
        runtime.sop_snapshots = (reviewed, *runtime.sop_snapshots)
    return runtime


async def run(config: dict[str, Any], output: Path) -> None:
    if await asyncio.to_thread(output.exists):
        raise ValueError("Use a new immutable content report path")
    from datetime import UTC, datetime

    source = json.loads(
        await asyncio.to_thread(
            Path(config["source_report"]).read_text,
            encoding="utf-8",
        )
    )
    owner = VerifiedIdentity.model_validate(config["source_owner"])
    gate = BudgetSession(Path(config["budget"]))
    gate.open()
    report: dict[str, Any] = {"status": "PENDING", "source_report": config["source_report"]}
    try:
        assembly = assembly_from_config(config)
        async with (
            httpx.AsyncClient(trust_env=False, timeout=90) as client,
            assembly.open(client, MemoryTrace()) as conversation,
        ):
            assert conversation.long_memory is not None and assembly.ledger is not None
            service = conversation.long_memory
            memory = await service.preserve(
                owner, source["input"]["session_id"], source["response"]["question_id"]
            )
            target = "m46-" + uuid4().hex
            scope = await service.configure(owner, target, 0, True, (memory.source_session,))
            use = MemoryUse(
                scope_revision=scope.revision,
                references=(MemoryReference(memory_id=memory.memory_id, revision=memory.revision),),
            )
            now = datetime.now(UTC)
            request = RequestEnvelope(
                identity=owner,
                channel="m46-probe",
                session_id=target,
                message_id=uuid4().hex,
                raw_text=source["input"]["raw_text"],
                occurred_at=now,
                received_at=now,
                request_id=uuid4().hex,
                trace_id=uuid4().hex,
                history_memory=use,
            )
            report.update(
                input=request.model_dump(mode="json"), memory=memory.model_dump(mode="json")
            )
            atomic_json(output, report)
            result = await conversation.run(request, gate.request())
            report["response"] = result.model_dump(mode="json")
            atomic_json(output, report)
            assert (
                result.outcome == Outcome.ANSWERED and result.tool_call_ids and result.evidence_refs
            )
            assert result.memory_selection and result.memory_selection.status == "SELECTED"
            assert result.question_id != memory.source_question and "历史参考" in result.reply
            # 当前事实需由本次工具证据支撑；历史引用不混入facts/evidence_refs。
            assert set(memory.evidence_ids).isdisjoint(e.evidence_id for e in result.evidence_refs)
            old = await assembly.ledger.question(
                owner, memory.source_session, memory.source_question
            )
            assert (
                old
                and old.status == QuestionStatus.RESOLVED
                and old.version == memory.source_version
            )
            before = dict(gate.state)
            replay = await conversation.run(request, gate.request())
            assert replay.replayed and sum(replay.call_counts.model_dump().values()) == 0
            assert gate.state == before
            report["replay"] = {
                "run_id": replay.run_id,
                "counts": replay.call_counts.model_dump(),
                "budget_unchanged": True,
            }
            report["candidates"] = await service.candidates(owner, target)
            foreign = owner.model_copy(update={"user_id": "m46-foreign"})
            assert await service.store.get(foreign, memory.memory_id) is None
            try:
                await service.preserve(foreign, memory.source_session, memory.source_question)
            except AppError as exc:
                assert exc.status_code == 404
            else:
                raise AssertionError("Foreign source accepted")
            # 两个相同版本修改的真实PG短事务只有一个胜者；测试目标是本次独有scope。
            outcomes = await asyncio.gather(
                *(
                    service.configure(owner, target, scope.revision, True, (memory.source_session,))
                    for _ in range(2)
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(r, AppError) for r in outcomes) == 1
            assert (await service.select(owner, target, use)).decisions[0].reason == "scope_changed"
            report["pg_checks"] = {
                "foreign_owner_denied": True,
                "scope_cas_one_winner": True,
                "stale_scope_denied": True,
            }
            report["diagnostics"] = assembly.gateway.diagnostics
            report["judge_diagnostics"] = assembly.judge_gateway.diagnostics
            report["status"] = "PASS"
    except BaseException as exc:
        report["status"] = "FAIL"
        report["error"] = type(exc).__name__
        raise
    finally:
        report["budget"] = dict(gate.state)
        atomic_json(output, report)
        gate.close()


async def read(
    report_path: Path,
    output: Path,
    *,
    protocol: bool = False,
    lifecycle: bool = False,
    check_memory: str | None = None,
) -> None:
    """新进程直接回读原PGscope/source/receipt；不打开模型、MCP或预算writer。"""
    original = json.loads(await asyncio.to_thread(report_path.read_text, encoding="utf-8"))
    request = RequestEnvelope.model_validate(original["input"])
    repository = await PostgresCaseRepository.open(Path.cwd())
    try:
        service = LongMemoryService(PostgresLongMemory(repository.pool))
        receipt = await repository.lookup(request)
        assert receipt and receipt.response
        assert receipt.response.run_id == original["response"]["run_id"]
        source = await service.store.source(
            request.identity,
            original["memory"]["source_session"],
            original["memory"]["source_question"],
        )
        assert source and source[0].version == original["memory"]["source_version"]
        scope = await service.store.scope(request.identity, request.session_id)
        checks: dict[str, Any] = {}
        if check_memory:
            head = await service.store.get(request.identity, check_memory)
            assert head and head.state == "FORGOTTEN" and head.text == "[forgotten]"
            assert head.revision == 4
            assert len(await service.store.versions(request.identity, check_memory)) == 4
            checks.update(restarted_head_forgotten=True, revision=4)
        if lifecycle:
            # 本次来源是F1新问题，不能改原M44摘要。所有动作只写派生记忆/范围，模型0。
            before = receipt.response.model_dump_json()
            assert receipt.response.question_id
            memory = await service.preserve(
                request.identity, request.session_id, receipt.response.question_id
            )
            target = "m46-lifecycle-" + uuid4().hex
            await service.configure(request.identity, target, 0, True, (request.session_id,))
            note = await service.change(
                request.identity,
                memory.memory_id,
                MemoryChange(
                    expected_revision=memory.revision,
                    action="retain",
                    edit_id=uuid4().hex,
                    text="请先列依据，再给结果。",
                ),
            )
            correction_results = await asyncio.gather(
                *(
                    service.change(
                        request.identity,
                        memory.memory_id,
                        MemoryChange(
                            expected_revision=note.revision,
                            action="correct",
                            edit_id=uuid4().hex,
                            text="请先给结果，再列依据。",
                        ),
                    )
                    for _ in range(2)
                ),
                return_exceptions=True,
            )
            assert (
                sum(isinstance(r, AppError) and r.status_code == 409 for r in correction_results)
                == 1
            )
            current = await service.store.get(request.identity, memory.memory_id)
            assert current and current.revision == note.revision + 1
            forgotten = await service.change(
                request.identity,
                memory.memory_id,
                MemoryChange(
                    expected_revision=current.revision, action="forget", edit_id=uuid4().hex
                ),
            )
            assert (
                await service.preserve(
                    request.identity, request.session_id, receipt.response.question_id
                )
            ).state == "FORGOTTEN"
            use = MemoryUse(
                scope_revision=1,
                references=(
                    MemoryReference(memory_id=memory.memory_id, revision=forgotten.revision),
                ),
            )
            assert (await service.select(request.identity, target, use)).decisions[
                0
            ].reason == "memory_forgotten"
            detail = await service.detail(request.identity, memory.memory_id)
            assert "请先给结果" not in str(detail)
            reloaded = await repository.lookup(request)
            assert reloaded and reloaded.response and reloaded.response.model_dump_json() == before
            checks.update(
                correction_cas_one_winner=True,
                forget_no_revive=True,
                original_receipt_unchanged=True,
            )
            checks["memory_id"] = memory.memory_id
        if protocol:
            foreign = request.identity.model_copy(update={"user_id": "m46-foreign"})
            assert await service.store.get(foreign, original["memory"]["memory_id"]) is None
            assert (
                await service.store.source(
                    foreign,
                    original["memory"]["source_session"],
                    original["memory"]["source_question"],
                )
                is None
            )
            results = await asyncio.gather(
                *(
                    service.configure(
                        request.identity,
                        request.session_id,
                        scope.revision,
                        True,
                        scope.source_sessions,
                    )
                    for _ in range(2)
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(r, AppError) and r.status_code == 409 for r in results) == 1
            assert request.history_memory is not None
            assert (
                await service.select(request.identity, request.session_id, request.history_memory)
            ).decisions[0].reason == "scope_changed"
            scope = await service.store.scope(request.identity, request.session_id)
            checks = {
                "foreign_owner_denied": True,
                "scope_cas_one_winner": True,
                "stale_scope_denied": True,
            }
        assert scope.revision >= 1
        atomic_json(
            output,
            {
                "status": "PASS",
                "run_id": receipt.run_id,
                "scope_revision": scope.revision,
                "source_version": source[0].version,
                "model_calls": 0,
                "checks": checks,
            },
        )
    finally:
        await repository.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicit cross-session real PG/MCP/model content")
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-report", type=Path)
    parser.add_argument("--protocol", action="store_true")
    parser.add_argument("--lifecycle", action="store_true")
    parser.add_argument("--check-memory")
    args = parser.parse_args()
    if args.read_report:
        asyncio.run(
            read(
                args.read_report,
                args.output,
                protocol=args.protocol,
                lifecycle=args.lifecycle,
                check_memory=args.check_memory,
            )
        )
    else:
        asyncio.run(run(json.loads(args.config.read_text(encoding="utf-8")), args.output))


if __name__ == "__main__":
    main()
