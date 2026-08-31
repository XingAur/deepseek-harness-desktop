from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from urllib.parse import quote_plus


_TEMP_ROOT = tempfile.mkdtemp(prefix="harness-knowledge-consultation-import-")
os.environ.setdefault("HARNESS_DB_PATH", str(Path(_TEMP_ROOT) / "manager.sqlite"))
os.environ.setdefault("HIS_KNOWLEDGE_HOME", str(Path(_TEMP_ROOT) / "his-knowledge"))

from app import database
from app.knowledge_consultation import (
    build_manager_knowledge_capability_service,
    consult_knowledge,
)
from app.knowledge_index import query_knowledge_index, sync_obsidian_markdown_index
from app.manager_provider_repository import ManagerProviderRepository
from app.plugin_inventory import load_plugin_inventory
from app.task_intent_repository import TaskIntentRepository
from app.task_intent_router import IntentContext
from app.task_intent_service import TaskIntentService
from tools.capability_check import load_runtime_config


HARNESS_ROOT = Path(__file__).resolve().parents[1]


def _frozen_plugin_root(plugin_name: str) -> Path:
    config = load_runtime_config(str(HARNESS_ROOT / "config" / "capabilities.json"))
    inventory = load_plugin_inventory(HARNESS_ROOT / "config" / "plugin_inventory.json")
    if len(config.plugin_roots) != len(inventory.plugins):
        raise ValueError("配置的插件根目录与冻结清单不一致。")
    configured_roots = {
        item.name: Path(root)
        for item, root in zip(inventory.plugins, config.plugin_roots)
    }
    try:
        plugin_root = configured_roots[plugin_name]
    except KeyError as exc:
        raise ValueError("冻结清单缺少目标插件。") from exc
    if not plugin_root.is_absolute():
        raise ValueError("配置的插件根目录必须是绝对路径。")
    return plugin_root


PLUGIN_ROOT = _frozen_plugin_root("his-knowledge")


class KnowledgeConsultationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / "his-knowledge"
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.repository = ManagerProviderRepository()
        self.consultation_sequence = 0

        rule_dir = self.home / "vault" / "10-his-rules"
        rule_dir.mkdir(parents=True)
        (rule_dir / "outpatient.md").write_text(
            """---
status: approved
evidence_level: verified
valid_until: 2999-12-31
---
# 门诊收费如何处理

门诊收费处理需要先核对患者身份和费用明细。
""",
            encoding="utf-8",
        )
        (rule_dir / "candidate.md").write_text(
            """---
status: candidate
evidence_level: draft
valid_until: 2999-12-31
---
# 候选规则

候选规则不能直接回答。
""",
            encoding="utf-8",
        )
        (rule_dir / "expired.md").write_text(
            """---
status: approved
evidence_level: policy
valid_until: 2000-01-01
---
# 过期规则

过期规则不能直接回答。
""",
            encoding="utf-8",
        )
        sync_obsidian_markdown_index(self.home)

    def test_formal_plugin_root_comes_from_frozen_runtime_layout(self) -> None:
        config = load_runtime_config(str(HARNESS_ROOT / "config" / "capabilities.json"))
        inventory = load_plugin_inventory(HARNESS_ROOT / "config" / "plugin_inventory.json")
        plugin_names = [item.name for item in inventory.plugins]
        expected_root = Path(config.plugin_roots[plugin_names.index("his-knowledge")])

        self.assertEqual(expected_root, PLUGIN_ROOT)
        self.assertTrue(PLUGIN_ROOT.is_absolute())

    def _consult(self, query: str) -> dict[str, object]:
        self.consultation_sequence += 1
        routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
            query,
            IntentContext(
                conversation_key=f"consult-{self.consultation_sequence}"
            ),
            explicit_override="question",
        )
        return consult_knowledge(
            query,
            knowledge_home=self.home,
            repository=self.repository,
            routing_result=routing,
            legacy_retrieval=query_knowledge_index,
        )

    @staticmethod
    def _seed_formal_knowledge(home: Path) -> None:
        home.mkdir(parents=True)
        connection = sqlite3.connect(home / "knowledge.sqlite")
        try:
            with connection:
                connection.execute(
                    """
                    create table knowledge_items (
                        id integer primary key,
                        stable_key text not null unique,
                        title text not null,
                        body text not null,
                        kind text not null,
                        authority text not null,
                        status text not null,
                        hospital_scope text not null default '',
                        region_scope text not null default '',
                        module_scope text not null default '',
                        repo_scope text not null default '',
                        branch_scope text not null default '',
                        version_label text not null default '',
                        valid_from text not null default '',
                        valid_until text not null default '',
                        source_refs_json text not null,
                        tags_json text not null,
                        content_hash text not null,
                        created_at text not null,
                        updated_at text not null
                    )
                    """
                )
                connection.execute(
                    "create table knowledge_relations (source_key text, relation text, target_key text)"
                )
                connection.execute(
                    """
                    insert into knowledge_items (
                        stable_key, title, body, kind, authority, status,
                        version_label, source_refs_json, tags_json, content_hash,
                        created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "harness.orchestration.boundary",
                        "Harness 编排边界",
                        "Harness 是治理与编排层，不是云效、Git、数据库或其他提供方本身。",
                        "workflow",
                        "verified_code",
                        "active",
                        "integration-v1",
                        json.dumps(
                            [{"ref": "harness-governance-contract", "claim_level": "code"}],
                            ensure_ascii=False,
                        ),
                        json.dumps(["harness", "governance"], ensure_ascii=False),
                        "sha256:" + "a" * 64,
                        "2026-08-13T00:00:00Z",
                        "2026-08-13T00:00:00Z",
                    ),
                )
        finally:
            connection.close()

    def _manager_capability_config(self, knowledge_home: Path) -> Path:
        """Freeze the staged plugin for this isolated runtime test."""

        config_dir = Path(self.temp_dir.name) / "capability-config"
        config_dir.mkdir()
        plugin_root = PLUGIN_ROOT.resolve(strict=True)
        manifest_path = plugin_root / "capabilities.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_paths = {"capabilities.json", ".codex-plugin/plugin.json"}
        for capability in manifest["capabilities"]:
            source_paths.add(capability["entrypoint"])
            source_paths.update(capability.get("dependencies", []))
        sources = {
            path: hashlib.sha256((plugin_root / path).read_bytes()).hexdigest()
            for path in sorted(source_paths)
        }
        (config_dir / "plugin_inventory.json").write_text(
            json.dumps(
                {
                    "schema_version": "his-plugin-inventory.v1",
                    "plugins": [{
                        "name": manifest["plugin"],
                        "version": manifest["plugin_version"],
                        "capabilities_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                        "capabilities": [item["name"] for item in manifest["capabilities"]],
                        "sources_sha256": sources,
                    }],
                }
            ),
            encoding="utf-8",
        )
        config_path = config_dir / "capabilities.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "his-capability-runtime-config.v1",
                    "routing_mode": "enforce",
                    "plugin_roots": [str(plugin_root)],
                    "external_writes_default": False,
                    "default_timeout_seconds": 5,
                    "knowledge_home": str(knowledge_home.resolve()),
                }
            ),
            encoding="utf-8",
        )
        return config_path

    def test_formal_knowledge_answer_is_used_when_legacy_obsidian_index_is_absent(self) -> None:
        formal_home = Path(self.temp_dir.name) / "formal-only-knowledge"
        self._seed_formal_knowledge(formal_home)
        routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
            "Harness 编排边界",
            IntentContext(conversation_key="formal-knowledge-answer"),
            explicit_override="question",
        )

        service = build_manager_knowledge_capability_service(
            config_path=self._manager_capability_config(formal_home),
        )
        with mock.patch(
            "app.knowledge_consultation.query_knowledge_index",
            side_effect=AssertionError("formal manager question must not use legacy index"),
        ):
            result = consult_knowledge(
                "Harness 编排边界",
                knowledge_home=formal_home,
                repository=self.repository,
                routing_result=routing,
                capability_service=service,
            )

        self.assertTrue(result["answerable"])
        self.assertFalse(result["model_used"])
        self.assertFalse(result["model_escalation_required"])
        self.assertEqual("knowledge_hit", result["retrieval_status"])
        self.assertEqual(["harness.orchestration.boundary"], result["citations"])
        self.assertIn("Harness 是治理与编排层", result["answer"])
        self.assertEqual(1, self.repository.count_knowledge_consultations())

    def test_manager_question_uses_pinned_harness_scope_for_ambiguous_formal_knowledge(self) -> None:
        formal_home = Path(self.temp_dir.name) / "formal-scoped-knowledge"
        self._seed_formal_knowledge(formal_home)
        connection = sqlite3.connect(formal_home / "knowledge.sqlite")
        try:
            with connection:
                connection.execute(
                    "update knowledge_items set module_scope = ? where stable_key = ?",
                    ("Harness", "harness.orchestration.boundary"),
                )
                connection.execute(
                    """
                    insert into knowledge_items (
                        stable_key, title, body, kind, authority, status,
                        module_scope, version_label, source_refs_json, tags_json,
                        content_hash, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "dfhis.harness.ambiguous",
                        "Harness 编排边界（DFHIS）",
                        "DFHIS 中的同名边界不能替代 Harness 治理知识。",
                        "workflow",
                        "verified_code",
                        "active",
                        "DFHIS",
                        "integration-v1",
                        json.dumps(
                            [{"ref": "dfhis-boundary", "claim_level": "code"}],
                            ensure_ascii=False,
                        ),
                        json.dumps(["harness", "dfhis"], ensure_ascii=False),
                        "sha256:" + "b" * 64,
                        "2026-08-13T00:00:00Z",
                        "2026-08-13T00:00:00Z",
                    ),
                )
        finally:
            connection.close()
        routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
            "Harness 编排边界是什么？",
            IntentContext(conversation_key="formal-harness-scope"),
            explicit_override="question",
        )

        result = consult_knowledge(
            "Harness 编排边界是什么？",
            repository=self.repository,
            routing_result=routing,
            capability_service=build_manager_knowledge_capability_service(
                config_path=self._manager_capability_config(formal_home),
            ),
        )

        self.assertTrue(result["answerable"])
        self.assertEqual("knowledge_hit", result["retrieval_status"])
        self.assertEqual(["harness.orchestration.boundary"], result["citations"])
        self.assertEqual(1, self.repository.count_knowledge_consultations())

    def test_manager_question_answers_from_formal_seed_import(self) -> None:
        formal_home = Path(self.temp_dir.name) / "formal-seed-knowledge"
        imported = subprocess.run(
            [
                sys.executable,
                str(PLUGIN_ROOT / "scripts" / "import_seed.py"),
                "--home",
                str(formal_home),
                "--seed",
                str(PLUGIN_ROOT / "assets" / "seed_knowledge.json"),
            ],
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(0, imported.returncode, imported.stderr)
        routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
            "Harness 编排边界是什么？",
            IntentContext(conversation_key="seed-harness"),
            explicit_override="question",
        )

        result = consult_knowledge(
            "Harness 编排边界是什么？",
            repository=self.repository,
            routing_result=routing,
            capability_service=build_manager_knowledge_capability_service(
                config_path=self._manager_capability_config(formal_home),
            ),
        )

        self.assertTrue(result["answerable"])
        self.assertEqual("knowledge_hit", result["retrieval_status"])
        self.assertEqual(
            ["governance:harness-orchestration-boundary"], result["citations"]
        )
        self.assertIn("治理与编排层", result["answer"])
        self.assertEqual(1, self.repository.count_knowledge_consultations())

    def test_default_formal_builder_ignores_ambient_knowledge_home(self) -> None:
        formal_home = Path(self.temp_dir.name) / "formal-configured-knowledge"
        ambient_home = Path(self.temp_dir.name) / "ambient-knowledge"
        self._seed_formal_knowledge(formal_home)
        config_path = self._manager_capability_config(formal_home)
        with mock.patch.dict(os.environ, {"HIS_KNOWLEDGE_HOME": str(ambient_home)}):
            service = build_manager_knowledge_capability_service(config_path=config_path)
            routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
                "Harness 编排边界",
                IntentContext(conversation_key="frozen-knowledge-home"),
                explicit_override="question",
            )
            result = consult_knowledge(
                "Harness 编排边界",
                repository=self.repository,
                routing_result=routing,
                capability_service=service,
            )
        self.assertTrue(result["answerable"])
        self.assertFalse(ambient_home.exists())

    def test_formal_capability_failure_and_malformed_output_fail_closed(self) -> None:
        routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
            "Harness 编排边界",
            IntentContext(conversation_key="provider-failure"),
            explicit_override="question",
        )

        class BrokenService:
            def __init__(self, result: object) -> None:
                self.result = result
                self.calls = 0

            def route(self, request, **_kwargs):
                self.calls += 1
                return type("Route", (), {"result": self.result})()

        for name, provider_result, expected in (
            ("failed", {"status": "failed"}, "knowledge_provider_output_unsafe"),
            ("malformed", [], "knowledge_provider_output_unsafe"),
        ):
            with self.subTest(name=name):
                service = BrokenService(provider_result)
                result = consult_knowledge(
                    "Harness 编排边界",
                    repository=self.repository,
                    routing_result=routing,
                    capability_service=service,
                )
                self.assertEqual(1, service.calls)
                self.assertFalse(result["answerable"])
                self.assertEqual(expected, result["retrieval_status"])
                self.assertNotIn("answer", result)
                self.assertEqual([], result["citations"])

    def test_formal_sensitive_provider_output_fails_closed_without_audit_leak(self) -> None:
        routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
            "Harness 编排边界",
            IntentContext(conversation_key="provider-sensitive"),
            explicit_override="question",
        )
        class SensitiveService:
            def route(self, request, **_kwargs):
                return type("Route", (), {"result": {
                    "status": "success", "capability": request.capability,
                    "provider": request.provider, "mutation_level": "L0", "changed": False,
                    "data": {"answer_status": "answered", "answer": "token=SENTINEL_SECRET", "applicability": ["global"], "freshness": "current", "confidence_basis": ["verified"], "missing_information": [], "suggested_capabilities": []},
                    "evidence": [{"stable_key": "knowledge:test", "title": "safe", "authority": "verified", "version_label": "v1", "source_refs": [{"ref": "local:test"}], "excerpt": "safe"}],
                }})()

        result = consult_knowledge(
            "Harness 编排边界", repository=self.repository,
            routing_result=routing, capability_service=SensitiveService(),
        )
        self.assertFalse(result["answerable"])
        self.assertEqual("knowledge_provider_output_unsafe", result["retrieval_status"])
        self.assertNotIn("SENTINEL_SECRET", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("SENTINEL_SECRET", json.dumps(self.repository.list_knowledge_consultations(), ensure_ascii=False))

    def test_sensitive_manager_query_is_redacted_and_never_reaches_knowledge_provider(self) -> None:
        routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
            "普通问题",
            IntentContext(conversation_key="sensitive-question"),
            explicit_override="question",
        )

        class UnexpectedService:
            calls = 0

            def route(self, _request, **_kwargs):
                self.calls += 1
                raise AssertionError("sensitive query must not reach knowledge provider")

        service = UnexpectedService()
        sentinel = "SENTINEL_SENSITIVE_QUERY"
        result = consult_knowledge(
            "什么是以下内容：%257B%2522client_secret%2522%253A%2522"
            + sentinel
            + "%2522%257D",
            repository=self.repository,
            routing_result=routing,
            capability_service=service,
        )

        self.assertEqual(0, service.calls)
        self.assertFalse(result["answerable"])
        self.assertEqual("knowledge_insufficient", result["retrieval_status"])
        self.assertNotIn(sentinel, json.dumps(result, ensure_ascii=False))
        self.assertNotIn(
            sentinel,
            json.dumps(self.repository.list_knowledge_consultations(), ensure_ascii=False),
        )

    def test_formal_answer_identity_or_change_mismatch_fails_closed_after_one_route(self) -> None:
        routing = TaskIntentService(TaskIntentRepository(initialize=False)).route(
            "Harness 编排边界",
            IntentContext(conversation_key="provider-identity"),
            explicit_override="question",
        )

        class MismatchedService:
            def __init__(self, field: str, value: object) -> None:
                self.field = field
                self.value = value
                self.calls = 0

            def route(self, request, **_kwargs):
                self.calls += 1
                result = {
                    "status": "success", "request_id": request.request_id,
                    "capability": request.capability, "provider": request.provider,
                    "mutation_level": request.mutation_level.name, "changed": False,
                    "data": {"answer_status": "answered", "answer": "安全答案", "applicability": ["global"], "freshness": "current", "confidence_basis": ["verified"], "missing_information": [], "suggested_capabilities": []},
                    "evidence": [{"stable_key": "knowledge:test", "title": "safe", "authority": "verified", "version_label": "v1", "source_refs": [{"ref": "local:test"}], "excerpt": "safe"}],
                }
                result[self.field] = self.value
                return type("Route", (), {"result": result})()

        for field, value in (
            ("capability", "other.capability"),
            ("provider", "other-provider"),
            ("mutation_level", "L2"),
            ("changed", True),
        ):
            with self.subTest(field=field):
                service = MismatchedService(field, value)
                result = consult_knowledge(
                    "Harness 编排边界", repository=self.repository,
                    routing_result=routing, capability_service=service,
                )
                self.assertEqual(1, service.calls)
                self.assertFalse(result["answerable"])
                self.assertEqual("knowledge_provider_output_unsafe", result["retrieval_status"])
                self.assertNotIn("answer", result)
                self.assertEqual([], result["evidence"])

    def test_frozen_plugin_inventory_drift_fails_before_execution(self) -> None:
        formal_home = Path(self.temp_dir.name) / "drift-knowledge"
        config_path = self._manager_capability_config(formal_home)
        inventory_path = config_path.parent / "plugin_inventory.json"
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
        payload["plugins"][0]["sources_sha256"]["scripts/knowledge_answer.py"] = "0" * 64
        inventory_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "哈希"):
            build_manager_knowledge_capability_service(config_path=config_path)

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_verified_local_match_returns_without_model_and_records_redacted_consultation(self) -> None:
        result = self._consult("门诊收费如何处理")

        self.assertTrue(result["answerable"])
        self.assertFalse(result["model_used"])
        self.assertEqual("knowledge_hit", result["retrieval_status"])
        self.assertEqual(1, self.repository.count_knowledge_consultations())
        self.assertEqual(
            ["vault/10-his-rules/outpatient.md"],
            result["citations"],
        )

    def test_routed_question_is_allowed_to_consult_verified_knowledge(self) -> None:
        routing = TaskIntentService(TaskIntentRepository()).route(
            "门诊收费如何处理",
            IntentContext(conversation_key="knowledge-question"),
            explicit_override="question",
        )

        result = consult_knowledge(
            "门诊收费如何处理",
            knowledge_home=self.home,
            repository=self.repository,
            routing_result=routing,
            legacy_retrieval=query_knowledge_index,
        )

        self.assertEqual("question", routing.decision.mode)
        self.assertTrue(result["answerable"])

    def test_task_route_fails_closed_before_plain_knowledge_retrieval(self) -> None:
        routing = TaskIntentService(TaskIntentRepository()).route(
            "这个需求为什么要这样改？",
            IntentContext(conversation_key="knowledge-task"),
        )

        with mock.patch(
            "app.knowledge_consultation.query_knowledge_index"
        ) as retrieval:
            with self.assertRaisesRegex(
                ValueError,
                "knowledge_route_requires_question_intent",
            ):
                consult_knowledge(
                    "这个需求为什么要这样改？",
                    knowledge_home=self.home,
                    repository=self.repository,
                    routing_result=routing,
                )

        retrieval.assert_not_called()
        self.assertEqual(0, self.repository.count_knowledge_consultations())

    def test_omitted_or_none_receipt_fails_before_retrieval_or_audit(self) -> None:
        with mock.patch(
            "app.knowledge_consultation.query_knowledge_index"
        ) as retrieval:
            with self.assertRaises(TypeError):
                consult_knowledge(
                    "门诊收费如何处理",
                    knowledge_home=self.home,
                    repository=self.repository,
                )
            with self.assertRaisesRegex(
                ValueError,
                "knowledge_route_requires_question_intent",
            ):
                consult_knowledge(
                    "门诊收费如何处理",
                    knowledge_home=self.home,
                    repository=self.repository,
                    routing_result=None,
                )

        retrieval.assert_not_called()
        self.assertEqual(0, self.repository.count_knowledge_consultations())

    def test_receipt_repository_failure_is_generic_and_precedes_retrieval(self) -> None:
        routing = TaskIntentService(TaskIntentRepository()).route(
            "Python 的装饰器是什么？",
            IntentContext(conversation_key="repo-failure"),
        )

        with (
            mock.patch(
                "app.task_intent_service.TaskIntentRepository.verify_event",
                side_effect=RuntimeError("storage unavailable"),
            ),
            mock.patch(
                "app.knowledge_consultation.query_knowledge_index"
            ) as retrieval,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "knowledge_route_requires_question_intent",
            ):
                consult_knowledge(
                    "Python 的装饰器是什么？",
                    knowledge_home=self.home,
                    repository=self.repository,
                    routing_result=routing,
                )

        retrieval.assert_not_called()
        self.assertEqual(0, self.repository.count_knowledge_consultations())

    def test_unknown_cross_conversation_and_decision_tampered_receipts_fail_closed(self) -> None:
        routing = TaskIntentService(TaskIntentRepository()).route(
            "门诊收费如何处理",
            IntentContext(conversation_key="knowledge-receipt"),
            explicit_override="question",
        )
        forged_results = (
            replace(routing, event_id=routing.event_id + 999),
            replace(
                routing,
                decision=replace(
                    routing.decision,
                    conversation_key="knowledge-other",
                ),
            ),
            replace(
                routing,
                decision=replace(routing.decision, confidence="conservative"),
            ),
        )

        for forged in forged_results:
            with self.subTest(forged=forged), mock.patch(
                "app.knowledge_consultation.query_knowledge_index"
            ) as retrieval:
                with self.assertRaisesRegex(
                    ValueError,
                    "knowledge_route_requires_question_intent",
                ):
                    consult_knowledge(
                        "门诊收费如何处理",
                        knowledge_home=self.home,
                        repository=self.repository,
                        routing_result=forged,
                    )
                retrieval.assert_not_called()

        self.assertEqual(0, self.repository.count_knowledge_consultations())

    def test_candidate_or_expired_note_is_not_returned_as_direct_answer(self) -> None:
        for query in ("候选规则", "过期规则"):
            with self.subTest(query=query):
                result = self._consult(query)

                self.assertFalse(result["answerable"])
                self.assertFalse(result["model_used"])
                self.assertEqual("knowledge_insufficient", result["retrieval_status"])
                self.assertEqual([], result["citations"])
                self.assertIn("candidate", result["candidate_suggestion"])

    def test_knowledge_gap_returns_reviewer_only_candidate_recommendation_without_model(self) -> None:
        result = self._consult("不存在的业务知识")

        self.assertFalse(result["answerable"])
        self.assertFalse(result["model_used"])
        self.assertFalse(result["model_escalation_required"])
        self.assertEqual(
            {
                "candidate_type": "knowledge.candidate",
                "state": "candidate",
                "requires_reviewer": True,
                "auto_promote": False,
            },
            result["candidate_recommendation"],
        )

    def test_sensitive_consultation_is_recorded_redacted_and_original_is_only_hashed(self) -> None:
        query = (
            "token=SENTINEL_SECRET Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345 "
            "手机号13800138000 身份证11010519491231002X 怎么配置"
        )
        result = self._consult(query)

        rendered = json.dumps(
            self.repository.list_knowledge_consultations(),
            ensure_ascii=False,
        )
        api_rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("SENTINEL_SECRET", rendered)
        self.assertNotIn("SENTINEL_SECRET", api_rendered)
        self.assertNotIn("13800138000", rendered)
        self.assertNotIn("11010519491231002X", rendered)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", rendered)
        self.assertNotIn("query_hash", rendered)
        with database.connect() as connection:
            stored = connection.execute(
                "select query_redacted, query_hash from manager_knowledge_consultations"
            ).fetchone()
        self.assertEqual(
            "sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest(),
            stored["query_hash"],
        )
        self.assertNotEqual(query, stored["query_redacted"])
        manager_storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )
        self.assertNotIn(query.encode("utf-8"), manager_storage)

    def test_private_key_block_is_redacted_before_persistence(self) -> None:
        query = (
            "-----BEGIN PRIVATE KEY-----\nSENTINEL_PRIVATE_KEY\n"
            "-----END PRIVATE KEY----- 如何处理"
        )

        self._consult(query)

        rendered = json.dumps(
            self.repository.list_knowledge_consultations(),
            ensure_ascii=False,
        )
        self.assertNotIn("SENTINEL_PRIVATE_KEY", rendered)
        self.assertIn("[REDACTED_PRIVATE_KEY]", rendered)

    def test_basic_authorization_and_unterminated_private_key_are_redacted(self) -> None:
        query = (
            "Authorization: Basic SENTINEL_BASIC_AUTH\n"
            "-----BEGIN PRIVATE KEY-----\nSENTINEL_UNTERMINATED_KEY"
        )

        self._consult(query)

        rendered = json.dumps(
            self.repository.list_knowledge_consultations(),
            ensure_ascii=False,
        )
        self.assertNotIn("SENTINEL_BASIC_AUTH", rendered)
        self.assertNotIn("SENTINEL_UNTERMINATED_KEY", rendered)

    def test_encoded_nested_key_variants_and_separated_personal_data_persist_redacted(self) -> None:
        encoded = quote_plus(
            '{"nested":{"client_secret":"SENTINEL_CLIENT_SECRET"}}'
        )
        query = (
            f"{encoded} "
            '{"api_key":"SENTINEL_API_KEY",'
            '"personal_access_token":"SENTINEL_PERSONAL_TOKEN",'
            '"gitlab_pat":"SENTINEL_GITLAB_PAT",'
            '"aliyun_devops_pat":"SENTINEL_ALIYUN_PAT"} '
            "Authorization=Basic SENTINEL_BASIC_VALUE "
            "手机 138.0013.8000 身份证 110105-19491231-002X"
        )

        result = self._consult(query)

        rows = self.repository.list_knowledge_consultations()
        rendered = json.dumps([result, rows], ensure_ascii=False)
        self.assertEqual(1, len(rows))
        for sentinel in (
            "SENTINEL_CLIENT_SECRET",
            "SENTINEL_API_KEY",
            "SENTINEL_PERSONAL_TOKEN",
            "SENTINEL_GITLAB_PAT",
            "SENTINEL_ALIYUN_PAT",
            "SENTINEL_BASIC_VALUE",
            "138.0013.8000",
            "110105-19491231-002X",
        ):
            self.assertNotIn(sentinel, rendered)
        storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )
        self.assertNotIn(b"SENTINEL", storage)

    def test_encoding_deeper_than_decode_bound_fails_closed_without_secret_storage(self) -> None:
        query = '{"client_secret":"SENTINEL_DEEP_ENCODING"}'
        for _ in range(5):
            query = quote_plus(query)

        self._consult(query)

        rendered = json.dumps(
            self.repository.list_knowledge_consultations(),
            ensure_ascii=False,
        )
        self.assertNotIn("SENTINEL_DEEP_ENCODING", rendered)

    def test_reviewer_sensitive_vectors_never_persist_or_render_original_values(self) -> None:
        queries = {
            "unicode-json-key": (
                '{"outer":[{"\\u0063\\u006c\\u0069\\u0065\\u006e\\u0074\\u005f'
                '\\u0073\\u0065\\u0063\\u0072\\u0065\\u0074":'
                '"SENTINEL_UNICODE_JSON"}]}'
            ),
            "percent-u": "%u0063lient_secret%3DSENTINEL_PERCENT_U",
            "html-entity": "&#x63;lient_secret&#x3A;SENTINEL_HTML_ENTITY",
            "independent-pat": "pat=SENTINEL_INDEPENDENT_PAT",
            "encrypted-private-key": (
                "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
                "SENTINEL_ENCRYPTED_PRIVATE_KEY\n"
                "-----END ENCRYPTED PRIVATE KEY-----"
            ),
            "country-code-mobile": "+8613800138000",
        }

        for name, query in queries.items():
            with self.subTest(name=name):
                result = self._consult(query)
                rendered = json.dumps(
                    [result, self.repository.list_knowledge_consultations()],
                    ensure_ascii=False,
                )
                self.assertNotIn("SENTINEL", rendered)
                self.assertNotIn(query, rendered)

        manager_storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )
        self.assertNotIn(b"SENTINEL", manager_storage)
        for query in queries.values():
            self.assertNotIn(query.encode("utf-8"), manager_storage)

    def test_url_decode_preserves_literal_plus_in_safe_consultation_history(self) -> None:
        self._consult("C++ %25")

        row = self.repository.list_knowledge_consultations()[0]
        self.assertEqual("C++ %", row["query_redacted"])

    def test_strict_json_review_vectors_fail_closed_before_persistence(self) -> None:
        escaped_key = (
            "\\u0063\\u006c\\u0069\\u0065\\u006e\\u0074\\u005f"
            "\\u0073\\u0065\\u0063\\u0072\\u0065\\u0074"
        )
        queries = {
            "prefixed-json": (
                f'请检查前缀 {{"outer":[{{"{escaped_key}":'
                '"SENTINEL_PREFIX_JSON"}}]}'
            ),
            "malformed-tail-comma": (
                f'prefix {{"{escaped_key}":"SENTINEL_MALFORMED_JSON",}} suffix'
            ),
            "deep-nesting": (
                "[" * 70 + '"SENTINEL_DEEP_JSON"' + "]" * 70
            ),
            "over-char-limit": (
                '{"message":"SENTINEL_OVER_CHAR_' + "a" * 33_000 + '"}'
            ),
            "over-byte-limit": (
                '{"message":"SENTINEL_OVER_BYTE_' + "汉" * 22_000 + '"}'
            ),
            "over-node-limit": (
                '["SENTINEL_OVER_NODE",' + ",".join("0" for _ in range(10_100)) + "]"
            ),
        }

        for name, query in queries.items():
            with self.subTest(name=name):
                result = self._consult(query)
                rendered = json.dumps(
                    [result, self.repository.list_knowledge_consultations()],
                    ensure_ascii=False,
                )
                self.assertNotIn("SENTINEL", rendered)

        manager_storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )
        self.assertFalse(b"SENTINEL" in manager_storage)
        for query in queries.values():
            self.assertFalse(query.encode("utf-8") in manager_storage)

    def test_json_parser_recursion_error_fails_closed_without_original_storage(self) -> None:
        query = '{"message":"SENTINEL_JSON_RECURSION_ERROR"}'

        with mock.patch("app.sensitive_text.json.loads", side_effect=RecursionError):
            self._consult(query)

        rendered = json.dumps(
            self.repository.list_knowledge_consultations(),
            ensure_ascii=False,
        )
        manager_storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )
        self.assertNotIn("SENTINEL_JSON_RECURSION_ERROR", rendered)
        self.assertFalse(query.encode("utf-8") in manager_storage)


if __name__ == "__main__":
    unittest.main()
