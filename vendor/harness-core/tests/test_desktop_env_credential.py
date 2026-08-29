from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class DesktopEnvCredentialTests(unittest.TestCase):
    def test_maps_only_the_declared_provider_fields(self) -> None:
        from app.provider_execution import _desktop_env_credential

        with patch.dict(
            os.environ,
            {"DSH_GITLAB_TOKEN": "gl-token", "ALIYUN_DEVOPS_PAT": "yx-pat"},
        ):
            self.assertEqual(_desktop_env_credential("gitlab", "access_token"), "gl-token")
            self.assertEqual(_desktop_env_credential("yunxiao", "pat"), "yx-pat")
            # 未声明组合不取值，避免凭证串用。
            self.assertEqual(_desktop_env_credential("gitlab", "pat"), "")
            self.assertEqual(_desktop_env_credential("database_readonly", "password"), "")
            self.assertEqual(_desktop_env_credential("gitlab", "access_token_extra"), "")


if __name__ == "__main__":
    unittest.main()
