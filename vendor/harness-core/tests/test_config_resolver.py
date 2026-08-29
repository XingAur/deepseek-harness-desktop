from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config_resolver import ConfigLayer, load_layer_document, resolve_config


HARD_GUARDS = {
    "no_secret_printing": True,
    "external_writes_default": "off",
    "real_status_transition_requires_confirmation": True,
    "real_commit_push_requires_confirmation": True,
    "destructive_git_forbidden": True,
    "publish_forbidden_by_default": True,
}


class ConfigResolverTests(unittest.TestCase):
    def test_precedence_and_leaf_provenance(self) -> None:
        resolved = resolve_config(
            layers=[
                ConfigLayer("builtin", "builtin_defaults", "builtin", {
                    "git": {"base_branch": "RC", "permissions": {"auto_commit": False}},
                    "features": {"enabled": ["readonly"]},
                    "workspace": {"output_path": "/tmp/harness-output"},
                    "empty_section": {},
                }),
                ConfigLayer("team", "team_package", "/team.json", {
                    "git": {"base_branch": "release"},
                    "empty_section": {"enabled": True},
                }),
                ConfigLayer("project", "project_config", "/project.json", {
                    "git": {"base_branch": "hotfix"},
                }),
                ConfigLayer("personal", "personal_override", "/personal.json", {
                    "models": {"default": "local-model"},
                }),
                ConfigLayer("run", "run_override", "cli", {
                    "models": {"default": "run-model"},
                }),
            ],
            hard_guards=HARD_GUARDS,
        )
        payload = resolved.to_dict()
        self.assertEqual("hotfix", payload["values"]["git"]["base_branch"])
        self.assertEqual("run-model", payload["values"]["models"]["default"])
        self.assertEqual("/tmp/harness-output", payload["values"]["workspace"]["output_path"])
        self.assertEqual("project_config", payload["provenance"]["git.base_branch"]["layer_kind"])
        self.assertEqual("run_override", payload["provenance"]["models.default"]["layer_kind"])
        self.assertNotIn("empty_section", payload["provenance"])
        self.assertEqual("team_package", payload["provenance"]["empty_section.enabled"]["layer_kind"])
        self.assertTrue(resolved.is_valid)

    def test_list_policies_and_locked_value(self) -> None:
        resolved = resolve_config(
            layers=[
                ConfigLayer(
                    "builtin", "builtin_defaults", "builtin",
                    {"features": ["a", "b"], "checks": ["lint"], "mode": "legacy"},
                    {"features": "union", "checks": "append", "mode": "locked"},
                ),
                ConfigLayer(
                    "team", "team_package", "/team.json",
                    {"features": ["b", "c"], "checks": ["unit"], "mode": "dynamic"},
                ),
                ConfigLayer(
                    "project", "project_config", "/project.json",
                    {"features": ["a"], "checks": ["lint"]},
                    {"features": "remove", "checks": "union"},
                ),
            ],
            hard_guards=HARD_GUARDS,
        )
        payload = resolved.to_dict()
        self.assertEqual(["b", "c"], payload["values"]["features"])
        self.assertEqual(["lint", "unit"], payload["values"]["checks"])
        self.assertEqual("legacy", payload["values"]["mode"])
        self.assertEqual("failed", payload["validation"]["status"])
        self.assertIn("locked_path_override", {item["code"] for item in payload["validation"]["issues"]})

    def test_hard_guards_veto_override_and_secret_is_redacted(self) -> None:
        literal = "literal-secret-value"
        resolved = resolve_config(
            layers=[
                ConfigLayer("builtin", "builtin_defaults", "builtin", {}, {}),
                ConfigLayer("run", "run_override", "cli", {
                    "hard_guards": {"external_writes_default": "on"},
                    "credentials": {"openai_api_key": literal},
                }),
            ],
            hard_guards=HARD_GUARDS,
        )
        serialized = json.dumps(resolved.to_dict(), ensure_ascii=False)
        self.assertNotIn(literal, serialized)
        self.assertIn("<redacted-invalid-secret>", serialized)
        self.assertEqual("off", resolved.to_dict()["hard_guards"]["external_writes_default"])
        self.assertEqual("failed", resolved.to_dict()["validation"]["status"])
        self.assertTrue({"hard_guard_override", "literal_secret_forbidden"}.issubset(
            {item["code"] for item in resolved.to_dict()["validation"]["issues"]}
        ))

    def test_explicit_replace_and_merge_policies(self) -> None:
        resolved = resolve_config(
            layers=[
                ConfigLayer(
                    "builtin", "builtin_defaults", "builtin",
                    {"settings": {"left": 1}, "labels": ["old"]},
                ),
                ConfigLayer(
                    "team", "team_package", "/team.json",
                    {
                        "settings": {"right": 2},
                        "labels": ["new"],
                        "new_section": {"checks": ["lint"]},
                    },
                    {
                        "settings": "merge",
                        "labels": "replace",
                        "new_section.checks": "union",
                    },
                ),
                ConfigLayer(
                    "project", "project_config", "/project.json",
                    {"new_section": {"checks": ["unit"]}},
                ),
            ],
            hard_guards=HARD_GUARDS,
        )
        payload = resolved.to_dict()
        self.assertEqual({"left": 1, "right": 2}, payload["values"]["settings"])
        self.assertEqual(["new"], payload["values"]["labels"])
        self.assertEqual(["lint", "unit"], payload["values"]["new_section"]["checks"])
        self.assertEqual("merge", payload["provenance"]["settings.right"]["policy"])

    def test_snapshot_is_immutable_and_hash_is_stable(self) -> None:
        first = resolve_config(
            layers=[ConfigLayer("builtin", "builtin_defaults", "builtin", {
                "a": {"b": 1},
                "empty": {},
            })],
            hard_guards=HARD_GUARDS,
        )
        second = resolve_config(
            layers=[ConfigLayer("builtin", "builtin_defaults", "builtin", {
                "a": {"b": 1},
                "empty": {},
            })],
            hard_guards=HARD_GUARDS,
        )
        with self.assertRaises(TypeError):
            first.values["a"]["b"] = 2
        self.assertIn("empty", first.provenance)
        self.assertEqual(first.content_hash, second.content_hash)

    def test_layer_document_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            path.write_text(json.dumps({
                "schema_version": "1.0",
                "layer": {"kind": "project_config", "id": "his-local"},
                "merge_policies": {"verification.required_checks": "union"},
                "config": {"verification": {"required_checks": ["lint"]}},
            }), encoding="utf-8")
            layer = load_layer_document(path, expected_kind="project_config")
        self.assertEqual("his-local", layer.name)
        self.assertEqual("union", layer.merge_policies["verification.required_checks"])


if __name__ == "__main__":
    unittest.main()
