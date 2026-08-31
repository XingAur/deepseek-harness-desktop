from __future__ import annotations

import unittest

from app.demand_case import DemandCase, advance_demand_case


class DemandCaseTests(unittest.TestCase):
    def test_records_evidence_backed_stage_progression(self) -> None:
        case = DemandCase.create("显示病人备注")

        case = advance_demand_case(
            case,
            "intake",
            "completed",
            evidence_refs=["ticket:DFHIS-1"],
        )
        case = advance_demand_case(
            case,
            "discovery",
            "completed",
            evidence_refs=["df-web-test:src/view.vue"],
        )

        self.assertEqual("discovery", case.current_stage)
        self.assertEqual(
            ("df-web-test:src/view.vue",),
            case.stages["discovery"].evidence_refs,
        )
        self.assertEqual("completed", case.stages["discovery"].status)

    def test_cannot_skip_or_mutate_after_blocked_contract(self) -> None:
        case = DemandCase.create("高风险规则")
        case = advance_demand_case(case, "intake", "completed")
        case = advance_demand_case(case, "discovery", "completed")
        case = advance_demand_case(
            case,
            "contract",
            "blocked",
            failure_code="high_risk_ambiguity",
        )

        with self.assertRaisesRegex(ValueError, "cannot advance"):
            advance_demand_case(case, "local_change", "completed")
        with self.assertRaisesRegex(ValueError, "cannot advance"):
            advance_demand_case(DemandCase.create("跳过阶段"), "verification", "completed")


if __name__ == "__main__":
    unittest.main()
