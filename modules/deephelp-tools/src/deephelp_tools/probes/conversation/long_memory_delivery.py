"""同一原引擎的八个跨会话真实内容场景及新进程零调用回读。

共享预算writer只由执行阶段拥有。每行输入/原回执先保存再断言；失败不会重写原件。
PG仍是来源和当前事实，明确用户陈述不送给模型、600、工具或协作角色。
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.adapters.mcp.business.business_client import BusinessClient
from deephelp_app.adapters.mcp.shared.network_mcp import HTTPMCPConfig
from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.adapters.persistence.cases import PostgresCaseRepository
from deephelp_app.adapters.persistence.long_memory import PostgresLongMemory
from deephelp_app.adapters.releases.asset_integrity import provenance
from deephelp_app.adapters.releases.memory_binding import memory_policy_binding
from deephelp_app.application.dense import atomic_json
from deephelp_app.application.long_memory import LongMemoryService
from deephelp_app.bootstrap.runtime.budget import BudgetSession
from deephelp_app.domain.models.common import Outcome, VerifiedIdentity
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.long_memory import MemoryChange, MemoryReference, MemoryUse
from deephelp_tools.integrity import evaluation_provenance
from deephelp_tools.probes.conversation.long_memory_probe import assembly_from_config


async def delivery(config: dict[str, Any], output: Path) -> None:
    if await asyncio.to_thread(output.exists):
        raise ValueError("Use a new immutable delivery report")
    source = json.loads(
        await asyncio.to_thread(Path(config["source_report"]).read_text, encoding="utf-8")
    )
    owner = VerifiedIdentity.model_validate(config["source_owner"])
    gate = BudgetSession(Path(config["budget"]))
    gate.open()
    report: dict[str, Any] = {
        "status": "PENDING",
        "rows": [],
        "checks": {},
        "memory_policy_binding": memory_policy_binding(),
        "code_digest": provenance()["package_digest"],
        "tooling_digest": evaluation_provenance()["tooling_digest"],
    }
    try:
        runtime = assembly_from_config(config)
        async with (
            httpx.AsyncClient(trust_env=False, timeout=90) as client,
            runtime.open(client, MemoryTrace()) as conversation,
        ):
            assert conversation.long_memory and runtime.ledger
            service = conversation.long_memory
            memory = await service.preserve(
                owner, source["input"]["session_id"], source["response"]["question_id"]
            )
            original = await service.store.source(
                owner, memory.source_session, memory.source_question
            )
            assert original
            order = next(e.value for e in original[0].entities if e.name == "order_id")
            other = "M46-other-" + uuid4().hex[:12]
            business = BusinessClient(
                HTTPMCPConfig(
                    url=config["business_url"],
                    credentials_path=Path(config["business_credentials"]),
                )
            )
            await business.request(
                owner,
                "POST",
                "orders",
                {
                    "order_id": other,
                    "paid": {"amount": "71.23"},
                    "discount": {"amount": "0.00"},
                    "discount_status": "not_eligible",
                    "sku_ids": ["m46-synthetic-sku"],
                },
            )
            text = source["input"]["raw_text"]
            enabled_facts: object = None
            missing_response = None
            missing_input = None
            for case in (
                "enabled",
                "disabled",
                "different-order",
                "missing-current",
                "unrelated",
                "continuation",
                "corrected",
                "forgotten",
            ):
                session = "m46-content-" + uuid4().hex
                if case == "corrected":
                    memory = await service.change(
                        owner,
                        memory.memory_id,
                        MemoryChange(
                            expected_revision=memory.revision,
                            action="retain",
                            edit_id=uuid4().hex,
                            text="请先列依据。",
                        ),
                    )
                    memory = await service.change(
                        owner,
                        memory.memory_id,
                        MemoryChange(
                            expected_revision=memory.revision,
                            action="correct",
                            edit_id=uuid4().hex,
                            text="请先给结果，再列依据。",
                        ),
                    )
                if case == "forgotten":
                    memory = await service.change(
                        owner,
                        memory.memory_id,
                        MemoryChange(
                            expected_revision=memory.revision, action="forget", edit_id=uuid4().hex
                        ),
                    )
                if case == "continuation":
                    assert missing_response and missing_response.question_id
                    assert missing_input and missing_response and missing_response.question_id
                    session = missing_input.session_id
                    scope = await service.store.scope(owner, session)
                else:
                    scope = await service.configure(
                        owner, session, 0, case != "disabled", (memory.source_session,)
                    )
                use = MemoryUse(
                    scope_revision=scope.revision,
                    references=(
                        MemoryReference(memory_id=memory.memory_id, revision=memory.revision),
                    ),
                )
                raw = (
                    text.replace(order, other)
                    if case == "different-order"
                    else text.replace(order, "我的订单")
                    if case == "missing-current"
                    else "订单物流什么时候送达"
                    if case == "unrelated"
                    else "订单编号是" + order
                    if case == "continuation"
                    else text
                )
                now = datetime.now(UTC)
                request = RequestEnvelope(
                    identity=owner,
                    channel="m46-content",
                    session_id=session,
                    message_id=uuid4().hex,
                    request_id=uuid4().hex,
                    trace_id=uuid4().hex,
                    raw_text=raw,
                    occurred_at=now,
                    received_at=now,
                    history_memory=use,
                )
                if case == "continuation":
                    assert missing_response and missing_response.question_id
                    question = await runtime.ledger.question(
                        owner, session, missing_response.question_id
                    )
                    assert question
                    request = request.model_copy(
                        update={
                            "question_hint": question.question_id,
                            "expected_question_version": question.version,
                        }
                    )
                response = await conversation.run(request, gate.request())
                report["rows"].append(
                    {
                        "case_id": case,
                        "input": request.model_dump(mode="json"),
                        "response": response.model_dump(mode="json"),
                    }
                )
                atomic_json(output, report)
                selected = response.memory_selection
                assert selected and response.versions.memory == "m46-explicit-v1"
                if case in {"enabled", "continuation", "corrected"}:
                    assert selected.status == "SELECTED" and "历史参考" in response.reply
                else:
                    assert selected.status == "REJECTED" and "历史参考" not in response.reply
                if case == "enabled":
                    enabled_facts = response.facts
                if case == "disabled":
                    assert (
                        response.facts == enabled_facts
                        and selected.decisions[0].reason == "scope_disabled"
                    )
                if case == "different-order":
                    assert (
                        selected.decisions[0].reason == "order_mismatch"
                        and "71.23" in response.reply
                    )
                if case == "missing-current":
                    assert (
                        response.outcome == Outcome.CLARIFY
                        and response.missing_slots
                        and not response.tool_call_ids
                    )
                    missing_response, missing_input = response, request
                elif case == "unrelated":
                    assert (
                        not response.tool_call_ids
                        and not response.facts
                        and response.outcome == Outcome.HANDOFF
                    )
                else:
                    assert (
                        response.outcome == Outcome.ANSWERED
                        and response.tool_call_ids
                        and response.evidence_refs
                    )
                if case == "continuation":
                    assert missing_response
                    assert response.question_id == missing_response.question_id
                if case == "corrected":
                    assert "请先给结果" in response.reply and "请先列依据。" not in response.reply
                if case == "forgotten":
                    assert selected.decisions[0].reason == "memory_forgotten"
                    assert (
                        await service.preserve(owner, memory.source_session, memory.source_question)
                    ).state == "FORGOTTEN"
                before = dict(gate.state)
                replay = await conversation.run(request, gate.request())
                assert (
                    replay.replayed
                    and sum(replay.call_counts.model_dump().values()) == 0
                    and gate.state == before
                )
                report["checks"][case + ":content"] = True
                report["checks"][case + ":zero_call_replay"] = True
                print(
                    json.dumps(
                        {"case": case, "outcome": response.outcome, "memory": selected.status},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            after = await service.store.source(owner, memory.source_session, memory.source_question)
            assert after == original
            report["checks"]["source_stayed_ended"] = True
            report["diagnostics"] = runtime.gateway.diagnostics
            report["judge_diagnostics"] = runtime.judge_gateway.diagnostics
            report["status"] = "PASS"
    except BaseException as exc:
        report.update(status="FAIL", error=type(exc).__name__)
        raise
    finally:
        report["budget"] = dict(gate.state)
        atomic_json(output, report)
        gate.close()


async def reread(path: Path, output: Path) -> None:
    """新进程只读本人原receipt和记忆墓碑；没有模型/MCP/预算writer。"""
    report = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    repository = await PostgresCaseRepository.open(Path.cwd())
    try:
        service = LongMemoryService(PostgresLongMemory(repository.pool))
        hashes = {}
        for row in report["rows"]:
            request = RequestEnvelope.model_validate(row["input"])
            receipt = await repository.lookup(request)
            assert receipt and receipt.response
            assert receipt.response.model_dump(mode="json") == row["response"]
            hashes[row["case_id"]] = receipt.run_id
        request = RequestEnvelope.model_validate(report["rows"][-1]["input"])
        assert request.history_memory
        head = await service.store.get(
            request.identity, request.history_memory.references[0].memory_id
        )
        assert head and head.state == "FORGOTTEN"
        atomic_json(
            output,
            {"status": "PASS", "original_runs": hashes, "head_state": head.state, "model_calls": 0},
        )
    finally:
        await repository.aclose()


async def refresh_quality(config: dict[str, Any], output: Path) -> None:
    """复用原quality_gate重验分类；向量/检索观察未变，只复制已绑定原件到新闭包。"""
    from deephelp_tools.evaluation.retrieval.corpus_candidate import immutable
    from deephelp_tools.evaluation.retrieval.corpus_release import (
        prepare_corpus_assets,
        quality_gate,
    )

    snapshot_path = Path(config["corpus_review"])
    snapshot = json.loads(await asyncio.to_thread(snapshot_path.read_text, encoding="utf-8"))
    directory = Path(config["quality_root"]) / "corpus" / snapshot["version_hash"]
    for name in (
        "contract.json",
        "index.json",
        "dev.json",
        "test.json",
        "retrieval-gate.json",
        "selection.json",
        "pointer.json",
    ):
        content = await asyncio.to_thread((snapshot_path.parent / name).read_bytes)
        await asyncio.to_thread(immutable, directory / name, content)
    baseline = dict(
        config,
        pointer=config["baseline_pointer"],
        corpus=config["baseline_corpus"],
        cascade=config["baseline_cascade"],
    )
    gate = BudgetSession(Path(config["budget"]))
    gate.open()
    try:
        runtime = assembly_from_config(baseline)
        async with (
            httpx.AsyncClient(trust_env=False, timeout=90) as client,
            runtime.open(client, MemoryTrace()),
        ):
            result = await quality_gate(
                Path(config["quality_root"]),
                snapshot,
                runtime,
                gate.request,
                Path(config["baseline_assets"]),
            )
            assert result["status"] == "PASS"
            assets = await asyncio.to_thread(
                prepare_corpus_assets,
                Path(config["quality_root"]),
                snapshot,
                runtime,
                Path(config["baseline_assets"]),
            )
            atomic_json(
                output,
                {
                    "status": "PASS",
                    "assets": str(assets.resolve()),
                    "classification_rows": len(result["rows"]),
                    "budget": dict(gate.state),
                    "reused_vector_observation": True,
                },
            )
    finally:
        gate.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicit M46 fixed real content and restart reads"
    )
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-report", type=Path)
    parser.add_argument("--quality", action="store_true")
    args = parser.parse_args()
    if args.read_report:
        asyncio.run(reread(args.read_report, args.output))
    elif args.quality:
        asyncio.run(
            refresh_quality(json.loads(args.config.read_text(encoding="utf-8")), args.output)
        )
    else:
        asyncio.run(delivery(json.loads(args.config.read_text(encoding="utf-8")), args.output))


if __name__ == "__main__":
    main()
