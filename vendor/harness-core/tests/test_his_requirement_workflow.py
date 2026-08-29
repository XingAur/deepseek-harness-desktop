from __future__ import annotations

import unittest
from unittest.mock import patch

from harnesses.his_requirement_workflow import (
    _REQUIREMENT_UNDERSTANDING_ARTIFACT_KINDS,
    _artifact_content,
)


class HisRequirementWorkflowTests(unittest.TestCase):
    def test_archive_prefers_current_understanding_artifact_over_legacy_calibration(self) -> None:
        self.assertEqual(
            "requirement_understanding_markdown",
            _REQUIREMENT_UNDERSTANDING_ARTIFACT_KINDS[0],
        )
        with patch(
            "harnesses.his_requirement_workflow.database.get_artifacts",
            return_value=[
                {"kind": "requirement_calibration_markdown", "content": "旧版理解"},
                {"kind": "requirement_understanding_markdown", "content": "新版理解"},
            ],
        ):
            self.assertEqual(
                "新版理解",
                _artifact_content(1, *_REQUIREMENT_UNDERSTANDING_ARTIFACT_KINDS),
            )


if __name__ == "__main__":
    unittest.main()
