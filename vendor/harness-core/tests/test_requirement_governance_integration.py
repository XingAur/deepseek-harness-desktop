from __future__ import annotations

import os
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from app import database
from app.acceptance_contracts import AcceptanceContractResult
from app.clarification_gate import PatchReadinessResult
from app.core_closure import (
    RequirementContract,
    build_requirement_contract_from_single_pass,
    review_final_diff,
    validate_requirement_governance_outputs,
)
from app.evaluator import EvaluationResult
from app.fullstack_executor import FullstackExecutionResult
from app.harness import (
    GOVERNANCE_ROUTE_ERROR,
    RequirementWorkflowRunner,
    _enforce_contract_boundary_error,
    _resolve_governance_execution,
    build_requirement_governance_outputs,
    gate_readonly_evaluation,
    single_demand_execution_blocker,
    write_run_outputs,
)
from app.llm_client import MockLLMClient
from app.precommit_verifier import PrecommitVerificationResult
from app.requirement_governance import GovernanceCheck, RequirementGovernanceResult
from app.requirement_understanding import RequirementUnderstandingResult, UnderstandingCheck
from app.review_executor import ReviewExecutionResult
from app.single_pass_change_contract import SinglePassChangeContract
from app.technical_decision import TechnicalDecisionResult
from app.worktree_executor import WorktreeExecutionResult


def ready_governance() -> RequirementGovernanceResult:
    return RequirementGovernanceResult(
        schema_version="requirement-governance.v1",
        status="ready_for_local_change",
        can_modify=True,
        can_complete_in_single_pass=True,
        risk_level="low",
        checks=tuple(
            GovernanceCheck(name, "pass", "已闭合。")
            for name in (
                "source_integrity", "reasonableness", "compliance", "completeness",
                "changeability", "impact", "verification", "single_pass_readiness",
            )
        ),
        blockers=(),
        missing_information=(),
        unsupported_reasons=(),
        required_capabilities=(),
        evidence_refs=({"source": "structured_input"},),
    )


def ready_single_pass() -> SinglePassChangeContract:
    return SinglePassChangeContract(
        schema_version="single-pass-change-contract.v1",
        status="ready",
        objective="显示只读字段",
        in_scope=("前端页面展示",),
        out_of_scope=("不修改后端",),
        repositories=({"name": "df-web-test", "path": "/tmp/df-web-test", "role": "frontend"},),
        allowed_paths=("src/view.vue",),
        business_rules=({"name": "readonly", "allowed_values": {"default": "保持原逻辑", "enabled": "显示字段"}},),
        preserved_behaviors=("默认保持原逻辑",),
        adjacent_paths=(),
        database_impacts=(),
        configuration_impacts=(),
        verify_commands=("test -f src/view.vue",),
        automatic_acceptance=("文件存在",),
        manual_acceptance=("页面展示验收",),
        rollback_strategy="restore_pre_change_local_files",
        blockers=(),
        change_context_pack_id="ccp:sha256:" + "a" * 64,
        change_context_projection_hash="sha256:" + "b" * 64,
        change_context_layer_hashes=tuple(
            {"layer_type": layer_type, "content_hash": "sha256:" + character * 64}
            for layer_type, character in zip(
                ("project_graph", "change_scope", "code_graph", "data_graph"),
                "cdef",
            )
        ),
    )


def blocked_governance() -> RequirementGovernanceResult:
    reason = "缺少可执行验证证据。"
    return RequirementGovernanceResult(
        schema_version="requirement-governance.v1",
        status="blocked_needs_requirement",
        can_modify=False,
        can_complete_in_single_pass=False,
        risk_level="unknown",
        checks=tuple(
            GovernanceCheck(name, "blocked", "治理输入无法安全闭合。", blockers=(reason,))
            for name in (
                "source_integrity", "reasonableness", "compliance", "completeness",
                "changeability", "impact", "verification", "single_pass_readiness",
            )
        ),
        blockers=(reason,),
        missing_information=(reason,),
        unsupported_reasons=(),
        required_capabilities=(),
        evidence_refs=(),
    )


def blocked_single_pass() -> SinglePassChangeContract:
    return SinglePassChangeContract(
        schema_version="single-pass-change-contract.v1",
        status="blocked",
        objective="验证治理阻断结果",
        in_scope=(),
        out_of_scope=(),
        repositories=(),
        allowed_paths=(),
        business_rules=(),
        preserved_behaviors=(),
        adjacent_paths=(),
        database_impacts=(),
        configuration_impacts=(),
        verify_commands=(),
        automatic_acceptance=(),
        manual_acceptance=(),
        rollback_strategy="not_available",
        blockers=("缺少可执行验证证据。",),
    )


def blocked_understanding() -> RequirementUnderstandingResult:
    reason = "缺少项目入口或调用链源码证据；请先定位页面/接口入口和数据或依赖路径。"
    return RequirementUnderstandingResult(
        schema_version="requirement-understanding.v1",
        status="blocked_needs_project_discovery",
        can_modify=False,
        checks=(
            UnderstandingCheck(
                name="entry_and_call_chain",
                status="blocked",
                summary="证据不足，改码门禁保持关闭。",
                blockers=(reason,),
            ),
        ),
        blockers=(reason,),
        next_readonly_actions=("在已选项目中只读定位实际项目入口、调用/数据链路。",),
    )


def ready_technical_decision(project_path: Path) -> TechnicalDecisionResult:
    return TechnicalDecisionResult(
        project_root=str(project_path.parent),
        selected_projects=[
            {
                "name": project_path.name,
                "path": str(project_path),
                "role": "frontend",
                "exists": True,
            },
        ],
        field_provenance={},
        implementation_decision={
            "can_patch": True,
            "summary": "允许本地受控修改。",
            "blockers": [],
        },
        recommended_allowed_paths=["src/view.vue"],
        recommended_verify_commands=["test -f src/view.vue"],
        artifacts={},
    )


class RequirementGovernanceIntegrationTests(unittest.TestCase):
    def test_high_risk_screenshot_gate_stops_before_technical_project_decision(self) -> None:
        class MissingVisualFacts:
            def analyze(self, *, title, description, image_paths):
                return {"facts": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "error.png"
            image.write_bytes(b"image")
            evidence_file = root / "snapshot.json"
            evidence_file.write_text(json.dumps({
                "source_type": "yunxiao",
                "work_item": {"title": "医保门诊退费预结算失败", "description": "截图显示退费时报错。"},
                "inline_file_downloads": [{"name": "error.png", "path": str(image), "content_type": "image/png", "status": "success"}],
            }), encoding="utf-8")
            with patch("app.harness.build_technical_decision", side_effect=AssertionError("不应开始项目定位")):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                    visual_evidence_analyzer=MissingVisualFacts(),
                ).run(
                    title="DFHIS-截图门禁",
                    demand_text="按云效需求分析。",
                    requirement_evidence_file=evidence_file,
                    execution_mode="readonly",
                )

        self.assertEqual("blocked", result.status)
        self.assertEqual("visual_evidence_blocked", result.evaluation_status)
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = Path(self.temp_dir.name) / "harness.sqlite"
        self.env_patch = patch.dict(os.environ, {"HARNESS_DB_PATH": str(self.test_db_path)})
        self.db_path_patch = patch.object(database, "DB_PATH", self.test_db_path)
        self.env_patch.start()
        self.db_path_patch.start()

    def tearDown(self) -> None:
        self.db_path_patch.stop()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_suite_uses_temporary_control_database(self) -> None:
        self.assertEqual(self.test_db_path, database.DB_PATH)
        self.assertNotEqual(
            Path(__file__).resolve().parents[1] / "data" / "harness.sqlite",
            database.DB_PATH,
        )

    def test_invalid_governance_mode_fails_before_provider_work(self) -> None:
        runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
        with patch("app.harness.collect_yunxiao_evidence") as provider:
            with self.assertRaisesRegex(ValueError, "requirement_governance"):
                runner.run(demand_text="显示只读字段", requirement_governance="invalid")
        provider.assert_not_called()

    def test_single_demand_trial_requires_governance_and_single_pass_contract_before_worktree(self) -> None:
        self.assertIn(
            "需求治理",
            single_demand_execution_blocker(
                governance_ready=False,
                contract_ready=True,
                technical_can_patch=True,
                technical_blockers=[],
            ),
        )
        self.assertIn(
            "一次改好变更契约",
            single_demand_execution_blocker(
                governance_ready=True,
                contract_ready=False,
                technical_can_patch=True,
                technical_blockers=[],
            ),
        )
        self.assertIn(
            "技术自治",
            single_demand_execution_blocker(
                governance_ready=True,
                contract_ready=True,
                technical_can_patch=False,
                technical_blockers=["字段来源未定位"],
            ),
        )
        self.assertEqual(
            "",
            single_demand_execution_blocker(
                governance_ready=True,
                contract_ready=True,
                technical_can_patch=True,
                technical_blockers=[],
            ),
        )

    def test_capability_boundary_reports_the_exact_expansion_dimension(self) -> None:
        legacy = ready_single_pass()
        expanded = SinglePassChangeContract(
            **{
                **legacy.__dict__,
                "allowed_paths": ("src/view.vue", "src/other.vue"),
            }
        )

        error = _enforce_contract_boundary_error(
            legacy_governance=ready_governance(),
            legacy_contract=legacy,
            capability_governance=ready_governance(),
            capability_contract=expanded,
            technical_decision={
                "selected_projects": [
                    {"name": "df-web-test", "path": "/tmp/df-web-test", "role": "frontend", "exists": True}
                ]
            },
            trusted_allowed_paths=["src/view.vue"],
            trusted_verify_commands=["test -f src/view.vue"],
        )

        self.assertIn("允许修改路径", error)

    def test_legacy_skips_governance_artifacts_and_assessment(self) -> None:
        runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
        with patch("app.requirement_governance.assess_requirement") as assess:
            result = runner.run(
                title="兼容性检查",
                demand_text="挂号页面显示一个只读提示字段，不涉及收费、医保或结算。",
                execution_mode="readonly",
                requirement_governance="legacy",
            )
        kinds = {item["kind"] for item in database.get_artifacts(result.run_id)}
        assess.assert_not_called()
        self.assertNotIn("requirement_governance_json", kinds)
        self.assertNotIn("single_pass_change_contract_json", kinds)

    def test_observe_emits_four_governance_artifacts_without_executor(self) -> None:
        runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
        with patch.object(RequirementWorkflowRunner, "_run_worktree_execution") as executor:
            result = runner.run(
                title="观察模式",
                demand_text="挂号页面显示一个只读提示字段，不涉及收费、医保或结算。",
                execution_mode="readonly",
            )
        kinds = {item["kind"] for item in database.get_artifacts(result.run_id)}
        executor.assert_not_called()
        self.assertTrue({
            "requirement_governance_json", "requirement_governance_markdown",
            "single_pass_change_contract_json", "single_pass_change_contract_markdown",
        }.issubset(kinds))

    def test_observe_writes_deterministic_local_governance_artifacts(self) -> None:
        result = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True).run(
            title="观察模式",
            demand_text="挂号页面显示一个只读提示字段，不涉及收费、医保或结算。",
            execution_mode="readonly",
        )
        output = write_run_outputs(result.run_id, self.temp_dir.name)
        self.assertTrue((output / "requirement_governance.json").is_file())
        self.assertTrue((output / "requirement_governance.md").is_file())
        self.assertTrue((output / "single_pass_change_contract.json").is_file())
        self.assertTrue((output / "single_pass_change_contract.md").is_file())
        self.assertTrue((output / "requirement_understanding.json").is_file())
        self.assertTrue((output / "requirement_understanding.md").is_file())

    def test_readonly_run_archives_conversation_and_blocks_incomplete_error_chain(self) -> None:
        evidence_file = Path(self.temp_dir.name) / "conversation.json"
        evidence_file.write_text(json.dumps({
            "host": "codex-app",
            "messages": [{"id": "u1", "role": "user", "content": "截图显示国家医保挂号预结算失败。"}],
            "confirmed_facts": [{
                "id": "pre-settlement", "kind": "screenshot_observed",
                "statement": "menZhenTfYjs 调用国家医保挂号预结算。",
                "source_message_ids": ["u1"], "required_code_terms": ["menZhenTfYjs"],
                "must_not_contradict": True,
            }],
        }, ensure_ascii=False), encoding="utf-8")

        result = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True).run(
            title="医保预结算截图报错",
            demand_text="医生申请退费时出现国家医保预结算失败，先只读定位。",
            execution_mode="readonly",
            conversation_evidence_file=evidence_file,
            project_root=self.temp_dir.name,
        )

        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}
        self.assertTrue({"conversation_evidence_json", "conversation_evidence_markdown", "error_chain_closure_json", "error_chain_closure_markdown"}.issubset(artifacts))
        self.assertIn("截图错误链路闭环", result.markdown_report)

    def test_enforce_blocked_governance_never_enters_worktree(self) -> None:
        runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
        with patch.object(RequirementWorkflowRunner, "_run_worktree_execution") as executor:
            result = runner.run(
                title="医保收费调整",
                demand_text="医保收费金额计算调整。",
                execution_mode="core-closure-trial",
                requirement_governance="enforce",
            )
        executor.assert_not_called()
        self.assertEqual("blocked", result.status)

    def test_understanding_gate_blocks_mutation_even_in_observe_mode(self) -> None:
        runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
        with (
            patch("app.harness.build_requirement_understanding", return_value=blocked_understanding()),
            patch.object(RequirementWorkflowRunner, "_run_worktree_execution") as executor,
        ):
            result = runner.run(
                title="入口未定位",
                demand_text="页面需要展示一个只读字段。",
                execution_mode="core-closure-trial",
                requirement_governance="observe",
            )

        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}
        executor.assert_not_called()
        self.assertEqual("blocked", result.status)
        self.assertIn("改码前理解证据包未就绪", (database.get_run(result.run_id) or {}).get("error") or "")
        self.assertTrue({"requirement_understanding_json", "requirement_understanding_markdown"}.issubset(artifacts))

    def test_auto_local_cannot_skip_project_context_scan_before_the_gate(self) -> None:
        runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
        with (
            patch("app.harness.build_fast_local_decision", return_value={"skip_project_context_scan": True}),
            patch("app.harness.build_requirement_understanding", return_value=blocked_understanding()),
            patch.object(RequirementWorkflowRunner, "_build_evidence_bundle", return_value=None) as context_scan,
            patch.object(RequirementWorkflowRunner, "_run_worktree_execution") as executor,
        ):
            result = runner.run(
                title="快速路径也要取证",
                demand_text="页面需要展示一个只读字段。",
                execution_mode="auto-local",
                requirement_governance="observe",
            )

        context_scan.assert_called_once()
        executor.assert_not_called()
        self.assertEqual("blocked", result.status)

    def test_readonly_enforce_finishes_analysis_without_opening_mutation_gate(self) -> None:
        runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
        with patch.object(RequirementWorkflowRunner, "_run_worktree_execution") as executor:
            result = runner.run(
                title="医保收费只读分析",
                demand_text="医保收费金额计算调整；只做只读分析，不修改代码。",
                execution_mode="readonly",
                requirement_governance="enforce",
            )

        executor.assert_not_called()
        self.assertEqual("success", result.status)
        self.assertEqual("analysis_complete_readonly", result.evaluation_status)
        run = database.get_run(result.run_id) or {}
        self.assertEqual("success", run.get("status"))
        self.assertIn("自动改码门禁仍关闭", run.get("evaluation_summary") or "")

    def test_readonly_blocked_contract_cannot_remain_evaluator_pass(self) -> None:
        evaluation = EvaluationResult(
            status="pass",
            summary="自动审核通过：阶段完整、结构满足要求，报告可进入人工审查。",
        )

        gated = gate_readonly_evaluation(
            evaluation,
            gate_blocked=True,
            reason="多项目改动合同未就绪：缺少药品 HTTP 路由。",
        )

        self.assertEqual("analysis_complete_readonly", gated.status)
        self.assertNotIn("自动审核通过", gated.summary)
        self.assertIn("缺少药品 HTTP 路由", gated.summary)

    def test_enforce_governance_block_is_persisted_as_blocked_not_failed(self) -> None:
        db_path = Path(self.temp_dir.name) / "governance-block.sqlite"
        with patch("app.database.DB_PATH", db_path):
            runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
            result = runner.run(
                title="医保收费调整",
                demand_text="医保收费金额计算调整。",
                execution_mode="core-closure-trial",
                requirement_governance="enforce",
            )
            run = database.get_run(result.run_id) or {}

        self.assertEqual("blocked", result.status)
        self.assertEqual("blocked", run.get("status"))
        self.assertEqual("blocked_requirement_governance", run.get("evaluation_status"))

    def test_enforce_preserves_structured_blocked_capability_governance(self) -> None:
        governance = blocked_governance()
        single_pass = blocked_single_pass()

        (
            mode,
            resolved_governance,
            resolved_contract,
            execution_blocked,
            error,
            capability_authoritative,
        ) = _resolve_governance_execution(
            requested_mode="enforce",
            legacy_governance=None,
            legacy_contract=None,
            legacy_error="",
            routed_result={
                "status": "blocked",
                "data": {
                    "governance": governance.to_dict(),
                    "single_pass_change_contract": single_pass.to_dict(),
                },
            },
            routed_mode="enforce",
            has_routed_result=True,
            technical_decision={},
            trusted_allowed_paths=[],
            trusted_verify_commands=[],
        )

        self.assertEqual("enforce", mode)
        self.assertIsNotNone(resolved_governance)
        self.assertIsNotNone(resolved_contract)
        self.assertEqual("blocked_needs_requirement", resolved_governance.status)
        self.assertEqual("blocked", resolved_contract.status)
        self.assertTrue(execution_blocked)
        self.assertIn("缺少可执行验证证据", error)
        self.assertNotEqual(GOVERNANCE_ROUTE_ERROR, error)
        self.assertFalse(capability_authoritative)

    def test_ready_single_pass_contract_converts_to_existing_requirement_contract(self) -> None:
        contract = build_requirement_contract_from_single_pass(
            title="显示只读字段",
            demand_text="页面显示一个只读字段。",
            governance_result=ready_governance(),
            single_pass_contract=ready_single_pass(),
            apply_to_project=False,
        )
        self.assertEqual("ready", contract.status)
        self.assertEqual(("src/view.vue",), contract.allowed_paths)
        self.assertEqual(("test -f src/view.vue",), contract.verify_commands)
        self.assertEqual(("页面展示验收",), contract.manual_acceptance)

    def test_canonical_validator_reconstructs_legal_ready_outputs(self) -> None:
        governance = ready_governance()
        single_pass = ready_single_pass()

        validated_governance, validated_single_pass = validate_requirement_governance_outputs(
            governance,
            single_pass,
        )

        self.assertIsNot(governance, validated_governance)
        self.assertIsNot(single_pass, validated_single_pass)
        self.assertEqual(governance.to_dict(), validated_governance.to_dict())
        self.assertEqual(single_pass.to_dict(), validated_single_pass.to_dict())

    def test_single_pass_adapter_rejects_subclass_and_frozen_object_mutation(self) -> None:
        class LookalikeSinglePass(SinglePassChangeContract):
            pass

        forged = ready_single_pass()
        object.__setattr__(forged, "allowed_paths", ("../../outside",))
        object.__setattr__(forged, "verify_commands", ("sh -c rm",))
        original = ready_single_pass()
        subclass = LookalikeSinglePass(
            schema_version=original.schema_version,
            status=original.status,
            objective=original.objective,
            in_scope=original.in_scope,
            out_of_scope=original.out_of_scope,
            repositories=original.repositories,
            allowed_paths=original.allowed_paths,
            business_rules=original.business_rules,
            preserved_behaviors=original.preserved_behaviors,
            adjacent_paths=original.adjacent_paths,
            database_impacts=original.database_impacts,
            configuration_impacts=original.configuration_impacts,
            verify_commands=original.verify_commands,
            automatic_acceptance=original.automatic_acceptance,
            manual_acceptance=original.manual_acceptance,
            rollback_strategy=original.rollback_strategy,
            change_context_pack_id=original.change_context_pack_id,
            change_context_projection_hash=original.change_context_projection_hash,
            change_context_layer_hashes=original.change_context_layer_hashes,
            blockers=original.blockers,
        )
        for candidate in (forged, subclass):
            with self.subTest(candidate=type(candidate).__name__):
                contract = build_requirement_contract_from_single_pass(
                    title="显示只读字段",
                    demand_text="页面显示一个只读字段。",
                    governance_result=ready_governance(),
                    single_pass_contract=candidate,
                    apply_to_project=False,
                )
                self.assertEqual("blocked", contract.status)
                self.assertEqual((), contract.allowed_paths)
                self.assertEqual((), contract.verify_commands)
        mutated_governance = ready_governance()
        object.__setattr__(mutated_governance, "checks", ())
        contract = build_requirement_contract_from_single_pass(
            title="显示只读字段",
            demand_text="页面显示一个只读字段。",
            governance_result=mutated_governance,
            single_pass_contract=ready_single_pass(),
            apply_to_project=False,
        )
        self.assertEqual("blocked", contract.status)
        self.assertEqual((), contract.allowed_paths)
        self.assertEqual((), contract.verify_commands)

    def test_enforce_revalidates_frozen_ready_outputs_before_every_execution_entry(self) -> None:
        project_path = Path(self.temp_dir.name) / "df-web-test"
        (project_path / "src").mkdir(parents=True)
        (project_path / "src/view.vue").write_text("<template />\n", encoding="utf-8")
        technical_decision = ready_technical_decision(project_path)
        patch_readiness = PatchReadinessResult(
            status="ready",
            can_patch=True,
            summary="允许本地受控修改。",
            allowed_paths=["src/view.vue"],
            suggested_verify_commands=["test -f src/view.vue"],
        )
        execution_entries = {
            "worktree": "_run_worktree_execution",
            "fullstack-worktree": "_run_fullstack_execution",
            "review-worktree": "_run_review_execution",
            "precommit-verify": "_run_precommit_verification",
            "single-demand-trial": "_run_worktree_execution",
            "core-closure-trial": "_run_worktree_execution",
            "auto-local": "_run_worktree_execution",
        }

        for execution_mode, expected_executor_name in execution_entries.items():
            with self.subTest(execution_mode=execution_mode):
                forged_contract = ready_single_pass()
                object.__setattr__(forged_contract, "allowed_paths", ("../../outside",))
                object.__setattr__(forged_contract, "verify_commands", ("sh -c rm",))

                runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
                with ExitStack() as stack:
                    stack.enter_context(
                        patch(
                            "app.harness.build_requirement_governance_outputs",
                            return_value=(ready_governance(), forged_contract, ""),
                        )
                    )
                    stack.enter_context(
                        patch("app.harness.build_technical_decision", return_value=technical_decision)
                    )
                    stack.enter_context(
                        patch("app.harness.evaluate_patch_readiness", return_value=patch_readiness)
                    )
                    stack.enter_context(patch("app.harness.build_review_context", return_value={}))
                    stack.enter_context(
                        patch.object(
                            runner.evaluator,
                            "evaluate",
                            return_value=EvaluationResult(status="pass", summary="pass"),
                        )
                    )
                    executors = {
                        "_run_worktree_execution": stack.enter_context(
                            patch.object(
                                RequirementWorkflowRunner,
                                "_run_worktree_execution",
                                return_value=WorktreeExecutionResult(
                                    status="success",
                                    summary="unexpected execution",
                                    allowed_paths=["src/view.vue"],
                                ),
                            )
                        ),
                        "_run_fullstack_execution": stack.enter_context(
                            patch.object(
                                RequirementWorkflowRunner,
                                "_run_fullstack_execution",
                                return_value=FullstackExecutionResult(
                                    status="success",
                                    summary="unexpected execution",
                                ),
                            )
                        ),
                        "_run_review_execution": stack.enter_context(
                            patch.object(
                                RequirementWorkflowRunner,
                                "_run_review_execution",
                                return_value=ReviewExecutionResult(
                                    status="success",
                                    summary="unexpected execution",
                                ),
                            )
                        ),
                        "_run_precommit_verification": stack.enter_context(
                            patch.object(
                                RequirementWorkflowRunner,
                                "_run_precommit_verification",
                                return_value=PrecommitVerificationResult(
                                    status="success",
                                    summary="unexpected execution",
                                ),
                            )
                        ),
                    }
                    stack.enter_context(
                        patch.object(RequirementWorkflowRunner, "_store_worktree_artifacts")
                    )
                    stack.enter_context(
                        patch.object(RequirementWorkflowRunner, "_store_fullstack_artifacts")
                    )
                    stack.enter_context(
                        patch.object(RequirementWorkflowRunner, "_store_review_artifacts")
                    )
                    stack.enter_context(
                        patch.object(
                            RequirementWorkflowRunner,
                            "_store_precommit_verification_artifacts",
                        )
                    )

                    result = runner.run(
                        title="统一治理入口校验",
                        demand_text="页面显示一个只读字段，不涉及收费、医保或结算。",
                        project_path=project_path,
                        execution_mode=execution_mode,
                        requirement_governance="enforce",
                        allowed_paths=["src/view.vue"],
                        verify_commands=["test -f src/view.vue"],
                        worktree_dir=Path(self.temp_dir.name) / "worktrees",
                        apply_approved_diff=False,
                    )

                executors[expected_executor_name].assert_not_called()
                self.assertNotEqual("success", result.status)
                run = database.get_run(result.run_id) or {}
                self.assertIn("完整结构校验", str(run.get("error") or ""))

    def test_single_pass_adapter_keeps_legacy_acceptance_contract_and_diff_gates(self) -> None:
        acceptance = AcceptanceContractResult(
            schema_version="1.0-acceptance-contract-result",
            status="pass",
            contract_id="DFHIS-31558",
            kind="ordering_relation",
            verify_command="node src/view.ordering.test.js",
            checks={"same_sequence_uses_source_index": "pass"},
            implementation_evidence=("sortByEarliestDescendant",),
        )
        legacy = RequirementContract(
            schema_version="1.0-requirement-contract",
            status="ready",
            title="DFHIS-31558",
            demand_digest="科室树和右侧排班按顺序号排序并保持一致。",
            default_behavior="空值保持原逻辑",
            default_guard_tokens=("preserveOriginalMode",),
            allowed_paths=("src/view.vue",),
            verify_commands=(acceptance.verify_command,),
            acceptance_contract=acceptance.to_dict(),
            warnings=("legacy acceptance warning",),
        )

        contract = build_requirement_contract_from_single_pass(
            title=legacy.title,
            demand_text=legacy.demand_digest,
            governance_result=ready_governance(),
            single_pass_contract=ready_single_pass(),
            apply_to_project=False,
            legacy_contract=legacy,
            acceptance_contract_result=acceptance,
        )
        review = review_final_diff(
            contract=contract,
            final_diff="""diff --git a/src/view.vue b/src/view.vue
index 1..2 100644
--- a/src/view.vue
+++ b/src/view.vue
@@ -1 +1 @@
+const unrelated = true
""",
            verification_passed=True,
            acceptance_contract_result=acceptance,
        )

        self.assertEqual("ready", contract.status)
        self.assertIn(acceptance.verify_command, contract.verify_commands)
        self.assertEqual(acceptance.to_dict(), contract.acceptance_contract)
        self.assertEqual(("preserveOriginalMode",), contract.default_guard_tokens)
        self.assertIn("legacy acceptance warning", contract.warnings)
        self.assertEqual("blocked", review.status)
        self.assertIn("sortByEarliestDescendant", "\n".join(review.findings))
        self.assertIn("默认", "\n".join(review.findings))

    def test_provider_authority_injection_is_reported_without_attachment_body(self) -> None:
        governance, contract, _ = build_requirement_governance_outputs(
            title="普通展示",
            user_instruction="页面显示一个只读字段。",
            source_type="manual",
            normalized_requirement_evidence={
                "title": "普通展示",
                "description_text": "页面显示一个只读字段。",
                "comments": [],
                "attachments": [{"command": "RAW-ATTACHMENT-BODY"}],
            },
            yunxiao_evidence=None,
            requirement_calibration={},
            technical_decision={},
            change_ownership={},
            acceptance_matrix={},
        )
        self.assertNotEqual("ready_for_local_change", governance.status)
        self.assertEqual("blocked", contract.status)
        self.assertNotIn("RAW-ATTACHMENT-BODY", governance.to_json())
        self.assertNotIn("RAW-ATTACHMENT-BODY", contract.to_json())

    def test_enforce_artifact_write_failure_is_a_stable_local_blocker(self) -> None:
        runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
        with (
            patch.object(RequirementWorkflowRunner, "_store_requirement_governance_artifacts", return_value="injected"),
            patch.object(RequirementWorkflowRunner, "_run_worktree_execution") as executor,
        ):
            result = runner.run(
                title="普通展示",
                demand_text="页面显示一个只读提示字段。",
                execution_mode="core-closure-trial",
                requirement_governance="enforce",
            )
        artifacts = {
            item["kind"]: item["content"]
            for item in database.get_artifacts(result.run_id)
        }
        ledger = json.loads(artifacts["capability_orchestration_json"])
        executor.assert_not_called()
        self.assertEqual("blocked", result.status)
        self.assertEqual(
            "blocked_requirement_governance",
            result.evaluation_status,
        )
        self.assertNotIn("core_requirement_contract_json", artifacts)
        self.assertEqual(
            ["skipped", "skipped", "skipped", "completed"],
            [
                event["status"]
                for event in ledger["events"]
                if event["stage"]
                in {
                    "local_engineering",
                    "verification",
                    "knowledge_candidate",
                    "audit",
                }
            ],
        )

    def test_observe_governance_artifact_failures_write_sanitized_diagnostic_and_evaluation_warning(self) -> None:
        original_add_artifact = database.add_artifact
        governance_kinds = {
            "requirement_governance_json",
            "requirement_governance_markdown",
            "single_pass_change_contract_json",
            "single_pass_change_contract_markdown",
        }

        for failed_kinds in (governance_kinds, {"requirement_governance_json"}):
            with self.subTest(failed_kinds=failed_kinds):
                def fail_governance_artifacts(run_id: int, kind: str, title: str, content: str) -> int:
                    if kind in failed_kinds:
                        raise RuntimeError("RAW-ARTIFACT-FAILURE")
                    return original_add_artifact(run_id, kind, title, content)

                with patch("app.harness.database.add_artifact", side_effect=fail_governance_artifacts):
                    result = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True).run(
                        title="观察模式工件失败",
                        demand_text="挂号页面显示一个只读提示字段，不涉及收费、医保或结算。",
                        execution_mode="readonly",
                        requirement_governance="observe",
                    )

                artifacts = database.get_artifacts(result.run_id)
                diagnostic = next(item for item in artifacts if item["kind"] == "requirement_governance_error")
                run = database.get_run(result.run_id) or {}
                self.assertIn("requirement_governance_json", diagnostic["content"])
                self.assertNotIn("RAW-ARTIFACT-FAILURE", diagnostic["content"])
                self.assertIn("需求治理工件未完整写入", run["evaluation_summary"])
                self.assertNotEqual("blocked_requirement_governance", result.evaluation_status)
                output = write_run_outputs(result.run_id, self.temp_dir.name)
                self.assertTrue((output / "requirement_governance_error.json").is_file())
                if len(failed_kinds) == 1:
                    self.assertIn("single_pass_change_contract_json", {item["kind"] for item in artifacts})


if __name__ == "__main__":
    unittest.main()
