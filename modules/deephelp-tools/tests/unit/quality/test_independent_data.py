"""独立金标、泄漏与盲审的反例；不拿教学声明当实际AI/客户独立性证明。"""

import copy
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_tools.evaluation.quality.independent_data import (
    Annotation,
    Intake,
    adjudicate,
    annotate,
    freeze,
    receive,
    summary,
    verify,
)
from deephelp_tools.learning.course.independent_quality import (
    independent_quality_demo,
    teaching_annotation,
    teaching_intake,
)
from deephelp_tools.learning.workbench.independent_quality import independent_router

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("kind", ["group", "near", "history", "privacy", "identity"])
def test_rejects_leakage_and_private_inputs_without_partial_intake(kind):
    raw = teaching_intake().model_dump(mode="json")
    historical = []
    if kind == "group":
        raw["cases"][2]["duplicate_group"] = raw["cases"][0]["duplicate_group"]
    elif kind == "near":
        raw["cases"][0]["turns"][0]["text"] = "订单O901该减的优惠没减，实际扣款不对"
        raw["cases"][2]["turns"][0]["text"] = "订单O902该减的优惠没减，实际扣款不对"
    elif kind == "history":
        historical = [raw["cases"][2]["turns"][0]["text"]]
    elif kind == "privacy":
        raw["cases"][2]["turns"][0]["text"] += "电话13812345678"
    else:
        raw["cases"][1]["turns"][0]["turn_id"] = raw["cases"][0]["turns"][0]["turn_id"]
    with pytest.raises(ConfigurationError):
        receive(Intake.model_validate(raw), {}, historical)


@pytest.mark.parametrize("kind", ["author", "partial", "wronghash", "parent", "duplicate"])
def test_gold_requires_distinct_blind_reviewer_complete_input_and_leaf(kind):
    row = receive(teaching_intake(), {}, [])
    raw = teaching_annotation(row, "blind-a").model_dump(mode="json")
    if kind == "author":
        raw["reviewer_id"] = "course-author"
    elif kind == "partial":
        raw["labels"].pop()
    elif kind == "wronghash":
        raw["input_hash"] = "0" * 64
    elif kind == "parent":
        raw["labels"][0]["label"] = "SERVICE"
    else:
        row = annotate(row, Annotation.model_validate(raw), {})
    with pytest.raises(ConfigurationError):
        annotate(row, Annotation.model_validate(raw), {})


def test_disagreement_requires_third_blind_receipt_preserves_votes_and_frozen_hash():
    row = receive(teaching_intake(), {}, [])
    a = teaching_annotation(row, "blind-a")
    b = teaching_annotation(row, "blind-b").model_dump(mode="json")
    b["labels"][1]["label"] = "unknown"
    row = annotate(annotate(row, a, {}), Annotation.model_validate(b), {})
    with pytest.raises(ConfigurationError, match="Unresolved"):
        freeze(row)
    resolution = teaching_annotation(row, "third").model_dump(mode="json")
    resolution["labels"] = [resolution["labels"][1]]
    row = adjudicate(row, Annotation.model_validate(resolution), {})
    frozen = freeze(row)
    verify(frozen["frozen"])
    assert frozen["annotations"]["blind-b"]["labels"][1]["label"] == "unknown"
    assert frozen["frozen"]["gold"]["turn1"]["label"] == "COUPON_UNUSABLE"
    assert summary(frozen)["disagreements"] == 1 and summary(frozen)["unresolved"] == 0
    with pytest.raises(ConfigurationError, match="Frozen"):
        annotate(frozen, teaching_annotation(row, "fourth"), {})
    altered = copy.deepcopy(frozen["frozen"])
    altered["gold"]["turn1"]["label"] = "unknown"
    with pytest.raises(ConfigurationError, match="hash"):
        verify(altered)
    assert "gold" not in summary(frozen)


async def test_course_shares_actual_guards_without_quality_claim():
    result = await independent_quality_demo()
    assert len(result["rejected"]) == 3
    assert result["remote_model_calls"] == 0 and result["quality_claim"] is False


class TransactionDouble:
    """API测试用聚合；真实PG并发/持久证据由显式入口取得，不由此替代。"""

    def __init__(self):
        self.rows = {}

    async def migrate(self):
        pass

    async def list(self, owner):
        return list(self.rows.values())

    async def create(self, owner, row):
        self.rows.setdefault(row["dataset_id"], copy.deepcopy(row))
        return self.rows[row["dataset_id"]]

    async def get(self, owner, key):
        return self.rows[key]

    async def change(self, owner, key, revision, operation):
        old = self.rows[key]
        if old["revision"] != revision:
            raise AppError(ErrorCode.VERSION_CONFLICT, "CAS lost", 409)
        new = operation(copy.deepcopy(old))
        new["revision"] = revision + 1
        self.rows[key] = new
        return new


async def test_api_broker_receipts_blind_projection_cas_freeze_and_foreign_owner():
    owner = VerifiedIdentity(tenant_id="m45-test", user_id="owner")
    actor = owner
    store = TransactionDouble()
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(
        independent_router(SimpleNamespace(ledger=None), lambda r: actor, owner, store=store)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.get("/independent")).json()["status"] == "DATA_NOT_AVAILABLE"
        row = (
            await client.post(
                "/independent/datasets", json=teaching_intake().model_dump(mode="json")
            )
        ).json()
        key = row["dataset_id"]
        assert "gold" not in (await client.get(f"/independent/datasets/{key}/inputs")).text
        path = f"/independent/datasets/{key}/annotations"
        for revision, reviewer in enumerate(("blind-a", "blind-b")):
            body = dict(
                revision=revision,
                annotation=teaching_annotation(store.rows[key], reviewer).model_dump(mode="json"),
            )
            assert (await client.post(path, json=body)).status_code == 200
            assert (await client.post(path, json=body)).status_code == 409
        frozen = await client.post(f"/independent/datasets/{key}/freeze", json=dict(revision=2))
        assert frozen.status_code == 200 and frozen.json()["status"] == "FROZEN"
        assert store.rows[key]["annotations"]["blind-a"]["submitted_by"] == owner.model_dump(
            mode="json"
        )
        actor = VerifiedIdentity(tenant_id="m45-test", user_id="foreign")
        assert (await client.get("/independent")).status_code == 404
