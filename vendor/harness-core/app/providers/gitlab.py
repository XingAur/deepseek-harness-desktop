from __future__ import annotations

import json
import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest
from app.sensitive_text import contains_sensitive_text


GITLAB_MAX_RESPONSE_BYTES = 65_536
_CODE_EVIDENCE_READ_ACTIONS = frozenset((
    "gitlab.repository.file.read",
    "gitlab.commit.read",
    "gitlab.commit.diff.read",
    "gitlab.compare.read",
    "gitlab.merge_request.commits.read",
    "gitlab.merge_request.diffs.read",
    "gitlab.pipeline.jobs.read",
))
_ALLOWED_ACTIONS = frozenset((
    "project.read", "merge_request.read", "merge_request.comment.write",
    "merge_request.create", *_CODE_EVIDENCE_READ_ACTIONS,
))
_ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_PROJECT_PART = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}\Z")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SENSITIVE_FILE_NAMES = frozenset((".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"))
_SENSITIVE_FILE_SUFFIXES = frozenset((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))


@dataclass(frozen=True)
class GitLabHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


GitLabTransport = Callable[..., GitLabHttpResponse]


class GitLabProviderAdapter:
    """GitLab adapter limited to fixed HTTPS hosts and reviewed REST routes."""

    def __init__(self, host_aliases: Mapping[str, str] | None = None, *, transport: GitLabTransport | None = None, simulated: bool | None = None) -> None:
        supplied = host_aliases or {}
        if not isinstance(supplied, Mapping):
            raise TypeError("gitlab_host_aliases_must_be_mapping")
        self._hosts = {self._host_alias(alias): self._host_url(url) for alias, url in supplied.items()}
        if transport is not None and simulated is not True:
            raise ValueError("gitlab_simulated_transport_required")
        if transport is None and simulated not in {None, False}:
            raise ValueError("gitlab_simulated_transport_required")
        self._transport = transport or _https_transport
        self._simulated = transport is not None

    def normalize_target_alias(self, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("gitlab_target_invalid")
        if not value.startswith("gl-h"):
            raise ValueError("gitlab_target_invalid")
        return self._parse_target_alias(value)

    def normalize_request_target(self, parameters: Mapping[str, object]) -> str:
        if not isinstance(parameters, Mapping):
            raise ValueError("gitlab_parameters_invalid")
        host = self._host_alias(parameters.get("host_alias"))
        if host not in self._hosts:
            raise ValueError("gitlab_host_not_allowlisted")
        project = self._project_alias(parameters.get("project_alias"))
        iid = self._iid(parameters["merge_request_iid"]) if "merge_request_iid" in parameters else None
        return self._target_alias(host, project, iid)

    def render_plan(self, request: ProviderExecutionRequest) -> dict[str, object]:
        action, host, project, values = self._validated_parameters(request.action, request.parameters)
        change: dict[str, object] = {"field": "read", "after": "no_remote_change"}
        if action == "merge_request.comment.write":
            change = {"field": "comment", "after": values["body"]}
        elif action == "merge_request.create":
            change = {"field": "merge_request", "source_branch": values["source_branch"], "target_branch": values["target_branch"], "title": values["title"]}
        return {"provider": "gitlab", "action": action, "target_alias": self._target_alias(host, project, values.get("merge_request_iid")), "change": change}

    def execute(self, request: ProviderExecutionRequest, context: ProviderExecutionContext) -> Mapping[str, object]:
        action, host, project, values = self._validated_parameters(request.action, request.parameters)
        token = self._token(context)
        timeout = values["timeout_seconds"]
        if action == "project.read":
            return self._read_result("project", self._request("GET", host, self._project_path(project), token, None, timeout, context, self._target_alias(host, project, None)))
        if action == "merge_request.read":
            return self._read_result("merge_request", self._request("GET", host, self._mr_path(project, values["merge_request_iid"]), token, None, timeout, context, self._target_alias(host, project, values["merge_request_iid"])))
        if action in _CODE_EVIDENCE_READ_ACTIONS:
            path, target_iid = self._code_evidence_path(action, project, values)
            response = self._request(
                "GET", host, path, token, None, timeout, context,
                self._target_alias(host, project, target_iid),
            )
            return self._code_evidence_result(action, response)
        if action == "merge_request.comment.write":
            response = self._request("POST", host, self._mr_path(project, values["merge_request_iid"]) + "/notes", token, {"body": values["body"]}, timeout, context, self._target_alias(host, project, values["merge_request_iid"]))
            context.set_read_back_reference(action, str(_positive_int(response.payload.get("id"))))
            return self._write_result("comment", response)
        response = self._request("POST", host, self._project_path(project) + "/merge_requests", token, {"source_branch": values["source_branch"], "target_branch": values["target_branch"], "title": values["title"]}, timeout, context, self._target_alias(host, project, None))
        context.set_read_back_reference(action, str(_positive_int(response.payload.get("iid"))))
        return self._write_result("merge_request", response)

    def verify(self, verifier_action: str, original_write_action: str, request: ProviderExecutionRequest, target_alias: str, context: ProviderExecutionContext) -> Literal["verified_applied", "verified_not_applied", "unknown"]:
        action, host, project, values = self._validated_parameters(original_write_action, request.parameters)
        expected_target = self._target_alias(host, project, values.get("merge_request_iid"))
        if verifier_action != "merge_request.read" or self.normalize_target_alias(target_alias) != expected_target:
            raise ValueError("gitlab_target_mismatch")
        receipt = context.read_back_reference(action)
        if not receipt:
            return "unknown"
        token = self._token(context)
        timeout = values["timeout_seconds"]
        if action == "merge_request.comment.write":
            response = self._request("GET", host, self._mr_path(project, values["merge_request_iid"]) + f"/notes/{receipt}", token, None, timeout, context, expected_target)
            return "verified_applied" if str(response.payload.get("id", "")) == receipt and response.payload.get("body") == values["body"] else "unknown"
        if action == "merge_request.create":
            created_iid = self._target_iid(receipt)
            actual_target = self._target_alias(host, project, created_iid)
            response = self._request("GET", host, self._mr_path(project, created_iid), token, None, timeout, context, actual_target)
            if str(response.payload.get("iid", "")) != receipt:
                return "unknown"
            return "verified_applied" if all(response.payload.get(key) == values[key] for key in ("source_branch", "target_branch", "title")) else "unknown"
        return "unknown"

    def read_back_target_alias(self, action: str, parameters: Mapping[str, object], context: ProviderExecutionContext) -> str:
        _action, host, project, values = self._validated_parameters(action, parameters)
        iid = values.get("merge_request_iid")
        if action == "merge_request.create":
            iid = self._target_iid(context.read_back_reference(action))
        return self._target_alias(host, project, iid)

    def _validated_parameters(self, action_value: object, parameters: Mapping[str, object]) -> tuple[str, str, str, dict[str, object]]:
        if not isinstance(parameters, Mapping):
            raise ValueError("gitlab_parameters_invalid")
        action = action_value if isinstance(action_value, str) else ""
        if action not in _ALLOWED_ACTIONS:
            raise ValueError("gitlab_action_not_allowed")
        allowed = {"host_alias", "project_alias", "timeout_seconds"}
        if action in {"merge_request.read", "merge_request.comment.write"}:
            allowed.add("merge_request_iid")
        if action == "merge_request.comment.write":
            allowed.add("body")
        if action == "merge_request.create":
            allowed.update(("source_branch", "target_branch", "title"))
        if action == "gitlab.repository.file.read":
            allowed.update(("file_path", "ref"))
        if action in {"gitlab.commit.read", "gitlab.commit.diff.read"}:
            allowed.add("sha")
        if action == "gitlab.compare.read":
            allowed.update(("from_ref", "to_ref"))
        if action in {"gitlab.merge_request.commits.read", "gitlab.merge_request.diffs.read"}:
            allowed.add("merge_request_iid")
        if action == "gitlab.pipeline.jobs.read":
            allowed.add("pipeline_id")
        if action in {
            "gitlab.commit.diff.read",
            "gitlab.merge_request.commits.read",
            "gitlab.merge_request.diffs.read",
            "gitlab.pipeline.jobs.read",
        }:
            allowed.update(("page", "per_page"))
        if set(parameters) - allowed or not {"host_alias", "project_alias"}.issubset(parameters):
            raise ValueError("gitlab_parameters_invalid")
        host = self._host_alias(parameters["host_alias"])
        if host not in self._hosts:
            raise ValueError("gitlab_host_not_allowlisted")
        project = self._project_alias(parameters["project_alias"])
        timeout = parameters.get("timeout_seconds", 15)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1 or timeout > 20:
            raise ValueError("gitlab_parameters_invalid")
        values: dict[str, object] = {"timeout_seconds": timeout}
        if action in {"merge_request.read", "merge_request.comment.write"}:
            values["merge_request_iid"] = self._iid(parameters.get("merge_request_iid"))
        if action == "merge_request.comment.write":
            values["body"] = self._text(parameters.get("body"), maximum=2_000)
        if action == "merge_request.create":
            values["source_branch"] = self._branch(parameters.get("source_branch"))
            values["target_branch"] = self._branch(parameters.get("target_branch"))
            if values["source_branch"] == values["target_branch"]:
                raise ValueError("gitlab_parameters_invalid")
            values["title"] = self._text(parameters.get("title"), maximum=256)
        if action == "gitlab.repository.file.read":
            values["file_path"] = self._file_path(parameters.get("file_path"))
            values["ref"] = self._ref(parameters.get("ref"))
        if action in {"gitlab.commit.read", "gitlab.commit.diff.read"}:
            values["sha"] = self._sha(parameters.get("sha"))
        if action == "gitlab.compare.read":
            values["from_ref"] = self._ref(parameters.get("from_ref"))
            values["to_ref"] = self._ref(parameters.get("to_ref"))
            if values["from_ref"] == values["to_ref"]:
                raise ValueError("gitlab_parameters_invalid")
        if action in {"gitlab.merge_request.commits.read", "gitlab.merge_request.diffs.read"}:
            values["merge_request_iid"] = self._iid(parameters.get("merge_request_iid"))
        if action == "gitlab.pipeline.jobs.read":
            values["pipeline_id"] = self._positive_id(parameters.get("pipeline_id"), "gitlab_pipeline_id_invalid")
        if action in {
            "gitlab.commit.diff.read",
            "gitlab.merge_request.commits.read",
            "gitlab.merge_request.diffs.read",
            "gitlab.pipeline.jobs.read",
        }:
            values["page"] = self._page(parameters.get("page"))
            values["per_page"] = self._per_page(parameters.get("per_page"))
        return action, host, project, values

    @staticmethod
    def _host_alias(value: object) -> str:
        if not isinstance(value, str) or _ALIAS.fullmatch(value) is None:
            raise ValueError("gitlab_host_alias_invalid")
        return value

    @staticmethod
    def _host_url(value: object) -> str:
        if not isinstance(value, str) or value != value.strip() or not value.startswith("https://") or any(character.isspace() for character in value):
            raise ValueError("gitlab_host_invalid")
        parsed = urllib.parse.urlsplit(value)
        try: port = parsed.port
        except ValueError: raise ValueError("gitlab_host_invalid") from None
        hostname = parsed.hostname
        raw_authority = value[len("https://"):].split("/", 1)[0]
        if (parsed.scheme != "https" or not parsed.netloc or not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or raw_authority != raw_authority.lower() or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in hostname.split("."))
                or port is not None and not 1 <= port <= 65535):
            raise ValueError("gitlab_host_invalid")
        return "https://" + hostname + (f":{port}" if port not in {None, 443} else "")

    @staticmethod
    def _project_alias(value: object) -> str:
        if not isinstance(value, str) or value.count("/") != 1 or value != value.strip():
            raise ValueError("gitlab_project_alias_invalid")
        group, project = value.split("/")
        if _PROJECT_PART.fullmatch(group) is None or _PROJECT_PART.fullmatch(project) is None:
            raise ValueError("gitlab_project_alias_invalid")
        return value

    @staticmethod
    def _iid(value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 2_147_483_647:
            raise ValueError("gitlab_merge_request_iid_invalid")
        return value

    @staticmethod
    def _positive_id(value: object, error: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 2_147_483_647:
            raise ValueError(error)
        return value

    @staticmethod
    def _page(value: object) -> int:
        return GitLabProviderAdapter._positive_id(value, "gitlab_page_invalid")

    @staticmethod
    def _per_page(value: object) -> int:
        result = GitLabProviderAdapter._positive_id(value, "gitlab_per_page_invalid")
        if result > 100:
            raise ValueError("gitlab_per_page_invalid")
        return result

    @staticmethod
    def _sha(value: object) -> str:
        if not isinstance(value, str) or _SHA.fullmatch(value) is None:
            raise ValueError("gitlab_commit_sha_invalid")
        return value

    @classmethod
    def _ref(cls, value: object) -> str:
        if isinstance(value, str) and _SHA.fullmatch(value) is not None:
            return value
        return cls._branch(value)

    @staticmethod
    def _file_path(value: object) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value.encode("utf-8")) > 512
            or value.startswith("/")
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("gitlab_file_path_invalid")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("gitlab_file_path_invalid")
        name = parts[-1].lower()
        if (
            name in _SENSITIVE_FILE_NAMES
            or name.startswith(".env.")
            or any(name.endswith(suffix) for suffix in _SENSITIVE_FILE_SUFFIXES)
        ):
            raise ValueError("gitlab_sensitive_file_blocked")
        return value

    @classmethod
    def _target_iid(cls, value: object) -> int:
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise ValueError("gitlab_merge_request_iid_invalid")
        return cls._iid(int(value))

    @staticmethod
    def _branch(value: object) -> str:
        if not isinstance(value, str) or _BRANCH.fullmatch(value) is None or ".." in value or "//" in value or value.startswith("-") or value.endswith(("/", ".")):
            raise ValueError("gitlab_branch_invalid")
        return value

    @staticmethod
    def _text(value: object, *, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip() or len(value.encode("utf-8")) > maximum or any(ord(character) < 32 and character not in "\n\t" for character in value) or contains_sensitive_text(value):
            raise ValueError("gitlab_text_invalid")
        return value

    @staticmethod
    def _project_path(project: str) -> str:
        return "/api/v4/projects/" + urllib.parse.quote(project, safe="")

    def _mr_path(self, project: str, iid: object) -> str:
        return self._project_path(project) + f"/merge_requests/{self._iid(iid)}"

    def _code_evidence_path(
        self, action: str, project: str, values: Mapping[str, object]
    ) -> tuple[str, int | None]:
        base = self._project_path(project)
        if action == "gitlab.repository.file.read":
            query = urllib.parse.urlencode((("ref", str(values["ref"])),))
            return base + "/repository/files/" + urllib.parse.quote(str(values["file_path"]), safe="") + "?" + query, None
        if action == "gitlab.commit.read":
            return base + "/repository/commits/" + str(values["sha"]), None
        if action == "gitlab.commit.diff.read":
            query = self._pagination_query(values)
            return base + "/repository/commits/" + str(values["sha"]) + "/diff?" + query, None
        if action == "gitlab.compare.read":
            query = urllib.parse.urlencode((("from", str(values["from_ref"])), ("to", str(values["to_ref"]))))
            return base + "/repository/compare?" + query, None
        if action in {"gitlab.merge_request.commits.read", "gitlab.merge_request.diffs.read"}:
            iid = self._iid(values["merge_request_iid"])
            suffix = "commits" if action.endswith("commits.read") else "diffs"
            return self._mr_path(project, iid) + "/" + suffix + "?" + self._pagination_query(values), iid
        pipeline_id = self._positive_id(values["pipeline_id"], "gitlab_pipeline_id_invalid")
        return base + f"/pipelines/{pipeline_id}/jobs?" + self._pagination_query(values), None

    @staticmethod
    def _pagination_query(values: Mapping[str, object]) -> str:
        return urllib.parse.urlencode((("page", str(values["page"])), ("per_page", str(values["per_page"]))))

    @staticmethod
    def _target_alias(host: str, project: str, iid: object | None) -> str:
        group, name = project.split("/", 1)
        value = f"gl-h{len(host)}-{host}-g{len(group)}-{group}-p{len(name)}-{name}" + (f"-m{iid}" if iid is not None else "")
        if len(value) > 127:
            raise ValueError("gitlab_target_invalid")
        return value

    @classmethod
    def _parse_target_alias(cls, value: str) -> str:
        """Accept only a canonical, length-delimited resource identity.

        A regex alone is not enough here: it would accept a fabricated alias
        whose component boundaries no longer map to the project used by the
        plan.  Length prefixes keep `a.b/c` distinct from `a/b.c`.
        """

        def take(prefix: str, offset: int) -> tuple[str, int]:
            if not value.startswith(prefix, offset):
                raise ValueError("gitlab_target_invalid")
            cursor = offset + len(prefix)
            end = value.find("-", cursor)
            if end < cursor + 1 or not value[cursor:end].isdigit() or value[cursor] == "0":
                raise ValueError("gitlab_target_invalid")
            length = int(value[cursor:end])
            start = end + 1
            stop = start + length
            if stop > len(value):
                raise ValueError("gitlab_target_invalid")
            return value[start:stop], stop

        host, cursor = take("gl-h", 0)
        group, cursor = take("-g", cursor)
        project, cursor = take("-p", cursor)
        iid: int | None = None
        if cursor != len(value):
            if not value.startswith("-m", cursor):
                raise ValueError("gitlab_target_invalid")
            try:
                iid = cls._target_iid(value[cursor + 2:])
            except ValueError:
                raise ValueError("gitlab_target_invalid") from None
        try:
            host = cls._host_alias(host)
            project_alias = cls._project_alias(group + "/" + project)
            return cls._target_alias(host, project_alias, iid)
        except ValueError:
            raise ValueError("gitlab_target_invalid") from None

    @staticmethod
    def _token(context: ProviderExecutionContext) -> str:
        if not context.network_allowed:
            raise PermissionError("gitlab_network_not_allowed")
        return context.credential("access_token")

    def _request(self, method: Literal["GET", "POST"], host: str, path: str, token: str, payload: Mapping[str, object] | None, timeout_seconds: int, context: ProviderExecutionContext, target_alias: str) -> "_ParsedResponse":
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", "Private-Token": token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        context.record_network_dispatch(target_alias, simulated=self._simulated)
        response = self._transport(method=method, url=self._hosts[host] + path, headers=headers, body=body, timeout_seconds=timeout_seconds)
        if not isinstance(response, GitLabHttpResponse) or response.status_code < 200 or response.status_code >= 300 or len(response.body) > GITLAB_MAX_RESPONSE_BYTES:
            raise RuntimeError("gitlab_request_failed")
        try:
            payload_value = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("gitlab_response_invalid") from None
        if not isinstance(payload_value, (Mapping, list)) or (
            isinstance(payload_value, list)
            and any(not isinstance(item, Mapping) for item in payload_value)
        ):
            raise RuntimeError("gitlab_response_invalid")
        normalized_payload: object = (
            dict(payload_value)
            if isinstance(payload_value, Mapping)
            else [dict(item) for item in payload_value]
        )
        return _ParsedResponse(normalized_payload, dict(response.headers))

    def _read_result(self, kind: str, response: "_ParsedResponse") -> dict[str, object]:
        if not isinstance(response.payload, Mapping):
            raise RuntimeError("gitlab_response_invalid")
        result: dict[str, object] = {"source": "gitlab", "kind": kind, "execution_provenance": "simulated" if self._simulated else "live"}
        if kind == "project":
            result["project_id_present"] = isinstance(response.payload.get("id"), int)
        else:
            result["merge_request_iid"] = response.payload.get("iid") if isinstance(response.payload.get("iid"), int) else None
            result["state"] = response.payload.get("state") if response.payload.get("state") in {"opened", "closed", "merged", "locked"} else "unknown"
            pipeline = response.payload.get("head_pipeline")
            result["head_pipeline_id"] = (
                pipeline.get("id")
                if isinstance(pipeline, Mapping)
                and isinstance(pipeline.get("id"), int)
                and not isinstance(pipeline.get("id"), bool)
                and pipeline["id"] > 0
                else None
            )
        return result

    def _code_evidence_result(self, action: str, response: "_ParsedResponse") -> dict[str, object]:
        encoded = json.dumps(
            response.payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > GITLAB_MAX_RESPONSE_BYTES:
            raise RuntimeError("gitlab_response_invalid")
        kind = {
            "gitlab.repository.file.read": "repository_file",
            "gitlab.commit.read": "commit",
            "gitlab.commit.diff.read": "commit_diff",
            "gitlab.compare.read": "compare",
            "gitlab.merge_request.commits.read": "merge_request_commits",
            "gitlab.merge_request.diffs.read": "merge_request_diffs",
            "gitlab.pipeline.jobs.read": "pipeline_jobs",
        }[action]
        next_page = next(
            (
                value.strip()
                for key, value in response.headers.items()
                if key.lower() == "x-next-page" and isinstance(value, str)
            ),
            "",
        )
        return {
            "source": "gitlab",
            "kind": kind,
            "payload_sha256": hashlib.sha256(encoded).hexdigest(),
            "payload_bytes": len(encoded),
            "item_count": len(response.payload) if isinstance(response.payload, list) else 1,
            "truncated": bool(next_page),
            "execution_provenance": "simulated" if self._simulated else "live",
            "__local_response__": {
                "payload": response.payload,
                "truncated": bool(next_page),
            },
        }

    def _write_result(self, kind: str, response: "_ParsedResponse") -> dict[str, object]:
        receipt_key = "id" if kind == "comment" else "iid"
        return {"source": "gitlab", "kind": kind + "_write", "receipt_present": isinstance(response.payload.get(receipt_key), int), "execution_provenance": "simulated" if self._simulated else "live"}


@dataclass(frozen=True)
class _ParsedResponse:
    payload: object
    headers: dict[str, str]


def canonical_gitlab_target(action: object, parameters: Mapping[str, object]) -> str:
    """Return the same canonical target identity used by GitLab execution.

    Planning has no transport or credentials.  It still has to bind the exact
    host/project/MR identity that an adapter will later validate against its
    configured host allowlist, so this helper deliberately validates only the
    public resource grammar and shares the adapter's target encoder.
    """

    if not isinstance(parameters, Mapping) or not isinstance(action, str):
        raise ValueError("gitlab_target_invalid")
    try:
        host = GitLabProviderAdapter._host_alias(parameters.get("host_alias"))
        project = GitLabProviderAdapter._project_alias(parameters.get("project_alias"))
        if action in {
            "merge_request.read", "merge_request.comment.write",
            "gitlab.merge_request.commits.read", "gitlab.merge_request.diffs.read",
        }:
            iid: int | None = GitLabProviderAdapter._iid(parameters.get("merge_request_iid"))
        elif action in {"project.read", "merge_request.create", *_CODE_EVIDENCE_READ_ACTIONS}:
            iid = None
        else:
            raise ValueError("gitlab_target_invalid")
        return GitLabProviderAdapter._target_alias(host, project, iid)
    except ValueError:
        raise ValueError("gitlab_target_invalid") from None


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RuntimeError("gitlab_response_invalid")
    return value


def _https_transport(*, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout_seconds: int) -> GitLabHttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    # urllib's default opener imports HTTPS_PROXY/ALL_PROXY from the process.
    # Provider credentials must be delivered only to the allowlisted GitLab
    # authority encoded in ``url``, never to an ambient workstation proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as opened:
            return GitLabHttpResponse(int(opened.status), dict(opened.headers.items()), opened.read(GITLAB_MAX_RESPONSE_BYTES + 1))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
        raise RuntimeError("gitlab_request_failed") from None


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None
