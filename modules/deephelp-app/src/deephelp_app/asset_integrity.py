"""File and package integrity, independent of evaluation and its datasets."""

import hashlib
import platform
import subprocess
from pathlib import Path
from typing import Any

from deephelp_app.corpus import digest

PACKAGE = Path(__file__).parent


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance() -> dict[str, Any]:
    # A complete runtime release binds runtime code/data. Evaluation/learning tools
    # have their own provenance below and need not be installed to serve a release.
    inputs = {
        str(p.relative_to(PACKAGE)): file_digest(p)
        for p in sorted(PACKAGE.rglob("*"))
        if p.is_file()
        and p.suffix in {".py", ".json", ".jsonl", ".txt", ".html", ".sql"}
        and not any(
            part in {"learning", "probes", "evaluation"} for part in p.relative_to(PACKAGE).parts
        )
    }
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "git_commit": commit,
        "dirty_worktree": bool(dirty),
        "package_digest": digest(inputs),
        "python": platform.python_version(),
        "artifact_hashes": inputs,
        "integrity_scope": "runtime-v2",
    }


def evaluation_provenance() -> dict[str, Any]:
    """Evaluation records both the serving code and all tools/frozen inputs it used."""
    value = provenance()
    inputs = {
        str(p.relative_to(PACKAGE)): file_digest(p)
        for p in sorted(PACKAGE.rglob("*"))
        if p.is_file() and p.suffix in {".py", ".json", ".jsonl", ".txt", ".html", ".sql"}
    }
    value["tooling_digest"] = digest(inputs)
    value["evaluation_artifact_hashes"] = inputs
    return value
