from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.self_check import preserve_failure_outputs


class SelfCheckFailureReportingTests(unittest.TestCase):
    def test_ephemeral_failure_reports_are_preserved_without_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "ephemeral"
            requested = root / "reports"
            source.mkdir()
            (source / "self_check_report.md").write_text("failed\n", encoding="utf-8")
            (source / "self_check_result.json").write_text('{"status":"failed"}\n', encoding="utf-8")
            (source / "fixture-secret.txt").write_text("must not be copied\n", encoding="utf-8")

            destination = preserve_failure_outputs(
                source_dir=source,
                requested_output_dir=requested,
                run_namespace="abcdefghijklmnop",
            )

            self.assertEqual(requested / "failure_abcdefghijkl", destination)
            self.assertEqual(
                ["self_check_report.md", "self_check_result.json"],
                sorted(item.name for item in destination.iterdir()),
            )
            self.assertEqual("failed\n", (destination / "self_check_report.md").read_text(encoding="utf-8"))

    def test_preserved_failure_directory_is_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "ephemeral"
            source.mkdir()
            for name in ("self_check_report.md", "self_check_result.json"):
                (source / name).write_text("failed\n", encoding="utf-8")

            preserve_failure_outputs(
                source_dir=source,
                requested_output_dir=root / "reports",
                run_namespace="same-run-namespace",
            )

            with self.assertRaises(FileExistsError):
                preserve_failure_outputs(
                    source_dir=source,
                    requested_output_dir=root / "reports",
                    run_namespace="same-run-namespace",
                )


if __name__ == "__main__":
    unittest.main()
