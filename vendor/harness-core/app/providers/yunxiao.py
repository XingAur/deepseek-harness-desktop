from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest
from app.sensitive_text import contains_sensitive_text


YUNXIAO_MANAGER_BASE_URL = "https://openapi-rdc.aliyuncs.com"
YUNXIAO_MANAGER_MAX_RESPONSE_BYTES = 65_536
_ALLOWED_ACTIONS = frozenset(
    (
        "workitem.read",
        "workitem.comments.read",
        "workitem.comment.write",
        "workitem.owner.update",
        "workitem.status.update",
    )
)
_COMMON_FIELDS = frozenset(
    ("organization_alias", "project_alias", "work_item_alias", "timeout_seconds")
)
_ACTION_FIELDS = {
    "workitem.read": _COMMON_FIELDS,
    "workitem.comments.read": _COMMON_FIELDS,
    "workitem.comment.write": _COMMON_FIELDS | {"comment"},
    "workitem.owner.update": _COMMON_FIELDS | {"owner_value"},
    "workitem.status.update": _COMMON_FIELDS | {"status_value"},
}
_ORGANIZATION_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_PROJECT_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_WORKITEM_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]{1,31}-[0-9]{1,20}\Z")
_RECEIPT_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_BUSINESS_COMMENT_TEXT = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff0-9０-９，。；：、（）《》“”‘’！？…—-]+\Z"
)
_COMMENT_FIELDS = (
    "business_logic",
    "trigger_condition",
    "handling_result",
    "covered_scenarios",
)


@dataclass(frozen=True)
class YunxiaoHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


YunxiaoTransport = Callable[..., YunxiaoHttpResponse]


class YunxiaoProviderAdapter:
    """Manager-only 云效 adapter with fixed routes and no legacy credential path."""

    def __init__(self, *, transport: YunxiaoTransport | None = None) -> None:
        self._transport = transport or _https_transport

    def normalize_target_alias(self, value: object) -> str:
        """Return the audit-safe organization/work-item target identity."""

        return _target_alias_from_display(value)

    def normalize_request_target(self, parameters: Mapping[str, object]) -> str:
        """Return the target identity actually used by the fixed HTTP routes."""

        return _target_alias(_validated_aliases(parameters))

    def render_plan(self, request: ProviderExecutionRequest) -> dict[str, object]:
        action, aliases, values = _validated_request(request.action, request.parameters)
        change: dict[str, object]
        if action == "workitem.comment.write":
            change = {"field": "comment", "after": values["comment"]}
        elif action == "workitem.owner.update":
            change = {"field": "owner", "after": values["owner_value"]}
        elif action == "workitem.status.update":
            change = {"field": "status", "after": values["status_value"]}
        else:
            change = {"field": "read", "after": "no_remote_change"}
        return {
            "provider": "yunxiao",
            "action": action,
            "target_alias": _target_alias(aliases),
            "change": change,
        }

    def execute(
        self, request: ProviderExecutionRequest, context: ProviderExecutionContext
    ) -> Mapping[str, object]:
        action, aliases, values = _validated_request(request.action, request.parameters)
        pat = _manager_pat(context)
        timeout_seconds = values["timeout_seconds"]
        if action == "workitem.read":
            return _read_result("workitem", self._request("GET", _workitem_path(aliases), pat, None, timeout_seconds))
        if action == "workitem.comments.read":
            return _read_result("comments", self._request("GET", _comments_path(aliases), pat, None, timeout_seconds))
        if action == "workitem.comment.write":
            response = self._request(
                "POST", _comments_path(aliases), pat, {"content": values["comment"]}, timeout_seconds
            )
            context.set_read_back_reference(action, _response_receipt(response.payload, "id"))
            return _write_result("comment", response)
        field = "assignee" if action == "workitem.owner.update" else "status"
        value_key = "owner_value" if action == "workitem.owner.update" else "status_value"
        response = self._request(
            "POST",
            "/oapi/v1/projex/workitems/updateWorkitemField",
            pat,
            {
                "organizationId": aliases["organization"],
                "workitemIdentifier": aliases["work_item"],
                "updateWorkitemPropertyRequest": [
                    {"fieldIdentifier": field, "fieldValue": values[value_key]}
                ],
            },
            timeout_seconds,
        )
        context.set_read_back_reference(action, _response_receipt(response.payload, "updateId"))
        return _write_result("owner" if field == "assignee" else "status", response)

    def verify(
        self,
        verifier_action: str,
        original_write_action: str,
        request: ProviderExecutionRequest,
        target_alias: str,
        context: ProviderExecutionContext,
    ) -> Literal["verified_applied", "verified_not_applied", "unknown"]:
        action, aliases, values = _validated_request(original_write_action, request.parameters)
        if action not in {"workitem.comment.write", "workitem.owner.update", "workitem.status.update"}:
            return "unknown"
        expected_verifier = (
            "workitem.comments.read"
            if action == "workitem.comment.write"
            else "workitem.read"
        )
        if verifier_action != expected_verifier:
            return "unknown"
        if self.normalize_target_alias(target_alias) != _target_alias(aliases):
            raise ValueError("yunxiao_target_mismatch")
        pat = _manager_pat(context)
        timeout_seconds = values["timeout_seconds"]
        receipt = context.read_back_reference(action)
        if action == "workitem.comment.write":
            response = self._request("GET", _comments_path(aliases), pat, None, timeout_seconds)
            return _comment_read_back(response.payload, receipt)
        response = self._request("GET", _workitem_path(aliases), pat, None, timeout_seconds)
        field = "assignee" if action == "workitem.owner.update" else "status"
        value_key = "owner_value" if action == "workitem.owner.update" else "status_value"
        return _field_read_back(
            response.payload,
            work_item=aliases["work_item"],
            field=field,
            expected=values[value_key],
            receipt=receipt,
        )

    def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        pat: str,
        payload: Mapping[str, object] | None,
        timeout_seconds: int,
    ) -> "_ParsedResponse":
        body = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {pat}",
            "x-yunxiao-token": pat,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self._transport(
            method=method,
            url=YUNXIAO_MANAGER_BASE_URL + path,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(response, YunxiaoHttpResponse):
            raise RuntimeError("yunxiao_transport_contract_invalid")
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError("yunxiao_request_failed")
        if len(response.body) > YUNXIAO_MANAGER_MAX_RESPONSE_BYTES:
            raise RuntimeError("yunxiao_response_too_large")
        try:
            payload_value = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("yunxiao_response_invalid") from None
        if not isinstance(payload_value, (dict, list)):
            raise RuntimeError("yunxiao_response_invalid")
        return _ParsedResponse(
            payload=payload_value,
            request_id=_safe_request_id(response.headers),
            content_hash=hashlib.sha256(response.body).hexdigest(),
        )


@dataclass(frozen=True)
class _ParsedResponse:
    payload: dict[str, object] | list[object]
    request_id: str
    content_hash: str


def _validated_request(
    action_value: object, parameters: Mapping[str, object]
) -> tuple[str, dict[str, str], dict[str, object]]:
    action = _allowed_action(action_value)
    if not isinstance(parameters, Mapping) or set(parameters) - _ACTION_FIELDS[action]:
        raise ValueError("yunxiao_parameters_invalid")
    required = _ACTION_FIELDS[action] - {"timeout_seconds"}
    if not required.issubset(parameters):
        raise ValueError("yunxiao_parameters_invalid")
    aliases = _validated_aliases(parameters)
    values: dict[str, object] = {"timeout_seconds": _timeout_seconds(parameters)}
    if action == "workitem.comment.write":
        values["comment"] = _business_comment(parameters["comment"])
    elif action == "workitem.owner.update":
        values["owner_value"] = _body_value(parameters["owner_value"])
    elif action == "workitem.status.update":
        values["status_value"] = _body_value(parameters["status_value"])
    return action, aliases, values


def _validated_aliases(parameters: Mapping[str, object]) -> dict[str, str]:
    if not isinstance(parameters, Mapping):
        raise ValueError("yunxiao_parameters_invalid")
    return {
        "organization": _organization_identifier(parameters.get("organization_alias")),
        "project": _project_identifier(parameters.get("project_alias")),
        "work_item": _workitem_identifier(parameters.get("work_item_alias")),
    }


def _allowed_action(value: object) -> str:
    if not isinstance(value, str) or value not in _ALLOWED_ACTIONS:
        raise ValueError("yunxiao_action_not_allowed")
    return value


def _identifier(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or value != value.strip() or not value or len(value.encode("utf-8")) > 64:
        raise ValueError("yunxiao_identifier_invalid")
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("yunxiao_identifier_invalid") from None
    if pattern.fullmatch(value) is None:
        raise ValueError("yunxiao_identifier_invalid")
    return value


def _organization_identifier(value: object) -> str:
    return _identifier(value, _ORGANIZATION_IDENTIFIER)


def _project_identifier(value: object) -> str:
    return _identifier(value, _PROJECT_IDENTIFIER)


def _workitem_identifier(value: object) -> str:
    return _identifier(value, _WORKITEM_IDENTIFIER).upper()


def _body_value(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value.encode("utf-8")) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or contains_sensitive_text(value)
    ):
        raise ValueError("yunxiao_parameters_invalid")
    return value


def _business_comment(value: object) -> str:
    if not isinstance(value, Mapping) or set(value) != set(_COMMENT_FIELDS):
        raise ValueError("yunxiao_comment_not_business_oriented")
    parts: list[str] = []
    labels = ("业务逻辑", "触发条件", "处理结果", "覆盖场景")
    for field, label in zip(_COMMENT_FIELDS, labels, strict=True):
        item = value[field]
        if (
            not isinstance(item, str)
            or item != item.strip()
            or not item
            or len(item.encode("utf-8")) > 400
            or contains_sensitive_text(item)
            or _BUSINESS_COMMENT_TEXT.fullmatch(item) is None
        ):
            raise ValueError("yunxiao_comment_not_business_oriented")
        parts.append(f"{label}：{item}")
    return "\n".join(parts)


def _timeout_seconds(parameters: Mapping[str, object]) -> int:
    value = parameters.get("timeout_seconds", 15)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 15:
        raise ValueError("yunxiao_parameters_invalid")
    return value


def _manager_pat(context: ProviderExecutionContext) -> str:
    if not context.network_allowed:
        raise PermissionError("yunxiao_network_not_allowed")
    return context.credential("pat")


def _workitem_path(aliases: Mapping[str, str]) -> str:
    return f"/oapi/v1/projex/organizations/{aliases['organization']}/workitems/{aliases['work_item']}"


def _target_alias(aliases: Mapping[str, str]) -> str:
    return f"{aliases['organization']}.{aliases['work_item'].lower()}"


def _target_alias_from_display(value: object) -> str:
    if not isinstance(value, str) or value.count(".") != 1:
        raise ValueError("yunxiao_identifier_invalid")
    organization, work_item = value.split(".")
    return _target_alias(
        {
            "organization": _organization_identifier(organization),
            "work_item": _workitem_identifier(work_item),
        }
    )


def _comments_path(aliases: Mapping[str, str]) -> str:
    return _workitem_path(aliases) + "/comments"


def _read_result(kind: str, response: _ParsedResponse) -> dict[str, object]:
    return {
        "source": "yunxiao",
        "kind": kind,
        "request_id": response.request_id,
        "content_hash": response.content_hash,
        "summary": _summary_shape(response.payload),
    }


def _write_result(field: str, response: _ParsedResponse) -> dict[str, object]:
    return {
        "source": "yunxiao",
        "kind": "workitem_write",
        "request_id": response.request_id,
        "content_hash": response.content_hash,
        "change": {"field": field},
        "summary": _summary_shape(response.payload),
    }


def _summary_shape(payload: dict[str, object] | list[object]) -> dict[str, object]:
    if isinstance(payload, list):
        return {"response_type": "list", "item_count": len(payload)}
    return {"response_type": "object", "field_count": len(payload)}


def _response_receipt(payload: dict[str, object] | list[object], key: str) -> str:
    if not isinstance(payload, Mapping):
        return ""
    value = payload.get(key)
    if isinstance(value, str) and _RECEIPT_IDENTIFIER.fullmatch(value):
        return value
    return ""


def _comment_read_back(
    payload: dict[str, object] | list[object], receipt: str
) -> Literal["verified_applied", "unknown"]:
    if not receipt or not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
        return "unknown"
    if payload.get("hasMore") is True or payload.get("truncated") is True:
        return "unknown"
    for item in payload["items"]:
        if isinstance(item, Mapping) and item.get("id") == receipt:
            return "verified_applied"
    return "unknown"


def _field_read_back(
    payload: dict[str, object] | list[object],
    *,
    work_item: str,
    field: str,
    expected: object,
    receipt: str,
) -> Literal["verified_applied", "unknown"]:
    if not receipt or not isinstance(payload, Mapping):
        return "unknown"
    if (
        payload.get("identifier") == work_item
        and payload.get("lastUpdateId") == receipt
        and payload.get(field) == expected
    ):
        return "verified_applied"
    return "unknown"


def _safe_request_id(headers: Mapping[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "x-acs-request-id" and isinstance(value, str):
            if _RECEIPT_IDENTIFIER.fullmatch(value):
                return value
    return ""


def _https_transport(
    *, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout_seconds: int
) -> YunxiaoHttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_RejectRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as opened:
            raw = opened.read(YUNXIAO_MANAGER_MAX_RESPONSE_BYTES + 1)
            return YunxiaoHttpResponse(int(opened.status), dict(opened.headers.items()), raw)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
        raise RuntimeError("yunxiao_request_failed") from None


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None
