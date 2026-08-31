from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from typing import Any

from app import database
from app.sensitive_text import contains_sensitive_scalar_text, redact_sensitive_text
from app.task_intent_router import (
    TASK_INTENT_CORRECTION_REASON_CODES,
    TASK_INTENT_REASON_CODES,
    IntentDecision,
)


TASK_INTENT_REPOSITORY_SCHEMA_VERSION = "manager-task-intent-repository.v1"
_CONVERSATION_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_WORK_ITEM = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,31}-\d+")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{24,}")
_REDACTION_MARKER = re.compile(r"\[REDACTED_[A-Z0-9_:.-]+\]")
_REDACTED_FIELD_STUB = re.compile(
    r"(?:authorization|cookie|credential|dsn|password|secret|token|"
    r"api[_-]?key|client[_-]?secret|private[_-]?key|"
    r"personal[_-]?access[_-]?token|gitlab[_-]?pat|aliyun[_-]?devops[_-]?pat)"
    r"\s*[:=]\s*",
    re.IGNORECASE,
)
_MODES = frozenset(("question", "task"))
_CONFIDENCE = frozenset(("high", "conservative"))
_YUNXIAO_STATUSES = frozenset(
    ("linked", "unlinked", "not_applicable", "lookup_failed")
)
_PHASES = frozenset(("knowledge_retrieval", "requirement_intake"))
_ROUTES = frozenset(("knowledge", "requirement_workflow"))
_EVENT_TYPES = frozenset(("decision", "explicit_correction"))
_MESSAGE_MAX_BYTES = 131_072
_SUMMARY_MAX_CHARS = 512
_JSON_LOADS = json.loads


class TaskIntentRepository:
    """Persist sticky routing state and append-only, secret-safe decisions."""

    def __init__(self, *, initialize: bool = True) -> None:
        if not isinstance(initialize, bool):
            raise ValueError("task_intent_input_invalid")
        self.runtime_fallback_path = ""
        if initialize:
            try:
                database.init_db()
            except (OSError, sqlite3.OperationalError) as exc:
                # Routing is needed before a full workflow runner exists. If
                # the checkout's bundled data directory is read-only, keep
                # the original path untouched and place only Harness control
                # state in a private temporary directory.
                from app.runtime_preflight import choose_private_runtime_root

                fallback_root = choose_private_runtime_root(prefix="his_harness_intent_runtime_")
                database.DB_PATH = fallback_root / "harness.sqlite"
                self.runtime_fallback_path = str(database.DB_PATH)
                database.init_db()

    def get_session(self, conversation_key: str) -> dict[str, object] | None:
        alias = _conversation_alias(conversation_key)
        with database.connect() as connection:
            row = connection.execute(
                """
                select conversation_key, mode, reason_codes_json, confidence,
                       sticky, linked_work_item, yunxiao_status, current_phase,
                       next_route, last_event_id, created_at, updated_at
                from manager_task_intent_sessions where conversation_key = ?
                """,
                (alias,),
            ).fetchone()
            if row is None:
                return None
            session = _session_record(row)
            event = _load_event(connection, int(session["last_event_id"]))
            latest_event_id = connection.execute(
                """
                select max(id) from manager_task_intent_events
                where conversation_key = ?
                """,
                (alias,),
            ).fetchone()[0]
        return _validated_session_event(session, event, latest_event_id)

    def record_decision(
        self,
        *,
        conversation_key: str,
        message: str,
        decision: IntentDecision,
        explicit_override: str | None = None,
        mutation_requested: bool = False,
    ) -> dict[str, object]:
        alias = _conversation_alias(conversation_key)
        prepared = _prepared_decision(decision, conversation_key=alias)
        override = _optional_mode(explicit_override)
        if not isinstance(mutation_requested, bool) or (
            prepared["mode"] == "question" and mutation_requested
        ):
            raise ValueError("task_intent_input_invalid")
        correction_reasons = list(TASK_INTENT_CORRECTION_REASON_CODES)
        has_correction_reason = "explicit_override" in prepared["reason_codes"]
        is_correction = prepared["reason_codes"] == correction_reasons
        if (
            has_correction_reason != is_correction
            or is_correction != (override is not None)
            or (override is not None and override != prepared["mode"])
        ):
            raise ValueError("task_intent_input_invalid")
        message_summary, message_sha256 = _message_audit_fields(message)
        recorded_at = database.now_iso()
        reason_codes_json = json.dumps(
            prepared["reason_codes"],
            ensure_ascii=True,
            separators=(",", ":"),
        )

        with database.connect() as connection:
            connection.execute("begin immediate")
            existing = connection.execute(
                """
                select conversation_key, mode, reason_codes_json, confidence,
                       sticky, linked_work_item, yunxiao_status, current_phase,
                       next_route, last_event_id, created_at, updated_at
                from manager_task_intent_sessions where conversation_key = ?
                """,
                (alias,),
            ).fetchone()
            previous_mode: str | None = None
            if existing is not None:
                previous = _session_record(existing)
                previous_mode = str(previous["mode"])
                if (
                    previous_mode == "task"
                    and prepared["mode"] == "question"
                    and override != "question"
                ):
                    raise ValueError("task_intent_sticky_override_required")

            session_sticky = prepared["mode"] == "task"
            connection.execute(
                """
                insert into manager_task_intent_sessions(
                    conversation_key, mode, reason_codes_json, confidence,
                    sticky, linked_work_item, yunxiao_status, current_phase,
                    next_route, last_event_id, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                on conflict(conversation_key) do update set
                    mode = excluded.mode,
                    reason_codes_json = excluded.reason_codes_json,
                    confidence = excluded.confidence,
                    sticky = excluded.sticky,
                    linked_work_item = excluded.linked_work_item,
                    yunxiao_status = excluded.yunxiao_status,
                    current_phase = excluded.current_phase,
                    next_route = excluded.next_route,
                    updated_at = excluded.updated_at
                """,
                (
                    alias,
                    prepared["mode"],
                    reason_codes_json,
                    prepared["confidence"],
                    int(session_sticky),
                    prepared["linked_work_item"] or "",
                    prepared["yunxiao_status"],
                    prepared["current_phase"],
                    prepared["next_route"],
                    recorded_at,
                    recorded_at,
                ),
            )
            cursor = connection.execute(
                """
                insert into manager_task_intent_events(
                    conversation_key, event_type, previous_mode, mode,
                    reason_codes_json, confidence, sticky, linked_work_item,
                    yunxiao_status, current_phase, next_route,
                    mutation_requested, message_summary, message_sha256, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alias,
                    "explicit_correction" if is_correction else "decision",
                    previous_mode or "",
                    prepared["mode"],
                    reason_codes_json,
                    prepared["confidence"],
                    int(session_sticky),
                    prepared["linked_work_item"] or "",
                    prepared["yunxiao_status"],
                    prepared["current_phase"],
                    prepared["next_route"],
                    int(mutation_requested),
                    message_summary,
                    message_sha256,
                    recorded_at,
                ),
            )
            event_id = int(cursor.lastrowid)
            connection.execute(
                """
                update manager_task_intent_sessions
                set last_event_id = ? where conversation_key = ?
                """,
                (event_id, alias),
            )
            row = connection.execute(
                """
                select conversation_key, mode, reason_codes_json, confidence,
                       sticky, linked_work_item, yunxiao_status, current_phase,
                       next_route, last_event_id, created_at, updated_at
                from manager_task_intent_sessions where conversation_key = ?
                """,
                (alias,),
            ).fetchone()
            session = _session_record(row)
            event = _load_event(connection, event_id)
            latest_event_id = connection.execute(
                """
                select max(id) from manager_task_intent_events
                where conversation_key = ?
                """,
                (alias,),
            ).fetchone()[0]
            return _validated_session_event(session, event, latest_event_id)

    def get_event(self, event_id: int) -> dict[str, object] | None:
        """Load one validated append-only routing event by its real id."""

        try:
            validated_event_id = _positive_int(event_id)
        except (TypeError, ValueError):
            raise ValueError("task_intent_input_invalid") from None
        with database.connect() as connection:
            return _load_event(connection, validated_event_id)

    def verify_event(
        self,
        *,
        event_id: int,
        decision: IntentDecision,
        mutation_requested: bool,
    ) -> dict[str, object]:
        """Verify a receipt against the latest persisted event for its conversation."""

        try:
            if not isinstance(decision, IntentDecision):
                raise ValueError
            alias = _conversation_alias(decision.conversation_key)
            prepared = _prepared_decision(decision, conversation_key=alias)
            if not isinstance(mutation_requested, bool):
                raise ValueError
            event = self.get_event(event_id)
            session = self.get_session(alias)
            if (
                event is None
                or session is None
                or session["last_event_id"] != event_id
                or event["id"] != event_id
                or event["conversation_key"] != alias
                or event["mutation_requested"] != mutation_requested
            ):
                raise ValueError
            for field in (
                "mode",
                "reason_codes",
                "confidence",
                "linked_work_item",
                "yunxiao_status",
                "current_phase",
                "next_route",
            ):
                if event[field] != prepared[field]:
                    raise ValueError
            if event["sticky"] != (prepared["mode"] == "task"):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("task_intent_receipt_invalid") from None
        return event

    def list_recent_events(self, limit: int = 100) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("task_intent_input_invalid")
        with database.connect() as connection:
            rows = connection.execute(
                """
                select event.id, event.conversation_key, event.event_type,
                       event.previous_mode, event.mode, event.reason_codes_json,
                       event.confidence, event.sticky, event.linked_work_item,
                       event.yunxiao_status, event.current_phase,
                       event.next_route, event.mutation_requested,
                       event.message_summary,
                       event.message_sha256, event.created_at,
                       (
                           select previous.mode
                           from manager_task_intent_events as previous
                           where previous.conversation_key = event.conversation_key
                             and previous.id < event.id
                           order by previous.id desc limit 1
                       ) as expected_previous_mode
                from manager_task_intent_events as event
                order by event.id desc limit ?
                """,
                (limit,),
            ).fetchall()
        return [_event_record(row) for row in rows]


def _prepared_decision(
    decision: IntentDecision, *, conversation_key: str
) -> dict[str, object]:
    if not isinstance(decision, IntentDecision):
        raise ValueError("task_intent_input_invalid")
    try:
        decision_alias = decision.conversation_key
        if decision_alias is not None and _conversation_alias(decision_alias) != conversation_key:
            raise ValueError
        mode = _stored_choice(decision.mode, _MODES)
        reason_codes = _reason_codes(decision.reason_codes)
        confidence = _stored_choice(decision.confidence, _CONFIDENCE)
        if not isinstance(decision.sticky, bool):
            raise ValueError
        linked_work_item = _optional_work_item(decision.linked_work_item)
        yunxiao_status = _stored_choice(decision.yunxiao_status, _YUNXIAO_STATUSES)
        current_phase = _stored_choice(decision.current_phase, _PHASES)
        next_route = _stored_choice(decision.next_route, _ROUTES)
        if mode == "question" and (
            yunxiao_status != "not_applicable"
            or current_phase != "knowledge_retrieval"
            or next_route != "knowledge"
        ):
            raise ValueError
        if mode == "task" and (
            yunxiao_status not in {"linked", "unlinked", "lookup_failed"}
            or current_phase != "requirement_intake"
            or next_route != "requirement_workflow"
        ):
            raise ValueError
        _validate_linked_work_item(linked_work_item, yunxiao_status)
    except (TypeError, ValueError):
        raise ValueError("task_intent_input_invalid") from None
    return {
        "mode": mode,
        "reason_codes": reason_codes,
        "confidence": confidence,
        "sticky": decision.sticky,
        "linked_work_item": linked_work_item,
        "yunxiao_status": yunxiao_status,
        "current_phase": current_phase,
        "next_route": next_route,
    }


def _session_record(row: Any) -> dict[str, object]:
    try:
        conversation_key = _conversation_alias(row["conversation_key"])
        mode = _stored_choice(row["mode"], _MODES)
        reason_codes = _stored_reason_codes(row["reason_codes_json"])
        confidence = _stored_choice(row["confidence"], _CONFIDENCE)
        sticky = _stored_boolean(row["sticky"])
        linked_work_item = _stored_optional_work_item(row["linked_work_item"])
        yunxiao_status = _stored_choice(row["yunxiao_status"], _YUNXIAO_STATUSES)
        current_phase = _stored_choice(row["current_phase"], _PHASES)
        next_route = _stored_choice(row["next_route"], _ROUTES)
        last_event_id = _positive_int(row["last_event_id"])
        created_at = _stored_text(row["created_at"], maximum=128)
        updated_at = _stored_text(row["updated_at"], maximum=128)
        if sticky != (mode == "task"):
            raise ValueError
        _validate_route_tuple(mode, yunxiao_status, current_phase, next_route)
        _validate_linked_work_item(linked_work_item, yunxiao_status)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        raise ValueError("task_intent_storage_invalid") from None
    return {
        "schema_version": TASK_INTENT_REPOSITORY_SCHEMA_VERSION,
        "conversation_key": conversation_key,
        "mode": mode,
        "reason_codes": reason_codes,
        "confidence": confidence,
        "sticky": sticky,
        "linked_work_item": linked_work_item,
        "yunxiao_status": yunxiao_status,
        "current_phase": current_phase,
        "next_route": next_route,
        "last_event_id": last_event_id,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _event_record(row: Any) -> dict[str, object]:
    try:
        event_id = _positive_int(row["id"])
        conversation_key = _conversation_alias(row["conversation_key"])
        event_type = _stored_choice(row["event_type"], _EVENT_TYPES)
        previous_mode = _stored_optional_mode(row["previous_mode"])
        mode = _stored_choice(row["mode"], _MODES)
        reason_codes = _stored_reason_codes(row["reason_codes_json"])
        confidence = _stored_choice(row["confidence"], _CONFIDENCE)
        sticky = _stored_boolean(row["sticky"])
        linked_work_item = _stored_optional_work_item(row["linked_work_item"])
        yunxiao_status = _stored_choice(row["yunxiao_status"], _YUNXIAO_STATUSES)
        current_phase = _stored_choice(row["current_phase"], _PHASES)
        next_route = _stored_choice(row["next_route"], _ROUTES)
        mutation_requested = _stored_boolean(row["mutation_requested"])
        message_summary = _stored_summary(row["message_summary"])
        message_sha256 = _stored_hash(row["message_sha256"])
        created_at = _stored_text(row["created_at"], maximum=128)
        _validate_route_tuple(mode, yunxiao_status, current_phase, next_route)
        _validate_linked_work_item(linked_work_item, yunxiao_status)
        if sticky != (mode == "task"):
            raise ValueError
        if mode == "question" and mutation_requested:
            raise ValueError
        is_correction = reason_codes == list(TASK_INTENT_CORRECTION_REASON_CODES)
        if (event_type == "explicit_correction") != is_correction:
            raise ValueError
        if "explicit_override" in reason_codes and not is_correction:
            raise ValueError
        expected_previous_mode = row["expected_previous_mode"]
        if expected_previous_mode is not None:
            expected_previous_mode = _stored_choice(expected_previous_mode, _MODES)
        if previous_mode != expected_previous_mode:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        raise ValueError("task_intent_storage_invalid") from None
    return {
        "schema_version": TASK_INTENT_REPOSITORY_SCHEMA_VERSION,
        "id": event_id,
        "conversation_key": conversation_key,
        "event_type": event_type,
        "previous_mode": previous_mode,
        "mode": mode,
        "reason_codes": reason_codes,
        "confidence": confidence,
        "sticky": sticky,
        "linked_work_item": linked_work_item,
        "yunxiao_status": yunxiao_status,
        "current_phase": current_phase,
        "next_route": next_route,
        "mutation_requested": mutation_requested,
        "message_summary": message_summary,
        "message_sha256": message_sha256,
        "created_at": created_at,
    }


def _message_audit_fields(message: object) -> tuple[str, str]:
    if not isinstance(message, str):
        raise ValueError("task_intent_input_invalid")
    try:
        encoded = message.encode("utf-8", "surrogatepass")
    except UnicodeError:
        raise ValueError("task_intent_input_invalid") from None
    if len(encoded) > _MESSAGE_MAX_BYTES:
        raise ValueError("task_intent_input_invalid")
    summary = redact_sensitive_text(message)
    summary = _OPAQUE_TOKEN.sub("[REDACTED_OPAQUE_VALUE]", summary)
    summary = " ".join(summary.split())[:_SUMMARY_MAX_CHARS]
    return summary, "sha256:" + hashlib.sha256(encoded).hexdigest()


def _conversation_alias(value: object) -> str:
    if not isinstance(value, str) or _CONVERSATION_KEY.fullmatch(value) is None:
        raise ValueError("task_intent_input_invalid")
    if (
        _OPAQUE_TOKEN.search(value) is not None
        or contains_sensitive_scalar_text(value)
        or redact_sensitive_text(value) != value
    ):
        raise ValueError("task_intent_input_invalid")
    return value


def _reason_codes(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError
    values = list(value)
    if not 1 <= len(values) <= 16 or len(set(values)) != len(values):
        raise ValueError
    if any(
        not isinstance(item, str)
        or _REASON_CODE.fullmatch(item) is None
        or item not in TASK_INTENT_REASON_CODES
        for item in values
    ):
        raise ValueError
    return values


def _stored_reason_codes(value: object) -> list[str]:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError
    return _reason_codes(_JSON_LOADS(value))


def _optional_work_item(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _WORK_ITEM.fullmatch(value) is None:
        raise ValueError
    return value


def _stored_optional_work_item(value: object) -> str | None:
    if value == "":
        return None
    return _optional_work_item(value)


def _optional_mode(value: object) -> str | None:
    if value is None:
        return None
    if value not in _MODES:
        raise ValueError("task_intent_input_invalid")
    return str(value)


def _stored_optional_mode(value: object) -> str | None:
    if value == "":
        return None
    return _stored_choice(value, _MODES)


def _stored_choice(value: object, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError
    return value


def _stored_boolean(value: object) -> bool:
    if not isinstance(value, int) or isinstance(value, bool) or value not in (0, 1):
        raise ValueError
    return bool(value)


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError
    return value


def _stored_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError
    if redact_sensitive_text(value) != value:
        raise ValueError
    return value


def _stored_summary(value: object) -> str:
    if not isinstance(value, str) or len(value) > _SUMMARY_MAX_CHARS:
        raise ValueError
    without_markers = _REDACTION_MARKER.sub("", value)
    residue = _REDACTED_FIELD_STUB.sub("", without_markers)
    if _OPAQUE_TOKEN.search(residue) is not None:
        raise ValueError
    if redact_sensitive_text(residue) != residue:
        raise ValueError
    return value


def _stored_hash(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


def _validate_route_tuple(
    mode: str, yunxiao_status: str, current_phase: str, next_route: str
) -> None:
    if mode == "question":
        if (
            yunxiao_status != "not_applicable"
            or current_phase != "knowledge_retrieval"
            or next_route != "knowledge"
        ):
            raise ValueError
        return
    if (
        yunxiao_status not in {"linked", "unlinked", "lookup_failed"}
        or current_phase != "requirement_intake"
        or next_route != "requirement_workflow"
    ):
        raise ValueError


def _validate_linked_work_item(
    linked_work_item: str | None, yunxiao_status: str
) -> None:
    if yunxiao_status == "linked" and linked_work_item is None:
        raise ValueError
    if yunxiao_status in {"unlinked", "not_applicable"} and linked_work_item is not None:
        raise ValueError


def _load_event(connection: Any, event_id: int) -> dict[str, object] | None:
    row = connection.execute(
        """
        select event.id, event.conversation_key, event.event_type,
               event.previous_mode, event.mode, event.reason_codes_json,
               event.confidence, event.sticky, event.linked_work_item,
               event.yunxiao_status, event.current_phase, event.next_route,
               event.mutation_requested, event.message_summary,
               event.message_sha256, event.created_at,
               (
                   select previous.mode
                   from manager_task_intent_events as previous
                   where previous.conversation_key = event.conversation_key
                     and previous.id < event.id
                   order by previous.id desc limit 1
               ) as expected_previous_mode
        from manager_task_intent_events as event where event.id = ?
        """,
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    return _event_record(row)


def _validated_session_event(
    session: dict[str, object],
    event: dict[str, object] | None,
    latest_event_id: object,
) -> dict[str, object]:
    try:
        if event is None or _positive_int(latest_event_id) != session["last_event_id"]:
            raise ValueError
        for field in (
            "conversation_key",
            "mode",
            "reason_codes",
            "confidence",
            "sticky",
            "linked_work_item",
            "yunxiao_status",
            "current_phase",
            "next_route",
        ):
            if session[field] != event[field]:
                raise ValueError
        if session["last_event_id"] != event["id"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("task_intent_storage_invalid") from None
    return session
