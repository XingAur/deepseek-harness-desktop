from __future__ import annotations

import tempfile
import unittest
import subprocess
from pathlib import Path

from app.service_architecture import (
    build_right_panel_contract_proposal,
    build_service_architecture_catalog,
    recommend_right_panel_architecture,
)


class ServiceArchitectureTests(unittest.TestCase):
    def test_catalog_reads_all_local_repositories_not_only_selected_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bff = root / "df-bff-jichufw"
            bff.mkdir()
            (bff / "FenLeiTreeService.java").write_text(
                "class FenLeiTreeService { YaoPinZdApi yaoPinZdApi; ShouFeiXmApi shouFeiXmApi; }\n",
                encoding="utf-8",
            )
            (bff / "ShouFeiXmController.java").write_text(
                '@RequestMapping("/shouFeiXm") class ShouFeiXmController {\n'
                ' @PostMapping("/getAndShouFeiXmJgPage")\n'
                ' public ResponseMessage<DTO_PageData<DTO_GY_ShouFeiXm>> page(DTO_GY_ShouFeiXmYiBaoCx req) { return shouFeiXmService.page(req); }\n'
                '}\n',
                encoding="utf-8",
            )
            (bff / "ShouFeiXmServiceImpl.java").write_text(
                "class ShouFeiXmServiceImpl { ShouFeiXmApi shouFeiXmApi; "
                "public Object page(DTO_GY_ShouFeiXmYiBaoCx req) { return shouFeiXmApi.page(); } }\n",
                encoding="utf-8",
            )
            yibaogl = root / "df-mic-yibaogl"
            yibaogl.mkdir()
            (yibaogl / "build.gradle").write_text(
                "implementation project(':mic-gy-jichufw-api')\n",
                encoding="utf-8",
            )
            (root / "df-mic-jichufw").mkdir()
            (root / "df-mic-yaokufang").mkdir()

            catalog = build_service_architecture_catalog(
                root=root,
                selected_projects=[
                    {"name": "df-mic-yibaogl", "path": str(yibaogl), "role": "service", "exists": True}
                ],
            )

        names = {item["project"] for item in catalog["nodes"]}
        self.assertEqual(
            {"df-bff-jichufw", "df-mic-jichufw", "df-mic-yaokufang", "df-mic-yibaogl"},
            names,
        )
        self.assertTrue(any(edge["target_project"] == "df-mic-jichufw" for edge in catalog["edges"]))

    def test_right_panel_chooses_bff_raw_sources_without_user_repeating_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bff = root / "df-bff-jichufw"
            bff.mkdir()
            (bff / "FenLeiTreeService.java").write_text(
                "class FenLeiTreeService { YaoPinZdApi yaoPinZdApi; ShouFeiXmApi shouFeiXmApi; }\n",
                encoding="utf-8",
            )
            (bff / "ShouFeiXmController.java").write_text(
                '@RequestMapping("/shouFeiXm") class ShouFeiXmController {\n'
                ' @PostMapping("/getAndShouFeiXmJgPage")\n'
                ' public ResponseMessage<DTO_PageData<DTO_GY_ShouFeiXm>> page(DTO_GY_ShouFeiXmYiBaoCx req) { return shouFeiXmService.page(req); }\n'
                '}\n',
                encoding="utf-8",
            )
            (bff / "ShouFeiXmServiceImpl.java").write_text(
                "class ShouFeiXmServiceImpl { ShouFeiXmApi shouFeiXmApi; "
                "public Object page(DTO_GY_ShouFeiXmYiBaoCx req) { return shouFeiXmApi.page(); } }\n",
                encoding="utf-8",
            )
            consumer = root / "df-mic-yibaogl"
            consumer.mkdir()
            (consumer / "build.gradle").write_text(
                "implementation project(':mic-gy-jichufw-api')\n",
                encoding="utf-8",
            )
            catalog = build_service_architecture_catalog(
                root=root,
                selected_projects=[
                    {"name": "df-bff-jichufw", "path": str(bff), "role": "bff", "exists": True},
                    {"name": "df-mic-yibaogl", "path": str(consumer), "role": "service", "exists": True},
                ],
            )
            decision = recommend_right_panel_architecture(catalog=catalog)

        # Internal YaoPinZdApi usage proves the category-tree dependency only;
        # without a BFF Controller contract the drug side cannot be marked
        # HTTP-ready or auto-resolved.
        self.assertEqual("needs_api_evidence", decision["status"])
        self.assertEqual("bff_raw_sources_yibaogl_enrichment", decision["recommended_option_id"])
        self.assertIn("/shouFeiXm/getAndShouFeiXmJgPage", decision["evidence"]["existing_charge_routes"])
        contract = decision["evidence"]["existing_charge_contracts"][0]
        self.assertEqual("DTO_GY_ShouFeiXmYiBaoCx", contract["request_types"][0])
        self.assertEqual("req", contract["request_parameters"][0]["name"])
        self.assertEqual("ShouFeiXmApi", contract["upstream_api_calls"][0]["api"])
        self.assertEqual("page", contract["upstream_api_calls"][0]["method"])
        self.assertEqual([], decision["evidence"]["existing_drug_contracts"])
        proposal = decision["contract_proposal"]
        self.assertEqual("review_required", proposal["status"])
        self.assertEqual("new_bff_unified_directory_contract_required", proposal["decision"])
        self.assertIsNone(proposal["route"]["candidate_path"])
        self.assertEqual("missing_bff_http_contract", proposal["source_contracts"]["drug"]["status"])
        rejected = next(item for item in decision["options"] if item["id"] == "direct_cross_schema_or_drug_service")
        self.assertEqual("rejected", rejected["status"])

    def test_contract_proposal_never_invents_a_route_without_drug_http_evidence(self) -> None:
        proposal = build_right_panel_contract_proposal(
            evidence={
                "charge_api_proven": True,
                "drug_api_proven": True,
                "existing_charge_contracts": [
                    {
                        "route": "/shouFeiXm/getAndShouFeiXmJgPage",
                        "http_method": "POST",
                        "response_types": ["DTO_GY_ShouFeiXm"],
                        "request_parameters": [],
                        "upstream_api_calls": [{"api": "ShouFeiXmApi", "method": "getAndShouFeiXmJgPage"}],
                    }
                ],
                "existing_drug_contracts": [],
                "consumer_contracts": [
                    {
                        "route": "/YiBaoSpXmWh/getYiYuanMuLuPage",
                        "http_method": "POST",
                        "request_parameters": [{"type": "String", "name": "queryString"}],
                        "response_types": ["DTO_YB_YiYuanMuLuXx"],
                    }
                ],
                "contract_gap": ["missing_drug_http_route"],
            }
        )

        self.assertFalse(proposal["write_ready"])
        self.assertIsNone(proposal["route"]["candidate_path"])
        self.assertIn("missing_drug_http_route", proposal["blocking_reasons"])

    def test_catalog_collects_api_definitions_and_usage_without_user_supplied_service_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            drug = root / "df-mic-yaokufang"
            drug.mkdir()
            (drug / "YaoPinZdApi.java").write_text(
                "@FeignClient(name = \"ykf-jichuyw\")\n"
                "public interface YaoPinZdApi {\n"
                "  ResponseMessage<DTO_YaoPin> page(DTO_YaoPinCx req);\n"
                "}\n",
                encoding="utf-8",
            )
            bff = root / "df-bff-jichufw"
            bff.mkdir()
            (bff / "FenLeiTreeService.java").write_text(
                "class FenLeiTreeService {\n"
                "  YaoPinZdApi yaoPinZdApi;\n"
                "  Object list(DTO_Query req) { return yaoPinZdApi.page(req); }\n"
                "}\n",
                encoding="utf-8",
            )
            consumer = root / "df-mic-yibaogl"
            consumer.mkdir()
            (consumer / "YiYuanMuLuService.java").write_text(
                "class YiYuanMuLuService {\n"
                "  DTO_YB_YiYuanMuLuXx save(String guojiaxmdm, String menzhenbz) {\n"
                "    return repository.save(guojiaxmdm + menzhenbz);\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            catalog = build_service_architecture_catalog(
                root=root,
                selected_projects=[
                    {"name": "df-bff-jichufw", "path": str(bff), "role": "bff", "exists": True},
                    {"name": "df-mic-yaokufang", "path": str(drug), "role": "service", "exists": True},
                    {"name": "df-mic-yibaogl", "path": str(consumer), "role": "service", "exists": True},
                ],
            )
            decision = recommend_right_panel_architecture(catalog=catalog)

        evidence = decision["evidence"]
        self.assertTrue(evidence["drug_api_definitions"])
        self.assertEqual("ykf-jichuyw", evidence["drug_api_definitions"][0]["client_name"])
        self.assertTrue(evidence["bff_drug_api_usage"])
        proposal = decision["contract_proposal"]
        statuses = {item["id"]: item["status"] for item in proposal["auto_collected_evidence"]}
        self.assertEqual("evidence_collected", statuses["drug_api_definition"])
        self.assertEqual("evidence_collected", statuses["bff_drug_api_usage"])
        self.assertEqual("not_proven", statuses["drug_bff_http_exposure"])
        self.assertIn("BFF 对药品 API 的 HTTP 暴露", proposal["remaining_evidence_before_worktree"])

    def test_frontend_gateway_route_is_recorded_but_not_mistaken_for_bff_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            web = root / "df-web-yibaogl"
            web.mkdir()
            (web / "api.js").write_text(
                "export const page = data => request({\n"
                "  url: '/ykf-jichuyw/yaoPinZd/getAllYaoPinMcCdForYiBaoByConditions',\n"
                "  method: 'post', data\n"
                "})\n",
                encoding="utf-8",
            )
            bff = root / "df-bff-jichufw"
            bff.mkdir()
            consumer = root / "df-mic-yibaogl"
            consumer.mkdir()
            catalog = build_service_architecture_catalog(
                root=root,
                selected_projects=[
                    {"name": "df-web-yibaogl", "path": str(web), "role": "frontend", "exists": True},
                    {"name": "df-bff-jichufw", "path": str(bff), "role": "bff", "exists": True},
                    {"name": "df-mic-yibaogl", "path": str(consumer), "role": "service", "exists": True},
                ],
            )
            decision = recommend_right_panel_architecture(catalog=catalog)

        routes = decision["evidence"]["frontend_drug_routes"]
        self.assertEqual("/ykf-jichuyw/yaoPinZd/getAllYaoPinMcCdForYiBaoByConditions", routes[0]["endpoint"])
        proposal = decision["contract_proposal"]
        statuses = {item["id"]: item["status"] for item in proposal["auto_collected_evidence"]}
        self.assertEqual("evidence_collected", statuses["frontend_drug_gateway_route"])
        self.assertFalse(proposal["write_ready"])
        self.assertIn("frontend_drug_route_ownership_unresolved", proposal["blocking_reasons"])

    def test_dirty_consumer_implementation_requires_architecture_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bff = root / "df-bff-jichufw"
            bff.mkdir()
            (bff / "FenLeiTreeService.java").write_text(
                "class FenLeiTreeService { YaoPinZdApi yaoPinZdApi; ShouFeiXmApi shouFeiXmApi; }\n",
                encoding="utf-8",
            )
            (bff / "ShouFeiXmController.java").write_text(
                '@RequestMapping("/shouFeiXm") class ShouFeiXmController {\n'
                ' @PostMapping("/getAndShouFeiXmJgPage") Object page() { return null; }\n'
                '}\n',
                encoding="utf-8",
            )
            consumer = root / "df-mic-yibaogl"
            (consumer / "src/main/java/yibaodz/service").mkdir(parents=True)
            (consumer / "src/main/java/yibaodz/service/YiBaoSpXmWhServiceImpl.java").write_text(
                "class YiBaoSpXmWhServiceImpl { Object getYiYuanMuLuPage() { return null; } }\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q"], cwd=consumer, check=True)
            catalog = build_service_architecture_catalog(
                root=root,
                selected_projects=[
                    {"name": "df-bff-jichufw", "path": str(bff), "role": "bff", "exists": True},
                    {"name": "df-mic-yibaogl", "path": str(consumer), "role": "service", "exists": True},
                ],
            )
            decision = recommend_right_panel_architecture(catalog=catalog)

        self.assertEqual("needs_reconciliation", decision["status"])
        self.assertTrue(decision["evidence"]["existing_consumer_dirty_implementation"])
        self.assertIn(
            "existing_consumer_dirty_implementation_requires_reconciliation",
            decision["evidence"]["contract_gap"],
        )

    def test_git_project_static_evidence_is_cached_and_invalidated_after_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "df-mic-yibaogl"
            project.mkdir()
            source = project / "YiYuanMuLuApi.java"
            source.write_text(
                "public interface YiYuanMuLuApi { Object page(); }\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            selected = [{"name": project.name, "path": str(project), "role": "service", "exists": True}]

            first = build_service_architecture_catalog(root=root, selected_projects=selected)
            second = build_service_architecture_catalog(root=root, selected_projects=selected)
            self.assertEqual(0, first["performance"]["cache_hits"])
            self.assertEqual(1, second["performance"]["cache_hits"])

            source.write_text(
                "public interface YiYuanMuLuApi { Object page(String query); }\n",
                encoding="utf-8",
            )
            changed = build_service_architecture_catalog(root=root, selected_projects=selected)
            self.assertEqual(0, changed["performance"]["cache_hits"])
            self.assertEqual(1, changed["performance"]["cache_misses"])


if __name__ == "__main__":
    unittest.main()
