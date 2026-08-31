from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest
from app.sensitive_text import contains_sensitive_text


GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_MAX_RESPONSE_BYTES = 65_536
_CODE_EVIDENCE_READ_ACTIONS = frozenset((
    "github.repository.file.read",
    "github.commit.read",
    "github.commit.diff.read",
    "github.compare.read",
    "github.pull_request.commits.read",
    "github.pull_request.diffs.read",
    "github.actions.run.jobs.read",
))
_WRITE_ACTIONS = frozenset((
    "github.pull_request.comment.write",
    "github.pull_request.create",
))
_ALLOWED_ACTIONS = frozenset((
    "github.connection_test",
    "github.repository.read",
    "github.issue.read",
    "github.pull_request.read",
    *_CODE_EVIDENCE_READ_ACTIONS,
    *_WRITE_ACTIONS,
))
_CONNECTION_TARGET = "github.connection"
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SENSITIVE_FILE_NAMES = frozenset((".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"))
_SENSITIVE_FILE_SUFFIXES = frozenset((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))


@dataclass(frozen=True)
class GitHubHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


GitHubTransport = Callable[..., GitHubHttpResponse]


class GitHubProviderAdapter:
    """Fixed-host GitHub REST adapter with governed PR writes.

    The host is not configurable. Reads expose bounded evidence; PR creation
    and comments execute only through the Manager's one-use remote-write
    authorization and exact read-back verification contract.
    """

    def __init__(
        self,
        *,
        transport: GitHubTransport | None = None,
        simulated: bool | None = None,
    ) -> None:
        if transport is not None and simulated is not True:
            raise ValueError("github_simulated_transport_required")
        if transport is None and simulated not in {None, False}:
            raise ValueError("github_simulated_transport_required")
        self._transport = transport or _https_transport
        self._simulated = transport is not None

    def normalize_target_alias(self, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("github_target_invalid")
        if value == _CONNECTION_TARGET:
            return value
        return self._parse_target_alias(value)

    def normalize_request_target(self, parameters: Mapping[str, object]) -> str:
        if isinstance(parameters, Mapping) and not {"owner", "repository"}.intersection(parameters):
            self._validated_parameters("github.connection_test", parameters)
            return _CONNECTION_TARGET
        if not isinstance(parameters, Mapping):
            raise ValueError("github_parameters_invalid")
        owner = self._owner(parameters.get("owner"))
        repository = self._repository(parameters.get("repository"))
        pull_request_number = (
            self._positive_number(parameters.get("pull_request_number"), "github_pull_request_number_invalid")
            if "pull_request_number" in parameters else None
        )
        issue_number = (
            self._positive_number(parameters.get("issue_number"), "github_issue_number_invalid")
            if "issue_number" in parameters else None
        )
        workflow_run_id = (
            self._positive_number(parameters.get("workflow_run_id"), "github_workflow_run_id_invalid")
            if "workflow_run_id" in parameters else None
        )
        if sum(value is not None for value in (pull_request_number, issue_number, workflow_run_id)) > 1:
            raise ValueError("github_parameters_invalid")
        return self._target_alias(
            owner,
            repository,
            pull_request_number=pull_request_number,
            issue_number=issue_number,
            workflow_run_id=workflow_run_id,
        )

    def render_plan(self, request: ProviderExecutionRequest) -> dict[str, object]:
        action, owner, repository, _values = self._validated_parameters(
            request.action, request.parameters
        )
        target_alias = self._action_target(action, owner, repository, _values)
        change: dict[str, object] = {"field": "read", "after": "no_remote_change"}
        if action == "github.pull_request.comment.write":
            change = {"field": "comment", "after": _values["body"]}
        elif action == "github.pull_request.create":
            change = {
                "field": "pull_request",
                "head": _values["head"],
                "base": _values["base"],
                "title": _values["title"],
            }
        return {
            "provider": "github",
            "action": action,
            "target_alias": target_alias,
            "change": change,
        }

    def execute(
        self, request: ProviderExecutionRequest, context: ProviderExecutionContext
    ) -> Mapping[str, object]:
        action, owner, repository, values = self._validated_parameters(
            request.action, request.parameters
        )
        target_alias = self._action_target(action, owner, repository, values)
        token = self._token(context)
        if action == "github.connection_test":
            resource, path = "connection", "/rate_limit"
        elif action == "github.repository.read":
            if owner is None or repository is None:  # pragma: no cover - validated above
                raise ValueError("github_parameters_invalid")
            resource, path = "repository", f"/repos/{owner}/{repository}"
        elif action == "github.issue.read":
            if owner is None or repository is None:  # pragma: no cover - validated above
                raise ValueError("github_parameters_invalid")
            resource = "issue"
            path = f"/repos/{owner}/{repository}/issues/{values['issue_number']}"
        elif action == "github.pull_request.read":
            if owner is None or repository is None:  # pragma: no cover - validated above
                raise ValueError("github_parameters_invalid")
            resource = "pull_request"
            path = f"/repos/{owner}/{repository}/pulls/{values['pull_request_number']}"
        elif action in _CODE_EVIDENCE_READ_ACTIONS:
            if owner is None or repository is None:  # pragma: no cover - validated above
                raise ValueError("github_parameters_invalid")
            resource = "code_evidence"
            path = self._code_evidence_path(action, owner, repository, values)
        elif action == "github.pull_request.comment.write":
            if owner is None or repository is None:  # pragma: no cover - validated above
                raise ValueError("github_parameters_invalid")
            response = self._request(
                method="POST",
                path=f"/repos/{owner}/{repository}/issues/{values['pull_request_number']}/comments",
                token=token,
                payload={"body": values["body"]},
                timeout_seconds=int(values["timeout_seconds"]),
                context=context,
                target_alias=target_alias,
            )
            parsed = self._parsed_payload(response, context)
            receipt = parsed.get("id") if isinstance(parsed, Mapping) else None
            if not isinstance(receipt, int) or isinstance(receipt, bool) or receipt < 1:
                raise RuntimeError("github_response_invalid")
            context.set_read_back_reference(action, str(receipt))
            return self._write_result("pull_request_comment", receipt, response)
        else:
            if owner is None or repository is None:  # pragma: no cover - validated above
                raise ValueError("github_parameters_invalid")
            response = self._request(
                method="POST",
                path=f"/repos/{owner}/{repository}/pulls",
                token=token,
                payload={"head": values["head"], "base": values["base"], "title": values["title"]},
                timeout_seconds=int(values["timeout_seconds"]),
                context=context,
                target_alias=target_alias,
            )
            parsed = self._parsed_payload(response, context)
            receipt = parsed.get("number") if isinstance(parsed, Mapping) else None
            if not isinstance(receipt, int) or isinstance(receipt, bool) or receipt < 1:
                raise RuntimeError("github_response_invalid")
            context.set_read_back_reference(action, str(receipt))
            return self._write_result("pull_request", receipt, response)
        response = self._request(
            method="GET",
            path=path,
            token=token,
            payload=None,
            timeout_seconds=int(values["timeout_seconds"]),
            context=context,
            target_alias=target_alias,
        )
        if action in _CODE_EVIDENCE_READ_ACTIONS:
            return self._code_evidence_result(action, response, context)
        return self._read_result(resource, response, context)

    def verify(
        self,
        verifier_action: str,
        original_write_action: str,
        request: ProviderExecutionRequest,
        target_alias: str,
        context: ProviderExecutionContext,
    ) -> str:
        action, owner, repository, values = self._validated_parameters(
            original_write_action, request.parameters
        )
        expected_target = self._action_target(action, owner, repository, values)
        if (
            verifier_action != "github.pull_request.read"
            or self.normalize_target_alias(target_alias) != expected_target
            or owner is None
            or repository is None
        ):
            raise ValueError("github_target_mismatch")
        receipt = context.read_back_reference(action)
        if not receipt or not receipt.isascii() or not receipt.isdecimal():
            return "unknown"
        token = self._token(context)
        if action == "github.pull_request.comment.write":
            response = self._request(
                method="GET",
                path=f"/repos/{owner}/{repository}/issues/comments/{int(receipt)}",
                token=token,
                payload=None,
                timeout_seconds=int(values["timeout_seconds"]),
                context=context,
                target_alias=expected_target,
            )
            parsed = self._parsed_payload(response, context)
            return (
                "verified_applied"
                if isinstance(parsed, Mapping)
                and parsed.get("id") == int(receipt)
                and parsed.get("body") == values["body"]
                else "unknown"
            )
        if action == "github.pull_request.create":
            number = self._positive_number(int(receipt), "github_pull_request_number_invalid")
            actual_target = self._target_alias(owner, repository, pull_request_number=number)
            response = self._request(
                method="GET",
                path=f"/repos/{owner}/{repository}/pulls/{number}",
                token=token,
                payload=None,
                timeout_seconds=int(values["timeout_seconds"]),
                context=context,
                target_alias=actual_target,
            )
            parsed = self._parsed_payload(response, context)
            head = parsed.get("head") if isinstance(parsed, Mapping) else None
            base = parsed.get("base") if isinstance(parsed, Mapping) else None
            return (
                "verified_applied"
                if isinstance(parsed, Mapping)
                and parsed.get("number") == number
                and parsed.get("title") == values["title"]
                and isinstance(head, Mapping)
                and head.get("ref") == values["head"]
                and isinstance(base, Mapping)
                and base.get("ref") == values["base"]
                else "unknown"
            )
        return "unknown"

    def read_back_target_alias(
        self,
        action: str,
        parameters: Mapping[str, object],
        context: ProviderExecutionContext,
    ) -> str:
        normalized_action, owner, repository, values = self._validated_parameters(action, parameters)
        if owner is None or repository is None:
            raise ValueError("github_target_invalid")
        if normalized_action == "github.pull_request.create":
            receipt = context.read_back_reference(action)
            if not receipt.isascii() or not receipt.isdecimal():
                raise ValueError("github_target_invalid")
            return self._target_alias(
                owner,
                repository,
                pull_request_number=self._positive_number(int(receipt), "github_pull_request_number_invalid"),
            )
        return self._action_target(normalized_action, owner, repository, values)

    def _validated_parameters(
        self,
        action_value: object,
        parameters: Mapping[str, object],
        *,
        validate_action: bool = True,
    ) -> tuple[str, str | None, str | None, dict[str, object]]:
        if not isinstance(parameters, Mapping):
            raise ValueError("github_parameters_invalid")
        action = action_value if isinstance(action_value, str) else ""
        if validate_action and action not in _ALLOWED_ACTIONS:
            raise ValueError("github_action_not_allowed")
        if not validate_action:
            action = "github.repository.read"
        if action == "github.connection_test":
            if set(parameters) - {"timeout_seconds"}:
                raise ValueError("github_parameters_invalid")
            return action, None, None, {"timeout_seconds": self._timeout(parameters)}
        allowed = {"owner", "repository", "timeout_seconds"}
        if action == "github.issue.read":
            allowed.add("issue_number")
        elif action in {
            "github.pull_request.read",
            "github.pull_request.commits.read",
            "github.pull_request.diffs.read",
            "github.pull_request.comment.write",
        }:
            allowed.add("pull_request_number")
        if action == "github.pull_request.comment.write":
            allowed.add("body")
        if action == "github.pull_request.create":
            allowed.update(("head", "base", "title"))
        if action == "github.repository.file.read":
            allowed.update(("file_path", "ref"))
        if action in {"github.commit.read", "github.commit.diff.read"}:
            allowed.add("sha")
        if action == "github.compare.read":
            allowed.update(("from_ref", "to_ref"))
        if action == "github.actions.run.jobs.read":
            allowed.add("workflow_run_id")
        if action in {
            "github.commit.diff.read",
            "github.compare.read",
            "github.pull_request.commits.read",
            "github.pull_request.diffs.read",
            "github.actions.run.jobs.read",
        }:
            allowed.update(("page", "per_page"))
        if set(parameters) - allowed or not {"owner", "repository"}.issubset(parameters):
            raise ValueError("github_parameters_invalid")
        timeout = self._timeout(parameters)
        values: dict[str, object] = {"timeout_seconds": timeout}
        if action == "github.issue.read":
            values["issue_number"] = self._positive_number(
                parameters.get("issue_number"), "github_issue_number_invalid"
            )
        elif action in {
            "github.pull_request.read",
            "github.pull_request.commits.read",
            "github.pull_request.diffs.read",
            "github.pull_request.comment.write",
        }:
            values["pull_request_number"] = self._positive_number(
                parameters.get("pull_request_number"), "github_pull_request_number_invalid"
            )
        if action == "github.pull_request.comment.write":
            values["body"] = self._text(parameters.get("body"), maximum=2_000)
        if action == "github.pull_request.create":
            values["head"] = self._branch(parameters.get("head"))
            values["base"] = self._branch(parameters.get("base"))
            if values["head"] == values["base"]:
                raise ValueError("github_parameters_invalid")
            values["title"] = self._text(parameters.get("title"), maximum=256)
        if action == "github.repository.file.read":
            values["file_path"] = self._file_path(parameters.get("file_path"))
            values["ref"] = self._ref(parameters.get("ref"))
        if action in {"github.commit.read", "github.commit.diff.read"}:
            values["sha"] = self._sha(parameters.get("sha"))
        if action == "github.compare.read":
            values["from_ref"] = self._ref(parameters.get("from_ref"))
            values["to_ref"] = self._ref(parameters.get("to_ref"))
            if values["from_ref"] == values["to_ref"]:
                raise ValueError("github_parameters_invalid")
        if action == "github.actions.run.jobs.read":
            values["workflow_run_id"] = self._positive_number(
                parameters.get("workflow_run_id"), "github_workflow_run_id_invalid"
            )
        if action in {
            "github.commit.diff.read",
            "github.compare.read",
            "github.pull_request.commits.read",
            "github.pull_request.diffs.read",
            "github.actions.run.jobs.read",
        }:
            values["page"] = self._positive_number(parameters.get("page"), "github_page_invalid")
            values["per_page"] = self._positive_number(parameters.get("per_page"), "github_per_page_invalid")
            if int(values["per_page"]) > 100:
                raise ValueError("github_per_page_invalid")
        return action, self._owner(parameters["owner"]), self._repository(parameters["repository"]), values

    @staticmethod
    def _timeout(parameters: Mapping[str, object]) -> int:
        timeout = parameters.get("timeout_seconds", 15)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 20:
            raise ValueError("github_parameters_invalid")
        return timeout

    @staticmethod
    def _owner(value: object) -> str:
        if not isinstance(value, str) or value != value.strip() or _OWNER.fullmatch(value) is None:
            raise ValueError("github_owner_invalid")
        return value

    @staticmethod
    def _repository(value: object) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or _REPOSITORY.fullmatch(value) is None
            or value.startswith((".", "-"))
            or ".." in value
        ):
            raise ValueError("github_repository_invalid")
        return value

    @staticmethod
    def _positive_number(value: object, error: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 2_147_483_647:
            raise ValueError(error)
        return value

    @staticmethod
    def _sha(value: object) -> str:
        if not isinstance(value, str) or _SHA.fullmatch(value) is None:
            raise ValueError("github_commit_sha_invalid")
        return value

    @classmethod
    def _ref(cls, value: object) -> str:
        if isinstance(value, str) and _SHA.fullmatch(value) is not None:
            return value
        return cls._branch(value)

    @staticmethod
    def _branch(value: object) -> str:
        if (
            not isinstance(value, str)
            or _BRANCH.fullmatch(value) is None
            or ".." in value
            or "//" in value
            or value.startswith("-")
            or value.endswith(("/", "."))
        ):
            raise ValueError("github_branch_invalid")
        return value

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
            raise ValueError("github_file_path_invalid")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("github_file_path_invalid")
        name = parts[-1].lower()
        if (
            name in _SENSITIVE_FILE_NAMES
            or name.startswith(".env.")
            or any(name.endswith(suffix) for suffix in _SENSITIVE_FILE_SUFFIXES)
        ):
            raise ValueError("github_sensitive_file_blocked")
        return value

    @staticmethod
    def _text(value: object, *, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value.encode("utf-8")) > maximum
            or any(ord(character) < 32 and character not in "\n\t" for character in value)
            or contains_sensitive_text(value)
        ):
            raise ValueError("github_text_invalid")
        return value

    @staticmethod
    def _target_alias(
        owner: str,
        repository: str,
        *,
        pull_request_number: int | None = None,
        issue_number: int | None = None,
        workflow_run_id: int | None = None,
    ) -> str:
        owner_value = owner.lower()
        repository_value = repository.lower()
        target = f"gh-o{len(owner_value)}-{owner_value}-r{len(repository_value)}-{repository_value}"
        if pull_request_number is not None:
            target += f"-p{pull_request_number}"
        elif issue_number is not None:
            target += f"-i{issue_number}"
        elif workflow_run_id is not None:
            target += f"-w{workflow_run_id}"
        if len(target) > 127:
            raise ValueError("github_target_invalid")
        return target

    @classmethod
    def _parse_target_alias(cls, value: str) -> str:
        def take(prefix: str, offset: int) -> tuple[str, int]:
            if not value.startswith(prefix, offset):
                raise ValueError("github_target_invalid")
            cursor = offset + len(prefix)
            end = value.find("-", cursor)
            if end <= cursor or not value[cursor:end].isdigit() or value[cursor] == "0":
                raise ValueError("github_target_invalid")
            length = int(value[cursor:end])
            start = end + 1
            stop = start + length
            if stop > len(value):
                raise ValueError("github_target_invalid")
            return value[start:stop], stop

        owner, cursor = take("gh-o", 0)
        repository, cursor = take("-r", cursor)
        pull_request_number = issue_number = workflow_run_id = None
        if cursor != len(value):
            suffix = value[cursor:cursor + 2]
            raw_number = value[cursor + 2:]
            if not raw_number.isascii() or not raw_number.isdecimal():
                raise ValueError("github_target_invalid")
            number = cls._positive_number(int(raw_number), "github_target_invalid")
            if suffix == "-p":
                pull_request_number = number
            elif suffix == "-i":
                issue_number = number
            elif suffix == "-w":
                workflow_run_id = number
            else:
                raise ValueError("github_target_invalid")
        try:
            return cls._target_alias(
                cls._owner(owner),
                cls._repository(repository),
                pull_request_number=pull_request_number,
                issue_number=issue_number,
                workflow_run_id=workflow_run_id,
            )
        except ValueError:
            raise ValueError("github_target_invalid") from None

    def _action_target(
        self,
        action: str,
        owner: str | None,
        repository: str | None,
        values: Mapping[str, object],
    ) -> str:
        if action == "github.connection_test":
            return _CONNECTION_TARGET
        if owner is None or repository is None:
            raise ValueError("github_target_invalid")
        return self._target_alias(
            owner,
            repository,
            pull_request_number=(
                int(values["pull_request_number"])
                if "pull_request_number" in values else None
            ),
            issue_number=int(values["issue_number"]) if "issue_number" in values else None,
            workflow_run_id=(
                int(values["workflow_run_id"])
                if "workflow_run_id" in values else None
            ),
        )

    def _code_evidence_path(
        self,
        action: str,
        owner: str,
        repository: str,
        values: Mapping[str, object],
    ) -> str:
        base = f"/repos/{owner}/{repository}"
        if action == "github.repository.file.read":
            query = urllib.parse.urlencode((('ref', str(values['ref'])),))
            return base + "/contents/" + urllib.parse.quote(str(values["file_path"]), safe="/") + "?" + query
        if action == "github.commit.read":
            return base + "/commits/" + str(values["sha"])
        if action == "github.commit.diff.read":
            return base + "/commits/" + str(values["sha"]) + "?" + self._pagination_query(values)
        if action == "github.compare.read":
            comparison = urllib.parse.quote(str(values["from_ref"]), safe="") + "..." + urllib.parse.quote(str(values["to_ref"]), safe="")
            return base + "/compare/" + comparison + "?" + self._pagination_query(values)
        if action in {"github.pull_request.commits.read", "github.pull_request.diffs.read"}:
            suffix = "commits" if action.endswith("commits.read") else "files"
            return base + f"/pulls/{values['pull_request_number']}/{suffix}?" + self._pagination_query(values)
        return base + f"/actions/runs/{values['workflow_run_id']}/jobs?" + self._pagination_query(values)

    @staticmethod
    def _pagination_query(values: Mapping[str, object]) -> str:
        return urllib.parse.urlencode((('page', str(values['page'])), ('per_page', str(values['per_page']))))

    @staticmethod
    def _token(context: ProviderExecutionContext) -> str:
        token = context.credential("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("github_access_token_unavailable")
        return token

    def _request(
        self,
        *,
        method: str,
        path: str,
        token: str,
        payload: Mapping[str, object] | None,
        timeout_seconds: int,
        context: ProviderExecutionContext,
        target_alias: str,
    ) -> GitHubHttpResponse:
        context.validate_network_target(target_alias)
        context.record_network_dispatch(target_alias, simulated=self._simulated)
        body = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if payload is not None else None
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "his-harness-readonly/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self._transport(
                method=method,
                url=GITHUB_API_BASE_URL + path,
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            raise RuntimeError("github_read_failed") from exc
        if not isinstance(response, GitHubHttpResponse) or not 200 <= response.status_code < 300:
            raise RuntimeError("github_read_failed")
        if len(response.body) > GITHUB_MAX_RESPONSE_BYTES:
            raise RuntimeError("github_response_too_large")
        return response

    @staticmethod
    def _read_result(
        resource: str,
        response: GitHubHttpResponse,
        context: ProviderExecutionContext,
    ) -> dict[str, object]:
        safe_payload = GitHubProviderAdapter._parsed_payload(response, context)
        raw_safe = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        request_id = str(response.headers.get("x-github-request-id") or "")[:128]
        return {
            "source": "github",
            "resource": resource,
            "request_id": request_id,
            "content_hash": hashlib.sha256(raw_safe).hexdigest(),
            "content_size": len(raw_safe),
            "execution_provenance": "simulated" if context.simulated_dispatch_count else "live",
        }

    def _code_evidence_result(
        self,
        action: str,
        response: GitHubHttpResponse,
        context: ProviderExecutionContext,
    ) -> dict[str, object]:
        payload = self._parsed_payload(response, context)
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > GITHUB_MAX_RESPONSE_BYTES:
            raise RuntimeError("github_response_invalid")
        kind = {
            "github.repository.file.read": "repository_file",
            "github.commit.read": "commit",
            "github.commit.diff.read": "commit_diff",
            "github.compare.read": "compare",
            "github.pull_request.commits.read": "pull_request_commits",
            "github.pull_request.diffs.read": "pull_request_diffs",
            "github.actions.run.jobs.read": "actions_run_jobs",
        }[action]
        link = next(
            (str(value) for key, value in response.headers.items() if key.lower() == "link"),
            "",
        )
        truncated = 'rel="next"' in link.lower()
        return {
            "source": "github",
            "kind": kind,
            "payload_sha256": hashlib.sha256(encoded).hexdigest(),
            "payload_bytes": len(encoded),
            "item_count": len(payload) if isinstance(payload, list) else 1,
            "truncated": truncated,
            "execution_provenance": "simulated" if context.simulated_dispatch_count else "live",
            "__local_response__": {"payload": payload, "truncated": truncated},
        }

    @staticmethod
    def _parsed_payload(
        response: GitHubHttpResponse,
        context: ProviderExecutionContext,
    ) -> Mapping[str, object] | list[Mapping[str, object]]:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("github_response_invalid") from exc
        safe_payload = context.redact_resolved_credentials(payload)
        if isinstance(safe_payload, Mapping):
            return dict(safe_payload)
        if isinstance(safe_payload, list) and all(isinstance(item, Mapping) for item in safe_payload):
            return [dict(item) for item in safe_payload]
        raise RuntimeError("github_response_invalid")

    @staticmethod
    def _write_result(kind: str, receipt: int, response: GitHubHttpResponse) -> dict[str, object]:
        request_id = str(response.headers.get("x-github-request-id") or "")[:128]
        return {
            "source": "github",
            "kind": kind + "_write",
            "receipt_present": receipt > 0,
            "request_id": request_id,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def canonical_github_target(action: object, parameters: Mapping[str, object]) -> str:
    """Return the exact non-secret target identity bound by the adapter."""
    adapter = GitHubProviderAdapter()
    normalized_action, owner, repository, values = adapter._validated_parameters(
        action, parameters
    )
    return adapter._action_target(normalized_action, owner, repository, values)


def _https_transport(
    *, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
    timeout_seconds: int,
) -> GitHubHttpResponse:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return GitHubHttpResponse(
                status_code=int(response.status),
                headers=dict(response.headers.items()),
                body=response.read(GITHUB_MAX_RESPONSE_BYTES + 1),
            )
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        raise RuntimeError("github_read_failed") from exc
