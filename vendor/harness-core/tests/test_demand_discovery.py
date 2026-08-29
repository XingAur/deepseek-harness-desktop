from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.demand_discovery import discover_demand, extract_candidate_terms


def project(name: str, path: Path, role: str) -> dict:
    return {"name": name, "path": str(path), "role": role, "exists": True}


class DemandDiscoveryTests(unittest.TestCase):
    def test_bounds_candidate_terms_for_long_chinese_requirements(self) -> None:
        terms = extract_candidate_terms(
            "医保科反馈医保审批项目维护不方便，参考老系统把医保审批维护和医保对照做在一个页面。"
            "系统包括保险类别、院区、批量上传、批量审核、同步医保等级、医院目录和医保目录查询。"
            "医院目录支持医保编码、门诊不上传、住院不上传、门诊自费、住院自费和历史记录。" * 8
        )

        self.assertLessEqual(len(terms), 512)
        self.assertIn("医保审批", terms)
        self.assertIn("医保对照", terms)

    def test_links_ui_request_and_stored_field_from_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            bff = root / "df-bff-guahaosf"
            service = root / "df-mic-jj-menzhen"
            page = frontend / "src/pages/guaHaoChaX/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <df-select label=\"上午下午\" v-model=\"query.shangXiaWWsBz\" />
                // 0-上午，1-下午，2-晚上
                getGuaHaoPageList(query)
                """,
                encoding="utf-8",
            )
            api = frontend / "src/apis/guaHao.js"
            api.parent.mkdir(parents=True)
            api.write_text(
                "export const getGuaHaoPageList = (params) => request({ params })\n",
                encoding="utf-8",
            )
            controller = bff / "src/main/java/GuaHaoController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                "getGuaHaoPageList(String shangXiaWWsBz) {}\n",
                encoding="utf-8",
            )
            entity = service / "src/main/java/DO_MZ_GuaHao.java"
            entity.parent.mkdir(parents=True)
            entity.write_text(
                "/** 上下午标志 */ private Integer shangXiaWWsBz;\n",
                encoding="utf-8",
            )

            result = discover_demand(
                demand_text="收费病人查询增加上午下午筛选，默认全部",
                selected_projects=[
                    project("df-web-guahaosf", frontend, "frontend"),
                    project("df-bff-guahaosf", bff, "backend"),
                    project("df-mic-jj-menzhen", service, "backend"),
                ],
            )

        self.assertTrue(
            result.find_nodes(kind="ui", path_suffix="guaHaoChaX/index.vue")
        )
        self.assertTrue(
            result.find_nodes(kind="stored_field", identifier="shangXiaWWsBz")
        )
        self.assertTrue(
            result.find_edges(kind="request_flow", identifier="getGuaHaoPageList")
        )
        self.assertEqual(
            (("0", "上午"), ("1", "下午"), ("2", "晚上")),
            tuple((option.value, option.label) for option in result.enum_options),
        )

    def test_discovers_an_unrelated_field_without_named_case_logic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-patient"
            service = root / "df-mic-patient"
            page = frontend / "src/pages/patient/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <df-select label=\"就诊状态\" v-model=\"filters.visitState\" />
                queryVisitList(filters)
                """,
                encoding="utf-8",
            )
            controller = service / "src/main/java/PatientController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                "queryVisitList(String visitState) {}\n",
                encoding="utf-8",
            )
            entity = service / "src/main/java/PatientRecord.java"
            entity.write_text(
                "/** 就诊状态 */ private String visitState;\n",
                encoding="utf-8",
            )

            result = discover_demand(
                demand_text="病人列表增加就诊状态筛选",
                selected_projects=[
                    project("df-web-patient", frontend, "frontend"),
                    project("df-mic-patient", service, "backend"),
                ],
            )

        self.assertTrue(result.find_nodes(kind="stored_field", identifier="visitState"))
        self.assertTrue(
            result.find_edges(kind="request_flow", identifier="queryVisitList")
        )

    def test_does_not_promote_generic_getters_to_cross_service_request_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            service = root / "df-mic-yibaogl"
            page = frontend / "src/views/catalog/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <!-- 医保目录 医院目录 分页 -->
                <df-data-grid :data="rows" />
                getPageIndex(query)
                getPageSize(query)
                getYiLiaoBxId(row)
                getYiYuanMuLuPage(query)
                """,
                encoding="utf-8",
            )
            source = service / "src/main/java/CatalogService.java"
            source.parent.mkdir(parents=True)
            source.write_text(
                """
                // 医保目录 医院目录 分页
                getPageIndex(query) {}
                getPageSize(query) {}
                getYiLiaoBxId(row) {}
                getYiYuanMuLuPage(query) {}
                """,
                encoding="utf-8",
            )

            result = discover_demand(
                demand_text="医保目录查询增加医院目录分页",
                selected_projects=[
                    project("df-web-yibaogl", frontend, "frontend"),
                    project("df-mic-yibaogl", service, "backend"),
                ],
            )

        request_identifiers = {
            edge.identifier for edge in result.find_edges(kind="request_flow")
        }
        self.assertIn("getYiYuanMuLuPage", request_identifiers)
        self.assertNotIn("getPageIndex", request_identifiers)
        self.assertNotIn("getPageSize", request_identifiers)
        self.assertNotIn("getYiLiaoBxId", request_identifiers)

    def test_keeps_enum_evidence_scoped_to_the_selected_bound_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            page = frontend / "src/pages/guaHaoChaX/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                const periodColumn = {
                  dataField: 'shangXiaWWsBz', caption: '上下午',
                  map: { '0': '上午', '1': '下午', '2': '晚上' }
                }
                const chargeStatusMap = { '0': '未收费', '1': '已收费' }
                getGuaHaoPageList(query)
                """,
                encoding="utf-8",
            )
            api = frontend / "src/apis/guaHao.js"
            api.parent.mkdir(parents=True)
            api.write_text(
                "export const getGuaHaoPageList = (params) => request({ params })\n",
                encoding="utf-8",
            )
            service = root / "df-mic-jj-menzhen"
            entity = service / "src/main/java/DO_MZ_GuaHao.java"
            entity.parent.mkdir(parents=True)
            entity.write_text(
                "/** 上下午晚上标志 */ private Integer shangXiaWWsBz;\n",
                encoding="utf-8",
            )

            result = discover_demand(
                demand_text="收费病人查询增加上午下午筛选，默认全部",
                selected_projects=[
                    project("df-web-guahaosf", frontend, "frontend"),
                    project("df-mic-jj-menzhen", service, "backend"),
                ],
            )

        self.assertEqual(
            (("0", "上午"), ("1", "下午"), ("2", "晚上")),
            tuple((option.value, option.label) for option in result.enum_options),
        )

    def test_prefers_a_field_with_a_nearby_requirement_label_over_a_popular_generic_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            page = frontend / "src/pages/guaHaoChaX/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <df-card-reader v-model="query.jiuZhenKh" placeholder="就诊卡号" />
                <df-data-grid :columns="[{ dataField: 'shangXiaWWsBz', caption: '上下午' }]" />
                const map = { '0': '上午', '1': '下午', '2': '晚上' }
                getGuaHaoPageList(query)
                """,
                encoding="utf-8",
            )
            api = frontend / "src/apis/guaHao.js"
            api.parent.mkdir(parents=True)
            api.write_text(
                "export const getGuaHaoPageList = (params) => request({ params })\n",
                encoding="utf-8",
            )
            service = root / "df-mic-jj-menzhen"
            source_root = service / "src/main/java"
            source_root.mkdir(parents=True)
            (source_root / "DO_MZ_GuaHao.java").write_text(
                """
                /** 上下午晚上标志 */ private Integer shangXiaWWsBz;
                private String jiuZhenKh;
                """,
                encoding="utf-8",
            )
            for index in range(8):
                (source_root / f"DTO_{index}.java").write_text(
                    "private String jiuZhenKh;\n",
                    encoding="utf-8",
                )

            result = discover_demand(
                demand_text="挂号收费病人查询增加上午下午筛选，默认全部",
                selected_projects=[
                    project("df-web-guahaosf", frontend, "frontend"),
                    project("df-mic-jj-menzhen", service, "backend"),
                ],
            )

        self.assertEqual("shangXiaWWsBz", result.target_field)

    def test_bounds_the_evidence_graph_when_many_files_match_a_broad_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-registration"
            source_root = frontend / "src/pages"
            source_root.mkdir(parents=True)
            for index in range(360):
                (source_root / f"page_{index}.vue").write_text(
                    (
                        f"<!-- 挂号页面查询 -->\n"
                        f"const requestField{index} = queryValue{index}\n"
                    ),
                    encoding="utf-8",
                )

            result = discover_demand(
                demand_text="挂号页面增加查询条件",
                selected_projects=[project("df-web-registration", frontend, "frontend")],
                max_files=360,
            )

        self.assertLessEqual(len(result.graph.nodes), 240)


if __name__ == "__main__":
    unittest.main()
