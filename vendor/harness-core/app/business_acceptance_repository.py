from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from app import database
from app.business_acceptance import build_business_acceptance_status
from app.sensitive_text import (
    contains_sensitive_scalar_text,
    contains_sensitive_text,
    redact_sensitive_mapping,
    redact_sensitive_text,
)


BUSINESS_ACCEPTANCE_REPOSITORY_SCHEMA_VERSION = (
    "his-business-acceptance-repository.v1"
)
_ALIAS = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_TECHNICAL_RESULTS = frozenset(("passed", "failed", "not_verified"))
_DECISIONS = frozenset(("accept", "reject"))
_SCENARIO_STATES = frozenset(("passed", "failed", "needs_evidence"))


class BusinessAcceptanceRepository:
    """Versioned HIS acceptance evidence and append-only reviewer decisions."""

    def __init__(self) -> None:
        database.init_db()

    def create_evidence(
        self,
        evidence: Mapping[str, object],
        *,
        scope_type: str = "local",
        scope_key: str = "default",
    ) -> dict[str, object]:
        prepared = _prepare_evidence(evidence)
        safe_scope_type = _alias(scope_type)
        safe_scope_key = _alias(scope_key)
        created_at = database.now_iso()
        payload = {
            "runtime_verified": prepared["runtime_verified"],
            "scenarios": prepared["scenarios"],
        }
        evidence_json = _safe_json(payload)
        evidence_hash = "sha256:" + hashlib.sha256(
            evidence_json.encode("utf-8")
        ).hexdigest()
        with database.connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                """
                select coalesce(max(evidence_version), 0) as version
                from manager_business_acceptance_evidence
                where evidence_key = ?
                """,
                (prepared["evidence_key"],),
            ).fetchone()
            version = int(row["version"]) + 1
            cursor = connection.execute(
                """
                insert into manager_business_acceptance_evidence(
                    evidence_key, evidence_version, scope_type, scope_key,
                    environment_alias, operator_alias, test_data_alias,
                    technical_result, evidence_hash, evidence_json,
                    business_valid, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    prepared["evidence_key"],
                    version,
                    safe_scope_type,
                    safe_scope_key,
                    prepared["environment_alias"],
                    prepared["operator_alias"],
                    prepared["test_data_alias"],
                    prepared["technical_result"],
                    evidence_hash,
                    evidence_json,
                    created_at,
                ),
            )
            evidence_id = int(cursor.lastrowid)
        return self.get_evidence(evidence_id)

    def append_reviewer_decision(
        self,
        *,
        evidence_id: int,
        reviewer_alias: str,
        decision: str,
        reason: str,
    ) -> dict[str, object]:
        normalized_id = _positive_int(evidence_id)
        reviewer = _alias(reviewer_alias)
        if decision not in _DECISIONS:
            raise ValueError("business_acceptance_input_invalid")
        safe_reason = _safe_text(reason, maximum=512)
        created_at = database.now_iso()
        with database.connect() as connection:
            connection.execute("begin immediate")
            exists = connection.execute(
                "select id from manager_business_acceptance_evidence where id = ?",
                (normalized_id,),
            ).fetchone()
            if exists is None:
                raise KeyError("business_acceptance_evidence_not_found")
            cursor = connection.execute(
                """
                insert into manager_business_acceptance_decisions(
                    evidence_id, reviewer_alias, decision, reason_redacted, created_at
                ) values (?, ?, ?, ?, ?)
                """,
                (normalized_id, reviewer, decision, safe_reason, created_at),
            )
            decision_id = int(cursor.lastrowid)
        return {
            "id": decision_id,
            "evidence_id": normalized_id,
            "reviewer_alias": reviewer,
            "decision": decision,
            "reason": safe_reason,
            "created_at": created_at,
        }

    def get_evidence(self, evidence_id: int) -> dict[str, object]:
        normalized_id = _positive_int(evidence_id)
        with database.connect() as connection:
            row = connection.execute(
                "select * from manager_business_acceptance_evidence where id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise KeyError("business_acceptance_evidence_not_found")
            decisions = connection.execute(
                """
                select id, evidence_id, reviewer_alias, decision,
                       reason_redacted, created_at
                from manager_business_acceptance_decisions
                where evidence_id = ? order by id
                """,
                (normalized_id,),
            ).fetchall()
        return _record(row, decisions)

    def list_evidence(self, *, limit: int = 100) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("business_acceptance_input_invalid")
        with database.connect() as connection:
            rows = connection.execute(
                """
                select * from manager_business_acceptance_evidence
                order by id desc limit ?
                """,
                (limit,),
            ).fetchall()
            decision_rows = connection.execute(
                """
                select id, evidence_id, reviewer_alias, decision,
                       reason_redacted, created_at
                from manager_business_acceptance_decisions order by id
                """
            ).fetchall()
        by_evidence: dict[int, list[Any]] = {}
        for decision in decision_rows:
            by_evidence.setdefault(int(decision["evidence_id"]), []).append(decision)
        return [_record(row, by_evidence.get(int(row["id"]), [])) for row in rows]

    def current_business_valid(self) -> bool:
        """Aggregate the current evidence independently for every scoped key."""

        with database.connect() as connection:
            rows = connection.execute(
                """
                select evidence.*
                from manager_business_acceptance_evidence evidence
                join (
                    select scope_type, scope_key, evidence_key,
                           max(evidence_version) as evidence_version
                    from manager_business_acceptance_evidence
                    group by scope_type, scope_key, evidence_key
                ) current
                  on current.scope_type = evidence.scope_type
                 and current.scope_key = evidence.scope_key
                 and current.evidence_key = evidence.evidence_key
                 and current.evidence_version = evidence.evidence_version
                order by evidence.scope_type, evidence.scope_key, evidence.evidence_key
                """
            ).fetchall()
            decision_rows = connection.execute(
                """
                select decision.id, decision.evidence_id, decision.reviewer_alias,
                       decision.decision, decision.reason_redacted, decision.created_at
                from manager_business_acceptance_decisions decision
                join manager_business_acceptance_evidence evidence
                  on evidence.id = decision.evidence_id
                join (
                    select scope_type, scope_key, evidence_key,
                           max(evidence_version) as evidence_version
                    from manager_business_acceptance_evidence
                    group by scope_type, scope_key, evidence_key
                ) current
                  on current.scope_type = evidence.scope_type
                 and current.scope_key = evidence.scope_key
                 and current.evidence_key = evidence.evidence_key
                 and current.evidence_version = evidence.evidence_version
                order by decision.id
                """
            ).fetchall()
        if not rows:
            return False
        by_evidence: dict[int, list[Any]] = {}
        for decision in decision_rows:
            by_evidence.setdefault(int(decision["evidence_id"]), []).append(decision)
        try:
            return all(
                bool(_record(row, by_evidence.get(int(row["id"]), []))["business_valid"])
                for row in rows
            )
        except (TypeError, ValueError):
            return False


def _record(row: Any, decisions: list[Any]) -> dict[str, object]:
    try:
        encoded_payload = str(row["evidence_json"])
        if (
            len(encoded_payload.encode("utf-8")) > 65_536
            or contains_sensitive_text(encoded_payload)
        ):
            raise ValueError
        payload = json.loads(encoded_payload)
        if not isinstance(payload, dict) or redact_sensitive_mapping(payload) != payload:
            raise ValueError
        decision_values = [
            {
                "id": int(item["id"]),
                "evidence_id": int(item["evidence_id"]),
                "reviewer_alias": _alias(str(item["reviewer_alias"])),
                "decision": _stored_decision(item["decision"]),
                "reason": _safe_text(item["reason_redacted"], maximum=512),
                "created_at": _safe_stored_text(item["created_at"], maximum=128),
            }
            for item in decisions
        ]
        latest = decision_values[-1]["decision"] if decision_values else ""
        evidence_key = _alias(str(row["evidence_key"]))
        scope_type = _alias(str(row["scope_type"]))
        scope_key = _alias(str(row["scope_key"]))
        environment_alias = _alias(str(row["environment_alias"]))
        operator_alias = _alias(str(row["operator_alias"]))
        test_data_alias = _alias(str(row["test_data_alias"]))
        technical_result = str(row["technical_result"])
        if technical_result not in _TECHNICAL_RESULTS:
            raise ValueError
        created_at = _safe_stored_text(row["created_at"], maximum=128)
        acceptance = build_business_acceptance_status(
            {
                "environment": environment_alias,
                "operator": operator_alias,
                "account_alias": operator_alias,
                "test_data_alias": test_data_alias,
                "accepted": latest == "accept",
                "runtime_verified": bool(payload.get("runtime_verified")),
                "scenarios": payload.get("scenarios", []),
            }
        )
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("business_acceptance_storage_invalid") from None
    business_valid = bool(
        acceptance["business_valid"] and technical_result == "passed"
    )
    return {
        "schema_version": BUSINESS_ACCEPTANCE_REPOSITORY_SCHEMA_VERSION,
        "id": int(row["id"]),
        "evidence_key": evidence_key,
        "evidence_version": int(row["evidence_version"]),
        "scope_type": scope_type,
        "scope_key": scope_key,
        "environment_alias": environment_alias,
        "operator_alias": operator_alias,
        "test_data_alias": test_data_alias,
        "technical_result": technical_result,
        "runtime_verified": bool(payload.get("runtime_verified")),
        "scenarios": list(payload.get("scenarios", [])),
        "reviewer_decisions": decision_values,
        "business_valid": business_valid,
        "status": "accepted" if business_valid else "evidence_recorded",
        "created_at": created_at,
    }


def _stored_decision(value: object) -> str:
    if value not in _DECISIONS:
        raise ValueError("business_acceptance_storage_invalid")
    return str(value)


def _safe_stored_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("business_acceptance_storage_invalid")
    if contains_sensitive_scalar_text(value) or redact_sensitive_text(value) != value:
        raise ValueError("business_acceptance_storage_invalid")
    return value


def _prepare_evidence(evidence: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(evidence, Mapping):
        raise ValueError("business_acceptance_input_invalid")
    technical_result = evidence.get("technical_result")
    if technical_result not in _TECHNICAL_RESULTS:
        raise ValueError("business_acceptance_input_invalid")
    runtime_verified = evidence.get("runtime_verified")
    if not isinstance(runtime_verified, bool):
        raise ValueError("business_acceptance_input_invalid")
    scenarios = evidence.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("business_acceptance_input_invalid")
    normalized_scenarios: list[dict[str, str]] = []
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ValueError("business_acceptance_input_invalid")
        status = scenario.get("status")
        if status not in _SCENARIO_STATES:
            raise ValueError("business_acceptance_input_invalid")
        normalized_scenarios.append(
            {
                "name": _safe_text(scenario.get("name"), maximum=160),
                "status": str(status),
                "expected": _safe_text(scenario.get("expected"), maximum=512),
                "actual": _safe_text(scenario.get("actual"), maximum=512),
                "evidence": _safe_text(scenario.get("evidence"), maximum=512),
                "evidence_hashes": [],
            }
        )
    return {
        "evidence_key": _alias(evidence.get("evidence_key")),
        "environment_alias": _alias(evidence.get("environment_alias")),
        "operator_alias": _alias(evidence.get("operator_alias")),
        "test_data_alias": _alias(evidence.get("test_data_alias")),
        "technical_result": technical_result,
        "runtime_verified": runtime_verified,
        "scenarios": normalized_scenarios,
    }


def _alias(value: object) -> str:
    if not isinstance(value, str) or not _ALIAS.fullmatch(value):
        raise ValueError("business_acceptance_input_invalid")
    if contains_sensitive_text(value):
        raise ValueError("business_acceptance_input_invalid")
    return value


def _safe_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("business_acceptance_input_invalid")
    stripped = value.strip()
    if contains_sensitive_scalar_text(stripped):
        raise ValueError("business_acceptance_input_invalid")
    normalized = redact_sensitive_text(stripped)
    if normalized != stripped:
        raise ValueError("business_acceptance_input_invalid")
    return normalized


def _safe_json(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > 65_536 or contains_sensitive_text(encoded):
        raise ValueError("business_acceptance_input_invalid")
    return encoded


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("business_acceptance_input_invalid")
    return value
