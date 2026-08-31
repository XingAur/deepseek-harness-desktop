from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/external_io_inventory.py"
MATRIX = ROOT / "config/role_capability_skill_matrix.json"


class ExternalIoInventoryCliTests(unittest.TestCase):
    def _fixture(self, root: Path, *, reviewed_hash: str | None = None) -> tuple[Path, Path]:
        source = root / "network.py"
        source.write_text(
            "import urllib.request\nurllib.request.urlopen('https://secret.example.invalid')\n",
            encoding="utf-8",
        )
        file_sha256 = reviewed_hash or hashlib.sha256(source.read_bytes()).hexdigest()
        policy = root / "boundaries.json"
        policy.write_text(
            json.dumps(
                {
                    "schema_version": "his-external-io-boundaries.v1",
                    "roots": [
                        {"root_id": "harness", "source": "harness_root", "value": "."}
                    ],
                    "rules": [
                        {
                            "root_id": "harness",
                            "relative_path": "network.py",
                            "file_sha256": file_sha256,
                            "findings": [
                                {
                                    "category": "network",
                                    "symbol": "urllib.request.urlopen",
                                    "occurrence": 1,
                                }
                            ],
                            "disposition": "compatibility_quarantine",
                            "owner": "test",
                            "rationale": "Reviewed test boundary.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return policy, source

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(TOOL), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_passing_policy_returns_zero_and_summary_does_not_echo_source_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy, _ = self._fixture(Path(temp_dir))
            result = self._run(
                "validate",
                "--policy",
                str(policy),
                "--matrix",
                str(MATRIX),
                "--format",
                "summary",
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("status=passed", result.stdout)
        self.assertIn("compatibility_debt=", result.stdout)
        self.assertNotIn("secret.example.invalid", result.stdout + result.stderr)

    def test_new_direct_io_call_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy, source = self._fixture(Path(temp_dir))
            source.write_text(
                source.read_text(encoding="utf-8")
                + "urllib.request.urlopen('https://second.example.invalid')\n",
                encoding="utf-8",
            )
            result = self._run(
                "validate",
                "--policy",
                str(policy),
                "--matrix",
                str(MATRIX),
                "--format",
                "summary",
            )

        self.assertEqual(1, result.returncode)
        self.assertIn("status=failed", result.stdout)

    def test_changed_direct_io_file_hash_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy, _ = self._fixture(Path(temp_dir), reviewed_hash="0" * 64)
            result = self._run(
                "validate",
                "--policy",
                str(policy),
                "--matrix",
                str(MATRIX),
                "--format",
                "summary",
            )

        self.assertEqual(1, result.returncode)
        self.assertIn("source_drift=1", result.stdout)

    def test_malformed_policy_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = Path(temp_dir) / "boundaries.json"
            policy.write_text("{not-json", encoding="utf-8")
            result = self._run(
                "validate",
                "--policy",
                str(policy),
                "--matrix",
                str(MATRIX),
                "--format",
                "summary",
            )

        self.assertEqual(2, result.returncode)
        self.assertNotIn("not-json", result.stdout + result.stderr)

    def test_scan_writes_only_the_explicit_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy, _ = self._fixture(root)
            output = root / "inventory.json"
            result = self._run(
                "scan",
                "--policy",
                str(policy),
                "--output",
                str(output),
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("his-external-io-inventory.v1", payload["schema_version"])
        self.assertEqual(1, len(payload["findings"]))


if __name__ == "__main__":
    unittest.main()
