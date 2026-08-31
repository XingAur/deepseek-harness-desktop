"""Bounded GET-only GitLab MCP server."""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from mcp_readonly_common import (  # noqa: E402
    JsonRpcReadonlyServer,
    content_version,
    fallback_metadata,
    result_envelope,
    safe_metadata,
    sanitize_json,
    tool_result,
    utc_timestamp,
)


SERVER_NAME = "gitlab"
SERVER_VERSION = "1.0.0"
TOOL_NAME = "repository_read"
MAX_RESPONSE_BYTES = 192 * 1024
DEFAULT_CREDENTIALS_FILE = Path("/Users/lym/WorkCode/ai/apiKey/credentials.json")
_PROJECT = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{0,160}$")
_PATH = re.compile(r"^[A-Za-z0-9._/-]{0,512}$")
_OBJECT = re.compile(r"^[A-Za-z0-9._:-]{0,128}$")
_OPERATIONS = frozenset({"project", "repository_file", "commit", "merge_request"})

INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["project", "operation", "ref", "path", "object_id"],
    "properties": {
        "project": {"type": "string", "pattern": _PROJECT.pattern, "maxLength": 160},
        "operation": {"type": "string", "enum": sorted(_OPERATIONS)},
        "ref": {"type": "string", "pattern": _REF.pattern, "maxLength": 160},
        "path": {"type": "string", "pattern": _PATH.pattern, "maxLength": 512},
        "object_id": {"type": "string", "pattern": _OBJECT.pattern, "maxLength": 128},
    },
}
TOOLS = (
    {
        "name": TOOL_NAME,
        "description": "Read one bounded GitLab project, repository file, commit, or merge request.",
        "inputSchema": INPUT_SCHEMA,
        "annotations": {
            "title": "GitLab bounded read",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
)


@dataclass(frozen=True)
class GitLabHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


GitLabTransport = Callable[..., GitLabHttpResponse]


def _credentials_file() -> Path:
    configured = os.environ.get("HARNESS_CREDENTIALS_FILE", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_CREDENTIALS_FILE


def load_credentials(**_kwargs: object) -> Mapping[str, str]:
    path = _credentials_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("credentials unavailable")
    base_url = os.environ.get("GITLAB_BASE_URL") or payload.get("gitlab_base_url")
    access_token = os.environ.get("GITLAB_ACCESS_TOKEN") or payload.get("gitlab_access_token")
    if not isinstance(base_url, str) or not isinstance(access_token, str):
        raise ValueError("credentials unavailable")
    return {"base_url": base_url, "access_token": access_token}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _https_get(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: int,
    max_response_bytes: int,
) -> GitLabHttpResponse:
    if method != "GET":
        raise ValueError("GitLab mutation is forbidden")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = response.read(max_response_bytes + 1)
            if len(body) > max_response_bytes:
                raise ValueError("GitLab response too large")
            return GitLabHttpResponse(
                status_code=int(response.status),
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=body,
            )
    except urllib.error.HTTPError as error:
        body = error.read(max_response_bytes + 1)
        return GitLabHttpResponse(int(error.code), {}, body[:max_response_bytes])


def _base_url(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("invalid GitLab base URL")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("invalid GitLab base URL")
    return f"https://{parsed.hostname}" + (f":{parsed.port}" if parsed.port not in {None, 443} else "")


def _arguments(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(INPUT_SCHEMA["required"]):
        raise ValueError("invalid arguments")
    result = {key: value.get(key) for key in INPUT_SCHEMA["required"]}
    if (
        not isinstance(result["project"], str)
        or _PROJECT.fullmatch(result["project"]) is None
        or result["operation"] not in _OPERATIONS
        or not isinstance(result["ref"], str)
        or _REF.fullmatch(result["ref"]) is None
        or not isinstance(result["path"], str)
        or _PATH.fullmatch(result["path"]) is None
        or not isinstance(result["object_id"], str)
        or _OBJECT.fullmatch(result["object_id"]) is None
    ):
        raise ValueError("invalid arguments")
    operation = str(result["operation"])
    required = {
        "project": (not result["ref"] and not result["path"] and not result["object_id"]),
        "repository_file": bool(result["ref"] and result["path"] and not result["object_id"]),
        "commit": bool(result["object_id"] and not result["ref"] and not result["path"]),
        "merge_request": bool(result["object_id"] and not result["ref"] and not result["path"]),
    }
    if not required[operation] or ".." in str(result["path"]).split("/"):
        raise ValueError("invalid arguments")
    return {key: str(item) for key, item in result.items()}


def _route(base_url: str, arguments: Mapping[str, str]) -> str:
    project = urllib.parse.quote(arguments["project"], safe="")
    operation = arguments["operation"]
    root = f"{base_url}/api/v4/projects/{project}"
    if operation == "project":
        return root
    if operation == "repository_file":
        path = urllib.parse.quote(arguments["path"], safe="")
        ref = urllib.parse.urlencode({"ref": arguments["ref"]})
        return f"{root}/repository/files/{path}/raw?{ref}"
    if operation == "commit":
        return f"{root}/repository/commits/{urllib.parse.quote(arguments['object_id'], safe='')}"
    return f"{root}/merge_requests/{urllib.parse.quote(arguments['object_id'], safe='')}"


class GitLabMcpServer(JsonRpcReadonlyServer):
    server_name = SERVER_NAME
    server_version = SERVER_VERSION
    tools = TOOLS

    def __init__(
        self,
        *,
        credential_loader: Optional[Callable[..., Mapping[str, str]]] = None,
        transport: Optional[GitLabTransport] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.credential_loader = credential_loader or load_credentials
        self.transport = transport or _https_get
        self.now = now or (lambda: datetime.now(timezone.utc))

    def call_tool(self, name: str, arguments: object, metadata: object = None) -> dict[str, object]:
        request_id, trace_id = fallback_metadata(metadata)
        try:
            request_id, trace_id = safe_metadata(metadata)
            checked = _arguments(arguments)
        except (TypeError, ValueError):
            return tool_result(self._failure(request_id, trace_id, "invalid", "INVALID_TOOL_ARGUMENTS", False))
        if name != TOOL_NAME:
            return tool_result(self._failure(request_id, trace_id, "invalid", "UNKNOWN_TOOL", False))
        try:
            credentials = self.credential_loader(credential_kind="read")
            base_url = _base_url(credentials.get("base_url"))
            token = credentials.get("access_token")
            if not isinstance(token, str) or not token:
                raise ValueError("credentials unavailable")
        except Exception:
            return tool_result(self._failure(request_id, trace_id, "unavailable", "GITLAB_CREDENTIAL_UNAVAILABLE", False))
        try:
            response = self.transport(
                method="GET",
                url=_route(base_url, checked),
                headers={"PRIVATE-TOKEN": token, "Accept": "application/json"},
                timeout_seconds=20,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError("GitLab read failed")
            if checked["operation"] == "repository_file":
                payload: object = {"content": response.body.decode("utf-8")}
            else:
                payload = json.loads(response.body.decode("utf-8"))
            safe = sanitize_json(payload, secrets=(token,))
            if not isinstance(safe, Mapping):
                safe = {"items": safe}
            data = {"operation": checked["operation"], "project": checked["project"], "result": safe}
            version = content_version(data)
            observed_at = utc_timestamp(self.now())
            envelope = result_envelope(
                request_id=request_id,
                trace_id=trace_id,
                capability="gitlab.read",
                provider="gitlab",
                server=SERVER_NAME,
                tool=TOOL_NAME,
                server_version=SERVER_VERSION,
                status="success",
                data=data,
                object_id=checked["project"],
                version=version,
                observed_at=observed_at,
            )
            return tool_result(envelope)
        except Exception:
            return tool_result(self._failure(request_id, trace_id, "failed", "GITLAB_READ_FAILED", True))

    @staticmethod
    def _failure(request_id: str, trace_id: str, status: str, code: str, retryable: bool) -> dict[str, object]:
        return result_envelope(
            request_id=request_id,
            trace_id=trace_id,
            capability="gitlab.read",
            provider="gitlab",
            server=SERVER_NAME,
            tool=TOOL_NAME,
            server_version=SERVER_VERSION,
            status=status,
            data={},
            error_code=code,
            retryable=retryable,
            recovery="Check the frozen GitLab read connector configuration and bounded target.",
        )


def main() -> int:
    GitLabMcpServer().serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
