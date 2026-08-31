from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.change_context_collectors import (
    ChangeScopeCollector,
    CodeGraphCollector,
    ProjectGraphCollector,
)
from app.task_context import TaskIntentContext
from app.technical_decision import discover_technical_context


class ChangeContextLocalCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.frontend = self.root / "df-web-demo"
        (self.frontend / "src/pages").mkdir(parents=True)
        (self.frontend / "tests").mkdir()
        (self.frontend / "src/pages/list.vue").write_text("<template>列表</template>\n", encoding="utf-8")
        (self.frontend / "tests/list.test.js").write_text("test('list', () => {})\n", encoding="utf-8")
        self.discovery = discover_technical_context(
            demand_text="仅调整列表样式",
            project_root=self.root,
            explicit_project_paths=[str(self.frontend)],
            explicit_allowed_paths=["src/pages/list.vue"],
        )
        self.intent = TaskIntentContext(
            background="列表布局需要调整。",
            goal="仅调整列表样式。",
            scenarios=("打开列表",),
            desired_outcome="布局正确。",
            constraints=("不修改接口",),
            acceptance_criteria=("页面测试通过",),
            source_refs=("evidence://requirement/one",),
        )

    def test_project_graph_is_bounded_and_has_relationship_fingerprint(self) -> None:
        collected = ProjectGraphCollector().collect(self.discovery)
        self.assertEqual("project_graph", collected.layer_type)
        self.assertEqual("complete", collected.status)
        self.assertTrue(collected.source_fingerprint.startswith("sha256:"))
        serialized = str(collected.payload)
        self.assertNotIn(str(self.root), serialized)
        self.assertIn("df-web-demo", serialized)

    def test_change_scope_binds_intent_requirement_revision_and_user_correction(self) -> None:
        collected = ChangeScopeCollector().collect(
            task_context=self.intent,
            normalized_requirement_evidence={
                "source_type": "manual",
                "ticket_id": "LOCAL-1",
                "revision": "rev-1",
                "comments": [{"content_hash": "sha256:" + "b" * 64}],
                "attachments": [],
            },
            current_user_correction="只调整样式，不改接口。",
            calibrated_scope={"do": "调整样式", "do_not": ["接口变更"]},
        )
        self.assertEqual("complete", collected.status)
        self.assertEqual(self.intent.content_hash, collected.payload["task_intent_hash"])
        changed = ChangeScopeCollector().collect(
            task_context=self.intent,
            normalized_requirement_evidence={"source_type": "manual", "ticket_id": "LOCAL-1", "revision": "rev-2", "comments": [], "attachments": []},
            current_user_correction="只调整样式，不改接口。",
            calibrated_scope={"do": "调整样式", "do_not": ["接口变更"]},
        )
        self.assertNotEqual(collected.source_fingerprint, changed.source_fingerprint)

    def test_incomplete_intent_blocks_change_scope(self) -> None:
        collected = ChangeScopeCollector().collect(
            task_context=TaskIntentContext.empty(),
            normalized_requirement_evidence={},
            current_user_correction="",
            calibrated_scope={},
        )
        self.assertEqual("incomplete", collected.status)
        self.assertTrue(collected.blockers)

    def test_code_graph_contains_only_relevant_paths_tests_and_hashes(self) -> None:
        collected = CodeGraphCollector(max_paths=16).collect(self.discovery)
        self.assertEqual("complete", collected.status)
        self.assertIn("src/pages/list.vue", collected.payload["target_paths"])
        self.assertTrue(collected.payload["tests"])
        self.assertNotIn("full_source", collected.payload)
        first = collected.source_fingerprint
        (self.frontend / "unrelated.txt").write_text("ignored", encoding="utf-8")
        self.assertEqual(first, CodeGraphCollector(max_paths=16).collect(self.discovery).source_fingerprint)
        (self.frontend / "src/pages/list.vue").write_text("<template>changed</template>\n", encoding="utf-8")
        self.assertNotEqual(first, CodeGraphCollector(max_paths=16).collect(self.discovery).source_fingerprint)

    def test_missing_project_or_call_chain_is_incomplete(self) -> None:
        self.frontend.rename(self.root / "moved")
        collected = CodeGraphCollector(max_paths=16).collect(self.discovery)
        self.assertEqual("incomplete", collected.status)
        self.assertTrue(collected.blockers)


if __name__ == "__main__":
    unittest.main()
