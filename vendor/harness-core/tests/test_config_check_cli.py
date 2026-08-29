from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConfigCheckCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "tools/config_check.py", *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_default_json_output_remains_v033_shape(self) -> None:
        completed = self.run_cli("--profile-key", "team-share-example", "--json")
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("0.22-harness-config-summary", payload["version"])
        self.assertNotIn("resolved_config", payload)

    def test_explicit_resolved_config_writes_readonly_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = self.run_cli(
                "--profile-key", "team-share-example",
                "--include-resolved-config",
                "--run-override-json", '{"orchestration":{"mode":"dynamic_plan"}}',
                "--output-dir", tmp,
                "--json",
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("1.0-resolved-config", payload["resolved_config"]["schema_version"])
            self.assertEqual("dynamic_plan", payload["resolved_config"]["values"]["orchestration"]["mode"])
            self.assertTrue((Path(tmp) / "harness_resolved_config.json").exists())
            self.assertTrue((Path(tmp) / "harness_resolved_config.md").exists())

    def test_layer_arguments_require_explicit_resolver_flag(self) -> None:
        completed = self.run_cli("--project-config", "/tmp/project.json", "--json")
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("--include-resolved-config", completed.stderr + completed.stdout)

    def test_strict_rejects_and_redacts_literal_secret(self) -> None:
        literal = "cli-literal-secret"
        completed = self.run_cli(
            "--profile-key", "team-share-example",
            "--include-resolved-config",
            "--run-override-json", json.dumps({"credentials": {"api_key": literal}}),
            "--strict", "--json",
        )
        self.assertEqual(1, completed.returncode)
        self.assertNotIn(literal, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("failed", payload["resolved_config"]["validation"]["status"])


if __name__ == "__main__":
    unittest.main()
