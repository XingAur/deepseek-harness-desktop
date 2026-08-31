from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import version
from app.core_status import CORE_VERSION
from app.release_bundle import build_release_bundle, collect_release_files


ROOT = Path(__file__).resolve().parents[1]


class VersionContractTests(unittest.TestCase):
    def test_version_file_core_version_and_loaded_version_are_identical(self) -> None:
        file_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

        self.assertTrue(file_version)
        self.assertEqual(file_version, version.VERSION)
        self.assertEqual(file_version, CORE_VERSION)
        self.assertIn(ROOT / "VERSION", collect_release_files(ROOT))

    def test_release_bundle_records_source_version_for_explicit_historical_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_release_bundle(
                project_root=ROOT,
                output_dir=Path(temp_dir),
                version="0.63.0",
            )

            manifest = Path(result["manifest_path"])
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual("0.63.0", payload["version"])
            self.assertEqual(version.VERSION, payload["source_version"])


if __name__ == "__main__":
    unittest.main()
