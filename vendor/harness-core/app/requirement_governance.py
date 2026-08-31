from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from app.requirement_calibration import default_value_precedence_is_resolved
from app.requirement_provider import local_change_evidence_exception_is_valid


GOVERNANCE_SCHEMA_VERSION = "requirement-governance.v1"
GOVERNANCE_CHECK_NAMES = (
    "source_integrity",
    "reasonableness",
    "compliance",
    "completeness",
    "changeability",
    "impact",
    "verification",
    "single_pass_readiness",
)
GOVERNANCE_STATUSES = {
    "ready_for_local_change",
    "review_only",
    "blocked_needs_requirement",
    "blocked_needs_business_decision",
    "blocked_unsupported",
}
CHECK_STATUSES = {"pass", "warning", "blocked", "not_applicable"}
_OWNERSHIP_LAYERS = ("frontend", "backend", "database", "configuration")
_RESOLVED_OWNERSHIP_STATUSES = {"required", "not_required", "already_satisfied"}
_UNSAFE_ATTACHMENT_STATUSES = {"failed", "missing", "incomplete", "unavailable"}
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_EXECUTABLE_COMMAND = re.compile(r"^[A-Za-z0-9_./:-]+(?:\s+[-A-Za-z0-9_./:=]+)*$")
_RISK_LEVELS = {"low", "medium", "high", "critical", "unknown"}
_CAPABILITY_LIST_FIELDS = ("required_capabilities", "capability_requirements")
_CAPABILITY_SINGLE_FIELDS = ("required_capability", "requires_capability")
_CAPABILITY_NESTED_FIELDS = ("implementation_decision", "rows", "operations", "database", "configuration")
_STRUCTURED_ARRAY_TYPES = (list, tuple)
_UNTRUSTED_OVERRIDE_PATTERNS = (
    re.compile(r"(?:忽略|无视)\s{0,24}(?:所有\s{0,24})?(?:规则|指令|系统|开发者)"),
    re.compile(r"(?:system|developer)\s+(?:instruction\s+)?override", re.IGNORECASE),
    re.compile(r"(?:ignore|disregard)\s{1,24}(?:all\s{1,24})?(?:rules|instructions)", re.IGNORECASE),
)
_UNTRUSTED_ACTION_PATTERNS = (
    re.compile(r"(?:运行|执行|run|execute).{0,24}(?:shell|命令|command)", re.IGNORECASE),
    re.compile(r"(?:读取|read).{0,24}(?:pat|token|credential|secret|凭证)", re.IGNORECASE),
    re.compile(r"(?:自动|auto(?:matically)?).{0,24}(?:git\s+push|push|评论回|comment)", re.IGNORECASE),
    re.compile(r"(?:执行|execute).{0,24}(?:git\s+push|评论回|comment)", re.IGNORECASE),
)
_PROVIDER_CLAUSE_BOUNDARY = re.compile(r"([。！？!?.；;\n，,:：]+)")
_PROVIDER_SOFT_CLAUSE_BOUNDARY = re.compile(r"^[，,:：]+$")
_PROVIDER_IMPERATIVE_PREFIX = re.compile(
    r"^(?:(?:请帮我|帮我|麻烦|务必|请|立即|必须|然后|现在|please\s+kindly|please|could\s+you|would\s+you|now|must|then)\s*)+",
    re.IGNORECASE,
)
_TRUSTED_EXACT_RULE_CHOICE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\s*(?:必须|应当)\s*(?:使用|为)\s*(?:===|==|!==|!=)\s*[；;。]?\s*默认(?:分支|行为)\s*(?:保持原逻辑|保持不变)[；;。]?"
)
_PROVIDER_LOCATION_FIELDS = {
    "title", "description_text", "description", "content", "comment", "body", "text",
    "comments", "attachments", "warnings", "message", "name", "metadata", "authorization",
    "explicit", "approved", "scope", "capabilities", "commands",
}


@dataclass(frozen=True)
class GovernanceCheck:
    name: str
    status: str
    summary: str
    evidence_refs: tuple[dict[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.name not in GOVERNANCE_CHECK_NAMES:
            raise ValueError("未知治理检查域。")
        if self.status not in CHECK_STATUSES:
            raise ValueError("治理检查状态无效。")
        object.__setattr__(self, "summary", _text(self.summary))
        object.__setattr__(self, "evidence_refs", _stable_evidence(self.evidence_refs))
        object.__setattr__(self, "blockers", _stable_texts(self.blockers))
        object.__setattr__(self, "warnings", _stable_texts(self.warnings))


@dataclass(frozen=True)
class RequirementGovernanceResult:
    schema_version: str
    status: str
    can_modify: bool
    can_complete_in_single_pass: bool
    risk_level: str
    checks: tuple[GovernanceCheck, ...]
    blockers: tuple[str, ...]
    missing_information: tuple[str, ...]
    unsupported_reasons: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    evidence_refs: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if self.schema_version != GOVERNANCE_SCHEMA_VERSION:
            raise ValueError("治理报告 schema 版本无效。")
        if self.status not in GOVERNANCE_STATUSES:
            raise ValueError("治理报告状态无效。")
        if tuple(item.name for item in self.checks) != GOVERNANCE_CHECK_NAMES:
            raise ValueError("治理报告必须包含且仅包含固定顺序的八个检查域。")
        if not isinstance(self.can_modify, bool) or not isinstance(self.can_complete_in_single_pass, bool):
            raise ValueError("治理报告顶层决策标记必须为布尔值。")
        if self.can_modify != (self.status == "ready_for_local_change"):
            raise ValueError("can_modify 只能与 ready_for_local_change 同时为 true。")
        if self.can_complete_in_single_pass != (self.status == "ready_for_local_change"):
            raise ValueError("can_complete_in_single_pass 只能与 ready_for_local_change 同时为 true。")
        risk_level = _text(self.risk_level)
        if risk_level not in _RISK_LEVELS:
            raise ValueError("治理报告风险等级无效。")
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "blockers", _stable_texts(self.blockers))
        object.__setattr__(self, "missing_information", _stable_texts(self.missing_information))
        object.__setattr__(self, "unsupported_reasons", _stable_texts(self.unsupported_reasons))
        object.__setattr__(self, "required_capabilities", _stable_texts(self.required_capabilities))
        object.__setattr__(self, "evidence_refs", _stable_evidence(self.evidence_refs))
        if any(check.status == "pass" and (check.blockers or check.warnings) for check in self.checks):
            raise ValueError("通过的治理检查不能包含阻断项或提醒。")
        if any(check.status == "blocked" and not check.blockers for check in self.checks):
            raise ValueError("阻断治理检查必须包含阻断项。")
        if any(check.status == "warning" and not check.warnings for check in self.checks):
            raise ValueError("警告治理检查必须包含提醒。")
        if any(check.status == "warning" and check.blockers for check in self.checks):
            raise ValueError("警告治理检查不能包含阻断项。")
        if self.status == "ready_for_local_change":
            if any(check.status != "pass" for check in self.checks) or any((self.blockers, self.missing_information, self.unsupported_reasons)):
                raise ValueError("ready_for_local_change 必须八域通过且无阻断项。")
        elif all(check.status == "pass" for check in self.checks) or not self.blockers:
            raise ValueError("非 ready 治理报告必须有非通过检查域和阻断项。")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), ensure_ascii=False, sort_keys=True))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    def to_markdown(self) -> str:
        lines = [
            "# HIS 需求治理报告",
            "",
            f"- Schema：{self.schema_version}",
            f"- 结论：{self.status}",
            f"- 可本地修改：{'是' if self.can_modify else '否'}",
            f"- 可一次受控闭环：{'是' if self.can_complete_in_single_pass else '否'}",
            f"- 风险等级：{self.risk_level}",
            "",
            "## 八域检查",
            "",
            "| 检查域 | 状态 | 摘要 |",
            "| --- | --- | --- |",
        ]
        for check in self.checks:
            lines.append(f"| {check.name} | {check.status} | {check.summary.replace('|', '/')} |")
        _append_markdown_list(lines, "阻断项", self.blockers)
        _append_markdown_list(lines, "缺失信息", self.missing_information)
        _append_markdown_list(lines, "不支持原因", self.unsupported_reasons)
        _append_markdown_list(lines, "所需能力", self.required_capabilities)
        return "\n".join(lines)


def assess_requirement(
    *,
    title: str,
    user_instruction: str,
    normalized_requirement_evidence: dict[str, Any] | None,
    requirement_calibration: dict[str, Any],
    technical_decision: dict[str, Any],
    change_ownership: dict[str, Any],
    acceptance_matrix: dict[str, Any],
    available_capabilities: Sequence[str],
    local_change_evidence_exception: Mapping[str, Any] | None = None,
) -> RequirementGovernanceResult:
    """Assess already-computed evidence only; this function never performs I/O or mutation."""
    evidence = _mapping(normalized_requirement_evidence)
    calibration = _mapping(requirement_calibration)
    technical = _mapping(technical_decision)
    ownership = _mapping(change_ownership)
    acceptance = _mapping(acceptance_matrix)
    available, capabilities_valid = _capability_set(available_capabilities)
    provider_instruction_refs = _provider_instruction_refs(evidence)
    source_conflict = _source_conflict_state(calibration, user_instruction=user_instruction)

    technical_state = _technical_state(technical)
    ownership_state = _ownership_state(ownership)
    acceptance_state = _acceptance_state(acceptance)
    local_change_exception_valid = local_change_evidence_exception_is_valid(
        normalized_evidence=evidence,
        exception=local_change_evidence_exception,
    )
    source_missing = _source_missing(
        title=title,
        evidence=evidence,
        allow_confirmed_inline_media_gap=local_change_exception_valid,
    )
    calibration_missing = _calibration_missing(calibration, user_instruction=user_instruction)
    acceptance_missing = _acceptance_missing(acceptance, state=acceptance_state)
    malformed = _malformed_inputs(
        evidence=evidence,
        calibration=calibration,
        technical=technical,
        ownership=ownership,
        acceptance=acceptance,
        capabilities_valid=capabilities_valid,
    )
    required_capabilities, capability_fields_valid = _capability_values(technical, ownership, acceptance)
    capability_malformed = ("能力需求结构无效。",) if not capability_fields_valid else ()
    missing_information = _stable_texts([
        *source_missing, *calibration_missing, *acceptance_missing, *malformed,
        *technical_state["malformed"], *ownership_state["malformed"],
        *capability_malformed,
    ])

    risk_level = acceptance_state["risk_level"]
    required_capabilities = required_capabilities if capability_fields_valid else ()
    missing_capabilities = tuple(item for item in required_capabilities if item not in available)
    ownership_unsupported = _has_unsupported_operation(technical, ownership, acceptance)
    unsupported_reasons = _stable_texts(
        ["缺少显式所需能力。"] if missing_capabilities else []
        + (["变更归属明确标记为不支持。"] if ownership_unsupported else [])
    )

    business_unresolved = risk_level in {"high", "critical"} and _has_unresolved_business_interpretation(
        calibration,
        acceptance,
        source_conflict_resolved=source_conflict["exact_user_choice"],
    )
    impact_state = _impact_state(acceptance)
    verification_state = _verification_state(technical, acceptance, acceptance_state=acceptance_state)
    domain_blockers = _stable_texts([
        *technical_state["blockers"], *ownership_state["blockers"], *impact_state["blockers"], *verification_state["blockers"],
    ])
    single_pass = (
        not missing_information
        and not unsupported_reasons
        and not business_unresolved
        and technical_state["paths_ready"]
        and technical_state["contract_ready"]
        and technical_state["can_patch"]
        and ownership_state["ready"]
        and ownership_state["database_configuration_resolved"]
        and impact_state["ready"]
        and verification_state["automatic_ready"]
        and verification_state["manual_ready"]
        and not provider_instruction_refs
        and not source_conflict["detected"]
        and not domain_blockers
    )

    if unsupported_reasons:
        status = "blocked_unsupported"
    elif business_unresolved:
        status = "blocked_needs_business_decision"
    elif missing_information:
        status = "blocked_needs_requirement"
    elif provider_instruction_refs or source_conflict["detected"]:
        status = "review_only"
    elif domain_blockers or not (technical_state["can_patch"] and technical_state["paths_ready"] and technical_state["contract_ready"] and ownership_state["ready"]):
        status = "review_only"
    elif not (impact_state["ready"] and verification_state["automatic_ready"] and verification_state["manual_ready"]):
        status = "review_only"
    else:
        status = "ready_for_local_change"

    checks = _build_checks(
        source_missing=source_missing,
        calibration_missing=calibration_missing,
        acceptance_missing=acceptance_missing,
        malformed=malformed,
        unsupported_reasons=unsupported_reasons,
        business_unresolved=business_unresolved,
        technical_state=technical_state,
        ownership_state=ownership_state,
        impact_state=impact_state,
        verification_state=verification_state,
        single_pass=single_pass,
        provider_instruction_refs=provider_instruction_refs,
        source_conflict=source_conflict,
    )
    business_blockers = ("高风险 HIS 业务口径尚未决策。",) if business_unresolved else ()
    provider_instruction_blockers = ("不可信需求内容需人工核验。",) if provider_instruction_refs else ()
    source_conflict_blockers = ("需求来源规则冲突需人工核验。",) if source_conflict["detected"] else ()
    blockers = _stable_texts(
        [
            *unsupported_reasons,
            *business_blockers,
            *provider_instruction_blockers,
            *source_conflict_blockers,
            *missing_information,
            *technical_state["blockers"],
            *ownership_state["blockers"],
            *impact_state["blockers"],
            *verification_state["blockers"],
        ]
    )
    evidence_refs = _stable_evidence(
        [
            {"domain": name, "source": source}
            for name, source, present in (
                ("source_integrity", "normalized_requirement_evidence", evidence is not None),
                ("reasonableness", "requirement_calibration", calibration is not None),
                ("changeability", "technical_decision", technical is not None),
                ("impact", "change_ownership", ownership is not None),
                ("verification", "acceptance_matrix", acceptance is not None),
            )
            if present
        ] + (
            [{
                "domain": "source_integrity",
                "source": "user_confirmed_local_change_exception",
                "provider_evidence_sha256": str(local_change_evidence_exception.get("provider_evidence_sha256") or ""),
                "scope": "local_implementation_only",
            }]
            if local_change_exception_valid and isinstance(local_change_evidence_exception, Mapping)
            else []
        ) + list(provider_instruction_refs) + list(source_conflict["evidence_refs"])
    )
    return RequirementGovernanceResult(
        schema_version=GOVERNANCE_SCHEMA_VERSION,
        status=status,
        can_modify=status == "ready_for_local_change",
        can_complete_in_single_pass=single_pass,
        risk_level=risk_level,
        checks=checks,
        blockers=blockers,
        missing_information=missing_information,
        unsupported_reasons=unsupported_reasons,
        required_capabilities=required_capabilities,
        evidence_refs=evidence_refs,
    )


def _build_checks(**state: Any) -> tuple[GovernanceCheck, ...]:
    source_problems = _stable_texts([*state["source_missing"], *state["malformed"]])
    completeness_problems = _stable_texts([*state["calibration_missing"], *state["acceptance_missing"], *state["malformed"]])
    technical = state["technical_state"]
    ownership = state["ownership_state"]
    impact = state["impact_state"]
    verification = state["verification_state"]
    return (
        _check(
            "source_integrity",
            source_problems,
            "需求来源已完整归一化。",
            warnings=("untrusted_instruction_detected",) if state["provider_instruction_refs"] else (),
            evidence_refs=(
                {"domain": "source_integrity", "source": "structured_input"},
                *state["provider_instruction_refs"],
            ),
        ),
        _check(
            "reasonableness",
            state["calibration_missing"],
            "目标、规则与默认行为已校准。",
            warnings=("source_conflict",) if state["source_conflict"]["detected"] else (),
            evidence_refs=(
                {"domain": "reasonableness", "source": "structured_input"},
                *state["source_conflict"]["evidence_refs"],
            ),
        ),
        _check("compliance", state["unsupported_reasons"] + (("高风险 HIS 业务口径尚未决策。",) if state["business_unresolved"] else ()), "未发现显式能力或高风险口径阻断。"),
        _check("completeness", completeness_problems, "参数、边界与验收信息完整。"),
        _check("changeability", _stable_texts([*technical["blockers"], *ownership["blockers"]]), "工程路径、契约与归属已闭合。", warning=not technical["can_patch"] or not ownership["ready"]),
        _check("impact", impact["blockers"], "相邻路径与 sibling 影响已识别。", warning=not impact["ready"]),
        _check("verification", verification["blockers"], "自动验证和人工验收均可执行。", warning=not verification["ready"]),
        _check("single_pass_readiness", () if state["single_pass"] else ("一次受控变更闭环证据尚未齐全。",), "单次受控变更条件全部满足。", warning=not state["single_pass"]),
    )


def _check(
    name: str,
    problems: Sequence[str],
    passed: str,
    *,
    warning: bool = False,
    warnings: Sequence[str] = (),
    evidence_refs: Sequence[Mapping[str, Any]] | None = None,
) -> GovernanceCheck:
    issues = _stable_texts(problems)
    warning_codes = _stable_texts(warnings)
    status = "blocked" if issues and not warning else "warning" if issues or warning_codes else "pass"
    return GovernanceCheck(
        name=name,
        status=status,
        summary=passed if not issues and not warning_codes else "需补充或核验该治理域证据。",
        blockers=issues if not warning else (),
        warnings=_stable_texts([*warning_codes, *(issues if warning else ())]),
        evidence_refs=tuple(evidence_refs or ({"domain": name, "source": "structured_input"},)),
    )


def _source_missing(
    *,
    title: str,
    evidence: Mapping[str, Any] | None,
    allow_confirmed_inline_media_gap: bool = False,
) -> tuple[str, ...]:
    issues: list[str] = []
    if not _text(title):
        issues.append("缺少需求标题。")
    if evidence is None:
        issues.append("需求来源证据结构无效。")
        return tuple(issues)
    if not _text(evidence.get("title")):
        issues.append("来源证据缺少标题。")
    if not _text(evidence.get("description_text")):
        issues.append("来源证据缺少正文。")
    if _evidence_read_failed(evidence):
        issues.append("需求来源读取失败。")
    if _evidence_collections_incomplete(evidence) and not allow_confirmed_inline_media_gap:
        issues.append("来源评论或附件证据不完整。")
    return _stable_texts(issues)


def _calibration_missing(calibration: Mapping[str, Any] | None, *, user_instruction: str) -> tuple[str, ...]:
    if calibration is None:
        return ("需求校准结构无效。",)
    issues: list[str] = []
    if not _text(user_instruction):
        issues.append("缺少明确的需求目标。")
    decision = _mapping(calibration.get("decision"))
    if calibration.get("status") != "ready_for_development" or not decision or decision.get("can_enter_development") is not True:
        issues.append("需求目标或规则尚未校准为可开发状态。")
    parameters = calibration.get("resolved_parameters")
    if not _has_complete_parameter(parameters):
        issues.append("缺少已解析的参数和值域默认行为。")
    precedence = calibration.get("default_value_precedence")
    if isinstance(precedence, Mapping) and precedence.get("required") is True and not default_value_precedence_is_resolved(dict(precedence)):
        issues.append("默认值来源优先级尚未完整确认。")
    scope = _mapping(calibration.get("resolved_scope"))
    if scope is None or not _has_explicit_boundary(scope):
        issues.append("需求边界不完整。")
    return _stable_texts(issues)


def _acceptance_missing(acceptance: Mapping[str, Any] | None, *, state: Mapping[str, Any]) -> tuple[str, ...]:
    if acceptance is None:
        return ("验收矩阵结构无效。",)
    issues: list[str] = []
    if not state["requirement_ready"]:
        issues.append("缺少明确的需求验收场景。")
    if not state["manual_ready"]:
        issues.append("缺少明确的人工验收路径。")
    if not state["automatic_ready"]:
        issues.append("缺少可执行的自动验证项。")
    if not state["risk_valid"]:
        issues.append("风险结构或等级无效。")
    if not state["structure_valid"]:
        issues.append("验收矩阵结构无效。")
    return _stable_texts(issues)


def _malformed_inputs(**values: Any) -> tuple[str, ...]:
    messages: list[str] = []
    labels = {
        "evidence": "需求来源证据结构无效。",
        "calibration": "需求校准结构无效。",
        "technical": "技术决策结构无效。",
        "ownership": "变更归属结构无效。",
        "acceptance": "验收矩阵结构无效。",
    }
    for key, message in labels.items():
        if values[key] is None:
            messages.append(message)
    if not values["capabilities_valid"]:
        messages.append("可用能力列表结构无效。")
    ownership = values["ownership"]
    if ownership is not None and not isinstance(ownership.get("rows"), _STRUCTURED_ARRAY_TYPES):
        messages.append("变更归属行结构无效。")
    return _stable_texts(messages)


def _technical_state(technical: Mapping[str, Any] | None) -> dict[str, Any]:
    blockers: list[str] = []
    malformed: list[str] = []
    if technical is None:
        return {"can_patch": False, "paths_ready": False, "contract_ready": False, "blockers": ("技术决策结构无效。",), "malformed": ("技术决策结构无效。",)}
    selected, selected_ready = _validated_selected_projects(technical.get("selected_projects"))
    if not selected_ready:
        malformed.append("已识别项目结构无效。")
    implementation = _mapping(technical.get("implementation_decision"))
    is_multi_service = bool(implementation and implementation.get("change_type") == "multi_service_feature")
    service_graph = _mapping((technical.get("field_provenance") or {}).get("service_graph"))
    candidate_targets = implementation.get("candidate_change_targets") if implementation else None
    multi_service_scope_ready = (
        selected_ready
        and isinstance(service_graph, Mapping)
        and service_graph.get("status") == "evidence_ready"
        and isinstance(candidate_targets, list)
        and bool(candidate_targets)
        and not service_graph.get("unresolved_endpoints")
    )
    paths = technical.get("recommended_allowed_paths")
    paths_ready = (
        multi_service_scope_ready
        if is_multi_service
        else selected_ready and _paths_proven(paths, technical.get("field_provenance"), selected)
    )
    if not paths_ready:
        blockers.append("允许修改路径或已识别项目证据不足。")
    if implementation is None or not isinstance(implementation.get("can_patch"), bool) or not _text_list(implementation.get("blockers")):
        malformed.append("技术实现决策结构无效。")
    can_patch = bool(implementation and implementation.get("can_patch") is True)
    if not can_patch:
        blockers.append("技术决策未允许安全修改。")
    contract = _mapping(technical.get("contract_verification"))
    contract_valid = contract is not None and isinstance(contract.get("required"), bool) and (
        (contract.get("required") is True and contract.get("status") in {"verified", "blocked"})
        or (contract.get("required") is False and contract.get("status") == "not_required")
    )
    if not contract_valid:
        malformed.append("前后端契约结构无效。")
    contract_ready = bool(contract_valid and (contract.get("required") is False or contract.get("status") == "verified"))
    if not contract_ready:
        blockers.append("必需前后端契约尚未核验。")
    if implementation and implementation.get("blockers"):
        blockers.append("技术实现决策存在阻断项。")
    return {"can_patch": can_patch, "paths_ready": paths_ready, "contract_ready": contract_ready, "blockers": _stable_texts(blockers), "malformed": _stable_texts(malformed)}


def _validated_selected_projects(value: Any) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    if not isinstance(value, list) or not value:
        return (), False
    projects: list[Mapping[str, Any]] = []
    names: set[str] = set()
    paths: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return (), False
        name = _text(item.get("name"))
        path = _text(item.get("path"))
        if item.get("exists") is not True or not name or not path or name in names or path in paths:
            return (), False
        names.add(name)
        paths.add(path)
        projects.append(item)
    return tuple(projects), True


def _ownership_state(ownership: Mapping[str, Any] | None) -> dict[str, Any]:
    if ownership is None or not isinstance(ownership.get("rows"), _STRUCTURED_ARRAY_TYPES):
        return {"ready": False, "database_configuration_resolved": False, "blockers": ("变更归属结构无效。",), "malformed": ("变更归属结构无效。",)}
    raw_rows = ownership["rows"]
    rows = {str(item.get("layer")): item for item in raw_rows if isinstance(item, Mapping)}
    missing = [layer for layer in _OWNERSHIP_LAYERS if layer not in rows]
    unresolved = [layer for layer in _OWNERSHIP_LAYERS if layer in rows and rows[layer].get("status") not in _RESOLVED_OWNERSHIP_STATUSES]
    exact_rows = len(raw_rows) == 4 and len(rows) == 4 and set(rows) == set(_OWNERSHIP_LAYERS) and all(isinstance(item, Mapping) for item in raw_rows)
    malformed = () if exact_rows and ownership.get("status") in {"ready", "blocked", "unsupported"} and _text_list(ownership.get("blockers")) else ("变更归属结构无效。",)
    ready = ownership.get("status") == "ready" and not missing and not unresolved and not malformed
    database_configuration_resolved = all(layer in rows and rows[layer].get("status") in _RESOLVED_OWNERSHIP_STATUSES for layer in ("database", "configuration"))
    blockers: list[str] = []
    if missing:
        blockers.append("变更归属未覆盖前端、后端、数据库和配置四层。")
    if unresolved or ownership.get("status") != "ready":
        blockers.append("变更归属尚未准备就绪。")
    if ownership.get("blockers"):
        blockers.append("变更归属存在阻断项。")
    return {"ready": ready, "database_configuration_resolved": database_configuration_resolved, "blockers": _stable_texts(blockers), "malformed": malformed}


def _acceptance_state(acceptance: Mapping[str, Any] | None) -> dict[str, Any]:
    if acceptance is None:
        return {"risk_level": "unknown", "risk_valid": False, "requirement_ready": False, "manual_ready": False, "automatic_ready": False, "structure_valid": False}
    risk = _mapping(acceptance.get("risk"))
    level = _text(risk.get("level")) if risk else ""
    return {
        "risk_level": level if level in _RISK_LEVELS - {"unknown"} else "unknown",
        "risk_valid": risk is not None and level in _RISK_LEVELS - {"unknown"} and ("reasons" not in risk or _text_list(risk["reasons"])),
        "requirement_ready": _acceptance_items_valid(acceptance.get("requirement_acceptance")),
        "manual_ready": _acceptance_items_valid(acceptance.get("manual_acceptance")),
        "automatic_ready": _automatic_items_valid(acceptance.get("auto_verification")),
        "structure_valid": _blockers_valid(acceptance.get("blockers")) and (
            "operations" not in acceptance
            or isinstance(acceptance.get("operations"), _STRUCTURED_ARRAY_TYPES)
            and all(isinstance(item, Mapping) for item in acceptance["operations"])
        ),
    }


def _acceptance_items_valid(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, Mapping) and any(_text(item.get(key)) for key in ("scenario", "path", "statement"))
        for item in value
    )


def _automatic_items_valid(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    executable_count = 0
    for item in value:
        if not isinstance(item, Mapping):
            return False
        if _executable_text(item.get("command")) or _executable_text(item.get("statement")):
            executable_count += 1
            continue
        # High-risk matrices deliberately carry a non-executable manual gate
        # beside the runnable checks. It must not make the whole matrix look
        # malformed; it remains a manual acceptance requirement.
        if item.get("type") == "manual_acceptance":
            continue
        # Evidence/project-profile suggestions are advisory. A repository may
        # emit shell syntax that is useful as a hint but not safe to execute
        # as a single command; it must not invalidate explicit runnable checks.
        if item.get("source") in {"evidence_suggested", "project_profile"} and item.get("explicitly_executable") is False:
            continue
        return False
    return executable_count > 0


def _blockers_valid(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, str) and item.strip():
            continue
        if isinstance(item, Mapping) and any(_text(item.get(key)) for key in ("id", "message", "severity")):
            continue
        return False
    return True


def _executable_text(value: Any) -> bool:
    return bool(_EXECUTABLE_COMMAND.fullmatch(_text(value)))


def _paths_proven(paths: Any, provenance: Any, selected: Any) -> bool:
    if not isinstance(paths, list) or not paths or not all(_safe_relative_path(path) for path in paths):
        return False
    if not isinstance(selected, Sequence) or isinstance(selected, (str, bytes)):
        return False
    project_names = {str(item.get("name")) for item in selected if isinstance(item, Mapping) and item.get("exists") is True}
    evidence = _mapping(provenance)
    entries = evidence.get("evidence") if evidence else None
    if not isinstance(entries, list) or not entries:
        return False
    proven: set[str] = set()
    for item in entries:
        if not isinstance(item, Mapping):
            return False
        project = _text(item.get("project"))
        path = item.get("path")
        if project not in project_names or not _safe_relative_path(path):
            return False
        proven.add(path)
    return all(path in proven for path in paths)


def _text_list(value: Any) -> bool:
    return isinstance(value, _STRUCTURED_ARRAY_TYPES) and all(isinstance(item, str) and item.strip() for item in value)


def _has_unsupported_operation(*inputs: Mapping[str, Any] | None) -> bool:
    for source in inputs:
        if not source:
            continue
        if source.get("status") == "unsupported" or source.get("supported") is False or source.get("operation_supported") is False:
            return True
        for key in ("implementation_decision", "operations", "rows"):
            value = source.get(key)
            items = value if isinstance(value, _STRUCTURED_ARRAY_TYPES) else [value] if isinstance(value, Mapping) else []
            if any(isinstance(item, Mapping) and (item.get("status") == "unsupported" or item.get("supported") is False) for item in items):
                return True
    return False


def _capability_values(*inputs: Mapping[str, Any] | None) -> tuple[tuple[str, ...], bool]:
    values: list[str] = []
    valid = True

    def walk(value: Mapping[str, Any] | None) -> None:
        nonlocal valid
        if value is None:
            valid = False
            return
        for key in _CAPABILITY_LIST_FIELDS:
            if key not in value:
                continue
            items = value[key]
            if not isinstance(items, _STRUCTURED_ARRAY_TYPES):
                valid = False
                continue
            for item in items:
                if not isinstance(item, str) or not _CAPABILITY_NAME.fullmatch(item):
                    valid = False
                else:
                    values.append(item)
        for key in _CAPABILITY_SINGLE_FIELDS:
            if key not in value:
                continue
            item = value[key]
            if not isinstance(item, str) or not _CAPABILITY_NAME.fullmatch(item):
                valid = False
            else:
                values.append(item)
        for key in _CAPABILITY_NESTED_FIELDS:
            if key not in value:
                continue
            nested = value[key]
            if isinstance(nested, Mapping):
                walk(nested)
            elif isinstance(nested, _STRUCTURED_ARRAY_TYPES):
                for item in nested:
                    if not isinstance(item, Mapping):
                        valid = False
                    else:
                        walk(item)
            else:
                valid = False

    for source in inputs:
        walk(source)
    return _stable_texts(values), valid


def _impact_state(acceptance: Mapping[str, Any] | None) -> dict[str, Any]:
    if acceptance is None:
        return {"ready": False, "blockers": ("验收矩阵结构无效。",)}
    if "sibling_impact" not in acceptance:
        return {"ready": True, "blockers": ()}
    sibling = _mapping(acceptance.get("sibling_impact"))
    if sibling is None or not isinstance(sibling.get("required"), bool) or not _text_list(sibling.get("blockers")):
        return {"ready": False, "blockers": ("sibling 项目影响结构无效。",)}
    required = sibling["required"]
    status = sibling.get("status")
    if sibling["blockers"] or (required and status not in {"identified", "verified"}) or (not required and status != "not_required"):
        return {"ready": False, "blockers": ("sibling 项目影响尚未闭合。",)}
    return {"ready": True, "blockers": ()}


def _verification_state(technical: Mapping[str, Any] | None, acceptance: Mapping[str, Any] | None, *, acceptance_state: Mapping[str, Any]) -> dict[str, Any]:
    technical_commands = technical.get("recommended_verify_commands") if technical else None
    implementation = _mapping(technical.get("implementation_decision")) if technical else None
    if implementation and implementation.get("change_type") == "multi_service_feature" and not _nonempty_text_items(technical_commands):
        contract = _mapping(technical.get("multi_service_change_contract"))
        repositories = contract.get("repositories") if contract else None
        discovered = []
        if isinstance(repositories, Mapping):
            for repository in repositories.values():
                if isinstance(repository, Mapping):
                    discovered.extend(repository.get("verify_commands") or [])
        if not discovered and acceptance:
            discovered = [
                item.get("command") or item.get("statement")
                for item in acceptance.get("auto_verification") or []
                if isinstance(item, Mapping)
            ]
        technical_commands = [str(item).strip() for item in discovered if str(item).strip()]
    automatic_ready = _nonempty_text_items(technical_commands) and acceptance_state["automatic_ready"]
    manual_ready = acceptance_state["manual_ready"]
    blockers: list[str] = []
    if not automatic_ready:
        blockers.append("缺少可执行的自动验证命令。")
    if not manual_ready:
        blockers.append("缺少人工验收路径。")
    if acceptance and _has_hard_acceptance_blockers(acceptance.get("blockers")):
        blockers.append("验收矩阵存在阻断项。")
    return {"automatic_ready": automatic_ready, "manual_ready": manual_ready, "ready": automatic_ready and manual_ready, "blockers": _stable_texts(blockers)}


def _has_hard_acceptance_blockers(value: Any) -> bool:
    """Manual gates are review requirements, not malformed engineering input."""
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, str) and item.strip():
            return True
        if isinstance(item, Mapping) and str(item.get("severity") or "blocker").strip() in {"blocker", "hard", "error"}:
            return True
    return False


def _has_unresolved_business_interpretation(
    calibration: Mapping[str, Any] | None,
    acceptance: Mapping[str, Any] | None,
    *,
    source_conflict_resolved: bool = False,
) -> bool:
    if acceptance and acceptance.get("unresolved_business_decision") is True:
        return True
    if not calibration:
        return False
    decision = _mapping(calibration.get("decision"))
    if decision and decision.get("unresolved_business_decision") is True:
        return True
    if _nonempty_items(calibration.get("must_confirm")):
        return True
    warnings = calibration.get("warnings")
    return isinstance(warnings, list) and any(
        isinstance(item, Mapping)
        and (
            item.get("type") == "business_decision_unresolved"
            or (item.get("type") == "source_conflict" and not source_conflict_resolved)
        )
        for item in warnings
    )


def _provider_instruction_refs(evidence: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Return redacted locations only; provider values remain caller-owned evidence."""
    if evidence is None:
        return ()
    refs: list[dict[str, Any]] = []
    seen: set[int] = set()

    def walk(value: Any, location: str, depth: int = 0) -> None:
        if depth > 16:
            return
        if isinstance(value, str):
            if _looks_like_untrusted_instruction(value):
                refs.append(
                    {
                        "code": "untrusted_instruction_detected",
                        "domain": "source_integrity",
                        "location": location,
                        "source": "provider_evidence",
                    }
                )
            return
        if not isinstance(value, (Mapping, list, tuple)):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        if isinstance(value, Mapping):
            for key, item in value.items():
                field = _text(key)
                next_location = f"{location}.{field}" if field in _PROVIDER_LOCATION_FIELDS else f"{location}.evidence"
                walk(item, next_location, depth + 1)
            return
        for index, item in enumerate(value):
            walk(item, f"{location}[{index}]", depth + 1)

    walk(evidence, "provider")
    return _stable_evidence(refs)


def _source_conflict_state(calibration: Mapping[str, Any] | None, *, user_instruction: str) -> dict[str, Any]:
    if calibration is None:
        return {"detected": False, "exact_user_choice": False, "evidence_refs": ()}
    warnings = calibration.get("warnings")
    conflicts = [item for item in warnings if isinstance(item, Mapping) and item.get("type") == "source_conflict"] if isinstance(warnings, list) else []
    if not conflicts:
        return {"detected": False, "exact_user_choice": False, "evidence_refs": ()}

    priority = calibration.get("source_priority")
    selected_user = (
        isinstance(priority, list)
        and bool(priority)
        and isinstance(priority[0], Mapping)
        and priority[0].get("source") == "user_instruction"
        and bool(_text(user_instruction))
    )
    exact_user_choice = selected_user and any(
        item.get("selected_source") == "user_instruction"
        and item.get("resolution") == "exact_user_choice"
        and _is_trusted_exact_user_choice(_text(item.get("selected_rule")), user_instruction)
        for item in conflicts
    )
    refs: list[dict[str, Any]] = [
        {"code": "source_conflict", "domain": "reasonableness", "source": "provider_evidence"}
    ]
    if selected_user:
        refs.append(
            {
                "code": "source_conflict",
                "domain": "reasonableness",
                "selected_source": "user_instruction",
                "source": "user_instruction",
            }
        )
    return {
        "detected": True,
        "exact_user_choice": exact_user_choice,
        "evidence_refs": _stable_evidence(refs),
    }


def _looks_like_untrusted_instruction(value: str) -> bool:
    if any(pattern.search(value) for pattern in _UNTRUSTED_OVERRIDE_PATTERNS):
        return True
    requires_prefix = False
    for part in _PROVIDER_CLAUSE_BOUNDARY.split(value):
        if _PROVIDER_CLAUSE_BOUNDARY.fullmatch(part):
            requires_prefix = bool(_PROVIDER_SOFT_CLAUSE_BOUNDARY.fullmatch(part))
            continue
        clause = part.strip()
        prefix = _PROVIDER_IMPERATIVE_PREFIX.match(clause)
        candidate = clause[prefix.end():] if prefix else clause
        if any(pattern.match(candidate) for pattern in _UNTRUSTED_ACTION_PATTERNS) and (prefix or not requires_prefix):
            return True
    return False


def _is_trusted_exact_user_choice(selected_rule: str, user_instruction: str) -> bool:
    normalized_rule = re.sub(r"\s+", "", selected_rule)
    normalized_user = re.sub(r"\s+", "", _text(user_instruction))
    if normalized_user.startswith("当前规则："):
        normalized_user = normalized_user.removeprefix("当前规则：")
    return bool(
        normalized_rule
        and normalized_rule == normalized_user
        and _TRUSTED_EXACT_RULE_CHOICE.fullmatch(normalized_rule)
    )


def _evidence_read_failed(evidence: Mapping[str, Any]) -> bool:
    warnings = evidence.get("warnings")
    return bool(evidence.get("source_read_failed")) or (
        isinstance(warnings, list)
        and any(isinstance(item, Mapping) and item.get("code") == "source_read_failed" for item in warnings)
    )


def _evidence_collections_incomplete(evidence: Mapping[str, Any]) -> bool:
    for key in ("comments", "attachments"):
        items = evidence.get(key)
        if items is not None and not isinstance(items, list):
            return True
        if isinstance(items, list) and any(
            isinstance(item, Mapping)
            and (item.get("complete") is False or _text(item.get("status")).lower() in _UNSAFE_ATTACHMENT_STATUSES)
            for item in items
        ):
            return True
    return False


def _has_complete_parameter(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, Mapping) or not _text(item.get("name")):
            continue
        allowed = item.get("allowed_values")
        if not isinstance(allowed, Mapping):
            continue
        default = any(str(key) in {"empty", "default", "other"} and _text(item_value) for key, item_value in allowed.items())
        rule = any(str(key) not in {"empty", "default", "other"} and _text(item_value) for key, item_value in allowed.items())
        if default and rule:
            return True
    return False


def _has_explicit_boundary(scope: Mapping[str, Any]) -> bool:
    return bool(_text(scope.get("do"))) and isinstance(scope.get("do_not"), list)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _capability_set(value: Any) -> tuple[set[str], bool]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return set(), False
    if not all(isinstance(item, str) and _CAPABILITY_NAME.fullmatch(item) for item in value):
        return set(), False
    return set(value), True


def _nonempty_items(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, Mapping) or _text(item) for item in value)


def _nonempty_text_items(value: Any) -> bool:
    return isinstance(value, list) and any(_text(item) for item in value)


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or re.match(r"^[A-Za-z]:", value):
        return False
    path = PurePosixPath(value)
    return bool(
        path.parts
        and not path.is_absolute()
        and str(path) == value
        and not any(part in {".", ".."} or part.startswith("~") for part in path.parts)
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _stable_texts(values: Sequence[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _stable_evidence(values: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        item = {str(key): _text(item_value) for key, item_value in value.items() if _text(item_value)}
        if not item:
            continue
        identity = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if identity not in seen:
            seen.add(identity)
            result.append(item)
    return tuple(result)


def _append_markdown_list(lines: list[str], title: str, values: Sequence[str]) -> None:
    if not values:
        return
    lines.extend(["", f"## {title}", "", *[f"- {item}" for item in values]])
