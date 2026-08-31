from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/mcp_capability_check.py"
MANIFEST = ROOT / "config/mcp_capabilities.json"


class McpCapabilityCheckCliTests(unittest.TestCase):
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

    def test_validate_reports_three_enabled_read_only_descriptors(self) -> None:
        result = self._run("validate", "--manifest", str(MANIFEST))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("status=valid", result.stdout)
        self.assertIn("capabilities=3", result.stdout)
        self.assertIn("enabled=3", result.stdout)
        self.assertIn("disabled=0", result.stdout)

    def test_list_is_deterministic_and_contains_metadata_only(self) -> None:
        result = self._run(
            "list", "--manifest", str(MANIFEST), "--format", "summary"
        )

        self.assertEqual(0, result.returncode, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("capability=")]
        self.assertEqual(
            ["database.inspect", "gitlab.read", "workitem.read"],
            [line.split()[0].split("=", 1)[1] for line in lines],
        )
        self.assertNotIn("properties", result.stdout)
        self.assertNotIn("input_schema", result.stdout)
        self.assertNotIn("environment", result.stdout.lower())

    def test_inspect_returns_enabled_descriptor_metadata_without_schema_body(self) -> None:
        result = self._run(
            "inspect",
            "--manifest",
            str(MANIFEST),
            "--capability",
            "workitem.read",
            "--provider",
            "yunxiao",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("server=yunxiao", result.stdout)
        self.assertIn("tool=workitem_get", result.stdout)
        self.assertIn("enabled=true", result.stdout)
        self.assertNotIn("disabled_reason=", result.stdout)
        self.assertNotIn("properties", result.stdout)

    def test_missing_capability_returns_one(self) -> None:
        result = self._run(
            "inspect",
            "--manifest",
            str(MANIFEST),
            "--capability",
            "missing.read",
            "--provider",
            "missing",
        )

        self.assertEqual(1, result.returncode)
        self.assertIn("status=not_found", result.stdout)

    def test_malformed_manifest_returns_two_without_echoing_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config"
            config.mkdir()
            manifest = config / "mcp_capabilities.json"
            manifest.write_text("{secret-not-json", encoding="utf-8")
            result = self._run("validate", "--manifest", str(manifest))

        self.assertEqual(2, result.returncode)
        self.assertNotIn("secret-not-json", result.stdout + result.stderr)

    def test_cli_source_has_no_transport_invocation(self) -> None:
        source = TOOL.read_text(encoding="utf-8")

        self.assertNotIn("McpTransport", source)
        self.assertNotIn(".call(", source)


if __name__ == "__main__":
    unittest.main()
