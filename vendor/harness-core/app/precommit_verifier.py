from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.behavior_acceptance import (
    behavior_to_json,
    behavior_to_markdown,
    build_behavior_acceptance,
)
from app.interaction_evidence import (
    behavior_test_plan_to_markdown,
    interaction_evidence_to_json,
    interaction_evidence_to_markdown,
    build_interaction_evidence_package,
    method_regression_result_to_markdown,
    playwright_screenshot_index_to_markdown,
    ui_evidence_manifest_to_markdown,
)
from app.method_test_runner import (
    METHOD_TEST_RUNNER_VERSION,
    method_test_runner_to_json,
    method_test_runner_to_markdown,
    run_method_test_commands,
)
from app.ui_evidence_runner import (
    UI_EVIDENCE_RUNNER_VERSION,
    run_ui_evidence_commands,
    ui_evidence_runner_to_json,
    ui_evidence_runner_to_markdown,
)
from app.fullstack_executor import (
    FullstackPatchTarget,
    create_fullstack_worktree,
    parse_status_paths,
    safe_worktree_suffix,
)
from app.worktree_executor import (
    DEFAULT_WORKTREE_ROOT,
    PATCH_TIMEOUT_SECONDS,
    VERIFY_TIMEOUT_SECONDS,
    build_side_effect_report,
    capture_worktree_snapshot,
    cleanup_git_worktree,
    normalize_relative_path,
    prepare_dependency_links,
    run_command,
    run_shell_command,
    truncate_text,
    unique_keep_order,
    validate_patch,
    validate_relative_path,
)


@dataclass
class PrecommitVerificationOptions:
    run_id: int
    project_root: str
    project_path: str = ""
    allowed_paths: list[str] = field(default_factory=list)
    verify_commands: list[str] = field(default_factory=list)
    target_key: str = ""
    target_name: str = ""
    target_role: str = "frontend"
    title: str = ""
    entity_id: str = ""
    demand_text: str = ""
    method_evidence: dict = field(default_factory=dict)
    method_test_commands: list[str] = field(default_factory=list)
    ui_evidence_paths: list[str] = field(default_factory=list)
    ui_capture_commands: list[str] = field(default_factory=list)
    worktree_root: str = DEFAULT_WORKTREE_ROOT
    cleanup_worktree: bool = True
    verify_command_overrides: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class PrecommitVerificationResult:
    status: str
    summary: str
    targets: list[dict] = field(default_factory=list)
    verification_matrix: dict = field(default_factory=dict)
    behavior_acceptance: dict = field(default_factory=dict)
    method_test_runner: dict = field(default_factory=dict)
    ui_evidence_runner: dict = field(default_factory=dict)
    interaction_evidence: dict = field(default_factory=dict)
    cleanup: dict[str, dict] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def matrix_json(self) -> str:
        return json.dumps(self.verification_matrix, ensure_ascii=False, indent=2)

    def matrix_markdown(self) -> str:
        matrix = self.verification_matrix or {}
        lines = [
            "## v0.9.1 提交前验证矩阵",
            "",
            f"- 总状态：{matrix.get('overall_status') or self.status}",
            f"- 是否可提交：{matrix.get('can_commit')}",
            f"- 是否可进入测试：{matrix.get('can_enter_test')}",
            f"- 是否允许云效交付评论：{matrix.get('can_yunxiao_comment')}",
            f"- 是否允许云效真实流转：{matrix.get('can_yunxiao_transition')}",
            f"- 结论：{matrix.get('summary') or self.summary}",
            "",
            "| 验证项 | 类型 | 状态 | 项目 | 证据 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in matrix.get("items") or []:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item.get("name") or item.get("id") or "-"),
                        str(item.get("type") or "-"),
                        str(item.get("status") or "-"),
                        str(item.get("project") or "-"),
                        str(item.get("evidence") or "-").replace("\n", "<br>"),
                    ]
                )
                + " |"
            )
        manual_items = matrix.get("manual_acceptance") or []
        if manual_items:
            lines.extend(["", "### 人工验收项", ""])
            for item in manual_items:
                lines.append(f"- {item}")
        warnings = matrix.get("warnings") or []
        if warnings:
            lines.extend(["", "### 告警/限制", ""])
            for item in warnings:
                lines.append(f"- {item}")
        blockers = matrix.get("blockers") or []
        if blockers:
            lines.extend(["", "### 阻断项", ""])
            for item in blockers:
                lines.append(f"- {item}")
        return "\n".join(lines)

    def behavior_json(self) -> str:
        return behavior_to_json(self.behavior_acceptance or {})

    def behavior_markdown(self) -> str:
        return behavior_to_markdown(self.behavior_acceptance or {})

    def method_test_runner_json(self) -> str:
        return method_test_runner_to_json(self.method_test_runner or {})

    def method_test_runner_markdown(self) -> str:
        return method_test_runner_to_markdown(self.method_test_runner or {})

    def ui_evidence_runner_json(self) -> str:
        return ui_evidence_runner_to_json(self.ui_evidence_runner or {})

    def ui_evidence_runner_markdown(self) -> str:
        return ui_evidence_runner_to_markdown(self.ui_evidence_runner or {})

    def interaction_json(self) -> str:
        return interaction_evidence_to_json(self.interaction_evidence or {})

    def interaction_markdown(self) -> str:
        return interaction_evidence_to_markdown(self.interaction_evidence or {})

    def behavior_test_plan_json(self) -> str:
        return json.dumps((self.interaction_evidence or {}).get("behavior_test_plan") or {}, ensure_ascii=False, indent=2)

    def behavior_test_plan_markdown(self) -> str:
        return behavior_test_plan_to_markdown((self.interaction_evidence or {}).get("behavior_test_plan") or {})

    def method_regression_json(self) -> str:
        return json.dumps((self.interaction_evidence or {}).get("method_regression_result") or {}, ensure_ascii=False, indent=2)

    def method_regression_markdown(self) -> str:
        return method_regression_result_to_markdown((self.interaction_evidence or {}).get("method_regression_result") or {})

    def ui_evidence_json(self) -> str:
        return json.dumps((self.interaction_evidence or {}).get("ui_evidence_manifest") or {}, ensure_ascii=False, indent=2)

    def ui_evidence_markdown(self) -> str:
        return ui_evidence_manifest_to_markdown((self.interaction_evidence or {}).get("ui_evidence_manifest") or {})

    def playwright_screenshot_index_markdown(self) -> str:
        return playwright_screenshot_index_to_markdown((self.interaction_evidence or {}).get("ui_evidence_manifest") or {})

    def code_review_markdown(self) -> str:
        if self.manifest.get("generic_precommit"):
            return self.generic_code_review_markdown()
        lines = [
            "## v0.9.1 代码审查包",
            "",
            "- 技术决策：后端字段已在实际 REST 返回中验证存在，本次只补前端结算收款页面展示列。",
            "- 字段链路：`df-his-api` `DTO_ZY_YuJiaoKuan.beiZhu` / REST `getAllByBingRenZyId` 响应 -> 前端预交金表格“备注”列。",
            "- 未改业务逻辑：未新增结算规则、未改查询条件、未改接口调用、未改收费流程、未改住院服务源码。",
            "- 云效边界：未真实流转状态、未改负责人、未关闭任务。",
            "",
            "| 项目 | 角色 | 文件 | 验证状态 |",
            "| --- | --- | --- | --- |",
        ]
        for target in self.targets:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(target.get("name") or target.get("key") or "-"),
                        str(target.get("role") or "-"),
                        "<br>".join(target.get("changed_paths") or target.get("allowed_paths") or []) or "-",
                        str(target.get("status") or "-"),
                    ]
                )
                + " |"
            )
        unrelated = []
        for target in self.targets:
            for path in target.get("unexpected_paths") or []:
                unrelated.append(f"{target.get('name') or target.get('key')}: `{path}`")
        if unrelated:
            lines.extend(["", "### 白名单外改动", ""])
            lines.extend(f"- {item}" for item in unrelated)
            lines.append("")
            lines.append("这些文件未纳入本需求验证范围，因此即使目标文件验证通过，也不能直接判定当前仓库可提交。")
        for target in self.targets:
            diff = str(target.get("current_diff") or "")
            if not diff.strip():
                continue
            lines.extend(
                [
                    "",
                    f"### {target.get('name') or target.get('key')} diff 摘要",
                    "",
                    "```diff",
                    truncate_text(diff, 5000),
                    "```",
                ]
            )
        return "\n".join(lines)

    def commit_ready_markdown(self) -> str:
        if self.manifest.get("generic_precommit"):
            return self.generic_commit_ready_markdown()
        matrix = self.verification_matrix or {}
        can_commit = bool(matrix.get("can_commit"))
        lines = [
            "## v0.9.1 Commit Ready Summary",
            "",
            f"- 结论：{'可以进入人工代码审查后提交' if can_commit else '不可提交，需先处理验证失败'}",
            f"- 是否可进入测试：{matrix.get('can_enter_test')}",
            "- 自动提交：未执行",
            "- 自动推送：未执行",
            "- 自动发布：未执行",
            "- 云效真实流转：未执行",
            "",
            "### 建议提交说明",
            "",
            "```text",
            "DFHIS-31270 住院收费结算收款页展示预交金备注",
            "```",
            "",
            "### 建议人工复核",
            "",
            "- 复核前端 diff 是否只包含本需求展示列改动。",
            "- 复核后端字段来源：实际 REST 响应已包含 `beiZhu`，本次不需要 BFF GraphQL 或后端服务改动。",
            "- 复核住院收费结算收款页面中预缴信息表格的备注展示位置和宽度；该表已按现有 `df-dx-table` 方式接入 `grid-view-name` 列配置。",
            "- 复核无备注数据的空态展示是否符合现场预期。",
        ]
        blockers = matrix.get("blockers") or []
        if blockers:
            lines.extend(["", "### 当前阻断", ""])
            for blocker in blockers:
                lines.append(f"- {blocker}")
        warnings = matrix.get("warnings") or []
        if warnings:
            lines.extend(["", "### 当前限制/告警", ""])
            for warning in warnings:
                lines.append(f"- {warning}")
        return "\n".join(lines)

    def generic_code_review_markdown(self) -> str:
        title = self.manifest.get("title") or self.manifest.get("entity_id") or "当前需求"
        lines = [
            "## 通用提交前代码审查包",
            "",
            f"- 需求：{title}",
            f"- 状态：{self.status}",
            f"- 审查结论：{self.summary}",
            "- 技术决策：基于当前本地 diff 做提交前审查；不重新生成 patch，不修改业务代码。",
            "- 云效边界：未真实流转状态、未改负责人、未调整迭代、未关闭任务。",
            "",
            "| 项目 | 角色 | 文件 | 验证状态 |",
            "| --- | --- | --- | --- |",
        ]
        for target in self.targets:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(target.get("name") or target.get("key") or "-"),
                        str(target.get("role") or "-"),
                        "<br>".join(target.get("changed_paths") or target.get("allowed_paths") or []) or "-",
                        str(target.get("status") or "-"),
                    ]
                )
                + " |"
            )
        for target in self.targets:
            diff = str(target.get("current_diff") or "")
            if not diff.strip():
                continue
            lines.extend(
                [
                    "",
                    f"### {target.get('name') or target.get('key')} diff 摘要",
                    "",
                    "```diff",
                    truncate_text(diff, 5000),
                    "```",
                ]
            )
        return "\n".join(lines)

    def generic_commit_ready_markdown(self) -> str:
        matrix = self.verification_matrix or {}
        can_commit = bool(matrix.get("can_commit"))
        blockers = matrix.get("blockers") or []
        warnings = matrix.get("warnings") or []
        if can_commit:
            conclusion = "可以进入人工代码审查后提交"
        elif blockers:
            conclusion = "不可提交，需先处理阻断项"
        elif warnings:
            conclusion = "不可提交，需先隔离或处理当前限制/告警"
        else:
            conclusion = "不可提交，需先确认提交条件"
        entity_id = self.manifest.get("entity_id") or "当前需求"
        title = str(self.manifest.get("title") or "").strip()
        commit_subject = title if title.startswith(str(entity_id)) else f"{entity_id} {title or '提交本地修复'}"
        lines = [
            "## 通用 Commit Ready Summary",
            "",
            f"- 需求：{entity_id}",
            f"- 结论：{conclusion}",
            f"- 是否可进入测试：{matrix.get('can_enter_test')}",
            "- 自动提交：未执行",
            "- 自动推送：未执行",
            "- 自动发布：未执行",
            "- 云效真实流转：未执行",
            "",
            "### 建议提交说明",
            "",
            "```text",
            commit_subject,
            "```",
            "",
            "### 建议人工复核",
            "",
            "- 复核 diff 是否只包含本需求相关改动。",
            "- 复核目标文件是否与云效需求、截图和代码调用路径一致。",
            "- 复核验证命令是否通过，且未产生格式化、构建产物或其他副作用。",
            "- 按验证矩阵执行人工业务验收后再决定是否提交和云效评论。",
        ]
        if blockers:
            lines.extend(["", "### 当前阻断", ""])
            for blocker in blockers:
                lines.append(f"- {blocker}")
        if warnings:
            lines.extend(["", "### 当前限制/告警", ""])
            for warning in warnings:
                lines.append(f"- {warning}")
        return "\n".join(lines)


class PrecommitVerifier:
    def execute(self, options: PrecommitVerificationOptions) -> PrecommitVerificationResult:
        started_at = time.time()
        worktree_root = Path(options.worktree_root).expanduser().resolve()
        targets = build_precommit_targets(options)
        target_records = [target_to_record(target) for target in targets]
        cleanup_results: dict[str, dict] = {}
        created: list[tuple[FullstackPatchTarget, Path]] = []
        manifest = {
            "run_id": options.run_id,
            "project_root": str(Path(options.project_root).expanduser().resolve()),
            "project_path": str(Path(options.project_path).expanduser().resolve()) if options.project_path else "",
            "worktree_root": str(worktree_root),
            "title": options.title,
            "entity_id": options.entity_id,
            "demand_text": options.demand_text,
            "method_evidence_provided": bool(options.method_evidence),
            "ui_evidence_paths": options.ui_evidence_paths,
            "generic_precommit": bool(options.project_path and options.allowed_paths),
            "started_at_epoch": started_at,
            "targets": target_records,
        }

        preflight_error = preflight_current_diffs(targets=targets, records=target_records)
        if preflight_error:
            return build_result(
                status="failed",
                summary=preflight_error,
                targets=target_records,
                cleanup=cleanup_results,
                manifest=manifest,
            )

        try:
            for target in targets:
                project_path = Path(target.project_path).expanduser().resolve()
                worktree_path = worktree_root / f"precommit_{options.run_id}_{safe_worktree_suffix(target.key)}"
                record = record_for_key(target_records, target.key)
                record["worktree_path"] = str(worktree_path)
                setup_error = create_fullstack_worktree(project_path=project_path, worktree_root=worktree_root, worktree_path=worktree_path)
                if setup_error:
                    record["status"] = "setup_failed"
                    record["message"] = setup_error
                    return build_result(
                        status="failed",
                        summary=f"创建提交前验证 worktree 失败：{setup_error}",
                        targets=target_records,
                        cleanup=cleanup_results,
                        manifest=manifest,
                    )
                created.append((target, worktree_path))
                record["dependency_links"] = prepare_dependency_links(project_path=project_path, worktree_path=worktree_path)

            for target, worktree_path in created:
                record = record_for_key(target_records, target.key)
                result = verify_target_current_diff(target=target, worktree_path=worktree_path)
                record.update(result)
                if record.get("preflight_blockers") and record.get("status") == "success":
                    record["target_verification_status"] = "success"
                    record["scope_warning"] = "目标文件验证通过；同仓库存在白名单外改动，提交前需隔离本需求改动。"
                    record["message"] = record["scope_warning"]
            failed = [target for target in target_records if target.get("status") != "success"]
            status = "failed" if failed else "success"
            behavior_acceptance = build_precommit_behavior_acceptance(options=options, records=target_records)
            manifest["behavior_acceptance"] = behavior_acceptance
            seed_interaction_evidence = build_precommit_interaction_evidence(
                options=options,
                records=target_records,
                behavior_acceptance=behavior_acceptance,
                method_evidence=options.method_evidence,
                ui_evidence_paths=options.ui_evidence_paths,
            )
            method_test_runner = build_precommit_method_test_runner(
                options=options,
                records=target_records,
                behavior_test_plan=seed_interaction_evidence.get("behavior_test_plan") or {},
            )
            if method_test_runner:
                manifest["method_test_runner"] = method_test_runner
            method_evidence = options.method_evidence or method_test_runner
            ui_evidence_runner = build_precommit_ui_evidence_runner(
                options=options,
                records=target_records,
                worktree_root=worktree_root,
            )
            if ui_evidence_runner:
                manifest["ui_evidence_runner"] = ui_evidence_runner
            ui_evidence_paths = unique_keep_order((options.ui_evidence_paths or []) + ((ui_evidence_runner.get("artifact_paths") or []) if ui_evidence_runner else []))
            interaction_evidence = build_precommit_interaction_evidence(
                options=options,
                records=target_records,
                behavior_acceptance=behavior_acceptance,
                method_evidence=method_evidence,
                ui_evidence_paths=ui_evidence_paths,
            )
            manifest["interaction_evidence"] = interaction_evidence
            warning_count = sum(len(item.get("warnings") or []) for item in target_records)
            scope_warning_count = sum(len(item.get("preflight_blockers") or []) for item in target_records)
            generic_precommit = bool(options.project_path and options.allowed_paths)
            if failed:
                summary = "提交前验证失败：" + "；".join(
                    f"{item.get('name') or item.get('key')}：{item.get('message') or item.get('status')}" for item in failed
                )
            elif behavior_acceptance.get("status") in {"failed", "needs_review"}:
                if interaction_evidence.get("status") == "pass":
                    summary = "提交前验证通过：行为门禁需复核项已由方法级交互测试证据覆盖；未提交、未推送、未发布。"
                else:
                    status = "failed"
                    summary = f"行为验收未通过：{behavior_acceptance.get('summary')}"
            elif method_test_runner and method_test_runner.get("status") in {"failed", "needs_evidence"}:
                status = "failed"
                summary = f"方法级测试命令未通过：{method_test_runner.get('summary')}"
            elif ui_evidence_runner and ui_evidence_runner.get("status") in {"failed", "needs_evidence"}:
                status = "failed"
                summary = f"UI 证据采集命令未通过：{ui_evidence_runner.get('summary')}"
            elif interaction_evidence.get("status") in {"failed", "needs_evidence"}:
                status = "failed"
                summary = f"交互证据未通过：{interaction_evidence.get('summary')}"
            elif scope_warning_count and generic_precommit:
                summary = "提交前验证通过：目标 diff 检查和验证命令均通过；但同仓库存在白名单外改动，不能直接整体提交或写云效交付评论。"
            elif warning_count and generic_precommit:
                summary = "提交前验证通过：目标 diff 检查通过；验证命令命中历史基线问题，未判定为本次改动引入；未提交、未推送、未发布。"
            elif warning_count:
                summary = "提交前验证通过：前端 diff 检查通过；单文件 lint 命中历史基线问题，未判定为本次改动引入；后端字段来源由实际 REST 响应/df-his-api 证据证明；未提交、未推送、未发布。"
            elif generic_precommit:
                summary = "提交前验证通过：目标 diff 检查和验证命令均通过；未提交、未推送、未发布。"
            else:
                summary = "提交前验证通过：前端 diff 检查和单文件 lint 均通过；后端字段来源由实际 REST 响应/df-his-api 证据证明；未提交、未推送、未发布。"
            return build_result(
                status=status,
                summary=summary,
                targets=target_records,
                cleanup=cleanup_results,
                manifest=manifest,
                behavior_acceptance=behavior_acceptance,
                method_test_runner=method_test_runner,
                ui_evidence_runner=ui_evidence_runner,
                interaction_evidence=interaction_evidence,
            )
        finally:
            if options.cleanup_worktree:
                for target, worktree_path in created:
                    cleanup_results[target.key] = cleanup_git_worktree(
                        project_path=Path(target.project_path).expanduser().resolve(),
                        worktree_path=worktree_path,
                    )
                manifest["cleanup"] = cleanup_results


def build_precommit_targets(options: PrecommitVerificationOptions) -> list[FullstackPatchTarget]:
    if options.project_path or options.allowed_paths:
        return build_generic_precommit_targets(options)
    return build_dfhis_31270_precommit_targets(options)


def build_generic_precommit_targets(options: PrecommitVerificationOptions) -> list[FullstackPatchTarget]:
    if not options.project_path:
        raise ValueError("通用 precommit-verify 必须提供 project_path。")
    if not options.allowed_paths:
        raise ValueError("通用 precommit-verify 必须提供 allowed_paths。")
    project_path = Path(options.project_path).expanduser().resolve()
    key = options.target_key or project_path.name
    return [
        FullstackPatchTarget(
            key=key,
            name=options.target_name or project_path.name,
            role=options.target_role or "frontend",
            project_path=str(project_path),
            patch_kind="generic_precommit_verify",
            allowed_paths=options.allowed_paths,
            verify_commands=options.verify_commands,
        )
    ]


def build_dfhis_31270_precommit_targets(options: PrecommitVerificationOptions) -> list[FullstackPatchTarget]:
    root = Path(options.project_root).expanduser().resolve()
    defaults = {
        "df-web-zhuyuansf": [
            "./node_modules/.bin/vue-cli-service lint --no-fix src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue"
        ],
    }
    overrides = options.verify_command_overrides or {}
    return [
        FullstackPatchTarget(
            key="df-web-zhuyuansf",
            name="df-web-zhuyuansf",
            role="frontend-view",
            project_path=str(root / "df-web-zhuyuansf"),
            patch_kind="precommit_frontend_lint",
            allowed_paths=["src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue"],
            verify_commands=overrides.get("df-web-zhuyuansf") or defaults["df-web-zhuyuansf"],
        ),
    ]


def read_allowed_current_diff(*, project_path: Path, allowed_paths: list[str], status_text: str = "") -> dict:
    status_stdout = status_text
    status_result: dict = {}
    if not status_stdout:
        status_result = run_command(["git", "status", "--porcelain", "--untracked-files=all"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if status_result["returncode"] != 0:
            return {
                "returncode": status_result["returncode"],
                "stdout": "",
                "stderr": status_result.get("stderr") or status_result.get("stdout") or "",
                "message": "无法读取 git status。",
                "command": status_result.get("command") or "git status --porcelain --untracked-files=all",
            }
        status_stdout = status_result.get("stdout", "")

    untracked_paths = [path for path in parse_untracked_status_paths(status_stdout) if path in set(allowed_paths)]
    tracked_diff = run_command(
        ["git", "diff", "--no-ext-diff", "--", *allowed_paths],
        cwd=project_path,
        timeout=PATCH_TIMEOUT_SECONDS,
        truncate_output=False,
    )
    if tracked_diff["returncode"] != 0:
        return tracked_diff

    chunks = [tracked_diff.get("stdout", "")]
    addition_results = []
    for path in untracked_paths:
        addition_diff = run_command(
            ["git", "diff", "--no-ext-diff", "--no-index", "--", "/dev/null", path],
            cwd=project_path,
            timeout=PATCH_TIMEOUT_SECONDS,
            truncate_output=False,
        )
        addition_results.append(addition_diff)
        if addition_diff["returncode"] not in {0, 1}:
            return {
                **addition_diff,
                "message": f"读取未跟踪文件 diff 失败：{path}",
                "untracked_paths": untracked_paths,
                "file_addition_diffs": addition_results,
            }
        chunks.append(addition_diff.get("stdout", ""))

    combined = "\n".join(chunk.rstrip("\n") for chunk in chunks if chunk.strip())
    if combined:
        combined += "\n"
    return {
        **tracked_diff,
        "returncode": 0,
        "stdout": combined,
        "status": status_result,
        "untracked_paths": untracked_paths,
        "file_addition_diffs": addition_results,
    }


def parse_untracked_status_paths(status_text: str) -> list[str]:
    paths: list[str] = []
    for raw_line in status_text.splitlines():
        line = raw_line.rstrip()
        if not line.startswith("?? "):
            continue
        paths.append(normalize_relative_path(line[3:]))
    return unique_keep_order(path for path in paths if path)


def preflight_current_diffs(*, targets: list[FullstackPatchTarget], records: list[dict]) -> str:
    for target in targets:
        project_path = Path(target.project_path).expanduser().resolve()
        record = record_for_key(records, target.key)
        if not project_path.is_dir():
            message = f"{target.name} 项目路径不存在：{project_path}"
            record.update({"status": "preflight_failed", "message": message})
            return message
        root_result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if root_result["returncode"] != 0:
            message = f"{target.name} 不是 Git 仓库。"
            record.update({"status": "preflight_failed", "message": message})
            return message
        git_root = Path(root_result["stdout"].strip()).resolve()
        if git_root != project_path:
            message = f"{target.name} project_path 必须指向 Git 根目录：当前根目录 {git_root}"
            record.update({"status": "preflight_failed", "message": message})
            return message
        for allowed_path in target.allowed_paths:
            safety = validate_relative_path(allowed_path)
            if safety:
                message = f"{target.name} 白名单路径不安全：{safety}"
                record.update({"status": "preflight_failed", "message": message})
                return message
            if not (project_path / allowed_path).is_file():
                message = f"{target.name} 白名单文件不存在：{allowed_path}"
                record.update({"status": "preflight_failed", "message": message})
                return message
        status = run_command(["git", "status", "--porcelain", "--untracked-files=all"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        record["current_status"] = status
        if status["returncode"] != 0:
            message = f"无法读取 {target.name} git status。"
            record.update({"status": "preflight_failed", "message": message})
            return message
        changed_paths = parse_status_paths(status.get("stdout", ""))
        record["changed_paths"] = changed_paths
        unexpected = [path for path in changed_paths if path not in set(target.allowed_paths)]
        if unexpected:
            message = f"{target.name} 存在白名单外改动：{', '.join(unexpected)}"
            record["unexpected_paths"] = unexpected
            record["preflight_blockers"] = [message]
        allowed_changed = [path for path in changed_paths if path in set(target.allowed_paths)]
        record["allowed_changed_paths"] = allowed_changed
        if not changed_paths:
            message = f"{target.name} 没有待验证 diff，无法生成提交前验证结果。"
            record.update({"status": "preflight_failed", "message": message})
            return message
        if not allowed_changed:
            message = f"{target.name} 白名单文件没有待验证 diff。"
            record.update({"status": "preflight_failed", "message": message})
            return message
        untracked_allowed = [path for path in parse_untracked_status_paths(status.get("stdout", "")) if path in set(target.allowed_paths)]
        record["untracked_allowed_paths"] = untracked_allowed
        diff = read_allowed_current_diff(
            project_path=project_path,
            allowed_paths=target.allowed_paths,
            status_text=status.get("stdout", ""),
        )
        if diff["returncode"] != 0:
            message = f"{target.name} 当前 diff 读取失败：{diff.get('stderr') or diff.get('stdout') or diff.get('message') or diff.get('returncode')}"
            record.update({"status": "preflight_failed", "message": message})
            return message
        record["current_diff"] = diff.get("stdout", "")
        validation = validate_patch(diff.get("stdout", ""), allowed_paths=target.allowed_paths, allow_file_additions=True)
        if not validation.ok:
            message = f"{target.name} 当前 diff 不满足白名单校验：{validation.message}"
            record.update({"status": "preflight_failed", "message": message})
            return message
    return ""


def verify_target_current_diff(*, target: FullstackPatchTarget, worktree_path: Path) -> dict:
    project_path = Path(target.project_path).expanduser().resolve()
    current_diff = read_allowed_current_diff(project_path=project_path, allowed_paths=target.allowed_paths)
    record: dict = {
        "status": "running",
        "current_diff": current_diff.get("stdout", ""),
        "changed_paths": [],
        "verify": [],
    }
    if current_diff["returncode"] != 0:
        record["status"] = "diff_read_failed"
        record["message"] = current_diff.get("stderr") or current_diff.get("stdout") or current_diff.get("message") or "读取当前 diff 失败。"
        return record
    record["untracked_allowed_paths"] = current_diff.get("untracked_paths", [])
    validation = validate_patch(record["current_diff"], allowed_paths=target.allowed_paths, allow_file_additions=True)
    record["changed_paths"] = validation.changed_paths
    if not validation.ok:
        record["status"] = "rejected"
        record["message"] = validation.message
        return record
    apply_check = run_command(["git", "apply", "--check", "-"], cwd=worktree_path, input_text=record["current_diff"], timeout=PATCH_TIMEOUT_SECONDS)
    record["apply_check"] = apply_check
    if apply_check["returncode"] != 0:
        record["status"] = "apply_check_failed"
        record["message"] = "临时 worktree git apply --check 当前 diff 失败。"
        return record
    apply_result = run_command(["git", "apply", "-"], cwd=worktree_path, input_text=record["current_diff"], timeout=PATCH_TIMEOUT_SECONDS)
    record["apply"] = apply_result
    if apply_result["returncode"] != 0:
        record["status"] = "apply_failed"
        record["message"] = "临时 worktree git apply 当前 diff 失败。"
        return record
    diff_check = run_command(["git", "diff", "--check"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
    record["diff_check"] = diff_check
    if diff_check["returncode"] != 0:
        record["status"] = "diff_check_failed"
        record["message"] = "临时 worktree git diff --check 失败。"
        return record
    setup_result = prepare_target_verification(target=target, worktree_path=worktree_path)
    record["verification_setup"] = setup_result
    if setup_result.get("status") == "failed":
        record["status"] = "verification_setup_failed"
        record["message"] = setup_result.get("message") or "验证准备失败。"
        return record
    verify_results = []
    warnings = []
    verification_status = "passed" if target.verify_commands else "not_run"
    for command in target.verify_commands:
        before = capture_worktree_snapshot(worktree_path)
        verify = run_shell_command(command, cwd=worktree_path, timeout=VERIFY_TIMEOUT_SECONDS)
        after = capture_worktree_snapshot(worktree_path)
        side_effect = build_side_effect_report(before_snapshot=before, after_snapshot=after, side=target.key)
        verify = {**verify, "before_snapshot": before, "after_snapshot": after, "side_effects": side_effect}
        verify_results.append(verify)
        if verify["returncode"] != 0:
            baseline = run_baseline_verify(
                target=target,
                worktree_path=worktree_path,
                command=command,
                current_result=verify,
                untracked_paths=record.get("untracked_allowed_paths") or [],
            )
            verify["baseline_comparison"] = baseline
            classification = baseline.get("classification") or "verification_failed"
            verify["effective_status"] = classification
            if classification == "baseline_existing":
                verification_status = "baseline_failed"
                warning = f"{target.name} 验证命令存在历史基线失败，未判定为本次改动引入：{command}"
                warnings.append(warning)
                restore = run_command(["git", "apply", "-"], cwd=worktree_path, input_text=record["current_diff"], timeout=PATCH_TIMEOUT_SECONDS)
                verify["restore_after_baseline"] = restore
                if restore["returncode"] != 0:
                    record["status"] = "baseline_restore_failed"
                    verification_status = "failed"
                    record["failure_classification"] = classification
                    record["message"] = f"{target.name} baseline 对比后恢复当前 diff 失败。"
                    record["verify"] = verify_results
                    record["warnings"] = warnings
                    return record
                continue
            record["status"] = "verify_failed"
            verification_status = "tool_missing" if verify.get("returncode") in {126, 127} else "failed"
            record["failure_classification"] = classification
            record["message"] = f"{target.name} 验证命令失败：{command}（{classification}）"
            record["verify"] = verify_results
            record["warnings"] = warnings
            return record
        verify["effective_status"] = "pass"
        if target.key == "df-web-zhuyuansf" and side_effect.get("changed"):
            record["status"] = "verify_side_effect_failed"
            verification_status = "side_effect_failed"
            record["message"] = f"{target.name} 前端 lint 修改了临时 worktree。"
            record["verify"] = verify_results
            record["warnings"] = warnings
            return record
    record["verify"] = verify_results
    record["verification_status"] = verification_status
    record["status"] = "success"
    record["warnings"] = warnings
    record["message"] = (
        "提交前验证通过；存在历史验证基线告警。"
        if warnings
        else "提交前验证通过。"
    )
    return record


def remove_untracked_file_additions(*, worktree_path: Path, untracked_paths: list[str]) -> dict:
    root = worktree_path.resolve()
    removed_paths: list[str] = []
    skipped_paths: list[str] = []
    errors: list[str] = []
    for raw_path in unique_keep_order(untracked_paths):
        raw_relative_path = str(raw_path).strip().replace("\\", "/")
        raw_parts = [part for part in raw_relative_path.split("/") if part]
        if not raw_relative_path or raw_relative_path.startswith("/") or ".." in raw_parts:
            errors.append(f"未跟踪文件路径不安全：{raw_path}")
            continue
        relative_path = normalize_relative_path(raw_relative_path)
        safety = validate_relative_path(relative_path)
        if safety:
            errors.append(f"未跟踪文件路径不安全：{raw_path}（{safety}）")
            continue
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"未跟踪文件路径越出临时 worktree：{raw_path}")
            continue
        if not candidate.exists():
            skipped_paths.append(relative_path)
            continue
        if not candidate.is_file():
            errors.append(f"未跟踪新增路径不是普通文件，拒绝删除：{relative_path}")
            continue
        try:
            candidate.unlink()
        except OSError as error:
            errors.append(f"删除临时 worktree 未跟踪文件失败：{relative_path}（{error}）")
            continue
        removed_paths.append(relative_path)
    return {
        "status": "failed" if errors else "success",
        "removed_paths": removed_paths,
        "skipped_paths": skipped_paths,
        "errors": errors,
    }


def run_baseline_verify(
    *,
    target: FullstackPatchTarget,
    worktree_path: Path,
    command: str,
    current_result: dict,
    untracked_paths: list[str] | None = None,
) -> dict:
    reset = run_command(["git", "reset", "--hard", "HEAD"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
    cleanup = (
        remove_untracked_file_additions(worktree_path=worktree_path, untracked_paths=untracked_paths or [])
        if reset.get("returncode") == 0
        else {"status": "skipped", "removed_paths": [], "skipped_paths": [], "errors": ["git reset --hard HEAD 失败，未执行未跟踪文件清理。"]}
    )
    if reset.get("returncode") != 0 or cleanup.get("status") == "failed":
        return {
            "classification": "baseline_check_failed",
            "reset": reset,
            "untracked_cleanup": cleanup,
            "setup": {"status": "skipped", "message": "基线重置或未跟踪文件清理失败。"},
            "verify": {"returncode": None, "stdout": "", "stderr": "未执行基线验证。"},
            "baseline_fingerprint": "",
            "current_fingerprint": failure_fingerprint(current_result),
        }
    setup = prepare_target_verification(target=target, worktree_path=worktree_path)
    verify = run_shell_command(command, cwd=worktree_path, timeout=VERIFY_TIMEOUT_SECONDS)
    current_fingerprint = failure_fingerprint(current_result)
    baseline_fingerprint = failure_fingerprint(verify)
    if setup.get("status") == "failed":
        classification = "baseline_check_failed"
    elif verify.get("returncode") == 0:
        classification = "regression_failed"
    elif baseline_fingerprint == current_fingerprint:
        classification = "baseline_existing"
    else:
        classification = "changed_failure"
    return {
        "classification": classification,
        "reset": reset,
        "untracked_cleanup": cleanup,
        "setup": setup,
        "verify": verify,
        "baseline_fingerprint": baseline_fingerprint,
        "current_fingerprint": current_fingerprint,
    }


def prepare_target_verification(*, target: FullstackPatchTarget, worktree_path: Path) -> dict:
    return {"status": "skipped", "message": "无需额外验证准备；不临时启用 settings.gradle 中已注释的模块。"}


def compatible_gradle_command() -> str:
    for candidate in [
        "/opt/gradle/gradle-6.8.3/bin/gradle",
        "/opt/gradle/gradle-6.8/bin/gradle",
        "gradle",
    ]:
        path = Path(candidate)
        if candidate == "gradle" or path.is_file():
            return candidate
    return "gradle"


def compatible_java_home_prefix() -> str:
    for candidate in [
        "/Library/Java/JavaVirtualMachines/jdk-1.8.jdk/Contents/Home",
        "/Library/Java/JavaVirtualMachines/jdk8u*/Contents/Home",
    ]:
        matches = sorted(Path("/").glob(candidate.lstrip("/"))) if "*" in candidate else [Path(candidate)]
        for path in matches:
            if (path / "lib" / "tools.jar").is_file():
                return f"JAVA_HOME={path} "
    return ""


def build_result(
    *,
    status: str,
    summary: str,
    targets: list[dict],
    cleanup: dict[str, dict],
    manifest: dict,
    behavior_acceptance: dict | None = None,
    method_test_runner: dict | None = None,
    ui_evidence_runner: dict | None = None,
    interaction_evidence: dict | None = None,
) -> PrecommitVerificationResult:
    behavior_acceptance = behavior_acceptance or {}
    method_test_runner = method_test_runner or {}
    ui_evidence_runner = ui_evidence_runner or {}
    interaction_evidence = interaction_evidence or {}
    manifest["status"] = status
    manifest["summary"] = summary
    manifest["finished_at_epoch"] = time.time()
    matrix = build_verification_matrix(
        status=status,
        summary=summary,
        targets=targets,
        manifest=manifest,
        behavior_acceptance=behavior_acceptance,
        method_test_runner=method_test_runner,
        ui_evidence_runner=ui_evidence_runner,
        interaction_evidence=interaction_evidence,
    )
    return PrecommitVerificationResult(
        status=status,
        summary=summary,
        targets=targets,
        verification_matrix=matrix,
        behavior_acceptance=behavior_acceptance,
        method_test_runner=method_test_runner,
        ui_evidence_runner=ui_evidence_runner,
        interaction_evidence=interaction_evidence,
        cleanup=cleanup,
        manifest=manifest,
    )


def verification_gate_can_modify(verification_status: str | None) -> bool:
    """Only a real, side-effect-free pass can open the mutation gate."""
    return str(verification_status or "").strip().lower() == "passed"


def build_verification_matrix(
    *,
    status: str,
    summary: str,
    targets: list[dict],
    manifest: dict | None = None,
    behavior_acceptance: dict | None = None,
    method_test_runner: dict | None = None,
    ui_evidence_runner: dict | None = None,
    interaction_evidence: dict | None = None,
) -> dict:
    manifest = manifest or {}
    behavior_acceptance = behavior_acceptance or {}
    method_test_runner = method_test_runner or {}
    ui_evidence_runner = ui_evidence_runner or {}
    interaction_evidence = interaction_evidence or {}
    items: list[dict] = []
    blockers: list[str] = []
    warnings: list[str] = []
    scope_warnings: list[str] = []
    for target in targets:
        project = target.get("name") or target.get("key") or "-"
        warnings.extend(target.get("warnings") or [])
        for item in target.get("preflight_blockers") or []:
            scope_warnings.append(f"{project}：{item}")
        diff_check = target.get("diff_check") or {}
        if diff_check:
            items.append(
                {
                    "id": f"{target.get('key')}-diff-check",
                    "name": "git diff --check",
                    "type": "static_check",
                    "project": project,
                    "status": "pass" if diff_check.get("returncode") == 0 else "failed",
                    "evidence": diff_check.get("command") or "-",
                }
            )
        for verify in target.get("verify") or []:
            command = verify.get("command") or "-"
            effective_status = verify.get("effective_status")
            verify_status = "pass" if verify.get("returncode") == 0 else str(effective_status or "failed")
            items.append(
                {
                    "id": f"{target.get('key')}-verify-{len(items) + 1}",
                    "name": command,
                    "type": command_type(command),
                    "project": project,
                    "status": verify_status,
                    "evidence": summarize_verify(verify),
                }
            )
        target_status = target.get("status")
        verification_status = str(target.get("verification_status") or "")
        if verification_status and verification_status != "passed":
            blockers.append(f"{project}：验证状态为 {verification_status}，不能视为真实通过")
        if target_status and target_status != "success":
            blockers.append(f"{project}：{target.get('message') or target_status}")
    if behavior_acceptance:
        behavior_status = behavior_acceptance.get("status") or "skipped"
        items.append(
            {
                "id": "behavior-acceptance",
                "name": "v0.10 行为验收矩阵",
                "type": "behavior_acceptance",
                "project": "all",
                "status": behavior_status,
                "evidence": behavior_acceptance.get("summary") or "-",
            }
        )
        for check in behavior_acceptance.get("checks") or []:
            items.append(
                {
                    "id": check.get("id") or f"behavior-check-{len(items) + 1}",
                    "name": check.get("name") or check.get("id") or "behavior_check",
                    "type": "behavior_check",
                    "project": "all",
                    "status": check.get("status") or "-",
                    "evidence": check.get("evidence") or check.get("message") or "-",
                }
            )
        if behavior_status in {"failed", "needs_review"}:
            interaction_status = interaction_evidence.get("status") if interaction_evidence else ""
            if interaction_status != "pass":
                blockers.append(f"行为验收未通过：{behavior_acceptance.get('summary') or behavior_status}")
    if method_test_runner:
        runner_status = method_test_runner.get("status") or "skipped"
        items.append(
            {
                "id": "method-test-runner",
                "name": "v0.10.3A 方法级测试命令",
                "type": "method_test_runner",
                "project": "all",
                "status": runner_status,
                "evidence": method_test_runner.get("summary") or "-",
            }
        )
        for item in method_test_runner.get("cases") or []:
            items.append(
                {
                    "id": item.get("id") or f"method-test-runner-case-{len(items) + 1}",
                    "name": item.get("id") or "method_test_runner_case",
                    "type": "method_test_runner_case",
                    "project": "all",
                    "status": item.get("status") or "-",
                    "evidence": item.get("evidence") or "-",
                }
            )
        if runner_status in {"failed", "needs_evidence"}:
            blockers.append(f"方法级测试命令未通过：{method_test_runner.get('summary') or runner_status}")
    if ui_evidence_runner:
        ui_runner_status = ui_evidence_runner.get("status") or "skipped"
        items.append(
            {
                "id": "ui-evidence-runner",
                "name": "v0.10.3B UI 证据采集命令",
                "type": "ui_evidence_runner",
                "project": "all",
                "status": ui_runner_status,
                "evidence": ui_evidence_runner.get("summary") or "-",
            }
        )
        for item in ui_evidence_runner.get("assertions") or []:
            items.append(
                {
                    "id": item.get("name") or f"ui-evidence-assertion-{len(items) + 1}",
                    "name": item.get("name") or "ui_evidence_assertion",
                    "type": "ui_evidence_assertion",
                    "project": "all",
                    "status": item.get("status") or "-",
                    "evidence": item.get("evidence") or "-",
                }
            )
        if ui_runner_status in {"failed", "needs_evidence"}:
            blockers.append(f"UI 证据采集命令未通过：{ui_evidence_runner.get('summary') or ui_runner_status}")
    if interaction_evidence:
        interaction_status = interaction_evidence.get("status") or "skipped"
        items.append(
            {
                "id": "interaction-evidence",
                "name": "v0.10.2 方法级交互测试与 UI 证据",
                "type": "interaction_evidence",
                "project": "all",
                "status": interaction_status,
                "evidence": interaction_evidence.get("summary") or "-",
            }
        )
        method_result = interaction_evidence.get("method_regression_result") or {}
        for item in method_result.get("passed") or []:
            items.append(
                {
                    "id": item.get("id") or f"method-pass-{len(items) + 1}",
                    "name": item.get("name") or item.get("id") or "method_regression",
                    "type": "method_regression",
                    "project": "all",
                    "status": "pass",
                    "evidence": item.get("evidence") or "-",
                }
            )
        for item in (method_result.get("failed") or []) + (method_result.get("missing") or []):
            items.append(
                {
                    "id": item.get("id") or f"method-missing-{len(items) + 1}",
                    "name": item.get("name") or item.get("id") or "method_regression",
                    "type": "method_regression",
                    "project": "all",
                    "status": item.get("status") or "needs_evidence",
                    "evidence": item.get("evidence") or item.get("reason") or "-",
                }
            )
        if interaction_status in {"failed", "needs_evidence"}:
            blockers.append(f"交互证据未通过：{interaction_evidence.get('summary') or interaction_status}")
    if status != "success" and not blockers:
        blockers.append(summary)
    warnings.extend(scope_warnings)
    manual_acceptance = [
        "进入住院收费结算收款页面，确认预交金列表展示“备注”列。",
        "确认 REST 接口 `getAllByBingRenZyId` 返回的预交金备注字段能到达前端表格字段 `beiZhu`。",
        "确认该结算收款预缴表已按现有 `df-dx-table` 方式接入 `grid-view-name`，列配置不再被 `allowColumnConfig=false` 阻断。",
        "确认无备注、空字符串或历史数据为空时页面正常展示，不影响结算收款流程。",
    ]
    if manifest.get("generic_precommit"):
        title = str(manifest.get("title") or manifest.get("entity_id") or "当前需求")
        manual_acceptance = [
            f"按云效需求复现路径验证：{title}。",
            "确认本次变更覆盖需求描述中的实际问题和目标，不引入相邻页面/其他需求改动。",
            "确认涉及下拉排序、默认值或字段展示时，新增、编辑、回显、保存前后状态均符合现场预期。",
            "确认无数据、历史数据和参数缺省场景不出现空指针、错误默认值或页面异常。",
        ]
    behavior_manual = behavior_acceptance.get("manual_acceptance") or []
    if behavior_manual:
        manual_acceptance.extend(behavior_manual)
        manual_acceptance = unique_keep_order(manual_acceptance)
    behavior_gate = behavior_acceptance.get("gate") or {}
    behavior_allows_auto_commit = behavior_gate.get("auto_commit_allowed")
    if behavior_allows_auto_commit is None:
        behavior_allows_auto_commit = True
    behavior_allows_yunxiao_comment = behavior_gate.get("yunxiao_comment_allowed")
    if behavior_allows_yunxiao_comment is None:
        behavior_allows_yunxiao_comment = True
    interaction_gate = interaction_evidence.get("gate") or {}
    interaction_allows_auto_commit = interaction_gate.get("auto_commit_allowed")
    if interaction_allows_auto_commit is None:
        interaction_allows_auto_commit = True
    interaction_allows_yunxiao_comment = interaction_gate.get("yunxiao_comment_allowed")
    if interaction_allows_yunxiao_comment is None:
        interaction_allows_yunxiao_comment = True
    runner_allows_auto_commit = True
    if method_test_runner:
        runner_allows_auto_commit = method_test_runner.get("status") == "pass"
    ui_runner_allows_auto_commit = True
    if ui_evidence_runner:
        ui_runner_allows_auto_commit = ui_evidence_runner.get("status") == "pass"
    target_verification_statuses = [str(target.get("verification_status") or "") for target in targets]
    verification_status = "passed"
    if any(item == "tool_missing" for item in target_verification_statuses):
        verification_status = "tool_missing"
    elif any(item == "side_effect_failed" for item in target_verification_statuses):
        verification_status = "side_effect_failed"
    elif any(item == "baseline_failed" for item in target_verification_statuses):
        verification_status = "baseline_failed"
    elif any(item in {"failed", "not_run"} for item in target_verification_statuses):
        verification_status = "failed" if any(item == "failed" for item in target_verification_statuses) else "not_run"
    overall_status = "pass" if status == "success" and verification_status == "passed" else verification_status
    return {
        "version": "0.9.6-generic-precommit" if manifest.get("generic_precommit") else "0.9.1",
        "overall_status": overall_status,
        "verification_status": verification_status,
        "summary": summary,
        "can_commit": overall_status == "pass" and not scope_warnings and bool(behavior_allows_auto_commit) and bool(interaction_allows_auto_commit) and bool(runner_allows_auto_commit) and bool(ui_runner_allows_auto_commit),
        "can_enter_test": "人工代码审查通过后可进入测试" if overall_status == "pass" else "不可进入测试，需先修复或补齐验证",
        "can_yunxiao_comment": overall_status == "pass" and not scope_warnings and bool(behavior_allows_yunxiao_comment) and bool(interaction_allows_yunxiao_comment) and bool(runner_allows_auto_commit) and bool(ui_runner_allows_auto_commit),
        "can_yunxiao_transition": False,
        "behavior_acceptance": behavior_acceptance,
        "method_test_runner": method_test_runner,
        "ui_evidence_runner": ui_evidence_runner,
        "interaction_evidence": interaction_evidence,
        "items": items,
        "warnings": warnings,
        "manual_acceptance": manual_acceptance,
        "blockers": blockers,
        "yunxiao_boundary": "本阶段不真实流转云效状态、负责人、迭代或关闭任务。",
    }


def build_precommit_behavior_acceptance(*, options: PrecommitVerificationOptions, records: list[dict]) -> dict:
    diffs = "\n".join(str(record.get("current_diff") or "") for record in records if record.get("current_diff"))
    changed_paths = unique_keep_order(
        [
            str(path)
            for record in records
            for path in (record.get("changed_paths") or record.get("allowed_changed_paths") or [])
            if path
        ]
    )
    return build_behavior_acceptance(
        title=options.title or options.entity_id or "当前提交前验证",
        demand_text=options.demand_text,
        diff_text=diffs,
        changed_paths=changed_paths,
    )


def build_precommit_interaction_evidence(
    *,
    options: PrecommitVerificationOptions,
    records: list[dict],
    behavior_acceptance: dict,
    method_evidence: dict,
    ui_evidence_paths: list[str],
) -> dict:
    diffs = "\n".join(str(record.get("current_diff") or "") for record in records if record.get("current_diff"))
    changed_paths = unique_keep_order(
        [
            str(path)
            for record in records
            for path in (record.get("changed_paths") or record.get("allowed_changed_paths") or [])
            if path
        ]
    )
    return build_interaction_evidence_package(
        title=options.title or options.entity_id or "当前提交前验证",
        demand_text=options.demand_text,
        diff_text=diffs,
        changed_paths=changed_paths,
        behavior_acceptance=behavior_acceptance,
        method_evidence=method_evidence,
        ui_evidence_paths=ui_evidence_paths,
    )


def build_precommit_method_test_runner(
    *,
    options: PrecommitVerificationOptions,
    records: list[dict],
    behavior_test_plan: dict,
) -> dict:
    if not options.method_test_commands:
        return {}
    worktree_path = first_success_worktree_path(records)
    if not worktree_path:
        return {
            "version": METHOD_TEST_RUNNER_VERSION,
            "status": "failed",
            "summary": "未找到可执行方法级测试命令的临时 worktree。",
            "cwd": "",
            "required_case_ids": [
                str(item.get("id") or "")
                for item in behavior_test_plan.get("cases") or []
                if item.get("required") and item.get("id")
            ],
            "missing_case_ids": [],
            "cases": [],
            "commands": [],
        }
    return run_method_test_commands(
        behavior_test_plan=behavior_test_plan,
        commands=options.method_test_commands,
        cwd=worktree_path,
    )


def build_precommit_ui_evidence_runner(
    *,
    options: PrecommitVerificationOptions,
    records: list[dict],
    worktree_root: Path,
) -> dict:
    if not options.ui_capture_commands:
        return {}
    worktree_path = first_success_worktree_path(records)
    output_dir = worktree_root / f"ui_evidence_{options.run_id}"
    if not worktree_path:
        return {
            "version": UI_EVIDENCE_RUNNER_VERSION,
            "status": "failed",
            "summary": "未找到可执行 UI 证据采集命令的临时 worktree。",
            "cwd": "",
            "output_dir": str(output_dir),
            "artifact_paths": [],
            "artifacts": [],
            "assertions": [],
            "commands": [],
        }
    return run_ui_evidence_commands(
        commands=options.ui_capture_commands,
        cwd=worktree_path,
        output_dir=output_dir,
    )


def first_success_worktree_path(records: list[dict]) -> str:
    for record in records:
        if record.get("status") == "success" and record.get("worktree_path"):
            return str(record.get("worktree_path"))
    for record in records:
        if record.get("worktree_path"):
            return str(record.get("worktree_path"))
    return ""


def unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def command_type(command: str) -> str:
    if "compileJava" in command:
        return "compile_check"
    if "lint" in command:
        return "lint_check"
    return "verification_command"


def summarize_verify(verify: dict) -> str:
    stdout = str(verify.get("stdout") or "").strip()
    stderr = str(verify.get("stderr") or "").strip()
    parts = [f"returncode={verify.get('returncode')}"]
    if stdout:
        parts.append(truncate_text(stdout, 300))
    if stderr:
        parts.append(truncate_text(stderr, 300))
    side_effect = verify.get("side_effects") or {}
    if side_effect.get("changed"):
        parts.append("side_effect_changed=true")
    baseline = verify.get("baseline_comparison") or {}
    if baseline:
        parts.append(f"baseline_classification={baseline.get('classification')}")
        fingerprint = baseline.get("baseline_fingerprint")
        if fingerprint:
            parts.append(truncate_text(str(fingerprint), 300))
    return "\n".join(parts)


def failure_fingerprint(command_result: dict) -> str:
    text = f"{command_result.get('stdout') or ''}\n{command_result.get('stderr') or ''}"
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = normalize_fingerprint_line(line)
        if not line:
            continue
        lowered = line.lower()
        if any(token in lowered for token in ["error", "failed", "could not", "cannot", "找不到", "错误", "失败"]):
            lines.append(line)
        if len(lines) >= 12:
            break
    return "\n".join(lines)


def normalize_fingerprint_line(line: str) -> str:
    if line.startswith("Starting a Gradle Daemon"):
        return ""
    line = re.sub(r"/(?:private/)?tmp/his_harness[^:\s]+", "<tmp>", line)
    line = re.sub(r"BUILD FAILED in \S+", "BUILD FAILED", line)
    line = re.sub(r"\d+ actionable tasks?: .*", "<gradle actionable tasks>", line)
    return line


def target_to_record(target: FullstackPatchTarget) -> dict:
    return {
        **asdict(target),
        "status": "pending",
        "message": "",
        "changed_paths": [],
        "worktree_path": "",
    }


def record_for_key(records: list[dict], key: str) -> dict:
    for record in records:
        if record.get("key") == key:
            return record
    raise KeyError(key)
