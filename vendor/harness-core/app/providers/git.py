from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest
from app.repository_scope import RepositoryScope


_ALLOWED_ACTIONS = frozenset((
    "repo.status.read", "repo.log.read", "repo.diff.read", "git.readonly_smoke",
    "branch.create", "commit.create", "remote.fetch", "git.operation.plan",
    "remote.push", "reset.local", "cherry-pick.local", "merge.local",
))
_LOCAL_OPERATION_ACTIONS = frozenset(("reset.local", "cherry-pick.local", "merge.local"))
REPOSITORY_BOUND_GIT_ACTIONS = frozenset(
    (
        "repo.status.read",
        "repo.log.read",
        "repo.diff.read",
        "branch.create",
        "commit.create",
        "remote.fetch",
        "remote.push",
        "git.operation.plan",
        "reset.local",
        "cherry-pick.local",
        "merge.local",
    )
)
_ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MODE = frozenset(("100644", "100755"))
_PROCESS_BYTE_LIMIT = 65_536
_LOCAL_FILE_LIMIT = 65_536
_SNAPSHOT_FILE_LIMIT = 4_096
_SNAPSHOT_BYTE_LIMIT = 32 * 1024 * 1024
_TRANSACTION_JOURNAL = "harness-transaction.json"
_TRANSACTION_INDEX_BACKUP = "harness-transaction-old-index"
_FETCH_RECOVERY_MARKER = "harness-fetch-recovery.json"
_FETCH_UNPACK_LIMIT = 4_096
_METADATA_COPY_ROOTS = frozenset(("HEAD", "index", "objects", "refs"))
# Standard Git operation markers are expected after a local reset or a
# conflict.  They are read-only evidence for the bounded provider and are not
# copied into an execution snapshot.
_METADATA_IGNORED_ROOTS = frozenset((
    "AUTO_MERGE", "CHERRY_PICK_HEAD", "COMMIT_EDITMSG", "MERGE_HEAD",
    "MERGE_MSG", "ORIG_HEAD", "REVERT_HEAD", "branches", "config", "description",
    "hooks", "info", "logs",
))
_METADATA_FORBIDDEN_ROOTS = frozenset((
    "commondir",
    "config.worktree",
    "packed-refs",
    "reftable",
    "split-index",
    "worktrees",
    _TRANSACTION_JOURNAL,
    _TRANSACTION_INDEX_BACKUP,
    _FETCH_RECOVERY_MARKER,
))
_LOOSE_OBJECT_DIRECTORY = re.compile(r"[0-9a-f]{2}\Z")
_LOOSE_OBJECT_NAME = re.compile(r"[0-9a-f]{38}\Z")
GitFetchTransport = Callable[..., None]
GitPushTransport = Callable[..., None]


class _GitLiveCommandFailure(RuntimeError):
    """A bounded live Git process failed without exposing command output."""

    def __init__(self, reason: str, *, returncode: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.returncode = returncode


class _GitOperationFailure(RuntimeError):
    """A local history operation has a stable, redacted provider reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.provider_reason = reason


@dataclass
class _SnapshotBudget:
    files: int = 0
    bytes: int = 0

    def add_directory(self) -> None:
        self.files += 1
        if self.files > _SNAPSHOT_FILE_LIMIT:
            raise ValueError("git_snapshot_limit_exceeded")

    def add_file(self, size: int) -> None:
        self.files += 1
        if self.files > _SNAPSHOT_FILE_LIMIT:
            raise ValueError("git_snapshot_limit_exceeded")
        self.add_bytes(size)

    def add_bytes(self, size: int) -> None:
        self.bytes += size
        if self.bytes > _SNAPSHOT_BYTE_LIMIT:
            raise ValueError("git_snapshot_limit_exceeded")


@dataclass(frozen=True)
class _SnapshotNode:
    identity: tuple[int, int, int, int, int, int]
    kind: Literal["directory", "file"]


@dataclass(frozen=True)
class _SnapshotManifest:
    nodes: Mapping[tuple[str, ...], _SnapshotNode]
    entries: Mapping[tuple[str, ...], tuple[str, ...]]
    copied_children: Mapping[tuple[str, ...], tuple[str, ...]]


class GitProviderAdapter:
    """Git plumbing constrained to a verified standalone local worktree."""

    def __init__(self, scopes: Mapping[str, RepositoryScope] | None = None, *, fetch_transport: GitFetchTransport | None = None, push_transport: GitPushTransport | None = None, simulated: bool | None = None) -> None:
        supplied = scopes or {}
        if not isinstance(supplied, Mapping):
            raise TypeError("git_scopes_must_be_mapping")
        self._scopes = {alias: scope for alias, scope in supplied.items() if isinstance(alias, str) and isinstance(scope, RepositoryScope) and alias == scope.alias}
        if len(self._scopes) != len(supplied):
            raise ValueError("git_scope_invalid")
        executable = Path("/usr/bin/git")
        if not executable.is_file() or executable.is_symlink():
            raise RuntimeError("git_executable_unavailable")
        self._git_executable = str(executable)
        # Replace refs can alter an object/tree view without changing a normal
        # loose ref.  Do not silently run a weaker command on an older Git.
        try:
            flag_probe = subprocess.run(
                [self._git_executable, "--no-replace-objects", "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
                env={"LC_ALL": "C", "LANG": "C"},
            )
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("git_safety_flags_unavailable") from None
        if flag_probe.returncode != 0:
            raise RuntimeError("git_safety_flags_unavailable")
        if (fetch_transport is not None or push_transport is not None) and simulated is not True:
            raise ValueError("git_simulated_transport_required")
        if fetch_transport is None and push_transport is None and simulated not in {None, False}:
            raise ValueError("git_simulated_transport_required")
        self._fetch_transport = fetch_transport
        self._push_transport = push_transport

    def normalize_target_alias(self, value: object) -> str:
        return self._scope(value).alias

    def normalize_request_target(self, parameters: Mapping[str, object]) -> str:
        if not isinstance(parameters, Mapping):
            raise ValueError("git_parameters_invalid")
        return self._scope(parameters.get("repository_alias")).alias

    def capture_branch_base(self, repository_alias: str) -> str:
        scope = self._scope(repository_alias)
        return self._sha(self._git(scope, ("rev-parse", "HEAD"), 5).stdout)

    def capture_commit_evidence(self, repository_alias: str, *, branch_name: str, file_list: Sequence[str]) -> dict[str, object]:
        scope = self._scope(repository_alias)
        branch = self._branch(scope, branch_name, require_mutable=True)
        paths = self._paths(scope, file_list, required=True)
        parent = self._sha(self._git(scope, ("rev-parse", "HEAD"), 5).stdout)
        if self._current_branch(scope, 5) != branch:
            raise ValueError("git_expected_branch_mismatch")
        return {"expected_parent": parent, "expected_file_blobs": {path: self._file_evidence(scope, path, 5) for path in paths}}

    def render_plan(self, request: ProviderExecutionRequest) -> dict[str, object]:
        action, scope, values = self._validated(request.action, request.parameters)
        change: dict[str, object] = {"field": "read", "after": "no_local_change"}
        if action == "git.operation.plan" or action in _LOCAL_OPERATION_ACTIONS:
            preflight = self._operation_preflight(scope, values)
            return {
                "provider": "git",
                "action": action,
                "target_alias": scope.alias,
                "operation": values["operation"],
                "required_confirmation": values["required_confirmation"],
                "change": values["change"],
                "preflight": preflight,
                "execution_status": (
                    "plan_only"
                    if action == "git.operation.plan"
                    else "awaiting_one_use_authorization"
                ),
            }
        if action == "branch.create":
            change = {"field": "branch", "after": values["branch_name"], "expected_base_sha": values["expected_base_sha"]}
        elif action == "commit.create":
            change = {"field": "commit", "branch": values["branch_name"], "expected_parent": values["expected_parent"], "file_list": values["file_list"], "expected_file_blobs": values["expected_file_blobs"]}
        elif action == "remote.fetch":
            change = {"field": "remote_tracking_ref", "remote_alias": values["remote_alias"], "ref_name": values["ref_name"]}
        elif action == "remote.push":
            change = {
                "field": "remote_ref",
                "remote_alias": values["remote_alias"],
                "source_ref": values["source_ref"],
                "target_ref": values["target_ref"],
                "expected_remote_sha": values["expected_remote_sha"],
                "force": False,
            }
        return {"provider": "git", "action": action, "target_alias": scope.alias, "change": change}

    def execute(self, request: ProviderExecutionRequest, context: ProviderExecutionContext) -> Mapping[str, object]:
        action, scope, values = self._validated(request.action, request.parameters)
        timeout = values["timeout_seconds"]
        if action in {"repo.status.read", "git.readonly_smoke"}:
            raw = self._git(scope, ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-ahead-behind"), timeout).stdout
            return {"source": "git", "action": action, "changed_file_count": len(self._status_paths(raw))}
        if action == "git.operation.plan":
            preflight = self._operation_preflight(scope, values)
            return {
                "source": "git",
                "action": action,
                "operation": values["operation"],
                "required_confirmation": values["required_confirmation"],
                "change": values["change"],
                "preflight": preflight,
                "execution_status": "plan_only",
            }
        if action in _LOCAL_OPERATION_ACTIONS:
            return self._execute_local_operation(scope, values, context)
        if action == "repo.log.read":
            raw = self._git(scope, ("log", "--no-ext-diff", "--no-textconv", f"--max-count={values['limit']}", "--format=%H"), timeout).stdout
            return {"source": "git", "action": action, "commit_count": len([line for line in raw.splitlines() if _SHA.fullmatch(line.decode("ascii", "ignore"))])}
        if action == "repo.diff.read":
            raw = self._git(scope, ("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--numstat", "--", *values["file_list"]), timeout).stdout
            added, deleted, changed = self._numstat(raw)
            return {"source": "git", "action": action, "changed_file_count": changed, "added_lines": added, "deleted_lines": deleted}
        if action == "branch.create":
            base = values["expected_base_sha"]
            self._ensure_clean(scope, timeout)
            if self._sha(self._git(scope, ("rev-parse", "HEAD"), timeout).stdout) != base:
                raise ValueError("git_expected_base_mismatch")
            ref_parts = self._relative_ref(str(values["branch_name"]))
            if self._read_ref(scope, ref_parts, missing=True) is not None:
                raise ValueError("git_branch_already_exists")
            self._publish_ref(scope, ref_parts, base, expected=None)
            context.set_read_back_reference(action, base)
            return {"source": "git", "action": action, "branch": values["branch_name"], "expected_base_sha": base}
        if action == "commit.create":
            return self._commit(scope, values, context)
        if action == "remote.push":
            return self._push(scope, values, context)
        if not context.network_allowed:
            raise PermissionError("git_network_not_allowed")
        remote = values["remote_alias"]
        ref_name = values["ref_name"]
        url = scope.remote_url(remote)
        target = f"refs/remotes/{remote}/{ref_name.removeprefix('refs/heads/')}"
        refspec = f"{ref_name}:{target}"
        dispatch_target = self._fetch_dispatch_target(scope, remote, ref_name)
        # All audit identity checks happen before snapshot construction/Popen.
        # A started process must not discover a target-length failure afterwards.
        context.validate_network_target(dispatch_target)
        self._preflight(scope)
        if self._fetch_transport is not None:
            context.record_network_dispatch(dispatch_target, simulated=True)
            # Simulated transports receive the same no-redirect contract as a
            # live Git process.  They are a test seam, not a way around the
            # configured HTTPS host boundary.
            self._fetch_transport(url=url, refspec=refspec, timeout_seconds=timeout, follow_redirects=False)
            updated = False
        else:
            tracking_parts = self._relative_ref(ref_name, tracking=True, remote=remote)
            before = self._read_ref(scope, tracking_parts, missing=True)
            with self._execution_snapshot(scope) as snapshot:
                before_snapshot = self._git_snapshot(snapshot, ("show-ref", "--verify", "--hash", target), timeout, allow_codes=(0, 1))
                self._git_snapshot(
                    snapshot,
                    ("fetch", "--no-tags", "--no-write-fetch-head", "--no-prune", "--no-recurse-submodules", url, refspec),
                    timeout,
                    on_started=lambda: self._record_live_fetch_dispatch(context, dispatch_target),
                )
                after_snapshot = self._git_snapshot(snapshot, ("show-ref", "--verify", "--hash", target), timeout, allow_codes=(0, 1))
                after = self._sha(after_snapshot.stdout) if after_snapshot.returncode == 0 else None
                snapshot_before = self._sha(before_snapshot.stdout) if before_snapshot.returncode == 0 else None
                updated = snapshot_before != after
                if updated and after is not None:
                    context.mark_local_mutation_unknown()
                    self._copy_new_objects(snapshot / ".git", scope)
                    self._publish_ref(scope, tracking_parts, after, expected=before)
                    context.clear_local_mutation_unknown()
        return {"source": "git", "action": action, "remote_alias": remote, "ref_name": ref_name, "tracking_ref_updated": updated, "execution_provenance": "simulated" if self._fetch_transport is not None else "live"}

    def _push(
        self,
        scope: RepositoryScope,
        values: Mapping[str, object],
        context: ProviderExecutionContext,
    ) -> Mapping[str, object]:
        """Push one immutable, non-force refspec after consuming its plan grant."""

        if not context.authorization_consumed:
            raise PermissionError("git_operation_authorization_required")
        if not context.network_allowed:
            raise PermissionError("git_network_not_allowed")
        preflight = self._operation_preflight(scope, values)
        if preflight["status"] != "needs_remote_evidence":
            blockers = preflight.get("blockers")
            reason = (
                str(blockers[0])
                if isinstance(blockers, Sequence) and blockers
                else "git_push_preflight_blocked"
            )
            raise ValueError(reason)
        remote = str(values["remote_alias"])
        source_ref = str(values["source_ref"])
        target_ref = str(values["target_ref"])
        expected_remote_sha = values["expected_remote_sha"]
        timeout = int(values["timeout_seconds"])
        url = scope.remote_url(remote)
        source_sha = self._sha(
            self._git(scope, ("rev-parse", source_ref), timeout).stdout
        )
        if source_sha != values["expected_head_sha"]:
            raise ValueError("git_push_source_drift")
        refspec = f"{source_ref}:{target_ref}"
        dispatch_target = self._push_dispatch_target(
            scope, remote, source_ref, target_ref
        )
        context.validate_network_target(dispatch_target)

        if self._push_transport is not None:
            context.record_network_dispatch(dispatch_target, simulated=True)
            self._push_transport(
                url=url,
                refspec=refspec,
                timeout_seconds=timeout,
                follow_redirects=False,
            )
            context.set_read_back_reference("remote.push", source_sha)
            return {
                "source": "git",
                "action": "remote.push",
                "remote_alias": remote,
                "target_ref": target_ref,
                "execution_provenance": "simulated",
            }

        observed_before = self._live_remote_ref(
            scope, url, target_ref, timeout, context, dispatch_target
        )
        if observed_before != expected_remote_sha:
            raise ValueError("git_remote_ref_drift")
        try:
            self._invoke_live(
                scope,
                ("push", "--porcelain", "--no-verify", url, refspec),
                timeout,
                on_started=lambda: self._record_live_fetch_dispatch(
                    context, dispatch_target
                ),
            )
        except _GitLiveCommandFailure as exc:
            raise RuntimeError("git_push_dispatch_unknown") from exc
        observed_after = self._live_remote_ref(
            scope, url, target_ref, timeout, context, dispatch_target
        )
        if observed_after != source_sha:
            raise RuntimeError("git_push_readback_unknown")
        context.set_read_back_reference("remote.push", source_sha)
        return {
            "source": "git",
            "action": "remote.push",
            "remote_alias": remote,
            "target_ref": target_ref,
            "execution_provenance": "live",
        }

    def _live_remote_ref(
        self,
        scope: RepositoryScope,
        url: str,
        target_ref: str,
        timeout: int,
        context: ProviderExecutionContext,
        dispatch_target: str,
    ) -> str | None:
        try:
            result = self._invoke_live(
                scope,
                ("ls-remote", "--refs", url, target_ref),
                timeout,
                on_started=lambda: self._record_live_fetch_dispatch(
                    context, dispatch_target
                ),
            )
        except _GitLiveCommandFailure as exc:
            raise RuntimeError("git_remote_readback_unknown") from exc
        lines = result.stdout.decode("ascii", "strict").splitlines()
        if not lines:
            return None
        if len(lines) != 1:
            raise ValueError("git_remote_ref_invalid")
        fields = lines[0].split("\t")
        if len(fields) != 2 or fields[1] != target_ref:
            raise ValueError("git_remote_ref_invalid")
        return self._sha(fields[0].encode("ascii"))

    def verify(self, verifier_action: str, original_write_action: str, request: ProviderExecutionRequest, target_alias: str, context: ProviderExecutionContext) -> Literal["verified_applied", "verified_not_applied", "unknown"]:
        action, scope, values = self._validated(original_write_action, request.parameters)
        if self.normalize_target_alias(target_alias) != scope.alias:
            raise ValueError("git_target_mismatch")
        timeout = values["timeout_seconds"]
        if action == "branch.create" and verifier_action == "repo.status.read":
            actual = self._git(scope, ("show-ref", "--verify", "--hash", f"refs/heads/{values['branch_name']}"), timeout, allow_codes=(0, 1))
            if actual.returncode == 1:
                return "verified_not_applied"
            return "verified_applied" if self._sha(actual.stdout) == values["expected_base_sha"] else "unknown"
        if action == "commit.create" and verifier_action == "repo.log.read":
            receipt = context.read_back_reference(action)
            if not _SHA.fullmatch(receipt):
                return "unknown"
            ref = f"refs/heads/{values['branch_name']}"
            if self._sha(self._git(scope, ("rev-parse", ref), timeout).stdout) != receipt:
                return "unknown"
            parent = self._git(scope, ("show", "-s", "--format=%P", receipt), timeout).stdout.decode("ascii", "ignore").strip()
            tree = self._sha(self._git(scope, ("show", "-s", "--format=%T", receipt), timeout).stdout)
            message = self._git(scope, ("show", "-s", "--format=%B", receipt), timeout).stdout.decode("utf-8", "replace").rstrip("\n")
            if parent != values["expected_parent"] or message != values["message"]:
                return "unknown"
            return "verified_applied" if self._tree_changes_match(scope, values["expected_parent"], tree, values["expected_file_blobs"], timeout) else "unknown"
        if action in _LOCAL_OPERATION_ACTIONS and verifier_action == "repo.status.read":
            receipt_text = context.read_back_reference(action)
            if not receipt_text:
                current_head = self._sha(self._git(scope, ("rev-parse", "HEAD"), timeout).stdout)
                current_branch = self._current_branch(scope, timeout)
                return "verified_not_applied" if (
                    current_head == values["expected_head_sha"]
                    and current_branch == values["branch_name"]
                ) else "unknown"
            try:
                receipt = json.loads(receipt_text)
            except (TypeError, ValueError):
                return "unknown"
            if not isinstance(receipt, Mapping):
                return "unknown"
            current_head = self._sha(self._git(scope, ("rev-parse", "HEAD"), timeout).stdout)
            current_branch = self._current_branch(scope, timeout)
            changed_paths = self._status_paths(
                self._git(
                    scope,
                    ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-ahead-behind"),
                    timeout,
                ).stdout
            )
            if (
                current_head != receipt.get("after_head_sha")
                or current_branch != receipt.get("branch")
                or (receipt.get("require_clean_after") is True and changed_paths)
            ):
                return "unknown"
            return "verified_applied"
        return "unknown"

    def _execute_local_operation(
        self,
        scope: RepositoryScope,
        values: Mapping[str, object],
        context: ProviderExecutionContext,
    ) -> Mapping[str, object]:
        """Execute one explicitly authorized local history operation in-place."""

        operation = str(values["operation"])
        if operation not in _LOCAL_OPERATION_ACTIONS:
            raise PermissionError("git_remote_operation_not_enabled")
        if not context.authorization_consumed:
            raise PermissionError("git_operation_authorization_required")
        timeout = int(values["timeout_seconds"])
        preflight = self._operation_preflight(scope, values)
        if preflight["status"] != "ready":
            blockers = preflight.get("blockers")
            reason = str(blockers[0]) if isinstance(blockers, Sequence) and blockers else "git_preflight_blocked"
            raise ValueError(reason)
        before_head = str(preflight["current_head_sha"])
        branch = str(preflight["current_branch"])
        started = False

        def mark_started() -> None:
            nonlocal started
            started = True
            context.mark_local_mutation_unknown()

        if operation == "reset.local":
            arguments = ("reset", f"--{values['mode']}", str(values["target_sha"]))
        elif operation == "cherry-pick.local":
            arguments = ("cherry-pick", "--no-edit", str(values["commit_sha"]))
        else:
            strategy = "--ff-only" if values["strategy"] == "ff-only" else "--no-ff"
            arguments = ("merge", strategy, "--no-edit", str(values["source_ref"]))
        try:
            self._invoke_live(scope, arguments, timeout, on_started=mark_started)
        except _GitLiveCommandFailure as failure:
            if started and operation in {"cherry-pick.local", "merge.local"} and self._has_unmerged_entries(scope, timeout):
                raise _GitOperationFailure("git_operation_conflict") from None
            raise _GitOperationFailure(failure.reason) from None

        after_head = self._sha(self._git(scope, ("rev-parse", "HEAD"), timeout).stdout)
        after_branch = self._current_branch(scope, timeout)
        changed_paths = self._status_paths(
            self._git(scope, ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-ahead-behind"), timeout).stdout
        )
        require_clean_after = operation != "reset.local" or values["mode"] == "hard"
        if after_branch != branch or (require_clean_after and changed_paths):
            raise _GitOperationFailure("git_operation_readback_failed")
        context.clear_local_mutation_unknown()
        receipt = json.dumps(
            {
                "operation": operation,
                "branch": branch,
                "before_head_sha": before_head,
                "after_head_sha": after_head,
                "require_clean_after": require_clean_after,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        context.set_read_back_reference(operation, receipt)
        return {
            "source": "git",
            "action": operation,
            "operation": operation,
            "before_head_sha": before_head,
            "after_head_sha": after_head,
            "branch": branch,
            "worktree_clean": not changed_paths,
            "execution_status": "applied",
            "write_scope": "local_repository_only",
        }

    def _has_unmerged_entries(self, scope: RepositoryScope, timeout: int) -> bool:
        raw = self._git(
            scope,
            ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-ahead-behind"),
            timeout,
        ).stdout
        return any(record.startswith(b"u ") for record in raw.split(b"\0") if record)

    def _commit(self, scope: RepositoryScope, values: Mapping[str, object], context: ProviderExecutionContext) -> Mapping[str, object]:
        timeout = int(values["timeout_seconds"])
        expected_parent = str(values["expected_parent"])
        evidence = values["expected_file_blobs"]
        branch = str(values["branch_name"])
        for path, expected in evidence.items():
            if self._file_evidence(scope, path, timeout) != expected:
                raise ValueError("git_final_diff_mismatch")
        with self._execution_snapshot(scope) as snapshot:
            if self._current_branch_snapshot(snapshot, scope, timeout) != branch or self._sha(self._git_snapshot(snapshot, ("rev-parse", "HEAD"), timeout).stdout) != expected_parent:
                raise ValueError("git_expected_parent_mismatch")
            status = self._git_snapshot(snapshot, ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-ahead-behind"), timeout).stdout
            if set(self._status_paths(status)) != set(values["file_list"]):
                raise ValueError("git_final_diff_mismatch")
            snapshot_git = snapshot / ".git"
            staged = self._git_snapshot(snapshot, ("diff", "--cached", "--quiet"), timeout, allow_codes=(0, 1))
            if staged.returncode != 0:
                raise ValueError("git_staged_data_not_allowed")
            snapshot_git_fd = os.open(snapshot_git, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                expected_source_index = self._read_relative_file(snapshot_git_fd, ("index",), missing=True)
            finally:
                os.close(snapshot_git_fd)
            index_path = snapshot_git / "harness-prepared-index"
            index_env = {"GIT_INDEX_FILE": str(index_path)}
            self._git_snapshot(snapshot, ("read-tree", expected_parent), timeout, extra_env=index_env)
            for path, expected in sorted(evidence.items()):
                if expected is None:
                    self._git_snapshot(snapshot, ("update-index", "--force-remove", "--", path), timeout, extra_env=index_env)
                    continue
                data, _mode = self._read_scoped_file(scope, path)
                blob = self._sha(self._git_snapshot(snapshot, ("hash-object", "-w", "--stdin"), timeout, extra_env=index_env, input_bytes=data).stdout)
                if blob != expected["blob"]:
                    raise RuntimeError("git_final_diff_mismatch")
                index_record = f"{expected['mode']} {blob}\t{path}".encode("utf-8") + b"\0"
                self._git_snapshot(snapshot, ("update-index", "-z", "--index-info"), timeout, extra_env=index_env, input_bytes=index_record)
            tree = self._sha(self._git_snapshot(snapshot, ("write-tree",), timeout, extra_env=index_env).stdout)
            commit_env = {"GIT_AUTHOR_NAME": "Harness", "GIT_AUTHOR_EMAIL": "harness@localhost", "GIT_COMMITTER_NAME": "Harness", "GIT_COMMITTER_EMAIL": "harness@localhost"}
            commit = self._sha(self._git_snapshot(snapshot, ("commit-tree", tree, "-p", expected_parent, "-m", str(values["message"])), timeout, extra_env=commit_env).stdout)
            snapshot_git_fd = os.open(snapshot_git, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                prepared_index = self._read_relative_file(snapshot_git_fd, ("harness-prepared-index",))
            finally:
                os.close(snapshot_git_fd)
            if prepared_index is None:
                raise RuntimeError("git_publish_unknown")
            self._publish_commit_transaction(scope, branch, expected_parent, commit, prepared_index, expected_source_index, snapshot_git)
        context.set_read_back_reference("commit.create", commit)
        return {"source": "git", "action": "commit.create", "commit_sha": commit}

    def _validated(self, action_value: object, parameters: Mapping[str, object]) -> tuple[str, RepositoryScope, dict[str, object]]:
        if not isinstance(action_value, str) or action_value not in _ALLOWED_ACTIONS or not isinstance(parameters, Mapping):
            raise ValueError("git_action_not_allowed")
        action = action_value
        allowed = {"repository_alias", "timeout_seconds"}
        if action == "repo.log.read": allowed.add("limit")
        if action == "repo.diff.read": allowed.add("file_list")
        if action == "branch.create": allowed.update(("branch_name", "expected_base_sha"))
        if action == "commit.create": allowed.update(("branch_name", "expected_parent", "file_list", "expected_file_blobs", "message"))
        if action == "remote.fetch": allowed.update(("remote_alias", "ref_name"))
        if action == "remote.push": allowed.update(("branch_name", "expected_head_sha", "remote_alias", "source_ref", "target_ref", "expected_remote_sha", "force"))
        if action == "git.operation.plan":
            allowed.update(("operation", "branch_name", "expected_head_sha", "target_sha", "mode", "allow_dirty", "commit_sha", "allow_conflict", "source_ref", "strategy", "remote_alias", "ref_name", "target_ref", "expected_remote_sha", "force"))
        if action == "reset.local":
            allowed.update(("branch_name", "expected_head_sha", "target_sha", "mode", "allow_dirty"))
        if action == "cherry-pick.local":
            allowed.update(("branch_name", "expected_head_sha", "commit_sha", "allow_conflict"))
        if action == "merge.local":
            allowed.update(("branch_name", "expected_head_sha", "source_ref", "strategy", "allow_conflict"))
        if set(parameters) - allowed or "repository_alias" not in parameters:
            raise ValueError("git_parameters_invalid")
        scope = self._scope(parameters["repository_alias"])
        values: dict[str, object] = {"timeout_seconds": self._timeout(parameters)}
        if action == "repo.log.read":
            value = parameters.get("limit", 20)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100: raise ValueError("git_parameters_invalid")
            values["limit"] = value
        if action in {"repo.diff.read", "commit.create"}:
            values["file_list"] = self._paths(scope, parameters.get("file_list"), required=action == "commit.create")
        if action == "branch.create":
            values["branch_name"] = self._branch(scope, parameters.get("branch_name"), require_mutable=True)
            values["expected_base_sha"] = self._sha_value(parameters.get("expected_base_sha"))
        if action == "commit.create":
            values["branch_name"] = self._branch(scope, parameters.get("branch_name"), require_mutable=True)
            values["expected_parent"] = self._sha_value(parameters.get("expected_parent"))
            values["expected_file_blobs"] = self._evidence(scope, values["file_list"], parameters.get("expected_file_blobs"))
            values["message"] = self._message(parameters.get("message"))
        if action == "remote.fetch":
            remote = parameters.get("remote_alias")
            if not isinstance(remote, str) or _ALIAS.fullmatch(remote) is None: raise ValueError("git_remote_invalid")
            scope.remote_url(remote)
            ref = self._validate_ref_name(parameters.get("ref_name"), require_heads_prefix=True)
            values["remote_alias"], values["ref_name"] = remote, ref
        if action == "remote.push":
            values.update(
                self._validate_operation_plan(
                    {**parameters, "operation": "remote.push"}, scope
                )
            )
            values["operation"] = "remote.push"
        if action == "git.operation.plan":
            values.update(self._validate_operation_plan(parameters, scope))
            values["operation"] = parameters["operation"]
        if action in _LOCAL_OPERATION_ACTIONS:
            values.update(
                self._validate_operation_plan(
                    {**parameters, "operation": action}, scope
                )
            )
            values["operation"] = action
        return action, scope, values

    def _validate_operation_plan(self, parameters: Mapping[str, object], scope: RepositoryScope) -> dict[str, object]:
        operation = parameters.get("operation")
        if operation not in {"reset.local", "cherry-pick.local", "merge.local", "remote.pull", "remote.push"}:
            raise ValueError("git_operation_not_allowed")
        expected_head = self._sha_value(parameters.get("expected_head_sha"))
        branch = self._branch(scope, parameters.get("branch_name"), require_mutable=True)
        common = {"branch_name": branch, "expected_head_sha": expected_head}
        if operation == "reset.local":
            target = self._sha_value(parameters.get("target_sha"))
            mode = parameters.get("mode")
            if mode not in {"soft", "mixed", "hard"}:
                raise ValueError("git_reset_mode_invalid")
            if parameters.get("allow_dirty", False) is not False:
                raise ValueError("git_dirty_override_forbidden")
            return {
                **common, "target_sha": target, "mode": mode, "allow_dirty": False,
                "required_confirmation": "确认移动本地分支指针；Harness 只生成计划，不直接改工作区。",
                "change": {"field": "branch_ref", "branch": branch, "before": expected_head, "after": target, "mode": mode, "worktree": "requires_explicit_apply"},
            }
        if operation == "cherry-pick.local":
            commit = self._sha_value(parameters.get("commit_sha"))
            if parameters.get("allow_conflict", False) is not False:
                raise ValueError("git_conflict_override_forbidden")
            return {
                **common, "commit_sha": commit, "allow_conflict": False,
                "required_confirmation": "确认在目标分支创建 cherry-pick 提交；冲突必须停止。",
                "change": {"field": "branch_ref", "branch": branch, "before": expected_head, "commit": commit, "conflict_policy": "stop"},
            }
        if operation == "merge.local":
            source = self._validate_operation_ref(parameters.get("source_ref"))
            strategy = parameters.get("strategy")
            if strategy not in {"ff-only", "no-ff"}:
                raise ValueError("git_merge_strategy_invalid")
            if parameters.get("allow_conflict", False) is not False:
                raise ValueError("git_conflict_override_forbidden")
            return {
                **common, "source_ref": source, "strategy": strategy, "allow_conflict": False,
                "required_confirmation": "确认合并指定来源；冲突必须停止，不自动解决。",
                "change": {"field": "branch_ref", "branch": branch, "before": expected_head, "source_ref": source, "strategy": strategy, "conflict_policy": "stop"},
            }
        remote = parameters.get("remote_alias")
        if not isinstance(remote, str) or _ALIAS.fullmatch(remote) is None:
            raise ValueError("git_remote_invalid")
        scope.remote_url(remote)
        if operation == "remote.pull":
            ref_name = self._validate_ref_name(parameters.get("ref_name"), require_heads_prefix=True)
            strategy = parameters.get("strategy")
            if strategy not in {"ff-only", "no-ff"}:
                raise ValueError("git_merge_strategy_invalid")
            return {
                **common, "remote_alias": remote, "ref_name": ref_name, "strategy": strategy,
                "required_confirmation": "确认从指定远端拉取并合并；网络失败或冲突均停止。",
                "change": {"field": "branch_ref_and_tracking_ref", "branch": branch, "remote_alias": remote, "ref_name": ref_name, "strategy": strategy, "conflict_policy": "stop"},
            }
        source_ref = self._validate_operation_ref(parameters.get("source_ref"))
        target_ref = self._validate_operation_ref(parameters.get("target_ref"))
        expected_remote_value = parameters.get("expected_remote_sha")
        expected_remote = (
            None
            if expected_remote_value is None
            else self._sha_value(expected_remote_value)
        )
        force = parameters.get("force", False)
        if force is not False:
            raise ValueError("git_force_push_forbidden")
        return {
            **common, "remote_alias": remote, "source_ref": source_ref, "target_ref": target_ref,
            "expected_remote_sha": expected_remote, "force": False,
            "required_confirmation": "确认向指定远端 ref 推送；Harness 禁止 force push。",
            "change": {"field": "remote_ref", "remote_alias": remote, "source_ref": source_ref, "target_ref": target_ref, "expected_remote_sha": expected_remote, "force": False},
        }

    def _operation_preflight(self, scope: RepositoryScope, values: Mapping[str, object]) -> dict[str, object]:
        """Read the exact local state needed before a history-changing plan."""
        timeout = int(values["timeout_seconds"])
        operation = str(values["operation"])
        current_head = self._sha(self._git(scope, ("rev-parse", "HEAD"), timeout).stdout)
        current_branch = self._current_branch(scope, timeout)
        changed_paths = self._status_paths(
            self._git(
                scope,
                ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-ahead-behind"),
                timeout,
            ).stdout
        )
        blockers: list[str] = []
        if current_head != values["expected_head_sha"]:
            blockers.append("git_expected_head_drift")
        if current_branch != values["branch_name"]:
            blockers.append("git_expected_branch_mismatch")
        if changed_paths:
            blockers.append("git_worktree_not_clean")

        target_ref: str | None = None
        if operation == "reset.local":
            target_ref = str(values["target_sha"])
        elif operation == "cherry-pick.local":
            target_ref = str(values["commit_sha"])
        elif operation == "merge.local":
            target_ref = str(values["source_ref"])
        elif operation == "remote.push":
            target_ref = str(values["source_ref"])

        target_exists: bool | None = None
        if target_ref is not None:
            target_exists = self._commit_ref_exists(scope, target_ref, timeout)
            if not target_exists:
                blockers.append("git_target_commit_missing")

        remote_state = "not_applicable"
        preflight_status = "ready" if not blockers else "blocked"
        if operation in {"remote.pull", "remote.push"} and not blockers:
            remote_state = "requires_remote_readback"
            preflight_status = "needs_remote_evidence"

        return {
            "status": preflight_status,
            "current_head_sha": current_head,
            "current_branch": current_branch,
            "worktree_clean": not changed_paths,
            "changed_file_count": len(changed_paths),
            "target_commit_exists": target_exists,
            "remote_state": remote_state,
            "blockers": blockers,
        }

    def _commit_ref_exists(self, scope: RepositoryScope, ref: str, timeout: int) -> bool:
        result = self._git(
            scope,
            ("rev-parse", "--verify", f"{ref}^{{commit}}"),
            timeout,
            allow_codes=(0, 1),
        )
        return result.returncode == 0 and bool(_SHA.fullmatch(result.stdout.decode("ascii", "ignore").strip()))

    @staticmethod
    def _validate_operation_ref(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("git_refspec_not_allowed")
        if value.startswith("refs/remotes/"):
            parts = value.split("/")
            if len(parts) < 4 or _ALIAS.fullmatch(parts[2]) is None:
                raise ValueError("git_refspec_not_allowed")
            GitProviderAdapter._validate_ref_name("/".join(parts[3:]), require_heads_prefix=False)
            return value
        if value.startswith("refs/heads/"):
            return GitProviderAdapter._validate_ref_name(value, require_heads_prefix=True)
        raise ValueError("git_refspec_not_allowed")

    def _scope(self, value: object) -> RepositoryScope:
        if not isinstance(value, str) or _ALIAS.fullmatch(value) is None or value not in self._scopes: raise ValueError("git_repository_alias_invalid")
        return self._scopes[value]

    @staticmethod
    def _timeout(parameters: Mapping[str, object]) -> int:
        value = parameters.get("timeout_seconds", 5)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 30: raise ValueError("git_parameters_invalid")
        return value

    def _branch(self, scope: RepositoryScope, value: object, *, require_mutable: bool) -> str:
        try:
            branch = self._validate_ref_name(value, require_heads_prefix=False)
        except ValueError:
            raise ValueError("git_branch_not_allowed") from None
        if branch.startswith("-") or (require_mutable and not scope.branch_allowed(branch)):
            raise ValueError("git_branch_not_allowed")
        return branch

    @staticmethod
    def _validate_ref_name(value: object, *, require_heads_prefix: bool) -> str:
        """Accept the intentionally narrow, Git-safe ref grammar used by Harness.

        The adapter only supports bounded ASCII refs.  That keeps the rendered
        plan, authorization hash, filesystem ref path, and fetch refspec on the
        same deterministic boundary while enforcing every component rule that
        Git's ``check-ref-format`` applies to these operations.
        """

        if not isinstance(value, str) or not value or len(value) > 80:
            raise ValueError("git_refspec_not_allowed")
        if (
            not value.isascii()
            or any(ord(character) <= 32 or ord(character) == 127 for character in value)
            or any(character in "~^:?*[\\" for character in value)
            or "@{" in value
            or value == "@"
            or value.startswith("/")
            or value.endswith("/")
            or "//" in value
            or ".." in value
        ):
            raise ValueError("git_refspec_not_allowed")
        components = value.split("/")
        if require_heads_prefix:
            if components[:2] != ["refs", "heads"] or len(components) == 2:
                raise ValueError("git_refspec_not_allowed")
            components = components[2:]
        for component in components:
            if (
                not component
                or component in {".", "..", "@"}
                or component.startswith(".")
                or component.endswith(".")
                or component.lower().endswith(".lock")
            ):
                raise ValueError("git_refspec_not_allowed")
        return value

    @classmethod
    def _validate_ref_parts(cls, parts: Sequence[str]) -> tuple[str, ...]:
        """Reconstruct and validate a ref immediately before local publish."""

        values = tuple(parts)
        if values[:2] == ("refs", "heads"):
            cls._validate_ref_name("/".join(values[2:]), require_heads_prefix=False)
        elif len(values) >= 4 and values[:2] == ("refs", "remotes") and _ALIAS.fullmatch(values[2]) is not None:
            cls._validate_ref_name("/".join(values[3:]), require_heads_prefix=False)
        else:
            raise ValueError("git_refspec_not_allowed")
        return values

    @staticmethod
    def _sha_value(value: object) -> str:
        if not isinstance(value, str) or _SHA.fullmatch(value) is None: raise ValueError("git_expected_sha_invalid")
        return value

    @staticmethod
    def _message(value: object) -> str:
        if not isinstance(value, str) or not value or value != value.strip() or len(value.encode("utf-8")) > 256 or any(ord(char) < 32 and char not in "\n\t" for char in value): raise ValueError("git_commit_message_invalid")
        return value

    @staticmethod
    def _paths(scope: RepositoryScope, value: object, *, required: bool) -> tuple[str, ...]:
        if value is None and not required: return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value or len(value) > 64: raise ValueError("git_paths_invalid")
        paths = tuple(scope.relative_path(item) for item in value)
        if len(set(paths)) != len(paths): raise ValueError("git_paths_invalid")
        return paths

    def _evidence(self, scope: RepositoryScope, paths: Sequence[str], value: object) -> dict[str, dict[str, str] | None]:
        if not isinstance(value, Mapping) or set(value) != set(paths): raise ValueError("git_final_evidence_invalid")
        result: dict[str, dict[str, str] | None] = {}
        for path in paths:
            item = value[path]
            if item is None:
                result[path] = None
            elif isinstance(item, Mapping) and set(item) == {"blob", "mode"} and isinstance(item["blob"], str) and _SHA.fullmatch(item["blob"]) and item["mode"] in _MODE:
                result[path] = {"blob": item["blob"], "mode": item["mode"]}
            else: raise ValueError("git_final_evidence_invalid")
        return result

    def _file_evidence(self, scope: RepositoryScope, path: str, timeout: int) -> dict[str, str] | None:
        data_and_mode = self._read_scoped_file(scope, path, missing=True)
        if data_and_mode is None:
            return None
        data, mode = data_and_mode
        return {"blob": self._blob_sha(data), "mode": mode}

    @classmethod
    def _read_scoped_file(cls, scope: RepositoryScope, path: str, *, missing: bool = False) -> tuple[bytes, str] | None:
        """Read one reviewed worktree file through the scope's root dirfd.

        The pathname is never opened directly.  Each path component is anchored
        with ``openat(O_NOFOLLOW)`` and the final regular-file identity is
        checked both before and after a bounded read.  ``O_NONBLOCK`` prevents
        a regular-file-to-FIFO replacement from stalling a worker.
        """

        try:
            parts = tuple(Path(path).parts)
        except TypeError:
            raise ValueError("git_file_not_allowed") from None
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("git_file_not_allowed")
        root_fd = scope.open_root_fd()
        fd = root_fd
        try:
            for part in parts[:-1]:
                checked = os.stat(part, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISDIR(checked.st_mode):
                    raise ValueError("git_file_not_allowed")
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                if cls._snapshot_identity(os.fstat(next_fd)) != cls._snapshot_identity(checked):
                    os.close(next_fd)
                    raise ValueError("git_file_not_allowed")
                os.close(fd); fd = next_fd
            try:
                checked_file = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                if missing:
                    return None
                raise ValueError("git_file_not_allowed") from None
            if not stat.S_ISREG(checked_file.st_mode) or checked_file.st_size > _PROCESS_BYTE_LIMIT:
                raise ValueError("git_file_not_allowed")
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                file_stat = os.fstat(file_fd)
                if cls._snapshot_identity(file_stat) != cls._snapshot_identity(checked_file):
                    raise ValueError("git_file_not_allowed")
                data = bytearray()
                while len(data) <= _PROCESS_BYTE_LIMIT:
                    block = os.read(file_fd, min(8192, _PROCESS_BYTE_LIMIT + 1 - len(data)))
                    if not block: break
                    data.extend(block)
                if len(data) > _PROCESS_BYTE_LIMIT:
                    raise ValueError("git_file_not_allowed")
                if cls._snapshot_identity(os.fstat(file_fd)) != cls._snapshot_identity(checked_file):
                    raise ValueError("git_file_not_allowed")
                return bytes(data), "100755" if file_stat.st_mode & 0o111 else "100644"
            finally:
                os.close(file_fd)
        except OSError:
            raise ValueError("git_file_not_allowed") from None
        finally:
            os.close(fd)

    @staticmethod
    def _blob_sha(data: bytes) -> str:
        return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()

    def _current_branch(self, scope: RepositoryScope, timeout: int) -> str:
        raw = self._git(scope, ("symbolic-ref", "--quiet", "--short", "HEAD"), timeout).stdout
        return self._branch(scope, raw.decode("ascii", "ignore").strip(), require_mutable=True)

    def _current_branch_snapshot(self, snapshot: Path, scope: RepositoryScope, timeout: int) -> str:
        raw = self._git_snapshot(snapshot, ("symbolic-ref", "--quiet", "--short", "HEAD"), timeout).stdout
        return self._branch(scope, raw.decode("ascii", "ignore").strip(), require_mutable=True)

    def _ensure_clean(self, scope: RepositoryScope, timeout: int) -> None:
        if self._status_paths(self._git(scope, ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--no-ahead-behind"), timeout).stdout):
            raise ValueError("git_worktree_not_clean")

    def _ref_value(self, scope: RepositoryScope, ref: str, timeout: int) -> str | None:
        result = self._git(scope, ("show-ref", "--verify", "--hash", ref), timeout, allow_codes=(0, 1))
        return self._sha(result.stdout) if result.returncode == 0 else None

    def _preflight(self, scope: RepositoryScope) -> _SnapshotManifest:
        """Capture a bounded, anchored source metadata proof before copying.

        The returned manifest is retained through snapshot construction.  It is
        intentionally more than a pathname preflight: a transient ``commondir``
        or ``alternates`` injection changes an anchored directory identity and
        prevents a Git child from starting even if the attacker restores the
        pathname before the final check.
        """

        scope.assert_identity()
        git_fd = scope.open_git_fd()
        try:
            manifest = self._capture_snapshot_manifest(
                git_fd,
                skipped=set(),
                reject_attributes=True,
                budget=_SnapshotBudget(),
                metadata=True,
            )
            self._reject_unsafe_repository_configuration(git_fd)
            self._assert_manifest_stable(git_fd, manifest)
            return manifest
        finally:
            os.close(git_fd)

    @staticmethod
    def _fetch_dispatch_target(scope: RepositoryScope, remote: str, ref_name: str) -> str:
        """Return a bounded, non-sensitive audit target for one fetch attempt."""

        material = "\0".join((scope.alias, remote, ref_name)).encode("ascii")
        return "git.dispatch." + hashlib.sha256(material).hexdigest()

    @staticmethod
    def _push_dispatch_target(
        scope: RepositoryScope, remote: str, source_ref: str, target_ref: str
    ) -> str:
        """Return a bounded, non-sensitive audit target for one push plan."""

        material = "\0".join(
            (scope.alias, remote, source_ref, target_ref)
        ).encode("ascii")
        return "git.dispatch." + hashlib.sha256(material).hexdigest()

    @staticmethod
    def _record_live_fetch_dispatch(context: ProviderExecutionContext, target_alias: str) -> None:
        try:
            context.record_network_dispatch(target_alias, simulated=False)
        except Exception:
            # `target_alias` was checked before Popen.  This is consequently an
            # audit-storage incident, not a bad target or a zero-dispatch fetch.
            context.record_network_dispatch_incident(target_alias)
            raise RuntimeError("git_network_dispatch_audit_unknown") from None

    @contextlib.contextmanager
    def _execution_snapshot(self, scope: RepositoryScope):
        """Build the only directory from which a Git child process may run.

        The source repository is evidence and a later publication target only.  A
        source config/attributes pathname is consequently never re-opened by a
        Git process after validation.
        """

        metadata_manifest = self._preflight(scope)
        with tempfile.TemporaryDirectory(prefix="harness-git-snapshot-") as directory:
            snapshot = Path(directory) / "repo"
            self._copy_snapshot(scope, snapshot, metadata_manifest=metadata_manifest)
            # A source replacement during the copy invalidates the operation;
            # the snapshot is discarded before a child process starts.
            self._preflight(scope)
            yield snapshot

    def _copy_snapshot(self, scope: RepositoryScope, destination: Path, *, metadata_manifest: _SnapshotManifest | None = None) -> None:
        """Copy a stable, symlink-free source view from anchored descriptors.

        The configured repository path is intentionally not used below.  The
        manifest is captured through the scope's already-bound root/git dirfds,
        then every source file is re-opened by ``openat(O_NOFOLLOW)`` and
        compared with that manifest while it is copied.  This prevents a source
        directory replacement from silently changing the snapshot's contents.
        """

        try:
            destination.mkdir(mode=0o700)
            destination_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            raise ValueError("git_snapshot_source_invalid") from None
        root_fd = git_fd = snapshot_git_fd = -1
        try:
            root_fd = scope.open_root_fd()
            git_fd = scope.open_git_fd()
            capture_budget = _SnapshotBudget()
            worktree_manifest = self._capture_snapshot_manifest(
                root_fd,
                skipped={".git"},
                reject_attributes=True,
                budget=capture_budget,
            )
            if metadata_manifest is None:
                metadata_manifest = self._capture_snapshot_manifest(
                    git_fd,
                    skipped=set(),
                    reject_attributes=True,
                    budget=capture_budget,
                    metadata=True,
                )
            budget = _SnapshotBudget()
            self._copy_manifested_tree(root_fd, destination_fd, worktree_manifest, (), budget)
            os.mkdir(".git", 0o700, dir_fd=destination_fd)
            snapshot_git_fd = os.open(".git", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination_fd)
            # The snapshot never inherits the source config/hooks/attributes.
            self._copy_manifested_tree(
                git_fd,
                snapshot_git_fd,
                metadata_manifest,
                (),
                budget,
                copy_predicate=self._metadata_snapshot_copy_allowed,
            )
            self._write_snapshot_config(snapshot_git_fd)
            self._assert_snapshot_tree(destination_fd)
            self._validate_snapshot_metadata(destination_fd)
            self._assert_manifest_stable(git_fd, metadata_manifest)
        except OSError:
            raise ValueError("git_snapshot_source_invalid") from None
        finally:
            if snapshot_git_fd >= 0:
                os.close(snapshot_git_fd)
            if git_fd >= 0:
                os.close(git_fd)
            if root_fd >= 0:
                os.close(root_fd)
            os.close(destination_fd)

    @staticmethod
    def _snapshot_identity(item: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )

    @classmethod
    def _directory_names(cls, directory_fd: int, *, budget: _SnapshotBudget | None = None) -> tuple[str, ...]:
        try:
            with os.scandir(os.dup(directory_fd)) as entries:
                names: list[str] = []
                for entry in entries:
                    if budget is not None:
                        budget.add_file(0)
                    names.append(entry.name)
                return tuple(sorted(names))
        except OSError:
            raise ValueError("git_snapshot_source_invalid") from None

    @classmethod
    def _capture_snapshot_manifest(
        cls,
        root_fd: int,
        *,
        skipped: set[str],
        reject_attributes: bool,
        budget: _SnapshotBudget,
        metadata: bool = False,
    ) -> _SnapshotManifest:
        nodes: dict[tuple[str, ...], _SnapshotNode] = {}
        entries: dict[tuple[str, ...], tuple[str, ...]] = {}
        copied_children: dict[tuple[str, ...], tuple[str, ...]] = {}

        def capture(directory_fd: int, relative: tuple[str, ...]) -> None:
            directory_stat = os.fstat(directory_fd)
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise ValueError("git_snapshot_source_invalid")
            budget.add_directory()
            nodes[relative] = _SnapshotNode(cls._snapshot_identity(directory_stat), "directory")
            names = cls._directory_names(directory_fd, budget=budget)
            entries[relative] = names
            copied: list[str] = []
            for name in names:
                child_relative = (*relative, name)
                if reject_attributes and name == ".gitattributes":
                    raise ValueError("git_unsafe_repository_configuration")
                if reject_attributes and child_relative == ("info", "attributes"):
                    raise ValueError("git_unsafe_repository_configuration")
                try:
                    checked_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if metadata:
                        cls._validate_metadata_capture_entry(child_relative, checked_stat)
                    if not (stat.S_ISDIR(checked_stat.st_mode) or stat.S_ISREG(checked_stat.st_mode)):
                        raise ValueError("git_snapshot_source_invalid")
                    if stat.S_ISREG(checked_stat.st_mode):
                        budget.add_bytes(checked_stat.st_size)
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_DIRECTORY if stat.S_ISDIR(checked_stat.st_mode) else 0)
                    child_fd = os.open(name, flags, dir_fd=directory_fd)
                except OSError:
                    raise ValueError("git_snapshot_source_invalid") from None
                try:
                    child_stat = os.fstat(child_fd)
                    if cls._snapshot_identity(child_stat) != cls._snapshot_identity(checked_stat):
                        raise ValueError("git_snapshot_source_changed")
                    if stat.S_ISDIR(child_stat.st_mode):
                        if name not in skipped:
                            copied.append(name)
                            capture(child_fd, child_relative)
                    elif stat.S_ISREG(child_stat.st_mode):
                        if name not in skipped:
                            nodes[child_relative] = _SnapshotNode(cls._snapshot_identity(child_stat), "file")
                            copied.append(name)
                    else:
                        raise ValueError("git_snapshot_source_invalid")
                finally:
                    os.close(child_fd)
            copied_children[relative] = tuple(copied)

        capture(root_fd, ())
        return _SnapshotManifest(nodes=nodes, entries=entries, copied_children=copied_children)

    @staticmethod
    def _metadata_snapshot_copy_allowed(relative: tuple[str, ...], node: _SnapshotNode) -> bool:
        """Copy only the storage Git needs in the private execution snapshot."""

        if not relative:
            return False
        first = relative[0]
        if first in {"HEAD", "index"}:
            return len(relative) == 1 and node.kind == "file"
        if first == "refs":
            if len(relative) == 1:
                return node.kind == "directory"
            if relative[1] == "heads":
                return node.kind in {"directory", "file"}
            if relative[1] == "remotes":
                return node.kind in {"directory", "file"}
            # Empty stock refs/tags is allowed in source proof but is never
            # copied into the execution snapshot.
            return False
        if first != "objects":
            return False
        if len(relative) == 1:
            return node.kind == "directory"
        if len(relative) == 2:
            return node.kind == "directory" and _LOOSE_OBJECT_DIRECTORY.fullmatch(relative[1]) is not None
        return len(relative) == 3 and node.kind == "file" and _LOOSE_OBJECT_DIRECTORY.fullmatch(relative[1]) is not None and _LOOSE_OBJECT_NAME.fullmatch(relative[2]) is not None

    @classmethod
    def _validate_metadata_capture_entry(cls, relative: tuple[str, ...], item: os.stat_result) -> None:
        """Reject unsupported Git storage while it is being manifested.

        This is deliberately a layout allowlist, not a best-effort copy skip.
        A supported source has loose objects and loose refs only; linked/shared
        worktrees, packs, reftables, alternates and transaction residue never
        enter the snapshot proof in the first place.
        """

        name = relative[-1]
        parent = relative[:-1]
        is_directory = stat.S_ISDIR(item.st_mode)
        is_file = stat.S_ISREG(item.st_mode)
        if not (is_directory or is_file):
            raise ValueError("git_unsafe_repository_metadata")
        if not parent:
            if name in {_TRANSACTION_JOURNAL, _TRANSACTION_INDEX_BACKUP, _FETCH_RECOVERY_MARKER}:
                raise ValueError("git_transaction_recovery_required")
            if name == "index.lock":
                # A concurrent ordinary Git writer is not malformed metadata;
                # surface the existing retryable publication boundary instead.
                raise ValueError("git_publish_lock_unavailable")
            if name in _METADATA_FORBIDDEN_ROOTS or name.startswith("sharedindex."):
                raise ValueError("git_unsafe_repository_metadata")
            if name not in _METADATA_COPY_ROOTS | _METADATA_IGNORED_ROOTS:
                raise ValueError("git_unsafe_repository_metadata")
            expected_directory = name in {"objects", "refs", "branches", "hooks", "info", "logs"}
            if is_directory != expected_directory:
                raise ValueError("git_unsafe_repository_metadata")
            return
        if parent == ("objects",):
            if name in {"info", "pack"}:
                if not is_directory:
                    raise ValueError("git_unsafe_repository_metadata")
                return
            if not is_directory or _LOOSE_OBJECT_DIRECTORY.fullmatch(name) is None:
                raise ValueError("git_unsafe_repository_metadata")
            return
        if parent in {("objects", "info"), ("objects", "pack")}:
            # Empty stock info/pack directories are harmless; any payload would
            # be an alternate store or packed-object configuration we cannot
            # prove safe inside this lightweight provider.
            raise ValueError("git_unsafe_repository_metadata")
        if len(parent) == 2 and parent[0] == "objects" and _LOOSE_OBJECT_DIRECTORY.fullmatch(parent[1]) is not None:
            if not is_file or _LOOSE_OBJECT_NAME.fullmatch(name) is None:
                raise ValueError("git_unsafe_repository_metadata")
            return
        if parent and parent[0] == "objects":
            raise ValueError("git_unsafe_repository_metadata")
        if parent == ("refs",):
            if name not in {"heads", "remotes", "tags"} or not is_directory:
                raise ValueError("git_unsafe_repository_metadata")
            return
        if relative[:2] == ("refs", "tags"):
            # Git init may create the empty stock directory.  A tag payload is
            # neither needed for these actions nor safe to import into the
            # private execution repository.
            raise ValueError("git_unsafe_repository_metadata")
        if relative[:2] == ("refs", "heads"):
            cls._validate_ref_name("/".join(relative[2:]), require_heads_prefix=False)
            return
        if relative[:2] == ("refs", "remotes"):
            if len(relative) == 2:
                if not is_directory:
                    raise ValueError("git_unsafe_repository_metadata")
                return
            if _ALIAS.fullmatch(relative[2]) is None:
                raise ValueError("git_unsafe_repository_metadata")
            if len(relative) == 3 and not is_directory:
                raise ValueError("git_unsafe_repository_metadata")
            if len(relative) > 3:
                cls._validate_ref_name("/".join(relative[3:]), require_heads_prefix=False)
            return
        if parent and parent[0] == "refs":
            raise ValueError("git_unsafe_repository_metadata")

    @classmethod
    def _assert_manifest_stable(cls, directory_fd: int, manifest: _SnapshotManifest, relative: tuple[str, ...] = ()) -> None:
        node = manifest.nodes.get(relative)
        if node is None or node.kind != "directory":
            raise ValueError("git_snapshot_source_invalid")
        cls._assert_snapshot_node(directory_fd, node)
        if cls._directory_names(directory_fd) != manifest.entries.get(relative):
            raise ValueError("git_snapshot_source_changed")
        for name in manifest.entries.get(relative, ()):
            child_relative = (*relative, name)
            child = manifest.nodes.get(child_relative)
            if child is None:
                raise ValueError("git_snapshot_source_invalid")
            cls._assert_snapshot_entry(directory_fd, name, child)
            if child.kind != "directory":
                continue
            try:
                child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            except OSError:
                raise ValueError("git_snapshot_source_changed") from None
            try:
                cls._assert_manifest_stable(child_fd, manifest, child_relative)
            finally:
                os.close(child_fd)
        cls._assert_snapshot_node(directory_fd, node)

    @classmethod
    def _assert_snapshot_node(cls, descriptor: int, node: _SnapshotNode) -> None:
        if cls._snapshot_identity(os.fstat(descriptor)) != node.identity:
            raise ValueError("git_snapshot_source_changed")

    @classmethod
    def _assert_snapshot_entry(cls, directory_fd: int, name: str, node: _SnapshotNode) -> None:
        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            raise ValueError("git_snapshot_source_changed") from None
        if cls._snapshot_identity(current) != node.identity:
            raise ValueError("git_snapshot_source_changed")

    @classmethod
    def _copy_manifested_tree(
        cls,
        source_fd: int,
        destination_fd: int,
        manifest: _SnapshotManifest,
        relative: tuple[str, ...],
        budget: _SnapshotBudget,
        *,
        copy_predicate: Callable[[tuple[str, ...], _SnapshotNode], bool] | None = None,
    ) -> None:
        directory_node = manifest.nodes.get(relative)
        if directory_node is None or directory_node.kind != "directory":
            raise ValueError("git_snapshot_source_invalid")
        cls._assert_snapshot_node(source_fd, directory_node)
        if cls._directory_names(source_fd) != manifest.entries.get(relative):
            raise ValueError("git_snapshot_source_changed")
        for name in manifest.copied_children.get(relative, ()):
            child_relative = (*relative, name)
            child_node = manifest.nodes.get(child_relative)
            if child_node is None:
                raise ValueError("git_snapshot_source_invalid")
            if copy_predicate is not None and not copy_predicate(child_relative, child_node):
                continue
            child_fd = cls._open_manifested_child(source_fd, name, child_node)
            try:
                cls._assert_snapshot_node(child_fd, child_node)
                if child_node.kind == "directory":
                    budget.add_directory()
                    os.mkdir(name, 0o700, dir_fd=destination_fd)
                    destination_child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination_fd)
                    try:
                        cls._copy_manifested_tree(
                            child_fd,
                            destination_child_fd,
                            manifest,
                            child_relative,
                            budget,
                            copy_predicate=copy_predicate,
                        )
                    finally:
                        os.close(destination_child_fd)
                else:
                    cls._copy_snapshot_file(child_fd, destination_fd, name, child_node, budget)
                cls._assert_snapshot_node(child_fd, child_node)
                cls._assert_snapshot_entry(source_fd, name, child_node)
            finally:
                os.close(child_fd)
        if cls._directory_names(source_fd) != manifest.entries.get(relative):
            raise ValueError("git_snapshot_source_changed")
        cls._assert_snapshot_node(source_fd, directory_node)

    @classmethod
    def _open_manifested_child(
        cls,
        directory_fd: int,
        name: str,
        node: _SnapshotNode,
    ) -> int:
        """Open exactly the manifest entry without blocking on a swapped FIFO."""

        try:
            checked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            raise ValueError("git_snapshot_source_changed") from None
        expected_directory = node.kind == "directory"
        if (
            cls._snapshot_identity(checked) != node.identity
            or stat.S_ISDIR(checked.st_mode) != expected_directory
            or (not expected_directory and not stat.S_ISREG(checked.st_mode))
        ):
            raise ValueError("git_snapshot_source_changed")
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if expected_directory:
            flags |= os.O_DIRECTORY
        else:
            # A replacement with a FIFO occurs after the stat above in the
            # adversarial case.  O_NONBLOCK is required before fstat can reject
            # that replacement without stalling the Harness worker.
            flags |= os.O_NONBLOCK
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
        except OSError:
            raise ValueError("git_snapshot_source_changed") from None
        try:
            cls._assert_snapshot_node(descriptor, node)
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    @classmethod
    def _copy_snapshot_file(
        cls,
        source_fd: int,
        destination_parent_fd: int,
        name: str,
        node: _SnapshotNode,
        budget: _SnapshotBudget,
    ) -> None:
        budget.add_file(0)
        try:
            destination_fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=destination_parent_fd,
            )
        except OSError:
            raise ValueError("git_snapshot_source_invalid") from None
        copied = 0
        try:
            while True:
                block = os.read(source_fd, 8192)
                if not block:
                    break
                copied += len(block)
                budget.add_bytes(len(block))
                cls._write_all(destination_fd, block)
            os.fsync(destination_fd)
        except (OSError, RuntimeError):
            raise ValueError("git_snapshot_source_invalid") from None
        finally:
            os.close(destination_fd)
        if copied != node.identity[3]:
            raise ValueError("git_snapshot_source_changed")
        try:
            os.chmod(name, stat.S_IMODE(node.identity[2]) & 0o755, dir_fd=destination_parent_fd, follow_symlinks=False)
        except (OSError, NotImplementedError):
            raise ValueError("git_snapshot_source_invalid") from None

    @classmethod
    def _write_snapshot_config(cls, snapshot_git_fd: int) -> None:
        contents = (
            b"[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
            b"\tfilemode = true\n\tlogallrefupdates = true\n"
        )
        try:
            descriptor = os.open("config", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=snapshot_git_fd)
            try:
                cls._write_lock(descriptor, contents)
            finally:
                os.close(descriptor)
        except OSError:
            raise ValueError("git_snapshot_source_invalid") from None

    @classmethod
    def _assert_snapshot_tree(cls, root_fd: int) -> None:
        """The private execution copy may not contain a symlink at any depth."""

        def validate(directory_fd: int) -> None:
            for name in cls._directory_names(directory_fd):
                try:
                    child_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                except OSError:
                    raise ValueError("git_snapshot_source_invalid") from None
                try:
                    item = os.fstat(child_fd)
                    if stat.S_ISDIR(item.st_mode):
                        validate(child_fd)
                    elif not stat.S_ISREG(item.st_mode):
                        raise ValueError("git_snapshot_source_invalid")
                finally:
                    os.close(child_fd)

        validate(root_fd)

    @classmethod
    def _validate_snapshot_metadata(cls, root_fd: int) -> None:
        """Validate the destination Git layout immediately before any Popen.

        Source layout checks prove what was captured; this second pass proves
        the private execution directory is still the small, no-extension view
        that a Git child is about to consume.  It is intentionally strict so a
        transient source metadata injection cannot survive as a snapshot file.
        """

        try:
            git_fd = os.open(".git", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        except OSError:
            raise ValueError("git_snapshot_source_invalid") from None
        try:
            manifest = cls._capture_snapshot_manifest(
                git_fd,
                skipped=set(),
                reject_attributes=True,
                budget=_SnapshotBudget(),
                metadata=True,
            )
            root_entries = set(manifest.entries.get((), ()))
            allowed = _METADATA_COPY_ROOTS | {"config"}
            if root_entries - allowed:
                raise ValueError("git_snapshot_source_invalid")
            if "HEAD" not in root_entries or "objects" not in root_entries or "refs" not in root_entries:
                raise ValueError("git_snapshot_source_invalid")
            contents = cls._read_relative_file(git_fd, ("config",))
            cls._validate_config_contents(contents or b"")
            cls._assert_manifest_stable(git_fd, manifest)
        finally:
            os.close(git_fd)

    @staticmethod
    def _open_parent(
        directory_fd: int,
        parts: Sequence[str],
        *,
        create_missing: bool = False,
        missing_ok: bool = False,
    ) -> tuple[int, str] | None:
        if not parts or any(not part or part in {".", ".."} or "/" in part for part in parts):
            raise ValueError("git_unsafe_repository_metadata")
        current = os.dup(directory_fd)
        try:
            for part in parts[:-1]:
                try:
                    checked = os.stat(part, dir_fd=current, follow_symlinks=False)
                except FileNotFoundError:
                    if not create_missing:
                        if missing_ok:
                            os.close(current)
                            return None
                        raise ValueError("git_unsafe_repository_metadata") from None
                    try:
                        os.mkdir(part, 0o700, dir_fd=current)
                        os.fsync(current)
                    except FileExistsError:
                        pass
                    checked = os.stat(part, dir_fd=current, follow_symlinks=False)
                if not stat.S_ISDIR(checked.st_mode):
                    raise ValueError("git_unsafe_repository_metadata")
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
                if GitProviderAdapter._snapshot_identity(os.fstat(next_fd)) != GitProviderAdapter._snapshot_identity(checked):
                    os.close(next_fd)
                    raise ValueError("git_unsafe_repository_metadata")
                os.close(current)
                current = next_fd
            return current, parts[-1]
        except OSError:
            os.close(current)
            raise ValueError("git_unsafe_repository_metadata") from None
        except ValueError:
            os.close(current)
            raise

    @classmethod
    def _read_relative_file(cls, directory_fd: int, parts: Sequence[str], *, missing: bool = False) -> bytes | None:
        parent = cls._open_parent(directory_fd, parts, missing_ok=missing)
        if parent is None:
            return None
        parent_fd, name = parent
        try:
            try:
                checked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if missing:
                    return None
                raise ValueError("git_unsafe_repository_metadata") from None
            if not stat.S_ISREG(checked.st_mode) or checked.st_size > _LOCAL_FILE_LIMIT:
                raise ValueError("git_unsafe_repository_metadata")
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            except FileNotFoundError:
                if missing:
                    return None
                raise ValueError("git_unsafe_repository_metadata") from None
            try:
                file_stat = os.fstat(fd)
                if cls._snapshot_identity(file_stat) != cls._snapshot_identity(checked):
                    raise ValueError("git_unsafe_repository_metadata")
                data = bytearray()
                while len(data) <= _LOCAL_FILE_LIMIT:
                    block = os.read(fd, min(8192, _LOCAL_FILE_LIMIT + 1 - len(data)))
                    if not block:
                        break
                    data.extend(block)
                if len(data) > _LOCAL_FILE_LIMIT:
                    raise ValueError("git_unsafe_repository_metadata")
                if cls._snapshot_identity(os.fstat(fd)) != cls._snapshot_identity(checked):
                    raise ValueError("git_unsafe_repository_metadata")
                return bytes(data)
            finally:
                os.close(fd)
        except OSError:
            raise ValueError("git_unsafe_repository_metadata") from None
        finally:
            os.close(parent_fd)

    @classmethod
    def _create_lock(cls, directory_fd: int, parts: Sequence[str]) -> tuple[int, int, str]:
        parent_fd, name = cls._open_parent(directory_fd, parts, create_missing=True)
        try:
            lock_fd = os.open(name + ".lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        except OSError:
            os.close(parent_fd)
            raise ValueError("git_publish_lock_unavailable") from None
        return parent_fd, lock_fd, name

    @staticmethod
    def _write_all(descriptor: int, value: bytes) -> None:
        """Write a complete buffer or fail closed on every non-progress write."""

        written = 0
        try:
            while written < len(value):
                count = os.write(descriptor, value[written:])
                if (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count <= 0
                    or count > len(value) - written
                ):
                    raise RuntimeError("git_publish_unknown")
                written += count
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    @classmethod
    def _write_lock(cls, lock_fd: int, value: bytes) -> None:
        cls._write_all(lock_fd, value)
        try:
            os.fsync(lock_fd)
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    @staticmethod
    def _remove_lock(parent_fd: int, name: str) -> None:
        try:
            os.unlink(name + ".lock", dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _relative_ref(branch: str, *, tracking: bool = False, remote: str = "") -> tuple[str, ...]:
        if tracking:
            if _ALIAS.fullmatch(remote) is None:
                raise ValueError("git_refspec_not_allowed")
            ref_name = GitProviderAdapter._validate_ref_name(branch, require_heads_prefix=True)
            return tuple(("refs", "remotes", remote, *ref_name.removeprefix("refs/heads/").split("/")))
        branch_name = GitProviderAdapter._validate_ref_name(branch, require_heads_prefix=False)
        return tuple(("refs", "heads", *branch_name.split("/")))

    def _read_ref(self, scope: RepositoryScope, parts: Sequence[str], *, missing: bool = False) -> str | None:
        git_fd = scope.open_git_fd()
        try:
            data = self._read_relative_file(git_fd, parts, missing=missing)
        finally:
            os.close(git_fd)
        if data is None:
            return None
        try:
            value = data.decode("ascii").strip()
        except UnicodeDecodeError:
            raise ValueError("git_unsafe_repository_metadata") from None
        if _SHA.fullmatch(value) is None:
            raise ValueError("git_unsafe_repository_metadata")
        return value

    def _publish_ref(self, scope: RepositoryScope, parts: Sequence[str], value: str, *, expected: str | None) -> None:
        """Publish a loose ref through a dirfd-anchored standard lock file."""

        parts = self._validate_ref_parts(parts)
        self._preflight(scope)
        git_fd = scope.open_git_fd()
        parent_fd = lock_fd = -1
        name = ""
        try:
            current = self._read_relative_file(git_fd, parts, missing=True)
            current_value = None if current is None else self._sha(current)
            if current_value != expected:
                raise ValueError("git_publish_ref_changed")
            parent_fd, lock_fd, name = self._create_lock(git_fd, parts)
            # The lock is now held; re-read through the anchored directory.
            current = self._read_relative_file(git_fd, parts, missing=True)
            current_value = None if current is None else self._sha(current)
            if current_value != expected:
                raise ValueError("git_publish_ref_changed")
            self._write_lock(lock_fd, (value + "\n").encode("ascii"))
            os.close(lock_fd); lock_fd = -1
            scope.assert_identity()
            os.replace(name + ".lock", name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError:
            raise RuntimeError("git_publish_unknown") from None
        finally:
            if lock_fd >= 0:
                os.close(lock_fd)
            if parent_fd >= 0:
                self._remove_lock(parent_fd, name)
                os.close(parent_fd)
            os.close(git_fd)

    @staticmethod
    def _index_hash(value: bytes | None) -> str:
        return hashlib.sha256(value or b"").hexdigest()

    @classmethod
    def _write_journal(cls, git_fd: int, payload: Mapping[str, object], *, replace: bool = False) -> None:
        encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _LOCAL_FILE_LIMIT:
            raise RuntimeError("git_publish_unknown")
        name = _TRANSACTION_JOURNAL + (".next" if replace else "")
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=git_fd)
            try:
                cls._write_lock(fd, encoded)
            finally:
                os.close(fd)
            if replace:
                os.replace(name, _TRANSACTION_JOURNAL, src_dir_fd=git_fd, dst_dir_fd=git_fd)
            os.fsync(git_fd)
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    @staticmethod
    def _remove_journal(git_fd: int) -> None:
        try:
            os.unlink(_TRANSACTION_JOURNAL, dir_fd=git_fd)
            os.fsync(git_fd)
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    @classmethod
    def _write_index_backup(cls, git_fd: int, original_index: bytes | None) -> str | None:
        if original_index is None:
            return None
        try:
            backup_fd = os.open(
                _TRANSACTION_INDEX_BACKUP,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=git_fd,
            )
            try:
                cls._write_lock(backup_fd, original_index)
            finally:
                os.close(backup_fd)
            os.fsync(git_fd)
            return _TRANSACTION_INDEX_BACKUP
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    @staticmethod
    def _remove_index_backup(git_fd: int) -> None:
        try:
            os.unlink(_TRANSACTION_INDEX_BACKUP, dir_fd=git_fd)
            os.fsync(git_fd)
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    @staticmethod
    def _discard_transaction_material(git_fd: int, name: str) -> None:
        try:
            os.unlink(name, dir_fd=git_fd)
            os.fsync(git_fd)
        except OSError:
            pass

    @classmethod
    def _create_fetch_recovery_marker(cls, git_fd: int) -> None:
        """Durably mark an object-copy attempt until all object writes are safe.

        The marker is deliberately treated as a recovery stop by preflight.  A
        failed object cleanup may otherwise leave a short object that a later
        fetch would see as an ordinary FileExistsError and silently skip.
        """

        try:
            descriptor = os.open(
                _FETCH_RECOVERY_MARKER,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=git_fd,
            )
            try:
                cls._write_lock(descriptor, b'{"version":1,"state":"object-copy"}')
            finally:
                os.close(descriptor)
            os.fsync(git_fd)
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    @staticmethod
    def _clear_fetch_recovery_marker(git_fd: int) -> None:
        try:
            os.unlink(_FETCH_RECOVERY_MARKER, dir_fd=git_fd)
            os.fsync(git_fd)
        except OSError:
            # Leaving the marker in place blocks later Harness actions.  This
            # is safer than treating an unconfirmed cleanup as successful.
            raise RuntimeError("git_publish_unknown") from None

    @staticmethod
    def _remove_new_object(parent_fd: int, name: str) -> None:
        """Remove a failed O_EXCL object and prove the removal is durable."""

        try:
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise RuntimeError("git_publish_unknown")
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    @classmethod
    def _validate_fetched_loose_objects(cls, snapshot_git: Path) -> None:
        """Prove a fetch snapshot has only bounded loose SHA-1 objects.

        Git fetch may choose a packed transfer even for an otherwise safe
        repository.  Source publication is deliberately limited to loose
        objects because source preflight rejects pack/info payloads.  Inspect
        this private snapshot before opening the source Git directory so a
        rejected transfer has no source object, marker, or ref side effect.
        """

        git_fd = objects_fd = -1
        try:
            git_fd = os.open(snapshot_git, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
            objects_fd = os.open("objects", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=git_fd)
            budget = _SnapshotBudget()
            budget.add_directory()
            objects_identity = cls._snapshot_identity(os.fstat(objects_fd))
            object_entries = cls._directory_names(objects_fd)
            for name in object_entries:
                try:
                    checked = os.stat(name, dir_fd=objects_fd, follow_symlinks=False)
                except OSError:
                    raise RuntimeError("git_snapshot_invalid") from None
                if name in {"info", "pack"}:
                    if not stat.S_ISDIR(checked.st_mode):
                        raise RuntimeError("git_snapshot_invalid")
                    try:
                        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=objects_fd)
                    except OSError:
                        raise RuntimeError("git_snapshot_invalid") from None
                    try:
                        if cls._snapshot_identity(os.fstat(child_fd)) != cls._snapshot_identity(checked) or cls._directory_names(child_fd):
                            raise RuntimeError("git_snapshot_invalid")
                        budget.add_directory()
                    finally:
                        os.close(child_fd)
                    continue
                if _LOOSE_OBJECT_DIRECTORY.fullmatch(name) is None or not stat.S_ISDIR(checked.st_mode):
                    raise RuntimeError("git_snapshot_invalid")
                try:
                    fanout_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=objects_fd)
                except OSError:
                    raise RuntimeError("git_snapshot_invalid") from None
                try:
                    if cls._snapshot_identity(os.fstat(fanout_fd)) != cls._snapshot_identity(checked):
                        raise RuntimeError("git_snapshot_invalid")
                    budget.add_directory()
                    for object_name in cls._directory_names(fanout_fd):
                        try:
                            object_stat = os.stat(object_name, dir_fd=fanout_fd, follow_symlinks=False)
                        except OSError:
                            raise RuntimeError("git_snapshot_invalid") from None
                        if _LOOSE_OBJECT_NAME.fullmatch(object_name) is None or not stat.S_ISREG(object_stat.st_mode):
                            raise RuntimeError("git_snapshot_invalid")
                        try:
                            object_fd = os.open(object_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fanout_fd)
                        except OSError:
                            raise RuntimeError("git_snapshot_invalid") from None
                        try:
                            if cls._snapshot_identity(os.fstat(object_fd)) != cls._snapshot_identity(object_stat):
                                raise RuntimeError("git_snapshot_invalid")
                            budget.add_file(object_stat.st_size)
                        finally:
                            os.close(object_fd)
                    if cls._snapshot_identity(os.fstat(fanout_fd)) != cls._snapshot_identity(checked):
                        raise RuntimeError("git_snapshot_invalid")
                finally:
                    os.close(fanout_fd)
            if cls._snapshot_identity(os.fstat(objects_fd)) != objects_identity or cls._directory_names(objects_fd) != object_entries:
                raise RuntimeError("git_snapshot_invalid")
        except OSError:
            raise RuntimeError("git_snapshot_invalid") from None
        finally:
            if objects_fd >= 0:
                os.close(objects_fd)
            if git_fd >= 0:
                os.close(git_fd)

    def _copy_new_objects(
        self,
        snapshot_git: Path,
        scope: RepositoryScope,
        *,
        require_source_preflight: bool = True,
    ) -> None:
        # The final source proof must happen after fetch has modified the
        # private snapshot but before this method opens the source for writing.
        self._validate_fetched_loose_objects(snapshot_git)
        if require_source_preflight:
            self._preflight(scope)
        objects = snapshot_git / "objects"
        git_fd = scope.open_git_fd()
        marker_created = False
        cleanup_confirmed = True
        try:
            self._create_fetch_recovery_marker(git_fd)
            marker_created = True
            object_fd = os.open("objects", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=git_fd)
            try:
                copied = _SnapshotBudget()
                for source in sorted(objects.rglob("*")):
                    if source.is_dir():
                        continue
                    relative = source.relative_to(objects).parts
                    if not relative or any(not re.fullmatch(r"[A-Za-z0-9._-]{1,255}", part) for part in relative):
                        raise RuntimeError("git_snapshot_invalid")
                    source_stat = source.lstat()
                    if source.is_symlink() or not stat.S_ISREG(source_stat.st_mode):
                        raise RuntimeError("git_snapshot_invalid")
                    copied.add_file(source_stat.st_size)
                    parent_fd = os.dup(object_fd)
                    try:
                        for part in relative[:-1]:
                            try:
                                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                            except FileNotFoundError:
                                os.mkdir(part, 0o700, dir_fd=parent_fd)
                                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                            os.close(parent_fd)
                            parent_fd = next_fd
                        try:
                            destination_fd = os.open(relative[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444, dir_fd=parent_fd)
                        except FileExistsError:
                            continue
                        try:
                            self._copy_fd_file(source, destination_fd)
                            os.fsync(parent_fd)
                        except BaseException:
                            # An O_EXCL object belongs to this attempt.  It must
                            # not become a future "already copied" object.
                            try:
                                self._remove_new_object(parent_fd, relative[-1])
                            except RuntimeError:
                                cleanup_confirmed = False
                            raise
                        finally:
                            os.close(destination_fd)
                    finally:
                        os.close(parent_fd)
            finally:
                os.close(object_fd)
            self._clear_fetch_recovery_marker(git_fd)
            marker_created = False
        except RuntimeError:
            if marker_created and cleanup_confirmed:
                # The failed object has been unlinked and its parent fsync'd, so
                # retry is safe.  If marker removal itself fails it intentionally
                # remains a durable recovery stop.
                self._clear_fetch_recovery_marker(git_fd)
                marker_created = False
            raise
        except OSError:
            if marker_created and cleanup_confirmed:
                self._clear_fetch_recovery_marker(git_fd)
                marker_created = False
            raise RuntimeError("git_publish_unknown") from None
        finally:
            os.close(git_fd)

    @staticmethod
    def _copy_fd_file(source: Path, destination_fd: int) -> None:
        try:
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                source_stat = os.fstat(source_fd)
                if not stat.S_ISREG(source_stat.st_mode):
                    raise RuntimeError("git_publish_unknown")
                copied = 0
                while True:
                    block = os.read(source_fd, 8192)
                    if not block:
                        break
                    copied += len(block)
                    GitProviderAdapter._write_all(destination_fd, block)
                os.fsync(destination_fd)
                if copied != source_stat.st_size or os.fstat(destination_fd).st_size != source_stat.st_size:
                    raise RuntimeError("git_publish_unknown")
            finally:
                os.close(source_fd)
        except OSError:
            raise RuntimeError("git_publish_unknown") from None

    def _publish_commit_transaction(
        self,
        scope: RepositoryScope,
        branch: str,
        expected_parent: str,
        commit: str,
        prepared_index: bytes,
        expected_source_index: bytes | None,
        snapshot_git: Path,
    ) -> None:
        """Install one prepared ref/index pair or leave an explicit recovery stop.

        Git has no atomic operation spanning ref and index.  We therefore never
        perform a best-effort rollback: after a ref publication, a durable
        journal blocks every later Harness operation until recovery is reviewed.
        """

        ref_parts = self._relative_ref(branch)
        self._validate_ref_parts(ref_parts)
        self._preflight(scope)
        git_fd = scope.open_git_fd()
        index_parent = ref_parent = index_lock = ref_lock = -1
        index_name = ref_name = ""
        journal_created = False
        backup_created = False
        ref_published = False
        try:
            head = self._read_relative_file(git_fd, ("HEAD",))
            if head != ("ref: refs/heads/" + branch + "\n").encode("ascii"):
                raise ValueError("git_expected_parent_mismatch")
            original_index = self._read_relative_file(git_fd, ("index",), missing=True)
            original_ref = self._read_relative_file(git_fd, ref_parts, missing=True)
            if original_ref is None or self._sha(original_ref) != expected_parent:
                raise ValueError("git_expected_parent_mismatch")
            if original_index != expected_source_index:
                raise ValueError("git_staged_data_not_allowed")
            index_parent, index_lock, index_name = self._create_lock(git_fd, ("index",))
            ref_parent, ref_lock, ref_name = self._create_lock(git_fd, ref_parts)
            # Locks were acquired before the final source snapshot/identity/CAS
            # check, so normal Git writers cannot replace staged state here.
            scope.assert_identity()
            if self._read_relative_file(git_fd, ("HEAD",)) != head:
                raise ValueError("git_publish_ref_changed")
            if self._read_relative_file(git_fd, ("index",), missing=True) != original_index:
                raise ValueError("git_publish_index_changed")
            current_ref = self._read_relative_file(git_fd, ref_parts, missing=True)
            if current_ref != original_ref:
                raise ValueError("git_publish_ref_changed")
            journal: dict[str, object] = {
                "version": 1,
                "state": "prepared",
                "journal_fsync": True,
                "ref": "/".join(ref_parts),
                "old_ref": expected_parent,
                "new_ref": commit,
                "old_index_sha256": self._index_hash(original_index),
                "new_index_sha256": self._index_hash(prepared_index),
                "old_index_present": original_index is not None,
            }
            backup_name = self._write_index_backup(git_fd, original_index)
            if backup_name is not None:
                journal["old_index_backup"] = backup_name
                backup_created = True
            self._write_journal(git_fd, journal)
            journal_created = True
            self._copy_new_objects(snapshot_git, scope, require_source_preflight=False)
            self._write_lock(index_lock, prepared_index)
            self._write_lock(ref_lock, (commit + "\n").encode("ascii"))
            journal["state"] = "locks_prepared"
            self._write_journal(git_fd, journal, replace=True)
            os.close(ref_lock); ref_lock = -1
            os.replace(ref_name + ".lock", ref_name, src_dir_fd=ref_parent, dst_dir_fd=ref_parent)
            os.fsync(ref_parent)
            ref_published = True
            journal["state"] = "ref_published"
            self._write_journal(git_fd, journal, replace=True)
            os.close(index_lock); index_lock = -1
            os.replace(index_name + ".lock", index_name, src_dir_fd=index_parent, dst_dir_fd=index_parent)
            os.fsync(index_parent)
            journal["state"] = "index_published"
            self._write_journal(git_fd, journal, replace=True)
            if backup_created:
                self._remove_index_backup(git_fd)
                backup_created = False
            self._remove_journal(git_fd)
            journal_created = False
        except OSError:
            # Once the ref exists, do not risk replacing a potentially changed
            # index during rollback.  The durable journal is the recovery proof.
            raise RuntimeError("git_publish_unknown") from None
        finally:
            if ref_lock >= 0:
                os.close(ref_lock)
            if index_lock >= 0:
                os.close(index_lock)
            if not ref_published and ref_parent >= 0:
                self._remove_lock(ref_parent, ref_name)
            if not ref_published and index_parent >= 0:
                self._remove_lock(index_parent, index_name)
            if ref_parent >= 0:
                os.close(ref_parent)
            if index_parent >= 0:
                os.close(index_parent)
            os.close(git_fd)
        if journal_created:
            raise RuntimeError("git_publish_unknown")

    @classmethod
    def _reject_unsafe_repository_configuration(cls, git_fd: int) -> None:
        contents = cls._read_relative_file(git_fd, ("config",), missing=True) or b""
        cls._validate_config_contents(contents)

    @staticmethod
    def _validate_config_contents(contents: bytes) -> None:
        # Fail closed: only stock init core settings and user identity are accepted.
        section = ""
        allowed_core = {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase", "precomposeunicode"}
        try:
            lines = contents.decode("utf-8", "strict").splitlines()
        except UnicodeDecodeError:
            raise ValueError("git_unsafe_repository_configuration") from None
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                if section not in {"core", "user"}:
                    raise ValueError("git_unsafe_repository_configuration")
                continue
            key = line.split("=", 1)[0].strip().lower()
            if not section or (section == "core" and key not in allowed_core):
                raise ValueError("git_unsafe_repository_configuration")

    def _git(self, scope: RepositoryScope, arguments: Sequence[str], timeout: int, *, extra_env: Mapping[str, str] | None = None, allow_codes: tuple[int, ...] = (0,), input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        with self._execution_snapshot(scope) as snapshot:
            return self._git_snapshot(snapshot, arguments, timeout, extra_env=extra_env, allow_codes=allow_codes, input_bytes=input_bytes)

    def _git_snapshot(self, snapshot: Path, arguments: Sequence[str], timeout: int, *, extra_env: Mapping[str, str] | None = None, allow_codes: tuple[int, ...] = (0,), input_bytes: bytes | None = None, on_started: Callable[[], None] | None = None) -> subprocess.CompletedProcess[bytes]:
        return self._invoke(snapshot, arguments, timeout, extra_env=extra_env, allow_codes=allow_codes, input_bytes=input_bytes, on_started=on_started)

    def _invoke_live(
        self,
        scope: RepositoryScope,
        arguments: Sequence[str],
        timeout: int,
        *,
        on_started: Callable[[], None] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run one fixed-argv operation only after source identity preflight."""

        scope.assert_identity()
        self._preflight(scope)
        env = {
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": os.devnull,
            "GIT_ALLOW_PROTOCOL": "https",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_DIR": str(scope.root / ".git"),
            "GIT_WORK_TREE": str(scope.root),
        }
        command = [
            self._git_executable,
            "--no-replace-objects",
            "-c", "credential.helper=",
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false",
            "-c", "diff.external=false",
            "-c", "core.attributesFile=/dev/null",
            "-c", "protocol.file.allow=never",
            "-c", "protocol.ext.allow=never",
            "-c", "http.sslVerify=true",
            "-c", "http.followRedirects=false",
            *arguments,
        ]
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=str(scope.root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            if on_started is not None:
                on_started()
            stdout, stderr, overflow = self._drain(process, timeout)
        except (OSError, subprocess.SubprocessError):
            if process is not None:
                self._cleanup_process(process)
            raise _GitLiveCommandFailure("git_operation_not_started") from None
        except BaseException:
            if process is not None:
                self._cleanup_process(process)
            raise
        if overflow:
            raise _GitLiveCommandFailure("git_operation_timeout")
        if process.returncode != 0:
            raise _GitLiveCommandFailure("git_operation_failed", returncode=process.returncode)
        scope.assert_identity()
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def _invoke(self, snapshot: Path, arguments: Sequence[str], timeout: int, *, extra_env: Mapping[str, str] | None = None, allow_codes: tuple[int, ...] = (0,), input_bytes: bytes | None = None, on_started: Callable[[], None] | None = None) -> subprocess.CompletedProcess[bytes]:
        git_dir = snapshot / ".git"
        env = {"LC_ALL": "C", "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": os.devnull, "GIT_ALLOW_PROTOCOL": "https", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(snapshot)}
        if extra_env:
            env.update(extra_env)
        command = [
            self._git_executable,
            "--no-replace-objects",
            "-c", "credential.helper=",
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false",
            "-c", "diff.external=false",
            "-c", "core.attributesFile=/dev/null",
            "-c", "protocol.file.allow=never",
            "-c", "protocol.ext.allow=never",
            "-c", "http.sslVerify=true",
            "-c", "http.followRedirects=false",
            "-c", f"fetch.unpackLimit={_FETCH_UNPACK_LIMIT}",
            *arguments,
        ]
        process: subprocess.Popen[bytes] | None = None
        try:
            if input_bytes is not None and len(input_bytes) > _PROCESS_BYTE_LIMIT:
                raise RuntimeError("git_command_failed")
            with tempfile.TemporaryFile() as input_file:
                if input_bytes is not None:
                    input_file.write(input_bytes); input_file.seek(0)
                process = subprocess.Popen(command, cwd=str(snapshot), env=env, stdin=input_file if input_bytes is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                if on_started is not None:
                    on_started()
                stdout, stderr, overflow = self._drain(process, timeout)
        except (OSError, subprocess.SubprocessError):
            if process is not None:
                self._cleanup_process(process)
            raise RuntimeError("git_command_failed") from None
        except BaseException:
            if process is not None:
                self._cleanup_process(process)
            raise
        if overflow or process.returncode not in allow_codes: raise RuntimeError("git_command_failed")
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    @staticmethod
    def _drain(process: subprocess.Popen[bytes], timeout: int) -> tuple[bytes, bytes, bool]:
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout
        overflow = False
        try:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    GitProviderAdapter._terminate_group(process); overflow = True; break
                for key, _events in selector.select(remaining):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj); continue
                    if len(buffers[key.data]) + len(data) > _PROCESS_BYTE_LIMIT:
                        GitProviderAdapter._terminate_group(process); overflow = True; break
                    buffers[key.data].extend(data)
                if overflow:
                    break
        except (OSError, ValueError):
            GitProviderAdapter._terminate_group(process)
            overflow = True
        finally:
            # A leader may have exited while a helper keeps a pipe open: killpg
            # is still required in that state.  Always reap/close best-effort.
            GitProviderAdapter._cleanup_process(process)
            selector.close()
        return bytes(buffers["stdout"]), bytes(buffers["stderr"]), overflow

    @staticmethod
    def _cleanup_process(process: subprocess.Popen[bytes]) -> None:
        """Best-effort whole-group kill/reap used for every post-Popen failure."""

        GitProviderAdapter._terminate_group(process)
        for _attempt in range(2):
            try:
                process.wait(timeout=1)
                break
            except (subprocess.TimeoutExpired, OSError):
                GitProviderAdapter._terminate_group(process)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()

    @staticmethod
    def _terminate_group(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            # The leader may have reaped before a child; process-group cleanup
            # has nevertheless been attempted and the caller still reaps it.
            pass
        except OSError:
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _sha(value: bytes) -> str:
        try: result = value.decode("ascii").strip()
        except UnicodeDecodeError: raise RuntimeError("git_command_failed") from None
        if _SHA.fullmatch(result) is None: raise RuntimeError("git_command_failed")
        return result

    @staticmethod
    def _status_paths(raw: bytes) -> tuple[str, ...]:
        records = raw.split(b"\0"); paths: list[str] = []; index = 0
        while index < len(records):
            record = records[index]; index += 1
            if not record: continue
            kind = record[:1]
            if kind == b"?": paths.append(record[2:].decode("utf-8", "strict")); continue
            if kind == b"1": paths.append(record.split(b" ", 8)[8].decode("utf-8", "strict")); continue
            if kind == b"2":
                paths.append(record.split(b" ", 9)[9].decode("utf-8", "strict"))
                if index >= len(records): raise ValueError("git_status_invalid")
                paths.append(records[index].decode("utf-8", "strict")); index += 1; continue
            raise ValueError("git_status_invalid")
        return tuple(paths)

    @staticmethod
    def _numstat(raw: bytes) -> tuple[int, int, int]:
        added = deleted = changed = 0
        for line in raw.splitlines():
            parts = line.split(b"\t", 2)
            if len(parts) != 3: raise ValueError("git_diff_invalid")
            if parts[0] != b"-": added += int(parts[0])
            if parts[1] != b"-": deleted += int(parts[1])
            changed += 1
        return added, deleted, changed

    def _tree_changes_match(self, scope: RepositoryScope, parent: str, tree: str, expected: Mapping[str, object], timeout: int) -> bool:
        for path, evidence in expected.items():
            before = self._tree_entry(scope, parent, path, timeout)
            after = self._tree_entry(scope, tree, path, timeout)
            if evidence is None:
                if after is not None or before is None: return False
            elif after != (evidence["mode"], evidence["blob"]) or before == after: return False
        return True

    def _tree_entry(self, scope: RepositoryScope, tree: str, path: str, timeout: int) -> tuple[str, str] | None:
        raw = self._git(scope, ("ls-tree", "-z", tree, "--", path), timeout).stdout
        if not raw:
            return None
        entry = raw.rstrip(b"\0")
        try:
            metadata, found = entry.split(b"\t", 1)
            mode, kind, blob = metadata.split(b" ")
            if found.decode("utf-8", "strict") != path or kind != b"blob" or mode.decode() not in _MODE:
                return None
            return mode.decode(), self._sha(blob)
        except (ValueError, UnicodeDecodeError, RuntimeError):
            return None

    def _tree_map(self, scope: RepositoryScope, object_name: str, timeout: int) -> dict[str, tuple[str, str]]:
        raw = self._git(scope, ("ls-tree", "-r", "-z", object_name), timeout).stdout
        result: dict[str, tuple[str, str]] = {}
        for entry in raw.split(b"\0"):
            if not entry: continue
            metadata, path = entry.split(b"\t", 1); mode, kind, blob = metadata.split(b" ")
            if kind != b"blob" or mode.decode() not in _MODE or _SHA.fullmatch(blob.decode("ascii")) is None: return {}
            result[path.decode("utf-8", "strict")] = (mode.decode(), blob.decode())
        return result


def validate_git_action_parameters(
    action_value: object,
    target_alias_value: object,
    parameters: Mapping[str, object],
) -> None:
    """Pure plan/execute-time Git input validation without opening a repository.

    Scope-specific path and branch-policy checks still belong to the adapter,
    but the plan boundary can reject malformed refs and a target mismatch before
    an authorization is consumed.  Keep this independent of registered scopes
    so stale plans can be blocked before any adapter is entered.
    """

    if (
        not isinstance(action_value, str)
        or action_value not in REPOSITORY_BOUND_GIT_ACTIONS
        or not isinstance(target_alias_value, str)
        or _ALIAS.fullmatch(target_alias_value) is None
        or not isinstance(parameters, Mapping)
    ):
        raise ValueError("git_action_not_allowed")
    action = action_value
    allowed = {"repository_alias", "timeout_seconds"}
    if action == "repo.log.read":
        allowed.add("limit")
    if action == "repo.diff.read":
        allowed.add("file_list")
    if action == "branch.create":
        allowed.update(("branch_name", "expected_base_sha"))
    if action == "commit.create":
        allowed.update(("branch_name", "expected_parent", "file_list", "expected_file_blobs", "message"))
    if action == "remote.fetch":
        allowed.update(("remote_alias", "ref_name"))
    if action == "git.operation.plan":
        allowed.update((
            "operation", "branch_name", "expected_head_sha", "target_sha", "mode",
            "allow_dirty", "commit_sha", "allow_conflict", "source_ref", "strategy", "remote_alias", "ref_name",
            "target_ref", "expected_remote_sha", "force",
        ))
    if action == "reset.local":
        allowed.update(("branch_name", "expected_head_sha", "target_sha", "mode", "allow_dirty"))
    if action == "cherry-pick.local":
        allowed.update(("branch_name", "expected_head_sha", "commit_sha", "allow_conflict"))
    if action == "merge.local":
        allowed.update(("branch_name", "expected_head_sha", "source_ref", "strategy", "allow_conflict"))
    if set(parameters) - allowed or parameters.get("repository_alias") != target_alias_value:
        raise ValueError("git_repository_target_mismatch")
    _validate_git_timeout(parameters)
    if action == "repo.log.read":
        limit = parameters.get("limit", 20)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("git_parameters_invalid")
    if action in {"repo.diff.read", "commit.create"}:
        _validate_git_path_shape(parameters.get("file_list"), required=action == "commit.create")
    if action in {"branch.create", "commit.create"}:
        try:
            branch = GitProviderAdapter._validate_ref_name(
                parameters.get("branch_name"), require_heads_prefix=False
            )
        except ValueError:
            raise ValueError("git_branch_not_allowed") from None
        if branch.startswith("-"):
            raise ValueError("git_branch_not_allowed")
    if action == "branch.create":
        GitProviderAdapter._sha_value(parameters.get("expected_base_sha"))
    if action == "commit.create":
        paths = tuple(parameters.get("file_list", ()))
        GitProviderAdapter._sha_value(parameters.get("expected_parent"))
        _validate_git_evidence_shape(paths, parameters.get("expected_file_blobs"))
        GitProviderAdapter._message(parameters.get("message"))
    if action == "remote.fetch":
        remote = parameters.get("remote_alias")
        if not isinstance(remote, str) or _ALIAS.fullmatch(remote) is None:
            raise ValueError("git_remote_invalid")
        GitProviderAdapter._validate_ref_name(
            parameters.get("ref_name"), require_heads_prefix=True
        )
    if action == "git.operation.plan":
        operation = parameters.get("operation")
        if operation not in {"reset.local", "cherry-pick.local", "merge.local", "remote.pull", "remote.push"}:
            raise ValueError("git_operation_not_allowed")
        GitProviderAdapter._sha_value(parameters.get("expected_head_sha"))
        try:
            GitProviderAdapter._validate_ref_name(parameters.get("branch_name"), require_heads_prefix=False)
        except ValueError:
            raise ValueError("git_branch_not_allowed") from None
        if operation == "reset.local":
            GitProviderAdapter._sha_value(parameters.get("target_sha"))
            if parameters.get("mode") not in {"soft", "mixed", "hard"}:
                raise ValueError("git_reset_mode_invalid")
        elif operation == "cherry-pick.local":
            GitProviderAdapter._sha_value(parameters.get("commit_sha"))
        elif operation == "merge.local":
            GitProviderAdapter._validate_operation_ref(parameters.get("source_ref"))
            if parameters.get("strategy") not in {"ff-only", "no-ff"}:
                raise ValueError("git_merge_strategy_invalid")
        else:
            remote = parameters.get("remote_alias")
            if not isinstance(remote, str) or _ALIAS.fullmatch(remote) is None:
                raise ValueError("git_remote_invalid")
            if operation == "remote.pull":
                GitProviderAdapter._validate_ref_name(parameters.get("ref_name"), require_heads_prefix=True)
                if parameters.get("strategy") not in {"ff-only", "no-ff"}:
                    raise ValueError("git_merge_strategy_invalid")
            else:
                GitProviderAdapter._validate_operation_ref(parameters.get("source_ref"))
                GitProviderAdapter._validate_operation_ref(parameters.get("target_ref"))
                expected_remote_value = parameters.get("expected_remote_sha")
                if expected_remote_value is not None:
                    GitProviderAdapter._sha_value(expected_remote_value)
                if parameters.get("force", False) is not False:
                    raise ValueError("git_force_push_forbidden")
    if action in _LOCAL_OPERATION_ACTIONS:
        GitProviderAdapter._sha_value(parameters.get("expected_head_sha"))
        try:
            GitProviderAdapter._validate_ref_name(parameters.get("branch_name"), require_heads_prefix=False)
        except ValueError:
            raise ValueError("git_branch_not_allowed") from None
        if action == "reset.local":
            GitProviderAdapter._sha_value(parameters.get("target_sha"))
            if parameters.get("mode") not in {"soft", "mixed", "hard"}:
                raise ValueError("git_reset_mode_invalid")
            if parameters.get("allow_dirty", False) is not False:
                raise ValueError("git_dirty_override_forbidden")
        elif action == "cherry-pick.local":
            GitProviderAdapter._sha_value(parameters.get("commit_sha"))
            if parameters.get("allow_conflict", False) is not False:
                raise ValueError("git_conflict_override_forbidden")
        else:
            GitProviderAdapter._validate_operation_ref(parameters.get("source_ref"))
            if parameters.get("strategy") not in {"ff-only", "no-ff"}:
                raise ValueError("git_merge_strategy_invalid")
            if parameters.get("allow_conflict", False) is not False:
                raise ValueError("git_conflict_override_forbidden")


def _validate_git_timeout(parameters: Mapping[str, object]) -> None:
    value = parameters.get("timeout_seconds", 5)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 30
    ):
        raise ValueError("git_parameters_invalid")


def _validate_git_path_shape(value: object, *, required: bool) -> None:
    if value is None and not required:
        return
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
        or len(value) > 64
        or any(not isinstance(path, str) or not path for path in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("git_paths_invalid")


def _validate_git_evidence_shape(
    paths: tuple[object, ...], value: object
) -> None:
    if not isinstance(value, Mapping) or set(value) != set(paths):
        raise ValueError("git_final_evidence_invalid")
    for path in paths:
        item = value[path]
        if item is None:
            continue
        if (
            not isinstance(item, Mapping)
            or set(item) != {"blob", "mode"}
            or not isinstance(item["blob"], str)
            or _SHA.fullmatch(item["blob"]) is None
            or item["mode"] not in _MODE
        ):
            raise ValueError("git_final_evidence_invalid")
