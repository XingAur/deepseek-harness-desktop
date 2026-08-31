"""Evidence-only prerequisite for changing a governed engineering requirement.

The evaluator deliberately consumes existing structured artifacts instead of
asking a model to fill gaps.  A result can say that analysis is incomplete,
but it never turns an unproven business or code assumption into a change
authorization.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from app.conversation_evidence import conversation_fact_terms


UNDERSTANDING_SCHEMA_VERSION = "requirement-understanding.v1"
UNDERSTANDING_CHECK_NAMES = (
    "business_background",
    "usage_scenario",
    "target_and_boundary",
    "project_selection",
    "entry_and_call_chain",
    "conversation_alignment",
    "change_and_impact_scope",
    "verification_baseline",
)
BUSINESS_BACKGROUND_HINTS = ("当前", "现状", "问题", "痛点", "原来", "原因", "为了", "避免", "业务", "效率", "风险")
USAGE_SCENARIO_HINTS = ("用户", "医生", "护士", "收费员", "操作员", "患者", "打开", "进入", "点击", "提交", "查询", "结算", "退费", "排班")


@dataclass(frozen=True)
class UnderstandingCheck:
    name: str
    status: str
    summary: str
    blockers: tuple[str, ...] = ()
    evidence_refs: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RequirementUnderstandingResult:
    schema_version: str
    status: str
    can_modify: bool
    checks: tuple[UnderstandingCheck, ...]
    blockers: tuple[str, ...]
    next_readonly_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        labels = {
            "business_background": "业务背景",
            "usage_scenario": "使用场景",
            "target_and_boundary": "目标与范围边界",
            "project_selection": "目标项目",
            "entry_and_call_chain": "项目入口与调用链",
            "conversation_alignment": "对话确认链路核验",
            "error_chain_closure": "截图错误链路闭环",
            "change_and_impact_scope": "改动与影响范围",
            "verification_baseline": "验证基线",
        }
        lines = [
            "## 改码前理解证据包",
            "",
            f"- 结论：`{self.status}`",
            f"- 是否允许改码：{'是' if self.can_modify else '否'}",
            "",
        ]
        for check in self.checks:
            lines.append(f"- {labels[check.name]}：`{check.status}`；{check.summary}")
            for blocker in check.blockers:
                lines.append(f"  - 缺口：{blocker}")
        if self.next_readonly_actions:
            lines.extend(["", "### 下一步（只读调查）", ""])
            lines.extend(f"- {action}" for action in self.next_readonly_actions)
        return "\n".join(lines)


def build_requirement_understanding(
    *,
    title: str,
    user_instruction: str,
    requirement_evidence: Mapping[str, Any] | None,
    requirement_calibration: Mapping[str, Any] | None,
    technical_decision: Mapping[str, Any] | None,
    change_ownership: Mapping[str, Any] | None,
    acceptance_matrix: Mapping[str, Any] | None,
    conversation_evidence: Mapping[str, Any] | None = None,
    error_chain_closure: Mapping[str, Any] | None = None,
) -> RequirementUnderstandingResult:
    """Return a change authorization only when all key understanding evidence exists."""
    source = _mapping(requirement_evidence)
    calibration = _mapping(requirement_calibration)
    technical = _mapping(technical_decision)
    ownership = _mapping(change_ownership)
    acceptance = _mapping(acceptance_matrix)
    provenance = _mapping(technical.get("field_provenance"))

    source_ref = _source_ref(source)
    source_description = _text(source.get("description_text"))
    source_has_context = bool(_text(source.get("title")) and source_description)
    background_explicit = source_has_context and _contains_any(source_description, BUSINESS_BACKGROUND_HINTS)
    has_user_goal = bool(_text(title) and _text(user_instruction))
    background = _check(
        "business_background",
        background_explicit,
        "已从需求来源正文记录当前问题和背景。",
        "需求来源缺少能说明当前业务问题的标题或正文，不能用模型猜测背景。",
        (source_ref,) if source_ref else (),
    )

    has_scenario = _has_entries(acceptance.get("requirement_acceptance")) and _contains_any(
        source_description,
        USAGE_SCENARIO_HINTS,
    )
    scenario = _check(
        "usage_scenario",
        has_scenario,
        "已记录至少一个用户操作或验收场景。",
        "缺少角色、触发条件或用户操作场景；请先从云效正文、评论、附件或业务方补证。",
        ({"source": "acceptance_matrix"},) if has_scenario else (),
    )

    decision = _mapping(calibration.get("decision"))
    scope = _mapping(calibration.get("resolved_scope"))
    scope_ready = bool(
        calibration.get("status") == "ready_for_development"
        and decision.get("can_enter_development") is True
        and _has_scope_boundary(scope)
    )
    target_boundary = _check(
        "target_and_boundary",
        has_user_goal and scope_ready,
        "目标、范围和保持不变的边界已校准。",
        "需求目标、范围或保持不变的行为尚未明确；只能继续澄清，不能改码。",
        ({"source": "requirement_calibration"},) if scope_ready else (),
    )

    selected_projects = _existing_projects(technical.get("selected_projects"))
    project_selection = _check(
        "project_selection",
        bool(selected_projects),
        "目标项目存在且已被技术决策选中。",
        "没有已存在且被证据支持的目标项目；请先定位实际代码仓库。",
        tuple({"project": item["name"], "path": item["path"]} for item in selected_projects),
    )

    evidence_paths = _proven_paths(provenance.get("evidence"), selected_projects)
    entry_ready = bool(
        evidence_paths
        and (
            provenance.get("target_ui_found") is True
            or _text_list(provenance.get("target_ui_paths"))
            or _service_graph_has_branch(provenance.get("service_graph"))
            or _contract_has_entry(provenance.get("response_contract"))
        )
    )
    entry_chain = _check(
        "entry_and_call_chain",
        entry_ready,
        "目标入口及其本地调用、依赖或服务链路已有源码证据。",
        "缺少项目入口或调用链源码证据；请先定位页面/接口入口和数据或依赖路径。",
        tuple({"source": "field_provenance", "path": path} for path in evidence_paths),
    )

    confirmed_terms = conversation_fact_terms(conversation_evidence)
    technical_text = json.dumps(technical, ensure_ascii=False)
    conversation_ready = not confirmed_terms or all(term in technical_text for term in confirmed_terms)
    conversation = _check(
        "conversation_alignment",
        conversation_ready,
        "用户已确认的调用链术语已在当前项目源码证据中出现。" if confirmed_terms else "本轮没有额外的用户确认调用链约束。",
        "用户已确认的调用链尚未在当前项目源码证据中验证；必须先追到对应入口/方法，不能用猜测替代。",
        tuple({"source": "conversation_evidence", "required_code_term": term} for term in confirmed_terms),
    )

    closure = _mapping(error_chain_closure)
    closure_required = closure.get("required") is True
    closure_ready = not closure_required or closure.get("status") == "closed"
    error_chain = _check(
        "error_chain_closure",
        closure_ready,
        "截图错误文本至外部调用的六段源码链路已闭合。" if closure_required else "本轮不触发截图错误链路闭环门禁。",
        "截图错误链路未完成：必须按截图报错文本、菜单、点击事件、前端接口、后端分支、外部医保调用逐段取到证据。",
        ({"source": "error_chain_closure", "status": _text(closure.get("status"))},) if closure_required else (),
    )

    allowed_paths = _text_list(technical.get("recommended_allowed_paths"))
    paths_ready = bool(allowed_paths and set(allowed_paths).issubset(set(evidence_paths)))
    implementation = _mapping(technical.get("implementation_decision"))
    ownership_ready = _ownership_ready(ownership)
    impact_ready = paths_ready and ownership_ready and implementation.get("can_patch") is True and not _text_list(implementation.get("blockers"))
    impact = _check(
        "change_and_impact_scope",
        impact_ready,
        "允许路径、改动归属和相邻影响已由源码与归属矩阵共同约束。",
        "允许修改路径、影响归属或技术阻断项未闭合；不得据此生成补丁。",
        tuple({"source": "field_provenance", "path": path} for path in allowed_paths if path in evidence_paths),
    )

    commands = _text_list(technical.get("recommended_verify_commands"))
    verification_ready = bool(commands and _has_entries(acceptance.get("auto_verification")) and _has_entries(acceptance.get("manual_acceptance")))
    verification = _check(
        "verification_baseline",
        verification_ready,
        "已有针对性自动验证命令和人工验收入口。",
        "缺少测试基线、可执行验证命令或人工验收路径；先补验证证据再改码。",
        ({"source": "technical_decision", "command": command} for command in commands),
    )

    checks = (background, scenario, target_boundary, project_selection, entry_chain, conversation, error_chain, impact, verification)
    blockers = tuple(blocker for check in checks for blocker in check.blockers)
    project_blocked = any(check.status == "blocked" for check in (project_selection, entry_chain, conversation, error_chain, impact, verification))
    requirement_blocked = any(check.status == "blocked" for check in (background, scenario, target_boundary))
    status = (
        "ready_for_change"
        if not blockers
        else "blocked_needs_requirement_context"
        if requirement_blocked
        else "blocked_needs_project_discovery"
    )
    return RequirementUnderstandingResult(
        schema_version=UNDERSTANDING_SCHEMA_VERSION,
        status=status,
        can_modify=not blockers,
        checks=checks,
        blockers=_unique(blockers),
        next_readonly_actions=_next_actions(
            requirement_blocked=requirement_blocked,
            project_blocked=project_blocked,
            verification_blocked=verification.status == "blocked",
        ),
    )


def _check(name: str, passed: bool, success: str, blocker: str, refs: Sequence[Mapping[str, Any]]) -> UnderstandingCheck:
    return UnderstandingCheck(
        name=name,
        status="pass" if passed else "blocked",
        summary=success if passed else "证据不足，改码门禁保持关闭。",
        blockers=() if passed else (blocker,),
        evidence_refs=tuple({str(key): _text(value) for key, value in item.items() if _text(value)} for item in refs),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains_any(text: str, hints: Sequence[str]) -> bool:
    return any(hint in text for hint in hints)


def _text_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(_unique(_text(item) for item in value if _text(item)))


def _has_entries(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, Mapping) and any(_text(item.get(key)) for key in ("scenario", "path", "statement", "command")) for item in value)


def _has_scope_boundary(scope: Mapping[str, Any]) -> bool:
    return bool(_text_list(scope.get("in_scope")) and _text_list(scope.get("out_of_scope")))


def _existing_projects(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        return ()
    projects = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("exists") is not True:
            continue
        name, path = _text(item.get("name")), _text(item.get("path"))
        if name and path:
            projects.append({"name": name, "path": path})
    return tuple(projects)


def _proven_paths(value: Any, selected_projects: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names = {item["name"] for item in selected_projects}
    return tuple(_unique(
        _text(item.get("path"))
        for item in value
        if isinstance(item, Mapping) and _text(item.get("project")) in names and _text(item.get("path"))
    ))


def _service_graph_has_branch(value: Any) -> bool:
    graph = _mapping(value)
    return isinstance(graph.get("branches"), list) and bool(graph["branches"])


def _contract_has_entry(value: Any) -> bool:
    contract = _mapping(value)
    return bool(_text_list(contract.get("api_endpoint_paths")))


def _ownership_ready(ownership: Mapping[str, Any]) -> bool:
    rows = ownership.get("rows")
    return bool(
        ownership.get("status") == "ready"
        and isinstance(rows, (list, tuple))
        and len(rows) == 4
        and not _text_list(ownership.get("blockers"))
    )


def _source_ref(source: Mapping[str, Any]) -> dict[str, str] | None:
    title = _text(source.get("title"))
    return {"source": "requirement_evidence", "title": title} if title else None


def _unique(values: Sequence[str] | Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _next_actions(*, requirement_blocked: bool, project_blocked: bool, verification_blocked: bool) -> tuple[str, ...]:
    actions: list[str] = []
    if requirement_blocked:
        actions.append("补读云效正文、评论和附件，记录业务背景、使用场景、目标和不改动的边界；不足时向需求方澄清。")
    if project_blocked:
        actions.append("在已选项目中只读定位实际项目入口、调用/数据链路、相邻影响和可修改源码路径。")
    if verification_blocked:
        actions.append("先检查目标项目现有测试基座，确定一条可执行的专项验证命令和人工验收路径。")
    return tuple(actions)
