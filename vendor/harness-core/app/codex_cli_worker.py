from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from app.sensitive_text import contains_sensitive_text, normalize_sensitive_text


CODEX_EXECUTABLE = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
HARNESS_SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "config" / "schemas"
MAX_PROMPT_BYTES = 65_536
MAX_OUTPUT_BYTES = 1_048_576
MAX_OUTPUT_LINE_BYTES = 65_536
MAX_EVENT_COUNT = 256
MAX_JSON_DEPTH = 12
_MAX_JSON_NODES = 2_048
_MAX_JSON_FIELDS = 256
_MAX_JSON_LIST_ITEMS = 256
_MAX_JSON_KEY_CHARS = 128
_MAX_JSON_SCALAR_CHARS = 16_384
_MAX_SCHEMA_BYTES = 131_072
_MAX_TIMEOUT_SECONDS = 3_600
_HEARTBEAT_SECONDS = 1.0
_MAX_HEARTBEATS = _MAX_TIMEOUT_SECONDS + 1
# The current bundled CLI is 0.149.x; its fixed `exec --json --ephemeral`
# worker/reviewer flags remain compatible with the 0.147 contract. Keep the
# upper bound so a future incompatible CLI still fails closed.
_SUPPORTED_VERSION = ((0, 147, 0), (0, 150, 0))
_CODE_SIGN_TEAM_ID = "2DC432GLL2"
_IDENTITY = re.compile(r"darwin-proc-bsdinfo-v1:\d+:\d+")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,256}")
_PUBLIC_SECRET = re.compile(
    r"(?:\b(?:basic|bearer)\s+\S+|\b(?:ghp_|github_pat_|xox[baprs]-|AKIA|sk-)[A-Za-z0-9._-]{8,}|\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@)",
    re.IGNORECASE,
)
_REQUEST_TOKEN = object()
_PROTOCOL_CANDIDATE = re.compile(r"[a-z0-9._-]{1,64}")
_PROTOCOL_CATEGORIES = frozenset({"missing", "non_string", "too_long", "sensitive", "invalid_chars"})
_KNOWN_PROTOCOL_EVENT_TYPES = frozenset({"thread.started", "turn.started", "turn.failed", "error", "item.started", "item.updated", "item.completed", "turn.completed"})
_KNOWN_PROTOCOL_ITEM_TYPES = frozenset({"agent_message", "reasoning", "command_execution", "file_change", "mcp_tool_call", "web_search", "todo_list", "error"})
_PROTOCOL_TOP_LEVEL_KEYS = ("type", "thread_id", "turn_id", "item", "usage", "error")
_PROTOCOL_ERROR_KEYS = ("message", "type", "code", "param")
_PROTOCOL_ERROR_KINDS = frozenset({"missing", "null", "string", "object", "other"})
_PROTOCOL_STATES = frozenset({"initial", "thread_started", "turn_active", "terminal"})
_ELAPSED_BUCKETS = frozenset({"under_10s", "10_59s", "60_179s", "180_360s", "over_360s"})
_MAX_PROTOCOL_SEQUENCE = _MAX_HEARTBEATS + MAX_EVENT_COUNT + 2

# The complete public classification surface returned in primary_error_code.
# Raw provider/process text is never part of this stable audit vocabulary.
STABLE_WORKER_ERROR_CODES = frozenset({
    "worker_anchor_changed", "worker_cancelled", "worker_cleanup_failed",
    "worker_event_overflow", "worker_event_sink_failed", "worker_executable_changed",
    "worker_executable_invalid", "worker_executable_unsupported", "worker_heartbeat_overflow",
    "worker_identity_unavailable", "worker_internal_error", "worker_output_invalid",
    "worker_output_too_large", "worker_process_failed", "worker_process_group_invalid",
    "worker_process_invalid", "worker_protocol_failed", "worker_protocol_invalid",
    "worker_request_invalid", "worker_reviewer_response_invalid", "worker_spawn_failed",
    "worker_started_sink_failed", "worker_stdin_failed", "worker_stream_failed",
    "worker_stream_unclosed", "worker_timeout",
})


class WorkerRole(str, Enum):
    WORKER = "worker"
    REVIEWER = "reviewer"


class WorkerEventSink(Protocol):
    def on_started(self, pid: int, start_identity: str) -> None: ...

    def on_event(self, event: dict[str, object]) -> None: ...


@dataclass(frozen=True, init=False)
class CodexWorkerRequest:
    role: WorkerRole
    worktree_path: Path
    prompt: str
    timeout_seconds: int
    output_schema_path: Path | None
    expected_schema_sha256: str | None
    image_paths: tuple[Path, ...]
    visual_only: bool
    skip_git_repo_check: bool

    def __init__(
        self,
        *,
        role: WorkerRole,
        worktree_path: Path,
        prompt: str,
        timeout_seconds: int,
        output_schema_path: Path | None,
        expected_schema_sha256: str | None,
        image_paths: tuple[Path, ...] = (),
        visual_only: bool = False,
        skip_git_repo_check: bool = False,
        _token: object | None = None,
    ) -> None:
        if _token is not _REQUEST_TOKEN:
            raise ValueError("worker_request_invalid")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "worktree_path", worktree_path)
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "timeout_seconds", timeout_seconds)
        object.__setattr__(self, "output_schema_path", output_schema_path)
        object.__setattr__(self, "expected_schema_sha256", expected_schema_sha256)
        object.__setattr__(self, "image_paths", image_paths)
        object.__setattr__(self, "visual_only", visual_only)
        object.__setattr__(self, "skip_git_repo_check", skip_git_repo_check)

    @classmethod
    def worker(
        cls,
        worktree_path: Path,
        prompt: str,
        timeout_seconds: int,
        *,
        image_paths: tuple[Path, ...] = (),
        visual_only: bool = False,
        skip_git_repo_check: bool = False,
    ) -> "CodexWorkerRequest":
        return cls(
            role=WorkerRole.WORKER,
            worktree_path=worktree_path,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            output_schema_path=None,
            expected_schema_sha256=None,
            image_paths=image_paths,
            visual_only=visual_only,
            skip_git_repo_check=skip_git_repo_check,
            _token=_REQUEST_TOKEN,
        )

    @classmethod
    def reviewer(
        cls,
        worktree_path: Path,
        prompt: str,
        timeout_seconds: int,
        output_schema_path: Path,
        expected_schema_sha256: str,
        *,
        image_paths: tuple[Path, ...] = (),
        visual_only: bool = False,
        skip_git_repo_check: bool = False,
    ) -> "CodexWorkerRequest":
        return cls(
            role=WorkerRole.REVIEWER,
            worktree_path=worktree_path,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            output_schema_path=output_schema_path,
            expected_schema_sha256=expected_schema_sha256,
            image_paths=image_paths,
            visual_only=visual_only,
            skip_git_repo_check=skip_git_repo_check,
            _token=_REQUEST_TOKEN,
        )

    @classmethod
    def visual_reviewer(
        cls,
        worktree_path: Path,
        prompt: str,
        timeout_seconds: int,
        output_schema_path: Path,
        expected_schema_sha256: str,
        image_paths: tuple[Path, ...],
    ) -> "CodexWorkerRequest":
        return cls.reviewer(
            worktree_path,
            prompt,
            timeout_seconds,
            output_schema_path,
            expected_schema_sha256,
            image_paths=image_paths,
            visual_only=True,
            skip_git_repo_check=True,
        )

@dataclass(frozen=True)
class CleanupOutcome:
    group_extinct: bool
    leader_reaped: bool
    error_code: str


@dataclass(frozen=True)
class ProtocolRejectionAudit:
    candidate_event_type: str
    candidate_item_type: str
    top_level_keys: int
    raw_line_sha256: str
    sequence_no: int
    fsm_state: str
    elapsed_bucket: str
    error_container_kind: str
    error_known_keys: int
    error_field_count: int

    def as_mapping(self) -> dict[str, object]:
        value = {
            "candidate_event_type": self.candidate_event_type,
            "candidate_item_type": self.candidate_item_type,
            "top_level_keys": self.top_level_keys,
            "raw_line_sha256": self.raw_line_sha256,
            "sequence_no": self.sequence_no,
            "fsm_state": self.fsm_state,
            "elapsed_bucket": self.elapsed_bucket,
            "error_container_kind": self.error_container_kind,
            "error_known_keys": self.error_known_keys,
            "error_field_count": self.error_field_count,
        }
        validate_protocol_rejection_audit(value)
        return value


def validate_protocol_rejection_audit(value: object) -> None:
    keys = {"candidate_event_type", "candidate_item_type", "top_level_keys", "raw_line_sha256", "sequence_no", "fsm_state", "elapsed_bucket", "error_container_kind", "error_known_keys", "error_field_count"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("worker_protocol_audit_invalid")
    candidate_allowlists = {
        "candidate_event_type": _PROTOCOL_CATEGORIES | _KNOWN_PROTOCOL_EVENT_TYPES | {"unknown_event_type"},
        "candidate_item_type": _PROTOCOL_CATEGORIES | _KNOWN_PROTOCOL_ITEM_TYPES | {"unknown_item_type"},
    }
    for key, allowed in candidate_allowlists.items():
        candidate = value.get(key)
        if not isinstance(candidate, str) or candidate not in allowed:
            raise ValueError("worker_protocol_audit_invalid")
    mask = value.get("top_level_keys")
    if not isinstance(mask, int) or isinstance(mask, bool) or not 0 <= mask < (1 << len(_PROTOCOL_TOP_LEVEL_KEYS)):
        raise ValueError("worker_protocol_audit_invalid")
    digest = value.get("raw_line_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("worker_protocol_audit_invalid")
    sequence = value.get("sequence_no")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or not 1 <= sequence <= _MAX_PROTOCOL_SEQUENCE:
        raise ValueError("worker_protocol_audit_invalid")
    if value.get("fsm_state") not in _PROTOCOL_STATES or value.get("elapsed_bucket") not in _ELAPSED_BUCKETS:
        raise ValueError("worker_protocol_audit_invalid")
    if value.get("error_container_kind") not in _PROTOCOL_ERROR_KINDS:
        raise ValueError("worker_protocol_audit_invalid")
    error_mask = value.get("error_known_keys")
    error_count = value.get("error_field_count")
    if not isinstance(error_mask, int) or isinstance(error_mask, bool) or not 0 <= error_mask < (1 << len(_PROTOCOL_ERROR_KEYS)):
        raise ValueError("worker_protocol_audit_invalid")
    if not isinstance(error_count, int) or isinstance(error_count, bool) or not 0 <= error_count <= 16:
        raise ValueError("worker_protocol_audit_invalid")
    if value["error_container_kind"] != "object" and (error_mask != 0 or error_count != 0):
        raise ValueError("worker_protocol_audit_invalid")
    if value["error_container_kind"] == "object" and error_mask.bit_count() > error_count:
        raise ValueError("worker_protocol_audit_invalid")


@dataclass(frozen=True)
class CodexWorkerResult:
    exit_code: int | None
    error_code: str
    primary_error_code: str
    cleanup_error_code: str
    pid: int | None
    process_start_identity: str | None
    stdout_sha256: str
    stderr_sha256: str
    event_count: int
    final_response: dict[str, object] | None
    final_response_sha256: str
    final_response_validated: bool
    untrusted_final_response: bool
    # Digest of the parsed response re-encoded with the Harness canonical
    # JSON codec.  This is distinct from the raw JSONL response digest.
    canonical_final_response_sha256: str = ""
    protocol_rejection: ProtocolRejectionAudit | None = None


@dataclass
class _Anchor:
    fd: int
    path: Path
    identity: tuple[int, int, int, int, int]


@dataclass
class _ExecutableBinding:
    anchor: _Anchor
    sha256: str


class CodexCliWorker:
    """A fixed-role Codex process boundary; callers cannot supply argv or a binary."""

    def __init__(
        self,
        *,
        process_factory: Callable[..., Any] = subprocess.Popen,
        process_identity_reader: Callable[[int], str | None] | None = None,
        process_group_reader: Callable[[int], int] = os.getpgid,
        process_group_signaler: Callable[[int, str], None] | None = None,
        process_group_exists: Callable[[int], bool] | None = None,
        executable_preflight: Callable[[float], _ExecutableBinding | None] | None = None,
        executable_revalidator: Callable[[_ExecutableBinding], None] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self._process_factory = process_factory
        self._process_identity_reader = process_identity_reader or _read_process_start_identity
        self._process_group_reader = process_group_reader
        self._process_group_signaler = process_group_signaler or _signal_group
        self._process_group_exists = process_group_exists or _group_exists
        self._executable_preflight = executable_preflight or _preflight_executable
        self._executable_revalidator = executable_revalidator or _revalidate_executable
        self._clock = monotonic_clock
        self._cancel_check = cancel_check or (lambda: False)

    def start(self, request: CodexWorkerRequest, sink: WorkerEventSink) -> CodexWorkerResult:
        deadline = self._clock() + _timeout(request)
        worktree: _Anchor | None = None
        schema: _Anchor | None = None
        image_anchors: list[_Anchor] = []
        executable: _ExecutableBinding | None = None
        process: Any | None = None
        primary = ""
        cleanup = CleanupOutcome(True, False, "")
        pid: int | None = None
        identity: str | None = None
        stdout_hash = hashlib.sha256()
        stderr_hash = hashlib.sha256()
        delivered = 0
        final_response: dict[str, object] | None = None
        final_hash = ""
        protocol_rejection: list[ProtocolRejectionAudit] = []
        try:
            executable = self._executable_preflight(deadline)
            worktree = _open_directory_anchor(request.worktree_path)
            prompt = _prompt_bytes(request)
            image_anchors = [
                _open_absolute(path, directory=False, trusted_root=None)
                for path in request.image_paths
            ]
            if request.role is WorkerRole.REVIEWER:
                schema = _open_schema_anchor(request)
            _check_deadline_or_cancel(deadline, self._cancel_check, self._clock)
            cwd = _canonical_anchor_path(worktree)
            argv = _fixed_argv(
                request,
                cwd,
                _fd_path(schema.fd) if schema else None,
                tuple(_fd_path(anchor.fd) for anchor in image_anchors),
            )
            _revalidate_path_anchor(worktree)
            if schema is not None:
                _revalidate_path_anchor(schema)
            for anchor in image_anchors:
                _revalidate_path_anchor(anchor)
            if executable is not None:
                self._executable_revalidator(executable)
            process = self._process_factory(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                shell=False,
                start_new_session=True,
                close_fds=True,
                pass_fds=(tuple([schema.fd]) if schema is not None else ()) + tuple(
                    anchor.fd for anchor in image_anchors
                ),
                env=_minimal_environment(),
            )
            pid = _pid(process)
            if self._process_group_reader(pid) != pid:
                raise _WorkerPhaseError("worker_process_group_invalid")
            _revalidate_path_anchor(worktree)
            if schema is not None:
                _revalidate_path_anchor(schema)
            for anchor in image_anchors:
                _revalidate_path_anchor(anchor)
            if executable is not None:
                self._executable_revalidator(executable)
            identity = _required_identity(self._process_identity_reader(pid))
            try:
                sink.on_started(pid, identity)
            except Exception:
                raise _WorkerPhaseError("worker_started_sink_failed")
            _write_prompt(process.stdin, prompt, deadline, self._clock, self._cancel_check)
            final_response, final_hash, delivered, primary = _drain_events(
                process,
                request.role,
                sink,
                deadline,
                self._clock,
                self._cancel_check,
                stdout_hash,
                stderr_hash,
                delivered,
                protocol_rejection,
            )
            if not primary:
                exit_code = _wait_until(process, deadline, self._clock)
                if exit_code is None:
                    primary = "worker_timeout"
                elif exit_code != 0:
                    primary = "worker_process_failed"
        except _WorkerPhaseError as error:
            primary = error.code
        except ValueError:
            primary = "worker_request_invalid" if process is None else "worker_protocol_invalid"
        except (OSError, subprocess.SubprocessError):
            primary = "worker_spawn_failed" if process is None else "worker_stream_failed"
        except Exception:
            primary = "worker_internal_error"
        finally:
            if process is not None:
                cleanup = _cleanup_process_group(
                    process,
                    pid or 0,
                    primary_error=primary,
                    deadline=deadline,
                    clock=self._clock,
                    signaler=self._process_group_signaler,
                    group_exists=self._process_group_exists,
                )
            _close_anchor(schema)
            _close_anchor(worktree)
            for anchor in image_anchors:
                _close_anchor(anchor)
            if executable is not None:
                _close_anchor(executable.anchor)
        if cleanup.error_code:
            if not primary:
                primary = "worker_cleanup_failed"
        return _result(
            process,
            primary,
            cleanup.error_code,
            pid,
            identity,
            stdout_hash,
            stderr_hash,
            delivered,
            final_response,
            final_hash,
            protocol_rejection[0] if protocol_rejection else None,
        )


class _WorkerPhaseError(Exception):
    def __init__(self, code: str, rejection_audit: ProtocolRejectionAudit | None = None) -> None:
        self.code = code
        self.rejection_audit = rejection_audit


def _timeout(request: CodexWorkerRequest) -> int:
    if not isinstance(request, CodexWorkerRequest) or not isinstance(request.role, WorkerRole):
        _invalid()
    if not isinstance(request.timeout_seconds, int) or isinstance(request.timeout_seconds, bool):
        _invalid()
    if not 1 <= request.timeout_seconds <= _MAX_TIMEOUT_SECONDS:
        _invalid()
    return request.timeout_seconds


def _prompt_bytes(request: CodexWorkerRequest) -> bytes:
    if not isinstance(request.prompt, str) or "\x00" in request.prompt:
        _invalid()
    try:
        raw = request.prompt.encode("utf-8")
    except UnicodeEncodeError:
        _invalid()
    if not raw or len(raw) > MAX_PROMPT_BYTES or contains_sensitive_text(request.prompt):
        _invalid()
    if request.role is WorkerRole.WORKER:
        if request.output_schema_path is not None or request.expected_schema_sha256 is not None:
            _invalid()
    elif request.role is WorkerRole.REVIEWER:
        if not isinstance(request.output_schema_path, Path) or not _sha256_text(request.expected_schema_sha256):
            _invalid()
    else:
        _invalid()
    _validate_image_request(request)
    return raw


def _fixed_argv(
    request: CodexWorkerRequest,
    cwd: str,
    schema: str | None,
    image_paths: tuple[str, ...] = (),
) -> list[str]:
    argv = [
        os.fspath(CODEX_EXECUTABLE), "exec", "--json", "--ephemeral", "--ignore-user-config",
    ]
    if request.role is WorkerRole.WORKER:
        # Bundled codex 0.147 defines --approve-for-me as the workspace-write
        # automatic-review policy and rejects an additional --sandbox flag.
        argv.append("--approve-for-me")
    else:
        argv.extend(("--sandbox", "read-only"))
        if schema is None:
            _invalid()
    if request.skip_git_repo_check:
        argv.append("--skip-git-repo-check")
    for image_path in image_paths:
        argv.extend(("--image", image_path))
    # The fixed schema remains anchored and hash-verified locally, then the
    # returned reviewer JSON is parsed with the stricter Harness contract.
    # Do not delegate schema enforcement to the bundled provider path: it can
    # terminate before returning a response even when local validation would
    # safely reject malformed output.
    if schema is not None and request.role is WorkerRole.WORKER:
        argv.extend(("--output-schema", schema))
    argv.extend(("--cd", cwd, "-"))
    return argv


def _validate_image_request(request: CodexWorkerRequest) -> None:
    if (
        not isinstance(request.image_paths, tuple)
        or len(request.image_paths) > 4
        or any(
            not isinstance(path, Path)
            or not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 20 * 1024 * 1024
            for path in request.image_paths
        )
        or not isinstance(request.visual_only, bool)
        or not isinstance(request.skip_git_repo_check, bool)
        or request.visual_only
        and (
            request.role is not WorkerRole.REVIEWER
            or not request.image_paths
            or not request.skip_git_repo_check
        )
    ):
        _invalid()
    if request.skip_git_repo_check and request.role is not WorkerRole.REVIEWER:
        _invalid()


def _minimal_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TERM": "dumb",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _open_directory_anchor(path: object) -> _Anchor:
    if not isinstance(path, Path) or not path.is_absolute():
        _invalid()
    return _open_absolute(path, directory=True, trusted_root=None)


def _open_schema_anchor(request: CodexWorkerRequest) -> _Anchor:
    assert request.output_schema_path is not None
    root = _open_directory_anchor(HARNESS_SCHEMA_ROOT)
    try:
        _revalidate_path_anchor(root)
        schema = _open_absolute(request.output_schema_path, directory=False, trusted_root=root)
    finally:
        _close_anchor(root)
    try:
        raw = _read_bounded_fd(schema.fd, _MAX_SCHEMA_BYTES)
        if hashlib.sha256(raw).hexdigest() != request.expected_schema_sha256:
            _invalid()
        parsed = _strict_json(raw)
        _validate_json_shape(parsed)
        if not isinstance(parsed, dict):
            _invalid()
        _validate_output_schema_compatibility(parsed)
        os.lseek(schema.fd, 0, os.SEEK_SET)
        return schema
    except Exception:
        _close_anchor(schema)
        raise


_OUTPUT_SCHEMA_KEYWORDS = frozenset({
    "additionalProperties", "const", "enum", "items", "properties", "required", "type",
})


def _validate_output_schema_compatibility(schema: object) -> None:
    """Reject schema features unsupported by the structured-output provider."""

    pending = [schema]
    while pending:
        value = pending.pop()
        if not isinstance(value, dict) or any(key not in _OUTPUT_SCHEMA_KEYWORDS for key in value):
            _invalid()
        properties = value.get("properties", {})
        if not isinstance(properties, dict):
            _invalid()
        pending.extend(properties.values())
        if "items" in value:
            pending.append(value["items"])


def _open_absolute(path: Path, *, directory: bool, trusted_root: _Anchor | None) -> _Anchor:
    try:
        if trusted_root is not None:
            relative = path.relative_to(trusted_root.path)
            components = relative.parts
            root_fd = os.dup(trusted_root.fd)
        else:
            components = path.parts[1:]
            root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if not components or any(part in {"", ".", ".."} for part in components):
            raise OSError("invalid path")
        current_fd = root_fd
        try:
            for index, component in enumerate(components):
                last = index == len(components) - 1
                flags = os.O_RDONLY | os.O_NOFOLLOW
                if not last or directory:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(component, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            metadata = os.fstat(current_fd)
            if directory != stat.S_ISDIR(metadata.st_mode) or (not directory and not stat.S_ISREG(metadata.st_mode)):
                raise OSError("unexpected file type")
            return _Anchor(current_fd, path, _identity(metadata))
        except Exception:
            os.close(current_fd)
            raise
    except OSError:
        _invalid()


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
    )


def _revalidate_anchor(anchor: _Anchor) -> None:
    if _identity(os.fstat(anchor.fd)) != anchor.identity:
        raise _WorkerPhaseError("worker_anchor_changed")


def _canonical_anchor_path(anchor: _Anchor) -> str:
    resolved = Path(os.path.realpath(os.fspath(anchor.path)))
    if resolved != anchor.path:
        raise _WorkerPhaseError("worker_anchor_changed")
    return os.fspath(resolved)


def _revalidate_path_anchor(anchor: _Anchor) -> None:
    _revalidate_anchor(anchor)
    try:
        metadata = os.lstat(anchor.path)
    except OSError:
        raise _WorkerPhaseError("worker_anchor_changed") from None
    if _identity(metadata) != anchor.identity:
        raise _WorkerPhaseError("worker_anchor_changed")


def _close_anchor(anchor: _Anchor | None) -> None:
    if anchor is not None:
        try:
            os.close(anchor.fd)
        except OSError:
            pass


def _fd_path(fd: int) -> str:
    return f"/dev/fd/{fd}"


def _read_bounded_fd(fd: int, maximum: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(fd, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if not raw or len(raw) > maximum:
        _invalid()
    return raw


def _strict_json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _invalid()


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _no_constant(_: str) -> None:
    _invalid()


def _validate_json_shape(value: object) -> None:
    pending = [(value, 0)]
    nodes = fields = list_items = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _invalid()
        if isinstance(item, dict):
            fields += len(item)
            if fields > _MAX_JSON_FIELDS:
                _invalid()
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > _MAX_JSON_KEY_CHARS:
                    _invalid()
                pending.append((child, depth + 1))
        elif isinstance(item, list):
            list_items += len(item)
            if list_items > _MAX_JSON_LIST_ITEMS:
                _invalid()
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > _MAX_JSON_SCALAR_CHARS:
                _invalid()
        elif isinstance(item, float):
            if not math.isfinite(item):
                _invalid()
        elif not isinstance(item, (int, float, bool, type(None))):
            _invalid()


def _write_prompt(stream: Any, data: bytes, deadline: float, clock: Callable[[], float], cancel: Callable[[], bool]) -> None:
    if stream is None:
        raise _WorkerPhaseError("worker_stdin_failed")
    try:
        if hasattr(stream, "write_chunk"):
            offset = 0
            while offset < len(data):
                _check_deadline_or_cancel(deadline, cancel, clock)
                written = stream.write_chunk(data[offset : offset + 16_384])
                if written is None:
                    if clock() >= deadline:
                        raise _WorkerPhaseError("worker_timeout")
                    continue
                if not isinstance(written, int) or written <= 0:
                    raise _WorkerPhaseError("worker_stdin_failed")
                offset += written
            return
        fd = stream.fileno()
        os.set_blocking(fd, False)
        selector = selectors.DefaultSelector()
        try:
            selector.register(fd, selectors.EVENT_WRITE)
            offset = 0
            while offset < len(data):
                _check_deadline_or_cancel(deadline, cancel, clock)
                timeout = max(0.0, min(0.05, deadline - clock()))
                if not selector.select(timeout):
                    continue
                try:
                    offset += os.write(fd, data[offset : offset + 16_384])
                except BlockingIOError:
                    continue
        finally:
            selector.close()
    finally:
        try:
            stream.close()
        except Exception:
            raise _WorkerPhaseError("worker_stdin_failed") from None


def _drain_events(
    process: Any,
    role: WorkerRole,
    sink: WorkerEventSink,
    deadline: float,
    clock: Callable[[], float],
    cancel: Callable[[], bool],
    stdout_hash: Any,
    stderr_hash: Any,
    delivered: int,
    protocol_rejection: list[ProtocolRejectionAudit] | None = None,
) -> tuple[dict[str, object] | None, str, int, str]:
    protocol = _Protocol(role)
    streams = {"stdout": process.stdout, "stderr": process.stderr}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    open_channels = set(streams)
    started_at = clock()
    last_heartbeat = started_at
    heartbeats = 0
    heartbeat_limit = min(
        _MAX_HEARTBEATS,
        max(1, int((deadline - started_at) / _HEARTBEAT_SECONDS) + 1),
    )
    cli_event_count = 0
    selector = _stream_selector(streams)
    try:
        while open_channels:
            _check_deadline_or_cancel(deadline, cancel, clock)
            now = clock()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                heartbeats += 1
                if heartbeats > heartbeat_limit:
                    return None, "", delivered, "worker_heartbeat_overflow"
                try:
                    sink.on_event({"type": "worker.heartbeat", "sequence_no": delivered + 1})
                except Exception:
                    return None, "", delivered, "worker_event_sink_failed"
                delivered += 1
                last_heartbeat = now
            reads = _read_streams(streams, open_channels, selector)
            if not reads:
                if _poll(process) is not None:
                    return None, "", delivered, "worker_stream_unclosed"
                continue
            observed = False
            for channel, chunk in reads:
                if chunk is None:
                    continue
                observed = True
                if chunk == b"":
                    open_channels.discard(channel)
                    continue
                target_hash = stdout_hash if channel == "stdout" else stderr_hash
                target_hash.update(chunk)
                totals[channel] += len(chunk)
                buffers[channel].extend(chunk)
                if totals["stdout"] + totals["stderr"] > MAX_OUTPUT_BYTES:
                    return None, "", delivered, "worker_output_too_large"
                while b"\n" in buffers[channel]:
                    raw_line, _, remaining = buffers[channel].partition(b"\n")
                    buffers[channel] = bytearray(remaining)
                    if len(raw_line) > MAX_OUTPUT_LINE_BYTES:
                        return None, "", delivered, "worker_output_too_large"
                    if channel == "stderr":
                        continue
                    if cli_event_count >= MAX_EVENT_COUNT:
                        return None, "", delivered, "worker_event_overflow"
                    try:
                        safe, final = protocol.accept(raw_line, delivered + 1, elapsed_seconds=max(0.0, clock() - started_at))
                    except _WorkerPhaseError as error:
                        if error.rejection_audit is not None and protocol_rejection is not None:
                            protocol_rejection.append(error.rejection_audit)
                        return None, "", delivered, error.code
                    try:
                        sink.on_event(safe)
                    except Exception:
                        return None, "", delivered, "worker_event_sink_failed"
                    cli_event_count += 1
                    delivered += 1
                    if final is not None:
                        final_response = final
                if len(buffers[channel]) > MAX_OUTPUT_LINE_BYTES:
                    return None, "", delivered, "worker_output_too_large"
            if not observed and _poll(process) is not None:
                return None, "", delivered, "worker_stream_unclosed"
        if any(buffers.values()):
            return None, "", delivered, "worker_output_invalid"
        try:
            final_response, final_hash = protocol.finish()
        except _WorkerPhaseError as error:
            return None, "", delivered, error.code
        return final_response, final_hash, delivered, ""
    finally:
        if selector is not None:
            selector.close()


def _classify_protocol_error_container(event: dict[str, object]) -> tuple[str, int, int]:
    if "error" not in event:
        return "missing", 0, 0
    error = event.get("error")
    if error is None:
        return "null", 0, 0
    if isinstance(error, str):
        return "string", 0, 0
    if not isinstance(error, dict):
        return "other", 0, 0
    mask = sum(1 << index for index, key in enumerate(_PROTOCOL_ERROR_KEYS) if key in error)
    return "object", mask, min(len(error), 16)


class _Protocol:
    def __init__(self, role: WorkerRole) -> None:
        self.role = role
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.turn_started = False
        self.terminal = False
        self.reviewer_text: bytes | None = None

    def accept(self, raw_line: bytes, sequence_no: int, *, elapsed_seconds: float = 0.0) -> tuple[dict[str, object], dict[str, object] | None]:
        event = _strict_json(raw_line)
        _validate_json_shape(event)
        if not isinstance(event, dict):
            raise _WorkerPhaseError("worker_protocol_invalid")
        try:
            return self._accept_event(event, raw_line, sequence_no)
        except _WorkerPhaseError as error:
            if error.code in {"worker_protocol_invalid", "worker_protocol_failed"}:
                raise _WorkerPhaseError(error.code, self._rejection_audit(event, raw_line, sequence_no, elapsed_seconds)) from None
            raise

    def _accept_event(self, event: dict[str, object], raw_line: bytes, sequence_no: int) -> tuple[dict[str, object], dict[str, object] | None]:
        if self.terminal:
            raise _WorkerPhaseError("worker_protocol_invalid")
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise _WorkerPhaseError("worker_protocol_invalid")
        if event_type == "turn.failed":
            raise _WorkerPhaseError("worker_protocol_failed")
        if event_type == "error":
            if self.thread_id is None or not self.turn_started:
                raise _WorkerPhaseError("worker_protocol_invalid")
            # Bundled Codex can emit bounded recoverable transport errors and
            # then complete the same turn.  Persist no untrusted error detail;
            # a missing terminal or explicit turn.failed still fails closed.
            safe = {"type": event_type}
        elif event_type == "thread.started":
            if self.thread_id is not None or self.turn_started:
                raise _WorkerPhaseError("worker_protocol_invalid")
            self.thread_id = _protocol_identity(event.get("thread_id"))
            safe = {"type": event_type}
        elif event_type == "turn.started":
            self._matching_available_ids(event)
            if self.turn_started:
                raise _WorkerPhaseError("worker_protocol_invalid")
            self.turn_started = True
            self._capture_optional_turn_id(event)
            safe = {"type": event_type}
        elif event_type in {"item.started", "item.updated", "item.completed"}:
            if self.thread_id is None or not self.turn_started:
                raise _WorkerPhaseError("worker_protocol_invalid")
            item = event.get("item")
            if not isinstance(item, dict):
                raise _WorkerPhaseError("worker_protocol_invalid")
            item_type = item.get("type")
            if item_type not in {
                "agent_message", "reasoning", "command_execution", "file_change",
                "mcp_tool_call", "web_search", "todo_list", "error",
            }:
                raise _WorkerPhaseError("worker_protocol_invalid")
            safe = {"type": event_type, "item_type": item_type}
            item_id = item.get("id")
            if item_id is not None:
                safe["item_id"] = _safe_scalar(item_id)
            if self.role is WorkerRole.REVIEWER and event_type == "item.completed" and item_type == "agent_message":
                text = item.get("text")
                if not isinstance(text, str) or _sensitive_public(text):
                    raise _WorkerPhaseError("worker_reviewer_response_invalid")
                encoded = text.encode("utf-8")
                if not encoded or len(encoded) > _MAX_JSON_SCALAR_CHARS:
                    raise _WorkerPhaseError("worker_reviewer_response_invalid")
                self.reviewer_text = encoded
        elif event_type == "turn.completed":
            self._matching_available_ids(event)
            if self.thread_id is None or not self.turn_started:
                raise _WorkerPhaseError("worker_protocol_invalid")
            self.terminal = True
            safe = {"type": event_type}
        else:
            raise _WorkerPhaseError("worker_protocol_invalid")
        safe["sequence_no"] = sequence_no
        safe["raw_line_sha256"] = hashlib.sha256(raw_line).hexdigest()
        return safe, None

    def _rejection_audit(self, event: dict[str, object], raw_line: bytes, sequence_no: int, elapsed_seconds: float) -> ProtocolRejectionAudit:
        item = event.get("item")
        item_type = item.get("type") if isinstance(item, dict) else None
        mask = sum(1 << index for index, key in enumerate(_PROTOCOL_TOP_LEVEL_KEYS) if key in event)
        state = "terminal" if self.terminal else "turn_active" if self.turn_started else "thread_started" if self.thread_id is not None else "initial"
        error_kind, error_mask, error_count = _classify_protocol_error_container(event)
        audit = ProtocolRejectionAudit(
            _classify_protocol_candidate(event.get("type"), present="type" in event, known=_KNOWN_PROTOCOL_EVENT_TYPES, unknown="unknown_event_type"),
            _classify_protocol_candidate(item_type, present=isinstance(item, dict) and "type" in item, known=_KNOWN_PROTOCOL_ITEM_TYPES, unknown="unknown_item_type"),
            mask,
            hashlib.sha256(raw_line).hexdigest(),
            sequence_no,
            state,
            _elapsed_bucket(elapsed_seconds),
            error_kind,
            error_mask,
            error_count,
        )
        audit.as_mapping()
        return audit
    def _matching_available_ids(self, event: dict[str, object]) -> None:
        if self.thread_id is None:
            raise _WorkerPhaseError("worker_protocol_invalid")
        if "thread_id" in event and _protocol_identity(event.get("thread_id")) != self.thread_id:
            raise _WorkerPhaseError("worker_protocol_invalid")
        if "turn_id" in event:
            candidate = _protocol_identity(event.get("turn_id"))
            if self.turn_id is not None and candidate != self.turn_id:
                raise _WorkerPhaseError("worker_protocol_invalid")

    def _capture_optional_turn_id(self, event: dict[str, object]) -> None:
        if "turn_id" in event:
            self.turn_id = _protocol_identity(event.get("turn_id"))

    def finish(self) -> tuple[dict[str, object] | None, str]:
        if self.thread_id is None or not self.turn_started or not self.terminal:
            raise _WorkerPhaseError("worker_protocol_invalid")
        if self.role is WorkerRole.WORKER:
            return None, ""
        if self.reviewer_text is None:
            raise _WorkerPhaseError("worker_reviewer_response_invalid")
        try:
            response = _strict_json(self.reviewer_text)
            _validate_json_shape(response)
        except ValueError:
            raise _WorkerPhaseError("worker_reviewer_response_invalid") from None
        if not isinstance(response, dict):
            raise _WorkerPhaseError("worker_reviewer_response_invalid")
        if _contains_sensitive_json(response):
            raise _WorkerPhaseError("worker_reviewer_response_invalid")
        return response, hashlib.sha256(self.reviewer_text).hexdigest()


def _safe_scalar(value: object) -> str:
    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None or _sensitive_public(value):
        raise _WorkerPhaseError("worker_protocol_invalid")
    return value


def _protocol_identity(value: object) -> str:
    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise _WorkerPhaseError("worker_protocol_invalid")
    if _sensitive_public(value) and re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
    ) is None:
        raise _WorkerPhaseError("worker_protocol_invalid")
    return value


def _classify_protocol_candidate(value: object, *, present: bool, known: frozenset[str], unknown: str) -> str:
    if not present:
        return "missing"
    if not isinstance(value, str):
        return "non_string"
    if len(value) > 64:
        return "too_long"
    if _sensitive_public(value):
        return "sensitive"
    if _PROTOCOL_CANDIDATE.fullmatch(value) is None:
        return "invalid_chars"
    return value if value in known else unknown


def _elapsed_bucket(value: float) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise _WorkerPhaseError("worker_protocol_invalid")
    if value < 10:
        return "under_10s"
    if value < 60:
        return "10_59s"
    if value < 180:
        return "60_179s"
    if value <= 360:
        return "180_360s"
    return "over_360s"


def _sensitive_public(value: str) -> bool:
    normalized = normalize_sensitive_text(value)
    return contains_sensitive_text(normalized) or bool(_PUBLIC_SECRET.search(normalized))


def _contains_sensitive_json(value: object) -> bool:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if _sensitive_public(item):
                return True
        elif isinstance(item, dict):
            for key, child in item.items():
                if _sensitive_public(key):
                    return True
                pending.append(child)
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _stream_selector(streams: dict[str, Any]) -> selectors.BaseSelector | None:
    if all(hasattr(stream, "read_chunk") for stream in streams.values()):
        return None
    selector = selectors.DefaultSelector()
    try:
        for channel, stream in streams.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, channel)
        return selector
    except Exception:
        selector.close()
        raise


def _read_streams(streams: dict[str, Any], open_channels: set[str], selector: selectors.BaseSelector | None) -> list[tuple[str, bytes | None]]:
    if selector is None:
        return [(channel, streams[channel].read_chunk(16_384)) for channel in sorted(open_channels)]
    result: list[tuple[str, bytes | None]] = []
    for key, _ in selector.select(timeout=0.05):
        try:
            result.append((str(key.data), os.read(key.fileobj.fileno(), 16_384)))
        except BlockingIOError:
            continue
    return result


def _check_deadline_or_cancel(
    deadline: float,
    cancel: Callable[[], bool],
    clock: Callable[[], float] = time.monotonic,
) -> None:
    if cancel():
        raise _WorkerPhaseError("worker_cancelled")
    if clock() >= deadline:
        raise _WorkerPhaseError("worker_timeout")


def _wait_until(
    process: Any, deadline: float, clock: Callable[[], float] = time.monotonic
) -> int | None:
    remaining = deadline - clock()
    if remaining <= 0:
        return None
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        return None


def _cleanup_process_group(
    process: Any,
    pgid: int,
    *,
    primary_error: str,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    signaler: Callable[[int, str], None] = None,
    group_exists: Callable[[int], bool] = None,
) -> CleanupOutcome:
    signaler = signaler or _signal_group
    group_exists = group_exists or _group_exists
    leader_reaped = False
    cleanup_error = ""
    try:
        # A clean EOF is allowed to finish normally.  We only kill a group after
        # a phase error, a deadline expiry, or a leader that is still running.
        if not primary_error and _poll(process) is None:
            if _wait_until(process, deadline, clock) is None:
                primary_error = "worker_timeout"
        should_terminate = bool(primary_error) or _poll(process) is None
        if should_terminate:
            signaler(pgid, "TERM")
            grace = min(deadline, clock() + 0.2)
            while clock() < grace and group_exists(pgid):
                time.sleep(0.01)
            if group_exists(pgid):
                signaler(pgid, "KILL")
        # Reap the captured leader before checking the group.  On POSIX a dead
        # leader may remain visible to killpg(0) as a zombie until it is reaped.
        try:
            process.wait(timeout=max(0.01, deadline - clock()))
            leader_reaped = True
        except Exception:
            # `Popen.poll()` itself reaps a completed leader.  Some test and
            # platform wrappers reject a second wait after that has happened.
            if _poll(process) is not None:
                leader_reaped = True
            else:
                cleanup_error = "worker_cleanup_reap_failed"
        # A leader can exit while a child still holds a stdio fd.  Re-check only
        # after leader reaping, then terminate that remaining captured group.
        if group_exists(pgid):
            signaler(pgid, "TERM")
            grace = min(deadline, clock() + 0.2)
            while clock() < grace and group_exists(pgid):
                time.sleep(0.01)
            if group_exists(pgid):
                signaler(pgid, "KILL")
        while group_exists(pgid) and clock() < deadline:
            time.sleep(0.01)
        if group_exists(pgid):
            cleanup_error = cleanup_error or "worker_cleanup_group_alive"
    except Exception:
        cleanup_error = cleanup_error or "worker_cleanup_failed"
    finally:
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    cleanup_error = cleanup_error or "worker_cleanup_close_failed"
    try:
        group_extinct = not group_exists(pgid)
    except Exception:
        group_extinct = False
        cleanup_error = cleanup_error or "worker_cleanup_group_check_failed"
    return CleanupOutcome(group_extinct, leader_reaped, cleanup_error)


def _signal_group(pgid: int, kind: str) -> None:
    os.killpg(pgid, signal.SIGTERM if kind == "TERM" else signal.SIGKILL)


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _pid(process: Any) -> int:
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise _WorkerPhaseError("worker_process_invalid")
    return pid


def _poll(process: Any) -> int | None:
    try:
        value = process.poll()
    except Exception:
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _required_identity(value: str | None) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise _WorkerPhaseError("worker_identity_unavailable")
    return value


def _read_process_start_identity(pid: int) -> str | None:
    try:
        from app.local_agent_repository import _read_process_start_identity as reader
        return reader(pid)
    except Exception:
        return None


def _preflight_executable(deadline: float) -> _ExecutableBinding:
    anchor = _open_absolute(CODEX_EXECUTABLE, directory=False, trusted_root=None)
    try:
        metadata = os.fstat(anchor.fd)
        if metadata.st_uid not in {0, os.getuid()} or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise _WorkerPhaseError("worker_executable_invalid")
        if not _is_macho(anchor.fd):
            raise _WorkerPhaseError("worker_executable_invalid")
        digest = _sha256_fd(anchor.fd)
        _verify_codesign(deadline)
        _revalidate_path_anchor(anchor)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _WorkerPhaseError("worker_timeout")
        result = subprocess.run(
            [os.fspath(CODEX_EXECUTABLE), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=remaining,
            env=_minimal_environment(),
            check=False,
        )
        _revalidate_path_anchor(anchor)
        if _sha256_fd(anchor.fd) != digest:
            raise _WorkerPhaseError("worker_executable_changed")
        match = re.fullmatch(r"codex-cli\s+(\d+)\.(\d+)\.(\d+)(?:[-+].*)?\s*", result.stdout)
        version = tuple(int(part) for part in match.groups()) if match else None
        if result.returncode != 0 or version is None or not (_SUPPORTED_VERSION[0] <= version < _SUPPORTED_VERSION[1]):
            raise _WorkerPhaseError("worker_executable_unsupported")
        return _ExecutableBinding(anchor, digest)
    except Exception:
        _close_anchor(anchor)
        raise


def _revalidate_executable(binding: _ExecutableBinding) -> None:
    _revalidate_path_anchor(binding.anchor)
    if _sha256_fd(binding.anchor.fd) != binding.sha256:
        raise _WorkerPhaseError("worker_executable_changed")


def _is_macho(fd: int) -> bool:
    os.lseek(fd, 0, os.SEEK_SET)
    magic = os.read(fd, 4)
    return magic in {
        b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    }


def _sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, 1_048_576)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def _verify_codesign(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _WorkerPhaseError("worker_timeout")
    common = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "timeout": min(remaining, 2.0),
        "env": _minimal_environment(),
        "check": False,
    }
    verified = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", os.fspath(CODEX_EXECUTABLE)],
        **common,
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _WorkerPhaseError("worker_timeout")
    details = subprocess.run(
        ["/usr/bin/codesign", "-dv", "--verbose=4", os.fspath(CODEX_EXECUTABLE)],
        **{**common, "timeout": min(remaining, 2.0)},
    )
    detail_text = f"{details.stdout}\n{details.stderr}"
    if verified.returncode != 0 or details.returncode != 0 or f"TeamIdentifier={_CODE_SIGN_TEAM_ID}" not in detail_text:
        raise _WorkerPhaseError("worker_executable_invalid")


def _sha256_text(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _result(process: Any | None, primary: str, cleanup: str, pid: int | None, identity: str | None, stdout_hash: Any, stderr_hash: Any, count: int, response: dict[str, object] | None, response_hash: str, protocol_rejection: ProtocolRejectionAudit | None = None) -> CodexWorkerResult:
    error = primary or ("worker_cleanup_failed" if cleanup else "")
    canonical_response_hash = ""
    if response is not None:
        canonical = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        canonical_response_hash = hashlib.sha256(canonical).hexdigest()
    return CodexWorkerResult(
        _poll(process), error, primary, cleanup, pid, identity,
        stdout_hash.hexdigest(), stderr_hash.hexdigest(), count, response,
        response_hash, False, response is not None, canonical_response_hash, protocol_rejection,
    )


def _invalid() -> None:
    raise ValueError("worker_request_invalid")
