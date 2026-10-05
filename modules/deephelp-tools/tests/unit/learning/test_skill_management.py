"""候选/审核不能热替换运行；CAS、原件漂移和浏览归属独立于模型事实测试。"""

import json
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.adapters.releases.skill_store import SkillStore, read_skill_review, skill_diff
from deephelp_app.api.app import create_app
from deephelp_app.application.skills import load_skills
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_tools.learning.workbench.skills import SkillEdit, edited_registry, skills_router

pytestmark = pytest.mark.integration
OWNER = VerifiedIdentity(tenant_id="owner", user_id="a")


def candidate():
    base = load_skills()
    edit = SkillEdit(
        base_digest=base.digest,
        skill_id="order-benefits",
        version="2",
        goal="只引用已确认订单的真实证据",
        confirmed=True,
    )
    return base, edited_registry(base, edit)


def test_edit_rebinds_actual_dependents_and_does_not_mutate_base():
    base, new = candidate()
    old = base.definitions[0]
    changed = new.definitions[0]
    assert old.version == "1" and changed.version == "2"
    step = new.compositions[0].steps[0]
    assert step.digest == changed.digest and step.version == changed.version
    assert new.compositions[0].version != base.compositions[0].version
    assert {r["skill_id"] for r in skill_diff(base, new)} == {
        "order-benefits",
        "order-coupon-diagnosis",
    }
    assert changed.tool == old.tool and changed.input_schema == old.input_schema


def test_review_is_cas_immutable_and_never_activates(tmp_path):
    base, new = candidate()
    store = SkillStore(tmp_path)
    row = store.propose(new, base)
    assert store.propose(new, base) == row
    with pytest.raises(AppError, match="not been approved"):
        store.approved(new.digest)
    with pytest.raises(AppError, match="changed"):
        store.review(new.digest, 2, OWNER, "已经核对", True)
    with pytest.raises(AppError, match="reason"):
        store.review(new.digest, 1, OWNER, "  ", True)
    store.review(new.digest, 1, OWNER, "机器契约及闭包已核对，内容验证另做", True)
    path = store.approved(new.digest)
    assert read_skill_review(path)[0] == new
    with pytest.raises(AppError, match="changed"):
        store.review(new.digest, 1, OWNER, "重复审核", True)
    assert not (tmp_path / "writer.lock").exists()
    assert not list(tmp_path.rglob("active*"))
    value = json.loads(path.read_text())
    value["reason"] = "漂移"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(AppError, match="drifted"):
        store.approved(new.digest)


def test_rejected_and_metadata_path_tampering_fail_closed(tmp_path):
    base, new = candidate()
    store = SkillStore(tmp_path)
    store.propose(new, base)
    store.review(new.digest, 1, OWNER, "目标不足", False)
    with pytest.raises(AppError):
        store.approved(new.digest)
    row = store.get(new.digest)
    row.update(status="APPROVED", review_digest="../../outside")
    store.path(new.digest).write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(AppError, match="digest"):
        store.approved(new.digest)
    with store.writer(), pytest.raises(AppError, match="busy"):
        store.propose(new, base)


async def test_http_owner_zero_model_reads_and_explicit_review(tmp_path):
    base = load_skills()
    runtime = SimpleNamespace(skills_enabled=True, skills_registry=base)
    current = [OWNER]
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(skills_router(runtime, lambda r: current[0], OWNER, tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        view = (await client.get("/learning/skills")).json()
        assert view["model_calls"] == 0 and view["published_digest"] == base.digest
        body = dict(
            base_digest=base.digest,
            skill_id="order-benefits",
            version="2",
            goal="真实证据",
            confirmed=True,
        )
        assert (
            await client.post("/learning/skills/candidates", json={**body, "shell": "bad"})
        ).status_code == 422
        response = await client.post("/learning/skills/candidates", json=body)
        assert response.status_code == 200
        key = response.json()["candidate_digest"]
        assert (await client.get(f"/learning/skills/{key}/diff")).json()["changes"]
        review = dict(revision=1, approve=True, reason="核对通过", confirmed=True)
        assert (await client.post(f"/learning/skills/{key}/review", json=review)).status_code == 200
        assert (await client.post(f"/learning/skills/{key}/review", json=review)).status_code == 409
        assert runtime.skills_registry == base
        current[0] = VerifiedIdentity(tenant_id="owner", user_id="foreign")
        assert (await client.get("/learning/skills")).status_code == 404
        assert (await client.get(f"/learning/skills/{key}/diff")).status_code == 404
        assert (await client.post(f"/learning/skills/{key}/review", json=review)).status_code == 404
        current[0] = OWNER
        runtime.skills_enabled = False
        assert (await client.get("/learning/skills")).status_code == 501
