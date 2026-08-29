from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.capability_contracts import CapabilityAuthorization, CapabilityRequest, MutationLevel
from app.capability_registry import CapabilityRegistry
from app.capability_runtime import CapabilityRuntime
from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT


PLUGIN_ENTRYPOINT = PLUGIN_SOURCE_ROOT / "his-engineering" / "scripts" / "git_local.py"


class GitCapabilityRuntimeTests(unittest.TestCase):
    def test_runtime_uses_a_minimal_git_inspect_fixture_without_later_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "his-engineering"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(PLUGIN_ENTRYPOINT, scripts / "git_local.py")
            (root / "capabilities.json").write_text(json.dumps({
                "schema_version": "his-capabilities.v1",
                "plugin": "his-engineering",
                "plugin_version": "0.1.0",
                "capabilities": [{
                    "name": "git.inspect",
                    "provider": "his-engineering",
                    "contract_version": "git-inspect.v1",
                    "mutation_level": "L0",
                    "credential_class": "none",
                    "entrypoint": "scripts/git_local.py",
                    "enabled": True,
                    "scopes": ["repository:inspect"],
                }],
            }), encoding="utf-8")
            project = root / "plain"
            project.mkdir()
            request = CapabilityRequest(
                request_id="git-runtime-1",
                capability="git.inspect",
                provider="his-engineering",
                mode="preview",
                mutation_level=MutationLevel.L0,
                authorization=CapabilityAuthorization(explicit=False, scope=()),
                input={"project_path": str(project)},
                context={},
            )

            execution = CapabilityRuntime(CapabilityRegistry.from_plugin_roots([root])).execute(request)

        self.assertEqual("blocked", execution.result.status)
        self.assertFalse(execution.result.changed)
        self.assertEqual("unsupported", execution.result.data["classification"])
        self.assertEqual("none", execution.result.audit["provider"]["credential_class"])
        self.assertFalse(execution.result.audit["provider"]["external_write_attempted"])


if __name__ == "__main__":
    unittest.main()
