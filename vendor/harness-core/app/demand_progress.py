"""Business-facing progress and confirmation snapshots for requirement runs.

The workflow already has a technical stage ledger.  This module deliberately
does not replace it; it translates that ledger into a small, sanitized view a
business user can understand without reading backend code or provider payloads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


USER_STAGE_LABELS = {
    "intake": "需求接收",
    "analysis": "证据分析",
    "scope_confirmation": "范围与证据确认",
    "pre_change_confirmation": "改动前确认",
    "implementation": "工程修改",
    "verification": "验证与审核",
    "business_acceptance": "改动后业务确认",
    "closure": "归档",
}

_ANALYSIS_EVENTS = {
    "provider_evidence",
    "calibration",
    "technical_decision",
    "ownership",
    "acceptance",
}

_CHANGE_PROJECT_SCOPES = {"change_required", "candidate_change"}
_PROJECT_SCOPE_DESCRIPTIONS = {
    "change_required": "需求已命中实际调用链，进入改动范围",
    "candidate_change": "已定位到实际调用链，仍需改动合同确认",
    "existing_dependency": "现有依赖，仅用于链路证据，不代表要改",
    "contract_check": "仅用于接口契约核验，不代表要改",
    "impact_regression": "仅用于影响回归核验，不代表要改",
    "entry_point": "仅为入口证据，不代表要改",
    "candidate_only": "仅候选，未形成实际改动证据",
    "legacy_selected": "旧数据未记录分层，暂按已选择项目展示",
}


def build_demand_progress_snapshot(
    *,
    phase: str,
    task_events: Sequence[Mapping[str, Any]],
    requirement_calibration: Mapping[str, Any] | None,
    technical_decision: Mapping[str, Any] | None,
    change_ownership: Mapping[str, Any] | None,
    governance: Mapping[str, Any] | None,
    single_pass_contract: Mapping[str, Any] | None,
    run_status: str,
    evaluation_status: str,
    execution_mode: str,
    scope_confirmation_status: str = "",
    scope_confirmation_reason: str = "",
    readonly_analysis_complete: bool = False,
) -> dict[str, Any]:
    """Build a sanitized, user-facing status snapshot.

    ``phase`` is either ``pre_change`` or ``post_change``.  The function is
    intentionally pure so it can be used in replay tests and in the CLI
    without changing the existing mutation gates.
    """
    if phase not in {"pre_change", "post_change"}:
        raise ValueError("phase must be pre_change or post_change")

    events = [dict(item) for item in task_events if isinstance(item, Mapping)]
    by_stage = {str(item.get("stage") or ""): item for item in events}
    calibration = dict(requirement_calibration or {})
    technical = dict(technical_decision or {})
    ownership = dict(change_ownership or {})
    governance_data = dict(governance or {})
    contract = dict(single_pass_contract or {})
    multi_service_contract = technical.get("multi_service_change_contract")
    continuation = (
        dict(multi_service_contract.get("continuation") or {})
        if isinstance(multi_service_contract, Mapping)
        else {}
    )

    stage_statuses = _build_stage_statuses(
        phase=phase,
        by_stage=by_stage,
        governance=governance_data,
        contract=contract,
        run_status=run_status,
        scope_confirmation_status=scope_confirmation_status,
    )
    current_stage = _current_user_stage(stage_statuses, phase=phase)
    open_questions = _open_questions(calibration, technical, governance_data, contract)
    affected_scope = _affected_scope(technical, ownership)
    can_modify = bool(
        governance_data.get("can_modify") is True
        and contract.get("status") == "ready"
        and technical.get("can_patch") is True
    )
    confirmation = _confirmation(
        phase=phase,
        can_modify=can_modify,
        open_questions=open_questions,
        verification_status=(by_stage.get("verification") or {}).get("status"),
    )
    if (
        readonly_analysis_complete
        and phase == "pre_change"
        and not _has_explicit_user_confirmation_question(calibration)
    ):
        confirmation = {
            "required": False,
            "gate": "none",
            "required_by": "system",
            "can_modify": False,
            "message": "只读服务边界分析已完成；剩余阻断项已转为架构选择，不要求重复补充已扫描证据。",
        }
    if phase == "pre_change" and scope_confirmation_status:
        # ``not_required`` means that readonly mode does not need a mutation
        # scope token; it must not hide an independent evidence/business gate
        # already detected from calibration or governance questions.
        if not (
            scope_confirmation_status in {"not_required", "confirmed"}
            and confirmation.get("required")
        ):
            confirmation = {
                **confirmation,
                "required": scope_confirmation_status not in {"not_required", "confirmed"},
                "gate": "pre_change_scope" if scope_confirmation_status != "not_required" else "",
                "required_by": "Harness 改动前范围确认令牌" if scope_confirmation_status != "not_required" else "",
                "message": scope_confirmation_reason or confirmation.get("message") or "",
                "status": scope_confirmation_status,
            }
    next_action = _next_action(
        phase=phase,
        current_stage=current_stage,
        can_modify=can_modify,
        open_questions=open_questions,
        verification_status=(by_stage.get("verification") or {}).get("status"),
        run_status=run_status,
        evaluation_status=evaluation_status,
        readonly_analysis_complete=readonly_analysis_complete,
        continuation=continuation,
    )
    return {
        "schema_version": "demand-progress.v1",
        "phase": phase,
        "current_stage": current_stage,
        "current_stage_label": USER_STAGE_LABELS[current_stage],
        "stage_statuses": stage_statuses,
        "completed": [
            key for key, value in stage_statuses.items() if value["status"] == "completed"
        ],
        "next_action": next_action,
        "continuation": continuation,
        "affected_scope": affected_scope,
        "evidence_level": _evidence_level(
            technical=technical,
            by_stage=by_stage,
            phase=phase,
        ),
        "confirmation": confirmation,
        "open_questions": open_questions,
        "proposed_subtasks": _safe_subtasks(calibration.get("proposed_subtasks")),
        "execution_mode": execution_mode,
        "run_status": run_status,
        "evaluation_status": evaluation_status,
    }


def demand_progress_to_markdown(snapshot: Mapping[str, Any]) -> str:
    """Render a compact business-facing progress card."""
    phase = str(snapshot.get("phase") or "pre_change")
    title = "改动前进度与确认" if phase == "pre_change" else "改动后进度与确认"
    confirmation = snapshot.get("confirmation") or {}
    scope = snapshot.get("affected_scope") or {}
    lines = [
        f"## {title}",
        "",
        f"- 当前阶段：**{snapshot.get('current_stage_label') or '-'}** (`{snapshot.get('current_stage') or '-'}`)",
        f"- 证据级别：`{snapshot.get('evidence_level') or '-'}`",
        f"- 下一步：{snapshot.get('next_action') or '-'}",
        f"- 是否需要用户确认：{'是' if confirmation.get('required') else '否'}",
    ]
    continuation = snapshot.get("continuation") or {}
    if continuation:
        lines.append(
            f"- 自动继续：`{continuation.get('status') or '-'}`；"
            f"是否需要用户：{'是' if continuation.get('requires_user', True) else '否'}"
        )
    if confirmation.get("required"):
        lines.append(f"- 确认类型：`{confirmation.get('gate') or '-'}`；确认对象：{confirmation.get('required_by') or '-'}")
    if confirmation.get("message"):
        lines.append(f"- 说明：{confirmation.get('message')}")
    lines.extend(["", "### 已完成阶段", ""])
    for key, item in (snapshot.get("stage_statuses") or {}).items():
        status = item.get("status") or "pending"
        reason = item.get("reason") or ""
        lines.append(f"- {USER_STAGE_LABELS.get(key, key)}：`{status}`{f'；{reason}' if reason else ''}")
    lines.extend(["", "### 影响范围", ""])
    projects = scope.get("projects") or []
    if not projects:
        lines.append("- 尚未形成项目范围。")
    for project in projects:
        role = project.get("role") or "unknown"
        selection_scope = project.get("selection_scope") or "legacy_selected"
        description = _PROJECT_SCOPE_DESCRIPTIONS.get(selection_scope, selection_scope)
        lines.append(
            f"- `{project.get('name') or '-'}`（{role}，{selection_scope}）："
            f"{project.get('path') or '-'}；{description}"
        )
    paths = scope.get("paths") or []
    if paths:
        lines.append(f"- 候选改动路径：{', '.join(paths)}")
    lines.extend(["", "### 证据与核验项目", ""])
    evidence_projects = scope.get("evidence_projects") or []
    if not evidence_projects:
        lines.append("- 无。")
    for project in evidence_projects:
        role = project.get("role") or "unknown"
        selection_scope = project.get("selection_scope") or "legacy_selected"
        description = _PROJECT_SCOPE_DESCRIPTIONS.get(selection_scope, selection_scope)
        lines.append(
            f"- `{project.get('name') or '-'}`（{role}，{selection_scope}）："
            f"{project.get('path') or '-'}；{description}"
        )
    lines.extend(["", "### 待补充或待确认", ""])
    questions = snapshot.get("open_questions") or []
    if not questions:
        lines.append("- 无。")
    else:
        lines.extend(f"- {question}" for question in questions)
    subtasks = snapshot.get("proposed_subtasks") or []
    if subtasks:
        lines.extend(["", "### 建议拆解", ""])
        lines.extend(
            f"- `{item.get('id') or '-'}`：{item.get('title') or '-'}；{item.get('boundary') or '-'}"
            for item in subtasks
        )
    return "\n".join(lines)


def _build_stage_statuses(
    *,
    phase: str,
    by_stage: Mapping[str, Mapping[str, Any]],
    governance: Mapping[str, Any],
    contract: Mapping[str, Any],
    run_status: str,
    scope_confirmation_status: str = "",
) -> dict[str, dict[str, str]]:
    statuses: dict[str, dict[str, str]] = {
        "intake": _event_status(by_stage.get("intake"), default="pending"),
        "analysis": _aggregate_analysis(by_stage),
        "scope_confirmation": _scope_status(by_stage, governance, contract),
        "pre_change_confirmation": {
            "status": (
                scope_confirmation_status
                if phase == "pre_change" and scope_confirmation_status
                else "pending" if phase == "pre_change" else "completed"
            ),
            "reason": (
                "等待改动范围确认"
                if phase == "pre_change" and not scope_confirmation_status
                else "改动前范围已冻结"
                if phase != "pre_change" or scope_confirmation_status == "confirmed"
                else "改动前范围未确认，不进入改码"
            ),
        },
        "implementation": _event_status(
            by_stage.get("local_engineering"),
            default="pending",
        ),
        "verification": _event_status(by_stage.get("verification"), default="pending"),
        "business_acceptance": {
            "status": "pending" if phase == "post_change" else "not_started",
            "reason": "等待用户确认业务效果" if phase == "post_change" else "改动完成后进入",
        },
        "closure": _event_status(by_stage.get("audit"), default="pending"),
    }
    if run_status in {"failed", "blocked"} and phase == "post_change":
        statuses["business_acceptance"] = {
            "status": "blocked",
            "reason": "运行未闭环，不能进入业务验收",
        }
    return statuses


def _aggregate_analysis(by_stage: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    relevant = [by_stage.get(key) for key in _ANALYSIS_EVENTS if by_stage.get(key)]
    if any(item.get("status") in {"blocked", "failed"} for item in relevant):
        return {"status": "blocked", "reason": "分析阶段仍有证据或判断阻断"}
    if relevant and all(item.get("status") == "completed" for item in relevant):
        return {"status": "completed", "reason": "已完成需求、工程和验收分析"}
    return {"status": "pending", "reason": "分析尚未完成"}


def _scope_status(
    by_stage: Mapping[str, Mapping[str, Any]],
    governance: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, str]:
    governance_status = str(governance.get("status") or "")
    if governance_status.startswith("blocked") or by_stage.get("governance", {}).get("status") in {"blocked", "failed"}:
        return {"status": "blocked", "reason": by_stage.get("governance", {}).get("reason") or "需求范围或证据未闭合"}
    if contract.get("status") == "ready" or by_stage.get("single_pass_contract", {}).get("status") == "completed":
        return {"status": "completed", "reason": "改动合同已形成"}
    return {"status": "pending", "reason": "等待改动合同"}


def _current_user_stage(stage_statuses: Mapping[str, Mapping[str, str]], *, phase: str) -> str:
    if phase == "post_change":
        # A post-change snapshot is also emitted for blocked/readonly runs so
        # the run remains replayable.  Do not present that bookkeeping card as
        # if business acceptance were ready when no local change happened.
        implementation_status = stage_statuses["implementation"]["status"]
        verification_status = stage_statuses["verification"]["status"]
        if implementation_status in {"pending", "skipped"} and verification_status in {
            "pending",
            "skipped",
        }:
            if stage_statuses["scope_confirmation"]["status"] in {"blocked", "failed", "pending"}:
                return "scope_confirmation"
            return "pre_change_confirmation"
        for key in ("implementation", "verification", "business_acceptance"):
            status = stage_statuses[key]["status"]
            if status in {"blocked", "failed", "pending"}:
                return key
        return "closure"
    if stage_statuses["analysis"]["status"] in {"pending", "blocked", "failed"}:
        return "analysis"
    if stage_statuses["scope_confirmation"]["status"] in {"pending", "blocked", "failed"}:
        return "scope_confirmation"
    return "pre_change_confirmation"


def _confirmation(
    *,
    phase: str,
    can_modify: bool,
    open_questions: Sequence[str],
    verification_status: str | None,
) -> dict[str, Any]:
    if phase == "pre_change":
        if open_questions:
            return {
                "required": True,
                "gate": "evidence_or_business_scope",
                "required_by": "user",
                "can_modify": False,
                "message": "只需要补充业务口径或证据，不需要审核后端实现。",
            }
        return {
            "required": can_modify,
            "gate": "pre_change_scope" if can_modify else "none",
            "required_by": "user" if can_modify else "system",
            "can_modify": can_modify,
            "message": "请确认前端、BFF、后端服务和验证范围。" if can_modify else "当前尚未达到改动条件。",
        }
    return {
        "required": verification_status == "completed",
        "gate": "business_acceptance" if verification_status == "completed" else "none",
        "required_by": "business" if verification_status == "completed" else "system",
        "can_modify": False,
        "message": "只确认页面效果和业务结果，不要求用户审核后端代码。" if verification_status == "completed" else "验证未完成，暂不能进行业务验收。",
    }


def _next_action(
    *,
    phase: str,
    current_stage: str,
    can_modify: bool,
    open_questions: Sequence[str],
    verification_status: str | None,
    run_status: str,
    evaluation_status: str,
    readonly_analysis_complete: bool = False,
    continuation: Mapping[str, Any] | None = None,
) -> str:
    continuation_data = dict(continuation or {})
    if continuation_data.get("status") == "auto_continue_readonly":
        return (
            f"Harness 自动继续只读分析：{continuation_data.get('next_action') or '继续收集源码证据'}；"
            "改码门禁保持关闭，不需要你重复发送“继续”。"
        )
    if continuation_data.get("status") == "await_user_choice":
        return (
            continuation_data.get("next_action")
            or "请补充证据或业务口径后再继续，改码门禁保持关闭。"
        )
    if readonly_analysis_complete:
        if phase == "pre_change":
            return "只读服务边界分析已完成；自动改码门禁保持关闭，下一步生成架构选择和改动合同，不等待重复补充已扫描证据。"
        return "本次只读分析未产生业务代码改动；保留服务图和阻断项，下一步按选定架构进入受控变更。"
    if open_questions:
        return "请补充上面列出的业务口径或证据，Harness 会继续只读分析。"
    if phase == "pre_change" and can_modify:
        return "准备改动前端/后端前，请确认上面的改动范围和验收条件。"
    if phase == "post_change" and verification_status == "completed":
        return "请确认页面效果、按钮行为和业务结果是否符合需求。"
    if run_status in {"failed", "blocked"}:
        return f"先处理 `{evaluation_status or current_stage}` 阻断项，暂不进入下一阶段。"
    return "Harness 将继续执行下一阶段，并在阶段结束时生成新的进度快照。"


def _event_status(event: Mapping[str, Any] | None, *, default: str) -> dict[str, str]:
    if not event:
        return {"status": default, "reason": "尚未开始"}
    return {
        "status": str(event.get("status") or default),
        "reason": str(event.get("reason") or event.get("reason_code") or ""),
    }


def _open_questions(
    calibration: Mapping[str, Any],
    technical: Mapping[str, Any],
    governance: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> list[str]:
    questions: list[str] = []
    for key in ("must_confirm", "warnings"):
        values = calibration.get(key) or []
        for value in values:
            if isinstance(value, Mapping):
                text = str(value.get("message") or value.get("question") or "").strip()
            else:
                text = str(value).strip()
            if text and text not in questions:
                questions.append(text)
    for source in (technical, governance, contract):
        for key in ("blockers", "missing_information", "unsupported_reasons"):
            values = source.get(key) or []
            for value in values:
                text = str(value).strip()
                if text and text not in questions:
                    questions.append(text)
    return questions[:30]


def _affected_scope(
    technical: Mapping[str, Any],
    ownership: Mapping[str, Any],
) -> dict[str, Any]:
    projects: list[dict[str, str]] = []
    evidence_projects: list[dict[str, str]] = []
    for item in technical.get("selected_projects") or []:
        if not isinstance(item, Mapping):
            continue
        selection_scope = str(item.get("selection_scope") or "legacy_selected")
        project = {
            "name": str(item.get("name") or "-"),
            "path": str(item.get("path") or "-"),
            "role": str(item.get("role") or "unknown"),
            "selection_scope": selection_scope,
        }
        if selection_scope in _CHANGE_PROJECT_SCOPES or selection_scope == "legacy_selected":
            projects.append(project)
        else:
            evidence_projects.append(project)
    paths: list[str] = []
    for value in (technical.get("recommended_allowed_paths") or []):
        text = str(value).strip()
        if text and text not in paths:
            paths.append(text)
    for value in ownership.values():
        if not isinstance(value, Mapping):
            continue
        for path in value.get("paths") or value.get("allowed_paths") or []:
            text = str(path).strip()
            if text and text not in paths:
                paths.append(text)
    return {
        "projects": projects,
        "evidence_projects": evidence_projects,
        "paths": paths[:80],
    }


def _evidence_level(
    *,
    technical: Mapping[str, Any],
    by_stage: Mapping[str, Mapping[str, Any]],
    phase: str,
) -> str:
    if phase == "post_change" and by_stage.get("verification", {}).get("status") == "completed":
        return "local_verification"
    if technical.get("service_graph") or technical.get("field_provenance"):
        return "code_evidence"
    if by_stage.get("technical_decision", {}).get("status") == "completed":
        return "technical_analysis"
    return "requirement_only"


def _safe_subtasks(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(value, (list, tuple)):
        return result
    for item in value:
        if not isinstance(item, Mapping):
            continue
        result.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "boundary": str(item.get("boundary") or ""),
            }
        )
    return result


def _has_explicit_user_confirmation_question(calibration: Mapping[str, Any]) -> bool:
    """Keep genuine business/source conflicts human-gated in readonly mode."""
    if calibration.get("must_confirm"):
        return True
    for warning in calibration.get("warnings") or []:
        if not isinstance(warning, Mapping):
            continue
        if str(warning.get("type") or "") in {"business_decision_unresolved", "source_conflict"}:
            return True
    return False
