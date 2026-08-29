from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.technical_decision import (
    build_service_graph,
    build_technical_decision,
    default_project_candidates,
    decide_discovered_stored_filter,
    demand_project_terms,
    discover_frontend_projects,
    endpoint_target_project,
    frontend_entry_roots,
    infer_ui_terms,
    is_broad_feature_requirement,
)


class GeneralProjectRoutingTests(unittest.TestCase):
    def test_explicit_frontend_project_paths_keep_entry_evidence_for_service_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "医保审批项目维护\nrequest('/winbff-yibaogl/duiZhao/page')\n",
                encoding="utf-8",
            )
            bff = root / "df-bff-yibaogl"
            controller = bff / "src/main/java/DuiZhaoController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/duiZhao") class DuiZhaoController {\n'
                ' @GetMapping("/page") void page() {}\n}\n',
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="医保审批项目维护。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(bff)],
            )

        graph = result.field_provenance["service_graph"]
        self.assertNotEqual("not_applicable", graph["status"])
        self.assertTrue(any(branch["target_project"] == "df-bff-yibaogl" for branch in graph["branches"]))

    def test_ui_terms_do_not_inject_unrelated_legacy_business_cases(self) -> None:
        terms = infer_ui_terms("医保审批项目维护和医保对照支持批量上传、批量审核。")

        self.assertIn("医保审批", terms)
        self.assertIn("医保对照", terms)
        self.assertNotIn("预交金", terms)
        self.assertNotIn("结算收款", terms)

    def test_service_graph_follows_page_imports_without_scanning_global_api_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "医保审批项目维护\n"
                "import api from './api/yiBaoSpXmApi'\n"
                "api.query()\n",
                encoding="utf-8",
            )
            relevant_api = page.parent / "api/yiBaoSpXmApi.js"
            relevant_api.parent.mkdir(parents=True, exist_ok=True)
            relevant_api.write_text(
                "request('/winbff-yibaogl/duiZhao/page')\n",
                encoding="utf-8",
            )
            unrelated_api = frontend / "src/apis/legacy.js"
            unrelated_api.parent.mkdir(parents=True)
            unrelated_api.write_text(
                "request('/yb-service/unrelated/legacy')\n",
                encoding="utf-8",
            )
            bff = root / "df-bff-yibaogl"
            controller = bff / "src/main/java/DuiZhaoController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/duiZhao") class DuiZhaoController {\n'
                ' @GetMapping("/page") void page() {}\n}\n',
                encoding="utf-8",
            )

            selected = discover_frontend_projects(
                combined_text="医保审批项目维护。",
                root=root,
                selected_projects=[],
            )
            graph = build_service_graph(
                combined_text="医保审批项目维护。",
                root=root,
                selected_projects=selected,
                restrict_to_selected_projects=False,
            )

        endpoints = {branch["endpoint"] for branch in graph["branches"]}
        unresolved = {item["endpoint"] for item in graph["unresolved_endpoints"]}
        self.assertIn("/winbff-yibaogl/duiZhao/page", endpoints)
        self.assertNotIn("/yb-service/unrelated/legacy", endpoints | unresolved)

    def test_extracts_business_terms_without_generic_sentence_prefixes(self) -> None:
        terms = demand_project_terms(
            "医保科反馈医保审批项目维护不方便，参考老系统把医保审批维护和医保对照做在一个页面，"
            "支持批量上传、批量审核和同步医保等级。是否可以考虑优化功能。"
        )

        self.assertIn("医保审批", terms)
        self.assertIn("医保对照", terms)
        self.assertIn("批量上传", terms)
        self.assertNotIn("是否可以", terms)
        self.assertNotIn("需求或问题描述", terms)

    def test_discovers_the_business_frontend_from_exact_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "df-web-yibaogl"
            page = target / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "<template>医保审批 医保对照 批量上传</template>\n"
                "request('/winbff-yibaogl/duiZhao/page')\n",
                encoding="utf-8",
            )
            generic = root / "df-web-menzhenysz"
            generic_page = generic / "src/pages/yiBaoShenHe/index.vue"
            generic_page.parent.mkdir(parents=True)
            generic_page.write_text("是否可以优化这个页面\n", encoding="utf-8")

            selected = discover_frontend_projects(
                combined_text="医保审批项目维护和医保对照支持批量上传。",
                root=root,
                selected_projects=[],
            )

        self.assertEqual("df-web-yibaogl", selected[0]["name"])
        self.assertTrue(selected[0]["entry_matches"])
        self.assertNotIn("df-web-menzhenysz", [item["name"] for item in selected])

    def test_enriches_a_default_frontend_with_discovered_entry_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "df-web-yibaogl"
            page = target / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text("医保审批项目维护\n", encoding="utf-8")

            selected = discover_frontend_projects(
                combined_text="医保审批项目维护，支持编辑医保编码。",
                root=root,
                selected_projects=[
                    {
                        "name": "df-web-yibaogl",
                        "path": str(target),
                        "role": "frontend",
                        "score": 105,
                        "exists": True,
                        "reasons": [],
                    }
                ],
            )

        target_item = next(item for item in selected if item["name"] == "df-web-yibaogl")
        self.assertTrue(target_item["entry_matches"])

    def test_does_not_append_generic_frontends_when_domain_frontend_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "df-web-yibaogl"
            page = target / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text("医保审批项目维护\n", encoding="utf-8")
            unrelated = root / "df-web-generic"
            generic_page = unrelated / "src/pages/list/index.vue"
            generic_page.parent.mkdir(parents=True)
            generic_page.write_text("医保编码\n", encoding="utf-8")

            selected = discover_frontend_projects(
                combined_text="医保审批项目维护，支持编辑医保编码。",
                root=root,
                selected_projects=[
                    {
                        "name": "df-web-yibaogl",
                        "path": str(target),
                        "role": "frontend",
                        "score": 105,
                        "exists": True,
                        "reasons": [],
                    }
                ],
            )

        self.assertEqual(["df-web-yibaogl"], [item["name"] for item in selected])

    def test_maps_yibaogl_http_prefix_and_keeps_multiple_frontend_branches(self) -> None:
        self.assertEqual("df-bff-yibaogl", endpoint_target_project("/winbff-yibaogl/duiZhao/page"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "医保审批 医保对照\n"
                "import api from '@/apis/duiZhao'\n"
                "const gateway = '/winbff-yibaogl'\n"
                "request(gateway + '/duiZhao/page')\n",
                encoding="utf-8",
            )
            api = frontend / "src/apis/duiZhao.js"
            api.parent.mkdir(parents=True, exist_ok=True)
            api.write_text(
                "const service = '/yb-yibaogl'\n"
                "export const query = () => request(service + '/yiBaoZd/query')\n",
                encoding="utf-8",
            )
            bff = root / "df-bff-yibaogl"
            bff_controller = bff / "src/main/java/DuiZhaoController.java"
            bff_controller.parent.mkdir(parents=True)
            bff_controller.write_text(
                "@RequestMapping(\"/duiZhao\") class DuiZhaoController {\n"
                " @GetMapping(\"/page\") void page() {}\n}\n",
                encoding="utf-8",
            )
            service = root / "df-mic-yibaogl"
            service_controller = service / "src/main/java/YiBaoZdController.java"
            service_controller.parent.mkdir(parents=True)
            service_controller.write_text(
                "@RequestMapping(\"/yiBaoZd\") class YiBaoZdController {\n"
                " @GetMapping(\"/query\") void query() {}\n}\n",
                encoding="utf-8",
            )

            selected = discover_frontend_projects(
                combined_text="医保审批项目维护和医保对照支持批量上传。",
                root=root,
                selected_projects=[],
            )
            graph = build_service_graph(
                combined_text="优化医保审批项目维护。",
                root=root,
                selected_projects=selected,
                restrict_to_selected_projects=False,
            )

        self.assertEqual("evidence_ready", graph["status"])
        self.assertEqual(
            {"df-bff-yibaogl", "df-mic-yibaogl"},
            {branch["target_project"] for branch in graph["branches"]},
        )

    def test_discards_broad_ancestor_entry_roots_when_a_specific_page_root_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "df-web-yibaogl"
            broad = project / "src/views/yiBaoMlDz/yaoPinMlDz_dlh.vue"
            target = project / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            broad.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            broad.write_text("医保支付标准金额", encoding="utf-8")
            target.write_text("医保审批项目维护", encoding="utf-8")

            roots = frontend_entry_roots(
                project_path=project,
                entry_matches=[
                    {"term": "医保支付标准金额", "path": "src/views/yiBaoMlDz/yaoPinMlDz_dlh.vue"},
                    {"term": "医保审批项目维护", "path": "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"},
                ],
            )

        self.assertEqual([project / "src/views/yiBaoMlDz/yiBaoSpXmWh"], roots)

    def test_collapses_repeated_endpoint_evidence_into_one_service_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            for name in ("index.vue", "components/child.vue"):
                page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh" / name
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text(
                    "医保审批项目维护\nrequest('/yb-yibaogl/yiBaoSpXmWh/page')\n",
                    encoding="utf-8",
                )
            service = root / "df-mic-yibaogl"
            controller = service / "src/main/java/YiBaoSpXmWhController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                "@RequestMapping(\"/yiBaoSpXmWh\") class C {\n"
                " @GetMapping(\"/page\") void page() {}\n}\n",
                encoding="utf-8",
            )
            selected = discover_frontend_projects(
                combined_text="医保审批项目维护。",
                root=root,
                selected_projects=[],
            )
            graph = build_service_graph(
                combined_text="优化医保审批项目维护。",
                root=root,
                selected_projects=selected,
                restrict_to_selected_projects=False,
            )

        self.assertEqual(1, len(graph["branches"]))
        self.assertEqual(2, len(graph["branches"][0]["source_paths"]))

    def test_discovered_filter_summary_describes_the_actual_field(self) -> None:
        result = decide_discovered_stored_filter(
            combined_text="病人列表增加就诊状态筛选。",
            provenance={
                "target_field": "visitState",
                "target_ui_found": True,
                "field_returned": True,
                "query_chain": {"status": "complete"},
                "enum_options": [
                    {"value": "0", "label": "未就诊"},
                    {"value": "1", "label": "已就诊"},
                ],
            },
        )

        self.assertIn("visitState", result["summary"])
        self.assertNotIn("上下午", result["summary"])
        self.assertNotIn("时钟时间", result["summary"])

    def test_marks_multi_capability_requirement_before_guessing_a_random_field(self) -> None:
        text = (
            "医保审批项目维护和医保对照放在一个页面，支持批量上传、批量审核、同步医保等级，"
            "包含医院目录、医保目录查询、可编辑字段和历史记录。"
        )

        self.assertTrue(is_broad_feature_requirement(text))

    def test_uses_domain_context_for_default_projects_not_field_mentions(self) -> None:
        candidates = default_project_candidates(
            "医保审批项目维护和医保对照，字段包括门诊不上传、住院不上传、医保编码。"
        )
        names = {name for name, _role, _score, _reason in candidates}

        self.assertIn("df-web-yibaogl", names)
        self.assertIn("df-bff-yibaogl", names)
        self.assertIn("df-mic-yibaogl", names)
        self.assertNotIn("df-web-zhuyuansf", names)

    def test_service_graph_can_use_a_service_specific_external_api_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "医保审批项目维护\n"
                "request('/yb-yibaogl/yiBaoSpXmWh/page')\n",
                encoding="utf-8",
            )
            service = root / "df-mic-yibaogl"
            controller = service / "src/main/java/YiBaoSpXmWhController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                "@RequestMapping(\"/yiBaoSpXmWh\") class C {\n"
                " @GetMapping(\"/page\") DTO_YiBaoSpXmWh page() {}\n}\n",
                encoding="utf-8",
            )
            external_api = root / "df-mic-yibaogl-api"
            dto = external_api / "src/main/java/DTO_YiBaoSpXmWh.java"
            dto.parent.mkdir(parents=True)
            dto.write_text("class DTO_YiBaoSpXmWh {}\n", encoding="utf-8")

            selected = discover_frontend_projects(
                combined_text="医保审批项目维护。",
                root=root,
                selected_projects=[],
            )
            graph = build_service_graph(
                combined_text="优化医保审批项目维护。",
                root=root,
                selected_projects=selected + [
                    {"name": "df-mic-yibaogl", "path": str(service), "role": "backend", "exists": True}
                ],
                restrict_to_selected_projects=False,
            )

        self.assertIn("df-mic-yibaogl-api", {node["project"] for node in graph["nodes"]})


if __name__ == "__main__":
    unittest.main()
