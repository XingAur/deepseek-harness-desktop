#!/usr/bin/env python3
"""Canonical append-only Harness history implementation owned by his-harness-core."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


CONTRACT_VERSION = "harness-history.v1"
EVIDENCE_CONTRACT_VERSION = "requirement-evidence.v2"
PROJECT_ROLES = frozenset({"primary", "affected", "reference"})
STAGE_STATUSES = {
    "project_mapping": frozenset({"pending", "in_progress", "completed", "blocked"}),
    "analysis": frozenset({"pending", "in_progress", "completed", "blocked"}),
    "change_decision": frozenset(
        {"pending", "in_progress", "can_change", "cannot_change", "blocked"}
    ),
    "implementation": frozenset(
        {"pending", "in_progress", "completed", "blocked", "skipped"}
    ),
    "codex_review": frozenset(
        {"pending", "in_progress", "passed", "failed", "blocked"}
    ),
    "verification": frozenset(
        {"pending", "in_progress", "passed", "failed", "blocked"}
    ),
    "apply_back": frozenset(
        {"not_requested", "pending", "in_progress", "applied", "failed", "blocked"}
    ),
}
STAGE_DIRECTORIES = {
    "project_mapping": "stage-records",
    "analysis": "analysis",
    "change_decision": "decisions",
    "implementation": "changes",
    "codex_review": "reviews",
    "verification": "verification",
    "apply_back": "apply-back",
}
RUN_SUBDIRECTORIES = (
    "events",
    "interactions",
    "stage-records",
    "projects",
    "analysis",
    "decisions",
    "reviews",
    "changes",
    "verification",
    "apply-back",
)
TASK_ID_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*-\d+")
PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9_-]*")
RUN_ID_PATTERN = re.compile(r"\d{8}-\d{6}(?:-\d{2})?")
PROJECT_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
REVIEW_SEVERITIES = frozenset({"Critical", "Important", "Minor"})
DECISION_VERDICTS = frozenset({"can_change", "cannot_change"})
DELIVERY_STAGES = frozenset({"workspace", "commit", "remote"})
DELIVERY_TARGET_WORKTREE = "WORKTREE"
DELIVERY_REF_PATTERN = re.compile(r"(?:HEAD|[a-f0-9]{7,64}|[A-Za-z0-9][A-Za-z0-9._/-]{0,254})")
INTERACTION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
INTERACTION_KINDS = frozenset(
    {"clarification", "scope_confirmation", "business_decision", "delivery_confirmation"}
)
STRUCTURED_ONLY_STAGE_STATUSES = {
    "change_decision": DECISION_VERDICTS,
    "implementation": frozenset({"completed"}),
    "codex_review": frozenset({"passed", "failed"}),
    "verification": frozenset({"passed", "failed"}),
    "apply_back": frozenset({"applied", "failed"}),
}


def archive_evidence(
    *,
    source_dir: str | Path,
    history_root: str | Path,
    provider: str,
    ticket_id: str,
    run_id: str | None = None,
    intake_required: bool = False,
) -> dict[str, str]:
    if type(intake_required) is not bool:
        raise ValueError("intake_required must be boolean")
    provider = _checked_provider(provider)
    ticket_id = _checked_ticket_id(ticket_id)
    run_id = _checked_run_id(run_id or datetime.now().strftime("%Y%m%d-%H%M%S"))
    source = _checked_evidence_source(source_dir)
    evidence = _load_json(source / "requirement_evidence.v2.json")
    evidence_errors = _validate_evidence_package(source, evidence)
    if evidence_errors:
        raise ValueError("invalid evidence package: " + "; ".join(evidence_errors))

    source_provider = str(evidence.get("provider") or "").upper()
    requested_id = str((evidence.get("source") or {}).get("requested_id") or "").upper()
    if source_provider != provider:
        raise ValueError(
            f"evidence provider mismatch: expected {provider}, got {source_provider or '-'}"
        )
    if requested_id != ticket_id:
        raise ValueError(
            f"evidence ticket mismatch: expected {ticket_id}, got {requested_id or '-'}"
        )

    root = _checked_history_root(history_root)
    task_dir = root / provider / ticket_id
    revision_dir = task_dir / "evidence" / "revisions" / run_id
    run_dir = task_dir / "runs" / run_id
    worktree_dir = task_dir / "worktrees" / run_id
    for target in (revision_dir, run_dir):
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"history revision already exists: {target}")

    title, category = _task_identity(evidence, ticket_id)
    created_at = str((evidence.get("source") or {}).get("fetched_at") or "")
    root.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    _ensure_root_files(root)
    _ensure_task_files(
        task_dir=task_dir,
        provider=provider,
        ticket_id=ticket_id,
        title=title,
        category=category,
    )

    revision_dir.parent.mkdir(parents=True, exist_ok=True)
    _copy_tree_exclusive(source, revision_dir)
    archived_evidence = _load_json(revision_dir / "requirement_evidence.v2.json")
    copied_errors = _validate_evidence_package(revision_dir, archived_evidence)
    if copied_errors:
        raise ValueError("archived evidence failed verification: " + "; ".join(copied_errors))

    evidence_json_sha256 = _sha256_file(
        revision_dir / "requirement_evidence.v2.json"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in RUN_SUBDIRECTORIES:
        (run_dir / name).mkdir()
    worktree_dir.mkdir(parents=True, exist_ok=False)

    gate = str((evidence.get("decision_gate") or {}).get("state") or "")
    completeness = str((evidence.get("completeness") or {}).get("status") or "")
    run_record = {
        "contract_version": CONTRACT_VERSION,
        "provider": provider,
        "ticket_id": ticket_id,
        "run_id": run_id,
        "created_at": created_at,
        "title": title,
        "category": category,
        "intake_policy": "required" if intake_required else "legacy_optional",
        "evidence": {
            "revision": run_id,
            "relative_path": f"../../evidence/revisions/{run_id}",
            "json_sha256": evidence_json_sha256,
            "decision_gate": gate,
            "completeness": completeness,
        },
        "stages": {
            "project_mapping": "pending",
            "analysis": "pending",
            "change_decision": "pending",
            "implementation": "pending",
            "codex_review": "pending",
            "verification": "pending",
            "apply_back": "not_requested",
        },
        "worktrees": {
            "relative_path": f"../../worktrees/{run_id}",
            "policy": "isolated_per_project",
        },
    }
    _create_json(run_dir / "run.json", run_record)
    _create_json(
        run_dir / "evidence-manifest.json",
        {
            "contract_version": CONTRACT_VERSION,
            "provider": provider,
            "ticket_id": ticket_id,
            "run_id": run_id,
            "evidence_revision": run_id,
            "evidence_json_sha256": evidence_json_sha256,
            "evidence_content_sha256": str(
                (evidence.get("integrity") or {}).get("evidence_sha256") or ""
            ),
            "verified_files": _successful_file_count(evidence),
        },
    )
    _append_event(
        run_dir,
        event_type="evidence_archived",
        summary=f"已归档并校验证据版本 {run_id}",
    )
    _rebuild_state(run_dir)
    _write_index(root)
    return {
        "task_dir": str(task_dir),
        "run_dir": str(run_dir),
        "evidence_dir": str(revision_dir),
        "worktree_dir": str(worktree_dir),
        "decision_gate": gate,
        "completeness": completeness,
    }


def record_project(
    *,
    task_dir: str | Path,
    run_id: str,
    name: str,
    repo_path: str | Path,
    role: str,
    reason: str,
    worktree_path: str | Path | None = None,
    base_branch: str = "",
    base_commit: str = "",
    historical_commits: list[str] | None = None,
) -> dict[str, Any]:
    task = _checked_existing_task_dir(task_dir)
    run_id = _checked_run_id(run_id)
    if not PROJECT_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"invalid project name: {name}")
    if role not in PROJECT_ROLES:
        raise ValueError(f"invalid project role: {role}")
    repo = Path(repo_path)
    if not repo.is_absolute():
        raise ValueError("repo_path must be absolute")
    if not reason.strip():
        raise ValueError("project mapping reason is required")
    commits = historical_commits or []
    for commit in commits:
        if not re.fullmatch(r"[a-f0-9]{7,64}", commit):
            raise ValueError(f"invalid historical commit: {commit}")

    run_dir = task / "runs" / run_id
    if not (run_dir / "run.json").is_file():
        raise FileNotFoundError(f"run does not exist: {run_dir}")
    expected_worktree_root = task / "worktrees" / run_id
    _ensure_no_symlink_components(expected_worktree_root, task)
    normalized_worktree = ""
    if worktree_path:
        candidate = Path(worktree_path)
        if not candidate.is_absolute():
            raise ValueError("worktree_path must be absolute")
        if ".." in candidate.parts or not _is_within_lexical(
            candidate,
            expected_worktree_root,
        ):
            raise ValueError(
                f"worktree_path must stay inside {expected_worktree_root}"
            )
        _ensure_no_symlink_components(candidate.parent, task)
        normalized_worktree = str(candidate)

    record = {
        "contract_version": CONTRACT_VERSION,
        "ticket_id": _task_id_from_dir(task),
        "run_id": run_id,
        "name": name,
        "role": role,
        "reason": reason.strip(),
        "repo_path": str(repo),
        "worktree_path": normalized_worktree,
        "base_branch": base_branch,
        "base_commit": base_commit,
        "historical_commits": commits,
        "status": "mapped",
    }
    project_dir = run_dir / "projects"
    project_dir.mkdir(exist_ok=True)
    json_path = project_dir / f"{name}.json"
    markdown_path = project_dir / f"{name}.md"
    _create_json(json_path, record)
    _create_text(markdown_path, _render_project(record))
    _append_event(
        run_dir,
        event_type="project_mapped",
        stage="project_mapping",
        status="completed",
        summary=f"已登记项目 {name}（{role}）",
        details={"project": name, "role": role},
    )
    _rebuild_state(run_dir)
    return record


def record_stage(
    *,
    task_dir: str | Path,
    run_id: str,
    stage: str,
    status: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = _checked_existing_task_dir(task_dir)
    run_id = _checked_run_id(run_id)
    if stage not in STAGE_STATUSES:
        raise ValueError(f"invalid stage: {stage}")
    if status not in STAGE_STATUSES[stage]:
        raise ValueError(f"invalid status for {stage}: {status}")
    if status in STRUCTURED_ONLY_STAGE_STATUSES.get(stage, frozenset()):
        raise ValueError(
            f"{stage}={status} requires its structured command"
        )
    if not summary.strip():
        raise ValueError("stage summary is required")
    run_dir = task / "runs" / run_id
    if not (run_dir / "run.json").is_file():
        raise FileNotFoundError(f"run does not exist: {run_dir}")
    _ensure_run_directories(run_dir)
    event = _append_event(
        run_dir,
        event_type="stage_recorded",
        stage=stage,
        status=status,
        summary=summary.strip(),
        details=details,
    )
    record_dir = run_dir / STAGE_DIRECTORIES[stage]
    record_dir.mkdir(exist_ok=True)
    _create_json(
        record_dir / f"{event['sequence']:04d}-{stage}.json",
        event,
    )
    _rebuild_state(run_dir)
    return event


def record_interaction_request(
    *,
    task_dir: str | Path,
    run_id: str,
    interaction_id: str,
    kind: str,
    question: str,
    options: list[str],
    resume_stage: str,
    next_action: str,
) -> dict[str, Any]:
    _, run_dir, _ = _run_context(task_dir, run_id)
    interaction_id = _checked_interaction_id(interaction_id)
    if kind not in INTERACTION_KINDS:
        raise ValueError(f"invalid interaction kind: {kind}")
    if resume_stage not in STAGE_STATUSES:
        raise ValueError(f"invalid interaction resume stage: {resume_stage}")
    normalized_question = question.strip()
    normalized_next_action = next_action.strip()
    if not normalized_question:
        raise ValueError("interaction question is required")
    if not normalized_next_action:
        raise ValueError("interaction next_action is required")
    normalized_options = _checked_strings(options, "interaction options")
    state = _interaction_state(run_dir)
    if state["status"] in {"awaiting_user", "resolved_resume_required"}:
        raise ValueError(
            f"pending interaction already exists: {state['interaction_id']}"
        )
    if any(
        record.get("interaction_id") == interaction_id
        for record in _all_json_records(run_dir / "interactions")
    ):
        raise ValueError(f"interaction_id already exists: {interaction_id}")
    event = _append_event(
        run_dir,
        event_type="interaction_requested",
        summary=normalized_question,
        details={
            "interaction_id": interaction_id,
            "kind": kind,
            "resume_stage": resume_stage,
            "next_action": normalized_next_action,
        },
    )
    record = {
        "contract_version": CONTRACT_VERSION,
        "record_type": "interaction_request",
        "sequence": event["sequence"],
        "recorded_at": event["recorded_at"],
        "interaction_id": interaction_id,
        "kind": kind,
        "question": normalized_question,
        "options": normalized_options,
        "resume_stage": resume_stage,
        "next_action": normalized_next_action,
    }
    _create_json(
        run_dir / "interactions" / f"{event['sequence']:04d}-interaction_request.json",
        record,
    )
    _rebuild_state(run_dir)
    return record


def record_interaction_resolution(
    *,
    task_dir: str | Path,
    run_id: str,
    interaction_id: str,
    answer: str,
) -> dict[str, Any]:
    _, run_dir, _ = _run_context(task_dir, run_id)
    interaction_id = _checked_interaction_id(interaction_id)
    normalized_answer = answer.strip()
    if not normalized_answer:
        raise ValueError("interaction answer is required")
    state = _interaction_state(run_dir)
    if state["status"] != "awaiting_user":
        raise ValueError("no interaction is awaiting a user answer")
    if state["interaction_id"] != interaction_id:
        raise ValueError(
            f"pending interaction mismatch: expected {state['interaction_id']}, got {interaction_id}"
        )
    request = state["request"]
    options = request.get("options") or []
    selected_option = normalized_answer if normalized_answer in options else ""
    event = _append_event(
        run_dir,
        event_type="interaction_resolved",
        summary=f"用户已答复待确认项 {interaction_id}",
        details={
            "interaction_id": interaction_id,
            "request_sequence": request["sequence"],
            "resume_stage": request["resume_stage"],
            "next_action": request["next_action"],
        },
    )
    record = {
        "contract_version": CONTRACT_VERSION,
        "record_type": "interaction_resolution",
        "sequence": event["sequence"],
        "recorded_at": event["recorded_at"],
        "interaction_id": interaction_id,
        "request_sequence": request["sequence"],
        "answer": normalized_answer,
        "selected_option": selected_option,
        "resume_stage": request["resume_stage"],
        "next_action": request["next_action"],
        "auto_resume_required": True,
    }
    _create_json(
        run_dir / "interactions" / f"{event['sequence']:04d}-interaction_resolution.json",
        record,
    )
    _rebuild_state(run_dir)
    return record


def record_interaction_resume(
    *,
    task_dir: str | Path,
    run_id: str,
    interaction_id: str,
) -> dict[str, Any]:
    _, run_dir, _ = _run_context(task_dir, run_id)
    interaction_id = _checked_interaction_id(interaction_id)
    state = _interaction_state(run_dir)
    if state["status"] == "awaiting_user" and state["interaction_id"] == interaction_id:
        raise ValueError(f"interaction has not been resolved: {interaction_id}")
    if state["status"] != "resolved_resume_required":
        raise ValueError("no resolved interaction is ready to resume")
    if state["interaction_id"] != interaction_id:
        raise ValueError(
            f"resolved interaction mismatch: expected {state['interaction_id']}, got {interaction_id}"
        )
    request = state["request"]
    resolution = state["resolution"]
    event = _append_event(
        run_dir,
        event_type="interaction_resumed",
        summary=f"已按用户答复恢复 {request['resume_stage']} 阶段",
        details={
            "interaction_id": interaction_id,
            "request_sequence": request["sequence"],
            "resolution_sequence": resolution["sequence"],
            "resume_stage": request["resume_stage"],
            "next_action": request["next_action"],
        },
    )
    record = {
        "contract_version": CONTRACT_VERSION,
        "record_type": "interaction_resume",
        "sequence": event["sequence"],
        "recorded_at": event["recorded_at"],
        "interaction_id": interaction_id,
        "request_sequence": request["sequence"],
        "resolution_sequence": resolution["sequence"],
        "resume_stage": request["resume_stage"],
        "next_action": request["next_action"],
    }
    _create_json(
        run_dir / "interactions" / f"{event['sequence']:04d}-interaction_resume.json",
        record,
    )
    _rebuild_state(run_dir)
    return record


def get_pending_interaction(
    *,
    task_dir: str | Path,
    run_id: str,
) -> dict[str, Any]:
    _, run_dir, _ = _run_context(task_dir, run_id)
    state = _interaction_state(run_dir)
    if state["status"] in {"awaiting_user", "resolved_resume_required"}:
        return state
    return {"status": "none", "last_interaction": state if state["status"] == "resumed" else None}


def record_change_decision(
    *,
    task_dir: str | Path,
    run_id: str,
    verdict: str,
    reason: str,
    projects: list[str],
    evidence: list[str],
    change_scope: str = "",
    blockers: list[str] | None = None,
    allowed_paths: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    task, run_dir, state = _run_context(task_dir, run_id)
    if verdict not in DECISION_VERDICTS:
        raise ValueError(f"invalid change decision verdict: {verdict}")
    if state["evidence"].get("decision_gate") != "ready_for_analysis":
        raise ValueError("change decision requires ready_for_analysis evidence")
    if state["stages"].get("project_mapping") != "completed":
        raise ValueError("change decision requires completed project_mapping")
    if state["stages"].get("analysis") != "completed":
        raise ValueError("change decision requires completed analysis")
    if state["stages"].get("implementation") not in {"pending", "skipped"}:
        raise ValueError(
            "change decision cannot change after implementation starts; create a new run"
        )
    if not reason.strip():
        raise ValueError("change decision reason is required")
    normalized_projects = _checked_nonempty_strings(projects, "projects")
    normalized_evidence = _checked_nonempty_strings(evidence, "evidence")
    normalized_blockers = _checked_strings(blockers or [], "blockers")
    for project in normalized_projects:
        _load_project_record(run_dir, project)
    if verdict == "cannot_change" and not normalized_blockers:
        normalized_blockers = [reason.strip()]
    normalized_allowed_paths: dict[str, list[str]] = {}
    if verdict == "can_change":
        if not isinstance(allowed_paths, dict):
            raise ValueError("can_change requires allowed_paths for every project")
        if set(allowed_paths) != set(normalized_projects):
            raise ValueError("allowed_paths keys must exactly match decision projects")
        for project in normalized_projects:
            normalized_allowed_paths[project] = _checked_relative_paths(
                allowed_paths[project],
                f"allowed_paths[{project}]",
            )
    elif allowed_paths:
        raise ValueError("cannot_change must not declare allowed_paths")

    record = _append_structured_record(
        run_dir=run_dir,
        directory="decisions",
        record_type="change_decision",
        event_type="change_decision_recorded",
        stage="change_decision",
        status=verdict,
        summary=reason.strip(),
        payload={
            "verdict": verdict,
            "reason": reason.strip(),
            "projects": normalized_projects,
            "evidence": normalized_evidence,
            "change_scope": change_scope.strip(),
            "blockers": normalized_blockers,
            "allowed_paths": normalized_allowed_paths,
            "next_action": (
                "create_isolated_worktree"
                if verdict == "can_change"
                else "explain_and_stop"
            ),
        },
    )
    if verdict == "cannot_change":
        record_stage(
            task_dir=task,
            run_id=run_id,
            stage="implementation",
            status="skipped",
            summary="变更判断为 cannot_change，不创建 worktree，不修改代码。",
            details={"decision_sequence": record["sequence"]},
        )
    return record


def create_project_worktree(
    *,
    task_dir: str | Path,
    run_id: str,
    project: str,
    base_ref: str = "",
) -> dict[str, Any]:
    task, run_dir, state = _run_context(task_dir, run_id)
    if state["stages"].get("change_decision") != "can_change":
        raise ValueError("worktree creation requires change_decision=can_change")
    decision = _latest_change_decision(run_dir)
    if (
        decision.get("verdict") != "can_change"
        or project not in decision.get("projects", [])
    ):
        raise ValueError(f"project is outside the latest decision scope: {project}")
    project_record = _load_project_record(run_dir, project)
    original_repo = Path(project_record["repo_path"]).resolve()
    if not original_repo.is_dir():
        raise FileNotFoundError(f"project repository does not exist: {original_repo}")
    top_level = Path(
        _git_text(original_repo, "rev-parse", "--show-toplevel")
    ).resolve()
    if top_level != original_repo:
        raise ValueError(
            f"repo_path must be the Git top-level directory: {top_level}"
        )

    expected_root = task / "worktrees" / run_id
    _ensure_no_symlink_components(expected_root, task)
    target = expected_root / project
    mapped_worktree = str(project_record.get("worktree_path") or "")
    if mapped_worktree and Path(mapped_worktree) != target:
        raise ValueError(
            f"mapped worktree_path must equal deterministic path: {target}"
        )
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"worktree already exists: {target}")

    selected_ref = (
        base_ref.strip()
        or str(project_record.get("base_commit") or "").strip()
        or "HEAD"
    )
    base_commit = _git_text(
        original_repo,
        "rev-parse",
        f"{selected_ref}^{{commit}}",
    )
    base_branch = str(project_record.get("base_branch") or "").strip()
    if not base_branch:
        branch_result = _run_git(
            original_repo,
            "branch",
            "--show-current",
            check=False,
        )
        base_branch = branch_result.stdout.decode("utf-8", "replace").strip()

    target.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        original_repo,
        "worktree",
        "add",
        "--detach",
        str(target),
        base_commit,
    )
    actual_commit = _git_text(target, "rev-parse", "HEAD")
    if actual_commit != base_commit:
        raise RuntimeError("created worktree does not match requested base commit")

    return _append_structured_record(
        run_dir=run_dir,
        directory="changes",
        record_type=f"worktree-{project}",
        event_type="worktree_created",
        stage="implementation",
        status="in_progress",
        summary=f"已为 {project} 创建 detached worktree",
        payload={
            "project": project,
            "original_repo_path": str(original_repo),
            "worktree_path": str(target),
            "base_ref": selected_ref,
            "base_branch": base_branch,
            "base_commit": base_commit,
            "checkout_mode": "detached",
        },
    )


def archive_project_patch(
    *,
    task_dir: str | Path,
    run_id: str,
    project: str,
) -> dict[str, Any]:
    _, run_dir, state = _run_context(task_dir, run_id)
    if state["stages"].get("change_decision") != "can_change":
        raise ValueError("patch archive requires change_decision=can_change")
    decision = _latest_change_decision(run_dir)
    if (
        decision.get("verdict") != "can_change"
        or project not in decision.get("projects", [])
    ):
        raise ValueError(f"project is outside the latest decision scope: {project}")
    worktree_record = _latest_typed_record(
        run_dir / "changes",
        record_type=f"worktree-{project}",
    )
    worktree = Path(worktree_record["worktree_path"]).resolve()
    if not worktree.is_dir():
        raise FileNotFoundError(f"registered worktree does not exist: {worktree}")
    base_commit = str(worktree_record["base_commit"])
    if _git_text(worktree, "rev-parse", "HEAD") != base_commit:
        raise ValueError(
            "worktree HEAD changed; commits require separate authorization and handling"
        )
    status_text = _git_text(worktree, "status", "--porcelain=v1")
    if any(line.startswith("?? ") for line in status_text.splitlines()):
        raise ValueError(
            "untracked files are not archived; add them with intent-to-add first"
        )
    diff_check = _run_git(
        worktree,
        "diff",
        "--check",
        base_commit,
        "--",
        check=False,
    )
    if diff_check.returncode != 0:
        raise ValueError(
            "git diff --check failed: "
            + diff_check.stdout.decode("utf-8", "replace").strip()
        )
    patch = _run_git(
        worktree,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        base_commit,
        "--",
    ).stdout
    if not patch:
        raise ValueError("worktree has no patchable changes")
    change_entries = _git_change_entries(worktree, base_commit)
    changed_files = [
        "\t".join([entry["status"], *entry["paths"]])
        for entry in change_entries
    ]
    changed_paths = [
        path
        for entry in change_entries
        for path in entry["paths"]
    ]
    allowed = set(
        (decision.get("allowed_paths") or {}).get(project) or []
    )
    outside_allowlist = [
        path for path in changed_paths if path not in allowed
    ]
    if outside_allowlist:
        raise ValueError(
            "patch contains paths outside decision allowlist: "
            + ", ".join(outside_allowlist)
        )
    patch_sha256 = hashlib.sha256(patch).hexdigest()
    event = _append_event(
        run_dir,
        event_type="patch_archived",
        stage="implementation",
        status="completed",
        summary=f"已归档 {project} 的本地补丁",
        details={
            "project": project,
            "patch_sha256": patch_sha256,
            "changed_file_count": len(changed_files),
        },
    )
    patch_name = f"{event['sequence']:04d}-{project}.patch"
    patch_path = run_dir / "changes" / patch_name
    _create_bytes(patch_path, patch)
    record = {
        "contract_version": CONTRACT_VERSION,
        "record_type": "project_patch",
        "sequence": event["sequence"],
        "recorded_at": event["recorded_at"],
        "project": project,
        "base_commit": base_commit,
        "worktree_path": str(worktree),
        "patch_relative_path": f"changes/{patch_name}",
        "patch_sha256": patch_sha256,
        "changed_files": changed_files,
        "changed_paths": changed_paths,
        "change_entries": change_entries,
        "diff_check": "passed",
    }
    _create_json(
        run_dir / "changes" / f"{event['sequence']:04d}-patch.json",
        record,
    )
    _append_event(
        run_dir,
        event_type="codex_review_invalidated",
        stage="codex_review",
        status="pending",
        summary=f"{project} 补丁已更新，需要重新评审。",
        details={"patch_sequence": event["sequence"], "project": project},
    )
    _append_event(
        run_dir,
        event_type="verification_invalidated",
        stage="verification",
        status="pending",
        summary=f"{project} 补丁已更新，需要重新验证。",
        details={"patch_sequence": event["sequence"], "project": project},
    )
    _append_event(
        run_dir,
        event_type="apply_back_invalidated",
        stage="apply_back",
        status="pending",
        summary=f"{project} 补丁已更新，禁止复用旧回写结论。",
        details={"patch_sequence": event["sequence"], "project": project},
    )
    _rebuild_state(run_dir)
    return {**record, "patch_path": str(patch_path)}


def record_codex_review(
    *,
    task_dir: str | Path,
    run_id: str,
    verdict: str,
    summary: str,
    can_fix: bool,
    findings: list[dict[str, Any]],
    cannot_fix_reason: str = "",
) -> dict[str, Any]:
    _, run_dir, state = _run_context(task_dir, run_id)
    if state["stages"].get("implementation") != "completed":
        raise ValueError("Codex review requires completed implementation")
    if verdict not in {"passed", "failed"}:
        raise ValueError(f"invalid review verdict: {verdict}")
    if not summary.strip():
        raise ValueError("review summary is required")
    normalized_findings: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"finding {index} must be an object")
        severity = str(finding.get("severity") or "")
        title = str(finding.get("title") or "").strip()
        resolved = finding.get("resolved")
        if severity not in REVIEW_SEVERITIES:
            raise ValueError(f"finding {index} has invalid severity")
        if not title:
            raise ValueError(f"finding {index} title is required")
        if not isinstance(resolved, bool):
            raise ValueError(f"finding {index} resolved must be boolean")
        normalized_findings.append(
            {
                "severity": severity,
                "title": title,
                "resolved": resolved,
                "file": str(finding.get("file") or ""),
                "line": finding.get("line"),
                "details": str(finding.get("details") or ""),
            }
        )
    unresolved_blocking = [
        finding
        for finding in normalized_findings
        if not finding["resolved"]
        and finding["severity"] in {"Critical", "Important"}
    ]
    if verdict == "passed" and unresolved_blocking:
        raise ValueError("review cannot pass with unresolved Critical/Important findings")
    if verdict == "failed" and not normalized_findings:
        raise ValueError("failed review requires at least one finding")
    if verdict == "failed" and not can_fix and not cannot_fix_reason.strip():
        raise ValueError("cannot_fix_reason is required when failed review cannot be fixed")
    decision = _latest_change_decision(run_dir)
    patch_references = _latest_patch_references(run_dir)
    if {
        reference["project"] for reference in patch_references
    } != set(decision.get("projects") or []):
        raise ValueError(
            "review requires one latest patch for every decision project"
        )
    patch_identity = _patch_content_identity(patch_references)
    terminal_review_exists = any(
        record.get("verdict") == "failed"
        and record.get("can_fix") is False
        and _patch_content_identity(record.get("patches") or [])
        == patch_identity
        for record in _typed_records(
            run_dir / "reviews",
            "codex_review",
        )
    )
    if terminal_review_exists:
        raise ValueError(
            "failed review with can_fix=false is terminal for the current patch set"
        )
    next_action = (
        "verify_patch"
        if verdict == "passed"
        else ("fix_and_rereview" if can_fix else "explain_and_stop")
    )
    return _append_structured_record(
        run_dir=run_dir,
        directory="reviews",
        record_type="codex_review",
        event_type="codex_review_recorded",
        stage="codex_review",
        status=verdict,
        summary=summary.strip(),
        payload={
            "verdict": verdict,
            "summary": summary.strip(),
            "can_fix": can_fix,
            "cannot_fix_reason": cannot_fix_reason.strip(),
            "findings": normalized_findings,
            "patches": patch_references,
            "next_action": next_action,
        },
    )


def record_verification(
    *,
    task_dir: str | Path,
    run_id: str,
    status: str,
    summary: str,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    _, run_dir, state = _run_context(task_dir, run_id)
    if state["stages"].get("codex_review") != "passed":
        raise ValueError("verification requires codex_review=passed")
    if status not in {"passed", "failed"}:
        raise ValueError(f"invalid verification status: {status}")
    if not summary.strip():
        raise ValueError("verification summary is required")
    if not checks:
        raise ValueError("verification requires at least one check")
    normalized_checks: list[dict[str, Any]] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ValueError(f"check {index} must be an object")
        name = str(check.get("name") or "").strip()
        command = str(check.get("command") or "").strip()
        exit_code = check.get("exit_code")
        result = str(check.get("result") or "").strip()
        if (
            not name
            or not command
            or type(exit_code) is not int
            or not 0 <= exit_code <= 255
            or not result
        ):
            raise ValueError(
                f"check {index} requires name, command, integer exit_code "
                "between 0 and 255, and result"
            )
        normalized_checks.append(
            {
                "name": name,
                "command": command,
                "exit_code": exit_code,
                "result": result,
            }
        )
    if status == "passed" and any(
        check["exit_code"] != 0 for check in normalized_checks
    ):
        raise ValueError("verification cannot pass with a non-zero check")
    if status == "failed" and all(
        check["exit_code"] == 0 for check in normalized_checks
    ):
        raise ValueError("failed verification requires a failing check")
    patch_references = _latest_patch_references(run_dir)
    review_record = _latest_typed_record(
        run_dir / "reviews",
        record_type="codex_review",
    )
    if (
        review_record.get("verdict") != "passed"
        or review_record.get("patches") != patch_references
    ):
        raise ValueError("verification requires a passed review of the latest patch")
    return _append_structured_record(
        run_dir=run_dir,
        directory="verification",
        record_type="verification",
        event_type="verification_recorded",
        stage="verification",
        status=status,
        summary=summary.strip(),
        payload={
            "status": status,
            "summary": summary.strip(),
            "checks": normalized_checks,
            "patches": patch_references,
            "review_sequence": review_record["sequence"],
            "next_action": (
                "apply_back_if_safe"
                if status == "passed"
                else "fix_and_repeat_review"
            ),
        },
    )


def apply_project_patch(
    *,
    task_dir: str | Path,
    run_id: str,
    project: str,
    ack_local_write: bool,
) -> dict[str, Any]:
    if not ack_local_write:
        raise PermissionError(
            "ack_local_write=True is required before modifying the original repository"
        )
    task, run_dir, state = _run_context(task_dir, run_id)
    history_errors = validate_task(task)
    if history_errors:
        raise ValueError(
            "history validation failed before local write: "
            + "; ".join(history_errors)
        )
    if state["stages"].get("codex_review") != "passed":
        raise ValueError("apply-back requires codex_review=passed")
    if state["stages"].get("verification") != "passed":
        raise ValueError("apply-back requires verification=passed")

    project_record = _load_project_record(run_dir, project)
    patch_record = _latest_project_patch(run_dir, project)
    review_record = _latest_typed_record(
        run_dir / "reviews",
        record_type="codex_review",
    )
    verification_record = _latest_typed_record(
        run_dir / "verification",
        record_type="verification",
    )
    patch_reference = _patch_reference(patch_record)
    patch_paths = _checked_relative_paths(
        patch_record.get("changed_paths") or [],
        f"patch changed_paths[{project}]",
    )
    latest_patch_references = _latest_patch_references(run_dir)
    if (
        review_record.get("verdict") != "passed"
        or review_record.get("patches") != latest_patch_references
        or patch_reference not in latest_patch_references
    ):
        raise ValueError("apply-back requires review of the latest patch")
    if (
        verification_record.get("status") != "passed"
        or verification_record.get("patches") != latest_patch_references
        or int(verification_record.get("review_sequence") or 0)
        != int(review_record["sequence"])
    ):
        raise ValueError("apply-back requires verification of the latest review")
    original_repo = Path(project_record["repo_path"]).resolve()
    patch_path = (run_dir / patch_record["patch_relative_path"]).resolve()
    if not _is_within(patch_path, run_dir.resolve()):
        raise ValueError("patch path escapes run directory")
    if not patch_path.is_file() or patch_path.is_symlink():
        raise FileNotFoundError(f"archived patch is missing: {patch_path}")
    if _sha256_file(patch_path) != patch_record["patch_sha256"]:
        raise ValueError("archived patch SHA-256 mismatch")
    base_commit = str(patch_record["base_commit"])
    current_head = _git_text(original_repo, "rev-parse", "HEAD")
    if current_head != base_commit:
        return _record_apply_back(
            run_dir=run_dir,
            project=project,
            status="blocked",
            summary="原项目 HEAD 已偏离补丁基线，未回写。",
            patch_record=patch_record,
            original_repo=original_repo,
            details={"current_head": current_head, "expected_head": base_commit},
        )

    status_text = _git_text(original_repo, "status", "--porcelain=v1")
    if status_text:
        current_patch = _worktree_patch_snapshot(
            original_repo,
            base_commit,
            force_paths=patch_paths,
            include_paths=patch_paths,
        )
        reverse_check = _run_git(
            original_repo,
            "apply",
            "--reverse",
            "--check",
            str(patch_path),
            check=False,
        )
        if (
            current_patch == patch_path.read_bytes()
            and reverse_check.returncode == 0
        ):
            return _record_apply_back(
                run_dir=run_dir,
                project=project,
                status="applied",
                summary="原项目已包含相同补丁，无需重复回写。",
                patch_record=patch_record,
                original_repo=original_repo,
                details={"apply_result": "already_present"},
            )
        if current_patch:
            return _record_apply_back(
                run_dir=run_dir,
                project=project,
                status="blocked",
                summary="原项目的目标路径存在非本任务改动，未回写。",
                patch_record=patch_record,
                original_repo=original_repo,
                details={"reason": "target_paths_dirty_or_partial"},
            )

    apply_check = _run_git(
        original_repo,
        "apply",
        "--check",
        str(patch_path),
        check=False,
    )
    if apply_check.returncode != 0:
        return _record_apply_back(
            run_dir=run_dir,
            project=project,
            status="blocked",
            summary="补丁无法干净应用到原项目，未回写。",
            patch_record=patch_record,
            original_repo=original_repo,
            details={
                "reason": "git_apply_check_failed",
                "stderr": apply_check.stderr.decode("utf-8", "replace").strip(),
            },
        )
    expected_patch = patch_path.read_bytes()
    _run_git(original_repo, "apply", str(patch_path))
    post_check = _run_git(
        original_repo,
        "diff",
        "--check",
        base_commit,
        "--",
        *patch_paths,
        check=False,
    )
    post_status = _git_text(original_repo, "status", "--porcelain=v1")
    post_patch = _worktree_patch_snapshot(
        original_repo,
        base_commit,
        force_paths=patch_paths,
        include_paths=patch_paths,
    )
    post_apply_exact = (
        post_check.returncode == 0
        and post_patch == expected_patch
    )
    if not post_apply_exact:
        reverse_check = _run_git(
            original_repo,
            "apply",
            "--reverse",
            "--check",
            str(patch_path),
            check=False,
        )
        rollback_status = "not_safe"
        if reverse_check.returncode == 0:
            reverse_apply = _run_git(
                original_repo,
                "apply",
                "--reverse",
                str(patch_path),
                check=False,
            )
            rollback_status = (
                "completed" if reverse_apply.returncode == 0 else "failed"
            )
        return _record_apply_back(
            run_dir=run_dir,
            project=project,
            status="blocked",
            summary=(
                "回写后工作区不再精确等于归档补丁，已撤销本次补丁。"
                if rollback_status == "completed"
                else "回写后工作区不再精确等于归档补丁，无法安全自动撤销。"
            ),
            patch_record=patch_record,
            original_repo=original_repo,
            details={
                "reason": "post_apply_exact_state_mismatch",
                "diff_check_exit_code": post_check.returncode,
                "has_untracked_files": any(
                    line.startswith("?? ") for line in post_status.splitlines()
                ),
                "patch_bytes_match": post_patch == expected_patch,
                "rollback_status": rollback_status,
            },
        )
    return _record_apply_back(
        run_dir=run_dir,
        project=project,
        status="applied",
        summary="补丁已安全回写到原项目的本地工作区。",
        patch_record=patch_record,
        original_repo=original_repo,
        details={
            "apply_result": "applied",
            "unrelated_changes_preserved": bool(status_text),
        },
    )


def reconcile_project_delivery(
    *,
    task_dir: str | Path,
    run_id: str,
    project: str,
    target_ref: str = DELIVERY_TARGET_WORKTREE,
    delivery_stage: str = "workspace",
) -> dict[str, Any]:
    """Record whether a delivery target is byte-for-byte the archived patch.

    This is deliberately read-only.  It never creates a commit, fetches a
    remote, or pushes anything; a remote target is only checked after the
    separately authorized delivery capability has already updated its local
    tracking reference.
    """

    task, run_dir, state = _run_context(task_dir, run_id)
    history_errors = validate_task(task)
    if history_errors:
        raise ValueError(
            "history validation failed before delivery reconciliation: "
            + "; ".join(history_errors)
        )
    if state["stages"].get("apply_back") != "applied":
        raise ValueError("delivery reconciliation requires apply_back=applied")
    if delivery_stage not in DELIVERY_STAGES:
        raise ValueError("invalid delivery stage")
    normalized_target = _checked_delivery_target_ref(target_ref)
    if (
        delivery_stage == "workspace"
        and normalized_target != DELIVERY_TARGET_WORKTREE
    ):
        raise ValueError("workspace delivery reconciliation requires target_ref=WORKTREE")
    if (
        delivery_stage != "workspace"
        and normalized_target == DELIVERY_TARGET_WORKTREE
    ):
        raise ValueError("commit or remote reconciliation requires a target ref")

    project_record = _load_project_record(run_dir, project)
    patch_record = _latest_project_patch(run_dir, project)
    apply_back_record = _latest_apply_back_record(run_dir, project)
    if (
        apply_back_record.get("status") != "applied"
        or apply_back_record.get("patch_sha256") != patch_record.get("patch_sha256")
    ):
        raise ValueError("delivery reconciliation requires the latest applied patch")

    original_repo = Path(project_record["repo_path"]).resolve()
    base_commit = str(patch_record["base_commit"])
    patch_path = (run_dir / str(patch_record["patch_relative_path"])).resolve()
    if not _is_within(patch_path, run_dir.resolve()) or not patch_path.is_file():
        raise ValueError("archived patch is missing")
    expected_patch = patch_path.read_bytes()
    expected_patch_sha256 = hashlib.sha256(expected_patch).hexdigest()
    if expected_patch_sha256 != patch_record.get("patch_sha256"):
        raise ValueError("archived patch SHA-256 mismatch")

    current_head = _git_text(original_repo, "rev-parse", "HEAD")
    target_commit = ""
    reason = ""
    observed_patch = b""
    if delivery_stage == "workspace":
        if current_head != base_commit:
            reason = "workspace_head_mismatch"
        else:
            observed_patch = _worktree_patch_snapshot(
                original_repo,
                base_commit,
                force_paths=[],
            )
            reason = (
                "exact_archived_patch"
                if observed_patch == expected_patch
                else "target_patch_mismatch"
            )
    elif (
        delivery_stage == "remote"
        and not _is_remote_tracking_ref(original_repo, normalized_target)
    ):
        reason = "remote_tracking_ref_unavailable"
    else:
        target = _run_git(
            original_repo,
            "rev-parse",
            "--verify",
            f"{normalized_target}^{{commit}}",
            check=False,
        )
        if target.returncode != 0:
            reason = "target_ref_unresolved"
        else:
            target_commit = target.stdout.decode("utf-8", "replace").strip()
            base_reachable = _run_git(
                original_repo,
                "merge-base",
                "--is-ancestor",
                base_commit,
                target_commit,
                check=False,
            ).returncode == 0
            if not base_reachable:
                reason = "base_not_reachable_from_target"
            else:
                observed_patch = _run_git(
                    original_repo,
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    base_commit,
                    target_commit,
                    "--",
                ).stdout
                reason = (
                    "exact_archived_patch"
                    if observed_patch == expected_patch
                    else "target_patch_mismatch"
                )

    verified = reason == "exact_archived_patch"
    status = (
        "local_workspace_verified"
        if verified and delivery_stage == "workspace"
        else "commit_verified"
        if verified and delivery_stage == "commit"
        else "remote_ref_verified"
        if verified
        else "blocked"
        if reason in {"target_ref_unresolved", "remote_tracking_ref_unavailable"}
        else "mismatch"
    )
    observed_patch_sha256 = hashlib.sha256(observed_patch).hexdigest()
    return _append_structured_record(
        run_dir=run_dir,
        directory="apply-back",
        record_type=f"delivery-reconciliation-{project}",
        event_type="delivery_reconciliation_recorded",
        stage="",
        status="",
        summary=(
            f"{project} 的 {delivery_stage} 交付目标与归档补丁一致。"
            if verified
            else f"{project} 的 {delivery_stage} 交付目标未通过补丁一致性校验：{reason}。"
        ),
        payload={
            "schema_version": "harness-delivery-reconciliation.v1",
            "project": project,
            "delivery_stage": delivery_stage,
            "target_ref": normalized_target,
            "target_commit": target_commit,
            "current_head": current_head,
            "status": status,
            "reason": reason,
            "base_commit": base_commit,
            "patch_relative_path": str(patch_record["patch_relative_path"]),
            "patch_sha256": expected_patch_sha256,
            "observed_patch_sha256": observed_patch_sha256,
            "patch_bytes_match": observed_patch == expected_patch,
            "expected_patch_size_bytes": len(expected_patch),
            "observed_patch_size_bytes": len(observed_patch),
            "remote_actions": False,
        },
    )


def _run_context(
    task_dir: str | Path,
    run_id: str,
) -> tuple[Path, Path, dict[str, Any]]:
    task = _checked_existing_task_dir(task_dir)
    checked_run_id = _checked_run_id(run_id)
    run_dir = task / "runs" / checked_run_id
    if not (run_dir / "run.json").is_file():
        raise FileNotFoundError(f"run does not exist: {run_dir}")
    _ensure_run_directories(run_dir)
    return task, run_dir, _calculate_state(run_dir)


def _checked_strings(values: list[str], label: str) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list")
    normalized: list[str] = []
    for index, value in enumerate(values):
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{label}[{index}] must not be blank")
        normalized.append(text)
    return normalized


def _checked_nonempty_strings(values: list[str], label: str) -> list[str]:
    normalized = _checked_strings(values, label)
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _checked_relative_paths(values: list[str], label: str) -> list[str]:
    normalized = _checked_nonempty_strings(values, label)
    result: list[str] = []
    for index, value in enumerate(normalized):
        parsed = PurePosixPath(value)
        if parsed.is_absolute() or ".." in parsed.parts or value.endswith("/"):
            raise ValueError(f"{label}[{index}] must be a safe relative file path")
        canonical = str(parsed)
        if canonical in {"", "."}:
            raise ValueError(f"{label}[{index}] must identify a file")
        result.append(canonical)
    if len(set(result)) != len(result):
        raise ValueError(f"{label} contains duplicate paths")
    return result


def _checked_interaction_id(value: str) -> str:
    interaction_id = str(value or "").strip()
    if not INTERACTION_ID_PATTERN.fullmatch(interaction_id):
        raise ValueError(f"invalid interaction_id: {value}")
    return interaction_id


def _interaction_state(run_dir: Path) -> dict[str, Any]:
    state: dict[str, Any] = {"status": "none"}
    for record in _all_json_records(run_dir / "interactions"):
        record_type = record.get("record_type")
        if record_type == "interaction_request":
            state = {
                "status": "awaiting_user",
                "interaction_id": record.get("interaction_id"),
                "resume_stage": record.get("resume_stage"),
                "next_action": record.get("next_action"),
                "request": record,
                "resolution": None,
            }
        elif (
            record_type == "interaction_resolution"
            and state.get("status") == "awaiting_user"
            and record.get("interaction_id") == state.get("interaction_id")
        ):
            state = {
                **state,
                "status": "resolved_resume_required",
                "resolution": record,
            }
        elif (
            record_type == "interaction_resume"
            and state.get("status") == "resolved_resume_required"
            and record.get("interaction_id") == state.get("interaction_id")
        ):
            state = {
                **state,
                "status": "resumed",
                "resume": record,
            }
    return state


def _load_project_record(run_dir: Path, project: str) -> dict[str, Any]:
    if not PROJECT_NAME_PATTERN.fullmatch(project):
        raise ValueError(f"invalid project name: {project}")
    path = run_dir / "projects" / f"{project}.json"
    if not path.is_file():
        raise FileNotFoundError(f"project mapping does not exist: {project}")
    record = _load_json(path)
    if record.get("name") != project:
        raise ValueError(f"project mapping name mismatch: {path}")
    return record


def _latest_change_decision(run_dir: Path) -> dict[str, Any]:
    return _latest_typed_record(
        run_dir / "decisions",
        record_type="change_decision",
    )


def _append_structured_record(
    *,
    run_dir: Path,
    directory: str,
    record_type: str,
    event_type: str,
    stage: str,
    status: str,
    summary: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    event = _append_event(
        run_dir,
        event_type=event_type,
        stage=stage,
        status=status,
        summary=summary,
        details={
            key: value
            for key, value in payload.items()
            if key
            in {
                "project",
                "verdict",
                "next_action",
                "patch_sha256",
                "status",
            }
        },
    )
    record = {
        "contract_version": CONTRACT_VERSION,
        "record_type": record_type,
        "sequence": event["sequence"],
        "recorded_at": event["recorded_at"],
        **payload,
    }
    target_dir = run_dir / directory
    target_dir.mkdir(exist_ok=True)
    _create_json(
        target_dir / f"{event['sequence']:04d}-{record_type}.json",
        record,
    )
    _rebuild_state(run_dir)
    return record


def _latest_typed_record(
    directory: Path,
    *,
    record_type: str,
) -> dict[str, Any]:
    matches = _typed_records(directory, record_type)
    if not matches:
        raise FileNotFoundError(f"record does not exist: {record_type}")
    return matches[-1]


def _latest_project_patch(run_dir: Path, project: str) -> dict[str, Any]:
    matches = [
        record
        for record in _typed_records(
            run_dir / "changes",
            "project_patch",
        )
        if record.get("project") == project
    ]
    if not matches:
        raise FileNotFoundError(f"archived patch does not exist: {project}")
    return matches[-1]


def _latest_apply_back_record(run_dir: Path, project: str) -> dict[str, Any]:
    record_type = f"apply-back-{project}"
    matches = [
        record
        for record in _all_json_records(run_dir / "apply-back")
        if record.get("record_type") == record_type
    ]
    if not matches:
        raise FileNotFoundError(f"apply-back record does not exist: {project}")
    return matches[-1]


def _latest_patch_references(run_dir: Path) -> list[dict[str, Any]]:
    latest_by_project: dict[str, dict[str, Any]] = {}
    for record in _typed_records(run_dir / "changes", "project_patch"):
        project = str(record.get("project") or "")
        if not project:
            raise ValueError("patch record project is missing")
        latest_by_project[project] = record
    if not latest_by_project:
        raise FileNotFoundError("no archived project patches exist")
    return [
        _patch_reference(latest_by_project[project])
        for project in sorted(latest_by_project)
    ]


def _patch_reference(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": str(record["project"]),
        "sequence": int(record["sequence"]),
        "patch_sha256": str(record["patch_sha256"]),
    }


def _patch_content_identity(
    patch_references: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return sorted(
        [
            {
                "project": str(reference.get("project") or ""),
                "patch_sha256": str(reference.get("patch_sha256") or ""),
            }
            for reference in patch_references
        ],
        key=lambda item: (item["project"], item["patch_sha256"]),
    )


def _git_change_entries(
    repo: Path,
    base_commit: str,
) -> list[dict[str, Any]]:
    raw = _run_git(
        repo,
        "diff",
        "--name-status",
        "-z",
        base_commit,
        "--",
    ).stdout
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    entries: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        status = os.fsdecode(tokens[index])
        index += 1
        path_count = 2 if status[:1] in {"R", "C"} else 1
        if not status or index + path_count > len(tokens):
            raise ValueError("git name-status output is malformed")
        paths = [
            os.fsdecode(tokens[index + offset])
            for offset in range(path_count)
        ]
        index += path_count
        entries.append({"status": status, "paths": paths})
    return entries


def _checked_delivery_target_ref(value: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("invalid delivery target ref")
    if value == DELIVERY_TARGET_WORKTREE:
        return value
    if (
        not DELIVERY_REF_PATTERN.fullmatch(value)
        or ".." in value
        or "//" in value
        or value.endswith("/")
    ):
        raise ValueError("invalid delivery target ref")
    return value


def _checked_record_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_remote_tracking_ref(repo: Path, target_ref: str) -> bool:
    result = _run_git(
        repo,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/remotes",
        check=False,
    )
    if result.returncode != 0:
        return False
    return target_ref in {
        line.strip()
        for line in result.stdout.decode("utf-8", "replace").splitlines()
        if line.strip()
    }


def _record_apply_back(
    *,
    run_dir: Path,
    project: str,
    status: str,
    summary: str,
    patch_record: dict[str, Any],
    original_repo: Path,
    details: dict[str, Any],
) -> dict[str, Any]:
    return _append_structured_record(
        run_dir=run_dir,
        directory="apply-back",
        record_type=f"apply-back-{project}",
        event_type="apply_back_recorded",
        stage="apply_back",
        status=status,
        summary=summary,
        payload={
            "project": project,
            "status": status,
            "summary": summary,
            "original_repo_path": str(original_repo),
            "base_commit": patch_record["base_commit"],
            "patch_relative_path": patch_record["patch_relative_path"],
            "patch_sha256": patch_record["patch_sha256"],
            "details": details,
        },
    )


def _run_git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"git {' '.join(args)} failed with {result.returncode}: {stderr}"
        )
    return result


def _git_text(repo: Path, *args: str) -> str:
    return _run_git(repo, *args).stdout.decode("utf-8", "replace").strip()


def _worktree_patch_snapshot(
    repo: Path,
    base_commit: str,
    *,
    force_paths: list[str],
    include_paths: list[str] | None = None,
) -> bytes:
    with tempfile.TemporaryDirectory(
        prefix="harness-history-index-"
    ) as temporary_dir:
        index_path = Path(temporary_dir) / "index"
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index_path)
        _run_git_with_environment(
            repo,
            environment,
            "read-tree",
            base_commit,
        )
        _run_git_with_environment(
            repo,
            environment,
            "add",
            "-A",
            "--",
        )
        stageable_force_paths = [
            path
            for path in force_paths
            if os.path.lexists(repo / path)
            or _run_git(
                repo,
                "cat-file",
                "-e",
                f"{base_commit}:{path}",
                check=False,
            ).returncode
            == 0
        ]
        if stageable_force_paths:
            _run_git_with_environment(
                repo,
                environment,
                "add",
                "-A",
                "-f",
                "--",
                *stageable_force_paths,
            )
        diff_args = [
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            base_commit,
            "--",
        ]
        if include_paths:
            diff_args.extend(include_paths)
        return _run_git_with_environment(
            repo,
            environment,
            *diff_args,
        ).stdout


def _run_git_with_environment(
    repo: Path,
    environment: dict[str, str],
    *args: str,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        env=environment,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"git {' '.join(args)} failed with {result.returncode}: {stderr}"
        )
    return result


def validate_task(task_dir: str | Path) -> list[str]:
    errors: list[str] = []
    try:
        task = _checked_existing_task_dir(task_dir)
    except (ValueError, FileNotFoundError) as exc:
        return [str(exc)]
    symlinks: list[Path] = []
    for metadata_root in (task / "evidence", task / "runs"):
        if metadata_root.is_symlink():
            symlinks.append(metadata_root)
        elif metadata_root.is_dir():
            symlinks.extend(
                path for path in metadata_root.rglob("*") if path.is_symlink()
            )
    worktrees_root = task / "worktrees"
    if worktrees_root.is_symlink():
        symlinks.append(worktrees_root)
    elif worktrees_root.is_dir():
        for run_worktree_root in worktrees_root.iterdir():
            if run_worktree_root.is_symlink():
                symlinks.append(run_worktree_root)
                continue
            if run_worktree_root.is_dir():
                symlinks.extend(
                    path
                    for path in run_worktree_root.iterdir()
                    if path.is_symlink()
                )
    if symlinks:
        errors.extend(
            f"symlink is forbidden in task history: {path.relative_to(task)}"
            for path in symlinks
        )
    try:
        task_record = _load_json(task / "task.json")
    except (OSError, ValueError) as exc:
        return [f"task.json: {exc}"]

    ticket_id = _task_id_from_dir(task)
    if task_record.get("contract_version") != CONTRACT_VERSION:
        errors.append("task.json contract_version is invalid")
    if task_record.get("ticket_id") != ticket_id:
        errors.append("task.json ticket_id does not match directory")

    runs_dir = task / "runs"
    if not runs_dir.is_dir():
        return errors + ["runs directory is missing"]
    run_names = {
        path.name for path in runs_dir.iterdir() if path.is_dir()
    }
    revision_root = task / "evidence" / "revisions"
    revision_names = (
        {path.name for path in revision_root.iterdir() if path.is_dir()}
        if revision_root.is_dir()
        else set()
    )
    worktree_root = task / "worktrees"
    worktree_names = (
        {
            path.name
            for path in worktree_root.iterdir()
            if path.is_dir() or path.is_symlink()
        }
        if worktree_root.is_dir()
        else set()
    )
    if run_names != revision_names:
        errors.append("run and evidence revision sets do not match")
    if run_names != worktree_names:
        errors.append("run and worktree batch sets do not match")

    for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
        try:
            run_id = _checked_run_id(run_dir.name)
            run_record = _load_json(run_dir / "run.json")
            manifest = _load_json(run_dir / "evidence-manifest.json")
        except (OSError, ValueError) as exc:
            errors.append(f"{run_dir.name}: {exc}")
            continue
        if run_record.get("ticket_id") != ticket_id:
            errors.append(f"{run_id}: run ticket_id mismatch")
        if run_record.get("provider") != task_record.get("provider"):
            errors.append(f"{run_id}: run provider mismatch")
        if run_record.get("run_id") != run_id:
            errors.append(f"{run_id}: run_id mismatch")
        intake_policy = run_record.get("intake_policy", "legacy_optional")
        if intake_policy not in {"required", "legacy_optional"}:
            errors.append(f"{run_id}: intake_policy is invalid")
        legacy_marker_path = run_dir / "legacy-import.json"
        legacy_marker: dict[str, Any] | None = None
        if legacy_marker_path.is_file():
            try:
                legacy_marker = _load_json(legacy_marker_path)
            except (OSError, ValueError) as exc:
                errors.append(f"{run_id}: legacy marker is invalid: {exc}")
            else:
                if (
                    legacy_marker.get("contract_version") != CONTRACT_VERSION
                    or legacy_marker.get("migration_type")
                    != "legacy_missing_evidence_event"
                    or legacy_marker.get("evidence_json_sha256")
                    != manifest.get("evidence_json_sha256")
                ):
                    errors.append(
                        f"{run_id}: legacy marker fields are invalid"
                    )
        errors.extend(
            f"{run_id}: {message}"
            for message in _validate_event_ledger(
                run_dir,
                allow_legacy_missing_evidence_event=bool(legacy_marker),
            )
        )
        try:
            stored_state = _load_json(run_dir / "run-state.json")
            calculated_state = _calculate_state(run_dir, run_record)
            if stored_state != calculated_state:
                errors.append(f"{run_id}: run-state.json is stale or inconsistent")
        except (OSError, ValueError) as exc:
            errors.append(f"{run_id}: run-state.json missing or invalid: {exc}")
        revision_dir = task / "evidence" / "revisions" / run_id
        try:
            evidence = _load_json(revision_dir / "requirement_evidence.v2.json")
        except (OSError, ValueError) as exc:
            errors.append(f"{run_id}: evidence missing or invalid: {exc}")
            continue
        if not (revision_dir / "requirement_evidence.v2.md").is_file():
            errors.append(
                f"{run_id}: requirement_evidence.v2.md is missing"
            )
        errors.extend(
            f"{run_id}: {message}"
            for message in _validate_evidence_package(revision_dir, evidence)
        )
        current_json_sha = _sha256_file(
            revision_dir / "requirement_evidence.v2.json"
        )
        if manifest.get("evidence_json_sha256") != current_json_sha:
            errors.append(f"{run_id}: evidence JSON SHA-256 mismatch")
        expected_manifest = {
            "contract_version": CONTRACT_VERSION,
            "provider": task_record.get("provider"),
            "ticket_id": ticket_id,
            "run_id": run_id,
            "evidence_revision": run_id,
            "evidence_content_sha256": str(
                (evidence.get("integrity") or {}).get("evidence_sha256") or ""
            ),
            "verified_files": _successful_file_count(evidence),
        }
        for field, expected in expected_manifest.items():
            if manifest.get(field) != expected:
                errors.append(
                    f"{run_id}: manifest {field} mismatch"
                )
        run_evidence = run_record.get("evidence") or {}
        if run_evidence.get("json_sha256") != current_json_sha:
            errors.append(f"{run_id}: run evidence JSON SHA-256 mismatch")
        evidence_gate = str(
            (evidence.get("decision_gate") or {}).get("state") or ""
        )
        evidence_completeness = str(
            (evidence.get("completeness") or {}).get("status") or ""
        )
        if run_evidence.get("decision_gate") != evidence_gate:
            errors.append(f"{run_id}: run evidence decision_gate mismatch")
        if run_evidence.get("completeness") != evidence_completeness:
            errors.append(f"{run_id}: run evidence completeness mismatch")
        intake_dir = run_dir / "intake"
        intake_path = run_dir / "intake" / "request.json"
        if intake_dir.is_dir() and not intake_path.is_file():
            errors.append(f"{run_id}: intake request is missing")
        if intake_policy == "required" and not intake_path.is_file():
            errors.append(f"{run_id}: required intake request is missing")
        if intake_path.exists():
            try:
                intake = _load_json(intake_path)
            except (OSError, ValueError) as exc:
                errors.append(f"{run_id}: intake request is invalid: {exc}")
            else:
                errors.extend(
                    f"{run_id}: {message}"
                    for message in _validate_intake_record(
                        intake=intake,
                        provider=str(task_record.get("provider") or ""),
                        ticket_id=ticket_id,
                        run_id=run_id,
                        decision_gate=str(
                            run_evidence.get("decision_gate") or ""
                        ),
                        completeness=str(
                            run_evidence.get("completeness") or ""
                        ),
                    )
                )
        if not (task / "worktrees" / run_id).is_dir():
            errors.append(f"{run_id}: worktree directory is missing")
        projects_dir = run_dir / "projects"
        for project_json in sorted(projects_dir.glob("*.json")):
            try:
                project = _load_json(project_json)
            except (OSError, ValueError) as exc:
                errors.append(f"{run_id}/{project_json.name}: {exc}")
                continue
            if project.get("event_type") == "stage_recorded":
                continue
            if project.get("role") not in PROJECT_ROLES:
                errors.append(f"{run_id}/{project_json.name}: invalid project role")
            if not Path(str(project.get("repo_path") or "")).is_absolute():
                errors.append(f"{run_id}/{project_json.name}: repo_path must be absolute")
            commits = project.get("historical_commits")
            if not isinstance(commits, list) or any(
                not isinstance(commit, str)
                or not re.fullmatch(r"[a-f0-9]{7,64}", commit)
                for commit in commits
            ):
                errors.append(
                    f"{run_id}/{project_json.name}: historical_commits are invalid"
                )
            if not project_json.with_suffix(".md").is_file():
                errors.append(f"{run_id}/{project_json.name}: Markdown summary is missing")
        changes_dir = run_dir / "changes"
        for change_json in sorted(changes_dir.glob("*.json")):
            try:
                change = _load_json(change_json)
            except (OSError, ValueError) as exc:
                errors.append(f"{run_id}/{change_json.name}: {exc}")
                continue
            record_type = str(change.get("record_type") or "")
            if record_type.startswith("worktree-"):
                worktree_path = Path(str(change.get("worktree_path") or ""))
                expected_worktree_root = task / "worktrees" / run_id
                if (
                    not worktree_path.is_absolute()
                    or ".." in worktree_path.parts
                    or not _is_within_lexical(
                        worktree_path,
                        expected_worktree_root,
                    )
                ):
                    errors.append(
                        f"{run_id}/{change_json.name}: worktree path escapes run"
                    )
                if change.get("checkout_mode") != "detached":
                    errors.append(
                        f"{run_id}/{change_json.name}: worktree must be detached"
                    )
                if not re.fullmatch(
                    r"[a-f0-9]{7,64}",
                    str(change.get("base_commit") or ""),
                ):
                    errors.append(
                        f"{run_id}/{change_json.name}: base_commit is invalid"
                    )
            if record_type == "project_patch":
                relative = change.get("patch_relative_path")
                parsed = (
                    PurePosixPath(relative)
                    if isinstance(relative, str)
                    else None
                )
                if (
                    not parsed
                    or parsed.is_absolute()
                    or ".." in parsed.parts
                ):
                    errors.append(
                        f"{run_id}/{change_json.name}: patch path is invalid"
                    )
                    continue
                patch_path = (
                    run_dir / Path(*parsed.parts)
                ).resolve(strict=False)
                if not _is_within(patch_path, run_dir.resolve()):
                    errors.append(
                        f"{run_id}/{change_json.name}: patch path escapes run"
                    )
                    continue
                if not patch_path.is_file() or patch_path.is_symlink():
                    errors.append(
                        f"{run_id}/{change_json.name}: archived patch is missing"
                    )
                    continue
                if change.get("patch_sha256") != _sha256_file(patch_path):
                    errors.append(
                        f"{run_id}/{change_json.name}: "
                        "archived patch SHA-256 mismatch"
                    )
        try:
            calculated_state = _calculate_state(run_dir, run_record)
        except (OSError, ValueError) as exc:
            errors.append(f"{run_id}: structured state is invalid: {exc}")
        else:
            try:
                structured_errors = _validate_structured_records(
                    run_dir,
                    calculated_state,
                )
            except (OSError, ValueError) as exc:
                errors.append(
                    f"{run_id}: structured record is invalid: {exc}"
                )
            else:
                errors.extend(
                    f"{run_id}: {message}"
                    for message in structured_errors
                )
    return errors


def _validate_intake_record(
    *,
    intake: dict[str, Any],
    provider: str,
    ticket_id: str,
    run_id: str,
    decision_gate: str,
    completeness: str,
) -> list[str]:
    errors: list[str] = []
    expected_fields = {
        "contract_version": "harness-intake.v1",
        "provider": provider,
        "ticket_id": ticket_id,
        "run_id": run_id,
        "decision_gate": decision_gate,
        "completeness": completeness,
    }
    for field, expected in expected_fields.items():
        if intake.get(field) != expected:
            errors.append(f"intake {field} mismatch")
    adapter_skill = intake.get("adapter_skill")
    if provider == "YUNXIAO" and adapter_skill not in {
        "yunxiao-workitem-read",
        # Preserve validation of immutable runs created before plugin routing.
        "yunxiao-workitem-evidence",
    }:
        errors.append("intake adapter_skill mismatch")

    source = str(intake.get("source") or "").strip()
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme or parsed.netloc:
        host = (parsed.hostname or "").lower()
        source_ticket = TASK_ID_PATTERN.search(parsed.path.upper())
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme.lower() != "https"
            or host != "devops.aliyun.com"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or not source_ticket
            or source_ticket.group(0) != ticket_id
        ):
            errors.append("intake source is not a sanitized official URL")
    elif source != ticket_id:
        errors.append("intake source must be the exact ticket ID")

    accepted = decision_gate == "ready_for_analysis"
    expected_status = "accepted" if accepted else "blocked"
    expected_action = (
        "start_readonly_analysis"
        if accepted
        else "complete_or_confirm_requirement_evidence"
    )
    if intake.get("intake_status") != expected_status:
        errors.append("intake intake_status mismatch")
    if intake.get("next_action") != expected_action:
        errors.append("intake next_action mismatch")
    if not str(intake.get("requested_at") or "").strip():
        errors.append("intake requested_at is missing")
    return errors


def _validate_event_ledger(
    run_dir: Path,
    *,
    allow_legacy_missing_evidence_event: bool,
) -> list[str]:
    errors: list[str] = []
    events_dir = run_dir / "events"
    if not events_dir.is_dir() or events_dir.is_symlink():
        return ["events ledger is missing or is a symlink"]
    events: list[dict[str, Any]] = []
    sequences: list[int] = []
    for path in sorted(events_dir.glob("*.json")):
        match = re.fullmatch(r"(\d{4,})-([a-z][a-z0-9_]*)\.json", path.name)
        if not match:
            errors.append(f"event filename is invalid: {path.name}")
            continue
        try:
            event = _load_json(path)
        except (OSError, ValueError) as exc:
            errors.append(f"event is invalid {path.name}: {exc}")
            continue
        filename_sequence = int(match.group(1))
        filename_type = match.group(2)
        if event.get("contract_version") != CONTRACT_VERSION:
            errors.append(f"event contract is invalid: {path.name}")
        if event.get("sequence") != filename_sequence:
            errors.append(f"event sequence mismatches filename: {path.name}")
        if event.get("event_type") != filename_type:
            errors.append(f"event type mismatches filename: {path.name}")
        stage = str(event.get("stage") or "")
        status = str(event.get("status") or "")
        if stage:
            if stage not in STAGE_STATUSES or status not in STAGE_STATUSES[stage]:
                errors.append(f"event stage/status is invalid: {path.name}")
        elif status:
            errors.append(f"event status without stage: {path.name}")
        sequences.append(filename_sequence)
        events.append(event)

    if not events:
        errors.append("evidence_archived event is missing")
        return errors
    if len(sequences) != len(set(sequences)):
        errors.append("event sequences contain duplicates")
    if sorted(set(sequences)) != list(range(1, max(sequences) + 1)):
        errors.append("event sequences are not continuous from 1")
    if not any(event.get("event_type") == "evidence_archived" for event in events):
        if not allow_legacy_missing_evidence_event:
            errors.append("evidence_archived event is missing")

    record_rules = {
        "change_decision_recorded": ("decisions", "change_decision"),
        "worktree_created": ("changes", "worktree-"),
        "patch_archived": ("changes", "project_patch"),
        "codex_review_recorded": ("reviews", "codex_review"),
        "verification_recorded": ("verification", "verification"),
        "apply_back_recorded": ("apply-back", "apply-back-"),
        "delivery_reconciliation_recorded": (
            "apply-back",
            "delivery-reconciliation-",
        ),
        "interaction_requested": ("interactions", "interaction_request"),
        "interaction_resolved": ("interactions", "interaction_resolution"),
        "interaction_resumed": ("interactions", "interaction_resume"),
    }
    for event in events:
        sequence = int(event.get("sequence") or 0)
        event_type = str(event.get("event_type") or "")
        if event_type == "stage_recorded":
            stage = str(event.get("stage") or "")
            record_dir = run_dir / STAGE_DIRECTORIES.get(stage, "stage-records")
            candidates = list(record_dir.glob(f"{sequence:04d}-*.json"))
            if (
                not candidates
                and allow_legacy_missing_evidence_event
                and stage == "project_mapping"
            ):
                candidates = list(
                    (run_dir / "projects").glob(f"{sequence:04d}-*.json")
                )
            if not candidates:
                errors.append(
                    f"stage_recorded event {sequence} has no stage record"
                )
        elif event_type == "project_mapped":
            project = str((event.get("details") or {}).get("project") or "")
            if not project or not (run_dir / "projects" / f"{project}.json").is_file():
                errors.append(
                    f"project_mapped event {sequence} has no project record"
                )
        elif event_type in record_rules:
            directory, record_type_prefix = record_rules[event_type]
            candidates = list(
                (run_dir / directory).glob(f"{sequence:04d}-*.json")
            )
            matched = False
            for path in candidates:
                try:
                    candidate = _load_json(path)
                except (OSError, ValueError) as exc:
                    errors.append(
                        f"record for {event_type} event {sequence} is invalid: {exc}"
                    )
                    continue
                if str(candidate.get("record_type") or "").startswith(
                    record_type_prefix
                ):
                    matched = True
            if not matched:
                errors.append(
                    f"{event_type} event {sequence} has no matching record"
                )
    return errors


def _validate_structured_records(
    run_dir: Path,
    state: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    errors.extend(_validate_interaction_records(run_dir))
    decisions = _typed_records(run_dir / "decisions", "change_decision")
    for record in decisions:
        verdict = record.get("verdict")
        projects = record.get("projects")
        evidence = record.get("evidence")
        allowed_paths = record.get("allowed_paths")
        if verdict not in DECISION_VERDICTS:
            errors.append("change decision verdict is invalid")
            continue
        if not isinstance(projects, list) or not projects:
            errors.append("change decision projects are invalid")
        if not isinstance(evidence, list) or not evidence:
            errors.append("change decision evidence is invalid")
        if verdict == "can_change":
            if not isinstance(allowed_paths, dict) or set(allowed_paths) != set(
                projects or []
            ):
                errors.append("change decision allowed_paths are invalid")
            else:
                for project, paths in allowed_paths.items():
                    try:
                        _checked_relative_paths(
                            paths,
                            f"allowed_paths[{project}]",
                        )
                    except ValueError as exc:
                        errors.append(str(exc))
        elif allowed_paths:
            errors.append("cannot_change decision has allowed_paths")

    reviews = _typed_records(run_dir / "reviews", "codex_review")
    for record in reviews:
        if record.get("verdict") not in {"passed", "failed"}:
            errors.append("Codex review verdict is invalid")
        if type(record.get("can_fix")) is not bool:
            errors.append("Codex review can_fix is invalid")
        if not isinstance(record.get("patches"), list) or not record.get("patches"):
            errors.append("Codex review patch references are invalid")
        findings = record.get("findings")
        if not isinstance(findings, list):
            errors.append("Codex review findings are invalid")
            continue
        unresolved_blocking = False
        for finding in findings:
            if (
                not isinstance(finding, dict)
                or finding.get("severity") not in REVIEW_SEVERITIES
                or not str(finding.get("title") or "").strip()
                or type(finding.get("resolved")) is not bool
            ):
                errors.append("Codex review finding is invalid")
                continue
            if (
                not finding["resolved"]
                and finding["severity"] in {"Critical", "Important"}
            ):
                unresolved_blocking = True
        if record.get("verdict") == "passed" and unresolved_blocking:
            errors.append("passed Codex review has unresolved blocking findings")
        if (
            record.get("verdict") == "failed"
            and record.get("can_fix") is False
            and not str(record.get("cannot_fix_reason") or "").strip()
        ):
            errors.append("non-fixable review has no reason")

    verifications = _typed_records(
        run_dir / "verification",
        "verification",
    )
    for record in verifications:
        checks = record.get("checks")
        if record.get("status") not in {"passed", "failed"}:
            errors.append("verification status is invalid")
        if not isinstance(checks, list) or not checks:
            errors.append("verification checks are invalid")
            continue
        exit_codes: list[int] = []
        for check in checks:
            exit_code = check.get("exit_code") if isinstance(check, dict) else None
            if (
                not isinstance(check, dict)
                or not str(check.get("name") or "").strip()
                or not str(check.get("command") or "").strip()
                or type(exit_code) is not int
                or not 0 <= exit_code <= 255
                or not str(check.get("result") or "").strip()
            ):
                errors.append("verification check is invalid")
                continue
            exit_codes.append(exit_code)
        if record.get("status") == "passed" and any(
            exit_code != 0 for exit_code in exit_codes
        ):
            errors.append("passed verification has a failing check")
        if record.get("status") == "failed" and exit_codes and all(
            exit_code == 0 for exit_code in exit_codes
        ):
            errors.append("failed verification has no failing check")

    stages = state.get("stages") or {}
    terminal_requirements = {
        "change_decision": (DECISION_VERDICTS, decisions),
        "codex_review": ({"passed", "failed"}, reviews),
        "verification": ({"passed", "failed"}, verifications),
    }
    for stage, (terminal_statuses, records) in terminal_requirements.items():
        if stages.get(stage) in terminal_statuses and not records:
            errors.append(f"{stage} terminal state has no structured record")
    if stages.get("implementation") == "completed" and not _typed_records(
        run_dir / "changes",
        "project_patch",
    ):
        errors.append("completed implementation has no patch record")
    if stages.get("apply_back") == "applied" and not any(
        str(record.get("record_type") or "").startswith("apply-back-")
        for record in _all_json_records(run_dir / "apply-back")
    ):
        errors.append("applied state has no apply-back record")
    for record in _all_json_records(run_dir / "apply-back"):
        if not str(record.get("record_type") or "").startswith(
            "delivery-reconciliation-"
        ):
            continue
        if (
            record.get("schema_version")
            != "harness-delivery-reconciliation.v1"
            or record.get("delivery_stage") not in DELIVERY_STAGES
            or record.get("status")
            not in {
                "local_workspace_verified",
                "commit_verified",
                "remote_ref_verified",
                "blocked",
                "mismatch",
            }
            or not _checked_record_sha256(record.get("patch_sha256"))
            or not _checked_record_sha256(record.get("observed_patch_sha256"))
            or type(record.get("patch_bytes_match")) is not bool
            or type(record.get("remote_actions")) is not bool
            or not str(record.get("reason") or "").strip()
        ):
            errors.append("delivery reconciliation record is invalid")
            continue
        if record.get("status") in {
            "local_workspace_verified",
            "commit_verified",
            "remote_ref_verified",
        } and (
            record.get("reason") != "exact_archived_patch"
            or record.get("patch_bytes_match") is not True
            or record.get("patch_sha256") != record.get("observed_patch_sha256")
        ):
            errors.append("verified delivery reconciliation is inconsistent")
    return errors


def _validate_interaction_records(run_dir: Path) -> list[str]:
    errors: list[str] = []
    open_request: dict[str, Any] | None = None
    resolution: dict[str, Any] | None = None
    seen_ids: set[str] = set()
    for record in _all_json_records(run_dir / "interactions"):
        record_type = record.get("record_type")
        interaction_id = str(record.get("interaction_id") or "")
        if record_type == "interaction_request":
            if open_request is not None and resolution is None:
                errors.append("interaction request overlaps an unresolved interaction")
            if open_request is not None and resolution is not None:
                errors.append("interaction request overlaps an interaction waiting to resume")
            if not INTERACTION_ID_PATTERN.fullmatch(interaction_id) or interaction_id in seen_ids:
                errors.append("interaction request id is invalid or duplicated")
            seen_ids.add(interaction_id)
            if (
                record.get("kind") not in INTERACTION_KINDS
                or not str(record.get("question") or "").strip()
                or record.get("resume_stage") not in STAGE_STATUSES
                or not str(record.get("next_action") or "").strip()
                or not isinstance(record.get("options"), list)
                or any(not str(item or "").strip() for item in record.get("options") or [])
            ):
                errors.append("interaction request fields are invalid")
            open_request = record
            resolution = None
        elif record_type == "interaction_resolution":
            if (
                open_request is None
                or resolution is not None
                or interaction_id != open_request.get("interaction_id")
                or record.get("request_sequence") != open_request.get("sequence")
                or not str(record.get("answer") or "").strip()
                or record.get("resume_stage") != open_request.get("resume_stage")
                or record.get("next_action") != open_request.get("next_action")
                or record.get("auto_resume_required") is not True
            ):
                errors.append("interaction resolution is invalid")
            else:
                resolution = record
        elif record_type == "interaction_resume":
            if (
                open_request is None
                or resolution is None
                or interaction_id != open_request.get("interaction_id")
                or record.get("request_sequence") != open_request.get("sequence")
                or record.get("resolution_sequence") != resolution.get("sequence")
                or record.get("resume_stage") != open_request.get("resume_stage")
                or record.get("next_action") != open_request.get("next_action")
            ):
                errors.append("interaction resume is invalid")
            open_request = None
            resolution = None
        else:
            errors.append("interaction record type is invalid")
    return errors


def _all_json_records(directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        records.append(_load_json(path))
    return sorted(records, key=lambda item: int(item.get("sequence") or 0))


def _typed_records(directory: Path, record_type: str) -> list[dict[str, Any]]:
    return [
        record
        for record in _all_json_records(directory)
        if record.get("record_type") == record_type
    ]


def _checked_evidence_source(source_dir: str | Path) -> Path:
    source = Path(source_dir)
    if not source.is_absolute():
        source = Path.cwd() / source
    current = Path(source.anchor)
    try:
        metadata = os.lstat(current)
    except OSError as exc:
        raise FileNotFoundError(
            f"evidence source directory does not exist: {source}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"evidence source path must not contain symlinks: {current}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"evidence source parent must be a directory: {current}")
    for part in source.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise FileNotFoundError(
                f"evidence source directory does not exist: {source}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(
                f"evidence source path must not contain symlinks: {current}"
            )
        if current != source and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(
                f"evidence source parent must be a directory: {current}"
            )
    if current != source or not stat.S_ISDIR(metadata.st_mode):
        raise FileNotFoundError(f"evidence source directory does not exist: {source}")
    source = source.resolve()
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"evidence package must not contain symlinks: {path}")
    for required in ("requirement_evidence.v2.json", "requirement_evidence.v2.md"):
        if not (source / required).is_file():
            raise FileNotFoundError(f"evidence package is missing {required}")
    return source


def _validate_evidence_package(root: Path, evidence: object) -> list[str]:
    if not isinstance(evidence, dict):
        return ["evidence must be an object"]
    errors: list[str] = []
    if evidence.get("contract_version") != EVIDENCE_CONTRACT_VERSION:
        errors.append(f"contract_version must be {EVIDENCE_CONTRACT_VERSION}")
    if evidence.get("mode") != "readonly":
        errors.append("mode must be readonly")
    if (evidence.get("policy") or {}).get("allowed_actions") != ["read"]:
        errors.append("policy.allowed_actions must contain only read")
    integrity = evidence.get("integrity") or {}
    if integrity.get("algorithm") != "sha256":
        errors.append("integrity.algorithm must be sha256")
    expected_hash = integrity.get("evidence_sha256")
    if expected_hash != _evidence_hash(evidence):
        errors.append("integrity.evidence_sha256 does not match evidence content")

    for file_item in _iter_file_items(evidence):
        if file_item.get("download_status") != "success":
            continue
        local_path = file_item.get("local_path")
        parsed = PurePosixPath(local_path) if isinstance(local_path, str) else None
        if (
            not parsed
            or not local_path
            or parsed.is_absolute()
            or ".." in parsed.parts
        ):
            errors.append("downloaded file local_path must be a safe relative path")
            continue
        target = (root / Path(*parsed.parts)).resolve(strict=False)
        if not _is_within(target, root.resolve()):
            errors.append("downloaded file local_path escapes evidence package")
            continue
        if not target.is_file() or target.is_symlink():
            errors.append(f"downloaded file is missing: {local_path}")
            continue
        expected_size = file_item.get("size")
        if isinstance(expected_size, int) and target.stat().st_size != expected_size:
            errors.append(f"downloaded file size mismatch: {local_path}")
        if file_item.get("sha256") != _sha256_file(target):
            errors.append(f"downloaded file SHA-256 mismatch: {local_path}")
    return errors


def _iter_file_items(evidence: dict[str, Any]):
    work_items = evidence.get("work_items")
    if not isinstance(work_items, list):
        return
    for work_item in work_items:
        if not isinstance(work_item, dict):
            continue
        for collection in ("attachments", "inline_files"):
            file_items = work_item.get(collection)
            if not isinstance(file_items, list):
                continue
            for file_item in file_items:
                if isinstance(file_item, dict):
                    yield file_item


def _evidence_hash(evidence: dict[str, Any]) -> str:
    payload = dict(evidence)
    payload.pop("integrity", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _task_identity(evidence: dict[str, Any], ticket_id: str) -> tuple[str, str]:
    source = evidence.get("source") or {}
    resolved_id = str(source.get("resolved_work_item_id") or "")
    for item in evidence.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("serial_number") or "").upper() == ticket_id
            or str(item.get("id") or "") == resolved_id
        ):
            return str(item.get("title") or ""), str(item.get("category") or "")
    return "", ""


def _successful_file_count(evidence: dict[str, Any]) -> int:
    return sum(
        1
        for file_item in _iter_file_items(evidence)
        if file_item.get("download_status") == "success"
    )


def _copy_tree_exclusive(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=str(destination.parent),
        )
    )
    try:
        for source_path in sorted(source.rglob("*")):
            relative = source_path.relative_to(source)
            target = staging / relative
            if source_path.is_symlink():
                raise ValueError(f"evidence package must not contain symlinks: {source_path}")
            if source_path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif source_path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target)
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _ensure_root_files(root: Path) -> None:
    readme = root / "README.md"
    if not readme.exists():
        _create_text(
            readme,
            "# Harness History\n\n"
            "这里保存需求处理的长期档案。证据版本和处理批次只新增、不覆盖；"
            "worktree 位于各需求目录的 `worktrees/<run-id>/`，不与原始证据混放。\n",
        )


def _ensure_task_files(
    *,
    task_dir: Path,
    provider: str,
    ticket_id: str,
    title: str,
    category: str,
) -> None:
    task_json = task_dir / "task.json"
    if task_json.exists():
        existing = _load_json(task_json)
        if (
            existing.get("provider") != provider
            or existing.get("ticket_id") != ticket_id
        ):
            raise ValueError(f"task metadata conflicts with directory: {task_dir}")
    else:
        _create_json(
            task_json,
            {
                "contract_version": CONTRACT_VERSION,
                "provider": provider,
                "ticket_id": ticket_id,
                "title_at_first_archive": title,
                "category_at_first_archive": category,
                "history_policy": "append_only_revisions",
            },
        )
    readme = task_dir / "README.md"
    if not readme.exists():
        _create_text(
            readme,
            f"# {ticket_id} {title}\n\n"
            f"- 来源：{provider}\n"
            f"- 类型：{category or '-'}\n"
            "- `evidence/revisions/`：每次读取到的原需求、评论和附件快照。\n"
            "- `runs/`：每次处理的项目映射、判断、改动、评审和验证记录。\n"
            "- `worktrees/`：按处理批次隔离的项目工作区。\n",
        )


def _write_index(root: Path) -> None:
    tasks: list[dict[str, str]] = []
    for provider_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if provider_dir.name.startswith("."):
            continue
        for task_dir in sorted(path for path in provider_dir.iterdir() if path.is_dir()):
            task_json = task_dir / "task.json"
            if not task_json.is_file():
                continue
            task = _load_json(task_json)
            tasks.append(
                {
                    "provider": str(task.get("provider") or provider_dir.name),
                    "ticket_id": str(task.get("ticket_id") or task_dir.name),
                    "title": str(task.get("title_at_first_archive") or ""),
                    "relative_path": str(task_dir.relative_to(root)),
                    "run_count": str(
                        len(
                            [
                                path
                                for path in (task_dir / "runs").glob("*")
                                if path.is_dir()
                            ]
                        )
                    ),
                }
            )
    _replace_json(
        root / "index.json",
        {"contract_version": CONTRACT_VERSION, "tasks": tasks},
    )


def _render_run_status(run: dict[str, Any]) -> str:
    interaction = run.get("interaction") or {"status": "none"}
    lines = [
        f"# {run['ticket_id']} / {run['run_id']}",
        "",
        f"- 标题：{run.get('title') or '-'}",
        f"- 证据门禁：{run['evidence']['decision_gate'] or '-'}",
        f"- 完整性：{run['evidence']['completeness'] or '-'}",
        f"- 证据版本：`{run['evidence']['relative_path']}`",
        f"- worktree：`{run['worktrees']['relative_path']}`",
        f"- 待答交互：{interaction.get('status') or 'none'}",
        f"- 自动续跑动作：{interaction.get('next_action') or '-'}",
        "",
        "## 处理阶段",
        "",
    ]
    lines.extend(f"- {name}: {status}" for name, status in run["stages"].items())
    return "\n".join(lines) + "\n"


def rebuild_state(*, task_dir: str | Path, run_id: str) -> dict[str, Any]:
    task = _checked_existing_task_dir(task_dir)
    run_id = _checked_run_id(run_id)
    run_dir = task / "runs" / run_id
    if not (run_dir / "run.json").is_file():
        raise FileNotFoundError(f"run does not exist: {run_dir}")
    return _rebuild_state(run_dir)


def _append_event(
    run_dir: Path,
    *,
    event_type: str,
    summary: str,
    stage: str = "",
    status: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", event_type):
        raise ValueError(f"invalid event type: {event_type}")
    events_dir = run_dir / "events"
    if events_dir.is_symlink():
        raise ValueError(f"events directory must not be a symlink: {events_dir}")
    events_dir.mkdir(exist_ok=True)
    lock_path = events_dir / ".sequence.lock"
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            sequence = max(
                (
                    int(path.name.split("-", 1)[0])
                    for path in events_dir.glob("*.json")
                    if path.name.split("-", 1)[0].isdigit()
                ),
                default=0,
            ) + 1
            while any(events_dir.glob(f"{sequence:04d}-*.json")):
                sequence += 1
            event = {
                "contract_version": CONTRACT_VERSION,
                "sequence": sequence,
                "event_type": event_type,
                "recorded_at": datetime.now().astimezone().isoformat(),
                "stage": stage,
                "status": status,
                "summary": summary,
                "details": details or {},
            }
            path = events_dir / f"{sequence:04d}-{event_type}.json"
            _create_json(path, event)
            return event
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _calculate_state(
    run_dir: Path,
    run_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = run_record or _load_json(run_dir / "run.json")
    stages = dict(run.get("stages") or {})
    events: list[dict[str, Any]] = []
    events_dir = run_dir / "events"
    if events_dir.is_dir():
        event_records = [
            _load_json(event_path)
            for event_path in events_dir.glob("*.json")
        ]
        for event in sorted(
            event_records,
            key=lambda item: int(item.get("sequence") or 0),
        ):
            events.append(event)
            stage = event.get("stage")
            status = event.get("status")
            if stage and status:
                if stage not in stages:
                    raise ValueError(f"unknown stage in event: {stage}")
                if status not in STAGE_STATUSES.get(stage, frozenset()):
                    raise ValueError(f"invalid status in event for {stage}: {status}")
                stages[stage] = status
    return {
        "contract_version": CONTRACT_VERSION,
        "provider": run.get("provider"),
        "ticket_id": run.get("ticket_id"),
        "run_id": run.get("run_id"),
        "title": run.get("title"),
        "category": run.get("category"),
        "evidence": run.get("evidence"),
        "worktrees": run.get("worktrees"),
        "stages": stages,
        "interaction": _interaction_state(run_dir),
        "event_count": len(events),
        "last_event": events[-1] if events else None,
    }


def _rebuild_state(run_dir: Path) -> dict[str, Any]:
    _ensure_run_directories(run_dir)
    state = _calculate_state(run_dir)
    _replace_json(run_dir / "run-state.json", state)
    _replace_text(run_dir / "STATUS.md", _render_run_status(state))
    return state


def _ensure_run_directories(run_dir: Path) -> None:
    for name in RUN_SUBDIRECTORIES:
        target = run_dir / name
        if target.is_symlink():
            raise ValueError(f"run directory must not be a symlink: {target}")
        target.mkdir(exist_ok=True)


def _render_project(project: dict[str, Any]) -> str:
    commits = project.get("historical_commits") or []
    commit_text = "、".join(f"`{commit}`" for commit in commits) or "未发现"
    return (
        f"# 项目映射：{project['name']}\n\n"
        f"- 角色：{project['role']}\n"
        f"- 原项目：`{project['repo_path']}`\n"
        f"- worktree：`{project['worktree_path'] or '尚未创建'}`\n"
        f"- 基线分支：`{project['base_branch'] or '尚未确定'}`\n"
        f"- 基线提交：`{project['base_commit'] or '尚未确定'}`\n"
        f"- 历史关联提交：{commit_text}\n"
        f"- 映射依据：{project['reason']}\n"
    )


def _checked_provider(value: str) -> str:
    provider = value.upper()
    if not PROVIDER_PATTERN.fullmatch(provider):
        raise ValueError(f"invalid provider: {value}")
    return provider


def _checked_ticket_id(value: str) -> str:
    ticket_id = value.upper()
    if not TASK_ID_PATTERN.fullmatch(ticket_id):
        raise ValueError(f"invalid ticket id: {value}")
    return ticket_id


def _checked_run_id(value: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(value):
        raise ValueError(f"invalid run id: {value}")
    return value


def _checked_history_root(value: str | Path) -> Path:
    root = Path(value)
    if root.is_symlink():
        raise ValueError("history root must not be a symlink")
    return root.resolve()


def _checked_existing_task_dir(value: str | Path) -> Path:
    task = Path(value)
    if task.is_symlink():
        raise ValueError("task directory must not be a symlink")
    task = task.resolve()
    if not task.is_dir():
        raise FileNotFoundError(f"task directory does not exist: {task}")
    _checked_ticket_id(task.name)
    return task


def _task_id_from_dir(task_dir: Path) -> str:
    return _checked_ticket_id(task_dir.name)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_within_lexical(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _ensure_no_symlink_components(path: Path, stop: Path) -> None:
    if not path.is_absolute() or not stop.is_absolute():
        raise ValueError("symlink boundary paths must be absolute")
    try:
        relative = path.relative_to(stop)
    except ValueError as exc:
        raise ValueError(f"path escapes task boundary: {path}") from exc
    current = stop
    if current.is_symlink():
        raise ValueError(f"task path must not be a symlink: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"path component must not be a symlink: {current}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _create_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def _create_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)


def _create_json(path: Path, value: dict[str, Any]) -> None:
    _create_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _replace_json(path: Path, value: dict[str, Any]) -> None:
    _replace_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _replace_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Archive immutable requirement evidence and project mappings."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    archive = subparsers.add_parser("archive-evidence")
    archive.add_argument("--source-dir", required=True)
    archive.add_argument("--history-root", required=True)
    archive.add_argument("--provider", required=True)
    archive.add_argument("--ticket-id", required=True)
    archive.add_argument("--run-id")
    archive.add_argument(
        "--intake-required",
        action="store_true",
        help="Require an intake/request.json record for this run",
    )

    project = subparsers.add_parser("record-project")
    project.add_argument("--task-dir", required=True)
    project.add_argument("--run-id", required=True)
    project.add_argument("--name", required=True)
    project.add_argument("--repo-path", required=True)
    project.add_argument("--role", choices=sorted(PROJECT_ROLES), required=True)
    project.add_argument("--reason", required=True)
    project.add_argument("--worktree-path")
    project.add_argument("--base-branch", default="")
    project.add_argument("--base-commit", default="")
    project.add_argument("--historical-commit", action="append", default=[])

    validate = subparsers.add_parser("validate-task")
    validate.add_argument("--task-dir", required=True)

    rebuild = subparsers.add_parser("rebuild-state")
    rebuild.add_argument("--task-dir", required=True)
    rebuild.add_argument("--run-id", required=True)

    stage = subparsers.add_parser("record-stage")
    stage.add_argument("--task-dir", required=True)
    stage.add_argument("--run-id", required=True)
    stage.add_argument("--stage", choices=sorted(STAGE_STATUSES), required=True)
    stage.add_argument("--status", required=True)
    stage.add_argument("--summary", required=True)

    interaction_request = subparsers.add_parser("request-interaction")
    interaction_request.add_argument("--task-dir", required=True)
    interaction_request.add_argument("--run-id", required=True)
    interaction_request.add_argument("--interaction-id", required=True)
    interaction_request.add_argument(
        "--kind",
        choices=sorted(INTERACTION_KINDS),
        required=True,
    )
    interaction_request.add_argument("--question", required=True)
    interaction_request.add_argument("--option", action="append", default=[])
    interaction_request.add_argument(
        "--resume-stage",
        choices=sorted(STAGE_STATUSES),
        required=True,
    )
    interaction_request.add_argument("--next-action", required=True)

    interaction_resolve = subparsers.add_parser("resolve-interaction")
    interaction_resolve.add_argument("--task-dir", required=True)
    interaction_resolve.add_argument("--run-id", required=True)
    interaction_resolve.add_argument("--interaction-id", required=True)
    interaction_resolve.add_argument("--answer", required=True)

    pending_interaction = subparsers.add_parser("pending-interaction")
    pending_interaction.add_argument("--task-dir", required=True)
    pending_interaction.add_argument("--run-id", required=True)

    interaction_resume = subparsers.add_parser("resume-interaction")
    interaction_resume.add_argument("--task-dir", required=True)
    interaction_resume.add_argument("--run-id", required=True)
    interaction_resume.add_argument("--interaction-id", required=True)

    decision = subparsers.add_parser("record-decision")
    decision.add_argument("--task-dir", required=True)
    decision.add_argument("--run-id", required=True)
    decision.add_argument(
        "--verdict",
        choices=sorted(DECISION_VERDICTS),
        required=True,
    )
    decision.add_argument("--reason", required=True)
    decision.add_argument("--project", action="append", required=True)
    decision.add_argument("--evidence", action="append", required=True)
    decision.add_argument("--change-scope", default="")
    decision.add_argument("--blocker", action="append", default=[])
    decision.add_argument(
        "--allowed-path",
        action="append",
        default=[],
        help="Repeat as <project>:<relative-file> for can_change",
    )

    worktree = subparsers.add_parser("create-worktree")
    worktree.add_argument("--task-dir", required=True)
    worktree.add_argument("--run-id", required=True)
    worktree.add_argument("--project", required=True)
    worktree.add_argument("--base-ref", default="")

    patch = subparsers.add_parser("archive-patch")
    patch.add_argument("--task-dir", required=True)
    patch.add_argument("--run-id", required=True)
    patch.add_argument("--project", required=True)

    review = subparsers.add_parser("record-review")
    review.add_argument("--task-dir", required=True)
    review.add_argument("--run-id", required=True)
    review.add_argument("--verdict", choices=("passed", "failed"), required=True)
    review.add_argument("--summary", required=True)
    review.add_argument("--can-fix", choices=("yes", "no"), required=True)
    review.add_argument("--findings-file", default="")
    review.add_argument("--cannot-fix-reason", default="")

    verification = subparsers.add_parser("record-verification")
    verification.add_argument("--task-dir", required=True)
    verification.add_argument("--run-id", required=True)
    verification.add_argument("--status", choices=("passed", "failed"), required=True)
    verification.add_argument("--summary", required=True)
    verification.add_argument("--checks-file", required=True)

    apply_back = subparsers.add_parser("apply-back")
    apply_back.add_argument("--task-dir", required=True)
    apply_back.add_argument("--run-id", required=True)
    apply_back.add_argument("--project", required=True)
    apply_back.add_argument("--ack-local-write", action="store_true")

    reconcile_delivery = subparsers.add_parser("reconcile-delivery")
    reconcile_delivery.add_argument("--task-dir", required=True)
    reconcile_delivery.add_argument("--run-id", required=True)
    reconcile_delivery.add_argument("--project", required=True)
    reconcile_delivery.add_argument(
        "--target-ref",
        default=DELIVERY_TARGET_WORKTREE,
        help="WORKTREE, a local commit/ref, or an already fetched remote tracking ref",
    )
    reconcile_delivery.add_argument(
        "--delivery-stage",
        choices=sorted(DELIVERY_STAGES),
        default="workspace",
    )
    return parser


def _load_json_array(path: str, label: str) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise ValueError(f"{label} must be a JSON array of objects")
    return value


def _parse_allowed_paths(values: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for value in values:
        project, separator, relative_path = value.partition(":")
        if not separator or not project or not relative_path:
            raise ValueError(
                "allowed path must use <project>:<relative-file>"
            )
        if not PROJECT_NAME_PATTERN.fullmatch(project):
            raise ValueError(f"invalid project name in allowed path: {project}")
        result.setdefault(project, []).append(relative_path)
    return result


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "archive-evidence":
        result = archive_evidence(
            source_dir=args.source_dir,
            history_root=args.history_root,
            provider=args.provider,
            ticket_id=args.ticket_id,
            run_id=args.run_id,
            intake_required=args.intake_required,
        )
        print(json.dumps({"status": "archived", **result}, ensure_ascii=False))
        return 0
    if args.command == "record-project":
        result = record_project(
            task_dir=args.task_dir,
            run_id=args.run_id,
            name=args.name,
            repo_path=args.repo_path,
            role=args.role,
            reason=args.reason,
            worktree_path=args.worktree_path,
            base_branch=args.base_branch,
            base_commit=args.base_commit,
            historical_commits=args.historical_commit,
        )
        print(json.dumps({"status": "recorded", "project": result}, ensure_ascii=False))
        return 0
    if args.command == "rebuild-state":
        result = rebuild_state(task_dir=args.task_dir, run_id=args.run_id)
        print(json.dumps({"status": "rebuilt", "state": result}, ensure_ascii=False))
        return 0
    if args.command == "record-stage":
        result = record_stage(
            task_dir=args.task_dir,
            run_id=args.run_id,
            stage=args.stage,
            status=args.status,
            summary=args.summary,
        )
        print(json.dumps({"status": "recorded", "event": result}, ensure_ascii=False))
        return 0
    if args.command == "request-interaction":
        result = record_interaction_request(
            task_dir=args.task_dir,
            run_id=args.run_id,
            interaction_id=args.interaction_id,
            kind=args.kind,
            question=args.question,
            options=args.option,
            resume_stage=args.resume_stage,
            next_action=args.next_action,
        )
        print(json.dumps({"status": "awaiting_user", "interaction": result}, ensure_ascii=False))
        return 0
    if args.command == "resolve-interaction":
        result = record_interaction_resolution(
            task_dir=args.task_dir,
            run_id=args.run_id,
            interaction_id=args.interaction_id,
            answer=args.answer,
        )
        print(json.dumps({"status": "resolved_resume_required", "interaction": result}, ensure_ascii=False))
        return 0
    if args.command == "pending-interaction":
        result = get_pending_interaction(task_dir=args.task_dir, run_id=args.run_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "resume-interaction":
        result = record_interaction_resume(
            task_dir=args.task_dir,
            run_id=args.run_id,
            interaction_id=args.interaction_id,
        )
        print(json.dumps({"status": "resumed", "interaction": result}, ensure_ascii=False))
        return 0
    if args.command == "record-decision":
        result = record_change_decision(
            task_dir=args.task_dir,
            run_id=args.run_id,
            verdict=args.verdict,
            reason=args.reason,
            projects=args.project,
            evidence=args.evidence,
            change_scope=args.change_scope,
            blockers=args.blocker,
            allowed_paths=_parse_allowed_paths(args.allowed_path),
        )
        print(
            json.dumps(
                {"status": "recorded", "decision": result},
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "create-worktree":
        result = create_project_worktree(
            task_dir=args.task_dir,
            run_id=args.run_id,
            project=args.project,
            base_ref=args.base_ref,
        )
        print(
            json.dumps(
                {"status": "created", "worktree": result},
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "archive-patch":
        result = archive_project_patch(
            task_dir=args.task_dir,
            run_id=args.run_id,
            project=args.project,
        )
        print(
            json.dumps(
                {"status": "archived", "patch": result},
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "record-review":
        findings = (
            _load_json_array(args.findings_file, "findings")
            if args.findings_file
            else []
        )
        result = record_codex_review(
            task_dir=args.task_dir,
            run_id=args.run_id,
            verdict=args.verdict,
            summary=args.summary,
            can_fix=args.can_fix == "yes",
            findings=findings,
            cannot_fix_reason=args.cannot_fix_reason,
        )
        print(
            json.dumps(
                {"status": "recorded", "review": result},
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "record-verification":
        result = record_verification(
            task_dir=args.task_dir,
            run_id=args.run_id,
            status=args.status,
            summary=args.summary,
            checks=_load_json_array(args.checks_file, "checks"),
        )
        print(
            json.dumps(
                {"status": "recorded", "verification": result},
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "apply-back":
        result = apply_project_patch(
            task_dir=args.task_dir,
            run_id=args.run_id,
            project=args.project,
            ack_local_write=args.ack_local_write,
        )
        print(
            json.dumps(
                {"status": result["status"], "apply_back": result},
                ensure_ascii=False,
            )
        )
        return 0 if result["status"] == "applied" else 2
    if args.command == "reconcile-delivery":
        result = reconcile_project_delivery(
            task_dir=args.task_dir,
            run_id=args.run_id,
            project=args.project,
            target_ref=args.target_ref,
            delivery_stage=args.delivery_stage,
        )
        print(
            json.dumps(
                {"status": result["status"], "delivery_reconciliation": result},
                ensure_ascii=False,
            )
        )
        return 0 if result["status"] in {
            "local_workspace_verified",
            "commit_verified",
            "remote_ref_verified",
        } else 2
    errors = validate_task(args.task_dir)
    if errors:
        print(json.dumps({"status": "invalid", "errors": errors}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "valid", "task_dir": args.task_dir}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
