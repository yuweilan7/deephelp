import asyncio
import json
from importlib.resources import files

import pytest
from pydantic import ValidationError

from deephelp_app.domain.checks import response_from_sop
from deephelp_app.domain.models import (
    EntityName,
    ErrorCode,
    IntentCode,
    ModelToolCall,
    NextAction,
    SOPResult,
    SOPStatus,
    VersionManifest,
)
from deephelp_app.errors import AppError
from deephelp_app.evaluation.sop_acceptance import proposal_registry, scenario_config, scenarios
from deephelp_app.execution import ExecutionBudget
from deephelp_app.learning.sop_replay import ReplayModel
from deephelp_app.probes.sop_probe import sop_question
from deephelp_app.sop import SOPExecutor
from deephelp_app.sop_governance import RegistryStore, SOPRegistry, bundled_registry
from deephelp_app.tool_gateway import ToolGateway, process_alive

pytestmark = pytest.mark.integration


def actions(case):
    return [
        ModelToolCall(
            call_id=f"selection-{i}",
            name=tool.value,
            arguments={
                "order_id": case.order,
                **({"coupon_id": case.coupon} if tool.value == "check_coupon" else {}),
            },
        )
        for i, tool in enumerate(case.tools)
    ]


def question_for(case, registry, budget):
    question, context = sop_question(
        budget, case.intent, order=case.order, coupon=case.coupon, raw_text=case.raw_text
    )
    question = question.model_copy(
        update={
            "versions": registry.pin(question.versions, case.intent),
            "entities": tuple(e for e in question.entities if e.name not in case.missing_slots),
        }
    )
    return question, context


@pytest.mark.parametrize("case", scenarios().cases, ids=lambda c: c.case_id)
async def test_frozen_mechanisms_with_real_stdio(case):
    registry = proposal_registry() if case.proposal else bundled_registry()
    budget = ExecutionBudget.start(60, 10, 0)
    question, context = question_for(case, registry, budget)
    model = ReplayModel(actions(case))
    gateway = ToolGateway(config=scenario_config(case))
    async with gateway.open():
        result = await SOPExecutor(model, gateway, registry=registry).execute(
            question, budget, context=context, run_id="m14-test-run"
        )
        ledger = await gateway.ledger()
    assert result.status == case.status
    assert tuple(r.tool_name for r in ledger) == case.tools
    assert result.tool_call_ids == tuple(r.call_id for r in ledger)
    assert gateway.pid and not process_alive(gateway.pid)
    if case.error:
        assert result.error and result.error.code == case.error
    facts = {f.name: f.value for f in result.facts}
    assert all(facts.get(k) == v for k, v in case.expected_facts.items())
    if case.end_node:
        assert result.node_path[-1] == case.end_node
    if case.missing_slots:
        assert not model.requests and not ledger
    if case.status == SOPStatus.FAILED:
        assert not result.facts and not result.evidence_refs
    if case.proposal:
        assert result.plan and result.plan.snapshot_hash == registry.snapshot_hash
        assert result.plan.question_version == question.version
        assert result.plan.identity == question.identity
        assert result.plan.parameters.order_id == case.order
        response = response_from_sop(
            context,
            run_id="m14-test-run",
            question_id=question.question_id,
            result=result,
            reply="只生成计划，需要审批，未执行变更。",
            versions=question.versions,
            budget_used=budget.usage(),
        )
        assert response.outcome == "HANDOFF" and response.sop_plan == result.plan
        assert response.run_status == "SUCCEEDED" and response.question_status == "HANDED_OFF"


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_tool",
        "grant_escalation",
        "slots",
        "unknown_condition",
        "missing_target",
        "duplicate_id",
        "unreachable",
        "cycle",
        "failed_to_success",
        "no_preflight",
        "unknown_fact",
        "fact_before_lookup",
        "money_type",
        "duplicate_condition",
        "tool_budget",
        "step_budget",
        "schema_hash",
        "arbitrary_code",
        "non_actionable",
    ],
)
def test_illegal_publish_preserves_active(tmp_path, mutation):
    registry = bundled_registry()
    store = RegistryStore(tmp_path)
    store.publish(registry)
    before = store.pointer.read_bytes()
    data = registry.model_dump(mode="json")
    flow = data["definitions"][0]
    nodes = flow["nodes"]
    if mutation == "unknown_tool":
        nodes[0]["tool"] = "refund_order"
    elif mutation == "grant_escalation":
        flow["allowed_tools"].append("check_coupon")
    elif mutation == "slots":
        flow["required_slots"] = ["coupon_id"]
    elif mutation == "unknown_condition":
        nodes[1]["routes"][0]["condition"]["op"] = "eval"
    elif mutation == "missing_target":
        nodes[0]["success"] = "absent-node"
    elif mutation == "duplicate_id":
        nodes[-1]["node_id"] = nodes[0]["node_id"]
    elif mutation == "unreachable":
        nodes.append(
            {"node_id": "orphan", "kind": "end", "status": "HANDED_OFF", "conclusion": "人工"}
        )
    elif mutation == "cycle":
        nodes[0]["success"] = nodes[0]["node_id"]
    elif mutation == "failed_to_success":
        nodes[0]["failure"] = "applied"
    elif mutation == "no_preflight":
        flow["entry"] = "applied"
    elif mutation == "unknown_fact":
        nodes[1]["routes"][0]["condition"]["fact"] = "order.secret"
    elif mutation == "fact_before_lookup":
        flow["entry"] = "decide"
    elif mutation == "money_type":
        nodes[1]["routes"][0]["condition"] = {
            "fact": "order.discount_status",
            "op": "money_lt",
            "other_fact": "order.paid",
        }
    elif mutation == "duplicate_condition":
        nodes[1]["routes"].append(nodes[1]["routes"][0])
    elif mutation == "tool_budget":
        data["definitions"][1]["limits"]["max_tool_calls"] = 1
    elif mutation == "step_budget":
        flow["limits"]["max_steps"] = 1
    elif mutation == "schema_hash":
        data["tool_schema_hash"] = "sha256:wrong"
    elif mutation == "arbitrary_code":
        flow["execute"] = "import os"
    else:
        flow["intent_code"] = "SERVICE"
    with pytest.raises(ValueError):
        store.publish(SOPRegistry.model_validate(data))
    assert store.pointer.read_bytes() == before and store.history()[0] == registry


def test_publish_duplicate_rollback_and_content_collision(tmp_path):
    store = RegistryStore(tmp_path)
    original, proposed = bundled_registry(), proposal_registry()
    store.publish(original)
    store.publish(proposed)
    previous = json.loads(store.pointer.read_text())["previous"]
    store.publish(proposed)
    assert json.loads(store.pointer.read_text())["previous"] == previous
    assert store.rollback() == original.snapshot_hash
    assert len(store.history()) == 2
    data = proposed.model_dump(mode="json")
    data["definitions"][0]["nodes"][-1]["conclusion"] += " changed"
    with pytest.raises(ValueError, match="version"):
        store.publish(SOPRegistry.model_validate(data))
    assert store.history()[0] == original


def test_corruption_lock_and_path_escape(tmp_path):
    store = RegistryStore(tmp_path)
    registry = bundled_registry()
    store.publish(registry)
    (tmp_path / "publish.lock").write_text("another writer")
    with pytest.raises(FileExistsError):
        store.publish(proposal_registry())
    (tmp_path / "publish.lock").unlink()
    snapshot_path = tmp_path / (registry.snapshot_hash[7:] + ".json")
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    data["registry_version"] += "-corrupt"
    snapshot_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        store.history()
    with pytest.raises(ValueError):
        store.load("../../arbitrary-file")


async def test_publish_during_run_and_retained_question_version(tmp_path):
    store = RegistryStore(tmp_path)
    original, proposed = bundled_registry(), proposal_registry()
    store.publish(original)
    case = next(c for c in scenarios().cases if c.case_id == "discount-missing-human")
    budget = ExecutionBudget.start(60, 20, 0)
    question, context = question_for(case, original, budget)
    started, release = asyncio.Event(), asyncio.Event()

    class WaitingModel(ReplayModel):
        async def chat(self, request, budget):
            started.set()
            await release.wait()
            return await super().chat(request, budget)

    gateway = ToolGateway()
    async with gateway.open():
        old = SOPExecutor(WaitingModel(actions(case)), gateway, registry=original)
        task = asyncio.create_task(old.execute(question, budget, context=context, run_id="old-run"))
        await started.wait()
        store.publish(proposed)
        release.set()
        result = await task
        assert result.status == SOPStatus.HANDED_OFF and result.plan is None
        history = store.history()
        new = SOPExecutor(ReplayModel(actions(case)), gateway, registry=history[0], history=history)
        # A new process/assembly still resolves the old pinned question to the old snapshot.
        same = await new.execute(question, budget, context=context, run_id="continued-run")
        assert same.status == SOPStatus.HANDED_OFF and same.sop_version == result.sop_version
        fresh = question.model_copy(
            update={"versions": proposed.pin(VersionManifest(), case.intent)}
        )
        fresh_executor = SOPExecutor(ReplayModel(actions(case)), gateway, registry=proposed)
        approved = await fresh_executor.execute(fresh, budget, context=context, run_id="new-run")
        assert approved.status == SOPStatus.NEEDS_APPROVAL and approved.plan


@pytest.mark.parametrize(
    "change", ["snapshot", "version", "prompt", "tool_signature", "arguments", "tool"]
)
async def test_runtime_version_and_action_guards(change):
    case = scenarios().cases[0]
    registry = bundled_registry()
    budget = ExecutionBudget.start(10, 10, 0)
    question, context = question_for(case, registry, budget)
    selected = actions(case)
    updates = {
        "snapshot": "sop_snapshot",
        "version": "sop",
        "prompt": "sop_prompt",
        "tool_signature": "tool_schema",
    }
    if change in updates:
        question = question.model_copy(
            update={"versions": question.versions.model_copy(update={updates[change]: "wrong"})}
        )
    elif change == "arguments":
        selected[0] = selected[0].model_copy(update={"arguments": {"order_id": "000053"}})
    else:
        selected[0] = selected[0].model_copy(update={"name": "refund_order"})
    gateway = ToolGateway()
    async with gateway.open():
        executor = SOPExecutor(ReplayModel(selected), gateway, registry=registry)
        if change in updates:
            with pytest.raises(AppError) as err:
                await executor.execute(question, budget, context=context, run_id="bad-version")
            assert err.value.code == ErrorCode.VERSION_CONFLICT
        else:
            result = await executor.execute(question, budget, context=context, run_id="bad-action")
            assert result.status == SOPStatus.FAILED
        assert not await gateway.ledger()


def test_published_schema_matches_exact_contract():
    data = json.loads(files("deephelp_app").joinpath("sop_data/governance-schema.json").read_text())
    assert {k: v for k, v in data.items() if k != "$schema"} == SOPRegistry.model_json_schema()
    registry = bundled_registry()
    with pytest.raises(ValidationError):
        registry.registry_version = "mutated"
    assert registry.definitions[0].required_slots == (EntityName.ORDER_ID,)


def test_startup_revalidates_unchecked_model_copy():
    registry = bundled_registry()
    definition = registry.definitions[0]
    unchecked_node = definition.nodes[0].model_copy(update={"tool": "refund_order"})
    unchecked_flow = definition.model_copy(
        update={"nodes": (unchecked_node, *definition.nodes[1:])}
    )
    unchecked = registry.model_copy(
        update={"definitions": (unchecked_flow, *registry.definitions[1:])}
    )
    with pytest.raises(ValueError):
        SOPExecutor(ReplayModel([]), ToolGateway(), registry=unchecked)


@pytest.mark.parametrize("mode", ["cancel", "deadline"])
async def test_governed_selection_cancel_and_deadline_do_not_dispatch(mode):
    registry = bundled_registry()
    case = scenarios().cases[0]
    budget = ExecutionBudget.start(0.1 if mode == "deadline" else 10, 10, 0)
    question, context = question_for(case, registry, budget)
    started = asyncio.Event()

    class BlockingModel:
        async def chat(self, request, budget):
            started.set()
            await asyncio.Event().wait()

    gateway = ToolGateway()
    task = asyncio.create_task(
        SOPExecutor(BlockingModel(), gateway, registry=registry).execute(
            question, budget, context=context, run_id="bounded-flow"
        )
    )
    await started.wait()
    if mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result.status == SOPStatus.FAILED and result.error.code == ErrorCode.BUDGET_EXHAUSTED
        assert not result.tool_call_ids
    assert gateway.pid is None and budget.tool_steps_used == 0


def test_approval_signal_without_bound_plan_is_rejected():
    with pytest.raises(ValidationError):
        SOPResult(
            status=SOPStatus.HANDED_OFF,
            sop_id="discount-missing-v1",
            sop_version="test-v1",
            next_action=NextAction.REQUEST_APPROVAL,
            reason="no plan",
        )


async def test_old_m07_question_survives_governed_startup():
    budget = ExecutionBudget.start(30, 10, 0)
    question, context = sop_question(budget, IntentCode.DISCOUNT_MISSING)
    gateway = ToolGateway()
    async with gateway.open():
        result = await SOPExecutor(
            ReplayModel.fixture("discount"), gateway, registry=bundled_registry()
        ).execute(question, budget, context=context, run_id="legacy-question")
    assert result.status == SOPStatus.RESOLVED and result.sop_version == "discount-missing-v1"
