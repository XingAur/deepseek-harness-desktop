from __future__ import annotations

import json
import hashlib
import html
import mimetypes
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from app.llm_client import redact_secrets


DEFAULT_YUNXIAO_BASE_URL = "https://openapi-rdc.aliyuncs.com"
DEFAULT_CREDENTIALS_FILE = "/Users/lym/WorkCode/ai/apiKey/credentials.json"
YUNXIAO_TIMEOUT_SECONDS = 30
MAX_EVIDENCE_TEXT_CHARS = 24000
MAX_INLINE_DOWNLOAD_BYTES = 8 * 1024 * 1024
DEFAULT_PARENT_CHAIN_DEPTH = 5
# A missing inline image often means that Yunxiao has expired or deleted the
# rich-text URL.  It must be visible in the evidence record, but it is not a
# reason to stop code/service discovery when the ticket title and body remain
# readable.  Comments and ordinary attachments deliberately stay outside this
# set because they can carry the actual business rule.
NON_BLOCKING_INLINE_MEDIA_WARNINGS = frozenset(
    (
        "inline_image_detail_failed",
        "inline_image_download_failed",
        "inline_image_identifier_missing",
        "inline_file_reference_unresolved",
        # Keep these legacy names non-blocking so historical evidence files
        # receive the same treatment after an upgrade.
        "inline_file_detail_failed",
        "inline_file_download_url_missing",
        "inline_file_download_failed",
        "file_download_failed",
    )
)
# This legacy module remains for existing read-only evidence collection only.
# Manager execution must use app.providers.yunxiao and ProviderExecutionContext.
LEGACY_YUNXIAO_READ_COMPATIBILITY_ONLY = True


@dataclass
class YunxiaoCredentialBundle:
    pat: str = ""
    organization_id: str = ""
    project_id: str = ""
    pat_source: str = ""
    organization_source: str = ""
    project_source: str = ""
    missing_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.pat and self.organization_id)

    def safe_summary(self) -> dict:
        return {
            "pat": "present" if self.pat else "missing",
            "organization_id": "present" if self.organization_id else "missing",
            "project_id": "present" if self.project_id else "missing",
            "pat_source": self.pat_source,
            "organization_source": self.organization_source,
            "project_source": self.project_source,
            "missing_keys": self.missing_keys,
        }


def collect_yunxiao_evidence(
    *,
    yunxiao_url: str,
    demand_text: str,
    output_dir: str | Path | None = None,
    include_comments: bool = True,
    download_policy: str = "bounded",
    max_download_bytes: int | None = MAX_INLINE_DOWNLOAD_BYTES,
    include_parent_chain: bool = True,
    max_parent_depth: int = DEFAULT_PARENT_CHAIN_DEPTH,
) -> dict:
    if download_policy not in {"bounded", "archive"}:
        raise ValueError("download_policy 只能是 bounded 或 archive")
    if max_download_bytes is not None and max_download_bytes < 0:
        raise ValueError("max_download_bytes 必须是非负整数或 None")
    if max_parent_depth < 0:
        raise ValueError("max_parent_depth 必须是非负整数")
    archive_mode = download_policy == "archive"
    credentials = load_yunxiao_credentials()
    work_item_id = parse_work_item_id(yunxiao_url) or parse_work_item_id(demand_text)
    evidence = {
        "status": "pending",
        "mode": "readonly",
        "yunxiao_url": yunxiao_url,
        "work_item_id": work_item_id,
        "credential_summary": credentials.safe_summary(),
        "policy": {
            "allowed_actions": ["read"],
            "blocked_actions": [
                "comment",
                "upload_attachment",
                "assign",
                "transition",
                "update_iteration",
                "update_service_change",
                "create_task",
                "link_artifact",
                "close",
            ],
            "note": "v0.7.4+ 只读取云效证据；即使令牌具备写权限，也不写评论、不上传附件、不流转状态、不改负责人。",
        },
        "work_item": {},
        "description": {},
        "clean_text": "",
        "html_excerpt": "",
        "attachments": [],
        "comments": [],
        "comment_read": {"status": "pending", "error": ""},
        "inline_files": [],
        "file_details": [],
        "inline_file_downloads": [],
        "parent_work_items": [],
        "parent_chain": {
            "status": "pending",
            "requested": include_parent_chain,
            "max_depth": max_parent_depth,
            "items": [],
            "warnings": [],
        },
        "download_policy": download_policy,
        "request_attempts": [],
        "text_excerpt": "",
        "error": "",
        "warnings": [],
        "completeness": {"status": "pending", "optional_failures": []},
        "decision_gate": {"state": "pending", "reason": ""},
    }
    if not work_item_id:
        evidence["status"] = "failed"
        evidence["error"] = "无法从云效 URL 或需求文本中解析工作项编号。"
        return evidence
    if not credentials.ok:
        evidence["status"] = "failed"
        evidence["error"] = "缺少云效只读凭证：" + "、".join(credentials.missing_keys)
        return evidence

    client = YunxiaoReadClient(credentials=credentials)
    work_item = client.get_work_item_info(work_item_id)
    evidence["request_attempts"].extend(work_item.get("attempts", []))
    if not work_item.get("ok"):
        evidence["status"] = "failed"
        evidence["error"] = work_item.get("error") or "云效工作项详情读取失败。"
        return evidence
    evidence["work_item"] = work_item.get("data") or {}
    description = extract_description_evidence(evidence["work_item"])
    evidence["description"] = description
    evidence["clean_text"] = description.get("clean_text") or ""
    evidence["html_excerpt"] = description.get("html_excerpt") or ""

    if include_comments:
        comments = client.list_comments(work_item_id)
        evidence["request_attempts"].extend(comments.get("attempts", []))
        if comments.get("ok"):
            evidence["comments"] = normalize_comment_list(comments.get("data"))
            evidence["comment_read"] = {"status": "success", "error": ""}
        else:
            evidence["comment_read"] = {"status": "warning", "error": comments.get("error") or "评论读取失败"}
            evidence["warnings"].append("comments_read_failed")
    else:
        evidence["comment_read"] = {"status": "skipped", "error": "", "reason": "user_instruction"}

    current_media = _collect_work_item_media(
        client=client,
        work_item_id=work_item_id,
        work_item=evidence["work_item"],
        output_dir=Path(output_dir) if output_dir else None,
        archive_mode=archive_mode,
        include_comments=include_comments,
        max_download_bytes=max_download_bytes,
        role="requested",
    )
    for key in ("attachments", "inline_files", "file_details", "inline_file_downloads"):
        evidence[key] = current_media[key]
    evidence["request_attempts"].extend(current_media["attempts"])
    evidence["warnings"].extend(current_media["warnings"])

    if include_parent_chain:
        parent_chain = _collect_parent_chain(
            client=client,
            requested_work_item=evidence["work_item"],
            output_dir=Path(output_dir) if output_dir else None,
            archive_mode=archive_mode,
            include_comments=include_comments,
            max_download_bytes=max_download_bytes,
            max_depth=max_parent_depth,
            requested_work_item_id=work_item_id,
        )
        evidence["parent_work_items"] = parent_chain["items"]
        evidence["parent_chain"] = {
            "status": parent_chain["status"],
            "requested": True,
            "max_depth": max_parent_depth,
            "items": [
                {
                    "depth": item.get("depth"),
                    "work_item_id": item.get("work_item_id"),
                    "serial_number": item.get("serial_number"),
                    "title": item.get("title"),
                    "attachment_count": len(item.get("attachments") or []),
                    "download_count": sum(
                        1
                        for media in item.get("inline_file_downloads") or []
                        if isinstance(media, dict) and media.get("status") == "success"
                    ),
                }
                for item in parent_chain["items"]
            ],
            "warnings": parent_chain["warnings"],
        }
        evidence["request_attempts"].extend(parent_chain["attempts"])
        evidence["warnings"].extend(parent_chain["warnings"])
        for parent in parent_chain["items"]:
            for key in ("attachments", "inline_files", "file_details", "inline_file_downloads"):
                evidence[key].extend(parent.get(key) or [])
    else:
        evidence["parent_chain"] = {
            "status": "skipped",
            "requested": False,
            "max_depth": max_parent_depth,
            "items": [],
            "warnings": [],
        }
    evidence["text_excerpt"] = build_text_excerpt(evidence)
    optional_failures = list(dict.fromkeys(evidence["warnings"]))
    evidence["completeness"] = {
        "status": "partial" if optional_failures else "complete",
        "optional_failures": optional_failures,
    }
    blocking_failures = [
        warning
        for warning in optional_failures
        if warning not in NON_BLOCKING_INLINE_MEDIA_WARNINGS
    ]
    if optional_failures:
        evidence["status"] = "partial"
        if blocking_failures:
            evidence["decision_gate"] = {
                "state": "needs_requirement_confirmation",
                "reason": "云效主需求可读，但评论或普通附件等可能包含业务规则的证据不可用；已保留警告并继续只读分析。",
            }
        else:
            evidence["decision_gate"] = {
                "state": "ready_for_analysis_with_warnings",
                "reason": "云效主需求可读，失效内联图片已标记为缺失证据；继续完整只读分析，不阻断项目识别、调用链和改动方案。",
            }
    else:
        evidence["status"] = "success"
        evidence["decision_gate"] = {
            "state": "ready_for_analysis",
            "reason": "云效主需求及可选证据已读取。",
        }
    return evidence


def _collect_work_item_media(
    *,
    client: "YunxiaoReadClient",
    work_item_id: str,
    work_item: dict,
    output_dir: Path | None,
    archive_mode: bool,
    include_comments: bool,
    max_download_bytes: int | None,
    role: str,
) -> dict:
    """Collect one work item's comments and media with bounded local downloads."""
    attempts: list[dict] = []
    warnings: list[str] = []
    attachments_result = client.list_attachments(work_item_id)
    attempts.extend(attachments_result.get("attempts", []))
    if attachments_result.get("ok"):
        attachments = normalize_attachment_list(
            attachments_result.get("data"),
            limit=None if archive_mode else 30,
        )
    else:
        attachments = []
        warnings.append(
            "attachments_read_failed"
            if role == "requested"
            else "parent_requirement_attachments_read_failed"
        )

    description = extract_description_evidence(work_item)
    inline_files = collect_inline_files_from_work_item(
        work_item,
        attachments,
        description,
        limit=None if archive_mode else 40,
    )
    if any(
        item.get("kind") == "inline_image"
        and not str(item.get("identifier") or "").strip()
        and (
            str(item.get("source_node_id") or "").strip()
            or str(item.get("name") or "").strip()
            or item.get("size") is not None
        )
        for item in inline_files
        if isinstance(item, dict)
    ):
        warnings.append("inline_image_identifier_missing")

    media_output_dir = output_dir
    if output_dir is not None and role != "requested":
        media_output_dir = output_dir / "yunxiao_parent_files" / safe_filename(work_item_id)
    file_details_result = client.collect_file_details(
        work_item_id,
        inline_files,
        output_dir=media_output_dir,
        max_files=None if archive_mode else 10,
        max_download_bytes=max_download_bytes,
    )
    attempts.extend(file_details_result.get("attempts", []))
    file_details = file_details_result.get("items", [])
    failed_file_details = [
        item
        for item in file_details
        if isinstance(item, dict) and item.get("status") == "failed"
    ]
    if any(item.get("kind") == "inline_image" for item in failed_file_details):
        warnings.append("inline_image_detail_failed")
    if any(item.get("kind") != "inline_image" for item in failed_file_details):
        warnings.append(
            "inline_file_detail_failed"
            if role == "requested"
            else "parent_requirement_attachment_detail_failed"
        )

    inline_file_downloads: list[dict] = []
    if output_dir is not None:
        inline_file_downloads = collect_file_detail_downloads(file_details)
        downloaded_identifiers = {
            item.get("identifier")
            for item in inline_file_downloads
            if item.get("status") == "success"
        }
        fallback_files = [
            item
            for item in inline_files
            if item.get("identifier") not in downloaded_identifiers
        ]
        downloads = client.download_inline_files(
            files=fallback_files,
            output_dir=media_output_dir or output_dir,
            max_files=None if archive_mode else 12,
            max_download_bytes=max_download_bytes,
        )
        inline_file_downloads = inline_file_downloads + downloads.get("items", [])
        attempts.extend(downloads.get("attempts", []))
        failed_downloads = [
            item
            for item in inline_file_downloads
            if isinstance(item, dict) and item.get("status") == "failed"
        ]
        if any(item.get("kind") == "inline_image" for item in failed_downloads):
            warnings.append("inline_image_download_failed")
        if any(item.get("kind") != "inline_image" for item in failed_downloads):
            warnings.append(
                "inline_file_download_failed"
                if role == "requested"
                else "parent_requirement_attachment_download_failed"
            )

    source_id = str(
        work_item.get("id")
        or work_item.get("workItemId")
        or work_item.get("workitemId")
        or work_item_id
    ).strip()
    source_serial = _work_item_text(work_item, ("serialNumber", "serial_number", "identifier"))
    for collection in (attachments, inline_files, file_details, inline_file_downloads):
        for item in collection:
            if isinstance(item, dict):
                item.setdefault("source_work_item_id", source_id)
                item.setdefault("source_work_item_serial_number", source_serial)
                item.setdefault("source_work_item_role", role)

    return {
        "attachments": attachments,
        "inline_files": inline_files,
        "file_details": file_details,
        "inline_file_downloads": inline_file_downloads,
        "attempts": attempts,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _collect_parent_chain(
    *,
    client: "YunxiaoReadClient",
    requested_work_item: dict,
    output_dir: Path | None,
    archive_mode: bool,
    include_comments: bool,
    max_download_bytes: int | None,
    max_depth: int,
    requested_work_item_id: str,
) -> dict:
    """Read parent requirements referenced by ``parentId`` before analysis."""
    items: list[dict] = []
    attempts: list[dict] = []
    warnings: list[str] = []
    visited = {
        str(value).strip()
        for value in (
            requested_work_item_id,
            requested_work_item.get("id"),
            requested_work_item.get("serialNumber"),
        )
        if str(value or "").strip()
    }
    parent_ref = _parent_work_item_reference(requested_work_item)
    depth = 0
    while parent_ref and depth < max_depth:
        if parent_ref in visited:
            warnings.append("parent_requirement_cycle")
            break
        visited.add(parent_ref)
        depth += 1
        parent_result = client.get_work_item_info(parent_ref)
        attempts.extend(parent_result.get("attempts", []))
        if not parent_result.get("ok"):
            warnings.append("parent_requirement_read_failed")
            break
        parent_work_item = parent_result.get("data") or {}
        if not isinstance(parent_work_item, dict):
            warnings.append("parent_requirement_invalid")
            break
        parent_id = str(
            parent_work_item.get("id")
            or parent_work_item.get("workItemId")
            or parent_work_item.get("workitemId")
            or parent_ref
        ).strip()
        parent_serial = _work_item_text(
            parent_work_item,
            ("serialNumber", "serial_number", "identifier"),
        )
        media = _collect_work_item_media(
            client=client,
            work_item_id=parent_id,
            work_item=parent_work_item,
            output_dir=output_dir,
            archive_mode=archive_mode,
            include_comments=include_comments,
            max_download_bytes=max_download_bytes,
            role="parent",
        )
        parent_comments: list[dict] = []
        if include_comments:
            comments_result = client.list_comments(parent_id)
            attempts.extend(comments_result.get("attempts", []))
            if comments_result.get("ok"):
                parent_comments = normalize_comment_list(comments_result.get("data"))
            else:
                warnings.append("parent_requirement_comments_read_failed")
        item = {
            "role": "parent",
            "depth": depth,
            "requested_id": parent_ref,
            "work_item_id": parent_id,
            "serial_number": parent_serial,
            "title": _work_item_text(parent_work_item, ("subject", "title", "name", "summary")),
            "work_item": parent_work_item,
            "description": extract_description_evidence(parent_work_item),
            "comments": parent_comments,
            **media,
        }
        items.append(item)
        attempts.extend(media["attempts"])
        warnings.extend(media["warnings"])
        parent_ref = _parent_work_item_reference(parent_work_item)
    if parent_ref and depth >= max_depth:
        warnings.append("parent_requirement_depth_exceeded")
    unique_warnings = list(dict.fromkeys(warnings))
    return {
        "status": "partial" if unique_warnings else "complete",
        "items": items,
        "attempts": attempts,
        "warnings": unique_warnings,
    }


def _parent_work_item_reference(work_item: dict) -> str:
    for key in (
        "parentId",
        "parent_id",
        "parentWorkItemId",
        "parentWorkitemId",
        "parentRequirementId",
        "parent_requirement_id",
    ):
        value = work_item.get(key)
        if isinstance(value, dict):
            value = value.get("id") or value.get("identifier") or value.get("serialNumber")
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _work_item_text(work_item: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = work_item.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("value") or value.get("id")
        if str(value or "").strip():
            return str(value).strip()
    return ""


class YunxiaoReadClient:
    def __init__(self, *, credentials: YunxiaoCredentialBundle, base_url: str = "") -> None:
        self.credentials = credentials
        self.base_url = (base_url or yunxiao_base_url()).rstrip("/")

    def get_work_item_info(self, work_item_id: str) -> dict:
        candidates = [
            f"{self.base_url}/oapi/v1/projex/organizations/{quote(self.credentials.organization_id)}/workitems/{quote(work_item_id)}",
            f"{self.base_url}/oapi/v1/projex/workitems/{quote(work_item_id)}?organizationId={quote(self.credentials.organization_id)}",
            f"{self.base_url}/oapi/v1/projex/organization/{quote(self.credentials.organization_id)}/workitem/{quote(work_item_id)}",
        ]
        return self._first_success(candidates=candidates, label="GetWorkItemInfo")

    def list_attachments(self, work_item_id: str) -> dict:
        candidates = [
            f"{self.base_url}/oapi/v1/projex/organizations/{quote(self.credentials.organization_id)}/workitems/{quote(work_item_id)}/attachments",
            f"{self.base_url}/oapi/v1/projex/workitems/{quote(work_item_id)}/attachments?organizationId={quote(self.credentials.organization_id)}",
        ]
        return self._first_success(candidates=candidates, label="ListWorkitemAttachments")

    def list_comments(self, work_item_id: str) -> dict:
        candidates = [
            f"{self.base_url}/oapi/v1/projex/organizations/{quote(self.credentials.organization_id)}/workitems/{quote(work_item_id)}/comments",
            f"{self.base_url}/oapi/v1/projex/workitems/{quote(work_item_id)}/comments?organizationId={quote(self.credentials.organization_id)}",
        ]
        return self._first_success(candidates=candidates, label="ListWorkitemComments")

    def collect_file_details(
        self,
        work_item_id: str,
        files: list[dict],
        output_dir: Path | None = None,
        max_files: int | None = 10,
        max_download_bytes: int | None = MAX_INLINE_DOWNLOAD_BYTES,
    ) -> dict:
        attempts: list[dict] = []
        items: list[dict] = []
        target_dir = output_dir / "yunxiao_inline_files" if output_dir else None
        seen: set[str] = set()
        selected_files = files if max_files is None else files[:max_files]
        for file_ref in selected_files:
            identifier = str(file_ref.get("identifier") or file_ref.get("fileIdentifier") or file_ref.get("fileId") or "").strip()
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            detail = self.get_workitem_file(work_item_id=work_item_id, file_identifier=identifier)
            attempts.extend(detail.get("attempts", []))
            normalized_detail = normalize_file_detail(detail.get("data")) if detail.get("ok") else {}
            download = {}
            raw_data = detail.get("data")
            raw_url = raw_data.get("url") if isinstance(raw_data, dict) else ""
            if target_dir and detail.get("ok") and raw_url:
                download_result = self._download_file(
                    url=str(raw_url),
                    identifier=identifier,
                    target_dir=target_dir,
                    max_download_bytes=max_download_bytes,
                )
                attempts.append(download_result["attempt"])
                download = {
                    "identifier": identifier,
                    "name": normalized_detail.get("name") or file_ref.get("name") or "",
                    "kind": file_ref.get("kind") or "",
                    "status": "success" if download_result.get("ok") else "failed",
                    "path": download_result.get("path") or "",
                    "size": download_result.get("size"),
                    "sha256": download_result.get("sha256") or "",
                    "content_type": download_result.get("content_type") or "",
                    "error": download_result.get("error") or "",
                }
            items.append(
                {
                    "identifier": identifier,
                    "name": file_ref.get("name") or "",
                    "kind": file_ref.get("kind") or "",
                    "status": "success" if detail.get("ok") else "failed",
                    "data": normalized_detail,
                    "download": download,
                    "error": detail.get("error") or "",
                }
            )
        return {"items": items, "attempts": attempts}

    def get_workitem_file(self, *, work_item_id: str, file_identifier: str) -> dict:
        candidates = [
            f"{self.base_url}/oapi/v1/projex/organizations/{quote(self.credentials.organization_id)}/workitems/{quote(work_item_id)}/files/{quote(file_identifier)}",
            f"{self.base_url}/oapi/v1/projex/organizations/{quote(self.credentials.organization_id)}/workitems/{quote(work_item_id)}/attachments/{quote(file_identifier)}",
            f"{self.base_url}/oapi/v1/projex/workitems/{quote(work_item_id)}/files/{quote(file_identifier)}?organizationId={quote(self.credentials.organization_id)}",
            (
                f"{self.base_url}/oapi/v1/projex/workitem/file"
                f"?organizationId={quote(self.credentials.organization_id)}"
                f"&workitemId={quote(work_item_id)}"
                f"&fileIdentifier={quote(file_identifier)}"
            ),
        ]
        return self._first_success(candidates=candidates, label="GetWorkitemFile")

    def download_inline_files(
        self,
        *,
        files: list[dict],
        output_dir: Path,
        max_files: int | None = 12,
        max_download_bytes: int | None = MAX_INLINE_DOWNLOAD_BYTES,
    ) -> dict:
        target_dir = output_dir / "yunxiao_inline_files"
        target_dir.mkdir(parents=True, exist_ok=True)
        attempts: list[dict] = []
        items: list[dict] = []
        seen: set[str] = set()
        selected_files = files if max_files is None else files[:max_files]
        for file_ref in selected_files:
            identifier = str(file_ref.get("identifier") or "").strip()
            url = str(file_ref.get("url") or "").strip()
            if not identifier or not url or identifier in seen:
                continue
            seen.add(identifier)
            result = self._download_file(
                url=url,
                identifier=identifier,
                target_dir=target_dir,
                max_download_bytes=max_download_bytes,
            )
            attempts.append(result["attempt"])
            items.append(
                {
                    "identifier": identifier,
                    "name": file_ref.get("name") or "",
                    "kind": file_ref.get("kind") or "",
                    "status": "success" if result.get("ok") else "failed",
                    "path": result.get("path") or "",
                    "size": result.get("size"),
                    "sha256": result.get("sha256") or "",
                    "content_type": result.get("content_type") or "",
                    "error": result.get("error") or "",
                }
            )
        return {"items": items, "attempts": attempts}

    def _first_success(self, *, candidates: list[str], label: str) -> dict:
        attempts: list[dict] = []
        for url in candidates:
            result = self._get_json(url=url, label=label)
            attempts.append(result["attempt"])
            if result["ok"]:
                return {"ok": True, "data": result["data"], "attempts": attempts}
        return {
            "ok": False,
            "data": {},
            "attempts": attempts,
            "error": f"{label} 读取失败：" + summarize_attempt_errors(attempts),
        }

    def _get_json(self, *, url: str, label: str) -> dict:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "x-yunxiao-token": self.credentials.pat,
                "Authorization": f"Bearer {self.credentials.pat}",
            },
            method="GET",
        )
        attempt = {"label": label, "url": redact_query(url), "status": "failed", "http_status": None, "error": ""}
        try:
            with urllib.request.urlopen(request, timeout=YUNXIAO_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8", errors="replace")
                attempt["http_status"] = response.status
                content_type = response.headers.get("content-type") or ""
                if raw.strip() and "json" not in content_type.lower():
                    attempt["error"] = f"非 JSON 响应：content-type={content_type}，body={truncate(raw, 220)}"
                    return {"ok": False, "data": {}, "attempt": attempt}
                data = json.loads(raw) if raw.strip() else {}
            attempt["status"] = "success"
            return {"ok": True, "data": data, "attempt": attempt}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            attempt["http_status"] = exc.code
            attempt["error"] = redact_secrets(truncate(detail, 600))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            attempt["error"] = redact_secrets(truncate(str(exc), 600))
        return {"ok": False, "data": {}, "attempt": attempt}

    def _download_file(
        self,
        *,
        url: str,
        identifier: str,
        target_dir: Path,
        max_download_bytes: int | None = MAX_INLINE_DOWNLOAD_BYTES,
    ) -> dict:
        target_dir.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "*/*",
                "x-yunxiao-token": self.credentials.pat,
                "Authorization": f"Bearer {self.credentials.pat}",
            },
            method="GET",
        )
        attempt = {"label": "DownloadInlineFile", "url": redact_query(url), "status": "failed", "http_status": None, "error": ""}
        try:
            with urllib.request.urlopen(request, timeout=YUNXIAO_TIMEOUT_SECONDS) as response:
                content_type = response.headers.get("content-type") or "application/octet-stream"
                raw = response.read(max_download_bytes + 1) if max_download_bytes is not None else response.read()
                attempt["http_status"] = response.status
                if max_download_bytes is not None and len(raw) > max_download_bytes:
                    attempt["error"] = f"内联文件超过下载上限 {max_download_bytes} bytes"
                    return {"ok": False, "attempt": attempt, "error": attempt["error"]}
            extension = extension_for_content_type(content_type)
            filename = f"{safe_filename(identifier)}{extension}"
            path = target_dir / filename
            path.write_bytes(raw)
            attempt["status"] = "success"
            return {
                "ok": True,
                "attempt": attempt,
                "path": str(path),
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content_type": content_type,
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            attempt["http_status"] = exc.code
            attempt["error"] = redact_secrets(truncate(detail, 600))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            attempt["error"] = redact_secrets(truncate(str(exc), 600))
        return {"ok": False, "attempt": attempt, "error": attempt["error"]}


def load_yunxiao_credentials() -> YunxiaoCredentialBundle:
    pat, pat_source = first_configured_value(["aliyun_devops_pat", "ALIYUN_DEVOPS_PAT"])
    organization_id, organization_source = first_configured_value(["aliyun_devops_organization_id", "ALIYUN_DEVOPS_ORGANIZATION_ID"])
    project_id, project_source = first_configured_value(["aliyun_devops_project_id", "ALIYUN_DEVOPS_PROJECT_ID"])
    missing: list[str] = []
    if not pat:
        missing.append("aliyun_devops_pat")
    if not organization_id:
        missing.append("aliyun_devops_organization_id")
    return YunxiaoCredentialBundle(
        pat=pat,
        organization_id=organization_id,
        project_id=project_id,
        pat_source=pat_source,
        organization_source=organization_source,
        project_source=project_source,
        missing_keys=missing,
    )


def yunxiao_base_url() -> str:
    return (
        os.environ.get("YUNXIAO_API_BASE_URL")
        or os.environ.get("ALIYUN_DEVOPS_BASE_URL")
        or DEFAULT_YUNXIAO_BASE_URL
    )


def first_configured_value(keys: list[str]) -> tuple[str, str]:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value, f"env:{key}"
    file_data = load_local_credentials_file()
    for key in keys:
        value = file_data.get(key)
        if isinstance(value, str) and value:
            return value, f"file:{credentials_file_path()}"
    if os.environ.get("HARNESS_YUNXIAO_DISABLE_KEYCHAIN", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "", ""
    for key in keys:
        value = read_keychain_secret(key)
        if value:
            return value, f"keychain:{key}"
    return "", ""


def read_keychain_secret(service: str) -> str:
    commands = [
        ["security", "find-generic-password", "-s", service, "-w"],
        ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", service, "-w"],
    ]
    for command in commands:
        try:
            completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    return ""


def credentials_file_path() -> Path:
    return Path(os.environ.get("HARNESS_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE).expanduser()


def load_local_credentials_file() -> dict:
    path = credentials_file_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def credentials_file_permission_issue() -> str:
    path = credentials_file_path()
    if not path.exists():
        return ""
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        return f"无法读取凭证文件权限：{exc}"
    if mode & 0o077:
        return f"凭证文件权限过宽：{oct(mode)}，建议执行 chmod 600 {path}"
    return ""


def parse_work_item_id(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"/(?:bug|req|requirement|task)/([A-Za-z]+-\d+)",
        r"\b([A-Za-z]+-\d+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).upper()
    return ""


def normalize_comment_list(data: object) -> list[dict]:
    """Normalize comment bodies for read-only requirement evidence without treating them as code proof."""
    nodes: list[object] = []

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if any(key in value for key in ("content", "comment", "body", "text")):
                nodes.append(value)
                return
            for key in ("items", "comments", "list", "data", "result"):
                if key in value:
                    visit(value[key])

    visit(data)
    result: list[dict] = []
    seen: set[str] = set()
    for item in nodes:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("comment") or item.get("body") or item.get("text") or "").strip()
        if not content:
            continue
        identifier = str(item.get("id") or item.get("commentId") or item.get("identifier") or content)
        if identifier in seen:
            continue
        seen.add(identifier)
        creator = item.get("creator") or item.get("author") or item.get("user") or ""
        if isinstance(creator, dict):
            creator = creator.get("name") or creator.get("nickName") or creator.get("userName") or ""
        result.append(
            {
                "id": str(item.get("id") or item.get("commentId") or ""),
                "author": str(creator or ""),
                "content": redact_urls_in_text(redact_secrets(content)),
                "created_at": str(item.get("createdAt") or item.get("createTime") or item.get("created_at") or ""),
            }
        )
    return result[:50]


def normalize_attachment_list(data: object, *, limit: int | None = 30) -> list[dict]:
    candidates = []
    if isinstance(data, dict):
        for key in ["attachments", "files", "data", "result"]:
            value = data.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if not candidates and isinstance(data.get("items"), list):
            candidates = data["items"]
    elif isinstance(data, list):
        candidates = data
    normalized: list[dict] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "name": str(item.get("name") or item.get("fileName") or item.get("filename") or ""),
                "identifier": str(item.get("identifier") or item.get("fileIdentifier") or item.get("fileId") or item.get("id") or ""),
                "size": item.get("size") or item.get("fileSize"),
                "content_type": item.get("contentType") or item.get("mimeType") or item.get("type"),
            }
        )
    return normalized if limit is None else normalized[:limit]


def normalize_file_detail(data: object) -> dict:
    if not isinstance(data, dict):
        return {"raw_type": type(data).__name__}
    result: dict = {}
    for key in ["name", "fileName", "filename", "identifier", "fileIdentifier", "fileId", "id", "size", "fileSize", "contentType", "mimeType", "type", "url"]:
        if key in data and data[key] not in (None, ""):
            result[key] = data[key] if key != "url" else redact_query(str(data[key]))
    text_parts: list[str] = []
    collect_strings(data, text_parts)
    if text_parts:
        result["text_excerpt"] = truncate("\n".join(text_parts), 4000)
    return result


def collect_file_detail_downloads(file_details: list[dict]) -> list[dict]:
    downloads = []
    for detail in file_details:
        download = detail.get("download") if isinstance(detail, dict) else None
        if isinstance(download, dict) and download:
            downloads.append(download)
    return downloads


def extract_description_evidence(work_item: dict) -> dict:
    html_values = extract_html_values(work_item)
    combined_html = "\n".join(unique_keep_order(html_values))
    clean_text = html_to_text(combined_html)
    return {
        "html_value_count": len(html_values),
        "html_excerpt": truncate(combined_html, 8000) if combined_html else "",
        "clean_text": truncate(clean_text, MAX_EVIDENCE_TEXT_CHARS) if clean_text else "",
    }


def extract_html_values(value: object) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "htmlValue" and isinstance(item, str) and item.strip():
                values.append(item.strip())
            else:
                values.extend(extract_html_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(extract_html_values(item))
    elif isinstance(value, str) and "<" in value and ">" in value and "htmlValue" in value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if decoded is not None:
            values.extend(extract_html_values(decoded))
        else:
            malformed_html = _extract_malformed_html_value(value)
            if malformed_html:
                values.append(malformed_html)
    return values


def _extract_malformed_html_value(value: str) -> str:
    marker = ',"jsonMLValue"'
    html_marker = '"htmlValue"'
    marker_start = value.find(html_marker)
    end = value.find(marker, marker_start)
    if marker_start < 0 or end < 0:
        return ""
    colon = value.find(":", marker_start + len(html_marker))
    opening_quote = value.find('"', colon + 1)
    if colon < 0 or opening_quote < 0 or opening_quote >= end:
        return ""
    body = value[opening_quote + 1:end]
    if body.endswith('"'):
        body = body[:-1]
    return body.replace(r'\"', '"').replace(r"\n", "\n")


def html_to_text(html_text: str) -> str:
    if not html_text:
        return ""
    parser = TextExtractingHTMLParser()
    parser.feed(html_text)
    return normalize_lines(parser.text())


class TextExtractingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "div", "br", "li", "tr", "table", "article"}:
            self.parts.append("\n")
        if tag == "img":
            src = dict(attrs).get("src") or ""
            identifier = extract_file_identifier(src)
            self.parts.append(f"\n[内联图片：{identifier or '未识别 fileIdentifier'}]\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return html.unescape("".join(self.parts))


def collect_inline_files_from_work_item(
    work_item: dict,
    attachments: list[dict],
    description: dict | None = None,
    *,
    limit: int | None = 40,
) -> list[dict]:
    files = []
    seen: set[str] = set()
    for attachment in attachments:
        identifier = str(attachment.get("identifier") or "").strip()
        key = identifier or json.dumps(attachment, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        item = dict(attachment)
        item.setdefault("kind", "attachment")
        files.append(item)

    text = json.dumps(work_item, ensure_ascii=False)
    html_text = str((description or {}).get("html_excerpt") or "")
    for ref in extract_inline_file_refs(text + "\n" + html_text):
        identifier = ref.get("identifier") or ref.get("url") or ""
        if identifier in seen:
            continue
        seen.add(identifier)
        files.append(ref)
    return files if limit is None else files[:limit]


def extract_inline_file_refs(text: str) -> list[dict]:
    refs: list[dict] = []
    seen: set[str] = set()

    def add_ref(ref: dict) -> None:
        identifier = str(ref.get("identifier") or "").strip()
        source_node_id = str(ref.get("source_node_id") or "").strip()
        url = str(ref.get("url") or "").strip()
        key = identifier or (f"node:{source_node_id}" if source_node_id else url)
        if not key:
            return
        if not identifier and not source_node_id and url:
            prior = next((item for item in refs if item.get("url") == url), None)
            if prior is not None:
                for field, value in ref.items():
                    if value not in (None, "") and prior.get(field) in (None, ""):
                        prior[field] = value
                return
        if key in seen:
            prior = next(item for item in refs if item.get("_dedupe_key") == key)
            for field, value in ref.items():
                if value not in (None, "") and prior.get(field) in (None, ""):
                    prior[field] = value
            return
        ref["_dedupe_key"] = key
        seen.add(key)
        refs.append(ref)

    for ref in _extract_jsonml_inline_images(text):
        add_ref(ref)
    for src in extract_image_sources(text):
        if any(item.get("url") == src for item in refs):
            continue
        identifier = extract_file_identifier(src)
        add_ref(
            {
                "name": "",
                "identifier": identifier,
                "url": src,
                "size": None,
                "content_type": None,
                "kind": "inline_image",
                "source_node_id": "",
            }
        )
    for url in re.findall(r"https?://[^\s)\\\"'<>]+", text):
        if not any(keyword in url.lower() for keyword in ["attachment", "file", "download", "workitem/file"]):
            continue
        identifier = extract_file_identifier(url)
        add_ref(
            {
                "name": "",
                "identifier": identifier,
                "url": html.unescape(url),
                "size": None,
                "content_type": None,
                "kind": "inline_file",
                "source_node_id": "",
            }
        )
    for identifier in re.findall(r"fileIdentifier[\"'=:%20]+([A-Za-z0-9_-]+)", text):
        add_ref(
            {
                "name": "",
                "identifier": identifier,
                "url": f"https://devops.aliyun.com/projex/api/workitem/file/url?fileIdentifier={quote(identifier)}",
                "size": None,
                "content_type": None,
                "kind": "inline_file_identifier",
                "source_node_id": "",
            }
        )
    normalized: list[dict] = []
    for ref in refs:
        ref = {key: value for key, value in ref.items() if key != "_dedupe_key"}
        if ref.get("url"):
            ref["url"] = html.unescape(str(ref["url"]))
        normalized.append(ref)
    return normalized


def _extract_jsonml_inline_images(text: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        marker = '"jsonMLValue"'
        marker_start = text.find(marker)
        start = text.find("[", marker_start)
        if marker_start < 0 or start < 0:
            return []
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            return []

    images: list[dict] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
            return
        if not isinstance(value, list):
            return
        if len(value) >= 2 and value[0] == "img" and isinstance(value[1], dict):
            attrs = value[1]
            src = html.unescape(str(attrs.get("src") or "").strip())
            explicit_identifier = str(
                attrs.get("fileIdentifier")
                or attrs.get("fileidentifier")
                or attrs.get("fileId")
                or attrs.get("file_id")
                or ""
            ).strip()
            images.append(
                {
                    "name": str(attrs.get("name") or attrs.get("fileName") or "").strip(),
                    "identifier": explicit_identifier or extract_file_identifier(src),
                    "url": src,
                    "size": attrs.get("size"),
                    "content_type": attrs.get("contentType") or attrs.get("mimeType"),
                    "kind": "inline_image",
                    "source_node_id": str(attrs.get("id") or "").strip(),
                }
            )
        for child in value:
            visit(child)

    visit(payload)
    return images


def extract_image_sources(text: str) -> list[str]:
    sources = []
    for match in re.finditer(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", text, re.IGNORECASE):
        sources.append(html.unescape(match.group(1)))
    return sources


def extract_file_identifier(url: str) -> str:
    if not url:
        return ""
    cleaned_url = html.unescape(url).rstrip("\\")
    parsed = urllib.parse.urlsplit(cleaned_url)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ["fileIdentifier", "fileidentifier", "fileId", "id"]:
        value = query.get(key)
        if value:
            return value[0]
    match = re.search(r"fileIdentifier[=/]([A-Za-z0-9_-]+)", cleaned_url)
    return match.group(1) if match else ""


def build_text_excerpt(evidence: dict) -> str:
    parts = []
    if evidence.get("clean_text"):
        parts.append(str(evidence.get("clean_text")))
    work_item = evidence.get("work_item") or {}
    if not parts:
        collect_strings(work_item, parts)
    for comment in evidence.get("comments", []):
        if isinstance(comment, dict) and comment.get("content"):
            parts.append(str(comment["content"]))
    for attachment in evidence.get("attachments", []):
        collect_strings(attachment, parts)
    for detail in evidence.get("file_details", []):
        collect_strings(detail, parts)
    for parent in evidence.get("parent_work_items", []):
        if not isinstance(parent, dict):
            continue
        collect_strings(parent.get("description"), parts)
        for comment in parent.get("comments", []):
            if isinstance(comment, dict) and comment.get("content"):
                parts.append(str(comment["content"]))
    text = "\n".join(part for part in parts if part)
    return truncate(text, MAX_EVIDENCE_TEXT_CHARS)


def collect_strings(value: object, output: list[str]) -> None:
    if len("\n".join(output)) > MAX_EVIDENCE_TEXT_CHARS:
        return
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            output.append(redact_urls_in_text(redact_secrets(stripped)))
    elif isinstance(value, dict):
        for item in value.values():
            collect_strings(item, output)
    elif isinstance(value, list):
        for item in value:
            collect_strings(item, output)


def build_yunxiao_prompt_context(evidence: dict | None) -> str:
    if not evidence:
        return "未启用云效只读证据读取。"
    lines = [
        f"- 云效读取状态：{evidence.get('status')}",
        f"- 工作项：{evidence.get('work_item_id') or '-'}",
        f"- 错误：{evidence.get('error') or '-'}",
        f"- 附件数：{len(evidence.get('attachments') or [])}",
        f"- 内联图片/文件数：{len(evidence.get('inline_files') or [])}",
        f"- 父需求数：{len(evidence.get('parent_work_items') or [])}",
        "- 读取策略：只读，不写评论、不上传附件、不流转状态、不改负责人。",
    ]
    excerpt = str(evidence.get("clean_text") or evidence.get("text_excerpt") or "").strip()
    if excerpt:
        lines.extend(["", "### 云效清洗文本", truncate(excerpt, 4000)])
    inline_files = evidence.get("inline_files") or []
    if inline_files:
        lines.extend(["", "### 云效内联图片/文件证据"])
        for item in inline_files[:12]:
            lines.append(
                f"- {item.get('kind') or 'file'}: fileIdentifier={item.get('identifier') or '-'} "
                f"name={item.get('name') or '-'}"
            )
    parent_items = evidence.get("parent_work_items") or []
    if parent_items:
        lines.extend(["", "### 父需求链证据"])
        for item in parent_items[:5]:
            if isinstance(item, dict):
                lines.append(
                    f"- 第 {item.get('depth') or '-'} 层：{item.get('serial_number') or item.get('work_item_id') or '-'} "
                    f"{item.get('title') or '-'}；附件 {len(item.get('attachments') or [])} 个"
                )
    return "\n".join(lines)


def summarize_attempt_errors(attempts: list[dict]) -> str:
    parts = []
    for item in attempts[-3:]:
        status = item.get("http_status") or "-"
        error = item.get("error") or "unknown"
        parts.append(f"{item.get('label')} HTTP {status}: {error}")
    return "；".join(parts)


def quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def redact_query(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if not parsed.query:
        return url
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "<query>", parsed.fragment))


def redact_urls_in_text(text: str) -> str:
    return re.sub(r"https?://[^\s)\\\"'<>]+", lambda match: redact_query(match.group(0)), text)


def normalize_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = re.sub(r"\s+", " ", line).strip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def unique_keep_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def extension_for_content_type(content_type: str) -> str:
    content_type = content_type.split(";", 1)[0].strip().lower()
    if content_type == "image/jpeg":
        return ".jpg"
    guessed = mimetypes.guess_extension(content_type)
    return guessed or ".bin"


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "yunxiao_file"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...（已截断）"
