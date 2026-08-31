from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app import database
from app.business_acceptance_repository import BusinessAcceptanceRepository
from app.database_read_policy import validate_readonly_sql
from app.knowledge_consultation import consult_knowledge
from app.knowledge_index import query_knowledge_index
from app.learning_candidate_repository import LearningCandidateRepository
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import ProviderExecutionRequest, ProviderExecutionService
from app.task_intent_repository import TaskIntentRepository
from app.task_intent_router import IntentContext
from app.task_intent_service import TaskIntentService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class FakeReadAdapter:
    """A no-I/O adapter that proves credentials stay inside execution context."""

    def __init__(self) -> None:
        self.calls = 0
        self.resolved_fields: list[str] = []

    def execute(self, request, context):
        self.calls += 1
        for field in context.required_credential_fields:
            credential = context.credential(field)
            if not credential:
                raise AssertionError("fake credential resolution failed")
            self.resolved_fields.append(field)
        context.record_network_dispatch("fake-provider", simulated=True)
        return {
            "source": "fake-provider",
            "work_item_alias": request.parameters.get("work_item_alias", ""),
            "content_hash": "a" * 64,
        }

    def verify(self, *_args, **_kwargs):
        raise AssertionError("read actions must not invoke a write read-back verifier")


class CompleteManagerFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="harness-task10-")
        self.root = Path(self.temp_dir.name)
        self.manager_db = self.root / "manager.sqlite"
        self.knowledge_home = self.root / "knowledge-home"
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.manager_db
        master_key = base64.urlsafe_b64encode(b"c" * 32).decode("ascii")
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HARNESS_DB_PATH": str(self.manager_db),
                "HIS_KNOWLEDGE_HOME": str(self.knowledge_home),
                "HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": master_key,
            },
            clear=False,
        )
        self.environment.start()
        self.clock = MutableClock(datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc))
        self.repository = ManagerProviderRepository()
        self.authorizer = ProviderActionAuthorizer(
            self.repository,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.environment.stop()
        self.temp_dir.cleanup()

    def _yunxiao_profile(self):
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="task10",
            provider="yunxiao",
            profile_key="fake-yunxiao",
            display_name="Fake Yunxiao",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        self.repository.upsert_credential(
            profile_id=profile.id,
            field="pat",
            plaintext="task10-manager-only-pat",
        )
        return profile

    def _failed_action_audit(self, *, action_type: str) -> int:
        return self.repository.record_action(
            profile_id=None,
            action_type=action_type,
            status="failed",
            details={"result": "redacted", "failure_kind": "verification_failed"},
        )

    @staticmethod
    def _failure_sample(run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "task_key": "DFHIS-31333",
            "failure_kind": "verification_failed",
            "summary": "金额汇总规则需要补充回归证据。",
            "evidence_refs": ["runs/fake/replay-result.json"],
            "scope": {"module": "门诊收费", "repo": "df-web-guahaosf"},
        }

    def test_complete_fake_manager_flow_is_local_audited_and_business_explicit(self) -> None:
        self.assertEqual(self.manager_db, database.DB_PATH)
        self.assertFalse(PROJECT_ROOT in self.manager_db.parents)
        self.assertFalse(PROJECT_ROOT in self.knowledge_home.parents)

        profile = self._yunxiao_profile()
        parameters = {
            "organization_alias": "org-fixture",
            "project_alias": "DFHIS",
            "work_item_alias": "DFHIS-31333",
            "timeout_seconds": 5,
        }
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.read",
            target_alias="org-fixture.dfhis-31333",
            parameters=parameters,
            requested_by="manager-user",
        )
        authorization = self.authorizer.confirm(
            plan.id,
            actor="manager-user",
            ttl_seconds=60,
        )
        adapter = FakeReadAdapter()
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": adapter},
        )
        result = service.execute(
            authorization,
            ProviderExecutionRequest(
                plan_id=plan.id,
                actor="manager-user",
                action="workitem.read",
                parameters=parameters,
            ),
        )

        self.assertEqual("succeeded", result["status"])
        self.assertEqual(1, adapter.calls)
        self.assertEqual(["pat"], adapter.resolved_fields)
        self.assertEqual("consumed", self.repository.get_action_plan(plan.id)["state"])
        action_audits = self.repository.list_action_audits(
            action_type="workitem.read",
            limit=20,
        )
        self.assertTrue(any(row["status"] == "succeeded" for row in action_audits))
        self.assertNotIn(
            "task10-manager-only-pat",
            json.dumps([result, action_audits], ensure_ascii=False),
        )

        candidates = LearningCandidateRepository()
        created = candidates.create_failed_run_candidates(
            self._failure_sample("task10-failed-run"),
            source_action_audit_id=self._failed_action_audit(
                action_type="controlled.fake.verification"
            ),
        )
        knowledge_candidate = next(
            item
            for item in created["candidates"]
            if item["candidate_type"] == "knowledge.candidate"
        )
        reviewed = candidates.review_candidate(
            candidate_key=str(knowledge_candidate["candidate_key"]),
            decision="approve",
            reviewer_alias="reviewer-a",
        )
        promoted = candidates.promote_knowledge_candidate(
            candidate_key=str(knowledge_candidate["candidate_key"]),
            reviewer_alias="reviewer-a",
            knowledge_home=self.knowledge_home,
            knowledge_allowed_base=self.root,
        )
        routing_result = TaskIntentService(
            TaskIntentRepository(initialize=False)
        ).route(
            "DFHIS-31333 的已有知识是什么？",
            IntentContext(conversation_key="complete-flow-knowledge"),
        )
        consultation = consult_knowledge(
            "DFHIS-31333",
            routing_result=routing_result,
            knowledge_home=self.knowledge_home,
            repository=self.repository,
            legacy_retrieval=query_knowledge_index,
        )

        self.assertEqual("approved", reviewed["state"])
        self.assertEqual("promoted", promoted["state"])
        self.assertTrue(consultation["answerable"])
        self.assertFalse(consultation["model_used"])
        self.assertEqual("knowledge_hit", consultation["retrieval_status"])
        self.assertTrue(consultation["citations"])

        acceptance_repository = BusinessAcceptanceRepository()
        evidence = acceptance_repository.create_evidence(
            {
                "evidence_key": "dfhis-31333-task10",
                "environment_alias": "his-test-a",
                "operator_alias": "operator-a",
                "test_data_alias": "outpatient-case-001",
                "technical_result": "passed",
                "runtime_verified": True,
                "scenarios": [
                    {
                        "name": "fee-summary-rounding",
                        "status": "passed",
                        "expected": "detail-and-total-match",
                        "actual": "detail-and-total-match",
                        "evidence": "sha256:" + "b" * 64,
                    }
                ],
            }
        )
        acceptance_repository.append_reviewer_decision(
            evidence_id=int(evidence["id"]),
            reviewer_alias="reviewer-a",
            decision="accept",
            reason="runtime-evidence-reviewed",
        )
        accepted = acceptance_repository.get_evidence(int(evidence["id"]))

        self.assertEqual("passed", accepted["technical_result"])
        self.assertTrue(accepted["business_valid"])
        self.assertTrue(self.manager_db.is_file())
        self.assertTrue(self.knowledge_home.is_dir())

    def test_expired_confirmation_and_changed_payload_fail_closed(self) -> None:
        profile = self._yunxiao_profile()
        parameters = {"work_item_alias": "DFHIS-31333", "timeout_seconds": 5}
        expired_plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.read",
            target_alias="org-fixture.dfhis-31333",
            parameters=parameters,
            requested_by="manager-user",
        )
        expired_authorization = self.authorizer.confirm(
            expired_plan.id,
            actor="manager-user",
            ttl_seconds=10,
        )
        self.clock.current += timedelta(seconds=11)

        expired = self.authorizer.consume(
            plan_id=expired_plan.id,
            authorization=expired_authorization,
            actor="manager-user",
            parameters=parameters,
        )

        self.assertFalse(expired.allowed)
        self.assertEqual("authorization_expired", expired.reason)

        changed_plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.read",
            target_alias="org-fixture.dfhis-31334",
            parameters=parameters,
            requested_by="manager-user",
        )
        changed_authorization = self.authorizer.confirm(
            changed_plan.id,
            actor="manager-user",
            ttl_seconds=60,
        )
        changed = self.authorizer.consume(
            plan_id=changed_plan.id,
            authorization=changed_authorization,
            actor="manager-user",
            parameters={"work_item_alias": "DFHIS-CHANGED", "timeout_seconds": 5},
        )

        self.assertFalse(changed.allowed)
        self.assertEqual("parameter_hash_mismatch", changed.reason)

    def test_forbidden_database_mutation_and_model_dag_invocation_are_blocked(self) -> None:
        with self.assertRaisesRegex(ValueError, "forbidden_token:update"):
            validate_readonly_sql("UPDATE patient SET name = 'changed' WHERE id = 1")

        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="task10",
            provider="model",
            profile_key="fake-model",
            display_name="Fake Model",
            enabled=True,
            connection={"provider_kind": "openai_compatible", "model": "fake-model"},
        )
        plan_count = len(self.repository.list_action_plans())
        adapter = FakeReadAdapter()
        with self.assertRaisesRegex(ValueError, "provider_action_not_registered"):
            self.authorizer.create_plan(
                profile_id=profile.id,
                action="model.dag.run",
                target_alias="fake-model",
                parameters={"dag_alias": "forbidden-real-dag"},
                requested_by="manager-user",
            )

        self.assertEqual(0, adapter.calls)
        self.assertEqual(plan_count, len(self.repository.list_action_plans()))

    def test_secret_input_external_write_without_confirmation_and_auto_promotion_fail_closed(self) -> None:
        profile = self._yunxiao_profile()
        plan_count = len(self.repository.list_action_plans())
        sentinel = "Authorization: Bearer task10-secret-value-123456789"
        with self.assertRaises(ValueError) as raised:
            self.authorizer.create_plan(
                profile_id=profile.id,
                action="workitem.read",
                target_alias="org-fixture.dfhis-31333",
                parameters={"note": sentinel},
                requested_by="manager-user",
            )
        self.assertNotIn(sentinel, str(raised.exception))
        self.assertEqual(plan_count, len(self.repository.list_action_plans()))

        write_parameters = {
            "work_item_alias": "DFHIS-31333",
            "comment": {"business_logic": "safe summary"},
        }
        write_plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.comment.write",
            target_alias="org-fixture.dfhis-31333",
            parameters=write_parameters,
            requested_by="manager-user",
        )
        adapter = FakeReadAdapter()
        blocked = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": adapter},
        ).execute(
            None,
            ProviderExecutionRequest(
                plan_id=write_plan.id,
                actor="manager-user",
                action="workitem.comment.write",
                parameters=write_parameters,
            ),
        )
        self.assertEqual("blocked", blocked["status"])
        self.assertEqual("authorization_required", blocked["reason"])
        self.assertEqual(0, adapter.calls)

        candidates = LearningCandidateRepository()
        created = candidates.create_failed_run_candidates(
            self._failure_sample("task10-no-auto-promote"),
            source_action_audit_id=self._failed_action_audit(
                action_type="controlled.fake.auto-promote"
            ),
        )
        knowledge_candidate = next(
            item
            for item in created["candidates"]
            if item["candidate_type"] == "knowledge.candidate"
        )
        with self.assertRaisesRegex(PermissionError, "candidate_not_approved"):
            candidates.promote_knowledge_candidate(
                candidate_key=str(knowledge_candidate["candidate_key"]),
                reviewer_alias="reviewer-a",
                knowledge_home=self.knowledge_home,
                knowledge_allowed_base=self.root,
            )
        self.assertFalse(self.knowledge_home.exists())

    def test_delivery_docs_publish_required_operational_boundaries(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        design = (
            PROJECT_ROOT
            / "docs/superpowers/specs/2026-08-09-manager-provider-configuration-design.md"
        ).read_text(encoding="utf-8")
        runbook_path = PROJECT_ROOT / "docs/manager-runbook.md"
        self.assertTrue(runbook_path.is_file())
        runbook = runbook_path.read_text(encoding="utf-8")

        for phrase in (
            "能力边界",
            "HARNESS_MANAGER_CREDENTIAL_MASTER_KEY",
            "一次性确认",
            "三方合并",
            "暂存副本",
            "回滚",
            "数据库修改和删除默认绝对禁止",
            "当前没有数据库写 executor",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme + design + runbook)
        self.assertIn("/providers", runbook)
        self.assertIn("/actions", runbook)
        self.assertIn("/learning-candidates", runbook)
        self.assertIn("/knowledge", runbook)
        self.assertIn("/business-acceptance", runbook)
        self.assertIn("/routing", runbook)
        for phrase in (
            "自动意图路由",
            "需求模式在会话内粘滞",
            "普通问题优先查询知识库",
            "需求相关问题进入完整需求流程",
            "unlinked",
            "not_applicable",
            "数据库修改和删除默认绝对禁止",
        ):
            with self.subTest(routing_phrase=phrase):
                self.assertIn(phrase, readme + runbook)

        self.assertIn("Manager Provider 配置中心阶段 A（历史快照）", readme)
        self.assertIn("阶段 A 历史交付快照", design)
        for document in (readme, design):
            with self.subTest(document="current-bc-boundary"):
                self.assertIn("正常 Agent DAG 仍冻结", document)
                self.assertIn("真实调用", document)
                self.assertIn("一次性授权", document)
                self.assertIn("凭证", document)
                self.assertIn("外部验收", document)
                self.assertIn("外部写动作默认禁用", document)
                self.assertIn("数据库修改和删除默认绝对禁止", document)
                self.assertIn("当前不存在数据库写 executor", document)


if __name__ == "__main__":
    unittest.main()
