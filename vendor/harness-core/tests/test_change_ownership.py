from __future__ import annotations

import unittest

from app.change_ownership import build_change_ownership_matrix
from app.core_closure import build_requirement_contract


def technical_decision(
    *,
    contract_required: bool = False,
    client_status: str = "not_required",
    server_status: str = "not_required",
    server_evidence: bool = False,
) -> dict:
    return {
        "selected_projects": [
            {
                "path": "/tmp/df-web-guahaosf",
                "name": "df-web-guahaosf",
                "role": "frontend",
                "exists": True,
            }
        ],
        "field_provenance": {
            "target_ui_found": True,
            "evidence": [
                {
                    "project": "df-web-guahaosf",
                    "path": "src/pages/guaHaoChaXun/index.vue",
                    "reason": "目标页面和请求入口命中",
                }
            ],
        },
        "contract_verification": {
            "required": contract_required,
            "status": "verified" if server_status == "verified" and client_status == "verified" else "blocked",
            "layers": {
                "client_request": {
                    "status": client_status,
                    "evidence_paths": ["src/pages/guaHaoChaXun/index.vue"] if client_status == "verified" else [],
                },
                "server_contract": {
                    "status": server_status,
                    "evidence_paths": ["src/main/java/GuaHaoController.java"] if server_evidence else [],
                },
            },
        },
        "implementation_decision": {"can_patch": True, "blockers": []},
        "recommended_allowed_paths": ["src/pages/guaHaoChaXun/index.vue"],
        "recommended_verify_commands": ["test -f src/pages/guaHaoChaXun/index.vue"],
    }


class ChangeOwnershipMatrixTests(unittest.TestCase):
    def test_grouped_no_change_boundary_does_not_invent_backend_or_database_work(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction=(
                "候选改动只应落在前端排班卡片展示；"
                "后端、BFF、公共 API 和数据库均不应修改。"
            ),
            requirement_text="每个挂号医生后面显示当前排班的诊室。",
            technical_decision=technical_decision(),
        )

        self.assertEqual("ready", matrix.status)
        self.assertEqual("required", matrix.row("frontend").status)
        self.assertEqual("not_required", matrix.row("backend").status)
        self.assertEqual("not_required", matrix.row("database").status)

    def test_frontend_evidence_does_not_include_backend_field_source_paths(self) -> None:
        decision = technical_decision()
        decision["selected_projects"].append(
            {
                "path": "/tmp/df-mic-jj-menzhen",
                "name": "df-mic-jj-menzhen",
                "role": "backend",
                "exists": True,
            }
        )
        decision["field_provenance"]["evidence"][0]["kind"] = "explicit_target_ui"
        decision["field_provenance"]["evidence"].append(
            {
                "project": "df-mic-jj-menzhen",
                "kind": "field_source",
                "path": "src/main/java/GuaHaoPb.java",
                "reason": "DTO 已返回 zhenShiMc",
            }
        )

        matrix = build_change_ownership_matrix(
            user_instruction="仅在排班卡片显示诊室，后端和数据库均不修改。",
            requirement_text="每个挂号医生后面显示当前排班的诊室。",
            technical_decision=decision,
        )

        evidence_paths = {item.get("path") for item in matrix.row("frontend").evidence}
        self.assertIn("src/pages/guaHaoChaXun/index.vue", evidence_paths)
        self.assertNotIn("src/main/java/GuaHaoPb.java", evidence_paths)

    def test_direct_base_table_boundary_requires_backend_resolution(self) -> None:
        decision = technical_decision(contract_required=True, client_status="verified", server_status="verified", server_evidence=True)
        decision["selected_projects"].extend(
            [
                {"path": "/tmp/df-mic-yibaogl", "name": "df-mic-yibaogl", "role": "backend", "exists": True},
                {"path": "/tmp/df-mic-jichufw", "name": "df-mic-jichufw", "role": "backend", "exists": True},
            ]
        )
        decision["field_provenance"]["service_graph"] = {
            "boundary_findings": [
                {
                    "type": "direct_cross_schema_access",
                    "status": "conflict",
                    "message": "业务服务直接查询底层表。",
                }
            ]
        }

        matrix = build_change_ownership_matrix(
            user_instruction="医院目录来源必须是 df-mic-jichufw 的 gy_shoufeixm。",
            requirement_text="确认前端、BFF、业务微服务、底层服务边界。",
            technical_decision=decision,
        )

        self.assertEqual("blocked", matrix.status)
        self.assertEqual("unresolved", matrix.row("backend").status)
        self.assertIn("数据来源边界", "\n".join(matrix.blockers))

    def test_explicit_frontend_no_change_keeps_backend_contract_single_layer(self) -> None:
        decision = technical_decision(
            contract_required=True,
            client_status="not_required",
            server_status="verified",
            server_evidence=True,
        )
        decision["selected_projects"] = [
            {
                "path": "/tmp/df-service-guahao",
                "name": "df-service-guahao",
                "role": "backend",
                "exists": True,
            }
        ]
        decision["field_provenance"] = {"target_ui_found": False, "evidence": []}

        matrix = build_change_ownership_matrix(
            user_instruction="本场景仅核验服务端查询参数 sortField，前端不需要修改。",
            requirement_text="查询接口使用 sortField。",
            technical_decision=decision,
        )

        self.assertEqual("ready", matrix.status)
        self.assertEqual("not_required", matrix.row("frontend").status)
        self.assertEqual("already_satisfied", matrix.row("backend").status)

    def test_frontend_change_and_backend_no_change_are_not_cross_clause_misread(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction="仅调整前端页面状态保持，不需要修改后端或数据库。",
            requirement_text="切换标签页不要刷新。",
            technical_decision=technical_decision(),
        )

        self.assertEqual("ready", matrix.status)
        self.assertEqual("required", matrix.row("frontend").status)
        self.assertEqual("not_required", matrix.row("backend").status)
        self.assertEqual("not_required", matrix.row("database").status)

    def test_explicit_no_database_field_change_does_not_invent_database_work(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction="只调整前端展示，不新增 BFF、后端或数据库字段。",
            requirement_text="展示接口已有备注字段。",
            technical_decision=technical_decision(),
        )

        self.assertEqual("not_required", matrix.row("database").status)

    def test_cross_layer_parameter_change_blocks_when_server_contract_is_unproven(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction="前端查询请求增加 sortField 入参。",
            requirement_text="需求评论：后端已经有人改好了，前端增加 sortField 入参即可。",
            technical_decision=technical_decision(
                contract_required=True,
                client_status="verified",
                server_status="missing",
            ),
        )

        self.assertEqual("blocked", matrix.status)
        self.assertEqual("required", matrix.row("frontend").status)
        self.assertEqual("unresolved", matrix.row("backend").status)
        self.assertIn("评论", "\n".join(matrix.blockers))

    def test_source_contract_proof_can_mark_backend_already_satisfied(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction="前端查询请求增加 sortField 入参。",
            requirement_text="需求评论：后端已经调整。",
            technical_decision=technical_decision(
                contract_required=True,
                client_status="verified",
                server_status="verified",
                server_evidence=True,
            ),
        )

        self.assertEqual("ready", matrix.status)
        self.assertEqual("already_satisfied", matrix.row("backend").status)
        self.assertEqual("source_contract", matrix.row("backend").evidence[0]["source_kind"])

    def test_explicit_user_confirmation_can_limit_change_to_frontend(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction="后端已经调整并验证通过，本次只改前端传入 sortField。",
            requirement_text="接口新增 sortField。",
            technical_decision=technical_decision(
                contract_required=True,
                client_status="verified",
                server_status="missing",
            ),
        )

        self.assertEqual("ready", matrix.status)
        self.assertEqual("already_satisfied", matrix.row("backend").status)
        self.assertEqual("user_confirmation", matrix.row("backend").evidence[0]["source_kind"])

    def test_frontend_only_behavior_does_not_invent_backend_or_database_work(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction="挂号病人查询切换标签页不要刷新，不需要查询数据库。",
            requirement_text="挂号病人查询切换标签页不要刷新。",
            technical_decision=technical_decision(),
        )

        self.assertEqual("ready", matrix.status)
        self.assertEqual("required", matrix.row("frontend").status)
        self.assertEqual("not_required", matrix.row("backend").status)
        self.assertEqual("not_required", matrix.row("database").status)

    def test_explicit_database_schema_change_is_unresolved_without_database_evidence(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction="数据库表结构增加顺序号字段并修改前端展示。",
            requirement_text="数据库表结构增加顺序号字段。",
            technical_decision=technical_decision(),
        )

        self.assertEqual("blocked", matrix.status)
        self.assertEqual("unresolved", matrix.row("database").status)

    def test_requirement_contract_blocks_unresolved_change_ownership(self) -> None:
        matrix = build_change_ownership_matrix(
            user_instruction="前端查询请求增加 sortField 入参。",
            requirement_text="评论说后端已改。",
            technical_decision=technical_decision(
                contract_required=True,
                client_status="verified",
                server_status="missing",
            ),
        )
        calibration = {
            "status": "ready_for_development",
            "decision": {"can_enter_development": True},
            "source_priority": [{"source": "user_instruction"}],
            "resolved_parameters": [
                {
                    "name": "sortField",
                    "allowed_values": {
                        "configured": "按指定字段排序",
                        "default": "未传时保持原顺序",
                    },
                }
            ],
            "warnings": [],
        }
        contract = build_requirement_contract(
            title="DFHIS-test",
            demand_text="前端查询请求增加 sortField 入参。",
            requirement_calibration=calibration,
            technical_decision=technical_decision(
                contract_required=True,
                client_status="verified",
                server_status="missing",
            ),
            acceptance_matrix={"items": [{"kind": "automatic", "statement": "前端传入 sortField"}]},
            apply_to_project=False,
            change_ownership_matrix=matrix.to_dict(),
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("变更归属", "\n".join(contract.blockers))


if __name__ == "__main__":
    unittest.main()
