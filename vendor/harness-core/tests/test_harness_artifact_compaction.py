from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.harness import (
    build_engineering_evidence_section,
    build_json_payload,
    build_step_manifest_entry,
    write_run_outputs,
)
from tools.self_check import first_artifact_json


class HarnessArtifactCompactionTests(unittest.TestCase):

    def test_run_json_contains_artifact_manifest_instead_of_duplicate_content(self) -> None:
        content = "x" * 50_000
        artifacts = [
            {
                "id": 7,
                "run_id": 1,
                "kind": "technical_decision_json",
                "title": "技术决策",
                "content": content,
                "created_at": "2026-08-23T00:00:00",
            }
        ]
        with (
            patch("app.harness.database.get_run", return_value={"id": 1}),
            patch("app.harness.database.get_latest_step_runs", return_value=[]),
            patch("app.harness.database.get_step_runs", return_value=[]),
            patch("app.harness.database.get_artifacts", return_value=artifacts),
        ):
            payload = json.loads(build_json_payload(1))

        artifact = payload["artifacts"][0]
        self.assertNotIn("content", artifact)
        self.assertEqual(50_000, artifact["content_size_bytes"])
        self.assertEqual(hashlib.sha256(content.encode("utf-8")).hexdigest(), artifact["content_sha256"])
        self.assertEqual("technical_decision.json", artifact["output_name"])
        self.assertLess(len(json.dumps(payload)), 5_000)

    def test_export_keeps_full_artifact_in_own_file_only(self) -> None:
        content = '{"large":"' + ("x" * 50_000) + '"}'
        artifacts = [
            {
                "id": 7,
                "run_id": 1,
                "kind": "technical_decision_json",
                "title": "技术决策",
                "content": content,
                "created_at": "2026-08-23T00:00:00",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("app.harness.build_markdown_report", return_value="# report"), \
             patch("app.harness.database.get_run", return_value={"id": 1}), \
             patch("app.harness.database.get_latest_step_runs", return_value=[]), \
             patch("app.harness.database.get_step_runs", return_value=[]), \
             patch("app.harness.database.get_artifacts", return_value=artifacts):
            output = write_run_outputs(1, temp_dir)
            run_payload = json.loads((output / "run.json").read_text(encoding="utf-8"))
            artifact_payload = (output / "technical_decision.json").read_text(encoding="utf-8")

        self.assertNotIn("content", run_payload["artifacts"][0])
        self.assertEqual(content, artifact_payload)

    def test_export_names_requirement_understanding_artifacts_stably(self) -> None:
        artifacts = [
            {
                "id": 8,
                "run_id": 1,
                "kind": "requirement_understanding_json",
                "title": "理解证据包",
                "content": '{"status":"blocked_needs_project_discovery"}',
                "created_at": "2026-08-27T00:00:00",
            },
            {
                "id": 9,
                "run_id": 1,
                "kind": "requirement_understanding_markdown",
                "title": "理解证据包",
                "content": "## 改码前理解证据包",
                "created_at": "2026-08-27T00:00:00",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("app.harness.build_markdown_report", return_value="# report"), \
             patch("app.harness.database.get_run", return_value={"id": 1}), \
             patch("app.harness.database.get_latest_step_runs", return_value=[]), \
             patch("app.harness.database.get_step_runs", return_value=[]), \
             patch("app.harness.database.get_artifacts", return_value=artifacts):
            output = write_run_outputs(1, temp_dir)
            self.assertTrue((output / "requirement_understanding.json").is_file())
            self.assertTrue((output / "requirement_understanding.md").is_file())

    def test_export_names_error_chain_closure_artifacts_stably(self) -> None:
        artifacts = [
            {"id": 10, "run_id": 1, "kind": "error_chain_closure_json", "title": "链路闭环", "content": '{"status":"blocked"}', "created_at": "2026-08-27T00:00:00"},
            {"id": 11, "run_id": 1, "kind": "error_chain_closure_markdown", "title": "链路闭环", "content": "## 截图错误链路闭环门禁", "created_at": "2026-08-27T00:00:00"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("app.harness.build_markdown_report", return_value="# report"), \
             patch("app.harness.database.get_run", return_value={"id": 1}), \
             patch("app.harness.database.get_latest_step_runs", return_value=[]), \
             patch("app.harness.database.get_step_runs", return_value=[]), \
             patch("app.harness.database.get_artifacts", return_value=artifacts):
            output = write_run_outputs(1, temp_dir)
            self.assertTrue((output / "error_chain_closure.json").is_file())
            self.assertTrue((output / "error_chain_closure.md").is_file())

    def test_self_check_reads_compacted_json_artifact_from_exported_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            artifact_path = output_dir / "requirement_calibration.json"
            artifact_path.write_text('{"version":"0.15-requirement-calibration"}', encoding="utf-8")
            payload = {
                "artifacts": [
                    {
                        "kind": "requirement_calibration_json",
                        "output_name": artifact_path.name,
                        "content_storage": "separate_artifact_file_and_database",
                    }
                ]
            }

            artifact = first_artifact_json(
                payload,
                "requirement_calibration_json",
                output_dir=output_dir,
            )

        self.assertEqual("0.15-requirement-calibration", artifact["version"])

    def test_run_json_compacts_step_outputs_to_content_addressed_manifests(self) -> None:
        output = "broad model output\n" * 20_000
        step = {
            "id": 11,
            "run_id": 1,
            "step_order": 2,
            "attempt_round": 0,
            "step_name": "工程分析",
            "expert_name": "developer",
            "status": "success",
            "duration_ms": 1200,
            "prompt_tokens": 20,
            "completion_tokens": 30,
            "error": "",
            "output_text": output,
        }
        with (
            patch("app.harness.database.get_run", return_value={"id": 1}),
            patch("app.harness.database.get_latest_step_runs", return_value=[step]),
            patch("app.harness.database.get_step_runs", return_value=[step]),
            patch("app.harness.database.get_artifacts", return_value=[]),
        ):
            payload = json.loads(build_json_payload(1))

        manifest = payload["latest_steps"][0]
        self.assertNotIn("output_text", manifest)
        self.assertEqual(len(output.encode("utf-8")), manifest["output_size_bytes"])
        self.assertEqual(
            hashlib.sha256(output.encode("utf-8")).hexdigest(),
            manifest["output_sha256"],
        )
        self.assertLess(len(json.dumps(payload)), 5_000)

    def test_authoritative_report_does_not_embed_broad_generic_evidence(self) -> None:
        section = build_engineering_evidence_section(
            evidence_markdown="无关退费、结算和历史接口的通用扫描结果",
            technical_decision_markdown="已收敛到当前排班 V2 接口",
        )

        self.assertIn("独立 evidence.md", "\n".join(section))
        self.assertNotIn("无关退费", "\n".join(section))

    def test_step_manifest_is_stable_for_empty_output(self) -> None:
        manifest = build_step_manifest_entry(
            {"id": 3, "step_order": 1, "attempt_round": 0, "output_text": "", "error": ""}
        )

        self.assertNotIn("output_text", manifest)
        self.assertEqual(0, manifest["output_size_bytes"])

    def test_export_keeps_full_step_output_in_a_separate_auditable_file(self) -> None:
        output = "完整步骤证据\n" * 5_000
        step = {
            "id": 11,
            "run_id": 1,
            "step_order": 2,
            "attempt_round": 1,
            "step_name": "工程分析",
            "expert_name": "developer",
            "status": "success",
            "duration_ms": 1200,
            "prompt_tokens": 20,
            "completion_tokens": 30,
            "error": "",
            "output_text": output,
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("app.harness.build_markdown_report", return_value="# report"), \
             patch("app.harness.database.get_run", return_value={"id": 1}), \
             patch("app.harness.database.get_latest_step_runs", return_value=[step]), \
             patch("app.harness.database.get_step_runs", return_value=[step]), \
             patch("app.harness.database.get_artifacts", return_value=[]):
            output_dir = write_run_outputs(1, temp_dir)
            run_payload = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
            relative_name = run_payload["latest_steps"][0]["output_name"]
            exported = (output_dir / relative_name).read_text(encoding="utf-8")

        self.assertEqual(output, exported)
        self.assertEqual(
            hashlib.sha256(exported.encode("utf-8")).hexdigest(),
            run_payload["latest_steps"][0]["output_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
