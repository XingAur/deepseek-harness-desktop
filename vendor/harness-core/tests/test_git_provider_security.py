from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest
from app.providers.git import GitProviderAdapter
from app.providers.gitlab import GitLabHttpResponse, GitLabProviderAdapter
from app.providers.registry import build_manager_adapter_registry
from app.repository_scope import RepositoryScope


class GitProviderSecurityTests(unittest.TestCase):
    def _git(self, directory: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(directory), *args], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def _repo(self, directory: Path) -> None:
        self._git(directory, "init")
        self._git(directory, "config", "user.email", "test@example.invalid")
        self._git(directory, "config", "user.name", "Harness Test")
        (directory / "safe.txt").write_text("initial\n", encoding="utf-8")
        self._git(directory, "add", "safe.txt")
        self._git(directory, "commit", "-m", "initial")

    def _context(self) -> ProviderExecutionContext:
        return ProviderExecutionContext(profile_id=1, required_credential_fields=(), network_allowed=False, credential_resolver=lambda _id, _field: "")

    def _request(self, action: str, **parameters: object) -> ProviderExecutionRequest:
        return ProviderExecutionRequest(1, "manager", action, {"repository_alias": "repo", **parameters})

    def test_adapter_fails_closed_when_no_replace_objects_flag_is_not_supported(self) -> None:
        with mock.patch("app.providers.git.subprocess.run", return_value=subprocess.CompletedProcess([], 129, b"", b"")):
            with self.assertRaisesRegex(RuntimeError, "git_safety_flags_unavailable"):
                GitProviderAdapter()

    def test_scope_inside_a_parent_repository_is_rejected_without_parent_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "parent"
            child = parent / "child"
            child.mkdir(parents=True)
            self._repo(parent)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", child, allowed_paths=(".",))})

            with self.assertRaisesRegex(ValueError, "(git_scope_not_repository|repository_scope_identity_changed)"):
                adapter.execute(self._request("repo.status.read"), self._context())

    def test_external_git_dir_environment_cannot_redirect_branch_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir(); outside.mkdir()
            self._repo(root); self._repo(outside)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), protected_branches=())})
            base = self._git(root, "rev-parse", "HEAD").strip()
            with mock.patch.dict(os.environ, {"GIT_DIR": str(outside / ".git"), "GIT_WORK_TREE": str(outside)}, clear=False):
                adapter.execute(self._request("branch.create", branch_name="safe-branch", expected_base_sha=base), self._context())

            self.assertIn("safe-branch", self._git(root, "branch", "--format=%(refname:short)").splitlines())
            self.assertNotIn("safe-branch", self._git(outside, "branch", "--format=%(refname:short)").splitlines())

    def test_git_process_uses_a_temporary_snapshot_not_the_configured_repository(self) -> None:
        """A validated source path is evidence only; Git must never execute there."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original = subprocess.Popen
            observed: list[dict[str, object]] = []

            def recording_popen(*args, **kwargs):
                observed.append(dict(kwargs))
                return original(*args, **kwargs)

            with mock.patch("app.providers.git.subprocess.Popen", side_effect=recording_popen):
                adapter.execute(self._request("repo.status.read"), self._context())

            self.assertTrue(observed)
            source_root = str(root.resolve())
            self.assertTrue(all(item.get("cwd") != source_root for item in observed))
            self.assertTrue(all(str(root.resolve() / ".git") != item.get("env", {}).get("GIT_DIR") for item in observed))

    def test_drain_kills_a_child_when_the_process_group_leader_has_already_exited(self) -> None:
        process = subprocess.Popen(
            ["/bin/sh", "-c", "sleep 30 & printf '%s\\n' \"$!\"; exit 0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, _stderr, overflow = GitProviderAdapter._drain(process, 0.05)
        child_pid = int(stdout.decode("ascii").strip())

        self.assertTrue(overflow)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            self.fail("orphaned child survived process-group cleanup")

    def test_drain_attempts_process_group_cleanup_after_a_leader_exits_with_closed_stdio(self) -> None:
        """A detached child can survive after EOF unless finally always killpgs."""
        process = subprocess.Popen(
            ["/bin/sh", "-c", "sleep 30 </dev/null >/dev/null 2>&1 & exit 0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        with mock.patch.object(GitProviderAdapter, "_terminate_group", wraps=GitProviderAdapter._terminate_group) as terminated:
            _stdout, _stderr, overflow = GitProviderAdapter._drain(process, 1)

        self.assertFalse(overflow)
        self.assertGreaterEqual(terminated.call_count, 1)

    def test_fetch_records_live_dispatch_only_after_fetch_process_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            self._git(root, "update-ref", "refs/remotes/origin/main", self._git(root, "rev-parse", "HEAD").strip())
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), remotes=(("origin", "https://gitlab.example.test/group/project.git"),))})
            context = ProviderExecutionContext(profile_id=1, required_credential_fields=(), network_allowed=True, credential_resolver=lambda _id, _field: "")
            original_popen = subprocess.Popen

            def fail_before_fetch_starts(command, *args, **kwargs):
                if "fetch" in command:
                    raise OSError("injected launch failure")
                return original_popen(command, *args, **kwargs)

            with mock.patch("app.providers.git.subprocess.Popen", side_effect=fail_before_fetch_starts):
                with self.assertRaisesRegex(RuntimeError, "git_command_failed"):
                    adapter.execute(self._request("remote.fetch", remote_alias="origin", ref_name="refs/heads/main"), context)

            self.assertEqual(0, context.network_call_count)
            self.assertEqual((), context.network_targets)

    def test_fetch_started_then_failed_records_one_live_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            self._git(root, "update-ref", "refs/remotes/origin/main", self._git(root, "rev-parse", "HEAD").strip())
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), remotes=(("origin", "https://gitlab.example.test/group/project.git"),))})
            context = ProviderExecutionContext(profile_id=1, required_credential_fields=(), network_allowed=True, credential_resolver=lambda _id, _field: "")
            original_popen = subprocess.Popen
            fetch_commands: list[list[str]] = []

            def start_then_fail(command, *args, **kwargs):
                if "fetch" in command:
                    fetch_commands.append(list(command))
                    return original_popen(["/usr/bin/false"], *args, **kwargs)
                return original_popen(command, *args, **kwargs)

            with mock.patch("app.providers.git.subprocess.Popen", side_effect=start_then_fail):
                with self.assertRaisesRegex(RuntimeError, "git_command_failed"):
                    adapter.execute(self._request("remote.fetch", remote_alias="origin", ref_name="refs/heads/main"), context)

            self.assertEqual(1, context.network_call_count)
            self.assertEqual(1, len(fetch_commands))
            self.assertIn("http.followRedirects=false", fetch_commands[0])
            self.assertIn("fetch.unpackLimit=4096", fetch_commands[0])
            self.assertIn("--no-replace-objects", fetch_commands[0])

    def test_fetch_dispatch_callback_failure_reaps_the_started_process_and_marks_an_incident(self) -> None:
        """A started fetch is never allowed to disappear from the audit facts."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            self._git(root, "update-ref", "refs/remotes/origin/main", self._git(root, "rev-parse", "HEAD").strip())
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), remotes=(("origin", "https://gitlab.example.test/group/project.git"),))})
            context = ProviderExecutionContext(profile_id=1, required_credential_fields=(), network_allowed=True, credential_resolver=lambda _id, _field: "")
            pid_file = Path(directory) / "fetch-child.pid"
            started: list[subprocess.Popen[bytes]] = []
            original_popen = subprocess.Popen

            def starts_a_long_lived_fetch_child(command, *args, **kwargs):
                if "fetch" not in command:
                    return original_popen(command, *args, **kwargs)
                process = original_popen(
                    ["/bin/sh", "-c", f"sleep 30 & child=$!; echo $child > {pid_file}; wait $child"],
                    *args,
                    **kwargs,
                )
                started.append(process)
                deadline = time.monotonic() + 1
                while not pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                return process

            try:
                with mock.patch.object(ProviderExecutionContext, "record_network_dispatch", side_effect=RuntimeError("audit storage unavailable")), mock.patch("app.providers.git.subprocess.Popen", side_effect=starts_a_long_lived_fetch_child):
                    with self.assertRaisesRegex(RuntimeError, "git_network_dispatch_audit_unknown"):
                        adapter.execute(self._request("remote.fetch", remote_alias="origin", ref_name="refs/heads/main"), context)

                self.assertEqual(1, context.network_dispatch_incident_count)
                self.assertTrue(started)
                self.assertTrue(pid_file.exists())
                child_pid = int(pid_file.read_text(encoding="ascii").strip())
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("fetch child survived audit callback failure")
                self.assertIsNotNone(started[0].poll())
            finally:
                for process in started:
                    GitProviderAdapter._terminate_group(process)
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        process.wait(timeout=1)

    def test_maximum_legal_fetch_dispatch_identity_is_bounded_and_callback_failure_is_recoverable(self) -> None:
        """Audit identity must not depend on the length of a legal Git ref."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            scope_alias = "a" * 64
            remote_alias = "b" * 64
            ref_name = "refs/heads/" + "f" * 69
            self.assertEqual(80, len(ref_name))
            adapter = GitProviderAdapter({
                scope_alias: RepositoryScope(
                    scope_alias,
                    root,
                    allowed_paths=(".",),
                    remotes=((remote_alias, "https://gitlab.example.test/group/project.git"),),
                ),
            })
            context = ProviderExecutionContext(profile_id=1, required_credential_fields=(), network_allowed=True, credential_resolver=lambda _id, _field: "")
            dispatch_target = adapter._fetch_dispatch_target(adapter._scopes[scope_alias], remote_alias, ref_name)
            self.assertRegex(dispatch_target, r"^git\.dispatch\.[0-9a-f]{64}$")
            self.assertLessEqual(len(dispatch_target), 127)
            self.assertNotIn("https://", dispatch_target)
            self.assertNotIn(ref_name, dispatch_target)
            self.assertEqual(dispatch_target, context.validate_network_target(dispatch_target))
            self._git(root, "update-ref", f"refs/remotes/{remote_alias}/{ref_name.removeprefix('refs/heads/')}", self._git(root, "rev-parse", "HEAD").strip())
            request = ProviderExecutionRequest(1, "manager", "remote.fetch", {"repository_alias": scope_alias, "remote_alias": remote_alias, "ref_name": ref_name})
            original_popen = subprocess.Popen
            started: list[subprocess.Popen[bytes]] = []

            def starts_then_callback_fails(command, *args, **kwargs):
                if "fetch" not in command:
                    return original_popen(command, *args, **kwargs)
                process = original_popen(["/bin/sh", "-c", "sleep 30 & wait"], *args, **kwargs)
                started.append(process)
                return process

            try:
                with mock.patch.object(ProviderExecutionContext, "record_network_dispatch", side_effect=RuntimeError("audit unavailable")), mock.patch("app.providers.git.subprocess.Popen", side_effect=starts_then_callback_fails):
                    with self.assertRaisesRegex(RuntimeError, "git_network_dispatch_audit_unknown"):
                        adapter.execute(request, context)

                self.assertEqual(1, context.network_dispatch_incident_count)
                self.assertEqual((dispatch_target,), context.network_targets)
                self.assertTrue(started)
                self.assertIsNotNone(started[0].poll())
            finally:
                for process in started:
                    GitProviderAdapter._terminate_group(process)
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        process.wait(timeout=1)

    def test_snapshot_manifest_rejects_fifo_before_open_or_git_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            fifo = root / "untrusted.fifo"
            os.mkfifo(fifo)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original_open = os.open

            def forbid_fifo_open(name, flags, *args, **kwargs):
                if os.fspath(name) == "untrusted.fifo":
                    raise AssertionError("manifest opened FIFO before validating its type")
                return original_open(name, flags, *args, **kwargs)

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.open", side_effect=forbid_fifo_open):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source_invalid"):
                    adapter._copy_snapshot(adapter._scopes["repo"], Path(target) / "snapshot")

    def test_manifest_regular_files_are_opened_nonblocking_after_the_type_check(self) -> None:
        """A regular-file-to-FIFO swap must not hang manifest capture."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original_open = os.open
            seen = 0

            def require_nonblocking(name, flags, *args, **kwargs):
                nonlocal seen
                if os.fspath(name) == "safe.txt" and kwargs.get("dir_fd") is not None and not flags & (os.O_WRONLY | os.O_RDWR):
                    seen += 1
                    if not flags & os.O_NONBLOCK:
                        raise AssertionError("manifest_regular_open_must_be_nonblocking")
                return original_open(name, flags, *args, **kwargs)

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.open", side_effect=require_nonblocking):
                adapter._copy_snapshot(adapter._scopes["repo"], Path(target) / "snapshot")

            self.assertGreaterEqual(seen, 2)

    def test_commit_evidence_uses_an_anchored_nonblocking_file_descriptor(self) -> None:
        """Evidence capture must not use Path.exists/open or block on a FIFO race."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original_open = os.open
            seen = 0

            def require_nonblocking(name, flags, *args, **kwargs):
                nonlocal seen
                if os.fspath(name) == "safe.txt" and kwargs.get("dir_fd") is not None and not flags & (os.O_WRONLY | os.O_RDWR):
                    seen += 1
                    if not flags & os.O_NONBLOCK:
                        raise AssertionError("commit_evidence_open_must_be_nonblocking")
                return original_open(name, flags, *args, **kwargs)

            with mock.patch("app.providers.git.os.open", side_effect=require_nonblocking):
                evidence = adapter._file_evidence(adapter._scopes["repo"], "safe.txt", 5)

            self.assertIsNotNone(evidence)
            self.assertGreaterEqual(seen, 1)

    def test_preflight_config_read_never_uses_path_open(self) -> None:
        """Repository config is source input and must be read from an anchored fd."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})

            with mock.patch.object(Path, "open", side_effect=AssertionError("source_config_must_not_use_path_open")):
                adapter._preflight(adapter._scopes["repo"])

    def test_preflight_config_fifo_replacement_fails_closed_without_blocking(self) -> None:
        """A config swap after manifest capture is a bounded no-Popen failure."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            config = root / ".git" / "config"
            original_config = config.read_bytes()
            original_open = os.open
            config_opens = 0

            def replace_config_after_manifest(name, flags, *args, **kwargs):
                nonlocal config_opens
                if os.fspath(name) == "config" and kwargs.get("dir_fd") is not None and not flags & (os.O_WRONLY | os.O_RDWR):
                    config_opens += 1
                    if config_opens == 2:
                        if not flags & os.O_NONBLOCK:
                            raise AssertionError("config_open_must_be_nonblocking")
                        config.unlink()
                        os.mkfifo(config)
                return original_open(name, flags, *args, **kwargs)

            try:
                with mock.patch("app.providers.git.os.open", side_effect=replace_config_after_manifest):
                    with self.assertRaisesRegex(ValueError, "git_unsafe_repository_metadata"):
                        adapter._preflight(adapter._scopes["repo"])
            finally:
                if config.exists() or config.is_symlink():
                    config.unlink()
                config.write_bytes(original_config)

            self.assertEqual(2, config_opens)

    def test_commit_evidence_fifo_replacement_fails_closed_before_file_content_is_read(self) -> None:
        """The evidence reader rejects a final regular-file-to-FIFO swap."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original_open = os.open

            def replace_safe_file(name, flags, *args, **kwargs):
                if os.fspath(name) == "safe.txt" and kwargs.get("dir_fd") is not None and not flags & (os.O_WRONLY | os.O_RDWR):
                    if not flags & os.O_NONBLOCK:
                        raise AssertionError("evidence_open_must_be_nonblocking")
                    safe = root / "safe.txt"
                    safe.unlink()
                    os.mkfifo(safe)
                return original_open(name, flags, *args, **kwargs)

            try:
                with mock.patch("app.providers.git.os.open", side_effect=replace_safe_file):
                    with self.assertRaisesRegex(ValueError, "git_file_not_allowed"):
                        adapter._file_evidence(adapter._scopes["repo"], "safe.txt", 5)
            finally:
                safe = root / "safe.txt"
                if safe.exists() or safe.is_symlink():
                    safe.unlink()

    def test_snapshot_manifest_stops_at_node_limit_before_copy_or_git_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            for index in range(5):
                (root / f"small-{index}.txt").write_text("x", encoding="ascii")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git._SNAPSHOT_FILE_LIMIT", 3), mock.patch.object(adapter, "_copy_manifested_tree", side_effect=AssertionError("manifest limit must reject before copying")):
                with self.assertRaisesRegex(ValueError, "git_snapshot_limit_exceeded"):
                    adapter._copy_snapshot(adapter._scopes["repo"], Path(target) / "snapshot")

    def test_post_preflight_commondir_injection_is_rejected_during_metadata_manifest_capture(self) -> None:
        """A transient shared-worktree marker may never reach a snapshot child."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original_copy = adapter._copy_snapshot
            injected = False

            def inject_then_restore(scope, destination, **kwargs):
                nonlocal injected
                if not injected:
                    marker = root / ".git" / "commondir"
                    marker.write_text("../outside\n", encoding="utf-8")
                    injected = True
                    marker.unlink()
                return original_copy(scope, destination, **kwargs)

            with mock.patch.object(adapter, "_copy_snapshot", side_effect=inject_then_restore), mock.patch("app.providers.git.subprocess.Popen", side_effect=AssertionError("git_must_not_launch")):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source_changed"):
                    adapter.execute(self._request("repo.status.read"), self._context())

            self.assertTrue(injected)

    def test_post_preflight_alternates_injection_is_rejected_during_metadata_manifest_capture(self) -> None:
        """A transient alternate-object-store marker may never reach Git."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original_copy = adapter._copy_snapshot
            injected = False

            def inject_then_restore(scope, destination, **kwargs):
                nonlocal injected
                if not injected:
                    marker = root / ".git" / "objects" / "info" / "alternates"
                    marker.write_text("/tmp/outside\n", encoding="utf-8")
                    injected = True
                    marker.unlink()
                return original_copy(scope, destination, **kwargs)

            with mock.patch.object(adapter, "_copy_snapshot", side_effect=inject_then_restore), mock.patch("app.providers.git.subprocess.Popen", side_effect=AssertionError("git_must_not_launch")):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source_changed"):
                    adapter.execute(self._request("repo.status.read"), self._context())

            self.assertTrue(injected)

    def test_fetch_tracking_fact_is_false_when_snapshot_ref_does_not_change_and_true_when_it_does(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), remotes=(("origin", "https://gitlab.example.test/group/project.git"),))})
            scope = adapter._scopes["repo"]

            @contextlib.contextmanager
            def fake_snapshot(_scope):
                yield root

            request = self._request("remote.fetch", remote_alias="origin", ref_name="refs/heads/main")
            for before, after, expected in (("a" * 40, "a" * 40, False), ("a" * 40, "b" * 40, True)):
                with self.subTest(before=before, after=after), mock.patch.object(adapter, "_preflight"), mock.patch.object(adapter, "_execution_snapshot", fake_snapshot), mock.patch.object(adapter, "_read_ref", return_value=None), mock.patch.object(adapter, "_copy_new_objects") as copied, mock.patch.object(adapter, "_publish_ref") as published:
                    responses = iter((
                        subprocess.CompletedProcess([], 0, (before + "\n").encode(), b""),
                        subprocess.CompletedProcess([], 0, b"", b""),
                        subprocess.CompletedProcess([], 0, (after + "\n").encode(), b""),
                    ))

                    def fake_git(_snapshot, _arguments, _timeout, **kwargs):
                        callback = kwargs.get("on_started")
                        if callback is not None:
                            callback()
                        return next(responses)

                    context = ProviderExecutionContext(profile_id=1, required_credential_fields=(), network_allowed=True, credential_resolver=lambda _id, _field: "")
                    with mock.patch.object(adapter, "_git_snapshot", side_effect=fake_git):
                        result = adapter.execute(request, context)

                self.assertEqual(expected, result["tracking_ref_updated"])
                self.assertEqual(1, context.network_call_count)
                self.assertEqual(expected, copied.called)
                self.assertEqual(expected, published.called)

    def test_fetch_short_object_write_blocks_ref_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as snapshot_directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            snapshot = Path(snapshot_directory) / "snapshot"
            source_object = snapshot / ".git" / "objects" / "aa" / ("b" * 38)
            source_object.parent.mkdir(parents=True)
            source_object.write_bytes(b"object-bytes")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), remotes=(("origin", "https://gitlab.example.test/group/project.git"),))})
            context = ProviderExecutionContext(profile_id=1, required_credential_fields=(), network_allowed=True, credential_resolver=lambda _id, _field: "")

            @contextlib.contextmanager
            def fake_snapshot(_scope):
                yield snapshot

            responses = iter((
                subprocess.CompletedProcess([], 0, ("a" * 40 + "\n").encode(), b""),
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.CompletedProcess([], 0, ("b" * 40 + "\n").encode(), b""),
            ))

            def fake_git(_snapshot, _arguments, _timeout, **kwargs):
                callback = kwargs.get("on_started")
                if callback is not None:
                    callback()
                return next(responses)

            original_write = os.write

            def zero_first_object_write(descriptor, data):
                if data == b"object-bytes":
                    return 0
                return original_write(descriptor, data)

            request = self._request("remote.fetch", remote_alias="origin", ref_name="refs/heads/main")
            with mock.patch.object(adapter, "_preflight"), mock.patch.object(adapter, "_execution_snapshot", fake_snapshot), mock.patch.object(adapter, "_read_ref", return_value=None), mock.patch.object(adapter, "_git_snapshot", side_effect=fake_git), mock.patch("app.providers.git.os.write", side_effect=zero_first_object_write), mock.patch.object(adapter, "_publish_ref") as published:
                with self.assertRaisesRegex(RuntimeError, "git_publish_unknown"):
                    adapter.execute(request, context)

            self.assertFalse(published.called)

    def test_object_copy_retries_a_positive_short_write_until_the_object_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source-object"
            destination = root / "destination-object"
            source.write_bytes(b"object-bytes")
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            original_write = os.write
            shortened = False

            def short_once(fd, data):
                nonlocal shortened
                if fd == descriptor and not shortened:
                    shortened = True
                    original_write(fd, data[:1])
                    return 1
                return original_write(fd, data)

            try:
                with mock.patch("app.providers.git.os.write", side_effect=short_once):
                    GitProviderAdapter._copy_fd_file(source, descriptor)
            finally:
                os.close(descriptor)

            self.assertTrue(shortened)
            self.assertEqual(source.read_bytes(), destination.read_bytes())

    def test_config_replacement_after_snapshot_cannot_change_the_git_process_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original = subprocess.Popen
            replaced = False

            def replacing_popen(*args, **kwargs):
                nonlocal replaced
                if not replaced:
                    replaced = True
                    (root / ".git" / "config").write_text("[http]\n\tproxy = https://invalid.example\n", encoding="utf-8")
                    (root / ".gitattributes").write_text("* filter=unsafe\n", encoding="utf-8")
                return original(*args, **kwargs)

            with mock.patch("app.providers.git.subprocess.Popen", side_effect=replacing_popen):
                result = adapter.execute(self._request("repo.status.read"), self._context())

            self.assertTrue(replaced)
            self.assertEqual(0, result["changed_file_count"])

    def test_snapshot_copy_rejects_a_nested_directory_replacement_after_manifest_capture(self) -> None:
        """The source copy must stay on anchored descriptors and detect swaps."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            outside = Path(directory) / "outside"
            root.mkdir(); outside.mkdir()
            self._repo(root)
            nested = root / "nested"
            nested.mkdir()
            (nested / "safe.txt").write_text("reviewed\n", encoding="utf-8")
            (outside / "safe.txt").write_text("outside\n", encoding="utf-8")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            scope = adapter._scopes["repo"]
            original_scandir = os.scandir
            nested_identity = nested.stat()
            replaced = False

            def swap_after_manifest(path):
                nonlocal replaced
                is_nested = (
                    isinstance(path, int)
                    and (lambda item: (item.st_dev, item.st_ino) == (nested_identity.st_dev, nested_identity.st_ino))(os.fstat(path))
                ) or (not isinstance(path, int) and Path(path) == nested)
                if is_nested and not replaced:
                    replaced = True
                    nested.rename(root / "nested-original")
                    os.symlink(outside, nested)
                return original_scandir(path)

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.scandir", side_effect=swap_after_manifest):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source"):
                    adapter._copy_snapshot(scope, Path(target) / "snapshot")

            self.assertTrue(replaced)

    def test_snapshot_copy_rejects_root_directory_replacement_after_manifest_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            outside = Path(directory) / "outside"
            root.mkdir(); outside.mkdir(); self._repo(root)
            (outside / "safe.txt").write_text("outside\n", encoding="utf-8")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            scope = adapter._scopes["repo"]
            original_scandir = os.scandir
            root_identity = root.stat()
            root_scans = 0
            replaced = False

            def swap_root_after_manifest(path):
                nonlocal replaced, root_scans
                if isinstance(path, int):
                    item = os.fstat(path)
                    if (item.st_dev, item.st_ino) == (root_identity.st_dev, root_identity.st_ino):
                        root_scans += 1
                        if root_scans == 2 and not replaced:
                            replaced = True
                            root.rename(Path(directory) / "repo-original")
                            os.symlink(outside, root)
                return original_scandir(path)

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.scandir", side_effect=swap_root_after_manifest):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source_changed"):
                    adapter._copy_snapshot(scope, Path(target) / "snapshot")

            self.assertTrue(replaced)

    def test_snapshot_copy_rejects_attribute_injection_even_when_source_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            scope = adapter._scopes["repo"]
            original_open = os.open
            safe_opens = 0
            injected = False

            def inject_then_restore(name, flags, *args, **kwargs):
                nonlocal injected, safe_opens
                if os.fspath(name) == "safe.txt" and kwargs.get("dir_fd") is not None and not (flags & (os.O_WRONLY | os.O_RDWR)):
                    safe_opens += 1
                    if safe_opens == 2:
                        attributes = root / ".gitattributes"
                        attributes.write_text("safe.txt filter=unsafe\n", encoding="utf-8")
                        attributes.unlink()
                        injected = True
                return original_open(name, flags, *args, **kwargs)

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.open", side_effect=inject_then_restore):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source_changed"):
                    adapter._copy_snapshot(scope, Path(target) / "snapshot")

            self.assertTrue(injected)
            self.assertFalse((root / ".gitattributes").exists())

    def test_snapshot_copy_counts_bytes_observed_during_copy_not_stale_directory_stat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            scope = adapter._scopes["repo"]
            original_open = os.open
            observed: dict[str, int] = {"first.txt": 0, "second.txt": 0}

            def grow_before_copy(name, flags, *args, **kwargs):
                name_text = os.fspath(name)
                key = Path(name_text).name
                if key in observed and not (flags & (os.O_WRONLY | os.O_RDWR)):
                    observed[key] += 1
                    # Existing pathname copy opens each file once.  An anchored
                    # manifest implementation opens it once to capture then a
                    # second time to copy, so grow at the matching copy point.
                    if observed[key] == (1 if kwargs.get("dir_fd") is None else 2):
                        (root / key).write_bytes(b"a" * (17 * 1024 * 1024))
                return original_open(name, flags, *args, **kwargs)

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.open", side_effect=grow_before_copy):
                with self.assertRaisesRegex(ValueError, "git_snapshot_(limit_exceeded|source_changed|source_invalid)"):
                    adapter._copy_snapshot(scope, Path(target) / "snapshot")

            self.assertEqual(2, observed["first.txt"])

    def test_snapshot_copy_rejects_fifo_replacing_a_manifested_regular_file_before_open(self) -> None:
        """The copy open must be non-blocking after an entry identity check."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            scope = adapter._scopes["repo"]
            original_open = os.open
            safe_opens = 0

            def replace_file_before_copy(name, flags, *args, **kwargs):
                nonlocal safe_opens
                if os.fspath(name) == "safe.txt" and kwargs.get("dir_fd") is not None and not (flags & (os.O_WRONLY | os.O_RDWR)):
                    safe_opens += 1
                    if safe_opens == 2:
                        # A blocking O_RDONLY open here would hang on the FIFO.
                        # The code must request O_NONBLOCK before it can inspect
                        # the replacement's identity.
                        if not flags & os.O_NONBLOCK:
                            raise AssertionError("copy_regular_open_must_be_nonblocking")
                        (root / "safe.txt").unlink()
                        os.mkfifo(root / "safe.txt")
                return original_open(name, flags, *args, **kwargs)

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.open", side_effect=replace_file_before_copy):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source_changed"):
                    adapter._copy_snapshot(scope, Path(target) / "snapshot")

            self.assertEqual(2, safe_opens)

    def test_snapshot_copy_rejects_fifo_replacing_a_manifested_directory_before_open(self) -> None:
        """A directory replacement is rejected before a recursion can begin."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            nested = root / "nested"
            nested.mkdir()
            (nested / "safe.txt").write_text("reviewed\n", encoding="utf-8")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            scope = adapter._scopes["repo"]
            original_open = os.open
            nested_opens = 0

            def replace_directory_before_copy(name, flags, *args, **kwargs):
                nonlocal nested_opens
                if os.fspath(name) == "nested" and kwargs.get("dir_fd") is not None and not (flags & (os.O_WRONLY | os.O_RDWR)):
                    nested_opens += 1
                    if nested_opens == 2:
                        if not flags & os.O_DIRECTORY:
                            raise AssertionError("copy_directory_open_must_require_directory")
                        nested.rename(root / "nested-original")
                        os.mkfifo(root / "nested")
                return original_open(name, flags, *args, **kwargs)

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.open", side_effect=replace_directory_before_copy):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source_changed"):
                    adapter._copy_snapshot(scope, Path(target) / "snapshot")

            self.assertEqual(2, nested_opens)

    def test_snapshot_copy_zero_write_fails_without_retrying_forever(self) -> None:
        """Snapshot copy shares the fail-closed short-write primitive."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            scope = adapter._scopes["repo"]
            original_write = os.write
            writes = 0

            def zero_then_fail(descriptor, data):
                nonlocal writes
                writes += 1
                if writes == 1:
                    return 0
                raise AssertionError("copy_snapshot_write_looped_after_zero")

            with tempfile.TemporaryDirectory() as target, mock.patch("app.providers.git.os.write", side_effect=zero_then_fail):
                with self.assertRaisesRegex(ValueError, "git_snapshot_source_invalid"):
                    adapter._copy_snapshot(scope, Path(target) / "snapshot")

            self.assertEqual(1, writes)

    def test_write_lock_zero_write_fails_without_retrying_forever(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "lock"
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            writes = 0

            def zero_then_fail(_descriptor, _data):
                nonlocal writes
                writes += 1
                if writes == 1:
                    return 0
                raise AssertionError("write_lock_looped_after_zero")

            try:
                with mock.patch("app.providers.git.os.write", side_effect=zero_then_fail):
                    with self.assertRaisesRegex(RuntimeError, "git_publish_unknown"):
                        GitProviderAdapter._write_lock(descriptor, b"prepared")
            finally:
                os.close(descriptor)

            self.assertEqual(1, writes)

    def test_partial_object_copy_failure_is_removed_then_a_retry_copies_the_full_object(self) -> None:
        """A residual object may never be treated as a successful prior copy."""
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as snapshot_directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            snapshot = Path(snapshot_directory) / "snapshot"
            source_object = snapshot / ".git" / "objects" / "aa" / ("b" * 38)
            source_object.parent.mkdir(parents=True)
            source_object.write_bytes(b"object-bytes")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            destination = root / ".git" / "objects" / "aa" / ("b" * 38)
            original_write = os.write
            writes = 0

            def partial_then_zero(descriptor, data):
                nonlocal writes
                if data != b"object-bytes" and data != b"bject-bytes":
                    return original_write(descriptor, data)
                if data and writes == 0:
                    writes += 1
                    original_write(descriptor, data[:1])
                    return 1
                if data:
                    writes += 1
                    return 0
                return original_write(descriptor, data)

            with mock.patch("app.providers.git.os.write", side_effect=partial_then_zero):
                with self.assertRaisesRegex(RuntimeError, "git_publish_unknown"):
                    adapter._copy_new_objects(snapshot / ".git", adapter._scopes["repo"])

            self.assertFalse(destination.exists())
            adapter._copy_new_objects(snapshot / ".git", adapter._scopes["repo"])
            self.assertEqual(b"object-bytes", destination.read_bytes())

    def test_object_cleanup_failure_leaves_durable_recovery_marker_that_blocks_git_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as snapshot_directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            snapshot = Path(snapshot_directory) / "snapshot"
            source_object = snapshot / ".git" / "objects" / "aa" / ("b" * 38)
            source_object.parent.mkdir(parents=True)
            source_object.write_bytes(b"object-bytes")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            original_write = os.write
            original_unlink = os.unlink

            def zero_write(descriptor, data):
                if data == b"object-bytes":
                    return 0
                return original_write(descriptor, data)

            def reject_object_cleanup(name, *args, **kwargs):
                if os.fspath(name) == "b" * 38:
                    raise PermissionError("injected cleanup failure")
                return original_unlink(name, *args, **kwargs)

            with mock.patch("app.providers.git.os.write", side_effect=zero_write), mock.patch("app.providers.git.os.unlink", side_effect=reject_object_cleanup):
                with self.assertRaisesRegex(RuntimeError, "git_publish_unknown"):
                    adapter._copy_new_objects(snapshot / ".git", adapter._scopes["repo"])

            marker = root / ".git" / "harness-fetch-recovery.json"
            self.assertTrue(marker.is_file())
            with self.assertRaisesRegex(ValueError, "git_transaction_recovery_required"):
                adapter.execute(self._request("repo.status.read"), self._context())

    def test_object_fsync_failure_removes_the_new_object_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as snapshot_directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            snapshot = Path(snapshot_directory) / "snapshot"
            source_object = snapshot / ".git" / "objects" / "aa" / ("b" * 38)
            source_object.parent.mkdir(parents=True)
            source_object.write_bytes(b"object-bytes")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            destination = root / ".git" / "objects" / "aa" / ("b" * 38)
            original_fsync = os.fsync
            fsync_calls = 0

            def fail_destination_fsync(descriptor):
                nonlocal fsync_calls
                fsync_calls += 1
                # marker file + .git dir are durable first; the third fsync is
                # the newly-created destination object.
                if fsync_calls == 3:
                    raise OSError("injected object fsync failure")
                return original_fsync(descriptor)

            with mock.patch("app.providers.git.os.fsync", side_effect=fail_destination_fsync):
                with self.assertRaisesRegex(RuntimeError, "git_publish_unknown"):
                    adapter._copy_new_objects(snapshot / ".git", adapter._scopes["repo"])

            self.assertFalse(destination.exists())
            self.assertFalse((root / ".git" / "harness-fetch-recovery.json").exists())
            adapter._copy_new_objects(snapshot / ".git", adapter._scopes["repo"])
            self.assertEqual(b"object-bytes", destination.read_bytes())

    def test_fetch_snapshot_pack_is_rejected_before_source_writes_or_recovery_marker(self) -> None:
        """Fetched pack payload must never cross from the private snapshot."""
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as snapshot_directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            snapshot_git = Path(snapshot_directory) / "snapshot" / ".git"
            payload = snapshot_git / "objects" / "pack" / "untrusted.pack"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"pack")
            objects_before = {
                path.relative_to(root / ".git" / "objects").as_posix(): path.read_bytes()
                for path in (root / ".git" / "objects").rglob("*")
                if path.is_file()
            }

            with self.assertRaisesRegex(RuntimeError, "git_snapshot_invalid"):
                adapter._copy_new_objects(snapshot_git, adapter._scopes["repo"])

            objects_after = {
                path.relative_to(root / ".git" / "objects").as_posix(): path.read_bytes()
                for path in (root / ".git" / "objects").rglob("*")
                if path.is_file()
            }
            self.assertEqual(objects_before, objects_after)
            self.assertFalse((root / ".git" / "harness-fetch-recovery.json").exists())
            self.assertFalse((root / ".git" / "refs" / "remotes" / "origin" / "main").exists())
            self.assertEqual(0, adapter.execute(self._request("repo.status.read"), self._context())["changed_file_count"])

    def test_snapshot_manifest_rejects_replace_and_unknown_ref_namespaces(self) -> None:
        """Only heads and remote-tracking loose refs belong in an execution view."""
        for relative in ("replace/" + "a" * 40, "notes/review"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "repo"
                root.mkdir(); self._repo(root)
                forbidden = root / ".git" / "refs" / relative
                forbidden.parent.mkdir(parents=True, exist_ok=True)
                forbidden.write_text("a" * 40 + "\n", encoding="ascii")
                adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})

                with self.assertRaisesRegex(ValueError, "git_unsafe_repository_metadata"):
                    adapter._preflight(adapter._scopes["repo"])

    def test_nested_branch_and_first_tracking_ref_create_parents_without_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), protected_branches=())})
            base = self._git(root, "rev-parse", "HEAD").strip()

            adapter.execute(self._request("branch.create", branch_name="feature/x", expected_base_sha=base), self._context())
            self.assertEqual(base, self._git(root, "rev-parse", "refs/heads/feature/x").strip())

            tracking_parts = adapter._relative_ref("refs/heads/main", tracking=True, remote="origin")
            adapter._publish_ref(adapter._scopes["repo"], tracking_parts, base, expected=None)
            self.assertEqual(base, self._git(root, "rev-parse", "refs/remotes/origin/main").strip())
            self.assertEqual(0, adapter.execute(self._request("repo.status.read"), self._context())["changed_file_count"])

    def test_ref_components_are_rejected_before_plan_render_or_publish(self) -> None:
        """Every write/fetch path shares Git's unsafe-component boundary."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            adapter = GitProviderAdapter({
                "repo": RepositoryScope(
                    "repo", root, allowed_paths=(".",), protected_branches=(),
                    remotes=(("origin", "https://gitlab.example.test/group/project.git"),),
                )
            })
            base = self._git(root, "rev-parse", "HEAD").strip()
            before_refs = self._git(root, "show-ref")

            invalid_branches = (
                ".foo", "feature/.foo", "feature/foo.", "feature/foo.lock", "feature/foo.LOCK",
                "", "feature//x", "feature/../x", "@", "feature/@", "feature/@{x",
                "feature/a~b", "feature/a^b", "feature/a:b", "feature/a?b", "feature/a*b",
                "feature/a[b", "feature/a\\b", "feature/white space", "feature/tab\tname",
                "feature/newline\nname",
            )
            for branch in invalid_branches:
                with self.subTest(action="branch.create", branch=branch):
                    with self.assertRaisesRegex(ValueError, "git_branch_not_allowed"):
                        adapter.render_plan(self._request("branch.create", branch_name=branch, expected_base_sha=base))
                with self.subTest(action="commit.create", branch=branch):
                    with self.assertRaisesRegex(ValueError, "git_branch_not_allowed"):
                        adapter.render_plan(self._request(
                            "commit.create", branch_name=branch, expected_parent=base,
                            file_list=["safe.txt"], expected_file_blobs={"safe.txt": None}, message="safe",
                        ))

            invalid_fetch_refs = (
                "refs/heads/.foo", "refs/heads/feature/foo.", "refs/heads/feature/foo.lock",
                "refs/heads/feature/foo.LOCK", "refs/heads/", "refs/heads/feature//x",
                "refs/heads/feature/../x", "refs/heads/@", "refs/heads/feature/@{x",
                "refs/heads/feature/a~b", "refs/heads/feature/a^b", "refs/heads/feature/a:b",
                "refs/heads/feature/a?b", "refs/heads/feature/a*b", "refs/heads/feature/a[b",
                "refs/heads/feature/a\\b", "refs/heads/feature/white space",
                "refs/heads/feature/tab\tname", "refs/heads/feature/newline\nname",
                "refs/tags/main", "refs/remotes/origin/main", "main",
            )
            for ref_name in invalid_fetch_refs:
                with self.subTest(action="remote.fetch", ref_name=ref_name):
                    with self.assertRaisesRegex(ValueError, "git_refspec_not_allowed"):
                        adapter.render_plan(self._request("remote.fetch", remote_alias="origin", ref_name=ref_name))

            self.assertEqual("feature/nested/x", adapter.render_plan(self._request(
                "branch.create", branch_name="feature/nested/x", expected_base_sha=base,
            ))["change"]["after"])
            self.assertEqual("refs/heads/feature/nested/x", adapter.render_plan(self._request(
                "remote.fetch", remote_alias="origin", ref_name="refs/heads/feature/nested/x",
            ))["change"]["ref_name"])
            self.assertEqual(before_refs, self._git(root, "show-ref"))

    def test_snapshot_metadata_reuses_ref_component_validation(self) -> None:
        """Existing loose refs must obey the same grammar as planned refs."""
        invalid_leaves = (".foo", "foo.", "foo.lock", "foo.LOCK")
        for namespace in ("heads", "remotes/origin"):
            for leaf in invalid_leaves:
                with self.subTest(namespace=namespace, leaf=leaf), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory) / "repo"
                    root.mkdir(); self._repo(root)
                    base = self._git(root, "rev-parse", "HEAD").strip()
                    ref = root / ".git" / "refs" / namespace / "feature" / leaf
                    ref.parent.mkdir(parents=True, exist_ok=True)
                    ref.write_text(base + "\n", encoding="ascii")
                    adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})

                    with self.assertRaisesRegex(ValueError, "git_refspec_not_allowed"):
                        adapter._preflight(adapter._scopes["repo"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            base = self._git(root, "rev-parse", "HEAD").strip()
            for namespace in ("heads/feature/nested", "remotes/origin/feature/nested"):
                ref = root / ".git" / "refs" / namespace / "x"
                ref.parent.mkdir(parents=True, exist_ok=True)
                ref.write_text(base + "\n", encoding="ascii")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
            adapter._preflight(adapter._scopes["repo"])

    def test_ref_helpers_revalidate_components_before_publish(self) -> None:
        with self.assertRaisesRegex(ValueError, "git_refspec_not_allowed"):
            GitProviderAdapter._relative_ref("feature/.foo")
        with self.assertRaisesRegex(ValueError, "git_refspec_not_allowed"):
            GitProviderAdapter._relative_ref("refs/heads/foo.lock", tracking=True, remote="origin")

    def test_nested_ref_parent_symlink_is_rejected_without_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            outside = Path(directory) / "outside"
            root.mkdir(); outside.mkdir(); self._repo(root)
            (root / ".git" / "refs" / "heads" / "feature").symlink_to(outside, target_is_directory=True)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), protected_branches=())})
            git_fd = adapter._scopes["repo"].open_git_fd()
            try:
                with self.assertRaisesRegex(ValueError, "git_unsafe_repository_metadata"):
                    adapter._open_parent(git_fd, ("refs", "heads", "feature", "x"), create_missing=True)
            finally:
                os.close(git_fd)

            self.assertFalse((outside / "x").exists())

    def test_local_textconv_and_hook_configuration_never_executes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            marker = root / "marker"
            script = root / "unsafe.sh"
            script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
            script.chmod(0o755)
            (root / ".gitattributes").write_text("safe.txt diff=unsafe\n", encoding="utf-8")
            self._git(root, "config", "diff.unsafe.textconv", str(script))
            (root / "safe.txt").write_text("changed\n", encoding="utf-8")
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})

            with self.assertRaisesRegex(ValueError, "git_unsafe_repository_configuration"):
                adapter.execute(self._request("repo.diff.read", file_list=["safe.txt"]), self._context())

            self.assertFalse(marker.exists())

    def test_repository_hook_is_disabled_for_ref_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            marker = root / "hook-marker"
            hook = root / ".git" / "hooks" / "reference-transaction"
            hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
            hook.chmod(0o755)
            adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",), protected_branches=())})
            base = self._git(root, "rev-parse", "HEAD").strip()

            adapter.execute(self._request("branch.create", branch_name="safe-branch", expected_base_sha=base), self._context())

            self.assertFalse(marker.exists())

    def test_gitfile_scope_is_rejected_before_a_git_command_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir(); self._repo(root)
            git_dir = root / ".git"
            git_dir.rename(root / ".git-real")
            git_dir.write_text("gitdir: .git-real\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "repository_scope_root_invalid"):
                RepositoryScope("repo", root, allowed_paths=(".",))

    def test_external_metadata_and_all_attribute_sources_fail_closed(self) -> None:
        for relative in ("commondir", "objects/info/alternates", "config.worktree", "packed-refs", "sharedindex.test", "info/attributes", "nested/.gitattributes"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "repo"; root.mkdir(); self._repo(root)
                target = root / ".git" / relative if relative.startswith(("commondir", "objects", "config", "packed-refs", "sharedindex", "info")) else root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("unsafe\n", encoding="utf-8")
                adapter = GitProviderAdapter({"repo": RepositoryScope("repo", root, allowed_paths=(".",))})
                with self.assertRaisesRegex(ValueError, "git_unsafe_repository"):
                    adapter.execute(self._request("repo.status.read"), self._context())


class RegistryAndTransportSecurityTests(unittest.TestCase):
    def test_registry_is_immutable_and_does_not_accept_fake_transport_in_production_builder(self) -> None:
        registry = build_manager_adapter_registry()
        with self.assertRaises(TypeError):
            registry["git"] = object()  # type: ignore[index]
        with self.assertRaises(TypeError):
            build_manager_adapter_registry(gitlab_transport=lambda **_kwargs: GitLabHttpResponse(200, {}, b"{}"))  # type: ignore[call-arg]

    def test_explicit_simulated_transport_marks_dispatch_provenance(self) -> None:
        calls: list[object] = []
        def fake(**kwargs):
            calls.append(kwargs)
            return GitLabHttpResponse(200, {}, b'{"id":1,"path_with_namespace":"group/project"}')
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=fake, simulated=True)
        context = ProviderExecutionContext(profile_id=2, required_credential_fields=("access_token",), network_allowed=True, credential_resolver=lambda _id, _field: "fake")

        result = adapter.execute(ProviderExecutionRequest(1, "manager", "project.read", {"host_alias": "corp", "project_alias": "group/project"}), context)

        self.assertEqual(0, context.network_call_count)
        self.assertEqual(1, context.simulated_dispatch_count)
        self.assertTrue(context.network_simulated)
        self.assertEqual("simulated", result["execution_provenance"])
        self.assertEqual(1, len(calls))

    def test_network_target_rejects_urls_and_simulation_is_not_an_external_call(self) -> None:
        context = ProviderExecutionContext(profile_id=2, required_credential_fields=(), network_allowed=True, credential_resolver=lambda _id, _field: "")
        with self.assertRaises(ValueError):
            context.record_network_dispatch("https://token@example.test", simulated=True)
        with self.assertRaises(ValueError):
            context.record_network_dispatch("gitlab.corp.group.project.mr7", simulated=True)
        context.record_network_dispatch("gl-h4-corp-g5-group-p7-project-m7", simulated=True)
        self.assertEqual(0, context.network_call_count)


if __name__ == "__main__":
    unittest.main()
