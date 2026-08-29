from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config_compat import resolve_legacy_compatible_config


class ConfigCompatibilityTests(unittest.TestCase):
    def test_current_v033_files_map_to_builtin_layer(self) -> None:
        resolved = resolve_legacy_compatible_config(profile_key="dfhis-local-example")
        payload = resolved.to_dict()
        values = payload["values"]
        self.assertEqual("legacy", values["orchestration"]["mode"])
        self.assertEqual("his_requirement_workflow", values["orchestration"]["team"]["key"])
        self.assertEqual(9, len(values["orchestration"]["legacy_steps"]))
        self.assertEqual("dfhis-default", values["projects"]["active_profile"]["rule_pack_id"])
        self.assertIn("yunxiao", values["providers"]["requirement_sources"])
        self.assertFalse(values["features"]["flags"]["config_resolver_v2"])
        self.assertEqual("off", payload["hard_guards"]["external_writes_default"])
        self.assertEqual("pass", payload["validation"]["status"])

    def test_explicit_project_and_run_layers_override_builtin_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp) / "project.json"
            project_path.write_text(json.dumps({
                "schema_version": "1.0",
                "layer": {"kind": "project_config", "id": "hospital-a"},
                "merge_policies": {},
                "config": {"projects": {"active_profile": {"output_root": "/tmp/project-output"}}},
            }), encoding="utf-8")
            resolved = resolve_legacy_compatible_config(
                profile_key="dfhis-local-example",
                project_config_path=project_path,
                run_overrides={"orchestration": {"mode": "dynamic_plan"}},
            )
        payload = resolved.to_dict()
        self.assertEqual("/tmp/project-output", payload["values"]["projects"]["active_profile"]["output_root"])
        self.assertEqual("dynamic_plan", payload["values"]["orchestration"]["mode"])
        self.assertEqual("project_config", payload["provenance"]["projects.active_profile.output_root"]["layer_kind"])
        self.assertEqual("run_override", payload["provenance"]["orchestration.mode"]["layer_kind"])

    def test_adapter_does_not_load_default_personal_or_remote_configuration(self) -> None:
        resolved = resolve_legacy_compatible_config(profile_key="team-share-example")
        sources = [item["source"] for item in resolved.to_dict()["layers"]]
        self.assertEqual(["legacy:v0.33"], sources)
        self.assertTrue(all(".his-harness" not in source for source in sources))


if __name__ == "__main__":
    unittest.main()
