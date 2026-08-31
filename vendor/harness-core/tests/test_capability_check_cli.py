from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CapabilityCheckCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.plugin_root = self.root / "plugin"
        (self.plugin_root / "scripts").mkdir(parents=True)
        self._write_plugin()
        self.config_path = self.root / "capabilities.json"
        self._write_config()
        self.request_path = self.root / "request.json"
        self._write_request()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_plugin(self, *, enabled: bool = True, provider: str = "yunxiao") -> None:
        runner = self.plugin_root / "scripts" / "runner.py"
        runner.write_text(
            "import argparse, json\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('--request', required=True)\n"
            "parser.add_argument('--output', required=True)\n"
            "args = parser.parse_args()\n"
            "request = json.loads(open(args.request, encoding='utf-8').read())\n"
            "result = {\n"
            "  'schema_version': 'his-capability-result.v1',\n"
            "  'request_id': request['request_id'],\n"
            "  'capability': request['capability'],\n"
            "  'provider': request['provider'],\n"
            "  'status': 'success', 'mutation_level': 'L1', 'changed': False,\n"
            "  'summary': 'previewed', 'data': {'source': 'fixture'}, 'evidence': [],\n"
            "  'warnings': [], 'blockers': [], 'audit': {}\n"
            "}\n"
            "open(args.output, 'w', encoding='utf-8').write(json.dumps(result))\n",
            encoding="utf-8",
        )
        capability = {
            "name": "workitem.read", "provider": provider,
            "contract_version": "v1", "mutation_level": "L1",
            "credential_class": "none", "enabled": enabled,
            "scopes": ["workitem:read"],
        }
        if enabled:
            capability["entrypoint"] = "scripts/runner.py"
        else:
            capability["disabled_reason"] = "fixture disabled"
        (self.plugin_root / "capabilities.json").write_text(json.dumps({
            "schema_version": "his-capabilities.v1", "plugin": "fixture",
            "plugin_version": "1.0.0", "capabilities": [capability],
        }), encoding="utf-8")

    def _write_config(self, **overrides: object) -> None:
        payload = {
            "schema_version": "his-capability-runtime-config.v1",
            "routing_mode": "legacy",
            "plugin_roots": [str(self.plugin_root)],
            "external_writes_default": False,
            "default_timeout_seconds": 5,
        }
        payload.update(overrides)
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_request(self, **overrides: object) -> None:
        payload = {
            "schema_version": "his-capability-request.v1", "request_id": "fixture-1",
            "capability": "workitem.read", "provider": "yunxiao", "mode": "preview",
            "mutation_level": "L1", "authorization": {"explicit": False, "scope": []},
            "input": {}, "context": {},
        }
        payload.update(overrides)
        self.request_path.write_text(json.dumps(payload), encoding="utf-8")

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "tools/capability_check.py", "--config", str(self.config_path), *args],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def payload(self, completed: subprocess.CompletedProcess[str]) -> dict:
        self.assertNotIn("Traceback", completed.stderr)
        return json.loads(completed.stdout)

    def assert_sanitized_json_failure(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(0, completed.returncode)
        payload = self.payload(completed)
        self.assertEqual("failed", payload["status"])
        self.assertNotIn(str(self.root), completed.stdout + completed.stderr)

    def test_list_returns_sorted_non_sensitive_descriptors(self) -> None:
        completed = self.run_cli("list", "--json")
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = self.payload(completed)
        self.assertEqual("his-capability-check.v1", payload["schema_version"])
        self.assertEqual("list", payload["command"])
        self.assertEqual("success", payload["status"])
        self.assertEqual("workitem.read", payload["data"]["capabilities"][0]["capability"])

    def test_inspect_validate_and_preview_return_stable_json(self) -> None:
        inspect = self.run_cli("inspect", "--capability", "workitem.read", "--provider", "yunxiao", "--json")
        validate = self.run_cli("validate", "--json")
        preview = self.run_cli("preview", "--request", str(self.request_path), "--json")
        for command, completed in (("inspect", inspect), ("validate", validate), ("preview", preview)):
            with self.subTest(command=command):
                self.assertEqual(0, completed.returncode, completed.stderr)
                payload = self.payload(completed)
                self.assertEqual(command, payload["command"])
                self.assertEqual("success", payload["status"])
        preview_payload = self.payload(preview)
        self.assertEqual("allowed", preview_payload["data"]["permission"]["status"])
        self.assertEqual("success", preview_payload["data"]["execution"]["result"]["status"])

    def test_relative_plugin_root_resolves_from_the_runtime_config_directory(self) -> None:
        self._write_config(plugin_roots=["plugin"])

        completed = self.run_cli("list", "--json")

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("success", self.payload(completed)["status"])

    def test_invalid_config_is_stable_json_failure(self) -> None:
        for overrides in (
            {"default_timeout_seconds": True},
            {"unknown": True},
        ):
            with self.subTest(overrides=overrides):
                self._write_config(**overrides)
                completed = self.run_cli("validate", "--json")
                self.assertNotEqual(0, completed.returncode)
                payload = self.payload(completed)
                self.assertEqual("failed", payload["status"])
                self.assertIn("error", payload)

    def test_invalid_utf8_config_is_sanitized_json_failure(self) -> None:
        self.config_path.write_bytes(b"\xff\xfe")

        self.assert_sanitized_json_failure(self.run_cli("validate", "--json"))

    def test_invalid_utf8_manifest_is_sanitized_json_failure(self) -> None:
        (self.plugin_root / "capabilities.json").write_bytes(b"\xff\xfe")

        self.assert_sanitized_json_failure(self.run_cli("validate", "--json"))

    def test_inspect_without_provider_rejects_ambiguous_capability(self) -> None:
        second = self.root / "second"
        second.mkdir()
        (second / "scripts").mkdir()
        (second / "scripts" / "runner.py").write_text("", encoding="utf-8")
        (second / "capabilities.json").write_text(json.dumps({
            "schema_version": "his-capabilities.v1", "plugin": "second", "plugin_version": "1.0.0",
            "capabilities": [{"name": "workitem.read", "provider": "other", "contract_version": "v1",
                              "mutation_level": "L1", "credential_class": "none", "entrypoint": "scripts/runner.py",
                              "enabled": True, "scopes": ["workitem:read"]}],
        }), encoding="utf-8")
        self._write_config(plugin_roots=[str(self.plugin_root), str(second)])
        completed = self.run_cli("inspect", "--capability", "workitem.read", "--json")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("failed", self.payload(completed)["status"])

    def test_preview_rejects_apply_request_and_missing_or_invalid_request_file(self) -> None:
        self._write_request(mode="apply")
        apply = self.run_cli("preview", "--request", str(self.request_path), "--json")
        missing = self.run_cli("preview", "--json")
        self.request_path.write_text("{", encoding="utf-8")
        invalid = self.run_cli("preview", "--request", str(self.request_path), "--json")
        for completed in (apply, missing, invalid):
            with self.subTest(arguments=completed.args):
                self.assertNotEqual(0, completed.returncode)
                self.assertEqual("failed", self.payload(completed)["status"])

    def test_preview_reports_permission_and_execution_when_disabled(self) -> None:
        self._write_plugin(enabled=False)
        completed = self.run_cli("preview", "--request", str(self.request_path), "--json")
        self.assertNotEqual(0, completed.returncode)
        payload = self.payload(completed)
        self.assertEqual("blocked", payload["status"])
        self.assertEqual("allowed", payload["data"]["permission"]["status"])
        self.assertEqual("blocked", payload["data"]["execution"]["result"]["status"])

    def test_preview_reports_permission_block_without_running_entrypoint(self) -> None:
        self._write_request(mutation_level="L2")
        completed = self.run_cli("preview", "--request", str(self.request_path), "--json")
        self.assertNotEqual(0, completed.returncode)
        payload = self.payload(completed)
        self.assertEqual("blocked", payload["status"])
        self.assertEqual("blocked", payload["data"]["permission"]["status"])
        self.assertEqual("CAPABILITY_PERMISSION_DENIED", payload["data"]["execution"]["result"]["audit"]["error_code"])

    def test_help_has_no_apply_subcommand(self) -> None:
        completed = subprocess.run(
            ["python3", "tools/capability_check.py", "--help"], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(0, completed.returncode)
        self.assertNotIn("apply", completed.stdout.lower())
        rejected = self.run_cli("apply", "--json")
        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual("failed", self.payload(rejected)["status"])

    def test_invalid_command_does_not_treat_config_value_as_subcommand(self) -> None:
        completed = subprocess.run(
            ["python3", "tools/capability_check.py", "--config", "list", "apply", "--json"],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("unknown", self.payload(completed)["command"])


if __name__ == "__main__":
    unittest.main()
