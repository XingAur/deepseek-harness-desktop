#!/usr/bin/env python3
"""Fail-closed PostgreSQL evidence capability entrypoint."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from pg_evidence import (  # noqa: E402
    PgEvidenceRequest,
    build_parameter_audit,
    build_psycopg_executor_factory,
    build_query_template_id,
    discover_pg_profiles,
    load_pg_policy,
    run_pg_evidence,
)


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
AUTHORIZATION_FIELDS = frozenset({"explicit", "scope"})
INPUT_FIELDS = frozenset(
    {
        "subject",
        "keywords",
        "sql",
        "parameters",
        "project_root",
        "profile_policy",
        "mode",
    }
)
EXACT_READ_SCOPES = ("database:metadata:read", "database:rows:read")
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RUNTIME_ENVIRONMENT_BYTES = 128 * 1024
RUNTIME_ENVIRONMENT_SCHEMA = "his-database-runtime-environment.v1"
READONLY_CREDENTIAL_PATTERN = re.compile(
    r"^pg_[a-z0-9_]+_readonly_(?:dsn|user|password)$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _validate_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise ValueError("invalid database.inspect request")
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("capability") != "database.inspect"
        or payload.get("provider") != "postgresql"
        or payload.get("mode") not in {"preview", "apply"}
        or payload.get("mutation_level") != "L1"
        or payload.get("context") != {}
    ):
        raise ValueError("invalid database.inspect request")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("invalid database.inspect request")

    authorization = payload.get("authorization")
    if (
        not isinstance(authorization, dict)
        or set(authorization) != AUTHORIZATION_FIELDS
        or not isinstance(authorization.get("explicit"), bool)
        or not _valid_string_list(authorization.get("scope"))
    ):
        raise ValueError("invalid database.inspect authorization")

    input_data = payload.get("input")
    if not isinstance(input_data, dict) or set(input_data) != INPUT_FIELDS:
        raise ValueError("invalid database.inspect input")
    subject = input_data.get("subject")
    keywords = input_data.get("keywords")
    sql = input_data.get("sql")
    parameters = input_data.get("parameters")
    project_root = input_data.get("project_root")
    profile_policy = input_data.get("profile_policy")
    inner_mode = input_data.get("mode")
    if (
        not isinstance(subject, str)
        or not subject.strip()
        or not _valid_string_list(keywords)
        or not isinstance(sql, str)
        or not isinstance(parameters, dict)
        or not all(isinstance(name, str) and name for name in parameters)
        or not _absolute_path(project_root)
        or not _absolute_path(profile_policy)
        or inner_mode not in {"plan", "execute"}
    ):
        raise ValueError("invalid database.inspect input")
    try:
        json.dumps(parameters)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid database.inspect parameters") from exc
    return payload


def _valid_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
        and len(value) == len(set(value))
    )


def _absolute_path(value: object) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).is_absolute()


def _authorization_allowed(request: Mapping[str, Any]) -> bool:
    authorization = request["authorization"]
    if request["mode"] == "preview":
        return authorization == {"explicit": False, "scope": []}
    return (
        authorization.get("explicit") is True
        and tuple(authorization.get("scope", ())) == EXACT_READ_SCOPES
    )


def _audit(
    *,
    query_template_id: str,
    parameter_audit: Sequence[Mapping[str, str]],
    profile: str = "",
    row_count: int = 0,
    masked_columns: Sequence[str] = (),
    database_connection_attempted: bool = False,
) -> dict[str, Any]:
    return {
        "credential_class": "database_readonly",
        "external_write_attempted": False,
        "database_connection_attempted": database_connection_attempted,
        "query_template_id": query_template_id,
        "parameter_audit": [dict(item) for item in parameter_audit],
        "profile": profile,
        "row_count": row_count,
        "masked_columns": list(masked_columns),
    }


def _result(
    request: Mapping[str, Any],
    *,
    status: str,
    summary: str,
    pg_status: str,
    effective_mode: str,
    plan_status: str = "",
    plan: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
    blockers: Sequence[str] = (),
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "pg_status": pg_status,
        "effective_mode": effective_mode,
        "plan_status": plan_status,
    }
    if plan is not None:
        data["plan"] = dict(plan)
    if result is not None:
        data["result"] = dict(result)
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request["request_id"],
        "capability": "database.inspect",
        "provider": "postgresql",
        "status": status,
        "mutation_level": "L1",
        "changed": False,
        "summary": summary,
        "data": data,
        "evidence": [],
        "warnings": [],
        "blockers": list(blockers),
        "audit": dict(audit),
    }


def execute_request(
    request: object,
    *,
    executor_factory: Any = None,
    environ: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    checked = _validate_request(request)
    input_data = checked["input"]
    query_template_id = build_query_template_id(input_data["sql"])
    parameter_audit = build_parameter_audit(input_data["parameters"])
    effective_mode = (
        "execute"
        if checked["mode"] == "apply" and input_data["mode"] == "execute"
        else "plan"
    )
    empty_audit = _audit(
        query_template_id=query_template_id,
        parameter_audit=parameter_audit,
    )

    if not _authorization_allowed(checked):
        return _result(
            checked,
            status="blocked",
            summary="DATABASE_INSPECT_AUTHORIZATION_BLOCKED",
            pg_status="blocked",
            effective_mode=effective_mode,
            blockers=("DATABASE_INSPECT_AUTHORIZATION_BLOCKED",),
            audit=empty_audit,
        )

    project_root = Path(input_data["project_root"])
    profile_policy = Path(input_data["profile_policy"])
    if not project_root.is_dir() or not profile_policy.is_file():
        return _result(
            checked,
            status="blocked",
            summary="DATABASE_INSPECT_PROFILE_POLICY_BLOCKED",
            pg_status="blocked",
            effective_mode=effective_mode,
            blockers=("DATABASE_INSPECT_PROFILE_POLICY_BLOCKED",),
            audit=empty_audit,
        )

    environment = {} if environ is None else environ
    try:
        policy = load_pg_policy(profile_policy)
        profiles = discover_pg_profiles(environment)
        pg_request = PgEvidenceRequest(
            subject=input_data["subject"],
            keywords=tuple(input_data["keywords"]),
            sql=input_data["sql"],
            parameters=dict(input_data["parameters"]),
        )
        selected_factory = None
        if effective_mode == "execute":
            if executor_factory is not None:
                selected_factory = executor_factory
            else:
                def selected_factory(*, plan: Any) -> Any:
                    factory = build_psycopg_executor_factory(environment)
                    return factory(plan=plan)
        run = run_pg_evidence(
            request=pg_request,
            policy=policy,
            profiles=profiles,
            project_root=project_root,
            mode=effective_mode,
            executor_factory=selected_factory,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return _result(
            checked,
            status="blocked",
            summary="DATABASE_INSPECT_PROFILE_POLICY_BLOCKED",
            pg_status="blocked",
            effective_mode=effective_mode,
            blockers=("DATABASE_INSPECT_PROFILE_POLICY_BLOCKED",),
            audit=empty_audit,
        )

    pg_status = run.status
    if effective_mode == "plan":
        pg_status = "planned" if run.plan.status in {"ready", "metadata_required"} else run.plan.status
    capability_status = {
        "planned": "success",
        "passed": "success",
        "blocked": "blocked",
        "needs_evidence": "blocked",
        "timeout": "failed",
        "failed": "failed",
    }.get(pg_status, "failed")
    run_audit = _audit(
        query_template_id=run.plan.query_template_id,
        parameter_audit=run.result.parameter_audit,
        profile=run.result.profile or run.plan.selected_profile,
        row_count=run.result.row_count,
        masked_columns=run.result.masked_columns,
        database_connection_attempted=run.audit.get("executor_created") is True,
    )
    status_blocker = (
        ()
        if capability_status == "success"
        else (f"DATABASE_INSPECT_{pg_status.upper()}",)
    )
    return _result(
        checked,
        status=capability_status,
        summary=f"DATABASE_INSPECT_{pg_status.upper()}",
        pg_status=pg_status,
        effective_mode=effective_mode,
        plan_status=run.plan.status,
        plan=run.plan.to_dict(),
        result=run.result.to_dict(),
        blockers=status_blocker,
        audit=run_audit,
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


def _read_runtime_environment(
    path: Path,
    *,
    request_path: Path,
    output_path: Path,
    expected_sha256: str,
) -> dict[str, str]:
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise ValueError("runtime environment unavailable")
    candidate = path.absolute()
    parent = candidate.parent
    resolved_candidate = candidate.resolve(strict=True)
    resolved_parent = resolved_candidate.parent
    if (
        not path.is_absolute()
        or candidate.is_symlink()
        or request_path.absolute().parent.resolve(strict=True) != resolved_parent
        or output_path.absolute().parent.resolve(strict=True) != resolved_parent
    ):
        raise ValueError("runtime environment unavailable")
    parent_info = resolved_parent.stat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) & 0o077
    ):
        raise ValueError("runtime environment unavailable")
    info = candidate.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != stat.S_IRUSR
        or info.st_size > MAX_RUNTIME_ENVIRONMENT_BYTES
        or resolved_candidate.name != candidate.name
    ):
        raise ValueError("runtime environment unavailable")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags)
    try:
        before = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        )
        expected_identity = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
        )
        if before_identity != expected_identity:
            raise ValueError("runtime environment unavailable")
        chunks: list[bytes] = []
        remaining = MAX_RUNTIME_ENVIRONMENT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        after = os.fstat(descriptor)
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        )
        if (
            len(encoded) != info.st_size
            or after_identity != expected_identity
            or hashlib.sha256(encoded).hexdigest() != expected_sha256
        ):
            raise ValueError("runtime environment unavailable")
    finally:
        os.close(descriptor)
    payload = json.loads(encoded.decode("utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "environment"}
        or payload.get("schema_version") != RUNTIME_ENVIRONMENT_SCHEMA
        or not isinstance(payload.get("environment"), dict)
    ):
        raise ValueError("runtime environment unavailable")
    environment = payload["environment"]
    if not all(
        isinstance(key, str)
        and READONLY_CREDENTIAL_PATTERN.fullmatch(key)
        and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValueError("runtime environment unavailable")
    return dict(environment)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        len(arguments) not in {4, 8}
        or arguments[0] != "--request"
        or arguments[2] != "--output"
        or (
            len(arguments) == 8
            and (
                arguments[4] != "--runtime-environment-file"
                or arguments[6] != "--runtime-environment-sha256"
            )
        )
    ):
        return 2
    try:
        request_path = Path(arguments[1])
        output_path = Path(arguments[3])
        environment = (
            {}
            if len(arguments) == 4
            else _read_runtime_environment(
                Path(arguments[5]),
                request_path=request_path,
                output_path=output_path,
                expected_sha256=arguments[7],
            )
        )
        if request_path.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        _ensure_new_output(output_path)
        result = execute_request(payload, environ=environment)
        _write_new_json(output_path, result)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
