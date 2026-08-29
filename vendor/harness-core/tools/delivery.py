from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.delivery_closure import (
    PLUGIN_REQUIRED_MESSAGE,
    DeliveryClosure,
    DeliveryError,
    DeliveryPolicy,
    DeliveryRequest,
    build_delivery_capability_service,
)
from app.delivery_gitlab import build_gitlab_delivery_executor
from app.delivery_github import build_github_delivery_executor
from app.capability_contracts import CapabilityAuthorization, CapabilityRequest, MutationLevel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HIS Harness 原源码 Git 交付闭环。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="检查原源码工作区并生成不可变交付计划")
    prepare.add_argument("--entity-kind", choices=["requirement", "bug", "task"], required=True)
    prepare.add_argument("--entity-id", required=True)
    prepare.add_argument("--title", required=True)
    prepare.add_argument("--url", required=True)
    prepare.add_argument("--project-path", required=True)
    prepare.add_argument("--diff-file", required=True)
    prepare.add_argument("--allowed-path", action="append", required=True)
    prepare.add_argument("--verify-command", action="append", default=[])
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument(
        "--base-branch",
        help="覆盖本次不可变交付计划的源码基线分支；不改变默认规则包",
    )
    prepare.add_argument("--task-id", type=int)
    prepare.add_argument("--source-run-id", type=int)
    prepare.add_argument("--push-feature", action="store_true")
    prepare.add_argument("--integrate-rc", action="store_true")
    prepare.add_argument("--push-rc", action="store_true")
    prepare.add_argument(
        "--create-gitlab-mr",
        action="store_true",
        help="从当前仓库 origin 自动解析 GitLab 项目并创建任务分支到 RC 的 MR",
    )
    prepare.add_argument(
        "--gitlab-action-file",
        help="包含一个已声明 GitLab MR 创建或评论动作的 JSON 文件",
    )
    prepare.add_argument(
        "--create-github-pr",
        action="store_true",
        help="从当前仓库 origin 自动解析 GitHub 仓库并创建任务分支到 RC 的 PR",
    )
    prepare.add_argument(
        "--github-action-file",
        help="包含一个已声明 GitHub PR 创建或评论动作的 JSON 文件",
    )
    prepare.add_argument("--json", action="store_true")

    show = subparsers.add_parser("show", help="查看交付事务、计划、事件和下一步")
    show.add_argument("--transaction-id", type=int, required=True)
    show.add_argument("--json", action="store_true")

    accept_release = subparsers.add_parser("accept-release", help="登记 release 真实运行时验收")
    add_acceptance_arguments(accept_release)

    first = subparsers.add_parser(
        "first-confirmation",
        help="执行已经明确的交付计划：本地任务 commit、任务推送和 RC 集成会按计划连续进行",
    )
    first.add_argument("--transaction-id", type=int, required=True)
    first.add_argument("--confirm", action="store_true", required=True)
    first.add_argument("--json", action="store_true")

    accept_rc = subparsers.add_parser("accept-rc", help="登记 RC 真实运行时二次验收")
    add_acceptance_arguments(accept_rc)

    second = subparsers.add_parser(
        "second-confirmation",
        help="RC 运行时验收后，执行计划内的 RC 推送与 GitLab 写入",
    )
    second.add_argument("--transaction-id", type=int, required=True)
    second.add_argument("--confirm", action="store_true", required=True)
    second.add_argument("--json", action="store_true")
    return parser


def add_acceptance_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--transaction-id", type=int, required=True)
    parser.add_argument("--status", choices=["passed", "failed"], default="passed")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--verifier", default="user")
    parser.add_argument("--json", action="store_true")


def prepare_policy(base_branch: str | None) -> DeliveryPolicy:
    policy = DeliveryPolicy.from_rule_pack()
    if not base_branch:
        return policy
    payload = asdict(policy)
    payload["base_branch"] = base_branch
    return DeliveryPolicy.from_payload(payload)


def _route_delivery_capability(
    service,
    *,
    capability: str,
    mutation_level: MutationLevel,
    scope: tuple[str, ...],
    input_data: dict,
) -> dict:
    result = service.route(
        CapabilityRequest(
            request_id=str(uuid.uuid4()),
            capability=capability,
            provider="his-engineering",
            mode="apply",
            mutation_level=mutation_level,
            authorization=CapabilityAuthorization(explicit=True, scope=scope),
            input=input_data,
            context={},
        )
    ).result
    if result.get("status") == "success":
        return dict(result.get("data") or {})
    if result.get("changed"):
        raise DeliveryError(
            capability.replace(".", "_") + "_recovery_required",
            "交付动作可能已发生；请先执行 show 查看可恢复证据。",
        )
    raise DeliveryError(
        capability.replace(".", "_") + "_blocked",
        "交付 capability 已阻断；未继续执行后续动作。",
    )


def _delivery_input(closure: DeliveryClosure, transaction_id: int, plan_hash: str) -> dict:
    return {
        "delivery_db": str(closure.store.path),
        "transaction_id": transaction_id,
        "approved_plan_hash": plan_hash,
    }


def execute_stage_two(
    closure: DeliveryClosure,
    *,
    transaction_id: int,
    plan: dict,
    service=None,
    executor_factory=build_gitlab_delivery_executor,
    github_executor_factory=build_github_delivery_executor,
) -> dict:
    """Execute one confirmed immutable plan through its declared L4 gates."""
    plan_hash = plan.get("plan_hash")
    if not isinstance(plan_hash, str):
        raise ValueError("delivery_plan_invalid")
    service = service or build_delivery_capability_service()
    push_result = _route_delivery_capability(
        service,
        capability="git.push",
        mutation_level=MutationLevel.L4,
        scope=("repository:push", "capability:git.push"),
        input_data={**_delivery_input(closure, transaction_id, plan_hash), "phase": "rc"},
    )
    if push_result.get("status") == "blocked":
        raise DeliveryError(
            str(push_result.get("code") or "rc_runtime_acceptance_pending"),
            str(push_result.get("message") or "RC 运行时验收未通过或已失效。"),
        )
    actions = plan.get("actions")
    gitlab_action = actions.get("gitlab_write") if isinstance(actions, dict) else None
    github_action = actions.get("github_write") if isinstance(actions, dict) else None
    if isinstance(gitlab_action, dict) and isinstance(github_action, dict):
        raise DeliveryError(
            "multiple_hosting_writes_not_allowed",
            "一个交付计划不能同时执行 GitLab 和 GitHub 写入。",
        )
    if not isinstance(gitlab_action, dict) and not isinstance(github_action, dict):
        return push_result
    if isinstance(gitlab_action, dict):
        _route_delivery_capability(
            service,
            capability="gitlab.write",
            mutation_level=MutationLevel.L4,
            scope=("gitlab:write", "capability:gitlab.write"),
            input_data=_delivery_input(closure, transaction_id, plan_hash),
        )
        receipt = executor_factory()(
            transaction_id=transaction_id,
            approved_plan_hash=plan_hash,
            gitlab_action=gitlab_action,
            plan=plan,
        )
        return closure.complete_declared_gitlab_action(
            transaction_id,
            approved_plan_hash=plan_hash,
            receipt=receipt,
        )
    _route_delivery_capability(
        service,
        capability="github.write",
        mutation_level=MutationLevel.L4,
        scope=("github:write", "capability:github.write"),
        input_data=_delivery_input(closure, transaction_id, plan_hash),
    )
    receipt = github_executor_factory()(
        transaction_id=transaction_id,
        approved_plan_hash=plan_hash,
        github_action=github_action,
        plan=plan,
    )
    return closure.complete_declared_github_action(
        transaction_id,
        approved_plan_hash=plan_hash,
        receipt=receipt,
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        closure = DeliveryClosure(
            policy=prepare_policy(args.base_branch) if args.command == "prepare" else None
        )
        if args.command == "prepare":
            diff_path = Path(args.diff_file).expanduser().resolve()
            gitlab_action = None
            if args.gitlab_action_file:
                action_path = Path(args.gitlab_action_file).expanduser().resolve()
                gitlab_action = json.loads(action_path.read_text(encoding="utf-8"))
            github_action = None
            if args.github_action_file:
                action_path = Path(args.github_action_file).expanduser().resolve()
                github_action = json.loads(action_path.read_text(encoding="utf-8"))
            result = closure.prepare(
                DeliveryRequest(
                    entity_kind=args.entity_kind,
                    entity_id=args.entity_id,
                    title=args.title,
                    url=args.url,
                    project_path=args.project_path,
                    expected_diff=diff_path.read_text(encoding="utf-8"),
                    allowed_paths=args.allowed_path,
                    output_dir=args.output_dir,
                    task_id=args.task_id,
                    source_run_id=args.source_run_id,
                    verify_commands=args.verify_command,
                    push_feature=args.push_feature,
                    cherry_pick_integration=args.integrate_rc,
                    push_integration=args.push_rc,
                    create_gitlab_merge_request=args.create_gitlab_mr,
                    gitlab_action=gitlab_action,
                    create_github_pull_request=args.create_github_pr,
                    github_action=github_action,
                )
            )
        elif args.command == "show":
            result = closure.show(args.transaction_id)
            result["next_action"] = next_action_for_state(str(result["transaction"].get("state") or ""))
        elif args.command == "accept-release":
            result = closure.record_runtime_acceptance(
                args.transaction_id,
                phase="release",
                status=args.status,
                summary=args.summary,
                verifier=args.verifier,
            )
        elif args.command == "accept-rc":
            result = closure.record_runtime_acceptance(
                args.transaction_id,
                phase="rc",
                status=args.status,
                summary=args.summary,
                verifier=args.verifier,
            )
        elif args.command == "first-confirmation":
            current = closure.show(args.transaction_id)
            plan = current["plan"]
            plan_hash = plan["plan_hash"]
            try:
                service = build_delivery_capability_service()
                request = CapabilityRequest(
                    request_id=str(uuid.uuid4()),
                    capability="git.commit-local",
                    provider="his-engineering",
                    mode="apply",
                    mutation_level=MutationLevel.L3,
                    authorization=CapabilityAuthorization(
                        explicit=True,
                        scope=("repository:commit-local",),
                    ),
                    input={
                        "delivery_db": str(closure.store.path),
                        "transaction_id": args.transaction_id,
                        "approved_plan_hash": plan_hash,
                    },
                    context={},
                )
                capability_result = service.route(request).result
            except DeliveryError:
                raise
            except Exception as exc:
                raise DeliveryError(
                    "git_commit_runtime_unavailable",
                    "本地 commit capability 不可用；未确认任何仓库改动。",
                ) from exc
            if capability_result.get("status") != "success":
                if (
                    capability_result.get("summary") == "CAPABILITY_ENTRYPOINT_INVALID"
                    or (capability_result.get("audit") or {}).get("error_code")
                    == "CAPABILITY_ENTRYPOINT_INVALID"
                ):
                    raise DeliveryError(
                        "his_engineering_plugin_required",
                        PLUGIN_REQUIRED_MESSAGE,
                    )
                if capability_result.get("changed"):
                    raise DeliveryError(
                        "git_commit_recovery_required",
                        "本地任务分支可能已创建，但结果需要恢复核对；请先执行 show 查看事务。",
                    )
                raise DeliveryError(
                    "git_commit_local_blocked",
                    "本地 commit capability 已阻断；请查看交付事务后重试。",
                )
            stage_one = dict(capability_result.get("data") or {})
            if plan["actions"].get("push_feature") or plan["actions"].get("cherry_pick_integration"):
                remote_phase = _route_delivery_capability(
                    service,
                    capability="git.push",
                    mutation_level=MutationLevel.L4,
                    scope=("repository:push", "capability:git.push"),
                    input_data={
                        **_delivery_input(closure, args.transaction_id, plan_hash),
                        "phase": "pre_rc",
                    },
                )
                task_push = dict(remote_phase.get("task_push") or {})
                integration = dict(remote_phase.get("integration") or {})
            else:
                task_push = {"status": "not_requested", "pushed": False}
                integration = {"status": "not_requested", "integrated": False}
            result = {
                "stage_one": stage_one,
                "task_push": task_push,
                "integration": integration,
                "rc_push_executed": False,
            }
        elif args.command == "second-confirmation":
            current = closure.show(args.transaction_id)
            result = execute_stage_two(
                closure,
                transaction_id=args.transaction_id,
                plan=current["plan"],
            )
        else:
            parser.error(f"unsupported command: {args.command}")
            return 2
    except (DeliveryError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, DeliveryError) else exc.__class__.__name__
        print(f"delivery blocked [{code}]: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_result(args.command, result))
    return 0


def next_action_for_state(state: str) -> str:
    return {
        "waiting_release_runtime_acceptance": "在 release 启动项目验证后登记 accept-release",
        "release_runtime_accepted": "执行 first-confirmation --confirm，重新核验后创建任务提交并按计划推送",
        "task_commit_created": "任务 commit 已创建；若计划声明任务推送或 RC 集成，已执行首阶段远端交付",
        "waiting_rc_runtime_acceptance": "RC 集成已完成；登记真实 RC 运行时验收后执行 second-confirmation --confirm",
        "rc_runtime_accepted": "执行 second-confirmation --confirm，重新核验后推送 RC 并执行计划内 GitLab/GitHub 动作",
        "gitlab_delivery_pending": "等待计划内 GitLab 动作的 Provider 回读核验回执",
        "github_delivery_pending": "等待计划内 GitHub 动作的 Provider 回读核验回执",
        "completed": "交付已完成",
    }.get(state, "查看事件和 last_error")


def render_result(command: str, result: dict) -> str:
    if command == "prepare":
        transaction = result.get("transaction") or {}
        plan = result.get("plan") or {}
        return "\n".join(
            [
                "Git 交付计划已生成，当前未执行分支、commit 或远端写入。",
                f"Transaction ID: {transaction.get('id')}",
                f"State: {transaction.get('state')}",
                f"Task branch: {plan.get('task_branch')}",
                f"RC: {plan.get('integration_branch')}",
                f"Output: {transaction.get('output_dir')}",
            ]
        )
    if command == "show":
        transaction = result.get("transaction") or {}
        return "\n".join(
            [
                f"Transaction ID: {transaction.get('id')}",
                f"State: {transaction.get('state')}",
                f"Next: {result.get('next_action')}",
                f"Last error: {transaction.get('last_error') or '-'}",
            ]
        )
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
