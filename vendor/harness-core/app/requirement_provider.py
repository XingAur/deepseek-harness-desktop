from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from app import database
from app.llm_client import redact_secrets
from app.visual_evidence_protocol import valid_visual_fact
from app.yunxiao_read import parse_work_item_id


EVIDENCE_VERSION = "0.23-requirement-evidence"
LOCAL_CHANGE_EVIDENCE_EXCEPTION_VERSION = "local-change-evidence-exception.v1"
SUPPORTED_SOURCE_TYPES = {"yunxiao", "tapd", "jira", "github_issue", "manual", "file"}
OPTIONAL_INLINE_EVIDENCE_CODES = frozenset(
    {
        "inline_image_detail_failed",
        "inline_image_download_failed",
        "inline_file_detail_failed",
        "inline_file_reference_unresolved",
        "inline_file_download_url_missing",
        "inline_file_download_failed",
        # v0.7 legacy collection emitted this name.  Keep it optional when
        # historical evidence is normalized after the granular image warning
        # codes were introduced.
        "file_download_failed",
    }
)
NORMALIZED_SCHEMA = [
    "source_type",
    "source_url",
    "external_id",
    "title",
    "description_text",
    "comments",
    "attachments",
    "images",
    "parent_work_items",
    "parent_chain",
    "visual_evidence",
    "status",
    "assignee",
    "fetched_at",
    "warnings",
]


class _PlainTextHTMLParser(HTMLParser):
    _BLOCK_TAGS = {
        "article", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "ol", "p", "section", "table", "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalize_requirement_evidence(
    *,
    source_type: str,
    payload: dict,
    source_url: str = "",
    fetched_at: str = "",
) -> dict:
    provider_type = normalize_source_type(source_type or payload.get("source_type") or payload.get("provider") or "manual")
    work_item = select_provider_work_item(payload)
    warnings = normalize_warnings(payload.get("warnings"))
    if provider_type not in SUPPORTED_SOURCE_TYPES:
        warnings.append(
            {
                "severity": "warning",
                "code": "unsupported_source_type",
                "message": f"未识别的需求来源 {provider_type}，按通用只读 payload 归一化。",
            }
        )
    status = normalize_status(provider_type=provider_type, payload=payload, work_item=work_item)
    description_text = normalize_description_text(provider_type=provider_type, payload=payload, work_item=work_item)
    title = normalize_title(provider_type=provider_type, payload=payload, work_item=work_item)
    external_id = normalize_external_id(provider_type=provider_type, payload=payload, work_item=work_item, source_url=source_url)
    if not title:
        warnings.append({"severity": "warning", "code": "title_missing", "message": "需求标题为空。"})
    if not description_text:
        warnings.append({"severity": "warning", "code": "description_missing", "message": "需求正文为空。"})
    if str(payload.get("status") or "").lower() in {"failed", "error"} or payload.get("error"):
        warnings.append(
            {
                "severity": "warning",
                "code": "source_read_failed",
                "message": sanitize_text(payload.get("error") or "来源读取失败，当前只保留已提供的本地 payload。"),
            }
        )
    attachments = normalize_attachments(payload, work_item=work_item)
    images = normalize_images(payload=payload, work_item=work_item, attachments=attachments)
    parent_work_items = [
        item for item in (payload.get("parent_work_items") or [])
        if isinstance(item, dict)
    ]
    parent_chain = payload.get("parent_chain") if isinstance(payload.get("parent_chain"), dict) else {}
    visual_evidence = normalize_visual_evidence(
        payload=payload,
        title=title,
        description_text=description_text,
        images=images,
        warnings=warnings,
    )
    evidence_quality = classify_evidence_quality(
        payload=payload,
        title=title,
        description_text=description_text,
        warnings=warnings,
        visual_evidence=visual_evidence,
    )
    return {
        "version": EVIDENCE_VERSION,
        "readonly": True,
        "external_writes_enabled": False,
        "source_type": provider_type,
        "source_url": sanitize_text(source_url or first_text(payload, ["source_url", "yunxiao_url", "url", "html_url", "link"])),
        "external_id": sanitize_text(external_id),
        "title": sanitize_text(title),
        "description_text": sanitize_text(description_text),
        "comments": normalize_comments(payload, work_item=work_item),
        "attachments": attachments,
        "images": images,
        "parent_work_items": parent_work_items,
        "parent_chain": parent_chain,
        "visual_evidence": visual_evidence,
        "status": sanitize_text(status),
        "assignee": sanitize_text(normalize_assignee(payload=payload, work_item=work_item)),
        "fetched_at": sanitize_text(fetched_at or first_text(payload, ["fetched_at", "read_at", "created_at", "updated_at"]) or database.now_iso()),
        "warnings": warnings,
        # Keep the provider's original gate for mutation decisions, but let
        # read-only discovery continue when only an expired inline image/file
        # reference failed.  This is deliberately a separate quality signal.
        "evidence_quality": evidence_quality,
        "provider": {
            "normalizer": "local-readonly",
            "schema": list(NORMALIZED_SCHEMA),
            "input_keys": sorted(str(key) for key in payload.keys()),
        },
        "boundaries": [
            "本结构只做本地证据归一化，不读取远端系统。",
            "不写云效/TAPD/Jira/GitHub，不发表评论，不流转状态。",
            "不保存或输出完整 token；输出文本会经过本地密钥脱敏。",
        ],
    }


def normalize_requirement_evidence_file(path: str | Path, *, source_type: str = "") -> dict:
    target = Path(path).expanduser()
    raw = target.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "source_type": source_type or "file",
            "source_url": str(target),
            "title": target.stem,
            "description_text": raw,
        }
    if not isinstance(data, dict):
        raise ValueError(f"需求来源文件根节点必须是对象或纯文本：{target}")
    # The provider-neutral workitem.read capability returns a standard
    # his-capability-result.v1 envelope.  Accept its data payload directly so
    # a host does not need to rewrite or manually copy Yunxiao evidence before
    # handing it to Harness Core.
    if data.get("schema_version") == "his-capability-result.v1" and isinstance(data.get("data"), dict):
        envelope = data
        data = dict(envelope["data"])
        data.setdefault("provider", envelope.get("provider"))
        data.setdefault("status", envelope.get("status"))
        data.setdefault("summary", envelope.get("summary"))
        data.setdefault("request_id", envelope.get("request_id"))
    data.setdefault("source_url", str(target))
    evidence = normalize_requirement_evidence(
        source_type=source_type
        or str(data.get("source_type") or data.get("provider") or "file"),
        payload=data,
    )
    resolve_local_evidence_paths(evidence, base_dir=target.resolve().parent)
    # Archive snapshots keep media paths relative to their Yunxiao directory.
    # Re-evaluate the visual gate after those paths have been rehydrated; the
    # initial normalizer deliberately never treats an unresolved path as
    # usable evidence.
    evidence["visual_evidence"] = normalize_visual_evidence(
        payload=evidence,
        title=str(evidence.get("title") or ""),
        description_text=str(evidence.get("description_text") or ""),
        images=evidence.get("images") if isinstance(evidence.get("images"), list) else [],
        warnings=evidence.get("warnings") if isinstance(evidence.get("warnings"), list) else [],
    )
    refresh_evidence_quality(evidence)
    return evidence


def requirement_evidence_to_markdown(evidence: dict) -> str:
    lines = [
        "# v0.23 需求来源归一化证据",
        "",
        f"- 版本：{evidence.get('version') or EVIDENCE_VERSION}",
        f"- 只读：{'是' if evidence.get('readonly') else '否'}",
        f"- 外部写入：{'关闭' if not evidence.get('external_writes_enabled') else '开启'}",
        f"- 来源：{evidence.get('source_type') or '-'}",
        f"- 来源链接：{evidence.get('source_url') or '-'}",
        f"- 外部 ID：{evidence.get('external_id') or '-'}",
        f"- 标题：{evidence.get('title') or '-'}",
        f"- 状态：{evidence.get('status') or '-'}",
        f"- 负责人：{evidence.get('assignee') or '-'}",
        f"- 获取时间：{evidence.get('fetched_at') or '-'}",
        "",
        "## 正文",
        "",
        sanitize_text(evidence.get("description_text") or "-"),
        "",
        "## 评论",
        "",
    ]
    comments = evidence.get("comments") or []
    if not comments:
        lines.append("- 无。")
    for item in comments[:30]:
        lines.append(f"- {item.get('author') or '-'}：{item.get('content') or '-'}")
    lines.extend(["", "## 附件", ""])
    attachments = evidence.get("attachments") or []
    if not attachments:
        lines.append("- 无。")
    for item in attachments[:30]:
        lines.append(f"- {item.get('name') or item.get('identifier') or '-'}：{item.get('path') or item.get('url') or '-'}")
    lines.extend(["", "## 图片", ""])
    images = evidence.get("images") or []
    if not images:
        lines.append("- 无。")
    for item in images[:30]:
        lines.append(f"- {item.get('name') or item.get('identifier') or '-'}：{item.get('path') or item.get('url') or '-'}")
    lines.extend(["", "## 父需求链", ""])
    parent_items = evidence.get("parent_work_items") or []
    if not parent_items:
        lines.append("- 无。")
    for item in parent_items[:10]:
        if isinstance(item, dict):
            lines.append(
                f"- 第 {item.get('depth') or '-'} 层："
                f"{item.get('serial_number') or item.get('work_item_id') or '-'} "
                f"{item.get('title') or '-'}；附件 {len(item.get('attachments') or [])} 个"
            )
    visual = evidence.get("visual_evidence") or {}
    if visual:
        lines.extend(["", "## 截图视觉事实", "", f"- 门禁状态：{visual.get('status') or '-'}"])
        for item in visual.get("facts") or []:
            if isinstance(item, dict):
                if str(item.get("fact_type") or "ui_trace").strip().lower() == "document":
                    lines.append(
                        f"- 文档类型：{item.get('document_type') or '-'}；"
                        f"可见文字：{item.get('visible_text') or '-'}；"
                        f"关键事实：{item.get('key_facts') or '-'}"
                    )
                else:
                    lines.append(
                        f"- 错误：{item.get('error_text') or '-'}；菜单：{item.get('menu') or '-'}；"
                        f"动作：{item.get('action') or '-'}；场景：{item.get('business_scene') or '-'}"
                    )
        for blocker in visual.get("blockers") or []:
            lines.append(f"- 阻断：{blocker}")
    lines.extend(["", "## Warning", ""])
    warnings = evidence.get("warnings") or []
    if not warnings:
        lines.append("- 无。")
    for item in warnings:
        lines.append(f"- [{item.get('severity') or 'warning'}] {item.get('code') or '-'}：{item.get('message') or '-'}")
    quality = evidence.get("evidence_quality") or {}
    if quality:
        lines.extend(
            [
                "",
                "## 证据质量与继续策略",
                "",
                f"- 只读分析：{quality.get('analysis_status') or '-'}",
                f"- 变更门禁：{quality.get('mutation_status') or '-'}",
                f"- 可忽略的可选媒体警告：{', '.join(quality.get('optional_warning_codes') or []) or '-'}",
                f"- 硬阻断警告：{', '.join(quality.get('blocking_warning_codes') or []) or '-'}",
                f"- 处理策略：{quality.get('strategy') or '-'}",
            ]
        )
    lines.extend(["", "## 边界", ""])
    for item in evidence.get("boundaries") or []:
        lines.append(f"- {item}")
    return sanitize_text("\n".join(lines))


def write_requirement_evidence_outputs(*, output_dir: str | Path, evidence: dict) -> dict:
    target_dir = Path(output_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "requirement_evidence.json"
    markdown_path = target_dir / "requirement_evidence.md"
    json_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(requirement_evidence_to_markdown(evidence), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def normalize_source_type(value: object) -> str:
    normalized = sanitize_text(value).strip().lower().replace("-", "_")
    return normalized or "manual"


def select_provider_work_item(payload: dict) -> dict:
    direct = payload.get("work_item")
    if isinstance(direct, dict):
        return direct
    items = [item for item in payload.get("work_items") or [] if isinstance(item, dict)]
    if not items:
        return {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    requested_id = sanitize_text(source.get("requested_id"))
    resolved_id = sanitize_text(source.get("resolved_work_item_id"))
    for predicate in (
        lambda item: sanitize_text(item.get("id")) == resolved_id and bool(resolved_id),
        lambda item: sanitize_text(item.get("serial_number")) == requested_id and bool(requested_id),
        lambda item: sanitize_text(item.get("role")).lower() == "requested",
    ):
        match = next((item for item in items if predicate(item)), None)
        if match is not None:
            return match
    return items[0]


def rich_text_to_plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text[:1] in {"{", "["}:
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if decoded is not None and decoded != value:
                normalized = rich_text_to_plain(decoded)
                if normalized:
                    return normalized
            # Some Yunxiao archive revisions contain a JSON-like envelope
            # whose htmlValue has literal newlines.  The outer evidence file
            # is valid JSON, but this nested string cannot be decoded again.
            # Extract only the provider-rendered body and discard schema keys.
            html_value = re.search(
                r'^\{\s*"htmlValue"\s*:\s*"(?P<body>.*?)"\s*,\s*"jsonMLValue"\s*:',
                text,
                flags=re.DOTALL,
            )
            if html_value:
                return rich_text_to_plain(
                    html_value.group("body").replace(r'\"', '"').replace(r"\n", "\n")
                )
        if re.search(r"<[/!]?[A-Za-z][^>]*>", text):
            parser = _PlainTextHTMLParser()
            try:
                parser.feed(text)
                parser.close()
                return normalize_plain_text("".join(parser.parts))
            except (TypeError, ValueError):
                return normalize_plain_text(re.sub(r"<[^>]+>", "\n", unescape(text)))
        return normalize_plain_text(text)
    if isinstance(value, dict):
        for key in (
            "clean_text", "plain_text", "plainText", "htmlValue", "html",
            "text", "description", "content", "body", "raw", "jsonMLValue",
        ):
            if key not in value:
                continue
            normalized = rich_text_to_plain(value.get(key))
            if normalized:
                return normalized
        return ""
    if isinstance(value, list):
        children = value[1:]
        if value and isinstance(value[0], str):
            children = value[2:] if len(value) > 1 and isinstance(value[1], dict) else value[1:]
        return normalize_plain_text("\n".join(rich_text_to_plain(item) for item in children))
    return normalize_plain_text(str(value)) if isinstance(value, (int, float, bool)) else ""


def normalize_plain_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in unescape(value).splitlines()]
    return "\n".join(line for line in lines if line)


def first_value(value: Any, keys: list[str]) -> Any:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = value.get(key)
        if candidate not in (None, "", [], {}):
            return candidate
    return None


def resolve_local_evidence_paths(evidence: dict, *, base_dir: Path) -> None:
    for collection_name in ("attachments", "images"):
        for item in evidence.get(collection_name) or []:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = (base_dir / path).resolve()
            if path.is_file():
                item["path"] = str(path)


def normalize_title(*, provider_type: str, payload: dict, work_item: dict) -> str:
    if provider_type == "yunxiao":
        return first_text(work_item, ["title", "subject", "name", "summary"]) or first_text(payload, ["title", "name", "summary"])
    if provider_type == "tapd":
        return first_text(payload, ["title", "name", "summary", "story_name", "bug_name"])
    return first_text(payload, ["title", "name", "summary", "subject"])


def normalize_description_text(*, provider_type: str, payload: dict, work_item: dict) -> str:
    if provider_type == "yunxiao":
        description = payload.get("description") if isinstance(payload.get("description"), dict) else {}
        return (
            sanitize_text(payload.get("clean_text"))
            or rich_text_to_plain(description)
            or rich_text_to_plain(work_item.get("description"))
            or rich_text_to_plain(first_value(work_item, ["details", "content", "body"]))
            or rich_text_to_plain(first_value(payload, ["description_text", "description", "content", "body"]))
        )
    return first_text(payload, ["description_text", "description", "details", "content", "body"])


def normalize_external_id(*, provider_type: str, payload: dict, work_item: dict, source_url: str = "") -> str:
    if provider_type == "yunxiao":
        return (
            sanitize_text(payload.get("work_item_id"))
            or first_text(work_item, ["serial_number", "identifier", "id", "workItemId", "workitemId"])
            or first_text(payload.get("source"), ["requested_id"])
            or parse_work_item_id(source_url or first_text(payload, ["source_url", "yunxiao_url", "url"]))
        )
    if provider_type == "tapd":
        return first_text(payload, ["external_id", "story_id", "bug_id", "task_id", "id"])
    if provider_type == "github_issue":
        return first_text(payload, ["external_id", "number", "id", "node_id"])
    return first_text(payload, ["external_id", "id", "key", "issue_id"])


def normalize_status(*, provider_type: str, payload: dict, work_item: dict) -> str:
    if provider_type == "yunxiao":
        return first_text(work_item, ["status", "statusName", "state", "stateName"]) or first_text(payload, ["work_item_status", "business_status", "state"])
    return first_text(payload, ["work_item_status", "status", "statusName", "state", "stateName"]) or first_text(
        work_item,
        ["status", "statusName", "state", "stateName"],
    )


def normalize_assignee(*, payload: dict, work_item: dict) -> str:
    return first_text(payload, ["assignee", "assignedTo", "owner", "ownerName", "responsible"]) or first_text(
        work_item,
        ["assignee", "assignedTo", "owner", "ownerName", "responsible"],
    )


def normalize_comments(payload: dict, *, work_item: dict | None = None) -> list[dict]:
    selected = work_item or {}
    raw_comments = (
        payload.get("comments")
        or payload.get("comment_list")
        or payload.get("notes")
        or selected.get("comments")
        or selected.get("comment_list")
        or selected.get("notes")
        or []
    )
    if isinstance(raw_comments, dict):
        raw_comments = [raw_comments]
    comments: list[dict] = []
    if not isinstance(raw_comments, list):
        return comments
    for item in raw_comments[:50]:
        if isinstance(item, str):
            comments.append({"author": "", "content": sanitize_text(item), "created_at": "", "source_id": ""})
        elif isinstance(item, dict):
            comments.append(
                {
                    "author": first_text(item, ["author", "creator", "user", "user_name", "name"]),
                    "content": first_text(item, ["content", "comment", "body", "text", "description"]),
                    "created_at": first_text(item, ["created_at", "create_time", "created", "time"]),
                    "source_id": first_text(item, ["id", "comment_id", "identifier"]),
                }
            )
    return [item for item in comments if item.get("content")]


def normalize_attachments(payload: dict, *, work_item: dict | None = None) -> list[dict]:
    raw_items: list[object] = []
    for source in (payload, work_item or {}):
        for key in ["attachments", "attachment_list", "files", "file_details"]:
            value = source.get(key)
            if isinstance(value, list):
                raw_items.extend(value)
    attachments: list[dict] = []
    seen: set[str] = set()
    for raw_item in raw_items[:80]:
        item = normalize_file_like_item(raw_item)
        if not item:
            continue
        identity = item.get("identifier") or item.get("path") or item.get("url") or item.get("name")
        if identity in seen:
            continue
        seen.add(identity)
        attachments.append(item)
    return attachments


def normalize_images(*, payload: dict, attachments: list[dict], work_item: dict | None = None) -> list[dict]:
    selected = work_item or {}
    raw_items: list[object] = (
        # ``images`` is the durable archive representation written after a
        # Yunxiao inline image has already been downloaded successfully.
        # Keep it before live inline references so an expired duplicate URL
        # cannot hide the locally archived source of truth.
        list(payload.get("images") or [])
        + list(selected.get("images") or [])
        + list(payload.get("inline_files") or [])
        + list(payload.get("inline_file_downloads") or [])
        + list(selected.get("inline_files") or [])
        + list(selected.get("inline_file_downloads") or [])
    )
    images: list[dict] = []
    seen: set[str] = set()
    for raw_item in [*raw_items, *attachments]:
        item = normalize_file_like_item(raw_item)
        if not item or not looks_like_image(item):
            continue
        identity = item.get("identifier") or item.get("path") or item.get("url") or item.get("name")
        if identity in seen:
            continue
        seen.add(identity)
        images.append(item)
    return images


def normalize_file_like_item(raw_item: object) -> dict:
    if isinstance(raw_item, str):
        return {"name": Path(raw_item).name, "path": sanitize_text(raw_item), "url": "", "identifier": "", "content_type": "", "status": "", "size": None, "sha256": ""}
    if not isinstance(raw_item, dict):
        return {}
    data = raw_item.get("data") if isinstance(raw_item.get("data"), dict) else {}
    download = raw_item.get("download") if isinstance(raw_item.get("download"), dict) else {}
    return {
        "name": first_text(raw_item, ["name", "file_name", "filename", "title"]) or first_text(data, ["name", "file_name", "filename"]),
        "path": first_text(raw_item, ["path", "local_path"]) or first_text(download, ["path", "local_path"]),
        "url": first_text(raw_item, ["url", "download_url", "href"]) or first_text(data, ["url", "download_url", "href"]),
        "identifier": first_text(raw_item, ["identifier", "fileIdentifier", "file_id", "id"]) or first_text(data, ["identifier", "fileIdentifier", "file_id", "id"]),
        "content_type": first_text(raw_item, ["content_type", "mime_type", "mimeType"]) or first_text(download, ["content_type", "mime_type", "mimeType"]),
        "status": first_text(raw_item, ["status"]) or first_text(download, ["status"]),
        "size": raw_item.get("size") if raw_item.get("size") is not None else download.get("size"),
        "sha256": first_text(raw_item, ["sha256"]) or first_text(download, ["sha256"]),
        "kind": first_text(raw_item, ["kind", "type"]) or first_text(data, ["kind", "type"]),
    }


def looks_like_image(item: dict) -> bool:
    content_type = str(item.get("content_type") or "").lower()
    name = str(item.get("name") or item.get("path") or item.get("url") or "").lower()
    identifier = str(item.get("identifier") or "").lower()
    return (
        content_type.startswith("image/")
        or any(name.endswith(suffix) for suffix in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"])
        or "image" in identifier
        or item.get("kind") == "image"
    )


def normalize_warnings(value: object) -> list[dict]:
    if not value:
        return []
    if isinstance(value, str):
        return [{"severity": "warning", "code": "source_warning", "message": sanitize_text(value)}]
    if isinstance(value, list):
        result: list[dict] = []
        for item in value:
            if isinstance(item, str):
                result.append({"severity": "warning", "code": "source_warning", "message": sanitize_text(item)})
            elif isinstance(item, dict):
                code = sanitize_text(item.get("code") or item.get("type") or "source_warning")
                message = sanitize_text(item.get("message") or item.get("text") or "")
                # Older snapshots preserved granular media codes as the
                # message of a generic source_warning. Recover that stable
                # meaning during local replay so an expired duplicate does
                # not become a false hard blocker.
                if code == "source_warning" and message in OPTIONAL_INLINE_EVIDENCE_CODES:
                    code = message
                result.append(
                    {
                        "severity": sanitize_text(item.get("severity") or "warning"),
                        "code": code,
                        "message": message,
                    }
                )
        return result
    return []


def classify_evidence_quality(
    *,
    payload: dict,
    title: str,
    description_text: str,
    warnings: list[dict],
    visual_evidence: dict | None = None,
) -> dict:
    """Separate read-only analysis readiness from mutation readiness.

    Yunxiao inline images are references embedded in rich text. A deleted or
    expired reference can return HTTP 400 even though the ticket title/body,
    comments and normal attachments are still readable. Such a failure is a
    warning for discovery, not evidence that the whole requirement cannot be
    analysed. The original provider gate is intentionally not rewritten.
    """

    warning_codes = [
        str(item.get("code") or "").strip()
        for item in warnings
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    ]
    optional_warning_codes = list(
        dict.fromkeys(
            code for code in warning_codes if code in OPTIONAL_INLINE_EVIDENCE_CODES
        )
    )
    blocking_warning_codes = list(
        dict.fromkeys(
            code for code in warning_codes if code not in OPTIONAL_INLINE_EVIDENCE_CODES
        )
    )
    visual = visual_evidence if isinstance(visual_evidence, dict) else {}
    if visual.get("required") is True and visual.get("status") != "analyzed":
        # A fetched local image is materially different from a missing image.
        # It must still be visually extracted before technical discovery, but a
        # stale duplicate URL must not erase the successful archived evidence.
        blocking_warning_codes.append(
            "visual_evidence_extraction_required"
            if visual.get("status") == "ready_for_extraction"
            else "visual_evidence_unavailable"
        )
    raw_gate = (
        payload.get("decision_gate")
        if isinstance(payload.get("decision_gate"), dict)
        else {}
    )
    raw_gate_state = str(raw_gate.get("state") or "").strip()
    raw_completeness = (
        payload.get("completeness")
        if isinstance(payload.get("completeness"), dict)
        else {}
    )
    raw_completeness_status = str(raw_completeness.get("status") or "").strip()
    has_requirement_body = bool(
        str(title or "").strip() and str(description_text or "").strip()
    )
    source_failed = "source_read_failed" in blocking_warning_codes or bool(
        payload.get("error")
    )
    analysis_ready = (
        has_requirement_body and not source_failed and not blocking_warning_codes
    )
    optional_media_only = analysis_ready and bool(optional_warning_codes)
    if optional_media_only:
        analysis_status = "ready_with_warnings"
        strategy = (
            "继续完整只读分析；将失效截图标记为缺失证据，不阻断项目识别、调用链和改动方案。"
        )
    elif analysis_ready:
        analysis_status = "ready"
        strategy = "继续完整只读分析。"
    else:
        analysis_status = "blocked"
        strategy = "先补齐正文或处理硬阻断证据，再继续分析。"
    mutation_ready = (
        raw_gate_state == "ready_for_analysis"
        and raw_completeness_status == "complete"
        and not optional_warning_codes
        and not blocking_warning_codes
    )
    return {
        "analysis_status": analysis_status,
        "analysis_ready": analysis_ready,
        "mutation_status": "ready" if mutation_ready else "blocked",
        "mutation_ready": mutation_ready,
        "provider_gate": raw_gate_state,
        "provider_completeness": raw_completeness_status,
        "optional_warning_codes": optional_warning_codes,
        "blocking_warning_codes": blocking_warning_codes,
        "strategy": strategy,
    }


def refresh_evidence_quality(evidence: dict) -> None:
    """Recompute quality after a local visual-evidence adapter enriches it."""
    evidence["evidence_quality"] = classify_evidence_quality(
        payload=evidence,
        title=str(evidence.get("title") or ""),
        description_text=str(evidence.get("description_text") or ""),
        warnings=evidence.get("warnings") if isinstance(evidence.get("warnings"), list) else [],
        visual_evidence=evidence.get("visual_evidence") if isinstance(evidence.get("visual_evidence"), dict) else None,
    )


def normalize_visual_evidence(
    *,
    payload: dict,
    title: str,
    description_text: str,
    images: list[dict],
    warnings: list[dict],
) -> dict:
    """Describe whether screenshots are mandatory input, without inventing facts.

    A visible high-risk error is business evidence, not decorative media.  The
    actual OCR/vision worker fills ``facts`` later; until then project discovery
    is deliberately blocked.
    """
    text = "\n".join((title, description_text))
    warning_codes = {
        str(item.get("code") or "").strip()
        for item in warnings if isinstance(item, dict)
    }
    high_risk = any(term in text for term in ("医保", "结算", "退费", "收费", "预结算", "外部调用"))
    error_or_screenshot = any(term in text for term in ("报错", "失败", "错误", "提示", "不能", "异常", "无法", "截图"))
    image_reference = bool(images) or any(code.startswith("inline_image_") for code in warning_codes)
    required = high_risk and error_or_screenshot and image_reference
    supplied = payload.get("visual_evidence") if isinstance(payload.get("visual_evidence"), dict) else {}
    facts = supplied.get("facts") if isinstance(supplied.get("facts"), list) else []
    valid_facts = [item for item in facts if valid_visual_fact(item)]
    if not required:
        return {"required": False, "status": "not_required", "can_begin_analysis": True, "facts": valid_facts, "blockers": []}
    if len(valid_facts) == len(facts) and valid_facts:
        return {"required": True, "status": "analyzed", "can_begin_analysis": True, "facts": valid_facts, "blockers": []}
    available_image_paths = [
        str(Path(str(item.get("path") or "")).resolve())
        for item in images
        if str(item.get("status") or "").lower() in {"", "success", "reused"}
        and str(item.get("path") or "").strip()
        and Path(str(item.get("path") or "")).is_file()
    ]
    if available_image_paths:
        return {
            "required": True,
            "status": "ready_for_extraction",
            "can_begin_analysis": False,
            "facts": valid_facts,
            "available_image_paths": list(dict.fromkeys(available_image_paths)),
            "blockers": [
                "高风险截图已成功归档，必须先由视觉适配器提取错误文本、菜单、操作动作和业务场景；不得开始项目定位、调用链分析或改码。"
            ],
        }
    blocker = "高风险错误截图缺失或无法读取；不得开始项目定位、调用链分析或改码。"
    return {"required": True, "status": "required", "can_begin_analysis": False, "facts": valid_facts, "available_image_paths": [], "blockers": [blocker]}


def build_local_change_evidence_exception(
    *,
    normalized_evidence: dict,
    user_confirmation: str,
    confirmed_at: str = "",
) -> dict:
    """Freeze a user-confirmed, local-only exception for stale inline media.

    This does not mutate or reinterpret provider evidence.  It may be created
    only after a direct user confirmation has already been captured by the
    interaction ledger.  The returned record is intentionally separate from
    the provider payload and grants neither external writes nor database
    writes.
    """
    if not isinstance(normalized_evidence, dict):
        raise ValueError("normalized_evidence_invalid")
    confirmation = sanitize_text(user_confirmation).strip()
    if not confirmation:
        raise ValueError("user_confirmation_required")

    quality = normalized_evidence.get("evidence_quality")
    if not isinstance(quality, dict):
        quality = classify_evidence_quality(
            payload=normalized_evidence,
            title=sanitize_text(normalized_evidence.get("title")),
            description_text=sanitize_text(normalized_evidence.get("description_text")),
            warnings=normalize_warnings(normalized_evidence.get("warnings")),
        )
    optional_codes = [
        str(code).strip()
        for code in quality.get("optional_warning_codes") or []
        if str(code).strip()
    ]
    blocking_codes = [
        str(code).strip()
        for code in quality.get("blocking_warning_codes") or []
        if str(code).strip()
    ]
    if not quality.get("analysis_ready") or not optional_codes or blocking_codes:
        raise ValueError("local_change_exception_requires_optional_inline_evidence_only")

    provider_evidence_sha256 = _provider_evidence_sha256(normalized_evidence)
    confirmation_sha256 = hashlib.sha256(confirmation.encode("utf-8")).hexdigest()
    return {
        "schema_version": LOCAL_CHANGE_EVIDENCE_EXCEPTION_VERSION,
        "status": "approved",
        "scope": "local_implementation_only",
        "external_writes_authorized": False,
        "database_writes_authorized": False,
        "provider_evidence_sha256": provider_evidence_sha256,
        "provider_gate": sanitize_text(quality.get("provider_gate")),
        "provider_completeness": sanitize_text(quality.get("provider_completeness")),
        "excepted_warning_codes": list(dict.fromkeys(optional_codes)),
        "user_confirmation_sha256": f"sha256:{confirmation_sha256}",
        "confirmed_at": sanitize_text(confirmed_at) or database.now_iso(),
        "boundaries": [
            "保留 provider 原始门禁和完整性状态，不将 partial 改写为 complete。",
            "仅豁免已确认的失效内联媒体，不豁免正文、评论或普通附件读取失败。",
            "不授权云效、远程 Git、数据库写入、部署或发布。",
        ],
    }


def local_change_evidence_exception_is_valid(
    *,
    normalized_evidence: dict,
    exception: object,
) -> bool:
    """Return true only when an exception still binds this exact evidence."""
    if not isinstance(normalized_evidence, dict) or not isinstance(exception, dict):
        return False
    if exception.get("schema_version") != LOCAL_CHANGE_EVIDENCE_EXCEPTION_VERSION:
        return False
    if exception.get("status") != "approved" or exception.get("scope") != "local_implementation_only":
        return False
    if exception.get("external_writes_authorized") is not False or exception.get("database_writes_authorized") is not False:
        return False
    if exception.get("provider_evidence_sha256") != _provider_evidence_sha256(normalized_evidence):
        return False
    quality = normalized_evidence.get("evidence_quality")
    if not isinstance(quality, dict) or not quality.get("analysis_ready"):
        return False
    optional_codes = [str(code).strip() for code in quality.get("optional_warning_codes") or [] if str(code).strip()]
    blocking_codes = [str(code).strip() for code in quality.get("blocking_warning_codes") or [] if str(code).strip()]
    excepted_codes = [str(code).strip() for code in exception.get("excepted_warning_codes") or [] if str(code).strip()]
    return bool(optional_codes) and not blocking_codes and list(dict.fromkeys(optional_codes)) == list(dict.fromkeys(excepted_codes))


def _provider_evidence_sha256(evidence: dict) -> str:
    frozen = {
        key: evidence.get(key)
        for key in (
            "source_type",
            "source_url",
            "external_id",
            "title",
            "description_text",
            "comments",
            "attachments",
            "images",
            "parent_work_items",
            "parent_chain",
            "warnings",
            "evidence_quality",
        )
    }
    encoded = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def first_text(value: Any, keys: list[str]) -> str:
    if isinstance(value, dict):
        for key in keys:
            text = stringify_text(value.get(key))
            if text:
                return text
        for nested in value.values():
            text = first_text(nested, keys)
            if text:
                return text
    if isinstance(value, list):
        for item in value[:30]:
            text = first_text(item, keys)
            if text:
                return text
    return ""


def stringify_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return sanitize_text(value.strip())
    if isinstance(value, (int, float, bool)):
        return sanitize_text(str(value))
    if isinstance(value, dict):
        for key in ["displayName", "display_name", "name", "label", "value", "title", "text"]:
            text = stringify_text(value.get(key))
            if text:
                return text
    return ""


def sanitize_text(value: object) -> str:
    if value is None:
        return ""
    return redact_secrets(str(value))
