"""Transactional local SQLite persistence for bounded repair-learning facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping

from app import database
from app.repair_learning import (
    LearningRule,
    LearningRuleState,
    RuleObservationOutcome,
    canonical_rule_bytes,
    rule_key,
    validate_rule_payload,
)
from app.sensitive_text import redact_sensitive_mapping, validate_audit_alias


_MATCHABLE_STATES = (
    LearningRuleState.ACTIVE_CURRENT_TASK.value,
    LearningRuleState.TRIAL.value,
    LearningRuleState.STABLE.value,
)
_TRANSITIONS = {
    LearningRuleState.DRAFT.value: {
        LearningRuleState.ACTIVE_CURRENT_TASK.value,
        LearningRuleState.RETIRED.value,
    },
    LearningRuleState.ACTIVE_CURRENT_TASK.value: {
        LearningRuleState.TRIAL.value,
        LearningRuleState.RETIRED.value,
    },
    LearningRuleState.TRIAL.value: {
        LearningRuleState.STABLE.value,
        LearningRuleState.RETIRED.value,
    },
    LearningRuleState.STABLE.value: {LearningRuleState.RETIRED.value},
}


class RepairLearningRepository:
    """Store replay-safe learning facts through one explicitly injected database."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory
        database.init_db(connection_factory=connection_factory)

    def record_retrospective(
        self,
        *,
        source_key: str,
        run_id: int,
        attempt_id: int,
        source_kind: str,
        root_cause_kind: str,
        safe_summary: Mapping[str, object],
        task_context: Mapping[str, object],
    ) -> dict[str, object]:
        values = (
            _alias(source_key),
            _positive_int(run_id),
            _positive_int(attempt_id),
            _alias(source_kind),
            _alias(root_cause_kind),
            _safe_json(safe_summary),
            _safe_json(task_context),
            database.now_iso(),
        )
        with self._connect() as connection:
            connection.execute("begin immediate")
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
                "select * from repair_retrospectives where source_key = ?",
                (values[0],),
            ).fetchone()
            if row is None or tuple(row[key] for key in (
                "source_key", "run_id", "attempt_id", "source_kind",
                "root_cause_kind", "safe_summary_json", "task_context_json",
            )) != values[:7]:
                raise ValueError("repair_learning_replay_conflict")
            return _retrospective_record(row)

    def upsert_rule(
        self,
        *,
        rule: LearningRule,
        origin_retrospective_id: int,
        active_run_id: int | None = None,
    ) -> dict[str, object]:
        if type(rule) is not LearningRule:
            raise ValueError("repair_learning_input_invalid")
        try:
            payload = rule.to_payload()
            rule_json = canonical_rule_bytes(payload).decode("utf-8")
            if rule.key != rule_key(payload):
                raise ValueError
        except Exception:
            raise ValueError("repair_learning_input_invalid") from None
        if rule.state not in {
            LearningRuleState.DRAFT,
            LearningRuleState.ACTIVE_CURRENT_TASK,
        }:
            raise ValueError("repair_learning_initial_state_invalid")
        storage_rule_key = _storage_rule_key(payload)
        retrospective_id = _positive_int(origin_retrospective_id)
        normalized_run_id = None if active_run_id is None else _positive_int(active_run_id)
        created_at = database.now_iso()
        values = (
            storage_rule_key,
            rule_json,
            rule.state.value,
            retrospective_id,
            normalized_run_id,
            created_at,
            created_at,
        )
        with self._connect() as connection:
            connection.execute("begin immediate")
            connection.execute(
                """
                insert into repair_learning_rules(
                    rule_key, rule_json, state, origin_retrospective_id,
                    active_run_id, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(rule_key) do nothing
                """,
                values,
            )
            row = connection.execute(
                "select * from repair_learning_rules where rule_key = ?",
                (storage_rule_key,),
            ).fetchone()
            if row is None or tuple(row[key] for key in (
                "rule_key", "origin_retrospective_id", "active_run_id",
            )) != (storage_rule_key, retrospective_id, normalized_run_id):
                raise ValueError("repair_learning_replay_conflict")
            return _rule_record(row)

    def list_matchable_rules(self, *, run_id: int) -> list[dict[str, object]]:
        normalized_run_id = _positive_int(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                select * from repair_learning_rules
                where state in (?, ?, ?)
                  and (state != 'active_current_task' or active_run_id = ?)
                  and (state != 'stable' or (
                    verified_task_count >= 3
                    and distinct_workspace_count >= 2
                    and counterexample_count = 0
                  ))
                order by id
                """,
                (*_MATCHABLE_STATES, normalized_run_id),
            ).fetchall()
        return [_rule_record(row) for row in rows]

    def list_human_correction_retrospectives(self) -> list[dict[str, object]]:
        """Read durable human corrections for cross-run no-repeat matching.

        These rows are the learning source of truth.  They are deliberately
        read separately from promoted rules: a human correction must affect
        the next compatible task immediately, without waiting for three later
        successful observations to promote a Flux-Lite candidate.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                select * from repair_retrospectives
                where source_kind = 'offline_import'
                order by id
                """
            ).fetchall()
        return [_retrospective_record(row) for row in rows]

    def record_observation(
        self,
        *,
        rule_id: int,
        run_id: int,
        attempt_id: int,
        task_key: str,
        workspace_fingerprint: str,
        outcome: RuleObservationOutcome | str,
        evidence: Mapping[str, object],
    ) -> dict[str, object]:
        normalized_rule_id = _positive_int(rule_id)
        normalized_run_id = _positive_int(run_id)
        normalized_attempt_id = _positive_int(attempt_id)
        normalized_task_key = _alias(task_key)
        normalized_workspace = _alias(workspace_fingerprint)
        try:
            normalized_outcome = RuleObservationOutcome(outcome).value
        except (TypeError, ValueError):
            raise ValueError("repair_learning_input_invalid") from None
        evidence_json = _safe_json(evidence)
        observed_at = database.now_iso()
        with self._connect() as connection:
            connection.execute("begin immediate")
            cursor = connection.execute(
                """
                insert into repair_learning_observations(
                    rule_id, run_id, attempt_id, task_key,
                    workspace_fingerprint, outcome, evidence_json, observed_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(rule_id, run_id, attempt_id, outcome) do nothing
                """,
                (
                    normalized_rule_id, normalized_run_id, normalized_attempt_id,
                    normalized_task_key, normalized_workspace, normalized_outcome,
                    evidence_json, observed_at,
                ),
            )
            inserted = cursor.rowcount == 1
            row = connection.execute(
                """
                select * from repair_learning_observations
                where rule_id = ? and run_id = ? and attempt_id = ? and outcome = ?
                """,
                (normalized_rule_id, normalized_run_id, normalized_attempt_id, normalized_outcome),
            ).fetchone()
            if row is None or tuple(row[key] for key in (
                "task_key", "workspace_fingerprint", "evidence_json",
            )) != (normalized_task_key, normalized_workspace, evidence_json):
                raise ValueError("repair_learning_replay_conflict")
            if inserted:
                _refresh_counts_and_suspend(connection, normalized_rule_id, observed_at)
            return _observation_record(row)

    def advance_rule_state(
        self,
        *,
        rule_id: int,
        expected_state_version: int,
        new_state: LearningRuleState | str,
    ) -> dict[str, object]:
        normalized_rule_id = _positive_int(rule_id)
        version = _nonnegative_int(expected_state_version)
        try:
            target = LearningRuleState(new_state).value
        except (TypeError, ValueError):
            raise ValueError("repair_learning_input_invalid") from None
        with self._connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select * from repair_learning_rules where id = ?",
                (normalized_rule_id,),
            ).fetchone()
            if row is None:
                raise KeyError(normalized_rule_id)
            if int(row["state_version"]) != version:
                raise ValueError("repair_learning_state_conflict")
            current = str(row["state"])
            if target not in _TRANSITIONS.get(current, set()):
                raise ValueError("repair_learning_state_invalid")
            if target == LearningRuleState.STABLE.value and (
                int(row["verified_task_count"]) < 3
                or int(row["distinct_workspace_count"]) < 2
                or int(row["counterexample_count"]) != 0
            ):
                raise ValueError("repair_learning_promotion_ineligible")
            now = database.now_iso()
            rule_json = _transition_rule_json(
                connection,
                row,
                target=target,
            )
            changed = connection.execute(
                """
                update repair_learning_rules
                set state = ?, rule_json = ?,
                    state_version = state_version + 1, updated_at = ?
                where id = ? and state_version = ? and state = ?
                """,
                (target, rule_json, now, normalized_rule_id, version, current),
            ).rowcount
            if changed != 1:
                raise ValueError("repair_learning_state_conflict")
            updated = connection.execute(
                "select * from repair_learning_rules where id = ?",
                (normalized_rule_id,),
            ).fetchone()
            return _rule_record(updated)

    def suspend_rule(
        self,
        *,
        rule_id: int,
        expected_state_version: int,
    ) -> dict[str, object]:
        normalized_rule_id = _positive_int(rule_id)
        version = _nonnegative_int(expected_state_version)
        now = database.now_iso()
        with self._connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select * from repair_learning_rules where id = ?",
                (normalized_rule_id,),
            ).fetchone()
            if row is None or int(row["state_version"]) != version:
                raise ValueError("repair_learning_state_conflict")
            rule_json = _transition_rule_json(
                connection,
                row,
                target=LearningRuleState.SUSPENDED.value,
            )
            changed = connection.execute(
                """
                update repair_learning_rules
                set state = 'suspended', rule_json = ?, state_version = state_version + 1,
                    updated_at = ?, suspended_at = ?
                where id = ? and state_version = ?
                  and state not in ('suspended', 'retired')
                """,
                (rule_json, now, now, normalized_rule_id, version),
            ).rowcount
            if changed != 1:
                raise ValueError("repair_learning_state_conflict")
            row = connection.execute(
                "select * from repair_learning_rules where id = ?",
                (normalized_rule_id,),
            ).fetchone()
            return _rule_record(row)

    def snapshot_for_run(self, *, run_id: int) -> dict[str, object]:
        normalized_run_id = _positive_int(run_id)
        with self._connect() as connection:
            rules = connection.execute(
                """
                select distinct rule.* from repair_learning_rules rule
                left join repair_learning_observations observation
                  on observation.rule_id = rule.id
                where rule.active_run_id = ? or observation.run_id = ?
                order by rule.id
                """,
                (normalized_run_id, normalized_run_id),
            ).fetchall()
            retrospectives = connection.execute(
                "select * from repair_retrospectives where run_id = ? order by id",
                (normalized_run_id,),
            ).fetchall()
            observations = connection.execute(
                "select * from repair_learning_observations where run_id = ? order by id",
                (normalized_run_id,),
            ).fetchall()
        return {
            "run_id": normalized_run_id,
            "rules": [_rule_record(row) for row in rules],
            "retrospectives": [_retrospective_record(row) for row in retrospectives],
            "observations": [_observation_record(row) for row in observations],
        }

    def _connect(self):
        return _OwnedConnection(self._connection_factory())


class _OwnedConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection_factory must return sqlite3.Connection")
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self._connection.__enter__()
        return self._connection

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(self._connection.__exit__(exc_type, exc_value, traceback))
        finally:
            self._connection.close()


def _refresh_counts_and_suspend(
    connection: sqlite3.Connection,
    rule_id: int,
    observed_at: str,
) -> None:
    counts = connection.execute(
        """
        select
          count(distinct case when outcome = 'matched' then task_key end),
          count(distinct case when outcome = 'matched' then workspace_fingerprint end),
          sum(case when outcome = 'not_matched' then 1 else 0 end)
        from repair_learning_observations where rule_id = ?
        """,
        (rule_id,),
    ).fetchone()
    counterexamples = int(counts[2] or 0)
    row = connection.execute(
        "select * from repair_learning_rules where id = ?",
        (rule_id,),
    ).fetchone()
    if row is None:
        raise KeyError(rule_id)
    rule_json = str(row["rule_json"])
    if counterexamples > 0 and str(row["state"]) not in {
        LearningRuleState.SUSPENDED.value,
        LearningRuleState.RETIRED.value,
    }:
        rule_json = _transition_rule_json(
            connection,
            row,
            target=LearningRuleState.SUSPENDED.value,
        )
    connection.execute(
        """
        update repair_learning_rules
        set verified_task_count = ?, distinct_workspace_count = ?,
            counterexample_count = ?, rule_json = ?,
            state = case when ? > 0 and state != 'retired' then 'suspended' else state end,
            state_version = state_version + case
                when ? > 0 and state not in ('suspended', 'retired') then 1 else 0 end,
            suspended_at = case
                when ? > 0 and state not in ('suspended', 'retired') then ? else suspended_at end,
            updated_at = ?
        where id = ?
        """,
        (
            int(counts[0] or 0), int(counts[1] or 0), counterexamples, rule_json,
            counterexamples, counterexamples, counterexamples, observed_at,
            observed_at, rule_id,
        ),
    )


def _retrospective_record(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "source_key": str(row["source_key"]),
        "run_id": int(row["run_id"]),
        "attempt_id": int(row["attempt_id"]),
        "source_kind": str(row["source_kind"]),
        "root_cause_kind": str(row["root_cause_kind"]),
        "safe_summary": _load_json(row["safe_summary_json"]),
        "task_context": _load_task_context_json(row["task_context_json"]),
        "created_at": str(row["created_at"]),
    }


def _rule_record(row: sqlite3.Row) -> dict[str, object]:
    rule_payload = _load_rule_json(row["rule_json"])
    if (
        str(rule_payload["state"]) != str(row["state"])
        or _storage_rule_key(rule_payload) != str(row["rule_key"])
    ):
        raise ValueError("repair_learning_storage_invalid")
    return {
        "id": int(row["id"]),
        "rule_key": str(row["rule_key"]),
        "rule": rule_payload,
        "state": str(row["state"]),
        "origin_retrospective_id": int(row["origin_retrospective_id"]),
        "active_run_id": None if row["active_run_id"] is None else int(row["active_run_id"]),
        "verified_task_count": int(row["verified_task_count"]),
        "distinct_workspace_count": int(row["distinct_workspace_count"]),
        "counterexample_count": int(row["counterexample_count"]),
        "state_version": int(row["state_version"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "suspended_at": None if row["suspended_at"] is None else str(row["suspended_at"]),
    }


def _observation_record(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "rule_id": int(row["rule_id"]),
        "run_id": int(row["run_id"]),
        "attempt_id": int(row["attempt_id"]),
        "task_key": str(row["task_key"]),
        "workspace_fingerprint": str(row["workspace_fingerprint"]),
        "outcome": str(row["outcome"]),
        "evidence": _load_json(row["evidence_json"]),
        "observed_at": str(row["observed_at"]),
    }


def _safe_json(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("repair_learning_input_invalid")
    try:
        safe = redact_sensitive_mapping(value)
        return json.dumps(
            safe,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError):
        raise ValueError("repair_learning_input_invalid") from None


def _load_json(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("repair_learning_storage_invalid") from None
    if not isinstance(parsed, dict) or _safe_json(parsed) != str(value):
        raise ValueError("repair_learning_storage_invalid")
    return parsed


_TASK_CONTEXT_KEYS = frozenset(
    {
        "run_id", "task_key", "repository_kind", "allowed_path_prefixes",
        "verification_command_fingerprints", "high_risk_tags", "failure_sources",
    }
)
_TASK_CONTEXT_REPOSITORIES = frozenset(("python", "node", "gradle", "unknown"))
_TASK_CONTEXT_TEXT = re.compile(r"[A-Za-z0-9._/-]{1,256}")


def _load_task_context_json(value: object) -> dict[str, object]:
    """Read the fixed learning context while tolerating pre-fix legacy rows."""

    if not isinstance(value, str):
        raise ValueError("repair_learning_storage_invalid")
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or set(parsed) != _TASK_CONTEXT_KEYS:
            # Older rows were encoded through the generic redactor.  Keep
            # those rows readable, but never treat their redacted keys as a
            # reusable cross-run correction.
            return _load_json(value)
        if not _valid_task_context(parsed):
            raise ValueError
        canonical = json.dumps(
            parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        if canonical != value:
            raise ValueError
        return parsed
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("repair_learning_storage_invalid") from None


def _valid_task_context(value: Mapping[str, object]) -> bool:
    if (
        not isinstance(value.get("run_id"), int)
        or isinstance(value.get("run_id"), bool)
        or int(value["run_id"]) <= 0
        or not isinstance(value.get("task_key"), str)
        or not isinstance(value.get("repository_kind"), str)
        or value["repository_kind"] not in _TASK_CONTEXT_REPOSITORIES
    ):
        return False
    if not _valid_context_strings(value.get("allowed_path_prefixes"), path=True):
        return False
    if not _valid_context_strings(value.get("verification_command_fingerprints"), digest=True):
        return False
    return _valid_context_strings(value.get("high_risk_tags"), allow_empty=True) and _valid_context_strings(
        value.get("failure_sources"), allow_empty=True,
    )


def _valid_context_strings(
    value: object,
    *,
    allow_empty: bool = False,
    path: bool = False,
    digest: bool = False,
) -> bool:
    if not isinstance(value, list) or (not value and not allow_empty):
        return False
    for item in value:
        if not isinstance(item, str) or not item or "\\" in item or ".." in item.split("/"):
            return False
        if path and (_TASK_CONTEXT_TEXT.fullmatch(item) is None or item.startswith("/")):
            return False
        if digest and re.fullmatch(r"[0-9a-f]{64}", item) is None:
            return False
    return True
    if not isinstance(parsed, dict) or _safe_json(parsed) != str(value):
        raise ValueError("repair_learning_storage_invalid")
    return parsed


def _load_rule_json(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise ValueError("repair_learning_storage_invalid")
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or canonical_rule_bytes(parsed).decode("utf-8") != value:
            raise ValueError
        normalized = validate_rule_payload(parsed)
        return json.loads(canonical_rule_bytes(normalized).decode("utf-8"))
    except (MemoryError, RecursionError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("repair_learning_storage_invalid") from None


def _storage_rule_key(payload: Mapping[str, object]) -> str:
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


def _transition_rule_json(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    target: str,
) -> str:
    payload = _load_rule_json(row["rule_json"])
    payload["state"] = target
    if target == LearningRuleState.STABLE.value:
        evidence_rows = connection.execute(
            """
            select task_key, workspace_fingerprint
            from repair_learning_observations
            where rule_id = ? and outcome = 'matched'
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


def _alias(value: object) -> str:
    try:
        return validate_audit_alias(value)
    except ValueError:
        raise ValueError("repair_learning_input_invalid") from None


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("repair_learning_input_invalid")
    return value


def _nonnegative_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("repair_learning_input_invalid")
    return value
