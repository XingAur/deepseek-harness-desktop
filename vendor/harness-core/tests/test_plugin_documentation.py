from __future__ import annotations

import unittest
from pathlib import Path

from app.plugin_inventory import resolve_plugin_source_root


HARNESS_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = HARNESS_ROOT.parent
PLUGIN_SOURCE_ROOT = resolve_plugin_source_root(
    REPOSITORY_ROOT,
    Path("/Users/lym/plugins"),
)
PLUGIN_NAMES = (
    "his-harness-core",
    "yunxiao",
    "his-engineering",
    "his-knowledge",
)
COMPATIBILITY_SKILLS = (
    "his-harness",
    "harness-workitem-intake",
    "harness-history",
    "yunxiao-workitem-evidence",
)


class PluginDocumentationTests(unittest.TestCase):
    def test_harness_readme_documents_governed_plugin_runtime(self) -> None:
        readme = (HARNESS_ROOT / "README.md").read_text(encoding="utf-8")

        for required in (
            "## v0.66 插件能力治理",
            "`question`",
            "`task`",
            "`his-harness-core`",
            "`yunxiao`",
            "`his-engineering`",
            "`his-knowledge`",
            "`L0`",
            "`L1`",
            "`L2`",
            "`L3`",
            "`L4`",
            "`L5`",
            "routing_mode",
            "四个正式插件已经安装",
            "/Users/lym/WorkCode/ai/his-knowledge",
            "/Users/lym/WorkCode/ai/his-knowledge/vault",
            "set -eu",
            "python3 -m unittest tests.test_plugin_inventory tests.test_plugin_documentation",
            "python3 tools/plugin_replay_suite.py",
            "business_valid=false",
            "runtime_verified=false",
            "以 v0.66 capability manifest 和本节边界为准",
        ):
            with self.subTest(required=required):
                self.assertIn(required, readme)
        self.assertNotIn("四个正式插件尚未安装", readme)

    def test_skills_readme_marks_legacy_entries_and_deletion_gate(self) -> None:
        readme = (HARNESS_ROOT / "skills" / "README.md").read_text(
            encoding="utf-8"
        )

        for skill in COMPATIBILITY_SKILLS:
            with self.subTest(skill=skill):
                self.assertIn(f"`{skill}`", readme)
        for required in (
            "`compatibility`",
            "下一版本",
            "使用证据",
            "用户确认",
            "不得删除",
        ):
            with self.subTest(required=required):
                self.assertIn(required, readme)

    def test_plugin_skill_indexes_mark_canonical_boundaries(self) -> None:
        for plugin_name in PLUGIN_NAMES:
            with self.subTest(plugin_name=plugin_name):
                skill_index = (
                    PLUGIN_SOURCE_ROOT
                    / plugin_name
                    / "skills"
                    / "README.md"
                )
                content = skill_index.read_text(encoding="utf-8")
                self.assertIn("`canonical`", content)
                self.assertIn(plugin_name, content)

    def test_legacy_skill_files_remain_compatibility_only(self) -> None:
        for skill in COMPATIBILITY_SKILLS:
            with self.subTest(skill=skill):
                content = (
                    HARNESS_ROOT / "skills" / skill / "SKILL.md"
                ).read_text(encoding="utf-8")
                self.assertIn("compatibility", content.lower())
                self.assertNotIn("status: deleted", content.lower())


if __name__ == "__main__":
    unittest.main()
