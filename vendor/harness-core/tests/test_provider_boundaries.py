from __future__ import annotations

import unittest

from app.provider_capability_status import build_provider_capability_status


class ProviderBoundaryTests(unittest.TestCase):
    def test_github_without_canonical_manifest_is_explicitly_unsupported(self) -> None:
        result = build_provider_capability_status(
            [{"provider": "github", "profile_key": "github-main", "credential_ref": "github_pat_ref", "connection": {}}]
        )
        item = result["items"][0]
        self.assertEqual("unsupported", item["capability_state"])
        self.assertEqual("canonical_provider_manifest_unavailable", item["reason"])


if __name__ == "__main__":
    unittest.main()
