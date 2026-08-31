from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Mapping

from app.local_agent_contract import LocalAgentTask, load_local_agent_task_bytes
from app.local_agent_repository import LocalAgentRunRepository
from app.local_agent_review import atomic_write_owned_artifact, parse_local_agent_review, read_owned_file_with_identity
from app.local_agent_runner import _tree_fingerprint
from app.sensitive_text import validate_audit_alias
from app.worktree_executor import (
    SafeGitBoundary,
    apply_final_diff_to_project,
    build_local_apply_application_id,
    capture_target_file_states,
    capture_local_agent_tree_snapshot,
    parse_status_paths,
    rebuild_local_apply_evidence_for_applied_source,
    run_command,
)


_TTL_SECONDS = 300
_CONTROL = ".harness_local_agent_control"
_HASH = frozenset("0123456789abcdef")
_EXPECTED_KINDS = (
    "task_contract",
    "worker_patch",
    "worker_change_manifest",
    "verification_manifest",
    "final_diff",
    "final_patch",
    "final_verification",
    "final_manifest",
    "final_review",
    "review_seal",
)


@dataclass(frozen=True)
class LocalApplyConfirmation:
    run_id: int
    requested_by: str
    expires_at: str
    token: str


class LocalAgentConfirmationService:
    """One-time human confirmation for local-only, journaled patch apply."""

    def __init__(
        self,
        *,
        repository: LocalAgentRunRepository,
        artifact_root: Path,
    ) -> None:
        if not isinstance(repository, LocalAgentRunRepository) or not isinstance(artifact_root, Path) or not artifact_root.is_absolute():
            raise TypeError("local_agent_confirmation_invalid")
        item = artifact_root.lstat()
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
            raise ValueError("local_agent_confirmation_invalid")
        self._repository = repository
        self._artifact_root = artifact_root
        self._artifact_root_identity = (item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode))
        self._service_capability = repository._bind_local_apply_service(self)

    def issue_local_apply_confirmation(self, run_id: int, requested_by: str) -> LocalApplyConfirmation:
        run_id = _positive_id(run_id)
        requester = _requester(requested_by)
        now = _now_utc()
        snapshot = self._repository.snapshot(run_id)
        run = snapshot["run"]
        attempts = snapshot["attempts"]
        if run["status"] != "awaiting_human_confirmation" or not attempts or attempts[-1]["status"] != "completed":
            raise ValueError("local_agent_confirmation_invalid")
        attempt_id = int(attempts[-1]["id"])
        evidence = self._validate_evidence(snapshot, attempt_id, require_sealed_source=True)
        source = _source_facts(evidence.task, evidence.changed_paths)
        if source["dirty_allowed_paths"]:
            raise ValueError("local_agent_confirmation_invalid")
        token = secrets.token_urlsafe(32)
        token_hash = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires = now + timedelta(seconds=_TTL_SECONDS)
        binding = {
            "schema_version": "his-local-agent-confirmation.v1",
            "run_id": run_id,
            "attempt_id": attempt_id,
            "contract_hash": run["contract_hash"],
            "initial_head": run["initial_head"],
            "requested_by": requester,
            "expires_at": expires.isoformat(),
            "artifacts": [_artifact_fact(item) for item in evidence.artifacts],
            "artifact_identities_sha256": evidence.identity_hash,
            "final_patch_sha256": evidence.patch_hash,
            "final_manifest_sha256": evidence.manifest_hash,
            "review_seal_sha256": evidence.seal_hash,
            "changed_paths_sha256": _json_hash(evidence.changed_paths),
            "repository_root_identity": list(evidence.task.repository_root_identity),
            "git_entry_identity": list(evidence.task.git_entry_identity),
            "git_dir_identity": list(evidence.task.git_dir_identity),
            "source_status_sha256": source["status_sha256"],
            "source_worktrees_sha256": source["worktrees_sha256"],
            "unrelated_status_sha256": source["unrelated_status_sha256"],
        }
        self._repository._issue_apply_confirmation(
            service_owner=self,
            service_capability=self._service_capability,
            run_id=run_id,
            attempt_id=attempt_id,
            token_hash=token_hash,
            requested_by=requester,
            binding=binding,
            issued_at=now.isoformat(),
            expires_at=expires.isoformat(),
        )
        return LocalApplyConfirmation(run_id, requester, expires.isoformat(), token)

    def confirm_and_apply(self, run_id: int, token: str, requested_by: str) -> dict[str, object]:
        run_id = _positive_id(run_id)
        requester = _requester(requested_by)
        if not isinstance(token, str) or not 20 <= len(token) <= 256 or token != token.strip():
            raise ValueError("local_agent_confirmation_invalid")
        token_hash = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = _now_utc()

        prepared = self._repository._prepare_local_apply_completion(
            service_owner=self,
            service_capability=self._service_capability,
            run_id=run_id,
            token_hash=token_hash,
            requested_by=requester,
            now=now.isoformat(),
        )
        if prepared.get("expired"):
            return {"run": prepared["run"], "status": "confirmation_expired"}
        completion = self._repository.finalize_local_apply(prepared["capability"])
        operation_result = prepared["operation"]
        return {
            "run": completion["run"],
            "status": "locally_applied",
            "apply": operation_result["apply"],
            "receipt": completion["receipt"],
        }

    def _execute_local_apply_operation(
        self,
        run: Mapping[str, object],
        attempt: Mapping[str, object],
        artifacts: tuple[Mapping[str, object], ...],
        binding: Mapping[str, object],
        requested_by: str,
        operation: Mapping[str, object],
    ) -> Mapping[str, object]:
        run_id = int(run["id"])
        snapshot = {
            "run": dict(run),
            "attempts": [dict(attempt)],
            "artifacts": [dict(item) for item in artifacts],
        }
        evidence = self._validate_evidence(snapshot, int(attempt["id"]), require_sealed_source=False)
        self._validate_binding(binding, run, evidence, requested_by)
        current = _source_facts(evidence.task, evidence.changed_paths)
        baseline = hmac.compare_digest(str(binding["source_status_sha256"]), str(current["status_sha256"]))
        already_applied = _source_patch(evidence.task, evidence.changed_paths) == evidence.patch_bytes
        if not baseline and not already_applied:
            raise ValueError("local_agent_confirmation_invalid")
        if current["worktrees_sha256"] != binding["source_worktrees_sha256"]:
            raise ValueError("local_agent_confirmation_invalid")
        if baseline and current["dirty_allowed_paths"]:
            raise ValueError("local_agent_confirmation_invalid")
        try:
            if baseline:
                applied = apply_final_diff_to_project(
                    project_path=evidence.task.project_path,
                    final_diff=evidence.patch_bytes.decode("utf-8", "strict"),
                    allow_file_changes=True,
                    expected_common_git_identity=evidence.task.git_dir_identity,
                    application_id=str(operation["primary_application_id"]),
                )
            else:
                applied = None
                for application_id in (
                    str(operation["primary_application_id"]),
                    str(operation["recovery_application_id"]),
                ):
                    try:
                        applied = rebuild_local_apply_evidence_for_applied_source(
                            project_path=evidence.task.project_path,
                            final_diff=evidence.patch_bytes.decode("utf-8", "strict"),
                            application_id=application_id,
                            expected_common_git_identity=evidence.task.git_dir_identity,
                            pre_file_states=operation["pre_file_states"],
                            pre_status=operation["pre_status"],
                            expected_post_file_states=operation["expected_post_file_states"],
                        )
                        break
                    except OSError:
                        continue
                if applied is None:
                    raise OSError("no safe local apply recovery evidence path")
        except Exception:
            raise ValueError("local_agent_apply_recovery_required") from None
        if applied.get("status") != "success":
            code = "local_agent_apply_recovery_required" if applied.get("status") == "recovery_required" else "local_agent_apply_failed"
            raise ValueError(code)
        after = _source_facts(evidence.task, evidence.changed_paths)
        if after["head"] != evidence.task.initial_head or after["worktrees_sha256"] != binding["source_worktrees_sha256"]:
            raise ValueError("local_agent_apply_recovery_required")
        if _source_patch(evidence.task, evidence.changed_paths) != evidence.patch_bytes:
            raise ValueError("local_agent_apply_recovery_required")
        if after["unrelated_status_sha256"] != binding["unrelated_status_sha256"]:
            raise ValueError("local_agent_apply_recovery_required")
        receipt_bytes = json.dumps(
            {
                "schema_version": "his-local-agent-apply-receipt.v1",
                "run_id": run_id,
                "attempt_id": int(attempt["id"]),
                "contract_hash": run["contract_hash"],
                "initial_head": run["initial_head"],
                "final_patch_sha256": evidence.patch_hash,
                "changed_paths_sha256": _json_hash(evidence.changed_paths),
                "remote_actions": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        receipt = self._write_or_validate_receipt(run_id, int(attempt["id"]), receipt_bytes)
        return {
            "receipt": receipt,
            "apply": {
                "status": "success",
                "idempotent": bool(applied.get("idempotent")),
                "application_id": str(applied.get("application_id") or ""),
                "transaction_state": str((applied.get("transaction") or {}).get("state") or ""),
                "recovery_status": str((applied.get("recovery") or {}).get("status") or ""),
            },
        }

    def _prepare_local_apply_operation(
        self,
        run: Mapping[str, object],
        attempt: Mapping[str, object],
        artifacts: tuple[Mapping[str, object], ...],
        binding: Mapping[str, object],
        requested_by: str,
    ) -> Mapping[str, object]:
        snapshot = {
            "run": dict(run),
            "attempts": [dict(attempt)],
            "artifacts": [dict(item) for item in artifacts],
        }
        evidence = self._validate_evidence(snapshot, int(attempt["id"]), require_sealed_source=False)
        self._validate_binding(binding, run, evidence, requested_by)
        source = _source_facts(evidence.task, evidence.changed_paths)
        if source["dirty_allowed_paths"]:
            raise ValueError("local_agent_confirmation_invalid")
        pre_status = run_command(
            ["git", "status", "--porcelain"], cwd=evidence.task.project_path, timeout=30,
        )
        if pre_status.get("returncode") != 0:
            raise ValueError("local_agent_confirmation_invalid")
        pre_file_states = capture_target_file_states(evidence.task.project_path, list(evidence.changed_paths))
        expected_post_file_states = capture_target_file_states(
            Path(str(run["worktree_path"])), list(evidence.changed_paths),
        )
        return {
            "schema_version": "his-local-agent-apply-operation.v1",
            "run_id": int(run["id"]),
            "attempt_id": int(attempt["id"]),
            "contract_hash": str(run["contract_hash"]),
            "initial_head": str(run["initial_head"]),
            "final_patch_sha256": evidence.patch_hash,
            "final_patch_size_bytes": len(evidence.patch_bytes),
            "changed_paths": list(evidence.changed_paths),
            "changed_paths_sha256": _json_hash(evidence.changed_paths),
            "pre_source_status_sha256": source["status_sha256"],
            "pre_source_worktrees_sha256": source["worktrees_sha256"],
            "pre_unrelated_status_sha256": source["unrelated_status_sha256"],
            "pre_file_states": pre_file_states,
            "pre_status": pre_status,
            "expected_post_file_states": expected_post_file_states,
            "primary_application_id": build_local_apply_application_id(
                project_path=evidence.task.project_path,
                patch_hash=evidence.patch_hash,
            ),
        }

    def _validate_evidence(self, snapshot: Mapping[str, object], attempt_id: int, *, require_sealed_source: bool) -> "_Evidence":
        self._assert_root()
        run = snapshot.get("run")
        artifacts = snapshot.get("artifacts")
        if not isinstance(run, Mapping) or run.get("status") != "awaiting_human_confirmation" or not isinstance(artifacts, list):
            raise ValueError("local_agent_confirmation_invalid")
        selected: list[Mapping[str, object]] = []
        contents: dict[str, bytes] = {}
        identities: list[object] = []
        for kind in _EXPECTED_KINDS:
            owner = None if kind == "task_contract" else attempt_id
            matches = [item for item in artifacts if isinstance(item, Mapping) and item.get("kind") == kind and item.get("attempt_id") == owner]
            if len(matches) != 1:
                raise ValueError("local_agent_confirmation_invalid")
            record = matches[0]
            content, identity = self._read_record(record)
            selected.append(record)
            contents[kind] = content
            identities.append([kind, str(record["relative_path"]), _stable_identity_chain(identity)])
        task = load_local_agent_task_bytes(contents["task_contract"])
        if task.contract_hash != run.get("contract_hash") or task.initial_head != run.get("initial_head"):
            raise ValueError("local_agent_confirmation_invalid")
        if contents["worker_patch"] != contents["final_diff"] or contents["worker_patch"] != contents["final_patch"]:
            raise ValueError("local_agent_confirmation_invalid")
        review = parse_local_agent_review(contents["final_review"])
        if review.verdict != "approved" or review.findings:
            raise ValueError("local_agent_confirmation_invalid")
        manifest = _strict_json(contents["final_manifest"])
        seal = _strict_json(contents["review_seal"])
        if (
            manifest.get("schema_version") != "his-local-agent-artifact-manifest.v1"
            or manifest.get("run_id") != run.get("id")
            or manifest.get("attempt_id") != attempt_id
            or manifest.get("contract_hash") != task.contract_hash
            or manifest.get("initial_head") != task.initial_head
            or manifest.get("current_head") != task.initial_head
            or manifest.get("remote_actions") is not False
            or seal.get("schema_version") != "his-local-agent-review-seal.v1"
            or seal.get("run_id") != run.get("id")
            or seal.get("attempt_id") != attempt_id
            or seal.get("verdict") != "approved"
            or seal.get("review_hash") != review.review_hash
        ):
            raise ValueError("local_agent_confirmation_invalid")
        changed = manifest.get("changed_paths")
        if not isinstance(changed, list) or not changed or changed != sorted(set(changed)):
            raise ValueError("local_agent_confirmation_invalid")
        changed_paths = tuple(_relative_path(item) for item in changed)
        if any(not _allowed(path, task.allowed_paths) for path in changed_paths):
            raise ValueError("local_agent_confirmation_invalid")
        expected_authoritative = [_artifact_fact(item) for item in selected[:8]]
        if seal.get("authoritative_artifacts") != expected_authoritative or seal.get("review_artifact") != _artifact_fact(selected[8]):
            raise ValueError("local_agent_confirmation_invalid")
        source_fingerprint = _tree_fingerprint(capture_local_agent_tree_snapshot(task.project_path))
        worktree_path = Path(str(run.get("worktree_path") or ""))
        if not worktree_path.is_absolute() or worktree_path.parent != self._artifact_root:
            raise ValueError("local_agent_confirmation_invalid")
        if (
            (require_sealed_source and seal.get("source_fingerprint") != source_fingerprint)
            or seal.get("worktree_fingerprint") != _tree_fingerprint(capture_local_agent_tree_snapshot(worktree_path))
        ):
            raise ValueError("local_agent_confirmation_invalid")
        return _Evidence(
            task=task,
            artifacts=tuple(selected),
            changed_paths=changed_paths,
            patch_bytes=contents["final_patch"],
            patch_hash=hashlib.sha256(contents["final_patch"]).hexdigest(),
            manifest_hash=hashlib.sha256(contents["final_manifest"]).hexdigest(),
            seal_hash=hashlib.sha256(contents["review_seal"]).hexdigest(),
            identity_hash=_json_hash(identities),
        )

    def _validate_binding(self, binding: Mapping[str, object], run: Mapping[str, object], evidence: "_Evidence", requester: str) -> None:
        if (
            binding.get("schema_version") != "his-local-agent-confirmation.v1"
            or binding.get("run_id") != run.get("id")
            or binding.get("attempt_id") != evidence.artifacts[-1].get("attempt_id")
            or binding.get("contract_hash") != evidence.task.contract_hash
            or binding.get("initial_head") != evidence.task.initial_head
            or binding.get("requested_by") != requester
            or binding.get("artifacts") != [_artifact_fact(item) for item in evidence.artifacts]
            or binding.get("artifact_identities_sha256") != evidence.identity_hash
            or binding.get("final_patch_sha256") != evidence.patch_hash
            or binding.get("final_manifest_sha256") != evidence.manifest_hash
            or binding.get("review_seal_sha256") != evidence.seal_hash
            or binding.get("changed_paths_sha256") != _json_hash(evidence.changed_paths)
            or binding.get("repository_root_identity") != list(evidence.task.repository_root_identity)
            or binding.get("git_entry_identity") != list(evidence.task.git_entry_identity)
            or binding.get("git_dir_identity") != list(evidence.task.git_dir_identity)
        ):
            raise ValueError("local_agent_confirmation_invalid")

    def _read_record(self, record: Mapping[str, object]) -> tuple[bytes, tuple[tuple[int, int, int, int], ...]]:
        relative, digest, size = record.get("relative_path"), record.get("sha256"), record.get("size_bytes")
        if not isinstance(relative, str) or not _digest(digest) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("local_agent_confirmation_invalid")
        try:
            content, identity = read_owned_file_with_identity(self._artifact_root, relative, maximum=size)
        except ValueError:
            raise ValueError("local_agent_confirmation_invalid") from None
        if len(content) != size or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), str(digest)):
            raise ValueError("local_agent_confirmation_invalid")
        return content, identity

    def _write_or_validate_receipt(self, run_id: int, attempt_id: int, content: bytes) -> dict[str, object]:
        relative = f"{_CONTROL}/run_{run_id}/attempt_{attempt_id}/apply-receipt.json"
        try:
            written_relative, _digest_value, _size = atomic_write_owned_artifact(
                self._artifact_root,
                run_id=run_id,
                attempt_id=attempt_id,
                leaf="apply-receipt.json",
                content=content,
            )
            if written_relative != relative:
                raise ValueError("local_agent_apply_recovery_required")
        except ValueError:
            try:
                existing, _identity = read_owned_file_with_identity(self._artifact_root, relative, maximum=len(content))
            except ValueError:
                raise ValueError("local_agent_apply_recovery_required") from None
            if existing != content:
                raise ValueError("local_agent_apply_recovery_required")
        reopened, _identity = read_owned_file_with_identity(self._artifact_root, relative, maximum=len(content))
        if reopened != content:
            raise ValueError("local_agent_apply_recovery_required")
        return {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "kind": "local_apply_receipt",
            "relative_path": relative,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    def _assert_root(self) -> None:
        item = self._artifact_root.lstat()
        if stat.S_ISLNK(item.st_mode) or (item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)) != self._artifact_root_identity:
            raise ValueError("local_agent_confirmation_invalid")


@dataclass(frozen=True)
class _Evidence:
    task: LocalAgentTask
    artifacts: tuple[Mapping[str, object], ...]
    changed_paths: tuple[str, ...]
    patch_bytes: bytes
    patch_hash: str
    manifest_hash: str
    seal_hash: str
    identity_hash: str


def _source_facts(task: LocalAgentTask, changed_paths: tuple[str, ...]) -> dict[str, object]:
    # Loading the task has already revalidated no-follow repository identities.
    boundary = SafeGitBoundary(task.project_path)
    head = boundary.text(["rev-parse", "--verify", "HEAD"], cwd=task.project_path).strip()
    if head != task.initial_head:
        raise ValueError("local_agent_confirmation_invalid")
    status = boundary.text(["status", "--porcelain=v1", "--untracked-files=all"], cwd=task.project_path)
    status_paths = parse_status_paths(status)
    dirty_allowed = sorted(path for path in status_paths if _allowed(path, task.allowed_paths))
    unrelated = sorted(path for path in status_paths if path not in set(changed_paths))
    worktrees = boundary.text(["worktree", "list", "--porcelain"], cwd=task.project_path)
    return {
        "head": head,
        "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "worktrees_sha256": hashlib.sha256(worktrees.encode("utf-8")).hexdigest(),
        "unrelated_status_sha256": _json_hash(unrelated),
        "dirty_allowed_paths": dirty_allowed,
    }


def _source_patch(task: LocalAgentTask, changed_paths: tuple[str, ...]) -> bytes:
    boundary = SafeGitBoundary(task.project_path)
    result = boundary.run(["diff", "--binary", "HEAD", "--", *changed_paths], cwd=task.project_path)
    if result["returncode"] != 0:
        raise ValueError("local_agent_confirmation_invalid")
    patch = bytes(result["stdout"])
    tracked = set(boundary.text(["ls-files", "--", *changed_paths], cwd=task.project_path).splitlines())
    for path in changed_paths:
        target = task.project_path / path
        if path not in tracked and target.is_file() and not target.is_symlink():
            item = boundary.run(["diff", "--no-index", "--binary", "--", "/dev/null", path], cwd=task.project_path)
            if item["returncode"] not in {0, 1}:
                raise ValueError("local_agent_confirmation_invalid")
            patch += bytes(item["stdout"])
    return patch


def _strict_json(raw: bytes) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=unique, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict) or json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") != raw:
            raise ValueError
        return value
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("local_agent_confirmation_invalid") from None


def _artifact_fact(record: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": record["kind"],
        "relative_path": record["relative_path"],
        "sha256": record["sha256"],
        "size_bytes": record["size_bytes"],
    }


def _requester(value: object) -> str:
    try:
        return validate_audit_alias(value)
    except ValueError:
        raise ValueError("local_agent_confirmation_invalid") from None


def _positive_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("local_agent_confirmation_invalid")
    return value


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("local_agent_confirmation_invalid")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in _HASH for character in value)


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("local_agent_confirmation_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts) or path.parts[0] == ".git":
        raise ValueError("local_agent_confirmation_invalid")
    return value


def _allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    return any(path == allowed or path.startswith(allowed + "/") for allowed in allowed_paths)


def _json_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _stable_identity_chain(identity: tuple[tuple[int, int, int, int], ...]) -> list[list[int]]:
    # Some filesystems expose a directory link count that changes when a regular
    # file is added. Bind directory ownership by device/inode/type, while the
    # terminal artifact keeps its full identity (including the one-link check).
    return [list(item[:3] if stat.S_ISDIR(item[2]) else item) for item in identity]
