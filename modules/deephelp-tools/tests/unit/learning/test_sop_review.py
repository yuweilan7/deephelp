"""审核是独立CAS/不可变原件；候选版本索引不能替换原问题和累计writer。"""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.adapters.releases.sop_drafts import SOPDraftStore, read_sop_review, sop_diff
from deephelp_app.api.app import create_app
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.bootstrap.runtime.budget import BudgetSession
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_tools.learning.workbench.evaluation import SharedGate
from deephelp_tools.learning.workbench.sop_authoring import sop_authoring_router

OWNER = VerifiedIdentity(tenant_id="review", user_id="a")
FOREIGN = VerifiedIdentity(tenant_id="review", user_id="b")


def changed(store, base, version="review-order-v43", conclusion="已核对活动关系"):
    draft = store.create(OWNER, base, base.definitions[0].sop_id)
    content = copy.deepcopy(draft["content"])
    content["version"] = version
    end = next(n for n in content["nodes"] if n["kind"] == "end")
    end["conclusion"] = conclusion
    return store.update(draft["draft_id"], OWNER, 1, content)


def test_freeze_cas_owner_reload_and_version_reuse(tmp_path):
    base = bundled_registry()
    store = SOPDraftStore(tmp_path)
    row = changed(store, base)
    identity = row["draft_id"]
    with pytest.raises(AppError, match="revision/base/status"):
        store.review(identity, OWNER, 1, base, "旧revision", True)
    with pytest.raises(AppError, match="not found"):
        store.review(identity, FOREIGN, 2, base, "外主体", True)
    frozen = store.review(identity, OWNER, 2, base, "字段、工具与依赖闭包已核对", True)
    assert frozen["revision"] == 3 and frozen["status"] == "APPROVED"
    path = SOPDraftStore(tmp_path).approved(identity, OWNER)
    candidate, review = read_sop_review(path)
    assert review["candidate_hash"] == candidate.snapshot_hash
    assert review["base_hash"] == base.snapshot_hash
    assert len(candidate.definitions) == len(base.definitions)
    assert base.definitions[0].version != candidate.definitions[0].version
    for call in (
        lambda: store.update(identity, OWNER, 3, row["content"]),
        lambda: store.review(identity, OWNER, 3, base, "重复审核", True),
    ):
        with pytest.raises(AppError, match="changed"):
            call()
    second = changed(store, base, conclusion="不同内容复用同版本")
    with pytest.raises(AppError, match="retained version"):
        store.review(second["draft_id"], OWNER, 2, base, "版本覆盖必须拒绝", True)
    assert store.get(second["draft_id"], OWNER)["status"] == "DRAFT"
    review["reason"] = "篡改审核理由"
    path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(AppError, match="drifted"):
        store.approved(identity, OWNER)
    assert not (tmp_path / "writer.lock").exists()


def test_review_requires_valid_new_version_and_current_base(tmp_path):
    base = bundled_registry()
    store = SOPDraftStore(tmp_path)
    row = store.create(OWNER, base, base.definitions[0].sop_id)
    with pytest.raises(AppError, match="new version"):
        store.review(row["draft_id"], OWNER, 1, base, "同版本", True)
    content = copy.deepcopy(row["content"])
    content["version"] = "bad-graph-v43"
    content["nodes"][0]["success"] = "missing"
    invalid = store.update(row["draft_id"], OWNER, 1, content)
    with pytest.raises(AppError, match="invalid graph"):
        store.review(row["draft_id"], OWNER, invalid["revision"], base, "坏图", True)
    other = base.model_copy(update={"registry_version": "different-current-base"})
    with pytest.raises(AppError, match="base/status"):
        store.review(row["draft_id"], OWNER, invalid["revision"], other, "基准变动", True)
    rejected = changed(store, base, version="reject-v43")
    frozen = store.review(rejected["draft_id"], OWNER, 2, base, "业务结论待修正", False)
    assert frozen["status"] == "REJECTED"
    with pytest.raises(AppError, match="no approved snapshot"):
        store.approved(rejected["draft_id"], OWNER)


def test_browser_numeric_json_has_one_candidate_and_review_identity(tmp_path):
    """独立模拟浏览器整数化的JSON，不能以Python自己的float输出替代页面输入。"""
    from deephelp_app.adapters.releases.sop_drafts import draft_registry

    base = bundled_registry()
    store = SOPDraftStore(tmp_path)
    row = store.create(OWNER, base, base.definitions[0].sop_id)
    raw = copy.deepcopy(row["content"])
    raw["version"] = "browser-numeric-v43"
    raw["limits"] = {k: int(v) if isinstance(v, float) else v for k, v in raw["limits"].items()}
    saved = store.update(row["draft_id"], OWNER, 1, raw)
    candidate = draft_registry(saved)
    assert sop_diff(base.definitions[0].model_dump(mode="json"), saved["content"]) == [
        {"path": ["version"], "before": base.definitions[0].version, "after": raw["version"]}
    ]
    store.review(row["draft_id"], OWNER, 2, base, "浏览器规范JSON核对", True)
    reviewed, record = read_sop_review(store.approved(row["draft_id"], OWNER))
    assert reviewed == candidate and record["candidate_hash"] == candidate.snapshot_hash


async def test_http_diff_review_explicit_confirmation_and_stale_base(tmp_path):
    base = bundled_registry()
    runtime = SimpleNamespace(sop_registry=base)
    store = SOPDraftStore(tmp_path)
    row = changed(store, base)
    original = base.definitions[0].model_dump(mode="json")
    url = f"/learning/sop/drafts/{row['draft_id']}"
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(sop_authoring_router(runtime, lambda r: OWNER, OWNER, tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        diff = (await c.get(url + "/diff")).json()
        assert diff["changes"] == sop_diff(original, row["content"])
        assert {tuple(v["path"]) for v in diff["changes"]} == {
            ("version",),
            ("nodes", 2, "conclusion"),
        }
        assert diff["model_calls"] == 0
        body = dict(revision=2, base_hash=base.snapshot_hash, approve=True, reason="明确审核")
        assert (await c.post(url + "/review", json=body)).status_code == 422
        body["confirmed"] = True
        runtime.sop_registry = base.model_copy(update={"registry_version": "new-active-base"})
        assert (await c.post(url + "/review", json=body)).status_code == 409
        runtime.sop_registry = base
        frozen = (await c.post(url + "/review", json=body)).json()
        assert frozen["status"] == "APPROVED" and frozen["candidate_hash"] == diff["candidate_hash"]
        assert (await c.post(url + "/review", json=body)).status_code == 409
        assert runtime.sop_registry.snapshot_hash == base.snapshot_hash


async def test_borrowed_gate_reads_same_cumulative_state_and_cannot_own_lock(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text(
        json.dumps(
            dict(
                max_calls=20,
                max_tokens=80000,
                max_cost_cny=2,
                attempts=2,
                tokens=1049,
                charged_tokens=1049,
                cost_upper_cny="0.008392",
                uncertain_attempts=0,
            )
        ),
        encoding="utf-8",
    )
    owner = BudgetSession(path)
    owner.open()
    try:
        borrowed = SharedGate(owner.request, path)
        assert borrowed.path == path and borrowed.state["attempts"] == 2
        budget = borrowed.request()
        budget.claim_attempt(retry=False)
        assert borrowed.state["attempts"] == 3
        for call in (borrowed.open, borrowed.close):
            with pytest.raises(ConfigurationError, match="Borrowed gate"):
                call()
        assert path.with_suffix(".lock").exists()
    finally:
        owner.close()
    assert json.loads(path.read_text())["attempts"] == 3
