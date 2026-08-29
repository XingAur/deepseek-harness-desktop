"""Export one durable, reviewable Harness task package.

The legacy archive keeps the provider snapshot under ``yunxiao/`` and the
workflow keeps generated artifacts in SQLite.  This module joins those two
truth sources into a user-selected, task-scoped directory without inventing
missing requirement facts.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app import database


PACKAGE_SCHEMA = "harness-task-package.v1"

_ARTIFACT_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "requirement_understanding.md": ("analysis", ("requirement_understanding_markdown", "requirement_calibration_markdown")),
    "requirement_understanding.json": ("analysis", ("requirement_understanding_json", "requirement_calibration_json")),
    "goals_and_wishes.md": ("analysis", ("requirement_calibration_markdown",)),
    "scenarios.md": ("analysis", ("acceptance_matrix_markdown", "behavior_test_plan_markdown")),
    "functional_requirements.md": ("analysis", ("requirement_understanding_markdown", "acceptance_matrix_markdown")),
    "acceptance_criteria.md": ("analysis", ("acceptance_matrix_markdown", "behavior_acceptance_markdown")),
    "constraints_and_non_goals.md": ("analysis", ("requirement_governance_markdown", "single_pass_change_contract_markdown")),
    "requirement_plan.md": ("analysis", ("fullstack_patch_plan_markdown", "single_pass_change_contract_markdown")),
    "project_understanding.md": ("analysis", ("project_selection_markdown", "service_graph_markdown", "technical_decision_markdown")),
    "project_plan.md": ("analysis", ("fullstack_patch_plan_markdown", "implementation_decision_markdown")),
    "implementation_plan.md": ("analysis", ("fullstack_patch_plan_markdown", "single_pass_change_contract_markdown")),
    "verification_plan.md": ("execution", ("behavior_test_plan_markdown", "verification_matrix_markdown", "method_test_runner_markdown")),
    "risk_and_rollback.md": ("execution", ("yunxiao_transaction_plan_markdown", "requirement_governance_markdown", "commit_ready_summary_markdown")),
    "prd.md": ("analysis", ("requirement_understanding_markdown", "acceptance_matrix_markdown")),
    "task_contract.json": ("engineering", ("single_pass_change_contract_json", "multi_service_change_contract_json")),
    "evidence_index.md": ("engineering", ("evidence_markdown", "yunxiao_evidence_json", "requirement_evidence_markdown")),
    "engineering_evidence.json": ("engineering", ("evidence_json",)),
    "technical_decision.json": ("engineering", ("technical_decision_json",)),
    "technical_decision.md": ("engineering", ("technical_decision_markdown", "implementation_decision_markdown")),
    "call_chain.md": ("engineering", ("service_graph_markdown", "field_provenance_markdown")),
    "change_ownership.md": ("engineering", ("change_ownership_markdown",)),
    "clarification_gate.json": ("engineering", ("clarification_gate_json",)),
    "execution_report.md": ("execution", ("worktree_summary_markdown", "single_demand_trial_markdown", "fullstack_summary_markdown")),
    "verification_matrix.json": ("execution", ("verification_matrix_json", "behavior_acceptance_json")),
    "review_report.md": ("execution", ("code_review_markdown", "review_summary_markdown", "patch_review_markdown")),
    "failure_redecisions.md": ("execution", ("error_chain_closure_markdown", "demand_progress_post_change_markdown")),
}


def export_requirement_package(
    *,
    ticket_dir: str | Path,
    run_id: int,
    package_dir: str | Path | None = None,
) -> dict[str, object]:
    """Export the source archive and all generated run evidence.

    The destination is ``<ticket>/harness`` by default.  Existing generated
    files are replaced individually; provider source files are copied from
    the durable archive and are never deleted.  Missing semantic artifacts are
    represented as ``pending`` documents so the UI can distinguish an absent
    model result from a confirmed requirement fact.
    """

    ticket = Path(ticket_dir).expanduser().resolve()
    if not ticket.is_dir():
        raise ValueError(f"需求档案目录不存在：{ticket}")
    target = Path(package_dir).expanduser().resolve() if package_dir else ticket / "harness"
    target.mkdir(parents=True, exist_ok=True)
    for directory in ("source", "analysis", "engineering", "execution"):
        (target / directory).mkdir(parents=True, exist_ok=True)

    # ``run_id=0`` is the pre-run desktop intake package.  It contains the
    # immutable source archive and explicit pending analysis documents, so it
    # must not initialize or mutate the Harness control database just to read
    # an empty artifact set.
    artifacts = [dict(item) for item in database.get_artifacts(run_id)] if run_id > 0 else []
    artifact_by_kind: dict[str, dict[str, object]] = {}
    for artifact in artifacts:
        kind = str(artifact.get("kind") or "")
        if kind and kind not in artifact_by_kind:
            artifact_by_kind[kind] = artifact

    _copy_source_archive(ticket, target / "source")
    _write_text(target / "execution" / "run_report.md", _read_text(ticket / "runs" / f"harness-run-{run_id}.md"))
    for filename, (category, kinds) in _ARTIFACT_TARGETS.items():
        destination = target / category / filename
        artifact = next((artifact_by_kind[kind] for kind in kinds if kind in artifact_by_kind), None)
        if artifact is not None and str(artifact.get("content") or "").strip():
            _write_text(destination, str(artifact["content"]).rstrip() + "\n")
        else:
            _write_text(destination, _pending_document(filename, run_id, kinds))

    step_dir = target / "execution" / "steps"
    step_dir.mkdir(parents=True, exist_ok=True)
    for step in database.get_step_runs(run_id) if run_id > 0 else []:
        step_key = _safe_name(f"{step.get('step_order', 0)}-{step.get('step_key') or 'step'}")
        output = str(step.get("output_text") or "")
        error = str(step.get("error") or "")
        if output:
            _write_text(step_dir / f"{step_key}.md", output.rstrip() + "\n")
        if error:
            _write_text(step_dir / f"{step_key}.error.md", error.rstrip() + "\n")

    manifest = _build_manifest(target=target, ticket=ticket, run_id=run_id)
    _write_text(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    _write_text(target / "README.md", _readme(manifest))
    manifest = _build_manifest(target=target, ticket=ticket, run_id=run_id)
    _write_text(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "schema": PACKAGE_SCHEMA,
        "run_id": run_id,
        "ticket_id": ticket.name,
        "package_dir": str(target),
        "status": manifest["status"],
        "manifest_path": str(target / "manifest.json"),
        "pending_count": manifest["pending_count"],
        "model_generated_count": manifest["model_generated_count"],
    }


def rebuild_requirement_package_manifest(
    *,
    package_dir: str | Path,
    ticket_dir: str | Path,
    run_id: int = 0,
) -> dict[str, object]:
    """Recompute the manifest after files were written in place.

    The intake model generation writes ``model_generated`` documents into an
    already-exported package.  This helper re-derives every entry (status,
    origin, sha256) and rewrites ``manifest.json`` / ``README.md`` without
    touching any document content.
    """

    target = Path(package_dir).expanduser().resolve()
    ticket = Path(ticket_dir).expanduser().resolve()
    manifest = _build_manifest(target=target, ticket=ticket, run_id=run_id)
    _write_text(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    _write_text(target / "README.md", _readme(manifest))
    return {
        "status": manifest["status"],
        "pending_count": manifest["pending_count"],
        "model_generated_count": manifest["model_generated_count"],
        "manifest_path": str(target / "manifest.json"),
    }


def _copy_source_archive(ticket: Path, source: Path) -> None:
    for name in ("requirement.md", "yunxiao", "runs"):
        origin = ticket / name
        destination = source / name
        if origin.is_dir():
            shutil.copytree(origin, destination, dirs_exist_ok=True)
        elif origin.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)


def _pending_document(filename: str, run_id: int, source_kinds: tuple[str, ...]) -> str:
    if filename.endswith(".json"):
        return json.dumps(
            {
                "status": "pending",
                "harness_run": run_id,
                "reason": "没有足够的已确认证据或对应模型工件，当前不能安全生成确认结论。",
                "pending_source_artifacts": list(source_kinds),
                "note": "该文件是可追踪占位，不代表需求事实，也不会触发向用户询问内部实现问题。",
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    return "\n".join(
        [
            f"# {filename.rsplit('.', 1)[0].replace('_', ' ').title()}",
            "",
            "- 状态：pending",
            f"- Harness run：{run_id}",
            "- 原因：没有足够的已确认证据或对应模型工件，当前不能安全生成确认结论。",
            f"- 待补来源工件：{', '.join(source_kinds)}",
            "- 说明：该文件是可追踪占位，不代表需求事实，也不会触发向用户询问内部实现问题。",
            "",
        ]
    )


def _build_manifest(*, target: Path, ticket: Path, run_id: int) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        relative = path.relative_to(target).as_posix()
        content = path.read_bytes()
        text = content.decode("utf-8", errors="ignore")
        head = text[:1200]
        pending = "状态：pending" in head or '"status": "pending"' in head
        model_generated = (
            not pending
            and ("状态：model_generated" in head or '"status": "model_generated"' in head)
        )
        entries.append(
            {
                "path": relative,
                "category": relative.split("/", 1)[0],
                "status": "pending" if pending else ("model_generated" if model_generated else "available"),
                "origin": "yunxiao_archive" if relative.startswith("source/") else "harness_generated",
                "generator": "selected_model" if model_generated else "",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    pending_count = sum(entry["status"] == "pending" for entry in entries)
    model_generated_count = sum(entry["status"] == "model_generated" for entry in entries)
    return {
        "schema": PACKAGE_SCHEMA,
        "ticket_id": ticket.name,
        "run_id": run_id,
        "status": "partial" if pending_count else "complete",
        "pending_count": pending_count,
        "model_generated_count": model_generated_count,
        "source_preserved": True,
        "files": entries,
    }


def _readme(manifest: Mapping[str, object]) -> str:
    return "\n".join(
        [
            "# Harness 任务包",
            "",
            f"- Schema：`{manifest['schema']}`",
            f"- 任务：`{manifest['ticket_id']}`",
            f"- Run：`{manifest['run_id']}`",
            f"- 状态：`{manifest['status']}`",
            f"- 待补工件：`{manifest['pending_count']}`",
            f"- 模型起草工件：`{manifest.get('model_generated_count', 0)}`",
            "",
            "`source/` 保留云效原始需求、正文、评论、图片、附件、父需求和原始文档；其余目录是 Harness 生成的理解、规划、工程、执行和验证资料。标记为 `model_generated` 的文件由当前任务选择的模型基于归档证据起草，未经人工确认，不代表已确认的需求事实。每个文件的来源和 sha256 见 `manifest.json`。",
            "",
        ]
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)[:120]
