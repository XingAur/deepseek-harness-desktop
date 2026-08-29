"""Deterministic post-governance capability routing for one Harness task."""
from __future__ import annotations

import re
from typing import Any, Mapping
from uuid import uuid4

from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityRequest,
    MutationLevel,
)
from app.task_intent_service import (
    TaskIntentRoutingResult,
    require_requirement_workflow_route,
)

__all__ = ("route_task_capabilities",)

READ_SCOPES = ("database:metadata:read", "database:rows:read")
_SENSITIVE_NAME = re.compile(
    r"(?:authorization|cookie|credential|dsn|password|secret|token|(?:^|_)pat(?:_|$)|private[_-]?key)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?:authorization|cookie|credential|dsn|password|secret|token|private[_ -]?key)"
    r"\s*[:=]",
    re.IGNORECASE,
)
SPECS = {
    "git.apply-local": ("his-engineering", MutationLevel.L2, "apply", ("repository:apply-local",)),
    "git.commit-local": ("his-engineering", MutationLevel.L3, "apply", ("repository:commit-local",)),
    "database.inspect.preview": ("postgresql", MutationLevel.L1, "preview", ()),
    "database.inspect.execute": ("postgresql", MutationLevel.L1, "apply", READ_SCOPES),
    "database.change-plan": ("postgresql", MutationLevel.L0, "preview", ()),
    "knowledge.candidate.create": ("his-knowledge", MutationLevel.L2, "apply", ("knowledge:candidate:create",)),
}


def _request(spec_name: str, payload: Mapping[str, Any]) -> CapabilityRequest:
    provider, level, mode, scopes = SPECS[spec_name]
    capability = spec_name.rsplit(".", 1)[0] if spec_name.startswith("database.inspect.") else spec_name
    return CapabilityRequest(
        request_id=f"{capability.replace('.', '-')}-{uuid4().hex}",
        capability=capability,
        provider=provider,
        mode=mode,
        mutation_level=level,
        authorization=CapabilityAuthorization(explicit=mode == "apply", scope=scopes),
        input=dict(payload),
        context={},
    )


def _readonly_preview_ready(result: Mapping[str, Any]) -> bool:
    data = result.get("data")
    plan = data.get("plan") if isinstance(data, Mapping) else None
    guard = plan.get("guard") if isinstance(plan, Mapping) else None
    return bool(
        isinstance(plan, Mapping)
        and plan.get("status") == "ready"
        and isinstance(plan.get("selected_profile"), str)
        and plan.get("selected_profile")
        and isinstance(guard, Mapping)
        and guard.get("status") == "pass"
        and guard.get("blockers") == []
    )


def _sanitize_public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_public_value(item)
            for key, item in value.items()
            if isinstance(key, str) and not _SENSITIVE_NAME.search(key)
        }
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, str) and _SENSITIVE_TEXT.search(value):
        return "[REDACTED]"
    if value is None or type(value) in {bool, int, float, str}:
        return value
    return None


def _public_result(
    spec_name: str,
    result: Mapping[str, Any],
) -> Mapping[str, Any]:
    status = result.get("status")
    public: dict[str, Any] = {
        "status": status if isinstance(status, str) else "failed",
    }
    if (
        status == "success"
        and spec_name.startswith("database.")
        and isinstance(result.get("data"), Mapping)
    ):
        public["data"] = _sanitize_public_value(result["data"])
    return public


def route_task_capabilities(
    service: Any,
    *,
    routing_result: TaskIntentRoutingResult,
    contract_ready: bool,
    project_path: str = "",
    expected_diff: str = "",
    allowed_paths: tuple[str, ...] = (),
    verify_commands: tuple[str, ...] = (),
    explicit_remote_delivery: bool = False,
    delivery: Mapping[str, Any] | None = None,
    code_evidence_sufficient: bool = True,
    database_inspect: Mapping[str, Any] | None = None,
    execute_database: bool = False,
    database_change: Mapping[str, Any] | None = None,
    knowledge_candidate: Mapping[str, Any] | None = None,
    knowledge_provenance: Mapping[str, Any] | None = None,
    allow_personal_memory: bool = False,
) -> tuple[
    str,
    tuple[str, ...],
    tuple[str, ...],
    Mapping[str, Mapping[str, Any]],
]:
    events: list[str] = []
    blockers: list[str] = []
    results: dict[str, Mapping[str, Any]] = {}
    mutation_actions_allowed = require_requirement_workflow_route(
        routing_result
    ).mutation_requested

    def route(spec_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        request = _request(spec_name, payload)
        events.append(request.capability)
        try:
            result = service.route(request).result
        except Exception:
            result = None
        if isinstance(result, Mapping):
            results[spec_name] = _public_result(spec_name, result)
        if isinstance(result, Mapping) and result.get("status") == "success":
            return result
        suffix = "failed" if result is None else "blocked"
        blockers.append(f"{request.capability.replace('.', '_').replace('-', '_')}_{suffix}")
        return None

    if expected_diff and mutation_actions_allowed:
        if contract_ready:
            route("git.apply-local", {
                "project_path": project_path,
                "expected_diff": expected_diff,
                "allowed_paths": list(allowed_paths),
                "verify_commands": list(verify_commands),
            })
        else:
            blockers.append("local_contract_not_ready")
    if explicit_remote_delivery and mutation_actions_allowed:
        if not contract_ready:
            blockers.append("delivery_contract_not_ready")
        elif isinstance(delivery, Mapping):
            route("git.commit-local", delivery)
        else:
            blockers.append("delivery_input_unavailable")
    if not code_evidence_sufficient and isinstance(database_inspect, Mapping):
        preview = route("database.inspect.preview", {**database_inspect, "mode": "plan"})
        if execute_database and preview is not None:
            if _readonly_preview_ready(preview):
                route("database.inspect.execute", {**database_inspect, "mode": "execute"})
            else:
                blockers.append("database_readonly_preview_not_ready")
    if mutation_actions_allowed and isinstance(database_change, Mapping):
        route("database.change-plan", database_change)
    if mutation_actions_allowed and isinstance(knowledge_candidate, Mapping):
        if isinstance(knowledge_provenance, Mapping):
            route("knowledge.candidate.create", {
                "payload": dict(knowledge_candidate),
                "provenance": dict(knowledge_provenance),
                "allow_personal_memory": bool(allow_personal_memory),
            })
        else:
            blockers.append("knowledge_candidate_provenance_unavailable")

    unique = tuple(dict.fromkeys(blockers))
    return (
        "blocked" if unique else "success",
        tuple(events),
        unique,
        results,
    )
