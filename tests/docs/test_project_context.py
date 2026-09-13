"""Offline unit tests for documentation helpers, not Agent/business integration."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("project_context", ROOT / "scripts/project_context.py")
assert spec and spec.loader
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for name in ("docs", "planning", "handoffs", "reports"):
            shutil.copytree(ROOT / name, self.root / name)
        for name in ("AGENTS.md", "00_START_HERE.md", ".gitignore"):
            shutil.copy2(ROOT / name, self.root / name)

    def tearDown(self) -> None:
        self.temp.cleanup()

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
