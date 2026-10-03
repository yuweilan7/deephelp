"""Package resource locations by responsibility; lookup performs no file reads."""

from pathlib import Path

PACKAGE = Path(__file__).parent


def asset_path(name: str) -> Path:
    groups = {
        "demo": {"business.json", "m09_corpus.jsonl"},
        "runtime": {"m12_policy.json", "m13_dictionary.txt"},
        "learning": {
            "mcp-faults.json",
            "event_sequences.json",
            "examples.json",
            "model-replay-v1.json",
        },
        "evaluation": {
            "cases.json",
            "text-golden.json",
            "m09_dev.json",
            "m09_test.json",
            "m13_fasttext.json",
            "m17_cases.json",
            "m17_manifest.json",
            "m17_scale_cases.json",
            "m17_scale_manifest.json",
            "m17_approval.json",
            "m18_demo.json",
            "scenarios-v1.json",
        },
    }
    for group, names in groups.items():
        if name in names:
            return PACKAGE / "assets" / group / name
    raise ValueError(f"Unknown bundled asset: {name}")
