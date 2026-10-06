"""作者图仍服从原契约；持久重载、并发CAS和owner不由页面正确性代替。"""

import copy
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.adapters.releases.sop_drafts import SOPDraftStore, validate_content
from deephelp_app.api.app import create_app
from deephelp_app.api.workbench import BrowserAuth, workbench_router
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_tools.learning.workbench.sop_authoring import sop_authoring_router

OWNER = VerifiedIdentity(tenant_id="owner", user_id="a")
FOREIGN = VerifiedIdentity(tenant_id="owner", user_id="b")


def test_persistent_cas_and_invalid_intermediate_graph(tmp_path):
    base = bundled_registry()
    original = base.snapshot_hash
    store = SOPDraftStore(tmp_path)
    draft = store.create(OWNER, base, base.definitions[0].sop_id)
    content = copy.deepcopy(draft["content"])
    content["nodes"][0]["success"] = "missing"
    saved = store.update(draft["draft_id"], OWNER, 1, content)
    assert saved["revision"] == 2 and not saved["validation"]["valid"]
    assert "order" in saved["validation"]["errors"][0]["message"]
    assert SOPDraftStore(tmp_path).get(draft["draft_id"], OWNER) == saved
    with pytest.raises(AppError, match="revision"):
        store.update(draft["draft_id"], OWNER, 1, draft["content"])
    with pytest.raises(AppError, match="not found"):
        store.get(draft["draft_id"], FOREIGN)
    assert store.list(FOREIGN) == []
    assert base.snapshot_hash == original
    with store.writer(), pytest.raises(AppError, match="busy"):
        store.update(draft["draft_id"], OWNER, 2, draft["content"])
    assert not list(tmp_path.glob("*.pending")) and not (tmp_path / "writer.lock").exists()


@pytest.mark.parametrize(
    "mutation", ["cycle", "duplicate", "unreachable", "code", "tool", "fact", "field"]
)
def test_backend_graph_rejects_frontend_bypass(mutation):
    content = bundled_registry().definitions[0].model_dump(mode="json")
    if mutation == "cycle":
        content["nodes"][0]["success"] = "order"
    elif mutation == "duplicate":
        content["nodes"].append(content["nodes"][0])
    elif mutation == "unreachable":
        content["nodes"].append(
            {"node_id": "orphan", "kind": "end", "status": "FAILED", "conclusion": "孤立"}
        )
    elif mutation == "code":
        content["nodes"][1]["routes"][0]["condition"]["op"] = "eval"
    elif mutation == "tool":
        content["nodes"][0]["tool"] = "simulate_discount_adjustment"
    elif mutation == "fact":
        content["nodes"][1]["routes"][0]["condition"]["fact"] = "order.unknown"
    else:
        content["endpoint"] = "https://untrusted.example"
    assert not validate_content(content)["valid"]


def test_duplicate_diagnostic_locates_non_entry_node():
    """重复的是分支节点时，错误必须指向它，不能把作者引到无关入口。"""
    content = bundled_registry().definitions[0].model_dump(mode="json")
    branch = next(n for n in content["nodes"] if n["kind"] == "branch")
    content["nodes"].append(copy.deepcopy(branch))
    result = validate_content(content)
    assert not result["valid"]
    assert result["errors"][0]["message"] == "Value error, Duplicate node ID: " + branch["node_id"]


async def test_http_persistent_author_scope_and_cas(tmp_path):
    base = bundled_registry()
    runtime = SimpleNamespace(sop_registry=base)
    current = [OWNER]
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(sop_authoring_router(runtime, lambda r: current[0], OWNER, tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        view = (await client.get("/learning/sop")).json()
        assert view["model_calls"] == 0 and view["base_hash"] == base.snapshot_hash
        body = {"sop_id": base.definitions[0].sop_id, "base_hash": base.snapshot_hash}
        assert (
            await client.post("/learning/sop/drafts", json={**body, "owner": FOREIGN.model_dump()})
        ).status_code == 422
        assert (
            await client.post("/learning/sop/drafts", json={**body, "base_hash": "stale"})
        ).status_code == 409
        row = (await client.post("/learning/sop/drafts", json=body)).json()
        url = f"/learning/sop/drafts/{row['draft_id']}"
        update = {"revision": 1, "content": row["content"]}
        assert (await client.put(url, json=update)).json()["revision"] == 2
        assert (await client.put(url, json=update)).status_code == 409
        assert (await client.get(url)).json()["content_hash"] == row["content_hash"]
        update["revision"] = 2
        update["content"]["required_slots"] = []
        assert (await client.put(url, json=update)).status_code == 400
        current[0] = FOREIGN
        assert (await client.get("/learning/sop")).status_code == 404
        assert (await client.get(url)).status_code == 404
        assert (await client.put(url, json=update)).status_code == 404
        assert runtime.sop_registry == base


async def test_real_browser_auth_checks_origin_and_csrf(tmp_path):
    base = bundled_registry()
    auth = BrowserAuth(lambda r: OWNER, identity=OWNER)
    app = create_app(Settings(mode="test"), trace=MemoryTrace(), identity_provider=auth)
    app.include_router(workbench_router(auth, lambda: {"mode": "offline"}))
    app.include_router(
        sop_authoring_router(SimpleNamespace(sop_registry=base), auth, OWNER, tmp_path)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        csrf = (await client.get("/auth/session")).json()["csrf"]
        body = {"sop_id": base.definitions[0].sop_id, "base_hash": base.snapshot_hash}
        assert (await client.post("/learning/sop/drafts", json=body)).status_code == 403
        assert (
            await client.post(
                "/learning/sop/drafts",
                json=body,
                headers={"X-CSRF-Token": csrf, "Origin": "https://foreign.example"},
            )
        ).status_code == 403
        assert (
            await client.post("/learning/sop/drafts", json=body, headers={"X-CSRF-Token": csrf})
        ).status_code == 200
