from __future__ import annotations

import hashlib
import difflib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_repository import CodeEvidenceRepository
from app.providers.git import GitProviderAdapter
from app.repository_scope import RepositoryScope
from app.sensitive_text import contains_sensitive_scalar_text, contains_sensitive_text


_GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SENSITIVE_NAMES = frozenset((".env", ".env.local", ".env.production", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"))
_SENSITIVE_SUFFIXES = frozenset((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_CHANGED_PATHS = 2048
_TIMEOUT_SECONDS = 60
_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}


@dataclass(frozen=True)
class _CapturedDiff:
    head_sha: str
    snapshot_sha256: str
    patch: bytes
    manifest: bytes
    changed_paths: tuple[str, ...]
    change_types: tuple[str, ...]
    diff_check_returncode: int


class GitDiffEvidenceService:
    """Capture one complete, immutable local Git diff evidence bundle."""

    def __init__(
        self,
        repository: CodeEvidenceRepository,
        artifact_store: EvidenceArtifactStore,
        scopes: Mapping[str, RepositoryScope],
    ) -> None:
        if not isinstance(repository, CodeEvidenceRepository) or not isinstance(artifact_store, EvidenceArtifactStore):
            raise TypeError("code_evidence_git_configuration_invalid")
        if not isinstance(scopes, Mapping) or not scopes:
            raise ValueError("code_evidence_git_configuration_invalid")
        checked = {
            alias: scope
            for alias, scope in scopes.items()
            if isinstance(alias, str) and isinstance(scope, RepositoryScope) and alias == scope.alias
        }
        if len(checked) != len(scopes):
            raise ValueError("code_evidence_git_configuration_invalid")
        self._repository = repository
        self._artifact_store = artifact_store
        self._scopes = checked
        self._git_adapter = GitProviderAdapter(checked)

    def capture(
        self,
        *,
        repository_alias: str,
        bundle_key: str,
        conversation_key: str,
        task_key: str,
    ) -> dict[str, object]:
        scope = self._scope(repository_alias)
        captured = self._capture_snapshot(scope)
        if self._repository_snapshot(scope) != captured.snapshot_sha256:
            raise ValueError("code_evidence_repository_changed")

        repository_identity = hashlib.sha256(
            json.dumps(
                {
                    "alias": scope.alias,
                    "git": list(scope.git_identity or ()),
                    "root": list(scope.root_identity),
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        bundle = self._repository.create_bundle(
            bundle_key=bundle_key,
            conversation_key=conversation_key,
            task_key=task_key,
            repository_alias=scope.alias,
            repository_identity_sha256=repository_identity,
            head_sha=captured.head_sha,
            snapshot_sha256=captured.snapshot_sha256,
            required_capabilities=("git.diff",),
        )
        bundle_id = int(bundle["id"])
        self._repository.append_event(
            bundle_id,
            event_type="capture_started",
            status="running",
            details={"capability": "git.diff"},
        )
        records = (
            self._artifact_store.persist(bundle_id, kind="diff_patch", leaf="full.patch", content=captured.patch),
            self._artifact_store.persist(bundle_id, kind="diff_manifest", leaf="manifest.json", content=captured.manifest),
        )
        for record in records:
            self._append_artifact(record)
        seal = self._artifact_store.seal(
            bundle_id,
            artifacts=records,
            repository_snapshot_sha256=captured.snapshot_sha256,
        )
        self._append_artifact(seal)
        self._repository.append_event(
            bundle_id,
            event_type="capture_completed",
            status="success",
            details={"capability": "git.diff", "changed_count": len(captured.changed_paths)},
        )
        sealed = self._repository.seal_bundle(bundle_id, seal_sha256=seal.sha256)
        return {
            "bundle_id": bundle_id,
            "bundle_key": sealed["bundle_key"],
            "repository_alias": scope.alias,
            "status": sealed["status"],
            "head_sha": captured.head_sha,
            "snapshot_sha256": captured.snapshot_sha256,
            "patch_sha256": records[0].sha256,
            "seal_sha256": seal.sha256,
            "changed_paths": list(captured.changed_paths),
            "change_types": list(captured.change_types),
            "diff_check_returncode": captured.diff_check_returncode,
            "diff_complete": True,
            "snapshot_consistent": True,
            "external_calls": False,
            "local_mutation": False,
        }

    def _capture_snapshot(self, scope: RepositoryScope) -> _CapturedDiff:
        with self._git_adapter._execution_snapshot(scope) as snapshot:
            head = _head(snapshot)
            raw_changes = _raw_changes(snapshot)
            untracked = _untracked(snapshot)
            files = _file_manifest(snapshot, raw_changes, untracked)
            if len(files) > _MAX_CHANGED_PATHS:
                raise ValueError("code_evidence_limit_exceeded")
            _reject_sensitive_files(snapshot, files)
            tracked_patch = _run_git(snapshot, ("diff", "--no-ext-diff", "--no-textconv", "--binary", "--full-index", "--find-renames", "HEAD", "--"), allow_codes=(0,))
            check = _run_git(snapshot, ("diff", "--no-ext-diff", "--no-textconv", "--check", "HEAD", "--"), allow_codes=(0,))
            patch_parts = [tracked_patch]
            for path in untracked:
                _run_git(snapshot, ("diff", "--no-ext-diff", "--no-textconv", "--no-index", "--check", "--", "/dev/null", path), allow_codes=(0, 1))
                patch_parts.append(
                    _run_git(
                        snapshot,
                        ("diff", "--no-ext-diff", "--no-textconv", "--no-index", "--binary", "--full-index", "--", "/dev/null", path),
                        allow_codes=(0, 1),
                    )
                )
            patch = b"".join(patch_parts)
            if len(patch) > _MAX_OUTPUT_BYTES:
                raise ValueError("code_evidence_limit_exceeded")
            snapshot_sha = _snapshot_digest(snapshot)
            manifest_value = {
                "change_types": sorted({value for item in files for value in item["change_types"]}),
                "diff_check_returncode": 0,
                "files": files,
                "head_sha": head,
                "patch_sha256": hashlib.sha256(patch).hexdigest(),
                "repository_alias": scope.alias,
                "schema_version": "his-git-diff-evidence.v1",
                "snapshot_sha256": snapshot_sha,
            }
            manifest = json.dumps(
                manifest_value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            return _CapturedDiff(
                head_sha=head,
                snapshot_sha256=snapshot_sha,
                patch=patch,
                manifest=manifest,
                changed_paths=tuple(item["path"] for item in files),
                change_types=tuple(manifest_value["change_types"]),
                diff_check_returncode=0 if check is not None else 1,
            )

    def _repository_snapshot(self, scope: RepositoryScope) -> str:
        with self._git_adapter._execution_snapshot(scope) as snapshot:
            return _snapshot_digest(snapshot)

    def _append_artifact(self, record: EvidenceArtifactRecord) -> None:
        self._repository.append_artifact(
            record.bundle_id,
            kind=record.kind,
            relative_path=record.relative_path,
            sha256=record.sha256,
            size_bytes=record.size_bytes,
            device=record.device,
            inode=record.inode,
            mode=record.mode,
            link_count=record.link_count,
        )

    def _scope(self, alias: object) -> RepositoryScope:
        if not isinstance(alias, str) or alias not in self._scopes:
            raise ValueError("code_evidence_repository_not_allowed")
        scope = self._scopes[alias]
        scope.assert_identity()
        return scope


def _head(snapshot: Path) -> str:
    value = _run_git(snapshot, ("rev-parse", "--verify", "HEAD"), allow_codes=(0,)).decode("ascii", "strict").strip()
    if _GIT_SHA.fullmatch(value) is None:
        raise ValueError("code_evidence_git_invalid")
    return value


def _raw_changes(snapshot: Path) -> tuple[dict[str, object], ...]:
    raw = _run_git(
        snapshot,
        ("diff", "--no-ext-diff", "--no-textconv", "--raw", "-z", "--full-index", "--find-renames", "HEAD", "--"),
        allow_codes=(0,),
    )
    values = raw.split(b"\0")
    index = 0
    result: list[dict[str, object]] = []
    while index < len(values):
        header = values[index]
        index += 1
        if not header:
            continue
        try:
            parts = header.decode("ascii", "strict").split(" ")
            if len(parts) != 5 or not parts[0].startswith(":"):
                raise ValueError
            old_mode, new_mode, status_value = parts[0][1:], parts[1], parts[4]
            if index >= len(values) or not values[index]:
                raise ValueError
            first = _path(values[index])
            index += 1
            second = ""
            if status_value[:1] in {"R", "C"}:
                if index >= len(values) or not values[index]:
                    raise ValueError
                second = _path(values[index])
                index += 1
        except (UnicodeDecodeError, ValueError):
            raise ValueError("code_evidence_git_invalid") from None
        result.append({"old_mode": old_mode, "new_mode": new_mode, "status": status_value, "old_path": first, "new_path": second or first})
    return tuple(result)


def _untracked(snapshot: Path) -> tuple[str, ...]:
    raw = _run_git(snapshot, ("ls-files", "--others", "--exclude-standard", "-z"), allow_codes=(0,))
    values = tuple(sorted(_path(item) for item in raw.split(b"\0") if item))
    if len(values) != len(set(values)):
        raise ValueError("code_evidence_git_invalid")
    return values


def _file_manifest(snapshot: Path, changes: Sequence[dict[str, object]], untracked: Sequence[str]) -> list[dict[str, object]]:
    entries: dict[str, dict[str, object]] = {}
    for change in changes:
        old_path, new_path = str(change["old_path"]), str(change["new_path"])
        status_value = str(change["status"])
        old_mode, new_mode = str(change["old_mode"]), str(change["new_mode"])
        if status_value.startswith("R"):
            _merge_entry(entries, snapshot, old_path, ("deleted", "renamed"), before_path=old_path, after_path=None)
            _merge_entry(entries, snapshot, new_path, ("added", "renamed"), before_path=None, after_path=new_path)
        elif status_value.startswith("A"):
            _merge_entry(entries, snapshot, new_path, ("added",), before_path=None, after_path=new_path)
        elif status_value.startswith("D"):
            _merge_entry(entries, snapshot, old_path, ("deleted",), before_path=old_path, after_path=None)
        else:
            _merge_entry(entries, snapshot, new_path, ("modified",), before_path=old_path, after_path=new_path)
        if old_mode != new_mode:
            entries[new_path if new_mode != "000000" else old_path]["change_types"].add("mode_changed")
    for path in untracked:
        _merge_entry(entries, snapshot, path, ("added",), before_path=None, after_path=path)
    result: list[dict[str, object]] = []
    for path in sorted(entries):
        item = entries[path]
        item["change_types"] = sorted(item["change_types"])
        result.append(item)
    return result


def _merge_entry(
    entries: dict[str, dict[str, object]],
    snapshot: Path,
    path: str,
    change_types: Sequence[str],
    *,
    before_path: str | None,
    after_path: str | None,
) -> None:
    before = _git_blob(snapshot, before_path) if before_path else None
    after = _read_snapshot_file(snapshot, after_path) if after_path else None
    types = set(change_types)
    is_binary = (before is not None and _binary(before)) or (after is not None and _binary(after))
    if is_binary:
        types.add("binary")
    additions, deletions = _line_counts(before, after) if not is_binary else (None, None)
    entries[path] = {
        "additions": additions,
        "after_sha256": hashlib.sha256(after).hexdigest() if after is not None else "",
        "after_size": len(after) if after is not None else 0,
        "before_sha256": hashlib.sha256(before).hexdigest() if before is not None else "",
        "before_size": len(before) if before is not None else 0,
        "change_types": types,
        "deletions": deletions,
        "path": path,
    }


def _line_counts(before: bytes | None, after: bytes | None) -> tuple[int, int]:
    before_lines = (before or b"").splitlines()
    after_lines = (after or b"").splitlines()
    additions = 0
    deletions = 0
    for tag, before_start, before_end, after_start, after_end in difflib.SequenceMatcher(
        a=before_lines, b=after_lines, autojunk=False
    ).get_opcodes():
        if tag in {"replace", "delete"}:
            deletions += before_end - before_start
        if tag in {"replace", "insert"}:
            additions += after_end - after_start
    return additions, deletions


def _git_blob(snapshot: Path, path: str) -> bytes | None:
    result = _run_git(snapshot, ("show", f"HEAD:{path}"), allow_codes=(0, 128), maximum=_MAX_OUTPUT_BYTES)
    return None if result == b"" and not _tracked_in_head(snapshot, path) else result


def _tracked_in_head(snapshot: Path, path: str) -> bool:
    result = _run_git(snapshot, ("ls-tree", "-z", "HEAD", "--", path), allow_codes=(0,))
    return bool(result)


def _read_snapshot_file(snapshot: Path, path: str) -> bytes | None:
    candidate = snapshot / path
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_OUTPUT_BYTES:
        raise ValueError("code_evidence_git_invalid")
    content = candidate.read_bytes()
    after = candidate.lstat()
    if (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) != (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ):
        raise ValueError("code_evidence_git_invalid")
    return content


def _snapshot_digest(snapshot: Path) -> str:
    head = _head(snapshot)
    status = _run_git(snapshot, ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-ahead-behind"), allow_codes=(0,))
    files: list[dict[str, object]] = []
    total = 0
    for current, directories, names in os.walk(snapshot, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(name for name in directories if current_path != snapshot or name != ".git")
        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(snapshot).as_posix()
            if relative == ".git" or relative.startswith(".git/"):
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError("code_evidence_git_invalid")
            total += info.st_size
            if total > _MAX_OUTPUT_BYTES * 4:
                raise ValueError("code_evidence_limit_exceeded")
            content = path.read_bytes()
            files.append({"mode": stat.S_IMODE(info.st_mode), "path": relative, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)})
    payload = json.dumps(
        {"files": files, "head": head, "status_sha256": hashlib.sha256(status).hexdigest()},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _reject_sensitive_files(snapshot: Path, files: Sequence[Mapping[str, object]]) -> None:
    for item in files:
        path = str(item["path"])
        leaf = Path(path).name.lower()
        if leaf in _SENSITIVE_NAMES or Path(leaf).suffix in _SENSITIVE_SUFFIXES:
            raise ValueError("code_evidence_sensitive")
        for content in (_git_blob(snapshot, path), _read_snapshot_file(snapshot, path)):
            if content is None:
                continue
            text = content.decode("utf-8", "ignore")
            if contains_sensitive_text(text) or contains_sensitive_scalar_text(text):
                raise ValueError("code_evidence_sensitive")


def _path(value: bytes) -> str:
    try:
        path = value.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise ValueError("code_evidence_git_invalid") from None
    candidate = Path(path)
    if not path or "\\" in path or candidate.is_absolute() or any(part in {"", ".", "..", ".git"} for part in candidate.parts):
        raise ValueError("code_evidence_git_invalid")
    return candidate.as_posix()


def _binary(content: bytes) -> bool:
    return b"\0" in content


def _run_git(
    snapshot: Path,
    arguments: Sequence[str],
    *,
    allow_codes: tuple[int, ...],
    maximum: int = _MAX_OUTPUT_BYTES,
) -> bytes:
    command = [
        "/usr/bin/git",
        "--no-replace-objects",
        "-c", "credential.helper=",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "diff.external=false",
        "-c", "core.attributesFile=/dev/null",
        "-c", "protocol.file.allow=never",
        "-c", "protocol.ext.allow=never",
        *arguments,
    ]
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    output = bytearray()
    try:
        process = subprocess.Popen(
            command,
            cwd=snapshot,
            env=dict(_ENV),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert process.stdout is not None
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + _TIMEOUT_SECONDS
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("code_evidence_git_timeout")
            for key, _events in selector.select(remaining):
                block = os.read(key.fileobj.fileno(), 8192)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(block)
                if len(output) > maximum:
                    raise ValueError("code_evidence_limit_exceeded")
        returncode = process.wait(timeout=2)
        if returncode not in allow_codes:
            raise ValueError("code_evidence_git_invalid")
        return bytes(output)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("code_evidence_git_invalid") from None
    finally:
        selector.close()
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
            if process.stdout is not None:
                process.stdout.close()
