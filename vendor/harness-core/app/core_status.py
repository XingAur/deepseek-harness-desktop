from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from app import database
from app.capability_registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityRegistryError,
)
from app.plugin_inventory import (
    PluginInventoryError,
    load_plugin_inventory,
    verify_plugin_inventory,
)
from app.model_worker_smoke import (
    MODEL_WORKER_SMOKE_READINESS_SCHEMA_VERSION,
    build_model_worker_smoke_readiness,
)
from app.manager_model_smoke_preflight import build_model_smoke_preflight
from app.runtime_policy import runtime_policy_snapshot
from app.external_write_plan import EXTERNAL_WRITE_PLAN_SCHEMA_VERSION
from tools.capability_check import CliError, load_runtime_config


CORE_VERSION = "0.66.0"
CORE_STATUS_SCHEMA_VERSION = "his-core-status.v1"
READINESS_SCHEMA_VERSION = "his-readiness.v1"
DEFAULT_HARNESS_ROOT = Path(__file__).resolve().parents[1]
LEARNING_CAPABILITIES = (
    "knowledge.candidate.create",
    "knowledge.candidate.review",
    "knowledge.item.promote",
)
WRITE_CAPABILITIES = (
    "database.change",
    "git.push",
    "gitlab.write",
    "github.write",
    "workitem.write",
)
ENABLED_HIGH_RISK_ALLOWLIST = ("git.push", "gitlab.write", "github.write")


def _descriptor_payload(descriptor: CapabilityDescriptor) -> dict[str, Any]:
    return {
        "name": descriptor.name,
        "provider": descriptor.provider,
        "plugin": descriptor.plugin,
        "plugin_version": descriptor.plugin_version,
        "contract_version": descriptor.contract_version,
        "mutation_level": descriptor.mutation_level.name,
        "credential_class": descriptor.credential_class,
        "enabled": descriptor.enabled,
        "disabled_reason": descriptor.disabled_reason,
        "scopes": list(descriptor.scopes),
    }


def _blocked(snapshot: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    snapshot["status"] = "blocked"
    snapshot["blockers"] = [{"code": code, "message": message}]
    return snapshot


def _capability_by_name(
    descriptors: tuple[CapabilityDescriptor, ...],
) -> dict[str, CapabilityDescriptor]:
    return {item.name: item for item in descriptors}


def _capability_names(
    descriptors: dict[str, CapabilityDescriptor],
    names: tuple[str, ...],
) -> list[str]:
    return [name for name in names if name in descriptors]


def _capability_disabled_reasons(
    descriptors: dict[str, CapabilityDescriptor],
    names: tuple[str, ...],
) -> dict[str, str]:
    return {
        name: descriptors[name].disabled_reason
        for name in names
        if name in descriptors and not descriptors[name].enabled
    }


def _knowledge_home_state(knowledge_home: str) -> dict[str, Any]:
    path = Path(knowledge_home)
    vault = path / "vault"
    sqlite_path = path / "knowledge.sqlite"
    home_exists = path.is_dir()
    vault_exists = vault.is_dir()
    sqlite_exists = sqlite_path.is_file()
    if home_exists and vault_exists and sqlite_exists:
        state = "ready"
    elif home_exists:
        state = "partial"
    else:
        state = "missing"
    return {
        "id": "knowledge_home",
        "title": "知识库与 Obsidian",
        "state": state,
        "capabilities": ["knowledge.retrieve", "knowledge.answer", *LEARNING_CAPABILITIES],
        "prerequisites": [
            {"id": "home_exists", "status": "passed" if home_exists else "missing"},
            {"id": "obsidian_vault_exists", "status": "passed" if vault_exists else "missing"},
            {"id": "sqlite_index_exists", "status": "passed" if sqlite_exists else "missing"},
        ],
        "verification": {
            "status": "passed" if state == "ready" else state,
            "method": "filesystem_metadata_only",
        },
        "next_actions": (
            []
            if state == "ready"
            else [
                "创建知识库目录、Obsidian vault 和 knowledge.sqlite。",
                "显式导入 seed 或通过 candidate 审核推广正式知识。",
            ]
        ),
        "manager_ui": {
            "card": "knowledge_home",
            "fields": ["state", "home", "vault", "sqlite_index", "last_seed_import"],
        },
    }


def _build_readiness(
    *,
    config: Any,
    registry: CapabilityRegistry,
    manager_status: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    descriptors = _capability_by_name(registry.descriptors)
    runtime_policy = runtime_policy_snapshot()
    model_worker_smoke = build_model_worker_smoke_readiness()
    manager_model_preflight = build_model_smoke_preflight(None)
    real_model_state = (
        "single_node_smoke_ready"
        if runtime_policy.real_model_smoke_allowed
        else "frozen" if runtime_policy.real_model_runtime_frozen else "smoke_ready"
    )
    learning_capabilities = _capability_names(descriptors, LEARNING_CAPABILITIES)
    learning_ready = all(
        descriptors[name].enabled for name in LEARNING_CAPABILITIES if name in descriptors
    ) and len(learning_capabilities) == len(LEARNING_CAPABILITIES)
    disabled_write_reasons = _capability_disabled_reasons(descriptors, WRITE_CAPABILITIES)
    external_write_capabilities = _capability_names(descriptors, WRITE_CAPABILITIES)
    external_writes_disabled = (
        not config.external_writes_default
        and len(external_write_capabilities) == len(WRITE_CAPABILITIES)
        and all(not descriptors[name].enabled for name in WRITE_CAPABILITIES if name in descriptors)
    )
    manager_evaluated = isinstance(manager_status, Mapping)
    business_accepted = bool(manager_evaluated and manager_status.get("business_valid") is True)
    business_state = (
        "accepted"
        if business_accepted
        else "not_accepted" if manager_evaluated else "not_evaluated"
    )
    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "verification_levels": _verification_levels(manager_status),
        "items": [
            {
                "id": "real_model_worker",
                "title": "真实模型 Worker",
                "state": real_model_state,
                "capabilities": [],
                "smoke_contract_schema": MODEL_WORKER_SMOKE_READINESS_SCHEMA_VERSION,
                "smoke_readiness": model_worker_smoke,
                "manager_smoke_preflight": manager_model_preflight,
                "prerequisites": [
                    {
                        "id": "runtime_unfrozen",
                        "status": "blocked" if runtime_policy.real_model_runtime_frozen else "passed",
                    },
                    {"id": "single_node_smoke_contract", "status": "passed"},
                    {"id": "single_node_smoke", "status": "not_run"},
                    {"id": "redacted_audit", "status": "not_run"},
                ],
                "verification": {
                    "status": "not_run",
                    "method": "controlled_single_node_smoke_required",
                },
                "next_actions": [
                    "保持真实模型 DAG 冻结。",
                    "后续只允许做双开关、固定 prompt、脱敏审计的单节点 smoke。",
                ],
                "manager_ui": {
                    "card": "real_model_worker",
                    "fields": ["state", "runtime_policy", "last_smoke", "blockers"],
                },
            },
            {
                "id": "learning_loop",
                "title": "自动学习闭环",
                "state": "candidate_only" if learning_ready else "blocked",
                "capabilities": learning_capabilities,
                "prerequisites": [
                    {
                        "id": "candidate_create_review_promote_capabilities",
                        "status": "passed" if learning_ready else "missing",
                    },
                    {"id": "auto_promote_disabled", "status": "passed"},
                    {"id": "failed_sample_to_candidate_contract", "status": "passed"},
                ],
                "verification": {
                    "status": "partial",
                    "method": "candidate_capabilities_present_without_auto_promote",
                },
                "next_actions": [
                    "把失败样本沉淀为 eval、contract plugin、rule draft 或 knowledge candidate。",
                    "candidate 必须人工 review/promote，不能自动晋升正式知识。",
                ],
                "manager_ui": {
                    "card": "learning_loop",
                    "fields": ["state", "candidate_count", "review_pending", "last_promotion"],
                },
            },
            {
                "id": "business_acceptance",
                "title": "真实业务验收",
                "state": business_state,
                "capabilities": [],
                "prerequisites": [
                    {"id": "business_acceptance_contract", "status": "passed"},
                    {"id": "his_test_environment", "status": "passed" if business_accepted else "missing"},
                    {"id": "test_account", "status": "passed" if business_accepted else "missing"},
                    {"id": "test_data", "status": "passed" if business_accepted else "missing"},
                    {"id": "manual_or_runtime_evidence", "status": "passed" if business_accepted else "missing"},
                ],
                "verification": {
                    "status": business_state,
                    "business_valid": business_accepted,
                    "runtime_verified": business_accepted,
                    "method": "explicit_business_evidence_required",
                },
                "next_actions": [
                    "增加结构化业务验收证据入口。",
                    "离线 enterprise gate 通过仍只能代表 technical_valid=true。",
                ],
                "manager_ui": {
                    "card": "business_acceptance",
                    "fields": ["state", "environment", "account", "test_data", "evidence"],
                },
            },
            {
                "id": "external_writes",
                "title": "外部写动作",
                "state": "disabled" if external_writes_disabled else "review_required",
                "capabilities": external_write_capabilities,
                "enabled_allowlist": [
                    name
                    for name in ENABLED_HIGH_RISK_ALLOWLIST
                    if name in descriptors and descriptors[name].enabled
                ],
                "disabled_reasons": disabled_write_reasons,
                "dry_run_plan_schema": EXTERNAL_WRITE_PLAN_SCHEMA_VERSION,
                "prerequisites": [
                    {
                        "id": "external_writes_default_false",
                        "status": "passed" if not config.external_writes_default else "blocked",
                    },
                    {"id": "dry_run_transaction_plan", "status": "passed"},
                    {"id": "test_object_write_acceptance", "status": "missing"},
                    {"id": "explicit_user_confirmation", "status": "missing"},
                ],
                "verification": {
                    "status": "blocked_by_policy",
                    "method": "manifest_high_risk_allowlist_and_per_execution_confirmation",
                },
                "next_actions": [
                    "先做事务计划、dry-run 和测试对象链路。",
                    "仅 Git push、GitLab 写和 GitHub 写可进入一次性确认；其他 L4/L5 保持禁用。",
                ],
                "manager_ui": {
                    "card": "external_writes",
                    "fields": ["state", "capability", "disabled_reason", "last_dry_run"],
                },
            },
            _knowledge_home_state(config.knowledge_home),
        ],
    }


def _verification_levels(
    manager_status: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    """Keep implementation, configuration and verification claims separate."""
    # Core status is deliberately read-only and must not initialize or migrate
    # the Manager database.  Manager-specific counts and records remain on the
    # dedicated localhost Manager APIs.
    manager_evaluated = isinstance(manager_status, Mapping)
    provider_profile_count = (
        manager_status.get("provider_profile_count")
        if manager_evaluated
        else 0
    )
    configured = (
        isinstance(provider_profile_count, int)
        and not isinstance(provider_profile_count, bool)
        and provider_profile_count > 0
    )
    business_accepted = bool(
        manager_evaluated and manager_status.get("business_valid") is True
    )
    return [
        {
            "id": "code_ready",
            "label": "代码就绪",
            "state": "ready",
            "evidence": "core_contract_loaded",
        },
        {
            "id": "configured",
            "label": "配置完成",
            "state": (
                "configured"
                if configured
                else "not_configured" if manager_evaluated else "not_evaluated"
            ),
            "boundary": "由 /api/manager/providers 单独报告，不由 core status 推断。",
        },
        {
            "id": "locally_tested",
            "label": "本地测试",
            "state": "not_recorded",
            "boundary": "测试结果必须由当前代码版本的专项测试证据单独记录。",
        },
        {
            "id": "externally_verified",
            "label": "外部验证",
            "state": "not_verified",
            "boundary": "Provider 连通、smoke 或本地测试均不能替代外部回读验证。",
        },
        {
            "id": "business_accepted",
            "label": "业务验收",
            "state": (
                "accepted"
                if business_accepted
                else "not_accepted" if manager_evaluated else "not_evaluated"
            ),
            "boundary": "由版本化证据与追加审核决定单独证明。",
        },
    ]


def build_core_status_snapshot(
    *,
    harness_root: Path = DEFAULT_HARNESS_ROOT,
    database_path: Path | None = None,
    capability_config_path: Path | None = None,
    plugin_inventory_path: Path | None = None,
    manager_status: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    root = harness_root.expanduser().resolve()
    config_path = (capability_config_path or root / "config" / "capabilities.json").resolve()
    inventory_path = (plugin_inventory_path or root / "config" / "plugin_inventory.json").resolve()
    snapshot: dict[str, Any] = {
        "schema_version": CORE_STATUS_SCHEMA_VERSION,
        "status": "blocked",
        "core_version": CORE_VERSION,
        "routing_mode": "",
        "external_writes_default": False,
        "plugin_inventory": {"schema_version": "", "sha256": "", "verified": False},
        "plugins": [],
        "capabilities": [],
        "runtime_policy": runtime_policy_snapshot().to_dict(),
        "database": (
            database.database_read_only_health_snapshot(database_path)
            if database_path is not None
            else {"status": "not_probed", "reason": "database_path_not_explicit"}
        ),
        "readiness": {"schema_version": READINESS_SCHEMA_VERSION, "items": []},
        "credentials_read": False,
        "external_calls": False,
        "blockers": [],
    }
    try:
        config = load_runtime_config(str(config_path))
        if capability_config_path is None:
            sibling_parent = root.parent / "plugins"
            sibling_roots = tuple(
                sibling_parent / Path(configured_root).name
                for configured_root in config.plugin_roots
            )
            if sibling_roots and all(path.is_dir() for path in sibling_roots):
                config = replace(
                    config,
                    plugin_roots=tuple(str(path) for path in sibling_roots),
                )
        registry = CapabilityRegistry.from_plugin_roots(config.plugin_roots)
        inventory = load_plugin_inventory(inventory_path)
        verify_plugin_inventory(inventory_path, list(config.plugin_roots), registry=registry)
        inventory_digest = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    except CliError:
        return _blocked(snapshot, "core_config_invalid", "Core capability 运行配置无效。")
    except CapabilityRegistryError:
        return _blocked(snapshot, "capability_registry_invalid", "Core capability registry 无法验证。")
    except PluginInventoryError:
        return _blocked(snapshot, "plugin_inventory_invalid", "Core 插件冻结清单无法验证。")
    except OSError:
        return _blocked(snapshot, "core_status_unavailable", "Core 状态文件无法安全读取。")

    snapshot["status"] = "ready"
    snapshot["routing_mode"] = config.routing_mode
    snapshot["external_writes_default"] = config.external_writes_default
    snapshot["plugin_inventory"] = {
        "schema_version": inventory.schema_version,
        "sha256": inventory_digest,
        "verified": True,
    }
    snapshot["plugins"] = [
        {
            "name": item.name,
            "version": item.version,
            "capability_count": len(item.capabilities),
            "capabilities_sha256": item.capabilities_sha256,
        }
        for item in inventory.plugins
    ]
    snapshot["capabilities"] = [
        _descriptor_payload(item)
        for item in sorted(
            registry.descriptors,
            key=lambda value: (value.plugin, value.name, value.provider),
        )
    ]
    snapshot["readiness"] = _build_readiness(
        config=config,
        registry=registry,
        manager_status=manager_status,
    )
    return snapshot
