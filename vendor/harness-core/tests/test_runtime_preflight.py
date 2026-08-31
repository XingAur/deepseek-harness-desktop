from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.runtime_preflight import run_runtime_preflight


class RuntimePreflightTests(unittest.TestCase):
    def test_private_paths_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            report = run_runtime_preflight(
                database_path=Path(root) / "harness.sqlite",
                output_dir=Path(root) / "output",
                worktree_root=Path(root) / "worktrees",
            )
            self.assertIn(report["status"], {"ready", "degraded_readonly"})
            self.assertEqual("ready", report["checks"]["database"]["status"])

    def test_unavailable_control_path_is_readonly_fallback(self) -> None:
        report = run_runtime_preflight(database_path="/proc/harness.sqlite")
        self.assertEqual("degraded_readonly", report["status"])
        self.assertIn("database", report["failed_checks"])
        self.assertIn("use_private_temp", report["checks"]["database"]["fallback"])


if __name__ == "__main__":
    unittest.main()
