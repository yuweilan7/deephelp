"""Small audited synthetic experiment. Only train/dev choose artifacts and gates."""

import itertools
import json
import platform
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from deephelp_app.dense import atomic_json
from deephelp_app.errors import ConfigurationError
from deephelp_app.fasttext_preprocess import FastTextPreprocessor, digest
from deephelp_app.fasttext_runtime import (
    LABELS,
    FastTextClassifier,
    FastTextGate,
    FastTextManifest,
    file_hash,
)

DATA = Path(__file__).parent / "sample_data/m13_fasttext.json"


class TrainingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Binding 0.11.8 initializes ten blocks; other thread counts are not qualified here.
    thread: int = Field(default=10, ge=10, le=10)
    dim: int = Field(default=20, ge=20, le=20)
    bucket: int = Field(default=4096, ge=1024, le=8192)
    epoch: int = Field(default=80, ge=1, le=120)
    lr: float = Field(default=0.5, gt=0, le=1, allow_inf_nan=False)
    wordNgrams: int = Field(default=2, ge=1, le=2)
    minn: int = Field(default=2, ge=0, le=2)
    maxn: int = Field(default=4, ge=0, le=4)
    seed: int = Field(default=42, ge=42, le=42)
    loss: Literal["softmax"] = "softmax"


def audit(path: Path = DATA) -> dict[str, list[dict[str, Any]]]:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ConfigurationError("FastText data exceeds the small experiment budget")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ConfigurationError("FastText data requires 1..2000 reviewed synthetic rows")
    prep = FastTextPreprocessor()
    groups: dict[str, str] = {}
    ids: set[str] = set()
    texts: dict[str, str] = {}
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "dev": [], "test": []}
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("synthetic") is not True
            or row.get("reviewed") is not True
            or row.get("source") not in {"manual-synthetic-m13-v1", "reviewed-flywheel-m18-v1"}
            or row.get("split") not in splits
            or row.get("label") not in LABELS
            or not isinstance(row.get("text"), str)
            or not 1 <= len(row["text"]) <= 2000
        ):
            raise ConfigurationError("Unreviewed, unregistered or oversized FastText data")
        if row.get("source") == "reviewed-flywheel-m18-v1" and (
            row.get("split") != "train"
            or not isinstance(row.get("feedback_review"), dict)
            or not row["feedback_review"].get("candidate_id")
            or not row["feedback_review"].get("reviewer")
            or row["feedback_review"].get("state") != "approved"
        ):
            raise ConfigurationError(
                "Feedback is train-only and needs an approved review reference"
            )
        split = row["split"]
        if not row.get("sample_id") or row["sample_id"] in ids:
            raise ConfigurationError("Duplicate or empty FastText sample identity")
        ids.add(row["sample_id"])
        for key in ("source_group", "variant_group"):
            group = row.get(key)
            if not isinstance(group, str) or not group:
                raise ConfigurationError("Every sample needs source and semantic variant groups")
            scoped = key + ":" + group
            if scoped in groups and groups[scoped] != split:
                raise ConfigurationError("FastText source/paraphrase group leaks across splits")
            groups[scoped] = split
        normalized = prep.prepare(row["text"])
        if not normalized or normalized in texts:
            raise ConfigurationError("Duplicate normalized FastText text or empty tokenization")
        texts[normalized] = split
        splits[split].append(row)
    if any({r["label"] for r in values} != set(LABELS) for values in splits.values()):
        raise ConfigurationError("Every split must include all five explicit labels")
    return splits


def metrics(model: FastTextClassifier, rows: list[dict[str, Any]]) -> dict[str, Any]:
    predictions = [(row, model.predict(row["text"])) for row in rows]
    accepted = [(r, p) for r, p in predictions if p.is_actionable]
    wrong = [(r, p) for r, p in accepted if p.final_intent_code != LABELS[r["label"]]]
    raw_correct = sum(bool(p.top_k) and p.top_k[0].label == r["label"] for r, p in predictions)
    durations = sorted(p.elapsed_ms for _, p in predictions)
    return {
        "samples": len(rows),
        "raw_top1_correct": raw_correct,
        "raw_top1_accuracy": raw_correct / len(rows),
        "takeovers": len(accepted),
        "coverage": len(accepted) / len(rows),
        "wrong_takeovers": len(wrong),
        "wrong_takeover_rate": len(wrong) / len(accepted) if accepted else 0,
        "safe_with_review": (len(rows) - len(wrong)) / len(rows),
        "latency_ms_median": median(durations),
        "latency_ms_p95": durations[min(len(durations) - 1, int(len(durations) * 0.95))],
        "rows": [dict(sample=row, prediction=p.model_dump(mode="json")) for row, p in predictions],
    }


def calibrate(model: FastTextClassifier, rows: list[dict[str, Any]]) -> FastTextGate:
    grid = itertools.product((0.5, 0.65, 0.8, 0.9, 0.95, 1.0, 1.0001), (0.1, 0.2, 0.35, 0.5))
    # An above-epsilon threshold safely disables takeover if no useful safe dev gate exists.
    best = (0, 1.0001, 1.0)
    for threshold, margin in grid:
        gate = FastTextGate(threshold=threshold, margin=margin, calibrated=True)
        accepted = [(r, model.predict(r["text"], gate=gate)) for r in rows]
        accepted = [(r, p) for r, p in accepted if p.is_actionable]
        if any(p.final_intent_code != LABELS[r["label"]] for r, p in accepted):
            continue
        # Prefer coverage; ties prefer the stricter threshold/margin.
        best = max(best, (len(accepted), threshold, margin))
    return FastTextGate(threshold=best[1], margin=best[2], calibrated=True)


def train(output: Path, *, data: Path = DATA) -> dict[str, Any]:
    import fasttext  # type: ignore[import-untyped]
    import numpy as np

    if output.exists():
        raise ConfigurationError("Use a new immutable FastText experiment directory")
    splits = audit(data)
    prep = FastTextPreprocessor()
    output.mkdir(parents=True)
    train_file = output / "train.txt"
    train_file.write_text(
        "".join(r["label"] + " " + prep.prepare(r["text"]) + "\n" for r in splits["train"]),
        encoding="utf-8",
    )
    configs = [TrainingConfig(epoch=80), TrainingConfig(epoch=120)]
    candidates: list[dict[str, Any]] = []
    for index, config in enumerate(configs, 1):
        folder = output / f"candidate-{index}"
        folder.mkdir()
        estimated = (config.bucket + len(splits["train"]) * 2000 + 5) * config.dim * 4 * 3
        if estimated > 32 * 1024 * 1024:
            raise ConfigurationError("FastText estimated matrices exceed 32MiB")
        started = perf_counter()
        model = fasttext.train_supervised(str(train_file), **config.model_dump(), verbose=0)
        if (
            not np.isfinite(model.get_input_matrix()).all()
            or not np.isfinite(model.get_output_matrix()).all()
        ):
            raise ConfigurationError("FastText binding produced nonfinite matrices")
        model_file = folder / "model.bin"
        model.save_model(str(model_file))
        manifest = FastTextManifest(
            version=f"m13-c{index}-raw-v1",
            model_file="model.bin",
            model_sha256=file_hash(model_file),
            model_bytes=model_file.stat().st_size,
            quantized=False,
            preprocessing=prep.spec,
            preprocessing_signature=prep.signature,
            labels=LABELS,
            train_digest=digest(splits["train"]),
            dev_digest=digest(splits["dev"]),
            test_digest=digest(splits["test"]),
            training=config.model_dump(),
            gate=FastTextGate(threshold=1, margin=0.5),
        )
        manifest_path = folder / "manifest.json"
        atomic_json(manifest_path, manifest.model_dump(mode="json"))
        classifier = FastTextClassifier(manifest_path)
        gate = calibrate(classifier, splits["dev"])
        manifest = manifest.model_copy(update={"gate": gate})
        atomic_json(manifest_path, manifest.model_dump(mode="json"))
        classifier = FastTextClassifier(manifest_path)
        candidates.append(
            {
                "manifest": str(manifest_path.resolve()),
                "training_seconds": perf_counter() - started,
                "train": metrics(classifier, splits["train"]),
                "dev": metrics(classifier, splits["dev"]),
            }
        )
    # Test labels and metrics are untouched until after this selection is frozen.
    selected = max(
        candidates,
        key=lambda c: (c["dev"]["takeovers"], c["dev"]["raw_top1_correct"], -candidates.index(c)),
    )
    raw_path = Path(selected["manifest"])
    raw = FastTextClassifier(raw_path)
    quant_folder = output / "quantized"
    quant_folder.mkdir()
    raw.model.quantize(input=str(train_file), retrain=False, dsub=2, qnorm=False, cutoff=512)
    quant_file = quant_folder / "model.ftz"
    raw.model.save_model(str(quant_file))
    quant_manifest = raw.manifest.model_copy(
        update={
            "version": raw.manifest.version.replace("raw", "quant"),
            "quantized": True,
            "model_file": "model.ftz",
            "model_sha256": file_hash(quant_file),
            "model_bytes": quant_file.stat().st_size,
            "gate": FastTextGate(threshold=1, margin=0.5),
            "training": {
                **raw.manifest.training,
                "quantization": {
                    "retrain": False,
                    "dsub": 2,
                    "qnorm": False,
                    "cutoff": 512,
                },
            },
        }
    )
    quant_path = quant_folder / "manifest.json"
    atomic_json(quant_path, quant_manifest.model_dump(mode="json"))
    quant = FastTextClassifier(quant_path)
    gate = calibrate(quant, splits["dev"])
    atomic_json(
        quant_path, quant_manifest.model_copy(update={"gate": gate}).model_dump(mode="json")
    )
    raw, quant = FastTextClassifier(raw_path), FastTextClassifier(quant_path)
    report = {
        "status": "PASS",
        "synthetic": True,
        "quality_claim": "small synthetic workflow only; not trusted generalization",
        "python": platform.python_version(),
        "binding": "fasttext-community 0.11.8",
        "source": str(data),
        "counts": {s: len(r) for s, r in splits.items()},
        "source_and_variant_split_isolation": True,
        "seed_supported": 42,
        "byte_reproducibility_claim": False,
        "candidates": candidates,
        "selected_raw": str(raw_path.resolve()),
        "selected_quantized": str(quant_path.resolve()),
        "models": {
            name: {
                "bytes": m.manifest.model_bytes,
                "sha256": m.manifest.model_sha256,
                "gate": m.manifest.gate.model_dump(),
                "train": metrics(m, splits["train"]),
                "dev": metrics(m, splits["dev"]),
                "test": metrics(m, splits["test"]),
            }
            for name, m in (("raw", raw), ("quantized", quant))
        },
    }
    atomic_json(output / "report.json", report)
    return report
