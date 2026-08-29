from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.plugin_inventory import (
    ENABLED_HIGH_RISK_ALLOWLIST,
    PluginInventoryError,
    audit_plugin_layout_drift,
    load_plugin_inventory,
    parse_plugin_inventory,
    resolve_plugin_source_root,
    verify_plugin_inventory,
    validate_high_risk_allowlist,
)
from app.capability_registry import CapabilityRegistry
from tools.capability_check import CliError, load_runtime_config


HARNESS_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = HARNESS_ROOT.parent
PLUGIN_NAMES = (
    "his-harness-core",
    "yunxiao",
    "his-engineering",
    "his-knowledge",
)
FORMAL_PLUGIN_ROOTS = tuple(f"/Users/lym/plugins/{name}" for name in PLUGIN_NAMES)
PLUGIN_SOURCE_ROOT = resolve_plugin_source_root(
    REPOSITORY_ROOT,
    Path(FORMAL_PLUGIN_ROOTS[0]).parent,
)


class PluginInventoryTests(unittest.TestCase):

    def test_requirement_governance_skill_is_frozen_with_the_capability(self) -> None:
        inventory = load_plugin_inventory(HARNESS_ROOT / "config" / "plugin_inventory.json")
        core = next(item for item in inventory.plugins if item.name == "his-harness-core")
        sources = dict(core.sources_sha256)

        self.assertIn("skills/his-requirement-governance/SKILL.md", sources)
        self.assertIn("skills/his-requirement-governance/agents/openai.yaml", sources)

    def test_harness_continuation_and_delivery_audit_sources_are_frozen(self) -> None:
        inventory = load_plugin_inventory(HARNESS_ROOT / "config" / "plugin_inventory.json")
        core = next(item for item in inventory.plugins if item.name == "his-harness-core")
        sources = dict(core.sources_sha256)
        for source in (
            "skills/his-harness/SKILL.md",
            "skills/his-harness/agents/openai.yaml",
            "skills/harness-history/SKILL.md",
            "skills/harness-history/references/history-contract.md",
            "skills/harness-history/scripts/history_manager.py",
        ):
            self.assertIn(source, sources)

    def test_only_three_reviewed_high_risk_capabilities_can_be_enabled(self) -> None:
        self.assertEqual(
            {"git.push", "gitlab.write", "github.write"},
            set(ENABLED_HIGH_RISK_ALLOWLIST),
        )
        validate_high_risk_allowlist(
            [
                {"name": "git.push", "mutation_level": "L4", "enabled": True},
                {"name": "gitlab.write", "mutation_level": "L4", "enabled": True},
                {"name": "github.write", "mutation_level": "L4", "enabled": True},
                {"name": "database.change", "mutation_level": "L5", "enabled": False},
            ]
        )
        with self.assertRaisesRegex(PluginInventoryError, "高风险 capability"):
            validate_high_risk_allowlist(
                [{"name": "workitem.write", "mutation_level": "L4", "enabled": True}]
            )

    def test_layout_drift_audit_reports_changed_frozen_source_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "active" / "demo-plugin"
            candidate = root / "candidate" / "demo-plugin"
            for plugin_root in (active, candidate):
                (plugin_root / ".codex-plugin").mkdir(parents=True)
                (plugin_root / ".codex-plugin" / "plugin.json").write_text(
                    '{"name":"demo-plugin","version":"1.0.0"}',
                    encoding="utf-8",
                )
                (plugin_root / "capabilities.json").write_text(
                    '{"plugin":"demo-plugin","plugin_version":"1.0.0","capabilities":[{"name":"demo.capability"}]}',
                    encoding="utf-8",
                )
                (plugin_root / "scripts").mkdir()
                (plugin_root / "scripts" / "demo.py").write_text(
                    "print('safe')\n", encoding="utf-8"
                )
            inventory_path = root / "inventory.json"
            source_hashes = {
                relative: hashlib.sha256((active / relative).read_bytes()).hexdigest()
                for relative in (
                    ".codex-plugin/plugin.json",
                    "capabilities.json",
                    "scripts/demo.py",
                )
            }
            inventory_path.write_text(
                json.dumps(
                    {
                        "schema_version": "his-plugin-inventory.v1",
                        "plugins": [
                            {
                                "name": "demo-plugin",
                                "version": "1.0.0",
                                "capabilities_sha256": source_hashes["capabilities.json"],
                                "capabilities": ["demo.capability"],
                                "sources_sha256": source_hashes,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (candidate / "scripts" / "demo.py").write_text(
                "print('changed')\n", encoding="utf-8"
            )

            report = audit_plugin_layout_drift(
                inventory_path=inventory_path,
                active_roots=[active.resolve()],
                candidate_roots=[candidate.resolve()],
            )

        self.assertEqual("drift_detected", report["status"])
        self.assertEqual(
            ["scripts/demo.py"], report["plugins"][0]["changed_sources"]
        )
        self.assertFalse(report["mutation_performed"])
    def test_default_runtime_config_is_enforce_and_uses_formal_plugin_roots(self) -> None:
        config = load_runtime_config(str(HARNESS_ROOT / "config" / "capabilities.json"))

        self.assertEqual("enforce", config.routing_mode)
        self.assertEqual(FORMAL_PLUGIN_ROOTS, config.plugin_roots)
        self.assertFalse(config.external_writes_default)
        self.assertEqual(60, config.default_timeout_seconds)
        self.assertEqual(
            "/Users/lym/WorkCode/ai/his-knowledge",
            config.knowledge_home,
        )

    def test_runtime_config_requires_a_supported_routing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "capabilities.json"
            payload = {
                "schema_version": "his-capability-runtime-config.v1",
                "routing_mode": "invalid",
                "plugin_roots": ["/Users/lym/plugins/yunxiao"],
                "external_writes_default": False,
                "default_timeout_seconds": 60,
            }
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(CliError, "routing_mode"):
                load_runtime_config(str(config_path))

            payload.pop("routing_mode")
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(CliError, "routing_mode"):
                load_runtime_config(str(config_path))

    def test_frozen_inventory_matches_all_plugin_manifests(self) -> None:
        inventory_path = HARNESS_ROOT / "config" / "plugin_inventory.json"
        raw_inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory = load_plugin_inventory(inventory_path)
        self.assertEqual(
            {"schema_version", "plugins"},
            set(raw_inventory),
        )
        self.assertEqual("his-plugin-inventory.v1", inventory.schema_version)
        self.assertEqual(list(PLUGIN_NAMES), [item.name for item in inventory.plugins])

        all_capability_names: list[str] = []
        for raw_item, item in zip(raw_inventory["plugins"], inventory.plugins):
            self.assertEqual(
                {
                    "name",
                    "version",
                    "capabilities_sha256",
                    "capabilities",
                    "sources_sha256",
                },
                set(raw_item),
            )
            plugin_root = PLUGIN_SOURCE_ROOT / item.name
            manifest_path = plugin_root / "capabilities.json"
            plugin_manifest = json.loads(
                (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
            )
            capability_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(plugin_manifest["version"], item.version)
            self.assertEqual(capability_manifest["plugin_version"], item.version)
            self.assertEqual(
                hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                item.capabilities_sha256,
            )
            self.assertEqual(
                [capability["name"] for capability in capability_manifest["capabilities"]],
                list(item.capabilities),
            )
            self.assertIn("capabilities.json", dict(item.sources_sha256))
            for relative_path, digest in item.sources_sha256:
                self.assertEqual(
                    hashlib.sha256((plugin_root / relative_path).read_bytes()).hexdigest(),
                    digest,
                )
            all_capability_names.extend(item.capabilities)

            enabled_high_risk = {
                capability["name"]
                for capability in capability_manifest["capabilities"]
                if capability["mutation_level"] in {"L4", "L5"}
                and capability["enabled"]
            }
            self.assertTrue(
                enabled_high_risk.issubset({"git.push", "gitlab.write", "github.write"})
            )
            for capability in capability_manifest["capabilities"]:
                if (
                    capability["mutation_level"] in {"L4", "L5"}
                    and capability["name"] not in {"git.push", "gitlab.write", "github.write"}
                ):
                    self.assertFalse(capability["enabled"])
                    self.assertTrue(capability["disabled_reason"].strip())

        self.assertEqual(len(all_capability_names), len(set(all_capability_names)))
        registry = CapabilityRegistry.from_plugin_roots(
            [PLUGIN_SOURCE_ROOT / name for name in PLUGIN_NAMES]
        )
        verified = verify_plugin_inventory(
            inventory_path,
            [PLUGIN_SOURCE_ROOT / name for name in PLUGIN_NAMES],
            registry=registry,
        )
        self.assertEqual(set(PLUGIN_NAMES), set(verified))
        self.assertEqual(
            {"git.push", "gitlab.write", "github.write"},
            {
                descriptor.name
                for descriptor in registry.descriptors
                if descriptor.mutation_level.name in {"L4", "L5"}
                and descriptor.enabled
            },
        )

    def test_plugin_source_root_supports_staged_and_formal_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository_root = root / "work" / "ai"
            formal_root = root / "plugins"
            repository_root.mkdir(parents=True)
            formal_root.mkdir()

            self.assertEqual(
                formal_root,
                resolve_plugin_source_root(repository_root, formal_root),
            )

            staged_root = repository_root / "plugins"
            staged_root.mkdir()
            self.assertEqual(
                staged_root,
                resolve_plugin_source_root(repository_root, formal_root),
            )

    def test_inventory_rejects_unknown_and_sensitive_shaped_fields(self) -> None:
        valid = {
            "schema_version": "his-plugin-inventory.v1",
            "plugins": [
                {
                    "name": "fixture",
                    "version": "1.0.0",
                    "capabilities_sha256": "a" * 64,
                    "capabilities": ["fixture.read"],
                    "sources_sha256": {
                        "capabilities.json": "b" * 64,
                    },
                }
            ],
        }
        for target, key in (
            ("root", "unknown"),
            ("root", "api_key"),
            ("plugin", "unknown"),
            ("plugin", "secret"),
            ("plugin", "credential"),
        ):
            with self.subTest(target=target, key=key):
                payload = json.loads(json.dumps(valid))
                destination = payload if target == "root" else payload["plugins"][0]
                destination[key] = "SENTINEL_SECRET_VALUE"
                with self.assertRaises(PluginInventoryError):
                    parse_plugin_inventory(payload)

    def test_inventory_rejects_invalid_sha_and_capability_strings(self) -> None:
        valid = {
            "schema_version": "his-plugin-inventory.v1",
            "plugins": [
                {
                    "name": "fixture",
                    "version": "1.0.0",
                    "capabilities_sha256": "a" * 64,
                    "capabilities": ["fixture.read"],
                    "sources_sha256": {
                        "capabilities.json": "b" * 64,
                    },
                }
            ],
        }
        for sha in ("a" * 63, "A" * 64, "g" * 64):
            with self.subTest(sha=sha):
                payload = json.loads(json.dumps(valid))
                payload["plugins"][0]["capabilities_sha256"] = sha
                with self.assertRaises(PluginInventoryError):
                    parse_plugin_inventory(payload)

        for sources in (
            {},
            {"../escape.py": "b" * 64},
            {"/absolute.py": "b" * 64},
            {"entrypoint.py": "B" * 64},
            {"entrypoint.py": "b" * 63},
        ):
            with self.subTest(sources=sources):
                payload = json.loads(json.dumps(valid))
                payload["plugins"][0]["sources_sha256"] = sources
                with self.assertRaises(PluginInventoryError):
                    parse_plugin_inventory(payload)
        for capabilities in (
            [""],
            ["fixture read"],
            ["fixture.read", "fixture.read"],
            [1],
        ):
            with self.subTest(capabilities=capabilities):
                payload = json.loads(json.dumps(valid))
                payload["plugins"][0]["capabilities"] = capabilities
                with self.assertRaises(PluginInventoryError):
                    parse_plugin_inventory(payload)

    def test_inventory_and_default_config_contain_no_secret_material(self) -> None:
        combined = (
            (HARNESS_ROOT / "config" / "capabilities.json").read_text(encoding="utf-8")
            + (HARNESS_ROOT / "config" / "plugin_inventory.json").read_text(encoding="utf-8")
        ).lower()
        for forbidden in ("token", "password", "dsn", "organization_id", "pat"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
