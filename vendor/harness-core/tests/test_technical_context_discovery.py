from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.change_context_contracts import ChangeContextProjection
from app.technical_decision import build_technical_decision, discover_technical_context


PACK_ID = "ccp:sha256:" + "a" * 64


class TechnicalContextDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "df-web-demo"
        (self.project / "src/pages").mkdir(parents=True)
        (self.project / "src/pages/list.vue").write_text(
            "<template><div>患者列表</div></template>\n<script>export default {}</script>\n",
            encoding="utf-8",
        )
        (self.project / "package.json").write_text('{"scripts":{"test":"echo ok"}}', encoding="utf-8")

    def _discover(self):
        return discover_technical_context(
            demand_text="仅调整患者列表页面文案",
            project_root=self.root,
            explicit_project_paths=[str(self.project)],
            explicit_allowed_paths=["src/pages/list.vue"],
        )

    def test_discovery_contains_readonly_inputs_without_implementation_approval(self) -> None:
        result = self._discover()
        self.assertTrue(result.selected_projects)
        self.assertEqual(("src/pages/list.vue",), result.explicit_allowed_paths)
        self.assertFalse(hasattr(result, "implementation_decision"))
        self.assertFalse(hasattr(result, "can_patch"))
        self.assertTrue(result.demand_discovery.to_dict()["schema_version"])

    def test_governed_decision_blocks_without_ready_analysis_projection(self) -> None:
        result = build_technical_decision(
            demand_text="仅调整患者列表页面文案",
            discovery=self._discover(),
        )
        self.assertFalse(result.can_patch)
        self.assertIn("ChangeContextPack", "\n".join(result.implementation_decision["blockers"]))

    def test_ready_analysis_projection_unlocks_existing_decision_logic(self) -> None:
        projection = ChangeContextProjection.create(
            pack_id=PACK_ID,
            role="analysis",
            tier0={"gate_status": "ready", "gate_code": "CHANGE_CONTEXT_READY"},
            tier1={"allowed_paths": ["src/pages/list.vue"]},
            opened_evidence_refs=(),
        )
        result = build_technical_decision(
            demand_text="仅调整患者列表页面文案",
            discovery=self._discover(),
            change_context_projection=projection,
        )
        self.assertTrue(result.can_patch)
        self.assertEqual(["src/pages/list.vue"], result.recommended_allowed_paths)

    def test_legacy_call_remains_compatible(self) -> None:
        result = build_technical_decision(
            demand_text="仅调整患者列表页面文案",
            project_root=self.root,
            explicit_project_paths=[str(self.project)],
            explicit_allowed_paths=["src/pages/list.vue"],
        )
        self.assertTrue(result.can_patch)


if __name__ == "__main__":
    unittest.main()
