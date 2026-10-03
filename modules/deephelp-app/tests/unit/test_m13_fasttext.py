import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from deephelp_app.cascade import CascadePolicy
from deephelp_app.domain.models import (
    Decision,
    DenseResult,
    FallbackResult,
    FastTextPrediction,
    IntentCode,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.event_replay import envelope
from deephelp_app.execution import ExecutionBudget
from deephelp_app.fasttext_cli import bounded_train
from deephelp_app.fasttext_preprocess import FastTextPreprocessor
from deephelp_app.fasttext_runtime import (
    LABELS,
    FastTextClassifier,
    FastTextFallback,
    FastTextGate,
    activate,
    pointer_manifest,
)
from deephelp_app.fasttext_training import DATA, TrainingConfig, audit, calibrate
from deephelp_app.intent import IntentService
from deephelp_app.mvp_replay import REPLAY_SCOPE
from deephelp_app.text_entity import TextEntityProcessor

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp("m13") / "experiment"
    return bounded_train(root, DATA)


def classifier(trained, quantized=False):
    return FastTextClassifier(Path(trained["selected_quantized" if quantized else "selected_raw"]))


def test_real_binding_train_quantize_save_reload_and_dev_only_selection(trained):
    assert trained["counts"] == {"train": 40, "dev": 20, "test": 20}
    assert trained["source_and_variant_split_isolation"]
    assert trained["seed_supported"] == 42 and not trained["byte_reproducibility_claim"]
    raw, quant = classifier(trained), classifier(trained, True)
    assert not raw.model.is_quantized() and quant.model.is_quantized()
    assert quant.manifest.model_bytes < raw.manifest.model_bytes
    for model in (raw, quant):
        assert model.manifest.gate.calibrated
        assert set(model.model.get_labels()) == set(LABELS)
        assert model.manifest.train_digest != model.manifest.dev_digest
        assert model.manifest.dev_digest != model.manifest.test_digest


def test_shared_preprocessing_preserves_width_negation_and_ids():
    prep = FastTextPreprocessor()
    assert prep.prepare("订单００００３１　优惠券\n未到账 SKU-A7") == prep.prepare(
        "订单000031 优惠券 未到账 sku-a7"
    )
    assert "000031" in prep.tokens("订单000031")
    assert "不是" in prep.tokens("不是优惠未到账")
    assert "__label__" not in prep.prepare("__label__discount 订单")


@pytest.mark.parametrize("field", ["source_group", "variant_group", "text", "sample_id"])
def test_data_audit_rejects_split_leaks_or_duplicates(tmp_path, field):
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    train = next(r for r in rows if r["split"] == "train")
    dev = next(r for r in rows if r["split"] == "dev")
    dev[field] = train[field]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        audit(path)


@pytest.mark.parametrize(
    "changes",
    [
        {"synthetic": False},
        {"reviewed": False},
        {"label": "__label__parent"},
        {"split": "reference"},
    ],
)
def test_unreviewed_or_invalid_training_data_rejected(tmp_path, changes):
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    rows[0].update(changes)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        audit(path)


@pytest.mark.parametrize(
    "changes",
    [
        {"thread": 1},
        {"dim": 32},
        {"epoch": 121},
        {"bucket": 8193},
        {"seed": 43},
        {"lr": float("nan")},
    ],
)
def test_training_resource_and_qualified_binding_limits(changes):
    with pytest.raises(ValidationError):
        TrainingConfig(**changes)


@pytest.mark.parametrize(
    "text,reason",
    [
        ("", "empty_input"),
        (" \n！", "empty_input"),
        ("000031", "number_only"),
        ("x" * 2001, "input_too_long"),
        ("xxzyphylorn qvwxzz", "oov_input"),
    ],
)
def test_input_rejection_cannot_take_over(trained, text, reason):
    row = classifier(trained).predict(text)
    assert row.reason == reason and row.need_review and not row.is_actionable
    assert row.final_intent_code is None


@pytest.mark.parametrize(
    "label,probability",
    [("__label__new", 0.99), ("__label__discount", 1.1), ("__label__discount", float("nan"))],
)
def test_bad_native_predictions_fail_closed(trained, label, probability):
    model = classifier(trained)
    model.model = SimpleNamespace(
        predict=lambda *a, **k: ((label, *list(LABELS)[1:]), [probability, 0.01, 0.0, 0.0, 0.0]),
        get_word_id=lambda word: 0,
    )
    row = model.predict("订单优惠未享受")
    assert row.need_review and row.reason == "invalid_prediction" and not row.top_k


def test_low_confidence_and_uncalibrated_top1_need_review(trained):
    model = classifier(trained)
    model.model = SimpleNamespace(
        predict=lambda *a, **k: (tuple(LABELS), [0.4, 0.3, 0.1, 0.1, 0.1]),
        get_word_id=lambda word: 0,
    )
    row = model.predict("订单优惠未享受")
    assert row.top_k[0].raw_probability == 0.4 and row.need_review
    assert row.final_intent_code is None
    assert (
        model.predict("订单", gate=FastTextGate(threshold=0, margin=0)).reason
        == "uncalibrated_gate"
    )


@pytest.mark.parametrize("mutate", ["hash", "labels", "signature", "model", "binding"])
def test_corrupt_artifact_or_manifest_blocks_explicit_enable(trained, tmp_path, mutate):
    source = Path(trained["selected_raw"])
    shutil.copytree(source.parent, tmp_path / "artifact")
    path = tmp_path / "artifact/manifest.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    if mutate == "hash":
        row["model_sha256"] = "0" * 64
    elif mutate == "labels":
        row["labels"]["__label__discount"] = "COMPLAINT"
    elif mutate == "signature":
        row["preprocessing_signature"] = "0" * 64
    elif mutate == "binding":
        row["binding_version"] = "0.9.3"
    else:
        (path.parent / "model.bin").write_bytes(b"damaged")
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        FastTextClassifier(path)


def test_atomic_pointer_switch_rollback_and_failed_activation_preserves_state(trained, tmp_path):
    pointer = tmp_path / "active.json"
    raw, quant = Path(trained["selected_raw"]), Path(trained["selected_quantized"])
    activate(pointer, raw)
    activate(pointer, quant)
    assert pointer_manifest(pointer) == quant
    same = pointer.read_bytes()
    activate(pointer, quant)
    assert pointer.read_bytes() == same
    activate(pointer, Path(), rollback=True)
    assert pointer_manifest(pointer) == raw
    before = pointer.read_bytes()
    with pytest.raises(ConfigurationError):
        activate(pointer, tmp_path / "missing.json")
    assert pointer.read_bytes() == before


class NextFallback:
    def __init__(self):
        self.calls = 0

    async def decide(self, text, retrievals, budget, *, context=None):
        self.calls += 1
        budget.claim_attempt(retry=False)
        return FallbackResult(code=None, reason="unknown")


@pytest.mark.parametrize("has_order", [True, False])
async def test_same_600_cascade_fasttext_takeover_preserves_topk_and_skips_llm(trained, has_order):
    model = classifier(trained)
    text = "订单000031未享受优惠" if has_order else "订单满减未减免"
    budget = ExecutionBudget.start(10, 10, 0)
    prepared = await TextEntityProcessor().process(envelope(text, "m13"), budget)
    # The rule is removed to exercise the optional fallback integration in isolation.
    prepared = prepared.model_copy(update={"rule_matches": ()})
    # Use a known train sentence; this is a wiring assertion, not a heldout quality claim.
    prepared = prepared.model_copy(
        update={"clean": prepared.clean.model_copy(update={"cleaned_text": "订单满减未减免"})}
    )
    downstream = NextFallback()

    class EmptyRetrieval:
        async def retrieve(self, text, scope, budget, *, top_k=3):
            return DenseResult(scope=scope, hits=(), candidates=())

    service = IntentService(
        None,
        EmptyRetrieval(),
        REPLAY_SCOPE,
        policy=CascadePolicy(),
        fallback=FastTextFallback(model, downstream),
    )
    result = await service.recognize(prepared, budget)
    assert result.final_code == IntentCode.DISCOUNT_MISSING
    assert result.decision == (Decision.ACCEPT if has_order else Decision.CLARIFY)
    assert result.reason_code == "fasttext_selected"
    assert result.cascade_steps[-1].fasttext.is_actionable
    assert result.candidates[0].score <= 1 and result.candidates[0].score > 0.65
    assert downstream.calls == 0 and budget.attempts_used == 0


async def test_fasttext_missing_slot_clarifies_and_rejected_input_uses_existing_fallback(trained):
    model = classifier(trained)
    downstream = NextFallback()
    port = FastTextFallback(model, downstream)
    budget = ExecutionBudget.start(10, 10, 0)
    prepared = await TextEntityProcessor().process(envelope("xxzyphylorn qvwxzz", "m13"), budget)
    result = await port.decide(prepared, (), budget, context="trusted context")
    assert result.reason == "unknown" and result.fasttext.need_review
    assert downstream.calls == 1 and budget.attempts_used == 1


async def test_expired_shared_budget_stops_before_cpu_or_llm(trained):
    port = FastTextFallback(classifier(trained), NextFallback())
    budget = ExecutionBudget.start(10, 10, 0)
    prepared = await TextEntityProcessor().process(envelope("订单满减未减免", "m13"), budget)
    budget.deadline = asyncio.get_running_loop().time() - 1
    with pytest.raises(AppError):
        await port.decide(prepared, (), budget)
    assert port.next_fallback.calls == 0


def test_final_code_review_and_top1_cannot_contradict(trained):
    row = classifier(trained).predict("订单满减未减免")
    assert row.is_actionable and not row.need_review
    for changes in (
        {"need_review": True},
        {"final_intent_code": "COUPON_UNUSABLE"},
        {"unknown": True},
        {"is_actionable": False},
    ):
        with pytest.raises(ValidationError):
            FastTextPrediction.model_validate(row.model_copy(update=changes).model_dump())


def test_fasttext_fallback_cannot_fabricate_actionable_selection():
    with pytest.raises(ValidationError):
        FallbackResult(code=IntentCode.DISCOUNT_MISSING, reason="supported", source="fasttext")


def test_no_safe_dev_gate_disables_even_epsilon_above_one(trained):
    model = classifier(trained)
    model.model = SimpleNamespace(
        predict=lambda *a, **k: (tuple(LABELS), [1.00001, 0.00001, 0.00001, 0.00001, 0.00001]),
        get_word_id=lambda word: 0,
    )
    rows = [{"label": "__label__coupon", "text": "订单优惠券"}]
    gate = calibrate(model, rows)
    assert gate.threshold == 1.0001
    result = model.predict(rows[0]["text"], gate=gate)
    assert not result.is_actionable and result.need_review


async def test_cancellation_waits_for_bounded_cpu_job_and_does_not_call_llm(trained):
    import threading

    model = classifier(trained)
    original = model.predict
    started, release = threading.Event(), threading.Event()

    def slow(text):
        started.set()
        assert release.wait(2)
        return original(text)

    model.predict = slow
    downstream = NextFallback()
    port = FastTextFallback(model, downstream)
    budget = ExecutionBudget.start(5, 10, 0)
    text = await TextEntityProcessor().process(envelope("订单满减未减免", "m13"), budget)
    task = asyncio.create_task(port.decide(text, (), budget))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert downstream.calls == 0
    # Both permits return; cancellation does not leave an accumulating CPU queue.
    assert port.slots._value == 2
