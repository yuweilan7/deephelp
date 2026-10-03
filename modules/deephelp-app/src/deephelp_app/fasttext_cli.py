"""CPU training, read-back inference and verified atomic rollback."""

import argparse
import json
import multiprocessing
from pathlib import Path
from typing import Any, cast

from deephelp_app.dense import atomic_json
from deephelp_app.errors import ConfigurationError
from deephelp_app.fasttext_preprocess import digest
from deephelp_app.fasttext_runtime import FastTextClassifier, activate, pointer_manifest
from deephelp_app.fasttext_training import DATA, audit, metrics, train


def train_worker(output: str, data: str) -> None:
    train(Path(output), data=Path(data))


def bounded_train(output: Path, data: Path, seconds: int = 180) -> dict[str, Any]:
    if not 1 <= seconds <= 600:
        raise ConfigurationError("Training timeout must be 1..600 seconds")
    process = multiprocessing.get_context("spawn").Process(
        target=train_worker, args=(str(output), str(data))
    )
    process.start()
    process.join(seconds)
    if process.is_alive():
        process.terminate()
        process.join(10)
        raise ConfigurationError("FastText training exceeded its bounded CPU time")
    if process.exitcode != 0:
        raise ConfigurationError("FastText training failed; inspect the retained local experiment")
    return cast(dict[str, Any], json.loads((output / "report.json").read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("audit", "train", "evaluate", "predict", "activate", "rollback")
    )
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=Path(".local/m13/experiment-v1"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--pointer", type=Path, default=Path(".local/m13/active.json"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--text", default="付款时该减的钱没有减掉")
    args = parser.parse_args()
    try:
        if args.command == "audit":
            rows = audit(args.data)
            result: dict[str, Any] = {
                "status": "PASS",
                "counts": {s: len(r) for s, r in rows.items()},
            }
        elif args.command == "train":
            report = bounded_train(args.output, args.data, args.timeout)
            result = {
                "status": report["status"],
                "counts": report["counts"],
                "models": {
                    k: {
                        "bytes": v["bytes"],
                        "gate": v["gate"],
                        "test": {key: value for key, value in v["test"].items() if key != "rows"},
                    }
                    for k, v in report["models"].items()
                },
            }
        elif args.command == "evaluate":
            if args.output.exists():
                raise ConfigurationError("Use a new FastText evaluation report")
            model = FastTextClassifier(args.manifest or pointer_manifest(args.pointer))
            splits = audit(args.data)
            if any(
                getattr(model.manifest, s + "_digest") != digest(rows) for s, rows in splits.items()
            ):
                raise ConfigurationError("Frozen evaluation data differs from the manifest")
            result = {
                "status": "PASS",
                "synthetic": True,
                "model": model.manifest.model_dump(mode="json"),
                "metrics": {s: metrics(model, rows) for s, rows in splits.items()},
            }
            atomic_json(args.output, result)
            result = {"status": "PASS", "report": str(args.output)}
        elif args.command == "predict":
            model = FastTextClassifier(args.manifest or pointer_manifest(args.pointer))
            result = model.predict(args.text).model_dump(mode="json")
        else:
            if args.command == "activate" and not args.manifest:
                raise ConfigurationError("Activation needs an explicit verified manifest")
            activate(args.pointer, args.manifest or Path(), rollback=args.command == "rollback")
            result = {"status": "PASS", "active": str(pointer_manifest(args.pointer))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ConfigurationError, OSError, ValueError:
        print("FastText command failed; inspect the local artifact/data and retained diagnostics")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
