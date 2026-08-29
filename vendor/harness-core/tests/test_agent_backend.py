from __future__ import annotations

import unittest

from app.agent_backend import (
    AgentBackendDescriptor,
    AgentBackendRegistry,
    AgentBackendRole,
)


class AgentBackendContractTests(unittest.TestCase):
    def test_descriptor_is_provider_neutral_and_serializable(self) -> None:
        descriptor = AgentBackendDescriptor(
            backend_id="host-bridge",
            display_name="Host bridge",
            transport="stdio-jsonl",
            supported_roles=(AgentBackendRole.WORKER, AgentBackendRole.REVIEWER),
            requires_local_executable=False,
            external_calls=False,
            enabled=True,
        )

        self.assertEqual(
            {
                "schema_version": "his-agent-backend-descriptor.v1",
                "backend_id": "host-bridge",
                "display_name": "Host bridge",
                "transport": "stdio-jsonl",
                "supported_roles": ["worker", "reviewer"],
                "requires_local_executable": False,
                "external_calls": False,
                "enabled": True,
            },
            descriptor.to_dict(),
        )

    def test_registry_resolves_only_enabled_declared_backends(self) -> None:
        registry = AgentBackendRegistry(
            (
                AgentBackendDescriptor(
                    backend_id="host-bridge",
                    display_name="Host bridge",
                    transport="stdio-jsonl",
                    supported_roles=(AgentBackendRole.WORKER,),
                    requires_local_executable=False,
                    external_calls=False,
                    enabled=True,
                ),
                AgentBackendDescriptor(
                    backend_id="disabled",
                    display_name="Disabled",
                    transport="stdio-jsonl",
                    supported_roles=(AgentBackendRole.WORKER,),
                    requires_local_executable=False,
                    external_calls=False,
                    enabled=False,
                ),
            )
        )

        self.assertEqual("host-bridge", registry.resolve("host-bridge").backend_id)
        with self.assertRaisesRegex(ValueError, "agent_backend_unavailable"):
            registry.resolve("disabled")
        with self.assertRaisesRegex(ValueError, "agent_backend_unknown"):
            registry.resolve("missing")

    def test_registry_rejects_duplicate_or_invalid_descriptors(self) -> None:
        descriptor = AgentBackendDescriptor(
            backend_id="host-bridge",
            display_name="Host bridge",
            transport="stdio-jsonl",
            supported_roles=(AgentBackendRole.WORKER,),
            requires_local_executable=False,
            external_calls=False,
            enabled=True,
        )
        with self.assertRaisesRegex(ValueError, "agent_backend_duplicate"):
            AgentBackendRegistry((descriptor, descriptor))
        with self.assertRaisesRegex(ValueError, "agent_backend_invalid"):
            AgentBackendDescriptor(
                backend_id="bad id",
                display_name="Bad",
                transport="stdio-jsonl",
                supported_roles=(AgentBackendRole.WORKER,),
                requires_local_executable=False,
                external_calls=False,
                enabled=True,
            )


if __name__ == "__main__":
    unittest.main()
