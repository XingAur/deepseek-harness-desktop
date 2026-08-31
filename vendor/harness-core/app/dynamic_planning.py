from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.requirement_calibration import find_high_risk_terms
from app.role_capability_skill_registry import (
    RoleCapabilitySkillRegistryError,
    load_role_capability_skill_registry,
)
from app.task_context import TaskIntentContext


DYNAMIC_PLANNING_SCHEMA_VERSION = "1.0-dynamic-planning"
ALLOWED_DEPENDENCY_TYPES = {"requires", "consumes", "validates", "reviews", "blocks", "atomic_with"}
SUPPORTED_LAYERS = ("frontend", "backend", "database", "report")


@dataclass(frozen=True)
class PlanningSignals:
    affected_layers: tuple[str, ...] = ()
    repository_count: int = 1
    estimated_file_count: int = 0
    dependency_mode: str = "none"
    evidence_status: str = "partial"
    verification_mode: str = "targeted"
    rollback_mode: str = "single_patch"
    external_write_requested: bool = False
    database_migration_requested: bool = False
    allowed_paths: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> PlanningSignals:
        payload = payload or {}
        allowed_paths = {
            str(layer): tuple(str(path) for path in paths or [] if str(path).strip())
            for layer, paths in (payload.get("allowed_paths") or {}).items()
        }
        return cls(
            affected_layers=tuple(str(item) for item in payload.get("affected_layers") or []),
            repository_count=max(1, int(payload.get("repository_count") or 1)),
            estimated_file_count=max(0, int(payload.get("estimated_file_count") or 0)),
            dependency_mode=str(payload.get("dependency_mode") or "none"),
            evidence_status=str(payload.get("evidence_status") or "partial"),
            verification_mode=str(payload.get("verification_mode") or "targeted"),
            rollback_mode=str(payload.get("rollback_mode") or "single_patch"),
            external_write_requested=bool(payload.get("external_write_requested", False)),
            database_migration_requested=bool(payload.get("database_migration_requested", False)),
            allowed_paths=allowed_paths,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["affected_layers"] = list(self.affected_layers)
        payload["allowed_paths"] = {key: list(value) for key, value in self.allowed_paths.items()}
        return payload


@dataclass(frozen=True)
class DynamicPlanningRequest:
    requirement_id: str
    title: str
    demand_text: str
    user_instruction: str = ""
    evidence_refs: tuple[str, ...] = ()
    signals: PlanningSignals = field(default_factory=PlanningSignals)
    task_context: TaskIntentContext = field(default_factory=TaskIntentContext.empty)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DynamicPlanningRequest:
        return cls(
            requirement_id=str(payload.get("requirement_id") or "LOCAL-REQUIREMENT"),
            title=str(payload.get("title") or ""),
            demand_text=str(payload.get("demand_text") or ""),
            user_instruction=str(payload.get("user_instruction") or ""),
            evidence_refs=tuple(str(item) for item in payload.get("evidence_refs") or []),
            signals=PlanningSignals.from_dict(payload.get("signals")),
            task_context=TaskIntentContext.from_dict(payload.get("task_context")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "title": self.title,
            "demand_text": self.demand_text,
            "user_instruction": self.user_instruction,
            "evidence_refs": list(self.evidence_refs),
            "signals": self.signals.to_dict(),
            "task_context": self.task_context.to_dict(),
        }


@dataclass(frozen=True)
class DimensionScore:
    dimension: str
    score: int
    reason: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComplexityAssessment:
    level: str
    total_score: int
    dimensions: tuple[DimensionScore, ...]
    forced_upgrade_rules: tuple[str, ...] = ()
    scoring_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "total_score": self.total_score,
            "dimensions": [item.to_dict() for item in self.dimensions],
            "forced_upgrade_rules": list(self.forced_upgrade_rules),
            "scoring_version": self.scoring_version,
        }


@dataclass(frozen=True)
class RoleSpec:
    role_id: str
    label: str
    responsibility: str
    writes_code: bool = False
    human_only: bool = False
    model_alias: str = "inherit"
    context_scope: str = "contract_only"
    allowed_tools: tuple[str, ...] = ("read_artifacts",)
    forbidden_tools: tuple[str, ...] = ("external_write", "git_push", "deploy")
    input_budget_tokens: int = 12000
    output_budget_tokens: int = 4000
    timeout_seconds: int = 300
    max_retries: int = 1
    parallel_allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = list(self.allowed_tools)
        payload["forbidden_tools"] = list(self.forbidden_tools)
        return payload


@dataclass(frozen=True)
class TeamPlan:
    preset: str
    roles: tuple[RoleSpec, ...]
    selection_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "roles": [role.to_dict() for role in self.roles],
            "selection_reasons": list(self.selection_reasons),
        }


@dataclass(frozen=True)
class SubtaskSpec:
    node_id: str
    title: str
    node_kind: str
    role_id: str
    input_contracts: tuple[str, ...]
    output_contract: str
    allowed_paths: tuple[str, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    parallel_group: str = ""
    human_confirmation_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("input_contracts", "allowed_paths", "completion_criteria"):
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class TaskEdge:
    source: str
    target: str
    dependency_type: str
    artifact_schema: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskGraph:
    nodes: tuple[SubtaskSpec, ...] = ()
    edges: tuple[TaskEdge, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


@dataclass(frozen=True)
class HandoffContract:
    schema_name: str
    schema_version: str
    artifact_id: str
    artifact_version: int
    requirement_id: str
    node_id: str
    producer: str
    created_at: str
    input_artifact_ids: tuple[str, ...]
    content_hash: str
    evidence_refs: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("input_artifact_ids", "evidence_refs", "warnings", "blockers"):
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class DynamicPlan:
    status: str
    request: DynamicPlanningRequest
    assessment: ComplexityAssessment
    team: TeamPlan
    graph: TaskGraph
    handoffs: tuple[HandoffContract, ...]
    role_routes: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    generated_at: str
    planning_mode: str = "dynamic-plan"
    schema_version: str = DYNAMIC_PLANNING_SCHEMA_VERSION
    readonly: bool = True
    code_write_enabled: bool = False
    database_access_enabled: bool = False
    external_actions_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "planning_mode": self.planning_mode,
            "status": self.status,
            "readonly": self.readonly,
            "code_write_enabled": self.code_write_enabled,
            "database_access_enabled": self.database_access_enabled,
            "external_actions_enabled": self.external_actions_enabled,
            "generated_at": self.generated_at,
            "task_context": self.request.task_context.to_dict(),
            "role_routes": [dict(item) for item in self.role_routes],
            "request": self.request.to_dict(),
            "assessment": self.assessment.to_dict(),
            "team": self.team.to_dict(),
            "graph": self.graph.to_dict(),
            "handoffs": [item.to_dict() for item in self.handoffs],
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "boundaries": [
                "本产物仅为只读动态规划，不代表代码已修改。",
                "本产物未执行模型、数据库、Git、云效、TAPD、发布或部署动作。",
                "节点 succeeded 与需求真实业务验收是不同结论。",
            ],
        }


def role_spec(
    role_id: str,
    label: str,
    responsibility: str,
    *,
    writes_code: bool = False,
    human_only: bool = False,
    allowed_tools: tuple[str, ...] = ("read_artifacts", "search_code"),
    forbidden_tools: tuple[str, ...] = ("external_write", "git_push", "deploy", "database_execute"),
    input_budget_tokens: int = 12000,
    output_budget_tokens: int = 4000,
    timeout_seconds: int = 300,
    max_retries: int = 1,
    parallel_allowed: bool = True,
) -> RoleSpec:
    return RoleSpec(
        role_id=role_id,
        label=label,
        responsibility=responsibility,
        writes_code=writes_code,
        human_only=human_only,
        model_alias="inherit",
        context_scope=f"{role_id}_contract_context",
        allowed_tools=allowed_tools,
        forbidden_tools=forbidden_tools,
        input_budget_tokens=input_budget_tokens,
        output_budget_tokens=output_budget_tokens,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        parallel_allowed=parallel_allowed,
    )


DEVELOPER_TOOLS = ("read_artifacts", "search_code", "worktree_edit", "targeted_test")
REVIEW_TOOLS = ("read_artifacts", "search_code", "read_diff", "targeted_test")


ROLE_CATALOG = {
    "product_analyst": role_spec(
        "product_analyst",
        "需求分析",
        "形成需求契约、范围和验收边界。",
        allowed_tools=("read_artifacts", "search_code", "extract_visual_evidence"),
    ),
    "architect": role_spec("architect", "架构", "形成模块、接口、依赖、兼容和回滚契约。"),
    "developer": role_spec("developer", "开发", "在确认的路径白名单内实现。", writes_code=True, allowed_tools=DEVELOPER_TOOLS, input_budget_tokens=18000, output_budget_tokens=6000, timeout_seconds=900),
    "frontend_developer": role_spec("frontend_developer", "前端开发", "在前端路径白名单内实现。", writes_code=True, allowed_tools=DEVELOPER_TOOLS, input_budget_tokens=18000, output_budget_tokens=6000, timeout_seconds=900),
    "backend_developer": role_spec("backend_developer", "后端开发", "在后端路径白名单内实现。", writes_code=True, allowed_tools=DEVELOPER_TOOLS, input_budget_tokens=18000, output_budget_tokens=6000, timeout_seconds=900),
    "database_specialist": role_spec("database_specialist", "数据库专家", "规划只读数据证据或数据库改动边界。", allowed_tools=("read_artifacts", "search_code", "pg_evidence_plan"), input_budget_tokens=16000, timeout_seconds=600),
    "report_specialist": role_spec("report_specialist", "报表专家", "核对报表数据来源和展示口径。", writes_code=True, allowed_tools=DEVELOPER_TOOLS, input_budget_tokens=16000, timeout_seconds=900),
    "code_reviewer": role_spec("code_reviewer", "代码审查", "独立审查实现结果，不批准自己的实现。", allowed_tools=REVIEW_TOOLS, input_budget_tokens=18000, output_budget_tokens=6000, timeout_seconds=600),
    "test_designer": role_spec("test_designer", "测试设计", "形成自动与人工验证矩阵。"),
    "test_executor": role_spec("test_executor", "验证", "执行允许的验证并收集证据。", allowed_tools=("read_artifacts", "targeted_test", "collect_local_evidence"), timeout_seconds=900, max_retries=0),
    "acceptance_agent": role_spec("acceptance_agent", "验收汇总", "映射需求、实现、审查和验证证据。", allowed_tools=("read_artifacts",)),
    "high_risk_reviewer": role_spec("high_risk_reviewer", "高风险审查", "审查医保收费结算等敏感业务边界。", allowed_tools=REVIEW_TOOLS, input_budget_tokens=20000, output_budget_tokens=6000, timeout_seconds=900, max_retries=0),
    "conflict_arbiter": role_spec("conflict_arbiter", "冲突仲裁", "按证据优先级裁决或升级人工。", allowed_tools=("read_artifacts",), input_budget_tokens=16000, max_retries=0),
    "human_gate": role_spec("human_gate", "人工闸口", "由用户或业务负责人确认高风险语义。", human_only=True, allowed_tools=(), input_budget_tokens=0, output_budget_tokens=0, timeout_seconds=0, max_retries=0, parallel_allowed=False),
}


def build_dynamic_plan(request: DynamicPlanningRequest, *, enabled: bool = False) -> DynamicPlan:
    generated_at = datetime.now(timezone.utc).isoformat()
    if not enabled:
        return DynamicPlan(
            status="disabled",
            request=request,
            assessment=ComplexityAssessment(level="disabled", total_score=0, dimensions=()),
            team=TeamPlan(preset="disabled", roles=(), selection_reasons=("dynamic_team_planning 未显式启用。",)),
            graph=TaskGraph(),
            handoffs=(),
            role_routes=(),
            warnings=("必须显式启用 dynamic-plan；旧流程保持不变。",),
            blockers=(),
            generated_at=generated_at,
        )

    layers = resolve_affected_layers(request)
    assessment = assess_complexity(request, layers=layers)
    team = select_dynamic_team(assessment, layers=layers)
    graph = build_task_graph(request, assessment=assessment, team=team, layers=layers)
    validation_errors = validate_task_graph(graph, team)
    blockers = list(validation_errors)
    for node in graph.nodes:
        if node.node_kind == "implementation" and not node.allowed_paths:
            blockers.append(f"{node.node_id}: allowed_paths 缺失，动态执行前必须确认文件白名单。")

    warnings: list[str] = []
    if not request.task_context.is_complete:
        warnings.append(
            "task_context_incomplete:" + ",".join(request.task_context.missing_fields)
        )
    if request.signals.evidence_status == "partial":
        warnings.append("需求证据不完整，规划使用保守评分。")
    if assessment.level == "high_risk":
        warnings.append("命中强制高风险规则，必须经过高风险审查和人工闸口。")

    role_routes: tuple[dict[str, Any], ...] = ()
    if request.task_context.is_complete:
        try:
            registry = load_role_capability_skill_registry()
            registry.validate_role_catalog(ROLE_CATALOG)
            route_items: list[dict[str, Any]] = []
            for role in team.roles:
                routes = registry.route_role(
                    role.role_id,
                    role.allowed_tools,
                    task_context=request.task_context,
                )
                route_items.append({
                    "role_id": role.role_id,
                    "bindings": [
                        {
                            "tool": route.tool,
                            "capability": route.capability,
                            "provider": route.provider,
                            "skill": route.skill,
                            "execution_kind": route.execution_kind,
                            "mutation_level": route.mutation_level,
                            "external_executable": route.external_executable,
                            "mcp_server": route.mcp_server,
                        }
                        for route in routes
                    ],
                })
            role_routes = tuple(route_items)
        except RoleCapabilitySkillRegistryError as exc:
            blockers.append(f"role_capability_skill_registry:{exc}")

    handoffs = build_handoff_contracts(request, graph, generated_at=generated_at, blockers=tuple(blockers))
    if validation_errors:
        status = "blocked"
    elif blockers:
        status = "needs_evidence"
    elif assessment.level == "high_risk":
        status = "needs_human_confirmation"
    else:
        status = "ready"
    return DynamicPlan(
        status=status,
        request=request,
        assessment=assessment,
        team=team,
        graph=graph,
        handoffs=handoffs,
        role_routes=role_routes,
        warnings=tuple(warnings),
        blockers=tuple(blockers),
        generated_at=generated_at,
    )


def resolve_affected_layers(request: DynamicPlanningRequest) -> tuple[str, ...]:
    explicit = tuple(layer for layer in request.signals.affected_layers if layer in SUPPORTED_LAYERS)
    if explicit:
        return unique_keep_order(explicit)
    text = "\n".join((request.title, request.demand_text, request.user_instruction)).lower()
    inferred: list[str] = []
    marker_map = {
        "frontend": ("前端", "页面", "vue", "javascript", "typescript", "ui"),
        "backend": ("后端", "服务端", "接口", "java", "controller", "service"),
        "database": ("数据库", "postgresql", "pg", "sql", "表结构", "迁移"),
        "report": ("报表", "prt", "打印"),
    }
    for layer, markers in marker_map.items():
        if any(marker in text for marker in markers):
            inferred.append(layer)
    return tuple(inferred or ["generic"])


def assess_complexity(request: DynamicPlanningRequest, *, layers: tuple[str, ...] | None = None) -> ComplexityAssessment:
    signals = request.signals
    layers = layers or resolve_affected_layers(request)
    combined_text = "\n".join((request.title, request.demand_text, request.user_instruction))
    dimensions = (
        score_business_scope(combined_text, layers, signals),
        score_technical_scope(layers),
        score_change_scope(signals),
        score_dependencies(signals),
        score_business_risk(request),
        score_evidence(signals),
        score_verification(signals),
        score_rollback(signals),
    )
    total_score = sum(item.score for item in dimensions)
    forced_rules = build_forced_upgrade_rules(request)
    if forced_rules:
        level = "high_risk"
    elif total_score <= 5:
        level = "simple"
    elif total_score <= 11:
        level = "medium"
    else:
        level = "large"
    return ComplexityAssessment(
        level=level,
        total_score=total_score,
        dimensions=dimensions,
        forced_upgrade_rules=forced_rules,
    )


def score_business_scope(text: str, layers: tuple[str, ...], signals: PlanningSignals) -> DimensionScore:
    if signals.external_write_requested or any(term in text for term in ("跨系统", "第三方系统", "外部系统")):
        return DimensionScore("business_scope", 3, "涉及外部系统或外部写入。", "request_signals")
    if signals.repository_count >= 2 or any(term in text for term in ("跨模块", "多模块", "多仓库")):
        return DimensionScore("business_scope", 2, "涉及跨模块或多仓库。", "request_signals")
    if len(layers) >= 2:
        return DimensionScore("business_scope", 1, "单一业务目标涉及多个技术层。", "inferred_layers")
    return DimensionScore("business_scope", 0, "单一业务目标和单一技术层。", "request_signals")


def score_technical_scope(layers: tuple[str, ...]) -> DimensionScore:
    count = len([layer for layer in layers if layer != "generic"])
    if count >= 4:
        score = 3
    elif count == 3:
        score = 2
    elif count == 2:
        score = 1
    else:
        score = 0
    return DimensionScore("technical_scope", score, f"识别到技术层：{', '.join(layers)}。", "affected_layers")


def score_change_scope(signals: PlanningSignals) -> DimensionScore:
    if signals.repository_count >= 3:
        score, reason = 3, "预计影响三个及以上仓库。"
    elif signals.repository_count >= 2 or signals.estimated_file_count >= 9:
        score, reason = 2, "预计影响多仓库或九个及以上文件。"
    elif signals.estimated_file_count >= 4:
        score, reason = 1, "预计影响四至八个文件。"
    else:
        score, reason = 0, "预计影响不超过三个文件。"
    return DimensionScore("change_scope", score, reason, "request_signals")


def score_dependencies(signals: PlanningSignals) -> DimensionScore:
    scores = {"none": 0, "serial": 1, "parallel": 2, "external": 3}
    score = scores.get(signals.dependency_mode, 1)
    return DimensionScore("dependency_complexity", score, f"依赖方式为 {signals.dependency_mode}。", "request_signals")


def score_business_risk(request: DynamicPlanningRequest) -> DimensionScore:
    rules = build_forced_upgrade_rules(request)
    score = 3 if rules else 0
    reason = "命中强制风险规则：" + ", ".join(rules) if rules else "未命中强制高风险规则。"
    return DimensionScore("business_risk", score, reason, "hard_guard")


def score_evidence(signals: PlanningSignals) -> DimensionScore:
    scores = {"complete": 0, "partial": 1, "missing": 2, "conflict": 3}
    score = scores.get(signals.evidence_status, 2)
    return DimensionScore("evidence_completeness", score, f"证据状态为 {signals.evidence_status}。", "request_signals")


def score_verification(signals: PlanningSignals) -> DimensionScore:
    scores = {
        "targeted": 0,
        "unit": 0,
        "lint": 1,
        "compile": 1,
        "integration": 2,
        "login_ui": 2,
        "real_runtime": 3,
        "external": 3,
    }
    score = scores.get(signals.verification_mode, 2)
    return DimensionScore("verification_difficulty", score, f"验证方式为 {signals.verification_mode}。", "request_signals")


def score_rollback(signals: PlanningSignals) -> DimensionScore:
    scores = {"single_patch": 0, "multi_file": 1, "multi_repo": 2, "migration": 3, "external_irreversible": 3}
    score = scores.get(signals.rollback_mode, 2)
    return DimensionScore("rollback_complexity", score, f"回滚方式为 {signals.rollback_mode}。", "request_signals")


def build_forced_upgrade_rules(request: DynamicPlanningRequest) -> tuple[str, ...]:
    rules = list(find_high_risk_terms(title=request.title, demand_text="\n".join((request.demand_text, request.user_instruction))))
    text = "\n".join((request.title, request.demand_text, request.user_instruction))
    for term in ("退费", "金额舍入"):
        if term in text:
            rules.append(term)
    if request.signals.database_migration_requested or request.signals.rollback_mode == "migration":
        rules.append("数据库迁移")
    if request.signals.external_write_requested:
        rules.append("外部写入")
    if request.signals.evidence_status == "conflict":
        rules.append("证据冲突")
    return unique_keep_order(tuple(rules))


def select_dynamic_team(assessment: ComplexityAssessment, *, layers: tuple[str, ...]) -> TeamPlan:
    role_ids = ["product_analyst"]
    if assessment.level in {"medium", "large", "high_risk"}:
        role_ids.append("architect")
    role_ids.extend(development_roles_for_layers(layers))
    if assessment.level in {"medium", "large", "high_risk"}:
        role_ids.append("code_reviewer")
    if assessment.level in {"large", "high_risk"}:
        role_ids.append("test_designer")
    role_ids.append("test_executor")
    if assessment.level in {"large", "high_risk"}:
        role_ids.append("acceptance_agent")
    if assessment.level == "high_risk":
        role_ids.extend(("high_risk_reviewer", "conflict_arbiter", "human_gate"))
    roles = tuple(ROLE_CATALOG[role_id] for role_id in unique_keep_order(tuple(role_ids)))
    reasons = (
        f"复杂度等级为 {assessment.level}，总分 {assessment.total_score}。",
        f"按需技术层为 {', '.join(layers)}。",
        "开发与审查角色保持独立。",
    )
    return TeamPlan(preset=assessment.level, roles=roles, selection_reasons=reasons)


def development_roles_for_layers(layers: tuple[str, ...]) -> tuple[str, ...]:
    mapping = {
        "frontend": "frontend_developer",
        "backend": "backend_developer",
        "database": "database_specialist",
        "report": "report_specialist",
        "generic": "developer",
    }
    return tuple(mapping[layer] for layer in layers if layer in mapping)


def build_task_graph(
    request: DynamicPlanningRequest,
    *,
    assessment: ComplexityAssessment,
    team: TeamPlan,
    layers: tuple[str, ...],
) -> TaskGraph:
    nodes: list[SubtaskSpec] = [
        SubtaskSpec(
            node_id="requirement_analysis",
            title="需求契约与验收边界",
            node_kind="analysis",
            role_id="product_analyst",
            input_contracts=("RequirementEvidence",),
            output_contract="RequirementContract",
            completion_criteria=("范围、排除项、来源优先级和验收标准可追溯",),
        )
    ]
    edges: list[TaskEdge] = []
    upstream_node = "requirement_analysis"
    upstream_contract = "RequirementContract"
    if assessment.level in {"medium", "large", "high_risk"}:
        nodes.append(
            SubtaskSpec(
                node_id="architecture",
                title="架构、依赖与回滚设计",
                node_kind="architecture",
                role_id="architect",
                input_contracts=("RequirementContract",),
                output_contract="ArchitectureContract",
                completion_criteria=("模块、接口、兼容、依赖和回滚边界明确",),
            )
        )
        edges.append(TaskEdge("requirement_analysis", "architecture", "consumes", "RequirementContract", "consume_requirement_contract"))
        upstream_node = "architecture"
        upstream_contract = "ArchitectureContract"

    implementation_nodes: list[SubtaskSpec] = []
    for layer in layers:
        role_id = development_roles_for_layers((layer,))[0]
        node_id = f"{layer}_implementation" if layer != "generic" else "implementation"
        paths = tuple(request.signals.allowed_paths.get(layer, ()))
        implementation_nodes.append(
            SubtaskSpec(
                node_id=node_id,
                title=f"{ROLE_CATALOG[role_id].label}交付",
                node_kind="implementation",
                role_id=role_id,
                input_contracts=(upstream_contract,),
                output_contract="ImplementationResult",
                allowed_paths=paths,
                completion_criteria=("只修改允许路径", "生成文件清单、diff、自测和残余风险"),
                parallel_group="implementation-1",
            )
        )
        edges.append(TaskEdge(upstream_node, node_id, "consumes", upstream_contract, "consume_upstream_contract"))

    implementation_nodes, overlap_edges = serialize_overlapping_implementations(implementation_nodes)
    nodes.extend(implementation_nodes)
    edges.extend(overlap_edges)

    if assessment.level in {"medium", "large", "high_risk"}:
        nodes.append(
            SubtaskSpec(
                node_id="code_review",
                title="独立代码审查",
                node_kind="review",
                role_id="code_reviewer",
                input_contracts=("ImplementationResult",),
                output_contract="ReviewDecision",
                completion_criteria=("逐项审查需求一致性、兼容性、异常路径和验证缺口",),
            )
        )
        for node in implementation_nodes:
            edges.append(TaskEdge(node.node_id, "code_review", "reviews", "ImplementationResult", "independent_review"))

    if assessment.level in {"large", "high_risk"}:
        acceptance_inputs = ("VerificationResult",)
        acceptance_artifact = "VerificationResult"
        if assessment.level == "high_risk":
            acceptance_inputs = ("VerificationResult", "ConflictCase", "HighRiskReviewDecision")
            acceptance_artifact = "ConflictCase"
        nodes.append(
            SubtaskSpec(
                node_id="test_design",
                title="测试矩阵设计",
                node_kind="test_design",
                role_id="test_designer",
                input_contracts=("RequirementContract", "ArchitectureContract"),
                output_contract="VerificationPlan",
                completion_criteria=("必须发生、禁止发生、保持不变和人工场景完整",),
                parallel_group="governance-1",
            )
        )
        edges.append(TaskEdge(upstream_node, "test_design", "consumes", upstream_contract, "design_from_contract"))

    verification_inputs = ("ReviewDecision",) if assessment.level in {"medium", "large", "high_risk"} else ("ImplementationResult",)
    nodes.append(
        SubtaskSpec(
            node_id="verify",
            title="专项验证与证据收集",
            node_kind="verification",
            role_id="test_executor",
            input_contracts=verification_inputs,
            output_contract="VerificationResult",
            completion_criteria=("记录命令、环境、退出码、证据和未验证边界",),
        )
    )
    if assessment.level in {"medium", "large", "high_risk"}:
        edges.append(TaskEdge("code_review", "verify", "requires", "ReviewDecision", "review_before_verification"))
    else:
        for node in implementation_nodes:
            edges.append(TaskEdge(node.node_id, "verify", "validates", "ImplementationResult", "verify_implementation"))
    if assessment.level in {"large", "high_risk"}:
        edges.append(TaskEdge("test_design", "verify", "consumes", "VerificationPlan", "execute_verification_plan"))

    acceptance_dependency = "verify"
    if assessment.level == "high_risk":
        nodes.append(
            SubtaskSpec(
                node_id="high_risk_review",
                title="高风险业务专项审查",
                node_kind="high_risk_review",
                role_id="high_risk_reviewer",
                input_contracts=("ReviewDecision", "VerificationResult"),
                output_contract="HighRiskReviewDecision",
                completion_criteria=("敏感业务路径、原逻辑影响和人工确认项明确",),
            )
        )
        edges.append(TaskEdge("code_review", "high_risk_review", "requires", "ReviewDecision", "high_risk_review_requires_code_review"))
        edges.append(TaskEdge("verify", "high_risk_review", "requires", "VerificationResult", "high_risk_review_requires_evidence"))
        nodes.append(
            SubtaskSpec(
                node_id="conflict_arbitration",
                title="证据冲突仲裁",
                node_kind="arbitration",
                role_id="conflict_arbiter",
                input_contracts=("HighRiskReviewDecision",),
                output_contract="ConflictCase",
                completion_criteria=("按用户、需求、代码/API/数据库、规则、推断顺序裁定或升级",),
            )
        )
        edges.append(TaskEdge("high_risk_review", "conflict_arbitration", "consumes", "HighRiskReviewDecision", "arbitrate_or_escalate"))
        acceptance_dependency = "conflict_arbitration"

    if assessment.level in {"large", "high_risk"}:
        nodes.append(
            SubtaskSpec(
                node_id="acceptance",
                title="验收证据汇总",
                node_kind="acceptance",
                role_id="acceptance_agent",
                input_contracts=acceptance_inputs,
                output_contract="AcceptanceDecision",
                completion_criteria=("区分自动验证、人工验收和生产真实结果",),
            )
        )
        edges.append(TaskEdge(acceptance_dependency, "acceptance", "requires", acceptance_artifact, "acceptance_requires_governance"))
        acceptance_dependency = "acceptance"

    if assessment.level == "high_risk":
        nodes.append(
            SubtaskSpec(
                node_id="human_gate",
                title="人工高风险确认",
                node_kind="human_gate",
                role_id="human_gate",
                input_contracts=("AcceptanceDecision", "ConflictCase"),
                output_contract="HumanGateDecision",
                completion_criteria=("用户或业务负责人明确确认业务语义和验证范围",),
                human_confirmation_required=True,
            )
        )
        edges.append(TaskEdge(acceptance_dependency, "human_gate", "blocks", "AcceptanceDecision", "human_confirmation_required"))
    return TaskGraph(nodes=tuple(nodes), edges=tuple(edges))


def serialize_overlapping_implementations(nodes: list[SubtaskSpec]) -> tuple[list[SubtaskSpec], list[TaskEdge]]:
    result: list[SubtaskSpec] = []
    edges: list[TaskEdge] = []
    for node in nodes:
        group_number = 1
        for previous in result:
            if paths_overlap(previous.allowed_paths, node.allowed_paths):
                edges.append(
                    TaskEdge(
                        previous.node_id,
                        node.node_id,
                        "requires",
                        "ImplementationResult",
                        "allowed_paths_overlap",
                    )
                )
                match = re.search(r"(\d+)$", previous.parallel_group)
                group_number = max(group_number, (int(match.group(1)) if match else 1) + 1)
        result.append(
            SubtaskSpec(
                **{
                    **asdict(node),
                    "input_contracts": tuple(node.input_contracts),
                    "allowed_paths": tuple(node.allowed_paths),
                    "completion_criteria": tuple(node.completion_criteria),
                    "parallel_group": f"implementation-{group_number}",
                }
            )
        )
    return result, edges


def paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left or not right:
        return False
    normalized_left = [normalize_path(path) for path in left]
    normalized_right = [normalize_path(path) for path in right]
    return any(
        left_path == right_path
        or left_path.startswith(right_path + "/")
        or right_path.startswith(left_path + "/")
        for left_path in normalized_left
        for right_path in normalized_right
    )


def normalize_path(path: str) -> str:
    return str(Path(path)).replace("\\", "/").rstrip("/")


def is_safe_relative_path(path: str) -> bool:
    normalized = normalize_path(path)
    if not normalized or Path(normalized).is_absolute() or normalized.startswith("~"):
        return False
    return ".." not in Path(normalized).parts


def validate_task_graph(graph: TaskGraph, team: TeamPlan) -> list[str]:
    errors: list[str] = []
    node_ids = [node.node_id for node in graph.nodes]
    node_by_id = {node.node_id: node for node in graph.nodes}
    if len(node_ids) != len(set(node_ids)):
        errors.append("duplicate_task_node_id")
    team_roles = {role.role_id for role in team.roles}
    for node in graph.nodes:
        if node.role_id not in team_roles:
            errors.append(f"node_role_not_in_team:{node.node_id}:{node.role_id}")
        if not node.output_contract:
            errors.append(f"missing_output_contract:{node.node_id}")
        for path in node.allowed_paths:
            if not is_safe_relative_path(path):
                errors.append(f"unsafe_allowed_path:{node.node_id}:{path}")
    for edge in graph.edges:
        if edge.source not in node_by_id or edge.target not in node_by_id:
            errors.append(f"unknown_task_edge_node:{edge.source}:{edge.target}")
        if edge.dependency_type not in ALLOWED_DEPENDENCY_TYPES:
            errors.append(f"invalid_dependency_type:{edge.dependency_type}")
    if not any(item.startswith("unknown_task_edge_node") for item in errors) and graph_has_cycle(graph):
        errors.append("task_graph_cycle")

    implementation_nodes = [node for node in graph.nodes if node.node_kind == "implementation"]
    reachability = build_reachability(graph)
    for index, left in enumerate(implementation_nodes):
        for right in implementation_nodes[index + 1 :]:
            if paths_overlap(left.allowed_paths, right.allowed_paths):
                ordered = right.node_id in reachability.get(left.node_id, set()) or left.node_id in reachability.get(right.node_id, set())
                if not ordered:
                    errors.append(f"unsafe_parallel_path_overlap:{left.node_id}:{right.node_id}")

    implementation_roles = {node.role_id for node in implementation_nodes}
    for node in graph.nodes:
        if node.node_kind in {"review", "high_risk_review"} and node.role_id in implementation_roles:
            errors.append(f"developer_self_review:{node.node_id}")
    return list(unique_keep_order(tuple(errors)))


def graph_has_cycle(graph: TaskGraph) -> bool:
    indegree = {node.node_id: 0 for node in graph.nodes}
    outgoing = {node.node_id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.source not in indegree or edge.target not in indegree:
            continue
        outgoing[edge.source].append(edge.target)
        indegree[edge.target] += 1
    ready = [node_id for node_id, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        node_id = ready.pop()
        visited += 1
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return visited != len(indegree)


def build_reachability(graph: TaskGraph) -> dict[str, set[str]]:
    outgoing: dict[str, list[str]] = {node.node_id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.source in outgoing and edge.target in outgoing:
            outgoing[edge.source].append(edge.target)
    result: dict[str, set[str]] = {}
    for node_id in outgoing:
        seen: set[str] = set()
        stack = list(outgoing[node_id])
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(outgoing.get(current, ()))
        result[node_id] = seen
    return result


def build_handoff_contracts(
    request: DynamicPlanningRequest,
    graph: TaskGraph,
    *,
    generated_at: str,
    blockers: tuple[str, ...],
) -> tuple[HandoffContract, ...]:
    safe_requirement_id = re.sub(r"[^a-z0-9-]+", "-", request.requirement_id.lower()).strip("-") or "local"
    artifact_ids = {
        node.node_id: f"artifact-{safe_requirement_id}-{node.node_id}-v1"
        for node in graph.nodes
    }
    incoming: dict[str, list[str]] = {node.node_id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.source in artifact_ids and edge.target in incoming:
            incoming[edge.target].append(artifact_ids[edge.source])
    contracts: list[HandoffContract] = []
    for node in graph.nodes:
        node_blockers = tuple(item for item in blockers if item.startswith(node.node_id + ":"))
        content = {
            "schema_name": node.output_contract,
            "node_id": node.node_id,
            "producer": node.role_id,
            "inputs": sorted(incoming[node.node_id]),
            "allowed_paths": list(node.allowed_paths),
            "completion_criteria": list(node.completion_criteria),
        }
        digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        contracts.append(
            HandoffContract(
                schema_name=node.output_contract,
                schema_version="1.0",
                artifact_id=artifact_ids[node.node_id],
                artifact_version=1,
                requirement_id=request.requirement_id,
                node_id=node.node_id,
                producer=node.role_id,
                created_at=generated_at,
                input_artifact_ids=tuple(sorted(incoming[node.node_id])),
                content_hash=f"sha256:{digest}",
                evidence_refs=request.evidence_refs,
                blockers=node_blockers,
            )
        )
    return tuple(contracts)


def dynamic_plan_to_markdown(plan: DynamicPlan) -> str:
    lines = [
        "# HIS Harness dynamic-plan",
        "",
        f"- 状态：{plan.status}",
        "- 模式：只读规划",
        f"- 需求：{plan.request.requirement_id} {plan.request.title}",
        f"- 复杂度：{plan.assessment.level}（{plan.assessment.total_score} 分）",
        "- 代码写入：关闭",
        "- 数据库访问：关闭",
        "- 外部动作：关闭",
        "",
        "## 评分证据",
        "",
    ]
    if not plan.assessment.dimensions:
        lines.append("- 未启用动态规划。")
    for item in plan.assessment.dimensions:
        lines.append(f"- {item.dimension}: {item.score}，{item.reason}（{item.source}）")
    if plan.assessment.forced_upgrade_rules:
        lines.append(f"- 强制升级：{', '.join(plan.assessment.forced_upgrade_rules)}")
    lines.extend(("", "## 动态团队", ""))
    for role in plan.team.roles:
        lines.append(f"- `{role.role_id}` {role.label}：{role.responsibility}")
    lines.extend(("", "## 子任务 DAG", ""))
    for node in plan.graph.nodes:
        path_text = ", ".join(node.allowed_paths) or "只读/待确认"
        group_text = node.parallel_group or "串行治理"
        lines.append(
            f"- `{node.node_id}` [{node.role_id}] -> {node.output_contract}；路径={path_text}；组={group_text}"
        )
    lines.extend(("", "## 依赖", ""))
    for edge in plan.graph.edges:
        lines.append(f"- `{edge.source}` -> `{edge.target}` [{edge.dependency_type}] {edge.reason}")
    lines.extend(("", "## Warning / Blocker", ""))
    for item in plan.warnings:
        lines.append(f"- Warning: {item}")
    for item in plan.blockers:
        lines.append(f"- Blocker: {item}")
    if not plan.warnings and not plan.blockers:
        lines.append("- 无。")
    lines.extend(
        (
            "",
            "## 边界",
            "",
            "- 本产物仅为只读规划，不代表代码已修改。",
            "- 本产物未执行模型、数据库、Git 或外部系统动作。",
            "- 自动验证与真实业务人工验收必须分开记录。",
        )
    )
    return "\n".join(lines)


def render_dynamic_plan_outputs(plan: DynamicPlan) -> str:
    return "\n\n".join(
        (
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            dynamic_plan_to_markdown(plan),
            json.dumps(build_dynamic_plan_audit(plan), ensure_ascii=False, indent=2),
        )
    )


def build_dynamic_plan_audit(plan: DynamicPlan) -> dict[str, Any]:
    return {
        "schema_version": "1.0-dynamic-plan-audit",
        "requirement_id": plan.request.requirement_id,
        "generated_at": plan.generated_at,
        "status": plan.status,
        "decision": {
            "level": plan.assessment.level,
            "score": plan.assessment.total_score,
            "forced_upgrade_rules": list(plan.assessment.forced_upgrade_rules),
            "selected_roles": [role.role_id for role in plan.team.roles],
            "node_count": len(plan.graph.nodes),
            "edge_count": len(plan.graph.edges),
        },
        "safety": {
            "readonly": True,
            "code_write_enabled": False,
            "database_access_enabled": False,
            "external_actions_enabled": False,
        },
        "warnings": list(plan.warnings),
        "blockers": list(plan.blockers),
    }


def write_dynamic_plan_outputs(output_dir: Path, plan: DynamicPlan) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "dynamic_plan.json"
    markdown_path = output_dir / "dynamic_plan.md"
    audit_path = output_dir / "dynamic_plan_audit.json"
    json_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(dynamic_plan_to_markdown(plan), encoding="utf-8")
    audit_path.write_text(json.dumps(build_dynamic_plan_audit(plan), ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path, markdown_path, audit_path


def unique_keep_order(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))
