from __future__ import annotations

import unittest

from app.clarification_gate import classify_harness_interaction, evaluate_patch_readiness


class ClarificationGateTests(unittest.TestCase):
    def test_internal_harness_failures_are_recoverable_without_asking_user(self) -> None:
        self.assertEqual("internal_recoverable", classify_harness_interaction(error_code="archive_media_download_failed"))
        self.assertEqual("internal_recoverable", classify_harness_interaction(error_code="mcp_probe_timeout"))
        self.assertEqual("internal_recoverable", classify_harness_interaction(error_code="verification_command_failed"))

    def test_only_business_ambiguity_or_external_authorization_reaches_user(self) -> None:
        self.assertEqual("business_clarification", classify_harness_interaction(ambiguity_kind="business_choice"))
        self.assertEqual("external_authorization", classify_harness_interaction(ambiguity_kind="external_write"))

    def test_readable_partial_yunxiao_evidence_does_not_block_patch_readiness(self) -> None:
        result = evaluate_patch_readiness(
            demand_text="新增医保审批项目维护页面。",
            yunxiao_evidence={
                "status": "partial",
                "title": "优化医保审批项目维护功能",
                "description_text": "主需求正文可用，部分正文图片已失效。",
            },
            requirement_evidence=None,
            evidence_bundle={"evidence_files": []},
            allowed_paths=["src/views/yiBaoMlDz/index.vue"],
            verify_commands=["npm run lint"],
            yunxiao_read_requested=True,
        )

        self.assertTrue(result.can_patch)
        self.assertEqual("ready", result.status)
        self.assertTrue(any("部分" in item for item in result.confirmed_facts))

    def test_local_normalized_requirement_evidence_satisfies_readonly_source_gate(self) -> None:
        result = evaluate_patch_readiness(
            demand_text="",
            yunxiao_evidence=None,
            requirement_evidence={
                "readonly": True,
                "title": "优化医保审批项目维护功能",
                "description_text": "医保审批维护和医保对照做在一个页面。",
            },
            evidence_bundle={"evidence_files": []},
            allowed_paths=["src/views/yiBaoMlDz/index.vue"],
            verify_commands=["python3 -m unittest"],
            yunxiao_read_requested=False,
        )

        self.assertTrue(result.can_patch)
        self.assertEqual("ready", result.status)


if __name__ == "__main__":
    unittest.main()
