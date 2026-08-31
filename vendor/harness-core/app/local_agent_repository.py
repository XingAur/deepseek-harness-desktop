from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import math
import os
import re
import stat
import sys
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from app import database
from app.codex_cli_worker import STABLE_WORKER_ERROR_CODES, validate_protocol_rejection_audit
from app.local_agent_contract import (
    LocalAgentTask,
    assert_local_agent_task_is_current,
    load_local_agent_task_bytes,
    validate_local_agent_task_key,
)
from app.runtime_policy import (
    LocalAgentActivationPreflight,
    verify_local_agent_activation_preflight,
)
from app.repair_learning import (
    LearningRuleState,
    RootCauseKind,
    RuleObservationOutcome,
    build_current_task_rule,
    canonical_rule_bytes,
    derive_task_learning_context,
    validate_rule_payload,
)
from app.sensitive_text import (
    contains_sensitive_text,
    normalize_sensitive_text,
    redact_sensitive_mapping,
    redact_sensitive_text,
    validate_audit_alias,
)
from app.worktree_executor import SafeGitBoundary, capture_local_agent_tree_snapshot
from app.worktree_lifecycle import capture_git_metadata


_STORAGE_INVALID = "local_agent_storage_invalid"
_AUTHORIZATION_CONSUMED = "local_agent_authorization_already_consumed"
_PROJECT_RUN_ACTIVE = "local_agent_project_run_active"
_STATE_TRANSITION_INVALID = "local_agent_state_transition_invalid"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_AUTHORIZATION_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROCESS_START_IDENTITY = re.compile(r"^darwin-proc-bsdinfo-v1:[1-9][0-9]*:[0-9]{1,6}$")
_LOCAL_AGENT_WORKTREE_ROOT = re.compile(r"^/private/tmp/his_harness_stage_f_[A-Za-z0-9_-]{1,96}$")
_LEARNING_SOURCE_CODES = {
    "run_observation": 1,
    "review_observation": 2,
    "offline_import": 3,
}
_LEARNING_ROOT_CAUSE_CODES = {
    "verification_failure": 1,
    "review_gap": 2,
    "path_coverage_gap": 3,
    "contract_mismatch": 4,
    "implementation_defect": 5,
}
_LEARNING_SOURCE_STATUSES = {
    "run_observation": frozenset({"verifying", "failed_verification"}),
    "review_observation": frozenset({"reviewing", "changes_requested"}),
    "offline_import": frozenset({
        "failed_verification", "awaiting_human_confirmation", "changes_requested",
    }),
}
_LEARNING_OBSERVATION_STATUSES = {
    "matched": frozenset({"awaiting_human_confirmation"}),
    "not_matched": frozenset({"changes_requested", "awaiting_human_confirmation"}),
}
_LEARNING_SAFE_SUMMARY_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_LEARNING_UNTRUSTED_CONTENT = re.compile(
    r"(?:untrusted_task_data_json|diff\s+--git|\bprompt\b)",
    re.IGNORECASE,
)
_LEARNING_PATCH_CONTENT = re.compile(
    r"(?:"
    r"^[^\S\r\n]*---\s+[^\n]+\n[^\S\r\n]*\+\+\+\s+[^\n]+(?:\n|$)"
    r"|^[^\S\r\n]*@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@"
    r"|^[^\S\r\n]*\*\*\*\s+[^\n]+\n[^\S\r\n]*---\s+[^\n]+(?:\n|$)"
    r"|^[^\S\r\n]*Index:\s+[^\n]+(?:\n|$)"
    r"|^[^\S\r\n]*GIT binary patch(?:\n|$)"
    r"|^[^\S\r\n]*Binary files\s+.+\s+differ(?:\n|$)"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_LEARNING_STANDALONE_SECRET = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"gh[opurs]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|xapp-[A-Za-z0-9-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|ASIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9_-]{8,}"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_MAX_ARTIFACT_BYTES = 1 << 40
_CONTROL = ".harness_local_agent_control"
_FINALIZATION_TTL_SECONDS = 300
_RUN_STATUSES = frozenset({
    "created", "workspace_ready", "worker_running", "verifying", "reviewing",
    "awaiting_human_confirmation", "locally_applied", "interrupted", "failed_scope",
    "failed_worker", "cancelled", "failed_verification", "changes_requested",
    "failed_review", "confirmation_expired", "failed_workspace",
    "attempts_exhausted",
})
_ATTEMPT_STATUSES = frozenset({
    "starting", "worker_running", "completed", "failed_scope", "failed_worker",
    "cancelled", "interrupted",
})
_ACTIVE_ATTEMPT_STATUSES = frozenset({"starting", "worker_running"})
_ALLOWED_TRANSITIONS = {
    "created": frozenset({"workspace_ready", "failed_workspace"}),
    "failed_workspace": frozenset({"workspace_ready"}),
    "workspace_ready": frozenset({"worker_running", "failed_verification"}),
    "worker_running": frozenset({"verifying", "failed_scope", "failed_worker", "cancelled", "interrupted"}),
    "verifying": frozenset({"reviewing", "failed_verification"}),
    "reviewing": frozenset({"awaiting_human_confirmation", "changes_requested", "failed_review"}),
    "awaiting_human_confirmation": frozenset({"changes_requested", "confirmation_expired", "failed_review"}),
    "interrupted": frozenset({"worker_running"}),
    "failed_worker": frozenset({"worker_running"}),
    "failed_verification": frozenset({"worker_running"}),
    "changes_requested": frozenset({"worker_running", "failed_review"}),
}
_ATTEMPT_START_STATES = frozenset({"workspace_ready", "interrupted", "failed_worker", "failed_verification", "changes_requested"})
_ATTEMPT_BUDGET = 3
_ATTEMPT_BUDGET_FAILURE_STATES = frozenset({
    "interrupted", "failed_worker", "failed_verification", "changes_requested",
})
_ATTEMPT_COMPLETION_TARGETS = {
    "completed": "verifying",
    "failed_scope": "failed_scope",
    "failed_worker": "failed_worker",
    "cancelled": "cancelled",
    "interrupted": "interrupted",
}
_FINALIZATION_ISSUER = object()
_REVIEW_LEARNING_OBSERVATION_ISSUER = object()
_LOCAL_APPLY_SERVICE_ISSUER = object()
_LOCAL_APPLY_COMPLETION_ISSUER = object()
_FINALIZATION_AUTHORITATIVE_KINDS = (
    "task_contract", "worker_patch", "worker_change_manifest",
    "verification_manifest", "final_diff", "final_patch",
    "final_verification", "final_manifest",
)
_CONFIRMATION_ARTIFACT_KINDS = (*_FINALIZATION_AUTHORITATIVE_KINDS, "final_review", "review_seal")


class _ReviewFinalizationCapability:
    """Identity-only capability; all authority lives in a repository registry."""

    __slots__ = ("__weakref__",)

    def __new__(cls, issuer: object = None):
        if issuer is not _FINALIZATION_ISSUER:
            raise TypeError("local_agent_finalization_capability_invalid")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("local_agent_finalization_capability_invalid")

    def __copy__(self):
        raise TypeError("local_agent_finalization_capability_invalid")

    def __deepcopy__(self, memo: object):
        raise TypeError("local_agent_finalization_capability_invalid")

    def __reduce__(self):
        raise TypeError("local_agent_finalization_capability_invalid")


class _ReviewLearningObservationCapability:
    """One-shot staging token tied to one sealed approved review."""

    __slots__ = ("__weakref__",)

    def __new__(cls, issuer: object = None):
        if issuer is not _REVIEW_LEARNING_OBSERVATION_ISSUER:
            raise TypeError("local_agent_review_learning_capability_invalid")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("local_agent_review_learning_capability_invalid")

    def __copy__(self):
        raise TypeError("local_agent_review_learning_capability_invalid")

    def __deepcopy__(self, memo: object):
        raise TypeError("local_agent_review_learning_capability_invalid")

    def __reduce__(self):
        raise TypeError("local_agent_review_learning_capability_invalid")


class _LocalApplyServiceCapability:
    __slots__ = ("__weakref__",)

    def __new__(cls, issuer: object = None):
        if issuer is not _LOCAL_APPLY_SERVICE_ISSUER:
            raise TypeError("local_agent_service_capability_invalid")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("local_agent_service_capability_invalid")

    def __copy__(self):
        raise TypeError("local_agent_service_capability_invalid")

    def __deepcopy__(self, memo: object):
        raise TypeError("local_agent_service_capability_invalid")

    def __reduce__(self):
        raise TypeError("local_agent_service_capability_invalid")


class _LocalApplyCompletionCapability:
    __slots__ = ("__weakref__",)

    def __new__(cls, issuer: object = None):
        if issuer is not _LOCAL_APPLY_COMPLETION_ISSUER:
            raise TypeError("local_agent_completion_capability_invalid")
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("local_agent_completion_capability_invalid")

    def __copy__(self):
        raise TypeError("local_agent_completion_capability_invalid")

    def __deepcopy__(self, memo: object):
        raise TypeError("local_agent_completion_capability_invalid")

    def __reduce__(self):
        raise TypeError("local_agent_completion_capability_invalid")


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32), ("status", ctypes.c_uint32), ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32), ("ppid", ctypes.c_uint32), ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32), ("ruid", ctypes.c_uint32), ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32), ("svgid", ctypes.c_uint32), ("reserved", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32), ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32), ("pjobc", ctypes.c_uint32), ("tdev", ctypes.c_uint32),
        ("tpgid", ctypes.c_uint32), ("nice", ctypes.c_int32), ("start_seconds", ctypes.c_uint64),
        ("start_microseconds", ctypes.c_uint64),
    ]


class LocalAgentRunRepository:
    """Transactional, append-only state surface for one local-agent task contract."""

    def __init__(
        self,
        database_path: Path,
        *,
        connection_factory: Callable[[], database.sqlite3.Connection] | None = None,
    ) -> None:
        if not isinstance(database_path, Path):
            raise TypeError("database_path must be a Path")
        if connection_factory is not None and not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._database_path = database_path.expanduser().resolve()
        self._connection_factory = connection_factory
        self._review_finalization_lock = threading.Lock()
        self._review_finalizations: weakref.WeakKeyDictionary[_ReviewFinalizationCapability, tuple[object, ...]] = weakref.WeakKeyDictionary()
        self._review_learning_observation_lock = threading.Lock()
        self._review_learning_observations: weakref.WeakKeyDictionary[_ReviewLearningObservationCapability, tuple[object, ...]] = weakref.WeakKeyDictionary()
        self._review_learning_by_finalization: weakref.WeakKeyDictionary[_ReviewFinalizationCapability, _ReviewLearningObservationCapability] = weakref.WeakKeyDictionary()
        self._local_apply_lock = threading.Lock()
        self._local_apply_services: weakref.WeakKeyDictionary[_LocalApplyServiceCapability, weakref.ReferenceType[object]] = weakref.WeakKeyDictionary()
        self._local_apply_completions: weakref.WeakKeyDictionary[_LocalApplyCompletionCapability, tuple[object, ...]] = weakref.WeakKeyDictionary()

    def _connect(self) -> database.sqlite3.Connection:
        if self._connection_factory is not None:
            return self._connection_factory()
        return database.connect_database(self._database_path)

    def open_learning_connection(self) -> database.sqlite3.Connection:
        """Return this run repository's explicit local database connection.

        Repair-learning persistence is intentionally tied to the same injected
        local database.  It must not fall back to the process-global DB path.
        The learning repository owns and closes each returned connection.
        """

        return self._connect()

    def read_learning_binding(
        self,
        task: LocalAgentTask,
        *,
        run_id: int,
        attempt_id: int | None = None,
    ) -> dict[str, object]:
        """Read the current durable identity required for repair learning.

        This is intentionally a narrow read-only projection: it verifies the
        supplied contract against disk and the persisted run, requires the
        current attempt when one is named, and exposes no authorization or
        source-worktree path.  The workspace value is a one-way fingerprint
        derived from the durable binding record.
        """

        run_id = _positive_id(run_id)
        attempt_id = _optional_positive_id(attempt_id)
        try:
            assert_local_agent_task_is_current(task)
            with self._connect() as connection:
                connection.execute("begin immediate")
                return _learning_binding_in_transaction(
                    connection,
                    task=task,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    allowed_run_statuses=None,
                )
        except ValueError as error:
            if str(error) == _STORAGE_INVALID:
                raise
            raise ValueError(_STORAGE_INVALID) from None
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def record_learning_retrospective(
        self,
        task: LocalAgentTask,
        *,
        source_key: str,
        run_id: int,
        attempt_id: int,
        source_kind: str,
        root_cause_kind: str,
        safe_summary: Mapping[str, object],
        task_context: Mapping[str, object],
        allowed_run_statuses: frozenset[str],
    ) -> dict[str, object]:
        """Atomically bind one retrospective to its durable current attempt.

        This deliberately exposes a bounded domain operation instead of a SQL
        callback: callers cannot write an arbitrary learning row after the
        durable run/attempt/workspace check has completed.
        """

        source_key = _safe_alias(source_key)
        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        source_kind, root_cause_kind = _safe_alias(source_kind), _safe_alias(root_cause_kind)
        statuses = _learning_statuses(allowed_run_statuses)
        expected_statuses = (
            frozenset({"failed_verification", "changes_requested"})
            if source_kind == "offline_import"
            else _LEARNING_SOURCE_STATUSES.get(source_kind)
        )
        if (
            source_kind not in _LEARNING_SOURCE_CODES
            or root_cause_kind not in _LEARNING_ROOT_CAUSE_CODES
            or statuses != expected_statuses
            or source_key != _learning_source_key(
                run_id, attempt_id, source_kind, root_cause_kind,
            )
        ):
            raise ValueError("repair_learning_input_invalid")
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                binding = _learning_binding_in_transaction(
                    connection,
                    task=task,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    allowed_run_statuses=statuses,
                )
                safe_summary_json = _encode_learning_safe_summary(safe_summary)
                task_context_json = _encode_learning_task_context(
                    task_context,
                    task=task,
                    run_id=int(binding["run_id"]),
                )
                values = (
                    source_key, run_id, attempt_id, source_kind, root_cause_kind,
                    safe_summary_json, task_context_json, database.now_iso(),
                )
                connection.execute(
                    """
                    insert into repair_retrospectives(
                        source_key, run_id, attempt_id, source_kind, root_cause_kind,
                        safe_summary_json, task_context_json, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(source_key) do nothing
                    """,
                    values,
                )
                row = connection.execute(
                    "select * from repair_retrospectives where source_key=?", (source_key,),
                ).fetchone()
                if row is None or tuple(row[key] for key in (
                    "source_key", "run_id", "attempt_id", "source_kind",
                    "root_cause_kind", "safe_summary_json", "task_context_json",
                )) != values[:7]:
                    raise ValueError("repair_learning_replay_conflict")
                return _learning_retrospective_from_row(row)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def record_learning_retrospective_with_rule(
        self,
        task: LocalAgentTask,
        *,
        source_key: str,
        run_id: int,
        attempt_id: int,
        source_kind: str,
        root_cause_kind: str,
        safe_summary: Mapping[str, object],
        task_context: Mapping[str, object],
        rule_payload: Mapping[str, object],
        allowed_run_statuses: frozenset[str],
    ) -> dict[str, object]:
        """Atomically persist one bounded retrospective and its current rule."""

        source_key = _safe_alias(source_key)
        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        source_kind, root_cause_kind = _safe_alias(source_kind), _safe_alias(root_cause_kind)
        statuses = _learning_statuses(allowed_run_statuses)
        expected_statuses = (
            frozenset({"failed_verification", "changes_requested"})
            if source_kind == "offline_import"
            else _LEARNING_SOURCE_STATUSES.get(source_kind)
        )
        if (
            source_kind not in _LEARNING_SOURCE_CODES
            or root_cause_kind not in _LEARNING_ROOT_CAUSE_CODES
            or statuses != expected_statuses
            or source_key != _learning_source_key(
                run_id, attempt_id, source_kind, root_cause_kind,
            )
        ):
            raise ValueError("repair_learning_input_invalid")
        try:
            normalized_rule = validate_rule_payload(rule_payload)
            if (
                normalized_rule["state"] != LearningRuleState.ACTIVE_CURRENT_TASK.value
                or normalized_rule["source_kind"] != source_kind
                or normalized_rule["root_cause"] != root_cause_kind
            ):
                raise ValueError
            rule_json = canonical_rule_bytes(normalized_rule).decode("utf-8")
            rule_key = _learning_storage_rule_key(normalized_rule)
        except (TypeError, ValueError, UnicodeError):
            raise ValueError("repair_learning_input_invalid") from None
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                binding = _learning_binding_in_transaction(
                    connection,
                    task=task,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    allowed_run_statuses=statuses,
                )
                safe_summary_json = _encode_learning_safe_summary(safe_summary)
                task_context_json = _encode_learning_task_context(
                    task_context,
                    task=task,
                    run_id=int(binding["run_id"]),
                )
                values = (
                    source_key, run_id, attempt_id, source_kind, root_cause_kind,
                    safe_summary_json, task_context_json, database.now_iso(),
                )
                connection.execute(
                    """
                    insert into repair_retrospectives(
                        source_key, run_id, attempt_id, source_kind, root_cause_kind,
                        safe_summary_json, task_context_json, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(source_key) do nothing
                    """,
                    values,
                )
                retrospective_row = connection.execute(
                    "select * from repair_retrospectives where source_key=?", (source_key,),
                ).fetchone()
                if retrospective_row is None or tuple(retrospective_row[key] for key in (
                    "source_key", "run_id", "attempt_id", "source_kind",
                    "root_cause_kind", "safe_summary_json", "task_context_json",
                )) != values[:7]:
                    raise ValueError("repair_learning_replay_conflict")
                retrospective = _learning_retrospective_from_row(retrospective_row)
                now = database.now_iso()
                connection.execute(
                    """
                    insert into repair_learning_rules(
                        rule_key, rule_json, state, origin_retrospective_id,
                        active_run_id, created_at, updated_at
                    ) values (?, ?, 'active_current_task', ?, ?, ?, ?)
                    on conflict(rule_key) do nothing
                    """,
                    (rule_key, rule_json, int(retrospective["id"]), run_id, now, now),
                )
                rule_row = connection.execute(
                    "select * from repair_learning_rules where rule_key=?", (rule_key,),
                ).fetchone()
                if rule_row is None or (
                    int(rule_row["origin_retrospective_id"]) != int(retrospective["id"])
                    or rule_row["active_run_id"] != run_id
                    or rule_row["rule_json"] != rule_json
                    or rule_row["state"] != LearningRuleState.ACTIVE_CURRENT_TASK.value
                ):
                    raise ValueError("repair_learning_replay_conflict")
                return {
                    "retrospective": retrospective,
                    "rule": _decode_learning_rule_json(rule_row["rule_json"]),
                }
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def record_awaiting_human_correction(
        self,
        task: LocalAgentTask,
        *,
        source_key: str,
        run_id: int,
        attempt_id: int,
        root_cause_kind: str,
        safe_summary: Mapping[str, object],
    ) -> dict[str, object]:
        """Atomically record an awaiting correction and revoke its apply token.

        This is intentionally a fixed domain operation.  It accepts neither a
        caller callback nor arbitrary rule/context/event data, so the writer
        reservation covers all durable facts that must block local apply.
        """

        source_key = _safe_alias(source_key)
        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        root_cause_kind = _safe_alias(root_cause_kind)
        if (
            root_cause_kind not in _LEARNING_ROOT_CAUSE_CODES
            or source_key != _learning_source_key(
                run_id, attempt_id, "offline_import", root_cause_kind,
            )
        ):
            raise ValueError("repair_learning_input_invalid")
        try:
            root_cause = RootCauseKind(root_cause_kind)
            safe_summary_json = _encode_learning_safe_summary(safe_summary)
            rule = build_current_task_rule(
                derive_task_learning_context(task, run_id=run_id),
                root_cause=root_cause,
                actions=("replan_before_execute", "verification_replay", "reviewer_focus"),
                source_kind="offline_import",
            )
            rule_payload = rule.to_payload()
            rule_json = canonical_rule_bytes(rule_payload).decode("utf-8")
            storage_rule_key = _learning_storage_rule_key(rule_payload)
        except (TypeError, ValueError, UnicodeError):
            raise ValueError("repair_learning_input_invalid") from None
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                binding = _learning_binding_in_transaction(
                    connection,
                    task=task,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    allowed_run_statuses=frozenset({"awaiting_human_confirmation"}),
                )
                task_context_json = _encode_learning_task_context(
                    _learning_task_context_payload(task, run_id=int(binding["run_id"])),
                    task=task,
                    run_id=int(binding["run_id"]),
                )
                values = (
                    source_key, run_id, attempt_id, "offline_import", root_cause_kind,
                    safe_summary_json, task_context_json, database.now_iso(),
                )
                connection.execute(
                    """
                    insert into repair_retrospectives(
                        source_key, run_id, attempt_id, source_kind, root_cause_kind,
                        safe_summary_json, task_context_json, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(source_key) do nothing
                    """,
                    values,
                )
                retrospective_row = connection.execute(
                    "select * from repair_retrospectives where source_key=?", (source_key,),
                ).fetchone()
                if retrospective_row is None or tuple(retrospective_row[key] for key in (
                    "source_key", "run_id", "attempt_id", "source_kind",
                    "root_cause_kind", "safe_summary_json", "task_context_json",
                )) != values[:7]:
                    raise ValueError("repair_learning_replay_conflict")
                retrospective = _learning_retrospective_from_row(retrospective_row)
                now = database.now_iso()
                connection.execute(
                    """
                    insert into repair_learning_rules(
                        rule_key, rule_json, state, origin_retrospective_id,
                        active_run_id, created_at, updated_at
                    ) values (?, ?, 'active_current_task', ?, ?, ?, ?)
                    on conflict(rule_key) do nothing
                    """,
                    (
                        storage_rule_key, rule_json, int(retrospective["id"]),
                        run_id, now, now,
                    ),
                )
                rule_row = connection.execute(
                    "select * from repair_learning_rules where rule_key=?", (storage_rule_key,),
                ).fetchone()
                if rule_row is None or (
                    int(rule_row["origin_retrospective_id"]) != int(retrospective["id"])
                    or rule_row["active_run_id"] != run_id
                ):
                    raise ValueError("repair_learning_replay_conflict")
                confirmation = connection.execute(
                    "select * from local_agent_apply_confirmations where run_id=?", (run_id,),
                ).fetchone()
                if (
                    confirmation is None
                    or confirmation["attempt_id"] != attempt_id
                    or confirmation["status"] != "issued"
                    or connection.execute(
                        "select 1 from local_agent_apply_operations where run_id=?", (run_id,),
                    ).fetchone() is not None
                ):
                    raise ValueError(_STORAGE_INVALID)
                if connection.execute(
                    """update local_agent_apply_confirmations set status='expired', consumed_at=?
                       where run_id=? and attempt_id=? and status='issued'""",
                    (database.now_iso(), run_id, attempt_id),
                ).rowcount != 1:
                    raise ValueError(_STORAGE_INVALID)
                if connection.execute(
                    """update local_agent_runs set status='changes_requested', summary_json=?, updated_at=?
                       where id=? and status='awaiting_human_confirmation'""",
                    (_encode_safe_mapping({"correction_kind": root_cause_kind}), database.now_iso(), run_id),
                ).rowcount != 1:
                    raise ValueError(_STATE_TRANSITION_INVALID)
                _append_event_in_transaction(
                    connection,
                    run_id,
                    attempt_id,
                    "confirmation_invalidated_for_correction",
                    _encode_safe_mapping({"correction_kind": root_cause_kind}),
                )
                return {
                    "retrospective": retrospective,
                    "rule": _decode_learning_rule_json(rule_row["rule_json"]),
                }
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def record_learning_observation(
        self,
        task: LocalAgentTask,
        *,
        rule_id: int,
        run_id: int,
        attempt_id: int,
        task_key: str,
        workspace_fingerprint: str,
        outcome: RuleObservationOutcome | str,
        evidence: Mapping[str, object],
        allowed_run_statuses: frozenset[str],
    ) -> dict[str, object]:
        """Atomically bind one observation to the checked durable workspace."""

        rule_id, run_id, attempt_id = _positive_id(rule_id), _positive_id(run_id), _positive_id(attempt_id)
        task_key, workspace_fingerprint = _safe_task_key(task_key), _safe_alias(workspace_fingerprint)
        statuses = _learning_statuses(allowed_run_statuses)
        try:
            outcome_value = RuleObservationOutcome(outcome).value
        except (TypeError, ValueError):
            raise ValueError("repair_learning_input_invalid") from None
        if statuses != _LEARNING_OBSERVATION_STATUSES[outcome_value]:
            raise ValueError("repair_learning_input_invalid")
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                binding = _learning_binding_in_transaction(
                    connection,
                    task=task,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    allowed_run_statuses=statuses,
                )
                if binding["workspace_fingerprint"] != workspace_fingerprint:
                    raise ValueError("repair_learning_input_invalid")
                if binding["task_key"] != task_key:
                    raise ValueError("repair_learning_input_invalid")
                rule = connection.execute(
                    "select * from repair_learning_rules where id=?", (rule_id,),
                ).fetchone()
                if rule is None or not _learning_rule_matches_durable_task(
                    rule,
                    task=task,
                    binding=binding,
                ):
                    raise ValueError("repair_learning_input_invalid")
                evidence_json = _encode_learning_observation_evidence(
                    evidence,
                    outcome=outcome_value,
                )
                observed_at = database.now_iso()
                cursor = connection.execute(
                    """
                    insert into repair_learning_observations(
                        rule_id, run_id, attempt_id, task_key,
                        workspace_fingerprint, outcome, evidence_json, observed_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(rule_id, run_id, attempt_id, outcome) do nothing
                    """,
                    (
                        rule_id, run_id, attempt_id, task_key,
                        workspace_fingerprint, outcome_value, evidence_json, observed_at,
                    ),
                )
                row = connection.execute(
                    """
                    select * from repair_learning_observations
                    where rule_id=? and run_id=? and attempt_id=? and outcome=?
                    """,
                    (rule_id, run_id, attempt_id, outcome_value),
                ).fetchone()
                if row is None or tuple(row[key] for key in (
                    "task_key", "workspace_fingerprint", "evidence_json",
                )) != (task_key, workspace_fingerprint, evidence_json):
                    raise ValueError("repair_learning_replay_conflict")
                if cursor.rowcount == 1:
                    _learning_refresh_counts_and_suspend(connection, rule_id, observed_at)
                return _learning_observation_from_row(row)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def consume_preflight(self, task: LocalAgentTask, preflight: LocalAgentActivationPreflight) -> dict[str, object]:
        assert_local_agent_task_is_current(task)
        try:
            authorization_hash = verify_local_agent_activation_preflight(preflight)
        except Exception:
            raise ValueError(_STORAGE_INVALID)
        if _AUTHORIZATION_HASH.fullmatch(authorization_hash) is None:
            raise ValueError(_STORAGE_INVALID)
        identity_json = _encode_safe_mapping({
            "git_dir": list(task.git_dir_identity), "git_entry": list(task.git_entry_identity),
            "repository_root": list(task.repository_root_identity),
        })
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                if connection.execute("select 1 from local_agent_runs where authorization_hash = ?", (authorization_hash,)).fetchone() is not None:
                    raise ValueError(_AUTHORIZATION_CONSUMED)
                now = database.now_iso()
                try:
                    cursor = connection.execute(
                        """insert into local_agent_runs(task_key, contract_hash, authorization_hash, project_identity_json, initial_head, status, created_at, updated_at)
                           values(?, ?, ?, ?, ?, 'created', ?, ?)""",
                        (task.task_key, task.contract_hash, authorization_hash, identity_json, task.initial_head, now, now),
                    )
                except database.sqlite3.IntegrityError as error:
                    if connection.execute("select 1 from local_agent_runs where authorization_hash = ?", (authorization_hash,)).fetchone() is not None:
                        raise ValueError(_AUTHORIZATION_CONSUMED) from error
                    raise ValueError(_STORAGE_INVALID) from error
                try:
                    # A lease is deliberately acquired in the same immediate
                    # transaction as capability consumption.  Retryable runs
                    # retain it; terminal transitions release it below.
                    connection.execute(
                        "insert into local_agent_project_leases(project_identity_json, run_id, created_at) values(?, ?, ?)",
                        (identity_json, cursor.lastrowid, now),
                    )
                except database.sqlite3.IntegrityError as error:
                    raise ValueError(_PROJECT_RUN_ACTIVE) from error
                return _run_from_row(connection.execute("select * from local_agent_runs where id = ?", (cursor.lastrowid,)).fetchone())
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def bind_workspace(self, run_id: int, binding: Mapping[str, object]) -> dict[str, object]:
        """Persist the complete no-follow workspace binding before prompting."""
        run_id = _positive_id(run_id)
        binding_json = _encode_workspace_binding(binding)
        path = binding.get("worktree_path") if isinstance(binding, Mapping) else None
        if not isinstance(path, str) or not path.startswith("/private/tmp/his_harness_stage_f_") or len(path) > 1024:
            raise ValueError(_STORAGE_INVALID)
        if binding.get("task_artifact") != f"{_CONTROL}/run_{run_id}/task.json":
            raise ValueError(_STORAGE_INVALID)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                run = _require_run(connection, run_id)
                if run["status"] not in {"created", "failed_workspace", "failed_verification"}:
                    raise ValueError(_STORAGE_INVALID)
                now = database.now_iso()
                if connection.execute("update local_agent_runs set worktree_path=?, status='workspace_ready', updated_at=? where id=? and status in ('created','failed_workspace','failed_verification')", (path, now, run_id)).rowcount != 1:
                    raise ValueError(_STORAGE_INVALID)
                connection.execute("insert or replace into local_agent_workspace_bindings(run_id, binding_json, created_at) values(?, ?, ?)", (run_id, binding_json, now))
                connection.execute("insert into local_agent_workspace_binding_events(run_id, binding_json, created_at) values(?, ?, ?)", (run_id, binding_json, now))
                _append_event_in_transaction(connection, run_id, None, "workspace_bound", _encode_safe_mapping({"bound": True}))
                return _require_run(connection, run_id)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def fail_workspace(self, run_id: int, reason: str = "workspace_prepare_failed") -> dict[str, object]:
        run_id, reason = _positive_id(run_id), _safe_alias(reason)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                _append_event_in_transaction(connection, run_id, None, "workspace_failed", _encode_safe_mapping({"reason": reason}))
                return _transition_in_transaction(connection, run_id, "created", "failed_workspace", _encode_safe_mapping({"reason": reason}))
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def start_attempt(self, run_id: int) -> dict[str, object]:
        run_id = _positive_id(run_id)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                run = _require_run(connection, run_id)
                if run["status"] not in _ATTEMPT_START_STATES:
                    raise ValueError(_STORAGE_INVALID)
                if connection.execute("select 1 from local_agent_attempts where run_id = ? and status in ('starting', 'worker_running')", (run_id,)).fetchone() is not None:
                    raise ValueError(_STORAGE_INVALID)
                next_number = int(connection.execute("select coalesce(max(attempt_no), 0) + 1 from local_agent_attempts where run_id = ?", (run_id,)).fetchone()[0])
                changed = connection.execute("update local_agent_runs set status = 'worker_running', updated_at = ? where id = ? and status = ?", (database.now_iso(), run_id, run["status"])).rowcount
                if changed != 1:
                    raise ValueError(_STORAGE_INVALID)
                cursor = connection.execute(
                    "insert into local_agent_attempts(run_id, attempt_no, status, started_at) values(?, ?, 'starting', ?)",
                    (run_id, next_number, database.now_iso()),
                )
                return _attempt_from_row(connection.execute("select * from local_agent_attempts where id = ?", (cursor.lastrowid,)).fetchone())
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def bind_worker_identity(self, attempt_id: int, pid: int, start_identity: str) -> dict[str, object]:
        attempt_id, pid = _positive_id(attempt_id), _positive_id(pid)
        start_identity = _process_start_identity(start_identity)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                attempt = _require_attempt(connection, attempt_id)
                if attempt["status"] != "starting" or _require_run(connection, int(attempt["run_id"]))["status"] != "worker_running":
                    raise ValueError(_STORAGE_INVALID)
                if _read_process_start_identity(pid) != start_identity:
                    raise ValueError(_STORAGE_INVALID)
                if connection.execute("update local_agent_attempts set status = 'worker_running', worker_pid = ?, worker_start_identity = ? where id = ? and status = 'starting'", (pid, start_identity, attempt_id)).rowcount != 1:
                    raise ValueError(_STORAGE_INVALID)
                return _require_attempt(connection, attempt_id)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError, RuntimeError):
            raise ValueError(_STORAGE_INVALID) from None

    def abandon_starting_attempt(self, run_id: int, attempt_id: int, error_code: str = "worker_start_failed") -> dict[str, object]:
        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        error_code = _safe_alias(error_code)
        if error_code != "worker_start_failed":
            raise ValueError(_STORAGE_INVALID)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                attempt = _require_attempt(connection, attempt_id)
                if attempt["run_id"] != run_id:
                    raise ValueError(_STORAGE_INVALID)
                return _abandon_starting_attempt_in_transaction(connection, attempt, error_code)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def complete_attempt(self, attempt_id: int, status: str, error_code: str = "", summary: Mapping[str, object] | None = None) -> dict[str, object]:
        attempt_id = _positive_id(attempt_id)
        status = _safe_alias(status)
        if status not in _ATTEMPT_COMPLETION_TARGETS:
            raise ValueError(_STORAGE_INVALID)
        error_code = _safe_optional_alias(error_code)
        if (status == "completed") != (error_code == ""):
            raise ValueError(_STORAGE_INVALID)
        summary_json = _encode_safe_mapping({} if summary is None else summary)
        target = _ATTEMPT_COMPLETION_TARGETS[status]
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                attempt = _require_attempt(connection, attempt_id)
                run_id = int(attempt["run_id"])
                if attempt["status"] != "worker_running" or _require_run(connection, run_id)["status"] != "worker_running":
                    raise ValueError(_STORAGE_INVALID)
                now = database.now_iso()
                if connection.execute("update local_agent_attempts set status = ?, error_code = ?, finished_at = ? where id = ? and status = 'worker_running'", (status, error_code, now, attempt_id)).rowcount != 1:
                    raise ValueError(_STORAGE_INVALID)
                _update_run_after_attempt_in_transaction(
                    connection, run_id, attempt_id, "worker_running", target, summary_json, now,
                )
                return _require_attempt(connection, attempt_id)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def append_event(self, run_id: int, attempt_id: int | None, event_type: str, payload: Mapping[str, object]) -> dict[str, object]:
        run_id, attempt_id = _positive_id(run_id), _optional_positive_id(attempt_id)
        event_type = _safe_alias(event_type)
        if event_type == "worker_protocol_rejected":
            validate_protocol_rejection_audit(payload)
        elif event_type == "harness_decision_issued":
            _validate_harness_decision_event(payload)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                _require_run(connection, run_id)
                _require_attempt_belongs_to_run(connection, run_id, attempt_id)
                payload_json = (
                    _encode_validated_audit_mapping(payload)
                    if event_type in {"harness_decision_issued", "worker_protocol_rejected"}
                    else _encode_safe_mapping(payload)
                )
                return _append_event_in_transaction(connection, run_id, attempt_id, event_type, payload_json)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def transition(self, run_id: int, expected: str, target: str, summary: Mapping[str, object]) -> dict[str, object]:
        run_id, expected, target = _positive_id(run_id), _safe_alias(expected), _safe_alias(target)
        if target not in _ALLOWED_TRANSITIONS.get(expected, frozenset()) or expected == "worker_running":
            raise ValueError(_STATE_TRANSITION_INVALID)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                return _transition_in_transaction(connection, run_id, expected, target, _encode_safe_mapping(summary))
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def exhaust_attempt_budget(self, run_id: int) -> dict[str, object]:
        """Atomically terminalize a retryable run after its fixed attempt budget."""
        run_id = _positive_id(run_id)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                run = _require_run(connection, run_id)
                attempts = [
                    _attempt_from_row(row)
                    for row in connection.execute(
                        "select * from local_agent_attempts where run_id=? order by attempt_no",
                        (run_id,),
                    )
                ]
                if (
                    run["status"] not in _ATTEMPT_BUDGET_FAILURE_STATES
                    or len(attempts) != _ATTEMPT_BUDGET
                    or any(item["status"] in _ACTIVE_ATTEMPT_STATUSES for item in attempts)
                ):
                    raise ValueError(_STATE_TRANSITION_INVALID)
                now = database.now_iso()
                summary = _encode_safe_mapping({"reason": "attempt_budget_exhausted"})
                if connection.execute(
                    "update local_agent_runs set status='attempts_exhausted', summary_json=?, updated_at=? where id=? and status=?",
                    (summary, now, run_id, run["status"]),
                ).rowcount != 1:
                    raise ValueError(_STATE_TRANSITION_INVALID)
                _append_event_in_transaction(
                    connection,
                    run_id,
                    int(attempts[-1]["id"]),
                    "attempt_budget_exhausted",
                    summary,
                )
                connection.execute("delete from local_agent_project_leases where run_id=?", (run_id,))
                return _require_run(connection, run_id)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def add_artifact(self, run_id: int, attempt_id: int | None, kind: str, relative_path: str, sha256: str, size_bytes: int) -> dict[str, object]:
        run_id, attempt_id = _positive_id(run_id), _optional_positive_id(attempt_id)
        if not isinstance(sha256, str) or _HASH.fullmatch(sha256) is None or not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or not 0 <= size_bytes <= _MAX_ARTIFACT_BYTES:
            raise ValueError(_STORAGE_INVALID)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                _require_run(connection, run_id)
                _require_attempt_belongs_to_run(connection, run_id, attempt_id)
                cursor = connection.execute("insert into local_agent_artifacts(run_id, attempt_id, kind, relative_path, sha256, size_bytes, created_at) values(?, ?, ?, ?, ?, ?, ?)", (run_id, attempt_id, _safe_alias(kind), _safe_relative_path(relative_path), sha256, size_bytes, database.now_iso()))
                return _artifact_from_row(connection.execute("select * from local_agent_artifacts where id = ?", (cursor.lastrowid,)).fetchone())
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def _bind_local_apply_service(self, owner: object) -> _LocalApplyServiceCapability:
        if type(owner).__module__ != "app.local_agent_confirmation" or type(owner).__name__ != "LocalAgentConfirmationService":
            raise TypeError("local_agent_service_capability_invalid")
        capability = _LocalApplyServiceCapability(_LOCAL_APPLY_SERVICE_ISSUER)
        with self._local_apply_lock:
            self._local_apply_services[capability] = weakref.ref(owner)
        return capability

    def _require_local_apply_service(self, owner: object, capability: object) -> None:
        if not isinstance(capability, _LocalApplyServiceCapability):
            raise ValueError("local_agent_confirmation_invalid")
        with self._local_apply_lock:
            reference = self._local_apply_services.get(capability)
        if reference is None or reference() is not owner:
            raise ValueError("local_agent_confirmation_invalid")

    def _issue_apply_confirmation(
        self,
        *,
        service_owner: object,
        service_capability: object,
        run_id: int,
        attempt_id: int,
        token_hash: str,
        requested_by: str,
        binding: Mapping[str, object],
        issued_at: str,
        expires_at: str,
    ) -> None:
        """Persist one opaque, run-bound confirmation after current DB checks."""

        self._require_local_apply_service(service_owner, service_capability)
        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        token_hash = _authorization_digest(token_hash)
        requested_by = _safe_alias(requested_by)
        binding_json = _encode_confirmation_binding(binding)
        issued = _timestamp(issued_at)
        expires = _timestamp(expires_at)
        if _timestamp_datetime(expires) <= _timestamp_datetime(issued):
            raise ValueError(_STORAGE_INVALID)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                run, attempt, artifacts = _load_confirmation_facts(connection, run_id)
                if run["status"] != "awaiting_human_confirmation" or attempt["id"] != attempt_id:
                    raise ValueError(_STATE_TRANSITION_INVALID)
                expected = binding.get("artifacts") if isinstance(binding, Mapping) else None
                if expected != [_public_review_artifact_fact(item) for item in artifacts]:
                    raise ValueError(_STORAGE_INVALID)
                try:
                    connection.execute(
                        """insert into local_agent_apply_confirmations(
                               run_id, attempt_id, token_hash, requested_by, binding_json,
                               issued_at, expires_at, status
                           ) values(?, ?, ?, ?, ?, ?, ?, 'issued')""",
                        (run_id, attempt_id, token_hash, requested_by, binding_json, issued, expires),
                    )
                except database.sqlite3.IntegrityError as error:
                    raise ValueError("local_agent_confirmation_invalid") from error
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def _prepare_local_apply_completion(
        self,
        *,
        service_owner: object,
        service_capability: object,
        run_id: int,
        token_hash: str,
        requested_by: str,
        now: str,
    ) -> dict[str, object]:
        """Run the bound service operation and issue one completion capability.

        The repository invokes the registered service instance directly while
        the write reservation is held.  Callers cannot substitute an operation
        or receipt-fact producer; only ``finalize_local_apply`` can advance the
        control state.
        """

        self._require_local_apply_service(service_owner, service_capability)
        run_id = _positive_id(run_id)
        token_hash = _authorization_digest(token_hash)
        requested_by = _safe_alias(requested_by)
        current = _timestamp(now)
        execute_operation = getattr(service_owner, "_execute_local_apply_operation", None)
        prepare_operation = getattr(service_owner, "_prepare_local_apply_operation", None)
        if not callable(execute_operation) or not callable(prepare_operation):
            raise ValueError(_STORAGE_INVALID)
        operation_persisted = False
        try:
            # Commit an authoritative intent before any source mutation.  A
            # later process death or namespace loss cannot erase this fact.
            with self._connect() as connection:
                connection.execute("begin immediate")
                row = connection.execute(
                    "select * from local_agent_apply_confirmations where run_id = ?", (run_id,),
                ).fetchone()
                if (
                    row is None
                    or row["status"] != "issued"
                    or not hmac.compare_digest(str(row["token_hash"]), token_hash)
                    or row["requested_by"] != requested_by
                ):
                    raise ValueError("local_agent_confirmation_invalid")
                run, attempt, artifacts = _load_confirmation_facts(connection, run_id)
                if run["status"] != "awaiting_human_confirmation" or attempt["id"] != row["attempt_id"]:
                    raise ValueError("local_agent_confirmation_invalid")
                operation_row = connection.execute(
                    "select * from local_agent_apply_operations where run_id=?", (run_id,),
                ).fetchone()
                if operation_row is None and _timestamp_datetime(current) >= _timestamp_datetime(_timestamp(row["expires_at"])):
                    if connection.execute(
                        "update local_agent_apply_confirmations set status='expired', consumed_at=? where run_id=? and status='issued'",
                        (current, run_id),
                    ).rowcount != 1:
                        raise ValueError(_STORAGE_INVALID)
                    _append_event_in_transaction(connection, run_id, int(attempt["id"]), "confirmation_expired", _encode_safe_mapping({"expired": True}))
                    expired_run = _transition_in_transaction(connection, run_id, "awaiting_human_confirmation", "confirmation_expired", _encode_safe_mapping({"confirmed": False}))
                    return {"run": expired_run, "expired": True}
                binding = _decode_confirmation_binding(row["binding_json"])
                if binding.get("artifacts") != [_public_review_artifact_fact(item) for item in artifacts]:
                    raise ValueError("local_agent_confirmation_invalid")
                if operation_row is None:
                    prepared = prepare_operation(run, attempt, tuple(artifacts), binding, requested_by)
                    if not isinstance(prepared, Mapping):
                        raise ValueError(_STORAGE_INVALID)
                    base_json = _encode_local_apply_operation(prepared, include_authority=False)
                    operation_id = hashlib.sha256((token_hash + "\0" + base_json).encode()).hexdigest()
                    facts = {
                        **dict(prepared),
                        "operation_id": operation_id,
                        "recovery_application_id": hashlib.sha256((operation_id + ":recovery").encode()).hexdigest()[:24],
                    }
                    facts_json = _encode_local_apply_operation(facts, include_authority=True)
                    created_at = database.now_iso()
                    connection.execute(
                        "insert into local_agent_apply_operations(run_id, attempt_id, operation_id, token_hash, facts_json, journal_application_id, status, created_at, updated_at) values(?, ?, ?, ?, ?, null, 'applying', ?, ?)",
                        (run_id, attempt["id"], operation_id, token_hash, facts_json, created_at, created_at),
                    )
                else:
                    if (
                        operation_row["attempt_id"] != attempt["id"]
                        or not hmac.compare_digest(str(operation_row["token_hash"]), token_hash)
                        or operation_row["status"] not in {"applying", "recovery_required"}
                    ):
                        raise ValueError("local_agent_confirmation_invalid")
                    _decode_local_apply_operation(operation_row["facts_json"])
            operation_persisted = True

            # Hold the SQLite writer reservation while the registered service
            # performs/reconciles the source operation.  Concurrent callers
            # cannot start a second source mutation, while the already-committed
            # intent survives abrupt process termination.
            with self._connect() as connection:
                connection.execute("begin immediate")
                row = connection.execute(
                    "select * from local_agent_apply_confirmations where run_id=?", (run_id,),
                ).fetchone()
                operation_row = connection.execute(
                    "select * from local_agent_apply_operations where run_id=?", (run_id,),
                ).fetchone()
                if (
                    row is None or row["status"] != "issued"
                    or operation_row is None
                    or operation_row["status"] not in {"applying", "recovery_required"}
                    or not hmac.compare_digest(str(row["token_hash"]), token_hash)
                    or not hmac.compare_digest(str(operation_row["token_hash"]), token_hash)
                ):
                    raise ValueError("local_agent_confirmation_invalid")
                run, attempt, artifacts = _load_confirmation_facts(connection, run_id)
                binding = _decode_confirmation_binding(row["binding_json"])
                operation_facts = _decode_local_apply_operation(operation_row["facts_json"])
                outcome = execute_operation(
                    run, attempt, tuple(artifacts), binding, requested_by, operation_facts,
                )
                if not isinstance(outcome, Mapping):
                    raise ValueError(_STORAGE_INVALID)
                apply_result = outcome.get("apply")
                application_id = apply_result.get("application_id") if isinstance(apply_result, Mapping) else None
                if (
                    not isinstance(application_id, str)
                    or application_id not in {
                        operation_facts["primary_application_id"],
                        operation_facts["recovery_application_id"],
                    }
                    or (operation_row["journal_application_id"] is not None and operation_row["journal_application_id"] != application_id)
                ):
                    raise ValueError(_STORAGE_INVALID)
                if connection.execute(
                    "update local_agent_apply_operations set journal_application_id=?, status='applying', updated_at=? where run_id=? and status in ('applying','recovery_required')",
                    (application_id, database.now_iso(), run_id),
                ).rowcount != 1:
                    raise ValueError(_STORAGE_INVALID)
                completion_facts, completion_receipt = _local_apply_completion_facts(
                    connection=connection,
                    run=run,
                    attempt=attempt,
                    artifacts=tuple(artifacts),
                    confirmation_row=row,
                    binding=binding,
                    outcome=outcome,
                )
                capability = _LocalApplyCompletionCapability(_LOCAL_APPLY_COMPLETION_ISSUER)
                with self._local_apply_lock:
                    self._local_apply_completions[capability] = (
                        completion_facts, _freeze_review_artifact(completion_receipt),
                    )
                return {"run": run, "expired": False, "capability": capability, "operation": dict(outcome)}
        except ValueError:
            if operation_persisted:
                self._mark_local_apply_operation_recovery_required(run_id, token_hash)
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError, RuntimeError):
            raise ValueError(_STORAGE_INVALID) from None

    def _mark_local_apply_operation_recovery_required(self, run_id: int, token_hash: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                connection.execute(
                    "update local_agent_apply_operations set status='recovery_required', updated_at=? where run_id=? and token_hash=? and status='applying'",
                    (database.now_iso(), run_id, token_hash),
                )
        except (database.sqlite3.DatabaseError, OSError):
            pass

    def finalize_local_apply(self, capability: object) -> dict[str, object]:
        """Consume one service-issued completion capability in one transaction."""

        if not isinstance(capability, _LocalApplyCompletionCapability):
            raise ValueError("local_agent_confirmation_invalid")
        with self._local_apply_lock:
            stored = self._local_apply_completions.pop(capability, None)
        if stored is None:
            raise ValueError("local_agent_confirmation_invalid")
        prepared_facts, prepared_receipt = stored
        run_id = _positive_id(prepared_facts[0])
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                row = connection.execute(
                    "select * from local_agent_apply_confirmations where run_id=?", (run_id,),
                ).fetchone()
                if row is None or row["status"] != "issued":
                    raise ValueError("local_agent_confirmation_invalid")
                run, attempt, artifacts = _load_confirmation_facts(connection, run_id)
                binding = _decode_confirmation_binding(row["binding_json"])
                try:
                    current_facts, current_receipt = _local_apply_completion_facts(
                        connection=connection,
                        run=run,
                        attempt=attempt,
                        artifacts=tuple(artifacts),
                        confirmation_row=row,
                        binding=binding,
                        outcome=None,
                    )
                except ValueError:
                    raise ValueError("local_agent_confirmation_invalid") from None
                if current_facts != prepared_facts or _freeze_review_artifact(current_receipt) != prepared_receipt:
                    raise ValueError("local_agent_confirmation_invalid")
                cursor = connection.execute(
                    "insert into local_agent_artifacts(run_id, attempt_id, kind, relative_path, sha256, size_bytes, created_at) values(?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id, attempt["id"], current_receipt["kind"],
                        current_receipt["relative_path"], current_receipt["sha256"],
                        current_receipt["size_bytes"], database.now_iso(),
                    ),
                )
                created = _artifact_from_row(connection.execute(
                    "select * from local_agent_artifacts where id=?", (cursor.lastrowid,),
                ).fetchone())
                _append_event_in_transaction(
                    connection, run_id, int(attempt["id"]), "local_apply_finished",
                    _encode_safe_mapping({"applied": True}),
                )
                applied_run = _complete_local_apply_in_transaction(connection, run_id)
                if connection.execute(
                    "update local_agent_apply_confirmations set status='consumed', consumed_at=? where run_id=? and status='issued'",
                    (database.now_iso(), run_id),
                ).rowcount != 1:
                    raise ValueError(_STORAGE_INVALID)
                if connection.execute(
                    "update local_agent_apply_operations set status='completed', updated_at=? where run_id=? and status in ('applying','recovery_required')",
                    (database.now_iso(), run_id),
                ).rowcount != 1:
                    raise ValueError(_STORAGE_INVALID)
                return {"run": applied_run, "receipt": created}
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError, RuntimeError):
            raise ValueError(_STORAGE_INVALID) from None

    def _prepare_review_finalization(
        self,
        *,
        run_id: int,
        attempt_id: int,
        expected_updated_at: str,
        expected_event_count: int,
        verdict: str,
        finding_count: int,
        pending_artifacts: tuple[Mapping[str, object], ...],
        now: datetime | None = None,
    ) -> _ReviewFinalizationCapability:
        """Issue one instance-bound capability after DB-authoritative checks."""
        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        if verdict not in {"approved", "changes_requested"} or not isinstance(finding_count, int) or isinstance(finding_count, bool) or finding_count < 0:
            raise ValueError(_STORAGE_INVALID)
        if not isinstance(expected_updated_at, str) or not isinstance(expected_event_count, int) or isinstance(expected_event_count, bool) or expected_event_count < 0:
            raise ValueError(_STORAGE_INVALID)
        pending = tuple(_review_artifact_fact(item, require_id=False) for item in pending_artifacts)
        pending_by_kind = {str(item["kind"]): item for item in pending}
        expected_pending_paths = {
            "final_review": f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}/review.json",
            "review_seal": f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}/review-seal.json",
        }
        if len(pending) != 2 or set(pending_by_kind) != set(expected_pending_paths):
            raise ValueError(_STORAGE_INVALID)
        for kind, expected_path in expected_pending_paths.items():
            item = pending_by_kind[kind]
            if item["run_id"] != run_id or item["attempt_id"] != attempt_id or item["relative_path"] != expected_path:
                raise ValueError(_STORAGE_INVALID)
        issued_at, expires_at, expires_monotonic = _review_capability_times(now)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                run, authoritative = _validate_review_finalization_state(
                    connection, run_id, attempt_id, expected_updated_at, expected_event_count,
                )
                file_evidence, sealed_finalization_facts = _validate_review_artifact_files(
                    run, attempt_id, expected_event_count, verdict, finding_count,
                    authoritative, pending,
                )
                facts: tuple[object, ...] = (
                    run_id, attempt_id, expected_updated_at, expected_event_count,
                    run["task_key"], run["contract_hash"], run["initial_head"],
                    run["worktree_path"],
                    verdict, finding_count,
                    issued_at.isoformat(), expires_at.isoformat(), expires_monotonic,
                    tuple(_freeze_review_artifact(item) for item in authoritative),
                    tuple(_freeze_review_artifact(item) for item in pending),
                    file_evidence,
                    sealed_finalization_facts,
                )
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None
        capability = _ReviewFinalizationCapability(_FINALIZATION_ISSUER)
        with self._review_finalization_lock:
            self._review_finalizations[capability] = facts
        return capability

    def stage_approved_review_learning_observation(
        self,
        capability: object,
        *,
        task: LocalAgentTask,
    ) -> _ReviewLearningObservationCapability | None:
        """Stage only fixed matched observations for one sealed approved review.

        Nothing is persisted here.  The staged payload is owned by this
        repository and is consumed atomically by ``finalize_review`` so a
        later finalization failure cannot leave a successful-observation row.
        """

        if not isinstance(capability, _ReviewFinalizationCapability):
            raise ValueError("repair_learning_input_invalid")
        with self._review_finalization_lock:
            facts = self._review_finalizations.get(capability)
        if facts is None:
            raise ValueError("repair_learning_input_invalid")
        (
            run_id, attempt_id, expected_updated_at, expected_event_count,
            task_key, contract_hash, initial_head, worktree_path,
            verdict, finding_count, issued_at, expires_at, expires_monotonic,
            frozen_authoritative, frozen_pending, frozen_file_evidence, frozen_sealed_finalization_facts,
        ) = facts
        del expected_updated_at, expected_event_count, task_key, contract_hash
        del initial_head, worktree_path, finding_count, frozen_authoritative
        del frozen_pending, frozen_file_evidence, frozen_sealed_finalization_facts
        if verdict != "approved":
            raise ValueError("repair_learning_input_invalid")
        try:
            _validate_review_capability_time(issued_at, expires_at, expires_monotonic)
            assert_local_agent_task_is_current(task)
        except ValueError:
            raise ValueError("repair_learning_input_invalid") from None
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                binding = _learning_binding_in_transaction(
                    connection,
                    task=task,
                    run_id=int(run_id),
                    attempt_id=int(attempt_id),
                    allowed_run_statuses=frozenset({"reviewing"}),
                )
                workspace = str(binding["workspace_fingerprint"])
                records = connection.execute(
                    """
                    select * from repair_learning_rules
                    where state in ('active_current_task', 'trial', 'stable')
                      and (state != 'active_current_task' or active_run_id = ?)
                      and (state != 'stable' or (
                        verified_task_count >= 3
                        and distinct_workspace_count >= 2
                        and counterexample_count = 0
                      ))
                    order by id
                    """,
                    (int(run_id),),
                ).fetchall()
                normalized_rule_ids = tuple(
                    int(rule["id"])
                    for rule in records
                    if _learning_rule_matches_durable_task(rule, task=task, binding=binding)
                )
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None
        if not normalized_rule_ids:
            return None
        staged = _ReviewLearningObservationCapability(_REVIEW_LEARNING_OBSERVATION_ISSUER)
        payload: tuple[object, ...] = (
            int(run_id),
            int(attempt_id),
            task,
            task.task_key,
            task.contract_hash,
            workspace,
            normalized_rule_ids,
        )
        with self._review_learning_observation_lock:
            if capability in self._review_learning_by_finalization:
                raise ValueError("repair_learning_input_invalid")
            self._review_learning_observations[staged] = payload
            self._review_learning_by_finalization[capability] = staged
        return staged

    def finalize_review(
        self,
        capability: object,
        *,
        learning_observation: object | None = None,
    ) -> dict[str, object]:
        """Consume one opaque capability and atomically persist the verdict."""
        if not isinstance(capability, _ReviewFinalizationCapability):
            raise ValueError(_STORAGE_INVALID)
        staged: tuple[object, ...] | None = None
        if learning_observation is not None:
            if not isinstance(learning_observation, _ReviewLearningObservationCapability):
                raise ValueError("repair_learning_input_invalid")
            with self._review_learning_observation_lock:
                staged = self._review_learning_observations.get(learning_observation)
            if staged is None:
                raise ValueError("repair_learning_input_invalid")
        with self._review_learning_observation_lock:
            linked = self._review_learning_by_finalization.get(capability)
        if linked is not learning_observation:
            raise ValueError("repair_learning_input_invalid")
        with self._review_finalization_lock:
            facts = self._review_finalizations.pop(capability, None)
        if facts is None:
            raise ValueError(_STORAGE_INVALID)
        (
            run_id, attempt_id, expected_updated_at, expected_event_count,
            task_key, contract_hash, initial_head, worktree_path,
            verdict, finding_count, issued_at, expires_at, expires_monotonic,
            frozen_authoritative, frozen_pending, frozen_file_evidence, frozen_sealed_finalization_facts,
        ) = facts
        _validate_review_capability_time(issued_at, expires_at, expires_monotonic)
        target = "awaiting_human_confirmation" if verdict == "approved" else "changes_requested"
        summary = _encode_safe_mapping({"review": verdict, "finding_count": finding_count})
        event_payload = _encode_safe_mapping({"verdict": verdict, "finding_count": finding_count})
        pending = tuple(_thaw_review_artifact(item) for item in frozen_pending)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                run, authoritative = _validate_review_finalization_state(
                    connection, int(run_id), int(attempt_id), str(expected_updated_at), int(expected_event_count),
                )
                if run["task_key"] != task_key or run["contract_hash"] != contract_hash or run["initial_head"] != initial_head:
                    raise ValueError(_STORAGE_INVALID)
                if tuple(_freeze_review_artifact(item) for item in authoritative) != frozen_authoritative:
                    raise ValueError(_STORAGE_INVALID)
                _validate_review_capability_time(issued_at, expires_at, expires_monotonic)
                file_evidence, sealed_finalization_facts = _validate_review_artifact_files(
                    run, int(attempt_id), int(expected_event_count), str(verdict), int(finding_count),
                    authoritative, pending,
                )
                if (
                    file_evidence != frozen_file_evidence
                    or sealed_finalization_facts != frozen_sealed_finalization_facts
                ):
                    raise ValueError(_STORAGE_INVALID)
                _validate_finalization_tree_evidence_in_transaction(
                    connection,
                    run=run,
                    authoritative=authoritative,
                    sealed_finalization_facts=sealed_finalization_facts,
                )
                if staged is not None:
                    self._record_staged_approved_review_observation(
                        connection,
                        staged=staged,
                        run_id=int(run_id),
                        attempt_id=int(attempt_id),
                        task_key=str(task_key),
                        contract_hash=str(contract_hash),
                    )
                created: list[dict[str, object]] = []
                for item in pending:
                    cursor = connection.execute(
                        "insert into local_agent_artifacts(run_id, attempt_id, kind, relative_path, sha256, size_bytes, created_at) values(?, ?, ?, ?, ?, ?, ?)",
                        (run_id, attempt_id, item["kind"], item["relative_path"], item["sha256"], item["size_bytes"], database.now_iso()),
                    )
                    created.append(_artifact_from_row(connection.execute("select * from local_agent_artifacts where id = ?", (cursor.lastrowid,)).fetchone()))
                _append_event_in_transaction(connection, int(run_id), int(attempt_id), "review_finished", event_payload)
                result = _transition_in_transaction(connection, int(run_id), "reviewing", target, summary)
                if learning_observation is not None:
                    with self._review_learning_observation_lock:
                        self._review_learning_observations.pop(learning_observation, None)
                        self._review_learning_by_finalization.pop(capability, None)
                return {"run": result, "artifacts": created}
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def _record_staged_approved_review_observation(
        self,
        connection: database.sqlite3.Connection,
        *,
        staged: tuple[object, ...],
        run_id: int,
        attempt_id: int,
        task_key: str,
        contract_hash: str,
    ) -> None:
        (
            staged_run_id,
            staged_attempt_id,
            staged_task,
            staged_task_key,
            staged_contract_hash,
            workspace_fingerprint,
            rule_ids,
        ) = staged
        if (
            staged_run_id != run_id
            or staged_attempt_id != attempt_id
            or staged_task_key != task_key
            or staged_contract_hash != contract_hash
            or not isinstance(staged_task, LocalAgentTask)
            or staged_task.task_key != task_key
            or staged_task.contract_hash != contract_hash
            or not isinstance(workspace_fingerprint, str)
            or not isinstance(rule_ids, tuple)
        ):
            raise ValueError("repair_learning_input_invalid")
        binding = _learning_binding_in_transaction(
            connection,
            task=staged_task,
            run_id=run_id,
            attempt_id=attempt_id,
            allowed_run_statuses=frozenset({"reviewing"}),
        )
        if binding["workspace_fingerprint"] != workspace_fingerprint:
            raise ValueError("repair_learning_input_invalid")
        evidence_json = _encode_learning_observation_evidence(
            {"event": "verification_and_review_passed"},
            outcome=RuleObservationOutcome.MATCHED.value,
        )
        for rule_id in rule_ids:
            if not isinstance(rule_id, int) or isinstance(rule_id, bool):
                raise ValueError("repair_learning_input_invalid")
            rule = connection.execute(
                "select * from repair_learning_rules where id=?", (rule_id,),
            ).fetchone()
            if rule is None or not _learning_rule_matches_durable_task(
                rule,
                task=staged_task,
                binding=binding,
            ):
                raise ValueError("repair_learning_input_invalid")
            observed_at = database.now_iso()
            cursor = connection.execute(
                """
                insert into repair_learning_observations(
                    rule_id, run_id, attempt_id, task_key,
                    workspace_fingerprint, outcome, evidence_json, observed_at
                ) values (?, ?, ?, ?, ?, 'matched', ?, ?)
                on conflict(rule_id, run_id, attempt_id, outcome) do nothing
                """,
                (
                    rule_id, run_id, attempt_id, task_key,
                    workspace_fingerprint, evidence_json, observed_at,
                ),
            )
            row = connection.execute(
                """select * from repair_learning_observations
                   where rule_id=? and run_id=? and attempt_id=? and outcome='matched'""",
                (rule_id, run_id, attempt_id),
            ).fetchone()
            if row is None or tuple(row[key] for key in (
                "task_key", "workspace_fingerprint", "evidence_json",
            )) != (task_key, workspace_fingerprint, evidence_json):
                raise ValueError("repair_learning_replay_conflict")
            if cursor.rowcount == 1:
                _learning_refresh_counts_and_suspend(connection, rule_id, observed_at)
            _advance_learning_rule_after_success(connection, rule_id)

    def fail_review(self, run_id: int, attempt_id: int, reason: str = "review_failed", audit: Mapping[str, object] | None = None) -> dict[str, object]:
        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        reason = _safe_alias(reason)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                if _require_attempt(connection, attempt_id)["run_id"] != run_id:
                    raise ValueError(_STORAGE_INVALID)
                payload = {"reason": reason} if audit is None else {"reason": reason, **dict(audit)}
                _append_event_in_transaction(connection, run_id, attempt_id, "review_failed", _encode_review_failure(payload))
                return _transition_in_transaction(connection, run_id, "reviewing", "failed_review", _encode_safe_mapping({"review": "failed"}))
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def invalidate_finalized_review(self, run_id: int, attempt_id: int, expected_status: str) -> dict[str, object]:
        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        if expected_status not in {"awaiting_human_confirmation", "changes_requested"}:
            raise ValueError(_STORAGE_INVALID)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                if _require_attempt(connection, attempt_id)["run_id"] != run_id:
                    raise ValueError(_STORAGE_INVALID)
                _append_event_in_transaction(connection, run_id, attempt_id, "review_invalidated", _encode_safe_mapping({"reason": "integrity_changed"}))
                return _transition_in_transaction(connection, run_id, expected_status, "failed_review", _encode_safe_mapping({"review": "invalidated"}))
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def invalidate_confirmation_for_correction(
        self,
        run_id: int,
        attempt_id: int,
        *,
        correction_kind: str,
    ) -> dict[str, object]:
        """Prevent a pending confirmation from applying after a correction.

        This is deliberately one-way: it never claims an already-applied
        source change was rolled back.  The only persistent confirmation
        terminal state available in the existing schema is ``expired``; the
        accompanying append-only event records that its actual reason was a
        human correction rather than timeout expiry.
        """

        run_id, attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        correction_kind = _safe_alias(correction_kind)
        if correction_kind not in {
            "verification_failure", "review_gap", "path_coverage_gap", "contract_mismatch",
            "implementation_defect",
        }:
            raise ValueError(_STORAGE_INVALID)
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                run = _require_run(connection, run_id)
                current_attempt = connection.execute(
                    "select id from local_agent_attempts where run_id=? order by attempt_no desc limit 1",
                    (run_id,),
                ).fetchone()
                if (
                    run["status"] != "awaiting_human_confirmation"
                    or current_attempt is None
                    or int(current_attempt["id"]) != attempt_id
                ):
                    raise ValueError(_STATE_TRANSITION_INVALID)
                if connection.execute(
                    "select 1 from local_agent_apply_operations where run_id=?",
                    (run_id,),
                ).fetchone() is not None:
                    raise ValueError(_STORAGE_INVALID)
                confirmation = connection.execute(
                    "select * from local_agent_apply_confirmations where run_id=?",
                    (run_id,),
                ).fetchone()
                if (
                    confirmation is None
                    or confirmation["attempt_id"] != attempt_id
                    or confirmation["status"] != "issued"
                ):
                    raise ValueError(_STORAGE_INVALID)
                if connection.execute(
                    "update local_agent_apply_confirmations set status='expired', consumed_at=? where run_id=? and attempt_id=? and status='issued'",
                    (database.now_iso(), run_id, attempt_id),
                ).rowcount != 1:
                    raise ValueError(_STORAGE_INVALID)
                if connection.execute(
                    "update local_agent_runs set status='changes_requested', summary_json=?, updated_at=? where id=? and status='awaiting_human_confirmation'",
                    (_encode_safe_mapping({"correction_kind": correction_kind}), database.now_iso(), run_id),
                ).rowcount != 1:
                    raise ValueError(_STATE_TRANSITION_INVALID)
                _append_event_in_transaction(
                    connection,
                    run_id,
                    attempt_id,
                    "confirmation_invalidated_for_correction",
                    _encode_safe_mapping({"correction_kind": correction_kind}),
                )
                return _require_run(connection, run_id)
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def snapshot(self, run_id: int) -> dict[str, object]:
        run_id = _positive_id(run_id)
        try:
            with self._connect() as connection:
                connection.execute("begin")
                run = _require_run(connection, run_id)
                attempts = [_attempt_from_row(row) for row in connection.execute("select * from local_agent_attempts where run_id = ? order by attempt_no", (run_id,))]
                events = [_event_from_row(row) for row in connection.execute("select * from local_agent_run_events where run_id = ? order by sequence_no", (run_id,))]
                artifacts = [_artifact_from_row(row) for row in connection.execute("select * from local_agent_artifacts where run_id = ? order by id", (run_id,))]
                confirmation = connection.execute(
                    "select run_id, attempt_id, status, consumed_at from local_agent_apply_confirmations where run_id=?", (run_id,),
                ).fetchone()
                operation_row = connection.execute(
                    "select run_id, attempt_id, operation_id, journal_application_id, status, created_at, updated_at from local_agent_apply_operations where run_id=?",
                    (run_id,),
                ).fetchone()
                apply_operation = None if operation_row is None else dict(operation_row)
                binding_row = connection.execute("select binding_json from local_agent_workspace_bindings where run_id = ?", (run_id,)).fetchone()
                binding = None if binding_row is None else _decode_workspace_binding(binding_row["binding_json"])
                _validate_snapshot(run, attempts, events, artifacts, confirmation, apply_operation)
                return {"run": run, "attempts": attempts, "events": events, "artifacts": artifacts, "workspace_binding": binding, "apply_operation": apply_operation}
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError):
            raise ValueError(_STORAGE_INVALID) from None

    def mark_orphaned_attempts_interrupted(self) -> list[int]:
        try:
            with self._connect() as connection:
                connection.execute("begin immediate")
                starting = [_attempt_from_row(row) for row in connection.execute("select * from local_agent_attempts where status = 'starting' order by id")]
                result: list[int] = []
                for attempt in starting:
                    _abandon_starting_attempt_in_transaction(connection, attempt, "worker_start_failed")
                    result.append(int(attempt["run_id"]))
                candidates = [_attempt_from_row(row) for row in connection.execute("select * from local_agent_attempts where status = 'worker_running' order by id")]
                orphaned: list[dict[str, object]] = []
                for attempt in candidates:
                    if _require_run(connection, int(attempt["run_id"]))["status"] != "worker_running":
                        raise ValueError(_STORAGE_INVALID)
                    if _worker_liveness(attempt) is False:
                        orphaned.append(attempt)
                for attempt in orphaned:
                    run_id, attempt_id = int(attempt["run_id"]), int(attempt["id"])
                    now = database.now_iso()
                    if connection.execute("update local_agent_attempts set status = 'interrupted', error_code = 'worker_orphaned', finished_at = ? where id = ? and status = 'worker_running'", (now, attempt_id)).rowcount != 1:
                        raise ValueError(_STORAGE_INVALID)
                    _append_event_in_transaction(connection, run_id, attempt_id, "attempt_interrupted", _encode_safe_mapping({"reason": "worker_not_live"}))
                    _transition_in_transaction(connection, run_id, "worker_running", "interrupted", _encode_safe_mapping({"reason": "worker_not_live"}))
                    result.append(run_id)
                return result
        except ValueError:
            raise
        except (database.sqlite3.DatabaseError, OSError, TypeError, UnicodeError, RuntimeError):
            raise ValueError(_STORAGE_INVALID) from None


def _positive_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(_STORAGE_INVALID)
    return value


def _optional_positive_id(value: object) -> int | None:
    return None if value is None else _positive_id(value)


def _safe_alias(value: object) -> str:
    try:
        return validate_audit_alias(value)
    except ValueError:
        raise ValueError(_STORAGE_INVALID) from None


def _safe_task_key(value: object) -> str:
    try:
        return validate_local_agent_task_key(value)
    except ValueError:
        raise ValueError(_STORAGE_INVALID) from None


def _safe_optional_alias(value: object) -> str:
    if value == "":
        return ""
    return _safe_alias(value)


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value or contains_sensitive_text(value):
        raise ValueError(_STORAGE_INVALID)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(_STORAGE_INVALID)
    return value


def _review_capability_times(now: datetime | None) -> tuple[datetime, datetime, float]:
    actual_utc = datetime.now(timezone.utc)
    actual_monotonic = time.monotonic()
    issued = actual_utc if now is None else now
    if not isinstance(issued, datetime) or issued.tzinfo is None or issued.utcoffset() != timedelta(0):
        raise ValueError(_STORAGE_INVALID)
    issued = issued.astimezone(timezone.utc)
    if issued > actual_utc + timedelta(seconds=1):
        raise ValueError(_STORAGE_INVALID)
    expires = issued + timedelta(seconds=_FINALIZATION_TTL_SECONDS)
    wall_age = max(0.0, (actual_utc - issued).total_seconds())
    expires_monotonic = actual_monotonic - wall_age + _FINALIZATION_TTL_SECONDS
    return issued, expires, expires_monotonic


def _validate_review_capability_time(issued_at: object, expires_at: object, expires_monotonic: object) -> None:
    try:
        if not isinstance(issued_at, str) or not isinstance(expires_at, str):
            raise ValueError
        issued = datetime.fromisoformat(issued_at)
        expires = datetime.fromisoformat(expires_at)
        if issued.tzinfo is None or expires.tzinfo is None or issued.utcoffset() != timedelta(0) or expires.utcoffset() != timedelta(0):
            raise ValueError
        if expires - issued != timedelta(seconds=_FINALIZATION_TTL_SECONDS):
            raise ValueError
        if not isinstance(expires_monotonic, float) or not math.isfinite(expires_monotonic):
            raise ValueError
        if datetime.now(timezone.utc) >= expires or time.monotonic() >= expires_monotonic:
            raise ValueError
    except (OverflowError, TypeError, ValueError):
        raise ValueError(_STORAGE_INVALID) from None


def _validate_review_finalization_state(
    connection: database.sqlite3.Connection,
    run_id: int,
    attempt_id: int,
    expected_updated_at: str,
    expected_event_count: int,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    run = _require_run(connection, run_id)
    latest = _attempt_from_row(connection.execute(
        "select * from local_agent_attempts where run_id = ? order by attempt_no desc limit 1", (run_id,),
    ).fetchone())
    event_count = int(connection.execute(
        "select count(*) from local_agent_run_events where run_id = ?", (run_id,),
    ).fetchone()[0])
    if (
        run["status"] != "reviewing"
        or run["updated_at"] != expected_updated_at
        or latest["id"] != attempt_id
        or latest["status"] != "completed"
        or event_count != expected_event_count
    ):
        raise ValueError(_STATE_TRANSITION_INVALID)
    attempt_kinds = _FINALIZATION_AUTHORITATIVE_KINDS[1:]
    placeholders = ",".join("?" for _item in attempt_kinds)
    rows = connection.execute(
        f"""select * from local_agent_artifacts
            where run_id = ? and (
                (attempt_id is null and kind = 'task_contract')
                or (attempt_id = ? and kind in ({placeholders}))
            ) order by id""",
        (run_id, attempt_id, *attempt_kinds),
    ).fetchall()
    artifacts = [_artifact_from_row(row) for row in rows]
    by_kind: dict[str, list[dict[str, object]]] = {}
    for item in artifacts:
        by_kind.setdefault(str(item["kind"]), []).append(item)
    if set(by_kind) != set(_FINALIZATION_AUTHORITATIVE_KINDS) or any(len(items) != 1 for items in by_kind.values()):
        raise ValueError(_STORAGE_INVALID)
    expected = {
        "task_contract": (None, f"{_CONTROL}/run_{run_id}/task.json"),
        "worker_patch": (attempt_id, f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}.patch"),
        "worker_change_manifest": (attempt_id, f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}.change.json"),
        "verification_manifest": (attempt_id, f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}.verification.json"),
        "final_diff": (attempt_id, f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}/final.diff"),
        "final_patch": (attempt_id, f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}/final.patch"),
        "final_verification": (attempt_id, f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}/verification.json"),
        "final_manifest": (attempt_id, f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}/manifest.json"),
    }
    ordered: list[dict[str, object]] = []
    for kind in _FINALIZATION_AUTHORITATIVE_KINDS:
        item = by_kind[kind][0]
        owner, path = expected[kind]
        if item["attempt_id"] != owner or item["relative_path"] != path:
            raise ValueError(_STORAGE_INVALID)
        ordered.append(item)
    facts = {str(item["kind"]): item for item in ordered}
    if (
        facts["final_diff"]["sha256"] != facts["worker_patch"]["sha256"]
        or facts["final_diff"]["size_bytes"] != facts["worker_patch"]["size_bytes"]
        or facts["final_patch"]["sha256"] != facts["worker_patch"]["sha256"]
        or facts["final_patch"]["size_bytes"] != facts["worker_patch"]["size_bytes"]
        or facts["final_verification"]["sha256"] != facts["verification_manifest"]["sha256"]
        or facts["final_verification"]["size_bytes"] != facts["verification_manifest"]["size_bytes"]
    ):
        raise ValueError(_STORAGE_INVALID)
    return run, tuple(ordered)


def _load_confirmation_facts(
    connection: database.sqlite3.Connection, run_id: int
) -> tuple[dict[str, object], dict[str, object], tuple[dict[str, object], ...]]:
    run = _require_run(connection, run_id)
    attempt_row = connection.execute(
        "select * from local_agent_attempts where run_id=? order by attempt_no desc limit 1",
        (run_id,),
    ).fetchone()
    attempt = _attempt_from_row(attempt_row)
    if attempt["status"] != "completed":
        raise ValueError(_STORAGE_INVALID)
    rows = connection.execute(
        "select * from local_agent_artifacts where run_id=? order by id", (run_id,),
    ).fetchall()
    all_artifacts = [_artifact_from_row(row) for row in rows]
    selected: list[dict[str, object]] = []
    for kind in _CONFIRMATION_ARTIFACT_KINDS:
        owner = None if kind == "task_contract" else attempt["id"]
        matches = [
            item for item in all_artifacts
            if item["kind"] == kind and item["attempt_id"] == owner
        ]
        if len(matches) != 1:
            raise ValueError(_STORAGE_INVALID)
        selected.append(matches[0])
    return run, attempt, tuple(selected)


def _local_apply_completion_facts(
    *,
    connection: database.sqlite3.Connection,
    run: Mapping[str, object],
    attempt: Mapping[str, object],
    artifacts: tuple[Mapping[str, object], ...],
    confirmation_row: Any,
    binding: Mapping[str, object],
    outcome: Mapping[str, object] | None,
) -> tuple[tuple[object, ...], dict[str, object]]:
    from app.local_agent_confirmation import _source_facts, _source_patch, _stable_identity_chain
    from app.local_agent_contract import load_local_agent_task_bytes
    from app.local_agent_review import read_owned_file_with_identity
    from app.worktree_executor import read_local_apply_transaction_evidence

    run_id, attempt_id = _positive_id(run["id"]), _positive_id(attempt["id"])
    workspace_row = connection.execute(
        "select binding_json from local_agent_workspace_bindings where run_id=?", (run_id,),
    ).fetchone()
    if workspace_row is None:
        raise ValueError(_STORAGE_INVALID)
    workspace = _decode_workspace_binding(workspace_row["binding_json"])
    worktree_path = Path(str(workspace["worktree_path"]))
    artifact_root = worktree_path.parent
    root_item = artifact_root.lstat()
    if stat.S_ISLNK(root_item.st_mode) or not stat.S_ISDIR(root_item.st_mode):
        raise ValueError(_STORAGE_INVALID)

    contents: dict[str, bytes] = {}
    artifact_identities: list[object] = []
    for record in artifacts:
        relative, size, digest = str(record["relative_path"]), int(record["size_bytes"]), str(record["sha256"])
        try:
            content, identity = read_owned_file_with_identity(artifact_root, relative, maximum=size)
        except ValueError:
            raise ValueError(_STORAGE_INVALID) from None
        if len(content) != size or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), digest):
            raise ValueError(_STORAGE_INVALID)
        contents[str(record["kind"])] = content
        artifact_identities.append([str(record["kind"]), relative, _stable_identity_chain(identity)])

    task = load_local_agent_task_bytes(contents["task_contract"])
    if task.contract_hash != run["contract_hash"] or task.initial_head != run["initial_head"]:
        raise ValueError(_STORAGE_INVALID)
    try:
        manifest = json.loads(contents["final_manifest"].decode("utf-8", "strict"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None
    changed = manifest.get("changed_paths") if isinstance(manifest, dict) else None
    if not isinstance(changed, list) or not changed or changed != sorted(set(changed)) or any(not isinstance(item, str) for item in changed):
        raise ValueError(_STORAGE_INVALID)
    changed_paths = tuple(changed)
    patch = contents["final_patch"]
    if _source_patch(task, changed_paths) != patch:
        raise ValueError("local_agent_confirmation_invalid")
    source = _source_facts(task, changed_paths)

    patch_hash = hashlib.sha256(patch).hexdigest()
    operation_row = connection.execute(
        "select * from local_agent_apply_operations where run_id=?", (run_id,),
    ).fetchone()
    if operation_row is None or operation_row["status"] not in {"applying", "recovery_required"}:
        raise ValueError(_STORAGE_INVALID)
    operation = _decode_local_apply_operation(operation_row["facts_json"])
    application_id = operation_row["journal_application_id"]
    if (
        application_id not in {operation["primary_application_id"], operation["recovery_application_id"]}
        or operation["final_patch_sha256"] != patch_hash
        or operation["final_patch_size_bytes"] != len(patch)
        or operation["run_id"] != run_id
        or operation["attempt_id"] != attempt_id
    ):
        raise ValueError(_STORAGE_INVALID)
    journal = read_local_apply_transaction_evidence(
        project_path=task.project_path,
        application_id=application_id,
        expected_common_git_identity=task.git_dir_identity,
    )
    if journal["patch_sha256"] != patch_hash or journal["patch_size_bytes"] != len(patch):
        raise ValueError(_STORAGE_INVALID)

    receipt_relative = f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}/apply-receipt.json"
    try:
        receipt_bytes, receipt_identity = read_owned_file_with_identity(
            artifact_root, receipt_relative, maximum=65_536,
        )
        receipt_payload = json.loads(receipt_bytes.decode("utf-8", "strict"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None
    expected_receipt = {
        "schema_version": "his-local-agent-apply-receipt.v1",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "contract_hash": run["contract_hash"],
        "initial_head": run["initial_head"],
        "final_patch_sha256": patch_hash,
        "changed_paths_sha256": hashlib.sha256(json.dumps(changed_paths, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "remote_actions": False,
    }
    if receipt_payload != expected_receipt:
        raise ValueError(_STORAGE_INVALID)
    receipt = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "kind": "local_apply_receipt",
        "relative_path": receipt_relative,
        "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "size_bytes": len(receipt_bytes),
    }
    if outcome is not None:
        claimed_receipt = outcome.get("receipt")
        apply = outcome.get("apply")
        if (
            not isinstance(claimed_receipt, Mapping)
            or _review_artifact_fact(claimed_receipt, require_id=False) != receipt
            or not isinstance(apply, Mapping)
            or apply.get("status") != "success"
            or apply.get("application_id") != application_id
            or apply.get("transaction_state") not in {"applied", "already_applied"}
        ):
            raise ValueError(_STORAGE_INVALID)

    event_count = int(connection.execute(
        "select count(*) from local_agent_run_events where run_id=?", (run_id,),
    ).fetchone()[0])
    confirmation = tuple(confirmation_row[key] for key in (
        "run_id", "attempt_id", "token_hash", "requested_by", "binding_json",
        "issued_at", "expires_at", "status", "consumed_at",
    ))
    facts = (
        run_id,
        attempt_id,
        run["updated_at"],
        event_count,
        confirmation,
        tuple(operation_row[key] for key in (
            "run_id", "attempt_id", "operation_id", "token_hash", "facts_json",
            "journal_application_id", "status", "created_at", "updated_at",
        )),
        tuple(_freeze_review_artifact(item) for item in artifacts),
        _encode_confirmation_binding(binding),
        hashlib.sha256(json.dumps(artifact_identities, separators=(",", ":")).encode()).hexdigest(),
        hashlib.sha256(json.dumps(_stable_identity_chain(receipt_identity), separators=(",", ":")).encode()).hexdigest(),
        tuple(sorted(journal.items())),
        tuple(sorted((key, value) for key, value in source.items() if key != "dirty_allowed_paths")),
    )
    return facts, receipt


def _freeze_review_artifact(value: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(value.get(key) for key in ("id", "run_id", "attempt_id", "kind", "relative_path", "sha256", "size_bytes"))


def _public_review_artifact_fact(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": value["kind"], "relative_path": value["relative_path"],
        "sha256": value["sha256"], "size_bytes": value["size_bytes"],
    }


def _validate_review_artifact_files(
    run: Mapping[str, object],
    attempt_id: int,
    event_count: int,
    verdict: str,
    finding_count: int,
    authoritative: tuple[Mapping[str, object], ...],
    pending: tuple[Mapping[str, object], ...],
) -> tuple[tuple[tuple[object, ...], ...], tuple[object, ...]]:
    # Local import avoids a module cycle; both modules are fully initialized
    # before a Runner can request finalization.
    from app.local_agent_review import parse_local_agent_review, read_owned_file_with_identity

    try:
        worktree = Path(str(run["worktree_path"]))
        if not worktree.is_absolute():
            raise ValueError
        root = worktree.parent
        by_kind = {str(item["kind"]): item for item in pending}
        review_record, seal_record = by_kind["final_review"], by_kind["review_seal"]
        contents: dict[str, bytes] = {}
        evidence: list[tuple[object, ...]] = []
        for record in (*authoritative, *pending):
            size = record["size_bytes"]
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError
            content, identity = read_owned_file_with_identity(
                root, str(record["relative_path"]), maximum=size,
            )
            digest = hashlib.sha256(content).hexdigest()
            if len(content) != size or digest != record["sha256"]:
                raise ValueError
            kind = str(record["kind"])
            contents[kind] = content
            evidence.append((kind, record["relative_path"], digest, size, identity))
        review_bytes = contents["final_review"]
        seal_bytes = contents["review_seal"]
        task = load_local_agent_task_bytes(contents["task_contract"])
        parsed = parse_local_agent_review(review_bytes)
        if parsed.verdict != verdict or len(parsed.findings) != finding_count:
            raise ValueError
        seal = json.loads(seal_bytes.decode("utf-8", "strict"), object_pairs_hook=_unique_json_object)
        required = {
            "schema_version", "run_id", "attempt_id", "run_revision",
            "event_count", "verdict", "review_hash", "source_fingerprint",
            "worktree_fingerprint", "authoritative_artifacts", "review_artifact",
        }
        if not isinstance(seal, dict) or set(seal) != required:
            raise ValueError
        expected_authoritative = [_public_review_artifact_fact(item) for item in authoritative]
        if (
            seal["schema_version"] != "his-local-agent-review-seal.v1"
            or seal["run_id"] != run["id"]
            or seal["attempt_id"] != attempt_id
            or seal["run_revision"] != run["updated_at"]
            or seal["event_count"] != event_count
            or seal["verdict"] != verdict
            or seal["review_hash"] != parsed.review_hash
            or seal["authoritative_artifacts"] != expected_authoritative
            or seal["review_artifact"] != _public_review_artifact_fact(review_record)
            or any(not isinstance(seal[key], str) or _HASH.fullmatch(seal[key]) is None for key in ("source_fingerprint", "worktree_fingerprint"))
        ):
            raise ValueError
        canonical_seal = json.dumps(seal, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if canonical_seal != seal_bytes:
            raise ValueError
        return tuple(evidence), (
            task,
            task.task_key,
            task.contract_hash,
            task.initial_head,
            str(task.project_path),
            seal["review_hash"],
            seal["source_fingerprint"],
            seal["worktree_fingerprint"],
        )
    except (KeyError, MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _validate_finalization_tree_evidence_in_transaction(
    connection: database.sqlite3.Connection,
    *,
    run: Mapping[str, object],
    authoritative: tuple[Mapping[str, object], ...],
    sealed_finalization_facts: tuple[object, ...],
) -> None:
    """Re-read immutable review/task evidence and compare both sealed trees.

    This executes while ``finalize_review`` holds its IMMEDIATE transaction.
    It intentionally owns all filesystem reads: a Runner callback cannot
    supply mutable identity, source, worktree, or fingerprint facts.
    """

    try:
        (
            task,
            task_key,
            contract_hash,
            initial_head,
            project_path,
            _review_hash,
            source_fingerprint,
            worktree_fingerprint,
        ) = sealed_finalization_facts
        if (
            not isinstance(task, LocalAgentTask)
            or task.task_key != task_key
            or task.contract_hash != contract_hash
            or task.initial_head != initial_head
            or str(task.project_path) != project_path
            or task_key != run["task_key"]
            or contract_hash != run["contract_hash"]
            or initial_head != run["initial_head"]
            or not isinstance(source_fingerprint, str)
            or not isinstance(worktree_fingerprint, str)
            or _HASH.fullmatch(source_fingerprint) is None
            or _HASH.fullmatch(worktree_fingerprint) is None
        ):
            raise ValueError
        task_record = next(item for item in authoritative if item["kind"] == "task_contract")
        binding_row = connection.execute(
            "select binding_json from local_agent_workspace_bindings where run_id=?",
            (run["id"],),
        ).fetchone()
        if binding_row is None:
            raise ValueError
        binding = _decode_workspace_binding(binding_row["binding_json"])
        if (
            binding["worktree_path"] != run["worktree_path"]
            or binding["task_artifact"] != task_record["relative_path"]
            or binding["task_sha256"] != task_record["sha256"]
        ):
            raise ValueError
        worktree = Path(str(run["worktree_path"]))
        if (
            _finalization_directory_identity(worktree)
            != _workspace_identity(binding["worktree_identity"], expected_kind=stat.S_IFDIR)
            or _finalization_git_entry_identity(worktree)
            != _workspace_identity(binding["worktree_git_identity"], expected_kind=stat.S_IFREG)
        ):
            raise ValueError
        assert_local_agent_task_is_current(task)
        boundary = SafeGitBoundary(task.project_path)
        if _finalization_head(boundary, worktree) != task.initial_head:
            raise ValueError
        source_worktrees = set(binding["source_worktrees"])
        if _finalization_worktrees(boundary, task.project_path) != source_worktrees:
            raise ValueError
        source_metadata = {
            key: (value[0], value[1], value[2], value[3])
            for key, value in binding["source_metadata"].items()
        }
        if not _finalization_source_metadata_matches(
            task.project_path,
            source_worktrees,
            source_metadata,
            capture_git_metadata(task.project_path),
        ):
            raise ValueError
        if _finalization_tree_fingerprint(capture_local_agent_tree_snapshot(task.project_path)) != source_fingerprint:
            raise ValueError
        if _finalization_tree_fingerprint(capture_local_agent_tree_snapshot(worktree)) != worktree_fingerprint:
            raise ValueError
    except (AttributeError, KeyError, OSError, TypeError, ValueError, UnicodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _finalization_tree_fingerprint(value: object) -> str:
    try:
        return hashlib.sha256(json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _finalization_directory_identity(path: Path) -> tuple[int, int, int]:
    item = path.lstat()
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
        raise ValueError(_STORAGE_INVALID)
    return item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)


def _finalization_git_entry_identity(path: Path) -> tuple[int, int, int]:
    item = (path / ".git").lstat()
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode):
        raise ValueError(_STORAGE_INVALID)
    return item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)


def _finalization_head(boundary: SafeGitBoundary, worktree: Path) -> str:
    value = boundary.text(["rev-parse", "--verify", "HEAD"], cwd=worktree).strip()
    if len(value) not in {40, 64} or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(_STORAGE_INVALID)
    return value


def _finalization_worktrees(boundary: SafeGitBoundary, project_path: Path) -> set[str]:
    return {
        str(Path(line.removeprefix("worktree ")).resolve())
        for line in boundary.text(["worktree", "list", "--porcelain"], cwd=project_path).splitlines()
        if line.startswith("worktree ")
    }


def _finalization_source_metadata_matches(
    project_path: Path,
    source_worktrees: set[str],
    expected: Mapping[str, tuple[int, int, int, str]],
    current: Mapping[str, tuple[int, int, int, str]],
) -> bool:
    allowed = {
        f"worktrees/{Path(path).name}"
        for path in source_worktrees
        if Path(path) != project_path
    }
    for key in set(expected) | set(current):
        if expected.get(key) == current.get(key):
            continue
        if not any(key == prefix or key.startswith(prefix + "/") for prefix in allowed):
            if not key.startswith("objects/") or key in expected or key not in current:
                return False
    return True


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_STORAGE_INVALID)
        result[key] = value
    return result


def _thaw_review_artifact(value: tuple[object, ...]) -> dict[str, object]:
    if not isinstance(value, tuple) or len(value) != 7:
        raise ValueError(_STORAGE_INVALID)
    keys = ("id", "run_id", "attempt_id", "kind", "relative_path", "sha256", "size_bytes")
    return dict(zip(keys, value))


def _review_artifact_fact(value: Mapping[str, object], *, require_id: bool) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(_STORAGE_INVALID)
    result: dict[str, object] = {
        "run_id": _positive_id(value.get("run_id")),
        "attempt_id": _positive_id(value.get("attempt_id")) if not (require_id and value.get("attempt_id") is None) else None,
        "kind": _safe_alias(value.get("kind")),
        "relative_path": _safe_relative_path(value.get("relative_path")),
        "sha256": _digest(value.get("sha256")),
    }
    size = value.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= _MAX_ARTIFACT_BYTES:
        raise ValueError(_STORAGE_INVALID)
    result["size_bytes"] = size
    if require_id:
        result["id"] = _positive_id(value.get("id"))
    return result


def _process_start_identity(value: object) -> str:
    if not isinstance(value, str) or _PROCESS_START_IDENTITY.fullmatch(value) is None:
        raise ValueError(_STORAGE_INVALID)
    return value


def _encode_safe_mapping(value: Mapping[str, object], *, maximum: int = 4096) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(_STORAGE_INVALID)
    try:
        sanitized = redact_sensitive_mapping(value)
        if sanitized != value:
            raise ValueError(_STORAGE_INVALID)
        encoded = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > maximum:
            raise ValueError(_STORAGE_INVALID)
        return encoded
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _decode_safe_mapping(value: object, *, maximum: int = 4096) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise ValueError(_STORAGE_INVALID)
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or _encode_safe_mapping(parsed, maximum=maximum) != value:
            raise ValueError(_STORAGE_INVALID)
        return parsed
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _encode_confirmation_binding(value: Mapping[str, object]) -> str:
    expected = {
        "schema_version", "run_id", "attempt_id", "contract_hash", "initial_head",
        "requested_by", "expires_at", "artifacts", "artifact_identities_sha256",
        "final_patch_sha256", "final_manifest_sha256", "review_seal_sha256",
        "changed_paths_sha256", "repository_root_identity", "git_entry_identity",
        "git_dir_identity", "source_status_sha256", "source_worktrees_sha256",
        "unrelated_status_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(_STORAGE_INVALID)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 65_536:
            raise ValueError
        return encoded
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _decode_confirmation_binding(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65_536:
        raise ValueError(_STORAGE_INVALID)
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_json_object)
        if not isinstance(parsed, dict) or _encode_confirmation_binding(parsed) != value:
            raise ValueError
        return parsed
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


_LOCAL_APPLY_OPERATION_BASE_KEYS = {
    "schema_version", "run_id", "attempt_id", "contract_hash", "initial_head",
    "final_patch_sha256", "final_patch_size_bytes", "changed_paths",
    "changed_paths_sha256", "pre_source_status_sha256",
    "pre_source_worktrees_sha256", "pre_unrelated_status_sha256",
    "pre_file_states", "pre_status", "expected_post_file_states",
    "primary_application_id",
}


def _encode_local_apply_operation(value: Mapping[str, object], *, include_authority: bool) -> str:
    expected = set(_LOCAL_APPLY_OPERATION_BASE_KEYS)
    if include_authority:
        expected.update({"operation_id", "recovery_application_id"})
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(_STORAGE_INVALID)
    if (
        value.get("schema_version") != "his-local-agent-apply-operation.v1"
        or not isinstance(value.get("changed_paths"), list)
        or not value["changed_paths"]
        or value["changed_paths"] != sorted(set(value["changed_paths"]))
        or any(not isinstance(item, str) for item in value["changed_paths"])
    ):
        raise ValueError(_STORAGE_INVALID)
    for key in (
        "contract_hash", "final_patch_sha256", "changed_paths_sha256",
        "pre_source_status_sha256", "pre_source_worktrees_sha256", "pre_unrelated_status_sha256",
    ):
        _digest(value.get(key))
    initial_head = value.get("initial_head")
    if not isinstance(initial_head, str) or len(initial_head) not in {40, 64} or any(character not in "0123456789abcdef" for character in initial_head):
        raise ValueError(_STORAGE_INVALID)
    if include_authority:
        _digest(value.get("operation_id"))
        for key in ("primary_application_id", "recovery_application_id"):
            item = value.get(key)
            if not isinstance(item, str) or len(item) != 24 or any(character not in "0123456789abcdef" for character in item):
                raise ValueError(_STORAGE_INVALID)
    size = value.get("final_patch_size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= _MAX_ARTIFACT_BYTES:
        raise ValueError(_STORAGE_INVALID)
    _positive_id(value.get("run_id"))
    _positive_id(value.get("attempt_id"))
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 262_144:
            raise ValueError
        return encoded
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _decode_local_apply_operation(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 262_144:
        raise ValueError(_STORAGE_INVALID)
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_json_object)
        if not isinstance(parsed, dict) or _encode_local_apply_operation(parsed, include_authority=True) != value:
            raise ValueError
        return parsed
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _encode_workspace_binding(value: Mapping[str, object]) -> str:
    """Bindings contain absolute local paths, which generic secret redaction
    correctly treats as untrusted text; validate their fixed structural shape
    instead of copying them into event/audit surfaces."""
    if not isinstance(value, Mapping) or set(value) != {"worktree_path", "source_metadata", "source_worktrees", "worktree_identity", "worktree_git_identity", "marker_path", "task_artifact", "task_sha256"}:
        raise ValueError(_STORAGE_INVALID)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 65_536:
            raise ValueError
        parsed = json.loads(encoded)
        if not isinstance(parsed, dict):
            raise ValueError
        worktree_path = parsed["worktree_path"]
        marker_path = parsed["marker_path"]
        task_artifact = parsed["task_artifact"]
        if (
            not isinstance(worktree_path, str)
            or not isinstance(marker_path, str)
            or not isinstance(task_artifact, str)
            or _HASH.fullmatch(str(parsed["task_sha256"])) is None
            or len(worktree_path) > 1024
            or len(marker_path) > 1024
            or len(task_artifact) > 512
            or "\n" in worktree_path
            or "\r" in worktree_path
        ):
            raise ValueError
        worktree = PurePosixPath(worktree_path)
        root = worktree.parent
        if (
            not worktree.is_absolute()
            or _LOCAL_AGENT_WORKTREE_ROOT.fullmatch(root.as_posix()) is None
            or re.fullmatch(r"run_[1-9][0-9]*(?:_attempt_[2-9][0-9]*)?", worktree.name) is None
            or marker_path != (root / ".harness_worktree_markers" / (hashlib.sha256(worktree_path.encode("utf-8")).hexdigest() + ".json")).as_posix()
            or re.fullmatch(r"\.harness_local_agent_control/run_[1-9][0-9]*/task\.json", task_artifact) is None
        ):
            raise ValueError
        _workspace_identity(parsed["worktree_identity"], expected_kind=stat.S_IFDIR)
        _workspace_identity(parsed["worktree_git_identity"], expected_kind=stat.S_IFREG)
        source_worktrees = parsed["source_worktrees"]
        if (
            not isinstance(source_worktrees, list)
            or len(source_worktrees) > 256
            or source_worktrees != sorted(set(source_worktrees))
            or any(
                not isinstance(item, str)
                or not item.startswith("/")
                or len(item) > 1024
                or "\n" in item
                or "\r" in item
                for item in source_worktrees
            )
        ):
            raise ValueError
        metadata = parsed["source_metadata"]
        if not isinstance(metadata, dict) or len(metadata) > 10_000:
            raise ValueError
        for key, item in metadata.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 512
                or key.startswith("/")
                or "\\" in key
                or not isinstance(item, list)
                or len(item) != 4
                or any(not isinstance(number, int) or isinstance(number, bool) or number < 0 for number in item[:3])
                or not isinstance(item[3], str)
                or (item[3] and _HASH.fullmatch(item[3]) is None)
            ):
                raise ValueError
        return encoded
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _decode_workspace_binding(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode()) > 65_536:
        raise ValueError(_STORAGE_INVALID)
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or _encode_workspace_binding(parsed) != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _workspace_identity(value: object, *, expected_kind: int) -> tuple[int, int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in value)
        or value[0] <= 0
        or value[1] <= 0
        or value[2] != expected_kind
    ):
        raise ValueError(_STORAGE_INVALID)
    return value[0], value[1], value[2]


def _learning_statuses(value: object) -> frozenset[str]:
    if not isinstance(value, frozenset) or not value:
        raise ValueError(_STORAGE_INVALID)
    result = frozenset(_safe_alias(item) for item in value)
    if result != value or not result.issubset(_RUN_STATUSES):
        raise ValueError(_STORAGE_INVALID)
    return result


def _learning_source_key(
    run_id: int,
    attempt_id: int,
    source_kind: str,
    root_cause_kind: str,
) -> str:
    return (
        f"retro-r{run_id}-a{attempt_id}-s{_LEARNING_SOURCE_CODES[source_kind]}"
        f"-c{_LEARNING_ROOT_CAUSE_CODES[root_cause_kind]}"
    )


def _learning_binding_in_transaction(
    connection: database.sqlite3.Connection,
    *,
    task: LocalAgentTask,
    run_id: int,
    attempt_id: int | None,
    allowed_run_statuses: frozenset[str] | None,
) -> dict[str, object]:
    """Verify durable learning identity while the caller holds IMMEDIATE.

    The transaction holder must write the retrospective/observation before it
    releases the reservation.  Returning this projection to a read-only
    caller is permitted; write callers remain in this exact transaction.
    """

    assert_local_agent_task_is_current(task)
    run = _require_run(connection, run_id)
    if run["task_key"] != task.task_key or run["contract_hash"] != task.contract_hash:
        raise ValueError(_STORAGE_INVALID)
    latest = _attempt_from_row(connection.execute(
        "select * from local_agent_attempts where run_id=? order by attempt_no desc limit 1",
        (run_id,),
    ).fetchone())
    if attempt_id is not None and int(latest["id"]) != attempt_id:
        raise ValueError(_STORAGE_INVALID)
    if allowed_run_statuses is not None and (
        run["status"] not in allowed_run_statuses or latest["status"] != "completed"
    ):
        raise ValueError("repair_learning_input_invalid")
    binding_row = connection.execute(
        "select binding_json from local_agent_workspace_bindings where run_id=?", (run_id,),
    ).fetchone()
    if binding_row is None:
        raise ValueError(_STORAGE_INVALID)
    binding = _decode_workspace_binding(binding_row["binding_json"])
    expected_artifact = f"{_CONTROL}/run_{run_id}/task.json"
    expected_worktree = re.compile(rf"^run_{run_id}(?:_attempt_[2-9][0-9]*)?$")
    if (
        binding["task_artifact"] != expected_artifact
        or not expected_worktree.fullmatch(PurePosixPath(str(binding["worktree_path"])).name)
    ):
        raise ValueError(_STORAGE_INVALID)
    return {
        "run_id": run_id,
        "attempt_id": int(latest["id"]),
        "task_key": str(run["task_key"]),
        "contract_hash": str(run["contract_hash"]),
        "run_status": str(run["status"]),
        "attempt_status": str(latest["status"]),
        "workspace_fingerprint": _workspace_fingerprint(binding),
    }


def _workspace_fingerprint(binding: Mapping[str, object]) -> str:
    """One-way, cross-run stable identity of a decoded workspace binding."""

    stable_identity = {
        "worktree_identity": binding["worktree_identity"],
        "worktree_git_identity": binding["worktree_git_identity"],
    }
    encoded = json.dumps(
        stable_identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "ws" + hashlib.sha256(encoded).hexdigest()[:21]


def _learning_rule_matches_durable_task(
    row: Any,
    *,
    task: LocalAgentTask,
    binding: Mapping[str, object],
) -> bool:
    """Accept only a current rule applicable to the bound task contract."""

    try:
        payload = _decode_learning_rule_json(row["rule_json"])
        state = LearningRuleState(str(row["state"]))
        if str(payload["state"]) != state.value:
            return False
        if state not in {
            LearningRuleState.ACTIVE_CURRENT_TASK,
            LearningRuleState.TRIAL,
            LearningRuleState.STABLE,
        }:
            return False
        match = payload["match"]
        if not isinstance(match, Mapping):
            return False
        run_id = _positive_id(binding["run_id"])
        context = derive_task_learning_context(task, run_id=run_id)
        exact_scope = (
            match["allowed_path_prefixes"] == list(context.allowed_path_prefixes)
            and match["verification_command_fingerprints"]
            == list(context.verification_command_fingerprints)
            and match["high_risk_tags"] == list(context.high_risk_tags)
            and match["failure_sources"] == list(context.failure_sources)
        )
        if state is LearningRuleState.ACTIVE_CURRENT_TASK:
            return (
                row["active_run_id"] == run_id
                and match["run_id"] == run_id
                and match["task_key"] == context.task_key
                and exact_scope
            )
        return (
            context.repository_kind != "unknown"
            and match["repository_kind"] == context.repository_kind
            and exact_scope
        )
    except (KeyError, TypeError, ValueError):
        return False


def _learning_storage_rule_key(payload: Mapping[str, object]) -> str:
    """Return the immutable persistence key used by repair-learning rules."""

    normalized = validate_rule_payload(payload)
    immutable = {
        key: value
        for key, value in normalized.items()
        if key not in {"state", "promotion_evidence"}
    }
    encoded = json.dumps(
        immutable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_learning_observation_evidence(
    evidence: Mapping[str, object],
    *,
    outcome: str,
) -> str:
    """Encode the only fixed, audit-safe learning observation evidence forms."""

    if type(evidence) is not dict:
        raise ValueError("repair_learning_input_invalid")
    if outcome == RuleObservationOutcome.MATCHED.value:
        if evidence != {"event": "verification_and_review_passed"}:
            raise ValueError("repair_learning_input_invalid")
    elif outcome == RuleObservationOutcome.NOT_MATCHED.value:
        _validate_learning_counterexample_evidence(evidence)
    else:  # pragma: no cover - public enum validation above
        raise ValueError("repair_learning_input_invalid")
    return _encode_learning_mapping(evidence)


def _encode_learning_safe_summary(value: Mapping[str, object]) -> str:
    if type(value) is not dict:
        raise ValueError("repair_learning_input_invalid")
    _validate_learning_safe_summary(value)
    return _encode_learning_mapping(value)


def _encode_learning_task_context(
    value: Mapping[str, object],
    *,
    task: LocalAgentTask,
    run_id: int,
) -> str:
    if type(value) is not dict:
        raise ValueError("repair_learning_input_invalid")
    expected = _learning_task_context_payload(task, run_id=run_id)
    if value != expected:
        raise ValueError("repair_learning_input_invalid")
    return _encode_learning_context_mapping(expected)


def _learning_task_context_payload(task: LocalAgentTask, *, run_id: int) -> dict[str, object]:
    try:
        context = derive_task_learning_context(task, run_id=run_id)
    except (TypeError, ValueError):
        raise ValueError("repair_learning_input_invalid") from None
    return {
        "run_id": context.run_id,
        "task_key": context.task_key,
        "repository_kind": context.repository_kind,
        "allowed_path_prefixes": list(context.allowed_path_prefixes),
        "verification_command_fingerprints": list(context.verification_command_fingerprints),
        "high_risk_tags": list(context.high_risk_tags),
        "failure_sources": list(context.failure_sources),
    }


def _validate_learning_counterexample_evidence(evidence: dict[str, object]) -> None:
    if evidence.get("event") != "counterexample":
        raise ValueError("repair_learning_input_invalid")
    _validate_learning_safe_summary(evidence, required_event="counterexample")


def _validate_learning_safe_summary(
    summary: dict[str, object],
    *,
    required_event: str | None = None,
) -> None:
    if required_event is not None and summary.get("event") != required_event:
        raise ValueError("repair_learning_input_invalid")
    status = summary.get("summary_status")
    if status == "empty":
        expected = {"summary_status"}
    elif status == "redacted":
        expected = {"summary_status", "summary_sha256"}
        if not isinstance(summary.get("summary_sha256"), str) or _LEARNING_SAFE_SUMMARY_HASH.fullmatch(
            str(summary["summary_sha256"]),
        ) is None:
            raise ValueError("repair_learning_input_invalid")
    elif status == "safe":
        expected = {"summary_status", "summary"}
        if not _is_safe_learning_summary(summary.get("summary")):
            raise ValueError("repair_learning_input_invalid")
    else:
        raise ValueError("repair_learning_input_invalid")
    if required_event is not None:
        expected.add("event")
    if set(summary) != expected:
        raise ValueError("repair_learning_input_invalid")


def _is_safe_learning_summary(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 12_000:
        return False
    line_preserving = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 320:
        return False
    try:
        secret_scan = normalize_sensitive_text(line_preserving)
    except (TypeError, ValueError, UnicodeError):
        return False
    return not (
        _LEARNING_UNTRUSTED_CONTENT.search(line_preserving)
        or _LEARNING_PATCH_CONTENT.search(line_preserving)
        or _LEARNING_STANDALONE_SECRET.search(line_preserving)
        or _LEARNING_STANDALONE_SECRET.search(secret_scan)
        or contains_sensitive_text(normalized)
        or redact_sensitive_text(normalized) != normalized
    )


def _encode_learning_mapping(value: Mapping[str, object]) -> str:
    """Match the learning repository's canonical redacted JSON encoding."""

    if not isinstance(value, Mapping):
        raise ValueError("repair_learning_input_invalid")
    try:
        return json.dumps(
            redact_sensitive_mapping(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError):
        raise ValueError("repair_learning_input_invalid") from None


_LEARNING_CONTEXT_KEYS = frozenset(
    {
        "run_id", "task_key", "repository_kind", "allowed_path_prefixes",
        "verification_command_fingerprints", "high_risk_tags", "failure_sources",
    }
)
_LEARNING_REPOSITORY_KINDS = frozenset(("python", "node", "gradle", "unknown"))
_LEARNING_CONTEXT_TEXT = re.compile(r"[A-Za-z0-9._/-]{1,256}")


def _encode_learning_context_mapping(value: Mapping[str, object]) -> str:
    """Encode a fixed, already allow-listed learning context.

    Generic audit maps deliberately use conservative key heuristics.  This
    dedicated map is narrower: it validates every expected field first, then
    serializes the exact schema so harmless path fields survive persistence.
    """

    _validate_learning_context_mapping(value)
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError):
        raise ValueError("repair_learning_input_invalid") from None
    if len(encoded.encode("utf-8")) > 65_536:
        raise ValueError("repair_learning_input_invalid")
    return encoded


def _decode_learning_context_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65_536:
        raise ValueError(_STORAGE_INVALID)
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_json_object)
        if not isinstance(parsed, dict):
            raise ValueError
        if set(parsed) != _LEARNING_CONTEXT_KEYS:
            # Keep pre-fix snapshots readable.  Redacted legacy contexts are
            # not eligible for new cross-run matching because their fixed
            # fields are unavailable, but they remain auditable.
            return _decode_learning_mapping(value)
        if _encode_learning_context_mapping(parsed) != value:
            raise ValueError
        return parsed
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _validate_learning_context_mapping(value: Mapping[str, object]) -> None:
    if not isinstance(value, Mapping) or set(value) != _LEARNING_CONTEXT_KEYS:
        raise ValueError("repair_learning_input_invalid")
    _positive_id(value["run_id"])
    _safe_task_key(value["task_key"])
    if value["repository_kind"] not in _LEARNING_REPOSITORY_KINDS:
        raise ValueError("repair_learning_input_invalid")
    _learning_context_strings(value["allowed_path_prefixes"], allow_empty=False, path=True)
    _learning_context_strings(
        value["verification_command_fingerprints"], allow_empty=False, digest=True,
    )
    _learning_context_strings(value["high_risk_tags"], allow_empty=True)
    _learning_context_strings(value["failure_sources"], allow_empty=True)


def _learning_context_strings(
    value: object,
    *,
    allow_empty: bool,
    path: bool = False,
    digest: bool = False,
) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError("repair_learning_input_invalid")
    for item in value:
        if not isinstance(item, str) or not item or "\\" in item or ".." in item.split("/"):
            raise ValueError("repair_learning_input_invalid")
        if digest:
            if _HASH.fullmatch(item) is None:
                raise ValueError("repair_learning_input_invalid")
        elif path:
            if _LEARNING_CONTEXT_TEXT.fullmatch(item) is None or item.startswith("/"):
                raise ValueError("repair_learning_input_invalid")
        else:
            _safe_alias(item)


def _learning_retrospective_from_row(row: Any) -> dict[str, object]:
    return {
        "id": _positive_id(row["id"]),
        "source_key": _safe_alias(row["source_key"]),
        "run_id": _positive_id(row["run_id"]),
        "attempt_id": _positive_id(row["attempt_id"]),
        "source_kind": _safe_alias(row["source_kind"]),
        "root_cause_kind": _safe_alias(row["root_cause_kind"]),
        "safe_summary": _decode_learning_mapping(row["safe_summary_json"]),
        "task_context": _decode_learning_context_mapping(row["task_context_json"]),
        "created_at": _timestamp(row["created_at"]),
    }


def _learning_observation_from_row(row: Any) -> dict[str, object]:
    return {
        "id": _positive_id(row["id"]),
        "rule_id": _positive_id(row["rule_id"]),
        "run_id": _positive_id(row["run_id"]),
        "attempt_id": _positive_id(row["attempt_id"]),
        "task_key": _safe_task_key(row["task_key"]),
        "workspace_fingerprint": _safe_alias(row["workspace_fingerprint"]),
        "outcome": _safe_alias(row["outcome"]),
        "evidence": _decode_learning_mapping(row["evidence_json"]),
        "observed_at": _timestamp(row["observed_at"]),
    }


def _decode_learning_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65_536:
        raise ValueError(_STORAGE_INVALID)
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or _encode_learning_mapping(parsed) != value:
            raise ValueError(_STORAGE_INVALID)
        return parsed
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _learning_refresh_counts_and_suspend(
    connection: database.sqlite3.Connection,
    rule_id: int,
    observed_at: str,
) -> None:
    """Refresh evidence inside the observation's binding transaction only."""

    counts = connection.execute(
        """
        select
          count(distinct case when outcome = 'matched' then task_key end),
          count(distinct case when outcome = 'matched' then workspace_fingerprint end),
          sum(case when outcome = 'not_matched' then 1 else 0 end)
        from repair_learning_observations where rule_id=?
        """,
        (rule_id,),
    ).fetchone()
    counterexamples = int(counts[2] or 0)
    row = connection.execute(
        "select * from repair_learning_rules where id=?", (rule_id,),
    ).fetchone()
    if row is None:
        raise KeyError(rule_id)
    rule_json = str(row["rule_json"])
    if counterexamples > 0 and str(row["state"]) not in {
        LearningRuleState.SUSPENDED.value,
        LearningRuleState.RETIRED.value,
    }:
        rule_json = _learning_transition_rule_json(
            connection,
            row,
            target=LearningRuleState.SUSPENDED.value,
        )
    connection.execute(
        """
        update repair_learning_rules
        set verified_task_count=?, distinct_workspace_count=?,
            counterexample_count=?, rule_json=?,
            state=case when ? > 0 and state != 'retired' then 'suspended' else state end,
            state_version=state_version + case
                when ? > 0 and state not in ('suspended', 'retired') then 1 else 0 end,
            suspended_at=case
                when ? > 0 and state not in ('suspended', 'retired') then ? else suspended_at end,
            updated_at=?
        where id=?
        """,
        (
            int(counts[0] or 0), int(counts[1] or 0), counterexamples, rule_json,
            counterexamples, counterexamples, counterexamples, observed_at,
            observed_at, rule_id,
        ),
    )


def _advance_learning_rule_after_success(
    connection: database.sqlite3.Connection,
    rule_id: int,
) -> None:
    """Advance active/trial learning state within final-review atomicity."""

    row = connection.execute(
        "select * from repair_learning_rules where id=?", (rule_id,),
    ).fetchone()
    if row is None:
        raise ValueError(_STORAGE_INVALID)
    state = str(row["state"])
    if state == LearningRuleState.ACTIVE_CURRENT_TASK.value:
        target = LearningRuleState.TRIAL.value
    elif (
        state == LearningRuleState.TRIAL.value
        and not _learning_rule_is_high_risk(row)
        and int(row["verified_task_count"]) >= 3
        and int(row["distinct_workspace_count"]) >= 2
        and int(row["counterexample_count"]) == 0
    ):
        target = LearningRuleState.STABLE.value
    else:
        return
    rule_json = _learning_transition_rule_json(connection, row, target=target)
    if connection.execute(
        """update repair_learning_rules
              set state=?, rule_json=?, state_version=state_version+1, updated_at=?
            where id=? and state_version=? and state=?""",
        (
            target, rule_json, database.now_iso(), rule_id,
            int(row["state_version"]), state,
        ),
    ).rowcount != 1:
        raise ValueError("repair_learning_state_conflict")


def _learning_rule_is_high_risk(row: Any) -> bool:
    payload = _decode_learning_rule_json(row["rule_json"])
    match = payload.get("match")
    if not isinstance(match, Mapping):
        raise ValueError(_STORAGE_INVALID)
    tags = match.get("high_risk_tags")
    if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags):
        raise ValueError(_STORAGE_INVALID)
    return bool(tags)


def _learning_transition_rule_json(
    connection: database.sqlite3.Connection,
    row: Any,
    *,
    target: str,
) -> str:
    payload = _decode_learning_rule_json(row["rule_json"])
    payload["state"] = target
    if target == LearningRuleState.STABLE.value:
        evidence_rows = connection.execute(
            """
            select task_key, workspace_fingerprint
            from repair_learning_observations
            where rule_id=? and outcome='matched'
            order by task_key, workspace_fingerprint
            """,
            (int(row["id"]),),
        ).fetchall()
        payload["promotion_evidence"] = {
            "task_keys": sorted({str(item["task_key"]) for item in evidence_rows}),
            "workspace_fingerprints": sorted({
                str(item["workspace_fingerprint"]) for item in evidence_rows
            }),
            "counterexample_count": int(row["counterexample_count"]),
        }
    return canonical_rule_bytes(payload).decode("utf-8")


def _decode_learning_rule_json(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise ValueError(_STORAGE_INVALID)
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or canonical_rule_bytes(parsed).decode("utf-8") != value:
            raise ValueError
        normalized = validate_rule_payload(parsed)
        return json.loads(canonical_rule_bytes(normalized).decode("utf-8"))
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _required_text(value: object, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or contains_sensitive_text(value):
        raise ValueError(_STORAGE_INVALID)
    return value


def _timestamp(value: object) -> str:
    text = _required_text(value)
    try:
        database.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(_STORAGE_INVALID) from None
    return text


def _timestamp_datetime(value: object) -> database.datetime:
    text = _timestamp(value)
    try:
        parsed = database.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(_STORAGE_INVALID) from None
    if parsed.tzinfo is None:
        raise ValueError(_STORAGE_INVALID)
    return parsed


def _digest(value: object) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError(_STORAGE_INVALID)
    return value


def _authorization_digest(value: object) -> str:
    if not isinstance(value, str) or _AUTHORIZATION_HASH.fullmatch(value) is None:
        raise ValueError(_STORAGE_INVALID)
    return value


def _git_head(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40,64}", value) is None:
        raise ValueError(_STORAGE_INVALID)
    return value


def _identity(value: object) -> dict[str, object]:
    result = _decode_safe_mapping(value)
    if set(result) != {"repository_root", "git_entry", "git_dir"}:
        raise ValueError(_STORAGE_INVALID)
    for item in result.values():
        if not isinstance(item, list) or len(item) != 2 or any(not isinstance(number, int) or isinstance(number, bool) or number < 0 for number in item):
            raise ValueError(_STORAGE_INVALID)
    return result


def _run_from_row(row: Any) -> dict[str, object]:
    if row is None:
        raise ValueError(_STORAGE_INVALID)
    result = {
        "id": _positive_id(row["id"]), "task_key": _safe_task_key(row["task_key"]),
        "contract_hash": _digest(row["contract_hash"]), "authorization_hash": _authorization_digest(row["authorization_hash"]),
        "project_identity": _identity(row["project_identity_json"]), "initial_head": _git_head(row["initial_head"]),
        "worktree_path": row["worktree_path"], "status": _safe_alias(row["status"]), "summary": _decode_safe_mapping(row["summary_json"]),
        "created_at": _timestamp(row["created_at"]), "updated_at": _timestamp(row["updated_at"]),
    }
    if result["status"] not in _RUN_STATUSES or _timestamp_datetime(result["updated_at"]) < _timestamp_datetime(result["created_at"]):
        raise ValueError(_STORAGE_INVALID)
    if not isinstance(result["worktree_path"], str) or len(result["worktree_path"]) > 1024 or contains_sensitive_text(result["worktree_path"]):
        raise ValueError(_STORAGE_INVALID)
    return result


def _attempt_from_row(row: Any) -> dict[str, object]:
    if row is None:
        raise ValueError(_STORAGE_INVALID)
    pid = row["worker_pid"]
    if pid is not None:
        pid = _positive_id(pid)
    result = {
        "id": _positive_id(row["id"]), "run_id": _positive_id(row["run_id"]), "attempt_no": _positive_id(row["attempt_no"]),
        "status": _safe_alias(row["status"]), "worker_pid": pid, "worker_start_identity": row["worker_start_identity"],
        "error_code": _safe_optional_alias(row["error_code"]), "started_at": _timestamp(row["started_at"]),
        "finished_at": None if row["finished_at"] is None else _timestamp(row["finished_at"]),
    }
    if result["status"] not in _ATTEMPT_STATUSES or not isinstance(result["worker_start_identity"], str) or len(result["worker_start_identity"]) > 128:
        raise ValueError(_STORAGE_INVALID)
    active = result["status"] in _ACTIVE_ATTEMPT_STATUSES
    if active and result["finished_at"] is not None:
        raise ValueError(_STORAGE_INVALID)
    if result["status"] == "starting" and (result["worker_pid"] is not None or result["worker_start_identity"] or result["error_code"]):
        raise ValueError(_STORAGE_INVALID)
    if result["status"] == "worker_running" and (result["worker_pid"] is None or _PROCESS_START_IDENTITY.fullmatch(result["worker_start_identity"]) is None or result["error_code"]):
        raise ValueError(_STORAGE_INVALID)
    if not active and (result["finished_at"] is None or (result["status"] == "completed") != (result["error_code"] == "")):
        raise ValueError(_STORAGE_INVALID)
    if result["finished_at"] is not None and _timestamp_datetime(result["finished_at"]) < _timestamp_datetime(result["started_at"]):
        raise ValueError(_STORAGE_INVALID)
    unbound_start_failure = result["status"] == "interrupted" and result["error_code"] == "worker_start_failed"
    if unbound_start_failure and (result["worker_pid"] is not None or result["worker_start_identity"]):
        raise ValueError(_STORAGE_INVALID)
    if not active and not unbound_start_failure and (result["worker_pid"] is None or _PROCESS_START_IDENTITY.fullmatch(result["worker_start_identity"]) is None):
        raise ValueError(_STORAGE_INVALID)
    return result


def _event_from_row(row: Any) -> dict[str, object]:
    if row is None:
        raise ValueError(_STORAGE_INVALID)
    event_type = _safe_alias(row["event_type"])
    payload = (
        _decode_validated_audit_mapping(row["payload_json"])
        if event_type in {"harness_decision_issued", "review_failed", "worker_protocol_rejected"}
        else _decode_safe_mapping(row["payload_json"])
    )
    if event_type == "review_failed":
        _validate_review_failure(payload)
    elif event_type == "worker_protocol_rejected":
        try:
            validate_protocol_rejection_audit(payload)
        except ValueError:
            raise ValueError(_STORAGE_INVALID) from None
    elif event_type == "harness_decision_issued":
        _validate_harness_decision_event(payload)
    return {"id": _positive_id(row["id"]), "run_id": _positive_id(row["run_id"]), "attempt_id": _optional_positive_id(row["attempt_id"]), "sequence_no": _positive_id(row["sequence_no"]), "event_type": event_type, "payload": payload, "created_at": _timestamp(row["created_at"])}


def _encode_review_failure(value: Mapping[str, object]) -> str:
    _validate_review_failure(value)
    return _encode_validated_audit_mapping(value)


def _encode_validated_audit_mapping(value: Mapping[str, object]) -> str:
    """Encode only payloads whose exact schema was validated by the caller."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError
        return encoded
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _decode_validated_audit_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
        raise ValueError(_STORAGE_INVALID)
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_json_object)
        if not isinstance(parsed, dict) or _encode_validated_audit_mapping(parsed) != value:
            raise ValueError
        return parsed
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError(_STORAGE_INVALID) from None


def _validate_harness_decision_event(value: Mapping[str, object]) -> None:
    """Validate the fixed decision audit schema before bypassing text redaction."""

    expected = {
        "plan_version", "supersedes_plan_version", "decision_kind",
        "failure_code", "decision_digest", "must_reinspect", "execute_only",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(_STORAGE_INVALID)
    plan_version = value.get("plan_version")
    supersedes = value.get("supersedes_plan_version")
    if (
        not isinstance(plan_version, int)
        or isinstance(plan_version, bool)
        or plan_version <= 0
        or (
            (plan_version == 1 and supersedes is not None)
            or (plan_version > 1 and supersedes != plan_version - 1)
        )
        or value.get("decision_kind") != ("initial_plan" if plan_version == 1 else "replan")
        or value.get("failure_code") not in {
            "initial_execution", "workspace_preparation_failed", "worker_interrupted",
            "worker_failed", "verification_failed", "review_changes_requested",
            "recovery_replan",
        }
        or not isinstance(value.get("decision_digest"), str)
        or _AUTHORIZATION_HASH.fullmatch(value["decision_digest"]) is None
        or value.get("must_reinspect") is not True
        or value.get("execute_only") is not True
    ):
        raise ValueError(_STORAGE_INVALID)

def _validate_review_failure(value: Mapping[str, object]) -> None:
    if set(value) == {"reason"}:
        if value.get("reason") not in {"review_failed", "stale_review"}:
            raise ValueError(_STORAGE_INVALID)
        return
    if set(value) in (
        {"reason", "validation_code"},
        {"reason", "validation_code", "value_kind", "known_fields_mask", "field_count"},
    ):
        if value.get("reason") != "review_failed" or value.get("validation_code") not in {
            "json_invalid", "fields_invalid", "schema_invalid", "verdict_invalid",
            "summary_invalid", "findings_invalid",
            "review_hash_invalid", "response_digest_mismatch",
        }:
            raise ValueError(_STORAGE_INVALID)
        if set(value) != {"reason", "validation_code"}:
            if value.get("validation_code") != "fields_invalid" or value.get("value_kind") not in {"object", "other"}:
                raise ValueError(_STORAGE_INVALID)
            mask, count = value.get("known_fields_mask"), value.get("field_count")
            if (
                not isinstance(mask, int) or isinstance(mask, bool) or not 0 <= mask <= 31
                or not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 16
                or (value.get("value_kind") == "other" and (mask != 0 or count != 0))
                or (value.get("value_kind") == "object" and mask.bit_count() > count)
            ):
                raise ValueError(_STORAGE_INVALID)
        return
    expected = {"reason", "error_code", "process_returncode", "stdout_sha256", "stderr_sha256", "terminal_shape", "elapsed_bucket"}
    if set(value) != expected or value.get("reason") != "review_failed":
        raise ValueError(_STORAGE_INVALID)
    if value.get("error_code") not in STABLE_WORKER_ERROR_CODES | {"worker_unclassified"} or value.get("elapsed_bucket") not in {"under_10s", "10_59s", "60_179s", "180_360s", "over_360s"}:
        raise ValueError(_STORAGE_INVALID)
    returncode = value.get("process_returncode")
    if returncode is not None and (not isinstance(returncode, int) or isinstance(returncode, bool) or not -255 <= returncode <= 255):
        raise ValueError(_STORAGE_INVALID)
    for key in ("stdout_sha256", "stderr_sha256"):
        if not isinstance(value.get(key), str) or _HASH.fullmatch(str(value[key])) is None:
            raise ValueError(_STORAGE_INVALID)
    shape = value.get("terminal_shape")
    if not isinstance(shape, Mapping) or set(shape) not in ({"type"}, {"type", "item_type"}) or any(not isinstance(item, str) or re.fullmatch(r"[a-z_.]{1,32}", item) is None for item in shape.values()):
        raise ValueError(_STORAGE_INVALID)


def _artifact_from_row(row: Any) -> dict[str, object]:
    if row is None:
        raise ValueError(_STORAGE_INVALID)
    size = row["size_bytes"]
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= _MAX_ARTIFACT_BYTES or not isinstance(row["sha256"], str) or _HASH.fullmatch(row["sha256"]) is None:
        raise ValueError(_STORAGE_INVALID)
    return {"id": _positive_id(row["id"]), "run_id": _positive_id(row["run_id"]), "attempt_id": _optional_positive_id(row["attempt_id"]), "kind": _safe_alias(row["kind"]), "relative_path": _safe_relative_path(row["relative_path"]), "sha256": row["sha256"], "size_bytes": size, "created_at": _timestamp(row["created_at"])}


def _require_run(connection: database.sqlite3.Connection, run_id: int) -> dict[str, object]:
    return _run_from_row(connection.execute("select * from local_agent_runs where id = ?", (run_id,)).fetchone())


def _require_attempt(connection: database.sqlite3.Connection, attempt_id: int) -> dict[str, object]:
    return _attempt_from_row(connection.execute("select * from local_agent_attempts where id = ?", (attempt_id,)).fetchone())


def _require_attempt_belongs_to_run(connection: database.sqlite3.Connection, run_id: int, attempt_id: int | None) -> None:
    if attempt_id is not None and _require_attempt(connection, attempt_id)["run_id"] != run_id:
        raise ValueError(_STORAGE_INVALID)


def _append_event_in_transaction(connection: database.sqlite3.Connection, run_id: int, attempt_id: int | None, event_type: str, payload_json: str) -> dict[str, object]:
    sequence = int(connection.execute("select coalesce(max(sequence_no), 0) + 1 from local_agent_run_events where run_id = ?", (run_id,)).fetchone()[0])
    cursor = connection.execute("insert into local_agent_run_events(run_id, attempt_id, sequence_no, event_type, payload_json, created_at) values(?, ?, ?, ?, ?, ?)", (run_id, attempt_id, sequence, event_type, payload_json, database.now_iso()))
    return _event_from_row(connection.execute("select * from local_agent_run_events where id = ?", (cursor.lastrowid,)).fetchone())


def _abandon_starting_attempt_in_transaction(connection: database.sqlite3.Connection, attempt: Mapping[str, object], error_code: str) -> dict[str, object]:
    run_id, attempt_id = _positive_id(attempt["run_id"]), _positive_id(attempt["id"])
    if attempt["status"] != "starting" or _require_run(connection, run_id)["status"] != "worker_running":
        raise ValueError(_STORAGE_INVALID)
    now = database.now_iso()
    if connection.execute(
        "update local_agent_attempts set status='interrupted', error_code=?, finished_at=? where id=? and status='starting'",
        (error_code, now, attempt_id),
    ).rowcount != 1:
        raise ValueError(_STORAGE_INVALID)
    _update_run_after_attempt_in_transaction(
        connection,
        run_id,
        attempt_id,
        "worker_running",
        "interrupted",
        _encode_safe_mapping({"reason": "worker_start_failed"}),
        now,
    )
    _append_event_in_transaction(
        connection,
        run_id,
        attempt_id,
        "attempt_interrupted",
        _encode_safe_mapping({"reason": "worker_start_failed"}),
    )
    return _require_attempt(connection, attempt_id)


def _transition_in_transaction(connection: database.sqlite3.Connection, run_id: int, expected: str, target: str, summary_json: str) -> dict[str, object]:
    if target not in _ALLOWED_TRANSITIONS.get(expected, frozenset()):
        raise ValueError(_STATE_TRANSITION_INVALID)
    if _require_run(connection, run_id)["status"] != expected:
        raise ValueError(_STATE_TRANSITION_INVALID)
    attempt_row = connection.execute(
        "select id from local_agent_attempts where run_id=? order by attempt_no desc limit 1", (run_id,),
    ).fetchone()
    attempt_id = None if attempt_row is None else int(attempt_row["id"])
    if target in _ATTEMPT_BUDGET_FAILURE_STATES and attempt_id is not None:
        _update_run_after_attempt_in_transaction(
            connection, run_id, attempt_id, expected, target, summary_json, database.now_iso(),
        )
    else:
        if connection.execute("update local_agent_runs set status = ?, summary_json = ?, updated_at = ? where id = ? and status = ?", (target, summary_json, database.now_iso(), run_id, expected)).rowcount != 1:
            raise ValueError(_STATE_TRANSITION_INVALID)
        if target in {"failed_scope", "cancelled", "failed_review", "confirmation_expired"}:
            connection.execute("delete from local_agent_project_leases where run_id = ?", (run_id,))
    return _require_run(connection, run_id)


def _update_run_after_attempt_in_transaction(
    connection: database.sqlite3.Connection,
    run_id: int,
    attempt_id: int,
    expected: str,
    target: str,
    summary_json: str,
    now: str,
) -> None:
    attempt = _require_attempt(connection, attempt_id)
    count = int(connection.execute(
        "select count(*) from local_agent_attempts where run_id=?", (run_id,),
    ).fetchone()[0])
    if attempt["run_id"] != run_id or count > _ATTEMPT_BUDGET:
        raise ValueError(_STORAGE_INVALID)
    exhausted = target in _ATTEMPT_BUDGET_FAILURE_STATES and count == _ATTEMPT_BUDGET
    effective_target = "attempts_exhausted" if exhausted else target
    effective_summary = (
        _encode_safe_mapping({"reason": "attempt_budget_exhausted", "last_status": target})
        if exhausted else summary_json
    )
    if connection.execute(
        "update local_agent_runs set status=?, summary_json=?, updated_at=? where id=? and status=?",
        (effective_target, effective_summary, now, run_id, expected),
    ).rowcount != 1:
        raise ValueError(_STATE_TRANSITION_INVALID)
    if exhausted:
        _append_event_in_transaction(
            connection, run_id, attempt_id, "attempt_budget_exhausted", effective_summary,
        )
        connection.execute("delete from local_agent_project_leases where run_id=?", (run_id,))
    elif target in {"failed_scope", "cancelled"}:
        connection.execute("delete from local_agent_project_leases where run_id=?", (run_id,))


def _complete_local_apply_in_transaction(connection: database.sqlite3.Connection, run_id: int) -> dict[str, object]:
    if _require_run(connection, run_id)["status"] != "awaiting_human_confirmation":
        raise ValueError(_STATE_TRANSITION_INVALID)
    if connection.execute(
        "update local_agent_runs set status='locally_applied', summary_json=?, updated_at=? where id=? and status='awaiting_human_confirmation'",
        (_encode_safe_mapping({"applied": True}), database.now_iso(), run_id),
    ).rowcount != 1:
        raise ValueError(_STATE_TRANSITION_INVALID)
    connection.execute("delete from local_agent_project_leases where run_id=?", (run_id,))
    return _require_run(connection, run_id)


def _validate_snapshot(
    run: Mapping[str, object],
    attempts: list[dict[str, object]],
    events: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    confirmation: Any = None,
    apply_operation: Mapping[str, object] | None = None,
) -> None:
    if [attempt["attempt_no"] for attempt in attempts] != list(range(1, len(attempts) + 1)):
        raise ValueError(_STORAGE_INVALID)
    active = [attempt for attempt in attempts if attempt["status"] in _ACTIVE_ATTEMPT_STATUSES]
    status = run["status"]
    if len(active) > 1:
        raise ValueError(_STORAGE_INVALID)
    if status in {"created", "workspace_ready", "failed_workspace"}:
        if attempts:
            raise ValueError(_STORAGE_INVALID)
    elif status == "worker_running":
        if len(active) != 1:
            raise ValueError(_STORAGE_INVALID)
    else:
        if active or not attempts:
            raise ValueError(_STORAGE_INVALID)
        expected_last_attempt = {
            "verifying": "completed",
            "reviewing": "completed",
            "awaiting_human_confirmation": "completed",
            "locally_applied": "completed",
            "failed_verification": "completed",
            "changes_requested": "completed",
            "failed_review": "completed",
            "confirmation_expired": "completed",
            "failed_scope": "failed_scope",
            "failed_worker": "failed_worker",
            "cancelled": "cancelled",
            "interrupted": "interrupted",
        }.get(status)
        if status == "attempts_exhausted":
            if len(attempts) != _ATTEMPT_BUDGET or attempts[-1]["status"] not in {
                "interrupted", "failed_worker", "completed",
            }:
                raise ValueError(_STORAGE_INVALID)
            expected_last_attempt = attempts[-1]["status"]
        if expected_last_attempt is None or attempts[-1]["status"] != expected_last_attempt:
            raise ValueError(_STORAGE_INVALID)
    if status == "locally_applied":
        latest_attempt_id = attempts[-1]["id"]
        receipts = [item for item in artifacts if item["kind"] == "local_apply_receipt"]
        completions = [item for item in events if item["event_type"] == "local_apply_finished"]
        if (
            confirmation is None
            or confirmation["run_id"] != run["id"]
            or confirmation["attempt_id"] != latest_attempt_id
            or confirmation["status"] != "consumed"
            or confirmation["consumed_at"] is None
            or len(receipts) != 1
            or receipts[0]["attempt_id"] != latest_attempt_id
            or len(completions) != 1
            or completions[0]["attempt_id"] != latest_attempt_id
            or apply_operation is None
            or apply_operation["run_id"] != run["id"]
            or apply_operation["attempt_id"] != latest_attempt_id
            or apply_operation["status"] != "completed"
            or not apply_operation["journal_application_id"]
        ):
            raise ValueError(_STORAGE_INVALID)
    elif apply_operation is not None:
        if (
            status != "awaiting_human_confirmation"
            or confirmation is None
            or confirmation["status"] != "issued"
            or apply_operation["run_id"] != run["id"]
            or apply_operation["attempt_id"] != attempts[-1]["id"]
            or apply_operation["status"] not in {"applying", "recovery_required"}
        ):
            raise ValueError(_STORAGE_INVALID)
    ids = {attempt["id"] for attempt in attempts}
    if [event["sequence_no"] for event in events] != list(range(1, len(events) + 1)) or any(event["attempt_id"] is not None and event["attempt_id"] not in ids for event in events) or any(artifact["attempt_id"] is not None and artifact["attempt_id"] not in ids for artifact in artifacts):
        raise ValueError(_STORAGE_INVALID)


def _worker_liveness(attempt: Mapping[str, object]) -> bool:
    pid, expected = attempt["worker_pid"], attempt["worker_start_identity"]
    if not isinstance(pid, int) or not isinstance(expected, str) or _PROCESS_START_IDENTITY.fullmatch(expected) is None:
        raise ValueError(_STORAGE_INVALID)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        raise ValueError(_STORAGE_INVALID) from None
    try:
        return _read_process_start_identity(pid) == expected
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError(_STORAGE_INVALID) from None


def _read_process_start_identity(pid: int) -> str:
    if sys.platform != "darwin":
        raise RuntimeError("platform_start_identity_unavailable")
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib")
        info = _ProcBsdInfo()
        reader = library.proc_pidinfo
        reader.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        reader.restype = ctypes.c_int
        size = ctypes.sizeof(info)
        if reader(pid, 3, 0, ctypes.byref(info), size) != size or info.start_seconds <= 0 or info.start_microseconds > 999999:
            raise RuntimeError("process_start_identity_unavailable")
        return f"darwin-proc-bsdinfo-v1:{info.start_seconds}:{info.start_microseconds}"
    except (AttributeError, OSError, ctypes.ArgumentError):
        raise RuntimeError("process_start_identity_unavailable") from None
