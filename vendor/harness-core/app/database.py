from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import stat
from collections import Counter
from collections.abc import Mapping
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from app.sensitive_text import redact_sensitive_mapping, validate_audit_alias


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("HARNESS_DB_PATH", DATA_DIR / "harness.sqlite"))
DEFAULT_CONFIG_PATH = BASE_DIR / "prompts" / "default_experts.json"
HARNESS_SCHEMA_VERSION = 73
SUPPORTED_MIGRATION_SOURCES = frozenset({0, 69, 70, 71, 72, HARNESS_SCHEMA_VERSION})
SQLITE_BUSY_TIMEOUT_MS = 5000


class ManagedConnection(sqlite3.Connection):
    """Commit or rollback on context exit, then close the SQLite handle."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def connect() -> sqlite3.Connection:
    return connect_database(DB_PATH)


def connect_database(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, factory=ManagedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma recursive_triggers = on")
    conn.execute("pragma foreign_keys = on")
    conn.execute(f"pragma busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("pragma synchronous = normal")
    return conn


def init_db(*, connection_factory: Callable[[], sqlite3.Connection] | None = None) -> None:
    if connection_factory is not None:
        with closing(connection_factory()) as connection:
            from_version = int(connection.execute("pragma user_version").fetchone()[0])
        if from_version not in SUPPORTED_MIGRATION_SOURCES:
            raise RuntimeError("anchored control database migration is not supported")
        snapshot: sqlite3.Connection | None = None
        if from_version < HARNESS_SCHEMA_VERSION:
            snapshot = sqlite3.connect(":memory:")
            with closing(connection_factory()) as connection:
                connection.backup(snapshot)
        try:
            _initialize_database_schema(
                from_version=from_version,
                migration_backup_path="",
                connection_factory=connection_factory,
            )
        except Exception as migration_error:
            if snapshot is not None:
                try:
                    with closing(connection_factory()) as connection:
                        snapshot.backup(connection)
                except Exception as restore_error:
                    raise RuntimeError(
                        "database migration failed and automatic factory restore also failed: "
                        f"migration={type(migration_error).__name__}; "
                        f"restore={type(restore_error).__name__}"
                    ) from restore_error
            raise
        finally:
            if snapshot is not None:
                snapshot.close()
        return
    database_existed = DB_PATH.is_file() and DB_PATH.stat().st_size > 0
    from_version = read_database_user_version(DB_PATH) if database_existed else 0
    if from_version not in SUPPORTED_MIGRATION_SOURCES:
        if from_version > HARNESS_SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {from_version} is newer than supported {HARNESS_SCHEMA_VERSION}"
            )
        raise RuntimeError("anchored control database migration is not supported")
    migration_backup: dict = {}
    if database_existed and from_version < HARNESS_SCHEMA_VERSION:
        migration_backup = backup_database(reason=f"pre-migration-v{from_version}-to-v{HARNESS_SCHEMA_VERSION}")
        if migration_backup.get("status") != "success":
            raise RuntimeError("database migration backup failed")
    try:
        _initialize_database_schema(
            from_version=from_version,
            migration_backup_path=str(migration_backup.get("backup_path") or ""),
        )
    except Exception as migration_error:
        if migration_backup:
            try:
                restore_database_backup(
                    backup_path=Path(str(migration_backup["backup_path"])),
                    confirmation=f"RESTORE:{migration_backup['sha256']}",
                    target_path=DB_PATH,
                )
            except Exception as restore_error:
                raise RuntimeError(
                    "database migration failed and automatic pre-migration restore also failed: "
                    f"migration={type(migration_error).__name__}; restore={type(restore_error).__name__}"
                ) from restore_error
        raise


def _initialize_database_schema(
    *,
    from_version: int,
    migration_backup_path: str,
    connection_factory: Callable[[], sqlite3.Connection] | None = None,
) -> None:
    factory = connect if connection_factory is None else connection_factory
    with closing(factory()) as conn, conn:
        if connection_factory is None:
            journal_mode = str(conn.execute("pragma journal_mode = wal").fetchone()[0]).lower()
            if journal_mode != "wal":
                raise RuntimeError(f"failed to enable sqlite WAL mode: {journal_mode}")
        conn.executescript(
            """
            create table if not exists experts (
                id integer primary key autoincrement,
                key text not null unique,
                name text not null,
                role text not null,
                description text not null default '',
                prompt text not null,
                tags text not null default '',
                model text not null default '',
                enabled integer not null default 1,
                created_at text not null
            );

            create table if not exists teams (
                id integer primary key autoincrement,
                key text not null unique,
                name text not null,
                description text not null default '',
                enabled integer not null default 1,
                created_at text not null
            );

            create table if not exists workflow_steps (
                id integer primary key autoincrement,
                team_key text not null,
                step_order integer not null,
                step_key text not null,
                step_name text not null,
                expert_key text not null,
                mode text not null default 'deep',
                timeout_seconds integer not null default 3600,
                retry_count integer not null default 0,
                stop_on_failure integer not null default 1,
                unique(team_key, step_key)
            );

            create table if not exists runs (
                id integer primary key autoincrement,
                team_key text not null,
                title text not null,
                source_type text not null default 'manual',
                demand_text text not null,
                status text not null,
                llm_mode text not null default '',
                llm_model text not null default '',
                evaluation_status text not null default '',
                evaluation_summary text not null default '',
                retry_rounds integer not null default 0,
                current_step integer not null default 0,
                total_steps integer not null default 0,
                error text not null default '',
                started_at text not null,
                finished_at text
            );

            create table if not exists step_runs (
                id integer primary key autoincrement,
                run_id integer not null,
                step_order integer not null,
                step_key text not null,
                step_name text not null,
                expert_key text not null,
                expert_name text not null,
                attempt_round integer not null default 0,
                review_feedback text not null default '',
                status text not null,
                input_text text not null,
                output_text text not null default '',
                error text not null default '',
                duration_ms integer not null default 0,
                prompt_tokens integer not null default 0,
                completion_tokens integer not null default 0,
                started_at text not null,
                finished_at text,
                foreign key(run_id) references runs(id)
            );

            create table if not exists artifacts (
                id integer primary key autoincrement,
                run_id integer not null,
                kind text not null,
                title text not null,
                content text not null,
                created_at text not null,
                foreign key(run_id) references runs(id)
            );

            create table if not exists yunxiao_audit_events (
                id integer primary key autoincrement,
                run_id integer,
                project_key text not null,
                entity_kind text not null,
                entity_id text not null,
                entity_title text not null default '',
                entity_url text not null default '',
                action text not null,
                status text not null,
                decision text not null,
                idempotency_key text not null unique,
                actor text not null,
                reason text not null default '',
                before_state text not null default '{}',
                after_state text not null default '{}',
                payload text not null default '{}',
                evidence_ids text not null default '[]',
                risk_level text not null default '',
                model_mode text not null default '',
                model_name text not null default '',
                runtime_mode text not null default '',
                real_write_status text not null default '',
                executed_at text not null default '',
                external_request_id text not null default '',
                external_response text not null default '{}',
                verification_status text not null default '',
                error text not null default '',
                created_at text not null,
                foreign key(run_id) references runs(id)
            );

            create index if not exists idx_yunxiao_audit_run_id
            on yunxiao_audit_events(run_id);

            create index if not exists idx_yunxiao_audit_entity
            on yunxiao_audit_events(project_key, entity_kind, entity_id);

            create table if not exists harness_tasks (
                id integer primary key autoincrement,
                task_key text not null unique,
                entity_kind text not null default '',
                entity_id text not null default '',
                entity_title text not null default '',
                entity_url text not null default '',
                source_type text not null default 'manual',
                current_stage text not null default 'created',
                status text not null default 'created',
                risk_level text not null default '',
                project_root text not null default '',
                project_paths text not null default '[]',
                base_branch text not null default '',
                work_branch text not null default '',
                latest_run_id integer,
                latest_output_dir text not null default '',
                latest_artifacts text not null default '{}',
                verification_status text not null default '',
                failure_stage text not null default '',
                recovery_action text not null default '',
                retryable integer not null default 0,
                can_commit integer not null default 0,
                can_yunxiao_transition integer not null default 0,
                notes text not null default '',
                metadata text not null default '{}',
                created_at text not null,
                updated_at text not null
            );

            create index if not exists idx_harness_tasks_entity
            on harness_tasks(entity_kind, entity_id);

            create table if not exists harness_task_runs (
                id integer primary key autoincrement,
                task_id integer not null,
                run_id integer,
                stage text not null default '',
                execution_mode text not null default '',
                status text not null default '',
                evaluation_status text not null default '',
                output_dir text not null default '',
                summary text not null default '',
                verification_status text not null default '',
                failure_stage text not null default '',
                recovery_action text not null default '',
                retryable integer not null default 0,
                artifact_paths text not null default '{}',
                started_at text not null,
                finished_at text,
                foreign key(task_id) references harness_tasks(id),
                foreign key(run_id) references runs(id)
            );

            create index if not exists idx_harness_task_runs_task_id
            on harness_task_runs(task_id);

            create table if not exists harness_task_changes (
                id integer primary key autoincrement,
                task_id integer not null,
                task_run_id integer,
                run_id integer,
                change_sequence integer not null,
                change_id text not null unique,
                source_type text not null default '',
                status text not null default '',
                project_path text not null default '',
                allowed_paths text not null default '[]',
                diff_path text not null default '',
                diff_summary text not null default '',
                diff_sha256 text not null default '',
                verification_status text not null default '',
                rollback_mode text not null default 'dry_run_only',
                rollback_status text not null default 'available',
                notes text not null default '',
                metadata text not null default '{}',
                created_at text not null,
                foreign key(task_id) references harness_tasks(id),
                foreign key(task_run_id) references harness_task_runs(id),
                foreign key(run_id) references runs(id)
            );

            create index if not exists idx_harness_task_changes_task_id
            on harness_task_changes(task_id, change_sequence);

            create table if not exists harness_schema_meta (
                key text primary key,
                value text not null,
                updated_at text not null
            );

            create table if not exists harness_schema_migrations (
                id integer primary key autoincrement,
                from_version integer not null,
                to_version integer not null unique,
                migration_name text not null,
                backup_path text not null default '',
                applied_at text not null
            );

            create table if not exists harness_retention_events (
                id integer primary key autoincrement,
                plan_hash text not null unique,
                status text not null,
                policy text not null,
                candidate_run_ids text not null default '[]',
                deleted_run_count integer not null default 0,
                deleted_artifact_count integer not null default 0,
                archive_backup text not null default '{}',
                database_size_before integer not null default 0,
                database_size_after integer not null default 0,
                created_at text not null
            );

            create table if not exists harness_dynamic_plans (
                id integer primary key autoincrement,
                task_id integer not null,
                plan_hash text not null,
                schema_version text not null,
                status text not null,
                complexity_level text not null,
                total_score integer not null default 0,
                plan_payload text not null,
                supersedes_plan_id integer,
                superseded_by_plan_id integer,
                created_at text not null,
                unique(task_id, plan_hash),
                foreign key(task_id) references harness_tasks(id),
                foreign key(supersedes_plan_id) references harness_dynamic_plans(id),
                foreign key(superseded_by_plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_dynamic_plans_task
            on harness_dynamic_plans(task_id, id);

            create table if not exists harness_dynamic_subtasks (
                id integer primary key autoincrement,
                plan_id integer not null,
                task_id integer not null,
                node_id text not null,
                title text not null,
                node_kind text not null,
                role_id text not null,
                status text not null default 'planned',
                output_contract text not null,
                allowed_paths text not null default '[]',
                parallel_group text not null default '',
                human_confirmation_required integer not null default 0,
                metadata text not null default '{}',
                created_at text not null,
                updated_at text not null,
                unique(plan_id, node_id),
                foreign key(plan_id) references harness_dynamic_plans(id),
                foreign key(task_id) references harness_tasks(id)
            );

            create index if not exists idx_harness_dynamic_subtasks_plan
            on harness_dynamic_subtasks(plan_id, id);

            create table if not exists harness_dynamic_edges (
                id integer primary key autoincrement,
                plan_id integer not null,
                source_node_id text not null,
                target_node_id text not null,
                dependency_type text not null,
                artifact_schema text not null,
                reason text not null default '',
                created_at text not null,
                unique(plan_id, source_node_id, target_node_id, dependency_type),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_dynamic_edges_plan
            on harness_dynamic_edges(plan_id, id);

            create table if not exists harness_contract_artifacts (
                id integer primary key autoincrement,
                plan_id integer not null,
                task_id integer not null,
                node_id text not null,
                artifact_id text not null unique,
                artifact_version integer not null,
                schema_name text not null,
                schema_version text not null,
                producer text not null,
                input_artifact_ids text not null default '[]',
                content_hash text not null,
                status text not null,
                payload text not null default '{}',
                supersedes_artifact_id text not null default '',
                created_at text not null,
                unique(plan_id, node_id, artifact_version),
                foreign key(plan_id) references harness_dynamic_plans(id),
                foreign key(task_id) references harness_tasks(id)
            );

            create index if not exists idx_harness_contract_artifacts_plan_node
            on harness_contract_artifacts(plan_id, node_id, artifact_version);

            create table if not exists harness_dynamic_audit_events (
                id integer primary key autoincrement,
                task_id integer not null,
                plan_id integer,
                node_id text not null default '',
                action text not null,
                status text not null,
                details text not null default '{}',
                created_at text not null,
                foreign key(task_id) references harness_tasks(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_dynamic_audit_plan
            on harness_dynamic_audit_events(plan_id, id);

            create table if not exists harness_dynamic_schedules (
                id integer primary key autoincrement,
                plan_id integer not null,
                task_id integer not null,
                mode text not null default 'dry_run',
                status text not null default 'active',
                tick integer not null default 0,
                policy_snapshot text not null default '{}',
                created_at text not null,
                updated_at text not null,
                foreign key(plan_id) references harness_dynamic_plans(id),
                foreign key(task_id) references harness_tasks(id)
            );

            create index if not exists idx_harness_dynamic_schedules_plan
            on harness_dynamic_schedules(plan_id, id);

            create table if not exists harness_dynamic_node_states (
                id integer primary key autoincrement,
                schedule_id integer not null,
                plan_id integer not null,
                node_id text not null,
                role_id text not null,
                state text not null,
                attempt_count integer not null default 0,
                max_retries integer not null default 0,
                input_budget_tokens integer not null default 0,
                output_budget_tokens integer not null default 0,
                timeout_seconds integer not null default 0,
                parallel_allowed integer not null default 1,
                human_only integer not null default 0,
                last_event_id text not null default '',
                last_decision text not null default '{}',
                created_at text not null,
                updated_at text not null,
                unique(schedule_id, node_id),
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_dynamic_node_states_schedule
            on harness_dynamic_node_states(schedule_id, id);

            create table if not exists harness_dynamic_schedule_events (
                id integer primary key autoincrement,
                schedule_id integer not null,
                event_key text not null,
                event_type text not null,
                node_id text not null default '',
                payload text not null default '{}',
                decision text not null default '{}',
                created_at text not null,
                unique(schedule_id, event_key),
                foreign key(schedule_id) references harness_dynamic_schedules(id)
            );

            create index if not exists idx_harness_dynamic_schedule_events_schedule
            on harness_dynamic_schedule_events(schedule_id, id);

            create table if not exists harness_dynamic_checkpoints (
                id integer primary key autoincrement,
                schedule_id integer not null,
                tick integer not null,
                checkpoint_hash text not null,
                payload text not null,
                created_at text not null,
                unique(schedule_id, tick),
                foreign key(schedule_id) references harness_dynamic_schedules(id)
            );

            create index if not exists idx_harness_dynamic_checkpoints_schedule
            on harness_dynamic_checkpoints(schedule_id, tick);

            create table if not exists harness_dynamic_context_envelopes (
                id integer primary key autoincrement,
                schedule_id integer not null,
                plan_id integer not null,
                node_id text not null,
                role_id text not null,
                checkpoint_hash text not null,
                plan_hash text not null,
                envelope_hash text not null unique,
                status text not null default 'current',
                requested_tools text not null default '[]',
                tool_decisions text not null default '[]',
                payload text not null default '{}',
                created_at text not null,
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_dynamic_context_envelopes_schedule
            on harness_dynamic_context_envelopes(schedule_id, node_id, id);

            create table if not exists harness_capability_leases (
                id integer primary key autoincrement,
                context_id integer not null,
                schedule_id integer not null,
                plan_id integer not null,
                node_id text not null,
                context_hash text not null,
                checkpoint_hash text not null,
                lease_key text not null unique,
                adapter_kind text not null,
                capabilities text not null default '[]',
                policy_hash text not null,
                issued_at text not null,
                expires_at text not null,
                max_uses integer not null default 1,
                use_count integer not null default 0,
                status text not null default 'issued',
                created_at text not null,
                updated_at text not null,
                foreign key(context_id) references harness_dynamic_context_envelopes(id),
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_capability_leases_context
            on harness_capability_leases(context_id, id);

            create table if not exists harness_dynamic_node_executions (
                id integer primary key autoincrement,
                context_id integer not null,
                schedule_id integer not null,
                plan_id integer not null,
                node_id text not null,
                execution_key text not null unique,
                executor_kind text not null default 'fixture_json',
                status text not null,
                fixture_relpath text not null default '',
                fixture_digest text not null default '',
                requested_tools text not null default '[]',
                tool_decisions text not null default '[]',
                candidate_schema text not null default '',
                candidate_hash text not null default '',
                candidate_payload text not null default '{}',
                error_code text not null default '',
                created_at text not null,
                foreign key(context_id) references harness_dynamic_context_envelopes(id),
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_dynamic_node_executions_context
            on harness_dynamic_node_executions(context_id, id);

            create table if not exists harness_mock_agent_runs (
                id integer primary key autoincrement,
                schedule_id integer not null unique,
                plan_id integer not null,
                run_key text not null unique,
                adapter_kind text not null,
                status text not null default 'running',
                max_parallel integer not null default 1,
                started_at text not null,
                completed_at text not null default '',
                summary text not null default '{}',
                created_at text not null,
                updated_at text not null,
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_mock_agent_runs_plan
            on harness_mock_agent_runs(plan_id, id);

            create table if not exists harness_mock_agent_traces (
                id integer primary key autoincrement,
                run_id integer not null,
                schedule_id integer not null,
                plan_id integer not null,
                wave_index integer not null,
                trace_id text not null unique,
                node_id text not null,
                role_id text not null,
                context_id integer not null,
                lease_id integer not null,
                execution_id integer not null,
                status text not null,
                error_code text not null default '',
                candidate_hash text not null default '',
                input_artifact_ids text not null default '[]',
                input_tokens integer not null default 0,
                output_tokens integer not null default 0,
                elapsed_ms integer not null default 0,
                observed_concurrency integer not null default 1,
                parallel_observed integer not null default 0,
                started_at text not null,
                finished_at text not null,
                details text not null default '{}',
                created_at text not null,
                foreign key(run_id) references harness_mock_agent_runs(id),
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_mock_agent_traces_run
            on harness_mock_agent_traces(run_id, wave_index, id);

            create table if not exists harness_model_invocations (
                id integer primary key autoincrement,
                context_id integer not null,
                schedule_id integer not null,
                plan_id integer not null,
                node_id text not null,
                role_id text not null,
                invocation_key text not null unique,
                request_hash text not null,
                mode text not null,
                provider text not null default '',
                model text not null default '',
                status text not null,
                request_payload text not null default '{}',
                response_payload text not null default '{}',
                response_hash text not null default '',
                candidate_payload text not null default '{}',
                candidate_hash text not null default '',
                usage text not null default '{}',
                error_code text not null default '',
                cassette_relpath text not null default '',
                cassette_digest text not null default '',
                started_at text not null,
                completed_at text not null default '',
                created_at text not null,
                updated_at text not null,
                foreign key(context_id) references harness_dynamic_context_envelopes(id),
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_model_invocations_context
            on harness_model_invocations(context_id, id);

            create table if not exists harness_model_invocation_events (
                id integer primary key autoincrement,
                invocation_id integer not null,
                sequence integer not null,
                event_type text not null,
                status text not null,
                details text not null default '{}',
                created_at text not null,
                unique(invocation_id, sequence),
                foreign key(invocation_id) references harness_model_invocations(id)
            );

            create index if not exists idx_harness_model_invocation_events_invocation
            on harness_model_invocation_events(invocation_id, sequence);

            create table if not exists harness_model_dag_runs (
                id integer primary key autoincrement,
                schedule_id integer not null unique,
                plan_id integer not null,
                run_key text not null unique,
                status text not null default 'running',
                max_parallel integer not null default 1,
                adapter_policy text not null default '{}',
                started_at text not null,
                completed_at text not null default '',
                summary text not null default '{}',
                created_at text not null,
                updated_at text not null,
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id)
            );

            create index if not exists idx_harness_model_dag_runs_plan
            on harness_model_dag_runs(plan_id, id);

            create table if not exists harness_model_dag_traces (
                id integer primary key autoincrement,
                run_id integer not null,
                schedule_id integer not null,
                plan_id integer not null,
                wave_index integer not null,
                trace_id text not null unique,
                node_id text not null,
                role_id text not null,
                context_id integer not null,
                invocation_id integer not null,
                mode text not null,
                provider text not null default '',
                model text not null default '',
                status text not null,
                error_code text not null default '',
                request_hash text not null default '',
                response_hash text not null default '',
                candidate_hash text not null default '',
                cassette_relpath text not null default '',
                input_tokens integer not null default 0,
                output_tokens integer not null default 0,
                elapsed_ms integer not null default 0,
                observed_concurrency integer not null default 1,
                parallel_observed integer not null default 0,
                started_at text not null,
                finished_at text not null,
                details text not null default '{}',
                created_at text not null,
                foreign key(run_id) references harness_model_dag_runs(id),
                foreign key(schedule_id) references harness_dynamic_schedules(id),
                foreign key(plan_id) references harness_dynamic_plans(id),
                foreign key(invocation_id) references harness_model_invocations(id)
            );

            create index if not exists idx_harness_model_dag_traces_run
            on harness_model_dag_traces(run_id, wave_index, id);

            create table if not exists harness_model_provider_smokes (
                id integer primary key autoincrement,
                smoke_key text not null unique,
                profile_key text not null,
                provider_kind text not null,
                endpoint_host text not null,
                model text not null,
                status text not null,
                transport_status text not null default '',
                protocol_status text not null default '',
                marker_status text not null default '',
                authorization_hash text not null,
                credential_key_names text not null default '{}',
                request_hash text not null,
                response_hash text not null default '',
                usage text not null default '{}',
                timeout_seconds integer not null,
                error_code text not null default '',
                error_detail text not null default '',
                started_at text not null,
                completed_at text not null default '',
                created_at text not null,
                updated_at text not null
            );

            create index if not exists idx_harness_model_provider_smokes_profile
            on harness_model_provider_smokes(profile_key, id);

            create table if not exists harness_model_provider_smoke_events (
                id integer primary key autoincrement,
                smoke_id integer not null,
                sequence integer not null,
                event_type text not null,
                status text not null,
                details text not null default '{}',
                created_at text not null,
                unique(smoke_id, sequence),
                foreign key(smoke_id) references harness_model_provider_smokes(id)
            );

            create index if not exists idx_harness_model_provider_smoke_events_smoke
            on harness_model_provider_smoke_events(smoke_id, sequence);

            create table if not exists harness_delivery_transactions (
                id integer primary key autoincrement,
                transaction_key text not null unique,
                task_id integer,
                source_run_id integer,
                entity_kind text not null default '',
                entity_id text not null default '',
                project_path text not null,
                state text not null,
                plan_hash text not null,
                policy_snapshot text not null default '{}',
                repository_snapshot text not null default '{}',
                release_acceptance text not null default '{}',
                rc_acceptance text not null default '{}',
                commit_records text not null default '[]',
                remote_results text not null default '[]',
                parity_result text not null default '{}',
                output_dir text not null default '',
                journal_path text not null default '',
                last_error text not null default '',
                created_at text not null,
                updated_at text not null,
                foreign key(task_id) references harness_tasks(id),
                foreign key(source_run_id) references runs(id)
            );

            create index if not exists idx_harness_delivery_transactions_task
            on harness_delivery_transactions(task_id, id);

            create index if not exists idx_harness_delivery_transactions_entity
            on harness_delivery_transactions(entity_kind, entity_id, id);

            create table if not exists harness_delivery_events (
                id integer primary key autoincrement,
                transaction_id integer not null,
                sequence integer not null,
                event_type text not null,
                status text not null,
                input_hash text not null default '',
                details text not null default '{}',
                created_at text not null,
                unique(transaction_id, sequence),
                foreign key(transaction_id) references harness_delivery_transactions(id)
            );

            create index if not exists idx_harness_delivery_events_transaction
            on harness_delivery_events(transaction_id, sequence);

            create table if not exists manager_provider_scopes (
                id integer primary key autoincrement,
                scope_type text not null check(scope_type in ('local', 'team', 'project', 'user')),
                scope_key text not null,
                display_name text not null default '',
                created_at text not null,
                unique(scope_type, scope_key)
            );

            create table if not exists manager_provider_profiles (
                id integer primary key autoincrement,
                scope_id integer not null,
                provider text not null,
                profile_key text not null,
                display_name text not null default '',
                enabled integer not null default 1 check(enabled in (0, 1)),
                connection_json text not null default '{}',
                created_at text not null,
                updated_at text not null,
                unique(scope_id, provider, profile_key),
                foreign key(scope_id) references manager_provider_scopes(id)
            );

            create table if not exists manager_provider_credentials (
                id integer primary key autoincrement,
                profile_id integer not null,
                credential_field text not null,
                cipher_version text not null,
                ciphertext text not null,
                created_at text not null,
                updated_at text not null,
                unique(profile_id, credential_field),
                foreign key(profile_id) references manager_provider_profiles(id)
            );

            create table if not exists manager_provider_action_plans (
                id integer primary key autoincrement,
                profile_id integer not null,
                scope_type text not null,
                scope_key text not null,
                provider text not null,
                profile_key text not null,
                action_type text not null,
                target_alias text not null,
                parameter_hash text not null,
                reviewed_parameter_summary_json text not null default '{}',
                requested_by text not null,
                confirmed_by text not null default '',
                authorization_hash text not null default '',
                state text not null default 'planned'
                    check(state in ('planned', 'confirmed', 'consumed', 'expired', 'rejected')),
                rejection_reason text not null default '',
                created_at text not null,
                confirmed_at text not null default '',
                authorization_expires_at text not null default '',
                consumed_at text not null default '',
                rejected_at text not null default '',
                foreign key(profile_id) references manager_provider_profiles(id)
            );

            create index if not exists idx_manager_provider_action_plans_profile_state
            on manager_provider_action_plans(profile_id, state, id);

            create index if not exists idx_manager_provider_action_plans_expiry
            on manager_provider_action_plans(authorization_expires_at, state, id);

            create table if not exists manager_provider_action_audits (
                id integer primary key autoincrement,
                action_plan_id integer,
                profile_id integer,
                action_type text not null,
                target_alias text not null default '',
                parameter_hash text not null default '',
                authorization_hash text not null default '',
                authorization_id_hash text not null default '',
                status text not null,
                result_summary_json text not null default '{}',
                details_json text not null default '{}',
                created_at text not null,
                foreign key(profile_id) references manager_provider_profiles(id),
                foreign key(action_plan_id) references manager_provider_action_plans(id)
            );

            create trigger if not exists trg_manager_provider_action_plans_created_at_immutable
            before update of created_at on manager_provider_action_plans
            for each row when new.created_at != old.created_at
            begin
                select raise(abort, 'manager provider action plan created_at is immutable');
            end;

            create trigger if not exists trg_manager_provider_action_audits_created_at_immutable
            before update of created_at on manager_provider_action_audits
            for each row when new.created_at != old.created_at
            begin
                select raise(abort, 'manager provider action audit created_at is immutable');
            end;

            create table if not exists manager_knowledge_consultations (
                id integer primary key autoincrement,
                scope_id integer not null,
                query_redacted text not null,
                query_hash text not null,
                retrieval_status text not null,
                citations_json text not null default '[]',
                model_used integer not null default 0 check(model_used in (0, 1)),
                created_at text not null,
                foreign key(scope_id) references manager_provider_scopes(id)
            );

            create table if not exists manager_provider_imports (
                id integer primary key autoincrement,
                source_sha256 text not null unique,
                imported_count integer not null,
                status text not null,
                created_at text not null
            );

            create table if not exists manager_learning_candidates (
                id integer primary key autoincrement,
                candidate_key text not null unique,
                candidate_type text not null,
                source_action_audit_id integer,
                evidence_hash text not null,
                safe_summary_json text not null default '{}',
                state text not null default 'candidate'
                    check(state in ('candidate', 'approved', 'rejected', 'promoted', 'expired')),
                reviewer_alias text not null default '',
                created_at text not null,
                reviewed_at text not null default '',
                promoted_at text not null default '',
                expires_at text not null default '',
                foreign key(source_action_audit_id) references manager_provider_action_audits(id)
            );

            create index if not exists idx_manager_learning_candidates_state_expiry
            on manager_learning_candidates(state, expires_at, id);

            create unique index if not exists ux_manager_learning_candidates_source_audit_type
            on manager_learning_candidates(source_action_audit_id, candidate_type)
            where source_action_audit_id is not null;

            create table if not exists manager_business_acceptance_evidence (
                id integer primary key autoincrement,
                evidence_key text not null,
                evidence_version integer not null default 1,
                scope_type text not null,
                scope_key text not null,
                environment_alias text not null,
                operator_alias text not null,
                test_data_alias text not null,
                technical_result text not null,
                evidence_hash text not null,
                evidence_json text not null default '{}',
                business_valid integer not null default 0 check(business_valid in (0, 1)),
                created_at text not null,
                unique(evidence_key, evidence_version)
            );

            create index if not exists idx_manager_business_acceptance_scope_created
            on manager_business_acceptance_evidence(scope_type, scope_key, created_at, id);

            create table if not exists manager_business_acceptance_decisions (
                id integer primary key autoincrement,
                evidence_id integer not null,
                reviewer_alias text not null,
                decision text not null check(decision in ('accept', 'reject')),
                reason_redacted text not null,
                created_at text not null,
                foreign key(evidence_id) references manager_business_acceptance_evidence(id)
            );

            create index if not exists idx_manager_business_acceptance_decisions_evidence
            on manager_business_acceptance_decisions(evidence_id, id);

            create trigger if not exists trg_manager_business_acceptance_evidence_created_at_immutable
            before update of created_at on manager_business_acceptance_evidence
            for each row when new.created_at != old.created_at
            begin
                select raise(abort, 'manager business acceptance evidence created_at is immutable');
            end;

            create trigger if not exists trg_manager_business_acceptance_evidence_append_only_update
            before update on manager_business_acceptance_evidence
            begin
                select raise(abort, 'manager business acceptance evidence is append only');
            end;

            create trigger if not exists trg_manager_business_acceptance_evidence_append_only_delete
            before delete on manager_business_acceptance_evidence
            begin
                select raise(abort, 'manager business acceptance evidence is append only');
            end;

            create trigger if not exists trg_manager_business_acceptance_decisions_append_only_update
            before update on manager_business_acceptance_decisions
            begin
                select raise(abort, 'manager business acceptance decisions are append only');
            end;

            create trigger if not exists trg_manager_business_acceptance_decisions_append_only_delete
            before delete on manager_business_acceptance_decisions
            begin
                select raise(abort, 'manager business acceptance decisions are append only');
            end;

            create table if not exists manager_task_intent_sessions (
                conversation_key text primary key,
                mode text not null check(mode in ('question', 'task')),
                reason_codes_json text not null default '[]',
                confidence text not null check(confidence in ('high', 'conservative')),
                sticky integer not null default 0 check(sticky in (0, 1)),
                linked_work_item text not null default '',
                yunxiao_status text not null
                    check(yunxiao_status in ('linked', 'unlinked', 'not_applicable', 'lookup_failed')),
                current_phase text not null
                    check(current_phase in ('knowledge_retrieval', 'requirement_intake')),
                next_route text not null
                    check(next_route in ('knowledge', 'requirement_workflow')),
                last_event_id integer not null default 0,
                created_at text not null,
                updated_at text not null
            );

            create table if not exists manager_task_intent_events (
                id integer primary key autoincrement,
                conversation_key text not null,
                event_type text not null
                    check(event_type in ('decision', 'explicit_correction')),
                previous_mode text not null default ''
                    check(previous_mode in ('', 'question', 'task')),
                mode text not null check(mode in ('question', 'task')),
                reason_codes_json text not null default '[]',
                confidence text not null check(confidence in ('high', 'conservative')),
                sticky integer not null default 0 check(sticky in (0, 1)),
                linked_work_item text not null default '',
                yunxiao_status text not null
                    check(yunxiao_status in ('linked', 'unlinked', 'not_applicable', 'lookup_failed')),
                current_phase text not null
                    check(current_phase in ('knowledge_retrieval', 'requirement_intake')),
                next_route text not null
                    check(next_route in ('knowledge', 'requirement_workflow')),
                mutation_requested integer not null default 0
                    check(mutation_requested in (0, 1)),
                message_summary text not null,
                message_sha256 text not null,
                created_at text not null
            );

            create index if not exists idx_manager_task_intent_events_conversation
            on manager_task_intent_events(conversation_key, id);

            create trigger if not exists trg_manager_task_intent_events_append_only_update
            before update on manager_task_intent_events
            begin
                select raise(abort, 'manager task intent events are append only');
            end;

            create trigger if not exists trg_manager_task_intent_events_append_only_delete
            before delete on manager_task_intent_events
            begin
                select raise(abort, 'manager task intent events are append only');
            end;

            create table if not exists local_agent_runs (
                id integer primary key autoincrement,
                task_key text not null,
                contract_hash text not null unique,
                authorization_hash text not null unique,
                project_identity_json text not null,
                initial_head text not null,
                worktree_path text not null default '',
                status text not null check(status in (
                    'created', 'workspace_ready', 'worker_running', 'verifying',
                    'reviewing', 'awaiting_human_confirmation', 'locally_applied',
                    'interrupted', 'failed_scope', 'failed_worker', 'cancelled',
                    'failed_verification', 'changes_requested', 'failed_review',
                    'confirmation_expired', 'failed_workspace', 'attempts_exhausted'
                )),
                summary_json text not null default '{}',
                created_at text not null,
                updated_at text not null
            );

            create table if not exists local_agent_attempts (
                id integer primary key autoincrement,
                run_id integer not null references local_agent_runs(id),
                attempt_no integer not null check(attempt_no > 0),
                status text not null check(status in (
                    'starting', 'worker_running', 'completed', 'failed_scope',
                    'failed_worker', 'cancelled', 'interrupted'
                )),
                worker_pid integer,
                worker_start_identity text not null default '',
                error_code text not null default '',
                started_at text not null,
                finished_at text,
                unique(run_id, attempt_no)
            );

            create table if not exists local_agent_run_events (
                id integer primary key autoincrement,
                run_id integer not null references local_agent_runs(id),
                attempt_id integer references local_agent_attempts(id),
                sequence_no integer not null check(sequence_no > 0),
                event_type text not null,
                payload_json text not null,
                created_at text not null,
                unique(run_id, sequence_no)
            );

            create table if not exists local_agent_artifacts (
                id integer primary key autoincrement,
                run_id integer not null references local_agent_runs(id),
                attempt_id integer references local_agent_attempts(id),
                kind text not null,
                relative_path text not null,
                sha256 text not null,
                size_bytes integer not null check(size_bytes >= 0),
                created_at text not null,
                unique(run_id, kind, relative_path)
            );

            create table if not exists code_evidence_bundles (
                id integer primary key autoincrement,
                bundle_key text not null unique,
                conversation_key text not null,
                task_key text not null,
                repository_alias text not null,
                repository_identity_sha256 text not null,
                head_sha text not null,
                snapshot_sha256 text not null,
                required_capabilities_json text not null,
                status text not null default 'collecting'
                    check(status in ('collecting', 'sealed', 'reviewed', 'invalid')),
                seal_sha256 text not null default '',
                created_at text not null,
                sealed_at text not null default '',
                reviewed_at text not null default ''
            );

            create table if not exists code_evidence_artifacts (
                id integer primary key autoincrement,
                bundle_id integer not null references code_evidence_bundles(id),
                kind text not null,
                relative_path text not null,
                sha256 text not null,
                size_bytes integer not null check(size_bytes >= 0),
                device integer not null check(device >= 0),
                inode integer not null check(inode > 0),
                mode integer not null check(mode >= 0),
                link_count integer not null check(link_count = 1),
                created_at text not null,
                unique(bundle_id, kind, relative_path)
            );

            create table if not exists code_evidence_events (
                id integer primary key autoincrement,
                bundle_id integer not null references code_evidence_bundles(id),
                sequence_no integer not null check(sequence_no > 0),
                event_type text not null,
                status text not null,
                details_json text not null,
                created_at text not null,
                unique(bundle_id, sequence_no)
            );

            create table if not exists code_evidence_reviews (
                id integer primary key autoincrement,
                bundle_id integer not null unique references code_evidence_bundles(id),
                verdict text not null check(verdict in ('approved', 'changes_requested')),
                review_sha256 text not null,
                evidence_seal_sha256 text not null,
                findings_json text not null,
                created_at text not null
            );

            create table if not exists code_evidence_sets (
                id integer primary key autoincrement,
                set_key text not null unique,
                conversation_key text not null,
                required_repository_count integer not null
                    check(required_repository_count > 0),
                status text not null default 'collecting'
                    check(status in ('collecting', 'sealed', 'invalid')),
                seal_sha256 text not null default '',
                created_at text not null,
                sealed_at text not null default ''
            );

            create table if not exists code_evidence_set_members (
                id integer primary key autoincrement,
                evidence_set_id integer not null references code_evidence_sets(id),
                repository_alias text not null,
                bundle_id integer not null unique references code_evidence_bundles(id),
                ordinal integer not null check(ordinal > 0),
                created_at text not null,
                unique(evidence_set_id, repository_alias),
                unique(evidence_set_id, ordinal)
            );

            create index if not exists idx_code_evidence_bundles_status_created
            on code_evidence_bundles(status, created_at, id);

            create index if not exists idx_code_evidence_artifacts_bundle
            on code_evidence_artifacts(bundle_id, id);

            create index if not exists idx_code_evidence_events_bundle_sequence
            on code_evidence_events(bundle_id, sequence_no);

            create index if not exists idx_code_evidence_set_members_set_ordinal
            on code_evidence_set_members(evidence_set_id, ordinal);

            create trigger if not exists trg_code_evidence_bundles_no_delete
            before delete on code_evidence_bundles
            begin
                select raise(abort, 'code evidence bundles are durable');
            end;

            create trigger if not exists trg_code_evidence_bundles_guarded_update
            before update on code_evidence_bundles
            when old.bundle_key != new.bundle_key
              or old.conversation_key != new.conversation_key
              or old.task_key != new.task_key
              or old.repository_alias != new.repository_alias
              or old.repository_identity_sha256 != new.repository_identity_sha256
              or old.head_sha != new.head_sha
              or old.snapshot_sha256 != new.snapshot_sha256
              or old.required_capabilities_json != new.required_capabilities_json
              or old.created_at != new.created_at
              or old.status not in ('collecting', 'sealed')
              or (old.status = 'collecting' and new.status not in ('sealed', 'invalid'))
              or (old.status = 'sealed' and new.status not in ('reviewed', 'invalid'))
              or (new.status = 'sealed' and (new.seal_sha256 = '' or new.sealed_at = ''))
              or (new.status = 'reviewed' and (new.seal_sha256 = '' or new.sealed_at = '' or new.reviewed_at = ''))
              or (old.seal_sha256 != '' and old.seal_sha256 != new.seal_sha256)
              or (old.sealed_at != '' and old.sealed_at != new.sealed_at)
              or (old.reviewed_at != '' and old.reviewed_at != new.reviewed_at)
            begin
                select raise(abort, 'code evidence bundle transition is invalid');
            end;

            create trigger if not exists trg_code_evidence_artifacts_append_only_update
            before update on code_evidence_artifacts
            begin select raise(abort, 'code evidence artifacts are append only'); end;
            create trigger if not exists trg_code_evidence_artifacts_append_only_delete
            before delete on code_evidence_artifacts
            begin select raise(abort, 'code evidence artifacts are append only'); end;
            create trigger if not exists trg_code_evidence_artifacts_append_only_collision
            before insert on code_evidence_artifacts
            when exists(
                select 1 from code_evidence_artifacts existing
                where (new.id is not null and new.id != -1 and existing.id = new.id)
                   or (existing.bundle_id = new.bundle_id and existing.kind = new.kind
                       and existing.relative_path = new.relative_path)
            )
            begin select raise(abort, 'code evidence artifacts are append only'); end;

            create trigger if not exists trg_code_evidence_events_append_only_update
            before update on code_evidence_events
            begin select raise(abort, 'code evidence events are append only'); end;
            create trigger if not exists trg_code_evidence_events_append_only_delete
            before delete on code_evidence_events
            begin select raise(abort, 'code evidence events are append only'); end;
            create trigger if not exists trg_code_evidence_events_append_only_collision
            before insert on code_evidence_events
            when exists(
                select 1 from code_evidence_events existing
                where (new.id is not null and new.id != -1 and existing.id = new.id)
                   or (existing.bundle_id = new.bundle_id and existing.sequence_no = new.sequence_no)
            )
            begin select raise(abort, 'code evidence events are append only'); end;

            create trigger if not exists trg_code_evidence_reviews_append_only_update
            before update on code_evidence_reviews
            begin select raise(abort, 'code evidence reviews are append only'); end;
            create trigger if not exists trg_code_evidence_reviews_append_only_delete
            before delete on code_evidence_reviews
            begin select raise(abort, 'code evidence reviews are append only'); end;
            create trigger if not exists trg_code_evidence_reviews_append_only_collision
            before insert on code_evidence_reviews
            when exists(
                select 1 from code_evidence_reviews existing
                where (new.id is not null and new.id != -1 and existing.id = new.id)
                   or existing.bundle_id = new.bundle_id
            )
            begin select raise(abort, 'code evidence reviews are append only'); end;

            create trigger if not exists trg_code_evidence_sets_no_delete
            before delete on code_evidence_sets
            begin select raise(abort, 'code evidence sets are durable'); end;
            create trigger if not exists trg_code_evidence_sets_guarded_update
            before update on code_evidence_sets
            when old.set_key != new.set_key
              or old.conversation_key != new.conversation_key
              or old.required_repository_count != new.required_repository_count
              or old.created_at != new.created_at
              or old.status != 'collecting'
              or new.status not in ('sealed', 'invalid')
              or (new.status = 'sealed' and (new.seal_sha256 = '' or new.sealed_at = ''))
            begin select raise(abort, 'code evidence set transition is invalid'); end;
            create trigger if not exists trg_code_evidence_set_members_append_only_update
            before update on code_evidence_set_members
            begin select raise(abort, 'code evidence set members are append only'); end;
            create trigger if not exists trg_code_evidence_set_members_append_only_delete
            before delete on code_evidence_set_members
            begin select raise(abort, 'code evidence set members are append only'); end;
            create trigger if not exists trg_code_evidence_set_members_append_only_collision
            before insert on code_evidence_set_members
            when exists(
                select 1 from code_evidence_set_members existing
                where (new.id is not null and new.id != -1 and existing.id = new.id)
                   or existing.bundle_id = new.bundle_id
                   or (existing.evidence_set_id = new.evidence_set_id
                       and (existing.repository_alias = new.repository_alias or existing.ordinal = new.ordinal))
            )
            begin select raise(abort, 'code evidence set members are append only'); end;

            -- Stage F tables remain additive and idempotent under schema v70;
            -- tables are intentionally created during every v69 open so a
            -- database migrated from v68 receives the durable control plane.
            create table if not exists local_agent_workspace_bindings (
                run_id integer primary key references local_agent_runs(id),
                binding_json text not null,
                created_at text not null
            );

            create table if not exists local_agent_workspace_binding_events (
                id integer primary key autoincrement,
                run_id integer not null references local_agent_runs(id),
                binding_json text not null,
                created_at text not null
            );

            create table if not exists local_agent_project_leases (
                project_identity_json text primary key,
                run_id integer not null unique references local_agent_runs(id),
                created_at text not null
            );

            create table if not exists local_agent_apply_confirmations (
                run_id integer primary key references local_agent_runs(id),
                attempt_id integer not null references local_agent_attempts(id),
                token_hash text not null unique,
                requested_by text not null,
                binding_json text not null,
                issued_at text not null,
                expires_at text not null,
                status text not null check(status in ('issued', 'consumed', 'expired')),
                consumed_at text
            );

            create table if not exists local_agent_apply_operations (
                run_id integer primary key references local_agent_runs(id),
                attempt_id integer not null references local_agent_attempts(id),
                operation_id text not null unique,
                token_hash text not null references local_agent_apply_confirmations(token_hash),
                facts_json text not null,
                journal_application_id text,
                status text not null check(status in ('applying', 'recovery_required', 'completed')),
                created_at text not null,
                updated_at text not null
            );

            create trigger if not exists trg_local_agent_apply_operations_no_delete
            before delete on local_agent_apply_operations
            begin
                select raise(abort, 'local agent apply operations are durable');
            end;

            create trigger if not exists trg_local_agent_apply_operations_immutable
            before update on local_agent_apply_operations
            when old.run_id != new.run_id
              or old.attempt_id != new.attempt_id
              or old.operation_id != new.operation_id
              or old.token_hash != new.token_hash
              or old.facts_json != new.facts_json
              or old.created_at != new.created_at
              or old.status = 'completed'
              or new.status not in ('applying', 'recovery_required', 'completed')
              or (old.journal_application_id is not null and old.journal_application_id != new.journal_application_id)
            begin
                select raise(abort, 'local agent apply operation is immutable');
            end;

            create trigger if not exists trg_local_agent_apply_confirmations_no_delete
            before delete on local_agent_apply_confirmations
            begin
                select raise(abort, 'local agent apply confirmations are durable');
            end;

            create trigger if not exists trg_local_agent_apply_confirmations_immutable
            before update on local_agent_apply_confirmations
            when old.run_id != new.run_id
              or old.attempt_id != new.attempt_id
              or old.token_hash != new.token_hash
              or old.requested_by != new.requested_by
              or old.binding_json != new.binding_json
              or old.issued_at != new.issued_at
              or old.expires_at != new.expires_at
              or old.status != 'issued'
              or new.status not in ('consumed', 'expired')
              or new.consumed_at is null
            begin
                select raise(abort, 'local agent apply confirmation is immutable');
            end;

            create trigger if not exists trg_local_agent_run_events_append_only_update
            before update on local_agent_run_events
            begin
                select raise(abort, 'local agent run events are append only');
            end;

            create trigger if not exists trg_local_agent_run_events_append_only_insert_collision
            before insert on local_agent_run_events
            when exists(
                select 1 from local_agent_run_events as existing
                where (new.id is not null and new.id != -1 and existing.id = new.id)
                   or (existing.run_id = new.run_id and existing.sequence_no = new.sequence_no)
            )
            begin
                select raise(abort, 'local agent run events are append only');
            end;

            create trigger if not exists trg_local_agent_run_events_append_only_delete
            before delete on local_agent_run_events
            begin
                select raise(abort, 'local agent run events are append only');
            end;

            create trigger if not exists trg_local_agent_artifacts_append_only_update
            before update on local_agent_artifacts
            begin
                select raise(abort, 'local agent artifacts are append only');
            end;

            create trigger if not exists trg_local_agent_artifacts_append_only_insert_collision
            before insert on local_agent_artifacts
            when exists(
                select 1 from local_agent_artifacts as existing
                where (new.id is not null and new.id != -1 and existing.id = new.id)
                   or (
                       existing.run_id = new.run_id
                       and existing.kind = new.kind
                       and existing.relative_path = new.relative_path
                   )
            )
            begin
                select raise(abort, 'local agent artifacts are append only');
            end;

            create trigger if not exists trg_local_agent_artifacts_append_only_delete
            before delete on local_agent_artifacts
            begin
                select raise(abort, 'local agent artifacts are append only');
            end;

            create index if not exists idx_local_agent_attempts_status_id
            on local_agent_attempts(status, id);

            create unique index if not exists ux_local_agent_attempts_one_active_per_run
            on local_agent_attempts(run_id)
            where status in ('starting', 'worker_running');

            create table if not exists repair_retrospectives (
                id integer primary key autoincrement,
                source_key text not null unique,
                run_id integer not null,
                attempt_id integer not null,
                source_kind text not null,
                root_cause_kind text not null,
                safe_summary_json text not null,
                task_context_json text not null,
                created_at text not null
            );

            create table if not exists repair_learning_rules (
                id integer primary key autoincrement,
                rule_key text not null unique,
                rule_json text not null,
                state text not null check(state in (
                    'draft', 'active_current_task', 'trial', 'stable',
                    'suspended', 'retired'
                )),
                origin_retrospective_id integer not null
                    references repair_retrospectives(id),
                active_run_id integer,
                verified_task_count integer not null default 0 check(verified_task_count >= 0),
                distinct_workspace_count integer not null default 0 check(distinct_workspace_count >= 0),
                counterexample_count integer not null default 0 check(counterexample_count >= 0),
                state_version integer not null default 0 check(state_version >= 0),
                created_at text not null,
                updated_at text not null,
                suspended_at text
            );

            create table if not exists repair_learning_observations (
                id integer primary key autoincrement,
                rule_id integer not null references repair_learning_rules(id),
                run_id integer not null,
                attempt_id integer not null,
                task_key text not null,
                workspace_fingerprint text not null,
                outcome text not null check(outcome in ('matched', 'not_matched')),
                evidence_json text not null,
                observed_at text not null,
                unique(rule_id, run_id, attempt_id, outcome)
            );

            create table if not exists flux_lite_reviewer_opinions (
                id integer primary key autoincrement,
                opinion_key text not null unique,
                run_id integer not null,
                attempt_id integer not null,
                reviewer_id text not null,
                scope_key text not null,
                root_cause text not null,
                focus_actions_json text not null,
                verdict text not null check(verdict in ('approved', 'changes_requested', 'blocked')),
                evidence_refs_json text not null,
                created_at text not null,
                unique(run_id, attempt_id, reviewer_id, scope_key)
            );

            create table if not exists flux_lite_experience_candidates (
                id integer primary key autoincrement,
                candidate_key text not null unique,
                candidate_id text not null,
                run_id integer not null,
                attempt_id integer not null,
                scope_key text not null,
                root_cause text not null,
                focus_actions_json text not null,
                reviewer_count integer not null check(reviewer_count > 0),
                agreement_ratio real not null check(agreement_ratio >= 0 and agreement_ratio <= 1),
                conflict_score real not null check(conflict_score >= 0 and conflict_score <= 1),
                context_weight real not null check(context_weight >= 0 and context_weight <= 1),
                state text not null check(state in ('candidate', 'trial', 'stable', 'suspended', 'retired')),
                promotion_allowed integer not null check(promotion_allowed in (0, 1)),
                high_risk integer not null check(high_risk in (0, 1)),
                opinion_keys_json text not null,
                created_at text not null,
                unique(run_id, attempt_id, candidate_id)
            );

            create index if not exists idx_flux_lite_opinions_attempt
            on flux_lite_reviewer_opinions(run_id, attempt_id, scope_key, id);

            create index if not exists idx_flux_lite_candidates_context
            on flux_lite_experience_candidates(scope_key, state, promotion_allowed, high_risk, id);

            create table if not exists change_context_layers (
                layer_id text primary key,
                schema_version text not null,
                layer_type text not null check(layer_type in ('project_graph', 'change_scope', 'code_graph', 'data_graph')),
                status text not null check(status in ('complete', 'incomplete', 'not_applicable', 'stale')),
                content_hash text not null,
                source_fingerprint text not null,
                artifact_ref text not null,
                evidence_refs_json text not null,
                policy_rule_ids_json text not null,
                blockers_json text not null,
                created_at text not null
            );

            create table if not exists change_context_layer_artifacts (
                content_hash text primary key,
                artifact_ref text not null unique,
                relative_path text not null unique,
                size_bytes integer not null check(size_bytes >= 0),
                device integer not null,
                inode integer not null,
                mode integer not null,
                link_count integer not null check(link_count = 1),
                created_at text not null
            );

            create table if not exists change_context_packs (
                pack_id text primary key,
                schema_version text not null,
                pack_version integer not null check(pack_version > 0),
                status text not null check(status in ('collecting', 'ready', 'blocked', 'stale', 'superseded')),
                provider text not null,
                ticket_id text not null,
                requirement_revision text not null,
                request_hash text not null,
                required_layers_json text not null,
                supersedes_pack_id text unique,
                created_at text not null,
                foreign key(supersedes_pack_id) references change_context_packs(pack_id)
            );

            create index if not exists idx_change_context_packs_task
            on change_context_packs(provider, ticket_id, pack_version, created_at);

            create table if not exists change_context_pack_layers (
                pack_id text not null,
                ordinal integer not null check(ordinal >= 0 and ordinal < 4),
                layer_type text not null,
                layer_id text not null,
                content_hash text not null,
                primary key(pack_id, layer_type),
                unique(pack_id, ordinal),
                foreign key(pack_id) references change_context_packs(pack_id),
                foreign key(layer_id) references change_context_layers(layer_id),
                foreign key(content_hash) references change_context_layer_artifacts(content_hash)
            );

            create table if not exists change_context_applicability_decisions (
                id integer primary key autoincrement,
                pack_id text not null,
                layer_type text not null,
                requirement text not null check(requirement in ('required', 'not_applicable')),
                rule_ids_json text not null,
                evidence_refs_json text not null,
                reasons_json text not null,
                created_at text not null,
                unique(pack_id, layer_type),
                foreign key(pack_id) references change_context_packs(pack_id)
            );

            create table if not exists change_context_gate_results (
                pack_id text primary key,
                status text not null check(status in ('ready', 'blocked')),
                code text not null,
                missing_json text not null,
                conflicts_json text not null,
                blockers_json text not null,
                created_at text not null,
                foreign key(pack_id) references change_context_packs(pack_id)
            );

            create table if not exists change_context_events (
                id integer primary key autoincrement,
                pack_id text not null,
                event_type text not null,
                payload_hash text not null,
                payload_json text not null,
                created_at text not null,
                foreign key(pack_id) references change_context_packs(pack_id)
            );

            create index if not exists idx_change_context_events_pack
            on change_context_events(pack_id, id);

            create table if not exists change_context_projection_metrics (
                id integer primary key autoincrement,
                pack_id text not null,
                role text not null,
                projection_hash text not null,
                raw_bytes integer not null check(raw_bytes >= 0),
                projected_bytes integer not null check(projected_bytes >= 0),
                reused_layer_count integer not null check(reused_layer_count >= 0),
                recollected_layer_count integer not null check(recollected_layer_count >= 0),
                evidence_refs_opened integer not null check(evidence_refs_opened >= 0),
                reported_model_tokens integer not null check(reported_model_tokens >= 0),
                created_at text not null,
                unique(pack_id, role, projection_hash),
                foreign key(pack_id) references change_context_packs(pack_id)
            );

            create trigger if not exists trg_change_context_layers_no_update before update on change_context_layers begin select raise(abort, 'change context layers are append only'); end;
            create trigger if not exists trg_change_context_layers_no_delete before delete on change_context_layers begin select raise(abort, 'change context layers are append only'); end;
            create trigger if not exists trg_change_context_artifacts_no_update before update on change_context_layer_artifacts begin select raise(abort, 'change context artifacts are append only'); end;
            create trigger if not exists trg_change_context_artifacts_no_delete before delete on change_context_layer_artifacts begin select raise(abort, 'change context artifacts are append only'); end;
            create trigger if not exists trg_change_context_packs_no_update before update on change_context_packs begin select raise(abort, 'change context packs are append only'); end;
            create trigger if not exists trg_change_context_packs_no_delete before delete on change_context_packs begin select raise(abort, 'change context packs are append only'); end;
            create trigger if not exists trg_change_context_pack_layers_no_update before update on change_context_pack_layers begin select raise(abort, 'change context pack layers are append only'); end;
            create trigger if not exists trg_change_context_pack_layers_no_delete before delete on change_context_pack_layers begin select raise(abort, 'change context pack layers are append only'); end;
            create trigger if not exists trg_change_context_applicability_no_update before update on change_context_applicability_decisions begin select raise(abort, 'change context applicability is append only'); end;
            create trigger if not exists trg_change_context_applicability_no_delete before delete on change_context_applicability_decisions begin select raise(abort, 'change context applicability is append only'); end;
            create trigger if not exists trg_change_context_gate_no_update before update on change_context_gate_results begin select raise(abort, 'change context gate results are append only'); end;
            create trigger if not exists trg_change_context_gate_no_delete before delete on change_context_gate_results begin select raise(abort, 'change context gate results are append only'); end;
            create trigger if not exists trg_change_context_events_no_update before update on change_context_events begin select raise(abort, 'change context events are append only'); end;
            create trigger if not exists trg_change_context_events_no_delete before delete on change_context_events begin select raise(abort, 'change context events are append only'); end;
            create trigger if not exists trg_change_context_projection_metrics_no_update before update on change_context_projection_metrics begin select raise(abort, 'change context projection metrics are append only'); end;
            create trigger if not exists trg_change_context_projection_metrics_no_delete before delete on change_context_projection_metrics begin select raise(abort, 'change context projection metrics are append only'); end;

            create trigger if not exists trg_flux_lite_reviewer_opinions_append_only_update
            before update on flux_lite_reviewer_opinions
            begin select raise(abort, 'flux lite reviewer opinions are append only'); end;

            create trigger if not exists trg_flux_lite_reviewer_opinions_append_only_delete
            before delete on flux_lite_reviewer_opinions
            begin select raise(abort, 'flux lite reviewer opinions are append only'); end;

            create trigger if not exists trg_flux_lite_candidates_append_only_update
            before update on flux_lite_experience_candidates
            begin select raise(abort, 'flux lite experience candidates are append only'); end;

            create trigger if not exists trg_flux_lite_candidates_append_only_delete
            before delete on flux_lite_experience_candidates
            begin select raise(abort, 'flux lite experience candidates are append only'); end;

            create index if not exists idx_repair_retrospectives_run
            on repair_retrospectives(run_id, attempt_id, id);

            create index if not exists idx_repair_learning_rules_state
            on repair_learning_rules(state, active_run_id, id);

            create index if not exists idx_repair_learning_observations_rule
            on repair_learning_observations(rule_id, observed_at, id);
            """
        )
        ensure_column(conn, "runs", "llm_mode", "text not null default ''")
        ensure_column(conn, "runs", "llm_model", "text not null default ''")
        ensure_column(conn, "runs", "evaluation_status", "text not null default ''")
        ensure_column(conn, "runs", "evaluation_summary", "text not null default ''")
        ensure_column(conn, "runs", "retry_rounds", "integer not null default 0")
        ensure_column(conn, "step_runs", "attempt_round", "integer not null default 0")
        ensure_column(conn, "step_runs", "review_feedback", "text not null default ''")
        ensure_column(conn, "yunxiao_audit_events", "runtime_mode", "text not null default ''")
        ensure_column(conn, "yunxiao_audit_events", "real_write_status", "text not null default ''")
        ensure_column(conn, "yunxiao_audit_events", "executed_at", "text not null default ''")
        ensure_column(conn, "yunxiao_audit_events", "external_request_id", "text not null default ''")
        ensure_column(conn, "yunxiao_audit_events", "external_response", "text not null default '{}'")
        ensure_column(conn, "yunxiao_audit_events", "verification_status", "text not null default ''")
        ensure_column(conn, "harness_tasks", "latest_artifacts", "text not null default '{}'")
        ensure_column(conn, "harness_tasks", "verification_status", "text not null default ''")
        ensure_column(conn, "harness_tasks", "failure_stage", "text not null default ''")
        ensure_column(conn, "harness_tasks", "recovery_action", "text not null default ''")
        ensure_column(conn, "harness_tasks", "retryable", "integer not null default 0")
        ensure_column(conn, "harness_task_runs", "failure_stage", "text not null default ''")
        ensure_column(conn, "harness_task_runs", "recovery_action", "text not null default ''")
        ensure_column(conn, "harness_task_runs", "retryable", "integer not null default 0")
        ensure_column(conn, "harness_dynamic_node_executions", "lease_id", "integer not null default 0")
        ensure_column(conn, "harness_dynamic_node_executions", "runtime_details", "text not null default '{}'")
        ensure_column(conn, "harness_model_provider_smokes", "transport_status", "text not null default ''")
        ensure_column(conn, "harness_model_provider_smokes", "protocol_status", "text not null default ''")
        ensure_column(conn, "harness_model_provider_smokes", "marker_status", "text not null default ''")
        ensure_column(conn, "manager_provider_action_audits", "action_plan_id", "integer")
        ensure_column(conn, "manager_provider_action_audits", "target_alias", "text not null default ''")
        ensure_column(conn, "manager_provider_action_audits", "parameter_hash", "text not null default ''")
        ensure_column(conn, "manager_provider_action_audits", "authorization_hash", "text not null default ''")
        ensure_column(conn, "manager_provider_action_audits", "result_summary_json", "text not null default '{}'")
        ensure_column(
            conn,
            "manager_task_intent_events",
            "mutation_requested",
            "integer not null default 0 check(mutation_requested in (0, 1))",
        )
        ensure_column(
            conn,
            "manager_provider_action_plans",
            "reviewed_parameter_summary_json",
            "text not null default '{}'",
        )
        conn.executescript(
            """
            create trigger if not exists trg_manager_provider_action_plans_review_immutable
            before update of profile_id, scope_type, scope_key, provider, profile_key,
                             action_type, target_alias, parameter_hash,
                             reviewed_parameter_summary_json, requested_by
            on manager_provider_action_plans
            for each row when
                new.profile_id != old.profile_id
                or new.scope_type != old.scope_type
                or new.scope_key != old.scope_key
                or new.provider != old.provider
                or new.profile_key != old.profile_key
                or new.action_type != old.action_type
                or new.target_alias != old.target_alias
                or new.parameter_hash != old.parameter_hash
                or new.reviewed_parameter_summary_json != old.reviewed_parameter_summary_json
                or new.requested_by != old.requested_by
            begin
                select raise(abort, 'manager provider action plan review is immutable');
            end;
            """
        )
        if from_version < 64:
            _sanitize_legacy_action_audits(conn)
        conn.execute(
            """
            create index if not exists idx_manager_provider_action_audits_profile_created
            on manager_provider_action_audits(profile_id, created_at, id)
            """
        )
        conn.execute(
            """
            create index if not exists idx_manager_provider_action_audits_plan_created
            on manager_provider_action_audits(action_plan_id, created_at, id)
            """
        )
        conn.execute(
            """
            insert or ignore into harness_schema_migrations(
                from_version, to_version, migration_name, backup_path, applied_at
            ) values(?, ?, ?, ?, ?)
            """,
            (
                from_version,
                HARNESS_SCHEMA_VERSION,
                "v0.73-change-context-pack",
                migration_backup_path,
                now_iso(),
            ),
        )
        conn.execute(f"pragma user_version = {HARNESS_SCHEMA_VERSION}")
        conn.execute(
            """
            insert into harness_schema_meta(key, value, updated_at)
            values('dynamic_plan_registry', '1.0-dynamic-plan-registry', ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso(),),
        )
        conn.execute(
            """
            insert into harness_schema_meta(key, value, updated_at)
            values('dynamic_dry_run_scheduler', '1.0-dynamic-dry-run-scheduler', ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso(),),
        )
        conn.execute(
            """
            insert into harness_schema_meta(key, value, updated_at)
            values('controlled_node_runtime', '1.0-controlled-node-runtime', ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso(),),
        )
        conn.execute(
            """
            insert into harness_schema_meta(key, value, updated_at)
            values('sandbox_executor_runtime', '1.0-sandbox-executor-runtime', ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso(),),
        )
        conn.execute(
            """
            insert into harness_schema_meta(key, value, updated_at)
            values('mock_agent_runtime', '1.0-deterministic-mock-agent-runtime', ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso(),),
        )
        conn.execute(
            """
            insert into harness_schema_meta(key, value, updated_at)
            values('model_invocation_runtime', '1.0-provider-neutral-offline-model-runtime', ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso(),),
        )
        conn.execute(
            """
            insert into harness_schema_meta(key, value, updated_at)
            values('model_dag_runtime', '1.0-offline-model-dag-runtime', ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso(),),
        )
        conn.execute(
            """
            insert into harness_schema_meta(key, value, updated_at)
            values('model_provider_runtime', '1.1-controlled-model-provider-smoke-layers', ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso(),),
        )
    seed_defaults(connection_factory=factory)


def read_database_user_version(path: Path) -> int:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return 0
    # sqlite3.Connection's transaction context manager does not close the
    # connection.  This helper is called by every Manager repository startup,
    # so explicit closing prevents a transient migration check from leaking a
    # read-only file descriptor into controlled Provider execution.
    with closing(sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=5.0)) as conn:
        return int(conn.execute("pragma user_version").fetchone()[0])


def backup_database(
    *,
    reason: str = "manual",
    source_path: Path | None = None,
    destination_dir: Path | None = None,
) -> dict:
    source = (source_path or DB_PATH).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Harness database does not exist: {source}")
    target_dir = (destination_dir or source.parent / "backups").expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_reason = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in reason).strip("-") or "manual"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = target_dir / f"{source.stem}-{timestamp}-v{read_database_user_version(source)}-{safe_reason}.sqlite"
    temporary_path = backup_path.with_suffix(".sqlite.tmp")
    try:
        with closing(
            sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
        ) as source_conn:
            with closing(sqlite3.connect(temporary_path, timeout=5.0)) as backup_conn:
                source_conn.backup(backup_conn)
                integrity = str(backup_conn.execute("pragma integrity_check").fetchone()[0])
                if integrity.lower() != "ok":
                    raise RuntimeError(f"backup integrity_check failed: {integrity}")
        os.replace(temporary_path, backup_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    sha256 = sha256_file(backup_path)
    manifest = {
        "schema_version": "1.0-harness-database-backup",
        "status": "success",
        "source_path": str(source),
        "backup_path": str(backup_path),
        "reason": reason,
        "database_user_version": read_database_user_version(backup_path),
        "integrity_check": "ok",
        "sha256": sha256,
        "size_bytes": backup_path.stat().st_size,
        "created_at": now_iso(),
    }
    manifest_path = backup_path.with_suffix(".manifest.json")
    atomic_write_database_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def restore_database_backup(
    *,
    backup_path: Path,
    confirmation: str,
    target_path: Path | None = None,
) -> dict:
    backup = backup_path.expanduser().resolve()
    target = (target_path or DB_PATH).expanduser().resolve()
    if not backup.is_file():
        raise FileNotFoundError(f"Harness database backup does not exist: {backup}")
    integrity = sqlite_integrity_check(backup)
    if integrity.lower() != "ok":
        raise RuntimeError(f"backup integrity_check failed: {integrity}")
    sha256 = sha256_file(backup)
    expected_confirmation = f"RESTORE:{sha256}"
    if confirmation != expected_confirmation:
        raise PermissionError(f"database restore requires exact confirmation: {expected_confirmation}")
    safety_backup = None
    if target.is_file() and target.stat().st_size > 0:
        safety_backup = backup_database(
            reason="pre-restore",
            source_path=target,
            destination_dir=target.parent / "backups",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target.with_name(f".{target.name}.{os.getpid()}.restore.tmp")
    try:
        with closing(
            sqlite3.connect(f"file:{backup}?mode=ro", uri=True, timeout=5.0)
        ) as source_conn:
            with closing(sqlite3.connect(temporary_path, timeout=5.0)) as target_conn:
                source_conn.backup(target_conn)
                restored_integrity = str(target_conn.execute("pragma integrity_check").fetchone()[0])
                if restored_integrity.lower() != "ok":
                    raise RuntimeError(f"restored database integrity_check failed: {restored_integrity}")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(target) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return {
        "schema_version": "1.0-harness-database-restore",
        "status": "success",
        "backup_path": str(backup),
        "target_path": str(target),
        "backup_sha256": sha256,
        "integrity_check": sqlite_integrity_check(target),
        "safety_backup": safety_backup or {},
        "restored_at": now_iso(),
        "remote_actions": "disabled",
    }


def database_health_snapshot(path: Path | None = None) -> dict:
    target = (path or DB_PATH).expanduser().resolve()
    if not target.is_file():
        return {"status": "missing", "path": str(target)}
    with closing(
        sqlite3.connect(target, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    ) as conn:
        conn.execute("pragma foreign_keys = on")
        conn.execute(f"pragma busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        integrity = str(conn.execute("pragma integrity_check").fetchone()[0])
        journal_mode = str(conn.execute("pragma journal_mode").fetchone()[0]).lower()
        foreign_keys = int(conn.execute("pragma foreign_keys").fetchone()[0])
        busy_timeout = int(conn.execute("pragma busy_timeout").fetchone()[0])
        user_version = int(conn.execute("pragma user_version").fetchone()[0])
        table_count = int(
            conn.execute("select count(*) from sqlite_master where type = 'table'").fetchone()[0]
        )
    return {
        "status": "healthy" if integrity.lower() == "ok" else "unhealthy",
        "path": str(target),
        "integrity_check": integrity,
        "journal_mode": journal_mode,
        "foreign_keys": foreign_keys,
        "busy_timeout_ms": busy_timeout,
        "user_version": user_version,
        "supported_user_version": HARNESS_SCHEMA_VERSION,
        "table_count": table_count,
        "size_bytes": target.stat().st_size,
    }


def database_read_only_health_snapshot(path: Path | None = None) -> dict:
    """Return a side-effect-free Core status probe without following WAL state."""
    configured_target = (path or DB_PATH).expanduser()
    target = configured_target if configured_target.is_absolute() else Path.cwd() / configured_target
    try:
        target_stat = target.lstat()
    except FileNotFoundError:
        return {"status": "missing"}
    except OSError:
        return {"status": "unavailable"}
    if not stat.S_ISREG(target_stat.st_mode):
        return {"status": "unavailable"}

    for suffix in ("-wal", "-shm"):
        try:
            Path(str(target) + suffix).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return {"status": "unavailable"}
        return {
            "status": "unknown",
            "probe": "metadata_only",
            "integrity_check": "not_run",
            "freshness": "unknown",
            "reason": "wal_sidecars_present",
        }

    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        try:
            opened_stat = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or (opened_stat.st_dev, opened_stat.st_ino) != (target_stat.st_dev, target_stat.st_ino)
        ):
            return {"status": "unavailable"}
        # immutable=1 intentionally ignores WAL state; sidecars are rejected above.
        conn = sqlite3.connect(
            f"{target.as_uri()}?mode=ro&immutable=1",
            uri=True,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        )
        try:
            integrity = str(conn.execute("pragma integrity_check").fetchone()[0])
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return {"status": "unavailable"}
    return {
        "status": "healthy" if integrity.lower() == "ok" else "unhealthy",
        "probe": "sqlite_immutable",
        "integrity_check": integrity,
        "integrity_scope": "main_file_only",
        "freshness": "checkpointed_snapshot",
    }


def build_retention_plan(
    *,
    keep_days: int,
    keep_recent_runs: int,
    as_of: datetime | None = None,
    database_path: Path | None = None,
) -> dict:
    if keep_days < 1:
        raise ValueError("keep_days must be at least 1")
    if keep_recent_runs < 0:
        raise ValueError("keep_recent_runs cannot be negative")
    target = (database_path or DB_PATH).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Harness database does not exist: {target}")
    active_now = normalize_utc_datetime(as_of or datetime.now(timezone.utc))
    with connect_database(target) as conn:
        plan = _build_retention_plan_from_conn(
            conn,
            target=target,
            keep_days=keep_days,
            keep_recent_runs=keep_recent_runs,
            as_of=active_now,
        )
    plan_hash = retention_plan_hash(plan)
    plan["plan_hash"] = plan_hash
    plan["required_confirmation"] = f"PRUNE:{plan_hash}"
    return plan


def apply_retention_plan(plan: dict, *, confirmation: str) -> dict:
    plan_hash = str(plan.get("plan_hash") or "")
    if not plan_hash or retention_plan_hash(plan) != plan_hash:
        raise ValueError("retention plan hash is missing or invalid")
    expected_confirmation = f"PRUNE:{plan_hash}"
    if confirmation != expected_confirmation:
        raise PermissionError(f"retention apply requires exact confirmation: {expected_confirmation}")
    target = Path(str(plan.get("database_path") or "")).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Harness database does not exist: {target}")
    as_of = normalize_utc_datetime(datetime.fromisoformat(str(plan.get("as_of") or "")))
    keep_days = int(plan.get("keep_days"))
    keep_recent_runs = int(plan.get("keep_recent_runs"))
    size_before = target.stat().st_size
    archive: dict = {}
    candidate_ids = [int(run_id) for run_id in plan.get("candidate_run_ids") or []]

    with connect_database(target) as conn:
        conn.execute("begin immediate")
        fresh = _build_retention_plan_from_conn(
            conn,
            target=target,
            keep_days=keep_days,
            keep_recent_runs=keep_recent_runs,
            as_of=as_of,
        )
        if retention_plan_hash(fresh) != plan_hash:
            raise RuntimeError(
                "retention plan drift detected; database candidates changed, generate a new preview before pruning"
            )
        archive = backup_database(
            reason=f"pre-retention-{plan_hash[:12]}",
            source_path=target,
            destination_dir=target.parent / "retention_archives",
        )
        deleted_artifacts = 0
        deleted_steps = 0
        deleted_runs = 0
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            conn.execute(f"delete from artifacts where run_id in ({placeholders})", candidate_ids)
            deleted_artifacts = int(conn.execute("select changes() as count").fetchone()["count"])
            conn.execute(f"delete from step_runs where run_id in ({placeholders})", candidate_ids)
            deleted_steps = int(conn.execute("select changes() as count").fetchone()["count"])
            conn.execute(f"delete from runs where id in ({placeholders})", candidate_ids)
            deleted_runs = int(conn.execute("select changes() as count").fetchone()["count"])
        if deleted_runs != len(candidate_ids):
            raise RuntimeError(
                f"retention delete count mismatch: expected {len(candidate_ids)}, deleted {deleted_runs}"
            )
        conn.execute(
            """
            insert into harness_retention_events(
                plan_hash, status, policy, candidate_run_ids, deleted_run_count,
                deleted_artifact_count, archive_backup, database_size_before,
                database_size_after, created_at
            ) values(?, 'success', ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                plan_hash,
                json.dumps(
                    {"keep_days": keep_days, "keep_recent_runs": keep_recent_runs, "as_of": as_of.isoformat()},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json.dumps(candidate_ids),
                deleted_runs,
                deleted_artifacts,
                json.dumps(archive, ensure_ascii=False, sort_keys=True),
                size_before,
                now_iso(),
            ),
        )

    with connect_database(target) as conn:
        conn.execute("vacuum")
    size_after = target.stat().st_size
    with connect_database(target) as conn:
        conn.execute(
            "update harness_retention_events set database_size_after = ? where plan_hash = ?",
            (size_after, plan_hash),
        )
    return {
        "schema_version": "1.0-harness-retention-result",
        "status": "success",
        "plan_hash": plan_hash,
        "database_path": str(target),
        "deleted_run_count": deleted_runs,
        "deleted_artifact_count": deleted_artifacts,
        "deleted_step_run_count": deleted_steps,
        "archive_backup": archive,
        "database_size_before": size_before,
        "database_size_after": size_after,
        "integrity_check": sqlite_integrity_check(target),
        "remote_actions": "disabled",
    }


def _build_retention_plan_from_conn(
    conn: sqlite3.Connection,
    *,
    target: Path,
    keep_days: int,
    keep_recent_runs: int,
    as_of: datetime,
) -> dict:
    cutoff = as_of - timedelta(days=keep_days)
    recent_ids = (
        {
            int(row["id"])
            for row in conn.execute(
                "select id from runs order by id desc limit ?",
                (keep_recent_runs,),
            ).fetchall()
        }
        if keep_recent_runs
        else set()
    )
    task_or_change_ids = {
        int(row["run_id"])
        for row in conn.execute(
            """
            select run_id from harness_task_runs where run_id is not null
            union select run_id from harness_task_changes where run_id is not null
            union select latest_run_id as run_id from harness_tasks where latest_run_id is not null
            """
        ).fetchall()
    }
    audit_ids = {
        int(row["run_id"])
        for row in conn.execute("select distinct run_id from yunxiao_audit_events where run_id is not null").fetchall()
    }
    artifact_stats = {
        int(row["run_id"]): (int(row["artifact_count"]), int(row["artifact_bytes"]))
        for row in conn.execute(
            """
            select run_id, count(*) as artifact_count, coalesce(sum(length(content)), 0) as artifact_bytes
            from artifacts group by run_id
            """
        ).fetchall()
    }
    candidate_ids: list[int] = []
    protected_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    for row in conn.execute("select id, source_type, status, started_at from runs order by id").fetchall():
        run_id = int(row["id"])
        if str(row["status"] or "") == "running":
            protected_counts["running"] += 1
            continue
        if run_id in task_or_change_ids:
            protected_counts["task_or_change"] += 1
            continue
        if run_id in audit_ids:
            protected_counts["audit"] += 1
            continue
        if run_id in recent_ids:
            protected_counts["recent_count_window"] += 1
            continue
        try:
            started_at = normalize_utc_datetime(datetime.fromisoformat(str(row["started_at"])))
        except (TypeError, ValueError):
            protected_counts["invalid_timestamp"] += 1
            continue
        if started_at >= cutoff:
            protected_counts["age_window"] += 1
            continue
        candidate_ids.append(run_id)
        source_counts[str(row["source_type"] or "unknown")] += 1
    candidate_artifact_count = sum(artifact_stats.get(run_id, (0, 0))[0] for run_id in candidate_ids)
    candidate_artifact_bytes = sum(artifact_stats.get(run_id, (0, 0))[1] for run_id in candidate_ids)
    return {
        "schema_version": "1.0-harness-retention-plan",
        "database_path": str(target),
        "database_user_version": int(conn.execute("pragma user_version").fetchone()[0]),
        "as_of": as_of.isoformat(),
        "keep_days": keep_days,
        "keep_recent_runs": keep_recent_runs,
        "retention_semantics": "keep_union_delete_intersection",
        "candidate_run_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
        "candidate_artifact_count": candidate_artifact_count,
        "candidate_artifact_bytes": candidate_artifact_bytes,
        "candidate_source_counts": dict(sorted(source_counts.items())),
        "protected_counts": dict(sorted(protected_counts.items())),
        "total_run_count": int(conn.execute("select count(*) from runs").fetchone()[0]),
        "will_modify_files": False,
        "archive_required_before_apply": True,
        "task_linked_runs_always_protected": True,
        "external_actions": "disabled",
    }


def retention_plan_hash(plan: dict) -> str:
    stable = dict(plan)
    stable.pop("plan_hash", None)
    stable.pop("required_confirmation", None)
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def sqlite_integrity_check(path: Path) -> str:
    resolved = path.expanduser().resolve()
    with closing(
        sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=5.0)
    ) as conn:
        return str(conn.execute("pragma integrity_check").fetchone()[0])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_database_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sanitize_legacy_action_audits(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        select id, action_type, status, target_alias,
               authorization_id_hash, details_json
        from manager_provider_action_audits
        """
    ).fetchall()
    for row in rows:
        raw_details = str(row["details_json"] or "")
        safe_metadata, metadata_source_hashes = _safe_legacy_action_audit_metadata(
            action_type=str(row["action_type"] or ""),
            status=str(row["status"] or ""),
            target_alias=str(row["target_alias"] or ""),
        )
        safe_summary_json = _legacy_action_audit_summary_json(
            raw_details,
            metadata_source_hashes=metadata_source_hashes,
        )
        safe_authorization_hash = _safe_legacy_authorization_hash(
            str(row["authorization_id_hash"] or "")
        )
        conn.execute(
            """
            update manager_provider_action_audits
            set action_type = ?, status = ?, target_alias = ?,
                authorization_id_hash = ?, authorization_hash = ?,
                details_json = ?, result_summary_json = ?
            where id = ?
            """,
            (
                safe_metadata["action_type"],
                safe_metadata["status"],
                safe_metadata["target_alias"],
                safe_authorization_hash,
                safe_authorization_hash,
                safe_summary_json,
                safe_summary_json,
                int(row["id"]),
            ),
        )


def _safe_legacy_action_audit_metadata(
    *,
    action_type: str,
    status: str,
    target_alias: str,
) -> tuple[dict[str, str], dict[str, str]]:
    safe: dict[str, str] = {}
    source_hashes: dict[str, str] = {}
    for field, value, placeholder, allow_empty in (
        ("action_type", action_type, "legacy.audit.invalid", False),
        ("status", status, "legacy_invalid", False),
        ("target_alias", target_alias, "legacy-invalid", True),
    ):
        try:
            safe[field] = validate_audit_alias(value, allow_empty=allow_empty)
        except (MemoryError, RecursionError, UnicodeError, ValueError, TypeError):
            safe[field] = placeholder
            source_hashes[field] = "sha256:" + hashlib.sha256(
                value.encode("utf-8", "surrogatepass")
            ).hexdigest()
    return safe, source_hashes


def _legacy_action_audit_summary_json(
    raw_details: str,
    *,
    metadata_source_hashes: Mapping[str, str] | None = None,
) -> str:
    source_hash = "sha256:" + hashlib.sha256(
        raw_details.encode("utf-8", "surrogatepass")
    ).hexdigest()
    placeholder = {
        "source_hash": source_hash,
        "status": "legacy_summary_unavailable",
    }
    try:
        parsed = json.loads(raw_details)
    except (json.JSONDecodeError, MemoryError, RecursionError, UnicodeError):
        safe = placeholder
    else:
        if not isinstance(parsed, Mapping):
            safe = placeholder
        else:
            safe = redact_sensitive_mapping(parsed)
            if safe == {"status": "summary_unavailable"}:
                safe = placeholder
    if metadata_source_hashes:
        safe = dict(safe)
        safe["metadata_source_hashes"] = dict(metadata_source_hashes)
    encoded = json.dumps(
        safe,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) <= 4_096:
        return encoded
    fallback = dict(placeholder)
    if metadata_source_hashes:
        fallback["metadata_source_hashes"] = dict(metadata_source_hashes)
    return json.dumps(
        fallback,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _safe_legacy_authorization_hash(value: str) -> str:
    if not value:
        return ""
    if (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdefABCDEF" for character in value[7:])
    ):
        return "sha256:" + value[7:].lower()
    return "sha256:" + hashlib.sha256(
        value.encode("utf-8", "surrogatepass")
    ).hexdigest()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    rows = conn.execute(f"pragma table_info({table})").fetchall()
    if column not in {row["name"] for row in rows}:
        conn.execute(f"alter table {table} add column {column} {definition}")


def seed_defaults(*, connection_factory: Callable[[], sqlite3.Connection] = connect) -> None:
    config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    team = config["team"]
    created_at = now_iso()
    with closing(connection_factory()) as conn, conn:
        conn.execute(
            """
            insert or ignore into teams(key, name, description, enabled, created_at)
            values(?, ?, ?, 1, ?)
            """,
            (team["key"], team["name"], team.get("description", ""), created_at),
        )
        conn.execute(
            "update teams set name = ?, description = ?, enabled = 1 where key = ?",
            (team["name"], team.get("description", ""), team["key"]),
        )
        for expert in config["experts"]:
            conn.execute(
                """
                insert or ignore into experts(key, name, role, description, prompt, tags, model, enabled, created_at)
                values(?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    expert["key"],
                    expert["name"],
                    expert["role"],
                    expert.get("description", ""),
                    expert["prompt"],
                    expert.get("tags", ""),
                    expert.get("model", ""),
                    created_at,
                ),
            )
            conn.execute(
                """
                update experts
                set name = ?, role = ?, description = ?, prompt = ?, tags = ?, model = ?, enabled = 1
                where key = ?
                """,
                (
                    expert["name"],
                    expert["role"],
                    expert.get("description", ""),
                    expert["prompt"],
                    expert.get("tags", ""),
                    expert.get("model", ""),
                    expert["key"],
                ),
            )
        for step in config["steps"]:
            conn.execute(
                """
                insert or ignore into workflow_steps(
                    team_key, step_order, step_key, step_name, expert_key, mode,
                    timeout_seconds, retry_count, stop_on_failure
                )
                values(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team["key"],
                    step["order"],
                    step["step_key"],
                    step["step_name"],
                    step["expert_key"],
                    step.get("mode", "deep"),
                    step.get("timeout_seconds", 3600),
                    step.get("retry_count", 0),
                    1 if step.get("stop_on_failure", True) else 0,
                ),
            )
            conn.execute(
                """
                update workflow_steps
                set step_order = ?, step_name = ?, expert_key = ?, mode = ?,
                    timeout_seconds = ?, retry_count = ?, stop_on_failure = ?
                where team_key = ? and step_key = ?
                """,
                (
                    step["order"],
                    step["step_name"],
                    step["expert_key"],
                    step.get("mode", "deep"),
                    step.get("timeout_seconds", 3600),
                    step.get("retry_count", 0),
                    1 if step.get("stop_on_failure", True) else 0,
                    team["key"],
                    step["step_key"],
                ),
            )


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]


def list_experts() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("select * from experts order by id"))


def get_expert(key: str) -> dict:
    with connect() as conn:
        row = conn.execute("select * from experts where key = ?", (key,)).fetchone()
    if row is None:
        raise KeyError(f"expert not found: {key}")
    return dict(row)


def get_workflow_steps(team_key: str) -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute(
                """
                select ws.*, e.name as expert_name, e.prompt as expert_prompt
                from workflow_steps ws
                join experts e on e.key = ws.expert_key
                where ws.team_key = ?
                order by ws.step_order
                """,
                (team_key,),
            )
        )


def create_run(
    team_key: str,
    title: str,
    source_type: str,
    demand_text: str,
    total_steps: int,
    llm_mode: str = "",
    llm_model: str = "",
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into runs(team_key, title, source_type, demand_text, status, total_steps, llm_mode, llm_model, started_at)
            values(?, ?, ?, ?, 'running', ?, ?, ?, ?)
            """,
            (team_key, title, source_type, demand_text, total_steps, llm_mode, llm_model, now_iso()),
        )
        return int(cursor.lastrowid)


def update_run(run_id: int, **fields: object) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [run_id]
    with connect() as conn:
        conn.execute(f"update runs set {assignments} where id = ?", values)


def insert_step_run(
    run_id: int,
    step: dict,
    status: str,
    input_text: str,
    output_text: str = "",
    error: str = "",
    duration_ms: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    attempt_round: int = 0,
    review_feedback: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into step_runs(
                run_id, step_order, step_key, step_name, expert_key, expert_name,
                attempt_round, review_feedback, status, input_text, output_text, error, duration_ms,
                prompt_tokens, completion_tokens, started_at, finished_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                step["step_order"],
                step["step_key"],
                step["step_name"],
                step["expert_key"],
                step["expert_name"],
                attempt_round,
                review_feedback,
                status,
                input_text,
                output_text,
                error,
                duration_ms,
                prompt_tokens,
                completion_tokens,
                started_at or now_iso(),
                finished_at,
            ),
        )
        return int(cursor.lastrowid)


def add_artifact(run_id: int, kind: str, title: str, content: str) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into artifacts(run_id, kind, title, content, created_at)
            values(?, ?, ?, ?, ?)
            """,
            (run_id, kind, title, content, now_iso()),
        )
        return int(cursor.lastrowid)


def list_runs(limit: int = 20) -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute(
                """
                select * from runs
                order by id desc
                limit ?
                """,
                (limit,),
            )
        )


def reconcile_stale_runs(*, max_age_hours: int = 24, now: datetime | None = None) -> dict:
    if max_age_hours < 1:
        raise ValueError("max_age_hours must be at least 1")
    active_now = now or datetime.now(timezone.utc)
    if active_now.tzinfo is None:
        active_now = active_now.replace(tzinfo=timezone.utc)
    cutoff = active_now.astimezone(timezone.utc) - timedelta(hours=max_age_hours)
    recovered: list[int] = []
    skipped_invalid_started_at: list[int] = []
    with connect() as conn:
        rows = conn.execute("select id, started_at from runs where status = 'running' order by id").fetchall()
        for row in rows:
            run_id = int(row["id"])
            try:
                started_at = datetime.fromisoformat(str(row["started_at"]))
            except (TypeError, ValueError):
                skipped_invalid_started_at.append(run_id)
                continue
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            if started_at.astimezone(timezone.utc) > cutoff:
                continue
            finished_at = active_now.astimezone().isoformat()
            summary = f"Harness 启动恢复：运行超过 {max_age_hours} 小时仍为 running，已收敛为 interrupted；原产物保留。"
            conn.execute(
                """
                update runs
                set status = 'interrupted', evaluation_status = 'interrupted_recovered',
                    evaluation_summary = ?, finished_at = ?
                where id = ? and status = 'running'
                """,
                (summary, finished_at, run_id),
            )
            if conn.execute("select changes() as count").fetchone()["count"] != 1:
                continue
            conn.execute(
                """
                update harness_task_runs
                set status = 'interrupted', evaluation_status = 'interrupted_recovered',
                    summary = ?, finished_at = ?
                where run_id = ? and status = 'running'
                """,
                (summary, finished_at, run_id),
            )
            conn.execute(
                """
                update harness_tasks
                set current_stage = 'recovery', status = 'interrupted', verification_status = 'interrupted',
                    can_commit = 0, can_yunxiao_transition = 0, updated_at = ?
                where latest_run_id = ?
                """,
                (finished_at, run_id),
            )
            audit = {
                "schema_version": "1.0-stale-run-recovery",
                "run_id": run_id,
                "previous_status": "running",
                "status": "interrupted",
                "max_age_hours": max_age_hours,
                "cutoff": cutoff.isoformat(),
                "recovered_at": finished_at,
                "destructive_cleanup": False,
                "artifacts_preserved": True,
            }
            conn.execute(
                "insert into artifacts(run_id, kind, title, content, created_at) values(?, ?, ?, ?, ?)",
                (run_id, "startup_recovery_json", "启动时中断运行恢复审计", json.dumps(audit, ensure_ascii=False, indent=2), finished_at),
            )
            recovered.append(run_id)
    return {
        "schema_version": "1.0-stale-run-recovery-summary",
        "status": "recovered" if recovered else "no_action",
        "max_age_hours": max_age_hours,
        "cutoff": cutoff.isoformat(),
        "recovered_count": len(recovered),
        "recovered_run_ids": recovered,
        "skipped_invalid_started_at": skipped_invalid_started_at,
    }


def reconcile_stale_tasks(*, max_age_hours: int = 24, now: datetime | None = None) -> dict:
    """Converge tasks left running without a durable run record."""
    if max_age_hours < 1:
        raise ValueError("max_age_hours must be at least 1")
    active_now = now or datetime.now(timezone.utc)
    cutoff = active_now.astimezone(timezone.utc) - timedelta(hours=max_age_hours)
    recovered: list[int] = []
    with connect() as conn:
        rows = conn.execute("select id, updated_at from harness_tasks where status = 'running' order by id").fetchall()
        for row in rows:
            try:
                updated_at = datetime.fromisoformat(str(row["updated_at"]))
            except (TypeError, ValueError):
                updated_at = cutoff - timedelta(seconds=1)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at.astimezone(timezone.utc) > cutoff:
                continue
            finished_at = active_now.astimezone().isoformat()
            summary = f"Harness 启动恢复：任务超过 {max_age_hours} 小时仍为 running，已收敛为 interrupted；原产物保留。"
            conn.execute(
                """
                update harness_tasks
                set current_stage = 'recovery', status = 'interrupted', verification_status = 'interrupted',
                    failure_stage = 'startup_recovery', recovery_action = '检查后台 worker、数据库和产物目录后重试；不自动删除产物。',
                    retryable = 1, can_commit = 0, can_yunxiao_transition = 0, notes = ?, updated_at = ?
                where id = ? and status = 'running'
                """,
                (summary, finished_at, int(row["id"])),
            )
            if conn.execute("select changes() as count").fetchone()["count"] == 1:
                recovered.append(int(row["id"]))
    return {
        "schema_version": "1.0-stale-task-recovery-summary",
        "status": "recovered" if recovered else "no_action",
        "max_age_hours": max_age_hours,
        "recovered_count": len(recovered),
        "recovered_task_ids": recovered,
    }


def get_run(run_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_step_runs(run_id: int) -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute(
                "select * from step_runs where run_id = ? order by step_order, attempt_round, id",
                (run_id,),
            )
        )


def get_latest_step_runs(run_id: int) -> list[dict]:
    rows = get_step_runs(run_id)
    latest: dict[int, dict] = {}
    for row in rows:
        current = latest.get(row["step_order"])
        if current is None or (row["attempt_round"], row["id"]) >= (current["attempt_round"], current["id"]):
            latest[row["step_order"]] = row
    return [latest[key] for key in sorted(latest)]


def get_artifacts(run_id: int) -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute(
                "select * from artifacts where run_id = ? order by id",
                (run_id,),
            )
        )


def get_artifact(artifact_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("select * from artifacts where id = ?", (artifact_id,)).fetchone()
    return dict(row) if row else None


def add_yunxiao_audit_event(record: dict) -> int:
    with connect() as conn:
        conn.execute(
            """
            insert into yunxiao_audit_events(
                run_id, project_key, entity_kind, entity_id, entity_title, entity_url,
                action, status, decision, idempotency_key, actor, reason,
                before_state, after_state, payload, evidence_ids, risk_level,
                model_mode, model_name, runtime_mode, real_write_status, executed_at,
                external_request_id, external_response, verification_status, error, created_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(idempotency_key) do update set
                run_id = excluded.run_id,
                project_key = excluded.project_key,
                entity_kind = excluded.entity_kind,
                entity_id = excluded.entity_id,
                entity_title = excluded.entity_title,
                entity_url = excluded.entity_url,
                action = excluded.action,
                status = excluded.status,
                decision = excluded.decision,
                actor = excluded.actor,
                reason = excluded.reason,
                before_state = excluded.before_state,
                after_state = excluded.after_state,
                payload = excluded.payload,
                evidence_ids = excluded.evidence_ids,
                risk_level = excluded.risk_level,
                model_mode = excluded.model_mode,
                model_name = excluded.model_name,
                runtime_mode = excluded.runtime_mode,
                real_write_status = excluded.real_write_status,
                executed_at = excluded.executed_at,
                external_request_id = excluded.external_request_id,
                external_response = excluded.external_response,
                verification_status = excluded.verification_status,
                error = excluded.error
            """,
            (
                record.get("run_id"),
                record.get("project_key", ""),
                record.get("entity_kind", ""),
                record.get("entity_id", ""),
                record.get("entity_title", ""),
                record.get("entity_url", ""),
                record.get("action", ""),
                record.get("status", ""),
                json.dumps(record.get("decision", {}), ensure_ascii=False),
                record.get("idempotency_key", ""),
                json.dumps(record.get("actor", {}), ensure_ascii=False),
                record.get("reason", ""),
                json.dumps(record.get("before_state", {}), ensure_ascii=False),
                json.dumps(record.get("after_state", {}), ensure_ascii=False),
                json.dumps(record.get("payload", {}), ensure_ascii=False),
                json.dumps(record.get("evidence_ids", []), ensure_ascii=False),
                record.get("risk_level", ""),
                record.get("model_mode", ""),
                record.get("model_name", ""),
                record.get("runtime_mode", ""),
                record.get("real_write_status", ""),
                record.get("executed_at", ""),
                record.get("external_request_id", ""),
                json.dumps(record.get("external_response", {}), ensure_ascii=False),
                record.get("verification_status", ""),
                record.get("error", ""),
                now_iso(),
            ),
        )
        row = conn.execute(
            "select id from yunxiao_audit_events where idempotency_key = ?",
            (record.get("idempotency_key", ""),),
        ).fetchone()
    if row is None:
        raise RuntimeError("云效事务审计记录写入失败")
    return int(row["id"])


def list_yunxiao_audit_events(run_id: int | None = None) -> list[dict]:
    with connect() as conn:
        if run_id is None:
            rows = conn.execute("select * from yunxiao_audit_events order by id desc limit 100").fetchall()
        else:
            rows = conn.execute(
                "select * from yunxiao_audit_events where run_id = ? order by id",
                (run_id,),
            ).fetchall()
    return rows_to_dicts(rows)


def upsert_task(record: dict) -> int:
    timestamp = now_iso()
    task_key = str(record.get("task_key") or "").strip()
    if not task_key:
        raise ValueError("task_key 不能为空")
    with connect() as conn:
        conn.execute(
            """
            insert into harness_tasks(
                task_key, entity_kind, entity_id, entity_title, entity_url, source_type,
                current_stage, status, risk_level, project_root, project_paths,
                base_branch, work_branch, latest_run_id, latest_output_dir, latest_artifacts,
                verification_status, can_commit, can_yunxiao_transition, notes, metadata,
                created_at, updated_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(task_key) do update set
                entity_kind = excluded.entity_kind,
                entity_id = excluded.entity_id,
                entity_title = excluded.entity_title,
                entity_url = excluded.entity_url,
                source_type = excluded.source_type,
                project_root = excluded.project_root,
                project_paths = excluded.project_paths,
                base_branch = excluded.base_branch,
                work_branch = excluded.work_branch,
                notes = excluded.notes,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                task_key,
                record.get("entity_kind", ""),
                record.get("entity_id", ""),
                record.get("entity_title", ""),
                record.get("entity_url", ""),
                record.get("source_type", "manual"),
                record.get("current_stage", "created"),
                record.get("status", "created"),
                record.get("risk_level", ""),
                record.get("project_root", ""),
                json.dumps(record.get("project_paths", []), ensure_ascii=False),
                record.get("base_branch", ""),
                record.get("work_branch", ""),
                record.get("latest_run_id"),
                record.get("latest_output_dir", ""),
                json.dumps(record.get("latest_artifacts", {}), ensure_ascii=False),
                record.get("verification_status", ""),
                1 if record.get("can_commit") else 0,
                1 if record.get("can_yunxiao_transition") else 0,
                record.get("notes", ""),
                json.dumps(record.get("metadata", {}), ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("select id from harness_tasks where task_key = ?", (task_key,)).fetchone()
    if row is None:
        raise RuntimeError("Harness task 写入失败")
    return int(row["id"])


def update_task(task_id: int, **fields: object) -> None:
    if not fields:
        return
    normalized = {}
    for key, value in fields.items():
        if key in {"project_paths", "latest_artifacts", "metadata"}:
            normalized[key] = json.dumps(value, ensure_ascii=False)
        elif key in {"can_commit", "can_yunxiao_transition", "retryable"}:
            normalized[key] = 1 if value else 0
        else:
            normalized[key] = value
    normalized["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    values = list(normalized.values()) + [task_id]
    with connect() as conn:
        conn.execute(f"update harness_tasks set {assignments} where id = ?", values)


def list_tasks(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from harness_tasks
            order by updated_at desc, id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [decode_task_row(row) for row in rows]


def get_task(task_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("select * from harness_tasks where id = ?", (task_id,)).fetchone()
    return decode_task_row(row) if row else None


def get_task_by_key(task_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("select * from harness_tasks where task_key = ?", (task_key,)).fetchone()
    return decode_task_row(row) if row else None


def get_task_by_entity(entity_kind: str, entity_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            select * from harness_tasks
            where entity_kind = ? and entity_id = ?
            order by id desc
            limit 1
            """,
            (entity_kind, entity_id),
        ).fetchone()
    return decode_task_row(row) if row else None


def add_task_run(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_task_runs(
                task_id, run_id, stage, execution_mode, status, evaluation_status,
                output_dir, summary, verification_status, failure_stage, recovery_action, retryable,
                artifact_paths, started_at, finished_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("task_id"),
                record.get("run_id"),
                record.get("stage", ""),
                record.get("execution_mode", ""),
                record.get("status", ""),
                record.get("evaluation_status", ""),
                record.get("output_dir", ""),
                record.get("summary", ""),
                record.get("verification_status", ""),
                record.get("failure_stage", ""),
                record.get("recovery_action", ""),
                1 if record.get("retryable") else 0,
                json.dumps(record.get("artifact_paths", {}), ensure_ascii=False),
                record.get("started_at") or timestamp,
                record.get("finished_at"),
            ),
        )
        return int(cursor.lastrowid)


def update_task_run(task_run_id: int, **fields: object) -> None:
    if not fields:
        return
    normalized: dict[str, object] = {}
    for key, value in fields.items():
        if key == "artifact_paths":
            normalized[key] = json.dumps(value, ensure_ascii=False)
        else:
            normalized[key] = value
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    values = list(normalized.values()) + [task_run_id]
    with connect() as conn:
        conn.execute(f"update harness_task_runs set {assignments} where id = ?", values)


def get_task_run(task_run_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("select * from harness_task_runs where id = ?", (task_run_id,)).fetchone()
    return decode_task_run_row(row) if row else None


def get_task_run_by_output_dir(task_id: int, output_dir: str, execution_mode: str = "") -> dict | None:
    query = """
        select * from harness_task_runs
        where task_id = ? and output_dir = ?
    """
    params: list[object] = [task_id, output_dir]
    if execution_mode:
        query += " and execution_mode = ?"
        params.append(execution_mode)
    query += " order by id desc limit 1"
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
    return decode_task_run_row(row) if row else None


def list_task_runs(task_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from harness_task_runs
            where task_id = ?
            order by id desc
            """,
            (task_id,),
        ).fetchall()
    return [decode_task_run_row(row) for row in rows]


def add_task_change(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_task_changes(
                task_id, task_run_id, run_id, change_sequence, change_id,
                source_type, status, project_path, allowed_paths, diff_path,
                diff_summary, diff_sha256, verification_status, rollback_mode,
                rollback_status, notes, metadata, created_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("task_id"),
                record.get("task_run_id"),
                record.get("run_id"),
                record.get("change_sequence"),
                record.get("change_id"),
                record.get("source_type", ""),
                record.get("status", ""),
                record.get("project_path", ""),
                json.dumps(record.get("allowed_paths", []), ensure_ascii=False),
                record.get("diff_path", ""),
                record.get("diff_summary", ""),
                record.get("diff_sha256", ""),
                record.get("verification_status", ""),
                record.get("rollback_mode", "dry_run_only"),
                record.get("rollback_status", "available"),
                record.get("notes", ""),
                json.dumps(record.get("metadata", {}), ensure_ascii=False),
                record.get("created_at") or timestamp,
            ),
        )
        return int(cursor.lastrowid)


def list_task_changes(task_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from harness_task_changes
            where task_id = ?
            order by change_sequence
            """,
            (task_id,),
        ).fetchall()
    return [decode_task_change_row(row) for row in rows]


def get_task_change_by_sequence(task_id: int, change_sequence: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            select * from harness_task_changes
            where task_id = ? and change_sequence = ?
            limit 1
            """,
            (task_id, change_sequence),
        ).fetchone()
    return decode_task_change_row(row) if row else None


def get_task_change_by_change_id(change_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            select * from harness_task_changes
            where change_id = ?
            limit 1
            """,
            (change_id,),
        ).fetchone()
    return decode_task_change_row(row) if row else None


def update_task_change(change_record_id: int, **fields: object) -> None:
    if not fields:
        return
    normalized = dict(fields)
    if "allowed_paths" in normalized:
        normalized["allowed_paths"] = json.dumps(normalized["allowed_paths"], ensure_ascii=False)
    if "metadata" in normalized:
        normalized["metadata"] = json.dumps(normalized["metadata"], ensure_ascii=False)
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    values = list(normalized.values()) + [change_record_id]
    with connect() as conn:
        conn.execute(f"update harness_task_changes set {assignments} where id = ?", values)


def decode_task_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["project_paths"] = json_loads_safe(item.get("project_paths"), [])
    item["latest_artifacts"] = json_loads_safe(item.get("latest_artifacts"), {})
    item["metadata"] = json_loads_safe(item.get("metadata"), {})
    item["can_commit"] = bool(item.get("can_commit"))
    item["can_yunxiao_transition"] = bool(item.get("can_yunxiao_transition"))
    return item


def decode_task_run_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["artifact_paths"] = json_loads_safe(item.get("artifact_paths"), {})
    return item


def decode_task_change_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["allowed_paths"] = json_loads_safe(item.get("allowed_paths"), [])
    item["metadata"] = json_loads_safe(item.get("metadata"), {})
    return item


def json_loads_safe(value: object, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


DELIVERY_TRANSACTION_JSON_FIELDS = {
    "policy_snapshot",
    "repository_snapshot",
    "release_acceptance",
    "rc_acceptance",
    "commit_records",
    "remote_results",
    "parity_result",
}


def add_delivery_transaction(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_delivery_transactions(
                transaction_key, task_id, source_run_id, entity_kind, entity_id,
                project_path, state, plan_hash, policy_snapshot, repository_snapshot,
                release_acceptance, rc_acceptance, commit_records, remote_results,
                parity_result, output_dir, journal_path, last_error, created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("transaction_key"),
                record.get("task_id"),
                record.get("source_run_id"),
                record.get("entity_kind", ""),
                record.get("entity_id", ""),
                record.get("project_path", ""),
                record.get("state", "planned"),
                record.get("plan_hash", ""),
                json.dumps(record.get("policy_snapshot", {}), ensure_ascii=False),
                json.dumps(record.get("repository_snapshot", {}), ensure_ascii=False),
                json.dumps(record.get("release_acceptance", {}), ensure_ascii=False),
                json.dumps(record.get("rc_acceptance", {}), ensure_ascii=False),
                json.dumps(record.get("commit_records", []), ensure_ascii=False),
                json.dumps(record.get("remote_results", []), ensure_ascii=False),
                json.dumps(record.get("parity_result", {}), ensure_ascii=False),
                record.get("output_dir", ""),
                record.get("journal_path", ""),
                record.get("last_error", ""),
                record.get("created_at") or timestamp,
                record.get("updated_at") or timestamp,
            ),
        )
        return int(cursor.lastrowid)


def update_delivery_transaction(transaction_id: int, **fields: object) -> None:
    if not fields:
        return
    allowed = {
        "state",
        "plan_hash",
        "policy_snapshot",
        "repository_snapshot",
        "release_acceptance",
        "rc_acceptance",
        "commit_records",
        "remote_results",
        "parity_result",
        "output_dir",
        "journal_path",
        "last_error",
    }
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ValueError("不支持更新 delivery transaction 字段：" + ", ".join(unknown))
    normalized: dict[str, object] = {}
    for key, value in fields.items():
        normalized[key] = json.dumps(value, ensure_ascii=False) if key in DELIVERY_TRANSACTION_JSON_FIELDS else value
    normalized["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    values = list(normalized.values()) + [transaction_id]
    with connect() as conn:
        conn.execute(
            f"update harness_delivery_transactions set {assignments} where id = ?",
            values,
        )


def get_delivery_transaction(transaction_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_delivery_transactions where id = ?",
            (transaction_id,),
        ).fetchone()
    return decode_delivery_transaction_row(row) if row else None


def get_delivery_transaction_by_key(transaction_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_delivery_transactions where transaction_key = ?",
            (transaction_key,),
        ).fetchone()
    return decode_delivery_transaction_row(row) if row else None


def list_delivery_transactions(*, task_id: int | None = None, limit: int = 50) -> list[dict]:
    with connect() as conn:
        if task_id is None:
            rows = conn.execute(
                """
                select * from harness_delivery_transactions
                order by id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select * from harness_delivery_transactions
                where task_id = ?
                order by id desc
                limit ?
                """,
                (task_id, limit),
            ).fetchall()
    return [decode_delivery_transaction_row(row) for row in rows]


def add_delivery_event(record: dict) -> int:
    transaction_id = int(record.get("transaction_id") or 0)
    if transaction_id <= 0:
        raise ValueError("delivery event 缺少 transaction_id")
    with connect() as conn:
        next_sequence = int(
            conn.execute(
                "select coalesce(max(sequence), 0) + 1 from harness_delivery_events where transaction_id = ?",
                (transaction_id,),
            ).fetchone()[0]
        )
        cursor = conn.execute(
            """
            insert into harness_delivery_events(
                transaction_id, sequence, event_type, status, input_hash, details, created_at
            ) values(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                next_sequence,
                record.get("event_type", ""),
                record.get("status", ""),
                record.get("input_hash", ""),
                json.dumps(record.get("details", {}), ensure_ascii=False),
                record.get("created_at") or now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def list_delivery_events(transaction_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from harness_delivery_events
            where transaction_id = ?
            order by sequence
            """,
            (transaction_id,),
        ).fetchall()
    return [decode_delivery_event_row(row) for row in rows]


def decode_delivery_transaction_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    for key in DELIVERY_TRANSACTION_JSON_FIELDS:
        fallback: object = [] if key in {"commit_records", "remote_results"} else {}
        item[key] = json_loads_safe(item.get(key), fallback)
    return item


def decode_delivery_event_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["details"] = json_loads_safe(item.get("details"), {})
    return item


def get_schema_meta(key: str) -> str:
    with connect() as conn:
        row = conn.execute("select value from harness_schema_meta where key = ?", (key,)).fetchone()
    return str(row["value"]) if row else ""


def add_dynamic_plan(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_plans(
                task_id, plan_hash, schema_version, status, complexity_level,
                total_score, plan_payload, supersedes_plan_id, created_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("task_id"),
                record.get("plan_hash", ""),
                record.get("schema_version", ""),
                record.get("status", ""),
                record.get("complexity_level", ""),
                int(record.get("total_score") or 0),
                json.dumps(record.get("plan_payload", {}), ensure_ascii=False),
                record.get("supersedes_plan_id"),
                record.get("created_at") or now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def update_dynamic_plan(plan_id: int, **fields: object) -> None:
    if not fields:
        return
    normalized: dict[str, object] = {}
    for key, value in fields.items():
        normalized[key] = json.dumps(value, ensure_ascii=False) if key == "plan_payload" else value
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    values = list(normalized.values()) + [plan_id]
    with connect() as conn:
        conn.execute(f"update harness_dynamic_plans set {assignments} where id = ?", values)


def get_dynamic_plan(plan_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("select * from harness_dynamic_plans where id = ?", (plan_id,)).fetchone()
    return decode_dynamic_plan_row(row) if row else None


def get_dynamic_plan_by_hash(task_id: int, plan_hash: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_dynamic_plans where task_id = ? and plan_hash = ?",
            (task_id, plan_hash),
        ).fetchone()
    return decode_dynamic_plan_row(row) if row else None


def get_latest_dynamic_plan(task_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_dynamic_plans where task_id = ? order by id desc limit 1",
            (task_id,),
        ).fetchone()
    return decode_dynamic_plan_row(row) if row else None


def add_dynamic_subtask(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_subtasks(
                plan_id, task_id, node_id, title, node_kind, role_id, status,
                output_contract, allowed_paths, parallel_group,
                human_confirmation_required, metadata, created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("plan_id"),
                record.get("task_id"),
                record.get("node_id", ""),
                record.get("title", ""),
                record.get("node_kind", ""),
                record.get("role_id", ""),
                record.get("status", "planned"),
                record.get("output_contract", ""),
                json.dumps(record.get("allowed_paths", []), ensure_ascii=False),
                record.get("parallel_group", ""),
                1 if record.get("human_confirmation_required") else 0,
                json.dumps(record.get("metadata", {}), ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def list_dynamic_subtasks(plan_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "select * from harness_dynamic_subtasks where plan_id = ? order by id",
            (plan_id,),
        ).fetchall()
    return [decode_dynamic_subtask_row(row) for row in rows]


def update_dynamic_subtask_status(plan_id: int, node_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "update harness_dynamic_subtasks set status = ?, updated_at = ? where plan_id = ? and node_id = ?",
            (status, now_iso(), plan_id, node_id),
        )


def add_dynamic_edge(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_edges(
                plan_id, source_node_id, target_node_id, dependency_type,
                artifact_schema, reason, created_at
            ) values(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("plan_id"),
                record.get("source_node_id", ""),
                record.get("target_node_id", ""),
                record.get("dependency_type", ""),
                record.get("artifact_schema", ""),
                record.get("reason", ""),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def list_dynamic_edges(plan_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "select * from harness_dynamic_edges where plan_id = ? order by id",
            (plan_id,),
        ).fetchall()
    return rows_to_dicts(rows)


def add_contract_artifact(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_contract_artifacts(
                plan_id, task_id, node_id, artifact_id, artifact_version,
                schema_name, schema_version, producer, input_artifact_ids,
                content_hash, status, payload, supersedes_artifact_id, created_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("plan_id"),
                record.get("task_id"),
                record.get("node_id", ""),
                record.get("artifact_id", ""),
                int(record.get("artifact_version") or 0),
                record.get("schema_name", ""),
                record.get("schema_version", ""),
                record.get("producer", ""),
                json.dumps(record.get("input_artifact_ids", []), ensure_ascii=False),
                record.get("content_hash", ""),
                record.get("status", "planned"),
                json.dumps(record.get("payload", {}), ensure_ascii=False),
                record.get("supersedes_artifact_id", ""),
                record.get("created_at") or now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def get_contract_artifact(artifact_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_contract_artifacts where artifact_id = ?",
            (artifact_id,),
        ).fetchone()
    return decode_contract_artifact_row(row) if row else None


def get_latest_contract_artifact(plan_id: int, node_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            select * from harness_contract_artifacts
            where plan_id = ? and node_id = ?
            order by artifact_version desc, id desc limit 1
            """,
            (plan_id, node_id),
        ).fetchone()
    return decode_contract_artifact_row(row) if row else None


def list_contract_artifacts(plan_id: int, *, latest_only: bool = False) -> list[dict]:
    if latest_only:
        query = """
            select artifact.* from harness_contract_artifacts artifact
            join (
                select node_id, max(artifact_version) as max_version
                from harness_contract_artifacts where plan_id = ? group by node_id
            ) latest
            on artifact.node_id = latest.node_id and artifact.artifact_version = latest.max_version
            where artifact.plan_id = ? order by artifact.id
        """
        params: tuple[object, ...] = (plan_id, plan_id)
    else:
        query = "select * from harness_contract_artifacts where plan_id = ? order by id"
        params = (plan_id,)
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [decode_contract_artifact_row(row) for row in rows]


def update_contract_artifact_status(artifact_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "update harness_contract_artifacts set status = ? where artifact_id = ?",
            (status, artifact_id),
        )


def add_dynamic_audit_event(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_audit_events(
                task_id, plan_id, node_id, action, status, details, created_at
            ) values(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("task_id"),
                record.get("plan_id"),
                record.get("node_id", ""),
                record.get("action", ""),
                record.get("status", ""),
                json.dumps(record.get("details", {}), ensure_ascii=False),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def list_dynamic_audit_events(plan_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "select * from harness_dynamic_audit_events where plan_id = ? order by id",
            (plan_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["details"] = json_loads_safe(item.get("details"), {})
        result.append(item)
    return result


def decode_dynamic_plan_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["plan_payload"] = json_loads_safe(item.get("plan_payload"), {})
    return item


def decode_dynamic_subtask_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["allowed_paths"] = json_loads_safe(item.get("allowed_paths"), [])
    item["metadata"] = json_loads_safe(item.get("metadata"), {})
    item["human_confirmation_required"] = bool(item.get("human_confirmation_required"))
    return item


def decode_contract_artifact_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["input_artifact_ids"] = json_loads_safe(item.get("input_artifact_ids"), [])
    item["payload"] = json_loads_safe(item.get("payload"), {})
    return item


def add_dynamic_schedule(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_schedules(
                plan_id, task_id, mode, status, tick, policy_snapshot,
                created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("plan_id"),
                record.get("task_id"),
                record.get("mode", "dry_run"),
                record.get("status", "active"),
                int(record.get("tick") or 0),
                json.dumps(record.get("policy_snapshot", {}), ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def get_dynamic_schedule(schedule_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_dynamic_schedules where id = ?",
            (schedule_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["policy_snapshot"] = json_loads_safe(item.get("policy_snapshot"), {})
    return item


def update_dynamic_schedule(schedule_id: int, **fields: object) -> None:
    if not fields:
        return
    normalized = {
        key: json.dumps(value, ensure_ascii=False) if key == "policy_snapshot" else value
        for key, value in fields.items()
    }
    normalized["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    values = list(normalized.values()) + [schedule_id]
    with connect() as conn:
        conn.execute(f"update harness_dynamic_schedules set {assignments} where id = ?", values)


def add_dynamic_node_state(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_node_states(
                schedule_id, plan_id, node_id, role_id, state, attempt_count,
                max_retries, input_budget_tokens, output_budget_tokens,
                timeout_seconds, parallel_allowed, human_only, last_event_id,
                last_decision, created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("schedule_id"),
                record.get("plan_id"),
                record.get("node_id", ""),
                record.get("role_id", ""),
                record.get("state", "planned"),
                int(record.get("attempt_count") or 0),
                int(record.get("max_retries") or 0),
                int(record.get("input_budget_tokens") or 0),
                int(record.get("output_budget_tokens") or 0),
                int(record.get("timeout_seconds") or 0),
                1 if record.get("parallel_allowed", True) else 0,
                1 if record.get("human_only") else 0,
                record.get("last_event_id", ""),
                json.dumps(record.get("last_decision", {}), ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def list_dynamic_node_states(schedule_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "select * from harness_dynamic_node_states where schedule_id = ? order by id",
            (schedule_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["parallel_allowed"] = bool(item.get("parallel_allowed"))
        item["human_only"] = bool(item.get("human_only"))
        item["last_decision"] = json_loads_safe(item.get("last_decision"), {})
        result.append(item)
    return result


def update_dynamic_node_state(schedule_id: int, node_id: str, **fields: object) -> None:
    if not fields:
        return
    normalized = {
        key: json.dumps(value, ensure_ascii=False) if key == "last_decision" else value
        for key, value in fields.items()
    }
    for key in ("parallel_allowed", "human_only"):
        if key in normalized:
            normalized[key] = 1 if normalized[key] else 0
    normalized["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    values = list(normalized.values()) + [schedule_id, node_id]
    with connect() as conn:
        conn.execute(
            f"update harness_dynamic_node_states set {assignments} where schedule_id = ? and node_id = ?",
            values,
        )


def add_dynamic_schedule_event(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_schedule_events(
                schedule_id, event_key, event_type, node_id, payload,
                decision, created_at
            ) values(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("schedule_id"),
                record.get("event_key", ""),
                record.get("event_type", ""),
                record.get("node_id", ""),
                json.dumps(record.get("payload", {}), ensure_ascii=False),
                json.dumps(record.get("decision", {}), ensure_ascii=False),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def get_dynamic_schedule_event(schedule_id: int, event_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_dynamic_schedule_events where schedule_id = ? and event_key = ?",
            (schedule_id, event_key),
        ).fetchone()
    return decode_dynamic_schedule_event(row) if row else None


def list_dynamic_schedule_events(schedule_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "select * from harness_dynamic_schedule_events where schedule_id = ? order by id",
            (schedule_id,),
        ).fetchall()
    return [decode_dynamic_schedule_event(row) for row in rows]


def decode_dynamic_schedule_event(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["payload"] = json_loads_safe(item.get("payload"), {})
    item["decision"] = json_loads_safe(item.get("decision"), {})
    return item


def add_dynamic_checkpoint(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_checkpoints(
                schedule_id, tick, checkpoint_hash, payload, created_at
            ) values(?, ?, ?, ?, ?)
            """,
            (
                record.get("schedule_id"),
                int(record.get("tick") or 0),
                record.get("checkpoint_hash", ""),
                json.dumps(record.get("payload", {}), ensure_ascii=False),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def get_latest_dynamic_checkpoint(schedule_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            select * from harness_dynamic_checkpoints
            where schedule_id = ? order by tick desc, id desc limit 1
            """,
            (schedule_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["payload"] = json_loads_safe(item.get("payload"), {})
    return item


def add_dynamic_context_envelope(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_context_envelopes(
                schedule_id, plan_id, node_id, role_id, checkpoint_hash,
                plan_hash, envelope_hash, status, requested_tools,
                tool_decisions, payload, created_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("schedule_id"),
                record.get("plan_id"),
                record.get("node_id", ""),
                record.get("role_id", ""),
                record.get("checkpoint_hash", ""),
                record.get("plan_hash", ""),
                record.get("envelope_hash", ""),
                record.get("status", "current"),
                json.dumps(record.get("requested_tools", []), ensure_ascii=False),
                json.dumps(record.get("tool_decisions", []), ensure_ascii=False),
                json.dumps(record.get("payload", {}), ensure_ascii=False),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def get_dynamic_context_envelope(context_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_dynamic_context_envelopes where id = ?",
            (context_id,),
        ).fetchone()
    return decode_dynamic_context_envelope(row) if row else None


def get_dynamic_context_envelope_by_hash(envelope_hash: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_dynamic_context_envelopes where envelope_hash = ?",
            (envelope_hash,),
        ).fetchone()
    return decode_dynamic_context_envelope(row) if row else None


def update_dynamic_context_envelope_status(context_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "update harness_dynamic_context_envelopes set status = ? where id = ?",
            (status, context_id),
        )


def decode_dynamic_context_envelope(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["requested_tools"] = json_loads_safe(item.get("requested_tools"), [])
    item["tool_decisions"] = json_loads_safe(item.get("tool_decisions"), [])
    item["payload"] = json_loads_safe(item.get("payload"), {})
    return item


def add_capability_lease(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_capability_leases(
                context_id, schedule_id, plan_id, node_id, context_hash,
                checkpoint_hash, lease_key, adapter_kind, capabilities,
                policy_hash, issued_at, expires_at, max_uses, use_count,
                status, created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("context_id"),
                record.get("schedule_id"),
                record.get("plan_id"),
                record.get("node_id", ""),
                record.get("context_hash", ""),
                record.get("checkpoint_hash", ""),
                record.get("lease_key", ""),
                record.get("adapter_kind", ""),
                json.dumps(record.get("capabilities", []), ensure_ascii=False),
                record.get("policy_hash", ""),
                record.get("issued_at", timestamp),
                record.get("expires_at", timestamp),
                int(record.get("max_uses") or 1),
                int(record.get("use_count") or 0),
                record.get("status", "issued"),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def get_capability_lease(lease_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_capability_leases where id = ?",
            (lease_id,),
        ).fetchone()
    return decode_capability_lease(row) if row else None


def get_capability_lease_by_key(lease_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_capability_leases where lease_key = ?",
            (lease_key,),
        ).fetchone()
    return decode_capability_lease(row) if row else None


def update_capability_lease_status(lease_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "update harness_capability_leases set status = ?, updated_at = ? where id = ?",
            (status, now_iso(), lease_id),
        )


def consume_capability_lease(lease_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            update harness_capability_leases
            set use_count = use_count + 1, status = 'consumed', updated_at = ?
            where id = ? and status = 'issued' and use_count < max_uses
            """,
            (now_iso(), lease_id),
        )
        return cursor.rowcount == 1


def decode_capability_lease(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["capabilities"] = json_loads_safe(item.get("capabilities"), [])
    return item


def add_dynamic_node_execution(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_dynamic_node_executions(
                context_id, schedule_id, plan_id, node_id, execution_key,
                executor_kind, status, fixture_relpath, fixture_digest,
                requested_tools, tool_decisions, candidate_schema,
                candidate_hash, candidate_payload, error_code, lease_id,
                runtime_details, created_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("context_id"),
                record.get("schedule_id"),
                record.get("plan_id"),
                record.get("node_id", ""),
                record.get("execution_key", ""),
                record.get("executor_kind", "fixture_json"),
                record.get("status", ""),
                record.get("fixture_relpath", ""),
                record.get("fixture_digest", ""),
                json.dumps(record.get("requested_tools", []), ensure_ascii=False),
                json.dumps(record.get("tool_decisions", []), ensure_ascii=False),
                record.get("candidate_schema", ""),
                record.get("candidate_hash", ""),
                json.dumps(record.get("candidate_payload", {}), ensure_ascii=False),
                record.get("error_code", ""),
                int(record.get("lease_id") or 0),
                json.dumps(record.get("runtime_details", {}), ensure_ascii=False),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def get_dynamic_node_execution(execution_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_dynamic_node_executions where id = ?",
            (execution_id,),
        ).fetchone()
    return decode_dynamic_node_execution(row) if row else None


def get_dynamic_node_execution_by_key(execution_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_dynamic_node_executions where execution_key = ?",
            (execution_key,),
        ).fetchone()
    return decode_dynamic_node_execution(row) if row else None


def get_latest_successful_node_execution(
    plan_id: int,
    node_id: str,
    *,
    schedule_id: int | None = None,
) -> dict | None:
    with connect() as conn:
        if schedule_id is None:
            row = conn.execute(
                """
                select * from harness_dynamic_node_executions
                where plan_id = ? and node_id = ?
                  and status in ('succeeded_fixture', 'succeeded_sandbox_fixture')
                order by id desc limit 1
                """,
                (plan_id, node_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                select * from harness_dynamic_node_executions
                where plan_id = ? and schedule_id = ? and node_id = ?
                  and status in ('succeeded_fixture', 'succeeded_sandbox_fixture')
                order by id desc limit 1
                """,
                (plan_id, schedule_id, node_id),
            ).fetchone()
    return decode_dynamic_node_execution(row) if row else None


def list_dynamic_node_executions(*, context_id: int | None = None) -> list[dict]:
    with connect() as conn:
        if context_id is None:
            rows = conn.execute(
                "select * from harness_dynamic_node_executions order by id"
            ).fetchall()
        else:
            rows = conn.execute(
                "select * from harness_dynamic_node_executions where context_id = ? order by id",
                (context_id,),
            ).fetchall()
    return [decode_dynamic_node_execution(row) for row in rows]


def decode_dynamic_node_execution(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["requested_tools"] = json_loads_safe(item.get("requested_tools"), [])
    item["tool_decisions"] = json_loads_safe(item.get("tool_decisions"), [])
    item["candidate_payload"] = json_loads_safe(item.get("candidate_payload"), {})
    item["runtime_details"] = json_loads_safe(item.get("runtime_details"), {})
    return item


def add_mock_agent_run(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_mock_agent_runs(
                schedule_id, plan_id, run_key, adapter_kind, status,
                max_parallel, started_at, completed_at, summary,
                created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(record["schedule_id"]),
                int(record["plan_id"]),
                record["run_key"],
                record.get("adapter_kind", "deterministic_mock_agent"),
                record.get("status", "running"),
                int(record.get("max_parallel") or 1),
                record.get("started_at", timestamp),
                record.get("completed_at", ""),
                json.dumps(record.get("summary", {}), ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def get_mock_agent_run(run_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_mock_agent_runs where id = ?",
            (run_id,),
        ).fetchone()
    return decode_mock_agent_run(row) if row else None


def get_mock_agent_run_by_schedule(schedule_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_mock_agent_runs where schedule_id = ?",
            (schedule_id,),
        ).fetchone()
    return decode_mock_agent_run(row) if row else None


def update_mock_agent_run(
    run_id: int,
    *,
    status: str,
    completed_at: str = "",
    summary: dict | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            update harness_mock_agent_runs
            set status = ?, completed_at = ?, summary = ?, updated_at = ?
            where id = ?
            """,
            (
                status,
                completed_at,
                json.dumps(summary or {}, ensure_ascii=False),
                now_iso(),
                run_id,
            ),
        )


def decode_mock_agent_run(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["summary"] = json_loads_safe(item.get("summary"), {})
    return item


def add_mock_agent_trace(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_mock_agent_traces(
                run_id, schedule_id, plan_id, wave_index, trace_id,
                node_id, role_id, context_id, lease_id, execution_id,
                status, error_code, candidate_hash, input_artifact_ids,
                input_tokens, output_tokens, elapsed_ms, observed_concurrency,
                parallel_observed, started_at, finished_at, details, created_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(record["run_id"]),
                int(record["schedule_id"]),
                int(record["plan_id"]),
                int(record["wave_index"]),
                record["trace_id"],
                record["node_id"],
                record["role_id"],
                int(record["context_id"]),
                int(record["lease_id"]),
                int(record["execution_id"]),
                record["status"],
                record.get("error_code", ""),
                record.get("candidate_hash", ""),
                json.dumps(record.get("input_artifact_ids", []), ensure_ascii=False),
                int(record.get("input_tokens") or 0),
                int(record.get("output_tokens") or 0),
                int(record.get("elapsed_ms") or 0),
                int(record.get("observed_concurrency") or 1),
                1 if record.get("parallel_observed") else 0,
                record["started_at"],
                record["finished_at"],
                json.dumps(record.get("details", {}), ensure_ascii=False),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def list_mock_agent_traces(run_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from harness_mock_agent_traces
            where run_id = ? order by wave_index, id
            """,
            (run_id,),
        ).fetchall()
    return [decode_mock_agent_trace(row) for row in rows]


def decode_mock_agent_trace(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["input_artifact_ids"] = json_loads_safe(item.get("input_artifact_ids"), [])
    item["details"] = json_loads_safe(item.get("details"), {})
    item["parallel_observed"] = bool(item.get("parallel_observed"))
    return item


def add_model_invocation(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_model_invocations(
                context_id, schedule_id, plan_id, node_id, role_id,
                invocation_key, request_hash, mode, provider, model, status,
                request_payload, response_payload, response_hash,
                candidate_payload, candidate_hash, usage, error_code,
                cassette_relpath, cassette_digest, started_at, completed_at,
                created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(record["context_id"]),
                int(record["schedule_id"]),
                int(record["plan_id"]),
                record["node_id"],
                record["role_id"],
                record["invocation_key"],
                record["request_hash"],
                record["mode"],
                record.get("provider", ""),
                record.get("model", ""),
                record["status"],
                json.dumps(record.get("request_payload", {}), ensure_ascii=False),
                json.dumps(record.get("response_payload", {}), ensure_ascii=False),
                record.get("response_hash", ""),
                json.dumps(record.get("candidate_payload", {}), ensure_ascii=False),
                record.get("candidate_hash", ""),
                json.dumps(record.get("usage", {}), ensure_ascii=False),
                record.get("error_code", ""),
                record.get("cassette_relpath", ""),
                record.get("cassette_digest", ""),
                record.get("started_at", timestamp),
                record.get("completed_at", ""),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def get_model_invocation(invocation_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_model_invocations where id = ?",
            (invocation_id,),
        ).fetchone()
    return decode_model_invocation(row) if row else None


def get_model_invocation_by_key(invocation_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_model_invocations where invocation_key = ?",
            (invocation_key,),
        ).fetchone()
    return decode_model_invocation(row) if row else None


def list_model_invocations(*, context_id: int | None = None) -> list[dict]:
    with connect() as conn:
        if context_id is None:
            rows = conn.execute("select * from harness_model_invocations order by id").fetchall()
        else:
            rows = conn.execute(
                "select * from harness_model_invocations where context_id = ? order by id",
                (context_id,),
            ).fetchall()
    return [decode_model_invocation(row) for row in rows]


def get_latest_successful_model_invocation(
    plan_id: int,
    node_id: str,
    *,
    schedule_id: int | None = None,
) -> dict | None:
    with connect() as conn:
        if schedule_id is None:
            row = conn.execute(
                """
                select * from harness_model_invocations
                where plan_id = ? and node_id = ? and status = 'succeeded_fixture'
                order by id desc limit 1
                """,
                (plan_id, node_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                select * from harness_model_invocations
                where plan_id = ? and schedule_id = ? and node_id = ?
                  and status = 'succeeded_fixture'
                order by id desc limit 1
                """,
                (plan_id, schedule_id, node_id),
            ).fetchone()
    return decode_model_invocation(row) if row else None


def decode_model_invocation(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["request_payload"] = json_loads_safe(item.get("request_payload"), {})
    item["response_payload"] = json_loads_safe(item.get("response_payload"), {})
    item["candidate_payload"] = json_loads_safe(item.get("candidate_payload"), {})
    item["usage"] = json_loads_safe(item.get("usage"), {})
    return item


def add_model_invocation_event(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_model_invocation_events(
                invocation_id, sequence, event_type, status, details, created_at
            ) values(?, ?, ?, ?, ?, ?)
            """,
            (
                int(record["invocation_id"]),
                int(record["sequence"]),
                record["event_type"],
                record["status"],
                json.dumps(record.get("details", {}), ensure_ascii=False),
                record.get("created_at", now_iso()),
            ),
        )
        return int(cursor.lastrowid)


def list_model_invocation_events(invocation_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from harness_model_invocation_events
            where invocation_id = ? order by sequence, id
            """,
            (invocation_id,),
        ).fetchall()
    return [decode_model_invocation_event(row) for row in rows]


def decode_model_invocation_event(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["details"] = json_loads_safe(item.get("details"), {})
    return item


def add_model_provider_smoke(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_model_provider_smokes(
                smoke_key, profile_key, provider_kind, endpoint_host, model,
                status, transport_status, protocol_status, marker_status,
                authorization_hash, credential_key_names, request_hash,
                response_hash, usage, timeout_seconds, error_code, error_detail,
                started_at, completed_at, created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["smoke_key"],
                record["profile_key"],
                record["provider_kind"],
                record["endpoint_host"],
                record["model"],
                record["status"],
                record.get("transport_status", ""),
                record.get("protocol_status", ""),
                record.get("marker_status", ""),
                record["authorization_hash"],
                json.dumps(record.get("credential_key_names", {}), ensure_ascii=False),
                record["request_hash"],
                record.get("response_hash", ""),
                json.dumps(record.get("usage", {}), ensure_ascii=False),
                int(record["timeout_seconds"]),
                record.get("error_code", ""),
                record.get("error_detail", ""),
                record.get("started_at", timestamp),
                record.get("completed_at", ""),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def get_model_provider_smoke(smoke_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_model_provider_smokes where id = ?",
            (smoke_id,),
        ).fetchone()
    return decode_model_provider_smoke(row) if row else None


def get_model_provider_smoke_by_key(smoke_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_model_provider_smokes where smoke_key = ?",
            (smoke_key,),
        ).fetchone()
    return decode_model_provider_smoke(row) if row else None


def decode_model_provider_smoke(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["credential_key_names"] = json_loads_safe(item.get("credential_key_names"), {})
    item["usage"] = json_loads_safe(item.get("usage"), {})
    if not item.get("transport_status"):
        item["transport_status"] = "failed" if item.get("status") == "failed_transport" else "passed"
    if not item.get("protocol_status"):
        item["protocol_status"] = (
            "not_run"
            if item["transport_status"] != "passed"
            else "passed"
            if item.get("error_code") == "smoke_response_mismatch" or item.get("status") == "passed"
            else "failed"
        )
    if not item.get("marker_status"):
        item["marker_status"] = (
            "not_run"
            if item["protocol_status"] != "passed"
            else "passed"
            if item.get("status") == "passed"
            else "failed"
        )
    return item


def add_model_provider_smoke_event(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_model_provider_smoke_events(
                smoke_id, sequence, event_type, status, details, created_at
            ) values(?, ?, ?, ?, ?, ?)
            """,
            (
                int(record["smoke_id"]),
                int(record["sequence"]),
                record["event_type"],
                record["status"],
                json.dumps(record.get("details", {}), ensure_ascii=False),
                record.get("created_at", now_iso()),
            ),
        )
        return int(cursor.lastrowid)


def list_model_provider_smoke_events(smoke_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from harness_model_provider_smoke_events
            where smoke_id = ? order by sequence, id
            """,
            (smoke_id,),
        ).fetchall()
    return [decode_model_provider_smoke_event(row) for row in rows]


def decode_model_provider_smoke_event(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["details"] = json_loads_safe(item.get("details"), {})
    return item


def add_model_dag_run(record: dict) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_model_dag_runs(
                schedule_id, plan_id, run_key, status, max_parallel,
                adapter_policy, started_at, completed_at, summary,
                created_at, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(record["schedule_id"]),
                int(record["plan_id"]),
                record["run_key"],
                record.get("status", "running"),
                int(record.get("max_parallel") or 1),
                json.dumps(record.get("adapter_policy", {}), ensure_ascii=False),
                record.get("started_at", timestamp),
                record.get("completed_at", ""),
                json.dumps(record.get("summary", {}), ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def get_model_dag_run(run_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_model_dag_runs where id = ?",
            (run_id,),
        ).fetchone()
    return decode_model_dag_run(row) if row else None


def get_model_dag_run_by_schedule(schedule_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "select * from harness_model_dag_runs where schedule_id = ?",
            (schedule_id,),
        ).fetchone()
    return decode_model_dag_run(row) if row else None


def update_model_dag_run(
    run_id: int,
    *,
    status: str,
    completed_at: str = "",
    summary: dict | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            update harness_model_dag_runs
            set status = ?, completed_at = ?, summary = ?, updated_at = ?
            where id = ?
            """,
            (
                status,
                completed_at,
                json.dumps(summary or {}, ensure_ascii=False),
                now_iso(),
                run_id,
            ),
        )


def decode_model_dag_run(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["adapter_policy"] = json_loads_safe(item.get("adapter_policy"), {})
    item["summary"] = json_loads_safe(item.get("summary"), {})
    return item


def add_model_dag_trace(record: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into harness_model_dag_traces(
                run_id, schedule_id, plan_id, wave_index, trace_id,
                node_id, role_id, context_id, invocation_id, mode,
                provider, model, status, error_code, request_hash,
                response_hash, candidate_hash, cassette_relpath,
                input_tokens, output_tokens, elapsed_ms,
                observed_concurrency, parallel_observed,
                started_at, finished_at, details, created_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(record["run_id"]),
                int(record["schedule_id"]),
                int(record["plan_id"]),
                int(record["wave_index"]),
                record["trace_id"],
                record["node_id"],
                record["role_id"],
                int(record["context_id"]),
                int(record["invocation_id"]),
                record["mode"],
                record.get("provider", ""),
                record.get("model", ""),
                record["status"],
                record.get("error_code", ""),
                record.get("request_hash", ""),
                record.get("response_hash", ""),
                record.get("candidate_hash", ""),
                record.get("cassette_relpath", ""),
                int(record.get("input_tokens") or 0),
                int(record.get("output_tokens") or 0),
                int(record.get("elapsed_ms") or 0),
                int(record.get("observed_concurrency") or 1),
                1 if record.get("parallel_observed") else 0,
                record["started_at"],
                record["finished_at"],
                json.dumps(record.get("details", {}), ensure_ascii=False),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def list_model_dag_traces(run_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from harness_model_dag_traces
            where run_id = ? order by wave_index, id
            """,
            (run_id,),
        ).fetchall()
    return [decode_model_dag_trace(row) for row in rows]


def decode_model_dag_trace(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["details"] = json_loads_safe(item.get("details"), {})
    item["parallel_observed"] = bool(item.get("parallel_observed"))
    return item
