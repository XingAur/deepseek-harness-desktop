from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.harness import RequirementWorkflowRunner, write_run_outputs
from app.llm_client import MockLLMClient


class DemandProgressIntegrationTests(unittest.TestCase):
    def test_readonly_run_persists_pre_and_post_change_business_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(database, "DB_PATH", Path(temp_dir) / "harness.sqlite"):
                result = RequirementWorkflowRunner(
                    MockLLMClient(),
                    allow_mock=True,
                ).run(
                    title="进度卡集成",
                    demand_text="只读分析页面字段展示逻辑",
                    project_root=Path(temp_dir) / "projects",
                    execution_mode="readonly",
                    requirement_governance="observe",
                )
                output_dir = Path(temp_dir) / "output"
                write_run_outputs(result.run_id, output_dir)
                run_output_dir = output_dir / f"run_{result.run_id}"
                artifacts = {
                    item["kind"]: item["content"]
                    for item in database.get_artifacts(result.run_id)
                }
                self.assertTrue((run_output_dir / "demand_progress_pre_change.json").is_file())
                self.assertTrue((run_output_dir / "demand_progress_post_change.md").is_file())

        self.assertIn("demand_progress_pre_change_json", artifacts)
        self.assertIn("demand_progress_post_change_json", artifacts)
        pre = json.loads(artifacts["demand_progress_pre_change_json"])
        post = json.loads(artifacts["demand_progress_post_change_json"])
        self.assertEqual("pre_change", pre["phase"])
        self.assertEqual("post_change", post["phase"])
        self.assertIn("改动前确认", artifacts["demand_progress_pre_change_markdown"])
        self.assertIn("改动后业务确认", artifacts["demand_progress_post_change_markdown"])
        self.assertFalse(pre["confirmation"]["can_modify"])


if __name__ == "__main__":
    unittest.main()
