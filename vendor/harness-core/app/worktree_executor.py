from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.llm_client import BaseLLMClient
from app.worktree_lifecycle import create_worktree_marker, remove_worktree_marker


class SafeGitBoundary:
    """Fail-closed Git invocation surface for the Stage-F local agent.

    It never inherits a caller's Git environment and disables hooks, fsmonitor,
    external diff and text conversion on every invocation.
    """
    _ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path)
        self.preflight()

    def run(self, arguments: list[str], *, cwd: Path | None = None, input_bytes: bytes | None = None, timeout: int = 30) -> dict[str, object]:
        if not isinstance(arguments, list) or any(not isinstance(item, str) or not item for item in arguments):
            raise ValueError("safe_git_invalid")
        diff_flags = ["--no-ext-diff", "--no-textconv"] if arguments and arguments[0] == "diff" else []
        command = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *arguments[:1], *diff_flags, *arguments[1:]]
        try:
            completed = subprocess.run(command, cwd=os.fspath(cwd or self.project_path), input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=dict(self._ENV), timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return {"returncode": 125, "stdout": b""}
        return {"returncode": completed.returncode, "stdout": completed.stdout}

    def text(self, arguments: list[str], *, cwd: Path | None = None, timeout: int = 30) -> str:
        result = self.run(arguments, cwd=cwd, timeout=timeout)
        if result["returncode"] != 0:
            raise ValueError("safe_git_failed")
        try:
            return bytes(result["stdout"]).decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("safe_git_output_invalid") from exc

    def preflight(self) -> None:
        git_dir = self.project_path / ".git"
        if not git_dir.is_dir() or git_dir.is_symlink():
            raise ValueError("safe_git_repository_invalid")
        hooks = git_dir / "hooks"
        if hooks.exists() and any(not item.name.endswith(".sample") for item in hooks.iterdir()):
            raise ValueError("safe_git_hooks_forbidden")
        config = self.text(["config", "--local", "--null", "--list"], cwd=self.project_path)
        entries = [entry for entry in config.split("\x00") if entry]
        for entry in entries:
            key = entry.split("\n", 1)[0].lower()
            if key == "core.fsmonitor" or key == "diff.external" or key.endswith(".textconv") or (key.startswith("filter.") and key.rsplit(".", 1)[-1] in {"command", "smudge", "clean", "process"}):
                raise ValueError("safe_git_config_forbidden")
        attributes_files = [*self.project_path.rglob(".gitattributes"), git_dir / "info" / "attributes"]
        for attributes in attributes_files:
            if not attributes.exists():
                continue
            if attributes.is_symlink() or not attributes.is_file():
                raise ValueError("safe_git_attributes_forbidden")
            text = attributes.read_text(encoding="utf-8", errors="strict")
            if any(token in text for token in ("filter=", "diff=", "textconv", "working-tree-encoding")):
                raise ValueError("safe_git_attributes_forbidden")


DEFAULT_WORKTREE_ROOT = "/tmp/his_harness_worktrees"
PROHIBITED_PATH_PARTS = {".git", ".hg", ".svn"}
PROHIBITED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "id_rsa",
    "id_dsa",
}
PROHIBITED_PATH_PREFIXES = {
    "config/yunxiao",
    "config/aliyun",
    "config/jenkins",
    "config/k8s",
}
PATCH_TIMEOUT_SECONDS = 120
VERIFY_TIMEOUT_SECONDS = 600
MAX_LOG_CHARS = 24000
DEPENDENCY_LINK_DIRS = ["node_modules"]


def build_worktree_run_key(run_id: int, execution_nonce: str | None = None) -> str:
    nonce = (execution_nonce or uuid.uuid4().hex).replace("-", "")[:16]
    return f"run_{int(run_id)}_{nonce}"


@dataclass
class WorktreeExecutionOptions:
    project_path: str
    run_id: int
    demand_text: str
    report_markdown: str
    evidence_bundle: dict | None = None
    worktree_root: str = DEFAULT_WORKTREE_ROOT
    allowed_paths: list[str] = field(default_factory=list)
    verify_commands: list[str] = field(default_factory=list)
    max_edit_rounds: int = 2
    apply_to_project: bool = True
    cleanup_worktree: bool = True
    execution_nonce: str = ""
    worktree_run_key: str = ""


@dataclass
class WorktreeExecutionResult:
    status: str
    summary: str
    worktree_path: str = ""
    allowed_paths: list[str] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    final_diff: str = ""
    apply_to_project: dict = field(default_factory=dict)
    cleanup: dict = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)
    verification_status: str = "not_run"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = [
            "## v0.7.4 Worktree 受控改码结果",
            "",
            f"- 状态：{self.status}",
            f"- 结论：{self.summary}",
            f"- Worktree：{self.worktree_path or '-'}",
            f"- 允许修改路径：{', '.join(self.allowed_paths) if self.allowed_paths else '-'}",
            f"- Patch 尝试次数：{len(self.attempts)}",
            f"- 原业务目录合入：{(self.apply_to_project or {}).get('status') or 'not_run'}",
            f"- 临时目录清理：{(self.cleanup or {}).get('status') or 'not_run'}",
            f"- 验证状态：{self.verification_status}",
            "- 禁止动作：不提交、不推送、不发布、不写云效事务。",
            "",
            "### 尝试记录",
            "",
        ]
        if not self.attempts:
            lines.append("- 未进入 patch 尝试。")
        for attempt in self.attempts:
            side_effect_paths = attempt_side_effect_paths(attempt)
            lines.extend(
                [
                    f"- Attempt {attempt.get('attempt')}：{attempt.get('status')}",
                    f"  - 变更路径：{', '.join(attempt.get('changed_paths', [])) or '-'}",
                    f"  - 验证副作用：{', '.join(side_effect_paths) if side_effect_paths else '-'}",
                    f"  - 问题：{attempt.get('message') or '-'}",
                ]
            )
        if self.final_diff:
            lines.extend(
                [
                    "",
                    "### 最终 Diff 摘要",
                    "",
                    "```diff",
                    truncate_text(self.final_diff, 8000),
                    "```",
                ]
            )
        return "\n".join(lines)


class WorktreeCodeExecutor:
    def __init__(self, llm_client: BaseLLMClient) -> None:
        self.llm_client = llm_client

    def execute(self, options: WorktreeExecutionOptions) -> WorktreeExecutionResult:
        started_at = time.time()
        project_path = Path(options.project_path).expanduser().resolve()
        worktree_root = Path(options.worktree_root).expanduser().resolve()
        worktree_run_key = options.worktree_run_key or build_worktree_run_key(options.run_id, options.execution_nonce)
        worktree_path = worktree_root / worktree_run_key
        allowed_paths = determine_allowed_paths(options.allowed_paths, options.evidence_bundle)
        attempts: list[dict] = []
        manifest = {
            "run_id": options.run_id,
            "worktree_run_key": worktree_run_key,
            "project_path": str(project_path),
            "worktree_root": str(worktree_root),
            "worktree_path": str(worktree_path),
            "allowed_paths": allowed_paths,
            "verify_commands": options.verify_commands,
            "max_edit_rounds": options.max_edit_rounds,
            "apply_to_project": options.apply_to_project,
            "cleanup_worktree": options.cleanup_worktree,
            "started_at_epoch": started_at,
        }

        preflight_error = self._preflight(project_path=project_path, allowed_paths=allowed_paths, manifest=manifest)
        if not options.verify_commands:
            preflight_error = "not_run：未提供验证命令，禁止进入改码或声称验证通过。"
            manifest["verification_status"] = "not_run"
        if preflight_error:
            manifest["finished_at_epoch"] = time.time()
            manifest["preflight_error"] = preflight_error
            return WorktreeExecutionResult(
                status="failed",
                summary=preflight_error,
                worktree_path=str(worktree_path),
                allowed_paths=allowed_paths,
                attempts=attempts,
                verification_status="not_run" if not options.verify_commands else "failed",
                manifest=manifest,
            )

        setup_error = self._create_worktree(project_path=project_path, worktree_root=worktree_root, worktree_path=worktree_path)
        if setup_error:
            manifest["finished_at_epoch"] = time.time()
            manifest["setup_error"] = setup_error
            manifest["failure_code"] = "blocked_worktree_collision" if "已存在" in setup_error else "worktree_setup_failed"
            return WorktreeExecutionResult(
                status="failed",
                summary=setup_error,
                worktree_path=str(worktree_path),
                allowed_paths=allowed_paths,
                attempts=attempts,
                verification_status="failed",
                manifest=manifest,
            )
        dependency_links = prepare_dependency_links(project_path=project_path, worktree_path=worktree_path)
        manifest["dependency_links"] = dependency_links

        feedback = ""
        final_diff = ""
        final_status = "failed"
        final_summary = "Patch 未通过自动验证。"
        verification_status = "not_run"
        apply_to_project_result: dict = {"status": "not_run", "message": "Patch 未成功，未合入原业务目录。"}
        max_attempts = max(0, options.max_edit_rounds) + 1
        for attempt_index in range(max_attempts):
            if attempt_index > 0:
                self._reset_worktree(worktree_path)
                dependency_links.extend(prepare_dependency_links(project_path=project_path, worktree_path=worktree_path))
                manifest["dependency_links"] = dependency_links
            attempt_no = attempt_index + 1
            attempt: dict = {"attempt": attempt_no, "status": "running", "message": "", "changed_paths": []}
            patch_response = self._generate_patch(options=options, allowed_paths=allowed_paths, feedback=feedback, attempt=attempt_no, worktree_path=worktree_path)
            attempt["patch"] = patch_response["patch"]
            attempt["prompt_tokens"] = patch_response.get("prompt_tokens", 0)
            attempt["completion_tokens"] = patch_response.get("completion_tokens", 0)

            validation = validate_patch(patch_response["patch"], allowed_paths=allowed_paths)
            attempt["changed_paths"] = validation.changed_paths
            if not validation.ok:
                attempt["status"] = "rejected"
                attempt["message"] = validation.message
                attempts.append(attempt)
                feedback = build_feedback(attempt)
                continue

            apply_check = run_command(
                ["git", "apply", "--check", "--recount", "-"],
                cwd=worktree_path,
                input_text=patch_response["patch"],
                timeout=PATCH_TIMEOUT_SECONDS,
            )
            attempt["apply_check"] = apply_check
            if apply_check["returncode"] != 0:
                attempt["status"] = "apply_check_failed"
                attempt["message"] = "git apply --check 失败"
                attempts.append(attempt)
                feedback = build_feedback(attempt)
                continue

            apply_result = run_command(
                ["git", "apply", "--recount", "-"],
                cwd=worktree_path,
                input_text=patch_response["patch"],
                timeout=PATCH_TIMEOUT_SECONDS,
            )
            attempt["apply"] = apply_result
            if apply_result["returncode"] != 0:
                attempt["status"] = "apply_failed"
                attempt["message"] = "git apply 失败"
                attempts.append(attempt)
                feedback = build_feedback(attempt)
                continue

            diff_check = run_command(["git", "diff", "--check"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
            attempt["diff_check"] = diff_check
            if diff_check["returncode"] != 0:
                attempt["status"] = "diff_check_failed"
                attempt["message"] = "git diff --check 失败"
                attempts.append(attempt)
                feedback = build_feedback(attempt)
                continue

            pre_verify_snapshot = capture_worktree_snapshot(worktree_path)
            attempt["pre_verify_snapshot"] = pre_verify_snapshot
            attempt["pre_verify_diff"] = pre_verify_snapshot.get("diff", {}).get("stdout", "")
            verify_results = []
            verify_side_effect_failed = False
            for command in options.verify_commands:
                before_snapshot = capture_worktree_snapshot(worktree_path)
                verify_result = run_shell_command(command, cwd=worktree_path, timeout=VERIFY_TIMEOUT_SECONDS)
                after_snapshot = capture_worktree_snapshot(worktree_path)
                side_effect = build_side_effect_report(before_snapshot=before_snapshot, after_snapshot=after_snapshot, side="worktree")
                enriched_verify_result = {
                    **verify_result,
                    "before_snapshot": before_snapshot,
                    "after_snapshot": after_snapshot,
                    "side_effects": side_effect,
                }
                verify_results.append(enriched_verify_result)
                if side_effect["changed"]:
                    verify_side_effect_failed = True
            attempt["verify"] = verify_results
            command_missing = any(item.get("returncode") in {126, 127} for item in verify_results)
            if verify_side_effect_failed:
                verification_status = "side_effect_failed"
                attempt["status"] = "verify_side_effect_failed"
                attempt["message"] = "验证命令修改了临时 worktree，拒绝把副作用混入最终 diff"
                attempts.append(attempt)
                feedback = build_feedback(attempt)
                continue
            baseline_check = self._check_failed_verifications_against_baseline(
                worktree_path=worktree_path,
                candidate_diff=pre_verify_snapshot.get("diff", {}).get("stdout", ""),
                verify_results=verify_results,
            )
            attempt["baseline_verification"] = baseline_check
            if not baseline_check.get("restored"):
                verification_status = "failed"
                attempt["status"] = "baseline_restore_failed"
                attempt["message"] = "基线验证后未能恢复候选 patch，拒绝继续。"
                attempts.append(attempt)
                feedback = build_feedback(attempt)
                continue
            verify_failed = any(
                item.get("returncode") != 0 and not item.get("accepted_as_baseline")
                for item in verify_results
            )
            if verify_failed:
                verification_status = "tool_missing" if command_missing else "failed"
                attempt["status"] = "verify_failed"
                attempt["message"] = "至少一个验证命令失败"
                attempts.append(attempt)
                feedback = build_feedback(attempt)
                continue

            if any(item.get("accepted_as_baseline") for item in verify_results):
                verification_status = "baseline_failed"
            else:
                verification_status = "passed"

            diff_result = run_command(["git", "diff", "--no-ext-diff"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
            final_diff = diff_result["stdout"]
            attempt["final_diff_returncode"] = diff_result["returncode"]
            attempt["status"] = "success"
            attempt["verification_status"] = verification_status
            attempt["message"] = "Patch 已应用并通过验证"
            attempts.append(attempt)
            final_status = "success"
            final_summary = (
                "Patch 已应用，但验证命中既有基线失败；不可视为真实通过，需人工复核。"
                if verification_status == "baseline_failed"
                else "Patch 已在独立 worktree 中应用并通过验证，可进入人工代码审查。"
            )
            break

        if final_status == "success" and verification_status != "passed":
            final_status = "failed"
            final_summary = f"Patch 已保留在临时 worktree，但验证状态为 {verification_status}，未视为成功。"
        if final_status == "success":
            if options.apply_to_project and verification_status == "passed":
                apply_to_project_result = apply_final_diff_to_project(project_path=project_path, final_diff=final_diff)
                if apply_to_project_result.get("status") != "success":
                    final_status = "failed"
                    final_summary = apply_to_project_result.get("message") or "最终 diff 合入原业务目录失败。"
                else:
                    final_summary = "Patch 已在独立 worktree 中通过验证，并已合入原业务目录；未提交、未推送、未发布。"
            elif options.apply_to_project:
                apply_to_project_result = {
                    "status": "blocked",
                    "message": f"验证状态为 {verification_status}，只有 passed 才允许合入原业务目录。",
                }
                final_status = "failed"
                final_summary = f"Patch 已保留在临时 worktree，但验证状态为 {verification_status}，未合入原业务目录。"
            else:
                apply_to_project_result = {"status": "skipped", "message": "配置为不自动合入原业务目录，仅输出 final.diff。"}

        if final_status != "success" and attempts:
            # Keep the explicit baseline classification.  The successful patch
            # attempt message says that the patch itself applied, but it must
            # never overwrite the run-level warning that verification is not a
            # real pass because the same command already fails on HEAD.
            if verification_status != "baseline_failed":
                final_summary = attempts[-1].get("message") or final_summary
            if attempts[-1].get("status") == "verify_side_effect_failed":
                final_diff = attempts[-1].get("pre_verify_diff", "")
                manifest["final_diff_note"] = "最后一轮验证命令产生副作用，final_diff 只保留验证前 diff。"
            else:
                diff_result = run_command(["git", "diff", "--no-ext-diff"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
                final_diff = diff_result["stdout"]

        cleanup_result = {"status": "skipped", "message": "配置为保留临时 worktree。"}
        if options.cleanup_worktree:
            cleanup_result = cleanup_git_worktree(project_path=project_path, worktree_path=worktree_path)
        manifest["apply_to_project"] = apply_to_project_result
        manifest["cleanup"] = cleanup_result
        manifest["finished_at_epoch"] = time.time()
        manifest["status"] = final_status
        manifest["verification_status"] = verification_status
        manifest["summary"] = final_summary
        manifest["attempt_count"] = len(attempts)
        return WorktreeExecutionResult(
            status=final_status,
            summary=final_summary,
            worktree_path=str(worktree_path),
            allowed_paths=allowed_paths,
            attempts=attempts,
            final_diff=final_diff,
            apply_to_project=apply_to_project_result,
            cleanup=cleanup_result,
            manifest=manifest,
            verification_status=verification_status,
        )

    def _preflight(self, *, project_path: Path, allowed_paths: list[str], manifest: dict | None = None) -> str:
        if not project_path.exists():
            return f"项目路径不存在：{project_path}"
        if not project_path.is_dir():
            return f"项目路径不是目录：{project_path}"
        root_result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if root_result["returncode"] != 0:
            return "worktree 模式要求 project-path 是 Git 仓库"
        git_root = Path(root_result["stdout"].strip()).resolve()
        if git_root != project_path:
            return f"project-path 必须指向 Git 仓库根目录：当前根目录为 {git_root}"
        status_result = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if status_result["returncode"] != 0:
            return "无法读取原项目 git status"
        if not allowed_paths:
            return "worktree 模式缺少允许修改路径；请传 --allowed-path 或提供可命中的工程证据"
        for path in allowed_paths:
            safety = validate_relative_path(path)
            if safety:
                return safety
        dirty_paths = parse_status_paths(status_result["stdout"])
        normalized_allowed_paths = {normalize_relative_path(path) for path in allowed_paths}
        dirty_allowed_paths = [path for path in dirty_paths if path in normalized_allowed_paths]
        if dirty_allowed_paths:
            return "原项目的白名单文件存在未提交改动，拒绝创建受控 worktree：" + ", ".join(dirty_allowed_paths)
        if dirty_paths and manifest is not None:
            manifest["original_unrelated_dirty_paths"] = dirty_paths
            manifest["preflight_note"] = "原项目存在白名单外未提交改动；临时 worktree 从 HEAD 创建，不读取或修改这些改动。"
        return ""

    def _create_worktree(self, *, project_path: Path, worktree_root: Path, worktree_path: Path) -> str:
        worktree_root.mkdir(parents=True, exist_ok=True)
        run_command(["git", "worktree", "prune"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if worktree_path.exists():
            return f"目标 worktree 已存在，拒绝覆盖；请先使用安全清理预览处理：{worktree_path}"
        create_worktree_marker(
            worktree_root=worktree_root,
            worktree_path=worktree_path,
            project_path=project_path,
            run_id=worktree_path.name.removeprefix("run_"),
            role="patch",
        )
        result = run_command(["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if result["returncode"] != 0:
            remove_worktree_marker(worktree_root=worktree_root, worktree_path=worktree_path)
            return "创建 Git worktree 失败：" + summarize_command(result)
        return ""

    def _reset_worktree(self, worktree_path: Path) -> None:
        run_command(["git", "reset", "--hard", "HEAD"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
        run_command(["git", "clean", "-fd"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)

    def _generate_patch(
        self,
        *,
        options: WorktreeExecutionOptions,
        allowed_paths: list[str],
        feedback: str,
        attempt: int,
        worktree_path: Path,
    ) -> dict:
        if self.llm_client.is_mock:
            patch = build_mock_patch(worktree_path=worktree_path, allowed_paths=allowed_paths)
            return {"patch": patch, "prompt_tokens": 0, "completion_tokens": max(1, len(patch) // 4)}

        response = self.llm_client.complete(
            system_prompt=build_patch_system_prompt(),
            user_prompt=build_patch_user_prompt(
                options=options,
                allowed_paths=allowed_paths,
                source_context=build_allowed_file_context(worktree_path=worktree_path, allowed_paths=allowed_paths),
                feedback=feedback,
                attempt=attempt,
            ),
            step_key="code_patch",
            expert_name="开发执行补丁生成器",
        )
        return {
            "patch": extract_unified_diff(response.content),
            "raw_content": response.content,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }

    def _check_failed_verifications_against_baseline(
        self,
        *,
        worktree_path: Path,
        candidate_diff: str,
        verify_results: list[dict],
    ) -> dict:
        failed_items = [item for item in verify_results if item.get("returncode") != 0]
        if not failed_items:
            return {"checked": False, "restored": True, "results": []}

        reset_result = run_command(["git", "reset", "--hard", "HEAD"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
        if reset_result["returncode"] != 0:
            return {"checked": True, "restored": False, "reason": "无法重置到验证基线", "results": []}

        results: list[dict] = []
        for item in failed_items:
            baseline = run_shell_command(str(item.get("command") or ""), cwd=worktree_path, timeout=VERIFY_TIMEOUT_SECONDS)
            matches = verification_failure_matches_baseline(patched=item, baseline=baseline)
            item["baseline"] = baseline
            item["accepted_as_baseline"] = matches
            results.append({"command": item.get("command"), "matches": matches, "baseline": baseline})

        restore_result = run_command(
            ["git", "apply", "--recount", "-"],
            cwd=worktree_path,
            input_text=candidate_diff,
            timeout=PATCH_TIMEOUT_SECONDS,
        )
        restored = restore_result["returncode"] == 0
        return {
            "checked": True,
            "restored": restored,
            "restore_result": restore_result,
            "results": results,
        }


def attempt_side_effect_paths(attempt: dict) -> list[str]:
    paths: list[str] = []
    for verify in attempt.get("verify", []):
        side_effects = verify.get("side_effects") or {}
        if side_effects.get("changed"):
            paths.extend(side_effects.get("changed_paths", []))
    return unique_keep_order(paths)


def verification_failure_matches_baseline(*, patched: dict, baseline: dict) -> bool:
    """Accept an unchanged failing verifier only after reproducing it on the same HEAD."""
    return (
        patched.get("returncode") != 0
        and baseline.get("returncode") != 0
        and str(patched.get("stdout") or "") == str(baseline.get("stdout") or "")
        and str(patched.get("stderr") or "") == str(baseline.get("stderr") or "")
    )


@dataclass
class PatchValidationResult:
    ok: bool
    message: str
    changed_paths: list[str] = field(default_factory=list)


def determine_allowed_paths(cli_allowed_paths: list[str], evidence_bundle: dict | None) -> list[str]:
    if cli_allowed_paths:
        return unique_keep_order(normalize_relative_path(path) for path in cli_allowed_paths if path)
    paths: list[str] = []
    for item in (evidence_bundle or {}).get("evidence_files", []):
        path = item.get("path") if isinstance(item, dict) else ""
        if path:
            paths.append(normalize_relative_path(path))
    return unique_keep_order(paths)


def validate_patch(patch: str, *, allowed_paths: list[str], allow_file_additions: bool = False) -> PatchValidationResult:
    if not patch.strip():
        return PatchValidationResult(ok=False, message="模型未输出 unified diff")
    if "NO_PATCH:" in patch[:200]:
        return PatchValidationResult(ok=False, message=patch.strip().splitlines()[0][:300])
    if "Binary files" in patch or "GIT binary patch" in patch:
        return PatchValidationResult(ok=False, message="拒绝二进制 patch")
    paths = extract_patch_paths(patch)
    if not paths:
        return PatchValidationResult(ok=False, message="未能从 patch 中解析变更路径")
    allowed_set = set(allowed_paths)
    changed_paths = [path for path in paths if path != "/dev/null"]
    for path in paths:
        if path == "/dev/null":
            if not allow_file_additions:
                return PatchValidationResult(ok=False, message="v0.7 暂不允许新增或删除文件", changed_paths=paths)
            if patch_contains_file_deletion(patch):
                return PatchValidationResult(ok=False, message="precommit 暂不允许删除文件", changed_paths=paths)
            continue
        safety = validate_relative_path(path)
        if safety:
            return PatchValidationResult(ok=False, message=safety, changed_paths=paths)
        if path not in allowed_set:
            return PatchValidationResult(ok=False, message=f"Patch 修改了白名单外路径：{path}", changed_paths=paths)
    return PatchValidationResult(ok=True, message="patch 路径校验通过", changed_paths=changed_paths)


def patch_contains_file_deletion(patch: str) -> bool:
    for line in patch.splitlines():
        if line.startswith("+++ ") and line[4:].strip().split("\t")[0] == "/dev/null":
            return True
    return False


def extract_patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                paths.extend([strip_diff_prefix(parts[2]), strip_diff_prefix(parts[3])])
        elif line.startswith("--- ") or line.startswith("+++ "):
            value = line[4:].strip().split("\t")[0]
            if value != "/dev/null":
                paths.append(strip_diff_prefix(value))
            else:
                paths.append(value)
    normalized = []
    for path in paths:
        if path == "/dev/null":
            normalized.append(path)
        elif path:
            normalized.append(normalize_relative_path(path))
    return unique_keep_order(normalized)


def validate_relative_path(path: str) -> str:
    if not path:
        return "路径不能为空"
    if path == "/dev/null":
        return "v0.7 暂不允许新增或删除文件"
    normalized = normalize_relative_path(path)
    path_obj = Path(normalized)
    if path_obj.is_absolute() or ".." in path_obj.parts:
        return f"拒绝非安全相对路径：{path}"
    if any(part in PROHIBITED_PATH_PARTS for part in path_obj.parts):
        return f"拒绝修改版本控制目录：{path}"
    if path_obj.name in PROHIBITED_FILE_NAMES:
        return f"拒绝修改锁文件、密钥或环境文件：{path}"
    lower = normalized.lower()
    if any(lower.startswith(prefix) for prefix in PROHIBITED_PATH_PREFIXES):
        return f"拒绝修改受保护配置路径：{path}"
    return ""


def build_patch_system_prompt() -> str:
    return (
        "你是 HIS Harness 的受控代码补丁生成器。"
        "你只能输出 unified diff，不要输出解释、Markdown 或代码块。"
        "如果证据不足以安全修改，请只输出：NO_PATCH: 具体原因。"
        "必须遵循目标文件现有代码风格、命名和组件/接口用法。"
        "不得顺手格式化，不得修改与需求无关的代码。"
    )


def build_allowed_file_context(*, worktree_path: Path, allowed_paths: list[str]) -> str:
    remaining = 48_000
    sections: list[str] = []
    for relative_path in allowed_paths:
        source_path = worktree_path / relative_path
        if not source_path.is_file():
            sections.append(f"【当前源码缺失：{relative_path}】")
            continue
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            sections.append(f"【当前源码读取失败：{relative_path}】{exc}")
            continue
        if remaining <= 0:
            sections.append(f"【当前源码已截断：{relative_path}】")
            continue
        # A single allowlisted file is often the complete edit context; do not lose its middle.
        excerpt = truncate_text(source_text, remaining)
        sections.append(f"【当前源码：{relative_path}】\n{excerpt}")
        remaining -= len(excerpt)
    return "\n\n".join(sections)


def build_patch_user_prompt(
    *,
    options: WorktreeExecutionOptions,
    allowed_paths: list[str],
    source_context: str,
    feedback: str,
    attempt: int,
) -> str:
    evidence = json.dumps(options.evidence_bundle or {}, ensure_ascii=False)[:12000]
    return (
        f"【Attempt】{attempt}\n\n"
        f"【原始需求】\n{options.demand_text}\n\n"
        f"【允许修改路径】\n" + "\n".join(f"- {path}" for path in allowed_paths) + "\n\n"
        f"【白名单文件当前源码】\n{source_context}\n\n"
        f"【工程证据 JSON 摘要】\n{evidence}\n\n"
        f"【专家团报告】\n{truncate_text(options.report_markdown, 16000)}\n\n"
        f"【上一轮失败反馈】\n{feedback or '无'}\n\n"
        "【硬性约束】\n"
        "- 只输出 unified diff。\n"
        "- 只能修改允许路径中的文件。\n"
        "- 必须以提供的当前源码为准生成可直接 git apply 的完整 unified diff；不得编造不存在的方法、上下文或行号。\n"
        "- 只修复当前需求明确要求的问题；不要顺手修 lint、格式、空值或历史代码风格。\n"
        "- 不碰路由、API、相似页面、全局配置或上下游契约，除非它们在允许路径内且证据明确要求。\n"
        "- 如果无法确认业务语义、字段来源、目标页面或前后端边界，输出 NO_PATCH。\n"
        "- UI 改动必须优先观察并复用目标文件上下文中已经使用的组件、表格列配置、插槽、provide/inject 和自定义列机制。\n"
        "- 不新增文件、不删除文件、不修改锁文件、密钥、云效配置、CI/CD 或发布配置。\n"
        "- 不提交、不推送、不发布、不写云效事务。\n"
        "- 需求不清楚时输出 NO_PATCH，不要强行改。\n"
    )


def extract_unified_diff(content: str) -> str:
    text = content.strip()
    if text.startswith("NO_PATCH:"):
        return text
    fence = "```"
    if fence in text:
        blocks = text.split(fence)
        for block in blocks:
            candidate = block.strip()
            if candidate.startswith("diff --git") or candidate.startswith("--- "):
                if candidate.startswith("diff") or candidate.startswith("---"):
                    return ensure_patch_terminal_newline(candidate)
            if candidate.startswith("diff\n") or candidate.startswith("patch\n"):
                return ensure_patch_terminal_newline("\n".join(candidate.splitlines()[1:]))
    index = text.find("diff --git ")
    if index != -1:
        return ensure_patch_terminal_newline(text[index:])
    index = text.find("--- ")
    if index != -1:
        return ensure_patch_terminal_newline(text[index:])
    return text


def ensure_patch_terminal_newline(patch: str) -> str:
    normalized = patch.strip()
    return normalized + "\n" if normalized else ""


def build_mock_patch(*, worktree_path: Path, allowed_paths: list[str]) -> str:
    for relative in allowed_paths:
        path = worktree_path / relative
        if path.exists() and path.is_file():
            original = path.read_text(encoding="utf-8")
            marker = mock_marker_for_path(relative)
            if marker in original:
                updated = original
            else:
                updated = original.rstrip("\n") + "\n" + marker + "\n"
            original_lines = original.splitlines(keepends=True)
            updated_lines = updated.splitlines(keepends=True)
            return "".join(
                difflib.unified_diff(
                    original_lines,
                    updated_lines,
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
    return "NO_PATCH: mock 模式没有找到可修改的白名单文件"


def mock_marker_for_path(relative: str) -> str:
    suffix = Path(relative).suffix.lower()
    if suffix in {".js", ".ts", ".java"}:
        return "// HARNESS_WORKTREE_SELF_CHECK"
    if suffix == ".py":
        return "# HARNESS_WORKTREE_SELF_CHECK"
    if suffix in {".vue", ".html", ".xml"}:
        return "<!-- HARNESS_WORKTREE_SELF_CHECK -->"
    if suffix in {".css", ".scss", ".less"}:
        return "/* HARNESS_WORKTREE_SELF_CHECK */"
    return "# HARNESS_WORKTREE_SELF_CHECK"


def build_feedback(attempt: dict) -> str:
    parts = [
        f"Attempt {attempt.get('attempt')} 状态：{attempt.get('status')}",
        f"问题：{attempt.get('message') or '-'}",
    ]
    for key in ["apply_check", "apply", "diff_check"]:
        if key in attempt:
            parts.append(f"{key}: {summarize_command(attempt[key])}")
    for verify in attempt.get("verify", []):
        parts.append(f"verify `{verify.get('command')}`: {summarize_command(verify)}")
        side_effects = verify.get("side_effects") or {}
        if side_effects.get("changed"):
            parts.append(
                "verify side effects: "
                + ", ".join(side_effects.get("changed_paths", []))
                + "；请调整验证命令或单独处理格式化/构建产物。"
            )
    return "\n".join(parts)


def run_command(
    command: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    timeout: int,
    truncate_output: bool = True,
) -> dict:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "command": " ".join(command),
            "returncode": completed.returncode,
            "stdout": truncate_text(completed.stdout, MAX_LOG_CHARS) if truncate_output else completed.stdout,
            "stderr": truncate_text(completed.stderr, MAX_LOG_CHARS) if truncate_output else completed.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": " ".join(command), "returncode": 124, "stdout": "", "stderr": str(exc)}


def run_shell_command(command: str, *, cwd: Path, timeout: int) -> dict:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": truncate_text(completed.stdout, MAX_LOG_CHARS),
            "stderr": truncate_text(completed.stderr, MAX_LOG_CHARS),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "returncode": 124, "stdout": "", "stderr": str(exc)}


def prepare_dependency_links(*, project_path: Path, worktree_path: Path) -> list[dict]:
    records: list[dict] = []
    for name in DEPENDENCY_LINK_DIRS:
        source = project_path / name
        target = worktree_path / name
        if not source.exists():
            continue
        if target.exists() or target.is_symlink():
            records.append({"name": name, "status": "exists", "source": str(source), "target": str(target)})
            continue
        try:
            target.symlink_to(source, target_is_directory=source.is_dir())
            records.append({"name": name, "status": "linked", "source": str(source), "target": str(target)})
        except OSError as exc:
            records.append({"name": name, "status": "failed", "source": str(source), "target": str(target), "error": str(exc)})
    return records


def capture_worktree_snapshot(worktree_path: Path) -> dict:
    status = run_command(["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
    diff = run_command(["git", "diff", "--no-ext-diff"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
    return {
        "status": {
            "returncode": status.get("returncode"),
            "stdout": status.get("stdout", ""),
            "stderr": status.get("stderr", ""),
            "paths": parse_status_paths(status.get("stdout", "")),
        },
        "diff": {
            "returncode": diff.get("returncode"),
            "stdout": truncate_text(diff.get("stdout", ""), 12000),
            "stderr": diff.get("stderr", ""),
        },
    }


def capture_local_agent_tree_snapshot(worktree_path: Path) -> dict[str, dict[str, object]]:
    """Capture every non-Git worktree entry with no-follow identities.

    Local-agent verification treats any new ignored/untracked file, symlink,
    special file, or mode change as a side effect.  This is deliberately more
    strict than `git status`, which cannot see ignored generated files.
    """

    metadata = worktree_path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise OSError("worktree path is not a no-follow directory")
    root = worktree_path
    result: dict[str, dict[str, object]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in sorted([*directories, *files]):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                result[relative] = {"type": "symlink"}
            elif stat.S_ISDIR(metadata.st_mode):
                result[relative] = {"type": "directory", "mode": stat.S_IMODE(metadata.st_mode)}
            elif stat.S_ISREG(metadata.st_mode):
                result[relative] = {
                    "type": "file",
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size_bytes": metadata.st_size,
                }
            else:
                result[relative] = {"type": "special"}
    return result


def run_local_agent_verification_argv(command: tuple[str, ...], *, cwd: Path, timeout: int, source_path: Path | None = None) -> dict:
    """Run a validated argv in a group and never retain command/output text.

    macOS's sandbox-exec enforces the runtime boundary; on an unsupported host
    spawn fails closed.  Callers receive hashes and cleanup facts only.
    """

    if not isinstance(command, tuple) or not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("local_agent_verification_invalid")
    if not isinstance(cwd, Path) or cwd.is_symlink() or not cwd.is_dir() or not isinstance(timeout, int) or timeout < 1:
        raise ValueError("local_agent_verification_invalid")
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"}
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file() or sandbox.is_symlink():
        return {"returncode": 125, "timed_out": False, "cleanup": "not_needed", "duration_ms": 0, "stdout_sha256": hashlib.sha256(b"").hexdigest(), "stderr_sha256": hashlib.sha256(b"sandbox_unavailable").hexdigest()}
    # Verification is genuinely read-only.  Worker writes happen before this
    # boundary; validated commands receive no write permission to worktree,
    # source, linked-worktree admin or common.git.
    profile = '(version 1) (deny default) (allow process*) (allow file-read*) (deny file-write*) (deny network*)'
    process: subprocess.Popen[str] | None = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [os.fspath(sandbox), "-p", profile, *command], cwd=str(cwd), shell=False, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True, close_fds=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return {"returncode": process.returncode, "timed_out": False, "cleanup": "not_needed", "duration_ms": int((time.monotonic()-started)*1000), "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(), "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest()}
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            return {"returncode": 124, "timed_out": True, "cleanup": "terminated", "duration_ms": int((time.monotonic()-started)*1000), "stdout_sha256": hashlib.sha256((stdout or "").encode()).hexdigest(), "stderr_sha256": hashlib.sha256((stderr or "").encode()).hexdigest()}
    except OSError:
        return {"returncode": 125, "timed_out": False, "cleanup": "spawn_failed", "duration_ms": int((time.monotonic()-started)*1000), "stdout_sha256": hashlib.sha256(b"").hexdigest(), "stderr_sha256": hashlib.sha256(b"verification_spawn_failed").hexdigest()}


class _AnchoredLocalApplyTransaction:
    """Directory-fd anchored storage below one validated common Git dir."""

    def __init__(self, project_path: Path, *, expected_common_git_identity: tuple[int, int] | None = None) -> None:
        self.project_path = project_path
        common = run_command(
            ["git", "rev-parse", "--git-common-dir"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS,
        )
        raw = str(common.get("stdout") or "").strip()
        if common.get("returncode") != 0 or not raw or "\x00" in raw:
            raise OSError("local apply common Git dir unavailable")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = project_path / candidate
        self.common_git_path = Path(os.path.abspath(os.fspath(candidate)))
        self.common_fd = _open_absolute_directory_no_follow(self.common_git_path)
        common_item = os.fstat(self.common_fd)
        if expected_common_git_identity is not None and (common_item.st_dev, common_item.st_ino) != tuple(expected_common_git_identity):
            self.close()
            raise OSError("local apply common Git identity changed")
        self.root_path = self.common_git_path / "his-harness" / "local-apply"
        self.harness_fd: int | None = None
        self.root_fd: int | None = None
        self.application_fd: int | None = None
        self.application_id = ""
        self._open_existing_roots()

    def __enter__(self) -> "_AnchoredLocalApplyTransaction":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        for name in ("application_fd", "root_fd", "harness_fd", "common_fd"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                setattr(self, name, None)

    def _open_existing_roots(self) -> None:
        try:
            self.harness_fd = _open_child_directory(self.common_fd, "his-harness", create=False)
        except FileNotFoundError:
            return
        try:
            self.root_fd = _open_child_directory(self.harness_fd, "local-apply", create=False)
        except FileNotFoundError:
            return

    def assert_namespace_unchanged(self) -> None:
        """Ensure every pathname still names the directory held by our fd."""

        reopened = _open_absolute_directory_no_follow(self.common_git_path)
        try:
            if _directory_identity(reopened) != _directory_identity(self.common_fd):
                raise OSError("local apply common Git directory replaced")
        finally:
            os.close(reopened)
        parent_fd = self.common_fd
        for leaf, descriptor in (
            ("his-harness", self.harness_fd),
            ("local-apply", self.root_fd),
            (self.application_id, self.application_fd),
        ):
            if descriptor is None:
                break
            try:
                item = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as error:
                raise OSError("local apply directory namespace changed") from error
            if (
                not stat.S_ISDIR(item.st_mode)
                or (item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)) != _directory_identity(descriptor)
            ):
                raise OSError("local apply directory namespace changed")
            parent_fd = descriptor
    def ensure_roots(self) -> None:
        if self.harness_fd is None:
            self.harness_fd = _open_child_directory(self.common_fd, "his-harness", create=True)
        if self.root_fd is None:
            self.root_fd = _open_child_directory(self.harness_fd, "local-apply", create=True)

    def open_application(self, application_id: str, *, create: bool) -> bool:
        if len(application_id) != 24 or any(character not in "0123456789abcdef" for character in application_id):
            raise OSError("local apply application id invalid")
        if self.application_fd is not None:
            os.close(self.application_fd)
            self.application_fd = None
        self.application_id = application_id
        if self.root_fd is None:
            if not create:
                return False
            self.ensure_roots()
        try:
            self.application_fd = _open_child_directory(self.root_fd, application_id, create=create)
        except FileNotFoundError:
            return False
        return True

    def path(self, leaf: str) -> Path:
        if not self.application_id:
            raise OSError("local apply application is not selected")
        return self.root_path / self.application_id / leaf

    def read_bytes(self, leaf: str, *, maximum: int = 1 << 24) -> bytes | None:
        if self.application_fd is None:
            return None
        try:
            return _read_regular_at(self.application_fd, leaf, maximum=maximum)
        except FileNotFoundError:
            return None

    def read_json(self, leaf: str = "journal.json") -> dict:
        try:
            raw = self.read_bytes(leaf)
            if raw is None:
                return {}
            payload = json.loads(raw.decode("utf-8", "strict"))
            if not isinstance(payload, dict):
                raise ValueError("journal root must be a JSON object")
            return payload
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            return {"read_error": str(error)}

    def write_once(self, leaf: str, content: bytes) -> None:
        if self.application_fd is None:
            raise OSError("local apply application is not open")
        self.assert_namespace_unchanged()
        _write_regular_once_at(self.application_fd, leaf, content)

    def write_atomic(self, leaf: str, content: bytes) -> None:
        if self.application_fd is None:
            raise OSError("local apply application is not open")
        self.assert_namespace_unchanged()
        _write_regular_atomic_at(self.application_fd, leaf, content)

    def archive_json(self, content: bytes) -> Path:
        if self.application_fd is None:
            raise OSError("local apply application is not open")
        self.assert_namespace_unchanged()
        history_fd = _open_child_directory(self.application_fd, "history", create=True)
        try:
            leaf = f"{time.time_ns()}-applied.json"
            _write_regular_once_at(history_fd, leaf, content)
        finally:
            os.close(history_fd)
        return self.path("history") / leaf


def _directory_identity(descriptor: int) -> tuple[int, int, int]:
    item = os.fstat(descriptor)
    if not stat.S_ISDIR(item.st_mode) or item.st_nlink < 2:
        raise OSError("unsafe directory identity")
    return item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)


def _open_absolute_directory_no_follow(path: Path) -> int:
    if not path.is_absolute():
        raise OSError("absolute directory required")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            item = os.fstat(child)
            if not stat.S_ISDIR(item.st_mode) or item.st_nlink < 2:
                os.close(child)
                raise OSError("unsafe directory identity")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_child_directory(parent_fd: int, leaf: str, *, create: bool) -> int:
    if not leaf or "/" in leaf or leaf in {".", ".."}:
        raise OSError("unsafe directory leaf")
    if create:
        try:
            os.mkdir(leaf, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
    descriptor = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    item = os.fstat(descriptor)
    if not stat.S_ISDIR(item.st_mode) or item.st_nlink < 2:
        os.close(descriptor)
        raise OSError("unsafe directory identity")
    return descriptor


def _read_regular_at(parent_fd: int, leaf: str, *, maximum: int) -> bytes:
    descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        item = os.fstat(descriptor)
        if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or item.st_size > maximum:
            raise OSError("unsafe local apply evidence file")
        chunks: list[bytes] = []
        remaining = item.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise OSError("short local apply evidence read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError("local apply evidence grew while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_regular_once_at(parent_fd: int, leaf: str, content: bytes) -> None:
    descriptor = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=parent_fd)
    try:
        view = memoryview(content)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(parent_fd)


def _write_regular_atomic_at(parent_fd: int, leaf: str, content: bytes) -> None:
    try:
        existing = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
        raise OSError("unsafe local apply evidence target")
    temporary = f".{leaf}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        _write_regular_once_at(parent_fd, temporary, content)
        os.replace(temporary, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except Exception:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass
        raise


def apply_final_diff_to_project(
    *,
    project_path: Path,
    final_diff: str,
    allow_file_changes: bool = False,
    expected_common_git_identity: tuple[int, int] | None = None,
    application_id: str | None = None,
) -> dict:
    project_path = project_path.expanduser().resolve()
    result = {
        "status": "not_run",
        "message": "",
        "project_path": str(project_path),
        "application_id": "",
        "idempotent": False,
        "transaction": {},
        "recovery": {"status": "not_run"},
        "pre_apply_status": {},
        "apply_check": {},
        "apply": {},
        "diff_check": {},
        "post_apply_status": {},
        "changed_paths": [],
        "unrelated_dirty_paths": [],
    }
    if not final_diff.strip():
        result["status"] = "failed"
        result["message"] = "final.diff 为空，拒绝合入原业务目录。"
        return result

    raw_paths = extract_patch_paths(final_diff)
    unsafe_paths = [
        path for path in raw_paths
        if not (allow_file_changes and path == "/dev/null") and validate_relative_path(path)
    ]
    if unsafe_paths:
        result["status"] = "failed"
        result["message"] = "final.diff 包含不安全、新增或删除路径，拒绝合入：" + ", ".join(unsafe_paths)
        return result
    changed_paths = [path for path in raw_paths if path != "/dev/null"]
    if not changed_paths:
        result["status"] = "failed"
        result["message"] = "无法从 final.diff 解析安全目标路径，拒绝合入。"
        return result
    result["changed_paths"] = changed_paths

    try:
        transaction = _AnchoredLocalApplyTransaction(
            project_path, expected_common_git_identity=expected_common_git_identity,
        )
    except OSError as error:
        result["status"] = "failed"
        result["message"] = f"本地应用事务目录不安全，拒绝合入：{error}"
        return result

    def finish() -> dict:
        transaction.close()
        return result

    patch_hash = hashlib.sha256(final_diff.encode("utf-8")).hexdigest()
    if application_id is None:
        application_id = build_local_apply_application_id(project_path=project_path, patch_hash=patch_hash)
    elif len(application_id) != 24 or any(character not in "0123456789abcdef" for character in application_id):
        result["status"] = "failed"
        result["message"] = "本地应用 operation id 无效。"
        return finish()
    try:
        transaction.open_application(application_id, create=False)
    except OSError as error:
        result["status"] = "failed"
        result["message"] = f"本地应用事务目录不安全，拒绝合入：{error}"
        return finish()
    journal_path = transaction.path("journal.json")
    patch_path = transaction.path("final.diff")
    result["application_id"] = application_id
    result["transaction"] = {
        "state": "not_started",
        "journal_path": str(journal_path),
        "patch_path": str(patch_path),
        "patch_hash": f"sha256:{patch_hash}",
    }

    existing_journal = transaction.read_json()
    if existing_journal.get("read_error"):
        result["status"] = "recovery_required"
        result["transaction"]["state"] = "recovery_required"
        result["message"] = "本地应用事务 journal 无法读取，拒绝覆盖，需要人工恢复。"
        result["recovery"] = {
            "status": "required",
            "reason": existing_journal["read_error"],
        }
        return finish()
    if existing_journal:
        existing_state = str(existing_journal.get("state") or "")
        if existing_state == "applied":
            current_states = capture_target_file_states(project_path, changed_paths)
            if current_states == existing_journal.get("post_file_states"):
                current_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
                result["status"] = "success"
                result["idempotent"] = True
                result["transaction"]["state"] = "already_applied"
                result["post_apply_status"] = current_status
                result["unrelated_dirty_paths"] = [
                    path for path in parse_status_paths(current_status.get("stdout", "")) if path not in set(changed_paths)
                ]
                result["message"] = "相同 final.diff 已成功应用且目标文件哈希一致，本次幂等返回；未重复写文件。"
                return finish()
            current_status = run_command(
                ["git", "status", "--porcelain"],
                cwd=project_path,
                timeout=PATCH_TIMEOUT_SECONDS,
            )
            restored_to_pre_state = (
                current_states == existing_journal.get("pre_file_states")
                and current_status.get("returncode") == 0
                and current_status.get("stdout", "")
                == (existing_journal.get("pre_apply_status") or {}).get("stdout", "")
            )
            if restored_to_pre_state:
                try:
                    archived_journal_path = archive_completed_local_apply_journal(
                        journal_path=journal_path,
                        journal=existing_journal,
                        reason="externally_reverted_to_exact_pre_state",
                        transaction=transaction,
                    )
                except OSError as exc:
                    result["status"] = "recovery_required"
                    result["transaction"]["state"] = "recovery_required"
                    result["message"] = "目标已恢复到事务前状态，但旧 journal 归档失败，拒绝覆盖审计证据。"
                    result["recovery"] = {"status": "required", "reason": str(exc)}
                    return finish()
                result["recovery"] = {
                    "status": "externally_reverted",
                    "archived_journal_path": str(archived_journal_path),
                }
            else:
                result["status"] = "recovery_required"
                result["transaction"]["state"] = "recovery_required"
                result["message"] = "已成功应用的事务记录与当前目标文件哈希不一致，拒绝重复应用。"
                result["recovery"] = {
                    "status": "required",
                    "reason": "applied journal post_file_states mismatch",
                }
                return finish()
        if existing_state in {"prepared", "applying", "post_check_failed", "rolling_back", "recovery_required"}:
            reconciliation = reconcile_local_apply_transactions(
                project_path,
                application_ids=[application_id],
                expected_common_git_identity=expected_common_git_identity,
            )
            result["reconciliation"] = reconciliation
            reconciled_journal = transaction.read_json()
            reconciled_state = str(reconciled_journal.get("state") or "")
            if reconciled_state not in {"rolled_back", "cancelled_not_applied", "failed_check", "failed_apply"}:
                result["status"] = "recovery_required"
                result["transaction"]["state"] = "recovery_required"
                result["message"] = f"发现未完成的本地应用事务 state={existing_state}，自动恢复未闭合。"
                result["recovery"] = {
                    "status": "required",
                    "reason": "incomplete transaction reconciliation failed",
                }
                return finish()

    pre_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    result["pre_apply_status"] = pre_status
    if pre_status["returncode"] != 0:
        result["status"] = "failed"
        result["message"] = "无法读取原业务目录 git status，拒绝合入。"
        return finish()
    dirty_paths = parse_status_paths(pre_status.get("stdout", ""))
    dirty_target_paths = [path for path in dirty_paths if path in set(changed_paths)]
    if dirty_target_paths:
        result["status"] = "failed"
        result["changed_paths"] = changed_paths
        result["message"] = "原业务目录的目标文件存在未提交改动，拒绝合入 final.diff：" + ", ".join(dirty_target_paths)
        return finish()
    result["unrelated_dirty_paths"] = [path for path in dirty_paths if path not in set(changed_paths)]

    try:
        pre_file_states = capture_target_file_states(project_path, changed_paths)
        transaction.open_application(application_id, create=True)
        existing_patch = transaction.read_bytes("final.diff")
        if existing_patch is None:
            transaction.write_once("final.diff", final_diff.encode("utf-8"))
        elif existing_patch != final_diff.encode("utf-8"):
            raise OSError("local apply patch evidence mismatch")
    except OSError as exc:
        result["status"] = "failed"
        result["message"] = f"无法准备本地应用事务证据：{exc}"
        return finish()

    journal = {
        "schema_version": "1.0-local-apply-transaction",
        "application_id": application_id,
        "project_path": str(project_path),
        "patch_hash": f"sha256:{patch_hash}",
        "patch_path": str(patch_path),
        "changed_paths": changed_paths,
        "unrelated_dirty_paths": result["unrelated_dirty_paths"],
        "pre_apply_status": pre_status,
        "pre_file_states": pre_file_states,
        "state": "prepared",
        "created_at_epoch": time.time(),
        "updated_at_epoch": time.time(),
    }
    if result["recovery"].get("status") == "externally_reverted":
        journal["previous_applied_transaction"] = result["recovery"]
    persist_local_apply_journal(journal_path, journal, state="prepared", transaction=transaction)
    result["transaction"]["state"] = "prepared"

    apply_check = run_command(["git", "apply", "--check", "-"], cwd=project_path, input_text=final_diff, timeout=PATCH_TIMEOUT_SECONDS)
    result["apply_check"] = apply_check
    if apply_check["returncode"] != 0:
        journal["apply_check"] = apply_check
        persist_local_apply_journal(journal_path, journal, state="failed_check", transaction=transaction)
        result["transaction"]["state"] = "failed_check"
        result["status"] = "failed"
        result["message"] = "原业务目录 git apply --check final.diff 失败。"
        return finish()

    journal["apply_check"] = apply_check
    persist_local_apply_journal(journal_path, journal, state="applying", transaction=transaction)
    result["transaction"]["state"] = "applying"
    try:
        transaction.assert_namespace_unchanged()
    except OSError as exc:
        result["status"] = "recovery_required"
        result["transaction"]["state"] = "recovery_required"
        result["message"] = f"本地应用事务目录在源文件写入前发生变化：{exc}"
        return finish()
    apply_result = run_command(["git", "apply", "-"], cwd=project_path, input_text=final_diff, timeout=PATCH_TIMEOUT_SECONDS)
    result["apply"] = apply_result
    if apply_result["returncode"] != 0:
        journal["apply"] = apply_result
        current_states = capture_target_file_states(project_path, changed_paths)
        current_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if current_states != pre_file_states or current_status.get("stdout", "") != pre_status.get("stdout", ""):
            recover_failed_local_apply(
                result=result,
                journal=journal,
                journal_path=journal_path,
                project_path=project_path,
                final_diff=final_diff,
                changed_paths=changed_paths,
                pre_file_states=pre_file_states,
                pre_status=pre_status,
                failure_message="git apply 返回失败且检测到工作区状态变化。",
                transaction=transaction,
            )
            return finish()
        persist_local_apply_journal(journal_path, journal, state="failed_apply", transaction=transaction)
        result["transaction"]["state"] = "failed_apply"
        result["status"] = "failed"
        result["message"] = "原业务目录 git apply final.diff 失败。"
        return finish()

    journal["apply"] = apply_result
    journal["post_file_states"] = capture_target_file_states(project_path, changed_paths)
    diff_check = run_command(
        ["git", "diff", "--check", "--", *changed_paths],
        cwd=project_path,
        timeout=PATCH_TIMEOUT_SECONDS,
    )
    result["diff_check"] = diff_check
    if diff_check["returncode"] != 0:
        journal["diff_check"] = diff_check
        persist_local_apply_journal(journal_path, journal, state="post_check_failed", transaction=transaction)
        result["transaction"]["state"] = "post_check_failed"
        recover_failed_local_apply(
            result=result,
            journal=journal,
            journal_path=journal_path,
            project_path=project_path,
            final_diff=final_diff,
            changed_paths=changed_paths,
            pre_file_states=pre_file_states,
            pre_status=pre_status,
            failure_message="final.diff 已应用，但原业务目录 git diff --check 失败。",
            transaction=transaction,
        )
        return finish()

    post_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    result["post_apply_status"] = post_status
    journal["diff_check"] = diff_check
    journal["post_apply_status"] = post_status
    journal["post_file_states"] = capture_target_file_states(project_path, changed_paths)
    persist_local_apply_journal(journal_path, journal, state="applied", transaction=transaction)
    result["transaction"]["state"] = "applied"
    result["status"] = "success"
    result["message"] = "final.diff 已合入原业务目录；未提交、未推送、未发布。"
    return finish()


def rebuild_local_apply_evidence_for_applied_source(
    *,
    project_path: Path,
    final_diff: str,
    application_id: str,
    expected_common_git_identity: tuple[int, int],
    pre_file_states: Mapping[str, object],
    pre_status: Mapping[str, object],
    expected_post_file_states: Mapping[str, object],
) -> dict[str, object]:
    """Rebuild durable evidence without performing a second source write."""

    project_path = project_path.expanduser().resolve()
    changed_paths = [path for path in extract_patch_paths(final_diff) if path != "/dev/null"]
    patch_bytes = final_diff.encode("utf-8")
    patch_hash = hashlib.sha256(patch_bytes).hexdigest()
    if (
        len(application_id) != 24
        or any(character not in "0123456789abcdef" for character in application_id)
        or not changed_paths
        or any(validate_relative_path(path) for path in extract_patch_paths(final_diff) if path != "/dev/null")
        or capture_target_file_states(project_path, changed_paths) != dict(expected_post_file_states)
    ):
        raise OSError("local apply recovery source facts mismatch")
    with _AnchoredLocalApplyTransaction(
        project_path, expected_common_git_identity=expected_common_git_identity,
    ) as transaction:
        if transaction.open_application(application_id, create=False):
            journal = transaction.read_json()
            patch = transaction.read_bytes("final.diff")
            if (
                journal.get("state") == "applied"
                and journal.get("patch_hash") == "sha256:" + patch_hash
                and patch == patch_bytes
            ):
                return {
                    "status": "success", "idempotent": True,
                    "application_id": application_id,
                    "transaction": {"state": "already_applied"},
                    "recovery": {"status": "rebuilt"},
                }
            raise OSError("local apply recovery evidence path already occupied")
        transaction.open_application(application_id, create=True)
        transaction.write_once("final.diff", patch_bytes)
        journal_path = transaction.path("journal.json")
        journal = {
            "schema_version": "1.0-local-apply-transaction",
            "application_id": application_id,
            "project_path": str(project_path),
            "patch_hash": "sha256:" + patch_hash,
            "patch_path": str(transaction.path("final.diff")),
            "changed_paths": changed_paths,
            "unrelated_dirty_paths": [],
            "pre_apply_status": dict(pre_status),
            "pre_file_states": dict(pre_file_states),
            "post_file_states": dict(expected_post_file_states),
            "post_apply_status": run_command(
                ["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS,
            ),
            "reconstructed_from_durable_operation": True,
            "created_at_epoch": time.time(),
            "updated_at_epoch": time.time(),
        }
        persist_local_apply_journal(journal_path, journal, state="applied", transaction=transaction)
        transaction.assert_namespace_unchanged()
        return {
            "status": "success", "idempotent": True,
            "application_id": application_id,
            "transaction": {"state": "applied"},
            "recovery": {"status": "rebuilt"},
        }


def resolve_local_apply_transaction_root(project_path: Path) -> dict:
    try:
        with _AnchoredLocalApplyTransaction(project_path.expanduser().resolve()) as transaction:
            return {"status": "success", "path": str(transaction.root_path)}
    except OSError as error:
        return {
            "status": "failed",
            "message": f"Git 内部事务目录不安全，拒绝合入原业务目录：{error}",
        }


def read_local_apply_transaction_evidence(
    *,
    project_path: Path,
    application_id: str,
    expected_common_git_identity: tuple[int, int],
) -> dict[str, object]:
    """Reopen one terminal apply journal through the anchored common.git tree."""

    with _AnchoredLocalApplyTransaction(
        project_path.expanduser().resolve(),
        expected_common_git_identity=expected_common_git_identity,
    ) as transaction:
        if not transaction.open_application(application_id, create=False):
            raise ValueError("local_apply_evidence_invalid")
        patch = transaction.read_bytes("final.diff")
        journal_bytes = transaction.read_bytes("journal.json")
        if patch is None or journal_bytes is None:
            raise ValueError("local_apply_evidence_invalid")
        try:
            journal = json.loads(journal_bytes.decode("utf-8", "strict"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise ValueError("local_apply_evidence_invalid") from None
        patch_hash = hashlib.sha256(patch).hexdigest()
        if (
            not isinstance(journal, dict)
            or journal.get("schema_version") != "1.0-local-apply-transaction"
            or journal.get("application_id") != application_id
            or journal.get("project_path") != str(project_path.expanduser().resolve())
            or journal.get("patch_hash") != "sha256:" + patch_hash
            or journal.get("patch_path") != str(transaction.path("final.diff"))
            or journal.get("state") != "applied"
        ):
            raise ValueError("local_apply_evidence_invalid")
        identities = []
        for descriptor in (
            transaction.common_fd, transaction.harness_fd,
            transaction.root_fd, transaction.application_fd,
        ):
            if descriptor is None:
                raise ValueError("local_apply_evidence_invalid")
            item = os.fstat(descriptor)
            identities.append([item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)])
        for leaf in ("final.diff", "journal.json"):
            item = os.stat(leaf, dir_fd=transaction.application_fd, follow_symlinks=False)
            if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
                raise ValueError("local_apply_evidence_invalid")
            identities.append([item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode), item.st_nlink])
        return {
            "application_id": application_id,
            "journal_state": "applied",
            "journal_path": str(transaction.path("journal.json")),
            "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
            "journal_size_bytes": len(journal_bytes),
            "patch_path": str(transaction.path("final.diff")),
            "patch_sha256": patch_hash,
            "patch_size_bytes": len(patch),
            "identity_sha256": hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest(),
        }


def reconcile_local_apply_transactions(
    project_path: Path,
    *,
    application_ids: list[str] | None = None,
    expected_common_git_identity: tuple[int, int] | None = None,
) -> dict:
    project_path = project_path.expanduser().resolve()
    summary = {
        "status": "success",
        "project_path": str(project_path),
        "scanned_count": 0,
        "recovered_count": 0,
        "cancelled_count": 0,
        "unchanged_count": 0,
        "recovery_required_count": 0,
        "transactions": [],
    }
    try:
        transaction = _AnchoredLocalApplyTransaction(
            project_path, expected_common_git_identity=expected_common_git_identity,
        )
    except OSError as error:
        summary["status"] = "failed"
        summary["error"] = f"Git 内部事务目录不安全：{error}"
        return summary
    try:
        if transaction.root_fd is None:
            return summary
        selected_ids = set(application_ids or [])
        names = sorted(selected_ids or set(os.listdir(transaction.root_fd)))
        for application_id in names:
            if len(application_id) != 24 or any(character not in "0123456789abcdef" for character in application_id):
                if selected_ids:
                    summary["status"] = "recovery_required"
                    summary["recovery_required_count"] += 1
                continue
            journal_path = transaction.root_path / application_id / "journal.json"
            try:
                if not transaction.open_application(application_id, create=False):
                    continue
                journal = transaction.read_json()
            except OSError as error:
                summary["scanned_count"] += 1
                summary["recovery_required_count"] += 1
                summary["transactions"].append({
                    "application_id": application_id,
                    "before_state": "",
                    "journal_path": str(journal_path),
                    "status": "recovery_required",
                    "reason": str(error),
                })
                continue
            if not journal:
                continue
            summary["scanned_count"] += 1
            state = str(journal.get("state") or "")
            record = {"application_id": application_id, "before_state": state, "journal_path": str(journal_path)}
            if journal.get("read_error"):
                record.update({"status": "recovery_required", "reason": journal["read_error"]})
                summary["recovery_required_count"] += 1
                summary["transactions"].append(record)
                continue
            if str(journal.get("project_path") or "") != str(project_path):
                record.update({"status": "recovery_required", "reason": "journal project_path mismatch"})
                summary["recovery_required_count"] += 1
                summary["transactions"].append(record)
                continue
            if str(journal.get("patch_path") or "") != str(transaction.path("final.diff")):
                record.update({"status": "recovery_required", "reason": "journal patch_path mismatch"})
                summary["recovery_required_count"] += 1
                summary["transactions"].append(record)
                continue
            if state in {"applied", "rolled_back", "cancelled_not_applied", "failed_check", "failed_apply"}:
                record["status"] = "unchanged"
                summary["unchanged_count"] += 1
                summary["transactions"].append(record)
                continue
            changed_paths = [str(path) for path in journal.get("changed_paths") or []]
            pre_file_states = journal.get("pre_file_states") or {}
            pre_status = journal.get("pre_apply_status") or {}
            try:
                transaction.assert_namespace_unchanged()
                current_states = capture_target_file_states(project_path, changed_paths)
            except OSError as exc:
                record.update({"status": "recovery_required", "reason": str(exc)})
                summary["recovery_required_count"] += 1
                summary["transactions"].append(record)
                continue
            current_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
            if current_states == pre_file_states and current_status.get("stdout", "") == pre_status.get("stdout", ""):
                journal["reconciliation"] = {"status": "cancelled_not_applied", "at_epoch": time.time()}
                persist_local_apply_journal(journal_path, journal, state="cancelled_not_applied", transaction=transaction)
                record["status"] = "cancelled_not_applied"
                summary["cancelled_count"] += 1
                summary["transactions"].append(record)
                continue
            try:
                patch_bytes = transaction.read_bytes("final.diff")
                if patch_bytes is None:
                    raise OSError("transaction patch missing")
                final_diff = patch_bytes.decode("utf-8", "strict")
            except (OSError, UnicodeError) as error:
                record.update({"status": "recovery_required", "reason": str(error)})
                summary["recovery_required_count"] += 1
                summary["transactions"].append(record)
                continue
            expected_hash = str(journal.get("patch_hash") or "").removeprefix("sha256:")
            actual_hash = hashlib.sha256(patch_bytes).hexdigest()
            if not expected_hash or expected_hash != actual_hash:
                record.update({"status": "recovery_required", "reason": "transaction patch hash mismatch"})
                summary["recovery_required_count"] += 1
                summary["transactions"].append(record)
                continue
            recovery_result = recover_failed_local_apply(
                result={
                    "status": "recovery_required", "message": "",
                    "transaction": {"state": state, "journal_path": str(journal_path), "patch_path": str(transaction.path("final.diff"))},
                    "recovery": {"status": "not_run"},
                },
                journal=journal, journal_path=journal_path, project_path=project_path,
                final_diff=final_diff, changed_paths=changed_paths,
                pre_file_states=pre_file_states, pre_status=pre_status,
                failure_message=f"恢复中断事务 state={state}。", transaction=transaction,
            )
            record["status"] = recovery_result["status"]
            if recovery_result["status"] == "rolled_back":
                summary["recovered_count"] += 1
            else:
                summary["recovery_required_count"] += 1
            summary["transactions"].append(record)
        if summary["recovery_required_count"]:
            summary["status"] = "recovery_required"
        return summary
    finally:
        transaction.close()


def build_local_apply_application_id(*, project_path: Path, patch_hash: str) -> str:
    payload = f"{project_path.resolve()}\0{patch_hash}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def rollback_applied_diff_in_project(
    *,
    project_path: Path,
    final_diff: str,
    expected_post_file_states: dict[str, dict],
    rollback_id: str,
    verify_commands: list[str] | None = None,
) -> dict:
    project_path = project_path.expanduser().resolve()
    paths = [path for path in extract_patch_paths(final_diff) if path != "/dev/null"]
    result = {
        "status": "not_run",
        "message": "",
        "rollback_id": rollback_id,
        "idempotent": False,
        "project_path": str(project_path),
        "changed_paths": paths,
        "unrelated_dirty_paths": [],
        "transaction": {},
        "recovery": {"status": "not_run"},
        "verification": [],
    }
    if not final_diff.strip() or not paths or any(validate_relative_path(path) for path in extract_patch_paths(final_diff)):
        result.update({"status": "failed", "message": "回滚 diff 为空或包含不安全、新增、删除路径。"})
        return result
    if set(expected_post_file_states) != set(paths):
        result.update({"status": "blocked_missing_baseline", "message": "登记的目标文件哈希与 diff 路径不完整，拒绝实际回滚。"})
        return result
    root = run_command(["git", "rev-parse", "--git-path", "his-harness/local-rollback"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    if root["returncode"] != 0 or not root.get("stdout", "").strip():
        result.update({"status": "failed", "message": "无法解析本地回滚事务目录。"})
        return result
    transaction_root = Path(root["stdout"].strip())
    if not transaction_root.is_absolute():
        transaction_root = project_path / transaction_root
    patch_hash = hashlib.sha256(final_diff.encode("utf-8")).hexdigest()
    transaction_id = hashlib.sha256(f"{project_path}\0{rollback_id}\0{patch_hash}".encode("utf-8")).hexdigest()[:24]
    transaction_dir = transaction_root.resolve() / transaction_id
    journal_path = transaction_dir / "journal.json"
    patch_path = transaction_dir / "source.diff"
    result["transaction"] = {
        "id": transaction_id,
        "state": "not_started",
        "journal_path": str(journal_path),
        "patch_path": str(patch_path),
    }
    existing = read_json_file(journal_path)
    if existing.get("state") == "rolled_back":
        current = capture_target_file_states(project_path, paths)
        if current == existing.get("rollback_post_file_states"):
            result.update({"status": "success", "idempotent": True, "message": "该修改记录已完成本地回滚，本次幂等返回。"})
            result["transaction"]["state"] = "already_rolled_back"
            return result
        result.update({"status": "blocked_target_drift", "message": "历史回滚记录与当前目标文件哈希不一致。"})
        result["transaction"]["state"] = "target_drift"
        return result
    current_states = capture_target_file_states(project_path, paths)
    if current_states != expected_post_file_states:
        result.update({"status": "blocked_target_drift", "message": "目标文件在修改历史登记后又发生变化，拒绝覆盖用户后续改动。"})
        result["transaction"]["state"] = "target_drift"
        return result
    pre_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    if pre_status["returncode"] != 0:
        result.update({"status": "failed", "message": "无法读取业务仓库状态。"})
        return result
    result["unrelated_dirty_paths"] = [path for path in parse_status_paths(pre_status.get("stdout", "")) if path not in set(paths)]
    atomic_write_text(patch_path, final_diff)
    journal = {
        "schema_version": "1.0-local-rollback-transaction",
        "rollback_id": rollback_id,
        "project_path": str(project_path),
        "patch_hash": "sha256:" + patch_hash,
        "patch_path": str(patch_path),
        "changed_paths": paths,
        "pre_file_states": current_states,
        "pre_status": pre_status,
    }
    persist_local_apply_journal(journal_path, journal, state="prepared")
    result["transaction"]["state"] = "prepared"
    reverse_check = run_command(["git", "apply", "--reverse", "--check", "-"], cwd=project_path, input_text=final_diff, timeout=PATCH_TIMEOUT_SECONDS)
    result["reverse_check"] = reverse_check
    if reverse_check["returncode"] != 0:
        journal["reverse_check"] = reverse_check
        persist_local_apply_journal(journal_path, journal, state="failed_check")
        result.update({"status": "failed_check", "message": "反向 patch 校验失败，未修改业务文件。"})
        result["transaction"]["state"] = "failed_check"
        return result
    persist_local_apply_journal(journal_path, journal, state="rolling_back")
    reverse_apply = run_command(["git", "apply", "--reverse", "-"], cwd=project_path, input_text=final_diff, timeout=PATCH_TIMEOUT_SECONDS)
    result["reverse_apply"] = reverse_apply
    rollback_states = capture_target_file_states(project_path, paths)
    if reverse_apply["returncode"] != 0:
        result.update({"status": "recovery_required", "message": "反向 patch 执行失败，需要人工检查。"})
        result["transaction"]["state"] = "recovery_required"
        journal["reverse_apply"] = reverse_apply
        persist_local_apply_journal(journal_path, journal, state="recovery_required")
        return result
    diff_check = run_command(["git", "diff", "--check", "--", *paths], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    result["diff_check"] = diff_check
    if diff_check["returncode"] != 0:
        forward_check = run_command(["git", "apply", "--check", "-"], cwd=project_path, input_text=final_diff, timeout=PATCH_TIMEOUT_SECONDS)
        forward_apply = run_command(["git", "apply", "-"], cwd=project_path, input_text=final_diff, timeout=PATCH_TIMEOUT_SECONDS) if forward_check["returncode"] == 0 else {"returncode": -1}
        restored = forward_apply.get("returncode") == 0 and capture_target_file_states(project_path, paths) == current_states
        result["recovery"] = {"status": "success" if restored else "required", "forward_check": forward_check, "forward_apply": forward_apply}
        state = "failed_rolled_back" if restored else "recovery_required"
        persist_local_apply_journal(journal_path, journal, state=state)
        result.update({"status": state, "message": "回滚后检查失败；" + ("已恢复回滚前状态。" if restored else "恢复失败，需要人工处理。")})
        result["transaction"]["state"] = state
        return result
    pre_verify_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    verification = [
        run_shell_command(command, cwd=project_path, timeout=VERIFY_TIMEOUT_SECONDS)
        for command in (verify_commands or [])
    ]
    post_verify_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    verification_failed = any(item.get("returncode") != 0 for item in verification)
    verification_side_effect = post_verify_status.get("stdout", "") != pre_verify_status.get("stdout", "")
    result["verification"] = verification
    result["verification_side_effect"] = verification_side_effect
    if verification_failed or verification_side_effect:
        forward_check = run_command(["git", "apply", "--check", "-"], cwd=project_path, input_text=final_diff, timeout=PATCH_TIMEOUT_SECONDS)
        forward_apply = (
            run_command(["git", "apply", "-"], cwd=project_path, input_text=final_diff, timeout=PATCH_TIMEOUT_SECONDS)
            if forward_check["returncode"] == 0
            else {"returncode": -1, "stdout": "", "stderr": "forward check failed"}
        )
        restored_states = capture_target_file_states(project_path, paths)
        restored_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        restored = (
            forward_apply.get("returncode") == 0
            and restored_states == current_states
            and restored_status.get("stdout", "") == pre_status.get("stdout", "")
        )
        result["recovery"] = {
            "status": "success" if restored else "required",
            "forward_check": forward_check,
            "forward_apply": forward_apply,
            "target_restored": restored_states == current_states,
            "workspace_status_restored": restored_status.get("stdout", "") == pre_status.get("stdout", ""),
        }
        state = "verification_failed_restored" if restored else "recovery_required"
        journal.update(
            {
                "diff_check": diff_check,
                "verification": verification,
                "verification_side_effect": verification_side_effect,
                "recovery": result["recovery"],
            }
        )
        if not restored:
            result["recovery"]["manual_command"] = ["git", "apply", str(patch_path)]
        persist_local_apply_journal(journal_path, journal, state=state)
        result.update(
            {
                "status": state,
                "message": (
                    "回滚专项验证未通过；已恢复回滚前状态。"
                    if restored
                    else "回滚专项验证未通过且无法证明恢复，需要人工处理。"
                ),
            }
        )
        result["transaction"]["state"] = state
        return result
    post_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    journal.update({
        "reverse_check": reverse_check,
        "reverse_apply": reverse_apply,
        "diff_check": diff_check,
        "rollback_post_file_states": rollback_states,
        "verification": verification,
        "post_status": post_status,
    })
    persist_local_apply_journal(journal_path, journal, state="rolled_back")
    result.update({"status": "success", "message": "修改记录已在本地业务仓库事务回滚；未提交、未推送。", "post_status": post_status})
    result["transaction"]["state"] = "rolled_back"
    return result


def capture_target_file_states(project_path: Path, changed_paths: list[str]) -> dict[str, dict]:
    states: dict[str, dict] = {}
    for relative_path in changed_paths:
        safety_error = validate_relative_path(relative_path)
        if safety_error:
            raise OSError(safety_error)
        path = project_path / relative_path
        if path.is_symlink():
            target = os.readlink(path)
            states[relative_path] = {
                "type": "symlink",
                "sha256": hashlib.sha256(target.encode("utf-8")).hexdigest(),
                "target": target,
            }
        elif path.is_file():
            states[relative_path] = {
                "type": "file",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": path.stat().st_mode & 0o777,
            }
        elif path.exists():
            raise OSError(f"目标路径不是普通文件：{relative_path}")
        else:
            states[relative_path] = {"type": "missing", "sha256": ""}
    return states


def recover_failed_local_apply(
    *,
    result: dict,
    journal: dict,
    journal_path: Path,
    project_path: Path,
    final_diff: str,
    changed_paths: list[str],
    pre_file_states: dict[str, dict],
    pre_status: dict,
    failure_message: str,
    transaction: _AnchoredLocalApplyTransaction | None = None,
) -> dict:
    persist_local_apply_journal(journal_path, journal, state="rolling_back", transaction=transaction)
    result["transaction"]["state"] = "rolling_back"
    reverse_check = run_command(
        ["git", "apply", "--reverse", "--check", "-"],
        cwd=project_path,
        input_text=final_diff,
        timeout=PATCH_TIMEOUT_SECONDS,
    )
    reverse_apply = {"returncode": -1, "stdout": "", "stderr": "reverse check failed", "command": "git apply --reverse -"}
    if reverse_check["returncode"] == 0:
        reverse_apply = run_command(
            ["git", "apply", "--reverse", "-"],
            cwd=project_path,
            input_text=final_diff,
            timeout=PATCH_TIMEOUT_SECONDS,
        )
    restored_states = capture_target_file_states(project_path, changed_paths)
    restored_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    target_restored = restored_states == pre_file_states
    workspace_restored = restored_status.get("stdout", "") == pre_status.get("stdout", "")
    recovered = reverse_apply["returncode"] == 0 and target_restored and workspace_restored
    recovery = {
        "status": "success" if recovered else "required",
        "reverse_check": reverse_check,
        "reverse_apply": reverse_apply,
        "target_restored": target_restored,
        "workspace_status_restored": workspace_restored,
        "restored_file_states": restored_states,
        "restored_status": restored_status,
    }
    result["recovery"] = recovery
    journal["failure_message"] = failure_message
    journal["recovery"] = recovery
    if recovered:
        result["status"] = "rolled_back"
        result["transaction"]["state"] = "rolled_back"
        result["message"] = failure_message + " Harness 已自动反向恢复，目标文件和原工作区状态均已还原。"
        persist_local_apply_journal(journal_path, journal, state="rolled_back", transaction=transaction)
        return result

    result["status"] = "recovery_required"
    result["transaction"]["state"] = "recovery_required"
    result["message"] = failure_message + " 自动反向恢复未能证明完整还原，需要人工处理。"
    result["recovery"]["manual_command"] = ["git", "apply", "--reverse", str(Path(journal.get("patch_path") or ""))]
    journal["recovery"] = result["recovery"]
    persist_local_apply_journal(journal_path, journal, state="recovery_required", transaction=transaction)
    return result


def persist_local_apply_journal(
    journal_path: Path,
    journal: dict,
    *,
    state: str,
    transaction: _AnchoredLocalApplyTransaction | None = None,
) -> None:
    journal["state"] = state
    journal["updated_at_epoch"] = time.time()
    encoded = (json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if transaction is not None:
        transaction.write_atomic("journal.json", encoded)
    else:
        atomic_write_text(journal_path, encoded.decode("utf-8"))


def archive_completed_local_apply_journal(
    *,
    journal_path: Path,
    journal: dict,
    reason: str,
    transaction: _AnchoredLocalApplyTransaction | None = None,
) -> Path:
    archived = {
        **journal,
        "archive_reason": reason,
        "archived_at_epoch": time.time(),
    }
    encoded = (json.dumps(archived, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if transaction is not None:
        return transaction.archive_json(encoded)
    history_dir = journal_path.parent / "history"
    archive_path = history_dir / f"{time.time_ns()}-applied.json"
    atomic_write_text(archive_path, encoded.decode("utf-8"))
    return archive_path


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"read_error": str(exc)}
    if not isinstance(payload, dict):
        return {"read_error": "journal root must be a JSON object"}
    return payload


def cleanup_git_worktree(*, project_path: Path, worktree_path: Path) -> dict:
    result = {
        "status": "not_run",
        "message": "",
        "worktree_path": str(worktree_path),
        "remove": {},
        "prune": {},
    }
    # This legacy entry point intentionally performs no deletion.  A caller
    # must first prove the persisted binding, marker, registration and inode,
    # then invoke its own explicit preview/confirm workflow.  In particular,
    # never fall back to rmtree for an unowned path.
    result["status"] = "manual_cleanup_required"
    result["message"] = "需要持久绑定、标记、Git 登记和 inode 校验后的显式确认；未删除任何路径。"
    return result


def build_side_effect_report(*, before_snapshot: dict, after_snapshot: dict, side: str) -> dict:
    before_status = before_snapshot.get("status", {})
    after_status = after_snapshot.get("status", {})
    before_diff = before_snapshot.get("diff", {})
    after_diff = after_snapshot.get("diff", {})
    before_paths = set(before_status.get("paths", []))
    after_paths = set(after_status.get("paths", []))
    status_changed = (before_status.get("stdout") or "") != (after_status.get("stdout") or "")
    diff_changed = (before_diff.get("stdout") or "") != (after_diff.get("stdout") or "")
    return {
        "side": side,
        "changed": status_changed or diff_changed,
        "status_changed": status_changed,
        "diff_changed": diff_changed,
        "changed_paths": unique_keep_order(sorted(before_paths | after_paths)),
        "before_status": before_status,
        "after_status": after_status,
    }


def parse_status_paths(status_text: str) -> list[str]:
    paths: list[str] = []
    for raw_line in status_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            old_path, new_path = path.split(" -> ", 1)
            paths.extend([normalize_relative_path(old_path), normalize_relative_path(new_path)])
        else:
            paths.append(normalize_relative_path(path))
    return unique_keep_order(path for path in paths if path)


def summarize_command(result: dict) -> str:
    text = (result.get("stderr") or result.get("stdout") or "").strip()
    if not text:
        text = f"returncode={result.get('returncode')}"
    return truncate_text(text.replace("\n", " "), 600)


def strip_diff_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def normalize_relative_path(path: str) -> str:
    normalized = strip_diff_prefix(str(path).strip().replace("\\", "/"))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def unique_keep_order(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...（日志已截断）...\n" + text[-limit // 2 :]
