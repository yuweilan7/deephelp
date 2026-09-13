"""Offline documentation checks, narrow planning exports and private PDF import.

Python standard library only. Never connects to a model, database or cloud server.
All generated local material stays under the ignored .local directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE = (
    "AGENTS.md", "docs/CONTRACTS.md", "docs/PROJECT_STATE.md",
    "docs/ARCHITECTURE.md", "docs/DECISIONS.md", "docs/RISKS.md",
    "docs/SOURCE_MAP.md", "docs/references/source-manifest.json",
    "planning/manifest.json", "pyproject.toml", "uv.lock",
)
PIPELINE = (
    "INPUT_VALIDATE", "SAFETY_CHECK", "TEXT_CLEAN", "BUILD_BIZ_CONTEXT",
    "EVENT_CLUSTER", "INTENT_RECOGNIZE", "INTENT_CHECK", "KEYWORD_CHECK",
    "FETCH_SOP_ID", "SOP_EXECUTE", "REPLY_POLISH", "SESSION_MAINTAIN",
    "RESPONSE_ENVELOPE",
)
MAX_FILE_BYTES = 256 * 1024
MAX_EXPORT_BYTES = 2 * 1024 * 1024


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    """Reject traversal, drive-qualified paths and all symlinks, including broken ones."""
    root = root.resolve()
    normalized = relative.replace("\\", "/")
    parts = normalized.split("/")
    if not relative or normalized.startswith("/") or ":" in normalized or ".." in parts:
        raise ValueError(f"Unsafe relative path: {relative}")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symlink not allowed: {relative}")
    if not current.resolve().is_relative_to(root):
        raise ValueError(f"Path escapes repository: {relative}")
    return current


def load_json(root: Path, relative: str) -> Any:
    path = safe_path(root, relative)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"File too large: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def get_modules(root: Path) -> dict[str, dict[str, Any]]:
    manifest = load_json(root, "planning/manifest.json")
    modules = manifest["modules"]
    expected = {f"M{i:02d}" for i in range(22)}
    if len(modules) != 22 or {m["id"] for m in modules} != expected:
        raise ValueError("Manifest must contain exactly M00 through M21, once each")
    result = {m["id"]: m for m in modules}
    deps = {key: value["deps"] for key, value in result.items()}
    deps["P00"] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node not in deps:
            raise ValueError(f"Unknown dependency: {node}")
        if node in visiting:
            raise ValueError(f"Dependency cycle at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in deps[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for key, item in result.items():
        visit(key)
        path = safe_path(root, item["path"])
        if not path.is_file():
            raise ValueError(f"Missing module file: {item['path']}")
        if not path.as_posix().startswith((root.resolve() / "planning/modules").as_posix() + "/"):
            raise ValueError(f"Module outside planning/modules: {key}")
    return result


def source_manifest(root: Path) -> dict[str, Any]:
    manifest = load_json(root, "docs/references/source-manifest.json")
    if manifest.get("pages") != 78 or manifest.get("publish_original") is not False:
        raise ValueError("Source must be the private 78-page original")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("sha256", "")):
        raise ValueError("Invalid source SHA-256")
    if manifest.get("local_relative_path") != ".local/references/deephelp-original.pdf":
        raise ValueError("Unexpected source destination")
    return manifest


def verify(root: Path) -> dict[str, Any]:
    modules = get_modules(root)
    source_manifest(root)
    ignore = safe_path(root, ".gitignore").read_text(encoding="utf-8")
    if "/.local/" not in ignore.splitlines() or "*.pdf" not in ignore.splitlines():
        raise ValueError("Private source/context ignore rules are missing")
    architecture = safe_path(root, "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    for step in PIPELINE:
        if step not in architecture:
            raise ValueError(f"Missing pipeline step: {step}")
    checked_links = 0
    candidates = [root / "AGENTS.md", root / "00_START_HERE.md"]
    for folder in ("docs", "planning", "handoffs"):
        directory = safe_path(root, folder)
        candidates.extend(directory.rglob("*.md"))
    for path in candidates:
        path = safe_path(root, path.relative_to(root).as_posix())
        text = path.read_text(encoding="utf-8")
        # Links are deliberately limited to managed docs; existing infra is not scanned.
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                raise ValueError(f"Broken or escaping link: {path.name} -> {target}")
            # A resolved link may not hide a symlink into private/outside content.
            if (path.parent / target).is_symlink():
                raise ValueError(f"Symlink link target: {target}")
            checked_links += 1
    return {"status": "PASS", "scope": "documentation_only", "modules": len(modules),
            "pipeline_steps": len(PIPELINE), "checked_links": checked_links,
            "business_integration": "NOT_RUN", "live_models": "NOT_RUN"}


def git_value(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True,
                            capture_output=True, timeout=15, check=False)
    if result.returncode != 0:
        raise ValueError(f"Git check failed: {' '.join(args)}")
    return result.stdout.strip()


def assert_private_destination(root: Path, destination: str) -> None:
    safe_path(root, destination)
    result = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", destination],
                            text=True, capture_output=True, timeout=15, check=False)
    if result.returncode != 0:
        raise ValueError("Destination is not Git-ignored; refusing local material write")
    tracked = git_value(root, "ls-files", "--", destination)
    if tracked:
        raise ValueError("Private destination is already tracked; refusing to overwrite")


def export_context(root: Path, module_id: str) -> Path:
    if not re.fullmatch(r"M(?:0\d|1\d|2[01])", module_id):
        raise ValueError("Expected exactly one module ID from M00 to M21")
    modules = get_modules(root)
    selected = modules[module_id]
    paths = list(CORE) + [selected["path"], "planning/PLAN_MODULE_PROMPT.md"]
    paths += [f"handoffs/{dep}.md" for dep in selected["deps"]]
    paths += [f"docs/MODULES/{module_id}.md", f"handoffs/{module_id}.md"]
    # Only package metadata is auto-included. Relevant source code must still be read separately.
    for path in sorted((root / "modules").glob("*/pyproject.toml")):
        paths.append(path.relative_to(root).as_posix())
    paths = list(dict.fromkeys(paths))
    commit = git_value(root, "rev-parse", "HEAD")
    dirty = bool(git_value(root, "status", "--porcelain"))
    chunks = [f"# {module_id} planning context\n\nCommit: `{commit}`\nWorking tree dirty: {dirty}\n"
              "This is a bounded workspace snapshot, not proof of implementation or all source code.\n"
              "Read relevant implementation files separately; missing handoffs do not imply completion.\n"]
    size = 0
    for relative in paths:
        path = safe_path(root, relative)
        if not path.is_file():
            chunks.append(f"\n## MISSING: {relative}\nNo completion inferred.\n")
            continue
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(f"Oversized context file: {relative}")
        size += len(raw)
        if size > MAX_EXPORT_BYTES:
            raise ValueError("Context size limit exceeded")
        chunks.append(f"\n## FILE: {relative}\nSHA256: {hashlib.sha256(raw).hexdigest()}\n\n" + raw.decode("utf-8"))
    relative = f".local/context/{module_id}_CONTEXT.md"
    assert_private_destination(root, relative)
    out = safe_path(root, relative)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(chunks), encoding="utf-8")
    return out


def copy_verified(source: Path, destination: Path, expected_hash: str) -> str:
    if source.is_symlink() or not source.is_file() or digest(source) != expected_hash:
        raise ValueError("Source hash mismatch or unsafe source")
    if destination.is_symlink():
        raise ValueError("Destination may not be a symlink")
    if destination.exists():
        if digest(destination) != expected_hash:
            raise ValueError("Different destination already exists; refusing overwrite")
        return "ALREADY_PRESENT"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as out:
            temporary = Path(out.name)
            with source.open("rb") as inp:
                for block in iter(lambda: inp.read(1024 * 1024), b""):
                    out.write(block)
        if digest(temporary) != expected_hash:
            raise ValueError("Copied bytes failed verification")
        # Link is atomic and will not overwrite an existing file. Same-directory local filesystem.
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or digest(destination) != expected_hash:
                raise ValueError("Destination changed during copy; refusing overwrite") from None
        return "COPIED_AND_VERIFIED"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_source(root: Path, source_dir: Path) -> tuple[Path, str]:
    manifest = source_manifest(root)
    relative = manifest["local_relative_path"]
    assert_private_destination(root, relative)
    destination = safe_path(root, relative)
    if destination.exists():
        if digest(destination) != manifest["sha256"]:
            raise ValueError("Existing private PDF differs; no overwrite performed")
        return destination, "ALREADY_PRESENT"
    if not source_dir.is_dir() or source_dir.is_symlink():
        raise ValueError("Source directory unavailable or symlinked")
    matches = [p for p in sorted(source_dir.glob("*.pdf"))
               if "deephelp" in p.name.lower() and not p.is_symlink()
               and digest(p) == manifest["sha256"]]
    if not matches:
        raise ValueError("No DeepHelp PDF with the expected SHA-256 in the specified directory")
    return destination, copy_verified(matches[0], destination, manifest["sha256"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify")
    export = sub.add_parser("export")
    export.add_argument("--module", required=True)
    source = sub.add_parser("import-source")
    source.add_argument("--source-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "verify":
            print(json.dumps(verify(root), ensure_ascii=False, indent=2))
        elif args.command == "export":
            print(export_context(root, args.module))
        else:
            path, status = import_source(root, args.source_dir)
            print(json.dumps({"status": status, "path": str(path),
                              "original_deleted": False, "uploaded": False}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
