"""Runtime and independent tool/input provenance for exact content acceptance."""

from pathlib import Path

from deephelp_app.adapters.asset_integrity import file_digest, provenance
from deephelp_app.application.corpus import digest
from deephelp_tools.assets import GROUPS, PACKAGE, asset_path


def evaluation_provenance() -> dict[str, object]:
    value = provenance()
    inputs = {"deephelp_app/" + name: sha for name, sha in value["artifact_hashes"].items()}
    inputs.update(
        {
            "deephelp_tools/" + str(p.relative_to(PACKAGE)): file_digest(p)
            for p in sorted(PACKAGE.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json", ".jsonl", ".txt", ".html", ".sql"}
        }
    )
    for group, names in GROUPS.items():
        for name in sorted(names):
            inputs["deephelp_tools/" + str(Path("resources") / group / name)] = file_digest(
                asset_path(name)
            )
    value["tooling_digest"] = digest(inputs)
    value["evaluation_artifact_hashes"] = inputs
    return value
