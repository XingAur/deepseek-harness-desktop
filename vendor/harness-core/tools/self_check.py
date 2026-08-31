from __future__ import annotations

import argparse
import atexit
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.dont_write_bytecode = True


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.plugin_inventory import resolve_plugin_source_root


# A self-check must never initialize or migrate the caller's persistent Harness
# database. Keep the isolated directory alive for this process and inherited
# local fixture subprocesses; an explicit test-only path remains respected.
_SELF_CHECK_DATABASE_TEMP = None
if "HARNESS_DB_PATH" not in os.environ:
    _SELF_CHECK_DATABASE_TEMP = tempfile.TemporaryDirectory(
        prefix="his_harness_self_check_db_"
    )
    os.environ["HARNESS_DB_PATH"] = str(
        Path(_SELF_CHECK_DATABASE_TEMP.name) / "harness.sqlite"
    )

# self_check is a local-only verification entrypoint. Let its compatibility
# imports resolve the repository's frozen plugin copies without weakening the
# production adapters' fail-closed default or leaking test routing to callers.
_SELF_CHECK_PLUGIN_ENV = {
    "HARNESS_ENABLE_STAGED_PLUGIN_TESTS": os.environ.get(
        "HARNESS_ENABLE_STAGED_PLUGIN_TESTS"
    ),
    "HARNESS_STAGED_PLUGIN_ROOT": os.environ.get("HARNESS_STAGED_PLUGIN_ROOT"),
}
_SELF_CHECK_PLUGIN_ROOT = resolve_plugin_source_root(
    PROJECT_ROOT.parent,
    Path("/Users/lym/plugins"),
)
os.environ["HARNESS_ENABLE_STAGED_PLUGIN_TESTS"] = "1"
os.environ["HARNESS_STAGED_PLUGIN_ROOT"] = str(_SELF_CHECK_PLUGIN_ROOT)

from app import database
from app.acceptance_contracts import execute_acceptance_contract
from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityRequest,
    CapabilityResult,
    MutationLevel,
)
from app.capability_permissions import (
    evaluate_capability_permission,
    evaluate_capability_result_permission,
)
from app.capability_registry import (
    CapabilityManifestError,
    CapabilityRegistry,
)
from app.plugin_replay_suite import (
    load_plugin_replay_manifest,
    run_plugin_replay_suite,
)
from app.requirement_governance import assess_requirement
from app.harness import TEAM_KEY, RequirementWorkflowRunner, write_run_outputs
from app.acceptance_matrix import build_acceptance_matrix
from app.behavior_acceptance import build_behavior_acceptance
from app.clarification_gate import evaluate_patch_readiness
from app.core_closure import build_requirement_contract, review_final_diff
from app.dynamic_planning import (
    DynamicPlanningRequest,
    PlanningSignals,
    build_dynamic_plan,
    write_dynamic_plan_outputs,
)
from app.dynamic_plan_registry import DynamicPlanRegistry, write_dynamic_registry_outputs
from app.dynamic_scheduler import DynamicDryRunScheduler, write_dynamic_schedule_outputs
from app.node_runtime import ControlledNodeRuntime, write_node_runtime_outputs
from app.executor_runtime import SandboxExecutorRuntime, write_executor_runtime_outputs
from app.mock_agent_runtime import (
    DeterministicMockAgentRuntime,
    write_mock_agent_runtime_outputs,
)
from app.model_invocation_runtime import (
    OfflineModelInvocationRuntime,
    write_model_invocation_outputs,
)
from app.model_dag_runtime import OfflineModelDagRuntime, write_model_dag_outputs
from app.model_provider_runtime import (
    MODEL_PROVIDER_RUNTIME_SCHEMA_VERSION,
    ControlledModelProviderRuntime,
    write_model_provider_smoke_outputs,
)
from app.change_context_contracts import (
    ChangeContextGateResult,
    ChangeContextLayer,
    ChangeContextPack,
    TaskBinding,
    content_hash,
)
from app.change_context_execution import (
    ChangeContextExecutionBinding,
    ChangeContextExecutionVerifier,
)
from app.change_context_gate import ChangeContextGate
from app.change_context_projection import ChangeContextProjectionService
from app.fullstack_executor import FullstackExecutionOptions, FullstackWorktreeExecutor
from app.precommit_verifier import PrecommitVerificationOptions, PrecommitVerifier
from app.llm_client import (
    MockLLMClient,
    get_llm_client,
    is_smoke_response_ok,
    load_claude_settings_env_if_requested,
    redact_secrets,
    smoke_test,
)
from app.review_executor import ReviewExecutionOptions, ReviewWorktreeExecutor, build_review_context
from app.runtime_storage import ephemeral_runtime_storage
from app.single_demand_trial import build_single_demand_trial_package
from app.task_manager import TaskCreateOptions, TaskDashboardFilters, TaskExistingRunOptions, TaskManager, TaskPrecommitRerunOptions, build_latest_artifacts
import app.task_manager as task_manager_module
from app.worktree_executor import WorktreeCodeExecutor, WorktreeExecutionOptions, WorktreeExecutionResult, validate_patch
from app.yunxiao_read import (
    collect_inline_files_from_work_item,
    collect_yunxiao_evidence,
    credentials_file_permission_issue,
    extract_description_evidence,
    load_yunxiao_credentials,
)
from app.yunxiao_transaction import (
    DEFAULT_ENABLED_ACTIONS,
    YunxiaoEntityRef,
    YunxiaoPolicy,
    YunxiaoTransactionManager,
    YunxiaoTransactionRequest,
    build_yunxiao_transaction_plan,
    load_yunxiao_write_credentials,
)

for _self_check_key, _self_check_value in _SELF_CHECK_PLUGIN_ENV.items():
    if _self_check_value is None:
        os.environ.pop(_self_check_key, None)
    else:
        os.environ[_self_check_key] = _self_check_value
del _self_check_key, _self_check_value, _SELF_CHECK_PLUGIN_ENV


class _SelfCheckContextRepository:
    def __init__(self, pack: ChangeContextPack, payloads: dict[str, dict[str, object]]) -> None:
        self.pack = pack
        self.layers = {
            layer.layer_id: (layer, payloads[layer.layer_type])
            for layer in pack.layers
        }

    def get_pack(self, pack_id: str) -> ChangeContextPack:
        if pack_id != self.pack.pack_id:
            raise KeyError(pack_id)
        return self.pack

    def get_layer(self, layer_id: str):
        if layer_id not in self.layers:
            raise KeyError(layer_id)
        return self.layers[layer_id]

    def get_successor_pack_id(self, pack_id: str) -> str:
        if pack_id != self.pack.pack_id:
            raise KeyError(pack_id)
        return ""

    def record_projection_metric(self, **kwargs) -> None:
        del kwargs


class _SelfCheckChangeContext:
    """Deterministic, sealed ChangeContext used only by local self-check fixtures."""

    def __init__(self) -> None:
        payloads = {
            "project_graph": {
                "schema_version": "project-graph.v1",
                "projects": [{"name": "self-check", "role": "application", "exists": True}],
                "relationships": [],
                "explicit_scope": True,
            },
            "change_scope": {
                "schema_version": "change-scope.v1",
                "provider": "self-check",
                "ticket_id": "SELF-CHECK-1",
                "requirement_revision": "sealed-fixture-v1",
                "current_user_correction": "execute only the isolated self-check fixture",
                "calibrated_scope": {"do": "isolated fixture validation", "do_not": ["external writes"]},
            },
            "code_graph": {
                "schema_version": "code-graph.v1",
                "target_paths": ["src/App.js", "src/view.vue"],
                "tests": ["self-check"],
                "call_edges": [],
                "file_hashes": [],
            },
            "data_graph": {
                "schema_version": "data-graph.v1",
                "decision": "not_applicable",
                "reason": "self-check fixtures do not access business data",
                "missing": [],
                "conflicts": [],
            },
        }
        layers = []
        for layer_type, payload in payloads.items():
            digest = content_hash(payload)
            layers.append(
                ChangeContextLayer.create(
                    layer_type=layer_type,
                    status="not_applicable" if layer_type == "data_graph" else "complete",
                    payload=payload,
                    source_fingerprint=digest,
                    artifact_ref=f"artifact://sha256/{digest.removeprefix('sha256:')}",
                    evidence_refs=(f"evidence://{layer_type}/self-check",),
                    policy_rule_ids=("CTX-SELF-CHECK-SEALED",),
                    blockers=(),
                )
            )
        gate_result = ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ())
        self.pack = ChangeContextPack.create(
            pack_version=1,
            status="ready",
            task_binding=TaskBinding(
                "self-check",
                "SELF-CHECK-1",
                "sealed-fixture-v1",
                "sha256:" + "a" * 64,
            ),
            required_layers=("project_graph", "change_scope", "code_graph"),
            layers=layers,
            gate=gate_result,
        )
        self.repository = _SelfCheckContextRepository(self.pack, payloads)
        self.gate = ChangeContextGate()
        self.projections = {
            role: ChangeContextProjectionService().render(
                pack=self.pack,
                layer_payloads=payloads,
                role=role,
            )
            for role in ("implementation", "review")
        }
        self.verifier = ChangeContextExecutionVerifier(
            repository=self.repository,
            gate=self.gate,
        )

    def bind(self, options, role: str) -> None:
        projection = self.projections[role]
        binding = ChangeContextExecutionBinding(
            pack_id=self.pack.pack_id,
            projection_hash=projection.projection_hash,
            layer_hashes={layer.layer_type: layer.content_hash for layer in self.pack.layers},
        )
        options.change_context_binding = binding.to_dict()
        options.change_context_projection = projection.to_dict()


_SELF_CHECK_CHANGE_CONTEXT = _SelfCheckChangeContext()


class _SelfCheckWorktreeExecutor:
    def __init__(self, llm_client) -> None:
        self.delegate = WorktreeCodeExecutor(
            llm_client,
            change_context_verifier=_SELF_CHECK_CHANGE_CONTEXT.verifier,
        )

    def execute(self, options):
        _SELF_CHECK_CHANGE_CONTEXT.bind(options, "implementation")
        return self.delegate.execute(options)


class _SelfCheckFullstackExecutor:
    def __init__(self) -> None:
        self.delegate = FullstackWorktreeExecutor(
            change_context_verifier=_SELF_CHECK_CHANGE_CONTEXT.verifier,
        )

    def execute(self, options):
        _SELF_CHECK_CHANGE_CONTEXT.bind(options, "implementation")
        return self.delegate.execute(options)


class _SelfCheckPrecommitVerifier:
    def __init__(self) -> None:
        self.delegate = PrecommitVerifier(
            change_context_verifier=_SELF_CHECK_CHANGE_CONTEXT.verifier,
        )

    def execute(self, options):
        _SELF_CHECK_CHANGE_CONTEXT.bind(options, "review")
        return self.delegate.execute(options)


class _SelfCheckTaskManager(TaskManager):
    def rerun_precommit(self, options):
        previous = task_manager_module.PrecommitVerifier
        task_manager_module.PrecommitVerifier = _SelfCheckPrecommitVerifier
        try:
            return super().rerun_precommit(options)
        finally:
            task_manager_module.PrecommitVerifier = previous


REQUIRED_FILES = [
    "app/acceptance_matrix.py",
    "app/acceptance_contracts.py",
    "app/behavior_acceptance.py",
    "app/capability_contracts.py",
    "app/capability_permissions.py",
    "app/capability_registry.py",
    "app/capability_runtime.py",
    "app/capability_service.py",
    "app/database.py",
    "app/enterprise_gate.py",
    "app/clarification_gate.py",
    "app/contract_plugins.py",
    "app/core_closure.py",
    "app/delivery_closure.py",
    "app/dynamic_planning.py",
    "app/dynamic_plan_registry.py",
    "app/dynamic_scheduler.py",
    "app/node_runtime.py",
    "app/executor_runtime.py",
    "app/mock_agent_runtime.py",
    "app/model_invocation_runtime.py",
    "app/model_dag_runtime.py",
    "app/model_provider_runtime.py",
    "app/evaluator.py",
    "app/harness.py",
    "app/interaction_evidence.py",
    "app/method_test_runner.py",
    "app/ui_capture_template.py",
    "app/ui_evidence_runner.py",
    "app/llm_client.py",
    "app/project_context.py",
    "app/plugin_replay_suite.py",
    "app/real_replay_suite.py",
    "app/release_bundle.py",
    "app/fullstack_executor.py",
    "app/harness_config.py",
    "app/precommit_verifier.py",
    "app/pg_evidence.py",
    "app/requirement_provider.py",
    "app/requirement_governance.py",
    "app/review_executor.py",
    "app/requirement_calibration.py",
    "app/single_demand_trial.py",
    "app/task_manager.py",
    "app/task_capability_routing.py",
    "app/technical_decision.py",
    "app/worktree_executor.py",
    "app/worktree_lifecycle.py",
    "app/yunxiao_read.py",
    "app/yunxiao_transaction.py",
    "tools/fixture_node_worker.py",
    "config/profiles.example.json",
    "config/dynamic_planning.example.json",
    "config/pg_evidence_profiles.example.json",
    "config/model_providers.example.json",
    "config/rule_packs/dfhis.default.json",
    "config/contract_plugins/dfhis.common.v1.json",
    "config/yunxiao.example.json",
    "fixtures/acceptance_contracts/dfhis-31558-ordering.json",
    "fixtures/replay/real_requirements_v1.json",
    "fixtures/replay/plugin_migration_v1.json",
    "harnesses/his_requirement_workflow.py",
    "tools/cleanup_worktrees.py",
    "tools/behavior_check.py",
    "tools/config_check.py",
    "tools/database_admin.py",
    "tools/delivery.py",
    "tools/dynamic_plan.py",
    "tools/enterprise_gate.py",
    "tools/interaction_evidence_check.py",
    "tools/precommit_verify.py",
    "tools/plugin_replay_suite.py",
    "tools/replay_suite.py",
    "tools/pg_evidence.py",
    "tools/requirement_provider_check.py",
    "tools/task_manager.py",
    "tools/ui_capture_template.py",
    "tools/yunxiao_read_check.py",
    "tools/build_release_bundle.py",
    "prompts/default_experts.json",
    "real_precommit_trial_template.md",
    "CHANGELOG.md",
    "scope_warning_policy.md",
    "run.py",
    "config/schemas/capability_manifest.v1.json",
    "config/schemas/capability_request.v1.json",
    "config/schemas/capability_result.v1.json",
    "config/schemas/requirement_governance.v1.json",
    "config/schemas/plugin_replay_manifest.v1.json",
]

FORMAL_PLUGIN_ROOT = Path("/Users/lym/plugins")
REQUIRED_PLUGIN_FILES = {
    "his-harness-core": (
        ".codex-plugin/plugin.json",
        "capabilities.json",
    ),
    "his-engineering": (
        ".codex-plugin/plugin.json",
        "capabilities.json",
    ),
    "his-knowledge": (
        ".codex-plugin/plugin.json",
        "capabilities.json",
    ),
    "yunxiao": (
        ".codex-plugin/plugin.json",
        "capabilities.json",
    ),
}


def resolve_required_plugin_files(
    *,
    repository_root: Path,
    formal_plugin_root: Path = FORMAL_PLUGIN_ROOT,
) -> tuple[tuple[str, Path], ...]:
    plugin_root = resolve_plugin_source_root(
        repository_root,
        formal_plugin_root,
    )
    return tuple(
        (
            f"plugin:{plugin_name}/{relative_file}",
            plugin_root / plugin_name / relative_file,
        )
        for plugin_name, relative_files in REQUIRED_PLUGIN_FILES.items()
        for relative_file in relative_files
    )


SAMPLES = [
    {
        "key": "frontend_field",
        "title": "门诊候诊列表字段展示需求",
        "demand": """
门诊候诊列表需要新增“患者年龄”字段。
护士进入页面后能看到每位患者的年龄，年龄为空时显示“-”。
接口如果暂时没有返回年龄字段，前端不能报错。
要求不影响候诊列表查询和叫号操作。
""".strip(),
    },
    {
        "key": "backend_flow",
        "title": "住院医嘱审核流程调整需求",
        "demand": """
住院医嘱提交后，如果医生修改了药品剂量，需要后端重新触发护士站审核状态。
已有审核通过的医嘱不能被静默覆盖，需要记录操作日志。
接口需要兼容旧版本前端，避免旧端未传新字段时报错。
""".strip(),
    },
    {
        "key": "insurance_settlement",
        "title": "医保结算高风险需求",
        "demand": """
医保结算完成后，收费明细报表需要展示医保基金支付、自费金额和统筹支付字段。
报表口径必须与医保结算回写结果一致。
如果医保回写失败，不能生成错误的结算完成状态，也不能影响对账。
需要明确人工核对和异常处理方案。
""".strip(),
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HIS AI Harness self check.")
    parser.add_argument(
        "--mode",
        choices=["openai", "anthropic", "mock"],
        default="mock",
        help="self-check mode; real model modes are frozen until enterprise core acceptance passes",
    )
    parser.add_argument("--output-dir", default="self_check_runs", help="where self_check_report.md and result json are written")
    parser.add_argument(
        "--retain-output",
        action="store_true",
        help="keep self-check fixtures and reports; default uses temporary storage and deletes it after the process exits",
    )
    parser.add_argument("--max-retries", type=int, default=2, help="automatic evaluator retry rounds")
    parser.add_argument(
        "--load-claude-settings",
        action="store_true",
        help="load Anthropic-compatible env vars from ~/.claude/settings.json without printing secrets",
    )
    parser.add_argument(
        "--allow-non-zhipu-anthropic",
        action="store_true",
        help="allow non-Zhipu Anthropic-compatible gateways for protocol debugging only",
    )
    args = parser.parse_args()
    requested_output_dir = Path(args.output_dir).expanduser()

    if args.load_claude_settings:
        os.environ["HARNESS_LOAD_CLAUDE_SETTINGS"] = "1"
    if args.allow_non_zhipu_anthropic:
        os.environ["HARNESS_ALLOW_NON_ZHIPU_ANTHROPIC"] = "1"

    non_zhipu_allowed = is_truthy_env("HARNESS_ALLOW_NON_ZHIPU_ANTHROPIC")

    runtime_storage = ephemeral_runtime_storage(
        prefix="self_check",
        retain_output=args.retain_output,
        output_dir=args.output_dir,
    )
    runtime = runtime_storage.__enter__()
    if not runtime.retained:
        atexit.register(runtime_storage.__exit__, None, None, None)
    output_dir = runtime.output_dir
    result = {
        "mode": args.mode,
        "business_valid": args.mode == "openai" or (args.mode == "anthropic" and not non_zhipu_allowed),
        "status": "running",
        "preflight": [],
        "project_context": {},
        "samples": [],
        "patch_readiness_checks": [],
        "requirement_calibration_checks": [],
        "core_closure_checks": [],
        "acceptance_contract_checks": [],
        "acceptance_matrix_checks": [],
        "behavior_acceptance_checks": [],
        "interaction_evidence_checks": [],
        "configuration_checks": [],
        "requirement_provider_checks": [],
        "dynamic_planning_checks": [],
        "dynamic_plan_registry_checks": [],
        "dynamic_scheduler_checks": [],
        "node_runtime_checks": [],
        "sandbox_executor_checks": [],
        "mock_agent_checks": [],
        "model_invocation_checks": [],
        "model_dag_checks": [],
        "pg_evidence_checks": [],
        "task_manager_checks": [],
        "yunxiao_transaction_checks": [],
        "worktree_checks": [],
        "review_checks": [],
        "capability_registry_checks": [],
        "capability_permission_checks": [],
        "yunxiao_readonly_plugin_checks": [],
        "requirement_governance_checks": [],
        "git_plugin_checks": [],
        "database_plugin_checks": [],
        "knowledge_plugin_checks": [],
        "plugin_replay_checks": [],
        "summary": "",
    }

    preflight_ok = run_preflight(mode=args.mode, output_dir=output_dir, result=result)
    if not preflight_ok:
        result["status"] = "failed"
        result["summary"] = "预检失败，未进入样例运行。"
        write_self_check_outputs(output_dir, result)
        visible_output_dir = output_dir
        if not runtime.retained:
            visible_output_dir = preserve_failure_outputs(
                source_dir=output_dir,
                requested_output_dir=requested_output_dir,
                run_namespace=runtime.run_namespace,
            )
        print(f"Self-check failed: {result['summary']}")
        print(f"Report: {visible_output_dir / 'self_check_report.md'}")
        print(f"JSON: {visible_output_dir / 'self_check_result.json'}")
        raise SystemExit(1)

    print("[self-check] 预检通过，开始运行需求样例。", flush=True)

    fixture_project = create_fixture_project(output_dir / "fixture_his_project")
    result["project_context"] = {
        "path": str(fixture_project),
        "mode": "read_only_fixture",
        "note": "自测使用本地 fixture 项目验证只读扫描，不写业务代码、不触发 Git/CI/发布。",
    }

    all_passed = True
    for sample in SAMPLES:
        sample_result = run_sample(
            sample=sample,
            mode=args.mode,
            output_dir=output_dir,
            max_retries=args.max_retries,
            project_path=fixture_project,
        )
        result["samples"].append(sample_result)
        if (
            sample_result["status"] != "success"
            or sample_result["evaluation_status"] not in {"pass", "analysis_complete_readonly"}
            or sample_result.get("read_only_safety") != "pass"
            or sample_result.get("acceptance_matrix") != "pass"
        ):
            all_passed = False

    print("[self-check] 需求样例完成，开始运行治理与验收检查。", flush=True)

    acceptance_matrix_checks = run_acceptance_matrix_checks(output_dir=output_dir, fixture_project=fixture_project)
    result["acceptance_matrix_checks"] = acceptance_matrix_checks
    if any(item["status"] != "pass" for item in acceptance_matrix_checks):
        all_passed = False
    requirement_calibration_checks = run_requirement_calibration_checks(output_dir=output_dir, fixture_project=fixture_project)
    result["requirement_calibration_checks"] = requirement_calibration_checks
    if any(item["status"] != "pass" for item in requirement_calibration_checks):
        all_passed = False
    core_closure_checks = run_core_closure_checks()
    result["core_closure_checks"] = core_closure_checks
    if any(item["status"] != "pass" for item in core_closure_checks):
        all_passed = False
    acceptance_contract_checks = run_acceptance_contract_checks()
    result["acceptance_contract_checks"] = acceptance_contract_checks
    if any(item["status"] != "pass" for item in acceptance_contract_checks):
        all_passed = False
    behavior_acceptance_checks = run_behavior_acceptance_checks()
    result["behavior_acceptance_checks"] = behavior_acceptance_checks
    if any(item["status"] != "pass" for item in behavior_acceptance_checks):
        all_passed = False
    interaction_evidence_checks = run_interaction_evidence_checks(output_dir=output_dir)
    result["interaction_evidence_checks"] = interaction_evidence_checks
    if any(item["status"] != "pass" for item in interaction_evidence_checks):
        all_passed = False
    configuration_checks = run_configuration_checks(output_dir=output_dir)
    result["configuration_checks"] = configuration_checks
    if any(item["status"] != "pass" for item in configuration_checks):
        all_passed = False
    requirement_provider_checks = run_requirement_provider_checks(output_dir=output_dir)
    result["requirement_provider_checks"] = requirement_provider_checks
    if any(item["status"] != "pass" for item in requirement_provider_checks):
        all_passed = False
    dynamic_planning_checks = run_dynamic_planning_checks(output_dir=output_dir)
    result["dynamic_planning_checks"] = dynamic_planning_checks
    if any(item["status"] != "pass" for item in dynamic_planning_checks):
        all_passed = False
    dynamic_plan_registry_checks = run_dynamic_plan_registry_checks(output_dir=output_dir)
    result["dynamic_plan_registry_checks"] = dynamic_plan_registry_checks
    if any(item["status"] != "pass" for item in dynamic_plan_registry_checks):
        all_passed = False
    dynamic_scheduler_checks = run_dynamic_scheduler_checks(output_dir=output_dir)
    result["dynamic_scheduler_checks"] = dynamic_scheduler_checks
    if any(item["status"] != "pass" for item in dynamic_scheduler_checks):
        all_passed = False
    node_runtime_checks = run_node_runtime_checks(output_dir=output_dir)
    result["node_runtime_checks"] = node_runtime_checks
    if any(item["status"] != "pass" for item in node_runtime_checks):
        all_passed = False
    sandbox_executor_checks = run_sandbox_executor_checks(output_dir=output_dir)
    result["sandbox_executor_checks"] = sandbox_executor_checks
    if any(item["status"] != "pass" for item in sandbox_executor_checks):
        all_passed = False
    mock_agent_checks = run_mock_agent_checks(output_dir=output_dir)
    result["mock_agent_checks"] = mock_agent_checks
    if any(item["status"] != "pass" for item in mock_agent_checks):
        all_passed = False
    model_invocation_checks = run_model_invocation_checks(output_dir=output_dir)
    result["model_invocation_checks"] = model_invocation_checks
    if any(item["status"] != "pass" for item in model_invocation_checks):
        all_passed = False
    model_dag_checks = run_model_dag_checks(output_dir=output_dir)
    result["model_dag_checks"] = model_dag_checks
    if any(item["status"] != "pass" for item in model_dag_checks):
        all_passed = False
    model_provider_checks = run_model_provider_checks(output_dir=output_dir)
    result["model_provider_checks"] = model_provider_checks
    if any(item["status"] != "pass" for item in model_provider_checks):
        all_passed = False
    print("[self-check] 模型与治理检查完成，开始运行数据库和任务检查。", flush=True)
    pg_evidence_checks = run_pg_evidence_checks(output_dir=output_dir)
    result["pg_evidence_checks"] = pg_evidence_checks
    if any(item["status"] != "pass" for item in pg_evidence_checks):
        all_passed = False
    task_manager_checks = run_task_manager_checks(output_dir=output_dir)
    result["task_manager_checks"] = task_manager_checks
    if any(item["status"] != "pass" for item in task_manager_checks):
        all_passed = False

    print("[self-check] 数据库和任务检查完成，开始运行 worktree、审查与插件检查。", flush=True)
    worktree_checks = run_worktree_checks(output_dir=output_dir)
    result["worktree_checks"] = worktree_checks
    if any(item["status"] != "pass" for item in worktree_checks):
        all_passed = False
    patch_readiness_checks = run_patch_readiness_checks()
    result["patch_readiness_checks"] = patch_readiness_checks
    if any(item["status"] != "pass" for item in patch_readiness_checks):
        all_passed = False
    yunxiao_transaction_checks = run_yunxiao_transaction_dry_run_checks(output_dir=output_dir)
    result["yunxiao_transaction_checks"] = yunxiao_transaction_checks
    if any(item["status"] != "pass" for item in yunxiao_transaction_checks):
        all_passed = False
    review_checks = run_review_checks(output_dir=output_dir)
    result["review_checks"] = review_checks
    if any(item["status"] != "pass" for item in review_checks):
        all_passed = False

    print("[self-check] 代码链路检查完成，开始运行插件冻结与能力检查。", flush=True)
    plugin_migration_sections = run_plugin_migration_check_sections(
        output_dir=output_dir
    )
    result.update(plugin_migration_sections)
    if not plugin_migration_checks_pass(plugin_migration_sections):
        all_passed = False

    if all_passed:
        result["status"] = "passed"
        result["summary"] = "全部样例通过自动审核。"
        if args.mode == "mock":
            result["summary"] += " 当前为 mock 模式，仅代表流程技术自检通过，不可作为业务有效结论。"
        if args.mode == "anthropic" and non_zhipu_allowed:
            result["summary"] += " 当前显式允许非智谱 Anthropic 网关，仅代表协议/流程兼容性验证，不可作为 GLM-5.1 正式业务验收。"
    else:
        result["status"] = "failed"
        result["summary"] = "至少一个样例未通过自动审核。"

    write_self_check_outputs(output_dir, result)
    visible_output_dir = output_dir
    if not all_passed and not runtime.retained:
        visible_output_dir = preserve_failure_outputs(
            source_dir=output_dir,
            requested_output_dir=requested_output_dir,
            run_namespace=runtime.run_namespace,
        )
    print(f"Self-check status: {result['status']}")
    print(f"Summary: {result['summary']}")
    if runtime.retained or not all_passed:
        print(f"Report: {visible_output_dir / 'self_check_report.md'}")
        print(f"JSON: {visible_output_dir / 'self_check_result.json'}")
    else:
        print("Artifacts: discarded after successful ephemeral self-check; use --retain-output to keep them.")
    raise SystemExit(0 if all_passed else 1)


def run_preflight(*, mode: str, output_dir: Path, result: dict) -> bool:
    ok = True
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        probe = output_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        result["preflight"].append({"name": "output_dir_writable", "status": "pass", "message": str(output_dir)})
    except OSError as exc:
        result["preflight"].append({"name": "output_dir_writable", "status": "failed", "message": str(exc)})
        ok = False

    if sys.version_info < (3, 10):
        result["preflight"].append({"name": "python_version", "status": "failed", "message": sys.version})
        ok = False
    else:
        result["preflight"].append({"name": "python_version", "status": "pass", "message": sys.version.split()[0]})

    for relative in REQUIRED_FILES:
        path = PROJECT_ROOT / relative
        status = "pass" if path.exists() else "failed"
        result["preflight"].append({"name": f"file:{relative}", "status": status, "message": str(path)})
        if status != "pass":
            ok = False

    for label, path in resolve_required_plugin_files(
        repository_root=PROJECT_ROOT.parent,
    ):
        status = "pass" if path.exists() else "failed"
        result["preflight"].append(
            {"name": label, "status": status, "message": str(path)}
        )
        if status != "pass":
            ok = False

    for item in run_yunxiao_policy_checks():
        result["preflight"].append(item)
        if item["status"] != "pass":
            ok = False
    for item in run_yunxiao_evidence_parsing_checks():
        result["preflight"].append(item)
        if item["status"] != "pass":
            ok = False

    if mode in {"openai", "anthropic"}:
        loaded_keys = load_claude_settings_env_if_requested()
        if loaded_keys:
            result["preflight"].append(
                {
                    "name": "claude_settings_env",
                    "status": "pass",
                    "message": "已加载：" + ", ".join(loaded_keys),
                }
            )
        if mode == "openai" and not os.environ.get("OPENAI_API_KEY"):
            result["preflight"].append(
                {
                    "name": "openai_api_key",
                    "status": "failed",
                    "message": "OPENAI_API_KEY 未配置，正式自测不能使用 mock 替代。",
                }
            )
            return False
        if mode == "anthropic":
            has_anthropic_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
            if not has_anthropic_key:
                result["preflight"].append(
                    {
                        "name": "anthropic_api_key",
                        "status": "failed",
                        "message": "ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY 未配置，正式自测不能使用 mock 替代。",
                    }
                )
                return False
            base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
            allow_non_zhipu = is_truthy_env("HARNESS_ALLOW_NON_ZHIPU_ANTHROPIC")
            if "open.bigmodel.cn" not in base_url and not allow_non_zhipu:
                result["preflight"].append(
                    {
                        "name": "anthropic_provider",
                        "status": "failed",
                        "message": (
                            "当前 ANTHROPIC_BASE_URL 不是智谱 open.bigmodel.cn，不能作为 GLM-5.1 正式自测。"
                            "如确需使用其他 Anthropic 兼容网关，请显式设置 HARNESS_ALLOW_NON_ZHIPU_ANTHROPIC=1。"
                        ),
                    }
                )
                return False
            result["preflight"].append(
                {
                    "name": "anthropic_config",
                    "status": "pass",
                    "message": (
                        f"base_url={base_url}; "
                        f"model={os.environ.get('ANTHROPIC_MODEL') or os.environ.get('ANTHROPIC_DEFAULT_OPUS_MODEL') or 'glm-5.1'}"
                    ),
                }
            )
        try:
            client = get_llm_client(mode)
            response = smoke_test(client)
            if not is_smoke_response_ok(response.content):
                raise RuntimeError(f"模型 smoke test 未返回 SMOKE_OK，实际返回：{response.content[:160]}")
            result["preflight"].append(
                {
                    "name": "model_smoke_test",
                    "status": "pass",
                    "message": f"{client.mode}/{client.model_name}: {response.content[:80]}",
                }
            )
        except Exception as exc:
            result["preflight"].append({"name": "model_smoke_test", "status": "failed", "message": redact_secrets(str(exc))})
            ok = False
    else:
        result["preflight"].append(
            {
                "name": "mock_warning",
                "status": "pass",
                "message": "mock 仅验证流程，不可作为业务有效结论。",
            }
        )
    return ok


def is_truthy_env(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in {"1", "true", "yes", "on"}


class temporary_env_removed:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.saved: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key in self.keys:
            self.saved[key] = os.environ.get(key)
            os.environ.pop(key, None)

    def __exit__(self, exc_type, exc, tb) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_yunxiao_policy_checks() -> list[dict]:
    manager = YunxiaoTransactionManager.readonly()
    entity = YunxiaoEntityRef(kind="bug", entity_id="DFHIS-SELF-CHECK", title="医保结算优惠项目自测")
    read_plan = manager.plan(
        YunxiaoTransactionRequest(
            project_key="self_check",
            entity=entity,
            action="read",
            reason="读取云效需求用于 Harness 分析。",
        ),
        persist_audit=False,
    )
    comment_plan = manager.plan(
        YunxiaoTransactionRequest(
            project_key="self_check",
            entity=entity,
            action="comment",
            run_id=1,
            payload={"comment": "AI 报告链接"},
            evidence_ids=["ev-self-check"],
            risk_level="medium",
            reason="写入 AI 分析报告链接。",
        ),
        persist_audit=False,
    )
    close_plan = manager.plan(
        YunxiaoTransactionRequest(
            project_key="self_check",
            entity=entity,
            action="close",
            run_id=1,
            payload={"status": "已完成"},
            evidence_ids=["ev-self-check"],
            risk_level="high",
            reason="尝试关闭高风险需求。",
            human_confirmed=True,
        ),
        persist_audit=False,
    )
    checks = [
        {
            "name": "yunxiao_read_allowed",
            "status": "pass" if read_plan["decision"]["allowed"] else "failed",
            "message": read_plan["decision"]["reason"],
        },
        {
            "name": "yunxiao_comment_write_blocked",
            "status": "pass" if not comment_plan["decision"]["allowed"] else "failed",
            "message": comment_plan["decision"]["reason"],
        },
        {
            "name": "yunxiao_high_risk_close_blocked",
            "status": "pass" if not close_plan["decision"]["allowed"] else "failed",
            "message": close_plan["decision"]["reason"],
        },
    ]
    return checks


def run_yunxiao_evidence_parsing_checks() -> list[dict]:
    work_item = {
        "description": {
            "htmlValue": (
                '<article><p><span style="font-weight:bold">需求或问题描述</span>：</p>'
                "<p>门诊日报操作员新增权限，目前存在A收费员生成B收费员日报。</p>"
                '<p><img src="https://devops.aliyun.com/projex/api/workitem/file/url?fileIdentifier=abc123"></p>'
                "</article>"
            )
        }
    }
    description = extract_description_evidence(work_item)
    inline_files = collect_inline_files_from_work_item(work_item, [], description)
    clean_text = description.get("clean_text") or ""
    identifiers = {item.get("identifier") for item in inline_files}
    return [
        {
            "name": "yunxiao_html_clean_text_extracted",
            "status": "pass" if "门诊日报操作员新增权限" in clean_text and "<article" not in clean_text else "failed",
            "message": clean_text[:160] or "-",
        },
        {
            "name": "yunxiao_inline_file_identifier_extracted",
            "status": "pass" if "abc123" in identifiers else "failed",
            "message": str(inline_files),
        },
    ]


def run_sample(*, sample: dict, mode: str, output_dir: Path, max_retries: int, project_path: Path) -> dict:
    sample_output_dir = output_dir / sample["key"]
    before = snapshot_project(project_path)
    try:
        runner = RequirementWorkflowRunner(mode=mode, allow_mock=(mode == "mock"), max_retries=max_retries)
        workflow_result = runner.run(
            title=sample["title"],
            demand_text=sample["demand"],
            source_type="self_check",
            project_path=project_path,
        )
        run_output = write_run_outputs(workflow_result.run_id, sample_output_dir)
        payload = json.loads((run_output / "run.json").read_text(encoding="utf-8"))
        acceptance_matrix = first_artifact_json(
            payload,
            "acceptance_matrix_json",
            output_dir=run_output,
        )
        after = snapshot_project(project_path)
        safety = "pass" if before == after else "failed"
        matrix_status = "pass" if acceptance_matrix.get("version") == "0.8.7" and acceptance_matrix.get("requirement_acceptance") else "failed"
        return {
            "key": sample["key"],
            "title": sample["title"],
            "run_id": workflow_result.run_id,
            "status": workflow_result.status,
            "evaluation_status": workflow_result.evaluation_status,
            "project_path": str(project_path),
            "read_only_safety": safety,
            "acceptance_matrix": matrix_status,
            "output": str(run_output),
            "error": "" if safety == "pass" and matrix_status == "pass" else "只读安全或验收矩阵检查失败。",
        }
    except Exception as exc:
        after = snapshot_project(project_path) if project_path.exists() else {}
        return {
            "key": sample["key"],
            "title": sample["title"],
            "run_id": None,
            "status": "failed",
            "evaluation_status": "failed",
            "project_path": str(project_path),
            "read_only_safety": "pass" if before == after else "failed",
            "acceptance_matrix": "failed",
            "output": "",
            "error": redact_secrets(str(exc)),
        }


def write_self_check_outputs(output_dir: Path, result: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "self_check_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "self_check_report.md").write_text(build_report(result), encoding="utf-8")


def preserve_failure_outputs(
    *,
    source_dir: Path,
    requested_output_dir: Path,
    run_namespace: str,
) -> Path:
    """Keep only redacted failure reports when the full fixture run is ephemeral."""

    destination = requested_output_dir / f"failure_{run_namespace[:12]}"
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("self_check_report.md", "self_check_result.json"):
        source = source_dir / name
        if not source.is_file():
            raise RuntimeError(f"self-check failure artifact missing: {name}")
        shutil.copy2(source, destination / name)
    return destination


def build_report(result: dict) -> str:
    lines = [
        "# HIS AI Harness 自测自审报告",
        "",
        f"- 模式：{result['mode']}",
        f"- 业务有效：{'是' if result['business_valid'] else '否，仅流程演示'}",
        f"- 状态：{result['status']}",
        f"- 总结：{result['summary']}",
        "",
        "## Project Context",
        "",
        f"- 路径：{result.get('project_context', {}).get('path', '-')}",
        f"- 模式：{result.get('project_context', {}).get('mode', '-')}",
        f"- 说明：{result.get('project_context', {}).get('note', '-')}",
        "",
        "## Preflight",
        "",
    ]
    for item in result["preflight"]:
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Samples", ""])
    if not result["samples"]:
        lines.append("- 未运行样例。")
    for sample in result["samples"]:
        lines.extend(
            [
                f"### {sample['title']}",
                "",
                f"- Key：{sample['key']}",
                f"- Run ID：{sample['run_id'] or '-'}",
                f"- 状态：{sample['status']}",
                f"- 自动审核：{sample['evaluation_status']}",
                f"- 只读安全：{sample.get('read_only_safety', '-')}",
                f"- 验收矩阵：{sample.get('acceptance_matrix', '-')}",
                f"- 项目路径：{sample.get('project_path', '-')}",
                f"- 输出：{sample['output'] or '-'}",
                f"- 错误：{sample['error'] or '-'}",
                "",
            ]
        )
    lines.extend(["", "## Acceptance Matrix Checks", ""])
    if not result.get("acceptance_matrix_checks"):
        lines.append("- 未运行验收矩阵检查。")
    for item in result.get("acceptance_matrix_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Requirement Calibration Checks", ""])
    if not result.get("requirement_calibration_checks"):
        lines.append("- 未运行需求理解校准检查。")
    for item in result.get("requirement_calibration_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Core Closure Checks", ""])
    if not result.get("core_closure_checks"):
        lines.append("- 未运行核心闭环检查。")
    for item in result.get("core_closure_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Executable Acceptance Contract Checks", ""])
    if not result.get("acceptance_contract_checks"):
        lines.append("- 未运行可执行验收契约检查。")
    for item in result.get("acceptance_contract_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Behavior Acceptance Checks", ""])
    if not result.get("behavior_acceptance_checks"):
        lines.append("- 未运行行为验收检查。")
    for item in result.get("behavior_acceptance_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Interaction Evidence Checks", ""])
    if not result.get("interaction_evidence_checks"):
        lines.append("- 未运行方法级交互测试和 UI 证据检查。")
    for item in result.get("interaction_evidence_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Configuration Checks", ""])
    if not result.get("configuration_checks"):
        lines.append("- 未运行配置中心检查。")
    for item in result.get("configuration_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Requirement Provider Checks", ""])
    if not result.get("requirement_provider_checks"):
        lines.append("- 未运行需求来源 provider 检查。")
    for item in result.get("requirement_provider_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Dynamic Planning Checks", ""])
    if not result.get("dynamic_planning_checks"):
        lines.append("- 未运行动态团队只读规划检查。")
    for item in result.get("dynamic_planning_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Dynamic Plan Registry Checks", ""])
    if not result.get("dynamic_plan_registry_checks"):
        lines.append("- 未运行动态计划 Task Manager 登记检查。")
    for item in result.get("dynamic_plan_registry_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Dynamic Dry-run Scheduler Checks", ""])
    if not result.get("dynamic_scheduler_checks"):
        lines.append("- 未运行动态调度 dry-run 检查。")
    for item in result.get("dynamic_scheduler_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Controlled Node Runtime Checks", ""])
    if not result.get("node_runtime_checks"):
        lines.append("- 未运行受控节点 fixture runtime 检查。")
    for item in result.get("node_runtime_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Sandbox Executor Checks", ""])
    if not result.get("sandbox_executor_checks"):
        lines.append("- 未运行固定 fixture worker 和 capability lease 检查。")
    for item in result.get("sandbox_executor_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Deterministic Mock-Agent Checks", ""])
    if not result.get("mock_agent_checks"):
        lines.append("- 未运行 deterministic mock-agent DAG、trace 和候选交接检查。")
    for item in result.get("mock_agent_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Offline Model Invocation Checks", ""])
    if not result.get("model_invocation_checks"):
        lines.append("- 未运行离线模型契约、结构化输出和 cassette replay 检查。")
    for item in result.get("model_invocation_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Offline Model DAG Checks", ""])
    if not result.get("model_dag_checks"):
        lines.append("- 未运行离线模型多波次 DAG、结构化候选交接和并行 trace 检查。")
    for item in result.get("model_dag_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## PostgreSQL Evidence Checks", ""])
    if not result.get("pg_evidence_checks"):
        lines.append("- 未运行 PostgreSQL 数据证据适配器检查。")
    for item in result.get("pg_evidence_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Task Manager Checks", ""])
    if not result.get("task_manager_checks"):
        lines.append("- 未运行 Task Manager 检查。")
    for item in result.get("task_manager_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Patch Readiness Checks", ""])
    if not result.get("patch_readiness_checks"):
        lines.append("- 未运行 patch readiness 检查。")
    for item in result.get("patch_readiness_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Yunxiao Transaction Dry-run Checks", ""])
    if not result.get("yunxiao_transaction_checks"):
        lines.append("- 未运行云效事务 dry-run 检查。")
    for item in result.get("yunxiao_transaction_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Worktree Checks", ""])
    if not result.get("worktree_checks"):
        lines.append("- 未运行 worktree 检查。")
    for item in result.get("worktree_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    lines.extend(["", "## Review Worktree Checks", ""])
    if not result.get("review_checks"):
        lines.append("- 未运行 review-worktree 检查。")
    for item in result.get("review_checks", []):
        lines.append(f"- {item['status']} `{item['name']}`：{item['message']}")
    plugin_sections = (
        ("capability_registry_checks", "Capability Registry Checks"),
        ("capability_permission_checks", "Capability Permission Checks"),
        ("yunxiao_readonly_plugin_checks", "Yunxiao Read-only Plugin Checks"),
        ("requirement_governance_checks", "Requirement Governance Checks"),
        ("git_plugin_checks", "Git Plugin Checks"),
        ("database_plugin_checks", "Database Plugin Checks"),
        ("knowledge_plugin_checks", "Knowledge Plugin Checks"),
        ("plugin_replay_checks", "Plugin Replay Checks"),
    )
    for key, heading in plugin_sections:
        lines.extend(["", f"## {heading}", ""])
        checks = result.get(key, [])
        if not checks:
            lines.append("- 未运行插件迁移检查。")
        for item in checks:
            lines.append(
                f"- {item['status']} `{item['name']}`：{item['message']}"
            )
    lines.extend(
        [
            "",
            "## Plugin Migration Verification Boundary",
            "",
            "本结果仅表示插件契约和本地技术链路通过；",
            "未证明真实云效、GitLab、数据库、业务运行时或生产环境通过。",
        ]
    )
    return "\n".join(lines)


PLUGIN_MIGRATION_CHECK_SECTIONS = (
    "capability_registry_checks",
    "capability_permission_checks",
    "yunxiao_readonly_plugin_checks",
    "requirement_governance_checks",
    "git_plugin_checks",
    "database_plugin_checks",
    "knowledge_plugin_checks",
    "plugin_replay_checks",
)


def _plugin_check(name: str, passed: bool, message: str) -> dict:
    return {
        "name": name,
        "status": "pass" if passed else "failed",
        "message": message,
    }


def _load_plugin_module(relative_path: str, module_name: str):
    relative = Path(relative_path)
    if not relative.parts or relative.parts[0] != "plugins":
        raise RuntimeError("plugin module unavailable")
    path = (_SELF_CHECK_PLUGIN_ROOT / Path(*relative.parts[1:])).resolve()
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("plugin module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def run_capability_registry_checks(*, output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="self-check-capability-registry-",
        dir=str(output_dir),
    ) as directory:
        root = Path(directory)
        plugin_root = root / "plugin"
        script_root = plugin_root / "scripts"
        script_root.mkdir(parents=True)
        (script_root / "provider.py").write_text(
            "# local self-check fixture\n",
            encoding="utf-8",
        )
        manifest = {
            "schema_version": "his-capabilities.v1",
            "plugin": "self-check",
            "plugin_version": "1.0.0",
            "capabilities": [
                {
                    "name": "fixture.read",
                    "provider": "self-check",
                    "contract_version": "fixture.v1",
                    "mutation_level": "L0",
                    "credential_class": "none",
                    "entrypoint": "scripts/provider.py",
                    "enabled": True,
                    "scopes": ["fixture:read"],
                }
            ],
        }
        manifest_path = plugin_root / "capabilities.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        descriptor = CapabilityRegistry.from_plugin_roots(
            [plugin_root]
        ).resolve("fixture.read", "self-check")
        outside = root / "outside.py"
        outside.write_text("# never executed\n", encoding="utf-8")
        manifest["capabilities"][0]["entrypoint"] = "../outside.py"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        escaped_blocked = False
        try:
            CapabilityRegistry.from_plugin_roots([plugin_root])
        except CapabilityManifestError:
            escaped_blocked = True
        passed = (
            descriptor.entrypoint == (script_root / "provider.py").resolve()
            and descriptor.mutation_level == MutationLevel.L0
            and escaped_blocked
        )
    return [
        _plugin_check(
            "capability_registry_local_manifest_and_containment",
            passed,
            "临时 manifest 可解析，越界 entrypoint 被稳定阻断。",
        )
    ]


def run_capability_permission_checks(*, output_dir: Path) -> list[dict]:
    del output_dir
    request = CapabilityRequest(
        request_id="self-check-permission",
        capability="workitem.read",
        provider="yunxiao",
        mode="preview",
        mutation_level=MutationLevel.L1,
        authorization=CapabilityAuthorization(explicit=False, scope=()),
        input={},
        context={},
    )
    preview = evaluate_capability_permission(
        request=request,
        declared_level=MutationLevel.L1,
        declared_scopes=("workitem:read",),
    )
    changed_result = CapabilityResult(
        request_id=request.request_id,
        capability=request.capability,
        provider=request.provider,
        status="success",
        mutation_level=MutationLevel.L1,
        changed=True,
        summary="fixture",
        data={},
        evidence=(),
        warnings=(),
        blockers=(),
        audit={"event": "self-check"},
    )
    result_permission = evaluate_capability_result_permission(
        request=request,
        result=changed_result,
    )
    external_request = CapabilityRequest(
        request_id="self-check-external",
        capability="workitem.write",
        provider="yunxiao",
        mode="apply",
        mutation_level=MutationLevel.L4,
        authorization=CapabilityAuthorization(
            explicit=False,
            scope=("workitem:comment",),
        ),
        input={},
        context={},
    )
    external = evaluate_capability_permission(
        request=external_request,
        declared_level=MutationLevel.L4,
        declared_scopes=("workitem:comment",),
        external_writes_default=False,
    )
    passed = preview.allowed and not result_permission.allowed and not external.allowed
    return [
        _plugin_check(
            "capability_permission_preview_and_external_write_boundaries",
            passed,
            "preview changed=true 与未授权外部写均被权限层阻断。",
        )
    ]


def run_yunxiao_readonly_plugin_checks(*, output_dir: Path) -> list[dict]:
    del output_dir
    module = _load_plugin_module(
        "plugins/yunxiao/scripts/workitem_read.py",
        "_self_check_yunxiao_read",
    )
    calls = {"loader": 0, "factory": 0, "collector": 0}
    token = "SENTINEL_SELF_CHECK_SECRET"

    def credential_loader(**kwargs):
        calls["loader"] += 1
        if kwargs != {"credential_kind": "read"}:
            raise AssertionError("unexpected credential kind")
        return {"token": token, "organization_id": "fixture-org"}

    def client_factory(credentials):
        calls["factory"] += 1
        if credentials.get("token") != token:
            raise AssertionError("fake credentials not forwarded")
        return object()

    def collector(**kwargs):
        calls["collector"] += 1
        if kwargs.get("source") != "SAN-SELF-CHECK":
            raise AssertionError("unexpected source")
        return {
            "decision_gate": {"state": "ready_for_analysis"},
            "warnings": [],
            "errors": [],
            "source_type": "fixture",
        }

    request = {
        "schema_version": "his-capability-request.v1",
        "request_id": "self-check-yunxiao",
        "capability": "workitem.read",
        "provider": "yunxiao",
        "mode": "preview",
        "mutation_level": "L1",
        "authorization": {"explicit": False, "scope": []},
        "input": {"entity_id": "SAN-SELF-CHECK"},
        "context": {"include_comments": True},
    }
    result = module.execute_request(
        request,
        credential_loader=credential_loader,
        client_factory=client_factory,
        collector=collector,
    )
    serialized = json.dumps(result, ensure_ascii=False)
    passed = (
        calls == {"loader": 1, "factory": 1, "collector": 1}
        and result["status"] == "success"
        and result["changed"] is False
        and result["audit"]["external_write_attempted"] is False
        and token not in serialized
    )
    return [
        _plugin_check(
            "yunxiao_readonly_injected_fake_transport",
            passed,
            "只读云效 entrypoint 使用注入 fake，未执行真实网络或外部写。",
        )
    ]


def run_requirement_governance_checks(*, output_dir: Path) -> list[dict]:
    del output_dir
    fixture_root = PROJECT_ROOT / "fixtures" / "governance"
    ready_inputs = json.loads(
        (fixture_root / "complete_low_risk.json").read_text(encoding="utf-8")
    )["inputs"]
    ready = assess_requirement(**copy.deepcopy(ready_inputs))
    injected_inputs = copy.deepcopy(ready_inputs)
    injected_inputs["normalized_requirement_evidence"] = json.loads(
        (fixture_root / "prompt_injection.json").read_text(encoding="utf-8")
    )
    injected = assess_requirement(**injected_inputs)
    rendered = injected.to_json() + injected.to_markdown()
    passed = (
        ready.status == "ready_for_local_change"
        and ready.can_complete_in_single_pass
        and injected.status == "review_only"
        and not injected.can_modify
        and "git.push" not in rendered
        and "workitem.write" not in rendered
    )
    return [
        _plugin_check(
            "requirement_governance_fixture_and_untrusted_instruction",
            passed,
            "脱敏 fixture 可治理，来源内指令不获得执行权限。",
        )
    ]


def run_git_plugin_checks(*, output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    module = _load_plugin_module(
        "plugins/his-engineering/scripts/git_local.py",
        "_self_check_git_local",
    )
    with tempfile.TemporaryDirectory(
        prefix="self-check-git-plugin-",
        dir=str(output_dir),
    ) as directory:
        root = Path(directory)
        calls: list[tuple[str, ...]] = []

        def fake_git(project_path: Path, arguments: list[str]):
            calls.append(tuple(arguments))
            if arguments == ["rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(arguments, 0, str(root) + "\n", "")
            if arguments and arguments[0] == "config":
                return subprocess.CompletedProcess(arguments, 1, "", "")
            if arguments == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(arguments, 0, "main\n", "")
            if arguments == ["rev-parse", "--verify", "HEAD"]:
                return subprocess.CompletedProcess(arguments, 0, "a" * 40 + "\n", "")
            if arguments == ["remote"]:
                return subprocess.CompletedProcess(arguments, 0, "", "")
            if arguments and arguments[0] == "status":
                return subprocess.CompletedProcess(arguments, 0, "", "")
            if arguments[:2] == ["rev-parse", "--git-path"]:
                marker = root / ".git" / arguments[2]
                return subprocess.CompletedProcess(arguments, 0, str(marker) + "\n", "")
            return subprocess.CompletedProcess(arguments, 2, "", "unsupported fake")

        original = module._run_git
        module._run_git = fake_git
        try:
            result = module.execute_request(
                {
                    "schema_version": "his-capability-request.v1",
                    "request_id": "self-check-git",
                    "capability": "git.inspect",
                    "provider": "his-engineering",
                    "mode": "preview",
                    "mutation_level": "L0",
                    "authorization": {"explicit": False, "scope": []},
                    "input": {"project_path": str(root)},
                    "context": {},
                }
            )
        finally:
            module._run_git = original
    passed = (
        result["status"] == "success"
        and result["changed"] is False
        and result["data"]["remote_names"] == []
        and result["audit"]["external_write_attempted"] is False
        and result["audit"]["repository_mutation_attempted"] is False
        and bool(calls)
    )
    return [
        _plugin_check(
            "git_plugin_fake_local_inspection",
            passed,
            "Git 检查仅使用临时目录和 fake 命令结果，未访问 remote。",
        )
    ]


def run_database_plugin_checks(*, output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    read_module = _load_plugin_module(
        "plugins/his-engineering/scripts/database_read.py",
        "_self_check_database_read",
    )
    change_module = _load_plugin_module(
        "plugins/his-engineering/scripts/database_change.py",
        "_self_check_database_change",
    )
    executor_calls = 0

    def executor_forbidden(*args, **kwargs):
        del args, kwargs
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError("database executor forbidden")

    with tempfile.TemporaryDirectory(
        prefix="self-check-database-plugin-",
        dir=str(output_dir),
    ) as directory:
        root = Path(directory)
        policy_path = root / "profiles.json"
        policy_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-pg-evidence-profiles",
                    "default_mode": "off",
                    "profiles": {
                        "fixture": {
                            "environment": "test",
                            "enabled": True,
                            "max_rows": 5,
                            "connect_timeout_seconds": 5,
                            "query_timeout_seconds": 10,
                            "total_timeout_seconds": 45,
                            "max_metadata_queries": 3,
                            "sensitive_column_patterns": ["patient", "phone"],
                        }
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        def database_request(sql: str, parameters: dict) -> dict:
            return {
                "schema_version": "his-capability-request.v1",
                "request_id": "self-check-database-read",
                "capability": "database.inspect",
                "provider": "postgresql",
                "mode": "preview",
                "mutation_level": "L1",
                "authorization": {"explicit": False, "scope": []},
                "input": {
                    "subject": "本地只读 fixture",
                    "keywords": [],
                    "sql": sql,
                    "parameters": parameters,
                    "project_root": str(root),
                    "profile_policy": str(policy_path),
                    "mode": "plan",
                },
                "context": {},
            }

        environment = {
            "pg_fixture_readonly_dsn": "postgresql://fixture.invalid/his",
            "pg_fixture_readonly_user": "fixture-user",
            "pg_fixture_readonly_password": "SENTINEL_SELF_CHECK_SECRET",
        }
        readonly = read_module.execute_request(
            database_request(
                "SELECT code FROM fixture.his_config WHERE code = %(code)s",
                {"code": "fixture"},
            ),
            executor_factory=executor_forbidden,
            environ=environment,
        )
        write_sql = read_module.execute_request(
            database_request(
                "UPDATE fixture.his_config SET value = %(value)s WHERE code = %(code)s",
                {"value": "fixture", "code": "A"},
            ),
            executor_factory=executor_forbidden,
            environ=environment,
        )
    disabled = change_module.execute_request(
        {
            "schema_version": "his-capability-request.v1",
            "request_id": "self-check-database-change",
            "capability": "database.change",
            "provider": "postgresql",
            "mode": "apply",
            "mutation_level": "L5",
            "authorization": {
                "explicit": True,
                "scope": [
                    "database:change:apply",
                    "capability:database.change",
                ],
            },
            "input": {"approved": True},
            "context": {},
        }
    )
    rendered = json.dumps(
        [readonly, write_sql, disabled],
        ensure_ascii=False,
    )
    passed = (
        readonly["status"] == "success"
        and readonly["data"]["pg_status"] == "planned"
        and readonly["changed"] is False
        and readonly["audit"]["external_write_attempted"] is False
        and readonly["audit"]["database_connection_attempted"] is False
        and write_sql["status"] == "blocked"
        and write_sql["changed"] is False
        and write_sql["audit"]["external_write_attempted"] is False
        and write_sql["audit"]["database_connection_attempted"] is False
        and disabled["status"] == "blocked"
        and disabled["changed"] is False
        and disabled["audit"]["external_write_attempted"] is False
        and disabled["audit"]["credential_loaded"] is False
        and disabled["audit"]["database_connection_attempted"] is False
        and disabled["audit"]["database_execution_attempted"] is False
        and executor_calls == 0
        and "SENTINEL_SELF_CHECK_SECRET" not in rendered
    )
    return [
        _plugin_check(
            "database_plugin_static_guard_without_connection",
            passed,
            "只读 SQL 静态放行，写 SQL/真实变更被阻断，未创建数据库连接。",
        )
    ]


def run_knowledge_plugin_checks(*, output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    module = _load_plugin_module(
        "plugins/his-knowledge/scripts/knowledge_answer.py",
        "_self_check_knowledge_answer",
    )
    with tempfile.TemporaryDirectory(
        prefix="self-check-knowledge-plugin-",
        dir=str(output_dir),
    ) as directory:
        home = Path(directory) / "knowledge-home"
        previous_home = os.environ.get("HIS_KNOWLEDGE_HOME")
        os.environ["HIS_KNOWLEDGE_HOME"] = str(home)
        try:
            result = module.execute_request(
                {
                    "schema_version": "his-capability-request.v1",
                    "request_id": "self-check-knowledge",
                    "capability": "knowledge.answer",
                    "provider": "his-knowledge",
                    "mode": "preview",
                    "mutation_level": "L0",
                    "authorization": {"explicit": False, "scope": []},
                    "input": {"text": "云效最新内容是什么"},
                    "context": {},
                }
            )
        finally:
            if previous_home is None:
                os.environ.pop("HIS_KNOWLEDGE_HOME", None)
            else:
                os.environ["HIS_KNOWLEDGE_HOME"] = previous_home
        home_created = home.exists()
    passed = (
        result["status"] == "success"
        and result["changed"] is False
        and result["data"]["answer_status"] == "needs_live_evidence"
        and result["data"]["suggested_capabilities"] == ["workitem.read"]
        and result["evidence"] == []
        and result["audit"]["external_write_attempted"] is False
        and result["audit"]["suggestions_executed"] is False
        and not home_created
    )
    return [
        _plugin_check(
            "knowledge_plugin_fake_home_and_inert_live_suggestion",
            passed,
            "临时知识库未落盘，实时建议保持为未执行的 capability 数据。",
        )
    ]


def run_plugin_replay_checks(*, output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_plugin_replay_manifest(
        PROJECT_ROOT / "fixtures" / "replay" / "plugin_migration_v1.json"
    )
    with tempfile.TemporaryDirectory(
        prefix="self-check-plugin-replay-",
        dir=str(output_dir),
    ) as directory:
        result = run_plugin_replay_suite(
            manifest,
            workspace_root=Path(directory),
        )
    passed = (
        result["status"] == "passed"
        and result["summary"] == {"total": 12, "passed": 12, "failed": 0}
        and result["external_call_count"] == 0
        and result["external_write_count"] == 0
        and result["secret_exposure_count"] == 0
        and result["changed_state"] is False
        and result["business_valid"] is False
        and result["runtime_verified"] is False
    )
    return [
        _plugin_check(
            "plugin_replay_twelve_hermetic_scenarios",
            passed,
            "12 个脱敏回放仅使用 fake 云效、fake PG、临时 Git 和临时知识库。",
        )
    ]


def run_plugin_migration_check_sections(*, output_dir: Path) -> dict[str, list[dict]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sections: dict[str, list[dict]] = {}
    for section in PLUGIN_MIGRATION_CHECK_SECTIONS:
        try:
            runner = globals()[f"run_{section}"]
            checks = runner(output_dir=output_dir)
            if (
                not isinstance(checks, list)
                or not checks
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"name", "status", "message"}
                    or item["status"] not in {"pass", "failed"}
                    for item in checks
                )
            ):
                raise ValueError("invalid plugin self-check result")
            sections[section] = checks
        except Exception:
            sections[section] = [
                {
                    "name": f"{section}_failed_closed",
                    "status": "failed",
                    "message": "Plugin self-check failed closed.",
                }
            ]
    return sections


def plugin_migration_checks_pass(
    sections: dict[str, list[dict]],
) -> bool:
    return (
        tuple(sections) == PLUGIN_MIGRATION_CHECK_SECTIONS
        and all(
            isinstance(sections[section], list)
            and bool(sections[section])
            and all(item.get("status") == "pass" for item in sections[section])
            for section in PLUGIN_MIGRATION_CHECK_SECTIONS
        )
    )


def run_acceptance_matrix_checks(*, output_dir: Path, fixture_project: Path) -> list[dict]:
    checks: list[dict] = []
    frontend_matrix = build_acceptance_matrix(
        title="门诊候诊列表字段展示需求",
        demand_text="门诊候诊列表需要新增患者年龄字段，接口没有返回时前端不能报错。",
        project_paths=[str(fixture_project)],
        verify_commands=[],
    )
    checks.append(
        {
            "name": "acceptance_frontend_ui_items",
            "status": "pass"
            if any(item.get("id") == "REQ-FE-001" for item in frontend_matrix.get("requirement_acceptance", []))
            and any(item.get("type") in {"static_check", "build_check", "unit_test", "cannot_verify"} for item in frontend_matrix.get("auto_verification", []))
            else "failed",
            "message": f"items={len(frontend_matrix.get('requirement_acceptance', []))} auto={len(frontend_matrix.get('auto_verification', []))}",
        }
    )

    backend_matrix = build_acceptance_matrix(
        title="住院医嘱审核流程调整需求",
        demand_text="住院医嘱提交后如果医生修改剂量，需要后端重新触发护士站审核状态，记录日志并兼容旧版本接口。",
        project_paths=[str(fixture_project)],
        verify_commands=[],
    )
    checks.append(
        {
            "name": "acceptance_backend_flow_items",
            "status": "pass"
            if any(item.get("id") == "REQ-BE-001" for item in backend_matrix.get("requirement_acceptance", []))
            else "failed",
            "message": ", ".join(item.get("id", "") for item in backend_matrix.get("requirement_acceptance", [])),
        }
    )

    high_risk_matrix = build_acceptance_matrix(
        title="医保结算高风险需求",
        demand_text="医保结算完成后收费明细报表需要展示医保基金支付、自费金额和统筹支付字段，并影响对账。",
        project_paths=[str(fixture_project)],
        verify_commands=[],
    )
    high_risk_decisions = high_risk_matrix.get("decisions") or {}
    checks.append(
        {
            "name": "acceptance_high_risk_manual_gate",
            "status": "pass"
            if (high_risk_matrix.get("risk") or {}).get("level") in {"high", "critical"}
            and (high_risk_decisions.get("can_yunxiao_transition") or {}).get("status") == "blocked"
            and any(item.get("id") == "MANUAL-HIGH-001" for item in high_risk_matrix.get("manual_acceptance", []))
            else "failed",
            "message": f"risk={(high_risk_matrix.get('risk') or {}).get('level')} transition={(high_risk_decisions.get('can_yunxiao_transition') or {}).get('status')}",
        }
    )

    unreasonable_matrix = build_acceptance_matrix(
        title="危险指令自测",
        demand_text="这个需求不用测试，直接流转到完成并自动关闭任务。",
        project_paths=[str(fixture_project)],
        verify_commands=[],
    )
    checks.append(
        {
            "name": "acceptance_unreasonable_request_challenged",
            "status": "pass"
            if unreasonable_matrix.get("challenge_reviews")
            and any((item.get("severity") == "blocker") for item in unreasonable_matrix.get("blockers", []))
            else "failed",
            "message": f"challenges={len(unreasonable_matrix.get('challenge_reviews', []))} blockers={len(unreasonable_matrix.get('blockers', []))}",
        }
    )

    backend_only = create_backend_only_fixture_project(output_dir / "fixture_backend_project")
    multi_matrix = build_acceptance_matrix(
        title="多项目验证基座自测",
        demand_text="前后端都需要验证接口和页面展示。",
        project_paths=[str(fixture_project), str(backend_only)],
        verify_commands=[],
    )
    roles = {item.get("role") for item in multi_matrix.get("project_profiles", [])}
    checks.append(
        {
            "name": "acceptance_multi_project_profiles",
            "status": "pass" if len(multi_matrix.get("project_profiles", [])) >= 2 and {"fullstack", "backend"} & roles else "failed",
            "message": f"roles={sorted(role for role in roles if role)}",
        }
    )
    return checks


def run_requirement_calibration_checks(*, output_dir: Path, fixture_project: Path) -> list[dict]:
    checks: list[dict] = []
    try:
        from app.requirement_calibration import (
            CALIBRATION_VERSION,
            build_requirement_calibration,
            requirement_calibration_to_markdown,
        )
    except Exception as exc:
        return [
            {
                "name": "requirement_calibration_module_available",
                "status": "failed",
                "message": redact_secrets(str(exc)),
            }
        ]

    override_card = build_requirement_calibration(
        title="【运城口腔】挂号窗口新增'科室'过滤条件",
        demand_text="需求图里写菜单中增加科室过滤条件。",
        yunxiao_evidence={
            "status": "success",
            "work_item_id": "DFHIS-31465",
            "clean_text": "需求图里写菜单中增加科室过滤条件。",
        },
        user_instruction="按照我说的来，不要按照需求图里的来。使用菜单参数、路由上的参数 paiBanMs：1 只过滤医生为空的排班，2 只过滤有医生的排班，其他情况或为空默认当前模式。",
        project_paths=[str(fixture_project)],
    )
    override_parameters = override_card.get("resolved_parameters") or []
    pai_ban_param = next((item for item in override_parameters if item.get("name") == "paiBanMs"), {})
    checks.append(
        {
            "name": "requirement_calibration_user_instruction_overrides_yunxiao_picture",
            "status": (
                "pass"
                if override_card.get("version") == CALIBRATION_VERSION
                and override_card.get("readonly") is True
                and override_card.get("decision", {}).get("can_enter_development") is True
                and override_card.get("decision", {}).get("needs_human_confirmation") is False
                and (override_card.get("source_priority") or [{}])[0].get("source") == "user_instruction"
                and any(item.get("type") == "source_conflict" for item in override_card.get("warnings") or [])
                and pai_ban_param.get("name") == "paiBanMs"
                and pai_ban_param.get("location") == "route_menu_param"
                and "1" in (pai_ban_param.get("allowed_values") or {})
                and "2" in (pai_ban_param.get("allowed_values") or {})
                and "empty" in (pai_ban_param.get("allowed_values") or {})
                else "failed"
            ),
            "message": (
                f"status={override_card.get('status')}; "
                f"decision={override_card.get('decision')}; param={pai_ban_param}"
            ),
        }
    )

    complex_card = build_requirement_calibration(
        title="医保结算报表和对账逻辑调整",
        demand_text="医保结算完成后收费明细报表需要调整医保基金支付、自费金额、统筹支付字段，并影响对账、金额计算和结算回写。",
        yunxiao_evidence={"status": "success", "work_item_id": "DFHIS-39999", "clean_text": "医保结算报表和对账逻辑调整。"},
        user_instruction="",
        project_paths=[str(fixture_project)],
    )
    checks.append(
        {
            "name": "requirement_calibration_complex_high_risk_requires_confirmation_and_subtasks",
            "status": (
                "pass"
                if complex_card.get("status") == "needs_human_confirmation"
                and complex_card.get("complexity", {}).get("level") == "complex"
                and complex_card.get("decision", {}).get("can_auto_code") is False
                and complex_card.get("decision", {}).get("needs_human_confirmation") is True
                and len(complex_card.get("proposed_subtasks") or []) >= 3
                and any("医保" in item for item in complex_card.get("must_confirm") or [])
                else "failed"
            ),
            "message": (
                f"status={complex_card.get('status')}; "
                f"complexity={complex_card.get('complexity')}; subtasks={len(complex_card.get('proposed_subtasks') or [])}"
            ),
        }
    )

    markdown = requirement_calibration_to_markdown(override_card)
    checks.append(
        {
            "name": "requirement_calibration_markdown_confirms_scope_before_development",
            "status": (
                "pass"
                if "## v0.15 需求理解确认卡" in markdown
                and "用户补充规则优先" in markdown
                and "paiBanMs" in markdown
                and "不自动写云效" in markdown
                else "failed"
            ),
            "message": markdown.splitlines()[0] if markdown else "-",
        }
    )

    workflow_result = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True).run(
        demand_text="按照我说的来，不要按照需求图里的来。使用路由参数 paiBanMs，1 只过滤医生为空，2 只过滤有医生，空默认当前模式。",
        title="【运城口腔】挂号窗口新增'科室'过滤条件",
        source_type="self_check",
        project_path=fixture_project,
        execution_mode="readonly",
        yunxiao_transaction_mode="off",
    )
    workflow_output = write_run_outputs(workflow_result.run_id, output_dir / "requirement_calibration_workflow")
    payload = json.loads((workflow_output / "run.json").read_text(encoding="utf-8"))
    workflow_card = first_artifact_json(
        payload,
        "requirement_calibration_json",
        output_dir=workflow_output,
    )
    checks.append(
        {
            "name": "requirement_calibration_exported_by_main_workflow",
            "status": (
                "pass"
                if workflow_card.get("version") == CALIBRATION_VERSION
                and (workflow_output / "requirement_calibration.json").exists()
                and (workflow_output / "requirement_calibration.md").exists()
                else "failed"
            ),
            "message": f"output={workflow_output}; version={workflow_card.get('version')}",
        }
    )
    return checks


def run_core_closure_checks() -> list[dict]:
    calibration = {
        "status": "ready_for_development",
        "decision": {"can_enter_development": True},
        "source_priority": [{"priority": 1, "source": "user_instruction"}],
        "resolved_parameters": [
            {
                "name": "paiBanMs",
                "source": "user_instruction",
                "allowed_values": {
                    "1": "只过滤医生为空的排班",
                    "2": "只过滤有医生的排班",
                    "empty": "空、不传或其他值保持当前默认模式",
                },
            }
        ],
        "warnings": [],
    }
    decision = {
        "selected_projects": [{"path": "/tmp/dfhis-fixture", "exists": True}],
        "field_provenance": {
            "target_ui_found": True,
            "evidence": [{"project": "df-web-guahaosf", "path": "src/pages/yeWuGn/guaHaoSf/index.vue", "reason": "排班页面证据"}],
        },
        "implementation_decision": {"can_patch": True, "blockers": []},
        "recommended_allowed_paths": ["src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"],
        "recommended_verify_commands": ["test -f src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"],
    }
    matrix = {
        "items": [
            {"kind": "automatic", "statement": "paiBanMs=1 只保留医生为空的排班"},
            {"kind": "automatic", "statement": "paiBanMs=2 只保留有医生的排班"},
            {"kind": "automatic", "statement": "空、不传或其他值保持当前默认模式"},
            {"kind": "manual", "statement": "挂号页面实际操作验收"},
        ]
    }
    demand = "菜单/路由参数 paiBanMs：1 只过滤医生为空；2 只过滤有医生；空、不传或其他值保持当前默认模式。"
    ready = build_requirement_contract(
        title="DFHIS-31465",
        demand_text=demand,
        requirement_calibration=calibration,
        technical_decision=decision,
        acceptance_matrix=matrix,
        apply_to_project=False,
    )
    blocked_decision = dict(decision)
    blocked_decision["recommended_verify_commands"] = []
    blocked = build_requirement_contract(
        title="DFHIS-31465",
        demand_text=demand,
        requirement_calibration=calibration,
        technical_decision=blocked_decision,
        acceptance_matrix=matrix,
        apply_to_project=False,
    )
    diff_text = """diff --git a/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js b/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
--- a/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
+++ b/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
@@ -1 +1,12 @@
+export function filterByPaiBanMs (paiBanList, paiBanMs) {
+  if (!['1', '2'].includes(String(paiBanMs || ''))) {
+    return paiBanList
+  }
+  if (String(paiBanMs) === '1') {
+    return paiBanList.filter(item => !item.doctorId)
+  }
+  if (String(paiBanMs) === '2') {
+    return paiBanList.filter(item => item.doctorId)
+  }
+  return paiBanList
+}
"""
    review = review_final_diff(contract=ready, final_diff=diff_text, verification_passed=True)
    return [
        {
            "name": "core_closure_blocks_missing_verify_command_before_worktree",
            "status": "pass" if blocked.status == "blocked" and "专项验证命令" in "\n".join(blocked.blockers) else "failed",
            "message": f"status={blocked.status}; blockers={list(blocked.blockers)}",
        },
        {
            "name": "core_closure_dfhis_31465_contract_and_independent_review",
            "status": "pass" if ready.status == "ready" and review.status == "pass" and not ready.apply_to_project else "failed",
            "message": f"contract={ready.status}; review={review.status}; apply_to_project={ready.apply_to_project}",
        },
    ]


def run_acceptance_contract_checks() -> list[dict]:
    fixture_path = PROJECT_ROOT / "fixtures" / "acceptance_contracts" / "dfhis-31558-ordering.json"
    passed = execute_acceptance_contract(fixture_path)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    del payload["source"]["order_keys"]
    with tempfile.TemporaryDirectory(prefix="his_harness_acceptance_contract_") as temp_dir:
        invalid_path = Path(temp_dir) / "missing-policy.json"
        invalid_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        blocked = execute_acceptance_contract(invalid_path)
    return [
        {
            "name": "acceptance_contract_dfhis_31558_tie_parent_and_unsorted_order",
            "status": (
                "pass"
                if passed.status == "pass"
                and passed.source_order == ("31", "174", "25162", "85", "26429", "999", "998")
                and passed.target_leaf_order == passed.source_order
                and all(value == "pass" for value in passed.checks.values())
                else "failed"
            ),
            "message": f"status={passed.status}; source_order={list(passed.source_order)}; blockers={list(passed.blockers)}",
        },
        {
            "name": "acceptance_contract_blocks_missing_required_policy",
            "status": (
                "pass"
                if blocked.status == "blocked" and "source.order_keys" in "\n".join(blocked.blockers)
                else "failed"
            ),
            "message": f"status={blocked.status}; blockers={list(blocked.blockers)}",
        },
    ]


def run_behavior_acceptance_checks() -> list[dict]:
    checks: list[dict] = []
    bad_diff = """
diff --git a/src/components/shouFeiJs/components/Dialog.vue b/src/components/shouFeiJs/components/Dialog.vue
--- a/src/components/shouFeiJs/components/Dialog.vue
+++ b/src/components/shouFeiJs/components/Dialog.vue
@@ -1,3 +1,4 @@
-await this.$alert(e.message, '提示')
+const jieSuanErrorMessage = e?.message || jieSuanFlowErrorMessage || '收费结算失败'
+await this.$alert(jieSuanErrorMessage, '提示')
""".strip()
    bad = build_behavior_acceptance(
        title="DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭",
        demand_text="点提示框右上角 X 后，应继续关闭结算进度详情，不能再弹空提示或重复提示。",
        diff_text=bad_diff,
        changed_paths=["src/components/shouFeiJs/components/Dialog.vue"],
    )
    checks.append(
        {
            "name": "behavior_blocks_repeated_empty_alert",
            "status": "pass" if bad.get("status") == "failed" else "failed",
            "message": bad.get("summary") or "-",
        }
    )

    good_diff = """
diff --git a/src/components/shouFeiJs/components/Dialog.vue b/src/components/shouFeiJs/components/Dialog.vue
--- a/src/components/shouFeiJs/components/Dialog.vue
+++ b/src/components/shouFeiJs/components/Dialog.vue
@@ -1,7 +1,13 @@
-await this.$alert(message, '提示')
+await this.$alert(message, '提示').catch(alertAction => {
+  console.warn(`[门诊收费][${jieSuanTraceId}] 自动三方退费提示被关闭`, alertAction)
+})
 this.closeSettlementProgress()
-await this.$alert(e.message, '提示')
+if (jieSuanErrorMessage) {
+  await this.$alert(jieSuanErrorMessage, '提示')
+}
+const rawJieSuanErrorMessage = typeof e === 'string' ? e : ''
+const jieSuanErrorMessage = !['cancel', 'close'].includes(rawJieSuanErrorMessage) ? rawJieSuanErrorMessage : ''
 return
""".strip()
    good = build_behavior_acceptance(
        title="DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭",
        demand_text="点提示框右上角 X 后，应继续关闭结算进度详情，不能再弹空提示或重复提示。",
        diff_text=good_diff,
        changed_paths=["src/components/shouFeiJs/components/Dialog.vue"],
    )
    checks.append(
        {
            "name": "behavior_allows_local_alert_close_guard",
            "status": "pass" if good.get("status") == "pass" else "failed",
            "message": good.get("summary") or "-",
        }
    )
    neutral_diff = """
diff --git a/src/pages/yeWuGn/guaHaoSf/index.vue b/src/pages/yeWuGn/guaHaoSf/index.vue
--- a/src/pages/yeWuGn/guaHaoSf/index.vue
+++ b/src/pages/yeWuGn/guaHaoSf/index.vue
@@ -1,3 +1,4 @@
+this.loadingPaiBan(paiBanList)
+this.rootPaiBanList = paiBanList
""".strip()
    neutral = build_behavior_acceptance(
        title="【运城口腔】挂号收费页面排班过滤",
        demand_text="菜单路由参数 paiBanMs 控制医生为空或有医生排班过滤，空值保持默认模式。",
        diff_text=neutral_diff,
        changed_paths=["src/pages/yeWuGn/guaHaoSf/index.vue"],
    )
    checks.append(
        {
            "name": "behavior_skips_non_interaction_paiban_loading_method",
            "status": "pass" if neutral.get("status") == "skipped" else "failed",
            "message": neutral.get("summary") or "-",
        }
    )
    return checks


def run_interaction_evidence_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    try:
        from app.interaction_evidence import build_interaction_evidence_package, interaction_evidence_to_markdown
        from app.method_test_runner import run_method_test_commands
        from app.ui_capture_template import write_playwright_capture_template
        from app.ui_evidence_runner import run_ui_evidence_commands
    except Exception as exc:
        return [
            {
                "name": "interaction_evidence_module_available",
                "status": "failed",
                "message": str(exc),
            }
        ]

    diff_text = """
diff --git a/src/components/shouFeiJs/components/Dialog.vue b/src/components/shouFeiJs/components/Dialog.vue
--- a/src/components/shouFeiJs/components/Dialog.vue
+++ b/src/components/shouFeiJs/components/Dialog.vue
@@ -1,7 +1,13 @@
-await this.$alert(message, '提示')
+await this.$alert(message, '提示').catch(alertAction => {
+  console.warn(`[门诊收费][${jieSuanTraceId}] 自动三方退费提示被关闭`, alertAction)
+})
 this.closeSettlementProgress()
 return
""".strip()
    title = "DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭"
    demand_text = "点提示框右上角 X 后，应继续关闭结算进度详情，不能再弹空提示或重复提示。"
    changed_paths = ["src/components/shouFeiJs/components/Dialog.vue"]
    behavior = build_behavior_acceptance(
        title=title,
        demand_text=demand_text,
        diff_text=diff_text,
        changed_paths=changed_paths,
    )
    evidence_file = output_dir / "interaction_evidence_fixture" / "dfhis-31446-progress-closed.png"
    evidence_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_file.write_bytes(b"fake png bytes for v0.10.2 evidence manifest")
    passed = build_interaction_evidence_package(
        title=title,
        demand_text=demand_text,
        diff_text=diff_text,
        changed_paths=changed_paths,
        behavior_acceptance=behavior,
        method_evidence={
            "cases": [
                {
                    "id": "METHOD-ALERT-RESOLVE",
                    "status": "pass",
                    "evidence": "模拟点击确定后继续关闭结算进度详情。",
                },
                {
                    "id": "METHOD-ALERT-CLOSE",
                    "status": "pass",
                    "evidence": "模拟 alert close/cancel reject 后未进入外层业务失败 catch。",
                },
                {
                    "id": "METHOD-SETTLEMENT-CLEANUP",
                    "status": "pass",
                    "evidence": "确认 closeSettlementProgress 与 return 收尾仍执行。",
                },
                {
                    "id": "METHOD-NO-REPEATED-ALERT",
                    "status": "pass",
                    "evidence": "确认关闭提示后不会二次弹空提示或泛化失败文案。",
                },
            ]
        },
        ui_evidence_paths=[str(evidence_file)],
    )
    gate = passed.get("gate") or {}
    method_result = passed.get("method_regression_result") or {}
    ui_manifest = passed.get("ui_evidence_manifest") or {}
    checks.append(
        {
            "name": "interaction_evidence_passes_with_method_and_ui_evidence",
            "status": (
                "pass"
                if passed.get("status") == "pass"
                and method_result.get("status") == "pass"
                and ui_manifest.get("status") == "present"
                and gate.get("auto_commit_allowed") is True
                and gate.get("yunxiao_comment_allowed") is True
                and gate.get("yunxiao_transition_allowed") is False
                else "failed"
            ),
            "message": passed.get("summary") or "-",
        }
    )

    missing = build_interaction_evidence_package(
        title=title,
        demand_text=demand_text,
        diff_text=diff_text,
        changed_paths=changed_paths,
        behavior_acceptance=behavior,
        method_evidence={},
        ui_evidence_paths=[],
    )
    missing_gate = missing.get("gate") or {}
    checks.append(
        {
            "name": "interaction_evidence_blocks_commit_without_method_result",
            "status": (
                "pass"
                if missing.get("status") == "needs_evidence"
                and missing_gate.get("auto_commit_allowed") is False
                and missing_gate.get("yunxiao_comment_allowed") is False
                else "failed"
            ),
            "message": missing.get("summary") or "-",
        }
    )

    markdown = interaction_evidence_to_markdown(passed)
    checks.append(
        {
            "name": "interaction_evidence_markdown_lists_required_artifacts",
            "status": (
                "pass"
                if "behavior_test_plan" in json.dumps(passed, ensure_ascii=False)
                and "method_regression_result" in json.dumps(passed, ensure_ascii=False)
                and "ui_evidence_manifest" in json.dumps(passed, ensure_ascii=False)
                and "v0.10.2 方法级交互测试与 UI 证据" in markdown
                else "failed"
            ),
            "message": "v0.10.2 artifact package generated",
        }
    )
    runner_fixture = output_dir / "method_runner_fixture"
    runner_fixture.mkdir(parents=True, exist_ok=True)
    runner_result = run_method_test_commands(
        behavior_test_plan=passed.get("behavior_test_plan") or {},
        commands=[
            "python3 -c 'import json; print(json.dumps({\"cases\":[{\"id\":\"METHOD-ALERT-RESOLVE\",\"status\":\"pass\",\"evidence\":\"resolve ok\"},{\"id\":\"METHOD-ALERT-CLOSE\",\"status\":\"pass\",\"evidence\":\"close ok\"},{\"id\":\"METHOD-NO-REPEATED-ALERT\",\"status\":\"pass\",\"evidence\":\"no repeat\"},{\"id\":\"METHOD-SETTLEMENT-CLEANUP\",\"status\":\"pass\",\"evidence\":\"cleanup ok\"}]}))'"
        ],
        cwd=runner_fixture,
    )
    checks.append(
        {
            "name": "method_test_runner_builds_method_evidence_from_command",
            "status": (
                "pass"
                if runner_result.get("status") == "pass"
                and len(runner_result.get("cases") or []) == 4
                and all(item.get("source") == "method_test_runner" for item in runner_result.get("cases") or [])
                else "failed"
            ),
            "message": runner_result.get("summary") or "-",
        }
    )
    ui_runner_fixture = output_dir / "ui_runner_fixture"
    ui_runner_fixture.mkdir(parents=True, exist_ok=True)
    ui_runner_result = run_ui_evidence_commands(
        commands=[
            "python3 -c 'import json, os, pathlib; d=pathlib.Path(os.environ[\"HARNESS_UI_EVIDENCE_DIR\"]); (d/\"progress_closed.png\").write_bytes(b\"fake png bytes\"); print(json.dumps({\"artifacts\":[{\"path\":\"progress_closed.png\",\"kind\":\"screenshot\",\"label\":\"进度详情已关闭\"}],\"assertions\":[{\"name\":\"dialog_count\",\"status\":\"pass\",\"evidence\":\"未出现重复弹框\"},{\"name\":\"loading_closed\",\"status\":\"pass\",\"evidence\":\"loading 已关闭\"}]}))'"
        ],
        cwd=ui_runner_fixture,
        output_dir=ui_runner_fixture / "evidence",
    )
    checks.append(
        {
            "name": "ui_evidence_runner_captures_artifact_and_state_assertions",
            "status": (
                "pass"
                if ui_runner_result.get("status") == "pass"
                and len(ui_runner_result.get("artifact_paths") or []) == 1
                and Path((ui_runner_result.get("artifact_paths") or [""])[0]).is_file()
                and all(item.get("status") == "pass" for item in ui_runner_result.get("assertions") or [])
                else "failed"
            ),
            "message": ui_runner_result.get("summary") or "-",
        }
    )
    capture_template_dir = output_dir / "ui_capture_template_fixture"
    template_result = write_playwright_capture_template(
        output_dir=capture_template_dir,
        entity_id="DFHIS-31446",
        title=title,
        route="/menzhen/shoufei",
        scenario_name="三方支付提示关闭后进度详情关闭",
    )
    script_path = Path(template_result.get("script_path") or "")
    env_path = Path(template_result.get("env_example_path") or "")
    manual_path = Path(template_result.get("manual_record_path") or "")
    script_text = script_path.read_text(encoding="utf-8") if script_path.is_file() else ""
    env_text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    manual_text = manual_path.read_text(encoding="utf-8") if manual_path.is_file() else ""
    checks.append(
        {
            "name": "playwright_capture_template_documents_login_state_and_outputs_runner_json",
            "status": (
                "pass"
                if template_result.get("status") == "pass"
                and script_path.is_file()
                and env_path.is_file()
                and manual_path.is_file()
                and "HARNESS_UI_EVIDENCE_DIR" in script_text
                and "HIS_UI_STORAGE_STATE" in script_text
                and '"artifacts"' in script_text
                and '"assertions"' in script_text
                and "HIS_UI_BASE_URL" in env_text
                and "HIS_UI_ROUTE" in env_text
                and "HIS_UI_STORAGE_STATE" in env_text
                and "DFHIS-31446" in manual_text
                else "failed"
            ),
            "message": template_result.get("summary") or "-",
        }
    )
    fixture_repo = create_interaction_precommit_fixture(output_dir / "interaction_precommit_fixture")
    verify_command = (
        "python3 -c \"from pathlib import Path; "
        "text = Path('src/components/shouFeiJs/components/Dialog.vue').read_text(); "
        "assert 'closeSettlementProgress' in text and '.catch(' in text\""
    )
    missing_precommit = _SelfCheckPrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=9401,
            project_root=str(output_dir),
            project_path=str(fixture_repo),
            allowed_paths=["src/components/shouFeiJs/components/Dialog.vue"],
            verify_commands=[verify_command],
            title=title,
            entity_id="DFHIS-31446",
            demand_text=demand_text,
            worktree_root=str(output_dir / "interaction_precommit_worktrees"),
        )
    )
    checks.append(
        {
            "name": "precommit_blocks_interaction_without_method_evidence",
            "status": (
                "pass"
                if missing_precommit.status == "failed"
                and (missing_precommit.interaction_evidence or {}).get("status") == "needs_evidence"
                and missing_precommit.verification_matrix.get("can_commit") is False
                and missing_precommit.verification_matrix.get("can_yunxiao_comment") is False
                else "failed"
            ),
            "message": missing_precommit.summary,
        }
    )
    passed_precommit = _SelfCheckPrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=9402,
            project_root=str(output_dir),
            project_path=str(fixture_repo),
            allowed_paths=["src/components/shouFeiJs/components/Dialog.vue"],
            verify_commands=[verify_command],
            title=title,
            entity_id="DFHIS-31446",
            demand_text=demand_text,
            method_evidence={
                "cases": [
                    {"id": "METHOD-ALERT-RESOLVE", "status": "pass", "evidence": "resolve path passed"},
                    {"id": "METHOD-ALERT-CLOSE", "status": "pass", "evidence": "close/cancel path passed"},
                    {"id": "METHOD-NO-REPEATED-ALERT", "status": "pass", "evidence": "no repeated alert"},
                    {"id": "METHOD-SETTLEMENT-CLEANUP", "status": "pass", "evidence": "cleanup path passed"},
                ]
            },
            ui_evidence_paths=[str(evidence_file)],
            worktree_root=str(output_dir / "interaction_precommit_worktrees"),
        )
    )
    checks.append(
        {
            "name": "precommit_allows_interaction_with_method_and_ui_evidence",
            "status": (
                "pass"
                if passed_precommit.status == "success"
                and (passed_precommit.interaction_evidence or {}).get("status") == "pass"
                and passed_precommit.verification_matrix.get("can_commit") is True
                and passed_precommit.verification_matrix.get("can_yunxiao_comment") is True
                and ((passed_precommit.interaction_evidence or {}).get("gate") or {}).get("yunxiao_comment_allowed") is True
                and passed_precommit.verification_matrix.get("can_yunxiao_transition") is False
                else "failed"
            ),
            "message": passed_precommit.summary,
        }
    )
    runner_precommit = _SelfCheckPrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=9403,
            project_root=str(output_dir),
            project_path=str(fixture_repo),
            allowed_paths=["src/components/shouFeiJs/components/Dialog.vue"],
            verify_commands=[verify_command],
            title=title,
            entity_id="DFHIS-31446",
            demand_text=demand_text,
            method_test_commands=[
                "python3 -c 'import json; print(json.dumps({\"cases\":[{\"id\":\"METHOD-ALERT-RESOLVE\",\"status\":\"pass\",\"evidence\":\"resolve ok\"},{\"id\":\"METHOD-ALERT-CLOSE\",\"status\":\"pass\",\"evidence\":\"close ok\"},{\"id\":\"METHOD-NO-REPEATED-ALERT\",\"status\":\"pass\",\"evidence\":\"no repeat\"},{\"id\":\"METHOD-SETTLEMENT-CLEANUP\",\"status\":\"pass\",\"evidence\":\"cleanup ok\"}]}))'"
            ],
            ui_evidence_paths=[str(evidence_file)],
            worktree_root=str(output_dir / "interaction_precommit_worktrees"),
        )
    )
    checks.append(
        {
            "name": "precommit_runs_method_test_command_as_evidence",
            "status": (
                "pass"
                if runner_precommit.status == "success"
                and ((runner_precommit.interaction_evidence or {}).get("method_regression_result") or {}).get("status") == "pass"
                and ((runner_precommit.manifest or {}).get("method_test_runner") or {}).get("status") == "pass"
                and runner_precommit.verification_matrix.get("can_commit") is True
                else "failed"
            ),
            "message": runner_precommit.summary,
        }
    )
    ui_runner_precommit = _SelfCheckPrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=9404,
            project_root=str(output_dir),
            project_path=str(fixture_repo),
            allowed_paths=["src/components/shouFeiJs/components/Dialog.vue"],
            verify_commands=[verify_command],
            title=title,
            entity_id="DFHIS-31446",
            demand_text=demand_text,
            method_test_commands=[
                "python3 -c 'import json; print(json.dumps({\"cases\":[{\"id\":\"METHOD-ALERT-RESOLVE\",\"status\":\"pass\",\"evidence\":\"resolve ok\"},{\"id\":\"METHOD-ALERT-CLOSE\",\"status\":\"pass\",\"evidence\":\"close ok\"},{\"id\":\"METHOD-NO-REPEATED-ALERT\",\"status\":\"pass\",\"evidence\":\"no repeat\"},{\"id\":\"METHOD-SETTLEMENT-CLEANUP\",\"status\":\"pass\",\"evidence\":\"cleanup ok\"}]}))'"
            ],
            ui_capture_commands=[
                "python3 -c 'import json, os, pathlib; d=pathlib.Path(os.environ[\"HARNESS_UI_EVIDENCE_DIR\"]); (d/\"progress_closed.png\").write_bytes(b\"fake png bytes\"); print(json.dumps({\"artifacts\":[{\"path\":\"progress_closed.png\",\"kind\":\"screenshot\",\"label\":\"进度详情已关闭\"}],\"assertions\":[{\"name\":\"dialog_count\",\"status\":\"pass\",\"evidence\":\"未出现重复弹框\"}]}))'"
            ],
            worktree_root=str(output_dir / "interaction_precommit_worktrees"),
        )
    )
    checks.append(
        {
            "name": "precommit_runs_ui_capture_command_as_ui_evidence",
            "status": (
                "pass"
                if ui_runner_precommit.status == "success"
                and ((ui_runner_precommit.manifest or {}).get("ui_evidence_runner") or {}).get("status") == "pass"
                and ((ui_runner_precommit.interaction_evidence or {}).get("ui_evidence_manifest") or {}).get("status") == "present"
                and ui_runner_precommit.verification_matrix.get("can_yunxiao_comment") is True
                else "failed"
            ),
            "message": ui_runner_precommit.summary,
        }
    )
    return checks


def run_task_manager_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    original_db_path = database.DB_PATH
    task_db_path = output_dir / "task_manager" / "harness.sqlite"
    sample_output = create_task_manager_existing_output_fixture(output_dir / "task_manager" / "existing_precommit_output").resolve()
    try:
        if task_db_path.exists():
            task_db_path.unlink()
        database.DB_PATH = task_db_path
        database.init_db()
        manager = _SelfCheckTaskManager()
        task, task_run = manager.record_existing_run(
            TaskExistingRunOptions(
                yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-31465",
                title="【运城口腔】挂号窗口新增'科室'过滤条件",
                entity_kind="requirement",
                entity_id="DFHIS-31465",
                project_root="/Users/lym/Desktop/dongFang/dfcode",
                project_paths=["/Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf"],
                output_dir=str(sample_output),
                execution_mode="precommit-verify",
                notes="self-check existing precommit output registration",
            )
        )
        repeated_task, repeated_task_run = manager.record_existing_run(
            TaskExistingRunOptions(
                yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-31465",
                title="【运城口腔】挂号窗口新增'科室'过滤条件",
                entity_kind="requirement",
                entity_id="DFHIS-31465",
                project_root="/Users/lym/Desktop/dongFang/dfcode",
                project_paths=["/Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf"],
                output_dir=str(sample_output),
                execution_mode="precommit-verify",
                notes="self-check existing precommit output registration repeated",
            )
        )
        runs = manager.list_task_runs(int(task["id"]))
        run_row = database.get_run(int(task["latest_run_id"])) if task.get("latest_run_id") else None
        latest_artifacts = task.get("latest_artifacts") or {}
        checks.append(
            {
                "name": "task_manager_registers_existing_precommit_output",
                "status": (
                    "pass"
                    if task.get("task_key") == "requirement-dfhis-31465"
                    and task.get("current_stage") == "precommit_verify"
                    and task.get("status") == "success"
                    and task.get("verification_status") == "passed"
                    and task.get("can_commit") is False
                    and task.get("latest_output_dir") == str(sample_output)
                    and task_run.get("output_dir") == str(sample_output)
                    and task_run.get("run_id") == task.get("latest_run_id")
                    and runs
                    and run_row is not None
                    and {"precommit_manifest", "verification_matrix", "code_review", "commit_ready_summary"}.issubset(latest_artifacts)
                    and {"task_manager_record_json", "task_manager_record_md"}.issubset(task_run.get("artifact_paths") or {})
                    and {"task_manager_run_history_json", "task_manager_run_history_md"}.issubset(task_run.get("artifact_paths") or {})
                    and {"ui_evidence_reuse_policy_json", "ui_evidence_reuse_policy_md"}.issubset(task_run.get("artifact_paths") or {})
                    else "failed"
                ),
                "message": (
                    f"task_id={task.get('id')}; run_id={task.get('latest_run_id')}; "
                    f"status={task.get('status')}; verification={task.get('verification_status')}; output={task.get('latest_output_dir')}"
                ),
            }
        )
        checks.append(
            {
                "name": "task_manager_register_run_is_idempotent_for_same_output_dir",
                "status": (
                    "pass"
                    if repeated_task.get("id") == task.get("id")
                    and repeated_task_run.get("id") == task_run.get("id")
                    and repeated_task_run.get("run_id") == task_run.get("run_id")
                    and len(manager.list_task_runs(int(task["id"]))) == 1
                    else "failed"
                ),
                "message": (
                    f"first_task_run={task_run.get('id')} repeated_task_run={repeated_task_run.get('id')} "
                    f"run_count={len(manager.list_task_runs(int(task['id'])))}"
                ),
            }
        )
        rerun_repo = create_untracked_precommit_fixture(output_dir / "task_manager" / "rerun_precommit_fixture")
        rerun_task = manager.create_task(
            TaskCreateOptions(
                title="自测：Task Manager precommit 复跑",
                entity_kind="requirement",
                entity_id="DFHIS-RERUN",
                project_root=str(output_dir),
                project_paths=[str(rerun_repo)],
            )
        )
        rerun_task, rerun_result, rerun_output = manager.rerun_precommit(
            TaskPrecommitRerunOptions(
                task_id=int(rerun_task["id"]),
                project_root=str(output_dir),
                project_path=str(rerun_repo),
                allowed_paths=["src/App.js", "src/helper.js"],
                verify_commands=[
                    "python3 -c \"from pathlib import Path; assert Path('src/helper.js').is_file(); assert \\\"require('./helper')\\\" in Path('src/App.js').read_text()\""
                ],
                demand_text="自测：Task Manager 应能从任务记录复跑 precommit 并登记 run。",
                output_root=str(output_dir / "task_manager" / "rerun_outputs"),
                worktree_dir=str(output_dir / "task_manager" / "rerun_worktrees"),
            )
        )
        rerun_runs = manager.list_task_runs(int(rerun_task["id"]))
        checks.append(
            {
                "name": "task_manager_reruns_precommit_and_registers_output",
                "status": (
                    "pass"
                    if rerun_result.status == "success"
                    and (rerun_output / "precommit_manifest.json").exists()
                    and rerun_task.get("latest_output_dir") == str(rerun_output)
                    and rerun_runs
                    and rerun_runs[0].get("run_id") == rerun_task.get("latest_run_id")
                    and "task_manager_run_history_json" in (rerun_runs[0].get("artifact_paths") or {})
                    else "failed"
                ),
                "message": (
                    f"status={rerun_result.status}; run_id={rerun_task.get('latest_run_id')}; output={rerun_output}"
                ),
            }
        )
        dashboard_output_dir = output_dir / "task_manager" / "dashboard"
        dashboard = manager.build_dashboard(limit=20)
        dashboard_files = manager.write_dashboard_outputs(output_dir=dashboard_output_dir, dashboard=dashboard)
        task_items = dashboard.get("tasks") or []
        dfhis_item = next((item for item in task_items if item.get("task_key") == "requirement-dfhis-31465"), {})
        rerun_item = next((item for item in task_items if item.get("task_key") == "requirement-dfhis-rerun"), {})
        checks.append(
            {
                "name": "task_manager_dashboard_exports_readonly_task_view",
                "status": (
                    "pass"
                    if dashboard.get("version") == "0.10.10-task-dashboard"
                    and dashboard.get("summary", {}).get("task_count") == 2
                    and dashboard.get("summary", {}).get("run_count") == 2
                    and dfhis_item.get("ui_evidence", {}).get("status") == "present"
                    and dfhis_item.get("latest_artifact_count", 0) >= 6
                    and rerun_item.get("latest_run", {}).get("status") == "success"
                    and rerun_item.get("can_yunxiao_transition") is False
                    and (dashboard_output_dir / "task_dashboard.json").exists()
                    and (dashboard_output_dir / "task_dashboard.md").exists()
                    and (dashboard_output_dir / "task_dashboard.html").exists()
                    and (dashboard_output_dir / "task_sample_set.json").exists()
                    and (dashboard_output_dir / "task_sample_set.md").exists()
                    and {"json", "markdown", "html", "sample_set_json", "sample_set_markdown"}.issubset(dashboard_files)
                    else "failed"
                ),
                "message": (
                    f"tasks={dashboard.get('summary', {}).get('task_count')} "
                    f"runs={dashboard.get('summary', {}).get('run_count')} "
                    f"dfhis_ui={dfhis_item.get('ui_evidence', {}).get('status')}"
                ),
            }
        )
        filtered_output_dir = output_dir / "task_manager" / "dashboard_filtered"
        filtered_dashboard = manager.build_dashboard(
            limit=20,
            filters=TaskDashboardFilters(
                entity_id="DFHIS-31465",
                verification_status="passed",
                ui_evidence_status="present",
                can_commit=False,
                sample_only=True,
            ),
        )
        filtered_files = manager.write_dashboard_outputs(output_dir=filtered_output_dir, dashboard=filtered_dashboard)
        filtered_items = filtered_dashboard.get("tasks") or []
        sample_set = filtered_dashboard.get("sample_set") or {}
        samples = sample_set.get("samples") or []
        checks.append(
            {
                "name": "task_manager_dashboard_filters_and_exports_sample_set",
                "status": (
                    "pass"
                    if filtered_dashboard.get("version") == "0.10.10-task-dashboard"
                    and filtered_dashboard.get("filters", {}).get("entity_id") == "DFHIS-31465"
                    and filtered_dashboard.get("filters", {}).get("can_commit") is False
                    and filtered_dashboard.get("filters", {}).get("sample_only") is True
                    and filtered_dashboard.get("summary", {}).get("task_count") == 1
                    and len(filtered_items) == 1
                    and filtered_items[0].get("task_key") == "requirement-dfhis-31465"
                    and sample_set.get("version") == "0.10.10-real-sample-set"
                    and sample_set.get("count") == 1
                    and samples
                    and samples[0].get("task_key") == "requirement-dfhis-31465"
                    and samples[0].get("ui_evidence_status") == "present"
                    and (filtered_output_dir / "task_sample_set.json").exists()
                    and (filtered_output_dir / "task_sample_set.md").exists()
                    and {"sample_set_json", "sample_set_markdown"}.issubset(filtered_files)
                    else "failed"
                ),
                "message": (
                    f"filters={filtered_dashboard.get('filters')}; "
                    f"tasks={filtered_dashboard.get('summary', {}).get('task_count')}; "
                    f"samples={sample_set.get('count')}"
                ),
            }
        )
        workbench_output_dir = output_dir / "task_manager" / "workbench"
        workbench = manager.build_task_workbench(task_key="requirement-dfhis-31465")
        workbench_files = manager.write_workbench_outputs(output_dir=workbench_output_dir, workbench=workbench)
        workbench_task = workbench.get("task") or {}
        workbench_runs = workbench.get("runs") or []
        workbench_artifacts = workbench.get("artifacts") or []
        workbench_commands = workbench.get("commands") or {}
        workbench_calibration = workbench.get("requirement_calibration") or {}
        workbench_markdown = (workbench_output_dir / "task_workbench.md").read_text(encoding="utf-8") if (workbench_output_dir / "task_workbench.md").exists() else ""
        checks.append(
            {
                "name": "task_manager_workbench_exports_task_run_artifacts_and_copyable_rerun_command",
                "status": (
                    "pass"
                    if workbench.get("version") == "0.24-task-workbench"
                    and workbench.get("readonly") is True
                    and workbench_task.get("task_key") == "requirement-dfhis-31465"
                    and workbench_task.get("entity_id") == "DFHIS-31465"
                    and len(workbench_runs) == 1
                    and workbench_runs[0].get("run_id") == task.get("latest_run_id")
                    and any(item.get("kind") == "precommit_manifest" and item.get("exists") for item in workbench_artifacts)
                    and any(item.get("kind") == "ui_evidence_reuse_policy_json" and item.get("exists") for item in workbench_artifacts)
                    and any(item.get("kind") == "requirement_calibration_json" and item.get("exists") for item in workbench_artifacts)
                    and any(item.get("kind") == "requirement_calibration_md" and item.get("exists") for item in workbench_artifacts)
                    and workbench_calibration.get("status") == "ready_for_development"
                    and workbench_calibration.get("parameter_names") == ["paiBanMs"]
                    and workbench_calibration.get("markdown_path", "").endswith("requirement_calibration.md")
                    and "需求理解确认卡" in workbench_markdown
                    and "用户补充规则优先" in workbench_markdown
                    and "paiBanMs" in workbench_markdown
                    and "tools/task_manager.py rerun-precommit" in (workbench_commands.get("rerun_precommit") or "")
                    and "--task-key requirement-dfhis-31465" in (workbench_commands.get("rerun_precommit") or "")
                    and "--project-path /Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf" in (workbench_commands.get("rerun_precommit") or "")
                    and (workbench_output_dir / "task_workbench.json").exists()
                    and (workbench_output_dir / "task_workbench.md").exists()
                    and {"json", "markdown"}.issubset(workbench_files)
                    else "failed"
                ),
                "message": (
                    f"task={workbench_task.get('task_key')}; runs={len(workbench_runs)}; "
                    f"artifacts={len(workbench_artifacts)}; rerun={bool(workbench_commands.get('rerun_precommit'))}"
                ),
            }
        )
        workspace_output_dir = output_dir / "task_manager" / "workspace"
        workspace = manager.build_task_workspace(limit=20)
        workspace_files = manager.write_workspace_outputs(output_dir=workspace_output_dir, workspace=workspace)
        workspace_entries = workspace.get("entries") or []
        dfhis_workspace_entry = next((item for item in workspace_entries if item.get("task_key") == "requirement-dfhis-31465"), {})
        dfhis_workspace_calibration = dfhis_workspace_entry.get("requirement_calibration") or {}
        workspace_html_path = workspace_output_dir / "task_workspace.html"
        workspace_html = workspace_html_path.read_text(encoding="utf-8") if workspace_html_path.exists() else ""
        checks.append(
            {
                "name": "task_manager_workspace_exports_html_entry_linking_dashboard_sample_set_and_workbench",
                "status": (
                    "pass"
                    if workspace.get("version") == "0.21-task-workspace"
                    and workspace.get("readonly") is True
                    and workspace.get("summary", {}).get("task_count") == 2
                    and workspace.get("sample_set", {}).get("count") == 1
                    and dfhis_workspace_entry.get("task_key") == "requirement-dfhis-31465"
                    and dfhis_workspace_entry.get("workbench_markdown") == "workbenches/requirement-dfhis-31465/task_workbench.md"
                    and dfhis_workspace_calibration.get("status") == "ready_for_development"
                    and dfhis_workspace_calibration.get("markdown_link") == "workbenches/requirement-dfhis-31465/requirement_calibration.md"
                    and dfhis_workspace_entry.get("filter_data", {}).get("requirement_calibration_status") == "ready_for_development"
                    and "paiBanMs" in (dfhis_workspace_entry.get("search_text") or "")
                    and "tools/task_manager.py rerun-precommit" in (dfhis_workspace_entry.get("rerun_precommit") or "")
                    and (workspace_output_dir / "task_workspace.json").exists()
                    and (workspace_output_dir / "task_workspace.html").exists()
                    and (workspace_output_dir / "task_dashboard.html").exists()
                    and (workspace_output_dir / "task_sample_set.json").exists()
                    and (workspace_output_dir / "workbenches" / "requirement-dfhis-31465" / "task_workbench.md").exists()
                    and (workspace_output_dir / "workbenches" / "requirement-dfhis-31465" / "requirement_calibration.md").exists()
                    and {"json", "html", "dashboard_html", "sample_set_json", "workbench_files"}.issubset(workspace_files)
                    and "requirement-dfhis-31465" in workspace_html
                    and "需求理解确认卡" in workspace_html
                    and "ready_for_development" in workspace_html
                    and "workbenches/requirement-dfhis-31465/requirement_calibration.md" in workspace_html
                    and "task_dashboard.html" in workspace_html
                    and "task_sample_set.json" in workspace_html
                    and "workbenches/requirement-dfhis-31465/task_workbench.md" in workspace_html
                    and "tools/task_manager.py rerun-precommit" in workspace_html
                    else "failed"
                ),
                "message": (
                    f"tasks={workspace.get('summary', {}).get('task_count')}; "
                    f"samples={workspace.get('sample_set', {}).get('count')}; "
                    f"entries={len(workspace_entries)}"
                ),
            }
        )
        change_one_diff = sample_output / "change_1.diff"
        change_one_diff.write_text(
            "\n".join(
                [
                    "diff --git a/src/views/guahao/GuaHao.vue b/src/views/guahao/GuaHao.vue",
                    "index 1111111..2222222 100644",
                    "--- a/src/views/guahao/GuaHao.vue",
                    "+++ b/src/views/guahao/GuaHao.vue",
                    "@@ -10,6 +10,7 @@ export default {",
                    "   created () {",
                    "+    const paiBanMs = this.$route.query.paiBanMs",
                    "     this.loadPaiBan()",
                    "   }",
                    " }",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        change_two_diff = sample_output / "change_2.diff"
        change_two_diff.write_text(
            "\n".join(
                [
                    "diff --git a/src/views/guahao/GuaHao.vue b/src/views/guahao/GuaHao.vue",
                    "index 2222222..3333333 100644",
                    "--- a/src/views/guahao/GuaHao.vue",
                    "+++ b/src/views/guahao/GuaHao.vue",
                    "@@ -18,7 +18,9 @@ export default {",
                    "   computed: {",
                    "-    filteredPaiBan () { return this.paiBanList }",
                    "+    filteredPaiBan () {",
                    "+      return filterByPaiBanMs(this.paiBanList, this.$route.query.paiBanMs)",
                    "+    }",
                    "   }",
                    " }",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        try:
            first_change = manager.record_change(
                {
                    "task_id": int(task["id"]),
                    "task_run_id": int(task_run["id"]),
                    "run_id": int(task_run["run_id"]),
                    "source_type": "self-check",
                    "project_path": "/Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf",
                    "allowed_paths": ["src/views/guahao/GuaHao.vue"],
                    "diff_path": str(change_one_diff),
                    "verification_status": "passed",
                    "notes": "第一次修改：读取 paiBanMs。",
                }
            )
            second_change = manager.record_change(
                {
                    "task_id": int(task["id"]),
                    "task_run_id": int(task_run["id"]),
                    "run_id": int(task_run["run_id"]),
                    "source_type": "self-check",
                    "project_path": "/Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf",
                    "allowed_paths": ["src/views/guahao/GuaHao.vue"],
                    "diff_path": str(change_two_diff),
                    "verification_status": "passed",
                    "notes": "第二次修改：按 paiBanMs 过滤排班。",
                }
            )
            change_history = manager.list_task_changes(int(task["id"]))
            rollback_output_dir = output_dir / "task_manager" / "rollback_plan"
            rollback_plan = manager.build_change_rollback_plan(
                {
                    "task_key": "requirement-dfhis-31465",
                    "target_change_sequence": 2,
                    "output_dir": str(rollback_output_dir),
                }
            )
            workbench_v017_output_dir = output_dir / "task_manager" / "workbench_v017"
            workbench_v017 = manager.build_task_workbench(task_key="requirement-dfhis-31465")
            workbench_v017_files = manager.write_workbench_outputs(output_dir=workbench_v017_output_dir, workbench=workbench_v017)
            workbench_v017_markdown = (workbench_v017_output_dir / "task_workbench.md").read_text(encoding="utf-8")
            change_history_markdown = (workbench_v017_output_dir / "task_change_history.md").read_text(encoding="utf-8")
            workspace_v017_output_dir = output_dir / "task_manager" / "workspace_v017"
            workspace_v017 = manager.build_task_workspace(limit=20)
            workspace_v017_files = manager.write_workspace_outputs(output_dir=workspace_v017_output_dir, workspace=workspace_v017)
            workspace_v017_entry = next((item for item in workspace_v017.get("entries") or [] if item.get("task_key") == "requirement-dfhis-31465"), {})
            workspace_v017_html = (workspace_v017_output_dir / "task_workspace.html").read_text(encoding="utf-8")
            workspace_snapshot_history_output_dir = output_dir / "task_manager" / "workspace_snapshot_history"
            manager.write_workspace_outputs(output_dir=workspace_snapshot_history_output_dir, workspace=workspace_v017)
            reverse_patch_path = Path(str(rollback_plan.get("reverse_patch_path") or ""))
            reverse_patch_text = reverse_patch_path.read_text(encoding="utf-8") if reverse_patch_path.exists() else ""
            rollback_commands = rollback_plan.get("commands") or {}
            workbench_change_history = workbench_v017.get("change_history") or {}
            checks.append(
                {
                    "name": "task_manager_change_history_and_rollback_dry_run_are_readonly",
                    "status": (
                        "pass"
                        if first_change.get("change_sequence") == 1
                        and second_change.get("change_sequence") == 2
                        and len(change_history) == 2
                        and workbench_v017.get("version") == "0.24-task-workbench"
                        and workbench_change_history.get("change_count") == 2
                        and (workbench_change_history.get("latest_change") or {}).get("change_sequence") == 2
                        and workspace_v017.get("version") == "0.21-task-workspace"
                        and workspace_v017_entry.get("change_count") == 2
                        and (workspace_v017_entry.get("change_history") or {}).get("rollback_mode") in {"dry_run_only", "local_transaction"}
                        and rollback_plan.get("version") == "0.17-rollback-dry-run"
                        and rollback_plan.get("dry_run_only") is True
                        and rollback_plan.get("will_modify_files") is False
                        and rollback_plan.get("target_change_sequence") == 2
                        and rollback_plan.get("status") == "ready_for_manual_review"
                        and "git apply --reverse --check" in (rollback_commands.get("apply_reverse_patch_check") or "")
                        and str(rollback_plan.get("source_diff_path") or "").endswith("final.diff")
                        and str((second_change.get("metadata") or {}).get("source_diff_path") or "").endswith("change_2.diff")
                        and reverse_patch_path.exists()
                        and "filterByPaiBanMs" in reverse_patch_text
                        and "修改历史" in workbench_v017_markdown
                        and "回滚 dry-run" in workbench_v017_markdown
                        and "修改历史" in change_history_markdown
                        and "回滚 dry-run" in change_history_markdown
                        and "修改历史" in workspace_v017_html
                        and "回滚 dry-run" in workspace_v017_html
                        and {"change_history_json", "change_history_markdown"}.issubset(workbench_v017_files)
                        and {"json", "html", "workbench_files"}.issubset(workspace_v017_files)
                        else "failed"
                    ),
                    "message": (
                        f"changes={len(change_history)}; latest={workbench_change_history.get('latest_change')}; "
                        f"rollback={rollback_plan.get('plan_path')}; reverse_patch={rollback_plan.get('reverse_patch_path')}"
                    ),
                }
            )
            workspace_v017_details = workspace_v017.get("task_details") or []
            dfhis_workspace_detail = next((item for item in workspace_v017_details if item.get("task_key") == "requirement-dfhis-31465"), {})
            detail_commands = dfhis_workspace_detail.get("commands") or {}
            detail_change_history = dfhis_workspace_detail.get("change_history") or {}
            detail_calibration = dfhis_workspace_detail.get("requirement_calibration") or {}
            detail_evidence_preview = dfhis_workspace_detail.get("evidence_preview") or {}
            detail_evidence_sections = detail_evidence_preview.get("sections") or []
            detail_evidence_kinds = {
                str(item.get("kind") or "")
                for item in detail_evidence_sections
                if isinstance(item, dict)
            }
            detail_artifact_kinds = {
                str(item.get("kind") or "")
                for item in dfhis_workspace_detail.get("artifacts") or []
                if isinstance(item, dict)
            }
            workspace_v017_html_readonly = all(
                marker not in workspace_v017_html
                for marker in ["fetch(", "XMLHttpRequest", "exec(", "child_process"]
            )
            checks.append(
                {
                    "name": "task_manager_workspace_detail_tabs_and_evidence_preview_are_readonly",
                    "status": (
                        "pass"
                        if workspace_v017.get("version") == "0.21-task-workspace"
                        and dfhis_workspace_detail.get("task_key") == "requirement-dfhis-31465"
                        and dfhis_workspace_detail.get("readonly") is True
                        and len(dfhis_workspace_detail.get("runs") or []) >= 1
                        and {"precommit_manifest", "verification_matrix", "requirement_calibration_json"}.issubset(detail_artifact_kinds)
                        and detail_calibration.get("status") == "ready_for_development"
                        and detail_calibration.get("parameter_names") == ["paiBanMs"]
                        and detail_change_history.get("change_count") == 2
                        and "tools/task_manager.py rerun-precommit" in (detail_commands.get("rerun_precommit") or "")
                        and "tools/task_manager.py rollback-plan" in (detail_commands.get("rollback_dry_run") or "")
                        and {"requirement_calibration", "verification_matrix", "ui_evidence_manifest", "task_change_history"}.issubset(detail_evidence_kinds)
                        and 'id="task-detail-panel"' in workspace_v017_html
                        and 'data-detail-task-key="requirement-dfhis-31465"' in workspace_v017_html
                        and 'data-tab="overview"' in workspace_v017_html
                        and 'data-tab="runs"' in workspace_v017_html
                        and 'data-tab="calibration"' in workspace_v017_html
                        and 'data-tab="requirement-evidence"' in workspace_v017_html
                        and 'data-tab="changes"' in workspace_v017_html
                        and 'data-tab="rollback"' in workspace_v017_html
                        and 'data-tab="evidence"' in workspace_v017_html
                        and 'data-tab="commands"' in workspace_v017_html
                        and "任务详情" in workspace_v017_html
                        and "Run 历史" in workspace_v017_html
                        and "需求理解确认卡" in workspace_v017_html
                        and "需求来源证据" in workspace_v017_html
                        and "修改历史" in workspace_v017_html
                        and "回滚 dry-run" in workspace_v017_html
                        and "证据预览" in workspace_v017_html
                        and "可复制命令" in workspace_v017_html
                        and "showTaskDetail" in workspace_v017_html
                        and "switchDetailTab" in workspace_v017_html
                        and "task_change_history.md" in workspace_v017_html
                        and "requirement_calibration.md" in workspace_v017_html
                        and "verification_matrix.json" in workspace_v017_html
                        and workspace_v017_html_readonly
                        else "failed"
                    ),
                    "message": (
                        f"details={len(workspace_v017_details)}; "
                        f"detail_task={dfhis_workspace_detail.get('task_key')}; "
                        f"tabs={'data-tab' in workspace_v017_html}; "
                        f"evidence={sorted(detail_evidence_kinds)}"
                    ),
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": "task_manager_change_history_and_rollback_dry_run_are_readonly",
                    "status": "failed",
                    "message": redact_secrets(str(exc)),
                }
            )
        stale_output = create_task_manager_existing_output_fixture(output_dir / "task_manager" / "existing_precommit_output_without_latest_ui").resolve()
        for stale_ui_file in [
            stale_output / "ui_evidence_manifest.json",
            stale_output / "ui_evidence_manifest.md",
            stale_output / "manual_acceptance_DFHIS-31465.md",
        ]:
            if stale_ui_file.exists():
                stale_ui_file.unlink()
        task_with_second_run, second_task_run = manager.record_existing_run(
            TaskExistingRunOptions(
                task_id=int(task["id"]),
                yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-31465",
                title="【运城口腔】挂号窗口新增'科室'过滤条件",
                entity_kind="requirement",
                entity_id="DFHIS-31465",
                project_root="/Users/lym/Desktop/dongFang/dfcode",
                project_paths=["/Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf"],
                output_dir=str(stale_output),
                execution_mode="precommit-verify",
                notes="self-check second run without latest UI evidence",
            )
        )
        if (stale_output / "verification_matrix.json").exists():
            (stale_output / "verification_matrix.json").unlink()
        stale_artifacts = build_latest_artifacts(output_dir=stale_output)
        database.update_task_run(int(second_task_run["id"]), artifact_paths=stale_artifacts)
        database.update_task(int(task_with_second_run["id"]), latest_artifacts=stale_artifacts)
        workbench_v013_output_dir = output_dir / "task_manager" / "workbench_v013"
        workbench_v013 = manager.build_task_workbench(task_key="requirement-dfhis-31465")
        workbench_v013_files = manager.write_workbench_outputs(output_dir=workbench_v013_output_dir, workbench=workbench_v013)
        comparison = workbench_v013.get("run_history_comparison") or {}
        warning_items = workbench_v013.get("evidence_warnings") or []
        workspace_v013_output_dir = output_dir / "task_manager" / "workspace_v013"
        workspace_v013 = manager.build_task_workspace(limit=20)
        workspace_v013_files = manager.write_workspace_outputs(output_dir=workspace_v013_output_dir, workspace=workspace_v013)
        workspace_v013_entry = next((item for item in workspace_v013.get("entries") or [] if item.get("task_key") == "requirement-dfhis-31465"), {})
        workspace_v013_html = (workspace_v013_output_dir / "task_workspace.html").read_text(encoding="utf-8")
        workspace_warning_summary = workspace_v013.get("warning_summary") or {}
        workspace_warning_summary_codes = {
            item.get("code")
            for item in workspace_warning_summary.get("codes") or []
            if isinstance(item, dict)
        }
        workspace_filter_options = workspace_v013.get("filter_options") or {}
        workspace_entry_filter_data = workspace_v013_entry.get("filter_data") or {}
        workspace_entry_search_text = workspace_v013_entry.get("search_text") or ""
        workspace_v018_history = manager.build_task_workspace(limit=20)
        workspace_v018_history_files = manager.write_workspace_outputs(output_dir=workspace_snapshot_history_output_dir, workspace=workspace_v018_history)
        workspace_v018_history_html = (workspace_snapshot_history_output_dir / "task_workspace.html").read_text(encoding="utf-8")
        workspace_export_index = workspace_v018_history.get("export_index") or {}
        workspace_snapshot_comparison = workspace_v018_history.get("snapshot_comparison") or {}
        workspace_snapshot_summary_delta = workspace_snapshot_comparison.get("summary_delta") or {}
        workspace_snapshot_changed_tasks = workspace_snapshot_comparison.get("changed_tasks") or []
        workspace_snapshot_dfhis_change = next(
            (item for item in workspace_snapshot_changed_tasks if item.get("task_key") == "requirement-dfhis-31465"),
            {},
        )
        workspace_snapshot_changed_fields = set(workspace_snapshot_dfhis_change.get("changed_fields") or [])
        checks.append(
            {
                "name": "task_manager_run_history_comparison_and_stale_evidence_warnings",
                "status": (
                    "pass"
                    if workbench_v013.get("version") == "0.24-task-workbench"
                    and comparison.get("run_count") == 2
                    and comparison.get("latest_run", {}).get("task_run_id") == second_task_run.get("id")
                    and comparison.get("previous_run", {}).get("task_run_id") == task_run.get("id")
                    and comparison.get("latest_run", {}).get("ui_evidence_status") == "missing"
                    and comparison.get("previous_run", {}).get("ui_evidence_status") == "present"
                    and any(item.get("code") == "latest_ui_evidence_missing_but_previous_present" for item in warning_items)
                    and any(item.get("code") == "latest_artifact_missing" and item.get("kind") == "verification_matrix" for item in warning_items)
                    and workspace_v013.get("version") == "0.21-task-workspace"
                    and workspace_v013_entry.get("warning_count", 0) >= 2
                    and "latest_ui_evidence_missing_but_previous_present" in workspace_v013_html
                    and "latest_artifact_missing" in workspace_v013_html
                    and {"json", "markdown"}.issubset(workbench_v013_files)
                    and {"json", "html", "workbench_files"}.issubset(workspace_v013_files)
                    else "failed"
                ),
                "message": (
                    f"runs={comparison.get('run_count')}; warnings={len(warning_items)}; "
                    f"workspace_warnings={workspace_v013_entry.get('warning_count')}"
                ),
            }
        )
        checks.append(
            {
                "name": "task_manager_workspace_snapshot_comparison_and_export_index_are_readonly",
                "status": (
                    "pass"
                    if workspace_v018_history.get("version") == "0.21-task-workspace"
                    and workspace_export_index.get("version") == "0.19-workspace-export-index"
                    and workspace_export_index.get("readonly") is True
                    and workspace_export_index.get("file_count", 0) >= 10
                    and {"workspace", "dashboard", "sample_set", "workbenches"}.issubset(
                        {str(item.get("group") or "") for item in workspace_export_index.get("groups") or [] if isinstance(item, dict)}
                    )
                    and workspace_snapshot_comparison.get("version") == "0.18-workspace-snapshot-comparison"
                    and workspace_snapshot_comparison.get("readonly") is True
                    and workspace_snapshot_comparison.get("compared") is True
                    and workspace_snapshot_summary_delta.get("warning_count_delta", 0) >= 2
                    and workspace_snapshot_summary_delta.get("task_count_delta") == 0
                    and workspace_snapshot_dfhis_change.get("task_key") == "requirement-dfhis-31465"
                    and {"warning_count", "run_count"}.issubset(workspace_snapshot_changed_fields)
                    and (workspace_snapshot_history_output_dir / "task_workspace_export_index.json").exists()
                    and (workspace_snapshot_history_output_dir / "task_workspace_export_index.md").exists()
                    and (workspace_snapshot_history_output_dir / "task_workspace_snapshot_comparison.json").exists()
                    and (workspace_snapshot_history_output_dir / "task_workspace_snapshot_comparison.md").exists()
                    and {"export_index_json", "export_index_markdown", "snapshot_comparison_json", "snapshot_comparison_markdown"}.issubset(workspace_v018_history_files)
                    and 'id="workspace-export-index"' in workspace_v018_history_html
                    and 'id="workspace-snapshot-comparison"' in workspace_v018_history_html
                    and "导出索引" in workspace_v018_history_html
                    and "历史快照对比" in workspace_v018_history_html
                    and "task_workspace_export_index.json" in workspace_v018_history_html
                    and "task_workspace_snapshot_comparison.json" in workspace_v018_history_html
                    and all(
                        marker not in workspace_v018_history_html
                        for marker in ["fetch(", "XMLHttpRequest", "exec(", "child_process"]
                    )
                    else "failed"
                ),
                "message": (
                    f"version={workspace_v018_history.get('version')}; "
                    f"export_files={workspace_export_index.get('file_count')}; "
                    f"comparison={workspace_snapshot_comparison.get('summary_delta')}; "
                    f"changed={workspace_snapshot_dfhis_change}"
                ),
            }
        )
        workspace_v019_history = manager.build_task_workspace(limit=20)
        workspace_v019_history_files = manager.write_workspace_outputs(output_dir=workspace_snapshot_history_output_dir, workspace=workspace_v019_history)
        workspace_v019_history_html = (workspace_snapshot_history_output_dir / "task_workspace.html").read_text(encoding="utf-8")
        workspace_snapshot_history = workspace_v019_history.get("snapshot_history") or {}
        workspace_evidence_trend = workspace_v019_history.get("evidence_trend") or {}
        workspace_snapshot_records = workspace_snapshot_history.get("snapshots") or []
        workspace_snapshot_comparisons = workspace_snapshot_history.get("comparisons") or []
        workspace_trend_tasks = workspace_evidence_trend.get("tasks") or []
        dfhis_trend = next((item for item in workspace_trend_tasks if item.get("task_key") == "requirement-dfhis-31465"), {})
        dfhis_trend_points = dfhis_trend.get("points") or []
        dfhis_trend_ui_statuses = {str(item.get("ui_evidence_status") or "") for item in dfhis_trend_points if isinstance(item, dict)}
        dfhis_trend_warning_counts = [int(item.get("warning_count") or 0) for item in dfhis_trend_points if isinstance(item, dict)]
        snapshot_file_paths = [
            workspace_snapshot_history_output_dir / str(item.get("relative_path") or "")
            for item in workspace_snapshot_records
            if isinstance(item, dict) and item.get("relative_path")
        ]
        checks.append(
            {
                "name": "task_manager_workspace_multi_snapshot_browser_and_evidence_trend_are_readonly",
                "status": (
                    "pass"
                    if workspace_v019_history.get("version") == "0.21-task-workspace"
                    and workspace_snapshot_history.get("version") == "0.19-workspace-snapshot-history"
                    and workspace_snapshot_history.get("readonly") is True
                    and len(workspace_snapshot_records) >= 3
                    and len(workspace_snapshot_comparisons) >= 3
                    and all(path.exists() for path in snapshot_file_paths)
                    and workspace_evidence_trend.get("version") == "0.19-workspace-evidence-trend"
                    and workspace_evidence_trend.get("readonly") is True
                    and dfhis_trend.get("task_key") == "requirement-dfhis-31465"
                    and len(dfhis_trend_points) >= 3
                    and {"present", "missing"}.issubset(dfhis_trend_ui_statuses)
                    and max(dfhis_trend_warning_counts or [0]) >= 2
                    and (workspace_snapshot_history_output_dir / "task_workspace_snapshot_history.json").exists()
                    and (workspace_snapshot_history_output_dir / "task_workspace_snapshot_history.md").exists()
                    and (workspace_snapshot_history_output_dir / "task_workspace_evidence_trend.json").exists()
                    and (workspace_snapshot_history_output_dir / "task_workspace_evidence_trend.md").exists()
                    and {"snapshot_history_json", "snapshot_history_markdown", "evidence_trend_json", "evidence_trend_markdown"}.issubset(workspace_v019_history_files)
                    and 'id="workspace-snapshot-history"' in workspace_v019_history_html
                    and 'id="workspace-evidence-trend"' in workspace_v019_history_html
                    and 'id="snapshot-base-select"' in workspace_v019_history_html
                    and 'id="snapshot-target-select"' in workspace_v019_history_html
                    and "showSelectedSnapshotComparison" in workspace_v019_history_html
                    and "证据状态趋势" in workspace_v019_history_html
                    and "task_workspace_snapshot_history.json" in workspace_v019_history_html
                    and "task_workspace_evidence_trend.json" in workspace_v019_history_html
                    and all(
                        marker not in workspace_v019_history_html
                        for marker in ["fetch(", "XMLHttpRequest", "exec(", "child_process"]
                    )
                    else "failed"
                ),
                "message": (
                    f"snapshots={len(workspace_snapshot_records)}; "
                    f"comparisons={len(workspace_snapshot_comparisons)}; "
                    f"trend_points={len(dfhis_trend_points)}; "
                    f"ui_statuses={sorted(dfhis_trend_ui_statuses)}"
                ),
            }
        )
        workspace_navigation = workspace_v019_history.get("navigation") or {}
        workspace_snapshot_detail = workspace_v019_history.get("snapshot_detail") or {}
        snapshot_detail_items = workspace_snapshot_detail.get("snapshots") or []
        snapshot_detail_dfhis = any(
            any(task.get("task_key") == "requirement-dfhis-31465" for task in item.get("task_summaries") or [])
            for item in snapshot_detail_items
            if isinstance(item, dict)
        )
        checks.append(
            {
                "name": "task_manager_workspace_navigation_snapshot_detail_and_evidence_preview_are_readonly",
                "status": (
                    "pass"
                    if workspace_v019_history.get("version") == "0.21-task-workspace"
                    and workspace_navigation.get("version") == "0.20-workspace-navigation"
                    and workspace_navigation.get("readonly") is True
                    and {"workspace-overview", "workspace-tasks", "task-detail-panel", "workspace-snapshot-detail-panel", "workspace-evidence-trend", "workspace-export-index"}.issubset(
                        {str(item.get("section_id") or "") for item in workspace_navigation.get("sections") or [] if isinstance(item, dict)}
                    )
                    and workspace_snapshot_detail.get("version") == "0.20-workspace-snapshot-detail"
                    and workspace_snapshot_detail.get("readonly") is True
                    and len(snapshot_detail_items) >= 3
                    and snapshot_detail_dfhis
                    and 'id="workspace-nav"' in workspace_v019_history_html
                    and 'data-nav-target="workspace-overview"' in workspace_v019_history_html
                    and 'id="workspace-overview"' in workspace_v019_history_html
                    and 'id="workspace-tasks"' in workspace_v019_history_html
                    and 'id="workspace-snapshot-detail-panel"' in workspace_v019_history_html
                    and 'id="snapshot-detail-select"' in workspace_v019_history_html
                    and 'data-snapshot-detail-id=' in workspace_v019_history_html
                    and "showSnapshotDetail" in workspace_v019_history_html
                    and "快照详情" in workspace_v019_history_html
                    and "任务摘要" in workspace_v019_history_html
                    and 'class="evidence-preview-summary"' in workspace_v019_history_html
                    and '<details class="preview-item"' in workspace_v019_history_html
                    and '<summary>' in workspace_v019_history_html
                    and all(
                        marker not in workspace_v019_history_html
                        for marker in ["fetch(", "XMLHttpRequest", "exec(", "child_process"]
                    )
                    else "failed"
                ),
                "message": (
                    f"nav_sections={len(workspace_navigation.get('sections') or [])}; "
                    f"snapshot_details={len(snapshot_detail_items)}; "
                    f"dfhis_in_snapshot_detail={snapshot_detail_dfhis}"
                ),
            }
        )
        workspace_ui_polish = workspace_v019_history.get("ui_polish") or {}
        workspace_offline_review = workspace_v019_history.get("offline_review") or {}
        workspace_ui_empty_states = workspace_ui_polish.get("empty_states") or []
        workspace_ui_error_states = workspace_ui_polish.get("error_states") or []
        checks.append(
            {
                "name": "task_manager_workspace_ui_polish_empty_error_and_offline_review_are_readonly",
                "status": (
                    "pass"
                    if workspace_v019_history.get("version") == "0.21-task-workspace"
                    and workspace_ui_polish.get("version") == "0.21-workspace-ui-polish"
                    and workspace_ui_polish.get("readonly") is True
                    and len(workspace_ui_empty_states) >= 4
                    and len(workspace_ui_error_states) >= 3
                    and workspace_offline_review.get("version") == "0.21-workspace-offline-review"
                    and workspace_offline_review.get("readonly") is True
                    and workspace_offline_review.get("file_count", 0) >= 10
                    and (workspace_snapshot_history_output_dir / "task_workspace_offline_review.json").exists()
                    and (workspace_snapshot_history_output_dir / "task_workspace_offline_review.md").exists()
                    and {"offline_review_json", "offline_review_markdown"}.issubset(workspace_v019_history_files)
                    and 'id="workspace-offline-review"' in workspace_v019_history_html
                    and 'class="workspace-empty-state"' in workspace_v019_history_html
                    and 'data-empty-kind="no-tasks"' in workspace_v019_history_html
                    and 'data-error-kind="missing-artifact"' in workspace_v019_history_html
                    and 'class="workspace-table-wrap"' in workspace_v019_history_html
                    and 'class="status-pill"' in workspace_v019_history_html
                    and "离线审查包" in workspace_v019_history_html
                    and "空态说明" in workspace_v019_history_html
                    and "错误态说明" in workspace_v019_history_html
                    and "task_workspace_offline_review.json" in workspace_v019_history_html
                    and "task_workspace_offline_review.md" in workspace_v019_history_html
                    and all(
                        marker not in workspace_v019_history_html
                        for marker in ["fetch(", "XMLHttpRequest", "exec(", "child_process"]
                    )
                    else "failed"
                ),
                "message": (
                    f"ui_polish={workspace_ui_polish.get('version')}; "
                    f"empty_states={len(workspace_ui_empty_states)}; "
                    f"error_states={len(workspace_ui_error_states)}; "
                    f"offline_files={workspace_offline_review.get('file_count', 0)}"
                ),
            }
        )
        checks.append(
            {
                "name": "task_manager_workspace_warning_summary_filters_and_search",
                "status": (
                    "pass"
                    if workspace_v013.get("version") == "0.21-task-workspace"
                    and workspace_warning_summary.get("total_warning_count", 0) >= 2
                    and workspace_warning_summary.get("task_count_with_warnings", 0) >= 1
                    and "latest_ui_evidence_missing_but_previous_present" in workspace_warning_summary_codes
                    and "latest_artifact_missing" in workspace_warning_summary_codes
                    and "latest_ui_evidence_missing_but_previous_present" in (workspace_filter_options.get("warning_codes") or [])
                    and "latest_artifact_missing" in (workspace_filter_options.get("warning_codes") or [])
                    and "DFHIS-31465" in (workspace_filter_options.get("entity_ids") or [])
                    and workspace_entry_filter_data.get("entity_id") == "DFHIS-31465"
                    and "latest_artifact_missing" in (workspace_entry_filter_data.get("warning_codes") or [])
                    and "DFHIS-31465" in workspace_entry_search_text
                    and "latest_ui_evidence_missing_but_previous_present" in workspace_entry_search_text
                    and 'id="workspace-search"' in workspace_v013_html
                    and 'id="warning-filter"' in workspace_v013_html
                    and 'class="warning-summary"' in workspace_v013_html
                    and "applyWorkspaceFilters" in workspace_v013_html
                    and "data-warning-codes" in workspace_v013_html
                    and "data-entity-id" in workspace_v013_html
                    and "data-verification-status" in workspace_v013_html
                    and "data-ui-evidence-status" in workspace_v013_html
                    else "failed"
                ),
                "message": (
                    f"warning_total={workspace_warning_summary.get('total_warning_count')}; "
                    f"warning_codes={sorted(workspace_warning_summary_codes)}; "
                    f"filter_warning_codes={workspace_filter_options.get('warning_codes')}"
                ),
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "task_manager_registers_existing_precommit_output",
                "status": "failed",
                "message": redact_secrets(str(exc)),
            }
        )
    finally:
        database.DB_PATH = original_db_path
    return checks


def run_configuration_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    config_output = output_dir / "configuration"
    config_output.mkdir(parents=True, exist_ok=True)
    credentials_file = config_output / "credentials.local.json"
    credentials_file.write_text(
        json.dumps(
            {
                "aliyun_devops_pat": "self-check-yunxiao-token",
                "aliyun_devops_organization_id": "self-check-org",
                "openai_api_key": "self-check-openai-token",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        from app.harness_config import build_config_summary, config_summary_to_markdown

        summary = build_config_summary(
            profile_key="dfhis-local-example",
            credentials_file=credentials_file,
            check_keychain=False,
        )
        markdown = config_summary_to_markdown(summary)
        config_json = config_output / "config_summary.json"
        config_md = config_output / "config_summary.md"
        config_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        config_md.write_text(markdown, encoding="utf-8")
        credential_items = summary.get("credentials", {}).get("items") or []
        credential_status = {item.get("key"): item for item in credential_items if isinstance(item, dict)}
        hard_guards = summary.get("rule_pack", {}).get("hard_guards") or {}
        checks.append(
            {
                "name": "rule_pack_profile_and_credentials_are_secret_free_and_compatible",
                "status": (
                    "pass"
                    if summary.get("version") == "0.22-harness-config-summary"
                    and summary.get("readonly") is True
                    and summary.get("profile", {}).get("key") == "dfhis-local-example"
                    and summary.get("rule_pack", {}).get("rule_pack_id") == "dfhis-default"
                    and summary.get("compatibility", {}).get("default_harness_behavior") == "unchanged_without_explicit_config"
                    and hard_guards.get("no_secret_printing") is True
                    and hard_guards.get("external_writes_default") == "off"
                    and hard_guards.get("real_status_transition_requires_confirmation") is True
                    and credential_status.get("aliyun_devops_pat", {}).get("status") == "configured"
                    and credential_status.get("aliyun_devops_pat", {}).get("source", "").startswith("file:")
                    and credential_status.get("aliyun_devops_pat", {}).get("masked_value", "").endswith("oken")
                    and "self-check-yunxiao-token" not in markdown
                    and "self-check-openai-token" not in markdown
                    and "Rule Pack" in markdown
                    and "Credential Store" in markdown
                    and config_json.exists()
                    and config_md.exists()
                    else "failed"
                ),
                "message": (
                    f"profile={summary.get('profile', {}).get('key')}; "
                    f"rule_pack={summary.get('rule_pack', {}).get('rule_pack_id')}; "
                    f"credentials={[item.get('key') + ':' + item.get('status') for item in credential_items if isinstance(item, dict)]}"
                ),
            }
        )
        original_db_path = database.DB_PATH
        try:
            config_workspace_db = config_output / "workspace_config.sqlite"
            if config_workspace_db.exists():
                config_workspace_db.unlink()
            database.DB_PATH = config_workspace_db
            database.init_db()
            config_workspace_dir = config_output / "workspace"
            manager = _SelfCheckTaskManager()
            workspace = manager.build_task_workspace(limit=10, config_summary=summary)
            files = manager.write_workspace_outputs(output_dir=config_workspace_dir, workspace=workspace)
            workspace_html = (config_workspace_dir / "task_workspace.html").read_text(encoding="utf-8")
            default_workspace = manager.build_task_workspace(limit=10)
            checks.append(
                {
                    "name": "task_workspace_config_summary_is_explicit_readonly_and_legacy_default_unchanged",
                    "status": (
                        "pass"
                        if workspace.get("version") == "0.22-task-workspace"
                        and workspace.get("readonly") is True
                        and (workspace.get("configuration") or {}).get("version") == "0.22-harness-config-summary"
                        and (config_workspace_dir / "task_workspace_config_summary.json").exists()
                        and (config_workspace_dir / "task_workspace_config_summary.md").exists()
                        and {"config_summary_json", "config_summary_markdown"}.issubset(files)
                        and 'id="workspace-configuration"' in workspace_html
                        and "Rule Pack" in workspace_html
                        and "Credential Store" in workspace_html
                        and "self-check-yunxiao-token" not in workspace_html
                        and "self-check-openai-token" not in workspace_html
                        and default_workspace.get("version") == "0.21-task-workspace"
                        and not default_workspace.get("configuration")
                        else "failed"
                    ),
                    "message": (
                        f"configured_version={workspace.get('version')}; "
                        f"default_version={default_workspace.get('version')}; "
                        f"files={sorted(files.keys())}"
                    ),
                }
            )
            try:
                from app.harness_config import (
                    build_configuration_preview,
                    configuration_preview_to_markdown,
                    write_configuration_preview_outputs,
                )

                preview = build_configuration_preview(summary)
                preview_markdown = configuration_preview_to_markdown(preview)
                preview_files = write_configuration_preview_outputs(output_dir=config_output / "preview", preview=preview)
                preview_provider_types = {
                    str(item.get("source_type") or "")
                    for item in preview.get("provider_templates") or []
                    if isinstance(item, dict)
                }
                preview_workspace_dir = config_output / "workspace_preview"
                preview_workspace = manager.build_task_workspace(limit=10, config_summary=summary, config_preview=preview)
                preview_workspace_files = manager.write_workspace_outputs(
                    output_dir=preview_workspace_dir,
                    workspace=preview_workspace,
                )
                preview_workspace_html = (preview_workspace_dir / "task_workspace.html").read_text(encoding="utf-8")
                default_preview_workspace = manager.build_task_workspace(limit=10)
                checks.append(
                    {
                        "name": "configuration_preview_templates_are_readonly_shareable_and_explicit",
                        "status": (
                            "pass"
                            if preview.get("version") == "0.25-configuration-preview"
                            and preview.get("readonly") is True
                            and preview.get("external_writes_enabled") is False
                            and preview.get("credential_values_exposed") is False
                            and {"yunxiao", "tapd", "manual", "file"}.issubset(preview_provider_types)
                            and all(item.get("mode") == "local_draft" for item in preview.get("provider_templates") or [] if isinstance(item, dict))
                            and all(item.get("external_write_enabled") is False for item in preview.get("provider_templates") or [] if isinstance(item, dict))
                            and (preview.get("workflow_rules") or {}).get("comment_template")
                            and (preview.get("workflow_rules") or {}).get("status_flow")
                            and "self-check-yunxiao-token" not in json.dumps(preview, ensure_ascii=False)
                            and "self-check-openai-token" not in json.dumps(preview, ensure_ascii=False)
                            and "Provider 模板" in preview_markdown
                            and "不会读取远端" in preview_markdown
                            and Path(preview_files.get("json") or "").exists()
                            and Path(preview_files.get("markdown") or "").exists()
                            and preview_workspace.get("version") == "0.25-task-workspace"
                            and (preview_workspace.get("configuration_preview") or {}).get("version") == "0.25-configuration-preview"
                            and {"config_preview_json", "config_preview_markdown"}.issubset(preview_workspace_files)
                            and (preview_workspace_dir / "task_workspace_config_preview.json").exists()
                            and (preview_workspace_dir / "task_workspace_config_preview.md").exists()
                            and 'id="workspace-configuration-preview"' in preview_workspace_html
                            and "配置预览" in preview_workspace_html
                            and "Provider 模板" in preview_workspace_html
                            and "不会读取远端" in preview_workspace_html
                            and "self-check-yunxiao-token" not in preview_workspace_html
                            and "self-check-openai-token" not in preview_workspace_html
                            and default_preview_workspace.get("version") == "0.21-task-workspace"
                            and not default_preview_workspace.get("configuration_preview")
                            else "failed"
                        ),
                        "message": (
                            f"preview_version={preview.get('version')}; "
                            f"providers={sorted(preview_provider_types)}; "
                            f"workspace_version={preview_workspace.get('version')}; "
                            f"files={sorted(preview_workspace_files.keys())}"
                        ),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "configuration_preview_templates_are_readonly_shareable_and_explicit",
                        "status": "failed",
                        "message": redact_secrets(str(exc)),
                    }
                )
            try:
                from app.harness_config import (
                    build_configuration_preview,
                    build_configuration_share_validation,
                    configuration_share_validation_to_markdown,
                    write_configuration_share_validation_outputs,
                )

                share_preview = build_configuration_preview(summary)
                share_validation = build_configuration_share_validation(
                    summary=summary,
                    rule_pack_path=None,
                    profile_config_path=None,
                )
                share_validation_markdown = configuration_share_validation_to_markdown(share_validation)
                share_validation_files = write_configuration_share_validation_outputs(
                    output_dir=config_output / "share_validation",
                    validation=share_validation,
                )
                share_workspace_dir = config_output / "workspace_share_validation"
                share_workspace = manager.build_task_workspace(
                    limit=10,
                    config_summary=summary,
                    config_preview=share_preview,
                    config_share_validation=share_validation,
                )
                share_workspace_files = manager.write_workspace_outputs(
                    output_dir=share_workspace_dir,
                    workspace=share_workspace,
                )
                share_workspace_html = (share_workspace_dir / "task_workspace.html").read_text(encoding="utf-8")
                override_strategy = share_validation.get("local_override_strategy") or {}
                precedence = override_strategy.get("precedence") or []
                checks.append(
                    {
                        "name": "configuration_share_validation_blocks_secrets_and_documents_local_override_strategy",
                        "status": (
                            "pass"
                            if share_validation.get("version") == "0.26-configuration-share-validation"
                            and share_validation.get("readonly") is True
                            and share_validation.get("will_apply_configuration") is False
                            and share_validation.get("external_writes_enabled") is False
                            and share_validation.get("status") == "pass"
                            and precedence
                            and precedence[0].get("kind") == "cli_args"
                            and any("~/.his-harness/profiles.json" in str(item.get("path") or "") for item in precedence)
                            and "团队分享包校验" in share_validation_markdown
                            and "不会应用配置" in share_validation_markdown
                            and "self-check-yunxiao-token" not in json.dumps(share_validation, ensure_ascii=False)
                            and "self-check-openai-token" not in json.dumps(share_validation, ensure_ascii=False)
                            and Path(share_validation_files.get("json") or "").exists()
                            and Path(share_validation_files.get("markdown") or "").exists()
                            and share_workspace.get("version") == "0.26-task-workspace"
                            and (share_workspace.get("config_share_validation") or {}).get("version") == "0.26-configuration-share-validation"
                            and {"config_share_validation_json", "config_share_validation_markdown"}.issubset(share_workspace_files)
                            and (share_workspace_dir / "task_workspace_config_share_validation.json").exists()
                            and (share_workspace_dir / "task_workspace_config_share_validation.md").exists()
                            and 'id="workspace-config-share-validation"' in share_workspace_html
                            and "配置分享校验" in share_workspace_html
                            and "本地覆盖策略" in share_workspace_html
                            and "不会应用配置" in share_workspace_html
                            and "self-check-yunxiao-token" not in share_workspace_html
                            and "self-check-openai-token" not in share_workspace_html
                            else "failed"
                        ),
                        "message": (
                            f"validation_version={share_validation.get('version')}; "
                            f"status={share_validation.get('status')}; "
                            f"issue_count={len(share_validation.get('issues') or [])}; "
                            f"workspace_version={share_workspace.get('version')}; "
                            f"files={sorted(share_workspace_files.keys())}"
                        ),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "configuration_share_validation_blocks_secrets_and_documents_local_override_strategy",
                        "status": "failed",
                        "message": redact_secrets(str(exc)),
                    }
                )
            try:
                from app.harness_config import (
                    build_configuration_import_draft,
                    configuration_import_draft_to_markdown,
                    write_configuration_import_draft_outputs,
                )

                import_preview = build_configuration_preview(summary)
                import_share_validation = build_configuration_share_validation(
                    summary=summary,
                    rule_pack_path=None,
                    profile_config_path=None,
                )
                draft_target_dir = config_output / f"import_drafts_{uuid.uuid4().hex}"
                import_draft = build_configuration_import_draft(
                    summary=summary,
                    rule_pack_path=None,
                    profile_config_path=None,
                    draft_output_dir=draft_target_dir,
                    overwrite=False,
                )
                import_draft_markdown = configuration_import_draft_to_markdown(import_draft)
                import_draft_files = write_configuration_import_draft_outputs(
                    output_dir=draft_target_dir,
                    draft=import_draft,
                    overwrite=False,
                )
                blocked_result = write_configuration_import_draft_outputs(
                    output_dir=draft_target_dir,
                    draft=import_draft,
                    overwrite=False,
                )
                import_workspace_dir = config_output / "workspace_import_draft"
                import_workspace = manager.build_task_workspace(
                    limit=10,
                    config_summary=summary,
                    config_preview=import_preview,
                    config_share_validation=import_share_validation,
                    config_import_draft=import_draft,
                )
                import_workspace_files = manager.write_workspace_outputs(
                    output_dir=import_workspace_dir,
                    workspace=import_workspace,
                )
                import_workspace_html = (import_workspace_dir / "task_workspace.html").read_text(encoding="utf-8")
                default_import_workspace = manager.build_task_workspace(limit=10)
                required_draft_files = {
                    "profiles_draft": draft_target_dir / "profiles.draft.json",
                    "rule_pack_draft": draft_target_dir / "rule_pack.draft.json",
                    "credentials_example": draft_target_dir / "credentials.example.json",
                    "import_guide": draft_target_dir / "IMPORT_GUIDE.md",
                    "manifest": draft_target_dir / "config_import_manifest.json",
                }
                draft_json_text = json.dumps(import_draft, ensure_ascii=False)
                checks.append(
                    {
                        "name": "configuration_import_draft_generates_user_selected_secret_free_files",
                        "status": (
                            "pass"
                            if import_draft.get("version") == "0.27-configuration-import-draft"
                            and import_draft.get("readonly") is True
                            and import_draft.get("will_apply_configuration") is False
                            and import_draft.get("writes_only_to_user_selected_dir") is True
                            and import_draft.get("draft_output_dir") == str(draft_target_dir.resolve())
                            and import_draft_files.get("status") == "created"
                            and blocked_result.get("status") == "blocked_existing_files"
                            and all(path.exists() for path in required_draft_files.values())
                            and {"profiles_draft", "rule_pack_draft", "credentials_example", "import_guide", "manifest"}.issubset(import_draft_files)
                            and "配置导入草案" in import_draft_markdown
                            and "不会应用配置" in import_draft_markdown
                            and "self-check-yunxiao-token" not in draft_json_text
                            and "self-check-openai-token" not in draft_json_text
                            and "self-check-yunxiao-token" not in (required_draft_files["credentials_example"]).read_text(encoding="utf-8")
                            and "self-check-openai-token" not in (required_draft_files["credentials_example"]).read_text(encoding="utf-8")
                            and import_workspace.get("version") == "0.27-task-workspace"
                            and (import_workspace.get("config_import_draft") or {}).get("version") == "0.27-configuration-import-draft"
                            and {"config_import_draft_json", "config_import_draft_markdown"}.issubset(import_workspace_files)
                            and (import_workspace_dir / "task_workspace_config_import_draft.json").exists()
                            and (import_workspace_dir / "task_workspace_config_import_draft.md").exists()
                            and 'id="workspace-config-import-draft"' in import_workspace_html
                            and "配置导入草案" in import_workspace_html
                            and "用户选择目录" in import_workspace_html
                            and "不会应用配置" in import_workspace_html
                            and "self-check-yunxiao-token" not in import_workspace_html
                            and "self-check-openai-token" not in import_workspace_html
                            and default_import_workspace.get("version") == "0.21-task-workspace"
                            and not default_import_workspace.get("config_import_draft")
                            else "failed"
                        ),
                        "message": (
                            f"draft_version={import_draft.get('version')}; "
                            f"write_status={import_draft_files.get('status')}; "
                            f"blocked_status={blocked_result.get('status')}; "
                            f"workspace_version={import_workspace.get('version')}; "
                            f"files={sorted(import_workspace_files.keys())}"
                        ),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "configuration_import_draft_generates_user_selected_secret_free_files",
                        "status": "failed",
                        "message": redact_secrets(str(exc)),
                    }
                )
            try:
                from app.harness_config import (
                    build_configuration_import_review,
                    configuration_import_review_to_markdown,
                    write_configuration_import_review_outputs,
                )

                review_target_dir = draft_target_dir
                import_review = build_configuration_import_review(draft_dir=review_target_dir)
                import_review_markdown = configuration_import_review_to_markdown(import_review)
                import_review_files = write_configuration_import_review_outputs(
                    output_dir=config_output / "import_review",
                    review=import_review,
                )
                review_workspace_dir = config_output / "workspace_import_review"
                review_workspace = manager.build_task_workspace(
                    limit=10,
                    config_summary=summary,
                    config_preview=import_preview,
                    config_share_validation=import_share_validation,
                    config_import_draft=import_draft,
                    config_import_review=import_review,
                )
                review_workspace_files = manager.write_workspace_outputs(
                    output_dir=review_workspace_dir,
                    workspace=review_workspace,
                )
                review_workspace_html = (review_workspace_dir / "task_workspace.html").read_text(encoding="utf-8")
                default_review_workspace = manager.build_task_workspace(limit=10)
                form_preview = import_review.get("form_preview") or {}
                form_field_names = {
                    str(field.get("name") or "")
                    for section in form_preview.get("sections") or []
                    if isinstance(section, dict)
                    for field in section.get("fields") or []
                    if isinstance(field, dict)
                }
                review_json_text = json.dumps(import_review, ensure_ascii=False)
                checks.append(
                    {
                        "name": "configuration_import_review_reads_back_drafts_and_shows_readonly_form_preview",
                        "status": (
                            "pass"
                            if import_review.get("version") == "0.28-configuration-import-review"
                            and import_review.get("readonly") is True
                            and import_review.get("status") == "pass"
                            and import_review.get("will_apply_configuration") is False
                            and import_review.get("will_write_real_config_dir") is False
                            and import_review.get("remote_connection_tests_enabled") is False
                            and import_review.get("draft_input_dir") == str(review_target_dir.resolve())
                            and len(import_review.get("files") or []) == 5
                            and {"project_root", "output_root", "credential_keys", "hard_guards", "manual_confirmation"}.issubset(form_field_names)
                            and import_review.get("manual_confirmation")
                            and "配置导入回读校验" in import_review_markdown
                            and "只读表单预览" in import_review_markdown
                            and "导入前风险提示" in import_review_markdown
                            and "不会应用配置" in import_review_markdown
                            and "self-check-yunxiao-token" not in review_json_text
                            and "self-check-openai-token" not in review_json_text
                            and Path(import_review_files.get("json") or "").exists()
                            and Path(import_review_files.get("markdown") or "").exists()
                            and "self-check-yunxiao-token" not in Path(import_review_files.get("json") or "").read_text(encoding="utf-8")
                            and "self-check-openai-token" not in Path(import_review_files.get("json") or "").read_text(encoding="utf-8")
                            and review_workspace.get("version") == "0.28-task-workspace"
                            and (review_workspace.get("config_import_review") or {}).get("version") == "0.28-configuration-import-review"
                            and {"config_import_review_json", "config_import_review_markdown"}.issubset(review_workspace_files)
                            and (review_workspace_dir / "task_workspace_config_import_review.json").exists()
                            and (review_workspace_dir / "task_workspace_config_import_review.md").exists()
                            and 'id="workspace-config-import-review"' in review_workspace_html
                            and "配置导入回读校验" in review_workspace_html
                            and "只读表单预览" in review_workspace_html
                            and "导入前风险提示" in review_workspace_html
                            and "不会应用配置" in review_workspace_html
                            and "self-check-yunxiao-token" not in review_workspace_html
                            and "self-check-openai-token" not in review_workspace_html
                            and default_review_workspace.get("version") == "0.21-task-workspace"
                            and not default_review_workspace.get("config_import_review")
                            else "failed"
                        ),
                        "message": (
                            f"review_version={import_review.get('version')}; "
                            f"review_status={import_review.get('status')}; "
                            f"workspace_version={review_workspace.get('version')}; "
                            f"form_fields={sorted(form_field_names)}; "
                            f"files={sorted(review_workspace_files.keys())}"
                        ),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "configuration_import_review_reads_back_drafts_and_shows_readonly_form_preview",
                        "status": "failed",
                        "message": redact_secrets(str(exc)),
                    }
                )
            try:
                from app.harness_config import (
                    build_configuration_template_index,
                    configuration_template_index_to_markdown,
                    write_configuration_import_draft_outputs,
                    write_configuration_template_index_outputs,
                )

                template_compare_dir = config_output / "import_drafts_compare"
                compare_import_draft = json.loads(json.dumps(import_draft, ensure_ascii=False))
                compare_payloads = compare_import_draft.get("draft_payloads") or {}
                compare_profiles = (compare_payloads.get("profiles_draft") or {}).get("profiles") or []
                if compare_profiles:
                    compare_profiles[0]["key"] = "team-share-preview"
                    compare_profiles[0]["display_name"] = "团队分享预览"
                    compare_profiles[0]["requirement_provider"] = {
                        "type": "manual",
                        "name": "手工粘贴需求来源",
                        "credential_keys": [],
                        "readonly": True,
                    }
                    compare_payloads["profiles_draft"]["default_profile"] = "team-share-preview"
                compare_rule_pack = compare_payloads.get("rule_pack_draft") or {}
                if isinstance(compare_rule_pack.get("comments"), dict):
                    compare_rule_pack["comments"]["delivery_template"] = "tapd_default_delivery"
                write_configuration_import_draft_outputs(
                    output_dir=template_compare_dir,
                    draft=compare_import_draft,
                    overwrite=True,
                )
                template_index = build_configuration_template_index(
                    draft_dirs=[review_target_dir, template_compare_dir],
                )
                template_index_markdown = configuration_template_index_to_markdown(template_index)
                template_index_files = write_configuration_template_index_outputs(
                    output_dir=config_output / "template_index",
                    index=template_index,
                )
                template_workspace_dir = config_output / "workspace_template_index"
                template_workspace = manager.build_task_workspace(
                    limit=10,
                    config_summary=summary,
                    config_preview=import_preview,
                    config_share_validation=import_share_validation,
                    config_import_draft=import_draft,
                    config_import_review=import_review,
                    config_template_index=template_index,
                )
                template_workspace_files = manager.write_workspace_outputs(
                    output_dir=template_workspace_dir,
                    workspace=template_workspace,
                )
                template_workspace_html = (template_workspace_dir / "task_workspace.html").read_text(encoding="utf-8")
                default_template_workspace = manager.build_task_workspace(limit=10)
                source_previews = [
                    source.get("profile_switch_preview") or []
                    for source in template_index.get("sources") or []
                    if isinstance(source, dict)
                ]
                checks.append(
                    {
                        "name": "configuration_template_index_compares_drafts_and_previews_profile_switches",
                        "status": (
                            "pass"
                            if template_index.get("version") == "0.29-configuration-template-index"
                            and template_index.get("readonly") is True
                            and template_index.get("status") == "pass"
                            and template_index.get("source_count") == 2
                            and all(source_previews)
                            and (template_index.get("diff_summary") or {}).get("provider_type_changed") is True
                            and (template_index.get("diff_summary") or {}).get("comment_template_changed") is True
                            and (template_index.get("team_template_index") or {}).get("files")
                            and "配置模板索引" in template_index_markdown
                            and "多 Profile 切换预览" in template_index_markdown
                            and "配置差异对比" in template_index_markdown
                            and "不会应用配置" in template_index_markdown
                            and Path(template_index_files.get("json") or "").exists()
                            and Path(template_index_files.get("markdown") or "").exists()
                            and "self-check-yunxiao-token" not in json.dumps(template_index, ensure_ascii=False)
                            and "self-check-openai-token" not in json.dumps(template_index, ensure_ascii=False)
                            and template_workspace.get("version") == "0.29-task-workspace"
                            and (template_workspace.get("config_template_index") or {}).get("version") == "0.29-configuration-template-index"
                            and {"config_template_index_json", "config_template_index_markdown"}.issubset(template_workspace_files)
                            and (template_workspace_dir / "task_workspace_config_template_index.json").exists()
                            and (template_workspace_dir / "task_workspace_config_template_index.md").exists()
                            and 'id="workspace-config-template-index"' in template_workspace_html
                            and "配置模板索引" in template_workspace_html
                            and "多 Profile 切换预览" in template_workspace_html
                            and "配置差异对比" in template_workspace_html
                            and "不会应用配置" in template_workspace_html
                            and "self-check-yunxiao-token" not in template_workspace_html
                            and "self-check-openai-token" not in template_workspace_html
                            and default_template_workspace.get("version") == "0.21-task-workspace"
                            and not default_template_workspace.get("config_template_index")
                            else "failed"
                        ),
                        "message": (
                            f"index_version={template_index.get('version')}; "
                            f"status={template_index.get('status')}; "
                            f"source_count={template_index.get('source_count')}; "
                            f"diff={template_index.get('diff_summary')}; "
                            f"workspace_version={template_workspace.get('version')}; "
                            f"files={sorted(template_workspace_files.keys())}"
                        ),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "configuration_template_index_compares_drafts_and_previews_profile_switches",
                        "status": "failed",
                        "message": redact_secrets(str(exc)),
                    }
                )
            try:
                from app.harness_config import (
                    build_configuration_wizard,
                    configuration_wizard_to_markdown,
                    write_configuration_wizard_outputs,
                )

                config_wizard = build_configuration_wizard(
                    config_summary=summary,
                    config_preview=import_preview,
                    config_share_validation=import_share_validation,
                    config_import_draft=import_draft,
                    config_import_review=import_review,
                    config_template_index=template_index,
                    draft_input_dir=review_target_dir,
                )
                config_wizard_markdown = configuration_wizard_to_markdown(config_wizard)
                config_wizard_files = write_configuration_wizard_outputs(
                    output_dir=config_output / "config_wizard",
                    wizard=config_wizard,
                )
                wizard_workspace_dir = config_output / "workspace_config_wizard"
                wizard_workspace = manager.build_task_workspace(
                    limit=10,
                    config_summary=summary,
                    config_preview=import_preview,
                    config_share_validation=import_share_validation,
                    config_import_draft=import_draft,
                    config_import_review=import_review,
                    config_template_index=template_index,
                    config_wizard=config_wizard,
                )
                wizard_workspace_files = manager.write_workspace_outputs(
                    output_dir=wizard_workspace_dir,
                    workspace=wizard_workspace,
                )
                wizard_workspace_html = (wizard_workspace_dir / "task_workspace.html").read_text(encoding="utf-8")
                default_wizard_workspace = manager.build_task_workspace(limit=10)
                step_ids = {str(item.get("id") or "") for item in config_wizard.get("steps") or [] if isinstance(item, dict)}
                wizard_readability = config_wizard.get("ui_readability") or {}
                wizard_filter_options = wizard_readability.get("step_filter_options") or {}
                wizard_command_targets = wizard_readability.get("command_copy_targets") or []
                checks.append(
                    {
                        "name": "configuration_wizard_combines_config_flow_into_readonly_guide",
                        "status": (
                            "pass"
                            if config_wizard.get("version") == "0.31-configuration-wizard"
                            and config_wizard.get("readonly") is True
                            and config_wizard.get("status") == "pass"
                            and config_wizard.get("will_apply_configuration") is False
                            and config_wizard.get("will_write_real_config_dir") is False
                            and config_wizard.get("remote_connection_tests_enabled") is False
                            and wizard_readability.get("version") == "0.31-configuration-wizard-readability"
                            and wizard_readability.get("blocked_step_count") == len(config_wizard.get("blocking_steps") or [])
                            and "pass" in (wizard_filter_options.get("statuses") or [])
                            and wizard_filter_options.get("blocking_modes") == ["all", "blocking", "non_blocking"]
                            and len(wizard_command_targets) == len(config_wizard.get("copy_commands") or [])
                            and all(item.get("copy_target_id") for item in wizard_command_targets if isinstance(item, dict))
                            and len(config_wizard.get("steps") or []) >= 7
                            and {
                                "config_summary",
                                "configuration_preview",
                                "share_validation",
                                "import_draft",
                                "import_review",
                                "template_index",
                                "manual_confirmation",
                            }.issubset(step_ids)
                            and "配置向导" in config_wizard_markdown
                            and "选择来源" in config_wizard_markdown
                            and "生成草案" in config_wizard_markdown
                            and "回读校验" in config_wizard_markdown
                            and "对比模板" in config_wizard_markdown
                            and "步骤筛选" in config_wizard_markdown
                            and "阻断摘要" in config_wizard_markdown
                            and "命令复制" in config_wizard_markdown
                            and "不会应用配置" in config_wizard_markdown
                            and Path(config_wizard_files.get("json") or "").exists()
                            and Path(config_wizard_files.get("markdown") or "").exists()
                            and "self-check-yunxiao-token" not in json.dumps(config_wizard, ensure_ascii=False)
                            and "self-check-openai-token" not in json.dumps(config_wizard, ensure_ascii=False)
                            and wizard_workspace.get("version") == "0.33-task-workspace"
                            and (wizard_workspace.get("config_wizard") or {}).get("version") == "0.31-configuration-wizard"
                            and {"config_wizard_json", "config_wizard_markdown"}.issubset(wizard_workspace_files)
                            and (wizard_workspace_dir / "task_workspace_config_wizard.json").exists()
                            and (wizard_workspace_dir / "task_workspace_config_wizard.md").exists()
                            and 'id="workspace-config-wizard"' in wizard_workspace_html
                            and 'id="wizard-step-search"' in wizard_workspace_html
                            and 'id="wizard-status-filter"' in wizard_workspace_html
                            and 'id="wizard-blocking-filter"' in wizard_workspace_html
                            and "function applyWizardFilters" in wizard_workspace_html
                            and "function copyWizardCommand" in wizard_workspace_html
                            and "data-wizard-status" in wizard_workspace_html
                            and "data-wizard-blocking" in wizard_workspace_html
                            and "data-copy-command" in wizard_workspace_html
                            and "配置向导" in wizard_workspace_html
                            and "选择来源" in wizard_workspace_html
                            and "生成草案" in wizard_workspace_html
                            and "回读校验" in wizard_workspace_html
                            and "对比模板" in wizard_workspace_html
                            and "阻断摘要" in wizard_workspace_html
                            and "命令复制" in wizard_workspace_html
                            and "不会应用配置" in wizard_workspace_html
                            and "self-check-yunxiao-token" not in wizard_workspace_html
                            and "self-check-openai-token" not in wizard_workspace_html
                            and default_wizard_workspace.get("version") == "0.21-task-workspace"
                            and not default_wizard_workspace.get("config_wizard")
                            else "failed"
                        ),
                        "message": (
                            f"wizard_version={config_wizard.get('version')}; "
                            f"status={config_wizard.get('status')}; "
                            f"steps={sorted(step_ids)}; "
                            f"readability={wizard_readability.get('version')}; "
                            f"workspace_version={wizard_workspace.get('version')}; "
                            f"files={sorted(wizard_workspace_files.keys())}"
                        ),
                    }
                )
                review_package = wizard_workspace.get("config_review_package_index") or {}
                review_package_files = review_package.get("files") or []
                review_package_kinds = {
                    str(item.get("kind") or "")
                    for item in review_package_files
                    if isinstance(item, dict)
                }
                checks.append(
                    {
                        "name": "configuration_review_package_index_collects_wizard_outputs_readonly",
                        "status": (
                            "pass"
                            if review_package.get("version") == "0.33-configuration-review-package-index"
                            and review_package.get("readonly") is True
                            and review_package.get("will_apply_configuration") is False
                            and review_package.get("will_write_real_config_dir") is False
                            and review_package.get("external_writes_enabled") is False
                            and review_package.get("command_count") == len(config_wizard.get("copy_commands") or [])
                            and review_package.get("manual_confirmation_count", 0) >= 1
                            and review_package.get("file_count", 0) >= 10
                            and {
                                "task_workspace_config_summary_json",
                                "task_workspace_config_preview_json",
                                "task_workspace_config_share_validation_json",
                                "task_workspace_config_import_review_json",
                                "task_workspace_config_template_index_json",
                                "task_workspace_config_wizard_json",
                                "task_workspace_config_review_package_json",
                                "task_workspace_config_review_package_md",
                            }.issubset(review_package_kinds)
                            and {"config_review_package_json", "config_review_package_markdown"}.issubset(wizard_workspace_files)
                            and (wizard_workspace_dir / "task_workspace_config_review_package.json").exists()
                            and (wizard_workspace_dir / "task_workspace_config_review_package.md").exists()
                            and (wizard_workspace.get("navigation") or {}).get("version") == "0.33-workspace-navigation"
                            and 'id="workspace-config-review-package"' in wizard_workspace_html
                            and "配置审查包" in wizard_workspace_html
                            and "复跑命令" in wizard_workspace_html
                            and "人工确认" in wizard_workspace_html
                            and "不会应用配置" in wizard_workspace_html
                            and not default_wizard_workspace.get("config_review_package_index")
                            else "failed"
                        ),
                        "message": (
                            f"package_version={review_package.get('version')}; "
                            f"file_count={review_package.get('file_count')}; "
                            f"command_count={review_package.get('command_count')}; "
                            f"files={sorted(review_package_kinds)}"
                        ),
                    }
                )
                package_readability = review_package.get("ui_readability") or {}
                package_filter_options = package_readability.get("file_filter_options") or {}
                package_handoff_summary = package_readability.get("handoff_summary") or {}
                checks.append(
                    {
                        "name": "configuration_review_package_readability_groups_handoff_summary",
                        "status": (
                            "pass"
                            if review_package.get("version") == "0.33-configuration-review-package-index"
                            and wizard_workspace.get("version") == "0.33-task-workspace"
                            and (wizard_workspace.get("navigation") or {}).get("version") == "0.33-workspace-navigation"
                            and package_readability.get("version") == "0.33-configuration-review-package-readability"
                            and package_filter_options.get("statuses") == ["all", "present", "missing"]
                            and package_readability.get("required_manual_confirmation_count", 0) >= 1
                            and package_readability.get("unconfirmed_required_count", 0) >= 1
                            and package_handoff_summary.get("status") in {"ready_for_manual_review", "missing_files"}
                            and package_handoff_summary.get("line_count", 0) >= 4
                            and 'id="review-package-file-search"' in wizard_workspace_html
                            and 'id="review-package-file-status-filter"' in wizard_workspace_html
                            and "function applyReviewPackageFilters" in wizard_workspace_html
                            and "data-review-package-file-status" in wizard_workspace_html
                            and "交接摘要" in wizard_workspace_html
                            and "待确认分组" in wizard_workspace_html
                            and not default_wizard_workspace.get("config_review_package_index")
                            else "failed"
                        ),
                        "message": (
                            f"package_version={review_package.get('version')}; "
                            f"readability={package_readability.get('version')}; "
                            f"handoff={package_handoff_summary.get('status')}; "
                            f"required={package_readability.get('required_manual_confirmation_count')}; "
                            f"unconfirmed={package_readability.get('unconfirmed_required_count')}"
                        ),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "configuration_wizard_combines_config_flow_into_readonly_guide",
                        "status": "failed",
                        "message": redact_secrets(str(exc)),
                    }
                )
        finally:
            database.DB_PATH = original_db_path
    except Exception as exc:
        checks.append(
            {
                "name": "rule_pack_profile_and_credentials_are_secret_free_and_compatible",
                "status": "failed",
                "message": redact_secrets(str(exc)),
            }
        )
    try:
        from app.config_compat import resolve_legacy_compatible_config
        from app.config_resolver import resolved_config_to_markdown, write_resolved_config_outputs

        resolved = resolve_legacy_compatible_config(
            profile_key="team-share-example",
            run_overrides={"orchestration": {"mode": "dynamic_plan"}},
        )
        resolved_payload = resolved.to_dict()
        resolved_markdown = resolved_config_to_markdown(resolved)
        resolved_files = write_resolved_config_outputs(
            output_dir=config_output / "resolved_config_v034",
            resolved=resolved,
        )
        serialized = json.dumps(resolved_payload, ensure_ascii=False)
        checks.append({
            "name": "resolved_config_v034_is_readonly_provenance_aware_and_legacy_compatible",
            "status": (
                "pass"
                if resolved_payload.get("schema_version") == "1.0-resolved-config"
                and resolved_payload.get("readonly") is True
                and (resolved_payload.get("validation") or {}).get("status") == "pass"
                and (resolved_payload.get("values") or {}).get("orchestration", {}).get("mode") == "dynamic_plan"
                and (resolved_payload.get("provenance") or {}).get("orchestration.mode", {}).get("layer_kind") == "run_override"
                and (resolved_payload.get("hard_guards") or {}).get("external_writes_default") == "off"
                and len(resolved_payload.get("content_hash") or "") == 64
                and "本报告只解析配置" in resolved_markdown
                and "self-check-yunxiao-token" not in serialized
                and "self-check-openai-token" not in serialized
                and Path(resolved_files.get("json") or "").exists()
                and Path(resolved_files.get("markdown") or "").exists()
                else "failed"
            ),
            "message": (
                f"version={resolved_payload.get('schema_version')}; "
                f"status={(resolved_payload.get('validation') or {}).get('status')}; "
                f"layers={len(resolved_payload.get('layers') or [])}; "
                f"hash={resolved_payload.get('content_hash')}"
            ),
        })
    except Exception as exc:
        checks.append({
            "name": "resolved_config_v034_is_readonly_provenance_aware_and_legacy_compatible",
            "status": "failed",
            "message": redact_secrets(str(exc)),
        })
    credentials_file.unlink(missing_ok=True)
    return checks


def run_dynamic_planning_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    simple_request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-SIMPLE",
        title="单页面默认值调整",
        demand_text="前端单页面默认值与既有档案规则保持一致。",
        evidence_refs=("fixture:user", "fixture:code"),
        signals=PlanningSignals(
            affected_layers=("frontend",),
            estimated_file_count=2,
            evidence_status="complete",
            verification_mode="targeted",
            allowed_paths={"frontend": ("fixture/src/Register.vue",)},
        ),
    )
    disabled_plan = build_dynamic_plan(simple_request)
    simple_plan = build_dynamic_plan(simple_request, enabled=True)
    expected_simple_roles = ["product_analyst", "frontend_developer", "test_executor"]
    checks.append(
        {
            "name": "dynamic_plan_default_off_and_simple_team",
            "status": "pass"
            if disabled_plan.status == "disabled"
            and simple_plan.status == "ready"
            and simple_plan.assessment.level == "simple"
            and [role.role_id for role in simple_plan.team.roles] == expected_simple_roles
            and not simple_plan.code_write_enabled
            and not simple_plan.database_access_enabled
            and not simple_plan.external_actions_enabled
            else "failed",
            "message": (
                f"default={disabled_plan.status}; enabled={simple_plan.status}; "
                f"level={simple_plan.assessment.level}; roles={[role.role_id for role in simple_plan.team.roles]}"
            ),
        }
    )

    high_risk_request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-HIGH",
        title="医保退费状态校验",
        demand_text="医保部分退费前校验状态，原收费结算逻辑保持不变。",
        signals=PlanningSignals(
            affected_layers=("frontend",),
            estimated_file_count=1,
            evidence_status="complete",
            allowed_paths={"frontend": ("fixture/src/Refund.vue",)},
        ),
    )
    high_risk_plan = build_dynamic_plan(high_risk_request, enabled=True)
    high_risk_roles = {role.role_id for role in high_risk_plan.team.roles}
    checks.append(
        {
            "name": "dynamic_plan_high_risk_human_gate",
            "status": "pass"
            if high_risk_plan.status == "needs_human_confirmation"
            and high_risk_plan.assessment.level == "high_risk"
            and {"high_risk_reviewer", "conflict_arbiter", "human_gate"}.issubset(high_risk_roles)
            else "failed",
            "message": (
                f"status={high_risk_plan.status}; level={high_risk_plan.assessment.level}; "
                f"forced={list(high_risk_plan.assessment.forced_upgrade_rules)}"
            ),
        }
    )

    overlap_request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-OVERLAP",
        title="跨层查询调整",
        demand_text="前端和后端共享目录需要串行处理。",
        signals=PlanningSignals(
            affected_layers=("frontend", "backend"),
            estimated_file_count=6,
            dependency_mode="parallel",
            evidence_status="complete",
            verification_mode="integration",
            allowed_paths={"frontend": ("fixture/shared",), "backend": ("fixture/shared",)},
        ),
    )
    overlap_plan = build_dynamic_plan(overlap_request, enabled=True)
    overlap_edges = [edge for edge in overlap_plan.graph.edges if edge.reason == "allowed_paths_overlap"]
    artifact_paths = write_dynamic_plan_outputs(output_dir / "dynamic_planning_check", simple_plan)
    checks.append(
        {
            "name": "dynamic_plan_path_lock_and_artifacts",
            "status": "pass"
            if overlap_plan.status == "ready"
            and len(overlap_edges) == 1
            and len(artifact_paths) == 3
            and all(path.exists() for path in artifact_paths)
            else "failed",
            "message": (
                f"status={overlap_plan.status}; overlap_edges={len(overlap_edges)}; "
                f"artifacts={len(artifact_paths)}"
            ),
        }
    )
    return checks


def run_dynamic_plan_registry_checks(*, output_dir: Path) -> list[dict]:
    request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-REGISTRY",
        title="前后端查询契约登记",
        demand_text="前端和后端同步一个低风险查询参数。",
        evidence_refs=("fixture:user", "fixture:code"),
        signals=PlanningSignals(
            affected_layers=("frontend", "backend"),
            estimated_file_count=6,
            dependency_mode="serial",
            evidence_status="partial",
            verification_mode="integration",
            allowed_paths={
                "frontend": ("fixture/web/Query.vue",),
                "backend": ("fixture/service/Query.java",),
            },
        ),
    )
    plan = build_dynamic_plan(request, enabled=True)
    registry = DynamicPlanRegistry()
    first = registry.register_plan(plan.to_dict())
    second = registry.register_plan(plan.to_dict())
    checks: list[dict] = [
        {
            "name": "dynamic_plan_registry_idempotent",
            "status": "pass"
            if second["idempotent"]
            and first["plan_id"] == second["plan_id"]
            and database.get_schema_meta("dynamic_plan_registry") == "1.0-dynamic-plan-registry"
            else "failed",
            "message": (
                f"first={first['idempotent']}; second={second['idempotent']}; "
                f"plan_id={first['plan_id']}"
            ),
        }
    ]
    artifact = registry.record_contract(
        plan_id=int(first["plan_id"]),
        node_id="requirement_analysis",
        schema_name="RequirementContract",
        schema_version="1.0",
        producer="product_analyst",
        content={"scope": "fixture-only", "acceptance": ["contract registry"]},
        input_artifact_ids=(),
    )
    snapshot = registry.get_plan(int(first["plan_id"]))
    files = write_dynamic_registry_outputs(output_dir / "dynamic_plan_registry_check", snapshot)
    recovery = snapshot["recovery_preview"]
    checks.append(
        {
            "name": "dynamic_plan_registry_contract_and_recovery_preview",
            "status": "pass"
            if artifact["status"] == "current"
            and "requirement_analysis" in recovery["completed_nodes"]
            and "architecture" in recovery["ready_nodes"]
            and recovery["readonly"]
            and not recovery["execution_enabled"]
            and len(files) == 3
            and all(path.exists() for path in files)
            else "failed",
            "message": (
                f"artifact={artifact['status']}; completed={recovery['completed_nodes']}; "
                f"ready={recovery['ready_nodes']}; artifacts={len(files)}"
            ),
        }
    )
    return checks


def run_dynamic_scheduler_checks(*, output_dir: Path) -> list[dict]:
    request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-SCHEDULER",
        title="前后端查询 dry-run 调度",
        demand_text="前端和后端同步一个低风险查询参数。",
        evidence_refs=("fixture:user", "fixture:code"),
        signals=PlanningSignals(
            affected_layers=("frontend", "backend"),
            estimated_file_count=6,
            dependency_mode="parallel",
            evidence_status="complete",
            verification_mode="integration",
            allowed_paths={
                "frontend": ("fixture/web/Query.vue",),
                "backend": ("fixture/service/Query.java",),
            },
        ),
    )
    registry = DynamicPlanRegistry()
    registration = registry.register_plan(build_dynamic_plan(request, enabled=True).to_dict())
    scheduler = DynamicDryRunScheduler()
    started = scheduler.start(int(registration["plan_id"]))
    schedule_id = int(started["schedule"]["id"])
    started_states = {item["node_id"]: item for item in started["node_states"]}
    failed = scheduler.advance(
        schedule_id,
        {
            "event_id": "self-check-failure-1",
            "node_id": "requirement_analysis",
            "outcome": "failure",
            "elapsed_seconds": 10,
            "input_tokens": 100,
            "output_tokens": 50,
        },
    )
    retried = scheduler.advance(schedule_id)
    succeeded = scheduler.advance(
        schedule_id,
        {
            "event_id": "self-check-success-2",
            "node_id": "requirement_analysis",
            "outcome": "success",
            "elapsed_seconds": 10,
            "input_tokens": 100,
            "output_tokens": 50,
        },
    )
    repeated = scheduler.advance(
        schedule_id,
        {
            "event_id": "self-check-success-2",
            "node_id": "requirement_analysis",
            "outcome": "success",
            "elapsed_seconds": 10,
            "input_tokens": 100,
            "output_tokens": 50,
        },
    )
    failed_states = {item["node_id"]: item for item in failed["node_states"]}
    retried_states = {item["node_id"]: item for item in retried["node_states"]}
    succeeded_states = {item["node_id"]: item for item in succeeded["node_states"]}
    files = write_dynamic_schedule_outputs(output_dir / "dynamic_scheduler_check", succeeded)
    return [
        {
            "name": "dynamic_scheduler_retry_and_progression",
            "status": "pass"
            if started_states["requirement_analysis"]["state"] == "running_simulated"
            and failed_states["requirement_analysis"]["state"] == "retry_wait"
            and retried_states["requirement_analysis"]["state"] == "running_simulated"
            and retried_states["requirement_analysis"]["attempt_count"] == 2
            and succeeded_states["requirement_analysis"]["state"] == "succeeded_simulated"
            and succeeded_states["architecture"]["state"] == "running_simulated"
            else "failed",
            "message": (
                f"start={started_states['requirement_analysis']['state']}; "
                f"failure={failed_states['requirement_analysis']['state']}; "
                f"retry={retried_states['requirement_analysis']['state']}; "
                f"success={succeeded_states['requirement_analysis']['state']}"
            ),
        },
        {
            "name": "dynamic_scheduler_checkpoint_idempotency_and_boundaries",
            "status": "pass"
            if succeeded["checkpoint"]["hash_valid"]
            and repeated["last_action"]["idempotent"]
            and succeeded["dry_run"]
            and not succeeded["execution_enabled"]
            and database.get_schema_meta("dynamic_dry_run_scheduler")
            == "1.0-dynamic-dry-run-scheduler"
            and len(files) == 3
            and all(path.exists() for path in files)
            else "failed",
            "message": (
                f"checkpoint={succeeded['checkpoint']['hash_valid']}; "
                f"idempotent={repeated['last_action']['idempotent']}; artifacts={len(files)}"
            ),
        },
    ]


def run_node_runtime_checks(*, output_dir: Path) -> list[dict]:
    request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-NODE-RUNTIME",
        title="受控 fixture 节点运行时",
        demand_text="使用脱敏 fixture 验证节点上下文和候选契约。",
        evidence_refs=("fixture:user", "fixture:code"),
        signals=PlanningSignals(
            affected_layers=("frontend",),
            estimated_file_count=2,
            evidence_status="complete",
            allowed_paths={"frontend": ("fixture/web/Query.vue",)},
        ),
    )
    registry = DynamicPlanRegistry()
    registration = registry.register_plan(build_dynamic_plan(request, enabled=True).to_dict())
    scheduler = DynamicDryRunScheduler()
    schedule = scheduler.start(int(registration["plan_id"]))
    schedule_id = int(schedule["schedule"]["id"])
    runtime = ControlledNodeRuntime()
    context = runtime.prepare_context(
        schedule_id,
        "requirement_analysis",
        requested_tools=("read_artifacts",),
    )
    fixture_root = output_dir / "node_runtime_check" / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    (fixture_root / ".harness-fixture-root.json").write_text(
        json.dumps({"schema_version": "1.0", "fixture_only": True}),
        encoding="utf-8",
    )
    fixture_file = fixture_root / f"requirement-{context['id']}.json"
    fixture_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0-fixture-node-input",
                "fixture_only": True,
                "context_hash": context["envelope_hash"],
                "requested_tools": ["read_artifacts"],
                "contract_content": {"scope": "self-check fixture"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    first = runtime.execute_fixture(
        context["id"],
        fixture_root=fixture_root,
        fixture_file=fixture_file,
    )
    repeated = runtime.execute_fixture(
        context["id"],
        fixture_root=fixture_root,
        fixture_file=fixture_file,
    )
    denied_context = runtime.prepare_context(
        schedule_id,
        "requirement_analysis",
        requested_tools=("git_push",),
    )
    denied_file = fixture_root / f"denied-{denied_context['id']}.json"
    denied_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0-fixture-node-input",
                "fixture_only": True,
                "context_hash": denied_context["envelope_hash"],
                "requested_tools": ["git_push"],
                "contract_content": {"scope": "must remain blocked"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    denied = runtime.execute_fixture(
        denied_context["id"],
        fixture_root=fixture_root,
        fixture_file=denied_file,
    )
    files = write_node_runtime_outputs(output_dir / "node_runtime_check", {"execution": first})
    latest_contract = database.get_latest_contract_artifact(
        int(registration["plan_id"]),
        "requirement_analysis",
    )
    refreshed = scheduler.get_schedule(schedule_id)
    states = {item["node_id"]: item for item in refreshed["node_states"]}
    return [
        {
            "name": "controlled_node_runtime_fixture_candidate_and_idempotency",
            "status": "pass"
            if context["hash_valid"]
            and context["permission_status"] == "allowed"
            and first["status"] == "succeeded_fixture"
            and first["candidate_hash_valid"]
            and repeated["idempotent"]
            and first["id"] == repeated["id"]
            and latest_contract["status"] == "planned"
            and states["requirement_analysis"]["state"] == "running_simulated"
            and len(files) == 3
            and all(path.exists() for path in files)
            else "failed",
            "message": (
                f"context_hash={context['hash_valid']}; fixture={first['status']}; "
                f"idempotent={repeated['idempotent']}; contract={latest_contract['status']}; "
                f"schedule_node={states['requirement_analysis']['state']}"
            ),
        },
        {
            "name": "controlled_node_runtime_permission_hard_guard",
            "status": "pass"
            if denied_context["permission_status"] == "denied"
            and denied["status"] == "blocked_policy"
            and not denied["business_valid"]
            and not denied["promotion_enabled"]
            and database.get_schema_meta("controlled_node_runtime")
            == "1.0-controlled-node-runtime"
            else "failed",
            "message": (
                f"permission={denied_context['permission_status']}; "
                f"status={denied['status']}; promotion={denied['promotion_enabled']}"
            ),
        },
    ]


def run_sandbox_executor_checks(*, output_dir: Path) -> list[dict]:
    request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-SANDBOX-EXECUTOR",
        title="固定 fixture worker",
        demand_text="验证一次性 lease、进程协议和失败隔离。",
        evidence_refs=("fixture:user", "fixture:code"),
        signals=PlanningSignals(
            affected_layers=("frontend",),
            estimated_file_count=2,
            evidence_status="complete",
            allowed_paths={"frontend": ("fixture/web/Query.vue",)},
        ),
    )
    registry = DynamicPlanRegistry()
    registration = registry.register_plan(build_dynamic_plan(request, enabled=True).to_dict())
    scheduler = DynamicDryRunScheduler()
    runtime = ControlledNodeRuntime()
    executor = SandboxExecutorRuntime()
    fixture_root = output_dir / "sandbox_executor_check" / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    (fixture_root / ".harness-fixture-root.json").write_text(
        json.dumps({"schema_version": "1.0", "fixture_only": True}),
        encoding="utf-8",
    )

    schedule = scheduler.start(int(registration["plan_id"]))
    context = runtime.prepare_context(
        int(schedule["schedule"]["id"]),
        "requirement_analysis",
        requested_tools=("read_artifacts",),
    )
    lease = executor.issue_lease(
        context["id"],
        capabilities=("read_artifacts",),
        ttl_seconds=60,
    )
    fixture_file = fixture_root / f"success-{lease['id']}.json"
    fixture_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0-fixture-node-input",
                "fixture_only": True,
                "context_hash": context["envelope_hash"],
                "requested_tools": ["read_artifacts"],
                "contract_content": {"scope": "sandbox self-check"},
                "worker_behavior": "success",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    succeeded = executor.execute(
        lease["id"],
        fixture_root=fixture_root,
        fixture_file=fixture_file,
        timeout_seconds=1,
    )
    repeated = executor.execute(
        lease["id"],
        fixture_root=fixture_root,
        fixture_file=fixture_file,
        timeout_seconds=1,
    )
    consumed = executor.get_lease(lease["id"])
    files = write_executor_runtime_outputs(
        output_dir / "sandbox_executor_check",
        {"execution": succeeded},
    )

    failure_schedule = scheduler.start(int(registration["plan_id"]))
    failure_context = runtime.prepare_context(
        int(failure_schedule["schedule"]["id"]),
        "requirement_analysis",
        requested_tools=("read_artifacts",),
    )
    failure_lease = executor.issue_lease(
        failure_context["id"],
        capabilities=("read_artifacts",),
        ttl_seconds=60,
    )
    failure_file = fixture_root / f"failure-{failure_lease['id']}.json"
    failure_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0-fixture-node-input",
                "fixture_only": True,
                "context_hash": failure_context["envelope_hash"],
                "requested_tools": ["read_artifacts"],
                "contract_content": {"scope": "must fail in worker"},
                "worker_behavior": "failure",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    failed = executor.execute(
        failure_lease["id"],
        fixture_root=fixture_root,
        fixture_file=failure_file,
        timeout_seconds=1,
    )
    latest_contract = database.get_latest_contract_artifact(
        int(registration["plan_id"]),
        "requirement_analysis",
    )
    return [
        {
            "name": "sandbox_executor_single_use_lease_and_candidate",
            "status": "pass"
            if lease["hash_valid"]
            and succeeded["status"] == "succeeded_sandbox_fixture"
            and succeeded["candidate_hash_valid"]
            and repeated["idempotent"]
            and consumed["status"] == "consumed"
            and consumed["use_count"] == 1
            and latest_contract["status"] == "planned"
            and len(files) == 3
            and all(path.exists() for path in files)
            else "failed",
            "message": (
                f"lease_hash={lease['hash_valid']}; status={succeeded['status']}; "
                f"idempotent={repeated['idempotent']}; uses={consumed['use_count']}; "
                f"contract={latest_contract['status']}"
            ),
        },
        {
            "name": "sandbox_executor_worker_failure_isolation",
            "status": "pass"
            if failed["status"] == "failed_adapter"
            and failed["error_code"] == "fixture_worker_failure"
            and not failed["business_valid"]
            and not failed["promotion_enabled"]
            and database.get_schema_meta("sandbox_executor_runtime")
            == "1.0-sandbox-executor-runtime"
            else "failed",
            "message": (
                f"status={failed['status']}; error={failed['error_code']}; "
                f"promotion={failed['promotion_enabled']}"
            ),
        },
    ]


def run_mock_agent_checks(*, output_dir: Path) -> list[dict]:
    request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-MOCK-AGENT",
        title="前后端并行 deterministic mock-agent",
        demand_text="验证多波次候选交接、trace 和并行观测。",
        evidence_refs=("fixture:user", "fixture:code"),
        signals=PlanningSignals(
            affected_layers=("frontend", "backend"),
            estimated_file_count=6,
            dependency_mode="parallel",
            evidence_status="complete",
            verification_mode="targeted",
            allowed_paths={
                "frontend": ("fixture/web/Query.vue",),
                "backend": ("fixture/service/Query.java",),
            },
        ),
    )
    registry = DynamicPlanRegistry()
    registration = registry.register_plan(build_dynamic_plan(request, enabled=True).to_dict())
    scheduler = DynamicDryRunScheduler()
    schedule = scheduler.start(int(registration["plan_id"]))
    fixture_root = output_dir / "mock_agent_check" / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    (fixture_root / ".harness-fixture-root.json").write_text(
        json.dumps({"schema_version": "1.0", "fixture_only": True}),
        encoding="utf-8",
    )
    runtime = DeterministicMockAgentRuntime()
    result = runtime.run(
        int(schedule["schedule"]["id"]),
        fixture_root=fixture_root,
        max_parallel=2,
    )
    repeated = runtime.run(
        int(schedule["schedule"]["id"]),
        fixture_root=fixture_root,
        max_parallel=2,
    )
    files = write_mock_agent_runtime_outputs(output_dir / "mock_agent_check", result)
    traces = {item["node_id"]: item for item in result["traces"]}
    executor = SandboxExecutorRuntime()
    requirement = executor.get_execution(traces["requirement_analysis"]["execution_id"])
    frontend = executor.get_execution(traces["frontend_implementation"]["execution_id"])
    backend = executor.get_execution(traces["backend_implementation"]["execution_id"])
    verify = executor.get_execution(traces["verify"]["execution_id"])
    requirement_id = requirement["sandbox_fixture_contract_candidate"]["artifact_id"]
    implementation_ids = {
        frontend["sandbox_fixture_contract_candidate"]["artifact_id"],
        backend["sandbox_fixture_contract_candidate"]["artifact_id"],
    }
    return [
        {
            "name": "mock_agent_full_dag_parallel_trace_and_idempotency",
            "status": "pass"
            if result["run"]["status"] == "completed_fixture"
            and result["schedule"]["schedule"]["status"] == "completed_simulated"
            and result["metrics"]["wave_count"] == 3
            and result["metrics"]["node_count"] == 4
            and result["metrics"]["max_observed_concurrency"] >= 2
            and repeated["idempotent"]
            and len(files) == 3
            and all(path.exists() for path in files)
            else "failed",
            "message": (
                f"status={result['run']['status']}; waves={result['metrics']['wave_count']}; "
                f"nodes={result['metrics']['node_count']}; "
                f"concurrency={result['metrics']['max_observed_concurrency']}; "
                f"idempotent={repeated['idempotent']}"
            ),
        },
        {
            "name": "mock_agent_candidate_handoff_without_business_promotion",
            "status": "pass"
            if frontend["sandbox_fixture_contract_candidate"]["input_artifact_ids"]
            == [requirement_id]
            and backend["sandbox_fixture_contract_candidate"]["input_artifact_ids"]
            == [requirement_id]
            and set(verify["sandbox_fixture_contract_candidate"]["input_artifact_ids"])
            == implementation_ids
            and all(
                database.get_latest_contract_artifact(
                    int(registration["plan_id"]), node_id
                )["status"]
                == "planned"
                for node_id in traces
            )
            and not result["business_valid"]
            and not result["promotion_enabled"]
            else "failed",
            "message": (
                f"handoffs={len(implementation_ids)}; business_valid={result['business_valid']}; "
                f"promotion={result['promotion_enabled']}"
            ),
        },
    ]


def run_model_invocation_checks(*, output_dir: Path) -> list[dict]:
    request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-MODEL-INVOCATION",
        title="单节点离线模型调用契约",
        demand_text="验证 mock 录制、结构化输出和 cassette replay。",
        evidence_refs=("fixture:user", "fixture:code"),
        signals=PlanningSignals(
            affected_layers=("frontend",),
            estimated_file_count=1,
            evidence_status="complete",
            verification_mode="targeted",
            allowed_paths={"frontend": ("fixture/web/Query.vue",)},
        ),
    )
    registry = DynamicPlanRegistry()
    registration = registry.register_plan(build_dynamic_plan(request, enabled=True).to_dict())
    plan_id = int(registration["plan_id"])
    scheduler = DynamicDryRunScheduler()
    schedule = scheduler.start(plan_id)
    schedule_id = int(schedule["schedule"]["id"])
    fixture_root = output_dir / "model_invocation_check" / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    (fixture_root / ".harness-fixture-root.json").write_text(
        json.dumps({"schema_version": "1.0", "fixture_only": True}),
        encoding="utf-8",
    )
    runtime = OfflineModelInvocationRuntime()
    mocked = runtime.invoke(
        schedule_id,
        "requirement_analysis",
        fixture_root=fixture_root,
        mode="mock",
        record_cassette=True,
    )
    repeated = runtime.invoke(
        schedule_id,
        "requirement_analysis",
        fixture_root=fixture_root,
        mode="mock",
        record_cassette=True,
    )
    replayed = runtime.invoke(
        schedule_id,
        "requirement_analysis",
        fixture_root=fixture_root,
        mode="replay",
        cassette_file=fixture_root / mocked["cassette"]["relative_path"],
    )
    files = write_model_invocation_outputs(
        output_dir / "model_invocation_check",
        mocked,
    )
    schedule_after = scheduler.get_schedule(schedule_id)
    contract = database.get_latest_contract_artifact(plan_id, "requirement_analysis")
    runtime_source = (PROJECT_ROOT / "app" / "model_invocation_runtime.py").read_text(encoding="utf-8")
    workflow_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PROJECT_ROOT / "app" / "harness.py",
            PROJECT_ROOT / "harnesses" / "his_requirement_workflow.py",
        )
    )
    return [
        {
            "name": "model_invocation_mock_structured_record_and_idempotency",
            "status": "pass"
            if mocked["invocation"]["status"] == "succeeded_fixture"
            and mocked["hashes_valid"]
            and mocked["cassette"]["recorded"]
            and repeated["idempotent"]
            and len(files) == 3
            and all(path.exists() for path in files)
            else "failed",
            "message": (
                f"status={mocked['invocation']['status']}; hashes={mocked['hashes_valid']}; "
                f"recorded={mocked['cassette']['recorded']}; idempotent={repeated['idempotent']}"
            ),
        },
        {
            "name": "model_invocation_replay_matches_recorded_response",
            "status": "pass"
            if replayed["invocation"]["status"] == "succeeded_fixture"
            and replayed["structured_output"] == mocked["structured_output"]
            and replayed["invocation"]["response_hash"] == mocked["invocation"]["response_hash"]
            and database.get_schema_meta("model_invocation_runtime")
            == "1.0-provider-neutral-offline-model-runtime"
            else "failed",
            "message": (
                f"status={replayed['invocation']['status']}; "
                f"response_match={replayed['invocation']['response_hash'] == mocked['invocation']['response_hash']}"
            ),
        },
        {
            "name": "model_invocation_offline_candidate_only_boundary",
            "status": "pass"
            if schedule_after["schedule"]["status"] == "active"
            and contract["status"] == "planned"
            and not mocked["business_valid"]
            and not mocked["promotion_enabled"]
            and "get_llm_client" not in runtime_source
            and "load_local_llm_credentials_env_if_available" not in runtime_source
            and "model_invocation_runtime" not in workflow_sources
            else "failed",
            "message": (
                f"schedule={schedule_after['schedule']['status']}; contract={contract['status']}; "
                f"business_valid={mocked['business_valid']}; promotion={mocked['promotion_enabled']}"
            ),
        },
    ]


def run_model_dag_checks(*, output_dir: Path) -> list[dict]:
    request = DynamicPlanningRequest(
        requirement_id="SELF-CHECK-MODEL-DAG",
        title="前后端并行离线模型 DAG",
        demand_text="验证多波次结构化模型候选交接、并行 trace 和失败边界。",
        evidence_refs=("fixture:user", "fixture:code"),
        signals=PlanningSignals(
            affected_layers=("frontend", "backend"),
            estimated_file_count=6,
            dependency_mode="parallel",
            evidence_status="complete",
            verification_mode="targeted",
            allowed_paths={
                "frontend": ("fixture/web/Query.vue",),
                "backend": ("fixture/service/Query.java",),
            },
        ),
    )
    registry = DynamicPlanRegistry()
    registration = registry.register_plan(build_dynamic_plan(request, enabled=True).to_dict())
    plan_id = int(registration["plan_id"])
    scheduler = DynamicDryRunScheduler()
    schedule = scheduler.start(plan_id)
    fixture_root = output_dir / "model_dag_check" / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    (fixture_root / ".harness-fixture-root.json").write_text(
        json.dumps({"schema_version": "1.0", "fixture_only": True}),
        encoding="utf-8",
    )
    runtime = OfflineModelDagRuntime()
    policy = {
        "schema_version": "1.0-offline-model-dag-adapters",
        "default": {"mode": "mock", "record_cassette": True},
        "nodes": {},
    }
    result = runtime.run(
        int(schedule["schedule"]["id"]),
        fixture_root=fixture_root,
        max_parallel=2,
        adapter_policy=policy,
    )
    repeated = runtime.run(
        int(schedule["schedule"]["id"]),
        fixture_root=fixture_root,
        max_parallel=2,
        adapter_policy=policy,
    )
    files = write_model_dag_outputs(output_dir / "model_dag_check", result)
    invocations = {
        trace["node_id"]: database.get_model_invocation(trace["invocation_id"])
        for trace in result["traces"]
    }
    requirement = invocations["requirement_analysis"]["candidate_payload"]
    frontend = invocations["frontend_implementation"]["candidate_payload"]
    backend = invocations["backend_implementation"]["candidate_payload"]
    verify = invocations["verify"]["candidate_payload"]
    runtime_source = (PROJECT_ROOT / "app" / "model_dag_runtime.py").read_text(encoding="utf-8")
    workflow_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PROJECT_ROOT / "app" / "harness.py",
            PROJECT_ROOT / "harnesses" / "his_requirement_workflow.py",
        )
    )
    implementation_ids = {frontend["artifact_id"], backend["artifact_id"]}
    return [
        {
            "name": "model_dag_full_waves_parallel_trace_and_idempotency",
            "status": "pass"
            if result["run"]["status"] == "completed_fixture"
            and result["schedule"]["schedule"]["status"] == "completed_simulated"
            and result["metrics"]["wave_count"] == 3
            and result["metrics"]["node_count"] == 4
            and result["metrics"]["max_observed_concurrency"] >= 2
            and repeated["idempotent"]
            and len(files) == 3
            and all(path.exists() for path in files)
            else "failed",
            "message": (
                f"status={result['run']['status']}; waves={result['metrics']['wave_count']}; "
                f"nodes={result['metrics']['node_count']}; "
                f"concurrency={result['metrics']['max_observed_concurrency']}; "
                f"idempotent={repeated['idempotent']}"
            ),
        },
        {
            "name": "model_dag_structured_candidate_handoff_without_promotion",
            "status": "pass"
            if frontend["input_artifact_ids"] == [requirement["artifact_id"]]
            and backend["input_artifact_ids"] == [requirement["artifact_id"]]
            and set(verify["input_artifact_ids"]) == implementation_ids
            and all(
                database.get_latest_contract_artifact(plan_id, node_id)["status"] == "planned"
                for node_id in invocations
            )
            and not result["business_valid"]
            and not result["promotion_enabled"]
            else "failed",
            "message": (
                f"handoffs={len(implementation_ids)}; business_valid={result['business_valid']}; "
                f"promotion={result['promotion_enabled']}"
            ),
        },
        {
            "name": "model_dag_offline_workflow_isolation",
            "status": "pass"
            if "get_llm_client" not in runtime_source
            and "load_local_llm_credentials_env_if_available" not in runtime_source
            and "model_dag_runtime" not in workflow_sources
            and all(trace["mode"] == "mock" for trace in result["traces"])
            and all(trace["cassette_relpath"] for trace in result["traces"])
            else "failed",
            "message": (
                f"modes={sorted(set(trace['mode'] for trace in result['traces']))}; "
                f"recorded={sum(bool(trace['cassette_relpath']) for trace in result['traces'])}"
            ),
        },
    ]


def run_model_provider_checks(*, output_dir: Path) -> list[dict]:
    class FakeProviderTransport:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def request(self, **kwargs) -> dict:
            self.calls.append(kwargs)
            return {
                "choices": [{"message": {"content": "SMOKE_OK"}}],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                },
            }

    provider_dir = output_dir / "model_provider_check"
    provider_dir.mkdir(parents=True, exist_ok=True)
    policy_path = provider_dir / "providers.json"
    credentials_path = provider_dir / "credentials.json"
    secret = "self-check-provider-secret"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0-controlled-model-provider-profiles",
                "profiles": {
                    "self-check": {
                        "provider_kind": "openai_compatible",
                        "enabled": True,
                        "smoke_enabled": True,
                        "credential_keys": {
                            "api_key": ["model_api_key"],
                            "base_url": ["model_base_url"],
                            "model": ["model_name"],
                        },
                        "allowed_endpoint_hosts": ["api.example.test"],
                        "timeout_seconds": 5,
                        "max_output_tokens": 16,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    credentials_path.write_text(
        json.dumps(
            {
                "model_api_key": secret,
                "model_base_url": "https://api.example.test/v1",
                "model_name": "self-check-model",
            }
        ),
        encoding="utf-8",
    )
    transport = FakeProviderTransport()
    runtime = ControlledModelProviderRuntime(transport=transport)
    result = runtime.run_smoke(
        profile_policy_path=policy_path,
        profile_key="self-check",
        credentials_path=credentials_path,
        allow_credentials=True,
        allow_network=True,
        authorization_id="self-check-fake-transport",
        allow_frozen_test_transport=True,
    )
    repeated = runtime.run_smoke(
        profile_policy_path=policy_path,
        profile_key="self-check",
        credentials_path=credentials_path,
        allow_credentials=True,
        allow_network=True,
        authorization_id="self-check-fake-transport",
        allow_frozen_test_transport=True,
    )
    files = write_model_provider_smoke_outputs(provider_dir / "outputs", result)
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)
    missing_credentials = provider_dir / "must-not-be-read.json"
    gate_blocked = False
    calls_before_gate = len(transport.calls)
    try:
        runtime.run_smoke(
            profile_policy_path=policy_path,
            profile_key="self-check",
            credentials_path=missing_credentials,
            allow_credentials=True,
            allow_network=False,
            authorization_id="self-check-blocked-gate",
        )
    except PermissionError:
        gate_blocked = True
    calls_after_gate = len(transport.calls)
    runtime_source = (PROJECT_ROOT / "app" / "model_provider_runtime.py").read_text(encoding="utf-8")
    workflow_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PROJECT_ROOT / "app" / "harness.py",
            PROJECT_ROOT / "harnesses" / "his_requirement_workflow.py",
            PROJECT_ROOT / "app" / "model_dag_runtime.py",
        )
    )
    return [
        {
            "name": "model_provider_double_gate_precedes_credentials_and_network",
            "status": "pass"
            if gate_blocked
            and not missing_credentials.exists()
            and calls_after_gate == calls_before_gate
            else "failed",
            "message": (
                f"gate_blocked={gate_blocked}; file_exists={missing_credentials.exists()}; "
                f"transport_calls_before={calls_before_gate}; "
                f"transport_calls_after={calls_after_gate}"
            ),
        },
        {
            "name": "model_provider_fixed_smoke_redacted_audited_and_idempotent",
            "status": "pass"
            if result["smoke"]["status"] == "passed"
            and result["smoke"]["transport_status"] == "passed"
            and result["smoke"]["protocol_status"] == "passed"
            and result["smoke"]["marker_status"] == "passed"
            and result["connectivity_verified"]
            and result["response_verified"]
            and repeated["idempotent"]
            and secret not in rendered
            and "Bearer" not in rendered
            and len(files) == 3
            and all(path.exists() for path in files)
            and database.get_schema_meta("model_provider_runtime")
            == MODEL_PROVIDER_RUNTIME_SCHEMA_VERSION
            else "failed",
            "message": (
                f"status={result['smoke']['status']}; verified={result['response_verified']}; "
                f"idempotent={repeated['idempotent']}; files={len(files)}"
            ),
        },
        {
            "name": "model_provider_single_node_isolated_from_dag_and_business_workflow",
            "status": "pass"
            if result["single_node_only"]
            and not result["dag_enabled"]
            and not result["tool_execution_enabled"]
            and not result["retry_enabled"]
            and not result["business_valid"]
            and "get_llm_client" not in runtime_source
            and "model_provider_runtime" not in workflow_sources
            else "failed",
            "message": (
                f"single_node={result['single_node_only']}; dag={result['dag_enabled']}; "
                f"tools={result['tool_execution_enabled']}; retry={result['retry_enabled']}"
            ),
        },
    ]


def run_pg_evidence_checks(*, output_dir: Path) -> list[dict]:
    from app.change_context_collectors import DataGraphCollector

    catalog = {
        "tables": (
            ["table_schema", "table_name", "table_type"],
            [["public", "mz_guahaob", "BASE TABLE"]],
        ),
        "columns": (
            [
                "table_schema", "table_name", "ordinal_position", "column_name",
                "data_type", "is_nullable", "column_default",
            ],
            [["public", "mz_guahaob", 1, "guahaobid", "bigint", "NO", None]],
        ),
        "constraints": (
            ["constraint_name", "constraint_type", "column_name", "ordinal_position"],
            [["mz_guahaob_pkey", "PRIMARY KEY", "guahaobid", 1]],
        ),
        "indexes": (
            ["schemaname", "tablename", "indexname", "indexdef"],
            [["public", "mz_guahaob", "mz_guahaob_pkey", "CREATE UNIQUE INDEX"]],
        ),
        "foreign_keys": (
            [
                "constraint_name", "table_schema", "table_name", "column_name",
                "foreign_table_schema", "foreign_table_name", "foreign_column_name",
            ],
            [],
        ),
    }

    class FakeMcpRuntime:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, request):
            self.calls.append(request)
            operation = str(request.input["operation"])
            columns, rows = catalog[operation]
            result = CapabilityResult(
                request_id=request.request_id,
                capability="database.inspect",
                provider="postgresql",
                status="success",
                mutation_level=MutationLevel.L1,
                changed=False,
                summary="self-check MCP fixture",
                data={
                    "connection_alias": request.input["connection_alias"],
                    "operation": operation,
                    "columns": columns,
                    "rows": rows,
                },
                evidence=({"ref": f"mcp-evidence:{request.request_id}:self-check"},),
                warnings=(),
                blockers=(),
                audit={
                    "execution_kind": "mcp",
                    "source_identity": (
                        f"postgresql:{request.input['connection_alias']}:{operation}"
                    ),
                    "source_version": "self-check-v1",
                    "freshness_status": "fresh",
                    "freshness_expires_at": "2099-01-01T00:00:00Z",
                    "collected_at": "2026-08-30T00:00:00Z",
                    "error_code": "",
                },
            )

            class Routed:
                pass

            routed = Routed()
            routed.result = result
            return routed

    invalid_runtime = FakeMcpRuntime()
    invalid = DataGraphCollector(runtime=invalid_runtime).collect(
        connection_alias="self_check_menzhen",
        schema="public",
        tables=("mz_guahaob",),
        task_id="self-check",
        run_id="self-check",
    )
    runtime = FakeMcpRuntime()
    collected = DataGraphCollector(runtime=runtime).collect(
        connection_alias="self_check_menzhen_readonly",
        schema="public",
        tables=("mz_guahaob",),
        task_id="self-check",
        run_id="self-check",
    )
    serialized = json.dumps(collected.payload, ensure_ascii=False).lower()
    del output_dir
    return [
        {
            "name": "pg_mcp_rejects_non_readonly_alias_without_connection",
            "status": "pass"
            if invalid.status == "incomplete" and not invalid_runtime.calls
            else "failed",
            "message": f"status={invalid.status}; mcp_calls={len(invalid_runtime.calls)}",
        },
        {
            "name": "pg_mcp_catalog_only_normalized_and_bounded",
            "status": "pass"
            if collected.status == "complete"
            and [item.input["operation"] for item in runtime.calls]
            == ["tables", "columns", "constraints", "indexes", "foreign_keys"]
            and all(item.mode == "preview" for item in runtime.calls)
            and all(item.mutation_level is MutationLevel.L1 for item in runtime.calls)
            and not any(
                token in serialized
                for token in ("password", "username", "dsn", "business_rows")
            )
            else "failed",
            "message": (
                f"status={collected.status}; "
                f"operations={[item.input['operation'] for item in runtime.calls]}"
            ),
        },
    ]


def run_requirement_provider_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    provider_output = output_dir / "requirement_provider"
    provider_output.mkdir(parents=True, exist_ok=True)
    original_token = os.environ.get("ALIYUN_DEVOPS_PAT")
    os.environ["ALIYUN_DEVOPS_PAT"] = "self-check-provider-secret-token"
    try:
        from app.requirement_provider import (
            normalize_requirement_evidence,
            normalize_requirement_evidence_file,
            requirement_evidence_to_markdown,
            write_requirement_evidence_outputs,
        )

        yunxiao_payload = {
            "status": "success",
            "mode": "readonly",
            "yunxiao_url": "https://devops.aliyun.com/projex/req/DFHIS-31465",
            "work_item_id": "DFHIS-31465",
            "work_item": {
                "title": "【运城口腔】挂号窗口新增'科室'过滤条件",
                "status": {"name": "开发中"},
                "assignee": {"displayName": "张三"},
                "description": "菜单路由参数 paiBanMs；内部 token self-check-provider-secret-token 不应出现在输出中。",
            },
            "clean_text": "菜单路由参数 paiBanMs：1 只过滤医生为空；2 只过滤有医生；空值保持默认。",
            "attachments": [{"name": "需求截图.png", "identifier": "file-1", "url": "https://example.invalid/file-1.png"}],
            "inline_files": [{"kind": "image", "identifier": "img-1", "name": "内联截图.png"}],
            "inline_file_downloads": [
                {
                    "identifier": "img-1",
                    "name": "内联截图.png",
                    "status": "success",
                    "path": "/tmp/inline.png",
                    "content_type": "image/png",
                    "size": 12,
                }
            ],
        }
        tapd_payload = {
            "source_url": "https://www.tapd.cn/123/prong/stories/view/112233",
            "story_id": "112233",
            "name": "TAPD 挂号参数适配",
            "description": "按 profile 配置读取 TAPD 需求，保持只读。",
            "status": "planning",
            "owner": "李四",
            "comments": [{"author": "测试", "content": "请补充验收路径"}],
            "attachments": [{"name": "tapd.png", "url": "https://example.invalid/tapd.png", "content_type": "image/png"}],
        }
        manual_payload = {
            "title": "手工需求",
            "description_text": "手工输入也要进入同一个 schema。",
            "external_id": "MANUAL-1",
        }
        file_payload = {
            "source_type": "file",
            "source_url": "/tmp/manual_requirement.json",
            "external_id": "FILE-1",
            "title": "文件需求",
            "description_text": "本地文件需求只读归一化。",
        }
        provider_file = provider_output / "file_requirement.json"
        provider_file.write_text(json.dumps(file_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        normalized_yunxiao = normalize_requirement_evidence(source_type="yunxiao", payload=yunxiao_payload)
        normalized_tapd = normalize_requirement_evidence(source_type="tapd", payload=tapd_payload)
        normalized_manual = normalize_requirement_evidence(source_type="manual", payload=manual_payload)
        normalized_file = normalize_requirement_evidence_file(provider_file)
        markdown = requirement_evidence_to_markdown(normalized_yunxiao)
        files = write_requirement_evidence_outputs(
            output_dir=provider_output / "outputs",
            evidence=normalized_yunxiao,
        )
        schema = [
            "source_type",
            "source_url",
            "external_id",
            "title",
            "description_text",
            "comments",
            "attachments",
            "images",
            "status",
            "assignee",
            "fetched_at",
            "warnings",
        ]
        checks.append(
            {
                "name": "requirement_provider_normalizes_yunxiao_tapd_manual_file_readonly",
                "status": (
                    "pass"
                    if normalized_yunxiao.get("version") == "0.23-requirement-evidence"
                    and normalized_yunxiao.get("readonly") is True
                    and normalized_yunxiao.get("external_writes_enabled") is False
                    and all(key in normalized_yunxiao for key in schema)
                    and normalized_yunxiao.get("source_type") == "yunxiao"
                    and normalized_yunxiao.get("external_id") == "DFHIS-31465"
                    and normalized_yunxiao.get("title") == "【运城口腔】挂号窗口新增'科室'过滤条件"
                    and normalized_yunxiao.get("status") == "开发中"
                    and "paiBanMs" in normalized_yunxiao.get("description_text", "")
                    and len(normalized_yunxiao.get("attachments") or []) == 1
                    and len(normalized_yunxiao.get("images") or []) >= 1
                    and normalized_tapd.get("source_type") == "tapd"
                    and normalized_tapd.get("external_id") == "112233"
                    and normalized_tapd.get("comments", [{}])[0].get("content") == "请补充验收路径"
                    and normalized_manual.get("source_type") == "manual"
                    and normalized_file.get("source_type") == "file"
                    and "self-check-provider-secret-token" not in json.dumps(normalized_yunxiao, ensure_ascii=False)
                    and "self-check-provider-secret-token" not in markdown
                    and (provider_output / "outputs" / "requirement_evidence.json").exists()
                    and (provider_output / "outputs" / "requirement_evidence.md").exists()
                    and "json" in files
                    and "markdown" in files
                    else "failed"
                ),
                "message": (
                    f"sources={[normalized_yunxiao.get('source_type'), normalized_tapd.get('source_type'), normalized_manual.get('source_type'), normalized_file.get('source_type')]}; "
                    f"schema_keys={sorted(key for key in schema if key in normalized_yunxiao)}"
                ),
            }
        )
        try:
            original_db_path = database.DB_PATH
            integration_db_path = provider_output / "requirement_evidence_integration.sqlite"
            if integration_db_path.exists():
                integration_db_path.unlink()
            database.DB_PATH = integration_db_path
            database.init_db()
            fixture_project = create_fixture_project(provider_output / "fixture_project")
            evidence_file = provider_output / "outputs" / "requirement_evidence.json"
            runner = RequirementWorkflowRunner(mode="mock", allow_mock=True, max_retries=1)
            workflow_result = runner.run(
                title="v0.24 需求证据接入样例",
                demand_text="菜单路由参数 paiBanMs 只读证据接入。",
                project_path=str(fixture_project),
                execution_mode="readonly",
                requirement_evidence_file=evidence_file,
            )
            workflow_output_root = provider_output / "workflow_outputs"
            run_dir = write_run_outputs(workflow_result.run_id, workflow_output_root)
            manager = _SelfCheckTaskManager()
            task, _record = manager.record_existing_run(
                TaskExistingRunOptions(
                    title="v0.24 需求证据接入样例",
                    entity_kind="requirement",
                    entity_id="DFHIS-31465",
                    output_dir=str(run_dir),
                    execution_mode="readonly",
                    project_paths=[str(fixture_project)],
                )
            )
            workbench = manager.build_task_workbench(task_id=int(task["id"]))
            workspace = manager.build_task_workspace(limit=5)
            workspace_output_dir = provider_output / "workspace_with_requirement_evidence"
            manager.write_workspace_outputs(output_dir=workspace_output_dir, workspace=workspace)
            workbench_evidence = workbench.get("requirement_evidence") or {}
            workspace_entry = next((item for item in workspace.get("entries") or [] if item.get("entity_id") == "DFHIS-31465"), {})
            workspace_evidence = workspace_entry.get("requirement_evidence") or {}
            workspace_html = (workspace_output_dir / "task_workspace.html").read_text(encoding="utf-8")
            checks.append(
                {
                    "name": "requirement_evidence_file_is_explicitly_integrated_into_workflow_and_workbench",
                    "status": (
                        "pass"
                        if workflow_result.status == "success"
                        and (run_dir / "requirement_evidence.json").exists()
                        and (run_dir / "requirement_evidence.md").exists()
                        and workbench_evidence.get("source_type") == "yunxiao"
                        and workbench_evidence.get("external_id") == "DFHIS-31465"
                        and workspace_evidence.get("source_type") == "yunxiao"
                        and workspace_evidence.get("markdown_link") == "workbenches/requirement-dfhis-31465/requirement_evidence.md"
                        and (workspace_output_dir / "workbenches" / "requirement-dfhis-31465" / "requirement_evidence.md").exists()
                        and "需求来源证据" in workspace_html
                        and "requirement_evidence.md" in workspace_html
                        else "failed"
                    ),
                    "message": (
                        f"run_dir={run_dir}; "
                        f"workbench_source={workbench_evidence.get('source_type')}; "
                        f"workspace_source={workspace_evidence.get('source_type')}"
                    ),
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": "requirement_evidence_file_is_explicitly_integrated_into_workflow_and_workbench",
                    "status": "failed",
                    "message": redact_secrets(str(exc)),
                }
            )
        finally:
            database.DB_PATH = original_db_path
    except Exception as exc:
        checks.append(
            {
                "name": "requirement_provider_normalizes_yunxiao_tapd_manual_file_readonly",
                "status": "failed",
                "message": redact_secrets(str(exc)),
            }
        )
    finally:
        if original_token is None:
            os.environ.pop("ALIYUN_DEVOPS_PAT", None)
        else:
            os.environ["ALIYUN_DEVOPS_PAT"] = original_token
    return checks


def create_task_manager_existing_output_fixture(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "precommit_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "summary": "提交前验证通过：目标 diff 检查和验证命令均通过；但同仓库存在白名单外改动，不能直接整体提交或写云效交付评论。",
                "manifest": {
                    "run_id": 31465104,
                    "project_root": "/Users/lym/Desktop/dongFang/dfcode",
                    "project_path": "/Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf",
                    "title": "【运城口腔】挂号窗口新增'科室'过滤条件",
                    "entity_id": "DFHIS-31465",
                    "demand_text": "菜单路由参数 paiBanMs：1 只过滤医生为空的排班；2 只过滤有医生的排班；空、不传或其他值保持当前默认模式。",
                    "generic_precommit": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "verification_matrix.json").write_text(
        json.dumps(
            {
                "overall_status": "pass",
                "summary": "提交前验证通过：目标 diff 检查和验证命令均通过；但同仓库存在白名单外改动，不能直接整体提交或写云效交付评论。",
                "can_commit": False,
                "can_enter_test": "人工代码审查通过后可进入测试",
                "can_yunxiao_comment": False,
                "can_yunxiao_transition": False,
                "warnings": ["df-web-guahaosf 存在白名单外改动，提交前需隔离本需求改动。"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "code_review.md").write_text("## 代码审查包\n\n- 样板：DFHIS-31465\n", encoding="utf-8")
    (root / "commit_ready_summary.md").write_text(
        "## Commit Ready Summary\n\n- 当前不能直接提交：同仓库存在白名单外改动。\n",
        encoding="utf-8",
    )
    requirement_calibration = {
        "version": "0.15-requirement-calibration",
        "readonly": True,
        "yunxiao_write_enabled": False,
        "status": "ready_for_development",
        "title": "【运城口腔】挂号窗口新增'科室'过滤条件",
        "entity_id": "DFHIS-31465",
        "source_priority": [
            {"priority": 1, "source": "user_instruction", "reason": "用户明确要求按补充规则执行，覆盖需求图或云效描述中的不一致表达。"},
            {"priority": 2, "source": "yunxiao_evidence", "reason": "云效只作为背景证据和标题来源，不覆盖用户补充规则。"},
        ],
        "resolved_scope": {
            "do": "使用菜单/路由参数 paiBanMs 控制排班过滤模式：1 过滤医生为空，2 过滤有医生，空值或其他值保持默认模式。",
            "do_not": ["不自动写云效", "不自动提交、推送或发布"],
        },
        "resolved_parameters": [
            {
                "name": "paiBanMs",
                "location": "route_menu_param",
                "source": "user_instruction",
                "allowed_values": {
                    "1": "只过滤医生为空的排班",
                    "2": "只过滤有医生的排班",
                    "empty": "为空、空值或其他情况时保持默认当前模式",
                },
            }
        ],
        "complexity": {"level": "simple", "reasons": ["参数名、位置和值域明确，且未命中高风险业务词。"]},
        "proposed_subtasks": [],
        "must_confirm": [],
        "warnings": [{"type": "source_conflict", "message": "用户补充规则明确覆盖需求图或云效描述，后续实现必须以用户补充为准。"}],
        "decision": {
            "can_enter_development": True,
            "can_auto_code": True,
            "needs_human_confirmation": False,
            "confidence": "high",
            "summary": "需求来源优先级、参数名、值域和默认行为已明确，可进入受控开发前审查。",
        },
        "boundaries": ["本卡只校准需求理解，不自动修改业务代码。", "不自动写云效、不自动流转状态、不改负责人、不调迭代、不关闭任务。"],
    }
    (root / "requirement_calibration.json").write_text(json.dumps(requirement_calibration, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "requirement_calibration.md").write_text(
        "\n".join(
            [
                "## v0.15 需求理解确认卡",
                "",
                "- 状态：ready_for_development",
                "- P1: 用户补充规则优先，原因：用户明确要求按补充规则执行，覆盖需求图或云效描述中的不一致表达。",
                "- 要做：使用菜单/路由参数 paiBanMs 控制排班过滤模式。",
                "- 不做：不自动写云效",
            ]
        ),
        encoding="utf-8",
    )
    (root / "ui_evidence_manifest.md").write_text("## UI 证据\n\n- 人工验收通过。\n", encoding="utf-8")
    (root / "ui_evidence_manifest.json").write_text(
        json.dumps(
            {
                "status": "present",
                "artifacts": [
                    {
                        "path": str(root / "manual_acceptance_DFHIS-31465.md"),
                        "kind": "manual",
                        "label": "人工验收记录",
                    }
                ],
                "assertions": [
                    {
                        "name": "manual_acceptance",
                        "status": "pass",
                        "evidence": "用户已在真实挂号收费页面验证通过。",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "manual_acceptance_DFHIS-31465.md").write_text("# 人工验收记录\n\n- 已通过。\n", encoding="utf-8")
    return root


def create_interaction_precommit_fixture(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    file_path = root / "src" / "components" / "shouFeiJs" / "components" / "Dialog.vue"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        "\n".join(
            [
                "<script>",
                "export default {",
                "  methods: {",
                "    async closePayAlert (message) {",
                "      await this.$alert(message, '提示')",
                "      this.closeSettlementProgress()",
                "      return",
                "    }",
                "  }",
                "}",
                "</script>",
                "",
            ]
        ),
        encoding="utf-8",
    )
    run_subprocess(["git", "init"], cwd=root)
    run_subprocess(["git", "add", "src/components/shouFeiJs/components/Dialog.vue"], cwd=root)
    run_subprocess(
        ["git", "-c", "user.name=Harness Self Check", "-c", "user.email=harness@example.local", "commit", "-m", "init interaction fixture"],
        cwd=root,
    )
    file_path.write_text(
        "\n".join(
            [
                "<script>",
                "export default {",
                "  methods: {",
                "    async closePayAlert (message) {",
                "      await this.$alert(message, '提示').catch(alertAction => {",
                "        if (['cancel', 'close'].includes(alertAction)) {",
                "          return",
                "        }",
                "        throw alertAction",
                "      })",
                "      this.closeSettlementProgress()",
                "      return",
                "    }",
                "  }",
                "}",
                "</script>",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def run_patch_readiness_checks() -> list[dict]:
    checks: list[dict] = []
    with temporary_env_removed([
        "aliyun_devops_pat",
        "ALIYUN_DEVOPS_PAT",
        "aliyun_devops_organization_id",
        "ALIYUN_DEVOPS_ORGANIZATION_ID",
        "HARNESS_CREDENTIALS_FILE",
        "HARNESS_YUNXIAO_DISABLE_KEYCHAIN",
    ]):
        os.environ["HARNESS_CREDENTIALS_FILE"] = "/tmp/his_harness_missing_credentials_for_self_check.json"
        os.environ["HARNESS_YUNXIAO_DISABLE_KEYCHAIN"] = "1"
        missing_evidence = collect_yunxiao_evidence(
            yunxiao_url="https://devops.aliyun.com/projex/bug/DFHIS-31195",
            demand_text="DFHIS-31195 优惠项目界面不限时",
        )
    checks.append(
        {
            "name": "yunxiao_missing_credentials_failed",
            "status": "pass" if missing_evidence.get("status") == "failed" and "缺少云效只读凭证" in missing_evidence.get("error", "") else "failed",
            "message": missing_evidence.get("error") or "-",
        }
    )

    unclear = evaluate_patch_readiness(
        demand_text="DFHIS-31195 优惠项目界面 老师出现添加的优惠项目不限时",
        yunxiao_evidence={
            "status": "success",
            "text_excerpt": "标题：优惠项目界面 老师出现添加的优惠项目不限时",
            "attachments": [],
            "work_item": {"title": "优惠项目界面 老师出现添加的优惠项目不限时"},
        },
        evidence_bundle={"evidence_files": [{"path": "src/pages/feiYongGl/youHuiLb.vue", "snippets": ["delete item.date"]}]},
        allowed_paths=["src/pages/feiYongGl/youHuiLb.vue"],
        verify_commands=["python3 -c \"pass\""],
        yunxiao_read_requested=True,
    )
    checks.append(
        {
            "name": "clarification_blocks_unclear_unlimited_time",
            "status": "pass" if unclear.status == "blocked_needs_clarification" and not unclear.can_patch else "failed",
            "message": unclear.summary,
        }
    )

    clear = evaluate_patch_readiness(
        demand_text=(
            "DFHIS-31195 优惠项目界面。复现步骤：新增优惠明细并填写有效时间，保存后再次打开，"
            "有效时间 youXiaoSJ/date 被清空，页面显示不限时。期望结果：保存后保留有效时间。"
        ),
        yunxiao_evidence={
            "status": "success",
            "text_excerpt": "复现步骤：新增优惠明细，填写有效时间，保存后变成不限时。期望保留有效时间 youXiaoSJ/date。",
            "attachments": [{"name": "bug.png"}],
            "work_item": {},
        },
        evidence_bundle={"evidence_files": [{"path": "src/pages/feiYongGl/youHuiLb.vue", "snippets": ["youXiaoSJ", "delete item.date", "youHuiXmMxList"]}]},
        allowed_paths=["src/pages/feiYongGl/youHuiLb.vue"],
        verify_commands=["python3 -c \"pass\""],
        yunxiao_read_requested=True,
    )
    checks.append(
        {
            "name": "clarification_allows_clear_date_issue",
            "status": "pass" if clear.status == "ready" and clear.can_patch else "failed",
            "message": clear.summary,
        }
    )
    return checks


def run_yunxiao_transaction_dry_run_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    checks.extend(run_yunxiao_credential_file_checks(output_dir=output_dir))
    off_result = RequirementWorkflowRunner(mode="mock", allow_mock=True, max_retries=0).run(
        title="云效事务 off 自测",
        demand_text="普通字段展示需求，不启用云效事务计划。",
        source_type="self_check",
        yunxiao_transaction_mode="off",
    )
    off_payload = json.loads(off_result.json_payload)
    off_has_plan = any(item.get("kind") == "yunxiao_transaction_plan_json" for item in off_payload.get("artifacts", []))
    checks.append(
        {
            "name": "transaction_off_no_plan",
            "status": "pass" if not off_has_plan else "failed",
            "message": "未生成事务计划" if not off_has_plan else "off 模式不应生成事务计划",
        }
    )

    missing_entity_result = RequirementWorkflowRunner(mode="mock", allow_mock=True, max_retries=0).run(
        title="云效事务缺少实体自测",
        demand_text="普通字段展示需求，启用 dry-run 但没有云效链接或实体 ID。",
        source_type="self_check",
        yunxiao_transaction_mode="dry-run",
    )
    missing_output = write_run_outputs(
        missing_entity_result.run_id,
        output_dir / "yunxiao_transaction_missing_entity",
    )
    missing_payload = json.loads((missing_output / "run.json").read_text(encoding="utf-8"))
    missing_plan = first_artifact_json(
        missing_payload,
        "yunxiao_transaction_plan_json",
        output_dir=missing_output,
    )
    checks.append(
        {
            "name": "transaction_missing_entity_failed",
            "status": "pass" if missing_plan.get("status") == "failed" and missing_entity_result.status == "success" else "failed",
            "message": missing_plan.get("summary") or "-",
        }
    )

    entity = YunxiaoEntityRef(kind="bug", entity_id="DFHIS-DRYRUN", title="优惠项目 dry-run 自测")
    default_plan = build_yunxiao_transaction_plan(
        manager=YunxiaoTransactionManager.dry_run(),
        project_key="self_check",
        entity=entity,
        run_id=8801,
        outcome="analysis_unclear",
        evidence_ids=["ev-dry-run"],
        risk_level="medium",
        persist_audit=False,
    )
    comment_action = first_action(default_plan, "comment")
    checks.append(
        {
            "name": "transaction_comment_disabled_rejected",
            "status": "pass" if not (comment_action.get("decision") or {}).get("allowed") else "failed",
            "message": (comment_action.get("decision") or {}).get("reason") or "-",
        }
    )

    enabled_actions = dict(DEFAULT_ENABLED_ACTIONS)
    enabled_actions.update(
        {
            "comment": True,
            "upload_attachment": True,
            "link_artifact": True,
            "transition": True,
            "assign": True,
            "update_iteration": True,
            "update_service_change": True,
        }
    )
    enabled_manager = YunxiaoTransactionManager.dry_run(policy=YunxiaoPolicy(project_key="self_check", enabled_actions=enabled_actions))
    allowed_plan = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=entity,
        run_id=8802,
        outcome="analysis_unclear",
        evidence_ids=["ev-dry-run"],
        risk_level="medium",
        persist_audit=False,
    )
    allowed_comment = first_action(allowed_plan, "comment")
    checks.append(
        {
            "name": "transaction_comment_enabled_dry_run_allowed",
            "status": "pass" if (allowed_comment.get("decision") or {}).get("status") == "dry_run_allowed" and allowed_comment.get("real_write_status") == "not_executed" else "failed",
            "message": (allowed_comment.get("decision") or {}).get("reason") or "-",
        }
    )

    fixture_dir = output_dir / "yunxiao_transaction_fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = fixture_dir / "dfhis-31195.png"
    screenshot_path.write_bytes(b"fake png bytes for dry-run metadata")
    service_change_path = fixture_dir / "service_change.json"
    service_change_path.write_text(
        json.dumps({"summary": "DFHIS-31195 前端页面修复验证", "service": "df-web-zhushujugl"}, ensure_ascii=False),
        encoding="utf-8",
    )
    full_plan = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=YunxiaoEntityRef(kind="bug", entity_id="DFHIS-FULL-TXN", title="普通字段 dry-run 全事务自测"),
        run_id=8810,
        outcome="developed_unverified",
        evidence_ids=["ev-dry-run"],
        risk_level="medium",
        current_status="开发中",
        target_status="待人工审核",
        target_iteration="迭代-2026-06",
        target_assignee="zhangsan",
        screenshot_paths=[str(screenshot_path)],
        service_change_file=str(service_change_path),
        artifacts=["diff=final.diff", "test_report=self_check_report.md"],
        persist_audit=False,
    )
    full_actions = full_plan.get("actions", [])
    full_action_names = [item.get("action") for item in full_actions]
    expected_actions = {"comment", "transition", "update_iteration", "assign", "upload_attachment", "update_service_change", "link_artifact"}
    full_allowed = all((item.get("decision") or {}).get("status") == "dry_run_allowed" for item in full_actions)
    checks.append(
        {
            "name": "transaction_full_dry_run_actions_planned",
            "status": "pass" if expected_actions.issubset(set(full_action_names)) and full_allowed else "failed",
            "message": f"actions={full_action_names}",
        }
    )

    missing_screenshot_plan = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=YunxiaoEntityRef(kind="bug", entity_id="DFHIS-MISSING-SCREENSHOT", title="普通字段 dry-run 缺失截图自测"),
        run_id=8811,
        outcome="developed_unverified",
        evidence_ids=["ev-dry-run"],
        risk_level="medium",
        screenshot_paths=[str(fixture_dir / "missing.png")],
        persist_audit=False,
    )
    missing_attachment = first_action(missing_screenshot_plan, "upload_attachment")
    checks.append(
        {
            "name": "transaction_missing_screenshot_rejected",
            "status": "pass" if missing_screenshot_plan.get("status") == "planned" and not (missing_attachment.get("decision") or {}).get("allowed") else "failed",
            "message": (missing_attachment.get("decision") or {}).get("reason") or "-",
        }
    )

    high_risk_plan = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=entity,
        run_id=8803,
        outcome="high_risk_needs_review",
        evidence_ids=["ev-dry-run"],
        risk_level="high",
        current_status="开发中",
        persist_audit=False,
    )
    action_names = [item.get("action") for item in high_risk_plan.get("actions", [])]
    transition_action = first_action(high_risk_plan, "transition")
    checks.append(
        {
            "name": "transaction_high_risk_review_no_close",
            "status": "pass" if "close" not in action_names and transition_action.get("after_state", {}).get("status") == "待人工审核" else "failed",
            "message": high_risk_plan.get("summary") or "-",
        }
    )

    high_risk_full_plan = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=entity,
        run_id=8812,
        outcome="high_risk_needs_review",
        evidence_ids=["ev-dry-run"],
        risk_level="high",
        current_status="开发中",
        target_status="待人工审核",
        target_iteration="迭代-高风险",
        target_assignee="lisi",
        screenshot_paths=[str(screenshot_path)],
        service_change_file=str(service_change_path),
        persist_audit=False,
    )
    blocked_high_risk_actions = {
        item.get("action")
        for item in high_risk_full_plan.get("actions", [])
        if not (item.get("decision") or {}).get("allowed")
    }
    checks.append(
        {
            "name": "transaction_high_risk_blocks_sensitive_actions",
            "status": "pass"
            if {"transition", "update_iteration", "assign", "upload_attachment", "update_service_change"}.issubset(blocked_high_risk_actions)
            else "failed",
            "message": f"blocked={sorted(blocked_high_risk_actions)}",
        }
    )

    all_passed_plan = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=YunxiaoEntityRef(kind="bug", entity_id="DFHIS-ALL-PASSED", title="普通字段 dry-run 自测"),
        run_id=8806,
        outcome="all_passed",
        evidence_ids=["ev-dry-run"],
        risk_level="low",
        current_status="待测试",
        persist_audit=False,
    )
    all_passed_actions = [item.get("action") for item in all_passed_plan.get("actions", [])]
    checks.append(
        {
            "name": "transaction_all_passed_no_transition_or_close",
            "status": "pass" if "transition" not in all_passed_actions and "close" not in all_passed_actions else "failed",
            "message": f"actions={all_passed_actions}",
        }
    )

    illegal_plan = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=entity,
        run_id=8804,
        outcome="verification_failed",
        evidence_ids=["ev-dry-run"],
        risk_level="medium",
        current_status="待处理",
        persist_audit=False,
    )
    illegal_transition = first_action(illegal_plan, "transition")
    checks.append(
        {
            "name": "transaction_illegal_transition_rejected",
            "status": "pass" if not (illegal_transition.get("decision") or {}).get("allowed") and "不允许" in (illegal_transition.get("decision") or {}).get("reason", "") else "failed",
            "message": (illegal_transition.get("decision") or {}).get("reason") or "-",
        }
    )

    idempotent_entity = YunxiaoEntityRef(kind="bug", entity_id="DFHIS-IDEMPOTENT", title="幂等自测")
    idempotent_run_id = database.create_run(
        team_key=TEAM_KEY,
        title="云效 dry-run 幂等审计 fixture",
        source_type="self_check",
        demand_text="只验证本地审计外键和幂等，不执行云效写入。",
        total_steps=0,
        llm_mode="mock",
    )
    idem_plan_1 = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=idempotent_entity,
        run_id=idempotent_run_id,
        outcome="analysis_unclear",
        evidence_ids=["ev-idempotent"],
        risk_level="medium",
        persist_audit=True,
    )
    idem_plan_2 = build_yunxiao_transaction_plan(
        manager=enabled_manager,
        project_key="self_check",
        entity=idempotent_entity,
        run_id=idempotent_run_id,
        outcome="analysis_unclear",
        evidence_ids=["ev-idempotent"],
        risk_level="medium",
        persist_audit=True,
    )
    first_ids = [item.get("audit_id") for item in idem_plan_1.get("actions", [])]
    second_ids = [item.get("audit_id") for item in idem_plan_2.get("actions", [])]
    checks.append(
        {
            "name": "transaction_idempotent_audit",
            "status": "pass" if first_ids and first_ids == second_ids else "failed",
            "message": f"audit_ids={first_ids}",
        }
    )
    checks.extend(run_yunxiao_transaction_write_checks(output_dir=output_dir, fixture_dir=fixture_dir, screenshot_path=screenshot_path, service_change_path=service_change_path))
    return checks


def run_yunxiao_credential_file_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    fixture_dir = output_dir / "credential_fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    missing_file = fixture_dir / "missing_credentials.json"
    file_path = fixture_dir / "credentials.json"
    credential_env_keys = [
        "ALIYUN_DEVOPS_PAT",
        "aliyun_devops_pat",
        "ALIYUN_DEVOPS_WRITE_PAT",
        "aliyun_devops_write_pat",
        "ALIYUN_DEVOPS_ORGANIZATION_ID",
        "aliyun_devops_organization_id",
        "HARNESS_CREDENTIALS_FILE",
        "HARNESS_YUNXIAO_DISABLE_KEYCHAIN",
    ]

    with temporary_env_removed(credential_env_keys):
        os.environ["HARNESS_CREDENTIALS_FILE"] = str(missing_file)
        os.environ["HARNESS_YUNXIAO_DISABLE_KEYCHAIN"] = "1"
        missing_read = load_yunxiao_credentials()
        missing_write = load_yunxiao_write_credentials()
    checks.append(
        {
            "name": "credential_file_missing_reports_missing",
            "status": "pass" if not missing_read.ok and not missing_write.ok else "failed",
            "message": f"read={missing_read.safe_summary()} write={missing_write.safe_summary()}",
        }
    )

    file_path.write_text(
        json.dumps(
            {
                "aliyun_devops_organization_id": "self-check-org",
                "aliyun_devops_pat": "self-check-read-token",
                "aliyun_devops_write_pat": "self-check-write-token",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    file_path.chmod(0o600)
    with temporary_env_removed(credential_env_keys):
        os.environ["HARNESS_CREDENTIALS_FILE"] = str(file_path)
        os.environ["HARNESS_YUNXIAO_DISABLE_KEYCHAIN"] = "1"
        file_read = load_yunxiao_credentials()
        file_write = load_yunxiao_write_credentials()
    checks.append(
        {
            "name": "credential_file_loaded",
            "status": "pass"
            if file_read.ok
            and file_write.ok
            and file_read.pat_source == f"file:{file_path}"
            and file_write.token_source == f"file:{file_path}"
            else "failed",
            "message": f"read={file_read.safe_summary()} write={file_write.safe_summary()}",
        }
    )

    with temporary_env_removed(credential_env_keys):
        os.environ["HARNESS_CREDENTIALS_FILE"] = str(file_path)
        os.environ["HARNESS_YUNXIAO_DISABLE_KEYCHAIN"] = "1"
        os.environ["aliyun_devops_pat"] = "env-read-token"
        os.environ["aliyun_devops_write_pat"] = "env-write-token"
        os.environ["aliyun_devops_organization_id"] = "env-org"
        env_read = load_yunxiao_credentials()
        env_write = load_yunxiao_write_credentials()
    checks.append(
        {
            "name": "credential_env_precedence_over_file",
            "status": "pass"
            if env_read.ok
            and env_write.ok
            and env_read.pat_source == "env:aliyun_devops_pat"
            and env_write.token_source == "env:aliyun_devops_write_pat"
            and env_read.organization_source == "env:aliyun_devops_organization_id"
            else "failed",
            "message": f"read={env_read.safe_summary()} write={env_write.safe_summary()}",
        }
    )

    file_path.chmod(0o644)
    with temporary_env_removed(credential_env_keys):
        os.environ["HARNESS_CREDENTIALS_FILE"] = str(file_path)
        permission_issue = credentials_file_permission_issue()
    checks.append(
        {
            "name": "credential_file_permission_warning",
            "status": "pass" if "权限过宽" in permission_issue else "failed",
            "message": permission_issue or "-",
        }
    )
    file_path.chmod(0o600)
    return checks


def run_yunxiao_transaction_write_checks(*, output_dir: Path, fixture_dir: Path, screenshot_path: Path, service_change_path: Path) -> list[dict]:
    checks: list[dict] = []
    enabled_actions = dict(DEFAULT_ENABLED_ACTIONS)
    enabled_actions.update(
        {
            "comment": True,
            "upload_attachment": True,
            "link_artifact": True,
            "transition": True,
            "assign": True,
            "update_iteration": True,
            "update_service_change": True,
        }
    )
    mappings = {
        "assign": {"propertyKey": "assignedTo", "fieldType": "user"},
        "transition": {"propertyKey": "status", "fieldType": "status"},
        "close": {"propertyKey": "status", "fieldType": "status"},
        "update_iteration": {"propertyKey": "sprintIdentifier", "fieldType": "sprint"},
        "update_service_change": {"fieldIdentifier": "custom_service_change"},
        "link_artifact": {"fieldIdentifier": "custom_harness_artifacts"},
    }
    write_policy = YunxiaoPolicy(project_key="self_check", enabled_actions=enabled_actions, field_mappings=mappings)
    entity = YunxiaoEntityRef(kind="bug", entity_id="DFHIS-WRITE", title="普通字段 fake write 自测")
    confirm = "WRITE:bug:DFHIS-WRITE"

    missing_confirm_manager = YunxiaoTransactionManager.controlled_write(
        policy=write_policy,
        write_confirm="",
        write_transport="fake",
    )
    missing_confirm_plan = build_yunxiao_transaction_plan(
        manager=missing_confirm_manager,
        project_key="self_check",
        entity=entity,
        run_id=8901,
        outcome="analysis_unclear",
        evidence_ids=["ev-write"],
        risk_level="medium",
        persist_audit=False,
    )
    missing_confirm_comment = first_action(missing_confirm_plan, "comment")
    checks.append(
        {
            "name": "transaction_write_missing_confirm_blocked",
            "status": "pass" if missing_confirm_comment.get("status") == "write_blocked" else "failed",
            "message": missing_confirm_comment.get("error") or "-",
        }
    )

    real_manager = YunxiaoTransactionManager.controlled_write(
        policy=write_policy,
        write_confirm=confirm,
        write_transport="real",
    )
    previous_env = {
        key: os.environ.get(key)
        for key in [
            "ALIYUN_DEVOPS_WRITE_PAT",
            "aliyun_devops_write_pat",
            "ALIYUN_DEVOPS_PAT",
            "aliyun_devops_pat",
            "ALIYUN_DEVOPS_ORGANIZATION_ID",
            "aliyun_devops_organization_id",
            "HARNESS_CREDENTIALS_FILE",
            "HARNESS_YUNXIAO_DISABLE_KEYCHAIN",
        ]
    }
    try:
        os.environ.pop("ALIYUN_DEVOPS_WRITE_PAT", None)
        os.environ.pop("aliyun_devops_write_pat", None)
        os.environ.pop("ALIYUN_DEVOPS_PAT", None)
        os.environ.pop("aliyun_devops_pat", None)
        os.environ.pop("ALIYUN_DEVOPS_ORGANIZATION_ID", None)
        os.environ.pop("aliyun_devops_organization_id", None)
        os.environ["HARNESS_CREDENTIALS_FILE"] = str(fixture_dir / "missing_write_credentials.json")
        os.environ["HARNESS_YUNXIAO_DISABLE_KEYCHAIN"] = "1"
        no_token_plan = build_yunxiao_transaction_plan(
            manager=real_manager,
            project_key="self_check",
            entity=entity,
            run_id=8902,
            outcome="analysis_unclear",
            evidence_ids=["ev-write"],
            risk_level="medium",
            persist_audit=False,
        )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    no_token_comment = first_action(no_token_plan, "comment")
    checks.append(
        {
            "name": "transaction_write_missing_token_blocked",
            "status": "pass" if no_token_comment.get("status") == "write_blocked" and "缺少云效写凭证" in (no_token_comment.get("error") or "") else "failed",
            "message": no_token_comment.get("error") or "-",
        }
    )

    fallback_previous_env = {
        key: os.environ.get(key)
        for key in [
            "ALIYUN_DEVOPS_WRITE_PAT",
            "aliyun_devops_write_pat",
            "ALIYUN_DEVOPS_PAT",
            "aliyun_devops_pat",
            "ALIYUN_DEVOPS_ORGANIZATION_ID",
            "aliyun_devops_organization_id",
            "HARNESS_CREDENTIALS_FILE",
            "HARNESS_YUNXIAO_DISABLE_KEYCHAIN",
        ]
    }
    try:
        os.environ.pop("ALIYUN_DEVOPS_WRITE_PAT", None)
        os.environ.pop("aliyun_devops_write_pat", None)
        os.environ["aliyun_devops_pat"] = "self-check-read-write-token"
        os.environ["aliyun_devops_organization_id"] = "self-check-org"
        os.environ["HARNESS_CREDENTIALS_FILE"] = str(fixture_dir / "fallback_only_credentials.json")
        os.environ["HARNESS_YUNXIAO_DISABLE_KEYCHAIN"] = "1"
        fallback_credentials = load_yunxiao_write_credentials()
        fallback_plan = build_yunxiao_transaction_plan(
            manager=real_manager,
            project_key="self_check",
            entity=entity,
            run_id=89021,
            outcome="analysis_unclear",
            evidence_ids=["ev-write"],
            risk_level="medium",
            persist_audit=False,
        )
    finally:
        for key, value in fallback_previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    checks.append(
        {
            "name": "transaction_write_token_falls_back_to_read_pat",
            "status": "pass"
            if fallback_credentials.ok
            and fallback_credentials.token_source == "env:aliyun_devops_pat"
            and fallback_credentials.token_kind == "fallback_read_pat"
            else "failed",
            "message": str(fallback_credentials.safe_summary()),
        }
    )
    fallback_comment = first_action(fallback_plan, "comment")
    checks.append(
        {
            "name": "transaction_real_write_fallback_pat_blocked",
            "status": "pass"
            if fallback_comment.get("status") == "write_blocked"
            and "专用 aliyun_devops_write_pat" in (fallback_comment.get("error") or "")
            else "failed",
            "message": fallback_comment.get("error") or "-",
        }
    )

    fake_manager = YunxiaoTransactionManager.controlled_write(
        policy=write_policy,
        write_confirm=confirm,
        write_transport="fake",
    )
    fake_plan = build_yunxiao_transaction_plan(
        manager=fake_manager,
        project_key="self_check",
        entity=entity,
        run_id=8903,
        outcome="developed_unverified",
        evidence_ids=["ev-write"],
        risk_level="medium",
        current_status="开发中",
        target_status="待人工审核",
        target_iteration="迭代-2026-06",
        target_assignee="zhangsan",
        screenshot_paths=[str(screenshot_path)],
        service_change_file=str(service_change_path),
        artifacts=["diff=final.diff", "test_report=self_check_report.md"],
        human_confirmed=True,
        persist_audit=False,
    )
    fake_statuses = [item.get("status") for item in fake_plan.get("actions", [])]
    fake_comment = first_action(fake_plan, "comment")
    fake_non_comments = [item for item in fake_plan.get("actions", []) if item.get("action") != "comment"]
    checks.append(
        {
            "name": "transaction_fake_write_comment_only_scope",
            "status": "pass"
            if fake_comment.get("status") == "write_executed"
            and fake_non_comments
            and all(item.get("status") == "write_blocked" for item in fake_non_comments)
            else "failed",
            "message": f"statuses={fake_statuses}",
        }
    )
    checks.append(
        {
            "name": "transaction_comment_only_effective_success_with_expected_blocks",
            "status": "pass" if fake_plan.get("effective_write_status") == "success_with_expected_blocks" else "failed",
            "message": f"effective={fake_plan.get('effective_write_status')} real={fake_plan.get('real_write_status')}",
        }
    )

    delivery_entity = YunxiaoEntityRef(kind="requirement", entity_id="DFHIS-31270", title="住院收费-出现结算收款页面需添加预交金备注列")
    delivery_manager = YunxiaoTransactionManager.dry_run(policy=write_policy)
    delivery_plan = build_yunxiao_transaction_plan(
        manager=delivery_manager,
        project_key="self_check",
        entity=delivery_entity,
        run_id=8912,
        outcome="all_passed",
        evidence_ids=["ev-delivery-comment"],
        risk_level="medium",
        screenshot_paths=[],
        artifacts=[
            "commit=043f7c3b",
            "branch=feature-DFHIS-31270",
            "changed_file=src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue",
            "verification=git diff --check 通过；单文件 lint 为历史基线告警，非本次引入。",
            "test_suggestion=进入住院收费结算收款页，检查预交金信息表格备注列。",
        ],
        persist_audit=False,
    )
    delivery_preview = str((first_action(delivery_plan, "comment").get("payload") or {}).get("comment_preview") or "")
    has_visible_idempotency_line = any(line.strip().startswith("HIS-HARNESS-IDEMPOTENCY:") for line in delivery_preview.splitlines())
    checks.append(
        {
            "name": "transaction_delivery_comment_template_core_fields",
            "status": "pass"
            if all(
                text in delivery_preview
                for text in [
                    "需求：DFHIS-31270",
                    "提交：043f7c3b",
                    "分支：feature-DFHIS-31270 + RC_2.16.1_250514",
                    "src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue",
                    "未提供截图/视频/GIF",
                ]
            )
            and "<!-- HIS-HARNESS-IDEMPOTENCY:" in delivery_preview
            and "该评论由 HIS Harness 自动生成" not in delivery_preview
            and not has_visible_idempotency_line
            else "failed",
            "message": delivery_preview[:500],
        }
    )

    delivery_media_plan = build_yunxiao_transaction_plan(
        manager=delivery_manager,
        project_key="self_check",
        entity=delivery_entity,
        run_id=8913,
        outcome="all_passed",
        evidence_ids=["ev-delivery-comment-media"],
        risk_level="medium",
        screenshot_paths=[str(screenshot_path)],
        artifacts=[
            "commit=043f7c3b",
            "changed_file=src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue",
            "gif=/tmp/not-existing-demo.gif",
        ],
        persist_audit=False,
    )
    media_preview = str((first_action(delivery_media_plan, "comment").get("payload") or {}).get("comment_preview") or "")
    checks.append(
        {
            "name": "transaction_delivery_comment_template_media_summary",
            "status": "pass"
            if "视觉证据" in media_preview
            and screenshot_path.name in media_preview
            and "sha256=" in media_preview
            and "not-existing-demo.gif" in media_preview
            and str(screenshot_path) not in media_preview
            else "failed",
            "message": media_preview[:500],
        }
    )

    missing_mapping_policy = YunxiaoPolicy(project_key="self_check", enabled_actions=enabled_actions, field_mappings={})
    missing_mapping_manager = YunxiaoTransactionManager.controlled_write(
        policy=missing_mapping_policy,
        write_confirm=confirm,
        write_transport="fake",
    )
    missing_mapping_plan = build_yunxiao_transaction_plan(
        manager=missing_mapping_manager,
        project_key="self_check",
        entity=entity,
        run_id=8904,
        outcome="developed_unverified",
        evidence_ids=["ev-write"],
        risk_level="medium",
        current_status="开发中",
        target_status="待人工审核",
        artifacts=["diff=final.diff"],
        human_confirmed=True,
        persist_audit=False,
    )
    mapping_transition = first_action(missing_mapping_plan, "transition")
    mapping_artifact = first_action(missing_mapping_plan, "link_artifact")
    checks.append(
        {
            "name": "transaction_comment_only_blocks_non_comment_before_mapping",
            "status": "pass" if mapping_transition.get("status") == "write_blocked" and mapping_artifact.get("status") == "write_blocked" else "failed",
            "message": f"transition={mapping_transition.get('status')} artifact={mapping_artifact.get('status')}",
        }
    )

    high_risk_plan = build_yunxiao_transaction_plan(
        manager=fake_manager,
        project_key="self_check",
        entity=entity,
        run_id=8905,
        outcome="high_risk_needs_review",
        evidence_ids=["ev-write"],
        risk_level="high",
        current_status="开发中",
        target_status="待人工审核",
        target_iteration="迭代-高风险",
        target_assignee="lisi",
        screenshot_paths=[str(screenshot_path)],
        service_change_file=str(service_change_path),
        persist_audit=False,
    )
    blocked = {
        item.get("action")
        for item in high_risk_plan.get("actions", [])
        if item.get("status") in {"rejected", "write_blocked"}
    }
    checks.append(
        {
            "name": "transaction_write_high_risk_or_comment_only_blocks_sensitive_actions",
            "status": "pass" if {"transition", "update_iteration", "assign", "upload_attachment", "update_service_change"}.issubset(blocked) else "failed",
            "message": f"blocked={sorted(blocked)}",
        }
    )

    failed_record = fake_manager.plan(
        YunxiaoTransactionRequest(
            project_key="self_check",
            entity=entity,
            action="comment",
            run_id=8906,
            payload={"fake_error": "simulated 403"},
            evidence_ids=["ev-write"],
            risk_level="medium",
            reason="模拟云效写接口失败。",
            model_mode="mock",
            model_name="mock-harness-local",
        ),
        persist_audit=False,
    )
    checks.append(
        {
            "name": "transaction_fake_write_failure_reported",
            "status": "pass" if failed_record.get("status") == "write_failed" and "simulated 403" in (failed_record.get("error") or "") else "failed",
            "message": failed_record.get("error") or "-",
        }
    )

    idem_entity = YunxiaoEntityRef(kind="bug", entity_id="DFHIS-WRITE-IDEMPOTENT", title="fake write 幂等自测")
    idem_confirm = "WRITE:bug:DFHIS-WRITE-IDEMPOTENT"
    idem_manager = YunxiaoTransactionManager.controlled_write(policy=write_policy, write_confirm=idem_confirm, write_transport="fake")
    idempotent_run_id = database.create_run(
        team_key=TEAM_KEY,
        title="云效 fake-write 幂等审计 fixture",
        source_type="self_check",
        demand_text="只验证本地 fake transport 审计外键和幂等，不访问云效。",
        total_steps=0,
        llm_mode="mock",
    )
    idem_plan_1 = build_yunxiao_transaction_plan(
        manager=idem_manager,
        project_key="self_check",
        entity=idem_entity,
        run_id=idempotent_run_id,
        outcome="analysis_unclear",
        evidence_ids=["ev-write-idem"],
        risk_level="medium",
        persist_audit=True,
    )
    idem_plan_2 = build_yunxiao_transaction_plan(
        manager=idem_manager,
        project_key="self_check",
        entity=idem_entity,
        run_id=idempotent_run_id,
        outcome="analysis_unclear",
        evidence_ids=["ev-write-idem"],
        risk_level="medium",
        persist_audit=True,
    )
    idem_ids_1 = [item.get("audit_id") for item in idem_plan_1.get("actions", [])]
    idem_ids_2 = [item.get("audit_id") for item in idem_plan_2.get("actions", [])]
    idem_comment_2 = first_action(idem_plan_2, "comment")
    checks.append(
        {
            "name": "transaction_fake_write_idempotent_audit",
            "status": "pass" if idem_ids_1 and idem_ids_1 == idem_ids_2 else "failed",
            "message": f"audit_ids={idem_ids_1}",
        }
    )
    checks.append(
        {
            "name": "transaction_fake_comment_duplicate_skipped",
            "status": "pass" if idem_comment_2.get("status") == "write_skipped_idempotent" else "failed",
            "message": f"second_comment_status={idem_comment_2.get('status')}",
        }
    )

    transition_policy = YunxiaoPolicy(
        project_key="self_check_transition_fake",
        enabled_actions=enabled_actions,
        allowed_transitions={"待开发": ["开发中", "待澄清", "待测试", "待人工审核"]},
        field_mappings={"transition": {"propertyKey": "status", "fieldType": "status"}},
    )
    transition_manager = YunxiaoTransactionManager.controlled_write(
        policy=transition_policy,
        write_confirm=confirm,
        write_transport="fake",
        write_scope="transition-fake",
    )
    transition_plan = build_yunxiao_transaction_plan(
        manager=transition_manager,
        project_key="self_check",
        entity=entity,
        run_id=8908,
        outcome="developed_unverified",
        evidence_ids=["ev-transition-fake"],
        risk_level="medium",
        current_status="待开发",
        target_status="待测试",
        target_assignee="blocked-assignee",
        artifacts=["diff=final.diff"],
        human_confirmed=True,
        persist_audit=False,
    )
    transition_comment = first_action(transition_plan, "comment")
    transition_action = first_action(transition_plan, "transition")
    transition_assign = first_action(transition_plan, "assign")
    transition_artifact = first_action(transition_plan, "link_artifact")
    checks.append(
        {
            "name": "transaction_transition_fake_executes_comment_and_transition",
            "status": "pass"
            if transition_comment.get("status") == "write_executed"
            and transition_action.get("status") == "write_executed"
            and transition_assign.get("status") == "write_blocked"
            and transition_artifact.get("status") == "write_blocked"
            else "failed",
            "message": f"comment={transition_comment.get('status')} transition={transition_action.get('status')} assign={transition_assign.get('status')} artifact={transition_artifact.get('status')}",
        }
    )

    clarification_transition_plan = build_yunxiao_transaction_plan(
        manager=transition_manager,
        project_key="self_check",
        entity=entity,
        run_id=8914,
        outcome="analysis_unclear",
        evidence_ids=["ev-transition-fake"],
        risk_level="medium",
        current_status="待开发",
        human_confirmed=True,
        persist_audit=False,
    )
    clarification_transition = first_action(clarification_transition_plan, "transition")
    checks.append(
        {
            "name": "transaction_transition_fake_suggests_clarification",
            "status": "pass"
            if clarification_transition.get("status") == "write_executed"
            and (clarification_transition.get("after_state") or {}).get("status") == "待澄清"
            else "failed",
            "message": f"transition={clarification_transition.get('status')} after={clarification_transition.get('after_state')}",
        }
    )

    invalid_transition_plan = build_yunxiao_transaction_plan(
        manager=transition_manager,
        project_key="self_check",
        entity=entity,
        run_id=8909,
        outcome="developed_unverified",
        evidence_ids=["ev-transition-fake"],
        risk_level="medium",
        current_status="待开发",
        target_status="已完成",
        human_confirmed=True,
        persist_audit=False,
    )
    invalid_transition = first_action(invalid_transition_plan, "transition")
    checks.append(
        {
            "name": "transaction_transition_fake_rejects_illegal_transition",
            "status": "pass" if invalid_transition.get("status") == "rejected" and "不允许" in (invalid_transition.get("error") or "") else "failed",
            "message": invalid_transition.get("error") or "-",
        }
    )

    real_transition_manager = YunxiaoTransactionManager.controlled_write(
        policy=transition_policy,
        write_confirm=confirm,
        write_transport="real",
        write_scope="transition-fake",
    )
    real_transition_plan = build_yunxiao_transaction_plan(
        manager=real_transition_manager,
        project_key="self_check",
        entity=entity,
        run_id=8910,
        outcome="developed_unverified",
        evidence_ids=["ev-transition-fake"],
        risk_level="medium",
        current_status="待开发",
        target_status="开发中",
        human_confirmed=True,
        persist_audit=False,
    )
    real_transition_comment = first_action(real_transition_plan, "comment")
    real_transition_action = first_action(real_transition_plan, "transition")
    checks.append(
        {
            "name": "transaction_transition_fake_real_transport_blocked",
            "status": "pass"
            if real_transition_comment.get("status") == "write_blocked"
            and real_transition_action.get("status") == "write_blocked"
            and real_transition_plan.get("effective_write_status") == "blocked_by_safety"
            else "failed",
            "message": f"comment={real_transition_comment.get('status')} transition={real_transition_action.get('status')} effective={real_transition_plan.get('effective_write_status')}",
        }
    )

    high_risk_transition_plan = build_yunxiao_transaction_plan(
        manager=transition_manager,
        project_key="self_check",
        entity=YunxiaoEntityRef(kind="bug", entity_id="DFHIS-HIGH-RISK", title="医保状态流转 fake 自测"),
        run_id=8911,
        outcome="high_risk_needs_review",
        evidence_ids=["ev-transition-fake"],
        risk_level="high",
        current_status="待开发",
        target_status="待人工审核",
        persist_audit=False,
    )
    high_risk_transition = first_action(high_risk_transition_plan, "transition")
    checks.append(
        {
            "name": "transaction_transition_fake_high_risk_requires_human_confirmation",
            "status": "pass" if high_risk_transition.get("status") == "rejected" and "人工确认" in (high_risk_transition.get("error") or "") else "failed",
            "message": high_risk_transition.get("error") or "-",
        }
    )
    return checks


def first_artifact_json(
    payload: dict,
    kind: str,
    *,
    output_dir: Path | None = None,
) -> dict:
    for artifact in payload.get("artifacts", []):
        if artifact.get("kind") == kind:
            content = artifact.get("content")
            if content:
                return json.loads(content)
            output_name = str(artifact.get("output_name") or "").strip()
            if output_dir is not None and output_name:
                artifact_path = (output_dir / output_name).resolve()
                if artifact_path.parent == output_dir.resolve() and artifact_path.is_file():
                    return json.loads(artifact_path.read_text(encoding="utf-8"))
            return {}
    return {}


def first_action(plan: dict, action: str) -> dict:
    for item in plan.get("actions", []):
        if item.get("action") == action:
            return item
    return {}


def run_worktree_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    repo = create_worktree_fixture_repo(output_dir / "worktree_fixture_repo")
    executor = _SelfCheckWorktreeExecutor(MockLLMClient())
    success_result = executor.execute(
        WorktreeExecutionOptions(
            project_path=str(repo),
            run_id=9001,
            demand_text="自测：修改前端文件并通过验证。",
            report_markdown="mock report",
            evidence_bundle={"evidence_files": [{"path": "src/App.js"}]},
            worktree_root=str(output_dir / "worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; assert 'HARNESS_WORKTREE_SELF_CHECK' in Path('src/App.js').read_text()\""
            ],
            max_edit_rounds=0,
        )
    )
    checks.append(
        {
            "name": "worktree_success_pipeline",
            "status": (
                "pass"
                if success_result.status == "success"
                and "HARNESS_WORKTREE_SELF_CHECK" in success_result.final_diff
                and success_result.apply_to_project.get("status") == "success"
                and success_result.cleanup.get("status") == "manual_cleanup_required"
                and "HARNESS_WORKTREE_SELF_CHECK" in (repo / "src" / "App.js").read_text(encoding="utf-8")
                and Path(success_result.worktree_path).exists()
                else "failed"
            ),
            "message": (
                f"{success_result.summary}; apply={success_result.apply_to_project.get('status')}; "
                f"cleanup={success_result.cleanup.get('status')}"
            ),
        }
    )
    run_subprocess(["git", "reset", "--hard", "HEAD"], cwd=repo)

    validation = validate_patch(
        "diff --git a/src/Other.js b/src/Other.js\n--- a/src/Other.js\n+++ b/src/Other.js\n@@ -1 +1 @@\n-old\n+new\n",
        allowed_paths=["src/App.js"],
    )
    checks.append(
        {
            "name": "worktree_allowed_path_rejection",
            "status": "pass" if not validation.ok and "白名单外路径" in validation.message else "failed",
            "message": validation.message,
        }
    )

    non_git_dir = output_dir / "not_git_project"
    non_git_dir.mkdir(parents=True, exist_ok=True)
    non_git_result = executor.execute(
        WorktreeExecutionOptions(
            project_path=str(non_git_dir),
            run_id=9002,
            demand_text="自测：非 Git 项目失败。",
            report_markdown="mock report",
            worktree_root=str(output_dir / "worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=["python3 -c \"raise SystemExit(0)\""],
            max_edit_rounds=0,
        )
    )
    checks.append(
        {
            "name": "worktree_non_git_rejection",
            "status": "pass" if non_git_result.status == "failed" and "Git 仓库" in non_git_result.summary else "failed",
            "message": non_git_result.summary,
        }
    )

    verify_fail_result = executor.execute(
        WorktreeExecutionOptions(
            project_path=str(repo),
            run_id=9003,
            demand_text="自测：验证失败后停止。",
            report_markdown="mock report",
            evidence_bundle={"evidence_files": [{"path": "src/App.js"}]},
            worktree_root=str(output_dir / "worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; import sys; sys.exit(7 if 'HARNESS_WORKTREE_SELF_CHECK' in Path('src/App.js').read_text() else 0)\""
            ],
            max_edit_rounds=1,
        )
    )
    checks.append(
        {
            "name": "worktree_verify_failure_stop",
            "status": "pass" if verify_fail_result.status == "failed" and len(verify_fail_result.attempts) == 2 else "failed",
            "message": f"{verify_fail_result.summary}; attempts={len(verify_fail_result.attempts)}",
        }
    )
    baseline_existing_result = executor.execute(
        WorktreeExecutionOptions(
            project_path=str(repo),
            run_id=9005,
            demand_text="自测：历史验证失败不应被误报为本次回归。",
            report_markdown="mock report",
            evidence_bundle={"evidence_files": [{"path": "src/App.js"}]},
            worktree_root=str(output_dir / "worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=["python3 -c \"raise SystemExit(7)\""],
            max_edit_rounds=0,
        )
    )
    baseline_attempt = baseline_existing_result.attempts[0] if baseline_existing_result.attempts else {}
    baseline_results = (baseline_attempt.get("baseline_verification") or {}).get("results") or []
    checks.append(
        {
            "name": "worktree_baseline_failure_is_not_patch_regression",
            "status": (
                "pass"
                if baseline_existing_result.status == "failed"
                and baseline_attempt.get("status") == "success"
                and baseline_existing_result.verification_status == "baseline_failed"
                and baseline_existing_result.apply_to_project.get("status") == "not_run"
                and baseline_results
                and baseline_results[0].get("matches")
                else "failed"
            ),
            "message": f"{baseline_existing_result.summary}; baseline={baseline_results}",
        }
    )
    run_subprocess(["git", "reset", "--hard", "HEAD"], cwd=repo)
    side_effect_result = executor.execute(
        WorktreeExecutionOptions(
            project_path=str(repo),
            run_id=9004,
            demand_text="自测：验证命令修改临时 worktree 后停止。",
            report_markdown="mock report",
            evidence_bundle={"evidence_files": [{"path": "src/App.js"}]},
            worktree_root=str(output_dir / "worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; p=Path('src/App.js'); p.write_text(p.read_text() + '// VERIFY_SIDE_EFFECT\\\\n')\""
            ],
            max_edit_rounds=0,
        )
    )
    first_attempt = side_effect_result.attempts[0] if side_effect_result.attempts else {}
    checks.append(
        {
            "name": "worktree_verify_side_effect_stop",
            "status": "pass" if side_effect_result.status == "failed" and first_attempt.get("status") == "verify_side_effect_failed" else "failed",
            "message": f"{side_effect_result.summary}; attempt_status={first_attempt.get('status')}",
        }
    )
    checks.extend(run_single_demand_trial_checks())
    checks.extend(run_fullstack_worktree_checks(output_dir=output_dir))
    return checks


def run_single_demand_trial_checks() -> list[dict]:
    checks: list[dict] = []
    technical_decision = {
        "implementation_decision": {
            "can_patch": True,
            "blockers": [],
            "summary": "fixture 可以进入受控 patch。",
        }
    }
    success_worktree = WorktreeExecutionResult(
        status="success",
        summary="Patch 已在独立 worktree 中通过验证，并已合入原业务目录；未提交、未推送、未发布。",
        worktree_path="/tmp/his_harness_worktrees/run_9901",
        allowed_paths=["src/App.js"],
        attempts=[
            {
                "attempt": 1,
                "status": "success",
                "changed_paths": ["src/App.js"],
                "diff_check": {"command": "git diff --check", "returncode": 0, "stdout": "", "stderr": ""},
                "verify": [
                    {
                        "command": "python3 -m py_compile src/App.js",
                        "returncode": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "side_effects": {"changed": False, "changed_paths": []},
                    }
                ],
            }
        ],
        final_diff="diff --git a/src/App.js b/src/App.js\n",
        apply_to_project={"status": "success", "changed_paths": ["src/App.js"]},
        cleanup={"status": "success"},
    )
    success_package = build_single_demand_trial_package(
        run_id=9901,
        technical_decision=technical_decision,
        acceptance_matrix={"manual_acceptance": [{"scenario": "人工检查页面展示"}]},
        project_paths=["/tmp/repo"],
        allowed_paths=["src/App.js"],
        verify_commands=["python3 -m py_compile src/App.js"],
        worktree_result=success_worktree,
        transaction_mode="dry-run",
        write_scope="comment-only",
    )
    checks.append(
        {
            "name": "single_demand_trial_success_boundaries",
            "status": "pass"
            if success_package.status == "success"
            and success_package.decision.get("manual_commit_allowed")
            and not success_package.decision.get("auto_commit_allowed")
            and not success_package.decision.get("yunxiao_real_transition_allowed")
            and "不自动 commit" in success_package.code_review_markdown()
            else "failed",
            "message": f"status={success_package.status}; decision={success_package.decision}",
        }
    )

    blocked_package = build_single_demand_trial_package(
        run_id=9902,
        technical_decision={
            "implementation_decision": {
                "can_patch": False,
                "blockers": ["未定位目标页面。"],
            }
        },
        acceptance_matrix={},
        project_paths=[],
        allowed_paths=[],
        verify_commands=[],
        worktree_result=None,
        transaction_mode="off",
        write_scope="comment-only",
    )
    checks.append(
        {
            "name": "single_demand_trial_blocked_package",
            "status": "pass"
            if blocked_package.status == "blocked"
            and any("未定位目标页面" in item for item in blocked_package.blockers)
            and any(item.get("status") == "not_run" for item in blocked_package.verification_matrix)
            else "failed",
            "message": f"status={blocked_package.status}; blockers={blocked_package.blockers}",
        }
    )
    return checks


def run_fullstack_worktree_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    root = create_fullstack_fixture_root(output_dir / "fullstack_fixture_root")
    executor = _SelfCheckFullstackExecutor()
    result = executor.execute(
        FullstackExecutionOptions(
            run_id=9201,
            demand_text="自测：DFHIS-31270 多项目全栈 patch。",
            report_markdown="mock report",
            project_root=str(root),
            authority_mode="legacy",
            worktree_root=str(output_dir / "fullstack_worktrees"),
            verify_commands=[
                "python3 -c \"from pathlib import Path; text = Path('src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue').read_text(); assert \\\"dataField: 'beiZhu'\\\" in text and 'grid-view-name=\\\"jieSuanInfo_YuJiaoKuanInfo\\\"' in text\""
            ],
        )
    )
    vue_file = root / "df-web-zhuyuansf" / "src" / "pages" / "chuYuanYw" / "jieSuan" / "dialog" / "jieSuan.vue"
    checks.append(
        {
            "name": "fullstack_worktree_success_pipeline",
            "status": (
                "pass"
                if result.status == "success"
                and "dataField: 'beiZhu'" in vue_file.read_text(encoding="utf-8")
                and 'grid-view-name="jieSuanInfo_YuJiaoKuanInfo"' in vue_file.read_text(encoding="utf-8")
                and all(item.get("status") == "success" for item in result.apply_to_projects.values())
                and all(item.get("status") == "manual_cleanup_required" for item in result.cleanup.values())
                and all(Path(item.get("worktree_path") or "").exists() for item in result.cleanup.values())
                else "failed"
            ),
            "message": result.summary,
        }
    )

    dirty_root = create_fullstack_fixture_root(output_dir / "fullstack_dirty_fixture_root")
    dirty_file = dirty_root / "df-web-zhuyuansf" / "src" / "pages" / "chuYuanYw" / "jieSuan" / "dialog" / "jieSuan.vue"
    dirty_file.write_text(dirty_file.read_text(encoding="utf-8") + "\n# dirty\n", encoding="utf-8")
    dirty_result = executor.execute(
        FullstackExecutionOptions(
            run_id=9202,
            demand_text="自测：任一项目 dirty 时整体阻断。",
            report_markdown="mock report",
            project_root=str(dirty_root),
            authority_mode="legacy",
            worktree_root=str(output_dir / "fullstack_worktrees"),
        )
    )
    checks.append(
        {
            "name": "fullstack_worktree_dirty_repo_rejection",
            "status": "pass" if dirty_result.status == "failed" and "未提交改动" in dirty_result.summary else "failed",
            "message": dirty_result.summary,
        }
    )
    precommit_root = create_fullstack_fixture_root(output_dir / "precommit_fixture_root")
    fullstack_result = executor.execute(
        FullstackExecutionOptions(
            run_id=9203,
            demand_text="自测：先生成 DFHIS-31270 多项目 diff。",
            report_markdown="mock report",
            project_root=str(precommit_root),
            authority_mode="legacy",
            worktree_root=str(output_dir / "fullstack_worktrees"),
            verify_commands=[
                "python3 -c \"from pathlib import Path; text = Path('src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue').read_text(); assert \\\"dataField: 'beiZhu'\\\" in text and 'grid-view-name=\\\"jieSuanInfo_YuJiaoKuanInfo\\\"' in text\""
            ],
        )
    )
    precommit_result = _SelfCheckPrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=9301,
            project_root=str(precommit_root),
            worktree_root=str(output_dir / "precommit_worktrees"),
            verify_command_overrides={
                "df-web-zhuyuansf": [
                    "python3 -c \"from pathlib import Path; text = Path('src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue').read_text(); assert \\\"dataField: 'beiZhu'\\\" in text and 'grid-view-name=\\\"jieSuanInfo_YuJiaoKuanInfo\\\"' in text\""
                ],
            },
        )
    )
    checks.append(
        {
            "name": "precommit_verification_success_pipeline",
            "status": (
                "pass"
                if fullstack_result.status == "success"
                and precommit_result.status == "success"
                and precommit_result.verification_matrix.get("can_commit") is True
                and precommit_result.verification_matrix.get("can_yunxiao_transition") is False
                else "failed"
            ),
            "message": precommit_result.summary,
        }
    )
    untracked_repo = create_untracked_precommit_fixture(output_dir / "precommit_untracked_fixture")
    untracked_result = _SelfCheckPrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=9302,
            project_root=str(output_dir),
            project_path=str(untracked_repo),
            allowed_paths=["src/App.js", "src/helper.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; assert Path('src/helper.js').is_file(); assert \\\"require('./helper')\\\" in Path('src/App.js').read_text()\""
            ],
            title="自测：precommit 支持白名单内新增文件",
            entity_id="SELF-CHECK-UNTRACKED",
            demand_text="本地 diff 包含一个已修改文件和一个白名单内未跟踪新增文件，precommit 必须在临时 worktree 中完整复现。",
            worktree_root=str(output_dir / "precommit_worktrees"),
        )
    )
    untracked_target = (untracked_result.targets or [{}])[0]
    checks.append(
        {
            "name": "precommit_includes_allowed_untracked_files",
            "status": (
                "pass"
                if untracked_result.status == "success"
                and "src/helper.js" in (untracked_target.get("changed_paths") or [])
                and "new file mode" in str(untracked_target.get("current_diff") or "")
                else "failed"
            ),
            "message": untracked_result.summary,
        }
    )
    large_untracked_repo = create_untracked_precommit_fixture(output_dir / "precommit_large_untracked_fixture")
    large_helper = large_untracked_repo / "src" / "helper.js"
    large_helper.write_text(
        "module.exports = value => value\n" + "".join(f"// filler line {index}\n" for index in range(2600)),
        encoding="utf-8",
    )
    large_untracked_result = _SelfCheckPrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=9304,
            project_root=str(output_dir),
            project_path=str(large_untracked_repo),
            allowed_paths=["src/App.js", "src/helper.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; assert Path('src/helper.js').read_text().count('filler line') == 2600\""
            ],
            title="自测：precommit 保留大 diff 原文",
            entity_id="SELF-CHECK-LARGE-UNTRACKED",
            demand_text="本地 diff 超过日志截断长度时，precommit 仍必须使用完整 patch 做临时 worktree apply-check。",
            worktree_root=str(output_dir / "precommit_worktrees"),
        )
    )
    large_untracked_target = (large_untracked_result.targets or [{}])[0]
    checks.append(
        {
            "name": "precommit_keeps_large_diff_untruncated_for_apply",
            "status": (
                "pass"
                if large_untracked_result.status == "success"
                and "src/helper.js" in (large_untracked_target.get("changed_paths") or [])
                and "...（日志已截断）..." not in str(large_untracked_target.get("current_diff") or "")
                else "failed"
            ),
            "message": large_untracked_result.summary,
        }
    )
    dirty_scope_repo = create_untracked_precommit_fixture(output_dir / "precommit_dirty_scope_fixture")
    (dirty_scope_repo / "src" / "unrelated.js").write_text("export const unrelated = true\n", encoding="utf-8")
    dirty_scope_result = _SelfCheckPrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=9303,
            project_root=str(output_dir),
            project_path=str(dirty_scope_repo),
            allowed_paths=["src/App.js", "src/helper.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; assert Path('src/helper.js').is_file(); assert \\\"require('./helper')\\\" in Path('src/App.js').read_text()\""
            ],
            title="自测：precommit 白名单外改动降级为范围告警",
            entity_id="SELF-CHECK-SCOPE",
            demand_text="目标 diff 验证通过，但同仓库还有其他未提交文件时，应阻止直接提交而不是判定目标验证失败。",
            worktree_root=str(output_dir / "precommit_worktrees"),
        )
    )
    checks.append(
        {
            "name": "precommit_unrelated_dirty_scope_blocks_commit_only",
            "status": (
                "pass"
                if dirty_scope_result.status == "success"
                and dirty_scope_result.verification_matrix.get("can_commit") is False
                and dirty_scope_result.verification_matrix.get("can_enter_test") == "人工代码审查通过后可进入测试"
                and dirty_scope_result.verification_matrix.get("warnings")
                else "failed"
            ),
            "message": dirty_scope_result.summary,
        }
    )
    return checks


def run_review_checks(*, output_dir: Path) -> list[dict]:
    checks: list[dict] = []
    repo = create_review_fixture_repo(output_dir / "review_fixture_repo")
    executor = ReviewWorktreeExecutor()
    try:
        context = build_review_context(
            project_path=repo,
            review_commit="HEAD",
            review_base="HEAD^",
            allowed_paths=["src/App.js"],
        )
        success_result = executor.execute(
            ReviewExecutionOptions(
                project_path=str(repo),
                run_id=9101,
                review_commit="HEAD",
                review_base="HEAD^",
                review_context=context,
                worktree_root=str(output_dir / "review_worktrees"),
                allowed_paths=["src/App.js"],
                verify_commands=[
                    "python3 -c \"from pathlib import Path; assert 'reviewed harness' in Path('src/App.js').read_text()\""
                ],
            )
        )
        checks.append(
            {
                "name": "review_worktree_success_pipeline",
                "status": "pass" if success_result.status == "success" and "reviewed harness" in success_result.review_diff else "failed",
                "message": success_result.summary,
            }
        )
    except Exception as exc:
        checks.append({"name": "review_worktree_success_pipeline", "status": "failed", "message": redact_secrets(str(exc))})

    try:
        build_review_context(
            project_path=repo,
            review_commit="HEAD",
            review_base="HEAD^",
            allowed_paths=["src/Other.js"],
        )
        checks.append({"name": "review_allowed_path_rejection", "status": "failed", "message": "未拦截白名单外提交"})
    except Exception as exc:
        message = redact_secrets(str(exc))
        checks.append(
            {
                "name": "review_allowed_path_rejection",
                "status": "pass" if "超出 --allowed-path" in message else "failed",
                "message": message,
            }
        )

    non_git_dir = output_dir / "review_not_git_project"
    non_git_dir.mkdir(parents=True, exist_ok=True)
    non_git_result = executor.execute(
        ReviewExecutionOptions(
            project_path=str(non_git_dir),
            run_id=9102,
            review_commit="HEAD",
            review_base="HEAD^",
            review_context={
                "review_commit": {"sha": "HEAD"},
                "review_base": {"sha": "HEAD^"},
                "changed_paths": ["src/App.js"],
                "allowed_paths": ["src/App.js"],
                "diff_excerpt": "",
                "diff_stat": "",
            },
            worktree_root=str(output_dir / "review_worktrees"),
            allowed_paths=["src/App.js"],
        )
    )
    checks.append(
        {
            "name": "review_non_git_rejection",
            "status": "pass" if non_git_result.status == "failed" and "Git 仓库" in non_git_result.summary else "failed",
            "message": non_git_result.summary,
        }
    )

    dirty_repo = create_review_fixture_repo(output_dir / "review_dirty_repo")
    (dirty_repo / "src" / "App.js").write_text("export const message = 'dirty';\n", encoding="utf-8")
    try:
        build_review_context(
            project_path=dirty_repo,
            review_commit="HEAD",
            review_base="HEAD^",
            allowed_paths=["src/App.js"],
        )
        checks.append({"name": "review_dirty_repo_rejection", "status": "failed", "message": "未拦截原仓库未提交改动"})
    except Exception as exc:
        message = redact_secrets(str(exc))
        checks.append(
            {
                "name": "review_dirty_repo_rejection",
                "status": "pass" if "未提交改动" in message else "failed",
                "message": message,
            }
        )

    verify_fail_context = build_review_context(
        project_path=repo,
        review_commit="HEAD",
        review_base="HEAD^",
        allowed_paths=["src/App.js"],
    )
    baseline_result = executor.execute(
        ReviewExecutionOptions(
            project_path=str(repo),
            run_id=9103,
            review_commit="HEAD",
            review_base="HEAD^",
            review_context=verify_fail_context,
            worktree_root=str(output_dir / "review_worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=["python3 -c \"import sys; sys.stderr.write('BASELINE_LINT_ERROR\\n'); raise SystemExit(7)\""],
        )
    )
    baseline_classification = first_review_classification(baseline_result)
    checks.append(
        {
            "name": "review_baseline_existing_not_blocking",
            "status": "pass" if baseline_result.status == "success" and baseline_classification == "baseline_existing" else "failed",
            "message": f"{baseline_result.summary}; classification={baseline_classification}",
        }
    )

    base_side_effect_result = executor.execute(
        ReviewExecutionOptions(
            project_path=str(repo),
            run_id=9107,
            review_commit="HEAD",
            review_base="HEAD^",
            review_context=verify_fail_context,
            worktree_root=str(output_dir / "review_worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; import sys; p=Path('src/App.js'); text=p.read_text(); "
                "p.write_text(text + '// BASE_SIDE_EFFECT\\\\n') if 'reviewed harness' not in text else None; "
                "sys.stderr.write('BASELINE_LINT_ERROR\\\\n'); raise SystemExit(7)\""
            ],
        )
    )
    base_side_effect_classification = first_review_classification(base_side_effect_result)
    checks.append(
        {
            "name": "review_baseline_side_effect_warning",
            "status": "pass" if base_side_effect_result.status == "success" and base_side_effect_classification == "baseline_side_effect" else "failed",
            "message": f"{base_side_effect_result.summary}; classification={base_side_effect_classification}",
        }
    )

    head_side_effect_result = executor.execute(
        ReviewExecutionOptions(
            project_path=str(repo),
            run_id=9108,
            review_commit="HEAD",
            review_base="HEAD^",
            review_context=verify_fail_context,
            worktree_root=str(output_dir / "review_worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; p=Path('src/App.js'); text=p.read_text(); "
                "p.write_text(text + '// HEAD_SIDE_EFFECT\\\\n') if 'reviewed harness' in text else None\""
            ],
        )
    )
    head_side_effect_classification = first_review_classification(head_side_effect_result)
    checks.append(
        {
            "name": "review_head_side_effect_blocking",
            "status": "pass" if head_side_effect_result.status == "failed" and head_side_effect_classification == "head_side_effect_failed" else "failed",
            "message": f"{head_side_effect_result.summary}; classification={head_side_effect_classification}",
        }
    )

    regression_result = executor.execute(
        ReviewExecutionOptions(
            project_path=str(repo),
            run_id=9104,
            review_commit="HEAD",
            review_base="HEAD^",
            review_context=verify_fail_context,
            worktree_root=str(output_dir / "review_worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; import sys; text=Path('src/App.js').read_text(); sys.exit(7 if 'reviewed harness' in text else 0)\""
            ],
        )
    )
    regression_classification = first_review_classification(regression_result)
    checks.append(
        {
            "name": "review_regression_failure_reported",
            "status": "pass" if regression_result.status == "failed" and regression_classification == "regression_failed" else "failed",
            "message": f"{regression_result.summary}; classification={regression_classification}",
        }
    )

    changed_failure_result = executor.execute(
        ReviewExecutionOptions(
            project_path=str(repo),
            run_id=9105,
            review_commit="HEAD",
            review_base="HEAD^",
            review_context=verify_fail_context,
            worktree_root=str(output_dir / "review_worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=[
                "python3 -c \"from pathlib import Path; import sys; sys.stderr.write(Path('src/App.js').read_text()); raise SystemExit(5)\""
            ],
        )
    )
    changed_classification = first_review_classification(changed_failure_result)
    checks.append(
        {
            "name": "review_changed_failure_reported",
            "status": "pass" if changed_failure_result.status == "failed" and changed_classification == "changed_failure" else "failed",
            "message": f"{changed_failure_result.summary}; classification={changed_classification}",
        }
    )

    diff_check_repo = create_review_diff_check_fixture_repo(output_dir / "review_diff_check_repo")
    diff_check_context = build_review_context(
        project_path=diff_check_repo,
        review_commit="HEAD",
        review_base="HEAD^",
        allowed_paths=["src/App.js"],
    )
    diff_check_result = executor.execute(
        ReviewExecutionOptions(
            project_path=str(diff_check_repo),
            run_id=9106,
            review_commit="HEAD",
            review_base="HEAD^",
            review_context=diff_check_context,
            worktree_root=str(output_dir / "review_worktrees"),
            allowed_paths=["src/App.js"],
            verify_commands=["python3 -c \"print('should not block before diff check')\""],
        )
    )
    checks.append(
        {
            "name": "review_diff_check_failure_reported",
            "status": "pass" if diff_check_result.status == "failed" and "git diff --check" in diff_check_result.summary else "failed",
            "message": diff_check_result.summary,
        }
    )
    return checks


def first_review_classification(result) -> str:
    if not result.verify_results:
        return ""
    return str(result.verify_results[0].get("classification") or "")


def create_worktree_fixture_repo(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    app_file = root / "src" / "App.js"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("export const message = 'hello harness';\n", encoding="utf-8")
    run_subprocess(["git", "init"], cwd=root)
    run_subprocess(["git", "add", "src/App.js"], cwd=root)
    run_subprocess(
        ["git", "-c", "user.name=Harness Self Check", "-c", "user.email=harness@example.local", "commit", "-m", "init fixture"],
        cwd=root,
    )
    return root


def create_review_fixture_repo(root: Path) -> Path:
    repo = create_worktree_fixture_repo(root)
    app_file = repo / "src" / "App.js"
    app_file.write_text("export const message = 'reviewed harness';\n", encoding="utf-8")
    run_subprocess(["git", "add", "src/App.js"], cwd=repo)
    run_subprocess(
        ["git", "-c", "user.name=Harness Self Check", "-c", "user.email=harness@example.local", "commit", "-m", "review fixture change"],
        cwd=repo,
    )
    return repo


def create_review_diff_check_fixture_repo(root: Path) -> Path:
    repo = create_worktree_fixture_repo(root)
    app_file = repo / "src" / "App.js"
    app_file.write_text("export const message = 'trailing whitespace'; \n", encoding="utf-8")
    run_subprocess(["git", "add", "src/App.js"], cwd=repo)
    run_subprocess(
        ["git", "-c", "user.name=Harness Self Check", "-c", "user.email=harness@example.local", "commit", "-m", "diff check fixture change"],
        cwd=repo,
    )
    return repo


def create_fullstack_fixture_root(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    service_repo = root / "df-mic-jj-zhuyuan"
    bff_repo = root / "df-bff-zhuyuansf"
    web_repo = root / "df-web-zhuyuansf"
    settings_file = service_repo / "settings.gradle"
    service_file = service_repo / "mic-jj-zhuyuan-api" / "src" / "main" / "java" / "com" / "df" / "cbhis" / "mic" / "jj" / "zhuyuan" / "dto" / "DTO_ZY_YuJiaoKuan.java"
    graphql_file = bff_repo / "src" / "main" / "resources" / "graphql" / "jjzhuyuan.graphqls"
    vue_file = web_repo / "src" / "pages" / "chuYuanYw" / "jieSuan" / "dialog" / "jieSuan.vue"
    service_file.parent.mkdir(parents=True, exist_ok=True)
    graphql_file.parent.mkdir(parents=True, exist_ok=True)
    vue_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(
        "\n".join(
            [
                "rootProject.name = 'df-mic-jj-zhuyuan'",
                "//include 'mic-jj-zhuyuan-api'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    service_file.write_text(
        "\n".join(
            [
                "package com.df.cbhis.mic.jj.zhuyuan.dto;",
                "",
                "public class DTO_ZY_YuJiaoKuan {",
                "    private String pingZhengHao;",
                "    private String zhiFuFsMc;",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    graphql_file.write_text(
        "\n".join(
            [
                "type DTO_ZY_YuJiaoKuan{",
                "     pingZhengHao : String,",
                "     zhiFuFsMc : String,",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    vue_file.write_text(
        "\n".join(
            [
                "<template>",
                "  <df-dx-table",
                "          :allowColumnConfig=\"false\"",
                "          :grid-data-columns=\"yuJiaoJinColumns\"",
                "  />",
                "</template>",
                "<script>",
                "export default {",
                "  data () {",
                "    return {",
                "      yuJiaoJinColumns: [",
                "        {",
                "          caption: '收款人',",
                "          dataField: 'shouKuanRenXm',",
                "          width: 120,",
                "          allowSorting: false",
                "        }",
                "      ]",
                "    }",
                "  }",
                "}",
                "</script>",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for repo, file_paths in [(service_repo, [service_file, settings_file]), (bff_repo, [graphql_file]), (web_repo, [vue_file])]:
        run_subprocess(["git", "init"], cwd=repo)
        run_subprocess(["git", "add", *[str(file_path.relative_to(repo)) for file_path in file_paths]], cwd=repo)
        run_subprocess(
            ["git", "-c", "user.name=Harness Self Check", "-c", "user.email=harness@example.local", "commit", "-m", "init fixture"],
            cwd=repo,
        )
    return root


def create_untracked_precommit_fixture(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    app_file = root / "src" / "App.js"
    helper_file = root / "src" / "helper.js"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("export const value = 'base'\n", encoding="utf-8")
    run_subprocess(["git", "init"], cwd=root)
    run_subprocess(["git", "add", "src/App.js"], cwd=root)
    run_subprocess(
        ["git", "-c", "user.name=Harness Self Check", "-c", "user.email=harness@example.local", "commit", "-m", "init precommit fixture"],
        cwd=root,
    )
    app_file.write_text("const helper = require('./helper')\nexport const value = helper('head')\n", encoding="utf-8")
    helper_file.write_text("module.exports = value => value\n", encoding="utf-8")
    return root


def run_subprocess(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{completed.stderr or completed.stdout}")


def create_fixture_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "package.json": json.dumps(
            {
                "scripts": {
                    "test": "echo frontend test",
                    "build": "echo frontend build",
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        "pom.xml": "<project><modelVersion>4.0.0</modelVersion><groupId>his</groupId><artifactId>fixture</artifactId></project>",
        "src/pages/outpatient/WaitingList.vue": """
<template>
  <PatientTable :columns="columns" />
</template>
<script setup>
const columns = ['患者姓名', '患者年龄', '叫号状态']
</script>
""".strip(),
        "backend/src/main/java/com/dfhis/order/MedicalOrderService.java": """
package com.dfhis.order;
public class MedicalOrderService {
    public void submitOrder() {
        // 医嘱提交后需要记录日志，兼容旧版本接口字段。
    }
}
""".strip(),
        "backend/src/main/resources/mapper/InsuranceSettlementMapper.xml": """
<mapper namespace="InsuranceSettlementMapper">
  <select id="selectSettlementReport">
    select 医保基金支付, 自费金额, 统筹支付 from settlement_report
  </select>
</mapper>
""".strip(),
        "backend/src/test/java/com/dfhis/order/MedicalOrderServiceTest.java": """
package com.dfhis.order;
public class MedicalOrderServiceTest {}
""".strip(),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def create_backend_only_fixture_project(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "pom.xml": "<project><modelVersion>4.0.0</modelVersion><groupId>his</groupId><artifactId>backend</artifactId></project>",
        "src/main/java/com/dfhis/ArrangeService.java": """
package com.dfhis;
public class ArrangeService {
    public void saveSchedule() {
        // 排班保存、事务和日志验证入口。
    }
}
""".strip(),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def snapshot_project(root: Path) -> dict:
    snapshot: dict[str, str] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = str(path.stat().st_size) + ":" + path.read_bytes().hex()
    return snapshot


if __name__ == "__main__":
    from app.runtime_bootstrap import reexec_in_project_venv

    reexec_in_project_venv(PROJECT_ROOT)
    main()
