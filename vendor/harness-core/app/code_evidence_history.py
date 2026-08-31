from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_git import _head, _read_snapshot_file, _run_git, _snapshot_digest
from app.code_evidence_repository import CodeEvidenceRepository
from app.providers.git import GitProviderAdapter
from app.repository_scope import RepositoryScope
from app.sensitive_text import contains_sensitive_scalar_text, contains_sensitive_text


_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class GitHistoryEvidenceService:
    def __init__(self, repository: CodeEvidenceRepository, store: EvidenceArtifactStore, scopes: Mapping[str, RepositoryScope]) -> None:
        self._repository, self._store = repository, store
        self._scopes = {key: value for key, value in scopes.items() if isinstance(key, str) and isinstance(value, RepositoryScope) and key == value.alias}
        if not self._scopes or len(self._scopes) != len(scopes):
            raise ValueError("code_evidence_history_configuration_invalid")
        self._adapter = GitProviderAdapter(self._scopes)

    def capture(self, *, repository_alias: str, path: str, limit: int, bundle_key: str, conversation_key: str, task_key: str, ref: str = "HEAD") -> dict[str, object]:
        scope = self._scope(repository_alias)
        safe_path = scope.relative_path(path)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 32:
            raise ValueError("code_evidence_history_invalid")
        if ref != "HEAD" and (not isinstance(ref, str) or _SHA.fullmatch(ref) is None):
            raise ValueError("code_evidence_history_invalid")
        with self._adapter._execution_snapshot(scope) as snapshot:
            current_head, snapshot_sha = _head(snapshot), _snapshot_digest(snapshot)
            revision = current_head if ref == "HEAD" else ref
            current = _read_snapshot_file(snapshot, safe_path)
            if current is not None:
                _reject_sensitive(current)
            raw_log = _run_git(snapshot, ("log", "--no-ext-diff", "--no-textconv", f"--max-count={limit}", "--format=%H%x00%an%x00%aI%x00%s%x00", revision, "--", safe_path), allow_codes=(0,))
            commits = _commits(raw_log)
            patch = _run_git(snapshot, ("show", "--no-ext-diff", "--no-textconv", "--format=", "--binary", "--full-index", revision, "--", safe_path), allow_codes=(0,))
            blame_raw = _run_git(snapshot, ("blame", "--line-porcelain", revision, "--", safe_path), allow_codes=(0,))
            _reject_sensitive(patch)
            _reject_sensitive(blame_raw)
            blame = _blame(blame_raw)
        if self._snapshot(scope) != snapshot_sha:
            raise ValueError("code_evidence_repository_changed")
        identity = hashlib.sha256(json.dumps({"git": scope.git_identity, "root": scope.root_identity}, sort_keys=True).encode()).hexdigest()
        bundle_id = int(self._repository.create_bundle(bundle_key=bundle_key, conversation_key=conversation_key, task_key=task_key, repository_alias=scope.alias, repository_identity_sha256=identity, head_sha=current_head, snapshot_sha256=snapshot_sha, required_capabilities=("git.history",))["id"])
        history = self._store.persist(bundle_id, kind="history", leaf="history.json", content=_json_bytes({"blame": blame, "commits": commits, "path": safe_path, "ref": revision, "schema_version": "his-git-history.v1"}))
        patch_record = self._store.persist(bundle_id, kind="diff_patch", leaf="history.patch", content=patch)
        manifest = self._store.persist(bundle_id, kind="history_manifest", leaf="index.json", content=_json_bytes({"commit_count": len(commits), "history_sha256": history.sha256, "patch_sha256": patch_record.sha256, "snapshot_sha256": snapshot_sha}))
        seal = self._store.seal(bundle_id, artifacts=(history, patch_record, manifest), repository_snapshot_sha256=snapshot_sha)
        self._register((history, patch_record, manifest, seal))
        self._repository.append_event(bundle_id, event_type="history_completed", status="success", details={"commit_count": len(commits)})
        sealed = self._repository.seal_bundle(bundle_id, seal_sha256=seal.sha256)
        return {"bundle_id": bundle_id, "commit_count": len(commits), "snapshot_consistent": True, "status": sealed["status"], "seal_sha256": seal.sha256}

    def _scope(self, alias: object) -> RepositoryScope:
        if not isinstance(alias, str) or alias not in self._scopes:
            raise ValueError("code_evidence_repository_not_allowed")
        return self._scopes[alias]

    def _snapshot(self, scope: RepositoryScope) -> str:
        with self._adapter._execution_snapshot(scope) as snapshot:
            return _snapshot_digest(snapshot)

    def _register(self, records: Sequence[EvidenceArtifactRecord]) -> None:
        for record in records:
            self._repository.append_artifact(record.bundle_id, kind=record.kind, relative_path=record.relative_path, sha256=record.sha256, size_bytes=record.size_bytes, device=record.device, inode=record.inode, mode=record.mode, link_count=record.link_count)


def _commits(raw: bytes) -> list[dict[str, str]]:
    fields = raw.split(b"\0")
    result: list[dict[str, str]] = []
    index = 0
    while index + 3 < len(fields):
        if not fields[index]:
            index += 1
            continue
        try:
            sha, author, authored_at, subject = (fields[index + offset].decode("utf-8", "strict").strip() for offset in range(4))
        except UnicodeDecodeError:
            raise ValueError("code_evidence_history_invalid") from None
        if _SHA.fullmatch(sha) is None or not author or not authored_at or not subject:
            raise ValueError("code_evidence_history_invalid")
        for text in (author, authored_at, subject):
            if contains_sensitive_text(text) or contains_sensitive_scalar_text(text):
                raise ValueError("code_evidence_sensitive")
        result.append({"author": author, "authored_at": authored_at, "sha": sha, "subject": subject})
        index += 4
    return result


def _blame(raw: bytes) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    current_sha = ""
    line = 0
    for raw_line in raw.splitlines():
        if raw_line.startswith(b"\t"):
            content = raw_line[1:].decode("utf-8", "strict")
            result.append({"commit_sha": current_sha, "content": content, "line": line})
            continue
        parts = raw_line.split(b" ")
        if len(parts) >= 3 and _SHA.fullmatch(parts[0].decode("ascii", "ignore")):
            current_sha = parts[0].decode("ascii")
            line = int(parts[2])
    return result


def _reject_sensitive(content: bytes) -> None:
    text = content.decode("utf-8", "ignore")
    if contains_sensitive_text(text) or contains_sensitive_scalar_text(text):
        raise ValueError("code_evidence_sensitive")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
