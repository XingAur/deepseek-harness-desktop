from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.acceptance_contracts import AcceptanceContractResult, ordering_contract_required
from app.requirement_calibration import default_value_precedence_is_resolved, find_high_risk_terms
from app.worktree_executor import extract_patch_paths


CORE_CLOSURE_VERSION = "0.37-core-closure"
REQUIREMENT_CONTRACT_VERSION = "1.0-requirement-contract"


@dataclass(frozen=True)
class RequirementContract:
    schema_version: str
    status: str
    title: str
    demand_digest: str
    source_priority: tuple[dict[str, Any], ...] = ()
    rules: tuple[dict[str, Any], ...] = ()
    default_behavior: str = ""
    default_guard_tokens: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    verify_commands: tuple[str, ...] = ()
    automatic_acceptance: tuple[str, ...] = ()
    manual_acceptance: tuple[str, ...] = ()
    acceptance_contract: dict[str, Any] = field(default_factory=dict)
    change_ownership: dict[str, Any] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()
    apply_to_project: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class DiffReview:
    schema_version: str
    status: str
    review_contract_digest: str
    project_path: str = ""
    final_diff_digest: str = ""
    allowed_paths: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    verified_rule_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class EngineeringHandoff:
    schema_version: str
    status: str
    project_path: str = ""
    allowed_paths: tuple[str, ...] = ()
    verify_commands: tuple[str, ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class CoreClosureResult:
    schema_version: str
    status: str
    summary: str
    contract: RequirementContract
    engineering_handoff: EngineeringHandoff
    diff_review: DiffReview | None = None
    worktree: dict[str, Any] = field(default_factory=dict)
    apply_to_project: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def build_requirement_contract(
    *,
    title: str,
    demand_text: str,
    requirement_calibration: dict[str, Any],
    technical_decision: dict[str, Any],
    acceptance_matrix: dict[str, Any],
    apply_to_project: bool,
    acceptance_contract_result: AcceptanceContractResult | None = None,
    change_ownership_matrix: dict[str, Any] | None = None,
) -> RequirementContract:
    blockers: list[str] = []
    warnings: list[str] = []
    calibration_status = str(requirement_calibration.get("status") or "")
    calibration_decision = requirement_calibration.get("decision") or {}
    if calibration_status != "ready_for_development" or not calibration_decision.get("can_enter_development"):
        blockers.append("需求校准未达到 ready_for_development，不能进入核心闭环改码。")
    default_value_precedence = requirement_calibration.get("default_value_precedence")
    if isinstance(default_value_precedence, dict) and default_value_precedence.get("required") and not default_value_precedence_is_resolved(default_value_precedence):
        blockers.append("默认值来源优先级未闭合，不能将通用表单、参数或页面硬编码默认值直接改为单一字段逻辑。")
    elif isinstance(default_value_precedence, dict) and default_value_precedence.get("required"):
        precedence_evidence = (technical_decision.get("field_provenance") or {}).get("default_value_precedence")
        if not default_value_precedence_evidence_is_closed(precedence_evidence):
            blockers.append("默认值来源优先级缺少通用表单、参数、页面硬编码、无默认值及同一初始化链路的源码证据，不能进入核心闭环改码。")

    if find_high_risk_terms(title=title, demand_text=demand_text):
        blockers.append("需求命中高风险 HIS 关键词，core-closure-trial 只处理低风险基础需求。")

    implementation = technical_decision.get("implementation_decision") or {}
    if not implementation.get("can_patch"):
        blockers.extend(normalize_text_list(implementation.get("blockers")))
        blockers.append("技术决策未允许自动 patch，不能进入核心闭环改码。")

    contract_verification = technical_decision.get("contract_verification") or {}
    if contract_verification.get("required") and contract_verification.get("status") != "verified":
        blockers.append("前后端契约未核验通过，不能仅凭需求评论或模型判断进入自动改码。")

    change_ownership = dict(change_ownership_matrix or {})
    if change_ownership and change_ownership.get("status") != "ready":
        blockers.append("需求变更归属矩阵未闭合，禁止在前端、后端、数据库或配置边界不明确时自动改码。")
        blockers.extend(normalize_text_list(change_ownership.get("blockers")))

    allowed_paths = tuple(normalize_text_list(technical_decision.get("recommended_allowed_paths")))
    if not allowed_paths:
        blockers.append("缺少允许修改路径，不能进入受控 worktree。")

    verify_commands = tuple(normalize_text_list(technical_decision.get("recommended_verify_commands")))
    if not verify_commands:
        blockers.append("缺少专项验证命令，不能声明基础需求可交付。")

    provenance = technical_decision.get("field_provenance") or {}
    evidence_refs = tuple(normalize_evidence(provenance.get("evidence")))
    if not provenance.get("target_ui_found") or not evidence_refs:
        blockers.append("缺少目标页面或模块的工程证据，不能仅凭模型猜测改码位置。")

    resolved_parameters = requirement_calibration.get("resolved_parameters") or []
    rules, default_behavior, default_guard_tokens = extract_rules(resolved_parameters)
    if not rules:
        blockers.append("未从需求校准中提取到可验证业务规则。")
    if not default_behavior:
        blockers.append("缺少空值、未传或非法值的默认行为，不能安全改动过滤逻辑。")

    source_priority = tuple(normalize_dict_list(requirement_calibration.get("source_priority")))
    source_warnings = requirement_calibration.get("warnings") or []
    for item in source_warnings:
        if not isinstance(item, dict):
            continue
        warning_type = str(item.get("type") or "")
        message = str(item.get("message") or "")
        if warning_type == "source_conflict" and not user_instruction_is_primary(source_priority):
            blockers.append("需求来源存在未解决冲突，且用户补充规则未被确认为最高优先级。")
        elif message:
            warnings.append(message)

    automatic_acceptance, manual_acceptance = extract_acceptance(acceptance_matrix)
    if not automatic_acceptance and rules:
        automatic_acceptance = unique_keep_order(
            str(rule.get("statement") or "").strip()
            for rule in rules
            if str(rule.get("statement") or "").strip()
        )
    if not automatic_acceptance:
        blockers.append("缺少可自动验证的验收项，不能进入基础需求自动试跑。")

    requires_ordering_contract = ordering_contract_required(title=title, demand_text=demand_text)
    acceptance_contract = acceptance_contract_result.to_dict() if acceptance_contract_result is not None else {}
    if requires_ordering_contract and acceptance_contract_result is None:
        blockers.append("排序/方案树关联需求缺少可执行排序验收契约。")
    elif acceptance_contract_result is not None and acceptance_contract_result.status != "pass":
        blockers.extend(acceptance_contract_result.blockers)
    elif requires_ordering_contract and not acceptance_contract_result.verify_command:
        blockers.append("排序/方案树关联验收契约缺少 verify_command。")
    elif requires_ordering_contract and not acceptance_contract_result.implementation_evidence:
        blockers.append("排序/方案树关联验收契约缺少 implementation_evidence。")

    return RequirementContract(
        schema_version=REQUIREMENT_CONTRACT_VERSION,
        status="blocked" if blockers else "ready",
        title=title.strip() or "手工需求",
        demand_digest=demand_text.strip()[:1000],
        source_priority=source_priority,
        rules=tuple(rules),
        default_behavior=default_behavior,
        default_guard_tokens=default_guard_tokens,
        allowed_paths=allowed_paths,
        verify_commands=verify_commands,
        automatic_acceptance=tuple(automatic_acceptance),
        manual_acceptance=tuple(manual_acceptance),
        acceptance_contract=acceptance_contract,
        change_ownership=change_ownership,
        blockers=tuple(unique_keep_order(blockers)),
        warnings=tuple(unique_keep_order(warnings)),
        evidence_refs=evidence_refs,
        apply_to_project=bool(apply_to_project),
    )


def default_value_precedence_evidence_is_closed(value: object) -> bool:
    if not isinstance(value, dict) or value.get("required") is not True or value.get("status") != "verified":
        return False
    expected = ("common_form_setting", "parameter_setting", "page_hardcoded_default", "no_default")
    sources = value.get("sources")
    if not isinstance(sources, list) or len(sources) != len(expected):
        return False
    if tuple(item.get("source") for item in sources if isinstance(item, dict)) != expected:
        return False
    if any(
        not isinstance(item, dict)
        or item.get("status") != "verified"
        or not isinstance(item.get("evidence"), list)
        or not item.get("evidence")
        for item in sources
    ):
        return False
    return isinstance(value.get("precedence_chain"), list) and bool(value.get("precedence_chain"))


def build_requirement_contract_from_single_pass(
    *,
    title: str,
    demand_text: str,
    governance_result: object,
    single_pass_contract: object,
    apply_to_project: bool,
    integration_blocker: str = "",
    legacy_contract: RequirementContract | None = None,
    acceptance_contract_result: AcceptanceContractResult | None = None,
) -> RequirementContract:
    """Adapt a validated governance contract to the legacy core-closure shape."""
    safe_title = title.strip() or "手工需求"
    safe_digest = demand_text.strip()[:1000]
    blocked_reason = integration_blocker.strip() or "需求治理或一次改好变更契约未达到 ready，不能进入核心闭环改码。"
    governance, single_pass = validate_requirement_governance_outputs(
        governance_result,
        single_pass_contract,
    )
    if (
        governance is None
        or governance.status != "ready_for_local_change"
        or governance.can_modify is not True
        or governance.can_complete_in_single_pass is not True
        or single_pass is None
        or single_pass.status != "ready"
        or (legacy_contract is not None and (type(legacy_contract) is not RequirementContract or legacy_contract.status != "ready"))
    ):
        return RequirementContract(
            schema_version=REQUIREMENT_CONTRACT_VERSION,
            status="blocked",
            title=safe_title,
            demand_digest=safe_digest,
            blockers=(blocked_reason,),
            apply_to_project=bool(apply_to_project),
        )

    source_priority = tuple(
        {
            "source": str(item.get("source") or "requirement_governance"),
            "reason": "需求治理已验证的结构化证据引用。",
        }
        for item in governance.evidence_refs
    )
    if legacy_contract is not None:
        allowed_paths = tuple(path for path in single_pass.allowed_paths if path in legacy_contract.allowed_paths)
        verify_commands = legacy_contract.verify_commands
        rules = tuple([*legacy_contract.rules, *single_pass.business_rules])
        default_behavior = legacy_contract.default_behavior or "；".join(single_pass.preserved_behaviors)
        default_guard_tokens = legacy_contract.default_guard_tokens
        automatic_acceptance = tuple(unique_keep_order([*legacy_contract.automatic_acceptance, *single_pass.automatic_acceptance]))
        manual_acceptance = tuple(unique_keep_order([*legacy_contract.manual_acceptance, *single_pass.manual_acceptance]))
        acceptance_contract = dict(legacy_contract.acceptance_contract)
        warnings = legacy_contract.warnings
        evidence_refs = legacy_contract.evidence_refs or governance.evidence_refs
        change_ownership = dict(legacy_contract.change_ownership)
        if not allowed_paths or not verify_commands:
            return RequirementContract(
                schema_version=REQUIREMENT_CONTRACT_VERSION,
                status="blocked",
                title=safe_title,
                demand_digest=safe_digest,
                blockers=("需求治理契约不能扩大旧核心闭环的路径或验证命令范围。",),
                apply_to_project=bool(apply_to_project),
            )
    else:
        allowed_paths = single_pass.allowed_paths
        verify_commands = single_pass.verify_commands
        rules = single_pass.business_rules
        default_behavior = "；".join(single_pass.preserved_behaviors)
        default_guard_tokens = ()
        automatic_acceptance = single_pass.automatic_acceptance
        manual_acceptance = single_pass.manual_acceptance
        acceptance_contract = acceptance_contract_result.to_dict() if acceptance_contract_result is not None else {}
        warnings = ()
        evidence_refs = governance.evidence_refs
        change_ownership = {
            "status": "ready",
            "source": "single_pass_change_contract",
            "database_impacts": list(single_pass.database_impacts),
            "configuration_impacts": list(single_pass.configuration_impacts),
        }
    return RequirementContract(
        schema_version=REQUIREMENT_CONTRACT_VERSION,
        status="ready",
        title=safe_title,
        demand_digest=safe_digest,
        source_priority=source_priority,
        rules=rules,
        default_behavior=default_behavior,
        default_guard_tokens=default_guard_tokens,
        allowed_paths=allowed_paths,
        verify_commands=verify_commands,
        automatic_acceptance=automatic_acceptance,
        manual_acceptance=manual_acceptance,
        acceptance_contract=acceptance_contract,
        change_ownership=change_ownership,
        warnings=warnings,
        evidence_refs=evidence_refs,
        apply_to_project=bool(apply_to_project),
    )


def validate_requirement_governance_outputs(
    governance_result: object,
    single_pass_contract: object,
) -> tuple[object | None, object | None]:
    """Return fresh exact-model instances after complete constructor validation."""
    return (
        _canonical_governance_result(governance_result),
        _canonical_single_pass_contract(single_pass_contract),
    )


def _canonical_governance_result(value: object) -> object | None:
    """Reconstruct an exact governance model so frozen-object mutation cannot grant execution."""
    from app.requirement_governance import GovernanceCheck, RequirementGovernanceResult

    if type(value) is not RequirementGovernanceResult:
        return None
    try:
        payload = value.to_dict()
        if not isinstance(payload, dict) or not isinstance(payload.get("checks"), list):
            return None
        return RequirementGovernanceResult(
            schema_version=payload["schema_version"],
            status=payload["status"],
            can_modify=payload["can_modify"],
            can_complete_in_single_pass=payload["can_complete_in_single_pass"],
            risk_level=payload["risk_level"],
            checks=tuple(GovernanceCheck(**item) for item in payload["checks"] if isinstance(item, dict)),
            blockers=payload["blockers"],
            missing_information=payload["missing_information"],
            unsupported_reasons=payload["unsupported_reasons"],
            required_capabilities=payload["required_capabilities"],
            evidence_refs=payload["evidence_refs"],
        )
    except Exception:
        return None


def _canonical_single_pass_contract(value: object) -> object | None:
    """Reconstruct an exact single-pass model before copying any executable fields."""
    from app.single_pass_change_contract import SinglePassChangeContract

    if type(value) is not SinglePassChangeContract:
        return None
    try:
        payload = value.to_dict()
        if not isinstance(payload, dict):
            return None
        return SinglePassChangeContract(
            schema_version=payload["schema_version"],
            status=payload["status"],
            objective=payload["objective"],
            in_scope=tuple(payload["in_scope"]),
            out_of_scope=tuple(payload["out_of_scope"]),
            repositories=tuple(payload["repositories"]),
            allowed_paths=tuple(payload["allowed_paths"]),
            business_rules=tuple(payload["business_rules"]),
            preserved_behaviors=tuple(payload["preserved_behaviors"]),
            adjacent_paths=tuple(payload["adjacent_paths"]),
            database_impacts=tuple(payload["database_impacts"]),
            configuration_impacts=tuple(payload["configuration_impacts"]),
            verify_commands=tuple(payload["verify_commands"]),
            automatic_acceptance=tuple(payload["automatic_acceptance"]),
            manual_acceptance=tuple(payload["manual_acceptance"]),
            rollback_strategy=payload["rollback_strategy"],
            blockers=tuple(payload["blockers"]),
        )
    except Exception:
        return None


def extract_rules(parameters: list[Any]) -> tuple[list[dict[str, Any]], str, tuple[str, ...]]:
    rules: list[dict[str, Any]] = []
    default_behavior = ""
    default_guard_tokens: list[str] = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        name = str(parameter.get("name") or "")
        allowed_values = parameter.get("allowed_values") or {}
        if not name or not isinstance(allowed_values, dict):
            continue
        for value, statement in allowed_values.items():
            statement_text = str(statement or "").strip()
            if not statement_text:
                continue
            if str(value) in {"empty", "default", "other"}:
                default_behavior = statement_text
                default_guard_tokens.extend(normalize_text_list(parameter.get("default_evidence_tokens")))
                continue
            rules.append(
                {
                    "id": f"PARAM-{name}-{value}",
                    "parameter": name,
                    "value": str(value),
                    "statement": statement_text,
                    "source": str(parameter.get("source") or "requirement_calibration"),
                    "evidence_tokens": normalize_text_list(parameter.get("evidence_tokens")),
                }
            )
    return rules, default_behavior, tuple(unique_keep_order(default_guard_tokens))


def review_final_diff(
    *,
    contract: RequirementContract,
    project_path: str | Path = "",
    final_diff: str,
    verification_passed: bool,
    acceptance_contract_result: AcceptanceContractResult | None = None,
) -> DiffReview:
    findings: list[str] = []
    if contract.status != "ready":
        findings.append("需求契约未 ready，禁止审查或放行 patch。")
    if not final_diff.strip():
        findings.append("final.diff 为空，无法证明需求已实现。")
    changed_paths = tuple(extract_patch_paths(final_diff))
    if not changed_paths:
        findings.append("未能从 final.diff 解析变更路径。")
    outside_paths = [path for path in changed_paths if path not in contract.allowed_paths]
    if outside_paths:
        findings.append("存在白名单外变更路径：" + ", ".join(outside_paths))
    if not verification_passed:
        findings.append("worktree 或专项验证未通过，独立审查不能放行。")

    if contract.acceptance_contract:
        if (
            type(acceptance_contract_result) is not AcceptanceContractResult
            or acceptance_contract_result.status != "pass"
        ):
            findings.append("可执行排序验收契约未通过，独立审查不能放行。")
        elif acceptance_contract_result.to_dict() != contract.acceptance_contract:
            findings.append("可执行排序验收结果与需求契约不一致，独立审查不能放行。")
        else:
            added_diff = extract_added_diff_text(final_diff)
            for token in acceptance_contract_result.implementation_evidence:
                if token not in added_diff:
                    findings.append(f"未在 diff 中找到排序验收契约实现证据：{token}。")
    elif acceptance_contract_result is not None:
        findings.append("需求契约未声明可执行验收契约，禁止携带额外验收结果。")

    verified_rule_ids: list[str] = []
    for rule in contract.rules:
        if diff_has_rule_signal(final_diff=final_diff, rule=rule):
            verified_rule_ids.append(str(rule.get("id") or ""))
        else:
            findings.append(f"未在 diff 中找到规则 {rule.get('id') or '-'} 的实现证据。")

    if contract.default_behavior and not diff_has_default_guard(
        final_diff,
        contract.default_behavior,
        default_guard_tokens=contract.default_guard_tokens,
    ):
        findings.append("未在 diff 中找到默认模式保护，空值、未传或非法参数可能改变原有行为。")

    return DiffReview(
        schema_version="1.0-diff-review",
        status="pass" if not findings else "blocked",
        review_contract_digest=hashlib.sha256(
            json.dumps(
                contract.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        project_path=(
            str(Path(project_path).expanduser().resolve())
            if str(project_path).strip()
            else ""
        ),
        final_diff_digest=hashlib.sha256(
            final_diff.encode("utf-8")
        ).hexdigest(),
        allowed_paths=tuple(contract.allowed_paths),
        changed_paths=changed_paths,
        findings=tuple(unique_keep_order(findings)),
        verified_rule_ids=tuple(item for item in verified_rule_ids if item),
    )


def build_engineering_handoff(
    *,
    contract: RequirementContract,
    technical_decision: dict[str, Any],
) -> EngineeringHandoff:
    blockers: list[str] = []
    project_path = ""
    for project in technical_decision.get("selected_projects") or []:
        if isinstance(project, dict) and project.get("exists") and project.get("path"):
            project_path = str(project["path"])
            break
    if contract.status != "ready":
        blockers.append("需求契约未 ready，不能形成工程交接。")
    if not project_path:
        blockers.append("未找到存在的主项目，不能进入 worktree。")
    if not contract.evidence_refs:
        blockers.append("工程交接缺少证据引用，不能只凭建议文件改码。")
    return EngineeringHandoff(
        schema_version="1.0-engineering-handoff",
        status="blocked" if blockers else "ready",
        project_path=project_path,
        allowed_paths=contract.allowed_paths,
        verify_commands=contract.verify_commands,
        evidence_refs=contract.evidence_refs,
        blockers=tuple(unique_keep_order(blockers)),
    )


def build_core_closure_result(
    *,
    contract: RequirementContract,
    engineering_handoff: EngineeringHandoff,
    worktree: dict[str, Any] | None = None,
    diff_review: DiffReview | None = None,
) -> CoreClosureResult:
    if contract.status != "ready" or engineering_handoff.status != "ready":
        status = "blocked"
        summary = "核心闭环在需求或工程证据闸门被阻断，未进入 worktree 改码。"
    elif not worktree or worktree.get("status") != "success":
        status = "blocked"
        summary = "受控 worktree 改码或专项验证未通过，不能交付。"
    elif diff_review is None or diff_review.status != "pass":
        status = "blocked"
        summary = "独立 diff 审查未通过，不能进入人工代码审查。"
    else:
        status = "ready_for_manual_review"
        summary = "核心闭环已通过结构化契约、受控改码、专项验证和独立 diff 审查；仍需人工代码审查与业务验收。"
    return CoreClosureResult(
        schema_version="1.0-core-closure-result",
        status=status,
        summary=summary,
        contract=contract,
        engineering_handoff=engineering_handoff,
        diff_review=diff_review,
        worktree=dict(worktree or {}),
        apply_to_project=bool((worktree or {}).get("apply_to_project", {}).get("status") == "success"),
    )


def core_closure_to_markdown(result: CoreClosureResult) -> str:
    lines = [
        "## v0.37 Core Closure",
        "",
        f"- 状态：{result.status}",
        f"- 结论：{result.summary}",
        f"- 是否本地合入：{'是' if result.apply_to_project else '否'}",
        "",
        "### 契约闸门",
        "",
        f"- 状态：{result.contract.status}",
        f"- 白名单：{', '.join(result.contract.allowed_paths) or '-'}",
        f"- 验证命令：{', '.join(result.contract.verify_commands) or '-'}",
        f"- 默认行为：{result.contract.default_behavior or '-'}",
        "",
        "### 工程交接",
        "",
        f"- 状态：{result.engineering_handoff.status}",
        f"- 主项目：{result.engineering_handoff.project_path or '-'}",
        "",
        "### 独立 Diff 审查",
        "",
    ]
    if result.diff_review is None:
        lines.append("- 未执行：前置闸门或 worktree 未通过。")
    else:
        lines.append(f"- 状态：{result.diff_review.status}")
        for finding in result.diff_review.findings:
            lines.append(f"- {finding}")
    lines.extend(["", "### 人工业务验收", ""])
    for item in result.contract.manual_acceptance:
        lines.append(f"- {item}")
    if not result.contract.manual_acceptance:
        lines.append("- 仍需依据真实页面和业务数据执行人工验收。")
    return "\n".join(lines)


def diff_has_rule_signal(*, final_diff: str, rule: dict[str, Any]) -> bool:
    parameter = str(rule.get("parameter") or "")
    value = str(rule.get("value") or "")
    statement = str(rule.get("statement") or "")
    evidence_tokens = normalize_text_list(rule.get("evidence_tokens"))
    if evidence_tokens:
        return all(token in final_diff for token in evidence_tokens)
    if parameter == "top_tab_state":
        return has_component_name_replacement(final_diff)
    if parameter != "paiBanMs":
        return parameter in final_diff and value in final_diff
    branch_tokens = [
        f"paiBanMs) === '{value}'",
        f'paiBanMs) === "{value}"',
        f"paiBanMs === '{value}'",
        f'paiBanMs === "{value}"',
    ]
    branch_present = any(token in final_diff for token in branch_tokens)
    if value == "1":
        return branch_present and ("!item.doctorId" in final_diff or "!item.yiSheng" in final_diff)
    if value == "2":
        return branch_present and ("item.doctorId" in final_diff or "item.yiSheng" in final_diff)
    return branch_present and statement in final_diff


def extract_added_diff_text(final_diff: str) -> str:
    return "\n".join(
        line[1:]
        for line in final_diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def diff_has_default_guard(
    final_diff: str,
    default_behavior: str,
    *,
    default_guard_tokens: tuple[str, ...] = (),
) -> bool:
    if default_guard_tokens:
        return all(token in final_diff for token in default_guard_tokens)
    if "首次进入或页面已关闭后" in default_behavior:
        return has_component_name_replacement(final_diff)
    has_parameter_guard = "paiBanMs || ''" in final_diff or "paiBanMs ?? ''" in final_diff
    has_allowed_values = "'1'" in final_diff and "'2'" in final_diff
    has_passthrough = "return paiBanList" in final_diff
    return has_parameter_guard and has_allowed_values and has_passthrough and bool(default_behavior)


def has_component_name_replacement(final_diff: str) -> bool:
    removed_name = re.search(r"^-\s+name:\s*['\"][^'\"]+['\"]", final_diff, flags=re.MULTILINE)
    added_name = re.search(r"^\+\s+name:\s*['\"][^'\"]+['\"]", final_diff, flags=re.MULTILINE)
    return bool(removed_name and added_name)


def extract_acceptance(matrix: dict[str, Any]) -> tuple[list[str], list[str]]:
    automatic: list[str] = []
    manual: list[str] = []
    if isinstance(matrix.get("items"), list):
        for item in matrix["items"]:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement") or item.get("expected_result") or "").strip()
            if not statement:
                continue
            if item.get("kind") == "manual":
                manual.append(statement)
            else:
                automatic.append(statement)
        return unique_keep_order(automatic), unique_keep_order(manual)

    for item in matrix.get("auto_verification") or []:
        if isinstance(item, dict):
            statement = str(item.get("expected_result") or item.get("scenario") or "").strip()
            if statement:
                automatic.append(statement)
    for item in matrix.get("requirement_acceptance") or []:
        if isinstance(item, dict):
            statement = str(item.get("expected_result") or item.get("scenario") or "").strip()
            if statement:
                manual.append(statement)
    for item in matrix.get("manual_acceptance") or []:
        if isinstance(item, dict):
            statement = str(item.get("expected_result") or item.get("scenario") or "").strip()
            if statement:
                manual.append(statement)
    return unique_keep_order(automatic), unique_keep_order(manual)


def user_instruction_is_primary(source_priority: tuple[dict[str, Any], ...]) -> bool:
    return any(
        item.get("source") == "user_instruction" and int(item.get("priority") or 99) == 1
        for item in source_priority
    )


def normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return unique_keep_order(str(item).strip() for item in value if str(item).strip())


def normalize_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def normalize_evidence(value: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in normalize_dict_list(value):
        path = str(item.get("path") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if path and reason:
            evidence.append({"project": str(item.get("project") or ""), "path": path, "reason": reason})
    return evidence


def unique_keep_order(items: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
