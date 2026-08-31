from __future__ import annotations

import unittest
from unittest.mock import patch

from app.providers.registry import build_manager_adapter_registry
from app.providers.git import GitProviderAdapter
from app.providers.github import GitHubProviderAdapter
from app.providers.gitlab import GitLabProviderAdapter
from app.providers.database_readonly import DatabaseReadonlyProviderAdapter
from app.providers.mcp_readonly import McpReadonlyProviderAdapter
from app.providers.model_smoke import ManagerModelSmokeProviderAdapter
from app.providers.yunxiao import YunxiaoProviderAdapter


class ProviderAdapterRegistryTests(unittest.TestCase):
    def test_registry_uses_mcp_for_external_reads_by_default(self) -> None:
        with patch(
            "app.yunxiao_read.load_yunxiao_credentials",
            side_effect=AssertionError("legacy credential loader must not be used"),
        ):
            registry = build_manager_adapter_registry()

        self.assertEqual(
            {"yunxiao", "git", "gitlab", "github", "database", "model"},
            set(registry),
        )
        self.assertIsInstance(registry["yunxiao"], McpReadonlyProviderAdapter)
        self.assertIsInstance(registry["git"], GitProviderAdapter)
        self.assertIsInstance(registry["gitlab"], McpReadonlyProviderAdapter)
        self.assertIsInstance(registry["github"], GitHubProviderAdapter)
        self.assertIsInstance(registry["database"], McpReadonlyProviderAdapter)
        self.assertIsInstance(registry["model"], ManagerModelSmokeProviderAdapter)

    def test_legacy_external_adapters_require_explicit_rollback_mode(self) -> None:
        registry = build_manager_adapter_registry(
            compatibility_mode="provider_rollback"
        )

        self.assertIsInstance(registry["yunxiao"], YunxiaoProviderAdapter)
        self.assertIsInstance(registry["gitlab"], GitLabProviderAdapter)
        self.assertIsInstance(registry["database"], DatabaseReadonlyProviderAdapter)

    def test_registry_rejects_legacy_provider_or_object_injection(self) -> None:
        with self.assertRaisesRegex(ValueError, "manager_provider_not_registered"):
            build_manager_adapter_registry(provider="legacy_yunxiao_read")
        with self.assertRaises(TypeError):
            build_manager_adapter_registry(adapter=object())  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
