from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.requirement_provider import (
    normalize_requirement_evidence,
    requirement_evidence_to_markdown,
)
from app.yunxiao_read import collect_yunxiao_evidence


ARCHIVE_SCHEMA_VERSION = "yunxiao-requirement-archive.v1"
DEFAULT_MAX_ARCHIVE_FILE_BYTES = 100 * 1024 * 1024
_TICKET_ID_PATTERN = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,31}-\d{1,20})\b")


def archive_yunxiao_requirement(
    *,
    archive_root: str | Path,
    evidence: Mapping[str, object],
    media_staging_root: str | Path,
    requirement_understanding: str = "",
    solution_plan: str = "",
    change_note: str = "",
    max_file_bytes: int | None = DEFAULT_MAX_ARCHIVE_FILE_BYTES,
) -> dict:
    """Persist one read-only Yunxiao evidence snapshot in its stable ticket folder.

    ``media_staging_root`` is deliberately mandatory.  Download metadata can
    contain arbitrary local paths, so only regular files below this root are
    eligible for archive copying.
    """
    root = _absolute_archive_root(archive_root)
    staging_root = Path(media_staging_root).expanduser().resolve()
    if not staging_root.is_dir():
        raise ValueError(f"云效介质临时目录不存在：{staging_root}")
    if max_file_bytes is not None and max_file_bytes < 0:
        raise ValueError("max_file_bytes 必须是非负整数或 None")

    ticket_id = _resolve_ticket_id(evidence)
    ticket_dir = root / ticket_id
    yunxiao_dir = ticket_dir / "yunxiao"
    attachment_dir = yunxiao_dir / "attachments"
    inline_dir = yunxiao_dir / "inline-assets"
    for directory in (attachment_dir, inline_dir, ticket_dir / "evidence", ticket_dir / "runs"):
        directory.mkdir(parents=True, exist_ok=True)

    previous_manifest = _read_existing_manifest(manifest_path=yunxiao_dir / "manifest.json")
    items, media_paths = _archive_media(
        evidence=evidence,
        staging_root=staging_root,
        attachment_dir=attachment_dir,
        inline_dir=inline_dir,
        previous_items=previous_manifest.get("items") or [],
        max_file_bytes=max_file_bytes,
    )
    snapshot = normalize_requirement_evidence(
        source_type="yunxiao",
        payload=dict(evidence),
        source_url=str(evidence.get("yunxiao_url") or ""),
    )
    _replace_snapshot_media_paths(snapshot, media_paths, media_items=items)

    now = _now_iso()
    media_complete = all(item["status"] in {"success", "reused"} for item in items)
    evidence_complete = str(evidence.get("status") or "") == "success"
    manifest = {
        "schema": ARCHIVE_SCHEMA_VERSION,
        "ticket_id": ticket_id,
        "synced_at": now,
        "mode": "readonly",
        "remote_writes": False,
        "max_file_bytes": max_file_bytes,
        "status": "complete" if media_complete and evidence_complete else "partial",
        "evidence_status": str(evidence.get("status") or "unknown"),
        "items": items,
    }
    snapshot_path = yunxiao_dir / "snapshot.json"
    source_path = yunxiao_dir / "source.md"
    manifest_path = yunxiao_dir / "manifest.json"
    _atomic_write_json(snapshot_path, snapshot)
    _atomic_write_text(source_path, requirement_evidence_to_markdown(snapshot) + "\n")
    _atomic_write_json(manifest_path, manifest)
    _ensure_readme(ticket_dir=ticket_dir, ticket_id=ticket_id)
    requirement_path = ticket_dir / "requirement.md"
    _update_requirement_document(
        path=requirement_path,
        ticket_id=ticket_id,
        snapshot=snapshot,
        manifest=manifest,
        requirement_understanding=requirement_understanding,
        solution_plan=solution_plan,
        change_note=change_note,
        synced_at=now,
    )
    return {
        "ticket_id": ticket_id,
        "ticket_dir": str(ticket_dir),
        "requirement_path": str(requirement_path),
        "snapshot_path": str(snapshot_path),
        "manifest_path": str(manifest_path),
        "status": manifest["status"],
        "items": items,
    }


def sync_yunxiao_requirement_archive(
    *,
    archive_root: str | Path,
    yunxiao_url: str,
    demand_text: str,
    include_comments: bool = True,
    requirement_understanding: str = "",
    solution_plan: str = "",
    change_note: str = "",
    max_file_bytes: int | None = DEFAULT_MAX_ARCHIVE_FILE_BYTES,
) -> dict:
    """Read Yunxiao once, then archive that exact read-only evidence snapshot."""
    root = _absolute_archive_root(archive_root)
    staging_parent = root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    from app.yunxiao_read import collect_yunxiao_evidence

    with tempfile.TemporaryDirectory(prefix="yunxiao-sync-", dir=staging_parent) as temp_dir:
        evidence = collect_yunxiao_evidence(
            yunxiao_url=yunxiao_url,
            demand_text=demand_text,
            output_dir=temp_dir,
            include_comments=include_comments,
            download_policy="archive",
            max_download_bytes=max_file_bytes,
        )
        return archive_yunxiao_requirement(
            archive_root=root,
            evidence=evidence,
            media_staging_root=temp_dir,
            requirement_understanding=requirement_understanding,
            solution_plan=solution_plan,
            change_note=change_note,
            max_file_bytes=max_file_bytes,
        )


def prepare_yunxiao_harness_package(
    *,
    archive_root: str | Path,
    yunxiao_url: str,
    demand_text: str = "",
    include_comments: bool = True,
    evidence_staging_root: str | Path | None = None,
    max_file_bytes: int | None = DEFAULT_MAX_ARCHIVE_FILE_BYTES,
) -> dict:
    """Fetch one read-only work item and return its selectable Harness package.

    This is the desktop first-use boundary: source evidence is archived before
    any model or code execution is considered.  The generated package may
    contain pending analysis documents until a Harness run produces them; that
    state is explicit and never treated as confirmed business fact.
    """
    root = _absolute_archive_root(archive_root)
    staging_parent = root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    if evidence_staging_root is not None:
        staging_root = Path(evidence_staging_root).expanduser().resolve()
        staging_root.mkdir(parents=True, exist_ok=True)
        evidence = collect_yunxiao_evidence(
            yunxiao_url=yunxiao_url,
            demand_text=demand_text,
            output_dir=staging_root,
            include_comments=include_comments,
            download_policy="archive",
            max_download_bytes=max_file_bytes,
        )
        archived = archive_yunxiao_requirement(
            archive_root=root,
            evidence=evidence,
            media_staging_root=staging_root,
            max_file_bytes=max_file_bytes,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="yunxiao-intake-", dir=staging_parent) as temp_dir:
            evidence = collect_yunxiao_evidence(
                yunxiao_url=yunxiao_url,
                demand_text=demand_text,
                output_dir=temp_dir,
                include_comments=include_comments,
                download_policy="archive",
                max_download_bytes=max_file_bytes,
            )
            archived = archive_yunxiao_requirement(
                archive_root=root,
                evidence=evidence,
                media_staging_root=temp_dir,
                max_file_bytes=max_file_bytes,
            )

    from app.requirement_package import export_requirement_package

    package = export_requirement_package(
        ticket_dir=archived["ticket_dir"],
        run_id=0,
    )
    return {
        **archived,
        "package_dir": package["package_dir"],
        "package_manifest_path": package["manifest_path"],
        "package_status": package["status"],
        "pending_count": package["pending_count"],
    }


def update_requirement_archive_notes(
    *,
    ticket_dir: str | Path,
    requirement_understanding: str = "",
    solution_plan: str = "",
    change_note: str = "",
) -> str:
    """Update only the managed notes of an existing ticket archive."""
    target = Path(ticket_dir).expanduser().resolve()
    snapshot_path = target / "yunxiao" / "snapshot.json"
    manifest_path = target / "yunxiao" / "manifest.json"
    if not snapshot_path.is_file() or not manifest_path.is_file():
        raise ValueError("云效需求档案缺少 snapshot.json 或 manifest.json，不能更新 requirement.md")
    snapshot = _read_json_object(snapshot_path)
    manifest = _read_json_object(manifest_path)
    ticket_id = _resolve_ticket_id({"work_item_id": target.name})
    requirement_path = target / "requirement.md"
    _update_requirement_document(
        path=requirement_path,
        ticket_id=ticket_id,
        snapshot=snapshot,
        manifest=manifest,
        requirement_understanding=requirement_understanding,
        solution_plan=solution_plan,
        change_note=change_note,
        synced_at=_now_iso(),
    )
    return str(requirement_path)


def record_requirement_archive_run(
    *,
    ticket_dir: str | Path,
    run_id: int,
    status: str,
    evaluation_status: str,
    markdown_report: str,
    requirement_understanding: str = "",
    solution_plan: str = "",
) -> dict:
    """Keep a local Harness run beside the durable requirement, never in Yunxiao."""
    target = Path(ticket_dir).expanduser().resolve()
    plan = solution_plan.strip() or (
        f"- 本次 Harness 分析结果：`{status}` / `{evaluation_status}`。\n"
        f"- 详细方案、风险与验证记录见 [runs/harness-run-{run_id}.md](runs/harness-run-{run_id}.md)。"
    )
    requirement_path = update_requirement_archive_notes(
        ticket_dir=target,
        requirement_understanding=requirement_understanding,
        solution_plan=plan,
        change_note=f"Harness run {run_id} 已归档（{status}/{evaluation_status}）。",
    )
    report_path = target / "runs" / f"harness-run-{run_id}.md"
    _atomic_write_text(report_path, markdown_report.rstrip() + "\n")
    from app.requirement_package import export_requirement_package

    package = export_requirement_package(ticket_dir=target, run_id=run_id)
    return {
        "report_path": str(report_path),
        "requirement_path": requirement_path,
        "package_dir": package["package_dir"],
        "package_manifest_path": package["manifest_path"],
        "package_status": package["status"],
    }


def _absolute_archive_root(value: str | Path) -> Path:
    root = Path(value).expanduser()
    if not root.is_absolute():
        raise ValueError("云效需求档案根目录必须是绝对路径")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _resolve_ticket_id(evidence: Mapping[str, object]) -> str:
    candidates = [
        evidence.get("work_item_id"),
        evidence.get("external_id"),
        evidence.get("yunxiao_url"),
    ]
    work_item = evidence.get("work_item")
    if isinstance(work_item, Mapping):
        candidates.extend([work_item.get("serial_number"), work_item.get("identifier"), work_item.get("id")])
    for candidate in candidates:
        match = _TICKET_ID_PATTERN.search(str(candidate or ""))
        if match:
            return match.group(1).upper()
    raise ValueError("无法从云效证据解析工作项编号，不能创建不稳定档案目录")


def _archive_media(
    *,
    evidence: Mapping[str, object],
    staging_root: Path,
    attachment_dir: Path,
    inline_dir: Path,
    previous_items: list[object],
    max_file_bytes: int | None,
) -> tuple[list[dict], dict[str, str]]:
    downloads = _collect_download_records(evidence)
    items: list[dict] = []
    stored_paths: dict[str, str] = {}
    previous_media = _index_previous_media(previous_items, media_root=inline_dir.parent)
    for record in downloads:
        identifier = str(
            record.get("identifier")
            or record.get("fileIdentifier")
            or record.get("fileId")
            or record.get("file_id")
            or ""
        ).strip()
        kind = str(record.get("kind") or "inline_file")
        original_name = str(
            record.get("name")
            or record.get("fileName")
            or record.get("filename")
            or ""
        )
        source_path_text = str(record.get("path") or "")
        item = {
            "identifier": identifier,
            "kind": kind,
            "original_name": original_name,
            "source_work_item_id": str(record.get("source_work_item_id") or ""),
            "source_work_item_serial_number": str(record.get("source_work_item_serial_number") or ""),
            "source_work_item_role": str(record.get("source_work_item_role") or ""),
            "source_status": str(record.get("status") or ""),
            "status": "pending",
            "stored_path": "",
            "size": None,
            "sha256": "",
            "content_type": str(record.get("content_type") or ""),
            "error": str(record.get("error") or ""),
        }
        if not source_path_text:
            historical = _find_previous_media(
                previous_media,
                kind=kind,
                original_name=original_name,
                size=record.get("size"),
            )
            if historical:
                item.update(
                    {
                        "status": "reused",
                        "stored_path": historical["stored_path"],
                        "size": historical["size"],
                        "sha256": historical["sha256"],
                        "content_type": item["content_type"] or historical["content_type"],
                        "reconciliation": {
                            "type": "historical_success",
                            "previous_identifier": historical["identifier"],
                            "reason": "当前云效媒体引用不可用，已按文件名和大小复用历史成功媒体。",
                        },
                    }
                )
                if identifier:
                    stored_paths[identifier] = historical["stored_path"]
                items.append(item)
                continue
            item["status"] = "not_downloaded"
            item["error"] = item["error"] or "云效未提供可归档的本地下载文件"
            items.append(item)
            continue
        source_path = Path(source_path_text).expanduser()
        if source_path.is_symlink():
            item["status"] = "untrusted_source_path"
            item["error"] = "拒绝归档符号链接来源文件"
            items.append(item)
            continue
        try:
            resolved_source = source_path.resolve(strict=True)
            resolved_source.relative_to(staging_root)
        except (OSError, ValueError):
            item["status"] = "untrusted_source_path"
            item["error"] = "来源文件不在本次云效读取的受控临时目录中"
            items.append(item)
            continue
        if not resolved_source.is_file():
            item["status"] = "source_missing"
            item["error"] = "受控临时目录中的下载文件不存在或不是普通文件"
            items.append(item)
            continue
        size = resolved_source.stat().st_size
        item["size"] = size
        if max_file_bytes is not None and size > max_file_bytes:
            item["status"] = "too_large"
            item["error"] = f"文件大小 {size} bytes 超过归档上限 {max_file_bytes} bytes"
            items.append(item)
            continue
        sha256 = _sha256_file(resolved_source)
        destination_dir = attachment_dir if kind == "attachment" else inline_dir
        destination = _choose_destination(
            directory=destination_dir,
            identifier=identifier or "file",
            original_name=original_name,
            source_path=resolved_source,
            sha256=sha256,
        )
        relative = destination.relative_to(destination_dir.parent).as_posix()
        if destination.exists():
            item["status"] = "reused"
        else:
            shutil.copyfile(resolved_source, destination)
            item["status"] = "success"
        item["stored_path"] = relative
        item["sha256"] = sha256
        if identifier:
            stored_paths[identifier] = relative
        items.append(item)
    return items, stored_paths


def _read_existing_manifest(*, manifest_path: Path) -> dict:
    if not manifest_path.exists():
        return {}
    try:
        return _read_json_object(manifest_path)
    except ValueError as exc:
        raise ValueError(f"现有云效归档清单无法读取，拒绝覆盖：{manifest_path}") from exc


def _index_previous_media(
    previous_items: list[object],
    *,
    media_root: Path,
) -> dict[tuple[str, str, int], dict]:
    indexed: dict[tuple[str, str, int], dict] = {}
    for raw in previous_items:
        if not isinstance(raw, Mapping) or raw.get("status") not in {"success", "reused"}:
            continue
        stored_path = str(raw.get("stored_path") or "").strip()
        original_name = str(raw.get("original_name") or "").strip()
        size = raw.get("size")
        if not stored_path or not original_name or not isinstance(size, int):
            continue
        candidate = (media_root / stored_path).resolve()
        try:
            candidate.relative_to(media_root.resolve())
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        actual_sha256 = _sha256_file(candidate)
        expected_sha256 = str(raw.get("sha256") or "").strip()
        if expected_sha256 and expected_sha256 != actual_sha256:
            continue
        indexed.setdefault(
            _media_reconciliation_key(raw),
            {
                "identifier": str(raw.get("identifier") or ""),
                "stored_path": stored_path,
                "size": size,
                "sha256": actual_sha256,
                "content_type": str(raw.get("content_type") or ""),
            },
        )
    return indexed


def _find_previous_media(
    previous_media: Mapping[tuple[str, str, int], dict],
    *,
    kind: str,
    original_name: str,
    size: object,
) -> dict | None:
    if not original_name or not isinstance(size, int):
        return None
    return previous_media.get(
        _media_reconciliation_key(
            {"kind": kind, "original_name": original_name, "size": size}
        )
    )


def _media_reconciliation_key(value: Mapping[str, object]) -> tuple[str, str, int]:
    kind = str(value.get("kind") or "inline_file").strip().lower()
    if kind == "attachment":
        normalized_kind = "attachment"
    elif "inline" in kind or "image" in kind:
        normalized_kind = "inline"
    else:
        normalized_kind = kind
    name = Path(
        str(value.get("original_name") or value.get("name") or "")
    ).name.strip().lower()
    size = value.get("size")
    return normalized_kind, name, size if isinstance(size, int) else -1


def _collect_download_records(evidence: Mapping[str, object]) -> list[dict]:
    records: dict[str, dict] = {}

    def record_key(value: Mapping[str, object], index: int) -> str:
        identifier = str(
            value.get("identifier")
            or value.get("fileIdentifier")
            or value.get("fileId")
            or value.get("file_id")
            or ""
        ).strip()
        if identifier:
            return f"id:{identifier}"
        return f"fallback:{value.get('kind') or 'file'}:{value.get('name') or value.get('url') or index}"

    def upsert(value: Mapping[str, object], index: int) -> None:
        key = record_key(value, index)
        prior = records.get(key, {})
        merged = dict(prior)
        for item_key, item_value in value.items():
            if item_value not in (None, "", {}, []):
                merged[item_key] = item_value
        records[key] = merged

    listed_index = 0
    for source_key, default_kind in (("attachments", "attachment"), ("inline_files", "inline_file")):
        listed = evidence.get(source_key)
        if not isinstance(listed, list):
            continue
        for item in listed:
            if not isinstance(item, Mapping):
                continue
            listed_index += 1
            candidate = dict(item)
            if not candidate.get("identifier"):
                candidate["identifier"] = (
                    candidate.get("fileIdentifier")
                    or candidate.get("fileId")
                    or candidate.get("file_id")
                    or ""
                )
            candidate.setdefault("kind", default_kind)
            upsert(candidate, listed_index)

    file_details = evidence.get("file_details")
    if isinstance(file_details, list):
        for detail in file_details:
            if not isinstance(detail, Mapping):
                continue
            download = detail.get("download")
            merged = dict(detail)
            metadata = detail.get("data")
            if isinstance(metadata, Mapping):
                for target_key, source_keys in {
                    "identifier": ("identifier", "fileIdentifier", "fileId", "id"),
                    "name": ("name", "fileName", "filename", "title"),
                    "size": ("size", "fileSize"),
                    "content_type": ("content_type", "contentType", "mimeType", "type"),
                    "kind": ("kind", "type"),
                }.items():
                    if merged.get(target_key) not in (None, ""):
                        continue
                    for source_key in source_keys:
                        if metadata.get(source_key) not in (None, ""):
                            merged[target_key] = metadata[source_key]
                            break
            if isinstance(download, Mapping):
                merged.update(download)
            listed_index += 1
            upsert(merged, listed_index)
    inline_downloads = evidence.get("inline_file_downloads")
    if isinstance(inline_downloads, list):
        for download in inline_downloads:
            if not isinstance(download, Mapping):
                continue
            listed_index += 1
            upsert(download, listed_index)
    return list(records.values())


def _choose_destination(*, directory: Path, identifier: str, original_name: str, source_path: Path, sha256: str) -> Path:
    safe_identifier = _safe_component(identifier) or "file"
    safe_name = _safe_filename(original_name) or _safe_filename(source_path.name) or f"file{source_path.suffix}"
    candidate = directory / f"{safe_identifier}--{safe_name}"
    if not candidate.exists() or _sha256_file(candidate) == sha256:
        return candidate
    return directory / f"{safe_identifier}--{sha256[:12]}--{safe_name}"


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")[:80]


def _safe_filename(value: str) -> str:
    name = Path(value).name.replace("\x00", "")
    name = re.sub(r"[/:\\\\]+", "_", name).strip(". ")
    return name[:180]


def _replace_snapshot_media_paths(
    snapshot: dict,
    paths: Mapping[str, str],
    *,
    media_items: list[Mapping[str, object]] | None = None,
) -> None:
    historical_paths = {
        _media_reconciliation_key(item): str(item.get("stored_path") or "")
        for item in (media_items or [])
        if isinstance(item, Mapping) and str(item.get("stored_path") or "").strip()
    }
    for key in ("attachments", "images"):
        values = snapshot.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("identifier") or item.get("id") or "")
            if identifier and identifier in paths:
                item["path"] = paths[identifier]
                continue
            if item.get("path"):
                continue
            kind = "attachment" if key == "attachments" else "inline_image"
            historical_path = historical_paths.get(
                _media_reconciliation_key(
                    {
                        "kind": kind,
                        "name": item.get("name"),
                        "size": item.get("size"),
                    }
                )
            )
            if historical_path:
                item["path"] = historical_path


def _ensure_readme(*, ticket_dir: Path, ticket_id: str) -> None:
    path = ticket_dir / "README.md"
    if path.exists():
        return
    _atomic_write_text(
        path,
        "\n".join(
            [
                f"# {ticket_id} 云效需求档案",
                "",
                "- 主文档：[requirement.md](requirement.md)",
                "- 云效原始快照：[yunxiao/snapshot.json](yunxiao/snapshot.json)",
                "- 可读来源：[yunxiao/source.md](yunxiao/source.md)",
                "- 附件清单与校验：[yunxiao/manifest.json](yunxiao/manifest.json)",
                "",
                "本目录只保存本地只读证据；不会向云效写评论、附件、状态或负责人。",
                "",
            ]
        ),
    )


def _update_requirement_document(
    *,
    path: Path,
    ticket_id: str,
    snapshot: Mapping[str, object],
    manifest: Mapping[str, object],
    requirement_understanding: str,
    solution_plan: str,
    change_note: str,
    synced_at: str,
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else _new_requirement_document(ticket_id)
    info = "\n".join(
        [
            f"- 云效编号：{ticket_id}",
            f"- 标题：{snapshot.get('title') or '-'}",
            f"- 云效状态：{snapshot.get('status') or '-'}",
            f"- 负责人：{snapshot.get('assignee') or '-'}",
            f"- 来源链接：{snapshot.get('source_url') or '-'}",
            f"- 最近同步：{synced_at}",
            f"- 附件完整性：{manifest.get('status') or '-'}（见 `yunxiao/manifest.json`）",
        ]
    )
    existing = _replace_managed_block(existing, "yunxiao-info", info)
    if requirement_understanding.strip():
        existing = _replace_managed_block(existing, "understanding", requirement_understanding.strip())
    if solution_plan.strip():
        existing = _replace_managed_block(existing, "solution-plan", solution_plan.strip())
    note = change_note.strip() or "云效只读同步更新。"
    change_block = _get_managed_block(existing, "change-log")
    entry = f"- {synced_at}：{note}"
    if entry not in change_block:
        change_block = "\n".join(part for part in [change_block.strip(), entry] if part)
    existing = _replace_managed_block(existing, "change-log", change_block)
    _atomic_write_text(path, existing.rstrip() + "\n")


def _new_requirement_document(ticket_id: str) -> str:
    return "\n".join(
        [
            f"# {ticket_id} 需求档案",
            "",
            "本文件是同一云效需求的长期工作文档。Harness 托管区块会随同步更新；未包含在托管区块中的人工补充会被保留。",
            "",
            _managed_block("yunxiao-info", "- 尚未同步。"),
            "",
            "## 需求理解",
            "",
            _managed_block("understanding", "- 待补充。"),
            "",
            "## 方案",
            "",
            _managed_block("solution-plan", "- 待补充。"),
            "",
            "## 人工补充",
            "",
            "在这里记录现场确认、业务边界或人工判断；后续同步不会覆盖本节。",
            "",
            "## 变更记录",
            "",
            _managed_block("change-log", ""),
            "",
        ]
    )


def _managed_block(name: str, content: str) -> str:
    return f"<!-- harness:{name}:start -->\n{content}\n<!-- harness:{name}:end -->"


def _get_managed_block(document: str, name: str) -> str:
    match = re.search(
        rf"<!-- harness:{re.escape(name)}:start -->\n?(.*?)\n?<!-- harness:{re.escape(name)}:end -->",
        document,
        flags=re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _replace_managed_block(document: str, name: str, content: str) -> str:
    replacement = _managed_block(name, content)
    pattern = re.compile(
        rf"<!-- harness:{re.escape(name)}:start -->\n?.*?\n?<!-- harness:{re.escape(name)}:end -->",
        flags=re.DOTALL,
    )
    if pattern.search(document):
        return pattern.sub(replacement, document, count=1)
    return document.rstrip() + "\n\n" + replacement + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, value: Mapping[str, object] | list[object]) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temp_path.write_text(content, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取云效需求档案：{path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"云效需求档案 JSON 根节点必须是对象：{path}")
    return data
