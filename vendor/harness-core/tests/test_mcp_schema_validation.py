from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.mcp_schema_validation import (
    McpSchemaValidationError,
    check_supported_schema,
    validate_mcp_arguments,
)


ROOT = Path(__file__).resolve().parents[1]


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "target", "tags"],
        "properties": {
            "operation": {"type": "string", "enum": ["read", "inspect"]},
            "target": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 12,
                        "pattern": "^[A-Z]+-[0-9]+$",
                    }
                },
            },
            "tags": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
                "items": {"type": "string", "maxLength": 8},
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    }


class McpSchemaValidationTests(unittest.TestCase):
    def test_supported_nested_object_and_array_schema_validates(self) -> None:
        schema = _schema()
        check_supported_schema(schema)

        validate_mcp_arguments(
            schema,
            {
                "operation": "read",
                "target": {"id": "WI-100"},
                "tags": ["detail", "comment"],
                "limit": 50,
            },
        )

    def test_unknown_fields_and_missing_required_fields_are_rejected(self) -> None:
        for payload in (
            {"operation": "read", "target": {"id": "WI-100"}, "tags": ["detail"], "extra": 1},
            {"operation": "read", "target": {}, "tags": ["detail"]},
            {"operation": "read", "target": {"id": "WI-100"}},
        ):
            with self.subTest(payload_keys=sorted(payload)):
                with self.assertRaises(McpSchemaValidationError):
                    validate_mcp_arguments(_schema(), payload)

    def test_limits_patterns_enums_and_unique_items_are_enforced(self) -> None:
        invalid_payloads = (
            {"operation": "write", "target": {"id": "WI-100"}, "tags": ["detail"]},
            {"operation": "read", "target": {"id": "bad"}, "tags": ["detail"]},
            {"operation": "read", "target": {"id": "WI-100"}, "tags": []},
            {"operation": "read", "target": {"id": "WI-100"}, "tags": ["same", "same"]},
            {"operation": "read", "target": {"id": "WI-100"}, "tags": ["detail"], "limit": 101},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(McpSchemaValidationError):
                    validate_mcp_arguments(_schema(), payload)

    def test_unsupported_keywords_are_rejected_at_any_depth(self) -> None:
        for schema in (
            {"type": "object", "$ref": "other.json"},
            {
                "type": "object",
                "properties": {"id": {"type": "string", "format": "uuid"}},
            },
        ):
            with self.subTest(schema=schema):
                with self.assertRaises(McpSchemaValidationError):
                    check_supported_schema(schema)

    def test_malformed_supported_constraints_are_rejected(self) -> None:
        invalid_schemas = (
            {"type": "object", "additionalProperties": "no"},
            {"type": "array", "minItems": 3, "maxItems": 2, "items": {"type": "string"}},
            {"type": "string", "pattern": "["},
            {"type": "object", "required": ["missing"], "properties": {}},
            {"type": "unknown"},
        )
        for schema in invalid_schemas:
            with self.subTest(schema=schema):
                with self.assertRaises(McpSchemaValidationError):
                    check_supported_schema(schema)

    def test_boolean_is_not_accepted_as_an_integer(self) -> None:
        with self.assertRaises(McpSchemaValidationError):
            validate_mcp_arguments(
                {"type": "object", "properties": {"limit": {"type": "integer"}}},
                {"limit": True},
            )

    def test_production_mcp_schemas_use_only_the_supported_subset(self) -> None:
        for name in (
            "mcp_result_envelope.v1.json",
            "mcp_capability_manifest.v1.json",
        ):
            with self.subTest(schema=name):
                schema = json.loads(
                    (ROOT / "config/schemas" / name).read_text(encoding="utf-8")
                )
                check_supported_schema(schema)
                self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
