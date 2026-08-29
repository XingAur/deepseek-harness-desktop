from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


_EXPLICIT_TEST_DB = os.environ.get("HARNESS_DB_PATH", "")
if not _EXPLICIT_TEST_DB or not Path(_EXPLICIT_TEST_DB).is_absolute() or not _EXPLICIT_TEST_DB.startswith("/private/tmp/"):
    raise RuntimeError("Task7 tests require an explicit fresh /private/tmp HARNESS_DB_PATH")

from app import database
from app import runtime_policy
from app import local_agent_repository as repository_module
from app.codex_cli_worker import CodexWorkerResult
from app.local_agent_repository import LocalAgentRunRepository, _read_process_start_identity
from app.local_agent_review import LocalAgentReviewer, canonical_review_hash
from app.local_agent_runner import LocalAgentRunner
from tools import task_manager as cli


def _worker_result(*, payload: dict[str, object] | None = None, error_code: str = "") -> CodexWorkerResult:
    encoded = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return CodexWorkerResult(
        exit_code=0 if not error_code else 1,
        error_code=error_code,
        primary_error_code=error_code,
        cleanup_error_code="",
        pid=os.getpid(),
        process_start_identity=_read_process_start_identity(os.getpid()),
        stdout_sha256="0" * 64,
        stderr_sha256="0" * 64,
        event_count=0,
        final_response=payload,
        final_response_sha256=hashlib.sha256(encoded).hexdigest() if encoded else "",
        final_response_validated=False,
        untrusted_final_response=payload is not None,
        canonical_final_response_sha256=hashlib.sha256(encoded).hexdigest() if encoded else "",
    )


class _CodeWorker:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def start(self, request, sink):
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        if self.fail:
            return _worker_result(error_code="worker_process_failed")
        (request.worktree_path / "calculator.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        return _worker_result()


class _ReviewWorker:
    def start(self, request, sink):
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        payload: dict[str, object] = {
            "schema_version": "his-local-agent-review.v1",
            "verdict": "approved",
            "findings": [],
            "summary": "No blocking findings.",
        }
        payload["review_hash"] = canonical_review_hash(payload)
        return _worker_result(payload=payload)


def _fake_runner(repository: LocalAgentRunRepository, worktree_root: Path, *, fail: bool = False) -> LocalAgentRunner:
    reviewer = LocalAgentReviewer(
        repository=repository, worker=_ReviewWorker(), artifact_root=worktree_root
    )
    return LocalAgentRunner(
        repository=repository,
        worker=_CodeWorker(fail=fail),
        reviewer=reviewer,
        worktree_root=worktree_root,
    )


class LocalAgentCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="his_harness_stage_f_cli_", dir="/private/tmp")
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "control.sqlite"
        self.knowledge_home = self.root / "knowledge"
        self.worktree_tmp = tempfile.TemporaryDirectory(prefix="his_harness_stage_f_cli_worktrees_", dir="/private/tmp")
        self.worktree_root = Path(self.worktree_tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self._git("init")
        self._git("config", "user.email", "harness@example.test")
        self._git("config", "user.name", "Harness Test")
        (self.project / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.project / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\n\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(3, add(1, 2))\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "initial")
        self.initial_head = self._git_text("rev-parse", "HEAD")
        self.contract = self.root / "task.json"
        payload = json.loads((Path(__file__).parent / "fixtures" / "local_agent_task.json").read_text(encoding="utf-8"))
        payload["project_path"] = str(self.project)
        payload["verification_commands"][0][0] = sys.executable
        self.contract.write_text(json.dumps(payload), encoding="utf-8")
        self.contract.chmod(0o600)
        self.previous_database_path = database.DB_PATH

    def tearDown(self) -> None:
        subprocess.run(["git", "worktree", "prune"], cwd=self.project, check=False, capture_output=True)
        database.DB_PATH = self.previous_database_path
        self.worktree_tmp.cleanup()
        self.tmp.cleanup()

    def _git(self, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=self.project, check=True, capture_output=True)

    def _git_text(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=self.project, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _invoke(self, arguments: list[str], *, stdin: str | object = "") -> tuple[int, dict[str, object], str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        input_stream = io.StringIO(stdin) if isinstance(stdin, str) else stdin
        with patch("sys.stdin", input_stream), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.local_agent_main(arguments)
        rendered = stdout.getvalue()
        self.assertEqual(1, len(rendered.splitlines()), rendered)
        return code, json.loads(rendered), rendered, stderr.getvalue()

    def _base(self) -> list[str]:
        return ["--db-path", str(self.db_path)]

    def _run_args(self, authorization: str = "cli-one-time-authorization") -> list[str]:
        return [
            "run", *self._base(), "--knowledge-home", str(self.knowledge_home),
            "--contract", str(self.contract), "--worktree-root", str(self.worktree_root),
            "--allow-real-agent", "--authorization-id", authorization,
        ]

    def _runner_factory(self, *, fail: bool = False):
        return lambda repository, worktree_root: _fake_runner(
            repository, worktree_root, fail=fail
        )

    def _subprocess(self, arguments: list[str], *, stdin: str = "") -> tuple[int, dict[str, object], str, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HARNESS_DB_PATH": str(self.db_path),
            "HIS_KNOWLEDGE_HOME": str(self.knowledge_home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [sys.executable, str(cli.PROJECT_ROOT / "tools" / "task_manager.py"), "local-agent", *arguments],
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        self.assertEqual(1, len(completed.stdout.splitlines()), completed.stdout)
        return completed.returncode, json.loads(completed.stdout), completed.stdout, completed.stderr

    def _subprocess_fake_run(self, arguments: list[str]) -> tuple[int, dict[str, object], str, str]:
        script = (
            "import sys\n"
            "from unittest.mock import patch\n"
            "from tools import task_manager as cli\n"
            "from tests.test_local_agent_cli import _fake_runner\n"
            "with patch.object(cli, '_build_local_agent_runner', "
            "side_effect=lambda repository, worktree_root: _fake_runner(repository, worktree_root)):\n"
            "    raise SystemExit(cli.local_agent_main(sys.argv[1:]))\n"
        )
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HARNESS_DB_PATH": str(self.db_path),
            "HIS_KNOWLEDGE_HOME": str(self.knowledge_home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [sys.executable, "-c", script, *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        self.assertEqual(1, len(completed.stdout.splitlines()), completed.stdout)
        return completed.returncode, json.loads(completed.stdout), completed.stdout, completed.stderr

    def _create_protected_sqlite(self, path: Path) -> dict[str, bytes]:
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("create table protected_sentinel(value text not null)")
                connection.execute("insert into protected_sentinel values('must-stay-unchanged')")
                connection.execute(f"pragma user_version = {database.HARNESS_SCHEMA_VERSION}")
        path.chmod(0o600)
        return {item.name: item.read_bytes() for item in path.parent.glob(path.name + "*")}

    def _sqlite_family(self, path: Path) -> dict[str, bytes]:
        return {item.name: item.read_bytes() for item in path.parent.glob(path.name + "*")}

    def test_help_missing_arguments_and_default_database_are_json_only(self) -> None:
        code, payload, rendered, stderr = self._subprocess(["--help"])
        self.assertEqual(0, code)
        self.assertTrue(payload["ok"])
        self.assertIn("confirm-apply", payload["help"])
        self.assertNotIn("--token", rendered)
        self.assertEqual("", stderr)
        self.assertFalse(self.db_path.exists())

        code, payload, _, stderr = self._invoke(["run"])
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_arguments_invalid", payload["error_code"])
        self.assertEqual("", stderr)

        without_activation = self._run_args("activation-required")
        without_activation.remove("--allow-real-agent")
        code, payload, _, stderr = self._invoke(without_activation)
        self.assertEqual(2, code)
        self.assertEqual("local_agent_run_not_allowed", payload["error_code"])
        self.assertEqual("", stderr)
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.knowledge_home.exists())

        default_db = Path(cli.PROJECT_ROOT) / "data" / "harness.sqlite"
        code, payload, _, stderr = self._invoke(["status", "--db-path", str(default_db), "--run-id", "1"])
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_database_not_temporary", payload["error_code"])
        self.assertEqual("", stderr)

        insecure_parent = self.root / "shared-control"
        insecure_parent.mkdir(mode=0o755)
        insecure_db = insecure_parent / "harness.sqlite"
        insecure_args = self._run_args("private-control-directory-required")
        insecure_args[insecure_args.index(str(self.db_path))] = str(insecure_db)
        with patch.object(cli, "_build_local_agent_runner", side_effect=AssertionError("must not start")):
            code, payload, _, stderr = self._invoke(insecure_args)
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_path_invalid", payload["error_code"])
        self.assertFalse(insecure_db.exists())
        self.assertEqual("", stderr)

        argv_token = "argv-token-must-never-be-accepted"
        code, payload, rendered, stderr = self._subprocess([
            "confirm-apply", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", "1", "--requested-by", "local-user", "--token", argv_token,
        ])
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_arguments_invalid", payload["error_code"])
        self.assertNotIn(argv_token, rendered + stderr)

    def test_auto_repair_cli_dispatches_with_explicit_round_budget(self) -> None:
        code, first, _, _ = self._subprocess_fake_run(
            self._run_args("cli-auto-repair-dispatch")
        )
        self.assertEqual(0, code)
        run_id = int(first["snapshot"]["run_id"])

        code, repaired, _, stderr = self._subprocess_fake_run([
            "auto-repair", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--max-rounds", "1",
        ])

        self.assertEqual(0, code)
        self.assertEqual("local-agent auto-repair", repaired["command"])
        self.assertEqual("awaiting_human_confirmation", repaired["snapshot"]["status"])
        self.assertEqual("", stderr)

    def test_fake_full_loop_is_local_only_and_confirmation_is_single_use(self) -> None:
        authorization = "cli-authorization-must-not-leak"
        code, run, rendered, stderr = self._subprocess_fake_run(self._run_args(authorization))
        self.assertEqual(0, code)
        self.assertEqual("awaiting_human_confirmation", run["snapshot"]["status"])
        self.assertNotIn(authorization, rendered)
        self.assertEqual("", stderr)
        run_id = int(run["snapshot"]["run_id"])
        self.assertEqual(self.initial_head, self._git_text("rev-parse", "HEAD"))
        self.assertIn("a - b", (self.project / "calculator.py").read_text(encoding="utf-8"))

        control_before = {path.name: path.read_bytes() for path in self.root.glob("control.sqlite*")}
        code, status, _, stderr = self._subprocess(["status", *self._base(), "--run-id", str(run_id)])
        self.assertEqual(0, code)
        self.assertEqual("awaiting_human_confirmation", status["snapshot"]["status"])
        self.assertEqual("", stderr)
        self.assertEqual(control_before, {path.name: path.read_bytes() for path in self.root.glob("control.sqlite*")})

        database_alias = self.root / "control-hardlink.sqlite"
        os.link(self.db_path, database_alias)
        code, rejected, _, _ = self._invoke(["status", *self._base(), "--run-id", str(run_id)])
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_path_invalid", rejected["error_code"])
        database_alias.unlink()

        code, issued, issue_rendered, _ = self._subprocess([
            "issue-confirmation", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--requested-by", "local-user",
        ])
        self.assertEqual(0, code)
        token = str(issued["confirmation_token"])
        self.assertEqual(1, issue_rendered.count(token))

        code, confirmed, confirm_rendered, _ = self._subprocess([
            "confirm-apply", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--requested-by", "local-user",
        ], stdin=token + "\n")
        self.assertEqual(0, code)
        self.assertEqual("locally_applied", confirmed["snapshot"]["status"])
        self.assertNotIn(token, confirm_rendered)
        self.assertEqual(self.initial_head, self._git_text("rev-parse", "HEAD"))
        self.assertIn("a + b", (self.project / "calculator.py").read_text(encoding="utf-8"))
        self.assertEqual("", self._git_text("remote"))
        self.assertEqual("1", self._git_text("rev-list", "--count", "HEAD"))
        subprocess.run([sys.executable, "-m", "unittest", "-q", "test_calculator"], cwd=self.project, check=True, capture_output=True)

        code, repeated, repeated_rendered, stderr = self._subprocess([
            "confirm-apply", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--requested-by", "local-user",
        ], stdin=token + "\n")
        self.assertEqual(2, code)
        self.assertEqual("local_agent_confirmation_invalid", repeated["error_code"])
        self.assertNotIn(token, repeated_rendered)
        self.assertEqual("", stderr)

        code, extra, extra_rendered, stderr = self._subprocess([
            "confirm-apply", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--requested-by", "local-user",
        ], stdin="not-a-valid-token\nsecond-line\n")
        self.assertEqual(2, code)
        self.assertEqual("local_agent_confirmation_invalid", extra["error_code"])
        self.assertNotIn("not-a-valid-token", extra_rendered + stderr)

    def test_record_correction_is_hash_only_idempotent_and_invalidates_issued_confirmation(self) -> None:
        success = {
            "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
        }
        with (
            patch.object(cli, "_build_local_agent_runner", side_effect=self._runner_factory()),
            patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=success),
        ):
            code, run, _, _ = self._invoke(self._run_args("correction-awaiting-authorization"))
        self.assertEqual(0, code)
        run_id = int(run["snapshot"]["run_id"])
        code, issued, _, _ = self._invoke([
            "issue-confirmation", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--requested-by", "local-user",
        ])
        self.assertEqual(0, code)
        summary = self.root / "human-correction.txt"
        raw_summary = "manual reproduction exposed an implementation defect"
        summary.write_text(raw_summary, encoding="utf-8")
        summary.chmod(0o600)
        command = [
            "record-correction", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--root-cause-kind", "implementation_defect",
            "--summary-file", str(summary),
        ]

        code, corrected, rendered, stderr = self._invoke(command)
        self.assertEqual(0, code)
        self.assertTrue(corrected["ok"])
        self.assertEqual("changes_requested", corrected["snapshot"]["status"])
        self.assertNotIn(raw_summary, rendered)
        self.assertNotIn(str(summary), rendered)
        self.assertEqual("", stderr)
        snapshot = LocalAgentRunRepository(self.db_path).snapshot(run_id)
        self.assertEqual("changes_requested", snapshot["run"]["status"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(
                "expired",
                connection.execute(
                    "select status from local_agent_apply_confirmations where run_id=?", (run_id,),
                ).fetchone()[0],
            )
        self.assertEqual("confirmation_invalidated_for_correction", snapshot["events"][-1]["event_type"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            stored = connection.execute(
                "select safe_summary_json from repair_retrospectives where run_id=?", (run_id,),
            ).fetchone()[0]
        self.assertNotIn(raw_summary, stored)
        self.assertRegex(stored, r'"summary":"sha256:[0-9a-f]{64}"')

        code, replayed, replay_rendered, stderr = self._invoke(command)
        self.assertEqual(0, code)
        self.assertTrue(replayed["ok"])
        self.assertEqual("changes_requested", replayed["snapshot"]["status"])
        self.assertNotIn(raw_summary, replay_rendered)
        self.assertEqual("", stderr)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(1, connection.execute(
                "select count(*) from repair_retrospectives where run_id=?", (run_id,),
            ).fetchone()[0])
        replayed_snapshot = LocalAgentRunRepository(self.db_path).snapshot(run_id)
        self.assertEqual(
            1,
            len([item for item in replayed_snapshot["artifacts"] if item["kind"] == "repair_retrospective"]),
        )

    def test_record_correction_rejects_unsafe_input_or_worktree_mismatch_without_state_change(self) -> None:
        good = self.root / "good-summary.txt"
        good.write_text("ordinary correction", encoding="utf-8")
        good.chmod(0o600)
        # Establish the prohibited terminal status before the confirmation
        # fixture.  It is deliberately a separate run, so the later checks
        # still prove that rejected inputs leave an issued confirmation alone.
        with patch.object(cli, "_build_local_agent_runner", side_effect=self._runner_factory(fail=True)):
            code, failed, _, _ = self._invoke(self._run_args("correction-disallowed-status"))
        self.assertEqual(2, code)
        self.assertEqual("failed_worker", failed["snapshot"]["status"])
        failed_run_id = int(failed["snapshot"]["run_id"])
        code, rejected, _, _ = self._invoke([
            "record-correction", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(failed_run_id), "--root-cause-kind", "implementation_defect", "--summary-file", str(good),
        ])
        self.assertEqual(2, code)
        self.assertFalse(rejected["ok"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(0, connection.execute(
                "select count(*) from repair_retrospectives where run_id=?", (failed_run_id,),
            ).fetchone()[0])

        success = {
            "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
        }
        with (
            patch.object(cli, "_build_local_agent_runner", side_effect=self._runner_factory()),
            patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=success),
        ):
            code, run, _, _ = self._invoke([
                "retry", *self._base(), "--worktree-root", str(self.worktree_root),
                "--run-id", str(failed_run_id),
            ])
        self.assertEqual(0, code)
        run_id = int(run["snapshot"]["run_id"])
        code, _, _, _ = self._invoke([
            "issue-confirmation", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--requested-by", "local-user",
        ])
        self.assertEqual(0, code)
        unsafe = self.root / "unsafe-summary.txt"
        unsafe.write_text("first line\nsecond line", encoding="utf-8")
        unsafe.chmod(0o600)
        oversized = self.root / "oversized-summary.txt"
        oversized.write_bytes(b"x" * 4097)
        oversized.chmod(0o600)
        link = self.root / "summary-link.txt"
        link.symlink_to(good)
        before = LocalAgentRunRepository(self.db_path).snapshot(run_id)
        cases = (
            ("missing", [
                "record-correction", *self._base(), "--worktree-root", str(self.worktree_root),
                "--run-id", str(run_id), "--root-cause-kind", "implementation_defect",
            ]),
            ("invalid-kind", [
                "record-correction", *self._base(), "--worktree-root", str(self.worktree_root),
                "--run-id", str(run_id), "--root-cause-kind", "model_guess", "--summary-file", str(good),
            ]),
            ("multiline", [
                "record-correction", *self._base(), "--worktree-root", str(self.worktree_root),
                "--run-id", str(run_id), "--root-cause-kind", "implementation_defect", "--summary-file", str(unsafe),
            ]),
            ("symlink", [
                "record-correction", *self._base(), "--worktree-root", str(self.worktree_root),
                "--run-id", str(run_id), "--root-cause-kind", "implementation_defect", "--summary-file", str(link),
            ]),
            ("oversized", [
                "record-correction", *self._base(), "--worktree-root", str(self.worktree_root),
                "--run-id", str(run_id), "--root-cause-kind", "implementation_defect", "--summary-file", str(oversized),
            ]),
        )
        for name, command in cases:
            with self.subTest(name=name):
                code, rejected, rendered, stderr = self._invoke(command)
                self.assertEqual(2, code)
                self.assertFalse(rejected["ok"])
                self.assertEqual(1, len(rendered.splitlines()))
                self.assertNotIn(str(good), rendered + stderr)
                self.assertEqual("", stderr)
                after = LocalAgentRunRepository(self.db_path).snapshot(run_id)
                self.assertEqual(before["run"], after["run"])
                self.assertEqual(before["events"], after["events"])
                self.assertEqual(before.get("confirmation"), after.get("confirmation"))

        grows_after_open = self.root / "summary-grows-after-open.txt"
        grows_after_open.write_text("x", encoding="utf-8")
        grows_after_open.chmod(0o600)
        original_open_summary = cli._open_correction_summary

        growth_fd = os.open(grows_after_open, os.O_WRONLY)
        try:
            @contextlib.contextmanager
            def open_then_grow(path):
                with original_open_summary(path) as anchored:
                    os.pwrite(growth_fd, b"x" * 4097, 0)
                    os.ftruncate(growth_fd, 4097)
                    yield anchored

            with patch.object(cli, "_open_correction_summary", side_effect=open_then_grow):
                code, rejected, rendered, stderr = self._invoke([
                    "record-correction", *self._base(), "--worktree-root", str(self.worktree_root),
                    "--run-id", str(run_id), "--root-cause-kind", "implementation_defect",
                    "--summary-file", str(grows_after_open),
                ])
        finally:
            os.close(growth_fd)
        self.assertEqual(2, code)
        self.assertFalse(rejected["ok"])
        self.assertEqual(1, len(rendered.splitlines()))
        self.assertEqual("", stderr)
        after = LocalAgentRunRepository(self.db_path).snapshot(run_id)
        self.assertEqual(before["run"], after["run"])
        self.assertEqual(before["events"], after["events"])
        self.assertEqual(before.get("confirmation"), after.get("confirmation"))
        self.assertEqual(before["artifacts"], after["artifacts"])

        other_root = Path(tempfile.mkdtemp(prefix="his_harness_stage_f_other_", dir="/private/tmp"))
        try:
            code, rejected, _, _ = self._invoke([
                "record-correction", *self._base(), "--worktree-root", str(other_root),
                "--run-id", str(run_id), "--root-cause-kind", "implementation_defect", "--summary-file", str(good),
            ])
            self.assertEqual(2, code)
            self.assertFalse(rejected["ok"])
            after = LocalAgentRunRepository(self.db_path).snapshot(run_id)
            self.assertEqual(before["run"], after["run"])
            self.assertEqual(before["events"], after["events"])
        finally:
            other_root.rmdir()

    def test_two_cli_status_processes_serialize_on_the_anchored_database(self) -> None:
        code, run, _, _ = self._subprocess_fake_run(self._run_args("concurrent-status-setup"))
        self.assertEqual(0, code)
        run_id = str(run["snapshot"]["run_id"])
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HARNESS_DB_PATH": str(self.db_path),
            "HIS_KNOWLEDGE_HOME": str(self.knowledge_home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        command = [
            sys.executable,
            str(cli.PROJECT_ROOT / "tools" / "task_manager.py"),
            "local-agent",
            "status",
            *self._base(),
            "--run-id",
            run_id,
        ]
        processes = [
            subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
            for _ in range(2)
        ]
        results = [process.communicate(timeout=20) for process in processes]
        self.assertEqual([0, 0], [process.returncode for process in processes])
        self.assertTrue(all(json.loads(stdout)["snapshot"]["run_id"] == int(run_id) for stdout, _ in results))
        self.assertTrue(all(stderr == "" for _, stderr in results))
        code, status, _, _ = self._subprocess(["status", *self._base(), "--run-id", run_id])
        self.assertEqual(0, code)
        self.assertEqual(int(run_id), status["snapshot"]["run_id"])

    def test_database_hardlink_alias_to_protected_like_database_is_rejected_before_open(self) -> None:
        protected = self.root / "formal-like.sqlite"
        protected.write_bytes(b"formal database sentinel")
        protected.chmod(0o600)
        original = protected.read_bytes()
        alias = self.root / "control-alias.sqlite"
        os.link(protected, alias)
        alias_args = self._run_args("hardlink-alias-authorization")
        alias_args[alias_args.index(str(self.db_path))] = str(alias)
        real_open = os.open
        with (
            patch.object(cli, "_protected_database_paths", return_value=(protected,)),
            patch.object(cli.os, "open", side_effect=real_open) as guarded_open,
            patch.object(database, "init_db", side_effect=AssertionError("database must not open")),
            patch.object(cli, "_build_local_agent_runner", side_effect=AssertionError("worker must not start")),
        ):
            code, rejected, _, stderr = self._invoke(alias_args)
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_path_invalid", rejected["error_code"])
        self.assertEqual(original, protected.read_bytes())
        self.assertEqual(2, protected.stat().st_nlink)
        self.assertFalse(any(call.args and call.args[0] == alias.name for call in guarded_open.call_args_list))
        self.assertFalse((protected.parent / (protected.name + "-wal")).exists())
        self.assertEqual("", stderr)

    def test_replacement_before_init_never_opens_or_mutates_protected_sqlite(self) -> None:
        protected = self.root / "protected-init.sqlite"
        protected_before = self._create_protected_sqlite(protected)
        real_connect = database.sqlite3.connect
        real_init = database.init_db

        def replace_control_then_initialize(*arguments, **keywords) -> None:
            self.db_path.unlink()
            os.link(protected, self.db_path)
            real_init(*arguments, **keywords)

        with (
            patch.object(cli, "_protected_database_paths", return_value=(protected,)),
            patch.object(database, "init_db", side_effect=replace_control_then_initialize),
            patch.object(database.sqlite3, "connect", side_effect=real_connect) as sqlite_open,
            patch.object(cli, "_build_local_agent_runner", side_effect=AssertionError("worker must not start")),
        ):
            code, rejected, _, stderr = self._invoke(self._run_args("replace-before-init"))
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_path_invalid", rejected["error_code"])
        self.assertEqual(protected_before, self._sqlite_family(protected))
        self.assertFalse(any(call.args and call.args[0] != ":memory:" for call in sqlite_open.call_args_list))
        self.assertEqual("", stderr)

    def test_replacement_before_status_never_path_opens_protected_sqlite(self) -> None:
        with patch.object(cli, "_build_local_agent_runner", side_effect=self._runner_factory()):
            code, run, _, _ = self._invoke(self._run_args("replace-before-status-setup"))
        self.assertEqual(0, code)
        run_id = int(run["snapshot"]["run_id"])
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.db_path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        protected = self.root / "protected-status.sqlite"
        protected_before = self._create_protected_sqlite(protected)
        real_repository = repository_module.LocalAgentRunRepository
        real_connect = database.sqlite3.connect

        def replace_control_then_construct(*arguments, **keywords):
            self.db_path.unlink()
            os.link(protected, self.db_path)
            return real_repository(*arguments, **keywords)

        with (
            patch.object(cli, "_protected_database_paths", return_value=(protected,)),
            patch.object(repository_module, "LocalAgentRunRepository", side_effect=replace_control_then_construct),
            patch.object(database.sqlite3, "connect", side_effect=real_connect) as sqlite_open,
        ):
            code, rejected, _, stderr = self._invoke(["status", *self._base(), "--run-id", str(run_id)])
        self.assertEqual(2, code)
        self.assertIn(rejected["error_code"], {"local_agent_cli_path_invalid", "local_agent_storage_invalid"})
        self.assertEqual(protected_before, self._sqlite_family(protected))
        self.assertFalse(any(call.args and call.args[0] != ":memory:" for call in sqlite_open.call_args_list))
        self.assertEqual("", stderr)

    def test_anchored_persist_crash_and_leaf_replacement_preserve_previous_or_unknown_database(self) -> None:
        with cli._open_control_database(str(self.db_path), create=True) as control:
            with control.connect() as connection:
                connection.execute("create table durable(value text not null)")
            durable_before = self.db_path.read_bytes()

            with patch.object(cli.os, "rename", side_effect=OSError("simulated crash")):
                with self.assertRaises(OSError):
                    with control.connect() as connection:
                        connection.execute("insert into durable values('not-persisted')")
            self.assertEqual(durable_before, self.db_path.read_bytes())
            with closing(sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)) as check:
                self.assertEqual(0, check.execute("select count(*) from durable").fetchone()[0])

            protected = self.root / "protected-during-persist.sqlite"
            protected_before = self._create_protected_sqlite(protected)
            with self.assertRaises(ValueError):
                with control.connect() as connection:
                    connection.execute("insert into durable values('must-not-land')")
                    self.db_path.unlink()
                    os.link(protected, self.db_path)
            self.assertEqual(protected_before, self._sqlite_family(protected))
            self.assertTrue(os.path.samefile(protected, self.db_path))

    def test_parent_replacement_is_rejected_without_opening_replacement_database(self) -> None:
        private_parent = self.root / "private-control"
        private_parent.mkdir(mode=0o700)
        anchored_path = private_parent / "control.sqlite"
        moved_parent = self.root / "moved-private-control"
        with cli._open_control_database(str(anchored_path), create=True) as control:
            with control.connect() as connection:
                connection.execute("create table durable(value text not null)")
            private_parent.rename(moved_parent)
            private_parent.mkdir(mode=0o700)
            replacement = private_parent / "control.sqlite"
            replacement_before = self._create_protected_sqlite(replacement)
            real_connect = database.sqlite3.connect
            with patch.object(database.sqlite3, "connect", side_effect=real_connect) as sqlite_open:
                with self.assertRaises(ValueError):
                    control.connect()
            self.assertEqual(replacement_before, self._sqlite_family(replacement))
            self.assertEqual([], sqlite_open.call_args_list)

    def test_contract_path_aliases_and_replacement_are_rejected_before_database_or_worker(self) -> None:
        cases: list[tuple[str, Path]] = []
        relative = Path(os.path.relpath(self.contract, Path.cwd()))
        cases.append(("relative", relative))
        symlink = self.root / "task-link.json"
        symlink.symlink_to(self.contract)
        cases.append(("leaf-symlink", symlink))
        ancestor = self.root / "contract-parent-link"
        ancestor.symlink_to(self.contract.parent, target_is_directory=True)
        cases.append(("ancestor-symlink", ancestor / self.contract.name))
        hardlink = self.root / "task-hardlink.json"
        os.link(self.contract, hardlink)
        cases.append(("hardlink", hardlink))
        for name, contract_path in cases:
            with self.subTest(name=name):
                arguments = self._run_args(f"contract-{name}-authorization")
                arguments[arguments.index(str(self.contract))] = str(contract_path)
                with patch.object(cli, "_build_local_agent_runner", side_effect=AssertionError("worker must not start")):
                    code, rejected, _, _ = self._invoke(arguments)
                self.assertEqual(2, code)
                self.assertEqual("local_agent_cli_contract_invalid", rejected["error_code"])
                self.assertFalse(self.db_path.exists())

        hardlink.unlink()
        original_validate = cli._validate_cli_source_clean

        def replace_contract(task) -> None:
            original_validate(task)
            replacement = self.root / "replacement.json"
            replacement.write_bytes(self.contract.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, self.contract)

        with patch.object(cli, "_validate_cli_source_clean", side_effect=replace_contract):
            code, rejected, _, _ = self._invoke(self._run_args("contract-replacement-authorization"))
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_contract_invalid", rejected["error_code"])
        self.assertFalse(self.db_path.exists())

    def test_unstaged_source_dirtiness_stops_before_run(self) -> None:
        (self.project / "calculator.py").write_text("def add(a, b):\n    return 99\n", encoding="utf-8")
        self._assert_source_dirty_rejected()

    def test_staged_source_dirtiness_stops_before_run(self) -> None:
        (self.project / "calculator.py").write_text("def add(a, b):\n    return 99\n", encoding="utf-8")
        self._git("add", "calculator.py")
        self._assert_source_dirty_rejected()

    def test_untracked_source_dirtiness_stops_before_run(self) -> None:
        (self.project / "untracked.txt").write_text("user data\n", encoding="utf-8")
        self._assert_source_dirty_rejected()

    def _assert_source_dirty_rejected(self) -> None:
        with (
            patch.object(runtime_policy, "assert_local_agent_run_allowed", side_effect=AssertionError("preflight must not start")),
            patch.object(cli, "_build_local_agent_runner", side_effect=AssertionError("worker must not start")),
        ):
            code, rejected, _, stderr = self._invoke(self._run_args("dirty-source-authorization"))
        self.assertEqual(2, code)
        self.assertEqual("local_agent_source_not_clean", rejected["error_code"])
        self.assertFalse(self.db_path.exists())
        self.assertFalse((self.worktree_root / "run_1").exists())
        self.assertEqual("", stderr)

    def test_retry_eligibility_unknown_run_tamper_and_secret_errors_fail_closed(self) -> None:
        with patch.object(cli, "_build_local_agent_runner", side_effect=self._runner_factory(fail=True)):
            code, failed, _, _ = self._invoke(self._run_args("retryable-authorization"))
        self.assertEqual(2, code)
        self.assertEqual("failed_worker", failed["snapshot"]["status"])
        run_id = int(failed["snapshot"]["run_id"])

        with patch.object(cli, "_build_local_agent_runner", side_effect=self._runner_factory()):
            code, retried, _, _ = self._invoke([
                "retry", *self._base(), "--worktree-root", str(self.worktree_root), "--run-id", str(run_id)
            ])
        self.assertEqual(0, code)
        self.assertEqual("awaiting_human_confirmation", retried["snapshot"]["status"])

        code, ineligible, _, _ = self._invoke([
            "retry", *self._base(), "--worktree-root", str(self.worktree_root), "--run-id", str(run_id)
        ])
        self.assertEqual(2, code)
        self.assertEqual("local_agent_retry_invalid", ineligible["error_code"])

        code, unknown, _, _ = self._invoke(["status", *self._base(), "--run-id", "999999"])
        self.assertEqual(2, code)
        self.assertEqual("local_agent_storage_invalid", unknown["error_code"])

        snapshot = LocalAgentRunRepository(self.db_path).snapshot(run_id)
        final_patch = next(item for item in snapshot["artifacts"] if item["kind"] == "final_patch")
        final_patch_path = self.worktree_root / str(final_patch["relative_path"])
        final_patch_path.chmod(0o600)
        final_patch_path.write_bytes(b"tampered\n")
        code, tampered, _, _ = self._invoke([
            "issue-confirmation", *self._base(), "--worktree-root", str(self.worktree_root),
            "--run-id", str(run_id), "--requested-by", "local-user",
        ])
        self.assertEqual(2, code)
        self.assertEqual("local_agent_confirmation_invalid", tampered["error_code"])

        secret = "Bearer " + "s" * 48
        with patch.object(cli, "_build_local_agent_runner", side_effect=RuntimeError(secret)):
            code, rejected, rendered, stderr = self._invoke(self._run_args("internal-error-authorization"))
        self.assertEqual(2, code)
        self.assertEqual("local_agent_cli_failed", rejected["error_code"])
        self.assertNotIn(secret, rendered + stderr)

        bad = json.loads(self.contract.read_text(encoding="utf-8"))
        bad["request"] = secret
        self.contract.write_text(json.dumps(bad), encoding="utf-8")
        code, rejected, rendered, stderr = self._invoke(self._run_args("secret-contract-authorization"))
        self.assertEqual(2, code)
        self.assertEqual("local_agent_contract_invalid", rejected["error_code"])
        self.assertNotIn(secret, rendered + stderr)


if __name__ == "__main__":
    unittest.main()
