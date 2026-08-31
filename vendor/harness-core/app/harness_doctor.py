"""Read-only Harness environment diagnosis.

The doctor deliberately checks configuration, local Git state and plugin
manifests without opening a remote connection or printing credential values.
It is safe to run before every requirement; mutation and remote-write gates
remain owned by the normal capability workflow.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.capability_registry import CapabilityRegistry
from app.runtime_preflight import run_runtime_preflight


DOCTOR_SCHEMA_VERSION = "his-harness-doctor.v1"
_DB_CREDENTIAL_SUFFIXES = ("dsn", "user", "password")


def run_harness_doctor(
    *,
    database_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    worktree_root: str | Path | None = None,
    plugin_roots: Sequence[str | Path] = (),
    repository_paths: Sequence[str | Path] = (),
    database_profile: str = "",
    database_policy_path: str | Path | None = None,
    credentials_file: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    require_git: bool = False,
    mutation_requested: bool = False,
    require_database: bool = False,
) -> dict[str, Any]:
    """Return a redacted, deterministic diagnosis and recovery suggestions."""

    runtime = run_runtime_preflight(
        database_path=database_path,
        output_dir=output_dir,
        worktree_root=worktree_root,
        require_git=require_git,
        mutation_requested=mutation_requested,
    )
    checks: dict[str, Any] = {"runtime": runtime}
    checks["plugins"] = _check_plugins(plugin_roots)
    checks["repositories"] = [
        _check_repository(path) for path in repository_paths
    ]
    checks["database_policy"] = _check_database_policy(
        database_profile,
        database_policy_path,
    )
    checks["database_credentials"] = _check_database_credentials(
        database_profile,
        environment if environment is not None else os.environ,
        credentials_file=credentials_file,
        credential_profile=str(
            checks["database_policy"].get("credential_profile") or database_profile
        ),
        required=require_database,
    )

    errors: list[str] = []
    warnings: list[str] = []
    if runtime.get("status") == "blocked":
        errors.extend(str(item) for item in runtime.get("mutation_blockers") or [])
    elif runtime.get("status") == "degraded_readonly":
        warnings.extend(str(item) for item in runtime.get("failed_checks") or [])
    plugin_check = checks["plugins"]
    errors.extend(str(item) for item in plugin_check.get("errors") or [])
    warnings.extend(str(item) for item in plugin_check.get("warnings") or [])
    for repository in checks["repositories"]:
        if repository.get("status") == "failed":
            (errors if require_git else warnings).append(
                f"repository:{repository.get('path')}:unavailable"
            )
    database_check = checks["database_credentials"]
    if database_check.get("status") == "failed":
        (errors if require_database else warnings).append("database_credentials_missing")
    elif database_check.get("status") == "degraded":
        warnings.append("database_credentials_incomplete")
    database_policy = checks["database_policy"]
    if database_policy.get("status") == "failed":
        (errors if require_database else warnings).append("database_policy_unavailable")

    status = "blocked" if errors else "degraded" if warnings else "ready"
    recovery = _recovery_actions(checks, errors=errors, warnings=warnings)
    return {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "status": status,
        "checks": checks,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "recovery_actions": recovery,
        "external_calls": False,
        "external_writes": False,
        "credential_key_names_inspected": bool(database_profile),
        "credential_values_exposed": False,
        "credentials_read": False,
    }


def _check_plugins(plugin_roots: Sequence[str | Path]) -> dict[str, Any]:
    roots = [Path(value).expanduser() for value in plugin_roots if str(value).strip()]
    if not roots:
        return {"status": "skipped", "roots": [], "errors": [], "warnings": ["plugin_roots_not_configured"]}
    root_items: list[dict[str, Any]] = []
    errors: list[str] = []
    for root in roots:
        manifest = root / "capabilities.json"
        plugin_metadata = root / ".codex-plugin" / "plugin.json"
        item = {
            "root": str(root),
            "exists": root.is_dir(),
            "capabilities_manifest": manifest.is_file(),
            "plugin_metadata": plugin_metadata.is_file(),
        }
        if not root.is_dir() or not manifest.is_file():
            errors.append(f"plugin:{root}:manifest_unavailable")
        root_items.append(item)
    if not errors:
        try:
            CapabilityRegistry.from_plugin_roots(roots)
        except Exception as exc:
            errors.append(f"plugin_registry:{type(exc).__name__}")
    return {
        "status": "failed" if errors else "ready",
        "roots": root_items,
        "errors": errors,
        "warnings": [],
    }


def _check_repository(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    result: dict[str, Any] = {
        "path": str(path),
        "status": "failed",
        "git_root": "",
        "branch": "",
        "changed_paths": [],
        "remotes": [],
        "message": "",
    }
    if not path.is_dir():
        result["message"] = "repository_path_missing"
        return result
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path), capture_output=True, text=True, timeout=5, check=False,
        )
        if root.returncode != 0:
            result["message"] = "not_a_git_repository"
            return result
        result["git_root"] = (root.stdout or "").strip()
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(path), capture_output=True, text=True, timeout=5, check=False,
        )
        result["branch"] = (branch.stdout or "").strip()
        status = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=str(path), capture_output=True, text=True, timeout=8, check=False,
        )
        result["changed_paths"] = [
            line[3:] if len(line) >= 4 else line
            for line in (status.stdout or "").splitlines()
            if line.strip()
        ][:160]
        remotes = subprocess.run(
            ["git", "remote"],
            cwd=str(path), capture_output=True, text=True, timeout=5, check=False,
        )
        result["remotes"] = sorted({line.strip() for line in (remotes.stdout or "").splitlines() if line.strip()})
        result["status"] = "ready"
    except Exception as exc:
        result["message"] = f"{type(exc).__name__}: {exc}"
    return result


def _check_database_credentials(
    profile: str,
    environment: Mapping[str, str],
    *,
    credentials_file: str | Path | None = None,
    credential_profile: str = "",
    required: bool,
) -> dict[str, Any]:
    profile = str(profile or "").strip().lower()
    if not profile:
        return {
            "status": "skipped",
            "profile": "",
            "required_keys": [],
            "present_keys": [],
            "missing_keys": [],
            "connection_attempted": False,
        }
    source_profile = str(credential_profile or profile).strip().lower()
    required_keys = [f"pg_{source_profile}_readonly_{suffix}" for suffix in _DB_CREDENTIAL_SUFFIXES]
    file_path = Path(credentials_file or environment.get("HARNESS_CREDENTIALS_FILE") or "").expanduser()
    file_keys = _credential_file_keys(file_path)
    present = [
        key for key in required_keys
        if bool(environment.get(key) or environment.get(key.upper()) or key in file_keys)
    ]
    missing = [key for key in required_keys if key not in present]
    return {
        "status": "ready" if not missing else "failed" if required else "degraded",
        "profile": profile,
        "credential_profile": source_profile,
        "required_keys": required_keys,
        "present_keys": present,
        "missing_keys": missing,
        "credential_file": str(file_path) if file_path else "",
        "credential_file_present": file_path.is_file() if file_path else False,
        "connection_attempted": False,
    }


def _credential_file_keys(path: Path) -> set[str]:
    """Read only key names from the local credential envelope, never values."""

    if not path or not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, Mapping):
        return set()
    return {
        str(key)
        for key in payload
        if isinstance(key, str)
        and key.startswith("pg_")
        and "_readonly_" in key
        and isinstance(payload.get(key), str)
        and bool(payload.get(key))
    }


def _check_database_policy(
    profile: str,
    policy_path: str | Path | None,
) -> dict[str, Any]:
    """Verify the named profile without loading credentials or opening PostgreSQL."""

    profile = str(profile or "").strip().lower()
    if not profile:
        return {
            "status": "skipped",
            "profile": "",
            "credential_profile": "",
            "policy_path": "",
            "schema_authorization": "",
        }
    if policy_path is None:
        default_local = Path(__file__).resolve().parents[1] / "config" / "pg_evidence_profiles.local.json"
        policy = default_local if default_local.is_file() else default_local.with_name("pg_evidence_profiles.example.json")
    else:
        policy = Path(policy_path).expanduser()
    result: dict[str, Any] = {
        "status": "failed",
        "profile": profile,
        "credential_profile": profile,
        "policy_path": str(policy),
        "schema_authorization": "",
        "message": "",
    }
    try:
        payload = json.loads(policy.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result["message"] = f"policy_unavailable:{type(exc).__name__}"
        return result
    profiles = payload.get("profiles") if isinstance(payload, Mapping) else None
    profile_policy = profiles.get(profile) if isinstance(profiles, Mapping) else None
    if not isinstance(profile_policy, Mapping):
        result["message"] = "profile_not_configured"
        return result
    credential_profile = str(profile_policy.get("credential_profile") or profile).strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", credential_profile):
        result["message"] = "credential_profile_invalid"
        return result
    result["credential_profile"] = credential_profile
    schemas = profile_policy.get("schemas")
    if schemas == ["*"]:
        result["schema_authorization"] = "postgresql_account"
    elif isinstance(schemas, list):
        result["schema_authorization"] = "profile_schema_list"
    else:
        result["message"] = "schemas_not_configured"
        return result
    if payload.get("default_mode") != "off" or profile_policy.get("enabled") is not True:
        result["message"] = "profile_not_readonly_enabled"
        return result
    result["status"] = "ready"
    return result


def _recovery_actions(
    checks: Mapping[str, Any], *, errors: Sequence[str], warnings: Sequence[str]
) -> list[str]:
    actions: list[str] = []
    runtime = checks.get("runtime") or {}
    failed_runtime = set(runtime.get("failed_checks") or [])
    if failed_runtime.intersection({"output", "worktree"}):
        actions.append("内部输出/worktree目录失败时使用Harness私有临时目录重试")
    if "dependencies" in failed_runtime:
        actions.append("补齐Harness运行依赖后重试；当前不会把依赖缺失误判成业务代码失败")
    if checks.get("plugins", {}).get("errors"):
        actions.append("重新加载插件manifest并检查入口文件，不读取或打印凭证")
    if any(item.get("status") == "failed" for item in checks.get("repositories") or []):
        actions.append("只读确认仓库路径和Git可执行文件，不自动拉取或写远程")
    if checks.get("database_credentials", {}).get("missing_keys"):
        actions.append("补充本地只读凭证引用后再执行数据库smoke；当前不尝试连接")
    if checks.get("database_policy", {}).get("status") == "failed":
        actions.append("修复本地数据库只读策略后重试；schema 权限应由 PostgreSQL 账号或明确策略决定")
    if not actions and not errors and not warnings:
        actions.append("无需恢复动作")
    return actions


def format_doctor_report(report: Mapping[str, Any]) -> str:
    lines = [f"Harness 自动体检：{report.get('status') or 'unknown'}"]
    for name, check in (report.get("checks") or {}).items():
        if isinstance(check, Mapping):
            lines.append(f"- {name}: {check.get('status') or '-'}")
        elif isinstance(check, list):
            states = ", ".join(str(item.get("status") or "-") for item in check if isinstance(item, Mapping))
            lines.append(f"- {name}: {states or 'empty'}")
    for item in report.get("errors") or []:
        lines.append(f"- 错误：{item}")
    for item in report.get("warnings") or []:
        lines.append(f"- 警告：{item}")
    for item in report.get("recovery_actions") or []:
        lines.append(f"- 恢复：{item}")
    return "\n".join(lines)
