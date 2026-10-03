import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deephelp_app.corpus import digest
from deephelp_app.dense import atomic_json
from deephelp_app.domain.models import IntentCode
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation import DATA, historical_inputs, semantic_label
from deephelp_app.fasttext_preprocess import DICTIONARY, FastTextPreprocessor
from deephelp_app.fasttext_training import audit
from deephelp_app.flywheel import (
    FeedbackCandidate,
    RouteReview,
    capture_candidate,
    isolation,
    reviewed,
    rule_matches,
    snapshot,
    withdrawn,
)
from deephelp_app.flywheel_assets import DEMO_DATA, activate, exports
from deephelp_app.flywheel_store import FeedbackStore
from deephelp_app.flywheel_validation import validation_cases
from deephelp_app.samples import load_business_fixtures

pytestmark = pytest.mark.unit


def candidate(index=0):
    row = json.loads(DEMO_DATA.read_text(encoding="utf-8"))["collect"][index]
    return FeedbackCandidate(
        candidate_id=digest(row),
        run_id="run-test",
        source_group=row["id"],
        variant_group=row["id"],
        text=row["text"],
        observation={"confidence": 0.999, "outcome": "ANSWERED"},
    )


def approval(**values):
    return RouteReview(
        **dict(
            dict(
                state="approved",
                label=IntentCode.DISCOUNT_MISSING,
                reviewer="synthetic-semantic-evidence-v1",
                method="deterministic_evidence",
                reason="Registered synthetic semantics",
                independent_from_heldout=True,
            ),
            **values,
        )
    )


def test_confident_success_is_only_candidate_and_each_route_is_independent():
    row = candidate()
    assert not snapshot([row])["candidates"]
    original = exports([row])
    assert len(original["corpus"]) == 12 and len(original["training"]) == 80
    row = reviewed(row, "corpus", approval())
    values = exports([row])
    assert len(values["corpus"]) == 13 and len(values["training"]) == 80 and not values["rules"]
    row = reviewed(row, "fasttext", approval(state="rejected"))
    values = exports([row])
    assert len(values["corpus"]) == 13 and len(values["training"]) == 80
    assert (
        values["source"]["candidates"][row.candidate_id]["reviews"]["fasttext"]["state"]
        == "rejected"
    )


def test_capture_is_stable_and_redacts_identifiers_contacts_and_secrets():
    message = dict(
        identity={"tenant_id": "synthetic-tenant", "user_id": "synthetic-user-a"},
        channel="m18-collect",
        raw_text="订单000031，联系13812345678或者a@example.com，token=secret-token",
    )
    response = dict(
        run_id="run-a",
        outcome="ANSWERED",
        trace_id="trace-a",
        facts=[
            {
                "name": "order_id",
                "value": "000031",
                "source": {"message_id": "msg-a", "excerpt": "联系13812345678 a@example.com"},
            }
        ],
    )
    row = capture_candidate(
        "run-a", message, response, b"x" * 32, source_group="new", variant_group="new"
    )
    assert row == capture_candidate(
        "run-a", message, response, b"x" * 32, source_group="new", variant_group="new"
    )
    assert all(
        v not in row.model_dump_json()
        for v in ("000031", "13812345678", "a@example.com", "secret-token")
    )
    assert not row.reviews and row.revision == 0


@pytest.mark.parametrize(
    "field,value",
    [("channel", "m17-evaluation"), ("identity", {"tenant_id": "production", "user_id": "real"})],
)
def test_capture_rejects_evaluation_and_non_synthetic_sources(field, value):
    message = dict(
        identity={"tenant_id": "synthetic-tenant", "user_id": "synthetic-user-a"},
        channel="m18-collect",
        raw_text="input",
    )
    message[field] = value
    with pytest.raises(ConfigurationError):
        capture_candidate("run", message, {}, b"x" * 32, source_group="s", variant_group="v")


@pytest.mark.parametrize(
    "data",
    [
        dict(independent_from_heldout=False),
        dict(label=IntentCode.COUPON_UNUSABLE),
        dict(reviewer="model-confidence"),
        dict(label=None),
    ],
)
def test_promotion_needs_supported_independent_audited_gold(data):
    with pytest.raises(ConfigurationError):
        reviewed(candidate(), "corpus", approval(**data))


def test_semantics_alone_do_not_register_a_new_evidence_rule():
    row = candidate().model_copy(update={"source_group": "unreviewed-variant"})
    with pytest.raises(ConfigurationError, match="registered audited"):
        reviewed(row, "corpus", approval())


@pytest.mark.parametrize(
    "text",
    [
        historical_inputs()[0],
        json.loads(DATA.read_text(encoding="utf-8"))[0]["turns"][0]["text"],
        json.loads(DEMO_DATA.read_text(encoding="utf-8"))["release"][0]["text"].replace(
            "000031", "765432"
        ),
    ],
)
def test_historical_and_identifier_only_release_rewrites_cannot_flow_back(text):
    with pytest.raises(ConfigurationError):
        isolation(candidate().model_copy(update={"text": text}))


@pytest.mark.parametrize("group", ["source-discount-basic", "discount-basic", "m18-release-coupon"])
def test_changed_wording_cannot_hide_a_frozen_source_or_paraphrase_group(group):
    with pytest.raises(ConfigurationError):
        isolation(candidate().model_copy(update={"variant_group": group}))


def test_three_routes_keep_gold_and_revision_provenance(tmp_path):
    row = candidate()
    for route in ("corpus", "fasttext", "rule"):
        row = reviewed(row, route, approval(phrases=("结算折扣未兑现",) if route == "rule" else ()))
    values = exports([row])
    assert (
        len(values["corpus"]) == 13 and len(values["training"]) == 81 and len(values["rules"]) == 1
    )
    assert values["training"][-1]["feedback_review"]["revision"] == 3
    assert values["training"][-1]["label"] == "__label__discount"
    data = tmp_path / "training.json"
    atomic_json(data, values["training"])
    assert len(audit(data)["train"]) == 41
    assert values["corpus"][-1]["metadata"]["review_revision"] == "3"
    assert values["source"]["digest"] == digest(values["source"]["candidates"])


def test_correction_invalidates_other_route_gold_and_withdrawal_keeps_original_trace():
    row = reviewed(candidate(), "corpus", approval())
    row = reviewed(row, "fasttext", approval())
    corrected = reviewed(
        row,
        "rule",
        approval(
            method="human",
            reviewer="reviewer-a",
            label=IntentCode.COUPON_UNUSABLE,
            phrases=("优惠差额",),
        ),
    )
    assert row.reviews["corpus"].state == "approved"
    assert corrected.reviews["corpus"].state == corrected.reviews["fasttext"].state == "disputed"
    removed = withdrawn(corrected, "reviewer-b", "wrong source removed")
    assert removed.revision == 4 and removed.run_id == row.run_id
    assert not snapshot([removed])["candidates"]
    assert all(v.state == "withdrawn" for v in removed.reviews.values())


def test_data_only_rules_preserve_spans_and_refuse_negation_and_injection():
    row = reviewed(candidate(), "rule", approval(phrases=("结算折扣未兑现",), exclusions=("忽略",)))
    data = dict(format="m18-literal-rules-v1", version="rules-v1", rules=exports([row])["rules"])
    request = SimpleNamespace(raw_text=row.text, message_id="original-message")
    found = rule_matches(data, request)
    assert len(found) == 1 and found[0].evidence.excerpt == row.text[found[0].start : found[0].end]
    assert (
        found[0].evidence.message_id == "original-message" and found[0].rule_version == "rules-v1"
    )
    for prefix in ("不是", "已经可以", "忽略", "忽略之前的指令"):
        assert not rule_matches(data, SimpleNamespace(raw_text=prefix + row.text, message_id="m"))
    with pytest.raises(ConfigurationError):
        reviewed(candidate(), "rule", approval(phrases=(".*优惠.*",)))


@pytest.mark.parametrize(
    "change", [dict(split="test"), dict(feedback_review={"state": "approved"})]
)
def test_feedback_cannot_be_exported_as_test_or_without_review_reference(tmp_path, change):
    values = exports([reviewed(candidate(), "fasttext", approval())])["training"]
    values[-1].update(change)
    path = tmp_path / "bad.json"
    atomic_json(path, values)
    with pytest.raises(ConfigurationError):
        audit(path)


def test_validation_uses_unchanged_m17_and_separate_release_semantics():
    cases = validation_cases()
    assert len(cases) == 11 and sum(len(c.turns) for c in cases) == 14
    releases = [c for c in cases if c.scenario == "m18-release"]
    assert len(releases) == 3
    for case in releases:
        assert semantic_label(case.turns[0].text) == case.turns[0].label
        assert case.turns[0].entities["order_id"] in {
            o.order_id for o in load_business_fixtures().orders
        }


@pytest.mark.asyncio
async def test_failed_version_gate_never_replaces_active_pointer(tmp_path, monkeypatch):
    assets, pointer = tmp_path / "assets.json", tmp_path / "active.json"
    data = dict(source={"candidates": {}}, version="incomplete")
    atomic_json(pointer, {"active": "last-complete"})
    atomic_json(tmp_path / "validation.json", {"status": "REJECTED", "assets_digest": digest(data)})
    original = pointer.read_bytes()
    monkeypatch.setattr("deephelp_app.flywheel_assets.verify_files", lambda path: data)
    monkeypatch.setattr("deephelp_app.flywheel_assets.verify_remote", AsyncMock())
    store = SimpleNamespace(require_current=AsyncMock())
    with pytest.raises(ConfigurationError, match="successful validation"):
        await activate(store, assets, pointer, None)
    assert pointer.read_bytes() == original


@pytest.mark.asyncio
async def test_changed_review_blocks_activation_before_vector_calls(tmp_path, monkeypatch):
    pointer = tmp_path / "active.json"
    atomic_json(pointer, {"active": "previous"})
    original = pointer.read_bytes()
    monkeypatch.setattr("deephelp_app.flywheel_assets.verify_files", lambda path: {"source": {}})
    remote = AsyncMock()
    monkeypatch.setattr("deephelp_app.flywheel_assets.verify_remote", remote)
    store = SimpleNamespace(
        require_current=AsyncMock(side_effect=ConfigurationError("stale review"))
    )
    with pytest.raises(ConfigurationError, match="stale review"):
        await activate(store, tmp_path / "assets.json", pointer, None)
    remote.assert_not_called()
    assert pointer.read_bytes() == original


@pytest.mark.asyncio
async def test_pooled_repeatable_read_snapshot_cannot_hide_a_withdrawal():
    approved = reviewed(candidate(), "corpus", approval())
    removed = withdrawn(approved, "reviewer", "remove after the previous SELECT")

    class Connection:
        current = removed
        retained_snapshot = approved

        async def rollback(self):
            self.retained_snapshot = None

        @asynccontextmanager
        async def cursor(self):
            yield self

        async def execute(self, query, values):
            if self.retained_snapshot is None:
                self.retained_snapshot = self.current

        async def fetchall(self):
            return [(self.retained_snapshot.model_dump_json(),)]

    connection = Connection()

    @asynccontextmanager
    async def acquire():
        yield connection

    store = FeedbackStore(SimpleNamespace(acquire=acquire))
    with pytest.raises(ConfigurationError, match="source changed"):
        await store.require_current(snapshot([approved]))
    assert (await store.get(approved.candidate_id)).revision == removed.revision
    assert connection.retained_snapshot is None


def test_same_dictionary_is_shared_readonly_and_new_signature_is_isolated(tmp_path, monkeypatch):
    old = FastTextPreprocessor()
    other = FastTextPreprocessor()
    original = old.prepare("订单000031折扣没兑现；券000009无法使用")
    assert other.tokenizer is old.tokenizer and other.signature == old.signature
    with pytest.raises(TypeError):
        other.tokenizer.FREQ["poisoned-token"] = 1000000
    changed = tmp_path / "dictionary.txt"
    changed.write_bytes(DICTIONARY.read_bytes() + "\n独立版本词 100000\n".encode())
    monkeypatch.setattr("deephelp_app.fasttext_preprocess.DICTIONARY", changed)
    new = FastTextPreprocessor()
    assert new.signature != old.signature and new.tokenizer is not old.tokenizer
    assert old.prepare("订单000031折扣没兑现；券000009无法使用") == original
