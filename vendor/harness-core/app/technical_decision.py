from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.change_context_contracts import ChangeContextProjection
from app.demand_discovery import DiscoveryResult, discover_demand
from app.project_context import DEFAULT_EXCLUDE_DIRS, TEXT_EXTENSIONS, safe_relative, unique_keep_order
from app.requirement_calibration import (
    DEFAULT_VALUE_PRECEDENCE_SOURCES,
    default_value_precedence_is_resolved,
    remove_negated_scope_clauses,
)
from app.service_architecture import (
    build_service_architecture_catalog,
    build_right_panel_contract_proposal,
    recommend_right_panel_architecture,
)


DEFAULT_PROJECT_ROOT = "/Users/lym/Desktop/dongFang/dfcode"
MAX_SCAN_FILES_PER_PROJECT = 1800
MAX_FILE_BYTES = 220_000
SERVICE_CONTRACT_HINTS = ("入参", "接口", "请求", "排序", "字段", "服务端", "后端", "BFF", "API")
ROUTE_LOCAL_HINTS = ("菜单参数", "菜单/路由参数", "路由参数")
RETURN_CONTRACT_HINT = re.compile(r"返回(?:字段|数据|值|参数|结果集)")
CONTRACT_IDENTIFIER_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{3,}\b")
CONTRACT_IDENTIFIER_EXCLUDES = {"this", "true", "false", "null", "undefined", "string", "return", "const", "from", "with"}
CONTRACT_ENDPOINT_RE = re.compile(r"\b(?:get|post|query|list|page)[A-Z][A-Za-z0-9_]{3,}\b")
CONTRACT_DECLARATION_RE = re.compile(r"(?:入参|参数)(?:新增)?[^。；\n]{0,240}")
EXPLICIT_UI_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.vue)\b",
    flags=re.IGNORECASE,
)
CONTRACT_EVIDENCE_WINDOW_LINES = 32
HARNESS_CONTEXT_MARKER = "【Harness v"
CONFIRMED_CODE_LOCATOR_MARKER = "【用户已确认的代码定位锚点：只用于只读源码检索，不得被猜测替换】"
PROJECT_SELECTION_SCOPES = {
    "change_required",
    "candidate_change",
    "existing_dependency",
    "contract_check",
    "impact_regression",
    "entry_point",
    "candidate_only",
}
HTTP_ENDPOINT_RE = re.compile(
    r"['\"](?P<endpoint>/?[A-Za-z0-9]+-[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+){1,})['\"]"
)
HTTP_GATEWAY_ASSIGNMENT_RE = re.compile(
    r"(?:const|let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*['\"](?P<prefix>/[A-Za-z0-9]+-[A-Za-z0-9_-]+)['\"]"
)
HTTP_DYNAMIC_ENDPOINT_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\+\s*['\"](?P<suffix>/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)['\"]"
)
IMPORT_SOURCE_RE = re.compile(
    r"(?:import\s+(?:[^;\n]*?\s+from\s+)?|require\s*\(\s*)['\"](?P<source>[^'\"]+)['\"]"
)
ROUTE_COMPONENT_IMPORT_RE = re.compile(r"import\(\s*['\"]@/(?P<path>[^'\"]+)['\"]\s*\)")
DTO_IDENTIFIER_RE = re.compile(r"\bDTO_[A-Za-z0-9_]+\b")
CHANGE_REQUEST_HINTS = ("优化", "新增", "增加", "调整", "改造", "维护", "修改")
MULTI_FEATURE_HINTS = (
    "一个页面", "系统包括", "功能按钮", "批量上传", "批量审核", "同步医保等级",
    "标签页", "数据表格", "可编辑字段", "历史记录", "分页功能", "分类导航",
    "目录查询", "行内操作", "字段定义", "合同外", "hetongbz", "自费状态",
    "医嘱处理_启用合同外选择", "护士记账", "医技记账", "手术室记账", "结算规则",
)
MULTI_SERVICE_BOUNDARY_HINTS = (
    "服务边界",
    "调用关系",
    "跨服务",
    "前端、BFF",
    "BFF、业务微服务",
    "业务微服务、底层服务",
    "gy_shoufeixm",
    "YB_XIANGMUZDY",
    "多条对照",
    "多行展示",
    "两条逻辑记录",
    "聚合为一个项目",
    "关联医保对照表",
)
BEHAVIOR_CHANGE_ERROR_HINTS = (
    "报错", "失败", "错误", "不能", "无法", "异常", "禁止", "提示",
)
BEHAVIOR_CHANGE_FLOW_HINTS = (
    "调用", "直接", "退费", "结算", "登记", "点击", "按钮", "申请",
    "流程", "分支", "行为", "不再",
)
FILTER_INTENT_HINTS = (
    "筛选", "过滤条件", "查询条件", "筛选条件", "筛选项", "列表过滤",
)
MAX_FRONTEND_PROJECTS = 4
MAX_FRONTEND_ENTRY_MATCHES = 12
MAX_FRONTEND_DEPENDENCY_FILES = 320
API_SOURCE_EXTENSIONS = {".java", ".kt", ".groovy", ".graphqls", ".proto", ".ts", ".js"}
DEFAULT_VALUE_SOURCE_CODE_PATTERNS = {
    "common_form_setting": re.compile(r"(?:tongYongBiaoDan|通用表单|commonForm|formConfig|biaoDan(?:PeiZhi|SheZhi))", re.IGNORECASE),
    "parameter_setting": re.compile(r"(?:getCanShu|getParam(?:eter)?|canShu|参数|parameter)", re.IGNORECASE),
    "page_hardcoded_default": re.compile(r"(?:page(?:Hardcoded)?Default|hardcodedDefault|hardcode|默认值)\s*(?:[:=]|\|\|)", re.IGNORECASE),
    "no_default": re.compile(r"(?:return\s+(?:undefined|null)|noDefault|no_default|无默认值|没有默认值)", re.IGNORECASE),
}


@dataclass
class TechnicalDecisionResult:
    version: str = "0.8.8"
    project_root: str = DEFAULT_PROJECT_ROOT
    selected_projects: list[dict] = field(default_factory=list)
    field_provenance: dict = field(default_factory=dict)
    contract_verification: dict = field(default_factory=dict)
    implementation_decision: dict = field(default_factory=dict)
    recommended_allowed_paths: list[str] = field(default_factory=list)
    recommended_verify_commands: list[str] = field(default_factory=list)
    verification_plan: dict = field(default_factory=dict)
    multi_service_change_contract: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)

    @property
    def primary_project_path(self) -> str:
        for item in self.selected_projects:
            if item.get("role") == "frontend" and item.get("exists"):
                return str(item.get("path") or "")
        for item in self.selected_projects:
            if item.get("exists"):
                return str(item.get("path") or "")
        return ""

    @property
    def can_patch(self) -> bool:
        return bool((self.implementation_decision or {}).get("can_patch"))

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        decision = self.implementation_decision or {}
        provenance = self.field_provenance or {}
        lines = [
            "## v0.8.8 技术自治决策",
            "",
            f"- 项目根：`{self.project_root}`",
            f"- 结论：{decision.get('summary') or '-'}",
            f"- 是否允许自动 patch：{'是' if decision.get('can_patch') else '否'}",
            f"- 判断类型：{decision.get('change_type') or '-'}",
            f"- 主项目：`{self.primary_project_path or '-'}`",
            f"- 推荐允许路径：{', '.join(self.recommended_allowed_paths) if self.recommended_allowed_paths else '-'}",
            f"- 推荐验证命令：{', '.join(self.recommended_verify_commands) if self.recommended_verify_commands else '-'}",
            f"- 验证配置：{(self.verification_plan or {}).get('active_profile') or '-'}",
            "",
            "### 项目选择",
            "",
        ]
        if not self.selected_projects:
            lines.append("- 未选择到项目。")
        for item in self.selected_projects:
            lines.append(
                f"- `{item.get('path') or '-'}` [{item.get('role') or '-'}] "
                f"范围={item.get('selection_scope') or 'candidate_only'} "
                f"score={item.get('score', 0)} exists={item.get('exists')}：{'; '.join(item.get('reasons') or []) or '-'}"
            )

        service_graph = provenance.get("service_graph") or {}
        if service_graph:
            lines.extend(["", "### 服务图", ""])
            lines.append(f"- 状态：{service_graph.get('status') or '-'}")
            architecture_catalog = service_graph.get("architecture_catalog") or {}
            if architecture_catalog:
                lines.append(
                    f"- 架构证据：`{architecture_catalog.get('schema_version') or '-'}`；"
                    f"节点 {architecture_catalog.get('node_count', len(architecture_catalog.get('nodes') or []))} 个；"
                    f"依赖边 {architecture_catalog.get('edge_count', len(architecture_catalog.get('edges') or []))} 条。"
                )
            architecture_findings = [
                item for item in service_graph.get("boundary_findings") or []
                if isinstance(item, dict) and item.get("architecture_decision")
            ]
            for finding in architecture_findings:
                lines.append(
                    f"- 架构判断：`{finding.get('architecture_decision')}`；"
                    f"推荐 `{finding.get('recommended_option_id') or '-'}`。"
                )
            for branch in service_graph.get("branches") or []:
                lines.append(
                    f"- `{branch.get('source_project')}` -- `{branch.get('endpoint')}` --> "
                    f"`{branch.get('target_project')}` [{branch.get('scope')}]"
                )
            for item in service_graph.get("unresolved_endpoints") or []:
                lines.append(f"- 未解析接口：`{item.get('endpoint')}`；{item.get('reason')}")

        candidate_targets = decision.get("candidate_change_targets") or []
        change_plan = decision.get("change_plan") or {}
        if candidate_targets or change_plan:
            lines.extend(["", "### 候选改动目标", ""])
            if change_plan:
                lines.append(
                    f"- 改动计划状态：`{change_plan.get('status') or '-'}`；"
                    f"候选目标 {change_plan.get('target_count', len(candidate_targets))} 个；"
                    f"未解析 {change_plan.get('unresolved_count', 0)} 个。"
                )
                for requirement in change_plan.get("architecture_requirements") or []:
                    lines.append(
                        f"- 架构目标：`{requirement.get('id') or '-'}`；"
                        f"{requirement.get('label') or '-'}；"
                        f"接口契约状态=`{requirement.get('endpoint_contract_status') or '-'}`。"
                    )
                    for surface in requirement.get("change_surfaces") or []:
                        lines.append(f"  - 改动面：{surface}")
                    for api_type, routes in (requirement.get("existing_api_candidates") or {}).items():
                        if routes:
                            lines.append(f"  - 已有 {api_type} API 候选证据：{', '.join(f'`{route}`' for route in routes[:8])}")
                    existing_contracts = requirement.get("existing_api_contracts") or {}
                    if isinstance(existing_contracts, dict):
                        contract_groups = existing_contracts.items()
                    else:
                        contract_groups = [("existing", existing_contracts)]
                    for api_type, contracts in contract_groups:
                        for contract in contracts[:8]:
                            if not isinstance(contract, dict):
                                continue
                            upstream = ", ".join(
                                f"{item.get('api')}.{item.get('method')}"
                                for item in contract.get("upstream_api_calls") or []
                                if isinstance(item, dict)
                            ) or "-"
                            lines.append(
                                f"  - {api_type} 契约证据：`{contract.get('http_method') or '-'} "
                                f"{contract.get('route') or '-'}`；"
                                f"请求={','.join(contract.get('request_types') or []) or '-'}；"
                                f"响应={','.join(contract.get('response_types') or []) or '-'}；"
                                f"上游={upstream}。"
                            )
                    for gap in requirement.get("contract_gap") or []:
                        lines.append(f"  - 契约缺口：{gap}")
                    proposal = requirement.get("contract_proposal") or {}
                    if proposal:
                        lines.append(
                            f"  - 契约提案：状态=`{proposal.get('status') or '-'}`；"
                            f"决策=`{proposal.get('decision') or '-'}`；"
                            f"写回就绪=`{proposal.get('write_ready')}`。"
                        )
                        route = proposal.get("route") or {}
                        lines.append(
                            f"  - 提案路由：方法候选=`{route.get('candidate_http_method') or '-'}`；"
                            f"路径=`{route.get('candidate_path') or '未证明'}`。"
                        )
                        for source_name, source in (proposal.get("source_contracts") or {}).items():
                            if not isinstance(source, dict):
                                continue
                            lines.append(
                                f"  - 提案来源 `{source_name}`：项目=`{source.get('owner_project') or '-'}`；"
                                f"API=`{source.get('public_api') or '-'}`；状态=`{source.get('status') or '-'}`。"
                            )
                        for item in proposal.get("auto_collected_evidence") or []:
                            if not isinstance(item, dict):
                                continue
                            evidence_count = len(item.get("evidence") or [])
                            lines.append(
                                f"  - Harness 自动取证 `{item.get('id') or '-'}`："
                                f"状态=`{item.get('status') or '-'}`；证据数={evidence_count}。"
                            )
                        for item in proposal.get("remaining_evidence_before_worktree") or []:
                            lines.append(f"  - 仍需 Harness 继续验证：{item}")
                        for item in proposal.get("required_evidence_before_worktree") or []:
                            lines.append(f"  - 进入 worktree 前必须补证据：{item}")
            if candidate_targets:
                for target in candidate_targets:
                    source_paths = ", ".join(f"`{path}`" for path in target.get("source_paths") or []) or "-"
                    lines.append(
                        f"- [{target.get('scope')}] `{target.get('source_project')}` {source_paths} "
                        f"-- `{target.get('endpoint')}` --> `{target.get('target_project')}` "
                        f"`{target.get('target_path') or '-'}`"
                    )
            else:
                lines.append("- 当前没有形成可核验的候选改动目标。")

        multi_service_contract = self.multi_service_change_contract or {}
        if multi_service_contract:
            rollback = multi_service_contract.get("rollback") or {}
            lines.extend(["", "### 多项目改动合同", ""])
            lines.append(f"- 状态：`{multi_service_contract.get('status') or '-'}`")
            lines.append(
                f"- 是否允许写回：`{'是' if rollback.get('status') == 'ready' else '否'}`"
            )
            for item in multi_service_contract.get("blockers") or []:
                lines.append(f"- 阻断：{item}")

        lines.extend(["", "### 字段来源", ""])
        lines.append(f"- 目标字段：{provenance.get('target_field') or '-'}")
        lines.append(f"- 字段是否已能证明返回：{provenance.get('field_returned')}")
        lines.append(f"- 目标界面是否定位：{provenance.get('target_ui_found')}")
        response_contract = provenance.get("response_contract") or {}
        if response_contract:
            lines.append(f"- 后端实体是否存在目标字段：{response_contract.get('backend_model_has_target_field')}")
            lines.append(f"- 返回契约是否包含目标字段：{response_contract.get('response_contract_has_target_field')}")
            for title, key in [
                ("接口入口证据", "api_endpoint_paths"),
                ("后端实体字段证据", "backend_model_paths"),
                ("返回契约证据", "response_contract_paths"),
                ("后端返回契约字段证据", "backend_response_contract_field_paths"),
                ("公共 API 字段证据", "public_api_contract_field_paths"),
                ("返回契约缺字段证据", "response_contract_without_field_paths"),
            ]:
                paths = response_contract.get(key) or []
                if paths:
                    lines.append(f"- {title}：{', '.join(f'`{path}`' for path in paths[:8])}")

        default_precedence = provenance.get("default_value_precedence") or {}
        if default_precedence.get("required"):
            lines.extend(["", "### 默认值来源源码取证", ""])
            lines.append(f"- 状态：{default_precedence.get('status') or '-'}")
            for source in default_precedence.get("sources") or []:
                evidence = source.get("evidence") or []
                paths = [
                    f"{item.get('project') or '-'}:{item.get('path') or '-'}"
                    for item in evidence
                    if isinstance(item, dict)
                ]
                lines.append(
                    f"- `{source.get('source') or '-'}`：{source.get('status') or '-'}；"
                    f"证据={', '.join(f'`{path}`' for path in paths[:6]) or '-'}"
                )
            for item in default_precedence.get("blockers") or []:
                lines.append(f"- 待 Harness 继续追踪：{item}")

        contract = self.contract_verification or {}
        lines.extend(["", "### 前后端契约核验", ""])
        lines.append(f"- 是否需要跨层契约核验：{'是' if contract.get('required') else '否'}")
        lines.append(f"- 核验结论：{contract.get('status') or '-'}")
        for layer_name, layer in (contract.get("layers") or {}).items():
            lines.append(f"- {layer_name}：{layer.get('status') or '-'}；{layer.get('summary') or '-'}")
            evidence_paths = layer.get("evidence_paths") or []
            if evidence_paths:
                lines.append(f"  - 证据：{', '.join(f'`{path}`' for path in evidence_paths[:6])}")
        for item in provenance.get("evidence", [])[:16]:
            lines.append(f"- `{item.get('project') or '-'}` / `{item.get('path') or '-'}`：{item.get('reason') or '-'}")
            snippet = str(item.get("snippet") or "").strip()
            if snippet:
                lines.append(f"  - {snippet[:260]}")

        blockers = decision.get("blockers") or []
        lines.extend(["", "### 阻断项", ""])
        if blockers:
            lines.extend(f"- {item}" for item in blockers)
        else:
            lines.append("- 无阻断项。")
        return "\n".join(lines)

    def to_prompt_context(self, *, limit: int = 7000) -> str:
        text = self.to_markdown()
        if len(text) <= limit:
            return text
        return text[: limit // 2] + "\n\n...（技术自治决策过长，已压缩）...\n\n" + text[-limit // 2 :]


@dataclass(frozen=True)
class TechnicalContextDiscovery:
    combined_text: str
    project_root: str
    selected_projects: tuple[dict, ...]
    service_graph: dict
    demand_discovery: DiscoveryResult
    explicit_scope: bool
    explicit_allowed_paths: tuple[str, ...]
    contract_parameters: tuple[str, ...]
    default_value_precedence: dict
    authoritative_code_locators: str


def _ready_analysis_projection(value: ChangeContextProjection | None) -> bool:
    """Accept only a bounded, gate-approved analysis projection."""
    return bool(
        isinstance(value, ChangeContextProjection)
        and value.role == "analysis"
        and value.tier0.get("gate_status") == "ready"
        and value.tier0.get("gate_code") == "CHANGE_CONTEXT_READY"
    )


def discover_technical_context(
    *,
    demand_text: str,
    yunxiao_evidence: dict | None = None,
    requirement_evidence: dict | None = None,
    project_root: str | Path = DEFAULT_PROJECT_ROOT,
    explicit_project_paths: list[str] | None = None,
    explicit_allowed_paths: list[str] | None = None,
    contract_parameters: list[str] | None = None,
    default_value_precedence: dict | None = None,
    authoritative_code_locators: str = "",
) -> TechnicalContextDiscovery:
    """Collect bounded read-only facts without producing implementation authority."""
    combined_text = build_combined_text(
        demand_text=demand_text,
        yunxiao_evidence=yunxiao_evidence,
        requirement_evidence=requirement_evidence,
    )
    root = Path(project_root).expanduser().resolve()
    selected = select_projects(combined_text=combined_text, root=root, explicit_project_paths=explicit_project_paths or [])
    explicit_scope = bool(explicit_project_paths)
    if not explicit_scope:
        selected = discover_frontend_projects(
            combined_text=combined_text,
            root=root,
            selected_projects=selected,
            authoritative_code_locators=authoritative_code_locators,
        )
    graph_text = combined_text
    if authoritative_code_locators.strip():
        graph_text += f"\n\n{CONFIRMED_CODE_LOCATOR_MARKER}\n{authoritative_code_locators}"
    service_graph = build_service_graph(
        combined_text=graph_text,
        root=root,
        selected_projects=selected,
        restrict_to_selected_projects=explicit_scope,
    )
    selected = merge_service_graph_projects(selected_projects=selected, service_graph=service_graph)
    if not explicit_scope:
        selected = prune_unrelated_candidate_projects(
            selected_projects=selected,
            service_graph=service_graph,
            combined_text=combined_text,
        )
    if not service_graph.get("branches") and requires_service_contract(combined_text):
        selected = expand_contract_projects(selected_projects=selected, root=root, combined_text=combined_text)
    demand_discovery = discover_demand(
        demand_text=combined_text,
        selected_projects=selected,
        max_files=MAX_SCAN_FILES_PER_PROJECT,
        max_file_bytes=MAX_FILE_BYTES,
    )
    allowed_paths = tuple(
        unique_keep_order(str(path).strip() for path in (explicit_allowed_paths or []) if str(path).strip())
    )
    return TechnicalContextDiscovery(
        combined_text=combined_text,
        project_root=str(root),
        selected_projects=tuple(dict(item) for item in selected),
        service_graph=dict(service_graph),
        demand_discovery=demand_discovery,
        explicit_scope=explicit_scope,
        explicit_allowed_paths=allowed_paths,
        contract_parameters=tuple(str(item) for item in (contract_parameters or [])),
        default_value_precedence=dict(default_value_precedence or {}),
        authoritative_code_locators=authoritative_code_locators,
    )


def build_technical_decision(
    *,
    demand_text: str,
    yunxiao_evidence: dict | None = None,
    requirement_evidence: dict | None = None,
    project_root: str | Path = DEFAULT_PROJECT_ROOT,
    explicit_project_paths: list[str] | None = None,
    explicit_allowed_paths: list[str] | None = None,
    contract_parameters: list[str] | None = None,
    default_value_precedence: dict | None = None,
    authoritative_code_locators: str = "",
    discovery: TechnicalContextDiscovery | None = None,
    change_context_projection: ChangeContextProjection | None = None,
) -> TechnicalDecisionResult:
    governed = discovery is not None
    technical_context = discovery or discover_technical_context(
        demand_text=demand_text,
        yunxiao_evidence=yunxiao_evidence,
        requirement_evidence=requirement_evidence,
        project_root=project_root,
        explicit_project_paths=explicit_project_paths,
        explicit_allowed_paths=explicit_allowed_paths,
        contract_parameters=contract_parameters,
        default_value_precedence=default_value_precedence,
        authoritative_code_locators=authoritative_code_locators,
    )
    if not isinstance(technical_context, TechnicalContextDiscovery):
        raise ValueError("technical_context_discovery_invalid")
    combined_text = technical_context.combined_text
    root = Path(technical_context.project_root)
    selected = [dict(item) for item in technical_context.selected_projects]
    service_graph = dict(technical_context.service_graph)
    demand_discovery = technical_context.demand_discovery
    explicit_scope = technical_context.explicit_scope
    explicit_allowed_paths = list(technical_context.explicit_allowed_paths)
    contract_parameters = list(technical_context.contract_parameters)
    default_value_precedence = dict(technical_context.default_value_precedence)
    authoritative_code_locators = technical_context.authoritative_code_locators
    if governed and not _ready_analysis_projection(change_context_projection):
        return TechnicalDecisionResult(
            project_root=str(root),
            selected_projects=selected,
            implementation_decision={
                "can_patch": False,
                "change_type": "blocked_by_change_context",
                "summary": "ChangeContextPack 未通过 ready 分析投影门禁。",
                "blockers": ["ChangeContextPack ready analysis projection is required before technical approval."],
            },
            recommended_allowed_paths=[],
            recommended_verify_commands=[],
            artifacts={},
        )
    if explicit_allowed_paths:
        explicit_path_provenance = build_explicit_path_provenance(
            selected_projects=selected,
            allowed_paths=explicit_allowed_paths,
        )
        inferred_target = infer_target_field(combined_text)
        if inferred_target.get("field"):
            # A path allowlist constrains where Harness may patch; it must not
            # replace the business field or masquerade as proof that an API
            # returns that field.  Preserve the independently discovered field
            # provenance and use the allowlist only as a file boundary.
            provenance = build_field_provenance(
                combined_text=combined_text,
                selected_projects=selected,
                discovery=demand_discovery,
                service_graph=service_graph,
                default_value_precedence=default_value_precedence,
                authoritative_code_locators=authoritative_code_locators,
            )
            provenance["missing_allowed_paths"] = list(
                explicit_path_provenance.get("missing_allowed_paths") or []
            )
            existing_allowed_paths = [
                str(item.get("path") or "")
                for item in explicit_path_provenance.get("evidence") or []
                if str(item.get("path") or "")
            ]
            provenance["target_ui_paths"] = unique_keep_order(
                list(provenance.get("target_ui_paths") or []) + existing_allowed_paths
            )
            provenance["target_ui_found"] = bool(provenance["target_ui_paths"])
            provenance["evidence"] = (
                list(explicit_path_provenance.get("evidence") or [])
                + list(provenance.get("evidence") or [])
            )[:30]
        else:
            provenance = explicit_path_provenance
        provenance["service_graph"] = service_graph
        provenance["default_value_precedence"] = build_default_value_precedence_provenance(
            default_value_precedence=default_value_precedence,
            target=infer_target_field(combined_text),
            selected_projects=selected,
            source_scope_paths=explicit_allowed_paths,
        )
        if inferred_target.get("field"):
            implementation = decide_implementation(
                combined_text=combined_text,
                provenance=provenance,
                selected_projects=selected,
            )
            missing_paths = list(provenance.get("missing_allowed_paths") or [])
            if missing_paths:
                implementation["blockers"] = unique_keep_order(
                    list(implementation.get("blockers") or [])
                    + ["显式白名单路径不存在：" + ", ".join(missing_paths)]
                )
                implementation["can_patch"] = False
            implementation["allowed_paths"] = list(explicit_allowed_paths)
            implementation["rules"] = unique_keep_order(
                ["只允许修改用户显式白名单中的现有源码文件。"]
                + list(implementation.get("rules") or [])
            )
        else:
            implementation = decide_explicit_path_implementation(
                provenance=provenance,
                allowed_paths=explicit_allowed_paths,
                selected_projects=selected,
            )
        if provenance["default_value_precedence"].get("required"):
            implementation = decide_default_value_precedence_implementation(
                provenance=provenance,
                selected_projects=selected,
            )
        contract_verification = build_contract_verification(
            combined_text=combined_text,
            selected_projects=selected,
            allowed_paths=explicit_allowed_paths,
            contract_parameters=contract_parameters,
            service_graph=service_graph,
        )
        implementation = apply_contract_gate(implementation=implementation, contract_verification=contract_verification)
        persisted_provenance = compact_field_provenance(provenance)
        recommended_commands = build_recommended_verify_commands(
            selected_projects=selected,
            allowed_paths=explicit_allowed_paths,
        )
        return TechnicalDecisionResult(
            project_root=str(root),
            selected_projects=selected,
            field_provenance=persisted_provenance,
            contract_verification=contract_verification,
            implementation_decision=implementation,
            recommended_allowed_paths=explicit_allowed_paths,
            recommended_verify_commands=recommended_commands,
            verification_plan=build_verification_plan(recommended_commands),
            artifacts={
                "project_selection_markdown": project_selection_to_markdown(selected, project_root=str(root)),
                "field_provenance_markdown": field_provenance_to_markdown(persisted_provenance),
                "contract_verification_markdown": contract_verification_to_markdown(contract_verification),
                "implementation_decision_markdown": implementation_decision_to_markdown(implementation),
                "service_graph_markdown": service_graph_to_markdown(persisted_provenance.get("service_graph") or {}),
            },
        )
    provenance = build_field_provenance(
        combined_text=combined_text,
        selected_projects=selected,
        discovery=demand_discovery,
        service_graph=service_graph,
        default_value_precedence=default_value_precedence,
        authoritative_code_locators=authoritative_code_locators,
    )
    provenance["service_graph"] = service_graph
    implementation = decide_implementation(combined_text=combined_text, provenance=provenance, selected_projects=selected)
    recommended_paths = implementation.get("allowed_paths") or []
    contract_verification = build_contract_verification(
        combined_text=combined_text,
        selected_projects=selected,
        allowed_paths=recommended_paths,
        contract_parameters=contract_parameters,
        service_graph=service_graph,
    )
    implementation = apply_contract_gate(implementation=implementation, contract_verification=contract_verification)
    recommended_commands = build_recommended_verify_commands(selected_projects=selected, allowed_paths=recommended_paths)
    persisted_provenance = compact_field_provenance(provenance)
    return TechnicalDecisionResult(
        project_root=str(root),
        selected_projects=selected,
        field_provenance=persisted_provenance,
        contract_verification=contract_verification,
        implementation_decision=implementation,
        recommended_allowed_paths=recommended_paths,
        recommended_verify_commands=recommended_commands,
        verification_plan=build_verification_plan(recommended_commands),
        artifacts={
            "project_selection_markdown": project_selection_to_markdown(selected, project_root=str(root)),
            "field_provenance_markdown": field_provenance_to_markdown(persisted_provenance),
            "contract_verification_markdown": contract_verification_to_markdown(contract_verification),
            "implementation_decision_markdown": implementation_decision_to_markdown(implementation),
            "service_graph_markdown": service_graph_to_markdown(persisted_provenance.get("service_graph") or {}),
        },
    )


def compact_field_provenance(provenance: dict) -> dict:
    """Persist authoritative evidence without serializing broad scan internals.

    Full architecture and discovery graphs are useful while deciding, but they
    are diagnostic indexes rather than the final change contract.  Persist a
    content-addressed summary plus only nodes/edges tied to the chosen field or
    target paths so each artifact remains independently auditable.
    """
    result = dict(provenance)
    result["service_graph"] = compact_service_graph(provenance.get("service_graph") or {})
    if provenance.get("field_kind") == "explicit_display_field":
        result["evidence_graph"] = compact_evidence_graph(
            provenance.get("evidence_graph") or {},
            target_field=str(provenance.get("target_field") or ""),
            evidence_paths=unique_keep_order(
                list(provenance.get("target_ui_paths") or [])
                + list(provenance.get("field_source_paths") or [])
            ),
        )
    return result


def compact_service_graph(service_graph: dict) -> dict:
    result = {key: value for key, value in service_graph.items() if key != "architecture_catalog"}
    catalog = service_graph.get("architecture_catalog") or {}
    if catalog:
        encoded = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        result["architecture_catalog"] = {
            "schema_version": catalog.get("schema_version"),
            "status": catalog.get("status"),
            "node_count": len(catalog.get("nodes") or []),
            "edge_count": len(catalog.get("edges") or []),
            "dependency_finding_count": len(catalog.get("dependency_findings") or []),
            "project_names": unique_keep_order(
                str(item.get("project") or "")
                for item in catalog.get("nodes") or []
                if str(item.get("project") or "")
            ),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "source_size_bytes": len(encoded),
            "persistence": "content_addressed_summary",
        }
    return result


def compact_evidence_graph(graph: dict, *, target_field: str, evidence_paths: list[str]) -> dict:
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    normalized_paths = {
        str(path).split(":", 1)[-1]
        for path in evidence_paths
        if str(path).strip()
    }
    retained_nodes = [
        node for node in nodes
        if (
            (target_field and target_field in (node.get("identifiers") or []))
            or str(node.get("path") or "") in normalized_paths
        )
    ]
    retained_qualified_paths = {
        f"{node.get('project')}:{node.get('path')}" for node in retained_nodes
    }
    retained_edges = [
        edge for edge in edges
        if (
            str(edge.get("identifier") or "") == target_field
            and str(edge.get("source_path") or "") in retained_qualified_paths
            and str(edge.get("target_path") or "") in retained_qualified_paths
        )
    ]
    return {
        "nodes": retained_nodes[:40],
        "edges": retained_edges[:120],
        "summary": {
            "source_node_count": len(nodes),
            "source_edge_count": len(edges),
            "retained_node_count": min(len(retained_nodes), 40),
            "retained_edge_count": min(len(retained_edges), 120),
            "scope": "target_field_and_authoritative_paths",
        },
    }


def build_explicit_path_provenance(*, selected_projects: list[dict], allowed_paths: list[str]) -> dict:
    evidence: list[dict] = []
    missing_paths: list[str] = []
    for relative_path in allowed_paths:
        found = False
        for project in selected_projects:
            project_path = Path(str(project.get("path") or ""))
            candidate = project_path / relative_path
            if not candidate.is_file():
                continue
            found = True
            evidence.append(
                {
                    "project": project_path.name,
                    "kind": "explicit_allowlisted_source",
                    "path": relative_path,
                    "reason": "用户显式白名单路径存在，作为受控改码的工程证据。",
                    "snippet": "",
                    "score": 100,
                }
            )
        if not found:
            missing_paths.append(relative_path)
    return {
        "target_field": "显式白名单源码",
        "aliases": [],
        "ui_terms": [],
        "field_returned": True,
        "target_ui_found": bool(evidence),
        "target_ui_paths": [item["path"] for item in evidence],
        "field_source_paths": [item["path"] for item in evidence],
        "response_contract": {},
        "evidence": evidence,
        "missing_allowed_paths": missing_paths,
    }


def decide_explicit_path_implementation(*, provenance: dict, allowed_paths: list[str], selected_projects: list[dict]) -> dict:
    blockers: list[str] = []
    missing_paths = list(provenance.get("missing_allowed_paths") or [])
    if missing_paths:
        blockers.append("显式白名单路径不存在：" + ", ".join(missing_paths))
    if not selected_projects:
        blockers.append("未选择到可用业务项目。")
    if not provenance.get("target_ui_found"):
        blockers.append("显式白名单未能对应到现有源码文件，不能进入 worktree。")
    return {
        "can_patch": not blockers,
        "change_type": "explicit_allowlisted_source",
        "summary": (
            "用户显式白名单路径已在项目中验证存在，可结合需求契约和专项验证进入受控 worktree。"
            if not blockers
            else "显式白名单工程证据不完整，Harness 已阻断自动 patch。"
        ),
        "allowed_paths": allowed_paths,
        "blockers": blockers,
        "rules": [
            "只允许修改用户显式白名单中的现有源码文件。",
            "专项验证、独立 diff 审查和人工业务验收仍是放行前置条件。",
        ],
    }


def build_combined_text(
    *,
    demand_text: str,
    yunxiao_evidence: dict | None = None,
    requirement_evidence: dict | None = None,
) -> str:
    parts = [remove_generated_analysis_appendices(demand_text)]
    for evidence in (yunxiao_evidence, requirement_evidence):
        if not evidence:
            continue
        parts.append(str(evidence.get("title") or ""))
        parts.append(str(evidence.get("description_text") or ""))
        parts.append(str(evidence.get("clean_text") or ""))
        parts.append(str(evidence.get("text_excerpt") or ""))
        parts.append(json.dumps(evidence.get("work_item") or {}, ensure_ascii=False))
        for comment in evidence.get("comments") or []:
            if isinstance(comment, dict):
                parts.append(str(comment.get("content") or comment.get("text") or ""))
            else:
                parts.append(str(comment or ""))
        # v2 provider evidence keeps the normalized requirement under
        # work_items[]. The top-level envelope intentionally contains only
        # lineage/gate metadata, so omitting this list silently discards the
        # actual Yunxiao description and comments during technical routing.
        for work_item in evidence.get("work_items") or []:
            if not isinstance(work_item, dict):
                parts.append(str(work_item or ""))
                continue
            parts.append(str(work_item.get("title") or ""))
            for key in ("description", "description_text", "clean_text", "text_excerpt"):
                value = work_item.get(key)
                if value:
                    parts.append(str(value))
            parts.append(json.dumps(work_item.get("work_item") or {}, ensure_ascii=False))
            for comment in work_item.get("comments") or []:
                if isinstance(comment, dict):
                    parts.append(str(comment.get("content") or comment.get("text") or ""))
                else:
                    parts.append(str(comment or ""))
    return "\n".join(part for part in parts if part)


def remove_generated_analysis_appendices(text: str) -> str:
    """Remove prior Harness exports accidentally pasted back into a demand.

    The source requirement remains authoritative.  Generated normalization and
    decision appendices are derived artifacts and must not become fresh field,
    service or risk evidence on the next run.
    """
    cleaned = str(text or "")
    markers = ("【需求来源归一化证据】", HARNESS_CONTEXT_MARKER)
    positions = [cleaned.find(marker) for marker in markers if cleaned.find(marker) >= 0]
    if positions:
        cleaned = cleaned[: min(positions)]
    return cleaned.rstrip()


def is_broad_feature_requirement(text: str) -> bool:
    """Detect feature bundles that must not be reduced to one discovered field."""
    clean_text = remove_generated_harness_context(text)
    hit_count = sum(1 for hint in MULTI_FEATURE_HINTS if hint in clean_text)
    boundary_hit_count = sum(1 for hint in MULTI_SERVICE_BOUNDARY_HINTS if hint in clean_text)
    explicit_service_layers = all(term in clean_text for term in ("前端", "微服务", "底层服务"))
    return (
        hit_count >= 3
        or ("一个页面" in clean_text and hit_count >= 2)
        or boundary_hit_count >= 2
        or (explicit_service_layers and boundary_hit_count >= 1)
    )


def has_narrow_filter_intent(text: str) -> bool:
    """Only promote a discovered field when the request explicitly asks for filtering.

    A source graph can always find an unrelated UI/store field in a large HIS
    module. Promotion is therefore limited to requirements whose business text
    explicitly describes a filter/query condition; feature bundles must remain
    multi-service decisions until their service graph is closed.
    """
    clean_text = remove_generated_harness_context(text)
    return any(hint in clean_text for hint in FILTER_INTENT_HINTS)


def is_behavior_change_requirement(text: str) -> bool:
    """Classify a flow/error correction separately from display-field work."""
    clean_text = extract_change_intent_text(remove_generated_harness_context(text))
    if infer_target_field(clean_text).get("field"):
        return False
    has_error = any(hint in clean_text for hint in BEHAVIOR_CHANGE_ERROR_HINTS)
    has_flow = any(hint in clean_text for hint in BEHAVIOR_CHANGE_FLOW_HINTS)
    explicit_branch_change = any(
        phrase in clean_text
        for phrase in ("不再调用", "不要再调用", "直接进行", "改为直接", "应识别为全退")
    )
    return (has_error and has_flow) or explicit_branch_change


def extract_authoritative_code_locator_terms(text: str) -> tuple[str, ...]:
    """Extract only code-shaped terms from a deliberately selected locator block."""
    clean_text = str(text or "")
    if CONFIRMED_CODE_LOCATOR_MARKER in clean_text:
        clean_text = clean_text.split(CONFIRMED_CODE_LOCATOR_MARKER, 1)[1]
    path_terms = re.findall(
        r"(?<![A-Za-z0-9_])/?(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+(?![A-Za-z0-9_])",
        clean_text,
    )
    path_segments = {
        segment
        for path in path_terms
        for segment in path.strip("/").split("/")
        if segment
    }
    identifier_terms = re.findall(
        r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]*[A-Z][A-Za-z0-9_]*(?![A-Za-z0-9_])",
        clean_text,
    )
    standalone_identifiers = {
        line.strip()
        for line in clean_text.splitlines()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*[A-Z][A-Za-z0-9_]*", line.strip())
    }
    terms: list[str] = []
    for term in (*path_terms, *identifier_terms):
        # A complete path is authoritative.  Its generic middle segments
        # (for example ``shouFei``) are not independent user-confirmed
        # anchors and must not select sibling repositories.
        if term in path_segments and term not in path_terms and term not in standalone_identifiers:
            continue
        if term not in terms:
            terms.append(term)
        if "/" in term and not term.startswith("/"):
            normalized = "/" + term
            if normalized not in terms:
                terms.append(normalized)
    return tuple(sorted(terms, key=lambda item: (-len(item), item)))


def select_specific_code_locator_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    """Drop shorter method prefixes when a more specific confirmed locator exists."""
    paths = [term for term in terms if "/" in term]
    identifiers = [term for term in terms if "/" not in term]
    specific_identifiers = [
        term
        for term in identifiers
        if not any(
            other != term and len(other) > len(term) and other.startswith(term)
            for other in identifiers
        )
    ]
    return tuple(sorted((*paths, *specific_identifiers), key=lambda item: (-len(item), item)))


def is_connected_frontend_entry_path(*, project_path: Path, relative_path: str) -> bool:
    """Accept UI files connected to an index page, not arbitrary shared helpers."""
    normalized = relative_path.replace("\\", "/")
    if not normalized.startswith(("src/pages/", "src/views/", "src/router/", "src/routes/")):
        return False
    if normalized.startswith(("src/router/", "src/routes/")):
        return True
    if Path(normalized).suffix.lower() != ".vue":
        return False
    current = (project_path / normalized).parent
    src_root = project_path / "src"
    while current != src_root and src_root in current.parents:
        if (current / "index.vue").is_file():
            return True
        current = current.parent
    return Path(normalized).name in {"index.vue", "index.jsx", "index.tsx"}


def _source_contains_code_locator(*, text: str, term: str) -> bool:
    if "/" in term:
        return term in text or term.lstrip("/") in text
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", text))


def find_authoritative_code_locator_matches(
    *,
    project_path: Path,
    terms: tuple[str, ...],
    max_files: int = MAX_FRONTEND_DEPENDENCY_FILES,
) -> list[tuple[str, str]]:
    """Find exact user-confirmed identifiers without treating them as business labels."""
    if not terms or not project_path.is_dir():
        return []
    matches: list[tuple[str, str]] = []
    scanned = 0
    src_root = project_path / "src"
    if not src_root.is_dir():
        return []
    for path in sorted(iter_text_files(src_root), key=lambda item: safe_relative(item, project_path)):
        scanned += 1
        if scanned > max_files:
            break
        text = read_source_text(path)
        if text is None:
            continue
        for term in terms:
            if _source_contains_code_locator(text=text, term=term):
                matches.append((term, safe_relative(path, project_path)))
    return sorted(
        unique_keep_order(matches),
        key=lambda item: (-len(item[0]), item[1], item[0]),
    )[:MAX_FRONTEND_ENTRY_MATCHES]


def discover_frontend_projects(
    *,
    combined_text: str,
    root: Path,
    selected_projects: list[dict],
    authoritative_code_locators: str = "",
) -> list[dict]:
    """Find a UI by business labels before guessing an identically named BFF."""
    if not root.is_dir():
        return selected_projects
    business_terms = demand_project_terms(combined_text)
    authoritative_terms = select_specific_code_locator_terms(
        extract_authoritative_code_locator_terms(authoritative_code_locators)
    )
    if not business_terms and not authoritative_terms:
        return selected_projects
    existing = {str(item.get("name") or ""): dict(item) for item in selected_projects}
    has_known_frontend = any(
        item.get("role") == "frontend" and item.get("exists")
        for item in selected_projects
    )
    candidates: list[dict] = []
    for project_path in sorted(root.iterdir(), key=lambda item: item.name):
        if not project_path.is_dir() or not project_path.name.startswith("df-web-"):
            continue
        matches = find_project_term_matches(project_path=project_path, terms=business_terms)
        authoritative_matches = find_authoritative_code_locator_matches(
            project_path=project_path,
            terms=authoritative_terms,
        )
        if not matches and not authoritative_matches:
            continue
        if has_known_frontend and not any(
            len(term) >= 6
            for term, _path in (*matches, *authoritative_matches)
        ):
            continue
        authoritative_page_matches = [
            item
            for item in authoritative_matches
            if is_connected_frontend_entry_path(
                project_path=project_path,
                relative_path=item[1],
            )
        ]
        score = sum(len(item[0]) for item in matches)
        score += sum(100 + len(item[0]) for item in authoritative_page_matches)
        candidate = existing.get(project_path.name) or {
            "path": str(project_path),
            "name": project_path.name,
            "role": "frontend",
            "exists": True,
            "reasons": [],
        }
        candidate["score"] = max(int(candidate.get("score", 0)), 100 + score)
        entry_match_pool = (
            authoritative_page_matches
            if authoritative_page_matches
            else matches
        )
        selected_matches = select_frontend_entry_matches(
            unique_keep_order(entry_match_pool),
            project_path=project_path,
        )
        candidate["entry_matches"] = [
            {"term": term, "path": path}
            for term, path in selected_matches
        ]
        candidate["entry_matches_truncated"] = len(selected_matches) < len(matches)
        # An exact identifier in a shared API helper is not enough to prove
        # that this repository owns the reported menu flow.  It becomes an
        # authoritative frontend only when the exact term is present in a
        # route/page reachable as a task entry.  Shared helpers remain
        # evidence candidates until that connection is proven.
        candidate["authoritative_code_match"] = bool(authoritative_page_matches)
        candidate["authoritative_code_paths"] = [
            path for _term, path in authoritative_page_matches
        ]
        candidate["reasons"] = unique_keep_order(
            list(candidate.get("reasons") or [])
            + ([
                f"命中用户确认的精确代码锚点 {', '.join(term for term, _path in authoritative_page_matches[:3])}，作为首要调用链项目。"
            ] if authoritative_page_matches else [])
            + ([
                "在共享 API/helper 中命中代码锚点，但尚未证明与需求菜单入口连通，仅保留为影响候选。"
            ] if authoritative_matches and not authoritative_page_matches else [])
            + ([
                f"业务标题/页面文本命中 {', '.join(term for term, _path in matches[:3])}，仅作为入口/影响候选。"
            ] if matches else [])
        )
        existing[project_path.name] = candidate
        candidates.append(candidate)
    selected_names = {str(item.get("name") or "") for item in selected_projects}
    enriched_existing = [
        existing[name]
        for name in selected_names
        if name in existing
    ]
    selected = enriched_existing + [
        item for item in sorted(candidates, key=lambda value: (-int(value.get("score", 0)), str(value.get("name") or "")))[:MAX_FRONTEND_PROJECTS]
        if str(item.get("name") or "") not in selected_names
    ]
    authoritative_found = any(item.get("authoritative_code_match") for item in selected)
    for item in selected:
        if item.get("role") == "frontend" and authoritative_found:
            item["selection_basis"] = (
                "authoritative_code_locator"
                if item.get("authoritative_code_match")
                else "business_context_only"
            )
            if not item.get("authoritative_code_match"):
                item["reasons"] = unique_keep_order(
                    list(item.get("reasons") or [])
                    + ["已找到更强的用户确认调用锚点；本项目降级为入口/影响候选，不得自动作为改动目标。"]
                )
    return sorted(selected, key=lambda item: (-int(item.get("score", 0)), role_rank(str(item.get("role") or ""))))


PROJECT_TERM_CONNECTOR_RE = re.compile(
    r"(?:科反馈|反馈|参考(?:老系统|现有系统)?|是否可以(?:考虑)?|可以考虑|把|做在|支持|包括|包含|并且|以及|同时|和|一个|"
    r"不方便|方便|页面|功能|需求|问题描述|优化|新增|增加|调整|改造|修改|完善|实现|用于|根据|按照|进入|点击|选择|保存|查询)"
)
PROJECT_TERM_PREFIX_RE = re.compile(
    r"^(?:医保科|业务方|用户|系统|需求方|请|需要|希望|可以|参考|根据|按照|优化|新增|增加|调整|改造|修改|完善|实现|支持)+"
)
PROJECT_TERM_SUFFIX_RE = re.compile(
    r"(?:功能|需求|页面|列表|条件|项目维护|项目管理|维护|管理|不方便|方便|一个)$"
)
PROJECT_TERM_STOPWORDS = {
    "是否可以", "可以考虑", "参考老系统", "参考现有系统", "项目维护", "项目管理",
    "一个页面", "业务功能", "需求描述", "问题描述", "查询条件", "全部功能",
}
PROJECT_TERM_HINTS = {
    "医保", "审批", "对照", "目录", "批量", "上传", "审核", "同步", "等级", "编码",
    "匹配", "自费", "就诊", "患者", "病人", "收费", "结算", "退费", "发票", "处方",
    "药品", "诊疗", "材料", "申请", "科室", "医院", "费用", "状态", "类型", "记录",
    "历史", "明细", "导出", "打印", "权限", "字典", "配置", "规则", "服务", "接口",
}


def demand_project_terms(text: str) -> tuple[str, ...]:
    """Extract bounded business labels instead of retaining whole requirement sentences.

    Requirements frequently concatenate a reporter's context, action words and the
    actual screen label without punctuation. Splitting on common connectors and
    ranking short business n-grams keeps discovery useful for new modules while
    preventing generic phrases from winning project selection.
    """
    clean_text = remove_generated_harness_context(text)
    scored: dict[str, int] = {}

    def add(candidate: str, score: int) -> None:
        candidate = re.sub(r"\s+", "", candidate).strip("，。；：、,.;:()（）[]【】")
        if len(candidate) < 3 or len(candidate) > 16:
            return
        if candidate in PROJECT_TERM_STOPWORDS:
            return
        if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9_-]+", candidate):
            return
        hint_score = sum(1 for hint in PROJECT_TERM_HINTS if hint in candidate)
        scored[candidate] = max(scored.get(candidate, 0), score + hint_score * 8 + len(candidate))

    for raw_clause in re.split(r"[，。；：、,.;:\n]+", clean_text):
        clauses = [part for part in PROJECT_TERM_CONNECTOR_RE.split(raw_clause) if part]
        for clause in clauses:
            run = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9]+|[^\u4e00-\u9fffA-Za-z0-9]+$", "", clause)
            run = PROJECT_TERM_PREFIX_RE.sub("", run)
            raw_run = run
            run = PROJECT_TERM_SUFFIX_RE.sub("", run)
            if len(run) < 3:
                continue
            add(raw_run, 44)
            add(run, 40)
            # Keep exact labels hidden inside an unpunctuated clause. The hint
            # score makes domain-bearing n-grams outrank sentence fragments.
            max_length = min(10, len(run))
            for length in range(max_length, 3, -1):
                for start in range(0, len(run) - length + 1):
                    candidate = run[start : start + length]
                    if any(hint in candidate for hint in PROJECT_TERM_HINTS) or length >= 5:
                        add(candidate, 22)

    ranked = sorted(scored, key=lambda term: (-scored[term], -len(term), term))
    return tuple(ranked[:64])


def find_project_term_matches(*, project_path: Path, terms: tuple[str, ...], max_files: int = MAX_FRONTEND_DEPENDENCY_FILES) -> list[tuple[str, str]]:
    """Search bounded UI entry directories and rank all useful matches.

    Do not stop at the first generic hit: large HIS frontends often contain a
    similarly named page before the real business module.
    """
    matches: list[tuple[str, str]] = []
    seen_paths: set[Path] = set()
    scanned = 0
    entry_roots = [
        project_path / "src/router",
        project_path / "src/routes",
        project_path / "src/views",
        project_path / "src/pages",
    ]
    for entry_root in entry_roots:
        if not entry_root.is_dir():
            continue
        for path in sorted(iter_text_files(entry_root), key=lambda item: safe_relative(item, project_path)):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            scanned += 1
            if scanned > max_files:
                break
            text = read_source_text(path)
            if text is None:
                continue
            matched_terms = [term for term in terms if term in text]
            for matched in sorted(matched_terms, key=lambda term: (-len(term), term))[:4]:
                matches.append((matched, safe_relative(path, project_path)))
        if scanned > max_files:
            break
    unique_matches = unique_keep_order(matches)
    matched_terms = {term for term, _path in unique_matches}
    strongest_terms = {
        term
        for term in matched_terms
        if not any(
            len(other) > len(term) and other.startswith(term)
            for other in matched_terms
        )
    }
    return sorted(
        [item for item in unique_matches if item[0] in strongest_terms],
        key=lambda item: (-len(item[0]), -sum(hint in item[0] for hint in PROJECT_TERM_HINTS), item[1]),
    )[:MAX_FRONTEND_ENTRY_MATCHES]


def select_frontend_entry_matches(
    matches: list[tuple[str, str]],
    *,
    project_path: Path | None = None,
) -> list[tuple[str, str]]:
    """Keep route/page files as graph roots; dependencies are import-traversed."""
    page_entries = [
        item
        for item in matches
        if item[1].replace("\\", "/").startswith(("src/router/", "src/routes/"))
        or Path(item[1]).name in {"index.vue", "index.jsx", "index.tsx", "index.html"}
    ]
    if project_path is not None and page_entries:
        connected_entries = []
        for term, relative_path in page_entries:
            text = read_source_text(project_path / relative_path)
            if text and (HTTP_ENDPOINT_RE.search(text) or IMPORT_SOURCE_RE.search(text)):
                connected_entries.append((term, relative_path))
        if connected_entries:
            page_entries = connected_entries
    if page_entries:
        return page_entries[:MAX_FRONTEND_ENTRY_MATCHES]
    return matches[:MAX_FRONTEND_ENTRY_MATCHES]


def build_service_graph(
    *,
    combined_text: str,
    root: Path,
    selected_projects: list[dict],
    restrict_to_selected_projects: bool,
) -> dict:
    """Resolve each frontend HTTP prefix independently; never force a single BFF path."""
    frontend_projects = [item for item in selected_projects if item.get("role") == "frontend" and item.get("exists")]
    allowed_names = {str(item.get("name") or "") for item in selected_projects}
    nodes: dict[str, dict] = {}
    branches: list[dict] = []
    unresolved: list[dict] = []
    controller_sources: dict[str, list[tuple[str, str]]] = {}
    public_contracts: dict[str, list[str]] = {}
    public_api_index: dict[str, list[str]] | None = None
    clean_text = remove_generated_harness_context(combined_text)
    change_requested = any(term in clean_text for term in CHANGE_REQUEST_HINTS)
    broad_architecture_scan = is_broad_feature_requirement(clean_text) or any(
        term in clean_text for term in MULTI_SERVICE_BOUNDARY_HINTS
    )
    architecture_catalog = build_service_architecture_catalog(
        root=root,
        selected_projects=selected_projects,
        include_workspace_projects=broad_architecture_scan,
    )
    target = infer_target_field(clean_text)
    authoritative_locator_terms = select_specific_code_locator_terms(
        extract_authoritative_code_locator_terms(
            clean_text if CONFIRMED_CODE_LOCATOR_MARKER in clean_text else ""
        )
    )
    has_authoritative_frontend = bool(authoritative_locator_terms) and any(
        item.get("role") == "frontend" and item.get("authoritative_code_match")
        for item in selected_projects
    )

    for frontend in frontend_projects:
        frontend_path = Path(str(frontend.get("path") or ""))
        if not frontend_path.is_dir():
            continue
        frontend_name = str(frontend.get("name") or frontend_path.name)
        entry_paths = unique_keep_order(
            f"{frontend_name}:{item.get('path')}"
            for item in frontend.get("entry_matches") or []
            if str(item.get("path") or "").strip()
        )
        endpoint_sources = find_frontend_endpoints(
            project_path=frontend_path,
            entry_matches=list(frontend.get("entry_matches") or []),
        )
        if authoritative_locator_terms:
            authoritative_endpoint_sources = [
                item
                for item in endpoint_sources
                if _endpoint_matches_authoritative_locator(
                    endpoint=item[0],
                    terms=authoritative_locator_terms,
                )
            ]
            # A confirmed method/path is an exact branch constraint.  If the
            # selected page's import closure cannot expose that endpoint, keep
            # the graph incomplete instead of silently falling back to a
            # neighboring request from the same page.
            endpoint_sources = authoritative_endpoint_sources
        if target.get("kind") == "explicit_display_field":
            endpoint_sources = narrow_endpoint_sources_by_demand(
                endpoint_sources=endpoint_sources,
                combined_text=clean_text,
            )
        if not endpoint_sources:
            continue
        frontend_is_authoritative = bool(frontend.get("authoritative_code_match"))
        frontend_change_requested = change_requested and (
            not has_authoritative_frontend or frontend_is_authoritative
        )
        add_service_graph_node(
            nodes,
            project=frontend_name,
            path=frontend_path,
            role="frontend",
            scope="change_required" if frontend_change_requested else "entry_point",
            evidence_paths=[f"{frontend_name}:{path}" for _endpoint, path in endpoint_sources],
            entry_paths=entry_paths,
        )
        for endpoint, source_path in endpoint_sources:
            target_name = resolve_endpoint_target_project(
                endpoint=endpoint,
                architecture_catalog=architecture_catalog,
            )
            if not target_name:
                unresolved.append({"endpoint": endpoint, "source_project": frontend_name, "reason": "接口前缀及 Controller 路由均未能唯一映射到本地服务。"})
                continue
            if restrict_to_selected_projects and target_name not in allowed_names:
                # An explicit project list limits mutation scope, not the
                # evidence universe.  If a sibling repository exists below
                # the declared workspace root, inspect its Controller/API as
                # evidence-only.  Do not force the user to enumerate every
                # backend just to prove the frontend's real call chain.
                target_path = root / target_name
                if not target_path.is_dir():
                    unresolved.append({"endpoint": endpoint, "source_project": frontend_name, "reason": "用户显式限制了项目范围，且本地未找到该服务；无法形成服务端证据。"})
                    continue
                evidence_only_target = True
            else:
                target_path = root / target_name
                evidence_only_target = False
            if target_name not in controller_sources:
                controller_sources[target_name] = load_controller_sources(target_path)
            controller_paths = find_controller_paths_for_endpoint(
                project_path=target_path,
                endpoint=endpoint,
                source_files=controller_sources[target_name],
            )
            if not controller_paths:
                unresolved.append({"endpoint": endpoint, "source_project": frontend_name, "reason": f"未在 {target_name} 找到同一路由的 Controller 证据。"})
                continue
            scope = branch_scope(
                endpoint=endpoint,
                combined_text=clean_text,
                target_name=target_name,
                change_requested=change_requested,
            )
            if (
                target.get("kind") == "explicit_display_field"
                and backend_change_is_explicitly_excluded(clean_text)
            ):
                scope = "contract_check"
            if evidence_only_target and scope in {"change_required", "candidate_change"}:
                # Keep the discovered backend out of the mutation contract;
                # it is still recorded for contract review and can be promoted
                # only after the endpoint/DTO evidence is complete.
                scope = "contract_check"
            branch = {
                "source_project": frontend_name,
                "source_path": f"{frontend_name}:{source_path}",
                "endpoint": endpoint,
                "target_project": target_name,
                "target_path": f"{target_name}:{controller_paths[0]}",
                "entry_paths": entry_paths,
                "scope": scope,
                "controller_verified": True,
            }
            endpoint_contract = find_endpoint_contract(
                endpoint=endpoint,
                target_project=target_name,
                architecture_catalog=architecture_catalog,
            )
            if endpoint_contract:
                branch["endpoint_contract"] = compact_endpoint_contract(endpoint_contract)
            if target.get("field"):
                branch["field_contract"] = build_endpoint_field_contract(
                    target=target,
                    target_project=target_name,
                    target_path=target_path,
                    endpoint_contract=endpoint_contract,
                )
            if branch not in branches:
                branches.append(branch)
            add_service_graph_node(
                nodes,
                project=target_name,
                path=target_path,
                role=infer_project_role(target_name),
                scope=scope,
                evidence_paths=[f"{target_name}:{path}" for path in controller_paths],
                endpoints=[endpoint],
            )
            if target_name.startswith("df-mic-"):
                if public_api_index is None:
                    public_api_index = build_public_api_dto_index(root, service_path=target_path)
                if target_name not in public_contracts:
                    public_contracts[target_name] = find_public_api_contract_paths(
                        root=root,
                        service_path=target_path,
                        controller_paths=controller_paths,
                        dto_index=public_api_index,
                    )
                contract_paths = public_contracts[target_name]
                if contract_paths:
                    api_paths_by_project: dict[str, list[str]] = {}
                    for contract_path in contract_paths:
                        project_name, relative_path = split_public_api_contract_path(contract_path)
                        api_paths_by_project.setdefault(project_name, []).append(relative_path)
                    for project_name, relative_paths in api_paths_by_project.items():
                        add_service_graph_node(
                            nodes,
                            project=project_name,
                            path=root / project_name,
                            role="api",
                            scope="contract_check",
                            evidence_paths=[f"{project_name}:{path}" for path in relative_paths],
                        )

    deduped_branches: dict[tuple[str, str, str], dict] = {}
    for branch in branches:
        key = (
            str(branch.get("source_project") or ""),
            str(branch.get("endpoint") or ""),
            str(branch.get("target_project") or ""),
        )
        existing_branch = deduped_branches.get(key)
        if existing_branch is None:
            existing_branch = dict(branch)
            existing_branch["source_paths"] = [str(branch.get("source_path") or "")]
            deduped_branches[key] = existing_branch
        else:
            source_path = str(branch.get("source_path") or "")
            existing_branch["source_paths"] = unique_keep_order(
                list(existing_branch.get("source_paths") or []) + [source_path]
            )
            existing_branch["entry_paths"] = unique_keep_order(
                list(existing_branch.get("entry_paths") or [])
                + [str(path) for path in (branch.get("entry_paths") or []) if str(path).strip()]
            )
    deduped_unresolved = unique_keep_order(
        (
            str(item.get("source_project") or ""),
            str(item.get("endpoint") or ""),
            str(item.get("reason") or ""),
        )
        for item in unresolved
    )
    unresolved = [
        {"source_project": source_project, "endpoint": endpoint, "reason": reason}
        for source_project, endpoint, reason in deduped_unresolved
    ]
    branches = list(deduped_branches.values())
    boundary_findings = find_data_source_boundary_findings(
        combined_text=clean_text,
        root=root,
        selected_projects=selected_projects,
        architecture_catalog=architecture_catalog,
    )
    boundary_findings.extend(
        find_multi_source_right_panel_findings(
            combined_text=clean_text,
            root=root,
            selected_projects=selected_projects,
            architecture_catalog=architecture_catalog,
        )
    )
    business_rule_findings = find_approval_flag_rule_findings(
        combined_text=clean_text,
        root=root,
        selected_projects=selected_projects,
    )
    return {
        "schema_version": "service-graph.v1",
        "status": "evidence_ready" if branches and not unresolved else ("incomplete" if branches or unresolved else "not_applicable"),
        "nodes": sorted(nodes.values(), key=lambda item: (role_rank(str(item.get("role") or "")), str(item.get("project") or ""))),
        "branches": sorted(branches, key=lambda item: (item["source_project"], item["endpoint"])),
        "unresolved_endpoints": unresolved,
        "boundary_findings": boundary_findings,
        "business_rule_findings": business_rule_findings,
        "architecture_catalog": architecture_catalog,
    }


def find_data_source_boundary_findings(
    *,
    combined_text: str,
    root: Path,
    selected_projects: list[dict],
    architecture_catalog: dict | None = None,
) -> list[dict]:
    """Detect a requirement-owned base table that is read directly by another service.

    A service graph made only from HTTP URLs can look complete while the actual
    implementation bypasses the owning base service through a shared schema.
    This finding is evidence, not an automatic refactor instruction: the
    boundary must be reviewed before a cross-service patch is allowed.
    """
    if not any(term in combined_text for term in ("gy_shoufeixm", "GY_ShouFeiXm", "df-mic-jichufw")):
        return []
    owner_projects = [
        item for item in selected_projects
        if str(item.get("name") or "") == "df-mic-jichufw"
        or "jichufw" in str(item.get("name") or "").lower()
    ]
    owner_evidence: list[str] = []
    for owner in owner_projects:
        project_path = Path(str(owner.get("path") or ""))
        if not project_path.is_dir():
            continue
        for path in iter_text_files(project_path):
            text = read_source_text(path)
            if text is None:
                continue
            if any(token in text for token in ("ShouFeiXmApi", "ShouFeiXmController", "GY_ShouFeiXm", "gy_shoufeixm")):
                owner_evidence.append(f"{owner.get('name')}:{safe_relative(path, project_path)}")
    consumer_evidence: list[str] = []
    sql_table_re = re.compile(
        r"(?:from|join|update|insert\s+into|delete\s+from)\s+(?:[A-Za-z0-9_]+\.)?gy_shoufeixm",
        flags=re.IGNORECASE,
    )
    for project in selected_projects:
        name = str(project.get("name") or "")
        if not name or name == "df-mic-jichufw" or "jichufw" in name.lower():
            continue
        project_path = Path(str(project.get("path") or ""))
        if not project_path.is_dir():
            continue
        for path in iter_text_files(project_path):
            text = read_source_text(path)
            if text is None or not sql_table_re.search(text):
                continue
            consumer_evidence.append(f"{name}:{safe_relative(path, project_path)}")
    if not consumer_evidence or not owner_evidence:
        return []
    catalog_nodes = {
        str(item.get("project") or ""): item
        for item in (architecture_catalog or {}).get("nodes") or []
        if isinstance(item, dict)
    }
    owner_node = catalog_nodes.get("df-mic-jichufw") or {}
    bff_node = catalog_nodes.get("df-bff-jichufw") or {}
    owner_api_symbols = set(owner_node.get("public_api_symbols") or [])
    bff_api_symbols = set(bff_node.get("public_api_symbols") or [])
    bff_api_contracts = [
        dict(item) for item in bff_node.get("public_api_contracts") or []
        if isinstance(item, dict)
        and any(
            token in str(item.get("route") or "").lower()
            for token in ("shoufeixm", "shoufei")
        )
    ]
    explicit_owner_api_rule = any(
        token.lower() in (combined_text or "").lower()
        for token in ("ShouFeiXmApi", "通过 BFF 公共 API", "改为 ShouFeiXmApi")
    )
    api_boundary_proven = bool(
        explicit_owner_api_rule
        and "ShouFeiXmApi" in owner_api_symbols
        and "ShouFeiXmApi" in bff_api_symbols
    )
    architecture_decision = "auto_resolved" if api_boundary_proven else "needs_user_choice"
    architecture_options = [
        {
            "id": "owner_service_api_through_bff",
            "label": "通过 BFF/所有者公共 API 读取 gy_shoufeixm，再由医保服务做映射",
            "status": "recommended" if api_boundary_proven else "candidate",
            "owner_projects": ["df-bff-jichufw", "df-mic-jichufw", "df-mic-yibaogl"],
            "evidence": [
                "df-mic-jichufw 提供 ShouFeiXmApi。",
                "df-bff-jichufw 的构建/源码证据可见 ShouFeiXmApi。",
            ],
            "rule": "不得继续从 df-mic-yibaogl 直接查询 gy_shoufeixm。",
        },
        {
            "id": "retain_direct_cross_schema",
            "label": "保留 yibaogl 直接跨库查询",
            "status": "rejected" if api_boundary_proven else "candidate",
            "owner_projects": ["df-mic-yibaogl"],
            "evidence": ["当前源码存在直接 SQL 证据，但这不是公共服务边界。"],
            "rule": "仅在明确批准历史兼容例外时讨论，不能作为默认自动改法。",
        },
    ]
    return [
        {
            "type": "direct_cross_schema_access",
            "status": "conflict",
            "owner_project": "df-mic-jichufw",
            "source_table": "gy_shoufeixm",
            "consumer_evidence": sorted(set(consumer_evidence)),
            "owner_evidence": sorted(set(owner_evidence)),
            "architecture_decision": architecture_decision,
            "recommended_option_id": "owner_service_api_through_bff",
            "architecture_options": architecture_options,
            "architecture_evidence": {
                "owner_api_proven": "ShouFeiXmApi" in owner_api_symbols,
                "bff_api_proven": "ShouFeiXmApi" in bff_api_symbols,
                "explicit_owner_api_rule": explicit_owner_api_rule,
                "owner_api_symbols": sorted(owner_api_symbols),
                "bff_api_symbols": sorted(bff_api_symbols),
                "bff_api_contracts": bff_api_contracts[:12],
            },
            "requires_code_change": api_boundary_proven,
            "message": (
                "业务微服务存在直接查询底层 gy_shoufeixm 的证据；Harness 已根据需求中的 ShouFeiXmApi 规则和所有者/BFF 公共 API 证据选择改为公共 API，"
                "但仍需把直接 SQL 纳入后端改动目标。"
                if api_boundary_proven
                else "业务微服务存在直接查询底层 gy_shoufeixm 的证据；需确认改为 ShouFeiXmApi，或明确保留现有跨库访问。"
            ),
        }
    ]


def find_multi_source_right_panel_findings(
    *,
    combined_text: str,
    root: Path,
    selected_projects: list[dict],
    architecture_catalog: dict | None = None,
) -> list[dict]:
    """Surface the drug/charge split behind the unified hospital-directory page.

    The category tree is already a BFF aggregation of ``YaoPinZdApi`` and
    ``ShouFeiXmApi``.  The right-hand page, however, is currently served by
    ``yb-yibaogl`` and combines drug/medical-material rows with charge rows,
   医保 mappings, and approval attributes.  A generic BFF charge endpoint is
    not equivalent to that contract.  This finding makes the missing boundary
    explicit before a patch routes one side of the table through the wrong
    service or silently drops the mapping/aggregation semantics.
    """
    lower = (combined_text or "").lower()
    # Do not require the ticket author to use one exact pair of labels.  A
    # requirement may say “医院目录/字典表/gy_shoufeixm” while the repository
    # proves the drug side through YaoPin* code.  The finding is still scoped
    # to a directory/data-source context so unrelated mentions do not trigger.
    has_directory_context = any(
        term.lower() in lower
        for term in ("医院目录", "gy_shoufeixm", "getYiYuanMuLuPage", "医保审批项目维护")
    )
    has_source_context = any(
        term.lower() in lower
        for term in ("药品", "收费项目", "字典表", "YaoPin", "ShouFeiXm")
    )
    if not (has_directory_context and has_source_context):
        return []
    selected_by_name = {
        str(item.get("name") or ""): item
        for item in selected_projects
        if str(item.get("name") or "").strip()
    }
    yibaogl = selected_by_name.get("df-mic-yibaogl")
    bff = selected_by_name.get("df-bff-jichufw")
    if not yibaogl or not yibaogl.get("exists"):
        return []

    consumer_tokens = (
        "getYiYuanMuLuPage",
        "getYaoPinMuLu",
        "V_YB_YaoPinDmDzXx",
        "ZhenLiaoMuLuSql",
        "gy_shoufeixm",
    )
    consumer_evidence: list[str] = []
    consumer_path = Path(str(yibaogl.get("path") or ""))
    if consumer_path.is_dir():
        for path in iter_text_files(consumer_path):
            text = read_source_text(path)
            if text is None or not any(token.lower() in text.lower() for token in consumer_tokens):
                continue
            consumer_evidence.append(
                f"df-mic-yibaogl:{safe_relative(path, consumer_path)}"
            )

    bff_evidence: list[str] = []
    bff_path = Path(str(bff.get("path") or "")) if bff else None
    if bff_path and bff_path.is_dir():
        for path in iter_text_files(bff_path):
            text = read_source_text(path)
            if text is None:
                continue
            if "FenLeiTreeService" in text and "YaoPinZdApi" in text and "ShouFeiXmApi" in text:
                bff_evidence.append(
                    f"df-bff-jichufw:{safe_relative(path, bff_path)}"
                )

    if not consumer_evidence:
        return []
    catalog_nodes = {
        str(item.get("project") or ""): item
        for item in (architecture_catalog or {}).get("nodes") or []
        if isinstance(item, dict)
    }
    required_source_projects = ("df-mic-jichufw", "df-mic-yaokufang")
    evidence_only_projects = [
        name for name in required_source_projects
        if name not in selected_by_name and bool((catalog_nodes.get(name) or {}).get("exists"))
    ]
    missing_projects = [
        name for name in required_source_projects
        if not bool((catalog_nodes.get(name) or {}).get("exists"))
    ]
    architecture = recommend_right_panel_architecture(
        catalog=architecture_catalog
        or build_service_architecture_catalog(root=root, selected_projects=selected_projects)
    )
    recommended = architecture.get("recommended_option_id") or ""
    message = (
        "右侧医院目录同时包含药品/卫材和收费项目；当前页面统一入口在 yb-yibaogl，"
        "而 BFF 证据只覆盖分类树。不能直接把现有收费项目 BFF 分页当作右侧完整接口；"
        "Harness 已根据本地构建文件和公共 API 证据优先选择 BFF 提供原始目录、"
        "yibaogl 负责医保多对照和审批属性的边界；只有原始 API 契约不足时才升级为新增 BFF 统一接口。"
    )
    if architecture.get("status") == "needs_reconciliation":
        message += (
            " 当前医保服务工作区存在与该目录链路相关的未提交实现；该实现与候选 BFF 原始目录边界尚未完成对照，"
            "Harness 已暂停自动改码，必须先确认现有实现是本需求基线还是待审改动。"
        )
    if evidence_only_projects:
        message += (
            " 以下服务未列入候选改动范围，但已作为证据项目读取："
            + ", ".join(evidence_only_projects)
            + "。"
        )
    if missing_projects:
        message += " 本地未发现以下必要服务，无法闭合范围：" + ", ".join(missing_projects) + "。"
    return [
        {
            "type": "multi_source_right_panel_boundary",
            "status": "conflict",
            "consumer_project": "df-mic-yibaogl",
            "source_projects": ["df-mic-yaokufang", "df-mic-jichufw"],
            "consumer_evidence": sorted(set(consumer_evidence))[:24],
            "bff_evidence": sorted(set(bff_evidence))[:12],
            "missing_selected_projects": missing_projects,
            "evidence_only_projects": evidence_only_projects,
            "architecture_decision": architecture.get("status") or "needs_api_evidence",
            "recommended_option_id": recommended,
            "existing_consumer_dirty_implementation": bool(
                (architecture.get("evidence") or {}).get("existing_consumer_dirty_implementation")
            ),
            # The current BFF evidence proves the category-tree aggregation,
            # not the complete paginated right-panel contract.  The boundary
            # is therefore understood but still requires an explicit code/API
            # target before a patch can be generated.
            "requires_code_change": True,
            "architecture_options": architecture.get("options") or [],
            "architecture_evidence": architecture.get("evidence") or {},
            "contract_proposal": architecture.get("contract_proposal")
            or build_right_panel_contract_proposal(evidence=architecture.get("evidence") or {}),
            "message": message,
        }
    ]


def find_approval_flag_rule_findings(
    *,
    combined_text: str,
    root: Path,
    selected_projects: list[dict],
) -> list[dict]:
    """Detect non-strict outpatient/inpatient flag checks in the target chain.

    The requirement explicitly defines the four approval flags as a strict
    two-stage rule: ``menzhenbz/zhuyuanbz == 1`` first, then
    ``zifeibz/bushangchuanbz == 1``.  A negated ``equals(..., 0)`` check is not
    equivalent because null and other values pass through.  This is a static
    conflict finding only; it must never auto-rewrite business code.
    """
    lower = (combined_text or "").lower()
    required_terms = ("menzhenbz", "zhuyuanbz", "zifeibz", "bushangchuanbz")
    has_field_names = all(term in lower for term in required_terms)
    # 需求单经常只写中文字段含义。中文业务口径仍足以证明这是同一组
    # 审批标志规则，不能因为没有再次抄写数据库字段名而跳过代码扫描。
    has_chinese_rule = (
        ("门诊自费" in lower and any(item in lower for item in ("门诊不上传", "门诊部上传", "门诊上传")))
        and (
            ("住院自费" in lower and any(item in lower for item in ("住院不上传", "住院部上传", "住院上传")))
            or "住院一样" in lower
        )
    )
    if not has_field_names and not has_chinese_rule:
        return []
    target_names = {
        str(item.get("name") or "")
        for item in selected_projects
        if str(item.get("name") or "").startswith("df-mic-")
    }
    if not target_names:
        return []
    non_strict = re.compile(
        r"!\s*Objects\.equals\s*\(\s*[^)]*get(?P<scope>MenZhen|ZhuYuan)Bz\s*\(\)\s*,\s*0\s*\)"
        r"|[^\n]{0,80}get(?P<scope2>MenZhen|ZhuYuan)Bz\s*\(\)\s*(?:!=|>)\s*0",
        flags=re.IGNORECASE,
    )
    findings: list[dict] = []
    for project in selected_projects:
        name = str(project.get("name") or "")
        if name not in target_names:
            continue
        project_path = Path(str(project.get("path") or ""))
        if not project_path.is_dir():
            continue
        for path in iter_text_files(project_path):
            text = read_source_text(path)
            if text is None:
                continue
            for match in non_strict.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                scope = match.group("scope") or match.group("scope2") or "门诊/住院"
                findings.append(
                    {
                        "type": "non_strict_approval_flag_check",
                        "status": "conflict",
                        "project": name,
                        "path": f"{name}:{safe_relative(path, project_path)}:{line}",
                        "scope": scope,
                        "message": (
                            f"{scope}标志当前通过非0判断；null或其他非0值可能被当成有效，"
                            "与需求要求严格等于1冲突，应先确认并改为 equals(..., 1)。"
                        ),
                    }
                )
    return findings[:24]


def find_frontend_endpoints(*, project_path: Path, entry_matches: list[dict], max_files: int = 240) -> list[tuple[str, str]]:
    """Follow the matched page's bounded import closure before collecting URLs.

    Scanning every ``src/api`` directory makes neighboring pages look like part
    of the requirement. Starting from the matched route/page and resolving only
    its local imports keeps the service graph branch-level and reproducible.
    """
    endpoints: list[tuple[str, str]] = []
    entry_roots = frontend_entry_roots(project_path=project_path, entry_matches=entry_matches)
    if not entry_roots:
        return endpoints
    queue: list[Path] = []
    for entry_root in entry_roots:
        queue.extend(
            sorted(
                iter_text_files(entry_root),
                key=lambda item: safe_relative(item, project_path),
            )
        )
    seen_paths: set[Path] = set()
    scanned = 0
    while queue and scanned < max_files:
        path = queue.pop(0).resolve()
        if path in seen_paths or not path.is_file():
            continue
        seen_paths.add(path)
        scanned += 1
        text = read_source_text(path)
        if text is None:
            continue
        for endpoint in extract_frontend_endpoints(text):
            item = (endpoint, safe_relative(path, project_path))
            if item not in endpoints:
                endpoints.append(item)
        for source in IMPORT_SOURCE_RE.findall(text):
            imported = resolve_frontend_import(project_path=project_path, source_path=path, import_source=source)
            if imported is not None and imported not in seen_paths:
                queue.append(imported)
    return endpoints


def narrow_endpoint_sources_by_demand(
    *,
    endpoint_sources: list[tuple[str, str]],
    combined_text: str,
) -> list[tuple[str, str]]:
    """Use an explicitly evidenced endpoint as the authoritative branch.

    A large page may import registration, settlement, refund and dictionary
    clients together.  When the requirement or normalized read-only evidence
    names one concrete endpoint/method, unrelated imports are not part of that
    change contract.  If no endpoint is named, preserve every branch for the
    broader analysis path.
    """
    def is_explicit_token(value: str) -> bool:
        return bool(
            value
            and re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])",
                combined_text,
            )
        )

    named = [
        item
        for item in endpoint_sources
        if is_explicit_token(item[0]) or is_explicit_token(item[0].rsplit("/", 1)[-1])
    ]
    return named or endpoint_sources


def _endpoint_matches_authoritative_locator(*, endpoint: str, terms: Sequence[str]) -> bool:
    normalized_endpoint = str(endpoint or "").strip().lstrip("/")
    endpoint_method = normalized_endpoint.rsplit("/", 1)[-1]
    for raw_term in terms:
        term = str(raw_term or "").strip().lstrip("/")
        if not term:
            continue
        if "/" in term and normalized_endpoint == term:
            return True
        if "/" not in term and endpoint_method == term:
            return True
    return False


def resolve_frontend_import(*, project_path: Path, source_path: Path, import_source: str) -> Path | None:
    source = str(import_source or "").strip()
    if not source or source.startswith(("@/", ".")) is False:
        return None
    if source.startswith("@/"):
        base = project_path / "src" / source[2:]
    else:
        base = source_path.parent / source
    candidates = [base]
    if not base.suffix:
        candidates.extend(base.with_suffix(suffix) for suffix in (".js", ".ts", ".jsx", ".tsx", ".vue"))
        candidates.extend(base / f"index{suffix}" for suffix in (".js", ".ts", ".jsx", ".tsx", ".vue"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(project_path.resolve())
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def extract_frontend_endpoints(text: str) -> list[str]:
    """Extract literal and simple concatenated gateway URLs.

    Frontends commonly keep ``/winbff-*`` or ``/yb-*`` in a constant and append
    the controller route in an API helper. Resolving this bounded form avoids
    treating an external API directory as an unrelated project.
    """
    endpoints: list[str] = [
        normalize_http_endpoint(match.group("endpoint"))
        for match in HTTP_ENDPOINT_RE.finditer(text)
    ]
    prefixes = {
        match.group("name"): match.group("prefix")
        for match in HTTP_GATEWAY_ASSIGNMENT_RE.finditer(text)
    }
    for match in HTTP_DYNAMIC_ENDPOINT_RE.finditer(text):
        prefix = prefixes.get(match.group("name"))
        if prefix:
            endpoints.append(prefix + match.group("suffix"))
    return unique_keep_order(endpoints)


def normalize_http_endpoint(value: str) -> str:
    endpoint = str(value or "").strip()
    return endpoint if endpoint.startswith("/") else "/" + endpoint


def frontend_entry_roots(*, project_path: Path, entry_matches: list[dict]) -> list[Path]:
    roots: list[Path] = []
    for item in entry_matches:
        relative_path = str(item.get("path") or "")
        term = str(item.get("term") or "")
        source_path = project_path / relative_path
        source_text = read_source_text(source_path)
        if not source_text:
            continue
        normalized_path = relative_path.replace("\\", "/")
        if normalized_path.startswith("src/router/") and term:
            position = source_text.find(term)
            if position < 0:
                continue
            match = ROUTE_COMPONENT_IMPORT_RE.search(source_text, position, min(len(source_text), position + 420))
            if match:
                candidate = project_path / "src" / match.group("path")
                roots.append(candidate if candidate.is_dir() else candidate.parent)
        elif normalized_path.startswith(("src/views/", "src/pages/")):
            roots.append(source_path.parent)
    unique_roots = [Path(path) for path in unique_keep_order(str(path) for path in roots)]
    page_roots = [path for path in unique_roots if (path / "index.vue").is_file()]
    if page_roots:
        retained_roots = [
            path
            for path in unique_roots
            if path in page_roots
            or not any(path != page_root and path in page_root.parents for page_root in page_roots)
        ]
    else:
        retained_roots = unique_roots
    return [path for path in retained_roots if path.is_dir()]


def endpoint_target_project(endpoint: str) -> str:
    parts = [part for part in endpoint.split("/") if part]
    if not parts:
        return ""
    gateway = parts[0]
    if gateway.startswith("winbff-"):
        return "df-bff-" + gateway[len("winbff-") :]
    if gateway.startswith("yb-"):
        return "df-mic-" + gateway[len("yb-") :]
    return ""


def resolve_endpoint_target_project(*, endpoint: str, architecture_catalog: dict) -> str:
    """Resolve a gateway by naming convention or one exact Controller route.

    DFHIS frontends also use service client prefixes such as ``/jj-guahao``.
    Those prefixes do not encode the repository name.  In that case Harness
    accepts ownership only when exactly one catalogued project exposes the
    remaining Controller route; ambiguous matches stay unresolved.
    """
    conventional = endpoint_target_project(endpoint)
    catalog_nodes = architecture_catalog.get("nodes") or []
    if conventional:
        return conventional
    route = endpoint_controller_route(endpoint)
    owners = unique_keep_order(
        str(node.get("project") or "")
        for node in catalog_nodes
        if any(
            str(contract.get("route") or "") == route
            for contract in node.get("public_api_contracts") or []
        )
    )
    return owners[0] if len(owners) == 1 else ""


def endpoint_controller_route(endpoint: str) -> str:
    parts = [part for part in str(endpoint or "").split("/") if part]
    if len(parts) < 3:
        return ""
    return "/" + "/".join(parts[1:])


def find_endpoint_contract(*, endpoint: str, target_project: str, architecture_catalog: dict) -> dict:
    route = endpoint_controller_route(endpoint)
    for node in architecture_catalog.get("nodes") or []:
        if str(node.get("project") or "") != target_project:
            continue
        matches = [
            dict(contract)
            for contract in node.get("public_api_contracts") or []
            if str(contract.get("route") or "") == route
        ]
        return matches[0] if len(matches) == 1 else {}
    return {}


def compact_endpoint_contract(contract: dict) -> dict:
    return {
        "route": contract.get("route"),
        "http_method": contract.get("http_method"),
        "controller": contract.get("controller"),
        "method": contract.get("method"),
        "return_type": contract.get("return_type"),
        "request_types": list(contract.get("request_types") or []),
        "response_types": list(contract.get("response_types") or []),
        "evidence_paths": unique_keep_order(
            list(contract.get("evidence_paths") or [])
            + list(contract.get("service_evidence_paths") or [])
        ),
        "evidence_status": contract.get("evidence_status"),
        "service_contract_status": contract.get("service_contract_status"),
    }


def build_endpoint_field_contract(
    *,
    target: dict,
    target_project: str,
    target_path: Path,
    endpoint_contract: dict,
) -> dict:
    target_field = str(target.get("field") or "")
    response_types = unique_keep_order(
        str(item) for item in endpoint_contract.get("response_types") or [] if str(item).strip()
    )
    evidence_paths = find_response_type_field_paths(
        project_path=target_path,
        project_name=target_project,
        response_types=response_types,
        aliases=list(target.get("aliases") or [target_field]),
    )
    verified = bool(endpoint_contract and response_types and evidence_paths)
    return {
        "status": "verified" if verified else "missing",
        "target_field": target_field,
        "response_types": response_types,
        "evidence_paths": evidence_paths,
        "reason": (
            "目标字段存在于该页面实际接口声明的响应 DTO 中。"
            if verified
            else "尚未在该页面实际接口声明的响应 DTO 中证明目标字段。"
        ),
    }


def find_response_type_field_paths(
    *,
    project_path: Path,
    project_name: str,
    response_types: list[str],
    aliases: list[str],
    max_files: int = 1200,
) -> list[str]:
    if not response_types or not project_path.is_dir():
        return []
    matches: list[str] = []
    scanned = 0
    for path in iter_text_files(project_path):
        scanned += 1
        if scanned > max_files:
            break
        if path.stem not in response_types:
            continue
        text = read_source_text(path)
        if text is None or not any(alias and alias in text for alias in aliases):
            continue
        matches.append(f"{project_name}:{safe_relative(path, project_path)}")
    return unique_keep_order(matches)[:12]


def backend_change_is_explicitly_excluded(text: str) -> bool:
    return bool(
        re.search(r"(?:后端|BFF|公共\s*API|数据库)[^。；\n]{0,80}(?:不修改|无需修改|不应修改)", text)
        or re.search(r"(?:不修改|无需修改|不应修改)[^。；\n]{0,80}(?:后端|BFF|公共\s*API|数据库)", text)
    )


def branch_scope(*, endpoint: str, combined_text: str, target_name: str, change_requested: bool) -> str:
    """Separate page reachability from a requirement-backed change decision."""
    if target_name.startswith("df-bff-"):
        return "existing_dependency"
    if not change_requested:
        return "impact_regression"
    operation = endpoint.rsplit("/", 1)[-1]
    if endpoint in combined_text or operation in combined_text:
        return "change_required"
    return "candidate_change"


def load_controller_sources(project_path: Path, max_files: int = 1200) -> list[tuple[str, str]]:
    if not project_path.is_dir():
        return []
    sources: list[tuple[str, str]] = []
    scanned = 0
    for path in iter_text_files(project_path):
        scanned += 1
        if scanned > max_files:
            break
        if "controller" not in path.name.lower():
            continue
        text = read_source_text(path)
        if text is not None:
            sources.append((safe_relative(path, project_path), text))
    return sources


def find_controller_paths_for_endpoint(
    *,
    project_path: Path,
    endpoint: str,
    source_files: list[tuple[str, str]] | None = None,
) -> list[str]:
    parts = [part for part in endpoint.split("/") if part][1:]
    if len(parts) < 2:
        return []
    sources = source_files if source_files is not None else load_controller_sources(project_path)
    class_path, method_path = parts[-2], parts[-1]
    matches: list[str] = []
    for relative_path, text in sources:
        if controller_contains_route(text=text, class_path=class_path, method_path=method_path):
            matches.append(relative_path)
    return matches[:6]


CLASS_MAPPING_RE = re.compile(
    r"@RequestMapping\s*\([^)]*?['\"]/(?P<path>[A-Za-z0-9_-]+)['\"]",
    flags=re.S,
)
METHOD_MAPPING_RE = re.compile(
    r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\s*\([^)]*?['\"]/(?P<path>[A-Za-z0-9_-]+)['\"]",
    flags=re.S,
)
CLASS_DECLARATION_RE = re.compile(r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*[^\{]*\{")


def controller_contains_route(*, text: str, class_path: str, method_path: str) -> bool:
    """Verify class and method mappings in one Java class body."""
    for class_match in CLASS_DECLARATION_RE.finditer(text):
        class_start = class_match.start()
        class_open = class_match.end() - 1
        class_close = matching_brace_end(text, class_open)
        if class_close < 0:
            continue
        prefix = text[max(0, class_start - 1000) : class_start]
        class_mappings = list(CLASS_MAPPING_RE.finditer(prefix))
        if not class_mappings or class_mappings[-1].group("path") != class_path:
            continue
        body = text[class_open : class_close + 1]
        if any(match.group("path") == method_path for match in METHOD_MAPPING_RE.finditer(body)):
            return True
    return False


def matching_brace_end(text: str, opening_index: int) -> int:
    depth = 0
    for index in range(opening_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def build_public_api_dto_index(root: Path, service_path: Path | None = None) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for project_name, api_root in public_api_project_roots(root, service_path):
        shared_service_marker = shared_api_service_marker(api_root=api_root, service_path=service_path) if project_name == "df-his-api" else ""
        for path in iter_text_files(api_root):
            if path.suffix.lower() not in API_SOURCE_EXTENSIONS:
                continue
            relative_path = safe_relative(path, api_root)
            if shared_service_marker and shared_service_marker not in relative_path.lower():
                continue
            text = read_source_text(path)
            if text is None:
                continue
            qualified_path = relative_path if project_name == "df-his-api" else f"{project_name}:{relative_path}"
            for term in unique_keep_order(DTO_IDENTIFIER_RE.findall(text)):
                index.setdefault(term, []).append(qualified_path)
    return index


def shared_api_service_marker(*, api_root: Path, service_path: Path | None) -> str:
    """Use a shared API module marker only when the repository exposes one."""
    if service_path is None or not api_root.is_dir():
        return ""
    service_name = service_path.name.lower().removeprefix("df-mic-")
    marker = service_name.split("-")[-1]
    if len(marker) < 4:
        return ""
    try:
        has_matching_module = any(
            child.is_dir() and marker in child.name.lower()
            for child in api_root.iterdir()
        )
    except OSError:
        return ""
    return marker if has_matching_module else ""


def public_api_project_roots(root: Path, service_path: Path | None = None) -> list[tuple[str, Path]]:
    """Locate the legacy shared API tree and service-specific external API trees."""
    roots: list[tuple[str, Path]] = []
    shared = root / "df-his-api"
    if shared.is_dir():
        roots.append((shared.name, shared))
    service_name = service_path.name if service_path else ""
    service_tokens = {service_name}
    if service_name.startswith("df-mic-"):
        service_tokens.add(service_name.removeprefix("df-mic-"))
    for candidate in sorted(root.iterdir(), key=lambda item: item.name) if root.is_dir() else []:
        if not candidate.is_dir() or not candidate.name.endswith("-api") or candidate == shared:
            continue
        if service_name and not any(token and token in candidate.name for token in service_tokens):
            continue
        roots.append((candidate.name, candidate))
    return roots


def split_public_api_contract_path(path: str) -> tuple[str, str]:
    if ":" in path:
        project_name, relative_path = path.split(":", 1)
        if project_name:
            return project_name, relative_path
    return "df-his-api", path


def find_public_api_contract_paths(
    *,
    root: Path,
    service_path: Path,
    controller_paths: list[str],
    dto_index: dict[str, list[str]] | None = None,
) -> list[str]:
    if not public_api_project_roots(root, service_path):
        return []
    dto_terms: set[str] = set()
    for relative_path in controller_paths:
        text = read_source_text(service_path / relative_path)
        if text:
            dto_terms.update(DTO_IDENTIFIER_RE.findall(text))
    if not dto_terms:
        return []
    index = dto_index if dto_index is not None else build_public_api_dto_index(root, service_path=service_path)
    return unique_keep_order(
        path
        for term in sorted(dto_terms)
        for path in index.get(term, [])
    )[:8]


def add_service_graph_node(
    nodes: dict[str, dict],
    *,
    project: str,
    path: Path,
    role: str,
    scope: str,
    evidence_paths: list[str],
    endpoints: list[str] | None = None,
    entry_paths: list[str] | None = None,
) -> None:
    node = nodes.setdefault(
        project,
        {
            "project": project,
            "path": str(path),
            "role": role,
            "scope": scope,
            "evidence_paths": [],
            "entry_paths": [],
            "endpoints": [],
        },
    )
    scope_rank = {
        "change_required": 5,
        "candidate_change": 4,
        "contract_check": 3,
        "impact_regression": 2,
        "existing_dependency": 1,
        "entry_point": 0,
    }
    if scope_rank.get(scope, 0) > scope_rank.get(str(node.get("scope") or ""), 0):
        node["scope"] = scope
    node["evidence_paths"] = unique_keep_order(list(node.get("evidence_paths") or []) + evidence_paths)
    node["entry_paths"] = unique_keep_order(list(node.get("entry_paths") or []) + list(entry_paths or []))
    node["endpoints"] = unique_keep_order(list(node.get("endpoints") or []) + list(endpoints or []))


def merge_service_graph_projects(*, selected_projects: list[dict], service_graph: dict) -> list[dict]:
    selected = {}
    for item in selected_projects:
        copied = dict(item)
        copied.setdefault("selection_scope", "candidate_only")
        selected[str(copied.get("name") or "")] = copied
    for node in service_graph.get("nodes") or []:
        name = str(node.get("project") or "")
        if not name:
            continue
        existing = selected.get(name) or {
            "path": str(node.get("path") or ""),
            "name": name,
            "role": node.get("role") or infer_project_role(name),
            "score": 90,
            "exists": Path(str(node.get("path") or "")).is_dir(),
            "reasons": [],
            "selection_scope": "candidate_only",
        }
        node_scope = str(node.get("scope") or "").strip()
        if node_scope in PROJECT_SELECTION_SCOPES:
            existing["selection_scope"] = node_scope
        existing["reasons"] = unique_keep_order(
            list(existing.get("reasons") or []) + [f"服务图已按实际接口定位，范围：{node.get('scope')}。"]
        )
        selected[name] = existing
    return sorted(selected.values(), key=lambda item: (-int(item.get("score", 0)), role_rank(str(item.get("role") or ""))))


def prune_unrelated_candidate_projects(
    *,
    selected_projects: list[dict],
    service_graph: dict,
    combined_text: str,
) -> list[dict]:
    """Drop heuristic-only candidates after a narrow exact graph is proven.

    Candidate discovery is intentionally broad before route resolution.  Once
    an explicit display field has a concrete frontend/service/API graph, a
    same-domain BFF guessed from the title must not remain in the formal scope.
    """
    target = infer_target_field(remove_generated_harness_context(combined_text))
    if target.get("kind") != "explicit_display_field":
        return selected_projects
    graph_projects = {
        str(node.get("project") or "")
        for node in service_graph.get("nodes") or []
        if str(node.get("project") or "")
    }
    if not graph_projects:
        return selected_projects
    return [
        item
        for item in selected_projects
        if (
            str(item.get("selection_scope") or "") != "candidate_only"
            or str(item.get("name") or "") in graph_projects
        )
    ]


def read_source_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="gb18030")
        except (UnicodeDecodeError, OSError):
            return None
    except OSError:
        return None


def expand_contract_projects(*, selected_projects: list[dict], root: Path, combined_text: str) -> list[dict]:
    """Add only deterministic sibling services when the request needs an API contract check."""
    if not requires_service_contract(combined_text):
        return selected_projects
    result = []
    for item in selected_projects:
        copied = dict(item)
        copied.setdefault("selection_scope", "candidate_only")
        result.append(copied)
    seen = {str(item.get("path") or "") for item in result}
    sibling_names: list[str] = ["df-his-api"]
    for item in selected_projects:
        if item.get("role") != "frontend":
            continue
        name = str(item.get("name") or Path(str(item.get("path") or "")).name)
        if name.startswith("df-web-"):
            sibling_names.append("df-bff-" + name[len("df-web-") :])
        if "guahao" in name.lower():
            sibling_names.append("df-mic-jj-menzhen")
    for name in unique_keep_order(sibling_names):
        path = root / name
        if not path.is_dir() or str(path) in seen:
            continue
        result.append(
            {
                "path": str(path),
                "name": name,
                "role": infer_project_role(name),
                "score": 80,
                "exists": True,
                "selection_scope": "contract_check",
                "reasons": ["需求涉及接口/入参/返回契约，作为最小服务端核验候选。"],
            }
        )
        seen.add(str(path))
    return sorted(result, key=lambda item: (-int(item.get("score", 0)), role_rank(str(item.get("role") or ""))))


def requires_service_contract(text: str) -> bool:
    text = extract_change_intent_text(text)
    text = remove_negated_scope_clauses(text)
    if any(term in text for term in ROUTE_LOCAL_HINTS) and not any(
        term in text for term in ("入参", "接口", "请求", "排序", "服务端", "后端", "BFF", "API")
    ):
        return False
    return any(term in text for term in SERVICE_CONTRACT_HINTS) or bool(RETURN_CONTRACT_HINT.search(text))


def extract_change_intent_text(text: str) -> str:
    """Exclude read-only evidence and local delivery notes from change ownership routing."""
    cleaned = remove_generated_harness_context(text)
    for marker in ("\n只读代码证据：", "\n当前本地仓库边界："):
        cleaned = cleaned.split(marker, 1)[0]
    cleaned = re.sub(
        r"(?:^|[。；\n])\s*(?:只读代码证据|只读证据|代码证据|当前本地仓库边界)[:：][^。；\n]*",
        "",
        cleaned,
    )
    return cleaned


def remove_generated_harness_context(text: str) -> str:
    """Keep generated reports from feeding their generic labels back into risk gates."""
    return (text or "").split(HARNESS_CONTEXT_MARKER, 1)[0]


def build_contract_verification(
    *,
    combined_text: str,
    selected_projects: list[dict],
    allowed_paths: list[str],
    contract_parameters: list[str] | None = None,
    service_graph: dict | None = None,
) -> dict:
    target = infer_target_field(combined_text)
    response_field_required = bool(
        target.get("kind") == "explicit_display_field" and target.get("field")
    )
    if not requires_service_contract(combined_text) and not response_field_required:
        return {
            "required": False,
            "status": "not_required",
            "reason": "需求未命中接口、入参、返回字段等跨层契约关键词，按客户端局部改动处理。",
            "contract_terms": [],
            "layers": {},
        }

    explicit_parameters = unique_keep_order(
        str(parameter).strip() for parameter in (contract_parameters or []) if str(parameter).strip()
    )
    terms = extract_contract_terms(combined_text, contract_parameters=explicit_parameters or None)
    graph_contract = build_service_graph_contract_evidence(service_graph)
    # A closed service-graph branch already proves the concrete frontend
    # request source and its server Controller target. Do not make a broad
    # feature wait for a literal endpoint string that may be assembled from a
    # gateway constant and a suffix in the source file. Named request
    # parameters remain on the stricter text-matching path below.
    if graph_contract and not explicit_parameters:
        terms = unique_keep_order([*graph_contract["terms"], *terms])
        client_matches = graph_contract["client_paths"]
        server_matches = graph_contract["server_paths"]
        evidence_mode = "service_graph"
    else:
        client_matches = find_contract_matches(selected_projects=selected_projects, roles={"frontend"}, terms=terms)
        server_matches = find_contract_matches(selected_projects=selected_projects, roles={"backend", "api"}, terms=terms)
        evidence_mode = "source_text" if not graph_contract else "source_text_with_named_parameters"
    client_status = "verified" if client_matches else "missing"
    server_status = "verified" if server_matches else "missing"
    response_field_contracts = matching_endpoint_field_contracts(
        service_graph=service_graph or {},
        target_field=str(target.get("field") or ""),
    )
    response_field_matches = unique_keep_order(
        str(path)
        for contract in response_field_contracts
        for path in contract.get("evidence_paths") or []
        if str(path).strip()
    )
    response_field_verified = bool(response_field_matches) and all(
        contract.get("status") == "verified" for contract in response_field_contracts
    )
    status = "verified" if (
        client_matches
        and server_matches
        and (not response_field_required or response_field_verified)
    ) else "blocked"
    return {
        "required": True,
        "status": status,
        "reason": "需求涉及跨层接口契约，客户端请求和服务端处理均需有源码证据。",
        "contract_terms": terms,
        "parameter_source": "explicit_resolved_parameters" if explicit_parameters else "demand_and_evidence",
        "evidence_mode": evidence_mode,
        "allowed_paths": list(allowed_paths),
        "layers": {
            "client_request": {
                "status": client_status,
                "summary": "已在客户端源码命中接口/参数证据。" if client_matches else "未在客户端源码命中可核验的接口/参数证据。",
                "evidence_paths": client_matches,
            },
            "server_contract": {
                "status": server_status,
                "summary": "已在 BFF、服务端或公共 API 源码命中同一接口/参数证据。" if server_matches else "未在 BFF、服务端或公共 API 源码命中接口/参数证据；不能仅凭需求评论假定服务端已支持。",
                "evidence_paths": server_matches,
            },
            **(
                {
                    "response_field": {
                        "status": "verified" if response_field_verified else "missing",
                        "summary": (
                            f"已证明实际接口响应 DTO 返回字段 {target.get('field')}。"
                            if response_field_verified
                            else f"尚未证明实际接口响应 DTO 返回字段 {target.get('field')}。"
                        ),
                        "evidence_paths": response_field_matches,
                    }
                }
                if response_field_required
                else {}
            ),
        },
    }


def build_service_graph_contract_evidence(service_graph: dict | None) -> dict:
    """Turn closed service-graph branches into bounded contract evidence.

    The graph is produced only after the frontend import closure and matching
    Controller route have been found. It is therefore stronger than a keyword
    hit, but it does not assert DTO fields or business semantics; those remain
    separate provenance/acceptance checks.
    """
    graph = service_graph or {}
    if graph.get("status") != "evidence_ready" or graph.get("unresolved_endpoints"):
        return {}
    branches = [
        branch
        for branch in graph.get("branches") or []
        if branch.get("controller_verified")
        and str(branch.get("endpoint") or "").strip()
        and str(branch.get("target_path") or "").strip()
    ]
    if not branches:
        return {}
    client_paths = unique_keep_order(
        str(path)
        for branch in branches
        for path in (branch.get("source_paths") or [branch.get("source_path")])
        if str(path).strip()
    )
    server_paths = unique_keep_order(
        str(branch.get("target_path") or "")
        for branch in branches
        if str(branch.get("target_path") or "").strip()
    )
    if not client_paths or not server_paths:
        return {}
    return {
        "terms": unique_keep_order(
            str(branch.get("endpoint") or "")
            for branch in branches
            if str(branch.get("endpoint") or "").strip()
        ),
        "client_paths": client_paths,
        "server_paths": server_paths,
    }


def extract_contract_terms(text: str, *, contract_parameters: list[str] | None = None) -> list[str]:
    endpoints = unique_keep_order(CONTRACT_ENDPOINT_RE.findall(text))
    if contract_parameters is not None:
        return unique_keep_order(endpoints + contract_parameters)
    declared = []
    for clause in CONTRACT_DECLARATION_RE.findall(text):
        declared.extend(CONTRACT_IDENTIFIER_RE.findall(clause))
    parameters = [
        item
        for item in declared
        if item.lower() not in CONTRACT_IDENTIFIER_EXCLUDES
        and not CONTRACT_ENDPOINT_RE.fullmatch(item)
        and any(token in item.lower() for token in ("sort", "field", "order", "param", "request", "response"))
    ]
    return unique_keep_order(endpoints + parameters)


def find_contract_matches(*, selected_projects: list[dict], roles: set[str], terms: list[str]) -> list[str]:
    if not terms:
        return []
    matches: list[str] = []
    for project in selected_projects:
        if str(project.get("role") or "") not in roles:
            continue
        project_path = Path(str(project.get("path") or ""))
        if not project_path.exists():
            continue
        scanned = 0
        for path in iter_text_files(project_path):
            scanned += 1
            if scanned > 600:
                break
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    text = path.read_text(encoding="gb18030")
                except UnicodeDecodeError:
                    continue
            except OSError:
                continue
            if not source_has_contract_terms(text=text, terms=terms):
                continue
            matches.append(f"{project.get('name') or project_path.name}:{safe_relative(path, project_path)}")
    return unique_keep_order(matches)[:12]


def source_has_contract_terms(*, text: str, terms: list[str]) -> bool:
    """Require endpoint and declared parameters to occur in one local call/signature context."""
    if not terms:
        return False
    endpoint = terms[0]
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if endpoint not in line:
            continue
        start = max(0, index - CONTRACT_EVIDENCE_WINDOW_LINES)
        end = min(len(lines), index + CONTRACT_EVIDENCE_WINDOW_LINES + 1)
        context = "\n".join(lines[start:end])
        if all(term in context for term in terms):
            return True
    return False


def apply_contract_gate(*, implementation: dict, contract_verification: dict) -> dict:
    decision = dict(implementation)
    if contract_verification.get("status") != "blocked":
        decision["contract_verification_status"] = contract_verification.get("status")
        return decision
    blockers = list(decision.get("blockers") or [])
    blockers.append("前后端契约未核验：需求评论或描述不能替代客户端请求与服务端处理的源码证据。")
    decision.update(
        {
            "can_patch": False,
            "change_type": "blocked_contract_unverified",
            "summary": "跨层接口契约缺少源码证据，Harness 已阻断自动 patch，避免误判为仅客户端或仅服务端改动。",
            "blockers": unique_keep_order(blockers),
            "contract_verification_status": "blocked",
        }
    )
    return decision


def select_projects(*, combined_text: str, root: Path, explicit_project_paths: list[str]) -> list[dict]:
    if explicit_project_paths:
        terms = demand_project_terms(combined_text)
        selected: list[dict] = []
        for raw_path in explicit_project_paths:
            if not str(raw_path).strip():
                continue
            project_path = Path(raw_path).expanduser().resolve()
            role = infer_project_role(project_path.name)
            item = {
                "path": str(project_path),
                "name": project_path.name,
                "role": role,
                "score": 100,
                "exists": project_path.exists(),
                "selection_scope": "candidate_only",
                "reasons": ["用户显式传入项目路径，Harness 只在该路径内做工程判断。"],
            }
            if role == "frontend" and project_path.is_dir() and terms:
                matches = find_project_term_matches(project_path=project_path, terms=terms)
                selected_matches = select_frontend_entry_matches(matches, project_path=project_path)
                item["entry_matches"] = [
                    {"term": term, "path": path}
                    for term, path in selected_matches
                ]
                item["entry_matches_truncated"] = len(selected_matches) < len(matches)
                if matches:
                    item["reasons"].append(
                        f"显式项目内定位到需求入口 {', '.join(term for term, _path in matches[:3])}。"
                    )
            selected.append(item)
        return selected

    candidates = default_project_candidates(combined_text)
    selected: list[dict] = []
    for name, role, base_score, reason in candidates:
        path = root / name
        selected.append(
            {
                "path": str(path),
                "name": name,
                "role": role,
                "score": base_score + score_project_name(name, combined_text),
                "exists": path.exists() and path.is_dir(),
                "selection_scope": "candidate_only",
                "reasons": [reason],
            }
        )
    selected = [item for item in selected if item.get("exists")]
    return sorted(selected, key=lambda item: (-int(item.get("score", 0)), role_rank(str(item.get("role") or ""))))[:6]


def default_project_candidates(text: str) -> list[tuple[str, str, int, str]]:
    candidates: list[tuple[str, str, int, str]] = []
    if any(term in text for term in ["医保审批", "医保对照", "医保目录", "医保限制说明", "批量上传医保"]):
        candidates.extend(
            [
                ("df-web-yibaogl", "frontend", 105, "需求命中医保审批/医保对照业务，优先定位医保管理前端。"),
                ("df-bff-yibaogl", "backend", 90, "医保管理页面通常经医保 BFF 聚合接口。"),
                ("df-mic-yibaogl", "backend", 85, "医保审批、目录和对照数据的服务端证据优先在医保微服务。"),
                ("df-his-api", "api", 60, "医保 DTO/API 公共契约可能在 HIS API 项目。"),
            ]
        )
    if "挂号收费" in text:
        candidates.extend(
            [
                ("df-web-guahaosf", "frontend", 95, "需求命中挂号收费，优先定位挂号收费前端。"),
                ("df-bff-guahaosf", "backend", 80, "收费病人查询通常经挂号收费 BFF 聚合。"),
                ("df-mic-jj-menzhen", "backend", 75, "挂号记录和查询条件可能在门诊挂号微服务。"),
                ("df-his-api", "api", 50, "查询接口公共契约可能在 HIS API 项目。"),
            ]
        )
    if any(term in text for term in ["住院收费", "住院结算", "住院病人", "住院患者", "住院页面", "住院服务", "预交金", "预交款", "结算收款"]):
        candidates.extend(
            [
                ("df-web-zhuyuansf", "frontend", 90, "需求命中住院收费/结算收款/预交金，优先定位住院收费前端。"),
                ("df-bff-zhuyuansf", "backend", 70, "需求可能涉及住院收费 BFF 返回字段或聚合接口。"),
                ("df-mic-jj-zhuyuan", "backend", 65, "需求可能涉及住院结算/预交金后端服务。"),
                ("df-his-api", "api", 50, "字段 DTO/API 定义可能在公共 API 项目。"),
            ]
        )
    if "挂号收费" not in text and any(term in text for term in ["门诊收费", "门诊挂号", "门诊查询", "门诊页面", "门诊服务", "诊间结算"]):
        candidates.extend(
            [
                ("df-web-zhushujugl", "frontend", 55, "需求命中门诊/诊间关键词，作为门诊前端候选。"),
                ("df-his-api", "api", 35, "字段 DTO/API 定义可能在公共 API 项目。"),
            ]
        )
    if not candidates:
        candidates.append(("df-his-api", "api", 20, "未命中明确项目，先保留公共 API 候选。"))
    return unique_candidate_projects(candidates)


def build_field_provenance(
    *,
    combined_text: str,
    selected_projects: list[dict],
    discovery: DiscoveryResult | None = None,
    service_graph: dict | None = None,
    default_value_precedence: dict | None = None,
    authoritative_code_locators: str = "",
) -> dict:
    discovery = discovery or discover_demand(
        demand_text=combined_text,
        selected_projects=selected_projects,
        max_files=MAX_SCAN_FILES_PER_PROJECT,
        max_file_bytes=MAX_FILE_BYTES,
    )
    target = infer_target_field(combined_text)
    discovery_target_field = infer_discovery_target_field(discovery)
    if is_broad_feature_requirement(combined_text):
        target = {
            "field": "",
            "kind": "multi_service_feature",
            "aliases": [],
        }
    elif not target.get("field") and is_behavior_change_requirement(combined_text):
        target = {
            "field": "",
            "kind": "behavior_change",
            "aliases": [],
        }
    elif not target.get("field") and discovery_target_field and has_narrow_filter_intent(combined_text):
        target = {
            "field": discovery_target_field,
            "kind": "discovered_stored_filter",
            "aliases": [discovery_target_field],
        }
    aliases = target.get("aliases") or []
    ui_terms = infer_ui_terms(combined_text)
    evidence: list[dict] = []
    target_ui_found = False
    field_returned = False
    target_ui_paths: list[str] = []
    field_source_paths: list[str] = []

    explicit_ui_evidence = find_explicit_ui_path_evidence(
        combined_text=combined_text,
        selected_projects=selected_projects,
    )
    evidence.extend(explicit_ui_evidence)
    target_ui_paths.extend(str(item.get("path") or "") for item in explicit_ui_evidence)
    target_ui_found = bool(target_ui_paths)
    authoritative_ui_paths = unique_keep_order(
        str(path)
        for project in selected_projects
        if isinstance(project, dict)
        and project.get("role") == "frontend"
        and project.get("authoritative_code_match") is True
        for path in project.get("authoritative_code_paths") or []
        if str(path).replace("\\", "/").endswith(".vue")
    )
    if authoritative_ui_paths and not explicit_ui_evidence:
        target_ui_paths = authoritative_ui_paths
        target_ui_found = True
        evidence.extend(
            {
                "project": str(project.get("name") or ""),
                "kind": "target_ui",
                "path": str(path),
                "terms": [],
                "reason": "用户确认的代码定位锚点已命中页面/组件，作为行为需求的受控入口。",
                "score": 120,
                "snippet": "",
            }
            for project in selected_projects
            if isinstance(project, dict)
            and project.get("role") == "frontend"
            and project.get("authoritative_code_match") is True
            for path in project.get("authoritative_code_paths") or []
            if str(path).replace("\\", "/").endswith(".vue")
        )
    endpoint_field_contracts = matching_endpoint_field_contracts(
        service_graph=service_graph or {},
        target_field=str(target.get("field") or ""),
    )
    endpoint_field_verified = bool(endpoint_field_contracts) and all(
        item.get("status") == "verified" for item in endpoint_field_contracts
    )
    bounded_display_evidence = bool(
        target.get("kind") == "explicit_display_field"
        and explicit_ui_evidence
        and (service_graph or {}).get("branches")
    )

    if target.get("kind") in {"multi_service_feature", "behavior_change"}:
        evidence = service_graph_evidence_items(service_graph or {})
        target_ui_paths = [
            path
            for item in evidence
            if item.get("kind") == "target_ui"
            for path in [str(item.get("path") or "")]
            if path
        ]
        target_ui_found = bool(target_ui_paths)
    elif bounded_display_evidence:
        graph_evidence = service_graph_evidence_items(service_graph or {})
        evidence.extend(graph_evidence)
        field_returned = endpoint_field_verified
        field_source_paths.extend(
            str(path)
            for contract in endpoint_field_contracts
            for path in contract.get("evidence_paths") or []
            if str(path).strip()
        )
    else:
        for project in selected_projects:
            project_path = Path(str(project.get("path") or ""))
            if not project_path.exists():
                continue
            for item in scan_project_for_terms(project_path=project_path, aliases=aliases, ui_terms=ui_terms):
                if (
                    explicit_ui_evidence
                    and item.get("kind") == "target_ui"
                    and str(item.get("path") or "") not in target_ui_paths
                ):
                    continue
                evidence.append({"project": project_path.name, **item})
                path = str(item.get("path") or "")
                reason = str(item.get("reason") or "")
                if item.get("kind") == "target_ui":
                    target_ui_found = True
                    target_ui_paths.append(path)
                if item.get("kind") == "field_source" and proves_backend_field_source(item=item, project=project):
                    field_returned = True
                    field_source_paths.append(path)
                if any(suffix in path for suffix in [".java", ".ts", ".d.ts"]) and any(alias in reason or alias in str(item.get("snippet") or "") for alias in aliases):
                    field_returned = True
                    field_source_paths.append(path)

    response_contract = analyze_response_contract(target=target, selected_projects=selected_projects)
    query_chain: dict = {}
    enum_options = [option.to_dict() for option in discovery.enum_options]
    if target.get("kind") == "discovered_stored_filter":
        graph_ui_nodes = discovery.find_nodes(kind="ui", identifier=target.get("field"))
        graph_field_nodes = discovery.find_nodes(kind="stored_field", identifier=target.get("field"))
        target_ui_found = bool(graph_ui_nodes)
        field_returned = bool(graph_field_nodes)
        target_ui_paths = unique_keep_order(
            [node.path for node in graph_ui_nodes] + target_ui_paths
        )
        field_source_paths = unique_keep_order(
            [node.path for node in graph_field_nodes] + field_source_paths
        )
        evidence = discovery_evidence_items(discovery) + evidence
        query_chain = build_discovery_query_chain(
            discovery=discovery,
            selected_projects=selected_projects,
            target_field=str(target.get("field") or ""),
            service_graph=service_graph,
        )
    if query_chain.get("stored_field_found"):
        field_returned = True
    if target.get("field") == "预交金备注":
        field_returned = bool(
            response_contract.get("response_contract_has_target_field")
            or response_contract.get("public_api_has_target_field")
        )
        field_source_paths = list(response_contract.get("field_source_paths") or [])

    consistency = build_field_identity_consistency(
        target_field=str(target.get("field") or ""),
        raw_discovery_target_field=discovery_target_field,
        endpoint_field_verified=endpoint_field_verified,
    )
    normalized_discovery_target = str(consistency.get("normalized_discovery_target_field") or "")
    if consistency.get("status") == "conflict":
        field_returned = False

    return {
        "target_field": target.get("field") or "",
        "field_kind": target.get("kind") or "",
        "aliases": aliases,
        "ui_terms": ui_terms,
        "field_returned": field_returned,
        "target_ui_found": target_ui_found,
        "target_ui_paths": unique_keep_order(target_ui_paths),
        "field_source_paths": unique_keep_order(field_source_paths),
        "response_contract": response_contract,
        "query_chain": query_chain,
        "evidence": evidence[:30],
        "evidence_graph": discovery.graph.to_dict(),
        "raw_discovery_target_field": discovery_target_field,
        "discovery_target_field": normalized_discovery_target,
        "field_identity_consistency": consistency,
        "discovery_unknowns": list(discovery.unknowns),
        "proven_rules": list(discovery.proven_rules),
        "enum_options": enum_options,
        "default_value_precedence": build_default_value_precedence_provenance(
            default_value_precedence=default_value_precedence,
            target=target,
            selected_projects=selected_projects,
            source_scope_paths=choose_allowed_paths(
                provenance={"target_ui_paths": unique_keep_order(target_ui_paths)}
            ),
        ),
        "authoritative_code_locators": list(
            select_specific_code_locator_terms(
                extract_authoritative_code_locator_terms(authoritative_code_locators)
            )
        ),
    }


def build_default_value_precedence_provenance(
    *,
    default_value_precedence: dict | None,
    target: dict,
    selected_projects: list[dict],
    source_scope_paths: list[str] | None = None,
) -> dict:
    """Find source-level proof for a configurable default-value chain.

    A requirement's wording authorizes source tracing, not a patch.  The four
    sources must have concrete code evidence and one initialization path must
    prove their declared order before code generation can continue.
    """
    if not isinstance(default_value_precedence, dict) or not default_value_precedence.get("required"):
        return {"required": False, "status": "not_required", "sources": [], "blockers": []}
    if not default_value_precedence_is_resolved(default_value_precedence):
        return {
            "required": True,
            "status": "blocked_requirement",
            "sources": [],
            "blockers": ["默认值业务优先级未完整确认，不能开始四层源码取证。"],
        }

    aliases = [str(item) for item in (target.get("aliases") or []) if str(item).strip()]
    source_evidence: dict[str, list[dict]] = {source: [] for source in DEFAULT_VALUE_PRECEDENCE_SOURCES}
    chain_evidence: list[dict] = []
    scoped_source_paths: list[str] = []
    for project in selected_projects:
        project_path = Path(str(project.get("path") or ""))
        if not project_path.exists():
            continue
        project_name = str(project.get("name") or project_path.name)
        source_paths = default_value_source_scope_paths(
            project_path=project_path,
            source_scope_paths=source_scope_paths or [],
        )
        scoped_source_paths.extend(
            f"{project_name}:{safe_relative(path, project_path)}" for path in source_paths
        )
        for path in source_paths:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    text = path.read_text(encoding="gb18030")
                except UnicodeDecodeError:
                    continue
            except OSError:
                continue
            if aliases and not any(alias in text for alias in aliases):
                continue
            positions: dict[str, int] = {}
            for source in DEFAULT_VALUE_PRECEDENCE_SOURCES:
                match = find_default_value_source_match(
                    source=source,
                    text=text,
                    aliases=aliases,
                )
                if match is None:
                    continue
                positions[source] = match.start()
                source_evidence[source].append(
                    {
                        "project": project_name,
                        "path": safe_relative(path, project_path),
                        "snippet": snippet_for_term(text, match.group(0)),
                    }
                )
            if set(positions) == set(DEFAULT_VALUE_PRECEDENCE_SOURCES):
                ordered = [positions[source] for source in DEFAULT_VALUE_PRECEDENCE_SOURCES]
                if ordered == sorted(ordered):
                    chain_evidence.append(
                        {
                            "project": project_name,
                            "path": safe_relative(path, project_path),
                            "source_order": list(DEFAULT_VALUE_PRECEDENCE_SOURCES),
                        }
                    )
    sources = [
        {
            "source": source,
            "status": "verified" if source_evidence[source] else "missing",
            "evidence": source_evidence[source][:8],
        }
        for source in DEFAULT_VALUE_PRECEDENCE_SOURCES
    ]
    blockers = [
        f"未定位 {source} 的源码证据。"
        for source in DEFAULT_VALUE_PRECEDENCE_SOURCES
        if not source_evidence[source]
    ]
    if not chain_evidence:
        blockers.append("未在同一初始化链路证明通用表单、参数、页面硬编码和无默认值的实际覆盖顺序。")
    return {
        "required": True,
        "status": "verified" if not blockers else "blocked",
        "sources": sources,
        "precedence_chain": chain_evidence[:8],
        "source_scope_paths": unique_keep_order(scoped_source_paths),
        "blockers": blockers,
    }


def find_default_value_source_match(*, source: str, text: str, aliases: list[str]) -> re.Match | None:
    if source == "page_hardcoded_default" and aliases:
        field_pattern = re.compile(
            r"(?:" + "|".join(re.escape(alias) for alias in aliases) + r")\s*(?:[:=]|\|\|)",
            re.IGNORECASE,
        )
        matched = field_pattern.search(text)
        if matched is not None:
            return matched
    return DEFAULT_VALUE_SOURCE_CODE_PATTERNS[source].search(text)


def default_value_source_scope_paths(*, project_path: Path, source_scope_paths: list[str]) -> list[Path]:
    """Read only the selected page(s) and their imports, never sibling pages.

    Default flags are commonly reused across registration, scheduling and
    settlement views.  A same-named flag elsewhere is not provenance for the
    selected page, so a broad repository search would be unsafe.
    """
    root = project_path.resolve()
    queue: list[Path] = []
    for relative_path in source_scope_paths:
        candidate = root / str(relative_path).strip()
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            queue.append(resolved)
    seen: set[Path] = set()
    while queue and len(seen) < MAX_FRONTEND_DEPENDENCY_FILES:
        path = queue.pop(0).resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        text = read_source_text(path)
        if text is None:
            continue
        for source in IMPORT_SOURCE_RE.findall(text):
            imported = resolve_frontend_import(
                project_path=root,
                source_path=path,
                import_source=source,
            )
            if imported is not None and imported not in seen:
                queue.append(imported)
    return sorted(seen, key=lambda item: safe_relative(item, root))


def infer_discovery_target_field(discovery: DiscoveryResult) -> str:
    """Choose a field only when the evidence graph connects a UI binding to storage."""
    return discovery.target_field


def discovery_evidence_items(discovery: DiscoveryResult) -> list[dict]:
    return [
        {
            "project": node.project,
            "kind": "target_ui" if node.kind == "ui" else "field_source",
            "path": node.path,
            "reason": "通用证据图定位到的源码节点。",
            "matched_terms": list(node.matched_terms),
            "snippet": node.snippet,
            "score": 80 if node.kind == "stored_field" else 70,
        }
        for node in discovery.graph.nodes
        if node.kind in {"ui", "stored_field", "controller", "repository", "service"}
    ]


def service_graph_evidence_items(service_graph: dict) -> list[dict]:
    """Expose only branch-backed files for broad features.

    A multi-service requirement has no single target field. Generic full-project
    keyword scans therefore produce misleading pages from neighboring modules;
    the service graph is the authoritative source until a branch is closed.
    """
    evidence: list[dict] = []
    for node in service_graph.get("nodes") or []:
        project = str(node.get("project") or "")
        role = str(node.get("role") or "")
        kind = "target_ui" if role == "frontend" else "service_graph_source"
        qualified_paths = node.get("entry_paths") or node.get("evidence_paths") or []
        for qualified_path in qualified_paths:
            value = str(qualified_path or "")
            node_project, separator, relative_path = value.partition(":")
            if not separator:
                node_project, relative_path = project, value
            evidence.append(
                {
                    "project": node_project or project,
                    "kind": kind,
                    "path": relative_path,
                    "reason": "服务图已定位到需求相关入口或跨服务证据。",
                    "snippet": "",
                    "score": 90 if kind == "target_ui" else 80,
                }
            )
    for branch in service_graph.get("branches") or []:
        field_contract = branch.get("field_contract") or {}
        for qualified_path in field_contract.get("evidence_paths") or []:
            project, separator, relative_path = str(qualified_path or "").partition(":")
            evidence.append(
                {
                    "project": project if separator else str(branch.get("target_project") or ""),
                    "kind": "endpoint_response_field",
                    "path": relative_path if separator else str(qualified_path or ""),
                    "reason": "页面实际接口的响应 DTO 已命中目标字段。",
                    "snippet": "",
                    "score": 100,
                }
            )
    return unique_evidence_items(evidence)


def matching_endpoint_field_contracts(*, service_graph: dict, target_field: str) -> list[dict]:
    if not target_field:
        return []
    return [
        dict(branch.get("field_contract") or {})
        for branch in service_graph.get("branches") or []
        if str((branch.get("field_contract") or {}).get("target_field") or "") == target_field
    ]


def build_field_identity_consistency(
    *,
    target_field: str,
    raw_discovery_target_field: str,
    endpoint_field_verified: bool,
) -> dict:
    if not target_field:
        return {
            "status": "not_applicable",
            "normalized_discovery_target_field": raw_discovery_target_field,
            "resolution": "no_explicit_target_field",
        }
    if not raw_discovery_target_field or raw_discovery_target_field == target_field:
        return {
            "status": "verified",
            "normalized_discovery_target_field": target_field,
            "resolution": "same_field_or_no_competing_discovery",
        }
    if endpoint_field_verified:
        return {
            "status": "verified",
            "normalized_discovery_target_field": target_field,
            "resolution": "endpoint_response_contract_overrides_broad_discovery",
            "discarded_discovery_target_field": raw_discovery_target_field,
        }
    return {
        "status": "conflict",
        "normalized_discovery_target_field": raw_discovery_target_field,
        "resolution": "unresolved_field_identity_conflict",
        "explicit_target_field": target_field,
        "competing_discovery_target_field": raw_discovery_target_field,
    }


def unique_evidence_items(items: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (
            str(item.get("project") or ""),
            str(item.get("path") or ""),
            str(item.get("kind") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def build_discovery_query_chain(
    *,
    discovery: DiscoveryResult,
    selected_projects: list[dict],
    target_field: str,
    service_graph: dict | None = None,
) -> dict:
    project_roles = {
        str(project.get("name") or Path(str(project.get("path") or "")).name): str(project.get("role") or "")
        for project in selected_projects
    }
    layers = {"frontend": [], "bff": [], "service": [], "stored_field": []}
    field_candidate = next(
        (candidate for candidate in discovery.field_candidates if candidate.field == target_field),
        None,
    )
    target_ui_paths = set(field_candidate.ui_paths if field_candidate else ())
    request_edges = discovery.find_edges(kind="request_flow")
    endpoint_scores: list[tuple[int, str]] = []
    for endpoint in field_candidate.endpoints if field_candidate else ():
        edges = [edge for edge in request_edges if edge.identifier == endpoint]
        if not edges:
            continue
        touches_target_ui = any(
            edge.source_path in target_ui_paths or edge.target_path in target_ui_paths
            for edge in edges
        )
        if not touches_target_ui:
            continue
        crosses_project = any(
            edge.source_path.split(":", 1)[0] != edge.target_path.split(":", 1)[0]
            for edge in edges
        )
        endpoint_score = 100 if crosses_project else 0
        endpoint_score += 40 if touches_target_ui else 0
        endpoint_score += 10 * sum(token in endpoint.lower() for token in ("query", "list", "page"))
        endpoint_scores.append((endpoint_score, endpoint))
    endpoints = [
        endpoint
        for _score, endpoint in sorted(endpoint_scores, key=lambda item: (-item[0], item[1]))
    ]
    selected_endpoint = endpoints[0] if endpoints else ""
    for node in discovery.graph.nodes:
        qualified_path = f"{node.project}:{node.path}"
        if node.kind == "ui" and target_field in node.identifiers:
            layers["frontend"].append(qualified_path)
        if node.kind == "stored_field" and target_field in node.identifiers:
            layers["stored_field"].append(qualified_path)
    for name, paths in layers.items():
        layers[name] = unique_keep_order(paths)

    graph_branches = list((service_graph or {}).get("branches") or [])
    graph_branch_operations = {
        str(branch.get("endpoint") or "").rsplit("/", 1)[-1]: branch
        for branch in graph_branches
    }
    graph_unresolved_operations = {
        str(item.get("endpoint") or "").rsplit("/", 1)[-1]: item
        for item in (service_graph or {}).get("unresolved_endpoints") or []
    }
    ui_request_operations = unique_keep_order(
        request
        for node in discovery.graph.nodes
        if node.kind == "ui" and target_field in node.identifiers
        for request in node.request_identifiers
    )
    graph_candidate_operations = unique_keep_order([*endpoints, *ui_request_operations])
    relevant_operations = [
        endpoint
        for endpoint in graph_candidate_operations
        if endpoint in graph_branch_operations or endpoint in graph_unresolved_operations
    ]
    branch_results: list[dict] = []
    unresolved_branches: list[dict] = []
    if (graph_branches or graph_unresolved_operations) and relevant_operations:
        for operation in relevant_operations:
            if operation not in graph_branch_operations:
                unresolved_branches.append(
                    {**graph_unresolved_operations[operation], "endpoint": operation}
                )
                continue
            branch = graph_branch_operations[operation]
            target_project = str(branch.get("target_project") or "")
            target_role = project_roles.get(target_project, "")
            target_path = str(branch.get("target_path") or "")
            if target_project.startswith("df-bff-"):
                layers["bff"].append(target_path)
            elif target_role in {"backend", "api"}:
                layers["service"].append(target_path)
            branch_results.append(
                {
                    "endpoint": operation,
                    "url": branch.get("endpoint"),
                    "source_project": branch.get("source_project"),
                    "target_project": target_project,
                    "target_path": target_path,
                    "scope": branch.get("scope"),
                    "verified": bool(branch.get("controller_verified")),
                }
            )
        for name in ("bff", "service"):
            layers[name] = unique_keep_order(layers[name])
        requires_bff = any(
            branch["target_project"].startswith("df-bff-")
            for branch in branch_results
        )
        requires_service = any(
            not branch["target_project"].startswith("df-bff-")
            for branch in branch_results
        )
        complete = bool(
            layers["frontend"]
            and layers["stored_field"]
            and branch_results
            and not unresolved_branches
            and all(branch["verified"] for branch in branch_results)
            and (not requires_bff or layers["bff"])
            and (not requires_service or layers["service"])
        )
        required_projects = unique_keep_order(
            branch["target_project"] for branch in branch_results if branch["target_project"]
        )
        return {
            "endpoint": selected_endpoint,
            "branches": branch_results,
            "required_projects": required_projects,
            "unresolved_branches": unresolved_branches,
            "layers": layers,
            "status": "complete" if complete else "incomplete",
            "stored_field_found": bool(layers["stored_field"]),
        }

    # Backward-compatible fallback for projects whose frontend only exposes a
    # named request function and does not contain a literal gateway URL.
    for node in discovery.graph.nodes:
        qualified_path = f"{node.project}:{node.path}"
        role = project_roles.get(node.project, "")
        if node.kind != "controller" or selected_endpoint not in node.request_identifiers:
            continue
        if node.project.startswith("df-bff-"):
            layers["bff"].append(qualified_path)
        elif role in {"backend", "api"}:
            layers["service"].append(qualified_path)
    for name, paths in layers.items():
        layers[name] = unique_keep_order(paths)
    requires_bff = any(
        str(project.get("name") or Path(str(project.get("path") or "")).name).startswith("df-bff-")
        for project in selected_projects
    )
    requires_service = any(
        str(project.get("role") or "") in {"backend", "api"}
        and not str(project.get("name") or Path(str(project.get("path") or "")).name).startswith("df-bff-")
        for project in selected_projects
    )
    complete = bool(
        layers["frontend"]
        and layers["stored_field"]
        and selected_endpoint
        and (not requires_bff or layers["bff"])
        and (not requires_service or layers["service"])
    )
    return {
        "endpoint": selected_endpoint,
        "branches": [],
        "required_projects": [],
        "unresolved_branches": [],
        "layers": layers,
        "status": "complete" if complete else "incomplete",
        "stored_field_found": bool(layers["stored_field"]),
    }


def decide_implementation(*, combined_text: str, provenance: dict, selected_projects: list[dict]) -> dict:
    if provenance.get("field_kind") == "multi_service_feature":
        return decide_multi_service_feature(
            combined_text=combined_text,
            provenance=provenance,
        )
    if provenance.get("field_kind") == "discovered_stored_filter":
        return decide_discovered_stored_filter(
            combined_text=combined_text,
            provenance=provenance,
        )
    if provenance.get("field_kind") == "behavior_change":
        return decide_behavior_change(
            combined_text=combined_text,
            provenance=provenance,
            selected_projects=selected_projects,
        )

    default_value_precedence = provenance.get("default_value_precedence") or {}
    if default_value_precedence.get("required"):
        return decide_default_value_precedence_implementation(
            provenance=provenance,
            selected_projects=selected_projects,
        )

    blockers: list[str] = []
    target_field = str(provenance.get("target_field") or "")
    allowed_paths = choose_allowed_paths(provenance=provenance)
    field_returned = bool(provenance.get("field_returned"))
    target_ui_found = bool(provenance.get("target_ui_found"))

    if not target_field:
        blockers.append("未能从需求中识别目标展示字段，不能安全生成 patch。")
    response_contract = provenance.get("response_contract") or {}
    if target_field and not field_returned:
        if response_contract.get("backend_model_has_target_field") and response_contract.get("response_contract_paths"):
            blockers.append(f"已发现后端实体存在 {target_field}，但尚未证明实际接口返回该字段；不能只改前端加空展示。")
            if response_contract.get("contract_inconsistent"):
                blockers.append("公共 API 定义与实际服务返回契约不一致，需先确认以实际运行服务为准。")
        else:
            blockers.append(f"尚未证明目标展示字段 {target_field} 已由接口、DTO 或服务模型提供；不能只改前端加空展示。")
    if not target_ui_found:
        blockers.append("尚未定位需求指定的目标页面或组件，不能从大仓相似字段中猜测改动位置。")
    if not selected_projects:
        blockers.append("未选择到可用业务项目。")
    if not allowed_paths:
        blockers.append("未形成受控 patch 白名单路径。")
    field_identity_consistency = provenance.get("field_identity_consistency") or {}
    if field_identity_consistency.get("status") == "conflict":
        blockers.append(
            "显式目标字段与通用发现字段冲突，且尚无页面实际接口响应契约可完成归一；不能生成 patch。"
        )

    can_patch = not blockers
    if can_patch and field_returned:
        change_type = "frontend_display_only"
    elif response_contract.get("backend_model_has_target_field") and response_contract.get("response_contract_paths"):
        change_type = "backend_contract_required"
    else:
        change_type = "blocked_needs_evidence"
    summary = (
        "字段来源和目标前端页面已被代码证据支持，可按前端展示列做最小改动。"
        if can_patch
        else (
            "字段在后端实体中存在，但返回契约缺少该字段，Harness 已阻断前端单文件 patch；应先补后端 DTO/接口契约，再加前端列。"
            if change_type == "backend_contract_required"
            else "技术证据不足，Harness 已阻断自动 patch；需要补充字段来源或目标页面证据。"
        )
    )
    return {
        "can_patch": can_patch,
        "change_type": change_type,
        "summary": summary,
        "allowed_paths": allowed_paths,
        "blockers": blockers,
        "rules": [
            "只允许目标页面或组件的展示改动，不扩展到筛选、保存、收费、结算或其他业务逻辑。",
            "文件命名、列配置和组件写法必须遵循目标页面现有代码风格。",
            "如果后续证据证明接口未返回目标字段，应重新形成跨层改动合同，不得伪造前端字段。",
        ],
    }


def decide_behavior_change(*, combined_text: str, provenance: dict, selected_projects: list[dict]) -> dict:
    """Keep behavior fixes separate from display-field decisions.

    A closed error chain proves where the current behavior occurs, but it does
    not by itself prove the business predicate that selects the replacement
    branch. For high-risk refund/settlement flows, keep patch generation closed
    until that predicate, direct-refund branch, and adjacent behavior are traced.
    """
    service_graph = provenance.get("service_graph") or {}
    branches = [
        branch
        for branch in service_graph.get("branches") or []
        if isinstance(branch, dict)
    ]
    allowed_paths = choose_allowed_paths(provenance=provenance)
    locators = [
        str(item).strip()
        for item in provenance.get("authoritative_code_locators") or []
        if str(item).strip()
    ]
    current_call_chain = [
        {
            "source_project": str(branch.get("source_project") or ""),
            "source_paths": unique_keep_order(
                str(path)
                for path in (branch.get("source_paths") or [branch.get("source_path")])
                if str(path).strip()
            ),
            "endpoint": str(branch.get("endpoint") or ""),
            "target_project": str(branch.get("target_project") or ""),
            "target_path": str(branch.get("target_path") or ""),
            "entry_paths": unique_keep_order(
                str(path) for path in branch.get("entry_paths") or [] if str(path).strip()
            ),
            "controller_verified": bool(branch.get("controller_verified")),
        }
        for branch in branches
    ]
    blockers: list[str] = []
    if service_graph.get("status") != "evidence_ready" or service_graph.get("unresolved_endpoints"):
        blockers.append("当前行为入口与前后端接口链尚未闭合，不能安全生成行为 patch。")
    if not allowed_paths:
        blockers.append("尚未形成受控行为入口白名单，不能从相似退费页面猜测改动位置。")
    blockers.extend(
        [
            "已定位当前医保预结算调用，但尚未证明‘单药品或全部费用已申请退费且无未执行费用’对应的全退判定字段、计算边界和触发时机。",
            "需求要求直接走医保退费；当前证据尚未形成从全退判定到现有直接退费分支的可修改合同。",
            "部分退费、移动医保、自费及其他非全退路径的保持不变边界尚未逐项核验。",
        ]
    )
    return {
        "can_patch": False,
        "change_type": "blocked_behavior_change_contract",
        "summary": (
            "已识别为医保退费行为变更；当前错误入口和源码调用链已定位，"
            "但全退判定、目标直退分支及相邻路径保护尚未形成可修改合同，Harness 已阻断自动 patch。"
        ),
        "allowed_paths": allowed_paths,
        "blockers": unique_keep_order(blockers),
        "behavior_contract": {
            "current_behavior": "退费操作当前进入门诊医保预结算链路，并可能触发‘患者在院不能进行医保登记’。",
            "requested_behavior": "仅当全退条件成立时，不再调用门诊医保预结算，直接进入医保退费。",
            "full_refund_condition": "单药品，或全部费用已申请退费且没有未执行费用。",
            "preserve_behavior": [
                "部分退费仍按原有预结算/退费规则处理。",
                "移动医保、自费及其他非全退场景不得被全退分支误伤。",
            ],
            "current_call_chain": current_call_chain,
            "confirmed_code_locators": locators,
            "next_readonly_actions": [
                "在已定位的退费页面和 menZhenTfYjs 分支中确认全退判定的真实字段与计算来源。",
                "继续核验现有 menZhenTf 直退路径的入参、医保接口和错误处理。",
                "为全退、部分退和无未执行费用边界建立针对性回归场景后，才重新评估是否允许 patch。",
            ],
        },
        "rules": [
            "只允许围绕已闭合的退费调用链形成后续变更合同，不扩展到无关医保登记或住院流程。",
            "不得把错误文案、医生申请退费背景或相似页面命中当成全退判定证据。",
            "不得删除或绕过部分退费、移动医保、自费及异常兜底逻辑。",
            "只读调查期间不生成业务代码 patch、不提交、不推送、不写入云效。",
        ],
    }


def decide_default_value_precedence_implementation(*, provenance: dict, selected_projects: list[dict]) -> dict:
    default_value_precedence = provenance.get("default_value_precedence") or {}
    blockers = list(default_value_precedence.get("blockers") or [])
    target_field = str(provenance.get("target_field") or "")
    allowed_paths = choose_allowed_paths(provenance=provenance)
    if not target_field:
        blockers.append("未能从需求和源码中识别默认值字段，不能生成配置优先级 patch。")
    if not provenance.get("target_ui_found"):
        blockers.append("未定位默认值对应的目标页面或组件，不能将通用配置误改到无关表单。")
    if not selected_projects:
        blockers.append("未选择到可用业务项目。")
    if not allowed_paths:
        blockers.append("未形成受控 patch 白名单路径。")
    blockers = unique_keep_order(str(item) for item in blockers if str(item).strip())
    can_patch = not blockers
    return {
        "can_patch": can_patch,
        "change_type": "default_value_precedence" if can_patch else "blocked_default_value_precedence",
        "summary": (
            "四层默认值来源及覆盖顺序均已由同一初始化链路证明，可按受控路径实现。"
            if can_patch
            else "默认值来源优先级尚未形成完整源码证据，Harness 将继续自动追踪；在闭合前不生成 patch。"
        ),
        "allowed_paths": allowed_paths,
        "blockers": blockers,
        "rules": [
            "通用表单设置优先于参数设置；参数设置优先于页面硬编码默认值。",
            "前三者均没有值时不得为字段伪造默认值或覆盖用户已有输入。",
            "修改前必须保留页面现有的新建、清屏、读卡和用户手工选择触发边界。",
        ],
    }


def decide_multi_service_feature(*, combined_text: str, provenance: dict) -> dict:
    service_graph = provenance.get("service_graph") or {}
    blockers = [
        "需求包含多个页面、操作和数据字段，不能压缩成单字段 patch。",
    ]
    unresolved = service_graph.get("unresolved_endpoints") or []
    boundary_findings = [
        item for item in service_graph.get("boundary_findings") or []
        if isinstance(item, dict) and item.get("status") == "conflict"
    ]
    # A conflict record can contain an architecture decision that Harness has
    # already resolved from local build/API evidence.  Keep that record in the
    # report, but do not turn it into a user question or an automatic-patch
    # blocker.  Genuine direct-table access (without a resolved decision) and
    # unresolved API choices remain blocking.
    unresolved_boundary_findings = [
        item for item in boundary_findings
        if item.get("architecture_decision") != "auto_resolved"
        or item.get("requires_code_change")
    ]
    architecture_findings = [
        item for item in boundary_findings
        if item.get("architecture_decision")
    ]
    business_rule_findings = [
        item for item in service_graph.get("business_rule_findings") or []
        if isinstance(item, dict) and item.get("status") == "conflict"
    ]
    candidate_change_targets = []
    for branch in service_graph.get("branches") or []:
        scope = str(branch.get("scope") or "")
        if scope not in {"change_required", "candidate_change"}:
            continue
        candidate_change_targets.append(
            {
                "scope": scope,
                "source_project": str(branch.get("source_project") or ""),
                "source_paths": unique_keep_order(
                    [
                        str(path)
                        for path in (branch.get("source_paths") or [branch.get("source_path")])
                        if str(path).strip()
                    ]
                ),
                "entry_paths": unique_keep_order(
                    [
                        str(path)
                        for path in (branch.get("entry_paths") or [])
                        if str(path).strip()
                    ]
                ),
                "endpoint": str(branch.get("endpoint") or ""),
                "target_project": str(branch.get("target_project") or ""),
                "target_path": str(branch.get("target_path") or ""),
                "controller_verified": bool(branch.get("controller_verified")),
            }
        )
    candidate_change_targets = sorted(
        candidate_change_targets,
        key=lambda item: (
            item["scope"],
            item["source_project"],
            item["endpoint"],
            item["target_project"],
        ),
    )
    plan_status = "ready_for_contract" if service_graph.get("status") == "evidence_ready" else "blocked_by_graph"
    if service_graph.get("status") != "evidence_ready":
        blockers.append(
            f"多服务证据图尚未闭合：{len(unresolved)} 个接口未解析，需先补齐服务边界和调用证据。"
        )
    for finding in unresolved_boundary_findings:
        blockers.append("数据来源边界未闭合：" + str(finding.get("message") or "存在底层表直连证据。"))
    for finding in business_rule_findings:
        blockers.append("审批属性规则冲突：" + str(finding.get("message") or "存在非严格标志判断。"))
    plan_status = "blocked_by_boundary" if unresolved_boundary_findings or business_rule_findings else plan_status
    architecture_decision = "auto_resolved" if any(
        item.get("architecture_decision") == "auto_resolved" for item in architecture_findings
    ) else ("needs_user_choice" if architecture_findings else "not_applicable")
    architecture_options = [
        option
        for finding in architecture_findings
        for option in (finding.get("architecture_options") or [])
        if isinstance(option, dict)
    ]
    architecture_requirements = []
    for finding in architecture_findings:
        finding_type = str(finding.get("type") or "")
        if finding_type == "direct_cross_schema_access":
            architecture_requirements.append(
                {
                    "id": "owner_service_api_through_bff",
                    "status": "target_required",
                    "label": "把 yibaogl 的 gy_shoufeixm 直查改为所有者公共 API，经 BFF 暴露给医保聚合",
                    "owner_projects": ["df-mic-jichufw", "df-bff-jichufw", "df-mic-yibaogl"],
                    "change_surfaces": [
                        "df-mic-jichufw: ShouFeiXmApi/DTO 契约",
                        "df-bff-jichufw: 原始目录公共接口与路由",
                        "df-mic-yibaogl: 目录查询/映射实现，移除 gy_shoufeixm 直查",
                    ],
                    "evidence_paths": sorted(
                        set(
                            list(finding.get("owner_evidence") or [])
                            + list(finding.get("consumer_evidence") or [])
                        )
                    )[:48],
                    "endpoint_contract_status": "not_proven",
                    "existing_api_contracts": list(
                        (finding.get("architecture_evidence") or {}).get("bff_api_contracts") or []
                    ),
                    "contract_proposal": finding.get("contract_proposal") or {},
                    "rule": "当前只证明服务所有权和已有 API 符号，不虚构新的 BFF URL；必须先形成 API/DTO/分页契约目标，才允许进入 worktree 改码。",
                }
            )
        elif finding_type == "multi_source_right_panel_boundary":
            architecture_requirements.append(
                {
                    "id": "bff_raw_sources_yibaogl_enrichment",
                    "status": "target_required",
                    "label": "右侧医院目录由 BFF 提供药品/收费项目原始目录，医保服务做多对照和审批属性投影",
                    "owner_projects": ["df-bff-jichufw", "df-mic-jichufw", "df-mic-yaokufang", "df-mic-yibaogl"],
                    "change_surfaces": [
                        "df-bff-jichufw: 原始目录 API（收费项目 + 药品字典）",
                        "df-mic-yibaogl: 1:N 医保对照、字典字段和四个审批标志的聚合/保存",
                        "df-web-yibaogl: 右侧表格和按钮交互只消费统一投影契约",
                    ],
                    "evidence_paths": sorted(
                        set(
                            list(finding.get("bff_evidence") or [])
                            + list(finding.get("consumer_evidence") or [])
                        )
                    )[:48],
                    "existing_api_candidates": {
                        "charge": list(
                            (finding.get("architecture_evidence") or {}).get("existing_charge_routes") or []
                        ),
                        "drug": list(
                            (finding.get("architecture_evidence") or {}).get("existing_drug_routes") or []
                        ),
                    },
                    "existing_api_contracts": {
                        "charge": list(
                            (finding.get("architecture_evidence") or {}).get("existing_charge_contracts") or []
                        ),
                        "drug": list(
                            (finding.get("architecture_evidence") or {}).get("existing_drug_contracts") or []
                        ),
                        "consumer": list(
                            (finding.get("architecture_evidence") or {}).get("consumer_contracts") or []
                        ),
                    },
                    "contract_gap": list(
                        (finding.get("architecture_evidence") or {}).get("contract_gap") or []
                    ),
                    "endpoint_contract_status": "not_proven",
                    "contract_proposal": finding.get("contract_proposal") or {},
                    "rule": "现有分类树 BFF 证据不能当成右侧分页接口；未证明原始目录分页/筛选契约前不得自动生成 BFF URL 或后端参数。",
                }
            )
    return {
        "can_patch": False,
        "change_type": "multi_service_feature",
        "summary": (
            "已识别为多功能、多服务需求；Harness 只输出候选项目、接口分支和未闭合证据，"
            "不把它误判为单个枚举字段筛选。"
        ),
        "allowed_paths": [],
        "blockers": blockers,
        "candidate_change_targets": candidate_change_targets,
        "unresolved_endpoints": unresolved,
        "change_plan": {
            "status": plan_status,
            "target_count": len(candidate_change_targets),
            "unresolved_count": len(unresolved),
            "architecture_decision": architecture_decision,
            "recommended_architecture_option_id": next(
                (
                    str(item.get("recommended_option_id") or "")
                    for item in architecture_findings
                    if item.get("recommended_option_id")
                ),
                "",
            ),
            "architecture_evidence": [
                item.get("architecture_evidence") or {}
                for item in architecture_findings
                if item.get("architecture_evidence")
            ],
            "architecture_options": architecture_options,
            "architecture_requirements": architecture_requirements,
            "contract_proposals": [
                item.get("contract_proposal") or {}
                for item in architecture_requirements
                if item.get("contract_proposal")
            ],
            "rule": "候选目标仅用于生成受控改动合同；未经过需求契约、治理和运行时验证前，不自动写入业务仓库。",
        },
        "rules": [
            "必须按页面、接口、服务、公共 API 和数据/历史规则拆分受控改动合同。",
            "任一服务分支未闭合时，不自动生成跨服务 patch。",
            "当前输出属于代码级只读证据，不等同于运行时业务验收。",
        ],
    }


def choose_allowed_paths(*, provenance: dict) -> list[str]:
    paths = list(provenance.get("target_ui_paths") or [])
    if not paths:
        return []
    evidence_scores: dict[str, int] = {}
    for item in provenance.get("evidence") or []:
        if not isinstance(item, dict) or item.get("kind") not in {"target_ui", "explicit_target_ui"}:
            continue
        path = str(item.get("path") or "")
        evidence_scores[path] = max(evidence_scores.get(path, 0), int(item.get("score") or 0))
    ranked = sorted(
        unique_keep_order(paths),
        key=lambda path: (-evidence_scores.get(path, 0), -target_path_score(path), path),
    )
    primary = ranked[0]
    return [primary]


def target_path_score(path: str) -> int:
    normalized = path.replace("\\", "/")
    lower = normalized.lower()
    score = 0
    preferred = ["jiesuanmx", "jieSuanMx", "jieSuan", "jiesuan", "结算", "components/base"]
    if normalized.endswith("src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue"):
        score += 140
    if "src/pages/chuYuanYw/jieSuan/dialog" in normalized:
        score += 80
    for term in preferred:
        if term in normalized or term in lower:
            score += 30
    if "components/base/jieSuanMx" in normalized or "components/base/jiesuanmx" in lower:
        score -= 60
    if "yujiaokuanxx" in lower or "yuJiaoKuanXx" in normalized:
        score -= 20
    if normalized.endswith(".vue"):
        score += 10
    if "feiYongCl/yuJiaoJin" in normalized or "feiyongcl/yujiaojin" in lower:
        score -= 40
    if "buttonBar" in normalized or "buttonbar" in lower:
        score -= 30
    return score


def scan_project_for_terms(*, project_path: Path, aliases: list[str], ui_terms: list[str]) -> list[dict]:
    results: list[dict] = []
    scanned = 0
    for path in iter_text_files(project_path):
        scanned += 1
        if scanned > MAX_SCAN_FILES_PER_PROJECT:
            break
        rel = safe_relative(path, project_path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gb18030")
            except UnicodeDecodeError:
                continue
        except OSError:
            continue

        has_field = any(term and term in text for term in aliases)
        has_ui = any(term and term in text for term in ui_terms)
        rel_lower = rel.lower()
        path_score = path_priority(rel)
        if path.suffix == ".vue" and has_ui and (has_field or not aliases):
            results.append(build_evidence_item(kind="target_ui", rel=rel, text=text, terms=aliases + ui_terms, reason="页面/组件命中需求相关业务证据。", score=90 + path_score))
        elif path.suffix == ".vue" and has_ui:
            results.append(build_evidence_item(kind="target_ui", rel=rel, text=text, terms=ui_terms, reason="页面/组件命中需求相关业务证据。", score=70 + path_score))
        elif path.suffix != ".vue" and has_field and any(part in rel_lower for part in ["api", "service", "dto", "entity", "model", "types"]):
            results.append(build_evidence_item(kind="field_source", rel=rel, text=text, terms=aliases, reason="接口、服务或类型文件命中目标字段。", score=65 + path_score))
    return sorted(results, key=lambda item: (-int(item.get("score", 0)), str(item.get("path") or "")))[:20]


def find_explicit_ui_path_evidence(*, combined_text: str, selected_projects: list[dict]) -> list[dict]:
    hints = unique_keep_order(match.group("path") for match in EXPLICIT_UI_PATH_RE.finditer(combined_text or ""))
    if not hints:
        return []
    results: list[dict] = []
    for project in selected_projects:
        if project.get("role") != "frontend":
            continue
        project_path = Path(str(project.get("path") or ""))
        if not project_path.is_dir():
            continue
        for path in iter_text_files(project_path):
            if path.suffix.lower() != ".vue":
                continue
            rel = safe_relative(path, project_path).replace("\\", "/")
            for hint in hints:
                normalized_hint = hint.replace("\\", "/")
                if rel == normalized_hint or rel.endswith("/" + normalized_hint) or path.name == Path(normalized_hint).name:
                    results.append(
                        {
                            "project": str(project.get("name") or project_path.name),
                            "kind": "explicit_target_ui",
                            "path": rel,
                            "reason": "需求证据明确点名目标 Vue 页面或组件。",
                            "matched_terms": [hint],
                            "snippet": "",
                            "score": 180 if rel == normalized_hint or rel.endswith("/" + normalized_hint) else 160,
                        }
                    )
                    break
    return sorted(results, key=lambda item: (-int(item.get("score") or 0), str(item.get("path") or "")))


def iter_text_files(project_path: Path):
    for root, dirs, filenames in os.walk(project_path):
        dirs[:] = [name for name in dirs if name not in DEFAULT_EXCLUDE_DIRS]
        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def build_evidence_item(*, kind: str, rel: str, text: str, terms: list[str], reason: str, score: int) -> dict:
    matched = [term for term in terms if term and term in text]
    snippet = ""
    for term in matched:
        snippet = snippet_for_term(text, term)
        if snippet:
            break
    return {
        "kind": kind,
        "path": rel,
        "reason": reason,
        "matched_terms": unique_keep_order(matched)[:12],
        "snippet": snippet,
        "score": score,
    }


def proves_backend_field_source(*, item: dict, project: dict) -> bool:
    """Return whether an evidence item can prove the field comes from data/API/backend."""
    path = str(item.get("path") or "").replace("\\", "/")
    lower = path.lower()
    role = str(project.get("role") or "")
    if lower.endswith(".vue"):
        return False
    if role in {"backend", "api"}:
        return True
    return any(part in lower for part in ["apis/", "/api/", "service", "dto", "entity", "model", "types"])


def analyze_response_contract(*, target: dict, selected_projects: list[dict]) -> dict:
    if target.get("field") != "预交金备注":
        return {}
    backend_model_paths: list[str] = []
    response_contract_paths: list[str] = []
    response_contract_without_field_paths: list[str] = []
    backend_field_source_paths: list[str] = []
    public_api_field_source_paths: list[str] = []
    api_endpoint_paths: list[str] = []

    for project in selected_projects:
        project_path = Path(str(project.get("path") or ""))
        if not project_path.exists():
            continue
        project_name = str(project.get("name") or project_path.name)
        for path in iter_text_files(project_path):
            rel = safe_relative(path, project_path)
            normalized = rel.replace("\\", "/")
            basename = Path(normalized).name
            lower = normalized.lower()
            if not is_relevant_yujiaokuan_contract_path(normalized):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    text = path.read_text(encoding="gb18030")
                except UnicodeDecodeError:
                    continue
            except OSError:
                continue

            has_bei_zhu = "beiZhu" in text or "备注" in text
            evidence_path = f"{project_name}:{normalized}"
            if "getListByBingRenZyIdAndJieSuanXh" in text:
                api_endpoint_paths.append(evidence_path)
            if basename in {"ZY_YuJiaoKuan.java", "DO_ZY_YuJiaoKuan.java"} and has_bei_zhu:
                backend_model_paths.append(evidence_path)
            if basename == "DTO_ZY_YuJiaoKuan.java" or normalized.endswith("jjzhuyuan.graphqls"):
                response_contract_paths.append(evidence_path)
                if has_response_contract_bei_zhu(text=text, normalized=normalized):
                    if project.get("role") == "backend":
                        backend_field_source_paths.append(evidence_path)
                    else:
                        public_api_field_source_paths.append(evidence_path)
                else:
                    response_contract_without_field_paths.append(evidence_path)

    backend_field_source_paths = unique_keep_order(backend_field_source_paths)
    public_api_field_source_paths = unique_keep_order(public_api_field_source_paths)
    response_contract_paths = unique_keep_order(response_contract_paths)
    return {
        "api_endpoint_paths": unique_keep_order(api_endpoint_paths),
        "backend_model_paths": unique_keep_order(backend_model_paths),
        "backend_model_has_target_field": bool(backend_model_paths),
        "response_contract_paths": response_contract_paths,
        "response_contract_without_field_paths": unique_keep_order(response_contract_without_field_paths),
        "backend_response_contract_field_paths": backend_field_source_paths,
        "public_api_contract_field_paths": public_api_field_source_paths,
        "response_contract_has_target_field": bool(backend_field_source_paths or public_api_field_source_paths),
        "public_api_has_target_field": bool(public_api_field_source_paths),
        "contract_inconsistent": bool(response_contract_without_field_paths and not (backend_field_source_paths or public_api_field_source_paths)),
        "field_source_paths": unique_keep_order(backend_field_source_paths + public_api_field_source_paths),
    }


def is_relevant_yujiaokuan_contract_path(path: str) -> bool:
    basename = Path(path).name
    return (
        basename in {"DTO_ZY_YuJiaoKuan.java", "ZY_YuJiaoKuan.java", "DO_ZY_YuJiaoKuan.java"}
        or path.endswith("jjzhuyuan.graphqls")
        or "YuJiaoKuanController.java" in path
        or "YuJiaoKuanServiceImpl.java" in path
    )


def has_response_contract_bei_zhu(*, text: str, normalized: str) -> bool:
    if normalized.endswith("jjzhuyuan.graphqls"):
        match = re.search(r"type\s+DTO_ZY_YuJiaoKuan\s*\{(?P<body>.*?)\n\}", text, flags=re.S)
        if not match:
            return False
        body = match.group("body")
        return "beiZhu" in body or "备注" in body
    return "private String beiZhu" in text or re.search(r"\bbeiZhu\b", text) is not None


def infer_target_field(text: str) -> dict:
    if "不收费" in text:
        return {"field": "不收费", "aliases": ["buShouFeiBz", "不收费"]}
    if "备注" in text and any(term in text for term in ["预交金", "预交款", "预交"]):
        return {"field": "预交金备注", "aliases": ["beiZhu", "备注", "remark", "memo", "yuJiao", "yuJiaoKuan", "yuJiaoJin", "预交金", "预交款"]}
    if "备注" in text:
        return {"field": "备注", "aliases": ["beiZhu", "备注", "remark", "memo"]}
    if any(term in text for term in ("显示", "展示", "加上", "增加")):
        source_match = re.search(
            r"(?P<label>[\u4e00-\u9fff]{1,8})(?:名称)?(?:来源|字段)[^。；\n]{0,28}?"
            r"(?P<field>[A-Za-z][A-Za-z0-9_]{3,})\b",
            text,
        )
        if source_match:
            label = source_match.group("label")
            field = source_match.group("field")
            return {
                "field": field,
                "kind": "explicit_display_field",
                "aliases": unique_keep_order([field, field.upper(), label, f"{label}名称"]),
            }
    return {"field": "", "aliases": []}


def infer_ui_terms(text: str) -> list[str]:
    return list(demand_project_terms(text)[:40])


def decide_discovered_stored_filter(*, combined_text: str, provenance: dict) -> dict:
    target_field = str(provenance.get("target_field") or "未命名字段")
    query_chain = provenance.get("query_chain") or {}
    enum_options = list(provenance.get("enum_options") or [])
    requested_options = [
        option
        for option in enum_options
        if str(option.get("label") or "") in combined_text
    ]
    blockers: list[str] = []
    if not provenance.get("target_ui_found"):
        blockers.append("尚未定位目标查询页面，不能生成筛选控件改动建议。")
    if not provenance.get("field_returned"):
        blockers.append("尚未从存储字段声明中证明该筛选字段，不能把它误判为时间切片。")
    if query_chain.get("status") != "complete":
        blockers.append("通用证据图未闭合查询入口、目标页面和存储字段，不能生成 patch。")
    if not requested_options:
        blockers.append("未从源码中证明需求所列筛选项的值映射，不能生成筛选参数。")
    blockers.append("跨层查询证据已定位；当前只生成只读实施建议，尚未形成可修改的受控合同。")
    labels = [str(option.get("label") or "") for option in requested_options]
    rules = [
        f"{option.get('label')}传 {option.get('value')}。"
        for option in requested_options
    ]
    all_values = ", ".join(
        f"{option.get('label')}={option.get('value')}" for option in enum_options
    )
    if enum_options:
        rules.append(f"全部不传筛选值，保留源码已证明的枚举值：{all_values}。")
    rules.append("只读侦查不能进入 patch、提交、推送、云效写入或部署。")
    return {
        "can_patch": False,
        "change_type": "cross_layer_stored_filter",
        "summary": (
            f"已从源码证据图识别为字段 {target_field} 的已存枚举筛选；"
            "当前仅形成跨层只读实施建议，不把它套用为其他需求的时间筛选规则。"
        ),
        "allowed_paths": choose_allowed_paths(provenance=provenance),
        "filter_options": ["全部", *labels],
        "default_behavior": "全部时不传筛选值，保留所有已证明的存储枚举值。",
        "blockers": blockers,
        "rules": rules,
    }


def build_recommended_verify_commands(*, selected_projects: list[dict], allowed_paths: list[str]) -> list[str]:
    primary = next((item for item in selected_projects if item.get("role") == "frontend" and item.get("exists")), None)
    if not primary or not allowed_paths:
        return []
    path = Path(str(primary.get("path") or ""))
    if not (path / "package.json").exists():
        return []
    vue_paths = [item for item in allowed_paths if item.endswith(".vue")]
    if vue_paths:
        return [f"./node_modules/.bin/vue-cli-service lint --no-fix {vue_paths[0]}"]
    return ["./node_modules/.bin/vue-cli-service lint --no-fix"]


def build_verification_plan(commands: list[str]) -> dict:
    """Keep demand verification separate from Harness release regression.

    A requirement run may execute only commands tied to the selected files and
    business acceptance contract.  Harness' own full suite remains a release
    gate for Harness changes and must not be injected into every HIS demand.
    """
    return {
        "active_profile": "requirement_targeted",
        "commands": list(commands),
        "scope": "selected_change_paths_and_acceptance_contract",
        "harness_release_regression": {
            "status": "separate_release_gate",
            "automatic_during_requirement_run": False,
            "reason": "Harness 全量回归用于 Harness 自身发布，不属于单个业务需求的专项验收。",
        },
    }


def project_selection_to_markdown(selected: list[dict], *, project_root: str) -> str:
    lines = ["## v0.8.8 项目选择", "", f"- 项目根：`{project_root}`"]
    for item in selected:
        lines.append(
            f"- `{item.get('path')}` [{item.get('role')}] "
            f"范围={item.get('selection_scope') or 'candidate_only'} "
            f"score={item.get('score')}：{'; '.join(item.get('reasons') or [])}"
        )
    if not selected:
        lines.append("- 未选择到可用项目。")
    return "\n".join(lines)


def service_graph_to_markdown(service_graph: dict) -> str:
    lines = ["## v0.8.8 服务图", "", f"- 状态：{service_graph.get('status') or '-'}"]
    catalog = service_graph.get("architecture_catalog") or {}
    if catalog:
        lines.append(
            f"- 架构证据：`{catalog.get('schema_version') or '-'}`；"
            f"节点 {catalog.get('node_count', len(catalog.get('nodes') or []))} 个；"
            f"依赖边 {catalog.get('edge_count', len(catalog.get('edges') or []))} 条。"
        )
    branches = service_graph.get("branches") or []
    if not branches:
        lines.append("- 未从前端实际 URL 发现可解析的跨服务分支。")
    for branch in branches:
        lines.append(
            f"- `{branch.get('source_project')}` -- `{branch.get('endpoint')}` --> "
            f"`{branch.get('target_project')}`；范围：{branch.get('scope')}。"
        )
    for item in service_graph.get("unresolved_endpoints") or []:
        lines.append(f"- 未解析：`{item.get('endpoint')}`；{item.get('reason')}")
    for item in service_graph.get("boundary_findings") or []:
        lines.append(
            f"- 边界冲突：`{item.get('type') or '-'}`；"
            f"{item.get('message') or '-'}"
        )
        if item.get("architecture_decision"):
            lines.append(
                f"  - 架构判断：`{item.get('architecture_decision')}`；"
                f"推荐：`{item.get('recommended_option_id') or '-'}`。"
            )
    for item in service_graph.get("business_rule_findings") or []:
        lines.append(
            f"- 业务规则冲突：`{item.get('path') or item.get('type') or '-'}`；"
            f"{item.get('message') or '-'}"
        )
    return "\n".join(lines)


def field_provenance_to_markdown(provenance: dict) -> str:
    return TechnicalDecisionResult(field_provenance=provenance).to_markdown().split("### 字段来源", 1)[-1].strip()


def contract_verification_to_markdown(contract_verification: dict) -> str:
    lines = [
        "## v0.39 前后端契约核验",
        "",
        f"- 是否需要跨层核验：{'是' if contract_verification.get('required') else '否'}",
        f"- 结论：{contract_verification.get('status') or '-'}",
        f"- 说明：{contract_verification.get('reason') or '-'}",
    ]
    terms = contract_verification.get("contract_terms") or []
    if terms:
        lines.append(f"- 关键接口/参数：{', '.join(f'`{term}`' for term in terms)}")
    lines.extend(["", "### 分层证据", ""])
    layers = contract_verification.get("layers") or {}
    if not layers:
        lines.append("- 当前需求不要求跨层契约核验。")
    for name, layer in layers.items():
        lines.append(f"- {name}：{layer.get('status') or '-'}；{layer.get('summary') or '-'}")
        for path in layer.get("evidence_paths") or []:
            lines.append(f"  - `{path}`")
    return "\n".join(lines)


def implementation_decision_to_markdown(decision: dict) -> str:
    lines = ["## v0.8.8 实施决策", "", f"- 是否允许 patch：{'是' if decision.get('can_patch') else '否'}", f"- 类型：{decision.get('change_type') or '-'}", f"- 结论：{decision.get('summary') or '-'}"]
    behavior_contract = decision.get("behavior_contract") or {}
    if behavior_contract:
        lines.extend(
            [
                "",
                "### 行为变更合同",
                f"- 当前行为：{behavior_contract.get('current_behavior') or '-'}",
                f"- 目标行为：{behavior_contract.get('requested_behavior') or '-'}",
                f"- 全退条件：{behavior_contract.get('full_refund_condition') or '-'}",
            ]
        )
        for item in behavior_contract.get("preserve_behavior") or []:
            lines.append(f"- 必须保留：{item}")
        for item in behavior_contract.get("next_readonly_actions") or []:
            lines.append(f"- 下一步只读追踪：{item}")
    change_plan = decision.get("change_plan") or {}
    candidate_targets = decision.get("candidate_change_targets") or []
    if change_plan:
        lines.extend(
            [
                "",
                "### 候选改动目标",
                f"- 状态：`{change_plan.get('status') or '-'}`；目标数：{change_plan.get('target_count', len(candidate_targets))}；未解析：{change_plan.get('unresolved_count', 0)}。",
            ]
        )
        for target in candidate_targets:
            lines.append(
                f"- `{target.get('source_project')}` -- `{target.get('endpoint')}` --> "
                f"`{target.get('target_project')}` / `{target.get('target_path') or '-'}` [{target.get('scope')}]"
            )
    blockers = decision.get("blockers") or []
    lines.extend(["", "### 阻断项"])
    lines.extend(f"- {item}" for item in blockers) if blockers else lines.append("- 无")
    return "\n".join(lines)


def snippet_for_term(text: str, term: str, limit: int = 260) -> str:
    index = text.find(term)
    if index < 0:
        return ""
    start = max(0, index - limit // 2)
    end = min(len(text), index + len(term) + limit // 2)
    return text[start:end].replace("\n", " ").strip()


def score_project_name(name: str, text: str) -> int:
    score = 0
    if "zhuyuan" in name.lower() and "住院" in text:
        score += 20
    if "zhuyuansf" in name.lower() and "收费" in text:
        score += 20
    if name.startswith("df-web") and any(term in text for term in ["页面", "界面", "展示", "列"]):
        score += 15
    return score


def infer_project_role(name: str) -> str:
    lower = name.lower()
    if "-web-" in lower or lower.startswith("df-web"):
        return "frontend"
    if "-bff-" in lower or "-mic-" in lower or "service" in lower:
        return "backend"
    if "api" in lower:
        return "api"
    return "unknown"


def role_rank(role: str) -> int:
    return {"frontend": 0, "backend": 1, "api": 2}.get(role, 9)


def path_priority(rel: str) -> int:
    lower = rel.lower()
    score = 0
    if "jiesuan" in lower or "jieSuan" in rel:
        score += 20
    if "yujiao" in lower or "yuJiao" in rel:
        score += 18
    if lower.endswith(".vue"):
        score += 10
    if "/pages/" in lower:
        score += 8
    if "/components/" in lower:
        score += 6
    if "/apis/" in lower or "/api/" in lower:
        score += 5
    return score


def unique_candidate_projects(items: list[tuple[str, str, int, str]]) -> list[tuple[str, str, int, str]]:
    result: list[tuple[str, str, int, str]] = []
    seen: set[str] = set()
    for item in items:
        if item[0] in seen:
            continue
        seen.add(item[0])
        result.append(item)
    return result
