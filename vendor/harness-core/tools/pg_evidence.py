from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.capability_contracts import CapabilityRequest
from app.mcp_capability_runtime import McpCapabilityRuntime
from app.mcp_runtime_factory import build_persistent_mcp_runtime
from app.runtime_bootstrap import reexec_in_project_venv


_OPERATIONS = {
    "schemas",
    "tables",
    "columns",
    "constraints",
    "indexes",
    "foreign_keys",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read bounded PostgreSQL catalog evidence through database.inspect MCP."
    )
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--state-root", default="")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    request = build_capability_request(load_request_file(Path(args.request_file)))
    state_root = (
        Path(args.state_root).expanduser().absolute()
        if args.state_root
        else output_dir / "mcp-runtime"
    )
    runtime = build_runtime(state_root=state_root)
    execution = runtime.execute(request)
    result = execution.result.to_dict()
    output = output_dir / "database_inspect_result.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"database.inspect status: {result['status']}")
    print(f"Result: {output}")
    if result["status"] != "success":
        raise SystemExit(1)


def build_runtime(*, state_root: Path) -> McpCapabilityRuntime:
    config = json.loads(
        (PROJECT_ROOT / "config" / "capabilities.json").read_text(encoding="utf-8")
    )
    plugin_roots = config.get("plugin_roots")
    if not isinstance(plugin_roots, list) or any(
        not isinstance(item, str) for item in plugin_roots
    ):
        raise SystemExit("MCP plugin roots are unavailable.")
    return build_persistent_mcp_runtime(
        harness_root=PROJECT_ROOT,
        manifest_path=PROJECT_ROOT / "config" / "mcp_capabilities.json",
        plugin_inventory_path=PROJECT_ROOT / "config" / "plugin_inventory.json",
        plugin_roots=[Path(item) for item in plugin_roots],
        state_root=state_root,
        environment=dict(os.environ),
    ).runtime


def load_request_file(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("database.inspect request is unavailable or invalid.") from exc
    if not isinstance(payload, Mapping) or set(payload) != {
        "connection_alias",
        "operation",
        "schema",
        "table",
    }:
        raise SystemExit("database.inspect request fields are invalid.")
    result = {key: str(payload.get(key) or "").strip() for key in payload}
    if not result["connection_alias"].endswith("_readonly"):
        raise SystemExit("connection_alias must select a configured readonly target.")
    if result["operation"] not in _OPERATIONS:
        raise SystemExit("database.inspect operation is invalid.")
    if result["operation"] == "schemas" and (result["schema"] or result["table"]):
        raise SystemExit("schemas does not accept schema or table.")
    if result["operation"] == "tables" and result["table"]:
        raise SystemExit("tables does not accept table.")
    if result["operation"] in {"columns", "constraints", "indexes", "foreign_keys"} and not (
        result["schema"] and result["table"]
    ):
        raise SystemExit("the selected operation requires schema and table.")
    return result


def build_capability_request(payload: Mapping[str, str]) -> CapabilityRequest:
    return CapabilityRequest.from_dict(
        {
            "schema_version": "his-capability-request.v1",
            "request_id": f"pg-catalog-{uuid.uuid4().hex}",
            "capability": "database.inspect",
            "provider": "postgresql",
            "mode": "preview",
            "mutation_level": "L1",
            "authorization": {
                "explicit": False,
                "scope": ["database:inspect"],
            },
            "input": {
                "connection_alias": payload["connection_alias"],
                "operation": payload["operation"],
                "schema": payload["schema"],
                "table": payload["table"],
            },
            "context": {},
        }
    )


if __name__ == "__main__":
    reexec_in_project_venv(PROJECT_ROOT)
    main()
