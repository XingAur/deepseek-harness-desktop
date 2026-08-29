from __future__ import annotations

import unittest

from tools.self_check import run_acceptance_contract_checks


class SelfCheckAcceptanceContractTests(unittest.TestCase):
    def test_self_check_covers_ordering_relation_contract(self) -> None:
        checks = run_acceptance_contract_checks()

        self.assertTrue(checks)
        self.assertTrue(all(item["status"] == "pass" for item in checks))
        self.assertIn(
            "acceptance_contract_dfhis_31558_tie_parent_and_unsorted_order",
            {item["name"] for item in checks},
        )
        self.assertIn(
            "acceptance_contract_blocks_missing_required_policy",
            {item["name"] for item in checks},
        )


if __name__ == "__main__":
    unittest.main()
