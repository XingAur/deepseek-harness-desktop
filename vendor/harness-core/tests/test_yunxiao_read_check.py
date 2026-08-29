from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import yunxiao_read_check


class YunxiaoReadCheckTests(unittest.TestCase):
    def test_partial_inline_media_is_a_successful_smoke_with_warning(self) -> None:
        class Credentials:
            ok = True
            missing_keys: list[str] = []

            def safe_summary(self) -> dict[str, str]:
                return {"pat": "present"}

        evidence = {
            "status": "partial",
            "yunxiao_url": "https://devops.aliyun.com/projex/req/DFHIS-1",
            "work_item_id": "DFHIS-1",
            "work_item": {"title": "正文可读"},
            "attachments": [],
            "request_attempts": [],
            "warnings": ["inline_image_detail_failed"],
            "decision_gate": {"state": "ready_for_analysis", "reason": "图片失效不阻断分析"},
        }
        with patch.object(yunxiao_read_check, "load_yunxiao_credentials", return_value=Credentials()), patch.object(
            yunxiao_read_check, "load_yunxiao_write_credentials", return_value=Credentials()
        ), patch.object(yunxiao_read_check, "credentials_file_permission_issue", return_value=""), patch.object(
            yunxiao_read_check, "collect_yunxiao_evidence", return_value=evidence
        ):
            result = yunxiao_read_check.run_check(
                urls=["https://devops.aliyun.com/projex/req/DFHIS-1"],
                output_dir=None,
            )

        self.assertEqual("passed_with_warnings", result["status"])
        self.assertIn("已按警告继续", result["summary"])
        self.assertEqual("ready_for_analysis", result["items"][0]["analysis_gate"])
        self.assertEqual(["inline_image_detail_failed"], result["items"][0]["warnings"])

    def test_source_read_failure_remains_failed(self) -> None:
        class Credentials:
            ok = True
            missing_keys: list[str] = []

            def safe_summary(self) -> dict[str, str]:
                return {"pat": "present"}

        evidence = {
            "status": "failed",
            "yunxiao_url": "https://devops.aliyun.com/projex/req/DFHIS-1",
            "work_item_id": "DFHIS-1",
            "work_item": {},
            "attachments": [],
            "request_attempts": [],
        }
        with patch.object(yunxiao_read_check, "load_yunxiao_credentials", return_value=Credentials()), patch.object(
            yunxiao_read_check, "load_yunxiao_write_credentials", return_value=Credentials()
        ), patch.object(yunxiao_read_check, "credentials_file_permission_issue", return_value=""), patch.object(
            yunxiao_read_check, "collect_yunxiao_evidence", return_value=evidence
        ):
            result = yunxiao_read_check.run_check(
                urls=["https://devops.aliyun.com/projex/req/DFHIS-1"],
                output_dir=None,
            )

        self.assertEqual("failed", result["status"])


if __name__ == "__main__":
    unittest.main()
