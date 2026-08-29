from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from app.acceptance_matrix import build_acceptance_matrix, build_prompt_context, matrix_to_json, matrix_to_markdown
from app.acceptance_contracts import AcceptanceContractResult, execute_acceptance_contract
from app import database
from app.demand_progress import (
    build_demand_progress_snapshot,
    demand_progress_to_markdown,
)
from app.capability_contracts import (
    RESULT_SCHEMA_VERSION,
    CapabilityAuthorization,
    CapabilityRequest,
    MutationLevel,
)
from app.clarification_gate import evaluate_patch_readiness
from app.change_ownership import build_change_ownership_matrix
from app.core_closure import (
    CoreClosureResult,
    DiffReview,
    RequirementContract,
    build_core_closure_result,
    build_engineering_handoff,
    build_requirement_contract,
    build_requirement_contract_from_single_pass,
    core_closure_to_markdown,
    review_final_diff,
    validate_requirement_governance_outputs,
)
from app.evaluator import EvaluationResult, Evaluator
from app.fast_local import build_fast_local_decision
from app.fullstack_executor import (
    FullstackExecutionOptions,
    FullstackExecutionResult,
    FullstackWorktreeExecutor,
    validate_authoritative_fullstack_options,
)
from app.llm_client import BaseLLMClient, describe_mode, get_llm_client, redact_secrets
from app.multi_service_change_contract import (
    MULTI_SERVICE_CHANGE_CONTRACT_SCHEMA_VERSION,
    build_multi_service_change_contract,
    discover_runtime_validation,
    suggest_runtime_commands,
)
from app.multi_service_executor import (
    MultiServiceExecutionOptions,
    MultiServiceWorktreeExecutor,
)
from app.precommit_verifier import PrecommitVerificationOptions, PrecommitVerificationResult, PrecommitVerifier
from app.project_context import EvidenceBundle, ProjectContextScanner, load_project_profile
from app.runtime_preflight import choose_private_runtime_root, run_runtime_preflight
from app.requirement_calibration import (
    build_requirement_calibration,
    requirement_calibration_to_json,
    requirement_calibration_to_markdown,
    requirement_calibration_to_prompt_context,
)
from app.requirement_provider import normalize_requirement_evidence, normalize_requirement_evidence_file, requirement_evidence_to_markdown
from app.conversation_evidence import conversation_code_locator_text, conversation_evidence_to_markdown, load_conversation_evidence_file
from app.error_chain_closure import build_error_chain_closure, error_chain_closure_to_markdown
from app.requirement_understanding import build_requirement_understanding
from app.visual_evidence import VisualEvidenceAnalyzer, analyze_requirement_visual_evidence
from app.review_executor import ReviewExecutionOptions, ReviewExecutionResult, ReviewWorktreeExecutor, build_review_context
from app.worktree_lifecycle import inspect_worktree_root
from app.single_demand_trial import SingleDemandTrialPackage, build_single_demand_trial_package
from app.scope_confirmation import (
    build_scope_confirmation_binding,
    scope_confirmation_to_markdown,
    validate_scope_confirmation,
)
from app.task_capability_routing import route_task_capabilities
from app.task_intent_router import IntentContext
from app.task_intent_service import (
    TaskIntentRoutingResult,
    TaskIntentService,
    require_requirement_workflow_route,
)
from app.technical_decision import (
    DEFAULT_PROJECT_ROOT,
    TechnicalDecisionResult,
    build_technical_decision,
    remove_generated_analysis_appendices,
)
from app.worktree_executor import (
    DEFAULT_WORKTREE_ROOT,
    WorktreeCodeExecutor,
    WorktreeExecutionOptions,
    WorktreeExecutionResult,
    apply_final_diff_to_project,
)
from app.yunxiao_read import build_yunxiao_prompt_context, collect_yunxiao_evidence, parse_work_item_id
from app.yunxiao_transaction import (
    HIGH_RISK_TERMS,
    YunxiaoEntityRef,
    YunxiaoTransactionManager,
    build_yunxiao_transaction_plan,
    load_yunxiao_policy,
    transaction_plan_to_markdown,
)


TEAM_KEY = "his_requirement_workflow"
HARNESS_VERSION = "0.58-enterprise-core-stabilization"
DEFAULT_MAX_RETRIES = 2
GOVERNANCE_VALIDATION_ERROR = "需求治理输出未通过完整结构校验，enforce 模式禁止进入执行阶段。"
GOVERNANCE_BOUNDARY_ERROR = "能力治理契约超出本地安全合同边界，禁止进入执行阶段。"
GOVERNANCE_MODE_ERROR = "能力治理返回了未知路由模式，禁止进入执行阶段。"
GOVERNANCE_ROUTE_ERROR = "需求治理能力路由未通过，禁止进入执行阶段。"
GOVERNANCE_ACCEPTANCE_ERROR = "能力治理契约缺少必需的验收命令，禁止进入执行阶段。"
MUTATING_EXECUTION_MODES = frozenset(
    {
        "worktree",
        "review-worktree",
        "fullstack-worktree",
        "precommit-verify",
        "single-demand-trial",
        "core-closure-trial",
    }
)


def _multi_service_contract_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "## 多项目改动合同",
        "",
        f"- schema：`{payload.get('schema_version') or '-'}`",
        f"- 状态：`{payload.get('status') or '-'}`",
        f"- 是否允许写回：`{'是' if (payload.get('rollback') or {}).get('status') == 'ready' else '否'}`",
        f"- 目标数：`{len(payload.get('targets') or [])}`",
        "",
        "### 阻断项",
        "",
    ]
    blockers = payload.get("blockers") or []
    lines.extend(f"- {item}" for item in blockers) if blockers else lines.append("- 无")
    lines.extend(["", "### 证据缺口与用户选择", ""])
    gaps = payload.get("evidence_gaps") or []
    options = payload.get("evidence_options") or []
    if not gaps:
        lines.append("- 无证据缺口。")
    for gap in gaps:
        lines.append(f"- 缺口 `{gap.get('id') or '-'}`：{gap.get('question') or gap.get('reason') or '-'}")
    for option in options:
        lines.append(f"- 选项 `{option.get('id') or '-'}`：{option.get('label') or '-'}；{option.get('action') or '-'}")
        candidates = option.get("candidate_commands_by_project") or {}
        for project_name, commands in candidates.items():
            lines.append(f"  - `{project_name}` 候选命令：{', '.join(commands) or '-'}（需用户选择并验证）")
    architecture = payload.get("architecture_decision") or {}
    if architecture:
        lines.extend(["", "### 服务架构判断", ""])
        lines.append(f"- 状态：`{architecture.get('status') or '-'}`")
        lines.append(f"- 推荐方案：`{architecture.get('recommended_option_id') or '-'}`")
        if architecture.get("status") == "auto_resolved":
            lines.append("- 结论：已由本地构建文件和公共 API 证据自动确定，不要求用户重复提供服务关系。")
        elif architecture.get("status") == "needs_user_choice":
            lines.append("- 结论：证据不足以区分方案，必须先选择并补充 API/服务证据。")
        for requirement in architecture.get("requirements") or []:
            lines.append(
                f"- 架构目标 `{requirement.get('id') or '-'}`："
                f"{requirement.get('label') or '-'}；接口契约状态=`{requirement.get('endpoint_contract_status') or '-'}`。"
            )
            for surface in requirement.get("change_surfaces") or []:
                lines.append(f"  - 改动面：{surface}")
            for api_type, routes in (requirement.get("existing_api_candidates") or {}).items():
                if routes:
                    lines.append(f"  - 已有 {api_type} API 候选证据：{', '.join(f'`{route}`' for route in routes[:8])}")
    continuation = payload.get("continuation") or {}
    lines.extend(["", "### 自动继续策略", ""])
    lines.append(
        f"- 继续状态：`{continuation.get('status') or '-'}`；"
        f"默认：`{continuation.get('default') or '-'}`；"
        f"是否需要用户：{'是' if continuation.get('requires_user', True) else '否'}。"
    )
    lines.append(f"- 下一步：{continuation.get('next_action') or '-'}")
    lines.append(f"- 安全边界：{continuation.get('reason') or '-'}")
    lines.extend(["", "### 按仓库边界", ""])
    repositories = payload.get("repositories") or {}
    if not repositories:
        lines.append("- 未形成可执行仓库合同。")
    for name, repo in repositories.items():
        lines.append(f"- `{name}`：允许路径={', '.join(repo.get('allowed_paths') or []) or '-'}；验证命令={'; '.join(repo.get('verify_commands') or []) or '-'}")
    rollback = payload.get("rollback") or {}
    lines.extend(["", "### 回退边界", "", f"- {rollback.get('strategy') or '-'}"])
    return "\n".join(lines)


def _submitted_evidence_list(payload: Mapping[str, Any] | None, key: str) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    value = payload.get(key)
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _submitted_evidence_mapping(payload: Mapping[str, Any] | None, key: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    value = payload.get(key)
    return dict(value) if isinstance(value, Mapping) else {}
TASK_CAPABILITY_SEQUENCE = (
    "intake",
    "provider_evidence",
    "calibration",
    "technical_decision",
    "ownership",
    "acceptance",
    "understanding",
    "governance",
    "single_pass_contract",
    "local_engineering",
    "verification",
    "knowledge_candidate",
    "audit",
)
TASK_STAGE_REASONS = {
    "intake_validated": "任务输入与执行模式已完成本地校验。",
    "provider_evidence_loaded": "已读取显式授权的只读需求证据。",
    "provider_evidence_not_requested": "本次任务未请求外部或本地需求证据。",
    "calibration_generated": "已生成需求理解确认卡。",
    "technical_decision_completed": "已完成技术边界与可修改性判断。",
    "ownership_generated": "已生成需求变更归属矩阵。",
    "acceptance_generated": "已生成自动与人工验收矩阵。",
    "understanding_ready": "业务背景、场景、项目入口、调用链、影响范围和验证基线已形成改码前证据包。",
    "understanding_blocked": "改码前理解证据不足，只允许继续只读调查，不进入本地工程修改。",
    "governance_blocked": "需求治理未 ready，执行链已在本地工程入口前阻断。",
    "governance_completed": "治理模式已完成评估，未触发执行阻断。",
    "analysis_complete_mutation_gate_closed": "只读分析已完成；改码门禁保持关闭。",
    "contract_governance_blocked": "治理阻断，未生成可执行的一次变更契约。",
    "contract_validated": "一次变更契约已通过完整结构校验。",
    "contract_unavailable": "当前治理模式未提供可执行的一次变更契约。",
    "core_contract_ready": "核心闭环需求契约已通过结构校验。",
    "core_contract_blocked": "核心闭环需求契约未 ready，后续工程阶段将跳过。",
    "local_governance_blocked": "治理阻断，未进入本地工程执行。",
    "local_scope_confirmation_blocked": "改动前范围未确认，未进入本地工程执行。",
    "local_readonly": "readonly 模式不进入本地工程修改。",
    "local_artifact_recorded": "已记录本地工程执行工件。",
    "local_precommit": "提交前验证模式不创建本地改动。",
    "local_upstream_blocked": "上游评估未允许进入本地工程执行。",
    "local_core_blocked": "核心闭环契约或工程交接未 ready，未进入 worktree。",
    "local_core_completed": "核心闭环已执行受控 worktree。",
    "verification_no_change": "未发生本地工程改动，无专项验证可执行。",
    "verification_readonly": "readonly 模式未产生待验证改动。",
    "verification_passed": "本地工程专项验证已完成。",
    "verification_failed": "本地工程专项验证未通过，详见执行工件。",
    "verification_precommit": "已记录提交前专项验证结果。",
    "verification_core_passed": "worktree 专项验证已通过。",
    "verification_core_failed": "worktree 专项验证未通过。",
    "knowledge_write_skipped": "Task 4 只记录候选阶段，不写入或晋升知识候选。",
    "knowledge_candidate_created": "已创建本地知识候选；未审核、未晋升。",
    "knowledge_candidate_blocked": "知识候选能力已阻断，已转为稳定任务阻断项。",
    "audit_saved": "已保存固定顺序的任务阶段账本。",
}
QUESTION_ANSWER_STATUSES = frozenset(
    {
        "answered",
        "needs_live_evidence",
        "needs_clarification",
        "conflicted",
        "unsupported",
    }
)
_READONLY_EVIDENCE_CAPABILITIES = {
    "workitem.read": ("yunxiao", MutationLevel.L1),
    "database.inspect": ("postgresql", MutationLevel.L1),
    "git.inspect": ("his-engineering", MutationLevel.L0),
}
_KNOWLEDGE_EVIDENCE_FIELDS = (
    "stable_key",
    "title",
    "authority",
    "version_label",
    "source_refs",
    "excerpt",
)
_KNOWLEDGE_SOURCE_REF_FIELDS = (
    "claim_level",
    "ref",
    "kind",
    "path",
    "url",
    "version",
    "commit",
)
_KNOWLEDGE_SENSITIVE_TEXT = re.compile(
    r"\b("
    r"authorization|cookie|credential|dsn|password|secret|token|"
    r"api[_ -]?key|access[_ -]?key|private[_ -]?key|pat"
    r")\b\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CapabilityWorkflowResult:
    status: str
    events: tuple[str, ...]
    data: Mapping[str, Any]


def _unsupported_question_result() -> CapabilityWorkflowResult:
    return CapabilityWorkflowResult("unsupported", ("knowledge.answer",), {})


def _public_knowledge_text(value: object, *, required: bool) -> str | None:
    if not isinstance(value, str):
        return None
    text = redact_secrets(value.strip())
    text = _KNOWLEDGE_SENSITIVE_TEXT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    if required and not text:
        return None
    return text[:4000]


def _public_knowledge_source_refs(value: object) -> list[dict[str, str]] | None:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or len(value) > 20
    ):
        return None
    public: list[dict[str, str]] = []
    for raw_ref in value:
        if not isinstance(raw_ref, Mapping):
            return None
        ref: dict[str, str] = {}
        for field in _KNOWLEDGE_SOURCE_REF_FIELDS:
            if field not in raw_ref:
                continue
            text = _public_knowledge_text(raw_ref[field], required=True)
            if text is None:
                return None
            ref[field] = text
        if not ref:
            return None
        public.append(ref)
    return public


def _public_knowledge_evidence(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, (list, tuple)) or len(value) > 20:
        return None
    public: list[dict[str, Any]] = []
    for raw_item in value:
        if not isinstance(raw_item, Mapping):
            return None
        item: dict[str, Any] = {}
        for field in _KNOWLEDGE_EVIDENCE_FIELDS:
            if field == "source_refs":
                refs = _public_knowledge_source_refs(raw_item.get(field))
                if refs is None:
                    return None
                item[field] = refs
                continue
            text = _public_knowledge_text(
                raw_item.get(field),
                required=True,
            )
            if text is None:
                return None
            item[field] = text
        public.append(item)
    return public


def _valid_answered_knowledge_contract(
    data: Mapping[str, Any],
    evidence: list[dict[str, Any]],
) -> bool:
    if not evidence:
        return False
    for field in ("answer", "freshness"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
    for field in ("applicability", "confidence_basis"):
        value = data.get(field)
        if (
            not isinstance(value, (list, tuple))
            or not value
            or len(value) > 100
            or not all(
                isinstance(item, str) and bool(item.strip())
                for item in value
            )
        ):
            return False
    return all(
        bool(item.get("version_label"))
        and bool(item.get("source_refs"))
        for item in evidence
    )


def resolve_capability_routing(
    configured_mode: str,
    requested_mode: str | None,
) -> str:
    """Resolve a CLI routing override without allowing an enforce upgrade."""
    allowed = {"legacy", "observe", "enforce"}
    if configured_mode not in allowed:
        raise ValueError("配置的 capability routing 无效")
    if requested_mode is None:
        return configured_mode
    if requested_mode not in allowed:
        raise ValueError("请求的 capability routing 无效")
    if requested_mode == "enforce" and configured_mode != "enforce":
        raise ValueError("命令行不能升级 capability routing 到 enforce")
    return requested_mode


def _preview_request(
    capability: str,
    provider: str,
    mutation_level: MutationLevel,
    input_data: Mapping[str, Any],
    *,
    request_id: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> CapabilityRequest:
    return CapabilityRequest(
        request_id=request_id or f"{capability.replace('.', '-')}-{uuid4().hex}",
        capability=capability,
        provider=provider,
        mode="preview",
        mutation_level=mutation_level,
        authorization=CapabilityAuthorization(explicit=False, scope=()),
        input=dict(input_data),
        context=dict(context or {}),
    )


def build_workitem_read_request(
    *,
    yunxiao_url: str,
    demand_text: str,
    include_comments: bool,
    request_id: str | None = None,
) -> CapabilityRequest:
    """Build the only Yunxiao request that task orchestration may issue."""
    source = yunxiao_url.strip()
    work_item_id = parse_work_item_id(source or demand_text)
    input_data = {"url": source} if source else {"entity_id": work_item_id}
    if not next(iter(input_data.values()), ""):
        raise ValueError("云效只读能力需要 URL 或工作项 ID")
    return _preview_request(
        "workitem.read",
        "yunxiao",
        MutationLevel.L1,
        input_data,
        request_id=request_id,
        context={"include_comments": bool(include_comments)},
    )


def build_workitem_write_request(
    *,
    entity_kind: str,
    entity_id: str,
    write_scope: str,
    explicitly_authorized: bool,
    request_id: str | None = None,
) -> CapabilityRequest:
    """Build the L4 guard request for the legacy Yunxiao write adapter."""
    return CapabilityRequest(
        request_id=request_id or f"workitem-write-{uuid4().hex}",
        capability="workitem.write",
        provider="yunxiao",
        mode="apply",
        mutation_level=MutationLevel.L4,
        authorization=CapabilityAuthorization(
            explicit=bool(explicitly_authorized),
            scope=(
                "workitem:comment",
                "workitem:transition",
                "workitem:upload",
                "capability:workitem.write",
            ),
        ),
        input={
            "entity_kind": entity_kind.strip(),
            "entity_id": entity_id.strip(),
            "write_scope": write_scope,
        },
        context={"legacy_adapter": True},
    )


def build_requirement_governance_request(
    payload: Mapping[str, Any],
    *,
    request_id: str | None = None,
) -> CapabilityRequest:
    return _preview_request(
        "requirement.govern",
        "his-harness-core",
        MutationLevel.L0,
        payload,
        request_id=request_id,
    )


def _governance_outputs_from_capability_data(
    value: object,
) -> tuple[object | None, object | None]:
    """Rebuild untrusted capability data as exact validated governance models."""
    if not isinstance(value, Mapping):
        return None, None
    try:
        from app.requirement_governance import GovernanceCheck, RequirementGovernanceResult
        from app.single_pass_change_contract import SinglePassChangeContract

        governance_payload = dict(value["governance"])
        contract_payload = dict(value["single_pass_change_contract"])
        checks = []
        for item in _strict_payload_tuple(governance_payload, "checks", dict):
            check = dict(item)
            check["evidence_refs"] = _strict_payload_tuple(check, "evidence_refs", dict, default=())
            for field in ("blockers", "warnings"):
                check[field] = _strict_payload_tuple(check, field, str, default=())
            checks.append(GovernanceCheck(**check))
        governance_payload["checks"] = tuple(checks)
        for field in ("blockers", "missing_information", "unsupported_reasons", "required_capabilities"):
            governance_payload[field] = _strict_payload_tuple(governance_payload, field, str)
        governance_payload["evidence_refs"] = _strict_payload_tuple(governance_payload, "evidence_refs", dict)
        governance = RequirementGovernanceResult(**governance_payload)

        mapping_fields = {"repositories", "business_rules", "database_impacts", "configuration_impacts"}
        for field in (
            "in_scope", "out_of_scope", "repositories", "allowed_paths",
            "business_rules", "preserved_behaviors", "adjacent_paths",
            "database_impacts", "configuration_impacts", "verify_commands",
            "automatic_acceptance", "manual_acceptance", "blockers",
        ):
            contract_payload[field] = _strict_payload_tuple(
                contract_payload, field, dict if field in mapping_fields else str
            )
        contract = SinglePassChangeContract(**contract_payload)
    except (KeyError, TypeError, ValueError):
        return None, None
    return validate_requirement_governance_outputs(governance, contract)


def _strict_payload_tuple(
    payload: Mapping[str, Any],
    field: str,
    item_type: type,
    *,
    default: object = None,
) -> tuple:
    value = payload[field] if default is None else payload.get(field, default)
    if not isinstance(value, (list, tuple)) or any(type(item) is not item_type for item in value):
        raise ValueError("能力治理序列格式无效。")
    return tuple(dict(item) if item_type is dict else item for item in value)


def _governance_outputs_ready(
    governance_result: object | None,
    single_pass_contract: object | None,
) -> bool:
    return bool(
        governance_result is not None
        and getattr(governance_result, "status", None) == "ready_for_local_change"
        and getattr(governance_result, "can_modify", None) is True
        and getattr(governance_result, "can_complete_in_single_pass", None) is True
        and single_pass_contract is not None
        and getattr(single_pass_contract, "status", None) == "ready"
    )


def governed_worktree_execution_blocker(*, governance_ready: bool, contract_ready: bool) -> str:
    """All local worktree mutation modes require both governance artifacts."""
    if governance_ready is not True:
        return "需求治理未闭合，禁止进入 worktree 改码。"
    if contract_ready is not True:
        return "一次改好变更契约未就绪，禁止进入 worktree 改码。"
    return ""


def single_demand_execution_blocker(
    *,
    governance_ready: bool,
    contract_ready: bool,
    technical_can_patch: bool,
    technical_blockers: list[str],
) -> str:
    """Keep the single-demand convenience path behind the same hard gates."""
    governance_blocker = governed_worktree_execution_blocker(
        governance_ready=governance_ready,
        contract_ready=contract_ready,
    )
    if governance_blocker:
        return governance_blocker.replace("禁止进入", "single-demand-trial 不得进入")
    if technical_can_patch is not True:
        details = "；".join(str(item) for item in technical_blockers if str(item).strip())
        return "技术自治未允许自动 patch：" + (details or "缺少可验证的技术证据。")
    return ""


def _enforce_contract_boundary_error(
    *,
    legacy_governance: object | None,
    legacy_contract: object | None,
    capability_governance: object,
    capability_contract: object,
    technical_decision: Mapping[str, Any],
    trusted_allowed_paths: list[str],
    trusted_verify_commands: list[str],
) -> str:
    if not _governance_outputs_ready(legacy_governance, legacy_contract):
        return GOVERNANCE_BOUNDARY_ERROR + " 原因：本地基准治理合同未就绪。"
    repository_key = lambda item: (
        str(item.get("name") or ""),
        str(Path(str(item.get("path") or "")).resolve()),
        str(item.get("role") or ""),
    )
    trusted_repositories = set(
        repository_key(item)
        for item in technical_decision.get("selected_projects") or ()
        if isinstance(item, Mapping) and item.get("exists") is True
    )
    capability_repositories = set(map(repository_key, capability_contract.repositories))
    risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3, "unknown": 4}
    capability_paths = set(capability_contract.allowed_paths)
    capability_commands = set(capability_contract.verify_commands)
    failures: list[str] = []
    if not capability_paths:
        failures.append("能力合同缺少允许修改路径")
    elif not capability_paths <= set(legacy_contract.allowed_paths):
        failures.append("能力合同扩大了本地基准允许修改路径")
    elif not capability_paths <= set(trusted_allowed_paths):
        failures.append("能力合同扩大了技术决策允许修改路径")
    if not capability_commands:
        failures.append("能力合同缺少验证命令")
    elif not capability_commands <= set(legacy_contract.verify_commands):
        failures.append("能力合同扩大了本地基准验证命令")
    elif not capability_commands <= set(trusted_verify_commands):
        failures.append("能力合同扩大了技术决策验证命令")
    if not capability_repositories:
        failures.append("能力合同缺少仓库范围")
    elif not capability_repositories <= trusted_repositories:
        failures.append("能力合同扩大了仓库范围")
    if risk_rank.get(capability_governance.risk_level, 4) < risk_rank.get(legacy_governance.risk_level, 4):
        failures.append("能力治理降低了风险等级")
    if not set(legacy_contract.out_of_scope) <= set(capability_contract.out_of_scope):
        failures.append("能力合同缩小了禁止修改范围")
    if not set(legacy_contract.adjacent_paths) <= set(capability_contract.adjacent_paths):
        failures.append("能力合同遗漏了相邻路径验证")
    return "" if not failures else GOVERNANCE_BOUNDARY_ERROR + " 原因：" + "；".join(failures) + "。"


def _resolve_governance_execution(
    *,
    requested_mode: str,
    legacy_governance: object | None,
    legacy_contract: object | None,
    legacy_error: str,
    routed_result: object = None,
    routed_mode: object = None,
    has_routed_result: bool = False,
    technical_decision: Mapping[str, Any],
    trusted_allowed_paths: list[str],
    trusted_verify_commands: list[str],
) -> tuple[str, object | None, object | None, bool, str, bool]:
    legacy_ready = _governance_outputs_ready(legacy_governance, legacy_contract)
    legacy_execution = (
        requested_mode,
        legacy_governance,
        legacy_contract,
        requested_mode == "enforce" and not legacy_ready,
        legacy_error,
        False,
    )
    if not has_routed_result:
        return legacy_execution
    if routed_mode not in {"legacy", "observe", "enforce"}:
        return "invalid", None, None, True, GOVERNANCE_MODE_ERROR, False
    if routed_mode != "enforce":
        route_failed = (
            not isinstance(routed_result, Mapping)
            or routed_result.get("status") != "success"
        )
        return (
            *legacy_execution[:4],
            legacy_error or (GOVERNANCE_ROUTE_ERROR if route_failed else ""),
            False,
        )
    # The capability provider uses ``blocked`` for a valid domain decision
    # (for example, incomplete requirement evidence).  It is still a
    # successful transport of structured governance data and must not be
    # collapsed into a route failure, otherwise the valid blocked artifacts
    # are lost before they can be persisted for review.
    if (
        not isinstance(routed_result, Mapping)
        or routed_result.get("status") not in {"success", "blocked"}
    ):
        return "enforce", None, None, True, GOVERNANCE_ROUTE_ERROR, False
    governance_result, single_pass_contract = (
        _governance_outputs_from_capability_data(routed_result.get("data"))
    )
    if governance_result is None or single_pass_contract is None:
        return (
            "enforce",
            None,
            None,
            True,
            GOVERNANCE_VALIDATION_ERROR,
            False,
        )
    error = ""
    if _governance_outputs_ready(governance_result, single_pass_contract):
        error = _enforce_contract_boundary_error(
            legacy_governance=legacy_governance,
            legacy_contract=legacy_contract,
            capability_governance=governance_result,
            capability_contract=single_pass_contract,
            technical_decision=technical_decision,
            trusted_allowed_paths=trusted_allowed_paths,
            trusted_verify_commands=trusted_verify_commands,
        )
    else:
        blockers: list[str] = []
        for candidate in (governance_result, single_pass_contract):
            for field in ("blockers", "missing_information", "unsupported_reasons"):
                for value in getattr(candidate, field, ()) or ():
                    text = str(value).strip()
                    if text and text not in blockers:
                        blockers.append(text)
        error = (
            "需求治理未 ready：" + "；".join(blockers)
            if blockers
            else "需求治理未 ready，enforce 模式禁止进入执行阶段。"
        )
    return (
        "enforce",
        governance_result,
        single_pass_contract,
        bool(error),
        error,
        not error,
    )


def build_knowledge_answer_request(
    text: str,
    *,
    request_id: str | None = None,
) -> CapabilityRequest:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("问题内容不能为空")
    return _preview_request(
        "knowledge.answer",
        "his-knowledge",
        MutationLevel.L0,
        {"text": text.strip()},
        request_id=request_id,
    )


def _legacy_capability_result(
    request: CapabilityRequest,
    data: Mapping[str, Any],
    *,
    status: str | None = None,
) -> Mapping[str, Any]:
    resolved_status = status or str(data.get("status") or "success")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "request_id": request.request_id,
        "capability": request.capability,
        "provider": request.provider,
        "status": resolved_status,
        "mutation_level": request.mutation_level.name,
        "changed": False,
        "summary": resolved_status,
        "data": dict(data),
        "evidence": [],
        "warnings": [],
        "blockers": [] if resolved_status == "success" else [resolved_status],
        "audit": {},
    }


class CapabilityWorkflowOrchestrator:
    """Capability coordinator for question mode and gated task integrations."""

    def __init__(
        self,
        capability_service: Any,
    ) -> None:
        self._service = capability_service

    def run_question(
        self,
        *,
        text: str,
        allow_live_evidence: bool = False,
        investigation_request: bool = False,
    ) -> CapabilityWorkflowResult:
        request = build_knowledge_answer_request(text)
        route = self._service.route(
            request,
            legacy_callable=lambda: _legacy_capability_result(
                request,
                {"answer_status": "unsupported"},
                status="unsupported",
            ),
        )
        events = ["knowledge.answer"]
        if not isinstance(route.result, Mapping):
            return _unsupported_question_result()
        result = dict(route.result)
        if result.get("status") != "success":
            return _unsupported_question_result()
        raw_data = result.get("data")
        if not isinstance(raw_data, Mapping):
            return _unsupported_question_result()
        data = {
            key: value
            for key, value in raw_data.items()
            if isinstance(key, str) and key not in {"evidence", "live_evidence"}
        }
        evidence = _public_knowledge_evidence(result.get("evidence"))
        if evidence is None:
            return _unsupported_question_result()
        data["evidence"] = evidence
        answer_status = data.get("answer_status")
        if not isinstance(answer_status, str) or answer_status not in QUESTION_ANSWER_STATUSES:
            return _unsupported_question_result()
        if (
            answer_status == "answered"
            and not _valid_answered_knowledge_contract(data, evidence)
        ):
            return _unsupported_question_result()
        suggestions: tuple[str, ...] = ()
        if answer_status == "needs_live_evidence":
            raw_suggestions = data.get("suggested_capabilities")
            if (
                not isinstance(raw_suggestions, (list, tuple))
                or not all(
                    isinstance(item, str)
                    and item in _READONLY_EVIDENCE_CAPABILITIES
                    for item in raw_suggestions
                )
            ):
                return _unsupported_question_result()
            suggestions = tuple(dict.fromkeys(raw_suggestions))
        if (
            answer_status == "needs_live_evidence"
            and (allow_live_evidence or investigation_request)
        ):
            live_evidence: dict[str, Mapping[str, Any]] = {}
            for suggestion_name in suggestions:
                if suggestion_name == "git.inspect":
                    events.append(suggestion_name)
                    live_evidence[suggestion_name] = {
                        "status": "blocked",
                        "summary": "GIT_INSPECT_STRUCTURED_INPUT_REQUIRED",
                        "required_input_fields": ["project_path"],
                        "repository_command_attempted": False,
                    }
                    continue
                if suggestion_name == "database.inspect":
                    events.append(suggestion_name)
                    live_evidence[suggestion_name] = {
                        "status": "blocked",
                        "summary": "DATABASE_INSPECT_STRUCTURED_INPUT_REQUIRED",
                        "required_input_fields": [
                            "subject",
                            "keywords",
                            "sql",
                            "parameters",
                            "project_root",
                            "profile_policy",
                        ],
                        "database_connection_attempted": False,
                    }
                    continue
                try:
                    evidence_request = self._build_readonly_evidence_request(
                        suggestion_name,
                        text=text,
                    )
                except ValueError:
                    continue
                evidence_route = self._service.route(
                    evidence_request,
                    legacy_callable=lambda request=evidence_request: _legacy_capability_result(
                        request,
                        {},
                        status="unsupported",
                    ),
                )
                events.append(suggestion_name)
                live_evidence[suggestion_name] = (
                    dict(evidence_route.result)
                    if isinstance(evidence_route.result, Mapping)
                    else {"status": "unsupported"}
                )
            data["live_evidence"] = live_evidence
        return CapabilityWorkflowResult(
            status=answer_status,
            events=tuple(events),
            data=data,
        )

    def run_task_capabilities(
        self,
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
    ) -> CapabilityWorkflowResult:
        """Route post-governance task capabilities without provider authority."""
        status, events, blockers, results = route_task_capabilities(
            self._service,
            routing_result=routing_result,
            contract_ready=contract_ready,
            project_path=project_path,
            expected_diff=expected_diff,
            allowed_paths=allowed_paths,
            verify_commands=verify_commands,
            explicit_remote_delivery=explicit_remote_delivery,
            delivery=delivery,
            code_evidence_sufficient=code_evidence_sufficient,
            database_inspect=database_inspect,
            execute_database=execute_database,
            database_change=database_change,
            knowledge_candidate=knowledge_candidate,
            knowledge_provenance=knowledge_provenance,
            allow_personal_memory=allow_personal_memory,
        )
        return CapabilityWorkflowResult(
            status=status,
            events=events,
            data={"blockers": blockers, "results": results},
        )

    @staticmethod
    def _build_readonly_evidence_request(
        capability: str,
        *,
        text: str,
    ) -> CapabilityRequest:
        provider, mutation_level = _READONLY_EVIDENCE_CAPABILITIES[capability]
        if capability == "workitem.read":
            return build_workitem_read_request(
                yunxiao_url="",
                demand_text=text,
                include_comments=True,
            )
        raise ValueError(
            f"{capability}/{provider}/{mutation_level.name} requires structured input"
        )


@dataclass
class WorkflowResult:
    run_id: int
    status: str
    evaluation_status: str
    markdown_report: str
    json_payload: str
    orchestration_events: tuple[Mapping[str, str], ...] = ()


class _TaskStageLedger:
    def __init__(self) -> None:
        self._events: list[dict[str, str]] = []

    @property
    def events(self) -> tuple[Mapping[str, str], ...]:
        return tuple(dict(item) for item in self._events)

    def record(self, stage: str, status: str, reason_code: str) -> None:
        index = len(self._events)
        if index >= len(TASK_CAPABILITY_SEQUENCE):
            raise RuntimeError("任务阶段账本已结束。")
        expected = TASK_CAPABILITY_SEQUENCE[index]
        if stage != expected:
            raise RuntimeError(
                f"任务阶段顺序无效：期望 {expected}，收到 {stage}。"
            )
        if reason_code not in TASK_STAGE_REASONS:
            raise RuntimeError("任务阶段账本原因码无效。")
        self._events.append(
            {
                "stage": stage,
                "status": status,
                "reason_code": reason_code,
                "reason": TASK_STAGE_REASONS[reason_code],
            }
        )

    def finish(
        self,
        *,
        local_engineering: tuple[str, str],
        verification: tuple[str, str],
        knowledge_candidate: tuple[str, str] = (
            "skipped",
            "knowledge_write_skipped",
        ),
    ) -> None:
        self.record("local_engineering", *local_engineering)
        self.record("verification", *verification)
        self.record("knowledge_candidate", *knowledge_candidate)
        self.record("audit", "completed", "audit_saved")


class RequirementWorkflowRunner:
    def __init__(
        self,
        llm_client: BaseLLMClient | None = None,
        *,
        mode: str | None = None,
        allow_mock: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
        capability_service: Any | None = None,
        visual_evidence_analyzer: VisualEvidenceAnalyzer | None = None,
    ) -> None:
        self.evidence_warnings: list[dict[str, Any]] = []
        self.runtime_fallback_root: Path | None = None
        self.runtime_preflight = run_runtime_preflight(database_path=database.DB_PATH, allow_mock=allow_mock)
        try:
            database.init_db()
        except (OSError, sqlite3.OperationalError) as exc:
            # A read-only checkout must still be usable for analysis. Keep the
            # original path untouched and move only Harness control data to a
            # private temporary directory. Mutation modes remain gated later.
            self.runtime_fallback_root = choose_private_runtime_root(prefix="his_harness_runtime_")
            database.DB_PATH = self.runtime_fallback_root / "harness.sqlite"
            self.runtime_preflight = {
                **self.runtime_preflight,
                "status": "degraded_readonly",
                "read_only": True,
                "fallback": {"database_path": str(database.DB_PATH)},
                "mutation_blockers": ["runtime_database_fallback"],
                "recovery_action": "修复原控制数据库目录可写性后重启；当前只读分析产物写入私有临时目录。",
                "error": f"{type(exc).__name__}: {exc}",
            }
            database.init_db()
        self.startup_recovery = {
            "runs": database.reconcile_stale_runs(max_age_hours=24),
            "tasks": database.reconcile_stale_tasks(max_age_hours=24),
        }
        self.llm_client = llm_client or get_llm_client(mode, allow_mock=allow_mock)
        self.allow_mock = allow_mock
        self.max_retries = max_retries
        self.evaluator = Evaluator(require_real_model=not self.llm_client.is_mock)
        self.yunxiao_transactions = YunxiaoTransactionManager.readonly()
        self.capability_service = capability_service
        self.visual_evidence_analyzer = visual_evidence_analyzer

    def _visual_evidence_blocked_result(
        self,
        *,
        title: str,
        source_type: str,
        demand_text: str,
        requirement_evidence: dict,
    ) -> WorkflowResult:
        """Persist a pre-discovery stop without touching project scanners."""
        visual = requirement_evidence.get("visual_evidence") or {}
        blockers = visual.get("blockers") or ["高风险截图证据未就绪。"]
        run_id = database.create_run(
            team_key=TEAM_KEY,
            title=title.strip() or "手工需求",
            source_type=source_type,
            demand_text=demand_text,
            total_steps=0,
            llm_mode=self.llm_client.mode,
            llm_model=self.llm_client.model_name,
        )
        database.add_artifact(
            run_id,
            "requirement_evidence_json",
            "需求来源归一化证据 JSON",
            json.dumps(requirement_evidence, ensure_ascii=False, indent=2),
        )
        database.add_artifact(
            run_id,
            "requirement_evidence_markdown",
            "需求来源归一化证据",
            requirement_evidence_to_markdown(requirement_evidence),
        )
        database.add_artifact(
            run_id,
            "visual_evidence_json",
            "截图视觉事实门禁 JSON",
            json.dumps(visual, ensure_ascii=False, indent=2),
        )
        report = "\n".join([
            "# Harness 截图证据门禁已阻断", "",
            "- 状态：`blocked_visual_evidence`",
            "- 已执行：云效截图下载/本地视觉事实提取。",
            "- 未执行：项目选择、源码搜索、调用链分析、补丁生成和业务代码修改。", "",
            "## 阻断原因", "",
            *(f"- {item}" for item in blockers), "",
            "## 恢复条件", "",
            "- 截图必须解析出错误文本、菜单/页面、触发动作和业务场景；随后才允许定位项目与完整调用链。",
        ])
        database.add_artifact(run_id, "markdown", "Harness 截图证据门禁报告", report)
        payload = json.dumps({"run_id": run_id, "status": "blocked_visual_evidence", "blockers": blockers}, ensure_ascii=False, indent=2)
        database.add_artifact(run_id, "json", "Harness 截图证据门禁数据", payload)
        database.update_run(run_id, status="blocked", evaluation_status="visual_evidence_blocked", error="；".join(str(item) for item in blockers))
        return WorkflowResult(run_id, "blocked", "visual_evidence_blocked", report, payload)

    def _capability_mutations_enforced(self) -> bool:
        return (
            self.capability_service is not None
            and getattr(
                self.capability_service,
                "routing_mode",
                getattr(self.capability_service, "mode", None),
            )
            == "enforce"
        )

    def run(
        self,
        *,
        demand_text: str,
        title: str = "手工需求",
        source_type: str = "manual",
        project_key: str | None = None,
        project_path: str | Path | list[str] | tuple[str, ...] | None = None,
        project_root: str | Path = DEFAULT_PROJECT_ROOT,
        project_config: str | Path | None = None,
        execution_mode: str = "readonly",
        worktree_dir: str | Path = DEFAULT_WORKTREE_ROOT,
        allowed_paths: list[str] | None = None,
        verify_commands: list[str] | None = None,
        method_evidence_file: str | Path | None = None,
        method_test_commands: list[str] | None = None,
        ui_evidence_paths: list[str] | None = None,
        ui_capture_commands: list[str] | None = None,
        max_edit_rounds: int = 2,
        apply_approved_diff: bool = True,
        pre_change_confirmation: str = "",
        review_commit: str = "HEAD",
        review_base: str = "",
        yunxiao_read: bool = False,
        yunxiao_include_comments: bool = True,
        yunxiao_url: str = "",
        yunxiao_output_dir: str | Path | None = None,
        yunxiao_transaction_mode: str = "off",
        yunxiao_policy_config: str | Path | None = None,
        yunxiao_policy_key: str = "",
        yunxiao_entity_kind: str = "",
        yunxiao_entity_id: str = "",
        yunxiao_current_status: str = "",
        yunxiao_target_assignee: str = "",
        yunxiao_target_status: str = "",
        yunxiao_target_iteration: str = "",
        yunxiao_screenshots: list[str] | None = None,
        yunxiao_service_change_file: str = "",
        yunxiao_artifacts: list[str] | None = None,
        yunxiao_write_confirm: str = "",
        yunxiao_human_confirmed: bool = False,
        yunxiao_write_transport: str = "real",
        yunxiao_write_scope: str = "comment-only",
        requirement_evidence_file: str | Path | None = None,
        conversation_evidence_file: str | Path | None = None,
        local_change_evidence_exception: Mapping[str, Any] | None = None,
        multi_service_evidence: Mapping[str, Any] | None = None,
        acceptance_contract_file: str | Path | None = None,
        requirement_governance: str = "observe",
        database_inspect: Mapping[str, Any] | None = None,
        database_execute: bool = False,
        database_change: Mapping[str, Any] | None = None,
        knowledge_candidate: Mapping[str, Any] | None = None,
        routing_result: TaskIntentRoutingResult | None = None,
    ) -> WorkflowResult:
        demand_text = demand_text.strip()
        if not demand_text:
            raise ValueError("需求内容不能为空")
        if execution_mode not in {"readonly", "worktree", "review-worktree", "fullstack-worktree", "precommit-verify", "single-demand-trial", "core-closure-trial", "auto-local"}:
            raise ValueError("execution_mode 只能是 readonly、worktree、review-worktree、fullstack-worktree、precommit-verify、single-demand-trial、core-closure-trial 或 auto-local")
        if requirement_governance not in {"legacy", "observe", "enforce"}:
            raise ValueError("requirement_governance 只能是 legacy、observe 或 enforce")
        if (
            database_inspect is not None
            and not isinstance(database_inspect, Mapping)
        ):
            raise ValueError("database_inspect 必须是结构化对象")
        if (
            database_change is not None
            and not isinstance(database_change, Mapping)
        ):
            raise ValueError("database_change 必须是结构化对象")
        if (
            multi_service_evidence is not None
            and not isinstance(multi_service_evidence, Mapping)
        ):
            raise ValueError("multi_service_evidence 必须是结构化对象")
        if (
            local_change_evidence_exception is not None
            and not isinstance(local_change_evidence_exception, Mapping)
        ):
            raise ValueError("local_change_evidence_exception 必须是结构化对象")
        if database_execute and database_inspect is None:
            raise ValueError("database_execute 必须同时提供 database_inspect")
        if (
            knowledge_candidate is not None
            and not isinstance(knowledge_candidate, Mapping)
        ):
            raise ValueError("knowledge_candidate 必须是结构化对象")
        if routing_result is None:
            routing_nonce = uuid4().hex
            routing_result = TaskIntentService().route(
                demand_text,
                IntentContext(
                    conversation_key=(
                        f"run-{routing_nonce[:6]}-{routing_nonce[6:12]}"
                    )
                ),
            )
        task_stages = _TaskStageLedger()
        task_stages.record("intake", "completed", "intake_validated")
        requested_execution_mode = execution_mode
        resolved_execution_mode = "core-closure-trial" if execution_mode == "auto-local" else execution_mode
        mutation_requested = resolved_execution_mode in MUTATING_EXECUTION_MODES
        runtime_checks = run_runtime_preflight(
            database_path=database.DB_PATH,
            output_dir=yunxiao_output_dir or self.runtime_fallback_root or Path("runs"),
            worktree_root=worktree_dir,
            require_git=resolved_execution_mode in {"worktree", "review-worktree", "fullstack-worktree", "precommit-verify", "core-closure-trial"},
            mutation_requested=mutation_requested,
            allow_mock=self.allow_mock,
        )
        runtime_fallback: dict[str, str] = {}
        # Output/worktree directories are Harness runtime data.  If the host
        # blocks those paths, move only those artifacts to a private temporary
        # root and retry; database, business-repository, and remote-write
        # safety gates are intentionally unchanged.
        failed_checks = set(runtime_checks.get("failed_checks") or [])
        fallback_checks = failed_checks.intersection({"output", "worktree"})
        if mutation_requested and fallback_checks and "git" not in failed_checks and "database" not in failed_checks:
            fallback_root = choose_private_runtime_root(prefix="his_harness_workflow_runtime_")
            if "worktree" in fallback_checks:
                worktree_dir = fallback_root / "worktrees"
                worktree_dir.mkdir(parents=True, exist_ok=True)
                runtime_fallback["worktree_root"] = str(worktree_dir)
            if "output" in fallback_checks:
                yunxiao_output_dir = fallback_root / "yunxiao"
                yunxiao_output_dir.mkdir(parents=True, exist_ok=True)
                runtime_fallback["output_dir"] = str(yunxiao_output_dir)
            runtime_checks = run_runtime_preflight(
                database_path=database.DB_PATH,
                output_dir=yunxiao_output_dir or self.runtime_fallback_root or Path("runs"),
                worktree_root=worktree_dir,
                require_git=resolved_execution_mode in {"worktree", "review-worktree", "fullstack-worktree", "precommit-verify", "core-closure-trial"},
                mutation_requested=mutation_requested,
                allow_mock=self.allow_mock,
            )
        self.runtime_preflight = {
            **self.runtime_preflight,
            "run": runtime_checks,
            "status": "blocked" if runtime_checks.get("status") == "blocked" else self.runtime_preflight.get("status", "ready"),
            "runtime_fallback": runtime_fallback,
            "mutation_blockers": list(dict.fromkeys([*(self.runtime_preflight.get("mutation_blockers") or []), *(runtime_checks.get("mutation_blockers") or [])])),
        }
        auto_local_performance = build_auto_local_performance_profile(
            requested_execution_mode=requested_execution_mode,
            resolved_execution_mode=resolved_execution_mode,
        )
        if yunxiao_transaction_mode not in {"off", "dry-run", "write"}:
            raise ValueError("yunxiao_transaction_mode 只能是 off、dry-run 或 write")
        if yunxiao_write_transport not in {"real", "fake"}:
            raise ValueError("yunxiao_write_transport 只能是 real 或 fake")
        if yunxiao_write_scope not in {"comment-only", "transition-fake"}:
            raise ValueError("yunxiao_write_scope 只能是 comment-only 或 transition-fake")
        if yunxiao_transaction_mode == "write":
            write_request = build_workitem_write_request(
                entity_kind=yunxiao_entity_kind,
                entity_id=yunxiao_entity_id or parse_work_item_id(
                    yunxiao_url or demand_text
                ),
                write_scope=yunxiao_write_scope,
                explicitly_authorized=bool(
                    yunxiao_human_confirmed and yunxiao_write_confirm
                ),
            )
            if self.capability_service is None:
                raise ValueError(
                    "workitem.write 能力未开放；legacy 旧直连已禁用，"
                    "当前也未提供能力路由服务。"
                )
            self.capability_service.route(write_request)
            raise ValueError(
                "workitem.write 能力未开放；legacy 旧直连已禁用，"
                "observe/enforce 也必须由已启用的插件能力执行。"
            )
        # Work-item mutations must be routed and blocked before the ordinary
        # requirement workflow guard. Otherwise a write-only request can be
        # rejected as an unrelated task-intent input before workitem.write is
        # even observed by the capability service.
        routing_result = require_requirement_workflow_route(routing_result)
        project_paths = normalize_project_paths(project_path)
        project_path_is_explicit = bool(project_paths)
        allowed_paths_are_explicit = bool(allowed_paths)
        primary_project_path = project_paths[0] if project_paths else None

        review_context = None
        if resolved_execution_mode == "review-worktree":
            if not primary_project_path:
                raise ValueError("review-worktree 模式必须提供 --project-path")
            review_context = build_review_context(
                project_path=primary_project_path,
                review_commit=review_commit,
                review_base=review_base,
                allowed_paths=allowed_paths or [],
            )

        requirement_evidence_started = time.perf_counter()
        if yunxiao_read and self.capability_service is not None:
            read_request = build_workitem_read_request(
                yunxiao_url=yunxiao_url,
                demand_text=demand_text,
                include_comments=yunxiao_include_comments,
            )

            def legacy_yunxiao_read() -> Mapping[str, Any]:
                evidence = collect_yunxiao_evidence(
                    yunxiao_url=yunxiao_url,
                    demand_text=demand_text,
                    output_dir=yunxiao_output_dir,
                    include_comments=yunxiao_include_comments,
                )
                return _legacy_capability_result(
                    read_request,
                    evidence,
                )

            routed_evidence = self.capability_service.route(
                read_request,
                legacy_callable=legacy_yunxiao_read,
                equivalence_fields=("status", "data.status", "data.work_item_id"),
            )
            routed_evidence_result = dict(routed_evidence.result)
            yunxiao_evidence = dict(
                routed_evidence_result.get("data") or {}
            )
            if routed_evidence_result.get("status") != "success":
                yunxiao_evidence.setdefault(
                    "status",
                    str(routed_evidence_result.get("status") or "failed"),
                )
                yunxiao_evidence.setdefault(
                    "error",
                    str(routed_evidence_result.get("summary") or "能力路由失败"),
                )
        else:
            yunxiao_evidence = (
                collect_yunxiao_evidence(
                    yunxiao_url=yunxiao_url,
                    demand_text=demand_text,
                    output_dir=yunxiao_output_dir,
                    include_comments=yunxiao_include_comments,
                )
                if yunxiao_read
                else None
            )
        requirement_evidence = load_requirement_evidence_file(requirement_evidence_file)
        if requirement_evidence is None and isinstance(yunxiao_evidence, dict):
            normalized_yunxiao = normalize_requirement_evidence(
                source_type="yunxiao",
                payload=yunxiao_evidence,
                source_url=str(yunxiao_evidence.get("yunxiao_url") or yunxiao_url),
            )
            if (normalized_yunxiao.get("visual_evidence") or {}).get("required") is True:
                requirement_evidence = normalized_yunxiao
        if requirement_evidence is not None:
            analyze_requirement_visual_evidence(
                requirement_evidence,
                analyzer=self.visual_evidence_analyzer,
            )
        conversation_evidence = load_conversation_evidence_file(conversation_evidence_file)
        # A v2 provider evidence file carries its own source (for example
        # ``provider: yunxiao``).  Do not leave the run labelled ``manual``
        # when the caller supplied a real provider package; that makes the
        # calibration layer incorrectly discard the fetched ticket as an
        # unreliable source.
        if requirement_evidence and source_type == "manual":
            source_type = str(
                requirement_evidence.get("source_type") or source_type
            )
        visual_evidence = (
            requirement_evidence.get("visual_evidence")
            if isinstance(requirement_evidence, dict)
            else {}
        )
        if isinstance(visual_evidence, dict) and visual_evidence.get("can_begin_analysis") is False:
            return self._visual_evidence_blocked_result(
                title=title,
                source_type=source_type,
                demand_text=demand_text,
                requirement_evidence=requirement_evidence,
            )
        database_capability = None
        database_blockers: tuple[str, ...] = ()
        if database_inspect is not None or database_change is not None:
            if self.capability_service is None:
                database_blockers = ("database_capability_service_unavailable",)
            else:
                database_capability = CapabilityWorkflowOrchestrator(
                    self.capability_service
                ).run_task_capabilities(
                    routing_result=routing_result,
                    contract_ready=False,
                    code_evidence_sufficient=database_inspect is None,
                    database_inspect=database_inspect,
                    execute_database=database_execute,
                    database_change=database_change,
                )
                database_blockers = tuple(
                    database_capability.data.get("blockers") or ()
                )
        record_auto_local_stage(
            auto_local_performance,
            key="requirement_evidence",
            started_at=requirement_evidence_started,
            status="completed",
            yunxiao_read=yunxiao_read,
            yunxiao_include_comments=yunxiao_include_comments,
            local_evidence_file=bool(requirement_evidence_file),
        )
        has_provider_evidence = bool(
            yunxiao_evidence
            or requirement_evidence
            or (
                database_capability is not None
                and database_capability.data.get("results")
            )
        )
        task_stages.record(
            "provider_evidence",
            "completed" if has_provider_evidence else "skipped",
            (
                "provider_evidence_loaded"
                if has_provider_evidence
                else "provider_evidence_not_requested"
            ),
        )
        base_workflow_demand_text = build_workflow_demand_text(
            demand_text=demand_text,
            yunxiao_evidence=yunxiao_evidence,
            requirement_evidence=requirement_evidence,
            conversation_evidence=conversation_evidence,
        )
        if database_capability is not None:
            base_workflow_demand_text += (
                "\n\n【数据库 capability 证据】\n"
                + json.dumps(
                    database_capability.data.get("results") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        requirement_calibration_started = time.perf_counter()
        requirement_calibration = build_requirement_calibration(
            title=title,
            # Calibration consumes only requirement semantics.  The rendered
            # provider markdown contains local paths, schema keys and status
            # labels that are evidence metadata, not business parameters.
            demand_text=demand_text,
            yunxiao_evidence=yunxiao_evidence,
            requirement_evidence=requirement_evidence,
            user_instruction=demand_text,
            project_paths=project_paths,
        )
        record_auto_local_stage(
            auto_local_performance,
            key="requirement_calibration",
            started_at=requirement_calibration_started,
            status="completed",
            calibration_status=requirement_calibration.get("status"),
        )
        task_stages.record("calibration", "completed", "calibration_generated")
        requirement_calibration_context = requirement_calibration_to_prompt_context(requirement_calibration)
        calibrated_demand_text = (
            f"{base_workflow_demand_text}\n\n"
            "【Harness v0.15 需求理解确认卡】\n"
            f"{requirement_calibration_context}"
        )
        explicit_contract_parameters = [
            str(parameter.get("name") or "").strip()
            for parameter in requirement_calibration.get("resolved_parameters") or []
            if parameter.get("source") == "explicit_harness_rule"
            and parameter.get("location") == "request_param"
            and str(parameter.get("name") or "").strip()
        ]
        conversation_locators = conversation_code_locator_text(conversation_evidence)
        technical_discovery_text = demand_text
        if isinstance(requirement_evidence, dict):
            visual_facts = (requirement_evidence.get("visual_evidence") or {}).get("facts") or []
            if visual_facts:
                technical_discovery_text += "\n【截图已确认事实】\n" + "\n".join(
                    f"错误={item.get('error_text') or ''}；菜单={item.get('menu') or ''}；动作={item.get('action') or ''}；场景={item.get('business_scene') or ''}"
                    for item in visual_facts if isinstance(item, dict)
                )
        technical_decision_started = time.perf_counter()
        technical_decision = build_technical_decision(
            # Pass the user's actual request here.  Provider evidence is
            # already supplied through the structured arguments below; feeding
            # the rendered evidence markdown back as demand text introduces
            # warning/metadata labels (for example ``errorMessage``) into
            # project-term discovery and can select unrelated repositories.
            demand_text=technical_discovery_text,
            yunxiao_evidence=yunxiao_evidence,
            requirement_evidence=requirement_evidence,
            project_root=project_root,
            explicit_project_paths=project_paths,
            explicit_allowed_paths=allowed_paths,
            contract_parameters=explicit_contract_parameters or None,
            default_value_precedence=requirement_calibration.get("default_value_precedence"),
            authoritative_code_locators=conversation_locators,
        )
        change_ownership_matrix = build_change_ownership_matrix(
            user_instruction=demand_text,
            requirement_text=base_workflow_demand_text,
            technical_decision=technical_decision.to_dict(),
        )
        error_chain_closure = build_error_chain_closure(
            demand_text=demand_text,
            conversation_evidence=conversation_evidence,
            technical_decision=technical_decision.to_dict(),
            requirement_evidence=requirement_evidence,
        )
        record_auto_local_stage(
            auto_local_performance,
            key="technical_decision",
            started_at=technical_decision_started,
            status="completed",
            can_patch=technical_decision.can_patch,
        )
        task_stages.record(
            "technical_decision",
            "completed",
            "technical_decision_completed",
        )
        task_stages.record("ownership", "completed", "ownership_generated")
        if not project_paths:
            project_paths = [str(item.get("path")) for item in technical_decision.selected_projects if item.get("exists") and item.get("path")]
            primary_project_path = technical_decision.primary_project_path or (project_paths[0] if project_paths else None)
            requirement_calibration["project_paths"] = project_paths
            requirement_calibration_context = requirement_calibration_to_prompt_context(requirement_calibration)
            calibrated_demand_text = (
                f"{base_workflow_demand_text}\n\n"
                "【Harness v0.15 需求理解确认卡】\n"
                f"{requirement_calibration_context}"
            )
        try:
            worktree_startup_recovery = inspect_worktree_root(
                worktree_root=worktree_dir,
                project_paths=[Path(path) for path in project_paths],
                max_age_hours=24,
            )
            attention_statuses = {"unowned", "project_not_allowed", "unregistered_blocked", "dirty_blocked", "inspection_failed"}
            needs_attention = bool(worktree_startup_recovery["candidates"] or worktree_startup_recovery["marker_errors"])
            needs_attention = needs_attention or any(
                item.get("status") in attention_statuses for item in worktree_startup_recovery["skipped"]
            )
            worktree_startup_recovery["status"] = "attention_required" if needs_attention else "clean"
        except ValueError as exc:
            worktree_startup_recovery = {
                "status": "inspection_unavailable",
                "root": str(Path(worktree_dir).expanduser().resolve()),
                "message": str(exc),
                "candidates": [],
                "skipped": [],
            }
        effective_allowed_paths = allowed_paths or technical_decision.recommended_allowed_paths
        effective_verify_commands = verify_commands or technical_decision.recommended_verify_commands
        if not effective_verify_commands and primary_project_path:
            discovered_verify_commands = suggest_runtime_commands(str(primary_project_path))
            if discovered_verify_commands:
                effective_verify_commands = discovered_verify_commands
                technical_decision.recommended_verify_commands = list(discovered_verify_commands)
        # The acceptance contract contributes a real, targeted verification
        # command to the mutating scope.  Resolve it before building the
        # confirmation binding; otherwise the preview hash omits the command
        # and the final core-closure contract adds it after confirmation,
        # making an exact user confirmation impossible to replay.
        precomputed_acceptance_contract_result = None
        if (
            resolved_execution_mode == "core-closure-trial"
            and acceptance_contract_file is not None
        ):
            precomputed_acceptance_contract_result = execute_acceptance_contract(
                acceptance_contract_file
            )
            if (
                precomputed_acceptance_contract_result.status == "pass"
                and precomputed_acceptance_contract_result.verify_command
            ):
                effective_verify_commands = list(
                    dict.fromkeys(
                        [
                            *(effective_verify_commands or []),
                            precomputed_acceptance_contract_result.verify_command,
                        ]
                    )
                )
                technical_decision.recommended_verify_commands = list(
                    effective_verify_commands
                )
        fast_local_decision = build_fast_local_decision(
            title=title,
            demand_text=demand_text,
            project_paths=project_paths,
            allowed_paths=effective_allowed_paths or [],
            project_path_is_explicit=project_path_is_explicit,
            allowed_paths_are_explicit=allowed_paths_are_explicit,
        ) if requested_execution_mode == "auto-local" else None
        if auto_local_performance is not None:
            auto_local_performance["fast_local"] = fast_local_decision
        method_evidence = load_method_evidence_file(method_evidence_file)
        workflow_demand_text = (
            f"{calibrated_demand_text}\n\n"
            "【Harness v0.9.5 技术自治决策】\n"
            f"{technical_decision.to_prompt_context()}\n\n"
            "【Harness v0.58 需求变更归属矩阵】\n"
            f"{change_ownership_matrix.to_markdown()}"
        )
        project_context_started = time.perf_counter()
        if (
            fast_local_decision
            and fast_local_decision["skip_project_context_scan"]
            and not mutation_requested
        ):
            evidence_bundle = None
            record_auto_local_stage(
                auto_local_performance,
                key="project_context_scan",
                started_at=project_context_started,
                status="skipped",
                reason="fast_local 条件全部满足，跳过全仓工程上下文扫描。",
            )
        else:
            evidence_bundle = self._build_evidence_bundle(
                demand_text=workflow_demand_text,
                project_key=project_key,
                project_path=primary_project_path,
                project_config=project_config,
                review_context=review_context,
            )
            record_auto_local_stage(
                auto_local_performance,
                key="project_context_scan",
                started_at=project_context_started,
                status="completed",
                reason="未命中 fast_local，执行完整工程上下文扫描。",
            )
        acceptance_matrix_started = time.perf_counter()
        acceptance_matrix = build_acceptance_matrix(
            title=title,
            demand_text=base_workflow_demand_text,
            evidence_bundle=evidence_bundle.to_dict() if evidence_bundle else None,
            yunxiao_evidence=yunxiao_evidence,
            project_paths=project_paths,
            verify_commands=effective_verify_commands or [],
            execution_mode=resolved_execution_mode,
            yunxiao_transaction_mode=yunxiao_transaction_mode,
            yunxiao_write_scope=yunxiao_write_scope,
            default_value_precedence=requirement_calibration.get("default_value_precedence"),
        )
        record_auto_local_stage(
            auto_local_performance,
            key="acceptance_matrix",
            started_at=acceptance_matrix_started,
            status="completed",
            item_count=len(acceptance_matrix.get("items") or []),
        )
        task_stages.record("acceptance", "completed", "acceptance_generated")
        understanding_requirement_evidence = requirement_evidence or {
            # A local task's user instruction is still first-party source
            # evidence.  It may establish context, but never substitutes for
            # missing project/call-chain proof below.
            "title": title,
            "description_text": demand_text,
        }
        requirement_understanding = build_requirement_understanding(
            title=title,
            user_instruction=demand_text,
            requirement_evidence=understanding_requirement_evidence,
            requirement_calibration=requirement_calibration,
            technical_decision=technical_decision.to_dict(),
            change_ownership=change_ownership_matrix.to_dict(),
            acceptance_matrix=acceptance_matrix,
            conversation_evidence=conversation_evidence,
            error_chain_closure=error_chain_closure,
        )
        understanding_execution_blocked = (
            mutation_requested and not requirement_understanding.can_modify
        )
        task_stages.record(
            "understanding",
            "blocked" if understanding_execution_blocked else "completed",
            "understanding_blocked" if understanding_execution_blocked else "understanding_ready",
        )
        legacy_governance_result = None
        legacy_single_pass_contract = None
        legacy_governance_error = ""
        routed_governance = None
        if requirement_governance != "legacy":
            (
                legacy_governance_result,
                legacy_single_pass_contract,
                legacy_governance_error,
            ) = build_requirement_governance_outputs(
                title=title,
                user_instruction=demand_text,
                source_type=source_type,
                normalized_requirement_evidence=requirement_evidence,
                yunxiao_evidence=yunxiao_evidence,
                requirement_calibration=requirement_calibration,
                technical_decision=technical_decision.to_dict(),
                change_ownership=change_ownership_matrix.to_dict(),
                acceptance_matrix=acceptance_matrix,
                local_change_evidence_exception=local_change_evidence_exception,
            )
            (
                legacy_governance_result,
                legacy_single_pass_contract,
            ) = validate_requirement_governance_outputs(
                legacy_governance_result,
                legacy_single_pass_contract,
            )
            if (
                legacy_governance_result is None
                or legacy_single_pass_contract is None
            ):
                legacy_governance_error = GOVERNANCE_VALIDATION_ERROR
            if self.capability_service is not None:
                governance_input = {
                    "title": title,
                    "user_instruction": demand_text,
                    "source_type": source_type,
                    "normalized_requirement_evidence": requirement_evidence,
                    "yunxiao_evidence": yunxiao_evidence,
                    "requirement_calibration": requirement_calibration,
                    "technical_decision": technical_decision.to_dict(),
                    "change_ownership": change_ownership_matrix.to_dict(),
                    "acceptance_matrix": acceptance_matrix,
                    "local_change_evidence_exception": local_change_evidence_exception,
                }
                governance_request = build_requirement_governance_request(
                    governance_input
                )
                legacy_governance_data = {
                    "status": (
                        "success"
                        if _governance_outputs_ready(
                            legacy_governance_result,
                            legacy_single_pass_contract,
                        )
                        else "blocked"
                    ),
                    "governance": (
                        legacy_governance_result.to_dict()
                        if legacy_governance_result is not None
                        else None
                    ),
                    "single_pass_change_contract": (
                        legacy_single_pass_contract.to_dict()
                        if legacy_single_pass_contract is not None
                        else None
                    ),
                }
                routed_governance = self.capability_service.route(
                    governance_request,
                    legacy_callable=lambda: (
                        _legacy_capability_result(
                            governance_request,
                            legacy_governance_data,
                        )
                    ),
                    equivalence_fields=(
                        "status",
                        "data.governance.status",
                        "data.single_pass_change_contract.status",
                    ),
                )
        (
            effective_governance_mode,
            governance_result,
            single_pass_contract,
            governance_execution_blocked,
            governance_error,
            capability_contract_authoritative,
        ) = _resolve_governance_execution(
            requested_mode=requirement_governance,
            legacy_governance=legacy_governance_result,
            legacy_contract=legacy_single_pass_contract,
            legacy_error=legacy_governance_error,
            routed_result=(
                getattr(routed_governance, "result", None)
                if routed_governance is not None
                else None
            ),
            routed_mode=(
                getattr(routed_governance, "mode", None)
                if routed_governance is not None
                else None
            ),
            has_routed_result=routed_governance is not None,
            technical_decision=technical_decision.to_dict(),
            trusted_allowed_paths=list(effective_allowed_paths or ()),
            trusted_verify_commands=list(effective_verify_commands or ()),
        )
        governance_ready = (
            not governance_execution_blocked
            and _governance_outputs_ready(governance_result, single_pass_contract)
        )
        if understanding_execution_blocked:
            governance_execution_blocked = True
            governance_ready = False
            understanding_reason = "改码前理解证据包未就绪：" + "；".join(
                requirement_understanding.blockers
            )
            governance_error = (
                f"{governance_error}；{understanding_reason}"
                if governance_error
                else understanding_reason
            )
        if (technical_decision.implementation_decision or {}).get("change_type") == "multi_service_feature":
            def _acceptance_text(item: object) -> str:
                if isinstance(item, dict):
                    for key in ("statement", "scenario", "description", "message", "command", "path"):
                        value = str(item.get(key) or "").strip()
                        if value:
                            return value
                return str(item or "").strip()

            submitted_runtime_validation = (
                _submitted_evidence_mapping(multi_service_evidence, "runtime_validation")
                if multi_service_evidence
                else {}
            )
            runtime_validation = (
                submitted_runtime_validation
                if submitted_runtime_validation
                else discover_runtime_validation(
                    technical_decision=technical_decision.to_dict(),
                    selected_projects=technical_decision.selected_projects,
                )
            )
            multi_service_contract = build_multi_service_change_contract(
                technical_decision=technical_decision.to_dict(),
                governance_ready=governance_ready,
                selected_projects=technical_decision.selected_projects,
                runtime_validation=runtime_validation,
                acceptance={
                    "automatic": (
                        _submitted_evidence_list(_submitted_evidence_mapping(multi_service_evidence, "acceptance"), "automatic")
                        if multi_service_evidence and _submitted_evidence_list(_submitted_evidence_mapping(multi_service_evidence, "acceptance"), "automatic")
                        else [
                            value
                            for value in (_acceptance_text(item) for item in acceptance_matrix.get("auto_verification") or [])
                            if value
                        ]
                    ),
                    "manual": (
                        _submitted_evidence_list(_submitted_evidence_mapping(multi_service_evidence, "acceptance"), "manual")
                        if multi_service_evidence and _submitted_evidence_list(_submitted_evidence_mapping(multi_service_evidence, "acceptance"), "manual")
                        else [
                            value
                            for value in (_acceptance_text(item) for item in acceptance_matrix.get("manual_acceptance") or [])
                            if value
                        ]
                    ),
                },
            )
            technical_decision.multi_service_change_contract = multi_service_contract.to_dict()
            workflow_demand_text = (
                f"{workflow_demand_text}\n\n"
                "【Harness v1 多项目改动合同与证据补充选择】\n"
                f"{_multi_service_contract_markdown(technical_decision.multi_service_change_contract)}"
            )
            if not multi_service_contract.can_apply:
                governance_ready = False
                governance_error = governance_error or (
                    "多项目改动合同未就绪：" + "；".join(multi_service_contract.blockers)
                )
                if resolved_execution_mode in {
                    "worktree",
                    "fullstack-worktree",
                    "single-demand-trial",
                    "core-closure-trial",
                }:
                    governance_execution_blocked = True
        # Readonly analysis may continue far enough to produce evidence and a
        # service graph, but an unresolved governance/contract gate must never
        # be reported as an ordinary evaluator pass.  Keep the run useful and
        # resumable while preserving the distinction between "analysis done"
        # and "safe to modify".
        readonly_governance_gate_blocked = (
            resolved_execution_mode == "readonly"
            and not governance_ready
        )
        if effective_governance_mode == "enforce" and governance_ready:
            effective_allowed_paths = list(single_pass_contract.allowed_paths)
            effective_verify_commands = list(single_pass_contract.verify_commands)
            if (
                precomputed_acceptance_contract_result is not None
                and precomputed_acceptance_contract_result.status == "pass"
                and precomputed_acceptance_contract_result.verify_command
            ):
                effective_verify_commands = list(
                    dict.fromkeys(
                        [
                            *effective_verify_commands,
                            precomputed_acceptance_contract_result.verify_command,
                        ]
                    )
                )
                technical_decision.recommended_verify_commands = list(
                    effective_verify_commands
                )
        fullstack_technical_decision = technical_decision
        fullstack_verify_commands = verify_commands or []
        fullstack_authoritative_contract = None
        fullstack_authority_mode = (
            "enforce" if capability_contract_authoritative else "legacy"
        )
        if (
            resolved_execution_mode == "fullstack-worktree"
            and capability_contract_authoritative
        ):
            fullstack_authoritative_contract = single_pass_contract.to_dict()
            fullstack_verify_commands = list(single_pass_contract.verify_commands)
            fullstack_technical_decision = replace(
                technical_decision,
                selected_projects=[
                    {**repository, "exists": True}
                    for repository in single_pass_contract.repositories
                ],
                recommended_allowed_paths=list(single_pass_contract.allowed_paths),
                recommended_verify_commands=fullstack_verify_commands,
            )
            if (
                technical_decision.multi_service_change_contract.get("schema_version")
                == MULTI_SERVICE_CHANGE_CONTRACT_SCHEMA_VERSION
                and technical_decision.multi_service_change_contract.get("status")
                == "ready"
            ):
                # The generic multi-service contract has its own repository
                # and path authority; do not run the legacy single-target
                # fullstack boundary validator against it.
                fullstack_boundary_error = ""
            else:
                fullstack_boundary_error = validate_authoritative_fullstack_options(
                    FullstackExecutionOptions(
                        run_id=0,
                        demand_text=workflow_demand_text,
                        report_markdown="",
                        project_root=str(project_root),
                        authority_mode=fullstack_authority_mode,
                        technical_decision=fullstack_technical_decision.to_dict(),
                        worktree_root=str(worktree_dir),
                        verify_commands=fullstack_verify_commands,
                        authoritative_contract=fullstack_authoritative_contract,
                        apply_to_project=False,
                        cleanup_worktree=False,
                    )
                )
            if fullstack_boundary_error:
                governance_ready = False
                governance_execution_blocked = True
                governance_error = fullstack_boundary_error
        confirmation_technical_decision = (
            fullstack_technical_decision
            if resolved_execution_mode == "fullstack-worktree"
            else technical_decision
        )
        confirmation_contract_payload: Mapping[str, Any] | None = None
        if (
            single_pass_contract is not None
            and hasattr(single_pass_contract, "to_dict")
            and getattr(single_pass_contract, "status", None) == "ready"
        ):
            confirmation_contract_payload = single_pass_contract.to_dict()
        elif resolved_execution_mode == "core-closure-trial":
            # The core path builds its final contract after the run is created.
            # Build the same deterministic legacy candidate here so the user
            # confirms paths and verification commands before any worktree starts.
            provisional_contract = build_requirement_contract(
                title=title,
                demand_text=base_workflow_demand_text,
                requirement_calibration=requirement_calibration,
                technical_decision={
                    **technical_decision.to_dict(),
                    "recommended_allowed_paths": list(effective_allowed_paths or []),
                    "recommended_verify_commands": list(effective_verify_commands or []),
                },
                acceptance_matrix=acceptance_matrix,
                apply_to_project=apply_approved_diff,
                acceptance_contract_result=precomputed_acceptance_contract_result,
                change_ownership_matrix=change_ownership_matrix.to_dict(),
            )
            confirmation_contract_payload = provisional_contract.to_dict()
        confirmation_governance_payload = (
            governance_result.to_dict()
            if governance_result is not None and hasattr(governance_result, "to_dict")
            else {}
        )
        confirmation_governance_payload = {
            **confirmation_governance_payload,
            "status": confirmation_governance_payload.get("status")
            or ("blocked" if governance_execution_blocked else effective_governance_mode),
            "can_modify": governance_ready,
        }
        scope_confirmation_binding = build_scope_confirmation_binding(
            execution_mode=resolved_execution_mode,
            technical_decision=confirmation_technical_decision.to_dict(),
            change_ownership=change_ownership_matrix.to_dict(),
            governance=confirmation_governance_payload,
            single_pass_contract=confirmation_contract_payload,
            allowed_paths=list(effective_allowed_paths or []),
            verify_commands=list(effective_verify_commands or []),
        )
        # ``auto-local`` is the user's local-first path.  Once the deterministic
        # contract has been calculated, reuse that exact binding as a task-scoped
        # authorization instead of asking for a second chat turn merely to copy
        # a hash token.  This does not bypass governance: high-risk/ambiguous
        # work is stopped above, and remote writes/database changes remain
        # separately authorized capabilities.
        auto_local_scope_confirmation = (
            requested_execution_mode == "auto-local"
            and resolved_execution_mode == "core-closure-trial"
            and not pre_change_confirmation.strip()
        )
        if auto_local_scope_confirmation:
            pre_change_confirmation = str(
                scope_confirmation_binding["confirmation_token"]
            )
        scope_confirmation_required = resolved_execution_mode in MUTATING_EXECUTION_MODES
        scope_confirmation_valid = (
            not scope_confirmation_required
            or validate_scope_confirmation(
                pre_change_confirmation,
                scope_confirmation_binding["scope_hash"],
            )
        )
        scope_confirmation_status = (
            "not_required"
            if not scope_confirmation_required
            else "auto_confirmed"
            if auto_local_scope_confirmation and scope_confirmation_valid
            else "confirmed"
            if scope_confirmation_valid
            else "pending"
        )
        scope_confirmation_reason = (
            "当前执行模式不修改本地业务目录。"
            if not scope_confirmation_required
            else "auto-local 已将当前确定的需求契约绑定为一次任务级本地授权；内部 worktree、验证和本地回写不再重复询问。"
            if auto_local_scope_confirmation and scope_confirmation_valid
            else "改动范围确认令牌有效，等待上游评估通过后进入本地执行。"
            if scope_confirmation_valid
            else "缺少或不匹配当前范围哈希的确认令牌；不会进入改码、合入或本地执行。"
        )
        patch_readiness = None
        if resolved_execution_mode == "worktree":
            patch_readiness = evaluate_patch_readiness(
                demand_text=workflow_demand_text,
                yunxiao_evidence=yunxiao_evidence,
                requirement_evidence=requirement_evidence,
                evidence_bundle=evidence_bundle.to_dict() if evidence_bundle else None,
                technical_decision=technical_decision.to_dict(),
                allowed_paths=effective_allowed_paths or [],
                verify_commands=effective_verify_commands or [],
                yunxiao_read_requested=yunxiao_read,
            )

        steps = database.get_workflow_steps(TEAM_KEY)
        run_id = database.create_run(
            team_key=TEAM_KEY,
            title=title.strip() or "手工需求",
            source_type=source_type,
            demand_text=workflow_demand_text,
            total_steps=0 if resolved_execution_mode == "core-closure-trial" else len(steps),
            llm_mode=self.llm_client.mode,
            llm_model=self.llm_client.model_name,
        )
        database.add_artifact(
            run_id,
            "worktree_startup_recovery_json",
            "v0.64 Worktree 启动恢复检查",
            json.dumps(worktree_startup_recovery, ensure_ascii=False, indent=2),
        )
        database.add_artifact(
            run_id,
            "runtime_preflight_json",
            "统一运行前诊断",
            json.dumps(self.runtime_preflight, ensure_ascii=False, indent=2),
        )
        if self.evidence_warnings:
            database.add_artifact(
                run_id,
                "evidence_warnings_json",
                "只读证据缺口与恢复动作",
                json.dumps(self.evidence_warnings, ensure_ascii=False, indent=2),
            )
        if yunxiao_evidence is not None:
            database.add_artifact(run_id, "yunxiao_evidence_json", "v0.8.4 云效只读证据", json.dumps(yunxiao_evidence, ensure_ascii=False, indent=2))
        if requirement_evidence is not None:
            database.add_artifact(
                run_id,
                "requirement_evidence_json",
                "v0.24 需求来源归一化证据 JSON",
                json.dumps(requirement_evidence, ensure_ascii=False, indent=2),
            )
            database.add_artifact(
                run_id,
                "requirement_evidence_markdown",
                "v0.24 需求来源归一化证据",
                requirement_evidence_to_markdown(requirement_evidence),
            )
        if conversation_evidence is not None:
            database.add_artifact(
                run_id,
                "conversation_evidence_json",
                "v1 对话与用户确认事实 JSON",
                json.dumps(conversation_evidence, ensure_ascii=False, indent=2),
            )
            database.add_artifact(
                run_id,
                "conversation_evidence_markdown",
                "v1 对话与用户确认事实",
                conversation_evidence_to_markdown(conversation_evidence),
            )
        if error_chain_closure.get("required"):
            database.add_artifact(
                run_id,
                "error_chain_closure_json",
                "v1 截图错误链路闭环门禁 JSON",
                json.dumps(error_chain_closure, ensure_ascii=False, indent=2),
            )
            database.add_artifact(
                run_id,
                "error_chain_closure_markdown",
                "v1 截图错误链路闭环门禁",
                error_chain_closure_to_markdown(error_chain_closure),
            )
        if database_capability is not None:
            database.add_artifact(
                run_id,
                "database_capability_evidence_json",
                "数据库 capability 证据 JSON",
                json.dumps(
                    {
                        "status": database_capability.status,
                        "events": list(database_capability.events),
                        "results": database_capability.data.get("results") or {},
                        "blockers": list(database_blockers),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        if database_blockers:
            self._store_task_capability_blockers(run_id, database_blockers)
            governance_execution_blocked = True
            governance_error = (
                "数据库 capability 已阻断："
                + "；".join(database_blockers)
            )
        database.add_artifact(run_id, "requirement_calibration_json", "v0.15 需求理解确认卡 JSON", requirement_calibration_to_json(requirement_calibration))
        database.add_artifact(run_id, "requirement_calibration_markdown", "v0.15 需求理解确认卡", requirement_calibration_to_markdown(requirement_calibration))
        database.add_artifact(
            run_id,
            "requirement_understanding_json",
            "v1 改码前理解证据包 JSON",
            requirement_understanding.to_json(),
        )
        database.add_artifact(
            run_id,
            "requirement_understanding_markdown",
            "v1 改码前理解证据包",
            requirement_understanding.to_markdown(),
        )
        if requested_execution_mode != resolved_execution_mode:
            database.add_artifact(
                run_id,
                "execution_route_json",
                "v0.38 自动本地执行路线",
                json.dumps(
                    {
                        "requested_execution_mode": requested_execution_mode,
                        "resolved_execution_mode": resolved_execution_mode,
                        "policy": "自动本地路线只复用核心闭环；不满足需求契约、工程交接、worktree、专项验证或独立 diff 审查时会阻断，不退回固定九步骤链。",
                        "remote_actions": "不会创建分支、提交、推送、合并、发布或写入外部系统。",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        if fast_local_decision is not None:
            database.add_artifact(
                run_id,
                "fast_local_decision_json",
                "v0.43 小需求快速路径判定",
                json.dumps(fast_local_decision, ensure_ascii=False, indent=2),
            )
        self._store_technical_decision_artifacts(
            run_id,
            technical_decision,
            multi_service_evidence=multi_service_evidence,
        )
        database.add_artifact(run_id, "change_ownership_json", "v0.58 需求变更归属矩阵 JSON", change_ownership_matrix.to_json())
        database.add_artifact(run_id, "change_ownership_markdown", "v0.58 需求变更归属矩阵", change_ownership_matrix.to_markdown())
        if patch_readiness is not None:
            database.add_artifact(run_id, "clarification_gate_json", "v0.7.4 业务澄清闸口", patch_readiness.to_json())
            database.add_artifact(run_id, "patch_readiness_markdown", "v0.7.4 Patch Readiness", patch_readiness.to_markdown())
        database.add_artifact(run_id, "acceptance_matrix_json", "v0.8.7 需求验收矩阵 JSON", matrix_to_json(acceptance_matrix))
        database.add_artifact(run_id, "acceptance_matrix_markdown", "v0.8.7 需求验收矩阵", matrix_to_markdown(acceptance_matrix))
        if effective_governance_mode != "legacy":
            artifact_error = self._store_requirement_governance_artifacts(
                run_id,
                governance_result=governance_result,
                single_pass_contract=single_pass_contract,
            )
            if artifact_error:
                governance_ready = False
                governance_error = governance_error or (
                    "需求治理工件写入失败，enforce 模式禁止进入执行阶段。"
                    if effective_governance_mode == "enforce"
                    else artifact_error
                )
                governance_execution_blocked = (
                    governance_execution_blocked
                    or effective_governance_mode == "enforce"
                )
        self._store_scope_confirmation_artifacts(
            run_id,
            binding=scope_confirmation_binding,
            status=(
                "blocked"
                if governance_execution_blocked and scope_confirmation_required
                else scope_confirmation_status
            ),
            reason=(
                governance_error
                if governance_execution_blocked and scope_confirmation_required
                else scope_confirmation_reason
            ),
        )
        if evidence_bundle is not None:
            database.add_artifact(run_id, "evidence_json", "只读工程证据包 JSON", evidence_bundle.to_json())
            database.add_artifact(run_id, "evidence_markdown", "只读工程证据包", evidence_bundle.to_markdown())
        transaction_manager = self._build_yunxiao_transaction_manager(
            transaction_mode=yunxiao_transaction_mode,
            policy_config=yunxiao_policy_config,
            policy_key=yunxiao_policy_key or project_key,
            write_confirm=yunxiao_write_confirm,
            write_transport=yunxiao_write_transport,
            write_scope=yunxiao_write_scope,
        )
        database.add_artifact(
            run_id,
            "yunxiao_transaction_policy",
            "云效事务权限策略",
            transaction_manager.policy_summary_json(),
        )
        # Readonly runs are analysis products.  A failed mutation gate must
        # not erase the completed discovery/project/contract report; it only
        # closes the write path.  Mutating modes retain the hard stop below.
        readonly_analysis_complete = resolved_execution_mode == "readonly"
        task_stages.record(
            "governance",
            "completed"
            if (
                not governance_execution_blocked
                or (readonly_analysis_complete and not database_blockers)
            )
            else "blocked",
            (
                "analysis_complete_mutation_gate_closed"
                if (
                    governance_execution_blocked
                    and readonly_analysis_complete
                    and not database_blockers
                )
                else "governance_blocked"
                if governance_execution_blocked
                else "governance_completed"
            ),
        )
        self._store_demand_progress_artifacts(
            run_id,
            phase="pre_change",
            task_stages=task_stages,
            run_status=(
                "success"
                if readonly_analysis_complete
                and governance_execution_blocked
                and not database_blockers
                else "blocked"
                if governance_execution_blocked
                else "running"
            ),
            evaluation_status=(
                "analysis_complete_readonly"
                if readonly_analysis_complete
                and governance_execution_blocked
                and not database_blockers
                else "blocked_requirement_governance"
                if governance_execution_blocked
                else "awaiting_pre_change_scope_confirmation"
            ),
            execution_mode=resolved_execution_mode,
            requirement_calibration=requirement_calibration,
            technical_decision=technical_decision.to_dict(),
            change_ownership=change_ownership_matrix.to_dict(),
            governance=(
                governance_result.to_dict()
                if hasattr(governance_result, "to_dict")
                else governance_result
            ),
            single_pass_contract=(
                single_pass_contract.to_dict()
                if hasattr(single_pass_contract, "to_dict")
                else single_pass_contract
            ),
            scope_confirmation_status=(
                "blocked"
                if governance_execution_blocked and scope_confirmation_required
                else scope_confirmation_status
            ),
            scope_confirmation_reason=(
                governance_error
                if governance_execution_blocked and scope_confirmation_required
                else scope_confirmation_reason
            ),
            readonly_analysis_complete=readonly_analysis_complete,
        )
        if governance_execution_blocked:
            block_reason = governance_error or "需求治理未 ready，enforce 模式禁止进入 worktree 或 patch。"
            task_stages.record(
                "single_pass_contract",
                "skipped",
                "contract_governance_blocked",
            )
            task_stages.finish(
                local_engineering=("skipped", "local_governance_blocked"),
                verification=("skipped", "verification_no_change"),
            )
            if readonly_analysis_complete and not database_blockers:
                readonly_summary = (
                    "只读需求分析已完成；自动改码门禁仍关闭：" + block_reason
                )
                database.update_run(
                    run_id,
                    status="success",
                    evaluation_status="analysis_complete_readonly",
                    evaluation_summary=readonly_summary,
                    error="",
                    finished_at=database.now_iso(),
                )
                return self._finalize_task_result(
                    run_id=run_id,
                    task_stages=task_stages,
                    status="success",
                    evaluation_status="analysis_complete_readonly",
                    execution_mode=resolved_execution_mode,
                )
            database.update_run(
                run_id,
                # This is a governed stop before local engineering, not an
                # execution failure.  Keep the persisted run status aligned
                # with the user-facing result and the stage ledger.
                status="blocked",
                evaluation_status="blocked_requirement_governance",
                evaluation_summary=block_reason,
                error=block_reason,
                finished_at=database.now_iso(),
            )
            return self._finalize_task_result(
                run_id=run_id,
                task_stages=task_stages,
                status="blocked",
                evaluation_status="blocked_requirement_governance",
                execution_mode=resolved_execution_mode,
            )

        if resolved_execution_mode == "core-closure-trial":
            core_closure_started = time.perf_counter()
            acceptance_contract_result = precomputed_acceptance_contract_result
            return self._run_core_closure_trial(
                run_id=run_id,
                routing_result=routing_result,
                title=title,
                demand_text=base_workflow_demand_text,
                requirement_calibration=requirement_calibration,
                technical_decision=technical_decision,
                change_ownership_matrix=change_ownership_matrix.to_dict(),
                acceptance_matrix=acceptance_matrix,
                project_path=primary_project_path,
                evidence_bundle=evidence_bundle,
                allowed_paths=effective_allowed_paths or [],
                verify_commands=effective_verify_commands or [],
                acceptance_contract_result=acceptance_contract_result,
                worktree_dir=worktree_dir,
                max_edit_rounds=max_edit_rounds,
                apply_approved_diff=apply_approved_diff,
                auto_local_performance=auto_local_performance,
                core_closure_started=core_closure_started,
                requirement_governance=effective_governance_mode,
                governance_result=governance_result,
                single_pass_contract=single_pass_contract,
                governance_error=governance_error,
                capability_contract_authoritative=capability_contract_authoritative,
                knowledge_candidate=knowledge_candidate,
                task_stages=task_stages,
                pre_change_confirmation=pre_change_confirmation,
                scope_confirmation_binding=scope_confirmation_binding,
                auto_local_scope_confirmation=auto_local_scope_confirmation,
            )

        contract_ready = (
            single_pass_contract is not None
            and single_pass_contract.status == "ready"
        )
        task_review_contract = build_requirement_contract_from_single_pass(
            title=title,
            demand_text=workflow_demand_text,
            governance_result=governance_result,
            single_pass_contract=single_pass_contract,
            apply_to_project=True,
        )
        task_stages.record(
            "single_pass_contract",
            "completed" if contract_ready else "skipped",
            (
                "contract_validated"
                if contract_ready
                else "contract_unavailable"
            ),
        )
        outputs_by_order: dict[int, dict] = {}
        retry_feedback_by_order: dict[int, str] = {}
        retry_round = 0
        start_order = 1
        final_evaluation: EvaluationResult | None = None

        while True:
            database.update_run(run_id, status="running", retry_rounds=retry_round)
            step_failed = False
            for step in steps:
                if step["step_order"] < start_order:
                    continue
                database.update_run(run_id, current_step=step["step_order"])
                step_input = self._build_step_input(
                    demand_text=demand_text,
                    evidence_bundle=evidence_bundle,
                    requirement_calibration=requirement_calibration,
                    technical_decision=technical_decision,
                    acceptance_matrix=acceptance_matrix,
                    step=step,
                    previous_outputs=[outputs_by_order[order] for order in sorted(outputs_by_order) if order < step["step_order"]],
                    review_feedback=retry_feedback_by_order.get(step["step_order"], ""),
                    attempt_round=retry_round,
                )
                started_at = database.now_iso()
                started = time.perf_counter()
                try:
                    response = self.llm_client.complete(
                        system_prompt=step["expert_prompt"],
                        user_prompt=step_input,
                        step_key=step["step_key"],
                        expert_name=step["expert_name"],
                    )
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    finished_at = database.now_iso()
                    database.insert_step_run(
                        run_id=run_id,
                        step=step,
                        status="success",
                        input_text=step_input,
                        output_text=response.content,
                        duration_ms=duration_ms,
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                        attempt_round=retry_round,
                        review_feedback=retry_feedback_by_order.get(step["step_order"], ""),
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                    outputs_by_order[step["step_order"]] = {
                        "step_key": step["step_key"],
                        "step_name": step["step_name"],
                        "expert_name": step["expert_name"],
                        "output": response.content,
                    }
                except Exception as exc:
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    finished_at = database.now_iso()
                    database.insert_step_run(
                        run_id=run_id,
                        step=step,
                        status="failed",
                        input_text=step_input,
                        error=str(exc),
                        duration_ms=duration_ms,
                        attempt_round=retry_round,
                        review_feedback=retry_feedback_by_order.get(step["step_order"], ""),
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                    step_failed = True
                    database.update_run(run_id, error=str(exc))
                    if step["stop_on_failure"]:
                        break

            latest_steps = database.get_latest_step_runs(run_id)
            final_evaluation = self.evaluator.evaluate(
                demand_text=demand_text,
                steps=latest_steps,
                llm_mode=self.llm_client.mode,
                evidence_bundle=evidence_bundle.to_dict() if evidence_bundle else None,
                acceptance_matrix=acceptance_matrix,
            )
            final_evaluation = gate_readonly_evaluation(
                final_evaluation,
                gate_blocked=readonly_governance_gate_blocked,
                reason=governance_error,
            )
            evaluation_summary = final_evaluation.summary
            if (
                requirement_governance == "observe"
                and governance_error
                and governance_error not in evaluation_summary
            ):
                evaluation_summary += "\n提醒：" + governance_error
            database.update_run(
                run_id,
                evaluation_status=final_evaluation.status,
                evaluation_summary=evaluation_summary,
                retry_rounds=retry_round,
            )

            if final_evaluation.status in {"pass", "analysis_complete_readonly"}:
                database.update_run(
                    run_id,
                    status="success",
                    current_step=len(steps),
                    finished_at=database.now_iso(),
                )
                break

            if final_evaluation.status == "failed" or retry_round >= self.max_retries:
                status_message = final_evaluation.summary
                if step_failed and not status_message:
                    status_message = "执行阶段失败，且自动返工未恢复。"
                database.update_run(
                    run_id,
                    status="failed",
                    error=status_message,
                    finished_at=database.now_iso(),
                )
                break

            retry_round += 1
            start_order = final_evaluation.first_bad_step_order or 1
            feedback = self._build_retry_feedback(final_evaluation)
            for order in list(outputs_by_order):
                if order >= start_order:
                    del outputs_by_order[order]
            for step in steps:
                if step["step_order"] >= start_order:
                    retry_feedback_by_order[step["step_order"]] = feedback
            database.update_run(run_id, status="retrying", retry_rounds=retry_round)

        if final_evaluation is not None:
            database.add_artifact(
                run_id,
                "evaluation",
                "Harness自动审核结果",
                json.dumps(final_evaluation.to_dict(), ensure_ascii=False, indent=2),
            )
        run_after_workflow = database.get_run(run_id) or {}
        if (
            execution_mode in {"worktree", "fullstack-worktree", "single-demand-trial"}
            and run_after_workflow.get("status") == "success"
            and final_evaluation
            and final_evaluation.status == "pass"
            and not scope_confirmation_valid
        ):
            gate_message = (
                "改动前范围确认未通过：请使用报告中的 CONFIRM-SCOPE 令牌重新运行；"
                "令牌只对当前项目、路径、验证命令和变更合同有效。"
            )
            database.update_run(
                run_id,
                status="failed",
                evaluation_status="awaiting_pre_change_scope_confirmation",
                evaluation_summary=gate_message,
                error=gate_message,
                finished_at=database.now_iso(),
            )
            task_stages.finish(
                local_engineering=("skipped", "local_scope_confirmation_blocked"),
                verification=("skipped", "verification_no_change"),
            )
            return self._finalize_task_result(
                run_id=run_id,
                task_stages=task_stages,
                status="blocked",
                evaluation_status="awaiting_pre_change_scope_confirmation",
                execution_mode=resolved_execution_mode,
            )
        if execution_mode == "worktree":
            if run_after_workflow.get("status") == "success" and final_evaluation and final_evaluation.status == "pass":
                worktree_gate = governed_worktree_execution_blocker(
                    governance_ready=governance_ready,
                    contract_ready=contract_ready,
                )
                if worktree_gate:
                    worktree_result = WorktreeExecutionResult(
                        status="failed",
                        summary=worktree_gate,
                        worktree_path=str(Path(worktree_dir) / f"run_{run_id}"),
                        allowed_paths=effective_allowed_paths or [],
                        attempts=[],
                        manifest={
                            "status": "blocked_governance_contract",
                            "governance_ready": governance_ready,
                            "contract_ready": contract_ready,
                        },
                    )
                elif patch_readiness is not None and not patch_readiness.can_patch:
                    worktree_result = WorktreeExecutionResult(
                        status="failed",
                        summary=patch_readiness.summary,
                        worktree_path=str(Path(worktree_dir) / f"run_{run_id}"),
                        allowed_paths=effective_allowed_paths or [],
                        attempts=[],
                        manifest={
                            "status": "blocked_needs_clarification",
                            "patch_readiness": patch_readiness.to_dict(),
                            "policy": "v0.7.4 证据不足时不生成 patch。",
                        },
                    )
                else:
                    worktree_result = self._run_worktree_execution(
                        run_id=run_id,
                        demand_text=workflow_demand_text,
                        project_path=primary_project_path,
                        evidence_bundle=evidence_bundle,
                        allowed_paths=effective_allowed_paths or [],
                        verify_commands=effective_verify_commands or [],
                        worktree_dir=worktree_dir,
                        max_edit_rounds=max_edit_rounds,
                        apply_to_project=not self._capability_mutations_enforced(),
                    )
                    diff_review = (
                        review_final_diff(
                            contract=task_review_contract,
                            project_path=primary_project_path or "",
                            final_diff=worktree_result.final_diff,
                            verification_passed=(
                                worktree_result.status == "success"
                            ),
                        )
                        if self._capability_mutations_enforced()
                        else None
                    )
                    worktree_result = self._route_worktree_local_apply(
                        worktree_result,
                        routing_result=routing_result,
                        contract_ready=contract_ready,
                        project_path=primary_project_path,
                        allowed_paths=effective_allowed_paths or [],
                        verify_commands=effective_verify_commands or [],
                        review_contract=task_review_contract,
                        diff_review=diff_review,
                        acceptance_contract_result=None,
                    )
                self._store_worktree_artifacts(run_id, worktree_result)
                if worktree_result.status != "success":
                    database.update_run(
                        run_id,
                        status="failed",
                        error=worktree_result.summary,
                        finished_at=database.now_iso(),
                    )
            else:
                database.add_artifact(
                    run_id,
                    "worktree_summary_markdown",
                    "v0.7.4 Worktree 受控改码结果",
                    "## v0.7.4 Worktree 受控改码结果\n\n- 状态：skipped\n- 结论：专家团报告或 Evaluator 未通过，未进入改码阶段。",
                )
        elif execution_mode == "fullstack-worktree":
            if run_after_workflow.get("status") == "success" and final_evaluation and final_evaluation.status == "pass":
                fullstack_result = self._run_fullstack_execution(
                    run_id=run_id,
                    demand_text=workflow_demand_text,
                    project_root=project_root,
                    technical_decision=fullstack_technical_decision,
                    verify_commands=fullstack_verify_commands,
                    worktree_dir=worktree_dir,
                    authority_mode=fullstack_authority_mode,
                    authoritative_contract=fullstack_authoritative_contract,
                )
                self._store_fullstack_artifacts(run_id, fullstack_result)
                if fullstack_result.status != "success":
                    database.update_run(
                        run_id,
                        status="failed",
                        error=fullstack_result.summary,
                        finished_at=database.now_iso(),
                    )
            else:
                database.add_artifact(
                    run_id,
                    "fullstack_summary_markdown",
                    "v0.8.9 多项目 Fullstack Worktree 结果",
                    "## v0.8.9 多项目 Fullstack Worktree 结果\n\n- 状态：skipped\n- 结论：专家团报告或 Evaluator 未通过，未进入多项目改码阶段。",
                )
        elif execution_mode == "precommit-verify":
            if run_after_workflow.get("status") == "success" and final_evaluation and final_evaluation.status == "pass":
                precommit_result = self._run_precommit_verification(
                    run_id=run_id,
                    project_root=project_root,
                    project_path=primary_project_path,
                    allowed_paths=effective_allowed_paths or [],
                    verify_commands=effective_verify_commands or [],
                    title=title,
                    entity_id=yunxiao_entity_id,
                    demand_text=demand_text,
                    method_evidence=method_evidence,
                    method_test_commands=method_test_commands or [],
                    ui_evidence_paths=ui_evidence_paths or [],
                    ui_capture_commands=ui_capture_commands or [],
                    worktree_dir=worktree_dir,
                )
                self._store_precommit_verification_artifacts(run_id, precommit_result)
                if precommit_result.status != "success":
                    database.update_run(
                        run_id,
                        status="failed",
                        error=precommit_result.summary,
                        finished_at=database.now_iso(),
                    )
            else:
                database.add_artifact(
                    run_id,
                    "verification_matrix_markdown",
                    "v0.9.1 提交前验证矩阵",
                    "## v0.9.1 提交前验证矩阵\n\n- 状态：skipped\n- 结论：专家团报告或 Evaluator 未通过，未进入提交前验证阶段。",
                )
        elif execution_mode == "review-worktree":
            if run_after_workflow.get("status") == "success" and final_evaluation and final_evaluation.status == "pass":
                review_result = self._run_review_execution(
                    run_id=run_id,
                    project_path=primary_project_path,
                    review_commit=review_commit,
                    review_base=review_base,
                    review_context=review_context,
                    allowed_paths=effective_allowed_paths or [],
                    verify_commands=effective_verify_commands or [],
                    worktree_dir=worktree_dir,
                )
                self._store_review_artifacts(run_id, review_result)
                if review_result.status != "success":
                    database.update_run(
                        run_id,
                        status="failed",
                        error=review_result.summary,
                        finished_at=database.now_iso(),
                    )
            else:
                database.add_artifact(
                    run_id,
                    "review_summary_markdown",
                    "v0.7.3 已提交 Diff 审查结果",
                    "## v0.7.3 已提交 Diff 审查结果\n\n- 状态：skipped\n- 结论：专家团报告或 Evaluator 未通过，未进入提交审查验证阶段。",
                )
        elif execution_mode == "single-demand-trial":
            worktree_result: WorktreeExecutionResult | None = None
            run_after_trial_workflow = database.get_run(run_id) or {}
            if run_after_trial_workflow.get("status") == "success" and final_evaluation and final_evaluation.status == "pass":
                trial_blockers = list((technical_decision.implementation_decision or {}).get("blockers") or [])
                trial_execution_blocker = single_demand_execution_blocker(
                    governance_ready=governance_ready,
                    contract_ready=contract_ready,
                    technical_can_patch=technical_decision.can_patch,
                    technical_blockers=trial_blockers,
                )
                if trial_execution_blocker:
                    database.update_run(
                        run_id,
                        status="failed",
                        error=trial_execution_blocker,
                        finished_at=database.now_iso(),
                    )
                elif not primary_project_path:
                    database.update_run(
                        run_id,
                        status="failed",
                        error="single-demand-trial 缺少主项目路径，不能进入受控 worktree 改码。",
                        finished_at=database.now_iso(),
                    )
                elif not effective_allowed_paths:
                    database.update_run(
                        run_id,
                        status="failed",
                        error="single-demand-trial 缺少允许修改路径，不能进入受控 worktree 改码。",
                        finished_at=database.now_iso(),
                    )
                else:
                    worktree_result = self._run_worktree_execution(
                        run_id=run_id,
                        demand_text=workflow_demand_text,
                        project_path=primary_project_path,
                        evidence_bundle=evidence_bundle,
                        allowed_paths=effective_allowed_paths or [],
                        verify_commands=effective_verify_commands or [],
                        worktree_dir=worktree_dir,
                        max_edit_rounds=max_edit_rounds,
                        apply_to_project=not self._capability_mutations_enforced(),
                    )
                    diff_review = (
                        review_final_diff(
                            contract=task_review_contract,
                            project_path=primary_project_path or "",
                            final_diff=worktree_result.final_diff,
                            verification_passed=(
                                worktree_result.status == "success"
                            ),
                        )
                        if self._capability_mutations_enforced()
                        else None
                    )
                    worktree_result = self._route_worktree_local_apply(
                        worktree_result,
                        routing_result=routing_result,
                        contract_ready=contract_ready,
                        project_path=primary_project_path,
                        allowed_paths=effective_allowed_paths or [],
                        verify_commands=effective_verify_commands or [],
                        review_contract=task_review_contract,
                        diff_review=diff_review,
                        acceptance_contract_result=None,
                    )
                    self._store_worktree_artifacts(run_id, worktree_result)
                    if worktree_result.status != "success":
                        database.update_run(
                            run_id,
                            status="failed",
                            error=worktree_result.summary,
                            finished_at=database.now_iso(),
                        )
            trial_package = build_single_demand_trial_package(
                run_id=run_id,
                technical_decision=technical_decision.to_dict(),
                acceptance_matrix=acceptance_matrix,
                project_paths=project_paths,
                allowed_paths=effective_allowed_paths or [],
                verify_commands=effective_verify_commands or [],
                worktree_result=worktree_result,
                transaction_mode=yunxiao_transaction_mode,
                write_scope=yunxiao_write_scope,
            )
            self._store_single_demand_trial_artifacts(run_id, trial_package)
            if trial_package.status != "success" and (database.get_run(run_id) or {}).get("status") == "success":
                database.update_run(
                    run_id,
                    status="failed",
                    error=trial_package.summary,
                    finished_at=database.now_iso(),
                )
        if yunxiao_transaction_mode in {"dry-run", "write"}:
            transaction_plan = self._build_yunxiao_transaction_plan(
                run_id=run_id,
                manager=transaction_manager,
                demand_text=workflow_demand_text,
                title=title,
                project_key=yunxiao_policy_key or project_key,
                yunxiao_url=yunxiao_url,
                entity_kind=yunxiao_entity_kind,
                entity_id=yunxiao_entity_id,
                current_status=yunxiao_current_status,
                target_assignee=yunxiao_target_assignee,
                target_status=yunxiao_target_status,
                target_iteration=yunxiao_target_iteration,
                screenshots=yunxiao_screenshots or [],
                service_change_file=yunxiao_service_change_file,
                artifacts=yunxiao_artifacts or [],
                project_paths=project_paths,
                human_confirmed=yunxiao_human_confirmed,
                execution_mode=execution_mode,
                verify_commands=effective_verify_commands or [],
                evidence_bundle=evidence_bundle,
            )
            self._store_yunxiao_transaction_plan_artifacts(run_id, transaction_plan)
        run = database.get_run(run_id) or {}
        artifact_kinds = {
            artifact["kind"] for artifact in database.get_artifacts(run_id)
        }
        engineering_artifacts = {
            "worktree_manifest_json",
            "fullstack_manifest_json",
            "review_manifest_json",
        }
        if execution_mode == "readonly":
            local_stage = ("skipped", "local_readonly")
            verification_stage = ("skipped", "verification_readonly")
        elif artifact_kinds & engineering_artifacts:
            local_stage = ("completed", "local_artifact_recorded")
            verification_passed = run.get("status") == "success"
            verification_stage = (
                "completed" if verification_passed else "failed",
                (
                    "verification_passed"
                    if verification_passed
                    else "verification_failed"
                ),
            )
        elif "precommit_manifest_json" in artifact_kinds:
            local_stage = ("skipped", "local_precommit")
            verification_stage = (
                "completed" if run.get("status") == "success" else "failed",
                "verification_precommit",
            )
        else:
            local_stage = ("skipped", "local_upstream_blocked")
            verification_stage = ("skipped", "verification_no_change")
        knowledge_stage, knowledge_blockers = (
            self._create_task_knowledge_candidate(
                run_id,
                enabled=(
                    capability_contract_authoritative
                    and run.get("status") == "success"
                ),
                candidate_payload=knowledge_candidate,
                routing_result=routing_result,
            )
        )
        if knowledge_blockers:
            blocker_summary = "；".join(knowledge_blockers)
            database.update_run(
                run_id,
                status="failed",
                evaluation_status="blocked_task_capability",
                evaluation_summary=blocker_summary,
                error=blocker_summary,
                finished_at=database.now_iso(),
            )
            run = database.get_run(run_id) or run
        task_stages.finish(
            local_engineering=local_stage,
            verification=verification_stage,
            knowledge_candidate=knowledge_stage,
        )
        return self._finalize_task_result(
            run_id=run_id,
            task_stages=task_stages,
            status=(
                "blocked"
                if knowledge_blockers
                else run.get("status", "failed")
            ),
            evaluation_status=run.get("evaluation_status", "failed"),
            execution_mode=resolved_execution_mode,
        )

    def _build_step_input(
        self,
        *,
        demand_text: str,
        evidence_bundle: EvidenceBundle | None,
        requirement_calibration: dict | None,
        technical_decision: TechnicalDecisionResult | None,
        acceptance_matrix: dict | None,
        step: dict,
        previous_outputs: list[dict],
        review_feedback: str,
        attempt_round: int,
    ) -> str:
        upstream = "\n\n".join(
            f"### {item['step_name']} / {item['expert_name']}\n{compress_text(item['output'], 2600)}"
            for item in previous_outputs
        )
        if not upstream:
            upstream = "暂无，上游为空，本步骤是首个阶段。"
        feedback = review_feedback.strip() or "无。本轮不是返工，按专家职责直接输出。"
        mock_notice = "当前为 MOCK 模式，只能用于演示，不可用于业务判断。\n\n" if self.llm_client.is_mock else ""
        evidence_context = (
            evidence_bundle.to_prompt_context()
            if evidence_bundle is not None
            else "未接入项目路径，本轮没有工程证据包；不得给出确定代码文件结论，只能说明需要人工补充项目上下文。"
        )
        calibration_context = (
            requirement_calibration_to_prompt_context(requirement_calibration)
            if requirement_calibration
            else "未生成 v0.15 需求理解确认卡；必须先确认需求来源优先级、字段/参数和值域。"
        )
        acceptance_context = (
            build_prompt_context(acceptance_matrix)
            if acceptance_matrix
            else "未生成需求验收矩阵；必须在测试验收中明确需求验收、自动验证和人工验收。"
        )
        technical_context = (
            technical_decision.to_prompt_context()
            if technical_decision is not None
            else "未生成技术自治决策；不得让用户指定技术文件名、代码规范或前后端边界，必须先基于工程证据判断。"
        )
        review_note = ""
        if evidence_bundle is not None and evidence_bundle.review:
            review_note = (
                "- 本轮是已提交 diff 审查，必须引用“已提交 Diff 审查证据”的 Review ID、提交号和变更文件；"
                "不得生成新 patch，只判断当前提交是否可进入人工代码审查/测试。\n"
            )
        return (
            f"{mock_notice}"
            f"你正在执行 HIS 需求研发专家团 Workflow。\n\n"
            f"【当前步骤】\n"
            f"- Step Key：{step['step_key']}\n"
            f"- 步骤名称：{step['step_name']}\n"
            f"- 专家：{step['expert_name']}\n"
            f"- 模式：{step['mode']}\n"
            f"- Attempt：{attempt_round}\n\n"
            f"【原始需求】\n{demand_text}\n\n"
            f"【v0.15 需求理解确认卡】\n{calibration_context}\n\n"
            f"【只读工程证据包】\n{evidence_context}\n\n"
            f"【v0.8.8 技术自治决策】\n{technical_context}\n\n"
            f"【v0.8.7 需求验收矩阵】\n{acceptance_context}\n\n"
            f"【上游专家输出】\n{upstream}\n\n"
            f"【自动审核返工意见】\n{feedback}\n\n"
            f"【强制输出结构】\n"
            f"请输出 Markdown，必须包含以下标题：\n"
            f"0. 工程证据引用\n"
            f"1. 结论\n"
            f"2. 事实依据\n"
            f"3. 待确认\n"
            f"4. 研发影响\n"
            f"5. 风险与边界\n"
            f"6. 测试验收\n"
            f"7. 下一步输入\n\n"
            f"【硬性约束】\n"
            f"- 如存在 Evidence ID，必须在“工程证据引用”中写出 Evidence ID 和引用的疑似模块/文件；没有证据不得编造文件结论。\n"
            f"- 如果未接入项目路径或没有工程证据包，必须明确写“未接入项目路径，不足以下结论给出确定代码、模块、表、接口或服务名”；"
            f"不得输出占位文件名、占位数据库表名、占位服务名、占位模块名，也不得使用 service/controller/mapper/module 等看似确定的工程对象。\n"
            f"- 没有工程证据时，不得自拟英文技术标识或反引号对象，例如 `settlement_payment_detail`、`payment-service`、`xxxController`；只能用中文描述“结算相关逻辑/支付明细存储/对账口径”等待确认方向。\n"
            f"{review_note}"
            f"- 当前阶段仅允许 read 动作，不得建议自动提交、自动推送、自动发布或直接触发 CI/CD。\n"
            f"- 真实业务云效任务仅允许读取和评论；状态、负责人、迭代、关闭只能 dry-run/fake，不能在报告中暗示已真实流转。\n"
            f"- 如果需求或用户指令要求跳过测试、直接流转、自动关闭、高风险无需人工确认，必须按“反驳/纠偏”写明不建议、原因和替代方案。\n"
            f"- 测试验收必须区分需求验收、自动验证命令和人工业务验收；未执行的验证命令只能写“建议”，不能写成已通过。\n"
            f"- 前后端边界、文件名、代码风格和验证命令由 Harness 根据代码上下文判断；只有业务规则无法从证据证明时才列为待确认。\n"
            f"- 不得编造需求未提供的信息；不确定内容必须标为待确认。\n"
            f"- 涉及医保、结算、收费、报表、对账、政策校验时，默认高风险、保守处理、要求人工确认。\n"
            f"- 输出必须能被研发、测试和人工审核直接审查。\n"
        )

    def _build_evidence_bundle(
        self,
        *,
        demand_text: str,
        project_key: str | None,
        project_path: str | Path | None,
        project_config: str | Path | None,
        review_context: dict | None = None,
    ) -> EvidenceBundle | None:
        if not project_path and not project_config:
            return None
        try:
            profile = load_project_profile(project_key=project_key, project_path=project_path, config_path=project_config)
            if profile is None:
                return None
            evidence_bundle = ProjectContextScanner(profile).scan(demand_text=demand_text)
            evidence_bundle.review = review_context
            return evidence_bundle
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            # Evidence is optional for readonly analysis. Never turn a missing
            # checkout into a fake code conclusion; callers receive the
            # structured warning through the runner state/artifacts.
            self.evidence_warnings = getattr(self, "evidence_warnings", [])
            self.evidence_warnings.append(
                {
                    "status": "unavailable",
                    "severity": "warning",
                    "retryable": True,
                    "reason_code": "project_context_unavailable",
                    "message": f"{type(exc).__name__}: {exc}",
                    "mutation_blocker": "project_context_required_for_mutation",
                }
            )
            return None

    def _build_yunxiao_transaction_manager(
        self,
        *,
        transaction_mode: str,
        policy_config: str | Path | None,
        policy_key: str | None,
        write_confirm: str = "",
        write_transport: str = "real",
        write_scope: str = "comment-only",
    ) -> YunxiaoTransactionManager:
        if transaction_mode == "dry-run":
            policy = load_yunxiao_policy(policy_config or None, project_key=policy_key or "default")
            return YunxiaoTransactionManager.dry_run(policy=policy)
        if transaction_mode == "write":
            policy = load_yunxiao_policy(policy_config or None, project_key=policy_key or "default")
            return YunxiaoTransactionManager.controlled_write(
                policy=policy,
                write_confirm=write_confirm,
                write_transport=write_transport,
                write_scope=write_scope,
            )
        return self.yunxiao_transactions

    def _build_retry_feedback(self, evaluation: EvaluationResult) -> str:
        lines = ["上一轮自动审核未通过，请按以下问题返工，不要跳过："]
        for issue in evaluation.issues:
            lines.append(f"- 第 {issue.step_order} 步 {issue.step_key}：{issue.message}")
        lines.append("返工要求：补齐缺失结构，明确事实依据、待确认项、风险等级和测试验收。")
        return "\n".join(lines)

    def _run_worktree_execution(
        self,
        *,
        run_id: int,
        demand_text: str,
        project_path: str | Path | None,
        evidence_bundle: EvidenceBundle | None,
        allowed_paths: list[str],
        verify_commands: list[str],
        worktree_dir: str | Path,
        max_edit_rounds: int,
        apply_to_project: bool = True,
    ) -> WorktreeExecutionResult:
        if not project_path:
            return WorktreeExecutionResult(
                status="failed",
                summary="worktree 模式必须提供 --project-path",
                worktree_path=str(Path(worktree_dir) / f"run_{run_id}"),
                allowed_paths=allowed_paths,
            )
        report_markdown = build_markdown_report(run_id)
        executor = WorktreeCodeExecutor(self.llm_client)
        return executor.execute(
            WorktreeExecutionOptions(
                project_path=str(project_path),
                run_id=run_id,
                demand_text=demand_text,
                report_markdown=report_markdown,
                evidence_bundle=evidence_bundle.to_dict() if evidence_bundle else None,
                worktree_root=str(worktree_dir),
                allowed_paths=allowed_paths,
                verify_commands=verify_commands,
                max_edit_rounds=max_edit_rounds,
                apply_to_project=apply_to_project,
            )
        )

    def _route_worktree_local_apply(
        self,
        result: WorktreeExecutionResult,
        *,
        routing_result: TaskIntentRoutingResult,
        contract_ready: bool,
        project_path: str | Path | None,
        allowed_paths: list[str],
        verify_commands: list[str],
        review_contract: RequirementContract,
        diff_review: DiffReview | None,
        acceptance_contract_result: AcceptanceContractResult | None,
    ) -> WorktreeExecutionResult:
        if not self._capability_mutations_enforced() or result.status != "success":
            return result
        final_diff = result.final_diff
        canonical_project_path = (
            Path(project_path).expanduser().resolve()
            if project_path
            else None
        )
        if (
            contract_ready is not True
            or canonical_project_path is None
            or type(final_diff) is not str
            or not final_diff.strip()
        ):
            blockers = ("local_contract_not_ready",)
        elif (
            type(review_contract) is not RequirementContract
            or type(diff_review) is not DiffReview
            or review_contract.status != "ready"
            or review_contract.apply_to_project is not True
            or review_contract.allowed_paths != tuple(allowed_paths)
            or review_contract.verify_commands != tuple(verify_commands)
            or diff_review.status != "pass"
            or diff_review
            != review_final_diff(
                contract=review_contract,
                project_path=canonical_project_path,
                final_diff=final_diff,
                verification_passed=True,
                acceptance_contract_result=acceptance_contract_result,
            )
        ):
            blockers = ("local_diff_review_not_passed",)
        else:
            routed = CapabilityWorkflowOrchestrator(
                self.capability_service
            ).run_task_capabilities(
                routing_result=routing_result,
                contract_ready=True,
                project_path=str(canonical_project_path),
                expected_diff=final_diff,
                allowed_paths=review_contract.allowed_paths,
                verify_commands=review_contract.verify_commands,
            )
            blockers = tuple(routed.data.get("blockers") or ())
        message = (
            "；".join(blockers)
            if blockers
            else "git.apply-local capability 已完成本地合入。"
        )
        result.apply_to_project = {
            "status": "blocked" if blockers else "success",
            "message": message,
        }
        if blockers:
            result.status = "failed"
            result.summary = "git.apply-local 已阻断：" + message
        else:
            result.summary = "Patch 已通过 git.apply-local 合入原业务目录；未提交、未推送、未发布。"
        result.manifest["apply_to_project"] = result.apply_to_project
        return result

    def _run_core_closure_trial(
        self,
        *,
        run_id: int,
        routing_result: TaskIntentRoutingResult,
        title: str,
        demand_text: str,
        requirement_calibration: dict,
        technical_decision: TechnicalDecisionResult,
        change_ownership_matrix: dict,
        acceptance_matrix: dict,
        project_path: str | Path | None,
        evidence_bundle: EvidenceBundle | None,
        allowed_paths: list[str],
        verify_commands: list[str],
        acceptance_contract_result: AcceptanceContractResult | None,
        worktree_dir: str | Path,
        max_edit_rounds: int,
        apply_approved_diff: bool,
        auto_local_performance: dict | None = None,
        core_closure_started: float | None = None,
        requirement_governance: str = "legacy",
        governance_result: object | None = None,
        single_pass_contract: object | None = None,
        governance_error: str = "",
        capability_contract_authoritative: bool = False,
        knowledge_candidate: Mapping[str, Any] | None = None,
        task_stages: _TaskStageLedger,
        pre_change_confirmation: str,
        scope_confirmation_binding: Mapping[str, Any],
        auto_local_scope_confirmation: bool = False,
    ) -> WorkflowResult:
        if acceptance_contract_result is not None:
            self._store_acceptance_contract_artifacts(run_id, result=acceptance_contract_result)
            if acceptance_contract_result.status == "pass" and acceptance_contract_result.verify_command:
                verify_commands = list(dict.fromkeys([*verify_commands, acceptance_contract_result.verify_command]))
        technical_payload = technical_decision.to_dict()
        technical_payload["recommended_allowed_paths"] = allowed_paths
        technical_payload["recommended_verify_commands"] = verify_commands
        legacy_contract = build_requirement_contract(
            title=title,
            demand_text=demand_text,
            requirement_calibration=requirement_calibration,
            technical_decision=technical_payload,
            acceptance_matrix=acceptance_matrix,
            apply_to_project=apply_approved_diff,
            acceptance_contract_result=acceptance_contract_result,
            change_ownership_matrix=change_ownership_matrix,
        )
        if requirement_governance == "enforce":
            contract = build_requirement_contract_from_single_pass(
                title=title,
                demand_text=demand_text,
                governance_result=governance_result,
                single_pass_contract=single_pass_contract,
                apply_to_project=apply_approved_diff,
                integration_blocker=governance_error,
                legacy_contract=legacy_contract,
                acceptance_contract_result=acceptance_contract_result,
            )
            if (
                contract.status == "ready"
                and capability_contract_authoritative
                and acceptance_contract_result is not None
                and acceptance_contract_result.status == "pass"
                and acceptance_contract_result.verify_command
                not in single_pass_contract.verify_commands
            ):
                contract = replace(
                    contract,
                    status="blocked",
                    allowed_paths=(),
                    verify_commands=(),
                    blockers=(GOVERNANCE_ACCEPTANCE_ERROR,),
                )
            elif contract.status == "ready" and capability_contract_authoritative:
                contract = replace(
                    contract,
                    allowed_paths=tuple(single_pass_contract.allowed_paths),
                    verify_commands=tuple(single_pass_contract.verify_commands),
                )
        else:
            contract = legacy_contract
        handoff = build_engineering_handoff(contract=contract, technical_decision=technical_payload)
        self._store_core_closure_artifacts(run_id, contract=contract, handoff=handoff)
        task_stages.record(
            "single_pass_contract",
            "completed" if contract.status == "ready" else "blocked",
            (
                "core_contract_ready"
                if contract.status == "ready"
                else "core_contract_blocked"
            ),
        )

        # Keep the final core-closure binding semantically identical to the
        # pre-change preview.  The execution contract is adapted into the
        # legacy RequirementContract for the executor, which does not carry
        # the repository list and would otherwise change the confirmation hash
        # after the user already confirmed the exact governed scope.
        final_scope_contract_payload = contract.to_dict()
        if single_pass_contract is not None and hasattr(single_pass_contract, "to_dict"):
            original_contract_payload = single_pass_contract.to_dict()
            if original_contract_payload.get("repositories"):
                final_scope_contract_payload["repositories"] = original_contract_payload["repositories"]
        final_scope_governance_status = (
            getattr(governance_result, "status", None) or requirement_governance
        )
        final_scope_can_modify = (
            getattr(governance_result, "can_modify", None)
            if governance_result is not None
            else contract.status == "ready" and handoff.status == "ready"
        )
        final_scope_confirmation_binding = build_scope_confirmation_binding(
            execution_mode="core-closure-trial",
            technical_decision=technical_payload,
            change_ownership=change_ownership_matrix,
            governance={
                "status": final_scope_governance_status,
                "can_modify": final_scope_can_modify is True,
            },
            single_pass_contract=final_scope_contract_payload,
            allowed_paths=list(contract.allowed_paths),
            verify_commands=list(contract.verify_commands),
        )
        if auto_local_scope_confirmation:
            # The final contract is still deterministic and is derived before
            # entering the worktree.  Rebind the implicit task authorization to
            # that final contract so adding an auto-discovered command does not
            # create a needless second confirmation turn.
            pre_change_confirmation = str(
                final_scope_confirmation_binding["confirmation_token"]
            )
        final_scope_hash_matches_preview = (
            auto_local_scope_confirmation
            or final_scope_confirmation_binding["scope_hash"]
            == scope_confirmation_binding.get("scope_hash")
        )
        final_scope_confirmation_valid = (
            final_scope_hash_matches_preview
            and validate_scope_confirmation(
                pre_change_confirmation,
                final_scope_confirmation_binding["scope_hash"],
            )
        )
        if not final_scope_hash_matches_preview:
            self._store_scope_confirmation_artifacts(
                run_id,
                binding=final_scope_confirmation_binding,
                status="pending",
                reason="最终需求契约与预览范围不同，原确认自动失效；请按最终范围重新确认。",
            )
        elif not final_scope_confirmation_valid:
            self._store_scope_confirmation_artifacts(
                run_id,
                binding=final_scope_confirmation_binding,
                status="pending",
                reason="缺少当前核心闭环契约的确认令牌；不会进入 worktree。",
            )

        worktree_result: WorktreeExecutionResult | None = None
        diff_review = None
        if (
            contract.status == "ready"
            and handoff.status == "ready"
            and final_scope_confirmation_valid
        ):
            worktree_result = self._run_worktree_execution(
                run_id=run_id,
                demand_text=demand_text,
                project_path=project_path or handoff.project_path,
                evidence_bundle=evidence_bundle,
                allowed_paths=list(contract.allowed_paths),
                verify_commands=list(contract.verify_commands),
                worktree_dir=worktree_dir,
                max_edit_rounds=max_edit_rounds,
                apply_to_project=False,
            )
            diff_review = review_final_diff(
                contract=contract,
                project_path=project_path or handoff.project_path,
                final_diff=worktree_result.final_diff,
                verification_passed=worktree_result.status == "success",
                acceptance_contract_result=acceptance_contract_result,
            )
            if apply_approved_diff:
                if diff_review.status != "pass":
                    worktree_result.apply_to_project = {
                        "status": "blocked_independent_review",
                        "message": "独立 diff 审查未通过，禁止合入原业务目录。",
                    }
                elif worktree_result.status == "success":
                    if self._capability_mutations_enforced():
                        worktree_result = self._route_worktree_local_apply(
                            worktree_result,
                            routing_result=routing_result,
                            contract_ready=diff_review.status == "pass",
                            project_path=project_path or handoff.project_path,
                            allowed_paths=list(contract.allowed_paths),
                            verify_commands=list(contract.verify_commands),
                            review_contract=contract,
                            diff_review=diff_review,
                            acceptance_contract_result=(
                                acceptance_contract_result
                            ),
                        )
                        apply_result = worktree_result.apply_to_project
                    else:
                        apply_result = apply_final_diff_to_project(
                            project_path=Path(
                                project_path or handoff.project_path
                            ),
                            final_diff=worktree_result.final_diff,
                        )
                    worktree_result.apply_to_project = apply_result
                    if apply_result.get("status") == "success":
                        worktree_result.summary = "Patch 已通过独立 diff 审查并合入原业务目录；未提交、未推送、未发布。"
                    else:
                        worktree_result.status = "failed"
                        worktree_result.summary = apply_result.get("message") or "独立 diff 审查后合入原业务目录失败。"
                else:
                    worktree_result.apply_to_project = {
                        "status": "blocked_worktree_failure",
                        "message": "worktree 或专项验证未通过，禁止合入原业务目录。",
                    }
                worktree_result.manifest["apply_to_project"] = worktree_result.apply_to_project
            self._store_worktree_artifacts(run_id, worktree_result)

        closure = build_core_closure_result(
            contract=contract,
            engineering_handoff=handoff,
            worktree=worktree_result.to_dict() if worktree_result is not None else None,
            diff_review=diff_review,
        )
        self._store_core_closure_result(run_id, closure)
        evaluation_summary = closure.summary
        if requirement_governance == "enforce" and governance_error:
            evaluation_summary = governance_error + "\n" + evaluation_summary
        elif requirement_governance == "observe" and governance_error:
            evaluation_summary += "\n提醒：" + governance_error
        database.update_run(
            run_id,
            status="success" if closure.status == "ready_for_manual_review" else "failed",
            evaluation_status=closure.status,
            evaluation_summary=evaluation_summary,
            current_step=0,
            finished_at=database.now_iso(),
            error="" if closure.status == "ready_for_manual_review" else evaluation_summary,
        )
        if auto_local_performance is not None and core_closure_started is not None:
            record_auto_local_stage(
                auto_local_performance,
                key="core_closure",
                started_at=core_closure_started,
                status="completed",
                closure_status=closure.status,
            )
            finish_auto_local_performance(auto_local_performance)
            database.add_artifact(
                run_id,
                "auto_local_performance_json",
                "v0.44 auto-local 阶段耗时与路线",
                json.dumps(auto_local_performance, ensure_ascii=False, indent=2),
            )
        knowledge_stage, knowledge_blockers = (
            self._create_task_knowledge_candidate(
                run_id,
                enabled=(
                    capability_contract_authoritative
                    and closure.status == "ready_for_manual_review"
                ),
                candidate_payload=knowledge_candidate,
                routing_result=routing_result,
            )
        )
        final_status = closure.status
        final_evaluation_status = closure.status
        if knowledge_blockers:
            blocker_summary = "；".join(knowledge_blockers)
            database.update_run(
                run_id,
                status="failed",
                evaluation_status="blocked_task_capability",
                evaluation_summary=blocker_summary,
                error=blocker_summary,
                finished_at=database.now_iso(),
            )
            final_status = "blocked"
            final_evaluation_status = "blocked_task_capability"
        if worktree_result is None:
            task_stages.finish(
                local_engineering=(
                    "skipped",
                    "local_core_blocked",
                ),
                verification=("skipped", "verification_no_change"),
                knowledge_candidate=knowledge_stage,
            )
        else:
            task_stages.finish(
                local_engineering=("completed", "local_core_completed"),
                verification=(
                    "completed"
                    if worktree_result.status == "success"
                    else "failed",
                    (
                        "verification_core_passed"
                        if worktree_result.status == "success"
                        else "verification_core_failed"
                    ),
                ),
                knowledge_candidate=knowledge_stage,
            )
        return self._finalize_task_result(
            run_id=run_id,
            task_stages=task_stages,
            status=final_status,
            evaluation_status=final_evaluation_status,
            report_prefix=core_closure_to_markdown(closure) + "\n\n",
            markdown_title="HIS Harness Core Closure 运行报告",
            json_title="HIS Harness Core Closure JSON",
            execution_mode="core-closure-trial",
        )

    def _run_fullstack_execution(
        self,
        *,
        run_id: int,
        demand_text: str,
        project_root: str | Path,
        technical_decision: TechnicalDecisionResult,
        verify_commands: list[str],
        worktree_dir: str | Path,
        authority_mode: str,
        authoritative_contract: dict | None = None,
    ) -> FullstackExecutionResult:
        report_markdown = build_markdown_report(run_id)
        multi_service_contract = technical_decision.multi_service_change_contract or {}
        if (
            multi_service_contract.get("schema_version")
            == MULTI_SERVICE_CHANGE_CONTRACT_SCHEMA_VERSION
        ):
            multi_result = MultiServiceWorktreeExecutor(self.llm_client).execute(
                MultiServiceExecutionOptions(
                    contract=multi_service_contract,
                    run_id=run_id,
                    demand_text=demand_text,
                    report_markdown=report_markdown,
                    worktree_root=str(worktree_dir),
                    # This integration is verification-only.  A later,
                    # separately authorized action may opt into local batch
                    # write-back after the aggregate diff is reviewed.
                    apply_to_projects=False,
                    cleanup_worktrees=False,
                )
            )
            targets = []
            for name, repository in (multi_service_contract.get("repositories") or {}).items():
                execution = multi_result.repositories.get(name) or {}
                attempts = execution.get("attempts") or []
                targets.append(
                    {
                        "key": name,
                        "name": name,
                        "role": repository.get("role") or "",
                        "project_path": repository.get("project_path") or "",
                        "allowed_paths": list(repository.get("allowed_paths") or []),
                        "verify_commands": list(repository.get("verify_commands") or []),
                        "status": execution.get("status") or "not_run",
                        "changed_paths": list(attempts[-1].get("changed_paths", [])) if attempts else [],
                    }
                )
            return FullstackExecutionResult(
                status=multi_result.status,
                summary=multi_result.summary,
                targets=targets,
                final_diffs=multi_result.final_diffs,
                apply_to_projects=multi_result.apply_to_projects,
                cleanup=multi_result.cleanup,
                manifest={
                    **multi_result.manifest,
                    "executor": "multi_service_worktree_executor",
                    "aggregate_review": multi_result.aggregate_review,
                    "repositories": multi_result.repositories,
                },
            )
        executor = FullstackWorktreeExecutor()
        result = executor.execute(
            FullstackExecutionOptions(
                run_id=run_id,
                demand_text=demand_text,
                report_markdown=report_markdown,
                project_root=str(project_root),
                authority_mode=authority_mode,
                technical_decision=technical_decision.to_dict(),
                worktree_root=str(worktree_dir),
                verify_commands=verify_commands,
                authoritative_contract=authoritative_contract,
                apply_to_project=not self._capability_mutations_enforced(),
            )
        )
        if self._capability_mutations_enforced() and result.status == "success":
            result.status = "failed"
            result.summary = (
                "git.apply-local 仅支持单仓原子本地落地；"
                "多仓 fullstack 未开放批量 apply capability，未修改原业务目录。"
            )
            result.manifest["status"] = "blocked"
            result.manifest["summary"] = result.summary
        return result

    def _run_precommit_verification(
        self,
        *,
        run_id: int,
        project_root: str | Path,
        project_path: str | Path | None = None,
        allowed_paths: list[str] | None = None,
        verify_commands: list[str] | None = None,
        title: str = "",
        entity_id: str = "",
        demand_text: str = "",
        method_evidence: dict | None = None,
        method_test_commands: list[str] | None = None,
        ui_evidence_paths: list[str] | None = None,
        ui_capture_commands: list[str] | None = None,
        worktree_dir: str | Path,
    ) -> PrecommitVerificationResult:
        verifier = PrecommitVerifier()
        return verifier.execute(
            PrecommitVerificationOptions(
                run_id=run_id,
                project_root=str(project_root),
                project_path=str(project_path or ""),
                allowed_paths=allowed_paths or [],
                verify_commands=verify_commands or [],
                title=title,
                entity_id=entity_id,
                demand_text=demand_text,
                method_evidence=method_evidence or {},
                method_test_commands=method_test_commands or [],
                ui_evidence_paths=ui_evidence_paths or [],
                ui_capture_commands=ui_capture_commands or [],
                worktree_root=str(worktree_dir),
            )
        )

    def _store_technical_decision_artifacts(
        self,
        run_id: int,
        result: TechnicalDecisionResult,
        *,
        multi_service_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        database.add_artifact(run_id, "technical_decision_json", "v0.8.8 技术自治决策 JSON", result.to_json())
        database.add_artifact(run_id, "technical_decision_markdown", "v0.8.8 技术自治决策", result.to_markdown())
        multi_service_contract = result.multi_service_change_contract or {}
        if multi_service_contract:
            database.add_artifact(
                run_id,
                "multi_service_change_contract_json",
                "v1 多项目改动合同 JSON",
                json.dumps(multi_service_contract, ensure_ascii=False, indent=2),
            )
            database.add_artifact(
                run_id,
                "multi_service_change_contract_markdown",
                "v1 多项目改动合同",
                _multi_service_contract_markdown(multi_service_contract),
            )
        if multi_service_evidence:
            database.add_artifact(
                run_id,
                "multi_service_evidence_selection_json",
                "多项目证据补充选择 JSON",
                json.dumps(dict(multi_service_evidence), ensure_ascii=False, indent=2),
            )
        database.add_artifact(
            run_id,
            "project_selection_markdown",
            "v0.8.8 项目选择",
            result.artifacts.get("project_selection_markdown") or "",
        )
        database.add_artifact(
            run_id,
            "field_provenance_markdown",
            "v0.8.8 字段来源",
            result.artifacts.get("field_provenance_markdown") or "",
        )
        database.add_artifact(
            run_id,
            "implementation_decision_markdown",
            "v0.8.8 实施决策",
            result.artifacts.get("implementation_decision_markdown") or "",
        )
        database.add_artifact(
            run_id,
            "service_graph_markdown",
            "v0.8.8 服务图",
            result.artifacts.get("service_graph_markdown") or "",
        )

    def _store_task_stage_ledger(
        self,
        run_id: int,
        task_stages: _TaskStageLedger,
    ) -> None:
        database.add_artifact(
            run_id,
            "capability_orchestration_json",
            "任务能力阶段账本",
            json.dumps(
                {
                    "schema_version": "task-capability-orchestration.v1",
                    "events": list(task_stages.events),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    def _store_demand_progress_artifacts(
        self,
        run_id: int,
        *,
        phase: str,
        task_stages: _TaskStageLedger,
        run_status: str,
        evaluation_status: str,
        execution_mode: str = "unknown",
        requirement_calibration: Mapping[str, Any] | None = None,
        technical_decision: Mapping[str, Any] | None = None,
        change_ownership: Mapping[str, Any] | None = None,
        governance: Mapping[str, Any] | None = None,
        single_pass_contract: Mapping[str, Any] | None = None,
        scope_confirmation_status: str = "",
        scope_confirmation_reason: str = "",
        readonly_analysis_complete: bool = False,
    ) -> None:
        """Persist a sanitized business-facing progress/confirmation card.

        Existing technical artifacts remain the source of detail.  This card
        is deliberately additive so old consumers and mutation gates keep
        their current behavior while the user receives a clear checkpoint.
        """
        snapshot = build_demand_progress_snapshot(
            phase=phase,
            task_events=task_stages.events,
            requirement_calibration=requirement_calibration
            or self._load_json_artifact(run_id, "requirement_calibration_json"),
            technical_decision=technical_decision
            or self._load_json_artifact(run_id, "technical_decision_json"),
            change_ownership=change_ownership
            or self._load_json_artifact(run_id, "change_ownership_json"),
            governance=governance
            or self._load_json_artifact(run_id, "requirement_governance_json"),
            single_pass_contract=single_pass_contract
            or self._load_json_artifact(run_id, "single_pass_change_contract_json"),
            run_status=run_status,
            evaluation_status=evaluation_status,
            execution_mode=execution_mode,
            scope_confirmation_status=scope_confirmation_status,
            scope_confirmation_reason=scope_confirmation_reason,
            readonly_analysis_complete=readonly_analysis_complete,
        )
        suffix = "pre_change" if phase == "pre_change" else "post_change"
        database.add_artifact(
            run_id,
            f"demand_progress_{suffix}_json",
            f"需求进度与确认卡（{phase}）JSON",
            json.dumps(snapshot, ensure_ascii=False, indent=2),
        )
        database.add_artifact(
            run_id,
            f"demand_progress_{suffix}_markdown",
            f"需求进度与确认卡（{phase}）",
            demand_progress_to_markdown(snapshot),
        )

    @staticmethod
    def _load_json_artifact(run_id: int, kind: str) -> Mapping[str, Any] | None:
        for artifact in database.get_artifacts(run_id):
            if artifact.get("kind") != kind:
                continue
            try:
                payload = json.loads(artifact.get("content") or "")
            except (TypeError, json.JSONDecodeError):
                return None
            return payload if isinstance(payload, Mapping) else None
        return None

    def _create_task_knowledge_candidate(
        self,
        run_id: int,
        *,
        routing_result: TaskIntentRoutingResult,
        enabled: bool,
        candidate_payload: Mapping[str, Any] | None = None,
    ) -> tuple[tuple[str, str], tuple[str, ...]]:
        if (
            not enabled
            or self.capability_service is None
            or not isinstance(candidate_payload, Mapping)
        ):
            return ("skipped", "knowledge_write_skipped"), ()
        result = CapabilityWorkflowOrchestrator(
            self.capability_service
        ).run_task_capabilities(
            routing_result=routing_result,
            contract_ready=True,
            knowledge_candidate=dict(candidate_payload),
            knowledge_provenance={
                "producer": "his-harness",
                "run_id": run_id,
                "candidate_only": True,
            },
        )
        blockers = tuple(result.data.get("blockers") or ())
        if blockers:
            self._store_task_capability_blockers(run_id, blockers)
            return ("blocked", "knowledge_candidate_blocked"), blockers
        return ("completed", "knowledge_candidate_created"), ()

    @staticmethod
    def _store_task_capability_blockers(
        run_id: int,
        blockers: tuple[str, ...],
    ) -> None:
        stable_blockers = tuple(
            dict.fromkeys(
                blocker
                for blocker in blockers
                if isinstance(blocker, str) and blocker
            )
        )
        database.add_artifact(
            run_id,
            "task_capability_blockers_json",
            "任务能力阻断项 JSON",
            json.dumps(
                {
                    "schema_version": "task-capability-blockers.v1",
                    "blockers": list(stable_blockers),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        database.add_artifact(
            run_id,
            "task_capability_blockers_markdown",
            "任务能力阻断项",
            "\n".join(
                [
                    "## 任务能力阻断项",
                    "",
                    *[f"- `{blocker}`" for blocker in stable_blockers],
                ]
            ),
        )

    def _finalize_task_result(
        self,
        *,
        run_id: int,
        task_stages: _TaskStageLedger,
        status: str,
        evaluation_status: str,
        report_prefix: str = "",
        markdown_title: str = "HIS需求研发专家团运行报告",
        json_title: str = "HIS需求研发专家团运行数据",
        execution_mode: str = "unknown",
    ) -> WorkflowResult:
        self._store_task_stage_ledger(run_id, task_stages)
        self._store_demand_progress_artifacts(
            run_id,
            phase="post_change",
            task_stages=task_stages,
            run_status=status,
            evaluation_status=evaluation_status,
            execution_mode=execution_mode,
            readonly_analysis_complete=execution_mode == "readonly",
        )
        markdown_report = report_prefix + build_markdown_report(run_id)
        json_payload = build_json_payload(run_id)
        database.add_artifact(run_id, "markdown", markdown_title, markdown_report)
        database.add_artifact(run_id, "json", json_title, json_payload)
        return WorkflowResult(
            run_id,
            status,
            evaluation_status,
            markdown_report,
            json_payload,
            task_stages.events,
        )

    def _store_requirement_governance_artifacts(
        self,
        run_id: int,
        *,
        governance_result: object,
        single_pass_contract: object,
    ) -> str:
        """Store only deterministic serializations; never persist provider bodies here."""
        artifact_kinds = (
            "requirement_governance_json",
            "requirement_governance_markdown",
            "single_pass_change_contract_json",
            "single_pass_change_contract_markdown",
        )
        try:
            from app.requirement_governance import RequirementGovernanceResult
            from app.single_pass_change_contract import SinglePassChangeContract

            if type(governance_result) is not RequirementGovernanceResult or type(single_pass_contract) is not SinglePassChangeContract:
                raise ValueError("invalid_governance_shape")
            artifacts = (
                (artifact_kinds[0], "v1 需求治理 JSON", governance_result.to_json()),
                (artifact_kinds[1], "v1 需求治理报告", governance_result.to_markdown()),
                (artifact_kinds[2], "v1 一次改好变更契约 JSON", single_pass_contract.to_json()),
                (artifact_kinds[3], "v1 一次改好变更契约", single_pass_contract.to_markdown()),
            )
        except Exception:
            artifacts = ()

        missing: list[str] = []
        for kind, title, content in artifacts:
            try:
                database.add_artifact(run_id, kind, title, content)
            except Exception:
                missing.append(kind)
        if len(artifacts) != len(artifact_kinds):
            missing = list(artifact_kinds)
        if not missing:
            return ""
        diagnostic = json.dumps(
            {
                "schema_version": "requirement-governance-artifact-error.v1",
                "status": "artifact_write_incomplete",
                "missing_artifacts": missing,
                "message": "需求治理工件未完整写入；已保留稳定本地诊断。",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        try:
            database.add_artifact(
                run_id,
                "requirement_governance_error",
                "需求治理工件写入诊断",
                diagnostic,
            )
        except Exception:
            pass
        return "需求治理工件未完整写入；已保留稳定本地诊断。"

    def _store_scope_confirmation_artifacts(
        self,
        run_id: int,
        *,
        binding: Mapping[str, Any],
        status: str,
        reason: str,
        confirmed_by: str = "",
    ) -> None:
        payload = {
            **dict(binding),
            "status": status,
            "reason": reason,
            "confirmed_by": confirmed_by,
        }
        database.add_artifact(
            run_id,
            "pre_change_confirmation_json",
            "改动前范围确认 JSON",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        )
        database.add_artifact(
            run_id,
            "pre_change_confirmation_markdown",
            "改动前范围确认",
            scope_confirmation_to_markdown(
                binding,
                status=status,
                reason=reason,
                confirmed_by=confirmed_by,
            ),
        )

    def _store_core_closure_artifacts(self, run_id: int, *, contract, handoff) -> None:
        database.add_artifact(run_id, "core_requirement_contract_json", "v0.37 需求契约 JSON", contract.to_json())
        database.add_artifact(
            run_id,
            "core_requirement_contract_markdown",
            "v0.37 需求契约",
            "## v0.37 需求契约\n\n```json\n" + contract.to_json() + "\n```",
        )
        database.add_artifact(run_id, "core_engineering_handoff_json", "v0.37 工程交接 JSON", handoff.to_json())
        database.add_artifact(
            run_id,
            "core_engineering_handoff_markdown",
            "v0.37 工程交接",
            "## v0.37 工程交接\n\n```json\n" + handoff.to_json() + "\n```",
        )

    def _store_acceptance_contract_artifacts(self, run_id: int, *, result: AcceptanceContractResult) -> None:
        database.add_artifact(run_id, "acceptance_contract_result_json", "v0.47 可执行验收契约结果 JSON", result.to_json())
        database.add_artifact(
            run_id,
            "acceptance_contract_result_markdown",
            "v0.47 可执行验收契约结果",
            "## v0.47 可执行验收契约结果\n\n```json\n" + result.to_json() + "\n```",
        )

    def _store_core_closure_result(self, run_id: int, result: CoreClosureResult) -> None:
        database.add_artifact(run_id, "core_closure_json", "v0.37 Core Closure JSON", result.to_json())
        database.add_artifact(run_id, "core_closure_markdown", "v0.37 Core Closure", core_closure_to_markdown(result))
        if result.diff_review is not None:
            database.add_artifact(run_id, "core_diff_review_json", "v0.37 独立 Diff 审查 JSON", result.diff_review.to_json())
            database.add_artifact(
                run_id,
                "core_diff_review_markdown",
                "v0.37 独立 Diff 审查",
                "## v0.37 独立 Diff 审查\n\n```json\n" + result.diff_review.to_json() + "\n```",
            )

    def _store_worktree_artifacts(self, run_id: int, result: WorktreeExecutionResult) -> None:
        database.add_artifact(run_id, "worktree_manifest_json", "v0.7.4 Worktree manifest", result.to_json())
        database.add_artifact(run_id, "worktree_summary_markdown", "v0.7.4 Worktree 受控改码结果", result.to_markdown())
        if result.final_diff:
            database.add_artifact(run_id, "worktree_final_diff", "v0.7.4 最终 Diff", result.final_diff)
            database.add_artifact(run_id, "patch_review_markdown", "v0.7.4 Patch Review", build_patch_review_markdown(result))
        for attempt in result.attempts:
            attempt_no = int(attempt.get("attempt", 0))
            if attempt.get("patch"):
                database.add_artifact(
                    run_id,
                    f"worktree_patch_attempt_{attempt_no}",
                    f"v0.7.4 Patch Attempt {attempt_no}",
                    attempt["patch"],
                )
            for key, title_prefix in [
                ("apply_check", "git apply --check"),
                ("apply", "git apply"),
                ("diff_check", "git diff --check"),
            ]:
                if key in attempt:
                    database.add_artifact(
                        run_id,
                        f"worktree_{key}_{attempt_no}",
                        f"v0.7.4 {title_prefix} Attempt {attempt_no}",
                        json.dumps(attempt[key], ensure_ascii=False, indent=2),
                    )
            if "verify" in attempt:
                database.add_artifact(
                    run_id,
                    f"worktree_verify_{attempt_no}",
                    f"v0.7.4 Verify Attempt {attempt_no}",
                    json.dumps(attempt["verify"], ensure_ascii=False, indent=2),
                )

    def _store_fullstack_artifacts(self, run_id: int, result: FullstackExecutionResult) -> None:
        database.add_artifact(run_id, "fullstack_manifest_json", "v0.8.9 Fullstack manifest", result.to_json())
        database.add_artifact(run_id, "fullstack_summary_markdown", "v0.8.9 多项目 Fullstack Worktree 结果", result.to_markdown())
        database.add_artifact(run_id, "fullstack_patch_plan_json", "v0.8.9 Fullstack Patch Plan JSON", json.dumps(result.targets, ensure_ascii=False, indent=2))
        database.add_artifact(run_id, "fullstack_patch_plan_markdown", "v0.8.9 Fullstack Patch Plan", result.plan_to_markdown())
        for key, diff in result.final_diffs.items():
            if diff:
                database.add_artifact(run_id, f"fullstack_final_diff_{key}", f"v0.8.9 {key} final.diff", diff)

    def _store_precommit_verification_artifacts(self, run_id: int, result: PrecommitVerificationResult) -> None:
        database.add_artifact(run_id, "precommit_manifest_json", "v0.9.1 提交前验证 Manifest", result.to_json())
        database.add_artifact(run_id, "verification_matrix_json", "v0.9.1 提交前验证矩阵 JSON", result.matrix_json())
        database.add_artifact(run_id, "verification_matrix_markdown", "v0.9.1 提交前验证矩阵", result.matrix_markdown())
        database.add_artifact(run_id, "behavior_acceptance_json", "v0.10 行为验收矩阵 JSON", result.behavior_json())
        database.add_artifact(run_id, "behavior_acceptance_markdown", "v0.10 行为验收矩阵", result.behavior_markdown())
        database.add_artifact(run_id, "method_test_runner_json", "v0.10.3A 方法级测试执行器 JSON", result.method_test_runner_json())
        database.add_artifact(run_id, "method_test_runner_markdown", "v0.10.3A 方法级测试执行器", result.method_test_runner_markdown())
        database.add_artifact(run_id, "ui_evidence_runner_json", "v0.10.3B UI 证据采集执行器 JSON", result.ui_evidence_runner_json())
        database.add_artifact(run_id, "ui_evidence_runner_markdown", "v0.10.3B UI 证据采集执行器", result.ui_evidence_runner_markdown())
        database.add_artifact(run_id, "interaction_evidence_json", "v0.10.2 交互证据包 JSON", result.interaction_json())
        database.add_artifact(run_id, "interaction_evidence_markdown", "v0.10.2 方法级交互测试与 UI 证据", result.interaction_markdown())
        database.add_artifact(run_id, "behavior_test_plan_json", "v0.10.2 方法级测试计划 JSON", result.behavior_test_plan_json())
        database.add_artifact(run_id, "behavior_test_plan_markdown", "v0.10.2 方法级测试计划", result.behavior_test_plan_markdown())
        database.add_artifact(run_id, "method_regression_result_json", "v0.10.2 方法级执行结果 JSON", result.method_regression_json())
        database.add_artifact(run_id, "method_regression_result_markdown", "v0.10.2 方法级执行结果", result.method_regression_markdown())
        database.add_artifact(run_id, "ui_evidence_manifest_json", "v0.10.2 UI 证据 Manifest JSON", result.ui_evidence_json())
        database.add_artifact(run_id, "ui_evidence_manifest_markdown", "v0.10.2 UI 证据 Manifest", result.ui_evidence_markdown())
        database.add_artifact(run_id, "playwright_screenshot_index_markdown", "v0.10.2 Playwright 截图索引", result.playwright_screenshot_index_markdown())
        database.add_artifact(run_id, "code_review_markdown", "v0.9.1 代码审查包", result.code_review_markdown())
        database.add_artifact(run_id, "commit_ready_summary_markdown", "v0.9.1 Commit Ready Summary", result.commit_ready_markdown())

    def _store_single_demand_trial_artifacts(self, run_id: int, package: SingleDemandTrialPackage) -> None:
        database.add_artifact(run_id, "single_demand_trial_json", "v0.9.5 单需求试跑 JSON", package.to_json())
        database.add_artifact(run_id, "single_demand_trial_markdown", "v0.9.5 单需求试跑结果", package.trial_markdown())
        database.add_artifact(run_id, "verification_matrix_json", "v0.9.5 验证矩阵 JSON", json.dumps(package.verification_matrix, ensure_ascii=False, indent=2))
        database.add_artifact(run_id, "verification_matrix_markdown", "v0.9.5 验证矩阵", package.verification_matrix_markdown())
        database.add_artifact(run_id, "code_review_markdown", "v0.9.5 代码审查包", package.code_review_markdown())
        database.add_artifact(run_id, "commit_ready_summary_markdown", "v0.9.5 Commit Ready Summary", package.commit_ready_markdown())

    def _run_review_execution(
        self,
        *,
        run_id: int,
        project_path: str | Path | None,
        review_commit: str,
        review_base: str,
        review_context: dict | None,
        allowed_paths: list[str],
        verify_commands: list[str],
        worktree_dir: str | Path,
    ) -> ReviewExecutionResult:
        if not project_path:
            return ReviewExecutionResult(
                status="failed",
                summary="review-worktree 模式必须提供 --project-path",
                worktree_path=str(Path(worktree_dir) / f"run_{run_id}"),
                allowed_paths=allowed_paths,
            )
        executor = ReviewWorktreeExecutor()
        return executor.execute(
            ReviewExecutionOptions(
                project_path=str(project_path),
                run_id=run_id,
                review_commit=review_commit,
                review_base=review_base,
                review_context=review_context,
                worktree_root=str(worktree_dir),
                allowed_paths=allowed_paths,
                verify_commands=verify_commands,
            )
        )

    def _store_review_artifacts(self, run_id: int, result: ReviewExecutionResult) -> None:
        database.add_artifact(run_id, "review_manifest_json", "v0.7.3 Review manifest", result.to_json())
        database.add_artifact(run_id, "review_summary_markdown", "v0.7.3 已提交 Diff 审查结果", result.to_markdown())
        if result.review_diff:
            database.add_artifact(run_id, "review_diff", "v0.7.3 Review Diff", result.review_diff)
        if result.diff_check:
            database.add_artifact(
                run_id,
                "review_diff_check",
                "v0.7.3 git diff --check",
                json.dumps(result.diff_check, ensure_ascii=False, indent=2),
            )
        for index, verify_result in enumerate(result.verify_results, start=1):
            database.add_artifact(
                run_id,
                f"review_verify_{index}",
                f"v0.7.3 Verify {index}",
                json.dumps(verify_result, ensure_ascii=False, indent=2),
            )

    def _build_yunxiao_transaction_plan(
        self,
        *,
        run_id: int,
        manager: YunxiaoTransactionManager,
        demand_text: str,
        title: str,
        project_key: str | None,
        yunxiao_url: str,
        entity_kind: str,
        entity_id: str,
        current_status: str,
        target_assignee: str,
        target_status: str,
        target_iteration: str,
        screenshots: list[str],
        service_change_file: str,
        artifacts: list[str],
        project_paths: list[str],
        human_confirmed: bool,
        execution_mode: str,
        verify_commands: list[str],
        evidence_bundle: EvidenceBundle | None,
    ) -> dict:
        inferred_entity_kind = entity_kind or infer_yunxiao_entity_kind(yunxiao_url)
        inferred_entity_id = entity_id or parse_work_item_id(yunxiao_url) or parse_work_item_id(demand_text)
        if not inferred_entity_kind:
            return {
                "status": "failed",
                "mode": "dry_run",
                "summary": "缺少云效 entity kind，无法生成事务计划。",
                "real_write_status": "not_executed",
                "errors": ["无法从 --yunxiao-url 推断实体类型，请传入 --yunxiao-entity-kind。"],
                "entity": {"kind": "", "entity_id": inferred_entity_id or "", "title": title, "url": yunxiao_url},
                "outcome": "",
                "actions": [],
            }
        entity = YunxiaoEntityRef(
            kind=inferred_entity_kind,
            entity_id=inferred_entity_id,
            title=title,
            url=yunxiao_url,
        )
        outcome = infer_yunxiao_outcome(
            run=database.get_run(run_id) or {},
            demand_text=demand_text,
            title=title,
            execution_mode=execution_mode,
            verify_commands=verify_commands,
            evidence_bundle=evidence_bundle,
        )
        risk_level = infer_yunxiao_risk_level(demand_text=demand_text, title=title, evidence_bundle=evidence_bundle)
        evidence_ids = [f"run:{run_id}"]
        if evidence_bundle is not None:
            evidence_ids.insert(0, evidence_bundle.evidence_id)
        enriched_artifacts = enrich_yunxiao_comment_artifacts(
            run_id=run_id,
            artifacts=artifacts,
            project_paths=project_paths,
        )
        return build_yunxiao_transaction_plan(
            manager=manager,
            project_key=project_key or "default",
            entity=entity,
            run_id=run_id,
            outcome=outcome,
            evidence_ids=evidence_ids,
            risk_level=risk_level,
            model_mode=self.llm_client.mode,
            model_name=self.llm_client.model_name,
            current_status=current_status,
            target_assignee=target_assignee,
            target_status=target_status,
            target_iteration=target_iteration,
            screenshot_paths=screenshots,
            service_change_file=service_change_file,
            artifacts=enriched_artifacts,
            human_confirmed=human_confirmed,
            persist_audit=True,
        )

    def _store_yunxiao_transaction_plan_artifacts(self, run_id: int, plan: dict) -> None:
        database.add_artifact(run_id, "yunxiao_transaction_plan_json", "v0.8.6 云效事务计划/写入 JSON", json.dumps(plan, ensure_ascii=False, indent=2))
        database.add_artifact(run_id, "yunxiao_transaction_plan_markdown", "v0.8.6 云效事务计划/写入结果", transaction_plan_to_markdown(plan))


def build_markdown_report(run_id: int) -> str:
    run = database.get_run(run_id)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    all_steps = database.get_step_runs(run_id)
    latest_steps = database.get_latest_step_runs(run_id)
    latest_by_order = {step["step_order"]: step for step in latest_steps}
    evidence_markdown = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "evidence_markdown"),
        "",
    )
    acceptance_matrix_markdown = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "acceptance_matrix_markdown"),
        "",
    )
    requirement_calibration_markdown = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "requirement_calibration_markdown"),
        "",
    )
    requirement_understanding_markdown = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "requirement_understanding_markdown"),
        "",
    )
    requirement_evidence_markdown = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "requirement_evidence_markdown"),
        "",
    )
    conversation_evidence_markdown = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "conversation_evidence_markdown"),
        "",
    )
    error_chain_closure_markdown = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "error_chain_closure_markdown"),
        "",
    )
    technical_decision_markdown = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "technical_decision_markdown"),
        "",
    )
    yunxiao_evidence = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "yunxiao_evidence_json"),
        "",
    )
    patch_readiness = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "patch_readiness_markdown"),
        "",
    )
    yunxiao_policy = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "yunxiao_transaction_policy"),
        "",
    )
    yunxiao_transaction_plan = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "yunxiao_transaction_plan_markdown"),
        "",
    )
    worktree_summary = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "worktree_summary_markdown"),
        "",
    )
    single_demand_trial = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "single_demand_trial_markdown"),
        "",
    )
    fullstack_summary = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "fullstack_summary_markdown"),
        "",
    )
    verification_matrix = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "verification_matrix_markdown"),
        "",
    )
    code_review = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "code_review_markdown"),
        "",
    )
    commit_ready_summary = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "commit_ready_summary_markdown"),
        "",
    )
    review_summary = next(
        (artifact["content"] for artifact in database.get_artifacts(run_id) if artifact["kind"] == "review_summary_markdown"),
        "",
    )
    task_capability_blockers = next(
        (
            artifact["content"]
            for artifact in database.get_artifacts(run_id)
            if artifact["kind"] == "task_capability_blockers_markdown"
        ),
        "",
    )
    demand_progress_pre_change = next(
        (
            artifact["content"]
            for artifact in database.get_artifacts(run_id)
            if artifact["kind"] == "demand_progress_pre_change_markdown"
        ),
        "",
    )
    demand_progress_post_change = next(
        (
            artifact["content"]
            for artifact in reversed(database.get_artifacts(run_id))
            if artifact["kind"] == "demand_progress_post_change_markdown"
        ),
        "",
    )
    pre_change_confirmation_markdown = next(
        (
            artifact["content"]
            for artifact in reversed(database.get_artifacts(run_id))
            if artifact["kind"] == "pre_change_confirmation_markdown"
        ),
        "",
    )
    mock_warning = (
        "\n> 当前报告由 MOCK 模式生成，仅用于流程演示，不可用于真实业务判断。\n"
        if run.get("llm_mode") == "mock"
        else ""
    )
    lines = [
        f"# {run['title']} - HIS需求研发专家团运行报告",
        mock_warning,
        "## 执行摘要",
        "",
        f"- Run ID：{run['id']}",
        f"- Harness：{HARNESS_VERSION}",
        f"- 状态：{run['status']}",
        f"- 自动审核：{run.get('evaluation_status') or '-'}",
        f"- 审核结论：{run.get('evaluation_summary') or '-'}",
        f"- 模型：{run.get('llm_mode') or '-'} / {run.get('llm_model') or '-'}",
        f"- 返工轮次：{run.get('retry_rounds', 0)}",
        f"- 来源：{run['source_type']}",
        f"- 开始：{run['started_at']}",
        f"- 结束：{run['finished_at'] or '-'}",
        f"- 步骤：{run['current_step']}/{run['total_steps']}",
        "",
        "## 下一步与人工审查",
        "",
        f"- 当前下一步：{build_development_entry_status(run)}",
        "- 必须人工确认项：见各专家报告的“待确认”和“风险与边界”。",
        "- 测试验收清单：见测试验证方案和最终评审。",
        "- v0.15 需求理解确认卡：先确认来源优先级、用户补充规则、参数/字段和值域；低置信度不得自动进入改码。",
        "- v1 改码前理解证据包：必须先证明业务背景、场景、目标边界、项目入口/调用链、影响范围和验证基线；缺口只能继续调查。",
        "- v1 对话证据：用户纠正和已确认链路会作为不可由后续猜测推翻的约束；仍需用源码和运行时证据分别核验。",
        "- v0.8.7 验收矩阵：见下方“需求验收矩阵”，后续自动改码、自动提交和云效流转必须以此为前置闸口。",
        "- 残余风险：需求描述不完整、政策口径不明确或真实系统上下文不足时，需人工补充证据。",
        "",
    ]
    if demand_progress_pre_change:
        lines.extend([demand_progress_pre_change, ""])
    if pre_change_confirmation_markdown:
        lines.extend([pre_change_confirmation_markdown, ""])
    if demand_progress_post_change:
        lines.extend([demand_progress_post_change, ""])
    if requirement_calibration_markdown:
        lines.extend([requirement_calibration_markdown, ""])
    if requirement_understanding_markdown:
        lines.extend([requirement_understanding_markdown, ""])
    if requirement_evidence_markdown:
        lines.extend(["## 需求来源归一化证据", "", requirement_evidence_markdown, ""])
    if conversation_evidence_markdown:
        lines.extend([conversation_evidence_markdown, ""])
    if error_chain_closure_markdown:
        lines.extend([error_chain_closure_markdown, ""])
    if acceptance_matrix_markdown:
        lines.extend([acceptance_matrix_markdown, ""])
    if technical_decision_markdown:
        lines.extend(["## Harness 技术自治", "", technical_decision_markdown, ""])
    lines.extend(
        build_engineering_evidence_section(
            evidence_markdown=evidence_markdown,
            technical_decision_markdown=technical_decision_markdown,
        )
    )
    if yunxiao_evidence:
        yunxiao_evidence_markdown = build_yunxiao_evidence_report(yunxiao_evidence)
        lines.extend(
            [
                "## 云效只读证据",
                "",
                "- v0.8.4 只读云效，不写评论、不流转状态、不改负责人、不上传附件。",
                "",
                yunxiao_evidence_markdown,
                "",
                "```json",
                yunxiao_evidence,
                "```",
                "",
            ]
        )
    if patch_readiness:
        lines.extend([patch_readiness, ""])
    if yunxiao_policy:
        yunxiao_boundary_lines = build_yunxiao_boundary_lines(yunxiao_policy)
        lines.extend(
            [
                "## 云效事务边界",
                "",
                *yunxiao_boundary_lines,
                "",
                "```json",
                yunxiao_policy,
                "```",
                "",
            ]
        )
    if yunxiao_transaction_plan:
        lines.extend([yunxiao_transaction_plan, ""])
    if single_demand_trial:
        lines.extend([single_demand_trial, ""])
    if worktree_summary:
        lines.extend([worktree_summary, ""])
    if fullstack_summary:
        lines.extend([fullstack_summary, ""])
    if verification_matrix:
        lines.extend([verification_matrix, ""])
    if code_review:
        lines.extend([code_review, ""])
    if commit_ready_summary:
        lines.extend([commit_ready_summary, ""])
    if review_summary:
        lines.extend([review_summary, ""])
    if task_capability_blockers:
        lines.extend([task_capability_blockers, ""])
    lines.extend(
        [
            "## 原始需求",
            "",
            remove_generated_analysis_appendices(run["demand_text"]),
            "",
            "## 最新有效步骤报告",
            "",
        ]
    )
    for order in sorted(latest_by_order):
        step = latest_by_order[order]
        if technical_decision_markdown:
            lines.extend(render_step_summary(step))
        else:
            lines.extend(render_step_report(step))

    if any(step.get("attempt_round", 0) > 0 for step in all_steps):
        lines.extend(["## 返工历史", ""])
        for step in all_steps:
            lines.extend(
                [
                    f"### Attempt {step['attempt_round']} / 第 {step['step_order']} 步 {step['step_name']}",
                    "",
                    f"- 状态：{step['status']}",
                    f"- 审核意见：{step['review_feedback'] or '-'}",
                    "",
                ]
            )

    lines.extend(
        [
            "## 第一版边界",
            "",
            "- 本报告由 Harness 自动编排专家生成，并经过独立 Evaluator 审核。",
            "- readonly 模式只做需求分析、方案和验证建议，不修改业务代码。",
            "- worktree 模式先修改 Harness 创建的独立 Git worktree；成功后会在原业务目录仍干净且 `git apply --check` 通过时合入 final.diff。",
            "- fullstack-worktree 模式会为多个业务仓库分别创建临时 worktree；全部验证和 apply-check 通过后才统一合入原业务目录。",
            "- review-worktree 模式只审查已有提交并在独立 Git worktree 中验证，不生成 patch、不修改原业务目录。",
            "- v0.7.4 不提交、不推送、不发布、不写云效事务；证据不足时阻断 patch。",
            "- v0.8.4 只增强云效只读证据，不开启真实写云效；内联文件仅作为证据读取。",
            "- v0.8.6 云效事务默认不写；dry-run 不读取写 token，write 模式必须双开关确认，real write 只允许 comment-only，transition-fake 仅用于 fake 验证。",
            "- v0.8.7 新增需求验收矩阵和项目验证基座；不自动改码、不自动提交、不真实流转云效状态。",
            "- v0.8.9 新增多项目全栈 worktree 合入；仍不提交、不推送、不发布。",
            "- v0.9.1 提交前验证矩阵和代码审查包只验证 BFF/前端当前本地 diff；后端字段来源由 df-his-api 只读证据证明，不自动提交、不推送、不真实流转云效。",
            "- v0.9.5 单需求试跑只处理一个真实云效需求；允许受控 worktree 改码和 comment-only 评论，真实状态、负责人、迭代和关闭仍冻结。",
            "- 进入真实开发前，需要人工确认需求边界、系统上下文和验收样例。",
        ]
    )
    return "\n".join(line for line in lines if line is not None)


def render_step_report(step: dict) -> list[str]:
    lines = [
        f"### {step['step_order']}. {step['step_name']} - {step['expert_name']}",
        "",
        f"- 状态：{step['status']}",
        f"- Attempt：{step.get('attempt_round', 0)}",
        f"- 耗时：{step['duration_ms']} ms",
        f"- Tokens：{step['prompt_tokens']}/{step['completion_tokens']}",
        "",
    ]
    if step["error"]:
        lines.extend(["#### 错误", "", step["error"], ""])
    if step["output_text"]:
        lines.extend(["#### 模型生成内容", "", step["output_text"], ""])
    return lines


def render_step_summary(step: dict) -> list[str]:
    """Keep formal reports proportional once an authoritative decision exists."""
    return [
        f"### {step['step_order']}. {step['step_name']} - {step['expert_name']}",
        "",
        f"- 状态：{step['status']}",
        f"- Attempt：{step.get('attempt_round', 0)}",
        f"- 耗时：{step['duration_ms']} ms",
        "- 完整模型输出保留在步骤审计记录中；不重复嵌入正式报告。",
        "",
    ]


def build_engineering_evidence_section(
    *,
    evidence_markdown: str,
    technical_decision_markdown: str,
) -> list[str]:
    lines = ["## Harness 工程证据链", ""]
    if technical_decision_markdown:
        lines.extend(
            [
                "- 正式改动范围以本报告的技术自治决策、服务图和接口/字段契约为准。",
                "- 通用扫描明细保留在独立 evidence.md，仅作发现线索，不作为最终改动范围。",
                "",
            ]
        )
    elif evidence_markdown:
        lines.extend([evidence_markdown, ""])
    else:
        lines.extend(
            [
                "- 本轮未提供项目路径或项目画像，未执行只读工程扫描。",
                "- 专家报告不得给出确定代码文件结论，进入开发前需要补充真实项目上下文。",
                "",
            ]
        )
    return lines


def build_development_entry_status(run: dict) -> str:
    if run.get("status") == "success" and run.get("evaluation_status") == "analysis_complete_readonly":
        return "只读分析已完成；自动改码门禁仍关闭，需补齐契约/业务证据后才能进入开发。"
    if run.get("status") == "success" and run.get("evaluation_status") == "ready_for_manual_review":
        return "本地验证已通过，可进入人工代码审查与业务验收；未自动提交或发布。"
    if run.get("status") == "success" and run.get("evaluation_status") == "pass":
        return "可以进入人工审查后开发"
    if run.get("evaluation_status") != "pass":
        return "不建议进入开发，需先处理专家报告审核问题"
    return "不建议进入开发，需先处理 Harness 执行阻断或验证失败"


def gate_readonly_evaluation(
    evaluation: EvaluationResult,
    *,
    gate_blocked: bool,
    reason: str = "",
) -> EvaluationResult:
    """Keep readonly analysis useful without turning an unresolved gate into pass."""
    if not gate_blocked or evaluation.status != "pass":
        return evaluation
    gate_reason = reason or "需求治理或多项目改动合同未闭合。"
    return replace(
        evaluation,
        status="analysis_complete_readonly",
        summary=(
            "只读分析已完成，但治理/改动合同未闭合；"
            "本次结果不能作为改码就绪或开发通过结论。\n"
            f"阻断原因：{gate_reason}"
        ),
    )


def build_auto_local_performance_profile(*, requested_execution_mode: str, resolved_execution_mode: str) -> dict | None:
    if requested_execution_mode != "auto-local":
        return None
    return {
        "version": "0.44-auto-local-performance",
        "requested_execution_mode": requested_execution_mode,
        "resolved_execution_mode": resolved_execution_mode,
        "started_at": database.now_iso(),
        "_started_perf_counter": time.perf_counter(),
        "fast_local": None,
        "stages": {},
        "total_duration_ms": 0,
    }


def record_auto_local_stage(
    profile: dict | None,
    *,
    key: str,
    started_at: float,
    status: str,
    **details,
) -> None:
    if profile is None:
        return
    profile["stages"][key] = {
        "status": status,
        "duration_ms": max(0, int((time.perf_counter() - started_at) * 1000)),
        **details,
    }


def finish_auto_local_performance(profile: dict) -> None:
    profile["finished_at"] = database.now_iso()
    started_at = float(profile.pop("_started_perf_counter", time.perf_counter()))
    profile["total_duration_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))


def build_json_payload(run_id: int) -> str:
    artifact_manifest = [
        build_artifact_manifest_entry(artifact)
        for artifact in database.get_artifacts(run_id)
    ]
    payload = {
        "run": database.get_run(run_id),
        "latest_steps": [
            build_step_manifest_entry(step)
            for step in database.get_latest_step_runs(run_id)
        ],
        "all_steps": [
            build_step_manifest_entry(step)
            for step in database.get_step_runs(run_id)
        ],
        "artifacts": artifact_manifest,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_step_manifest_entry(step: Mapping[str, Any]) -> dict[str, Any]:
    output = str(step.get("output_text") or "")
    error = str(step.get("error") or "")
    output_bytes = output.encode("utf-8")
    error_bytes = error.encode("utf-8")
    return {
        key: value
        for key, value in step.items()
        if key not in {"output_text", "error"}
    } | {
        "output_size_bytes": len(output_bytes),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "output_name": step_output_name(step) if output else "",
        "error_size_bytes": len(error_bytes),
        "error_sha256": hashlib.sha256(error_bytes).hexdigest(),
        "error_name": step_error_name(step) if error else "",
        "detail_storage": "separate_step_files_and_database",
    }


def step_output_name(step: Mapping[str, Any]) -> str:
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(step.get("step_key") or "step"))
    return (
        f"steps/step_{int(step.get('step_order') or 0):02d}_"
        f"attempt_{int(step.get('attempt_round') or 0)}_"
        f"{int(step.get('id') or 0)}_{safe_key}.md"
    )


def step_error_name(step: Mapping[str, Any]) -> str:
    return step_output_name(step).removesuffix(".md") + ".error.txt"


def build_artifact_manifest_entry(artifact: Mapping[str, Any]) -> dict[str, Any]:
    content = str(artifact.get("content") or "")
    encoded = content.encode("utf-8")
    kind = str(artifact.get("kind") or "")
    artifact_id = int(artifact.get("id") or 0)
    return {
        key: value
        for key, value in artifact.items()
        if key != "content"
    } | {
        "content_size_bytes": len(encoded),
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "output_name": artifact_output_name(kind=kind, artifact_id=artifact_id),
        "content_storage": "separate_artifact_file_and_database",
    }


def build_yunxiao_evidence_report(evidence_json: str) -> str:
    try:
        evidence = json.loads(evidence_json)
    except json.JSONDecodeError:
        return "- 云效证据 JSON 无法解析，见原始 artifact。"
    lines = [
        f"- 读取状态：{evidence.get('status') or '-'}",
        f"- 工作项：{evidence.get('work_item_id') or '-'}",
        f"- 附件数：{len(evidence.get('attachments') or [])}",
        f"- 内联图片/文件数：{len(evidence.get('inline_files') or [])}",
        f"- 内联下载数：{len(evidence.get('inline_file_downloads') or [])}",
    ]
    clean_text = str(evidence.get("clean_text") or "").strip()
    if clean_text:
        lines.extend(["", "### 云效清洗正文", "", compress_text(clean_text, 3000)])
    inline_files = evidence.get("inline_files") or []
    if inline_files:
        lines.extend(["", "### 云效内联图片/文件", ""])
        for item in inline_files[:20]:
            lines.append(
                f"- {item.get('kind') or 'file'}：fileIdentifier={item.get('identifier') or '-'}，"
                f"name={item.get('name') or '-'}"
            )
    downloads = evidence.get("inline_file_downloads") or []
    if downloads:
        lines.extend(["", "### 云效内联下载摘要", ""])
        for item in downloads[:20]:
            lines.append(
                f"- {item.get('status')}：fileIdentifier={item.get('identifier') or '-'}，"
                f"size={item.get('size') or '-'}，content_type={item.get('content_type') or '-'}，"
                f"path={item.get('path') or '-'}，error={item.get('error') or '-'}"
            )
    return "\n".join(lines)


def write_run_outputs(run_id: int, output_dir: str | Path) -> Path:
    target = Path(output_dir) / f"run_{run_id}"
    target.mkdir(parents=True, exist_ok=True)
    report = build_markdown_report(run_id)
    payload = build_json_payload(run_id)
    (target / "report.md").write_text(report, encoding="utf-8")
    (target / "run.json").write_text(payload, encoding="utf-8")
    for artifact in database.get_artifacts(run_id):
        kind = str(artifact["kind"])
        if (
            kind not in {
                "acceptance_matrix_json",
                "acceptance_matrix_markdown",
                "requirement_calibration_json",
                "requirement_calibration_markdown",
                "requirement_understanding_json",
                "requirement_understanding_markdown",
                "requirement_evidence_json",
                "requirement_evidence_markdown",
                "conversation_evidence_json",
                "conversation_evidence_markdown",
                "error_chain_closure_json",
                "error_chain_closure_markdown",
                "evidence_json",
                "evidence_markdown",
                "technical_decision_json",
                "technical_decision_markdown",
                "multi_service_change_contract_json",
                "multi_service_change_contract_markdown",
                "multi_service_evidence_selection_json",
                "project_selection_markdown",
                "field_provenance_markdown",
                "implementation_decision_markdown",
                "service_graph_markdown",
                "evaluation",
                "yunxiao_transaction_policy",
                "yunxiao_transaction_plan_json",
                "yunxiao_transaction_plan_markdown",
                "yunxiao_evidence_json",
                "clarification_gate_json",
                "patch_readiness_markdown",
                "patch_review_markdown",
                "review_diff",
                "review_diff_check",
                "fullstack_manifest_json",
                "fullstack_summary_markdown",
                "fullstack_patch_plan_json",
                "fullstack_patch_plan_markdown",
                "precommit_manifest_json",
                "verification_matrix_json",
                "verification_matrix_markdown",
                "behavior_acceptance_json",
                "behavior_acceptance_markdown",
                "method_test_runner_json",
                "method_test_runner_markdown",
                "ui_evidence_runner_json",
                "ui_evidence_runner_markdown",
                "interaction_evidence_json",
                "interaction_evidence_markdown",
                "behavior_test_plan_json",
                "behavior_test_plan_markdown",
                "method_regression_result_json",
                "method_regression_result_markdown",
                "ui_evidence_manifest_json",
                "ui_evidence_manifest_markdown",
                "playwright_screenshot_index_markdown",
                "code_review_markdown",
                "commit_ready_summary_markdown",
                "single_demand_trial_json",
                "single_demand_trial_markdown",
                "change_ownership_json",
                "change_ownership_markdown",
                "requirement_governance_json",
                "requirement_governance_markdown",
                "requirement_governance_error",
                "single_pass_change_contract_json",
                "single_pass_change_contract_markdown",
                "demand_progress_pre_change_json",
                "demand_progress_pre_change_markdown",
                "demand_progress_post_change_json",
                "demand_progress_post_change_markdown",
                "pre_change_confirmation_json",
                "pre_change_confirmation_markdown",
                "capability_orchestration_json",
            }
            and not kind.startswith("worktree_")
            and not kind.startswith("review_")
            and not kind.startswith("fullstack_")
        ):
            continue
        output_name = artifact_output_name(kind=kind, artifact_id=artifact["id"])
        (target / output_name).write_text(artifact["content"], encoding="utf-8")
    for step in database.get_step_runs(run_id):
        output = str(step.get("output_text") or "")
        error = str(step.get("error") or "")
        if output:
            path = target / step_output_name(step)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8")
        if error:
            path = target / step_error_name(step)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(error, encoding="utf-8")
    return target


def compress_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n...（中间内容已压缩）...\n\n{tail}"


def load_method_evidence_file(path: str | Path | None) -> dict:
    if not path:
        return {}
    evidence_path = Path(path).expanduser().resolve()
    return json.loads(evidence_path.read_text(encoding="utf-8"))


def load_requirement_evidence_file(path: str | Path | None) -> dict | None:
    if not path:
        return None
    evidence_path = Path(path).expanduser().resolve()
    return normalize_requirement_evidence_file(evidence_path)


def artifact_output_name(*, kind: str, artifact_id: int) -> str:
    if kind == "acceptance_matrix_json":
        return "acceptance_matrix.json"
    if kind == "acceptance_matrix_markdown":
        return "acceptance_matrix.md"
    if kind == "requirement_calibration_json":
        return "requirement_calibration.json"
    if kind == "requirement_calibration_markdown":
        return "requirement_calibration.md"
    if kind == "requirement_understanding_json":
        return "requirement_understanding.json"
    if kind == "requirement_understanding_markdown":
        return "requirement_understanding.md"
    if kind == "requirement_evidence_json":
        return "requirement_evidence.json"
    if kind == "requirement_evidence_markdown":
        return "requirement_evidence.md"
    if kind == "conversation_evidence_json":
        return "conversation_evidence.json"
    if kind == "conversation_evidence_markdown":
        return "conversation_evidence.md"
    if kind == "error_chain_closure_json":
        return "error_chain_closure.json"
    if kind == "error_chain_closure_markdown":
        return "error_chain_closure.md"
    if kind == "technical_decision_json":
        return "technical_decision.json"
    if kind == "technical_decision_markdown":
        return "technical_decision.md"
    if kind == "multi_service_change_contract_json":
        return "multi_service_change_contract.json"
    if kind == "multi_service_change_contract_markdown":
        return "multi_service_change_contract.md"
    if kind == "multi_service_evidence_selection_json":
        return "multi_service_evidence_selection.json"
    if kind == "change_ownership_json":
        return "change_ownership_matrix.json"
    if kind == "change_ownership_markdown":
        return "change_ownership_matrix.md"
    if kind == "requirement_governance_json":
        return "requirement_governance.json"
    if kind == "requirement_governance_markdown":
        return "requirement_governance.md"
    if kind == "requirement_governance_error":
        return "requirement_governance_error.json"
    if kind == "single_pass_change_contract_json":
        return "single_pass_change_contract.json"
    if kind == "single_pass_change_contract_markdown":
        return "single_pass_change_contract.md"
    if kind == "demand_progress_pre_change_json":
        return "demand_progress_pre_change.json"
    if kind == "demand_progress_pre_change_markdown":
        return "demand_progress_pre_change.md"
    if kind == "demand_progress_post_change_json":
        return "demand_progress_post_change.json"
    if kind == "demand_progress_post_change_markdown":
        return "demand_progress_post_change.md"
    if kind == "pre_change_confirmation_json":
        return "pre_change_confirmation.json"
    if kind == "pre_change_confirmation_markdown":
        return "pre_change_confirmation.md"
    if kind == "project_selection_markdown":
        return "project_selection.md"
    if kind == "field_provenance_markdown":
        return "field_provenance.md"
    if kind == "service_graph_markdown":
        return "service_graph.md"
    if kind == "implementation_decision_markdown":
        return "implementation_decision.md"
    if kind == "yunxiao_evidence_json":
        return "yunxiao_evidence.json"
    if kind == "clarification_gate_json":
        return "clarification_gate.json"
    if kind == "patch_readiness_markdown":
        return "patch_readiness.md"
    if kind == "patch_review_markdown":
        return "patch_review.md"
    if kind == "yunxiao_transaction_plan_json":
        return "yunxiao_transaction_plan.json"
    if kind == "yunxiao_transaction_plan_markdown":
        return "yunxiao_transaction_plan.md"
    if kind == "worktree_manifest_json":
        return "worktree_manifest.json"
    if kind == "worktree_summary_markdown":
        return "worktree_summary.md"
    if kind == "worktree_final_diff":
        return "final.diff"
    if kind.startswith("worktree_patch_attempt_"):
        attempt = kind.rsplit("_", 1)[-1]
        return f"patch_attempt_{attempt}.diff"
    if kind.startswith("worktree_apply_check_"):
        attempt = kind.rsplit("_", 1)[-1]
        return f"apply_check_{attempt}.log"
    if kind.startswith("worktree_apply_"):
        attempt = kind.rsplit("_", 1)[-1]
        return f"apply_{attempt}.log"
    if kind.startswith("worktree_diff_check_"):
        attempt = kind.rsplit("_", 1)[-1]
        return f"diff_check_{attempt}.log"
    if kind.startswith("worktree_verify_"):
        attempt = kind.rsplit("_", 1)[-1]
        return f"verify_{attempt}.log"
    if kind == "fullstack_manifest_json":
        return "fullstack_manifest.json"
    if kind == "fullstack_summary_markdown":
        return "fullstack_summary.md"
    if kind == "fullstack_patch_plan_json":
        return "fullstack_patch_plan.json"
    if kind == "fullstack_patch_plan_markdown":
        return "fullstack_patch_plan.md"
    if kind.startswith("fullstack_final_diff_"):
        key = kind.replace("fullstack_final_diff_", "", 1)
        return f"final_{key}.diff"
    if kind == "precommit_manifest_json":
        return "precommit_manifest.json"
    if kind == "verification_matrix_json":
        return "verification_matrix.json"
    if kind == "verification_matrix_markdown":
        return "verification_matrix.md"
    if kind == "behavior_acceptance_json":
        return "behavior_acceptance.json"
    if kind == "behavior_acceptance_markdown":
        return "behavior_acceptance.md"
    if kind == "method_test_runner_json":
        return "method_test_runner.json"
    if kind == "method_test_runner_markdown":
        return "method_test_runner.md"
    if kind == "ui_evidence_runner_json":
        return "ui_evidence_runner.json"
    if kind == "ui_evidence_runner_markdown":
        return "ui_evidence_runner.md"
    if kind == "interaction_evidence_json":
        return "interaction_evidence.json"
    if kind == "interaction_evidence_markdown":
        return "interaction_evidence.md"
    if kind == "behavior_test_plan_json":
        return "behavior_test_plan.json"
    if kind == "behavior_test_plan_markdown":
        return "behavior_test_plan.md"
    if kind == "method_regression_result_json":
        return "method_regression_result.json"
    if kind == "method_regression_result_markdown":
        return "method_regression_result.md"
    if kind == "ui_evidence_manifest_json":
        return "ui_evidence_manifest.json"
    if kind == "ui_evidence_manifest_markdown":
        return "ui_evidence_manifest.md"
    if kind == "playwright_screenshot_index_markdown":
        return "playwright_screenshot_index.md"
    if kind == "code_review_markdown":
        return "code_review.md"
    if kind == "commit_ready_summary_markdown":
        return "commit_ready_summary.md"
    if kind == "single_demand_trial_json":
        return "single_demand_trial.json"
    if kind == "single_demand_trial_markdown":
        return "single_demand_trial.md"
    if kind == "review_manifest_json":
        return "review_manifest.json"
    if kind == "review_summary_markdown":
        return "review_summary.md"
    if kind == "review_diff":
        return "review.diff"
    if kind == "review_diff_check":
        return "review_diff_check.log"
    if kind.startswith("review_verify_"):
        attempt = kind.rsplit("_", 1)[-1]
        return f"review_verify_{attempt}.log"
    safe_kind = kind.replace("/", "_")
    suffix = ".json" if safe_kind.endswith("_json") or safe_kind in {"evaluation", "yunxiao_transaction_policy"} else ".md"
    return f"artifact_{artifact_id}_{safe_kind}{suffix}"


def normalize_project_paths(project_path: str | Path | list[str] | tuple[str, ...] | None) -> list[str]:
    if project_path is None:
        return []
    if isinstance(project_path, (list, tuple)):
        return [str(path).strip() for path in project_path if str(path).strip()]
    text = str(project_path).strip()
    return [text] if text else []


def enrich_yunxiao_comment_artifacts(*, run_id: int, artifacts: list[str], project_paths: list[str]) -> list[str]:
    enriched = list(artifacts)
    existing_types = artifact_types(enriched)
    if "commit" not in existing_types:
        commit = first_review_commit(run_id) or first_git_output(project_paths, ["rev-parse", "--short", "HEAD"])
        if commit:
            enriched.append(f"commit={commit}")
            existing_types.add("commit")
    if "changed_file" not in existing_types and "changed_files" not in existing_types:
        changed_files = artifact_changed_files(run_id) or first_git_changed_files(project_paths)
        for path in changed_files:
            enriched.append(f"changed_file={path}")
    return enriched


def artifact_types(artifacts: list[str]) -> set[str]:
    result: set[str] = set()
    for item in artifacts:
        text = str(item or "").strip()
        if "=" not in text:
            continue
        key, _ = text.split("=", 1)
        key = key.strip().lower()
        if key:
            result.add(key)
    return result


def first_review_commit(run_id: int) -> str:
    review = artifact_json(run_id, "review_manifest_json")
    commit = str(review.get("review_commit") or "").strip()
    return "" if commit in {"HEAD", "-"} else commit


def artifact_changed_files(run_id: int) -> list[str]:
    changed: list[str] = []
    review = artifact_json(run_id, "review_manifest_json")
    changed.extend(str(path).strip() for path in review.get("changed_paths") or [] if str(path).strip())
    precommit = artifact_json(run_id, "precommit_manifest_json")
    for target in precommit.get("targets") or []:
        if isinstance(target, dict):
            changed.extend(str(path).strip() for path in target.get("changed_paths") or [] if str(path).strip())
    return unique_strings(changed)


def artifact_json(run_id: int, kind: str) -> dict:
    for artifact in database.get_artifacts(run_id):
        if artifact.get("kind") != kind:
            continue
        try:
            data = json.loads(artifact.get("content") or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def first_git_output(project_paths: list[str], args: list[str]) -> str:
    for project_path in project_paths:
        value = git_output(project_path, args)
        if value:
            return value
    return ""


def first_git_changed_files(project_paths: list[str]) -> list[str]:
    for project_path in project_paths:
        changed = git_lines(project_path, ["diff", "--name-only"])
        if not changed:
            changed = git_lines(project_path, ["diff", "--name-only", "HEAD^..HEAD"])
        if changed:
            return changed
    return []


def git_output(project_path: str, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), *args],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def git_lines(project_path: str, args: list[str]) -> list[str]:
    return unique_strings(line.strip() for line in git_output(project_path, args).splitlines() if line.strip())


def unique_strings(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def build_requirement_governance_outputs(
    *,
    title: str,
    user_instruction: str,
    source_type: str,
    normalized_requirement_evidence: dict | None,
    yunxiao_evidence: dict | None,
    requirement_calibration: dict,
    technical_decision: dict,
    change_ownership: dict,
    acceptance_matrix: dict,
    local_change_evidence_exception: Mapping[str, Any] | None = None,
) -> tuple[object, object, str]:
    """Build governance data from already collected local evidence only."""
    from app.requirement_governance import GOVERNANCE_CHECK_NAMES, GovernanceCheck, RequirementGovernanceResult, assess_requirement
    from app.requirement_provider import normalize_requirement_evidence
    from app.single_pass_change_contract import SinglePassChangeContract, build_single_pass_change_contract

    objective = title.strip() or "手工需求"

    def blocked(reason: str) -> tuple[RequirementGovernanceResult, SinglePassChangeContract, str]:
        checks = tuple(
            GovernanceCheck(name=name, status="blocked", summary="治理输入无法安全闭合。", blockers=(reason,))
            for name in GOVERNANCE_CHECK_NAMES
        )
        governance = RequirementGovernanceResult(
            schema_version="requirement-governance.v1",
            status="blocked_needs_requirement",
            can_modify=False,
            can_complete_in_single_pass=False,
            risk_level="unknown",
            checks=checks,
            blockers=(reason,),
            missing_information=(reason,),
            unsupported_reasons=(),
            required_capabilities=(),
            evidence_refs=(),
        )
        contract = SinglePassChangeContract(
            schema_version="single-pass-change-contract.v1",
            status="blocked",
            objective=objective,
            in_scope=(),
            out_of_scope=(),
            repositories=(),
            allowed_paths=(),
            business_rules=(),
            preserved_behaviors=(),
            adjacent_paths=(),
            database_impacts=(),
            configuration_impacts=(),
            verify_commands=(),
            automatic_acceptance=(),
            manual_acceptance=(),
            rollback_strategy="not_available",
            blockers=(reason,),
        )
        return governance, contract, reason

    try:
        evidence = normalized_requirement_evidence
        if evidence is None:
            yunxiao = yunxiao_evidence if isinstance(yunxiao_evidence, dict) else {}
            evidence = normalize_requirement_evidence(
                source_type=source_type,
                payload={
                    "title": title,
                    "description_text": str(yunxiao.get("clean_text") or user_instruction),
                    "comments": yunxiao.get("comments") if isinstance(yunxiao.get("comments"), list) else [],
                    "attachments": yunxiao.get("attachments") if isinstance(yunxiao.get("attachments"), list) else [],
                    "images": yunxiao.get("inline_files") if isinstance(yunxiao.get("inline_files"), list) else [],
                },
            )
        governance = assess_requirement(
            title=title,
            user_instruction=user_instruction,
            normalized_requirement_evidence=evidence,
            requirement_calibration=requirement_calibration,
            technical_decision=technical_decision,
            change_ownership=change_ownership,
            acceptance_matrix=acceptance_matrix,
            available_capabilities=(),
            local_change_evidence_exception=local_change_evidence_exception,
        )
        if not isinstance(governance, RequirementGovernanceResult):
            return blocked("需求治理结果结构无效。")
        contract = build_single_pass_change_contract(
            governance_result=governance,
            objective=objective,
            requirement_calibration=requirement_calibration,
            technical_decision=technical_decision,
            change_ownership=change_ownership,
            acceptance_matrix=acceptance_matrix,
            normalized_requirement_evidence=evidence,
            available_capabilities=(),
        )
        if not isinstance(contract, SinglePassChangeContract):
            return blocked("一次改好变更契约结构无效。")
        return governance, contract, ""
    except Exception:
        return blocked("需求治理输入格式无效，已在本地阻断。")


def describe_runner_mode(runner: RequirementWorkflowRunner) -> str:
    return describe_mode(runner.llm_client)


def build_workflow_demand_text(
    *,
    demand_text: str,
    yunxiao_evidence: dict | None,
    requirement_evidence: dict | None = None,
    conversation_evidence: dict | None = None,
) -> str:
    parts = [demand_text.strip()]
    if requirement_evidence:
        parts.extend(
            [
                "【需求来源归一化证据】",
                compress_text(requirement_evidence_to_markdown(requirement_evidence), 4000),
            ]
        )
    if conversation_evidence:
        parts.extend(
            [
                "【对话与用户确认事实：不得以猜测推翻】",
                conversation_evidence_to_markdown(conversation_evidence),
            ]
        )
    if yunxiao_evidence:
        parts.extend(
            [
                "【云效只读证据摘要】",
                build_yunxiao_prompt_context(yunxiao_evidence),
            ]
        )
    return "\n\n".join(part for part in parts if part)


def infer_yunxiao_entity_kind(yunxiao_url: str) -> str:
    lowered = (yunxiao_url or "").lower()
    if "/bug/" in lowered:
        return "bug"
    if "/req/" in lowered or "/requirement/" in lowered:
        return "requirement"
    if "/task/" in lowered:
        return "task"
    return ""


def infer_yunxiao_outcome(
    *,
    run: dict,
    demand_text: str,
    title: str,
    execution_mode: str,
    verify_commands: list[str],
    evidence_bundle: EvidenceBundle | None,
) -> str:
    run_status = run.get("status") or ""
    run_error = str(run.get("error") or "")
    if run_status != "success":
        if "证据不足" in run_error or "澄清" in run_error or "blocked_needs_clarification" in run_error:
            return "analysis_unclear"
        return "verification_failed"
    if is_yunxiao_sensitive(demand_text=demand_text, title=title, evidence_bundle=evidence_bundle):
        return "high_risk_needs_review"
    if execution_mode in {"worktree", "fullstack-worktree", "single-demand-trial"}:
        return "all_passed" if verify_commands else "developed_unverified"
    if execution_mode == "precommit-verify":
        return "all_passed"
    if execution_mode == "review-worktree":
        return "all_passed"
    return "analysis_unclear"


def infer_yunxiao_risk_level(*, demand_text: str, title: str, evidence_bundle: EvidenceBundle | None) -> str:
    if evidence_bundle is not None:
        risk_level = str(evidence_bundle.risk.get("level") or "medium")
        if risk_level in {"high", "critical"}:
            return risk_level
    if is_yunxiao_sensitive(demand_text=demand_text, title=title, evidence_bundle=evidence_bundle):
        return "high"
    return str((evidence_bundle.risk.get("level") if evidence_bundle is not None else "") or "medium")


def is_yunxiao_sensitive(*, demand_text: str, title: str, evidence_bundle: EvidenceBundle | None) -> bool:
    text = f"{title}\n{demand_text}"
    if evidence_bundle is not None:
        text += "\n" + json.dumps(evidence_bundle.risk, ensure_ascii=False)
    return any(term in text for term in HIGH_RISK_TERMS)


def build_patch_review_markdown(result: WorktreeExecutionResult) -> str:
    lines = [
        "## v0.7.4 Patch Review",
        "",
        f"- 状态：{result.status}",
        f"- 结论：{result.summary}",
        f"- Worktree：{result.worktree_path or '-'}",
        f"- 允许修改路径：{', '.join(result.allowed_paths) if result.allowed_paths else '-'}",
        "- 禁止动作：未提交、未推送、未发布、未写云效。",
        "",
        "### 人工审查重点",
        "",
        "- 检查 diff 是否只围绕当前需求，不包含相似页面误改或无关历史清理。",
        "- 检查是否存在顺手格式化、无关逻辑修改或隐藏业务规则。",
        "- 检查验证命令是否无副作用，且失败日志没有被忽略。",
        "",
    ]
    if result.final_diff:
        lines.extend(["### Final Diff", "", "```diff", compress_text(result.final_diff, 12000), "```"])
    return "\n".join(lines)


def build_yunxiao_boundary_lines(policy_json: str) -> list[str]:
    try:
        policy = json.loads(policy_json)
    except json.JSONDecodeError:
        policy = {}
    mode = str(policy.get("mode") or "readonly")
    transport = str(policy.get("write_transport") or "")
    if mode == "write" and transport == "real":
        first_line = "- 当前运行处于云效 real write 模式；只有事务计划中 `write_executed` 的动作才代表真实写入。"
    elif mode == "write" and transport == "fake":
        first_line = "- 当前运行处于云效 fake write 模式；只验证写入执行器、幂等和审计，不写真实云效。"
    elif mode == "dry_run":
        first_line = "- 当前运行处于云效 dry-run 模式；只生成事务建议和审计记录，不写真实云效。"
    else:
        first_line = "- 当前运行不直接写入云效需求、缺陷、迭代、评论、负责人或状态流转。"
    return [
        first_line,
        "- v0.8.6 默认不写云效；write 模式必须显式确认，真实写入还需要策略允许、专用写凭证和 comment-only 边界；transition-fake 不调用真实状态接口。",
        "- 云效写动作必须显式开启，并绑定 run_id、证据、原因、幂等键和人工确认策略。",
        "- 高风险 HIS 需求禁止自动关闭；医保、结算、收费、报表、日报、对账、核算类状态流转需要人工闸口。",
    ]
