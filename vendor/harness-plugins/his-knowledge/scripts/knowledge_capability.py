"""Fail-closed JSON transport shared by local HIS knowledge entrypoints."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Mapping


REQUEST_FIELDS = frozenset((
    "schema_version", "request_id", "capability", "provider", "mode",
    "mutation_level", "authorization", "input", "context",
))
PROVIDER = "his-knowledge"


def validate_request(
    payload: object,
    *, capability: str,
    mode: str,
    mutation_level: str,
    scope: tuple[str, ...],
    input_fields: frozenset[str],
    validator: Callable[[Mapping[str, Any]], None],
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise ValueError("invalid capability request")
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("capability") != capability
        or payload.get("provider") != PROVIDER
        or payload.get("mode") != mode
        or payload.get("mutation_level") != mutation_level
        or not isinstance(payload.get("request_id"), str)
        or not payload["request_id"].strip()
    ):
        raise ValueError("invalid capability request")
    expected_authorization = {
        "explicit": mode == "apply",
        "scope": list(scope),
    }
    if payload.get("authorization") != expected_authorization:
        raise ValueError("invalid capability request")
    if payload.get("context") != {}:
        raise ValueError("invalid capability request")
    input_data = payload.get("input")
    if not isinstance(input_data, dict) or not set(input_data).issubset(input_fields):
        raise ValueError("invalid capability request")
    validator(input_data)
    return payload


def result(
    request: Mapping[str, Any],
    *, status: str,
    summary: str,
    mutation_level: str,
    changed: bool,
    data: Mapping[str, Any],
    evidence: list[Mapping[str, Any]] | None = None,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request["request_id"],
        "capability": request["capability"],
        "provider": PROVIDER,
        "status": status,
        "mutation_level": mutation_level,
        "changed": changed,
        "summary": summary,
        "data": dict(data),
        "evidence": list(evidence or ()),
        "warnings": list(warnings or ()),
        "blockers": list(blockers or ()),
        "audit": dict(audit or {"credential_class": "none", "external_write_attempted": False}),
    }


def knowledge_home() -> Path:
    value = os.environ.get("HIS_KNOWLEDGE_HOME")
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError("invalid knowledge home")
    home = Path(value)
    parent = home.parent
    _safe_ancestors(parent / "placeholder")
    if home.exists() and (home.is_symlink() or not home.is_dir()):
        raise ValueError("invalid knowledge home")
    return home


def _safe_file(path: Path, *, must_exist: bool) -> None:
    _safe_ancestors(path)
    if path.is_symlink():
        raise ValueError("path unavailable")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if must_exist:
            raise ValueError("path unavailable")
        return
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("path unavailable")


def _safe_ancestors(path: Path) -> None:
    candidate = path.absolute()
    temporary_root = Path("/tmp").absolute()
    trusted = {temporary_root, *temporary_root.parents, Path("/var"), Path("/private"), Path("/private/var")}
    current = Path(candidate.anchor)
    for part in candidate.parts[1:-1]:
        current = current / part
        if not current.exists() or (current.is_symlink() and current not in trusted) or not current.is_dir():
            raise ValueError("path unavailable")


def _safe_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("output unavailable")
    _safe_ancestors(path)


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    created = False
    try:
        descriptor = os.open(str(path), flags, stat.S_IRUSR | stat.S_IWUSR)
        created = True
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("output write failed")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def run_main(argv: list[str] | None, handler: Callable[[object], Mapping[str, Any]]) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 4 or arguments[0] != "--request" or arguments[2] != "--output":
        sys.stderr.write("invalid arguments\n")
        return 2
    try:
        request_path, output_path = Path(arguments[1]), Path(arguments[3])
        if request_path.absolute() == output_path.absolute():
            raise ValueError("path alias")
        _safe_file(request_path, must_exist=True)
        _safe_output(output_path)
        request = json.loads(request_path.read_text(encoding="utf-8"))
        payload = handler(request)
        _write_new_json(output_path, payload)
    except Exception:
        sys.stderr.write("invalid capability request or output\n")
        return 2
    return 0
