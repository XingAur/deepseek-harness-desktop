from __future__ import annotations

import ast
import hashlib
import inspect
import json
import unittest
from pathlib import Path

from app import database
from app.mcp_capability_registry import McpCapabilityRegistry
from app.mcp_persistence import SqliteMcpStore
from app.mcp_runtime_factory import build_persistent_mcp_runtime
from app.mcp_stdio_transport import StdioMcpTransport, load_stdio_server_configs


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "mcp_capabilities.json"


class McpPhase1DRuntimeAcceptanceTests(unittest.TestCase):
    def test_concrete_transport_and_factory_use_the_frozen_plugin_path(self) -> None:
        self.assertTrue(inspect.isclass(StdioMcpTransport))
        source = inspect.getsource(build_persistent_mcp_runtime)
        self.assertLess(
            source.index("verify_plugin_inventory"),
            source.index("load_stdio_server_configs"),
        )
        self.assertIn("verified_plugins", source)
        self.assertNotIn("subprocess", source)
        self.assertTrue(callable(load_stdio_server_configs))

    def test_store_is_independent_from_main_database_and_append_only(self) -> None:
        self.assertNotIn("mcp_", database.DB_PATH.name)
        database_source = (ROOT / "app" / "database.py").read_text(encoding="utf-8")
        self.assertIn("HARNESS_SCHEMA_VERSION = 73", database_source)
        self.assertNotIn("mcp_evidence_records", database_source)
        store_source = inspect.getsource(SqliteMcpStore)
        for trigger in (
            "mcp_evidence_no_update",
            "mcp_evidence_no_delete",
            "mcp_audit_no_update",
            "mcp_audit_no_delete",
        ):
            self.assertIn(trigger, store_source)

    def test_gateway_still_has_one_transport_call_and_no_retry_loop(self) -> None:
        source = (ROOT / "app" / "mcp_gateway.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        execute = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )
        calls = [
            node
            for node in ast.walk(execute)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "call"
        ]
        loops = [node for node in ast.walk(execute) if isinstance(node, (ast.For, ast.While))]
        self.assertEqual(1, len(calls))
        self.assertEqual([], loops)

    def test_all_readonly_descriptors_and_routes_are_native_mcp(self) -> None:
        registry = McpCapabilityRegistry.from_file(MANIFEST, harness_root=ROOT)
        descriptors = {
            (item.capability, item.provider): item
            for item in registry.list_capabilities()
        }
        for key in (
            ("workitem.read", "yunxiao"),
            ("gitlab.read", "gitlab"),
            ("database.inspect", "postgresql"),
        ):
            self.assertTrue(descriptors[key].enabled)
            self.assertEqual("", descriptors[key].disabled_reason)

        matrix = json.loads(
            (ROOT / "config" / "role_capability_skill_matrix.json").read_text(
                encoding="utf-8"
            )
        )
        routes = {
            (item["capability"], item["provider"]): item
            for item in matrix["capability_routes"]
        }
        for key in (
            ("workitem.read", "yunxiao"),
            ("gitlab.read", "gitlab"),
            ("database.inspect", "postgresql"),
        ):
            self.assertEqual("mcp", routes[key]["execution_kind"])
            self.assertEqual("native", routes[key]["migration_state"])
            self.assertEqual("mcp_required", routes[key]["required_boundary"])

    def test_manifest_adds_no_write_generic_shell_or_raw_sql_tool(self) -> None:
        registry = McpCapabilityRegistry.from_file(MANIFEST, harness_root=ROOT)
        forbidden = {"request", "execute", "proxy", "raw_sql", "shell", "command"}
        for descriptor in registry.list_capabilities():
            self.assertLessEqual(descriptor.mutation_level.value, 1)
            self.assertNotIn(descriptor.tool, forbidden)
            self.assertNotIn("write", descriptor.capability)

    def test_external_io_policy_registers_only_the_hash_pinned_process_boundary(self) -> None:
        policy = json.loads(
            (ROOT / "config" / "external_io_boundaries.v1.json").read_text(
                encoding="utf-8"
            )
        )
        rules = [
            item
            for item in policy["rules"]
            if item["relative_path"] == "app/mcp_stdio_transport.py"
        ]
        self.assertEqual(1, len(rules))
        rule = rules[0]
        self.assertEqual("control_plane_internal", rule["disposition"])
        self.assertEqual(
            hashlib.sha256((ROOT / rule["relative_path"]).read_bytes()).hexdigest(),
            rule["file_sha256"],
        )
        self.assertEqual(
            [{"category": "process", "symbol": "subprocess.Popen", "occurrence": 1}],
            rule["findings"],
        )
        self.assertIn("hash-pinned MCP process boundary", rule["rationale"])

    def test_architecture_gate_freezes_runtime_and_phase_1d_primary_modules(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")
        for module in (
            "tests.test_mcp_stdio_transport",
            "tests.test_mcp_persistence",
            "tests.test_mcp_runtime_factory",
            "tests.test_mcp_phase_1b_runtime_acceptance",
            "tests.test_mcp_phase_1d_primary_activation",
            "tests.test_mcp_primary_provider_adapter",
            "tests.test_mcp_connector_server_contracts",
        ):
            self.assertIn(module, script)

    def test_status_docs_describe_mcp_primary_and_live_connection_boundary(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for source in (readme, changelog):
            self.assertIn("stdio", source)
            self.assertIn("mcp.sqlite", source)
            self.assertIn("Phase 1D", source)
            self.assertIn("路由", source)
            self.assertIn("fail closed", source)
            self.assertIn("GitLab", source)
            self.assertIn("PostgreSQL", source)


if __name__ == "__main__":
    unittest.main()
