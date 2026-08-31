from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from app.sensitive_text import contains_sensitive_text


KNOWLEDGE_INDEX_SCHEMA_VERSION = "his-knowledge-index.v2"
_GOVERNANCE_KEYS = frozenset(("status", "evidence_level", "valid_until"))
_GOVERNANCE_STATUSES = frozenset(("approved", "candidate", "conflicted", "unknown"))
KNOWLEDGE_MANIFEST_SCHEMA_VERSION = "his-knowledge-manifest.v1"
_MANIFEST_FILE_NAME = ".harness-knowledge-manifest.json"


def publish_approved_knowledge_markdown(
    knowledge_home: str | Path,
    *,
    content_hash: str,
    title: str,
    body: str,
    valid_until: str = "",
    allowed_base: str | Path,
) -> dict[str, str | bool]:
    """Atomically publish one already-reviewed, audit-safe knowledge note.

    This is intentionally not a candidate reviewer.  Callers must authorize
    the candidate state transition before reaching this narrow Markdown/index
    publication seam.  The manifest is content-hash keyed so a repeated
    promotion cannot create duplicate Obsidian notes.
    """

    safe_hash = _required_content_hash(content_hash)
    safe_title = _required_public_markdown_text(title, "title", maximum=160)
    safe_body = _required_public_markdown_text(body, "body", maximum=4_096)
    safe_valid_until = _required_valid_until(valid_until)
    home, configured_base = _resolve_knowledge_home(
        knowledge_home, allowed_base=allowed_base, create_home=True
    )
    vault = _ensure_supported_directory(home, "vault")
    review_dir = _ensure_supported_directory(home, "vault", "90-review")
    manifest_path = vault / _MANIFEST_FILE_NAME
    if manifest_path.is_symlink():
        raise ValueError("knowledge_manifest_path_invalid")
    manifest = _read_knowledge_manifest(manifest_path)
    entries = manifest["entries"]
    existing = next(
        (
            item
            for item in entries
            if isinstance(item, dict) and item.get("content_hash") == safe_hash
        ),
        None,
    )
    if existing is not None:
        source_path = existing.get("source_path")
        if not _valid_manifest_source_path(source_path):
            raise ValueError("knowledge_manifest_invalid")
        candidate_path = _supported_child_path(home, *PurePosixPath(source_path).parts)
        if not candidate_path.is_file():
            raise ValueError("knowledge_manifest_invalid")
        return {
            "changed": False,
            "content_hash": safe_hash,
            "source_path": source_path,
            "markdown_path": str(candidate_path),
            "manifest_path": str(manifest_path),
        }

    source_name = f"knowledge-{safe_hash[7:23]}.md"
    markdown_path = review_dir / source_name
    if markdown_path.exists() or markdown_path.is_symlink():
        raise ValueError("knowledge_markdown_path_conflict")
    frontmatter = [
        "---",
        "status: approved",
        "evidence_level: reviewer_approved",
    ]
    if safe_valid_until:
        frontmatter.append(f'valid_until: "{safe_valid_until}"')
    frontmatter.append("---")
    markdown = "\n".join(frontmatter) + f"\n# {safe_title}\n\n{safe_body}\n"
    _atomic_write_text(markdown_path, markdown)
    source_path = markdown_path.relative_to(home).as_posix()
    entries.append(
        {
            "content_hash": safe_hash,
            "source_path": source_path,
            "valid_until": safe_valid_until,
        }
    )
    _atomic_write_text(
        manifest_path,
        json.dumps(
            {
                "schema_version": KNOWLEDGE_MANIFEST_SCHEMA_VERSION,
                "entries": sorted(entries, key=lambda item: str(item["content_hash"])),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    # The readable Markdown and manifest are written atomically first; the
    # structured index is a derived cache and can always be explicitly re-sync'd.
    sync_obsidian_markdown_index(home, allowed_base=configured_base)
    return {
        "changed": True,
        "content_hash": safe_hash,
        "source_path": source_path,
        "markdown_path": str(markdown_path),
        "manifest_path": str(manifest_path),
    }


def sync_obsidian_markdown_index(
    knowledge_home: str | Path,
    *,
    allowed_base: str | Path | None = None,
) -> dict[str, Any]:
    """Index Obsidian Markdown notes into the local knowledge SQLite database."""

    home, _configured_base = _resolve_knowledge_home(
        knowledge_home, allowed_base=allowed_base, create_home=True
    )
    vault = _supported_child_path(home, "vault")
    database_path = _supported_child_path(home, "knowledge.sqlite")
    markdown_files = sorted(vault.rglob("*.md")) if vault.is_dir() else []
    rows: list[dict[str, str]] = []
    skipped_sensitive = 0
    skipped_invalid_metadata = 0
    for markdown_file in markdown_files:
        _supported_child_path(home, *markdown_file.relative_to(home).parts)
        if not markdown_file.is_file():
            continue
        content = markdown_file.read_text(encoding="utf-8")
        if contains_sensitive_text(content):
            skipped_sensitive += 1
            continue
        metadata, body, metadata_valid = _parse_frontmatter(content)
        if not metadata_valid:
            skipped_invalid_metadata += 1
            continue
        rows.append(
            {
                "source_path": markdown_file.relative_to(home).as_posix(),
                "title": _markdown_title(markdown_file, body),
                "content": body,
                "content_hash": _content_hash(content),
                "status": metadata.get("status", "unknown").strip().lower() or "unknown",
                "evidence_level": metadata.get("evidence_level", "").strip(),
                "valid_until": metadata.get("valid_until", "").strip(),
            }
        )
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("begin immediate")
        try:
            connection.execute(
                """
                create table if not exists obsidian_markdown_index (
                    source_path text primary key,
                    title text not null,
                    content text not null,
                    content_hash text not null,
                    status text not null default 'unknown',
                    evidence_level text not null default '',
                    valid_until text not null default ''
                )
                """
            )
            _ensure_index_column(connection, "status", "text not null default 'unknown'")
            _ensure_index_column(connection, "evidence_level", "text not null default ''")
            _ensure_index_column(connection, "valid_until", "text not null default ''")
            _replace_index_rows(connection, rows)
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
    return {
        "schema_version": KNOWLEDGE_INDEX_SCHEMA_VERSION,
        "changed": bool(rows),
        "indexed_count": len(rows),
        "skipped_sensitive_count": skipped_sensitive,
        "skipped_invalid_metadata_count": skipped_invalid_metadata,
        "sqlite_path": str(database_path),
        "vault_path": str(vault),
    }


def query_knowledge_index(
    knowledge_home: str | Path,
    query: str,
    *,
    limit: int = 5,
    allowed_base: str | Path | None = None,
) -> dict[str, Any]:
    """Retrieve indexed local knowledge with citations.

    If no indexed source matches, the response explicitly reports a gap instead
    of fabricating an answer.
    """

    search_text = _required_query(query)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("knowledge query limit must be a positive integer")
    home, _configured_base = _resolve_knowledge_home(
        knowledge_home, allowed_base=allowed_base, create_home=False
    )
    database_path = _supported_child_path(home, "knowledge.sqlite")
    if not database_path.is_file():
        return _knowledge_gap(search_text)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row[1])
            for row in connection.execute("pragma table_info(obsidian_markdown_index)")
        }
        required_columns = {"status", "evidence_level", "valid_until"}
        if not required_columns.issubset(columns):
            return _knowledge_gap(search_text)
        rows = connection.execute(
            """
            select source_path, title, content, content_hash,
                   status, evidence_level, valid_until
            from obsidian_markdown_index
            where instr(lower(title), lower(?)) > 0
               or instr(lower(content), lower(?)) > 0
            order by source_path
            """,
            (search_text, search_text),
        ).fetchall()
    matched = [
        {
            "source_path": row["source_path"],
            "title": row["title"],
            "snippet": _snippet(row["content"], search_text),
            "content_hash": row["content_hash"],
            "status": row["status"],
            "evidence_level": row["evidence_level"],
            "valid_until": row["valid_until"],
        }
        for row in rows
    ]
    if not matched:
        return _knowledge_gap(search_text)
    results = [item for item in matched if _is_direct_answer_source(item)][:limit]
    if not results:
        return _knowledge_gap(search_text, retrieval_status="knowledge_insufficient")
    return {
        "schema_version": KNOWLEDGE_INDEX_SCHEMA_VERSION,
        "answerable": True,
        "retrieval_status": "knowledge_hit",
        "query": search_text,
        "message": "已从本地知识库检索到可引用来源。",
        "results": results,
        "citations": [item["source_path"] for item in results],
    }


def _required_content_hash(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("knowledge_content_hash_invalid")
    return value


def _required_public_markdown_text(value: object, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"knowledge_{name}_invalid")
    normalized = value.strip()
    if contains_sensitive_text(normalized) or "\x00" in normalized:
        raise ValueError(f"knowledge_{name}_invalid")
    return normalized


def _required_valid_until(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError("knowledge_valid_until_invalid")
    return _valid_expiry_timestamp(value)


def _resolve_knowledge_home(
    knowledge_home: str | Path,
    *,
    allowed_base: str | Path | None,
    create_home: bool,
) -> tuple[Path, Path]:
    raw_home = _absolute_unresolved_path(knowledge_home)
    raw_base = _absolute_unresolved_path(
        allowed_base if allowed_base is not None else raw_home.parent
    )
    try:
        raw_home.relative_to(raw_base)
    except ValueError:
        raise ValueError("knowledge_path_outside_allowed_base") from None
    # Verify raw components first.  Resolving a path before this check would
    # silently turn an allowed root or a supported vault child into an external
    # location through a symlink.
    _reject_symlink_components(raw_base, raw_home)
    if not raw_base.exists() or not raw_base.is_dir():
        raise ValueError("knowledge_allowed_base_invalid")
    resolved_base = raw_base.resolve(strict=True)
    resolved_home = raw_home.resolve(strict=False)
    try:
        resolved_home.relative_to(resolved_base)
    except ValueError:
        raise ValueError("knowledge_path_outside_allowed_base") from None
    if create_home:
        raw_home.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(raw_base, raw_home)
        if not raw_home.is_dir():
            raise ValueError("knowledge_path_invalid")
    elif not raw_home.exists():
        return raw_home, raw_base
    return raw_home, raw_base


def _absolute_unresolved_path(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise ValueError("knowledge_path_invalid")
    return Path(os.path.abspath(os.fspath(Path(value).expanduser())))


def _reject_symlink_components(base: Path, candidate: Path) -> None:
    if base.is_symlink():
        raise ValueError("knowledge_path_invalid")
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        raise ValueError("knowledge_path_outside_allowed_base") from None
    current = base
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("knowledge_path_invalid")


def _supported_child_path(home: Path, *parts: str) -> Path:
    path = home.joinpath(*parts)
    _reject_symlink_components(home, path)
    resolved_home = home.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_home)
    except ValueError:
        raise ValueError("knowledge_path_invalid") from None
    return path


def _ensure_supported_directory(home: Path, *parts: str) -> Path:
    path = _supported_child_path(home, *parts)
    path.mkdir(parents=True, exist_ok=True)
    _supported_child_path(home, *parts)
    if not path.is_dir():
        raise ValueError("knowledge_path_invalid")
    return path


def _read_knowledge_manifest(path: Path) -> dict[str, list[dict[str, str]]]:
    if not path.exists():
        return {"entries": []}
    if path.is_symlink() or not path.is_file():
        raise ValueError("knowledge_manifest_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("knowledge_manifest_invalid") from None
    if not isinstance(value, dict) or value.get("schema_version") != KNOWLEDGE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("knowledge_manifest_invalid")
    entries = value.get("entries")
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise ValueError("knowledge_manifest_invalid")
    normalized: list[dict[str, str]] = []
    for item in entries:
        content_hash = item.get("content_hash")
        source_path = item.get("source_path")
        if not _valid_manifest_source_path(source_path):
            raise ValueError("knowledge_manifest_invalid")
        normalized.append(
            {
                "content_hash": _required_content_hash(content_hash),
                "source_path": source_path,
                "valid_until": _required_valid_until(item.get("valid_until", "")),
            }
        )
    return {"entries": normalized}


def _valid_manifest_source_path(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("vault/"):
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and len(path.parts) >= 2
        and path.parts[0] == "vault"
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _atomic_write_text(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("knowledge_path_invalid")
    descriptor = -1
    temporary_path = ""
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as opened:
            descriptor = -1
            opened.write(content)
            opened.flush()
            os.fsync(opened.fileno())
        os.replace(temporary_path, path)
        temporary_path = ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _markdown_title(path: Path, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _required_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("knowledge query must be a non-empty string")
    return query.strip()


def _knowledge_gap(
    query: str,
    *,
    retrieval_status: str = "knowledge_gap",
) -> dict[str, Any]:
    return {
        "schema_version": KNOWLEDGE_INDEX_SCHEMA_VERSION,
        "answerable": False,
        "retrieval_status": retrieval_status,
        "query": query,
        "message": "本地知识库缺资料，不能直接回答；需要补充来源或生成 knowledge candidate。",
        "results": [],
        "citations": [],
    }


def _snippet(content: str, query: str, radius: int = 48) -> str:
    compact = " ".join(content.split())
    index = compact.lower().find(query.lower())
    if index < 0:
        return compact[: radius * 2]
    start = max(0, index - radius)
    end = min(len(compact), index + len(query) + radius)
    return compact[start:end]


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str, bool]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content, True
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        return {}, "", False
    metadata: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line != line.lstrip():
            return {}, "", False
        if ":" not in line:
            return {}, "", False
        key, value = line.split(":", 1)
        parsed_key = _frontmatter_scalar(key)
        if parsed_key is None:
            return {}, "", False
        normalized_key = parsed_key.strip().lower()
        if normalized_key in _GOVERNANCE_KEYS:
            if normalized_key in metadata:
                return {}, "", False
            parsed_value = _frontmatter_scalar(value)
            if parsed_value is None:
                return {}, "", False
            metadata[normalized_key] = parsed_value
    body = "\n".join(lines[closing_index + 1 :]).lstrip()
    status = metadata.get("status", "unknown").strip().lower() or "unknown"
    if status not in _GOVERNANCE_STATUSES:
        return {}, "", False
    valid_until = metadata.get("valid_until", "").strip()
    if valid_until:
        try:
            _valid_expiry_timestamp(valid_until)
        except ValueError:
            return {}, "", False
    return metadata, body, True


def _frontmatter_scalar(value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return ""
    if stripped[0] in {"\"", "'"}:
        if len(stripped) < 2 or stripped[-1] != stripped[0]:
            return None
        inner = stripped[1:-1]
        if stripped[0] in inner:
            return None
        return inner.strip()
    if stripped[-1] in {"\"", "'"}:
        return None
    if stripped[0] in {"[", "{", "|", ">", "&", "*", "!"}:
        return None
    if stripped.lower() in {"null", "~"}:
        return None
    if ":" in stripped:
        return None
    return stripped


def _is_direct_answer_source(item: dict[str, Any]) -> bool:
    if str(item.get("status") or "").strip().lower() != "approved":
        return False
    if not str(item.get("evidence_level") or "").strip():
        return False
    valid_until = str(item.get("valid_until") or "").strip()
    if not valid_until:
        return True
    try:
        if len(valid_until) == 10:
            # Legacy date-only metadata is treated as expiring at the start of
            # that day.  New publications always use a timezone timestamp.
            return date.fromisoformat(valid_until) > date.today()
        expiry = datetime.fromisoformat(valid_until)
        return (
            expiry.tzinfo is not None
            and expiry.utcoffset() is not None
            and expiry > datetime.now(timezone.utc)
        )
    except ValueError:
        return False


def _valid_expiry_timestamp(value: str) -> str:
    try:
        legacy_date = date.fromisoformat(value)
    except ValueError:
        legacy_date = None
    if legacy_date is not None and legacy_date.isoformat() == value:
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("knowledge_valid_until_invalid") from None
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.isoformat() != value
    ):
        raise ValueError("knowledge_valid_until_invalid")
    return value


def _ensure_index_column(connection: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("pragma table_info(obsidian_markdown_index)")
    }
    if name not in columns:
        connection.execute(f"alter table obsidian_markdown_index add column {name} {definition}")


def _replace_index_rows(
    connection: sqlite3.Connection,
    rows: list[dict[str, str]],
) -> None:
    connection.execute("delete from obsidian_markdown_index")
    connection.executemany(
        """
        insert into obsidian_markdown_index
            (source_path, title, content, content_hash, status, evidence_level, valid_until)
        values
            (:source_path, :title, :content, :content_hash, :status, :evidence_level, :valid_until)
        """,
        rows,
    )
