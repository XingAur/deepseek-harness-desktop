from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from app.real_replay_suite import load_replay_manifest, run_replay_suite


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fixtures" / "replay" / "real_requirements_v1.json"


class RealReplaySuiteTests(unittest.TestCase):
    def test_manifest_has_required_real_case_coverage_and_contract_fields(self) -> None:
        manifest = load_replay_manifest(MANIFEST)
        cases = manifest["cases"]

        self.assertEqual(10, len(cases))
        self.assertEqual(
            Counter({"frontend": 3, "backend": 2, "fullstack": 2, "ordering": 1, "high_risk": 2}),
            Counter(case["category"] for case in cases),
        )
        self.assertGreaterEqual(len({case["entity_id"] for case in cases}), 8)
        for case in cases:
            for key in (
                "source_refs",
                "allowed_paths",
                "expected_diff_features",
                "verify_commands",
                "negative",
                "manual_acceptance",
            ):
                self.assertTrue(case[key], f"{case['id']} missing {key}")

    def test_all_fixed_replays_pass_without_claiming_business_validity(self) -> None:
        result = run_replay_suite(MANIFEST)

        self.assertEqual("passed", result["status"])
        self.assertEqual(10, result["summary"]["passed"])
        self.assertTrue(result["technical_valid"])
        self.assertFalse(result["business_valid"])
        self.assertFalse(result["runtime_verified"])
        self.assertFalse(result["promotion_enabled"])
        self.assertTrue(all(case["negative_status"] == "passed" for case in result["cases"]))

    def test_tampered_expected_ownership_fails_closed(self) -> None:
        manifest = load_replay_manifest(MANIFEST)
        tampered = copy.deepcopy(manifest)
        tampered["cases"][0]["expected"]["ownership"]["backend"] = "required"

        result = run_replay_suite(tampered, manifest_base=MANIFEST.parent)

        self.assertEqual("failed", result["status"])
        self.assertFalse(result["technical_valid"])
        self.assertIn("ownership.backend", json.dumps(result, ensure_ascii=False))

    def test_cli_writes_truthful_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "replay_suite.py"),
                    "--manifest",
                    str(MANIFEST),
                    "--output-dir",
                    temp_dir,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads((Path(temp_dir) / "real_replay_result.json").read_text(encoding="utf-8"))
            report = (Path(temp_dir) / "real_replay_report.md").read_text(encoding="utf-8")
            self.assertEqual("passed", payload["status"])
            self.assertFalse(payload["business_valid"])
            self.assertIn("不代表业务运行时通过", report)


if __name__ == "__main__":
    unittest.main()
