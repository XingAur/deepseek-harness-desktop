from __future__ import annotations

import copy
import unittest

from app.mcp_contracts import (
    MCP_RESULT_SCHEMA_VERSION,
    McpContractError,
    canonical_json_size,
    mcp_envelope_to_dict,
    parse_mcp_result_envelope,
)


def _success_payload() -> dict[str, object]:
    return {
        "schema_version": MCP_RESULT_SCHEMA_VERSION,
        "request_id": "request-001",
        "capability": "workitem.read",
        "provider": "yunxiao",
        "status": "success",
        "data": {"item": {"id": "WI-100", "title": "Reviewed requirement"}},
        "evidence_ref": "source-evidence:WI-100:v3",
        "source": {
            "system": "yunxiao",
            "object_id": "WI-100",
            "version": "3",
            "observed_at": "2026-08-30T00:00:00Z",
        },
        "freshness": {
            "status": "fresh",
            "expires_at": "2026-08-30T00:05:00Z",
        },
        "pagination": {"truncated": False, "next_cursor": ""},
        "redaction": {"applied": False, "fields": []},
        "error": {"code": "", "retryable": False, "recovery": ""},
        "trace": {
            "mcp_server": "yunxiao",
            "tool": "workitem_get",
            "server_version": "1.0.0",
            "trace_id": "trace-001",
        },
    }


def _parse(payload: dict[str, object]):
    return parse_mcp_result_envelope(
        payload,
        expected_request_id="request-001",
        expected_capability="workitem.read",
        expected_provider="yunxiao",
    )


class McpContractTests(unittest.TestCase):
    def test_complete_success_envelope_parses_as_an_immutable_snapshot(self) -> None:
        payload = _success_payload()
        envelope = _parse(payload)
        payload["data"]["item"]["title"] = "mutated"  # type: ignore[index]

        serialized = mcp_envelope_to_dict(envelope)
        self.assertEqual("success", envelope.status)
        self.assertEqual("Reviewed requirement", serialized["data"]["item"]["title"])

    def test_unknown_top_level_and_nested_fields_are_rejected(self) -> None:
        for location in ("top", "source", "trace"):
            with self.subTest(location=location):
                payload = _success_payload()
                target = payload if location == "top" else payload[location]
                target["unexpected"] = True  # type: ignore[index]
                with self.assertRaises(McpContractError):
                    _parse(payload)

    def test_unsupported_schema_version_is_rejected(self) -> None:
        payload = _success_payload()
        payload["schema_version"] = "his-mcp-result-envelope.v999"

        with self.assertRaises(McpContractError):
            _parse(payload)

    def test_request_capability_and_provider_identity_must_match(self) -> None:
        for field in ("request_id", "capability", "provider"):
            with self.subTest(field=field):
                payload = _success_payload()
                payload[field] = "different"
                with self.assertRaises(McpContractError):
                    _parse(payload)

    def test_success_requires_evidence_and_observed_source_identity(self) -> None:
        mutations = (
            ("evidence_ref", None),
            ("source", "system"),
            ("source", "observed_at"),
        )
        for container, field in mutations:
            with self.subTest(container=container, field=field):
                payload = _success_payload()
                if field is None:
                    payload[container] = ""
                else:
                    payload[container][field] = ""  # type: ignore[index]
                with self.assertRaises(McpContractError):
                    _parse(payload)

    def test_failure_requires_error_code_and_recovery(self) -> None:
        for field in ("code", "recovery"):
            with self.subTest(field=field):
                payload = _success_payload()
                payload["status"] = "failed"
                payload["evidence_ref"] = ""
                payload["error"] = {
                    "code": "REMOTE_FAILED",
                    "retryable": True,
                    "recovery": "Retry after provider recovery.",
                }
                payload["error"][field] = ""  # type: ignore[index]
                with self.assertRaises(McpContractError):
                    _parse(payload)

    def test_cursor_is_present_only_for_truncated_results(self) -> None:
        payload = _success_payload()
        payload["pagination"] = {"truncated": False, "next_cursor": "cursor-2"}
        with self.assertRaises(McpContractError):
            _parse(payload)

        payload["pagination"] = {"truncated": True, "next_cursor": "cursor-2"}
        self.assertEqual("cursor-2", _parse(payload).pagination.next_cursor)

    def test_redaction_fields_are_sorted_unique_and_consistent(self) -> None:
        for fields in (["title", "body"], ["title", "title"]):
            with self.subTest(fields=fields):
                payload = _success_payload()
                payload["redaction"] = {"applied": True, "fields": fields}
                with self.assertRaises(McpContractError):
                    _parse(payload)

        payload = _success_payload()
        payload["redaction"] = {"applied": True, "fields": ["body", "title"]}
        self.assertEqual(("body", "title"), _parse(payload).redaction.fields)

    def test_secret_like_keys_are_rejected_recursively(self) -> None:
        for key in ("token", "password", "authorization", "dsn", "secret"):
            with self.subTest(key=key):
                payload = _success_payload()
                payload["data"] = {"nested": {key: "not-echoed"}}
                with self.assertRaises(McpContractError) as caught:
                    _parse(payload)
                self.assertNotIn("not-echoed", str(caught.exception))

    def test_secret_credential_and_pii_shaped_scalars_are_rejected_without_echo(self) -> None:
        values = (
            "sk-abcdefgh12345678",
            "Bearer abcdefghijklmnop",
            "postgresql://user:password@example.invalid/db",
            "13800138000",
        )
        for value in values:
            with self.subTest(shape=value[:3]):
                payload = _success_payload()
                payload["data"] = {"nested": [value]}
                with self.assertRaises(McpContractError) as caught:
                    _parse(payload)
                self.assertNotIn(value, str(caught.exception))

    def test_canonical_json_size_uses_sorted_compact_utf8_bytes(self) -> None:
        payload = {"z": "中", "a": 1}

        self.assertEqual(len('{"a":1,"z":"中"}'.encode("utf-8")), canonical_json_size(payload))

    def test_envelope_serialization_returns_independent_mutable_data(self) -> None:
        envelope = _parse(_success_payload())
        first = mcp_envelope_to_dict(envelope)
        second = mcp_envelope_to_dict(envelope)
        first["data"]["item"]["title"] = "changed"

        self.assertEqual("Reviewed requirement", second["data"]["item"]["title"])


if __name__ == "__main__":
    unittest.main()
