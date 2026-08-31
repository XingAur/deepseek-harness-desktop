#!/usr/bin/env python3
"""Standalone, read-only Yunxiao work-item capability entrypoint."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping


REQUEST_FIELDS = frozenset((
    "schema_version", "request_id", "capability", "provider", "mode",
    "mutation_level", "authorization", "input", "context",
))
INPUT_FIELDS = frozenset(("url", "entity_id"))
CONTEXT_FIELDS = frozenset(("include_comments",))


def _evidence_module() -> Any:
    path = Path(__file__).resolve().parent / "yunxiao_evidence.py"
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("evidence module unavailable")
    # The capability runtime snapshots the frozen plugin source into a fresh
    # execution directory.  Compile the dependency bytes directly instead of
    # asking Python's source loader, which may reuse a stale sibling .pyc when
    # an installed plugin was updated in place.
    module = ModuleType("_yunxiao_plugin_evidence")
    module.__file__ = str(path)
    module.__package__ = ""
    code = compile(path.read_bytes(), str(path), "exec")
    exec(code, module.__dict__)
    return module


_EVIDENCE = _evidence_module()


def _validate_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise ValueError("invalid capability request")
    if payload.get("schema_version") != "his-capability-request.v1":
        raise ValueError("invalid capability request")
    for field in ("request_id", "capability", "provider", "mode", "mutation_level"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError("invalid capability request")
    if (
        payload["capability"] != "workitem.read"
        or payload["provider"] != "yunxiao"
        or payload["mode"] != "preview"
        or payload["mutation_level"] != "L1"
    ):
        raise ValueError("invalid capability request")
    authorization = payload.get("authorization")
    if (
        not isinstance(authorization, dict)
        or set(authorization) != {"explicit", "scope"}
        or not isinstance(authorization.get("explicit"), bool)
        or not isinstance(authorization.get("scope"), list)
        or any(not isinstance(item, str) or not item.strip() for item in authorization["scope"])
    ):
        raise ValueError("invalid capability request")
    input_data = payload.get("input")
    if not isinstance(input_data, dict) or not set(input_data).issubset(INPUT_FIELDS):
        raise ValueError("invalid capability request")
    if any(not isinstance(value, str) for value in input_data.values()):
        raise ValueError("invalid capability request")
    if not any(input_data.get(field, "").strip() for field in INPUT_FIELDS):
        raise ValueError("invalid capability request")
    context = payload.get("context")
    if (
        not isinstance(context, dict)
        or set(context) != CONTEXT_FIELDS
        or not isinstance(context.get("include_comments"), bool)
    ):
        raise ValueError("invalid capability request")
    return payload


def _result(request: Mapping[str, Any], *, status: str, summary: str, data: Mapping[str, Any], warnings: list[str], blockers: list[str], audit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request["request_id"],
        "capability": "workitem.read",
        "provider": "yunxiao",
        "status": status,
        "mutation_level": "L1",
        "changed": False,
        "summary": summary,
        "data": dict(data),
        "evidence": [],
        "warnings": warnings,
        "blockers": blockers,
        "audit": dict(audit),
    }


def _issue_codes(evidence: Mapping[str, Any], field: str) -> list[str]:
    values = evidence.get(field, [])
    if not isinstance(values, list):
        return []
    return [str(item.get("code")) for item in values if isinstance(item, dict) and isinstance(item.get("code"), str)]


def _usable_partial_evidence(evidence: Mapping[str, Any]) -> bool:
    """Allow analysis to continue when optional Yunxiao evidence is missing.

    A stale inline-file identifier, an unavailable parent relation, or a
    single attachment download must not hide a readable work item.  The
    requested work item itself still has to contain a title and description;
    malformed or unavailable primary data remains a hard failure.
    """

    completeness = evidence.get("completeness")
    if not isinstance(completeness, Mapping) or completeness.get("status") != "partial":
        return False
    work_items = evidence.get("work_items")
    if not isinstance(work_items, list):
        return False
    primary = next(
        (
            item for item in work_items
            if isinstance(item, Mapping)
            and str(item.get("id") or "") == str(
                ((evidence.get("source") or {}).get("resolved_work_item_id") or "")
            )
        ),
        None,
    )
    primary = primary or next((item for item in work_items if isinstance(item, Mapping)), None)
    if not isinstance(primary, Mapping):
        return False
    title = str(primary.get("title") or "").strip()
    description = primary.get("description")
    description_text = (
        str(description.get("text") or "").strip()
        if isinstance(description, Mapping)
        else ""
    )
    if not title or not description_text:
        return False
    hard_errors = {
        "requested_work_item_invalid",
        "requested_work_item_unavailable",
        "work_item_not_found",
    }
    return not hard_errors.intersection(_issue_codes(evidence, "errors"))


def execute_request(
    request: object,
    *,
    credential_loader: Callable[..., Mapping[str, Any]] | None = None,
    client_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    collector: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure injectable entrypoint used by offline tests; production defaults are read-only."""
    checked = _validate_request(request)
    loader = credential_loader or _EVIDENCE.load_credentials
    factory = client_factory or (lambda credentials: _EVIDENCE.YunxiaoClient(
        token=credentials["token"], organization_id=credentials["organization_id"]
    ))
    collect = collector or _EVIDENCE.collect_evidence
    audit = {"credential_class": "yunxiao_read", "external_write_attempted": False}
    try:
        credentials = loader(credential_kind="read")
        token = credentials.get("token") if isinstance(credentials, Mapping) else ""
        organization_id = credentials.get("organization_id") if isinstance(credentials, Mapping) else ""
        if not isinstance(token, str) or not token or not isinstance(organization_id, str) or not organization_id:
            raise ValueError("read credentials unavailable")
        client = factory(credentials)
        input_data = checked["input"]
        source = input_data.get("url", "").strip() or input_data.get("entity_id", "").strip()
        evidence = dict(collect(
            source=source,
            client=client,
            include_comments=checked["context"]["include_comments"],
            secrets=[token, organization_id],
        ))
    except Exception:
        return _result(checked, status="failed", summary="YUNXIAO_READ_UNAVAILABLE", data={}, warnings=[], blockers=[], audit=audit)
    gate = ((evidence.get("decision_gate") or {}).get("state") if isinstance(evidence.get("decision_gate"), dict) else "")
    warnings = _issue_codes(evidence, "warnings")
    errors = _issue_codes(evidence, "errors")
    if gate == "ready_for_analysis":
        return _result(checked, status="success", summary="YUNXIAO_READ_READY", data=evidence, warnings=warnings, blockers=[], audit=audit)
    if gate == "needs_requirement_confirmation":
        if _usable_partial_evidence(evidence):
            return _result(
                checked,
                status="partial",
                summary="YUNXIAO_READ_PARTIAL",
                data=evidence,
                warnings=warnings,
                blockers=[],
                audit=audit,
            )
        return _result(checked, status="blocked", summary="YUNXIAO_READ_CONFIRMATION_REQUIRED", data=evidence, warnings=warnings, blockers=errors or ["requirement_confirmation"], audit=audit)
    return _result(checked, status="failed", summary="YUNXIAO_READ_FAILED", data=evidence, warnings=warnings, blockers=errors, audit=audit)


def _ensure_new_output(path: Path) -> None:
    candidate = path.absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("output unavailable")
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None or path.is_symlink():
        raise ValueError("output unavailable")


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_new_output(path)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
    except Exception:
        raise


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 4 or arguments[0] != "--request" or arguments[2] != "--output":
        sys.stderr.write("invalid arguments\n")
        return 2
    try:
        request_path, output_path = Path(arguments[1]), Path(arguments[3])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        _ensure_new_output(output_path)
        result = execute_request(request)
        _write_new_json(output_path, result)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        sys.stderr.write("invalid capability request or output\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
