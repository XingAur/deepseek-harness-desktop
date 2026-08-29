from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.harness import RequirementWorkflowRunner, artifact_output_name
from app.requirement_calibration import build_requirement_calibration
from app.technical_decision import (
    TechnicalDecisionResult,
    build_combined_text,
    build_contract_verification,
    build_field_identity_consistency,
    build_technical_decision,
    decide_multi_service_feature,
    find_controller_paths_for_endpoint,
    find_multi_source_right_panel_findings,
    find_public_api_contract_paths,
    build_service_graph,
    decide_behavior_change,
    is_broad_feature_requirement,
    is_behavior_change_requirement,
    merge_service_graph_projects,
    prune_unrelated_candidate_projects,
    requires_service_contract,
    service_graph_to_markdown,
)


class TechnicalDecisionTests(unittest.TestCase):

    def test_refund_error_is_behavior_change_not_display_field(self) -> None:
        text = (
            "医保结算单据只有一个药品，退药后点击退费按钮报患者在院不能进行医保登记；"
            "全退时不再调用门诊医保预结算，直接进行医保退费。"
        )
        self.assertTrue(is_behavior_change_requirement(text))

        decision = decide_behavior_change(
            combined_text=text,
            provenance={
                "field_kind": "behavior_change",
                "target_ui_paths": ["src/pages/yeWuGn/menZhenTf/components/costTable.vue"],
                "target_ui_found": True,
                "service_graph": {
                    "status": "evidence_ready",
                    "branches": [
                        {
                            "source_project": "df-web-guahaosf",
                            "source_path": "df-web-guahaosf:src/apis/shouFei.js",
                            "endpoint": "/winbff-guahaosf/shouFei/menZhenTfYjs",
                            "target_project": "df-bff-guahaosf",
                            "target_path": "df-bff-guahaosf:ShouFeiController.java",
                            "entry_paths": [
                                "df-web-guahaosf:src/pages/yeWuGn/menZhenTf/components/costTable.vue"
                            ],
                            "controller_verified": True,
                        }
                    ],
                    "unresolved_endpoints": [],
                },
                "authoritative_code_locators": ["menZhenTfYjs", "menZhenTf"],
            },
            selected_projects=[{"name": "df-web-guahaosf", "role": "frontend", "exists": True}],
        )

        self.assertEqual("blocked_behavior_change_contract", decision["change_type"])
        self.assertFalse(decision["can_patch"])
        self.assertNotIn("目标展示字段", "；".join(decision["blockers"]))
        self.assertEqual(
            "单药品，或全部费用已申请退费且没有未执行费用。",
            decision["behavior_contract"]["full_refund_condition"],
        )

    def test_explicit_display_field_targets_named_component_without_cross_layer_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            card = frontend / "src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue"
            card.parent.mkdir(parents=True)
            card.write_text(
                "<template><div>{{ data.yiShengMc }} {{ data.guaHaoLbMc }}</div></template>\n",
                encoding="utf-8",
            )
            page = frontend / "src/pages/yeWuGn/guaHaoSf/index.vue"
            page.write_text(
                "import { getPaiBanListByJiaGeTxV2 } from '@/apis/jj-guahao/paiban'\n"
                "getPaiBanListByJiaGeTxV2().then(rows => { this.rows = rows; const room = rows[0].zhenShiMc })\n",
                encoding="utf-8",
            )
            api = frontend / "src/apis/jj-guahao/paiban.js"
            api.parent.mkdir(parents=True)
            api.write_text(
                "request.post('/jj-guahao/paiBan/getPaiBanListByJiaGeTx')\n"
                "request.post('/jj-guahao/paiBan/getPaiBanListByJiaGeTxV2')\n"
                "request.post('/jj-guahao/paiBan/getUnrelatedList')\n",
                encoding="utf-8",
            )
            service = root / "df-mic-jj-menzhen"
            controller = service / "mic-jj-guahao/src/main/java/GuaHaoPbController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/paiBan") class GuaHaoPbController {\n'
                ' @PostMapping("/getPaiBanListByJiaGeTx")\n'
                ' public ResponseMessage<List<DTO_MZ_GuaHaoPb>> legacy() {}\n'
                ' @PostMapping("/getPaiBanListByJiaGeTxV2")\n'
                ' public ResponseMessage<List<DTO_MZ_GuaHaoPb>> page() {}\n}\n',
                encoding="utf-8",
            )
            unrelated_controller = service / "mic-jj-guahao/src/main/java/UnrelatedController.java"
            unrelated_controller.write_text(
                '@RequestMapping("/paiBan") class UnrelatedController {\n'
                ' @PostMapping("/getUnrelatedList")\n'
                ' public ResponseMessage<List<DTO_Other>> page() {}\n}\n',
                encoding="utf-8",
            )
            dto = service / "mic-jj-guahao-api/src/main/java/DTO_MZ_GuaHaoPb.java"
            dto.parent.mkdir(parents=True)
            dto.write_text("private String zhenShiMc;\n", encoding="utf-8")
            unrelated_dto = service / "mic-jj-guahao-api/src/main/java/DTO_MZ_PaiBanSz.java"
            unrelated_dto.write_text("private String zhenShiMc;\n", encoding="utf-8")
            entity = service / "mic-jj-guahao/src/main/java/MZ_GuaHaoPb.java"
            entity.parent.mkdir(parents=True, exist_ok=True)
            entity.write_text("private String zhenShiMc;\n", encoding="utf-8")

            result = build_technical_decision(
                demand_text=(
                    "每个挂号医生后面加上诊室。诊室来源必须是当前排班的 zhenShiMc；"
                    "目标组件是 src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue。"
                    "页面实际调用 /jj-guahao/paiBan/getPaiBanListByJiaGeTxV2。"
                    "只读证据：接口 DTO 已返回该字段。"
                    "候选改动只应落在前端；后端、BFF、公共 API 和数据库均不应修改。"
                ),
                project_root=root,
                explicit_project_paths=[str(frontend), str(service)],
            )

        self.assertEqual("zhenShiMc", result.field_provenance["target_field"])
        self.assertEqual("explicit_display_field", result.field_provenance["field_kind"])
        self.assertTrue(result.field_provenance["field_returned"])
        self.assertTrue(result.field_provenance["target_ui_found"])
        self.assertEqual(
            ["src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue"],
            result.field_provenance["target_ui_paths"],
        )
        self.assertEqual(
            ["src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue"],
            result.recommended_allowed_paths,
        )
        self.assertEqual("frontend_display_only", result.implementation_decision["change_type"])
        self.assertTrue(result.implementation_decision["can_patch"])
        self.assertTrue(result.contract_verification["required"])
        self.assertEqual("verified", result.contract_verification["status"])
        self.assertEqual("verified", result.contract_verification["layers"]["response_field"]["status"])
        self.assertEqual("evidence_ready", result.field_provenance["service_graph"]["status"])
        branch = result.field_provenance["service_graph"]["branches"][0]
        self.assertEqual(1, len(result.field_provenance["service_graph"]["branches"]))
        self.assertEqual("/jj-guahao/paiBan/getPaiBanListByJiaGeTxV2", branch["endpoint"])
        self.assertEqual("df-mic-jj-menzhen", branch["target_project"])
        self.assertEqual("verified", branch["field_contract"]["status"])
        self.assertEqual(
            ["df-mic-jj-menzhen:mic-jj-guahao-api/src/main/java/DTO_MZ_GuaHaoPb.java"],
            branch["field_contract"]["evidence_paths"],
        )
        self.assertIn("raw_discovery_target_field", result.field_provenance)
        self.assertEqual("zhenShiMc", result.field_provenance["discovery_target_field"])
        self.assertEqual("verified", result.field_provenance["field_identity_consistency"]["status"])
        self.assertNotIn("DTO_MZ_PaiBanSz.java", "\n".join(result.field_provenance["field_source_paths"]))
        self.assertNotIn("nodes", result.field_provenance["service_graph"]["architecture_catalog"])
        self.assertLess(len(result.to_json().encode("utf-8")), 120_000)
        self.assertEqual(
            {"df-web-guahaosf", "df-mic-jj-menzhen"},
            {item["name"] for item in result.selected_projects},
        )
        backend = next(item for item in result.selected_projects if item["name"] == "df-mic-jj-menzhen")
        self.assertEqual("contract_check", backend["selection_scope"])
        self.assertEqual("requirement_targeted", result.verification_plan["active_profile"])
        self.assertEqual(
            "separate_release_gate",
            result.verification_plan["harness_release_regression"]["status"],
        )
        self.assertNotIn("unittest discover", "\n".join(result.verification_plan["commands"]))

    def test_endpoint_contract_reconciles_competing_generic_discovery_field(self) -> None:
        verified = build_field_identity_consistency(
            target_field="zhenShiMc",
            raw_discovery_target_field="paiBanId",
            endpoint_field_verified=True,
        )
        blocked = build_field_identity_consistency(
            target_field="zhenShiMc",
            raw_discovery_target_field="paiBanId",
            endpoint_field_verified=False,
        )

        self.assertEqual("verified", verified["status"])
        self.assertEqual("zhenShiMc", verified["normalized_discovery_target_field"])
        self.assertEqual("paiBanId", verified["discarded_discovery_target_field"])
        self.assertEqual("conflict", blocked["status"])

    def test_explicit_field_prunes_unrelated_auto_selected_candidates(self) -> None:
        selected = [
            {"name": "df-web-guahaosf", "selection_scope": "change_required"},
            {"name": "df-bff-guahaosf", "selection_scope": "candidate_only"},
            {"name": "df-mic-jj-menzhen", "selection_scope": "contract_check"},
            {"name": "df-his-api", "selection_scope": "contract_check"},
        ]
        graph = {
            "nodes": [
                {"project": "df-web-guahaosf"},
                {"project": "df-mic-jj-menzhen"},
                {"project": "df-his-api"},
            ]
        }

        pruned = prune_unrelated_candidate_projects(
            selected_projects=selected,
            service_graph=graph,
            combined_text="诊室显示字段 zhenShiMc",
        )

        self.assertEqual(
            {"df-web-guahaosf", "df-mic-jj-menzhen", "df-his-api"},
            {item["name"] for item in pruned},
        )

    def test_default_value_source_evidence_never_uses_a_sibling_page_with_the_same_field(self) -> None:
        demand = (
            "不收费需要支持默认值：如果通用表单设置了默认值，优先使用通用表单设置；"
            "没有通用表单设置时，如果参数设置了默认值，使用参数默认值；"
            "前两者都没有且界面写死了默认值时，使用页面硬编码默认值；"
            "以上都没有时代表没有默认值。"
        )
        calibration = build_requirement_calibration(title="DFHIS-32106", demand_text=demand)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            target = frontend / "src/pages/guaHao/index.vue"
            sibling = frontend / "src/pages/paiBan/index.vue"
            target.parent.mkdir(parents=True)
            sibling.parent.mkdir(parents=True)
            target.write_text(
                """export default {
  methods: {
    resolve () {
      const parameter = this.getCanShu('buShouFeiBz')
      const pageHardcodedDefault = 0
      return undefined
    }
  }
}
""",
                encoding="utf-8",
            )
            sibling.write_text(
                "this.tongYongBiaoDan.getDefault('buShouFeiBz')\n",
                encoding="utf-8",
            )
            result = build_technical_decision(
                demand_text=demand,
                project_root=root,
                explicit_project_paths=[str(frontend)],
                explicit_allowed_paths=["src/pages/guaHao/index.vue"],
                default_value_precedence=calibration["default_value_precedence"],
            )

        precedence = result.field_provenance["default_value_precedence"]
        common = next(item for item in precedence["sources"] if item["source"] == "common_form_setting")
        self.assertEqual("missing", common["status"])
        self.assertNotIn("src/pages/paiBan/index.vue", "\n".join(precedence["source_scope_paths"]))
        self.assertFalse(result.implementation_decision["can_patch"])

    def test_default_value_precedence_requires_four_source_code_evidence_before_patch(self) -> None:
        demand = (
            "不收费需要支持默认值：如果通用表单设置了默认值，优先使用通用表单设置；"
            "没有通用表单设置时，如果参数设置了默认值，使用参数默认值；"
            "前两者都没有且界面写死了默认值时，使用界面写死的默认值；"
            "以上都没有时代表没有默认值。"
        )
        calibration = build_requirement_calibration(
            title="DFHIS-32106 挂号收费不收费默认值",
            demand_text=demand,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            page = frontend / "src/views/guaHaoDengJi.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """export default {
  methods: {
    resolveBuShouFeiBz () {
      const common = this.tongYongBiaoDan.getDefault('buShouFeiBz')
      if (common !== undefined) return common
      const parameter = this.getCanShu('挂号_不收费默认值')
      if (parameter !== undefined) return parameter
      const pageHardcodedDefault = 0
      if (pageHardcodedDefault !== undefined) return pageHardcodedDefault
      return undefined
    }
  }
}
""",
                encoding="utf-8",
            )
            result = build_technical_decision(
                demand_text=demand,
                project_root=root,
                explicit_project_paths=[str(frontend)],
                default_value_precedence=calibration["default_value_precedence"],
            )

        precedence = result.field_provenance["default_value_precedence"]
        self.assertEqual("verified", precedence["status"])
        self.assertEqual(
            ["common_form_setting", "parameter_setting", "page_hardcoded_default", "no_default"],
            [item["source"] for item in precedence["sources"]],
        )
        self.assertTrue(result.implementation_decision["can_patch"])
        self.assertIn("默认值来源源码取证", result.to_markdown())

    def test_explicit_frontend_scope_still_reads_local_sibling_backend_as_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "request('/yb-yibaogl/YiBaoSpXmWh/getYiYuanMuLuPage')\n",
                encoding="utf-8",
            )
            service = root / "df-mic-yibaogl"
            controller = service / "src/main/java/YiBaoSpXmWhController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/YiBaoSpXmWh") class YiBaoSpXmWhController {\n'
                ' @PostMapping("/getYiYuanMuLuPage") void page() {}\n}\n',
                encoding="utf-8",
            )
            selected = [
                {
                    "name": "df-web-yibaogl",
                    "path": str(frontend),
                    "role": "frontend",
                    "exists": True,
                    "entry_matches": [{"term": "医保审批项目维护", "path": "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"}],
                },
            ]

            graph = build_service_graph(
                combined_text="医保审批项目维护，接口请求需要核验。",
                root=root,
                selected_projects=selected,
                restrict_to_selected_projects=True,
            )

        self.assertEqual([], graph["unresolved_endpoints"])
        self.assertEqual("contract_check", graph["branches"][0]["scope"])
        self.assertEqual("df-mic-yibaogl", graph["branches"][0]["target_project"])

    def test_medical_approval_projection_rules_force_multi_service_routing(self) -> None:
        demand = (
            "医院目录来源必须是 df-mic-jichufw 的 gy_shoufeixm，关联医保对照表和字典表，"
            "多条对照按多行展示；YB_XIANGMUZDY 两条逻辑记录前端聚合为一个项目；"
            "门诊自费、门诊部上传先 menzhenbz=1 再判断 zifeibz/bushangchuanbz，"
            "住院先 zhuyuanbz=1；先确认前端、BFF、业务微服务、底层服务边界。"
        )

        self.assertTrue(is_broad_feature_requirement(demand))

    def test_auto_resolved_architecture_is_not_repeated_as_a_blocker(self) -> None:
        decision = decide_multi_service_feature(
            combined_text="医院目录包含药品和收费项目。",
            provenance={
                "service_graph": {
                    "status": "evidence_ready",
                    "branches": [],
                    "unresolved_endpoints": [],
                    "boundary_findings": [
                        {
                            "type": "multi_source_right_panel_boundary",
                            "status": "conflict",
                            "architecture_decision": "auto_resolved",
                            "recommended_option_id": "bff_raw_sources_yibaogl_enrichment",
                            "architecture_options": [],
                            "message": "BFF 和医保服务边界已由源码证据确定。",
                        }
                    ],
                    "business_rule_findings": [],
                }
            },
        )

        self.assertEqual("ready_for_contract", decision["change_plan"]["status"])
        self.assertEqual("auto_resolved", decision["change_plan"]["architecture_decision"])
        self.assertEqual(
            "bff_raw_sources_yibaogl_enrichment",
            decision["change_plan"]["architecture_requirements"][0]["id"],
        )
        self.assertEqual(
            "not_proven",
            decision["change_plan"]["architecture_requirements"][0]["endpoint_contract_status"],
        )
        self.assertNotIn("数据来源边界未闭合", "\n".join(decision["blockers"]))

    def test_service_graph_scope_is_persisted_on_selected_projects(self) -> None:
        selected = [
            {
                "name": "df-web-yibaogl",
                "path": "/tmp/df-web-yibaogl",
                "role": "frontend",
                "score": 100,
                "reasons": ["默认候选"],
            },
            {
                "name": "df-bff-yibaogl",
                "path": "/tmp/df-bff-yibaogl",
                "role": "backend",
                "score": 90,
                "reasons": ["默认候选"],
            },
        ]
        merged = merge_service_graph_projects(
            selected_projects=selected,
            service_graph={
                "nodes": [
                    {"project": "df-web-yibaogl", "path": "/tmp/df-web-yibaogl", "role": "frontend", "scope": "change_required"},
                    {"project": "df-bff-yibaogl", "path": "/tmp/df-bff-yibaogl", "role": "backend", "scope": "existing_dependency"},
                ]
            },
        )
        by_name = {item["name"]: item for item in merged}
        self.assertEqual("change_required", by_name["df-web-yibaogl"]["selection_scope"])
        self.assertEqual("existing_dependency", by_name["df-bff-yibaogl"]["selection_scope"])

    def test_service_graph_closes_client_and_server_contract_evidence(self) -> None:
        contract = build_contract_verification(
            combined_text="医保审批页面需要跨服务接口和后端字段。",
            selected_projects=[
                {"name": "df-web-yibaogl", "role": "frontend", "exists": True},
                {"name": "df-mic-yibaogl", "role": "backend", "exists": True},
            ],
            allowed_paths=[],
            service_graph={
                "status": "evidence_ready",
                "branches": [
                    {
                        "source_project": "df-web-yibaogl",
                        "source_paths": [
                            "df-web-yibaogl:src/views/yiBaoMlDz/yiBaoSpXmWh/api/yiBaoSpXmApi.js"
                        ],
                        "endpoint": "/yb-yibaogl/YiBaoSpXmWh/getYiYuanMuLuPage",
                        "target_project": "df-mic-yibaogl",
                        "target_path": (
                            "df-mic-yibaogl:mic-yb-yibaogl/src/main/java/"
                            "YiBaoSpXmWhController.java"
                        ),
                        "controller_verified": True,
                    }
                ],
                "unresolved_endpoints": [],
            },
        )

        self.assertEqual("verified", contract["status"])
        self.assertEqual("verified", contract["layers"]["client_request"]["status"])
        self.assertEqual("verified", contract["layers"]["server_contract"]["status"])
        self.assertEqual("service_graph", contract["evidence_mode"])
        self.assertIn("/yb-yibaogl/YiBaoSpXmWh/getYiYuanMuLuPage", contract["contract_terms"])

    def test_service_graph_surfaces_direct_base_table_access_as_boundary_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "医保审批项目维护 request('/yb-yibaogl/YiBaoSpXmWh/getYiYuanMuLuPage')\n",
                encoding="utf-8",
            )
            service = root / "df-mic-yibaogl"
            controller = service / "src/main/java/YiBaoSpXmWhController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/YiBaoSpXmWh") class YiBaoSpXmWhController {\n'
                ' @GetMapping("/getYiYuanMuLuPage") void page() {}\n}\n',
                encoding="utf-8",
            )
            direct_sql = service / "src/main/java/ZhenLiaoMuLuSql.java"
            direct_sql.write_text(
                "select * from df_zhushuju.gy_shoufeixm where id = :id\n",
                encoding="utf-8",
            )
            base = root / "df-mic-jichufw"
            api = base / "src/main/java/ShouFeiXmApi.java"
            api.parent.mkdir(parents=True)
            api.write_text("interface ShouFeiXmApi { Object page(); }\n", encoding="utf-8")

            result = build_technical_decision(
                demand_text=(
                    "医院目录来源必须是 df-mic-jichufw 的 gy_shoufeixm，"
                    "先确认前端、BFF、业务微服务、底层服务的真实调用边界。"
                ),
                project_root=root,
                explicit_project_paths=[str(frontend), str(service), str(base)],
            )

        graph = result.field_provenance["service_graph"]
        findings = graph.get("boundary_findings") or []
        self.assertTrue(any(item.get("type") == "direct_cross_schema_access" for item in findings))
        self.assertIn("数据来源边界", "\n".join(result.implementation_decision["blockers"]))

    def test_service_graph_surfaces_non_strict_approval_flag_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "医保审批项目维护 request('/yb-yibaogl/YiBaoSpXmWh/getYiYuanMuLuPage')\n",
                encoding="utf-8",
            )
            service = root / "df-mic-yibaogl"
            controller = service / "src/main/java/YiBaoSpXmWhController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/YiBaoSpXmWh") class YiBaoSpXmWhController {\n'
                ' @GetMapping("/getYiYuanMuLuPage") void page() {}\n}\n',
                encoding="utf-8",
            )
            logic = service / "src/main/java/YiBaoSpXmWhServiceImpl.java"
            logic.write_text(
                "if (!Objects.equals(o.getMenZhenBz(), 0)) { row.setMenZhenZiFei(\"1\"); }\n"
                "if (!Objects.equals(o.getZhuYuanBz(), 0)) { row.setZhuYuanZiFei(\"1\"); }\n",
                encoding="utf-8",
            )
            base = root / "df-mic-jichufw"
            api = base / "src/main/java/ShouFeiXmApi.java"
            api.parent.mkdir(parents=True)
            api.write_text("interface ShouFeiXmApi { Object page(); }\n", encoding="utf-8")

            result = build_technical_decision(
                demand_text=(
                    "医院目录来源必须是 df-mic-jichufw 的 gy_shoufeixm，"
                    "YB_XIANGMUZDY 关联多条记录；门诊严格 menzhenbz=1 后再判断 zifeibz/bushangchuanbz，"
                    "住院严格 zhuyuanbz=1；先确认前端、BFF、业务微服务、底层服务边界。"
                ),
                project_root=root,
                explicit_project_paths=[str(frontend), str(service), str(base)],
            )

        graph = result.field_provenance["service_graph"]
        findings = graph.get("business_rule_findings") or []
        self.assertEqual(2, len(findings))
        self.assertTrue(all(item.get("status") == "conflict" for item in findings))
        self.assertIn("审批属性规则冲突", "\n".join(result.implementation_decision["blockers"]))

    def test_service_graph_scans_non_strict_flags_from_chinese_business_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text("医保审批项目维护 request('/yb-yibaogl/YiBaoSpXmWh/getYiYuanMuLuPage')\n", encoding="utf-8")
            service = root / "df-mic-yibaogl"
            controller = service / "src/main/java/YiBaoSpXmWhController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/YiBaoSpXmWh") class YiBaoSpXmWhController {\n'
                ' @GetMapping("/getYiYuanMuLuPage") void page() {}\n}\n',
                encoding="utf-8",
            )
            logic = service / "src/main/java/YiBaoSpXmWhServiceImpl.java"
            logic.write_text(
                "if (!Objects.equals(o.getMenZhenBz(), 0)) { row.setMenZhenZiFei(\"1\"); }\n"
                "if (!Objects.equals(o.getZhuYuanBz(), 0)) { row.setZhuYuanZiFei(\"1\"); }\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="门诊自费和门诊不上传必须先判断门诊标志；住院自费和住院不上传必须先判断住院标志。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(service)],
            )

        findings = result.field_provenance["service_graph"].get("business_rule_findings") or []
        self.assertEqual(2, len(findings))
        self.assertTrue(all(item.get("type") == "non_strict_approval_flag_check" for item in findings))

    def test_service_graph_surfaces_drug_and_charge_right_panel_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            yibaogl = root / "df-mic-yibaogl"
            yibaogl_file = yibaogl / "src/main/java/YiBaoSpXmWhServiceImpl.java"
            yibaogl_file.parent.mkdir(parents=True)
            yibaogl_file.write_text(
                "getYiYuanMuLuPage(); getYaoPinMuLu(); V_YB_YaoPinDmDzXx; ZhenLiaoMuLuSql; gy_shoufeixm;\n",
                encoding="utf-8",
            )
            bff = root / "df-bff-jichufw"
            bff_file = bff / "src/main/java/FenLeiTreeService.java"
            bff_file.parent.mkdir(parents=True)
            bff_file.write_text(
                "class FenLeiTreeService { YaoPinZdApi yaoPinZdApi; ShouFeiXmApi shouFeiXmApi; }\n",
                encoding="utf-8",
            )
            selected = [
                {"name": "df-mic-yibaogl", "path": str(yibaogl), "exists": True},
                {"name": "df-bff-jichufw", "path": str(bff), "exists": True},
                {"name": "df-mic-jichufw", "path": str(root / "df-mic-jichufw"), "exists": True},
                {"name": "df-mic-yaokufang", "path": str(root / "df-mic-yaokufang"), "exists": True},
            ]

            findings = find_multi_source_right_panel_findings(
                combined_text="右侧医院目录包含药品、卫材和收费项目。",
                root=root,
                selected_projects=selected,
            )

        self.assertEqual(1, len(findings))
        self.assertEqual("multi_source_right_panel_boundary", findings[0]["type"])
        self.assertEqual("conflict", findings[0]["status"])
        self.assertIn("分类树", findings[0]["message"])
        # Category-tree API usage is not an HTTP drug-directory contract.
        self.assertEqual("needs_api_evidence", findings[0]["architecture_decision"])
        self.assertEqual(
            "bff_raw_sources_yibaogl_enrichment",
            findings[0]["recommended_option_id"],
        )
        self.assertIn("existing_charge_contracts", findings[0]["architecture_evidence"])
        self.assertEqual([], findings[0]["architecture_evidence"]["existing_drug_contracts"])
        proposal = findings[0]["contract_proposal"]
        self.assertEqual("review_required", proposal["status"])
        self.assertIsNone(proposal["route"]["candidate_path"])

    def test_multi_service_decision_emits_exact_candidate_change_targets(self) -> None:
        decision = decide_multi_service_feature(
            combined_text="优化医保审批项目维护，支持批量上传和批量审核。",
            provenance={
                "service_graph": {
                    "status": "evidence_ready",
                    "branches": [
                        {
                            "source_project": "df-web-yibaogl",
                            "source_path": "df-web-yibaogl:src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue",
                            "endpoint": "/yb-yibaogl/YiBaoSpXmWh/batchUpload",
                            "target_project": "df-mic-yibaogl",
                            "target_path": "df-mic-yibaogl:mic-yb-yibaogl/src/main/java/YiBaoSpXmWhController.java",
                            "scope": "candidate_change",
                            "controller_verified": True,
                        },
                        {
                            "source_project": "df-web-yibaogl",
                            "source_path": "df-web-yibaogl:src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue",
                            "endpoint": "/winbff-jichufw/shouFeiXm/getFenLeiTree",
                            "target_project": "df-bff-jichufw",
                            "target_path": "df-bff-jichufw:src/main/java/ShouFeiXmController.java",
                            "scope": "existing_dependency",
                            "controller_verified": True,
                        },
                    ],
                    "unresolved_endpoints": [],
                }
            },
        )

        targets = decision["candidate_change_targets"]
        self.assertEqual(1, len(targets))
        self.assertEqual("/yb-yibaogl/YiBaoSpXmWh/batchUpload", targets[0]["endpoint"])
        self.assertEqual("df-web-yibaogl", targets[0]["source_project"])
        self.assertEqual("df-mic-yibaogl", targets[0]["target_project"])
        self.assertEqual("ready_for_contract", decision["change_plan"]["status"])
        self.assertFalse(decision["can_patch"])

    def test_normalized_requirement_evidence_is_used_for_project_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            page = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text("医保审批项目维护 医保对照 批量上传\n", encoding="utf-8")

            result = build_technical_decision(
                demand_text="",
                requirement_evidence={
                    "title": "优化医保审批项目维护功能",
                    "description_text": "医保审批维护和医保对照做在一个页面，支持批量上传。",
                    "comments": [],
                },
                project_root=root,
            )

        self.assertEqual("df-web-yibaogl", result.selected_projects[0]["name"])
        self.assertEqual("multi_service_feature", result.field_provenance["field_kind"])

    def test_broad_feature_evidence_is_taken_from_service_graph_not_generic_page_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            target = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"
            unrelated = frontend / "src/views/other/index.vue"
            target.parent.mkdir(parents=True)
            unrelated.parent.mkdir(parents=True)
            target.write_text(
                "医保审批项目维护 医保对照 批量上传\n"
                "request('/winbff-yibaogl/duiZhao/page')\n",
                encoding="utf-8",
            )
            unrelated.write_text("医保审批项目维护的无关说明\n", encoding="utf-8")
            bff = root / "df-bff-yibaogl"
            controller = bff / "src/main/java/DuiZhaoController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/duiZhao") class DuiZhaoController {\n'
                ' @GetMapping("/page") void page() {}\n}\n',
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="医保审批项目维护和医保对照支持批量上传、批量审核、同步医保等级。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(bff)],
            )

        evidence_paths = [str(item.get("path") or "") for item in result.field_provenance["evidence"]]
        self.assertTrue(any(path.endswith("yiBaoSpXmWh/index.vue") for path in evidence_paths))
        self.assertFalse(any("src/views/other/" in path for path in evidence_paths))

    def test_complex_baoyun_contract_requirement_is_not_reduced_to_discovered_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-zhuyuansf" / "src/pages/chuYuanYw/jieSuan/dialog"
            frontend.mkdir(parents=True)
            (frontend / "jieSuan.vue").write_text(
                "<df-input v-model=\"query.chuangJianSj\" label=\"合同外自费状态\" />\n"
                "getSettlementPage(query)\n",
                encoding="utf-8",
            )
            api = root / "df-his-api" / "src/main/java"
            api.mkdir(parents=True)
            (api / "Settlement.java").write_text(
                "private Date chuangJianSj;\nprivate Integer hetongbz;\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text=(
                    "新疆佳音医院保孕业务改造：住院医嘱、护士记账、医技、手术室、门诊和住院结算；"
                    "新增hetongbz合同外标记和医嘱处理_启用合同外选择参数，涉及yz_bingrenyz、"
                    "YF_YIZHUSQD1、YF_JIFEIJK1、ZY_JIFEIJK1、ZY_JIFEIDAN、zy_feiyong1，"
                    "结算还涉及预交款和保孕支付。"
                ),
                project_root=root,
            )

        self.assertEqual("multi_service_feature", result.field_provenance["field_kind"])
        self.assertNotEqual("chuangJianSj", result.field_provenance["target_field"])
        self.assertFalse(result.implementation_decision["can_patch"])
        self.assertEqual([], result.recommended_allowed_paths)

    def test_v2_work_items_are_included_when_classifying_complex_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_technical_decision(
                demand_text="",
                yunxiao_evidence={
                    "work_items": [
                        {
                            "title": "保孕业务改造",
                            "description": "涉及合同外 hetongbz、护士记账、医技、手术费用和住院结算。",
                            "comments": [{"content": "医嘱处理_启用合同外选择参数。"}],
                        }
                    ]
                },
                project_root=Path(temp_dir),
            )

        self.assertEqual("multi_service_feature", result.field_provenance["field_kind"])
        self.assertFalse(result.implementation_decision["can_patch"])

    def test_v2_work_item_metadata_does_not_pollute_business_project_terms(self) -> None:
        combined_text = build_combined_text(
            demand_text="",
            yunxiao_evidence={
                "work_items": [
                    {
                        "title": "保孕业务改造",
                        "description": "涉及合同外 hetongbz 和住院结算。",
                        "attachments_status": {
                            "errorMessage": "文件不存在",
                        },
                    }
                ]
            },
        )

        self.assertIn("合同外", combined_text)
        self.assertNotIn("errorMessage", combined_text)

    def test_generic_discovery_graph_is_exposed_for_an_unrelated_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-patient"
            page = frontend / "src/pages/query/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <df-select label="就诊状态" v-model="query.visitState" />
                queryVisitList(query)
                """,
                encoding="utf-8",
            )
            api = frontend / "src/apis/patient.js"
            api.parent.mkdir(parents=True)
            api.write_text(
                "export const queryVisitList = (params) => request({ params })\n",
                encoding="utf-8",
            )
            service = root / "df-mic-patient"
            controller = service / "src/main/java/VisitController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                "queryVisitList(String visitState) {}\n",
                encoding="utf-8",
            )
            entity = service / "src/main/java/Visit.java"
            entity.write_text(
                "/** 就诊状态 */ private String visitState;\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="病人列表增加就诊状态筛选，默认全部。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(service)],
            )

        graph = result.field_provenance["evidence_graph"]
        self.assertTrue(
            any(node["kind"] == "stored_field" and "visitState" in node["identifiers"] for node in graph["nodes"])
        )
        self.assertTrue(
            any(edge["kind"] == "request_flow" and edge["identifier"] == "queryVisitList" for edge in graph["edges"])
        )
        self.assertEqual("visitState", result.field_provenance["discovery_target_field"])

    def test_morning_afternoon_filter_discovers_stored_field_and_query_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            page = frontend / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <df-search-bar />
                const columns = [{ dataField: 'shangXiaWWsBz', caption: '上下午' }]
                shangXiaWWsBz: ''
                // 0-上午，1-下午，2-晚上
                getGuaHaoPageList(param)
                """,
                encoding="utf-8",
            )
            frontend_api = frontend / "src/apis/winbff-guahaosf/guaHao.js"
            frontend_api.parent.mkdir(parents=True)
            frontend_api.write_text(
                "export const getGuaHaoPageList = (params) => request({ params })\n",
                encoding="utf-8",
            )

            bff = root / "df-bff-guahaosf"
            bff_controller = bff / "src/main/java/GuaHaoController.java"
            bff_controller.parent.mkdir(parents=True)
            bff_controller.write_text(
                "getGuaHaoPageList(String shangXiaWWsBz) {}\n",
                encoding="utf-8",
            )

            service = root / "df-mic-jj-menzhen"
            service_controller = service / "src/main/java/GuaHaoController.java"
            service_controller.parent.mkdir(parents=True)
            service_controller.write_text(
                "getGuaHaoPageList() {}\n",
                encoding="utf-8",
            )
            entity = service / "src/main/java/DO_MZ_GuaHao.java"
            entity.write_text(
                """
                /** 上下午晚上标志 */
                private Integer shangXiaWWsBz;
                """,
                encoding="utf-8",
            )
            repository = service / "src/main/java/MzGuaHaoDslRepositoryImpl.java"
            repository.write_text(
                "qmzGuaHao.shangXiaWWsBz.eq(shangXiaWWsBz);\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="挂号收费--收费病人查询，增加一个上午下午的筛选条件，默认全部。",
                project_root=root,
            )

        provenance = result.field_provenance
        evidence_paths = {
            f"{item['project']}:{item['path']}"
            for item in provenance["evidence"]
        }
        self.assertEqual("shangXiaWWsBz", provenance["target_field"])
        self.assertEqual("discovered_stored_filter", provenance["field_kind"])
        self.assertTrue(provenance["target_ui_found"])
        self.assertTrue(provenance["field_returned"])
        self.assertEqual("complete", provenance["query_chain"]["status"])
        self.assertTrue(
            provenance["query_chain"]["layers"]["stored_field"][0].endswith(
                "DO_MZ_GuaHao.java"
            )
        )
        self.assertIn(
            "df-web-guahaosf:src/pages/chaXunTj/guaHaoChaX/index.vue",
            evidence_paths,
        )
        self.assertIn(
            "df-mic-jj-menzhen:src/main/java/DO_MZ_GuaHao.java",
            evidence_paths,
        )
        self.assertIn(
            "df-mic-jj-menzhen:src/main/java/MzGuaHaoDslRepositoryImpl.java",
            evidence_paths,
        )
        self.assertEqual(
            ["全部", "上午", "下午"],
            result.implementation_decision["filter_options"],
        )
        self.assertTrue(
            any(rule.startswith("上午传 0") for rule in result.implementation_decision["rules"])
        )
        self.assertTrue(
            any(rule.startswith("下午传 1") for rule in result.implementation_decision["rules"])
        )
        self.assertFalse(result.can_patch)
        self.assertIn("跨层查询证据", "\n".join(result.implementation_decision["blockers"]))

    def test_discovery_ignores_date_noise_when_a_bound_stored_filter_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            page = frontend / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <df-data-grid :columns="[{ dataField: 'shangXiaWWsBz', caption: '上下午' }]" />
                const optionMap = { '0': '上午', '1': '下午', '2': '晚上' }
                const now = new Date()
                getGuaHaoPageList(param)
                """,
                encoding="utf-8",
            )
            api = frontend / "src/apis/winbff-guahaosf/guaHao.js"
            api.parent.mkdir(parents=True)
            api.write_text(
                "export const getGuaHaoPageList = (params) => request({ params })\n",
                encoding="utf-8",
            )
            service = root / "df-mic-jj-menzhen"
            entity = service / "src/main/java/DO_MZ_GuaHao.java"
            entity.parent.mkdir(parents=True)
            entity.write_text(
                """
                /** 挂号日期 */
                private Date guaHaoRq;
                /** 上下午晚上标志 */
                private Integer shangXiaWWsBz;
                """,
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="挂号收费--收费病人查询，增加一个上午下午的筛选条件，默认全部。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(service)],
            )

        self.assertEqual("shangXiaWWsBz", result.field_provenance["discovery_target_field"])
        self.assertEqual(
            ["全部", "上午", "下午"],
            result.implementation_decision["filter_options"],
        )

    def test_query_chain_prefers_the_cross_project_list_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            page = frontend / "src/pages/guaHaoChaX/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <df-data-grid :columns="[{ dataField: 'shangXiaWWsBz', caption: '上下午' }]" />
                queryParam()
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
            helper = frontend / "src/utils/query.js"
            helper.parent.mkdir(parents=True)
            helper.write_text("export const queryParam = () => ({})\n", encoding="utf-8")
            service = root / "df-mic-jj-menzhen"
            controller = service / "src/main/java/GuaHaoController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                "getGuaHaoPageList(String shangXiaWWsBz) {}\n",
                encoding="utf-8",
            )
            entity = service / "src/main/java/DO_MZ_GuaHao.java"
            entity.write_text(
                "/** 上下午晚上标志 */ private Integer shangXiaWWsBz;\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="收费病人查询增加上午下午筛选，默认全部。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(service)],
            )

        self.assertEqual(
            "getGuaHaoPageList",
            result.field_provenance["query_chain"]["endpoint"],
        )

    def test_query_chain_does_not_accept_an_unrelated_bff_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            page = frontend / "src/pages/guaHaoChaX/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                """
                <df-data-grid :columns="[{ dataField: 'shangXiaWWsBz', caption: '上下午' }]" />
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
            bff = root / "df-bff-guahaosf"
            bff_controller = bff / "src/main/java/OtherController.java"
            bff_controller.parent.mkdir(parents=True)
            bff_controller.write_text("getOtherPageList() {}\n", encoding="utf-8")
            service = root / "df-mic-jj-menzhen"
            service_controller = service / "src/main/java/GuaHaoController.java"
            service_controller.parent.mkdir(parents=True)
            service_controller.write_text(
                "getGuaHaoPageList(String shangXiaWWsBz) {}\n",
                encoding="utf-8",
            )
            entity = service / "src/main/java/DO_MZ_GuaHao.java"
            entity.write_text(
                "/** 上下午晚上标志 */ private Integer shangXiaWWsBz;\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="收费病人查询增加上午下午筛选，默认全部。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(bff), str(service)],
            )

        self.assertEqual("incomplete", result.field_provenance["query_chain"]["status"])

    def test_tab_switch_return_does_not_imply_service_contract(self) -> None:
        self.assertFalse(
            requires_service_contract("切换顶部业务页签再返回，已输入条件和查询结果不能被清空。")
        )

    def test_negated_interface_scope_does_not_imply_service_contract(self) -> None:
        self.assertFalse(
            requires_service_contract("不修改查询接口、查询参数、发票页签或后端服务。")
        )

    def test_generated_calibration_card_does_not_create_service_contract(self) -> None:
        self.assertFalse(
            requires_service_contract(
                "切换顶部业务页签再返回，查询结果不能被清空。\n\n"
                "【Harness v0.15 需求理解确认卡】\n"
                "字段 / 参数：无。\n"
            )
        )

    def test_combined_text_excludes_generated_appendices_from_prior_runs(self) -> None:
        combined = build_combined_text(
            demand_text=(
                "当前排班显示诊室 zhenShiMc。\n"
                "【需求来源归一化证据】\n"
                "旧的通用扫描候选字段 paiBanId 和无关退费接口。\n"
                "【Harness v0.15 需求理解确认卡】\n"
                "旧分析生成内容。"
            ),
            requirement_evidence={"description_text": "云效原始正文仍然保留。"},
        )

        self.assertIn("当前排班显示诊室 zhenShiMc", combined)
        self.assertIn("云效原始正文仍然保留", combined)
        self.assertNotIn("旧的通用扫描候选字段", combined)
        self.assertNotIn("旧分析生成内容", combined)

    def test_explicit_allowlisted_source_is_strong_engineering_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "df-web-bui"
            target = project / "src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js"
            target.parent.mkdir(parents=True)
            target.write_text("export const ziDianInfo = {}\n", encoding="utf-8")

            result = build_technical_decision(
                demand_text="挂号缩减版默认值与建档保持一致。",
                project_root=Path(temp_dir),
                explicit_project_paths=[str(project)],
                explicit_allowed_paths=["src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js"],
            )

        self.assertTrue(result.can_patch)
        self.assertEqual(["src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js"], result.recommended_allowed_paths)
        self.assertTrue(result.field_provenance["target_ui_found"])

    def test_explicit_allowlist_preserves_named_display_field_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            card = frontend / "src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue"
            card.parent.mkdir(parents=True)
            card.write_text(
                "<template><div>{{ data.yiShengMc }}</div></template>\n",
                encoding="utf-8",
            )
            page = frontend / "src/pages/yeWuGn/guaHaoSf/index.vue"
            page.write_text(
                "getPaiBanListByJiaGeTxV2().then(rows => { const room = rows[0].zhenShiMc })\n",
                encoding="utf-8",
            )
            backend = root / "df-mic-jj-menzhen"
            dto = backend / "mic-jj-guahao-api/src/main/java/DTO_MZ_GuaHaoPb.java"
            dto.parent.mkdir(parents=True)
            dto.write_text("private String zhenShiMc;\n", encoding="utf-8")

            result = build_technical_decision(
                demand_text=(
                    "挂号界面增加每个排班对应的诊室信息显示。"
                    "诊室来源必须是当前排班的 zhenShiMc；没有维护时保持空白。"
                    "目标组件是 src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue。"
                    "后端、BFF 和数据库不修改。"
                ),
                project_root=root,
                explicit_project_paths=[str(frontend), str(backend)],
                explicit_allowed_paths=["src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue"],
            )

        self.assertEqual("zhenShiMc", result.field_provenance["target_field"])
        self.assertEqual("explicit_display_field", result.field_provenance["field_kind"])
        self.assertTrue(result.field_provenance["field_returned"])
        self.assertEqual(
            ["src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue"],
            result.recommended_allowed_paths,
        )
        self.assertNotIn(
            "src/pages/yeWuGn/guaHaoSf/components/paiBanCard.vue",
            result.field_provenance["field_source_paths"],
        )

    def test_explicit_allowlisted_missing_source_blocks_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "df-web-bui"
            project.mkdir()
            result = build_technical_decision(
                demand_text="挂号缩减版默认值与建档保持一致。",
                project_root=Path(temp_dir),
                explicit_project_paths=[str(project)],
                explicit_allowed_paths=["src/missing.js"],
            )

        self.assertFalse(result.can_patch)
        self.assertIn("白名单路径不存在", "\n".join(result.implementation_decision["blockers"]))

    def test_service_contract_blocks_client_patch_without_server_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            target = frontend / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text(
                "getGuaHaoPageList({sortField: this.sortField, sortOrder: this.sortOrder})\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="挂号病人查询增加排序入参。",
                yunxiao_evidence={
                    "comments": [{"content": "getGuaHaoPageList 入参新增 sortField、sortOrder，后端已支持。"}]
                },
                project_root=root,
                explicit_project_paths=[str(frontend)],
                explicit_allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
            )

        contract = result.contract_verification
        self.assertFalse(result.can_patch)
        self.assertEqual("blocked", contract["status"])
        self.assertIn("服务端", "\n".join(result.implementation_decision["blockers"]))

    def test_service_contract_allows_client_patch_when_request_and_server_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            target = frontend / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text(
                "getGuaHaoPageList({sortField: this.sortField, sortOrder: this.sortOrder})\n",
                encoding="utf-8",
            )
            backend = root / "df-bff-guahaosf"
            handler = backend / "src/main/java/GuaHaoController.java"
            handler.parent.mkdir(parents=True)
            handler.write_text(
                "getGuaHaoPageList(String sortField, String sortOrder) {}\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="挂号病人查询增加排序入参。",
                yunxiao_evidence={
                    "comments": [{"content": "getGuaHaoPageList 入参新增 sortField、sortOrder，后端已支持。"}]
                },
                project_root=root,
                explicit_project_paths=[str(frontend)],
                explicit_allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
            )

        contract = result.contract_verification
        self.assertTrue(result.can_patch)
        self.assertEqual("verified", contract["status"])
        self.assertEqual("verified", contract["layers"]["client_request"]["status"])
        self.assertEqual("verified", contract["layers"]["server_contract"]["status"])

    def test_explicit_contract_parameters_override_conflicting_comment_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            target = frontend / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text("getGuaHaoPageList({sortField: this.sortField})\n", encoding="utf-8")
            backend = root / "df-bff-guahaosf"
            handler = backend / "src/main/java/GuaHaoController.java"
            handler.parent.mkdir(parents=True)
            handler.write_text("getGuaHaoPageList(String sortField) {}\n", encoding="utf-8")

            result = build_technical_decision(
                demand_text="前端只传 sortField，值为 字段A|排序方式,字段B|排序方式。",
                yunxiao_evidence={
                    "comments": [{"content": "getGuaHaoPageList 入参新增 sortField、sortOrder，后端已支持。"}]
                },
                project_root=root,
                explicit_project_paths=[str(frontend), str(backend)],
                explicit_allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
                contract_parameters=["sortField"],
            )

        contract = result.contract_verification
        self.assertTrue(result.can_patch)
        self.assertEqual(["getGuaHaoPageList", "sortField"], contract["contract_terms"])
        self.assertEqual("explicit_resolved_parameters", contract["parameter_source"])
        self.assertEqual("verified", contract["status"])

    def test_service_contract_requires_every_named_parameter_on_same_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            target = frontend / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text(
                "getGuaHaoPageList({sortField: this.sortField, sortOrder: this.sortOrder})\n",
                encoding="utf-8",
            )
            backend = root / "df-bff-guahaosf"
            handler = backend / "src/main/java/GuaHaoController.java"
            handler.parent.mkdir(parents=True)
            handler.write_text(
                "getGuaHaoPageList(String sortField, Integer pageSize) {}\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="接口 getGuaHaoPageList 入参新增 sortField=排序字段，sortOrder=排序方式(desc/asc)。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(backend)],
                explicit_allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
            )

        self.assertFalse(result.can_patch)
        self.assertEqual("blocked", result.contract_verification["status"])
        self.assertEqual("missing", result.contract_verification["layers"]["server_contract"]["status"])

    def test_service_contract_does_not_accept_parameter_far_from_request_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            target = frontend / "src/pages/chaXunTj/guaHaoChaX/index.vue"
            target.parent.mkdir(parents=True)
            target.write_text(
                "sortOrder: 'desc'\n" + ("// unrelated\n" * 60) + "getGuaHaoPageList({sortField: this.sortField})\n",
                encoding="utf-8",
            )
            backend = root / "df-bff-guahaosf"
            handler = backend / "src/main/java/GuaHaoController.java"
            handler.parent.mkdir(parents=True)
            handler.write_text(
                "getGuaHaoPageList(String sortField, String sortOrder) {}\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="接口 getGuaHaoPageList 入参新增 sortField=排序字段，sortOrder=排序方式(desc/asc)。",
                project_root=root,
                explicit_project_paths=[str(frontend), str(backend)],
                explicit_allowed_paths=["src/pages/chaXunTj/guaHaoChaX/index.vue"],
            )

        self.assertFalse(result.can_patch)
        self.assertEqual("missing", result.contract_verification["layers"]["client_request"]["status"])

    def test_route_parameter_does_not_require_server_contract_without_api_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-guahaosf"
            target = frontend / "src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"
            target.parent.mkdir(parents=True)
            target.write_text("export const filterByPaiBanMs = () => []\n", encoding="utf-8")

            result = build_technical_decision(
                demand_text="菜单/路由参数 paiBanMs：1 只过滤医生为空的排班；2 只过滤有医生的排班。",
                project_root=root,
                explicit_project_paths=[str(frontend)],
                explicit_allowed_paths=["src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"],
            )

        self.assertTrue(result.can_patch)
        self.assertEqual("not_required", result.contract_verification["status"])

    def test_service_graph_keeps_direct_microservice_and_bff_dependency_as_separate_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-yibaogl"
            router = frontend / "src/router/yiBaoMlDz/index.js"
            router.parent.mkdir(parents=True)
            router.write_text(
                "export default [{ path: '/yiBaoSpXmWh', meta: { title: '医保审批项目维护' }, component: () => import('@/views/yiBaoMlDz/yiBaoSpXmWh') }]\n",
                encoding="utf-8",
            )
            api = frontend / "src/views/yiBaoMlDz/yiBaoSpXmWh/api/yiBaoSpXmApi.js"
            api.parent.mkdir(parents=True)
            api.write_text(
                """
                export const urls = {
                  catalog: '/yb-yibaogl/YiBaoSpXmWh/getYiYuanMuLuPage',
                  category: '/winbff-jichufw/shouFeiXm/getFenLeiTree'
                }
                """,
                encoding="utf-8",
            )
            unrelated_api = frontend / "src/views/unrelated/api.js"
            unrelated_api.parent.mkdir(parents=True)
            unrelated_api.write_text(
                "export const unrelated = '/winbff-yibaogl/Other/getList'\n",
                encoding="utf-8",
            )

            micro = root / "df-mic-yibaogl"
            micro_controller = micro / "src/main/java/YiBaoSpXmWhController.java"
            micro_controller.parent.mkdir(parents=True)
            micro_controller.write_text(
                """
                @RequestMapping("/YiBaoSpXmWh")
                class YiBaoSpXmWhController {
                  @PostMapping("/getYiYuanMuLuPage")
                  DTO_YB_YiYuanMuLuXx getYiYuanMuLuPage() { return null; }
                }
                """,
                encoding="utf-8",
            )
            (micro / "build.gradle").write_text(
                "compile ('com.df.cbhis:mic-yb-yibaogl-api:3.0.0-SNAPSHOT')\n",
                encoding="utf-8",
            )

            bff = root / "df-bff-jichufw"
            bff_controller = bff / "src/main/java/ShouFeiXmController.java"
            bff_controller.parent.mkdir(parents=True)
            bff_controller.write_text(
                """
                @RequestMapping("/shouFeiXm")
                class ShouFeiXmController {
                  @PostMapping("/getFenLeiTree")
                  Object getFenLeiTree() { return null; }
                }
                """,
                encoding="utf-8",
            )

            contract = root / "df-his-api/mic-yb-yibaogl-api/src/main/java/DTO_YB_YiYuanMuLuXx.java"
            contract.parent.mkdir(parents=True)
            contract.write_text("class DTO_YB_YiYuanMuLuXx {}\n", encoding="utf-8")
            unrelated_bff = root / "df-bff-yibaogl"
            unrelated_bff.mkdir()

            result = build_technical_decision(
                demand_text="优化医保审批项目维护功能，医保审批维护和医保对照放在一个页面。",
                project_root=root,
            )

        service_graph = result.field_provenance["service_graph"]
        by_project = {node["project"]: node for node in service_graph["nodes"]}
        self.assertEqual("change_required", by_project["df-web-yibaogl"]["scope"])
        self.assertEqual("candidate_change", by_project["df-mic-yibaogl"]["scope"])
        self.assertEqual("existing_dependency", by_project["df-bff-jichufw"]["scope"])
        self.assertEqual("contract_check", by_project["df-his-api"]["scope"])
        self.assertNotIn("df-bff-yibaogl", by_project)
        self.assertEqual(2, len(service_graph["branches"]))
        self.assertEqual("evidence_ready", service_graph["status"])
        self.assertEqual([], service_graph["unresolved_endpoints"])

    def test_service_graph_markdown_is_saved_as_a_task_artifact(self) -> None:
        result = TechnicalDecisionResult(
            artifacts={
                "project_selection_markdown": "project",
                "field_provenance_markdown": "field",
                "implementation_decision_markdown": "decision",
                "service_graph_markdown": "graph",
            }
        )
        saved_kinds: list[str] = []

        with patch("app.harness.database.add_artifact", side_effect=lambda _run_id, kind, _title, _content: saved_kinds.append(kind)):
            RequirementWorkflowRunner._store_technical_decision_artifacts(object(), 1, result)

        self.assertIn("service_graph_markdown", saved_kinds)
        self.assertEqual("service_graph.md", artifact_output_name(kind="service_graph_markdown", artifact_id=1))

    def test_multi_service_contract_is_saved_as_a_task_artifact(self) -> None:
        result = TechnicalDecisionResult(
            multi_service_change_contract={
                "schema_version": "multi-service-change-contract.v1",
                "status": "blocked",
                "blockers": ["runtime validation missing"],
                "targets": [],
                "repositories": {},
                "rollback": {"status": "not_available", "strategy": "no apply"},
            }
        )
        saved_kinds: list[str] = []

        with patch("app.harness.database.add_artifact", side_effect=lambda _run_id, kind, _title, _content: saved_kinds.append(kind)):
            RequirementWorkflowRunner._store_technical_decision_artifacts(object(), 1, result)

        self.assertIn("multi_service_change_contract_json", saved_kinds)
        self.assertIn("multi_service_change_contract_markdown", saved_kinds)
        self.assertEqual("multi_service_change_contract.json", artifact_output_name(kind="multi_service_change_contract_json", artifact_id=1))

    def test_service_graph_keeps_all_matching_frontend_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-demo"
            for page_name, endpoint, controller_name, method in (
                ("first", "/yb-demo/First/getFirst", "FirstController.java", "getFirst"),
                ("second", "/yb-demo/Second/getSecond", "SecondController.java", "getSecond"),
                ("broad", "/yb-demo/Broad/getBroad", "BroadController.java", "getBroad"),
            ):
                page = frontend / f"src/views/{page_name}/index.vue"
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text(
                    f"{'医保审批' if page_name == 'broad' else '医保审批项目维护'}\nconst url = '{endpoint}'\n",
                    encoding="utf-8",
                )
                service = root / "df-mic-demo"
                controller = service / f"src/main/java/{controller_name}"
                controller.parent.mkdir(parents=True, exist_ok=True)
                controller.write_text(
                    f'@RequestMapping("/{page_name.title()}") class C {{ @PostMapping("/{method}") Object {method}() {{}} }}',
                    encoding="utf-8",
                )

            result = build_technical_decision(
                demand_text="优化医保审批项目维护功能，两个页面都要支持。",
                project_root=root,
            )

        graph = result.field_provenance["service_graph"]
        self.assertEqual(
            {"/yb-demo/First/getFirst", "/yb-demo/Second/getSecond"},
            {branch["endpoint"] for branch in graph["branches"]},
        )

    def test_service_graph_does_not_mark_every_page_dependency_as_change_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-demo"
            page = frontend / "src/views/医保审批项目维护/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "医保审批项目维护\n"
                "const urls = { changed: '/yb-demo/Approval/getList', "
                "existing: '/yb-demo/Approval/getAuditLog' }\n",
                encoding="utf-8",
            )
            service = root / "df-mic-demo"
            controller = service / "src/main/java/ApprovalController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/Approval") class C { '
                '@PostMapping("/getList") Object getList() {} '
                '@PostMapping("/getAuditLog") Object getAuditLog() {} }',
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="优化医保审批项目维护功能，调整 /yb-demo/Approval/getList。",
                project_root=root,
            )

        scopes = {
            branch["endpoint"]: branch["scope"]
            for branch in result.field_provenance["service_graph"]["branches"]
        }
        self.assertEqual("change_required", scopes["/yb-demo/Approval/getList"])
        self.assertNotEqual("change_required", scopes["/yb-demo/Approval/getAuditLog"])

    def test_confirmed_code_locator_wins_over_generic_sibling_frontend_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            primary = root / "df-web-refund"
            primary_page = primary / "src/pages/refund/index.vue"
            primary_page.parent.mkdir(parents=True)
            primary_page.write_text(
                "退费按钮\n"
                "request.post('/winbff-refund/shouFei/menZhenTfYjs')\n",
                encoding="utf-8",
            )

            sibling = root / "df-web-claims"
            sibling_page = sibling / "src/pages/refund/index.vue"
            sibling_page.parent.mkdir(parents=True)
            sibling_page.write_text(
                "退费按钮页面\n"
                "request.post('/winbff-claims/shouFei/menZhenTf')\n",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="调整退费按钮处理，当前只读定位，不修改代码。",
                project_root=root,
                authoritative_code_locators=(
                "menZhenTfYjs\n"
                "menZhenTf\n"
                "winbff-refund/shouFei/menZhenTfYjs"
                ),
            )

        by_project = {item["name"]: item for item in result.selected_projects}
        self.assertTrue(by_project["df-web-refund"]["authoritative_code_match"])
        self.assertEqual("change_required", by_project["df-web-refund"]["selection_scope"])
        self.assertFalse(by_project["df-web-claims"]["authoritative_code_match"])
        self.assertNotEqual("change_required", by_project["df-web-claims"]["selection_scope"])

    def test_authoritative_code_locator_narrows_graph_to_the_confirmed_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-refund"
            page = frontend / "src/pages/refund/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "退费按钮\n"
                "request.post('winbff-refund/shouFei/menZhenTfYjs')\n"
                "request.post('/winbff-refund/shouFei/checkZiJinZh')\n",
                encoding="utf-8",
            )
            backend = root / "df-bff-refund"
            controller = backend / "src/main/java/RefundController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@RequestMapping("/shouFei") class C { '
                '@PostMapping("/menZhenTfYjs") Object menZhenTfYjs() {} '
                '@PostMapping("/checkZiJinZh") Object checkZiJinZh() {} }',
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="调整退费按钮处理，当前只读定位，不修改代码。",
                project_root=root,
                authoritative_code_locators=(
                    "menZhenTfYjs\n"
                    "winbff-refund/shouFei/menZhenTfYjs"
                ),
            )

        graph = result.field_provenance["service_graph"]
        self.assertEqual(
            ["/winbff-refund/shouFei/menZhenTfYjs"],
            [branch["endpoint"] for branch in graph["branches"]],
        )
        self.assertEqual(["src/pages/refund/index.vue"], result.recommended_allowed_paths)

    def test_query_chain_closes_all_verified_service_graph_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-demo"
            page = frontend / "src/views/医保审批项目维护/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                "<df-select v-model=\"query.filterCode\" label=\"审批项目\" />\n"
                "const optionMap = { '0': '全部', '1': '启用' }\n"
                "getYbList(query)\ngetBffTree(query)\n"
                "const urls = { list: '/yb-demo/Approval/getYbList', "
                "tree: '/winbff-common/Category/getBffTree' }\n",
                encoding="utf-8",
            )
            service = root / "df-mic-demo"
            service_controller = service / "src/main/java/ApprovalController.java"
            service_controller.parent.mkdir(parents=True)
            service_controller.write_text(
                "@RequestMapping(\"/Approval\") class C { "
                "@PostMapping(\"/getYbList\") Object getYbList(String filterCode) {} }",
                encoding="utf-8",
            )
            (service / "src/main/java/Approval.java").write_text(
                "/** 审批项目 */ private String filterCode;",
                encoding="utf-8",
            )
            bff = root / "df-bff-common"
            bff_controller = bff / "src/main/java/CategoryController.java"
            bff_controller.parent.mkdir(parents=True)
            bff_controller.write_text(
                "@RequestMapping(\"/Category\") class C { "
                "@PostMapping(\"/getBffTree\") Object getBffTree() {} }",
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="优化医保审批项目维护功能，增加审批项目筛选条件，默认全部。",
                project_root=root,
            )

        query_chain = result.field_provenance["query_chain"]
        self.assertEqual("complete", query_chain["status"])
        self.assertEqual(
            {"getYbList", "getBffTree"},
            {branch["endpoint"] for branch in query_chain["branches"]},
        )
        self.assertTrue(query_chain["layers"]["bff"])
        self.assertTrue(query_chain["layers"]["service"])

    def test_query_chain_stays_incomplete_when_a_graph_branch_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "df-web-demo"
            page = frontend / "src/views/医保审批项目维护/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                '<df-select v-model="query.filterCode" label="审批项目" />\n'
                "getYbList(query)\ngetBffTree(query)\n"
                "const urls = { list: '/yb-demo/Approval/getYbList', "
                "tree: '/winbff-common/Category/getBffTree' }\n",
                encoding="utf-8",
            )
            service = root / "df-mic-demo"
            service_controller = service / "src/main/java/ApprovalController.java"
            service_controller.parent.mkdir(parents=True)
            service_controller.write_text(
                '@RequestMapping("/Approval") class C { '
                '@PostMapping("/getYbList") Object getYbList(String filterCode) {} }',
                encoding="utf-8",
            )
            (service / "src/main/java/Approval.java").write_text(
                "/** 审批项目 */ private String filterCode;",
                encoding="utf-8",
            )
            bff = root / "df-bff-common"
            bff_controller = bff / "src/main/java/CategoryController.java"
            bff_controller.parent.mkdir(parents=True)
            bff_controller.write_text(
                '@RequestMapping("/Other") class C { '
                '@PostMapping("/getBffTree") Object getBffTree() {} }',
                encoding="utf-8",
            )

            result = build_technical_decision(
                demand_text="优化医保审批项目维护功能，增加审批项目筛选条件，默认全部。",
                project_root=root,
            )

        query_chain = result.field_provenance["query_chain"]
        self.assertEqual("incomplete", query_chain["status"])
        self.assertEqual(
            ["getBffTree"],
            [branch["endpoint"] for branch in query_chain["unresolved_branches"]],
        )

    def test_controller_route_requires_class_and_method_mapping_in_same_scope(self) -> None:
        text = (
            '@RequestMapping("/Approval") class First { @PostMapping("/other") Object other() {} }\n'
            '@RequestMapping("/Other") class Second { @PostMapping("/getList") Object getList() {} }\n'
        )
        self.assertEqual(
            [],
            find_controller_paths_for_endpoint(
                project_path=Path("/tmp"),
                endpoint="/yb-demo/Approval/getList",
                source_files=[("BadController.java", text)],
            ),
        )

    def test_public_api_contract_ignores_documentation_only_dto_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = root / "df-mic-demo"
            controller = service / "src/main/java/ApprovalController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text("class C { DTO_ApprovalPage getList() {} }", encoding="utf-8")
            api = root / "df-his-api"
            (api / "module-a").mkdir(parents=True)
            (api / "module-b").mkdir(parents=True)
            (api / "module-a/DTO_ApprovalPage.java").write_text("class DTO_ApprovalPage {}", encoding="utf-8")
            (api / "module-b/README.md").write_text("DTO_ApprovalPage documentation", encoding="utf-8")

            matches = find_public_api_contract_paths(
                root=root,
                service_path=service,
                controller_paths=["src/main/java/ApprovalController.java"],
            )

        self.assertNotIn("module-b/README.md", matches)

    def test_public_api_contract_does_not_pull_unrelated_shared_api_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = root / "df-mic-yibaogl"
            controller = service / "src/main/java/ApprovalController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                "class C { DTO_YB_ApprovalPage getList() {} DTO_PageData page() {} }",
                encoding="utf-8",
            )
            api = root / "df-his-api"
            (api / "mic-yb-yibaogl-api/src/main/java").mkdir(parents=True)
            (api / "mic-jj-guahao-api/src/main/java").mkdir(parents=True)
            (api / "mic-yb-yibaogl-api/src/main/java/DTO_YB_ApprovalPage.java").write_text(
                "class DTO_YB_ApprovalPage {}",
                encoding="utf-8",
            )
            (api / "mic-jj-guahao-api/src/main/java/DTO_PageData.java").write_text(
                "class DTO_PageData {}",
                encoding="utf-8",
            )

            matches = find_public_api_contract_paths(
                root=root,
                service_path=service,
                controller_paths=["src/main/java/ApprovalController.java"],
            )

        self.assertIn("mic-yb-yibaogl-api/src/main/java/DTO_YB_ApprovalPage.java", matches)
        self.assertNotIn("mic-jj-guahao-api/src/main/java/DTO_PageData.java", matches)

    def test_technical_decision_and_service_graph_use_consistent_version_labels(self) -> None:
        result = TechnicalDecisionResult()
        self.assertIn(f"## v{result.version} 技术自治决策", result.to_markdown())
        self.assertTrue(
            service_graph_to_markdown({"status": "not_applicable"}).startswith(
                f"## v{result.version} 服务图"
            )
        )
