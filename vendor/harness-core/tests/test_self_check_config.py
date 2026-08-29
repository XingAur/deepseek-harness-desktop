from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.self_check import run_configuration_checks


class SelfCheckConfigurationTests(unittest.TestCase):
    def test_import_draft_directory_is_reused_by_follow_up_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checks = run_configuration_checks(output_dir=Path(temp_dir))

        statuses = {item["name"]: item["status"] for item in checks}
        self.assertEqual("pass", statuses["configuration_import_review_reads_back_drafts_and_shows_readonly_form_preview"])
        self.assertEqual("pass", statuses["configuration_template_index_compares_drafts_and_previews_profile_switches"])
        self.assertEqual("pass", statuses["configuration_wizard_combines_config_flow_into_readonly_guide"])


if __name__ == "__main__":
    unittest.main()
