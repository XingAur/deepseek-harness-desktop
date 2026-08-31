#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit, urlunsplit


CONTRACT_VERSION = "requirement-evidence.v2"
DEFAULT_RELATION_TYPES = ("PARENT", "SUB", "ASSOCIATED", "DEPEND_ON", "DEPENDED_BY")
BLOCKED_ACTIONS = (
    "comment",
    "upload_attachment",
    "assign",
    "transition",
    "update",
    "create",
    "delete",
    "close",
)
DEFAULT_BASE_URL = "https://openapi-rdc.aliyuncs.com"
DEFAULT_CREDENTIALS_FILE = Path.home() / "WorkCode/ai/apiKey/credentials.json"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_API_HOSTS = frozenset({"openapi-rdc.aliyuncs.com"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


class _ImageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = dict(attrs)
        src = str(values.get("src") or "").strip()
        if src:
            self.urls.append(html.unescape(src))


class SafeApiRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject API redirects so authentication headers never cross origins."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "云效 API 重定向已拒绝，避免认证头被转发。",
            headers,
            fp,
        )


class YunxiaoClient:
    def __init__(
        self,
        *,
        token: str,
        organization_id: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
        opener: Any = None,
    ) -> None:
        if not token:
            raise ValueError("token is required")
        if not organization_id:
            raise ValueError("organization_id is required")
        parsed_base = urlsplit(base_url)
        try:
            port = parsed_base.port
        except ValueError as exc:
            raise ValueError("云效 API 地址端口无效。") from exc
        if (
            parsed_base.scheme.lower() != "https"
            or (parsed_base.hostname or "").lower() not in ALLOWED_API_HOSTS
            or parsed_base.username
            or parsed_base.password
            or port not in (None, 443)
            or parsed_base.query
            or parsed_base.fragment
            or parsed_base.path not in ("", "/")
        ):
            raise ValueError(
                "云效 API 地址必须是受信任的 HTTPS 接入点："
                "https://openapi-rdc.aliyuncs.com"
            )
        self.token = token
        self.organization_id = organization_id
        self.base_url = "https://openapi-rdc.aliyuncs.com"
        self.timeout_seconds = timeout_seconds
        self.max_download_bytes = max_download_bytes
        self.api_opener = opener or urllib.request.build_opener(
            SafeApiRedirectHandler()
        ).open
        self.download_opener = opener or urllib.request.urlopen

    def get_work_item(self, work_item_id: str) -> dict:
        return self._get_json(self._work_item_url(work_item_id))

    def list_comments(self, work_item_id: str) -> dict:
        return self._get_json(f"{self._work_item_url(work_item_id)}/comments")

    def list_attachments(self, work_item_id: str) -> dict:
        return self._get_json(f"{self._work_item_url(work_item_id)}/attachments")

    def list_relations(self, work_item_id: str, relation_type: str) -> dict:
        relation = str(relation_type or "").upper()
        if relation not in DEFAULT_RELATION_TYPES:
            return {
                "ok": False,
                "http_status": None,
                "data": None,
                "error": f"不支持的关系类型：{relation}",
            }
        query = urllib.parse.urlencode({"relationType": relation})
        return self._get_json(
            f"{self._work_item_url(work_item_id)}/relationRecords?{query}"
        )

    def get_workitem_file(self, work_item_id: str, file_identifier: str) -> dict:
        identifier = urllib.parse.quote(str(file_identifier), safe="")
        return self._get_json(f"{self._work_item_url(work_item_id)}/files/{identifier}")

    def download_file(self, url: str) -> dict:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https":
            return {
                "ok": False,
                "http_status": None,
                "data": None,
                "error": "附件下载只允许 HTTPS 地址。",
            }
        request = urllib.request.Request(
            url,
            headers={"Accept": "*/*"},
            method="GET",
        )
        try:
            with self.download_opener(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                content = response.read(self.max_download_bytes + 1)
                status = getattr(response, "status", 200)
                content_type = response.headers.get("content-type") or "application/octet-stream"
            if len(content) > self.max_download_bytes:
                return {
                    "ok": False,
                    "http_status": status,
                    "data": None,
                    "error": f"附件超过下载上限 {self.max_download_bytes} bytes。",
                }
            return {
                "ok": True,
                "http_status": status,
                "data": {"content": content, "content_type": content_type},
                "error": "",
            }
        except urllib.error.HTTPError as exc:
            detail = _read_http_error(exc)
            return {
                "ok": False,
                "http_status": exc.code,
                "data": None,
                "error": redact_sensitive(detail, [self.token]),
            }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {
                "ok": False,
                "http_status": None,
                "data": None,
                "error": redact_sensitive(exc, [self.token]),
            }

    def _work_item_url(self, work_item_id: str) -> str:
        organization = urllib.parse.quote(self.organization_id, safe="")
        item = urllib.parse.quote(str(work_item_id), safe="")
        return (
            f"{self.base_url}/oapi/v1/projex/organizations/"
            f"{organization}/workitems/{item}"
        )

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "x-yunxiao-token": self.token,
            },
            method="GET",
        )
        try:
            with self.api_opener(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                body = response.read()
                status = getattr(response, "status", 200)
                content_type = response.headers.get("content-type") or ""
            if body and "json" not in content_type.lower():
                return {
                    "ok": False,
                    "http_status": status,
                    "data": None,
                    "error": f"云效接口返回非 JSON 内容：{content_type or 'unknown'}",
                }
            data = json.loads(body.decode("utf-8")) if body else {}
            if isinstance(data, dict) and str(data.get("success")).lower() == "false":
                error_code = _stringify(data.get("errorCode") or data.get("code"))
                error_message = _stringify(
                    data.get("errorMessage") or data.get("errorMsg") or data.get("message")
                )
                detail = ": ".join(
                    part for part in (error_code, error_message) if part
                ) or "云效接口返回 success=false。"
                return {
                    "ok": False,
                    "http_status": status,
                    "data": None,
                    "error": redact_sensitive(detail, [self.token]),
                }
            return {
                "ok": True,
                "http_status": status,
                "data": data,
                "error": "",
            }
        except urllib.error.HTTPError as exc:
            detail = _read_http_error(exc)
            return {
                "ok": False,
                "http_status": exc.code,
                "data": None,
                "error": redact_sensitive(detail, [self.token]),
            }
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            return {
                "ok": False,
                "http_status": None,
                "data": None,
                "error": redact_sensitive(exc, [self.token]),
            }


def load_credentials(
    credentials_file: Mapping[str, object] | str | Path | None = None,
    *,
    credential_kind: str = "read",
) -> dict:
    if credential_kind not in {"read", "write"}:
        raise ValueError("credential_kind must be read or write")
    file_data: dict[str, object] = {}
    mapping_mode = isinstance(credentials_file, Mapping)
    credentials_source = "configured-file"
    if mapping_mode:
        try:
            file_data = dict(credentials_file)
        except Exception:
            raise ValueError(
                "credential mapping could not be read; allowed keys are "
                "aliyun_devops_pat, aliyun_devops_write_pat, and "
                "aliyun_devops_organization_id"
            ) from None
        credentials_source = "mapping"
    else:
        file_path = Path(
            credentials_file
            or os.environ.get("YUNXIAO_CREDENTIALS_FILE")
            or DEFAULT_CREDENTIALS_FILE
        ).expanduser()
        try:
            file_exists = file_path.is_file()
        except OSError:
            file_exists = False
        if file_exists:
            try:
                loaded = json.loads(file_path.read_text(encoding="utf-8"))
                file_data = loaded if isinstance(loaded, dict) else {}
            except (OSError, UnicodeError, json.JSONDecodeError):
                file_data = {}
    token_keys = (
        ("ALIYUN_DEVOPS_WRITE_PAT", "aliyun_devops_write_pat")
        if credential_kind == "write"
        else ("ALIYUN_DEVOPS_PAT", "aliyun_devops_pat")
    )
    organization_keys = (
        "ALIYUN_DEVOPS_ORGANIZATION_ID",
        "aliyun_devops_organization_id",
    )
    if mapping_mode:
        token, token_source = _first_mapping_credential(
            keys=token_keys,
            file_data=file_data,
        )
        organization_id, organization_source = _first_mapping_credential(
            keys=organization_keys,
            file_data=file_data,
        )
    else:
        token, token_source = _first_credential(
            env_keys=token_keys,
            file_data=file_data,
        )
        organization_id, organization_source = _first_credential(
            env_keys=organization_keys,
            file_data=file_data,
        )
    missing = []
    if not token:
        missing.append(
            "aliyun_devops_write_pat"
            if credential_kind == "write"
            else "aliyun_devops_pat"
        )
    if not organization_id:
        missing.append("aliyun_devops_organization_id")
    if credential_kind == "read" and missing:
        safe_missing_names = {
            "aliyun_devops_pat": "ALIYUN_DEVOPS_PAT (aliyun_devops_pat)",
            "aliyun_devops_organization_id": (
                "ALIYUN_DEVOPS_ORGANIZATION_ID (aliyun_devops_organization_id)"
            ),
        }
        raise ValueError(
            "read credentials missing required keys: "
            + ", ".join(safe_missing_names[key] for key in missing)
        )
    return {
        "token": token,
        "organization_id": organization_id,
        "token_source": token_source,
        "organization_id_source": organization_source,
        "credential_kind": credential_kind,
        "missing_keys": missing,
        "safe_summary": {
            "token": "present" if token else "missing",
            "organization_id": "present" if organization_id else "missing",
            "token_source": token_source,
            "organization_id_source": organization_source,
            "credential_kind": credential_kind,
            "credentials_file": credentials_source,
        },
    }


def parse_work_item_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    serial_match = re.search(r"\b[A-Za-z][A-Za-z0-9_]*-\d+\b", text)
    if serial_match:
        return serial_match.group(0)
    if "://" not in text:
        return text if re.fullmatch(r"[A-Za-z0-9_.:-]+", text) else ""
    parsed = urlsplit(text)
    segments = [unquote(item) for item in parsed.path.split("/") if item]
    for marker in ("workitems", "workitem", "req", "bug", "task", "story"):
        if marker in segments:
            index = segments.index(marker)
            if index + 1 < len(segments):
                return segments[index + 1]
            return ""
    return segments[-1] if segments else ""


def collect_evidence(
    *,
    source: str,
    client: Any,
    include_comments: bool = True,
    include_attachments: bool = True,
    output_dir: str | Path | None = None,
    download_files: bool = False,
    fetched_at: str = "",
    secrets: list[str] | None = None,
    max_parent_depth: int = 8,
) -> dict:
    secret_values = [item for item in (secrets or []) if item]
    prepared_output_dir = (
        _prepare_new_output_directory(output_dir) if output_dir else None
    )
    requested_id = parse_work_item_id(source)
    evidence = _new_evidence(
        source=_sanitize_source(source, secret_values),
        requested_id=requested_id,
        fetched_at=fetched_at or datetime.now(timezone.utc).isoformat(),
    )
    state = {
        "incomplete": False,
        "current_failed": False,
        "secrets": secret_values,
        "output_dir": prepared_output_dir,
        "download_files": bool(download_files),
        "include_comments": bool(include_comments),
        "include_attachments": bool(include_attachments),
        "work_items": {},
        "relations": [],
        "relation_keys": set(),
        "attempted_item_ids": set(),
    }
    if not requested_id:
        _add_issue(
            evidence,
            "errors",
            code="work_item_id_missing",
            message="无法从输入中解析云效工作项编号。",
            secrets=secret_values,
        )
        state["current_failed"] = True
        return _finalize(evidence, state)

    current_result = client.get_work_item(requested_id)
    _record_request(
        evidence,
        operation="get_work_item",
        work_item_id=requested_id,
        result=current_result,
        secrets=secret_values,
    )
    if not current_result.get("ok"):
        _add_issue(
            evidence,
            "errors",
            code="requested_work_item_unavailable",
            message=current_result.get("error") or "当前工作项读取失败。",
            work_item_id=requested_id,
            http_status=current_result.get("http_status"),
            secrets=secret_values,
        )
        state["current_failed"] = True
        return _finalize(evidence, state)

    current_raw = _unwrap_object(current_result.get("data"))
    if not _is_work_item_payload(current_raw):
        _add_issue(
            evidence,
            "errors",
            code="requested_work_item_invalid",
            message="工作项详情为空或缺少可识别的工作项字段。",
            work_item_id=requested_id,
            secrets=secret_values,
        )
        state["current_failed"] = True
        return _finalize(evidence, state)
    current = _normalize_work_item(current_raw, fallback_id=requested_id)

    evidence["source"]["resolved_work_item_id"] = current["id"]
    state["attempted_item_ids"].add(current["id"])
    _collect_item_content(
        evidence=evidence,
        state=state,
        client=client,
        item=current,
        role="requested",
    )

    current_parent_edges: list[str] = []
    for relation_type in DEFAULT_RELATION_TYPES:
        relation_result, relation_items = _checked_list_result(
            client.list_relations(current["id"], relation_type),
            label=f"{relation_type} 关系",
            item_kind="relation",
        )
        _record_request(
            evidence,
            operation="list_relations",
            work_item_id=current["id"],
            relation_type=relation_type,
            result=relation_result,
            secrets=secret_values,
        )
        if not relation_result.get("ok"):
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="relation_read_failed",
                message=relation_result.get("error") or f"{relation_type} 关系读取失败。",
                operation="list_relations",
                work_item_id=current["id"],
                relation_type=relation_type,
                http_status=relation_result.get("http_status"),
                secrets=secret_values,
            )
            continue
        edges = _normalize_relations(
            relation_items,
            from_id=current["id"],
            fallback_type=relation_type,
        )
        for edge in edges:
            _add_relation(state, edge)
            if edge["type"] == "PARENT" and edge["to_id"]:
                current_parent_edges.append(edge["to_id"])

    declared_parent = current.get("parent_id") or ""
    if declared_parent and current_parent_edges and declared_parent not in current_parent_edges:
        state["incomplete"] = True
        _add_issue(
            evidence,
            "warnings",
            code="parent_relation_conflict",
            message="工作项 parentId 与 PARENT 关系记录不一致。",
            work_item_id=current["id"],
            secrets=secret_values,
        )

    current_to_root = [current["id"]]
    parent_candidates = _ordered_parent_candidates(current, current_parent_edges)
    if len(parent_candidates) > 1:
        state["incomplete"] = True
        _add_issue(
            evidence,
            "warnings",
            code="multiple_parent_candidates",
            message=f"检测到多个父级候选：{', '.join(parent_candidates)}",
            work_item_id=current["id"],
            secrets=secret_values,
        )
    next_parent = parent_candidates[0] if parent_candidates else ""
    visited = {current["id"]}
    depth = 0
    while next_parent and depth < max_parent_depth:
        if next_parent in visited:
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="parent_cycle_detected",
                message=f"父级关系出现循环：{next_parent}",
                work_item_id=next_parent,
                secrets=secret_values,
            )
            break
        visited.add(next_parent)
        state["attempted_item_ids"].add(next_parent)
        parent_result = client.get_work_item(next_parent)
        _record_request(
            evidence,
            operation="get_work_item",
            work_item_id=next_parent,
            result=parent_result,
            secrets=secret_values,
        )
        if not parent_result.get("ok"):
            state["incomplete"] = True
            _add_issue(
                evidence,
                "errors",
                code="parent_work_item_unavailable",
                message=parent_result.get("error") or "父工作项读取失败。",
                work_item_id=next_parent,
                http_status=parent_result.get("http_status"),
                secrets=secret_values,
            )
            break
        parent_raw = _unwrap_object(parent_result.get("data"))
        if not _is_work_item_payload(parent_raw):
            state["incomplete"] = True
            _add_issue(
                evidence,
                "errors",
                code="parent_work_item_invalid",
                message="父工作项详情为空或缺少可识别的工作项字段。",
                work_item_id=next_parent,
                secrets=secret_values,
            )
            break
        parent = _normalize_work_item(parent_raw, fallback_id=next_parent)
        _collect_item_content(
            evidence=evidence,
            state=state,
            client=client,
            item=parent,
            role="parent",
        )
        current_to_root.append(parent["id"])
        parent_relation_result, parent_relation_items = _checked_list_result(
            client.list_relations(parent["id"], "PARENT"),
            label="PARENT 关系",
            item_kind="relation",
        )
        _record_request(
            evidence,
            operation="list_relations",
            work_item_id=parent["id"],
            relation_type="PARENT",
            result=parent_relation_result,
            secrets=secret_values,
        )
        relation_parent_ids: list[str] = []
        if parent_relation_result.get("ok"):
            parent_edges = _normalize_relations(
                parent_relation_items,
                from_id=parent["id"],
                fallback_type="PARENT",
            )
            for edge in parent_edges:
                _add_relation(state, edge)
                if edge["to_id"]:
                    relation_parent_ids.append(edge["to_id"])
        else:
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="relation_read_failed",
                message=parent_relation_result.get("error") or "父级 PARENT 关系读取失败。",
                operation="list_relations",
                work_item_id=parent["id"],
                relation_type="PARENT",
                http_status=parent_relation_result.get("http_status"),
                secrets=secret_values,
            )
        if parent.get("parent_id") and relation_parent_ids and parent["parent_id"] not in relation_parent_ids:
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="parent_relation_conflict",
                message="父工作项 parentId 与 PARENT 关系记录不一致。",
                work_item_id=parent["id"],
                secrets=secret_values,
            )
        candidates = _ordered_parent_candidates(parent, relation_parent_ids)
        if len(candidates) > 1:
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="multiple_parent_candidates",
                message=f"检测到多个父级候选：{', '.join(candidates)}",
                work_item_id=parent["id"],
                secrets=secret_values,
            )
        next_parent = candidates[0] if candidates else ""
        depth += 1

    if next_parent and depth >= max_parent_depth:
        state["incomplete"] = True
        _add_issue(
            evidence,
            "warnings",
            code="parent_depth_exceeded",
            message=f"父级追溯超过最大深度 {max_parent_depth}。",
            work_item_id=next_parent,
            secrets=secret_values,
        )

    for edge in list(state["relations"]):
        target_id = edge.get("to_id") or ""
        if not target_id or target_id in state["attempted_item_ids"]:
            continue
        state["attempted_item_ids"].add(target_id)
        related_result = client.get_work_item(target_id)
        _record_request(
            evidence,
            operation="get_work_item",
            work_item_id=target_id,
            result=related_result,
            secrets=secret_values,
        )
        if not related_result.get("ok"):
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="related_work_item_unavailable",
                message=related_result.get("error") or "关联工作项读取失败。",
                work_item_id=target_id,
                http_status=related_result.get("http_status"),
                secrets=secret_values,
            )
            continue
        related_raw = _unwrap_object(related_result.get("data"))
        if not _is_work_item_payload(related_raw):
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="related_work_item_invalid",
                message="关联工作项详情为空或缺少可识别的工作项字段。",
                work_item_id=target_id,
                secrets=secret_values,
            )
            continue
        related = _normalize_work_item(related_raw, fallback_id=target_id)
        _collect_item_content(
            evidence=evidence,
            state=state,
            client=client,
            item=related,
            role="related",
        )

    evidence["lineage"] = list(reversed(current_to_root))
    evidence["root_work_item_id"] = evidence["lineage"][0] if evidence["lineage"] else current["id"]
    return _finalize(evidence, state)


def validate_evidence(evidence: object) -> list[str]:
    if not isinstance(evidence, dict):
        return ["evidence must be an object"]
    errors: list[str] = _validate_contract_schema(evidence)
    required = (
        "contract_version",
        "provider",
        "mode",
        "source",
        "policy",
        "decision_gate",
        "completeness",
        "root_work_item_id",
        "work_items",
        "relations",
        "lineage",
        "warnings",
        "errors",
        "request_log",
        "integrity",
    )
    for key in required:
        if key not in evidence:
            errors.append(f"missing field: {key}")
    if evidence.get("contract_version") != CONTRACT_VERSION:
        errors.append(f"contract_version must be {CONTRACT_VERSION}")
    if evidence.get("provider") != "yunxiao":
        errors.append("provider must be yunxiao")
    if evidence.get("mode") != "readonly":
        errors.append("mode must be readonly")
    source = evidence.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
    policy = evidence.get("policy")
    if not isinstance(policy, dict):
        errors.append("policy must be an object")
        policy = {}
    work_items = evidence.get("work_items")
    if not isinstance(work_items, list):
        errors.append("work_items must be an array")
        work_items = []
    elif any(not isinstance(item, dict) for item in work_items):
        errors.append("work_items entries must be objects")
    for work_item in work_items:
        if not isinstance(work_item, dict):
            continue
        for collection_name in ("attachments", "inline_files"):
            file_items = work_item.get(collection_name)
            if not isinstance(file_items, list):
                continue
            for file_item in file_items:
                if not isinstance(file_item, dict) or file_item.get("download_status") != "success":
                    continue
                local_path = file_item.get("local_path")
                parsed_path = PurePosixPath(local_path) if isinstance(local_path, str) else None
                if (
                    not parsed_path
                    or not local_path
                    or parsed_path.is_absolute()
                    or ".." in parsed_path.parts
                ):
                    errors.append(
                        "downloaded file local_path must be a safe relative path"
                    )
    relations = evidence.get("relations")
    if not isinstance(relations, list):
        errors.append("relations must be an array")
        relations = []
    elif any(not isinstance(item, dict) for item in relations):
        errors.append("relations entries must be objects")
    lineage = evidence.get("lineage")
    if not isinstance(lineage, list) or any(
        not isinstance(item, str) for item in (lineage or [])
    ):
        errors.append("lineage must be an array of strings")
    if not isinstance(evidence.get("root_work_item_id"), str):
        errors.append("root_work_item_id must be a string")
    gate_object = evidence.get("decision_gate")
    if not isinstance(gate_object, dict):
        errors.append("decision_gate must be an object")
        gate_object = {}
    decision_state = gate_object.get("state")
    if decision_state not in {
        "ready_for_analysis",
        "needs_requirement_confirmation",
        "fetch_failed",
    }:
        errors.append("decision_gate.state is invalid")
    completeness_object = evidence.get("completeness")
    if not isinstance(completeness_object, dict):
        errors.append("completeness must be an object")
        completeness_object = {}
    completeness = completeness_object.get("status")
    if completeness not in {"complete", "partial", "failed"}:
        errors.append("completeness.status is invalid")
    expected_gate_status = {
        "ready_for_analysis": "complete",
        "needs_requirement_confirmation": "partial",
        "fetch_failed": "failed",
    }
    if (
        decision_state in expected_gate_status
        and completeness in {"complete", "partial", "failed"}
        and expected_gate_status[decision_state] != completeness
    ):
        errors.append("decision_gate.state and completeness.status are inconsistent")
    allowed_actions = policy.get("allowed_actions")
    if allowed_actions != ["read"]:
        errors.append("policy.allowed_actions must contain only read")
    request_log = evidence.get("request_log")
    if not isinstance(request_log, list):
        errors.append("request_log must be an array")
        request_log = []
    elif any(not isinstance(item, dict) for item in request_log):
        errors.append("request_log entries must be objects")
    actual_failed = sum(
        1
        for item in request_log
        if isinstance(item, dict) and item.get("status") == "failed"
    )
    if completeness_object.get("request_count") != len(request_log):
        errors.append("completeness.request_count does not match request_log")
    if completeness_object.get("failed_request_count") != actual_failed:
        errors.append("completeness.failed_request_count does not match request_log")
    for field in ("warnings", "errors"):
        value = evidence.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, dict) for item in (value or [])
        ):
            errors.append(f"{field} must be an array of objects")
    integrity = evidence.get("integrity")
    if not isinstance(integrity, dict):
        errors.append("integrity must be an object")
    else:
        if integrity.get("algorithm") != "sha256":
            errors.append("integrity.algorithm must be sha256")
        stored_hash = integrity.get("evidence_sha256")
        if not isinstance(stored_hash, str) or not re.fullmatch(
            r"[a-f0-9]{64}", stored_hash
        ):
            errors.append("integrity.evidence_sha256 must be a SHA-256 hex string")
        elif stored_hash != _evidence_hash(evidence):
            errors.append("integrity.evidence_sha256 does not match evidence content")
    return errors


def _validate_contract_schema(evidence: dict) -> list[str]:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "requirement-evidence.v2.schema.json"
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"schema could not be loaded: {exc}"]
    errors: list[str] = []
    _validate_schema_node(
        value=evidence,
        schema=schema,
        root_schema=schema,
        path="$",
        errors=errors,
    )
    return errors


def _validate_schema_node(
    *,
    value: object,
    schema: object,
    root_schema: dict,
    path: str,
    errors: list[str],
) -> None:
    if not isinstance(schema, dict):
        return
    reference = schema.get("$ref")
    if isinstance(reference, str):
        target = _resolve_local_schema_reference(root_schema, reference)
        if target is None:
            errors.append(f"schema: {path} unresolved reference {reference}")
            return
        _validate_schema_node(
            value=value,
            schema=target,
            root_schema=root_schema,
            path=path,
            errors=errors,
        )
        return

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_schema_type(value, expected_type):
        errors.append(f"schema: {path} must be {expected_type}")
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"schema: {path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"schema: {path} must be one of {schema['enum']!r}")

    if isinstance(value, dict):
        required = schema.get("required") or []
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"schema: {path} missing required field {key}")
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value:
                    _validate_schema_node(
                        value=value[key],
                        schema=child_schema,
                        root_schema=root_schema,
                        path=f"{path}.{key}",
                        errors=errors,
                    )
            if schema.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"schema: {path} has unexpected field {key}")
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_node(
                    value=item,
                    schema=item_schema,
                    root_schema=root_schema,
                    path=f"{path}[{index}]",
                    errors=errors,
                )
    elif isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            errors.append(f"schema: {path} is shorter than {minimum_length}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"schema: {path} does not match {pattern}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"schema: {path} must be at least {minimum}")


def _resolve_local_schema_reference(root_schema: dict, reference: str) -> object | None:
    if not reference.startswith("#/"):
        return None
    current: object = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _matches_schema_type(value: object, expected: object) -> bool:
    types = expected if isinstance(expected, list) else [expected]
    for item in types:
        if item == "object" and isinstance(value, dict):
            return True
        if item == "array" and isinstance(value, list):
            return True
        if item == "string" and isinstance(value, str):
            return True
        if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if item == "boolean" and isinstance(value, bool):
            return True
        if item == "null" and value is None:
            return True
    return False


def render_markdown(evidence: dict) -> str:
    source = evidence.get("source") or {}
    gate = evidence.get("decision_gate") or {}
    completeness = evidence.get("completeness") or {}
    lines = [
        "# 云效工作项证据",
        "",
        f"- 协议：{evidence.get('contract_version') or '-'}",
        f"- 输入编号：{source.get('requested_id') or '-'}",
        f"- 获取时间：{source.get('fetched_at') or '-'}",
        f"- 决策门禁：{gate.get('state') or '-'}",
        f"- 完整性：{completeness.get('status') or '-'}",
        f"- 根工作项：{evidence.get('root_work_item_id') or '-'}",
        "",
        "## 工作项",
        "",
    ]
    for item in evidence.get("work_items") or []:
        lines.extend(
            [
                f"### {item.get('serial_number') or item.get('id') or '-'} {item.get('title') or ''}".rstrip(),
                "",
                f"- 角色：{item.get('role') or '-'}",
                f"- 类型：{item.get('category') or '-'}",
                f"- 父级：{item.get('parent_id') or '-'}",
                f"- 评论：{item.get('comments_status') or '-'} / {len(item.get('comments') or [])}",
                f"- 附件：{item.get('attachments_status') or '-'} / {len(item.get('attachments') or [])}",
                "",
                item.get("description", {}).get("text") or "-",
                "",
            ]
        )
        comments = item.get("comments") or []
        if comments:
            lines.extend(["#### 评论证据", ""])
            for comment in comments:
                author = comment.get("author") or "未知作者"
                created_at = comment.get("created_at") or "未知时间"
                content = comment.get("content") or "-"
                lines.extend([f"- {author} / {created_at}", f"  {content}", ""])
        files = [
            ("附件", file_item)
            for file_item in (item.get("attachments") or [])
        ] + [
            ("内联文件", file_item)
            for file_item in (item.get("inline_files") or [])
        ]
        if files:
            lines.extend(["#### 文件证据", ""])
            for kind, file_item in files:
                digest = file_item.get("sha256") or "未下载"
                lines.append(
                    f"- {kind}：{file_item.get('name') or '-'}；"
                    f"状态：{file_item.get('download_status') or '-'}；SHA-256：{digest}"
                )
            lines.append("")
    lines.extend(["## 关系", ""])
    relations = evidence.get("relations") or []
    if not relations:
        lines.append("- 无。")
    for edge in relations:
        lines.append(f"- {edge.get('from_id') or '-'} --{edge.get('type') or '-'}--> {edge.get('to_id') or '-'}")
    lines.extend(["", "## 告警与错误", ""])
    issues = [*(evidence.get("errors") or []), *(evidence.get("warnings") or [])]
    if not issues:
        lines.append("- 无。")
    for issue in issues:
        lines.append(f"- [{issue.get('code') or '-'}] {issue.get('message') or '-'}")
    lines.extend(
        [
            "",
            "## 安全边界",
            "",
            "- 本证据由只读请求生成。",
            "- 不允许评论、上传、指派、流转、更新、创建、删除或关闭工作项。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(*, evidence: dict, output_dir: str | Path) -> dict[str, str]:
    errors = validate_evidence(evidence)
    if errors:
        raise ValueError("invalid evidence: " + "; ".join(errors))
    target = _checked_output_directory(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "requirement_evidence.v2.json"
    markdown_path = target / "requirement_evidence.v2.md"
    if json_path.exists() or json_path.is_symlink():
        raise FileExistsError(f"证据文件已存在，拒绝覆盖：{json_path}")
    if markdown_path.exists() or markdown_path.is_symlink():
        raise FileExistsError(f"证据文件已存在，拒绝覆盖：{markdown_path}")
    _atomic_create_bytes(
        json_path,
        (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    _atomic_create_bytes(markdown_path, render_markdown(evidence).encode("utf-8"))
    return {"json": str(json_path), "markdown": str(markdown_path)}


def redact_sensitive(value: object, secrets: list[str] | None = None) -> str:
    text = str(value or "")
    for secret in secrets or []:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"(?i)(x-yunxiao-token\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    return text


def redact_for_output(value: object, secrets: list[str] | None = None) -> str:
    return _strip_url_queries(redact_sensitive(value, secrets))


def _new_evidence(*, source: str, requested_id: str, fetched_at: str) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "provider": "yunxiao",
        "mode": "readonly",
        "source": {
            "input": source,
            "requested_id": requested_id,
            "resolved_work_item_id": "",
            "fetched_at": fetched_at,
        },
        "policy": {
            "allowed_actions": ["read"],
            "blocked_actions": list(BLOCKED_ACTIONS),
        },
        "decision_gate": {"state": "fetch_failed", "reasons": []},
        "completeness": {
            "status": "failed",
            "request_count": 0,
            "failed_request_count": 0,
        },
        "root_work_item_id": "",
        "lineage": [],
        "work_items": [],
        "relations": [],
        "warnings": [],
        "errors": [],
        "request_log": [],
        "integrity": {"algorithm": "sha256", "evidence_sha256": ""},
    }


def _first_credential(*, env_keys: tuple[str, ...], file_data: dict) -> tuple[str, str]:
    for key in env_keys:
        value = os.environ.get(key)
        if value:
            return value, f"env:{key}"
    for key in env_keys:
        value = file_data.get(key)
        if isinstance(value, str) and value:
            return value, f"file:{key}"
    return "", ""


def _first_mapping_credential(*, keys: tuple[str, ...], file_data: dict) -> tuple[str, str]:
    for key in keys:
        value = file_data.get(key)
        if isinstance(value, str) and value:
            return value, f"mapping:{key}"
    return "", ""


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read(1200)
    except TypeError:
        body = exc.read()
    detail = body.decode("utf-8", errors="replace") if body else str(exc)
    return detail[:1200]


def _collect_item_content(
    *,
    evidence: dict,
    state: dict,
    client: Any,
    item: dict,
    role: str,
) -> None:
    item_id = item["id"]
    if item_id in state["work_items"]:
        if role == "parent":
            state["work_items"][item_id]["role"] = "parent"
        return
    item["role"] = role
    state["work_items"][item_id] = item

    if not state["include_comments"]:
        item["comments_status"] = "skipped"
        item["comments"] = []
    else:
        comments_result, comment_items = _checked_list_result(
            client.list_comments(item_id),
            label="评论",
            item_kind="comment",
        )
        _record_request(
            evidence,
            operation="list_comments",
            work_item_id=item_id,
            result=comments_result,
            secrets=state["secrets"],
        )
        if comments_result.get("ok"):
            item["comments_status"] = "success"
            item["comments"] = _normalize_comments(comment_items)
        else:
            item["comments_status"] = "failed"
            item["comments"] = []
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code=(
                    "comments_response_invalid"
                    if comments_result.get("invalid_structure")
                    else "comments_read_failed"
                ),
                message=comments_result.get("error") or "评论读取失败。",
                operation="list_comments",
                work_item_id=item_id,
                http_status=comments_result.get("http_status"),
                secrets=state["secrets"],
            )

    if not state["include_attachments"]:
        item["attachments_status"] = "skipped"
        item["attachments"] = []
        item["inline_files"] = []
        return

    attachments_result, attachment_items = _checked_list_result(
        client.list_attachments(item_id),
        label="附件",
        item_kind="attachment",
    )
    _record_request(
        evidence,
        operation="list_attachments",
        work_item_id=item_id,
        result=attachments_result,
        secrets=state["secrets"],
    )
    if attachments_result.get("ok"):
        item["attachments_status"] = "success"
        item["attachments"] = _normalize_attachments(attachment_items)
    else:
        item["attachments_status"] = "failed"
        item["attachments"] = []
        state["incomplete"] = True
        _add_issue(
            evidence,
            "warnings",
            code=(
                "attachments_response_invalid"
                if attachments_result.get("invalid_structure")
                else "attachments_read_failed"
            ),
            message=attachments_result.get("error") or "附件读取失败。",
            operation="list_attachments",
            work_item_id=item_id,
            http_status=attachments_result.get("http_status"),
            secrets=state["secrets"],
        )

    inline_refs = _extract_inline_refs(
        "\n".join(
            [
                item.get("description", {}).get("raw") or "",
                *(comment.get("raw") or "" for comment in item["comments"]),
            ]
        )
    )
    item["inline_files"] = [
        {
            "file_id": ref.get("file_id") or "",
            "name": ref.get("name") or _inline_name(ref.get("source_url") or "", index),
            "source_url": _redact_url(ref["source_url"]),
            "download_status": "skipped",
            "local_path": "",
            "content_type": ref.get("content_type") or "",
            "size": ref.get("size"),
            "sha256": "",
            "source_node_id": ref.get("source_node_id") or "",
        }
        for index, ref in enumerate(inline_refs, start=1)
    ]

    resolved_inline_urls: list[str] = []
    for inline_item, ref in zip(item["inline_files"], inline_refs):
        url = ref["source_url"]
        file_identifier = ref["file_id"]
        if file_identifier and not url and _is_unresolved_inline_placeholder(
            ref,
            fallback_name=inline_item["name"],
        ):
            inline_item["download_status"] = "unavailable"
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="inline_file_reference_unresolved",
                message=(
                    "正文只发现无法回溯来源的内联文件标识，已跳过云效文件详情请求："
                    + file_identifier
                ),
                work_item_id=item_id,
                secrets=state["secrets"],
            )
            resolved_inline_urls.append("")
            continue
        if not file_identifier and (
            str(ref.get("source_node_id") or "").strip()
            or str(ref.get("name") or "").strip()
            or ref.get("size") is not None
        ):
            inline_item["download_status"] = "unavailable"
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="inline_image_identifier_missing",
                message=(
                    "正文内联图片只有富文本节点标识，缺少可调用的 fileIdentifier："
                    + str(ref.get("source_node_id") or "-")
                ),
                work_item_id=item_id,
                secrets=state["secrets"],
            )
            resolved_inline_urls.append("")
            continue
        if file_identifier:
            detail_result = client.get_workitem_file(item_id, file_identifier)
            _record_request(
                evidence,
                operation="get_workitem_file",
                work_item_id=item_id,
                result=detail_result,
                secrets=state["secrets"],
            )
            if not detail_result.get("ok"):
                inline_item["download_status"] = "unavailable"
                state["incomplete"] = True
                _add_issue(
                    evidence,
                    "warnings",
                    code="inline_file_detail_failed",
                    message=detail_result.get("error") or "正文内联文件详情读取失败。",
                    operation="get_workitem_file",
                    work_item_id=item_id,
                    http_status=detail_result.get("http_status"),
                    secrets=state["secrets"],
                )
                resolved_inline_urls.append("")
                continue
            detail = _unwrap_object(detail_result.get("data"))
            url = _first_text(detail, ("url", "downloadUrl", "download_url", "href"))
            inline_item.update(
                {
                    "name": _safe_filename(
                        _first_text(detail, ("name", "fileName", "filename"))
                        or inline_item["name"]
                    ),
                    "source_url": _redact_url(url),
                    "size": detail.get("size"),
                    "content_type": _first_text(
                        detail,
                        ("contentType", "content_type", "mimeType"),
                    ),
                }
            )
            if not url:
                inline_item["download_status"] = "unavailable"
                state["incomplete"] = True
                _add_issue(
                    evidence,
                    "warnings",
                    code="inline_file_download_url_missing",
                    message=f"正文内联文件缺少下载地址：{file_identifier}",
                    work_item_id=item_id,
                    secrets=state["secrets"],
                )
        resolved_inline_urls.append(url)

    if not state["download_files"]:
        return
    if not state["output_dir"]:
        state["incomplete"] = True
        _add_issue(
            evidence,
            "warnings",
            code="download_output_dir_missing",
            message="启用附件下载时必须提供 output_dir。",
            work_item_id=item_id,
            secrets=state["secrets"],
        )
        return

    target_dir = state["output_dir"] / "files" / _safe_filename(item_id)
    for attachment, raw_attachment in zip(
        item["attachments"],
        attachment_items if attachments_result.get("ok") else [],
    ):
        url = _first_text(raw_attachment, ("url", "downloadUrl", "download_url", "href"))
        if not url:
            attachment["download_status"] = "unavailable"
            state["incomplete"] = True
            _add_issue(
                evidence,
                "warnings",
                code="attachment_download_url_missing",
                message=f"附件缺少下载地址：{attachment.get('name') or attachment.get('id')}",
                work_item_id=item_id,
                secrets=state["secrets"],
            )
            continue
        _download_into_evidence(
            evidence=evidence,
            state=state,
            client=client,
            item=attachment,
            url=url,
            target_dir=target_dir,
            work_item_id=item_id,
        )

    for inline_item, url in zip(item["inline_files"], resolved_inline_urls):
        if not url:
            continue
        _download_into_evidence(
            evidence=evidence,
            state=state,
            client=client,
            item=inline_item,
            url=url,
            target_dir=target_dir,
            work_item_id=item_id,
        )


def _download_into_evidence(
    *,
    evidence: dict,
    state: dict,
    client: Any,
    item: dict,
    url: str,
    target_dir: Path,
    work_item_id: str,
) -> None:
    result = client.download_file(url)
    _record_request(
        evidence,
        operation="download_file",
        work_item_id=work_item_id,
        result=result,
        secrets=state["secrets"],
    )
    if not result.get("ok"):
        item["download_status"] = "failed"
        state["incomplete"] = True
        _add_issue(
            evidence,
            "warnings",
            code="file_download_failed",
            message=result.get("error") or f"文件下载失败：{item.get('name') or '-'}",
            operation="download_file",
            work_item_id=work_item_id,
            http_status=result.get("http_status"),
            secrets=state["secrets"],
        )
        return
    data = result.get("data") or {}
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, bytes):
        item["download_status"] = "failed"
        state["incomplete"] = True
        _add_issue(
            evidence,
            "warnings",
            code="file_download_invalid",
            message=f"文件下载结果不是二进制内容：{item.get('name') or '-'}",
            operation="download_file",
            work_item_id=work_item_id,
            secrets=state["secrets"],
        )
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(item.get("name") or "file")
    path = _unique_path(target_dir, filename)
    while True:
        try:
            _atomic_create_bytes(path, content)
            break
        except FileExistsError:
            path = _unique_path(target_dir, filename)
    item.update(
        {
            "download_status": "success",
            "local_path": path.relative_to(state["output_dir"]).as_posix(),
            "content_type": str(data.get("content_type") or item.get("content_type") or ""),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    )


def _normalize_work_item(raw: dict, *, fallback_id: str) -> dict:
    assigned = raw.get("assignedTo") or raw.get("assignee") or raw.get("owner")
    status = raw.get("status") or raw.get("state")
    description_raw = _first_text(raw, ("description", "document", "content", "body", "details"))
    return {
        "id": _first_text(raw, ("id", "workItemId", "workitemId", "identifier")) or fallback_id,
        "serial_number": _first_text(
            raw,
            ("serialNumber", "serial_number", "workitemIdentifier", "workItemIdentifier", "key"),
        )
        or (fallback_id if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", fallback_id) else ""),
        "title": _first_text(raw, ("title", "subject", "name", "summary")),
        "category": _first_text(raw, ("categoryId", "category", "type", "workitemType")),
        "status": _stringify(status),
        "assignee": _stringify(assigned),
        "parent_id": _first_text(raw, ("parentId", "parent_id")),
        "id_path": _normalize_id_path(raw.get("idPath") or raw.get("id_path")),
        "created_at": _first_text(raw, ("gmtCreate", "createdAt", "created_at")),
        "modified_at": _first_text(raw, ("gmtModified", "modifiedAt", "modified_at")),
        "description": {
            "format": _first_text(raw, ("formatType", "documentFormat", "descriptionFormat")) or "UNKNOWN",
            "raw": redact_sensitive(description_raw),
            "text": _html_to_text(description_raw),
        },
        "comments_status": "pending",
        "comments": [],
        "attachments_status": "pending",
        "attachments": [],
        "inline_files": [],
    }


def _normalize_comments(raw_items: list[object]) -> list[dict]:
    comments: list[dict] = []
    for raw in raw_items:
        if isinstance(raw, str):
            comments.append(
                {
                    "id": "",
                    "author": "",
                    "created_at": "",
                    "format": "UNKNOWN",
                    "raw": redact_sensitive(raw),
                    "content": _html_to_text(raw),
                }
            )
            continue
        if not isinstance(raw, dict):
            continue
        content = _first_text(raw, ("content", "comment", "body", "text", "description"))
        comments.append(
            {
                "id": _first_text(raw, ("id", "commentId", "comment_id")),
                "author": _stringify(raw.get("user") or raw.get("author") or raw.get("creator")),
                "created_at": _first_text(raw, ("gmtCreate", "createdAt", "created_at")),
                "format": _first_text(raw, ("contentFormat", "formatType", "format")) or "UNKNOWN",
                "raw": redact_sensitive(content),
                "content": _html_to_text(content),
            }
        )
    return comments


def _normalize_attachments(raw_items: list[object]) -> list[dict]:
    attachments: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        raw_name = _first_text(raw, ("fileName", "filename", "name", "title"))
        attachments.append(
            {
                "id": _first_text(raw, ("id", "attachmentId", "attachment_id")),
                "file_id": _first_text(raw, ("fileId", "file_id", "identifier")),
                "name": _safe_filename(raw_name or "attachment"),
                "suffix": _first_text(raw, ("suffix", "extension")),
                "source_url": _redact_url(
                    _first_text(raw, ("url", "downloadUrl", "download_url", "href"))
                ),
                "size": raw.get("size"),
                "created_at": _first_text(raw, ("gmtCreate", "createdAt", "created_at")),
                "download_status": "skipped",
                "local_path": "",
                "content_type": _first_text(raw, ("contentType", "content_type", "mimeType")),
                "sha256": "",
            }
        )
    return attachments


def _normalize_relations(raw_items: list[object], *, from_id: str, fallback_type: str) -> list[dict]:
    edges: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        resource_type = _first_text(raw, ("resourceType", "resource_type")) or "WORKITEM"
        target = _first_text(
            raw,
            ("resourceId", "relatedWorkitemId", "relatedWorkItemId", "workitemId", "toId"),
        )
        if resource_type.upper() != "WORKITEM" or not target:
            continue
        edges.append(
            {
                "record_id": _first_text(raw, ("id", "recordId", "relationId")),
                "from_id": from_id,
                "to_id": target,
                "type": (
                    _first_text(raw, ("relationType", "relation_type")) or fallback_type
                ).upper(),
                "resource_type": resource_type.upper(),
                "created_at": _first_text(raw, ("gmtCreate", "createdAt", "created_at")),
            }
        )
    return edges


def _ordered_parent_candidates(item: dict, relation_parent_ids: list[str]) -> list[str]:
    candidates: list[str] = []
    if item.get("parent_id"):
        candidates.append(item["parent_id"])
    path = item.get("id_path") or []
    if item.get("id") in path:
        current_index = path.index(item["id"])
        if current_index > 0:
            candidates.append(path[current_index - 1])
    elif path:
        candidates.append(path[-1])
    candidates.extend(relation_parent_ids)
    return list(dict.fromkeys(item for item in candidates if item))


def _add_relation(state: dict, edge: dict) -> None:
    key = (edge.get("from_id"), edge.get("to_id"), edge.get("type"))
    if key in state["relation_keys"]:
        return
    state["relation_keys"].add(key)
    state["relations"].append(edge)


def _record_request(
    evidence: dict,
    *,
    operation: str,
    work_item_id: str,
    result: dict,
    secrets: list[str],
    relation_type: str = "",
) -> None:
    evidence["request_log"].append(
        {
            "operation": operation,
            "work_item_id": work_item_id,
            "relation_type": relation_type,
            "status": "success" if result.get("ok") else "failed",
            "http_status": result.get("http_status"),
            "error": redact_sensitive(result.get("error") or "", secrets),
        }
    )


def _add_issue(
    evidence: dict,
    bucket: str,
    *,
    code: str,
    message: object,
    secrets: list[str],
    operation: str = "",
    work_item_id: str = "",
    relation_type: str = "",
    http_status: object = None,
) -> None:
    issue = {
        "code": code,
        "message": redact_sensitive(message, secrets),
        "operation": operation,
        "work_item_id": work_item_id,
        "relation_type": relation_type,
        "http_status": http_status,
    }
    key = (code, operation, work_item_id, relation_type, http_status, issue["message"])
    existing = {
        (
            item.get("code"),
            item.get("operation"),
            item.get("work_item_id"),
            item.get("relation_type"),
            item.get("http_status"),
            item.get("message"),
        )
        for item in evidence[bucket]
    }
    if key not in existing:
        evidence[bucket].append(issue)


def _finalize(evidence: dict, state: dict) -> dict:
    evidence["work_items"] = list(state["work_items"].values())
    evidence["relations"] = list(state["relations"])
    failed_requests = sum(
        1 for item in evidence["request_log"] if item.get("status") == "failed"
    )
    evidence["completeness"]["request_count"] = len(evidence["request_log"])
    evidence["completeness"]["failed_request_count"] = failed_requests
    if state["current_failed"]:
        status = "failed"
        gate = "fetch_failed"
    elif state["incomplete"] or evidence["errors"]:
        status = "partial"
        gate = "needs_requirement_confirmation"
    else:
        status = "complete"
        gate = "ready_for_analysis"
    evidence["completeness"]["status"] = status
    reasons = [item["code"] for item in [*evidence["errors"], *evidence["warnings"]]]
    evidence["decision_gate"] = {"state": gate, "reasons": list(dict.fromkeys(reasons))}
    _redact_tree_in_place(evidence, state["secrets"])
    evidence["integrity"]["evidence_sha256"] = _evidence_hash(evidence)
    return evidence


def _evidence_hash(evidence: dict) -> str:
    payload = copy.deepcopy(evidence)
    payload.pop("integrity", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unwrap_object(data: object) -> dict:
    if not isinstance(data, dict):
        return {}
    for key in ("workitem", "workItem", "workitemFile", "data", "result"):
        value = data.get(key)
        if isinstance(value, dict):
            return _unwrap_object(value)
    return data


def _is_work_item_payload(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    identity = _first_text(
        value,
        (
            "id",
            "workItemId",
            "workitemId",
            "identifier",
            "serialNumber",
            "serial_number",
            "workitemIdentifier",
            "workItemIdentifier",
            "key",
        ),
    )
    substance = _first_text(
        value,
        (
            "title",
            "subject",
            "name",
            "summary",
            "description",
            "document",
            "content",
            "body",
            "details",
        ),
    )
    return bool(identity and substance)


def _checked_list_result(
    result: dict,
    *,
    label: str,
    item_kind: str,
) -> tuple[dict, list[object]]:
    if not result.get("ok"):
        return result, []
    items, valid = _unwrap_list_checked(result.get("data"))
    if valid:
        valid = all(_is_valid_list_item(item, item_kind=item_kind) for item in items)
    if valid:
        return result, items
    invalid = dict(result)
    invalid.update(
        {
            "ok": False,
            "data": None,
            "error": f"{label}接口返回结构无法识别。",
            "invalid_structure": True,
        }
    )
    return invalid, []


def _is_valid_list_item(item: object, *, item_kind: str) -> bool:
    if item_kind == "comment":
        if isinstance(item, str):
            return bool(item.strip())
        if not isinstance(item, dict):
            return False
        return any(
            isinstance(item.get(key), str)
            for key in ("content", "comment", "body", "text", "description")
            if key in item
        )
    if item_kind == "attachment":
        if not isinstance(item, dict):
            return False
        return bool(
            _first_text(
                item,
                (
                    "id",
                    "attachmentId",
                    "attachment_id",
                    "fileId",
                    "file_id",
                    "identifier",
                    "fileName",
                    "filename",
                    "name",
                    "title",
                    "url",
                    "downloadUrl",
                    "download_url",
                    "href",
                ),
            )
        )
    if item_kind == "relation":
        if not isinstance(item, dict):
            return False
        return bool(
            _first_text(item, ("relationType", "relation_type"))
            and _first_text(item, ("resourceType", "resource_type"))
            and _first_text(
                item,
                (
                    "resourceId",
                    "relatedWorkitemId",
                    "relatedWorkItemId",
                    "workitemId",
                    "toId",
                ),
            )
        )
    return False


def _unwrap_list_checked(data: object) -> tuple[list[object], bool]:
    if isinstance(data, list):
        return data, True
    if not isinstance(data, dict):
        return [], False
    for key in (
        "data",
        "result",
        "items",
        "records",
        "comments",
        "attachments",
        "relationRecords",
    ):
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, list):
            return value, True
        if isinstance(value, dict):
            return _unwrap_list_checked(value)
        return [], False
    return [], False


def _first_text(value: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in value:
            text = _stringify(value.get(key))
            if text:
                return text
    return ""


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "displayName", "display_name", "label", "value", "title", "id"):
            text = _stringify(value.get(key))
            if text:
                return text
    return ""


def _normalize_id_path(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _html_to_text(value: object) -> str:
    raw = html.unescape(str(value or ""))
    parser = _TextExtractor()
    try:
        parser.feed(raw)
    except Exception:
        return redact_sensitive(re.sub(r"<[^>]+>", " ", raw)).strip()
    text = "\n".join(parser.parts)
    return redact_sensitive(re.sub(r"[ \t]+", " ", text)).strip()


def _extract_inline_refs(value: str) -> list[dict[str, Any]]:
    parser = _ImageExtractor()
    try:
        parser.feed(value)
    except Exception:
        pass
    markdown_urls = re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)", value)
    urls = [*parser.urls, *markdown_urls]
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for ref in _extract_jsonml_inline_refs(value):
        file_id = str(ref.get("file_id") or "").strip()
        node_id = str(ref.get("source_node_id") or "").strip()
        source_url = str(ref.get("source_url") or "").strip()
        key = file_id or (f"node:{node_id}" if node_id else source_url)
        if not key or key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    for raw_url in urls:
        url = html.unescape(raw_url).strip()
        if not url.lower().startswith(("https://", "http://")):
            continue
        file_id = _extract_file_identifier(url)
        key = file_id or url
        if key in seen:
            continue
        seen.add(key)
        refs.append({"file_id": file_id, "source_url": url, "name": "", "size": None, "content_type": "", "source_node_id": ""})
    for file_id in re.findall(
        r"fileIdentifier[\"'=:%20]+([A-Za-z0-9_-]+)",
        value,
        re.IGNORECASE,
    ):
        if file_id in seen:
            continue
        seen.add(file_id)
        refs.append({"file_id": file_id, "source_url": "", "name": "", "size": None, "content_type": "", "source_node_id": ""})
    return refs


def _extract_jsonml_inline_refs(value: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        marker = '"jsonMLValue"'
        marker_start = value.find(marker)
        start = value.find("[", marker_start)
        if marker_start < 0 or start < 0:
            return []
        try:
            payload, _ = json.JSONDecoder().raw_decode(value[start:])
        except json.JSONDecodeError:
            return []

    refs: list[dict[str, Any]] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            for child in node.values():
                visit(child)
            return
        if not isinstance(node, list):
            return
        if len(node) >= 2 and node[0] == "img" and isinstance(node[1], dict):
            attrs = node[1]
            source_url = html.unescape(str(attrs.get("src") or "").strip())
            explicit_file_id = str(
                attrs.get("fileIdentifier")
                or attrs.get("fileidentifier")
                or attrs.get("fileId")
                or attrs.get("file_id")
                or ""
            ).strip()
            refs.append(
                {
                    "file_id": explicit_file_id or _extract_file_identifier(source_url),
                    "source_url": source_url,
                    "name": str(attrs.get("name") or attrs.get("fileName") or "").strip(),
                    "size": attrs.get("size"),
                    "content_type": str(attrs.get("contentType") or attrs.get("mimeType") or ""),
                    "source_node_id": str(attrs.get("id") or "").strip(),
                }
            )
        for child in node:
            visit(child)

    visit(payload)
    return refs


def _extract_file_identifier(url: str) -> str:
    parsed = urlsplit(html.unescape(url))
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("fileIdentifier", "fileidentifier", "fileId", "id"):
        values = query.get(key)
        if values:
            return values[0]
    match = re.search(
        r"fileIdentifier[=/]([A-Za-z0-9_-]+)",
        html.unescape(url),
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _redact_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _sanitize_source(value: object, secrets: list[str]) -> str:
    text = redact_sensitive(value, secrets).strip()
    if "://" not in text:
        return text
    return _redact_url(text)


def _redact_tree_in_place(value: object, secrets: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                value[key] = redact_for_output(item, secrets)
            else:
                _redact_tree_in_place(item, secrets)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                value[index] = redact_for_output(item, secrets)
            else:
                _redact_tree_in_place(item, secrets)


def _strip_url_queries(value: str) -> str:
    pattern = re.compile(r"""https?://[^\s"'<>]+""", re.IGNORECASE)

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        suffix = ""
        while raw and raw[-1] in ".,;)]}":
            suffix = raw[-1] + suffix
            raw = raw[:-1]
        return _redact_url(raw) + suffix

    return pattern.sub(replace, value)


def _inline_name(url: str, index: int) -> str:
    path_name = Path(urlsplit(url).path).name
    return _safe_filename(path_name or f"inline-{index}")


def _is_unresolved_inline_placeholder(
    ref: Mapping[str, object],
    *,
    fallback_name: str = "",
) -> bool:
    """Recognize a bare generated ref, not a real rich-text image.

    Yunxiao can leave old ``fileIdentifier`` tokens in serialized rich text
    without a URL, name, size, or editor node.  Calling the file API for such
    a token only creates a misleading "file not found" warning.  Keep it as
    an unavailable evidence record, but do not turn it into a network error.
    """
    name = str(ref.get("name") or fallback_name or "").strip().lower()
    return bool(
        re.fullmatch(r"inline-\d+", name)
        and not str(ref.get("source_node_id") or "").strip()
        and ref.get("size") is None
    )


def _safe_filename(value: object) -> str:
    name = Path(str(value or "file").replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f/:*?\"<>|]+", "_", name)
    return name[:180] or "file"


def _prepare_new_output_directory(value: str | Path) -> Path:
    target = _checked_output_directory(value)
    if target.exists():
        if not target.is_dir():
            raise ValueError(f"输出路径不是目录：{target}")
        if any(target.iterdir()):
            raise ValueError(f"输出目录必须为空，拒绝复用证据修订：{target}")
    else:
        target.mkdir(parents=True, exist_ok=False)
        _assert_no_symlink_components(target)
    return target


def _checked_output_directory(value: str | Path) -> Path:
    requested = Path(value).expanduser().absolute()
    _assert_no_symlink_components(requested)
    target = requested.resolve(strict=False)
    if target.exists() and not target.is_dir():
        raise ValueError(f"输出路径不是目录：{target}")
    return target


def _assert_no_symlink_components(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError(f"输出路径不允许包含符号链接：{candidate}")


def _atomic_create_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(path.parent)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _unique_path(target_dir: Path, filename: str) -> Path:
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        candidate = target_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1
