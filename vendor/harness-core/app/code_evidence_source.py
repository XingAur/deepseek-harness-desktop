from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Mapping, Sequence

from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_git import _head, _read_snapshot_file, _snapshot_digest
from app.code_evidence_repository import CodeEvidenceRepository
from app.providers.git import GitProviderAdapter
from app.repository_scope import RepositoryScope
from app.sensitive_text import contains_sensitive_scalar_text, contains_sensitive_text


_MAX_FILES = 64
_MAX_SOURCE_BYTES = 512 * 1024
_MAX_SEARCH_FILES = 4096
_SENSITIVE_NAMES = frozenset((".env", ".env.local", ".env.production", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"))
_SENSITIVE_SUFFIXES = frozenset((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))


class SourceEvidenceService:
    def __init__(self, repository: CodeEvidenceRepository, store: EvidenceArtifactStore, scopes: Mapping[str, RepositoryScope]) -> None:
        if not isinstance(repository, CodeEvidenceRepository) or not isinstance(store, EvidenceArtifactStore):
            raise TypeError("code_evidence_source_configuration_invalid")
        if not isinstance(scopes, Mapping) or not scopes:
            raise ValueError("code_evidence_source_configuration_invalid")
        self._scopes = {key: value for key, value in scopes.items() if isinstance(key, str) and isinstance(value, RepositoryScope) and key == value.alias}
        if len(self._scopes) != len(scopes):
            raise ValueError("code_evidence_source_configuration_invalid")
        self._repository, self._store = repository, store
        self._adapter = GitProviderAdapter(self._scopes)

    def read(self, *, repository_alias: str, paths: Sequence[str], bundle_key: str, conversation_key: str, task_key: str) -> dict[str, object]:
        scope = self._scope(repository_alias)
        safe_paths = _paths(scope, paths)
        with self._adapter._execution_snapshot(scope) as snapshot:
            head = _head(snapshot)
            snapshot_sha = _snapshot_digest(snapshot)
            captured: list[tuple[str, bytes, int]] = []
            for path in safe_paths:
                content = _source_file(snapshot, path)
                captured.append((path, content, stat.S_IMODE((snapshot / path).lstat().st_mode)))
        if self._snapshot(scope) != snapshot_sha:
            raise ValueError("code_evidence_repository_changed")

        bundle_id = self._create_bundle(bundle_key, conversation_key, task_key, scope, head, snapshot_sha, "source.read")
        records: list[EvidenceArtifactRecord] = []
        files: list[dict[str, object]] = []
        for index, (path, content, mode) in enumerate(captured, 1):
            record = self._store.persist(bundle_id, kind="source", leaf=f"source-{index:03d}.txt", content=content)
            records.append(record)
            files.append({
                "artifact_leaf": Path(record.relative_path).name,
                "line_count": len(content.decode("utf-8").splitlines()),
                "mode": mode,
                "path": path,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            })
        manifest = _json_bytes({"files": files, "head_sha": head, "repository_alias": scope.alias, "schema_version": "his-source-evidence.v1", "snapshot_sha256": snapshot_sha})
        records.append(self._store.persist(bundle_id, kind="source_manifest", leaf="sources.json", content=manifest))
        seal = self._store.seal(bundle_id, artifacts=records, repository_snapshot_sha256=snapshot_sha)
        records.append(seal)
        self._register(records)
        self._repository.append_event(bundle_id, event_type="source_read_completed", status="success", details={"file_count": len(files)})
        sealed = self._repository.seal_bundle(bundle_id, seal_sha256=seal.sha256)
        return {"bundle_id": bundle_id, "paths": list(safe_paths), "snapshot_consistent": True, "status": sealed["status"], "seal_sha256": seal.sha256}

    def search(self, *, repository_alias: str, pattern: str, path_prefix: str, bundle_key: str, conversation_key: str, task_key: str, max_matches: int = 128) -> dict[str, object]:
        scope = self._scope(repository_alias)
        if not isinstance(pattern, str) or not pattern or len(pattern.encode("utf-8")) > 128 or contains_sensitive_text(pattern) or contains_sensitive_scalar_text(pattern):
            raise ValueError("code_evidence_search_invalid")
        if not isinstance(max_matches, int) or isinstance(max_matches, bool) or not 1 <= max_matches <= 512:
            raise ValueError("code_evidence_search_invalid")
        prefix = "." if path_prefix == "." else scope.relative_path(path_prefix)
        with self._adapter._execution_snapshot(scope) as snapshot:
            head, snapshot_sha = _head(snapshot), _snapshot_digest(snapshot)
            matches: list[dict[str, object]] = []
            seen_files = 0
            root = snapshot / prefix
            if not root.is_dir() or root.is_symlink():
                raise ValueError("code_evidence_search_invalid")
            for current, directories, names in os.walk(root, followlinks=False):
                directories[:] = sorted(directories)
                for name in sorted(names):
                    seen_files += 1
                    if seen_files > _MAX_SEARCH_FILES:
                        raise ValueError("code_evidence_search_incomplete")
                    path = (Path(current) / name).relative_to(snapshot).as_posix()
                    content = _source_file(snapshot, path)
                    digest = hashlib.sha256(content).hexdigest()
                    for line_no, line in enumerate(content.decode("utf-8").splitlines(), 1):
                        if pattern not in line:
                            continue
                        matches.append({"content": line[:512], "file_sha256": digest, "line": line_no, "path": path})
                        if len(matches) > max_matches:
                            raise ValueError("code_evidence_search_incomplete")
        if self._snapshot(scope) != snapshot_sha:
            raise ValueError("code_evidence_repository_changed")
        bundle_id = self._create_bundle(bundle_key, conversation_key, task_key, scope, head, snapshot_sha, "source.search")
        record = self._store.persist(bundle_id, kind="search_manifest", leaf="search.json", content=_json_bytes({"complete": True, "matches": matches, "pattern_sha256": hashlib.sha256(pattern.encode()).hexdigest(), "schema_version": "his-source-search.v1"}))
        seal = self._store.seal(bundle_id, artifacts=(record,), repository_snapshot_sha256=snapshot_sha)
        self._register((record, seal))
        self._repository.append_event(bundle_id, event_type="source_search_completed", status="success", details={"match_count": len(matches)})
        sealed = self._repository.seal_bundle(bundle_id, seal_sha256=seal.sha256)
        return {
            "bundle_id": bundle_id,
            "match_count": len(matches),
            "matched_paths": list(dict.fromkeys(str(item["path"]) for item in matches)),
            "search_complete": True,
            "snapshot_consistent": True,
            "status": sealed["status"],
            "seal_sha256": seal.sha256,
        }

    def _scope(self, alias: object) -> RepositoryScope:
        if not isinstance(alias, str) or alias not in self._scopes:
            raise ValueError("code_evidence_repository_not_allowed")
        return self._scopes[alias]

    def _snapshot(self, scope: RepositoryScope) -> str:
        with self._adapter._execution_snapshot(scope) as snapshot:
            return _snapshot_digest(snapshot)

    def _create_bundle(self, bundle_key: str, conversation_key: str, task_key: str, scope: RepositoryScope, head: str, snapshot_sha: str, capability: str) -> int:
        identity = hashlib.sha256(json.dumps({"git": scope.git_identity, "root": scope.root_identity}, sort_keys=True).encode()).hexdigest()
        return int(self._repository.create_bundle(bundle_key=bundle_key, conversation_key=conversation_key, task_key=task_key, repository_alias=scope.alias, repository_identity_sha256=identity, head_sha=head, snapshot_sha256=snapshot_sha, required_capabilities=(capability,))["id"])

    def _register(self, records: Sequence[EvidenceArtifactRecord]) -> None:
        for record in records:
            self._repository.append_artifact(record.bundle_id, kind=record.kind, relative_path=record.relative_path, sha256=record.sha256, size_bytes=record.size_bytes, device=record.device, inode=record.inode, mode=record.mode, link_count=record.link_count)


def _paths(scope: RepositoryScope, values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence) or not values or len(values) > _MAX_FILES:
        raise ValueError("code_evidence_source_invalid")
    result = tuple(scope.relative_path(item) for item in values)
    if len(result) != len(set(result)):
        raise ValueError("code_evidence_source_invalid")
    return result


def _source_file(snapshot: Path, path: str) -> bytes:
    leaf = Path(path).name.lower()
    if leaf in _SENSITIVE_NAMES or Path(leaf).suffix in _SENSITIVE_SUFFIXES:
        raise ValueError("code_evidence_sensitive")
    content = _read_snapshot_file(snapshot, path)
    if content is None or len(content) > _MAX_SOURCE_BYTES:
        raise ValueError("code_evidence_source_invalid")
    if b"\0" in content:
        raise ValueError("code_evidence_source_binary")
    try:
        text = content.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise ValueError("code_evidence_source_binary") from None
    if contains_sensitive_text(text) or contains_sensitive_scalar_text(text):
        raise ValueError("code_evidence_sensitive")
    return content


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
