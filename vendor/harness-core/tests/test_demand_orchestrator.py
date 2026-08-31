from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace

from app.demand_discovery import discover_demand
from app.demand_orchestrator import (
    build_governed_demand_case,
    build_governed_demand_case_from_intake_file,
    write_demand_case_snapshot,
)


class DemandOrchestratorTests(unittest.TestCase):
    def test_readonly_intake_reaches_discovery_then_blocks_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "df-web-patient"
            page = project / "src/pages/query/index.vue"
            page.parent.mkdir(parents=True)
            page.write_text(
                '<df-select label="就诊状态" v-model="query.visitState" />',
                encoding="utf-8",
            )
            discovery = discover_demand(
                demand_text="病人列表增加就诊状态筛选",
                selected_projects=[
                    {"name": project.name, "path": str(project), "role": "frontend"}
                ],
            )
            result = build_governed_demand_case(
                demand_text="病人列表增加就诊状态筛选",
                intake_record={
                    "source": "DFHIS-90002",
                    "intake_status": "accepted_for_readonly_discovery",
                    "readonly_discovery_allowed": True,
                    "mutation_allowed": False,
                },
                discovery=discovery,
                technical_decision={"can_patch": False},
                governance=SimpleNamespace(
                    status="review_only",
                    can_modify=False,
                ),
            )

        self.assertEqual("blocked", result.case.stages["contract"].status)
        self.assertEqual("review_only", result.case.stages["contract"].failure_code)
        self.assertFalse(result.mutation_allowed)
        self.assertEqual("contract", result.case.current_stage)

    def test_only_ready_contract_allows_mutation_after_evidence(self) -> None:
        discovery = discover_demand(
            demand_text="显示患者列表",
            selected_projects=[],
        )
        result = build_governed_demand_case(
            demand_text="显示患者列表",
            intake_record={
                "source": "DFHIS-90003",
                "intake_status": "accepted",
                "readonly_discovery_allowed": True,
                "mutation_allowed": True,
            },
            discovery=discovery,
            technical_decision={"can_patch": True},
            governance=SimpleNamespace(
                status="ready_for_local_change",
                can_modify=True,
            ),
        )

        self.assertEqual("completed", result.case.stages["contract"].status)
        self.assertTrue(result.mutation_allowed)

    def test_intake_file_produces_sanitized_immutable_case_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            intake_path = root / "intake" / "request.json"
            intake_path.parent.mkdir()
            intake_path.write_text(
                json.dumps(
                    {
                        "source": "DFHIS-90004?token=must-not-persist",
                        "intake_status": "accepted_for_readonly_discovery",
                        "readonly_discovery_allowed": True,
                        "mutation_allowed": False,
                    }
                ),
                encoding="utf-8",
            )
            result = build_governed_demand_case_from_intake_file(
                demand_text="查询页面增加筛选",
                intake_path=intake_path,
                discovery=discover_demand(
                    demand_text="查询页面增加筛选",
                    selected_projects=[],
                ),
                technical_decision={"can_patch": False},
                governance={"status": "review_only", "can_modify": False},
            )
            snapshot = write_demand_case_snapshot(run_dir=root, result=result)

            payload = json.loads(snapshot.read_text(encoding="utf-8"))

        self.assertEqual(root / "demand_case.json", snapshot)
        self.assertNotIn("must-not-persist", json.dumps(payload))
        self.assertFalse(payload["mutation_allowed"])
        self.assertEqual("contract", payload["case"]["current_stage"])


if __name__ == "__main__":
    unittest.main()
