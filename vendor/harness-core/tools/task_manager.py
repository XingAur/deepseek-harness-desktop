from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.harness_config import (
    build_config_summary,
    build_configuration_import_draft,
    build_configuration_import_review,
    build_configuration_preview,
    build_configuration_share_validation,
    build_configuration_template_index,
    build_configuration_wizard,
    write_configuration_import_draft_outputs,
)
from app.dynamic_plan_registry import DynamicPlanRegistry, write_dynamic_registry_outputs
from app.dynamic_scheduler import DynamicDryRunScheduler, write_dynamic_schedule_outputs
from app.node_runtime import ControlledNodeRuntime, write_node_runtime_outputs
from app.executor_runtime import SandboxExecutorRuntime, write_executor_runtime_outputs
from app.mock_agent_runtime import (
    DeterministicMockAgentRuntime,
    write_mock_agent_runtime_outputs,
)
from app.model_invocation_runtime import (
    OfflineModelInvocationRuntime,
    write_model_invocation_outputs,
)
from app.model_dag_runtime import OfflineModelDagRuntime, write_model_dag_outputs
from app.model_provider_runtime import (
    ControlledModelProviderRuntime,
    model_provider_smoke_exit_code,
    write_model_provider_smoke_outputs,
)
from app.task_manager import (
    DEFAULT_TASK_OUTPUT_ROOT,
    DEFAULT_TASK_WORKTREE_ROOT,
    TaskChangeRecordOptions,
    TaskCreateOptions,
    TaskDashboardFilters,
    TaskExistingRunOptions,
    TaskManualVerificationOptions,
    TaskManager,
    TaskPrecommitRerunOptions,
    TaskRollbackPlanOptions,
    TaskRollbackApplyOptions,
    TaskRunOptions,
    task_to_json,
    task_to_markdown,
)
from app.technical_decision import DEFAULT_PROJECT_ROOT


_LOCAL_AGENT_TEMP_ROOT = Path("/private/tmp")
_LOCAL_AGENT_SAFE_ERROR = re.compile(r"local_agent_[a-z0-9_]{1,96}\Z")
_LOCAL_AGENT_WORKTREE_ROOT = re.compile(r"^/private/tmp/his_harness_stage_f_[A-Za-z0-9_-]{1,96}$")
_LOCAL_AGENT_CONFIRMATION_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
_LOCAL_AGENT_CONTRACT_MAX_BYTES = 65_536
_LOCAL_AGENT_DATABASE_MAX_BYTES = 128 * 1024 * 1024
_LOCAL_AGENT_CORRECTION_SUMMARY_MAX_BYTES = 4 * 1024
_LOCAL_AGENT_CORRECTION_ROOT_CAUSES = frozenset({
    "verification_failure",
    "review_gap",
    "path_coverage_gap",
    "contract_mismatch",
    "implementation_defect",
})


class _LocalAgentArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("local_agent_cli_arguments_invalid")


def _local_agent_parser() -> argparse.ArgumentParser:
    parser = _LocalAgentArgumentParser(prog="task_manager.py local-agent", add_help=False)
    commands = parser.add_subparsers(dest="local_agent_command", required=True)

    run = commands.add_parser("run", add_help=False)
    _add_local_agent_database_argument(run)
    run.add_argument("--knowledge-home", required=True)
    run.add_argument("--contract", required=True)
    run.add_argument("--worktree-root", required=True)
    _add_local_agent_backend_argument(run)
    run.add_argument("--allow-real-agent", action="store_true")
    run.add_argument("--authorization-id", required=True)

    status = commands.add_parser("status", add_help=False)
    _add_local_agent_database_argument(status)
    status.add_argument("--run-id", type=int, required=True)

    retry = commands.add_parser("retry", add_help=False)
    _add_local_agent_database_argument(retry)
    retry.add_argument("--run-id", type=int, required=True)
    retry.add_argument("--worktree-root", required=True)
    _add_local_agent_backend_argument(retry)

    auto_repair = commands.add_parser("auto-repair", add_help=False)
    _add_local_agent_database_argument(auto_repair)
    auto_repair.add_argument("--run-id", type=int, required=True)
    auto_repair.add_argument("--worktree-root", required=True)
    auto_repair.add_argument("--max-rounds", type=int, required=True)
    _add_local_agent_backend_argument(auto_repair)

    issue = commands.add_parser("issue-confirmation", add_help=False)
    _add_local_agent_database_argument(issue)
    issue.add_argument("--run-id", type=int, required=True)
    issue.add_argument("--worktree-root", required=True)
    issue.add_argument("--requested-by", required=True)

    confirm = commands.add_parser("confirm-apply", add_help=False)
    _add_local_agent_database_argument(confirm)
    confirm.add_argument("--run-id", type=int, required=True)
    confirm.add_argument("--worktree-root", required=True)
    confirm.add_argument("--requested-by", required=True)

    correction = commands.add_parser("record-correction", add_help=False)
    _add_local_agent_database_argument(correction)
    correction.add_argument("--run-id", type=int, required=True)
    correction.add_argument("--worktree-root", required=True)
    correction.add_argument("--root-cause-kind", required=True)
    correction.add_argument("--summary-file", required=True)
    return parser


def _add_local_agent_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db-path", "--database",
        dest="db_path",
        default=os.environ.get("HARNESS_DB_PATH", ""),
    )


def _add_local_agent_backend_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent-backend",
        default=None,
        help="agent execution backend; defaults to HARNESS_AGENT_BACKEND or config/agent_backends.json",
    )


def local_agent_main(arguments: list[str] | None = None) -> int:
    """Run the explicit local-agent CLI and emit exactly one safe JSON object."""
    from app import database

    argv = list(sys.argv[2:] if arguments is None else arguments)
    parser = _local_agent_parser()
    if any(item in {"-h", "--help"} for item in argv):
        _emit_local_agent_json({"ok": True, "command": "local-agent help", "help": parser.format_help()})
        return 0
    command = "local-agent"
    try:
        args = parser.parse_args(argv)
        command = f"local-agent {args.local_agent_command}"
        from app.local_agent_confirmation import LocalAgentConfirmationService
        from app.local_agent_contract import load_local_agent_task_bytes
        from app.local_agent_repository import LocalAgentRunRepository
        from app.runtime_policy import assert_local_agent_run_allowed

        if args.local_agent_command == "run":
            with _open_cli_contract(Path(args.contract)) as contract:
                task = load_local_agent_task_bytes(contract.read_and_verify())
                _validate_cli_source_clean(task)
                contract.verify()
                preflight = assert_local_agent_run_allowed(
                    allow_real_agent=args.allow_real_agent,
                    authorization_id=args.authorization_id,
                )
                knowledge_home = _explicit_temporary_path(args.knowledge_home, kind="knowledge", require_existing=False, directory=True)
                worktree_root = _explicit_temporary_path(args.worktree_root, kind="worktree", require_existing=False, directory=True)
                os.environ["HIS_KNOWLEDGE_HOME"] = str(knowledge_home)
                with _open_control_database(args.db_path, create=True) as control:
                    database.DB_PATH = control.path
                    os.environ["HARNESS_DB_PATH"] = str(control.path)
                    database.init_db(connection_factory=control.connect)
                    control.verify()
                    contract.verify()
                    _validate_cli_source_clean(task)
                    repository = LocalAgentRunRepository(
                        control.path,
                        connection_factory=control.connect,
                    )
                    snapshot = _build_local_agent_runner_for_args(
                        repository, worktree_root, args.agent_backend
                    ).execute(task, preflight)
                    control.verify()
                    contract.verify()
                    return _emit_local_agent_snapshot(command, snapshot)

        with _open_control_database(args.db_path, create=False) as control:
            database.DB_PATH = control.path
            os.environ["HARNESS_DB_PATH"] = str(control.path)
            connection_factory = (
                control.connect_read_only
                if args.local_agent_command == "status"
                else control.connect
            )
            repository = LocalAgentRunRepository(
                control.path,
                connection_factory=connection_factory,
            )
            if args.local_agent_command == "status":
                snapshot = repository.snapshot(args.run_id)
                control.verify()
                _emit_local_agent_json({"ok": True, "command": command, "snapshot": _safe_local_agent_snapshot(snapshot)})
                return 0

            worktree_root = _explicit_temporary_path(args.worktree_root, kind="worktree", require_existing=True, directory=True)
            if args.local_agent_command == "retry":
                snapshot = _build_local_agent_runner_for_args(
                    repository, worktree_root, args.agent_backend
                ).retry(args.run_id)
                control.verify()
                return _emit_local_agent_snapshot(command, snapshot)

            if args.local_agent_command == "auto-repair":
                snapshot = _build_local_agent_runner_for_args(
                    repository, worktree_root, args.agent_backend
                ).auto_repair(
                    args.run_id,
                    max_rounds=args.max_rounds,
                )
                control.verify()
                return _emit_local_agent_snapshot(command, snapshot)

            if args.local_agent_command == "record-correction":
                summary_sha256 = _read_correction_summary_sha256(args.summary_file)
                if args.root_cause_kind not in _LOCAL_AGENT_CORRECTION_ROOT_CAUSES:
                    raise ValueError("local_agent_correction_invalid")
                snapshot = _build_local_agent_runner(repository, worktree_root).record_human_correction(
                    args.run_id,
                    root_cause_kind=args.root_cause_kind,
                    summary_sha256=summary_sha256,
                )
                control.verify()
                _emit_local_agent_json({
                    "ok": True,
                    "command": command,
                    "snapshot": _safe_local_agent_snapshot(snapshot),
                })
                return 0

            service = LocalAgentConfirmationService(repository=repository, artifact_root=worktree_root)
            if args.local_agent_command == "issue-confirmation":
                confirmation = service.issue_local_apply_confirmation(args.run_id, args.requested_by)
                control.verify()
                _emit_local_agent_json({
                    "ok": True,
                    "command": command,
                    "run_id": confirmation.run_id,
                    "status": "issued",
                    "expires_at": confirmation.expires_at,
                    "confirmation_token": confirmation.token,
                })
                return 0

            token = _read_confirmation_token()
            result = service.confirm_and_apply(args.run_id, token, args.requested_by)
            snapshot = repository.snapshot(args.run_id)
            control.verify()
            code = 0 if result.get("status") == "locally_applied" else 2
            _emit_local_agent_json({
                "ok": code == 0,
                "command": command,
                "snapshot": _safe_local_agent_snapshot(snapshot),
                "apply_status": result.get("status"),
            })
            return code
    except Exception as error:
        _emit_local_agent_json({"ok": False, "command": command, "error_code": _safe_local_agent_error(error)})
        return 2


def _build_local_agent_runner(repository, worktree_root: Path, *, backend_id: str | None = None):
    from app.local_agent_runner import LocalAgentRunner

    return LocalAgentRunner(
        repository=repository,
        worktree_root=worktree_root,
        backend_id=backend_id,
    )


def _build_local_agent_runner_for_args(repository, worktree_root: Path, backend_id: str | None):
    """Keep legacy in-process CLI test/integration factories source-compatible."""
    if backend_id is None:
        return _build_local_agent_runner(repository, worktree_root)
    return _build_local_agent_runner(repository, worktree_root, backend_id=backend_id)


class _AnchoredFile:
    def __init__(self, *, path: Path, parent_fd: int, fd: int, error_code: str, content_hash: str = "") -> None:
        self.path = path
        self.parent_fd = parent_fd
        self.fd = fd
        self.error_code = error_code
        self.content_hash = content_hash
        self.identity = _file_identity(os.fstat(fd), error_code)
        self.parent_identity = _directory_identity(os.fstat(parent_fd), error_code)

    def verify(self) -> None:
        try:
            parent = _directory_identity(
                os.stat(self.path.parent, follow_symlinks=False),
                self.error_code,
            )
            held = _file_identity(os.fstat(self.fd), self.error_code)
            named = _file_identity(os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False), self.error_code)
        except (OSError, ValueError):
            raise ValueError(self.error_code) from None
        if parent != self.parent_identity or held != self.identity or named != self.identity:
            raise ValueError(self.error_code)
        if self.content_hash:
            if hashlib.sha256(
                _pread_bounded(
                    self.fd,
                    _LOCAL_AGENT_CONTRACT_MAX_BYTES,
                    self.error_code,
                )
            ).hexdigest() != self.content_hash:
                raise ValueError(self.error_code)

    def read_and_verify(self) -> bytes:
        raw = _pread_bounded(self.fd, _LOCAL_AGENT_CONTRACT_MAX_BYTES, self.error_code)
        self.content_hash = hashlib.sha256(raw).hexdigest()
        self.verify()
        return raw

    def close(self) -> None:
        os.close(self.fd)
        os.close(self.parent_fd)


class _AnchoredSQLiteConnection(sqlite3.Connection):
    def bind(self, capability: "_AnchoredSQLiteFile", read_only: bool) -> None:
        self._anchored_capability = capability
        self._anchored_read_only = read_only

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            suppressed = bool(super().__exit__(exc_type, exc_value, traceback))
            if exc_type is None and not self._anchored_read_only:
                self._anchored_capability.persist(self.serialize())
            return suppressed
        finally:
            self.close()


class _AnchoredSQLiteFile(_AnchoredFile):
    def verify(self) -> None:
        super().verify()
        if _is_protected_database_identity(self.identity[:2]):
            raise ValueError(self.error_code)

    def connect(self) -> sqlite3.Connection:
        return self._connect(read_only=False)

    def connect_read_only(self) -> sqlite3.Connection:
        return self._connect(read_only=True)

    def _connect(self, *, read_only: bool) -> sqlite3.Connection:
        self.verify()
        image = _pread_database_image(self.fd, self.error_code)
        connection = sqlite3.connect(
            ":memory:",
            timeout=5.0,
            factory=_AnchoredSQLiteConnection,
        )
        try:
            if image:
                connection.deserialize(image)
            connection.row_factory = sqlite3.Row
            connection.execute("pragma recursive_triggers = on")
            connection.execute("pragma foreign_keys = on")
            connection.execute("pragma busy_timeout = 5000")
            if read_only:
                connection.execute("pragma query_only = on")
            connection.bind(self, read_only)
            self.verify()
            return connection
        except Exception:
            connection.close()
            raise

    def persist(self, image: bytes) -> None:
        if not isinstance(image, bytes) or not image or len(image) > _LOCAL_AGENT_DATABASE_MAX_BYTES:
            raise ValueError(self.error_code)
        self.verify()
        temporary_name = f".{self.path.name}.anchored-{secrets.token_hex(16)}.tmp"
        temporary_fd = -1
        try:
            temporary_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=self.parent_fd,
            )
            offset = 0
            while offset < len(image):
                written = os.write(temporary_fd, image[offset:])
                if written <= 0:
                    raise OSError("anchored database write failed")
                offset += written
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = -1
            self.verify()
            os.rename(
                temporary_name,
                self.path.name,
                src_dir_fd=self.parent_fd,
                dst_dir_fd=self.parent_fd,
            )
            os.fsync(self.parent_fd)
            replacement_fd = os.open(
                self.path.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=self.parent_fd,
            )
            replacement_identity = _file_identity(os.fstat(replacement_fd), self.error_code)
            named_identity = _file_identity(
                os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False),
                self.error_code,
            )
            if replacement_identity != named_identity or _is_protected_database_identity(replacement_identity[:2]):
                os.close(replacement_fd)
                raise ValueError(self.error_code)
            previous_fd = self.fd
            self.fd = replacement_fd
            self.identity = replacement_identity
            os.close(previous_fd)
        finally:
            if temporary_fd >= 0:
                os.close(temporary_fd)
            try:
                os.unlink(temporary_name, dir_fd=self.parent_fd)
            except FileNotFoundError:
                pass


@contextlib.contextmanager
def _open_cli_contract(path: Path):
    error = "local_agent_cli_contract_invalid"
    parent_fd = -1
    fd = -1
    try:
        parent_fd, canonical = _open_parent_no_follow(path, error)
        fd = os.open(canonical.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        item = os.fstat(fd)
        if (
            not stat.S_ISREG(item.st_mode)
            or item.st_uid != os.getuid()
            or item.st_nlink != 1
            or item.st_mode & 0o077
            or item.st_size <= 0
            or item.st_size > _LOCAL_AGENT_CONTRACT_MAX_BYTES
        ):
            raise ValueError(error)
        anchored = _AnchoredFile(path=canonical, parent_fd=parent_fd, fd=fd, error_code=error)
        parent_fd = fd = -1
    except (OSError, ValueError):
        raise ValueError(error) from None
    finally:
        if fd >= 0:
            os.close(fd)
        if parent_fd >= 0:
            os.close(parent_fd)
    try:
        yield anchored
    finally:
        if anchored is not None:
            anchored.close()


@contextlib.contextmanager
def _open_correction_summary(path: Path):
    """Anchor a private correction summary without retaining its contents."""

    error = "local_agent_correction_invalid"
    parent_fd = -1
    fd = -1
    anchored: _AnchoredFile | None = None
    try:
        parent_fd, canonical = _open_parent_no_follow(path, error)
        fd = os.open(canonical.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        item = os.fstat(fd)
        if (
            not stat.S_ISREG(item.st_mode)
            or item.st_uid != os.getuid()
            or item.st_nlink != 1
            or item.st_mode & 0o077
            or item.st_size <= 0
            or item.st_size > _LOCAL_AGENT_CORRECTION_SUMMARY_MAX_BYTES
        ):
            raise ValueError(error)
        anchored = _AnchoredFile(path=canonical, parent_fd=parent_fd, fd=fd, error_code=error)
        parent_fd = fd = -1
    except (OSError, ValueError):
        raise ValueError(error) from None
    finally:
        if fd >= 0:
            os.close(fd)
        if parent_fd >= 0:
            os.close(parent_fd)
    try:
        yield anchored
    finally:
        if anchored is not None:
            anchored.close()


def _read_correction_summary_sha256(raw_path: object) -> str:
    """Validate the caller-owned one-line file and return its one-way hash."""

    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("local_agent_correction_invalid")
    with _open_correction_summary(Path(raw_path)) as summary:
        raw = _pread_bounded(
            summary.fd,
            _LOCAL_AGENT_CORRECTION_SUMMARY_MAX_BYTES,
            summary.error_code,
        )
        summary.content_hash = hashlib.sha256(raw).hexdigest()
        summary.verify()
        try:
            decoded = raw.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise ValueError("local_agent_correction_invalid") from None
        if not decoded or "\n" in decoded or "\r" in decoded or "\x00" in decoded:
            raise ValueError("local_agent_correction_invalid")
        return "sha256:" + hashlib.sha256(raw).hexdigest()


@contextlib.contextmanager
def _open_control_database(raw: object, *, create: bool):
    error = "local_agent_cli_path_invalid"
    parent_fd = -1
    fd = -1
    try:
        if not isinstance(raw, str) or not raw:
            raise ValueError("local_agent_cli_arguments_invalid")
        parent_fd, path = _open_parent_no_follow(Path(raw), error)
        try:
            path.relative_to(_LOCAL_AGENT_TEMP_ROOT)
        except ValueError:
            raise ValueError("local_agent_cli_database_not_temporary") from None
        if path == _LOCAL_AGENT_TEMP_ROOT:
            raise ValueError(error)
        parent = os.fstat(parent_fd)
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError(error)
        fcntl.flock(parent_fd, fcntl.LOCK_EX)
        flags = os.O_RDWR | os.O_NOFOLLOW
        try:
            named_before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if not create:
                raise ValueError("local_agent_cli_storage_missing") from None
            fd = os.open(path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent_fd)
        else:
            identity_before = _file_identity(named_before, error)
            if _is_protected_database_identity(identity_before[:2]):
                raise ValueError(error)
            fd = os.open(path.name, flags, dir_fd=parent_fd)
            if _file_identity(os.fstat(fd), error) != identity_before:
                raise ValueError(error)
        item = os.fstat(fd)
        identity = _file_identity(item, error)
        if _is_protected_database_identity(identity[:2]):
            raise ValueError(error)
        anchored = _AnchoredSQLiteFile(path=path, parent_fd=parent_fd, fd=fd, error_code=error)
        parent_fd = fd = -1
    except ValueError:
        raise
    except OSError:
        raise ValueError(error) from None
    finally:
        if fd >= 0:
            os.close(fd)
        if parent_fd >= 0:
            os.close(parent_fd)
    try:
        yield anchored
    finally:
        if anchored is not None:
            anchored.close()


def _protected_database_paths() -> tuple[Path, ...]:
    return (
        PROJECT_ROOT / "data" / "harness.sqlite",
        Path("/Users/lym/WorkCode/ai/Harness/data/harness.sqlite"),
    )


def _is_protected_database_identity(identity: tuple[int, int]) -> bool:
    for protected in _protected_database_paths():
        try:
            protected_item = protected.lstat()
        except OSError:
            continue
        if stat.S_ISREG(protected_item.st_mode) and identity == (protected_item.st_dev, protected_item.st_ino):
            return True
    return False


def _open_parent_no_follow(path: Path, error_code: str) -> tuple[int, Path]:
    if not isinstance(path, Path) or not path.is_absolute() or "\x00" in os.fspath(path):
        raise ValueError(error_code)
    try:
        canonical = path.resolve(strict=False)
    except OSError:
        raise ValueError(error_code) from None
    if canonical != path or canonical.parent == canonical:
        raise ValueError(error_code)
    current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in canonical.parent.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, canonical
    except OSError:
        os.close(current_fd)
        raise ValueError(error_code) from None


def _file_identity(item: os.stat_result, error_code: str) -> tuple[int, int, int, int, int]:
    if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or item.st_nlink != 1 or item.st_mode & 0o077:
        raise ValueError(error_code)
    return (item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode), item.st_uid, item.st_nlink)


def _directory_identity(item: os.stat_result, error_code: str) -> tuple[int, int, int, int]:
    if not stat.S_ISDIR(item.st_mode) or item.st_uid != os.getuid() or item.st_mode & 0o077:
        raise ValueError(error_code)
    return (item.st_dev, item.st_ino, item.st_uid, stat.S_IMODE(item.st_mode))


def _pread_bounded(fd: int, maximum: int, error_code: str) -> bytes:
    try:
        item = os.fstat(fd)
        if item.st_size <= 0 or item.st_size > maximum:
            raise ValueError(error_code)
        raw = os.pread(fd, maximum + 1, 0)
    except OSError:
        raise ValueError(error_code) from None
    if len(raw) != item.st_size or len(raw) > maximum:
        raise ValueError(error_code)
    return raw


def _pread_database_image(fd: int, error_code: str) -> bytes:
    try:
        item = os.fstat(fd)
        if item.st_size < 0 or item.st_size > _LOCAL_AGENT_DATABASE_MAX_BYTES:
            raise ValueError(error_code)
        chunks: list[bytes] = []
        offset = 0
        while offset < item.st_size:
            chunk = os.pread(fd, min(1 << 20, item.st_size - offset), offset)
            if not chunk:
                raise ValueError(error_code)
            chunks.append(chunk)
            offset += len(chunk)
        return b"".join(chunks)
    except OSError:
        raise ValueError(error_code) from None


def _validate_cli_source_clean(task) -> None:
    from app.worktree_executor import SafeGitBoundary

    try:
        boundary = SafeGitBoundary(task.project_path)
        head = boundary.text(["rev-parse", "--verify", "HEAD"], cwd=task.project_path).strip()
        status_result = boundary.run(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=task.project_path,
        )
        if head != task.initial_head or status_result["returncode"] != 0 or bytes(status_result["stdout"]):
            raise ValueError("local_agent_source_not_clean")
    except ValueError as error:
        if str(error) == "local_agent_source_not_clean":
            raise
        raise ValueError("local_agent_source_not_clean") from None


def _explicit_temporary_path(
    raw: object,
    *,
    kind: str,
    require_existing: bool,
    directory: bool = False,
) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("local_agent_cli_arguments_invalid")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError("local_agent_cli_path_invalid")
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(_LOCAL_AGENT_TEMP_ROOT)
    except (OSError, ValueError):
        code = "local_agent_cli_database_not_temporary" if kind == "database" else "local_agent_cli_path_not_temporary"
        raise ValueError(code) from None
    if candidate != resolved or (kind == "worktree" and _LOCAL_AGENT_WORKTREE_ROOT.fullmatch(resolved.as_posix()) is None):
        raise ValueError("local_agent_cli_path_invalid")
    if resolved == _LOCAL_AGENT_TEMP_ROOT:
        raise ValueError("local_agent_cli_path_invalid")
    _require_private_cli_directory(resolved.parent if kind == "database" else resolved, allow_missing=directory)
    if candidate.exists() or candidate.is_symlink():
        item = candidate.lstat()
        if stat.S_ISLNK(item.st_mode):
            raise ValueError("local_agent_cli_path_invalid")
        expected = stat.S_ISDIR(item.st_mode) if directory else stat.S_ISREG(item.st_mode)
        if not expected:
            raise ValueError("local_agent_cli_path_invalid")
    elif require_existing:
        raise ValueError("local_agent_cli_storage_missing")
    if directory and not resolved.exists():
        resolved.mkdir(mode=0o700, parents=True)
    return resolved


def _require_private_cli_directory(path: Path, *, allow_missing: bool) -> None:
    target = path
    if not target.exists():
        if not allow_missing:
            raise ValueError("local_agent_cli_path_invalid")
        target = target.parent
    try:
        item = target.lstat()
    except OSError:
        raise ValueError("local_agent_cli_path_invalid") from None
    if (
        stat.S_ISLNK(item.st_mode)
        or not stat.S_ISDIR(item.st_mode)
        or item.st_uid != os.getuid()
        or item.st_mode & 0o077
    ):
        raise ValueError("local_agent_cli_path_invalid")


def _safe_local_agent_snapshot(snapshot: object) -> dict[str, object]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("run"), dict):
        raise ValueError("local_agent_storage_invalid")
    run = snapshot["run"]
    attempts = snapshot.get("attempts")
    events = snapshot.get("events")
    artifacts = snapshot.get("artifacts")
    if not isinstance(attempts, list) or not isinstance(events, list) or not isinstance(artifacts, list):
        raise ValueError("local_agent_storage_invalid")
    return {
        "run_id": run["id"],
        "task_key": run["task_key"],
        "status": run["status"],
        "contract_hash": run["contract_hash"],
        "initial_head": run["initial_head"],
        "attempts": [
            {
                "attempt_no": item["attempt_no"],
                "status": item["status"],
                "error_code": item["error_code"],
            }
            for item in attempts
        ],
        "event_types": [item["event_type"] for item in events],
        "artifacts": [
            {
                "kind": item["kind"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in artifacts
        ],
        "apply_operation_status": None if snapshot.get("apply_operation") is None else snapshot["apply_operation"]["status"],
        "external_writes": False,
        "database_business_writes": False,
        "git_commit_push": False,
    }


def _emit_local_agent_snapshot(command: str, snapshot: dict[str, object]) -> int:
    safe = _safe_local_agent_snapshot(snapshot)
    ok = safe["status"] in {"awaiting_human_confirmation", "locally_applied"}
    _emit_local_agent_json({"ok": ok, "command": command, "snapshot": safe})
    return 0 if ok else 2


def _read_confirmation_token() -> str:
    try:
        if sys.stdin.isatty():
            token = getpass.getpass("Local apply confirmation token: ")
        else:
            raw = sys.stdin.read(258)
            if len(raw) > 257 or raw.count("\n") != 1 or not raw.endswith("\n"):
                raise ValueError("local_agent_confirmation_invalid")
            token = raw[:-1]
    except (EOFError, OSError, ValueError):
        raise ValueError("local_agent_confirmation_invalid")
    if _LOCAL_AGENT_CONFIRMATION_TOKEN.fullmatch(token) is None:
        raise ValueError("local_agent_confirmation_invalid")
    return token


def _safe_local_agent_error(error: BaseException) -> str:
    code = getattr(error, "code", "")
    if isinstance(code, str) and _LOCAL_AGENT_SAFE_ERROR.fullmatch(code):
        return code
    value = str(error)
    return value if _LOCAL_AGENT_SAFE_ERROR.fullmatch(value) else "local_agent_cli_failed"


def _emit_local_agent_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "local-agent":
        code = local_agent_main(sys.argv[2:])
        if code:
            raise SystemExit(code)
        return
    parser = argparse.ArgumentParser(description="HIS Harness v0.58 Task Manager.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create or update a Harness task")
    add_task_identity_args(create)
    create.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    create.add_argument("--project-path", action="append", default=[])
    create.add_argument("--base-branch", default="")
    create.add_argument("--work-branch", default="")
    create.add_argument("--notes", default="")
    create.add_argument("--json", action="store_true", help="print JSON instead of Markdown")

    list_cmd = subparsers.add_parser("list", help="list Harness tasks")
    list_cmd.add_argument("--limit", type=int, default=30)
    list_cmd.add_argument("--json", action="store_true")

    dashboard_cmd = subparsers.add_parser("dashboard", help="export readonly Task Manager dashboard")
    dashboard_cmd.add_argument("--limit", type=int, default=50)
    dashboard_cmd.add_argument("--output-dir", default="/tmp/his_harness_task_dashboard")
    dashboard_cmd.add_argument("--entity-id", default="", help="filter by exact DFHIS work item id")
    dashboard_cmd.add_argument("--task-key", default="", help="filter by exact task key")
    dashboard_cmd.add_argument("--entity-kind", choices=["bug", "requirement", "task"], default="", help="filter by task entity kind")
    dashboard_cmd.add_argument("--status", default="", help="filter by exact task status")
    dashboard_cmd.add_argument("--verification-status", default="", help="filter by exact verification status")
    dashboard_cmd.add_argument("--ui-evidence-status", default="", help="filter by exact UI evidence status")
    dashboard_cmd.add_argument("--can-commit", choices=["yes", "no", "true", "false", "1", "0"], default="", help="filter by can_commit")
    dashboard_cmd.add_argument("--sample-only", action="store_true", help="only include registered real sample outputs")
    dashboard_cmd.add_argument("--json", action="store_true")

    show = subparsers.add_parser("show", help="show one Harness task")
    add_task_lookup_args(show)
    show.add_argument("--json", action="store_true")

    register_dynamic_plan = subparsers.add_parser(
        "register-dynamic-plan",
        help="register a v0.49 readonly dynamic plan without executing its DAG",
    )
    register_dynamic_plan.add_argument("--plan-file", required=True, help="v0.49 dynamic_plan.json path")
    register_dynamic_plan.add_argument("--task-key", default="", help="optional existing/new Task Manager key")
    register_dynamic_plan.add_argument("--output-dir", default="/tmp/his_harness_dynamic_plan_registry")
    register_dynamic_plan.add_argument("--json", action="store_true")

    show_dynamic_plan = subparsers.add_parser(
        "show-dynamic-plan",
        help="show a registered dynamic plan and readonly recovery preview",
    )
    show_dynamic_plan.add_argument("--plan-id", type=int, required=True)
    show_dynamic_plan.add_argument("--output-dir", default="/tmp/his_harness_dynamic_plan_registry")
    show_dynamic_plan.add_argument("--json", action="store_true")

    record_dynamic_contract = subparsers.add_parser(
        "record-dynamic-contract",
        help="record a validated contract version and mark reachable downstream nodes stale",
    )
    record_dynamic_contract.add_argument("--plan-id", type=int, required=True)
    record_dynamic_contract.add_argument("--node-id", required=True)
    record_dynamic_contract.add_argument("--contract-file", required=True, help="contract JSON object")
    record_dynamic_contract.add_argument("--output-dir", default="/tmp/his_harness_dynamic_plan_registry")
    record_dynamic_contract.add_argument("--json", action="store_true")

    start_dynamic_schedule = subparsers.add_parser(
        "start-dynamic-schedule",
        help="start a persistent dry-run schedule without executing DAG nodes",
    )
    start_dynamic_schedule.add_argument("--plan-id", type=int, required=True)
    start_dynamic_schedule.add_argument("--output-dir", default="/tmp/his_harness_dynamic_schedule")
    start_dynamic_schedule.add_argument("--json", action="store_true")

    advance_dynamic_schedule = subparsers.add_parser(
        "advance-dynamic-schedule",
        help="apply one simulated event or retry tick to a dry-run schedule",
    )
    advance_dynamic_schedule.add_argument("--schedule-id", type=int, required=True)
    advance_dynamic_schedule.add_argument("--event-file", default="", help="optional simulated outcome JSON")
    advance_dynamic_schedule.add_argument("--output-dir", default="/tmp/his_harness_dynamic_schedule")
    advance_dynamic_schedule.add_argument("--json", action="store_true")

    show_dynamic_schedule = subparsers.add_parser(
        "show-dynamic-schedule",
        help="show a dry-run schedule and its verified checkpoint",
    )
    show_dynamic_schedule.add_argument("--schedule-id", type=int, required=True)
    show_dynamic_schedule.add_argument("--output-dir", default="/tmp/his_harness_dynamic_schedule")
    show_dynamic_schedule.add_argument("--json", action="store_true")

    prepare_dynamic_node_context = subparsers.add_parser(
        "prepare-dynamic-node-context",
        help="prepare an immutable fixture-only context for one simulated node",
    )
    prepare_dynamic_node_context.add_argument("--schedule-id", type=int, required=True)
    prepare_dynamic_node_context.add_argument("--node-id", required=True)
    prepare_dynamic_node_context.add_argument("--requested-tool", action="append", default=[])
    prepare_dynamic_node_context.add_argument("--output-dir", default="/tmp/his_harness_node_runtime")
    prepare_dynamic_node_context.add_argument("--json", action="store_true")

    execute_fixture_node = subparsers.add_parser(
        "execute-fixture-node",
        help="validate one local fixture JSON without running a real agent or tool",
    )
    execute_fixture_node.add_argument("--context-id", type=int, required=True)
    execute_fixture_node.add_argument("--fixture-root", required=True)
    execute_fixture_node.add_argument("--fixture-file", required=True)
    execute_fixture_node.add_argument("--output-dir", default="/tmp/his_harness_node_runtime")
    execute_fixture_node.add_argument("--json", action="store_true")

    show_fixture_node_execution = subparsers.add_parser(
        "show-fixture-node-execution",
        help="show one fixture-only node execution and candidate contract",
    )
    show_fixture_node_execution.add_argument("--execution-id", type=int, required=True)
    show_fixture_node_execution.add_argument("--output-dir", default="/tmp/his_harness_node_runtime")
    show_fixture_node_execution.add_argument("--json", action="store_true")

    issue_fixture_capability_lease = subparsers.add_parser(
        "issue-fixture-capability-lease",
        help="issue one short-lived single-use lease for the fixed fixture worker",
    )
    issue_fixture_capability_lease.add_argument("--context-id", type=int, required=True)
    issue_fixture_capability_lease.add_argument("--capability", action="append", default=[])
    issue_fixture_capability_lease.add_argument("--ttl-seconds", type=int, default=60)
    issue_fixture_capability_lease.add_argument("--output-dir", default="/tmp/his_harness_executor_runtime")
    issue_fixture_capability_lease.add_argument("--json", action="store_true")

    show_fixture_capability_lease = subparsers.add_parser(
        "show-fixture-capability-lease",
        help="show one fixture-only capability lease",
    )
    show_fixture_capability_lease.add_argument("--lease-id", type=int, required=True)
    show_fixture_capability_lease.add_argument("--output-dir", default="/tmp/his_harness_executor_runtime")
    show_fixture_capability_lease.add_argument("--json", action="store_true")

    execute_sandbox_fixture_node = subparsers.add_parser(
        "execute-sandbox-fixture-node",
        help="invoke the fixed Harness fixture worker with a single-use lease",
    )
    execute_sandbox_fixture_node.add_argument("--lease-id", type=int, required=True)
    execute_sandbox_fixture_node.add_argument("--fixture-root", required=True)
    execute_sandbox_fixture_node.add_argument("--fixture-file", required=True)
    execute_sandbox_fixture_node.add_argument("--timeout-seconds", type=float, default=2.0)
    execute_sandbox_fixture_node.add_argument("--output-dir", default="/tmp/his_harness_executor_runtime")
    execute_sandbox_fixture_node.add_argument("--json", action="store_true")

    run_mock_agent_fixture_schedule = subparsers.add_parser(
        "run-mock-agent-fixture-schedule",
        help="run a dry-run DAG with deterministic fixture-only mock agents",
    )
    run_mock_agent_fixture_schedule.add_argument("--schedule-id", type=int, required=True)
    run_mock_agent_fixture_schedule.add_argument("--fixture-root", required=True)
    run_mock_agent_fixture_schedule.add_argument("--max-parallel", type=int, default=2)
    run_mock_agent_fixture_schedule.add_argument(
        "--behavior-file",
        default="",
        help="optional node-to-fixture-behavior JSON object",
    )
    run_mock_agent_fixture_schedule.add_argument(
        "--output-dir",
        default="/tmp/his_harness_mock_agent_runtime",
    )
    run_mock_agent_fixture_schedule.add_argument("--json", action="store_true")

    show_mock_agent_fixture_run = subparsers.add_parser(
        "show-mock-agent-fixture-run",
        help="show one deterministic mock-agent fixture run and traces",
    )
    show_mock_agent_fixture_run.add_argument("--run-id", type=int, required=True)
    show_mock_agent_fixture_run.add_argument(
        "--output-dir",
        default="/tmp/his_harness_mock_agent_runtime",
    )
    show_mock_agent_fixture_run.add_argument("--json", action="store_true")

    run_model_fixture_node = subparsers.add_parser(
        "run-model-fixture-node",
        help="run one provider-neutral offline model invocation in mock/replay mode",
    )
    run_model_fixture_node.add_argument("--schedule-id", type=int, required=True)
    run_model_fixture_node.add_argument("--node-id", required=True)
    run_model_fixture_node.add_argument("--fixture-root", required=True)
    run_model_fixture_node.add_argument("--mode", choices=["mock", "replay"], default="mock")
    run_model_fixture_node.add_argument("--cassette-file", default="")
    run_model_fixture_node.add_argument("--record-cassette", action="store_true")
    run_model_fixture_node.add_argument(
        "--output-dir",
        default="/tmp/his_harness_model_invocation_runtime",
    )
    run_model_fixture_node.add_argument("--json", action="store_true")

    show_model_fixture_invocation = subparsers.add_parser(
        "show-model-fixture-invocation",
        help="show one provider-neutral offline model fixture invocation",
    )
    show_model_fixture_invocation.add_argument("--invocation-id", type=int, required=True)
    show_model_fixture_invocation.add_argument(
        "--output-dir",
        default="/tmp/his_harness_model_invocation_runtime",
    )
    show_model_fixture_invocation.add_argument("--json", action="store_true")

    run_model_fixture_schedule = subparsers.add_parser(
        "run-model-fixture-schedule",
        help="run a dry-run DAG through provider-neutral offline model adapters",
    )
    run_model_fixture_schedule.add_argument("--schedule-id", type=int, required=True)
    run_model_fixture_schedule.add_argument("--fixture-root", required=True)
    run_model_fixture_schedule.add_argument("--max-parallel", type=int, default=2)
    run_model_fixture_schedule.add_argument("--adapter-file", default="")
    run_model_fixture_schedule.add_argument("--record-cassettes", action="store_true")
    run_model_fixture_schedule.add_argument(
        "--output-dir",
        default="/tmp/his_harness_model_dag_runtime",
    )
    run_model_fixture_schedule.add_argument("--json", action="store_true")

    show_model_fixture_schedule_run = subparsers.add_parser(
        "show-model-fixture-schedule-run",
        help="show one offline model fixture DAG run and traces",
    )
    show_model_fixture_schedule_run.add_argument("--run-id", type=int, required=True)
    show_model_fixture_schedule_run.add_argument(
        "--output-dir",
        default="/tmp/his_harness_model_dag_runtime",
    )
    show_model_fixture_schedule_run.add_argument("--json", action="store_true")

    run_model_provider_smoke = subparsers.add_parser(
        "run-model-provider-smoke",
        help="deprecated: blocked; use a Manager-confirmed model smoke plan",
    )
    run_model_provider_smoke.add_argument(
        "--profile-policy",
        default=str(PROJECT_ROOT / "config" / "model_providers.example.json"),
    )
    run_model_provider_smoke.add_argument("--profile-key", required=True)
    run_model_provider_smoke.add_argument("--credentials-file", required=True)
    run_model_provider_smoke.add_argument("--allow-credentials", action="store_true")
    run_model_provider_smoke.add_argument("--allow-network", action="store_true")
    run_model_provider_smoke.add_argument("--authorization-id", required=True)
    run_model_provider_smoke.add_argument(
        "--output-dir",
        default="/tmp/his_harness_model_provider_smoke",
    )
    run_model_provider_smoke.add_argument("--json", action="store_true")

    show_model_provider_smoke = subparsers.add_parser(
        "show-model-provider-smoke",
        help="show one redacted model provider smoke audit record",
    )
    show_model_provider_smoke.add_argument("--smoke-id", type=int, required=True)
    show_model_provider_smoke.add_argument(
        "--output-dir",
        default="/tmp/his_harness_model_provider_smoke",
    )
    show_model_provider_smoke.add_argument("--json", action="store_true")

    workbench = subparsers.add_parser("workbench", help="export readonly task workbench detail")
    add_task_lookup_args(workbench)
    workbench.add_argument("--output-dir", default="/tmp/his_harness_task_workbench")
    workbench.add_argument("--json", action="store_true")

    record_change = subparsers.add_parser("record-change", help="record a task code change diff without touching the business repo")
    add_task_lookup_args(record_change)
    record_change.add_argument("--task-run-id", type=int)
    record_change.add_argument("--run-id", type=int)
    record_change.add_argument("--source-type", default="manual")
    record_change.add_argument("--status", default="recorded")
    record_change.add_argument("--project-path", default="")
    record_change.add_argument("--allowed-path", action="append", default=[])
    record_change.add_argument("--diff-path", default="")
    record_change.add_argument("--diff-text", default="")
    record_change.add_argument("--verification-status", default="")
    record_change.add_argument("--notes", default="")
    record_change.add_argument("--json", action="store_true")

    manual_verification = subparsers.add_parser("record-manual-verification", help="record user-confirmed runtime acceptance without relaxing source gates")
    add_task_lookup_args(manual_verification)
    manual_verification.add_argument("--source-task-run-id", type=int, help="source task run that remains traceable")
    manual_verification.add_argument("--source-run-id", type=int, help="source Harness run when task run id is unavailable")
    manual_verification.add_argument("--status", choices=["passed", "failed"], default="passed")
    manual_verification.add_argument("--verifier", default="user")
    manual_verification.add_argument("--summary", required=True, help="actual runtime verification conclusion")
    manual_verification.add_argument("--scenario", action="append", default=[])
    manual_verification.add_argument("--note", action="append", default=[])
    manual_verification.add_argument("--output-root", default=str(DEFAULT_TASK_OUTPUT_ROOT))
    manual_verification.add_argument("--json", action="store_true")

    rollback_plan = subparsers.add_parser("rollback-plan", help="create a readonly rollback dry-run plan for a recorded change")
    add_task_lookup_args(rollback_plan)
    rollback_plan.add_argument("--change-id", default="")
    rollback_plan.add_argument("--target-change-sequence", type=int)
    rollback_plan.add_argument("--output-dir", default="/tmp/his_harness_rollback_plan")
    rollback_plan.add_argument("--json", action="store_true")

    rollback_apply = subparsers.add_parser("rollback-apply", help="transactionally roll back one recorded local change; never commits or pushes")
    add_task_lookup_args(rollback_apply)
    rollback_apply.add_argument("--change-id", default="")
    rollback_apply.add_argument("--target-change-sequence", type=int)
    rollback_apply.add_argument("--confirm", required=True, help="exact confirmation token ROLLBACK:<change-id>")
    rollback_apply.add_argument(
        "--verify-command",
        action="append",
        default=[],
        help="targeted local verification command to run after rollback; repeatable",
    )
    rollback_apply.add_argument("--json", action="store_true")

    workspace_cmd = subparsers.add_parser("workspace", help="export readonly local HTML workspace")
    workspace_cmd.add_argument("--limit", type=int, default=50)
    workspace_cmd.add_argument("--output-dir", default="/tmp/his_harness_task_workspace")
    workspace_cmd.add_argument("--entity-id", default="", help="filter by exact DFHIS work item id")
    workspace_cmd.add_argument("--task-key", default="", help="filter by exact task key")
    workspace_cmd.add_argument("--entity-kind", choices=["bug", "requirement", "task"], default="", help="filter by task entity kind")
    workspace_cmd.add_argument("--status", default="", help="filter by exact task status")
    workspace_cmd.add_argument("--verification-status", default="", help="filter by exact verification status")
    workspace_cmd.add_argument("--ui-evidence-status", default="", help="filter by exact UI evidence status")
    workspace_cmd.add_argument("--can-commit", choices=["yes", "no", "true", "false", "1", "0"], default="", help="filter by can_commit")
    workspace_cmd.add_argument("--sample-only", action="store_true", help="only include registered real sample outputs")
    workspace_cmd.add_argument("--include-config-summary", action="store_true", help="include readonly v0.22 Rule Pack/Profile/Credential summary")
    workspace_cmd.add_argument("--include-config-preview", action="store_true", help="include readonly v0.25 provider template and rule preview; implies --include-config-summary")
    workspace_cmd.add_argument("--include-config-share-validation", action="store_true", help="include readonly v0.26 share-package validation and local override strategy; implies summary and preview")
    workspace_cmd.add_argument("--include-config-import-draft", action="store_true", help="generate readonly v0.27 config import draft files into --draft-output-dir; implies summary, preview and share validation")
    workspace_cmd.add_argument("--include-config-import-review", action="store_true", help="include readonly v0.28 review of v0.27 draft files from --draft-input-dir; implies summary, preview and share validation")
    workspace_cmd.add_argument("--include-config-template-index", action="store_true", help="include readonly v0.29 template index and optional diff for v0.27 draft files; implies summary, preview, share validation and import review")
    workspace_cmd.add_argument("--include-config-wizard", action="store_true", help="include readonly v0.30 configuration wizard; implies summary, preview, share validation, import review and template index")
    workspace_cmd.add_argument("--draft-output-dir", default="", help="user-selected directory for v0.27 draft files; required with --include-config-import-draft")
    workspace_cmd.add_argument("--draft-input-dir", default="", help="directory containing v0.27 draft files; required with --include-config-import-review unless --include-config-import-draft uses --draft-output-dir")
    workspace_cmd.add_argument("--compare-draft-input-dir", default="", help="optional second draft directory for v0.29 workspace template diff summary")
    workspace_cmd.add_argument("--overwrite-drafts", action="store_true", help="overwrite existing draft files in --draft-output-dir")
    workspace_cmd.add_argument("--rule-pack", default="", help="Rule Pack JSON path for --include-config-summary")
    workspace_cmd.add_argument("--profile-config", default="", help="Profile config JSON path for --include-config-summary")
    workspace_cmd.add_argument("--profile-key", default="", help="Profile key for --include-config-summary")
    workspace_cmd.add_argument("--credentials-file", default="", help="credentials file path for readonly credential status summary")
    workspace_cmd.add_argument("--check-keychain", action="store_true", help="also check macOS Keychain for credential status")
    workspace_cmd.add_argument("--json", action="store_true")

    register_run = subparsers.add_parser("register-run", help="register an existing Harness output directory as a task run")
    add_task_lookup_args(register_run)
    register_run.add_argument("--output-dir", required=True, help="existing Harness output directory to register")
    register_run.add_argument("--source-run-id", type=int, help="reuse the matching run.id from the existing output; mismatch is rejected")
    register_run.add_argument(
        "--execution-mode",
        choices=["readonly", "worktree", "review-worktree", "fullstack-worktree", "precommit-verify", "single-demand-trial", "core-closure-trial", "auto-local"],
        default="precommit-verify",
    )
    register_run.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    register_run.add_argument("--project-path", action="append", default=[])
    register_run.add_argument("--notes", default="")
    register_run.add_argument("--json", action="store_true")

    rerun_precommit = subparsers.add_parser("rerun-precommit", help="rerun precommit verification from a Harness task")
    add_task_lookup_args(rerun_precommit)
    rerun_precommit.add_argument("--demand", default="", help="inline demand text; optional when task already has title/url")
    rerun_precommit.add_argument("--demand-file", default="", help="demand text file")
    rerun_precommit.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    rerun_precommit.add_argument("--project-path", default="", help="specific Git repo path; falls back to task project_paths")
    rerun_precommit.add_argument("--allowed-path", action="append", default=[])
    rerun_precommit.add_argument("--verify-command", action="append", default=[])
    rerun_precommit.add_argument("--method-test-command", action="append", default=[])
    rerun_precommit.add_argument("--ui-evidence-path", action="append", default=[])
    rerun_precommit.add_argument("--ui-capture-command", action="append", default=[])
    rerun_precommit.add_argument("--target-key", default="")
    rerun_precommit.add_argument("--target-name", default="")
    rerun_precommit.add_argument("--target-role", default="frontend")
    rerun_precommit.add_argument("--worktree-dir", default=str(DEFAULT_TASK_WORKTREE_ROOT))
    rerun_precommit.add_argument("--output-root", default=str(DEFAULT_TASK_OUTPUT_ROOT))
    rerun_precommit.add_argument("--output-dir", default="")
    rerun_precommit.add_argument("--json", action="store_true")

    run = subparsers.add_parser("run", help="run an existing or new task through Harness workflow")
    add_task_lookup_args(run)
    run.add_argument("--demand", default="", help="inline demand text; optional when Yunxiao URL is provided")
    run.add_argument("--demand-file", default="", help="demand text file")
    run.add_argument("--mode", choices=["openai", "anthropic", "mock"], default=os.environ.get("HARNESS_LLM_MODE") or "mock")
    run.add_argument("--load-claude-settings", action="store_true")
    run.add_argument(
        "--execution-mode",
        choices=["readonly", "worktree", "review-worktree", "fullstack-worktree", "precommit-verify", "single-demand-trial", "core-closure-trial", "auto-local"],
        default="readonly",
    )
    run.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    run.add_argument("--project-path", action="append", default=[])
    run.add_argument("--allowed-path", action="append", default=[])
    run.add_argument("--verify-command", action="append", default=[])
    run.add_argument("--requirement-evidence-file", default="", help="local v0.23 requirement_evidence JSON/text file to include explicitly")
    run.add_argument("--multi-service-evidence-file", default="", help="JSON file containing user-selected multi-service evidence/verification supplements")
    run.add_argument("--yunxiao-ignore-comments", action="store_true", help="do not request or use Yunxiao comments for this run")
    run.add_argument("--worktree-dir", default=str(DEFAULT_TASK_WORKTREE_ROOT))
    run.add_argument("--output-root", default=str(DEFAULT_TASK_OUTPUT_ROOT))
    run.add_argument("--max-retries", type=int, default=2)
    run.add_argument("--max-edit-rounds", type=int, default=2)
    run.add_argument("--review-commit", default="HEAD")
    run.add_argument("--review-base", default="")
    run.add_argument("--json", action="store_true")

    args = parser.parse_args()
    manager = TaskManager()

    if args.command == "register-dynamic-plan":
        registry = DynamicPlanRegistry()
        plan_payload = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
        registration = registry.register_plan(plan_payload, task_key=args.task_key)
        snapshot = registration["snapshot"]
        files = write_dynamic_registry_outputs(Path(args.output_dir), snapshot)
        result = {
            "registration": {key: value for key, value in registration.items() if key != "snapshot"},
            "snapshot": snapshot,
            "files": [str(path) for path in files],
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Dynamic plan registered.")
            print(f"Task ID: {registration['task_id']}")
            print(f"Plan ID: {registration['plan_id']}")
            print(f"Idempotent: {registration['idempotent']}")
            print(f"Registry JSON: {files[0]}")
            print(f"Registry Markdown: {files[1]}")
            print(f"Recovery JSON: {files[2]}")
        return

    if args.command == "start-dynamic-schedule":
        scheduler = DynamicDryRunScheduler()
        snapshot = scheduler.start(args.plan_id)
        files = write_dynamic_schedule_outputs(Path(args.output_dir), snapshot)
        result = {"snapshot": snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Dynamic dry-run schedule started.")
            print(f"Schedule ID: {snapshot['schedule']['id']}")
            print(f"Status: {snapshot['schedule']['status']}")
            print("Execution enabled: false")
            print(f"Schedule JSON: {files[0]}")
        return

    if args.command == "advance-dynamic-schedule":
        scheduler = DynamicDryRunScheduler()
        event = None
        if args.event_file:
            event = json.loads(Path(args.event_file).read_text(encoding="utf-8"))
        snapshot = scheduler.advance(args.schedule_id, event)
        files = write_dynamic_schedule_outputs(Path(args.output_dir), snapshot)
        result = {"snapshot": snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Dynamic dry-run schedule advanced.")
            print(f"Schedule ID: {args.schedule_id}")
            print(f"Status: {snapshot['schedule']['status']}")
            print("Execution enabled: false")
            print(f"Schedule JSON: {files[0]}")
        return

    if args.command == "show-dynamic-schedule":
        scheduler = DynamicDryRunScheduler()
        snapshot = scheduler.get_schedule(args.schedule_id)
        files = write_dynamic_schedule_outputs(Path(args.output_dir), snapshot)
        result = {"snapshot": snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Dynamic dry-run schedule exported.")
            print(f"Schedule ID: {args.schedule_id}")
            print(f"Status: {snapshot['schedule']['status']}")
            print("Execution enabled: false")
            print(f"Schedule JSON: {files[0]}")
        return

    if args.command == "prepare-dynamic-node-context":
        runtime = ControlledNodeRuntime()
        context = runtime.prepare_context(
            args.schedule_id,
            args.node_id,
            requested_tools=tuple(args.requested_tool),
        )
        files = write_node_runtime_outputs(Path(args.output_dir), {"context": context})
        result = {"context": context, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Fixture-only dynamic node context prepared.")
            print(f"Context ID: {context['id']}")
            print(f"Node: {context['node_id']}")
            print(f"Permission: {context['permission_status']}")
            print("Real execution enabled: false")
            print(f"Context JSON: {files[0]}")
        return

    if args.command == "execute-fixture-node":
        runtime = ControlledNodeRuntime()
        execution = runtime.execute_fixture(
            args.context_id,
            fixture_root=Path(args.fixture_root),
            fixture_file=Path(args.fixture_file),
        )
        files = write_node_runtime_outputs(Path(args.output_dir), {"execution": execution})
        result = {"execution": execution, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Fixture-only node execution recorded.")
            print(f"Execution ID: {execution['id']}")
            print(f"Status: {execution['status']}")
            print("Business valid: false")
            print("Promotion enabled: false")
            print(f"Execution JSON: {files[0]}")
        return

    if args.command == "show-fixture-node-execution":
        runtime = ControlledNodeRuntime()
        execution = runtime.get_execution(args.execution_id)
        files = write_node_runtime_outputs(Path(args.output_dir), {"execution": execution})
        result = {"execution": execution, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Fixture-only node execution exported.")
            print(f"Execution ID: {execution['id']}")
            print(f"Status: {execution['status']}")
            print("Business valid: false")
            print(f"Execution JSON: {files[0]}")
        return

    if args.command == "issue-fixture-capability-lease":
        runtime = SandboxExecutorRuntime()
        lease = runtime.issue_lease(
            args.context_id,
            capabilities=tuple(args.capability),
            ttl_seconds=args.ttl_seconds,
        )
        files = write_executor_runtime_outputs(Path(args.output_dir), {"lease": lease})
        result = {"lease": lease, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Fixture capability lease issued.")
            print(f"Lease ID: {lease['id']}")
            print(f"Status: {lease['status']}")
            print(f"Expires at: {lease['expires_at']}")
            print("Max uses: 1")
            print(f"Lease JSON: {files[0]}")
        return

    if args.command == "show-fixture-capability-lease":
        runtime = SandboxExecutorRuntime()
        lease = runtime.get_lease(args.lease_id)
        files = write_executor_runtime_outputs(Path(args.output_dir), {"lease": lease})
        result = {"lease": lease, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Fixture capability lease exported.")
            print(f"Lease ID: {lease['id']}")
            print(f"Status: {lease['status']}")
            print(f"Uses: {lease['use_count']}/{lease['max_uses']}")
            print(f"Lease JSON: {files[0]}")
        return

    if args.command == "execute-sandbox-fixture-node":
        runtime = SandboxExecutorRuntime()
        execution = runtime.execute(
            args.lease_id,
            fixture_root=Path(args.fixture_root),
            fixture_file=Path(args.fixture_file),
            timeout_seconds=args.timeout_seconds,
        )
        files = write_executor_runtime_outputs(Path(args.output_dir), {"execution": execution})
        result = {"execution": execution, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Sandbox fixture node execution recorded.")
            print(f"Execution ID: {execution['id']}")
            print(f"Status: {execution['status']}")
            print("Business valid: false")
            print("Promotion enabled: false")
            print(f"Execution JSON: {files[0]}")
        return

    if args.command == "run-mock-agent-fixture-schedule":
        runtime = DeterministicMockAgentRuntime()
        behavior_overrides = {}
        if args.behavior_file:
            behavior_overrides = json.loads(Path(args.behavior_file).read_text(encoding="utf-8"))
            if not isinstance(behavior_overrides, dict):
                raise ValueError("behavior-file 必须是 node_id 到 behavior 的 JSON 对象")
        snapshot = runtime.run(
            args.schedule_id,
            fixture_root=Path(args.fixture_root),
            max_parallel=args.max_parallel,
            behavior_overrides=behavior_overrides,
        )
        files = write_mock_agent_runtime_outputs(Path(args.output_dir), snapshot)
        result = {**snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Deterministic mock-agent fixture schedule recorded.")
            print(f"Run ID: {snapshot['run']['id']}")
            print(f"Status: {snapshot['run']['status']}")
            print(f"Waves: {snapshot['metrics']['wave_count']}")
            print(f"Nodes: {snapshot['metrics']['node_count']}")
            print("Business valid: false")
            print(f"Run JSON: {files[0]}")
        return

    if args.command == "show-mock-agent-fixture-run":
        runtime = DeterministicMockAgentRuntime()
        snapshot = runtime.get_run(args.run_id)
        files = write_mock_agent_runtime_outputs(Path(args.output_dir), snapshot)
        result = {**snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Deterministic mock-agent fixture run exported.")
            print(f"Run ID: {snapshot['run']['id']}")
            print(f"Status: {snapshot['run']['status']}")
            print("Business valid: false")
            print(f"Run JSON: {files[0]}")
        return

    if args.command == "run-model-fixture-node":
        runtime = OfflineModelInvocationRuntime()
        snapshot = runtime.invoke(
            args.schedule_id,
            args.node_id,
            fixture_root=Path(args.fixture_root),
            mode=args.mode,
            cassette_file=Path(args.cassette_file) if args.cassette_file else None,
            record_cassette=args.record_cassette,
        )
        files = write_model_invocation_outputs(Path(args.output_dir), snapshot)
        result = {**snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Offline model fixture invocation recorded.")
            print(f"Invocation ID: {snapshot['invocation']['id']}")
            print(f"Mode: {snapshot['invocation']['mode']}")
            print(f"Status: {snapshot['invocation']['status']}")
            print("Business valid: false")
            print("Credentials/network: disabled")
            print(f"Invocation JSON: {files[0]}")
        return

    if args.command == "show-model-fixture-invocation":
        runtime = OfflineModelInvocationRuntime()
        snapshot = runtime.get_invocation(args.invocation_id)
        files = write_model_invocation_outputs(Path(args.output_dir), snapshot)
        result = {**snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Offline model fixture invocation exported.")
            print(f"Invocation ID: {snapshot['invocation']['id']}")
            print(f"Status: {snapshot['invocation']['status']}")
            print("Business valid: false")
            print(f"Invocation JSON: {files[0]}")
        return

    if args.command == "run-model-fixture-schedule":
        if args.adapter_file and args.record_cassettes:
            raise ValueError("--adapter-file 与 --record-cassettes 不能同时使用，请在 adapter policy 中显式配置")
        adapter_policy = None
        if args.adapter_file:
            adapter_policy = json.loads(Path(args.adapter_file).read_text(encoding="utf-8"))
            if not isinstance(adapter_policy, dict):
                raise ValueError("adapter-file 必须是 JSON 对象")
        elif args.record_cassettes:
            adapter_policy = {
                "schema_version": "1.0-offline-model-dag-adapters",
                "default": {"mode": "mock", "record_cassette": True},
                "nodes": {},
            }
        runtime = OfflineModelDagRuntime()
        snapshot = runtime.run(
            args.schedule_id,
            fixture_root=Path(args.fixture_root),
            max_parallel=args.max_parallel,
            adapter_policy=adapter_policy,
        )
        files = write_model_dag_outputs(Path(args.output_dir), snapshot)
        result = {**snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Offline model fixture DAG recorded.")
            print(f"Run ID: {snapshot['run']['id']}")
            print(f"Status: {snapshot['run']['status']}")
            print(f"Waves: {snapshot['metrics']['wave_count']}")
            print(f"Nodes: {snapshot['metrics']['node_count']}")
            print("Business valid: false")
            print("Credentials/network: disabled")
            print(f"Run JSON: {files[0]}")
        return

    if args.command == "show-model-fixture-schedule-run":
        runtime = OfflineModelDagRuntime()
        snapshot = runtime.get_run(args.run_id)
        files = write_model_dag_outputs(Path(args.output_dir), snapshot)
        result = {**snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Offline model fixture DAG exported.")
            print(f"Run ID: {snapshot['run']['id']}")
            print(f"Status: {snapshot['run']['status']}")
            print("Business valid: false")
            print(f"Run JSON: {files[0]}")
        return

    if args.command == "run-model-provider-smoke":
        # Legacy flags were a bypass around Manager B1/B2/B6.  Parse them only
        # for backwards-compatible diagnostics; never construct a runtime,
        # read a profile/credential file, or open a network transport here.
        result = {
            "status": "blocked",
            "reason": "legacy_model_provider_smoke_disabled",
            "required_action": "model.single_node.smoke",
            "credentials_read": False,
            "external_calls": False,
            "write_performed": False,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Legacy model provider smoke is blocked.")
            print("Create and confirm the Manager action: model.single_node.smoke")
        raise SystemExit(2)

    if args.command == "show-model-provider-smoke":
        runtime = ControlledModelProviderRuntime()
        snapshot = runtime.get_smoke(args.smoke_id)
        files = write_model_provider_smoke_outputs(Path(args.output_dir), snapshot)
        result = {**snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            smoke = snapshot["smoke"]
            print("Controlled model provider smoke exported.")
            print(f"Smoke ID: {smoke['id']}")
            print(f"Status: {smoke['status']}")
            print(f"Endpoint host: {smoke['endpoint_host']}")
            print(f"Model: {smoke['model']}")
            print(f"Smoke JSON: {files[0]}")
        return

    if args.command == "show-dynamic-plan":
        registry = DynamicPlanRegistry()
        snapshot = registry.get_plan(args.plan_id)
        files = write_dynamic_registry_outputs(Path(args.output_dir), snapshot)
        if args.json:
            print(json.dumps({"snapshot": snapshot, "files": [str(path) for path in files]}, ensure_ascii=False, indent=2))
        else:
            print("Dynamic plan exported.")
            print(f"Plan ID: {args.plan_id}")
            print(f"Registry JSON: {files[0]}")
            print(f"Registry Markdown: {files[1]}")
            print(f"Recovery JSON: {files[2]}")
        return

    if args.command == "record-dynamic-contract":
        registry = DynamicPlanRegistry()
        contract = json.loads(Path(args.contract_file).read_text(encoding="utf-8"))
        artifact = registry.record_contract(
            plan_id=args.plan_id,
            node_id=args.node_id,
            schema_name=str(contract.get("schema_name") or ""),
            schema_version=str(contract.get("schema_version") or ""),
            producer=str(contract.get("producer") or ""),
            content=contract.get("content") or {},
            input_artifact_ids=tuple(str(item) for item in contract.get("input_artifact_ids") or []),
        )
        snapshot = registry.get_plan(args.plan_id)
        files = write_dynamic_registry_outputs(Path(args.output_dir), snapshot)
        result = {"artifact": artifact, "snapshot": snapshot, "files": [str(path) for path in files]}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Dynamic contract recorded.")
            print(f"Artifact ID: {artifact['artifact_id']}")
            print(f"Version: {artifact['artifact_version']}")
            print(f"Registry JSON: {files[0]}")
        return

    if args.command == "create":
        task = manager.create_task(
            TaskCreateOptions(
                yunxiao_url=args.yunxiao_url,
                title=args.title,
                entity_kind=args.entity_kind,
                entity_id=args.entity_id,
                project_root=args.project_root,
                project_paths=args.project_path,
                base_branch=args.base_branch,
                work_branch=args.work_branch,
                notes=args.notes,
            )
        )
        print_task(task, manager.list_task_runs(int(task["id"])), as_json=args.json)
        return

    if args.command == "list":
        tasks = manager.list_tasks(limit=args.limit)
        if args.json:
            print(json.dumps(tasks, ensure_ascii=False, indent=2))
        else:
            print(render_task_list(tasks))
        return

    if args.command == "dashboard":
        filters = TaskDashboardFilters(
            entity_id=args.entity_id,
            task_key=args.task_key,
            entity_kind=args.entity_kind,
            status=args.status,
            verification_status=args.verification_status,
            ui_evidence_status=args.ui_evidence_status,
            can_commit=parse_optional_bool(args.can_commit),
            sample_only=args.sample_only,
        )
        dashboard = manager.build_dashboard(limit=args.limit, filters=filters)
        files = manager.write_dashboard_outputs(output_dir=args.output_dir, dashboard=dashboard)
        if args.json:
            print(json.dumps({"dashboard": dashboard, "files": files}, ensure_ascii=False, indent=2))
        else:
            summary = dashboard.get("summary") or {}
            sample_set = dashboard.get("sample_set") or {}
            print("Task Manager dashboard exported.")
            print(f"Tasks: {summary.get('task_count', 0)}")
            print(f"Runs: {summary.get('run_count', 0)}")
            print(f"Samples: {sample_set.get('count', 0)}")
            print(f"JSON: {files['json']}")
            print(f"Markdown: {files['markdown']}")
            print(f"HTML: {files['html']}")
            print(f"Sample JSON: {files['sample_set_json']}")
            print(f"Sample Markdown: {files['sample_set_markdown']}")
        return

    if args.command == "show":
        task = manager.resolve_task(task_id=args.task_id, task_key=args.task_key, yunxiao_url=args.yunxiao_url, title=args.title)
        print_task(task, manager.list_task_runs(int(task["id"])), as_json=args.json)
        return

    if args.command == "workbench":
        workbench_data = manager.build_task_workbench(task_id=args.task_id, task_key=args.task_key, yunxiao_url=args.yunxiao_url, title=args.title)
        files = manager.write_workbench_outputs(output_dir=args.output_dir, workbench=workbench_data)
        if args.json:
            print(json.dumps({"workbench": workbench_data, "files": files}, ensure_ascii=False, indent=2))
        else:
            task = workbench_data.get("task") or {}
            commands = workbench_data.get("commands") or {}
            print("Task Manager workbench exported.")
            print(f"Task: {task.get('task_key') or task.get('task_id') or '-'}")
            print(f"Runs: {len(workbench_data.get('runs') or [])}")
            print(f"Artifacts: {len(workbench_data.get('artifacts') or [])}")
            print(f"JSON: {files['json']}")
            print(f"Markdown: {files['markdown']}")
            if commands.get("rerun_precommit"):
                print(f"Rerun: {commands['rerun_precommit']}")
        return

    if args.command == "record-change":
        change = manager.record_change(
            TaskChangeRecordOptions(
                task_id=args.task_id,
                task_key=args.task_key,
                yunxiao_url=args.yunxiao_url,
                title=args.title,
                task_run_id=args.task_run_id,
                run_id=args.run_id,
                source_type=args.source_type,
                status=args.status,
                project_path=args.project_path,
                allowed_paths=args.allowed_path,
                diff_path=args.diff_path,
                diff_text=args.diff_text,
                verification_status=args.verification_status,
                notes=args.notes,
            )
        )
        if args.json:
            print(json.dumps(change, ensure_ascii=False, indent=2))
        else:
            print("Task change recorded.")
            print(f"Change ID: {change.get('change_id')}")
            print(f"Sequence: {change.get('change_sequence')}")
            print(f"Diff: {change.get('diff_path') or '-'}")
            print(f"Rollback: {change.get('rollback_mode')}")
        return

    if args.command == "record-manual-verification":
        task, task_run, output_dir = manager.record_manual_verification(
            TaskManualVerificationOptions(
                task_id=args.task_id,
                task_key=args.task_key,
                yunxiao_url=args.yunxiao_url,
                title=args.title,
                source_task_run_id=args.source_task_run_id,
                source_run_id=args.source_run_id,
                status=args.status,
                verifier=args.verifier,
                summary=args.summary,
                scenarios=args.scenario,
                notes=args.note,
                output_root=args.output_root,
            )
        )
        if args.json:
            print(task_to_json(task, manager.list_task_runs(int(task["id"]))))
        else:
            print("Manual runtime verification recorded.")
            print(f"Task Run ID: {task_run.get('id')}")
            print(f"Run ID: {task_run.get('run_id')}")
            print(f"Status: {task.get('verification_status')}")
            print(f"Output: {output_dir}")
            print("Source gate, auto-apply, commit, remote Git and Yunxiao writes remain disabled.")
        return

    if args.command == "rollback-plan":
        plan = manager.build_change_rollback_plan(
            TaskRollbackPlanOptions(
                task_id=args.task_id,
                task_key=args.task_key,
                yunxiao_url=args.yunxiao_url,
                title=args.title,
                change_id=args.change_id,
                target_change_sequence=args.target_change_sequence,
                output_dir=args.output_dir,
            )
        )
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print("Rollback dry-run plan exported.")
            print(f"Status: {plan.get('status')}")
            print(f"Plan: {plan.get('plan_path')}")
            print(f"Markdown: {plan.get('markdown_path')}")
            print(f"Reverse patch: {plan.get('reverse_patch_path')}")
            print(f"Check: {(plan.get('commands') or {}).get('apply_reverse_patch_check')}")
        return

    if args.command == "rollback-apply":
        result = manager.apply_change_rollback(
            TaskRollbackApplyOptions(
                task_id=args.task_id,
                task_key=args.task_key,
                yunxiao_url=args.yunxiao_url,
                title=args.title,
                change_id=args.change_id,
                target_change_sequence=args.target_change_sequence,
                confirmation=args.confirm,
                verify_commands=args.verify_command,
            )
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Local rollback completed." if result.get("status") == "success" else "Local rollback blocked or failed.")
            print(f"Status: {result.get('status')}")
            print(f"Change: {result.get('change_id')}")
            print(f"Idempotent: {result.get('idempotent')}")
            print("Remote Git and external writes: disabled")
        if result.get("status") != "success":
            raise SystemExit(2)
        return

    if args.command == "workspace":
        filters = TaskDashboardFilters(
            entity_id=args.entity_id,
            task_key=args.task_key,
            entity_kind=args.entity_kind,
            status=args.status,
            verification_status=args.verification_status,
            ui_evidence_status=args.ui_evidence_status,
            can_commit=parse_optional_bool(args.can_commit),
            sample_only=args.sample_only,
        )
        config_summary = None
        config_preview = None
        config_share_validation = None
        config_import_draft = None
        config_import_review = None
        config_template_index = None
        config_wizard = None
        if args.include_config_import_draft and not args.draft_output_dir:
            raise SystemExit("--include-config-import-draft 需要显式传入 --draft-output-dir，避免写到非用户选择目录。")
        review_input_dir = args.draft_input_dir or (args.draft_output_dir if args.include_config_import_draft else "")
        if args.include_config_import_review and not review_input_dir:
            raise SystemExit("--include-config-import-review 需要显式传入 --draft-input-dir，避免误读非用户选择目录。")
        if args.include_config_template_index and not review_input_dir:
            raise SystemExit("--include-config-template-index 需要显式传入 --draft-input-dir，避免误读非用户选择目录。")
        if args.include_config_wizard and not review_input_dir:
            raise SystemExit("--include-config-wizard 需要显式传入 --draft-input-dir，避免误读非用户选择目录。")
        if (
            args.include_config_summary
            or args.include_config_preview
            or args.include_config_share_validation
            or args.include_config_import_draft
            or args.include_config_import_review
            or args.include_config_template_index
            or args.include_config_wizard
            or args.rule_pack
            or args.profile_config
            or args.profile_key
            or args.credentials_file
            or args.check_keychain
        ):
            config_summary = build_config_summary(
                rule_pack_path=args.rule_pack or None,
                profile_config_path=args.profile_config or None,
                profile_key=args.profile_key,
                credentials_file=args.credentials_file or None,
                check_keychain=args.check_keychain,
            )
            if args.include_config_preview or args.include_config_share_validation or args.include_config_import_draft or args.include_config_import_review or args.include_config_template_index or args.include_config_wizard:
                config_preview = build_configuration_preview(config_summary)
            if args.include_config_share_validation or args.include_config_import_draft or args.include_config_import_review or args.include_config_template_index or args.include_config_wizard:
                config_share_validation = build_configuration_share_validation(
                    summary=config_summary,
                    rule_pack_path=args.rule_pack or None,
                    profile_config_path=args.profile_config or None,
                )
            if args.include_config_import_draft:
                config_import_draft = build_configuration_import_draft(
                    summary=config_summary,
                    rule_pack_path=args.rule_pack or None,
                    profile_config_path=args.profile_config or None,
                    draft_output_dir=args.draft_output_dir,
                    overwrite=args.overwrite_drafts,
                )
                import_draft_files = write_configuration_import_draft_outputs(
                    output_dir=args.draft_output_dir,
                    draft=config_import_draft,
                    overwrite=args.overwrite_drafts,
                )
                config_import_draft = dict(config_import_draft)
                config_import_draft["write_result"] = import_draft_files
            if args.include_config_import_review or args.include_config_template_index or args.include_config_wizard:
                config_import_review = build_configuration_import_review(draft_dir=review_input_dir)
            if args.include_config_template_index or args.include_config_wizard:
                config_template_index = build_configuration_template_index(
                    draft_dirs=[item for item in [review_input_dir, args.compare_draft_input_dir] if item]
                )
            if args.include_config_wizard:
                config_wizard = build_configuration_wizard(
                    config_summary=config_summary,
                    config_preview=config_preview,
                    config_share_validation=config_share_validation,
                    config_import_draft=config_import_draft,
                    config_import_review=config_import_review,
                    config_template_index=config_template_index,
                    draft_input_dir=review_input_dir,
                    compare_draft_input_dir=args.compare_draft_input_dir or None,
                )
        workspace_data = manager.build_task_workspace(
            limit=args.limit,
            filters=filters,
            config_summary=config_summary,
            config_preview=config_preview,
            config_share_validation=config_share_validation,
            config_import_draft=config_import_draft,
            config_import_review=config_import_review,
            config_template_index=config_template_index,
            config_wizard=config_wizard,
        )
        files = manager.write_workspace_outputs(output_dir=args.output_dir, workspace=workspace_data)
        if args.json:
            print(json.dumps({"workspace": workspace_data, "files": files}, ensure_ascii=False, indent=2))
        else:
            summary = workspace_data.get("summary") or {}
            sample_set = workspace_data.get("sample_set") or {}
            print("Task Manager workspace exported.")
            print(f"Tasks: {summary.get('task_count', 0)}")
            print(f"Runs: {summary.get('run_count', 0)}")
            print(f"Samples: {sample_set.get('count', 0)}")
            print(f"JSON: {files['json']}")
            print(f"HTML: {files['html']}")
            print(f"Dashboard HTML: {files['dashboard_html']}")
            print(f"Sample JSON: {files['sample_set_json']}")
            print(f"Workbenches: {len(files.get('workbench_files') or {})}")
            if config_summary:
                print(f"Config Summary: {files['config_summary_markdown']}")
            if config_preview:
                print(f"Config Preview: {files['config_preview_markdown']}")
            if config_share_validation:
                print(f"Config Share Validation: {files['config_share_validation_markdown']}")
            if config_import_draft:
                write_result = config_import_draft.get("write_result") or {}
                print(f"Config Import Draft: {files['config_import_draft_markdown']}")
                print(f"Draft Status: {write_result.get('status')}")
                print(f"Draft Dir: {write_result.get('output_dir')}")
            if config_import_review:
                print(f"Config Import Review: {files['config_import_review_markdown']}")
                print(f"Review Status: {config_import_review.get('status')}")
                print(f"Review Dir: {config_import_review.get('draft_input_dir')}")
            if config_template_index:
                print(f"Config Template Index: {files['config_template_index_markdown']}")
            if config_wizard:
                print(f"Config Wizard: {files['config_wizard_markdown']}")
                print(f"Template Index Status: {config_template_index.get('status')}")
                print(f"Template Sources: {config_template_index.get('source_count')}")
        return

    if args.command == "register-run":
        task, task_run = manager.record_existing_run(
            TaskExistingRunOptions(
                task_id=args.task_id,
                task_key=args.task_key,
                yunxiao_url=args.yunxiao_url,
                title=args.title,
                entity_kind=args.entity_kind,
                entity_id=args.entity_id,
                project_root=args.project_root,
                project_paths=args.project_path,
                output_dir=args.output_dir,
                execution_mode=args.execution_mode,
                source_run_id=args.source_run_id,
                notes=args.notes,
            )
        )
        if args.json:
            print(task_to_json(task, manager.list_task_runs(int(task["id"]))))
        else:
            print(task_to_markdown(task, manager.list_task_runs(int(task["id"]))))
            print()
            print(f"Registered Task Run ID: {task_run.get('id')}")
            print(f"Run ID: {task_run.get('run_id')}")
            print(f"Output: {task_run.get('output_dir')}")
        return

    if args.command == "rerun-precommit":
        task, result, output_dir = manager.rerun_precommit(
            TaskPrecommitRerunOptions(
                task_id=args.task_id,
                task_key=args.task_key,
                yunxiao_url=args.yunxiao_url,
                title=args.title,
                demand_text=args.demand,
                demand_file=args.demand_file,
                project_root=args.project_root,
                project_path=args.project_path,
                allowed_paths=args.allowed_path,
                verify_commands=args.verify_command,
                method_test_commands=args.method_test_command,
                ui_evidence_paths=args.ui_evidence_path,
                ui_capture_commands=args.ui_capture_command,
                target_key=args.target_key,
                target_name=args.target_name,
                target_role=args.target_role,
                worktree_dir=args.worktree_dir,
                output_root=args.output_root,
                output_dir=args.output_dir,
            )
        )
        if args.json:
            print(task_to_json(task, manager.list_task_runs(int(task["id"]))))
        else:
            print(task_to_markdown(task, manager.list_task_runs(int(task["id"]))))
            print()
            print(f"Run ID: {result.manifest.get('run_id') or task.get('latest_run_id')}")
            print(f"Status: {result.status}")
            print(f"Summary: {result.summary}")
            print(f"Output: {output_dir}")
        return

    if args.command == "run":
        task, result, output_dir = manager.run_task(
            TaskRunOptions(
                task_id=args.task_id,
                task_key=args.task_key,
                yunxiao_url=args.yunxiao_url,
                title=args.title,
                demand_text=args.demand,
                demand_file=args.demand_file,
                mode=args.mode,
                load_claude_settings=args.load_claude_settings,
                execution_mode=args.execution_mode,
                project_root=args.project_root,
                project_paths=args.project_path,
                allowed_paths=args.allowed_path,
                verify_commands=args.verify_command,
                requirement_evidence_file=args.requirement_evidence_file,
                multi_service_evidence_file=args.multi_service_evidence_file,
                worktree_dir=args.worktree_dir,
                output_root=args.output_root,
                max_retries=args.max_retries,
                max_edit_rounds=args.max_edit_rounds,
                review_commit=args.review_commit,
                review_base=args.review_base,
                yunxiao_include_comments=not args.yunxiao_ignore_comments,
            )
        )
        if args.json:
            print(task_to_json(task, manager.list_task_runs(int(task["id"]))))
        else:
            print(task_to_markdown(task, manager.list_task_runs(int(task["id"]))))
            print()
            print(f"Run ID: {result.run_id}")
            print(f"Status: {result.status}")
            print(f"Evaluation: {result.evaluation_status}")
            print(f"Output: {output_dir}")
            if args.mode == "mock":
                print("WARNING: 当前为 mock 模式，仅用于演示流程，不可用于真实业务判断。")
        return


def add_task_identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--yunxiao-url", default="", help="Yunxiao requirement/bug URL")
    parser.add_argument("--title", default="", help="task title")
    parser.add_argument("--entity-kind", choices=["bug", "requirement", "task"], default="")
    parser.add_argument("--entity-id", default="", help="DFHIS work item id")


def add_task_lookup_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--task-key", default="")
    add_task_identity_args(parser)


def print_task(task: dict, runs: list[dict], *, as_json: bool) -> None:
    if as_json:
        print(task_to_json(task, runs))
    else:
        print(task_to_markdown(task, runs))


def render_task_list(tasks: list[dict]) -> str:
    if not tasks:
        return "暂无任务。"
    lines = ["ID | Task Key | Entity | Stage | Status | Latest Run | Title", "---|---|---|---|---|---|---"]
    for task in tasks:
        entity = f"{task.get('entity_kind') or '-'}:{task.get('entity_id') or '-'}"
        lines.append(
            " | ".join(
                [
                    str(task.get("id")),
                    str(task.get("task_key")),
                    entity,
                    str(task.get("current_stage")),
                    str(task.get("status")),
                    str(task.get("latest_run_id") or "-"),
                    str(task.get("entity_title") or "-").replace("|", "/"),
                ]
            )
        )
    return "\n".join(lines)


def parse_optional_bool(value: str) -> bool | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    return normalized in {"yes", "true", "1"}


if __name__ == "__main__":
    main()
