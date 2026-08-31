from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.capability_contracts import CapabilityRequest
from app.mcp_runtime_factory import (
    McpRuntimeFactoryError,
    _mcp_python_executable,
    build_persistent_mcp_runtime,
)
from app.mcp_transport import McpTransportUnavailable
from app.plugin_inventory import PluginInventoryError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "mcp_stdio_fixture_server.py"
INPUT_SCHEMA = ROOT / "config" / "schemas" / "mcp_tools" / "yunxiao_workitem_read.v1.json"
RESULT_SCHEMA = ROOT / "config" / "schemas" / "mcp_result_envelope.v1.json"


class McpRuntimeFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.harness_root = root / "harness"
        self.plugin_root = root / "fixture-plugin"
        self.state_root = root / "state"
        self.harness_root.mkdir()
        self.plugin_root.mkdir()
        self.state_root.mkdir()
        self._write_plugin()
        self._write_schemas()
        self.inventory_path = self.harness_root / "plugin_inventory.json"
        self.manifest_path = self.harness_root / "mcp_capabilities.json"
        self._write_inventory()
        self._write_manifest(enabled=False)

    def _write_plugin(self) -> None:
        (self.plugin_root / ".codex-plugin").mkdir()
        (self.plugin_root / "scripts").mkdir()
        (self.plugin_root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "fixture-plugin", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (self.plugin_root / "capabilities.json").write_text(
            json.dumps(
                {
                    "plugin": "fixture-plugin",
                    "plugin_version": "1.0.0",
                    "capabilities": [
                        {
                            "name": "workitem.read",
                            "mutation_level": "L1",
                            "enabled": False,
                            "dependencies": [],
                        }
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (self.plugin_root / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "fixture": {
                            "command": "python3",
                            "args": ["./scripts/fixture_mcp.py"],
                            "cwd": ".",
                            "env_vars": ["MCP_FIXTURE_MODE"],
                        }
                    }
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (self.plugin_root / "scripts" / "fixture_mcp.py").write_bytes(FIXTURE.read_bytes())

    def test_project_venv_python_is_selected_when_available(self) -> None:
        launcher = self.harness_root / ".venv" / "bin" / "python"
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(Path(sys.executable).resolve())

        self.assertEqual(launcher, _mcp_python_executable(self.harness_root))

    def _write_schemas(self) -> None:
        schema_root = self.harness_root / "config" / "schemas"
        (schema_root / "mcp_tools").mkdir(parents=True)
        (schema_root / "mcp_tools" / INPUT_SCHEMA.name).write_bytes(INPUT_SCHEMA.read_bytes())
        (schema_root / RESULT_SCHEMA.name).write_bytes(RESULT_SCHEMA.read_bytes())

    def _write_inventory(self) -> None:
        paths = (
            ".codex-plugin/plugin.json",
            ".mcp.json",
            "capabilities.json",
            "scripts/fixture_mcp.py",
        )
        source_hashes = {
            relative: hashlib.sha256((self.plugin_root / relative).read_bytes()).hexdigest()
            for relative in paths
        }
        payload = {
            "schema_version": "his-plugin-inventory.v1",
            "plugins": [
                {
                    "name": "fixture-plugin",
                    "version": "1.0.0",
                    "capabilities_sha256": source_hashes["capabilities.json"],
                    "capabilities": ["workitem.read"],
                    "sources_sha256": source_hashes,
                }
            ],
        }
        self.inventory_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def _write_manifest(self, *, enabled: bool, server: str = "fixture") -> None:
        input_relative = "config/schemas/mcp_tools/yunxiao_workitem_read.v1.json"
        result_relative = "config/schemas/mcp_result_envelope.v1.json"
        payload = {
            "schema_version": "his-mcp-capabilities.v1",
            "capabilities": [
                {
                    "capability": "workitem.read",
                    "provider": "yunxiao",
                    "server": server,
                    "tool": "fixture_read",
                    "contract_version": "workitem-read.v1",
                    "mutation_level": "L1",
                    "required_scopes": ["workitem:read"],
                    "timeout_seconds": 2,
                    "max_result_bytes": 262144,
                    "input_schema_path": input_relative,
                    "input_schema_sha256": hashlib.sha256(
                        (self.harness_root / input_relative).read_bytes()
                    ).hexdigest(),
                    "result_schema_path": result_relative,
                    "result_schema_sha256": hashlib.sha256(
                        (self.harness_root / result_relative).read_bytes()
                    ).hexdigest(),
                    "enabled": enabled,
                    "disabled_reason": "" if enabled else "phase_1b_gateway_transport_pending",
                }
            ],
        }
        self.manifest_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def _request(self) -> CapabilityRequest:
        return CapabilityRequest.from_dict(
            {
                "schema_version": "his-capability-request.v1",
                "request_id": "factory-request-1",
                "capability": "workitem.read",
                "provider": "yunxiao",
                "mode": "preview",
                "mutation_level": "L1",
                "authorization": {"explicit": False, "scope": ["workitem:read"]},
                "input": {
                    "work_item_id": "DFHIS-1",
                    "include_comments": True,
                    "include_attachments": False,
                    "page_cursor": "",
                    "page_size": 20,
                },
                "context": {"task_id": "task-1", "run_id": "run-1"},
            }
        )

    def _build(self, **changes: object):
        arguments = {
            "harness_root": self.harness_root,
            "manifest_path": self.manifest_path,
            "plugin_inventory_path": self.inventory_path,
            "plugin_roots": [self.plugin_root],
            "state_root": self.state_root,
            "environment": {"MCP_FIXTURE_MODE": "healthy"},
        }
        return build_persistent_mcp_runtime(**{**arguments, **changes})

    def test_builds_persistent_bundle_but_disabled_descriptor_never_launches(self) -> None:
        first = self._build()

        with mock.patch("app.mcp_stdio_transport.subprocess.Popen") as popen:
            execution = first.runtime.execute(self._request())

        self.assertEqual("unsupported", execution.result.status)
        self.assertEqual("MCP_CAPABILITY_DISABLED", execution.result.audit["error_code"])
        popen.assert_not_called()
        self.assertEqual(self.state_root / "mcp.sqlite", first.store.path)
        second = self._build()
        self.assertEqual("passed", second.store.verify_integrity()["status"])

    def test_enabled_fixture_runs_gateway_stdio_and_persists_recoverable_evidence(self) -> None:
        self._write_manifest(enabled=True)
        bundle = self._build()

        execution = bundle.runtime.execute(self._request())

        self.assertEqual("success", execution.result.status)
        reference = execution.result.evidence[0]["ref"]
        rebuilt = self._build()
        self.assertEqual({"fixture": "ok"}, rebuilt.store.load_evidence(reference)["data"])
        events = rebuilt.store.list_audit_events()
        self.assertEqual(1, len(events))
        self.assertEqual(reference, events[0]["evidence_ref"])
        self.assertEqual("passed", rebuilt.store.verify_integrity()["status"])

    def test_inventory_drift_is_rejected_before_runtime_construction(self) -> None:
        (self.plugin_root / "scripts" / "fixture_mcp.py").write_text(
            "print('drift')\n", encoding="utf-8"
        )

        with self.assertRaises(PluginInventoryError):
            self._build()

        self.assertFalse((self.state_root / "mcp.sqlite").exists())

    def test_enabled_descriptor_requires_a_frozen_server_configuration(self) -> None:
        self._write_manifest(enabled=True, server="missing")

        with self.assertRaises(McpRuntimeFactoryError):
            self._build()

    def test_rejects_relative_or_symlinked_state_roots_and_root_count_drift(self) -> None:
        with self.assertRaises(McpRuntimeFactoryError):
            self._build(state_root=Path("relative-state"))
        linked = self.harness_root / "linked-state"
        linked.symlink_to(self.state_root)
        with self.assertRaises(McpRuntimeFactoryError):
            self._build(state_root=linked)
        with self.assertRaises(PluginInventoryError):
            self._build(plugin_roots=[])

    def test_inventory_manifest_must_belong_to_the_declared_harness_root(self) -> None:
        outside = self.harness_root.parent / "outside-inventory.json"
        outside.write_bytes(self.inventory_path.read_bytes())

        with self.assertRaises(McpRuntimeFactoryError):
            self._build(plugin_inventory_path=outside)

    def test_frozen_but_unrouted_server_is_not_exposed_by_the_transport(self) -> None:
        mcp_path = self.plugin_root / ".mcp.json"
        payload = json.loads(mcp_path.read_text(encoding="utf-8"))
        payload["mcpServers"]["unrouted"] = dict(payload["mcpServers"]["fixture"])
        mcp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        self._write_inventory()

        bundle = self._build()

        with mock.patch("app.mcp_stdio_transport.subprocess.Popen") as popen:
            with self.assertRaises(McpTransportUnavailable):
                bundle.transport.call(
                    server="unrouted",
                    tool="fixture_read",
                    arguments={},
                    timeout_seconds=2,
                    trace_id="unrouted-1",
                )
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
