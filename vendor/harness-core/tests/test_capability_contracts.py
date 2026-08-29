from __future__ import annotations

import unittest

from app.capability_contracts import (
    CapabilityContractError,
    CapabilityRequest,
    CapabilityResult,
)


def request_payload() -> dict:
    return {
        "schema_version": "his-capability-request.v1",
        "request_id": "req-1",
        "capability": "workitem.read",
        "provider": "yunxiao",
        "mode": "preview",
        "mutation_level": "L1",
        "authorization": {"explicit": False, "scope": []},
        "input": {},
        "context": {},
    }


def result_payload() -> dict:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": "req-1",
        "capability": "workitem.read",
        "provider": "yunxiao",
        "status": "success",
        "mutation_level": "L1",
        "changed": False,
        "summary": "",
        "data": {},
        "evidence": [],
        "warnings": [],
        "blockers": [],
        "audit": {},
    }


class CapabilityContractTests(unittest.TestCase):
    def test_valid_request_round_trips_to_versioned_payload(self) -> None:
        request = CapabilityRequest.from_dict(request_payload())

        self.assertEqual(request_payload(), request.to_dict())

    def test_unknown_request_schema_is_rejected(self) -> None:
        payload = request_payload()
        payload["schema_version"] = "unknown.v1"

        with self.assertRaises(CapabilityContractError):
            CapabilityRequest.from_dict(payload)

    def test_required_request_fields_are_rejected_when_missing(self) -> None:
        for field in ("request_id", "capability", "provider", "mode"):
            with self.subTest(field=field):
                payload = request_payload()
                del payload[field]

                with self.assertRaises(CapabilityContractError):
                    CapabilityRequest.from_dict(payload)

    def test_mode_only_allows_preview_or_apply(self) -> None:
        payload = request_payload()
        payload["mode"] = "write"

        with self.assertRaises(CapabilityContractError):
            CapabilityRequest.from_dict(payload)

    def test_authorization_rejects_privilege_shaped_extra_fields(self) -> None:
        payload = request_payload()
        payload["authorization"]["allow_external_write"] = True

        with self.assertRaises(CapabilityContractError):
            CapabilityRequest.from_dict(payload)

    def test_result_request_id_must_match(self) -> None:
        request = CapabilityRequest.from_dict(request_payload())
        payload = result_payload()
        payload["request_id"] = "req-other"

        with self.assertRaises(CapabilityContractError):
            CapabilityResult.from_dict(payload, request=request)

    def test_changed_result_requires_non_empty_audit(self) -> None:
        payload = result_payload()
        payload["changed"] = True

        with self.assertRaises(CapabilityContractError):
            CapabilityResult.from_dict(payload)

    def test_unknown_result_status_is_rejected(self) -> None:
        payload = result_payload()
        payload["status"] = "pending"

        with self.assertRaises(CapabilityContractError):
            CapabilityResult.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
