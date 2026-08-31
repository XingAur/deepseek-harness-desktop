"""Deterministic, non-secret binding for the pre-change user confirmation.

The confirmation is deliberately a hash of the exact technical scope that is
about to be handed to a mutating executor.  It is not an authorization to
write Yunxiao, Git remotes, or production systems; those existing capability
gates remain independent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


SCOPE_CONFIRMATION_SCHEMA_VERSION = "demand-scope-confirmation.v1"
_TOKEN_PREFIX = "CONFIRM-SCOPE:"
_CHANGE_PROJECT_SCOPES = {"change_required", "candidate_change"}
_PROJECT_SCOPE_DESCRIPTIONS = {
    "change_required": "需求已命中实际调用链，进入改动范围",
    "candidate_change": "已定位到实际调用链，仍需改动合同确认",
    "existing_dependency": "现有依赖，仅用于链路证据，不代表要改",
    "contract_check": "仅用于接口契约核验，不代表要改",
    "impact_regression": "仅用于影响回归核验，不代表要改",
    "entry_point": "仅为入口证据，不代表要改",
    "candidate_only": "仅候选，未形成实际改动证据",
    "legacy_selected": "旧数据未记录分层，暂按已选择项目展示",
}


def build_scope_confirmation_binding(
    *,
    execution_mode: str,
    technical_decision: Mapping[str, Any] | None,
    change_ownership: Mapping[str, Any] | None,
    governance: Mapping[str, Any] | None,
    single_pass_contract: Mapping[str, Any] | None,
    allowed_paths: Sequence[str] | None,
    verify_commands: Sequence[str] | None,
) -> dict[str, Any]:
    """Build a safe, canonical scope binding and its exact confirmation token."""

    technical = dict(technical_decision or {})
    ownership = dict(change_ownership or {})
    governance_data = dict(governance or {})
    contract = dict(single_pass_contract or {})
    explicit_allowed_paths = _strings(allowed_paths)
    explicit_verify_commands = _strings(verify_commands)
    scope = {
        "execution_mode": str(execution_mode or "unknown"),
        "projects": _projects(
            technical.get("selected_projects"),
            contract.get("repositories"),
            technical.get("multi_service_change_contract", {}).get("repositories")
            if isinstance(technical.get("multi_service_change_contract"), Mapping)
            else None,
        ),
        # The caller supplies the effective, already-authorized scope.  Do
        # not union stale technical recommendations into that exact binding;
        # doing so can make a narrowed capability contract change its hash
        # before execution and invalidate a valid user confirmation.
        "allowed_paths": (
            explicit_allowed_paths
            if explicit_allowed_paths
            else _strings(
                technical.get("recommended_allowed_paths"),
                contract.get("allowed_paths"),
                _ownership_paths(ownership),
            )
        ),
        "verify_commands": (
            explicit_verify_commands
            if explicit_verify_commands
            else _strings(contract.get("verify_commands"))
        ),
        "ownership": _ownership_summary(ownership),
        "governance": {
            "status": str(governance_data.get("status") or ""),
            "can_modify": governance_data.get("can_modify") is True,
        },
        "contract": {
            "status": str(contract.get("status") or ""),
            "repositories": _projects(contract.get("repositories")),
        },
    }
    canonical = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    scope_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "schema_version": SCOPE_CONFIRMATION_SCHEMA_VERSION,
        "status": "pending",
        "scope_hash": scope_hash,
        "confirmation_token": confirmation_token(scope_hash),
        "scope": scope,
    }


def confirmation_token(scope_hash: str) -> str:
    """Return the only accepted human confirmation token for a scope hash."""

    if not isinstance(scope_hash, str) or len(scope_hash) != 64:
        raise ValueError("scope_hash_invalid")
    if any(character not in "0123456789abcdef" for character in scope_hash):
        raise ValueError("scope_hash_invalid")
    return f"{_TOKEN_PREFIX}{scope_hash}"


def validate_scope_confirmation(received: str, expected_scope_hash: str) -> bool:
    """Fail closed unless the supplied value is the exact current token."""

    if not isinstance(received, str) or received != received.strip():
        return False
    try:
        expected = confirmation_token(expected_scope_hash)
    except ValueError:
        return False
    return received == expected


def scope_confirmation_to_markdown(
    binding: Mapping[str, Any],
    *,
    status: str,
    reason: str,
    confirmed_by: str = "",
) -> str:
    """Render the confirmation card without provider payloads or secrets."""

    scope = binding.get("scope") if isinstance(binding.get("scope"), Mapping) else {}
    lines = [
        "## 改动前范围确认",
        "",
        f"- 状态：`{status or 'pending'}`",
        f"- 执行模式：`{scope.get('execution_mode') or '-'}`",
        f"- 范围哈希：`{binding.get('scope_hash') or '-'}`",
        f"- 确认令牌：`{binding.get('confirmation_token') or '-'}`",
        f"- 说明：{reason or '请确认项目、服务、路径和验证命令。'}",
        "- 不确认不会进入改码、合入或本地执行。",
    ]
    if confirmed_by:
        lines.append(f"- 确认人：`{confirmed_by}`")
    lines.extend(["", "### 实际改动候选项目 / 服务", ""])
    projects = scope.get("projects") or []
    change_projects = [
        project
        for project in projects
        if str(project.get("selection_scope") or "legacy_selected") in _CHANGE_PROJECT_SCOPES
        or not project.get("selection_scope")
    ]
    if not change_projects:
        lines.append("- 尚未形成项目范围。")
    else:
        for project in change_projects:
            selection_scope = str(project.get("selection_scope") or "legacy_selected")
            lines.append(
                f"- `{project.get('name') or '-'}`（{project.get('role') or 'unknown'}，{selection_scope}）："
                f"{project.get('path') or '-'}；{_PROJECT_SCOPE_DESCRIPTIONS.get(selection_scope, selection_scope)}"
            )
    lines.extend(["", "### 证据与核验项目（不代表要改）", ""])
    evidence_projects = [
        project
        for project in projects
        if project not in change_projects
    ]
    if not evidence_projects:
        lines.append("- 无。")
    else:
        for project in evidence_projects:
            selection_scope = str(project.get("selection_scope") or "legacy_selected")
            lines.append(
                f"- `{project.get('name') or '-'}`（{project.get('role') or 'unknown'}，{selection_scope}）："
                f"{project.get('path') or '-'}；{_PROJECT_SCOPE_DESCRIPTIONS.get(selection_scope, selection_scope)}"
            )
    lines.extend(["", "### 允许路径", ""])
    paths = scope.get("allowed_paths") or []
    lines.extend(f"- `{path}`" for path in paths) if paths else lines.append("- 无。")
    lines.extend(["", "### 验证命令", ""])
    commands = scope.get("verify_commands") or []
    lines.extend(f"- `{command}`" for command in commands) if commands else lines.append("- 未配置。")
    return "\n".join(lines)


def _strings(*groups: Sequence[str] | None) -> list[str]:
    values: set[str] = set()
    for group in groups:
        if not isinstance(group, (list, tuple, set, frozenset)):
            continue
        for value in group:
            text = str(value).strip()
            if text:
                values.add(text)
    return sorted(values)


def _projects(*groups: Any) -> list[dict[str, str]]:
    values: dict[tuple[str, str, str], dict[str, str]] = {}
    for group in groups:
        if not isinstance(group, (list, tuple)):
            continue
        for item in group:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or item.get("project") or "").strip()
            path = str(item.get("path") or "").strip()
            role = str(item.get("role") or "unknown").strip()
            if not name and not path:
                continue
            key = (name, path, role)
            selection_scope = str(item.get("selection_scope") or "").strip()
            candidate = {"name": name, "path": path, "role": role}
            if selection_scope:
                candidate["selection_scope"] = selection_scope
            existing = values.get(key)
            if existing is None:
                values[key] = candidate
            elif selection_scope and not existing.get("selection_scope"):
                existing["selection_scope"] = selection_scope
    return [values[key] for key in sorted(values)]


def _ownership_paths(ownership: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []
    for value in ownership.values():
        if not isinstance(value, Mapping):
            continue
        paths.extend(value.get("paths") or value.get("allowed_paths") or [])
    return paths


def _ownership_summary(ownership: Mapping[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for layer in sorted(str(key) for key in ownership):
        value = ownership.get(layer)
        if not isinstance(value, Mapping):
            continue
        summary.append(
            {
                "layer": layer,
                "status": str(value.get("status") or ""),
                "paths": _strings(value.get("paths"), value.get("allowed_paths")),
            }
        )
    return summary
