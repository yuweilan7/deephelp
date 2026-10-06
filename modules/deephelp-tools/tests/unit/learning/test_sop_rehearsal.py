"""同一执行器的候选内容、取消清理和重载；任务完成不代替内容终态断言。"""

import asyncio
import copy
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.observability.trace import MemoryTrace, sop_node_observer
from deephelp_app.adapters.releases.sop_drafts import SOPDraftStore, draft_registry
from deephelp_app.api.app import create_app
from deephelp_app.application.conversation.tracking import current_run
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import request_execution
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_app.domain.models.intents import IntentCode
from deephelp_tools.learning.course.catalog import catalog_registry
from deephelp_tools.learning.replay.sop_rehearsal import execute_rehearsal
from deephelp_tools.learning.workbench.sop_authoring import sop_authoring_router

OWNER = VerifiedIdentity(tenant_id="owner", user_id="a")
FOREIGN = VerifiedIdentity(tenant_id="owner", user_id="b")


def candidate(tmp_path):
    base = bundled_registry()
    flow = base.definition(IntentCode.ORDER_ACTIVITY_QUERY)
    store = SOPDraftStore(tmp_path)
    draft = store.create(OWNER, base, flow.sop_id)
    content = copy.deepcopy(draft["content"])
    content["version"] = "rehearsal-activity-v43"
    # 新结论必须由同一候选真实终点返回，而不是仅检查构造出的JSON。
    for node in content["nodes"]:
        if node["kind"] == "end" and node["status"] == "RESOLVED":
            node["conclusion"] = "M43候选原执行器固定活动结论"
    draft = store.update(draft["draft_id"], OWNER, 1, content)
    return store, draft


def options(draft, *, scenario="normal", slots=None):
    return dict(
        registry=draft_registry(draft).model_dump(mode="json"),
        intent_code="ORDER_ACTIVITY_QUERY",
        owner=OWNER.model_dump(mode="json"),
        run_id="rehearsal-test",
        mode="offline",
        scenario=scenario,
        slots={"order_id": "90234105"} if slots is None else slots,
    )


async def test_original_engine_returns_candidate_content_and_actual_route(tmp_path):
    _, draft = candidate(tmp_path)
    result = await execute_rehearsal(options(draft))
    assert result["status"] == "RESOLVED"
    facts = {f["name"]: f["value"] for f in result["result"]["facts"]}
    assert facts["activity_labels"] == "明确合成的预演活动"
    assert facts["sop_conclusion"] == "M43候选原执行器固定活动结论"
    assert result["candidate_hash"] == draft_registry(draft).snapshot_hash
    assert result["model_calls"] == result["business_writes"] == 0
    assert len(result["tools"]) == 1
    branch = next(n for n in result["timeline"] if n["kind"] == "branch")
    assert branch["matched_route"] == 0 and branch["next_node"] == "present"
    assert branch["condition"]["fact"] == "order.activity_ids"
    assert "failed" in result["unexecuted_nodes"]
    assert current_run.get() is None and request_execution.get() is None


@pytest.mark.parametrize(
    ("scenario", "slots", "status", "tools"),
    [
        ("normal", {}, "WAITING_SLOT", 0),
        ("invalid_fact", None, "FAILED", 1),
        ("tool_failure", None, "FAILED", 1),
    ],
)
async def test_wait_and_failed_observation_do_not_forge_success(
    tmp_path, scenario, slots, status, tools
):
    _, draft = candidate(tmp_path)
    result = await execute_rehearsal(options(draft, scenario=scenario, slots=slots))
    assert result["status"] == status and len(result["tools"]) == tools
    assert result["model_calls"] == result["business_writes"] == 0
    if scenario == "invalid_fact":
        assert result["result"]["error"]["code"] == "MODEL_OUTPUT_INVALID"
        assert not any(n["kind"] == "branch" for n in result["timeline"])
    if not tools:
        assert result["timeline"] == [] and result["budget"]["attempts"] == 0


async def test_cancel_dispatched_lookup_saves_partial_and_resets_context(tmp_path):
    _, draft = candidate(tmp_path)
    partial = []

    async def work():
        try:
            return await execute_rehearsal(options(draft, scenario="slow"), progress=partial.append)
        finally:
            assert current_run.get() is None and request_execution.get() is None
            assert sop_node_observer.get() is None

    task = asyncio.create_task(work())
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert partial[-1]["timeline"][0]["error_code"] == "CANCELLED"
    assert partial[-1]["model_calls"] == 0 and partial[-1]["budget"]["attempts"] == 2


async def test_http_rehearsal_cas_owner_persistence_and_cancel_join(tmp_path):
    store, draft = candidate(tmp_path)
    runtime = SimpleNamespace(sop_registry=bundled_registry())
    current = [OWNER]

    def build():
        app = create_app(Settings(mode="test"), trace=MemoryTrace())
        app.include_router(sop_authoring_router(runtime, lambda r: current[0], OWNER, tmp_path))
        return app

    app = build()
    url = "/learning/sop/drafts/" + draft["draft_id"] + "/rehearsals"
    body = dict(revision=2, slots={"order_id": "90234105"})
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client,
    ):
        assert (await client.post(url, json={**body, "revision": 1})).status_code == 409
        assert (await client.post(url, json={**body, "slots": {"user_id": "b"}})).status_code == 403
        assert (await client.post(url, json={**body, "mode": "live-readonly"})).status_code == 403
        assert (
            await client.post(url, json={**body, "mode": "live-readonly", "confirmed": True})
        ).status_code == 503
        row = (await client.post(url, json=body)).json()
        key = row["id"]
        for _ in range(100):
            row = (await client.get("/learning/sop/rehearsals/" + key)).json()
            if row["state"] != "RUNNING":
                break
            await asyncio.sleep(0.01)
        assert row["state"] == "SUCCEEDED" and row["result"]["status"] == "RESOLVED"
        old = row["options"]["draft_hash"]
        content = copy.deepcopy(draft["content"])
        content["version"] = "next-candidate"
        saved = store.update(draft["draft_id"], OWNER, 2, content)
        assert saved["content_hash"] != old
        assert (await client.get(url)).json()["jobs"][0]["options"]["draft_hash"] == old
        current[0] = FOREIGN
        assert (await client.get("/learning/sop/rehearsals/" + key)).status_code == 404
        assert (await client.get(url)).status_code == 404
        current[0] = OWNER
        running = (await client.post(url, json={**body, "revision": 3, "scenario": "slow"})).json()
        await asyncio.sleep(0.03)
        cancelled = (
            await client.post("/learning/sop/rehearsals/" + running["id"] + "/cancel")
        ).json()
        assert cancelled["state"] == "CANCELLED"
        assert cancelled["observation"]["timeline"][0]["error_code"] == "CANCELLED"
    # 重建router从同一原JSON读取；没有任何自动预演或模型请求。
    restored = build()
    async with (
        restored.router.lifespan_context(restored),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restored), base_url="http://localhost"
        ) as client,
    ):
        loaded = (await client.get("/learning/sop/rehearsals/" + key)).json()
        assert loaded == row
        assert (await client.get(url)).json()["model_calls"] == 0


async def test_proposal_graph_rejected_before_ports(tmp_path):
    base = catalog_registry("complaints-v3")
    draft = SOPDraftStore(tmp_path).create(
        OWNER, base, base.definition(IntentCode.DISCOUNT_MISSING).sop_id
    )
    content = copy.deepcopy(draft["content"])
    end = next(n for n in content["nodes"] if n["kind"] == "end" and n["status"] == "RESOLVED")
    end.update(status="NEEDS_APPROVAL", proposal="simulate_discount_adjustment")
    store = SOPDraftStore(tmp_path)
    draft = store.update(draft["draft_id"], OWNER, 1, content)
    assert draft["validation"]["valid"]
    with pytest.raises(AppError, match="approval proposals"):
        await execute_rehearsal({**options(draft), "intent_code": "DISCOUNT_MISSING"})
