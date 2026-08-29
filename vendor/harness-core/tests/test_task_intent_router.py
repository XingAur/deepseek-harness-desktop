from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


_TEMP_ROOT = tempfile.mkdtemp(prefix="harness-task-intent-router-")
os.environ.setdefault("HARNESS_DB_PATH", str(Path(_TEMP_ROOT) / "manager.sqlite"))
os.environ.setdefault("HIS_KNOWLEDGE_HOME", str(Path(_TEMP_ROOT) / "his-knowledge"))

from app.task_intent_router import (  # noqa: E402
    IntentContext,
    classify_task_intent,
)


class TaskIntentRouterTests(unittest.TestCase):
    def test_general_knowledge_question_routes_to_knowledge(self) -> None:
        decision = classify_task_intent(
            "Python 的装饰器是什么？",
            IntentContext(conversation_key="conversation-a"),
        )

        self.assertEqual("question", decision.mode)
        self.assertEqual(("general_knowledge_question",), decision.reason_codes)
        self.assertEqual("high", decision.confidence)
        self.assertFalse(decision.sticky)
        self.assertEqual("conversation-a", decision.conversation_key)
        self.assertIsNone(decision.linked_work_item)
        self.assertEqual("not_applicable", decision.yunxiao_status)
        self.assertEqual("knowledge_retrieval", decision.current_phase)
        self.assertEqual("knowledge", decision.next_route)

    def test_database_git_and_yunxiao_nouns_alone_are_not_task_evidence(self) -> None:
        for message in ("数据库索引是什么？", "Git rebase 是什么？", "云效是什么？"):
            with self.subTest(message=message):
                decision = classify_task_intent(message, IntentContext())

                self.assertEqual("question", decision.mode)
                self.assertEqual("knowledge", decision.next_route)

    def test_valid_structured_work_item_routes_to_task(self) -> None:
        decision = classify_task_intent(
            "请帮我看看",
            IntentContext(conversation_key="conversation-b", work_item_id="DFHIS-31351"),
        )

        self.assertEqual("task", decision.mode)
        self.assertEqual(("structured_work_item",), decision.reason_codes)
        self.assertEqual("DFHIS-31351", decision.linked_work_item)
        self.assertEqual("linked", decision.yunxiao_status)
        self.assertEqual("requirement_intake", decision.current_phase)
        self.assertEqual("requirement_workflow", decision.next_route)

    def test_requirement_question_without_yunxiao_stays_in_task_workflow(self) -> None:
        decision = classify_task_intent("这个需求为什么要这样改？", IntentContext())

        self.assertEqual("task", decision.mode)
        self.assertIn("strong_task_text", decision.reason_codes)
        self.assertIsNone(decision.linked_work_item)
        self.assertEqual("unlinked", decision.yunxiao_status)
        self.assertEqual("requirement_workflow", decision.next_route)

    def test_task_session_is_sticky_for_followup_general_question(self) -> None:
        decision = classify_task_intent(
            "这个字段是什么意思？",
            IntentContext(),
            previous_mode="task",
        )

        self.assertEqual("task", decision.mode)
        self.assertEqual(("sticky_task_session",), decision.reason_codes)
        self.assertTrue(decision.sticky)
        self.assertEqual("requirement_workflow", decision.next_route)

    def test_provider_and_yunxiao_failure_do_not_downgrade_task(self) -> None:
        decision = classify_task_intent(
            "这个 BUG 怎么定位？",
            IntentContext(provider_available=False, yunxiao_lookup_failed=True),
        )

        self.assertEqual("task", decision.mode)
        self.assertEqual("lookup_failed", decision.yunxiao_status)
        self.assertEqual("requirement_workflow", decision.next_route)

    def test_question_punctuation_does_not_hide_task_intent(self) -> None:
        decision = classify_task_intent("这次验收需要验证哪些场景？", IntentContext())

        self.assertEqual("task", decision.mode)
        self.assertIn("strong_task_text", decision.reason_codes)

    def test_troubleshooting_questions_are_not_downgraded_by_general_interrogatives(
        self,
    ) -> None:
        for message in (
            "这个页面为什么打不开？",
            "接口怎么返回 500？",
            "这个BUG为什么会出现？",
        ):
            with self.subTest(message=message):
                decision = classify_task_intent(message, IntentContext())

                self.assertEqual("task", decision.mode)
                self.assertNotEqual(
                    ("general_knowledge_question",), decision.reason_codes
                )

    def test_error_and_failure_questions_are_not_downgraded_by_definition_words(
        self,
    ) -> None:
        for message in (
            "这个错误是什么原因？",
            "这个错误是什么？",
            "接口 500 是什么问题？",
            "服务异常是什么情况？",
            "页面打不开是什么原因？",
            "请求失败是什么问题？",
            "调用报错是什么原因？",
            "状态码 404 是什么问题？",
        ):
            with self.subTest(message=message):
                decision = classify_task_intent(message, IntentContext())

                self.assertEqual("task", decision.mode)
                self.assertNotEqual(
                    ("general_knowledge_question",), decision.reason_codes
                )

    def test_clear_technical_definitions_with_error_terms_route_to_knowledge(self) -> None:
        for message in (
            "Python 的异常是什么？",
            "HTTP 状态码是什么？",
            "什么是错误处理？",
        ):
            with self.subTest(message=message):
                decision = classify_task_intent(message, IntentContext())

                self.assertEqual("question", decision.mode)
                self.assertEqual(
                    ("general_knowledge_question",), decision.reason_codes
                )

    def test_clear_definitions_with_failure_terms_route_to_knowledge(self) -> None:
        for message in (
            "HTTP 404 是什么？",
            "什么是请求失败？",
            "系统故障是什么？",
        ):
            with self.subTest(message=message):
                decision = classify_task_intent(message, IntentContext())

                self.assertEqual("question", decision.mode)
                self.assertEqual(
                    ("general_knowledge_question",), decision.reason_codes
                )

    def test_concrete_diagnostic_context_routes_to_task(self) -> None:
        for message in (
            "这个错误是什么原因？",
            "接口 500 是什么问题？",
            "页面打不开",
            "请求失败",
            "发生异常",
            "系统故障",
            "提示报错",
            "返回状态码500",
        ):
            with self.subTest(message=message):
                decision = classify_task_intent(message, IntentContext())

                self.assertEqual("task", decision.mode)
                self.assertEqual(
                    ("troubleshooting_task_text",), decision.reason_codes
                )

    def test_invalid_structured_metadata_is_not_high_confidence_task_evidence(
        self,
    ) -> None:
        for context in (
            IntentContext(work_item_id="not-an-id"),
            IntentContext(current_phase="knowledge_retrieval"),
        ):
            with self.subTest(context=context):
                decision = classify_task_intent("Python 的装饰器是什么？", context)

                self.assertEqual("question", decision.mode)
                self.assertEqual(
                    ("general_knowledge_question",), decision.reason_codes
                )
                self.assertIsNone(decision.linked_work_item)
                self.assertEqual("not_applicable", decision.yunxiao_status)

    def test_invalid_work_item_falls_back_conservatively_without_a_false_link(
        self,
    ) -> None:
        decision = classify_task_intent(
            "帮我看一下这个情况",
            IntentContext(work_item_id="not-an-id"),
        )

        self.assertEqual("task", decision.mode)
        self.assertEqual(("conservative_task_fallback",), decision.reason_codes)
        self.assertIsNone(decision.linked_work_item)
        self.assertEqual("unlinked", decision.yunxiao_status)

    def test_invalid_work_item_does_not_claim_structured_work_item_reason(
        self,
    ) -> None:
        decision = classify_task_intent(
            "请继续处理",
            IntentContext(
                work_item_id="not-an-id",
                current_phase="requirement_intake",
            ),
        )

        self.assertEqual("task", decision.mode)
        self.assertEqual(("structured_task_context",), decision.reason_codes)
        self.assertIsNone(decision.linked_work_item)
        self.assertEqual("unlinked", decision.yunxiao_status)

    def test_conversation_key_is_a_validated_non_sensitive_alias(self) -> None:
        for conversation_key in ("", "Bearer secret-token"):
            with self.subTest(conversation_key=conversation_key):
                decision = classify_task_intent(
                    "Python 的装饰器是什么？",
                    IntentContext(conversation_key=conversation_key),
                )

                self.assertIsNone(decision.conversation_key)

    def test_ambiguous_task_like_input_is_conservatively_a_task(self) -> None:
        decision = classify_task_intent("帮我看一下这个情况", IntentContext())

        self.assertEqual("task", decision.mode)
        self.assertEqual(("conservative_task_fallback",), decision.reason_codes)
        self.assertEqual("conservative", decision.confidence)

    def test_explicit_override_is_the_only_way_to_correct_sticky_task_mode(self) -> None:
        decision = classify_task_intent(
            "这只是普通咨询，Python 的装饰器是什么？",
            IntentContext(),
            previous_mode="task",
            explicit_override="question",
        )

        self.assertEqual("question", decision.mode)
        self.assertEqual(("explicit_override",), decision.reason_codes)
        self.assertFalse(decision.sticky)
        self.assertEqual("knowledge", decision.next_route)

    def test_invalid_explicit_override_is_rejected_even_for_sticky_task_mode(
        self,
    ) -> None:
        for explicit_override in (" question ", "consultation", "", 1, []):
            with self.subTest(explicit_override=explicit_override):
                with self.assertRaises(ValueError):
                    classify_task_intent(
                        "Python 的装饰器是什么？",
                        IntentContext(),
                        previous_mode="task",
                        explicit_override=explicit_override,
                    )

    def test_invalid_public_inputs_are_rejected_deterministically(self) -> None:
        with self.assertRaises(ValueError):
            classify_task_intent(1, IntentContext())

        with self.assertRaises(ValueError):
            classify_task_intent("Python 的装饰器是什么？", object())

        for previous_mode in (" task ", "consultation", 1):
            with self.subTest(previous_mode=previous_mode):
                with self.assertRaises(ValueError):
                    classify_task_intent(
                        "Python 的装饰器是什么？",
                        IntentContext(),
                        previous_mode=previous_mode,
                    )


if __name__ == "__main__":
    unittest.main()
