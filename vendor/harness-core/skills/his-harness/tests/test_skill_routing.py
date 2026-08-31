from __future__ import annotations

import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = Path("/Users/lym/.codex/skills/his-harness")


class HisHarnessRoutingTests(unittest.TestCase):
    def test_routes_work_items_through_standalone_skills_and_history(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Mandatory Work Item Routing", text)
        self.assertIn("harness-workitem-intake", text)
        self.assertIn("yunxiao-workitem-evidence", text)
        self.assertIn("harness-history", text)
        self.assertIn("/Users/lym/WorkCode/ai/HarnessHistory", text)
        self.assertIn("--credential-kind write", text)
        self.assertIn("GET-only", text)
        self.assertIn("ready_for_analysis", text)
        self.assertIn("needs_requirement_confirmation", text)
        self.assertIn("exact relative-file allowlist", text)
        self.assertIn("Codex review", text)
        self.assertIn("every Yunxiao write", text)

    def test_runtime_skill_resolves_to_canonical_source(self):
        self.assertTrue(RUNTIME_DIR.is_symlink())
        self.assertEqual(SKILL_DIR, RUNTIME_DIR.resolve())
        self.assertTrue((RUNTIME_DIR / "SKILL.md").is_file())

    def test_default_route_keeps_requirement_worktrees_out_of_tmp(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        mandatory = text.split("## Mandatory Work Item Routing", 1)[1].split(
            "## Core Boundaries",
            1,
        )[0]
        self.assertIn("Do not start", mandatory)
        self.assertIn("`/tmp` or `/private/tmp`", mandatory)
        self.assertIn(
            "worktrees/<RUN-ID>/<PROJECT>",
            mandatory,
        )

    def test_legacy_auto_local_cannot_bypass_history_apply_back(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        legacy_loop = text.split(
            "Legacy low-risk local development loop",
            1,
        )[1].split("Generic precommit verification", 1)[0]
        self.assertIn("--execution-mode auto-local", legacy_loop)
        self.assertIn("--review-only", legacy_loop)
        self.assertIn("`harness-history archive-patch`", legacy_loop)
        self.assertIn("`record-review`", legacy_loop)
        self.assertIn("`record-verification`", legacy_loop)
        self.assertIn("`apply-back`", legacy_loop)
        self.assertNotIn(
            "applied to the original local repo by default",
            legacy_loop,
        )


if __name__ == "__main__":
    unittest.main()
