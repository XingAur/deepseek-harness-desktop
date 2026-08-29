from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from app.release_bundle import build_release_bundle


ROOT = Path(__file__).resolve().parents[1]


class ReleaseBundleTests(unittest.TestCase):
    def test_two_builds_are_byte_for_byte_reproducible_and_exclude_runtime_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            first = build_release_bundle(
                project_root=ROOT,
                output_dir=output_root / "first",
                version="0.63.0",
            )
            second = build_release_bundle(
                project_root=ROOT,
                output_dir=output_root / "second",
                version="0.63.0",
            )

            first_archive = Path(first["archive_path"])
            second_archive = Path(second["archive_path"])
            self.assertEqual(first["archive_sha256"], second["archive_sha256"])
            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            self.assertEqual(hashlib.sha256(first_archive.read_bytes()).hexdigest(), first["archive_sha256"])

            with tarfile.open(first_archive, "r:gz") as archive:
                names = archive.getnames()
            self.assertIn("his-harness-0.63.0/CHANGELOG.md", names)
            self.assertIn("his-harness-0.63.0/app/enterprise_gate.py", names)
            self.assertNotIn("his-harness-0.63.0/data/harness.sqlite", names)
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any(name.endswith("/.DS_Store") for name in names))

    def test_manifest_contains_only_relative_content_hashes_and_no_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_release_bundle(
                project_root=ROOT,
                output_dir=temp_dir,
                version="0.63.0",
            )
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

            self.assertEqual("0.63.0", manifest["version"])
            self.assertEqual("passed", manifest["secret_scan_status"])
            self.assertTrue(manifest["files"])
            self.assertTrue(all(not Path(item["path"]).is_absolute() for item in manifest["files"]))
            self.assertTrue(all(set(item) == {"path", "sha256", "size"} for item in manifest["files"]))
            self.assertNotIn(str(ROOT), json.dumps(manifest))

    def test_invalid_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "版本号"):
                build_release_bundle(project_root=ROOT, output_dir=temp_dir, version="../../unsafe")


if __name__ == "__main__":
    unittest.main()
