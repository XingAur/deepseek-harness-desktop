#!/usr/bin/env python3
"""Dependency-free, local and read-only MCP facade for formal HIS knowledge."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from dataclasses import fields, is_dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Mapping, Optional, TextIO


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from knowledge_capability import knowledge_home  # noqa: E402
from knowledge_retrieve import KnowledgeQuery, KnowledgeRetriever  # noqa: E402
from knowledge_store import DEFAULT_KNOWLEDGE_HOME, KnowledgeStore  # noqa: E402


SERVER_NAME = "his-knowledge"
SERVER_VERSION = "0.2.1"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SEARCH_FIELDS = frozenset(
    {"query", "hospital", "region", "module", "repo", "branch", "as_of", "limit"}
)


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def _public_item(item: Mapping[str, object]) -> dict[str, object]:
    return {
        key: _json_safe(item.get(key))
        for key in (
            "stable_key",
            "title",
            "body",
            "kind",
            "authority",
            "status",
            "hospital_scope",
            "region_scope",
            "module_scope",
            "repo_scope",
            "branch_scope",
            "version_label",
            "valid_from",
            "valid_until",
            "source_refs",
            "tags",
            "updated_at",
        )
    }


def _tool_annotations() -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


TOOLS = (
    {
        "name": "knowledge_search",
        "description": (
            "Search formal HIS knowledge with evidence authority, scope, freshness, conflicts, "
            "source references and deterministic score details. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "hospital": {"type": "string"},
                "region": {"type": "string"},
                "module": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string"},
                "as_of": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": _tool_annotations(),
    },
    {
        "name": "knowledge_get",
        "description": "Get one active formal HIS knowledge item by stable key. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"stable_key": {"type": "string", "minLength": 1}},
            "required": ["stable_key"],
            "additionalProperties": False,
        },
        "annotations": _tool_annotations(),
    },
    {
        "name": "knowledge_related",
        "description": (
            "Read direct service, API, repository, requirement and data relations for one formal "
            "knowledge key. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stable_key": {"type": "string", "minLength": 1},
                "direction": {"type": "string", "enum": ["both", "outgoing", "incoming"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["stable_key"],
            "additionalProperties": False,
        },
        "annotations": _tool_annotations(),
    },
    {
        "name": "knowledge_health",
        "description": (
            "Report whether the local formal knowledge database is absent, ready or unreadable, "
            "with non-sensitive counts. Read-only and never creates storage."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": _tool_annotations(),
    },
)


def _mcp_knowledge_home() -> Path:
    """Use the governed override when present; otherwise use the local read-only default."""
    if os.environ.get("HIS_KNOWLEDGE_HOME"):
        return knowledge_home()
    return DEFAULT_KNOWLEDGE_HOME


class KnowledgeMcpServer:
    """Small MCP stdio server whose complete public surface is read-only."""

    def __init__(
        self,
        *,
        store: Optional[KnowledgeStore] = None,
        utc_date: Optional[Callable[[], date]] = None,
    ) -> None:
        self.store = store or KnowledgeStore(home=_mcp_knowledge_home())
        self.utc_date = utc_date

    @staticmethod
    def _arguments(value: object, allowed: frozenset[str]) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError("arguments must be an object")
        result = {str(key): item for key, item in value.items()}
        if not set(result).issubset(allowed):
            raise ValueError("arguments contain unsupported fields")
        return result

    def _snapshot(self):
        return self.store.read_retrieval_snapshot()

    def _search(self, arguments: object) -> dict[str, object]:
        values = self._arguments(arguments, SEARCH_FIELDS)
        query = values.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        for key in SEARCH_FIELDS - {"query", "limit"}:
            if key in values and not isinstance(values[key], str):
                raise ValueError("scope and date arguments must be strings")
        limit = values.get("limit", 8)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be an integer from 1 through 50")
        retriever = KnowledgeRetriever(self.store, utc_date=self.utc_date)
        retrieval = retriever.retrieve(
            KnowledgeQuery(
                text=query,
                hospital=str(values.get("hospital", "")),
                region=str(values.get("region", "")),
                module=str(values.get("module", "")),
                repo=str(values.get("repo", "")),
                branch=str(values.get("branch", "")),
                as_of=str(values.get("as_of", "")),
                limit=limit,
            )
        )
        return _json_safe(retrieval)

    def _get(self, arguments: object) -> dict[str, object]:
        values = self._arguments(arguments, frozenset({"stable_key"}))
        stable_key = values.get("stable_key")
        if not isinstance(stable_key, str) or not stable_key.strip():
            raise ValueError("stable_key must be a non-empty string")
        snapshot = self._snapshot()
        if snapshot is None:
            return {"status": "absent", "item": None, "relations": []}
        items, relations = snapshot
        item = next((entry for entry in items if entry["stable_key"] == stable_key), None)
        direct = [
            {"source_key": source, "relation": relation, "target_key": target}
            for source, relation, target in relations
            if source == stable_key or target == stable_key
        ]
        return {
            "status": "found" if item is not None else "not_found",
            "item": None if item is None else _public_item(item),
            "relations": direct,
        }

    def _related(self, arguments: object) -> dict[str, object]:
        values = self._arguments(arguments, frozenset({"stable_key", "direction", "limit"}))
        stable_key = values.get("stable_key")
        if not isinstance(stable_key, str) or not stable_key.strip():
            raise ValueError("stable_key must be a non-empty string")
        direction = values.get("direction", "both")
        if direction not in {"both", "outgoing", "incoming"}:
            raise ValueError("direction must be both, outgoing, or incoming")
        limit = values.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 through 100")
        snapshot = self._snapshot()
        if snapshot is None:
            return {"status": "absent", "stable_key": stable_key, "relations": []}
        items, relations = snapshot
        by_key = {str(item["stable_key"]): item for item in items}
        result = []
        for source, relation, target in relations:
            relative_direction = "outgoing" if source == stable_key else "incoming" if target == stable_key else ""
            if not relative_direction or direction not in {"both", relative_direction}:
                continue
            peer_key = target if relative_direction == "outgoing" else source
            peer = by_key.get(peer_key)
            result.append(
                {
                    "source_key": source,
                    "relation": relation,
                    "target_key": target,
                    "direction": relative_direction,
                    "peer": None
                    if peer is None
                    else {
                        "stable_key": peer["stable_key"],
                        "title": peer["title"],
                        "kind": peer["kind"],
                        "authority": peer["authority"],
                    },
                }
            )
            if len(result) == limit:
                break
        return {"status": "ready", "stable_key": stable_key, "relations": result}

    def _health(self, arguments: object) -> dict[str, object]:
        self._arguments(arguments, frozenset())
        exists = self.store.database_path.is_file()
        snapshot = self._snapshot()
        if snapshot is None:
            return {
                "status": "unreadable" if exists else "absent",
                "storage": "local_sqlite",
                "database": "knowledge.sqlite",
                "read_only": True,
                "counts": {"items": 0, "relations": 0, "kinds": {}, "authorities": {}},
            }
        items, relations = snapshot
        return {
            "status": "ready",
            "storage": "local_sqlite",
            "database": "knowledge.sqlite",
            "read_only": True,
            "counts": {
                "items": len(items),
                "relations": len(relations),
                "kinds": dict(sorted(Counter(str(item["kind"]) for item in items).items())),
                "authorities": dict(sorted(Counter(str(item["authority"]) for item in items).items())),
            },
        }

    def call_tool(self, name: str, arguments: object) -> dict[str, object]:
        handlers = {
            "knowledge_search": self._search,
            "knowledge_get": self._get,
            "knowledge_related": self._related,
            "knowledge_health": self._health,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ValueError("unknown tool")
        return handler(arguments)

    @staticmethod
    def _response(identifier: object, result: Mapping[str, object]) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": identifier, "result": dict(result)}

    @staticmethod
    def _error(identifier: object, code: int, message: str) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}

    def handle(self, message: object) -> Optional[dict[str, object]]:
        if not isinstance(message, Mapping):
            return self._error(None, -32600, "Invalid Request")
        identifier = message.get("id")
        method = message.get("method")
        if identifier is None:
            return None
        if not isinstance(method, str):
            return self._error(identifier, -32600, "Invalid Request")
        params = message.get("params", {})
        if method == "initialize":
            if not isinstance(params, Mapping):
                return self._error(identifier, -32602, "Invalid params")
            protocol_version = params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
            if not isinstance(protocol_version, str):
                return self._error(identifier, -32602, "Invalid params")
            return self._response(
                identifier,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method == "ping":
            return self._response(identifier, {})
        if method == "tools/list":
            return self._response(identifier, {"tools": list(TOOLS)})
        if method == "tools/call":
            if not isinstance(params, Mapping) or not isinstance(params.get("name"), str):
                return self._error(identifier, -32602, "Invalid params")
            try:
                data = self.call_tool(str(params["name"]), params.get("arguments", {}))
            except (KeyError, TypeError, ValueError):
                text = "INVALID_TOOL_ARGUMENTS: unknown tool" if params.get("name") not in {
                    tool["name"] for tool in TOOLS
                } else "INVALID_TOOL_ARGUMENTS"
                tool_result = {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": {"status": "error", "code": "INVALID_TOOL_ARGUMENTS"},
                    "isError": True,
                }
            else:
                tool_result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(data, ensure_ascii=False, sort_keys=True),
                        }
                    ],
                    "structuredContent": data,
                    "isError": False,
                }
            return self._response(identifier, tool_result)
        return self._error(identifier, -32601, "Method not found")

    def serve(self, source: TextIO, target: TextIO) -> None:
        for raw_line in source:
            if not raw_line.strip():
                continue
            try:
                message = json.loads(raw_line)
            except json.JSONDecodeError:
                response = self._error(None, -32700, "Parse error")
            else:
                response = self.handle(message)
            if response is not None:
                target.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                target.flush()


def main() -> int:
    KnowledgeMcpServer().serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
