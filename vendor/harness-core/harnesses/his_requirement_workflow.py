from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping

sys.dont_write_bytecode = True


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.capability_registry import CapabilityRegistry
from app.capability_runtime import CapabilityRuntime
from app.capability_service import CapabilityService
from app import database
from app.plugin_inventory import verify_plugin_inventory
from app.requirement_archive import (
    DEFAULT_MAX_ARCHIVE_FILE_BYTES,
    record_requirement_archive_run,
    sync_yunxiao_requirement_archive,
)
from app.harness import (
    CapabilityWorkflowOrchestrator,
    RequirementWorkflowRunner,
    resolve_capability_routing,
    write_run_outputs,
)
from app.llm_client import load_claude_settings_env_if_requested
from app.runtime_storage import ephemeral_runtime_storage
from app.technical_decision import DEFAULT_PROJECT_ROOT
from tools.capability_check import load_runtime_config
from app.visual_evidence import FileVisualEvidenceAnalyzer


DEMO_DEMAND = """
门诊收费页面需要增加“医保结算状态”展示。
要求收费员能看到当前处方是否已完成医保结算；未完成时提示继续结算。
需要注意不影响自费患者和历史收费记录。
"""
CAPABILITY_CONFIG = PROJECT_ROOT / "config" / "capabilities.json"
PLUGIN_INVENTORY = PROJECT_ROOT / "config" / "plugin_inventory.json"
_KNOWLEDGE_CAPABILITIES = (
    "knowledge.retrieve",
    "knowledge.answer",
    "knowledge.candidate.create",
    "knowledge.candidate.review",
    "knowledge.item.promote",
)
_REQUIREMENT_UNDERSTANDING_ARTIFACT_KINDS = (
    "requirement_understanding_markdown",
    "requirement_calibration_markdown",
)
_DATABASE_READONLY_CREDENTIAL_SUFFIXES = (
    "_readonly_dsn",
    "_readonly_user",
    "_readonly_password",
)


def _read_database_readonly_credentials(
    path_value: str | Path | None,
) -> dict[str, str]:
    if path_value is None:
        return {}
    path = Path(path_value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise RuntimeError("database credentials file is unavailable")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("database credentials file is unavailable") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("database credentials file is invalid")
    return {
        key: value
        for key, value in payload.items()
        if (
            isinstance(key, str)
            and key.startswith("pg_")
            and key.endswith(_DATABASE_READONLY_CREDENTIAL_SUFFIXES)
            and isinstance(value, str)
            and value
        )
    }


def _artifact_content(run_id: int, *kinds: str) -> str:
    """Return the first locally stored generated artifact, never a provider body."""
    artifacts = database.get_artifacts(run_id)
    for kind in kinds:
        for artifact in artifacts:
            if artifact.get("kind") == kind:
                content = artifact.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
    return ""


def build_capability_service(
    *,
    requested_mode: str | None,
    config_path: str | Path = CAPABILITY_CONFIG,
    database_credentials_path: str | Path | None = None,
) -> CapabilityService:
    config = load_runtime_config(str(config_path))
    routing_mode = resolve_capability_routing(
        config.routing_mode,
        requested_mode,
    )
    if routing_mode == "legacy":
        return CapabilityService(
            CapabilityRuntime(CapabilityRegistry([])),
            routing_mode="legacy",
        )
    registry = CapabilityRegistry.from_plugin_roots(config.plugin_roots)
    inventory_path = Path(config_path).resolve().parent / "plugin_inventory.json"
    verify_plugin_inventory(
        inventory_path,
        list(config.plugin_roots),
        registry=registry,
    )
    database_credentials = _read_database_readonly_credentials(
        database_credentials_path
    )
    runtime = CapabilityRuntime(
        registry,
        external_writes_default=False,
        default_timeout_seconds=config.default_timeout_seconds,
        environment_allowlist=(
            "HIS_HARNESS_ROOT",
            "HIS_KNOWLEDGE_HOME",
            *sorted(database_credentials),
        ),
    )
    capability_environments = {
        (capability, "his-knowledge"): {
            "HIS_KNOWLEDGE_HOME": config.knowledge_home,
        }
        for capability in _KNOWLEDGE_CAPABILITIES
    }
    capability_environments[("requirement.govern", "his-harness-core")] = {
        "HIS_HARNESS_ROOT": str(PROJECT_ROOT),
    }
    capability_environments[("database.inspect", "postgresql")] = (
        database_credentials
    )
    return CapabilityService(
        runtime,
        routing_mode=routing_mode,
        capability_environments=capability_environments,
    )
def _is_investigation_request(text: str) -> bool:
    normalized = text.strip()
    return normalized.startswith(
        ("调查", "查询", "核实", "检查", "读取", "查看", "定位")
    )


def _read_structured_input(path_value: str, label: str) -> dict[str, object] | None:
    if not path_value:
        return None
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} 无法解析") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run HIS requirement workflow.")
    parser.add_argument("--demo", action="store_true", help="run with built-in demo demand; defaults to mock mode")
    parser.add_argument("--demand", help="inline demand description; use --demand-file for long or structured input")
    parser.add_argument("--demand-file", help="path to a text file containing demand description")
    parser.add_argument("--title", default="手工需求", help="run title")
    parser.add_argument("--mode", choices=["openai", "anthropic", "mock"], help="LLM mode; defaults to local mock while real model runtime is frozen")
    parser.add_argument("--output-dir", default="runs", help="directory for report.md, run.json and step reports")
    parser.add_argument(
        "--retain-output",
        action="store_true",
        help="keep run records and output files for Task Manager or future local workspace use; default is disposable runtime storage",
    )
    parser.add_argument("--max-retries", type=int, default=2, help="automatic evaluator retry rounds")
    parser.add_argument(
        "--requirement-governance",
        choices=["legacy", "observe", "enforce"],
        default="observe",
        help="requirement governance mode: legacy preserves the old path, observe only records local reports, enforce blocks non-ready changes",
    )
    parser.add_argument("--project-path", action="append", default=[], help="optional HIS project repo path for read-only engineering evidence scan; repeatable")
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT, help="root directory used for v0.8.8 automatic HIS project selection")
    parser.add_argument("--project-key", help="optional project profile key from config/projects.json")
    parser.add_argument("--project-config", help="optional project profile config path")
    parser.add_argument(
        "--execution-mode",
        choices=["readonly", "worktree", "review-worktree", "fullstack-worktree", "precommit-verify", "single-demand-trial", "core-closure-trial", "auto-local"],
        default="readonly",
        help="auto-local automatically uses the local core closure and applies only a verified diff; readonly analyzes; worktree patches one repo; review-worktree reviews a commit; fullstack-worktree patches multiple repos; precommit-verify validates current diffs; single-demand-trial runs one demand through controlled dev trial; core-closure-trial uses structured gates and reviewed diff for one low-risk demand",
    )
    parser.add_argument("--worktree-dir", default="/tmp/his_harness_worktrees", help="root directory for temporary Git worktrees")
    parser.add_argument("--allowed-path", action="append", default=[], help="relative path allowed for v0.7 patch; repeatable")
    parser.add_argument("--verify-command", action="append", default=[], help="verification command to run inside worktree; repeatable")
    parser.add_argument("--method-evidence-file", default="", help="JSON file with v0.10.2 method-level interaction results for precommit-verify")
    parser.add_argument("--requirement-evidence-file", default="", help="local v0.23 requirement_evidence JSON/text file to include explicitly")
    parser.add_argument(
        "--visual-evidence-file",
        default="",
        help="explicit host-produced his-visual-evidence.v1 JSON; required image facts are checked before project discovery",
    )
    parser.add_argument("--conversation-evidence-file", default="", help="local v1 selected conversation JSON; user corrections and confirmed facts become change gates")
    parser.add_argument(
        "--acceptance-contract-file",
        default="",
        help="local v0.47 executable acceptance contract JSON; sorting/tree relation core runs require it",
    )
    parser.add_argument("--method-test-command", action="append", default=[], help="command run in temporary worktree to emit v0.10.3A method evidence JSON; repeatable")
    parser.add_argument("--ui-evidence-path", action="append", default=[], help="screenshot/video/GIF/manual evidence file path for v0.10.2; repeatable")
    parser.add_argument("--ui-capture-command", action="append", default=[], help="command run in temporary worktree to emit v0.10.3B UI evidence JSON and files; repeatable")
    parser.add_argument("--max-edit-rounds", type=int, default=2, help="automatic code-edit retry rounds after the first patch attempt")
    parser.add_argument(
        "--pre-change-confirmation",
        default="",
        help="mutating modes require the exact CONFIRM-SCOPE token from the prior scope report; without it no code change or local apply is allowed",
    )
    local_apply_group = parser.add_mutually_exclusive_group()
    local_apply_group.add_argument(
        "--review-only",
        dest="apply_approved_diff",
        action="store_false",
        help="core-closure-trial/auto-local only: keep a verified patch in the temporary worktree and do not apply it to the local project",
    )
    local_apply_group.add_argument(
        "--apply-approved-diff",
        dest="apply_approved_diff",
        action="store_true",
        help="legacy compatibility flag: apply a verified final diff to the local project (now the default)",
    )
    parser.set_defaults(apply_approved_diff=True)
    parser.add_argument("--review-commit", default="HEAD", help="commit to review in review-worktree mode")
    parser.add_argument("--review-base", default="", help="base commit for review-worktree mode; defaults to review-commit^")
    parser.add_argument("--yunxiao-read", action="store_true", help="read Yunxiao work item evidence without writing comments/status/assignees")
    parser.add_argument("--yunxiao-url", default="", help="Yunxiao requirement/bug URL used for read-only evidence collection")
    parser.add_argument("--yunxiao-ignore-comments", action="store_true", help="do not request or use Yunxiao comments; use only the work item body and files")
    parser.add_argument(
        "--yunxiao-archive-root",
        default="",
        help="absolute local root for durable DFHIS-<id> Yunxiao archives; enables full read-only attachment collection",
    )
    parser.add_argument(
        "--yunxiao-archive-change-note",
        default="",
        help="optional user change note appended to the existing requirement.md of the same Yunxiao item",
    )
    parser.add_argument(
        "--yunxiao-archive-max-file-bytes",
        type=int,
        default=DEFAULT_MAX_ARCHIVE_FILE_BYTES,
        help="per-file archive download limit; default 100 MiB",
    )
    parser.add_argument(
        "--yunxiao-archive-no-size-limit",
        action="store_true",
        help="explicitly remove the per-file archive limit; use only when the local storage risk is understood",
    )
    parser.add_argument("--yunxiao-transaction-mode", choices=["off", "dry-run", "write"], default="off", help="generate a Yunxiao transaction plan; write remains disabled in every capability routing mode")
    parser.add_argument("--yunxiao-policy-config", default="", help="optional Yunxiao transaction policy config path")
    parser.add_argument("--yunxiao-policy-key", default="", help="project key inside Yunxiao transaction policy config; separate from engineering project profile key")
    parser.add_argument("--yunxiao-entity-kind", choices=["bug", "requirement", "task"], default="", help="Yunxiao entity kind for transaction dry-run")
    parser.add_argument("--yunxiao-entity-id", default="", help="Yunxiao entity id for transaction dry-run; defaults to URL/demand id")
    parser.add_argument("--yunxiao-current-status", default="", help="current Yunxiao status used for transition policy validation")
    parser.add_argument("--yunxiao-target-assignee", default="", help="optional target assignee for dry-run assignment recommendation")
    parser.add_argument("--yunxiao-target-status", default="", help="optional target Yunxiao status for dry-run transition recommendation")
    parser.add_argument("--yunxiao-target-iteration", default="", help="optional target iteration id/name for dry-run iteration update recommendation")
    parser.add_argument("--yunxiao-screenshot", action="append", default=[], help="screenshot/attachment path to include in dry-run plan; repeatable")
    parser.add_argument("--yunxiao-service-change-file", default="", help="JSON file describing service-change dry-run plan")
    parser.add_argument("--yunxiao-artifact", action="append", default=[], help="artifact to link in dry-run plan, format type=value; repeatable")
    parser.add_argument("--yunxiao-write-confirm", default="", help="required for write mode: WRITE:<entity_kind>:<entity_id>")
    parser.add_argument("--yunxiao-human-confirmed", action="store_true", help="confirm high-risk Yunxiao write actions have human approval")
    parser.add_argument("--yunxiao-write-transport", choices=["real", "fake"], default="real", help="real calls Yunxiao OpenAPI; fake only exercises write pipeline")
    parser.add_argument(
        "--yunxiao-write-scope",
        choices=["comment-only", "transition-fake"],
        default="comment-only",
        help="limit Yunxiao writes; real writes only allow comment-only, transition-fake requires fake transport",
    )
    parser.add_argument(
        "--load-claude-settings",
        action="store_true",
        help="load Anthropic-compatible env vars from ~/.claude/settings.json without printing secrets",
    )
    parser.add_argument(
        "--capability-routing",
        choices=["legacy", "observe", "enforce"],
        default=None,
        help="capability routing override; may observe or lower configured authority, but cannot upgrade to enforce",
    )
    parser.add_argument(
        "--interaction-mode",
        choices=["task", "question"],
        default="task",
        help="task runs the requirement workflow; question only invokes knowledge.answer",
    )
    parser.add_argument(
        "--allow-live-evidence",
        action="store_true",
        help="question mode only: explicitly allow suggested read-only evidence capabilities",
    )
    parser.add_argument(
        "--database-inspect-file",
        default="",
        help="task mode only: structured database.inspect JSON; absence means code evidence is sufficient and no database is queried",
    )
    parser.add_argument(
        "--database-execute",
        action="store_true",
        help="task mode only: after a compliant readonly preview, execute database.inspect with the readonly profile",
    )
    parser.add_argument(
        "--database-credentials-file",
        default="",
        help="task mode only: private local JSON containing pg_<profile>_readonly_* values; values are injected only into database.inspect and are never reported",
    )
    parser.add_argument(
        "--database-change-file",
        default="",
        help="task mode only: structured database change request; generates database.change-plan and never applies SQL",
    )
    parser.add_argument(
        "--knowledge-candidate-file",
        default="",
        help="task mode only: explicit reusable knowledge candidate JSON; successful runs create a pending candidate and never auto-promote it",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.database_execute and not args.database_inspect_file:
        parser.error("--database-execute requires --database-inspect-file")
    if args.database_execute and not args.database_credentials_file:
        parser.error("--database-execute requires --database-credentials-file")
    if args.database_credentials_file and not args.database_inspect_file:
        parser.error("--database-credentials-file requires --database-inspect-file")
    if args.yunxiao_archive_root and not args.yunxiao_read:
        parser.error("--yunxiao-archive-root requires --yunxiao-read")
    if args.yunxiao_archive_root and args.requirement_evidence_file:
        parser.error("--yunxiao-archive-root cannot be combined with --requirement-evidence-file; the archive snapshot is the evidence file")
    if args.yunxiao_archive_max_file_bytes < 0:
        parser.error("--yunxiao-archive-max-file-bytes must be non-negative")
    try:
        database_inspect = _read_structured_input(
            args.database_inspect_file,
            "database.inspect 输入",
        )
        database_change = _read_structured_input(
            args.database_change_file,
            "database.change-plan 输入",
        )
        knowledge_candidate = _read_structured_input(
            args.knowledge_candidate_file,
            "knowledge candidate 输入",
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.load_claude_settings:
        os.environ["HARNESS_LOAD_CLAUDE_SETTINGS"] = "1"
        load_claude_settings_env_if_requested()

    if args.demo:
        demand_text = DEMO_DEMAND.strip()
        mode = args.mode or "mock"
    elif args.demand:
        demand_text = args.demand.strip()
        mode = args.mode or os.environ.get("HARNESS_LLM_MODE") or "mock"
    elif args.demand_file:
        demand_text = Path(args.demand_file).read_text(encoding="utf-8").strip()
        mode = args.mode or os.environ.get("HARNESS_LLM_MODE") or "mock"
    else:
        print("请使用 --demo、--demand 或 --demand-file 指定需求内容。当前真实模型入口已冻结，默认仅运行本地 mock 技术验证。", file=sys.stderr)
        raise SystemExit(2)

    allow_mock = mode == "mock"
    with ephemeral_runtime_storage(
        prefix="workflow",
        retain_output=args.retain_output,
        output_dir=args.output_dir,
    ) as runtime:
        try:
            capability_service = build_capability_service(
                requested_mode=args.capability_routing,
                database_credentials_path=(
                    args.database_credentials_file
                    if database_inspect is not None
                    else None
                ),
            )
            if args.interaction_mode == "question":
                answer = CapabilityWorkflowOrchestrator(
                    capability_service,
                ).run_question(
                    text=demand_text,
                    allow_live_evidence=args.allow_live_evidence,
                    investigation_request=_is_investigation_request(demand_text),
                )
                print(
                    json.dumps(
                        {
                            "status": answer.status,
                            "events": list(answer.events),
                            "data": dict(answer.data),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            archived_requirement = None
            requirement_evidence_file = args.requirement_evidence_file or None
            yunxiao_read = args.yunxiao_read
            yunxiao_output_dir = runtime.output_dir / "_yunxiao_evidence" if args.yunxiao_read else None
            if args.yunxiao_archive_root:
                archive_max_file_bytes = (
                    None
                    if args.yunxiao_archive_no_size_limit
                    else args.yunxiao_archive_max_file_bytes
                )
                archived_requirement = sync_yunxiao_requirement_archive(
                    archive_root=args.yunxiao_archive_root,
                    yunxiao_url=args.yunxiao_url,
                    demand_text=demand_text,
                    include_comments=not args.yunxiao_ignore_comments,
                    change_note=args.yunxiao_archive_change_note,
                    max_file_bytes=archive_max_file_bytes,
                )
                requirement_evidence_file = archived_requirement["snapshot_path"]
                # The archive service has already issued the single permitted
                # read-only request. Reuse its local snapshot for this run.
                yunxiao_read = False
                yunxiao_output_dir = None
            runner = RequirementWorkflowRunner(
                mode=mode,
                allow_mock=allow_mock,
                max_retries=args.max_retries,
                capability_service=capability_service,
                visual_evidence_analyzer=(
                    FileVisualEvidenceAnalyzer(args.visual_evidence_file)
                    if args.visual_evidence_file
                    else None
                ),
            )
            result = runner.run(
                title=args.title,
                demand_text=demand_text,
                project_key=args.project_key,
                project_path=args.project_path,
                project_root=args.project_root,
                project_config=args.project_config,
                execution_mode=args.execution_mode,
                worktree_dir=args.worktree_dir,
                allowed_paths=args.allowed_path,
                verify_commands=args.verify_command,
                method_evidence_file=args.method_evidence_file,
                requirement_evidence_file=requirement_evidence_file,
                conversation_evidence_file=args.conversation_evidence_file or None,
                acceptance_contract_file=args.acceptance_contract_file or None,
                requirement_governance=args.requirement_governance,
                database_inspect=database_inspect,
                database_execute=args.database_execute,
                database_change=database_change,
                knowledge_candidate=knowledge_candidate,
                method_test_commands=args.method_test_command,
                ui_evidence_paths=args.ui_evidence_path,
                ui_capture_commands=args.ui_capture_command,
                max_edit_rounds=args.max_edit_rounds,
                apply_approved_diff=args.apply_approved_diff,
                pre_change_confirmation=args.pre_change_confirmation,
                review_commit=args.review_commit,
                review_base=args.review_base,
                yunxiao_read=yunxiao_read,
                yunxiao_include_comments=not args.yunxiao_ignore_comments,
                yunxiao_url=args.yunxiao_url,
                yunxiao_output_dir=yunxiao_output_dir,
                yunxiao_transaction_mode=args.yunxiao_transaction_mode,
                yunxiao_policy_config=args.yunxiao_policy_config,
                yunxiao_policy_key=args.yunxiao_policy_key,
                yunxiao_entity_kind=args.yunxiao_entity_kind,
                yunxiao_entity_id=args.yunxiao_entity_id,
                yunxiao_current_status=args.yunxiao_current_status,
                yunxiao_target_assignee=args.yunxiao_target_assignee,
                yunxiao_target_status=args.yunxiao_target_status,
                yunxiao_target_iteration=args.yunxiao_target_iteration,
                yunxiao_screenshots=args.yunxiao_screenshot,
                yunxiao_service_change_file=args.yunxiao_service_change_file,
                yunxiao_artifacts=args.yunxiao_artifact,
                yunxiao_write_confirm=args.yunxiao_write_confirm,
                yunxiao_human_confirmed=args.yunxiao_human_confirmed,
                yunxiao_write_transport=args.yunxiao_write_transport,
                yunxiao_write_scope=args.yunxiao_write_scope,
            )
            if archived_requirement is not None:
                record_requirement_archive_run(
                    ticket_dir=archived_requirement["ticket_dir"],
                    run_id=result.run_id,
                    status=result.status,
                    evaluation_status=result.evaluation_status,
                    markdown_report=result.markdown_report,
                    requirement_understanding=_artifact_content(
                        result.run_id,
                        *_REQUIREMENT_UNDERSTANDING_ARTIFACT_KINDS,
                    ),
                    solution_plan=_artifact_content(
                        result.run_id,
                        "fullstack_patch_plan_markdown",
                        "behavior_test_plan_markdown",
                    ),
                )
            output_path = write_run_outputs(result.run_id, runtime.output_dir)
        except Exception as exc:
            print(f"Harness 执行失败：{exc}", file=sys.stderr)
            raise SystemExit(1)

        print(result.markdown_report)
        print()
        print(f"Run ID: {result.run_id}")
        print(f"Status: {result.status}")
        print(f"Evaluation: {result.evaluation_status}")
        if runtime.retained:
            print(f"Output: {output_path}")
        else:
            print("Output: 本次运行使用临时存储，命令结束后自动删除。传 --retain-output 才会保留。")
        if mode == "mock":
            print("WARNING: 当前为 mock 模式，仅用于演示流程，不可用于真实业务判断。")


if __name__ == "__main__":
    from app.runtime_bootstrap import reexec_in_project_venv

    reexec_in_project_venv(PROJECT_ROOT)
    main()
