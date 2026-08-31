from __future__ import annotations

import json
import unittest
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path("/Users/lym/plugins")


class McpPhase1dPrimaryActivationTests(unittest.TestCase):
    def test_read_capabilities_are_enabled_native_mcp_routes(self) -> None:
        manifest = json.loads(
            (HARNESS_ROOT / "config" / "mcp_capabilities.json").read_text(encoding="utf-8")
        )
        descriptors = {
            (item["capability"], item["provider"]): item
            for item in manifest["capabilities"]
        }
        for identity in (
            ("workitem.read", "yunxiao"),
            ("gitlab.read", "gitlab"),
            ("database.inspect", "postgresql"),
        ):
            with self.subTest(identity=identity):
                self.assertTrue(descriptors[identity]["enabled"])
                self.assertEqual("", descriptors[identity]["disabled_reason"])

        matrix = json.loads(
            (HARNESS_ROOT / "config" / "role_capability_skill_matrix.json").read_text(
                encoding="utf-8"
            )
        )
        routes = {item["capability"]: item for item in matrix["capability_routes"]}
        for capability in ("workitem.read", "gitlab.read", "database.inspect"):
            with self.subTest(capability=capability):
                self.assertEqual("mcp", routes[capability]["execution_kind"])
                self.assertEqual("native", routes[capability]["migration_state"])
                self.assertEqual("mcp_required", routes[capability]["required_boundary"])

    def test_every_enabled_server_has_a_frozen_executable_entrypoint(self) -> None:
        expected = {
            "yunxiao": PLUGIN_ROOT / "yunxiao" / ".mcp.json",
            "gitlab": PLUGIN_ROOT / "his-engineering" / ".mcp.json",
            "postgresql": PLUGIN_ROOT / "his-engineering" / ".mcp.json",
        }
        for server, path in expected.items():
            with self.subTest(server=server):
                self.assertTrue(path.is_file(), f"missing frozen MCP config: {path}")
                if not path.is_file():
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                item = payload["mcpServers"][server]
                self.assertEqual("python3", item["command"])
                self.assertEqual(".", item["cwd"])
                self.assertEqual(1, len(item["args"]))
                entrypoint = item["args"][0].removeprefix("./")
                self.assertTrue((path.parent / entrypoint).is_file())

    def test_default_manager_registry_is_mcp_primary_and_rollback_is_explicit(self) -> None:
        from app.providers.registry import build_manager_adapter_registry

        primary = build_manager_adapter_registry()
        for provider in ("yunxiao", "gitlab", "database"):
            with self.subTest(provider=provider):
                self.assertEqual("McpReadonlyProviderAdapter", type(primary[provider]).__name__)

        rollback = build_manager_adapter_registry(compatibility_mode="provider_rollback")
        self.assertEqual("YunxiaoProviderAdapter", type(rollback["yunxiao"]).__name__)
        self.assertEqual("GitLabProviderAdapter", type(rollback["gitlab"]).__name__)
        self.assertEqual(
            "DatabaseReadonlyProviderAdapter", type(rollback["database"]).__name__
        )
        with self.assertRaises(ValueError):
            build_manager_adapter_registry(compatibility_mode="fallback")


if __name__ == "__main__":
    unittest.main()
