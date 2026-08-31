from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_git import _snapshot_digest
from app.code_evidence_repository import CodeEvidenceRepository
from app.local_agent_contract import _validate_unittest_command
from app.providers.git import GitProviderAdapter
from app.repository_scope import RepositoryScope
from app.worktree_executor import (
    SafeGitBoundary,
    capture_local_agent_tree_snapshot,
    run_local_agent_verification_argv,
)


_SHA256_EMPTY = hashlib.sha256(b"").hexdigest()
_MAX_COMMANDS = 8
_MAX_TIMEOUT_SECONDS = 600
_RECEIPT_FIELDS = frozenset(
    (
        "commands",
        "input_bundle_id",
        "input_bundle_seal_sha256",
        "patch_sha256",
        "repository_alias",
        "repository_snapshot_sha256",
        "schema_version",
        "verification_status",
        "workspace_after_sha256",
        "workspace_before_sha256",
    )
)
_RESULT_FIELDS = frozenset(
    ("cleanup", "duration_ms", "returncode", "stderr_sha256", "stdout_sha256", "timed_out")
)
_PERSISTED_RESULT_FIELDS = _RESULT_FIELDS | {"argv_sha256"}


class CodeEvidenceVerificationService:
    """Replay a sealed diff in a private snapshot and run fixed local checks."""

    def __init__(
        self,
        repository: CodeEvidenceRepository,
        artifact_store: EvidenceArtifactStore,
        scopes: Mapping[str, RepositoryScope],
        *,
        command_runner: Callable[..., Mapping[str, object]] = run_local_agent_verification_argv,
    ) -> None:
        if (
            not isinstance(repository, CodeEvidenceRepository)
            or not isinstance(artifact_store, EvidenceArtifactStore)
            or not isinstance(scopes, Mapping)
            or not scopes
            or not callable(command_runner)
        ):
            raise TypeError("code_evidence_verification_configuration_invalid")
        checked = {
            alias: scope
            for alias, scope in scopes.items()
            if isinstance(alias, str) and isinstance(scope, RepositoryScope) and alias == scope.alias
        }
        if len(checked) != len(scopes):
            raise ValueError("code_evidence_verification_configuration_invalid")
        self._repository = repository
        self._artifact_store = artifact_store
        self._scopes = checked
        self._adapter = GitProviderAdapter(checked)
        self._command_runner = command_runner

    def verify(
        self,
        *,
        diff_bundle_id: int,
        bundle_key: str,
        conversation_key: str,
        task_key: str,
        commands: Sequence[tuple[str, ...]],
        timeout_seconds: int,
    ) -> dict[str, object]:
        safe_commands = _commands(commands)
        safe_timeout = _timeout(timeout_seconds)
        input_bundle, artifacts, patch, manifest = self._load_diff_bundle(diff_bundle_id)
        repository_alias = str(input_bundle["repository_alias"])
        scope = self._scopes.get(repository_alias)
        if scope is None:
            raise ValueError("code_evidence_repository_not_allowed")
        scope.assert_identity()
        expected_snapshot = str(input_bundle["snapshot_sha256"])
        source_before = _source_snapshot(self._adapter, scope)
        if source_before != expected_snapshot:
            raise ValueError("code_evidence_repository_changed")

        with self._adapter._execution_snapshot(scope) as workspace:
            boundary = SafeGitBoundary(workspace)
            _git_ok(boundary, ["reset", "--hard", "HEAD"], workspace)
            _git_ok(boundary, ["clean", "-fdx"], workspace)
            _git_ok(boundary, ["apply", "--check", "--binary", "--whitespace=nowarn", "-"], workspace, patch)
            _git_ok(boundary, ["apply", "--binary", "--whitespace=nowarn", "-"], workspace, patch)
            workspace_before = capture_local_agent_tree_snapshot(workspace)
            workspace_before_sha = _tree_sha(workspace_before)
            results: list[dict[str, object]] = []
            for command in safe_commands:
                raw = self._command_runner(
                    command,
                    cwd=workspace,
                    timeout=safe_timeout,
                    source_path=scope.root,
                )
                results.append(_command_result(command, raw))
            workspace_after = capture_local_agent_tree_snapshot(workspace)
            workspace_after_sha = _tree_sha(workspace_after)
            if workspace_after != workspace_before:
                raise ValueError("code_evidence_verification_side_effect")

        source_after = _source_snapshot(self._adapter, scope)
        if source_after != source_before:
            raise ValueError("code_evidence_repository_changed")
        verification_status = (
            "passed"
            if all(item["returncode"] == 0 and item["timed_out"] is False for item in results)
            else "failed"
        )
        receipt_value = {
            "commands": results,
            "input_bundle_id": int(input_bundle["id"]),
            "input_bundle_seal_sha256": str(input_bundle["seal_sha256"]),
            "patch_sha256": str(artifacts["diff_patch"].sha256),
            "repository_alias": repository_alias,
            "repository_snapshot_sha256": source_after,
            "schema_version": "his-code-evidence-verification.v1",
            "verification_status": verification_status,
            "workspace_after_sha256": workspace_after_sha,
            "workspace_before_sha256": workspace_before_sha,
        }
        _validate_receipt(receipt_value)
        receipt = _canonical_json(receipt_value)
        output_bundle = self._repository.create_bundle(
            bundle_key=bundle_key,
            conversation_key=conversation_key,
            task_key=task_key,
            repository_alias=repository_alias,
            repository_identity_sha256=str(input_bundle["repository_identity_sha256"]),
            head_sha=str(input_bundle["head_sha"]),
            snapshot_sha256=source_after,
            required_capabilities=("verification.run-local",),
        )
        output_bundle_id = int(output_bundle["id"])
        self._repository.append_event(
            output_bundle_id,
            event_type="verification_started",
            status="running",
            details={"capability": "verification.run-local"},
        )
        record = self._artifact_store.persist(
            output_bundle_id,
            kind="verification_receipt",
            leaf="verify.json",
            content=receipt,
        )
        _append_artifact(self._repository, record)
        seal = self._artifact_store.seal(
            output_bundle_id,
            artifacts=(record,),
            repository_snapshot_sha256=source_after,
        )
        _append_artifact(self._repository, seal)
        self._repository.append_event(
            output_bundle_id,
            event_type="verification_completed",
            status="success" if verification_status == "passed" else "failed",
            details={"capability": "verification.run-local", "result": verification_status},
        )
        self._repository.seal_bundle(output_bundle_id, seal_sha256=seal.sha256)
        return {
            "verification_bundle_id": output_bundle_id,
            "verification_bundle_sha256": seal.sha256,
            "evidence_bundle_sha256": str(input_bundle["seal_sha256"]),
            "verification_status": verification_status,
            "snapshot_consistent": True,
            "repository_alias": repository_alias,
            "external_calls": False,
            "local_mutation": False,
        }

    def _load_diff_bundle(
        self, bundle_id: object
    ) -> tuple[dict[str, object], dict[str, EvidenceArtifactRecord], bytes, dict[str, object]]:
        return load_sealed_diff_evidence(self._repository, self._artifact_store, bundle_id)


def load_sealed_diff_evidence(
    repository: CodeEvidenceRepository,
    artifact_store: EvidenceArtifactStore,
    bundle_id: object,
) -> tuple[dict[str, object], dict[str, EvidenceArtifactRecord], bytes, dict[str, object]]:
    """Re-open and cross-bind every artifact in one sealed git.diff bundle."""
    if not isinstance(repository, CodeEvidenceRepository) or not isinstance(artifact_store, EvidenceArtifactStore):
        raise TypeError("code_evidence_verification_configuration_invalid")
    if not isinstance(bundle_id, int) or isinstance(bundle_id, bool) or bundle_id <= 0:
        raise ValueError("code_evidence_verification_input_invalid")
    bundle = repository.get_bundle(bundle_id)
    if bundle["status"] != "sealed" or bundle["required_capabilities"] != ["git.diff"]:
        raise ValueError("code_evidence_verification_input_invalid")
    values = {str(item["kind"]): _record(item) for item in bundle["artifacts"]}
    if set(values) != {"diff_patch", "diff_manifest", "bundle_seal"}:
        raise ValueError("code_evidence_verification_input_invalid")
    patch = artifact_store.reopen(values["diff_patch"])
    manifest_bytes = artifact_store.reopen(values["diff_manifest"])
    seal_bytes = artifact_store.reopen(values["bundle_seal"])
    if values["bundle_seal"].sha256 != bundle["seal_sha256"]:
        raise ValueError("code_evidence_verification_input_invalid")
    try:
        manifest = json.loads(manifest_bytes)
        seal = json.loads(seal_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("code_evidence_verification_input_invalid") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "his-git-diff-evidence.v1"
        or manifest.get("repository_alias") != bundle["repository_alias"]
        or manifest.get("head_sha") != bundle["head_sha"]
        or manifest.get("snapshot_sha256") != bundle["snapshot_sha256"]
        or manifest.get("patch_sha256") != values["diff_patch"].sha256
        or not isinstance(seal, dict)
        or seal.get("schema_version") != "his-code-evidence-seal.v1"
        or seal.get("bundle_id") != bundle_id
        or seal.get("repository_snapshot_sha256") != bundle["snapshot_sha256"]
    ):
        raise ValueError("code_evidence_verification_input_invalid")
    expected_seal_artifacts = [
        {
            "kind": item.kind,
            "relative_path": item.relative_path,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }
        for item in sorted(
            (values["diff_manifest"], values["diff_patch"]),
            key=lambda value: (value.kind, value.relative_path),
        )
    ]
    if seal.get("artifacts") != expected_seal_artifacts:
        raise ValueError("code_evidence_verification_input_invalid")
    return bundle, values, patch, manifest


def load_sealed_verification_evidence(
    repository: CodeEvidenceRepository,
    artifact_store: EvidenceArtifactStore,
    bundle_id: object,
) -> tuple[dict[str, object], dict[str, EvidenceArtifactRecord], dict[str, object]]:
    """Re-open a verification receipt and its exact immutable bundle seal."""
    if not isinstance(repository, CodeEvidenceRepository) or not isinstance(artifact_store, EvidenceArtifactStore):
        raise TypeError("code_evidence_verification_configuration_invalid")
    if not isinstance(bundle_id, int) or isinstance(bundle_id, bool) or bundle_id <= 0:
        raise ValueError("code_evidence_verification_input_invalid")
    bundle = repository.get_bundle(bundle_id)
    if bundle["status"] != "sealed" or bundle["required_capabilities"] != ["verification.run-local"]:
        raise ValueError("code_evidence_verification_input_invalid")
    records = {str(item["kind"]): _record(item) for item in bundle["artifacts"]}
    if set(records) != {"verification_receipt", "bundle_seal"}:
        raise ValueError("code_evidence_verification_input_invalid")
    receipt_bytes = artifact_store.reopen(records["verification_receipt"])
    seal_bytes = artifact_store.reopen(records["bundle_seal"])
    if records["bundle_seal"].sha256 != bundle["seal_sha256"]:
        raise ValueError("code_evidence_verification_input_invalid")
    try:
        receipt = json.loads(receipt_bytes)
        seal = json.loads(seal_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("code_evidence_verification_input_invalid") from None
    _validate_receipt(receipt)
    expected = [{
        "kind": records["verification_receipt"].kind,
        "relative_path": records["verification_receipt"].relative_path,
        "sha256": records["verification_receipt"].sha256,
        "size_bytes": records["verification_receipt"].size_bytes,
    }]
    if (
        not isinstance(seal, dict)
        or seal.get("schema_version") != "his-code-evidence-seal.v1"
        or seal.get("bundle_id") != bundle_id
        or seal.get("repository_snapshot_sha256") != bundle["snapshot_sha256"]
        or seal.get("artifacts") != expected
        or receipt["repository_alias"] != bundle["repository_alias"]
        or receipt["repository_snapshot_sha256"] != bundle["snapshot_sha256"]
    ):
        raise ValueError("code_evidence_verification_input_invalid")
    return bundle, records, receipt


def _source_snapshot(adapter: GitProviderAdapter, scope: RepositoryScope) -> str:
    with adapter._execution_snapshot(scope) as snapshot:
        return _snapshot_digest(snapshot)


def _commands(value: object) -> tuple[tuple[str, ...], ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
        or len(value) > _MAX_COMMANDS
    ):
        raise ValueError("code_evidence_verification_command_invalid")
    result: list[tuple[str, ...]] = []
    try:
        for command in value:
            if not isinstance(command, tuple):
                raise ValueError
            checked, _identity = _validate_unittest_command(command)
            result.append(checked)
    except (TypeError, ValueError):
        raise ValueError("code_evidence_verification_command_invalid") from None
    return tuple(result)


def _timeout(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= _MAX_TIMEOUT_SECONDS:
        raise ValueError("code_evidence_verification_command_invalid")
    return value


def _command_result(command: tuple[str, ...], value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _RESULT_FIELDS:
        raise ValueError("code_evidence_verification_result_invalid")
    returncode = value["returncode"]
    timed_out = value["timed_out"]
    cleanup = value["cleanup"]
    duration_ms = value["duration_ms"]
    if (
        not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or not -255 <= returncode <= 255
        or type(timed_out) is not bool
        or cleanup not in {"not_needed", "terminated", "spawn_failed"}
        or not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or not 0 <= duration_ms <= 3_600_000
        or not _is_sha(value["stdout_sha256"])
        or not _is_sha(value["stderr_sha256"])
    ):
        raise ValueError("code_evidence_verification_result_invalid")
    return {
        "argv_sha256": hashlib.sha256(b"\0".join(item.encode("utf-8") for item in command)).hexdigest(),
        "cleanup": cleanup,
        "duration_ms": duration_ms,
        "returncode": returncode,
        "stderr_sha256": value["stderr_sha256"],
        "stdout_sha256": value["stdout_sha256"],
        "timed_out": timed_out,
    }


def _validate_receipt(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS:
        raise ValueError("code_evidence_verification_result_invalid")
    if (
        value["schema_version"] != "his-code-evidence-verification.v1"
        or value["verification_status"] not in {"passed", "failed"}
        or not _is_sha(value["input_bundle_seal_sha256"])
        or not _is_sha(value["patch_sha256"])
        or not _is_sha(value["repository_snapshot_sha256"])
        or not _is_sha(value["workspace_before_sha256"])
        or value["workspace_before_sha256"] != value["workspace_after_sha256"]
        or not isinstance(value["commands"], list)
        or not value["commands"]
    ):
        raise ValueError("code_evidence_verification_result_invalid")
    passed = True
    for item in value["commands"]:
        if (
            not isinstance(item, dict)
            or set(item) != _PERSISTED_RESULT_FIELDS
            or not _is_sha(item.get("argv_sha256"))
            or not _is_sha(item.get("stdout_sha256"))
            or not _is_sha(item.get("stderr_sha256"))
            or not isinstance(item.get("returncode"), int)
            or isinstance(item.get("returncode"), bool)
            or not -255 <= item["returncode"] <= 255
            or type(item.get("timed_out")) is not bool
            or item.get("cleanup") not in {"not_needed", "terminated", "spawn_failed"}
            or not isinstance(item.get("duration_ms"), int)
            or isinstance(item.get("duration_ms"), bool)
            or not 0 <= item["duration_ms"] <= 3_600_000
        ):
            raise ValueError("code_evidence_verification_result_invalid")
        passed = passed and item["returncode"] == 0 and item["timed_out"] is False
    if (value["verification_status"] == "passed") != passed:
        raise ValueError("code_evidence_verification_result_invalid")


def _git_ok(
    boundary: SafeGitBoundary,
    arguments: list[str],
    cwd: Path,
    content: bytes | None = None,
) -> None:
    result = boundary.run(arguments, cwd=cwd, input_bytes=content)
    if result["returncode"] != 0:
        raise ValueError("code_evidence_patch_replay_failed")


def _tree_sha(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _record(item: Mapping[str, object]) -> EvidenceArtifactRecord:
    return EvidenceArtifactRecord(
        bundle_id=int(item["bundle_id"]),
        kind=str(item["kind"]),
        relative_path=str(item["relative_path"]),
        sha256=str(item["sha256"]),
        size_bytes=int(item["size_bytes"]),
        device=int(item["device"]),
        inode=int(item["inode"]),
        mode=int(item["mode"]),
        link_count=int(item["link_count"]),
    )


def _append_artifact(repository: CodeEvidenceRepository, record: EvidenceArtifactRecord) -> None:
    repository.append_artifact(
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
