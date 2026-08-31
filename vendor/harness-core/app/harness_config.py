from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from app import database


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RULE_PACK_PATH = BASE_DIR / "config" / "rule_packs" / "dfhis.default.json"
DEFAULT_PROFILE_CONFIG_PATH = BASE_DIR / "config" / "profiles.example.json"
DEFAULT_CREDENTIALS_FILE = "/Users/lym/WorkCode/ai/apiKey/credentials.json"

REQUIRED_HARD_GUARDS = {
    "no_secret_printing": True,
    "external_writes_default": "off",
    "real_status_transition_requires_confirmation": True,
    "real_commit_push_requires_confirmation": True,
    "destructive_git_forbidden": True,
    "publish_forbidden_by_default": True,
}

DEFAULT_PROVIDER_TEMPLATE_TYPES = ["yunxiao", "tapd", "manual", "file"]

PROVIDER_LABELS = {
    "yunxiao": "云效只读需求来源",
    "tapd": "TAPD 只读需求来源",
    "manual": "手工粘贴需求来源",
    "file": "本地文件需求来源",
    "jira": "Jira 只读需求来源",
    "github_issue": "GitHub Issue 只读需求来源",
}

PROVIDER_DESCRIPTIONS = {
    "yunxiao": "预留云效 OpenAPI 只读读取模板；需要用户本机配置云效 PAT 和组织 ID。",
    "tapd": "预留 TAPD 只读读取模板；当前只描述本地配置草案，不测试 TAPD 连通性。",
    "manual": "用于手工粘贴需求正文，不需要任何远端凭证。",
    "file": "用于读取用户显式提供的本地需求文件，不扫描目录、不自动下载附件。",
    "jira": "预留 Jira 只读读取模板；当前仅作为扩展占位。",
    "github_issue": "预留 GitHub Issue 只读读取模板；当前仅作为扩展占位。",
}

SECRET_KEY_HINTS = ("token", "password", "passwd", "secret", "api_key", "apikey", "access_key", "private_key", "pat")
SECRET_REFERENCE_PATH_PARTS = {
    "credential_refs",
    "credential_keys",
    "env_keys",
    "file_keys",
    "keychain_services",
    "usage",
}


def build_config_summary(
    *,
    rule_pack_path: str | Path | None = None,
    profile_config_path: str | Path | None = None,
    profile_key: str = "",
    credentials_file: str | Path | None = None,
    check_keychain: bool = False,
) -> dict:
    rule_pack = load_rule_pack(rule_pack_path)
    profile = resolve_profile(profile_config_path=profile_config_path, profile_key=profile_key)
    credential_file_path = Path(credentials_file).expanduser() if credentials_file else credentials_file_path()
    validation = validate_rule_pack(rule_pack)
    credentials = build_credential_summary(
        rule_pack=rule_pack,
        profile=profile,
        credentials_file=credential_file_path,
        check_keychain=check_keychain,
    )
    return {
        "version": "0.22-harness-config-summary",
        "generated_at": database.now_iso(),
        "readonly": True,
        "rule_pack": summarize_rule_pack(rule_pack),
        "profile": summarize_profile(profile),
        "providers": summarize_providers(rule_pack=rule_pack, profile=profile),
        "credentials": credentials,
        "validation": validation,
        "compatibility": {
            "default_harness_behavior": "unchanged_without_explicit_config",
            "legacy_commands_require_new_args": False,
            "config_is_readonly_by_default": True,
            "external_writes_stay_disabled_by_default": True,
            "notes": [
                "v0.22 配置中心只读取 Rule Pack、Profile 和凭证状态摘要；不改变旧命令默认行为。",
                "未显式传入配置参数时，Task Manager、precommit、Yunxiao read 仍沿用原有路径。",
                "真实外部写入、commit/push、状态流转和发布仍需要单独显式确认。",
            ],
        },
        "residual_risk": "配置摘要只证明规则包结构、profile 引用和凭证存在状态；不验证云效/TAPD 真实网络连通性，不执行外部写入。",
    }


def load_rule_pack(path: str | Path | None = None) -> dict:
    target = Path(path).expanduser() if path else DEFAULT_RULE_PACK_PATH
    data = read_json_object(target)
    if not data.get("rule_pack_id"):
        raise ValueError(f"规则包缺少 rule_pack_id：{target}")
    return data


def resolve_profile(*, profile_config_path: str | Path | None = None, profile_key: str = "") -> dict:
    target = Path(profile_config_path).expanduser() if profile_config_path else DEFAULT_PROFILE_CONFIG_PATH
    data = read_json_object(target)
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError(f"profile 配置缺少 profiles 数组：{target}")
    selected_key = profile_key or str(data.get("default_profile") or "")
    if not selected_key and profiles:
        selected_key = str(profiles[0].get("key") or "")
    for profile in profiles:
        if isinstance(profile, dict) and profile.get("key") == selected_key:
            return profile
    raise ValueError(f"未找到 profile：{selected_key or '<empty>'}")


def read_json_object(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是合法 JSON：{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"配置文件根节点必须是对象：{path}")
    return data


def summarize_rule_pack(rule_pack: dict) -> dict:
    return {
        "rule_pack_id": rule_pack.get("rule_pack_id") or "",
        "version": rule_pack.get("version") or "",
        "display_name": rule_pack.get("display_name") or "",
        "hard_guards": dict(rule_pack.get("hard_guards") or {}),
        "git": summarize_rule_group(rule_pack.get("git")),
        "comments": summarize_rule_group(rule_pack.get("comments")),
        "status_flow": summarize_rule_group(rule_pack.get("status_flow")),
        "verification": summarize_rule_group(rule_pack.get("verification")),
        "risk": summarize_rule_group(rule_pack.get("risk")),
        "sharing": summarize_rule_group(rule_pack.get("sharing")),
    }


def summarize_rule_group(group: object) -> dict:
    if not isinstance(group, dict):
        return {}
    result = {}
    for key, value in group.items():
        if isinstance(value, dict):
            result[key] = sorted(value.keys())
        elif isinstance(value, list):
            result[key] = len(value)
        else:
            result[key] = value
    return result


def summarize_profile(profile: dict) -> dict:
    return {
        "key": profile.get("key") or "",
        "display_name": profile.get("display_name") or "",
        "rule_pack_id": profile.get("rule_pack_id") or "",
        "project_root": profile.get("project_root") or "",
        "output_root": profile.get("output_root") or "",
        "default_provider": ((profile.get("requirement_provider") or {}).get("type") or ""),
        "enabled_features": sorted(str(item) for item in profile.get("enabled_features") or []),
        "readonly": True,
    }


def summarize_providers(*, rule_pack: dict, profile: dict) -> dict:
    supported = rule_pack.get("providers") or {}
    requirement_provider = profile.get("requirement_provider") or {}
    return {
        "supported_requirement_sources": sorted(str(item) for item in supported.get("requirement_sources") or []),
        "active_requirement_source": requirement_provider.get("type") or "",
        "active_provider_name": requirement_provider.get("name") or "",
        "normalization_schema": supported.get("normalized_schema") or [],
        "readonly": True,
    }


def validate_rule_pack(rule_pack: dict) -> dict:
    issues: list[dict] = []
    hard_guards = rule_pack.get("hard_guards") or {}
    for key, expected in REQUIRED_HARD_GUARDS.items():
        actual = hard_guards.get(key)
        if actual != expected:
            issues.append(
                {
                    "severity": "error",
                    "code": f"hard_guard_{key}_invalid",
                    "message": f"硬保护 {key} 必须是 {expected!r}，当前是 {actual!r}。",
                }
            )
    return {
        "status": "pass" if not any(item.get("severity") == "error" for item in issues) else "failed",
        "issues": issues,
    }


def build_credential_summary(*, rule_pack: dict, profile: dict, credentials_file: Path, check_keychain: bool) -> dict:
    specs = collect_credential_specs(rule_pack=rule_pack, profile=profile)
    file_data = read_credentials_file(credentials_file)
    items = [
        summarize_credential(spec=spec, file_data=file_data, credentials_file=credentials_file, check_keychain=check_keychain)
        for spec in specs
    ]
    configured = [item for item in items if item.get("status") == "configured"]
    required_missing = [item for item in items if item.get("status") == "missing" and item.get("required")]
    return {
        "version": "0.22-credential-summary",
        "readonly": True,
        "credentials_file": str(credentials_file),
        "check_keychain": check_keychain,
        "configured_count": len(configured),
        "required_missing_count": len(required_missing),
        "items": items,
        "notes": [
            "只展示凭证是否存在、来源和脱敏尾号，不输出完整 key。",
            "规则包和 profile 不应保存真实 token；真实 token 应由每个用户本机环境、凭证文件或系统 Keychain 提供。",
        ],
    }


def collect_credential_specs(*, rule_pack: dict, profile: dict) -> list[dict]:
    specs: list[dict] = []
    for item in rule_pack.get("credential_refs") or []:
        if isinstance(item, dict):
            specs.append(dict(item))
    for item in profile.get("credential_refs") or []:
        if isinstance(item, dict):
            specs.append(dict(item))
    deduped: dict[str, dict] = {}
    for spec in specs:
        key = str(spec.get("key") or "").strip()
        if key and key not in deduped:
            deduped[key] = spec
    return list(deduped.values())


def summarize_credential(*, spec: dict, file_data: dict, credentials_file: Path, check_keychain: bool) -> dict:
    key = str(spec.get("key") or "")
    env_keys = [str(item) for item in spec.get("env_keys") or [key]]
    file_keys = [str(item) for item in spec.get("file_keys") or [key]]
    value = ""
    source = ""
    for env_key in env_keys:
        if os.environ.get(env_key):
            value = str(os.environ.get(env_key))
            source = f"env:{env_key}"
            break
    if not value:
        for file_key in file_keys:
            candidate = file_data.get(file_key)
            if isinstance(candidate, str) and candidate:
                value = candidate
                source = f"file:{credentials_file}:{file_key}"
                break
    if not value and check_keychain:
        for keychain_key in spec.get("keychain_services") or [key]:
            candidate = read_keychain_secret(str(keychain_key))
            if candidate:
                value = candidate
                source = f"keychain:{keychain_key}"
                break
    required = bool(spec.get("required", False))
    return {
        "key": key,
        "label": spec.get("label") or key,
        "required": required,
        "status": "configured" if value else ("missing" if required else "optional_missing"),
        "source": source,
        "masked_value": mask_secret(value) if value else "",
        "secret": bool(spec.get("secret", True)),
        "usage": spec.get("usage") or [],
    }


def read_credentials_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def credentials_file_path() -> Path:
    return Path(os.environ.get("HARNESS_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE).expanduser()


def read_keychain_secret(service: str) -> str:
    commands = [
        ["security", "find-generic-password", "-s", service, "-w"],
        ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", service, "-w"],
    ]
    for command in commands:
        try:
            completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    return ""


def mask_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * max(4, len(text) - 4) + text[-4:]


def config_summary_to_markdown(summary: dict) -> str:
    rule_pack = summary.get("rule_pack") or {}
    profile = summary.get("profile") or {}
    providers = summary.get("providers") or {}
    credentials = summary.get("credentials") or {}
    validation = summary.get("validation") or {}
    hard_guards = rule_pack.get("hard_guards") or {}
    lines = [
        "# Harness 配置中心摘要",
        "",
        f"- 版本：{summary.get('version')}",
        f"- 只读：{summary.get('readonly')}",
        f"- 生成时间：{summary.get('generated_at')}",
        "",
        "## Rule Pack",
        "",
        f"- ID：{rule_pack.get('rule_pack_id') or '-'}",
        f"- 名称：{rule_pack.get('display_name') or '-'}",
        f"- 版本：{rule_pack.get('version') or '-'}",
        f"- 外部写入默认：{hard_guards.get('external_writes_default') or '-'}",
        f"- 禁止打印密钥：{hard_guards.get('no_secret_printing')}",
        f"- 状态流转需确认：{hard_guards.get('real_status_transition_requires_confirmation')}",
        "",
        "## Profile",
        "",
        f"- Key：{profile.get('key') or '-'}",
        f"- 名称：{profile.get('display_name') or '-'}",
        f"- Rule Pack：{profile.get('rule_pack_id') or '-'}",
        f"- 默认需求来源：{providers.get('active_requirement_source') or '-'}",
        f"- 支持来源：{', '.join(providers.get('supported_requirement_sources') or []) or '-'}",
        "",
        "## Credential Store",
        "",
        f"- 凭证文件：{credentials.get('credentials_file') or '-'}",
        f"- 已配置：{credentials.get('configured_count', 0)}",
        f"- 必填缺失：{credentials.get('required_missing_count', 0)}",
    ]
    for item in credentials.get("items") or []:
        lines.append(
            f"- {item.get('key')}: {item.get('status')}，来源：{item.get('source') or '-'}，尾号：{item.get('masked_value') or '-'}"
        )
    lines.extend(["", "## Validation", "", f"- 状态：{validation.get('status') or '-'}"])
    issues = validation.get("issues") or []
    if issues:
        for item in issues:
            lines.append(f"- {item.get('severity')} {item.get('code')}：{item.get('message')}")
    else:
        lines.append("- 未发现硬保护配置错误。")
    lines.extend(["", "## Compatibility", ""])
    for note in (summary.get("compatibility") or {}).get("notes") or []:
        lines.append(f"- {note}")
    lines.extend(["", "## Residual Risk", "", f"- {summary.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def write_config_summary_outputs(*, output_dir: str | Path, summary: dict) -> dict:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "harness_config_summary.json"
    markdown_path = target_dir / "harness_config_summary.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(config_summary_to_markdown(summary), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def build_configuration_preview(summary: dict) -> dict:
    rule_pack = summary.get("rule_pack") or {}
    profile = summary.get("profile") or {}
    providers = summary.get("providers") or {}
    hard_guards = rule_pack.get("hard_guards") or {}
    provider_types = unique_keep_order(
        [
            *(providers.get("supported_requirement_sources") or []),
            providers.get("active_requirement_source") or "",
            *DEFAULT_PROVIDER_TEMPLATE_TYPES,
        ]
    )
    provider_templates = [
        build_provider_template(source_type=str(source_type), summary=summary)
        for source_type in provider_types
        if str(source_type).strip()
    ]
    return {
        "version": "0.25-configuration-preview",
        "generated_at": database.now_iso(),
        "readonly": True,
        "external_writes_enabled": False,
        "credential_values_exposed": False,
        "remote_connection_tests_enabled": False,
        "rule_pack_id": rule_pack.get("rule_pack_id") or "",
        "profile_key": profile.get("key") or "",
        "active_requirement_source": providers.get("active_requirement_source") or "",
        "provider_templates": provider_templates,
        "workflow_rules": build_workflow_rule_preview(rule_pack=rule_pack),
        "share_profile_template": build_share_profile_template(summary),
        "export_templates": build_export_templates(),
        "warnings": build_configuration_preview_warnings(summary=summary, provider_templates=provider_templates),
        "compatibility": {
            "default_harness_behavior": "unchanged_without_explicit_config_preview",
            "requires_explicit_cli_flag": True,
            "config_preview_is_readonly": True,
            "no_remote_fetch": True,
            "no_external_write": True,
            "notes": [
                "v0.25 只生成本地配置预览和 provider 接入模板草案；不会读取远端、不会保存真实 token。",
                "模板用于团队分享时说明每个人需要配置哪些 key，真实凭证仍由各自本机 env、credentials file 或 Keychain 提供。",
                "提交规范、评论模板、状态流转和验证要求只展示规则摘要，不自动应用到 Git、云效、TAPD 或发布流程。",
            ],
        },
        "hard_guards": {
            "external_writes_default": hard_guards.get("external_writes_default") or "off",
            "no_secret_printing": hard_guards.get("no_secret_printing") is True,
            "real_status_transition_requires_confirmation": hard_guards.get("real_status_transition_requires_confirmation") is True,
            "real_commit_push_requires_confirmation": hard_guards.get("real_commit_push_requires_confirmation") is True,
            "publish_forbidden_by_default": hard_guards.get("publish_forbidden_by_default") is True,
        },
        "residual_risk": "配置预览只说明本地规则和 provider 模板草案；不验证云效/TAPD/Jira/GitHub 网络连通性，不执行需求读取、评论、状态流转、commit、push、回滚或发布。",
    }


def build_provider_template(*, source_type: str, summary: dict) -> dict:
    source = source_type.strip()
    credential_items = [
        item
        for item in (summary.get("credentials") or {}).get("items") or []
        if isinstance(item, dict) and credential_matches_provider(item, source)
    ]
    credential_refs = [
        {
            "key": item.get("key") or "",
            "label": item.get("label") or item.get("key") or "",
            "required": bool(item.get("required")),
            "status": item.get("status") or "",
            "usage": item.get("usage") or [],
        }
        for item in credential_items
    ]
    return {
        "source_type": source,
        "label": PROVIDER_LABELS.get(source, f"{source} 只读需求来源"),
        "mode": "local_draft",
        "readonly": True,
        "remote_read_enabled": False,
        "external_write_enabled": False,
        "credential_values_exposed": False,
        "credential_keys": [item.get("key") or "" for item in credential_refs if item.get("key")],
        "credential_refs": credential_refs,
        "template_status": "configured" if credential_refs and all(item.get("status") == "configured" or not item.get("required") for item in credential_refs) else ("no_credentials_required" if not credential_refs else "needs_local_credentials"),
        "input_modes": provider_input_modes(source),
        "output_schema": (summary.get("providers") or {}).get("normalization_schema") or [],
        "description": PROVIDER_DESCRIPTIONS.get(source, f"预留 {source} 只读需求来源模板；当前仅作为本地配置草案。"),
        "notes": [
            "不会读取远端。",
            "不会写评论、改状态、改负责人、上传附件或关闭任务。",
            "不会保存、导出或显示真实 token 值。",
        ],
    }


def credential_matches_provider(item: dict, source_type: str) -> bool:
    key = str(item.get("key") or "").lower()
    usage_text = " ".join(str(value).lower() for value in item.get("usage") or [])
    source = source_type.lower()
    if source == "yunxiao":
        return "yunxiao" in usage_text or key.startswith("aliyun_devops")
    if source == "tapd":
        return "tapd" in usage_text or "tapd" in key
    if source == "github_issue":
        return "github" in usage_text or "github" in key
    if source == "jira":
        return "jira" in usage_text or "jira" in key
    return False


def provider_input_modes(source_type: str) -> list[str]:
    if source_type == "manual":
        return ["inline_title", "inline_description"]
    if source_type == "file":
        return ["local_json_file", "local_text_file"]
    return ["local_payload_file", "future_readonly_api_adapter"]


def build_workflow_rule_preview(*, rule_pack: dict) -> dict:
    git = rule_pack.get("git") or {}
    comments = rule_pack.get("comments") or {}
    status_flow = rule_pack.get("status_flow") or {}
    verification = rule_pack.get("verification") or {}
    risk = rule_pack.get("risk") or {}
    sharing = rule_pack.get("sharing") or {}
    return {
        "git_commit_convention": {
            "branch_name_templates": git.get("branch_name") or [],
            "commit_message_templates": git.get("commit_message") or [],
            "permissions": git.get("permissions") or [],
            "required_before_commit": git.get("required_before_commit") or 0,
        },
        "comment_template": {
            "delivery_template": comments.get("delivery_template") or "",
            "include_fields": comments.get("include") or [],
            "available_templates": comments.get("templates") or [],
        },
        "status_flow": {
            "real_transition_enabled": status_flow.get("real_transition_enabled") is True,
            "dry_run_enabled": status_flow.get("dry_run_enabled") is True,
            "allowed_transition_states": status_flow.get("allowed_transitions") or [],
            "blocked_real_action_count": status_flow.get("blocked_real_actions") or 0,
            "human_confirm_action_count": status_flow.get("require_human_confirm_for") or 0,
        },
        "verification": {
            "required_check_count": verification.get("required_checks") or 0,
            "ui_change_requires": verification.get("ui_change_requires") or 0,
            "interaction_change_requires": verification.get("interaction_change_requires") or 0,
            "blocking_warning_count": verification.get("blocking_warning_codes") or 0,
            "readonly_forbidden_marker_count": verification.get("readonly_workspace_forbidden_markers") or 0,
        },
        "risk": {
            "high_risk_keyword_count": risk.get("high_risk_keywords") or 0,
            "high_risk_requires": risk.get("high_risk_requires") or 0,
            "auto_flow_blocked_for_high_risk": risk.get("auto_flow_blocked_for_high_risk") is True,
        },
        "sharing": {
            "portable": sharing.get("portable") is True,
            "secret_free": sharing.get("secret_free") is True,
            "do_not_export_credentials": sharing.get("do_not_export_credentials") is True,
            "recommended_secret_sources": sharing.get("recommended_secret_sources") or 0,
        },
    }


def build_share_profile_template(summary: dict) -> dict:
    profile = summary.get("profile") or {}
    rule_pack = summary.get("rule_pack") or {}
    providers = summary.get("providers") or {}
    return {
        "key": f"{profile.get('key') or 'local'}-copy",
        "display_name": f"{profile.get('display_name') or '本地配置'} 副本",
        "rule_pack_id": rule_pack.get("rule_pack_id") or "",
        "project_root": "<本机项目根目录>",
        "output_root": "<本机 Harness 输出目录>",
        "requirement_provider": {
            "type": providers.get("active_requirement_source") or "manual",
            "credential_keys": [
                key
                for item in (summary.get("credentials") or {}).get("items") or []
                for key in [str(item.get("key") or "")]
                if key and any(str(usage).startswith((providers.get("active_requirement_source") or "")) for usage in item.get("usage") or [])
            ],
            "readonly": True,
        },
        "notes": "分享给他人时复制该模板后改本机路径和凭证 key 引用；不要写入真实 token 值。",
    }


def build_export_templates() -> dict:
    return {
        "summary_json": "harness_config_summary.json",
        "summary_markdown": "harness_config_summary.md",
        "preview_json": "harness_config_preview.json",
        "preview_markdown": "harness_config_preview.md",
        "workspace_preview_json": "task_workspace_config_preview.json",
        "workspace_preview_markdown": "task_workspace_config_preview.md",
        "commands": [
            "python3 tools/config_check.py --profile-key <profile-key> --include-preview --output-dir /tmp/his_harness_config_check",
            "python3 tools/task_manager.py workspace --include-config-summary --include-config-preview --profile-key <profile-key> --output-dir /tmp/his_harness_task_workspace_configured",
        ],
    }


def build_configuration_preview_warnings(*, summary: dict, provider_templates: list[dict]) -> list[dict]:
    warnings: list[dict] = []
    credentials = summary.get("credentials") or {}
    if int(credentials.get("required_missing_count") or 0) > 0:
        warnings.append(
            {
                "severity": "warning",
                "code": "required_credentials_missing_for_local_user",
                "message": "存在必填凭证缺失；这只影响该用户本机真实读取能力，不阻断配置模板导出。",
            }
        )
    for item in provider_templates:
        if item.get("template_status") == "needs_local_credentials":
            warnings.append(
                {
                    "severity": "info",
                    "code": f"{item.get('source_type')}_needs_local_credentials",
                    "message": f"{item.get('label') or item.get('source_type')} 需要本机凭证 key；模板不会包含真实凭证值。",
                }
            )
    return warnings


def build_configuration_share_validation(
    *,
    summary: dict,
    rule_pack_path: str | Path | None = None,
    profile_config_path: str | Path | None = None,
) -> dict:
    issues: list[dict] = []
    rule_pack = load_json_for_validation(rule_pack_path or DEFAULT_RULE_PACK_PATH, label="rule_pack", issues=issues)
    profile_config = load_json_for_validation(profile_config_path or DEFAULT_PROFILE_CONFIG_PATH, label="profile_config", issues=issues)
    issues.extend(validate_share_hard_guards(rule_pack=rule_pack, summary=summary))
    issues.extend(validate_share_secret_leaks({"rule_pack": rule_pack, "profile_config": profile_config}))
    issues.extend(validate_share_profile_paths(profile_config=profile_config))
    status = "failed" if any(item.get("severity") == "error" for item in issues) else "pass"
    return {
        "version": "0.26-configuration-share-validation",
        "generated_at": database.now_iso(),
        "readonly": True,
        "will_apply_configuration": False,
        "will_write_local_files": False,
        "external_writes_enabled": False,
        "remote_connection_tests_enabled": False,
        "status": status,
        "rule_pack_id": (summary.get("rule_pack") or {}).get("rule_pack_id") or rule_pack.get("rule_pack_id") or "",
        "profile_key": (summary.get("profile") or {}).get("key") or "",
        "input_files": {
            "rule_pack": str(Path(rule_pack_path).expanduser()) if rule_pack_path else str(DEFAULT_RULE_PACK_PATH),
            "profile_config": str(Path(profile_config_path).expanduser()) if profile_config_path else str(DEFAULT_PROFILE_CONFIG_PATH),
        },
        "local_override_strategy": build_local_override_strategy(summary=summary),
        "share_package_rules": {
            "secret_values_forbidden": True,
            "personal_absolute_paths_warn": True,
            "external_writes_must_remain_off": True,
            "real_status_transition_must_remain_off": True,
            "git_auto_commit_push_merge_must_remain_off": True,
            "profile_overrides_require_explicit_cli_arg": True,
        },
        "issues": issues,
        "export_templates": {
            "validation_json": "harness_config_share_validation.json",
            "validation_markdown": "harness_config_share_validation.md",
            "workspace_validation_json": "task_workspace_config_share_validation.json",
            "workspace_validation_markdown": "task_workspace_config_share_validation.md",
            "commands": [
                "python3 tools/config_check.py --profile-key <profile-key> --include-share-validation --output-dir /tmp/his_harness_config_share_validation",
                "python3 tools/task_manager.py workspace --include-config-share-validation --profile-key <profile-key> --output-dir /tmp/his_harness_task_workspace_configured_share",
            ],
        },
        "residual_risk": "配置分享校验只检查本地 JSON 模板结构和明显风险标记；不会应用配置、不会写入 ~/.his-harness、不会验证远端账号权限或真实项目路径是否存在。",
    }


def load_json_for_validation(path: str | Path, *, label: str, issues: list[dict]) -> dict:
    target = Path(path).expanduser()
    try:
        return read_json_object(target)
    except Exception as exc:
        issues.append(
            {
                "severity": "error",
                "code": f"{label}_read_failed",
                "message": f"{label} 读取失败：{target}: {exc}",
            }
        )
        return {}


def validate_share_hard_guards(*, rule_pack: dict, summary: dict) -> list[dict]:
    issues: list[dict] = []
    hard_guards = rule_pack.get("hard_guards") or (summary.get("rule_pack") or {}).get("hard_guards") or {}
    if hard_guards.get("external_writes_default") != "off":
        issues.append(
            {
                "severity": "error",
                "code": "external_writes_default_not_off",
                "message": "团队分享规则包必须保持 external_writes_default=off。",
            }
        )
    for key in ["no_secret_printing", "real_status_transition_requires_confirmation", "real_commit_push_requires_confirmation", "destructive_git_forbidden", "publish_forbidden_by_default"]:
        if hard_guards.get(key) is not True:
            issues.append(
                {
                    "severity": "error",
                    "code": f"hard_guard_{key}_not_enabled",
                    "message": f"团队分享规则包必须启用硬保护 {key}。",
                }
            )
    permissions = (rule_pack.get("git") or {}).get("permissions") or {}
    for key in ["auto_create_branch", "auto_commit", "auto_push", "auto_merge"]:
        if permissions.get(key) is True:
            issues.append(
                {
                    "severity": "error",
                    "code": f"git_permission_{key}_enabled",
                    "message": f"团队分享规则包不能默认开启 Git 动作：{key}。",
                }
            )
    status_flow = rule_pack.get("status_flow") or {}
    if status_flow.get("real_transition_enabled") is True:
        issues.append(
            {
                "severity": "error",
                "code": "real_status_transition_enabled",
                "message": "团队分享规则包不能默认开启真实状态流转。",
            }
        )
    sharing = rule_pack.get("sharing") or {}
    if sharing.get("do_not_export_credentials") is not True:
        issues.append(
            {
                "severity": "error",
                "code": "sharing_credentials_export_not_forbidden",
                "message": "团队分享规则包必须声明 do_not_export_credentials=true。",
            }
        )
    return issues


def validate_share_secret_leaks(data: object, path: list[str] | None = None) -> list[dict]:
    current_path = path or []
    issues: list[dict] = []
    if isinstance(data, dict):
        for key, value in data.items():
            key_text = str(key)
            next_path = [*current_path, key_text]
            if is_secret_reference_path(next_path):
                continue
            if isinstance(value, str) and key_contains_secret_hint(key_text) and looks_like_real_secret(value):
                issues.append(
                    {
                        "severity": "error",
                        "code": "possible_secret_value_in_template",
                        "path": ".".join(next_path),
                        "message": f"配置模板疑似包含真实密钥字段：{'.'.join(next_path)}。",
                    }
                )
                continue
            issues.extend(validate_share_secret_leaks(value, next_path))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            issues.extend(validate_share_secret_leaks(value, [*current_path, str(index)]))
    return issues


def is_secret_reference_path(path: list[str]) -> bool:
    return any(part in SECRET_REFERENCE_PATH_PARTS for part in path)


def key_contains_secret_hint(key: str) -> bool:
    lower = key.lower().replace("-", "_")
    return any(hint in lower for hint in SECRET_KEY_HINTS)


def looks_like_real_secret(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower in {"true", "false", "readonly", "secret"}:
        return False
    if any(marker in lower for marker in ["<", ">", "placeholder", "example", "your_", "xxx", "dummy", "sample"]):
        return False
    if len(text) < 12:
        return False
    return any(ch.isdigit() for ch in text) or "-" in text or "_" in text or len(text) >= 20


def validate_share_profile_paths(*, profile_config: dict) -> list[dict]:
    issues: list[dict] = []
    for profile in profile_config.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        profile_key = profile.get("key") or "<unknown>"
        for field in ["project_root", "output_root"]:
            value = str(profile.get(field) or "").strip()
            if not value:
                continue
            if is_personal_absolute_path(value):
                issues.append(
                    {
                        "severity": "warning",
                        "code": "personal_absolute_path_in_shared_profile",
                        "path": f"profiles.{profile_key}.{field}",
                        "message": f"共享 profile 中包含个人绝对路径，建议改为占位符或由本机覆盖：{field}。",
                    }
                )
    return issues


def is_personal_absolute_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/absolute/path") or normalized.startswith("/tmp/"):
        return False
    return normalized.startswith("/Users/") or normalized.startswith("/home/") or normalized.lower().startswith("c:/users/")


def build_local_override_strategy(*, summary: dict) -> dict:
    profile = summary.get("profile") or {}
    return {
        "version": "0.26-local-override-strategy",
        "readonly": True,
        "current_profile_key": profile.get("key") or "",
        "current_project_root": profile.get("project_root") or "",
        "current_output_root": profile.get("output_root") or "",
        "will_apply_configuration": False,
        "precedence": [
            {
                "kind": "cli_args",
                "path": "--rule-pack / --profile-config / --profile-key / --credentials-file",
                "status": "supported_now",
                "note": "当前唯一会被配置命令实际读取的覆盖方式是显式 CLI 参数。",
            },
            {
                "kind": "local_profile_file",
                "path": "~/.his-harness/profiles.json",
                "status": "recommended_manual_arg",
                "note": "团队成员可复制 profile 模板到该路径，再通过 --profile-config 显式传入；Harness 不会自动写入该文件。",
            },
            {
                "kind": "local_rule_pack_file",
                "path": "~/.his-harness/rule_packs/<rule-pack-id>.json",
                "status": "recommended_manual_arg",
                "note": "团队成员可复制规则包到本机目录，再通过 --rule-pack 显式传入。",
            },
            {
                "kind": "repo_example",
                "path": "config/profiles.example.json / config/rule_packs/dfhis.default.json",
                "status": "fallback_default",
                "note": "未传显式参数时仍读取仓库示例配置，保持旧行为稳定。",
            },
        ],
        "secret_sources": [
            "env",
            "local_credentials_file",
            "os_keychain",
        ],
        "notes": [
            "本策略只做展示和校验，不会应用配置。",
            "分享给他人时只分享模板和 key 名称，不分享真实 token。",
            "需要记住 key 时优先使用本机凭证文件或系统 Keychain，不把 key 写入 profile。",
        ],
    }


def configuration_share_validation_to_markdown(validation: dict) -> str:
    lines = [
        "# Harness 团队分享包校验",
        "",
        f"- 版本：{validation.get('version')}",
        f"- 状态：{validation.get('status')}",
        f"- 只读：{validation.get('readonly')}",
        f"- 会应用配置：{validation.get('will_apply_configuration')}",
        f"- 外部写入：{validation.get('external_writes_enabled')}",
        f"- Rule Pack：{validation.get('rule_pack_id') or '-'}",
        f"- Profile：{validation.get('profile_key') or '-'}",
        "",
        "## 本地覆盖策略",
        "",
        "以下策略只是建议和校验说明，不会应用配置、不会写入本机文件。",
    ]
    for item in (validation.get("local_override_strategy") or {}).get("precedence") or []:
        lines.append(
            f"- {item.get('kind') or '-'}：`{item.get('path') or '-'}`，状态：{item.get('status') or '-'}，说明：{item.get('note') or '-'}"
        )
    lines.extend(["", "## 分享规则", ""])
    for key, value in (validation.get("share_package_rules") or {}).items():
        lines.append(f"- {key}：{value}")
    issues = validation.get("issues") or []
    lines.extend(["", "## Issues", ""])
    if issues:
        for item in issues:
            path = item.get("path") or "-"
            lines.append(f"- [{item.get('severity') or '-'}] {item.get('code') or '-'} `{path}`：{item.get('message') or '-'}")
    else:
        lines.append("- 未发现阻断项。")
    lines.extend(["", "## 建议命令", ""])
    for command in (validation.get("export_templates") or {}).get("commands") or []:
        lines.append(f"- `{command}`")
    lines.extend(["", "## Residual Risk", "", f"- {validation.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def write_configuration_share_validation_outputs(*, output_dir: str | Path, validation: dict) -> dict:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "harness_config_share_validation.json"
    markdown_path = target_dir / "harness_config_share_validation.md"
    json_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(configuration_share_validation_to_markdown(validation), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def configuration_preview_to_markdown(preview: dict) -> str:
    lines = [
        "# Harness 配置预览",
        "",
        f"- 版本：{preview.get('version')}",
        f"- 只读：{preview.get('readonly')}",
        f"- 外部写入：{preview.get('external_writes_enabled')}",
        f"- 显示凭证值：{preview.get('credential_values_exposed')}",
        f"- 远端连通测试：{preview.get('remote_connection_tests_enabled')}",
        f"- Rule Pack：{preview.get('rule_pack_id') or '-'}",
        f"- Profile：{preview.get('profile_key') or '-'}",
        "",
        "## Provider 模板",
        "",
        "这些模板是本地草案，不会读取远端，不会保存真实 token，不会执行外部写入。",
    ]
    for item in preview.get("provider_templates") or []:
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                "",
                f"### {item.get('label') or item.get('source_type')}",
                "",
                f"- 类型：{item.get('source_type') or '-'}",
                f"- 模式：{item.get('mode') or '-'}",
                f"- 状态：{item.get('template_status') or '-'}",
                f"- 远端读取：{item.get('remote_read_enabled')}",
                f"- 外部写入：{item.get('external_write_enabled')}",
                f"- 凭证 key：{', '.join(item.get('credential_keys') or []) or '无'}",
                f"- 输入方式：{', '.join(item.get('input_modes') or []) or '-'}",
                f"- 说明：{item.get('description') or '-'}",
            ]
        )
    workflow_rules = preview.get("workflow_rules") or {}
    lines.extend(
        [
            "",
            "## 规则预览",
            "",
            f"- Git/提交规范：{', '.join((workflow_rules.get('git_commit_convention') or {}).get('commit_message_templates') or []) or '-'}",
            f"- 需求评论模板：{(workflow_rules.get('comment_template') or {}).get('delivery_template') or '-'}",
            f"- 状态流转真实执行：{(workflow_rules.get('status_flow') or {}).get('real_transition_enabled')}",
            f"- 状态流转 dry-run：{(workflow_rules.get('status_flow') or {}).get('dry_run_enabled')}",
            f"- 高风险自动流程阻断：{(workflow_rules.get('risk') or {}).get('auto_flow_blocked_for_high_risk')}",
            "",
            "## 导出模板",
            "",
        ]
    )
    export_templates = preview.get("export_templates") or {}
    for key in ["summary_json", "summary_markdown", "preview_json", "preview_markdown", "workspace_preview_json", "workspace_preview_markdown"]:
        lines.append(f"- {key}：{export_templates.get(key) or '-'}")
    lines.extend(["", "## 建议命令", ""])
    for command in export_templates.get("commands") or []:
        lines.append(f"- `{command}`")
    warnings = preview.get("warnings") or []
    lines.extend(["", "## Warning", ""])
    if warnings:
        for warning in warnings:
            lines.append(f"- [{warning.get('severity') or 'warning'}] {warning.get('code') or '-'}：{warning.get('message') or '-'}")
    else:
        lines.append("- 暂无")
    lines.extend(["", "## Residual Risk", "", f"- {preview.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def write_configuration_preview_outputs(*, output_dir: str | Path, preview: dict) -> dict:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "harness_config_preview.json"
    markdown_path = target_dir / "harness_config_preview.md"
    json_path.write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(configuration_preview_to_markdown(preview), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def build_configuration_import_draft(
    *,
    summary: dict,
    rule_pack_path: str | Path | None = None,
    profile_config_path: str | Path | None = None,
    draft_output_dir: str | Path | None = None,
    overwrite: bool = False,
) -> dict:
    target_dir = Path(draft_output_dir).expanduser().resolve() if draft_output_dir else None
    rule_pack = load_rule_pack(rule_pack_path)
    profile_config = load_profile_config_for_draft(profile_config_path)
    rule_pack_id = (summary.get("rule_pack") or {}).get("rule_pack_id") or rule_pack.get("rule_pack_id") or "dfhis-default"
    profile = summary.get("profile") or {}
    provider = summary.get("providers") or {}
    profiles_draft = build_profiles_import_draft(
        summary=summary,
        profile_config=profile_config,
        rule_pack_id=rule_pack_id,
    )
    rule_pack_draft = build_rule_pack_import_draft(rule_pack)
    credentials_example = build_credentials_import_example(summary=summary)
    file_names = {
        "profiles_draft": "profiles.draft.json",
        "rule_pack_draft": "rule_pack.draft.json",
        "credentials_example": "credentials.example.json",
        "import_guide": "IMPORT_GUIDE.md",
        "manifest": "config_import_manifest.json",
    }
    planned_files = [
        {
            "kind": kind,
            "file_name": file_name,
            "path": str(target_dir / file_name) if target_dir else "",
            "will_create": bool(target_dir),
            "overwrite_existing": bool(overwrite),
        }
        for kind, file_name in file_names.items()
    ]
    draft = {
        "version": "0.27-configuration-import-draft",
        "generated_at": database.now_iso(),
        "readonly": True,
        "will_write_draft_files": True,
        "will_apply_configuration": False,
        "will_write_real_config_dir": False,
        "writes_only_to_user_selected_dir": True,
        "overwrite_existing_files": bool(overwrite),
        "draft_output_dir": str(target_dir) if target_dir else "",
        "rule_pack_id": rule_pack_id,
        "profile_key": profile.get("key") or "",
        "active_requirement_source": provider.get("active_requirement_source") or "",
        "files": planned_files,
        "draft_payloads": {
            "profiles_draft": profiles_draft,
            "rule_pack_draft": rule_pack_draft,
            "credentials_example": credentials_example,
        },
        "manual_steps": [
            "先在用户选择目录检查 profiles.draft.json、rule_pack.draft.json、credentials.example.json 和 IMPORT_GUIDE.md。",
            "确认 profile 中的本机项目路径、输出目录、需求来源 provider 和凭证 key 名称符合自己的环境。",
            "如果需要长期保存，再由人工复制到 ~/.his-harness 或其他个人配置目录，并在命令中显式传 --profile-config / --rule-pack。",
            "真实 token 只放在个人 env、本机凭证文件或系统 Keychain；不要写进 profiles.draft.json 或 rule_pack.draft.json。",
            "复制后先运行 tools/config_check.py 做只读校验，不要直接开放云效/TAPD 写动作。",
        ],
        "copy_commands": build_configuration_import_copy_commands(target_dir=target_dir),
        "compatibility": {
            "requires_explicit_cli_flag": True,
            "requires_user_selected_output_dir": True,
            "default_harness_behavior": "unchanged_without_explicit_import_draft",
            "no_external_write": True,
            "no_remote_fetch": True,
            "no_real_config_write": True,
            "notes": [
                "v0.27 只生成配置导入草案文件，不会应用配置。",
                "默认不覆盖用户选择目录下的同名草案文件；需要覆盖时必须显式传 --overwrite-drafts。",
                "生成内容只包含规则、profile 模板和凭证 key 名称，不包含真实 token。",
            ],
        },
        "residual_risk": "配置导入草案只证明可生成 secret-free 示例文件；是否复制到个人配置目录、路径是否真实存在、远端账号是否可读，仍需要用户人工确认和只读校验。",
    }
    draft["draft_payloads"]["import_guide_markdown"] = configuration_import_draft_to_markdown(draft)
    return draft


def load_profile_config_for_draft(path: str | Path | None = None) -> dict:
    target = Path(path).expanduser() if path else DEFAULT_PROFILE_CONFIG_PATH
    return read_json_object(target)


def build_profiles_import_draft(*, summary: dict, profile_config: dict, rule_pack_id: str) -> dict:
    profile = summary.get("profile") or {}
    providers = summary.get("providers") or {}
    source_profile_key = profile.get("key") or profile_config.get("default_profile") or "local-draft"
    provider_type = providers.get("active_requirement_source") or profile.get("default_provider") or "manual"
    return {
        "version": "0.27-draft",
        "purpose": "HIS Harness 个人配置导入草案。该文件不包含真实 token，只保存本机路径、规则包引用和凭证 key 名称。",
        "default_profile": source_profile_key,
        "profiles": [
            {
                "key": source_profile_key,
                "display_name": profile.get("display_name") or "本地开发草案",
                "rule_pack_id": rule_pack_id,
                "project_root": "<本机项目根目录>",
                "output_root": "<本机 Harness 输出目录>",
                "requirement_provider": {
                    "type": provider_type,
                    "name": PROVIDER_LABELS.get(provider_type, f"{provider_type} 只读需求来源"),
                    "credential_keys": provider_credential_keys(summary=summary, provider_type=provider_type),
                    "readonly": True,
                },
                "enabled_features": sorted(set((profile.get("enabled_features") or []) + ["config_summary", "config_import_draft"])),
                "notes": "按本机环境修改路径和 provider；不要把真实 token 写入 profile。",
            }
        ],
        "source": {
            "source_profile_key": source_profile_key,
            "generated_from_summary_version": summary.get("version") or "",
            "secret_values_included": False,
        },
    }


def provider_credential_keys(*, summary: dict, provider_type: str) -> list[str]:
    keys = []
    for item in (summary.get("credentials") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        if credential_matches_provider(item, provider_type):
            key = str(item.get("key") or "")
            if key:
                keys.append(key)
    return unique_keep_order(keys)


def build_rule_pack_import_draft(rule_pack: dict) -> dict:
    draft = json.loads(json.dumps(rule_pack, ensure_ascii=False))
    draft["draft_generated_by"] = "his-harness-v0.27-configuration-import-draft"
    draft["draft_notes"] = [
        "该规则包草案不包含真实凭证值。",
        "团队分享前仍需运行 --include-share-validation 做只读校验。",
        "外部写入、真实状态流转、commit/push 和发布默认必须保持关闭。",
    ]
    return draft


def build_credentials_import_example(*, summary: dict) -> dict:
    items = []
    values = {}
    for item in (summary.get("credentials") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if not key:
            continue
        values[key] = ""
        items.append(
            {
                "key": key,
                "label": item.get("label") or key,
                "required": bool(item.get("required")),
                "secret": bool(item.get("secret", True)),
                "usage": item.get("usage") or [],
                "value": "<在本机填写，不要提交>" if item.get("secret", True) else "<本机配置值>",
            }
        )
    return {
        "version": "0.27-credentials-example",
        "purpose": "本文件只是凭证格式示例；不要提交真实 token，不要分享真实值。",
        "secret_values_included": False,
        "recommended_secret_sources": ["env", "local_credentials_file", "os_keychain"],
        "values": values,
        "items": items,
    }


def build_configuration_import_copy_commands(*, target_dir: Path | None) -> list[str]:
    draft_dir = str(target_dir) if target_dir else "<用户选择目录>"
    return [
        "mkdir -p ~/.his-harness/rule_packs",
        f"cp {draft_dir}/profiles.draft.json ~/.his-harness/profiles.json",
        f"cp {draft_dir}/rule_pack.draft.json ~/.his-harness/rule_packs/dfhis-default.json",
        "python3 tools/config_check.py --profile-config ~/.his-harness/profiles.json --rule-pack ~/.his-harness/rule_packs/dfhis-default.json --include-share-validation --output-dir /tmp/his_harness_config_import_check",
    ]


def configuration_import_draft_to_markdown(draft: dict) -> str:
    lines = [
        "# Harness 配置导入草案",
        "",
        f"- 版本：{draft.get('version')}",
        f"- 只读：{draft.get('readonly')}",
        f"- 用户选择目录：`{draft.get('draft_output_dir') or '<未指定>'}`",
        f"- 会应用配置：{draft.get('will_apply_configuration')}",
        f"- 写真实配置目录：{draft.get('will_write_real_config_dir')}",
        f"- 默认覆盖同名文件：{draft.get('overwrite_existing_files')}",
        "",
        "本草案只会在用户选择目录生成文件，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端连接。",
        "",
        "## 生成文件",
        "",
    ]
    for item in draft.get("files") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {item.get('kind') or '-'}：`{item.get('path') or item.get('file_name') or '-'}`，覆盖：{item.get('overwrite_existing')}"
        )
    lines.extend(["", "## 人工导入步骤", ""])
    for index, step in enumerate(draft.get("manual_steps") or [], start=1):
        lines.append(f"{index}. {step}")
    lines.extend(["", "## 可复制命令", ""])
    for command in draft.get("copy_commands") or []:
        lines.append(f"- `{command}`")
    lines.extend(["", "## 兼容边界", ""])
    for note in (draft.get("compatibility") or {}).get("notes") or []:
        lines.append(f"- {note}")
    lines.extend(["", "## Residual Risk", "", f"- {draft.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def write_configuration_import_draft_outputs(*, output_dir: str | Path, draft: dict, overwrite: bool = False) -> dict:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    payloads = draft.get("draft_payloads") or {}
    files = {
        "profiles_draft": target_dir / "profiles.draft.json",
        "rule_pack_draft": target_dir / "rule_pack.draft.json",
        "credentials_example": target_dir / "credentials.example.json",
        "import_guide": target_dir / "IMPORT_GUIDE.md",
        "manifest": target_dir / "config_import_manifest.json",
    }
    existing = [str(path) for path in files.values() if path.exists()]
    if existing and not overwrite:
        return {
            "status": "blocked_existing_files",
            "output_dir": str(target_dir),
            "blocked_existing_files": existing,
            "created_files": [],
            **{key: str(path) for key, path in files.items()},
        }
    guide_markdown = payloads.get("import_guide_markdown") or configuration_import_draft_to_markdown(draft)
    manifest = {
        "version": "0.27-configuration-import-manifest",
        "generated_at": database.now_iso(),
        "readonly": True,
        "will_apply_configuration": False,
        "output_dir": str(target_dir),
        "draft_version": draft.get("version") or "",
        "files": [
            {
                "kind": key,
                "path": str(path),
                "exists_after_write": True,
            }
            for key, path in files.items()
        ],
        "manual_steps": draft.get("manual_steps") or [],
        "copy_commands": draft.get("copy_commands") or [],
        "residual_risk": draft.get("residual_risk") or "",
    }
    files["profiles_draft"].write_text(json.dumps(payloads.get("profiles_draft") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    files["rule_pack_draft"].write_text(json.dumps(payloads.get("rule_pack_draft") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    files["credentials_example"].write_text(json.dumps(payloads.get("credentials_example") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    files["import_guide"].write_text(guide_markdown, encoding="utf-8")
    files["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "created",
        "output_dir": str(target_dir),
        "created_files": [str(path) for path in files.values()],
        **{key: str(path) for key, path in files.items()},
    }


def build_configuration_import_review(*, draft_dir: str | Path) -> dict:
    target_dir = Path(draft_dir).expanduser().resolve()
    files = {
        "profiles_draft": target_dir / "profiles.draft.json",
        "rule_pack_draft": target_dir / "rule_pack.draft.json",
        "credentials_example": target_dir / "credentials.example.json",
        "import_guide": target_dir / "IMPORT_GUIDE.md",
        "manifest": target_dir / "config_import_manifest.json",
    }
    issues: list[dict] = []
    profiles_draft = read_import_review_json(files["profiles_draft"], kind="profiles_draft", issues=issues)
    rule_pack_draft = read_import_review_json(files["rule_pack_draft"], kind="rule_pack_draft", issues=issues)
    credentials_example = read_import_review_json(files["credentials_example"], kind="credentials_example", issues=issues)
    manifest = read_import_review_json(files["manifest"], kind="manifest", issues=issues)
    import_guide_text = read_import_review_text(files["import_guide"], kind="import_guide", issues=issues)

    issues.extend(validate_share_secret_leaks({"profiles_draft": profiles_draft, "rule_pack_draft": rule_pack_draft, "credentials_example": credentials_example}))
    issues.extend(validate_import_review_profiles(profiles_draft))
    issues.extend(validate_import_review_rule_pack(rule_pack_draft))
    issues.extend(validate_import_review_credentials(credentials_example))
    issues.extend(validate_import_review_manifest(manifest))
    if import_guide_text and "不会应用配置" not in import_guide_text:
        issues.append(
            {
                "severity": "warning",
                "code": "import_guide_missing_readonly_boundary",
                "path": "IMPORT_GUIDE.md",
                "message": "导入说明未明确写出不会应用配置，建议人工补充只读边界。",
            }
        )

    status = "failed" if any(item.get("severity") == "error" for item in issues) else "pass"
    file_entries = [
        {
            "kind": kind,
            "file_name": path.name,
            "path": str(path),
            "exists": path.exists(),
            "required": True,
        }
        for kind, path in files.items()
    ]
    profiles_summary = summarize_import_review_profiles(profiles_draft)
    rule_pack_summary = summarize_import_review_rule_pack(rule_pack_draft)
    credentials_summary = summarize_import_review_credentials(credentials_example)
    form_preview = build_import_review_form_preview(
        profiles_summary=profiles_summary,
        rule_pack_summary=rule_pack_summary,
        credentials_summary=credentials_summary,
    )
    manual_confirmation = build_import_review_manual_confirmation(
        profiles_summary=profiles_summary,
        rule_pack_summary=rule_pack_summary,
        credentials_summary=credentials_summary,
    )
    return {
        "version": "0.28-configuration-import-review",
        "generated_at": database.now_iso(),
        "readonly": True,
        "status": status,
        "draft_input_dir": str(target_dir),
        "will_apply_configuration": False,
        "will_write_real_config_dir": False,
        "will_write_local_files": False,
        "external_writes_enabled": False,
        "remote_connection_tests_enabled": False,
        "files": file_entries,
        "profiles": profiles_summary,
        "rule_pack": rule_pack_summary,
        "credentials": credentials_summary,
        "manifest": summarize_import_review_manifest(manifest),
        "form_preview": form_preview,
        "manual_confirmation": manual_confirmation,
        "import_before_risk_prompts": [
            "确认 project_root 和 output_root 已按本机环境填写，不能直接使用占位符。",
            "确认 provider 类型和 credential_keys 只是 key 名称，不包含任何真实 token 值。",
            "确认 hard_guards.external_writes_default 仍为 off，真实状态流转、commit/push、发布仍需单独确认。",
            "确认本回读校验不会应用配置；如需长期保存，仍由人工复制到个人目录并再次运行只读校验。",
        ],
        "issues": issues,
        "compatibility": {
            "requires_explicit_cli_flag": True,
            "requires_user_selected_input_dir": True,
            "default_harness_behavior": "unchanged_without_explicit_import_review",
            "review_is_readonly": True,
            "no_external_write": True,
            "no_remote_fetch": True,
            "no_real_config_write": True,
            "notes": [
                "v0.28 只回读 v0.27 生成的导入草案文件并生成只读表单预览。",
                "校验不会写入 ~/.his-harness，不会应用配置，不会保存真实 token，不会测试远端账号。",
                "即使校验通过，真实复制和路径确认仍需要用户人工完成。",
            ],
        },
        "residual_risk": "配置导入回读校验只检查草案文件结构、明显密钥泄漏、路径提示和硬保护开关；不能证明本机路径真实存在、远端账号可读、人工复制无误或后续配置已生效。",
    }


def read_import_review_json(path: Path, *, kind: str, issues: list[dict]) -> dict:
    if not path.exists():
        issues.append(
            {
                "severity": "error",
                "code": f"missing_{kind}",
                "path": str(path),
                "message": f"缺少导入草案文件：{path.name}。",
            }
        )
        return {}
    try:
        return read_json_object(path)
    except Exception as exc:
        issues.append(
            {
                "severity": "error",
                "code": f"invalid_{kind}_json",
                "path": str(path),
                "message": f"{path.name} 不是合法 JSON 对象：{exc}",
            }
        )
        return {}


def read_import_review_text(path: Path, *, kind: str, issues: list[dict]) -> str:
    if not path.exists():
        issues.append(
            {
                "severity": "error",
                "code": f"missing_{kind}",
                "path": str(path),
                "message": f"缺少导入说明文件：{path.name}。",
            }
        )
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        issues.append(
            {
                "severity": "error",
                "code": f"read_{kind}_failed",
                "path": str(path),
                "message": f"{path.name} 读取失败：{exc}",
            }
        )
        return ""


def validate_import_review_profiles(profiles_draft: dict) -> list[dict]:
    issues: list[dict] = []
    profiles = profiles_draft.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        return [
            {
                "severity": "error",
                "code": "profiles_draft_profiles_missing",
                "path": "profiles.profiles",
                "message": "profiles.draft.json 必须包含非空 profiles 数组。",
            }
        ]
    if not profiles_draft.get("default_profile"):
        issues.append(
            {
                "severity": "error",
                "code": "profiles_draft_default_profile_missing",
                "path": "profiles.default_profile",
                "message": "profiles.draft.json 必须声明 default_profile。",
            }
        )
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            issues.append(
                {
                    "severity": "error",
                    "code": "profiles_draft_profile_invalid",
                    "path": f"profiles.{index}",
                    "message": "profile 条目必须是 JSON 对象。",
                }
            )
            continue
        profile_key = profile.get("key") or f"index-{index}"
        provider = profile.get("requirement_provider") or {}
        if not profile.get("key"):
            issues.append(
                {
                    "severity": "error",
                    "code": "profile_key_missing",
                    "path": f"profiles.{profile_key}.key",
                    "message": "导入草案 profile 必须有 key。",
                }
            )
        if not profile.get("rule_pack_id"):
            issues.append(
                {
                    "severity": "error",
                    "code": "profile_rule_pack_missing",
                    "path": f"profiles.{profile_key}.rule_pack_id",
                    "message": "导入草案 profile 必须引用 rule_pack_id。",
                }
            )
        for field in ["project_root", "output_root"]:
            value = str(profile.get(field) or "").strip()
            path = f"profiles.{profile_key}.{field}"
            if not value:
                issues.append({"severity": "error", "code": f"{field}_missing", "path": path, "message": f"{field} 不能为空。"})
            elif is_placeholder_value(value):
                issues.append(
                    {
                        "severity": "warning",
                        "code": f"{field}_placeholder_requires_confirmation",
                        "path": path,
                        "message": f"{field} 仍是占位符，人工复制前必须改成本机真实路径。",
                    }
                )
            elif is_personal_absolute_path(value):
                issues.append(
                    {
                        "severity": "warning",
                        "code": f"{field}_personal_absolute_path",
                        "path": path,
                        "message": f"{field} 是个人绝对路径，分享给他人前应改为占位符或本机覆盖。",
                    }
                )
        if provider.get("readonly") is not True:
            issues.append(
                {
                    "severity": "error",
                    "code": "requirement_provider_not_readonly",
                    "path": f"profiles.{profile_key}.requirement_provider.readonly",
                    "message": "需求来源 provider 必须保持 readonly=true。",
                }
            )
        credential_keys = provider.get("credential_keys")
        if credential_keys is not None and not isinstance(credential_keys, list):
            issues.append(
                {
                    "severity": "error",
                    "code": "credential_keys_not_list",
                    "path": f"profiles.{profile_key}.requirement_provider.credential_keys",
                    "message": "credential_keys 必须是 key 名称数组，不能写真实凭证值。",
                }
            )
    return issues


def validate_import_review_rule_pack(rule_pack_draft: dict) -> list[dict]:
    issues = validate_share_hard_guards(rule_pack=rule_pack_draft, summary={})
    if not rule_pack_draft.get("rule_pack_id"):
        issues.append(
            {
                "severity": "error",
                "code": "rule_pack_id_missing",
                "path": "rule_pack.rule_pack_id",
                "message": "rule_pack.draft.json 必须包含 rule_pack_id。",
            }
        )
    return issues


def validate_import_review_credentials(credentials_example: dict) -> list[dict]:
    issues: list[dict] = []
    if credentials_example.get("secret_values_included") is not False:
        issues.append(
            {
                "severity": "error",
                "code": "credentials_example_secret_values_included",
                "path": "credentials.secret_values_included",
                "message": "credentials.example.json 必须声明 secret_values_included=false。",
            }
        )
    values = credentials_example.get("values")
    if values is not None and not isinstance(values, dict):
        issues.append(
            {
                "severity": "error",
                "code": "credentials_values_not_object",
                "path": "credentials.values",
                "message": "credentials.example.json 的 values 必须是对象。",
            }
        )
    for key, value in (values or {}).items():
        if isinstance(value, str) and value.strip() and not is_placeholder_value(value):
            issues.append(
                {
                    "severity": "error",
                    "code": "credential_value_must_not_be_filled",
                    "path": f"credentials.values.{key}",
                    "message": "credentials.example.json 不能填写真实凭证值；这里只记录风险路径，不回显具体值。",
                }
            )
    items = credentials_example.get("items")
    if not isinstance(items, list):
        issues.append(
            {
                "severity": "error",
                "code": "credentials_items_missing",
                "path": "credentials.items",
                "message": "credentials.example.json 必须包含 items 数组。",
            }
        )
        return issues
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(
                {
                    "severity": "error",
                    "code": "credentials_item_invalid",
                    "path": f"credentials.items.{index}",
                    "message": "凭证条目必须是 JSON 对象。",
                }
            )
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            issues.append(
                {
                    "severity": "error",
                    "code": "credential_key_missing",
                    "path": f"credentials.items.{index}.key",
                    "message": "凭证条目必须包含 key。",
                }
            )
        item_value = item.get("value")
        if item.get("secret", True) and isinstance(item_value, str) and item_value.strip() and not is_placeholder_value(item_value):
            issues.append(
                {
                    "severity": "error",
                    "code": "credential_item_value_must_be_placeholder",
                    "path": f"credentials.items.{index}.value",
                    "message": "secret 凭证条目的 value 只能是占位符或空值，不能写真实 token。",
                }
            )
    return issues


def validate_import_review_manifest(manifest: dict) -> list[dict]:
    issues: list[dict] = []
    if manifest.get("will_apply_configuration") is not False:
        issues.append(
            {
                "severity": "error",
                "code": "manifest_will_apply_configuration_not_false",
                "path": "manifest.will_apply_configuration",
                "message": "config_import_manifest.json 必须声明 will_apply_configuration=false。",
            }
        )
    if manifest.get("readonly") is not True:
        issues.append(
            {
                "severity": "error",
                "code": "manifest_readonly_not_true",
                "path": "manifest.readonly",
                "message": "config_import_manifest.json 必须声明 readonly=true。",
            }
        )
    return issues


def is_placeholder_value(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    return any(marker in text for marker in ["<", ">", "placeholder", "example", "your_", "xxx", "dummy", "sample", "本机填写", "本机配置"])


def summarize_import_review_profiles(profiles_draft: dict) -> dict:
    items = []
    for profile in profiles_draft.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        provider = profile.get("requirement_provider") or {}
        items.append(
            {
                "key": profile.get("key") or "",
                "display_name": profile.get("display_name") or "",
                "rule_pack_id": profile.get("rule_pack_id") or "",
                "project_root": profile.get("project_root") or "",
                "output_root": profile.get("output_root") or "",
                "requirement_provider_type": provider.get("type") or "",
                "requirement_provider_name": provider.get("name") or "",
                "credential_keys": [str(key) for key in provider.get("credential_keys") or []],
                "provider_readonly": provider.get("readonly") is True,
                "enabled_features": [str(item) for item in profile.get("enabled_features") or []],
            }
        )
    first = items[0] if items else {}
    return {
        "version": profiles_draft.get("version") or "",
        "default_profile": profiles_draft.get("default_profile") or "",
        "profile_count": len(items),
        "active_profile": first,
        "items": items,
    }


def summarize_import_review_rule_pack(rule_pack_draft: dict) -> dict:
    hard_guards = rule_pack_draft.get("hard_guards") or {}
    permissions = (rule_pack_draft.get("git") or {}).get("permissions") or {}
    status_flow = rule_pack_draft.get("status_flow") or {}
    comments = rule_pack_draft.get("comments") or {}
    return {
        "rule_pack_id": rule_pack_draft.get("rule_pack_id") or "",
        "version": rule_pack_draft.get("version") or "",
        "display_name": rule_pack_draft.get("display_name") or "",
        "hard_guards": {
            "external_writes_default": hard_guards.get("external_writes_default") or "",
            "no_secret_printing": hard_guards.get("no_secret_printing") is True,
            "real_status_transition_requires_confirmation": hard_guards.get("real_status_transition_requires_confirmation") is True,
            "real_commit_push_requires_confirmation": hard_guards.get("real_commit_push_requires_confirmation") is True,
            "destructive_git_forbidden": hard_guards.get("destructive_git_forbidden") is True,
            "publish_forbidden_by_default": hard_guards.get("publish_forbidden_by_default") is True,
        },
        "git_permissions": {
            "auto_create_branch": permissions.get("auto_create_branch") is True,
            "auto_commit": permissions.get("auto_commit") is True,
            "auto_push": permissions.get("auto_push") is True,
            "auto_merge": permissions.get("auto_merge") is True,
        },
        "comment_template": comments.get("delivery_template") or "",
        "status_flow": {
            "real_transition_enabled": status_flow.get("real_transition_enabled") is True,
            "dry_run_enabled": status_flow.get("dry_run_enabled") is True,
        },
    }


def summarize_import_review_credentials(credentials_example: dict) -> dict:
    items = []
    for item in credentials_example.get("items") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        items.append(
            {
                "key": item.get("key") or "",
                "label": item.get("label") or item.get("key") or "",
                "required": bool(item.get("required")),
                "secret": bool(item.get("secret", True)),
                "usage": item.get("usage") or [],
                "filled": bool(isinstance(value, str) and value.strip() and not is_placeholder_value(value)),
                "placeholder": is_placeholder_value(value),
            }
        )
    return {
        "version": credentials_example.get("version") or "",
        "secret_values_included": credentials_example.get("secret_values_included") is True,
        "recommended_secret_sources": credentials_example.get("recommended_secret_sources") or [],
        "credential_count": len(items),
        "items": items,
    }


def summarize_import_review_manifest(manifest: dict) -> dict:
    return {
        "version": manifest.get("version") or "",
        "readonly": manifest.get("readonly") is True,
        "will_apply_configuration": manifest.get("will_apply_configuration") is True,
        "output_dir": manifest.get("output_dir") or "",
        "draft_version": manifest.get("draft_version") or "",
        "file_count": len(manifest.get("files") or []),
    }


def build_import_review_form_preview(*, profiles_summary: dict, rule_pack_summary: dict, credentials_summary: dict) -> dict:
    profile = profiles_summary.get("active_profile") or {}
    credential_keys = profile.get("credential_keys") or []
    hard_guards = rule_pack_summary.get("hard_guards") or {}
    git_permissions = rule_pack_summary.get("git_permissions") or {}
    status_flow = rule_pack_summary.get("status_flow") or {}
    return {
        "version": "0.28-readonly-import-form-preview",
        "readonly": True,
        "sections": [
            {
                "key": "profile",
                "title": "Profile",
                "fields": [
                    {"name": "profile_key", "label": "Profile Key", "value": profile.get("key") or profiles_summary.get("default_profile") or "", "readonly": True},
                    {"name": "display_name", "label": "显示名称", "value": profile.get("display_name") or "", "readonly": True},
                    {"name": "project_root", "label": "项目根目录", "value": profile.get("project_root") or "", "readonly": True, "requires_user_confirmation": True},
                    {"name": "output_root", "label": "Harness 输出目录", "value": profile.get("output_root") or "", "readonly": True, "requires_user_confirmation": True},
                    {"name": "requirement_provider_type", "label": "需求来源类型", "value": profile.get("requirement_provider_type") or "", "readonly": True},
                    {"name": "credential_keys", "label": "凭证 Key 名称", "value": credential_keys, "readonly": True, "secret_values_visible": False},
                ],
            },
            {
                "key": "rule_pack",
                "title": "Rule Pack",
                "fields": [
                    {"name": "rule_pack_id", "label": "Rule Pack ID", "value": rule_pack_summary.get("rule_pack_id") or "", "readonly": True},
                    {"name": "hard_guards", "label": "硬保护", "value": hard_guards, "readonly": True},
                    {"name": "git_permissions", "label": "Git 自动动作", "value": git_permissions, "readonly": True},
                    {"name": "comment_template", "label": "需求评论模板", "value": rule_pack_summary.get("comment_template") or "", "readonly": True},
                    {"name": "status_flow", "label": "状态流转", "value": status_flow, "readonly": True},
                ],
            },
            {
                "key": "credentials",
                "title": "Credential Store",
                "fields": [
                    {"name": "credential_keys", "label": "凭证 Key", "value": [item.get("key") or "" for item in credentials_summary.get("items") or []], "readonly": True, "secret_values_visible": False},
                    {"name": "credential_items", "label": "凭证条目", "value": credentials_summary.get("items") or [], "readonly": True, "secret_values_visible": False},
                    {"name": "recommended_secret_sources", "label": "推荐密钥来源", "value": credentials_summary.get("recommended_secret_sources") or [], "readonly": True},
                ],
            },
            {
                "key": "manual_confirmation",
                "title": "导入前确认",
                "fields": [
                    {"name": "manual_confirmation", "label": "必须人工确认", "value": "路径、provider、credential key、硬保护、无真实 token、不会应用配置", "readonly": True, "requires_user_confirmation": True},
                ],
            },
        ],
    }


def build_import_review_manual_confirmation(*, profiles_summary: dict, rule_pack_summary: dict, credentials_summary: dict) -> list[dict]:
    profile = profiles_summary.get("active_profile") or {}
    return [
        {"key": "project_root", "label": "项目根目录", "value": profile.get("project_root") or "", "required": True, "confirmed_by_harness": False},
        {"key": "output_root", "label": "Harness 输出目录", "value": profile.get("output_root") or "", "required": True, "confirmed_by_harness": False},
        {"key": "requirement_provider", "label": "需求来源 provider", "value": profile.get("requirement_provider_type") or "", "required": True, "confirmed_by_harness": False},
        {"key": "credential_keys", "label": "凭证 Key 名称", "value": profile.get("credential_keys") or [], "required": True, "confirmed_by_harness": False},
        {"key": "hard_guards", "label": "硬保护仍关闭真实写入", "value": rule_pack_summary.get("hard_guards") or {}, "required": True, "confirmed_by_harness": False},
        {"key": "credentials_are_local_only", "label": "真实凭证只放本机 env/凭证文件/Keychain", "value": credentials_summary.get("recommended_secret_sources") or [], "required": True, "confirmed_by_harness": False},
    ]


def configuration_import_review_to_markdown(review: dict) -> str:
    lines = [
        "# Harness 配置导入回读校验",
        "",
        f"- 版本：{review.get('version')}",
        f"- 状态：{review.get('status')}",
        f"- 只读：{review.get('readonly')}",
        f"- 草案目录：`{review.get('draft_input_dir') or '-'}`",
        f"- 会应用配置：{review.get('will_apply_configuration')}",
        f"- 写真实配置目录：{review.get('will_write_real_config_dir')}",
        f"- 远端连通测试：{review.get('remote_connection_tests_enabled')}",
        "",
        "本校验只回读草案文件并生成只读表单预览，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。",
        "",
        "## 草案文件",
        "",
    ]
    for item in review.get("files") or []:
        lines.append(f"- {item.get('kind') or '-'}：`{item.get('path') or '-'}`，存在：{item.get('exists')}")
    profile = (review.get("profiles") or {}).get("active_profile") or {}
    rule_pack = review.get("rule_pack") or {}
    lines.extend(
        [
            "",
            "## 只读表单预览",
            "",
            f"- Profile：`{profile.get('key') or '-'}`",
            f"- 项目根目录：`{profile.get('project_root') or '-'}`",
            f"- 输出目录：`{profile.get('output_root') or '-'}`",
            f"- Provider：{profile.get('requirement_provider_type') or '-'}",
            f"- Credential Keys：{', '.join(profile.get('credential_keys') or []) or '无'}",
            f"- Rule Pack：`{rule_pack.get('rule_pack_id') or '-'}`",
            f"- 硬保护：{json.dumps(rule_pack.get('hard_guards') or {}, ensure_ascii=False)}",
            f"- Git 自动动作：{json.dumps(rule_pack.get('git_permissions') or {}, ensure_ascii=False)}",
            f"- 状态真实流转：{(rule_pack.get('status_flow') or {}).get('real_transition_enabled')}",
            "",
            "## 导入前风险提示",
            "",
        ]
    )
    for prompt in review.get("import_before_risk_prompts") or []:
        lines.append(f"- {prompt}")
    lines.extend(["", "## 人工确认项", ""])
    for item in review.get("manual_confirmation") or []:
        lines.append(f"- {item.get('key') or '-'}：{item.get('label') or '-'}，Harness 已确认：{item.get('confirmed_by_harness')}")
    issues = review.get("issues") or []
    lines.extend(["", "## Issues", ""])
    if issues:
        for item in issues:
            lines.append(f"- [{item.get('severity') or '-'}] {item.get('code') or '-'} `{item.get('path') or '-'}`：{item.get('message') or '-'}")
    else:
        lines.append("- 未发现阻断项。")
    lines.extend(["", "## 兼容边界", ""])
    for note in (review.get("compatibility") or {}).get("notes") or []:
        lines.append(f"- {note}")
    lines.extend(["", "## Residual Risk", "", f"- {review.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def write_configuration_import_review_outputs(*, output_dir: str | Path, review: dict) -> dict:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "harness_config_import_review.json"
    markdown_path = target_dir / "harness_config_import_review.md"
    json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(configuration_import_review_to_markdown(review), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def build_configuration_template_index(*, draft_dirs: list[str | Path]) -> dict:
    issues: list[dict] = []
    normalized_dirs = [Path(item).expanduser().resolve() for item in draft_dirs if str(item or "").strip()]
    if not normalized_dirs:
        issues.append(
            {
                "severity": "error",
                "code": "template_index_requires_draft_dir",
                "message": "配置模板索引至少需要一个 --draft-input-dir。",
            }
        )
    sources = [build_configuration_template_source(index=index, draft_dir=draft_dir) for index, draft_dir in enumerate(normalized_dirs, start=1)]
    for source in sources:
        for item in source.get("issues") or []:
            if item.get("severity") == "error":
                issues.append(
                    {
                        "severity": "error",
                        "code": f"source_{source.get('source_key')}_{item.get('code')}",
                        "path": item.get("path") or source.get("draft_input_dir") or "",
                        "message": item.get("message") or "草案来源存在阻断项。",
                    }
                )
    status = "failed" if any(item.get("severity") == "error" for item in issues) else "pass"
    return {
        "version": "0.29-configuration-template-index",
        "generated_at": database.now_iso(),
        "readonly": True,
        "status": status,
        "source_count": len(sources),
        "will_apply_configuration": False,
        "will_write_real_config_dir": False,
        "external_writes_enabled": False,
        "remote_connection_tests_enabled": False,
        "sources": sources,
        "team_template_index": build_team_template_index(sources),
        "diff_summary": build_template_diff_summary(sources[0], sources[1]) if len(sources) >= 2 else build_empty_template_diff_summary(),
        "issues": issues,
        "compatibility": {
            "requires_explicit_cli_flag": True,
            "requires_user_selected_input_dir": True,
            "default_harness_behavior": "unchanged_without_explicit_template_index",
            "index_is_readonly": True,
            "no_external_write": True,
            "no_remote_fetch": True,
            "no_real_config_write": True,
            "notes": [
                "v0.29 只索引和对比配置草案目录，不会应用配置。",
                "多 profile 切换预览只展示 provider、credential key、路径状态和规则差异，不保存真实 token。",
                "团队模板索引用于人工选择和复制前审查，不能替代本机路径、凭证和只读读取验证。",
            ],
        },
        "residual_risk": "配置模板索引只对本地草案做静态只读对比；不能证明路径存在、远端账号可读、人工复制无误或后续配置已生效。",
    }


def build_configuration_template_source(*, index: int, draft_dir: Path) -> dict:
    files = {
        "profiles_draft": draft_dir / "profiles.draft.json",
        "rule_pack_draft": draft_dir / "rule_pack.draft.json",
        "credentials_example": draft_dir / "credentials.example.json",
        "import_guide": draft_dir / "IMPORT_GUIDE.md",
        "manifest": draft_dir / "config_import_manifest.json",
    }
    issues: list[dict] = []
    profiles_draft = read_import_review_json(files["profiles_draft"], kind="profiles_draft", issues=issues)
    rule_pack_draft = read_import_review_json(files["rule_pack_draft"], kind="rule_pack_draft", issues=issues)
    credentials_example = read_import_review_json(files["credentials_example"], kind="credentials_example", issues=issues)
    manifest = read_import_review_json(files["manifest"], kind="manifest", issues=issues)
    if not files["import_guide"].exists():
        issues.append(
            {
                "severity": "error",
                "code": "missing_import_guide",
                "path": str(files["import_guide"]),
                "message": "缺少导入说明文件：IMPORT_GUIDE.md。",
            }
        )
    review = build_configuration_import_review(draft_dir=draft_dir)
    source_key = f"source-{index}-{draft_dir.name or 'draft'}"
    template_files = [
        {
            "kind": kind,
            "file_name": path.name,
            "path": str(path),
            "exists": path.exists(),
            "share_role": template_file_share_role(kind),
            "secret_values_allowed": False,
        }
        for kind, path in files.items()
    ]
    return {
        "version": "0.29-template-source",
        "source_key": source_key,
        "draft_input_dir": str(draft_dir),
        "readonly": True,
        "review_status": review.get("status") or "failed",
        "review_issue_count": len(review.get("issues") or []),
        "blocking_issue_count": len([item for item in review.get("issues") or [] if item.get("severity") == "error"]),
        "profile_switch_preview": build_profile_switch_preview(profiles_draft),
        "rule_pack": summarize_import_review_rule_pack(rule_pack_draft),
        "credentials": summarize_import_review_credentials(credentials_example),
        "manifest": summarize_import_review_manifest(manifest),
        "template_files": template_files,
        "issues": [*(review.get("issues") or []), *issues],
    }


def template_file_share_role(kind: str) -> str:
    return {
        "profiles_draft": "profile_template",
        "rule_pack_draft": "rule_pack_template",
        "credentials_example": "credential_format_example",
        "import_guide": "manual_import_guide",
        "manifest": "draft_file_manifest",
    }.get(kind, "template_file")


def build_profile_switch_preview(profiles_draft: dict) -> list[dict]:
    previews = []
    for profile in profiles_draft.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        provider = profile.get("requirement_provider") or {}
        previews.append(
            {
                "profile_key": profile.get("key") or "",
                "display_name": profile.get("display_name") or "",
                "rule_pack_id": profile.get("rule_pack_id") or "",
                "provider_type": provider.get("type") or "",
                "provider_name": provider.get("name") or "",
                "provider_readonly": provider.get("readonly") is True,
                "credential_keys": [str(key) for key in provider.get("credential_keys") or []],
                "project_root_state": classify_template_path(profile.get("project_root")),
                "output_root_state": classify_template_path(profile.get("output_root")),
                "enabled_features": [str(item) for item in profile.get("enabled_features") or []],
                "switch_requires_manual_confirmation": True,
                "secret_values_visible": False,
            }
        )
    return previews


def classify_template_path(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "missing"
    if is_placeholder_value(text):
        return "placeholder"
    if is_personal_absolute_path(text):
        return "personal_absolute_path"
    if text.startswith("/tmp/") or text.startswith("/absolute/path"):
        return "template_or_temp_path"
    return "custom_path"


def build_team_template_index(sources: list[dict]) -> dict:
    files = []
    for source in sources:
        for item in source.get("template_files") or []:
            if not isinstance(item, dict):
                continue
            file_item = dict(item)
            file_item["source_key"] = source.get("source_key") or ""
            file_item["draft_input_dir"] = source.get("draft_input_dir") or ""
            files.append(file_item)
    return {
        "version": "0.29-team-template-index",
        "readonly": True,
        "source_count": len(sources),
        "file_count": len(files),
        "files": files,
        "share_rules": {
            "secret_values_forbidden": True,
            "local_paths_require_confirmation": True,
            "real_config_write_forbidden": True,
            "remote_connection_tests_disabled": True,
        },
    }


def build_empty_template_diff_summary() -> dict:
    return {
        "version": "0.29-template-diff-summary",
        "readonly": True,
        "source_pair": [],
        "profile_keys_added": [],
        "profile_keys_removed": [],
        "profile_keys_common": [],
        "provider_type_changed": False,
        "credential_keys_added": [],
        "credential_keys_removed": [],
        "hard_guard_changes": {},
        "git_permission_changes": {},
        "comment_template_changed": False,
        "status_flow_changes": {},
        "path_state_changes": {},
        "change_count": 0,
    }


def build_template_diff_summary(base: dict, compare: dict) -> dict:
    summary = build_empty_template_diff_summary()
    summary["source_pair"] = [base.get("source_key") or "base", compare.get("source_key") or "compare"]
    base_profiles = {item.get("profile_key") or "": item for item in base.get("profile_switch_preview") or [] if isinstance(item, dict)}
    compare_profiles = {item.get("profile_key") or "": item for item in compare.get("profile_switch_preview") or [] if isinstance(item, dict)}
    base_keys = {key for key in base_profiles if key}
    compare_keys = {key for key in compare_profiles if key}
    summary["profile_keys_added"] = sorted(compare_keys - base_keys)
    summary["profile_keys_removed"] = sorted(base_keys - compare_keys)
    summary["profile_keys_common"] = sorted(base_keys & compare_keys)
    base_active = first_profile_preview(base)
    compare_active = first_profile_preview(compare)
    summary["provider_type_changed"] = (base_active.get("provider_type") or "") != (compare_active.get("provider_type") or "")
    base_credential_keys = set(base_active.get("credential_keys") or [])
    compare_credential_keys = set(compare_active.get("credential_keys") or [])
    summary["credential_keys_added"] = sorted(compare_credential_keys - base_credential_keys)
    summary["credential_keys_removed"] = sorted(base_credential_keys - compare_credential_keys)
    summary["hard_guard_changes"] = diff_dict_values((base.get("rule_pack") or {}).get("hard_guards") or {}, (compare.get("rule_pack") or {}).get("hard_guards") or {})
    summary["git_permission_changes"] = diff_dict_values((base.get("rule_pack") or {}).get("git_permissions") or {}, (compare.get("rule_pack") or {}).get("git_permissions") or {})
    summary["comment_template_changed"] = ((base.get("rule_pack") or {}).get("comment_template") or "") != ((compare.get("rule_pack") or {}).get("comment_template") or "")
    summary["status_flow_changes"] = diff_dict_values((base.get("rule_pack") or {}).get("status_flow") or {}, (compare.get("rule_pack") or {}).get("status_flow") or {})
    summary["path_state_changes"] = diff_dict_values(
        {
            "project_root_state": base_active.get("project_root_state") or "",
            "output_root_state": base_active.get("output_root_state") or "",
        },
        {
            "project_root_state": compare_active.get("project_root_state") or "",
            "output_root_state": compare_active.get("output_root_state") or "",
        },
    )
    summary["change_count"] = (
        len(summary["profile_keys_added"])
        + len(summary["profile_keys_removed"])
        + int(summary["provider_type_changed"])
        + len(summary["credential_keys_added"])
        + len(summary["credential_keys_removed"])
        + len(summary["hard_guard_changes"])
        + len(summary["git_permission_changes"])
        + int(summary["comment_template_changed"])
        + len(summary["status_flow_changes"])
        + len(summary["path_state_changes"])
    )
    return summary


def first_profile_preview(source: dict) -> dict:
    previews = source.get("profile_switch_preview") or []
    for item in previews:
        if isinstance(item, dict):
            return item
    return {}


def diff_dict_values(base: dict, compare: dict) -> dict:
    changes = {}
    for key in sorted(set(base) | set(compare)):
        if base.get(key) != compare.get(key):
            changes[key] = {"base": base.get(key), "compare": compare.get(key)}
    return changes


def configuration_template_index_to_markdown(index: dict) -> str:
    lines = [
        "# Harness 配置模板索引",
        "",
        f"- 版本：{index.get('version')}",
        f"- 状态：{index.get('status')}",
        f"- 只读：{index.get('readonly')}",
        f"- 来源数：{index.get('source_count')}",
        f"- 会应用配置：{index.get('will_apply_configuration')}",
        f"- 写真实配置目录：{index.get('will_write_real_config_dir')}",
        "",
        "本索引只读取用户选择目录中的草案文件，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。",
        "",
        "## 多 Profile 切换预览",
        "",
    ]
    for source in index.get("sources") or []:
        lines.extend(["", f"### {source.get('source_key') or '-'}", "", f"- 草案目录：`{source.get('draft_input_dir') or '-'}`", f"- 回读状态：{source.get('review_status') or '-'}"])
        for profile in source.get("profile_switch_preview") or []:
            lines.append(
                f"- `{profile.get('profile_key') or '-'}`：provider={profile.get('provider_type') or '-'}，credential_keys={', '.join(profile.get('credential_keys') or []) or '无'}，project_root={profile.get('project_root_state') or '-'}，output_root={profile.get('output_root_state') or '-'}"
            )
    diff = index.get("diff_summary") or {}
    lines.extend(
        [
            "",
            "## 配置差异对比",
            "",
            f"- 对比来源：{', '.join(diff.get('source_pair') or []) or '-'}",
            f"- 新增 Profile：{', '.join(diff.get('profile_keys_added') or []) or '无'}",
            f"- 移除 Profile：{', '.join(diff.get('profile_keys_removed') or []) or '无'}",
            f"- Provider 变化：{diff.get('provider_type_changed')}",
            f"- Credential Key 新增：{', '.join(diff.get('credential_keys_added') or []) or '无'}",
            f"- Credential Key 移除：{', '.join(diff.get('credential_keys_removed') or []) or '无'}",
            f"- 评论模板变化：{diff.get('comment_template_changed')}",
            f"- 硬保护变化：{json.dumps(diff.get('hard_guard_changes') or {}, ensure_ascii=False)}",
            f"- Git 权限变化：{json.dumps(diff.get('git_permission_changes') or {}, ensure_ascii=False)}",
            f"- 状态流转变化：{json.dumps(diff.get('status_flow_changes') or {}, ensure_ascii=False)}",
            f"- 路径状态变化：{json.dumps(diff.get('path_state_changes') or {}, ensure_ascii=False)}",
            f"- 变化计数：{diff.get('change_count')}",
            "",
            "## 团队模板索引",
            "",
        ]
    )
    for item in (index.get("team_template_index") or {}).get("files") or []:
        lines.append(
            f"- {item.get('source_key') or '-'} / {item.get('kind') or '-'}：`{item.get('path') or '-'}`，存在：{item.get('exists')}"
        )
    issues = index.get("issues") or []
    lines.extend(["", "## Issues", ""])
    if issues:
        for item in issues:
            lines.append(f"- [{item.get('severity') or '-'}] {item.get('code') or '-'} `{item.get('path') or '-'}`：{item.get('message') or '-'}")
    else:
        lines.append("- 未发现阻断项。")
    lines.extend(["", "## 兼容边界", ""])
    for note in (index.get("compatibility") or {}).get("notes") or []:
        lines.append(f"- {note}")
    lines.extend(["", "## Residual Risk", "", f"- {index.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def write_configuration_template_index_outputs(*, output_dir: str | Path, index: dict) -> dict:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "harness_config_template_index.json"
    markdown_path = target_dir / "harness_config_template_index.md"
    json_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(configuration_template_index_to_markdown(index), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def build_configuration_wizard(
    *,
    config_summary: dict,
    config_preview: dict | None = None,
    config_share_validation: dict | None = None,
    config_import_draft: dict | None = None,
    config_import_review: dict | None = None,
    config_template_index: dict | None = None,
    draft_input_dir: str | Path | None = None,
    compare_draft_input_dir: str | Path | None = None,
) -> dict:
    resolved_draft_input_dir = str(Path(draft_input_dir).expanduser().resolve()) if draft_input_dir else ""
    resolved_compare_dir = str(Path(compare_draft_input_dir).expanduser().resolve()) if compare_draft_input_dir else ""
    steps = build_configuration_wizard_steps(
        config_summary=config_summary,
        config_preview=config_preview,
        config_share_validation=config_share_validation,
        config_import_draft=config_import_draft,
        config_import_review=config_import_review,
        config_template_index=config_template_index,
        draft_input_dir=resolved_draft_input_dir,
        compare_draft_input_dir=resolved_compare_dir,
    )
    blocking_steps = [step for step in steps if step.get("blocking") and step.get("status") in {"failed", "missing"}]
    copy_commands = build_configuration_wizard_copy_commands(
        draft_input_dir=resolved_draft_input_dir,
        compare_draft_input_dir=resolved_compare_dir,
    )
    return {
        "version": "0.31-configuration-wizard",
        "generated_at": database.now_iso(),
        "readonly": True,
        "status": "failed" if blocking_steps else "pass",
        "will_apply_configuration": False,
        "will_write_real_config_dir": False,
        "external_writes_enabled": False,
        "remote_connection_tests_enabled": False,
        "credential_values_exposed": False,
        "draft_input_dir": resolved_draft_input_dir,
        "compare_draft_input_dir": resolved_compare_dir,
        "steps": steps,
        "blocking_steps": [
            {"id": step.get("id") or "", "title": step.get("title") or "", "status": step.get("status") or ""}
            for step in blocking_steps
        ],
        "ui_readability": build_configuration_wizard_readability(
            steps=steps,
            blocking_steps=blocking_steps,
            copy_commands=copy_commands,
        ),
        "manual_checklist": build_configuration_wizard_manual_checklist(
            config_summary=config_summary,
            config_import_review=config_import_review,
            config_template_index=config_template_index,
        ),
        "copy_commands": copy_commands,
        "risk_prompts": [
            "确认需求来源 provider 只读，真实评论、状态流转、附件上传、提交和发布仍由独立流程显式确认。",
            "确认草案目录是用户选择目录，真实凭证只放本机 env、凭证文件或 Keychain，不复制到团队模板。",
            "确认项目路径、输出路径、credential key 名称、评论模板和状态流转规则符合当前团队约定。",
            "确认本页面只是人工复制前的只读向导，不会应用配置、不会写入 ~/.his-harness、不会测试远端账号。",
        ],
        "compatibility": {
            "requires_explicit_cli_flag": True,
            "default_harness_behavior": "unchanged_without_explicit_config_wizard",
            "wizard_is_readonly": True,
            "no_external_write": True,
            "no_remote_fetch": True,
            "no_real_config_write": True,
            "notes": [
                "v0.31 配置向导只聚合 v0.22-v0.30 的只读结果，默认旧命令不进入该流程。",
                "向导页面可以用于人工复制前检查，但不能替代本机真实路径、凭证和远端只读权限验证。",
                "步骤筛选、阻断摘要和命令复制只服务离线阅读，不会触发任何命令执行。",
                "配置应用、真实外部写入、状态流转、commit/push 和发布仍然需要单独显式治理流程。",
            ],
        },
        "residual_risk": "配置向导只整合本地静态结果和草案回读结果；不能证明人工复制无误、真实远端账号可读、业务项目启动正常或后续配置已经生效。",
    }


def build_configuration_wizard_steps(
    *,
    config_summary: dict,
    config_preview: dict | None,
    config_share_validation: dict | None,
    config_import_draft: dict | None,
    config_import_review: dict | None,
    config_template_index: dict | None,
    draft_input_dir: str,
    compare_draft_input_dir: str,
) -> list[dict]:
    summary_validation = config_summary.get("validation") or {}
    import_draft_write_result = (config_import_draft or {}).get("write_result") or {}
    import_draft_status = "pass" if config_import_draft else ("manual_ready" if draft_input_dir else "missing")
    if import_draft_write_result.get("status") in {"failed", "blocked_existing_files"}:
        import_draft_status = import_draft_write_result.get("status")
    return [
        build_configuration_wizard_step(
            step_id="config_summary",
            title="选择来源 / 配置摘要",
            status=summary_validation.get("status") or ("pass" if config_summary else "missing"),
            blocking=True,
            description="确认 Rule Pack、Profile、Provider 和凭证 key 的只读摘要。",
            source_version=config_summary.get("version") or "",
            artifacts=["harness_config_summary.json", "harness_config_summary.md"],
            confirmations=[
                f"当前 Profile：{(config_summary.get('profile') or {}).get('key') or '-'}",
                f"需求来源：{(config_summary.get('providers') or {}).get('active_requirement_source') or '-'}",
                f"凭证缺失数：{(config_summary.get('credentials') or {}).get('required_missing_count') or 0}",
            ],
            next_action="如需切换云效、TAPD、手工或文件来源，先修改草案中的 profile，不在向导内直接写真实配置。",
        ),
        build_configuration_wizard_step(
            step_id="configuration_preview",
            title="看规则 / Provider 模板",
            status="pass" if config_preview else "missing",
            blocking=True,
            description="预览可分享 provider 模板、评论模板、状态流转和硬保护。",
            source_version=(config_preview or {}).get("version") or "",
            artifacts=["harness_config_preview.json", "harness_config_preview.md"],
            confirmations=[
                f"模板数量：{len((config_preview or {}).get('provider_templates') or [])}",
                f"密钥值暴露：{(config_preview or {}).get('credential_values_exposed')}",
                f"外部写入：{(config_preview or {}).get('external_writes_enabled')}",
            ],
            next_action="确认模板只描述 key 名称和本地覆盖方式，不包含真实 token。",
        ),
        build_configuration_wizard_step(
            step_id="share_validation",
            title="分享校验",
            status=(config_share_validation or {}).get("status") or "missing",
            blocking=True,
            description="检查分享包是否有真实密钥、个人路径、硬保护和本地覆盖策略风险。",
            source_version=(config_share_validation or {}).get("version") or "",
            artifacts=["harness_config_share_validation.json", "harness_config_share_validation.md"],
            confirmations=[
                f"Issue 数：{len((config_share_validation or {}).get('issues') or [])}",
                f"会应用配置：{(config_share_validation or {}).get('will_apply_configuration')}",
                f"外部写入：{(config_share_validation or {}).get('external_writes_enabled')}",
            ],
            next_action="存在 error 时先修草案，不要复制给他人使用。",
        ),
        build_configuration_wizard_step(
            step_id="import_draft",
            title="生成草案",
            status=import_draft_status,
            blocking=False,
            description="生成或登记用户选择目录中的 profile/rule/credential/import guide 草案。",
            source_version=(config_import_draft or {}).get("version") or "",
            artifacts=["profiles.draft.json", "rule_pack.draft.json", "credentials.example.json", "IMPORT_GUIDE.md", "config_import_manifest.json"],
            confirmations=[
                f"草案输出目录：{(config_import_draft or {}).get('draft_output_dir') or draft_input_dir or '-'}",
                f"只写用户选择目录：{(config_import_draft or {}).get('writes_only_to_user_selected_dir')}",
                f"写入状态：{import_draft_write_result.get('status') or ('已由 --draft-input-dir 提供' if draft_input_dir and not config_import_draft else '-')}",
            ],
            next_action="需要新草案时使用 --include-import-draft/--include-config-import-draft 并显式传入 --draft-output-dir。",
        ),
        build_configuration_wizard_step(
            step_id="import_review",
            title="回读校验",
            status=(config_import_review or {}).get("status") or "missing",
            blocking=True,
            description="从用户选择目录回读草案，生成只读表单预览和导入前风险提示。",
            source_version=(config_import_review or {}).get("version") or "",
            artifacts=["harness_config_import_review.json", "harness_config_import_review.md"],
            confirmations=[
                f"草案输入目录：{(config_import_review or {}).get('draft_input_dir') or draft_input_dir or '-'}",
                f"文件数：{len((config_import_review or {}).get('files') or [])}",
                f"远端连通测试：{(config_import_review or {}).get('remote_connection_tests_enabled')}",
            ],
            next_action="确认只读表单里的路径、provider、credential key、硬保护和状态流转后再人工复制。",
        ),
        build_configuration_wizard_step(
            step_id="template_index",
            title="对比模板",
            status=(config_template_index or {}).get("status") or "missing",
            blocking=True,
            description="索引一个或两个草案目录，预览 profile 切换和团队模板差异。",
            source_version=(config_template_index or {}).get("version") or "",
            artifacts=["harness_config_template_index.json", "harness_config_template_index.md"],
            confirmations=[
                f"来源数：{(config_template_index or {}).get('source_count') or 0}",
                f"变化计数：{((config_template_index or {}).get('diff_summary') or {}).get('change_count') or 0}",
                f"对比目录：{compare_draft_input_dir or '-'}",
            ],
            next_action="多人共享前先看 provider、credential key、评论模板、状态流转和路径状态差异。",
        ),
        build_configuration_wizard_step(
            step_id="manual_confirmation",
            title="人工复制前确认",
            status="manual_required",
            blocking=False,
            description="最后一步只给人工确认清单，不自动写入 ~/.his-harness，不保存真实凭证。",
            source_version="0.31-configuration-wizard",
            artifacts=["harness_config_wizard.json", "harness_config_wizard.md"],
            confirmations=[
                "不会应用配置",
                "不会写入 ~/.his-harness",
                "不会保存真实 token",
                "不会测试远端账号",
            ],
            next_action="人工确认无误后，再由用户按团队约定复制到个人配置位置。",
        ),
    ]


def build_configuration_wizard_step(
    *,
    step_id: str,
    title: str,
    status: object,
    blocking: bool,
    description: str,
    source_version: str,
    artifacts: list[str],
    confirmations: list[str],
    next_action: str,
) -> dict:
    return {
        "id": step_id,
        "title": title,
        "status": normalize_wizard_status(status),
        "blocking": blocking,
        "readonly": True,
        "description": description,
        "source_version": source_version,
        "artifacts": artifacts,
        "confirmations": confirmations,
        "next_action": next_action,
        "search_text": " ".join(
            [
                step_id,
                title,
                normalize_wizard_status(status),
                description,
                source_version,
                " ".join(str(item) for item in artifacts),
                " ".join(str(item) for item in confirmations),
                next_action,
            ]
        ),
    }


def normalize_wizard_status(value: object) -> str:
    text = str(value if value is not None else "").strip().lower()
    if text in {"pass", "passed", "success", "created", "blocked_existing_files", "manual_ready", "manual_required"}:
        return text
    if text in {"failed", "error", "missing"}:
        return text
    return text or "missing"


def build_configuration_wizard_manual_checklist(
    *,
    config_summary: dict,
    config_import_review: dict | None,
    config_template_index: dict | None,
) -> list[dict]:
    profile = config_summary.get("profile") or {}
    provider = config_summary.get("providers") or {}
    credentials = config_summary.get("credentials") or {}
    review_profile = ((config_import_review or {}).get("profiles") or {}).get("active_profile") or {}
    return [
        {"key": "provider_type", "label": "需求来源 provider 类型", "value": provider.get("active_requirement_source") or review_profile.get("requirement_provider_type") or "", "required": True, "confirmed_by_harness": False},
        {"key": "profile_key", "label": "Profile Key", "value": profile.get("key") or review_profile.get("key") or "", "required": True, "confirmed_by_harness": False},
        {"key": "project_root", "label": "项目根目录", "value": profile.get("project_root") or review_profile.get("project_root") or "", "required": True, "confirmed_by_harness": False},
        {"key": "output_root", "label": "Harness 输出目录", "value": profile.get("output_root") or review_profile.get("output_root") or "", "required": True, "confirmed_by_harness": False},
        {"key": "credential_keys", "label": "凭证 Key 名称", "value": [item.get("key") or "" for item in credentials.get("items") or [] if isinstance(item, dict)], "required": True, "confirmed_by_harness": False},
        {"key": "template_change_count", "label": "模板差异变化数", "value": ((config_template_index or {}).get("diff_summary") or {}).get("change_count") or 0, "required": False, "confirmed_by_harness": False},
        {"key": "readonly_boundary", "label": "确认不会应用配置、不会写真实配置目录、不会保存真实 token", "value": True, "required": True, "confirmed_by_harness": False},
    ]


def build_configuration_wizard_copy_commands(*, draft_input_dir: str, compare_draft_input_dir: str) -> list[dict]:
    safe_draft_dir = sh_quote(draft_input_dir or "/tmp/his_harness_config_import_drafts")
    safe_compare_dir = sh_quote(compare_draft_input_dir) if compare_draft_input_dir else ""
    config_command = (
        "python3 tools/config_check.py --profile-key dfhis-local-example "
        f"--include-config-wizard --draft-input-dir {safe_draft_dir} "
        "--output-dir /tmp/his_harness_config_wizard"
    )
    workspace_command = (
        "python3 tools/task_manager.py workspace --limit 50 --profile-key dfhis-local-example "
        f"--include-config-wizard --draft-input-dir {safe_draft_dir} "
        "--output-dir /tmp/his_harness_task_workspace_config_wizard"
    )
    if safe_compare_dir:
        config_command += f" --compare-draft-input-dir {safe_compare_dir}"
        workspace_command += f" --compare-draft-input-dir {safe_compare_dir}"
    return [
        {"key": "config_check_wizard", "label": "导出配置向导", "command": config_command, "copy_target_id": "wizard-command-config-check"},
        {"key": "workspace_config_wizard", "label": "导出工作台配置向导", "command": workspace_command, "copy_target_id": "wizard-command-workspace"},
    ]


def build_configuration_wizard_readability(*, steps: list[dict], blocking_steps: list[dict], copy_commands: list[dict]) -> dict:
    statuses = sorted(unique_keep_order([step.get("status") or "" for step in steps if isinstance(step, dict)]))
    blocking_count = len(blocking_steps)
    manual_required_count = len([step for step in steps if step.get("status") == "manual_required"])
    return {
        "version": "0.31-configuration-wizard-readability",
        "readonly": True,
        "step_filter_options": {
            "statuses": statuses,
            "blocking_modes": ["all", "blocking", "non_blocking"],
            "search_fields": ["id", "title", "status", "description", "confirmations", "artifacts", "next_action"],
        },
        "step_summary": {
            "total_step_count": len(steps),
            "blocked_step_count": blocking_count,
            "manual_required_step_count": manual_required_count,
            "non_blocking_step_count": len([step for step in steps if not step.get("blocking")]),
            "command_count": len(copy_commands),
        },
        "blocked_step_count": blocking_count,
        "manual_required_step_count": manual_required_count,
        "command_copy_targets": [
            {
                "key": command.get("key") or "",
                "label": command.get("label") or "",
                "copy_target_id": command.get("copy_target_id") or f"wizard-command-{index}",
                "command": command.get("command") or "",
            }
            for index, command in enumerate(copy_commands, start=1)
        ],
        "empty_states": [
            {"kind": "no_matching_steps", "message": "当前筛选条件下没有配置向导步骤。"},
            {"kind": "no_blocking_steps", "message": "当前向导没有阻断步骤，但仍需要人工确认路径、凭证 key 和只读边界。"},
            {"kind": "copy_unavailable", "message": "浏览器不支持自动复制时，请手动选择命令文本复制。"},
        ],
        "html_markers": ["wizard-step-search", "wizard-status-filter", "wizard-blocking-filter", "copyWizardCommand"],
    }


def sh_quote(value: str) -> str:
    text = str(value or "")
    if not text:
        return "''"
    if re.match(r"^[A-Za-z0-9_@%+=:,./-]+$", text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def configuration_wizard_to_markdown(wizard: dict) -> str:
    lines = [
        "# Harness 配置向导",
        "",
        f"- 版本：{wizard.get('version')}",
        f"- 状态：{wizard.get('status')}",
        f"- 只读：{wizard.get('readonly')}",
        f"- 草案目录：`{wizard.get('draft_input_dir') or '-'}`",
        f"- 会应用配置：{wizard.get('will_apply_configuration')}",
        f"- 写真实配置目录：{wizard.get('will_write_real_config_dir')}",
        f"- 远端连通测试：{wizard.get('remote_connection_tests_enabled')}",
        "",
        "本向导只整合配置摘要、Provider 模板、分享校验、生成草案、回读校验和对比模板结果，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。",
        "",
        "## 可读性增强",
        "",
    ]
    readability = wizard.get("ui_readability") or {}
    filter_options = readability.get("step_filter_options") or {}
    step_summary = readability.get("step_summary") or {}
    lines.extend(
        [
            f"- 步骤筛选：状态={', '.join(filter_options.get('statuses') or []) or '-'}；阻断模式={', '.join(filter_options.get('blocking_modes') or []) or '-'}",
            f"- 阻断摘要：阻断步骤={step_summary.get('blocked_step_count') or 0}，需人工确认={step_summary.get('manual_required_step_count') or 0}，总步骤={step_summary.get('total_step_count') or 0}",
            f"- 命令复制：{step_summary.get('command_count') or len(wizard.get('copy_commands') or [])} 条命令可在 HTML 工作台中复制；复制动作不执行命令。",
            "",
        ]
    )
    lines.extend(
        [
        "## 向导步骤",
        "",
        ]
    )
    for index, step in enumerate(wizard.get("steps") or [], start=1):
        lines.extend(
            [
                f"### {index}. {step.get('title') or '-'}",
                "",
                f"- ID：`{step.get('id') or '-'}`",
                f"- 状态：{step.get('status') or '-'}",
                f"- 阻断：{step.get('blocking')}",
                f"- 来源版本：{step.get('source_version') or '-'}",
                f"- 说明：{step.get('description') or '-'}",
                f"- 下一步：{step.get('next_action') or '-'}",
                "- 确认点：",
            ]
        )
        for item in step.get("confirmations") or []:
            lines.append(f"  - {item}")
        lines.append("- 产物：")
        for artifact in step.get("artifacts") or []:
            lines.append(f"  - `{artifact}`")
        lines.append("")
    lines.extend(["## 复制命令", ""])
    for item in wizard.get("copy_commands") or []:
        lines.append(f"- {item.get('label') or item.get('key') or '-'}：`{item.get('command') or '-'}`")
    lines.extend(["", "## 人工确认清单", ""])
    for item in wizard.get("manual_checklist") or []:
        lines.append(f"- {item.get('key') or '-'}：{item.get('label') or '-'}，值：`{item.get('value')}`，Harness 已确认：{item.get('confirmed_by_harness')}")
    lines.extend(["", "## 风险提示", ""])
    for prompt in wizard.get("risk_prompts") or []:
        lines.append(f"- {prompt}")
    lines.extend(["", "## 兼容边界", ""])
    for note in (wizard.get("compatibility") or {}).get("notes") or []:
        lines.append(f"- {note}")
    lines.extend(["", "## Residual Risk", "", f"- {wizard.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def write_configuration_wizard_outputs(*, output_dir: str | Path, wizard: dict) -> dict:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "harness_config_wizard.json"
    markdown_path = target_dir / "harness_config_wizard.md"
    json_path.write_text(json.dumps(wizard, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(configuration_wizard_to_markdown(wizard), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def unique_keep_order(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
