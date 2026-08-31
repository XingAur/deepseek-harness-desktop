from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from app import database


class DatabaseGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_init_enables_connection_guards_and_records_schema_version(self) -> None:
        database.init_db()

        with database.connect() as conn:
            foreign_keys = int(conn.execute("pragma foreign_keys").fetchone()[0])
            busy_timeout = int(conn.execute("pragma busy_timeout").fetchone()[0])
            journal_mode = str(conn.execute("pragma journal_mode").fetchone()[0])
            user_version = int(conn.execute("pragma user_version").fetchone()[0])
            migration = conn.execute(
                "select * from harness_schema_migrations where to_version = ?",
                (database.HARNESS_SCHEMA_VERSION,),
            ).fetchone()

        self.assertEqual(1, foreign_keys)
        self.assertGreaterEqual(busy_timeout, 5000)
        self.assertEqual("wal", journal_mode.lower())
        self.assertEqual(database.HARNESS_SCHEMA_VERSION, user_version)
        self.assertIsNotNone(migration)

    def test_read_database_user_version_closes_its_readonly_connection(self) -> None:
        readonly_path = self.root / "readonly.sqlite"
        readonly_path.touch()
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = (64,)

        with mock.patch("app.database.sqlite3.connect", return_value=connection):
            version = database.read_database_user_version(readonly_path)

        self.assertEqual(64, version)
        connection.close.assert_called_once_with()

    def test_fresh_subprocess_never_connects_outside_the_configured_temporary_root(self) -> None:
        subprocess_root = self.root / "subprocess"
        subprocess_root.mkdir()
        database_path = subprocess_root / "harness.sqlite"
        environment = dict(os.environ)
        environment.update({
            "HARNESS_DB_PATH": str(database_path),
            "HIS_KNOWLEDGE_HOME": str(subprocess_root / "knowledge"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        script = """
import os
import sqlite3
from pathlib import Path

root = Path(os.environ['HARNESS_DB_PATH']).parent.resolve()
real_connect = sqlite3.connect
observed = []

def spy(target, *arguments, **keywords):
    raw = str(target)
    observed.append(raw)
    if raw.startswith('file:'):
        raw = raw[5:].split('?', 1)[0]
    candidate = Path(raw).expanduser().resolve()
    if candidate != root and root not in candidate.parents:
        raise RuntimeError('database connection escaped temporary root')
    return real_connect(target, *arguments, **keywords)

sqlite3.connect = spy
from app import database
database.init_db()
with database.connect() as connection:
    connection.execute('select 1').fetchone()
if not observed:
    raise RuntimeError('database connect spy did not observe any path')
print('temporary-root-only')
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("temporary-root-only", completed.stdout.strip())

    def test_import_scoped_repository_tests_preserve_explicit_process_environment(self) -> None:
        subprocess_temp = tempfile.TemporaryDirectory(
            prefix="his_harness_stage_f_import_environment_",
            dir="/private/tmp",
        )
        self.addCleanup(subprocess_temp.cleanup)
        subprocess_root = Path(subprocess_temp.name)
        database_path = subprocess_root / "harness.sqlite"
        knowledge_home = subprocess_root / "knowledge"
        environment = dict(os.environ)
        environment.update({
            "HARNESS_DB_PATH": str(database_path),
            "HIS_KNOWLEDGE_HOME": str(knowledge_home),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        script = """
import importlib
import os

expected_db = os.environ['HARNESS_DB_PATH']
expected_knowledge = os.environ['HIS_KNOWLEDGE_HOME']
for module_name in (
    'tests.test_knowledge_consultation',
    'tests.test_task_capability_routing',
    'tests.test_task_intent_repository',
    'tests.test_task_intent_router',
    'tests.test_task_intent_service',
):
    importlib.import_module(module_name)
    if os.environ.get('HARNESS_DB_PATH') != expected_db:
        raise RuntimeError(f'{module_name} changed HARNESS_DB_PATH')
    if os.environ.get('HIS_KNOWLEDGE_HOME') != expected_knowledge:
        raise RuntimeError(f'{module_name} changed HIS_KNOWLEDGE_HOME')

importlib.import_module('tests.test_local_agent_confirmation')
importlib.import_module('tests.test_local_agent_cli')
print('explicit-environment-preserved')
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("explicit-environment-preserved", completed.stdout.strip())

    def test_schema_v73_creates_manager_governance_task_intent_local_agent_repair_flux_lite_and_change_context_tables(self) -> None:
        database.init_db()
        with database.connect() as conn:
            names = {
                row[0] for row in conn.execute(
                    "select name from sqlite_master where type = 'table'"
                )
            }
            indexes = {
                row[0] for row in conn.execute(
                    "select name from sqlite_master where type = 'index'"
                )
            }
            triggers = {
                row[0] for row in conn.execute(
                    "select name from sqlite_master where type = 'trigger'"
                )
            }
            task_intent_event_columns = {
                row[1]
                for row in conn.execute(
                    "pragma table_info(manager_task_intent_events)"
                )
            }

        self.assertEqual(73, database.HARNESS_SCHEMA_VERSION)
        self.assertTrue({
            "manager_provider_scopes", "manager_provider_profiles",
            "manager_provider_credentials", "manager_provider_action_audits",
            "manager_knowledge_consultations", "manager_provider_imports",
            "manager_provider_action_plans", "manager_learning_candidates",
            "manager_business_acceptance_evidence",
            "manager_business_acceptance_decisions",
            "manager_task_intent_sessions", "manager_task_intent_events",
        }.issubset(names))
        self.assertTrue({
            "idx_manager_provider_action_plans_profile_state",
            "idx_manager_provider_action_plans_expiry",
            "idx_manager_provider_action_audits_profile_created",
            "ux_manager_learning_candidates_source_audit_type",
            "idx_manager_business_acceptance_decisions_evidence",
            "idx_manager_task_intent_events_conversation",
        }.issubset(indexes))
        self.assertTrue({
            "trg_manager_provider_action_plans_created_at_immutable",
            "trg_manager_provider_action_plans_review_immutable",
            "trg_manager_provider_action_audits_created_at_immutable",
            "trg_manager_business_acceptance_evidence_created_at_immutable",
            "trg_manager_business_acceptance_evidence_append_only_update",
            "trg_manager_business_acceptance_evidence_append_only_delete",
            "trg_manager_business_acceptance_decisions_append_only_update",
            "trg_manager_business_acceptance_decisions_append_only_delete",
            "trg_manager_task_intent_events_append_only_update",
            "trg_manager_task_intent_events_append_only_delete",
        }.issubset(triggers))
        self.assertIn("mutation_requested", task_intent_event_columns)
        self.assertTrue({
            "local_agent_runs", "local_agent_attempts", "local_agent_run_events",
            "local_agent_artifacts",
        }.issubset(names))
        self.assertTrue({
            "trg_local_agent_run_events_append_only_update",
            "trg_local_agent_run_events_append_only_insert_collision",
            "trg_local_agent_run_events_append_only_delete",
            "trg_local_agent_artifacts_append_only_update",
            "trg_local_agent_artifacts_append_only_insert_collision",
            "trg_local_agent_artifacts_append_only_delete",
        }.issubset(triggers))
        self.assertTrue({
            "idx_local_agent_attempts_status_id",
            "ux_local_agent_attempts_one_active_per_run",
        }.issubset(indexes))
        self.assertTrue({
            "repair_retrospectives",
            "repair_learning_rules",
            "repair_learning_observations",
            "flux_lite_reviewer_opinions",
            "flux_lite_experience_candidates",
        }.issubset(names))
        self.assertTrue({
            "change_context_layers",
            "change_context_layer_artifacts",
            "change_context_packs",
            "change_context_pack_layers",
            "change_context_applicability_decisions",
            "change_context_gate_results",
            "change_context_events",
            "change_context_projection_metrics",
        }.issubset(names))
        self.assertTrue({
            "idx_repair_retrospectives_run",
            "idx_repair_learning_rules_state",
            "idx_repair_learning_observations_rule",
            "idx_flux_lite_opinions_attempt",
            "idx_flux_lite_candidates_context",
        }.issubset(indexes))
        self.assertTrue({
            "trg_flux_lite_reviewer_opinions_append_only_update",
            "trg_flux_lite_reviewer_opinions_append_only_delete",
            "trg_flux_lite_candidates_append_only_update",
            "trg_flux_lite_candidates_append_only_delete",
        }.issubset(triggers))
        self.assertTrue({
            "trg_change_context_layers_no_update",
            "trg_change_context_layers_no_delete",
            "trg_change_context_packs_no_update",
            "trg_change_context_packs_no_delete",
            "trg_change_context_gate_no_update",
            "trg_change_context_gate_no_delete",
            "trg_change_context_events_no_update",
            "trg_change_context_events_no_delete",
        }.issubset(triggers))

        with database.connect() as conn:
            unique_indexes = {
                table: {
                    tuple(
                        column[2]
                        for column in conn.execute(f"pragma index_info('{row[1]}')")
                    )
                    for row in conn.execute(f"pragma index_list('{table}')")
                    if int(row[2]) == 1
                }
                for table in (
                    "repair_retrospectives",
                    "repair_learning_rules",
                    "repair_learning_observations",
                )
            }
        self.assertIn(("source_key",), unique_indexes["repair_retrospectives"])
        self.assertIn(("rule_key",), unique_indexes["repair_learning_rules"])
        self.assertIn(
            ("rule_id", "run_id", "attempt_id", "outcome"),
            unique_indexes["repair_learning_observations"],
        )

    def test_explicit_v70_database_migrates_to_v73_with_identical_learning_schema(self) -> None:
        path = self.root / "explicit-v70.sqlite"

        def connection_factory() -> sqlite3.Connection:
            return database.connect_database(path)

        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("create table legacy_marker(value text not null)")
            connection.execute("insert into legacy_marker values('preserved')")
            connection.execute("pragma user_version = 70")

        database.init_db(connection_factory=connection_factory)

        with connection_factory() as connection:
            version = int(connection.execute("pragma user_version").fetchone()[0])
            marker = connection.execute("select value from legacy_marker").fetchone()[0]
            tables = {
                str(row[0])
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                )
            }
            migration = connection.execute(
                """
                select from_version, to_version, migration_name
                from harness_schema_migrations where to_version = 73
                """
            ).fetchone()

        self.assertEqual(73, version)
        self.assertEqual("preserved", marker)
        self.assertTrue({
            "repair_retrospectives",
            "repair_learning_rules",
            "repair_learning_observations",
            "flux_lite_reviewer_opinions",
            "flux_lite_experience_candidates",
        }.issubset(tables))
        self.assertEqual(
            (70, 73, "v0.73-change-context-pack"),
            tuple(migration),
        )

    def test_explicit_connection_factory_rejects_unsupported_and_future_versions_without_mutation(self) -> None:
        for version in (68, 74):
            with self.subTest(version=version):
                path = self.root / f"unsupported-v{version}.sqlite"

                def connection_factory() -> sqlite3.Connection:
                    return database.connect_database(path)

                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("create table marker(value text not null)")
                    connection.execute("insert into marker values('preserved')")
                    connection.execute(f"pragma user_version = {version}")

                with self.assertRaisesRegex(
                    RuntimeError,
                    "anchored control database migration is not supported",
                ):
                    database.init_db(connection_factory=connection_factory)

                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(
                        ("preserved", version),
                        (
                            connection.execute("select value from marker").fetchone()[0],
                            int(connection.execute("pragma user_version").fetchone()[0]),
                        ),
                    )

    def test_default_v68_database_is_rejected_before_backup_or_schema_mutation(self) -> None:
        with closing(sqlite3.connect(database.DB_PATH)) as connection, connection:
            connection.execute("create table legacy_marker(value text not null)")
            connection.execute("insert into legacy_marker(value) values('keep-v67')")
            connection.execute("pragma user_version = 68")

        with self.assertRaisesRegex(
            RuntimeError,
            "anchored control database migration is not supported",
        ):
            database.init_db()

        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            version = int(connection.execute("pragma user_version").fetchone()[0])
            marker = connection.execute("select value from legacy_marker").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                )
            }
        self.assertEqual(68, version)
        self.assertEqual("keep-v67", marker)
        self.assertNotIn("repair_learning_rules", tables)
        self.assertFalse((self.root / "backups").exists())

    def test_explicit_v70_migration_failure_restores_version_schema_and_data(self) -> None:
        path = self.root / "factory-v70-failure.sqlite"

        def connection_factory() -> sqlite3.Connection:
            return database.connect_database(path)

        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("create table legacy_marker(value text not null)")
            connection.execute("insert into legacy_marker values('must-survive')")
            connection.execute("pragma user_version = 70")

        with patch("app.database.seed_defaults", side_effect=RuntimeError("injected factory failure")):
            with self.assertRaisesRegex(RuntimeError, "injected factory failure"):
                database.init_db(connection_factory=connection_factory)

        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(70, int(connection.execute("pragma user_version").fetchone()[0]))
            self.assertEqual(
                "must-survive",
                connection.execute("select value from legacy_marker").fetchone()[0],
            )
            tables = {
                row[0]
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                )
            }
        self.assertNotIn("repair_learning_rules", tables)
        self.assertNotIn("repair_learning_observations", tables)

    def test_future_schema_version_is_rejected_without_mutation(self) -> None:
        future_version = database.HARNESS_SCHEMA_VERSION + 1
        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            conn.execute(f"pragma user_version = {future_version}")
            conn.execute("create table future_marker(value text not null)")
            conn.execute("insert into future_marker(value) values('keep')")

        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            database.init_db()

        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            value = conn.execute("select value from future_marker").fetchone()[0]
            version = int(conn.execute("pragma user_version").fetchone()[0])
        self.assertEqual("keep", value)
        self.assertEqual(future_version, version)

    def test_unversioned_action_plan_table_adds_reviewed_summary_before_trigger(self) -> None:
        with closing(sqlite3.connect(database.DB_PATH)) as connection, connection:
            connection.execute(
                """
                create table manager_provider_action_plans (
                    id integer primary key autoincrement,
                    profile_id integer not null,
                    scope_type text not null,
                    scope_key text not null,
                    provider text not null,
                    profile_key text not null,
                    action_type text not null,
                    target_alias text not null,
                    parameter_hash text not null,
                    requested_by text not null,
                    confirmed_by text not null default '',
                    authorization_hash text not null default '',
                    state text not null default 'planned',
                    rejection_reason text not null default '',
                    created_at text not null,
                    confirmed_at text not null default '',
                    authorization_expires_at text not null default '',
                    consumed_at text not null default '',
                    rejected_at text not null default ''
                )
                """
            )
            connection.execute("pragma user_version = 0")

        database.init_db()

        with database.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "pragma table_info(manager_provider_action_plans)"
                )
            }
            trigger = connection.execute(
                "select name from sqlite_master where type = 'trigger' and name = ?",
                ("trg_manager_provider_action_plans_review_immutable",),
            ).fetchone()
        self.assertIn("reviewed_parameter_summary_json", columns)
        self.assertIsNotNone(trigger)

    def test_existing_database_is_backed_up_before_first_versioned_migration(self) -> None:
        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            conn.execute("create table legacy_marker(value text not null)")
            conn.execute("insert into legacy_marker(value) values('legacy')")

        database.init_db()

        backups = sorted((self.root / "backups").glob("*.sqlite"))
        self.assertEqual(1, len(backups))
        with closing(sqlite3.connect(backups[0])) as conn, conn:
            self.assertEqual("legacy", conn.execute("select value from legacy_marker").fetchone()[0])
            self.assertEqual(0, int(conn.execute("pragma user_version").fetchone()[0]))
        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            self.assertEqual("legacy", conn.execute("select value from legacy_marker").fetchone()[0])

    def test_unversioned_action_audit_safe_fields_are_preserved_during_migration(self) -> None:
        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            conn.execute(
                """
                create table manager_provider_action_audits (
                    id integer primary key autoincrement,
                    profile_id integer,
                    action_type text not null,
                    authorization_id_hash text not null default '',
                    status text not null,
                    details_json text not null default '{}',
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                insert into manager_provider_action_audits(
                    profile_id, action_type, authorization_id_hash,
                    status, details_json, created_at
                ) values(null, 'legacy.safe', ?, 'success', ?, ?)
                """,
                (
                    "sha256:" + "b" * 64,
                    '{"status":"legacy-safe"}',
                    "2026-08-09T00:00:00+00:00",
                ),
            )
            conn.execute("pragma user_version = 0")

        database.init_db()

        with database.connect() as conn:
            row = conn.execute(
                """
                select authorization_id_hash, authorization_hash,
                       details_json, result_summary_json
                from manager_provider_action_audits
                """
            ).fetchone()
        self.assertEqual(row["authorization_id_hash"], row["authorization_hash"])
        self.assertEqual(row["details_json"], row["result_summary_json"])

    def test_unversioned_action_audit_details_are_safely_rewritten_during_migration(self) -> None:
        structured_token = "LegacyOpaqueAccessToken7Qp4Lm2Nv8Bc6Zx9"
        authorization_value = "Bearer LegacyAuthorization8Mn4Vp6Lq2Xz7Ht5"
        connection_string = "postgresql://legacy-user:legacy-pass@db.example/his"
        private_key = (
            "-----BEGIN PRIVATE KEY-----\n"
            "LegacyPrivateMaterial9Rs3Wq7Yk2Mn5\n"
            "-----END PRIVATE KEY-----"
        )
        invalid_json = "Authorization: Bearer LegacyInvalidJson7Ht5Rs3Wq9Yk2"
        structured_json = json.dumps(
            {
                "accessToken": structured_token,
                "Authorization-Value": authorization_value,
                "connection": connection_string,
                "privateMaterial": private_key,
            }
        )
        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            conn.execute(
                """
                create table manager_provider_action_audits (
                    id integer primary key autoincrement,
                    profile_id integer,
                    action_type text not null,
                    authorization_id_hash text not null default '',
                    status text not null,
                    details_json text not null default '{}',
                    created_at text not null
                )
                """
            )
            conn.executemany(
                """
                insert into manager_provider_action_audits(
                    profile_id, action_type, authorization_id_hash,
                    status, details_json, created_at
                ) values(null, ?, '', 'success', ?, '2026-08-09T00:00:00+00:00')
                """,
                (
                    ("legacy.structured", structured_json),
                    ("legacy.invalid", invalid_json),
                ),
            )
            conn.execute("pragma user_version = 0")

        database.init_db()

        with database.connect() as conn:
            rows = conn.execute(
                """
                select action_type, details_json, result_summary_json
                from manager_provider_action_audits order by id
                """
            ).fetchall()
        structured = rows[0]
        invalid = rows[1]
        self.assertEqual(structured["details_json"], structured["result_summary_json"])
        for sentinel in (
            structured_token,
            authorization_value,
            connection_string,
            private_key,
            "accessToken",
            "Authorization-Value",
        ):
            self.assertNotIn(sentinel, structured["details_json"])
        self.assertIn("REDACTED", structured["details_json"])
        expected_hash = "sha256:" + hashlib.sha256(invalid_json.encode("utf-8")).hexdigest()
        self.assertEqual(invalid["details_json"], invalid["result_summary_json"])
        self.assertEqual(
            {"source_hash": expected_hash, "status": "legacy_summary_unavailable"},
            json.loads(invalid["details_json"]),
        )
        self.assertNotIn(invalid_json, invalid["details_json"])

    def test_unversioned_action_audit_metadata_is_safely_rewritten_during_migration(self) -> None:
        sensitive_values = {
            "action_type": "Authorization: Bearer LegacyAuditToken9Zx7Qp4Lm2Nv8Bc6",
            "status": "postgresql://legacy-user:legacy-pass@db.example/his",
            "target_alias": (
                "-----BEGIN PRIVATE KEY-----\n"
                "LegacyPrivateAuditMaterial8Mn4Vp6Lq2Xz7Ht5\n"
                "-----END PRIVATE KEY-----"
            ),
        }
        noncanonical_values = {
            "action_type": "Legacy Audit Action",
            "status": "SUCCESS",
            "target_alias": "legacy/model",
        }
        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            conn.execute(
                """
                create table manager_provider_action_audits (
                    id integer primary key autoincrement,
                    profile_id integer,
                    action_type text not null,
                    target_alias text not null default '',
                    authorization_id_hash text not null default '',
                    status text not null,
                    details_json text not null default '{}',
                    created_at text not null
                )
                """
            )
            conn.executemany(
                """
                insert into manager_provider_action_audits(
                    profile_id, action_type, target_alias, authorization_id_hash,
                    status, details_json, created_at
                ) values(null, ?, ?, '', ?, ?, '2026-08-09T00:00:00+00:00')
                """,
                (
                    ("legacy.safe", "model-demo", "success", '{"result":"safe"}'),
                    (
                        sensitive_values["action_type"],
                        sensitive_values["target_alias"],
                        sensitive_values["status"],
                        '{"result":"sensitive-metadata"}',
                    ),
                    (
                        noncanonical_values["action_type"],
                        noncanonical_values["target_alias"],
                        noncanonical_values["status"],
                        '{"result":"noncanonical-metadata"}',
                    ),
                ),
            )
            conn.execute("pragma user_version = 0")

        database.init_db()

        with database.connect() as conn:
            rows = conn.execute(
                """
                select action_type, status, target_alias,
                       details_json, result_summary_json
                from manager_provider_action_audits order by id
                """
            ).fetchall()

        self.assertEqual(
            ("legacy.safe", "success", "model-demo"),
            (rows[0]["action_type"], rows[0]["status"], rows[0]["target_alias"]),
        )
        self.assertEqual({"result": "safe"}, json.loads(rows[0]["details_json"]))
        placeholders = ("legacy.audit.invalid", "legacy_invalid", "legacy-invalid")
        for row, source_values in (
            (rows[1], sensitive_values),
            (rows[2], noncanonical_values),
        ):
            self.assertEqual(
                placeholders,
                (row["action_type"], row["status"], row["target_alias"]),
            )
            self.assertEqual(row["details_json"], row["result_summary_json"])
            summary = json.loads(row["details_json"])
            self.assertEqual(
                {
                    field: "sha256:"
                    + hashlib.sha256(
                        value.encode("utf-8", "surrogatepass")
                    ).hexdigest()
                    for field, value in source_values.items()
                },
                summary["metadata_source_hashes"],
            )
            persisted = "|".join(str(value) for value in row)
            for raw_value in source_values.values():
                self.assertNotIn(raw_value, persisted)

    def test_failed_migration_automatically_restores_pre_migration_database(self) -> None:
        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            conn.execute("create table legacy_marker(value text not null)")
            conn.execute("insert into legacy_marker(value) values('must-survive')")

        with patch("app.database.seed_defaults", side_effect=RuntimeError("injected migration failure")):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                database.init_db()

        with closing(sqlite3.connect(database.DB_PATH)) as conn, conn:
            self.assertEqual(0, int(conn.execute("pragma user_version").fetchone()[0]))
            self.assertEqual("must-survive", conn.execute("select value from legacy_marker").fetchone()[0])
            table_names = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        self.assertNotIn("harness_schema_migrations", table_names)

    def test_backup_and_exact_confirmation_restore_round_trip(self) -> None:
        database.init_db()
        run_id = database.create_run(
            team_key="his_requirement_workflow",
            title="before backup",
            source_type="test",
            demand_text="fixture",
            total_steps=0,
            llm_mode="mock",
        )
        backup = database.backup_database(reason="unit-test")
        database.update_run(run_id, title="after backup")

        with self.assertRaisesRegex(PermissionError, "RESTORE"):
            database.restore_database_backup(
                backup_path=Path(backup["backup_path"]),
                confirmation="",
            )

        restored = database.restore_database_backup(
            backup_path=Path(backup["backup_path"]),
            confirmation=f"RESTORE:{backup['sha256']}",
        )

        self.assertEqual("success", backup["status"])
        self.assertEqual("ok", backup["integrity_check"])
        self.assertTrue(Path(backup["manifest_path"]).is_file())
        self.assertEqual("success", restored["status"])
        self.assertEqual("before backup", database.get_run(run_id)["title"])
        health = database.database_health_snapshot()
        self.assertEqual("ok", health["integrity_check"])
        self.assertEqual(database.HARNESS_SCHEMA_VERSION, health["user_version"])

    def test_foreign_keys_and_busy_timeout_are_effective(self) -> None:
        database.init_db()
        with self.assertRaises(sqlite3.IntegrityError):
            database.add_artifact(999999, "missing-run", "invalid", "fixture")

        blocker = database.connect()
        blocker.execute("begin immediate")
        blocker.execute(
            "insert into runs(team_key, title, source_type, demand_text, status, total_steps, started_at) values(?, ?, ?, ?, ?, ?, ?)",
            ("his_requirement_workflow", "blocker", "test", "fixture", "running", 0, database.now_iso()),
        )
        outcome: dict[str, object] = {}

        def write_after_lock() -> None:
            started = time.monotonic()
            try:
                outcome["run_id"] = database.create_run(
                    team_key="his_requirement_workflow",
                    title="waited writer",
                    source_type="test",
                    demand_text="fixture",
                    total_steps=0,
                    llm_mode="mock",
                )
            except Exception as exc:  # pragma: no cover - failure detail is asserted below
                outcome["error"] = repr(exc)
            outcome["elapsed"] = time.monotonic() - started

        worker = threading.Thread(target=write_after_lock)
        worker.start()
        time.sleep(0.15)
        blocker.commit()
        blocker.close()
        worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", outcome)
        self.assertGreaterEqual(float(outcome.get("elapsed") or 0), 0.1)
        self.assertIsNotNone(database.get_run(int(outcome["run_id"])))


class DeliveryDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_delivery_transaction_and_ordered_events_round_trip(self) -> None:
        transaction_id = database.add_delivery_transaction(
            {
                "transaction_key": "delivery-fixture-1",
                "entity_kind": "requirement",
                "entity_id": "DFHIS-31557",
                "project_path": "/tmp/business-repo",
                "state": "planned",
                "plan_hash": "plan-hash",
                "policy_snapshot": {"base_branch": "release"},
                "repository_snapshot": {"head": "abc"},
                "output_dir": "/tmp/output",
                "journal_path": "/tmp/journal.json",
            }
        )
        database.add_delivery_event(
            {
                "transaction_id": transaction_id,
                "event_type": "planned",
                "status": "success",
                "input_hash": "input-a",
                "details": {"plan_hash": "plan-hash"},
            }
        )
        database.add_delivery_event(
            {
                "transaction_id": transaction_id,
                "event_type": "release_runtime_accepted",
                "status": "success",
                "input_hash": "input-b",
                "details": {"verifier": "user"},
            }
        )
        database.update_delivery_transaction(
            transaction_id,
            state="release_runtime_accepted",
            release_acceptance={"status": "passed", "head": "abc"},
            commit_records=[{"commit": "def"}],
        )

        transaction = database.get_delivery_transaction(transaction_id)
        events = database.list_delivery_events(transaction_id)

        self.assertEqual("delivery-fixture-1", transaction["transaction_key"])
        self.assertEqual({"base_branch": "release"}, transaction["policy_snapshot"])
        self.assertEqual({"head": "abc"}, transaction["repository_snapshot"])
        self.assertEqual({"status": "passed", "head": "abc"}, transaction["release_acceptance"])
        self.assertEqual([{"commit": "def"}], transaction["commit_records"])
        self.assertEqual(["planned", "release_runtime_accepted"], [item["event_type"] for item in events])
        self.assertEqual([1, 2], [item["sequence"] for item in events])

    def test_delivery_status_does_not_create_an_approval_event(self) -> None:
        transaction_id = database.add_delivery_transaction(
            {
                "transaction_key": "delivery-fixture-2",
                "entity_kind": "requirement",
                "entity_id": "DFHIS-31558",
                "project_path": "/tmp/business-repo",
                "state": "delivery_confirmed",
                "plan_hash": "plan-hash",
            }
        )

        transaction = database.get_delivery_transaction(transaction_id)
        events = database.list_delivery_events(transaction_id)

        self.assertEqual("delivery_confirmed", transaction["state"])
        self.assertEqual([], events)


if __name__ == "__main__":
    unittest.main()
