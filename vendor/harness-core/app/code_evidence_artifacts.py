from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


_LEAF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = frozenset(
    (
        "bundle_seal",
        "diff_patch",
        "diff_manifest",
        "diff_file",
        "source",
        "source_manifest",
        "search_manifest",
        "history",
        "history_manifest",
        "verification_receipt",
        "verification_stdout",
        "verification_stderr",
        "review",
        "review_seal",
        "evidence_set_manifest",
    )
)
_SENSITIVE_SUFFIXES = frozenset((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))
_SENSITIVE_NAMES = frozenset((".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"))
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class EvidenceArtifactRecord:
    bundle_id: int
    kind: str
    relative_path: str
    sha256: str
    size_bytes: int
    device: int
    inode: int
    mode: int
    link_count: int


class EvidenceArtifactStore:
    """Private no-follow storage for immutable code-evidence artifacts."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("code_evidence_artifact_root_invalid")
        self.root = root
        try:
            root.parent.mkdir(parents=True, exist_ok=True)
            if not root.exists():
                root.mkdir(mode=0o700)
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode) or root.is_symlink() or info.st_uid != os.getuid():
                raise ValueError
            if stat.S_IMODE(info.st_mode) != 0o700:
                os.chmod(root, 0o700, follow_symlinks=False)
            with self._open_root() as root_fd:
                root_now = os.fstat(root_fd)
                if (root_now.st_dev, root_now.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError
        except ValueError:
            raise ValueError("code_evidence_artifact_root_invalid") from None
        except OSError:
            raise ValueError("code_evidence_artifact_root_invalid") from None

    def persist(
        self,
        bundle_id: int,
        *,
        kind: str,
        leaf: str,
        content: bytes,
    ) -> EvidenceArtifactRecord:
        safe_bundle_id = _positive_int(bundle_id)
        safe_kind = _kind(kind)
        safe_leaf = _leaf(leaf)
        if not isinstance(content, bytes):
            raise ValueError("code_evidence_artifact_input_invalid")
        if len(content) > _MAX_ARTIFACT_BYTES:
            raise ValueError("code_evidence_artifact_limit_exceeded")
        if safe_kind != "bundle_seal" and self._seal_exists(safe_bundle_id):
            raise ValueError("code_evidence_bundle_sealed")
        if safe_kind == "bundle_seal" and safe_leaf != "seal.json":
            raise ValueError("code_evidence_artifact_input_invalid")

        bundle_name = f"bundle_{safe_bundle_id}"
        temp_leaf = f".tmp-{secrets.token_hex(16)}"
        temp_created = False
        try:
            with self._open_root() as root_fd:
                bundle_fd = self._open_bundle(root_fd, bundle_name)
                try:
                    self._verify_bundle_entry(safe_bundle_id, bundle_fd)
                    if _entry_exists(bundle_fd, safe_leaf):
                        raise ValueError("code_evidence_artifact_exists")
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                    file_fd = os.open(temp_leaf, flags, 0o600, dir_fd=bundle_fd)
                    temp_created = True
                    try:
                        _write_all(file_fd, content)
                        os.fsync(file_fd)
                        written = os.fstat(file_fd)
                        _validate_regular_file(written, size=len(content))
                    finally:
                        os.close(file_fd)
                    self._verify_bundle_entry(safe_bundle_id, bundle_fd)
                    os.rename(temp_leaf, safe_leaf, src_dir_fd=bundle_fd, dst_dir_fd=bundle_fd)
                    temp_created = False
                    os.fsync(bundle_fd)
                    final_fd = os.open(safe_leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=bundle_fd)
                    try:
                        final_info = os.fstat(final_fd)
                        _validate_regular_file(final_info, size=len(content))
                        actual = _read_all(final_fd, len(content))
                    finally:
                        os.close(final_fd)
                    if actual != content:
                        raise ValueError("code_evidence_artifact_changed")
                    self._verify_bundle_entry(safe_bundle_id, bundle_fd)
                finally:
                    if temp_created:
                        try:
                            os.unlink(temp_leaf, dir_fd=bundle_fd)
                        except OSError:
                            pass
                    os.close(bundle_fd)
        except ValueError:
            raise
        except OSError:
            raise ValueError("code_evidence_artifact_path_invalid") from None

        return EvidenceArtifactRecord(
            bundle_id=safe_bundle_id,
            kind=safe_kind,
            relative_path=f"{bundle_name}/{safe_leaf}",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            device=int(final_info.st_dev),
            inode=int(final_info.st_ino),
            mode=int(stat.S_IMODE(final_info.st_mode)),
            link_count=int(final_info.st_nlink),
        )

    def reopen(self, record: EvidenceArtifactRecord) -> bytes:
        checked = _record(record)
        bundle_name, leaf = checked.relative_path.split("/", 1)
        try:
            with self._open_root() as root_fd:
                bundle_fd = os.open(bundle_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    self._verify_bundle_entry(checked.bundle_id, bundle_fd)
                    file_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=bundle_fd)
                    try:
                        info = os.fstat(file_fd)
                        _validate_regular_file(info, size=checked.size_bytes)
                        if (
                            int(info.st_dev) != checked.device
                            or int(info.st_ino) != checked.inode
                            or stat.S_IMODE(info.st_mode) != checked.mode
                            or int(info.st_nlink) != checked.link_count
                        ):
                            raise ValueError("code_evidence_artifact_changed")
                        content = _read_all(file_fd, checked.size_bytes)
                    finally:
                        os.close(file_fd)
                    self._verify_bundle_entry(checked.bundle_id, bundle_fd)
                finally:
                    os.close(bundle_fd)
        except ValueError:
            raise
        except OSError:
            raise ValueError("code_evidence_artifact_changed") from None
        if len(content) != checked.size_bytes or hashlib.sha256(content).hexdigest() != checked.sha256:
            raise ValueError("code_evidence_artifact_changed")
        return content

    def seal(
        self,
        bundle_id: int,
        *,
        artifacts: Sequence[EvidenceArtifactRecord],
        repository_snapshot_sha256: str,
    ) -> EvidenceArtifactRecord:
        safe_bundle_id = _positive_int(bundle_id)
        if not isinstance(repository_snapshot_sha256, str) or _SHA256.fullmatch(repository_snapshot_sha256) is None:
            raise ValueError("code_evidence_artifact_input_invalid")
        if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)) or not artifacts:
            raise ValueError("code_evidence_artifact_input_invalid")
        checked = tuple(_record(item) for item in artifacts)
        if any(item.bundle_id != safe_bundle_id or item.kind == "bundle_seal" for item in checked):
            raise ValueError("code_evidence_artifact_input_invalid")
        if len({item.relative_path for item in checked}) != len(checked):
            raise ValueError("code_evidence_artifact_input_invalid")
        for item in checked:
            self.reopen(item)
        manifest = {
            "artifacts": [
                {
                    "kind": item.kind,
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in sorted(checked, key=lambda value: (value.kind, value.relative_path))
            ],
            "bundle_id": safe_bundle_id,
            "repository_snapshot_sha256": repository_snapshot_sha256,
            "schema_version": "his-code-evidence-seal.v1",
        }
        content = json.dumps(
            manifest,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return self.persist(safe_bundle_id, kind="bundle_seal", leaf="seal.json", content=content)

    def _seal_exists(self, bundle_id: int) -> bool:
        bundle_name = f"bundle_{bundle_id}"
        try:
            with self._open_root() as root_fd:
                try:
                    bundle_fd = os.open(bundle_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                except FileNotFoundError:
                    return False
                try:
                    return _entry_exists(bundle_fd, "seal.json")
                finally:
                    os.close(bundle_fd)
        except OSError:
            raise ValueError("code_evidence_artifact_path_invalid") from None

    def _open_root(self):
        class _FdContext:
            def __init__(inner) -> None:
                inner.fd = -1

            def __enter__(inner) -> int:
                inner.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                return inner.fd

            def __exit__(inner, _type, _value, _traceback) -> None:
                os.close(inner.fd)

        return _FdContext()

    def _open_bundle(self, root_fd: int, bundle_name: str) -> int:
        try:
            os.mkdir(bundle_name, 0o700, dir_fd=root_fd)
            os.fsync(root_fd)
        except FileExistsError:
            pass
        try:
            bundle_fd = os.open(bundle_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        except OSError:
            raise ValueError("code_evidence_artifact_path_invalid") from None
        info = os.fstat(bundle_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_nlink < 2
        ):
            os.close(bundle_fd)
            raise ValueError("code_evidence_artifact_path_invalid")
        return bundle_fd

    def _verify_bundle_entry(self, bundle_id: int, bundle_fd: int) -> None:
        name = f"bundle_{_positive_int(bundle_id)}"
        try:
            with self._open_root() as root_fd:
                entry = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            opened = os.fstat(bundle_fd)
        except OSError:
            raise ValueError("code_evidence_artifact_path_changed") from None
        if (
            not stat.S_ISDIR(entry.st_mode)
            or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
            or entry.st_uid != os.getuid()
        ):
            raise ValueError("code_evidence_artifact_path_changed")


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("code_evidence_artifact_input_invalid")
    return value


def _kind(value: object) -> str:
    if not isinstance(value, str) or value not in _KINDS:
        raise ValueError("code_evidence_artifact_input_invalid")
    return value


def _leaf(value: object) -> str:
    if not isinstance(value, str) or _LEAF.fullmatch(value) is None:
        raise ValueError("code_evidence_artifact_input_invalid")
    lowered = value.lower()
    if lowered in _SENSITIVE_NAMES or Path(lowered).suffix in _SENSITIVE_SUFFIXES or lowered == ".git":
        raise ValueError("code_evidence_artifact_input_invalid")
    return value


def _record(value: object) -> EvidenceArtifactRecord:
    if not isinstance(value, EvidenceArtifactRecord):
        raise ValueError("code_evidence_artifact_input_invalid")
    bundle_id = _positive_int(value.bundle_id)
    _kind(value.kind)
    prefix = f"bundle_{bundle_id}/"
    if not value.relative_path.startswith(prefix) or value.relative_path.count("/") != 1:
        raise ValueError("code_evidence_artifact_input_invalid")
    _leaf(value.relative_path.removeprefix(prefix))
    if _SHA256.fullmatch(value.sha256) is None or value.size_bytes < 0 or value.size_bytes > _MAX_ARTIFACT_BYTES:
        raise ValueError("code_evidence_artifact_input_invalid")
    if value.device < 0 or value.inode < 1 or value.mode != 0o600 or value.link_count != 1:
        raise ValueError("code_evidence_artifact_input_invalid")
    return value


def _entry_exists(directory_fd: int, leaf: str) -> bool:
    try:
        os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _validate_regular_file(info: os.stat_result, *, size: int) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size != size
    ):
        raise ValueError("code_evidence_artifact_changed")


def _write_all(file_fd: int, content: bytes) -> None:
    view = memoryview(content)
    offset = 0
    while offset < len(view):
        written = os.write(file_fd, view[offset:])
        if written < 1:
            raise OSError("short write")
        offset += written


def _read_all(file_fd: int, expected: int) -> bytes:
    chunks: list[bytes] = []
    remaining = expected + 1
    while remaining > 0:
        chunk = os.read(file_fd, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
