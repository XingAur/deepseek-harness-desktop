"""Safe multi-project worktree orchestration.

The change contract is deliberately the only source of write scope.  This
executor performs a read-only preflight for every repository before creating
any worktree, runs the existing single-repository executor in isolated
worktrees, and performs one deterministic cross-repository review before a
caller may opt into local write-back.

The default is verification-only.  A successful run therefore means that the
candidate patches passed all configured checks and are available for review;
it does not mean that any original repository was modified.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from app.change_context_execution import ChangeContextExecutionVerifier, validate_worker_context
from app.llm_client import BaseLLMClient
from app.multi_service_change_contract import MULTI_SERVICE_CHANGE_CONTRACT_SCHEMA_VERSION
from app.worktree_executor import (
    DEFAULT_WORKTREE_ROOT,
    WorktreeCodeExecutor,
    WorktreeExecutionOptions,
    WorktreeExecutionResult,
    capture_target_file_states,
    extract_patch_paths,
    rollback_applied_diff_in_project,
    validate_patch,
)


@dataclass
class MultiServiceExecutionOptions:
    contract: Mapping[str, Any]
    run_id: int
    demand_text: str
    report_markdown: str
    evidence_bundle: dict | None = None
    worktree_root: str = DEFAULT_WORKTREE_ROOT
    max_edit_rounds: int = 2
    # This remains opt-in.  The Harness caller must pass an explicit true
    # only after its own capability and user-confirmation gates have passed.
    apply_to_projects: bool = False
    cleanup_worktrees: bool = False
    change_context_binding: dict | None = None
    change_context_projection: dict | None = None


@dataclass
class MultiServiceExecutionResult:
    status: str
    summary: str
    repositories: dict[str, dict[str, Any]] = field(default_factory=dict)
    final_diffs: dict[str, str] = field(default_factory=dict)
    aggregate_review: dict[str, Any] = field(default_factory=dict)
    apply_to_projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    cleanup: dict[str, dict[str, Any]] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def plan_to_markdown(self) -> str:
        lines = [
            "## 多服务受控改码计划",
            "",
            "- 编排方式：每个仓库独立 worktree，全部通过后再进入汇总审查。",
            "- 默认写回：否；当前仅保留验证通过的 final.diff。",
            "",
            "| 仓库 | 角色 | 允许路径 | 验证命令 |",
            "| --- | --- | --- | --- |",
        ]
        for name, item in self.repositories.items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        name,
                        str(item.get("role") or "-"),
                        "<br>".join(item.get("allowed_paths") or []) or "-",
                        "<br>".join(item.get("verify_commands") or []) or "-",
                    ]
                )
                + " |"
            )
        return "\n".join(lines)

    def to_markdown(self) -> str:
        lines = [
            "## 多服务受控改码结果",
            "",
            f"- 状态：`{self.status}`",
            f"- 结论：{self.summary}",
            f"- 汇总 Diff 审查：`{self.aggregate_review.get('status') or 'not_run'}`",
            "- 原仓库写回：仅在显式开启且全部仓库通过后执行。",
            "",
            "| 仓库 | 状态 | Worktree | 变更路径 | 写回 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for name, item in self.repositories.items():
            apply_result = self.apply_to_projects.get(name) or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        name,
                        str(item.get("status") or "-"),
                        str(item.get("worktree_path") or "-"),
                        "<br>".join(item.get("changed_paths") or []) or "-",
                        str(apply_result.get("status") or "not_run"),
                    ]
                )
                + " |"
            )
        review_reasons = self.aggregate_review.get("reasons") or []
        if review_reasons:
            lines.extend(["", "### 汇总审查问题", ""])
            lines.extend(f"- {reason}" for reason in review_reasons)
        if self.final_diffs:
            lines.extend(["", "### final.diff 已保留", ""])
            lines.extend(f"- `{name}`：{len(diff.splitlines())} 行" for name, diff in self.final_diffs.items())
        return "\n".join(lines)


class MultiServiceWorktreeExecutor:
    """Orchestrate a ready multi-service contract without widening its scope."""

    def __init__(
        self,
        llm_client: BaseLLMClient,
        *,
        change_context_verifier: ChangeContextExecutionVerifier | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.change_context_verifier = change_context_verifier
        self.single_repo_executor = WorktreeCodeExecutor(
            llm_client,
            change_context_verifier=change_context_verifier,
        )

    def execute(self, options: MultiServiceExecutionOptions) -> MultiServiceExecutionResult:
        context_validation = validate_worker_context(
            self.change_context_verifier,
            options.change_context_binding,
            options.change_context_projection,
        )
        if context_validation.status != "ready":
            return MultiServiceExecutionResult(
                status="blocked",
                summary=context_validation.message,
                manifest={
                    "run_id": options.run_id,
                    "status": "blocked",
                    "change_context_binding": options.change_context_binding,
                    "change_context_validation": context_validation.to_dict(),
                },
            )
        contract = options.contract if isinstance(options.contract, Mapping) else {}
        preflight = self._preflight_contract(contract)
        manifest: dict[str, Any] = {
            "run_id": options.run_id,
            "contract_schema_version": contract.get("schema_version"),
            "worktree_root": str(Path(options.worktree_root).expanduser().resolve()),
            "apply_to_projects_requested": bool(options.apply_to_projects),
            "cleanup_worktrees_requested": bool(options.cleanup_worktrees),
            "preflight": preflight,
            "change_context_binding": options.change_context_binding,
            "change_context_validation": context_validation.to_dict(),
        }
        if preflight["status"] != "ready":
            manifest["status"] = "blocked"
            return MultiServiceExecutionResult(
                status="blocked",
                summary="多服务改码合同未通过执行前置检查，未创建任何 worktree。",
                aggregate_review={"status": "not_run", "reasons": preflight["reasons"]},
                manifest=manifest,
            )

        repositories = contract["repositories"]
        ordered_names = sorted(repositories)
        result_repositories: dict[str, dict[str, Any]] = {}
        final_diffs: dict[str, str] = {}
        cleanup: dict[str, dict[str, Any]] = {}
        execution_failed = False
        failure_reasons: list[str] = []
        for index, name in enumerate(ordered_names, start=1):
            repository = repositories[name]
            if execution_failed:
                result_repositories[name] = {
                    "status": "skipped",
                    "summary": "前置仓库执行失败，按 fail-closed 策略未继续修改后续仓库。",
                    "project_path": repository.get("project_path", ""),
                    "allowed_paths": list(repository.get("allowed_paths") or []),
                    "verify_commands": list(repository.get("verify_commands") or []),
                }
                continue
            repo_root = Path(options.worktree_root).expanduser().resolve() / _safe_worktree_name(name)
            repo_options = WorktreeExecutionOptions(
                project_path=str(repository["project_path"]),
                run_id=options.run_id,
                demand_text=options.demand_text,
                report_markdown=options.report_markdown,
                evidence_bundle=options.evidence_bundle,
                worktree_root=str(repo_root),
                allowed_paths=list(repository["allowed_paths"]),
                verify_commands=list(repository["verify_commands"]),
                max_edit_rounds=options.max_edit_rounds,
                # Never let a child executor write the original repository.
                # Batch write-back is handled only after aggregate review.
                apply_to_project=False,
                cleanup_worktree=False,
                change_context_binding=options.change_context_binding,
                change_context_projection=options.change_context_projection,
            )
            try:
                child_result = self.single_repo_executor.execute(repo_options)
            except Exception as exc:  # fail closed and retain a structured reason
                child_result = WorktreeExecutionResult(
                    status="failed",
                    summary=f"单仓执行器异常：{type(exc).__name__}: {exc}",
                    allowed_paths=list(repository["allowed_paths"]),
                )
            child_dict = child_result.to_dict()
            child_dict.update(
                {
                    "repository": name,
                    "project_path": repository["project_path"],
                    "role": repository.get("role", ""),
                    "allowed_paths": list(repository["allowed_paths"]),
                    "verify_commands": list(repository["verify_commands"]),
                }
            )
            result_repositories[name] = child_dict
            if child_result.status != "success":
                execution_failed = True
                failure_reasons.append(f"{name}：{child_result.summary}")
                continue
            final_diffs[name] = child_result.final_diff
            cleanup[name] = child_result.cleanup
            manifest.setdefault("child_manifests", {})[name] = child_result.manifest

        completion_context_validation = validate_worker_context(
            self.change_context_verifier,
            options.change_context_binding,
            options.change_context_projection,
        )
        manifest["change_context_completion_validation"] = completion_context_validation.to_dict()
        if completion_context_validation.status != "ready":
            return MultiServiceExecutionResult(
                status="blocked",
                summary=completion_context_validation.message,
                repositories=result_repositories,
                final_diffs=final_diffs,
                apply_to_projects={
                    name: {"status": "blocked", "message": completion_context_validation.message}
                    for name in ordered_names
                },
                cleanup=cleanup,
                manifest=manifest,
            )
        aggregate_review = self._review_all(contract, result_repositories, final_diffs)
        apply_results: dict[str, dict[str, Any]] = {
            name: {"status": "not_run", "message": "汇总审查未通过或未开启写回。"}
            for name in ordered_names
        }
        status = "success"
        summary = "所有仓库的独立 worktree patch 和定向验证通过；原仓库未写回。"
        if execution_failed:
            status = "failed"
            summary = "多服务执行在某个仓库失败，已停止后续仓库，原仓库未写回。"
            aggregate_review.setdefault("reasons", []).extend(failure_reasons)
        elif aggregate_review.get("status") != "passed":
            status = "failed"
            summary = "逐仓验证虽完成，但汇总 Diff 审查未通过，原仓库未写回。"
        elif not options.apply_to_projects:
            apply_results = {
                name: {
                    "status": "skipped",
                    "message": "默认验证模式：只保留 final.diff，不写回原仓库。",
                }
                for name in ordered_names
            }
        elif options.apply_to_projects:
            pre_apply_context_validation = validate_worker_context(
                self.change_context_verifier,
                options.change_context_binding,
                options.change_context_projection,
            )
            manifest["change_context_pre_apply_validation"] = pre_apply_context_validation.to_dict()
            if pre_apply_context_validation.status != "ready":
                status = "blocked"
                summary = pre_apply_context_validation.message
                apply_results = {
                    name: {"status": "blocked", "message": pre_apply_context_validation.message}
                    for name in ordered_names
                }
            else:
                # This phase intentionally keeps the write path explicit. The
                # caller can opt in, but all repositories must pass the
                # pre-apply clean check under the still-current context.
                apply_results = self._apply_batch(
                    contract=contract,
                    final_diffs=final_diffs,
                    ordered_names=ordered_names,
                    run_id=options.run_id,
                )
                if any(item.get("status") != "success" for item in apply_results.values()):
                    status = "failed"
                    summary = "汇总审查通过，但批量写回未全部成功；请依据回滚/恢复证据人工处理。"
                else:
                    summary = "所有仓库 patch、定向验证和汇总 Diff 审查通过，并已按显式授权写回原仓库；未提交、未推送。"
        for name in ordered_names:
            if name not in cleanup:
                cleanup[name] = {"status": "not_run", "message": result_repositories.get(name, {}).get("summary", "")}
        manifest.update(
            {
                "status": status,
                "summary": summary,
                "aggregate_review": aggregate_review,
                "repository_order": ordered_names,
                "finished": True,
            }
        )
        return MultiServiceExecutionResult(
            status=status,
            summary=summary,
            repositories=result_repositories,
            final_diffs=final_diffs,
            aggregate_review=aggregate_review,
            apply_to_projects=apply_results,
            cleanup=cleanup,
            manifest=manifest,
        )

    def _preflight_contract(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        reasons: list[str] = []
        if contract.get("schema_version") != MULTI_SERVICE_CHANGE_CONTRACT_SCHEMA_VERSION:
            reasons.append("改动合同 schema 版本不受当前执行器支持。")
        if contract.get("status") != "ready":
            reasons.append("改动合同不是 ready，拒绝进入 worktree。")
        if (contract.get("continuation") or {}).get("status") != "ready_for_execution":
            reasons.append("改动合同尚未进入 ready_for_execution。")
        if (contract.get("rollback") or {}).get("status") != "ready":
            reasons.append("改动合同没有可用回退策略。")
        repositories = contract.get("repositories")
        if not isinstance(repositories, Mapping) or not repositories:
            reasons.append("改动合同没有逐仓库边界。")
            repositories = {}
        targets = contract.get("targets")
        if not isinstance(targets, list) or not targets:
            reasons.append("改动合同没有候选目标。")
            targets = []
        resolved_paths: dict[str, str] = {}
        for name in sorted(repositories):
            item = repositories[name]
            if not isinstance(item, Mapping):
                reasons.append(f"仓库 {name} 不是结构化对象。")
                continue
            raw_project_path = str(item.get("project_path") or "").strip()
            if not raw_project_path:
                reasons.append(f"仓库 {name} 缺少 project_path。")
                continue
            project_path = Path(raw_project_path).expanduser()
            try:
                resolved = str(project_path.resolve(strict=False))
            except OSError:
                resolved = str(project_path.absolute())
            if resolved in resolved_paths.values():
                reasons.append(f"仓库 {name} 与其他仓库指向同一个 project_path。")
            resolved_paths[name] = resolved
            allowed = item.get("allowed_paths")
            commands = item.get("verify_commands")
            if not isinstance(allowed, list) or not allowed:
                reasons.append(f"仓库 {name} 缺少允许修改路径。")
            else:
                for path in allowed:
                    if not isinstance(path, str) or not path.strip():
                        reasons.append(f"仓库 {name} 存在空白允许路径。")
            if not isinstance(commands, list) or not commands or any(not isinstance(command, str) or not command.strip() for command in commands):
                reasons.append(f"仓库 {name} 缺少逐仓库验证命令。")
            preflight_options = WorktreeExecutionOptions(
                project_path=raw_project_path,
                run_id=0,
                demand_text="",
                report_markdown="",
                allowed_paths=list(allowed or []),
                verify_commands=list(commands or []),
                apply_to_project=False,
                cleanup_worktree=False,
            )
            error = self.single_repo_executor._preflight(
                project_path=Path(raw_project_path).expanduser().resolve(),
                allowed_paths=list(preflight_options.allowed_paths),
            )
            if error:
                reasons.append(f"仓库 {name} 前置检查失败：{error}")

        repo_names = set(repositories)
        for index, target in enumerate(targets, start=1):
            if not isinstance(target, Mapping):
                reasons.append(f"候选目标 #{index} 不是结构化对象。")
                continue
            source = str(target.get("source_project") or "")
            destination = str(target.get("target_project") or "")
            if source not in repo_names or destination not in repo_names:
                reasons.append(f"候选目标 #{index} 引用了合同外仓库。")
                continue
            source_allowed = set(repositories[source].get("allowed_paths") or [])
            destination_allowed = set(repositories[destination].get("allowed_paths") or [])
            source_paths = {str(path) for path in (target.get("source_paths") or [])}
            source_paths.update(str(path) for path in (target.get("entry_paths") or []))
            destination_path = str(target.get("target_path") or "")
            if not source_paths or not source_paths.issubset(source_allowed):
                reasons.append(f"候选目标 #{index} 的 source/entry 路径不在 source 仓库白名单内。")
            if not destination_path or destination_path not in destination_allowed:
                reasons.append(f"候选目标 #{index} 的 target_path 不在 target 仓库白名单内。")
        return {"status": "ready" if not reasons else "blocked", "reasons": sorted(set(reasons))}

    def _review_all(
        self,
        contract: Mapping[str, Any],
        repositories: Mapping[str, Mapping[str, Any]],
        final_diffs: Mapping[str, str],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        checked: dict[str, Any] = {}
        for name, item in repositories.items():
            if item.get("status") == "skipped":
                reasons.append(f"仓库 {name} 未执行。")
                continue
            if item.get("status") != "success":
                reasons.append(f"仓库 {name} 未通过独立 worktree 验证。")
                continue
            diff = str(final_diffs.get(name) or "")
            validation = validate_patch(diff, allowed_paths=list(item.get("allowed_paths") or []))
            checked[name] = {
                "status": "passed" if validation.ok else "failed",
                "changed_paths": validation.changed_paths,
                "message": validation.message,
            }
            if not validation.ok:
                reasons.append(f"仓库 {name} 的 final.diff 未通过白名单复核：{validation.message}")
            if not validation.changed_paths:
                reasons.append(f"仓库 {name} 没有实际变更路径。")

        changed_by_repo = {
            name: set(extract_patch_paths(final_diffs.get(name, "")))
            for name in final_diffs
        }
        for index, target in enumerate(contract.get("targets") or [], start=1):
            source = str(target.get("source_project") or "")
            destination = str(target.get("target_project") or "")
            source_expected = set(target.get("source_paths") or []) | set(target.get("entry_paths") or [])
            destination_expected = {str(target.get("target_path") or "")}
            if not source_expected.intersection(changed_by_repo.get(source, set())):
                reasons.append(f"候选目标 #{index} 没有修改 source 仓库证据路径：{source}。")
            if not destination_expected.intersection(changed_by_repo.get(destination, set())):
                reasons.append(f"候选目标 #{index} 没有修改 target 仓库证据路径：{destination}。")
        return {
            "status": "passed" if not reasons else "failed",
            "repositories": checked,
            "target_coverage_checked": len(contract.get("targets") or []),
            "reasons": sorted(set(reasons)),
        }

    def _apply_batch(
        self,
        *,
        contract: Mapping[str, Any],
        final_diffs: Mapping[str, str],
        ordered_names: list[str],
        run_id: int,
    ) -> dict[str, dict[str, Any]]:
        """Apply only after all checks pass; never apply a partial unchecked set.

        The current Harness keeps this method private and opt-in.  It performs
        a clean-target preflight for every repository before the first write;
        a later failure is reported explicitly rather than hidden as success.
        The default execution path never calls it.
        """

        from app.worktree_executor import apply_final_diff_to_project

        results = {
            name: {"status": "not_run", "message": "未执行写回。"}
            for name in ordered_names
        }
        for name in ordered_names:
            repository = contract["repositories"][name]
            diff = str(final_diffs.get(name) or "")
            if not diff.strip():
                results[name] = {"status": "failed", "message": "final.diff 为空，拒绝写回。"}
                return results
            clean_error = self.single_repo_executor._preflight(
                project_path=Path(repository["project_path"]).expanduser().resolve(),
                allowed_paths=list(repository["allowed_paths"]),
            )
            if clean_error:
                results[name] = {"status": "failed", "message": f"写回前置检查失败：{clean_error}"}
                return results
        applied: list[tuple[str, dict[str, Any]]] = []
        for name in ordered_names:
            repository = contract["repositories"][name]
            results[name] = apply_final_diff_to_project(
                project_path=Path(repository["project_path"]).expanduser().resolve(),
                final_diff=str(final_diffs[name]),
            )
            if results[name].get("status") != "success":
                # A later repository can become dirty between the all-repo
                # preflight and its own apply.  Try to reverse earlier writes
                # using their exact post-apply file identities; if identities
                # drifted, fail closed and retain recovery evidence.
                for applied_name, applied_result in reversed(applied):
                    applied_repository = contract["repositories"][applied_name]
                    try:
                        paths = [
                            path
                            for path in extract_patch_paths(str(final_diffs[applied_name]))
                            if path != "/dev/null"
                        ]
                        rollback = rollback_applied_diff_in_project(
                            project_path=Path(applied_repository["project_path"]).expanduser().resolve(),
                            final_diff=str(final_diffs[applied_name]),
                            expected_post_file_states=applied_result.get("post_file_states") or capture_target_file_states(
                                Path(applied_repository["project_path"]).expanduser().resolve(), paths
                            ),
                            rollback_id=f"multi-{run_id}-{applied_name}",
                            verify_commands=[],
                        )
                    except (OSError, ValueError) as exc:
                        rollback = {"status": "recovery_required", "message": str(exc)}
                    results[applied_name]["rollback"] = rollback
                return results
            try:
                paths = [
                    path
                    for path in extract_patch_paths(str(final_diffs[name]))
                    if path != "/dev/null"
                ]
                results[name]["post_file_states"] = capture_target_file_states(
                    Path(repository["project_path"]).expanduser().resolve(), paths
                )
            except (OSError, ValueError) as exc:
                results[name]["status"] = "recovery_required"
                results[name]["message"] = f"写回后无法登记目标文件状态：{exc}"
                for applied_name, applied_result in reversed(applied):
                    applied_repository = contract["repositories"][applied_name]
                    paths = [
                        path
                        for path in extract_patch_paths(str(final_diffs[applied_name]))
                        if path != "/dev/null"
                    ]
                    applied_result["rollback"] = rollback_applied_diff_in_project(
                        project_path=Path(applied_repository["project_path"]).expanduser().resolve(),
                        final_diff=str(final_diffs[applied_name]),
                        expected_post_file_states=applied_result.get("post_file_states") or capture_target_file_states(
                            Path(applied_repository["project_path"]).expanduser().resolve(), paths
                        ),
                        rollback_id=f"multi-{run_id}-{applied_name}",
                        verify_commands=[],
                    )
                return results
            applied.append((name, results[name]))
        return results


def _safe_worktree_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    normalized = normalized.strip("._-") or "repository"
    return normalized[:80]
