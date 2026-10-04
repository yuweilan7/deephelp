import asyncio
import copy
import csv
import json
import zipfile
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from deephelp_app.adapters.milvus_dense import FIELDS, MilvusDenseStore, activate, rollback
from deephelp_app.application.corpus import REQUIRED, read_corpus, validate_records
from deephelp_app.application.dense import (
    Capacity,
    DenseImporter,
    DenseRetriever,
    atomic_json,
    evaluate_dev,
    load_json,
)
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    DenseHit,
    DenseResult,
    DenseScope,
    EmbeddingResult,
    EmbeddingSignature,
    ErrorCode,
    IntentCode,
    ModelUsage,
)
from deephelp_tools.evaluation.corpus_preview import frozen_preview

pytestmark = pytest.mark.unit
SIGNATURE = EmbeddingSignature(provider="synthetic", model="offline-only", dimension=3)
SCOPE = DenseScope(namespace="synthetic", dataset_version="unit-v1", signature=SIGNATURE)
CAPACITY = Capacity(40 * 1024**3, 100 * 1024**2, 200 * 1024**2, 5 * 1024**3, 4 * 1024**3)


def record(doc_id="001", content="优惠没有享受", split="reference", **kwargs):
    return {
        "doc_id": doc_id,
        "content": content,
        "intent_code": "DISCOUNT_MISSING",
        "split": split,
        "source_group": "source-" + str(doc_id),
        "variant_group": "variant-" + str(doc_id),
        "synthetic": True,
        **kwargs,
    }


def preview(rows=None):
    rows = rows or [record(), record("002", "优惠券无法使用", intent_code="COUPON_UNUSABLE")]
    return validate_records(list(enumerate(rows, 1)), "a" * 64)


def budget(timeout=10):
    return ExecutionBudget.start(timeout, 300, 0)


async def capacity():
    return CAPACITY


class Embeddings:
    calls = 0
    signature = SIGNATURE
    vector = [1.0, 0.0, 0.0]

    async def embed(self, texts, run_budget):
        run_budget.claim_attempt(retry=False)
        self.calls += 1
        return EmbeddingResult(
            signature=self.signature,
            vectors=[list(self.vector) for _ in texts],
            usage=ModelUsage(input_tokens=1, output_tokens=0),
        )


class Store:
    def __init__(self):
        self.rows, self.versions = {}, {}
        self.writes, self.fail_write = 0, None
        self.search_limits = []

    async def ensure(self, scope, corpus_digest):
        if scope.fingerprint in self.versions and self.versions[scope.fingerprint] != corpus_digest:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Version changed")
        self.versions[scope.fingerprint] = corpus_digest
        self.rows.setdefault(scope.fingerprint, {})

    async def validate(self, scope, corpus_digest=None):
        current = self.versions[scope.fingerprint]
        if corpus_digest and current != corpus_digest:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Version changed")
        return current

    async def write(self, scope, rows):
        self.writes += 1
        self.rows[scope.fingerprint].update({r["doc_id"]: copy.deepcopy(r) for r in rows})
        if self.writes == self.fail_write:
            raise AppError(ErrorCode.TIMEOUT, "Lost acknowledgement")

    async def read(self, scope, ids):
        return [
            copy.deepcopy(self.rows[scope.fingerprint][i])
            for i in ids
            if i in self.rows[scope.fingerprint]
        ]

    async def finish(self, scope):
        pass

    async def count(self, scope, filtered=True):
        return len(self.rows[scope.fingerprint])

    async def search(self, scope, vector, limit, code=None):
        self.search_limits.append((limit, code))
        rows = [
            r
            for r in self.rows[scope.fingerprint].values()
            if code is None or r["intent_code"] == code
        ]
        return [
            DenseHit(
                scope=scope,
                doc_id=r["doc_id"],
                intent_code=r["intent_code"],
                content=r["content"],
                raw_score=0.8,
                metadata=r["metadata"],
            )
            for r in rows[:limit]
        ]


def test_frozen_manifest_has_six_index_records_and_disjoint_query_groups():
    data = frozen_preview()
    data.require_valid()
    assert len(data.index_records) == 6
    assert len([r for r in data.records if r.split == "dev"]) == 12
    assert {r.source_group for r in data.index_records}.isdisjoint(
        {r.source_group for r in data.records if r.split not in {"train", "reference"}}
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"intent_code": "UNKNOWN"},
        {"intent_code": "SERVICE"},
        {"content": " "},
        {"content": "x" * 2001},
        {"doc_id": 7},
        {"doc_id": "中" * 50},
        {"synthetic": False},
        {"label_path": ["service", "order", "activity_query"]},
        {"metadata": {"x": "y" * 513}},
        {"unexpected": "no"},
    ],
)
def test_record_invalid_values_reported_without_input_values(changes):
    data = preview([record(**changes)])
    assert data.errors and "row 1" in data.errors[0]
    with pytest.raises(ValueError, match="preview failed"):
        data.require_valid()


@pytest.mark.parametrize(
    "second",
    [
        record(content="不同句子"),
        record("002", "优惠没有享受"),
        record("002", " 优惠没有享受 ", split="dev"),
        record("002", "不同句子", split="dev", source_group="source-001"),
        record("002", "不同句子", split="test", variant_group="variant-001"),
    ],
)
def test_duplicates_and_cross_split_groups_rejected(second):
    assert preview([record(), second]).errors


def test_jsonl_csv_roundtrip_and_blank_bad_lines(tmp_path):
    rows = [record(), record("002", "别的句子", split="dev")]
    jsonl = tmp_path / "input.jsonl"
    jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    first = read_corpus(jsonl)
    first.require_valid()
    target = tmp_path / "input.csv"
    with target.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({**r, "synthetic": "true"} for r in rows)
    second = read_corpus(target)
    second.require_valid()
    assert first.records == second.records and first.file_sha256 != second.file_sha256
    assert first.index_digest == second.index_digest
    jsonl.write_text("\nnot-json\n{}", encoding="utf-8")
    assert len(read_corpus(jsonl).errors) == 3


@pytest.mark.parametrize("body", ["content,split\nx,train\n", "doc_id,doc_id\nx,y\n"])
def test_csv_missing_duplicate_headers(tmp_path, body):
    path = tmp_path / "x.csv"
    path.write_text(body, encoding="utf-8")
    assert "headers" in read_corpus(path).errors[0]


def xlsx(path, *, formula=False, numeric=False):
    from xml.sax.saxutils import escape

    mapping = {k: chr(65 + i) for i, k in enumerate(sorted(REQUIRED))}
    data, cells = record(), []
    for key, column in mapping.items():
        value = "true" if key == "synthetic" else data[key]
        if formula and key == "content":
            cells.append(f'<c r="{column}1"><f>1+1</f><v>2</v></c>')
        elif numeric and key == "doc_id":
            cells.append(f'<c r="{column}1"><v>1</v></c>')
        else:
            cells.append(f'<c r="{column}1" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="data" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1">' + "".join(cells) + "</row></sheetData></worksheet>",
        )
    return mapping


@pytest.mark.parametrize("formula,numeric", [(False, False), (True, False), (False, True)])
def test_headerless_xlsx_mapping_never_executes_formulas(tmp_path, formula, numeric):
    path = tmp_path / "x.xlsx"
    mapping = xlsx(path, formula=formula, numeric=numeric)
    data = read_corpus(path, columns=mapping)
    if formula or numeric:
        assert data.errors
    else:
        data.require_valid()
        assert data.records[0].doc_id == "001"
    assert read_corpus(path).errors


@pytest.mark.parametrize(
    "field,value",
    [
        ("root_free", 10 * 1024**3),
        ("milvus_data", 6 * 1024**3),
        ("memory_limit", 0),
        ("memory_used", 4 * 1024**3),
        ("host_available", 100),
    ],
)
def test_capacity_stops_admission(field, value):
    from dataclasses import replace

    with pytest.raises(AppError, match="capacity gate"):
        replace(CAPACITY, **{field: value}).admit(6, 1024)


async def test_idempotent_readback_and_lost_ack_resume(tmp_path):
    port, store = Embeddings(), Store()
    store.fail_write = 2
    importer = DenseImporter(port, store, capacity, batch_size=1)
    data, manifest = preview(), tmp_path / "import.json"
    with pytest.raises(AppError, match="Lost acknowledgement"):
        await importer.import_corpus(data, SCOPE, budget(), manifest)
    state = load_json(manifest)
    assert state["completed_ids"] == ["001"] and state["pending_ids"] == ["002"]
    assert len(store.rows[SCOPE.fingerprint]) == 2
    assert not manifest.with_suffix(".lock").exists()
    await importer.import_corpus(data, SCOPE, budget(), manifest)
    assert port.calls == 3
    await importer.import_corpus(data, SCOPE, budget(), manifest)
    assert port.calls == 3 and len(store.rows[SCOPE.fingerprint]) == 2
    assert load_json(manifest)["status"] == "VERIFIED"


async def test_completed_manifest_does_not_hide_tampered_or_missing_remote_data(tmp_path):
    port, store = Embeddings(), Store()
    importer, manifest = DenseImporter(port, store, capacity), tmp_path / "import.json"
    await importer.import_corpus(preview(), SCOPE, budget(), manifest)
    store.rows[SCOPE.fingerprint]["001"]["content"] = "tampered"
    with pytest.raises(AppError, match="mismatch"):
        await importer.import_corpus(preview(), SCOPE, budget(), manifest)
    assert port.calls == 1
    del store.rows[SCOPE.fingerprint]["001"]
    with pytest.raises(AppError, match="count mismatch"):
        await importer.import_corpus(preview(), SCOPE, budget(), manifest)


async def test_signature_same_dimension_mismatch_never_writes(tmp_path):
    port, store = Embeddings(), Store()
    port.signature = SIGNATURE.model_copy(update={"model": "other-model"})
    with pytest.raises(AppError, match="signature"):
        await DenseImporter(port, store, capacity).import_corpus(
            preview(), SCOPE, budget(), tmp_path / "i.json"
        )
    assert store.writes == 0
    assert (
        SCOPE.model_copy(update={"signature": port.signature}).collection_name
        != SCOPE.collection_name
    )


@pytest.mark.parametrize("vector", [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
async def test_zero_or_unnormalized_embedding_rejected(tmp_path, vector):
    port, store = Embeddings(), Store()
    port.vector = vector
    with pytest.raises(AppError):
        await DenseImporter(port, store, capacity).import_corpus(
            preview(), SCOPE, budget(), tmp_path / "i.json"
        )
    assert store.writes == 0


async def test_manifest_binding_version_and_dev_only_rejected(tmp_path):
    port, store = Embeddings(), Store()
    importer, manifest = DenseImporter(port, store, capacity), tmp_path / "i.json"
    await importer.import_corpus(preview(), SCOPE, budget(), manifest)
    with pytest.raises(AppError, match="binding changed"):
        await importer.import_corpus(
            preview(), SCOPE.model_copy(update={"dataset_version": "v2"}), budget(), manifest
        )
    with pytest.raises(ValueError, match="No authorized"):
        await importer.import_corpus(preview([record(split="dev")]), SCOPE, budget(), manifest)
    with pytest.raises(AppError, match="Version changed"):
        await importer.import_corpus(
            preview([record(content="另一个版本")]), SCOPE, budget(), tmp_path / "new.json"
        )


async def test_capacity_failure_between_batches_keeps_completed_ids(tmp_path):
    checks = 0

    async def declining():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "capacity unavailable")
        return CAPACITY

    store = Store()
    with pytest.raises(AppError, match="capacity"):
        await DenseImporter(Embeddings(), store, declining, batch_size=1).import_corpus(
            preview(), SCOPE, budget(), tmp_path / "i.json"
        )
    assert store.writes == 1
    assert load_json(tmp_path / "i.json")["completed_ids"] == ["001"]


async def test_cancelled_writer_keeps_pending_batch_and_cleans_lock(tmp_path):
    event, store = asyncio.Event(), Store()
    real_write = store.write

    async def slow_write(scope, rows):
        await real_write(scope, rows)
        event.set()
        await asyncio.Event().wait()

    store.write = slow_write
    manifest = tmp_path / "i.json"
    task = asyncio.create_task(
        DenseImporter(Embeddings(), store, capacity).import_corpus(
            preview(), SCOPE, budget(), manifest
        )
    )
    await event.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manifest.with_suffix(".lock").exists()
    assert load_json(manifest)["pending_ids"] == ["001", "002"]
    store.write = real_write
    await DenseImporter(Embeddings(), store, capacity).import_corpus(
        preview(), SCOPE, budget(), manifest
    )


async def test_retrieval_each_class_independent_best_match_and_dto_roundtrip(tmp_path):
    port, store = Embeddings(), Store()
    rows = [record(str(i), "不同样本" + str(i)) for i in range(8)]
    rows.append(record("coupon", "券不可用", intent_code="COUPON_UNUSABLE"))
    await DenseImporter(port, store, capacity).import_corpus(
        preview(rows), SCOPE, budget(), tmp_path / "i.json"
    )
    result = await DenseRetriever(port, store).retrieve("query", SCOPE, budget(), top_k=3)
    assert all(h.intent_code == IntentCode.DISCOUNT_MISSING for h in result.hits)
    assert {c.intent_code for c in result.candidates} == {
        IntentCode.DISCOUNT_MISSING,
        IntentCode.COUPON_UNUSABLE,
    }
    assert store.search_limits[-3:] == [
        (1, c)
        for c in (
            IntentCode.DISCOUNT_MISSING,
            IntentCode.COUPON_UNUSABLE,
            IntentCode.ORDER_ACTIVITY_QUERY,
        )
    ]
    assert DenseResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("text,top_k", [(" ", 3), ("x" * 2001, 3), ("ok", 0), ("ok", 21)])
async def test_invalid_query_does_not_embed(text, top_k):
    port = Embeddings()
    with pytest.raises(ValueError):
        await DenseRetriever(port, Store()).retrieve(text, SCOPE, budget(), top_k=top_k)
    assert port.calls == 0


async def test_dev_observations_without_takeover_threshold(tmp_path):
    port, store = Embeddings(), Store()
    data = preview([record(), record("query", "未享优惠，请查原因", split="dev")])
    await DenseImporter(port, store, capacity).import_corpus(
        data, SCOPE, budget(), tmp_path / "i.json"
    )
    report = await evaluate_dev(data, SCOPE, DenseRetriever(port, store), budget())
    assert report["metrics"]["doc_recall_1"] == 1
    assert report["threshold"] is None and report["final_intent_decisions"] == 0


async def test_activation_rollback_failed_target_preserves_pointer(tmp_path):
    store, port = Store(), Embeddings()
    pointer, old_manifest, new_manifest = (
        tmp_path / "active.json",
        tmp_path / "old.json",
        tmp_path / "new.json",
    )
    other = SCOPE.model_copy(update={"dataset_version": "v2"})
    for scope, manifest in ((SCOPE, old_manifest), (other, new_manifest)):
        await DenseImporter(port, store, capacity).import_corpus(
            preview(), scope, budget(), manifest
        )
        await activate(store, scope, manifest, pointer)
    assert load_json(pointer)["active"] == other.model_dump(mode="json")
    await rollback(store, pointer)
    assert load_json(pointer)["active"] == SCOPE.model_dump(mode="json")
    assert load_json(pointer)["previous"]["active"] == other.model_dump(mode="json")
    del store.rows[other.fingerprint]["001"]
    before = pointer.read_bytes()
    with pytest.raises(ConfigurationError, match="readback"):
        await rollback(store, pointer)
    assert pointer.read_bytes() == before


def test_scope_namespace_and_byte_limits():
    for changes in ({"namespace": 'x" or true'}, {"dataset_version": "中" * 50}):
        with pytest.raises(ValidationError):
            DenseScope.model_validate({**SCOPE.model_dump(), **changes})
    assert SCOPE.model_copy(update={"namespace": "other"}).collection_name != SCOPE.collection_name


async def test_milvus_schema_signature_and_field_types_checked():
    client = AsyncMock()
    fields = [
        {
            "name": k,
            "type": int(kind),
            "params": {"max_length": length},
            "is_primary": k == "doc_id",
        }
        for k, (kind, length) in FIELDS.items()
    ]
    fields.extend(
        [
            {"name": "vector", "type": 101, "params": {"dim": 3}},
            {"name": "metadata", "type": 23, "params": {}},
        ]
    )
    details = {
        "description": json.dumps(
            {
                "format": "m05-dense-fp32-v1",
                "scope": SCOPE.model_dump(mode="json"),
                "corpus_digest": "a",
            }
        ),
        "auto_id": False,
        "enable_dynamic_field": False,
        "fields": fields,
    }
    client.describe_collection.return_value = details
    store = MilvusDenseStore(client, budget())
    assert await store.validate(SCOPE, "a") == "a"
    with pytest.raises(AppError, match="mismatch"):
        await store.validate(
            SCOPE.model_copy(update={"signature": SIGNATURE.model_copy(update={"model": "other"})})
        )
    fields[-2]["type"] = 102
    with pytest.raises(AppError, match="mismatch"):
        await store.validate(SCOPE)


def test_filter_quotes_versions_and_restricts_index_splits():
    query = MilvusDenseStore.filter(
        SCOPE.model_copy(update={"dataset_version": 'x" or true'}), IntentCode.COUPON_UNUSABLE
    )
    assert 'dataset_version == "x\\" or true"' in query
    assert 'split in ["reference", "train"]' in query
    assert "model_signature" in query and "namespace" in query


def test_atomic_manifest_size_bounds(tmp_path):
    path = tmp_path / "state.json"
    atomic_json(path, {"x": 1})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="size"):
        atomic_json(path, {"x": "y" * 3 * 1024**2})
    assert path.read_bytes() == before


def test_live_control_paths_cannot_overwrite_budget_or_share_lock(monkeypatch, tmp_path):
    from deephelp_tools.evaluation.dense_cli import control_paths

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigurationError, match="must differ"):
        control_paths([".local/b.json", ".local/m.json", ".local/p.json", ".local/b.json"])
    with pytest.raises(ConfigurationError, match="must differ"):
        control_paths([".local/b.json", ".local/b.lock", ".local/p.json", ".local/o.json"])


async def test_unverified_or_content_corrupt_collection_cannot_activate(tmp_path):
    port, store = Embeddings(), Store()
    manifest, pointer = tmp_path / "m.json", tmp_path / "active.json"
    await DenseImporter(port, store, capacity).import_corpus(preview(), SCOPE, budget(), manifest)
    store.rows[SCOPE.fingerprint]["001"]["content"] = "tampered"
    with pytest.raises(AppError, match="hash mismatch"):
        await activate(store, SCOPE, manifest, pointer)
    assert not pointer.exists()


def test_csv_formula_and_blank_line_are_reported(tmp_path):
    path = tmp_path / "formula.csv"
    row = record(content="=1+1")
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow({**row, "synthetic": "true"})
        f.write("\n")
    assert len(read_corpus(path).errors) >= 2


async def test_sdk_timeout_none_honors_zero_retries():
    from pymilvus import MilvusException
    from pymilvus.decorators import retry_on_rpc_failure

    attempts = 0

    @retry_on_rpc_failure()
    async def failing(self, **kwargs):
        nonlocal attempts
        attempts += 1
        raise MilvusException(code=8, message="synthetic rate limit")

    with pytest.raises(MilvusException):
        await failing(object(), timeout=None, retry_times=0)
    assert attempts == 1


async def test_full_vector_hash_blocks_normalized_but_changed_data(tmp_path):
    port, store = Embeddings(), Store()
    manifest, pointer = tmp_path / "m.json", tmp_path / "active.json"
    state = await DenseImporter(port, store, capacity).import_corpus(
        preview(), SCOPE, budget(), manifest
    )
    assert set(state["vector_hashes"]) == {"001", "002"}
    # Dimension and norm remain valid; persistence must still reject changed components.
    store.rows[SCOPE.fingerprint]["001"]["vector"] = [0.0, 1.0, 0.0]
    with pytest.raises(AppError, match="vector hash"):
        await activate(store, SCOPE, manifest, pointer)
    assert not pointer.exists()
    with pytest.raises(AppError, match="vector hash"):
        await DenseImporter(port, store, capacity).import_corpus(
            preview(), SCOPE, budget(), manifest
        )
    assert port.calls == 1


async def test_verified_readback_does_not_repeat_flush(tmp_path):
    port, store = Embeddings(), Store()
    store.finish = AsyncMock()
    manifest = tmp_path / "m.json"
    importer = DenseImporter(port, store, capacity)
    await importer.import_corpus(preview(), SCOPE, budget(), manifest)
    await importer.import_corpus(preview(), SCOPE, budget(), manifest)
    assert store.finish.await_count == 1
    assert store.writes == port.calls == 1


def test_atomic_state_retries_transient_destination_lock(monkeypatch, tmp_path):
    from pathlib import Path

    path = tmp_path / "state.json"
    original = Path.replace
    attempts = 0

    def briefly_locked(self, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("synthetic transient scanner lock")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", briefly_locked)
    monkeypatch.setattr("deephelp_app.application.dense.time.sleep", lambda _: None)
    atomic_json(path, {"attempts": 3})
    assert load_json(path) == {"attempts": 3}
    assert attempts == 3


async def test_missing_completed_vector_hash_cannot_be_reblessed(tmp_path):
    port, store = Embeddings(), Store()
    manifest = tmp_path / "m.json"
    state = await DenseImporter(port, store, capacity).import_corpus(
        preview(), SCOPE, budget(), manifest
    )
    state["vector_hashes"].pop("001")
    atomic_json(manifest, state)
    with pytest.raises(ValueError, match="completed IDs"):
        await DenseImporter(port, store, capacity).import_corpus(
            preview(), SCOPE, budget(), manifest
        )
    assert port.calls == store.writes == 1
