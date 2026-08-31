from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from app.worktree_executor import WorktreeExecutionResult


VERSION = "0.9.5"


@dataclass
class SingleDemandTrialPackage:
    run_id: int
    status: str
    summary: str
    decision: dict = field(default_factory=dict)
    projects: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    verify_commands: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    verification_matrix: list[dict] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    worktree: dict = field(default_factory=dict)
    technical_decision: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def trial_markdown(self) -> str:
        lines = [
            "## v0.9.5 单需求真实开发试跑",
            "",
            f"- 状态：{self.status}",
            f"- 结论：{self.summary}",
            f"- 是否可进入人工代码审查：{'是' if self.decision.get('can_enter_code_review') else '否'}",
            f"- 是否允许自动提交：{'是' if self.decision.get('auto_commit_allowed') else '否'}",
            f"- 是否允许真实云效流转：{'是' if self.decision.get('yunxiao_real_transition_allowed') else '否'}",
            f"- 是否允许真实云效评论：{'是' if self.decision.get('yunxiao_comment_allowed') else '否'}",
            f"- 项目：{', '.join(f'`{item}`' for item in self.projects) if self.projects else '-'}",
            f"- 允许修改路径：{', '.join(f'`{item}`' for item in self.allowed_paths) if self.allowed_paths else '-'}",
            f"- 实际变更路径：{', '.join(f'`{item}`' for item in self.changed_paths) if self.changed_paths else '-'}",
            "",
            "### 阻断项",
            "",
        ]
        if self.blockers:
            lines.extend(f"- {item}" for item in self.blockers)
        else:
            lines.append("- 无。")
        lines.extend(["", "### 边界", ""])
        lines.extend(f"- {item}" for item in self.boundaries)
        return "\n".join(lines)

    def verification_matrix_markdown(self) -> str:
        lines = [
            "## v0.9.5 验证矩阵",
            "",
            "| 类型 | 对象 | 状态 | 证据 | 是否阻断 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in self.verification_matrix:
            lines.append(
                "| {kind} | {target} | {status} | {evidence} | {blocking} |".format(
                    kind=escape_cell(item.get("kind") or "-"),
                    target=escape_cell(item.get("target") or "-"),
                    status=escape_cell(item.get("status") or "-"),
                    evidence=escape_cell(item.get("evidence") or "-"),
                    blocking="是" if item.get("blocking") else "否",
                )
            )
        if not self.verification_matrix:
            lines.append("| - | - | not_run | 未形成验证项 | 是 |")
        return "\n".join(lines)

    def code_review_markdown(self) -> str:
        lines = [
            "## v0.9.5 代码审查包",
            "",
            f"- 状态：{self.status}",
            f"- 审查结论：{self.summary}",
            f"- 修改项目：{', '.join(f'`{item}`' for item in self.projects) if self.projects else '-'}",
            f"- 修改文件：{', '.join(f'`{item}`' for item in self.changed_paths or self.allowed_paths) if (self.changed_paths or self.allowed_paths) else '-'}",
            "- 未执行动作：不自动 commit、不 push、不发布、不真实流转云效状态、不改负责人、不调迭代、不关闭任务。",
            "",
            "### 审查重点",
            "",
            "- Diff 是否只围绕当前云效需求，不包含顺手格式化或历史代码清理。",
            "- 目标文件是否由工程证据和现有代码上下文支持，未把相似页面误当目标页面。",
            "- 前后端边界是否有证据；字段来源不明确时不得只改前端展示。",
            "- 验证命令是否通过，且未产生格式化、构建产物或其他副作用。",
            "- 人工验收项是否覆盖真实业务操作路径和空数据/异常边界。",
        ]
        worktree_summary = self.worktree.get("summary") if self.worktree else ""
        if worktree_summary:
            lines.extend(["", "### Worktree 结果", "", f"- {worktree_summary}"])
        return "\n".join(lines)

    def commit_ready_markdown(self) -> str:
        manual_commit_allowed = bool(self.decision.get("manual_commit_allowed"))
        lines = [
            "## v0.9.5 提交准备摘要",
            "",
            f"- 是否建议人工提交：{'是' if manual_commit_allowed else '否'}",
            "- 是否已自动提交：否",
            "- 是否已推送：否",
            "- 是否已发布：否",
            "- 是否已真实流转云效状态：否",
            f"- 建议提交前动作：{self.decision.get('next_action') or '-'}",
            "",
        ]
        if self.changed_paths:
            lines.extend(["### 建议提交范围", ""])
            lines.extend(f"- `{path}`" for path in self.changed_paths)
        if self.blockers:
            lines.extend(["", "### 不建议提交原因", ""])
            lines.extend(f"- {item}" for item in self.blockers)
        return "\n".join(lines)


def build_single_demand_trial_package(
    *,
    run_id: int,
    technical_decision: dict,
    acceptance_matrix: dict,
    project_paths: list[str],
    allowed_paths: list[str],
    verify_commands: list[str],
    worktree_result: WorktreeExecutionResult | None,
    transaction_mode: str,
    write_scope: str,
) -> SingleDemandTrialPackage:
    blockers: list[str] = []
    changed_paths = extract_changed_paths(worktree_result) if worktree_result else []
    tech_decision = technical_decision.get("implementation_decision") or {}
    tech_blockers = [str(item) for item in tech_decision.get("blockers") or [] if str(item).strip()]
    blockers.extend(tech_blockers)

    if not project_paths:
        blockers.append("未选择到业务项目，不能进行真实开发试跑。")
    if not allowed_paths:
        blockers.append("未形成允许修改路径，不能进入受控 patch。")
    if worktree_result is None:
        blockers.append("未运行 worktree 改码；本轮只完成分析和计划。")
    elif worktree_result.status != "success":
        blockers.append(worktree_result.summary or "worktree 改码或验证失败。")

    verification_matrix = build_verification_matrix(
        acceptance_matrix=acceptance_matrix,
        verify_commands=verify_commands,
        worktree_result=worktree_result,
    )
    blocking_verification = [item for item in verification_matrix if item.get("blocking")]
    if blocking_verification:
        blockers.append("存在阻断性验证项，不能声明完成。")

    worktree_success = bool(worktree_result and worktree_result.status == "success")
    manual_commit_allowed = worktree_success and not blocking_verification
    status = "success" if manual_commit_allowed else "blocked"
    summary = (
        "单需求试跑完成：已通过受控 worktree 改码、合入本地原目录并生成审查包；仍需人工提交和业务验收。"
        if manual_commit_allowed
        else "单需求试跑未达到提交准备状态；请先处理阻断项。"
    )
    decision = {
        "can_enter_code_review": worktree_success and not blocking_verification,
        "manual_commit_allowed": manual_commit_allowed,
        "auto_commit_allowed": False,
        "can_enter_test": manual_commit_allowed and bool(verify_commands),
        "yunxiao_comment_allowed": transaction_mode in {"dry-run", "write"} and write_scope == "comment-only",
        "yunxiao_real_transition_allowed": False,
        "next_action": "人工审查 diff，确认后人工提交；云效可只写交付评论，不真实流转状态。"
        if manual_commit_allowed
        else "补充项目/字段/目标页面证据，或修复 worktree/验证失败后重跑。",
    }

    boundaries = [
        "本模式每次只处理一个真实云效需求。",
        "AI 只在临时 worktree 试错，成功后才把 final.diff 合入本地原业务目录。",
        "不自动 commit、不 push、不发布。",
        "真实云效只允许 comment-only；transition、assign、iteration、close 继续禁止真实执行。",
        "状态建议只能进入 dry-run/fake 报告，不能替代人工流程规则。",
    ]

    return SingleDemandTrialPackage(
        run_id=run_id,
        status=status,
        summary=summary,
        decision=decision,
        projects=project_paths,
        allowed_paths=allowed_paths,
        verify_commands=verify_commands,
        changed_paths=changed_paths,
        verification_matrix=verification_matrix,
        blockers=unique_keep_order(blockers),
        boundaries=boundaries,
        worktree=worktree_result.to_dict() if worktree_result else {},
        technical_decision=technical_decision,
    )


def build_verification_matrix(
    *,
    acceptance_matrix: dict,
    verify_commands: list[str],
    worktree_result: WorktreeExecutionResult | None,
) -> list[dict]:
    items: list[dict] = []
    if worktree_result is None:
        items.append(
            {
                "kind": "受控改码",
                "target": "worktree",
                "status": "not_run",
                "evidence": "未运行 worktree",
                "blocking": True,
            }
        )
    else:
        items.append(
            {
                "kind": "受控改码",
                "target": "worktree",
                "status": worktree_result.status,
                "evidence": worktree_result.summary,
                "blocking": worktree_result.status != "success",
            }
        )
        success_attempt = first_success_attempt(worktree_result)
        if success_attempt:
            diff_check = success_attempt.get("diff_check") or {}
            items.append(
                {
                    "kind": "静态差异检查",
                    "target": "git diff --check",
                    "status": "pass" if diff_check.get("returncode") == 0 else "failed",
                    "evidence": summarize_command_result(diff_check),
                    "blocking": diff_check.get("returncode") != 0,
                }
            )
            for verify in success_attempt.get("verify") or []:
                side_effects = verify.get("side_effects") or {}
                blocking = verify.get("returncode") != 0 or bool(side_effects.get("changed"))
                status = "pass" if not blocking else ("side_effect_failed" if side_effects.get("changed") else "failed")
                items.append(
                    {
                        "kind": "专项验证命令",
                        "target": verify.get("command") or "-",
                        "status": status,
                        "evidence": summarize_command_result(verify),
                        "blocking": blocking,
                    }
                )
    if not verify_commands:
        items.append(
            {
                "kind": "专项自动验证",
                "target": "verify_commands",
                "status": "manual_required",
                "evidence": "未传入或未推导出可稳定运行的专项验证命令。",
                "blocking": False,
            }
        )

    for manual_item in extract_manual_acceptance_items(acceptance_matrix):
        items.append(
            {
                "kind": "人工业务验收",
                "target": manual_item,
                "status": "manual_required",
                "evidence": "来自需求验收矩阵，不能由 Harness 自动证明。",
                "blocking": False,
            }
        )
    return items


def extract_changed_paths(worktree_result: WorktreeExecutionResult | None) -> list[str]:
    if worktree_result is None:
        return []
    apply_result = worktree_result.apply_to_project or {}
    paths = [str(item) for item in apply_result.get("changed_paths") or [] if str(item).strip()]
    if paths:
        return unique_keep_order(paths)
    success_attempt = first_success_attempt(worktree_result)
    if success_attempt:
        return unique_keep_order(str(item) for item in success_attempt.get("changed_paths") or [] if str(item).strip())
    return unique_keep_order(str(item) for item in worktree_result.allowed_paths or [] if str(item).strip())


def first_success_attempt(worktree_result: WorktreeExecutionResult | None) -> dict:
    if worktree_result is None:
        return {}
    for attempt in worktree_result.attempts:
        if attempt.get("status") == "success":
            return attempt
    return {}


def extract_manual_acceptance_items(acceptance_matrix: dict) -> list[str]:
    result: list[str] = []
    if not isinstance(acceptance_matrix, dict):
        return result
    for key in ["manual_acceptance", "manual_checks", "human_acceptance", "blocking_items"]:
        value = acceptance_matrix.get(key)
        if isinstance(value, list):
            for item in value:
                text = item_to_text(item)
                if text:
                    result.append(text)
    return unique_keep_order(result)[:12]


def item_to_text(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ["scenario", "title", "description", "expected_result", "item"]:
            value = str(item.get(key) or "").strip()
            if value:
                return value
    return ""


def summarize_command_result(result: dict) -> str:
    if not result:
        return "-"
    command = str(result.get("command") or "").strip()
    returncode = result.get("returncode")
    stderr = str(result.get("stderr") or "").strip().replace("\n", " ")
    stdout = str(result.get("stdout") or "").strip().replace("\n", " ")
    detail = stderr or stdout
    if len(detail) > 180:
        detail = detail[:177] + "..."
    prefix = f"returncode={returncode}"
    if command:
        prefix = f"{command}；{prefix}"
    return f"{prefix}；{detail}" if detail else prefix


def escape_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def unique_keep_order(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
