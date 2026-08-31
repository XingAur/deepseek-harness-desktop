from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import database
from app.change_context_applicability import CandidateTarget
from app.change_context_artifacts import ChangeContextArtifactStore
from app.change_context_collectors import (
    ChangeScopeCollector,
    CodeGraphCollector,
    CollectedContextLayer,
    ProjectGraphCollector,
)
from app.change_context_contracts import content_hash
from app.change_context_gate import ChangeContextGate
from app.change_context_projection import ChangeContextProjectionService
from app.change_context_repository import ChangeContextRepository
from app.change_context_service import ChangeContextService
from app.task_context import TaskIntentContext
from app.technical_decision import discover_technical_context


class CountingCollector:
    def __init__(self, collector) -> None:
        self.collector = collector
        self.calls = 0

    def collect(self, *args, **kwargs):
        self.calls += 1
        return self.collector.collect(*args, **kwargs)


class CompleteDataCollector:
    def __init__(self) -> None:
        self.calls = 0

    def collect(self, *, connection_alias, schema, tables, task_id, run_id):
        del task_id, run_id
        self.calls += 1
        payload = {
            "schema_version": "data-graph.v1",
            "connection_alias": connection_alias,
            "schema": schema,
            "tables": [
                {
                    "name": table,
                    "columns": [{"name": "id", "type": "bigint", "nullable": False}],
                    "constraints": [],
                    "indexes": [],
                    "foreign_keys": [],
                }
                for table in tables
            ],
            "mcp_receipts": [],
            "missing": [],
            "conflicts": [],
        }
        return CollectedContextLayer(
            layer_type="data_graph",
            status="complete",
            payload=payload,
            source_fingerprint=content_hash(payload),
            evidence_refs=("mcp-evidence:postgresql:test:catalog",),
            policy_rule_ids=("CTX-DATA-MCP-ONLY",),
            blockers=(),
        )


class ChangeContextServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "web"
        (self.project / "src").mkdir(parents=True)
        (self.project / "tests").mkdir()
        (self.project / "src/list.vue").write_text("<template>list</template>\n", encoding="utf-8")
        (self.project / "tests/list.test.js").write_text("test('list', () => {})\n", encoding="utf-8")
        self.discovery = discover_technical_context(
            demand_text="只调整列表样式",
            project_root=self.root,
            explicit_project_paths=[str(self.project)],
            explicit_allowed_paths=["src/list.vue"],
        )
        self.intent = TaskIntentContext(
            background="列表布局不统一。",
            goal="只调整列表样式。",
            scenarios=("打开列表",),
            desired_outcome="布局统一。",
            constraints=("不改接口",),
            acceptance_criteria=("页面测试通过",),
            source_refs=("evidence://requirement/local",),
        )
        self.evidence = {
            "source_type": "manual",
            "ticket_id": "LOCAL-1",
            "revision": "rev-1",
            "comments": [],
            "attachments": [],
        }
        self.style_target = CandidateTarget(
            repository_alias="web",
            relative_path="src/list.vue",
            target_kind="style_only",
            evidence_refs=("evidence://code/list",),
            relationships=(),
        )
        self.factory = lambda: database.connect_database(self.root / "harness.sqlite")
        database.init_db(connection_factory=self.factory)
        store = ChangeContextArtifactStore(self.root / "artifacts")
        self.repository = ChangeContextRepository(self.factory, store)
        self.project_collector = CountingCollector(ProjectGraphCollector())
        self.scope_collector = CountingCollector(ChangeScopeCollector())
        self.code_collector = CountingCollector(CodeGraphCollector())
        self.data_collector = CompleteDataCollector()
        self.service = ChangeContextService(
            repository=self.repository,
            project_collector=self.project_collector,
            change_scope_collector=self.scope_collector,
            code_collector=self.code_collector,
            data_collector=self.data_collector,
            gate=ChangeContextGate(),
            projection_service=ChangeContextProjectionService(repository=self.repository),
        )

    def _build(self, **overrides):
        values = {
            "discovery": self.discovery,
            "task_context": self.intent,
            "normalized_requirement_evidence": self.evidence,
            "current_user_correction": "只调整样式，不修改接口。",
            "calibrated_scope": {"do": "样式", "do_not": ["接口"]},
            "candidate_targets": (self.style_target,),
            "task_id": "task-1",
            "run_id": "run-1",
        }
        values.update(overrides)
        return self.service.build(**values)

    def test_collecting_snapshot_becomes_ready_and_renders_all_roles(self) -> None:
        result = self._build()
        self.assertEqual("ready", result.pack.status)
        self.assertEqual("CHANGE_CONTEXT_READY", result.gate.code)
        self.assertEqual(2, result.pack.pack_version)
        self.assertEqual(
            {"manager", "analysis", "implementation", "review", "knowledge_answer"},
            set(result.projections),
        )
        self.assertEqual("not_applicable", result.layer("data_graph").status)
        self.assertEqual(0, self.data_collector.calls)
        with closing(self.factory()) as connection:
            statuses = [row[0] for row in connection.execute("select status from change_context_packs order by pack_version")]
        self.assertEqual(["collecting", "ready"], statuses)

    def test_same_pack_retry_reuses_all_layers_without_running_collectors(self) -> None:
        first = self._build()
        counts = (self.project_collector.calls, self.scope_collector.calls, self.code_collector.calls, self.data_collector.calls)
        retried = self._build(reuse_pack_id=first.pack.pack_id, run_id="run-2")
        self.assertEqual(first.pack.pack_id, retried.pack.pack_id)
        self.assertEqual(4, retried.reused_layer_count)
        self.assertEqual(0, retried.recollected_layer_count)
        self.assertEqual(counts, (self.project_collector.calls, self.scope_collector.calls, self.code_collector.calls, self.data_collector.calls))

    def test_new_data_decision_revalidates_once_then_retry_does_not_call_mcp_collector(self) -> None:
        backend_target = CandidateTarget(
            repository_alias="web",
            relative_path="src/list.vue",
            target_kind="frontend_save_path",
            evidence_refs=("evidence://code/list-save",),
            relationships=("persistence_call",),
        )
        first = self._build(
            candidate_targets=(backend_target,),
            data_connection_alias="his_test_readonly",
            data_schema="public",
            data_tables=("patient",),
        )
        self.assertEqual("ready", first.pack.status)
        self.assertEqual(1, self.data_collector.calls)
        self._build(
            candidate_targets=(backend_target,),
            data_connection_alias="his_test_readonly",
            data_schema="public",
            data_tables=("patient",),
            reuse_pack_id=first.pack.pack_id,
            run_id="run-review",
        )
        self.assertEqual(1, self.data_collector.calls)

    def test_correction_creates_superseding_lineage_and_invalidates_old_pack(self) -> None:
        first = self._build()
        corrected = self._build(current_user_correction="用户修正：还要调整按钮间距。", run_id="run-2")
        self.assertEqual("ready", corrected.pack.status)
        self.assertGreater(corrected.pack.pack_version, first.pack.pack_version)
        self.assertEqual("BLOCKED_CONTEXT_STALE", ChangeContextGate().evaluate(first.pack, self.repository).code)
        with closing(self.factory()) as connection:
            statuses = [row[0] for row in connection.execute("select status from change_context_packs order by pack_version")]
        self.assertEqual(["collecting", "ready", "superseded", "collecting", "ready"], statuses)

    def test_retry_after_interruption_finalizes_matching_collecting_snapshot(self) -> None:
        original_create = self.repository.create_pack_snapshot
        calls = 0

        def interrupt_before_final(pack, applicability_decisions=()):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated_process_interruption")
            return original_create(pack, applicability_decisions)

        self.repository.create_pack_snapshot = interrupt_before_final  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "simulated_process_interruption"):
            self._build()
        self.repository.create_pack_snapshot = original_create  # type: ignore[method-assign]

        retried = self._build(run_id="run-retry")

        self.assertEqual("ready", retried.pack.status)
        with closing(self.factory()) as connection:
            statuses = [row[0] for row in connection.execute("select status from change_context_packs order by pack_version")]
        self.assertEqual(["collecting", "ready"], statuses)

    def test_correction_after_interruption_supersedes_abandoned_collection(self) -> None:
        original_create = self.repository.create_pack_snapshot
        calls = 0

        def interrupt_before_final(pack, applicability_decisions=()):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated_process_interruption")
            return original_create(pack, applicability_decisions)

        self.repository.create_pack_snapshot = interrupt_before_final  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "simulated_process_interruption"):
            self._build()
        self.repository.create_pack_snapshot = original_create  # type: ignore[method-assign]

        corrected = self._build(
            current_user_correction="用户修正：还要调整按钮间距。",
            run_id="run-corrected",
        )

        self.assertEqual("ready", corrected.pack.status)
        with closing(self.factory()) as connection:
            statuses = [row[0] for row in connection.execute("select status from change_context_packs order by pack_version")]
        self.assertEqual(["collecting", "superseded", "collecting", "ready"], statuses)

    def test_required_data_without_current_collector_evidence_finishes_blocked(self) -> None:
        service = ChangeContextService(
            repository=self.repository,
            project_collector=self.project_collector,
            change_scope_collector=self.scope_collector,
            code_collector=self.code_collector,
            data_collector=None,
            gate=ChangeContextGate(),
            projection_service=ChangeContextProjectionService(repository=self.repository),
        )
        backend_target = CandidateTarget(
            repository_alias="web",
            relative_path="src/list.vue",
            target_kind="frontend_save_path",
            evidence_refs=("evidence://code/list-save",),
            relationships=("persistence_call",),
        )
        result = service.build(
            discovery=self.discovery,
            task_context=self.intent,
            normalized_requirement_evidence=self.evidence,
            current_user_correction="保存路径变更。",
            calibrated_scope={"do": "保存"},
            candidate_targets=(backend_target,),
            task_id="task-1",
            run_id="run-1",
            data_connection_alias="",
            data_schema="",
            data_tables=(),
        )
        self.assertEqual("blocked", result.pack.status)
        self.assertEqual("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE", result.gate.code)
        self.assertEqual({}, result.projections)


if __name__ == "__main__":
    unittest.main()
