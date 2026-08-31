from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.change_context_contracts import canonical_json_bytes


_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ChangeContextArtifactRecord:
    content_hash: str
    artifact_ref: str
    relative_path: str
    size_bytes: int
    device: int
    inode: int
    mode: int
    link_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "content_hash": self.content_hash,
            "artifact_ref": self.artifact_ref,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
            "link_count": self.link_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ChangeContextArtifactRecord":
        fields = {"content_hash", "artifact_ref", "relative_path", "size_bytes", "device", "inode", "mode", "link_count"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("change_context_artifact_record_invalid")
        strings = (value["content_hash"], value["artifact_ref"], value["relative_path"])
        numbers = (value["size_bytes"], value["device"], value["inode"], value["mode"], value["link_count"])
        if any(not isinstance(item, str) for item in strings) or any(isinstance(item, bool) or not isinstance(item, int) for item in numbers):
            raise ValueError("change_context_artifact_record_invalid")
        return cls(
            content_hash=str(value["content_hash"]),
            artifact_ref=str(value["artifact_ref"]),
            relative_path=str(value["relative_path"]),
            size_bytes=int(value["size_bytes"]),
            device=int(value["device"]),
            inode=int(value["inode"]),
            mode=int(value["mode"]),
            link_count=int(value["link_count"]),
        )


class ChangeContextArtifactStore:
    """Private, no-follow, immutable content-addressed layer storage."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("change_context_artifact_root_invalid")
        self.root = root
        try:
            root.parent.mkdir(parents=True, exist_ok=True)
            if not root.exists() and not root.is_symlink():
                root.mkdir(mode=0o700)
            info = root.lstat()
            if root.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError
            if stat.S_IMODE(info.st_mode) != 0o700:
                os.chmod(root, 0o700, follow_symlinks=False)
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            os.close(root_fd)
        except (OSError, ValueError):
            raise ValueError("change_context_artifact_root_invalid") from None

    def reference_for_payload(self, payload: Mapping[str, object]) -> str:
        return self.reference_for_bytes(canonical_json_bytes(payload))

    def reference_for_bytes(self, content: bytes) -> str:
        digest = _hash_bytes(content)
        return f"artifact://change-context/sha256/{digest[7:]}"

    def path_for(self, content_hash: str) -> Path:
        digest = _digest(content_hash)
        return self.root / "sha256" / digest[:2] / digest[2:] / "layer.json"

    def persist_layer(self, payload: Mapping[str, object]) -> ChangeContextArtifactRecord:
        return self.persist_bytes(canonical_json_bytes(payload))

    def persist_bytes(self, content: bytes) -> ChangeContextArtifactRecord:
        if not isinstance(content, bytes):
            raise ValueError("change_context_artifact_input_invalid")
        if len(content) > _MAX_ARTIFACT_BYTES:
            raise ValueError("change_context_artifact_limit_exceeded")
        content_hash = _hash_bytes(content)
        target = self.path_for(content_hash)
        _ensure_private_dir(self.root / "sha256")
        _ensure_private_dir(target.parent.parent)
        _ensure_private_dir(target.parent)
        if target.exists() or target.is_symlink() or (target.parent / "seal.json").exists():
            raise ValueError("change_context_artifact_exists")
        _atomic_write(target.parent, "layer.json", content)
        info = target.lstat()
        _validate_info(info, expected_size=len(content))
        seal = json.dumps(
            {"schema_version": "change-context-layer-seal.v1", "content_hash": content_hash, "size_bytes": len(content)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            _atomic_write(target.parent, "seal.json", seal)
        except Exception:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        return ChangeContextArtifactRecord(
            content_hash=content_hash,
            artifact_ref=self.reference_for_bytes(content),
            relative_path=target.relative_to(self.root).as_posix(),
            size_bytes=len(content),
            device=info.st_dev,
            inode=info.st_ino,
            mode=stat.S_IMODE(info.st_mode),
            link_count=info.st_nlink,
        )

    def reopen(self, record: ChangeContextArtifactRecord) -> dict[str, Any]:
        if not isinstance(record, ChangeContextArtifactRecord):
            raise ValueError("change_context_artifact_record_invalid")
        target = self.path_for(record.content_hash)
        if record.relative_path != target.relative_to(self.root).as_posix() or record.artifact_ref != f"artifact://change-context/sha256/{record.content_hash[7:]}":
            raise ValueError("change_context_artifact_record_invalid")
        try:
            file_fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise ValueError("change_context_artifact_missing") from None
        except OSError:
            raise ValueError("change_context_artifact_changed") from None
        try:
            info = os.fstat(file_fd)
            if info.st_nlink != 1:
                raise ValueError("change_context_artifact_link_invalid")
            _validate_info(info, expected_size=record.size_bytes)
            if (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)) != (record.device, record.inode, record.mode):
                raise ValueError("change_context_artifact_changed")
            content = _read_all(file_fd, record.size_bytes)
        finally:
            os.close(file_fd)
        if _hash_bytes(content) != record.content_hash:
            raise ValueError("change_context_artifact_hash_mismatch")
        _verify_seal(target.parent / "seal.json", record)
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("change_context_artifact_changed") from None
        if not isinstance(value, dict) or canonical_json_bytes(value) != content:
            raise ValueError("change_context_artifact_changed")
        return value

    def inspect_verified(self, content_hash: str) -> ChangeContextArtifactRecord:
        target = self.path_for(content_hash)
        try:
            info = target.lstat()
        except FileNotFoundError:
            raise ValueError("change_context_artifact_missing") from None
        if info.st_nlink != 1:
            raise ValueError("change_context_artifact_link_invalid")
        _validate_info(info, expected_size=info.st_size)
        record = ChangeContextArtifactRecord(
            content_hash=content_hash,
            artifact_ref=f"artifact://change-context/sha256/{content_hash[7:]}",
            relative_path=target.relative_to(self.root).as_posix(),
            size_bytes=info.st_size,
            device=info.st_dev,
            inode=info.st_ino,
            mode=stat.S_IMODE(info.st_mode),
            link_count=info.st_nlink,
        )
        self.reopen(record)
        return record


def _ensure_private_dir(path: Path) -> None:
    try:
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.chmod(path, 0o700, follow_symlinks=False)
    except (OSError, ValueError):
        raise ValueError("change_context_artifact_path_invalid") from None


def _atomic_write(directory: Path, leaf: str, content: bytes) -> None:
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".tmp-{secrets.token_hex(16)}"
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        file_fd = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        created = True
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(file_fd, content[offset:])
            os.fsync(file_fd)
            _validate_info(os.fstat(file_fd), expected_size=len(content))
        finally:
            os.close(file_fd)
        os.rename(temporary, leaf, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        created = False
        os.fsync(directory_fd)
    except FileExistsError:
        raise ValueError("change_context_artifact_exists") from None
    finally:
        if created:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass
        os.close(directory_fd)


def _validate_info(info: os.stat_result, *, expected_size: int) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != expected_size:
        raise ValueError("change_context_artifact_changed")


def _verify_seal(path: Path, record: ChangeContextArtifactRecord) -> None:
    try:
        info = path.lstat()
        _validate_info(info, expected_size=info.st_size)
        seal = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("change_context_artifact_missing") from None
    except (OSError, ValueError, json.JSONDecodeError):
        raise ValueError("change_context_artifact_changed") from None
    expected = {"schema_version": "change-context-layer-seal.v1", "content_hash": record.content_hash, "size_bytes": record.size_bytes}
    if seal != expected:
        raise ValueError("change_context_artifact_changed")


def _read_all(file_fd: int, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= expected_size:
        chunk = os.read(file_fd, min(65536, expected_size + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    content = b"".join(chunks)
    if len(content) != expected_size:
        raise ValueError("change_context_artifact_changed")
    return content


def _hash_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _digest(content_hash: str) -> str:
    if not isinstance(content_hash, str) or not content_hash.startswith("sha256:") or len(content_hash) != 71:
        raise ValueError("change_context_artifact_hash_invalid")
    digest = content_hash[7:]
    if any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("change_context_artifact_hash_invalid")
    return digest
