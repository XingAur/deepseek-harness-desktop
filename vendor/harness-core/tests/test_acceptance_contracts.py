from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.acceptance_contracts import execute_acceptance_contract, ordering_contract_required


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "acceptance_contracts" / "dfhis-31558-ordering.json"


class AcceptanceContractExecutionTests(unittest.TestCase):
    def test_ordering_contract_uses_source_index_for_ties_and_parent_descendants(self) -> None:
        result = execute_acceptance_contract(FIXTURE_PATH)

        self.assertEqual("pass", result.status)
        self.assertEqual(("31", "174", "25162", "85", "26429", "999", "998"), result.source_order)
        self.assertEqual(result.source_order, result.target_leaf_order)
        self.assertEqual("pass", result.checks["same_sequence_uses_source_index"])
        self.assertEqual("pass", result.checks["parent_uses_earliest_descendant"])
        self.assertEqual("pass", result.checks["unsorted_preserves_relative_order"])

    def test_contract_blocks_when_required_ordering_policies_are_missing(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        del payload["source"]["order_keys"]
        del payload["source"]["unsorted_behavior"]
        del payload["target"]["parent_order"]

        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "invalid.json"
            contract_path.write_text(json.dumps(payload), encoding="utf-8")
            result = execute_acceptance_contract(contract_path)

        self.assertEqual("blocked", result.status)
        blockers = "\n".join(result.blockers)
        self.assertIn("source.order_keys", blockers)
        self.assertIn("source.unsorted_behavior", blockers)
        self.assertIn("target.parent_order", blockers)

    def test_contract_blocks_when_fixture_collection_names_are_invalid(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["source"]["collection"] = "rows"
        payload["target"]["collection"] = "tree"

        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "invalid-collections.json"
            contract_path.write_text(json.dumps(payload), encoding="utf-8")
            result = execute_acceptance_contract(contract_path)

        self.assertEqual("blocked", result.status)
        blockers = "\n".join(result.blockers)
        self.assertIn("source.collection", blockers)
        self.assertIn("target.collection", blockers)

    def test_contract_blocks_when_fixture_lacks_same_sequence_scenario(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["fixture"]["schedule_rows"][1]["shunXuHao"] = 3

        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "missing-tie.json"
            contract_path.write_text(json.dumps(payload), encoding="utf-8")
            result = execute_acceptance_contract(contract_path)

        self.assertEqual("blocked", result.status)
        self.assertEqual("blocked", result.checks["same_sequence_uses_source_index"])
        self.assertIn("同顺序号", "\n".join(result.blockers))

    def test_contract_blocks_when_fixture_lacks_scheme_parent_scenario(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["fixture"]["department_tree"]["children"] = [
            {"keShiId": row["keShiId"], "keShiMc": row["keShiMc"]}
            for row in payload["fixture"]["schedule_rows"]
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "missing-scheme-parent.json"
            contract_path.write_text(json.dumps(payload), encoding="utf-8")
            result = execute_acceptance_contract(contract_path)

        self.assertEqual("blocked", result.status)
        self.assertEqual("blocked", result.checks["parent_uses_earliest_descendant"])
        self.assertIn("方案父节点", "\n".join(result.blockers))

    def test_contract_supports_configured_leaf_key(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["source"]["department_key"] = "departmentId"
        payload["target"]["leaf_key"] = "departmentId"
        for row in payload["fixture"]["schedule_rows"]:
            row["departmentId"] = row.pop("keShiId")

        def rename_tree_key(node: dict[str, object]) -> None:
            if "keShiId" in node:
                node["departmentId"] = node.pop("keShiId")
            for child in node.get("children", []):
                rename_tree_key(child)

        rename_tree_key(payload["fixture"]["department_tree"])
        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "configured-leaf-key.json"
            contract_path.write_text(json.dumps(payload), encoding="utf-8")
            result = execute_acceptance_contract(contract_path)

        self.assertEqual("pass", result.status)

    def test_contract_blocks_when_tree_leaf_order_differs_from_source_order(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["fixture"]["department_tree"]["children"].pop(3)

        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "different-order.json"
            contract_path.write_text(json.dumps(payload), encoding="utf-8")
            result = execute_acceptance_contract(contract_path)

        self.assertEqual("blocked", result.status)
        self.assertIn("第 3 项", "\n".join(result.blockers))

    def test_contract_deduplicates_schedule_departments_by_first_sorted_occurrence(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["fixture"]["schedule_rows"].append(
            {"keShiId": "174", "keShiMc": "高血压病科门诊", "shunXuHao": 1, "sourceIndex": 7}
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "duplicate-department.json"
            contract_path.write_text(json.dumps(payload), encoding="utf-8")
            result = execute_acceptance_contract(contract_path)

        self.assertEqual("pass", result.status)
        self.assertEqual(1, result.source_order.count("174"))
        self.assertEqual(("31", "174"), result.source_order[:2])

    def test_only_sorting_tree_requirements_require_contract(self) -> None:
        self.assertTrue(ordering_contract_required(title="DFHIS-31558", demand_text="科室树和右侧排班按顺序号排序并保持一致。"))
        self.assertFalse(ordering_contract_required(title="普通展示", demand_text="挂号管理页面按顺序号展示科室树，列表中显示科室名称。"))
        self.assertFalse(ordering_contract_required(title="DFHIS-31557", demand_text="挂号处理界面证件类型默认身份证。"))


if __name__ == "__main__":
    unittest.main()
