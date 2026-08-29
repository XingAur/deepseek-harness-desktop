"""Deterministic preflight contract for multi-project business changes.

This module deliberately does not edit repositories.  It turns the technical
decision's candidate targets into a per-project contract and blocks whenever
the evidence or verification boundary is incomplete.  A caller may only hand
the contract to an executor when ``status == 'ready'``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping


MULTI_SERVICE_CHANGE_CONTRACT_SCHEMA_VERSION = "multi-service-change-contract.v1"
_ALLOWED_ROLES = {"frontend", "backend", "api", "bff", "service"}


@dataclass
class MultiServiceChangeContract:
    status: str = "blocked"
    schema_version: str = MULTI_SERVICE_CHANGE_CONTRACT_SCHEMA_VERSION
    objective: str = ""
    targets: list[dict[str, Any]] = field(default_factory=list)
    repositories: dict[str, dict[str, Any]] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    evidence_gaps: list[dict[str, Any]] = field(default_factory=list)
    evidence_options: list[dict[str, Any]] = field(default_factory=list)
    architecture_decision: dict[str, Any] = field(default_factory=dict)
    contract_proposals: list[dict[str, Any]] = field(default_factory=list)
    continuation: dict[str, Any] = field(
        default_factory=lambda: {
            "status": "await_user_choice",
            "default": "readonly_only",
        }
    )
    acceptance: dict[str, list[str]] = field(default_factory=dict)
    runtime_validation: dict[str, Any] = field(default_factory=dict)
    rollback: dict[str, Any] = field(
        default_factory=lambda: {
            "status": "not_available",
            "strategy": "不得向原仓库写入；阻断后仅保留临时 worktree 和审查产物。",
        }
    )

    @property
    def can_apply(self) -> bool:
        return self.status == "ready" and self.rollback.get("status") == "ready"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = [
            "## 多项目改动合同",
            "",
            f"- schema：`{self.schema_version}`",
            f"- 状态：`{self.status}`",
            f"- 是否允许写回：`{'是' if self.can_apply else '否'}`",
            f"- 目标数：`{len(self.targets)}`",
            "",
            "### 阻断项",
            "",
        ]
        lines.extend(f"- {item}" for item in self.blockers) if self.blockers else lines.append("- 无")
        lines.extend(["", "### 证据缺口与可选继续方式", ""])
        if not self.evidence_gaps:
            lines.append("- 无证据缺口。")
        for gap in self.evidence_gaps:
            lines.append(f"- 缺口 `{gap.get('id') or '-'}`：{gap.get('question') or gap.get('reason') or '-'}")
        for option in self.evidence_options:
            lines.append(f"- 选项 `{option.get('id') or '-'}`：{option.get('label') or '-'}；{option.get('action') or '-'}")
            candidates = option.get("candidate_commands_by_project") or {}
            for project_name, commands in candidates.items():
                lines.append(f"  - `{project_name}` 候选命令：{', '.join(commands) or '-'}（需用户选择并验证）")
        if self.architecture_decision:
            lines.extend(["", "### 服务架构判断", ""])
            lines.append(f"- 状态：`{self.architecture_decision.get('status') or '-'}`")
            lines.append(
                f"- 推荐方案：`{self.architecture_decision.get('recommended_option_id') or '-'}`"
            )
            if self.architecture_decision.get("status") == "auto_resolved":
                lines.append("- 结论：已由本地构建文件和公共 API 证据自动确定，不要求用户重复提供服务关系。")
            elif self.architecture_decision.get("status") == "needs_user_choice":
                lines.append("- 结论：证据不足以区分方案，必须先选择并补充 API/服务证据。")
            requirements = self.architecture_decision.get("requirements") or []
            for requirement in requirements:
                lines.append(
                    f"- 需要形成的架构改动目标 `{requirement.get('id') or '-'}`："
                    f"{requirement.get('label') or '-'}；接口契约状态=`{requirement.get('endpoint_contract_status') or '-'}`。"
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
                    for item in proposal.get("auto_collected_evidence") or []:
                        if not isinstance(item, dict):
                            continue
                        lines.append(
                            f"  - Harness 自动取证 `{item.get('id') or '-'}`："
                            f"状态=`{item.get('status') or '-'}`；"
                            f"证据数={len(item.get('evidence') or [])}。"
                        )
                    for item in proposal.get("remaining_evidence_before_worktree") or []:
                        lines.append(f"  - 仍需 Harness 继续验证：{item}")
                for item in proposal.get("required_evidence_before_worktree") or []:
                    lines.append(f"  - 进入 worktree 前必须补证据：{item}")
        continuation = self.continuation or {}
        lines.extend(["", "### 自动继续策略", ""])
        lines.append(
            f"- 状态：`{continuation.get('status') or '-'}`；"
            f"是否需要用户：{'是' if continuation.get('requires_user', True) else '否'}。"
        )
        lines.append(f"- 下一步：{continuation.get('next_action') or '-'}")
        lines.append(f"- 安全边界：{continuation.get('reason') or '-'}")
        lines.extend(["", "### 按仓库边界", ""])
        if not self.repositories:
            lines.append("- 未形成可执行仓库合同。")
        for name, repo in self.repositories.items():
            lines.append(f"- `{name}`：角色={repo.get('role') or '-'}")
            lines.append(f"  - 允许路径：{', '.join(repo.get('allowed_paths') or []) or '-'}")
            lines.append(f"  - 验证命令：{'; '.join(repo.get('verify_commands') or []) or '-'}")
        if self.runtime_validation:
            lines.extend(["", "### 验证命令来源", ""])
            lines.append(f"- 来源：`{self.runtime_validation.get('source') or '-'}`")
            lines.append(f"- 执行方式：{self.runtime_validation.get('verification_mode') or '-'}")
            lines.append(f"- 说明：{self.runtime_validation.get('message') or '-'}")
        lines.extend(["", "### 回退边界", "", f"- {self.rollback.get('strategy') or '-'}"])
        return "\n".join(lines)


def split_qualified_path(value: Any, *, expected_project: str = "") -> tuple[str, str]:
    """Return ``(project, relative_path)`` for ``project:path`` values."""

    raw = str(value or "").strip().replace("\\", "/")
    if ":" not in raw:
        return expected_project, raw
    project, relative = raw.split(":", 1)
    return project.strip() or expected_project, relative.strip()


def _safe_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or "\x00" in raw:
        return ""
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return str(path)


def _project_index(selected_projects: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for project in selected_projects:
        name = str(project.get("name") or "").strip()
        path = str(project.get("path") or "").strip()
        if not name and path:
            name = path.rstrip("/").rsplit("/", 1)[-1]
        if name:
            index[name] = project
    return index


def build_evidence_choices(
    blockers: list[str],
    *,
    architecture_decision: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn deterministic blockers into user-facing evidence choices.

    The choices are data, not an implicit approval.  A caller must submit the
    selected evidence and rebuild the contract; selecting ``readonly_only``
    never authorizes a patch.
    """

    definitions = (
        (
            "runtime_validation",
            ("运行时验证", "逐仓库验证命令"),
            "请为每个受影响仓库提供已经确认可运行的 lint、编译或测试命令。",
            "提供逐仓库验证命令",
            "提交 commands_by_project，再重新生成合同。",
            ["commands_by_project"],
        ),
        (
            "service_evidence",
            ("controller_verified", "target_path", "source_paths", "接口未解析", "证据图"),
            "请补充页面请求、BFF/微服务接口和控制器文件之间的证据。",
            "补充接口与控制器证据",
            "提交接口路径、控制器路径或重新选择项目后重跑分析。",
            ["endpoint", "target_path", "controller_verified"],
        ),
        (
            "project_scope",
            ("项目未被选中", "仓库路径不存在", "项目角色"),
            "请补充受影响仓库的本地路径、角色或正确项目范围。",
            "补充项目范围",
            "提交 project_paths 后重新生成技术决策。",
            ["project_paths"],
        ),
        (
            "acceptance",
            ("自动验收标准", "自动验收"),
            "请明确至少一个可自动检查的验收标准，并保留必要的人工验收项。",
            "补充验收标准",
            "提交 acceptance.automatic 后重新生成合同。",
            ["acceptance.automatic"],
        ),
        (
            "governance",
            ("需求治理", "治理尚未就绪"),
            "请补齐需求治理、变更归属和单次变更契约，再继续改码。",
            "补齐需求治理",
            "重新运行治理阶段；治理未 ready 时仍只读。",
            ["governance"],
        ),
        (
            "candidate_targets",
            ("候选改动目标", "multi_service_feature", "没有候选"),
            "请补充需求范围或代码证据，使每个实际改动点都能形成候选目标。",
            "补充改动目标证据",
            "补充需求正文、页面入口或接口证据后重新分析。",
            ["requirement_evidence", "service_graph"],
        ),
    )
    gaps: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    for gap_id, needles, question, label, action, required_fields in definitions:
        matched = [blocker for blocker in blockers if any(needle in blocker for needle in needles)]
        if not matched:
            continue
        gaps.append(
            {
                "id": gap_id,
                "question": question,
                "blockers": sorted(set(matched)),
                "required_fields": required_fields,
            }
        )
        options.append(
            {
                "id": f"provide_{gap_id}",
                "label": label,
                "action": action,
                "required_fields": required_fields,
            }
        )
    architecture = architecture_decision or {}
    if architecture.get("status") == "needs_user_choice":
        gaps.append(
            {
                "id": "architecture_evidence",
                "question": "本地构建/API 证据不足以唯一确定服务边界，请选择架构方案并补充对应公共 API 证据。",
                "blockers": ["服务架构方案未能自动确定。"],
                "required_fields": ["architecture_option_id", "architecture_evidence"],
            }
        )
        options.append(
            {
                "id": "provide_architecture_evidence",
                "label": "补充服务架构证据",
                "action": "选择架构方案并提交 BFF、微服务、公共 API 的源码或构建证据后重新生成合同。",
                "required_fields": ["architecture_option_id", "architecture_evidence"],
            }
        )
    options.extend(
        [
            {
                "id": "readonly_only",
                "label": "先只读分析",
                "action": "保留当前候选目标和阻断项，不生成、不应用业务代码 patch。",
                "required_fields": [],
            },
            {
                "id": "cancel_change",
                "label": "暂不继续",
                "action": "结束本次改动流程，保留分析工件供后续恢复。",
                "required_fields": [],
            },
        ]
    )
    return gaps, options


_USER_DECISION_BLOCKER_MARKERS = (
    "需求治理",
    "运行时验证未就绪",
    "没有受信的逐仓库验证命令",
    "缺少自动验收标准",
    "项目未被选中",
    "仓库路径不存在",
    "角色未受支持",
    "候选目标 #",
    "source_path 项目",
    "target_path 项目",
    "架构方案未能自动确定",
    "数据来源边界",
    "审批属性规则",
    "没有候选改动目标",
)


def build_continuation_state(
    *,
    blockers: list[str],
    architecture_decision: Mapping[str, Any],
    contract_proposals: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Choose whether a blocked contract can advance without another prompt.

    A blocked contract remains non-executable.  ``auto_continue_readonly`` only
    means that Harness may keep collecting deterministic source/API evidence;
    it never authorizes a patch, a worktree, a command, or an external write.
    """

    stable_blockers = [str(item).strip() for item in blockers if str(item).strip()]
    proposal_evidence_pending = any(
        isinstance(proposal, Mapping)
        and (
            proposal.get("write_ready") is False
            or proposal.get("remaining_evidence_before_worktree")
            or proposal.get("required_evidence_before_worktree")
        )
        for proposal in contract_proposals
    )
    architecture_auto_resolved = (
        str(architecture_decision.get("status") or "") == "auto_resolved"
    )
    user_decision_required = any(
        any(marker in blocker for marker in _USER_DECISION_BLOCKER_MARKERS)
        for blocker in stable_blockers
    )
    if architecture_auto_resolved and proposal_evidence_pending and not user_decision_required:
        steps: list[str] = []
        for blocker in stable_blockers:
            if any(token in blocker for token in ("http", "契约", "接口", "endpoint")):
                step = "继续核验 HTTP 路由、Controller 与 DTO 契约"
            elif any(token in blocker for token in ("映射", "投影", "字段", "数据")):
                step = "继续核验数据来源、字段映射与分页投影"
            elif any(token in blocker for token in ("未解析", "证据图")):
                step = "继续追踪未闭合的跨服务调用链"
            else:
                step = "继续收集改动前所需的源码证据"
            if step not in steps:
                steps.append(step)
        return {
            "status": "auto_continue_readonly",
            "default": "continue_readonly_analysis",
            "resume_event": "auto_readonly_evidence_pass",
            "requires_user": False,
            "write_gate": "closed",
            "next_action": "；".join(steps[:4]) or "继续收集改动前所需的源码证据",
            "reason": "架构方案已由本地证据确定，剩余仅是可自动核验的代码契约缺口；Harness 自动继续只读分析，不自动改码。",
        }
    return {
        "status": "await_user_choice",
        "default": "readonly_only",
        "resume_event": "submit_multi_service_evidence",
        "requires_user": True,
        "write_gate": "closed",
        "next_action": "补充业务口径、项目范围、可运行验证命令或明确选择架构方案后再继续。",
        "reason": "当前至少有一项信息无法从本地代码安全推断，Harness 不替用户拍板。",
    }


def suggest_runtime_commands(project_path: str) -> list[str]:
    """Return read-only command candidates; never claims they are verified."""

    root = Path(project_path).expanduser()
    candidates: list[str] = []
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            package = {}
        scripts = package.get("scripts") if isinstance(package, dict) else {}
        if isinstance(scripts, dict):
            if (root / "pnpm-lock.yaml").is_file():
                script_prefix = "pnpm"
            elif (root / "yarn.lock").is_file():
                script_prefix = "yarn"
            else:
                script_prefix = "npm run"
            for script in ("lint", "test", "build"):
                if script in scripts:
                    candidates.append(f"{script_prefix} {script}")
    if (root / "gradlew").is_file():
        candidates.append("./gradlew compileJava")
    elif (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        candidates.append("gradle compileJava")
    if (root / "mvnw").is_file():
        candidates.append("./mvnw -DskipTests compile")
    elif (root / "pom.xml").is_file():
        candidates.append("mvn -DskipTests compile")
    return list(dict.fromkeys(candidates))


def discover_runtime_validation(
    *,
    technical_decision: Mapping[str, Any],
    selected_projects: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Discover per-repository commands from the affected repositories.

    Discovery is evidence collection, not proof that a command will pass.  The
    worktree executors run these commands after the patch and fail closed if a
    command is missing or fails.  Evidence-only projects are intentionally not
    included: they constrain the decision but are not changed or verified.
    """

    raw_decision = technical_decision or {}
    nested = raw_decision.get("implementation_decision")
    decision = dict(nested) if isinstance(nested, Mapping) else dict(raw_decision)
    projects = list(selected_projects or raw_decision.get("selected_projects") or [])
    project_index = _project_index(projects)
    targets = decision.get("candidate_change_targets") or []
    affected_names: set[str] = set()
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        for key in ("source_project", "target_project"):
            name = str(target.get(key) or "").strip()
            if name:
                affected_names.add(name)
    commands_by_project: dict[str, list[str]] = {}
    missing_projects: dict[str, str] = {}
    for project_name in sorted(affected_names):
        project = project_index.get(project_name)
        if not project:
            missing_projects[project_name] = "项目未进入 selected_projects，无法发现本地命令。"
            continue
        path = str(project.get("path") or "").strip()
        commands = suggest_runtime_commands(path) if path else []
        if commands:
            commands_by_project[project_name] = commands
        else:
            missing_projects[project_name] = "未从 package.json、Gradle 或 Maven 配置发现 lint、编译或测试命令。"
    ready = bool(affected_names) and not missing_projects and len(commands_by_project) == len(affected_names)
    return {
        "status": "ready" if ready else "blocked",
        "source": "harness_auto_discovery",
        "verification_mode": "改动后在每个仓库独立 worktree 自动执行；失败即阻断写回",
        "commands_by_project": commands_by_project,
        "missing_projects": missing_projects,
        "message": (
            "Harness 已从受影响仓库配置自动发现验证命令；不会要求用户重复提供。"
            if ready
            else "Harness 未能为所有实际改动仓库发现命令；仅要求补充缺失仓库，不把证据项目算作改动项目。"
        ),
    }


def build_multi_service_change_contract(
    *,
    technical_decision: Mapping[str, Any],
    governance_ready: bool = False,
    selected_projects: list[Mapping[str, Any]] | None = None,
    runtime_validation: Mapping[str, Any] | None = None,
    acceptance: Mapping[str, Any] | None = None,
) -> MultiServiceChangeContract:
    """Build a conservative contract; never infer an executable patch plan.

    ``runtime_validation`` is intentionally caller supplied.  The technical
    decision can locate code, but it cannot prove that a command is runnable
    in each repository.  Requiring this separate evidence prevents a model
    from making a plausible but unverified multi-service edit.
    """

    raw_decision = technical_decision or {}
    nested_decision = raw_decision.get("implementation_decision")
    decision = dict(nested_decision) if isinstance(nested_decision, Mapping) else dict(raw_decision)
    provenance = raw_decision.get("field_provenance") or {}
    service_graph = provenance.get("service_graph") or {}
    projects = list(selected_projects or raw_decision.get("selected_projects") or [])
    project_index = _project_index(projects)
    blockers: list[str] = []
    if str(decision.get("change_type") or "") != "multi_service_feature":
        blockers.append("技术决策不是 multi_service_feature，不能套用多项目合同。")
    if not governance_ready:
        blockers.append("需求治理尚未就绪，不能生成可执行改动合同。")
    plan = decision.get("change_plan") or {}
    if not plan and service_graph:
        plan = {"status": "ready_for_contract" if service_graph.get("status") == "evidence_ready" else "blocked_by_graph"}
    architecture_decision = {
        "status": str(plan.get("architecture_decision") or "not_applicable"),
        "recommended_option_id": str(plan.get("recommended_architecture_option_id") or ""),
        "options": list(plan.get("architecture_options") or []),
        "evidence": list(plan.get("architecture_evidence") or []),
        "requirements": list(plan.get("architecture_requirements") or []),
    }
    raw_contract_proposals = plan.get("contract_proposals") or [
        requirement.get("contract_proposal")
        for requirement in architecture_decision["requirements"]
        if isinstance(requirement, Mapping) and requirement.get("contract_proposal")
    ]
    contract_proposals = [
        dict(item) for item in raw_contract_proposals
        if isinstance(item, Mapping)
    ]
    if architecture_decision["status"] == "needs_user_choice":
        blockers.append("服务架构方案未能自动确定，拒绝跨服务改码。")
    for requirement in architecture_decision["requirements"]:
        if not isinstance(requirement, Mapping):
            blockers.append("架构改动目标不是结构化对象，拒绝跨服务改码。")
            continue
        endpoint_status = str(requirement.get("endpoint_contract_status") or "")
        if endpoint_status not in {"verified", "existing_target"}:
            gaps = ", ".join(str(item) for item in requirement.get("contract_gap") or [])
            contract_detail = f" 已识别契约缺口：{gaps}。" if gaps else ""
            blockers.append(
                f"架构改动目标 {requirement.get('id') or '-'} 的 API/DTO/分页契约尚未形成可执行目标，"
                f"Harness 继续只读分析，不自动生成接口或后端参数。{contract_detail}"
            )
    unresolved = decision.get("unresolved_endpoints") or service_graph.get("unresolved_endpoints") or []
    graph_status = str(service_graph.get("status") or "")
    if service_graph and graph_status != "evidence_ready":
        blockers.append(
            f"多服务证据图尚未闭合：{len(unresolved)} 个接口未解析，拒绝跨服务改码。"
        )
    elif str(plan.get("status") or "") not in {"ready_for_contract", "blocked_by_boundary"}:
        blockers.append("多服务实施计划尚未达到可生成合同状态。")
    for decision_blocker in decision.get("blockers") or []:
        text = str(decision_blocker).strip()
        if text and any(term in text for term in ("数据来源边界", "审批属性规则", "架构方案")):
            blockers.append(text)
    if unresolved:
        blockers.append(f"仍有 {len(unresolved)} 个接口未解析，拒绝跨服务改码。")

    raw_targets = decision.get("candidate_change_targets") or []
    if not raw_targets:
        blockers.append("没有候选改动目标，无法建立逐仓库改动合同。")
    runtime = runtime_validation or {}
    commands_by_project = runtime.get("commands_by_project") or {}
    if str(runtime.get("status") or "") != "ready":
        blockers.append("运行时验证未就绪；没有逐仓库可执行验证命令，拒绝自动改码。")
    acceptance_data = {
        "automatic": [str(item).strip() for item in (acceptance or {}).get("automatic", []) if str(item).strip()],
        "manual": [str(item).strip() for item in (acceptance or {}).get("manual", []) if str(item).strip()],
    }
    if not acceptance_data["automatic"]:
        blockers.append("缺少自动验收标准，无法判断改动是否正确。")

    normalized_targets: list[dict[str, Any]] = []
    affected: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(raw_targets, start=1):
        if not isinstance(item, Mapping):
            blockers.append(f"候选目标 #{index} 不是结构化对象。")
            continue
        source_project = str(item.get("source_project") or "").strip()
        target_project = str(item.get("target_project") or "").strip()
        endpoint = str(item.get("endpoint") or "").strip()
        source_paths = item.get("source_paths") or []
        if not source_paths and item.get("source_path"):
            source_paths = [item.get("source_path")]
        source_relative: list[str] = []
        for raw_path in source_paths:
            path_project, relative = split_qualified_path(raw_path, expected_project=source_project)
            if path_project and source_project and path_project != source_project:
                blockers.append(f"候选目标 #{index} 的 source_path 项目与 source_project 不一致。")
            safe = _safe_relative_path(relative)
            if safe:
                source_relative.append(safe)
        target_path_project, target_relative_raw = split_qualified_path(item.get("target_path"), expected_project=target_project)
        target_relative = _safe_relative_path(target_relative_raw)
        entry_relative: list[str] = []
        for raw_path in item.get("entry_paths") or []:
            path_project, relative = split_qualified_path(raw_path, expected_project=source_project)
            if path_project and source_project and path_project != source_project:
                blockers.append(f"候选目标 #{index} 的 entry_path 项目与 source_project 不一致。")
            safe = _safe_relative_path(relative)
            if safe:
                entry_relative.append(safe)
        missing = []
        if not source_project:
            missing.append("source_project")
        if not target_project:
            missing.append("target_project")
        if not endpoint:
            missing.append("endpoint")
        if not source_relative:
            missing.append("source_paths")
        if not target_relative:
            missing.append("target_path")
        if not item.get("controller_verified"):
            missing.append("controller_verified")
        if missing:
            blockers.append(f"候选目标 #{index} 缺少或未验证：{', '.join(missing)}。")
            continue
        if source_project not in project_index or target_project not in project_index:
            blockers.append(f"候选目标 #{index} 的项目未被选中或不存在：{source_project}, {target_project}。")
            continue
        if not bool(project_index[source_project].get("exists")) or not bool(project_index[target_project].get("exists")):
            blockers.append(f"候选目标 #{index} 的仓库路径不存在：{source_project}, {target_project}。")
            continue
        if target_path_project and target_path_project != target_project:
            blockers.append(f"候选目标 #{index} 的 target_path 项目与 target_project 不一致。")
            continue
        key = (source_project, endpoint, target_project)
        if key in seen:
            blockers.append(f"候选目标 #{index} 与已有目标重复：{source_project} -> {endpoint} -> {target_project}。")
            continue
        seen.add(key)
        normalized = {
            "scope": str(item.get("scope") or "candidate_change"),
            "source_project": source_project,
            "source_paths": sorted(set(source_relative)),
            "entry_paths": sorted(set(entry_relative)),
            "endpoint": endpoint,
            "target_project": target_project,
            "target_path": target_relative,
            "controller_verified": True,
        }
        normalized_targets.append(normalized)
        for project_name, paths, role in (
            (source_project, sorted(set(source_relative + entry_relative)), project_index[source_project].get("role")),
            (target_project, [target_relative], project_index[target_project].get("role")),
        ):
            record = affected.setdefault(
                project_name,
                {
                    "role": str(role or ""),
                    "project_path": str(project_index[project_name].get("path") or ""),
                    "allowed_paths": [],
                    "verify_commands": [],
                },
            )
            record["allowed_paths"] = sorted(set(record["allowed_paths"] + paths))

    for project_name in sorted(affected):
        project = project_index[project_name]
        role = str(project.get("role") or "")
        if role not in _ALLOWED_ROLES:
            blockers.append(f"项目 {project_name} 的角色未受支持：{role or '-'}。")
        commands = commands_by_project.get(project_name) or []
        commands = [str(command).strip() for command in commands if str(command).strip()]
        if not commands:
            blockers.append(f"项目 {project_name} 没有受信的逐仓库验证命令。")
        affected[project_name]["verify_commands"] = commands

    if blockers or len(normalized_targets) != len(raw_targets):
        evidence_gaps, evidence_options = build_evidence_choices(
            sorted(set(blockers)),
            architecture_decision=architecture_decision,
        )
        runtime_candidates = {
            project_name: suggest_runtime_commands(str(record.get("project_path") or ""))
            for project_name, record in affected.items()
        }
        for option in evidence_options:
            if option.get("id") == "provide_runtime_validation":
                option["candidate_commands_by_project"] = runtime_candidates
        continuation = build_continuation_state(
            blockers=sorted(set(blockers)),
            architecture_decision=architecture_decision,
            contract_proposals=contract_proposals,
        )
        return MultiServiceChangeContract(
            objective=str(decision.get("summary") or ""),
            blockers=sorted(set(blockers)),
            evidence_gaps=evidence_gaps,
            evidence_options=evidence_options,
            continuation=continuation,
            acceptance=acceptance_data,
            runtime_validation=dict(runtime),
            architecture_decision=architecture_decision,
            contract_proposals=contract_proposals,
        )

    return MultiServiceChangeContract(
        status="ready",
        objective=str(decision.get("summary") or ""),
        targets=normalized_targets,
        repositories=affected,
        blockers=[],
        evidence_gaps=[],
        evidence_options=[],
        continuation={"status": "ready_for_execution", "default": "review_then_apply"},
        acceptance=acceptance_data,
        runtime_validation=dict(runtime),
        architecture_decision=architecture_decision,
        contract_proposals=contract_proposals,
        rollback={
            "status": "ready",
            "strategy": "只在每个临时 worktree 的合同路径、定向验证和独立 diff 审查全部通过后，允许显式写回；失败则丢弃临时 worktree，不触碰原仓库。",
        },
    )
