from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityContractError,
    CapabilityRequest,
    CapabilityResult,
    MutationLevel,
)
from app.capability_permissions import PermissionDecision
from app.capability_registry import CapabilityDescriptor, CapabilityRegistry
from app.capability_runtime import (
    CapabilityExecution,
    CapabilityPreflight,
    CapabilityRuntime,
)
from app.capability_service import (
    CapabilityService,
    LegacyReadFallbackPolicy,
)


def make_request(
    *,
    capability: str = "workitem.read",
    provider: str = "yunxiao",
    mode: str = "preview",
    mutation_level: MutationLevel = MutationLevel.L1,
) -> CapabilityRequest:
    return CapabilityRequest(
        request_id="route-1",
        capability=capability,
        provider=provider,
        mode=mode,
        mutation_level=mutation_level,
        authorization=CapabilityAuthorization(explicit=False, scope=()),
        input={},
        context={},
    )


def make_result(
    request: CapabilityRequest,
    *,
    status: str = "success",
    data: dict | None = None,
) -> CapabilityResult:
    return CapabilityResult(
        request_id=request.request_id,
        capability=request.capability,
        provider=request.provider,
        status=status,
        mutation_level=request.mutation_level,
        changed=False,
        summary="fixture",
        data={} if data is None else data,
        evidence=(),
        warnings=(),
        blockers=(),
        audit={},
    )


class FakeRuntime:
    def __init__(self, result: CapabilityResult | Exception) -> None:
        self.result = result
        self.calls: list[CapabilityRequest] = []
        self.environments: list[dict[str, str] | None] = []
        self.preflight_calls: list[CapabilityRequest] = []

    @staticmethod
    def _descriptor(request: CapabilityRequest) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            plugin="fixture",
            plugin_version="1.0.0",
            name=request.capability,
            provider=request.provider,
            contract_version="fixture.v1",
            mutation_level=request.mutation_level,
            credential_class="none",
            entrypoint=None,
            enabled=True,
            disabled_reason="",
            scopes=(),
        )

    @staticmethod
    def _permission(request: CapabilityRequest) -> PermissionDecision:
        return PermissionDecision(
            status="allowed",
            allowed=True,
            required_level=request.mutation_level,
            blockers=(),
        )

    def preflight(self, request: CapabilityRequest) -> object:
        self.preflight_calls.append(request)
        return CapabilityPreflight(
            descriptor=self._descriptor(request),
            permission=self._permission(request),
        )

    def execute(
        self,
        request: CapabilityRequest,
        *,
        environment: dict[str, str] | None = None,
    ) -> CapabilityExecution:
        self.calls.append(request)
        self.environments.append(environment)
        if isinstance(self.result, Exception):
            raise self.result
        return CapabilityExecution(
            descriptor=self._descriptor(request),
            permission=self._permission(request),
            result=self.result,
            duration_ms=1,
        )


class CapabilityServiceTests(unittest.TestCase):
    def test_capability_specific_environment_is_not_shared_with_other_providers(
        self,
    ) -> None:
        knowledge_request = make_request(
            capability="knowledge.answer",
            provider="his-knowledge",
            mutation_level=MutationLevel.L0,
        )
        runtime = FakeRuntime(make_result(knowledge_request))
        service = CapabilityService(
            runtime,
            routing_mode="enforce",
            capability_environments={
                ("knowledge.answer", "his-knowledge"): {
                    "HIS_KNOWLEDGE_HOME": "/tmp/knowledge"
                }
            },
        )

        service.route(knowledge_request)
        unrelated_request = make_request()
        runtime.result = make_result(unrelated_request)
        service.route(unrelated_request)

        self.assertEqual(
            [{"HIS_KNOWLEDGE_HOME": "/tmp/knowledge"}, None],
            runtime.environments,
        )

    def test_global_and_capability_specific_environments_are_mutually_exclusive(
        self,
    ) -> None:
        request = make_request()
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            CapabilityService(
                FakeRuntime(make_result(request)),
                routing_mode="enforce",
                runtime_environment={"A": "1"},
                capability_environments={
                    ("workitem.read", "yunxiao"): {"B": "2"}
                },
            )

    def test_legacy_calls_only_legacy_adapter(self) -> None:
        request = make_request()
        runtime = FakeRuntime(make_result(request))
        legacy_calls: list[str] = []
        legacy = {"status": "success", "data": {"title": "legacy"}}

        result = CapabilityService(runtime, routing_mode="legacy").route(
            request,
            legacy_callable=lambda: legacy_calls.append("legacy") or legacy,
        )

        self.assertEqual([], runtime.calls)
        self.assertEqual([], runtime.preflight_calls)
        self.assertEqual(["legacy"], legacy_calls)
        self.assertEqual("legacy", result.selected)
        self.assertEqual(legacy, result.result)
        self.assertFalse(result.fallback_used)

    def test_observe_calls_both_paths_returns_legacy_and_compares_without_values(self) -> None:
        secret = "SENTINEL_SECRET_VALUE"
        request = make_request()
        runtime = FakeRuntime(
            make_result(
                request,
                data={"title": "same", "body": "new", "token": secret},
            )
        )
        legacy_calls: list[str] = []
        legacy = {
            "status": "success",
            "data": {"title": "same", "body": "old", "token": "legacy-" + secret},
        }

        result = CapabilityService(runtime, routing_mode="observe").route(
            request,
            legacy_callable=lambda: legacy_calls.append("legacy") or legacy,
            equivalence_fields=("data.title", "data.body", "data.token"),
        )

        self.assertEqual([request], runtime.calls)
        self.assertEqual([request], runtime.preflight_calls)
        self.assertEqual(["legacy"], legacy_calls)
        self.assertEqual("legacy", result.selected)
        self.assertEqual(legacy, result.result)
        self.assertEqual(
            {
                "status": "different",
                "fields": {
                    "status": {"equal": True},
                    "data.title": {"equal": True},
                    "data.body": {"equal": False},
                },
                "redacted_field_count": 1,
            },
            result.comparison,
        )
        self.assertNotIn(secret, json.dumps(result.comparison))
        self.assertNotIn("token", json.dumps(result.comparison).lower())

    def test_observe_sensitive_only_difference_is_redacted_but_not_equal(self) -> None:
        secret = "SENTINEL_SECRET_VALUE"
        request = make_request()
        runtime = FakeRuntime(
            make_result(
                request,
                data={"apiToken": secret, "readPat": secret, "compat": "same"},
            )
        )

        result = CapabilityService(runtime, routing_mode="observe").route(
            request,
            legacy_callable=lambda: {
                "status": "success",
                "data": {
                    "apiToken": "legacy-" + secret,
                    "readPat": "legacy-" + secret,
                    "compat": "same",
                },
            },
            equivalence_fields=("data.apiToken", "data.readPat", "data.compat"),
        )

        self.assertEqual("different", result.comparison["status"])
        self.assertEqual(
            {
                "status": {"equal": True},
                "data.compat": {"equal": True},
            },
            result.comparison["fields"],
        )
        self.assertEqual(2, result.comparison["redacted_field_count"])
        encoded = json.dumps(result.comparison)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("token", encoded.lower())

    def test_observe_redacts_api_key_field_without_leaking_its_identifier(self) -> None:
        secret = "SENTINEL_API_KEY"
        request = make_request()
        runtime = FakeRuntime(make_result(request, data={"apiKey": secret}))

        result = CapabilityService(runtime, routing_mode="observe").route(
            request,
            legacy_callable=lambda: {
                "status": "success",
                "data": {"apiKey": "legacy-" + secret},
            },
            equivalence_fields=("data.apiKey",),
        )

        encoded = json.dumps(result.comparison)
        self.assertEqual("different", result.comparison["status"])
        self.assertEqual(1, result.comparison["redacted_field_count"])
        self.assertNotIn("apiKey", encoded)
        self.assertNotIn(secret, encoded)

    def test_observe_redacts_non_json_scalar_instead_of_trusting_its_eq_result(self) -> None:
        secret = "SENTINEL_EQ_SECRET"

        class LeakyEquality:
            def __eq__(self, other: object) -> str:
                return secret

        request = make_request()
        runtime = FakeRuntime(make_result(request, data={"title": "new"}))

        result = CapabilityService(runtime, routing_mode="observe").route(
            request,
            legacy_callable=lambda: {
                "status": "success",
                "data": {"title": LeakyEquality()},
            },
            equivalence_fields=("data.title",),
        )

        encoded = json.dumps(result.comparison)
        self.assertEqual("different", result.comparison["status"])
        self.assertEqual(1, result.comparison["redacted_field_count"])
        self.assertNotIn("data.title", encoded)
        self.assertNotIn(secret, encoded)
        for item in result.comparison["fields"].values():
            self.assertIs(bool, type(item["equal"]))

    def test_observe_calls_capability_once_even_when_legacy_raises(self) -> None:
        request = make_request()
        runtime = FakeRuntime(make_result(request))

        def broken_legacy() -> dict:
            raise RuntimeError("SENTINEL_LEGACY_EXCEPTION")

        result = CapabilityService(runtime, routing_mode="observe").route(
            request,
            legacy_callable=broken_legacy,
        )

        self.assertEqual([request], runtime.preflight_calls)
        self.assertEqual([request], runtime.calls)
        self.assertEqual("legacy", result.selected)
        self.assertEqual("CAPABILITY_LEGACY_FAILED", result.result["audit"]["error_code"])
        self.assertEqual("different", result.comparison["status"])
        self.assertNotIn("SENTINEL_LEGACY_EXCEPTION", json.dumps(result.result))

    def test_observe_handles_malformed_legacy_audit_without_skipping_capability(self) -> None:
        request = make_request()
        runtime = FakeRuntime(make_result(request))
        malformed = {"status": "failed", "audit": "not-an-object"}

        result = CapabilityService(runtime, routing_mode="observe").route(
            request,
            legacy_callable=lambda: malformed,
        )

        self.assertEqual([request], runtime.preflight_calls)
        self.assertEqual([request], runtime.calls)
        self.assertEqual(malformed, result.result)
        self.assertEqual("different", result.comparison["status"])

    def test_observe_preflights_real_descriptor_before_legacy_write_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            marker = root / "provider-ran"
            (scripts / "runner.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            (root / "capabilities.json").write_text(
                json.dumps(
                    {
                        "schema_version": "his-capabilities.v1",
                        "plugin": "his-engineering",
                        "plugin_version": "1.0.0",
                        "capabilities": [
                            {
                                "name": "git.apply-local",
                                "provider": "his-engineering",
                                "contract_version": "git-local.v1",
                                "mutation_level": "L2",
                                "credential_class": "none",
                                "entrypoint": "scripts/runner.py",
                                "enabled": True,
                                "disabled_reason": "",
                                "scopes": ["repository:local:write"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            runtime = CapabilityRuntime(
                CapabilityRegistry.from_plugin_roots([root])
            )
            disguised_request = make_request(
                capability="git.apply-local",
                provider="his-engineering",
                mode="preview",
                mutation_level=MutationLevel.L1,
            )

            legacy_calls: list[str] = []
            result = CapabilityService(
                runtime, routing_mode="observe"
            ).route(
                disguised_request,
                legacy_callable=lambda: legacy_calls.append("legacy")
                or {"status": "success"},
            )

            self.assertEqual([], legacy_calls)
            self.assertEqual("none", result.selected)
            self.assertEqual("blocked", result.result["status"])
            self.assertEqual(
                "CAPABILITY_PERMISSION_DENIED",
                result.result["audit"]["error_code"],
            )
            self.assertFalse(marker.exists())

    def test_enforce_calls_only_capability_runtime(self) -> None:
        request = make_request()
        capability = make_result(request, data={"title": "new"})
        runtime = FakeRuntime(capability)
        legacy_calls: list[str] = []

        result = CapabilityService(runtime, routing_mode="enforce").route(
            request,
            legacy_callable=lambda: legacy_calls.append("legacy") or {},
        )

        self.assertEqual([request], runtime.calls)
        self.assertEqual([], legacy_calls)
        self.assertEqual("capability", result.selected)
        self.assertEqual(capability.to_dict(), result.result)
        self.assertFalse(result.fallback_used)

    def test_blocked_never_falls_back(self) -> None:
        request = make_request()
        runtime = FakeRuntime(make_result(request, status="blocked"))
        legacy_calls: list[str] = []
        service = CapabilityService(
            runtime,
            routing_mode="enforce",
            legacy_read_policies={
                ("workitem.read", "yunxiao"): LegacyReadFallbackPolicy(
                    mutation_level=MutationLevel.L1,
                    fallback_on_failed=True,
                )
            },
        )

        result = service.route(
            request,
            legacy_callable=lambda: legacy_calls.append("legacy") or {"status": "success"},
        )

        self.assertEqual([], legacy_calls)
        self.assertEqual("blocked", result.result["status"])
        self.assertFalse(result.fallback_used)

    def test_unsupported_falls_back_only_to_registered_equal_read(self) -> None:
        request = make_request()
        runtime = FakeRuntime(make_result(request, status="unsupported"))
        service = CapabilityService(
            runtime,
            routing_mode="enforce",
            legacy_read_policies={
                ("workitem.read", "yunxiao"): LegacyReadFallbackPolicy(
                    mutation_level=MutationLevel.L1,
                    fallback_on_failed=False,
                )
            },
        )

        result = service.route(
            request,
            legacy_callable=lambda: {"status": "success", "data": {"source": "legacy"}},
        )

        self.assertEqual("legacy", result.selected)
        self.assertTrue(result.fallback_used)
        self.assertEqual("legacy", result.result["data"]["source"])

    def test_failed_fallback_requires_provider_policy(self) -> None:
        request = make_request()
        runtime = FakeRuntime(make_result(request, status="failed"))
        legacy_calls: list[str] = []
        no_policy = CapabilityService(runtime, routing_mode="enforce")

        blocked = no_policy.route(
            request,
            legacy_callable=lambda: legacy_calls.append("legacy") or {"status": "success"},
        )

        self.assertEqual([], legacy_calls)
        self.assertEqual("failed", blocked.result["status"])
        self.assertFalse(blocked.fallback_used)

        with_policy = CapabilityService(
            runtime,
            routing_mode="enforce",
            legacy_read_policies={
                ("workitem.read", "yunxiao"): LegacyReadFallbackPolicy(
                    mutation_level=MutationLevel.L1,
                    fallback_on_failed=True,
                )
            },
        )
        fallback = with_policy.route(
            request,
            legacy_callable=lambda: legacy_calls.append("legacy") or {"status": "success"},
        )
        self.assertEqual(["legacy"], legacy_calls)
        self.assertTrue(fallback.fallback_used)

    def test_write_request_never_falls_back(self) -> None:
        request = make_request(
            capability="git.apply-local",
            provider="his-engineering",
            mode="apply",
            mutation_level=MutationLevel.L2,
        )
        runtime = FakeRuntime(make_result(request, status="failed"))
        legacy_calls: list[str] = []
        service = CapabilityService(
            runtime,
            routing_mode="enforce",
            legacy_read_policies={
                ("git.apply-local", "his-engineering"): LegacyReadFallbackPolicy(
                    mutation_level=MutationLevel.L1,
                    fallback_on_failed=True,
                )
            },
        )

        result = service.route(
            request,
            legacy_callable=lambda: legacy_calls.append("legacy") or {"status": "success"},
        )

        self.assertEqual([], legacy_calls)
        self.assertEqual("failed", result.result["status"])
        self.assertFalse(result.fallback_used)

    def test_partial_is_returned_without_second_execution(self) -> None:
        request = make_request()
        runtime = FakeRuntime(make_result(request, status="partial"))
        legacy_calls: list[str] = []

        result = CapabilityService(runtime, routing_mode="enforce").route(
            request,
            legacy_callable=lambda: legacy_calls.append("legacy") or {"status": "success"},
        )

        self.assertEqual(1, len(runtime.calls))
        self.assertEqual([], legacy_calls)
        self.assertEqual("partial", result.result["status"])

    def test_runtime_exception_and_unknown_status_fail_closed_without_legacy(self) -> None:
        request = make_request()
        legacy_calls: list[str] = []

        for runtime in (
            FakeRuntime(RuntimeError("SENTINEL_EXCEPTION")),
            FakeRuntime(make_result(request, status="unexpected")),
        ):
            with self.subTest(result=runtime.result):
                result = CapabilityService(
                    runtime,
                    routing_mode="enforce",
                    legacy_read_policies={
                        ("workitem.read", "yunxiao"): LegacyReadFallbackPolicy(
                            mutation_level=MutationLevel.L1,
                            fallback_on_failed=True,
                        )
                    },
                ).route(
                    request,
                    legacy_callable=lambda: legacy_calls.append("legacy") or {},
                )
                encoded = json.dumps(result.result)
                self.assertEqual("failed", result.result["status"])
                self.assertEqual("CAPABILITY_ROUTE_FAILED", result.result["audit"]["error_code"])
                self.assertNotIn("SENTINEL_EXCEPTION", encoded)

        self.assertEqual([], legacy_calls)

    def test_observe_rejects_non_readonly_preview_before_either_path_can_run(self) -> None:
        for mode, level in (
            ("apply", MutationLevel.L1),
            ("preview", MutationLevel.L2),
        ):
            with self.subTest(mode=mode, level=level):
                request = make_request(
                    capability="git.apply-local",
                    provider="his-engineering",
                    mode=mode,
                    mutation_level=level,
                )
                runtime = FakeRuntime(make_result(request))
                legacy_calls: list[str] = []

                result = CapabilityService(runtime, routing_mode="observe").route(
                    request,
                    legacy_callable=lambda: legacy_calls.append("legacy")
                    or {"status": "success"},
                )

                self.assertEqual([], runtime.calls)
                self.assertEqual([], legacy_calls)
                self.assertEqual("failed", result.result["status"])
                self.assertEqual(
                    "CAPABILITY_OBSERVE_REQUIRES_READONLY_PREVIEW",
                    result.result["audit"]["error_code"],
                )
                self.assertEqual("not_compared", result.comparison["status"])

    def test_result_contract_accepts_partial_and_unsupported_but_rejects_unknown(self) -> None:
        request = make_request()
        for status in ("partial", "unsupported"):
            with self.subTest(status=status):
                payload = make_result(request, status=status).to_dict()
                self.assertEqual(status, CapabilityResult.from_dict(payload, request=request).status)

        payload = make_result(request, status="unexpected").to_dict()
        with self.assertRaisesRegex(CapabilityContractError, "status"):
            CapabilityResult.from_dict(payload, request=request)


if __name__ == "__main__":
    unittest.main()
