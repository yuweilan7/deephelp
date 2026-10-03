"""Validated local artifacts and one optional adapter for the existing FallbackPort."""

import asyncio
import hashlib
import importlib.metadata
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from deephelp_app.dense import atomic_json, load_json
from deephelp_app.domain.models import (
    FASTTEXT_LABELS,
    DenseResult,
    FallbackResult,
    FastTextCandidate,
    FastTextPrediction,
    IntentCode,
    TextEntityResult,
)
from deephelp_app.errors import ConfigurationError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.fasttext_preprocess import FastTextPreprocessor, digest
from deephelp_app.ports import FallbackPort

LABELS = FASTTEXT_LABELS
BINDING = "0.11.8"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FastTextGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    threshold: float = Field(ge=0, le=1.0001, allow_inf_nan=False)
    margin: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_known_ratio: float = Field(default=0.35, ge=0, le=1, allow_inf_nan=False)
    calibrated: bool = False


class FastTextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["m13-fasttext-v1"] = "m13-fasttext-v1"
    version: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    binding: Literal["fasttext-community"] = "fasttext-community"
    binding_version: Literal["0.11.8"] = "0.11.8"
    registry: Literal["complaints-v1"] = "complaints-v1"
    synthetic: Literal[True] = True
    model_file: Literal["model.bin", "model.ftz"]
    model_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_bytes: int = Field(gt=0, le=32 * 1024 * 1024)
    quantized: bool
    preprocessing: dict[str, Any]
    preprocessing_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    labels: dict[str, IntentCode | None]
    train_digest: str
    dev_digest: str
    test_digest: str
    training: dict[str, Any]
    gate: FastTextGate
    policy_version: str = "m13-dev-gate-v1"

    @model_validator(mode="after")
    def consistent(self) -> FastTextManifest:
        if self.labels != LABELS:
            raise ValueError("FastText needs the explicit registered label map")
        if self.quantized != (self.model_file == "model.ftz"):
            raise ValueError("Model kind differs from manifest")
        if digest(self.preprocessing) != self.preprocessing_signature:
            raise ValueError("Preprocessing manifest signature differs")
        return self


class FastTextClassifier:
    def __init__(self, manifest_path: Path) -> None:
        import fasttext  # type: ignore[import-untyped]

        try:
            self.manifest_path = manifest_path.resolve()
            self.manifest = FastTextManifest.model_validate(load_json(self.manifest_path))
            self.preprocessor = FastTextPreprocessor()
            path = self.manifest_path.parent / self.manifest.model_file
            if (
                self.preprocessor.signature != self.manifest.preprocessing_signature
                or importlib.metadata.version("fasttext-community") != BINDING
                or path.stat().st_size != self.manifest.model_bytes
                or file_hash(path) != self.manifest.model_sha256
            ):
                raise ValueError("Artifact hash, binding or preprocessing differs")
            self.model = fasttext.load_model(str(path))
            if set(self.model.get_labels()) != set(LABELS) or (
                bool(self.model.is_quantized()) != self.manifest.quantized
            ):
                raise ValueError("Artifact label or quantization map differs")
        except OSError, ValueError, RuntimeError, ValidationError:
            raise ConfigurationError(
                "Invalid FastText artifact; restore a verified version"
            ) from None

    def predict(self, text: str, *, gate: FastTextGate | None = None) -> FastTextPrediction:
        started = perf_counter()
        active_gate = gate or self.manifest.gate
        top: tuple[FastTextCandidate, ...] = ()
        code = None
        tokens = self.preprocessor.tokens(text) if len(text) <= 2000 else ()
        if len(text) > 2000:
            reason = "input_too_long"
        elif not tokens:
            reason = "empty_input"
        elif not any(any(c.isalpha() for c in token) for token in tokens):
            reason = "number_only"
        else:
            try:
                labels, probabilities = self.model.predict(" ".join(tokens), k=len(LABELS))
                if len(labels) != len(LABELS) or len(probabilities) != len(labels):
                    raise ValueError("Missing ranked labels")
                # Preserve binding probabilities, including its softmax epsilon.
                top = tuple(
                    FastTextCandidate(
                        label=label,
                        code=LABELS[label],
                        rank=i,
                        raw_probability=float(p),
                    )
                    for i, (label, p) in enumerate(zip(labels, probabilities, strict=True), 1)
                )
                lexical = [t for t in tokens if any(c.isalpha() for c in t)]
                known = sum(self.model.get_word_id(t) >= 0 for t in lexical) / len(lexical)
                if known < active_gate.min_known_ratio:
                    reason = "oov_input"
                elif top[0].code is None:
                    reason = "unknown_label" if top[0].label == "__label__unknown" else "multiple"
                elif not active_gate.calibrated:
                    reason = "uncalibrated_gate"
                elif top[0].raw_probability < active_gate.threshold:
                    reason = "below_threshold"
                elif top[0].raw_probability - top[1].raw_probability < active_gate.margin:
                    reason = "close_candidates"
                else:
                    reason, code = "calibrated_top1", top[0].code
            except KeyError, ValueError, RuntimeError, ValidationError:
                top, reason = (), "invalid_prediction"
        return FastTextPrediction(
            top_k=top,
            final_intent_code=code,
            is_actionable=code is not None,
            need_review=code is None,
            unknown=not top or top[0].code is None or reason in {"oov_input", "invalid_prediction"},
            reason=reason,
            model_version=self.manifest.version,
            preprocessing_signature=self.preprocessor.signature,
            policy_version=self.manifest.policy_version,
            quantized=self.manifest.quantized,
            elapsed_ms=(perf_counter() - started) * 1000,
        )


class FastTextFallback:
    enabled = True

    def __init__(self, classifier: FastTextClassifier, next_fallback: FallbackPort) -> None:
        if not classifier.manifest.gate.calibrated:
            raise ConfigurationError("FastText takeover requires a dev-calibrated manifest")
        self.classifier, self.next_fallback = classifier, next_fallback
        self.slots = asyncio.Semaphore(2)

    async def decide(
        self,
        text: TextEntityResult,
        retrievals: tuple[DenseResult, ...],
        budget: ExecutionBudget,
        *,
        context: str | None = None,
    ) -> FallbackResult:
        # Bounded CPU jobs; retain the permit until the thread finishes even on cancellation.
        async with asyncio.timeout(budget.remaining_seconds()):
            async with self.slots:
                task = asyncio.create_task(
                    asyncio.to_thread(self.classifier.predict, text.clean.cleaned_text)
                )
                try:
                    prediction = await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
        budget.remaining_seconds()
        # Classification can name an intent before slots are complete. The existing
        # Cascade removes unresolved slots and returns CLARIFY; SOP remains forbidden.
        if prediction.is_actionable:
            return FallbackResult(
                code=prediction.final_intent_code,
                reason="supported",
                source="fasttext",
                fasttext=prediction,
            )
        chosen = await self.next_fallback.decide(text, retrievals, budget, context=context)
        return FallbackResult.model_validate(
            chosen.model_copy(update={"fasttext": prediction}).model_dump()
        )


def pointer_manifest(pointer: Path) -> Path:
    try:
        data = load_json(pointer)
        if data["format"] != "m13-pointer-v1" or not isinstance(data["active"], str):
            raise ValueError("Invalid pointer")
        path = Path(data["active"])
        if not path.is_absolute():
            path = pointer.parent / path
        return path.resolve()
    except KeyError, OSError, ValueError:
        raise ConfigurationError("Invalid FastText rollback pointer") from None


def activate(pointer: Path, manifest_path: Path, *, rollback: bool = False) -> None:
    old = load_json(pointer) if pointer.exists() else {}
    if old and old.get("format") != "m13-pointer-v1":
        raise ConfigurationError("Invalid existing FastText pointer")
    target = Path(str(old.get("previous", ""))) if rollback else manifest_path.resolve()
    if rollback and not old.get("previous"):
        raise ConfigurationError("No previous FastText version")
    model = FastTextClassifier(target)
    if not model.manifest.gate.calibrated:
        raise ConfigurationError("Only dev-calibrated artifacts can be activated")
    # Read-back prediction checks content as well as the artifact hash.
    if model.predict("订单未享受优惠").reason == "invalid_prediction":
        raise ConfigurationError("FastText activation content probe failed")
    if old.get("active") == str(target.resolve()):
        return
    atomic_json(
        pointer,
        {
            "format": "m13-pointer-v1",
            "active": str(target.resolve()),
            "previous": old.get("active"),
            "default_enabled": False,
        },
    )
