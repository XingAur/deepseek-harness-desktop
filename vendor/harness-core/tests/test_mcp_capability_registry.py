from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.mcp_capability_registry import (
    McpCapabilityManifestError,
    McpCapabilityNotFound,
    McpCapabilityRegistry,
)


class McpCapabilityRegistryTests(unittest.TestCase):
    def _schema(self, *, marker: str = "fixture") -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {
                "id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": marker,
                }
            },
        }

    def _write_fixture(
        self,
        root: Path,
        *,
        descriptors: list[dict[str, object]] | None = None,
    ) -> tuple[Path, list[dict[str, object]]]:
        schema_root = root / "config/schemas"
        schema_root.mkdir(parents=True)
        input_path = schema_root / "input.json"
        result_path = schema_root / "result.json"
        input_path.write_text(json.dumps(self._schema(marker="input")), encoding="utf-8")
        result_path.write_text(json.dumps(self._schema(marker="result")), encoding="utf-8")
        descriptor = {
            "capability": "workitem.read",
            "provider": "yunxiao",
            "server": "yunxiao",
            "tool": "workitem_get",
            "contract_version": "workitem-read.v1",
            "mutation_level": "L1",
            "required_scopes": ["workitem:read"],
            "timeout_seconds": 30,
            "max_result_bytes": 262144,
            "input_schema_path": "config/schemas/input.json",
            "input_schema_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "result_schema_path": "config/schemas/result.json",
            "result_schema_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            "enabled": False,
            "disabled_reason": "phase_1b_transport_not_configured",
        }
        items = descriptors if descriptors is not None else [descriptor]
        manifest = root / "config/mcp_capabilities.json"
        manifest.write_text(
            json.dumps(
                {"schema_version": "his-mcp-capabilities.v1", "capabilities": items}
            ),
            encoding="utf-8",
        )
        return manifest, items

    def _load(self, root: Path, descriptors: list[dict[str, object]] | None = None):
        manifest, _ = self._write_fixture(root, descriptors=descriptors)
        return McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_exact_manifest_fields_parse_and_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = self._load(root)
            descriptor = registry.resolve("workitem.read", "yunxiao")

        self.assertEqual("workitem_get", descriptor.tool)
        self.assertEqual("L1", descriptor.mutation_level.name)
        self.assertFalse(descriptor.enabled)

    def test_unknown_manifest_or_descriptor_fields_are_rejected(self) -> None:
        for location in ("manifest", "descriptor"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manifest, items = self._write_fixture(root)
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                target = payload if location == "manifest" else payload["capabilities"][0]
                target["unexpected"] = True
                manifest.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(McpCapabilityManifestError):
                    McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_duplicate_capability_provider_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, items = self._write_fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["capabilities"].append(dict(payload["capabilities"][0]))
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(McpCapabilityManifestError):
                McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_schema_path_traversal_and_symlink_escape_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "root"
            root.mkdir()
            manifest, _ = self._write_fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["capabilities"][0]["input_schema_path"] = "../outside.json"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(McpCapabilityManifestError):
                McpCapabilityRegistry.from_file(manifest, harness_root=root)

            outside = root.parent / "outside.json"
            outside.write_text(json.dumps(self._schema()), encoding="utf-8")
            link = root / "config/schemas/link.json"
            link.symlink_to(outside)
            payload["capabilities"][0]["input_schema_path"] = "config/schemas/link.json"
            payload["capabilities"][0]["input_schema_sha256"] = hashlib.sha256(
                outside.read_bytes()
            ).hexdigest()
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(McpCapabilityManifestError):
                McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_schema_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, _ = self._write_fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["capabilities"][0]["input_schema_sha256"] = "0" * 64
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(McpCapabilityManifestError):
                McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_loaded_schemas_are_deeply_immutable_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, _ = self._write_fixture(root)
            registry = McpCapabilityRegistry.from_file(manifest, harness_root=root)
            descriptor = registry.resolve("workitem.read", "yunxiao")
            schema_path = root / "config/schemas/input.json"
            schema_path.write_text(json.dumps(self._schema(marker="changed")), encoding="utf-8")

            self.assertEqual(
                "input",
                descriptor.input_schema["properties"]["id"]["description"],
            )
            with self.assertRaises(TypeError):
                descriptor.input_schema["properties"]["id"]["maxLength"] = 999

    def test_wildcard_and_generic_server_tools_are_rejected(self) -> None:
        cases = (
            ("server", "yunxiao*"),
            ("tool", "workitem_*"),
            ("tool", "request"),
            ("tool", "execute"),
            ("tool", "proxy"),
            ("tool", "raw_sql"),
            ("tool", "shell"),
            ("tool", "command"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manifest, _ = self._write_fixture(root)
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                payload["capabilities"][0][field] = value
                manifest.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(McpCapabilityManifestError):
                    McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_phase_1a_rejects_l2_and_l3_mutation(self) -> None:
        for level in ("L2", "L3"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manifest, _ = self._write_fixture(root)
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                payload["capabilities"][0]["mutation_level"] = level
                manifest.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(McpCapabilityManifestError):
                    McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_enabled_and_disabled_reason_contract_is_exact(self) -> None:
        cases = ((True, "not-empty"), (False, ""))
        for enabled, reason in cases:
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manifest, _ = self._write_fixture(root)
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                payload["capabilities"][0].update(
                    enabled=enabled,
                    disabled_reason=reason,
                )
                manifest.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(McpCapabilityManifestError):
                    McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_timeout_and_result_size_limits_are_enforced(self) -> None:
        cases = (
            ("timeout_seconds", 0),
            ("timeout_seconds", 61),
            ("max_result_bytes", 1023),
            ("max_result_bytes", 1048577),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manifest, _ = self._write_fixture(root)
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                payload["capabilities"][0][field] = value
                manifest.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(McpCapabilityManifestError):
                    McpCapabilityRegistry.from_file(manifest, harness_root=root)

    def test_list_is_deterministic_and_missing_lookup_is_typed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, _ = self._write_fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            second = dict(payload["capabilities"][0])
            second.update(capability="database.inspect", provider="postgresql", server="postgresql", tool="readonly_inspect")
            payload["capabilities"] = [payload["capabilities"][0], second]
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            registry = McpCapabilityRegistry.from_file(manifest, harness_root=root)

            self.assertEqual(
                [("database.inspect", "postgresql"), ("workitem.read", "yunxiao")],
                [(item.capability, item.provider) for item in registry.list_capabilities()],
            )
            with self.assertRaises(McpCapabilityNotFound):
                registry.resolve("missing.read", "missing")


if __name__ == "__main__":
    unittest.main()
