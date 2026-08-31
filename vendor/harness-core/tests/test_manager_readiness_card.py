from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.business_acceptance_repository import BusinessAcceptanceRepository
from app.server import (
    build_manager_business_acceptance_status,
    build_manager_provider_status,
    build_manager_readiness_status,
    render_actions_page,
    render_business_acceptance_page,
    render_home,
    render_learning_candidates_page,
    render_provider_profiles_page,
    render_readiness_card,
    render_operating_console,
)


class ManagerReadinessCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HARNESS_PROVIDER_PROFILE_STORE": str(
                    Path(self.temp_dir.name) / "legacy-profiles.json"
                ),
                "HARNESS_PROVIDER_CONNECTION_TEST_AUDIT": str(
                    Path(self.temp_dir.name) / "connection-tests.jsonl"
                ),
            },
            clear=False,
        )
        self.environment.start()
        database.init_db()

    def tearDown(self) -> None:
        self.environment.stop()
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_home_renders_read_only_five_gap_status_card_without_secrets(self) -> None:
        sentinel = "SENTINEL_MANAGER_CARD_SECRET"
        with mock.patch.dict(os.environ, {"aliyun_devops_pat": sentinel}):
            html = render_home()

        self.assertIn("五缺口状态", html)
        self.assertIn("/api/core-status", html)
        self.assertIn("真实模型 Worker", html)
        self.assertIn("his-model-worker-smoke-readiness.v1", html)
        self.assertIn("single-node-smoke-only", html)
        self.assertIn("his-manager-model-smoke-preflight.v1", html)
        self.assertIn("&quot;runtime_verified&quot;: false", html)
        self.assertIn("readiness-detail-real_model_worker", html)
        self.assertIn("&quot;credentials_read&quot;: false", html)
        self.assertIn("&quot;real_model_dag_enabled&quot;: false", html)
        self.assertIn("自动学习闭环", html)
        self.assertIn("真实业务验收", html)
        self.assertIn("外部写动作", html)
        self.assertIn("知识库与 Obsidian", html)
        self.assertNotIn(sentinel, html)

    def test_home_readiness_keeps_five_verification_levels_distinct(self) -> None:
        html = render_home()

        for label in (
            "代码就绪",
            "配置完成",
            "本地测试",
            "外部验证",
            "业务验收",
        ):
            self.assertIn(label, html)

    def test_readiness_renderer_never_connects_to_default_manager_database(self) -> None:
        with (
            mock.patch("app.server.ManagerProviderRepository", side_effect=AssertionError),
            mock.patch("app.server.BusinessAcceptanceRepository", side_effect=AssertionError),
            mock.patch("app.database.init_db", side_effect=AssertionError) as init_db,
            mock.patch("app.database.connect", side_effect=AssertionError) as connect,
            mock.patch("app.database.sqlite3.connect", side_effect=AssertionError) as sqlite_connect,
        ):
            html = render_readiness_card()

        self.assertIn("五缺口状态", html)
        self.assertIn('id="verification-level-configured" data-state="not_evaluated"', html)
        init_db.assert_not_called()
        connect.assert_not_called()
        sqlite_connect.assert_not_called()

    def test_operating_console_without_injected_readiness_never_constructs_repository(self) -> None:
        with (
            mock.patch("app.server.ManagerProviderRepository", side_effect=AssertionError),
            mock.patch("app.server.BusinessAcceptanceRepository", side_effect=AssertionError),
            mock.patch("app.database.init_db", side_effect=AssertionError) as init_db,
            mock.patch("app.database.connect", side_effect=AssertionError) as connect,
        ):
            html = render_operating_console()

        self.assertIn("运营控制台", html)
        self.assertIn("not_evaluated", html)
        init_db.assert_not_called()
        connect.assert_not_called()

    def test_manager_readiness_uses_explicit_provider_and_current_business_status_only(self) -> None:
        ManagerProviderRepository().upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="configured-model",
            display_name="Configured Model",
            enabled=True,
            connection={"provider_kind": "openai_compatible", "model": "demo"},
        )
        repository = BusinessAcceptanceRepository()
        evidence = repository.create_evidence(
            {
                "evidence_key": "readiness-business-case",
                "environment_alias": "his-test-a",
                "operator_alias": "operator-a",
                "test_data_alias": "case-a",
                "technical_result": "passed",
                "runtime_verified": True,
                "scenarios": [
                    {
                        "name": "charge-save",
                        "status": "passed",
                        "expected": "record-created",
                        "actual": "record-created",
                        "evidence": "sha256:" + "d" * 64,
                    }
                ],
            }
        )
        repository.append_reviewer_decision(
            evidence_id=int(evidence["id"]),
            reviewer_alias="reviewer-a",
            decision="accept",
            reason="runtime-evidence-reviewed",
        )

        manager_status = build_manager_readiness_status(
            provider_status={"profiles": [{"profile_key": "configured-model"}]},
            business_status={"business_valid": True},
        )
        html = render_readiness_card(manager_status=manager_status)

        self.assertIn('id="verification-level-configured"', html)
        self.assertIn('id="verification-level-business_accepted"', html)
        self.assertIn('data-state="configured"', html)
        self.assertIn('data-state="accepted"', html)
        self.assertIn('id="verification-level-locally_tested" data-state="not_recorded"', html)
        self.assertIn('id="verification-level-externally_verified" data-state="not_verified"', html)

    def test_historical_sensitive_manager_rows_return_generic_blocked_views(self) -> None:
        provider = ManagerProviderRepository().upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="historical-provider",
            display_name="Historical Provider",
            enabled=True,
            connection={"host": "db.example.test"},
        )
        host_sentinel = "fixture_user:Fixture9Pass"
        evidence_sentinel = "Bearer " + "R7" * 24
        created_at = database.now_iso()
        with database.connect() as connection:
            connection.execute(
                "update manager_provider_profiles set connection_json = ? where id = ?",
                (json.dumps({"host": host_sentinel}), provider.id),
            )
            connection.execute(
                """
                insert into manager_business_acceptance_evidence(
                    evidence_key, evidence_version, scope_type, scope_key,
                    environment_alias, operator_alias, test_data_alias,
                    technical_result, evidence_hash, evidence_json,
                    business_valid, created_at
                ) values (?, 1, 'local', 'default', 'his-test-a', 'operator-a',
                          'case-a', 'passed', ?, ?, 0, ?)
                """,
                (
                    "historical-sensitive-status",
                    "sha256:" + "c" * 64,
                    json.dumps(
                        {
                            "runtime_verified": True,
                            "scenarios": [{"name": "save", "actual": evidence_sentinel}],
                        }
                    ),
                    created_at,
                ),
            )

        provider_status = build_manager_provider_status()
        business_status = build_manager_business_acceptance_status()
        page = render_business_acceptance_page()
        rendered = json.dumps(
            [provider_status, business_status, page], ensure_ascii=False
        )

        self.assertEqual("blocked", provider_status["status"])
        self.assertEqual([], provider_status["profiles"])
        self.assertEqual("blocked", business_status["status"])
        self.assertFalse(business_status["business_valid"])
        self.assertEqual([], business_status["evidence"])
        self.assertIn("验收证据存储不可安全读取", page)
        self.assertNotIn(host_sentinel, rendered)
        self.assertNotIn(evidence_sentinel, rendered)

    def test_complete_manager_maintenance_pages_are_explicit_and_have_no_generic_executor(self) -> None:
        pages = {
            "actions": render_actions_page(),
            "learning": render_learning_candidates_page(),
            "business": render_business_acceptance_page(),
        }
        rendered = "\n".join(pages.values())

        self.assertIn("Provider 动作计划与审计", pages["actions"])
        self.assertIn("精确参数摘要", pages["actions"])
        self.assertIn("知识候选审核", pages["learning"])
        self.assertIn("业务验收证据", pages["business"])
        self.assertIn('name="_csrf_token"', rendered)
        self.assertNotIn("run command", rendered.lower())
        self.assertNotIn("raw sql", rendered.lower())
        self.assertNotIn('name="sql"', rendered.lower())
        self.assertNotIn('<textarea name="parameters_json"', pages["actions"])

    def test_business_acceptance_page_shows_exact_version_scenarios_and_bound_decision(self) -> None:
        repository = BusinessAcceptanceRepository()
        evidence = repository.create_evidence(
            {
                "evidence_key": "ui-business-case",
                "environment_alias": "his-test-a",
                "operator_alias": "operator-a",
                "test_data_alias": "case-a",
                "technical_result": "passed",
                "runtime_verified": True,
                "scenarios": [
                    {
                        "name": "charge-save",
                        "status": "passed",
                        "expected": "expected-record-created",
                        "actual": "actual-record-created",
                        "evidence": "sha256:" + "e" * 64,
                    }
                ],
            }
        )
        repository.append_reviewer_decision(
            evidence_id=int(evidence["id"]),
            reviewer_alias="reviewer-a",
            decision="reject",
            reason="needs-business-recheck",
        )

        html = render_business_acceptance_page()

        self.assertIn(f"Evidence ID {evidence['id']}", html)
        self.assertIn("ui-business-case v1", html)
        self.assertIn("runtime_verified=true", html)
        self.assertIn("expected-record-created", html)
        self.assertIn("actual-record-created", html)
        self.assertIn("sha256:" + "e" * 64, html)
        self.assertIn("reviewer-a", html)
        self.assertIn("needs-business-recheck", html)
        self.assertIn(
            f'<option value="{evidence["id"]}">ui-business-case v1 / Evidence ID {evidence["id"]}</option>',
            html,
        )
        self.assertNotIn('<input name="evidence_id"', html)

    def test_home_renders_operating_console_sections_without_write_actions(self) -> None:
        html = render_home()

        self.assertIn("运营控制台", html)
        self.assertIn("/providers", html)
        self.assertIn("Provider维护", html)
        self.assertIn("ops-section-connection-profiles", html)
        self.assertIn("连接维护", html)
        self.assertIn("ops-section-capability-state", html)
        self.assertIn("能力状态", html)
        self.assertIn("ops-section-transaction-plans", html)
        self.assertIn("执行计划", html)
        self.assertIn("ops-section-review-confirmation", html)
        self.assertIn("审核确认", html)
        self.assertIn("ops-section-business-evidence", html)
        self.assertIn("业务证据", html)
        self.assertIn("ops-section-knowledge-candidates", html)
        self.assertIn("知识候选", html)
        self.assertIn("真实外部写入默认禁用", html)
        self.assertIn("云效/Git/GitLab 写动作：dry-run -&gt; review -&gt; explicit confirmation -&gt; execute -&gt; audit", html)
        self.assertIn(
            "数据库修改/删除默认绝对禁止：当前 Harness 只生成 SQL 草案且没有写 executor；只有用户明确授权精确对象、操作、条件和影响范围后才可进入独立变更流程。",
            html,
        )
        operating_console = html.split("运营控制台", 1)[1]
        self.assertNotIn("数据库 change", operating_console)
        self.assertNotIn("<form", html.split("运营控制台", 1)[1])

    def test_provider_profiles_page_renders_maintenance_and_test_plan_without_secret(self) -> None:
        sentinel = "SENTINEL_PROVIDER_PROFILE_SECRET"
        with mock.patch.dict(os.environ, {"aliyun_devops_pat": sentinel}):
            html = render_provider_profiles_page()

        self.assertIn("Provider Profile 维护", html)
        self.assertIn("测试连接入口", html)
        self.assertIn("/api/provider-profiles/test-plan", html)
        self.assertIn("真实连接未执行", html)
        self.assertIn("云效", html)
        self.assertIn("GitLab", html)
        self.assertIn("数据库", html)
        self.assertIn("凭证状态", html)
        self.assertIn("新增/更新 Profile", html)
        self.assertIn('name="display_name"', html)
        self.assertIn('name="enabled"', html)
        self.assertIn('name="api_key"', html)
        self.assertIn('type="password"', html)
        self.assertIn('name="_csrf_token"', html)
        self.assertIn('name="organization_id"', html)
        self.assertNotIn('name="credential_ref"', html)
        self.assertNotIn('name="connection_json"', html)
        self.assertIn("执行测试连接记录", html)
        self.assertIn("/api/provider-profiles/test-connection", html)
        self.assertIn("his-provider-connection-test-plan.v1", html)
        self.assertIn("/api/provider-profiles/readonly-smoke-plan", html)
        self.assertIn("/api/provider-profiles/readonly-smoke", html)
        self.assertIn("Canonical Provider 能力", html)
        self.assertIn("Provider</th><th>能力</th><th>Canonical Skill</th>", html)
        self.assertIn("云效", html)
        self.assertIn("his-git-local", html)
        self.assertIn("his-gitlab", html)
        self.assertIn("his-database-read", html)
        self.assertIn("his-knowledge-answer", html)
        self.assertIn("模型", html)
        self.assertIn("canonical_provider_contract_unregistered", html)
        self.assertIn("独立 OS sandbox executor 未登记", html)
        self.assertIn("写能力仅展示为 disabled", html)
        self.assertNotIn('"schema_version": "his-provider-capability-status.v1"', html)
        self.assertNotIn("/Users/lym/plugins/his-engineering/capabilities.json", html)
        self.assertIn("免人工确认", html)
        self.assertIn("OS-sandboxed Git Provider/Skill", html)
        self.assertIn("执行本地只读 smoke", html)
        self.assertNotIn(sentinel, html)

    def test_provider_page_escapes_stored_public_values_and_never_renders_hidden_secrets(self) -> None:
        repository = ManagerProviderRepository()
        repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="escape-demo",
            display_name='<img src=x onerror="alert(1)">',
            enabled=True,
            connection={"model": "demo-model"},
        )

        html = render_provider_profiles_page()

        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", html)
        self.assertNotIn('<img src=x onerror="alert(1)">', html)
        self.assertNotIn("ciphertext", html.lower())
        self.assertNotIn('type="hidden" name="api_key"', html)


if __name__ == "__main__":
    unittest.main()
