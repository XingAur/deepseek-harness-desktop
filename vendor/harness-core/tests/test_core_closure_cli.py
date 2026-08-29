from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "harnesses" / "his_requirement_workflow.py"


class CoreClosureCliTests(unittest.TestCase):
    def test_help_lists_core_closure_trial(self) -> None:
        completed = subprocess.run(
            ["python3", str(CLI), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("core-closure-trial", completed.stdout)

    def test_help_lists_auto_local(self) -> None:
        completed = subprocess.run(
            ["python3", str(CLI), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("auto-local", completed.stdout)

    def test_help_lists_explicit_apply_flag(self) -> None:
        completed = subprocess.run(
            ["python3", str(CLI), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("--apply-approved-diff", completed.stdout)

    def test_help_lists_acceptance_contract_file(self) -> None:
        completed = subprocess.run(
            ["python3", str(CLI), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("--acceptance-contract-file", completed.stdout)


if __name__ == "__main__":
    unittest.main()
