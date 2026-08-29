from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
    summarize_command,
    truncate_text,
    unique_keep_order,
    validate_relative_path,
)
from app.worktree_lifecycle import create_worktree_marker, remove_worktree_marker


MAX_REVIEW_DIFF_CHARS = 24000


@dataclass
class ReviewExecutionOptions:
    project_path: str
    run_id: int
    review_commit: str = "HEAD"
    review_base: str = ""
    review_context: dict | None = None
    worktree_root: str = DEFAULT_WORKTREE_ROOT
    allowed_paths: list[str] = field(default_factory=list)
    verify_commands: list[str] = field(default_factory=list)


@dataclass
class ReviewExecutionResult:
    status: str
    summary: str
    worktree_path: str = ""
    base_worktree_path: str = ""
    head_worktree_path: str = ""
    review_commit: str = ""
    review_base: str = ""
    changed_paths: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    diff_stat: str = ""
    review_diff: str = ""
    diff_check: dict = field(default_factory=dict)
    verify_results: list[dict] = field(default_factory=list)
    dependency_links: list[dict] = field(default_factory=list)
    post_verify_status: str = ""
    manifest: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = [
            "## v0.7.3 已提交 Diff 审查结果",
            "",
            f"- 状态：{self.status}",
            f"- 结论：{self.summary}",
            f"- Head Worktree：{self.head_worktree_path or self.worktree_path or '-'}",
            f"- Base Worktree：{self.base_worktree_path or '-'}",
            f"- Commit：{self.review_commit or '-'}",
            f"- Base：{self.review_base or '-'}",
            f"- 变更文件：{', '.join(self.changed_paths) if self.changed_paths else '-'}",
            f"- 允许审查路径：{', '.join(self.allowed_paths) if self.allowed_paths else '-'}",
            "- 禁止动作：不生成 patch、不提交、不推送、不发布、不写云效事务。",
            "",
            "### 审查结论分层",
            "",
            "- 模型审查结论：见上方专家团 9 阶段报告与 Evaluator 审核结论。",
            f"- 提交 diff 验证结论：{self.status} / {self.summary}",
            "- 历史基线风险：`baseline_existing` 表示 base/head 均失败且错误指纹一致，只作为 warning，不伪装成验证通过。",
            "- 验证命令副作用：`baseline_side_effect` 作为 warning；`head_side_effect_failed` 会阻断当前提交。",
            "",
            "### 验证结果",
            "",
        ]
        if self.diff_check:
            lines.append(f"- `git diff --check`：{command_status(self.diff_check)}")
        else:
            lines.append("- `git diff --check`：未运行")
        if self.verify_results:
            for result in self.verify_results:
                lines.append(f"- `{result.get('command')}`：{review_verify_status(result)}")
        else:
            lines.append("- 显式验证命令：未传入")
        side_effect_lines = build_side_effect_markdown_lines(self.verify_results)
        if side_effect_lines:
            lines.extend(["", "### 验证命令副作用", ""])
            lines.extend(side_effect_lines)
        if self.dependency_links:
            lines.extend(["", "### 临时 Worktree 依赖链接", ""])
            for item in self.dependency_links:
                side = f"{item.get('side')} " if item.get("side") else ""
                lines.append(f"- {side}{item.get('status')} `{item.get('name')}`：{item.get('source')} -> {item.get('target')}")
        if self.post_verify_status:
            lines.extend(["", "### Worktree 验证后状态", "", "```text", self.post_verify_status, "```"])
        if self.diff_stat:
            lines.extend(["", "### Diff Stat", "", "```text", self.diff_stat, "```"])
        if self.review_diff:
            lines.extend(["", "### Review Diff 摘要", "", "```diff", truncate_text(self.review_diff, 8000), "```"])
        return "\n".join(lines)


def build_review_context(
    *,
    project_path: str | Path,
    review_commit: str = "HEAD",
    review_base: str = "",
    allowed_paths: list[str] | None = None,
) -> dict:
    repo = Path(project_path).expanduser().resolve()
    normalized_allowed = unique_keep_order(normalize_relative_path(path) for path in (allowed_paths or []) if path)
    preflight_error = preflight_review_repo(project_path=repo, allowed_paths=normalized_allowed)
    if preflight_error:
        raise ValueError(preflight_error)

    commit_input = validate_revision(review_commit or "HEAD", field_name="review_commit")
    base_input = validate_revision(review_base or f"{commit_input}^", field_name="review_base")
    commit_sha = resolve_commit(repo, commit_input)
    base_sha = resolve_commit(repo, base_input)

    name_result = run_command(["git", "diff", "--name-only", "--find-renames", f"{base_sha}..{commit_sha}"], cwd=repo, timeout=PATCH_TIMEOUT_SECONDS)
    if name_result["returncode"] != 0:
        raise ValueError("读取提交变更文件失败：" + summarize_command(name_result))
    changed_paths = unique_keep_order(normalize_relative_path(line) for line in name_result["stdout"].splitlines() if line.strip())
    if not changed_paths:
        raise ValueError("review-worktree 模式未检测到可审查的提交 diff")
    for path in changed_paths:
        safety = validate_relative_path(path)
        if safety:
            raise ValueError(safety)
    if normalized_allowed:
        allowed_set = set(normalized_allowed)
        outside = [path for path in changed_paths if path not in allowed_set]
        if outside:
            raise ValueError("提交改动超出 --allowed-path：" + ", ".join(outside))
    effective_allowed = normalized_allowed or changed_paths

    stat_result = run_command(["git", "diff", "--stat", "--find-renames", f"{base_sha}..{commit_sha}"], cwd=repo, timeout=PATCH_TIMEOUT_SECONDS)
    if stat_result["returncode"] != 0:
        raise ValueError("读取提交 diff stat 失败：" + summarize_command(stat_result))
    diff_result = run_command(
        ["git", "diff", "--src-prefix=a/", "--dst-prefix=b/", "--find-renames", f"{base_sha}..{commit_sha}"],
        cwd=repo,
        timeout=PATCH_TIMEOUT_SECONDS,
    )
    if diff_result["returncode"] != 0:
        raise ValueError("读取提交 diff 失败：" + summarize_command(diff_result))
    subject_result = run_command(["git", "log", "-1", "--format=%s", commit_sha], cwd=repo, timeout=PATCH_TIMEOUT_SECONDS)
    subject = subject_result["stdout"].strip() if subject_result["returncode"] == 0 else ""
    full_diff = diff_result["stdout"]
    review_id = build_review_id(project_path=str(repo), review_base=base_sha, review_commit=commit_sha, changed_paths=changed_paths)
    return {
        "mode": "review-worktree",
        "review_id": review_id,
        "project_path": str(repo),
        "review_commit": {"input": commit_input, "sha": commit_sha, "subject": subject},
        "review_base": {"input": base_input, "sha": base_sha},
        "changed_paths": changed_paths,
        "allowed_paths": effective_allowed,
        "diff_stat": stat_result["stdout"].strip(),
        "diff_name_only": "\n".join(changed_paths),
        "diff_excerpt": truncate_text(full_diff, MAX_REVIEW_DIFF_CHARS),
        "diff_truncated": len(full_diff) > MAX_REVIEW_DIFF_CHARS,
        "policy": "审查已提交 diff：不生成 patch、不 git apply、不提交、不推送、不发布、不写云效事务。",
    }


class ReviewWorktreeExecutor:
    def execute(self, options: ReviewExecutionOptions) -> ReviewExecutionResult:
        started_at = time.time()
        project_path = Path(options.project_path).expanduser().resolve()
        worktree_root = Path(options.worktree_root).expanduser().resolve()
        base_worktree_path = worktree_root / f"run_{options.run_id}_base"
        head_worktree_path = worktree_root / f"run_{options.run_id}_head"
        review_context = options.review_context or build_review_context(
            project_path=project_path,
            review_commit=options.review_commit,
            review_base=options.review_base,
            allowed_paths=options.allowed_paths,
        )
        commit_sha = str(review_context.get("review_commit", {}).get("sha") or "")
        base_sha = str(review_context.get("review_base", {}).get("sha") or "")
        changed_paths = list(review_context.get("changed_paths", []))
        allowed_paths = list(review_context.get("allowed_paths", []))
        manifest = {
            "run_id": options.run_id,
            "project_path": str(project_path),
            "worktree_root": str(worktree_root),
            "base_worktree_path": str(base_worktree_path),
            "head_worktree_path": str(head_worktree_path),
            "review_context": review_context,
            "verify_commands": options.verify_commands,
            "started_at_epoch": started_at,
        }

        preflight_error = preflight_review_repo(project_path=project_path, allowed_paths=allowed_paths)
        if preflight_error:
            return self._failed_result(
                summary=preflight_error,
                worktree_path=head_worktree_path,
                base_worktree_path=base_worktree_path,
                head_worktree_path=head_worktree_path,
                review_context=review_context,
                manifest=manifest,
            )
        outside = [path for path in changed_paths if allowed_paths and path not in set(allowed_paths)]
        if outside:
            return self._failed_result(
                summary="提交改动超出 --allowed-path：" + ", ".join(outside),
                worktree_path=head_worktree_path,
                base_worktree_path=base_worktree_path,
                head_worktree_path=head_worktree_path,
                review_context=review_context,
                manifest=manifest,
            )

        setup_error = create_review_worktree(
            project_path=project_path,
            worktree_root=worktree_root,
            worktree_path=base_worktree_path,
            commit_sha=base_sha,
        )
        if setup_error:
            return self._failed_result(
                summary=setup_error,
                worktree_path=head_worktree_path,
                base_worktree_path=base_worktree_path,
                head_worktree_path=head_worktree_path,
                review_context=review_context,
                manifest=manifest,
            )
        setup_error = create_review_worktree(
            project_path=project_path,
            worktree_root=worktree_root,
            worktree_path=head_worktree_path,
            commit_sha=commit_sha,
        )
        if setup_error:
            base_cleanup = cleanup_git_worktree(project_path=project_path, worktree_path=base_worktree_path)
            manifest["cleanup"] = {"base": base_cleanup}
            return self._failed_result(
                summary=setup_error,
                worktree_path=head_worktree_path,
                base_worktree_path=base_worktree_path,
                head_worktree_path=head_worktree_path,
                review_context=review_context,
                manifest=manifest,
            )
        dependency_links = []
        for side, path in (("base", base_worktree_path), ("head", head_worktree_path)):
            for item in prepare_dependency_links(project_path=project_path, worktree_path=path):
                item["side"] = side
                dependency_links.append(item)
        manifest["dependency_links"] = dependency_links

        dependency_error = first_failed_dependency_link(dependency_links)
        diff_check = run_command(["git", "diff", "--check", f"{base_sha}..{commit_sha}"], cwd=head_worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
        verify_results: list[dict] = []
        blocking_verify_results: list[dict] = []
        if diff_check["returncode"] == 0 and not dependency_error:
            for command in options.verify_commands:
                result = run_verify_comparison(
                    command=command,
                    base_worktree_path=base_worktree_path,
                    head_worktree_path=head_worktree_path,
                )
                verify_results.append(result)
                if result.get("blocks_current_commit"):
                    blocking_verify_results.append(result)
        base_status_result = run_command(["git", "status", "--porcelain"], cwd=base_worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
        head_status_result = run_command(["git", "status", "--porcelain"], cwd=head_worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
        post_verify_status = "\n".join(
            [
                "base:",
                base_status_result["stdout"].strip() if base_status_result["returncode"] == 0 else summarize_command(base_status_result),
                "head:",
                head_status_result["stdout"].strip() if head_status_result["returncode"] == 0 else summarize_command(head_status_result),
            ]
        ).strip()

        baseline_warnings = [item for item in verify_results if item.get("classification") in {"baseline_existing", "baseline_side_effect"}]
        if dependency_error:
            status = "failed"
            summary = "临时 worktree 依赖链接失败：" + dependency_error
        elif diff_check["returncode"] != 0:
            status = "failed"
            summary = "提交 diff 未通过 git diff --check：" + summarize_command(diff_check)
        elif blocking_verify_results:
            status = "failed"
            failed_commands = [
                f"{item.get('command', '')}({item.get('classification')})"
                for item in blocking_verify_results
            ]
            summary = "至少一个显式验证命令阻断当前提交：" + ", ".join(failed_commands)
        elif baseline_warnings:
            status = "success"
            warning_commands = [item.get("command", "") for item in baseline_warnings]
            summary = "已提交 diff 未发现新增验证失败，但存在历史验证基线或验证副作用 warning：" + ", ".join(warning_commands)
        else:
            status = "success"
            summary = "已提交 diff 在独立 worktree 中通过审查验证，可进入人工代码审查/测试。"

        manifest["finished_at_epoch"] = time.time()
        manifest["status"] = status
        manifest["summary"] = summary
        manifest["diff_check"] = diff_check
        manifest["verify_results"] = verify_results
        manifest["blocking_verify_results"] = blocking_verify_results
        manifest["post_verify_status"] = post_verify_status
        manifest["cleanup"] = {
            "head": cleanup_git_worktree(project_path=project_path, worktree_path=head_worktree_path),
            "base": cleanup_git_worktree(project_path=project_path, worktree_path=base_worktree_path),
        }
        return ReviewExecutionResult(
            status=status,
            summary=summary,
            worktree_path=str(head_worktree_path),
            base_worktree_path=str(base_worktree_path),
            head_worktree_path=str(head_worktree_path),
            review_commit=commit_sha,
            review_base=base_sha,
            changed_paths=changed_paths,
            allowed_paths=allowed_paths,
            diff_stat=str(review_context.get("diff_stat") or ""),
            review_diff=str(review_context.get("diff_excerpt") or ""),
            diff_check=diff_check,
            verify_results=verify_results,
            dependency_links=dependency_links,
            post_verify_status=post_verify_status,
            manifest=manifest,
        )

    def _failed_result(
        self,
        *,
        summary: str,
        worktree_path: Path,
        base_worktree_path: Path | None = None,
        head_worktree_path: Path | None = None,
        review_context: dict,
        manifest: dict,
    ) -> ReviewExecutionResult:
        manifest["finished_at_epoch"] = time.time()
        manifest["status"] = "failed"
        manifest["summary"] = summary
        return ReviewExecutionResult(
            status="failed",
            summary=summary,
            worktree_path=str(worktree_path),
            base_worktree_path=str(base_worktree_path or ""),
            head_worktree_path=str(head_worktree_path or worktree_path),
            review_commit=str(review_context.get("review_commit", {}).get("sha") or ""),
            review_base=str(review_context.get("review_base", {}).get("sha") or ""),
            changed_paths=list(review_context.get("changed_paths", [])),
            allowed_paths=list(review_context.get("allowed_paths", [])),
            diff_stat=str(review_context.get("diff_stat") or ""),
            review_diff=str(review_context.get("diff_excerpt") or ""),
            manifest=manifest,
        )


def preflight_review_repo(*, project_path: Path, allowed_paths: list[str]) -> str:
    if not project_path.exists():
        return f"项目路径不存在：{project_path}"
    if not project_path.is_dir():
        return f"项目路径不是目录：{project_path}"
    root_result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    if root_result["returncode"] != 0:
        return "review-worktree 模式要求 project-path 是 Git 仓库"
    git_root = Path(root_result["stdout"].strip()).resolve()
    if git_root != project_path:
        return f"project-path 必须指向 Git 仓库根目录：当前根目录为 {git_root}"
    status_result = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    if status_result["returncode"] != 0:
        return "无法读取原项目 git status"
    if status_result["stdout"].strip():
        return "原项目存在未提交改动，拒绝创建 review worktree"
    for path in allowed_paths:
        safety = validate_relative_path(path)
        if safety:
            return safety
    return ""


def create_review_worktree(*, project_path: Path, worktree_root: Path, worktree_path: Path, commit_sha: str) -> str:
    worktree_root.mkdir(parents=True, exist_ok=True)
    run_command(["git", "worktree", "prune"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    if worktree_path.exists():
        return f"目标 review worktree 已存在，拒绝覆盖；请先使用安全清理预览处理：{worktree_path}"
    create_worktree_marker(
        worktree_root=worktree_root,
        worktree_path=worktree_path,
        project_path=project_path,
        run_id=worktree_path.name.removeprefix("run_"),
        role="review",
    )
    result = run_command(["git", "worktree", "add", "--detach", str(worktree_path), commit_sha], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    if result["returncode"] != 0:
        remove_worktree_marker(worktree_root=worktree_root, worktree_path=worktree_path)
        return "创建 review Git worktree 失败：" + summarize_command(result)
    return ""


def resolve_commit(repo: Path, revision: str) -> str:
    result = run_command(["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=repo, timeout=PATCH_TIMEOUT_SECONDS)
    if result["returncode"] != 0:
        raise ValueError(f"无法解析提交 {revision}：" + summarize_command(result))
    return result["stdout"].strip()


def validate_revision(value: str, *, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    if len(text) > 200 or any(char in text for char in "\n\r\t\0"):
        raise ValueError(f"{field_name} 不是安全的 Git revision：{value!r}")
    return text


def build_review_id(*, project_path: str, review_base: str, review_commit: str, changed_paths: list[str]) -> str:
    raw = json.dumps(
        {
            "project_path": project_path,
            "review_base": review_base,
            "review_commit": review_commit,
            "changed_paths": changed_paths,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "review-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def command_status(result: dict) -> str:
    code = result.get("returncode")
    if code == 0:
        return "pass"
    return f"failed ({summarize_command(result)})"


def review_verify_status(result: dict) -> str:
    classification = result.get("classification") or "unknown"
    blocks = "阻断" if result.get("blocks_current_commit") else "不阻断"
    summary = result.get("summary") or "-"
    return f"{classification} / {blocks} / {summary}"


def build_side_effect_markdown_lines(results: list[dict]) -> list[str]:
    lines: list[str] = []
    for result in results:
        side_effects = result.get("side_effects") or {}
        if not side_effects.get("has_side_effects"):
            continue
        command = result.get("command") or "-"
        classification = result.get("classification") or "-"
        blocks = "阻断" if result.get("blocks_current_commit") else "不阻断"
        lines.append(f"- `{command}`：{classification} / {blocks}")
        for side in ["base", "head"]:
            item = side_effects.get(side) or {}
            if item.get("changed"):
                paths = ", ".join(item.get("changed_paths", [])) or "-"
                lines.append(f"  - {side} 修改路径：{paths}")
    return lines


def first_failed_dependency_link(items: list[dict]) -> str:
    for item in items:
        if item.get("status") == "failed":
            return f"{item.get('side') or '-'} {item.get('name')}: {item.get('error') or 'unknown'}"
    return ""


def run_verify_comparison(*, command: str, base_worktree_path: Path, head_worktree_path: Path) -> dict:
    base_before_snapshot = capture_worktree_snapshot(base_worktree_path)
    base_result = run_shell_command(command, cwd=base_worktree_path, timeout=VERIFY_TIMEOUT_SECONDS)
    base_after_snapshot = capture_worktree_snapshot(base_worktree_path)
    head_before_snapshot = capture_worktree_snapshot(head_worktree_path)
    head_result = run_shell_command(command, cwd=head_worktree_path, timeout=VERIFY_TIMEOUT_SECONDS)
    head_after_snapshot = capture_worktree_snapshot(head_worktree_path)
    base_side_effect = build_side_effect_report(
        before_snapshot=base_before_snapshot,
        after_snapshot=base_after_snapshot,
        side="base",
    )
    head_side_effect = build_side_effect_report(
        before_snapshot=head_before_snapshot,
        after_snapshot=head_after_snapshot,
        side="head",
    )
    base_fingerprint = verify_fingerprint(base_result, worktree_path=base_worktree_path)
    head_fingerprint = verify_fingerprint(head_result, worktree_path=head_worktree_path)
    base_classification, base_blocks_current_commit, base_summary = classify_verify_result(
        base_result=base_result,
        head_result=head_result,
        base_fingerprint=base_fingerprint,
        head_fingerprint=head_fingerprint,
    )
    classification, blocks_current_commit, summary = classify_with_side_effects(
        base_classification=base_classification,
        base_blocks_current_commit=base_blocks_current_commit,
        base_summary=base_summary,
        base_side_effect=base_side_effect,
        head_side_effect=head_side_effect,
    )
    return {
        "command": command,
        "classification": classification,
        "verify_classification": base_classification,
        "blocks_current_commit": blocks_current_commit,
        "summary": summary,
        "base": base_result,
        "head": head_result,
        "base_fingerprint": base_fingerprint,
        "head_fingerprint": head_fingerprint,
        "side_effects": {
            "has_side_effects": base_side_effect["changed"] or head_side_effect["changed"],
            "base": base_side_effect,
            "head": head_side_effect,
        },
    }


def classify_with_side_effects(
    *,
    base_classification: str,
    base_blocks_current_commit: bool,
    base_summary: str,
    base_side_effect: dict,
    head_side_effect: dict,
) -> tuple[str, bool, str]:
    if head_side_effect.get("changed"):
        paths = ", ".join(head_side_effect.get("changed_paths", [])) or "-"
        return "head_side_effect_failed", True, f"验证命令修改了 head worktree：{paths}"
    if base_side_effect.get("changed"):
        paths = ", ".join(base_side_effect.get("changed_paths", [])) or "-"
        return "baseline_side_effect", False, f"验证命令只修改了 base worktree，按历史基线副作用 warning 处理：{paths}"
    return base_classification, base_blocks_current_commit, base_summary


def classify_verify_result(
    *,
    base_result: dict,
    head_result: dict,
    base_fingerprint: dict,
    head_fingerprint: dict,
) -> tuple[str, bool, str]:
    if is_infra_failure(base_result) or is_infra_failure(head_result):
        return "infra_failed", True, "验证命令或执行环境异常，需先处理依赖/命令/worktree。"
    base_code = int(base_result.get("returncode", 1))
    head_code = int(head_result.get("returncode", 1))
    if head_code == 0:
        if base_code == 0:
            return "pass", False, "base/head 均通过。"
        return "pass", False, "head 通过；当前提交未引入验证失败。"
    if base_code == 0 and head_code != 0:
        return "regression_failed", True, "base 通过但 head 失败，判定为本次提交引入的问题。"
    if base_code != 0 and head_code != 0:
        same_code = base_code == head_code
        same_fingerprint = base_fingerprint.get("sha1") == head_fingerprint.get("sha1")
        if same_code and same_fingerprint:
            return "baseline_existing", False, "base/head 均失败且错误指纹一致，判定为历史基线问题。"
        return "changed_failure", True, "base/head 均失败但错误指纹不同，需人工判断是否影响当前提交。"
    return "changed_failure", True, "验证状态无法归类，需人工介入。"


def is_infra_failure(result: dict) -> bool:
    code = int(result.get("returncode", 1))
    text = f"{result.get('stderr') or ''}\n{result.get('stdout') or ''}".lower()
    if code in {124, 126, 127}:
        return True
    infra_markers = [
        "command not found",
        "not found",
        "no such file or directory",
        "enoent",
        "permission denied",
        "operation not permitted",
    ]
    return any(marker in text for marker in infra_markers)


def verify_fingerprint(result: dict, *, worktree_path: Path) -> dict:
    raw_text = (result.get("stderr") or result.get("stdout") or "").strip()
    if not raw_text:
        raw_text = f"returncode={result.get('returncode')}"
    normalized = normalize_verify_output(raw_text, worktree_path=worktree_path)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return {
        "sha1": digest,
        "returncode": result.get("returncode"),
        "excerpt": truncate_text(normalized, 1600),
    }


def normalize_verify_output(text: str, *, worktree_path: Path) -> str:
    normalized = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    normalized = normalized.replace("\r", "\n")
    path_variants = {
        str(worktree_path),
        str(worktree_path.resolve()),
        str(worktree_path).replace("/private/tmp/", "/tmp/"),
        str(worktree_path).replace("/tmp/", "/private/tmp/"),
    }
    for value in sorted(path_variants, key=len, reverse=True):
        if value:
            normalized = normalized.replace(value, "<worktree>")
    normalized = re.sub(r"run_\d+_(base|head)", "run_<id>_<side>", normalized)
    normalized = re.sub(r"\b\d+(\.\d+)?\s*(ms|s)\b", "<duration>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"Done in \d+(\.\d+)?s\.", "Done in <duration>.", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()
