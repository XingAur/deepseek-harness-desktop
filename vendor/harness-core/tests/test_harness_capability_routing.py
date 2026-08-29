from __future__ import annotations

import hashlib
from itertools import count
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import database
from app.capability_registry import CapabilityRegistry
from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityRequest,
    MutationLevel,
)
from app.capability_runtime import CapabilityRuntime
from app.capability_service import CapabilityService
from app.plugin_inventory import PluginInventoryError
from app.core_closure import DiffReview, RequirementContract, review_final_diff
from app.evaluator import EvaluationResult
from app.fullstack_executor import (
    FullstackExecutionOptions,
    FullstackExecutionResult,
    FullstackWorktreeExecutor,
    build_dfhis_31270_targets,
)
from app.harness import (
    TASK_CAPABILITY_SEQUENCE,
    CapabilityWorkflowOrchestrator,
    RequirementWorkflowRunner,
    WorkflowResult,
    build_markdown_report,
    build_workitem_read_request,
    resolve_capability_routing,
)
from app.llm_client import MockLLMClient
from app.worktree_executor import WorktreeExecutionResult
from app.task_intent_repository import TaskIntentRepository
from app.task_intent_router import IntentContext
from app.task_intent_service import TaskIntentService
from harnesses.his_requirement_workflow import build_capability_service, build_parser
from tests.test_core_closure import (
    ordering_diff,
    ready_ordering_review_contract,
)
from tests.test_requirement_governance_integration import (
    ready_governance,
    ready_single_pass,
    ready_technical_decision,
)
from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT


TEST_CHANGE_REQUIREMENT_TEXT = (
    "当前页面需要向护士展示 paiBanMs 只读字段，避免排班确认时看不到诊室。"
    "用户点击查询后，页面只显示该字段并保持既有查询行为；不涉及收费、医保或结算。\n"
    "```harness-rules\n"
    "[{\"name\":\"paiBanMs\",\"location\":\"response_field\","
    "\"allowed_values\":{\"readonly\":\"展示字段值\","
    "\"default\":\"保持原有查询行为\"}}]\n"
    "```"
)


def write_ready_change_evidence(root: Path, project_name: str, paths: tuple[str, ...]) -> Path:
    evidence_path = root / "requirement-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "source_type": "test",
                "title": "受控只读字段变更",
                "description_text": TEST_CHANGE_REQUIREMENT_TEXT,
                "project": project_name,
                "evidence_quality": {"analysis_ready": True},
                "evidence": [
                    {"project": project_name, "path": path, "reason": "测试用源码入口证据"}
                    for path in paths
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return evidence_path


def route_result(
    request,
    *,
    status: str = "success",
    data: object | None = None,
    mode: str = "enforce",
):
    return SimpleNamespace(
        result={
            "request_id": request.request_id,
            "capability": request.capability,
            "provider": request.provider,
            "status": status,
            "mutation_level": request.mutation_level.name,
            "changed": False,
            "summary": status,
            "data": {} if data is None else data,
            "evidence": [],
            "warnings": [],
            "blockers": [] if status == "success" else ["blocked"],
            "audit": {},
        },
        mode=mode,
    )


class FakeCapabilityService:
    def __init__(
        self,
        answers: dict[str, list[tuple[str, object]]] | None = None,
        *,
        mode: str = "enforce",
    ):
        self.requests = []
        self.answers = answers or {}
        self.mode = mode

    def route(self, request, *, legacy_callable=None, equivalence_fields=()):
        self.requests.append(request)
        queued = self.answers.get(request.capability, [])
        if queued:
            status, data = queued.pop(0)
            return route_result(request, status=status, data=data, mode=self.mode)
        return route_result(request, mode=self.mode)


class RawCapabilityService:
    def __init__(self, result: object, *, mode: str = "enforce"):
        self.result = result
        self.mode = mode
        self.requests = []

    def route(self, request, *, legacy_callable=None, equivalence_fields=()):
        self.requests.append(request)
        return SimpleNamespace(result=self.result, mode=self.mode)


class HarnessCapabilityRoutingTests(unittest.TestCase):
    _routing_sequence = count(1)

    @staticmethod
    def _task_routing():
        return TaskIntentService(TaskIntentRepository()).route(
            "请修改并直接实现这个需求",
            IntentContext(
                conversation_key=(
                    f"test-route-{next(HarnessCapabilityRoutingTests._routing_sequence):04d}"
                )
            ),
        )

    @staticmethod
    def _reviewed_diff() -> str:
        return (
            "diff --git a/src/view.vue b/src/view.vue\n"
            "--- a/src/view.vue\n"
            "+++ b/src/view.vue\n"
            "@@ -1 +1,4 @@\n"
            "-<template />\n"
            "+<template />\n"
            "+const mode = paiBanMs || '';\n"
            "+if (!['1', '2'].includes(mode)) return paiBanList;\n"
        )

    @staticmethod
    def _review_contract() -> RequirementContract:
        return RequirementContract(
            schema_version="1.0-requirement-contract",
            status="ready",
            title="受信 diff 审查",
            demand_digest="显示只读字段",
            allowed_paths=("src/view.vue",),
            verify_commands=("test -f src/view.vue",),
            evidence_refs=({"path": "src/view.vue", "reason": "受控证据"},),
            apply_to_project=True,
        )

    def _database_input(self) -> dict[str, object]:
        return {
            "subject": "确认测试库配置值",
            "keywords": ["配置"],
            "sql": "SELECT code, value FROM his_test.his_config WHERE code = %(code)s",
            "parameters": {"code": "EXAMPLE"},
            "project_root": "/tmp/his-project",
            "profile_policy": "/tmp/pg-policy.json",
        }

    @staticmethod
    def _knowledge_candidate() -> dict[str, object]:
        return {
            "stable_key": "harness.integration.reusable-rule",
            "title": "Harness 可复用治理规则",
            "body": "需求进入本地改码前必须先通过结构化治理门禁。",
            "kind": "workflow",
            "authority": "verified_code",
            "status": "active",
            "hospital_scope": "",
            "region_scope": "",
            "module_scope": "Harness",
            "repo_scope": "",
            "branch_scope": "",
            "version_label": "integration-v1",
            "valid_from": "",
            "valid_until": "",
            "source_refs": [
                {"ref": "harness-governance-contract", "claim_level": "code"}
            ],
            "tags": ["governance", "harness", "workflow"],
        }

    def test_task_local_apply_requires_ready_contract_and_uses_l2_capability(
        self,
    ) -> None:
        service = FakeCapabilityService()
        orchestrator = CapabilityWorkflowOrchestrator(service)

        blocked = orchestrator.run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=False,
            project_path="/tmp/his-project",
            expected_diff="diff --git a/app.py b/app.py\n",
            allowed_paths=("app.py",),
            verify_commands=("python3 -m unittest tests.test_app",),
        )
        ready = orchestrator.run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=True,
            project_path="/tmp/his-project",
            expected_diff="diff --git a/app.py b/app.py\n",
            allowed_paths=("app.py",),
            verify_commands=("python3 -m unittest tests.test_app",),
        )

        self.assertEqual(("local_contract_not_ready",), blocked.data["blockers"])
        self.assertEqual(["git.apply-local"], list(ready.events))
        request = service.requests[0]
        self.assertEqual("git.apply-local", request.capability)
        self.assertEqual(MutationLevel.L2, request.mutation_level)
        self.assertEqual("apply", request.mode)
        self.assertTrue(request.authorization.explicit)
        self.assertEqual(("repository:apply-local",), request.authorization.scope)
        self.assertEqual(
            {
                "project_path": "/tmp/his-project",
                "expected_diff": "diff --git a/app.py b/app.py\n",
                "allowed_paths": ["app.py"],
                "verify_commands": ["python3 -m unittest tests.test_app"],
            },
            request.input,
        )

    def test_orchestrator_requires_routing_receipt_before_capability_provider(self) -> None:
        service = FakeCapabilityService()

        with self.assertRaises(TypeError):
            CapabilityWorkflowOrchestrator(service).run_task_capabilities(
                contract_ready=True,
                expected_diff="diff --git a/app.py b/app.py\n",
            )

        self.assertEqual([], service.requests)

    def test_task_commit_requires_current_user_intent_and_never_routes_remote_git(
        self,
    ) -> None:
        service = FakeCapabilityService()
        orchestrator = CapabilityWorkflowOrchestrator(service)
        delivery = {
            "delivery_db": "/tmp/delivery.sqlite",
            "transaction_id": 7,
            "approved_plan_hash": "a" * 64,
        }

        orchestrator.run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=True,
            explicit_remote_delivery=False,
            delivery=delivery,
        )
        orchestrator.run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=True,
            explicit_remote_delivery=True,
            delivery=delivery,
        )

        self.assertEqual(
            ["git.commit-local"],
            [request.capability for request in service.requests],
        )
        request = service.requests[0]
        self.assertEqual(MutationLevel.L3, request.mutation_level)
        self.assertEqual(("repository:commit-local",), request.authorization.scope)
        self.assertFalse(
            {"git.push", "gitlab.write", "pull-request.create", "rc.integrate"}
            & {item.capability for item in service.requests}
        )

    def test_task_skips_database_when_code_evidence_is_sufficient(self) -> None:
        service = FakeCapabilityService()

        result = CapabilityWorkflowOrchestrator(service).run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=True,
            code_evidence_sufficient=True,
            database_inspect=self._database_input(),
            execute_database=True,
        )

        self.assertEqual((), result.events)
        self.assertEqual([], service.requests)

    def test_task_database_execute_requires_successful_readonly_preview(self) -> None:
        ready_plan = {
            "plan": {
                "status": "ready",
                "guard": {"status": "pass", "blockers": []},
                "selected_profile": "his_test",
            }
        }
        service = FakeCapabilityService(
            {"database.inspect": [("success", ready_plan), ("success", {})]}
        )

        result = CapabilityWorkflowOrchestrator(service).run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=True,
            code_evidence_sufficient=False,
            database_inspect=self._database_input(),
            execute_database=True,
        )

        self.assertEqual(
            ("database.inspect", "database.inspect"),
            result.events,
        )
        preview, execute = service.requests
        self.assertEqual(("preview", "apply"), (preview.mode, execute.mode))
        self.assertEqual(("plan", "execute"), (preview.input["mode"], execute.input["mode"]))
        self.assertFalse(preview.authorization.explicit)
        self.assertTrue(execute.authorization.explicit)
        self.assertEqual(
            ("database:metadata:read", "database:rows:read"),
            execute.authorization.scope,
        )

        blocked_service = FakeCapabilityService(
            {"database.inspect": [("success", {"plan": {"status": "blocked"}})]}
        )
        blocked = CapabilityWorkflowOrchestrator(
            blocked_service
        ).run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=True,
            code_evidence_sufficient=False,
            database_inspect=self._database_input(),
            execute_database=True,
        )
        self.assertEqual(1, len(blocked_service.requests))
        self.assertEqual(
            ("database_readonly_preview_not_ready",),
            blocked.data["blockers"],
        )

    def test_task_database_write_is_change_plan_only(self) -> None:
        service = FakeCapabilityService()
        change = {"operation": "alter table", "reason": "需求评估"}

        result = CapabilityWorkflowOrchestrator(service).run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=True,
            database_change=change,
        )

        self.assertEqual(("database.change-plan",), result.events)
        request = service.requests[0]
        self.assertEqual("database.change-plan", request.capability)
        self.assertEqual(MutationLevel.L0, request.mutation_level)
        self.assertEqual("preview", request.mode)
        self.assertFalse(request.authorization.explicit)
        self.assertNotIn(
            "database.change",
            {item.capability for item in service.requests},
        )

    def test_runner_routes_explicit_database_inputs_and_persists_public_evidence(
        self,
    ) -> None:
        service = FakeCapabilityService(
            {
                "database.inspect": [
                    (
                        "success",
                        {
                            "pg_status": "planned",
                            "effective_mode": "plan",
                            "plan": {
                                "status": "ready",
                                "selected_profile": "his_test",
                                "guard": {
                                    "status": "pass",
                                    "blockers": [],
                                },
                            },
                        },
                    )
                ],
                "database.change-plan": [
                    ("success", {"status": "planned"})
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                database,
                "DB_PATH",
                Path(temp_dir) / "harness.sqlite",
            ):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                ).run(
                    title="数据库能力主流程",
                    demand_text="请修改测试配置，只生成只读证据与变更计划。",
                    execution_mode="readonly",
                    requirement_governance="legacy",
                    database_inspect=self._database_input(),
                    database_change={
                        "subject": "配置变更评估",
                        "operation": "update",
                        "reason": "仅生成计划",
                    },
                )
                artifacts = database.get_artifacts(result.run_id)

        routed = [
            request.capability
            for request in service.requests
            if request.capability.startswith("database.")
        ]
        self.assertEqual(
            ["database.inspect", "database.change-plan"],
            routed,
        )
        evidence = next(
            artifact
            for artifact in artifacts
            if artifact["kind"] == "database_capability_evidence_json"
        )
        public = json.loads(evidence["content"])
        self.assertEqual("success", public["status"])
        self.assertEqual(
            "his_test",
            public["results"]["database.inspect.preview"]["data"]["plan"][
                "selected_profile"
            ],
        )
        self.assertNotIn("database.change", routed)

    def test_runner_database_provider_block_is_stable_and_redacted(
        self,
    ) -> None:
        provider_secret = "provider token=must-not-enter-database-report"
        service = FakeCapabilityService(
            {
                "database.inspect": [
                    ("blocked", {"raw_provider_text": provider_secret})
                ]
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                database,
                "DB_PATH",
                Path(temp_dir) / "harness.sqlite",
            ):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                ).run(
                    title="数据库能力阻断",
                    demand_text="核验测试配置。",
                    execution_mode="readonly",
                    requirement_governance="legacy",
                    database_inspect=self._database_input(),
                )
                report = build_markdown_report(result.run_id)
                artifacts = database.get_artifacts(result.run_id)

        serialized = report + json.dumps(artifacts, ensure_ascii=False)
        self.assertEqual("blocked", result.status)
        self.assertIn("database_inspect_blocked", serialized)
        self.assertNotIn(provider_secret, serialized)

    def test_task_creates_candidate_without_promote_and_provider_failure_is_blocker(
        self,
    ) -> None:
        provider_secret = "provider raw token=should-not-leak"
        service = FakeCapabilityService(
            {
                "knowledge.candidate.create": [
                    (
                        "blocked",
                        {
                            "status": "candidate",
                            "raw_provider_text": provider_secret,
                        },
                    )
                ]
            }
        )

        result = CapabilityWorkflowOrchestrator(service).run_task_capabilities(
            routing_result=self._task_routing(),
            contract_ready=True,
            knowledge_candidate={"answer": "已验证结论"},
            knowledge_provenance={"run_id": 7},
        )

        self.assertEqual(("knowledge.candidate.create",), result.events)
        self.assertEqual(
            ("knowledge_candidate_create_blocked",),
            result.data["blockers"],
        )
        self.assertNotIn(provider_secret, json.dumps(result.data, ensure_ascii=False))
        self.assertNotIn(
            "knowledge.item.promote",
            {item.capability for item in service.requests},
        )

    def test_runner_candidate_blocker_is_stable_and_redacted_in_report(self) -> None:
        provider_secret = "provider token=must-not-enter-report"
        service = FakeCapabilityService(
            {
                "knowledge.candidate.create": [
                    ("failed", {"raw_provider_text": provider_secret})
                ]
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                database,
                "DB_PATH",
                Path(temp_dir) / "harness.sqlite",
            ):
                runner = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                )
                run_id = database.create_run(
                    "his_requirement_workflow",
                    "候选阻断报告",
                    "manual",
                    "只记录稳定阻断码",
                    0,
                    "mock",
                    "mock",
                )
                stage, blockers = runner._create_task_knowledge_candidate(
                    run_id,
                    routing_result=self._task_routing(),
                    enabled=True,
                    candidate_payload=self._knowledge_candidate(),
                )
                report = build_markdown_report(run_id)

        self.assertEqual(("blocked", "knowledge_candidate_blocked"), stage)
        self.assertEqual(("knowledge_candidate_create_blocked",), blockers)
        self.assertIn("knowledge_candidate_create_blocked", report)
        self.assertNotIn(provider_secret, report)
        self.assertEqual(
            ["knowledge.candidate.create"],
            [request.capability for request in service.requests],
        )

    def test_runner_routes_generated_diff_through_git_apply_local_only_after_bound_review(
        self,
    ) -> None:
        service = FakeCapabilityService()
        runner = object.__new__(RequirementWorkflowRunner)
        runner.capability_service = service
        project_path = "/tmp/his-project"
        final_diff = self._reviewed_diff()
        contract = self._review_contract()
        worktree = WorktreeExecutionResult(
            status="success",
            summary="worktree ready",
            final_diff=final_diff,
            allowed_paths=["src/view.vue"],
            manifest={},
        )
        diff_review = review_final_diff(
            contract=contract,
            project_path=project_path,
            final_diff=final_diff,
            verification_passed=True,
        )

        result = runner._route_worktree_local_apply(
            worktree,
            routing_result=self._task_routing(),
            contract_ready=True,
            project_path=project_path,
            allowed_paths=["src/view.vue"],
            verify_commands=["test -f src/view.vue"],
            review_contract=contract,
            diff_review=diff_review,
            acceptance_contract_result=None,
        )

        self.assertEqual("success", result.status)
        self.assertEqual("success", result.apply_to_project["status"])
        self.assertEqual(
            ["git.apply-local"],
            [request.capability for request in service.requests],
        )
        request = service.requests[0]
        self.assertEqual(
            list(contract.allowed_paths),
            request.input["allowed_paths"],
        )
        self.assertEqual(
            list(contract.verify_commands),
            request.input["verify_commands"],
        )

    def test_legacy_and_observe_keep_local_apply_on_the_existing_runner_path(
        self,
    ) -> None:
        for mode in ("legacy", "observe"):
            with self.subTest(mode=mode):
                service = CapabilityService(
                    CapabilityRuntime(CapabilityRegistry([])),
                    routing_mode=mode,
                )
                runner = object.__new__(RequirementWorkflowRunner)
                runner.capability_service = service
                worktree = WorktreeExecutionResult(
                    status="success",
                    summary="legacy local apply already completed",
                    final_diff=self._reviewed_diff(),
                    manifest={},
                )

                result = runner._route_worktree_local_apply(
                    worktree,
                    routing_result=self._task_routing(),
                    contract_ready=True,
                    project_path="/tmp/his-project",
                    allowed_paths=["src/view.vue"],
                    verify_commands=["test -f src/view.vue"],
                    review_contract=self._review_contract(),
                    diff_review=None,
                    acceptance_contract_result=None,
                )

                self.assertIs(worktree, result)
                self.assertFalse(runner._capability_mutations_enforced())
                self.assertEqual({}, result.apply_to_project)

    def test_runner_local_apply_uses_review_canonical_project_path_for_payload(
        self,
    ) -> None:
        for project_path in ("~/review-bound-repo", ".", ".."):
            with self.subTest(project_path=project_path):
                service = FakeCapabilityService()
                runner = object.__new__(RequirementWorkflowRunner)
                runner.capability_service = service
                canonical_project_path = str(
                    Path(project_path).expanduser().resolve()
                )
                if project_path.startswith("~"):
                    self.assertNotEqual(
                        canonical_project_path,
                        str(Path(project_path).resolve()),
                    )
                final_diff = self._reviewed_diff()
                contract = self._review_contract()
                diff_review = review_final_diff(
                    contract=contract,
                    project_path=project_path,
                    final_diff=final_diff,
                    verification_passed=True,
                )
                self.assertEqual(
                    canonical_project_path,
                    diff_review.project_path,
                )

                result = runner._route_worktree_local_apply(
                    WorktreeExecutionResult(
                        status="success",
                        summary="worktree ready",
                        final_diff=final_diff,
                        allowed_paths=["src/view.vue"],
                        manifest={},
                    ),
                    routing_result=self._task_routing(),
                    contract_ready=True,
                    project_path=project_path,
                    allowed_paths=["src/view.vue"],
                    verify_commands=["test -f src/view.vue"],
                    review_contract=contract,
                    diff_review=diff_review,
                    acceptance_contract_result=None,
                )

                self.assertEqual("success", result.status)
                self.assertEqual(1, len(service.requests))
                self.assertEqual(
                    canonical_project_path,
                    service.requests[0].input["project_path"],
                )

    def test_runner_local_apply_snapshots_drifting_final_diff_once(
        self,
    ) -> None:
        trusted_diff = self._reviewed_diff()
        changed_diff = trusted_diff.replace(
            "return paiBanList;",
            "return changedList;",
        )

        class DriftingWorktreeResult:
            def __init__(self) -> None:
                self.status = "success"
                self.summary = "drifting final diff"
                self.manifest = {}
                self.apply_to_project = None
                self.final_diff_reads = 0

            @property
            def final_diff(self) -> str:
                self.final_diff_reads += 1
                if self.final_diff_reads <= 2:
                    return trusted_diff
                return changed_diff

        service = FakeCapabilityService()
        runner = object.__new__(RequirementWorkflowRunner)
        runner.capability_service = service
        contract = self._review_contract()
        review = review_final_diff(
            contract=contract,
            project_path="/tmp/his-project",
            final_diff=trusted_diff,
            verification_passed=True,
        )
        result = DriftingWorktreeResult()

        runner._route_worktree_local_apply(
            result,
            routing_result=self._task_routing(),
            contract_ready=True,
            project_path="/tmp/his-project",
            allowed_paths=list(contract.allowed_paths),
            verify_commands=list(contract.verify_commands),
            review_contract=contract,
            diff_review=review,
            acceptance_contract_result=None,
        )

        self.assertEqual(1, len(service.requests))
        self.assertEqual(
            trusted_diff,
            service.requests[0].input["expected_diff"],
        )
        self.assertEqual(1, result.final_diff_reads)
        self.assertEqual(
            hashlib.sha256(trusted_diff.encode("utf-8")).hexdigest(),
            review.final_diff_digest,
        )

    def test_runner_local_apply_uses_snapshot_when_result_mutates_after_review(
        self,
    ) -> None:
        service = FakeCapabilityService()
        runner = object.__new__(RequirementWorkflowRunner)
        runner.capability_service = service
        trusted_diff = self._reviewed_diff()
        changed_diff = trusted_diff.replace(
            "return paiBanList;",
            "return changedList;",
        )
        contract = self._review_contract()
        review = review_final_diff(
            contract=contract,
            project_path="/tmp/his-project",
            final_diff=trusted_diff,
            verification_passed=True,
        )
        result = WorktreeExecutionResult(
            status="success",
            summary="mutable final diff",
            final_diff=trusted_diff,
            manifest={},
        )

        def mutate_after_review(**kwargs):
            recomputed_review = review_final_diff(**kwargs)
            result.final_diff = changed_diff
            return recomputed_review

        with patch(
            "app.harness.review_final_diff",
            side_effect=mutate_after_review,
        ):
            runner._route_worktree_local_apply(
                result,
                routing_result=self._task_routing(),
                contract_ready=True,
                project_path="/tmp/his-project",
                allowed_paths=list(contract.allowed_paths),
                verify_commands=list(contract.verify_commands),
                review_contract=contract,
                diff_review=review,
                acceptance_contract_result=None,
            )

        self.assertEqual(changed_diff, result.final_diff)
        self.assertEqual(1, len(service.requests))
        self.assertEqual(
            trusted_diff,
            service.requests[0].input["expected_diff"],
        )
        self.assertEqual(
            hashlib.sha256(trusted_diff.encode("utf-8")).hexdigest(),
            review.final_diff_digest,
        )

    def test_runner_local_apply_helper_requires_exact_bound_review(self) -> None:
        service = FakeCapabilityService()
        runner = object.__new__(RequirementWorkflowRunner)
        runner.capability_service = service
        kwargs = {
            "routing_result": self._task_routing(),
            "contract_ready": True,
            "project_path": "/tmp/his-project",
            "allowed_paths": ["src/view.vue"],
            "verify_commands": ["test -f src/view.vue"],
        }

        with self.assertRaises(TypeError):
            runner._route_worktree_local_apply(
                WorktreeExecutionResult(
                    status="success",
                    summary="missing review",
                    final_diff=self._reviewed_diff(),
                    manifest={},
                ),
                **kwargs,
            )
        self.assertEqual([], service.requests)

        forged = runner._route_worktree_local_apply(
            WorktreeExecutionResult(
                status="success",
                summary="forged review",
                final_diff=self._reviewed_diff(),
                manifest={},
            ),
            **kwargs,
            review_contract=self._review_contract(),
            diff_review=SimpleNamespace(
                status="pass",
                project_path="/tmp/his-project",
            ),
            acceptance_contract_result=None,
        )
        self.assertEqual("failed", forged.status)
        self.assertEqual([], service.requests)

    def test_runner_local_apply_rejects_review_for_different_bound_inputs(
        self,
    ) -> None:
        service = FakeCapabilityService()
        runner = object.__new__(RequirementWorkflowRunner)
        runner.capability_service = service
        contract = self._review_contract()
        review = review_final_diff(
            contract=contract,
            project_path="/tmp/his-project",
            final_diff=self._reviewed_diff(),
            verification_passed=True,
        )
        cases = (
            (
                "/tmp/his-project",
                ["src/view.vue"],
                self._reviewed_diff().replace(
                    "return paiBanList;",
                    "return changedList;",
                ),
            ),
            ("/tmp/other-project", ["src/view.vue"], self._reviewed_diff()),
            ("/tmp/his-project", ["src/other.vue"], self._reviewed_diff()),
        )
        for project_path, allowed_paths, final_diff in cases:
            with self.subTest(
                project_path=project_path,
                allowed_paths=allowed_paths,
            ):
                result = runner._route_worktree_local_apply(
                    WorktreeExecutionResult(
                        status="success",
                        summary="review binding changed",
                        final_diff=final_diff,
                        manifest={},
                    ),
                    routing_result=self._task_routing(),
                    contract_ready=True,
                    project_path=project_path,
                    allowed_paths=allowed_paths,
                    verify_commands=["test -f src/view.vue"],
                    review_contract=contract,
                    diff_review=review,
                    acceptance_contract_result=None,
                )
                self.assertEqual("failed", result.status)
        self.assertEqual([], service.requests)

    def test_runner_local_apply_rejects_unbound_verify_commands(
        self,
    ) -> None:
        project_path = "/tmp/his-project"
        final_diff = self._reviewed_diff()
        contract = replace(
            self._review_contract(),
            verify_commands=(
                "test -f src/view.vue",
                "python3 -m unittest tests.test_view",
            ),
        )
        review = review_final_diff(
            contract=contract,
            project_path=project_path,
            final_diff=final_diff,
            verification_passed=True,
        )
        command_variants = {
            "drop": ["test -f src/view.vue"],
            "replace": ["true"],
            "reorder": list(reversed(contract.verify_commands)),
        }

        for variant, verify_commands in command_variants.items():
            with self.subTest(variant=variant):
                service = FakeCapabilityService()
                runner = object.__new__(RequirementWorkflowRunner)
                runner.capability_service = service
                result = runner._route_worktree_local_apply(
                    WorktreeExecutionResult(
                        status="success",
                        summary="unbound verify commands",
                        final_diff=final_diff,
                        manifest={},
                    ),
                    routing_result=self._task_routing(),
                    contract_ready=True,
                    project_path=project_path,
                    allowed_paths=list(contract.allowed_paths),
                    verify_commands=verify_commands,
                    review_contract=contract,
                    diff_review=review,
                    acceptance_contract_result=None,
                )

                self.assertEqual([], service.requests)
                self.assertEqual("failed", result.status)

    def test_runner_local_apply_requires_explicit_contract_apply_authorization(
        self,
    ) -> None:
        project_path = "/tmp/his-project"
        final_diff = self._reviewed_diff()
        for apply_to_project in (False, None, 0):
            with self.subTest(apply_to_project=apply_to_project):
                contract = replace(
                    self._review_contract(),
                    apply_to_project=apply_to_project,
                )
                review = review_final_diff(
                    contract=contract,
                    project_path=project_path,
                    final_diff=final_diff,
                    verification_passed=True,
                )
                service = FakeCapabilityService()
                runner = object.__new__(RequirementWorkflowRunner)
                runner.capability_service = service
                result = runner._route_worktree_local_apply(
                    WorktreeExecutionResult(
                        status="success",
                        summary="contract does not authorize apply",
                        final_diff=final_diff,
                        manifest={},
                    ),
                    routing_result=self._task_routing(),
                    contract_ready=True,
                    project_path=project_path,
                    allowed_paths=list(contract.allowed_paths),
                    verify_commands=list(contract.verify_commands),
                    review_contract=contract,
                    diff_review=review,
                    acceptance_contract_result=None,
                )

                self.assertEqual([], service.requests)
                self.assertEqual("failed", result.status)

    def test_runner_local_apply_requires_exact_true_ready_signal(
        self,
    ) -> None:
        project_path = "/tmp/his-project"
        final_diff = self._reviewed_diff()
        contract = self._review_contract()
        review = review_final_diff(
            contract=contract,
            project_path=project_path,
            final_diff=final_diff,
            verification_passed=True,
        )
        for contract_ready in (False, None, 0, 1, "ready"):
            with self.subTest(contract_ready=contract_ready):
                service = FakeCapabilityService()
                runner = object.__new__(RequirementWorkflowRunner)
                runner.capability_service = service
                result = runner._route_worktree_local_apply(
                    WorktreeExecutionResult(
                        status="success",
                        summary="ready signal is not exact true",
                        final_diff=final_diff,
                        manifest={},
                    ),
                    routing_result=self._task_routing(),
                    contract_ready=contract_ready,
                    project_path=project_path,
                    allowed_paths=list(contract.allowed_paths),
                    verify_commands=list(contract.verify_commands),
                    review_contract=contract,
                    diff_review=review,
                    acceptance_contract_result=None,
                )

                self.assertEqual([], service.requests)
                self.assertEqual("failed", result.status)

    def test_runner_local_apply_rejects_tampered_acceptance_result(
        self,
    ) -> None:
        contract, acceptance_result = ready_ordering_review_contract()
        contract = replace(contract, apply_to_project=True)
        project_path = "/tmp/his-project"
        final_diff = ordering_diff(includes_parent_sort_evidence=True)
        review = review_final_diff(
            contract=contract,
            project_path=project_path,
            final_diff=final_diff,
            verification_passed=True,
            acceptance_contract_result=acceptance_result,
        )
        tampered_results = {
            "contract_id": replace(
                acceptance_result,
                contract_id=acceptance_result.contract_id + "-forged",
            ),
            "checks": replace(
                acceptance_result,
                checks={
                    **acceptance_result.checks,
                    "same_sequence_uses_source_index": "forged",
                },
            ),
            "verify_command": replace(
                acceptance_result,
                verify_command=acceptance_result.verify_command + " --forged",
            ),
        }

        for field_name, tampered_result in tampered_results.items():
            with self.subTest(field_name=field_name):
                service = FakeCapabilityService()
                runner = object.__new__(RequirementWorkflowRunner)
                runner.capability_service = service
                result = runner._route_worktree_local_apply(
                    WorktreeExecutionResult(
                        status="success",
                        summary="tampered acceptance result",
                        final_diff=final_diff,
                        manifest={},
                    ),
                    routing_result=self._task_routing(),
                    contract_ready=True,
                    project_path=project_path,
                    allowed_paths=list(contract.allowed_paths),
                    verify_commands=list(contract.verify_commands),
                    review_contract=contract,
                    diff_review=review,
                    acceptance_contract_result=tampered_result,
                )

                self.assertEqual("failed", result.status)
                self.assertEqual([], service.requests)

    def test_runner_local_apply_rejects_acceptance_result_without_contract(
        self,
    ) -> None:
        service = FakeCapabilityService()
        runner = object.__new__(RequirementWorkflowRunner)
        runner.capability_service = service
        contract = self._review_contract()
        _, acceptance_result = ready_ordering_review_contract()
        final_diff = self._reviewed_diff()
        review = review_final_diff(
            contract=contract,
            project_path="/tmp/his-project",
            final_diff=final_diff,
            verification_passed=True,
        )

        result = runner._route_worktree_local_apply(
            WorktreeExecutionResult(
                status="success",
                summary="unexpected acceptance result",
                final_diff=final_diff,
                manifest={},
            ),
            routing_result=self._task_routing(),
            contract_ready=True,
            project_path="/tmp/his-project",
            allowed_paths=list(contract.allowed_paths),
            verify_commands=list(contract.verify_commands),
            review_contract=contract,
            diff_review=review,
            acceptance_contract_result=acceptance_result,
        )

        self.assertEqual("failed", result.status)
        self.assertEqual([], service.requests)

    def test_runner_local_apply_rejects_review_from_previous_exact_contract(
        self,
    ) -> None:
        contract_a, acceptance_result_a = ready_ordering_review_contract()
        contract_a = replace(contract_a, apply_to_project=True)
        project_path = "/tmp/his-project"
        final_diff = ordering_diff(includes_parent_sort_evidence=True)
        review_a = review_final_diff(
            contract=contract_a,
            project_path=project_path,
            final_diff=final_diff,
            verification_passed=True,
            acceptance_contract_result=acceptance_result_a,
        )
        result_variants = {
            "contract_id": replace(
                acceptance_result_a,
                contract_id=acceptance_result_a.contract_id + "-b",
            ),
            "checks": replace(
                acceptance_result_a,
                checks={
                    **acceptance_result_a.checks,
                    "same_sequence_uses_source_index": "contract-b",
                },
            ),
            "verify_command": replace(
                acceptance_result_a,
                verify_command=acceptance_result_a.verify_command + " --contract-b",
            ),
            "source_order": replace(
                acceptance_result_a,
                source_order=(*acceptance_result_a.source_order, "contract-b"),
            ),
        }

        for field_name, acceptance_result_b in result_variants.items():
            with self.subTest(field_name=field_name):
                contract_b = replace(
                    contract_a,
                    verify_commands=(acceptance_result_b.verify_command,),
                    acceptance_contract=acceptance_result_b.to_dict(),
                )
                self.assertEqual(
                    contract_b.acceptance_contract,
                    acceptance_result_b.to_dict(),
                )
                service = FakeCapabilityService()
                runner = object.__new__(RequirementWorkflowRunner)
                runner.capability_service = service
                result = runner._route_worktree_local_apply(
                    WorktreeExecutionResult(
                        status="success",
                        summary="review belongs to contract A",
                        final_diff=final_diff,
                        manifest={},
                    ),
                    routing_result=self._task_routing(),
                    contract_ready=True,
                    project_path=project_path,
                    allowed_paths=list(contract_b.allowed_paths),
                    verify_commands=list(contract_b.verify_commands),
                    review_contract=contract_b,
                    diff_review=review_a,
                    acceptance_contract_result=acceptance_result_b,
                )

                self.assertEqual([], service.requests)
                self.assertEqual("failed", result.status)

    def test_runner_local_apply_rejects_review_after_nested_contract_mutation(
        self,
    ) -> None:
        contract, acceptance_result_a = ready_ordering_review_contract()
        contract = replace(contract, apply_to_project=True)
        project_path = "/tmp/his-project"
        final_diff = ordering_diff(includes_parent_sort_evidence=True)
        review_a = review_final_diff(
            contract=contract,
            project_path=project_path,
            final_diff=final_diff,
            verification_passed=True,
            acceptance_contract_result=acceptance_result_a,
        )
        acceptance_result_b = replace(
            acceptance_result_a,
            contract_id=acceptance_result_a.contract_id + "-mutated",
        )
        contract.acceptance_contract.clear()
        contract.acceptance_contract.update(acceptance_result_b.to_dict())
        self.assertEqual(
            contract.acceptance_contract,
            acceptance_result_b.to_dict(),
        )
        service = FakeCapabilityService()
        runner = object.__new__(RequirementWorkflowRunner)
        runner.capability_service = service

        result = runner._route_worktree_local_apply(
            WorktreeExecutionResult(
                status="success",
                summary="nested contract mutated after review",
                final_diff=final_diff,
                manifest={},
            ),
            routing_result=self._task_routing(),
            contract_ready=True,
            project_path=project_path,
            allowed_paths=list(contract.allowed_paths),
            verify_commands=list(contract.verify_commands),
            review_contract=contract,
            diff_review=review_a,
            acceptance_contract_result=acceptance_result_b,
        )

        self.assertEqual([], service.requests)
        self.assertEqual("failed", result.status)

    def _run_review_gated_execution_mode(
        self,
        *,
        execution_mode: str,
        review_outcome: str,
    ) -> tuple[int, int]:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir) / "df-web-test"
            (project_path / "src").mkdir(parents=True)
            (project_path / "src/view.vue").write_text(
                "<template />\n",
                encoding="utf-8",
            )
            technical_decision = ready_technical_decision(project_path)
            technical_decision.field_provenance = {
                "target_ui_found": True,
                "target_ui_paths": ["src/view.vue"],
                "evidence": [
                    {"project": project_path.name, "path": "src/view.vue", "reason": "测试用源码入口证据"}
                ],
            }
            requirement_evidence_file = write_ready_change_evidence(
                Path(temp_dir), project_path.name, ("src/view.vue",)
            )
            governance = ready_governance()
            single_pass = replace(
                ready_single_pass(),
                repositories=(
                    {
                        "name": project_path.name,
                        "path": str(project_path),
                        "role": "frontend",
                    },
                ),
            )
            governance_answer = (
                "success",
                {
                    "governance": governance.to_dict(),
                    "single_pass_change_contract": single_pass.to_dict(),
                },
            )
            service = FakeCapabilityService(
                {
                    "requirement.govern": [
                        governance_answer,
                        governance_answer,
                        governance_answer,
                    ]
                },
                mode="enforce",
            )

            def review_side_effect(**kwargs):
                if review_outcome == "absent":
                    return None
                if review_outcome == "blocked":
                    return DiffReview(
                        schema_version="1.0-diff-review",
                        status="blocked",
                        review_contract_digest="",
                        findings=("review blocked",),
                    )
                return review_final_diff(**kwargs)

            patch_readiness = SimpleNamespace(
                can_patch=True,
                summary="ready",
                to_dict=lambda: {"status": "ready"},
                to_json=lambda: "{}",
                to_markdown=lambda: "ready",
            )
            with (
                patch.object(
                    database,
                    "DB_PATH",
                    Path(temp_dir) / "harness.sqlite",
                ),
                patch(
                    "app.harness.build_requirement_governance_outputs",
                    return_value=(governance, single_pass, ""),
                ),
                patch(
                    "app.harness.build_technical_decision",
                    return_value=technical_decision,
                ),
                patch(
                    "app.harness.build_requirement_contract",
                    return_value=self._review_contract(),
                ),
                patch(
                    "app.harness.evaluate_patch_readiness",
                    return_value=patch_readiness,
                ),
                patch.object(
                    RequirementWorkflowRunner,
                    "_run_worktree_execution",
                    return_value=WorktreeExecutionResult(
                        status="success",
                        summary="受控执行",
                        allowed_paths=["src/view.vue"],
                        final_diff=self._reviewed_diff(),
                        manifest={},
                    ),
                ),
                patch(
                    "app.harness.review_final_diff",
                    side_effect=review_side_effect,
                ) as review,
            ):
                runner = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                )
                options = dict(
                    title="受信 diff 审查",
                    demand_text=TEST_CHANGE_REQUIREMENT_TEXT,
                    # The mutating execution mode is intentionally paired
                    # with an explicit task-intent receipt.  The runner must
                    # never infer write authority from the mode alone.
                    routing_result=self._task_routing(),
                    project_path=project_path,
                    allowed_paths=["src/view.vue"],
                    verify_commands=["test -f src/view.vue"],
                    execution_mode=execution_mode,
                    requirement_governance="enforce",
                    worktree_dir=Path(temp_dir) / "worktrees",
                    requirement_evidence_file=requirement_evidence_file,
                    # This helper explicitly exercises the review-gated local
                    # apply path for all three execution modes.
                    apply_approved_diff=True,
                )
                first = runner.run(**options)
                confirmation = json.loads(
                    next(
                        item["content"]
                        for item in reversed(database.get_artifacts(first.run_id))
                        if item["kind"] == "pre_change_confirmation_json"
                    )
                )
                second = runner.run(
                    **options,
                    pre_change_confirmation=confirmation["confirmation_token"],
                )
                if second.status == "blocked":
                    final_confirmation = json.loads(
                        next(
                            item["content"]
                            for item in reversed(database.get_artifacts(second.run_id))
                            if item["kind"] == "pre_change_confirmation_json"
                        )
                    )
                    if final_confirmation["confirmation_token"] != confirmation["confirmation_token"]:
                        runner.run(
                            **options,
                            pre_change_confirmation=final_confirmation["confirmation_token"],
                        )

        apply_count = sum(
            request.capability == "git.apply-local"
            for request in service.requests
        )
        return apply_count, review.call_count

    def test_worktree_and_single_demand_require_passing_diff_review_before_apply(
        self,
    ) -> None:
        for execution_mode in ("worktree", "single-demand-trial"):
            for review_outcome, expected_apply in (
                ("absent", 0),
                ("blocked", 0),
                ("pass", 1),
            ):
                with self.subTest(
                    execution_mode=execution_mode,
                    review_outcome=review_outcome,
                ):
                    apply_count, review_calls = (
                        self._run_review_gated_execution_mode(
                            execution_mode=execution_mode,
                            review_outcome=review_outcome,
                        )
                    )
                    self.assertEqual(expected_apply, apply_count)
                    self.assertGreaterEqual(review_calls, 1)

    def test_core_closure_passes_existing_bound_review_to_local_apply(self) -> None:
        apply_count, review_calls = self._run_review_gated_execution_mode(
            execution_mode="core-closure-trial",
            review_outcome="pass",
        )

        self.assertEqual(1, apply_count)
        self.assertGreaterEqual(review_calls, 2)

    def _fullstack_options(
        self,
        *,
        authority_mode: str,
        authoritative_contract: dict | None = None,
    ) -> FullstackExecutionOptions:
        return FullstackExecutionOptions(
            run_id=0,
            demand_text="显式 authority mode 合同",
            report_markdown="",
            project_root="/tmp/fullstack-authority-mode",
            authority_mode=authority_mode,
            authoritative_contract=authoritative_contract,
        )

    def test_fullstack_interfaces_require_explicit_authority_mode(
        self,
    ) -> None:
        runner = object.__new__(RequirementWorkflowRunner)
        with (
            patch("app.fullstack_executor.preflight_targets") as preflight,
            patch.object(FullstackWorktreeExecutor, "execute") as execute,
        ):
            with self.assertRaises(TypeError):
                FullstackExecutionOptions(
                    run_id=0,
                    demand_text="漏传 mode",
                    report_markdown="",
                    project_root="/tmp/fullstack-authority-mode",
                )
            with self.assertRaises(TypeError):
                runner._run_fullstack_execution(
                    run_id=0,
                    demand_text="漏传 mode",
                    project_root="/tmp/fullstack-authority-mode",
                    technical_decision=ready_technical_decision(
                        Path("/tmp/fullstack-authority-mode/df-web-test")
                    ),
                    verify_commands=[],
                    worktree_dir="/tmp/fullstack-worktrees",
                )

        preflight.assert_not_called()
        execute.assert_not_called()

    def test_fullstack_enforce_without_contract_blocks_before_preflight(
        self,
    ) -> None:
        with patch("app.fullstack_executor.preflight_targets") as preflight:
            result = FullstackWorktreeExecutor().execute(
                self._fullstack_options(authority_mode="enforce")
            )

        self.assertEqual("failed", result.status)
        self.assertIn("enforce", result.summary)
        preflight.assert_not_called()

    def test_fullstack_legacy_without_contract_keeps_preflight_compatibility(
        self,
    ) -> None:
        with patch(
            "app.fullstack_executor.preflight_targets",
            return_value="legacy preflight marker",
        ) as preflight:
            result = FullstackWorktreeExecutor().execute(
                self._fullstack_options(authority_mode="legacy")
            )

        self.assertEqual("failed", result.status)
        self.assertEqual("legacy preflight marker", result.summary)
        preflight.assert_called_once()

    def test_fullstack_unknown_authority_mode_blocks_before_preflight(
        self,
    ) -> None:
        with patch("app.fullstack_executor.preflight_targets") as preflight:
            result = FullstackWorktreeExecutor().execute(
                self._fullstack_options(authority_mode="observe")
            )

        self.assertEqual("failed", result.status)
        self.assertIn("authority mode", result.summary)
        preflight.assert_not_called()

    def test_fullstack_legacy_rejects_contract_before_preflight(
        self,
    ) -> None:
        with patch("app.fullstack_executor.preflight_targets") as preflight:
            result = FullstackWorktreeExecutor().execute(
                self._fullstack_options(
                    authority_mode="legacy",
                    authoritative_contract={"repositories": []},
                )
            )

        self.assertEqual("failed", result.status)
        self.assertIn("legacy", result.summary)
        preflight.assert_not_called()

    def _run_fullstack_contract_case(
        self,
        *,
        capability_allowed_paths: tuple[str, ...],
        capability_verify_commands: tuple[str, ...],
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            project_path = project_root / "df-web-zhuyuansf"
            project_path.mkdir()
            target_path = (
                "src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue"
            )
            legacy_allowed_paths = (target_path, "src/legacy.vue")
            legacy_verify_commands = (
                f"test -f {target_path}",
                "test -f src/legacy.vue",
            )
            technical_decision = ready_technical_decision(project_path)
            technical_decision.field_provenance = {
                "target_ui_found": True,
                "target_ui_paths": list(legacy_allowed_paths),
                "evidence": [
                    {"project": project_path.name, "path": path, "reason": "测试用源码入口证据"}
                    for path in legacy_allowed_paths
                ],
            }
            technical_decision.recommended_allowed_paths = list(
                legacy_allowed_paths
            )
            technical_decision.recommended_verify_commands = list(
                legacy_verify_commands
            )
            requirement_evidence_file = write_ready_change_evidence(
                project_root, project_path.name, legacy_allowed_paths
            )
            repository = {
                "name": project_path.name,
                "path": str(project_path),
                "role": "frontend",
            }
            legacy_contract = replace(
                ready_single_pass(),
                repositories=(repository,),
                allowed_paths=legacy_allowed_paths,
                verify_commands=legacy_verify_commands,
            )
            capability_contract = replace(
                legacy_contract,
                allowed_paths=capability_allowed_paths,
                verify_commands=capability_verify_commands,
            )
            service = FakeCapabilityService(
                {
                    "requirement.govern": [
                        (
                            "success",
                            {
                                "governance": ready_governance().to_dict(),
                                "single_pass_change_contract": capability_contract.to_dict(),
                            },
                        ),
                        (
                            "success",
                            {
                                "governance": ready_governance().to_dict(),
                                "single_pass_change_contract": capability_contract.to_dict(),
                            },
                        ),
                        (
                            "success",
                            {
                                "governance": ready_governance().to_dict(),
                                "single_pass_change_contract": capability_contract.to_dict(),
                            },
                        ),
                        (
                            "success",
                            {
                                "governance": ready_governance().to_dict(),
                                "single_pass_change_contract": capability_contract.to_dict(),
                            },
                        ),
                    ]
                }
            )
            with (
                patch.object(
                    database,
                    "DB_PATH",
                    project_root / "harness.sqlite",
                ),
                patch(
                    "app.harness.build_requirement_governance_outputs",
                    return_value=(
                        ready_governance(),
                        legacy_contract,
                        "",
                    ),
                ),
                patch(
                    "app.harness.build_technical_decision",
                    return_value=technical_decision,
                ),
                patch.object(
                    RequirementWorkflowRunner,
                    "_run_fullstack_execution",
                    return_value=FullstackExecutionResult(
                        status="success",
                        summary="受控执行",
                    ),
                ) as fullstack,
            ):
                runner = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                )
                with patch.object(
                    runner.evaluator,
                    "evaluate",
                    return_value=EvaluationResult(
                        status="pass",
                        summary="pass",
                    ),
                ):
                    options = dict(
                        title="Fullstack 合同权威性",
                        demand_text=TEST_CHANGE_REQUIREMENT_TEXT,
                        project_path=project_path,
                        project_root=project_root,
                        allowed_paths=list(legacy_allowed_paths),
                        verify_commands=list(legacy_verify_commands),
                        execution_mode="fullstack-worktree",
                        requirement_governance="observe",
                        worktree_dir=project_root / "worktrees",
                        requirement_evidence_file=requirement_evidence_file,
                    )
                    first = runner.run(**options)
                    confirmation = json.loads(
                        next(
                            item["content"]
                            for item in reversed(database.get_artifacts(first.run_id))
                            if item["kind"] == "pre_change_confirmation_json"
                        )
                    )
                    result = runner.run(
                        **options,
                        pre_change_confirmation=confirmation["confirmation_token"],
                    )
                call_kwargs = (
                    dict(fullstack.call_args.kwargs)
                    if fullstack.call_args is not None
                    else {}
                )
        return {
            "status": result.status,
            "fullstack_calls": fullstack.call_count,
            "technical_decision": call_kwargs.get("technical_decision"),
            "verify_commands": call_kwargs.get("verify_commands"),
            "authoritative_contract": call_kwargs.get(
                "authoritative_contract"
            ),
            "project_root": str(project_root),
        }

    def _run_core_contract_case(
        self,
        *,
        capability_allowed_paths: tuple[str, ...],
        capability_verify_commands: tuple[str, ...],
        capability_mode: str = "enforce",
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir) / "df-web-test"
            (project_path / "src").mkdir(parents=True)
            for filename in ("view.vue", "legacy.vue"):
                (project_path / "src" / filename).write_text(
                    "<template />\n",
                    encoding="utf-8",
                )
            legacy_allowed_paths = ("src/view.vue", "src/legacy.vue")
            legacy_verify_commands = (
                "test -f src/view.vue",
                "test -f src/legacy.vue",
            )
            technical_decision = ready_technical_decision(project_path)
            technical_decision.field_provenance = {
                "target_ui_found": True,
                "target_ui_paths": list(legacy_allowed_paths),
                "evidence": [
                    {"project": project_path.name, "path": path, "reason": "测试用源码入口证据"}
                    for path in legacy_allowed_paths
                ],
            }
            technical_decision.recommended_allowed_paths = list(legacy_allowed_paths)
            technical_decision.recommended_verify_commands = list(
                legacy_verify_commands
            )
            requirement_evidence_file = write_ready_change_evidence(
                Path(temp_dir), project_path.name, legacy_allowed_paths
            )
            capability_contract = replace(
                ready_single_pass(),
                repositories=(
                    {
                        "name": project_path.name,
                        "path": str(project_path),
                        "role": "frontend",
                    },
                ),
                allowed_paths=capability_allowed_paths,
                verify_commands=capability_verify_commands,
            )
            service = FakeCapabilityService(
                {
                    "requirement.govern": [
                        (
                            "success",
                            {
                                "governance": ready_governance().to_dict(),
                                "single_pass_change_contract": (
                                    capability_contract.to_dict()
                                ),
                            },
                        ),
                        (
                            "success",
                            {
                                "governance": ready_governance().to_dict(),
                                "single_pass_change_contract": capability_contract.to_dict(),
                            },
                        ),
                        (
                            "success",
                            {
                                "governance": ready_governance().to_dict(),
                                "single_pass_change_contract": capability_contract.to_dict(),
                            },
                        ),
                    ]
                },
                mode=capability_mode,
            )
            legacy_contract = RequirementContract(
                schema_version="1.0-requirement-contract",
                status="ready",
                title="合同权威性",
                demand_digest="显示只读字段",
                allowed_paths=legacy_allowed_paths,
                verify_commands=legacy_verify_commands,
                evidence_refs=({"path": "src/view.vue"},),
            )
            with (
                patch.object(
                    database,
                    "DB_PATH",
                    Path(temp_dir) / "harness.sqlite",
                ),
                patch(
                    "app.harness.build_requirement_governance_outputs",
                    return_value=(ready_governance(), ready_single_pass(), ""),
                ),
                patch(
                    "app.harness.build_technical_decision",
                    return_value=technical_decision,
                ),
                patch(
                    "app.harness.build_requirement_contract",
                    return_value=legacy_contract,
                ),
                patch.object(
                    RequirementWorkflowRunner,
                    "_run_worktree_execution",
                    return_value=WorktreeExecutionResult(
                        status="success",
                        summary="受控执行",
                        allowed_paths=list(capability_allowed_paths),
                    ),
                ) as worktree,
            ):
                runner = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                )
                options = dict(
                    title="合同权威性",
                    demand_text=TEST_CHANGE_REQUIREMENT_TEXT,
                    project_path=project_path,
                    allowed_paths=list(legacy_allowed_paths),
                    verify_commands=list(legacy_verify_commands),
                    execution_mode="core-closure-trial",
                    requirement_governance="observe",
                    worktree_dir=Path(temp_dir) / "worktrees",
                    requirement_evidence_file=requirement_evidence_file,
                )
                first = runner.run(**options)
                confirmation = json.loads(
                    next(
                        item["content"]
                        for item in reversed(database.get_artifacts(first.run_id))
                        if item["kind"] == "pre_change_confirmation_json"
                    )
                )
                result = runner.run(
                    **options,
                    pre_change_confirmation=confirmation["confirmation_token"],
                )
                if result.status == "blocked":
                    final_confirmation = json.loads(
                        next(
                            item["content"]
                            for item in reversed(database.get_artifacts(result.run_id))
                            if item["kind"] == "pre_change_confirmation_json"
                        )
                    )
                    if final_confirmation["confirmation_token"] != confirmation["confirmation_token"]:
                        result = runner.run(
                            **options,
                            pre_change_confirmation=final_confirmation["confirmation_token"],
                        )
                call_kwargs = (
                    dict(worktree.call_args.kwargs)
                    if worktree.call_args is not None
                    else {}
                )
        return {
            "status": result.status,
            "worktree_calls": worktree.call_count,
            "allowed_paths": call_kwargs.get("allowed_paths"),
            "verify_commands": call_kwargs.get("verify_commands"),
        }

    def test_yunxiao_read_request_is_read_only_preview(self) -> None:
        request = build_workitem_read_request(
            yunxiao_url="https://devops.example/workitem/DFHIS-1",
            demand_text="读取需求",
            include_comments=True,
        )

        self.assertEqual("workitem.read", request.capability)
        self.assertEqual("yunxiao", request.provider)
        self.assertEqual(MutationLevel.L1, request.mutation_level)
        self.assertEqual("preview", request.mode)
        self.assertFalse(request.authorization.explicit)
        self.assertEqual((), request.authorization.scope)
        self.assertNotIn("write", request.capability)

    def test_task_sequence_contract_is_explicit_and_fixed(self) -> None:
        self.assertEqual(
            (
                "intake",
                "provider_evidence",
                "calibration",
                "technical_decision",
                "ownership",
                "acceptance",
                "understanding",
                "governance",
                "single_pass_contract",
                "local_engineering",
                "verification",
                "knowledge_candidate",
                "audit",
            ),
            TASK_CAPABILITY_SEQUENCE,
        )

    def test_task_knowledge_candidate_uses_real_runtime_contract(self) -> None:
        plugin_root = PLUGIN_SOURCE_ROOT / "his-knowledge"
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_home = Path(temp_dir) / "knowledge"
            harness_db = Path(temp_dir) / "harness.sqlite"
            service = CapabilityService(
                CapabilityRuntime(
                    CapabilityRegistry.from_plugin_roots([plugin_root]),
                    environment_allowlist=("HIS_KNOWLEDGE_HOME",),
                ),
                routing_mode="enforce",
                capability_environments={
                    (capability, "his-knowledge"): {
                        "HIS_KNOWLEDGE_HOME": str(knowledge_home),
                    }
                    for capability in (
                        "knowledge.candidate.create",
                        "knowledge.candidate.review",
                        "knowledge.item.promote",
                    )
                },
            )
            with patch.object(database, "DB_PATH", harness_db):
                runner = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                )
                run_id = database.create_run(
                    "his_requirement_workflow",
                    "可复用知识候选",
                    "manual",
                    "只创建显式可复用知识候选",
                    0,
                    "mock",
                    "mock",
                )
                stage, blockers = runner._create_task_knowledge_candidate(
                    run_id,
                    routing_result=self._task_routing(),
                    enabled=True,
                    candidate_payload=self._knowledge_candidate(),
                )

            knowledge_db = knowledge_home / "knowledge.sqlite"
            connection = sqlite3.connect(knowledge_db)
            try:
                candidate_id, candidate_status = connection.execute(
                    "SELECT id, review_status FROM knowledge_candidates"
                ).fetchone()
                item_count = connection.execute(
                    "SELECT COUNT(*) FROM knowledge_items"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(("completed", "knowledge_candidate_created"), stage)
            self.assertEqual((), blockers)
            self.assertEqual("pending", candidate_status)
            self.assertEqual(0, item_count)

            review = service.route(
                CapabilityRequest(
                    request_id="review-runner-candidate",
                    capability="knowledge.candidate.review",
                    provider="his-knowledge",
                    mode="apply",
                    mutation_level=MutationLevel.L2,
                    authorization=CapabilityAuthorization(
                        True,
                        ("knowledge:candidate:review",),
                    ),
                    input={
                        "candidate_id": candidate_id,
                        "status": "approved",
                        "reviewer": "integration-reviewer",
                        "reason": "reusable and evidence-backed",
                    },
                    context={},
                )
            )
            promote = service.route(
                CapabilityRequest(
                    request_id="promote-runner-candidate",
                    capability="knowledge.item.promote",
                    provider="his-knowledge",
                    mode="apply",
                    mutation_level=MutationLevel.L2,
                    authorization=CapabilityAuthorization(
                        True,
                        ("knowledge:item:promote",),
                    ),
                    input={
                        "candidate_id": candidate_id,
                        "reviewer": "integration-reviewer",
                        "review_reason": "reusable and evidence-backed",
                    },
                    context={},
                )
            )
            connection = sqlite3.connect(knowledge_db)
            try:
                stable_key = connection.execute(
                    "SELECT stable_key FROM knowledge_items"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual("success", review.result["status"])
            self.assertEqual("success", promote.result["status"])
            self.assertEqual(
                self._knowledge_candidate()["stable_key"],
                stable_key,
            )

    def test_task_run_without_explicit_reusable_knowledge_skips_candidate(
        self,
    ) -> None:
        service = FakeCapabilityService()
        runner = object.__new__(RequirementWorkflowRunner)
        runner.capability_service = service

        stage, blockers = runner._create_task_knowledge_candidate(
            17,
            routing_result=self._task_routing(),
            enabled=True,
        )

        self.assertEqual(("skipped", "knowledge_write_skipped"), stage)
        self.assertEqual((), blockers)
        self.assertEqual([], service.requests)

    def test_governance_blocked_stops_before_local_engineering(self) -> None:
        service = FakeCapabilityService(
            {"requirement.govern": [("blocked", {"status": "blocked"})]}
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    database,
                    "DB_PATH",
                    Path(temp_dir) / "harness.sqlite",
                ),
                patch.object(
                    RequirementWorkflowRunner,
                    "_run_worktree_execution",
                ) as local_engineering,
            ):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                ).run(
                    title="治理阻断",
                    demand_text="医保收费规则存在歧义",
                    execution_mode="core-closure-trial",
                )

        self.assertEqual("blocked", result.status)
        local_engineering.assert_not_called()
        self.assertEqual(
            ["requirement.govern"],
            [request.capability for request in service.requests],
        )

    def test_capability_enforce_block_overrides_ready_local_governance_for_core_routes(
        self,
    ) -> None:
        for execution_mode in ("core-closure-trial", "auto-local"):
            with self.subTest(execution_mode=execution_mode):
                service = FakeCapabilityService(
                    {"requirement.govern": [("blocked", {"status": "blocked"})]},
                    mode="enforce",
                )
                with tempfile.TemporaryDirectory() as temp_dir:
                    project_path = Path(temp_dir) / "df-web-test"
                    (project_path / "src").mkdir(parents=True)
                    (project_path / "src/view.vue").write_text(
                        "<template />\n",
                        encoding="utf-8",
                    )
                    with (
                        patch.object(
                            database,
                            "DB_PATH",
                            Path(temp_dir) / "harness.sqlite",
                        ),
                        patch(
                            "app.harness.build_requirement_governance_outputs",
                            return_value=(ready_governance(), ready_single_pass(), ""),
                        ),
                        patch(
                            "app.harness.build_technical_decision",
                            return_value=ready_technical_decision(project_path),
                        ),
                        patch.object(
                            RequirementWorkflowRunner,
                            "_run_core_closure_trial",
                            return_value=WorkflowResult(
                                run_id=0,
                                status="unexpected_core_entry",
                                evaluation_status="unexpected_core_entry",
                                markdown_report="",
                                json_payload="{}",
                            ),
                        ) as core_closure,
                        patch.object(
                            RequirementWorkflowRunner,
                            "_run_worktree_execution",
                            return_value=WorktreeExecutionResult(
                                status="success",
                                summary="unexpected execution",
                                allowed_paths=["src/view.vue"],
                            ),
                        ) as worktree,
                    ):
                        result = RequirementWorkflowRunner(
                            MockLLMClient(),
                            allow_mock=True,
                            capability_service=service,
                        ).run(
                            title="能力治理阻断",
                            demand_text="显示只读字段",
                            project_path=project_path,
                            allowed_paths=["src/view.vue"],
                            verify_commands=["test -f src/view.vue"],
                            execution_mode=execution_mode,
                            requirement_governance="observe",
                        )

                self.assertEqual("blocked", result.status)
                core_closure.assert_not_called()
                worktree.assert_not_called()

    def test_capability_enforce_rejects_success_without_valid_governance_contract(
        self,
    ) -> None:
        service = FakeCapabilityService(
            {"requirement.govern": [("success", {})]},
            mode="enforce",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir) / "df-web-test"
            (project_path / "src").mkdir(parents=True)
            (project_path / "src/view.vue").write_text(
                "<template />\n",
                encoding="utf-8",
            )
            with (
                patch.object(
                    database,
                    "DB_PATH",
                    Path(temp_dir) / "harness.sqlite",
                ),
                patch(
                    "app.harness.build_requirement_governance_outputs",
                    return_value=(ready_governance(), ready_single_pass(), ""),
                ),
                patch(
                    "app.harness.build_technical_decision",
                    return_value=ready_technical_decision(project_path),
                ),
                patch.object(
                    RequirementWorkflowRunner,
                    "_run_core_closure_trial",
                    return_value=WorkflowResult(
                        run_id=0,
                        status="unexpected_core_entry",
                        evaluation_status="unexpected_core_entry",
                        markdown_report="",
                        json_payload="{}",
                    ),
                ) as core_closure,
                patch.object(
                    RequirementWorkflowRunner,
                    "_run_worktree_execution",
                    return_value=WorktreeExecutionResult(
                        status="success",
                        summary="unexpected execution",
                        allowed_paths=["src/view.vue"],
                    ),
                ) as worktree,
            ):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                ).run(
                    title="无效能力治理结果",
                    demand_text="显示只读字段",
                    project_path=project_path,
                    allowed_paths=["src/view.vue"],
                    verify_commands=["test -f src/view.vue"],
                    execution_mode="core-closure-trial",
                    requirement_governance="observe",
                )

        self.assertEqual("blocked", result.status)
        core_closure.assert_not_called()
        worktree.assert_not_called()

    def test_enforce_contract_is_authoritative_but_cannot_expand_legacy_contract(
        self,
    ) -> None:
        unsafe = self._run_core_contract_case(
            capability_allowed_paths=("src/capability.vue",),
            capability_verify_commands=("test -f src/view.vue",),
        )
        safe = self._run_core_contract_case(
            capability_allowed_paths=("src/view.vue",),
            capability_verify_commands=("test -f src/view.vue",),
        )

        self.assertEqual(
            {
                "unsafe_status": "blocked",
                "unsafe_worktree_calls": 0,
                "safe_worktree_calls": 1,
                "safe_allowed_paths": ["src/view.vue"],
                "safe_verify_commands": ["test -f src/view.vue"],
            },
            {
                "unsafe_status": unsafe["status"],
                "unsafe_worktree_calls": unsafe["worktree_calls"],
                "safe_worktree_calls": safe["worktree_calls"],
                "safe_allowed_paths": safe["allowed_paths"],
                "safe_verify_commands": safe["verify_commands"],
            },
        )

    def test_enforce_fullstack_incomplete_contract_blocks_before_executor(
        self,
    ) -> None:
        result = self._run_fullstack_contract_case(
            capability_allowed_paths=("src/legacy.vue",),
            capability_verify_commands=("test -f src/legacy.vue",),
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual(0, result["fullstack_calls"])

    def test_enforce_fullstack_passes_only_capability_bounded_inputs(
        self,
    ) -> None:
        target_path = "src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue"
        target_command = f"test -f {target_path}"
        result = self._run_fullstack_contract_case(
            capability_allowed_paths=(target_path,),
            capability_verify_commands=(target_command,),
        )
        technical_decision = result["technical_decision"]
        authoritative_contract = result["authoritative_contract"]

        self.assertEqual("success", result["status"])
        self.assertEqual(1, result["fullstack_calls"])
        self.assertEqual([target_path], technical_decision.recommended_allowed_paths)
        self.assertEqual(
            [target_command],
            technical_decision.recommended_verify_commands,
        )
        self.assertEqual([target_command], result["verify_commands"])
        targets = build_dfhis_31270_targets(
            FullstackExecutionOptions(
                run_id=0,
                demand_text="",
                report_markdown="",
                project_root=result["project_root"],
                authority_mode="enforce",
                technical_decision=technical_decision.to_dict(),
                verify_commands=result["verify_commands"],
                authoritative_contract=authoritative_contract,
            )
        )
        repositories = {
            (
                repository["name"],
                str(Path(repository["path"]).resolve()),
            )
            for repository in authoritative_contract["repositories"]
        }
        for target in targets:
            self.assertIn(
                (target.name, str(Path(target.project_path).resolve())),
                repositories,
            )
            self.assertLessEqual(
                set(target.allowed_paths),
                set(authoritative_contract["allowed_paths"]),
            )
            self.assertLessEqual(
                set(target.verify_commands),
                set(authoritative_contract["verify_commands"]),
            )

    def test_unexpected_routing_mode_cannot_use_valid_payload_to_enter_core(
        self,
    ) -> None:
        result = self._run_core_contract_case(
            capability_allowed_paths=("src/view.vue",),
            capability_verify_commands=("test -f src/view.vue",),
            capability_mode="unexpected-mode",
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual(0, result["worktree_calls"])

    def test_provider_summary_cannot_control_serialized_stage_reason(self) -> None:
        sentinel = "PROVIDER_CONTROLLED_LEDGER_REASON"
        service = RawCapabilityService(
            {
                "status": "blocked",
                "summary": sentinel,
                "data": {},
            },
            mode="enforce",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                database,
                "DB_PATH",
                Path(temp_dir) / "harness.sqlite",
            ):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                ).run(
                    title="账本原因固定",
                    demand_text="页面显示一个只读字段。",
                    execution_mode="readonly",
                    requirement_governance="observe",
                )

        self.assertNotIn(
            sentinel,
            json.dumps(
                result.orchestration_events,
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def test_question_mode_only_calls_knowledge_answer_and_stops_without_consent(self) -> None:
        service = FakeCapabilityService(
            {
                "knowledge.answer": [
                    (
                        "success",
                        {
                            "answer_status": "needs_live_evidence",
                            "suggested_capabilities": ["workitem.read"],
                        },
                    )
                ]
            }
        )
        result = CapabilityWorkflowOrchestrator(service).run_question(
            text="云效 DFHIS-1 当前是什么状态？",
        )

        self.assertEqual("needs_live_evidence", result.status)
        self.assertEqual(("knowledge.answer",), result.events)
        self.assertEqual(
            ["knowledge.answer"],
            [request.capability for request in service.requests],
        )

    def test_question_preserves_conflicted_answer_without_live_evidence(self) -> None:
        service = FakeCapabilityService(
            {
                "knowledge.answer": [
                    (
                        "success",
                        {
                            "answer_status": "conflicted",
                            "answer": "存在同级高权威知识冲突，不能选择其中一条。",
                            "evidence": [],
                            "freshness": "conflict",
                            "suggested_capabilities": [],
                        },
                    )
                ]
            }
        )

        result = CapabilityWorkflowOrchestrator(service).run_question(
            text="收费规则是什么？",
            allow_live_evidence=True,
        )

        self.assertEqual("conflicted", result.status)
        self.assertEqual("conflict", result.data["freshness"])
        self.assertEqual(("knowledge.answer",), result.events)
        self.assertEqual(
            ["knowledge.answer"],
            [request.capability for request in service.requests],
        )

    def test_question_evidence_is_allowlisted_and_sensitive_text_is_redacted(
        self,
    ) -> None:
        raw_result = {
            "status": "success",
            "data": {
                "answer_status": "answered",
                "answer": "已回答",
                "applicability": ["module=Harness"],
                "freshness": "current",
                "confidence_basis": ["reviewed_team_knowledge"],
                "evidence": [{"spoofed": True}],
            },
            "evidence": [
                {
                    "stable_key": "governance:test",
                    "title": "证据测试",
                    "authority": "reviewed_team_knowledge",
                    "version_label": "v1",
                    "source_refs": [
                        {
                            "claim_level": "governance",
                            "ref": "local:test",
                            "password": "must-not-escape",
                        }
                    ],
                    "excerpt": "token=must-not-escape",
                    "password": "must-not-escape",
                }
            ],
        }

        result = CapabilityWorkflowOrchestrator(
            RawCapabilityService(raw_result)
        ).run_question(text="问题")

        self.assertEqual("answered", result.status)
        evidence = result.data["evidence"][0]
        self.assertEqual(
            {
                "stable_key",
                "title",
                "authority",
                "version_label",
                "source_refs",
                "excerpt",
            },
            set(evidence),
        )
        self.assertEqual(
            {"claim_level": "governance", "ref": "local:test"},
            evidence["source_refs"][0],
        )
        self.assertEqual("token=[REDACTED]", evidence["excerpt"])
        self.assertNotIn(
            "must-not-escape",
            json.dumps(result.data, ensure_ascii=False),
        )

    def test_answered_question_fails_closed_without_complete_evidence_contract(
        self,
    ) -> None:
        valid_data = {
            "answer_status": "answered",
            "answer": "有证据的答案",
            "applicability": ["module=Harness"],
            "freshness": "current",
            "confidence_basis": ["reviewed_team_knowledge"],
        }
        valid_evidence = [
            {
                "stable_key": "governance:test",
                "title": "证据测试",
                "authority": "reviewed_team_knowledge",
                "version_label": "v1",
                "source_refs": [{"ref": "local:test"}],
                "excerpt": "经过审核的知识证据",
            }
        ]
        cases = {
            "empty_evidence": (valid_data, []),
            "empty_source_refs": (
                valid_data,
                [{**valid_evidence[0], "source_refs": []}],
            ),
            "empty_version": (
                valid_data,
                [{**valid_evidence[0], "version_label": ""}],
            ),
            "empty_answer": ({**valid_data, "answer": ""}, valid_evidence),
            "empty_applicability": (
                {**valid_data, "applicability": []},
                valid_evidence,
            ),
            "empty_freshness": (
                {**valid_data, "freshness": ""},
                valid_evidence,
            ),
            "empty_confidence": (
                {**valid_data, "confidence_basis": []},
                valid_evidence,
            ),
        }

        for name, (data, evidence) in cases.items():
            with self.subTest(name=name):
                result = CapabilityWorkflowOrchestrator(
                    RawCapabilityService(
                        {
                            "status": "success",
                            "data": data,
                            "evidence": evidence,
                        }
                    )
                ).run_question(text="问题")

                self.assertEqual("unsupported", result.status)
                self.assertEqual({}, result.data)

    def test_question_live_evidence_requires_consent_or_investigation_signal(self) -> None:
        for consent, investigation in ((True, False), (False, True)):
            with self.subTest(consent=consent, investigation=investigation):
                service = FakeCapabilityService(
                    {
                        "knowledge.answer": [
                            (
                                "success",
                                {
                                    "answer_status": "needs_live_evidence",
                                    "suggested_capabilities": ["workitem.read"],
                                },
                            )
                        ]
                    }
                )
                result = CapabilityWorkflowOrchestrator(service).run_question(
                    text="调查云效 DFHIS-1",
                    allow_live_evidence=consent,
                    investigation_request=investigation,
                )

                self.assertEqual(
                    ["knowledge.answer", "workitem.read"],
                    [request.capability for request in service.requests],
                )
                self.assertEqual(
                    ("knowledge.answer", "workitem.read"),
                    result.events,
                )
                self.assertFalse(
                    {"requirement.govern", "harness.task"}
                    & {request.capability for request in service.requests}
                )

    def test_question_database_investigation_requires_structured_input_before_route(
        self,
    ) -> None:
        service = FakeCapabilityService(
            {
                "knowledge.answer": [
                    (
                        "success",
                        {
                            "answer_status": "needs_live_evidence",
                            "suggested_capabilities": ["database.inspect"],
                        },
                    )
                ]
            }
        )

        result = CapabilityWorkflowOrchestrator(service).run_question(
            text="调查生产数据库中的门诊结算记录",
            allow_live_evidence=True,
        )

        self.assertEqual(
            ["knowledge.answer"],
            [request.capability for request in service.requests],
        )
        self.assertEqual(
            ("knowledge.answer", "database.inspect"),
            result.events,
        )
        database_evidence = result.data["live_evidence"]["database.inspect"]
        self.assertEqual("blocked", database_evidence["status"])
        self.assertEqual(
            "DATABASE_INSPECT_STRUCTURED_INPUT_REQUIRED",
            database_evidence["summary"],
        )
        self.assertEqual(
            {
                "subject",
                "keywords",
                "sql",
                "parameters",
                "project_root",
                "profile_policy",
            },
            set(database_evidence["required_input_fields"]),
        )
        self.assertFalse(database_evidence["database_connection_attempted"])

    def test_question_git_investigation_requires_structured_path_before_route(
        self,
    ) -> None:
        service = FakeCapabilityService(
            {
                "knowledge.answer": [
                    (
                        "success",
                        {
                            "answer_status": "needs_live_evidence",
                            "suggested_capabilities": ["git.inspect"],
                        },
                    )
                ]
            }
        )

        result = CapabilityWorkflowOrchestrator(service).run_question(
            text="调查代码仓库当前状态",
            allow_live_evidence=True,
        )

        self.assertEqual(
            ["knowledge.answer"],
            [request.capability for request in service.requests],
        )
        self.assertEqual(("knowledge.answer", "git.inspect"), result.events)
        evidence = result.data["live_evidence"]["git.inspect"]
        self.assertEqual("blocked", evidence["status"])
        self.assertEqual(
            "GIT_INSPECT_STRUCTURED_INPUT_REQUIRED",
            evidence["summary"],
        )
        self.assertEqual(["project_path"], evidence["required_input_fields"])
        self.assertFalse(evidence["repository_command_attempted"])

    def test_question_outer_failure_cannot_claim_answered(self) -> None:
        service = FakeCapabilityService(
            {
                "knowledge.answer": [
                    ("blocked", {"answer_status": "answered", "answer": "伪答案"})
                ]
            }
        )

        result = CapabilityWorkflowOrchestrator(service).run_question(text="问题")

        self.assertEqual("unsupported", result.status)
        self.assertNotEqual("answered", result.status)
        self.assertEqual(("knowledge.answer",), result.events)

    def test_question_outer_partial_cannot_trigger_live_evidence(self) -> None:
        service = FakeCapabilityService(
            {
                "knowledge.answer": [
                    (
                        "partial",
                        {
                            "answer_status": "needs_live_evidence",
                            "suggested_capabilities": ["workitem.read"],
                        },
                    )
                ]
            }
        )

        result = CapabilityWorkflowOrchestrator(service).run_question(
            text="调查云效 DFHIS-1",
            allow_live_evidence=True,
        )

        self.assertEqual("unsupported", result.status)
        self.assertEqual(("knowledge.answer",), result.events)
        self.assertEqual(1, len(service.requests))

    def test_question_untrusted_shapes_stop_safely_without_mutating_provider_data(
        self,
    ) -> None:
        for raw_result in (None, [], "invalid"):
            with self.subTest(raw_result=raw_result):
                result = CapabilityWorkflowOrchestrator(
                    RawCapabilityService(raw_result)
                ).run_question(text="问题")
                self.assertEqual("unsupported", result.status)
                self.assertEqual(("knowledge.answer",), result.events)

        provider_data = {
            "answer_status": "needs_live_evidence",
            "suggested_capabilities": "workitem.read",
            "live_evidence": {"provider": {"status": "original"}},
        }
        service = FakeCapabilityService(
            {"knowledge.answer": [("success", provider_data)]}
        )

        result = CapabilityWorkflowOrchestrator(service).run_question(
            text="调查云效 DFHIS-1",
            allow_live_evidence=True,
        )

        self.assertEqual("unsupported", result.status)
        self.assertEqual(1, len(service.requests))
        self.assertEqual(
            {"provider": {"status": "original"}},
            provider_data["live_evidence"],
        )
        self.assertIsNot(result.data.get("live_evidence"), provider_data["live_evidence"])

    def test_cli_routing_cannot_upgrade_configured_mode(self) -> None:
        self.assertEqual("legacy", resolve_capability_routing("legacy", None))
        self.assertEqual("observe", resolve_capability_routing("legacy", "observe"))
        self.assertEqual("observe", resolve_capability_routing("enforce", "observe"))
        self.assertEqual("legacy", resolve_capability_routing("enforce", "legacy"))
        with self.assertRaisesRegex(ValueError, "不能升级"):
            resolve_capability_routing("legacy", "enforce")
        with self.assertRaisesRegex(ValueError, "不能升级"):
            resolve_capability_routing("observe", "enforce")

    def test_cli_exposes_routing_and_interaction_modes_without_write_upgrade(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--demand", "只读问题"])

        self.assertIsNone(args.capability_routing)
        self.assertEqual("task", args.interaction_mode)
        self.assertEqual("", args.visual_evidence_file)
        self.assertEqual("", args.database_credentials_file)
        self.assertEqual("", args.knowledge_candidate_file)
        routing_action = next(
            action
            for action in parser._actions
            if action.dest == "capability_routing"
        )
        self.assertEqual({"legacy", "observe", "enforce"}, set(routing_action.choices))

        explicit = parser.parse_args(
            [
                "--demand",
                "需要只读数据库证据",
                "--database-inspect-file",
                "/private/query.json",
                "--database-execute",
                "--database-credentials-file",
                "/private/readonly.json",
                "--knowledge-candidate-file",
                "/private/candidate.json",
            ]
        )
        self.assertTrue(explicit.database_execute)
        self.assertEqual("/private/query.json", explicit.database_inspect_file)
        self.assertEqual(
            "/private/readonly.json",
            explicit.database_credentials_file,
        )
        self.assertEqual(
            "/private/candidate.json",
            explicit.knowledge_candidate_file,
        )

    def test_legacy_mode_does_not_require_formal_plugin_roots(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "capabilities.json"
            missing_root = Path(temp_dir) / "staging" / "his-harness-core"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "his-capability-runtime-config.v1",
                        "routing_mode": "legacy",
                        "plugin_roots": [str(missing_root)],
                        "external_writes_default": False,
                        "default_timeout_seconds": 60,
                    }
                ),
                encoding="utf-8",
            )

            service = build_capability_service(
                requested_mode=None,
                config_path=config_path,
            )
            request = CapabilityRequest(
                request_id="legacy-no-plugin",
                capability="workitem.read",
                provider="yunxiao",
                mode="preview",
                mutation_level=MutationLevel.L1,
                authorization=CapabilityAuthorization(False, ()),
                input={},
                context={},
            )

            result = service.route(
                request,
                legacy_callable=lambda: {"status": "success", "source": "legacy"},
            )

            self.assertEqual("legacy", result.selected)
            self.assertEqual("success", result.result["status"])

    def test_enforce_builder_verifies_inventory_without_initializing_knowledge(
        self,
    ) -> None:
        source_plugins = PLUGIN_SOURCE_ROOT
        source_inventory = Path(__file__).resolve().parents[1] / "config" / "plugin_inventory.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            plugin_roots = []
            for name in (
                "his-harness-core",
                "yunxiao",
                "his-engineering",
                "his-knowledge",
            ):
                destination = root / "plugins" / name
                shutil.copytree(source_plugins / name, destination)
                plugin_roots.append(str(destination))
            shutil.copy2(source_inventory, root / "plugin_inventory.json")
            knowledge_home = root / "knowledge"
            config_path = root / "capabilities.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "his-capability-runtime-config.v1",
                        "routing_mode": "enforce",
                        "plugin_roots": plugin_roots,
                        "external_writes_default": False,
                        "default_timeout_seconds": 60,
                        "knowledge_home": str(knowledge_home),
                    }
                ),
                encoding="utf-8",
            )

            service = build_capability_service(
                requested_mode=None,
                config_path=config_path,
            )
            answer = CapabilityWorkflowOrchestrator(service).run_question(
                text="harness",
            )

            self.assertEqual("unsupported", answer.status)
            self.assertFalse(knowledge_home.exists())

    def test_enforce_builder_answers_after_explicit_seed_import(
        self,
    ) -> None:
        source_plugins = PLUGIN_SOURCE_ROOT
        source_inventory = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "plugin_inventory.json"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            plugin_roots = []
            for name in (
                "his-harness-core",
                "yunxiao",
                "his-engineering",
                "his-knowledge",
            ):
                destination = root / "plugins" / name
                shutil.copytree(source_plugins / name, destination)
                plugin_roots.append(str(destination))
            shutil.copy2(source_inventory, root / "plugin_inventory.json")
            knowledge_home = root / "knowledge"
            knowledge_plugin = root / "plugins" / "his-knowledge"
            imported = subprocess.run(
                [
                    sys.executable,
                    str(knowledge_plugin / "scripts" / "import_seed.py"),
                    "--home",
                    str(knowledge_home),
                    "--seed",
                    str(knowledge_plugin / "assets" / "seed_knowledge.json"),
                ],
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, imported.returncode, imported.stderr)
            config_path = root / "capabilities.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "his-capability-runtime-config.v1",
                        "routing_mode": "enforce",
                        "plugin_roots": plugin_roots,
                        "external_writes_default": False,
                        "default_timeout_seconds": 60,
                        "knowledge_home": str(knowledge_home),
                    }
                ),
                encoding="utf-8",
            )

            service = build_capability_service(
                requested_mode=None,
                config_path=config_path,
            )
            answer = CapabilityWorkflowOrchestrator(service).run_question(
                text="harness",
            )

            self.assertEqual("answered", answer.status)
            self.assertIn("治理与编排层", answer.data["answer"])
            self.assertEqual(1, len(answer.data["evidence"]))
            evidence = answer.data["evidence"][0]
            self.assertEqual(
                {
                    "stable_key",
                    "title",
                    "authority",
                    "version_label",
                    "source_refs",
                    "excerpt",
                },
                set(evidence),
            )
            self.assertEqual(
                "governance:harness-orchestration-boundary",
                evidence["stable_key"],
            )
            self.assertEqual("reviewed_team_knowledge", evidence["authority"])
            self.assertEqual("governance-v1", evidence["version_label"])
            self.assertEqual(
                [
                    {
                        "claim_level": "governance",
                        "ref": "staged-governance:harness-orchestration-boundary",
                    }
                ],
                evidence["source_refs"],
            )
            self.assertEqual(
                ("module=Harness",),
                tuple(answer.data["applicability"]),
            )
            self.assertEqual("current", answer.data["freshness"])

    def test_enforce_builder_injects_only_explicit_readonly_database_credentials(
        self,
    ) -> None:
        source_plugins = PLUGIN_SOURCE_ROOT
        source_inventory = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "plugin_inventory.json"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            plugin_roots = []
            for name in (
                "his-harness-core",
                "yunxiao",
                "his-engineering",
                "his-knowledge",
            ):
                destination = root / "plugins" / name
                shutil.copytree(source_plugins / name, destination)
                plugin_roots.append(str(destination))
            shutil.copy2(source_inventory, root / "plugin_inventory.json")
            config_path = root / "capabilities.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "his-capability-runtime-config.v1",
                        "routing_mode": "enforce",
                        "plugin_roots": plugin_roots,
                        "external_writes_default": False,
                        "default_timeout_seconds": 60,
                        "knowledge_home": str(root / "knowledge"),
                    }
                ),
                encoding="utf-8",
            )
            policy_path = root / "pg-policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0-pg-evidence-profiles",
                        "default_mode": "off",
                        "profiles": {
                            "his_test": {
                                "environment": "test",
                                "enabled": True,
                                "max_rows": 2,
                                "connect_timeout_seconds": 5,
                                "query_timeout_seconds": 10,
                                "total_timeout_seconds": 45,
                                "max_metadata_queries": 3,
                                "sensitive_column_patterns": [
                                    "patient",
                                    "phone",
                                ],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            readonly_secrets = {
                "pg_his_test_readonly_dsn": (
                    "postgresql://secret-user:secret-password@127.0.0.1:1/his"
                ),
                "pg_his_test_readonly_user": "secret-user",
                "pg_his_test_readonly_password": "secret-password",
            }
            credentials_path = root / "credentials.json"
            credentials_path.write_text(
                json.dumps(
                    {
                        **readonly_secrets,
                        "pg_his_test_write_password": "must-not-be-injected",
                        "aliyun_devops_pat": "must-not-be-injected-either",
                    }
                ),
                encoding="utf-8",
            )
            service = build_capability_service(
                requested_mode=None,
                config_path=config_path,
                database_credentials_path=credentials_path,
            )
            request = CapabilityRequest(
                request_id="database-main-service-preview",
                capability="database.inspect",
                provider="postgresql",
                mode="preview",
                mutation_level=MutationLevel.L1,
                authorization=CapabilityAuthorization(False, ()),
                input={
                    "subject": "确认测试库配置值",
                    "keywords": ["配置"],
                    "sql": (
                        "SELECT code, value FROM his_test.his_config "
                        "WHERE code = %(code)s"
                    ),
                    "parameters": {"code": "EXAMPLE"},
                    "project_root": str(root),
                    "profile_policy": str(policy_path),
                    "mode": "plan",
                },
                context={},
            )

            routed = service.route(request)
            serialized = json.dumps(routed.result, ensure_ascii=False)

        self.assertEqual("success", routed.result["status"])
        self.assertEqual(
            "ready",
            routed.result["data"]["plan"]["status"],
        )
        self.assertEqual(
            sorted(readonly_secrets),
            routed.result["audit"]["runtime"]["environment_keys"],
        )
        for secret in (
            *readonly_secrets.values(),
            "must-not-be-injected",
            "must-not-be-injected-either",
        ):
            self.assertNotIn(secret, serialized)

    def test_enforce_builder_rejects_pre_start_source_drift(self) -> None:
        source_plugins = PLUGIN_SOURCE_ROOT
        source_inventory = Path(__file__).resolve().parents[1] / "config" / "plugin_inventory.json"
        targets = (
            ("his-harness-core", "capabilities.json"),
            ("yunxiao", "scripts/workitem_read.py"),
            (
                "his-knowledge",
                "scripts/knowledge_store.py",
            ),
        )
        for plugin_name, relative_path in targets:
            with self.subTest(plugin=plugin_name, source=relative_path):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir).resolve()
                    plugin_roots = []
                    for name in (
                        "his-harness-core",
                        "yunxiao",
                        "his-engineering",
                        "his-knowledge",
                    ):
                        destination = root / "plugins" / name
                        shutil.copytree(source_plugins / name, destination)
                        plugin_roots.append(str(destination))
                    shutil.copy2(source_inventory, root / "plugin_inventory.json")
                    target = root / "plugins" / plugin_name / relative_path
                    target.write_bytes(
                        target.read_bytes()
                        + (b"\n " if target.name == "capabilities.json" else b"\n# drift\n")
                    )
                    config_path = root / "capabilities.json"
                    config_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "his-capability-runtime-config.v1",
                                "routing_mode": "enforce",
                                "plugin_roots": plugin_roots,
                                "external_writes_default": False,
                                "default_timeout_seconds": 60,
                                "knowledge_home": str(root / "knowledge"),
                            }
                        ),
                        encoding="utf-8",
                    )

                    with self.assertRaises(PluginInventoryError):
                        build_capability_service(
                            requested_mode=None,
                            config_path=config_path,
                        )

    def test_observe_builder_and_question_do_not_initialize_persistent_knowledge(
        self,
    ) -> None:
        source_plugins = PLUGIN_SOURCE_ROOT
        source_inventory = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "plugin_inventory.json"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            plugin_roots = []
            for name in (
                "his-harness-core",
                "yunxiao",
                "his-engineering",
                "his-knowledge",
            ):
                destination = root / "plugins" / name
                shutil.copytree(source_plugins / name, destination)
                plugin_roots.append(str(destination))
            shutil.copy2(source_inventory, root / "plugin_inventory.json")
            knowledge_home = root / "knowledge"
            config_path = root / "capabilities.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "his-capability-runtime-config.v1",
                        "routing_mode": "observe",
                        "plugin_roots": plugin_roots,
                        "external_writes_default": False,
                        "default_timeout_seconds": 60,
                        "knowledge_home": str(knowledge_home),
                    }
                ),
                encoding="utf-8",
            )

            service = build_capability_service(
                requested_mode=None,
                config_path=config_path,
            )
            answer = CapabilityWorkflowOrchestrator(service).run_question(
                text="harness",
            )

            self.assertEqual("unsupported", answer.status)
            self.assertFalse(knowledge_home.exists())

    def test_runner_records_real_twelve_stage_ledger_and_skips_knowledge_write(
        self,
    ) -> None:
        service = FakeCapabilityService(mode="observe")
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                database,
                "DB_PATH",
                Path(temp_dir) / "harness.sqlite",
            ):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                ).run(
                    title="真实阶段账本",
                    demand_text="只读分析字段展示逻辑",
                    execution_mode="readonly",
                    requirement_governance="observe",
                )
                artifacts = database.get_artifacts(result.run_id)

        self.assertEqual(
            TASK_CAPABILITY_SEQUENCE,
            tuple(event["stage"] for event in result.orchestration_events),
        )
        knowledge_event = next(
            event
            for event in result.orchestration_events
            if event["stage"] == "knowledge_candidate"
        )
        self.assertEqual("skipped", knowledge_event["status"])
        self.assertIn("不写入", knowledge_event["reason"])
        ledger_artifact = next(
            artifact
            for artifact in artifacts
            if artifact["kind"] == "capability_orchestration_json"
        )
        self.assertEqual(
            list(result.orchestration_events),
            json.loads(ledger_artifact["content"])["events"],
        )

    def test_runner_self_generated_question_receipt_fails_before_side_effects(
        self,
    ) -> None:
        service = FakeCapabilityService(mode="observe")
        llm = MockLLMClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                database,
                "DB_PATH",
                Path(temp_dir) / "harness.sqlite",
            ), patch.object(
                llm,
                "complete",
                wraps=llm.complete,
            ) as complete, patch(
                "app.harness.collect_yunxiao_evidence",
            ) as collect_evidence:
                runner = RequirementWorkflowRunner(
                    llm,
                    allow_mock=True,
                    capability_service=service,
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "task_capability_route_requires_requirement_workflow",
                ):
                    runner.run(
                        title="普通知识咨询",
                        demand_text="Python 的装饰器是什么？",
                        execution_mode="readonly",
                        requirement_governance="observe",
                        yunxiao_read=True,
                    )
                with database.connect() as connection:
                    run_count = int(
                        connection.execute("select count(*) from runs").fetchone()[0]
                    )
                events = TaskIntentRepository().list_recent_events()

        complete.assert_not_called()
        collect_evidence.assert_not_called()
        self.assertEqual([], service.requests)
        self.assertEqual(0, run_count)
        self.assertEqual(1, len(events))
        self.assertEqual("question", events[0]["mode"])

    def test_runner_reuses_manager_routing_receipt_without_second_decision(self) -> None:
        service = FakeCapabilityService(mode="observe")
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                database,
                "DB_PATH",
                Path(temp_dir) / "harness.sqlite",
            ):
                receipt = TaskIntentService().route(
                    "这个需求会影响哪些路径？",
                    IntentContext(conversation_key="manager-runner-receipt"),
                )
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                ).run(
                    title="Manager 统一入口",
                    demand_text="这个需求会影响哪些路径？",
                    execution_mode="readonly",
                    requirement_governance="observe",
                    routing_result=receipt,
                )
                events = TaskIntentRepository().list_recent_events()

        self.assertFalse(receipt.mutation_requested)
        self.assertEqual(1, len(events))
        self.assertEqual(
            TASK_CAPABILITY_SEQUENCE,
            tuple(event["stage"] for event in result.orchestration_events),
        )

    def test_runner_routes_yunxiao_read_through_injected_capability_service(self) -> None:
        service = FakeCapabilityService(
            {
                "workitem.read": [
                    (
                        "success",
                        {
                            "status": "ready_for_analysis",
                            "work_item_id": "DFHIS-1",
                            "clean_text": "只读需求证据",
                        },
                    )
                ]
            },
            mode="observe",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    database,
                    "DB_PATH",
                    Path(temp_dir) / "harness.sqlite",
                ),
                patch(
                    "app.harness.collect_yunxiao_evidence",
                    side_effect=AssertionError("real provider must not be called"),
                ) as legacy_provider,
            ):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    capability_service=service,
                ).run(
                    title="能力路由",
                    demand_text="读取 DFHIS-1 需求",
                    execution_mode="readonly",
                    yunxiao_read=True,
                    yunxiao_url="https://devops.example/workitem/DFHIS-1",
                )

        self.assertIn(result.status, {"success", "failed"})
        self.assertEqual(
            ["workitem.read", "requirement.govern"],
            [request.capability for request in service.requests],
        )
        legacy_provider.assert_not_called()

    def test_enforce_yunxiao_write_routes_and_blocks_before_legacy_transport(
        self,
    ) -> None:
        service = FakeCapabilityService(mode="enforce")
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    database,
                    "DB_PATH",
                    Path(temp_dir) / "harness.sqlite",
                ),
                patch.object(
                    RequirementWorkflowRunner,
                    "_build_yunxiao_transaction_manager",
                    side_effect=AssertionError("legacy write manager must not be built"),
                ) as legacy_manager,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "workitem.write.*legacy",
                ):
                    RequirementWorkflowRunner(
                        MockLLMClient(),
                        allow_mock=True,
                        capability_service=service,
                    ).run(
                        title="阻断旧云效写入",
                        demand_text="为 DFHIS-1 写入评论",
                        execution_mode="readonly",
                        yunxiao_transaction_mode="write",
                        yunxiao_entity_kind="bug",
                        yunxiao_entity_id="DFHIS-1",
                        yunxiao_write_confirm="WRITE:bug:DFHIS-1",
                        yunxiao_human_confirmed=True,
                    )

        self.assertEqual(
            ["workitem.write"],
            [request.capability for request in service.requests],
        )
        request = service.requests[0]
        self.assertEqual("apply", request.mode)
        self.assertEqual(MutationLevel.L4, request.mutation_level)
        legacy_manager.assert_not_called()

    def test_legacy_yunxiao_write_is_blocked_before_legacy_transport(
        self,
    ) -> None:
        service = FakeCapabilityService(mode="legacy")
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    database,
                    "DB_PATH",
                    Path(temp_dir) / "harness.sqlite",
                ),
                patch.object(
                    RequirementWorkflowRunner,
                    "_build_yunxiao_transaction_manager",
                    side_effect=AssertionError(
                        "legacy write manager must not be built"
                    ),
                ) as legacy_manager,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "workitem.write.*未开放",
                ):
                    RequirementWorkflowRunner(
                        MockLLMClient(),
                        allow_mock=True,
                        capability_service=service,
                    ).run(
                        title="阻断 legacy 云效写入",
                        demand_text="为 DFHIS-1 写入评论",
                        execution_mode="readonly",
                        yunxiao_transaction_mode="write",
                        yunxiao_entity_kind="bug",
                        yunxiao_entity_id="DFHIS-1",
                        yunxiao_write_confirm="WRITE:bug:DFHIS-1",
                        yunxiao_human_confirmed=True,
                    )

        self.assertEqual(
            ["workitem.write"],
            [request.capability for request in service.requests],
        )
        legacy_manager.assert_not_called()


if __name__ == "__main__":
    unittest.main()
