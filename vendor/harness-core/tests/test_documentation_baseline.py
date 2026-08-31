from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationBaselineTests(unittest.TestCase):
    def test_current_documents_point_to_single_version_source(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertIn("VERSION", readme)
        self.assertNotIn("--version 0.64.0", readme + changelog)

    def test_current_documents_distinguish_gate_and_business_status(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("完整离线门禁", readme)
        self.assertIn("business_valid=false", readme)
        self.assertIn("runtime_verified=false", readme)
        self.assertIn("promotion_enabled=false", readme)


if __name__ == "__main__":
    unittest.main()
