#!/usr/bin/env python3
"""Local, no-mutation adapter for the Harness requirement governance API."""
from __future__ import annotations

import importlib
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
INPUT_FIELDS = frozenset((
    "title", "user_instruction", "source_type", "normalized_requirement_evidence",
    "yunxiao_evidence", "requirement_calibration", "technical_decision",
    "change_ownership", "acceptance_matrix",
))
_SOURCE_TYPES = frozenset(("manual", "file", "yunxiao"))
_BUNDLED_HARNESS_ROOT = Path(__file__).resolve().parents[3] / "core"
_DESKTOP_BUILD_HARNESS_ROOT = Path(__file__).resolve().parents[3] / "harness-core"
_STAGED_HARNESS_ROOT = Path(__file__).resolve().parents[3] / "Harness"
_DOCUMENTED_HARNESS_ROOT = Path("/Users/lym/WorkCode/ai/Harness")
_BOUNDARIES = {
    "credential_lookup": False,
    "network": False,
    "repository_mutation": False,
    "external_write": False,
    "command_execution": False,
    "environment_authorization": False,
}


def _validate_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise ValueError("invalid capability request")
    if payload.get("schema_version") != "his-capability-request.v1":
        raise ValueError("invalid capability request")
    for field in ("request_id", "capability", "provider", "mode", "mutation_level"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError("invalid capability request")
    if (
        payload["capability"] != "requirement.govern"
        or payload["provider"] != "his-harness-core"
        or payload["mode"] != "preview"
        or payload["mutation_level"] != "L0"
    ):
        raise ValueError("invalid capability request")
    authorization = payload.get("authorization")
    if authorization != {"explicit": False, "scope": []}:
        raise ValueError("invalid capability request")
    if payload.get("context") != {}:
        raise ValueError("invalid capability request")
    input_data = payload.get("input")
    if not isinstance(input_data, dict) or set(input_data) != INPUT_FIELDS:
        raise ValueError("invalid capability request")
    if (
        not isinstance(input_data["title"], str)
        or not isinstance(input_data["user_instruction"], str)
        or not isinstance(input_data["source_type"], str)
        or input_data["source_type"] not in _SOURCE_TYPES
    ):
        raise ValueError("invalid capability request")
    for field in (
        "requirement_calibration", "technical_decision", "change_ownership", "acceptance_matrix",
    ):
        if not isinstance(input_data[field], dict):
            raise ValueError("invalid capability request")
    for field in ("normalized_requirement_evidence", "yunxiao_evidence"):
        if input_data[field] is not None and not isinstance(input_data[field], dict):
            raise ValueError("invalid capability request")
    return payload


def _safe_harness_root(candidate: Path) -> Path | None:
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            return None
        root = candidate.resolve(strict=True)
        module = root / "app" / "harness.py"
        if module.is_symlink() or not module.is_file():
            return None
        module.resolve(strict=True).relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return root


def _resolve_harness_root() -> Path:
    configured = os.environ.get("HIS_HARNESS_ROOT", "")
    candidates = (
        *((Path(configured),) if configured else ()),
        _BUNDLED_HARNESS_ROOT,
        _DESKTOP_BUILD_HARNESS_ROOT,
        _STAGED_HARNESS_ROOT,
        _DOCUMENTED_HARNESS_ROOT,
    )
    for candidate in candidates:
        root = _safe_harness_root(candidate)
        if root is not None:
            return root
    raise RuntimeError("harness unavailable")


def _verified_harness_module(root: Path, module_name: str, relative_path: str) -> Any:
    expected = root / relative_path
    try:
        if expected.is_symlink() or not expected.is_file():
            raise RuntimeError("harness unavailable")
        expected = expected.resolve(strict=True)
        expected.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("harness unavailable") from exc
    module = sys.modules.get(module_name)
    if module is None:
        module = importlib.import_module(module_name)
    module_path = getattr(module, "__file__", "")
    try:
        if not isinstance(module_path, str) or Path(module_path).resolve(strict=True) != expected:
            raise RuntimeError("harness unavailable")
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("harness unavailable") from exc
    return module


def _load_governance_api() -> tuple[Callable[..., tuple[object, object, str]], Callable[..., tuple[object | None, object | None]]]:
    root = _resolve_harness_root()
    existing_package = sys.modules.get("app")
    if existing_package is not None:
        package_path = getattr(existing_package, "__path__", ())
        if str(root / "app") not in package_path:
            raise RuntimeError("harness unavailable")
    sys.path.insert(0, str(root))
    try:
        builder_module = _verified_harness_module(root, "app.harness", "app/harness.py")
        validator_module = _verified_harness_module(
            root, "app.core_closure", "app/core_closure.py"
        )
        builder = getattr(builder_module, "build_requirement_governance_outputs", None)
        validator = getattr(
            validator_module, "validate_requirement_governance_outputs", None
        )
        if not callable(builder) or not callable(validator):
            raise RuntimeError("harness unavailable")
        return builder, validator
    finally:
        sys.path.pop(0)


def _result(
    request: Mapping[str, Any], *, status: str, summary: str, data: Mapping[str, Any],
    blockers: list[str], warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request["request_id"],
        "capability": "requirement.govern",
        "provider": "his-harness-core",
        "status": status,
        "mutation_level": "L0",
        "changed": False,
        "summary": summary,
        "data": dict(data),
        "evidence": [],
        "warnings": list(warnings or []),
        "blockers": list(blockers),
        "audit": {"credential_class": "none", "boundaries": dict(_BOUNDARIES)},
    }


def _serialize_validated_model(value: object) -> dict[str, Any]:
    to_dict = getattr(value, "to_dict", None)
    if not callable(to_dict):
        raise RuntimeError("governance unavailable")
    payload = to_dict()
    if not isinstance(payload, dict):
        raise RuntimeError("governance unavailable")
    canonical = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if not isinstance(canonical, dict):
        raise RuntimeError("governance unavailable")
    return canonical


def _blockers(governance: Mapping[str, Any], contract: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for payload in (governance, contract):
        raw = payload.get("blockers")
        if isinstance(raw, list):
            values.extend(item for item in raw if isinstance(item, str) and item.strip())
    if not values:
        values.append("REQUIREMENT_GOVERNANCE_NOT_READY")
    return list(dict.fromkeys(values))


def execute_request(request: object) -> dict[str, Any]:
    """Validate the exact local request, then call the canonical Harness API once."""
    checked = _validate_request(request)
    try:
        builder, validator = _load_governance_api()
        governance, contract, error = builder(**checked["input"])
        if error:
            raise RuntimeError("governance unavailable")
        validated_governance, validated_contract = validator(governance, contract)
        if (
            validated_governance is None
            or validated_contract is None
            or validated_governance is governance
            or validated_contract is contract
        ):
            raise RuntimeError("governance unavailable")
        governance_data = _serialize_validated_model(validated_governance)
        contract_data = _serialize_validated_model(validated_contract)
    except Exception:
        return _result(
            checked,
            status="failed",
            summary="REQUIREMENT_GOVERNANCE_UNAVAILABLE",
            data={},
            blockers=["REQUIREMENT_GOVERNANCE_UNAVAILABLE"],
        )
    data = {
        "governance": governance_data,
        "single_pass_change_contract": contract_data,
    }
    if (
        governance_data.get("status") == "ready_for_local_change"
        and contract_data.get("status") == "ready"
    ):
        return _result(
            checked,
            status="success",
            summary="REQUIREMENT_GOVERNANCE_READY",
            data=data,
            blockers=[],
        )
    return _result(
        checked,
        status="blocked",
        summary="REQUIREMENT_GOVERNANCE_BLOCKED",
        data=data,
        blockers=_blockers(governance_data, contract_data),
    )


def _ensure_new_output(path: Path) -> None:
    candidate = path.absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:-1]:
        current = current / part
        if current.is_symlink() and current != Path("/var"):
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
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 4 or arguments[0] != "--request" or arguments[2] != "--output":
        sys.stderr.write("invalid arguments\n")
        return 2
    try:
        request_path, output_path = Path(arguments[1]), Path(arguments[3])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        _ensure_new_output(output_path)
        _write_new_json(output_path, execute_request(request))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        sys.stderr.write("invalid capability request or output\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
