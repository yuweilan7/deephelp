"""作者操作先过完整数据守卫；HTTP证明owner/CAS/确认，PG事务另做真实协议验证。"""

import base64
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.api.workbench import BrowserAuth, workbench_router
from deephelp_app.application.corpus import read_corpus_bytes
from deephelp_app.application.corpus_authoring import (
    checked_records,
    edit_record,
    empty_workspace,
    freeze_workspace,
    import_preview,
    review_records,
    stage_preview,
    verify_snapshot,
)
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_app.domain.models.retrieval import CorpusRecord
from deephelp_tools.learning.workbench.corpus_authoring import corpus_authoring_router

OWNER = VerifiedIdentity(tenant_id="m44-test", user_id="owner")
FOREIGN = VerifiedIdentity(tenant_id="m44-test", user_id="foreign")


def record(doc_id="one", **changes):
    return CorpusRecord.model_validate(
        {
            **dict(
                doc_id=doc_id,
                content="查询这笔订单关联的活动信息",
                intent_code="ORDER_ACTIVITY_QUERY",
                split="reference",
                source_group=doc_id,
                variant_group=doc_id,
                synthetic=True,
                registry_version="complaints-v3",
            ),
            **changes,
        }
    )


class TransactionDouble:
    """只验证HTTP与纯变换，不声称持久/并发PG协议；失败的副本从不保存。"""

    def __init__(self):
        self.rows = {}

    async def migrate(self):
        pass

    async def get(self, owner, catalog):
        return copy.deepcopy(self.rows.get((owner, catalog), empty_workspace(catalog)))

    async def change(self, owner, catalog, revision, operation):
        old = await self.get(owner, catalog)
        if revision != old["revision"]:
            raise AppError(ErrorCode.VERSION_CONFLICT, "revision conflict", 409)
        row = operation(old)
        row["revision"] = revision + 1
        self.rows[(owner, catalog)] = copy.deepcopy(row)
        return row


@pytest.mark.parametrize(
    "mutation",
    [
        "parent",
        "unknown",
        "catalog",
        "duplicate_id",
        "content",
        "group",
        "near",
        "extra",
        "missing_source",
    ],
)
def test_full_dataset_guards(mutation):
    first = record().model_dump(mode="json")
    second = {
        **first,
        "doc_id": "two",
        "content": "优惠券达到门槛仍不能使用",
        "source_group": "two",
        "variant_group": "two",
    }
    if mutation == "parent":
        second["intent_code"] = "SERVICE"
    elif mutation == "unknown":
        second["intent_code"] = "EXECUTE_ANYTHING"
    elif mutation == "catalog":
        second["registry_version"] = "complaints-v1"
    elif mutation == "duplicate_id":
        second["doc_id"] = "one"
    elif mutation == "content":
        second["content"] = first["content"]
    elif mutation == "group":
        second["source_group"], second["split"] = "one", "test"
    elif mutation == "near":
        second["content"], second["split"] = first["content"] + "？", "test"
    elif mutation == "extra":
        second["tool"] = "pay"
    else:
        second.pop("source_group")
    assert checked_records([first, second], "complaints-v3").errors


def test_preview_import_is_explicit_atomic_and_review_is_invalidated():
    first = record().model_dump(mode="json")
    bad = {**first, "doc_id": "bad", "intent_code": "SERVICE"}
    data = "\n".join(json.dumps(r) for r in [first, bad]).encode()
    original = empty_workspace("complaints-v3")
    staged = stage_preview(original, read_corpus_bytes(data, ".jsonl"))
    preview = next(iter(staged["previews"]))
    assert not staged["entries"] and "row 2" in str(staged["previews"][preview]["errors"])
    with pytest.raises(AppError):
        import_preview(staged, preview)
    assert original == empty_workspace("complaints-v3") and not staged["entries"]
    staged = stage_preview(original, read_corpus_bytes(json.dumps(first).encode(), ".jsonl"))
    preview = next(iter(staged["previews"]))
    imported = import_preview(staged, preview)
    assert imported["previews"][preview]["status"] == "IMPORTED"
    assert imported["previews"][preview]["records"] == []
    reviewed = review_records(
        imported, ("one",), True, "人工核对标签与独立来源", OWNER.model_dump()
    )
    assert reviewed["entries"]["one"]["status"] == "APPROVED"
    edited = edit_record(reviewed, record(), "修订来源", create=False)
    assert edited["entries"]["one"]["status"] == "DRAFT"
    assert reviewed["reviews"] == edited["reviews"]
    with pytest.raises(AppError):
        import_preview(imported, preview)


def test_csv_mapping_and_formula_errors_are_rows():
    raw = (
        "编号,正文,标签,集合,来源,近义,合成\n"
        "a,询问订单参与活动,ORDER_ACTIVITY_QUERY,reference,a,a,true\n"
    )
    mapping = dict(
        zip(
            [
                "doc_id",
                "content",
                "intent_code",
                "split",
                "source_group",
                "variant_group",
                "synthetic",
            ],
            ["编号", "正文", "标签", "集合", "来源", "近义", "合成"],
            strict=True,
        )
    )
    preview = read_corpus_bytes(raw.encode(), ".csv", columns=mapping)
    assert not preview.errors and preview.records[0].doc_id == "a"
    assert read_corpus_bytes(raw.encode(), ".csv").errors
    bad = raw.replace("询问订单参与活动", "=SUM(A1)")
    assert "row 2" in str(read_corpus_bytes(bad.encode(), ".csv", columns=mapping).errors)


def test_freeze_requires_current_review_and_preserves_history():
    row = edit_record(empty_workspace("complaints-v3"), record(), "合成人工来源", create=True)
    with pytest.raises(AppError):
        freeze_workspace(row, "v1", OWNER.model_dump())
    reviewed = review_records(row, ("one",), True, "逐项人工核对", OWNER.model_dump())
    snapshot = freeze_workspace(reviewed, "v1", OWNER.model_dump())
    verify_snapshot(snapshot)
    edited = edit_record(reviewed, record(content="询问订单是否关联活动"), "新来源", create=False)
    with pytest.raises(AppError):
        freeze_workspace(edited, "v2", OWNER.model_dump())
    assert snapshot["entries"]["one"]["record"]["content"] == "查询这笔订单关联的活动信息"
    tampered = copy.deepcopy(snapshot)
    tampered["entries"]["one"]["source"] = "偷换来源"
    with pytest.raises(AppError):
        verify_snapshot(tampered)
    with pytest.raises(AppError):
        freeze_workspace(reviewed, "v1", FOREIGN.model_dump())


async def test_http_scope_cas_preview_export_and_confirmation():
    store = TransactionDouble()
    current = [OWNER]
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    app.include_router(
        corpus_authoring_router(
            SimpleNamespace(sop_registry=SimpleNamespace(catalog_version="complaints-v3")),
            lambda r: current[0],
            OWNER,
            store=store,
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.get("/learning/corpus")).json()["model_calls"] == 0
        body = dict(revision=0, record=record().model_dump(mode="json"), source="合成来源")
        row = (await client.post("/learning/corpus/records", json=body)).json()
        assert row["revision"] == 1 and row["total"] == 1
        assert (await client.post("/learning/corpus/records", json=body)).status_code == 409
        assert (
            await client.post(
                "/learning/corpus/records", json={**body, "owner": FOREIGN.model_dump()}
            )
        ).status_code == 422
        assert (
            await client.post(
                "/learning/corpus/reviews",
                json=dict(revision=1, doc_ids=["one"], approve=True, reason="确认"),
            )
        ).status_code == 422
        approved = await client.post(
            "/learning/corpus/reviews",
            json=dict(revision=1, doc_ids=["one"], approve=True, reason="确认", confirmed=True),
        )
        assert approved.json()["entries"][0]["status"] == "APPROVED"
        exported = (await client.get("/learning/corpus/export")).json()
        assert (
            read_corpus_bytes(exported["jsonl"].encode(), ".jsonl").records[0].metadata["source"]
            == "合成来源"
        )
        upload = dict(
            revision=2, format="jsonl", columns={}, data_base64=base64.b64encode(b"{}").decode()
        )
        previewed = (await client.post("/learning/corpus/previews", json=upload)).json()
        assert (
            previewed["revision"] == 3
            and previewed["total"] == 1
            and previewed["previews"][0]["errors"]
        )
        assert (
            await client.post(
                "/learning/corpus/imports",
                json=dict(
                    revision=3, preview_id=previewed["previews"][0]["preview_id"], confirmed=True
                ),
            )
        ).status_code == 400
        assert (await client.get("/learning/corpus")).json()["revision"] == 3
        current[0] = FOREIGN
        for method, path, foreign_body in [
            ("GET", "", None),
            ("GET", "/export", None),
            ("POST", "/records", body),
        ]:
            assert (
                await client.request(method, "/learning/corpus" + path, json=foreign_body)
            ).status_code == 404


async def test_real_browser_auth_origin_csrf_and_unavailable_pg():
    auth = BrowserAuth(lambda r: OWNER, identity=OWNER)
    runtime = SimpleNamespace(sop_registry=SimpleNamespace(catalog_version="complaints-v3"))
    app = create_app(Settings(mode="test"), trace=MemoryTrace(), identity_provider=auth)
    app.include_router(workbench_router(auth, lambda: {}))
    app.include_router(corpus_authoring_router(runtime, auth, OWNER, store=TransactionDouble()))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        csrf = (await client.get("/auth/session")).json()["csrf"]
        body = dict(revision=0, record=record().model_dump(mode="json"), source="合成来源")
        assert (await client.post("/learning/corpus/records", json=body)).status_code == 403
        assert (
            await client.post(
                "/learning/corpus/records",
                json=body,
                headers={"X-CSRF-Token": csrf, "Origin": "https://foreign.example"},
            )
        ).status_code == 403
        assert (
            await client.post("/learning/corpus/records", json=body, headers={"X-CSRF-Token": csrf})
        ).status_code == 200
    unavailable = create_app(Settings(mode="test"), trace=MemoryTrace())
    unavailable.include_router(corpus_authoring_router(runtime, lambda r: OWNER, OWNER))
    async with (
        unavailable.router.lifespan_context(unavailable),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=unavailable), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.get("/learning/corpus")).status_code == 501
