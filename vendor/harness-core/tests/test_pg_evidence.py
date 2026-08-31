from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT


os.environ.setdefault("HARNESS_ENABLE_STAGED_PLUGIN_TESTS", "1")
os.environ.setdefault(
    "HARNESS_STAGED_PLUGIN_ROOT",
    str(PLUGIN_SOURCE_ROOT),
)
from app.pg_evidence import (
    DEFAULT_SENSITIVE_COLUMN_PATTERNS,
    PgEvidenceRequest,
    PgProfile,
    PgProfilePolicy,
    apply_readonly_credential_aliases,
    build_parameter_audit,
    build_postgres_connect_kwargs,
    build_pg_evidence_plan,
    discover_pg_profiles,
    execute_pg_evidence_plan,
    load_pg_policy,
    render_pg_evidence_outputs,
    run_pg_evidence,
    safe_error_summary,
    validate_readonly_sql,
)


class PgEvidenceProfileTests(unittest.TestCase):
    def test_logical_environment_profile_can_reuse_complete_readonly_credential_triplet(self) -> None:
        credentials = {
            "pg_his_test_readonly_dsn": "postgresql://example.invalid/df_his",
            "pg_his_test_readonly_user": "df_bi",
            "pg_his_test_readonly_password": "secret-password",
        }

        resolved = apply_readonly_credential_aliases(
            credentials,
            {"his_152": "his_test"},
        )

        self.assertEqual(
            credentials["pg_his_test_readonly_dsn"],
            resolved["pg_his_152_readonly_dsn"],
        )
        self.assertEqual(
            credentials["pg_his_test_readonly_user"],
            resolved["pg_his_152_readonly_user"],
        )
        self.assertEqual(
            credentials["pg_his_test_readonly_password"],
            resolved["pg_his_152_readonly_password"],
        )

    def test_discovers_named_readonly_profile_without_exposing_values(self) -> None:
        profiles = discover_pg_profiles(
            {
                "pg_df_jj_menzhen_readonly_dsn": "postgresql://secret-host/df_jj_menzhen",
                "pg_df_jj_menzhen_readonly_user": "readonly-user",
                "pg_df_jj_menzhen_readonly_password": "secret-password",
            }
        )

        self.assertEqual(["df_jj_menzhen"], [profile.name for profile in profiles])
        serialized = json.dumps([profile.to_dict() for profile in profiles], ensure_ascii=False)
        self.assertNotIn("secret-host", serialized)
        self.assertNotIn("secret-password", serialized)
        self.assertNotIn("readonly-user", serialized)

    def test_policy_rejects_profile_outside_test_or_development(self) -> None:
        payload = {
            "schema_version": "1.0-pg-evidence-profiles",
            "default_mode": "off",
            "profiles": {
                "df_jj_menzhen": {
                    "environment": "production",
                    "enabled": True,
                    "max_rows": 50,
                    "connect_timeout_seconds": 5,
                    "query_timeout_seconds": 10,
                    "total_timeout_seconds": 45,
                    "max_metadata_queries": 3,
                }
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            policy = load_pg_policy(path)

        self.assertFalse(policy.profiles["df_jj_menzhen"].executable)
        self.assertIn("environment", "\n".join(policy.profiles["df_jj_menzhen"].blockers))

    def test_custom_sensitive_patterns_extend_instead_of_replace_the_default_baseline(self) -> None:
        payload = policy_payload("df_jj_menzhen")
        payload["profiles"]["df_jj_menzhen"]["sensitive_column_patterns"] = [
            "custom_secret"
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            policy = load_pg_policy(path)

        patterns = policy.profiles["df_jj_menzhen"].sensitive_column_patterns
        self.assertEqual(
            set(DEFAULT_SENSITIVE_COLUMN_PATTERNS) | {"custom_secret"},
            set(patterns),
        )

    def test_policy_can_bind_physical_schema_to_logical_readonly_profile(self) -> None:
        payload = policy_payload("his_test")
        payload["profiles"]["his_test"]["schemas"] = ["df_zhushuju"]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            policy = load_pg_policy(path)

        self.assertEqual(("df_zhushuju",), policy.profiles["his_test"].schemas)

    def test_policy_can_delegate_schema_authorization_to_postgres(self) -> None:
        payload = policy_payload("his_test")
        payload["profiles"]["his_test"]["schemas"] = ["*"]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            policy = load_pg_policy(path)

        self.assertTrue(policy.profiles["his_test"].executable)
        self.assertEqual(("*",), policy.profiles["his_test"].schemas)

    def test_local_152_profile_accepts_schema_selected_by_df_bi_permissions(self) -> None:
        policy = load_pg_policy(
            Path(__file__).resolve().parents[1]
            / "config"
            / "pg_evidence_profiles.local.json"
        )
        request = PgEvidenceRequest(
            subject="住院病人信息只读核对",
            sql="SELECT * FROM df_jj_zhuyuan.zy_bingrenxx LIMIT 1",
        )
        profile = PgProfile(
            name="his_152",
            dsn_configured=True,
            user_configured=True,
            password_configured=True,
            credential_prefix="pg_his_152_readonly",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_pg_evidence_plan(
                request,
                policy,
                [profile],
                Path(temp_dir),
            )

        self.assertEqual("ready", plan.status)
        self.assertEqual("his_152", plan.selected_profile)


class PgEvidenceSqlGuardTests(unittest.TestCase):
    def test_allows_parameterized_select(self) -> None:
        result = validate_readonly_sql(
            "SELECT keshiid FROM df_jj_menzhen.mz_guahaob WHERE bingrenid = %(patient_id)s",
            {"patient_id": "x"},
        )

        self.assertEqual("pass", result.status)
        self.assertEqual(("patient_id",), result.parameter_names)

    def test_blocks_write_and_multi_statement(self) -> None:
        self.assertEqual("blocked", validate_readonly_sql("DELETE FROM mz_guahaob", {}).status)
        self.assertEqual(
            "blocked",
            validate_readonly_sql("SELECT 1; UPDATE mz_guahaob SET x = 1", {}).status,
        )

    def test_blocks_missing_named_parameter_and_lock(self) -> None:
        self.assertEqual(
            "blocked",
            validate_readonly_sql("SELECT * FROM mz_guahaob WHERE bingrenid = %(patient_id)s", {}).status,
        )
        self.assertEqual(
            "blocked",
            validate_readonly_sql("SELECT * FROM mz_guahaob FOR UPDATE", {}).status,
        )

    def test_blocks_nested_write_copy_program_and_case_newline_bypasses(self) -> None:
        blocked_sql = (
            "WITH x AS (DELETE FROM mz_guahaob RETURNING *) SELECT * FROM x",
            "COPY (SELECT 1) TO PROGRAM 'cat'",
            "SeLeCt *\nFROM mz_guahaob\nFoR\nUpDaTe",
        )

        for sql in blocked_sql:
            with self.subTest(sql=sql):
                result = validate_readonly_sql(sql, {})
                self.assertEqual("blocked", result.status)

    def test_allows_forbidden_words_inside_comments_and_string_literals(self) -> None:
        sql = """
            /* UPDATE mz_guahaob SET x = 1 */
            SELECT guahaobid
            FROM mz_guahaob
            -- FOR UPDATE
            WHERE note = 'delete update copy to program' AND guahaobid = %(id)s
        """

        result = validate_readonly_sql(sql, {"id": "1"})

        self.assertEqual("pass", result.status, result.blockers)

    def test_blocks_functions_and_table_functions_but_keeps_plain_columns_and_readonly_with(self) -> None:
        blocked_sql = (
            "SELECT pg_read_file('/etc/passwd') FROM his_config",
            "SELECT * FROM pg_ls_dir('/tmp')",
            'SELECT * FROM "pg_catalog"."pg_ls_dir"(\'/tmp\')',
            'SELECT * FROM "pg_catalog"."pg_""ls_dir"(\'/tmp\')',
            "SELECT custom_probe(code) FROM his_config",
        )
        for sql in blocked_sql:
            with self.subTest(sql=sql):
                result = validate_readonly_sql(sql, {})
                self.assertEqual("blocked", result.status)
                self.assertIn("函数", "\n".join(result.blockers))

        allowed = validate_readonly_sql(
            "WITH configured AS ("
            "SELECT code, value FROM his_test.his_config WHERE code = %(code)s"
            ") SELECT code, value FROM configured",
            {"code": "A"},
        )
        self.assertEqual("pass", allowed.status, allowed.blockers)

    def test_blocks_projection_expression_without_from(self) -> None:
        result = validate_readonly_sql("SELECT 1", {})

        self.assertEqual("blocked", result.status)
        self.assertIn("投影", "\n".join(result.blockers))
        self.assertEqual("pass", validate_readonly_sql("SELECT code", {}).status)

    def test_blocks_multi_statement_even_when_first_statement_is_readonly(self) -> None:
        result = validate_readonly_sql(
            "SELECT 1;\nUPDATE mz_guahaob SET x = 1",
            {},
        )

        self.assertEqual("blocked", result.status)
        self.assertIn("单条", "\n".join(result.blockers))

    def test_requires_every_named_parameter(self) -> None:
        result = validate_readonly_sql(
            "SELECT * FROM mz_guahaob WHERE a = %(a)s AND b = %(b)s",
            {"a": "present"},
        )

        self.assertEqual("blocked", result.status)
        self.assertIn("b", "\n".join(result.blockers))


class FakePostgresExecutor:
    def __init__(self, metadata: list[dict[str, str]], rows: list[dict[str, object]]) -> None:
        self.metadata = metadata
        self.rows = rows
        self.calls: list[str] = []
        self.sqls: list[str] = []

    def discover_metadata(self, **kwargs: object) -> list[dict[str, str]]:
        self.calls.append("metadata")
        return self.metadata

    def execute_select(self, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append("select")
        self.sqls.append(str(kwargs.get("sql") or ""))
        return self.rows


class PgEvidencePlanningTests(unittest.TestCase):
    def test_physical_schema_alias_selects_logical_profile_without_guessing_credentials(self) -> None:
        payload = policy_payload("his_test")
        payload["profiles"]["his_test"]["schemas"] = ["df_zhushuju"]
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "policy.json"
            policy_path.write_text(json.dumps(payload), encoding="utf-8")
            policy = load_pg_policy(policy_path)
            plan = build_pg_evidence_plan(
                PgEvidenceRequest(
                    subject="验证收费项目目录",
                    keywords=("gy_shoufeixm",),
                    sql="SELECT * FROM df_zhushuju.gy_shoufeixm LIMIT 1",
                ),
                policy,
                [complete_profile("his_test")],
                Path(temp_dir),
            )

        self.assertEqual("ready", plan.status)
        self.assertEqual("his_test", plan.selected_profile)
        self.assertEqual("df_zhushuju.gy_shoufeixm", plan.selected_table)

    def test_database_authority_profile_accepts_any_explicit_schema_for_db_to_authorize(self) -> None:
        payload = policy_payload("his_test")
        payload["profiles"]["his_test"]["schemas"] = ["*"]
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "policy.json"
            policy_path.write_text(json.dumps(payload), encoding="utf-8")
            policy = load_pg_policy(policy_path)
            plan = build_pg_evidence_plan(
                PgEvidenceRequest(
                    subject="验证结算主数据",
                    keywords=("mz_jiesuan1",),
                    sql="SELECT * FROM df_jj_menzhen.mz_jiesuan1 LIMIT 1",
                ),
                policy,
                [complete_profile("his_test")],
                Path(temp_dir),
            )

        self.assertEqual("ready", plan.status)
        self.assertEqual("his_test", plan.selected_profile)
        self.assertEqual("df_jj_menzhen.mz_jiesuan1", plan.selected_table)

    def test_alias_or_projection_expression_is_blocked_before_fake_executor(self) -> None:
        blocked_sql = (
            "SELECT patient_phone AS safe_value FROM df_jj_menzhen.mz_guahaob",
            "SELECT patient_phone safe_value FROM df_jj_menzhen.mz_guahaob",
            "SELECT patient_phone || '' FROM df_jj_menzhen.mz_guahaob",
        )
        for sql in blocked_sql:
            with self.subTest(sql=sql), tempfile.TemporaryDirectory() as temp_dir:
                executor = FakePostgresExecutor(
                    metadata=[],
                    rows=[{"safe_value": "13800138000"}],
                )
                run = run_pg_evidence(
                    request=PgEvidenceRequest(subject="别名脱敏绕过", sql=sql),
                    policy=build_policy("df_jj_menzhen"),
                    profiles=[complete_profile("df_jj_menzhen")],
                    project_root=Path(temp_dir),
                    mode="execute",
                    executor_factory=lambda *, plan: executor,
                )

                self.assertEqual("blocked", run.status)
                self.assertEqual([], executor.calls)
                self.assertNotIn(
                    "13800138000",
                    json.dumps(run.to_dict(), ensure_ascii=False),
                )
    def test_source_schema_and_table_select_unique_profile_without_metadata_scan(self) -> None:
        policy = build_policy("df_jj_menzhen")
        profiles = [complete_profile("df_jj_menzhen"), complete_profile("df_jj_zhuyuan")]
        request = PgEvidenceRequest(
            subject="验证门诊挂号数据",
            keywords=("挂号",),
            sql="SELECT guahaobid, bingrenid FROM df_jj_menzhen.mz_guahaob WHERE guahaobid = %(registration_id)s",
            parameters={"registration_id": "1"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "RegistrationEntity.java").write_text(
                '@Table(name = "mz_guahaob", schema = "df_jj_menzhen")', encoding="utf-8"
            )
            plan = build_pg_evidence_plan(request, policy, profiles, project_root)

        self.assertEqual("ready", plan.status)
        self.assertEqual("df_jj_menzhen", plan.selected_profile)
        self.assertEqual("df_jj_menzhen.mz_guahaob", plan.selected_table)
        self.assertEqual(0, plan.metadata_queries_remaining)

    def test_ambiguous_candidates_need_evidence_and_never_execute_select(self) -> None:
        policy = build_policy("df_jj_menzhen", "df_jj_zhuyuan")
        profiles = [complete_profile("df_jj_menzhen"), complete_profile("df_jj_zhuyuan")]
        request = PgEvidenceRequest(
            subject="验证挂号数据",
            keywords=("挂号",),
            sql="SELECT guahaobid FROM mz_guahaob WHERE guahaobid = %(registration_id)s",
            parameters={"registration_id": "1"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_pg_evidence_plan(request, policy, profiles, Path(temp_dir))
        executor = FakePostgresExecutor(metadata=[], rows=[{"guahaobid": "1"}])
        result = execute_pg_evidence_plan(plan, executor)

        self.assertEqual("needs_evidence", plan.status)
        self.assertEqual("needs_evidence", result.status)
        self.assertEqual([], executor.calls)

    def test_metadata_query_budget_is_never_exceeded(self) -> None:
        policy = build_policy("df_jj_menzhen")
        profiles = [complete_profile("df_jj_menzhen")]
        request = PgEvidenceRequest(
            subject="验证挂号数据",
            keywords=("挂号",),
            sql="SELECT guahaobid FROM mz_guahaob WHERE guahaobid = %(registration_id)s",
            parameters={"registration_id": "1"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_pg_evidence_plan(request, policy, profiles, Path(temp_dir))
        executor = FakePostgresExecutor(
            metadata=[{"schema": "df_jj_menzhen", "table": "mz_guahaob"}],
            rows=[{"guahaobid": "1"}],
        )
        result = execute_pg_evidence_plan(plan, executor)

        self.assertEqual("passed", result.status)
        self.assertEqual(["metadata", "select"], executor.calls)

    def test_metadata_resolution_qualifies_unqualified_query_before_select(self) -> None:
        request = PgEvidenceRequest(
            subject="按元数据定位挂号表",
            keywords=("挂号",),
            sql="SELECT guahaobid FROM mz_guahaob WHERE guahaobid = %(registration_id)s",
            parameters={"registration_id": "1"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_pg_evidence_plan(
                request,
                build_policy("df_jj_menzhen"),
                [complete_profile("df_jj_menzhen")],
                Path(temp_dir),
            )
        executor = FakePostgresExecutor(
            metadata=[{"schema": "df_jj_menzhen", "table": "mz_guahaob"}],
            rows=[{"guahaobid": "1"}],
        )

        result = execute_pg_evidence_plan(plan, executor)

        self.assertEqual("passed", result.status)
        self.assertEqual(
            [
                'SELECT guahaobid FROM "df_jj_menzhen"."mz_guahaob" '
                "WHERE guahaobid = %(registration_id)s",
            ],
            executor.sqls,
        )

    def test_database_scalar_values_are_json_serializable(self) -> None:
        request = PgEvidenceRequest(
            subject="序列化数据库日期金额",
            sql="SELECT qiyongrq, price FROM df_jj_menzhen.mz_guahaob",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_pg_evidence_plan(
                request,
                build_policy("df_jj_menzhen"),
                [complete_profile("df_jj_menzhen")],
                Path(temp_dir),
            )
        executor = FakePostgresExecutor(
            metadata=[],
            rows=[{
                "qiyongrq": datetime(2026, 8, 18, 9, 30),
                "price": Decimal("12.30"),
            }],
        )

        result = execute_pg_evidence_plan(plan, executor)

        self.assertEqual("passed", result.status)
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertIn("2026-08-18T09:30:00", serialized)
        self.assertIn('"12.30"', serialized)

    def test_query_timeout_is_classified_without_retry_or_error_detail(self) -> None:
        class TimeoutExecutor(FakePostgresExecutor):
            def execute_select(self, **kwargs: object) -> list[dict[str, object]]:
                self.calls.append("select")
                raise TimeoutError("secret-host timed out")

        request = PgEvidenceRequest(
            subject="查询超时分类",
            sql="SELECT guahaobid FROM df_jj_menzhen.mz_guahaob WHERE guahaobid = %(id)s",
            parameters={"id": "1"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_pg_evidence_plan(
                request,
                build_policy("df_jj_menzhen"),
                [complete_profile("df_jj_menzhen")],
                Path(temp_dir),
            )
        executor = TimeoutExecutor(metadata=[], rows=[])
        result = execute_pg_evidence_plan(plan, executor)

        self.assertEqual("timeout", result.status)
        self.assertEqual(["select"], executor.calls)
        self.assertNotIn("secret-host", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_execution_failure_never_exposes_dsn_or_password(self) -> None:
        class FailingExecutor(FakePostgresExecutor):
            def execute_select(self, **kwargs: object) -> list[dict[str, object]]:
                self.calls.append("select")
                raise RuntimeError(
                    "postgresql://readonly:secret-password@secret-host/his failed"
                )

        request = PgEvidenceRequest(
            subject="异常脱敏",
            sql="SELECT guahaobid FROM df_jj_menzhen.mz_guahaob WHERE guahaobid = %(id)s",
            parameters={"id": "1"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_pg_evidence_plan(
                request,
                build_policy("df_jj_menzhen"),
                [complete_profile("df_jj_menzhen")],
                Path(temp_dir),
            )
        executor = FailingExecutor(metadata=[], rows=[])

        result = execute_pg_evidence_plan(plan, executor)
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)

        self.assertEqual("failed", result.status)
        self.assertEqual(["select"], executor.calls)
        for secret in ("secret-password", "secret-host", "readonly"):
            self.assertNotIn(secret, serialized)

    def test_safe_error_summary_classifies_authentication_failure_without_details(self) -> None:
        class AuthenticationError(Exception):
            sqlstate = "28P01"

        error = AuthenticationError(
            "password authentication failed for user df_bi at secret-host"
        )

        summary = safe_error_summary(error)

        self.assertEqual("PG_AUTH_FAILED: PostgreSQL 认证失败；未重试。", summary)
        self.assertNotIn("df_bi", summary)
        self.assertNotIn("secret-host", summary)

    def test_safe_error_summary_classifies_network_failure_from_cause(self) -> None:
        class DatabaseOperationalError(Exception):
            pass

        error = DatabaseOperationalError("connection failed")
        error.__cause__ = ConnectionRefusedError("secret-host:5432")

        summary = safe_error_summary(error)

        self.assertEqual(
            "PG_NETWORK_UNREACHABLE: PostgreSQL 网络连接失败；未重试。",
            summary,
        )
        self.assertNotIn("secret-host", summary)

    def test_safe_error_summary_uses_driver_diagnostic_fields_without_details(self) -> None:
        class Diagnostic:
            sqlstate = "28P01"
            message_primary = "password authentication failed for user df_bi"

        class DriverError(Exception):
            diag = Diagnostic()

        summary = safe_error_summary(DriverError("opaque connection failure"))

        self.assertEqual("PG_AUTH_FAILED: PostgreSQL 认证失败；未重试。", summary)
        self.assertNotIn("df_bi", summary)

    def test_safe_error_summary_keeps_generic_connection_message_distinct(self) -> None:
        class OperationalError(Exception):
            pass

        summary = safe_error_summary(OperationalError("connection failed"))

        self.assertEqual(
            "PG_CONNECTION_FAILED: PostgreSQL 连接失败；未重试。",
            summary,
        )

    def test_postgres_connect_kwargs_match_his_connection_contract(self) -> None:
        kwargs = build_postgres_connect_kwargs(
            "postgresql://192.168.1.154:5432/df_his?sslmode=disable"
        )

        self.assertEqual(
            {
                "host": "192.168.1.154",
                "port": 5432,
                "dbname": "df_his",
                "sslmode": "disable",
            },
            kwargs,
        )

    def test_max_rows_and_sensitive_column_masks_are_enforced(self) -> None:
        request = PgEvidenceRequest(
            subject="限行和脱敏",
            sql="SELECT patient_phone, guahaobid FROM df_jj_menzhen.mz_guahaob",
        )
        policy = build_policy("df_jj_menzhen")
        policy.profiles["df_jj_menzhen"] = PgProfilePolicy(
            **{
                **policy.profiles["df_jj_menzhen"].__dict__,
                "max_rows": 2,
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = build_pg_evidence_plan(
                request,
                policy,
                [complete_profile("df_jj_menzhen")],
                Path(temp_dir),
            )
        executor = FakePostgresExecutor(
            metadata=[],
            rows=[
                {"patient_phone": "13800138000", "guahaobid": "1"},
                {"patient_phone": "13800138001", "guahaobid": "2"},
                {"patient_phone": "13800138002", "guahaobid": "3"},
            ],
        )

        result = execute_pg_evidence_plan(plan, executor)

        self.assertEqual(2, result.row_count)
        self.assertEqual(("patient_phone",), result.masked_columns)
        self.assertEqual(
            ("[REDACTED]", "[REDACTED]"),
            tuple(row["patient_phone"] for row in result.rows),
        )


class PgEvidenceCliTests(unittest.TestCase):
    def test_plan_mode_never_calls_executor_factory(self) -> None:
        request = PgEvidenceRequest(
            subject="只生成数据库证据计划",
            sql="SELECT guahaobid FROM df_jj_menzhen.mz_guahaob WHERE guahaobid = %(id)s",
            parameters={"id": "1"},
        )

        def fail_if_called(*args: object, **kwargs: object) -> object:
            raise AssertionError("plan 模式不得创建数据库执行器")

        with tempfile.TemporaryDirectory() as temp_dir:
            run = run_pg_evidence(
                request=request,
                policy=build_policy("df_jj_menzhen"),
                profiles=[complete_profile("df_jj_menzhen")],
                project_root=Path(temp_dir),
                mode="plan",
                executor_factory=fail_if_called,
            )

        self.assertEqual("planned", run.status)

    def test_outputs_redact_parameter_and_sensitive_row_values(self) -> None:
        request = PgEvidenceRequest(
            subject="执行脱敏证据查询",
            sql="SELECT patient_phone FROM df_jj_menzhen.mz_guahaob WHERE guahaobid = %(id)s",
            parameters={"id": "secret-registration-id"},
        )
        executor = FakePostgresExecutor(
            metadata=[],
            rows=[{"patient_phone": "13800138000", "guahaobid": "1"}],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run = run_pg_evidence(
                request=request,
                policy=build_policy("df_jj_menzhen"),
                profiles=[complete_profile("df_jj_menzhen")],
                project_root=Path(temp_dir),
                mode="execute",
                executor_factory=lambda *args, **kwargs: executor,
            )

        serialized = render_pg_evidence_outputs(run)
        self.assertEqual("passed", run.status)
        self.assertNotIn("secret-registration-id", serialized)
        self.assertNotIn("13800138000", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_execute_without_driver_returns_blocked_without_retry(self) -> None:
        calls = 0

        def missing_driver(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            raise RuntimeError("缺少可选 PostgreSQL 驱动 psycopg")

        request = PgEvidenceRequest(
            subject="驱动缺失测试",
            sql="SELECT guahaobid FROM df_jj_menzhen.mz_guahaob WHERE guahaobid = %(id)s",
            parameters={"id": "1"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run = run_pg_evidence(
                request=request,
                policy=build_policy("df_jj_menzhen"),
                profiles=[complete_profile("df_jj_menzhen")],
                project_root=Path(temp_dir),
                mode="execute",
                executor_factory=missing_driver,
            )

        self.assertEqual("blocked", run.status)
        self.assertEqual(1, calls)
        self.assertIn("驱动", "\n".join(run.result.blockers))

    def test_production_profile_never_creates_executor(self) -> None:
        calls = 0

        def fail_if_called(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            raise AssertionError("production profile must never create an executor")

        profile = PgProfilePolicy(
            name="production",
            environment="production",
            enabled=True,
            max_rows=50,
            connect_timeout_seconds=5,
            query_timeout_seconds=10,
            total_timeout_seconds=45,
            max_metadata_queries=3,
            sensitive_column_patterns=("patient", "phone"),
        )
        policy = type("Policy", (), {"profiles": {"production": profile}, "blockers": ()})()
        request = PgEvidenceRequest(
            subject="生产库不得执行",
            sql="SELECT code FROM production.his_config WHERE code = %(code)s",
            parameters={"code": "EXAMPLE"},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            run = run_pg_evidence(
                request=request,
                policy=policy,
                profiles=[complete_profile("production")],
                project_root=Path(temp_dir),
                mode="execute",
                executor_factory=fail_if_called,
            )

        self.assertEqual("blocked", run.status)
        self.assertEqual(0, calls)

    def test_parameter_metadata_hash_excludes_values_and_is_stable_for_name_and_type(self) -> None:
        request = PgEvidenceRequest(
            subject="额外参数只进哈希审计",
            sql="SELECT guahaobid FROM df_jj_menzhen.mz_guahaob WHERE guahaobid = %(id)s",
            parameters={"id": "used-secret", "unused": "unused-secret"},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            run = run_pg_evidence(
                request=request,
                policy=build_policy("df_jj_menzhen"),
                profiles=[complete_profile("df_jj_menzhen")],
                project_root=Path(temp_dir),
                mode="plan",
            )

        audit = run.result.parameter_audit
        self.assertEqual(("id", "unused"), tuple(item["name"] for item in audit))
        self.assertTrue(all(set(item) == {"name", "type", "metadata_sha256"} for item in audit))
        self.assertTrue(all(len(item["metadata_sha256"]) == 64 for item in audit))
        self.assertEqual(
            build_parameter_audit({"id": "different-value", "unused": "another-value"}),
            audit,
        )
        serialized = json.dumps(audit, ensure_ascii=False)
        self.assertNotIn("used-secret", serialized)
        self.assertNotIn("unused-secret", serialized)

    def test_cli_plan_writes_outputs_without_secret_values(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_file = root / "request.json"
            policy_file = root / "policy.json"
            credentials_file = root / "credentials.json"
            output_dir = root / "outputs"
            request_file.write_text(
                json.dumps(
                    {
                        "subject": "挂号证据计划",
                        "keywords": ["挂号"],
                        "sql": "SELECT patient_phone FROM df_jj_menzhen.mz_guahaob WHERE guahaobid = %(id)s",
                        "parameters": {"id": "secret-registration-id"},
                    }
                ),
                encoding="utf-8",
            )
            policy_file.write_text(
                json.dumps(policy_payload("df_jj_menzhen")),
                encoding="utf-8",
            )
            credentials_file.write_text(
                json.dumps(
                    {
                        "pg_df_jj_menzhen_readonly_dsn": "postgresql://secret-host/secret-db",
                        "pg_df_jj_menzhen_readonly_user": "secret-user",
                        "pg_df_jj_menzhen_readonly_password": "secret-password",
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(project_root / "tools" / "pg_evidence.py"),
                    "--request-file",
                    str(request_file),
                    "--profile-policy",
                    str(policy_file),
                    "--credentials-file",
                    str(credentials_file),
                    "--mode",
                    "plan",
                    "--project-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=project_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stdout)
            expected = {
                "pg_evidence_plan.json",
                "pg_evidence_plan.md",
                "pg_evidence_result.json",
                "pg_evidence_result.md",
                "pg_evidence_audit.json",
            }
            self.assertTrue(expected.issubset({path.name for path in output_dir.iterdir()}))
            serialized = completed.stdout + "\n" + "\n".join(
                path.read_text(encoding="utf-8") for path in output_dir.iterdir()
            )
            for secret in (
                "secret-host",
                "secret-db",
                "secret-user",
                "secret-password",
                "secret-registration-id",
            ):
                self.assertNotIn(secret, serialized)

    def test_self_check_pg_adapter_uses_only_fake_executor(self) -> None:
        from tools.self_check import run_pg_evidence_checks

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            checks = run_pg_evidence_checks(output_dir=output_dir)
            serialized = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output_dir.rglob("*")
                if path.is_file()
            )

        self.assertTrue(checks)
        self.assertTrue(all(item["status"] == "pass" for item in checks), checks)
        self.assertNotIn("fake-secret", serialized)


def complete_profile(name: str) -> PgProfile:
    return PgProfile(
        name=name,
        dsn_configured=True,
        user_configured=True,
        password_configured=True,
        credential_prefix=f"pg_{name}_readonly",
    )


def build_policy(*names: str):
    profiles = {
        name: PgProfilePolicy(
            name=name,
            environment="test",
            enabled=True,
            max_rows=50,
            connect_timeout_seconds=5,
            query_timeout_seconds=10,
            total_timeout_seconds=45,
            max_metadata_queries=3,
            sensitive_column_patterns=("patient", "phone"),
        )
        for name in names
    }
    return type("Policy", (), {"profiles": profiles, "blockers": ()})()


def policy_payload(*names: str) -> dict[str, object]:
    return {
        "schema_version": "1.0-pg-evidence-profiles",
        "default_mode": "off",
        "profiles": {
            name: {
                "environment": "test",
                "enabled": True,
                "max_rows": 50,
                "connect_timeout_seconds": 5,
                "query_timeout_seconds": 10,
                "total_timeout_seconds": 45,
                "max_metadata_queries": 3,
                "sensitive_column_patterns": ["patient", "phone"],
            }
            for name in names
        },
    }


if __name__ == "__main__":
    unittest.main()
