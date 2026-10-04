"""Independent tool resources, from the wheel or the single repository source."""

from pathlib import Path

from deephelp_app.resources import asset_path as runtime_asset_path

PACKAGE = Path(__file__).parent
GROUPS = {
    "learning": {
        "mcp-faults.json",
        "event_sequences.json",
        "examples.json",
        "model-replay-v1.json",
    },
    "demo": {"business.json", "business-fixtures-v2.json", "m09_corpus.jsonl"},
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
        "business-catalog-v2.json",
    },
}


def asset_path(name: str) -> Path:
    for group, names in GROUPS.items():
        if name in names:
            bundled = PACKAGE / "resources" / group / name
            if bundled.is_file():
                return bundled
            root = PACKAGE.parents[3]
            source = root / ("datasets" if group == "evaluation" else "demo/data") / name
            if source.is_file():
                return source
            raise FileNotFoundError(f"Tool resource missing: {name}")
    return runtime_asset_path(name)
