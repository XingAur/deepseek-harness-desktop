from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.project_context import (
    DEFAULT_SENSITIVE_KEYWORDS,
    FINANCIAL_HIGH_RISK_KEYWORDS,
    LOW_RISK_KEYWORDS,
    MEDIUM_RISK_KEYWORDS,
    PREFERENTIAL_RISK_KEYWORDS,
    unique_keep_order,
)
from app.requirement_calibration import (
    default_value_precedence_is_resolved,
    normalize_business_risk_text,
    remove_negated_scope_clauses,
    split_requirement_scope,
)


MATRIX_VERSION = "0.8.7"

DIRECT_FLOW_TERMS = ["直接流转", "直接转", "自动流转", "自动转", "改状态", "状态流转"]
DIRECT_CLOSE_TERMS = ["直接关闭", "自动关闭", "关闭任务", "关单", "完成并关闭"]
SKIP_VERIFY_TERMS = ["不测试", "不用测试", "无需测试", "跳过测试", "不用验证", "无需验证", "没测直接"]
AUTO_DEPLOY_TERMS = ["直接上线", "自动发布", "直接发布", "自动部署", "跳过审核"]
NO_HUMAN_TERMS = ["无需人工", "不需要人工", "不用确认", "无需确认"]
BACKEND_STATE_TERMS = ["状态流转", "状态修改", "状态更新", "状态值", "状态码", "审核状态", "结算状态", "支付状态", "退费状态"]


def build_acceptance_matrix(
    *,
    title: str,
    demand_text: str,
    evidence_bundle: dict | None = None,
    yunxiao_evidence: dict | None = None,
    project_paths: list[str] | None = None,
    verify_commands: list[str] | None = None,
    execution_mode: str = "readonly",
    yunxiao_transaction_mode: str = "off",
    yunxiao_write_scope: str = "comment-only",
    default_value_precedence: dict | None = None,
) -> dict:
    demand_source_text = "\n".join(part for part in [demand_text, clean_yunxiao_text(yunxiao_evidence)] if part).strip()
    _, text = split_requirement_scope(title=title, demand_text=demand_source_text)
    evidence_bundle = evidence_bundle or {}
    risk = build_risk(demand_text=text, evidence_bundle=evidence_bundle)
    categories = infer_categories(text=text, evidence_bundle=evidence_bundle)
    project_profiles = build_project_profiles(
        project_paths=project_paths or [],
        evidence_bundle=evidence_bundle,
        explicit_verify_commands=verify_commands or [],
    )
    challenge_reviews = build_challenge_reviews(
        demand_text=text,
        risk_level=risk["level"],
        execution_mode=execution_mode,
        yunxiao_transaction_mode=yunxiao_transaction_mode,
        yunxiao_write_scope=yunxiao_write_scope,
    )
    requirement_acceptance = build_requirement_acceptance(
        demand_text=text,
        categories=categories,
        risk_level=risk["level"],
        yunxiao_evidence=yunxiao_evidence,
        default_value_precedence=default_value_precedence,
    )
    auto_verification = build_auto_verification(
        evidence_bundle=evidence_bundle,
        project_profiles=project_profiles,
        explicit_verify_commands=verify_commands or [],
        categories=categories,
        risk_level=risk["level"],
    )
    manual_acceptance = build_manual_acceptance(
        demand_text=text,
        evidence_bundle=evidence_bundle,
        categories=categories,
        risk_level=risk["level"],
        yunxiao_evidence=yunxiao_evidence,
    )
    blockers = build_blockers(
        evidence_bundle=evidence_bundle,
        project_profiles=project_profiles,
        challenge_reviews=challenge_reviews,
        risk_level=risk["level"],
        execution_mode=execution_mode,
        default_value_precedence=default_value_precedence,
    )
    decisions = build_decisions(
        blockers=blockers,
        challenge_reviews=challenge_reviews,
        risk_level=risk["level"],
        execution_mode=execution_mode,
    )
    return {
        "version": MATRIX_VERSION,
        "title": title,
        "risk": risk,
        "categories": sorted(categories),
        "decisions": decisions,
        "requirement_acceptance": requirement_acceptance,
        "auto_verification": auto_verification,
        "manual_acceptance": manual_acceptance,
        "blockers": blockers,
        "challenge_reviews": challenge_reviews,
        "project_profiles": project_profiles,
        "yunxiao_boundary": {
            "real_business_task_allowed_actions": ["read", "comment"],
            "blocked_real_actions": ["transition", "assign", "update_iteration", "upload_attachment", "update_service_change", "close"],
            "note": "v0.8.7 真实业务云效任务只允许读取和评论；状态、负责人、迭代和关闭只能 dry-run/fake。",
        },
        "definition": {
            "requirement_acceptance": "从需求本身推导的业务验收场景，必须能被研发、测试或业务人员复现。",
            "auto_verification": "可以由本地命令或自动化测试验证的工程检查；未显式执行时只能作为建议。",
            "manual_acceptance": "必须由人工基于截图、真实数据、政策口径或业务流程确认的验收项。",
        },
    }


def matrix_to_json(matrix: dict) -> str:
    return json.dumps(matrix, ensure_ascii=False, indent=2)


def matrix_to_markdown(matrix: dict) -> str:
    lines = [
        "## v0.8.7 需求验收矩阵",
        "",
        f"- 版本：{matrix.get('version') or MATRIX_VERSION}",
        f"- 风险等级：{(matrix.get('risk') or {}).get('level') or '-'}",
        f"- 风险原因：{'; '.join((matrix.get('risk') or {}).get('reasons') or []) or '-'}",
        f"- 识别范围：{', '.join(matrix.get('categories') or []) or '-'}",
        "",
        "### 闸口结论",
        "",
    ]
    decisions = matrix.get("decisions") or {}
    for key, label in [
        ("can_enter_development", "是否可进入开发"),
        ("can_auto_code", "是否可自动改码"),
        ("can_auto_commit", "是否可自动提交"),
        ("can_yunxiao_transition", "是否可真实云效流转"),
    ]:
        item = decisions.get(key) or {}
        lines.append(f"- {label}：{item.get('status') or '-'}，原因：{item.get('reason') or '-'}")

    challenge_reviews = matrix.get("challenge_reviews") or []
    lines.extend(["", "### 反驳/纠偏", ""])
    if not challenge_reviews:
        lines.append("- 未发现需要反驳的危险指令。")
    for item in challenge_reviews:
        lines.extend(
            [
                f"- 不建议这样做：{item.get('statement') or '-'}",
                f"  - 原因：{item.get('reason') or '-'}",
                f"  - 替代方案：{item.get('alternative') or '-'}",
            ]
        )

    lines.extend(["", "### 需求验收项", ""])
    append_acceptance_items(lines, matrix.get("requirement_acceptance") or [])
    lines.extend(["", "### 自动验证建议", ""])
    append_verification_items(lines, matrix.get("auto_verification") or [])
    lines.extend(["", "### 人工验收项", ""])
    append_acceptance_items(lines, matrix.get("manual_acceptance") or [])
    lines.extend(["", "### 阻断项", ""])
    blockers = matrix.get("blockers") or []
    if not blockers:
        lines.append("- 无阻断项；仍需按闸口结论执行人工审查。")
    for item in blockers:
        lines.append(f"- [{item.get('severity') or 'blocker'}] {item.get('message') or '-'}")

    lines.extend(["", "### 项目验证画像", ""])
    project_profiles = matrix.get("project_profiles") or []
    if not project_profiles:
        lines.append("- 未提供项目路径，无法生成项目级验证建议。")
    for profile in project_profiles:
        lines.extend(
            [
                f"- 项目：{profile.get('path') or '-'}",
                f"  - 状态：{profile.get('status') or '-'}",
                f"  - 类型：{profile.get('role') or '-'}",
                f"  - 建议命令：{', '.join(profile.get('recommended_commands') or []) or '-'}",
                f"  - 说明：{profile.get('note') or '-'}",
            ]
        )
    lines.extend(
        [
            "",
            "### 边界说明",
            "",
            "- v0.8.7 只生成验收矩阵和验证基座，不自动改业务代码、不提交、不推送、不发布。",
            "- 真实业务云效任务仍只允许读取和评论；状态、负责人、迭代和关闭必须等后续专用测试任务验证。",
        ]
    )
    return "\n".join(lines)


def build_prompt_context(matrix: dict, *, limit: int = 5000) -> str:
    text = matrix_to_markdown(matrix)
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n\n...（验收矩阵已压缩）...\n\n" + text[-limit // 2 :]


def build_risk(*, demand_text: str, evidence_bundle: dict) -> dict:
    demand_text = normalize_business_risk_text(demand_text)
    evidence_risk = (evidence_bundle.get("risk") or {}) if evidence_bundle else {}
    evidence_level = str(evidence_risk.get("level") or "")
    evidence_reasons = [str(item) for item in evidence_risk.get("reasons") or [] if item]
    if evidence_level:
        return {"level": evidence_level, "reasons": evidence_reasons or ["沿用工程证据包风险等级。"]}

    reasons: list[str] = []
    sensitive_hits = [keyword for keyword in DEFAULT_SENSITIVE_KEYWORDS if keyword in demand_text]
    financial_hits = [keyword for keyword in FINANCIAL_HIGH_RISK_KEYWORDS if keyword in demand_text]
    preferential_hits = [keyword for keyword in PREFERENTIAL_RISK_KEYWORDS if keyword in demand_text]
    if sensitive_hits and financial_hits:
        level = "critical" if {"医保", "结算"} & set(sensitive_hits) else "high"
        reasons.append("需求命中 HIS 高敏感和费用/结算类关键词：" + ", ".join(unique_keep_order(sensitive_hits + financial_hits)[:10]))
    elif sensitive_hits or preferential_hits:
        level = "high" if sensitive_hits else "medium"
        reasons.append("需求命中敏感业务关键词：" + ", ".join(unique_keep_order(sensitive_hits + preferential_hits)[:10]))
    elif any(keyword in demand_text for keyword in MEDIUM_RISK_KEYWORDS if keyword != "状态") or any(
        keyword in demand_text for keyword in BACKEND_STATE_TERMS
    ):
        level = "medium"
        reasons.append("需求涉及流程、接口、权限、事务或兼容。")
    elif any(keyword in demand_text for keyword in LOW_RISK_KEYWORDS):
        level = "low"
        reasons.append("需求主要为字段、页面、样式或文案。")
    else:
        level = "medium"
        reasons.append("需求信息不足，按中风险保守处理。")
    return {"level": level, "reasons": reasons}


def infer_categories(*, text: str, evidence_bundle: dict) -> set[str]:
    text = extract_change_category_text(text)
    text = normalize_business_risk_text(text)
    categories: set[str] = set()
    evidence_categories = ((evidence_bundle.get("impact") or {}).get("categories") or {}) if evidence_bundle else {}
    if any(term in text for term in ["页面", "界面", "字段", "展示", "按钮", "排班", "同步", "前端", "页签", "标签页", "刷新"]):
        categories.add("frontend")
    if any(term in text for term in ["接口", "保存", "流程", "事务", "日志", "权限", "后端"] + BACKEND_STATE_TERMS):
        categories.add("backend")
    if any(term in text for term in ["SQL", "数据库", "报表", "统计", "对账", "结算", "收费"]):
        categories.add("database_report")
    if any(term in text for term in ["自动化测试", "测试代码", "单元测试", "回归测试"]):
        categories.add("test")
    if not categories:
        for category, files in evidence_categories.items():
            if files and str(category) in {"frontend", "backend", "database_report", "config", "test"}:
                categories.add(str(category))
    if not categories:
        categories.add("unknown")
    return categories


def extract_change_category_text(text: str) -> str:
    """Keep read-only evidence and delivery notes from becoming code-change categories."""
    cleaned = text or ""
    for marker in ("\n只读代码证据：", "\n当前本地仓库边界："):
        cleaned = cleaned.split(marker, 1)[0]
    return cleaned


def _mentions_schedule(demand_text: str) -> bool:
    """Only schedule-specific wording should add schedule acceptance checks."""
    return (
        "一周排班" in demand_text
        and any(term in demand_text for term in ("今日排班", "同步", "确认", "确定"))
    ) or any(term in demand_text for term in ("同步今日排班", "排班确认后同步"))


def build_requirement_acceptance(
    *,
    demand_text: str,
    categories: set[str],
    risk_level: str,
    yunxiao_evidence: dict | None,
    default_value_precedence: dict | None = None,
) -> list[dict]:
    source = evidence_source(yunxiao_evidence)
    items = [
        acceptance_item(
            item_id="REQ-CORE-001",
            category="requirement_acceptance",
            scenario="核心需求场景可复现",
            preconditions="使用需求描述中的角色、菜单入口和样例数据。",
            steps="按需求描述复现当前问题，再执行修复后的同一操作。",
            expected_result="实际结果与需求期望一致，且不破坏原有主流程。",
            evidence=source,
        )
    ]
    if _mentions_schedule(demand_text):
        items.extend(
            [
                acceptance_item(
                    item_id="REQ-SCHEDULE-001",
                    category="requirement_acceptance",
                    scenario="一周排班确定时默认不同步今日排班",
                    preconditions="存在今日排班和未来一周排班数据，配置处于默认值。",
                    steps="进入一周排班确认流程，直接点击确定或保存。",
                    expected_result="系统不自动同步或覆盖今日排班，只保存本次一周排班确认结果。",
                    evidence=source,
                ),
                acceptance_item(
                    item_id="REQ-SCHEDULE-002",
                    category="requirement_acceptance",
                    scenario="需要同步今日排班时必须有明确动作或配置",
                    preconditions="用户具备排班权限，页面提供同步今日排班的显式选择或配置。",
                    steps="开启同步选项后确认一周排班。",
                    expected_result="只有在用户明确选择或配置允许时才同步今日排班，并有可追踪提示。",
                    evidence=source,
                ),
                acceptance_item(
                    item_id="REQ-SCHEDULE-003",
                    category="requirement_acceptance",
                    scenario="历史兼容和回归",
                    preconditions="存在已保存的一周排班、今日排班和不同操作员权限。",
                    steps="分别验证新增、修改、再次确认和取消流程。",
                    expected_result="历史排班不被误改，取消流程不产生同步副作用，权限不足时不允许操作。",
                    evidence=source,
                ),
            ]
        )
    if default_value_precedence_is_resolved(default_value_precedence):
        items.extend(
            [
                acceptance_item(
                    item_id="REQ-DEFAULT-PRECEDENCE-COMMON-FORM",
                    category="requirement_acceptance",
                    scenario="通用表单默认值优先",
                    preconditions="通用表单和参数均配置不同默认值，页面也存在硬编码默认值。",
                    steps="进入目标页面并新建或清屏。",
                    expected_result="使用通用表单设置的默认值，不读取参数或页面硬编码默认值。",
                    evidence=source,
                ),
                acceptance_item(
                    item_id="REQ-DEFAULT-PRECEDENCE-PARAMETER",
                    category="requirement_acceptance",
                    scenario="参数默认值作为第二优先级",
                    preconditions="通用表单未配置；参数和页面硬编码均配置不同默认值。",
                    steps="进入目标页面并新建或清屏。",
                    expected_result="使用参数默认值，不使用页面硬编码默认值。",
                    evidence=source,
                ),
                acceptance_item(
                    item_id="REQ-DEFAULT-PRECEDENCE-HARDCODED",
                    category="requirement_acceptance",
                    scenario="页面硬编码默认值作为第三优先级",
                    preconditions="通用表单和参数均未配置；页面存在硬编码默认值。",
                    steps="进入目标页面并新建或清屏。",
                    expected_result="使用页面硬编码默认值。",
                    evidence=source,
                ),
                acceptance_item(
                    item_id="REQ-DEFAULT-PRECEDENCE-NONE",
                    category="requirement_acceptance",
                    scenario="无默认值来源时保持无默认值",
                    preconditions="通用表单、参数和页面硬编码均不存在默认值。",
                    steps="进入目标页面并新建或清屏。",
                    expected_result="字段不被自动赋默认值。",
                    evidence=source,
                ),
            ]
        )
    if "frontend" in categories:
        items.append(
            acceptance_item(
                item_id="REQ-FE-001",
                category="requirement_acceptance",
                scenario="前端交互和异常态",
                preconditions="页面可正常打开，准备正常数据、空数据和接口异常数据。",
                steps="验证 loading、空数据、错误提示、权限态和重复操作。",
                expected_result="页面状态清晰，字段不越界，不因缺失字段或接口失败导致白屏。",
                evidence=source,
            )
        )
    if "backend" in categories:
        items.append(
            acceptance_item(
                item_id="REQ-BE-001",
                category="requirement_acceptance",
                scenario="后端保存与兼容",
                preconditions="准备新旧客户端入参、正常数据和异常数据。",
                steps="执行保存、查询、重复提交和异常回滚路径。",
                expected_result="接口兼容旧数据，异常有日志和明确错误，不产生脏数据或隐式状态变化。",
                evidence=source,
            )
        )
    if risk_level in {"high", "critical"}:
        items.append(
            acceptance_item(
                item_id="REQ-HIGH-001",
                category="requirement_acceptance",
                scenario="高敏感业务口径验收",
                preconditions="业务负责人确认收费、结算、医保、报表、对账或政策口径。",
                steps="使用真实或脱敏样例数据验证正常、异常、回滚和对账路径。",
                expected_result="口径与人工确认一致，异常不被误判成功，保留人工验收证据。",
                evidence=source,
            )
        )
    return items


def build_auto_verification(
    *,
    evidence_bundle: dict,
    project_profiles: list[dict],
    explicit_verify_commands: list[str],
    categories: set[str],
    risk_level: str,
) -> list[dict]:
    commands: list[tuple[str, str, bool]] = []
    for command in explicit_verify_commands:
        commands.append((command, "explicit", True))
    for command in evidence_bundle.get("suggested_commands") or []:
        commands.append((str(command), "evidence_suggested", False))
    for profile in project_profiles:
        for command in profile.get("recommended_commands") or []:
            commands.append((str(command), "project_profile", False))

    items: list[dict] = []
    for index, (command, source, explicit) in enumerate(unique_command_tuples(commands), start=1):
        if source == "project_profile":
            execute_policy = "Harness 已从仓库配置自动发现；进入受控 worktree 后自动执行，失败即阻断写回。"
        else:
            execute_policy = "只有用户通过 --verify-command 显式传入时才执行；推荐命令不会自动运行。"
        items.append(
            {
                "id": f"AUTO-{index:03d}",
                "type": classify_command(command),
                "command": command,
                "source": source,
                "explicitly_executable": explicit,
                "execute_policy": execute_policy,
                "side_effect_policy": "验证命令如修改 worktree，v0.7.3+ 必须报告或阻断。",
            }
        )
    if "frontend" in categories and not any(item["type"] in {"static_check", "build_check", "unit_test"} for item in items):
        items.append(default_verification("AUTO-FE-MANUAL", "cannot_verify", "未识别前端自动化命令，需要人工指定 lint/build/test。"))
    if "backend" in categories and not any(item["type"] in {"unit_test", "api_test"} for item in items):
        items.append(default_verification("AUTO-BE-MANUAL", "cannot_verify", "未识别后端自动化命令，需要人工指定单测或接口验证。"))
    if risk_level in {"high", "critical"}:
        items.append(default_verification("AUTO-HIGH-MANUAL", "manual_acceptance", "高敏感需求不能仅靠自动化命令通过，必须附人工验收证据。"))
    return items


def build_manual_acceptance(
    *,
    demand_text: str,
    evidence_bundle: dict,
    categories: set[str],
    risk_level: str,
    yunxiao_evidence: dict | None,
) -> list[dict]:
    source = evidence_source(yunxiao_evidence)
    confirmations = [str(item) for item in evidence_bundle.get("human_confirmations") or []]
    items = [
        acceptance_item(
            item_id="MANUAL-001",
            category="manual_acceptance",
            scenario="需求口径人工确认",
            preconditions="需求提出人、测试或业务负责人可确认需求边界。",
            steps="核对需求描述、截图、影响菜单、角色权限和验收样例。",
            expected_result="确认哪些内容属于本次需求，哪些内容不应由 AI 自行推断。",
            evidence=source,
        )
    ]
    for index, confirmation in enumerate(confirmations[:8], start=2):
        items.append(
            acceptance_item(
                item_id=f"MANUAL-{index:03d}",
                category="manual_acceptance",
                scenario=confirmation,
                preconditions="具备对应业务角色、样例数据和验收环境。",
                steps="按人工确认项逐条核对，并保留截图或测试记录。",
                expected_result="确认项有明确结论；不明确时不得进入自动提交或云效状态流转。",
                evidence="工程证据包 human_confirmations",
            )
        )
    if _mentions_schedule(demand_text):
        items.append(
            acceptance_item(
                item_id="MANUAL-SCHEDULE-001",
                category="manual_acceptance",
                scenario="排班同步规则人工验收",
                preconditions="测试环境存在今日排班和未来排班，业务负责人确认默认值。",
                steps="人工验证默认不同步、显式同步、取消保存、权限不足四类路径。",
                expected_result="今日排班不会被默认覆盖；需要同步时有明确选择和结果提示。",
                evidence=source,
            )
        )
    if risk_level in {"high", "critical"} or "database_report" in categories:
        items.append(
            acceptance_item(
                item_id="MANUAL-HIGH-001",
                category="manual_acceptance",
                scenario="高风险口径和数据结果人工复核",
                preconditions="准备真实或脱敏样例数据、对账口径和业务负责人。",
                steps="核对收费/结算/医保/报表/对账结果与需求口径。",
                expected_result="人工验收记录明确通过或失败；未通过不得流转完成。",
                evidence=source,
            )
        )
    return unique_items_by_id(items)


def build_blockers(
    *,
    evidence_bundle: dict,
    project_profiles: list[dict],
    challenge_reviews: list[dict],
    risk_level: str,
    execution_mode: str,
    default_value_precedence: dict | None = None,
) -> list[dict]:
    blockers: list[dict] = []
    if not evidence_bundle:
        blockers.append(
            {
                "id": "BLOCK-NO-EVIDENCE",
                "severity": "warning",
                "message": "未提供工程证据包，不能自动改码、提交或给出确定代码位置。",
            }
        )
    if not project_profiles:
        blockers.append(
            {
                "id": "BLOCK-NO-PROJECT",
                "severity": "warning",
                "message": "未提供业务项目路径，无法生成项目级验证命令和项目结构画像。",
            }
        )
    missing_projects = [item for item in project_profiles if item.get("status") != "ok"]
    for item in missing_projects:
        blockers.append(
            {
                "id": "BLOCK-PROJECT-PATH",
                "severity": "blocker",
                "message": f"项目路径不可用：{item.get('path')}，原因：{item.get('note')}",
            }
        )
    if risk_level in {"high", "critical"}:
        blockers.append(
            {
                "id": "BLOCK-HIGH-RISK-MANUAL",
                "severity": "manual_gate",
                "message": "高风险 HIS 需求必须人工确认验收口径，不能自动提交、发布或真实云效流转。",
            }
        )
    for index, item in enumerate(challenge_reviews, start=1):
        blockers.append(
            {
                "id": f"BLOCK-CHALLENGE-{index:03d}",
                "severity": "blocker",
                "message": f"{item.get('statement')}；替代方案：{item.get('alternative')}",
            }
        )
    if execution_mode != "readonly":
        blockers.append(
            {
                "id": "BLOCK-V087-NO-AUTO-COMMIT",
                "severity": "boundary",
                "message": "v0.8.7 只补验收矩阵；worktree/review 可保留原能力，但本版本不新增自动提交或真实云效状态流转。",
            }
        )
    if isinstance(default_value_precedence, dict) and default_value_precedence.get("required") and not default_value_precedence_is_resolved(default_value_precedence):
        blockers.append(
            {
                "id": "BLOCK-DEFAULT-PRECEDENCE",
                "severity": "blocker",
                "message": "默认值来源优先级未闭合，禁止把通用表单、参数或页面默认值简化为单一字段默认。",
            }
        )
    return blockers


def build_decisions(*, blockers: list[dict], challenge_reviews: list[dict], risk_level: str, execution_mode: str) -> dict:
    hard_blocked = any(item.get("severity") == "blocker" for item in blockers)
    can_enter_development_status = "blocked" if hard_blocked else "allowed_after_human_review"
    can_enter_reason = "存在必须先处理的危险指令或项目路径问题。" if hard_blocked else "可作为研发输入，但需人工审查验收矩阵。"
    return {
        "can_enter_development": {"status": can_enter_development_status, "reason": can_enter_reason},
        "can_auto_code": {
            "status": "blocked_in_v0.8.7",
            "reason": "v0.8.7 是验收基座版本，不自动生成业务代码；后续 v0.8.8 才可进入 worktree 自动改码。",
        },
        "can_auto_commit": {
            "status": "blocked",
            "reason": "未建立需求验收通过、代码审查通过和提交策略前，不允许自动提交。",
        },
        "can_yunxiao_transition": {
            "status": "blocked",
            "reason": "真实业务云效状态流转需专用测试任务和已确认状态机；当前只允许读取和评论。",
        },
        "high_risk_gate": {
            "status": "manual_required" if risk_level in {"high", "critical"} else "not_required",
            "reason": "高敏感 HIS 需求必须人工验收。" if risk_level in {"high", "critical"} else "未命中高敏感闸口。",
        },
        "challenge_gate": {
            "status": "blocked" if challenge_reviews else "pass",
            "reason": "存在不合理或危险指令，Harness 已给出替代方案。" if challenge_reviews else "未发现需要反驳的危险指令。",
        },
    }


def build_challenge_reviews(*, demand_text: str, risk_level: str, execution_mode: str, yunxiao_transaction_mode: str, yunxiao_write_scope: str) -> list[dict]:
    challenges: list[dict] = []
    if has_any(demand_text, SKIP_VERIFY_TERMS):
        challenges.append(
            challenge(
                statement="跳过测试或未验证就推进需求",
                reason="需求验收不能等同于模型报告；没有验证证据会把缺陷推给测试或生产环境。",
                alternative="先生成验收矩阵，再运行显式验证命令；不能自动验证的部分列入人工验收。",
            )
        )
    if has_any(demand_text, DIRECT_FLOW_TERMS):
        challenges.append(
            challenge(
                statement="直接流转云效状态",
                reason="状态流转会改变团队流程和负责人责任，必须有验证证据和状态机策略。",
                alternative="真实业务任务只写分析评论；状态流转先在专用测试任务使用 dry-run/fake 验证。",
            )
        )
    if has_any(demand_text, DIRECT_CLOSE_TERMS):
        challenges.append(
            challenge(
                statement="自动关闭云效任务或缺陷",
                reason="关闭代表业务验收完成，AI 不能在缺少人工验收和测试证据时替代负责人关单。",
                alternative="最多建议关闭，不默认执行；高风险任务必须人工确认后再处理。",
            )
        )
    if has_any(demand_text, AUTO_DEPLOY_TERMS):
        challenges.append(
            challenge(
                statement="跳过审核直接上线或发布",
                reason="发布属于 CI/CD 和生产风险动作，当前 Harness 未开放真实部署。",
                alternative="先完成 worktree diff、验证报告和人工代码审查，再由既有发布流程处理。",
            )
        )
    if risk_level in {"high", "critical"} and has_any(demand_text, NO_HUMAN_TERMS):
        challenges.append(
            challenge(
                statement="高风险 HIS 需求无需人工确认",
                reason="医保、收费、结算、报表、对账和政策口径不能由模型自行下最终结论。",
                alternative="保留人工确认项、验收清单和对账证据，未确认前阻断自动提交和流转。",
            )
        )
    if yunxiao_transaction_mode == "write" and yunxiao_write_scope != "comment-only":
        challenges.append(
            challenge(
                statement="真实写入范围超过 comment-only",
                reason="v0.8.7 仍不开放真实状态、负责人、迭代、附件或关闭动作。",
                alternative="将真实写入限制为 comment-only，其它动作只生成 dry-run/fake 计划。",
            )
        )
    return challenges


def build_project_profiles(*, project_paths: list[str], evidence_bundle: dict, explicit_verify_commands: list[str]) -> list[dict]:
    paths = list(project_paths)
    evidence_project_path = str(((evidence_bundle.get("project") or {}).get("repo_path") or "")).strip()
    if evidence_project_path:
        paths.insert(0, evidence_project_path)
    profiles: list[dict] = []
    seen_paths: set[str] = set()
    for raw_path in unique_keep_order([path for path in paths if path]):
        path = Path(raw_path).expanduser()
        dedupe_key = str(path.resolve()) if path.exists() else str(path)
        if dedupe_key in seen_paths:
            continue
        seen_paths.add(dedupe_key)
        if not path.exists():
            profiles.append({"path": str(path), "status": "missing", "role": "unknown", "recommended_commands": [], "note": "路径不存在"})
            continue
        if not path.is_dir():
            profiles.append({"path": str(path), "status": "invalid", "role": "unknown", "recommended_commands": [], "note": "路径不是目录"})
            continue
        role, indicators = detect_project_role(path)
        commands = build_project_commands(path=path, role=role)
        if explicit_verify_commands:
            note = "已提供显式验证命令；推荐命令仅作为补充，不会自动执行。"
        else:
            note = "未提供显式验证命令；Harness 会从仓库配置自动发现，并在受控 worktree 中尝试执行。"
        profiles.append(
            {
                "path": str(path),
                "status": "ok",
                "role": role,
                "indicators": indicators,
                "recommended_commands": commands,
                "note": note,
            }
        )
    return profiles


def detect_project_role(path: Path) -> tuple[str, list[str]]:
    indicators: list[str] = []
    frontend = False
    backend = False
    if (path / "package.json").exists():
        frontend = True
        indicators.append("package.json")
    if (path / "src").exists() and any(path.rglob("*.vue")):
        frontend = True
        indicators.append("*.vue")
    if (path / "pom.xml").exists():
        backend = True
        indicators.append("pom.xml")
    if any(path.rglob("*.java")):
        backend = True
        indicators.append("*.java")
    if (path / "build.gradle").exists() or (path / "build.gradle.kts").exists():
        backend = True
        indicators.append("gradle")
    if frontend and backend:
        return "fullstack", unique_keep_order(indicators)
    if frontend:
        return "frontend", unique_keep_order(indicators)
    if backend:
        return "backend", unique_keep_order(indicators)
    return "unknown", unique_keep_order(indicators)


def build_project_commands(*, path: Path, role: str) -> list[str]:
    commands: list[str] = []
    package_json = path / "package.json"
    if package_json.exists():
        scripts = read_package_scripts(package_json)
        package_manager = "yarn" if (path / "yarn.lock").exists() else "npm run"
        if "lint" in scripts:
            commands.append(f"{package_manager} lint" if package_manager == "yarn" else "npm run lint")
        if "test" in scripts:
            commands.append(f"{package_manager} test" if package_manager == "yarn" else "npm test")
        if "build" in scripts:
            commands.append(f"{package_manager} build" if package_manager == "yarn" else "npm run build")
    if (path / "pom.xml").exists():
        commands.append("mvn test")
    if (path / "build.gradle").exists() or (path / "build.gradle.kts").exists():
        commands.append("./gradlew test")
    if role == "unknown":
        commands.append("人工确认项目类型和验证命令")
    return unique_keep_order(commands)


def read_package_scripts(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def classify_command(command: str) -> str:
    lower = command.lower()
    if "lint" in lower or "diff --check" in lower or "py_compile" in lower:
        return "static_check"
    if "build" in lower or "package" in lower:
        return "build_check"
    if "test" in lower or "pytest" in lower or "mvn test" in lower or "gradlew test" in lower:
        return "unit_test"
    if "curl" in lower or "postman" in lower or "newman" in lower:
        return "api_test"
    if "playwright" in lower or "cypress" in lower:
        return "ui_e2e"
    if "人工" in command:
        return "manual_acceptance"
    return "cannot_verify"


def default_verification(item_id: str, item_type: str, message: str) -> dict:
    return {
        "id": item_id,
        "type": item_type,
        "command": "",
        "source": "harness_gap",
        "explicitly_executable": False,
        "execute_policy": message,
        "side_effect_policy": "未执行命令。",
    }


def clean_yunxiao_text(yunxiao_evidence: dict | None) -> str:
    if not yunxiao_evidence:
        return ""
    return str(yunxiao_evidence.get("clean_text") or yunxiao_evidence.get("text_excerpt") or "")


def evidence_source(yunxiao_evidence: dict | None) -> str:
    if not yunxiao_evidence:
        return "手工需求/工程证据"
    work_item_id = yunxiao_evidence.get("work_item_id") or "-"
    if yunxiao_evidence.get("status") in {"success", "partial"}:
        suffix = "（含部分证据缺失警告）" if yunxiao_evidence.get("status") == "partial" else ""
        return f"云效只读证据 {work_item_id}{suffix}"
    return f"云效只读证据读取失败：{yunxiao_evidence.get('error') or work_item_id}"


def acceptance_item(
    *,
    item_id: str,
    category: str,
    scenario: str,
    preconditions: str,
    steps: str,
    expected_result: str,
    evidence: str,
) -> dict:
    return {
        "id": item_id,
        "category": category,
        "scenario": scenario,
        "preconditions": preconditions,
        "steps": steps,
        "expected_result": expected_result,
        "evidence_source": evidence,
    }


def append_acceptance_items(lines: list[str], items: list[dict]) -> None:
    if not items:
        lines.append("- 无。")
        return
    for item in items:
        lines.extend(
            [
                f"- `{item.get('id')}` {item.get('scenario')}",
                f"  - 前置条件：{item.get('preconditions')}",
                f"  - 步骤：{item.get('steps')}",
                f"  - 期望结果：{item.get('expected_result')}",
                f"  - 证据来源：{item.get('evidence_source')}",
            ]
        )


def append_verification_items(lines: list[str], items: list[dict]) -> None:
    if not items:
        lines.append("- 未生成自动验证建议。")
        return
    for item in items:
        command = f"`{item.get('command')}`" if item.get("command") else "-"
        lines.extend(
            [
                f"- `{item.get('id')}` {item.get('type')}：{command}",
                f"  - 来源：{item.get('source')}",
                f"  - 执行策略：{item.get('execute_policy')}",
            ]
        )


def unique_items_by_id(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        item_id = str(item.get("id") or "")
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def unique_command_tuples(commands: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    seen: set[str] = set()
    result: list[tuple[str, str, bool]] = []
    for command, source, explicit in commands:
        normalized = command.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append((normalized, source, explicit))
    return result


def has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def challenge(*, statement: str, reason: str, alternative: str) -> dict:
    return {
        "statement": statement,
        "reason": reason,
        "alternative": alternative,
        "format": "不建议这样做 / 原因 / 替代方案",
    }


def compact_text(text: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
