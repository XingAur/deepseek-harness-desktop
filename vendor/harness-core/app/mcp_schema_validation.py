from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any


SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
    }
)
_SUPPORTED_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)
_MAX_SCHEMA_DEPTH = 32
_MAX_VALIDATION_NODES = 20_000


class McpSchemaValidationError(ValueError):
    """The bounded MCP schema subset or its payload is invalid."""


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise McpSchemaValidationError("schema value is not canonical JSON") from exc


def check_supported_schema(schema: Mapping[str, Any], *, path: str = "$") -> None:
    _check_schema(schema, path=path, depth=0)


def _check_schema(schema: Mapping[str, Any], *, path: str, depth: int) -> None:
    if depth > _MAX_SCHEMA_DEPTH or not isinstance(schema, Mapping):
        raise McpSchemaValidationError(f"invalid schema at {path}")
    unknown = set(schema) - SUPPORTED_SCHEMA_KEYWORDS
    if unknown:
        raise McpSchemaValidationError(f"unsupported schema keyword at {path}")

    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _SUPPORTED_TYPES:
        raise McpSchemaValidationError(f"unsupported schema type at {path}")
    for metadata_key in ("$schema", "$id", "title", "description"):
        if metadata_key in schema and not isinstance(schema[metadata_key], str):
            raise McpSchemaValidationError(f"invalid schema metadata at {path}")

    properties = schema.get("properties")
    if properties is not None:
        if schema_type not in {None, "object"} or not isinstance(properties, Mapping):
            raise McpSchemaValidationError(f"invalid object properties at {path}")
        for key, child in properties.items():
            if not isinstance(key, str) or not isinstance(child, Mapping):
                raise McpSchemaValidationError(f"invalid property schema at {path}")
            _check_schema(child, path=f"{path}.properties.{key}", depth=depth + 1)
    required = schema.get("required")
    if required is not None:
        if (
            schema_type not in {None, "object"}
            or not isinstance(required, (list, tuple))
            or any(not isinstance(item, str) or not item for item in required)
            or len(required) != len(set(required))
            or not isinstance(properties, Mapping)
            or not set(required).issubset(properties)
        ):
            raise McpSchemaValidationError(f"invalid required fields at {path}")
    if "additionalProperties" in schema:
        if schema_type not in {None, "object"} or not isinstance(
            schema["additionalProperties"], bool
        ):
            raise McpSchemaValidationError(f"invalid additionalProperties at {path}")

    items = schema.get("items")
    if items is not None:
        if schema_type not in {None, "array"} or not isinstance(items, Mapping):
            raise McpSchemaValidationError(f"invalid array items at {path}")
        _check_schema(items, path=f"{path}.items", depth=depth + 1)

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, (list, tuple)) or not enum:
            raise McpSchemaValidationError(f"invalid enum at {path}")
        encoded = [_canonical(item) for item in enum]
        if len(encoded) != len(set(encoded)):
            raise McpSchemaValidationError(f"duplicate enum value at {path}")
    if "const" in schema:
        _canonical(schema["const"])

    for minimum_key, maximum_key in (
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
    ):
        minimum = schema.get(minimum_key)
        maximum = schema.get(maximum_key)
        if minimum is not None and (not _integer(minimum) or minimum < 0):
            raise McpSchemaValidationError(f"invalid {minimum_key} at {path}")
        if maximum is not None and (not _integer(maximum) or maximum < 0):
            raise McpSchemaValidationError(f"invalid {maximum_key} at {path}")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise McpSchemaValidationError(f"inverted limits at {path}")
    if any(key in schema for key in ("minLength", "maxLength", "pattern")):
        if schema_type not in {None, "string"}:
            raise McpSchemaValidationError(f"string constraint on non-string at {path}")
    if any(key in schema for key in ("minItems", "maxItems", "uniqueItems")):
        if schema_type not in {None, "array"}:
            raise McpSchemaValidationError(f"array constraint on non-array at {path}")
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise McpSchemaValidationError(f"invalid uniqueItems at {path}")
    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            raise McpSchemaValidationError(f"invalid pattern at {path}")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise McpSchemaValidationError(f"invalid pattern at {path}") from exc

    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and not _number(minimum):
        raise McpSchemaValidationError(f"invalid minimum at {path}")
    if maximum is not None and not _number(maximum):
        raise McpSchemaValidationError(f"invalid maximum at {path}")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise McpSchemaValidationError(f"inverted numeric limits at {path}")
    if (minimum is not None or maximum is not None) and schema_type not in {
        None,
        "integer",
        "number",
    }:
        raise McpSchemaValidationError(f"numeric constraint on non-number at {path}")


def validate_mcp_arguments(
    schema: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    check_supported_schema(schema)
    if not isinstance(payload, Mapping):
        raise McpSchemaValidationError("MCP arguments must be an object")
    _validate(schema, payload, path="$", depth=0, budget=[_MAX_VALIDATION_NODES])


def _validate(
    schema: Mapping[str, Any],
    value: Any,
    *,
    path: str,
    depth: int,
    budget: list[int],
) -> None:
    if depth > _MAX_SCHEMA_DEPTH or budget[0] <= 0:
        raise McpSchemaValidationError("MCP arguments exceed validation limits")
    budget[0] -= 1
    if "const" in schema and value != schema["const"]:
        raise McpSchemaValidationError(f"const mismatch at {path}")
    if "enum" in schema and value not in schema["enum"]:
        raise McpSchemaValidationError(f"enum mismatch at {path}")

    schema_type = schema.get("type")
    type_valid = {
        None: True,
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": _integer(value),
        "number": _number(value),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[schema_type]
    if not type_valid:
        raise McpSchemaValidationError(f"type mismatch at {path}")

    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise McpSchemaValidationError(f"non-string object key at {path}")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = set(required) - set(value)
        if missing:
            raise McpSchemaValidationError(f"required field missing at {path}")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise McpSchemaValidationError(f"unknown field at {path}")
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                _validate(
                    child_schema,
                    item,
                    path=f"{path}.{key}",
                    depth=depth + 1,
                    budget=budget,
                )
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise McpSchemaValidationError(f"array too short at {path}")
        if maximum is not None and len(value) > maximum:
            raise McpSchemaValidationError(f"array too long at {path}")
        if schema.get("uniqueItems"):
            encoded = [_canonical(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise McpSchemaValidationError(f"array items not unique at {path}")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate(
                    item_schema,
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    budget=budget,
                )
    elif isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            raise McpSchemaValidationError(f"string too short at {path}")
        if maximum is not None and len(value) > maximum:
            raise McpSchemaValidationError(f"string too long at {path}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise McpSchemaValidationError(f"pattern mismatch at {path}")
    elif _number(value):
        if "minimum" in schema and value < schema["minimum"]:
            raise McpSchemaValidationError(f"number below minimum at {path}")
        if "maximum" in schema and value > schema["maximum"]:
            raise McpSchemaValidationError(f"number above maximum at {path}")
