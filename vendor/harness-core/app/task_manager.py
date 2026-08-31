from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import shlex
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import database
from app.harness import TEAM_KEY, RequirementWorkflowRunner, WorkflowResult, write_run_outputs
from app.harness_config import (
    config_summary_to_markdown,
    configuration_import_draft_to_markdown,
    configuration_import_review_to_markdown,
    configuration_preview_to_markdown,
    configuration_share_validation_to_markdown,
    configuration_template_index_to_markdown,
    configuration_wizard_to_markdown,
)
from app.llm_client import load_claude_settings_env_if_requested
from app.precommit_verifier import PrecommitVerificationOptions, PrecommitVerificationResult, PrecommitVerifier
from app.technical_decision import DEFAULT_PROJECT_ROOT
from app.worktree_executor import (
    atomic_write_text,
    capture_target_file_states,
    extract_patch_paths,
    rollback_applied_diff_in_project,
)
from app.runtime_preflight import choose_private_runtime_root, run_runtime_preflight
from app.yunxiao_read import parse_work_item_id


DEFAULT_TASK_OUTPUT_ROOT = Path("/tmp/his_harness_tasks")
DEFAULT_TASK_WORKTREE_ROOT = Path("/tmp/his_harness_task_worktrees")
WORKSPACE_SNAPSHOT_HISTORY_LIMIT = 20


@dataclass
class TaskCreateOptions:
    yunxiao_url: str = ""
    title: str = ""
    entity_kind: str = ""
    entity_id: str = ""
    source_type: str = "yunxiao"
    project_root: str = DEFAULT_PROJECT_ROOT
    project_paths: list[str] | None = None
    base_branch: str = ""
    work_branch: str = ""
    notes: str = ""
    metadata: dict | None = None


@dataclass
class TaskRunOptions:
    task_id: int | None = None
    task_key: str = ""
    yunxiao_url: str = ""
    title: str = ""
    demand_text: str = ""
    demand_file: str = ""
    mode: str = "mock"
    load_claude_settings: bool = False
    execution_mode: str = "readonly"
    project_root: str = DEFAULT_PROJECT_ROOT
    project_paths: list[str] | None = None
    allowed_paths: list[str] | None = None
    verify_commands: list[str] | None = None
    worktree_dir: str = str(DEFAULT_TASK_WORKTREE_ROOT)
    output_root: str = str(DEFAULT_TASK_OUTPUT_ROOT)
    max_retries: int = 2
    max_edit_rounds: int = 2
    pre_change_confirmation: str = ""
    review_commit: str = "HEAD"
    review_base: str = ""
    requirement_evidence_file: str = ""
    multi_service_evidence_file: str = ""
    yunxiao_include_comments: bool = True


@dataclass
class TaskExistingRunOptions:
    task_id: int | None = None
    task_key: str = ""
    yunxiao_url: str = ""
    title: str = ""
    entity_kind: str = ""
    entity_id: str = ""
    project_root: str = DEFAULT_PROJECT_ROOT
    project_paths: list[str] | None = None
    output_dir: str = ""
    execution_mode: str = "precommit-verify"
    source_run_id: int | None = None
    status: str = ""
    evaluation_status: str = ""
    notes: str = ""
    metadata: dict | None = None


@dataclass
class TaskPrecommitRerunOptions:
    task_id: int | None = None
    task_key: str = ""
    yunxiao_url: str = ""
    title: str = ""
    demand_text: str = ""
    demand_file: str = ""
    project_root: str = DEFAULT_PROJECT_ROOT
    project_path: str = ""
    allowed_paths: list[str] | None = None
    verify_commands: list[str] | None = None
    method_test_commands: list[str] | None = None
    ui_evidence_paths: list[str] | None = None
    ui_capture_commands: list[str] | None = None
    worktree_dir: str = str(DEFAULT_TASK_WORKTREE_ROOT)
    output_root: str = str(DEFAULT_TASK_OUTPUT_ROOT)
    output_dir: str = ""
    target_key: str = ""
    target_name: str = ""
    target_role: str = "frontend"


@dataclass
class TaskChangeRecordOptions:
    task_id: int | None = None
    task_key: str = ""
    yunxiao_url: str = ""
    title: str = ""
    task_run_id: int | None = None
    run_id: int | None = None
    source_type: str = "manual"
    status: str = "recorded"
    project_path: str = ""
    allowed_paths: list[str] | None = None
    diff_path: str = ""
    diff_text: str = ""
    verification_status: str = ""
    notes: str = ""
    metadata: dict | None = None


@dataclass
class TaskManualVerificationOptions:
    task_id: int | None = None
    task_key: str = ""
    yunxiao_url: str = ""
    title: str = ""
    source_task_run_id: int | None = None
    source_run_id: int | None = None
    status: str = "passed"
    verifier: str = "user"
    summary: str = ""
    scenarios: list[str] | None = None
    notes: list[str] | None = None
    output_root: str = str(DEFAULT_TASK_OUTPUT_ROOT)


@dataclass
class TaskRollbackPlanOptions:
    task_id: int | None = None
    task_key: str = ""
    yunxiao_url: str = ""
    title: str = ""
    change_id: str = ""
    target_change_sequence: int | None = None
    output_dir: str = ""


@dataclass
class TaskRollbackApplyOptions:
    task_id: int | None = None
    task_key: str = ""
    yunxiao_url: str = ""
    title: str = ""
    change_id: str = ""
    target_change_sequence: int | None = None
    confirmation: str = ""
    verify_commands: list[str] | None = None


@dataclass
class TaskDashboardFilters:
    entity_id: str = ""
    task_key: str = ""
    entity_kind: str = ""
    status: str = ""
    verification_status: str = ""
    ui_evidence_status: str = ""
    can_commit: bool | None = None
    sample_only: bool = False


class TaskManager:
    def __init__(self) -> None:
        self.runtime_preflight = run_runtime_preflight(database_path=database.DB_PATH)
        try:
            database.init_db()
        except (OSError, sqlite3.OperationalError):
            fallback_root = choose_private_runtime_root(prefix="his_harness_task_runtime_")
            database.DB_PATH = fallback_root / "harness.sqlite"
            self.runtime_preflight = {
                **self.runtime_preflight,
                "status": "degraded_readonly",
                "read_only": True,
                "fallback": {"database_path": str(database.DB_PATH)},
                "mutation_blockers": ["runtime_database_fallback"],
            }
            database.init_db()
        self.startup_recovery = {
            "runs": database.reconcile_stale_runs(max_age_hours=24),
            "tasks": database.reconcile_stale_tasks(max_age_hours=24),
        }

    def create_task(self, options: TaskCreateOptions) -> dict:
        entity_id = normalize_entity_id(options.entity_id or parse_work_item_id(options.yunxiao_url) or parse_work_item_id(options.title))
        entity_kind = options.entity_kind or infer_entity_kind(options.yunxiao_url)
        task_key = build_task_key(entity_kind=entity_kind, entity_id=entity_id, title=options.title)
        project_paths = unique_keep_order(options.project_paths or [])
        task_id = database.upsert_task(
            {
                "task_key": task_key,
                "entity_kind": entity_kind,
                "entity_id": entity_id,
                "entity_title": options.title,
                "entity_url": options.yunxiao_url,
                "source_type": options.source_type or ("yunxiao" if options.yunxiao_url else "manual"),
                "current_stage": "created",
                "status": "created",
                "project_root": options.project_root or DEFAULT_PROJECT_ROOT,
                "project_paths": project_paths,
                "base_branch": options.base_branch,
                "work_branch": options.work_branch or suggest_work_branch(entity_kind=entity_kind, entity_id=entity_id),
                "notes": options.notes,
                "metadata": options.metadata or {},
                "can_yunxiao_transition": False,
            }
        )
        return self.get_task(task_id)

    def get_task(self, task_id: int) -> dict:
        task = database.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    def resolve_task(self, *, task_id: int | None = None, task_key: str = "", yunxiao_url: str = "", title: str = "") -> dict:
        if task_id:
            return self.get_task(task_id)
        if task_key:
            task = database.get_task_by_key(task_key)
            if task is None:
                raise KeyError(f"task not found: {task_key}")
            return task
        entity_id = normalize_entity_id(parse_work_item_id(yunxiao_url) or parse_work_item_id(title))
        entity_kind = infer_entity_kind(yunxiao_url)
        if entity_id and entity_kind:
            task = database.get_task_by_entity(entity_kind, entity_id)
            if task is not None:
                return task
        if yunxiao_url or title:
            return self.create_task(
                TaskCreateOptions(
                    yunxiao_url=yunxiao_url,
                    title=title,
                    entity_kind=entity_kind,
                    entity_id=entity_id,
                )
            )
        raise ValueError("请提供 --task-id、--task-key 或 --yunxiao-url。")

    def list_tasks(self, limit: int = 50) -> list[dict]:
        return database.list_tasks(limit=limit)

    def list_task_runs(self, task_id: int) -> list[dict]:
        return database.list_task_runs(task_id)

    def list_delivery_transactions(self, task_id: int) -> list[dict]:
        return database.list_delivery_transactions(task_id=task_id)

    def record_change(self, options: TaskChangeRecordOptions | dict) -> dict:
        active_options = coerce_task_change_options(options)
        task = self.resolve_task(
            task_id=active_options.task_id,
            task_key=active_options.task_key,
            yunxiao_url=active_options.yunxiao_url,
            title=active_options.title,
        )
        task_id = int(task["id"])
        source_diff_path = str(Path(active_options.diff_path).expanduser().resolve()) if active_options.diff_path else ""
        diff_path = source_diff_path
        diff_text = active_options.diff_text
        if not diff_text and diff_path:
            diff_text = Path(diff_path).read_text(encoding="utf-8", errors="ignore")
        if not diff_text.strip():
            raise ValueError("记录修改历史需要提供 diff_path 或 diff_text。")
        changes = database.list_task_changes(task_id)
        next_sequence = max([int(item.get("change_sequence") or 0) for item in changes] or [0]) + 1
        task_key = str(task.get("task_key") or f"task-{task_id}")
        change_id = build_change_id(task_key=task_key, sequence=next_sequence)
        project_path = active_options.project_path or first_text(task.get("project_paths") or [])
        if project_path:
            project_path = str(Path(project_path).expanduser().resolve())
        changed_paths = [path for path in extract_patch_paths(diff_text) if path != "/dev/null"]
        if not changed_paths:
            raise ValueError("记录修改历史无法从 diff 解析目标文件。")
        allowed_paths = unique_keep_order(active_options.allowed_paths or [])
        outside_paths = [path for path in changed_paths if allowed_paths and path not in set(allowed_paths)]
        if outside_paths:
            raise ValueError("diff 包含允许路径外文件：" + ", ".join(outside_paths))
        stored_diff_path = task_change_diff_storage_path(change_id)
        atomic_write_text(stored_diff_path, diff_text)
        diff_path = str(stored_diff_path)
        metadata = dict(active_options.metadata or {})
        metadata.update(
            {
                "source_diff_path": source_diff_path,
                "stored_diff_path": diff_path,
                "changed_paths": changed_paths,
            }
        )
        post_file_states: dict = {}
        baseline_error = ""
        if project_path:
            try:
                post_file_states = capture_target_file_states(Path(project_path).expanduser().resolve(), changed_paths)
            except OSError as exc:
                baseline_error = str(exc)
        metadata["post_file_states"] = post_file_states
        if baseline_error:
            metadata["rollback_baseline_error"] = baseline_error
        rollback_mode = "local_transaction" if set(post_file_states) == set(changed_paths) else "dry_run_only"
        record_id = database.add_task_change(
            {
                "task_id": task_id,
                "task_run_id": active_options.task_run_id,
                "run_id": active_options.run_id,
                "change_sequence": next_sequence,
                "change_id": change_id,
                "source_type": active_options.source_type or "manual",
                "status": active_options.status or "recorded",
                "project_path": project_path,
                "allowed_paths": allowed_paths,
                "diff_path": diff_path,
                "diff_summary": summarize_unified_diff(diff_text),
                "diff_sha256": hashlib.sha256(diff_text.encode("utf-8")).hexdigest(),
                "verification_status": active_options.verification_status or "",
                "rollback_mode": rollback_mode,
                "rollback_status": "available",
                "notes": active_options.notes,
                "metadata": metadata,
            }
        )
        change = database.get_task_change_by_sequence(task_id, next_sequence)
        if change is None:
            raise RuntimeError(f"修改历史写入失败：{record_id}")
        return enrich_task_change(change)

    def list_task_changes(self, task_id: int) -> list[dict]:
        return [enrich_task_change(item) for item in database.list_task_changes(task_id)]

    def record_manual_verification(self, options: TaskManualVerificationOptions | dict) -> tuple[dict, dict, Path]:
        active_options = coerce_task_manual_verification_options(options)
        task = self.resolve_task(
            task_id=active_options.task_id,
            task_key=active_options.task_key,
            yunxiao_url=active_options.yunxiao_url,
            title=active_options.title,
        )
        verification_status = active_options.status.strip().lower()
        if verification_status not in {"passed", "failed"}:
            raise ValueError("人工运行时验收状态仅支持 passed 或 failed。")
        summary = active_options.summary.strip()
        if not summary:
            raise ValueError("人工运行时验收需要提供 summary，说明实际验证结论。")

        source_task_run = resolve_manual_verification_source_run(
            task_id=int(task["id"]),
            runs=self.list_task_runs(int(task["id"])),
            source_task_run_id=active_options.source_task_run_id,
            source_run_id=active_options.source_run_id,
        )
        source_run_id = source_task_run.get("run_id")
        source_run = database.get_run(int(source_run_id)) if source_run_id else {}
        output_dir = build_task_output_dir(
            output_root=active_options.output_root,
            task=task,
            execution_mode="manual-runtime-verification",
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        result_status = "success" if verification_status == "passed" else "failed"
        evaluation_status = "manual_verified" if verification_status == "passed" else "manual_verification_failed"
        task_verification_status = "manual_passed" if verification_status == "passed" else "manual_failed"
        run_id = database.create_run(
            team_key=TEAM_KEY,
            title=str(task.get("entity_title") or task.get("entity_id") or "手工运行时验收"),
            source_type="manual-runtime-verification",
            demand_text=str(task.get("entity_title") or task.get("entity_url") or task.get("task_key") or "手工运行时验收"),
            total_steps=0,
            llm_mode="manual",
            llm_model="",
        )
        evidence = build_manual_runtime_verification_evidence(
            task=task,
            source_task_run=source_task_run,
            source_run=source_run or {},
            run_id=run_id,
            status=verification_status,
            verifier=active_options.verifier,
            summary=summary,
            scenarios=active_options.scenarios or [],
            notes=active_options.notes or [],
        )
        evidence_json_path = output_dir / "manual_runtime_verification.json"
        evidence_markdown_path = output_dir / "manual_runtime_verification.md"
        evidence_json_text = json.dumps(evidence, ensure_ascii=False, indent=2)
        evidence_json_path.write_text(evidence_json_text, encoding="utf-8")
        evidence_markdown_text = manual_runtime_verification_to_markdown(evidence)
        evidence_markdown_path.write_text(evidence_markdown_text, encoding="utf-8")
        database.add_artifact(run_id, "manual_runtime_verification_json", "人工运行时验收记录", evidence_json_text)
        database.add_artifact(run_id, "manual_runtime_verification_md", "人工运行时验收说明", evidence_markdown_text)
        database.update_run(
            run_id,
            status=result_status,
            evaluation_status=evaluation_status,
            evaluation_summary=summary,
            finished_at=database.now_iso(),
        )
        artifacts = {
            **(source_task_run.get("artifact_paths") or {}),
            **build_latest_artifacts(output_dir=output_dir),
        }
        task_run_id = database.add_task_run(
            {
                "task_id": task["id"],
                "run_id": run_id,
                "stage": stage_for_execution_mode("manual-runtime-verification"),
                "execution_mode": "manual-runtime-verification",
                "status": result_status,
                "evaluation_status": evaluation_status,
                "output_dir": str(output_dir),
                "summary": summary,
                "verification_status": task_verification_status,
                "artifact_paths": artifacts,
                "started_at": database.now_iso(),
                "finished_at": database.now_iso(),
            }
        )
        database.update_task(
            int(task["id"]),
            current_stage=stage_for_execution_mode("manual-runtime-verification"),
            status="manual_verified" if verification_status == "passed" else "manual_verification_failed",
            latest_run_id=run_id,
            latest_output_dir=str(output_dir),
            latest_artifacts=artifacts,
            verification_status=task_verification_status,
            can_commit=False,
            can_yunxiao_transition=False,
        )
        task = self.get_task(int(task["id"]))
        task_run = database.get_task_run(task_run_id) or {}
        write_task_manager_record_outputs(output_dir=output_dir, task=task, task_run=task_run)
        write_task_manager_run_history(output_dir=output_dir, task=task, runs=self.list_task_runs(int(task["id"])))
        write_ui_evidence_reuse_policy(output_dir=output_dir, task=task)
        artifacts = {
            **(source_task_run.get("artifact_paths") or {}),
            **build_latest_artifacts(output_dir=output_dir),
        }
        database.update_task_run(task_run_id, artifact_paths=artifacts)
        database.update_task(int(task["id"]), latest_artifacts=artifacts)
        return self.get_task(int(task["id"])), database.get_task_run(task_run_id) or task_run, output_dir

    def build_task_change_history(self, task_id: int) -> dict:
        task = self.get_task(task_id)
        changes = self.list_task_changes(task_id)
        latest = changes[-1] if changes else {}
        transactional_changes = [
            change
            for change in changes
            if change.get("rollback_mode") == "local_transaction"
        ]
        rollback_mode = "local_transaction" if transactional_changes else "dry_run_only"
        return {
            "version": "0.58-task-change-history",
            "readonly": True,
            "dry_run_only": not bool(transactional_changes),
            "task_id": task_id,
            "task_key": task.get("task_key") or "",
            "rollback_mode": rollback_mode,
            "rollback_available": bool(changes),
            "transactional_rollback_available": bool(transactional_changes),
            "change_count": len(changes),
            "latest_change": latest,
            "changes": changes,
            "residual_risk": (
                "修改历史只登记 Harness 已知 diff 和目标文件后置哈希。实际本地回滚要求目标文件未漂移、"
                "diff 哈希一致并提供精确确认；不会提交、推送或修改远端。"
                if transactional_changes
                else "旧修改记录缺少完整目标文件后置哈希，只能生成回滚 dry-run 计划。"
            ),
        }

    def build_change_rollback_plan(self, options: TaskRollbackPlanOptions | dict) -> dict:
        active_options = coerce_task_rollback_options(options)
        task = self.resolve_task(
            task_id=active_options.task_id,
            task_key=active_options.task_key,
            yunxiao_url=active_options.yunxiao_url,
            title=active_options.title,
        )
        task_id = int(task["id"])
        change = resolve_change_for_rollback(task_id=task_id, options=active_options)
        if change is None:
            raise KeyError("未找到可回滚的修改记录。")
        diff_path = str(change.get("diff_path") or "").strip()
        if not diff_path:
            raise ValueError("该修改记录缺少 diff_path，无法生成回滚 dry-run 计划。")
        source_diff = Path(diff_path).expanduser().resolve()
        if not source_diff.exists() or not source_diff.is_file():
            raise FileNotFoundError(f"修改记录 diff 不存在：{source_diff}")
        output_dir = Path(active_options.output_dir).expanduser().resolve() if active_options.output_dir else Path(str(task.get("latest_output_dir") or DEFAULT_TASK_OUTPUT_ROOT)).expanduser().resolve() / "rollback_plan"
        output_dir.mkdir(parents=True, exist_ok=True)
        diff_text = source_diff.read_text(encoding="utf-8", errors="ignore")
        sequence = int(change.get("change_sequence") or 0)
        reverse_patch_path = output_dir / f"change_{sequence}_reverse.patch"
        reverse_patch_path.write_text(reverse_unified_diff(diff_text), encoding="utf-8")
        project_path = str(change.get("project_path") or first_text(task.get("project_paths") or []) or "").strip()
        check_command = build_git_apply_reverse_command(project_path=project_path, diff_path=str(source_diff), check=True)
        apply_command = build_git_apply_reverse_command(project_path=project_path, diff_path=str(source_diff), check=False)
        plan = {
            "version": "0.17-rollback-dry-run",
            "generated_at": database.now_iso(),
            "readonly": True,
            "dry_run_only": True,
            "will_modify_files": False,
            "status": "ready_for_manual_review",
            "task_id": task_id,
            "task_key": task.get("task_key") or "",
            "target_change_sequence": sequence,
            "change_id": change.get("change_id"),
            "source_diff_path": str(source_diff),
            "reverse_patch_path": str(reverse_patch_path),
            "project_path": project_path,
            "allowed_paths": change.get("allowed_paths") or [],
            "diff_summary": change.get("diff_summary") or "",
            "commands": {
                "open_plan_dir": shell_join(["open", str(output_dir)]),
                "apply_reverse_patch_check": check_command,
                "apply_reverse_patch": apply_command,
                "apply_transactional_rollback": shell_join(
                    [
                        "python3",
                        "tools/task_manager.py",
                        "rollback-apply",
                        "--task-key",
                        str(task.get("task_key") or ""),
                        "--change-id",
                        str(change.get("change_id") or ""),
                        "--confirm",
                        f"ROLLBACK:{change.get('change_id') or ''}",
                    ]
                ),
            },
            "manual_steps": [
                "先阅读 source_diff_path 和 reverse_patch_path，确认目标修改记录无误。",
                "在业务仓库中执行 apply_reverse_patch_check，确认反向补丁可应用。",
                "旧记录可人工执行 apply_reverse_patch；具备完整后置哈希的记录可使用 apply_transactional_rollback。",
                "事务回滚必须提供精确 ROLLBACK:<change_id> 确认，并会在目标漂移时拒绝修改。",
            ],
            "residual_risk": "该计划只基于登记时保存的 diff 生成；如果业务仓库后续已有其他修改，必须先用 check 命令确认上下文仍匹配。",
        }
        plan_path = output_dir / "rollback_plan.json"
        markdown_path = output_dir / "rollback_plan.md"
        plan["plan_path"] = str(plan_path)
        plan["markdown_path"] = str(markdown_path)
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(rollback_plan_to_markdown(plan), encoding="utf-8")
        return plan

    def apply_change_rollback(self, options: TaskRollbackApplyOptions | dict) -> dict:
        active_options = coerce_task_rollback_apply_options(options)
        task = self.resolve_task(
            task_id=active_options.task_id,
            task_key=active_options.task_key,
            yunxiao_url=active_options.yunxiao_url,
            title=active_options.title,
        )
        task_id = int(task["id"])
        lookup = TaskRollbackPlanOptions(
            task_id=task_id,
            change_id=active_options.change_id,
            target_change_sequence=active_options.target_change_sequence,
        )
        change = resolve_change_for_rollback(task_id=task_id, options=lookup)
        if change is None:
            raise KeyError("未找到可回滚的修改记录。")
        change_id = str(change.get("change_id") or "")
        expected_confirmation = f"ROLLBACK:{change_id}"
        if active_options.confirmation != expected_confirmation:
            raise PermissionError(f"实际本地回滚必须提供精确确认：{expected_confirmation}")
        diff_path = Path(str(change.get("diff_path") or "")).expanduser().resolve()
        if not diff_path.is_file():
            raise FileNotFoundError(f"修改记录 diff 不存在：{diff_path}")
        diff_text = diff_path.read_text(encoding="utf-8", errors="strict")
        actual_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
        if actual_hash != str(change.get("diff_sha256") or ""):
            raise ValueError("修改记录 diff 哈希不一致，拒绝实际回滚。")
        metadata = dict(change.get("metadata") or {})
        post_file_states = metadata.get("post_file_states") or {}
        project_path = str(change.get("project_path") or first_text(task.get("project_paths") or []) or "").strip()
        if not project_path:
            raise ValueError("修改记录缺少本地业务仓库路径。")
        result = rollback_applied_diff_in_project(
            project_path=Path(project_path),
            final_diff=diff_text,
            expected_post_file_states=post_file_states,
            rollback_id=change_id,
            verify_commands=active_options.verify_commands or [],
        )
        metadata["latest_rollback"] = {
            "status": result.get("status"),
            "idempotent": bool(result.get("idempotent")),
            "transaction": result.get("transaction") or {},
            "completed_at": database.now_iso(),
        }
        rollback_status = "completed" if result.get("status") == "success" else str(result.get("status") or "failed")
        database.update_task_change(
            int(change["id"]),
            rollback_mode="local_transaction",
            rollback_status=rollback_status,
            metadata=metadata,
        )
        if result.get("status") == "success":
            database.update_task(
                task_id,
                current_stage="local_rollback",
                status="local_rolled_back",
                can_commit=False,
                can_yunxiao_transition=False,
            )
        return {
            **result,
            "task_id": task_id,
            "task_key": task.get("task_key") or "",
            "change_id": change_id,
            "remote_actions": "disabled",
        }

    def build_dashboard(self, limit: int = 50, filters: TaskDashboardFilters | None = None) -> dict:
        active_filters = filters or TaskDashboardFilters()
        tasks = self.list_tasks(limit=limit)
        items = []
        runs_by_task_id: dict[int, list[dict]] = {}
        for task in tasks:
            runs_by_task_id[int(task["id"])] = self.list_task_runs(int(task["id"]))
            item = build_dashboard_task_item(task=task, runs=runs_by_task_id[int(task["id"])])
            if dashboard_item_matches_filters(item, active_filters):
                items.append(item)
        total_runs = sum(len(runs_by_task_id.get(int(item["task_id"]), [])) for item in items if item.get("task_id") is not None)
        status_counts: dict[str, int] = {}
        verification_counts: dict[str, int] = {}
        ui_evidence_counts: dict[str, int] = {}
        can_commit_count = 0
        for item in items:
            status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
            verification_key = item.get("verification_status") or "unknown"
            verification_counts[verification_key] = verification_counts.get(verification_key, 0) + 1
            ui_key = item.get("ui_evidence", {}).get("status") or "missing"
            ui_evidence_counts[ui_key] = ui_evidence_counts.get(ui_key, 0) + 1
            if item.get("can_commit"):
                can_commit_count += 1
        return {
            "version": "0.10.10-task-dashboard",
            "generated_at": database.now_iso(),
            "readonly": True,
            "yunxiao_write_enabled": False,
            "filters": dashboard_filters_to_dict(active_filters),
            "summary": {
                "task_count": len(items),
                "run_count": total_runs,
                "can_commit_count": can_commit_count,
                "status_counts": status_counts,
                "verification_status_counts": verification_counts,
                "ui_evidence_status_counts": ui_evidence_counts,
            },
            "sample_set": build_dashboard_sample_set(items),
            "tasks": items,
        }

    def write_dashboard_outputs(
        self,
        *,
        output_dir: Path | str,
        dashboard: dict | None = None,
        limit: int = 50,
        filters: TaskDashboardFilters | None = None,
    ) -> dict:
        target_dir = Path(output_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        data = dashboard or self.build_dashboard(limit=limit, filters=filters)
        if "sample_set" not in data:
            data = {**data, "sample_set": build_dashboard_sample_set(data.get("tasks") or [])}
        files = {
            "json": target_dir / "task_dashboard.json",
            "markdown": target_dir / "task_dashboard.md",
            "html": target_dir / "task_dashboard.html",
            "sample_set_json": target_dir / "task_sample_set.json",
            "sample_set_markdown": target_dir / "task_sample_set.md",
        }
        files["json"].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        files["markdown"].write_text(task_dashboard_to_markdown(data), encoding="utf-8")
        files["html"].write_text(task_dashboard_to_html(data), encoding="utf-8")
        files["sample_set_json"].write_text(json.dumps(data["sample_set"], ensure_ascii=False, indent=2), encoding="utf-8")
        files["sample_set_markdown"].write_text(task_sample_set_to_markdown(data["sample_set"]), encoding="utf-8")
        return {key: str(path) for key, path in files.items()}

    def build_task_workbench(self, *, task_id: int | None = None, task_key: str = "", yunxiao_url: str = "", title: str = "") -> dict:
        task = self.resolve_task(task_id=task_id, task_key=task_key, yunxiao_url=yunxiao_url, title=title)
        runs = self.list_task_runs(int(task["id"]))
        item = build_dashboard_task_item(task=task, runs=runs)
        latest_manifest = read_latest_precommit_manifest(task)
        comparison = build_run_history_comparison(runs)
        warnings = build_task_evidence_warnings(task=task, runs=runs, comparison=comparison)
        requirement_calibration = build_requirement_calibration_summary(task.get("latest_artifacts") or (runs[0].get("artifact_paths") if runs else {}) or {})
        requirement_evidence = build_requirement_evidence_summary(task.get("latest_artifacts") or (runs[0].get("artifact_paths") if runs else {}) or {})
        change_history = self.build_task_change_history(int(task["id"]))
        delivery = build_delivery_workbench_projection(self.list_delivery_transactions(int(task["id"])))
        return {
            "version": "0.24-task-workbench",
            "generated_at": database.now_iso(),
            "readonly": True,
            "yunxiao_write_enabled": False,
            "task": item,
            "runs": [build_workbench_run_detail(run) for run in runs],
            "artifacts": build_workbench_artifact_index(task=task, runs=runs),
            "change_history": change_history,
            "delivery": delivery,
            "requirement_calibration": requirement_calibration,
            "requirement_evidence": requirement_evidence,
            "run_history_comparison": comparison,
            "evidence_warnings": warnings,
            "commands": build_workbench_commands(task=task, latest_manifest=latest_manifest),
            "residual_risk": "本地工作台只读取 Task Manager 数据库、修改历史账本和既有产物索引；回滚只提供 dry-run 计划和命令，不自动修改业务仓库。",
        }

    def write_workbench_outputs(self, *, output_dir: Path | str, workbench: dict | None = None, task_id: int | None = None, task_key: str = "") -> dict:
        target_dir = Path(output_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        data = workbench or self.build_task_workbench(task_id=task_id, task_key=task_key)
        files = {
            "json": target_dir / "task_workbench.json",
            "markdown": target_dir / "task_workbench.md",
            "change_history_json": target_dir / "task_change_history.json",
            "change_history_markdown": target_dir / "task_change_history.md",
        }
        files["json"].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        files["markdown"].write_text(task_workbench_to_markdown(data), encoding="utf-8")
        files["change_history_json"].write_text(json.dumps(data.get("change_history") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        files["change_history_markdown"].write_text(task_change_history_to_markdown(data.get("change_history") or {}), encoding="utf-8")
        files.update(write_requirement_calibration_copies(target_dir=target_dir, summary=data.get("requirement_calibration") or {}))
        files.update(write_requirement_evidence_copies(target_dir=target_dir, summary=data.get("requirement_evidence") or {}))
        return {key: str(path) for key, path in files.items()}

    def build_task_workspace(
        self,
        limit: int = 50,
        filters: TaskDashboardFilters | None = None,
        config_summary: dict | None = None,
        config_preview: dict | None = None,
        config_share_validation: dict | None = None,
        config_import_draft: dict | None = None,
        config_import_review: dict | None = None,
        config_template_index: dict | None = None,
        config_wizard: dict | None = None,
    ) -> dict:
        dashboard = self.build_dashboard(limit=limit, filters=filters)
        entries = []
        task_details = []
        for item in dashboard.get("tasks") or []:
            task_key = str(item.get("task_key") or item.get("task_id") or "task")
            task_slug = safe_slug(task_key)
            workbench = self.build_task_workbench(task_id=int(item["task_id"])) if item.get("task_id") is not None else {}
            commands = workbench.get("commands") or {}
            warnings = workbench.get("evidence_warnings") or []
            change_history = workbench.get("change_history") or {}
            calibration = build_workspace_requirement_calibration_entry(
                workbench.get("requirement_calibration") or {},
                task_slug=task_slug,
            )
            requirement_evidence = build_workspace_requirement_evidence_entry(
                workbench.get("requirement_evidence") or {},
                task_slug=task_slug,
            )
            ui = item.get("ui_evidence") or {}
            entry = {
                "task_id": item.get("task_id"),
                "task_key": item.get("task_key"),
                "entity_kind": item.get("entity_kind") or "",
                "entity_id": item.get("entity_id") or "",
                "entity_title": item.get("entity_title") or "",
                "status": item.get("status") or "",
                "verification_status": item.get("verification_status") or "",
                "ui_evidence_status": ui.get("status") or "",
                "latest_run_id": item.get("latest_run_id"),
                "latest_output_dir": item.get("latest_output_dir") or "",
                "artifact_count": len(workbench.get("artifacts") or []),
                "warning_count": len(warnings),
                "warning_codes": [str(warning.get("code") or "") for warning in warnings if warning.get("code")],
                "change_count": int(change_history.get("change_count") or 0),
                "change_history": build_workspace_change_history_entry(change_history, task_slug=task_slug),
                "requirement_calibration": calibration,
                "requirement_evidence": requirement_evidence,
                "workbench_json": f"workbenches/{task_slug}/task_workbench.json",
                "workbench_markdown": f"workbenches/{task_slug}/task_workbench.md",
                "rerun_precommit": commands.get("rerun_precommit") or "",
            }
            detail = build_workspace_task_detail(entry=entry, workbench=workbench, task_slug=task_slug)
            entry["detail_id"] = detail.get("detail_id") or ""
            entry["filter_data"] = build_workspace_entry_filter_data(entry)
            entry["search_text"] = build_workspace_entry_search_text(entry)
            entries.append(entry)
            task_details.append(detail)
        workspace = {
            "version": (
                "0.33-task-workspace"
                if config_wizard
                else (
                    "0.29-task-workspace"
                    if config_template_index
                    else (
                        "0.28-task-workspace"
                        if config_import_review
                        else (
                            "0.27-task-workspace"
                            if config_import_draft
                            else (
                                "0.26-task-workspace"
                                if config_share_validation
                                else ("0.25-task-workspace" if config_preview else ("0.22-task-workspace" if config_summary else "0.21-task-workspace"))
                            )
                        )
                    )
                )
            ),
            "generated_at": database.now_iso(),
            "readonly": True,
            "yunxiao_write_enabled": False,
            "filters": dashboard.get("filters") or {},
            "summary": dashboard.get("summary") or {},
            "sample_set": dashboard.get("sample_set") or {},
            "navigation": build_workspace_navigation(
                include_configuration=bool(config_summary),
                include_configuration_preview=bool(config_preview),
                include_config_share_validation=bool(config_share_validation),
                include_config_import_draft=bool(config_import_draft),
                include_config_import_review=bool(config_import_review),
                include_config_template_index=bool(config_template_index),
                include_config_wizard=bool(config_wizard),
                include_config_review_package=bool(config_wizard),
            ),
            "ui_polish": build_workspace_ui_polish(),
            "warning_summary": build_workspace_warning_summary(entries),
            "filter_options": build_workspace_filter_options(entries),
            "links": {
                "dashboard_html": "task_dashboard.html",
                "dashboard_json": "task_dashboard.json",
                "dashboard_markdown": "task_dashboard.md",
                "sample_set_json": "task_sample_set.json",
                "sample_set_markdown": "task_sample_set.md",
                "export_index_json": "task_workspace_export_index.json",
                "export_index_markdown": "task_workspace_export_index.md",
                "snapshot_comparison_json": "task_workspace_snapshot_comparison.json",
                "snapshot_comparison_markdown": "task_workspace_snapshot_comparison.md",
                "snapshot_history_json": "task_workspace_snapshot_history.json",
                "snapshot_history_markdown": "task_workspace_snapshot_history.md",
                "evidence_trend_json": "task_workspace_evidence_trend.json",
                "evidence_trend_markdown": "task_workspace_evidence_trend.md",
                "offline_review_json": "task_workspace_offline_review.json",
                "offline_review_markdown": "task_workspace_offline_review.md",
            },
            "entries": entries,
            "task_details": task_details,
            "dashboard": dashboard,
            "residual_risk": "本地 HTML 工作台只读取 Task Manager 数据库和既有产物索引；页面链接、快照浏览、趋势、对比和复跑命令仅供人工查看或复制，不自动执行。",
        }
        if config_summary:
            workspace["configuration"] = config_summary
            workspace["links"]["config_summary_json"] = "task_workspace_config_summary.json"
            workspace["links"]["config_summary_markdown"] = "task_workspace_config_summary.md"
            workspace["residual_risk"] += " v0.22 配置中心摘要仅在显式传入时展示，默认旧命令不读取、不应用配置规则。"
        if config_preview:
            workspace["configuration_preview"] = config_preview
            workspace["links"]["config_preview_json"] = "task_workspace_config_preview.json"
            workspace["links"]["config_preview_markdown"] = "task_workspace_config_preview.md"
            workspace["residual_risk"] += " v0.25 配置预览仅在显式传入时展示，只生成本地 provider 模板草案，不读取远端、不保存真实 token、不执行外部写入。"
        if config_share_validation:
            workspace["config_share_validation"] = config_share_validation
            workspace["links"]["config_share_validation_json"] = "task_workspace_config_share_validation.json"
            workspace["links"]["config_share_validation_markdown"] = "task_workspace_config_share_validation.md"
            workspace["residual_risk"] += " v0.26 配置分享校验仅在显式传入时展示，只检查本地模板风险和覆盖策略，不会应用配置、不写本机文件。"
        if config_import_draft:
            workspace["config_import_draft"] = config_import_draft
            workspace["links"]["config_import_draft_json"] = "task_workspace_config_import_draft.json"
            workspace["links"]["config_import_draft_markdown"] = "task_workspace_config_import_draft.md"
            workspace["residual_risk"] += " v0.27 配置导入草案仅在显式传入时生成到用户选择目录；workspace 只展示只读索引和人工复制命令，不会应用配置、不写 ~/.his-harness。"
        if config_import_review:
            workspace["config_import_review"] = config_import_review
            workspace["links"]["config_import_review_json"] = "task_workspace_config_import_review.json"
            workspace["links"]["config_import_review_markdown"] = "task_workspace_config_import_review.md"
            workspace["residual_risk"] += " v0.28 配置导入回读校验仅在显式传入时展示，只回读用户选择目录中的草案文件和只读表单预览，不会应用配置、不写 ~/.his-harness。"
        if config_template_index:
            workspace["config_template_index"] = config_template_index
            workspace["links"]["config_template_index_json"] = "task_workspace_config_template_index.json"
            workspace["links"]["config_template_index_markdown"] = "task_workspace_config_template_index.md"
            workspace["residual_risk"] += " v0.29 配置模板索引仅在显式传入时展示，只对本地草案做只读差异对比和 profile 切换预览，不会应用配置、不写 ~/.his-harness。"
        if config_wizard:
            workspace["config_wizard"] = config_wizard
            workspace["links"]["config_wizard_json"] = "task_workspace_config_wizard.json"
            workspace["links"]["config_wizard_markdown"] = "task_workspace_config_wizard.md"
            workspace["links"]["config_review_package_json"] = "task_workspace_config_review_package.json"
            workspace["links"]["config_review_package_markdown"] = "task_workspace_config_review_package.md"
            workspace["residual_risk"] += " v0.32 配置向导仅在显式传入时展示，只聚合本地只读配置检查、草案回读、模板索引和离线审查包索引；步骤筛选、命令复制和审查包索引只服务离线阅读，不会应用配置、不写 ~/.his-harness。"
        return workspace

    def write_workspace_outputs(
        self,
        *,
        output_dir: Path | str,
        workspace: dict | None = None,
        limit: int = 50,
        filters: TaskDashboardFilters | None = None,
        config_summary: dict | None = None,
        config_preview: dict | None = None,
        config_share_validation: dict | None = None,
        config_import_draft: dict | None = None,
        config_import_review: dict | None = None,
        config_template_index: dict | None = None,
        config_wizard: dict | None = None,
    ) -> dict:
        target_dir = Path(output_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        previous_workspace = read_json_file(target_dir / "task_workspace.json")
        archive_workspace_snapshot(target_dir=target_dir, workspace=previous_workspace)
        data = workspace or self.build_task_workspace(
            limit=limit,
            filters=filters,
            config_summary=config_summary,
            config_preview=config_preview,
            config_share_validation=config_share_validation,
            config_import_draft=config_import_draft,
            config_import_review=config_import_review,
            config_template_index=config_template_index,
            config_wizard=config_wizard,
        )
        dashboard = data.get("dashboard") if isinstance(data.get("dashboard"), dict) else self.build_dashboard(limit=limit, filters=filters)
        dashboard_files = self.write_dashboard_outputs(output_dir=target_dir, dashboard=dashboard)

        workbench_files = {}
        for entry in data.get("entries") or []:
            task_key = str(entry.get("task_key") or entry.get("task_id") or "task")
            workbench_dir = target_dir / "workbenches" / safe_slug(task_key)
            task_id = entry.get("task_id")
            if task_id is not None:
                workbench = self.build_task_workbench(task_id=int(task_id))
            else:
                workbench = self.build_task_workbench(task_key=task_key)
            workbench_files[task_key] = self.write_workbench_outputs(output_dir=workbench_dir, workbench=workbench)

        files = {
            "json": target_dir / "task_workspace.json",
            "html": target_dir / "task_workspace.html",
            "export_index_json": target_dir / "task_workspace_export_index.json",
            "export_index_markdown": target_dir / "task_workspace_export_index.md",
            "snapshot_comparison_json": target_dir / "task_workspace_snapshot_comparison.json",
            "snapshot_comparison_markdown": target_dir / "task_workspace_snapshot_comparison.md",
            "snapshot_history_json": target_dir / "task_workspace_snapshot_history.json",
            "snapshot_history_markdown": target_dir / "task_workspace_snapshot_history.md",
            "evidence_trend_json": target_dir / "task_workspace_evidence_trend.json",
            "evidence_trend_markdown": target_dir / "task_workspace_evidence_trend.md",
            "offline_review_json": target_dir / "task_workspace_offline_review.json",
            "offline_review_markdown": target_dir / "task_workspace_offline_review.md",
        }
        has_configuration = bool(data.get("configuration"))
        has_configuration_preview = bool(data.get("configuration_preview"))
        has_config_share_validation = bool(data.get("config_share_validation"))
        has_config_import_draft = bool(data.get("config_import_draft"))
        has_config_import_review = bool(data.get("config_import_review"))
        has_config_template_index = bool(data.get("config_template_index"))
        has_config_wizard = bool(data.get("config_wizard"))
        has_config_review_package = has_config_wizard
        if has_configuration:
            files["config_summary_json"] = target_dir / "task_workspace_config_summary.json"
            files["config_summary_markdown"] = target_dir / "task_workspace_config_summary.md"
        if has_configuration_preview:
            files["config_preview_json"] = target_dir / "task_workspace_config_preview.json"
            files["config_preview_markdown"] = target_dir / "task_workspace_config_preview.md"
        if has_config_share_validation:
            files["config_share_validation_json"] = target_dir / "task_workspace_config_share_validation.json"
            files["config_share_validation_markdown"] = target_dir / "task_workspace_config_share_validation.md"
        if has_config_import_draft:
            files["config_import_draft_json"] = target_dir / "task_workspace_config_import_draft.json"
            files["config_import_draft_markdown"] = target_dir / "task_workspace_config_import_draft.md"
        if has_config_import_review:
            files["config_import_review_json"] = target_dir / "task_workspace_config_import_review.json"
            files["config_import_review_markdown"] = target_dir / "task_workspace_config_import_review.md"
        if has_config_template_index:
            files["config_template_index_json"] = target_dir / "task_workspace_config_template_index.json"
            files["config_template_index_markdown"] = target_dir / "task_workspace_config_template_index.md"
        if has_config_wizard:
            files["config_wizard_json"] = target_dir / "task_workspace_config_wizard.json"
            files["config_wizard_markdown"] = target_dir / "task_workspace_config_wizard.md"
        if has_config_review_package:
            files["config_review_package_json"] = target_dir / "task_workspace_config_review_package.json"
            files["config_review_package_markdown"] = target_dir / "task_workspace_config_review_package.md"
        links = dict(data.get("links") or {})
        links.update(
            {
                "export_index_json": "task_workspace_export_index.json",
                "export_index_markdown": "task_workspace_export_index.md",
                "snapshot_comparison_json": "task_workspace_snapshot_comparison.json",
                "snapshot_comparison_markdown": "task_workspace_snapshot_comparison.md",
                "snapshot_history_json": "task_workspace_snapshot_history.json",
                "snapshot_history_markdown": "task_workspace_snapshot_history.md",
                "evidence_trend_json": "task_workspace_evidence_trend.json",
                "evidence_trend_markdown": "task_workspace_evidence_trend.md",
                "offline_review_json": "task_workspace_offline_review.json",
                "offline_review_markdown": "task_workspace_offline_review.md",
            }
        )
        if has_configuration:
            links.update(
                {
                    "config_summary_json": "task_workspace_config_summary.json",
                    "config_summary_markdown": "task_workspace_config_summary.md",
                }
            )
        if has_configuration_preview:
            links.update(
                {
                    "config_preview_json": "task_workspace_config_preview.json",
                    "config_preview_markdown": "task_workspace_config_preview.md",
                }
            )
        if has_config_share_validation:
            links.update(
                {
                    "config_share_validation_json": "task_workspace_config_share_validation.json",
                    "config_share_validation_markdown": "task_workspace_config_share_validation.md",
                }
            )
        if has_config_import_draft:
            links.update(
                {
                    "config_import_draft_json": "task_workspace_config_import_draft.json",
                    "config_import_draft_markdown": "task_workspace_config_import_draft.md",
                }
            )
        if has_config_import_review:
            links.update(
                {
                    "config_import_review_json": "task_workspace_config_import_review.json",
                    "config_import_review_markdown": "task_workspace_config_import_review.md",
                }
            )
        if has_config_template_index:
            links.update(
                {
                    "config_template_index_json": "task_workspace_config_template_index.json",
                    "config_template_index_markdown": "task_workspace_config_template_index.md",
                }
            )
        if has_config_wizard:
            links.update(
                {
                    "config_wizard_json": "task_workspace_config_wizard.json",
                    "config_wizard_markdown": "task_workspace_config_wizard.md",
                    "config_review_package_json": "task_workspace_config_review_package.json",
                    "config_review_package_markdown": "task_workspace_config_review_package.md",
                }
            )
        data["links"] = links
        data["snapshot_comparison"] = build_workspace_snapshot_comparison(previous_workspace=previous_workspace, current_workspace=data)
        data["snapshot_history"] = build_workspace_snapshot_history(target_dir=target_dir, current_workspace=data)
        data["evidence_trend"] = build_workspace_evidence_trend(
            snapshot_history=data["snapshot_history"],
            target_dir=target_dir,
            current_workspace=data,
        )
        data["snapshot_detail"] = build_workspace_snapshot_detail(
            snapshot_history=data["snapshot_history"],
            target_dir=target_dir,
            current_workspace=data,
        )
        data["export_index"] = build_workspace_export_index(
            workspace=data,
            target_dir=target_dir,
            workspace_files=files,
            dashboard_files=dashboard_files,
            workbench_files=workbench_files,
        )
        data["offline_review"] = build_workspace_offline_review(workspace=data, target_dir=target_dir)
        if has_config_review_package:
            data["config_review_package_index"] = build_configuration_review_package_index(
                workspace=data,
                target_dir=target_dir,
            )
        files["json"].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        files["html"].write_text(task_workspace_to_html(data), encoding="utf-8")
        files["export_index_json"].write_text(json.dumps(data["export_index"], ensure_ascii=False, indent=2), encoding="utf-8")
        files["export_index_markdown"].write_text(workspace_export_index_to_markdown(data["export_index"]), encoding="utf-8")
        files["snapshot_comparison_json"].write_text(json.dumps(data["snapshot_comparison"], ensure_ascii=False, indent=2), encoding="utf-8")
        files["snapshot_comparison_markdown"].write_text(workspace_snapshot_comparison_to_markdown(data["snapshot_comparison"]), encoding="utf-8")
        files["snapshot_history_json"].write_text(json.dumps(data["snapshot_history"], ensure_ascii=False, indent=2), encoding="utf-8")
        files["snapshot_history_markdown"].write_text(workspace_snapshot_history_to_markdown(data["snapshot_history"]), encoding="utf-8")
        files["evidence_trend_json"].write_text(json.dumps(data["evidence_trend"], ensure_ascii=False, indent=2), encoding="utf-8")
        files["evidence_trend_markdown"].write_text(workspace_evidence_trend_to_markdown(data["evidence_trend"]), encoding="utf-8")
        files["offline_review_json"].write_text(json.dumps(data["offline_review"], ensure_ascii=False, indent=2), encoding="utf-8")
        files["offline_review_markdown"].write_text(workspace_offline_review_to_markdown(data["offline_review"]), encoding="utf-8")
        if has_config_review_package:
            files["config_review_package_json"].write_text(json.dumps(data["config_review_package_index"], ensure_ascii=False, indent=2), encoding="utf-8")
            files["config_review_package_markdown"].write_text(configuration_review_package_index_to_markdown(data["config_review_package_index"]), encoding="utf-8")
        if has_configuration:
            files["config_summary_json"].write_text(json.dumps(data["configuration"], ensure_ascii=False, indent=2), encoding="utf-8")
            files["config_summary_markdown"].write_text(config_summary_to_markdown(data["configuration"]), encoding="utf-8")
        if has_configuration_preview:
            files["config_preview_json"].write_text(json.dumps(data["configuration_preview"], ensure_ascii=False, indent=2), encoding="utf-8")
            files["config_preview_markdown"].write_text(configuration_preview_to_markdown(data["configuration_preview"]), encoding="utf-8")
        if has_config_share_validation:
            files["config_share_validation_json"].write_text(json.dumps(data["config_share_validation"], ensure_ascii=False, indent=2), encoding="utf-8")
            files["config_share_validation_markdown"].write_text(configuration_share_validation_to_markdown(data["config_share_validation"]), encoding="utf-8")
        if has_config_import_draft:
            files["config_import_draft_json"].write_text(json.dumps(data["config_import_draft"], ensure_ascii=False, indent=2), encoding="utf-8")
            files["config_import_draft_markdown"].write_text(configuration_import_draft_to_markdown(data["config_import_draft"]), encoding="utf-8")
        if has_config_import_review:
            files["config_import_review_json"].write_text(json.dumps(data["config_import_review"], ensure_ascii=False, indent=2), encoding="utf-8")
            files["config_import_review_markdown"].write_text(configuration_import_review_to_markdown(data["config_import_review"]), encoding="utf-8")
        if has_config_template_index:
            files["config_template_index_json"].write_text(json.dumps(data["config_template_index"], ensure_ascii=False, indent=2), encoding="utf-8")
            files["config_template_index_markdown"].write_text(configuration_template_index_to_markdown(data["config_template_index"]), encoding="utf-8")
        if has_config_wizard:
            files["config_wizard_json"].write_text(json.dumps(data["config_wizard"], ensure_ascii=False, indent=2), encoding="utf-8")
            files["config_wizard_markdown"].write_text(configuration_wizard_to_markdown(data["config_wizard"]), encoding="utf-8")
        archive_workspace_snapshot(target_dir=target_dir, workspace=data)
        result = {
            "json": str(files["json"]),
            "html": str(files["html"]),
            "export_index_json": str(files["export_index_json"]),
            "export_index_markdown": str(files["export_index_markdown"]),
            "snapshot_comparison_json": str(files["snapshot_comparison_json"]),
            "snapshot_comparison_markdown": str(files["snapshot_comparison_markdown"]),
            "snapshot_history_json": str(files["snapshot_history_json"]),
            "snapshot_history_markdown": str(files["snapshot_history_markdown"]),
            "evidence_trend_json": str(files["evidence_trend_json"]),
            "evidence_trend_markdown": str(files["evidence_trend_markdown"]),
            "offline_review_json": str(files["offline_review_json"]),
            "offline_review_markdown": str(files["offline_review_markdown"]),
            "dashboard_json": dashboard_files["json"],
            "dashboard_markdown": dashboard_files["markdown"],
            "dashboard_html": dashboard_files["html"],
            "sample_set_json": dashboard_files["sample_set_json"],
            "sample_set_markdown": dashboard_files["sample_set_markdown"],
            "workbench_files": workbench_files,
        }
        if has_configuration:
            result["config_summary_json"] = str(files["config_summary_json"])
            result["config_summary_markdown"] = str(files["config_summary_markdown"])
        if has_configuration_preview:
            result["config_preview_json"] = str(files["config_preview_json"])
            result["config_preview_markdown"] = str(files["config_preview_markdown"])
        if has_config_share_validation:
            result["config_share_validation_json"] = str(files["config_share_validation_json"])
            result["config_share_validation_markdown"] = str(files["config_share_validation_markdown"])
        if has_config_import_draft:
            result["config_import_draft_json"] = str(files["config_import_draft_json"])
            result["config_import_draft_markdown"] = str(files["config_import_draft_markdown"])
        if has_config_import_review:
            result["config_import_review_json"] = str(files["config_import_review_json"])
            result["config_import_review_markdown"] = str(files["config_import_review_markdown"])
        if has_config_template_index:
            result["config_template_index_json"] = str(files["config_template_index_json"])
            result["config_template_index_markdown"] = str(files["config_template_index_markdown"])
        if has_config_wizard:
            result["config_wizard_json"] = str(files["config_wizard_json"])
            result["config_wizard_markdown"] = str(files["config_wizard_markdown"])
        if has_config_review_package:
            result["config_review_package_json"] = str(files["config_review_package_json"])
            result["config_review_package_markdown"] = str(files["config_review_package_markdown"])
        return result

    def run_task(self, options: TaskRunOptions) -> tuple[dict, WorkflowResult, Path]:
        """Run a task and converge startup/output failures into a durable state."""
        try:
            return self._run_task_impl(options)
        except Exception as exc:
            try:
                task = self.resolve_task(
                    task_id=options.task_id,
                    task_key=options.task_key,
                    yunxiao_url=options.yunxiao_url,
                    title=options.title,
                )
                database.update_task(
                    int(task["id"]),
                    current_stage="failure",
                    status="failed",
                    failure_stage="task_manager",
                    recovery_action="检查运行前诊断、项目路径、数据库/产物目录和验证命令后重试；原有产物不自动删除。",
                    retryable=True,
                    can_commit=False,
                    can_yunxiao_transition=False,
                    notes=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                # The original failure is more useful than a secondary lookup
                # failure; callers still receive a non-running outcome.
                pass
            raise

    def _run_task_impl(self, options: TaskRunOptions) -> tuple[dict, WorkflowResult, Path]:
        task = self.resolve_task(
            task_id=options.task_id,
            task_key=options.task_key,
            yunxiao_url=options.yunxiao_url,
            title=options.title,
        )
        if options.load_claude_settings:
            import os

            os.environ["HARNESS_LOAD_CLAUDE_SETTINGS"] = "1"
            load_claude_settings_env_if_requested()
        demand_text = resolve_demand_text(options=options, task=task)
        multi_service_evidence = load_structured_evidence_file(options.multi_service_evidence_file)
        title = options.title or task.get("entity_title") or task.get("entity_id") or "手工需求"
        project_paths = unique_keep_order(options.project_paths or task.get("project_paths") or [])
        project_root = options.project_root or task.get("project_root") or DEFAULT_PROJECT_ROOT
        yunxiao_url = options.yunxiao_url or task.get("entity_url") or ""
        output_dir = build_task_output_dir(output_root=options.output_root, task=task, execution_mode=options.execution_mode)
        database.update_task(
            int(task["id"]),
            current_stage=stage_for_execution_mode(options.execution_mode),
            status="running",
            project_root=project_root,
            project_paths=project_paths,
            entity_url=yunxiao_url,
            entity_title=title,
        )
        runner = RequirementWorkflowRunner(
            mode=options.mode,
            allow_mock=options.mode == "mock",
            max_retries=options.max_retries,
        )
        result = runner.run(
            title=title,
            demand_text=demand_text,
            source_type="yunxiao" if yunxiao_url else "manual",
            project_path=project_paths,
            project_root=project_root,
            execution_mode=options.execution_mode,
            worktree_dir=options.worktree_dir,
            allowed_paths=options.allowed_paths or [],
            verify_commands=options.verify_commands or [],
            max_edit_rounds=options.max_edit_rounds,
            pre_change_confirmation=options.pre_change_confirmation,
            review_commit=options.review_commit,
            review_base=options.review_base,
            yunxiao_read=bool(yunxiao_url),
            yunxiao_include_comments=options.yunxiao_include_comments,
            yunxiao_url=yunxiao_url,
            yunxiao_output_dir=output_dir / "_yunxiao_evidence" if yunxiao_url else None,
            yunxiao_transaction_mode="off",
            yunxiao_entity_kind=task.get("entity_kind") or infer_entity_kind(yunxiao_url),
            yunxiao_entity_id=task.get("entity_id") or parse_work_item_id(yunxiao_url),
            requirement_evidence_file=options.requirement_evidence_file or None,
            multi_service_evidence=multi_service_evidence,
        )
        written_dir = write_run_outputs(result.run_id, output_dir)
        run = database.get_run(result.run_id) or {}
        latest_artifacts = build_latest_artifacts(output_dir=written_dir)
        verification_status = infer_verification_status(run=run, result=result)
        database.add_task_run(
            {
                "task_id": task["id"],
                "run_id": result.run_id,
                "stage": stage_for_execution_mode(options.execution_mode),
                "execution_mode": options.execution_mode,
                "status": result.status,
                "evaluation_status": result.evaluation_status,
                "output_dir": str(written_dir),
                "summary": run.get("error") or run.get("evaluation_summary") or "",
                "verification_status": verification_status,
                "failure_stage": "",
                "recovery_action": "",
                "retryable": False,
                "artifact_paths": latest_artifacts,
                "started_at": run.get("started_at") or database.now_iso(),
                "finished_at": run.get("finished_at"),
            }
        )
        database.update_task(
            int(task["id"]),
            current_stage=stage_for_execution_mode(options.execution_mode),
            status=result.status,
            latest_run_id=result.run_id,
            latest_output_dir=str(written_dir),
            latest_artifacts=latest_artifacts,
            verification_status=verification_status,
            failure_stage="",
            recovery_action="",
            retryable=False,
            can_commit=can_commit_from_output(output_dir=written_dir, status=result.status),
            can_yunxiao_transition=False,
        )
        return self.get_task(int(task["id"])), result, written_dir

    def record_existing_run(self, options: TaskExistingRunOptions) -> tuple[dict, dict]:
        output_dir = Path(options.output_dir).expanduser().resolve() if options.output_dir else None
        if output_dir is None:
            raise ValueError("请提供 --output-dir。")
        if not output_dir.is_dir():
            raise FileNotFoundError(f"产物目录不存在：{output_dir}")

        output_summary = read_existing_output_summary(output_dir)
        output_source_run_id = parse_optional_run_id(output_summary.get("source_run_id"))
        requested_source_run_id = options.source_run_id
        if requested_source_run_id is not None and output_source_run_id is None:
            raise ValueError("--source-run-id 仅能绑定包含原始 run.id 的核心闭环输出目录。")
        if requested_source_run_id is not None and output_source_run_id is not None and requested_source_run_id != output_source_run_id:
            raise ValueError("--source-run-id 与输出目录 run.json 中的 run.id 不一致，拒绝错误关联。")
        source_run_id = requested_source_run_id if requested_source_run_id is not None else output_source_run_id
        if source_run_id is not None and database.get_run(source_run_id) is None:
            raise KeyError(f"source_run_id 不存在于当前 Harness 数据库：{source_run_id}")
        title = options.title or output_summary.get("title") or "手工需求"
        entity_id = normalize_entity_id(options.entity_id or output_summary.get("entity_id") or parse_work_item_id(options.yunxiao_url) or parse_work_item_id(title))
        entity_kind = options.entity_kind or infer_entity_kind(options.yunxiao_url)
        project_root = options.project_root or output_summary.get("project_root") or DEFAULT_PROJECT_ROOT
        project_paths = unique_keep_order(options.project_paths or output_summary.get("project_paths") or [])
        yunxiao_url = options.yunxiao_url or ""

        if options.task_id or options.task_key:
            task = self.resolve_task(task_id=options.task_id, task_key=options.task_key)
            database.update_task(
                int(task["id"]),
                entity_kind=entity_kind or task.get("entity_kind") or "",
                entity_id=entity_id or task.get("entity_id") or "",
                entity_title=title or task.get("entity_title") or "",
                entity_url=yunxiao_url or task.get("entity_url") or "",
                project_root=project_root,
                project_paths=project_paths or task.get("project_paths") or [],
                notes=options.notes or task.get("notes") or "",
                metadata=merge_metadata(task.get("metadata") or {}, options.metadata or {}, {"recorded_existing_output": True}),
            )
            task = self.get_task(int(task["id"]))
        else:
            task = self.create_task(
                TaskCreateOptions(
                    yunxiao_url=yunxiao_url,
                    title=title,
                    entity_kind=entity_kind,
                    entity_id=entity_id,
                    source_type="existing-output",
                    project_root=project_root,
                    project_paths=project_paths,
                    notes=options.notes,
                    metadata=merge_metadata(options.metadata or {}, {"recorded_existing_output": True}),
                )
            )

        status = options.status or output_summary.get("status") or "success"
        evaluation_status = options.evaluation_status or output_summary.get("evaluation_status") or ""
        verification_status = output_summary.get("verification_status") or infer_existing_output_verification_status(
            output_summary=output_summary,
            status=status,
            evaluation_status=evaluation_status,
        )
        summary = output_summary.get("summary") or "登记已有 Harness 产物目录。"
        demand_text = output_summary.get("demand_text") or title
        existing_task_run = database.get_task_run_by_output_dir(int(task["id"]), str(output_dir), options.execution_mode)
        if existing_task_run is not None:
            database.update_task(
                int(task["id"]),
                current_stage=stage_for_execution_mode(options.execution_mode),
                status=status,
                latest_run_id=existing_task_run.get("run_id"),
                latest_output_dir=str(output_dir),
                latest_artifacts=build_latest_artifacts(output_dir=output_dir),
                verification_status=verification_status,
                can_commit=bool(output_summary.get("can_commit")),
                can_yunxiao_transition=False,
                project_root=project_root,
                project_paths=project_paths,
            )
            database.update_task_run(
                int(existing_task_run["id"]),
                status=status,
                evaluation_status=evaluation_status,
                summary=summary,
                verification_status=verification_status,
                artifact_paths=build_latest_artifacts(output_dir=output_dir),
            )
            task = self.get_task(int(task["id"]))
            write_task_manager_record_outputs(output_dir=output_dir, task=task, task_run=existing_task_run)
            write_task_manager_run_history(output_dir=output_dir, task=task, runs=self.list_task_runs(int(task["id"])))
            write_ui_evidence_reuse_policy(output_dir=output_dir, task=task)
            artifacts = build_latest_artifacts(output_dir=output_dir)
            database.update_task_run(int(existing_task_run["id"]), artifact_paths=artifacts)
            database.update_task(int(task["id"]), latest_artifacts=artifacts)
            task = self.get_task(int(task["id"]))
            task_run = database.get_task_run(int(existing_task_run["id"])) or existing_task_run
            return task, task_run

        if source_run_id is not None:
            run_id = source_run_id
        else:
            run_id = database.create_run(
                team_key=TEAM_KEY,
                title=title,
                source_type="existing-output",
                demand_text=demand_text,
                total_steps=0,
                llm_mode="recorded",
                llm_model="",
            )
            database.update_run(
                run_id,
                status=status,
                evaluation_status=evaluation_status,
                evaluation_summary=summary,
                finished_at=database.now_iso(),
            )

        artifacts = build_latest_artifacts(output_dir=output_dir)
        task_run_id = database.add_task_run(
            {
                "task_id": task["id"],
                "run_id": run_id,
                "stage": stage_for_execution_mode(options.execution_mode),
                "execution_mode": options.execution_mode,
                "status": status,
                "evaluation_status": evaluation_status,
                "output_dir": str(output_dir),
                "summary": summary,
                "verification_status": verification_status,
                "artifact_paths": artifacts,
                "started_at": database.now_iso(),
                "finished_at": database.now_iso(),
            }
        )
        task_run = database.get_task_run(task_run_id) or database.list_task_runs(int(task["id"]))[0]
        database.update_task(
            int(task["id"]),
            current_stage=stage_for_execution_mode(options.execution_mode),
            status=status,
            latest_run_id=run_id,
            latest_output_dir=str(output_dir),
            latest_artifacts=artifacts,
            verification_status=verification_status,
            can_commit=bool(output_summary.get("can_commit")),
            can_yunxiao_transition=False,
            project_root=project_root,
            project_paths=project_paths,
        )
        task = self.get_task(int(task["id"]))
        write_task_manager_record_outputs(output_dir=output_dir, task=task, task_run={**task_run, "id": task_run_id})
        write_task_manager_run_history(output_dir=output_dir, task=task, runs=self.list_task_runs(int(task["id"])))
        write_ui_evidence_reuse_policy(output_dir=output_dir, task=task)
        artifacts = build_latest_artifacts(output_dir=output_dir)
        database.update_task_run(task_run_id, artifact_paths=artifacts)
        database.update_task(int(task["id"]), latest_artifacts=artifacts)
        task = self.get_task(int(task["id"]))
        task_run = database.list_task_runs(int(task["id"]))[0]
        return task, task_run

    def rerun_precommit(self, options: TaskPrecommitRerunOptions) -> tuple[dict, PrecommitVerificationResult, Path]:
        task = self.resolve_task(
            task_id=options.task_id,
            task_key=options.task_key,
            yunxiao_url=options.yunxiao_url,
            title=options.title,
        )
        demand_text = resolve_demand_text(options=options, task=task)
        title = options.title or task.get("entity_title") or task.get("entity_id") or "手工需求"
        project_root = options.project_root or task.get("project_root") or DEFAULT_PROJECT_ROOT
        latest_manifest = read_latest_precommit_manifest(task)
        project_path = resolve_precommit_project_path(options=options, task=task, latest_manifest=latest_manifest)
        allowed_paths = options.allowed_paths or resolve_precommit_allowed_paths(latest_manifest)
        verify_commands = options.verify_commands or resolve_precommit_verify_commands(latest_manifest)
        if not project_path:
            raise ValueError("复跑 precommit-verify 需要提供 --project-path，或任务历史中存在 project_path。")
        if not allowed_paths:
            raise ValueError("复跑 precommit-verify 需要提供至少一个 --allowed-path，或任务历史中存在 allowed_paths。")

        output_dir = Path(options.output_dir).expanduser().resolve() if options.output_dir else build_task_output_dir(
            output_root=options.output_root,
            task=task,
            execution_mode="precommit-verify",
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        database.update_task(
            int(task["id"]),
            current_stage=stage_for_execution_mode("precommit-verify"),
            status="running",
            project_root=project_root,
            project_paths=unique_keep_order([project_path]),
            entity_title=title,
        )
        run_id = database.create_run(
            team_key=TEAM_KEY,
            title=title,
            source_type="task-manager-precommit-rerun",
            demand_text=demand_text,
            total_steps=0,
            llm_mode="local",
            llm_model="",
        )
        try:
            result = PrecommitVerifier().execute(
                PrecommitVerificationOptions(
                    run_id=run_id,
                    project_root=project_root,
                    project_path=project_path,
                    allowed_paths=allowed_paths,
                    verify_commands=verify_commands,
                    target_key=options.target_key,
                    target_name=options.target_name,
                    target_role=options.target_role,
                    title=title,
                    entity_id=str(task.get("entity_id") or parse_work_item_id(options.yunxiao_url) or ""),
                    demand_text=demand_text,
                    method_test_commands=options.method_test_commands or [],
                    ui_evidence_paths=options.ui_evidence_paths or [],
                    ui_capture_commands=options.ui_capture_commands or [],
                    worktree_root=options.worktree_dir,
                )
            )
        except Exception as exc:
            database.update_run(
                run_id,
                status="failed",
                evaluation_status="failed",
                evaluation_summary=str(exc),
                error=str(exc),
                finished_at=database.now_iso(),
            )
            raise

        write_precommit_result_outputs(result=result, output_dir=output_dir)
        evaluation_status = "pass" if (result.verification_matrix or {}).get("overall_status") == "pass" else "failed"
        verification_status = "passed" if result.status == "success" and evaluation_status == "pass" else "failed"
        database.update_run(
            run_id,
            status=result.status,
            evaluation_status=evaluation_status,
            evaluation_summary=result.summary,
            finished_at=database.now_iso(),
        )
        artifacts = build_latest_artifacts(output_dir=output_dir)
        task_run_id = database.add_task_run(
            {
                "task_id": task["id"],
                "run_id": run_id,
                "stage": stage_for_execution_mode("precommit-verify"),
                "execution_mode": "precommit-verify",
                "status": result.status,
                "evaluation_status": evaluation_status,
                "output_dir": str(output_dir),
                "summary": result.summary,
                "verification_status": verification_status,
                "artifact_paths": artifacts,
                "started_at": database.now_iso(),
                "finished_at": database.now_iso(),
            }
        )
        database.update_task(
            int(task["id"]),
            current_stage=stage_for_execution_mode("precommit-verify"),
            status=result.status,
            latest_run_id=run_id,
            latest_output_dir=str(output_dir),
            latest_artifacts=artifacts,
            verification_status=verification_status,
            can_commit=bool((result.verification_matrix or {}).get("can_commit")),
            can_yunxiao_transition=False,
            project_root=project_root,
            project_paths=unique_keep_order([project_path]),
        )
        task = self.get_task(int(task["id"]))
        task_run = database.get_task_run(task_run_id) or database.list_task_runs(int(task["id"]))[0]
        write_task_manager_record_outputs(output_dir=output_dir, task=task, task_run=task_run)
        write_task_manager_run_history(output_dir=output_dir, task=task, runs=self.list_task_runs(int(task["id"])))
        write_ui_evidence_reuse_policy(output_dir=output_dir, task=task)
        artifacts = build_latest_artifacts(output_dir=output_dir)
        database.update_task_run(task_run_id, artifact_paths=artifacts)
        database.update_task(int(task["id"]), latest_artifacts=artifacts)
        return self.get_task(int(task["id"])), result, output_dir


def resolve_demand_text(*, options: TaskRunOptions | TaskPrecommitRerunOptions, task: dict) -> str:
    if options.demand_file:
        return Path(options.demand_file).expanduser().read_text(encoding="utf-8").strip()
    if options.demand_text.strip():
        return options.demand_text.strip()
    parts = []
    if task.get("entity_title"):
        parts.append(str(task["entity_title"]))
    if task.get("entity_url"):
        parts.append(str(task["entity_url"]))
    if task.get("entity_id"):
        parts.append(str(task["entity_id"]))
    return "\n".join(part for part in parts if part).strip() or "手工需求"


def load_structured_evidence_file(path_text: str) -> dict | None:
    if not path_text:
        return None
    path = Path(path_text).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"多项目证据补充文件无法读取或不是 JSON：{path}；{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("多项目证据补充文件根节点必须是 JSON 对象")
    return payload


def build_latest_artifacts(*, output_dir: Path) -> dict:
    candidates = {
        "report": output_dir / "report.md",
        "run_json": output_dir / "run.json",
        "steps_dir": output_dir / "steps",
        "precommit_manifest": output_dir / "precommit_manifest.json",
        "verification_matrix": output_dir / "verification_matrix.json",
        "verification_matrix_md": output_dir / "verification_matrix.md",
        "behavior_acceptance": output_dir / "behavior_acceptance.json",
        "interaction_evidence": output_dir / "interaction_evidence.json",
        "method_test_runner": output_dir / "method_test_runner.json",
        "ui_evidence_runner": output_dir / "ui_evidence_runner.json",
        "ui_evidence_manifest": output_dir / "ui_evidence_manifest.json",
        "playwright_screenshot_index": output_dir / "playwright_screenshot_index.md",
        "code_review": output_dir / "code_review.md",
        "commit_ready_summary": output_dir / "commit_ready_summary.md",
        "requirement_calibration_json": output_dir / "requirement_calibration.json",
        "requirement_calibration_md": output_dir / "requirement_calibration.md",
        "requirement_evidence_json": output_dir / "requirement_evidence.json",
        "requirement_evidence_md": output_dir / "requirement_evidence.md",
        "multi_service_change_contract_json": output_dir / "multi_service_change_contract.json",
        "multi_service_change_contract_md": output_dir / "multi_service_change_contract.md",
        "multi_service_evidence_selection_json": output_dir / "multi_service_evidence_selection.json",
        "task_manager_record_json": output_dir / "task_manager_real_trial_record.json",
        "task_manager_record_md": output_dir / "task_manager_real_trial_record.md",
        "task_manager_run_history_json": output_dir / "task_manager_run_history.json",
        "task_manager_run_history_md": output_dir / "task_manager_run_history.md",
        "ui_evidence_reuse_policy_json": output_dir / "ui_evidence_reuse_policy.json",
        "ui_evidence_reuse_policy_md": output_dir / "ui_evidence_reuse_policy.md",
        "task_change_history_json": output_dir / "task_change_history.json",
        "task_change_history_md": output_dir / "task_change_history.md",
        "manual_runtime_verification_json": output_dir / "manual_runtime_verification.json",
        "manual_runtime_verification_md": output_dir / "manual_runtime_verification.md",
    }
    return {key: str(path) for key, path in candidates.items() if path.exists()}


def dashboard_filters_to_dict(filters: TaskDashboardFilters | None) -> dict:
    filters = filters or TaskDashboardFilters()
    return {
        "entity_id": filters.entity_id.strip(),
        "task_key": filters.task_key.strip(),
        "entity_kind": filters.entity_kind.strip(),
        "status": filters.status.strip(),
        "verification_status": filters.verification_status.strip(),
        "ui_evidence_status": filters.ui_evidence_status.strip(),
        "can_commit": filters.can_commit,
        "sample_only": bool(filters.sample_only),
    }


def dashboard_item_matches_filters(item: dict, filters: TaskDashboardFilters | None) -> bool:
    filters = filters or TaskDashboardFilters()
    if filters.entity_id and normalize_entity_id(str(item.get("entity_id") or "")) != normalize_entity_id(filters.entity_id):
        return False
    if filters.task_key and normalize_filter_text(item.get("task_key")) != normalize_filter_text(filters.task_key):
        return False
    if filters.entity_kind and normalize_filter_text(item.get("entity_kind")) != normalize_filter_text(filters.entity_kind):
        return False
    if filters.status and normalize_filter_text(item.get("status")) != normalize_filter_text(filters.status):
        return False
    if filters.verification_status and normalize_filter_text(item.get("verification_status")) != normalize_filter_text(filters.verification_status):
        return False
    ui_status = (item.get("ui_evidence") or {}).get("status")
    if filters.ui_evidence_status and normalize_filter_text(ui_status) != normalize_filter_text(filters.ui_evidence_status):
        return False
    if filters.can_commit is not None and bool(item.get("can_commit")) != filters.can_commit:
        return False
    if filters.sample_only and not is_dashboard_sample_item(item):
        return False
    return True


def normalize_filter_text(value: object) -> str:
    return str(value or "").strip().lower()


def is_dashboard_sample_item(item: dict) -> bool:
    metadata = item.get("metadata") or {}
    return bool(item.get("latest_output_dir")) and (
        item.get("source_type") == "existing-output" or bool(metadata.get("recorded_existing_output"))
    )


def build_dashboard_sample_set(items: list[dict]) -> dict:
    samples = [build_dashboard_sample_item(item) for item in items if is_dashboard_sample_item(item)]
    return {
        "version": "0.10.10-real-sample-set",
        "readonly": True,
        "count": len(samples),
        "samples": samples,
    }


def build_dashboard_sample_item(item: dict) -> dict:
    artifacts = item.get("latest_artifacts") or {}
    ui_evidence = item.get("ui_evidence") or {}
    return {
        "sample_id": item.get("task_key") or item.get("task_id"),
        "task_id": item.get("task_id"),
        "task_key": item.get("task_key"),
        "entity_kind": item.get("entity_kind") or "",
        "entity_id": item.get("entity_id") or "",
        "entity_title": item.get("entity_title") or "",
        "source_type": item.get("source_type") or "",
        "status": item.get("status") or "",
        "verification_status": item.get("verification_status") or "",
        "ui_evidence_status": ui_evidence.get("status") or "",
        "can_commit": bool(item.get("can_commit")),
        "latest_run_id": item.get("latest_run_id"),
        "latest_output_dir": item.get("latest_output_dir") or "",
        "latest_artifact_count": item.get("latest_artifact_count") or 0,
        "rerun_ready": bool(item.get("latest_output_dir") and artifacts.get("precommit_manifest") and artifacts.get("verification_matrix")),
        "precommit_manifest": artifacts.get("precommit_manifest") or "",
        "verification_matrix": artifacts.get("verification_matrix") or "",
        "task_manager_record": artifacts.get("task_manager_record_json") or "",
        "ui_evidence_reuse_policy": artifacts.get("ui_evidence_reuse_policy_json") or "",
    }


def build_workbench_run_detail(task_run: dict) -> dict:
    detail = summarize_task_run(task_run)
    detail["artifacts"] = build_artifact_rows(task_run.get("artifact_paths") or {}, source=f"task_run:{task_run.get('id') or '-'}")
    detail["ui_evidence"] = build_ui_evidence_summary(task_run.get("artifact_paths") or {})
    return detail


def build_run_history_comparison(runs: list[dict]) -> dict:
    run_items = [build_comparable_run_item(run) for run in runs]
    latest = run_items[0] if run_items else {}
    previous = run_items[1] if len(run_items) > 1 else {}
    changes = []
    for key, label in [
        ("status", "状态"),
        ("verification_status", "验证状态"),
        ("ui_evidence_status", "UI证据"),
        ("artifact_count", "产物数"),
    ]:
        if latest and previous and latest.get(key) != previous.get(key):
            changes.append(
                {
                    "field": key,
                    "label": label,
                    "latest": latest.get(key),
                    "previous": previous.get(key),
                }
            )
    return {
        "version": "0.13-run-history-comparison",
        "run_count": len(run_items),
        "latest_run": latest,
        "previous_run": previous,
        "changes": changes,
    }


def build_comparable_run_item(task_run: dict) -> dict:
    artifacts = task_run.get("artifact_paths") or {}
    ui = build_ui_evidence_summary(artifacts)
    return {
        "task_run_id": task_run.get("id"),
        "run_id": task_run.get("run_id"),
        "execution_mode": task_run.get("execution_mode") or "",
        "status": task_run.get("status") or "",
        "evaluation_status": task_run.get("evaluation_status") or "",
        "verification_status": task_run.get("verification_status") or "",
        "ui_evidence_status": ui.get("status") or "missing",
        "output_dir": task_run.get("output_dir") or "",
        "artifact_count": len(artifacts),
        "artifact_kinds": sorted(artifacts.keys()),
        "started_at": task_run.get("started_at") or "",
        "finished_at": task_run.get("finished_at") or "",
    }


def build_task_evidence_warnings(*, task: dict, runs: list[dict], comparison: dict) -> list[dict]:
    warnings = []
    latest = runs[0] if runs else {}
    latest_artifacts = latest.get("artifact_paths") or task.get("latest_artifacts") or {}
    latest_comparable = comparison.get("latest_run") or {}
    previous_comparable = comparison.get("previous_run") or {}
    latest_output_dir = latest.get("output_dir") or task.get("latest_output_dir") or ""
    if latest_output_dir and not path_exists(str(latest_output_dir)):
        warnings.append(
            {
                "code": "latest_output_dir_missing",
                "severity": "warning",
                "message": "最新 run 的产物目录不存在，无法复查本地证据。",
                "path": str(latest_output_dir),
            }
        )
    for kind, path in sorted(latest_artifacts.items()):
        if path and not path_exists(str(path)):
            warnings.append(
                {
                    "code": "latest_artifact_path_missing",
                    "kind": kind,
                    "severity": "warning",
                    "message": f"最新 run 的产物路径不存在：{kind}",
                    "path": str(path),
                }
            )
    if latest.get("execution_mode") == "precommit-verify":
        for kind in ["precommit_manifest", "verification_matrix"]:
            if kind not in latest_artifacts:
                warnings.append(
                    {
                        "code": "latest_artifact_missing",
                        "kind": kind,
                        "severity": "warning",
                        "message": f"最新 precommit run 缺少关键产物：{kind}",
                        "path": "",
                    }
                )
    if (
        latest_comparable.get("ui_evidence_status") == "missing"
        and previous_comparable.get("ui_evidence_status") == "present"
    ):
        warnings.append(
            {
                "code": "latest_ui_evidence_missing_but_previous_present",
                "severity": "warning",
                "message": "最新 run 缺少 UI 证据，但上一条 run 有 UI 证据；旧证据只能作为历史参考，不能替代最新验证。",
                "latest_task_run_id": latest_comparable.get("task_run_id"),
                "previous_task_run_id": previous_comparable.get("task_run_id"),
            }
        )
    return warnings


def build_workbench_artifact_index(*, task: dict, runs: list[dict]) -> list[dict]:
    rows: list[dict] = []
    rows.extend(build_artifact_rows(task.get("latest_artifacts") or {}, source="latest_task"))
    for run in runs:
        rows.extend(build_artifact_rows(run.get("artifact_paths") or {}, source=f"task_run:{run.get('id') or '-'}", run_id=run.get("run_id"), task_run_id=run.get("id")))
    deduped: list[dict] = []
    seen = set()
    for row in rows:
        key = (row.get("kind"), row.get("path"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def build_requirement_calibration_summary(artifact_paths: dict) -> dict:
    json_path = str((artifact_paths or {}).get("requirement_calibration_json") or "").strip()
    markdown_path = str((artifact_paths or {}).get("requirement_calibration_md") or "").strip()
    card = read_json_file(Path(json_path).expanduser()) if json_path else {}
    markdown_preview = read_markdown_preview(Path(markdown_path).expanduser(), limit=6) if markdown_path else []
    parameters = card.get("resolved_parameters") if isinstance(card.get("resolved_parameters"), list) else []
    warning_items = card.get("warnings") if isinstance(card.get("warnings"), list) else []
    decision = card.get("decision") if isinstance(card.get("decision"), dict) else {}
    source_priority = card.get("source_priority") if isinstance(card.get("source_priority"), list) else []
    exists = bool((json_path and path_exists(json_path)) or (markdown_path and path_exists(markdown_path)))
    return {
        "version": card.get("version") or "",
        "exists": exists,
        "status": card.get("status") or ("present" if exists else "missing"),
        "confidence": decision.get("confidence") or "",
        "summary": decision.get("summary") or "",
        "needs_human_confirmation": bool(decision.get("needs_human_confirmation")),
        "parameter_names": [str(item.get("name") or "") for item in parameters if isinstance(item, dict) and item.get("name")],
        "source_priority": [str(item.get("source") or "") for item in source_priority if isinstance(item, dict) and item.get("source")],
        "warning_types": [str(item.get("type") or "") for item in warning_items if isinstance(item, dict) and item.get("type")],
        "markdown_preview": markdown_preview,
        "json_path": json_path,
        "markdown_path": markdown_path,
        "json_exists": path_exists(json_path) if json_path else False,
        "markdown_exists": path_exists(markdown_path) if markdown_path else False,
    }


def build_workspace_requirement_calibration_entry(summary: dict, *, task_slug: str) -> dict:
    markdown_path = str(summary.get("markdown_path") or "")
    json_path = str(summary.get("json_path") or "")
    return {
        "status": summary.get("status") or "missing",
        "confidence": summary.get("confidence") or "",
        "summary": summary.get("summary") or "",
        "parameter_names": summary.get("parameter_names") or [],
        "warning_types": summary.get("warning_types") or [],
        "markdown_preview": summary.get("markdown_preview") or [],
        "markdown_link": f"workbenches/{task_slug}/requirement_calibration.md" if markdown_path else "",
        "json_link": f"workbenches/{task_slug}/requirement_calibration.json" if json_path else "",
    }


def build_requirement_evidence_summary(artifact_paths: dict) -> dict:
    json_path = str((artifact_paths or {}).get("requirement_evidence_json") or "").strip()
    markdown_path = str((artifact_paths or {}).get("requirement_evidence_md") or "").strip()
    evidence = read_json_file(Path(json_path).expanduser()) if json_path else {}
    markdown_preview = read_markdown_preview(Path(markdown_path).expanduser(), limit=6) if markdown_path else []
    warnings = evidence.get("warnings") if isinstance(evidence.get("warnings"), list) else []
    exists = bool((json_path and path_exists(json_path)) or (markdown_path and path_exists(markdown_path)))
    return {
        "version": evidence.get("version") or "",
        "exists": exists,
        "status": "present" if exists else "missing",
        "source_type": evidence.get("source_type") or "",
        "external_id": evidence.get("external_id") or "",
        "title": evidence.get("title") or "",
        "source_url": evidence.get("source_url") or "",
        "requirement_status": evidence.get("status") or "",
        "assignee": evidence.get("assignee") or "",
        "attachment_count": len(evidence.get("attachments") or []),
        "image_count": len(evidence.get("images") or []),
        "comment_count": len(evidence.get("comments") or []),
        "warning_codes": [str(item.get("code") or "") for item in warnings if isinstance(item, dict) and item.get("code")],
        "markdown_preview": markdown_preview,
        "json_path": json_path,
        "markdown_path": markdown_path,
        "json_exists": path_exists(json_path) if json_path else False,
        "markdown_exists": path_exists(markdown_path) if markdown_path else False,
    }


def build_workspace_requirement_evidence_entry(summary: dict, *, task_slug: str) -> dict:
    markdown_path = str(summary.get("markdown_path") or "")
    json_path = str(summary.get("json_path") or "")
    return {
        "status": summary.get("status") or "missing",
        "source_type": summary.get("source_type") or "",
        "external_id": summary.get("external_id") or "",
        "title": summary.get("title") or "",
        "requirement_status": summary.get("requirement_status") or "",
        "assignee": summary.get("assignee") or "",
        "attachment_count": summary.get("attachment_count") or 0,
        "image_count": summary.get("image_count") or 0,
        "comment_count": summary.get("comment_count") or 0,
        "warning_codes": summary.get("warning_codes") or [],
        "markdown_preview": summary.get("markdown_preview") or [],
        "markdown_link": f"workbenches/{task_slug}/requirement_evidence.md" if markdown_path else "",
        "json_link": f"workbenches/{task_slug}/requirement_evidence.json" if json_path else "",
    }


def build_workspace_change_history_entry(history: dict, *, task_slug: str) -> dict:
    latest = history.get("latest_change") or {}
    change_count = int(history.get("change_count") or 0)
    return {
        "change_count": change_count,
        "rollback_mode": history.get("rollback_mode") or "dry_run_only",
        "rollback_available": bool(history.get("rollback_available")),
        "latest_change_id": latest.get("change_id") or "",
        "latest_change_sequence": latest.get("change_sequence") or "",
        "latest_diff_summary": latest.get("diff_summary") or "",
        "markdown_link": f"workbenches/{task_slug}/task_change_history.md",
        "json_link": f"workbenches/{task_slug}/task_change_history.json",
    }


def build_workspace_task_detail(*, entry: dict, workbench: dict, task_slug: str) -> dict:
    task = workbench.get("task") or {}
    change_history = workbench.get("change_history") or {}
    detail = {
        "version": "0.17B-task-detail",
        "readonly": True,
        "yunxiao_write_enabled": False,
        "task_id": entry.get("task_id") or task.get("task_id"),
        "task_key": entry.get("task_key") or task.get("task_key") or "",
        "task_slug": task_slug,
        "detail_id": f"detail-{task_slug}",
        "entity_kind": entry.get("entity_kind") or task.get("entity_kind") or "",
        "entity_id": entry.get("entity_id") or task.get("entity_id") or "",
        "entity_title": entry.get("entity_title") or task.get("entity_title") or "",
        "overview": {
            "status": entry.get("status") or task.get("status") or "",
            "verification_status": entry.get("verification_status") or task.get("verification_status") or "",
            "ui_evidence_status": entry.get("ui_evidence_status") or (task.get("ui_evidence") or {}).get("status") or "",
            "latest_run_id": entry.get("latest_run_id") or task.get("latest_run_id"),
            "latest_output_dir": entry.get("latest_output_dir") or task.get("latest_output_dir") or "",
            "artifact_count": entry.get("artifact_count") or len(workbench.get("artifacts") or []),
            "warning_count": entry.get("warning_count") or len(workbench.get("evidence_warnings") or []),
            "change_count": entry.get("change_count") or change_history.get("change_count") or 0,
            "workbench_json": entry.get("workbench_json") or "",
            "workbench_markdown": entry.get("workbench_markdown") or "",
        },
        "runs": workbench.get("runs") or [],
        "artifacts": workbench.get("artifacts") or [],
        "requirement_calibration": workbench.get("requirement_calibration") or {},
        "requirement_evidence": workbench.get("requirement_evidence") or {},
        "change_history": change_history,
        "run_history_comparison": workbench.get("run_history_comparison") or {},
        "evidence_warnings": workbench.get("evidence_warnings") or [],
        "commands": build_workspace_detail_commands(entry=entry, workbench=workbench),
        "residual_risk": "任务详情页只展示本地已登记信息、产物预览和命令文本；复跑和回滚 dry-run 均需人工复制执行。",
    }
    detail["evidence_preview"] = build_workspace_evidence_preview(detail=detail, task_slug=task_slug)
    return detail


def build_workspace_detail_commands(*, entry: dict, workbench: dict) -> dict:
    commands = dict(workbench.get("commands") or {})
    change_history = workbench.get("change_history") or {}
    latest_change = change_history.get("latest_change") or {}
    task_key = str(entry.get("task_key") or (workbench.get("task") or {}).get("task_key") or "").strip()
    change_id = str(latest_change.get("change_id") or "").strip()
    if task_key and change_id:
        output_slug = safe_slug(str(entry.get("entity_id") or task_key))
        commands["rollback_dry_run"] = shell_join(
            [
                "python3",
                "tools/task_manager.py",
                "rollback-plan",
                "--task-key",
                task_key,
                "--change-id",
                change_id,
                "--output-dir",
                f"/tmp/his_harness_{output_slug}_rollback_plan",
            ]
        )
    return {key: value for key, value in commands.items() if value}


def build_workspace_evidence_preview(*, detail: dict, task_slug: str) -> dict:
    artifacts = detail.get("artifacts") or []
    calibration = detail.get("requirement_calibration") or {}
    requirement_evidence = detail.get("requirement_evidence") or {}
    change_history = detail.get("change_history") or {}
    sections = []
    requirement_evidence_path = str(requirement_evidence.get("markdown_path") or requirement_evidence.get("json_path") or "")
    if requirement_evidence_path or requirement_evidence.get("markdown_preview"):
        sections.append(
            build_workspace_evidence_preview_section(
                label="需求来源证据",
                kind="requirement_evidence",
                path=requirement_evidence_path,
                link=(
                    f"workbenches/{task_slug}/requirement_evidence.md"
                    if requirement_evidence.get("markdown_path")
                    else f"workbenches/{task_slug}/requirement_evidence.json"
                    if requirement_evidence.get("json_path")
                    else ""
                ),
                preview_lines=requirement_evidence.get("markdown_preview") or None,
            )
        )
    calibration_path = str(calibration.get("markdown_path") or calibration.get("json_path") or "")
    if calibration_path or calibration.get("markdown_preview"):
        sections.append(
            build_workspace_evidence_preview_section(
                label="需求理解确认卡",
                kind="requirement_calibration",
                path=calibration_path,
                link=(
                    f"workbenches/{task_slug}/requirement_calibration.md"
                    if calibration.get("markdown_path")
                    else f"workbenches/{task_slug}/requirement_calibration.json"
                    if calibration.get("json_path")
                    else ""
                ),
                preview_lines=calibration.get("markdown_preview") or None,
            )
        )
    for label, preview_kind, artifact_kinds in [
        ("验证矩阵", "verification_matrix", ["verification_matrix", "verification_matrix_md"]),
        ("UI 证据清单", "ui_evidence_manifest", ["ui_evidence_manifest"]),
        ("截图索引", "playwright_screenshot_index", ["playwright_screenshot_index"]),
        ("Precommit Manifest", "precommit_manifest", ["precommit_manifest"]),
    ]:
        artifact = find_first_artifact(artifacts, artifact_kinds)
        if artifact:
            sections.append(
                build_workspace_evidence_preview_section(
                    label=label,
                    kind=preview_kind,
                    path=str(artifact.get("path") or ""),
                    link=str(artifact.get("path") or ""),
                )
            )
    sections.append(
        build_workspace_evidence_preview_section(
            label="修改历史",
            kind="task_change_history",
            path=f"workbenches/{task_slug}/task_change_history.md",
            link=f"workbenches/{task_slug}/task_change_history.md",
            exists=bool(change_history.get("change_count")),
            preview_lines=task_change_history_body_lines(change_history)[:8],
        )
    )
    return {
        "version": "0.17B-evidence-preview",
        "readonly": True,
        "section_count": len(sections),
        "sections": sections,
        "residual_risk": "证据预览只截取本地文本/JSON摘要，不代表重新运行 UI、单测或 precommit。",
    }


def find_first_artifact(artifacts: list[dict], kinds: list[str]) -> dict:
    kind_set = set(kinds)
    for artifact in artifacts:
        if isinstance(artifact, dict) and artifact.get("kind") in kind_set:
            return artifact
    return {}


def build_workspace_evidence_preview_section(
    *,
    label: str,
    kind: str,
    path: str,
    link: str = "",
    exists: bool | None = None,
    preview_lines: list[str] | None = None,
) -> dict:
    path_text = str(path or "").strip()
    path_exists_value = path_exists(path_text) if exists is None else bool(exists)
    return {
        "label": label,
        "kind": kind,
        "path": path_text,
        "link": str(link or path_text),
        "exists": path_exists_value,
        "preview_lines": preview_lines if preview_lines is not None else read_text_preview(Path(path_text).expanduser(), limit=8),
        "open_command": shell_join(["open", path_text]) if path_text and path_exists_value else "",
    }


def write_requirement_calibration_copies(*, target_dir: Path, summary: dict) -> dict:
    files: dict[str, Path] = {}
    for source_key, output_name, file_key in [
        ("json_path", "requirement_calibration.json", "requirement_calibration_json"),
        ("markdown_path", "requirement_calibration.md", "requirement_calibration_markdown"),
    ]:
        source_text = str(summary.get(source_key) or "").strip()
        if not source_text:
            continue
        source = Path(source_text).expanduser()
        if not source.exists() or not source.is_file():
            continue
        target = target_dir / output_name
        target.write_text(source.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        files[file_key] = target
    return files


def write_requirement_evidence_copies(*, target_dir: Path, summary: dict) -> dict:
    files: dict[str, Path] = {}
    for source_key, output_name, file_key in [
        ("json_path", "requirement_evidence.json", "requirement_evidence_json"),
        ("markdown_path", "requirement_evidence.md", "requirement_evidence_markdown"),
    ]:
        source_text = str(summary.get(source_key) or "").strip()
        if not source_text:
            continue
        source = Path(source_text).expanduser()
        if not source.exists() or not source.is_file():
            continue
        target = target_dir / output_name
        target.write_text(source.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        files[file_key] = target
    return files


def build_artifact_rows(artifact_paths: dict, *, source: str, run_id: object = None, task_run_id: object = None) -> list[dict]:
    rows = []
    for kind, path in sorted((artifact_paths or {}).items()):
        path_text = str(path or "").strip()
        rows.append(
            {
                "kind": kind,
                "path": path_text,
                "exists": path_exists(path_text),
                "source": source,
                "run_id": run_id,
                "task_run_id": task_run_id,
                "open_command": shell_join(["open", path_text]) if path_text else "",
            }
        )
    return rows


def build_workbench_commands(*, task: dict, latest_manifest: dict) -> dict:
    output_dir = str(task.get("latest_output_dir") or "").strip()
    commands = {
        "open_latest_output_dir": shell_join(["open", output_dir]) if output_dir else "",
        "dashboard_filter": shell_join(
            [
                "python3",
                "tools/task_manager.py",
                "dashboard",
                "--entity-id",
                task.get("entity_id") or "",
                "--output-dir",
                "/tmp/his_harness_task_dashboard",
            ]
        )
        if task.get("entity_id")
        else "",
        "rerun_precommit": build_rerun_precommit_command(task=task, latest_manifest=latest_manifest),
    }
    return {key: value for key, value in commands.items() if value}


def build_rerun_precommit_command(*, task: dict, latest_manifest: dict) -> str:
    task_key = str(task.get("task_key") or "").strip()
    if not task_key:
        return ""
    project_path = resolve_precommit_project_path(options=TaskPrecommitRerunOptions(), task=task, latest_manifest=latest_manifest)
    allowed_paths = resolve_precommit_allowed_paths(latest_manifest)
    verify_commands = resolve_precommit_verify_commands(latest_manifest)
    parts = [
        "python3",
        "tools/task_manager.py",
        "rerun-precommit",
        "--task-key",
        task_key,
    ]
    if task.get("project_root"):
        parts.extend(["--project-root", str(task["project_root"])])
    if project_path:
        parts.extend(["--project-path", project_path])
    for path in allowed_paths:
        parts.extend(["--allowed-path", path])
    for command in verify_commands:
        parts.extend(["--verify-command", command])
    parts.extend(["--output-root", str(DEFAULT_TASK_OUTPUT_ROOT), "--worktree-dir", str(DEFAULT_TASK_WORKTREE_ROOT)])
    return shell_join(parts)


def shell_join(parts: list[object]) -> str:
    return shlex.join([str(part) for part in parts if str(part).strip()])


def coerce_task_change_options(options: TaskChangeRecordOptions | dict) -> TaskChangeRecordOptions:
    if isinstance(options, TaskChangeRecordOptions):
        return options
    allowed = set(TaskChangeRecordOptions.__dataclass_fields__)
    return TaskChangeRecordOptions(**{key: value for key, value in (options or {}).items() if key in allowed})


def coerce_task_manual_verification_options(options: TaskManualVerificationOptions | dict) -> TaskManualVerificationOptions:
    if isinstance(options, TaskManualVerificationOptions):
        return options
    allowed = set(TaskManualVerificationOptions.__dataclass_fields__)
    return TaskManualVerificationOptions(**{key: value for key, value in (options or {}).items() if key in allowed})


def resolve_manual_verification_source_run(
    *,
    task_id: int,
    runs: list[dict],
    source_task_run_id: int | None,
    source_run_id: int | None,
) -> dict:
    if source_task_run_id:
        task_run = database.get_task_run(source_task_run_id)
        if task_run is None or int(task_run.get("task_id") or 0) != task_id:
            raise KeyError("source_task_run_id 不属于当前 Harness 任务。")
        return task_run
    if source_run_id:
        for task_run in runs:
            if int(task_run.get("run_id") or 0) == source_run_id:
                return task_run
        raise KeyError("source_run_id 不属于当前 Harness 任务。")
    raise ValueError("人工运行时验收需要提供 --source-task-run-id 或 --source-run-id，以保留源码门禁追溯。")


def build_manual_runtime_verification_evidence(
    *,
    task: dict,
    source_task_run: dict,
    source_run: dict,
    run_id: int,
    status: str,
    verifier: str,
    summary: str,
    scenarios: list[str],
    notes: list[str],
) -> dict:
    return {
        "version": "0.42-manual-runtime-verification",
        "generated_at": database.now_iso(),
        "evidence_source": "user_confirmed",
        "verification_scope": "runtime_business_acceptance",
        "status": status,
        "verifier": verifier.strip() or "user",
        "summary": summary,
        "scenarios": [item.strip() for item in scenarios if item and item.strip()],
        "notes": [item.strip() for item in notes if item and item.strip()],
        "task_id": task.get("id"),
        "task_key": task.get("task_key") or "",
        "manual_run_id": run_id,
        "source_task_run_id": source_task_run.get("id"),
        "source_run_id": source_task_run.get("run_id"),
        "source_execution_mode": source_task_run.get("execution_mode") or "",
        "source_status": source_task_run.get("status") or "",
        "source_evaluation_status": source_task_run.get("evaluation_status") or "",
        "source_verification_status": source_task_run.get("verification_status") or "",
        "source_output_dir": source_task_run.get("output_dir") or "",
        "source_run_evaluation_summary": source_run.get("evaluation_summary") or "",
        "source_contract_status": (
            "proven_by_source_run"
            if source_task_run.get("status") == "success" and source_task_run.get("verification_status") == "passed"
            else "not_proven_by_current_local_source"
        ),
        "safety_boundaries": {
            "does_not_override_source_contract_gate": True,
            "does_not_enable_auto_apply": True,
            "can_commit": False,
            "can_remote_write": False,
            "can_yunxiao_transition": False,
        },
    }


def manual_runtime_verification_to_markdown(evidence: dict) -> str:
    lines = [
        "# 人工运行时验收记录",
        "",
        f"- 结论：{evidence.get('status')}",
        f"- 验证人：{evidence.get('verifier')}",
        f"- 证据来源：{evidence.get('evidence_source')}",
        f"- 范围：{evidence.get('verification_scope')}",
        f"- 来源 Task Run：{evidence.get('source_task_run_id')}",
        f"- 来源 Run：{evidence.get('source_run_id')}",
        f"- 当前本地源码契约：{evidence.get('source_contract_status')}",
        "",
        "## 验收结论",
        "",
        f"- {evidence.get('summary')}",
        "",
        "## 验收场景",
        "",
    ]
    scenarios = evidence.get("scenarios") or []
    lines.extend([f"- {item}" for item in scenarios] or ["- 未单独记录"])
    lines.extend(["", "## 安全边界", ""])
    boundaries = evidence.get("safety_boundaries") or {}
    lines.extend(
        [
            f"- 不覆盖原源码门禁：{'是' if boundaries.get('does_not_override_source_contract_gate') else '否'}",
            f"- 不启用自动应用：{'是' if boundaries.get('does_not_enable_auto_apply') else '否'}",
            f"- 可提交：{'是' if boundaries.get('can_commit') else '否'}",
            f"- 可远端写入：{'是' if boundaries.get('can_remote_write') else '否'}",
        ]
    )
    notes = evidence.get("notes") or []
    if notes:
        lines.extend(["", "## 备注", ""])
        lines.extend([f"- {item}" for item in notes])
    return "\n".join(lines)


def coerce_task_rollback_options(options: TaskRollbackPlanOptions | dict) -> TaskRollbackPlanOptions:
    if isinstance(options, TaskRollbackPlanOptions):
        return options
    allowed = set(TaskRollbackPlanOptions.__dataclass_fields__)
    return TaskRollbackPlanOptions(**{key: value for key, value in (options or {}).items() if key in allowed})


def coerce_task_rollback_apply_options(options: TaskRollbackApplyOptions | dict) -> TaskRollbackApplyOptions:
    if isinstance(options, TaskRollbackApplyOptions):
        return options
    allowed = set(TaskRollbackApplyOptions.__dataclass_fields__)
    return TaskRollbackApplyOptions(**{key: value for key, value in (options or {}).items() if key in allowed})


def first_text(items: list[object]) -> str:
    for item in items:
        text = str(item or "").strip()
        if text:
            return text
    return ""


def build_change_id(*, task_key: str, sequence: int) -> str:
    return f"change-{safe_slug(task_key)}-{sequence:03d}"


def task_change_diff_storage_path(change_id: str) -> Path:
    root = Path(database.DB_PATH).expanduser().resolve().parent / "task_changes" / safe_slug(change_id)
    return root / "final.diff"


def enrich_task_change(change: dict) -> dict:
    diff_path = str(change.get("diff_path") or "")
    return {
        **change,
        "rollback_mode": change.get("rollback_mode") or "dry_run_only",
        "rollback_status": change.get("rollback_status") or "available",
        "diff_exists": path_exists(diff_path),
        "open_diff_command": shell_join(["open", diff_path]) if diff_path else "",
    }


def summarize_unified_diff(diff_text: str) -> str:
    files: list[str] = []
    added = 0
    deleted = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                files.append(parts[3][2:] if parts[3].startswith("b/") else parts[3])
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            deleted += 1
    file_text = ", ".join(unique_keep_order(files)) if files else "unknown files"
    return f"{file_text}; +{added}/-{deleted}"


def resolve_change_for_rollback(*, task_id: int, options: TaskRollbackPlanOptions) -> dict | None:
    if options.change_id:
        change = database.get_task_change_by_change_id(options.change_id)
        if change and int(change.get("task_id") or 0) != task_id:
            raise ValueError("change_id 不属于当前 task。")
        return enrich_task_change(change) if change else None
    if options.target_change_sequence is not None:
        change = database.get_task_change_by_sequence(task_id, int(options.target_change_sequence))
        return enrich_task_change(change) if change else None
    changes = database.list_task_changes(task_id)
    return enrich_task_change(changes[-1]) if changes else None


def build_git_apply_reverse_command(*, project_path: str, diff_path: str, check: bool) -> str:
    parts = ["git", "apply", "--reverse"]
    if check:
        parts.append("--check")
    parts.append(diff_path)
    command = shell_join(parts)
    if not project_path:
        return command
    return f"cd {shlex.quote(project_path)} && {command}"


def reverse_unified_diff(diff_text: str) -> str:
    lines = diff_text.splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                result.append(f"diff --git {parts[3]} {parts[2]}")
            else:
                result.append(line)
            index += 1
            continue
        if line.startswith("--- ") and index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
            old_path = line[4:]
            new_path = lines[index + 1][4:]
            result.append(f"--- {new_path}")
            result.append(f"+++ {old_path}")
            index += 2
            continue
        if line.startswith("@@ "):
            result.append(reverse_hunk_header(line))
        elif line.startswith("+") and not line.startswith("+++"):
            result.append("-" + line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            result.append("+" + line[1:])
        else:
            result.append(line)
        index += 1
    return "\n".join(result) + ("\n" if diff_text.endswith("\n") else "")


def reverse_hunk_header(line: str) -> str:
    match = re.match(r"@@ -([^ ]+) \+([^ ]+) @@(.*)", line)
    if not match:
        return line
    return f"@@ -{match.group(2)} +{match.group(1)} @@{match.group(3)}"


def format_rollback_mode(mode: str) -> str:
    return "dry-run" if str(mode or "") == "dry_run_only" else str(mode or "")


def task_change_history_to_markdown(history: dict) -> str:
    lines = ["# Task Manager 修改历史", ""]
    lines.extend(task_change_history_body_lines(history))
    return "\n".join(lines)


def task_change_history_body_lines(history: dict) -> list[str]:
    lines = [
        f"- 修改次数：{history.get('change_count', 0)}",
        f"- 回滚 dry-run：{'可生成' if history.get('rollback_available') else '暂无修改记录'}",
        f"- 本地事务回滚：{'可用' if history.get('transactional_rollback_available') else '不可用'}",
        f"- 模式：{format_rollback_mode(history.get('rollback_mode') or 'dry_run_only')}",
        f"- 残余风险：{history.get('residual_risk') or '-'}",
        "",
        "| 序号 | Change ID | 状态 | 验证 | Diff | 摘要 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for change in history.get("changes") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(change.get("change_sequence") or "-"),
                    str(change.get("change_id") or "-"),
                    str(change.get("status") or "-"),
                    str(change.get("verification_status") or "-"),
                    str(change.get("diff_path") or "-"),
                    str(change.get("diff_summary") or "-").replace("|", "/"),
                ]
            )
            + " |"
        )
    if not history.get("changes"):
        lines.append("| - | - | - | - | - | - |")
    lines.extend(["", "### 回滚 dry-run 命令模板", ""])
    latest = history.get("latest_change") or {}
    if latest:
        lines.extend(
            [
                "```bash",
                f"python3 tools/task_manager.py rollback-plan --task-key {history.get('task_key') or '<task-key>'} --change-id {latest.get('change_id')} --output-dir /tmp/his_harness_rollback_plan",
                "```",
            ]
        )
        if latest.get("rollback_mode") == "local_transaction":
            lines.extend(
                [
                    "",
                    "### 本地事务回滚命令模板",
                    "",
                    "```bash",
                    f"python3 tools/task_manager.py rollback-apply --task-key {history.get('task_key') or '<task-key>'} --change-id {latest.get('change_id')} --confirm ROLLBACK:{latest.get('change_id')}",
                    "```",
                ]
            )
    else:
        lines.append("- 暂无")
    return lines


def rollback_plan_to_markdown(plan: dict) -> str:
    commands = plan.get("commands") or {}
    lines = [
        "# Task Manager 回滚 dry-run 计划",
        "",
        f"- 版本：{plan.get('version')}",
        f"- 状态：{plan.get('status')}",
        f"- Task Key：{plan.get('task_key')}",
        f"- Change：{plan.get('change_id')} / #{plan.get('target_change_sequence')}",
        f"- 只生成计划不改文件：{'是' if plan.get('dry_run_only') else '否'}",
        f"- 将修改文件：{'是' if plan.get('will_modify_files') else '否'}",
        f"- 原始 diff：{plan.get('source_diff_path')}",
        f"- 反向 patch：{plan.get('reverse_patch_path')}",
        "",
        "## 命令",
    ]
    for name, command in commands.items():
        lines.extend(["", f"### {name}", "", "```bash", str(command), "```"])
    lines.extend(["", "## 人工步骤"])
    for step in plan.get("manual_steps") or []:
        lines.append(f"- {step}")
    lines.extend(["", "## 残余风险", "", f"- {plan.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def path_exists(path_text: str) -> bool:
    if not path_text:
        return False
    try:
        return Path(path_text).expanduser().exists()
    except OSError:
        return False


def build_dashboard_task_item(*, task: dict, runs: list[dict]) -> dict:
    latest_run = runs[0] if runs else {}
    latest_artifacts = task.get("latest_artifacts") or latest_run.get("artifact_paths") or {}
    ui_evidence = build_ui_evidence_summary(latest_artifacts)
    return {
        "task_id": task.get("id"),
        "task_key": task.get("task_key"),
        "entity_kind": task.get("entity_kind") or "",
        "entity_id": task.get("entity_id") or "",
        "entity_title": task.get("entity_title") or "",
        "entity_url": task.get("entity_url") or "",
        "source_type": task.get("source_type") or "",
        "current_stage": task.get("current_stage") or "",
        "status": task.get("status") or "",
        "verification_status": task.get("verification_status") or "",
        "can_commit": bool(task.get("can_commit")),
        "can_yunxiao_transition": False,
        "project_root": task.get("project_root") or "",
        "project_paths": task.get("project_paths") or [],
        "latest_run_id": task.get("latest_run_id"),
        "latest_output_dir": task.get("latest_output_dir") or "",
        "latest_artifact_count": len(latest_artifacts),
        "latest_artifacts": latest_artifacts,
        "latest_run": summarize_task_run(latest_run),
        "runs": [summarize_task_run(item) for item in runs],
        "ui_evidence": ui_evidence,
        "metadata": task.get("metadata") or {},
        "updated_at": task.get("updated_at") or "",
    }


def summarize_task_run(task_run: dict) -> dict:
    if not task_run:
        return {}
    artifacts = task_run.get("artifact_paths") or {}
    return {
        "task_run_id": task_run.get("id"),
        "run_id": task_run.get("run_id"),
        "stage": task_run.get("stage") or "",
        "execution_mode": task_run.get("execution_mode") or "",
        "status": task_run.get("status") or "",
        "evaluation_status": task_run.get("evaluation_status") or "",
        "verification_status": task_run.get("verification_status") or "",
        "output_dir": task_run.get("output_dir") or "",
        "summary": task_run.get("summary") or "",
        "artifact_count": len(artifacts),
        "artifact_paths": artifacts,
        "started_at": task_run.get("started_at") or "",
        "finished_at": task_run.get("finished_at") or "",
    }


def build_ui_evidence_summary(artifact_paths: dict) -> dict:
    manifest = read_json_file(Path(str(artifact_paths.get("ui_evidence_manifest") or "")))
    runner = read_json_file(Path(str(artifact_paths.get("ui_evidence_runner") or "")))
    policy = read_json_file(Path(str(artifact_paths.get("ui_evidence_reuse_policy_json") or "")))
    manifest_artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
    manifest_assertions = manifest.get("assertions") if isinstance(manifest.get("assertions"), list) else []
    runner_artifacts = runner.get("artifact_paths") if isinstance(runner.get("artifact_paths"), list) else []
    runner_assertions = runner.get("assertions") if isinstance(runner.get("assertions"), list) else []
    artifact_count = policy.get("artifact_count")
    if artifact_count is None:
        artifact_count = len(manifest_artifacts) + len(runner_artifacts)
    assertion_count = policy.get("assertion_count")
    if assertion_count is None:
        assertion_count = len(manifest_assertions) + len(runner_assertions)
    status = policy.get("evidence_status") or manifest.get("status") or runner.get("status")
    if not status:
        status = "present" if artifact_count or assertion_count else "missing"
    evidence_paths = {
        key: path
        for key, path in {
            "manifest": artifact_paths.get("ui_evidence_manifest"),
            "runner": artifact_paths.get("ui_evidence_runner"),
            "reuse_policy": artifact_paths.get("ui_evidence_reuse_policy_json"),
            "screenshot_index": artifact_paths.get("playwright_screenshot_index"),
        }.items()
        if path
    }
    return {
        "status": status,
        "reusable": bool(policy.get("reusable")) if policy else bool(artifact_count or assertion_count),
        "artifact_count": int(artifact_count or 0),
        "assertion_count": int(assertion_count or 0),
        "paths": evidence_paths,
        "residual_risk": policy.get("residual_risk") or "",
    }


def active_dashboard_filter_labels(dashboard: dict) -> list[str]:
    filters = dashboard.get("filters") or {}
    labels = []
    for key, label in [
        ("entity_id", "DFHIS"),
        ("task_key", "Task Key"),
        ("entity_kind", "类型"),
        ("status", "状态"),
        ("verification_status", "验证"),
        ("ui_evidence_status", "UI证据"),
    ]:
        if filters.get(key):
            labels.append(f"{label}={filters[key]}")
    if filters.get("can_commit") is not None:
        labels.append(f"可提交={'是' if filters.get('can_commit') else '否'}")
    if filters.get("sample_only"):
        labels.append("仅真实样板")
    return labels


def task_dashboard_to_markdown(dashboard: dict) -> str:
    summary = dashboard.get("summary") or {}
    sample_set = dashboard.get("sample_set") or {}
    filter_labels = active_dashboard_filter_labels(dashboard)
    lines = [
        "# Task Manager 只读看板",
        "",
        f"- 版本：{dashboard.get('version')}",
        f"- 生成时间：{dashboard.get('generated_at')}",
        f"- 只读模式：{'是' if dashboard.get('readonly') else '否'}",
        f"- 云效真实写入：{'开启' if dashboard.get('yunxiao_write_enabled') else '关闭'}",
        f"- 当前筛选：{'; '.join(filter_labels) if filter_labels else '无'}",
        f"- 任务数：{summary.get('task_count', 0)}",
        f"- 运行数：{summary.get('run_count', 0)}",
        f"- 可提交任务数：{summary.get('can_commit_count', 0)}",
        f"- 真实样板数：{sample_set.get('count', 0)}",
        "",
        "| Task | 标题 | 阶段 | 状态 | 验证 | 可提交 | UI证据 | 最新Run | 产物数 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in dashboard.get("tasks") or []:
        ui = item.get("ui_evidence") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("task_key") or item.get("task_id") or "-"),
                    str(item.get("entity_title") or "-").replace("|", "/"),
                    str(item.get("current_stage") or "-"),
                    str(item.get("status") or "-"),
                    str(item.get("verification_status") or "-"),
                    "是" if item.get("can_commit") else "否",
                    str(ui.get("status") or "-"),
                    str(item.get("latest_run_id") or "-"),
                    str(item.get("latest_artifact_count") or 0),
                ]
            )
            + " |"
        )
    lines.extend(["", "## 任务详情"])
    for item in dashboard.get("tasks") or []:
        ui = item.get("ui_evidence") or {}
        lines.extend(
            [
                "",
                f"### {item.get('task_key') or item.get('task_id')}",
                "",
                f"- 编号：{item.get('entity_id') or '-'}",
                f"- 标题：{item.get('entity_title') or '-'}",
                f"- 最新产物目录：{item.get('latest_output_dir') or '-'}",
                f"- UI 证据：{ui.get('status') or '-'}，证据 {ui.get('artifact_count', 0)}，断言 {ui.get('assertion_count', 0)}",
                f"- 云效真实流转：否",
                "",
                "#### 运行历史",
            ]
        )
        for run in item.get("runs") or []:
            lines.append(
                f"- task_run={run.get('task_run_id')} run={run.get('run_id') or '-'} "
                f"mode={run.get('execution_mode') or '-'} status={run.get('status') or '-'} "
                f"output={run.get('output_dir') or '-'}"
            )
        if not item.get("runs"):
            lines.append("- 暂无")
    return "\n".join(lines)


def task_sample_set_to_markdown(sample_set: dict) -> str:
    lines = [
        "# Task Manager 真实样板集",
        "",
        f"- 版本：{sample_set.get('version')}",
        f"- 只读模式：{'是' if sample_set.get('readonly') else '否'}",
        f"- 样板数：{sample_set.get('count', 0)}",
        "",
        "| Sample | DFHIS | 标题 | 状态 | 验证 | UI证据 | 可复跑 | 产物目录 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sample in sample_set.get("samples") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(sample.get("sample_id") or "-"),
                    str(sample.get("entity_id") or "-"),
                    str(sample.get("entity_title") or "-").replace("|", "/"),
                    str(sample.get("status") or "-"),
                    str(sample.get("verification_status") or "-"),
                    str(sample.get("ui_evidence_status") or "-"),
                    "是" if sample.get("rerun_ready") else "否",
                    str(sample.get("latest_output_dir") or "-"),
                ]
            )
            + " |"
        )
    if not sample_set.get("samples"):
        lines.append("| - | - | - | - | - | - | - | - |")
    lines.extend(["", "## 样板详情"])
    for sample in sample_set.get("samples") or []:
        lines.extend(
            [
                "",
                f"### {sample.get('sample_id') or sample.get('task_id')}",
                "",
                f"- Task Key：{sample.get('task_key') or '-'}",
                f"- 最新 Run：{sample.get('latest_run_id') or '-'}",
                f"- 产物数：{sample.get('latest_artifact_count') or 0}",
                f"- Precommit Manifest：{sample.get('precommit_manifest') or '-'}",
                f"- 验证矩阵：{sample.get('verification_matrix') or '-'}",
                f"- Task Manager 登记记录：{sample.get('task_manager_record') or '-'}",
                f"- UI 证据复用策略：{sample.get('ui_evidence_reuse_policy') or '-'}",
            ]
        )
    return "\n".join(lines)


def task_workbench_to_markdown(workbench: dict) -> str:
    task = workbench.get("task") or {}
    commands = workbench.get("commands") or {}
    change_history = workbench.get("change_history") or {}
    comparison = workbench.get("run_history_comparison") or {}
    warnings = workbench.get("evidence_warnings") or []
    calibration = workbench.get("requirement_calibration") or {}
    requirement_evidence = workbench.get("requirement_evidence") or {}
    delivery = workbench.get("delivery") or {}
    lines = [
        "# Task Manager 本地工作台",
        "",
        f"- 版本：{workbench.get('version')}",
        f"- 生成时间：{workbench.get('generated_at')}",
        f"- 只读模式：{'是' if workbench.get('readonly') else '否'}",
        f"- 云效真实写入：{'开启' if workbench.get('yunxiao_write_enabled') else '关闭'}",
        f"- Task Key：{task.get('task_key') or '-'}",
        f"- 编号：{task.get('entity_id') or '-'}",
        f"- 标题：{task.get('entity_title') or '-'}",
        f"- 状态：{task.get('status') or '-'}",
        f"- 验证状态：{task.get('verification_status') or '-'}",
        f"- 最新产物目录：{task.get('latest_output_dir') or '-'}",
        "",
        "## 可复制命令",
    ]
    if commands:
        for name, command in commands.items():
            lines.extend(["", f"### {name}", "", "```bash", command, "```"])
    else:
        lines.append("- 暂无")

    latest_delivery = delivery.get("latest") or {}
    lines.extend(
        [
            "",
            "## Git 交付闭环",
            "",
            f"- 事务数：{delivery.get('transaction_count', 0)}",
            f"- 最新事务：{latest_delivery.get('id') or '-'}",
            f"- 当前状态：{latest_delivery.get('state') or '-'}",
            f"- 下一步：{delivery.get('next_action') or '-'}",
            f"- 远端写入：{'已开启' if delivery.get('remote_write_enabled') else '默认关闭'}",
            f"- RC 推送阻断：{'是' if delivery.get('rc_push_blocked') else '否'}",
        ]
    )

    lines.extend(["", "## 修改历史 / 回滚 dry-run", ""])
    lines.extend(task_change_history_body_lines(change_history))

    lines.extend(
        [
            "",
            "## 运行详情",
            "",
            "| Task Run | Run ID | 阶段 | 模式 | 状态 | 验证 | 产物数 | 产物目录 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for run in workbench.get("runs") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(run.get("task_run_id") or "-"),
                    str(run.get("run_id") or "-"),
                    str(run.get("stage") or "-"),
                    str(run.get("execution_mode") or "-"),
                    str(run.get("status") or "-"),
                    str(run.get("verification_status") or run.get("evaluation_status") or "-"),
                    str(run.get("artifact_count") or 0),
                    str(run.get("output_dir") or "-"),
                ]
            )
            + " |"
        )
    if not workbench.get("runs"):
        lines.append("| - | - | - | - | - | - | - | - |")

    latest_run = comparison.get("latest_run") or {}
    previous_run = comparison.get("previous_run") or {}
    lines.extend(
        [
            "",
            "## Run 对比",
            "",
            f"- Run 数：{comparison.get('run_count', 0)}",
            f"- 最新 Task Run：{latest_run.get('task_run_id') or '-'}，验证：{latest_run.get('verification_status') or '-'}，UI证据：{latest_run.get('ui_evidence_status') or '-'}",
            f"- 上一 Task Run：{previous_run.get('task_run_id') or '-'}，验证：{previous_run.get('verification_status') or '-'}，UI证据：{previous_run.get('ui_evidence_status') or '-'}",
            "",
            "| 字段 | 上一条 | 最新 |",
            "| --- | --- | --- |",
        ]
    )
    for change in comparison.get("changes") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(change.get("label") or change.get("field") or "-"),
                    str(change.get("previous") or "-"),
                    str(change.get("latest") or "-"),
                ]
            )
            + " |"
        )
    if not comparison.get("changes"):
        lines.append("| - | - | - |")

    lines.extend(
        [
            "",
            "## 需求理解确认卡",
            "",
            f"- 状态：{calibration.get('status') or 'missing'}",
            f"- 置信度：{calibration.get('confidence') or '-'}",
            f"- 参数：{', '.join(calibration.get('parameter_names') or []) or '-'}",
            f"- 来源优先级：{', '.join(calibration.get('source_priority') or []) or '-'}",
            f"- Warning：{', '.join(calibration.get('warning_types') or []) or '-'}",
            f"- JSON：{calibration.get('json_path') or '-'}",
            f"- Markdown：{calibration.get('markdown_path') or '-'}",
            f"- 结论：{calibration.get('summary') or '-'}",
        ]
    )
    if calibration.get("markdown_preview"):
        lines.extend(["", "### 原文摘要"])
        for item in calibration.get("markdown_preview") or []:
            lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## 需求来源证据",
            "",
            f"- 状态：{requirement_evidence.get('status') or 'missing'}",
            f"- 来源类型：{requirement_evidence.get('source_type') or '-'}",
            f"- 外部编号：{requirement_evidence.get('external_id') or '-'}",
            f"- 需求状态：{requirement_evidence.get('requirement_status') or '-'}",
            f"- 负责人：{requirement_evidence.get('assignee') or '-'}",
            f"- 标题：{requirement_evidence.get('title') or '-'}",
            f"- 附件/图片/评论：{requirement_evidence.get('attachment_count') or 0}/{requirement_evidence.get('image_count') or 0}/{requirement_evidence.get('comment_count') or 0}",
            f"- Warning：{', '.join(requirement_evidence.get('warning_codes') or []) or '-'}",
            f"- JSON：{requirement_evidence.get('json_path') or '-'}",
            f"- Markdown：{requirement_evidence.get('markdown_path') or '-'}",
        ]
    )
    if requirement_evidence.get("markdown_preview"):
        lines.extend(["", "### 需求来源原文摘要"])
        for item in requirement_evidence.get("markdown_preview") or []:
            lines.append(f"- {item}")

    lines.extend(["", "## 证据 Warning", ""])
    if warnings:
        for warning in warnings:
            lines.append(
                f"- [{warning.get('severity') or 'warning'}] {warning.get('code') or '-'}"
                f"{(' (' + str(warning.get('kind')) + ')') if warning.get('kind') else ''}：{warning.get('message') or '-'}"
            )
    else:
        lines.append("- 暂无")

    lines.extend(
        [
            "",
            "## 产物路径",
            "",
            "| Kind | Exists | Source | Path |",
            "| --- | --- | --- | --- |",
        ]
    )
    for artifact in workbench.get("artifacts") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(artifact.get("kind") or "-"),
                    "是" if artifact.get("exists") else "否",
                    str(artifact.get("source") or "-"),
                    str(artifact.get("path") or "-"),
                ]
            )
            + " |"
        )
    if not workbench.get("artifacts"):
        lines.append("| - | - | - | - |")
    lines.extend(["", "## 残余风险", "", f"- {workbench.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def build_delivery_workbench_projection(transactions: list[dict]) -> dict:
    latest = transactions[0] if transactions else {}
    state = str(latest.get("state") or "")
    next_action = {
        "waiting_release_runtime_acceptance": "等待 release 真实页面验收",
        "release_runtime_accepted": "等待第一次确认：创建任务分支并提交",
        "task_commit_created": "按计划推送任务分支或同步 RC",
        "waiting_rc_runtime_acceptance": "等待 RC 真实页面二次验收",
        "rc_runtime_accepted": "等待第二次确认：推送 RC",
        "completed": "交付完成",
        "stage_one_failed": "修复失败原因后复跑第一次确认",
        "rc_integration_failed": "处理 RC 集成失败后复跑",
        "recovery_required": "按 Safety Shelf 恢复本地工作区",
    }.get(state, "尚未创建 Git 交付事务" if not latest else "查看交付事件和阻断原因")
    parity = latest.get("parity_result") or {}
    projected = [
        {
            "id": item.get("id"),
            "entity_id": item.get("entity_id") or "",
            "state": item.get("state") or "",
            "project_path": item.get("project_path") or "",
            "output_dir": item.get("output_dir") or "",
            "last_error": item.get("last_error") or "",
            "updated_at": item.get("updated_at") or "",
        }
        for item in transactions
    ]
    return {
        "version": "1.0-delivery-workbench-projection",
        "readonly": True,
        "transaction_count": len(projected),
        "latest": projected[0] if projected else {},
        "transactions": projected,
        "next_action": next_action,
        "remote_write_enabled": False,
        "rc_push_blocked": bool(parity.get("rc_push_blocked")) if parity else False,
    }


def task_dashboard_to_html(dashboard: dict) -> str:
    summary = dashboard.get("summary") or {}
    sample_set = dashboard.get("sample_set") or {}
    filter_text = "; ".join(active_dashboard_filter_labels(dashboard)) or "无"
    rows = []
    for item in dashboard.get("tasks") or []:
        ui = item.get("ui_evidence") or {}
        rows.append(
            "<tr>"
            f"<td>{escape_html(item.get('task_key') or item.get('task_id') or '-')}</td>"
            f"<td>{escape_html(item.get('entity_title') or '-')}</td>"
            f"<td>{escape_html(item.get('current_stage') or '-')}</td>"
            f"<td>{escape_html(item.get('status') or '-')}</td>"
            f"<td>{escape_html(item.get('verification_status') or '-')}</td>"
            f"<td>{'是' if item.get('can_commit') else '否'}</td>"
            f"<td>{escape_html(ui.get('status') or '-')}</td>"
            f"<td>{escape_html(item.get('latest_run_id') or '-')}</td>"
            f"<td>{escape_html(item.get('latest_output_dir') or '-')}</td>"
            "</tr>"
        )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>HIS Harness Task Dashboard</title>",
            "<style>",
            "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;color:#1f2933;background:#f7f9fb}",
            "h1{font-size:22px;margin:0 0 16px}",
            ".summary{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}",
            ".metric{background:#fff;border:1px solid #d8e0e8;border-radius:6px;padding:10px 12px;min-width:120px}",
            ".metric strong{display:block;font-size:20px}",
            "table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d8e0e8}",
            "th,td{padding:8px 10px;border-bottom:1px solid #e6edf3;text-align:left;font-size:13px;vertical-align:top}",
            "th{background:#edf3f8;font-weight:600}",
            ".note{margin-top:14px;color:#52616f;font-size:13px}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>Task Manager 只读看板</h1>",
            '<div class="summary">',
            f'<div class="metric">任务数<strong>{escape_html(summary.get("task_count", 0))}</strong></div>',
            f'<div class="metric">运行数<strong>{escape_html(summary.get("run_count", 0))}</strong></div>',
            f'<div class="metric">可提交<strong>{escape_html(summary.get("can_commit_count", 0))}</strong></div>',
            f'<div class="metric">真实样板<strong>{escape_html(sample_set.get("count", 0))}</strong></div>',
            f'<div class="metric">云效写入<strong>{"关闭" if not dashboard.get("yunxiao_write_enabled") else "开启"}</strong></div>',
            "</div>",
            f'<p class="note">当前筛选：{escape_html(filter_text)}</p>',
            "<table>",
            "<thead><tr><th>Task</th><th>标题</th><th>阶段</th><th>状态</th><th>验证</th><th>可提交</th><th>UI证据</th><th>Run</th><th>产物目录</th></tr></thead>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
            '<p class="note">该页面只读取本地 Harness 数据库和产物索引，不执行云效写入、提交、推送或发布。</p>',
            "</body>",
            "</html>",
        ]
    )


def build_workspace_entry_filter_data(entry: dict) -> dict:
    warning_codes = sorted(set(str(code).strip() for code in entry.get("warning_codes") or [] if str(code).strip()))
    calibration = entry.get("requirement_calibration") or {}
    requirement_evidence = entry.get("requirement_evidence") or {}
    change_history = entry.get("change_history") or {}
    return {
        "warning_codes": warning_codes,
        "entity_id": str(entry.get("entity_id") or ""),
        "status": str(entry.get("status") or ""),
        "verification_status": str(entry.get("verification_status") or ""),
        "ui_evidence_status": str(entry.get("ui_evidence_status") or ""),
        "requirement_calibration_status": str(calibration.get("status") or ""),
        "requirement_evidence_status": str(requirement_evidence.get("status") or ""),
        "rollback_mode": str(change_history.get("rollback_mode") or ""),
    }


def build_workspace_entry_search_text(entry: dict) -> str:
    calibration = entry.get("requirement_calibration") or {}
    requirement_evidence = entry.get("requirement_evidence") or {}
    change_history = entry.get("change_history") or {}
    values = [
        entry.get("task_key"),
        entry.get("task_id"),
        entry.get("entity_kind"),
        entry.get("entity_id"),
        entry.get("entity_title"),
        entry.get("status"),
        entry.get("verification_status"),
        entry.get("ui_evidence_status"),
        entry.get("latest_run_id"),
        entry.get("latest_output_dir"),
        entry.get("change_count"),
        *(entry.get("warning_codes") or []),
        change_history.get("rollback_mode"),
        change_history.get("latest_change_id"),
        change_history.get("latest_change_sequence"),
        change_history.get("latest_diff_summary"),
        change_history.get("markdown_link"),
        change_history.get("json_link"),
        calibration.get("status"),
        calibration.get("confidence"),
        calibration.get("summary"),
        calibration.get("markdown_link"),
        calibration.get("json_link"),
        *(calibration.get("parameter_names") or []),
        *(calibration.get("warning_types") or []),
        *(calibration.get("markdown_preview") or []),
        requirement_evidence.get("status"),
        requirement_evidence.get("source_type"),
        requirement_evidence.get("external_id"),
        requirement_evidence.get("title"),
        requirement_evidence.get("requirement_status"),
        requirement_evidence.get("assignee"),
        requirement_evidence.get("markdown_link"),
        requirement_evidence.get("json_link"),
        *(requirement_evidence.get("warning_codes") or []),
        *(requirement_evidence.get("markdown_preview") or []),
    ]
    return " ".join(str(value).strip() for value in values if str(value).strip())


def build_workspace_warning_summary(entries: list[dict]) -> dict:
    code_counts: dict[str, int] = {}
    total_warning_count = 0
    task_count_with_warnings = 0
    for entry in entries:
        warning_count = int(entry.get("warning_count") or 0)
        total_warning_count += warning_count
        if warning_count:
            task_count_with_warnings += 1
        for code in entry.get("warning_codes") or []:
            code_text = str(code).strip()
            if not code_text:
                continue
            code_counts[code_text] = code_counts.get(code_text, 0) + 1
    return {
        "total_warning_count": total_warning_count,
        "task_count_with_warnings": task_count_with_warnings,
        "codes": [
            {"code": code, "count": count}
            for code, count in sorted(code_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def build_workspace_filter_options(entries: list[dict]) -> dict:
    warning_codes = []
    entity_ids = []
    statuses = []
    verification_statuses = []
    ui_evidence_statuses = []
    requirement_calibration_statuses = []
    requirement_evidence_statuses = []
    for entry in entries:
        warning_codes.extend(entry.get("warning_codes") or [])
        entity_ids.append(entry.get("entity_id") or "")
        statuses.append(entry.get("status") or "")
        verification_statuses.append(entry.get("verification_status") or "")
        ui_evidence_statuses.append(entry.get("ui_evidence_status") or "")
        requirement_calibration_statuses.append((entry.get("requirement_calibration") or {}).get("status") or "")
        requirement_evidence_statuses.append((entry.get("requirement_evidence") or {}).get("status") or "")
    return {
        "warning_codes": sorted(unique_keep_order(warning_codes)),
        "entity_ids": sorted(unique_keep_order(entity_ids)),
        "statuses": sorted(unique_keep_order(statuses)),
        "verification_statuses": sorted(unique_keep_order(verification_statuses)),
        "ui_evidence_statuses": sorted(unique_keep_order(ui_evidence_statuses)),
        "requirement_calibration_statuses": sorted(unique_keep_order(requirement_calibration_statuses)),
        "requirement_evidence_statuses": sorted(unique_keep_order(requirement_evidence_statuses)),
    }


def build_workspace_navigation(
    *,
    include_configuration: bool = False,
    include_configuration_preview: bool = False,
    include_config_share_validation: bool = False,
    include_config_import_draft: bool = False,
    include_config_import_review: bool = False,
    include_config_template_index: bool = False,
    include_config_wizard: bool = False,
    include_config_review_package: bool = False,
) -> dict:
    sections = [
        ("workspace-overview", "概览", "overview"),
    ]
    if include_configuration:
        sections.append(("workspace-configuration", "配置中心", "configuration"))
    if include_configuration_preview:
        sections.append(("workspace-configuration-preview", "配置预览", "configuration_preview"))
    if include_config_share_validation:
        sections.append(("workspace-config-share-validation", "配置分享校验", "config_share_validation"))
    if include_config_import_draft:
        sections.append(("workspace-config-import-draft", "配置导入草案", "config_import_draft"))
    if include_config_import_review:
        sections.append(("workspace-config-import-review", "导入回读校验", "config_import_review"))
    if include_config_template_index:
        sections.append(("workspace-config-template-index", "配置模板索引", "config_template_index"))
    if include_config_wizard:
        sections.append(("workspace-config-wizard", "配置向导", "config_wizard"))
    if include_config_review_package:
        sections.append(("workspace-config-review-package", "配置审查包", "config_review_package"))
    sections.extend(
        [
            ("workspace-tasks", "任务列表", "tasks"),
            ("task-detail-panel", "任务详情", "task_detail"),
            ("workspace-snapshot-history", "多快照", "snapshots"),
            ("workspace-snapshot-detail-panel", "快照详情", "snapshot_detail"),
            ("workspace-evidence-trend", "证据趋势", "evidence_trend"),
            ("workspace-export-index", "导出索引", "export_index"),
            ("workspace-offline-review", "离线审查", "offline_review"),
        ]
    )
    return {
        "version": (
            "0.33-workspace-navigation"
            if include_config_review_package
            else (
                "0.31-workspace-navigation"
                if include_config_wizard
                else (
                    "0.29-workspace-navigation"
                    if include_config_template_index
                    else (
                        "0.28-workspace-navigation"
                        if include_config_import_review
                        else (
                            "0.27-workspace-navigation"
                            if include_config_import_draft
                            else (
                                "0.26-workspace-navigation"
                                if include_config_share_validation
                                else ("0.25-workspace-navigation" if include_configuration_preview else ("0.22-workspace-navigation" if include_configuration else "0.20-workspace-navigation"))
                            )
                        )
                    )
                )
            )
        ),
        "readonly": True,
        "sections": [
            {"section_id": section_id, "label": label, "kind": kind}
            for section_id, label, kind in sections
        ],
        "residual_risk": "导航只跳转本地静态 HTML 区块，不读取远端、不执行命令。",
    }


def build_workspace_ui_polish() -> dict:
    return {
        "version": "0.21-workspace-ui-polish",
        "readonly": True,
        "empty_states": [
            {
                "kind": "no-tasks",
                "title": "暂无任务",
                "message": "当前筛选条件下没有 Task Manager 任务；可调整筛选或先通过 register-run/run 登记任务。",
            },
            {
                "kind": "no-snapshots",
                "title": "暂无历史快照",
                "message": "同一输出目录尚未形成多个 workspace 快照；再次导出后会逐步出现对比数据。",
            },
            {
                "kind": "no-calibration",
                "title": "暂无确认卡",
                "message": "该 run 没有 requirement_calibration.json/md；需要回到需求理解阶段补齐。",
            },
            {
                "kind": "no-evidence",
                "title": "暂无证据预览",
                "message": "没有找到可预览的 Markdown/JSON/截图索引；只展示缺失状态，不自动读取或生成证据。",
            },
        ],
        "error_states": [
            {
                "kind": "missing-artifact",
                "title": "产物文件缺失",
                "message": "索引中记录了文件但本地路径不存在；需要人工检查 output_dir 或重新登记 run。",
            },
            {
                "kind": "stale-ui-evidence",
                "title": "UI 证据过期",
                "message": "历史 run 有 UI 证据但最新 run 缺失；不能直接作为当前业务验收结论。",
            },
            {
                "kind": "readonly-boundary",
                "title": "只读边界",
                "message": "页面只允许浏览和复制命令，不会复跑、回滚、提交、推送或写云效。",
            },
        ],
        "readability": [
            "sticky_navigation",
            "scrollable_tables",
            "status_pills",
            "collapsible_evidence_preview",
            "offline_review_index",
        ],
        "residual_risk": "v0.21 只增强静态 HTML 可读性和离线审查说明，不改变 Task Manager 写入、复跑、回滚或云效边界。",
    }


def build_workspace_export_index(*, workspace: dict, target_dir: Path, workspace_files: dict, dashboard_files: dict, workbench_files: dict) -> dict:
    groups = [
        {
            "group": "workspace",
            "label": "Workspace",
            "files": [
                build_export_file_item(kind="task_workspace_json", path=workspace_files.get("json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_html", path=workspace_files.get("html"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_export_index_json", path=workspace_files.get("export_index_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_export_index_md", path=workspace_files.get("export_index_markdown"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_snapshot_comparison_json", path=workspace_files.get("snapshot_comparison_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_snapshot_comparison_md", path=workspace_files.get("snapshot_comparison_markdown"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_snapshot_history_json", path=workspace_files.get("snapshot_history_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_snapshot_history_md", path=workspace_files.get("snapshot_history_markdown"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_evidence_trend_json", path=workspace_files.get("evidence_trend_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_evidence_trend_md", path=workspace_files.get("evidence_trend_markdown"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_offline_review_json", path=workspace_files.get("offline_review_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_offline_review_md", path=workspace_files.get("offline_review_markdown"), target_dir=target_dir, planned=True),
            ],
        },
        {
            "group": "dashboard",
            "label": "Dashboard",
            "files": [
                build_export_file_item(kind="task_dashboard_json", path=dashboard_files.get("json"), target_dir=target_dir),
                build_export_file_item(kind="task_dashboard_md", path=dashboard_files.get("markdown"), target_dir=target_dir),
                build_export_file_item(kind="task_dashboard_html", path=dashboard_files.get("html"), target_dir=target_dir),
            ],
        },
        {
            "group": "sample_set",
            "label": "Sample Set",
            "files": [
                build_export_file_item(kind="task_sample_set_json", path=dashboard_files.get("sample_set_json"), target_dir=target_dir),
                build_export_file_item(kind="task_sample_set_md", path=dashboard_files.get("sample_set_markdown"), target_dir=target_dir),
            ],
        },
    ]
    configuration_files = []
    if workspace_files.get("config_summary_json") or workspace_files.get("config_summary_markdown"):
        configuration_files.extend(
            [
                build_export_file_item(kind="task_workspace_config_summary_json", path=workspace_files.get("config_summary_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_config_summary_md", path=workspace_files.get("config_summary_markdown"), target_dir=target_dir, planned=True),
            ]
        )
    if workspace_files.get("config_preview_json") or workspace_files.get("config_preview_markdown"):
        configuration_files.extend(
            [
                build_export_file_item(kind="task_workspace_config_preview_json", path=workspace_files.get("config_preview_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_config_preview_md", path=workspace_files.get("config_preview_markdown"), target_dir=target_dir, planned=True),
            ]
        )
    if workspace_files.get("config_share_validation_json") or workspace_files.get("config_share_validation_markdown"):
        configuration_files.extend(
            [
                build_export_file_item(kind="task_workspace_config_share_validation_json", path=workspace_files.get("config_share_validation_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_config_share_validation_md", path=workspace_files.get("config_share_validation_markdown"), target_dir=target_dir, planned=True),
            ]
        )
    if workspace_files.get("config_import_draft_json") or workspace_files.get("config_import_draft_markdown"):
        configuration_files.extend(
            [
                build_export_file_item(kind="task_workspace_config_import_draft_json", path=workspace_files.get("config_import_draft_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_config_import_draft_md", path=workspace_files.get("config_import_draft_markdown"), target_dir=target_dir, planned=True),
            ]
        )
    if workspace_files.get("config_import_review_json") or workspace_files.get("config_import_review_markdown"):
        configuration_files.extend(
            [
                build_export_file_item(kind="task_workspace_config_import_review_json", path=workspace_files.get("config_import_review_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_config_import_review_md", path=workspace_files.get("config_import_review_markdown"), target_dir=target_dir, planned=True),
            ]
        )
    if workspace_files.get("config_template_index_json") or workspace_files.get("config_template_index_markdown"):
        configuration_files.extend(
            [
                build_export_file_item(kind="task_workspace_config_template_index_json", path=workspace_files.get("config_template_index_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_config_template_index_md", path=workspace_files.get("config_template_index_markdown"), target_dir=target_dir, planned=True),
            ]
        )
    if workspace_files.get("config_wizard_json") or workspace_files.get("config_wizard_markdown"):
        configuration_files.extend(
            [
                build_export_file_item(kind="task_workspace_config_wizard_json", path=workspace_files.get("config_wizard_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_config_wizard_md", path=workspace_files.get("config_wizard_markdown"), target_dir=target_dir, planned=True),
            ]
        )
    if workspace_files.get("config_review_package_json") or workspace_files.get("config_review_package_markdown"):
        configuration_files.extend(
            [
                build_export_file_item(kind="task_workspace_config_review_package_json", path=workspace_files.get("config_review_package_json"), target_dir=target_dir, planned=True),
                build_export_file_item(kind="task_workspace_config_review_package_md", path=workspace_files.get("config_review_package_markdown"), target_dir=target_dir, planned=True),
            ]
        )
    if configuration_files:
        groups.append(
            {
                "group": "configuration",
                "label": "Configuration",
                "files": configuration_files,
            }
        )
    workbench_group_files = []
    for task_key, files in sorted((workbench_files or {}).items()):
        for kind, path in sorted((files or {}).items()):
            item = build_export_file_item(kind=f"workbench_{kind}", path=path, target_dir=target_dir)
            item["task_key"] = task_key
            workbench_group_files.append(item)
    groups.append({"group": "workbenches", "label": "Workbenches", "files": workbench_group_files})
    snapshot_files = []
    latest_snapshot_id = (workspace.get("snapshot_history") or {}).get("latest_snapshot_id") or ""
    for record in (workspace.get("snapshot_history") or {}).get("snapshots") or []:
        if not isinstance(record, dict):
            continue
        relative_path = str(record.get("relative_path") or "")
        item = build_export_file_item(
            kind="snapshot_task_workspace_json",
            path=target_dir / relative_path if relative_path else "",
            target_dir=target_dir,
            planned=bool(record.get("snapshot_id") == latest_snapshot_id),
        )
        item["snapshot_id"] = record.get("snapshot_id") or ""
        snapshot_files.append(item)
    groups.append({"group": "snapshots", "label": "Workspace Snapshots", "files": snapshot_files})
    file_count = sum(len(group.get("files") or []) for group in groups)
    return {
        "version": "0.19-workspace-export-index",
        "readonly": True,
        "generated_at": workspace.get("generated_at") or database.now_iso(),
        "output_dir": str(target_dir),
        "file_count": file_count,
        "groups": groups,
        "residual_risk": "导出索引只列出本地已生成或本次计划写出的静态文件，不读取远端、不执行命令。",
    }


def build_export_file_item(*, kind: str, path: object, target_dir: Path, planned: bool = False) -> dict:
    path_text = str(path or "").strip()
    path_obj = Path(path_text).expanduser() if path_text else Path()
    return {
        "kind": kind,
        "path": path_text,
        "relative_path": relative_export_path(path_text, target_dir=target_dir),
        "exists": bool(planned or (path_text and path_obj.exists())),
        "planned": bool(planned),
    }


def build_workspace_offline_review(*, workspace: dict, target_dir: Path) -> dict:
    files = []
    for group in (workspace.get("export_index") or {}).get("groups") or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("files") or []:
            if not isinstance(item, dict):
                continue
            files.append(
                {
                    "group": group.get("group") or "",
                    "kind": item.get("kind") or "",
                    "task_key": item.get("task_key") or "",
                    "relative_path": item.get("relative_path") or item.get("path") or "",
                    "exists": bool(item.get("exists")),
                    "planned": bool(item.get("planned")),
                }
            )
    existing_count = sum(1 for item in files if item.get("exists"))
    missing_count = len(files) - existing_count
    return {
        "version": "0.21-workspace-offline-review",
        "readonly": True,
        "generated_at": workspace.get("generated_at") or database.now_iso(),
        "output_dir": str(target_dir),
        "file_count": len(files),
        "existing_file_count": existing_count,
        "missing_file_count": missing_count,
        "files": files,
        "review_steps": [
            "打开 task_workspace.html 作为只读入口。",
            "确认 warning、UI 证据、确认卡、修改历史和回滚 dry-run 状态。",
            "保留 task_workspace*.json/md、task_dashboard.*、task_sample_set.* 和 workbenches/ 目录作为离线审查包。",
            "如存在缺失产物或过期 UI 证据，先回到对应 output_dir/Task Run 人工核对，不在页面内自动复跑。",
        ],
        "readonly_boundaries": [
            "不读取远端系统。",
            "不执行复跑命令。",
            "不执行回滚命令。",
            "不修改业务仓库。",
            "不提交、不推送、不写云效。",
        ],
        "residual_risk": "离线审查包只说明应保留和检查的本地文件；文件是否代表业务验收通过仍需要人工或专项验证确认。",
    }


def build_configuration_review_package_index(*, workspace: dict, target_dir: Path) -> dict:
    wizard = workspace.get("config_wizard") or {}
    configuration_files = []
    for group in (workspace.get("export_index") or {}).get("groups") or []:
        if not isinstance(group, dict) or group.get("group") != "configuration":
            continue
        for item in group.get("files") or []:
            if not isinstance(item, dict):
                continue
            configuration_files.append(
                {
                    "group": group.get("group") or "",
                    "kind": item.get("kind") or "",
                    "relative_path": item.get("relative_path") or item.get("path") or "",
                    "exists": bool(item.get("exists")),
                    "planned": bool(item.get("planned")),
                }
            )
    existing_count = sum(1 for item in configuration_files if item.get("exists"))
    missing_count = len(configuration_files) - existing_count
    commands = [
        {
            "key": item.get("key") or "",
            "label": item.get("label") or "",
            "copy_target_id": item.get("copy_target_id") or "",
            "command": item.get("command") or "",
            "will_execute": False,
        }
        for item in wizard.get("copy_commands") or []
        if isinstance(item, dict)
    ]
    manual_items = [
        {
            "key": item.get("key") or "",
            "label": item.get("label") or "",
            "required": bool(item.get("required")),
            "confirmed_by_harness": bool(item.get("confirmed_by_harness")),
        }
        for item in wizard.get("manual_checklist") or []
        if isinstance(item, dict)
    ]
    readability = build_configuration_review_package_readability(
        files=configuration_files,
        commands=commands,
        manual_items=manual_items,
        missing_count=missing_count,
    )
    return {
        "version": "0.33-configuration-review-package-index",
        "readonly": True,
        "generated_at": workspace.get("generated_at") or database.now_iso(),
        "output_dir": str(target_dir),
        "will_apply_configuration": False,
        "will_write_real_config_dir": False,
        "external_writes_enabled": False,
        "remote_connection_tests_enabled": False,
        "file_count": len(configuration_files),
        "existing_file_count": existing_count,
        "missing_file_count": missing_count,
        "command_count": len(commands),
        "manual_confirmation_count": len(manual_items),
        "entry_points": [
            {"label": "只读 HTML 工作台", "relative_path": "task_workspace.html"},
            {"label": "配置向导 Markdown", "relative_path": workspace.get("links", {}).get("config_wizard_markdown") or "task_workspace_config_wizard.md"},
            {"label": "审查包索引 Markdown", "relative_path": workspace.get("links", {}).get("config_review_package_markdown") or "task_workspace_config_review_package.md"},
        ],
        "files": configuration_files,
        "commands": commands,
        "manual_confirmation_items": manual_items,
        "ui_readability": readability,
        "review_steps": [
            "打开 task_workspace.html，先看配置向导和配置审查包。",
            "逐项检查配置摘要、配置预览、分享校验、导入回读、模板索引和配置向导产物是否存在。",
            "只复制复跑命令文本，不在页面内执行命令；需要执行时由人工在终端确认。",
            "人工确认 provider、profile、项目路径、输出目录、credential key 和状态流转规则后，再决定是否复制到个人配置目录。",
        ],
        "readonly_boundaries": [
            "不会应用配置。",
            "不会写入 ~/.his-harness。",
            "不会保存真实 token。",
            "不会测试远端账号。",
            "不会读取或写入云效/TAPD。",
        ],
        "residual_risk": "配置审查包索引只证明本地只读产物已经被索引；人工复制后的真实路径、凭证、远端只读权限和业务项目可用性仍需单独验证。",
    }


def build_configuration_review_package_readability(*, files: list[dict], commands: list[dict], manual_items: list[dict], missing_count: int) -> dict:
    required_items = [item for item in manual_items if item.get("required")]
    optional_items = [item for item in manual_items if not item.get("required")]
    unconfirmed_required = [item for item in required_items if not item.get("confirmed_by_harness")]
    status = "ready_for_manual_review" if missing_count == 0 else "missing_files"
    handoff_lines = [
        "打开 task_workspace.html，进入配置审查包。",
        f"配置产物文件：共 {len(files)} 个，缺失 {missing_count} 个。",
        f"复跑命令：{len(commands)} 条，仅复制文本，不自动执行。",
        f"必填人工确认：{len(required_items)} 项，未由 Harness 自动确认 {len(unconfirmed_required)} 项。",
        "确认 provider、profile、路径、credential key 和只读边界后，再由人工决定是否复制到个人配置目录。",
    ]
    return {
        "version": "0.33-configuration-review-package-readability",
        "readonly": True,
        "file_filter_options": {
            "statuses": ["all", "present", "missing"],
            "search_fields": ["kind", "relative_path"],
        },
        "file_summary": {
            "total_file_count": len(files),
            "present_file_count": len(files) - missing_count,
            "missing_file_count": missing_count,
        },
        "manual_confirmation_groups": [
            {
                "group": "required",
                "label": "必填确认项",
                "count": len(required_items),
                "unconfirmed_count": len(unconfirmed_required),
                "keys": [item.get("key") or "" for item in required_items],
            },
            {
                "group": "optional",
                "label": "可选确认项",
                "count": len(optional_items),
                "unconfirmed_count": len([item for item in optional_items if not item.get("confirmed_by_harness")]),
                "keys": [item.get("key") or "" for item in optional_items],
            },
        ],
        "required_manual_confirmation_count": len(required_items),
        "unconfirmed_required_count": len(unconfirmed_required),
        "handoff_summary": {
            "status": status,
            "line_count": len(handoff_lines),
            "lines": handoff_lines,
        },
        "empty_states": [
            {"kind": "no_matching_files", "message": "当前筛选条件下没有配置审查包文件。"},
            {"kind": "no_missing_files", "message": "当前配置审查包文件均已索引，但仍需要人工确认配置语义。"},
        ],
    }


def relative_export_path(path_text: str, *, target_dir: Path) -> str:
    if not path_text:
        return ""
    try:
        return str(Path(path_text).expanduser().resolve().relative_to(target_dir))
    except (OSError, ValueError):
        return path_text


def build_workspace_snapshot_comparison(*, previous_workspace: dict, current_workspace: dict) -> dict:
    current_summary = build_workspace_snapshot_summary(current_workspace)
    if not previous_workspace:
        return {
            "version": "0.18-workspace-snapshot-comparison",
            "readonly": True,
            "compared": False,
            "status": "no_previous_snapshot",
            "previous_generated_at": "",
            "current_generated_at": current_workspace.get("generated_at") or "",
            "summary_delta": {},
            "added_tasks": [],
            "removed_tasks": [],
            "changed_tasks": [],
            "current_summary": current_summary,
            "residual_risk": "当前导出目录没有上一版 task_workspace.json，只能作为首个快照保存。",
        }
    previous_summary = build_workspace_snapshot_summary(previous_workspace)
    previous_tasks = build_workspace_snapshot_task_map(previous_workspace)
    current_tasks = build_workspace_snapshot_task_map(current_workspace)
    previous_keys = set(previous_tasks)
    current_keys = set(current_tasks)
    added = [current_tasks[key] for key in sorted(current_keys - previous_keys)]
    removed = [previous_tasks[key] for key in sorted(previous_keys - current_keys)]
    changed = []
    for task_key in sorted(previous_keys & current_keys):
        before = previous_tasks[task_key]
        after = current_tasks[task_key]
        field_changes = []
        for field in [
            "status",
            "verification_status",
            "ui_evidence_status",
            "latest_run_id",
            "run_count",
            "warning_count",
            "warning_codes",
            "change_count",
            "requirement_calibration_status",
        ]:
            if before.get(field) != after.get(field):
                field_changes.append(
                    {
                        "field": field,
                        "previous": before.get(field),
                        "current": after.get(field),
                    }
                )
        if field_changes:
            changed.append(
                {
                    "task_key": task_key,
                    "entity_id": after.get("entity_id") or before.get("entity_id") or "",
                    "title": after.get("entity_title") or before.get("entity_title") or "",
                    "changed_fields": [item["field"] for item in field_changes],
                    "field_changes": field_changes,
                }
            )
    return {
        "version": "0.18-workspace-snapshot-comparison",
        "readonly": True,
        "compared": True,
        "status": "compared",
        "previous_version": previous_workspace.get("version") or "",
        "current_version": current_workspace.get("version") or "",
        "previous_generated_at": previous_workspace.get("generated_at") or "",
        "current_generated_at": current_workspace.get("generated_at") or "",
        "previous_summary": previous_summary,
        "current_summary": current_summary,
        "summary_delta": build_snapshot_summary_delta(previous=previous_summary, current=current_summary),
        "added_tasks": added,
        "removed_tasks": removed,
        "changed_tasks": changed,
        "residual_risk": "快照对比只比较上一次和本次导出的 workspace JSON 摘要字段；它不重新验证产物、不执行复跑或回滚。",
    }


def build_workspace_snapshot_summary(workspace: dict) -> dict:
    summary = workspace.get("summary") or {}
    warning_summary = workspace.get("warning_summary") or {}
    sample_set = workspace.get("sample_set") or {}
    entries = workspace.get("entries") or []
    return {
        "task_count": int(summary.get("task_count") or len(entries)),
        "run_count": int(summary.get("run_count") or sum(get_workspace_entry_run_count(workspace, entry) for entry in entries if isinstance(entry, dict))),
        "warning_count": int(warning_summary.get("total_warning_count") or sum(int((entry or {}).get("warning_count") or 0) for entry in entries if isinstance(entry, dict))),
        "task_count_with_warnings": int(warning_summary.get("task_count_with_warnings") or sum(1 for entry in entries if int((entry or {}).get("warning_count") or 0))),
        "sample_count": int(sample_set.get("count") or 0),
        "change_count": sum(int((entry or {}).get("change_count") or 0) for entry in entries if isinstance(entry, dict)),
    }


def build_snapshot_summary_delta(*, previous: dict, current: dict) -> dict:
    return {
        f"{key}_delta": int(current.get(key) or 0) - int(previous.get(key) or 0)
        for key in ["task_count", "run_count", "warning_count", "task_count_with_warnings", "sample_count", "change_count"]
    }


def build_workspace_snapshot_task_map(workspace: dict) -> dict[str, dict]:
    details_by_key = {
        str(detail.get("task_key") or detail.get("task_id") or ""): detail
        for detail in workspace.get("task_details") or []
        if isinstance(detail, dict)
    }
    result = {}
    for entry in workspace.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        task_key = str(entry.get("task_key") or entry.get("task_id") or "").strip()
        if not task_key:
            continue
        calibration = entry.get("requirement_calibration") or {}
        detail = details_by_key.get(task_key) or {}
        result[task_key] = {
            "task_key": task_key,
            "task_id": entry.get("task_id"),
            "entity_id": entry.get("entity_id") or "",
            "entity_title": entry.get("entity_title") or "",
            "status": entry.get("status") or "",
            "verification_status": entry.get("verification_status") or "",
            "ui_evidence_status": entry.get("ui_evidence_status") or "",
            "latest_run_id": entry.get("latest_run_id"),
            "run_count": len(detail.get("runs") or []),
            "warning_count": int(entry.get("warning_count") or 0),
            "warning_codes": sorted(str(code) for code in entry.get("warning_codes") or [] if str(code).strip()),
            "change_count": int(entry.get("change_count") or 0),
            "requirement_calibration_status": calibration.get("status") or "",
        }
    return result


def get_workspace_entry_run_count(workspace: dict, entry: dict) -> int:
    task_key = str(entry.get("task_key") or entry.get("task_id") or "")
    for detail in workspace.get("task_details") or []:
        if isinstance(detail, dict) and str(detail.get("task_key") or detail.get("task_id") or "") == task_key:
            return len(detail.get("runs") or [])
    return 0


def workspace_snapshot_id(workspace: dict) -> str:
    generated_at = str(workspace.get("generated_at") or database.now_iso()).strip()
    return "snapshot-" + safe_slug(generated_at)


def workspace_snapshot_relative_path(snapshot_id: str) -> str:
    return f"workspace_snapshots/{safe_slug(snapshot_id)}/task_workspace.json"


def build_workspace_snapshot_record(*, workspace: dict, target_dir: Path, snapshot_path: Path | None = None, planned: bool = False) -> dict:
    snapshot_id = workspace_snapshot_id(workspace)
    relative_path = relative_export_path(str(snapshot_path), target_dir=target_dir) if snapshot_path else workspace_snapshot_relative_path(snapshot_id)
    summary = build_workspace_snapshot_summary(workspace)
    task_map = build_workspace_snapshot_task_map(workspace)
    warning_codes = []
    for task in task_map.values():
        warning_codes.extend(task.get("warning_codes") or [])
    return {
        "snapshot_id": snapshot_id,
        "version": workspace.get("version") or "",
        "generated_at": workspace.get("generated_at") or "",
        "relative_path": relative_path,
        "exists": bool((target_dir / relative_path).exists()) if relative_path else False,
        "planned": bool(planned),
        "task_count": summary.get("task_count", 0),
        "run_count": summary.get("run_count", 0),
        "warning_count": summary.get("warning_count", 0),
        "task_count_with_warnings": summary.get("task_count_with_warnings", 0),
        "sample_count": summary.get("sample_count", 0),
        "change_count": summary.get("change_count", 0),
        "warning_codes": sorted(unique_keep_order(warning_codes)),
    }


def archive_workspace_snapshot(*, target_dir: Path, workspace: dict) -> dict:
    if not workspace:
        return {}
    record = build_workspace_snapshot_record(workspace=workspace, target_dir=target_dir)
    snapshot_path = target_dir / record["relative_path"]
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(workspace, ensure_ascii=False, indent=2), encoding="utf-8")
    record["exists"] = True
    return record


def read_workspace_snapshots(target_dir: Path) -> dict[str, dict]:
    result = {}
    snapshot_root = target_dir / "workspace_snapshots"
    if not snapshot_root.exists():
        return result
    for path in sorted(snapshot_root.glob("*/task_workspace.json")):
        workspace = read_json_file(path)
        if not workspace:
            continue
        record = build_workspace_snapshot_record(workspace=workspace, target_dir=target_dir, snapshot_path=path)
        record["exists"] = True
        result[record["snapshot_id"]] = {"record": record, "workspace": workspace}
    return result


def build_workspace_snapshot_history(*, target_dir: Path, current_workspace: dict) -> dict:
    snapshots = read_workspace_snapshots(target_dir)
    current_id = workspace_snapshot_id(current_workspace)
    snapshots[current_id] = {
        "record": build_workspace_snapshot_record(workspace=current_workspace, target_dir=target_dir, planned=True),
        "workspace": current_workspace,
    }
    ordered_items = sorted(
        snapshots.values(),
        key=lambda item: ((item.get("record") or {}).get("generated_at") or "", (item.get("record") or {}).get("snapshot_id") or ""),
    )[-WORKSPACE_SNAPSHOT_HISTORY_LIMIT:]
    records = [item.get("record") or {} for item in ordered_items]
    workspaces_by_id = {
        str((item.get("record") or {}).get("snapshot_id") or ""): item.get("workspace") or {}
        for item in ordered_items
        if (item.get("record") or {}).get("snapshot_id")
    }
    comparisons = build_workspace_snapshot_pair_comparisons(records=records, workspaces_by_id=workspaces_by_id)
    default_pair_id = comparisons[-1].get("pair_id") if comparisons else ""
    latest_snapshot_id = records[-1].get("snapshot_id") if records else ""
    return {
        "version": "0.19-workspace-snapshot-history",
        "readonly": True,
        "generated_at": current_workspace.get("generated_at") or database.now_iso(),
        "snapshot_dir": "workspace_snapshots",
        "retention_limit": WORKSPACE_SNAPSHOT_HISTORY_LIMIT,
        "snapshot_count": len(records),
        "latest_snapshot_id": latest_snapshot_id,
        "default_pair_id": default_pair_id,
        "snapshots": records,
        "comparisons": comparisons,
        "residual_risk": "多快照浏览只读取同一输出目录下已归档的 task_workspace.json；不读取远端、不复跑验证、不执行回滚。",
    }


def build_workspace_snapshot_pair_comparisons(*, records: list[dict], workspaces_by_id: dict[str, dict]) -> list[dict]:
    comparisons = []
    for left_index, previous_record in enumerate(records):
        previous_id = str(previous_record.get("snapshot_id") or "")
        previous_workspace = workspaces_by_id.get(previous_id) or {}
        if not previous_workspace:
            continue
        for current_record in records[left_index + 1 :]:
            current_id = str(current_record.get("snapshot_id") or "")
            current_workspace = workspaces_by_id.get(current_id) or {}
            if not current_workspace:
                continue
            comparison = build_workspace_snapshot_comparison(previous_workspace=previous_workspace, current_workspace=current_workspace)
            comparisons.append(
                {
                    "pair_id": f"{previous_id}__{current_id}",
                    "previous_snapshot_id": previous_id,
                    "current_snapshot_id": current_id,
                    "previous_generated_at": previous_record.get("generated_at") or "",
                    "current_generated_at": current_record.get("generated_at") or "",
                    "summary_delta": comparison.get("summary_delta") or {},
                    "added_tasks": comparison.get("added_tasks") or [],
                    "removed_tasks": comparison.get("removed_tasks") or [],
                    "changed_tasks": comparison.get("changed_tasks") or [],
                }
            )
    return comparisons


def build_workspace_evidence_trend(*, snapshot_history: dict, target_dir: Path, current_workspace: dict) -> dict:
    records = snapshot_history.get("snapshots") or []
    current_id = workspace_snapshot_id(current_workspace)
    task_points: dict[str, dict] = {}
    timeline = []
    for record in records:
        if not isinstance(record, dict):
            continue
        snapshot_id = str(record.get("snapshot_id") or "")
        workspace = current_workspace if snapshot_id == current_id else read_json_file(target_dir / str(record.get("relative_path") or ""))
        if not workspace:
            continue
        summary = build_workspace_snapshot_summary(workspace)
        timeline.append(
            {
                "snapshot_id": snapshot_id,
                "generated_at": record.get("generated_at") or "",
                "task_count": summary.get("task_count", 0),
                "run_count": summary.get("run_count", 0),
                "warning_count": summary.get("warning_count", 0),
                "task_count_with_warnings": summary.get("task_count_with_warnings", 0),
                "change_count": summary.get("change_count", 0),
            }
        )
        for task in build_workspace_snapshot_task_map(workspace).values():
            task_key = str(task.get("task_key") or "")
            if not task_key:
                continue
            bucket = task_points.setdefault(
                task_key,
                {
                    "task_key": task_key,
                    "entity_id": task.get("entity_id") or "",
                    "title": task.get("entity_title") or "",
                    "points": [],
                },
            )
            bucket["entity_id"] = task.get("entity_id") or bucket.get("entity_id") or ""
            bucket["title"] = task.get("entity_title") or bucket.get("title") or ""
            bucket["points"].append(
                {
                    "snapshot_id": snapshot_id,
                    "generated_at": record.get("generated_at") or "",
                    "status": task.get("status") or "",
                    "verification_status": task.get("verification_status") or "",
                    "ui_evidence_status": task.get("ui_evidence_status") or "",
                    "warning_count": int(task.get("warning_count") or 0),
                    "warning_codes": task.get("warning_codes") or [],
                    "run_count": int(task.get("run_count") or 0),
                    "change_count": int(task.get("change_count") or 0),
                    "requirement_calibration_status": task.get("requirement_calibration_status") or "",
                }
            )
    tasks = []
    for task in task_points.values():
        points = task.get("points") or []
        warning_counts = [int(point.get("warning_count") or 0) for point in points]
        ui_statuses = unique_keep_order([str(point.get("ui_evidence_status") or "") for point in points])
        verification_statuses = unique_keep_order([str(point.get("verification_status") or "") for point in points])
        calibration_statuses = unique_keep_order([str(point.get("requirement_calibration_status") or "") for point in points])
        task["trend_summary"] = {
            "point_count": len(points),
            "latest_ui_evidence_status": (points[-1] or {}).get("ui_evidence_status") if points else "",
            "ui_evidence_statuses": ui_statuses,
            "verification_statuses": verification_statuses,
            "requirement_calibration_statuses": calibration_statuses,
            "warning_count_min": min(warning_counts) if warning_counts else 0,
            "warning_count_max": max(warning_counts) if warning_counts else 0,
            "warning_count_latest": warning_counts[-1] if warning_counts else 0,
            "changed": len(set(ui_statuses + verification_statuses + calibration_statuses)) > 1 or len(set(warning_counts)) > 1,
        }
        tasks.append(task)
    return {
        "version": "0.19-workspace-evidence-trend",
        "readonly": True,
        "generated_at": current_workspace.get("generated_at") or database.now_iso(),
        "snapshot_count": len(timeline),
        "timeline": timeline,
        "tasks": sorted(tasks, key=lambda item: (item.get("entity_id") or "", item.get("task_key") or "")),
        "residual_risk": "证据趋势只基于已归档 workspace 摘要字段，不重新读取证据文件、不判断业务验收结论。",
    }


def build_workspace_snapshot_detail(*, snapshot_history: dict, target_dir: Path, current_workspace: dict) -> dict:
    current_id = workspace_snapshot_id(current_workspace)
    details = []
    for record in snapshot_history.get("snapshots") or []:
        if not isinstance(record, dict):
            continue
        snapshot_id = str(record.get("snapshot_id") or "")
        workspace = current_workspace if snapshot_id == current_id else read_json_file(target_dir / str(record.get("relative_path") or ""))
        if not workspace:
            continue
        task_map = build_workspace_snapshot_task_map(workspace)
        task_summaries = [
            {
                "task_key": task.get("task_key") or "",
                "entity_id": task.get("entity_id") or "",
                "title": task.get("entity_title") or "",
                "status": task.get("status") or "",
                "verification_status": task.get("verification_status") or "",
                "ui_evidence_status": task.get("ui_evidence_status") or "",
                "warning_count": int(task.get("warning_count") or 0),
                "warning_codes": task.get("warning_codes") or [],
                "run_count": int(task.get("run_count") or 0),
                "change_count": int(task.get("change_count") or 0),
                "requirement_calibration_status": task.get("requirement_calibration_status") or "",
            }
            for task in task_map.values()
        ]
        warning_codes = []
        for task in task_summaries:
            warning_codes.extend(task.get("warning_codes") or [])
        details.append(
            {
                "snapshot_id": snapshot_id,
                "generated_at": record.get("generated_at") or "",
                "relative_path": record.get("relative_path") or "",
                "summary": build_workspace_snapshot_summary(workspace),
                "warning_codes": sorted(unique_keep_order(warning_codes)),
                "task_summaries": sorted(task_summaries, key=lambda item: (item.get("entity_id") or "", item.get("task_key") or "")),
            }
        )
    return {
        "version": "0.20-workspace-snapshot-detail",
        "readonly": True,
        "snapshot_count": len(details),
        "snapshots": details,
        "residual_risk": "快照详情只展示已归档 workspace 摘要，不打开业务页面、不复跑验证、不读取远端。",
    }


def workspace_task_detail_to_html(detail: dict) -> str:
    task_key = str(detail.get("task_key") or detail.get("task_id") or "")
    detail_id = str(detail.get("detail_id") or f"detail-{safe_slug(task_key or 'task')}")
    tabs = [
        ("overview", "概览", workspace_detail_overview_html(detail)),
        ("runs", "Run 历史", workspace_detail_runs_html(detail)),
        ("calibration", "需求理解确认卡", workspace_detail_calibration_html(detail)),
        ("requirement-evidence", "需求来源证据", workspace_detail_requirement_evidence_html(detail)),
        ("changes", "修改历史", workspace_detail_changes_html(detail)),
        ("rollback", "回滚 dry-run", workspace_detail_rollback_html(detail)),
        ("evidence", "证据预览", workspace_detail_evidence_html(detail)),
        ("commands", "可复制命令", workspace_detail_commands_html(detail)),
    ]
    button_html = []
    panel_html = []
    for index, (tab_key, label, body) in enumerate(tabs):
        active = " active" if index == 0 else ""
        hidden = "" if index == 0 else " hidden"
        button_html.append(
            f'<button type="button" class="detail-tab-button{active}" data-tab-button="{escape_html(tab_key)}" '
            f'onclick="switchDetailTab(this, \'{escape_html(tab_key)}\')">{escape_html(label)}</button>'
        )
        panel_html.append(
            f'<div class="detail-tab-panel{active}" data-tab="{escape_html(tab_key)}"{hidden}>{body}</div>'
        )
    return "\n".join(
        [
            f'<section class="task-detail" id="{escape_html(detail_id)}" data-detail-task-key="{escape_html(task_key)}" hidden>',
            '<header class="detail-header">',
            "<div>",
            f'<h2>{escape_html(detail.get("entity_id") or task_key or "任务详情")}</h2>',
            f'<p>{escape_html(detail.get("entity_title") or "-")}</p>',
            "</div>",
            '<span class="readonly-badge">只读</span>',
            "</header>",
            '<div class="detail-tabs" role="tablist">',
            *button_html,
            "</div>",
            '<div class="detail-tab-content">',
            *panel_html,
            "</div>",
            "</section>",
        ]
    )


def workspace_detail_overview_html(detail: dict) -> str:
    overview = detail.get("overview") or {}
    warnings = detail.get("evidence_warnings") or []
    rows = [
        ("Task Key", detail.get("task_key") or "-"),
        ("DFHIS", detail.get("entity_id") or "-"),
        ("状态", overview.get("status") or "-"),
        ("验证", overview.get("verification_status") or "-"),
        ("UI证据", overview.get("ui_evidence_status") or "-"),
        ("最新 Run", overview.get("latest_run_id") or "-"),
        ("产物数", overview.get("artifact_count") or 0),
        ("修改次数", overview.get("change_count") or 0),
        ("产物目录", overview.get("latest_output_dir") or "-"),
    ]
    links = []
    if overview.get("workbench_markdown"):
        links.append(f'<a href="{escape_html(overview.get("workbench_markdown"))}">task_workbench.md</a>')
    if overview.get("workbench_json"):
        links.append(f'<a href="{escape_html(overview.get("workbench_json"))}">task_workbench.json</a>')
    warning_items = [
        f'<li><code>{escape_html(item.get("code") or "-")}</code> {escape_html(item.get("message") or "")}</li>'
        for item in warnings
        if isinstance(item, dict)
    ]
    if not warning_items:
        warning_items.append("<li>暂无</li>")
    return "\n".join(
        [
            workspace_detail_kv_grid(rows),
            f'<p class="detail-links">Workbench：{" / ".join(links) if links else "-"}</p>',
            "<h3>Warning</h3>",
            "<ul>",
            *warning_items,
            "</ul>",
        ]
    )


def workspace_detail_runs_html(detail: dict) -> str:
    rows = []
    for run in detail.get("runs") or []:
        rows.append(
            "<tr>"
            f"<td>{escape_html(run.get('task_run_id') or '-')}</td>"
            f"<td>{escape_html(run.get('run_id') or '-')}</td>"
            f"<td>{escape_html(run.get('execution_mode') or '-')}</td>"
            f"<td>{escape_html(run.get('status') or '-')}</td>"
            f"<td>{escape_html(run.get('verification_status') or run.get('evaluation_status') or '-')}</td>"
            f"<td>{escape_html((run.get('ui_evidence') or {}).get('status') or '-')}</td>"
            f"<td>{escape_html(run.get('artifact_count') or 0)}</td>"
            f"<td><code>{escape_html(run.get('output_dir') or '-')}</code></td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="8">暂无 run。</td></tr>')
    comparison = detail.get("run_history_comparison") or {}
    changes = comparison.get("changes") or []
    change_lines = [
        f'<li>{escape_html(item.get("label") or item.get("field") or "-")}：'
        f'{escape_html(item.get("previous") or "-")} -> {escape_html(item.get("latest") or "-")}</li>'
        for item in changes
        if isinstance(item, dict)
    ]
    if not change_lines:
        change_lines.append("<li>暂无差异</li>")
    return "\n".join(
        [
            '<table class="detail-table">',
            "<thead><tr><th>Task Run</th><th>Run ID</th><th>模式</th><th>状态</th><th>验证</th><th>UI证据</th><th>产物</th><th>目录</th></tr></thead>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
            "<h3>最新/上一条差异</h3>",
            "<ul>",
            *change_lines,
            "</ul>",
        ]
    )


def workspace_detail_calibration_html(detail: dict) -> str:
    calibration = detail.get("requirement_calibration") or {}
    rows = [
        ("状态", calibration.get("status") or "missing"),
        ("置信度", calibration.get("confidence") or "-"),
        ("参数", ", ".join(calibration.get("parameter_names") or []) or "-"),
        ("来源优先级", ", ".join(calibration.get("source_priority") or []) or "-"),
        ("Warning", ", ".join(calibration.get("warning_types") or []) or "-"),
        ("结论", calibration.get("summary") or "-"),
    ]
    links = []
    if calibration.get("markdown_path"):
        links.append(f'<a href="{escape_html(calibration.get("markdown_path"))}">requirement_calibration.md</a>')
    if calibration.get("json_path"):
        links.append(f'<a href="{escape_html(calibration.get("json_path"))}">requirement_calibration.json</a>')
    preview = workspace_preview_lines_html(calibration.get("markdown_preview") or [])
    return "\n".join(
        [
            workspace_detail_kv_grid(rows),
            f'<p class="detail-links">原始文件：{" / ".join(links) if links else "-"}</p>',
            "<h3>摘要</h3>",
            preview,
        ]
    )


def workspace_detail_requirement_evidence_html(detail: dict) -> str:
    evidence = detail.get("requirement_evidence") or {}
    rows = [
        ("状态", evidence.get("status") or "missing"),
        ("来源类型", evidence.get("source_type") or "-"),
        ("外部编号", evidence.get("external_id") or "-"),
        ("需求状态", evidence.get("requirement_status") or "-"),
        ("负责人", evidence.get("assignee") or "-"),
        ("标题", evidence.get("title") or "-"),
        ("附件", evidence.get("attachment_count") or 0),
        ("图片", evidence.get("image_count") or 0),
        ("评论", evidence.get("comment_count") or 0),
        ("Warning", ", ".join(evidence.get("warning_codes") or []) or "-"),
    ]
    links = []
    if evidence.get("markdown_path"):
        links.append(f'<a href="{escape_html(evidence.get("markdown_path"))}">requirement_evidence.md</a>')
    if evidence.get("json_path"):
        links.append(f'<a href="{escape_html(evidence.get("json_path"))}">requirement_evidence.json</a>')
    preview = workspace_preview_lines_html(evidence.get("markdown_preview") or [])
    return "\n".join(
        [
            '<p class="readonly-note">需求来源证据来自本地归一化产物；本页不会重新读取云效、TAPD 或其他需求系统。</p>',
            workspace_detail_kv_grid(rows),
            f'<p class="detail-links">原始文件：{" / ".join(links) if links else "-"}</p>',
            "<h3>摘要</h3>",
            preview,
        ]
    )


def workspace_detail_changes_html(detail: dict) -> str:
    history = detail.get("change_history") or {}
    changes = history.get("changes") or []
    rows = []
    for change in changes:
        rows.append(
            "<tr>"
            f"<td>{escape_html(change.get('change_sequence') or '-')}</td>"
            f"<td><code>{escape_html(change.get('change_id') or '-')}</code></td>"
            f"<td>{escape_html(change.get('status') or '-')}</td>"
            f"<td>{escape_html(change.get('verification_status') or '-')}</td>"
            f"<td><code>{escape_html(change.get('diff_path') or '-')}</code></td>"
            f"<td>{escape_html(change.get('diff_summary') or '-')}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6">暂无修改记录。</td></tr>')
    task_slug = safe_slug(str(detail.get("task_key") or detail.get("task_id") or "task"))
    return "\n".join(
        [
            workspace_detail_kv_grid(
                [
                    ("修改次数", history.get("change_count") or 0),
                    ("回滚模式", format_rollback_mode(history.get("rollback_mode") or "dry_run_only")),
                    ("最新 Change", (history.get("latest_change") or {}).get("change_id") or "-"),
                ]
            ),
            f'<p class="detail-links"><a href="workbenches/{escape_html(task_slug)}/task_change_history.md">task_change_history.md</a> / '
            f'<a href="workbenches/{escape_html(task_slug)}/task_change_history.json">task_change_history.json</a></p>',
            '<table class="detail-table">',
            "<thead><tr><th>序号</th><th>Change ID</th><th>状态</th><th>验证</th><th>Diff</th><th>摘要</th></tr></thead>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
        ]
    )


def workspace_detail_rollback_html(detail: dict) -> str:
    command = (detail.get("commands") or {}).get("rollback_dry_run") or ""
    return "\n".join(
        [
            '<p class="readonly-note">这里仅提供回滚 dry-run 计划生成命令，不会自动执行反向 patch，不会修改业务仓库。</p>',
            f'<pre class="command-box"><code>{escape_html(command or "暂无可回滚修改记录")}</code></pre>',
        ]
    )


def workspace_detail_evidence_html(detail: dict) -> str:
    sections = ((detail.get("evidence_preview") or {}).get("sections") or [])
    existing_count = sum(1 for section in sections if isinstance(section, dict) and section.get("exists"))
    missing_count = sum(1 for section in sections if isinstance(section, dict) and not section.get("exists"))
    kinds = [
        str(section.get("kind") or section.get("label") or "")
        for section in sections
        if isinstance(section, dict) and str(section.get("kind") or section.get("label") or "").strip()
    ]
    summary = (
        '<div class="evidence-preview-summary">'
        f'<span>证据项 <strong>{escape_html(len(sections))}</strong></span>'
        f'<span>存在 <strong>{escape_html(existing_count)}</strong></span>'
        f'<span>缺失 <strong>{escape_html(missing_count)}</strong></span>'
        f'<span>类型 <code>{escape_html(", ".join(kinds[:8]) or "-")}</code></span>'
        "</div>"
    )
    items = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        preview = workspace_preview_lines_html(section.get("preview_lines") or [])
        link = str(section.get("link") or section.get("path") or "")
        link_html = f'<a href="{escape_html(link)}">{escape_html(Path(link).name or link)}</a>' if link else "-"
        items.append(
            f'<details class="preview-item" data-evidence-kind="{escape_html(section.get("kind") or "-")}" data-evidence-exists="{str(bool(section.get("exists"))).lower()}">'
            f'<summary>{escape_html(section.get("label") or section.get("kind") or "-")} · {"存在" if section.get("exists") else "缺失"}</summary>'
            f'<p><code>{escape_html(section.get("kind") or "-")}</code> · {link_html}</p>'
            f'<p><code>{escape_html(section.get("path") or "-")}</code></p>'
            f"{preview}"
            "</details>"
        )
    if not items:
        items.append('<p class="readonly-note">暂无可预览证据。</p>')
    return "\n".join([summary, '<div class="preview-grid">', *items, "</div>"])


def workspace_detail_commands_html(detail: dict) -> str:
    blocks = []
    for name, command in (detail.get("commands") or {}).items():
        blocks.extend(
            [
                f"<h3>{escape_html(name)}</h3>",
                f'<pre class="command-box"><code>{escape_html(command)}</code></pre>',
            ]
        )
    if not blocks:
        blocks.append('<p class="readonly-note">暂无可复制命令。</p>')
    return "\n".join(blocks)


def workspace_detail_kv_grid(rows: list[tuple[str, object]]) -> str:
    items = []
    for label, value in rows:
        items.append(f"<dt>{escape_html(label)}</dt><dd>{escape_html(value)}</dd>")
    return "\n".join(['<dl class="detail-kv">', *items, "</dl>"])


def workspace_preview_lines_html(lines: list[str]) -> str:
    if not lines:
        return '<pre class="preview-box"><code>暂无预览</code></pre>'
    return '<pre class="preview-box"><code>' + escape_html("\n".join(str(line) for line in lines)) + "</code></pre>"


def workspace_export_index_to_markdown(index: dict) -> str:
    lines = [
        "# Task Workspace 导出索引",
        "",
        f"- 版本：{index.get('version')}",
        f"- 只读：{'是' if index.get('readonly') else '否'}",
        f"- 输出目录：{index.get('output_dir') or '-'}",
        f"- 文件数：{index.get('file_count', 0)}",
        "",
    ]
    for group in index.get("groups") or []:
        lines.extend([f"## {group.get('label') or group.get('group') or '-'}", "", "| Kind | Task | Exists | Relative Path |", "| --- | --- | --- | --- |"])
        for item in group.get("files") or []:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item.get("kind") or "-"),
                        str(item.get("task_key") or "-"),
                        "是" if item.get("exists") else "否",
                        str(item.get("relative_path") or item.get("path") or "-"),
                    ]
                )
                + " |"
            )
        if not group.get("files"):
            lines.append("| - | - | - | - |")
        lines.append("")
    lines.extend(["## 残余风险", "", f"- {index.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def workspace_offline_review_to_markdown(review: dict) -> str:
    lines = [
        "# Task Workspace 离线审查包",
        "",
        f"- 版本：{review.get('version')}",
        f"- 只读：{'是' if review.get('readonly') else '否'}",
        f"- 输出目录：{review.get('output_dir') or '-'}",
        f"- 文件数：{review.get('file_count', 0)}",
        f"- 已存在：{review.get('existing_file_count', 0)}",
        f"- 缺失：{review.get('missing_file_count', 0)}",
        "",
        "## 审查步骤",
        "",
    ]
    for step in review.get("review_steps") or []:
        lines.append(f"- {step}")
    lines.extend(["", "## 只读边界", ""])
    for boundary in review.get("readonly_boundaries") or []:
        lines.append(f"- {boundary}")
    lines.extend(["", "## 文件清单", "", "| Group | Kind | Task | Exists | Relative Path |", "| --- | --- | --- | --- | --- |"])
    for item in review.get("files") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("group") or "-"),
                    str(item.get("kind") or "-"),
                    str(item.get("task_key") or "-"),
                    "是" if item.get("exists") else "否",
                    str(item.get("relative_path") or "-"),
                ]
            )
            + " |"
        )
    if not review.get("files"):
        lines.append("| - | - | - | - | - |")
    lines.extend(["", "## 残余风险", "", f"- {review.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def configuration_review_package_index_to_markdown(index: dict) -> str:
    readability = index.get("ui_readability") or {}
    handoff = readability.get("handoff_summary") or {}
    lines = [
        "# 配置审查包索引",
        "",
        f"- 版本：{index.get('version')}",
        f"- 只读：{'是' if index.get('readonly') else '否'}",
        f"- 输出目录：{index.get('output_dir') or '-'}",
        f"- 文件数：{index.get('file_count', 0)}",
        f"- 已存在：{index.get('existing_file_count', 0)}",
        f"- 缺失：{index.get('missing_file_count', 0)}",
        f"- 复跑命令：{index.get('command_count', 0)}",
        f"- 人工确认：{index.get('manual_confirmation_count', 0)}",
        "",
        "## 交接摘要",
        "",
    ]
    for line in handoff.get("lines") or []:
        lines.append(f"- {line}")
    if not handoff.get("lines"):
        lines.append("- 暂无交接摘要。")
    lines.extend(
        [
            "",
            "## 待确认分组",
            "",
            "| 分组 | 数量 | 未确认 | Keys |",
            "| --- | --- | --- | --- |",
        ]
    )
    for group in readability.get("manual_confirmation_groups") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(group.get("label") or group.get("group") or "-"),
                    str(group.get("count") or 0),
                    str(group.get("unconfirmed_count") or 0),
                    ", ".join(str(item) for item in group.get("keys") or []) or "-",
                ]
            )
            + " |"
        )
    if not readability.get("manual_confirmation_groups"):
        lines.append("| - | - | - | - |")
    lines.extend(
        [
            "",
            "## 入口",
            "",
        ]
    )
    for item in index.get("entry_points") or []:
        lines.append(f"- {item.get('label') or '-'}：`{item.get('relative_path') or '-'}`")
    lines.extend(["", "## 审查步骤", ""])
    for step in index.get("review_steps") or []:
        lines.append(f"- {step}")
    lines.extend(["", "## 只读边界", ""])
    for boundary in index.get("readonly_boundaries") or []:
        lines.append(f"- {boundary}")
    lines.extend(["", "## 复跑命令", "", "| 用途 | Copy Target | Command |", "| --- | --- | --- |"])
    for item in index.get("commands") or []:
        lines.append(f"| {item.get('label') or item.get('key') or '-'} | `{item.get('copy_target_id') or '-'}` | `{item.get('command') or '-'}` |")
    if not index.get("commands"):
        lines.append("| - | - | - |")
    lines.extend(["", "## 人工确认", "", "| Key | 确认项 | Required | Harness 已确认 |", "| --- | --- | --- | --- |"])
    for item in index.get("manual_confirmation_items") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("key") or "-"),
                    str(item.get("label") or "-"),
                    "是" if item.get("required") else "否",
                    "是" if item.get("confirmed_by_harness") else "否",
                ]
            )
            + " |"
        )
    if not index.get("manual_confirmation_items"):
        lines.append("| - | - | - | - |")
    lines.extend(["", "## 文件清单", "", "| Kind | Exists | Planned | Relative Path |", "| --- | --- | --- | --- |"])
    for item in index.get("files") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("kind") or "-"),
                    "是" if item.get("exists") else "否",
                    "是" if item.get("planned") else "否",
                    str(item.get("relative_path") or "-"),
                ]
            )
            + " |"
        )
    if not index.get("files"):
        lines.append("| - | - | - | - |")
    lines.extend(["", "## 残余风险", "", f"- {index.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def workspace_snapshot_comparison_to_markdown(comparison: dict) -> str:
    lines = [
        "# Task Workspace 历史快照对比",
        "",
        f"- 版本：{comparison.get('version')}",
        f"- 只读：{'是' if comparison.get('readonly') else '否'}",
        f"- 状态：{comparison.get('status') or '-'}",
        f"- 已比较：{'是' if comparison.get('compared') else '否'}",
        f"- 上一快照：{comparison.get('previous_generated_at') or '-'}",
        f"- 当前快照：{comparison.get('current_generated_at') or '-'}",
        "",
        "## 汇总变化",
        "",
        "| 字段 | 变化 |",
        "| --- | --- |",
    ]
    for key, value in (comparison.get("summary_delta") or {}).items():
        lines.append(f"| {key} | {value} |")
    if not comparison.get("summary_delta"):
        lines.append("| - | - |")
    lines.extend(["", "## 任务变化", "", "| Task | DFHIS | 字段 |", "| --- | --- | --- |"])
    for task in comparison.get("changed_tasks") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(task.get("task_key") or "-"),
                    str(task.get("entity_id") or "-"),
                    ", ".join(task.get("changed_fields") or []) or "-",
                ]
            )
            + " |"
        )
    if not comparison.get("changed_tasks"):
        lines.append("| - | - | - |")
    lines.extend(["", "## 新增任务"])
    for task in comparison.get("added_tasks") or []:
        lines.append(f"- {task.get('task_key') or '-'} / {task.get('entity_id') or '-'}")
    if not comparison.get("added_tasks"):
        lines.append("- 暂无")
    lines.extend(["", "## 移除任务"])
    for task in comparison.get("removed_tasks") or []:
        lines.append(f"- {task.get('task_key') or '-'} / {task.get('entity_id') or '-'}")
    if not comparison.get("removed_tasks"):
        lines.append("- 暂无")
    lines.extend(["", "## 残余风险", "", f"- {comparison.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def workspace_snapshot_history_to_markdown(history: dict) -> str:
    lines = [
        "# Task Workspace 多快照索引",
        "",
        f"- 版本：{history.get('version')}",
        f"- 只读：{'是' if history.get('readonly') else '否'}",
        f"- 快照数：{history.get('snapshot_count', 0)}",
        f"- 最新快照：{history.get('latest_snapshot_id') or '-'}",
        "",
        "## 快照",
        "",
        "| Snapshot | Generated At | Tasks | Runs | Warnings | Changes | Path |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in history.get("snapshots") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(record.get("snapshot_id") or "-"),
                    str(record.get("generated_at") or "-"),
                    str(record.get("task_count", 0)),
                    str(record.get("run_count", 0)),
                    str(record.get("warning_count", 0)),
                    str(record.get("change_count", 0)),
                    str(record.get("relative_path") or "-"),
                ]
            )
            + " |"
        )
    if not history.get("snapshots"):
        lines.append("| - | - | - | - | - | - | - |")
    lines.extend(["", "## 可选对比", "", "| From | To | Run Δ | Warning Δ | Change Δ | Changed Tasks |", "| --- | --- | --- | --- | --- | --- |"])
    for item in history.get("comparisons") or []:
        delta = item.get("summary_delta") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("previous_snapshot_id") or "-"),
                    str(item.get("current_snapshot_id") or "-"),
                    str(delta.get("run_count_delta", 0)),
                    str(delta.get("warning_count_delta", 0)),
                    str(delta.get("change_count_delta", 0)),
                    str(len(item.get("changed_tasks") or [])),
                ]
            )
            + " |"
        )
    if not history.get("comparisons"):
        lines.append("| - | - | - | - | - | - |")
    lines.extend(["", "## 残余风险", "", f"- {history.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def workspace_evidence_trend_to_markdown(trend: dict) -> str:
    lines = [
        "# Task Workspace 证据状态趋势",
        "",
        f"- 版本：{trend.get('version')}",
        f"- 只读：{'是' if trend.get('readonly') else '否'}",
        f"- 快照数：{trend.get('snapshot_count', 0)}",
        "",
        "## 任务趋势",
        "",
        "| Task | DFHIS | Points | UI Evidence | Warning Min/Max/Latest | Verification | Calibration |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for task in trend.get("tasks") or []:
        summary = task.get("trend_summary") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(task.get("task_key") or "-"),
                    str(task.get("entity_id") or "-"),
                    str(summary.get("point_count", 0)),
                    ", ".join(summary.get("ui_evidence_statuses") or []) or "-",
                    f"{summary.get('warning_count_min', 0)}/{summary.get('warning_count_max', 0)}/{summary.get('warning_count_latest', 0)}",
                    ", ".join(summary.get("verification_statuses") or []) or "-",
                    ", ".join(summary.get("requirement_calibration_statuses") or []) or "-",
                ]
            )
            + " |"
        )
    if not trend.get("tasks"):
        lines.append("| - | - | - | - | - | - | - |")
    lines.extend(["", "## 时间线", "", "| Snapshot | Generated At | Tasks | Runs | Warnings | Changes |", "| --- | --- | --- | --- | --- | --- |"])
    for item in trend.get("timeline") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("snapshot_id") or "-"),
                    str(item.get("generated_at") or "-"),
                    str(item.get("task_count", 0)),
                    str(item.get("run_count", 0)),
                    str(item.get("warning_count", 0)),
                    str(item.get("change_count", 0)),
                ]
            )
            + " |"
        )
    if not trend.get("timeline"):
        lines.append("| - | - | - | - | - | - |")
    lines.extend(["", "## 残余风险", "", f"- {trend.get('residual_risk') or '-'}"])
    return "\n".join(lines)


def workspace_export_index_to_html(index: dict) -> str:
    groups = []
    for group in index.get("groups") or []:
        files = group.get("files") or []
        rows = []
        for item in files[:8]:
            link = item.get("relative_path") or item.get("path") or ""
            link_html = f'<a href="{escape_html(link)}">{escape_html(link)}</a>' if link else "-"
            rows.append(
                "<tr>"
                f"<td>{escape_html(item.get('kind') or '-')}</td>"
                f"<td>{escape_html(item.get('task_key') or '-')}</td>"
                f"<td>{'是' if item.get('exists') else '否'}</td>"
                f"<td>{link_html}</td>"
                "</tr>"
            )
        if not rows:
            rows.append('<tr><td colspan="4">暂无文件。</td></tr>')
        more = len(files) - len(rows) if files and len(files) > 8 else 0
        groups.append(
            '<article class="export-group">'
            f"<h3>{escape_html(group.get('label') or group.get('group') or '-')}</h3>"
            '<table class="mini-table"><thead><tr><th>Kind</th><th>Task</th><th>存在</th><th>路径</th></tr></thead><tbody>'
            + "".join(rows)
            + "</tbody></table>"
            + (f'<p class="readonly-note">另有 {escape_html(more)} 个文件见完整索引。</p>' if more > 0 else "")
            + "</article>"
        )
    if not groups:
        groups.append('<p class="readonly-note">暂无导出索引。</p>')
    return "\n".join(
        [
            '<section id="workspace-export-index" class="workspace-section">',
            "<h2>导出索引</h2>",
            f'<p class="readonly-note">文件数：{escape_html(index.get("file_count", 0))}，完整索引：'
            '<a href="task_workspace_export_index.json">task_workspace_export_index.json</a> / '
            '<a href="task_workspace_export_index.md">task_workspace_export_index.md</a></p>',
            '<div class="export-grid">',
            *groups,
            "</div>",
            "</section>",
        ]
    )


def workspace_state_catalog_to_html(ui_polish: dict, key: str, *, title: str, data_attr: str) -> str:
    cards = []
    for item in ui_polish.get(key) or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        cards.append(
            f'<article class="workspace-empty-state" {data_attr}="{escape_html(kind)}">'
            f'<h3>{escape_html(item.get("title") or kind or "-")}</h3>'
            f'<p>{escape_html(item.get("message") or "-")}</p>'
            "</article>"
        )
    if not cards:
        cards.append(
            f'<article class="workspace-empty-state" {data_attr}="-"><h3>{escape_html(title)}</h3><p>暂无说明。</p></article>'
        )
    return "\n".join(["<h3>" + escape_html(title) + "</h3>", '<div class="workspace-state-grid">', *cards, "</div>"])


def workspace_offline_review_to_html(review: dict, ui_polish: dict) -> str:
    files = review.get("files") or []
    rows = []
    for item in files[:12]:
        link = str(item.get("relative_path") or "")
        link_html = f'<a href="{escape_html(link)}">{escape_html(link)}</a>' if link else "-"
        rows.append(
            "<tr>"
            f"<td>{escape_html(item.get('group') or '-')}</td>"
            f"<td>{escape_html(item.get('kind') or '-')}</td>"
            f"<td>{escape_html(item.get('task_key') or '-')}</td>"
            f"<td>{workspace_status_pill('存在' if item.get('exists') else '缺失', 'file-exists' if item.get('exists') else 'missing-artifact')}</td>"
            f"<td>{link_html}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="5">暂无离线文件清单。</td></tr>')
    more = len(files) - len(rows) if files and len(files) > 12 else 0
    step_items = [f"<li>{escape_html(step)}</li>" for step in review.get("review_steps") or []]
    boundary_items = [f"<li>{escape_html(item)}</li>" for item in review.get("readonly_boundaries") or []]
    return "\n".join(
        [
            '<section id="workspace-offline-review" class="workspace-section offline-review">',
            "<h2>离线审查包</h2>",
            f'<p class="readonly-note">文件数：{escape_html(review.get("file_count", 0))}，已存在：{escape_html(review.get("existing_file_count", 0))}，缺失：{escape_html(review.get("missing_file_count", 0))}。完整说明：'
            '<a href="task_workspace_offline_review.json">task_workspace_offline_review.json</a> / '
            '<a href="task_workspace_offline_review.md">task_workspace_offline_review.md</a></p>',
            '<div class="offline-review-grid">',
            "<article>",
            "<h3>审查步骤</h3>",
            "<ol>",
            *(step_items or ["<li>暂无审查步骤。</li>"]),
            "</ol>",
            "</article>",
            "<article>",
            "<h3>只读边界</h3>",
            "<ul>",
            *(boundary_items or ["<li>保持本地只读。</li>"]),
            "</ul>",
            "</article>",
            "</div>",
            workspace_state_catalog_to_html(ui_polish, "empty_states", title="空态说明", data_attr="data-empty-kind"),
            workspace_state_catalog_to_html(ui_polish, "error_states", title="错误态说明", data_attr="data-error-kind"),
            '<div class="workspace-table-wrap offline-file-list">',
            '<table class="mini-table"><thead><tr><th>Group</th><th>Kind</th><th>Task</th><th>状态</th><th>路径</th></tr></thead><tbody>',
            *rows,
            "</tbody></table>",
            "</div>",
            (f'<p class="readonly-note">另有 {escape_html(more)} 个文件见完整离线审查包。</p>' if more > 0 else ""),
            "</section>",
        ]
    )


def workspace_snapshot_history_to_html(history: dict) -> str:
    records = history.get("snapshots") or []
    rows = []
    for record in records:
        relative_path = str(record.get("relative_path") or "")
        link_html = f'<a href="{escape_html(relative_path)}">{escape_html(relative_path)}</a>' if relative_path else "-"
        rows.append(
            "<tr>"
            f"<td><code>{escape_html(record.get('snapshot_id') or '-')}</code></td>"
            f"<td>{escape_html(record.get('generated_at') or '-')}</td>"
            f"<td>{escape_html(record.get('task_count', 0))}</td>"
            f"<td>{escape_html(record.get('run_count', 0))}</td>"
            f"<td>{escape_html(record.get('warning_count', 0))}</td>"
            f"<td>{escape_html(record.get('change_count', 0))}</td>"
            f"<td>{link_html}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="7">暂无历史快照。</td></tr>')
    default_pair_id = str(history.get("default_pair_id") or "")
    default_previous = default_pair_id.split("__", 1)[0] if "__" in default_pair_id else ""
    default_current = default_pair_id.split("__", 1)[1] if "__" in default_pair_id else ""
    return "\n".join(
        [
            '<section id="workspace-snapshot-history" class="workspace-section">',
            "<h2>多快照浏览</h2>",
            f'<p class="readonly-note">快照数：{escape_html(history.get("snapshot_count", 0))}，完整索引：'
            '<a href="task_workspace_snapshot_history.json">task_workspace_snapshot_history.json</a> / '
            '<a href="task_workspace_snapshot_history.md">task_workspace_snapshot_history.md</a></p>',
            '<div class="snapshot-picker">',
            f'<select id="snapshot-base-select" onchange="showSelectedSnapshotComparison()">{workspace_snapshot_select_options(records, default_previous, "选择基准快照")}</select>',
            f'<select id="snapshot-target-select" onchange="showSelectedSnapshotComparison()">{workspace_snapshot_select_options(records, default_current, "选择目标快照")}</select>',
            '<button type="button" onclick="showSelectedSnapshotComparison()">查看对比</button>',
            "</div>",
            '<div id="snapshot-comparison-view" class="snapshot-comparison-view readonly-note">请选择两个不同快照。</div>',
            '<table class="mini-table"><thead><tr><th>Snapshot</th><th>生成时间</th><th>任务</th><th>Run</th><th>Warning</th><th>修改</th><th>文件</th></tr></thead><tbody>',
            *rows,
            "</tbody></table>",
            "</section>",
        ]
    )


def workspace_snapshot_select_options(records: list[dict], selected_snapshot_id: str, all_label: str) -> str:
    options = [f'<option value="">{escape_html(all_label)}</option>']
    for record in records:
        snapshot_id = str(record.get("snapshot_id") or "")
        if not snapshot_id:
            continue
        selected = " selected" if snapshot_id == selected_snapshot_id else ""
        label = f"{record.get('generated_at') or snapshot_id} · W{record.get('warning_count', 0)} · R{record.get('run_count', 0)}"
        options.append(f'<option value="{escape_html(snapshot_id)}"{selected}>{escape_html(label)}</option>')
    return "\n".join(options)


def workspace_evidence_trend_to_html(trend: dict) -> str:
    rows = []
    for task in trend.get("tasks") or []:
        summary = task.get("trend_summary") or {}
        point_labels = []
        for point in (task.get("points") or [])[-6:]:
            point_labels.append(
                '<span class="trend-point">'
                f"{escape_html(point.get('generated_at') or '-')}"
                f" · UI {escape_html(point.get('ui_evidence_status') or '-')}"
                f" · W{escape_html(point.get('warning_count', 0))}"
                f" · C{escape_html(point.get('change_count', 0))}"
                "</span>"
            )
        rows.append(
            "<tr>"
            f"<td>{escape_html(task.get('task_key') or '-')}<br><small>{escape_html(task.get('entity_id') or '-')}</small></td>"
            f"<td>{escape_html(summary.get('point_count', 0))}</td>"
            f"<td>{escape_html(', '.join(summary.get('ui_evidence_statuses') or []) or '-')}</td>"
            f"<td>{escape_html(summary.get('warning_count_min', 0))}/{escape_html(summary.get('warning_count_max', 0))}/{escape_html(summary.get('warning_count_latest', 0))}</td>"
            f"<td>{escape_html(', '.join(summary.get('verification_statuses') or []) or '-')}</td>"
            f"<td>{''.join(point_labels) if point_labels else '-'}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6">暂无趋势数据。</td></tr>')
    return "\n".join(
        [
            '<section id="workspace-evidence-trend" class="workspace-section">',
            "<h2>证据状态趋势</h2>",
            f'<p class="readonly-note">基于 {escape_html(trend.get("snapshot_count", 0))} 个本地快照。完整趋势：'
            '<a href="task_workspace_evidence_trend.json">task_workspace_evidence_trend.json</a> / '
            '<a href="task_workspace_evidence_trend.md">task_workspace_evidence_trend.md</a></p>',
            '<table class="mini-table trend-table"><thead><tr><th>Task</th><th>点数</th><th>UI证据</th><th>Warning min/max/latest</th><th>验证</th><th>最近趋势点</th></tr></thead><tbody>',
            *rows,
            "</tbody></table>",
            "</section>",
        ]
    )


def workspace_snapshot_pair_script_data(history: dict) -> dict:
    return {
        str(item.get("pair_id") or ""): {
            "previous": item.get("previous_snapshot_id") or "",
            "current": item.get("current_snapshot_id") or "",
            "previousGeneratedAt": item.get("previous_generated_at") or "",
            "currentGeneratedAt": item.get("current_generated_at") or "",
            "summaryDelta": item.get("summary_delta") or {},
            "changedTasks": [
                {
                    "taskKey": task.get("task_key") or "",
                    "entityId": task.get("entity_id") or "",
                    "changedFields": task.get("changed_fields") or [],
                }
                for task in item.get("changed_tasks") or []
                if isinstance(task, dict)
            ],
            "addedCount": len(item.get("added_tasks") or []),
            "removedCount": len(item.get("removed_tasks") or []),
        }
        for item in history.get("comparisons") or []
        if isinstance(item, dict) and item.get("pair_id")
    }


def json_script_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def workspace_navigation_to_html(navigation: dict) -> str:
    links = []
    for section in navigation.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "").strip()
        label = str(section.get("label") or section_id or "").strip()
        if not section_id or not label:
            continue
        links.append(
            f'<a href="#{escape_html(section_id)}" data-nav-target="{escape_html(section_id)}">{escape_html(label)}</a>'
        )
    if not links:
        links.append('<span class="readonly-note">暂无导航</span>')
    return "\n".join(['<nav id="workspace-nav" class="workspace-nav" aria-label="Workspace sections">', *links, "</nav>"])


def workspace_snapshot_detail_to_html(snapshot_detail: dict) -> str:
    snapshots = snapshot_detail.get("snapshots") or []
    selected_id = str((snapshots[-1] or {}).get("snapshot_id") or "") if snapshots else ""
    options = [f'<option value="{escape_html(item.get("snapshot_id") or "")}"{" selected" if item.get("snapshot_id") == selected_id else ""}>{escape_html(item.get("generated_at") or item.get("snapshot_id") or "-")}</option>' for item in snapshots if isinstance(item, dict)]
    rows = []
    for item in snapshots:
        if not isinstance(item, dict):
            continue
        snapshot_id = str(item.get("snapshot_id") or "")
        summary = item.get("summary") or {}
        warning_codes = ", ".join(item.get("warning_codes") or []) or "-"
        task_rows = []
        for task in (item.get("task_summaries") or [])[:12]:
            task_rows.append(
                "<tr>"
                f"<td><code>{escape_html(task.get('task_key') or '-')}</code><br>{escape_html(task.get('entity_id') or '-')}</td>"
                f"<td>{escape_html(task.get('status') or '-')}</td>"
                f"<td>{escape_html(task.get('verification_status') or '-')}</td>"
                f"<td>{escape_html(task.get('ui_evidence_status') or '-')}</td>"
                f"<td>{escape_html(task.get('warning_count', 0))}<br><code>{escape_html(', '.join(task.get('warning_codes') or []) or '-')}</code></td>"
                f"<td>{escape_html(task.get('run_count', 0))}</td>"
                f"<td>{escape_html(task.get('change_count', 0))}</td>"
                f"<td>{escape_html(task.get('requirement_calibration_status') or '-')}</td>"
                "</tr>"
            )
        if not task_rows:
            task_rows.append('<tr><td colspan="8">暂无任务摘要。</td></tr>')
        rows.append(
            f'<article class="snapshot-detail-card" data-snapshot-detail-id="{escape_html(snapshot_id)}"{" hidden" if snapshot_id != selected_id else ""}>'
            f"<h3>{escape_html(item.get('generated_at') or snapshot_id or '快照')}</h3>"
            f'<p class="readonly-note">文件：<a href="{escape_html(item.get("relative_path") or "")}">{escape_html(item.get("relative_path") or "-")}</a></p>'
            '<div class="snapshot-delta">'
            f'<div class="delta-item"><span>任务</span><strong>{escape_html(summary.get("task_count", 0))}</strong></div>'
            f'<div class="delta-item"><span>Run</span><strong>{escape_html(summary.get("run_count", 0))}</strong></div>'
            f'<div class="delta-item"><span>Warning</span><strong>{escape_html(summary.get("warning_count", 0))}</strong></div>'
            f'<div class="delta-item"><span>修改</span><strong>{escape_html(summary.get("change_count", 0))}</strong></div>'
            "</div>"
            f'<p class="readonly-note">Warning Code：<code>{escape_html(warning_codes)}</code></p>'
            "<h4>任务摘要</h4>"
            '<table class="mini-table"><thead><tr><th>Task</th><th>状态</th><th>验证</th><th>UI证据</th><th>Warning</th><th>Run</th><th>修改</th><th>确认卡</th></tr></thead><tbody>'
            + "".join(task_rows)
            + "</tbody></table>"
            "</article>"
        )
    if not rows:
        rows.append('<p class="readonly-note">暂无快照详情。</p>')
    return "\n".join(
        [
            '<section id="workspace-snapshot-detail-panel" class="workspace-section">',
            "<h2>快照详情</h2>",
            f'<p class="readonly-note">快照详情只读取本地归档摘要。完整数据：{escape_html(snapshot_detail.get("snapshot_count", 0))} 个快照。</p>',
            f'<select id="snapshot-detail-select" onchange="showSnapshotDetail(this.value)">{"".join(options)}</select>',
            '<div class="snapshot-detail-list">',
            *rows,
            "</div>",
            "</section>",
        ]
    )


def workspace_snapshot_comparison_to_html(comparison: dict) -> str:
    delta = comparison.get("summary_delta") or {}
    delta_items = []
    for key in ["task_count_delta", "run_count_delta", "warning_count_delta", "task_count_with_warnings_delta", "sample_count_delta", "change_count_delta"]:
        delta_items.append(
            f'<div class="delta-item"><span>{escape_html(key)}</span><strong>{escape_html(delta.get(key, 0))}</strong></div>'
        )
    rows = []
    for task in comparison.get("changed_tasks") or []:
        rows.append(
            "<tr>"
            f"<td>{escape_html(task.get('task_key') or '-')}</td>"
            f"<td>{escape_html(task.get('entity_id') or '-')}</td>"
            f"<td>{escape_html(', '.join(task.get('changed_fields') or []) or '-')}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="3">暂无任务字段变化。</td></tr>')
    status_text = "已比较上一快照" if comparison.get("compared") else "暂无上一快照"
    return "\n".join(
        [
            '<section id="workspace-snapshot-comparison" class="workspace-section">',
            "<h2>历史快照对比</h2>",
            f'<p class="readonly-note">{escape_html(status_text)}。完整对比：'
            '<a href="task_workspace_snapshot_comparison.json">task_workspace_snapshot_comparison.json</a> / '
            '<a href="task_workspace_snapshot_comparison.md">task_workspace_snapshot_comparison.md</a></p>',
            '<div class="snapshot-delta">',
            *delta_items,
            "</div>",
            '<table class="mini-table"><thead><tr><th>Task</th><th>DFHIS</th><th>变化字段</th></tr></thead><tbody>',
            *rows,
            "</tbody></table>",
            "</section>",
        ]
    )


def js_string_arg(value: object) -> str:
    return escape_html(json.dumps(str(value or ""), ensure_ascii=False))


def workspace_status_pill(value: object, kind: str) -> str:
    text = str(value if value not in (None, "") else "-")
    return f'<span class="status-pill" data-status-kind="{escape_html(kind)}" data-status-value="{escape_html(text)}">{escape_html(text)}</span>'


def workspace_configuration_to_html(configuration: dict, links: dict) -> str:
    if not configuration:
        return ""
    rule_pack = configuration.get("rule_pack") or {}
    profile = configuration.get("profile") or {}
    providers = configuration.get("providers") or {}
    credentials = configuration.get("credentials") or {}
    validation = configuration.get("validation") or {}
    hard_guards = rule_pack.get("hard_guards") or {}
    credential_rows = []
    for item in credentials.get("items") or []:
        if not isinstance(item, dict):
            continue
        credential_rows.append(
            "<tr>"
            f"<td>{escape_html(item.get('key') or '-')}</td>"
            f"<td>{workspace_status_pill(item.get('status') or '-', 'credential')}</td>"
            f"<td>{escape_html(item.get('source') or '-')}</td>"
            f"<td><code>{escape_html(item.get('masked_value') or '-')}</code></td>"
            f"<td>{escape_html(', '.join(item.get('usage') or []) or '-')}</td>"
            "</tr>"
        )
    if not credential_rows:
        credential_rows.append('<tr><td colspan="5">暂无凭证引用。</td></tr>')
    issue_rows = []
    for item in validation.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issue_rows.append(
            "<tr>"
            f"<td>{workspace_status_pill(item.get('severity') or '-', 'config-validation')}</td>"
            f"<td><code>{escape_html(item.get('code') or '-')}</code></td>"
            f"<td>{escape_html(item.get('message') or '-')}</td>"
            "</tr>"
        )
    if not issue_rows:
        issue_rows.append('<tr><td colspan="3">未发现硬保护配置错误。</td></tr>')
    return "\n".join(
        [
            '<section id="workspace-configuration" class="workspace-section workspace-configuration">',
            "<h2>配置中心</h2>",
            '<p class="readonly-note">只读配置摘要：展示 Rule Pack、Profile、Provider 和 Credential Store 状态，不保存、不显示完整密钥，不执行外部写入。</p>',
            '<div class="config-grid">',
            "<article>",
            "<h3>Rule Pack</h3>",
            f"<p><strong>{escape_html(rule_pack.get('display_name') or '-')}</strong></p>",
            f"<p>ID：<code>{escape_html(rule_pack.get('rule_pack_id') or '-')}</code> / 版本：{escape_html(rule_pack.get('version') or '-')}</p>",
            f"<p>外部写入默认：{workspace_status_pill(hard_guards.get('external_writes_default') or '-', 'write-boundary')}</p>",
            f"<p>禁止打印密钥：{workspace_status_pill(hard_guards.get('no_secret_printing'), 'hard-guard')}</p>",
            "</article>",
            "<article>",
            "<h3>Profile</h3>",
            f"<p><strong>{escape_html(profile.get('display_name') or '-')}</strong></p>",
            f"<p>Key：<code>{escape_html(profile.get('key') or '-')}</code></p>",
            f"<p>需求来源：{workspace_status_pill(providers.get('active_requirement_source') or '-', 'provider')}</p>",
            f"<p>支持来源：{escape_html(', '.join(providers.get('supported_requirement_sources') or []) or '-')}</p>",
            "</article>",
            "<article>",
            "<h3>兼容边界</h3>",
            f"<p>{escape_html((configuration.get('compatibility') or {}).get('default_harness_behavior') or '-')}</p>",
            f"<p>配置只读：{workspace_status_pill((configuration.get('compatibility') or {}).get('config_is_readonly_by_default'), 'config-readonly')}</p>",
            f"<p>真实状态流转需确认：{workspace_status_pill(hard_guards.get('real_status_transition_requires_confirmation'), 'hard-guard')}</p>",
            "</article>",
            "</div>",
            "<h3>Credential Store</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>Key</th><th>状态</th><th>来源</th><th>尾号</th><th>用途</th></tr></thead>",
            "<tbody>",
            *credential_rows,
            "</tbody></table></div>",
            "<h3>Validation</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>级别</th><th>Code</th><th>说明</th></tr></thead>",
            "<tbody>",
            *issue_rows,
            "</tbody></table></div>",
            '<div class="links config-links">',
            f'<a href="{escape_html(links.get("config_summary_json") or "task_workspace_config_summary.json")}">配置摘要 JSON</a>',
            f'<a href="{escape_html(links.get("config_summary_markdown") or "task_workspace_config_summary.md")}">配置摘要 Markdown</a>',
            "</div>",
            f'<p class="note">{escape_html(configuration.get("residual_risk") or "")}</p>',
            "</section>",
        ]
    )


def workspace_configuration_preview_to_html(preview: dict, links: dict) -> str:
    if not preview:
        return ""
    provider_rows = []
    for item in preview.get("provider_templates") or []:
        if not isinstance(item, dict):
            continue
        provider_rows.append(
            "<tr>"
            f"<td><code>{escape_html(item.get('source_type') or '-')}</code></td>"
            f"<td>{escape_html(item.get('label') or '-')}</td>"
            f"<td>{workspace_status_pill(item.get('template_status') or '-', 'provider-template')}</td>"
            f"<td>{escape_html(', '.join(item.get('credential_keys') or []) or '无')}</td>"
            f"<td>{workspace_status_pill(item.get('remote_read_enabled'), 'provider-readonly')}</td>"
            f"<td>{workspace_status_pill(item.get('external_write_enabled'), 'provider-write')}</td>"
            f"<td>{escape_html(item.get('description') or '-')}</td>"
            "</tr>"
        )
    if not provider_rows:
        provider_rows.append('<tr><td colspan="7">暂无 provider 模板。</td></tr>')
    warnings = []
    for item in preview.get("warnings") or []:
        if not isinstance(item, dict):
            continue
        warnings.append(
            "<li>"
            f"{workspace_status_pill(item.get('severity') or '-', 'config-preview-warning')} "
            f"<code>{escape_html(item.get('code') or '-')}</code>：{escape_html(item.get('message') or '-')}"
            "</li>"
        )
    if not warnings:
        warnings.append("<li>暂无</li>")
    workflow_rules = preview.get("workflow_rules") or {}
    comment_template = workflow_rules.get("comment_template") or {}
    status_flow = workflow_rules.get("status_flow") or {}
    risk = workflow_rules.get("risk") or {}
    return "\n".join(
        [
            '<section id="workspace-configuration-preview" class="workspace-section workspace-configuration-preview">',
            "<h2>配置预览</h2>",
            '<p class="readonly-note">Provider 模板和规则预览均为本地草案：不会读取远端、不会保存真实 token、不会执行云效/TAPD 写入、commit、push、回滚或发布。</p>',
            '<div class="config-grid">',
            "<article>",
            "<h3>基本信息</h3>",
            f"<p>Rule Pack：<code>{escape_html(preview.get('rule_pack_id') or '-')}</code></p>",
            f"<p>Profile：<code>{escape_html(preview.get('profile_key') or '-')}</code></p>",
            f"<p>默认需求来源：{workspace_status_pill(preview.get('active_requirement_source') or '-', 'provider')}</p>",
            f"<p>外部写入：{workspace_status_pill(preview.get('external_writes_enabled'), 'write-boundary')}</p>",
            "</article>",
            "<article>",
            "<h3>规则摘要</h3>",
            f"<p>评论模板：<code>{escape_html(comment_template.get('delivery_template') or '-')}</code></p>",
            f"<p>状态真实流转：{workspace_status_pill(status_flow.get('real_transition_enabled'), 'status-flow')}</p>",
            f"<p>状态 dry-run：{workspace_status_pill(status_flow.get('dry_run_enabled'), 'status-flow')}</p>",
            f"<p>高风险自动流程阻断：{workspace_status_pill(risk.get('auto_flow_blocked_for_high_risk'), 'risk-rule')}</p>",
            "</article>",
            "<article>",
            "<h3>团队分享边界</h3>",
            f"<p>显示凭证值：{workspace_status_pill(preview.get('credential_values_exposed'), 'credential')}</p>",
            f"<p>远端连通测试：{workspace_status_pill(preview.get('remote_connection_tests_enabled'), 'provider-readonly')}</p>",
            f"<p>显式参数启用：{workspace_status_pill((preview.get('compatibility') or {}).get('requires_explicit_cli_flag'), 'config-readonly')}</p>",
            "</article>",
            "</div>",
            "<h3>Provider 模板</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>类型</th><th>名称</th><th>状态</th><th>凭证 Key</th><th>远端读取</th><th>外部写入</th><th>说明</th></tr></thead>",
            "<tbody>",
            *provider_rows,
            "</tbody></table></div>",
            "<h3>Warning</h3>",
            "<ul>",
            *warnings,
            "</ul>",
            '<div class="links config-links">',
            f'<a href="{escape_html(links.get("config_preview_json") or "task_workspace_config_preview.json")}">配置预览 JSON</a>',
            f'<a href="{escape_html(links.get("config_preview_markdown") or "task_workspace_config_preview.md")}">配置预览 Markdown</a>',
            "</div>",
            f'<p class="note">{escape_html(preview.get("residual_risk") or "")}</p>',
            "</section>",
        ]
    )


def workspace_config_share_validation_to_html(validation: dict, links: dict) -> str:
    if not validation:
        return ""
    issue_rows = []
    for item in validation.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issue_rows.append(
            "<tr>"
            f"<td>{workspace_status_pill(item.get('severity') or '-', 'share-validation')}</td>"
            f"<td><code>{escape_html(item.get('code') or '-')}</code></td>"
            f"<td><code>{escape_html(item.get('path') or '-')}</code></td>"
            f"<td>{escape_html(item.get('message') or '-')}</td>"
            "</tr>"
        )
    if not issue_rows:
        issue_rows.append('<tr><td colspan="4">未发现阻断项。</td></tr>')
    strategy_rows = []
    for item in (validation.get("local_override_strategy") or {}).get("precedence") or []:
        if not isinstance(item, dict):
            continue
        strategy_rows.append(
            "<tr>"
            f"<td>{escape_html(item.get('kind') or '-')}</td>"
            f"<td><code>{escape_html(item.get('path') or '-')}</code></td>"
            f"<td>{workspace_status_pill(item.get('status') or '-', 'override-strategy')}</td>"
            f"<td>{escape_html(item.get('note') or '-')}</td>"
            "</tr>"
        )
    if not strategy_rows:
        strategy_rows.append('<tr><td colspan="4">暂无本地覆盖策略。</td></tr>')
    return "\n".join(
        [
            '<section id="workspace-config-share-validation" class="workspace-section workspace-config-share-validation">',
            "<h2>配置分享校验</h2>",
            '<p class="readonly-note">团队分享包校验只读取本地 Rule Pack/Profile 模板；不会应用配置、不会写入 ~/.his-harness、不会保存真实 token、不会执行远端连通测试。</p>',
            '<div class="config-grid">',
            "<article>",
            "<h3>校验状态</h3>",
            f"<p>状态：{workspace_status_pill(validation.get('status') or '-', 'share-validation')}</p>",
            f"<p>会应用配置：{workspace_status_pill(validation.get('will_apply_configuration'), 'config-readonly')}</p>",
            f"<p>外部写入：{workspace_status_pill(validation.get('external_writes_enabled'), 'write-boundary')}</p>",
            "</article>",
            "<article>",
            "<h3>输入文件</h3>",
            f"<p>Rule Pack：<code>{escape_html((validation.get('input_files') or {}).get('rule_pack') or '-')}</code></p>",
            f"<p>Profile：<code>{escape_html((validation.get('input_files') or {}).get('profile_config') or '-')}</code></p>",
            "</article>",
            "<article>",
            "<h3>安全规则</h3>",
            f"<p>禁止密钥值：{workspace_status_pill((validation.get('share_package_rules') or {}).get('secret_values_forbidden'), 'hard-guard')}</p>",
            f"<p>外部写入关闭：{workspace_status_pill((validation.get('share_package_rules') or {}).get('external_writes_must_remain_off'), 'hard-guard')}</p>",
            f"<p>本地覆盖需显式参数：{workspace_status_pill((validation.get('share_package_rules') or {}).get('profile_overrides_require_explicit_cli_arg'), 'hard-guard')}</p>",
            "</article>",
            "</div>",
            "<h3>本地覆盖策略</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>类型</th><th>路径/参数</th><th>状态</th><th>说明</th></tr></thead>",
            "<tbody>",
            *strategy_rows,
            "</tbody></table></div>",
            "<h3>Issues</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>级别</th><th>Code</th><th>路径</th><th>说明</th></tr></thead>",
            "<tbody>",
            *issue_rows,
            "</tbody></table></div>",
            '<div class="links config-links">',
            f'<a href="{escape_html(links.get("config_share_validation_json") or "task_workspace_config_share_validation.json")}">分享校验 JSON</a>',
            f'<a href="{escape_html(links.get("config_share_validation_markdown") or "task_workspace_config_share_validation.md")}">分享校验 Markdown</a>',
            "</div>",
            f'<p class="note">{escape_html(validation.get("residual_risk") or "")}</p>',
            "</section>",
        ]
    )


def workspace_config_import_draft_to_html(draft: dict, links: dict) -> str:
    if not draft:
        return ""
    write_result = draft.get("write_result") or {}
    file_rows = []
    for item in draft.get("files") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or "-"
        path = item.get("path") or item.get("file_name") or "-"
        exists = Path(path).exists() if path and path != "-" else False
        file_rows.append(
            "<tr>"
            f"<td><code>{escape_html(kind)}</code></td>"
            f"<td>{escape_html(item.get('file_name') or '-')}</td>"
            f"<td><code>{escape_html(path)}</code></td>"
            f"<td>{workspace_status_pill('存在' if exists else '待生成', 'import-draft-file')}</td>"
            f"<td>{workspace_status_pill(item.get('overwrite_existing'), 'import-draft-overwrite')}</td>"
            "</tr>"
        )
    if not file_rows:
        file_rows.append('<tr><td colspan="5">暂无草案文件计划。</td></tr>')
    step_rows = []
    for index, step in enumerate(draft.get("manual_steps") or [], start=1):
        step_rows.append(f"<li>{escape_html(index)}. {escape_html(step)}</li>")
    if not step_rows:
        step_rows.append("<li>暂无人工步骤。</li>")
    command_rows = []
    for command in draft.get("copy_commands") or []:
        command_rows.append(f"<li><code>{escape_html(command)}</code></li>")
    if not command_rows:
        command_rows.append("<li>暂无命令。</li>")
    blocked_files = []
    for path in write_result.get("blocked_existing_files") or []:
        blocked_files.append(f"<li><code>{escape_html(path)}</code></li>")
    if not blocked_files:
        blocked_files.append("<li>无</li>")
    return "\n".join(
        [
            '<section id="workspace-config-import-draft" class="workspace-section workspace-config-import-draft">',
            "<h2>配置导入草案</h2>",
            '<p class="readonly-note">配置导入草案只写入用户选择目录；不会应用配置、不会写入 ~/.his-harness、不会保存真实 token、不会读取远端或测试账号。</p>',
            '<div class="config-grid">',
            "<article>",
            "<h3>导入草案状态</h3>",
            f"<p>用户选择目录：<code>{escape_html(draft.get('draft_output_dir') or '-')}</code></p>",
            f"<p>写入状态：{workspace_status_pill(write_result.get('status') or '-', 'import-draft-status')}</p>",
            f"<p>会应用配置：{workspace_status_pill(draft.get('will_apply_configuration'), 'config-readonly')}</p>",
            "</article>",
            "<article>",
            "<h3>写入边界</h3>",
            f"<p>只写用户选择目录：{workspace_status_pill(draft.get('writes_only_to_user_selected_dir'), 'config-readonly')}</p>",
            f"<p>写真实配置目录：{workspace_status_pill(draft.get('will_write_real_config_dir'), 'config-readonly')}</p>",
            f"<p>覆盖同名文件：{workspace_status_pill(draft.get('overwrite_existing_files'), 'import-draft-overwrite')}</p>",
            "</article>",
            "<article>",
            "<h3>阻断文件</h3>",
            "<ul>",
            *blocked_files,
            "</ul>",
            "</article>",
            "</div>",
            "<h3>草案文件</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>类型</th><th>文件名</th><th>路径</th><th>状态</th><th>覆盖</th></tr></thead>",
            "<tbody>",
            *file_rows,
            "</tbody></table></div>",
            "<h3>人工导入步骤</h3>",
            "<ol>",
            *step_rows,
            "</ol>",
            "<h3>可复制命令</h3>",
            "<ul>",
            *command_rows,
            "</ul>",
            '<div class="links config-links">',
            f'<a href="{escape_html(links.get("config_import_draft_json") or "task_workspace_config_import_draft.json")}">导入草案 JSON</a>',
            f'<a href="{escape_html(links.get("config_import_draft_markdown") or "task_workspace_config_import_draft.md")}">导入草案 Markdown</a>',
            "</div>",
            f'<p class="note">{escape_html(draft.get("residual_risk") or "")}</p>',
            "</section>",
        ]
    )


def workspace_config_import_review_to_html(review: dict, links: dict) -> str:
    if not review:
        return ""
    file_rows = []
    for item in review.get("files") or []:
        if not isinstance(item, dict):
            continue
        file_rows.append(
            "<tr>"
            f"<td><code>{escape_html(item.get('kind') or '-')}</code></td>"
            f"<td>{escape_html(item.get('file_name') or '-')}</td>"
            f"<td><code>{escape_html(item.get('path') or '-')}</code></td>"
            f"<td>{workspace_status_pill('存在' if item.get('exists') else '缺失', 'import-review-file')}</td>"
            "</tr>"
        )
    if not file_rows:
        file_rows.append('<tr><td colspan="4">暂无回读文件。</td></tr>')
    form_rows = []
    for section in (review.get("form_preview") or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_title = section.get("title") or section.get("key") or "-"
        for field in section.get("fields") or []:
            if not isinstance(field, dict):
                continue
            value = field.get("value")
            if isinstance(value, (dict, list)):
                value_text = json.dumps(value, ensure_ascii=False)
            else:
                value_text = str(value if value not in (None, "") else "-")
            form_rows.append(
                "<tr>"
                f"<td>{escape_html(section_title)}</td>"
                f"<td><code>{escape_html(field.get('name') or '-')}</code></td>"
                f"<td>{escape_html(field.get('label') or '-')}</td>"
                f"<td><code>{escape_html(value_text)}</code></td>"
                f"<td>{workspace_status_pill(field.get('readonly'), 'config-readonly')}</td>"
                f"<td>{workspace_status_pill(field.get('requires_user_confirmation') is True, 'manual-confirmation')}</td>"
                "</tr>"
            )
    if not form_rows:
        form_rows.append('<tr><td colspan="6">暂无只读表单预览。</td></tr>')
    issue_rows = []
    for item in review.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issue_rows.append(
            "<tr>"
            f"<td>{workspace_status_pill(item.get('severity') or '-', 'import-review-issue')}</td>"
            f"<td><code>{escape_html(item.get('code') or '-')}</code></td>"
            f"<td><code>{escape_html(item.get('path') or '-')}</code></td>"
            f"<td>{escape_html(item.get('message') or '-')}</td>"
            "</tr>"
        )
    if not issue_rows:
        issue_rows.append('<tr><td colspan="4">未发现阻断项。</td></tr>')
    risk_prompts = [f"<li>{escape_html(prompt)}</li>" for prompt in review.get("import_before_risk_prompts") or []]
    if not risk_prompts:
        risk_prompts.append("<li>暂无风险提示。</li>")
    confirmation_rows = []
    for item in review.get("manual_confirmation") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        value_text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value if value not in (None, "") else "-")
        confirmation_rows.append(
            "<tr>"
            f"<td><code>{escape_html(item.get('key') or '-')}</code></td>"
            f"<td>{escape_html(item.get('label') or '-')}</td>"
            f"<td><code>{escape_html(value_text)}</code></td>"
            f"<td>{workspace_status_pill(item.get('required'), 'manual-confirmation')}</td>"
            f"<td>{workspace_status_pill(item.get('confirmed_by_harness'), 'manual-confirmation')}</td>"
            "</tr>"
        )
    if not confirmation_rows:
        confirmation_rows.append('<tr><td colspan="5">暂无人工确认项。</td></tr>')
    return "\n".join(
        [
            '<section id="workspace-config-import-review" class="workspace-section workspace-config-import-review">',
            "<h2>配置导入回读校验</h2>",
            '<p class="readonly-note">配置导入回读校验只读取用户选择目录中的草案文件并生成只读表单预览；不会应用配置、不会写入 ~/.his-harness、不会保存真实 token、不会测试远端账号。</p>',
            '<div class="config-grid">',
            "<article>",
            "<h3>回读状态</h3>",
            f"<p>状态：{workspace_status_pill(review.get('status') or '-', 'import-review-status')}</p>",
            f"<p>草案目录：<code>{escape_html(review.get('draft_input_dir') or '-')}</code></p>",
            f"<p>会应用配置：{workspace_status_pill(review.get('will_apply_configuration'), 'config-readonly')}</p>",
            "</article>",
            "<article>",
            "<h3>只读边界</h3>",
            f"<p>写真实配置目录：{workspace_status_pill(review.get('will_write_real_config_dir'), 'config-readonly')}</p>",
            f"<p>外部写入：{workspace_status_pill(review.get('external_writes_enabled'), 'write-boundary')}</p>",
            f"<p>远端连通测试：{workspace_status_pill(review.get('remote_connection_tests_enabled'), 'provider-readonly')}</p>",
            "</article>",
            "<article>",
            "<h3>导入前风险提示</h3>",
            "<ul>",
            *risk_prompts,
            "</ul>",
            "</article>",
            "</div>",
            "<h3>草案文件回读</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>类型</th><th>文件名</th><th>路径</th><th>状态</th></tr></thead>",
            "<tbody>",
            *file_rows,
            "</tbody></table></div>",
            "<h3>只读表单预览</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>分组</th><th>字段</th><th>名称</th><th>值</th><th>只读</th><th>需人工确认</th></tr></thead>",
            "<tbody>",
            *form_rows,
            "</tbody></table></div>",
            "<h3>人工确认项</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>Key</th><th>确认项</th><th>当前值</th><th>必需</th><th>Harness 已确认</th></tr></thead>",
            "<tbody>",
            *confirmation_rows,
            "</tbody></table></div>",
            "<h3>Issues</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>级别</th><th>Code</th><th>路径</th><th>说明</th></tr></thead>",
            "<tbody>",
            *issue_rows,
            "</tbody></table></div>",
            '<div class="links config-links">',
            f'<a href="{escape_html(links.get("config_import_review_json") or "task_workspace_config_import_review.json")}">导入回读校验 JSON</a>',
            f'<a href="{escape_html(links.get("config_import_review_markdown") or "task_workspace_config_import_review.md")}">导入回读校验 Markdown</a>',
            "</div>",
            f'<p class="note">{escape_html(review.get("residual_risk") or "")}</p>',
            "</section>",
        ]
    )


def workspace_config_template_index_to_html(index: dict, links: dict) -> str:
    if not index:
        return ""
    source_rows = []
    profile_rows = []
    file_rows = []
    for source in index.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_rows.append(
            "<tr>"
            f"<td><code>{escape_html(source.get('source_key') or '-')}</code></td>"
            f"<td><code>{escape_html(source.get('draft_input_dir') or '-')}</code></td>"
            f"<td>{workspace_status_pill(source.get('review_status') or '-', 'template-index-status')}</td>"
            f"<td>{escape_html(source.get('review_issue_count') or 0)}</td>"
            f"<td>{escape_html(source.get('blocking_issue_count') or 0)}</td>"
            "</tr>"
        )
        for profile in source.get("profile_switch_preview") or []:
            if not isinstance(profile, dict):
                continue
            profile_rows.append(
                "<tr>"
                f"<td><code>{escape_html(source.get('source_key') or '-')}</code></td>"
                f"<td><code>{escape_html(profile.get('profile_key') or '-')}</code></td>"
                f"<td>{escape_html(profile.get('display_name') or '-')}</td>"
                f"<td>{workspace_status_pill(profile.get('provider_type') or '-', 'provider')}</td>"
                f"<td>{escape_html(', '.join(profile.get('credential_keys') or []) or '无')}</td>"
                f"<td>{workspace_status_pill(profile.get('project_root_state') or '-', 'template-path')}</td>"
                f"<td>{workspace_status_pill(profile.get('output_root_state') or '-', 'template-path')}</td>"
                f"<td>{workspace_status_pill(profile.get('switch_requires_manual_confirmation'), 'manual-confirmation')}</td>"
                "</tr>"
            )
        for file_item in source.get("template_files") or []:
            if not isinstance(file_item, dict):
                continue
            file_rows.append(
                "<tr>"
                f"<td><code>{escape_html(source.get('source_key') or '-')}</code></td>"
                f"<td><code>{escape_html(file_item.get('kind') or '-')}</code></td>"
                f"<td>{escape_html(file_item.get('share_role') or '-')}</td>"
                f"<td><code>{escape_html(file_item.get('path') or '-')}</code></td>"
                f"<td>{workspace_status_pill('存在' if file_item.get('exists') else '缺失', 'template-file')}</td>"
                f"<td>{workspace_status_pill(file_item.get('secret_values_allowed'), 'credential')}</td>"
                "</tr>"
            )
    if not source_rows:
        source_rows.append('<tr><td colspan="5">暂无模板来源。</td></tr>')
    if not profile_rows:
        profile_rows.append('<tr><td colspan="8">暂无 Profile 切换预览。</td></tr>')
    if not file_rows:
        file_rows.append('<tr><td colspan="6">暂无模板文件索引。</td></tr>')
    diff = index.get("diff_summary") or {}
    diff_cards = [
        ("对比来源", ", ".join(diff.get("source_pair") or []) or "-"),
        ("Provider 变化", diff.get("provider_type_changed")),
        ("评论模板变化", diff.get("comment_template_changed")),
        ("新增 Profile", ", ".join(diff.get("profile_keys_added") or []) or "无"),
        ("移除 Profile", ", ".join(diff.get("profile_keys_removed") or []) or "无"),
        ("Credential Key 新增", ", ".join(diff.get("credential_keys_added") or []) or "无"),
        ("Credential Key 移除", ", ".join(diff.get("credential_keys_removed") or []) or "无"),
        ("硬保护变化", json.dumps(diff.get("hard_guard_changes") or {}, ensure_ascii=False)),
        ("Git 权限变化", json.dumps(diff.get("git_permission_changes") or {}, ensure_ascii=False)),
        ("状态流转变化", json.dumps(diff.get("status_flow_changes") or {}, ensure_ascii=False)),
        ("路径状态变化", json.dumps(diff.get("path_state_changes") or {}, ensure_ascii=False)),
        ("变化计数", diff.get("change_count")),
    ]
    diff_items = [
        "<li>"
        f"<strong>{escape_html(label)}</strong>：<code>{escape_html(value)}</code>"
        "</li>"
        for label, value in diff_cards
    ]
    return "\n".join(
        [
            '<section id="workspace-config-template-index" class="workspace-section workspace-config-template-index">',
            "<h2>配置模板索引</h2>",
            '<p class="readonly-note">配置模板索引只读取本地草案目录，展示多 Profile 切换预览、配置差异对比和团队模板文件索引；不会应用配置、不会写入 ~/.his-harness、不会保存真实 token、不会测试远端账号。</p>',
            '<div class="config-grid">',
            "<article>",
            "<h3>索引状态</h3>",
            f"<p>状态：{workspace_status_pill(index.get('status') or '-', 'template-index-status')}</p>",
            f"<p>来源数：{escape_html(index.get('source_count') or 0)}</p>",
            f"<p>会应用配置：{workspace_status_pill(index.get('will_apply_configuration'), 'config-readonly')}</p>",
            "</article>",
            "<article>",
            "<h3>只读边界</h3>",
            f"<p>写真实配置目录：{workspace_status_pill(index.get('will_write_real_config_dir'), 'config-readonly')}</p>",
            f"<p>外部写入：{workspace_status_pill(index.get('external_writes_enabled'), 'write-boundary')}</p>",
            f"<p>远端连通测试：{workspace_status_pill(index.get('remote_connection_tests_enabled'), 'provider-readonly')}</p>",
            "</article>",
            "<article>",
            "<h3>配置差异对比</h3>",
            "<ul>",
            *diff_items,
            "</ul>",
            "</article>",
            "</div>",
            "<h3>模板来源</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>来源</th><th>目录</th><th>回读状态</th><th>Issue</th><th>阻断</th></tr></thead>",
            "<tbody>",
            *source_rows,
            "</tbody></table></div>",
            "<h3>多 Profile 切换预览</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>来源</th><th>Profile</th><th>名称</th><th>Provider</th><th>Credential Keys</th><th>项目路径</th><th>输出路径</th><th>需确认</th></tr></thead>",
            "<tbody>",
            *profile_rows,
            "</tbody></table></div>",
            "<h3>团队模板索引</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>来源</th><th>类型</th><th>角色</th><th>路径</th><th>状态</th><th>允许密钥值</th></tr></thead>",
            "<tbody>",
            *file_rows,
            "</tbody></table></div>",
            '<div class="links config-links">',
            f'<a href="{escape_html(links.get("config_template_index_json") or "task_workspace_config_template_index.json")}">配置模板索引 JSON</a>',
            f'<a href="{escape_html(links.get("config_template_index_markdown") or "task_workspace_config_template_index.md")}">配置模板索引 Markdown</a>',
            "</div>",
            f'<p class="note">{escape_html(index.get("residual_risk") or "")}</p>',
            "</section>",
        ]
    )


def workspace_config_wizard_to_html(wizard: dict, links: dict) -> str:
    if not wizard:
        return ""
    readability = wizard.get("ui_readability") or {}
    filter_options = readability.get("step_filter_options") or {}
    step_summary = readability.get("step_summary") or {}
    status_options = workspace_select_options(filter_options.get("statuses") or [], "全部状态")
    step_rows = []
    for step in wizard.get("steps") or []:
        if not isinstance(step, dict):
            continue
        confirmations = "<br>".join(escape_html(item) for item in step.get("confirmations") or []) or "-"
        artifacts = "<br>".join(f"<code>{escape_html(item)}</code>" for item in step.get("artifacts") or []) or "-"
        step_rows.append(
            '<tr class="wizard-step-row"'
            f' data-wizard-search="{escape_html(step.get("search_text") or "")}"'
            f' data-wizard-status="{escape_html(step.get("status") or "")}"'
            f' data-wizard-blocking="{escape_html("blocking" if step.get("blocking") else "non_blocking")}"'
            ">"
            f"<td><code>{escape_html(step.get('id') or '-')}</code><br>{escape_html(step.get('title') or '-')}</td>"
            f"<td>{workspace_status_pill(step.get('status') or '-', 'wizard-step-status')}</td>"
            f"<td>{workspace_status_pill(step.get('blocking'), 'wizard-blocking')}</td>"
            f"<td>{escape_html(step.get('description') or '-')}<br><small>{escape_html(step.get('next_action') or '-')}</small></td>"
            f"<td>{confirmations}</td>"
            f"<td>{artifacts}</td>"
            "</tr>"
        )
    if not step_rows:
        step_rows.append('<tr><td colspan="6">暂无配置向导步骤。</td></tr>')
    command_rows = []
    for item in wizard.get("copy_commands") or []:
        if not isinstance(item, dict):
            continue
        copy_target_id = item.get("copy_target_id") or f"wizard-command-{len(command_rows) + 1}"
        command_rows.append(
            "<tr>"
            f"<td>{escape_html(item.get('label') or item.get('key') or '-')}</td>"
            f'<td><button type="button" class="copy-command-button" data-copy-command="{escape_html(item.get("command") or "")}" onclick="copyWizardCommand(this)">复制</button> '
            f'<code id="{escape_html(copy_target_id)}">{escape_html(item.get("command") or "-")}</code></td>'
            "</tr>"
        )
    if not command_rows:
        command_rows.append('<tr><td colspan="2">暂无复制命令。</td></tr>')
    checklist_rows = []
    for item in wizard.get("manual_checklist") or []:
        if not isinstance(item, dict):
            continue
        checklist_rows.append(
            "<tr>"
            f"<td><code>{escape_html(item.get('key') or '-')}</code></td>"
            f"<td>{escape_html(item.get('label') or '-')}</td>"
            f"<td><code>{escape_html(item.get('value'))}</code></td>"
            f"<td>{workspace_status_pill(item.get('confirmed_by_harness'), 'manual-confirmation')}</td>"
            "</tr>"
        )
    if not checklist_rows:
        checklist_rows.append('<tr><td colspan="4">暂无人工确认清单。</td></tr>')
    risk_items = [f"<li>{escape_html(item)}</li>" for item in wizard.get("risk_prompts") or []]
    if not risk_items:
        risk_items.append("<li>暂无额外风险提示。</li>")
    return "\n".join(
        [
            '<section id="workspace-config-wizard" class="workspace-section workspace-config-wizard">',
            "<h2>配置向导</h2>",
            '<p class="readonly-note">配置向导把选择来源、Provider 模板、分享校验、生成草案、回读校验和对比模板串成一个人工检查入口；不会应用配置、不会写入 ~/.his-harness、不会保存真实 token、不会测试远端账号。</p>',
            '<div class="config-grid">',
            "<article>",
            "<h3>向导状态</h3>",
            f"<p>状态：{workspace_status_pill(wizard.get('status') or '-', 'wizard-status')}</p>",
            f"<p>草案目录：<code>{escape_html(wizard.get('draft_input_dir') or '-')}</code></p>",
            f"<p>对比目录：<code>{escape_html(wizard.get('compare_draft_input_dir') or '-')}</code></p>",
            "</article>",
            "<article>",
            "<h3>只读边界</h3>",
            f"<p>会应用配置：{workspace_status_pill(wizard.get('will_apply_configuration'), 'config-readonly')}</p>",
            f"<p>写真实配置目录：{workspace_status_pill(wizard.get('will_write_real_config_dir'), 'config-readonly')}</p>",
            f"<p>远端连通测试：{workspace_status_pill(wizard.get('remote_connection_tests_enabled'), 'provider-readonly')}</p>",
            "</article>",
            "<article>",
            "<h3>阻断步骤</h3>",
            f"<p>阻断数：{escape_html(len(wizard.get('blocking_steps') or []))}</p>",
            f"<p>密钥值暴露：{workspace_status_pill(wizard.get('credential_values_exposed'), 'credential')}</p>",
            "</article>",
            "<article>",
            "<h3>阻断摘要</h3>",
            f"<p>总步骤：{escape_html(step_summary.get('total_step_count') or len(wizard.get('steps') or []))}</p>",
            f"<p>需人工确认：{escape_html(step_summary.get('manual_required_step_count') or 0)}</p>",
            f"<p>命令复制：{escape_html(step_summary.get('command_count') or len(wizard.get('copy_commands') or []))}</p>",
            "</article>",
            "</div>",
            "<h3>向导步骤</h3>",
            '<div class="wizard-controls">',
            '<input id="wizard-step-search" type="search" placeholder="搜索步骤、确认点、产物、下一步" oninput="applyWizardFilters()">',
            f'<select id="wizard-status-filter" onchange="applyWizardFilters()">{status_options}</select>',
            '<select id="wizard-blocking-filter" onchange="applyWizardFilters()"><option value="">全部阻断</option><option value="blocking">只看阻断</option><option value="non_blocking">只看非阻断</option></select>',
            '<button type="button" onclick="resetWizardFilters()">重置向导筛选</button>',
            "</div>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>步骤</th><th>状态</th><th>阻断</th><th>说明</th><th>确认点</th><th>产物</th></tr></thead>",
            "<tbody>",
            *step_rows,
            "</tbody></table></div>",
            '<div id="wizard-empty-state" class="workspace-empty-state" hidden><h3>暂无匹配步骤</h3><p>当前筛选条件下没有配置向导步骤。</p></div>',
            "<h3>复制命令</h3>",
            '<p class="readonly-note">命令复制只把文本放入剪贴板，不会执行命令；浏览器不支持自动复制时，请手动选择命令文本。</p>',
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>用途</th><th>命令</th></tr></thead>",
            "<tbody>",
            *command_rows,
            "</tbody></table></div>",
            "<h3>人工确认清单</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>Key</th><th>确认项</th><th>值</th><th>Harness 已确认</th></tr></thead>",
            "<tbody>",
            *checklist_rows,
            "</tbody></table></div>",
            "<h3>风险提示</h3>",
            "<ul>",
            *risk_items,
            "</ul>",
            '<div class="links config-links">',
            f'<a href="{escape_html(links.get("config_wizard_json") or "task_workspace_config_wizard.json")}">配置向导 JSON</a>',
            f'<a href="{escape_html(links.get("config_wizard_markdown") or "task_workspace_config_wizard.md")}">配置向导 Markdown</a>',
            "</div>",
            f'<p class="note">{escape_html(wizard.get("residual_risk") or "")}</p>',
            "</section>",
        ]
    )


def workspace_config_review_package_to_html(index: dict, links: dict) -> str:
    if not index:
        return ""
    readability = index.get("ui_readability") or {}
    handoff = readability.get("handoff_summary") or {}
    file_rows = []
    for item in index.get("files") or []:
        if not isinstance(item, dict):
            continue
        relative_path = item.get("relative_path") or ""
        file_link = f'<a href="{escape_html(relative_path)}">{escape_html(relative_path)}</a>' if relative_path else "-"
        status = "present" if item.get("exists") else "missing"
        search_text = " ".join([str(item.get("kind") or ""), str(relative_path)])
        file_rows.append(
            '<tr class="review-package-file-row"'
            f' data-review-package-search="{escape_html(search_text)}"'
            f' data-review-package-file-status="{escape_html(status)}"'
            ">"
            f"<td><code>{escape_html(item.get('kind') or '-')}</code></td>"
            f"<td>{workspace_status_pill(item.get('exists'), 'review-file-exists')}</td>"
            f"<td>{workspace_status_pill(item.get('planned'), 'review-file-planned')}</td>"
            f"<td>{file_link}</td>"
            "</tr>"
        )
    if not file_rows:
        file_rows.append('<tr><td colspan="4">暂无配置审查包文件。</td></tr>')
    command_rows = []
    for item in index.get("commands") or []:
        if not isinstance(item, dict):
            continue
        command_rows.append(
            "<tr>"
            f"<td>{escape_html(item.get('label') or item.get('key') or '-')}</td>"
            f"<td><code>{escape_html(item.get('copy_target_id') or '-')}</code></td>"
            f"<td><code>{escape_html(item.get('command') or '-')}</code></td>"
            "</tr>"
        )
    if not command_rows:
        command_rows.append('<tr><td colspan="3">暂无复跑命令。</td></tr>')
    manual_rows = []
    for item in index.get("manual_confirmation_items") or []:
        if not isinstance(item, dict):
            continue
        manual_rows.append(
            "<tr>"
            f"<td><code>{escape_html(item.get('key') or '-')}</code></td>"
            f"<td>{escape_html(item.get('label') or '-')}</td>"
            f"<td>{workspace_status_pill(item.get('required'), 'review-manual-required')}</td>"
            f"<td>{workspace_status_pill(item.get('confirmed_by_harness'), 'review-manual-confirmed')}</td>"
            "</tr>"
        )
    if not manual_rows:
        manual_rows.append('<tr><td colspan="4">暂无人工确认项。</td></tr>')
    group_rows = []
    for group in readability.get("manual_confirmation_groups") or []:
        if not isinstance(group, dict):
            continue
        group_rows.append(
            "<tr>"
            f"<td>{escape_html(group.get('label') or group.get('group') or '-')}</td>"
            f"<td>{escape_html(group.get('count') or 0)}</td>"
            f"<td>{escape_html(group.get('unconfirmed_count') or 0)}</td>"
            f"<td><code>{escape_html(', '.join(str(item) for item in group.get('keys') or []) or '-')}</code></td>"
            "</tr>"
        )
    if not group_rows:
        group_rows.append('<tr><td colspan="4">暂无待确认分组。</td></tr>')
    handoff_lines = [f"<li>{escape_html(item)}</li>" for item in handoff.get("lines") or []] or ["<li>暂无交接摘要。</li>"]
    review_steps = [f"<li>{escape_html(item)}</li>" for item in index.get("review_steps") or []] or ["<li>暂无审查步骤。</li>"]
    boundaries = [f"<li>{escape_html(item)}</li>" for item in index.get("readonly_boundaries") or []] or ["<li>不会应用配置。</li>"]
    return "\n".join(
        [
            '<section id="workspace-config-review-package" class="workspace-section workspace-config-review-package">',
            "<h2>配置审查包</h2>",
            '<p class="readonly-note">配置审查包索引只汇总本地配置产物、复跑命令和人工确认项；不会应用配置、不会写入 ~/.his-harness、不会保存真实 token、不会测试远端账号。</p>',
            '<div class="config-grid">',
            "<article>",
            "<h3>审查包状态</h3>",
            f"<p>版本：<code>{escape_html(index.get('version') or '-')}</code></p>",
            f"<p>文件数：{escape_html(index.get('file_count') or 0)}</p>",
            f"<p>缺失：{escape_html(index.get('missing_file_count') or 0)}</p>",
            "</article>",
            "<article>",
            "<h3>复跑命令</h3>",
            f"<p>命令数：{escape_html(index.get('command_count') or 0)}</p>",
            f"<p>会执行命令：{workspace_status_pill(False, 'review-command-execute')}</p>",
            "</article>",
            "<article>",
            "<h3>人工确认</h3>",
            f"<p>确认项：{escape_html(index.get('manual_confirmation_count') or 0)}</p>",
            f"<p>会应用配置：{workspace_status_pill(index.get('will_apply_configuration'), 'config-readonly')}</p>",
            "</article>",
            "<article>",
            "<h3>只读边界</h3>",
            f"<p>写真实配置：{workspace_status_pill(index.get('will_write_real_config_dir'), 'config-readonly')}</p>",
            f"<p>外部写入：{workspace_status_pill(index.get('external_writes_enabled'), 'write-boundary')}</p>",
            "</article>",
            "</div>",
            "<h3>交接摘要</h3>",
            "<ul>",
            *handoff_lines,
            "</ul>",
            "<h3>待确认分组</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>分组</th><th>数量</th><th>未确认</th><th>Keys</th></tr></thead>",
            "<tbody>",
            *group_rows,
            "</tbody></table></div>",
            "<h3>审查步骤</h3>",
            "<ul>",
            *review_steps,
            "</ul>",
            "<h3>只读边界</h3>",
            "<ul>",
            *boundaries,
            "</ul>",
            "<h3>复跑命令</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>用途</th><th>Copy Target</th><th>Command</th></tr></thead>",
            "<tbody>",
            *command_rows,
            "</tbody></table></div>",
            "<h3>人工确认项</h3>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>Key</th><th>确认项</th><th>Required</th><th>Harness 已确认</th></tr></thead>",
            "<tbody>",
            *manual_rows,
            "</tbody></table></div>",
            "<h3>配置产物清单</h3>",
            '<div class="wizard-controls">',
            '<input id="review-package-file-search" type="search" placeholder="搜索文件类型或路径" oninput="applyReviewPackageFilters()">',
            '<select id="review-package-file-status-filter" onchange="applyReviewPackageFilters()"><option value="">全部文件</option><option value="present">只看已存在</option><option value="missing">只看缺失</option></select>',
            '<button type="button" onclick="resetReviewPackageFilters()">重置审查包筛选</button>',
            "</div>",
            '<div class="workspace-table-wrap config-table-wrap">',
            '<table class="mini-table">',
            "<thead><tr><th>Kind</th><th>Exists</th><th>Planned</th><th>Relative Path</th></tr></thead>",
            "<tbody>",
            *file_rows,
            "</tbody></table></div>",
            '<div id="review-package-empty-state" class="workspace-empty-state" hidden><h3>暂无匹配文件</h3><p>当前筛选条件下没有配置审查包文件。</p></div>',
            '<div class="links config-links">',
            f'<a href="{escape_html(links.get("config_review_package_json") or "task_workspace_config_review_package.json")}">配置审查包 JSON</a>',
            f'<a href="{escape_html(links.get("config_review_package_markdown") or "task_workspace_config_review_package.md")}">配置审查包 Markdown</a>',
            "</div>",
            f'<p class="note">{escape_html(index.get("residual_risk") or "")}</p>',
            "</section>",
        ]
    )


def task_workspace_to_html(workspace: dict) -> str:
    summary = workspace.get("summary") or {}
    sample_set = workspace.get("sample_set") or {}
    links = workspace.get("links") or {}
    warning_summary = workspace.get("warning_summary") or {}
    filter_options = workspace.get("filter_options") or {}
    export_index = workspace.get("export_index") or {}
    snapshot_comparison = workspace.get("snapshot_comparison") or {}
    snapshot_history = workspace.get("snapshot_history") or {}
    snapshot_detail = workspace.get("snapshot_detail") or {}
    evidence_trend = workspace.get("evidence_trend") or {}
    ui_polish = workspace.get("ui_polish") or {}
    offline_review = workspace.get("offline_review") or {}
    configuration = workspace.get("configuration") or {}
    configuration_preview = workspace.get("configuration_preview") or {}
    config_share_validation = workspace.get("config_share_validation") or {}
    config_import_draft = workspace.get("config_import_draft") or {}
    config_import_review = workspace.get("config_import_review") or {}
    config_template_index = workspace.get("config_template_index") or {}
    config_wizard = workspace.get("config_wizard") or {}
    config_review_package = workspace.get("config_review_package_index") or {}
    snapshot_pair_data_json = json_script_value(workspace_snapshot_pair_script_data(snapshot_history))
    warning_code_summary_rows = []
    for item in warning_summary.get("codes") or []:
        if not isinstance(item, dict):
            continue
        warning_code_summary_rows.append(
            '<span class="warning-chip">'
            f"<code>{escape_html(item.get('code') or '-')}</code>"
            f"<strong>{escape_html(item.get('count') or 0)}</strong>"
            "</span>"
        )
    if not warning_code_summary_rows:
        warning_code_summary_rows.append('<span class="warning-empty">暂无 warning</span>')
    warning_options = workspace_select_options(filter_options.get("warning_codes") or [], "全部 warning")
    entity_options = workspace_select_options(filter_options.get("entity_ids") or [], "全部 DFHIS")
    verification_options = workspace_select_options(filter_options.get("verification_statuses") or [], "全部验证")
    ui_evidence_options = workspace_select_options(filter_options.get("ui_evidence_statuses") or [], "全部 UI证据")
    calibration_options = workspace_select_options(filter_options.get("requirement_calibration_statuses") or [], "全部确认卡")
    requirement_evidence_options = workspace_select_options(filter_options.get("requirement_evidence_statuses") or [], "全部需求来源")
    detail_sections = [workspace_task_detail_to_html(detail) for detail in workspace.get("task_details") or []]
    if not detail_sections:
        detail_sections.append('<p class="readonly-note">暂无任务详情。</p>')
    rows = []
    for entry in workspace.get("entries") or []:
        task_label = entry.get("task_key") or entry.get("task_id") or "-"
        task_key_for_detail = str(entry.get("task_key") or entry.get("task_id") or "")
        workbench_markdown = entry.get("workbench_markdown") or ""
        workbench_json = entry.get("workbench_json") or ""
        rerun_precommit = entry.get("rerun_precommit") or ""
        warning_codes = ", ".join(entry.get("warning_codes") or []) or "-"
        change_history = entry.get("change_history") or {}
        change_count = entry.get("change_count") or change_history.get("change_count") or 0
        change_links = []
        if change_history.get("markdown_link"):
            change_links.append(f'<a href="{escape_html(change_history.get("markdown_link"))}">MD</a>')
        if change_history.get("json_link"):
            change_links.append(f'<a href="{escape_html(change_history.get("json_link"))}">JSON</a>')
        change_text = [
            f"修改 {change_count} 次",
            f"回滚 {format_rollback_mode(change_history.get('rollback_mode') or 'dry_run_only')}",
        ]
        if change_history.get("latest_change_id"):
            change_text.append(f"最新 {change_history.get('latest_change_id')}")
        calibration = entry.get("requirement_calibration") or {}
        calibration_status = calibration.get("status") or "missing"
        calibration_text = ", ".join(calibration.get("parameter_names") or []) or calibration_status
        calibration_markdown = calibration.get("markdown_link") or ""
        calibration_json = calibration.get("json_link") or ""
        calibration_links = []
        if calibration_markdown:
            calibration_links.append(f'<a href="{escape_html(calibration_markdown)}">MD</a>')
        if calibration_json:
            calibration_links.append(f'<a href="{escape_html(calibration_json)}">JSON</a>')
        requirement_evidence = entry.get("requirement_evidence") or {}
        requirement_evidence_status = requirement_evidence.get("status") or "missing"
        requirement_evidence_text = requirement_evidence.get("source_type") or requirement_evidence_status
        requirement_evidence_links = []
        if requirement_evidence.get("markdown_link"):
            requirement_evidence_links.append(f'<a href="{escape_html(requirement_evidence.get("markdown_link"))}">MD</a>')
        if requirement_evidence.get("json_link"):
            requirement_evidence_links.append(f'<a href="{escape_html(requirement_evidence.get("json_link"))}">JSON</a>')
        requirement_evidence_detail = " / ".join(
            item
            for item in [
                requirement_evidence.get("external_id") or "",
                requirement_evidence.get("requirement_status") or "",
                requirement_evidence.get("assignee") or "",
            ]
            if item
        )
        filter_data = entry.get("filter_data") or build_workspace_entry_filter_data(entry)
        search_text = entry.get("search_text") or build_workspace_entry_search_text(entry)
        row_warning_codes = "|".join(filter_data.get("warning_codes") or [])
        rows.append(
            '<tr class="workspace-row"'
            f' data-search="{escape_html(search_text)}"'
            f' data-warning-codes="{escape_html(row_warning_codes)}"'
            f' data-entity-id="{escape_html(filter_data.get("entity_id") or "")}"'
            f' data-verification-status="{escape_html(filter_data.get("verification_status") or "")}"'
            f' data-ui-evidence-status="{escape_html(filter_data.get("ui_evidence_status") or "")}"'
            f' data-requirement-calibration-status="{escape_html(filter_data.get("requirement_calibration_status") or "")}"'
            f' data-requirement-evidence-status="{escape_html(filter_data.get("requirement_evidence_status") or "")}"'
            f' data-task-key="{escape_html(task_key_for_detail)}"'
            ">"
            f'<td><button type="button" class="detail-link" onclick="showTaskDetail({js_string_arg(task_key_for_detail)})">{escape_html(task_label)}</button><br><a href="{escape_html(workbench_markdown)}">MD</a></td>'
            f"<td>{escape_html(entry.get('entity_id') or '-')}</td>"
            f"<td>{escape_html(entry.get('entity_title') or '-')}</td>"
            f"<td>{workspace_status_pill(entry.get('status') or '-', 'task-status')}</td>"
            f"<td>{workspace_status_pill(entry.get('verification_status') or '-', 'verification')}</td>"
            f"<td>{workspace_status_pill(entry.get('ui_evidence_status') or '-', 'ui-evidence')}</td>"
            f"<td>{workspace_status_pill(entry.get('warning_count') or 0, 'warning-count')}<br><code>{escape_html(warning_codes)}</code></td>"
            f"<td>{escape_html(entry.get('latest_output_dir') or '-')}</td>"
            f"<td>{'<br>'.join(escape_html(item) for item in change_text)}<br>{' / '.join(change_links) if change_links else '-'}</td>"
            f"<td>{escape_html(calibration_status)}<br>{' / '.join(calibration_links) if calibration_links else '-'}<br><code>{escape_html(calibration_text)}</code></td>"
            f"<td>{escape_html(requirement_evidence_status)}<br>{' / '.join(requirement_evidence_links) if requirement_evidence_links else '-'}<br><code>{escape_html(requirement_evidence_text)}</code><br><small>{escape_html(requirement_evidence_detail or '-')}</small></td>"
            f'<td><a href="{escape_html(workbench_json)}">JSON</a> / <a href="{escape_html(workbench_markdown)}">MD</a></td>'
            f"<td><code>{escape_html(rerun_precommit or '-')}</code></td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="13"><div class="workspace-empty-state" data-empty-kind="no-tasks"><h3>暂无任务</h3><p>当前筛选条件下没有任务。请调整筛选，或先登记已有 run。</p></div></td></tr>')
    dashboard_html = links.get("dashboard_html") or "task_dashboard.html"
    dashboard_json = links.get("dashboard_json") or "task_dashboard.json"
    sample_set_json = links.get("sample_set_json") or "task_sample_set.json"
    sample_set_markdown = links.get("sample_set_markdown") or "task_sample_set.md"
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>HIS Harness Task Workspace</title>",
            "<style>",
            "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;color:#1f2933;background:#f7f9fb}",
            "h1{font-size:22px;margin:0 0 16px}",
            "a{color:#1f6feb;text-decoration:none}",
            "a:hover{text-decoration:underline}",
            ".workspace-nav{position:sticky;top:0;z-index:2;display:flex;gap:8px;flex-wrap:wrap;background:#f7f9fb;border-bottom:1px solid #d8e0e8;margin:-24px -24px 16px;padding:12px 24px}",
            ".workspace-nav a{background:#fff;border:1px solid #d8e0e8;border-radius:6px;padding:7px 10px;font-size:13px}",
            ".summary{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}",
            ".metric{background:#fff;border:1px solid #d8e0e8;border-radius:6px;padding:10px 12px;min-width:120px}",
            ".metric strong{display:block;font-size:20px}",
            ".warning-summary{display:flex;gap:14px;align-items:stretch;background:#fff;border:1px solid #d8e0e8;border-radius:6px;margin:0 0 16px;padding:12px}",
            ".warning-total{min-width:180px;border-right:1px solid #e6edf3;padding-right:14px}",
            ".warning-total span,.warning-total small{display:block;color:#52616f;font-size:13px}",
            ".warning-total strong{display:block;font-size:24px;color:#b42318}",
            ".warning-codes{display:flex;gap:8px;flex-wrap:wrap;align-items:center}",
            ".warning-chip{display:inline-flex;gap:8px;align-items:center;border:1px solid #f3c9a6;background:#fff7ed;border-radius:6px;padding:6px 8px;font-size:12px}",
            ".warning-chip strong{color:#b42318}",
            ".warning-empty{color:#52616f;font-size:13px}",
            ".links{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 16px}",
            ".links a{background:#fff;border:1px solid #d8e0e8;border-radius:6px;padding:8px 10px;font-size:13px}",
            ".workspace-controls{display:grid;grid-template-columns:minmax(240px,1.5fr) repeat(6,minmax(120px,1fr)) auto;gap:8px;margin:0 0 12px}",
            ".workspace-controls input,.workspace-controls select,.workspace-controls button{height:34px;border:1px solid #c9d4df;border-radius:6px;background:#fff;padding:0 10px;font-size:13px}",
            ".workspace-controls button{cursor:pointer;background:#edf3f8}",
            ".wizard-controls{display:grid;grid-template-columns:minmax(220px,1.4fr) minmax(130px,.7fr) minmax(140px,.7fr) auto;gap:8px;margin:8px 0 12px}",
            ".wizard-controls input,.wizard-controls select,.wizard-controls button{height:34px;border:1px solid #c9d4df;border-radius:6px;background:#fff;padding:0 10px;font-size:13px}",
            ".wizard-controls button,.copy-command-button{cursor:pointer;background:#edf3f8}",
            ".copy-command-button{height:28px;border:1px solid #c9d4df;border-radius:6px;margin:0 6px 4px 0;padding:0 8px;font-size:12px}",
            ".detail-link{border:0;background:transparent;color:#1f6feb;padding:0;font:inherit;cursor:pointer;text-align:left}",
            ".detail-link:hover{text-decoration:underline}",
            ".workspace-row.is-selected{outline:2px solid #88b7e8;outline-offset:-2px;background:#f7fbff}",
            ".workspace-table-wrap{overflow:auto;border:1px solid #d8e0e8;border-radius:6px;background:#fff}",
            ".workspace-table-wrap table{border:0}",
            ".workspace-table{min-width:1180px}",
            "table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d8e0e8}",
            "th,td{padding:8px 10px;border-bottom:1px solid #e6edf3;text-align:left;font-size:13px;vertical-align:top}",
            "th{background:#edf3f8;font-weight:600}",
            "code{white-space:pre-wrap;word-break:break-all}",
            ".status-pill{display:inline-flex;align-items:center;min-height:22px;border:1px solid #c9d4df;border-radius:999px;background:#f8fafc;color:#243447;padding:1px 8px;font-size:12px;white-space:nowrap}",
            ".status-pill[data-status-value='passed'],.status-pill[data-status-value='success'],.status-pill[data-status-value='存在']{border-color:#b6d7c6;background:#effaf4;color:#17633a}",
            ".status-pill[data-status-value='missing'],.status-pill[data-status-value='缺失']{border-color:#f3c9a6;background:#fff7ed;color:#9a3412}",
            ".status-pill[data-status-kind='warning-count'][data-status-value='0']{border-color:#d8e0e8;background:#f8fafc;color:#52616f}",
            ".detail-shell{margin-top:18px;background:#fff;border:1px solid #d8e0e8;border-radius:6px;padding:14px}",
            ".detail-shell>h2{font-size:18px;margin:0 0 12px}",
            ".detail-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;border-bottom:1px solid #e6edf3;padding-bottom:12px;margin-bottom:12px}",
            ".detail-header h2{font-size:18px;margin:0 0 4px}",
            ".detail-header p{margin:0;color:#52616f;font-size:13px}",
            ".readonly-badge{border:1px solid #b6d7c6;background:#effaf4;color:#17633a;border-radius:6px;padding:4px 8px;font-size:12px}",
            ".detail-tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}",
            ".detail-tab-button{height:32px;border:1px solid #c9d4df;border-radius:6px;background:#fff;padding:0 10px;cursor:pointer;font-size:13px}",
            ".detail-tab-button.active{background:#1f6feb;color:#fff;border-color:#1f6feb}",
            ".detail-tab-panel{border-top:1px solid #e6edf3;padding-top:12px}",
            ".detail-tab-panel h3{font-size:14px;margin:12px 0 8px}",
            ".detail-kv{display:grid;grid-template-columns:140px minmax(0,1fr);gap:8px 12px;margin:0}",
            ".detail-kv dt{color:#52616f;font-size:13px}",
            ".detail-kv dd{margin:0;font-size:13px;word-break:break-word}",
            ".detail-links{font-size:13px;color:#52616f}",
            ".detail-table{margin-top:6px}",
            ".command-box,.preview-box{background:#f6f8fa;border:1px solid #d8e0e8;border-radius:6px;padding:10px;overflow:auto;font-size:12px}",
            ".preview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}",
            ".preview-item{border:1px solid #d8e0e8;border-radius:6px;padding:10px;background:#fbfcfe}",
            ".preview-item summary{cursor:pointer;font-weight:600;font-size:13px}",
            ".preview-item h3{margin-top:0}",
            ".preview-item p{font-size:12px;color:#52616f;margin:6px 0}",
            ".evidence-preview-summary{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 10px}",
            ".evidence-preview-summary span{border:1px solid #d8e0e8;border-radius:6px;background:#fbfcfe;padding:6px 8px;font-size:12px}",
            ".readonly-note{color:#52616f;font-size:13px}",
            ".workspace-section{background:#fff;border:1px solid #d8e0e8;border-radius:6px;margin:0 0 16px;padding:12px}",
            ".workspace-section h2{font-size:16px;margin:0 0 8px}",
            ".workspace-empty-state{border:1px dashed #c9d4df;border-radius:6px;background:#fbfcfe;padding:10px}",
            ".workspace-empty-state h3{font-size:13px;margin:0 0 4px}",
            ".workspace-empty-state p{font-size:12px;color:#52616f;margin:0}",
            ".workspace-state-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;margin:8px 0 12px}",
            ".export-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}",
            ".export-group{border:1px solid #e6edf3;border-radius:6px;padding:10px;background:#fbfcfe}",
            ".export-group h3{font-size:14px;margin:0 0 8px}",
            ".offline-review-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin:8px 0}",
            ".offline-review-grid article{border:1px solid #e6edf3;border-radius:6px;background:#fbfcfe;padding:10px}",
            ".offline-review-grid h3{font-size:14px;margin:0 0 6px}",
            ".offline-review-grid ol,.offline-review-grid ul{margin:0 0 0 18px;padding:0}",
            ".offline-file-list{margin-top:8px}",
            ".config-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin:8px 0 12px}",
            ".config-grid article{border:1px solid #e6edf3;border-radius:6px;background:#fbfcfe;padding:10px}",
            ".config-grid h3{font-size:14px;margin:0 0 6px}",
            ".config-grid p{font-size:13px;margin:5px 0;color:#243447}",
            ".config-table-wrap{margin:8px 0 12px}",
            ".config-links{margin-top:8px}",
            ".mini-table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e6edf3}",
            ".mini-table th,.mini-table td{padding:6px 8px;border-bottom:1px solid #edf2f7;text-align:left;font-size:12px;vertical-align:top}",
            ".snapshot-delta{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}",
            ".delta-item{border:1px solid #e6edf3;border-radius:6px;padding:8px;background:#fbfcfe}",
            ".delta-item span{display:block;color:#52616f;font-size:12px}",
            ".delta-item strong{font-size:18px}",
            ".snapshot-picker{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}",
            ".snapshot-picker select,.snapshot-picker button{height:34px;border:1px solid #c9d4df;border-radius:6px;background:#fff;padding:0 10px;font-size:13px}",
            ".snapshot-picker button{cursor:pointer;background:#edf3f8}",
            ".snapshot-comparison-view{border:1px solid #e6edf3;border-radius:6px;background:#fbfcfe;padding:10px;margin:8px 0}",
            ".snapshot-comparison-view ul{margin:8px 0 0 18px;padding:0}",
            "#snapshot-detail-select{height:34px;border:1px solid #c9d4df;border-radius:6px;background:#fff;padding:0 10px;font-size:13px;margin:4px 0 10px;max-width:100%}",
            ".snapshot-detail-card h3{font-size:14px;margin:0 0 6px}",
            ".snapshot-detail-card h4{font-size:13px;margin:10px 0 6px}",
            ".trend-point{display:block;border:1px solid #e6edf3;border-radius:6px;background:#fbfcfe;margin:3px 0;padding:4px 6px;font-size:12px}",
            ".note{margin-top:14px;color:#52616f;font-size:13px}",
            "@media(max-width:900px){.warning-summary{display:block}.warning-total{border-right:0;border-bottom:1px solid #e6edf3;margin-bottom:10px;padding-bottom:10px}.workspace-controls,.wizard-controls{grid-template-columns:1fr 1fr}.workspace-controls input,.wizard-controls input{grid-column:1/-1}.detail-kv{grid-template-columns:1fr}.detail-header{display:block}}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>Task Manager 只读本地工作台</h1>",
            workspace_navigation_to_html(workspace.get("navigation") or {}),
            '<section id="workspace-overview" class="workspace-section workspace-overview">',
            "<h2>概览</h2>",
            '<div class="summary">',
            f'<div class="metric">任务数<strong>{escape_html(summary.get("task_count", 0))}</strong></div>',
            f'<div class="metric">运行数<strong>{escape_html(summary.get("run_count", 0))}</strong></div>',
            f'<div class="metric">真实样板<strong>{escape_html(sample_set.get("count", 0))}</strong></div>',
            f'<div class="metric">云效写入<strong>{"关闭" if not workspace.get("yunxiao_write_enabled") else "开启"}</strong></div>',
            "</div>",
            '<section class="warning-summary">',
            '<div class="warning-total">',
            "<span>Warning 汇总</span>",
            f'<strong>{escape_html(warning_summary.get("total_warning_count", 0))}</strong>',
            f'<small>{escape_html(warning_summary.get("task_count_with_warnings", 0))} 个任务存在 warning</small>',
            "</div>",
            '<div class="warning-codes">',
            *warning_code_summary_rows,
            "</div>",
            "</section>",
            "</section>",
            workspace_configuration_to_html(configuration, links),
            workspace_configuration_preview_to_html(configuration_preview, links),
            workspace_config_share_validation_to_html(config_share_validation, links),
            workspace_config_import_draft_to_html(config_import_draft, links),
            workspace_config_import_review_to_html(config_import_review, links),
            workspace_config_template_index_to_html(config_template_index, links),
            workspace_config_wizard_to_html(config_wizard, links),
            workspace_config_review_package_to_html(config_review_package, links),
            workspace_snapshot_history_to_html(snapshot_history),
            workspace_snapshot_detail_to_html(snapshot_detail),
            workspace_snapshot_comparison_to_html(snapshot_comparison),
            workspace_evidence_trend_to_html(evidence_trend),
            workspace_export_index_to_html(export_index),
            workspace_offline_review_to_html(offline_review, ui_polish),
            '<div class="links">',
            f'<a href="{escape_html(dashboard_html)}">Dashboard HTML</a>',
            f'<a href="{escape_html(dashboard_json)}">Dashboard JSON</a>',
            f'<a href="{escape_html(sample_set_json)}">Sample Set JSON</a>',
            f'<a href="{escape_html(sample_set_markdown)}">Sample Set Markdown</a>',
            f'<a href="{escape_html(links.get("export_index_json") or "task_workspace_export_index.json")}">导出索引 JSON</a>',
            f'<a href="{escape_html(links.get("snapshot_comparison_json") or "task_workspace_snapshot_comparison.json")}">快照对比 JSON</a>',
            f'<a href="{escape_html(links.get("snapshot_history_json") or "task_workspace_snapshot_history.json")}">多快照 JSON</a>',
            f'<a href="{escape_html(links.get("evidence_trend_json") or "task_workspace_evidence_trend.json")}">证据趋势 JSON</a>',
            f'<a href="{escape_html(links.get("offline_review_markdown") or "task_workspace_offline_review.md")}">离线审查包</a>',
            (
                f'<a href="{escape_html(links.get("config_summary_markdown") or "task_workspace_config_summary.md")}">配置摘要</a>'
                if configuration
                else ""
            ),
            (
                f'<a href="{escape_html(links.get("config_preview_markdown") or "task_workspace_config_preview.md")}">配置预览</a>'
                if configuration_preview
                else ""
            ),
            (
                f'<a href="{escape_html(links.get("config_share_validation_markdown") or "task_workspace_config_share_validation.md")}">配置分享校验</a>'
                if config_share_validation
                else ""
            ),
            (
                f'<a href="{escape_html(links.get("config_import_draft_markdown") or "task_workspace_config_import_draft.md")}">配置导入草案</a>'
                if config_import_draft
                else ""
            ),
            (
                f'<a href="{escape_html(links.get("config_import_review_markdown") or "task_workspace_config_import_review.md")}">导入回读校验</a>'
                if config_import_review
                else ""
            ),
            (
                f'<a href="{escape_html(links.get("config_template_index_markdown") or "task_workspace_config_template_index.md")}">配置模板索引</a>'
                if config_template_index
                else ""
            ),
            (
                f'<a href="{escape_html(links.get("config_wizard_markdown") or "task_workspace_config_wizard.md")}">配置向导</a>'
                if config_wizard
                else ""
            ),
            (
                f'<a href="{escape_html(links.get("config_review_package_markdown") or "task_workspace_config_review_package.md")}">配置审查包</a>'
                if config_review_package
                else ""
            ),
            "</div>",
            '<section id="workspace-tasks" class="workspace-section">',
            "<h2>任务列表</h2>",
            '<div class="workspace-controls">',
            '<input id="workspace-search" type="search" placeholder="搜索任务、编号、标题、warning、确认卡、需求来源、参数、产物路径" oninput="applyWorkspaceFilters()">',
            f'<select id="warning-filter" onchange="applyWorkspaceFilters()">{warning_options}</select>',
            f'<select id="entity-filter" onchange="applyWorkspaceFilters()">{entity_options}</select>',
            f'<select id="verification-filter" onchange="applyWorkspaceFilters()">{verification_options}</select>',
            f'<select id="ui-evidence-filter" onchange="applyWorkspaceFilters()">{ui_evidence_options}</select>',
            f'<select id="calibration-filter" onchange="applyWorkspaceFilters()">{calibration_options}</select>',
            f'<select id="requirement-evidence-filter" onchange="applyWorkspaceFilters()">{requirement_evidence_options}</select>',
            '<button type="button" onclick="resetWorkspaceFilters()">重置</button>',
            "</div>",
            '<div class="workspace-table-wrap" role="region" aria-label="任务列表">',
            '<table class="workspace-table">',
            "<thead><tr><th>Task</th><th>DFHIS</th><th>标题</th><th>状态</th><th>验证</th><th>UI证据</th><th>Warning</th><th>产物目录</th><th>修改历史</th><th>需求理解确认卡</th><th>需求来源证据</th><th>Workbench</th><th>复跑命令</th></tr></thead>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
            "</div>",
            "</section>",
            '<section id="task-detail-panel" class="detail-shell">',
            "<h2>任务详情</h2>",
            *detail_sections,
            "</section>",
            f'<p class="note">{escape_html(workspace.get("residual_risk") or "")}</p>',
            "<script>",
            f"const workspaceSnapshotComparisons = {snapshot_pair_data_json};",
            "function escapeHtmlText(value){return String(value || '').replace(/[&<>\"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[ch];});}",
            "function snapshotSelectIndex(id, value){",
            "  const select = document.getElementById(id);",
            "  if(!select){return -1;}",
            "  return Array.from(select.options).findIndex(function(option){return option.value === value;});",
            "}",
            "function snapshotPairKey(base, target){",
            "  if(!base || !target || base === target){return '';}",
            "  const baseIndex = snapshotSelectIndex('snapshot-base-select', base);",
            "  const targetIndex = snapshotSelectIndex('snapshot-target-select', target);",
            "  return baseIndex <= targetIndex ? base + '__' + target : target + '__' + base;",
            "}",
            "function showSelectedSnapshotComparison(){",
            "  const view = document.getElementById('snapshot-comparison-view');",
            "  const base = selectedValue('snapshot-base-select');",
            "  const target = selectedValue('snapshot-target-select');",
            "  if(!view){return;}",
            "  const key = snapshotPairKey(base, target);",
            "  const item = key ? workspaceSnapshotComparisons[key] : null;",
            "  if(!item){view.textContent = '请选择两个不同快照，或查看完整多快照 JSON。'; return;}",
            "  const delta = item.summaryDelta || {};",
            "  const changed = (item.changedTasks || []).slice(0, 8).map(function(task){",
            "    return '<li><code>' + escapeHtmlText(task.taskKey || '-') + '</code> ' + escapeHtmlText(task.entityId || '-') + '：' + escapeHtmlText((task.changedFields || []).join(', ') || '-') + '</li>';",
            "  }).join('') || '<li>暂无任务字段变化</li>';",
            "  view.innerHTML = '<strong>' + escapeHtmlText(item.previousGeneratedAt || item.previous) + ' -> ' + escapeHtmlText(item.currentGeneratedAt || item.current) + '</strong>'",
            "    + '<div class=\"snapshot-delta\">'",
            "    + '<div class=\"delta-item\"><span>run_count_delta</span><strong>' + escapeHtmlText(delta.run_count_delta || 0) + '</strong></div>'",
            "    + '<div class=\"delta-item\"><span>warning_count_delta</span><strong>' + escapeHtmlText(delta.warning_count_delta || 0) + '</strong></div>'",
            "    + '<div class=\"delta-item\"><span>change_count_delta</span><strong>' + escapeHtmlText(delta.change_count_delta || 0) + '</strong></div>'",
            "    + '<div class=\"delta-item\"><span>added/removed</span><strong>' + escapeHtmlText(item.addedCount || 0) + '/' + escapeHtmlText(item.removedCount || 0) + '</strong></div>'",
            "    + '</div><ul>' + changed + '</ul>';",
            "}",
            "function showSnapshotDetail(snapshotId){",
            "  document.querySelectorAll('[data-snapshot-detail-id]').forEach(function(item){",
            "    item.hidden = item.dataset.snapshotDetailId !== snapshotId;",
            "  });",
            "}",
            "function selectedValue(id){return document.getElementById(id).value;}",
            "function applyWorkspaceFilters(){",
            "  const query = selectedValue('workspace-search').trim().toLowerCase();",
            "  const warning = selectedValue('warning-filter');",
            "  const entity = selectedValue('entity-filter');",
            "  const verification = selectedValue('verification-filter');",
            "  const uiEvidence = selectedValue('ui-evidence-filter');",
            "  const calibration = selectedValue('calibration-filter');",
            "  const requirementEvidence = selectedValue('requirement-evidence-filter');",
            "  document.querySelectorAll('.workspace-row').forEach(function(row){",
            "    const warnings = (row.dataset.warningCodes || '').split('|').filter(Boolean);",
            "    const matchesQuery = !query || (row.dataset.search || '').toLowerCase().includes(query);",
            "    const matchesWarning = !warning || warnings.includes(warning);",
            "    const matchesEntity = !entity || row.dataset.entityId === entity;",
            "    const matchesVerification = !verification || row.dataset.verificationStatus === verification;",
            "    const matchesUi = !uiEvidence || row.dataset.uiEvidenceStatus === uiEvidence;",
            "    const matchesCalibration = !calibration || row.dataset.requirementCalibrationStatus === calibration;",
            "    const matchesRequirementEvidence = !requirementEvidence || row.dataset.requirementEvidenceStatus === requirementEvidence;",
            "    row.hidden = !(matchesQuery && matchesWarning && matchesEntity && matchesVerification && matchesUi && matchesCalibration && matchesRequirementEvidence);",
            "  });",
            "  ensureVisibleDetail();",
            "}",
            "function resetWorkspaceFilters(){",
            "  ['workspace-search','warning-filter','entity-filter','verification-filter','ui-evidence-filter','calibration-filter','requirement-evidence-filter'].forEach(function(id){",
            "    document.getElementById(id).value = '';",
            "  });",
            "  applyWorkspaceFilters();",
            "}",
            "function applyWizardFilters(){",
            "  const search = (document.getElementById('wizard-step-search') || {}).value || '';",
            "  const status = (document.getElementById('wizard-status-filter') || {}).value || '';",
            "  const blocking = (document.getElementById('wizard-blocking-filter') || {}).value || '';",
            "  const query = search.trim().toLowerCase();",
            "  let visible = 0;",
            "  document.querySelectorAll('.wizard-step-row').forEach(function(row){",
            "    const matchesQuery = !query || (row.dataset.wizardSearch || '').toLowerCase().includes(query);",
            "    const matchesStatus = !status || row.dataset.wizardStatus === status;",
            "    const matchesBlocking = !blocking || row.dataset.wizardBlocking === blocking;",
            "    row.hidden = !(matchesQuery && matchesStatus && matchesBlocking);",
            "    if(!row.hidden){visible += 1;}",
            "  });",
            "  const empty = document.getElementById('wizard-empty-state');",
            "  if(empty){empty.hidden = visible !== 0;}",
            "}",
            "function resetWizardFilters(){",
            "  ['wizard-step-search','wizard-status-filter','wizard-blocking-filter'].forEach(function(id){",
            "    const node = document.getElementById(id);",
            "    if(node){node.value = '';}",
            "  });",
            "  applyWizardFilters();",
            "}",
            "function applyReviewPackageFilters(){",
            "  const search = (document.getElementById('review-package-file-search') || {}).value || '';",
            "  const status = (document.getElementById('review-package-file-status-filter') || {}).value || '';",
            "  const query = search.trim().toLowerCase();",
            "  let visible = 0;",
            "  document.querySelectorAll('.review-package-file-row').forEach(function(row){",
            "    const matchesQuery = !query || (row.dataset.reviewPackageSearch || '').toLowerCase().includes(query);",
            "    const matchesStatus = !status || row.dataset.reviewPackageFileStatus === status;",
            "    row.hidden = !(matchesQuery && matchesStatus);",
            "    if(!row.hidden){visible += 1;}",
            "  });",
            "  const empty = document.getElementById('review-package-empty-state');",
            "  if(empty){empty.hidden = visible !== 0;}",
            "}",
            "function resetReviewPackageFilters(){",
            "  ['review-package-file-search','review-package-file-status-filter'].forEach(function(id){",
            "    const node = document.getElementById(id);",
            "    if(node){node.value = '';}",
            "  });",
            "  applyReviewPackageFilters();",
            "}",
            "function copyWizardCommand(button){",
            "  const command = button.dataset.copyCommand || '';",
            "  if(!command){return;}",
            "  const original = button.textContent;",
            "  const markCopied = function(){button.textContent = '已复制'; window.setTimeout(function(){button.textContent = original || '复制';}, 1400);};",
            "  if(navigator.clipboard && navigator.clipboard.writeText){navigator.clipboard.writeText(command).then(markCopied).catch(function(){button.textContent = '手动复制';}); return;}",
            "  button.textContent = '手动复制';",
            "}",
            "function showTaskDetail(taskKey){",
            "  document.querySelectorAll('.task-detail').forEach(function(detail){",
            "    detail.hidden = detail.dataset.detailTaskKey !== taskKey;",
            "  });",
            "  document.querySelectorAll('.workspace-row').forEach(function(row){",
            "    row.classList.toggle('is-selected', row.dataset.taskKey === taskKey);",
            "  });",
            "}",
            "function switchDetailTab(button, tabName){",
            "  const detail = button.closest('.task-detail');",
            "  if(!detail){return;}",
            "  detail.querySelectorAll('.detail-tab-button').forEach(function(tab){",
            "    tab.classList.toggle('active', tab === button);",
            "  });",
            "  detail.querySelectorAll('.detail-tab-panel').forEach(function(panel){",
            "    const active = panel.dataset.tab === tabName;",
            "    panel.hidden = !active;",
            "    panel.classList.toggle('active', active);",
            "  });",
            "}",
            "function ensureVisibleDetail(){",
            "  const selected = document.querySelector('.workspace-row.is-selected');",
            "  if(selected && !selected.hidden){return;}",
            "  const firstVisible = Array.from(document.querySelectorAll('.workspace-row')).find(function(row){return !row.hidden;});",
            "  if(firstVisible){showTaskDetail(firstVisible.dataset.taskKey || '');}",
            "}",
            "document.addEventListener('DOMContentLoaded', function(){ensureVisibleDetail(); showSelectedSnapshotComparison(); showSnapshotDetail(selectedValue('snapshot-detail-select')); applyWizardFilters(); applyReviewPackageFilters();});",
            "</script>",
            "</body>",
            "</html>",
        ]
    )


def workspace_select_options(values: list[str], all_label: str) -> str:
    options = [f'<option value="">{escape_html(all_label)}</option>']
    for value in values:
        value_text = str(value).strip()
        if value_text:
            options.append(f'<option value="{escape_html(value_text)}">{escape_html(value_text)}</option>')
    return "\n".join(options)


def escape_html(value: object) -> str:
    return html_lib.escape(str(value), quote=True)


def build_task_output_dir(*, output_root: str, task: dict, execution_mode: str) -> Path:
    root = Path(output_root).expanduser().resolve()
    key = safe_slug(str(task.get("task_key") or task.get("entity_id") or "manual"))
    timestamp = database.now_iso().replace(":", "").replace("+", "_").replace(".", "_")
    return root / key / f"{execution_mode}_{timestamp}"


def infer_verification_status(*, run: dict, result: WorkflowResult) -> str:
    run_id = run.get("id")
    if run_id:
        for artifact in database.get_artifacts(int(run_id)):
            if artifact.get("kind") not in {"worktree_manifest_json", "verification_matrix_json", "fullstack_manifest_json"}:
                continue
            try:
                payload = json.loads(str(artifact.get("content") or "{}"))
            except json.JSONDecodeError:
                continue
            status = str(payload.get("verification_status") or payload.get("overall_status") or "").strip()
            if status:
                return "passed" if status == "pass" else status
    if result.status != "success":
        return "failed"
    if result.evaluation_status == "pass":
        return "passed"
    return result.evaluation_status or "unknown"


def can_commit_from_output(*, output_dir: Path, status: str) -> bool:
    if status != "success":
        return False
    summary_path = output_dir / "commit_ready_summary.md"
    if not summary_path.exists():
        return False
    text = summary_path.read_text(encoding="utf-8", errors="ignore")
    return "可以进入人工代码审查后提交" in text or "可以提交" in text


def read_existing_output_summary(output_dir: Path) -> dict:
    core_closure_summary = read_core_closure_output_summary(output_dir)
    if core_closure_summary:
        return core_closure_summary

    precommit = read_json_file(output_dir / "precommit_manifest.json")
    matrix = read_json_file(output_dir / "verification_matrix.json")
    manifest = precommit.get("manifest") if isinstance(precommit.get("manifest"), dict) else {}
    targets = precommit.get("targets") if isinstance(precommit.get("targets"), list) else []
    project_paths = unique_keep_order(
        [
            str(manifest.get("project_path") or ""),
            *[str(item.get("project_path") or "") for item in targets if isinstance(item, dict)],
        ]
    )
    status = str(precommit.get("status") or "").strip()
    overall_status = str(matrix.get("overall_status") or "").strip().lower()
    evaluation_status = "pass" if overall_status == "pass" else ("failed" if overall_status in {"failed", "fail"} else "")
    if not status:
        status = "success" if overall_status == "pass" else ("failed" if overall_status in {"failed", "fail"} else "recorded")
    return {
        "status": status,
        "evaluation_status": evaluation_status,
        "verification_status": "passed" if overall_status == "pass" else ("failed" if overall_status in {"failed", "fail"} else ""),
        "summary": matrix.get("summary") or precommit.get("summary") or "",
        "can_commit": bool(matrix.get("can_commit")),
        "can_yunxiao_comment": bool(matrix.get("can_yunxiao_comment")),
        "title": manifest.get("title") or "",
        "entity_id": manifest.get("entity_id") or "",
        "demand_text": manifest.get("demand_text") or "",
        "project_root": manifest.get("project_root") or "",
        "project_paths": project_paths,
    }


def read_core_closure_output_summary(output_dir: Path) -> dict:
    run_document = read_json_file(output_dir / "run.json")
    run = run_document.get("run") if isinstance(run_document.get("run"), dict) else {}
    artifacts = run_document.get("artifacts") if isinstance(run_document.get("artifacts"), list) else []
    diff_review = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("kind") != "core_diff_review_json":
            continue
        content = artifact.get("content")
        if isinstance(content, dict):
            diff_review = content
            break
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                diff_review = parsed
                break
    if not run or not diff_review:
        return {}

    status = str(run.get("status") or "").strip()
    evaluation_status = str(run.get("evaluation_status") or "").strip()
    diff_review_status = str(diff_review.get("status") or "").strip().lower()
    verification_status = ""
    if status != "success" or diff_review_status != "pass":
        verification_status = "failed"
    elif evaluation_status == "ready_for_manual_review":
        verification_status = "passed"
    return {
        "source_run_id": run.get("id"),
        "status": status or "recorded",
        "evaluation_status": evaluation_status,
        "verification_status": verification_status,
        "summary": run.get("evaluation_summary") or "",
        "can_commit": False,
        "can_yunxiao_comment": False,
        "title": run.get("title") or "",
        "entity_id": "",
        "demand_text": run.get("demand_text") or "",
        "project_root": "",
        "project_paths": [],
    }


def parse_optional_run_id(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def write_precommit_result_outputs(*, result: PrecommitVerificationResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "precommit_manifest.json").write_text(result.to_json(), encoding="utf-8")
    (output_dir / "verification_matrix.json").write_text(result.matrix_json(), encoding="utf-8")
    (output_dir / "verification_matrix.md").write_text(result.matrix_markdown(), encoding="utf-8")
    (output_dir / "behavior_acceptance.json").write_text(result.behavior_json(), encoding="utf-8")
    (output_dir / "behavior_acceptance.md").write_text(result.behavior_markdown(), encoding="utf-8")
    (output_dir / "method_test_runner.json").write_text(result.method_test_runner_json(), encoding="utf-8")
    (output_dir / "method_test_runner.md").write_text(result.method_test_runner_markdown(), encoding="utf-8")
    (output_dir / "ui_evidence_runner.json").write_text(result.ui_evidence_runner_json(), encoding="utf-8")
    (output_dir / "ui_evidence_runner.md").write_text(result.ui_evidence_runner_markdown(), encoding="utf-8")
    (output_dir / "interaction_evidence.json").write_text(result.interaction_json(), encoding="utf-8")
    (output_dir / "interaction_evidence.md").write_text(result.interaction_markdown(), encoding="utf-8")
    (output_dir / "behavior_test_plan.json").write_text(result.behavior_test_plan_json(), encoding="utf-8")
    (output_dir / "behavior_test_plan.md").write_text(result.behavior_test_plan_markdown(), encoding="utf-8")
    (output_dir / "method_regression_result.json").write_text(result.method_regression_json(), encoding="utf-8")
    (output_dir / "method_regression_result.md").write_text(result.method_regression_markdown(), encoding="utf-8")
    (output_dir / "ui_evidence_manifest.json").write_text(result.ui_evidence_json(), encoding="utf-8")
    (output_dir / "ui_evidence_manifest.md").write_text(result.ui_evidence_markdown(), encoding="utf-8")
    (output_dir / "playwright_screenshot_index.md").write_text(result.playwright_screenshot_index_markdown(), encoding="utf-8")
    (output_dir / "code_review.md").write_text(result.code_review_markdown(), encoding="utf-8")
    (output_dir / "commit_ready_summary.md").write_text(result.commit_ready_markdown(), encoding="utf-8")


def read_latest_precommit_manifest(task: dict) -> dict:
    output_dir = str(task.get("latest_output_dir") or "").strip()
    if not output_dir:
        return {}
    return read_json_file(Path(output_dir).expanduser() / "precommit_manifest.json")


def resolve_precommit_project_path(*, options: TaskPrecommitRerunOptions, task: dict, latest_manifest: dict) -> str:
    if options.project_path:
        return str(Path(options.project_path).expanduser().resolve())
    project_paths = task.get("project_paths") or []
    if project_paths:
        return str(Path(str(project_paths[0])).expanduser().resolve())
    manifest = latest_manifest.get("manifest") if isinstance(latest_manifest.get("manifest"), dict) else {}
    if manifest.get("project_path"):
        return str(Path(str(manifest["project_path"])).expanduser().resolve())
    targets = latest_manifest.get("targets") if isinstance(latest_manifest.get("targets"), list) else []
    for target in targets:
        if isinstance(target, dict) and target.get("project_path"):
            return str(Path(str(target["project_path"])).expanduser().resolve())
    return ""


def resolve_precommit_allowed_paths(latest_manifest: dict) -> list[str]:
    paths: list[str] = []
    targets = latest_manifest.get("targets") if isinstance(latest_manifest.get("targets"), list) else []
    for target in targets:
        if isinstance(target, dict):
            paths.extend(str(path) for path in target.get("allowed_paths") or [])
    return unique_keep_order(paths)


def resolve_precommit_verify_commands(latest_manifest: dict) -> list[str]:
    commands: list[str] = []
    targets = latest_manifest.get("targets") if isinstance(latest_manifest.get("targets"), list) else []
    for target in targets:
        if not isinstance(target, dict):
            continue
        for verify in target.get("verify") or []:
            if isinstance(verify, dict) and verify.get("command"):
                commands.append(str(verify["command"]))
        commands.extend(str(command) for command in target.get("verify_commands") or [])
    return unique_keep_order(commands)


def read_json_file(path: Path) -> dict:
    if not path.exists() or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_markdown_preview(path: Path, *, limit: int = 6) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    preview = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        preview.append(text)
        if len(preview) >= limit:
            break
    return preview


def read_text_preview(path: Path, *, limit: int = 8) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    if path.suffix.lower() == ".json":
        value = read_json_file(path)
        if value:
            return json.dumps(value, ensure_ascii=False, indent=2).splitlines()[:limit]
    return read_markdown_preview(path, limit=limit)


def infer_existing_output_verification_status(*, output_summary: dict, status: str, evaluation_status: str) -> str:
    if output_summary.get("verification_status"):
        return str(output_summary["verification_status"])
    if status != "success":
        return "failed"
    if evaluation_status == "pass":
        return "passed"
    return evaluation_status or "unknown"


def merge_metadata(*items: dict) -> dict:
    merged: dict = {}
    for item in items:
        merged.update(item)
    return merged


def write_task_manager_record_outputs(*, output_dir: Path, task: dict, task_run: dict) -> None:
    record = {
        "version": "0.10.8-task-manager-output",
        "task_id": task.get("id"),
        "task_key": task.get("task_key"),
        "run_id": task.get("latest_run_id"),
        "task_run_id": task_run.get("id"),
        "output_dir": str(output_dir),
        "stage": task_run.get("stage"),
        "execution_mode": task_run.get("execution_mode"),
        "status": task_run.get("status"),
        "verification_status": task_run.get("verification_status"),
        "can_commit": bool(task.get("can_commit")),
        "can_yunxiao_transition": False,
        "artifact_paths": task.get("latest_artifacts") or {},
    }
    (output_dir / "task_manager_real_trial_record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Task Manager 真实样板登记记录",
        "",
        f"- Task ID：{record['task_id']}",
        f"- Task Key：{record['task_key']}",
        f"- Run ID：{record['run_id']}",
        f"- Task Run ID：{record['task_run_id']}",
        f"- 阶段：{record['stage']}",
        f"- 执行模式：{record['execution_mode']}",
        f"- 状态：{record['status']}",
        f"- 验证状态：{record['verification_status']}",
        f"- 是否可提交：{'是' if record['can_commit'] else '否'}",
        f"- 云效真实流转：否",
        f"- 产物目录：{record['output_dir']}",
        "",
        "## 产物索引",
    ]
    for key, path in sorted((record.get("artifact_paths") or {}).items()):
        lines.append(f"- {key}: {path}")
    (output_dir / "task_manager_real_trial_record.md").write_text("\n".join(lines), encoding="utf-8")


def write_task_manager_run_history(*, output_dir: Path, task: dict, runs: list[dict]) -> None:
    history = {
        "version": "0.10.7-task-manager-run-history",
        "task_id": task.get("id"),
        "task_key": task.get("task_key"),
        "latest_run_id": task.get("latest_run_id"),
        "latest_output_dir": task.get("latest_output_dir"),
        "runs": [
            {
                "task_run_id": item.get("id"),
                "run_id": item.get("run_id"),
                "stage": item.get("stage"),
                "execution_mode": item.get("execution_mode"),
                "status": item.get("status"),
                "evaluation_status": item.get("evaluation_status"),
                "verification_status": item.get("verification_status"),
                "output_dir": item.get("output_dir"),
                "summary": item.get("summary"),
                "started_at": item.get("started_at"),
                "finished_at": item.get("finished_at"),
            }
            for item in runs
        ],
    }
    (output_dir / "task_manager_run_history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Task Manager Run 历史",
        "",
        f"- Task ID：{history['task_id']}",
        f"- Task Key：{history['task_key']}",
        f"- 最新 Run：{history['latest_run_id'] or '-'}",
        f"- 最新产物目录：{history['latest_output_dir'] or '-'}",
        "",
        "| Task Run | Run ID | 阶段 | 模式 | 状态 | 验证 | 产物目录 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in history["runs"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("task_run_id") or "-"),
                    str(item.get("run_id") or "-"),
                    str(item.get("stage") or "-"),
                    str(item.get("execution_mode") or "-"),
                    str(item.get("status") or "-"),
                    str(item.get("verification_status") or item.get("evaluation_status") or "-"),
                    str(item.get("output_dir") or "-"),
                ]
            )
            + " |"
        )
    (output_dir / "task_manager_run_history.md").write_text("\n".join(lines), encoding="utf-8")


def write_ui_evidence_reuse_policy(*, output_dir: Path, task: dict) -> None:
    ui_manifest = read_json_file(output_dir / "ui_evidence_manifest.json")
    ui_runner = read_json_file(output_dir / "ui_evidence_runner.json")
    artifacts = ui_manifest.get("artifacts") if isinstance(ui_manifest.get("artifacts"), list) else []
    assertions = ui_manifest.get("assertions") if isinstance(ui_manifest.get("assertions"), list) else []
    reusable = bool(artifacts or assertions or ui_runner.get("artifact_paths"))
    policy = {
        "version": "0.10.8-ui-evidence-reuse-policy",
        "task_id": task.get("id"),
        "task_key": task.get("task_key"),
        "output_dir": str(output_dir),
        "reusable": reusable,
        "evidence_status": ui_manifest.get("status") or ui_runner.get("status") or ("missing" if not reusable else "present"),
        "artifact_count": len(artifacts) + len(ui_runner.get("artifact_paths") or []),
        "assertion_count": len(assertions) + len(ui_runner.get("assertions") or []),
        "reuse_rules": [
            "仅复用同一 task_id/task_key 下的 UI 证据，不跨需求或跨业务页面复用。",
            "复用前必须确认路由参数、登录态、测试患者、日期、班次和页面入口与当前验证目标一致。",
            "人工截图或人工验收记录只能作为辅助证据；涉及弹框、loading、进度条关闭状态时优先重新采集自动证据。",
            "复跑后如果代码 diff、菜单参数或接口数据变化，旧 UI 证据必须标记为历史证据，不能直接作为新结论。",
        ],
        "residual_risk": "UI 证据依赖本地浏览器、业务项目启动方式和登录态；Task Manager 只登记与复用索引，不替代人工业务验收。",
    }
    (output_dir / "ui_evidence_reuse_policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# UI 证据复用策略",
        "",
        f"- Task ID：{policy['task_id']}",
        f"- Task Key：{policy['task_key']}",
        f"- 证据状态：{policy['evidence_status']}",
        f"- 是否可复用：{'是' if policy['reusable'] else '否'}",
        f"- 证据数量：{policy['artifact_count']}",
        f"- 断言数量：{policy['assertion_count']}",
        "",
        "## 复用规则",
    ]
    for rule in policy["reuse_rules"]:
        lines.append(f"- {rule}")
    lines.extend(["", "## 残余风险", "", f"- {policy['residual_risk']}"])
    (output_dir / "ui_evidence_reuse_policy.md").write_text("\n".join(lines), encoding="utf-8")


def stage_for_execution_mode(execution_mode: str) -> str:
    mapping = {
        "readonly": "analysis",
        "worktree": "code_trial",
        "fullstack-worktree": "fullstack_code_trial",
        "review-worktree": "review",
        "precommit-verify": "precommit_verify",
        "single-demand-trial": "single_demand_trial",
        "core-closure-trial": "core_closure_trial",
        "auto-local": "auto_local",
        "manual-runtime-verification": "manual_runtime_verification",
    }
    return mapping.get(execution_mode, execution_mode or "unknown")


def infer_entity_kind(url: str) -> str:
    lowered = (url or "").lower()
    if "/bug/" in lowered:
        return "bug"
    if "/req/" in lowered or "/requirement/" in lowered:
        return "requirement"
    if "/task/" in lowered:
        return "task"
    return ""


def normalize_entity_id(value: str) -> str:
    return value.strip().upper()


def build_task_key(*, entity_kind: str, entity_id: str, title: str) -> str:
    if entity_id:
        prefix = entity_kind or "dfhis"
        return f"{prefix}-{entity_id}".lower()
    return "manual-" + safe_slug(title or database.now_iso())


def suggest_work_branch(*, entity_kind: str, entity_id: str) -> str:
    if not entity_id:
        return ""
    if entity_kind == "bug":
        return f"hotfix-{entity_id}"
    return f"feature-{entity_id}"


def unique_keep_order(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "task"


def task_to_markdown(task: dict, runs: list[dict] | None = None) -> str:
    project_paths = task.get("project_paths") or []
    latest_artifacts = task.get("latest_artifacts") or {}
    lines = [
        f"# Harness Task {task.get('id')}",
        "",
        f"- Task Key：{task.get('task_key')}",
        f"- 类型：{task.get('entity_kind') or '-'}",
        f"- 编号：{task.get('entity_id') or '-'}",
        f"- 标题：{task.get('entity_title') or '-'}",
        f"- 云效链接：{task.get('entity_url') or '-'}",
        f"- 当前阶段：{task.get('current_stage')}",
        f"- 状态：{task.get('status')}",
        f"- 验证状态：{task.get('verification_status') or '-'}",
        f"- 是否可提交：{'是' if task.get('can_commit') else '否'}",
        f"- 是否允许云效真实流转：否",
        f"- 项目根：{task.get('project_root') or '-'}",
        f"- 项目路径：{', '.join(project_paths) if project_paths else '-'}",
        f"- 基线分支：{task.get('base_branch') or '-'}",
        f"- 工作分支：{task.get('work_branch') or '-'}",
        f"- 最新 Run：{task.get('latest_run_id') or '-'}",
        f"- 最新产物目录：{task.get('latest_output_dir') or '-'}",
        "",
        "## 最新产物",
    ]
    if latest_artifacts:
        for key, path in latest_artifacts.items():
            lines.append(f"- {key}: {path}")
    else:
        lines.append("- 暂无")
    lines.extend(["", "## 运行记录"])
    for item in runs or []:
        lines.append(
            f"- task_run={item.get('id')} run={item.get('run_id') or '-'} "
            f"stage={item.get('stage')} mode={item.get('execution_mode')} "
            f"status={item.get('status')} output={item.get('output_dir') or '-'}"
        )
    if not runs:
        lines.append("- 暂无")
    return "\n".join(lines)


def task_to_json(task: dict, runs: list[dict] | None = None) -> str:
    return json.dumps({"task": task, "runs": runs or []}, ensure_ascii=False, indent=2)
