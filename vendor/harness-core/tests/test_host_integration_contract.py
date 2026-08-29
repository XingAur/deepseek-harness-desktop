import unittest

from app.agent_backend import AgentBackendRole
from app.host_integration_contract import (
    DEFAULT_HOST_DESCRIPTORS,
    HostDescriptor,
    HostNegotiationRequest,
    build_host_integration_status,
    negotiate_host,
    parse_host_negotiation_request,
)


class HostIntegrationContractTests(unittest.TestCase):
    def test_four_hosts_are_explicitly_described(self):
        status = build_host_integration_status()
        self.assertEqual("his-agent-host-status.v1", status["schema_version"])
        self.assertEqual(
            {"terminal", "codex-app", "codex-cli", "deepseek-harness-desktop"},
            {item["host_id"] for item in status["hosts"]},
        )

    def test_negotiation_returns_backend_without_authorizing_mutation(self):
        request = parse_host_negotiation_request({
            "schema_version": "his-agent-host-negotiation.v1",
            "host_id": "deepseek-harness-desktop",
            "role": "worker",
            "required_capabilities": ["source.search", "verification.run-local"],
            "requested_mutation_level": "L0",
        })
        result = negotiate_host(request, authorized_mutation_level="L0")

        self.assertTrue(result["negotiated"])
        self.assertEqual("host-bridge", result["backend_id"])
        self.assertEqual("harness_policy", result["authorization_source"])

    def test_codex_desktop_host_selects_its_optional_app_server_backend(self):
        result = negotiate_host(
            HostNegotiationRequest("codex-app", AgentBackendRole.WORKER, (), "L0"),
            authorized_mutation_level="L0",
        )

        self.assertEqual("codex-app-server", result["backend_id"])

    def test_host_name_cannot_raise_harness_authorization(self):
        for host_id in ("terminal", "codex-app", "codex-cli", "deepseek-harness-desktop"):
            request = HostNegotiationRequest(
                host_id=host_id,
                role=AgentBackendRole.WORKER,
                required_capabilities=("source.search",),
                requested_mutation_level="L2",
            )
            with self.subTest(host_id=host_id):
                with self.assertRaisesRegex(ValueError, "host_mutation_not_authorized"):
                    negotiate_host(request, authorized_mutation_level="L0")

    def test_unsupported_host_role_capability_and_mutation_have_stable_errors(self):
        descriptor = HostDescriptor(
            host_id="limited-host",
            display_name="Limited Host",
            transport="stdio-jsonl",
            backend_id="host-bridge",
            supported_roles=(AgentBackendRole.WORKER,),
            capabilities=("source.search",),
            max_mutation_level="L0",
        )
        with self.assertRaisesRegex(ValueError, "host_role_unsupported"):
            negotiate_host(
                HostNegotiationRequest("limited-host", AgentBackendRole.REVIEWER, (), "L0"),
                authorized_mutation_level="L0",
                descriptors=(descriptor,),
            )
        with self.assertRaisesRegex(ValueError, "host_capability_unsupported"):
            negotiate_host(
                HostNegotiationRequest("limited-host", AgentBackendRole.WORKER, ("git.diff",), "L0"),
                authorized_mutation_level="L0",
                descriptors=(descriptor,),
            )
        with self.assertRaisesRegex(ValueError, "host_mutation_unsupported"):
            negotiate_host(
                HostNegotiationRequest("limited-host", AgentBackendRole.WORKER, (), "L1"),
                authorized_mutation_level="L1",
                descriptors=(descriptor,),
            )

    def test_unknown_or_opaque_negotiation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "host_unknown"):
            negotiate_host(
                HostNegotiationRequest("missing-host", AgentBackendRole.WORKER, (), "L0"),
                authorized_mutation_level="L0",
            )
        with self.assertRaisesRegex(ValueError, "host_negotiation_invalid"):
            parse_host_negotiation_request({
                "schema_version": "his-agent-host-negotiation.v1",
                "host_id": "codex-app",
                "role": "worker",
                "required_capabilities": ["thread_id"],
                "requested_mutation_level": "L0",
            })


if __name__ == "__main__":
    unittest.main()
