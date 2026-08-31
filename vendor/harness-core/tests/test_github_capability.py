from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


PLUGIN_SCRIPT = Path("/Users/lym/plugins/his-engineering/scripts/github_read.py")
PLUGIN_WRITE_SCRIPT = Path("/Users/lym/plugins/his-engineering/scripts/github_write.py")
PLUGIN_MANIFEST = Path("/Users/lym/plugins/his-engineering/capabilities.json")
PLUGIN_SKILL = Path("/Users/lym/plugins/his-engineering/skills/his-github/SKILL.md")


def _load_module():
    spec = importlib.util.spec_from_file_location("github_read_capability", PLUGIN_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("github capability module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTransport:
    def get_repository(self, owner, repository):
        self.called = (owner, repository)
        return {"full_name": f"{owner}/{repository}", "token_echo": "github-test-secret"}


class GitHubCapabilityTests(unittest.TestCase):
    def request(self) -> dict[str, object]:
        return {
            "schema_version": "his-capability-request.v1",
            "request_id": "github-read-1",
            "capability": "github.read",
            "provider": "his-engineering",
            "mode": "preview",
            "mutation_level": "L1",
            "authorization": {"explicit": False, "scope": []},
            "input": {"operation": "repository", "owner": "octocat", "repository": "hello-world"},
            "context": {},
        }

    def test_repository_read_is_injected_readonly_and_redacts_sensitive_values(self) -> None:
        module = _load_module()
        transport = FakeTransport()

        result = module.execute_request(
            self.request(),
            config=module.GitHubReadConfiguration(credential_key_name="github_access_token"),
            transport=transport,
            sensitive_values=("github-test-secret",),
        )

        self.assertEqual(("octocat", "hello-world"), transport.called)
        self.assertEqual("success", result["status"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["audit"]["external_write_attempted"])
        self.assertNotIn("github-test-secret", repr(result))

    def test_cli_mode_has_no_implicit_network_or_credential_access(self) -> None:
        module = _load_module()

        result = module.execute_request(self.request())

        self.assertEqual("blocked", result["status"])
        self.assertIn("github_read_configuration_or_transport_unavailable", result["blockers"])

    def test_manifest_and_skill_expose_the_same_plugin_owned_github_boundary(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        capability = next(
            item for item in manifest["capabilities"] if item["name"] == "github.read"
        )

        self.assertEqual("his-engineering", capability["provider"])
        self.assertEqual("L1", capability["mutation_level"])
        self.assertTrue(capability["enabled"])
        self.assertTrue(PLUGIN_SKILL.is_file())
        skill = PLUGIN_SKILL.read_text(encoding="utf-8")
        for phrase in (
            "api.github.com",
            "caller-only ephemeral",
            "confirmed and consumed once",
            "exact verified read-back receipt",
        ):
            self.assertIn(phrase, skill)

    def test_manifest_exposes_enabled_l4_github_write_through_delivery_gate(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        capability = next(
            item for item in manifest["capabilities"] if item["name"] == "github.write"
        )

        self.assertEqual("his-engineering", capability["provider"])
        self.assertEqual("github-write.v1", capability["contract_version"])
        self.assertEqual("L4", capability["mutation_level"])
        self.assertEqual("github_write", capability["credential_class"])
        self.assertEqual("scripts/github_write.py", capability["entrypoint"])
        self.assertEqual(["github:write"], capability["scopes"])
        self.assertTrue(capability["enabled"])
        self.assertTrue(PLUGIN_WRITE_SCRIPT.is_file())


if __name__ == "__main__":
    unittest.main()
