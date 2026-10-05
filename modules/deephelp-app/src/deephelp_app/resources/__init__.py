"""只定位包内明确允许的运行资产；正式app不回查demo或冻结评测资料。"""

from pathlib import Path

PACKAGE = Path(__file__).parents[1]


def asset_path(name: str) -> Path:
    if name not in {"m12_policy.json", "m13_dictionary.txt"}:
        raise ValueError(f"Not a runtime resource: {name}")
    return PACKAGE / "resources" / "runtime" / name
