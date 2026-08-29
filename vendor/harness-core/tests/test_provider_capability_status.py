from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.provider_capability_status import build_provider_capability_status


class ProviderCapabilityStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def provider_profiles(self) -> list[dict[str, object]]:
        return [
            {
                "provider": "yunxiao",
                "profile_key": "company-yunxiao",
                "credential_ref": "aliyun_devops_pat",
                "connection": {"project_key": "DFHIS"},
            },
            self.git_profile(),
            {
                "provider": "gitlab",
                "profile_key": "company-gitlab",
                "credential_ref": "gitlab_token",
                "connection": {"host": "gitlab.company.test"},
            },
            {
                "provider": "database",
                "profile_key": "his-main-db",
                "credential_ref": "his_db_readonly",
                "connection": {},
                "test_connection": {},
            },
            {
                "provider": "knowledge",
                "profile_key": "company-knowledge",
                "credential_ref": "HIS_KNOWLEDGE_HOME",
                "connection": {"home_ref": "HIS_KNOWLEDGE_HOME"},
            },
            {
                "provider": "model",
                "profile_key": "company-model",
                "credential_ref": "model_provider_api_key_ref",
                "connection": {"model": "test-model"},
            },
        ]

    def manifest(self, plugin: str, capabilities: object) -> dict[str, object]:
        return {
            "schema_version": "his-capabilities.v1",
            "plugin": plugin,
            "capabilities": capabilities,
        }

    def write_manifests(self, payloads: dict[str, dict[str, object]]) -> dict[str, str]:
        directory = Path(self.temp_dir.name)
        paths = {}
        for plugin, payload in payloads.items():
            manifest_path = directory / plugin / "capabilities.json"
            manifest_path.parent.mkdir()
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            paths[plugin] = str(manifest_path)
        return paths

    def git_profile(self) -> dict[str, object]:
        return {
            "provider": "git",
            "profile_key": "local-git",
            "credential_ref": "local_git_identity",
            "connection": {"remote": "origin"},
        }

    def write_manifest(self, directory: Path, payload: object) -> str:
        manifest_path = directory / "capabilities.json"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        return str(manifest_path)

    def canonical_manifest(self, capabilities: object) -> dict[str, object]:
        return {
            "schema_version": "his-capabilities.v1",
            "plugin": "his-engineering",
            "capabilities": capabilities,
        }

    def test_supported_provider_profiles_report_declared_read_and_write_boundaries(self) -> None:
        manifests = self.write_manifests({
            "yunxiao": self.manifest("yunxiao", [
                {"name": "workitem.read", "enabled": True},
                {"name": "workitem.write", "enabled": False},
            ]),
            "his-engineering": self.manifest("his-engineering", [
                {"name": "git.inspect", "enabled": True},
                {"name": "git.diff", "enabled": True},
                {"name": "source.read", "enabled": True},
                {"name": "source.search", "enabled": True},
                {"name": "git.history", "enabled": True},
                {"name": "verification.run-local", "enabled": True},
                {"name": "code.review-local", "enabled": True},
                {"name": "git.push", "enabled": False},
                {"name": "gitlab.read", "enabled": True},
                {"name": "gitlab.write", "enabled": False},
                {"name": "database.inspect", "enabled": True},
                {"name": "database.change", "enabled": False},
            ]),
            "his-knowledge": self.manifest("his-knowledge", [
                {"name": "knowledge.retrieve", "enabled": True},
                {"name": "knowledge.answer", "enabled": True},
            ]),
        })

        profiles = self.provider_profiles() + [
            {
                "provider": "github",
                "profile_key": "github-dfhis",
                "credential_ref": "github_access_token",
                "connection": {"owner": "dfhis", "repository": "guahao"},
            }
        ]
        result = build_provider_capability_status(profiles, manifest_paths=manifests)

        by_provider = {item["provider"]: item for item in result["items"]}
        self.assertEqual("enabled", by_provider["yunxiao"]["capabilities"][0]["contract_status"])
        self.assertEqual("disabled", by_provider["yunxiao"]["capabilities"][1]["contract_status"])
        self.assertEqual("blocked", by_provider["database"]["execution_status"])
        self.assertEqual("canonical_provider_contract_unregistered", by_provider["model"]["reason"])
        capability_statuses = {
            capability["name"]: capability
            for item in result["items"]
            for capability in item["capabilities"]
        }
        available_capabilities = {
            "git.diff",
            "source.read",
            "source.search",
            "git.history",
            "verification.run-local",
            "code.review-local",
        }
        self.assertEqual(
            available_capabilities,
            {
                name
                for name, capability in capability_statuses.items()
                if capability["execution_status"] == "available"
            },
        )
        self.assertTrue(all(
            capability_statuses[name]["execution_reason"]
            == "code_evidence_orchestrator_registered"
            for name in available_capabilities
        ))
        self.assertTrue(all(
            capability["execution_status"] == "blocked"
            for name, capability in capability_statuses.items()
            if name not in available_capabilities
        ))
        self.assertEqual("blocked", capability_statuses["git.inspect"]["execution_status"])
        self.assertEqual(
            "git_inspect_os_sandbox_executor_unregistered",
            capability_statuses["git.inspect"]["execution_reason"],
        )
        self.assertEqual("local_readonly", capability_statuses["git.inspect"]["execution_boundary"])
        self.assertEqual(
            "readonly_sql_dynamic_database_access",
            capability_statuses["database.inspect"]["execution_boundary"],
        )
        self.assertEqual("disabled", capability_statuses["git.push"]["contract_status"])
        self.assertEqual("blocked", capability_statuses["git.push"]["execution_status"])
        self.assertEqual(
            "canonical_provider_capability_disabled",
            next(
                capability["execution_reason"]
                for capability in by_provider["git"]["capabilities"]
                if capability["name"] == "git.push"
            ),
        )
        gitlab_actions = {
            action["action"] for action in by_provider["gitlab"]["actions"]
        }
        self.assertTrue({
            "gitlab.repository.file.read",
            "gitlab.commit.read",
            "gitlab.commit.diff.read",
            "gitlab.compare.read",
            "gitlab.merge_request.commits.read",
            "gitlab.merge_request.diffs.read",
            "gitlab.pipeline.jobs.read",
        }.issubset(gitlab_actions))
        self.assertTrue(all(
            action["risk"] == "read"
            and action["required_credential_fields"] == ["access_token"]
            and action["availability_status"] == "blocked"
            for action in by_provider["gitlab"]["actions"]
            if action["action"] in gitlab_actions
            and action["action"].startswith("gitlab.")
            and action["action"].endswith(".read")
        ))

    def test_result_redacts_profile_connection_and_credential_values(self) -> None:
        repository = "https://private.example.test/secret-repository"
        credential = "credential-reference-should-not-render"
        profiles = [self.git_profile()]
        profiles[0]["credential_ref"] = credential
        profiles[0]["connection"] = {"remote": repository}

        result = build_provider_capability_status(profiles)

        rendered = json.dumps(result)
        self.assertNotIn(repository, rendered)
        self.assertNotIn(credential, rendered)
        self.assertEqual("git", result["items"][0]["provider"])
        self.assertEqual("local-git", result["items"][0]["profile_key"])

    def test_profile_configuration_only_describes_actions_and_never_claims_adapter_availability(self) -> None:
        result = build_provider_capability_status([self.git_profile()])

        item = result["items"][0]
        self.assertEqual("configured", item["configuration_status"])
        self.assertEqual("blocked", item["availability_status"])
        self.assertEqual("provider_adapter_not_registered", item["availability_reason"])
        actions = {action["action"]: action for action in item["actions"]}
        readonly = actions["git.readonly_smoke"]
        self.assertEqual("read", readonly["risk"])
        self.assertGreater(readonly["max_timeout_seconds"], 0)
        self.assertGreater(readonly["max_result_bytes"], 0)
        self.assertEqual([], readonly["required_credential_fields"])
        self.assertIsNone(readonly["read_back_verifier"])
        self.assertTrue(all(action["availability_status"] == "blocked" for action in actions.values()))

    def test_git_delivery_capabilities_follow_the_manifest_while_other_writes_stay_forced_off(self) -> None:
        manifests = self.write_manifests({
            "yunxiao": self.manifest("yunxiao", [
                {"name": "workitem.read", "enabled": True},
                {"name": "workitem.write", "enabled": True},
            ]),
            "his-engineering": self.manifest("his-engineering", [
                {"name": "git.inspect", "enabled": True},
                {"name": "git.push", "enabled": True},
                {"name": "gitlab.read", "enabled": True},
                {"name": "gitlab.write", "enabled": True},
                {"name": "github.read", "enabled": True},
                {"name": "github.write", "enabled": True},
                {"name": "database.inspect", "enabled": True},
                {"name": "database.change", "enabled": True},
            ]),
            "his-knowledge": self.manifest("his-knowledge", []),
        })

        profiles = self.provider_profiles() + [
            {
                "provider": "github",
                "profile_key": "github-dfhis",
                "credential_ref": "github_access_token",
                "connection": {"owner": "dfhis", "repository": "guahao"},
            }
        ]
        result = build_provider_capability_status(profiles, manifest_paths=manifests)
        by_provider = {item["provider"]: item for item in result["items"]}

        for provider, capability_name in (
            ("yunxiao", "workitem.write"),
            ("database", "database.change"),
        ):
            capability = next(
                item for item in by_provider[provider]["capabilities"]
                if item["name"] == capability_name
            )
            self.assertEqual("disabled", capability["contract_status"])
            self.assertEqual("blocked", capability["execution_status"])
            self.assertEqual("canonical_provider_capability_disabled", capability["execution_reason"])

        for provider, capability_name in (
            ("git", "git.push"),
            ("gitlab", "gitlab.write"),
            ("github", "github.write"),
        ):
            capability = next(
                item for item in by_provider[provider]["capabilities"]
                if item["name"] == capability_name
            )
            self.assertEqual("enabled", capability["contract_status"])
            self.assertEqual("blocked", capability["execution_status"])
            self.assertEqual(
                "canonical_provider_executor_unregistered",
                capability["execution_reason"],
            )

    def test_malformed_or_missing_plugin_manifest_fails_closed_without_path_disclosure(self) -> None:
        malformed_path = self.write_manifests({
            "yunxiao": self.manifest("his-engineering", [
                {"name": "workitem.read", "enabled": True},
            ]),
        })["yunxiao"]
        missing_path = str(Path(self.temp_dir.name) / "his-knowledge" / "capabilities.json")

        result = build_provider_capability_status(
            self.provider_profiles(),
            manifest_paths={"yunxiao": malformed_path, "his-knowledge": missing_path},
        )

        by_provider = {item["provider"]: item for item in result["items"]}
        self.assertEqual("malformed", by_provider["yunxiao"]["capabilities"][0]["contract_status"])
        self.assertEqual("unavailable", by_provider["knowledge"]["capabilities"][0]["contract_status"])
        rendered = json.dumps(result)
        self.assertNotIn(malformed_path, rendered)
        self.assertNotIn(missing_path, rendered)

    def test_git_profile_maps_to_canonical_git_inspect_without_running_git(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_provider_capability_status(
                [self.git_profile()],
                manifest_path=self.write_manifest(
                    Path(temp_dir),
                    self.canonical_manifest([{"name": "git.inspect", "enabled": True}]),
                ),
            )

        item = result["items"][0]
        self.assertEqual("his-engineering", item["provider_plugin"])
        self.assertEqual("his-git-local", item["skill"])
        self.assertEqual("git.inspect", item["inspect_capability"])
        self.assertEqual("enabled", item["status"])
        self.assertEqual("blocked", item["execution_status"])
        self.assertEqual("git_inspect_os_sandbox_executor_unregistered", item["execution_reason"])
        self.assertFalse(result["credentials_read"])
        self.assertFalse(result["external_calls"])
        self.assertFalse(result["write_performed"])

    def test_missing_or_malformed_manifest_fails_closed_without_path_disclosure(self) -> None:
        missing_path = "/private/tmp/missing.json"
        result = build_provider_capability_status([self.git_profile()], manifest_path=missing_path)
        item = result["items"][0]
        self.assertEqual("blocked", item["status"])
        self.assertEqual("canonical_provider_manifest_unavailable", item["reason"])
        self.assertNotIn(missing_path, json.dumps(result))

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "capabilities.json"
            manifest_path.write_text("[]", encoding="utf-8")
            malformed = build_provider_capability_status([self.git_profile()], manifest_path=str(manifest_path))

        malformed_item = malformed["items"][0]
        self.assertEqual("blocked", malformed_item["status"])
        self.assertEqual("canonical_provider_manifest_malformed", malformed_item["reason"])
        self.assertNotIn(str(manifest_path), json.dumps(malformed))

    def test_disabled_and_missing_capabilities_are_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            disabled = build_provider_capability_status(
                [self.git_profile()],
                manifest_path=self.write_manifest(
                    directory,
                    self.canonical_manifest([{"name": "git.inspect", "enabled": False}]),
                ),
            )
            missing = build_provider_capability_status(
                [self.git_profile()],
                manifest_path=self.write_manifest(
                    directory,
                    self.canonical_manifest([{"name": "git.push", "enabled": False}]),
                ),
            )

        self.assertEqual("canonical_provider_capability_disabled", disabled["items"][0]["reason"])
        self.assertEqual("canonical_provider_capability_missing", missing["items"][0]["reason"])

    def test_invalid_canonical_manifest_shape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_provider_capability_status(
                [self.git_profile()],
                manifest_path=self.write_manifest(
                    Path(temp_dir),
                    self.canonical_manifest(
                        [
                            {"name": "git.inspect", "enabled": True},
                            {"name": "git.inspect", "enabled": False},
                        ]
                    ),
                ),
            )

        self.assertEqual("blocked", result["items"][0]["status"])
        self.assertEqual("canonical_provider_manifest_malformed", result["items"][0]["reason"])

    def test_readme_describes_canonical_git_provider_and_no_direct_manager_execution(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

        self.assertIn("his-engineering", text)
        self.assertIn("his-git-local", text)
        self.assertIn("Git Provider", text)
        self.assertIn("静态发现", text)
        self.assertIn("不直接运行 Git", text)
        self.assertIn("只显示状态和记录 handoff", text)
        self.assertIn("OS sandbox executor", text)
        self.assertIn("未登记时执行 blocked", text)
        self.assertIn("git.push", text)
        self.assertIn("保持 disabled", text)
        self.assertNotIn("四个正式插件尚未安装", text)

    def test_readme_describes_static_multi_provider_manager_contract(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

        for value in (
            "yunxiao",
            "GitLab",
            "数据库",
            "his-knowledge",
            "静态 capability contract",
            "不读取凭证",
            "不连接外部系统",
            "不执行 Provider 代码",
            "workitem.write",
            "git.push",
            "gitlab.write",
            "database.change",
            "显式授权",
            "真实连接测试",
        ):
            self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
