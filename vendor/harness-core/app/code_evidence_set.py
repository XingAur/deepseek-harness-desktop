from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_git import _snapshot_digest
from app.code_evidence_repository import CodeEvidenceRepository
from app.providers.git import GitProviderAdapter
from app.repository_scope import RepositoryScope


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_MEMBER_FIELDS = frozenset((
    "repository_alias", "review_bundle_id", "review_bundle_sha256",
    "repository_snapshot_sha256", "verdict",
))


def canonical_evidence_set_manifest(members: Sequence[Mapping[str, object]]) -> bytes:
    if isinstance(members, (str, bytes, bytearray)) or not isinstance(members, Sequence) or not members or len(members) > 16:
        raise ValueError("code_evidence_set_invalid")
    normalized: list[dict[str, object]] = []
    for member in members:
        if not isinstance(member, Mapping) or set(member) != _MEMBER_FIELDS:
            raise ValueError("code_evidence_set_invalid")
        alias = member["repository_alias"]
        bundle_id = member["review_bundle_id"]
        bundle_sha = member["review_bundle_sha256"]
        snapshot_sha = member["repository_snapshot_sha256"]
        if (
            not isinstance(alias, str) or _ALIAS.fullmatch(alias) is None
            or not isinstance(bundle_id, int) or isinstance(bundle_id, bool) or bundle_id <= 0
            or not isinstance(bundle_sha, str) or _SHA256.fullmatch(bundle_sha) is None
            or not isinstance(snapshot_sha, str) or _SHA256.fullmatch(snapshot_sha) is None
            or member["verdict"] != "approved"
        ):
            raise ValueError("code_evidence_set_invalid")
        normalized.append({key: member[key] for key in sorted(_MEMBER_FIELDS)})
    normalized.sort(key=lambda value: str(value["repository_alias"]))
    aliases = [str(item["repository_alias"]) for item in normalized]
    if len(aliases) != len(set(aliases)):
        raise ValueError("code_evidence_set_invalid")
    return json.dumps(
        {"members": normalized, "schema_version": "his-code-evidence-set.v1"},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


class CodeEvidenceSetService:
    """Seal sequential repository reviews and revalidate every child as one set."""

    def __init__(
        self,
        repository: CodeEvidenceRepository,
        artifact_store: EvidenceArtifactStore,
        scopes: Mapping[str, RepositoryScope],
    ) -> None:
        if not isinstance(repository, CodeEvidenceRepository) or not isinstance(artifact_store, EvidenceArtifactStore):
            raise TypeError("code_evidence_set_configuration_invalid")
        self._repository = repository
        self._store = artifact_store
        self._scopes = dict(scopes)
        if not self._scopes or any(alias != scope.alias for alias, scope in self._scopes.items()):
            raise ValueError("code_evidence_set_configuration_invalid")
        self._adapter = GitProviderAdapter(self._scopes)

    def create(
        self,
        *,
        set_key: str,
        conversation_key: str,
        review_bundle_ids: Sequence[int],
    ) -> dict[str, object]:
        members = [self._review_member(bundle_id) for bundle_id in review_bundle_ids]
        manifest = canonical_evidence_set_manifest(members)
        evidence_set = self._repository.create_evidence_set(
            set_key=set_key,
            conversation_key=conversation_key,
            required_repository_count=len(members),
        )
        evidence_set_id = int(evidence_set["id"])
        ordered = sorted(members, key=lambda value: str(value["repository_alias"]))
        for ordinal, member in enumerate(ordered, 1):
            self._repository.append_set_member(
                evidence_set_id,
                repository_alias=str(member["repository_alias"]),
                bundle_id=int(member["review_bundle_id"]),
                ordinal=ordinal,
            )
        # Re-open and re-snapshot after every member has been recorded.
        final_manifest = canonical_evidence_set_manifest(
            [self._review_member(int(item["review_bundle_id"])) for item in ordered]
        )
        if final_manifest != manifest:
            raise ValueError("code_evidence_set_changed")
        seal = hashlib.sha256(manifest).hexdigest()
        sealed = self._repository.seal_evidence_set(evidence_set_id, seal_sha256=seal)
        return {
            "evidence_set_id": evidence_set_id,
            "repository_count": len(ordered),
            "seal_sha256": seal,
            "status": sealed["status"],
            "members": ordered,
            "snapshot_consistent": True,
        }

    def validate(self, evidence_set_id: int) -> dict[str, object]:
        evidence_set = self._repository.get_evidence_set(evidence_set_id)
        if evidence_set["status"] != "sealed":
            raise ValueError("code_evidence_set_invalid")
        members = [
            self._review_member(int(item["bundle_id"]))
            for item in evidence_set["members"]
        ]
        seal = hashlib.sha256(canonical_evidence_set_manifest(members)).hexdigest()
        if seal != evidence_set["seal_sha256"]:
            raise ValueError("code_evidence_set_changed")
        return {"evidence_set_id": evidence_set_id, "status": "sealed", "seal_sha256": seal, "snapshot_consistent": True}

    def _review_member(self, bundle_id: object) -> dict[str, object]:
        if not isinstance(bundle_id, int) or isinstance(bundle_id, bool) or bundle_id <= 0:
            raise ValueError("code_evidence_set_invalid")
        bundle = self._repository.get_bundle(bundle_id)
        alias = str(bundle["repository_alias"])
        scope = self._scopes.get(alias)
        if (
            bundle["status"] != "reviewed"
            or bundle["required_capabilities"] != ["code.review-local"]
            or scope is None
            or not isinstance(bundle.get("review"), Mapping)
            or bundle["review"].get("verdict") != "approved"
        ):
            raise ValueError("code_evidence_set_invalid")
        kinds = {str(item["kind"]) for item in bundle["artifacts"]}
        if kinds != {"review", "review_seal", "bundle_seal"}:
            raise ValueError("code_evidence_set_invalid")
        for item in bundle["artifacts"]:
            self._store.reopen(_record(item))
        with self._adapter._execution_snapshot(scope) as snapshot:
            current = _snapshot_digest(snapshot)
        if current != bundle["snapshot_sha256"]:
            raise ValueError("code_evidence_set_changed")
        return {
            "repository_alias": alias,
            "review_bundle_id": bundle_id,
            "review_bundle_sha256": str(bundle["seal_sha256"]),
            "repository_snapshot_sha256": str(bundle["snapshot_sha256"]),
            "verdict": "approved",
        }


def _record(value: Mapping[str, object]) -> EvidenceArtifactRecord:
    return EvidenceArtifactRecord(
        bundle_id=int(value["bundle_id"]), kind=str(value["kind"]),
        relative_path=str(value["relative_path"]), sha256=str(value["sha256"]),
        size_bytes=int(value["size_bytes"]), device=int(value["device"]),
        inode=int(value["inode"]), mode=int(value["mode"]),
        link_count=int(value["link_count"]),
    )
