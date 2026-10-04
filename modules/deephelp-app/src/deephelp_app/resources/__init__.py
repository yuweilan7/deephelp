"""Runtime-only resources; no lookup into demo or frozen evaluation data."""

from pathlib import Path

PACKAGE = Path(__file__).parents[1]


def asset_path(name: str) -> Path:
    if name not in {"m12_policy.json", "m13_dictionary.txt"}:
        raise ValueError(f"Not a runtime resource: {name}")
    return PACKAGE / "resources" / "runtime" / name
