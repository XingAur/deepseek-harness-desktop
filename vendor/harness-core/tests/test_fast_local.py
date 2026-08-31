from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.fast_local import build_fast_local_decision


class FastLocalDecisionTests(unittest.TestCase):
    def test_explicit_single_repo_frontend_change_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "df-web-guahaosf"
            target = project / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text("<template />\n", encoding="utf-8")

            decision = build_fast_local_decision(
                title="挂号查询条件在低分辨率下换行",
                demand_text="调整挂号查询顶部条件区域的响应式布局，不改变查询参数和业务逻辑。",
                project_paths=[str(project)],
                allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
            )

        self.assertTrue(decision["eligible"])
        self.assertEqual("fast_local", decision["route"])
        self.assertEqual([], decision["blockers"])

    def test_sort_contract_change_is_not_eligible(self) -> None:
        decision = build_fast_local_decision(
            title="挂号病人查询默认排序",
            demand_text="前端调用 getGuaHaoPageList 时传 sortField，后端按排序字段处理。",
            project_paths=["/tmp/df-web-guahaosf"],
            allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
        )

        self.assertFalse(decision["eligible"])
        self.assertTrue(any("跨层接口或排序契约" in item for item in decision["blockers"]))

    def test_high_risk_business_change_is_not_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "df-web-guahaosf"
            target = project / "src/pages/menZhenTf/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text("<template />\n", encoding="utf-8")

            decision = build_fast_local_decision(
                title="医保患者部分退费校验",
                demand_text="部分退费前校验在院状态。",
                project_paths=[str(project)],
                allowed_paths=["src/pages/menZhenTf/index.vue"],
            )

        self.assertFalse(decision["eligible"])
        self.assertTrue(any("医保、收费、结算" in item for item in decision["blockers"]))

    def test_charging_module_prefix_does_not_block_pure_query_ui_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "df-web-guahaosf"
            target = project / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text("<template />\n", encoding="utf-8")

            decision = build_fast_local_decision(
                title="【宁远人民医院】挂号收费--挂号病人查询切换标签页不要刷新",
                demand_text="输入查询条件后切换顶部业务页签再返回，已输入条件和查询结果不能被清空。",
                project_paths=[str(project)],
                allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
            )

        self.assertTrue(decision["eligible"])
        self.assertEqual([], decision["blockers"])

    def test_missing_explicit_path_is_not_eligible(self) -> None:
        decision = build_fast_local_decision(
            title="挂号页面文字调整",
            demand_text="调整页面提示文字。",
            project_paths=["/tmp/df-web-guahaosf"],
            allowed_paths=[],
        )

        self.assertFalse(decision["eligible"])
        self.assertTrue(any("需要显式白名单路径" in item for item in decision["blockers"]))

    def test_inferred_paths_are_not_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "df-web-guahaosf"
            target = project / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text("<template />\n", encoding="utf-8")

            decision = build_fast_local_decision(
                title="挂号页面文字调整",
                demand_text="调整页面提示文字。",
                project_paths=[str(project)],
                allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
                project_path_is_explicit=False,
                allowed_paths_are_explicit=False,
            )

        self.assertFalse(decision["eligible"])
        self.assertTrue(any("调用方显式指定业务项目路径" in item for item in decision["blockers"]))
        self.assertTrue(any("调用方显式指定白名单路径" in item for item in decision["blockers"]))
