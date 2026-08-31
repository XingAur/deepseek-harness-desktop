from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from app.agent_backend_factory import build_agent_backend, resolve_agent_backend_id
from app.codex_cli_worker import CodexWorkerRequest, ProtocolRejectionAudit
from app.local_agent_contract import LocalAgentTask, assert_local_agent_task_is_current, build_worker_prompt, load_local_agent_task_bytes, serialize_local_agent_task, validate_learning_checks
from app.local_agent_events import persistent_worker_event
from app.local_agent_repository import LocalAgentRunRepository
from app.local_agent_review import LocalAgentReviewer, ReviewValidationFailure, ReviewWorkerFailure
from app.harness_learning_guard import build_learning_guard_payload, build_replan_decision
from app.repair_learning import MatchedLearningRule, derive_task_learning_context
from app.repair_learning_service import RepairLearningRecord, RepairLearningService
from app.runtime_policy import LocalAgentActivationPreflight
from app.worktree_executor import SafeGitBoundary, capture_local_agent_tree_snapshot, run_local_agent_verification_argv
from app.worktree_lifecycle import capture_git_metadata, prepare_local_agent_worktree

_MAX_ATTEMPTS = 3
_MAX_WORKTREE_FILES = 4096
_MAX_WORKTREE_BYTES = 64 * 1024 * 1024
_RETRYABLE = frozenset({"workspace_ready", "failed_workspace", "interrupted", "failed_worker", "failed_verification", "changes_requested"})
_CONTROL = ".harness_local_agent_control"


class _Worker(Protocol):
    def start(self, request: CodexWorkerRequest, sink: Any) -> Any: ...


@dataclass(frozen=True)
class _RunBinding:
    task: LocalAgentTask
    worktree_path: Path
    source_metadata: dict[str, tuple[int, int, int, str]]
    source_worktrees: tuple[str, ...]
    worktree_identity: tuple[int, int, int]
    worktree_git_identity: tuple[int, int, int]
    task_artifact: str


class _RepairLearningService(Protocol):
    def matched_checks_for_attempt(self, task: LocalAgentTask, *, run_id: int) -> tuple[MatchedLearningRule, ...]: ...
    def record_verification_failure(self, *, task: LocalAgentTask, run_id: int, attempt_id: int, summary: str) -> RepairLearningRecord: ...
    def record_reviewer_changes_requested(self, *, task: LocalAgentTask, run_id: int, attempt_id: int, summary: str) -> RepairLearningRecord: ...
    def record_human_correction(self, *, task: LocalAgentTask, run_id: int, attempt_id: int, root_cause_kind: str, summary: str) -> RepairLearningRecord: ...
    def record_awaiting_human_correction(self, *, task: LocalAgentTask, run_id: int, attempt_id: int, root_cause_kind: str, summary: str) -> RepairLearningRecord: ...
    def record_approved_review_success_observation(self, *, task: LocalAgentTask, run_id: int, attempt_id: int, review_finalization_capability: object) -> object | None: ...


class LocalAgentRunner:
    """Durable local-agent runner.  The database/control artifacts are truth."""
    def __init__(self, *, repository: LocalAgentRunRepository, worker: _Worker | None = None, reviewer: LocalAgentReviewer | None = None, worktree_root: Path, learning_service: _RepairLearningService | None = None, backend_id: str | None = None, host_handler: Callable[..., Any] | None = None) -> None:
        if not isinstance(repository, LocalAgentRunRepository) or not isinstance(worktree_root, Path):
            raise TypeError("local_agent_runner_invalid")
        if learning_service is not None and not all(callable(getattr(learning_service, name, None)) for name in (
            "matched_checks_for_attempt", "record_verification_failure",
            "record_reviewer_changes_requested", "record_approved_review_success_observation",
        )):
            raise TypeError("local_agent_runner_invalid")
        self._repository = repository
        self._backend_requested = backend_id or os.environ.get("HARNESS_AGENT_BACKEND")
        self._host_handler = host_handler
        self._backend_id = (
            resolve_agent_backend_id(backend_id)
            if worker is None or backend_id is not None
            else None
        )
        self._worker = worker if worker is not None else build_agent_backend(self._backend_id, host_handler=host_handler)
        self._worktree_root = worktree_root
        self._reviewer_injected = reviewer is not None
        self._reviewer = reviewer or LocalAgentReviewer(
            repository=repository,
            artifact_root=worktree_root,
            backend_id=self._backend_id,
            host_handler=host_handler,
        )
        self._learning_service: _RepairLearningService = learning_service or RepairLearningService(repository)

    def execute(self, task: LocalAgentTask, preflight: LocalAgentActivationPreflight) -> dict[str, object]:
        # Deliberately first: consuming the opaque capability precedes all I/O.
        run = self._repository.consume_preflight(task, preflight)
        run_id = int(run["id"])
        if self._backend_id is not None:
            self._repository.append_event(
                run_id,
                None,
                "agent_backend_selected",
                {"backend_id": self._backend_id},
            )
        try:
            artifact = self._write_artifact(run_id, None, "task_contract", serialize_local_agent_task(task), "task.json")
            binding = self._prepare_binding(run_id, task, artifact["relative_path"])
        except Exception as error:
            self._repository.fail_workspace(run_id, _safe_error(error))
            return self._result(run_id, {}, [])
        return self._run_attempt(run_id, binding)

    def retry(self, run_id: int) -> dict[str, object]:
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
            raise ValueError("local_agent_runner_invalid")
        snapshot = self._repository.snapshot(run_id)
        previous_run_status = str(snapshot["run"]["status"])
        self._rebind_to_persisted_backend(snapshot)
        if snapshot["run"]["status"] == "attempts_exhausted":
            raise ValueError("local_agent_retry_exhausted")
        if snapshot["run"]["status"] in _RETRYABLE and len(snapshot["attempts"]) >= _MAX_ATTEMPTS:
            self._repository.exhaust_attempt_budget(run_id)
            raise ValueError("local_agent_retry_exhausted")
        if snapshot["run"]["status"] not in _RETRYABLE:
            raise ValueError("local_agent_retry_invalid")
        binding = self._binding_from_snapshot(snapshot)
        if snapshot["run"]["status"] == "failed_verification":
            if binding is None:
                raise ValueError("local_agent_retry_invalid")
            # A verification side effect quarantines the old worktree forever.
            # Never reset/delete it: create a new owned generation and replay
            # the already-durable cumulative worker patch after rehashing it.
            binding = self._rebuild_quarantined_binding(run_id, snapshot, binding.task, binding.task_artifact)
            return self._run_attempt(
                run_id, binding, previous_run_status=previous_run_status,
            )
        if binding is None:
            task_artifact = next((item for item in snapshot["artifacts"] if item["kind"] == "task_contract"), None)
            if task_artifact is None:
                raise ValueError("local_agent_retry_invalid")
            task = load_local_agent_task_bytes(self._read_artifact(str(task_artifact["relative_path"]), str(task_artifact["sha256"])))
            # A failed create may have a registered but unmarked `run_<id>`.
            # It is quarantined in place; retry never removes/reuses it and
            # creates a proven new generation instead.
            binding = self._prepare_binding(run_id, task, str(task_artifact["relative_path"]), generation=len(snapshot["attempts"]) + 2)
        self._revalidate(binding)
        return self._run_attempt(
            run_id, binding, previous_run_status=previous_run_status,
        )

    def _rebind_to_persisted_backend(self, snapshot: dict[str, object]) -> None:
        selected = next(
            (
                str(event["payload"]["backend_id"])
                for event in snapshot.get("events", [])
                if event.get("event_type") == "agent_backend_selected"
                and isinstance(event.get("payload"), dict)
                and isinstance(event["payload"].get("backend_id"), str)
            ),
            None,
        )
        if selected is None or selected == self._backend_id:
            return
        if self._backend_requested is not None:
            raise ValueError("local_agent_backend_mismatch")
        if self._reviewer_injected:
            raise ValueError("local_agent_backend_mismatch")
        self._backend_id = selected
        self._worker = build_agent_backend(selected, host_handler=self._host_handler)
        self._reviewer = LocalAgentReviewer(
            repository=self._repository,
            artifact_root=self._worktree_root,
            backend_id=selected,
            host_handler=self._host_handler,
        )

    def auto_repair(self, run_id: int, *, max_rounds: int = _MAX_ATTEMPTS - 1) -> dict[str, object]:
        """Run bounded local repair rounds until review approval or a gate.

        This is an explicit caller-facing operation.  It never applies a
        change, pushes, deploys, or bypasses human confirmation.  High-risk
        task contracts pause before any automatic retry.
        """

        if (
            not isinstance(run_id, int)
            or isinstance(run_id, bool)
            or run_id <= 0
            or not isinstance(max_rounds, int)
            or isinstance(max_rounds, bool)
            or not 1 <= max_rounds <= _MAX_ATTEMPTS - 1
        ):
            raise ValueError("local_agent_auto_repair_invalid")
        snapshot = self._repository.snapshot(run_id)
        for round_number in range(1, max_rounds + 1):
            status = str(snapshot["run"]["status"])
            if status not in _RETRYABLE:
                break
            binding = self._binding_from_snapshot(snapshot)
            if binding is None:
                break
            context = derive_task_learning_context(binding.task, run_id=run_id)
            if context.high_risk_tags:
                self._repository.append_event(
                    run_id,
                    int(snapshot["attempts"][-1]["id"]) if snapshot["attempts"] else None,
                    "auto_repair_paused_high_risk",
                    {"round": round_number, "risk_tags": list(context.high_risk_tags)},
                )
                break
            self._repository.append_event(
                run_id,
                int(snapshot["attempts"][-1]["id"]) if snapshot["attempts"] else None,
                "auto_repair_round",
                {"round": round_number, "status_before": status},
            )
            snapshot = self.retry(run_id)
        return snapshot

    def record_human_correction(
        self,
        run_id: int,
        *,
        root_cause_kind: str,
        summary_sha256: str,
    ) -> dict[str, object]:
        """Persist one bounded human correction against the current attempt.

        The CLI supplies only the SHA-256 of a caller-owned one-line summary.
        No summary path or raw text reaches this boundary.  A pending local
        apply confirmation is invalidated only after the correction evidence
        has been durably recorded; replay after a later invalidation failure
        reuses the deterministic source key.
        """

        if (
            not isinstance(run_id, int)
            or isinstance(run_id, bool)
            or run_id <= 0
            or not isinstance(root_cause_kind, str)
            or root_cause_kind not in {
                "verification_failure", "review_gap", "path_coverage_gap",
                "contract_mismatch", "implementation_defect",
            }
            or not isinstance(summary_sha256, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", summary_sha256) is None
        ):
            raise ValueError("local_agent_runner_invalid")
        snapshot = self._repository.snapshot(run_id)
        run = snapshot["run"]
        if run["status"] not in {
            "failed_verification", "changes_requested", "awaiting_human_confirmation",
        } or not snapshot["attempts"]:
            raise ValueError("local_agent_correction_invalid")
        binding = self._binding_from_snapshot(snapshot)
        if binding is None or binding.worktree_path.parent != self._worktree_root:
            raise ValueError("local_agent_correction_invalid")
        self._revalidate(binding)
        attempt_id = int(snapshot["attempts"][-1]["id"])
        if run["status"] == "awaiting_human_confirmation":
            record_awaiting = getattr(self._learning_service, "record_awaiting_human_correction", None)
            if not callable(record_awaiting):
                raise ValueError("local_agent_correction_invalid")
            record = record_awaiting(
                task=binding.task,
                run_id=run_id,
                attempt_id=attempt_id,
                root_cause_kind=root_cause_kind,
                summary=summary_sha256,
            )
        else:
            record = self._learning_service.record_human_correction(
                task=binding.task,
                run_id=run_id,
                attempt_id=attempt_id,
                root_cause_kind=root_cause_kind,
                summary=summary_sha256,
            )
        self._write_learning_retrospective_artifact(run_id, attempt_id, record)
        return self._result(run_id, {}, [])

    def _prepare_binding(self, run_id: int, task: LocalAgentTask, artifact: str, *, generation: int | None = None) -> _RunBinding:
        assert_local_agent_task_is_current(task)
        prepared = prepare_local_agent_worktree(project_path=task.project_path, worktree_root=self._worktree_root, run_id=run_id, generation=generation)
        binding = _binding(task, prepared, artifact)
        self._repository.bind_workspace(run_id, _binding_mapping(binding, prepared))
        return binding

    def _rebuild_quarantined_binding(self, run_id: int, snapshot: dict[str, object], task: LocalAgentTask, artifact: str) -> _RunBinding:
        patch_record = next((item for item in reversed(snapshot["artifacts"]) if item["kind"] == "worker_patch"), None)
        if patch_record is None:
            raise ValueError("local_agent_quarantine_patch_missing")
        patch = self._read_artifact(str(patch_record["relative_path"]), str(patch_record["sha256"]))
        generation = len(snapshot["attempts"]) + 1
        binding = self._prepare_binding(run_id, task, artifact, generation=generation)
        boundary = SafeGitBoundary(task.project_path)
        # `git apply` is the fixed SafeGitBoundary argv; no hooks, filters or
        # external diff are reachable.  Check then apply is required before
        # any worker receives the new path.
        fresh = _tree_or_fail(binding.worktree_path)
        checked = boundary.run(["apply", "--check", "--whitespace=nowarn", "-"], cwd=binding.worktree_path, input_bytes=patch)
        applied = boundary.run(["apply", "--whitespace=nowarn", "-"], cwd=binding.worktree_path, input_bytes=patch) if checked["returncode"] == 0 else {"returncode": 125}
        if applied["returncode"] != 0:
            self._repository.transition(run_id, "workspace_ready", "failed_verification", {"quarantined": True, "replay": "failed"})
            raise ValueError("local_agent_quarantine_patch_replay_failed")
        replayed = _validate_change(binding, fresh)
        if hashlib.sha256(bytes(replayed["patch_bytes"])).hexdigest() != str(patch_record["sha256"]):
            self._repository.transition(run_id, "workspace_ready", "failed_verification", {"quarantined": True, "replay": "mismatch"})
            raise ValueError("local_agent_quarantine_patch_replay_mismatch")
        self._repository.append_event(run_id, None, "workspace_rebuilt", {"generation": generation, "quarantined": True})
        return binding

    def _binding_from_snapshot(self, snapshot: dict[str, object]) -> _RunBinding | None:
        raw = snapshot.get("workspace_binding")
        if not isinstance(raw, dict):
            return None
        try:
            task = load_local_agent_task_bytes(self._read_artifact(str(raw["task_artifact"]), str(raw["task_sha256"])))
            binding = _RunBinding(task, Path(str(raw["worktree_path"])), _decode_metadata(raw["source_metadata"]), tuple(raw["source_worktrees"]), tuple(raw["worktree_identity"]), tuple(raw["worktree_git_identity"]), str(raw["task_artifact"]))
            return binding
        except (KeyError, TypeError, ValueError):
            raise ValueError("local_agent_retry_invalid") from None

    def _run_attempt(
        self,
        run_id: int,
        binding: _RunBinding,
        *,
        previous_run_status: str | None = None,
    ) -> dict[str, object]:
        try:
            self._revalidate(binding)
        except Exception:
            return self._workspace_failure(run_id)
        if previous_run_status is None:
            previous_run_status = str(self._repository.snapshot(run_id)["run"]["status"])
        attempt = self._repository.start_attempt(run_id)
        attempt_id = int(attempt["id"])
        try:
            learning_checks = validate_learning_checks(
                binding.task,
                run_id=run_id,
                checks=self._learning_service.matched_checks_for_attempt(
                    binding.task, run_id=run_id,
                ),
            )
            self._write_artifact(
                run_id,
                attempt_id,
                "repair_learning_checks",
                _learning_checks_artifact(run_id, attempt_id, learning_checks),
                f"attempt_{attempt_id}.learning-checks.json",
            )
            self._repository.append_event(
                run_id,
                attempt_id,
                "repair_learning_checks_matched",
                {"matched_count": len(learning_checks)},
            )
            decision = self._issue_harness_decision(
                run_id,
                attempt_id,
                binding.task,
                learning_checks,
                previous_run_status=previous_run_status,
            )
        except Exception:
            try:
                self._repository.append_event(
                    run_id, attempt_id, "repair_learning_failed", {"stage": "attempt_start"},
                )
            finally:
                self._repository.abandon_starting_attempt(run_id, attempt_id)
            return self._result(run_id, {}, [])
        before = _tree_or_fail(binding.worktree_path)
        sink = _RepositorySink(self._repository, run_id, attempt_id)
        try:
            result = self._worker.start(CodexWorkerRequest.worker(
                binding.worktree_path,
                build_worker_prompt(
                    binding.task,
                    workspace_path=binding.worktree_path,
                    learning_checks=learning_checks,
                    learning_run_id=run_id,
                    harness_decision=decision,
                ),
                binding.task.timeout_seconds,
            ), sink)
        except Exception:
            # Before binding there is no identity to complete; after binding it
            # is a real worker failure, not an interrupted phantom attempt.
            if sink.bound:
                self._repository.complete_attempt(attempt_id, "failed_worker", "worker_start_failed", {"primary": "worker_start_failed", "cleanup": "none"})
            else:
                self._repository.abandon_starting_attempt(run_id, attempt_id)
            return self._result(run_id, {}, [])
        if not sink.bound:
            self._repository.abandon_starting_attempt(run_id, attempt_id)
            return self._result(run_id, {}, [])
        error = _worker_error(result)
        cleanup = _cleanup_error(result)
        rejection = getattr(result, "protocol_rejection", None)
        if rejection is not None:
            if error not in {"worker_protocol_invalid", "worker_protocol_failed"} or not isinstance(rejection, ProtocolRejectionAudit):
                error = "worker_result_invalid"
            else:
                try:
                    self._repository.append_event(run_id, attempt_id, "worker_protocol_rejected", rejection.as_mapping())
                except ValueError:
                    error = "worker_result_invalid"
        if error or cleanup or not _typed_worker_result_matches(result, sink):
            primary = error or "worker_result_invalid"
            self._repository.complete_attempt(attempt_id, _failure_attempt_status(primary), primary, {"primary": primary, "cleanup": cleanup or "none"})
            return self._result(run_id, {}, [])
        try:
            self._revalidate(binding)
            try:
                change = _validate_change(binding, before)
            except ValueError as error:
                if str(error) != "local_agent_change_outside_contract" or before != _tree_or_fail(binding.worktree_path):
                    raise
                change = _validate_existing_change(binding)
            patch = bytes(change.pop("patch_bytes"))
            patch_artifact = self._write_artifact(run_id, attempt_id, "worker_patch", patch, f"attempt_{attempt_id}.patch")
            change["patch_sha256"], change["patch_size_bytes"] = patch_artifact["sha256"], patch_artifact["size_bytes"]
            changed_paths_bytes = json.dumps(change["changed_paths"], ensure_ascii=False, separators=(",", ":")).encode()
            change_manifest = {
                "schema_version": "his-local-agent-change.v1",
                "changed_paths": change["changed_paths"],
                "changed_paths_sha256": hashlib.sha256(changed_paths_bytes).hexdigest(),
                "patch_sha256": patch_artifact["sha256"],
                "patch_size_bytes": patch_artifact["size_bytes"],
                "current_head": binding.task.initial_head,
            }
            self._write_artifact(run_id, attempt_id, "worker_change_manifest", json.dumps(change_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(), f"attempt_{attempt_id}.change.json")
        except Exception as error:
            self._repository.complete_attempt(attempt_id, "failed_scope", "worker_scope_invalid", {"primary": _safe_error(error), "cleanup": "none"})
            return self._result(run_id, {}, [])
        self._repository.complete_attempt(attempt_id, "completed", summary={"changed_count": len(change["changed_paths"])})
        try:
            verification = self._verify(run_id, attempt_id, binding)
        except Exception:
            # Once an attempt completed, every verifier/snapshot/cleanup
            # exception is verification evidence, never a workspace failure.
            self._repository.transition(run_id, "verifying", "failed_verification", {"verification_exception": True})
            try:
                record = self._learning_service.record_verification_failure(
                    task=binding.task,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    summary="bounded verification failed",
                )
                self._write_learning_retrospective_artifact(run_id, attempt_id, record)
            except Exception:
                self._repository.append_event(
                    run_id, attempt_id, "repair_learning_failed", {"stage": "verification_failure"},
                )
            return self._result(run_id, change, [])
        target = "failed_verification" if any(item["returncode"] != 0 or item.get("side_effect") for item in verification) else "reviewing"
        try:
            self._write_artifact(run_id, attempt_id, "verification_manifest", json.dumps(verification, sort_keys=True, separators=(",", ":")).encode(), f"attempt_{attempt_id}.verification.json")
        except Exception:
            self._repository.transition(run_id, "verifying", "failed_verification", {"verification_artifact": "failed"})
            return self._result(run_id, change, verification)
        self._repository.transition(run_id, "verifying", target, {"verification_count": len(verification), "changed_count": len(change["changed_paths"])})
        if target == "reviewing":
            # Retain the existing internal call shape when there is no rule.
            # This keeps deterministic legacy reviewer fixtures/monkeypatches
            # compatible while the named learning path remains explicit.
            if learning_checks:
                return self._review(
                    run_id, attempt_id, binding, change, verification,
                    learning_checks=learning_checks,
                )
            return self._review(run_id, attempt_id, binding, change, verification)
        try:
            record = self._learning_service.record_verification_failure(
                task=binding.task,
                run_id=run_id,
                attempt_id=attempt_id,
                summary="bounded verification failed",
            )
            self._write_learning_retrospective_artifact(run_id, attempt_id, record)
        except Exception:
            self._repository.append_event(
                run_id, attempt_id, "repair_learning_failed", {"stage": "verification_failure"},
            )
        return self._result(run_id, change, verification)

    def _issue_harness_decision(
        self,
        run_id: int,
        attempt_id: int,
        task: LocalAgentTask,
        learning_checks: Sequence[MatchedLearningRule],
        *,
        previous_run_status: str,
    ) -> dict[str, object]:
        """Persist one authoritative decision before the worker starts."""

        snapshot = self._repository.snapshot(run_id)
        previous_versions = [
            int(event["payload"]["plan_version"])
            for event in snapshot.get("events", [])
            if event.get("event_type") == "harness_decision_issued"
            and isinstance(event.get("payload"), dict)
            and isinstance(event["payload"].get("plan_version"), int)
        ]
        previous_plan_version = max(previous_versions, default=0)
        failure_code = _decision_failure_code(previous_run_status)
        learning_guard = build_learning_guard_payload(
            run_id=run_id,
            attempt_id=attempt_id,
            checks=learning_checks,
        )
        decision = build_replan_decision(
            task,
            run_id=run_id,
            attempt_id=attempt_id,
            previous_plan_version=previous_plan_version,
            failure_code=failure_code,
            learning_guard=learning_guard,
        )
        content = json.dumps(
            decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self._write_artifact(
            run_id,
            attempt_id,
            "harness_decision",
            content,
            f"attempt_{attempt_id}.harness-decision.json",
        )
        self._repository.append_event(
            run_id,
            attempt_id,
            "harness_decision_issued",
            {
                "plan_version": int(decision["plan_version"]),
                "supersedes_plan_version": decision["supersedes_plan_version"],
                "decision_kind": decision["decision_kind"],
                "failure_code": decision["failure_code"],
                # Prefix the digest in the audit event so the conservative
                # scalar sanitizer cannot mistake a bare 64-char hex value
                # for an opaque credential.
                "decision_digest": "sha256:" + str(decision["decision_sha256"]),
                "must_reinspect": True,
                "execute_only": True,
            },
        )
        return decision

    def _review(self, run_id: int, attempt_id: int, binding: _RunBinding, change: dict[str, object], verification: list[dict[str, object]], learning_checks: Sequence[MatchedLearningRule] = ()) -> dict[str, object]:
        try:
            self._revalidate(binding)
            before = _tree_or_fail(binding.worktree_path)
            source_before = _tree_or_fail(binding.task.project_path)
            result = self._reviewer.review(run_id, learning_focus=learning_checks)
            if result.run_id != run_id or result.attempt_id != attempt_id or result.worktree_path != str(binding.worktree_path):
                raise ValueError("local_agent_review_binding_changed")
            after = _tree_or_fail(binding.worktree_path)
            source_after = _tree_or_fail(binding.task.project_path)
            self._revalidate(binding)
            if before != after or source_before != source_after:
                raise ValueError("local_agent_reviewer_mutation")
            source_fingerprint = _tree_fingerprint(source_after)
            worktree_fingerprint = _tree_fingerprint(after)
            result = self._reviewer.seal(
                result,
                source_fingerprint=source_fingerprint,
                worktree_fingerprint=worktree_fingerprint,
            )

            def integrity_check() -> None:
                self._revalidate(binding)
                if _tree_fingerprint(_tree_or_fail(binding.worktree_path)) != worktree_fingerprint:
                    raise ValueError("local_agent_review_worktree_changed")
                if _tree_fingerprint(_tree_or_fail(binding.task.project_path)) != source_fingerprint:
                    raise ValueError("local_agent_review_source_changed")
                self._reviewer.revalidate(result)

            integrity_check()
            if result.verdict == "changes_requested":
                record = self._learning_service.record_reviewer_changes_requested(
                    task=binding.task,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    summary="bounded reviewer changes requested",
                )
                self._write_learning_retrospective_artifact(run_id, attempt_id, record)
                integrity_check()
            capability = self._repository._prepare_review_finalization(
                run_id=run_id,
                attempt_id=attempt_id,
                expected_updated_at=result.run_revision,
                expected_event_count=result.event_count,
                verdict=result.verdict,
                finding_count=len(result.findings),
                pending_artifacts=result.pending_artifacts,
            )
            integrity_check()
            staged_observation = None
            if result.verdict == "approved":
                staged_observation = self._learning_service.record_approved_review_success_observation(
                    task=binding.task,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    review_finalization_capability=capability,
                )
            integrity_check()
            self._repository.finalize_review(
                capability,
                learning_observation=staged_observation,
            )
            if result.verdict == "changes_requested":
                record_flux_opinion = getattr(
                    self._learning_service,
                    "record_local_reviewer_opinion",
                    None,
                )
                if callable(record_flux_opinion):
                    try:
                        flux_record = record_flux_opinion(
                            task=binding.task,
                            run_id=run_id,
                            attempt_id=attempt_id,
                            reviewer_id="local-reviewer",
                            verdict=result.verdict,
                            review_hash=result.review_hash,
                        )
                        self._repository.append_event(
                            run_id,
                            attempt_id,
                            "flux_lite_reviewer_opinion_recorded",
                            {"candidate_id": str(flux_record["candidate_id"])},
                        )
                    except Exception:
                        self._repository.append_event(
                            run_id,
                            attempt_id,
                            "flux_lite_learning_failed",
                            {"stage": "reviewer_opinion"},
                        )
        except Exception as error:
            snapshot = self._repository.snapshot(run_id)
            status = str(snapshot["run"]["status"])
            if status == "reviewing":
                audit = error.audit if isinstance(error, (ReviewWorkerFailure, ReviewValidationFailure)) else None
                self._repository.fail_review(run_id, attempt_id, audit=audit)
            return self._result(run_id, change, verification)
        review = result.as_mapping()
        return self._result(run_id, change, verification, review)

    def _workspace_failure(self, run_id: int) -> dict[str, object]:
        snapshot = self._repository.snapshot(run_id)
        if snapshot["run"]["status"] == "failed_workspace":
            return self._result(run_id, {}, [])
        # Existing prepared binding corruption is a scope failure only after a
        # worker starts.  Do not mislabel verification/worker facts as setup.
        raise ValueError("local_agent_workspace_revalidation_failed")

    def _verify(self, run_id: int, attempt_id: int, binding: _RunBinding) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for index, command in enumerate(binding.task.verification_commands):
            self._revalidate(binding)
            before = _tree_or_fail(binding.worktree_path)
            record = run_local_agent_verification_argv(command, cwd=binding.worktree_path, timeout=binding.task.timeout_seconds, source_path=binding.task.project_path)
            after = _tree_or_fail(binding.worktree_path)
            record["index"] = index
            record["side_effect"] = before != after
            try:
                self._revalidate(binding)
            except Exception:
                # Source/common-git or worktree-admin mutation is a
                # verification side effect too, never a workspace setup error.
                record["side_effect"] = True
            if record["side_effect"]:
                # The runner refuses to promote a tainted worktree.  The
                # original worker patch remains durable for a fresh recovery.
                record["restore"] = "quarantined"
            self._repository.append_event(run_id, attempt_id, "verification_finished", {"index": index, "returncode": int(record["returncode"]), "timed_out": bool(record["timed_out"]), "side_effect": bool(record["side_effect"])})
            records.append(_public_verification(record))
            if record["returncode"] != 0 or record["side_effect"]:
                break
        return records

    def _revalidate(self, binding: _RunBinding) -> None:
        assert_local_agent_task_is_current(binding.task)
        if _directory_identity(binding.worktree_path) != binding.worktree_identity or _git_entry_identity(binding.worktree_path) != binding.worktree_git_identity:
            raise ValueError("local_agent_worktree_identity_changed")
        boundary = SafeGitBoundary(binding.task.project_path)
        if _head(boundary, binding.worktree_path) != binding.task.initial_head:
            raise ValueError("local_agent_worktree_head_changed")
        listed = _worktrees(boundary, binding.task.project_path)
        if listed != set(binding.source_worktrees) or not _source_metadata_matches(binding, capture_git_metadata(binding.task.project_path)):
            raise ValueError("local_agent_source_changed")

    def _write_artifact(self, run_id: int, attempt_id: int | None, kind: str, content: bytes, leaf: str) -> dict[str, object]:
        if not isinstance(content, bytes) or len(content) > _MAX_WORKTREE_BYTES:
            raise ValueError("local_agent_artifact_invalid")
        directory = self._worktree_root / _CONTROL / f"run_{run_id}"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / leaf
        temporary = directory / (leaf + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(temporary, flags, 0o400)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o400)
        finally:
            if temporary.exists():
                temporary.unlink()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = f"{_CONTROL}/run_{run_id}/{leaf}"
        return self._repository.add_artifact(run_id, attempt_id, kind, relative, digest, len(content))

    def _read_artifact(self, relative: str, digest: str) -> bytes:
        path = self._worktree_root / relative
        if path.is_symlink() or not path.is_file() or path.resolve().parent.parent != (self._worktree_root / _CONTROL).resolve():
            raise ValueError("local_agent_artifact_invalid")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("local_agent_artifact_invalid")
        return data

    def _write_learning_retrospective_artifact(
        self,
        run_id: int,
        attempt_id: int,
        record: RepairLearningRecord,
    ) -> None:
        if re.fullmatch(r"repair-retrospective-retro-r[1-9][0-9]*-a[1-9][0-9]*-s[1-3]-c[1-5]\.json", record.artifact.leaf) is None:
            raise ValueError("local_agent_artifact_invalid")
        leaf = f"attempt_{attempt_id}.{record.artifact.leaf}"
        relative = f"{_CONTROL}/run_{run_id}/{leaf}"
        existing = [item for item in self._repository.snapshot(run_id)["artifacts"] if item["relative_path"] == relative]
        if existing:
            if len(existing) != 1 or (
                existing[0]["kind"] != record.artifact.kind
                or existing[0]["attempt_id"] != attempt_id
                or existing[0]["sha256"] != record.artifact.sha256
                or existing[0]["size_bytes"] != len(record.artifact.content)
            ):
                raise ValueError("local_agent_artifact_invalid")
            return
        self._write_artifact(
            run_id,
            attempt_id,
            record.artifact.kind,
            record.artifact.content,
            leaf,
        )

    def _result(self, run_id: int, change: dict[str, object], verification: list[dict[str, object]], review: dict[str, object] | None = None) -> dict[str, object]:
        snapshot = self._repository.snapshot(run_id)
        snapshot.update({"change": change, "verification": verification, "manifest": {"schema_version": "his-local-agent-run.v2", "run_id": run_id, "remote_actions": False}})
        if review is not None:
            snapshot["review"] = review
        return snapshot


class _RepositorySink:
    def __init__(self, repository: LocalAgentRunRepository, run_id: int, attempt_id: int) -> None:
        self._repository, self._run_id, self._attempt_id, self.bound = repository, run_id, attempt_id, False
        self.pid: int | None = None; self.identity = ""
    def on_started(self, pid: int, start_identity: str) -> None:
        self._repository.bind_worker_identity(self._attempt_id, pid, start_identity)
        self.pid, self.identity, self.bound = pid, start_identity, True
        self._repository.append_event(self._run_id, self._attempt_id, "worker_started", {"bound": True})
    def on_event(self, event: dict[str, object]) -> None:
        self._repository.append_event(
            self._run_id,
            self._attempt_id,
            "worker_event",
            _persistent_worker_event(event),
        )


def _persistent_worker_event(event: object) -> dict[str, object]:
    return persistent_worker_event(event)


def _binding(task: LocalAgentTask, prepared: dict[str, Any], artifact: str) -> _RunBinding:
    return _RunBinding(task, Path(str(prepared["worktree_path"])), dict(prepared["source_git_metadata"]), tuple(prepared["worktrees_after"]), tuple(prepared["worktree_identity"]), tuple(prepared["worktree_git_entry_identity"]), artifact)


def _tree_fingerprint(value: dict[str, dict[str, object]]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _learning_checks_artifact(
    run_id: int,
    attempt_id: int,
    checks: Sequence[MatchedLearningRule],
) -> bytes:
    if not isinstance(run_id, int) or not isinstance(attempt_id, int):
        raise ValueError("local_agent_runner_invalid")
    payload = {
        "schema_version": "his-repair-learning-checks.v1",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "checks": [
            {"rule_key": item.rule.key, "actions": list(item.rule.actions)}
            for item in checks
        ],
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")

def _binding_mapping(binding: _RunBinding, prepared: dict[str, Any]) -> dict[str, object]:
    task_bytes = serialize_local_agent_task(binding.task)
    return {"worktree_path": str(binding.worktree_path), "source_metadata": {key: list(value) for key, value in binding.source_metadata.items()}, "source_worktrees": list(binding.source_worktrees), "worktree_identity": list(binding.worktree_identity), "worktree_git_identity": list(binding.worktree_git_identity), "marker_path": str(prepared["marker_path"]), "task_artifact": binding.task_artifact, "task_sha256": hashlib.sha256(task_bytes).hexdigest()}

def _decode_metadata(raw: object) -> dict[str, tuple[int, int, int, str]]:
    if not isinstance(raw, dict): raise ValueError("invalid")
    result: dict[str, tuple[int, int, int, str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, list) or len(value) != 4: raise ValueError("invalid")
        result[key] = tuple(value)  # type: ignore[assignment]
    return result

def _validate_change(binding: _RunBinding, before: dict[str, dict[str, object]]) -> dict[str, object]:
    after = _tree_or_fail(binding.worktree_path)
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    boundary = SafeGitBoundary(binding.task.project_path)
    _reject_changed_gitlink(boundary, binding.worktree_path)
    if not changed or any(path == ".git" or path.startswith(".git/") or not _allowed(path, binding.task.allowed_paths) for path in changed):
        raise ValueError("local_agent_change_outside_contract")
    if any((before.get(path) or {}).get("type") in {"symlink", "special"} or (after.get(path) or {}).get("type") in {"symlink", "special"} for path in changed):
        raise ValueError("local_agent_special_change")
    index = boundary.run(["diff", "--binary", "HEAD"], cwd=binding.worktree_path)
    check = boundary.run(["diff", "--check", "HEAD"], cwd=binding.worktree_path)
    if index["returncode"] != 0 or check["returncode"] != 0: raise ValueError("local_agent_diff_invalid")
    patch = bytes(index["stdout"])
    tracked = set(boundary.text(["diff", "--name-only", "HEAD"], cwd=binding.worktree_path).splitlines())
    for path in changed:
        if path not in tracked and path not in before and after[path].get("type") == "file":
            item = boundary.run(["diff", "--no-index", "--binary", "--", "/dev/null", path], cwd=binding.worktree_path)
            if item["returncode"] not in {0, 1}: raise ValueError("local_agent_diff_invalid")
            patch += bytes(item["stdout"])
    if len(patch) > _MAX_WORKTREE_BYTES: raise ValueError("local_agent_patch_budget")
    return {"changed_paths": changed, "patch_bytes": patch, "diff_check": {"returncode": int(check["returncode"])}}


def _validate_existing_change(binding: _RunBinding) -> dict[str, object]:
    """Validate a replayed cumulative patch when retry adds no new bytes."""
    boundary = SafeGitBoundary(binding.task.project_path)
    _reject_changed_gitlink(boundary, binding.worktree_path)
    changed = _cumulative_paths(boundary, binding.worktree_path)
    if not changed or any(path == ".git" or path.startswith(".git/") or not _allowed(path, binding.task.allowed_paths) for path in changed):
        raise ValueError("local_agent_change_outside_contract")
    index = boundary.run(["diff", "--binary", "HEAD"], cwd=binding.worktree_path)
    check = boundary.run(["diff", "--check", "HEAD"], cwd=binding.worktree_path)
    if index["returncode"] != 0 or check["returncode"] != 0:
        raise ValueError("local_agent_diff_invalid")
    patch = bytes(index["stdout"])
    for path in changed:
        if path not in boundary.text(["diff", "--name-only", "HEAD"], cwd=binding.worktree_path).splitlines():
            item = boundary.run(["diff", "--no-index", "--binary", "--", "/dev/null", path], cwd=binding.worktree_path)
            if item["returncode"] not in {0, 1}: raise ValueError("local_agent_diff_invalid")
            patch += bytes(item["stdout"])
    return {"changed_paths": changed, "patch_bytes": patch, "diff_check": {"returncode": int(check["returncode"])}}


def _cumulative_paths(boundary: SafeGitBoundary, worktree_path: Path) -> list[str]:
    tracked = boundary.text(["diff", "--name-only", "HEAD"], cwd=worktree_path).splitlines()
    untracked_raw = boundary.run(["ls-files", "--others", "--exclude-standard", "-z"], cwd=worktree_path)
    if untracked_raw["returncode"] != 0: raise ValueError("local_agent_diff_invalid")
    untracked = [item.decode("utf-8", "strict") for item in bytes(untracked_raw["stdout"]).split(b"\0") if item]
    return sorted(set(tracked) | set(untracked))


def _reject_changed_gitlink(boundary: SafeGitBoundary, worktree_path: Path) -> None:
    """Filesystem snapshots cannot observe an index-only gitlink OID swap."""
    raw = boundary.run(["diff", "--cached", "--raw", "-z", "HEAD"], cwd=worktree_path)
    if raw["returncode"] != 0:
        raise ValueError("local_agent_diff_invalid")
    if b":160000 " in bytes(raw["stdout"]) or b" 160000 " in bytes(raw["stdout"]):
        raise ValueError("local_agent_gitlink_changed")


def _source_metadata_matches(binding: _RunBinding, current: dict[str, tuple[int, int, int, str]]) -> bool:
    # Git owns per-linked-worktree index/admin files under common.git/worktrees.
    # Only the exact registered local-agent generations may vary; all other
    # common-git metadata remains immutable.
    allowed = {f"worktrees/{Path(path).name}" for path in binding.source_worktrees if Path(path) != binding.task.project_path}
    for key in set(binding.source_metadata) | set(current):
        if binding.source_metadata.get(key) == current.get(key):
            continue
        if not any(key == prefix or key.startswith(prefix + "/") for prefix in allowed):
            # `git add` in a linked worktree creates immutable loose objects
            # in common.git. Permit additions only; replacement/removal or
            # changes to existing objects remain a source-integrity failure.
            if not key.startswith("objects/") or key in binding.source_metadata or key not in current:
                return False
    return True

def _tree_or_fail(path: Path) -> dict[str, dict[str, object]]:
    tree = capture_local_agent_tree_snapshot(path)
    if len(tree) > _MAX_WORKTREE_FILES or sum(int(item.get("size_bytes", 0)) for item in tree.values()) > _MAX_WORKTREE_BYTES: raise ValueError("local_agent_worktree_budget")
    return tree
def _allowed(path: str, allowed: tuple[str, ...]) -> bool: return any(path == item or path.startswith(item + "/") for item in allowed)
def _directory_identity(path: Path) -> tuple[int, int, int]:
    item = path.lstat()
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode): raise ValueError("invalid")
    return item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)
def _git_entry_identity(path: Path) -> tuple[int, int, int]:
    item = (path / ".git").lstat()
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode): raise ValueError("invalid")
    return item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)
def _head(boundary: SafeGitBoundary, path: Path) -> str:
    value = boundary.text(["rev-parse", "--verify", "HEAD"], cwd=path).strip()
    if len(value) not in {40,64} or any(char not in "0123456789abcdef" for char in value): raise ValueError("invalid")
    return value
def _worktrees(boundary: SafeGitBoundary, path: Path) -> set[str]:
    return {str(Path(line.removeprefix("worktree ")).resolve()) for line in boundary.text(["worktree","list","--porcelain"], cwd=path).splitlines() if line.startswith("worktree ")}
def _worker_error(result: Any) -> str: return _safe_error(getattr(result, "error_code", "worker_result_invalid")) if getattr(result, "error_code", "") else ""
def _cleanup_error(result: Any) -> str: return _safe_error(getattr(result, "cleanup_error_code", "")) if getattr(result, "cleanup_error_code", "") else ""
def _typed_worker_result_matches(result: Any, sink: _RepositorySink) -> bool: return getattr(result,"pid",None) == sink.pid and getattr(result,"process_start_identity",None) == sink.identity and isinstance(getattr(result,"exit_code",None),int) and getattr(result,"exit_code",1) == 0
def _failure_attempt_status(error: str) -> str: return "cancelled" if error == "worker_cancelled" else "failed_worker"
def _decision_failure_code(previous_status: str) -> str:
    return {
        "workspace_ready": "initial_execution",
        "failed_workspace": "workspace_preparation_failed",
        "interrupted": "worker_interrupted",
        "failed_worker": "worker_failed",
        "failed_verification": "verification_failed",
        "changes_requested": "review_changes_requested",
    }.get(previous_status, "recovery_replan")
def _safe_error(error: object) -> str:
    value = str(error).lower().replace("-", "_")
    return value if value.replace("_", "").isalnum() and len(value) <= 128 else "local_agent_runner_error"
def _public_verification(record: dict[str, object]) -> dict[str, object]:
    return {key: record[key] for key in ("index","returncode","timed_out","cleanup","duration_ms","stdout_sha256","stderr_sha256","side_effect") if key in record}
