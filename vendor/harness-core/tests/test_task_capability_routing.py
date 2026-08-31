from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace


_TEMP_ROOT = tempfile.mkdtemp(prefix="harness-task-capability-routing-import-")
os.environ.setdefault("HARNESS_DB_PATH", str(Path(_TEMP_ROOT) / "manager.sqlite"))
os.environ.setdefault("HIS_KNOWLEDGE_HOME", str(Path(_TEMP_ROOT) / "his-knowledge"))

from app import database  # noqa: E402
from app.task_capability_routing import route_task_capabilities  # noqa: E402
from app.task_intent_repository import TaskIntentRepository  # noqa: E402
from app.task_intent_router import IntentContext  # noqa: E402
from app.task_intent_service import TaskIntentService  # noqa: E402


class _CapabilityService:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def route(self, request: object) -> SimpleNamespace:
        self.requests.append(request)
        if request.capability == "database.inspect" and request.mode == "preview":
            data = {
                "plan": {
                    "status": "ready",
                    "selected_profile": "his-test",
                    "guard": {"status": "pass", "blockers": []},
                }
            }
        else:
            data = {}
        return SimpleNamespace(result={"status": "success", "data": data})


class TaskCapabilityRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.intent_service = TaskIntentService(TaskIntentRepository())

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_inquiry_only_task_routes_zero_mutation_or_external_write_actions(self) -> None:
        routing = self.intent_service.route(
            "这个需求会影响哪些路径？",
            IntentContext(conversation_key="capability-inquiry"),
        )
        service = _CapabilityService()

        status, events, blockers, results = route_task_capabilities(
            service,
            routing_result=routing,
            contract_ready=True,
            project_path="/tmp/project",
            expected_diff="diff --git a/a.py b/a.py\n",
            explicit_remote_delivery=True,
            delivery={"approved_plan_hash": "a" * 64},
            knowledge_candidate={"title": "candidate"},
            knowledge_provenance={"source": "review"},
            database_change={"operation": "update", "reason": "draft"},
        )

        self.assertEqual("success", status)
        self.assertEqual((), events)
        self.assertEqual((), blockers)
        self.assertEqual({}, results)
        self.assertEqual([], service.requests)

    def test_omitted_or_none_routing_receipt_fails_before_provider(self) -> None:
        service = _CapabilityService()

        with self.assertRaises(TypeError):
            route_task_capabilities(service, contract_ready=True)
        with self.assertRaisesRegex(
            ValueError,
            "task_capability_route_requires_requirement_workflow",
        ):
            route_task_capabilities(
                service,
                routing_result=None,
                contract_ready=True,
            )

        self.assertEqual([], service.requests)

    def test_unknown_cross_conversation_and_mutation_tampered_receipts_fail_closed(self) -> None:
        inquiry = self.intent_service.route(
            "这个需求会影响哪些路径？",
            IntentContext(conversation_key="receipt-task"),
        )
        forged_results = (
            replace(inquiry, event_id=inquiry.event_id + 999),
            replace(
                inquiry,
                decision=replace(
                    inquiry.decision,
                    conversation_key="receipt-other",
                ),
            ),
            replace(inquiry, mutation_requested=True),
        )

        for forged in forged_results:
            service = _CapabilityService()
            with self.subTest(forged=forged):
                with self.assertRaisesRegex(
                    ValueError,
                    "task_capability_route_requires_requirement_workflow",
                ):
                    route_task_capabilities(
                        service,
                        routing_result=forged,
                        contract_ready=True,
                        expected_diff="diff --git a/a.py b/a.py\n",
                        database_change={"operation": "update"},
                    )
                self.assertEqual([], service.requests)

    def test_stale_receipt_fails_before_provider(self) -> None:
        context = IntentContext(conversation_key="receipt-stale")
        stale = self.intent_service.route(
            "这个需求会影响哪些路径？",
            context,
        )
        latest = self.intent_service.route("继续分析影响", context)
        service = _CapabilityService()

        self.assertGreater(latest.event_id, stale.event_id)
        with self.assertRaisesRegex(
            ValueError,
            "task_capability_route_requires_requirement_workflow",
        ):
            route_task_capabilities(
                service,
                routing_result=stale,
                contract_ready=True,
                code_evidence_sufficient=False,
                database_inspect={"profile_key": "his-test", "sql": "select 1"},
            )

        self.assertEqual([], service.requests)

    def test_adversarial_inquiry_phrases_emit_no_change_or_mutation_requests(self) -> None:
        inquiries = (
            "请分析如何修改这个需求？",
            "如何实现这个需求？",
            "请说明怎么修复",
        )

        for index, message in enumerate(inquiries):
            routing = self.intent_service.route(
                message,
                IntentContext(conversation_key=f"capability-inquiry-{index}"),
            )
            service = _CapabilityService()
            route_task_capabilities(
                service,
                routing_result=routing,
                contract_ready=True,
                expected_diff="diff --git a/a.py b/a.py\n",
                explicit_remote_delivery=True,
                delivery={"approved_plan_hash": "a" * 64},
                database_change={"operation": "update"},
                knowledge_candidate={"title": "candidate"},
                knowledge_provenance={"source": "review"},
            )

            self.assertFalse(routing.mutation_requested)
            self.assertEqual([], service.requests)

    def test_question_cannot_enter_task_capability_routing(self) -> None:
        routing = self.intent_service.route(
            "Python 的装饰器是什么？",
            IntentContext(conversation_key="capability-question"),
        )
        service = _CapabilityService()

        with self.assertRaisesRegex(
            ValueError,
            "task_capability_route_requires_requirement_workflow",
        ):
            route_task_capabilities(
                service,
                routing_result=routing,
                contract_ready=True,
                expected_diff="diff --git a/a.py b/a.py\n",
            )

        self.assertEqual([], service.requests)

    def test_explicit_mutation_request_keeps_local_and_remote_confirmation_gates(self) -> None:
        routing = self.intent_service.route(
            "请修改并修复这个需求",
            IntentContext(conversation_key="capability-change"),
        )
        service = _CapabilityService()

        route_task_capabilities(
            service,
            routing_result=routing,
            contract_ready=True,
            project_path="/tmp/project",
            expected_diff="diff --git a/a.py b/a.py\n",
            explicit_remote_delivery=False,
            delivery={"approved_plan_hash": "a" * 64},
        )
        route_task_capabilities(
            service,
            routing_result=routing,
            contract_ready=True,
            explicit_remote_delivery=True,
            delivery={"approved_plan_hash": "a" * 64},
        )

        self.assertEqual(
            ["git.apply-local", "git.commit-local"],
            [request.capability for request in service.requests],
        )

    def test_database_capabilities_remain_readonly_or_change_plan_only(self) -> None:
        routing = self.intent_service.route(
            "请修改并修复这个需求",
            IntentContext(conversation_key="capability-database"),
        )
        service = _CapabilityService()

        route_task_capabilities(
            service,
            routing_result=routing,
            contract_ready=True,
            code_evidence_sufficient=False,
            database_inspect={"profile_key": "his-test", "sql": "select 1"},
            execute_database=True,
            database_change={"operation": "update", "reason": "plan only"},
        )

        self.assertEqual(
            ["database.inspect", "database.inspect", "database.change-plan"],
            [request.capability for request in service.requests],
        )
        self.assertNotIn(
            "database.change",
            {request.capability for request in service.requests},
        )
        execute = service.requests[1]
        self.assertEqual(
            ("database:metadata:read", "database:rows:read"),
            execute.authorization.scope,
        )


if __name__ == "__main__":
    unittest.main()
