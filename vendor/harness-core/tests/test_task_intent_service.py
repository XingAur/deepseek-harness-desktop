from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_TEMP_ROOT = tempfile.mkdtemp(prefix="harness-task-intent-service-import-")
os.environ.setdefault("HARNESS_DB_PATH", str(Path(_TEMP_ROOT) / "manager.sqlite"))
os.environ.setdefault("HIS_KNOWLEDGE_HOME", str(Path(_TEMP_ROOT) / "his-knowledge"))

from app import database  # noqa: E402
from app.task_intent_repository import TaskIntentRepository  # noqa: E402
from app.task_intent_router import IntentContext, classify_task_intent  # noqa: E402
from app.task_intent_service import TaskIntentService  # noqa: E402


class _OrderedRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_session(self, conversation_key: str) -> dict[str, object]:
        self.calls.append(f"get:{conversation_key}")
        return {"mode": "task"}

    def record_decision(self, **values: object) -> dict[str, object]:
        self.calls.append(f"record:{values['conversation_key']}")
        return {"last_event_id": 7}


class TaskIntentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.service = TaskIntentService(TaskIntentRepository())

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_question_is_classified_persisted_and_routed_to_knowledge(self) -> None:
        result = self.service.route(
            "Python 的装饰器是什么？",
            IntentContext(conversation_key="conversation-question"),
        )

        self.assertEqual("question", result.decision.mode)
        self.assertEqual("knowledge", result.decision.next_route)
        self.assertFalse(result.mutation_requested)
        session = TaskIntentRepository().get_session("conversation-question")
        self.assertEqual("question", session["mode"])
        self.assertEqual(result.event_id, session["last_event_id"])

    def test_requirement_question_stays_task_when_providers_are_unavailable(self) -> None:
        result = self.service.route(
            "这个需求为什么要这样改？",
            IntentContext(
                conversation_key="conversation-task",
                provider_available=False,
                yunxiao_lookup_failed=True,
            ),
        )

        self.assertEqual("task", result.decision.mode)
        self.assertEqual("requirement_workflow", result.decision.next_route)
        self.assertEqual("lookup_failed", result.decision.yunxiao_status)
        self.assertFalse(result.mutation_requested)

    def test_existing_task_session_is_read_before_classification_and_recording(self) -> None:
        repository = _OrderedRepository()
        service = TaskIntentService(repository)
        real_classifier = classify_task_intent

        def classify_with_trace(*args: object, **kwargs: object):
            repository.calls.append("classify")
            self.assertEqual("task", kwargs["previous_mode"])
            return real_classifier(*args, **kwargs)

        with mock.patch(
            "app.task_intent_service.classify_task_intent",
            side_effect=classify_with_trace,
        ):
            result = service.route(
                "这个字段是什么意思？",
                IntentContext(conversation_key="conversation-sticky"),
            )

        self.assertEqual(
            ["get:conversation-sticky", "classify", "record:conversation-sticky"],
            repository.calls,
        )
        self.assertEqual("task", result.decision.mode)
        self.assertTrue(result.decision.sticky)

    def test_only_explicit_change_language_marks_mutation_requested(self) -> None:
        inquiry = self.service.route(
            "这个需求会影响哪些路径？",
            IntentContext(conversation_key="conversation-inquiry"),
        )
        change = self.service.route(
            "请修改并修复这个需求",
            IntentContext(conversation_key="conversation-change"),
        )

        self.assertFalse(inquiry.mutation_requested)
        self.assertTrue(change.mutation_requested)

    def test_direct_imperative_is_mutation_but_change_inquiry_is_not(self) -> None:
        inquiry = self.service.route(
            "这个需求需要修改哪些地方？",
            IntentContext(conversation_key="change-inquiry"),
        )
        direct = self.service.route(
            "修复这个需求",
            IntentContext(conversation_key="direct-change"),
        )

        self.assertFalse(inquiry.mutation_requested)
        self.assertTrue(direct.mutation_requested)

    def test_analysis_and_how_to_phrasing_never_requests_mutation(self) -> None:
        inquiries = (
            "请分析如何修改这个需求？",
            "如何实现这个需求？",
            "请说明怎么修复",
            "帮我看看应该怎么改？",
            "请做一下这个需求的影响分析",
        )

        for index, message in enumerate(inquiries):
            with self.subTest(message=message):
                result = self.service.route(
                    message,
                    IntentContext(conversation_key=f"inquiry-{index}"),
                )
                self.assertFalse(result.mutation_requested)

    def test_unambiguous_action_commands_request_mutation(self) -> None:
        commands = ("请修改这个需求", "开始修复这个需求", "直接实现这个需求")

        for index, message in enumerate(commands):
            with self.subTest(message=message):
                result = self.service.route(
                    message,
                    IntentContext(conversation_key=f"command-{index}"),
                )
                self.assertTrue(result.mutation_requested)

    def test_service_persists_mutation_fact_on_the_same_append_only_event(self) -> None:
        result = self.service.route(
            "请修改这个需求",
            IntentContext(conversation_key="persisted-mutation"),
        )

        event = TaskIntentRepository().get_event(result.event_id)

        self.assertEqual(result.event_id, event["id"])
        self.assertEqual("persisted-mutation", event["conversation_key"])
        self.assertTrue(event["mutation_requested"])


if __name__ == "__main__":
    unittest.main()
