from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest import mock

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.business_acceptance_repository import BusinessAcceptanceRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.task_intent_router import IntentContext
from app.task_intent_service import TaskIntentService
from app.server import (
    HarnessRequestHandler,
    _MANAGER_FORM_CSRF_TOKEN,
    build_manager_business_acceptance_status,
    build_manager_routing_status,
    dispatch_manager_message,
    render_actions_page,
    render_routing_page,
)


class ServerCoreStatusApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager_temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.manager_temp_dir.name) / "manager.sqlite"
        self.assertNotEqual(
            Path(__file__).resolve().parents[1] / "data" / "harness.sqlite",
            database.DB_PATH,
        )
        self.legacy_profile_store = Path(self.manager_temp_dir.name) / "legacy-profiles.json"
        self.connection_audit_path = Path(self.manager_temp_dir.name) / "connection-tests.jsonl"
        self.readonly_audit_path = Path(self.manager_temp_dir.name) / "readonly-smoke.jsonl"
        self.knowledge_home = Path(self.manager_temp_dir.name) / "his-knowledge"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HARNESS_DB_PATH": str(database.DB_PATH),
                "HARNESS_PROVIDER_PROFILE_STORE": str(self.legacy_profile_store),
                "HARNESS_PROVIDER_CONNECTION_TEST_AUDIT": str(self.connection_audit_path),
                "HIS_KNOWLEDGE_HOME": str(self.knowledge_home),
                "HARNESS_CODE_EVIDENCE_REVIEWER_ENABLED": "1",
            },
            clear=False,
        )
        self.environment.start()
        self.code_evidence_service = mock.Mock()
        self.code_evidence_service.review_changes.return_value = {
            "status": "approved",
            "evidence_set": {"evidence_set_id": 1, "repository_count": 1},
            "repositories": [{"repository_alias": "harness", "status": "approved"}],
        }
        self.code_evidence_service.inspect.return_value = {
            "status": "complete",
            "repositories": [{"repository_alias": "harness", "status": "complete"}],
        }
        self.code_evidence_configuration = mock.patch(
            "app.server._manager_code_evidence_configuration",
            return_value=(
                self.code_evidence_service,
                ("harness",),
                {"harness": (("/usr/bin/true",),)},
            ),
        )
        self.code_evidence_configuration.start()
        database.init_db()

    def tearDown(self) -> None:
        self.code_evidence_configuration.stop()
        self.environment.stop()
        database.DB_PATH = self.previous_db_path
        self.manager_temp_dir.cleanup()

    def _start_server(self) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        return server

    @staticmethod
    def _post_form(
        server: ThreadingHTTPServer,
        path: str,
        fields: dict[str, str],
        *,
        csrf_token: str | None = _MANAGER_FORM_CSRF_TOKEN,
        origin: str | None = "auto",
        cookie: str | None = None,
    ) -> tuple[int, object, bytes]:
        import http.client

        payload = dict(fields)
        protected = path in {
            "/providers",
            "/providers/credentials",
            "/runs",
            "/knowledge/consult",
            "/routing/classify",
            "/actions/plans",
            "/actions/confirm",
            "/learning-candidates/review",
            "/business-acceptance/evidence",
            "/business-acceptance/decisions",
            "/api/provider-profiles/test-connection",
            "/api/provider-profiles/readonly-smoke",
        }
        if protected and csrf_token is not None:
            payload["_csrf_token"] = csrf_token
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie is not None:
            headers["Cookie"] = cookie
        if protected and origin == "auto":
            headers["Origin"] = f"http://127.0.0.1:{server.server_port}"
        elif protected and origin is not None:
            headers["Origin"] = origin
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(
            "POST",
            path,
            body=urllib.parse.urlencode(payload).encode("utf-8"),
            headers=headers,
        )
        response = connection.getresponse()
        result = (response.status, response.headers, response.read())
        connection.close()
        return result

    @staticmethod
    def _get_json(server: ThreadingHTTPServer, path: str) -> dict:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}{path}", timeout=5
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_manager_provider_form_saves_model_config_and_never_returns_api_key(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_MANAGER_MODEL_API_KEY"

        with mock.patch.dict(
            os.environ,
            {
                "HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": (
                    "bW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW0="
                )
            },
            clear=False,
        ):
            response_status, response_headers, _ = self._post_form(
                server,
                "/providers",
                {
                    "provider": "model",
                    "profile_key": "demo",
                    "display_name": "Demo",
                    "enabled": "on",
                    "provider_kind": "openai_compatible",
                    "base_url": "https://api.example.test/v1",
                    "model": "demo-model",
                    "api_key": sentinel,
                },
            )
            payload = self._get_json(server, "/api/manager/providers")

        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(303, response_status)
        self.assertEqual("/providers?saved=1", response_headers["Location"])
        self.assertEqual("his-manager-provider-status.v1", payload["schema_version"])
        self.assertEqual(["model"], [item["provider"] for item in payload["profiles"]])
        self.assertEqual("configured", payload["profiles"][0]["credentials"]["api_key"])
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("ciphertext", rendered.lower())

    def test_knowledge_consult_page_and_api_use_temporary_verified_index_without_model(self) -> None:
        server = self._start_server()

        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/knowledge", timeout=5
        ) as page_response:
            page = page_response.read().decode("utf-8")
        with mock.patch(
            "app.server.consult_knowledge",
            return_value={
                "schema_version": "his-knowledge-consultation.v1",
                "answerable": True,
                "model_used": False,
                "retrieval_status": "knowledge_hit",
                "citations": ["knowledge:approved"],
            },
        ) as consultation:
            status, _, body = self._post_form(
                server,
                "/knowledge/consult",
                {"query": "收费安全规则是什么？"},
            )
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(200, status)
        self.assertIn('action="/knowledge/consult"', page)
        self.assertIn("本地知识咨询", page)
        self.assertTrue(payload["answerable"])
        self.assertFalse(payload["model_used"])
        self.assertEqual("knowledge_hit", payload["retrieval_status"])
        self.assertEqual(["knowledge:approved"], payload["citations"])
        self.assertNotIn("knowledge_home", consultation.call_args.kwargs)

    def test_routing_page_is_automatic_and_correction_is_optional(self) -> None:
        page = render_routing_page()

        self.assertIn("自动意图路由", page)
        self.assertIn('action="/routing/classify"', page)
        self.assertIn('name="message"', page)
        self.assertIn('name="conversation_key"', page)
        self.assertIn('name="work_item_id"', page)
        self.assertIn('name="current_phase"', page)
        self.assertIn('name="explicit_override"', page)
        self.assertNotIn('name="mode"', page)
        self.assertNotIn('name="explicit_override" required', page)
        self.assertIn("普通问题优先查询知识库", page)
        self.assertIn("需求相关问题进入完整需求流程", page)
        for label in ("模式", "判断原因", "工作项", "云效状态", "当前阶段", "下一路由", "修改请求"):
            self.assertIn(label, page)

    def test_routing_status_only_returns_safe_latest_session_and_event_fields(self) -> None:
        sentinel = "SENTINEL_ROUTING_AUTHORIZATION_SECRET"
        TaskIntentService().route(
            f"Python 的装饰器是什么？ authorization=Bearer {sentinel}",
            IntentContext(conversation_key="manager-routing-safe"),
        )

        payload = build_manager_routing_status()
        rendered = json.dumps(payload, ensure_ascii=False)

        self.assertEqual("his-manager-routing-status.v1", payload["schema_version"])
        self.assertEqual("ready", payload["status"])
        self.assertEqual(1, len(payload["conversations"]))
        self.assertEqual(1, len(payload["events"]))
        latest = payload["conversations"][0]
        self.assertEqual("manager-routing-safe", latest["conversation_key"])
        self.assertEqual("question", latest["mode"])
        self.assertEqual("knowledge", latest["next_route"])
        self.assertNotIn("message_summary", latest)
        self.assertNotIn("message_sha256", latest)
        self.assertNotIn("authorization", rendered.lower())
        self.assertNotIn(sentinel, rendered)

    def test_routing_http_classifies_automatically_and_explicit_correction_is_audited(self) -> None:
        server = self._start_server()
        fake_result = mock.Mock(
            run_id=31,
            status="success",
            evaluation_status="pass",
            orchestration_events=tuple(
                {"stage": f"stage-{index}", "status": "completed", "reason_code": "ok"}
                for index in range(12)
            ),
        )

        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/routing", timeout=5
        ) as page_response:
            page = page_response.read().decode("utf-8")
        with (
            mock.patch("app.server.RequirementWorkflowRunner") as runner_type,
            mock.patch(
                "app.server.consult_knowledge",
                return_value={
                    "schema_version": "his-knowledge-consultation.v1",
                    "answerable": False,
                    "results": [],
                    "citations": [],
                },
            ) as knowledge,
        ):
            runner_type.return_value.run.return_value = fake_result
            first_status, _, first_body = self._post_form(
                server,
                "/routing/classify",
                {
                    "message": "这个需求为什么要这样改？",
                    "conversation_key": "manager-routing-task",
                    "work_item_id": "DFHIS-31333",
                    "current_phase": "requirement_intake",
                },
            )
            correction_status, _, correction_body = self._post_form(
                server,
                "/routing/classify",
                {
                    "message": "这只是普通咨询",
                    "conversation_key": "manager-routing-task",
                    "explicit_override": "question",
                },
            )
        first = json.loads(first_body.decode("utf-8"))
        correction = json.loads(correction_body.decode("utf-8"))
        state = self._get_json(server, "/api/manager/routing")

        self.assertEqual(200, first_status)
        self.assertEqual("task", first["mode"])
        self.assertEqual("DFHIS-31333", first["linked_work_item"])
        self.assertEqual("linked", first["yunxiao_status"])
        self.assertEqual("requirement_workflow", first["next_route"])
        self.assertEqual("requirement_workflow", first["downstream"])
        self.assertEqual(12, first["workflow"]["stage_count"])
        self.assertFalse(first["mutation_requested"])
        self.assertEqual(1, runner_type.return_value.run.call_count)
        self.assertEqual(1, knowledge.call_count)
        self.assertEqual(200, correction_status)
        self.assertEqual("question", correction["mode"])
        self.assertTrue(correction["explicit_correction"])
        self.assertEqual("explicit_correction", state["events"][0]["event_type"])
        self.assertIn("自动意图路由", page)
        self.assertNotIn("这个需求为什么要这样改？", json.dumps(state, ensure_ascii=False))

    def test_unified_dispatch_calls_only_the_selected_downstream(self) -> None:
        knowledge_payload = {
            "schema_version": "his-knowledge-consultation.v1",
            "answerable": True,
            "model_used": False,
            "results": [{"citation": "vault/approved.md", "excerpt": "安全答案"}],
            "citations": ["vault/approved.md"],
        }
        fake_runner = mock.Mock()
        with mock.patch(
            "app.server.consult_knowledge", return_value=knowledge_payload
        ) as knowledge:
            result = dispatch_manager_message(
                "Python 的装饰器是什么？",
                IntentContext(conversation_key="manager-uq"),
                workflow_runner=fake_runner,
                enforce_code_evidence=False,
            )

        self.assertEqual("knowledge", result["downstream"])
        self.assertEqual(knowledge_payload, result["knowledge"])
        self.assertEqual(1, knowledge.call_count)
        self.assertNotIn("knowledge_home", knowledge.call_args.kwargs)
        fake_runner.run.assert_not_called()

        twelve_events = tuple(
            {"stage": f"stage-{index}", "status": "completed", "reason_code": "ok"}
            for index in range(12)
        )
        fake_runner.run.return_value = mock.Mock(
            run_id=41,
            status="success",
            evaluation_status="pass",
            orchestration_events=twelve_events,
        )
        with mock.patch("app.server.consult_knowledge") as knowledge:
            task_result = dispatch_manager_message(
                "这个需求会影响哪些路径？",
                IntentContext(conversation_key="manager-ut"),
                workflow_runner=fake_runner,
                enforce_code_evidence=False,
            )

        self.assertEqual("requirement_workflow", task_result["downstream"])
        self.assertEqual(12, task_result["workflow"]["stage_count"])
        self.assertFalse(task_result["mutation_requested"])
        knowledge.assert_not_called()
        call = fake_runner.run.call_args
        self.assertEqual("readonly", call.kwargs["execution_mode"])
        self.assertEqual(task_result["event_id"], call.kwargs["routing_result"].event_id)

    def test_default_manager_task_dispatch_runs_local_twelve_stage_governance(self) -> None:
        with (
            mock.patch.dict(os.environ, {"HARNESS_LLM_MODE": "mock"}, clear=False),
            mock.patch(
                "app.llm_client.load_local_llm_credentials_env_if_available",
                side_effect=AssertionError("local Manager governance must not read model credentials"),
            ) as credential_loader,
        ):
            result = dispatch_manager_message(
                "请分析并完成一个需求：只读梳理 Harness 自动路由的现有流程，不修改任何代码。",
                IntentContext(conversation_key="manager-gov"),
                enforce_code_evidence=False,
            )

        workflow = result["workflow"]
        self.assertEqual("requirement_workflow", result["downstream"])
        self.assertEqual(12, workflow["stage_count"])
        self.assertEqual("local_deterministic", workflow["analysis_backend"])
        self.assertTrue(workflow["technical_only"])
        self.assertFalse(workflow["real_model_used"])
        self.assertFalse(workflow["business_valid"])
        self.assertFalse(result["external_calls"])
        self.assertFalse(result["write_performed"])
        credential_loader.assert_not_called()

    def test_blank_conversation_uses_server_cookie_and_preserves_sticky_task(self) -> None:
        server = self._start_server()
        fake_result = mock.Mock(
            run_id=51,
            status="success",
            evaluation_status="pass",
            orchestration_events=tuple(
                {"stage": f"stage-{index}", "status": "completed", "reason_code": "ok"}
                for index in range(12)
            ),
        )
        with mock.patch("app.server.RequirementWorkflowRunner") as runner_type:
            runner_type.return_value.run.return_value = fake_result
            first_status, first_headers, first_body = self._post_form(
                server,
                "/routing/classify",
                {"message": "这个需求为什么要这样改？"},
            )
            cookie = first_headers["Set-Cookie"].split(";", 1)[0]
            second_status, _, second_body = self._post_form(
                server,
                "/routing/classify",
                {"message": "Python 的装饰器是什么？"},
                cookie=cookie,
            )

        first = json.loads(first_body.decode("utf-8"))
        second = json.loads(second_body.decode("utf-8"))
        self.assertEqual(200, first_status)
        self.assertEqual(200, second_status)
        self.assertEqual(first["conversation_key"], second["conversation_key"])
        self.assertEqual("task", second["mode"])
        self.assertEqual(["sticky_task_session"], second["reason_codes"])
        self.assertEqual(2, runner_type.return_value.run.call_count)

    def test_routing_classification_requires_csrf_before_event_and_never_echoes_secret(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_ROUTING_RAW_SECRET"

        with (
            mock.patch(
                "app.server.consult_knowledge",
                return_value={
                    "schema_version": "his-knowledge-consultation.v1",
                    "answerable": False,
                    "results": [],
                    "citations": [],
                },
            ) as knowledge,
            mock.patch("app.server.RequirementWorkflowRunner") as runner_type,
        ):
            rejected_status, _, _ = self._post_form(
                server,
                "/routing/classify",
                {"message": "什么是装饰器？", "conversation_key": "csrf-routing"},
                csrf_token=None,
            )
            self.assertEqual([], build_manager_routing_status()["events"])
            knowledge.assert_not_called()
            runner_type.assert_not_called()
            accepted_status, _, body = self._post_form(
                server,
                "/routing/classify",
                {
                    "message": f"什么是装饰器？ token={sentinel}",
                    "conversation_key": "csrf-routing",
                },
            )
        payload = json.loads(body.decode("utf-8"))
        state = self._get_json(server, "/api/manager/routing")
        raw_manager_storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )

        self.assertEqual(403, rejected_status)
        self.assertEqual(200, accepted_status)
        self.assertNotIn(sentinel, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(sentinel, json.dumps(state, ensure_ascii=False))
        self.assertNotIn(sentinel.encode("utf-8"), raw_manager_storage)

    def test_knowledge_consult_routes_task_to_full_workflow_without_false_empty_error(self) -> None:
        server = self._start_server()
        fake_result = mock.Mock(
            run_id=61,
            status="success",
            evaluation_status="pass",
            orchestration_events=tuple(
                {"stage": f"stage-{index}", "status": "completed", "reason_code": "ok"}
                for index in range(12)
            ),
        )
        with (
            mock.patch("app.server.consult_knowledge") as knowledge,
            mock.patch("app.server.RequirementWorkflowRunner") as runner_type,
        ):
            runner_type.return_value.run.return_value = fake_result
            status, _, body = self._post_form(
                server,
                "/knowledge/consult",
                {"query": "这个需求为什么要这样改？"},
            )

        payload = json.loads(body.decode("utf-8"))
        with database.connect() as connection:
            intent_events = connection.execute(
                "select count(*) from manager_task_intent_events"
            ).fetchone()[0]
        self.assertEqual(200, status)
        self.assertEqual("task", payload["mode"])
        self.assertEqual("requirement_workflow", payload["downstream"])
        self.assertEqual(12, payload["workflow"]["stage_count"])
        self.assertFalse(payload["mutation_requested"])
        self.assertNotEqual("invalid_query", payload.get("retrieval_status"))
        self.assertEqual(1, intent_events)
        knowledge.assert_not_called()
        self.assertEqual(1, runner_type.return_value.run.call_count)

    def test_invalid_routing_override_is_400_before_event_or_downstream(self) -> None:
        server = self._start_server()
        with (
            mock.patch("app.server.consult_knowledge") as knowledge,
            mock.patch("app.server.RequirementWorkflowRunner") as runner_type,
        ):
            status, _, body = self._post_form(
                server,
                "/routing/classify",
                {
                    "message": "什么是装饰器？",
                    "explicit_override": "invalid-mode",
                },
            )

        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(400, status)
        self.assertEqual("routing_input_invalid", payload["error_code"])
        self.assertFalse(payload["changed"])
        self.assertFalse(payload["downstream_completed"])
        self.assertEqual([], build_manager_routing_status()["events"])
        knowledge.assert_not_called()
        runner_type.assert_not_called()

    def test_post_route_knowledge_failure_returns_safe_receipt_cookie_and_reuses_conversation(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_KNOWLEDGE_DOWNSTREAM_SECRET"
        with mock.patch(
            "app.server.consult_knowledge",
            side_effect=RuntimeError(f"authorization=Bearer {sentinel}"),
        ):
            status, headers, body = self._post_form(
                server,
                "/knowledge/consult",
                {"query": "什么是装饰器？"},
            )

        payload = json.loads(body.decode("utf-8"))
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(503, status)
        self.assertEqual("downstream_failed", payload["error_code"])
        self.assertTrue(payload["changed"])
        self.assertFalse(payload["downstream_completed"])
        self.assertIsInstance(payload["event_id"], int)
        self.assertEqual("question", payload["mode"])
        self.assertEqual("knowledge", payload["next_route"])
        self.assertEqual(cookie.split("=", 1)[1], payload["conversation_key"])
        self.assertNotIn(sentinel, json.dumps(payload, ensure_ascii=False))
        self.assertEqual(1, len(build_manager_routing_status()["events"]))

        with mock.patch(
            "app.server.consult_knowledge",
            return_value={
                "schema_version": "his-knowledge-consultation.v1",
                "answerable": False,
                "model_used": False,
                "results": [],
                "citations": [],
            },
        ):
            retry_status, _, retry_body = self._post_form(
                server,
                "/knowledge/consult",
                {"query": "什么是闭包？"},
                cookie=cookie,
            )
        retry = json.loads(retry_body.decode("utf-8"))
        self.assertEqual(200, retry_status)
        self.assertEqual(payload["conversation_key"], retry["routing"]["conversation_key"])

    def test_post_route_workflow_failure_returns_one_safe_persisted_route(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_WORKFLOW_DOWNSTREAM_SECRET"
        with mock.patch("app.server.RequirementWorkflowRunner") as runner_type:
            runner_type.return_value.run.side_effect = RuntimeError(
                f"client_secret={sentinel}"
            )
            status, headers, body = self._post_form(
                server,
                "/routing/classify",
                {"message": "这个需求为什么要这样改？"},
            )

        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(503, status)
        self.assertEqual("downstream_failed", payload["error_code"])
        self.assertTrue(payload["changed"])
        self.assertFalse(payload["downstream_completed"])
        self.assertEqual("task", payload["mode"])
        self.assertEqual("requirement_workflow", payload["next_route"])
        self.assertFalse(payload["mutation_requested"])
        self.assertIn("Set-Cookie", headers)
        self.assertNotIn(sentinel, json.dumps(payload, ensure_ascii=False))
        events = build_manager_routing_status()["events"]
        self.assertEqual(1, len(events))
        self.assertEqual(payload["event_id"], events[0]["id"])

    def test_knowledge_consult_post_requires_csrf_and_never_returns_sensitive_query(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_KNOWLEDGE_ENCODED"
        sensitive_query = (
            "什么是以下知识内容：%257B%2522client_secret%2522%253A%2522"
            + sentinel
            + "%2522%257D"
        )

        rejected_status, _, _ = self._post_form(
            server,
            "/knowledge/consult",
            {"query": sensitive_query},
            csrf_token=None,
        )
        accepted_status, _, body = self._post_form(
            server,
            "/knowledge/consult",
            {"query": sensitive_query},
        )
        payload = json.loads(body.decode("utf-8"))
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/knowledge", timeout=5
        ) as response:
            page = response.read().decode("utf-8")
        raw_manager_storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )

        self.assertEqual(403, rejected_status)
        self.assertEqual(200, accepted_status)
        self.assertFalse(payload["answerable"])
        self.assertFalse(payload["model_used"])
        self.assertEqual("question", payload["routing"]["mode"])
        self.assertEqual("knowledge", payload["routing"]["downstream"])
        self.assertNotIn(sentinel, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(sentinel, page)
        self.assertNotIn(sentinel.encode("utf-8"), raw_manager_storage)

    def test_knowledge_api_history_and_storage_hide_reviewer_sensitive_vectors(self) -> None:
        server = self._start_server()
        escaped_key = (
            "\\u0063\\u006c\\u0069\\u0065\\u006e\\u0074\\u005f"
            "\\u0073\\u0065\\u0063\\u0072\\u0065\\u0074"
        )
        queries = (
            (
                '{"outer":[{"\\u0063\\u006c\\u0069\\u0065\\u006e\\u0074\\u005f'
                '\\u0073\\u0065\\u0063\\u0072\\u0065\\u0074":'
                '"SENTINEL_API_UNICODE_JSON"}]}'
            ),
            "%u0063lient_secret%3DSENTINEL_API_PERCENT_U",
            "&#x63;lient_secret&#x3A;SENTINEL_API_HTML_ENTITY",
            "pat=SENTINEL_API_INDEPENDENT_PAT",
            "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
            "SENTINEL_API_ENCRYPTED_KEY\n"
            "-----END ENCRYPTED PRIVATE KEY-----",
            "+8613800138000",
            (
                f'前缀 {{"outer":[{{"{escaped_key}":'
                '"SENTINEL_API_PREFIX_JSON"}}]} 后缀'
            ),
            f'prefix {{"{escaped_key}":"SENTINEL_API_MALFORMED_JSON",}} suffix',
            "[" * 70 + '"SENTINEL_API_DEEP_JSON"' + "]" * 70,
            '{"message":"SENTINEL_API_OVER_CHAR_' + "a" * 33_000 + '"}',
            '{"message":"SENTINEL_API_OVER_BYTE_' + "汉" * 22_000 + '"}',
            (
                '["SENTINEL_API_OVER_NODE",'
                + ",".join("0" for _ in range(10_100))
                + "]"
            ),
        )

        rendered_payloads: list[str] = []
        routed_queries = tuple(f"什么是以下知识内容：{query}" for query in queries)
        for query in routed_queries:
            status, _, body = self._post_form(
                server,
                "/knowledge/consult",
                {"query": query},
            )
            self.assertEqual(200, status)
            rendered_payloads.append(body.decode("utf-8"))
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/knowledge", timeout=5
        ) as response:
            page = response.read().decode("utf-8")
        raw_manager_storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )

        self.assertNotIn("SENTINEL", "".join(rendered_payloads))
        self.assertNotIn("SENTINEL", page)
        self.assertFalse(b"SENTINEL" in raw_manager_storage)
        for query in routed_queries:
            self.assertNotIn(query, page)
            self.assertNotIn(query.encode("utf-8"), raw_manager_storage)

    def test_knowledge_consultation_page_escapes_stored_manual_query(self) -> None:
        server = self._start_server()
        malicious_query = '什么是 <script>alert("consultation")</script>？'

        accepted_status, _, _ = self._post_form(
            server,
            "/knowledge/consult",
            {"query": malicious_query},
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/knowledge", timeout=5
        ) as response:
            page = response.read().decode("utf-8")

        self.assertEqual(200, accepted_status)
        self.assertNotIn(malicious_query, page)
        self.assertIn("&lt;script&gt;", page)

    def test_manager_provider_form_saves_profile_but_not_credential_without_master_key(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_UNAVAILABLE_ENCRYPTION_KEY"

        with mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": ""},
            clear=False,
        ):
            response_status, _, response_body = self._post_form(
                server,
                "/providers",
                {
                    "provider": "model",
                    "profile_key": "blocked-demo",
                    "display_name": "Blocked Demo",
                    "enabled": "on",
                    "provider_kind": "openai_compatible",
                    "model": "demo-model",
                    "api_key": sentinel,
                },
            )
            self.assertEqual(400, response_status)
            error_html = response_body.decode("utf-8")
            payload = self._get_json(server, "/api/manager/providers")

        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(["blocked-demo"], [item["profile_key"] for item in payload["profiles"]])
        self.assertEqual({}, payload["profiles"][0]["credentials"])
        self.assertIn("凭证加密服务不可用", error_html)
        self.assertNotIn(sentinel, error_html)
        self.assertNotIn(sentinel, rendered)

    def test_model_smoke_preflight_http_is_inert_and_blocked_without_master_key(self) -> None:
        server = self._start_server()
        with mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": ""},
            clear=False,
        ), mock.patch(
            "app.model_provider_runtime.ControlledModelProviderRuntime.run_smoke",
            side_effect=AssertionError("preflight must not run a model"),
        ) as run_smoke, mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("preflight must not call a network"),
        ) as urlopen:
            # Use the handler helper instead of the patched urllib client for this request.
            import http.client

            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", "/api/manager/model-smoke-preflight?profile_key=demo")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()

        self.assertEqual(200, response.status)
        self.assertEqual("blocked", payload["status"])
        self.assertEqual("encryption_unavailable", payload["reason"])
        self.assertFalse(payload["credentials_read"])
        self.assertFalse(payload["external_calls"])
        self.assertFalse(payload["write_performed"])
        run_smoke.assert_not_called()
        urlopen.assert_not_called()

    def test_manager_credential_update_route_returns_status_only(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_CREDENTIAL_UPDATE_SECRET"
        master_key = "bW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW0="

        with mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": master_key},
            clear=False,
        ):
            profile_status, _, _ = self._post_form(
                server,
                "/providers",
                {
                    "provider": "model",
                    "profile_key": "credential-demo",
                    "display_name": "Credential Demo",
                    "enabled": "on",
                    "provider_kind": "openai_compatible",
                    "base_url": "https://api.example.test/v1",
                    "model": "demo-model",
                },
            )
            credential_status, credential_headers, _ = self._post_form(
                server,
                "/providers/credentials",
                {
                    "provider": "model",
                    "profile_key": "credential-demo",
                    "field": "api_key",
                    "credential_value": sentinel,
                },
            )
            payload = self._get_json(server, "/api/manager/providers")

        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(303, profile_status)
        self.assertEqual(303, credential_status)
        self.assertEqual("/providers?credential_saved=1", credential_headers["Location"])
        self.assertEqual("configured", payload["profiles"][0]["credentials"]["api_key"])
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("ciphertext", rendered.lower())

    def test_database_manager_status_never_advertises_a_write_executor(self) -> None:
        server = self._start_server()
        response_status, _, _ = self._post_form(
            server,
            "/providers",
            {
                "provider": "database",
                "profile_key": "his-readonly",
                "display_name": "HIS Readonly",
                "enabled": "on",
                "driver": "postgresql",
                "host": "db.example.test",
                "port": "5432",
                "database": "his",
                "schema": "public",
                "username": "readonly_user",
                "readonly_policy": "required",
            },
        )
        payload = self._get_json(server, "/api/manager/providers")

        readiness = payload["profiles"][0]["action_readiness"]
        self.assertEqual(303, response_status)
        self.assertEqual("permanently_disabled", readiness["write_policy"])
        self.assertIn("read_only_select", readiness["supported_actions"])
        self.assertIn("sql_draft", readiness["supported_actions"])
        self.assertNotIn("database.change", readiness["supported_actions"])
        self.assertFalse(readiness["write_performed"])

    def test_provider_profile_post_rejects_wrong_csrf_or_origin_before_database_mutation(self) -> None:
        server = self._start_server()
        fields = {
            "provider": "model",
            "profile_key": "csrf-blocked",
            "display_name": "CSRF Blocked",
            "enabled": "on",
            "provider_kind": "openai_compatible",
            "base_url": "https://api.example.test/v1",
            "model": "demo-model",
        }

        missing_status, _, _ = self._post_form(
            server, "/providers", fields, csrf_token=None
        )
        wrong_token_status, _, _ = self._post_form(
            server, "/providers", fields, csrf_token="wrong-token"
        )
        wrong_origin_status, _, _ = self._post_form(
            server,
            "/providers",
            fields,
            origin="http://attacker.example.test",
        )
        payload = self._get_json(server, "/api/manager/providers")

        self.assertEqual(403, missing_status)
        self.assertEqual(403, wrong_token_status)
        self.assertEqual(403, wrong_origin_status)
        self.assertEqual([], payload["profiles"])
        self.assertNotIn(_MANAGER_FORM_CSRF_TOKEN, json.dumps(payload))

    def test_provider_credential_post_rejects_missing_csrf_or_wrong_origin_before_write(self) -> None:
        server = self._start_server()
        profile_status, _, _ = self._post_form(
            server,
            "/providers",
            {
                "provider": "model",
                "profile_key": "credential-csrf",
                "display_name": "Credential CSRF",
                "enabled": "on",
                "provider_kind": "openai_compatible",
                "base_url": "https://api.example.test/v1",
                "model": "demo-model",
            },
        )
        fields = {
            "provider": "model",
            "profile_key": "credential-csrf",
            "field": "api_key",
            "credential_value": "SENTINEL_CSRF_CREDENTIAL",
        }
        missing_status, _, _ = self._post_form(
            server, "/providers/credentials", fields, csrf_token=None
        )
        wrong_origin_status, _, _ = self._post_form(
            server,
            "/providers/credentials",
            fields,
            origin="http://attacker.example.test",
        )
        payload = self._get_json(server, "/api/manager/providers")

        self.assertEqual(303, profile_status)
        self.assertEqual(403, missing_status)
        self.assertEqual(403, wrong_origin_status)
        self.assertEqual({}, payload["profiles"][0]["credentials"])
        self.assertNotIn("SENTINEL_CSRF_CREDENTIAL", json.dumps(payload))

    def test_provider_connection_test_failure_is_generic_and_never_audits_exception_text(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_PROVIDER_CONNECTION_EXCEPTION"

        with mock.patch(
            "app.server.run_provider_connection_test",
            side_effect=RuntimeError(sentinel),
        ):
            status, _, body = self._post_form(
                server,
                "/api/provider-profiles/test-connection",
                {"provider": "yunxiao", "profile_key": "demo"},
            )
            payload = json.loads(body.decode("utf-8"))

        audit_text = (
            self.connection_audit_path.read_text(encoding="utf-8")
            if self.connection_audit_path.exists()
            else ""
        )
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(200, status)
        self.assertEqual("his-provider-connection-test-result.v2", payload["schema_version"])
        self.assertIsNone(payload["plan_id"])
        self.assertEqual("failed", payload["status"])
        self.assertEqual("provider_connection_test_failed", payload["reason"])
        self.assertEqual("provider_connection_test_failed", payload["error_code"])
        self.assertEqual("Provider 连接测试未执行。", payload["message"])
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn(sentinel, audit_text)

    def test_provider_capability_status_api_returns_only_redacted_contract_state(self) -> None:
        profile = [{
            "provider": provider,
            "profile_key": f"default-{provider}",
            "credential_ref": "identity",
            "connection": {"sentinel": "/SENTINEL_REPOSITORY_PATH"},
        } for provider in ("yunxiao", "git", "gitlab", "database", "knowledge", "model")]
        server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)

        with mock.patch("app.server.load_provider_profiles", return_value=profile):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/provider-profiles/capability-status",
                timeout=5,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))

        items = {item["provider"]: item for item in payload["items"]}
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(200, response.status)
        self.assertEqual(
            {"yunxiao", "git", "gitlab", "database", "knowledge", "model"},
            set(items),
        )
        self.assertEqual("git_inspect_os_sandbox_executor_unregistered", items["git"]["execution_reason"])
        self.assertEqual("his-git-local", items["git"]["skill"])
        self.assertEqual("enabled", items["yunxiao"]["capabilities"][0]["contract_status"])
        self.assertEqual("enabled", items["knowledge"]["capabilities"][0]["contract_status"])
        self.assertEqual("canonical_provider_contract_unregistered", items["model"]["execution_reason"])
        capability_statuses = {
            capability["name"]: capability["execution_status"]
            for item in items.values()
            for capability in item["capabilities"]
        }
        available_capabilities = {
            "git.diff",
            "source.read",
            "source.search",
            "git.history",
            "verification.run-local",
            "code.review-local",
        }
        self.assertEqual(
            available_capabilities,
            {
                name
                for name, execution_status in capability_statuses.items()
                if execution_status == "available"
            },
        )
        self.assertTrue(all(
            execution_status == "blocked"
            for name, execution_status in capability_statuses.items()
            if name not in available_capabilities
        ))
        self.assertFalse(payload["credentials_read"])
        self.assertFalse(payload["external_calls"])
        self.assertFalse(payload["write_performed"])
        self.assertNotIn("SENTINEL_REPOSITORY_PATH", rendered)
        self.assertNotIn("scripts/git_local.py", rendered)

    def test_core_status_api_returns_readiness_without_secret_values(self) -> None:
        sentinel = "SENTINEL_MANAGER_API_SECRET"
        server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)

        with mock.patch.dict(os.environ, {"aliyun_devops_pat": sentinel, "GITLAB_TOKEN": sentinel}):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/core-status",
                timeout=5,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual("his-core-status.v1", payload["schema_version"])
        self.assertEqual("ready", payload["status"])
        self.assertFalse(payload["credentials_read"])
        self.assertFalse(payload["external_calls"])
        self.assertNotIn(sentinel, json.dumps(payload, ensure_ascii=False))
        readiness = payload["readiness"]
        self.assertEqual("his-readiness.v1", readiness["schema_version"])
        self.assertEqual(
            {
                "real_model_worker",
                "learning_loop",
                "business_acceptance",
                "external_writes",
                "knowledge_home",
            },
            {item["id"] for item in readiness["items"]},
        )
        self.assertEqual(
            [
                "code_ready",
                "configured",
                "locally_tested",
                "externally_verified",
                "business_accepted",
            ],
            [item["id"] for item in readiness["verification_levels"]],
        )

    def test_core_status_api_does_not_probe_default_database_and_uses_manager_status_input(self) -> None:
        server = self._start_server()

        with (
            mock.patch(
                "app.database.database_read_only_health_snapshot",
                side_effect=AssertionError("default database health probe is forbidden"),
            ) as health_probe,
            mock.patch("app.server.ManagerProviderRepository", side_effect=AssertionError) as provider_repository,
            mock.patch("app.server.BusinessAcceptanceRepository", side_effect=AssertionError) as business_repository,
            mock.patch("app.database.init_db", side_effect=AssertionError) as init_db,
            mock.patch("app.database.connect", side_effect=AssertionError) as connect,
            mock.patch("app.database.sqlite3.connect", side_effect=AssertionError) as sqlite_connect,
        ):
            payload = self._get_json(server, "/api/core-status")

        levels = {
            item["id"]: item["state"]
            for item in payload["readiness"]["verification_levels"]
        }
        self.assertEqual("not_probed", payload["database"]["status"])
        self.assertEqual("not_evaluated", levels["configured"])
        self.assertEqual("not_recorded", levels["locally_tested"])
        self.assertEqual("not_verified", levels["externally_verified"])
        self.assertEqual("not_evaluated", levels["business_accepted"])
        health_probe.assert_not_called()
        provider_repository.assert_not_called()
        business_repository.assert_not_called()
        init_db.assert_not_called()
        connect.assert_not_called()
        sqlite_connect.assert_not_called()

    def test_manager_c3_pages_and_json_statuses_are_local_and_redacted(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_C3_MANAGER_SECRET"
        pages: list[str] = []
        payloads: list[dict] = []

        with mock.patch.dict(os.environ, {"aliyun_devops_pat": sentinel}, clear=False):
            for path in (
                "/actions",
                "/knowledge",
                "/learning-candidates",
                "/business-acceptance",
            ):
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}{path}", timeout=5
                ) as response:
                    pages.append(response.read().decode("utf-8"))
            for path in (
                "/api/manager/actions",
                "/api/manager/knowledge",
                "/api/manager/learning-candidates",
                "/api/manager/business-acceptance",
            ):
                payloads.append(self._get_json(server, path))

        rendered = "\n".join(pages) + json.dumps(payloads, ensure_ascii=False)
        self.assertIn("Provider 动作计划与审计", rendered)
        self.assertIn("知识候选审核", rendered)
        self.assertIn("业务验收证据", rendered)
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("ciphertext", rendered.lower())
        self.assertNotIn("authorization_hash", rendered.lower())

    def test_all_new_manager_mutations_reject_bad_origin_or_csrf_before_change(self) -> None:
        server = self._start_server()
        cases = (
            ("/actions/plans", {"profile_id": "1", "action": "repo.status.read", "target_alias": "repo-a", "parameters_json": "{}", "requested_by": "manager"}),
            ("/actions/confirm", {"plan_id": "1", "reviewer_alias": "reviewer-a"}),
            ("/learning-candidates/review", {"candidate_key": "learn-missing", "decision": "approve", "reviewer_alias": "reviewer-a"}),
            ("/business-acceptance/evidence", {"evidence_key": "case-a"}),
            ("/business-acceptance/decisions", {"evidence_id": "1"}),
        )

        for path, fields in cases:
            with self.subTest(path=path):
                missing_status, _, _ = self._post_form(
                    server, path, fields, csrf_token=None
                )
                wrong_origin_status, _, _ = self._post_form(
                    server, path, fields, origin="http://attacker.example.test"
                )
                self.assertEqual(403, missing_status)
                self.assertEqual(403, wrong_origin_status)
        with database.connect() as connection:
            self.assertEqual(0, int(connection.execute("select count(*) from manager_provider_action_plans").fetchone()[0]))
            self.assertEqual(0, int(connection.execute("select count(*) from manager_business_acceptance_evidence").fetchone()[0]))
            self.assertEqual(0, int(connection.execute("select count(*) from manager_business_acceptance_decisions").fetchone()[0]))

    def test_business_acceptance_http_requires_evidence_then_append_only_acceptance(self) -> None:
        server = self._start_server()
        evidence_status, _, evidence_body = self._post_form(
            server,
            "/business-acceptance/evidence",
            {
                "evidence_key": "dfhis-case-a",
                "environment_alias": "his-test-a",
                "operator_alias": "operator-a",
                "test_data_alias": "case-a",
                "technical_result": "passed",
                "runtime_verified": "true",
                "scenario_name": "charge-save",
                "scenario_status": "passed",
                "scenario_expected": "record-created",
                "scenario_actual": "record-created",
                "scenario_evidence": "sha256:" + "a" * 64,
            },
        )
        evidence_payload = json.loads(evidence_body.decode("utf-8"))
        before = self._get_json(server, "/api/manager/business-acceptance")
        decision_status, _, decision_body = self._post_form(
            server,
            "/business-acceptance/decisions",
            {
                "evidence_id": str(evidence_payload["id"]),
                "reviewer_alias": "reviewer-a",
                "decision": "accept",
                "reason": "runtime-evidence-reviewed",
            },
        )
        after = self._get_json(server, "/api/manager/business-acceptance")

        self.assertEqual(200, evidence_status)
        self.assertFalse(before["business_valid"])
        self.assertEqual(200, decision_status)
        self.assertEqual("accept", json.loads(decision_body)["decision"])
        self.assertTrue(after["business_valid"])

    def test_business_acceptance_aggregate_invalidates_old_acceptance_after_new_version(self) -> None:
        repository = BusinessAcceptanceRepository()
        first = repository.create_evidence(
            {
                "evidence_key": "http-current-version-case",
                "environment_alias": "his-test-a",
                "operator_alias": "operator-a",
                "test_data_alias": "case-a",
                "technical_result": "passed",
                "runtime_verified": True,
                "scenarios": [{"name": "save", "status": "passed", "expected": "ok", "actual": "ok", "evidence": "sha256:" + "f" * 64}],
            }
        )
        repository.append_reviewer_decision(
            evidence_id=int(first["id"]), reviewer_alias="reviewer-a",
            decision="accept", reason="version-one-accepted",
        )
        second = repository.create_evidence(
            {
                "evidence_key": "http-current-version-case",
                "environment_alias": "his-test-a",
                "operator_alias": "operator-a",
                "test_data_alias": "case-b",
                "technical_result": "failed",
                "runtime_verified": True,
                "scenarios": [{"name": "save", "status": "failed", "expected": "ok", "actual": "failed", "evidence": "sha256:" + "1" * 64}],
            }
        )
        repository.append_reviewer_decision(
            evidence_id=int(second["id"]), reviewer_alias="reviewer-b",
            decision="accept", reason="reviewed-but-failed",
        )
        payload = build_manager_business_acceptance_status()

        self.assertFalse(payload["business_valid"])

    def test_action_plan_http_rejects_unknown_and_provider_mismatch_and_reports_canonical_risk(self) -> None:
        profile = ManagerProviderRepository().upsert_profile(
            scope_type="local", scope_key="default", provider="model",
            profile_key="action-model", display_name="Action Model", enabled=True,
            connection={"provider_kind": "openai_compatible", "model": "demo"},
        )
        server = self._start_server()
        for action in ("unknown.action", "repo.status.read"):
            with self.subTest(action=action):
                status, _, body = self._post_form(
                    server,
                    "/actions/plans",
                    {
                        "profile_id": str(profile.id),
                        "action": action,
                        "target_alias": "model-demo",
                        "parameters_json": "{}",
                        "requested_by": "manager-user",
                    },
                )
                self.assertEqual(400, status)
                self.assertEqual("blocked", json.loads(body)["status"])

        self.assertEqual([], ManagerProviderRepository().list_action_plans())

    def test_actions_page_shows_canonical_risk_and_reviewed_summary_before_confirmation(self) -> None:
        profile = ManagerProviderRepository().upsert_profile(
            scope_type="local", scope_key="default", provider="git",
            profile_key="local-repo", display_name="Local Repo", enabled=True,
            connection={"repository_path": "/private/tmp/repo"},
        )
        parameters = {
            "repository_alias": "repo",
            "remote_alias": "origin",
            "ref_name": "refs/heads/main",
        }
        plan = ProviderActionAuthorizer(
            ManagerProviderRepository(), clock=lambda: datetime.now(timezone.utc)
        ).create_plan(
            profile_id=profile.id,
            action="remote.fetch",
            target_alias="repo",
            parameters=parameters,
            requested_by="manager-user",
        )

        page = render_actions_page()

        self.assertIn("remote.fetch", page)
        self.assertIn("local_mutation", page)
        self.assertIn("repository_alias", page)
        self.assertIn("refs/heads/main", page)
        self.assertIn(f'value="{plan.id}"', page)
        self.assertNotIn('<input name="action"', page)

    def test_business_acceptance_http_rejects_secret_without_echo_or_storage(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_C3_BUSINESS_TOKEN"
        status, _, body = self._post_form(
            server,
            "/business-acceptance/evidence",
            {
                "evidence_key": "dfhis-case-secret",
                "environment_alias": "his-test-a",
                "operator_alias": "operator-a",
                "test_data_alias": "case-a",
                "technical_result": "passed",
                "runtime_verified": "true",
                "scenario_name": "charge-save",
                "scenario_status": "passed",
                "scenario_expected": "record-created",
                "scenario_actual": f"token={sentinel}",
                "scenario_evidence": "sha256:" + "b" * 64,
            },
        )
        raw = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )

        self.assertEqual(400, status)
        self.assertNotIn(sentinel, body.decode("utf-8"))
        self.assertNotIn(sentinel.encode(), raw)

    def test_business_acceptance_http_rejects_bare_bearer_without_html_json_or_storage_echo(self) -> None:
        server = self._start_server()
        sentinel = "Bearer " + "Z7" * 24
        status, _, body = self._post_form(
            server,
            "/business-acceptance/evidence",
            {
                "evidence_key": "dfhis-case-bearer",
                "environment_alias": "his-test-a",
                "operator_alias": "operator-a",
                "test_data_alias": "case-a",
                "technical_result": "passed",
                "runtime_verified": "true",
                "scenario_name": "charge-save",
                "scenario_status": "passed",
                "scenario_expected": "record-created",
                "scenario_actual": sentinel,
                "scenario_evidence": "sha256:" + "c" * 64,
            },
        )
        payload = self._get_json(server, "/api/manager/business-acceptance")
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/business-acceptance", timeout=5
        ) as response:
            page = response.read().decode("utf-8")
        raw = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )

        self.assertEqual(400, status)
        self.assertNotIn(sentinel, body.decode("utf-8"))
        self.assertNotIn(sentinel, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(sentinel, page)
        self.assertNotIn(sentinel.encode(), raw)

    def test_manager_provider_api_does_not_echo_historical_authenticated_jdbc_host(self) -> None:
        repository = ManagerProviderRepository()
        profile = repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="historical-oracle",
            display_name="Historical Oracle",
            enabled=True,
            connection={
                "driver": "oracle",
                "host": "db.test",
                "port": "1521",
                "database": "HIS",
                "username": "report_user",
                "readonly_policy": "required",
            },
        )
        sentinel = "jdbc:oracle:thin:report_user/Secret9Password@//db.test:1521/HIS"
        with database.connect() as connection:
            connection.execute(
                "update manager_provider_profiles set connection_json = ? where id = ?",
                (
                    json.dumps(
                        {
                            "driver": "oracle",
                            "host": sentinel,
                            "port": "1521",
                            "database": "HIS",
                            "username": "report_user",
                            "readonly_policy": "required",
                        }
                    ),
                    profile.id,
                ),
            )

        server = self._start_server()
        payload = self._get_json(server, "/api/manager/providers")

        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertEqual("his-manager-provider-status.v1", payload["schema_version"])
        self.assertEqual("blocked", payload["status"])
        self.assertEqual([], payload["profiles"])
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("Secret9Password", rendered)

    def test_provider_profile_apis_return_inert_redacted_status(self) -> None:
        sentinel = "SENTINEL_PROVIDER_API_SECRET"
        server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)

        with mock.patch.dict(os.environ, {"aliyun_devops_pat": sentinel, "GITLAB_TOKEN": sentinel}):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/provider-profiles",
                timeout=5,
            ) as response:
                profile_payload = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/provider-profiles/test-plan",
                timeout=5,
            ) as response:
                plan_payload = json.loads(response.read().decode("utf-8"))

        rendered = json.dumps([profile_payload, plan_payload], ensure_ascii=False)
        self.assertEqual("his-provider-profiles.v1", profile_payload["schema_version"])
        self.assertEqual("his-provider-connection-test-plan.v1", plan_payload["schema_version"])
        self.assertFalse(profile_payload["changed"])
        self.assertFalse(plan_payload["changed"])
        self.assertFalse(plan_payload["credentials_read"])
        self.assertFalse(plan_payload["external_calls"])
        self.assertFalse(plan_payload["execution_allowed"])
        self.assertIn("aliyun_devops_pat", rendered)
        self.assertNotIn(sentinel, rendered)

    def test_provider_profile_post_saves_local_profile_without_reading_secret(self) -> None:
        sentinel = "SENTINEL_PROVIDER_POST_SECRET"
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "profiles.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.shutdown)

            form = urllib.parse.urlencode(
                {
                    "provider": "yunxiao",
                    "profile_key": "company-yunxiao",
                    "display_name": "公司云效",
                    "enabled": "on",
                    "organization_id": "org-fixture",
                    "project_key": "DFHIS",
                    "_csrf_token": _MANAGER_FORM_CSRF_TOKEN,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/providers",
                data=form,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                },
            )

            with mock.patch.dict(
                os.environ,
                {
                    "HARNESS_PROVIDER_PROFILE_STORE": str(store_path),
                    "aliyun_devops_pat": sentinel,
                },
            ):
                try:
                    urllib.request.urlopen(request, timeout=5)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(303, exc.code)
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/provider-profiles",
                    timeout=5,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))

            rendered = json.dumps(payload, ensure_ascii=False)
            self.assertTrue(database.DB_PATH.is_file())
            self.assertFalse(store_path.exists())
            self.assertEqual(["company-yunxiao"], [profile["profile_key"] for profile in payload["profiles"]])
            self.assertIn("manager_provider_credentials:yunxiao", rendered)

    def test_provider_connection_test_post_creates_v2_plan_without_execution(self) -> None:
        sentinel = "SENTINEL_CONNECTION_TEST_SECRET"
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "profiles.json"
            audit_path = Path(temp_dir) / "connection-tests.jsonl"
            server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.shutdown)

            profile_form = urllib.parse.urlencode(
                {
                    "provider": "yunxiao",
                    "profile_key": "company-yunxiao",
                    "display_name": "公司云效",
                    "enabled": "on",
                    "organization_id": "org-fixture",
                    "project_key": "DFHIS",
                    "_csrf_token": _MANAGER_FORM_CSRF_TOKEN,
                }
            ).encode("utf-8")
            test_fields = {
                "provider": "yunxiao",
                "profile_key": "company-yunxiao",
                "confirmation_text": "只允许本地记录，不允许读取凭证或联网",
            }

            with mock.patch.dict(
                os.environ,
                {
                    "HARNESS_PROVIDER_PROFILE_STORE": str(store_path),
                    "HARNESS_PROVIDER_CONNECTION_TEST_AUDIT": str(audit_path),
                    "aliyun_devops_pat": sentinel,
                },
            ):
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"http://127.0.0.1:{server.server_port}/providers",
                            data=profile_form,
                            method="POST",
                            headers={
                                "Content-Type": "application/x-www-form-urlencoded",
                                "Origin": f"http://127.0.0.1:{server.server_port}",
                            },
                        ),
                        timeout=5,
                    )
                except urllib.error.HTTPError as exc:
                    self.assertEqual(303, exc.code)
                status, _, body = self._post_form(
                    server,
                    "/api/provider-profiles/test-connection",
                    test_fields,
                )
                payload = json.loads(body.decode("utf-8"))

            rendered = json.dumps(payload, ensure_ascii=False)
            self.assertEqual("his-provider-connection-test-result.v2", payload["schema_version"])
            self.assertEqual("awaiting_confirmation", payload["status"])
            self.assertEqual("provider_action_confirmation_required", payload["reason"])
            self.assertIsInstance(payload["plan_id"], int)
            self.assertEqual("yunxiao.connection_test", payload["action"])
            self.assertEqual("read", payload["risk"])
            self.assertFalse(payload["credentials_read"])
            self.assertFalse(payload["external_calls"])
            self.assertFalse(payload["execution_allowed"])
            self.assertEqual(200, status)
            self.assertFalse(audit_path.exists())
            self.assertEqual(
                "planned",
                ManagerProviderRepository().get_action_plan(payload["plan_id"])["state"],
            )
            self.assertEqual(
                [],
                ManagerProviderRepository().list_action_audits(
                    action_type="yunxiao.connection_test"
                ),
            )
            self.assertNotIn(sentinel, rendered)

    def test_provider_action_posts_require_csrf_before_any_manager_audit(self) -> None:
        server = self._start_server()
        forms = {
            "/api/provider-profiles/test-connection": {
                "provider": "yunxiao",
                "profile_key": "company-yunxiao",
                "requested_by": "manager",
                "confirmation_text": "local-only",
            },
            "/api/provider-profiles/readonly-smoke": {
                "provider": "git",
                "profile_key": "local-smoke",
                "confirmation_text": "bad",
            },
        }

        for path, fields in forms.items():
            with self.subTest(path=path):
                status, _, body = self._post_form(
                    server,
                    path,
                    fields,
                    csrf_token=None,
                )
                wrong_origin_status, _, wrong_origin_body = self._post_form(
                    server,
                    path,
                    fields,
                    origin="http://attacker.example.test",
                )
                self.assertEqual(403, status)
                self.assertEqual(403, wrong_origin_status)
                self.assertNotIn("local-only", body.decode("utf-8"))
                self.assertNotIn("local-only", wrong_origin_body.decode("utf-8"))

        repository = ManagerProviderRepository()
        self.assertEqual(0, len(repository.list_action_audits()))
        self.assertFalse(self.connection_audit_path.exists())
        self.assertFalse(self.readonly_audit_path.exists())

    def test_provider_action_posts_reject_sensitive_public_inputs_without_echo_or_audit(self) -> None:
        server = self._start_server()
        sentinel = "SENTINEL_PROVIDER_ACTION_INPUT"
        forms = {
            "/api/provider-profiles/test-connection": {
                "provider": "yunxiao",
                "profile_key": "company-yunxiao",
                "requested_by": f"Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345{sentinel}",
                "confirmation_text": "local-only",
            },
            "/api/provider-profiles/readonly-smoke": {
                "provider": "git",
                "profile_key": f"-----BEGIN PRIVATE KEY-----{sentinel}",
                "confirmation_text": "bad",
            },
        }

        rendered = []
        for path, fields in forms.items():
            with self.subTest(path=path):
                status, _, body = self._post_form(server, path, fields)
                payload = json.loads(body.decode("utf-8"))
                rendered.append(payload)
                self.assertEqual(400, status)
                self.assertEqual("provider_action_input_invalid", payload["error_code"])

        self.assertNotIn(sentinel, json.dumps(rendered, ensure_ascii=False))
        self.assertEqual(0, len(ManagerProviderRepository().list_action_audits()))
        self.assertFalse(self.connection_audit_path.exists())
        self.assertFalse(self.readonly_audit_path.exists())

    def test_provider_readonly_smoke_post_creates_v2_plans_without_execution(self) -> None:
        sentinel = "SENTINEL_READONLY_SMOKE_API_SECRET"
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir) / "repository"
            repository.mkdir()
            self.assertFalse((repository / ".git").exists())
            store_path = Path(temp_dir) / "profiles.json"
            audit_path = Path(temp_dir) / "readonly-smoke.jsonl"
            server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.shutdown)
            profile_form = urllib.parse.urlencode({
                "provider": "git", "profile_key": "local-smoke",
                "display_name": "Local smoke", "enabled": "on",
                "repository_path": str(repository),
                "_csrf_token": _MANAGER_FORM_CSRF_TOKEN,
            }).encode("utf-8")
            smoke_fields = {
                "provider": "git", "profile_key": "local-smoke",
                "confirmation_text": "attempt to override server confirmation",
                "requested_by": sentinel,
            }
            valid_smoke_fields = {
                "provider": "git", "profile_key": "local-smoke",
                "confirmation_text": "确认仅执行本地、只读、免凭证且离线的 Git smoke 检查",
            }
            with mock.patch.dict(os.environ, {
                "HARNESS_PROVIDER_PROFILE_STORE": str(store_path),
                "READONLY_SMOKE_API_SENTINEL": sentinel,
            }):
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:{server.server_port}/providers", data=profile_form,
                        method="POST", headers={
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Origin": f"http://127.0.0.1:{server.server_port}",
                        }), timeout=5)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(303, exc.code)
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/provider-profiles/readonly-smoke-plan", timeout=5
                ) as response:
                    plan = json.loads(response.read().decode("utf-8"))
                status, _, body = self._post_form(
                    server, "/api/provider-profiles/readonly-smoke", smoke_fields
                )
                result = json.loads(body.decode("utf-8"))
                valid_status, _, valid_body = self._post_form(
                    server, "/api/provider-profiles/readonly-smoke", valid_smoke_fields
                )
                valid_result = json.loads(valid_body.decode("utf-8"))
            manager_repository = ManagerProviderRepository()
            audits = manager_repository.list_action_audits(
                action_type="git.readonly_smoke"
            )
            rendered = json.dumps([plan, result, valid_result, audits], ensure_ascii=False)
            self.assertEqual("his-provider-readonly-smoke-plan.v2", plan["schema_version"])
            self.assertEqual("awaiting_confirmation", result["status"])
            self.assertEqual("provider_action_confirmation_required", result["reason"])
            self.assertEqual("manager", result["requested_by"])
            self.assertIsInstance(result["plan_id"], int)
            self.assertFalse(result["credentials_read"])
            self.assertFalse(result["external_calls"])
            self.assertFalse(result["write_performed"])
            self.assertNotIn("audit_path", result)
            self.assertEqual("awaiting_confirmation", valid_result["status"])
            self.assertEqual("provider_action_confirmation_required", valid_result["reason"])
            self.assertIsInstance(valid_result["plan_id"], int)
            self.assertEqual((200, 200), (status, valid_status))
            self.assertEqual([], audits)
            self.assertEqual(
                ["planned", "planned"],
                [
                    manager_repository.get_action_plan(plan_id)["state"]
                    for plan_id in (result["plan_id"], valid_result["plan_id"])
                ],
            )
            self.assertFalse(audit_path.exists())

    def test_provider_readonly_smoke_post_error_uses_audited_safe_result_contract(self) -> None:
        sentinel = "SENTINEL_POST_ERROR"
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "readonly-smoke.jsonl"
            server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.shutdown)
            fields = {
                "provider": "git", "profile_key": "local-smoke",
                "confirmation_text": "确认仅执行本地、只读、免凭证且离线的 Git smoke 检查",
            }
            with mock.patch("app.server.load_provider_profiles", side_effect=ValueError(sentinel)):
                status, _, body = self._post_form(
                    server, "/api/provider-profiles/readonly-smoke", fields
                )
                result = json.loads(body.decode("utf-8"))

            audits = ManagerProviderRepository().list_action_audits()
            rendered = json.dumps([result, audits], ensure_ascii=False)
            self.assertEqual("provider_readonly_smoke_execution_failed", result["reason"])
            self.assertEqual("his-provider-readonly-smoke-result.v2", result["schema_version"])
            self.assertIsNone(result["plan_id"])
            self.assertFalse(result["credentials_read"])
            self.assertFalse(result["external_calls"])
            self.assertFalse(result["write_performed"])
            self.assertEqual(200, status)
            self.assertEqual([], audits)
            self.assertFalse(audit_path.exists())
            self.assertNotIn(sentinel, rendered)

    def test_provider_readonly_smoke_post_returns_json_when_failure_audit_also_fails(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        fields = {"provider": "git", "profile_key": "local", "confirmation_text": "bad"}
        with mock.patch("app.server.load_provider_profiles", side_effect=ValueError("SENTINEL_ERROR")), mock.patch(
            "app.server.record_provider_readonly_smoke_failure", side_effect=OSError("SENTINEL_AUDIT")
        ):
            status, _, body = self._post_form(
                server, "/api/provider-profiles/readonly-smoke", fields
            )
            result = json.loads(body.decode("utf-8"))

        rendered = json.dumps(result, ensure_ascii=False)
        self.assertEqual("provider_readonly_smoke_audit_failed", result["reason"])
        self.assertEqual("his-provider-readonly-smoke-result.v2", result["schema_version"])
        self.assertIsNone(result["plan_id"])
        self.assertFalse(result["credentials_read"])
        self.assertFalse(result["external_calls"])
        self.assertFalse(result["write_performed"])
        self.assertNotIn("SENTINEL", rendered)
        self.assertEqual(200, status)
        self.assertEqual(0, len(ManagerProviderRepository().list_action_audits()))


if __name__ == "__main__":
    unittest.main()
