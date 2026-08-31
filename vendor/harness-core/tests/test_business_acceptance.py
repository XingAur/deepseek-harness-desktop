from __future__ import annotations

import unittest

from app.business_acceptance import build_business_acceptance_status


class BusinessAcceptanceTests(unittest.TestCase):
    def test_checkbox_and_smoke_flags_cannot_bypass_evidence_contract(self) -> None:
        status = build_business_acceptance_status(
            {
                "environment": "his-test-a",
                "operator": "operator-a",
                "account_alias": "account-a",
                "test_data_alias": "case-a",
                "accepted": True,
                "runtime_verified": False,
                "checkbox": True,
                "smoke_passed": True,
                "scenarios": [],
            }
        )

        self.assertFalse(status["business_valid"])
        self.assertEqual("evidence_recorded", status["status"])

    def test_missing_business_evidence_remains_not_verified(self) -> None:
        status = build_business_acceptance_status(None)

        self.assertEqual("his-business-acceptance.v1", status["schema_version"])
        self.assertEqual("not_verified", status["status"])
        self.assertFalse(status["business_valid"])
        self.assertFalse(status["runtime_verified"])
        self.assertEqual("missing", status["prerequisites"]["his_test_environment"])

    def test_explicit_passed_runtime_evidence_can_be_recorded_as_accepted(self) -> None:
        status = build_business_acceptance_status(
            {
                "environment": "HIS-TEST",
                "accepted": True,
                "runtime_verified": True,
                "operator": "人工验收人",
                "account_alias": "HIS_TEST_CHARGE_USER",
                "test_data_alias": "OUTPATIENT_CHARGE_CASE_001",
                "scenarios": [
                    {
                        "name": "门诊收费保存",
                        "status": "passed",
                        "expected": "保存成功并生成收费记录",
                        "actual": "保存成功并生成收费记录",
                        "evidence": "截图和测试数据已归档",
                        "evidence_hashes": ["sha256:abc123"],
                    },
                ],
            }
        )

        self.assertEqual("accepted", status["status"])
        self.assertTrue(status["business_valid"])
        self.assertTrue(status["runtime_verified"])
        self.assertEqual("passed", status["prerequisites"]["test_account"])
        self.assertEqual("passed", status["prerequisites"]["test_data"])
        self.assertEqual("passed", status["prerequisites"]["manual_or_runtime_evidence"])

    def test_offline_technical_pass_does_not_become_business_valid(self) -> None:
        status = build_business_acceptance_status(
            {
                "environment": "offline-gate",
                "accepted": True,
                "runtime_verified": False,
                "operator": "enterprise_gate",
                "scenarios": [
                    {"name": "unit", "status": "passed", "evidence": "43 tests OK"},
                ],
            }
        )

        self.assertEqual("evidence_recorded", status["status"])
        self.assertFalse(status["business_valid"])
        self.assertFalse(status["runtime_verified"])

    def test_runtime_pass_without_account_and_test_data_remains_incomplete(self) -> None:
        status = build_business_acceptance_status(
            {
                "environment": "HIS-TEST",
                "accepted": True,
                "runtime_verified": True,
                "operator": "人工验收人",
                "scenarios": [
                    {
                        "name": "门诊收费保存",
                        "status": "passed",
                        "expected": "保存成功",
                        "actual": "保存成功",
                        "evidence": "截图已归档",
                    },
                ],
            }
        )

        self.assertEqual("evidence_recorded", status["status"])
        self.assertFalse(status["business_valid"])
        self.assertTrue(status["runtime_verified"])
        self.assertEqual("missing", status["prerequisites"]["test_account"])
        self.assertEqual("missing", status["prerequisites"]["test_data"])

    def test_runtime_pass_without_operator_alias_is_not_business_valid(self) -> None:
        status = build_business_acceptance_status(
            {
                "environment": "HIS-TEST",
                "accepted": True,
                "runtime_verified": True,
                "operator": "",
                "account_alias": "HIS_TEST_CHARGE_USER",
                "test_data_alias": "OUTPATIENT_CHARGE_CASE_001",
                "scenarios": [
                    {
                        "name": "门诊收费保存",
                        "status": "passed",
                        "expected": "保存成功",
                        "actual": "保存成功",
                        "evidence": "截图已归档",
                    }
                ],
            }
        )

        self.assertFalse(status["business_valid"])


if __name__ == "__main__":
    unittest.main()
