from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODE = os.environ.get("MCP_FIXTURE_MODE", "healthy")
SERVER_NAME = "wrong" if MODE == "wrong_server" else "fixture"


def response(identifier: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def tool() -> dict[str, object]:
    annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    if MODE == "unsafe_tool":
        annotations["readOnlyHint"] = False
    return {
        "name": "fixture_read",
        "description": "Offline fixture read",
        "inputSchema": {
            "type": "object",
            "additionalProperties": True,
        },
        "annotations": annotations,
    }


def envelope(message: dict[str, object]) -> dict[str, object]:
    params = message.get("params", {})
    meta = params.get("_meta", {}) if isinstance(params, dict) else {}
    request_id = meta.get("request_id", "") if isinstance(meta, dict) else ""
    trace_id = meta.get("trace_id", "") if isinstance(meta, dict) else ""
    now = datetime.now(timezone.utc)
    return {
        "schema_version": "his-mcp-result-envelope.v1",
        "request_id": request_id,
        "capability": "workitem.read",
        "provider": "yunxiao",
        "status": "success",
        "data": {"fixture": "ok"},
        "source": {
            "system": "fixture",
            "object_id": "DFHIS-1",
            "version": "fixture-v1",
            "observed_at": now.isoformat().replace("+00:00", "Z"),
        },
        "freshness": {
            "status": "fresh",
            "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        },
        "pagination": {"truncated": False, "next_cursor": ""},
        "redaction": {"applied": False, "fields": []},
        "evidence_ref": "fixture:DFHIS-1:fixture-v1",
        "error": {"code": "", "retryable": False, "recovery": ""},
        "trace": {
            "mcp_server": "fixture",
            "tool": "fixture_read",
            "server_version": "1.0.0",
            "trace_id": trace_id,
        },
    }


def emit(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    if MODE == "stderr_secret":
        sys.stderr.write("SENTINEL_MCP_STDERR_SECRET\n")
        sys.stderr.flush()
        return 7
    if MODE == "oversized_stdout":
        sys.stdout.write("x" * 200_000)
        sys.stdout.flush()
        return 0
    if MODE == "oversized_stderr":
        sys.stderr.write("x" * 200_000)
        sys.stderr.flush()
        return 0
    if MODE == "hang_child":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        pid_file = os.environ.get("MCP_FIXTURE_PID_FILE", "")
        if pid_file:
            Path(pid_file).write_text(str(child.pid), encoding="utf-8")
        time.sleep(60)
        return 0

    count = 0
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        count += 1
        if MODE == "malformed_json":
            sys.stdout.write("{not-json\n")
            sys.stdout.flush()
            continue
        message = json.loads(raw_line)
        identifier = message.get("id")
        method = message.get("method")
        if method == "initialize":
            emit(
                response(
                    identifier,
                    {
                        "protocolVersion": message.get("params", {}).get("protocolVersion"),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
                    },
                )
            )
        elif method == "tools/list":
            emit(response(identifier, {"tools": [tool()]}))
        elif method == "tools/call":
            if MODE == "call_error":
                emit({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32000, "message": "fixture"}})
            else:
                emit(
                    response(
                        identifier,
                        {
                            "content": [{"type": "text", "text": "metadata-only"}],
                            "structuredContent": envelope(message),
                            "isError": False,
                        },
                    )
                )
        if MODE == "extra_response" and count == 3:
            emit(response("extra", {}))
    return 9 if MODE == "nonzero" else 0


if __name__ == "__main__":
    raise SystemExit(main())
