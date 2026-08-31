from __future__ import annotations

import ast
import inspect
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import app.mcp_transport as mcp_transport
from app.mcp_capability_registry import McpCapabilityRegistry
from app.mcp_transport import DisabledMcpTransport, McpTransportUnavailable


HARNESS_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = HARNESS_ROOT / "config/mcp_capabilities.json"
SENSITIVE_KEY = re.compile(
    r"(?:authorization|credential|dsn|password|secret|token)", re.IGNORECASE
)


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


class McpPhase1AAcceptanceTests(unittest.TestCase):
    def test_current_manifest_is_exactly_read_only_and_enabled(self) -> None:
        registry = McpCapabilityRegistry.from_file(MANIFEST, harness_root=HARNESS_ROOT)
        descriptors = registry.list_capabilities()

        self.assertEqual(
            [
                ("database.inspect", "postgresql"),
                ("gitlab.read", "gitlab"),
                ("workitem.read", "yunxiao"),
            ],
            [(item.capability, item.provider) for item in descriptors],
        )
        self.assertTrue(all(item.mutation_level.name in {"L0", "L1"} for item in descriptors))
        self.assertTrue(all(item.enabled for item in descriptors))
        self.assertTrue(all(item.disabled_reason == "" for item in descriptors))
        self.assertTrue(all("write" not in item.capability for item in descriptors))
        self.assertTrue(all(item.tool not in {"request", "execute", "proxy", "raw_sql", "shell", "command"} for item in descriptors))

    def test_yunxiao_skill_and_runtime_route_are_native_mcp(self) -> None:
        matrix = json.loads(
            (HARNESS_ROOT / "config/role_capability_skill_matrix.json").read_text(
                encoding="utf-8"
            )
        )
        skill = next(
            item for item in matrix["skills"]
            if item["name"] == "yunxiao-workitem-read"
        )
        route = next(
            item for item in matrix["capability_routes"]
            if item["capability"] == "workitem.read" and item["provider"] == "yunxiao"
        )

        self.assertEqual("mcp_skill", skill["kind"])
        self.assertEqual("yunxiao", skill["mcp_server"])
        self.assertEqual("yunxiao", route["mcp_server"])
        self.assertEqual("mcp", route["execution_kind"])
        self.assertEqual("mcp_required", route["required_boundary"])
        self.assertEqual("native", route["migration_state"])

    def test_mcp_config_has_no_secret_shaped_keys_and_all_hashes_validate(self) -> None:
        paths = [MANIFEST]
        paths.extend(sorted((HARNESS_ROOT / "config/schemas/mcp_tools").glob("*.json")))
        paths.append(HARNESS_ROOT / "config/schemas/mcp_result_envelope.v1.json")
        for path in paths:
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual([], [key for key in _walk_keys(payload) if SENSITIVE_KEY.search(key)])

        registry = McpCapabilityRegistry.from_file(MANIFEST, harness_root=HARNESS_ROOT)
        self.assertEqual(3, len(registry.list_capabilities()))

    def test_external_io_policy_passes_and_keeps_compatibility_debt_visible(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "tools/external_io_inventory.py",
                "validate",
                "--policy",
                "config/external_io_boundaries.v1.json",
                "--matrix",
                "config/role_capability_skill_matrix.json",
                "--format",
                "summary",
            ],
            cwd=HARNESS_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("status=passed", completed.stdout)
        match = re.search(r"compatibility_debt=(\d+)", completed.stdout)
        self.assertIsNotNone(match)
        self.assertGreater(int(match.group(1)), 0)
        for field in ("unclassified", "source_drift", "forbidden", "skill_contract_errors"):
            self.assertIn(f"{field}=0", completed.stdout)

    def test_mcp_persistence_does_not_migrate_the_main_database(self) -> None:
        database_source = (HARNESS_ROOT / "app/database.py").read_text(encoding="utf-8")
        self.assertNotIn("mcp_gateway_audit", database_source)
        self.assertNotIn("mcp_evidence", database_source)
        self.assertIn("HARNESS_SCHEMA_VERSION = 73", database_source)

        matrix = json.loads(
            (HARNESS_ROOT / "config/role_capability_skill_matrix.json").read_text(
                encoding="utf-8"
            )
        )
        serialized = json.dumps(matrix, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("mcp_capability_runtime", serialized)
        self.assertIn('"migration_state": "compatibility"', serialized)

    def test_disabled_transport_is_the_only_concrete_phase_1a_transport(self) -> None:
        transports = {
            name
            for name, value in inspect.getmembers(mcp_transport, inspect.isclass)
            if name.endswith("Transport") and not getattr(value, "_is_protocol", False)
        }
        self.assertEqual({"DisabledMcpTransport"}, transports)
        with self.assertRaises(McpTransportUnavailable):
            DisabledMcpTransport().call()

    def test_gateway_has_one_call_site_and_no_automatic_retry_loop(self) -> None:
        source = (HARNESS_ROOT / "app/mcp_gateway.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        execute = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "execute"
        )
        transport_calls = [
            node
            for node in ast.walk(execute)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "call"
        ]
        retry_loops = [
            node for node in ast.walk(execute) if isinstance(node, (ast.For, ast.While))
        ]

        self.assertEqual(1, len(transport_calls))
        self.assertEqual([], retry_loops)


if __name__ == "__main__":
    unittest.main()
