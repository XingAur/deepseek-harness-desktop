from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityRequest,
    MutationLevel,
)
from app.capability_registry import CapabilityRegistry
from app.capability_runtime import CapabilityRuntime
from app.provider_capability_status import build_provider_capability_status


HARNESS_ROOT = Path(__file__).resolve().parents[1]


def _resolve_plugin_root(harness_root: Path) -> Path:
    development_root = harness_root.parent / "plugins" / "his-engineering"
    if (
        (development_root / "capabilities.json").is_file()
        and (development_root / "install_manifest.json").is_file()
    ):
        return development_root
    payload = json.loads(
        (harness_root / "config" / "capabilities.json").read_text(encoding="utf-8")
    )
    matches = [
        Path(value)
        for value in payload["plugin_roots"]
        if Path(value).name == "his-engineering"
    ]
    if len(matches) != 1:
        raise ValueError("his-engineering plugin root is not uniquely configured")
    return matches[0]


PLUGIN_ROOT = _resolve_plugin_root(HARNESS_ROOT)
CAPABILITIES = (
    ("git.diff", "repository:diff:read"),
    ("source.read", "repository:source:read"),
    ("source.search", "repository:search:read"),
    ("git.history", "repository:history:read"),
    ("verification.run-local", "repository:verification:run-local"),
    ("code.review-local", "repository:review:local"),
)


class CodeEvidenceCapabilityRegistrationTests(unittest.TestCase):
    def test_installed_layout_resolves_the_configured_formal_plugin_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            harness_root = root / "WorkCode" / "ai" / "Harness"
            plugin_root = root / "plugins" / "his-engineering"
            (harness_root / "config").mkdir(parents=True)
            plugin_root.mkdir(parents=True)
            (plugin_root / "capabilities.json").write_text("{}\n", encoding="utf-8")
            (plugin_root / "install_manifest.json").write_text("{}\n", encoding="utf-8")
            (harness_root / "config" / "capabilities.json").write_text(
                json.dumps({"plugin_roots": [str(plugin_root)]}),
                encoding="utf-8",
            )

            self.assertEqual(plugin_root, _resolve_plugin_root(harness_root))

    def test_plugin_install_manifest_covers_the_frozen_code_evidence_contract(self) -> None:
        payload = json.loads((PLUGIN_ROOT / "install_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual("harness-manager-install.v1", payload["schema_version"])
        self.assertEqual("/Users/lym/plugins/his-engineering", payload["formal_target"])
        self.assertEqual(
            ["capabilities.json", "install_manifest.json", "scripts/git_local.py"],
            payload["files"],
        )

    def test_manifest_registers_all_foundational_read_capabilities(self) -> None:
        payload = json.loads(
            (PLUGIN_ROOT / "capabilities.json").read_text(encoding="utf-8")
        )
        by_name = {item["name"]: item for item in payload["capabilities"]}

        for name, scope in CAPABILITIES:
            with self.subTest(capability=name):
                item = by_name[name]
                self.assertTrue(item["enabled"])
                self.assertEqual("his-engineering", item["provider"])
                self.assertEqual("L0", item["mutation_level"])
                self.assertEqual(
                    "codex_model_access" if name == "code.review-local" else "none",
                    item["credential_class"],
                )
                self.assertEqual([scope], item["scopes"])
                self.assertEqual("scripts/git_local.py", item["entrypoint"])

    def test_manager_status_exposes_registered_code_evidence_capabilities(self) -> None:
        result = build_provider_capability_status(
            [{
                "provider": "git",
                "profile_key": "local-git",
                "credential_ref": "local_git_identity",
                "connection": {"remote": "origin"},
            }],
            manifest_path=str(PLUGIN_ROOT / "capabilities.json"),
        )

        capabilities = {
            item["name"]: item for item in result["items"][0]["capabilities"]
        }
        for name, _scope in CAPABILITIES:
            with self.subTest(capability=name):
                self.assertEqual("enabled", capabilities[name]["contract_status"])
                self.assertEqual("available", capabilities[name]["execution_status"])
                self.assertEqual(
                    "code_evidence_orchestrator_registered",
                    capabilities[name]["execution_reason"],
                )

    def test_direct_plugin_execution_fails_closed_instead_of_running_git_inspect(self) -> None:
        registry = CapabilityRegistry.from_plugin_roots([PLUGIN_ROOT])
        runtime = CapabilityRuntime(registry)
        for name, _scope in CAPABILITIES:
            request = CapabilityRequest(
                request_id=f"request-{name.replace('.', '-')}",
                capability=name,
                provider="his-engineering",
                mode="preview",
                mutation_level=MutationLevel.L0,
                authorization=CapabilityAuthorization(explicit=False, scope=()),
                input={"repository_alias": "repo-a"},
                context={},
            )
            with self.subTest(capability=name):
                result = runtime.execute(request).result
                self.assertEqual("blocked", result.status)
                self.assertEqual("CODE_EVIDENCE_ORCHESTRATOR_REQUIRED", result.summary)
                self.assertEqual(name, result.capability)


if __name__ == "__main__":
    unittest.main()
