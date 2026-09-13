"""Offline unit tests for documentation helpers, not Agent/business integration."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("project_context", ROOT / "scripts/project_context.py")
assert spec and spec.loader
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for name in ("docs", "planning", "handoffs", "reports"):
            shutil.copytree(ROOT / name, self.root / name)
        for name in ("AGENTS.md", "00_START_HERE.md", ".gitignore"):
            shutil.copy2(ROOT / name, self.root / name)
        # Reports link to the helper and its test source; retain real link targets.
        for name in ("scripts/project_context.py", "tests/docs/test_project_context.py"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)

    def mutate_manifest(self, change) -> None:
        path = self.root / "planning/manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def init_git(self) -> None:
        for args in (("init",), ("add", "."),
                     ("-c", "user.name=Doc Test", "-c", "user.email=docs@example.invalid",
                      "commit", "-m", "synthetic documentation fixture")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def test_valid_documentation(self) -> None:
        self.assertEqual(context.verify(self.root)["modules"], 22)

    def test_cycle_rejected(self) -> None:
        self.mutate_manifest(lambda data: data["modules"][0]["deps"].append("M01"))
        with self.assertRaisesRegex(ValueError, "cycle"):
            context.get_modules(self.root)

    def test_unknown_dependency_rejected(self) -> None:
        self.mutate_manifest(lambda data: data["modules"][0]["deps"].append("M99"))
        with self.assertRaisesRegex(ValueError, "Unknown dependency"):
            context.get_modules(self.root)

    def test_duplicate_module_rejected(self) -> None:
        self.mutate_manifest(lambda data: data["modules"].append(data["modules"][0]))
        with self.assertRaisesRegex(ValueError, "exactly"):
            context.get_modules(self.root)

    def test_missing_module_file_rejected(self) -> None:
        self.mutate_manifest(lambda data: data["modules"][0].update(path="planning/modules/absent.md"))
        with self.assertRaisesRegex(ValueError, "Missing module"):
            context.get_modules(self.root)

    def test_traversal_and_drive_rejected(self) -> None:
        for value in ("../.env", "C:\\private", "/etc/passwd", "a/../../b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                context.safe_path(self.root, value)

    def test_symlink_rejected(self) -> None:
        target = Path(self.temp.name) / "outside"
        target.write_text("private")
        link = self.root / "link"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("Symlinks unavailable in this test environment")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            context.safe_path(self.root, "link")

    def directory_link(self, link: Path, target: Path) -> None:
        # Junctions exercise real Windows path redirection without symlink privilege.
        if os.name == "nt":
            def quote(path):
                return "'" + str(path).replace("'", "''") + "'"
            subprocess.run([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path "
                + quote(link) + " -Value " + quote(target) + " | Out-Null",
            ], check=True, capture_output=True)
            self.assertTrue(link.is_junction())
        else:
            link.symlink_to(target, target_is_directory=True)
        # Remove only this test's directory entry, never recurse into its target.
        self.addCleanup(os.rmdir if os.name == "nt" else os.unlink, link)

    def test_export_rejects_real_redirected_directory(self) -> None:
        self.init_git()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "pyproject.toml").write_text("PRIVATE_SENTINEL")
        (self.root / "modules").mkdir()
        self.directory_link(self.root / "modules/redirect", outside)
        with self.assertRaisesRegex(ValueError, "Symlink|junction"):
            context.export_context(self.root, "M00")
        self.assertFalse((self.root / ".local/context/M00_CONTEXT.md").exists())
        self.assertEqual((outside / "pyproject.toml").read_text(), "PRIVATE_SENTINEL")

    def test_link_through_private_directory_rejected(self) -> None:
        private = self.root / ".local/private"
        private.mkdir(parents=True)
        (private / "hidden.txt").write_text("PRIVATE_SENTINEL")
        self.directory_link(self.root / "docs/redirect", private)
        (self.root / "docs/bad.md").write_text("[private](redirect/hidden.txt)")
        with self.assertRaisesRegex(ValueError, "Symlink|junction"):
            context.verify(self.root)

    def test_import_rejects_redirected_source_ancestor(self) -> None:
        self.init_git()
        outside = Path(self.temp.name) / "outside"
        (outside / "nested").mkdir(parents=True)
        pdf = outside / "nested/DeepHelp.pdf"
        pdf.write_bytes(b"synthetic approved source")
        manifest_path = self.root / "docs/references/source-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sha256"] = context.digest(pdf)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.directory_link(self.root / "source", outside)
        with self.assertRaisesRegex(ValueError, "Symlink|junction"):
            context.import_source(self.root, self.root / "source/nested")
        self.assertFalse((self.root / ".local/references/deephelp-original.pdf").exists())

    def test_export_symlink_policy_before_read(self) -> None:
        self.init_git()
        target = self.root / "docs/CONTRACTS.md"
        real_is_symlink = Path.is_symlink
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=lambda p: p == target or real_is_symlink(p)):
            with self.assertRaisesRegex(ValueError, "Symlink"):
                context.export_context(self.root, "M00")
        self.assertFalse((self.root / ".local/context/M00_CONTEXT.md").exists())

    def test_tracked_private_destination_rejected(self) -> None:
        self.init_git()
        # Track a synthetic file normally, then add its ignore rule; never force-add.
        path = self.root / "later-private.md"
        path.write_text("KEEP")
        subprocess.run(["git", "-C", str(self.root), "add", path.name],
                       check=True, capture_output=True)
        with (self.root / ".gitignore").open("a") as stream:
            stream.write("\nlater-private.md\n")
        with self.assertRaisesRegex(ValueError, "already tracked"):
            context.assert_private_destination(self.root, path.name)
        self.assertEqual(path.read_text(), "KEEP")

    def test_import_wrong_named_pdf_and_conflict(self) -> None:
        self.init_git()
        source = Path(self.temp.name) / "source"
        source.mkdir()
        (source / "DeepHelp.pdf").write_bytes(b"wrong source with matching name")
        with self.assertRaisesRegex(ValueError, "expected SHA-256"):
            context.import_source(self.root, source)
        destination = self.root / ".local/references/deephelp-original.pdf"
        self.assertFalse(destination.exists())
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"KEEP")
        with self.assertRaisesRegex(ValueError, "no overwrite"):
            context.import_source(self.root, source)
        self.assertEqual(destination.read_bytes(), b"KEEP")

    def test_bad_source_manifest_rejected(self) -> None:
        path = self.root / "docs/references/source-manifest.json"
        value = json.loads(path.read_text(encoding="utf-8")); value["pages"] = 77
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            context.source_manifest(self.root)

    def test_wrong_hash_no_copy(self) -> None:
        src = Path(self.temp.name) / "source.pdf"; src.write_bytes(b"synthetic test bytes")
        dest = self.root / "out.pdf"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            context.copy_verified(src, dest, "0" * 64)
        self.assertFalse(dest.exists())

    def test_copy_is_verified_idempotent_and_preserves_original(self) -> None:
        src = Path(self.temp.name) / "source.pdf"; src.write_bytes(b"synthetic test bytes")
        dest = self.root / "private" / "out.pdf"; expected = context.digest(src)
        self.assertEqual(context.copy_verified(src, dest, expected), "COPIED_AND_VERIFIED")
        self.assertEqual(context.copy_verified(src, dest, expected), "ALREADY_PRESENT")
        self.assertTrue(src.exists()); self.assertEqual(context.digest(dest), expected)

    def test_different_destination_not_overwritten(self) -> None:
        src = Path(self.temp.name) / "source.pdf"; src.write_bytes(b"new")
        dest = self.root / "out.pdf"; dest.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "refusing overwrite"):
            context.copy_verified(src, dest, context.digest(src))
        self.assertEqual(dest.read_bytes(), b"keep")

    def test_invalid_module_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            context.export_context(self.root, "../../.env")

    def test_export_is_narrow_and_reports_missing(self) -> None:
        self.init_git()
        (self.root / ".env").write_text("SHOULD_NOT_EXPORT_SECRET")
        text = context.export_context(self.root, "M01").read_text(encoding="utf-8")
        self.assertIn("handoffs/M00.md", text)
        self.assertIn("MISSING: handoffs/M01.md", text)
        self.assertNotIn("SHOULD_NOT_EXPORT_SECRET", text)
        self.assertNotIn("FILE: planning/modules/M21_", text)
        self.assertIn("Working tree dirty: False", text)  # .env is ignored

    def test_nonignored_destination_rejected(self) -> None:
        self.init_git()
        with self.assertRaisesRegex(ValueError, "not Git-ignored"):
            context.assert_private_destination(self.root, "public.md")

    def test_broken_link_rejected(self) -> None:
        (self.root / "docs/bad.md").write_text("[missing](not-here.md)")
        with self.assertRaisesRegex(ValueError, "Broken"):
            context.verify(self.root)


if __name__ == "__main__":
    unittest.main()
