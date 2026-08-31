#!/usr/bin/env python3
"""Static database change planning; database mutation stays disabled."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


DISABLED_MESSAGE = "真实数据库变更能力未启用；本次仅生成变更计划，未连接数据库。"
REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "capability",
        "provider",
        "mode",
        "mutation_level",
        "authorization",
        "input",
        "context",
    }
)
PLAN_INPUT_FIELDS = frozenset(
    {
        "environment",
        "objective",
        "statements",
        "migration_description",
        "transaction_strategy",
        "rollback_strategy",
        "backup_confirmed",
        "validation_queries",
    }
)
ALLOWED_PLAN_ENVIRONMENTS = frozenset({"test", "development"})
CHANGE_TYPES = frozenset(
    {"ALTER", "CREATE", "DELETE", "DROP", "INSERT", "MERGE", "TRUNCATE", "UPDATE"}
)
DML_TYPES = frozenset({"DELETE", "INSERT", "MERGE", "UPDATE"})
STATEMENT_TYPE_PATTERN = re.compile(
    r"^\s*(?:/\*.*?\*/\s*)*(?:--[^\n]*\n\s*)*([A-Za-z]+)",
    re.DOTALL,
)
IDENTIFIER = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)(?:\.(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*))?'
OBJECT_PATTERNS = (
    re.compile(rf"^\s*(?:ALTER|CREATE|DROP|TRUNCATE)\s+TABLE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?({IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^\s*INSERT\s+INTO\s+({IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^\s*UPDATE\s+({IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^\s*DELETE\s+FROM\s+({IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^\s*MERGE\s+INTO\s+({IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+\S+\s+ON\s+({IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^\s*DROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?({IDENTIFIER})", re.IGNORECASE),
)
FORBIDDEN_VALIDATION_PATTERN = re.compile(
    r"\b(insert|update|delete|merge|create|alter|drop|truncate|grant|revoke|copy|call|do)\b",
    re.IGNORECASE,
)
MAX_REQUEST_BYTES = 1024 * 1024


@dataclass(frozen=True)
class DatabaseChangePlan:
    schema_version: str
    status: str
    provider: str
    environment: str
    objective: str
    affected_objects: tuple[str, ...]
    proposed_statements: tuple[str, ...]
    data_migration_required: bool
    transaction_strategy: str
    rollback_strategy: str
    backup_required: bool
    validation_queries: tuple[str, ...]
    application_approval_required: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "provider": self.provider,
            "environment": self.environment,
            "objective": self.objective,
            "affected_objects": list(self.affected_objects),
            "proposed_statements": list(self.proposed_statements),
            "data_migration_required": self.data_migration_required,
            "transaction_strategy": self.transaction_strategy,
            "rollback_strategy": self.rollback_strategy,
            "backup_required": self.backup_required,
            "validation_queries": list(self.validation_queries),
            "application_approval_required": self.application_approval_required,
            "blockers": list(self.blockers),
        }


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} items must be strings")
        if item.strip():
            values.append(item.strip())
    return tuple(values)


def _validate_plan_input(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != PLAN_INPUT_FIELDS:
        raise ValueError("invalid database change plan input")
    text_fields = (
        "environment",
        "objective",
        "migration_description",
        "transaction_strategy",
        "rollback_strategy",
    )
    if not all(isinstance(payload.get(field), str) for field in text_fields):
        raise ValueError("invalid database change plan input")
    if not payload["environment"].strip() or not payload["objective"].strip():
        raise ValueError("invalid database change plan input")
    if not isinstance(payload.get("backup_confirmed"), bool):
        raise ValueError("invalid database change plan input")
    _string_list(payload.get("statements"), "statements")
    _string_list(payload.get("validation_queries"), "validation_queries")
    return payload


def _statement_type(statement: str) -> str:
    match = STATEMENT_TYPE_PATTERN.search(statement)
    return match.group(1).upper() if match else ""


def _affected_object(statement: str) -> str:
    for pattern in OBJECT_PATTERNS:
        match = pattern.search(statement)
        if match:
            return match.group(1).replace('"', "").lower()
    return ""


def _readonly_validation(query: str) -> bool:
    normalized = re.sub(r"/\*.*?\*/|--[^\n]*", " ", query, flags=re.DOTALL).strip()
    return (
        normalized.lower().startswith(("select", "with"))
        and not FORBIDDEN_VALIDATION_PATTERN.search(normalized)
    )


def build_database_change_plan(input_payload: object) -> DatabaseChangePlan:
    checked = _validate_plan_input(input_payload)
    environment = checked["environment"].strip().lower()
    objective = checked["objective"].strip()
    statements = _string_list(checked["statements"], "statements")
    migration_description = checked["migration_description"].strip()
    transaction_strategy = checked["transaction_strategy"].strip()
    rollback_strategy = checked["rollback_strategy"].strip()
    validation_queries = _string_list(checked["validation_queries"], "validation_queries")
    statement_types = tuple(_statement_type(statement) for statement in statements)
    blockers: list[str] = []

    if not statements:
        blockers.append("必须提供待评审的数据库变更 SQL。")
    unsupported = [kind or "UNKNOWN" for kind in statement_types if kind not in CHANGE_TYPES]
    if unsupported:
        blockers.append("变更计划只接受 DDL/DML SQL；存在无法识别或非变更语句。")
    if environment not in ALLOWED_PLAN_ENVIRONMENTS:
        if environment in {"prod", "production"}:
            blockers.append("生产环境数据库变更不在首版能力范围，必须独立审批。")
        else:
            blockers.append("environment 只允许 test 或 development。")

    risk_text = " ".join((objective, migration_description, *statements)).lower()
    if "医保" in risk_text or "medical insurance" in risk_text:
        blockers.append("变更涉及医保数据或流程，必须完成医保相邻路径专项评审。")
    if "收费" in risk_text or "billing" in risk_text or "charge" in risk_text:
        blockers.append("变更涉及收费数据或流程，必须完成金额和结算专项评审。")
    if not transaction_strategy:
        blockers.append("必须提供事务策略。")
    if not rollback_strategy:
        blockers.append("必须提供回滚策略。")
    if checked["backup_confirmed"] is not True:
        blockers.append("数据库变更前必须确认可恢复备份。")
    if not validation_queries:
        blockers.append("必须提供至少一条验证查询。")
    elif not all(_readonly_validation(query) for query in validation_queries):
        blockers.append("验证查询必须为只读 SELECT/WITH。")

    affected_objects = tuple(
        dict.fromkeys(
            item
            for item in (_affected_object(statement) for statement in statements)
            if item
        )
    )
    data_migration_required = (
        any(kind in DML_TYPES for kind in statement_types)
        or bool(migration_description)
    )
    return DatabaseChangePlan(
        schema_version="database-change-plan.v1",
        status="blocked" if blockers else "ready_for_review",
        provider="postgresql",
        environment=environment,
        objective=objective,
        affected_objects=affected_objects,
        proposed_statements=statements,
        data_migration_required=data_migration_required,
        transaction_strategy=transaction_strategy,
        rollback_strategy=rollback_strategy,
        backup_required=True,
        validation_queries=validation_queries,
        application_approval_required=True,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _validate_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise ValueError("invalid database capability request")
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("provider") != "postgresql"
        or payload.get("capability") not in {"database.change-plan", "database.change"}
        or payload.get("context") != {}
    ):
        raise ValueError("invalid database capability request")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("invalid database capability request")
    authorization = payload.get("authorization")
    if (
        not isinstance(authorization, dict)
        or set(authorization) != {"explicit", "scope"}
        or not isinstance(authorization.get("explicit"), bool)
        or not isinstance(authorization.get("scope"), list)
        or not all(isinstance(item, str) and item for item in authorization["scope"])
    ):
        raise ValueError("invalid database capability request")
    if not isinstance(payload.get("input"), dict):
        raise ValueError("invalid database capability request")
    return payload


def _base_result(
    request: Mapping[str, Any],
    *,
    status: str,
    summary: str,
    mutation_level: str,
    data: Mapping[str, Any],
    blockers: Sequence[str],
    statement_types: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request["request_id"],
        "capability": request["capability"],
        "provider": "postgresql",
        "status": status,
        "mutation_level": mutation_level,
        "changed": False,
        "summary": summary,
        "data": dict(data),
        "evidence": [],
        "warnings": [],
        "blockers": list(blockers),
        "audit": {
            "credential_class": "none" if mutation_level == "L0" else "database_write",
            "external_write_attempted": False,
            "credential_loaded": False,
            "database_connection_attempted": False,
            "database_execution_attempted": False,
            "statement_types": list(statement_types),
        },
    }


def _disabled_result(request: Mapping[str, Any]) -> dict[str, Any]:
    return _base_result(
        request,
        status="blocked",
        summary=DISABLED_MESSAGE,
        mutation_level="L5",
        data={"plan_only": True},
        blockers=(DISABLED_MESSAGE,),
    )


def execute_request(request: object) -> dict[str, Any]:
    checked = _validate_request(request)
    if checked["capability"] == "database.change":
        if checked.get("mutation_level") != "L5" or checked.get("mode") not in {"preview", "apply"}:
            raise ValueError("invalid database.change request")
        return _disabled_result(checked)
    if (
        checked.get("mutation_level") != "L0"
        or checked.get("mode") != "preview"
        or checked.get("authorization") != {"explicit": False, "scope": []}
    ):
        raise ValueError("invalid database.change-plan request")
    plan = build_database_change_plan(checked["input"])
    statement_types = tuple(_statement_type(item) for item in plan.proposed_statements)
    return _base_result(
        checked,
        status="success" if plan.status == "ready_for_review" else "blocked",
        summary=(
            "DATABASE_CHANGE_PLAN_READY"
            if plan.status == "ready_for_review"
            else "DATABASE_CHANGE_PLAN_BLOCKED"
        ),
        mutation_level="L0",
        data={"plan": plan.to_dict()},
        blockers=plan.blockers,
        statement_types=statement_types,
    )


def _ensure_new_output(path: Path) -> None:
    candidate = path.absolute()
    temporary_root = Path(tempfile.gettempdir()).absolute()
    trusted_temporary_ancestors = {temporary_root, *temporary_root.parents}
    trusted_system_symlinks = {Path("/var"), Path("/tmp")}
    current = Path(candidate.anchor)
    for part in candidate.parts[1:-1]:
        current = current / part
        if (
            current.is_symlink()
            and current not in trusted_temporary_ancestors
            and current not in trusted_system_symlinks
        ) or not current.is_dir():
            raise ValueError("output unavailable")
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        info = None
    if info is not None or candidate.is_symlink():
        raise ValueError("output unavailable")


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_new_output(path)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 4 or arguments[0] != "--request" or arguments[2] != "--output":
        return 2
    try:
        request_path = Path(arguments[1])
        output_path = Path(arguments[3])
        if request_path.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        _ensure_new_output(output_path)
        result = execute_request(payload)
        _write_new_json(output_path, result)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
