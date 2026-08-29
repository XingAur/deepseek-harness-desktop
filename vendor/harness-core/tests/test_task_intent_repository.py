from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


_TEMP_ROOT = tempfile.mkdtemp(prefix="harness-task-intent-repository-")
os.environ.setdefault("HARNESS_DB_PATH", str(Path(_TEMP_ROOT) / "manager.sqlite"))
os.environ.setdefault("HIS_KNOWLEDGE_HOME", str(Path(_TEMP_ROOT) / "his-knowledge"))

from app import database  # noqa: E402
from app.task_intent_repository import TaskIntentRepository  # noqa: E402
from app.task_intent_router import IntentContext, classify_task_intent  # noqa: E402


class TaskIntentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="harness-task-intent-repository-case-"
        )
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.repository = TaskIntentRepository()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_records_current_session_and_complete_append_only_event(self) -> None:
        decision = classify_task_intent(
            "这个需求为什么要这样改？",
            IntentContext(
                conversation_key="conversation-a",
                work_item_id="DFHIS-31351",
            ),
        )

        recorded = self.repository.record_decision(
            conversation_key="conversation-a",
            message="这个需求为什么要这样改？",
            decision=decision,
        )
        session = self.repository.get_session("conversation-a")
        events = self.repository.list_recent_events()

        self.assertEqual(session, recorded)
        self.assertEqual("task", session["mode"])
        self.assertEqual(["structured_work_item"], session["reason_codes"])
        self.assertEqual("high", session["confidence"])
        self.assertTrue(session["sticky"])
        self.assertEqual("DFHIS-31351", session["linked_work_item"])
        self.assertEqual("linked", session["yunxiao_status"])
        self.assertEqual("requirement_intake", session["current_phase"])
        self.assertEqual("requirement_workflow", session["next_route"])
        self.assertEqual(1, len(events))
        self.assertEqual("decision", events[0]["event_type"])
        self.assertIsNone(events[0]["previous_mode"])
        self.assertEqual("task", events[0]["mode"])
        self.assertEqual(["structured_work_item"], events[0]["reason_codes"])

    def test_get_and_verify_event_bind_decision_and_mutation_fact(self) -> None:
        decision = classify_task_intent(
            "请修改这个需求",
            IntentContext(conversation_key="conversation-receipt"),
        )
        recorded = self.repository.record_decision(
            conversation_key="conversation-receipt",
            message="请修改这个需求",
            decision=decision,
            mutation_requested=True,
        )

        event = self.repository.get_event(recorded["last_event_id"])
        verified = self.repository.verify_event(
            event_id=recorded["last_event_id"],
            decision=decision,
            mutation_requested=True,
        )

        self.assertEqual(event, verified)
        self.assertTrue(verified["mutation_requested"])
        with self.assertRaisesRegex(ValueError, "task_intent_receipt_invalid"):
            self.repository.verify_event(
                event_id=recorded["last_event_id"],
                decision=decision,
                mutation_requested=False,
            )

    def test_task_session_cannot_downgrade_without_explicit_correction(self) -> None:
        task = classify_task_intent(
            "这个需求为什么要这样改？",
            IntentContext(conversation_key="conversation-b"),
        )
        self.repository.record_decision(
            conversation_key="conversation-b",
            message="这个需求为什么要这样改？",
            decision=task,
        )
        unguarded_question = classify_task_intent(
            "Python 的装饰器是什么？",
            IntentContext(conversation_key="conversation-b"),
        )

        with self.assertRaisesRegex(
            ValueError, "task_intent_sticky_override_required"
        ):
            self.repository.record_decision(
                conversation_key="conversation-b",
                message="Python 的装饰器是什么？",
                decision=unguarded_question,
            )

        session = self.repository.get_session("conversation-b")
        self.assertEqual("task", session["mode"])
        self.assertTrue(session["sticky"])
        self.assertEqual(1, len(self.repository.list_recent_events()))

    def test_explicit_question_correction_releases_stickiness_and_is_audited(self) -> None:
        context = IntentContext(conversation_key="conversation-c")
        task = classify_task_intent("请处理这个需求", context)
        self.repository.record_decision(
            conversation_key="conversation-c",
            message="请处理这个需求",
            decision=task,
        )
        correction = classify_task_intent(
            "这只是普通咨询",
            context,
            previous_mode="task",
            explicit_override="question",
        )

        session = self.repository.record_decision(
            conversation_key="conversation-c",
            message="这只是普通咨询",
            decision=correction,
            explicit_override="question",
        )
        events = list(reversed(self.repository.list_recent_events()))

        self.assertEqual("question", session["mode"])
        self.assertFalse(session["sticky"])
        self.assertEqual("knowledge_retrieval", session["current_phase"])
        self.assertEqual("knowledge", session["next_route"])
        self.assertEqual(["decision", "explicit_correction"], [
            event["event_type"] for event in events
        ])
        self.assertEqual("task", events[-1]["previous_mode"])
        self.assertEqual("question", events[-1]["mode"])

    def test_message_summary_is_redacted_and_only_raw_sha256_is_persisted(self) -> None:
        bearer = "Bearer RoutingSecret9Qp4Lm2Nv8Bc6Zx7"
        opaque = "OpaqueRoutingToken7Ht5Rs3Wq9Yk2Mn8Vp6L"
        message = f"请定位 Authorization: {bearer}，补充值 {opaque}"
        decision = classify_task_intent(
            "请定位这个问题",
            IntentContext(conversation_key="conversation-d"),
        )

        self.repository.record_decision(
            conversation_key="conversation-d",
            message=message,
            decision=decision,
        )
        event = self.repository.list_recent_events(limit=1)[0]

        self.assertEqual(
            "sha256:" + hashlib.sha256(message.encode("utf-8")).hexdigest(),
            event["message_sha256"],
        )
        self.assertIn("REDACTED", event["message_summary"])
        persisted = json.dumps(event, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(bearer, persisted)
        self.assertNotIn(opaque, persisted)
        with database.connect() as connection:
            stored = "|".join(
                str(value)
                for value in connection.execute(
                    "select * from manager_task_intent_events"
                ).fetchone()
            )
        self.assertNotIn(bearer, stored)
        self.assertNotIn(opaque, stored)

    def test_event_rows_reject_update_and_delete(self) -> None:
        decision = classify_task_intent(
            "Python 的装饰器是什么？",
            IntentContext(conversation_key="conversation-e"),
        )
        self.repository.record_decision(
            conversation_key="conversation-e",
            message="Python 的装饰器是什么？",
            decision=decision,
        )

        with database.connect() as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append only"):
                connection.execute(
                    "update manager_task_intent_events set mode = 'task' where id = 1"
                )
        with database.connect() as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append only"):
                connection.execute("delete from manager_task_intent_events where id = 1")

    def test_insert_or_replace_cannot_rewrite_an_append_only_event(self) -> None:
        decision = classify_task_intent(
            "Python 的装饰器是什么？",
            IntentContext(conversation_key="conversation-replace"),
        )
        self.repository.record_decision(
            conversation_key="conversation-replace",
            message="Python 的装饰器是什么？",
            decision=decision,
        )
        with database.connect() as connection:
            original = tuple(
                connection.execute(
                    "select * from manager_task_intent_events where id = 1"
                ).fetchone()
            )

        with database.connect() as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append only"):
                connection.execute(
                    """
                    insert or replace into manager_task_intent_events(
                        id, conversation_key, event_type, previous_mode, mode,
                        reason_codes_json, confidence, sticky, linked_work_item,
                        yunxiao_status, current_phase, next_route,
                        message_summary, message_sha256, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "conversation-replace",
                        "decision",
                        "",
                        "task",
                        '["strong_task_text"]',
                        "high",
                        1,
                        "",
                        "unlinked",
                        "requirement_intake",
                        "requirement_workflow",
                        "tampered",
                        "sha256:" + "b" * 64,
                        database.now_iso(),
                    ),
                )

        with database.connect() as connection:
            unchanged = tuple(
                connection.execute(
                    "select * from manager_task_intent_events where id = 1"
                ).fetchone()
            )
        self.assertEqual(original, unchanged)

    def test_invalid_conversation_alias_is_rejected_without_storage(self) -> None:
        decision = classify_task_intent(
            "Python 的装饰器是什么？",
            IntentContext(),
        )

        for alias in (
            "",
            " conversation ",
            "Bearer secret-value",
            "OpaqueConversationSecret7Ht5Rs3Wq9Yk2Mn8Vp6L",
        ):
            with self.subTest(alias=alias):
                with self.assertRaisesRegex(ValueError, "task_intent_input_invalid"):
                    self.repository.record_decision(
                        conversation_key=alias,
                        message="Python 的装饰器是什么？",
                        decision=decision,
                    )
        self.assertEqual([], self.repository.list_recent_events())

    def test_all_opaque_credential_shapes_are_rejected_as_conversation_aliases(
        self,
    ) -> None:
        decision = classify_task_intent(
            "Python 的装饰器是什么？",
            IntentContext(),
        )
        opaque_values = (
            "QwertyuiopasdfghjklzxcvbnmASDFGH",
            "qwertyuiopasdfghjklzxcvbnmasdfgh",
            "12345678901234567890123456789012",
            "AbCdEfGhIjKlMnOpQrStUvWxYz012345+/=",
        )

        for opaque in opaque_values:
            with self.subTest(kind=opaque[:4]):
                with self.assertRaises(ValueError) as caught:
                    self.repository.record_decision(
                        conversation_key=opaque,
                        message="Python 的装饰器是什么？",
                        decision=decision,
                    )
                self.assertEqual("task_intent_input_invalid", str(caught.exception))
                self.assertNotIn(opaque, str(caught.exception))
        self.assertEqual([], self.repository.list_recent_events())

    def test_message_summary_redacts_every_opaque_credential_shape(self) -> None:
        opaque_values = (
            "QwertyuiopasdfghjklzxcvbnmASDFGH",
            "qwertyuiopasdfghjklzxcvbnmasdfgh",
            "12345678901234567890123456789012",
            "AbCdEfGhIjKlMnOpQrStUvWxYz012345+/=",
        )
        message = "请处理 " + " ".join(opaque_values)
        decision = classify_task_intent(
            "请处理这个需求",
            IntentContext(conversation_key="conv-opaque-summary"),
        )

        self.repository.record_decision(
            conversation_key="conv-opaque-summary",
            message=message,
            decision=decision,
        )
        event = self.repository.list_recent_events(limit=1)[0]

        for opaque in opaque_values:
            self.assertNotIn(opaque, event["message_summary"])
        self.assertGreaterEqual(event["message_summary"].count("REDACTED"), 4)

    def test_benign_alias_and_chinese_summary_are_not_over_redacted(self) -> None:
        message = "请检查住院结算流程，保持原有逻辑"
        decision = classify_task_intent(
            message,
            IntentContext(conversation_key="conversation-a"),
        )

        self.repository.record_decision(
            conversation_key="conversation-a",
            message=message,
            decision=decision,
        )
        event = self.repository.list_recent_events(limit=1)[0]

        self.assertEqual("conversation-a", event["conversation_key"])
        self.assertNotIn("REDACTED", event["message_summary"])
        self.assertIn("请检查住院结算流程", event["message_summary"])

    def test_historical_opaque_aliases_and_summaries_fail_closed(self) -> None:
        opaque_values = (
            "QwertyuiopasdfghjklzxcvbnmASDFGH",
            "qwertyuiopasdfghjklzxcvbnmasdfgh",
            "12345678901234567890123456789012",
            "AbCdEfGhIjKlMnOpQrStUvWxYz012345+/=",
        )
        for index, opaque in enumerate(opaque_values):
            for field_name in ("conversation_key", "message_summary"):
                with self.subTest(index=index, field_name=field_name):
                    database.DB_PATH = Path(self.temp_dir.name) / (
                        f"historical-{index}-{field_name}.sqlite"
                    )
                    repository = TaskIntentRepository()
                    values = {
                        "conversation_key": "conversation-history",
                        "message_summary": "历史摘要",
                    }
                    values[field_name] = opaque
                    with database.connect() as connection:
                        connection.execute(
                            """
                            insert into manager_task_intent_events(
                                conversation_key, event_type, previous_mode, mode,
                                reason_codes_json, confidence, sticky,
                                linked_work_item, yunxiao_status, current_phase,
                                next_route, message_summary, message_sha256,
                                created_at
                            ) values (?, 'decision', '', 'task',
                                      '["strong_task_text"]', 'high', 1, '',
                                      'unlinked', 'requirement_intake',
                                      'requirement_workflow', ?, ?, ?)
                            """,
                            (
                                values["conversation_key"],
                                values["message_summary"],
                                "sha256:" + "a" * 64,
                                database.now_iso(),
                            ),
                        )

                    with self.assertRaises(ValueError) as caught:
                        repository.list_recent_events()
                    self.assertEqual(
                        "task_intent_storage_invalid", str(caught.exception)
                    )
                    self.assertNotIn(opaque, str(caught.exception))

    def test_explicit_override_fact_must_be_supplied_bidirectionally(self) -> None:
        correction = classify_task_intent(
            "请进入需求流程",
            IntentContext(conversation_key="conversation-override"),
            explicit_override="task",
        )

        invalid_calls = (
            (correction, None),
            (correction, "question"),
            (
                replace(
                    correction,
                    reason_codes=("explicit_override", "strong_task_text"),
                ),
                "task",
            ),
        )
        for decision, override in invalid_calls:
            with self.subTest(override=override, reasons=decision.reason_codes):
                with self.assertRaises(ValueError) as caught:
                    self.repository.record_decision(
                        conversation_key="conversation-override",
                        message="请进入需求流程",
                        decision=decision,
                        explicit_override=override,
                    )
                self.assertEqual("task_intent_input_invalid", str(caught.exception))
        self.assertEqual([], self.repository.list_recent_events())

    def test_question_to_task_override_is_recorded_only_as_correction(self) -> None:
        context = IntentContext(conversation_key="conv-question-task")
        question = classify_task_intent("Python 的装饰器是什么？", context)
        self.repository.record_decision(
            conversation_key="conv-question-task",
            message="Python 的装饰器是什么？",
            decision=question,
        )
        correction = classify_task_intent(
            "请进入需求流程",
            context,
            previous_mode="question",
            explicit_override="task",
        )

        session = self.repository.record_decision(
            conversation_key="conv-question-task",
            message="请进入需求流程",
            decision=correction,
            explicit_override="task",
        )
        events = list(reversed(self.repository.list_recent_events()))

        self.assertEqual("task", session["mode"])
        self.assertEqual("explicit_correction", events[-1]["event_type"])
        self.assertEqual(["explicit_override"], events[-1]["reason_codes"])
        self.assertEqual("question", events[-1]["previous_mode"])

    def test_historical_stable_reason_and_relationship_pollution_fails_closed(
        self,
    ) -> None:
        polluted_rows = (
            {
                "event_type": "decision",
                "reason_codes_json": '["invented_reason"]',
                "linked_work_item": "",
                "yunxiao_status": "unlinked",
            },
            {
                "event_type": "decision",
                "reason_codes_json": '["strong_task_text"]',
                "linked_work_item": "DFHIS-31351",
                "yunxiao_status": "unlinked",
            },
            {
                "event_type": "decision",
                "reason_codes_json": '["explicit_override"]',
                "linked_work_item": "",
                "yunxiao_status": "unlinked",
            },
            {
                "event_type": "explicit_correction",
                "reason_codes_json": '["strong_task_text"]',
                "linked_work_item": "",
                "yunxiao_status": "unlinked",
            },
        )
        for index, polluted in enumerate(polluted_rows):
            with self.subTest(index=index):
                database.DB_PATH = Path(self.temp_dir.name) / f"semantic-{index}.sqlite"
                repository = TaskIntentRepository()
                with database.connect() as connection:
                    connection.execute(
                        """
                        insert into manager_task_intent_events(
                            conversation_key, event_type, previous_mode, mode,
                            reason_codes_json, confidence, sticky,
                            linked_work_item, yunxiao_status, current_phase,
                            next_route, message_summary, message_sha256,
                            created_at
                        ) values ('conversation-semantic', ?, '', 'task', ?,
                                  'high', 1, ?, ?, 'requirement_intake',
                                  'requirement_workflow', '历史摘要', ?, ?)
                        """,
                        (
                            polluted["event_type"],
                            polluted["reason_codes_json"],
                            polluted["linked_work_item"],
                            polluted["yunxiao_status"],
                            "sha256:" + "c" * 64,
                            database.now_iso(),
                        ),
                    )

                with self.assertRaises(ValueError) as caught:
                    repository.list_recent_events()
                self.assertEqual("task_intent_storage_invalid", str(caught.exception))

    def test_session_last_event_must_exist_belong_and_match_final_state(self) -> None:
        for pollution in ("missing", "other_conversation", "route_mismatch"):
            with self.subTest(pollution=pollution):
                database.DB_PATH = Path(self.temp_dir.name) / f"session-{pollution}.sqlite"
                repository = TaskIntentRepository()
                primary = classify_task_intent(
                    "请处理 DFHIS 需求",
                    IntentContext(
                        conversation_key="conversation-primary",
                        work_item_id="DFHIS-31351",
                    ),
                )
                repository.record_decision(
                    conversation_key="conversation-primary",
                    message="请处理 DFHIS 需求",
                    decision=primary,
                )
                other = classify_task_intent(
                    "Python 的装饰器是什么？",
                    IntentContext(conversation_key="conversation-other"),
                )
                repository.record_decision(
                    conversation_key="conversation-other",
                    message="Python 的装饰器是什么？",
                    decision=other,
                )
                with database.connect() as connection:
                    if pollution == "missing":
                        connection.execute(
                            """
                            update manager_task_intent_sessions
                            set last_event_id = 999999
                            where conversation_key = 'conversation-primary'
                            """
                        )
                    elif pollution == "other_conversation":
                        other_event_id = int(
                            connection.execute(
                                """
                                select last_event_id
                                from manager_task_intent_sessions
                                where conversation_key = 'conversation-other'
                                """
                            ).fetchone()[0]
                        )
                        connection.execute(
                            """
                            update manager_task_intent_sessions
                            set last_event_id = ?
                            where conversation_key = 'conversation-primary'
                            """,
                            (other_event_id,),
                        )
                    else:
                        connection.execute(
                            """
                            update manager_task_intent_sessions
                            set confidence = 'conservative'
                            where conversation_key = 'conversation-primary'
                            """
                        )

                with self.assertRaises(ValueError) as caught:
                    repository.get_session("conversation-primary")
                self.assertEqual("task_intent_storage_invalid", str(caught.exception))

    def test_historical_event_chain_previous_mode_mismatch_fails_closed(self) -> None:
        first = classify_task_intent(
            "Python 的装饰器是什么？",
            IntentContext(conversation_key="conversation-chain"),
        )
        self.repository.record_decision(
            conversation_key="conversation-chain",
            message="Python 的装饰器是什么？",
            decision=first,
        )
        second = classify_task_intent(
            "请处理这个需求",
            IntentContext(conversation_key="conversation-chain"),
        )
        self.repository.record_decision(
            conversation_key="conversation-chain",
            message="请处理这个需求",
            decision=second,
        )
        with database.connect() as connection:
            connection.execute(
                """
                insert into manager_task_intent_events(
                    conversation_key, event_type, previous_mode, mode,
                    reason_codes_json, confidence, sticky, linked_work_item,
                    yunxiao_status, current_phase, next_route,
                    message_summary, message_sha256, created_at
                ) values ('conversation-chain', 'decision', 'question', 'task',
                          '["strong_task_text"]', 'high', 1, '', 'unlinked',
                          'requirement_intake', 'requirement_workflow',
                          '继续处理', ?, ?)
                """,
                ("sha256:" + "d" * 64, database.now_iso()),
            )

        with self.assertRaises(ValueError) as caught:
            self.repository.list_recent_events()
        self.assertEqual("task_intent_storage_invalid", str(caught.exception))

    def test_historical_pollution_fails_closed_without_echoing_secret(self) -> None:
        decision = classify_task_intent(
            "请处理这个需求",
            IntentContext(conversation_key="conversation-f"),
        )
        self.repository.record_decision(
            conversation_key="conversation-f",
            message="请处理这个需求",
            decision=decision,
        )
        polluted = "Bearer HistoricalRoutingSecret8Mn4Vp6Lq2Xz7Ht5"
        with database.connect() as connection:
            connection.execute(
                "update manager_task_intent_sessions set reason_codes_json = ? where conversation_key = ?",
                (json.dumps([polluted]), "conversation-f"),
            )

        with self.assertRaises(ValueError) as caught:
            self.repository.get_session("conversation-f")
        self.assertEqual("task_intent_storage_invalid", str(caught.exception))
        self.assertNotIn(polluted, str(caught.exception))

        with database.connect() as connection:
            connection.execute(
                """
                insert into manager_task_intent_events(
                    conversation_key, event_type, previous_mode, mode,
                    reason_codes_json, confidence, sticky, linked_work_item,
                    yunxiao_status, current_phase, next_route,
                    message_summary, message_sha256, created_at
                ) values (?, 'decision', '', 'task', '[\"strong_task_text\"]',
                          'high', 0, '', 'unlinked', 'requirement_intake',
                          'requirement_workflow', ?, ?, ?)
                """,
                (
                    "conversation-f",
                    polluted,
                    "sha256:" + "a" * 64,
                    database.now_iso(),
                ),
            )

        with self.assertRaises(ValueError) as caught:
            self.repository.list_recent_events()
        self.assertEqual("task_intent_storage_invalid", str(caught.exception))
        self.assertNotIn(polluted, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
