"""File and package integrity, independent of evaluation and its datasets."""

import hashlib
import platform
import subprocess
from pathlib import Path
from typing import Any

from deephelp_app.application.corpus import digest

PACKAGE = Path(__file__).parents[1]


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
    # Installed serving still verifies every runtime byte without needing a Git checkout.
    # Preparation separately requires source provenance; unknown is never a clean commit.
    commit: str | None = None
    dirty: bool | None = None
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10
        )
        if revision.returncode == 0 and status.returncode == 0:
            commit, dirty = revision.stdout.strip(), bool(status.stdout.strip())
    except OSError, subprocess.TimeoutExpired:
        pass
    return {
        "git_commit": commit,
        "dirty_worktree": dirty,
        "package_digest": digest(inputs),
        "python": platform.python_version(),
        "artifact_hashes": inputs,
        "integrity_scope": "runtime-v2",
    }
