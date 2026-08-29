from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tarfile
from pathlib import Path
from typing import Any, Iterable

from app.enterprise_gate import scan_source_secrets


RELEASE_MANIFEST_SCHEMA_VERSION = "1.0-reproducible-release-manifest"
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9A-Za-z-]+)+$")
RELEASE_DIRECTORIES = (
    ".github/workflows",
    "app",
    "config",
    "docs",
    "fixtures",
    "harnesses",
    "prompts",
    "tests",
    "tools",
)
RELEASE_ROOT_FILES = (
    "CHANGELOG.md",
    "HANDOFF.md",
    "README.md",
    "real_precommit_trial_template.md",
    "requirements.txt",
    "run.py",
    "scope_warning_policy.md",
)
IGNORED_NAMES = frozenset({".DS_Store", "__pycache__"})
IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".sqlite", ".db"})


def build_release_bundle(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    version: str,
) -> dict[str, Any]:
    normalized_version = validate_version(version)
    root = Path(project_root).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    secret_scan = scan_source_secrets(root)
    if secret_scan["status"] != "passed":
        raise ValueError("源码密钥扫描未通过，拒绝构建发布包。")

    files = collect_release_files(root)
    if not files:
        raise ValueError("发布文件白名单为空，拒绝构建。")
    content_manifest = [file_manifest_entry(root, path) for path in files]
    bundle_name = f"his-harness-{normalized_version}"
    archive_path = destination / f"{bundle_name}.tar.gz"
    temporary_archive = destination / f".{bundle_name}.tar.gz.tmp"
    write_deterministic_archive(
        project_root=root,
        files=files,
        archive_path=temporary_archive,
        bundle_name=bundle_name,
    )
    os.replace(temporary_archive, archive_path)
    archive_sha256 = sha256_file(archive_path)
    manifest = {
        "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
        "version": normalized_version,
        "bundle_name": bundle_name,
        "archive_name": archive_path.name,
        "archive_sha256": archive_sha256,
        "secret_scan_status": secret_scan["status"],
        "file_count": len(content_manifest),
        "files": content_manifest,
        "excluded_runtime_content": [
            "data/",
            "credentials and personal config",
            "run outputs and workspace snapshots",
            "temporary worktrees and caches",
        ],
        "external_calls": False,
    }
    manifest_path = destination / f"{bundle_name}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    checksum_path = destination / f"{bundle_name}.sha256"
    checksum_path.write_text(f"{archive_sha256}  {archive_path.name}\n", encoding="ascii")
    return {
        "status": "passed",
        "version": normalized_version,
        "archive_path": str(archive_path),
        "archive_sha256": archive_sha256,
        "manifest_path": str(manifest_path),
        "checksum_path": str(checksum_path),
        "file_count": len(content_manifest),
        "secret_scan_status": secret_scan["status"],
        "external_calls": False,
    }


def validate_version(version: str) -> str:
    normalized = str(version).strip()
    if not VERSION_PATTERN.fullmatch(normalized):
        raise ValueError("版本号必须是类似 0.63.0 的安全格式。")
    return normalized


def collect_release_files(project_root: Path) -> list[Path]:
    candidates: set[Path] = set()
    for relative in RELEASE_ROOT_FILES:
        path = project_root / relative
        if path.is_file() and should_include(path):
            candidates.add(path)
    for relative in RELEASE_DIRECTORIES:
        directory = project_root / relative
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and should_include(path):
                candidates.add(path)
    return sorted(candidates, key=lambda item: item.relative_to(project_root).as_posix())


def should_include(path: Path) -> bool:
    if any(part in IGNORED_NAMES for part in path.parts):
        return False
    return path.suffix.lower() not in IGNORED_SUFFIXES


def file_manifest_entry(project_root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(project_root).as_posix(),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def write_deterministic_archive(
    *,
    project_root: Path,
    files: Iterable[Path],
    archive_path: Path,
    bundle_name: str,
) -> None:
    with archive_path.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, compresslevel=9, mtime=0) as gzip_file:
            with tarfile.open(fileobj=gzip_file, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in files:
                    relative = path.relative_to(project_root).as_posix()
                    data = path.read_bytes()
                    info = tarfile.TarInfo(name=f"{bundle_name}/{relative}")
                    info.size = len(data)
                    info.mode = 0o644
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.pax_headers = {}
                    archive.addfile(info, fileobj=BytesReader(data))


class BytesReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
