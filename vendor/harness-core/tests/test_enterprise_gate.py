from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.enterprise_gate import (
    build_stage_command,
    run_enterprise_gate,
    run_gate_stage,
    sanitize_environment,
    stage_timeout_seconds,
    scan_source_secrets,
)
from app.version import VERSION


ROOT = Path(__file__).resolve().parents[1]


class EnterpriseGateTests(unittest.TestCase):
    def test_gate_result_records_current_interpreter_and_source_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_enterprise_gate(
                project_root=ROOT,
                output_dir=temp_dir,
                stages=("compile", "secret"),
            )

        self.assertEqual(VERSION, result["version"])
        self.assertEqual(sys.executable, result["interpreter"])
        self.assertEqual(platform.python_version(), result["python_version"])
        self.assertEqual(300, result["stage_timeout_seconds"])
        self.assertEqual(1200, result["unit_stage_timeout_seconds"])
        self.assertEqual(1200, stage_timeout_seconds("unit"))

    def test_timeout_is_a_failed_stage_with_explicit_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "app.enterprise_gate.subprocess.run",
                side_effect=subprocess.TimeoutExpired([sys.executable], 1200, output="", stderr=""),
            ):
                result = run_gate_stage(
                    "unit",
                    project_root=ROOT,
                    output_dir=Path(temp_dir),
                    iteration=1,
                )

        self.assertEqual("failed", result["status"])
        self.assertEqual("timeout", result["reason"])

    def test_ci_release_step_uses_current_version_default(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "enterprise-core.yml").read_text(encoding="utf-8")

        self.assertNotIn("--version 0.64.0", workflow)
        self.assertIn("tools/build_release_bundle.py --output-dir", workflow)

    def test_compile_stage_uses_no_write_syntax_checker(self) -> None:
        command, output_dir = build_stage_command(
            "compile",
            project_root=ROOT,
            output_dir=ROOT / "test-output",
        )

        self.assertIsNone(output_dir)
        self.assertEqual(str(ROOT / "tools" / "syntax_check.py"), command[1])
        self.assertNotIn("compileall", command)

    def test_environment_removes_secret_bearing_variables(self) -> None:
        sanitized = sanitize_environment(
            {
                "PATH": "/usr/bin",
                "HOME": "/tmp/home",
                "OPENAI_API_KEY": "must-not-pass",
                "ALIYUN_DEVOPS_PAT": "must-not-pass",
                "DB_PASSWORD": "must-not-pass",
                "HARNESS_CREDENTIALS_FILE": "/tmp/credentials.json",
            }
        )

        self.assertEqual("/usr/bin", sanitized["PATH"])
        self.assertEqual("/tmp/home", sanitized["HOME"])
        self.assertNotIn("OPENAI_API_KEY", sanitized)
        self.assertNotIn("ALIYUN_DEVOPS_PAT", sanitized)
        self.assertNotIn("DB_PASSWORD", sanitized)
        self.assertNotIn("HARNESS_CREDENTIALS_FILE", sanitized)

    def test_secret_scan_detects_high_confidence_literal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app").mkdir()
            (root / "app" / "safe.py").write_text("api_key_name = 'openai_api_key'\n", encoding="utf-8")
            (root / "app" / "unsafe.py").write_text(
                "header = 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456'\n",
                encoding="utf-8",
            )

            result = scan_source_secrets(root)

            self.assertEqual("failed", result["status"])
            self.assertEqual(1, len(result["findings"]))
            self.assertEqual("app/unsafe.py", result["findings"][0]["path"])
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz", json.dumps(result))

    def test_fast_cli_subset_writes_truthful_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "enterprise_gate.py"),
                    "--output-dir",
                    temp_dir,
                    "--stages",
                    "compile,replay,secret",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads((Path(temp_dir) / "enterprise_gate_result.json").read_text(encoding="utf-8"))
            self.assertEqual("passed", payload["status"])
            self.assertTrue(payload["technical_valid"])
            self.assertEqual(VERSION, payload["version"])
            self.assertEqual(sys.executable, payload["interpreter"])
            self.assertFalse(payload["business_valid"])
            self.assertFalse(payload["external_calls"])
            self.assertFalse(payload["real_git_remote_writes_used"])
            self.assertTrue(payload["local_git_fixture_only"])
            self.assertTrue(
                any(
                    "未写云效或真实 Git 远端" in boundary
                    for boundary in payload["boundaries"]
                )
            )
            checkpoint = json.loads((Path(temp_dir) / "enterprise_gate_checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual("completed", checkpoint["status"])
            self.assertEqual(1, checkpoint["iterations_completed"])
            self.assertTrue((Path(temp_dir) / "iteration_01" / "iteration_result.json").is_file())


if __name__ == "__main__":
    unittest.main()
