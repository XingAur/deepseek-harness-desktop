#!/usr/bin/env python3
"""Read-only local Git inspection capability entrypoint."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import ctypes
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence


REQUEST_FIELDS = frozenset((
    "schema_version", "request_id", "capability", "provider", "mode",
    "mutation_level", "authorization", "input", "context",
))
READ_ONLY_GIT_COMMANDS = frozenset({
    "rev-parse", "status", "diff", "show", "log", "branch", "remote", "ls-files", "config",
})
_OPERATION_MARKERS = (
    ("merge", "MERGE_HEAD"),
    ("cherry_pick", "CHERRY_PICK_HEAD"),
    ("revert", "REVERT_HEAD"),
    ("rebase_merge", "rebase-merge"),
    ("rebase_apply", "rebase-apply"),
)
_EXTERNAL_FILTER_QUERY = (
    "config", "--includes", "--null", "--name-only", "--get-regexp",
    r"^filter\..*\.(clean|process)$",
)
_TRUSTED_GIT_OPTIONS = (
    "--no-pager",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "diff.external=",
    "-c", "pager.status=false",
    "-c", "pager.config=false",
    "-c", "pager.branch=false",
    "-c", "pager.remote=false",
)
_ALLOWED_GIT_ARGUMENTS = frozenset({
    ("rev-parse", "--show-toplevel"),
    ("branch", "--show-current"),
    ("rev-parse", "--verify", "HEAD"),
    ("remote",),
    ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all"),
    _EXTERNAL_FILTER_QUERY,
    *(("rev-parse", "--git-path", marker) for _, marker in _OPERATION_MARKERS),
})
_ORCHESTRATED_CODE_EVIDENCE_CAPABILITIES = frozenset({
    "git.diff",
    "source.read",
    "source.search",
    "git.history",
    "verification.run-local",
    "code.review-local",
})
_APPLY_INPUT_FIELDS = frozenset(("project_path", "expected_diff", "allowed_paths", "verify_commands"))
_PROHIBITED_FILE_NAMES = frozenset({
    ".env", ".env.local", ".env.production", ".envrc", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "cargo.lock", "pipfile.lock", "poetry.lock", "gemfile.lock", "composer.lock", ".npmrc", ".pypirc",
    "id_rsa", "id_dsa", "id_ed25519",
})
_PROHIBITED_PREFIXES = ("config", ".ssh")
_SENSITIVE_COMPONENT_TOKENS = frozenset({"secret", "secrets", "token", "tokens", "credential", "credentials"})
_SENSITIVE_FILE_NAMES = frozenset({"application-prod.yml", "application-prod.yaml"})
_SENSITIVE_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"})
_DEPENDENCY_METADATA_MAX_ENTRIES = 100_000
_DEPENDENCY_METADATA_TIMEOUT_SECONDS = 10.0
# macOS <sys/clonefile.h>: do not traverse any symlink and do not copy privileged ownership.
_CLONE_NOOWNERCOPY = 0x0002
_CLONE_NOFOLLOW_ANY = 0x0008


def _validate_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise ValueError("invalid capability request")
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("capability") != "git.inspect"
        or payload.get("provider") != "his-engineering"
        or payload.get("mode") != "preview"
        or payload.get("mutation_level") != "L0"
    ):
        raise ValueError("invalid capability request")
    if not isinstance(payload.get("request_id"), str) or not payload["request_id"].strip():
        raise ValueError("invalid capability request")
    if payload.get("authorization") != {"explicit": False, "scope": []}:
        raise ValueError("invalid capability request")
    if payload.get("context") != {}:
        raise ValueError("invalid capability request")
    input_data = payload.get("input")
    if not isinstance(input_data, dict) or set(input_data) != {"project_path"}:
        raise ValueError("invalid capability request")
    project_path = input_data.get("project_path")
    if not isinstance(project_path, str) or not project_path or not Path(project_path).is_absolute():
        raise ValueError("invalid capability request")
    return payload


def _result(
    request: Mapping[str, Any],
    *,
    status: str,
    summary: str,
    data: Mapping[str, Any],
    warnings: list[str],
    blockers: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request["request_id"],
        "capability": "git.inspect",
        "provider": "his-engineering",
        "status": status,
        "mutation_level": "L0",
        "changed": False,
        "summary": summary,
        "data": dict(data),
        "evidence": [],
        "warnings": list(warnings),
        "blockers": list(blockers),
        "audit": {
            "credential_class": "none",
            "external_write_attempted": False,
            "repository_mutation_attempted": False,
        },
    }


def _run_git(project_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one of the fixed inspection argv shapes, never a general Git command."""
    argv = tuple(args)
    if not argv or argv[0] not in READ_ONLY_GIT_COMMANDS or argv not in _ALLOWED_GIT_ARGUMENTS:
        raise ValueError("unsupported git inspection command")
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(
        ["git", "-C", str(project_path), *_TRUSTED_GIT_OPTIONS, *args],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
        shell=False,
        env=environment,
    )


def _parse_porcelain_z(raw: str) -> list[dict[str, str]]:
    tokens = raw.split("\0")
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token or len(token) < 4:
            continue
        status = token[:2]
        entry = {"status": status, "path": token[3:]}
        if "R" in status or "C" in status:
            if index < len(tokens) and tokens[index]:
                entry["source_path"] = tokens[index]
                index += 1
        entries.append(entry)
    return entries


def _operation_markers(project_path: Path) -> Optional[List[str]]:
    active: list[str] = []
    for operation, marker in _OPERATION_MARKERS:
        result = _run_git(project_path, ["rev-parse", "--git-path", marker])
        if result.returncode != 0:
            return None
        candidate = Path(result.stdout.strip())
        marker_path = candidate if candidate.is_absolute() else project_path / candidate
        if marker_path.exists():
            active.append(operation)
    return active


def _external_filters_configured(project_path: Path) -> Optional[bool]:
    result = _run_git(project_path, list(_EXTERNAL_FILTER_QUERY))
    if result.returncode not in {0, 1}:
        return None
    return bool(result.stdout.strip("\0"))


def _unsupported(request: Mapping[str, Any], blocker: str) -> dict[str, Any]:
    return _result(
        request,
        status="blocked",
        summary="GIT_INSPECT_UNSUPPORTED",
        data={"classification": "unsupported"},
        warnings=[],
        blockers=[blocker],
    )


def _execute_inspect_request(request: object) -> dict[str, Any]:
    """Inspect an absolute local project path without changing its repository state."""
    checked = _validate_request(request)
    project_path = Path(checked["input"]["project_path"])
    if not project_path.is_dir():
        return _unsupported(checked, "project_path_missing")
    project_path = project_path.resolve()
    try:
        root_result = _run_git(project_path, ["rev-parse", "--show-toplevel"])
    except (OSError, subprocess.SubprocessError):
        return _result(
            checked, status="failed", summary="GIT_INSPECT_FAILED", data={"classification": "failed"},
            warnings=[], blockers=["git_inspection_unavailable"],
        )
    if root_result.returncode != 0:
        return _unsupported(checked, "not_git_repository")

    try:
        external_filters = _external_filters_configured(project_path)
        if external_filters:
            return _unsupported(checked, "external_filter_configured")
        repository_root = str(Path(root_result.stdout.strip()).resolve())
        branch_result = _run_git(project_path, ["branch", "--show-current"])
        head_result = _run_git(project_path, ["rev-parse", "--verify", "HEAD"])
        remote_result = _run_git(project_path, ["remote"])
        status_result = _run_git(
            project_path,
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all"],
        )
        markers = _operation_markers(project_path)
    except (OSError, subprocess.SubprocessError):
        return _result(
            checked, status="failed", summary="GIT_INSPECT_FAILED", data={"classification": "failed"},
            warnings=[], blockers=["git_inspection_unavailable"],
        )
    if (
        branch_result.returncode != 0
        or external_filters is None
        or remote_result.returncode != 0
        or status_result.returncode != 0
        or markers is None
    ):
        return _result(
            checked, status="failed", summary="GIT_INSPECT_FAILED", data={"classification": "failed"},
            warnings=[], blockers=["git_inspection_failed"],
        )

    entries = _parse_porcelain_z(status_result.stdout)
    warnings: list[str] = []
    head = ""
    if head_result.returncode == 0:
        head = head_result.stdout.strip()
    else:
        warnings.append("unborn_repository")
    renamed = [entry for entry in entries if "R" in entry["status"]]
    data = {
        "classification": "supported",
        "repository_root": repository_root,
        "branch": branch_result.stdout.strip(),
        "head": head,
        "remote_names": [line for line in remote_result.stdout.splitlines() if line],
        "status_entries": entries,
        "untracked_paths": [entry["path"] for entry in entries if entry["status"] == "??"],
        "worktree_modified_paths": [
            entry["path"] for entry in entries if entry["status"][1] not in {" ", "?"}
        ],
        "staged_paths": [entry["path"] for entry in entries if entry["status"][0] not in {" ", "?"}],
        "renamed_paths": renamed,
        "operation_markers": markers,
    }
    return _result(
        checked, status="success", summary="GIT_INSPECT_OK", data=data,
        warnings=warnings, blockers=[],
    )


def _validate_apply_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise ValueError("invalid capability request")
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("capability") != "git.apply-local"
        or payload.get("provider") != "his-engineering"
        or payload.get("mode") != "apply"
        or payload.get("mutation_level") != "L2"
        or payload.get("authorization") != {"explicit": True, "scope": ["repository:apply-local"]}
        or payload.get("context") != {}
        or not isinstance(payload.get("request_id"), str)
        or not payload["request_id"].strip()
    ):
        raise ValueError("invalid capability request")
    input_data = payload.get("input")
    if not isinstance(input_data, dict) or set(input_data) != _APPLY_INPUT_FIELDS:
        raise ValueError("invalid capability request")
    project_path = input_data.get("project_path")
    expected_diff = input_data.get("expected_diff")
    allowed_paths = input_data.get("allowed_paths")
    verify_commands = input_data.get("verify_commands")
    if (
        not isinstance(project_path, str) or not project_path or not Path(project_path).is_absolute()
        or not isinstance(expected_diff, str) or not expected_diff or not isinstance(allowed_paths, list)
        or not isinstance(verify_commands, list)
    ):
        raise ValueError("invalid capability request")
    _validate_allowed_paths(allowed_paths)
    _validate_expected_diff(expected_diff, allowed_paths)
    _validate_verify_commands(verify_commands, allowed_paths=allowed_paths)
    return payload


def _validate_allowed_paths(paths: list[object]) -> None:
    if not paths or not all(isinstance(path, str) for path in paths):
        raise ValueError("invalid capability request")
    if len(paths) != len(set(paths)):
        raise ValueError("invalid capability request")
    for path in paths:
        if not isinstance(path, str) or _unsafe_relative_path(path):
            raise ValueError("invalid capability request")


def _unsafe_relative_path(path: str) -> bool:
    if not path or path != path.strip() or "\\" in path or any(ord(char) < 32 for char in path):
        return True
    candidate = Path(path)
    if candidate.is_absolute() or "." in candidate.parts or ".." in candidate.parts or ".git" in candidate.parts:
        return True
    normalized = candidate.as_posix()
    if normalized != path or any(part == "" for part in candidate.parts):
        return True
    lowered = normalized.lower()
    file_name = candidate.name.lower()
    if file_name in _PROHIBITED_FILE_NAMES or file_name in _SENSITIVE_FILE_NAMES or file_name.startswith(".env."):
        return True
    if Path(file_name).suffix in _SENSITIVE_SUFFIXES:
        return True
    if any(lowered == prefix or lowered.startswith(prefix + "/") for prefix in _PROHIBITED_PREFIXES):
        return True
    for component in candidate.parts:
        tokens = [token for token in re.split(r"[^a-z0-9]+", component.lower()) if token]
        if any(token in _SENSITIVE_COMPONENT_TOKENS for token in tokens):
            return True
        if {"private", "key"}.issubset(tokens) or {"api", "key"}.issubset(tokens):
            return True
    return False


def _safe_project_path(path: Path) -> bool:
    """Permit only absolute paths whose symlink ancestry is system-owned /var or /tmp."""
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        return False
    trusted_system_symlinks = {Path("/var"), Path("/tmp")}
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink() and current not in trusted_system_symlinks:
            return False
    return True


def _patch_paths(expected_diff: str) -> list[str]:
    paths: list[str] = []
    for line in expected_diff.splitlines():
        if line.startswith("diff --git a/"):
            paths.append(line.split(" ")[2][2:])
    return paths


def _validate_expected_diff(expected_diff: str, allowed_paths: list[object]) -> None:
    if not expected_diff.endswith("\n") or "\x00" in expected_diff or "\r" in expected_diff:
        raise ValueError("invalid capability request")
    prohibited = (
        "GIT binary patch", "Binary files ", "new file mode", "deleted file mode", "old mode ", "new mode ",
        "similarity index", "rename from ", "rename to ", "copy from ", "copy to ", "Subproject commit",
    )
    if any(marker in expected_diff for marker in prohibited):
        raise ValueError("invalid capability request")
    lines = expected_diff.splitlines()
    index = 0
    parsed: list[str] = []
    while index < len(lines):
        if not lines[index].startswith("diff --git a/"):
            raise ValueError("invalid capability request")
        header = lines[index].split(" ")
        if len(header) != 4 or not header[2].startswith("a/") or not header[3].startswith("b/"):
            raise ValueError("invalid capability request")
        source, destination = header[2][2:], header[3][2:]
        if source != destination or _unsafe_relative_path(source):
            raise ValueError("invalid capability request")
        index += 1
        if index >= len(lines) or not re.fullmatch(r"index [0-9a-f]{40,64}\.\.[0-9a-f]{40,64} 100644", lines[index]):
            raise ValueError("invalid capability request")
        index += 1
        if index + 1 >= len(lines) or lines[index] != f"--- a/{source}" or lines[index + 1] != f"+++ b/{source}":
            raise ValueError("invalid capability request")
        index += 2
        hunk_count = 0
        while index < len(lines) and not lines[index].startswith("diff --git "):
            if lines[index].startswith("@@ "):
                hunk_count += 1
            index += 1
        if hunk_count == 0:
            raise ValueError("invalid capability request")
        parsed.append(source)
    if len(parsed) != len(set(parsed)):
        raise ValueError("invalid capability request")


def _validate_verify_commands(commands: list[object], *, allowed_paths: Sequence[object] | None = None) -> None:
    if not commands or not all(isinstance(command, str) for command in commands):
        raise ValueError("invalid capability request")
    if len(commands) != len(set(commands)):
        raise ValueError("invalid capability request")
    for command in commands:
        if not isinstance(command, str) or not command or command != command.strip() or any(ord(char) < 32 for char in command):
            raise ValueError("invalid capability request")
        if any(token in command for token in (";", "|", "&", ">", "<", "$", "`", "(", ")", "\\")):
            raise ValueError("invalid capability request")
        try:
            argv = tuple(shlex.split(command, posix=True))
        except ValueError as exc:
            raise ValueError("invalid capability request") from exc
        if any(part.startswith("/") or ".." in Path(part).parts for part in argv):
            raise ValueError("invalid capability request")
        if not _allowed_verify_argv(argv, allowed_paths):
            raise ValueError("invalid capability request")
    if len(_verification_ecosystems(commands)) > 1:
        raise ValueError("invalid capability request")


def _allowed_verify_argv(argv: tuple[str, ...], allowed_paths: Sequence[object] | None) -> bool:
    fixed = {
        ("python3", "-m", "unittest"), ("true",), ("false",), ("npm", "test"), ("npm", "run", "lint"),
        ("npm", "run", "build"), ("yarn", "test"), ("yarn", "lint"), ("yarn", "build"),
        ("mvn", "test"), ("./gradlew", "test"),
    }
    if argv in fixed:
        return True
    if len(argv) == 3 and argv[:2] == ("node", "--check"):
        return _safe_verify_path(argv[2], allowed_paths, require_allowed=True)
    if len(argv) == 2 and argv[0] == "node":
        return argv[1].endswith(".mjs") and _safe_verify_path(argv[1], allowed_paths, require_allowed=False)
    if len(argv) == 4 and argv[:3] == ("./gradlew", "test", "--tests"):
        return bool(re.fullmatch(r"[A-Za-z0-9_.$*?]+", argv[3]))
    if len(argv) >= 4 and argv[:3] == ("./node_modules/.bin/vue-cli-service", "lint", "--no-fix"):
        return all(_safe_verify_path(target, allowed_paths, require_allowed=True) for target in argv[3:])
    return False


def _safe_verify_path(path: str, allowed_paths: Sequence[object] | None, *, require_allowed: bool) -> bool:
    return (
        not _unsafe_relative_path(path)
        and "*" not in path and "?" not in path
        and (not require_allowed or allowed_paths is None or path in allowed_paths)
    )


def _verify_command_targets_are_tracked(project_path: Path, commands: Sequence[str]) -> bool:
    for command in commands:
        argv = tuple(shlex.split(command, posix=True))
        target = ""
        if len(argv) == 3 and argv[:2] == ("node", "--check"):
            target = argv[2]
        elif len(argv) == 2 and argv[0] == "node":
            target = argv[1]
        if target:
            try:
                tracked = _run_apply_git(project_path, "tracked", (target,))
            except (OSError, subprocess.SubprocessError, ValueError):
                return False
            if tracked.returncode != 0 or not _safe_regular_target(project_path, target):
                return False
    return True


def _apply_result(
    request: Mapping[str, Any], *, status: str, summary: str, changed: bool,
    data: Mapping[str, Any], blockers: Sequence[str], repository_mutation_attempted: bool,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request["request_id"],
        "capability": "git.apply-local",
        "provider": "his-engineering",
        "status": status,
        "mutation_level": "L2",
        "changed": changed,
        "summary": summary,
        "data": dict(data),
        "evidence": [],
        "warnings": list(warnings),
        "blockers": list(blockers),
        "audit": {
            "credential_class": "none",
            "external_write_attempted": False,
            "repository_mutation_attempted": repository_mutation_attempted,
            "verification_isolation": dict(data.get("verification_isolation") or {"enforced": False, "network": "not_run"}),
        },
    }


def _apply_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _run_apply_git(project_path: Path, action: str, values: Sequence[str] = (), input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    fixed: dict[str, list[str]] = {
        "root": ["rev-parse", "--show-toplevel"],
        "branch": ["branch", "--show-current"],
        "head": ["rev-parse", "--verify", "HEAD"],
        "git_dir": ["rev-parse", "--git-dir"],
        "status": ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all"],
        "refs": ["show-ref"],
        "config": ["config", "--includes", "--null", "--list", "--show-origin"],
        "remote": ["remote"],
        "worktree_list": ["worktree", "list", "--porcelain"],
        "filters": ["config", "--includes", "--null", "--name-only", "--get-regexp", r"^filter\..*\.(clean|process|smudge)$"],
    }
    if action in fixed:
        args = fixed[action]
    elif action == "marker" and len(values) == 1 and values[0] in {marker for _, marker in _OPERATION_MARKERS}:
        args = ["rev-parse", "--git-path", values[0]]
    elif action == "tracked" and values and all(not _unsafe_relative_path(value) for value in values):
        args = ["ls-files", "--error-unmatch", "--", *values]
    elif action == "diff" and values and all(not _unsafe_relative_path(value) for value in values):
        args = ["diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-renames", "--", *values]
    elif action in {"apply_check", "apply", "reverse_check", "reverse"} and not values:
        suffix = ["apply", "--recount", "-"]
        if action in {"apply_check", "reverse_check"}:
            suffix.insert(2, "--check")
        if action in {"reverse_check", "reverse"}:
            suffix.insert(2, "--reverse")
        args = suffix
    elif action == "worktree_add" and len(values) == 2:
        target, head = values
        if not Path(target).is_absolute() or not re.fullmatch(r"[0-9a-f]{40,64}", head):
            raise ValueError("unsupported git apply command")
        args = ["worktree", "add", "--detach", target, head]
    elif action == "worktree_remove" and len(values) == 1 and Path(values[0]).is_absolute():
        args = ["worktree", "remove", "--force", values[0]]
    else:
        raise ValueError("unsupported git apply command")
    return subprocess.run(
        ["git", "-C", str(project_path), *_TRUSTED_GIT_OPTIONS, *args],
        text=True, input=input_text, capture_output=True, timeout=60, check=False, shell=False, env=_apply_environment(),
    )


def _operation_markers_apply(project_path: Path) -> list[str] | None:
    active: list[str] = []
    for name, marker in _OPERATION_MARKERS:
        result = _run_apply_git(project_path, "marker", (marker,))
        if result.returncode != 0:
            return None
        candidate = Path(result.stdout.strip())
        marker_path = candidate if candidate.is_absolute() else project_path / candidate
        if marker_path.exists():
            active.append(name)
    return active


def _snapshot_repository(
    project_path: Path, allowed_paths: Sequence[str], *, allow_allowed_dirty: bool = False,
) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        root = _run_apply_git(project_path, "root")
        if root.returncode != 0:
            return None, ["not_git_repository"]
        root_path = Path(root.stdout.strip()).resolve()
        if root_path != project_path or not (project_path / ".git").is_dir():
            return None, ["project_path_not_git_root_or_linked_worktree"]
        branch = _run_apply_git(project_path, "branch")
        head = _run_apply_git(project_path, "head")
        filters = _run_apply_git(project_path, "filters")
        markers = _operation_markers_apply(project_path)
        if branch.returncode != 0 or head.returncode != 0 or filters.returncode not in {0, 1} or markers is None:
            return None, ["repository_preflight_failed"]
        if not branch.stdout.strip() or not head.stdout.strip():
            return None, ["unborn_or_detached_repository"]
        if filters.stdout.strip("\0"):
            return None, ["external_filter_configured"]
        if markers:
            return None, ["git_operation_in_progress"]
        status = _run_apply_git(project_path, "status")
        refs = _run_apply_git(project_path, "refs")
        config = _run_apply_git(project_path, "config")
        remotes = _run_apply_git(project_path, "remote")
        if any(item.returncode != 0 for item in (status, refs, config, remotes)):
            return None, ["repository_snapshot_failed"]
        entries = _parse_porcelain_z(status.stdout)
        changed = {entry["path"] for entry in entries if entry["path"]}
        changed.update(entry["source_path"] for entry in entries if entry.get("source_path"))
        dirty_allowed = sorted(path for path in allowed_paths if path in changed)
        if dirty_allowed and not allow_allowed_dirty:
            return None, ["allowed_path_already_dirty"]
        file_state: dict[str, tuple[str, int]] = {}
        for relative in allowed_paths:
            target = project_path / relative
            tracked = _run_apply_git(project_path, "tracked", (relative,))
            if tracked.returncode != 0 or not _safe_regular_target(project_path, relative):
                return None, ["allowed_path_not_tracked_regular_file"]
            info = target.stat()
            if not stat.S_ISREG(info.st_mode):
                return None, ["allowed_path_not_tracked_regular_file"]
            file_state[relative] = (hashlib.sha256(target.read_bytes()).hexdigest(), stat.S_IMODE(info.st_mode))
        return {
            "head": head.stdout.strip(), "branch": branch.stdout.strip(), "status": status.stdout,
            "refs": refs.stdout, "config_digest": hashlib.sha256(config.stdout.encode()).hexdigest(),
            "remotes": tuple(line for line in remotes.stdout.splitlines() if line), "markers": tuple(markers),
            "files": file_state,
            "unrelated_state": {
                path: _path_fingerprint(project_path / path)
                for path in sorted(path for path in changed if path not in allowed_paths)
            },
        }, []
    except (OSError, subprocess.SubprocessError):
        return None, ["repository_preflight_failed"]


def _snapshot_matches(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return dict(before) == dict(after)


def _safe_regular_target(project_path: Path, relative: str) -> bool:
    current = project_path
    for part in Path(relative).parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
    target = project_path / relative
    try:
        info = target.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _path_fingerprint(path: Path) -> tuple[str, str, int]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return ("missing", "", 0)
    if stat.S_ISLNK(info.st_mode):
        return ("symlink", os.readlink(path), stat.S_IMODE(info.st_mode))
    if stat.S_ISREG(info.st_mode):
        return ("file", hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(info.st_mode))
    if stat.S_ISDIR(info.st_mode):
        return ("directory", _directory_digest(path), stat.S_IMODE(info.st_mode))
    return ("other", "", stat.S_IMODE(info.st_mode))


def _directory_digest(root: Path) -> str:
    """A bounded metadata manifest; it never opens dependency file content."""
    return _bounded_tree_manifest(root, allow_internal_symlinks=True) or "unreadable_or_bounded"


def _bounded_tree_manifest(root: Path, *, allow_internal_symlinks: bool) -> str | None:
    digest = hashlib.sha256()
    deadline = time.monotonic() + _DEPENDENCY_METADATA_TIMEOUT_SECONDS
    entries = 0
    try:
        for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
            directories.sort()
            filenames.sort()
            base = Path(current)
            for name in [*directories, *filenames]:
                entries += 1
                if entries > _DEPENDENCY_METADATA_MAX_ENTRIES or time.monotonic() > deadline:
                    return None
                candidate = base / name
                info = candidate.lstat()
                relative = candidate.relative_to(root).as_posix().encode("utf-8", "surrogateescape")
                digest.update(
                    relative + b"\0" + str(stat.S_IMODE(info.st_mode)).encode() + b"\0"
                    + str(info.st_size).encode() + b"\0" + str(info.st_mtime_ns).encode() + b"\0"
                )
                if stat.S_ISLNK(info.st_mode):
                    if not allow_internal_symlinks:
                        return None
                    resolved = candidate.resolve(strict=True)
                    resolved.relative_to(root)
                    digest.update(b"symlink\0" + str(resolved.relative_to(root)).encode("utf-8", "surrogateescape"))
                elif stat.S_ISREG(info.st_mode):
                    digest.update(b"file\0")
                elif stat.S_ISDIR(info.st_mode):
                    digest.update(b"directory\0")
                else:
                    return None
    except (OSError, ValueError):
        return None
    return digest.hexdigest()


def _current_exact_diff(project_path: Path, allowed_paths: Sequence[str]) -> str | None:
    try:
        result = _run_apply_git(project_path, "diff", tuple(allowed_paths))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return result.stdout if result.returncode == 0 else None


def _sandbox_path_literal(path: Path) -> str:
    value = str(path.resolve())
    if not value or any(ord(char) < 32 for char in value):
        raise ValueError("invalid sandbox path")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _sandbox_profile(worktree_path: Path, scratch_path: Path) -> str:
    worktree = _sandbox_path_literal(worktree_path)
    scratch = _sandbox_path_literal(scratch_path)
    return "\n".join((
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow file-read*)",
        f'(allow file-write* (subpath "{worktree}") (subpath "{scratch}"))',
        "(deny network*)",
    ))


def _verification_environment(
    scratch_path: Path, dependency_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = _apply_environment()
    scratch = str(scratch_path.resolve())
    environment.update({
        "HOME": scratch,
        "TMPDIR": scratch,
        "TMP": scratch,
        "TEMP": scratch,
        "XDG_CACHE_HOME": scratch,
        "XDG_CONFIG_HOME": scratch,
        "XDG_DATA_HOME": scratch,
    })
    if dependency_environment:
        for name in ("MAVEN_OPTS", "GRADLE_USER_HOME"):
            if name in dependency_environment:
                environment[name] = dependency_environment[name]
    return environment


def _requires_node_projection(commands: Sequence[str]) -> bool:
    for command in commands:
        argv = tuple(shlex.split(command, posix=True))
        if argv and argv[0] in {"npm", "yarn", "./node_modules/.bin/vue-cli-service"}:
            return True
    return False


def _verification_ecosystems(commands: Sequence[str]) -> set[str]:
    ecosystems: set[str] = set()
    for command in commands:
        argv = tuple(shlex.split(command, posix=True))
        if not argv or argv[0] in {"true", "false"}:
            continue
        if argv[0] in {"node", "npm", "yarn", "./node_modules/.bin/vue-cli-service"}:
            ecosystems.add("node")
        elif argv[0] == "mvn":
            ecosystems.add("maven")
        elif argv[0] == "./gradlew":
            ecosystems.add("gradle")
        elif argv[0] == "python3":
            ecosystems.add("python")
    return ecosystems


def _requires_maven_projection(commands: Sequence[str]) -> bool:
    return any(tuple(shlex.split(command, posix=True))[:2] == ("mvn", "test") for command in commands)


def _requires_gradle_projection(commands: Sequence[str]) -> bool:
    return any(tuple(shlex.split(command, posix=True))[:2] == ("./gradlew", "test") for command in commands)


def _safe_directory(path: Path) -> Path | None:
    """Return a canonical directory only when no path component is a symlink."""
    if not path.is_absolute():
        return None
    try:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return None
        return path.resolve(strict=True)
    except OSError:
        return None


def _clonefile_callable() -> Any | None:
    """Load the Darwin clonefile syscall wrapper without exposing a general copy API."""
    if sys.platform != "darwin":
        return None
    try:
        function = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).clonefile
        function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
        function.restype = ctypes.c_int
        return function
    except (AttributeError, OSError):
        return None


def _clone_fixed_cache_to_scratch(source: Path, destination: Path) -> bool:
    """Use only clonefile COW; ENOTSUP/EXDEV/EEXIST and all other errors fail closed."""
    try:
        clonefile = _clonefile_callable()
        if clonefile is None or destination.exists() or destination.is_symlink():
            return False
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if source.stat().st_dev != destination.parent.stat().st_dev:
            return False
        ctypes.set_errno(0)
        result = clonefile(
            os.fsencode(source), os.fsencode(destination), _CLONE_NOFOLLOW_ANY | _CLONE_NOOWNERCOPY,
        )
        return result == 0 and destination.is_dir() and not destination.is_symlink()
    except OSError:
        return False


def _remove_scratch_projection(scratch: Path, target: Path) -> bool:
    try:
        scratch_root = scratch.resolve(strict=True)
        candidate = target.resolve(strict=False)
        candidate.relative_to(scratch_root)
        if candidate.exists() or candidate.is_symlink():
            shutil.rmtree(candidate)
        return not candidate.exists() and not candidate.is_symlink()
    except (OSError, ValueError):
        return False


def _node_projection_target_is_safe(worktree: Path, source: Path, commands: Sequence[str]) -> bool:
    if not any(command.startswith("./node_modules/.bin/vue-cli-service ") for command in commands):
        return True
    target = worktree / "node_modules" / ".bin" / "vue-cli-service"
    try:
        resolved = target.resolve(strict=True)
        return resolved.is_relative_to(source) and stat.S_ISREG(resolved.stat().st_mode)
    except OSError:
        return False


def _cache_home() -> Path:
    return Path.home()


def _prepare_dependency_projection(
    *, source_project: Path, validation_project: Path, scratch_path: Path, verify_commands: Sequence[str],
) -> tuple[bool, str, dict[str, object], dict[str, str]]:
    """Project only explicit, safe dependency trees into an isolated validation run."""
    proof: dict[str, object] = {"strategy": "none", "available": True, "cleaned": True, "source_unchanged": True}
    environment: dict[str, str] = {}
    try:
        scratch_root = scratch_path.resolve(strict=True)
    except OSError:
        proof.update({"available": False, "source_unchanged": False})
        return False, "verification_dependencies_unavailable", proof, environment
    if _requires_node_projection(verify_commands):
        source = _safe_directory(source_project.resolve() / "node_modules")
        target = validation_project / "node_modules"
        if source is None or target.exists() or target.is_symlink():
            proof.update({"available": False, "source_unchanged": False})
            return False, "verification_dependencies_unavailable", proof, environment
        before = _bounded_tree_manifest(source, allow_internal_symlinks=True)
        if before is None:
            proof.update({"available": False, "source_unchanged": False})
            return False, "verification_dependencies_unavailable", proof, environment
        try:
            target.symlink_to(source, target_is_directory=True)
        except OSError:
            proof.update({"available": False, "source_unchanged": _bounded_tree_manifest(source, allow_internal_symlinks=True) == before})
            return False, "verification_dependencies_unavailable", proof, environment
        if target.resolve() != source or not _node_projection_target_is_safe(validation_project, source, verify_commands):
            target.unlink(missing_ok=True)
            proof.update({"available": False, "source_unchanged": _bounded_tree_manifest(source, allow_internal_symlinks=True) == before})
            return False, "verification_dependencies_unavailable", proof, environment
        proof.update({"strategy": "node_modules_symlink", "source": str(source), "source_manifest_before": before})
        return True, "", proof, environment
    if _requires_maven_projection(verify_commands):
        source = _safe_directory(_cache_home().resolve() / ".m2" / "repository")
        target = scratch_root / "m2" / "repository"
        if source is None:
            proof.update({"available": False, "source_unchanged": False})
            return False, "verification_dependencies_unavailable", proof, environment
        before = _bounded_tree_manifest(source, allow_internal_symlinks=False)
        if before is None:
            proof.update({"available": False, "source_unchanged": False})
            return False, "verification_dependencies_unavailable", proof, environment
        if not _clone_fixed_cache_to_scratch(source, target):
            source_unchanged = _bounded_tree_manifest(source, allow_internal_symlinks=False) == before
            if not _remove_scratch_projection(scratch_path, scratch_path / "m2"):
                proof.update({"available": False, "cleaned": False, "source_unchanged": source_unchanged})
                return False, "verification_dependency_projection_cleanup_failed", proof, environment
            proof.update({"available": False, "source_unchanged": source_unchanged})
            return False, "verification_dependencies_unavailable", proof, environment
        proof.update({"strategy": "maven_clone_on_write", "source_manifest_before": before})
        environment["MAVEN_OPTS"] = f"-Dmaven.repo.local={target}"
        return True, "", proof, environment
    if _requires_gradle_projection(verify_commands):
        home = _cache_home().resolve()
        sources = ((_safe_directory(home / ".gradle" / "caches"), "caches"), (_safe_directory(home / ".gradle" / "wrapper" / "dists"), "wrapper/dists"))
        if any(source is None for source, _ in sources):
            proof.update({"available": False, "source_unchanged": False})
            return False, "verification_dependencies_unavailable", proof, environment
        before = {target: _bounded_tree_manifest(source, allow_internal_symlinks=False) for source, target in sources if source is not None}
        if any(manifest is None for manifest in before.values()):
            proof.update({"available": False, "source_unchanged": False})
            return False, "verification_dependencies_unavailable", proof, environment
        for source, relative in sources:
            assert source is not None
            if not _clone_fixed_cache_to_scratch(source, scratch_root / "gradle" / relative):
                source_unchanged = all(
                    _bounded_tree_manifest(item, allow_internal_symlinks=False) == before[name]
                    for item, name in sources if item is not None
                )
                if not _remove_scratch_projection(scratch_path, scratch_path / "gradle"):
                    proof.update({"available": False, "cleaned": False, "source_unchanged": source_unchanged})
                    return False, "verification_dependency_projection_cleanup_failed", proof, environment
                proof.update({"available": False, "source_unchanged": source_unchanged})
                return False, "verification_dependencies_unavailable", proof, environment
        proof.update({"strategy": "gradle_clone_on_write", "source_manifests_before": before})
        environment["GRADLE_USER_HOME"] = str(scratch_root / "gradle")
        return True, "", proof, environment
    return True, "", proof, environment


def _cleanup_dependency_projection(
    *, validation_project: Path, scratch_path: Path, proof: dict[str, object], verify_commands: Sequence[str],
) -> bool:
    strategy = proof.get("strategy")
    source_unchanged = True
    cleaned = True
    if strategy == "node_modules_symlink":
        target = validation_project / "node_modules"
        source = Path(str(proof["source"]))
        try:
            if not target.is_symlink() or target.resolve() != source:
                cleaned = False
            else:
                target.unlink()
        except OSError:
            cleaned = False
        source_unchanged = _bounded_tree_manifest(source, allow_internal_symlinks=True) == proof.get("source_manifest_before")
    elif strategy == "maven_clone_on_write":
        source = _safe_directory(_cache_home().resolve() / ".m2" / "repository")
        cleaned = _remove_scratch_projection(scratch_path, scratch_path / "m2")
        source_unchanged = source is not None and _bounded_tree_manifest(source, allow_internal_symlinks=False) == proof.get("source_manifest_before")
    elif strategy == "gradle_clone_on_write":
        home = _cache_home().resolve()
        sources = ((_safe_directory(home / ".gradle" / "caches"), "caches"), (_safe_directory(home / ".gradle" / "wrapper" / "dists"), "wrapper/dists"))
        cleaned = _remove_scratch_projection(scratch_path, scratch_path / "gradle")
        before = proof.get("source_manifests_before", {})
        source_unchanged = all(source is not None and _bounded_tree_manifest(source, allow_internal_symlinks=False) == before.get(relative) for source, relative in sources)
    proof["cleaned"] = cleaned
    proof["source_unchanged"] = source_unchanged
    return cleaned and source_unchanged


def _run_verification(
    *, project_path: Path, scratch_path: Path, verify_commands: Sequence[str], dependency_environment: Mapping[str, str] | None = None,
) -> tuple[bool, str, dict[str, object]]:
    proof: dict[str, object] = {"enforced": False, "engine": "sandbox-exec", "network": "denied"}
    sandbox = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not sandbox.is_file():
        return False, "verification_isolation_unavailable", proof
    try:
        profile = _sandbox_profile(project_path, scratch_path)
    except ValueError:
        return False, "verification_isolation_unavailable", proof
    for command in verify_commands:
        argv = shlex.split(command, posix=True)
        if argv and argv[0] in {"mvn", "./gradlew"}:
            argv.append("--offline")
        try:
            completed = subprocess.run(
                [str(sandbox), "-p", profile, *argv], cwd=str(project_path), text=True, capture_output=True,
                timeout=120, check=False, shell=False, env=_verification_environment(scratch_path, dependency_environment),
            )
        except (OSError, subprocess.SubprocessError):
            return False, "verification_isolation_unavailable", proof
        if completed.returncode != 0:
            return False, "verification_failed", proof
    proof["enforced"] = True
    return True, "", proof


def _temporary_validate(
    *, project_path: Path, snapshot: Mapping[str, Any], expected_diff: str,
    allowed_paths: Sequence[str], verify_commands: Sequence[str],
) -> tuple[bool, list[str], bool, bool, dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="his-engineering-apply-") as temporary_dir:
        worktree = Path(temporary_dir) / "validation"
        scratch = Path(temporary_dir) / "scratch"
        scratch.mkdir(mode=0o700)
        created = False
        temporary_mutation_attempted = False
        isolation_proof: dict[str, object] = {"enforced": False, "network": "not_run"}
        outcome: tuple[bool, list[str]] = (False, ["temporary_validation_failed"])
        try:
            temporary_mutation_attempted = True
            add = _run_apply_git(project_path, "worktree_add", (str(worktree), str(snapshot["head"])))
            if add.returncode != 0:
                outcome = (False, ["temporary_worktree_create_failed"])
            else:
                created = True
                check = _run_apply_git(worktree, "apply_check", input_text=expected_diff)
                if check.returncode != 0:
                    outcome = (False, ["temporary_apply_check_failed"])
                else:
                    applied = _run_apply_git(worktree, "apply", input_text=expected_diff)
                    if applied.returncode != 0 or _current_exact_diff(worktree, allowed_paths) != expected_diff:
                        outcome = (False, ["temporary_apply_proof_failed"])
                    else:
                        before_state = _temporary_state(worktree, allowed_paths)
                        dependencies_ready, dependency_blocker, dependency_proof, dependency_environment = _prepare_dependency_projection(
                            source_project=project_path, validation_project=worktree, scratch_path=scratch,
                            verify_commands=verify_commands,
                        )
                        isolation_proof["dependency_projection"] = dependency_proof
                        if not dependencies_ready:
                            outcome = (False, [dependency_blocker])
                        else:
                            verified, blocker, isolation_proof = _run_verification(
                                project_path=worktree, scratch_path=scratch, verify_commands=verify_commands,
                                dependency_environment=dependency_environment,
                            )
                            isolation_proof["dependency_projection"] = dependency_proof
                            projection_clean = _cleanup_dependency_projection(
                                validation_project=worktree, scratch_path=scratch, proof=dependency_proof,
                                verify_commands=verify_commands,
                            )
                            after_state = _temporary_state(worktree, allowed_paths)
                            if not projection_clean:
                                outcome = (False, ["verification_dependency_projection_integrity_failed"])
                            elif not verified:
                                outcome = (False, [blocker])
                            elif before_state is None or before_state != after_state:
                                outcome = (False, ["verification_side_effect"])
                            else:
                                outcome = (True, [])
        except (OSError, subprocess.SubprocessError, ValueError):
            outcome = (False, ["temporary_validation_failed"])
        cleanup_ok = True
        try:
            initial_listing = _run_apply_git(project_path, "worktree_list")
            registered = initial_listing.returncode == 0 and str(worktree) in initial_listing.stdout
        except (OSError, subprocess.SubprocessError, ValueError):
            registered = True
        if created or registered or worktree.exists():
            try:
                removed = _run_apply_git(project_path, "worktree_remove", (str(worktree),))
                listing = _run_apply_git(project_path, "worktree_list")
                if removed.returncode != 0 or listing.returncode != 0 or str(worktree) in listing.stdout or worktree.exists():
                    cleanup_ok = False
            except (OSError, subprocess.SubprocessError, ValueError):
                cleanup_ok = False
        if not cleanup_ok:
            outcome = (False, ["temporary_worktree_cleanup_failed"])
        return outcome[0], outcome[1], temporary_mutation_attempted, cleanup_ok, isolation_proof


def _temporary_state(project_path: Path, allowed_paths: Sequence[str]) -> tuple[str, str, str, str, str, tuple[str, ...], tuple[str, ...]] | None:
    try:
        status = _run_apply_git(project_path, "status")
        head = _run_apply_git(project_path, "head")
        refs = _run_apply_git(project_path, "refs")
        config = _run_apply_git(project_path, "config")
        remotes = _run_apply_git(project_path, "remote")
        markers = _operation_markers_apply(project_path)
        diff = _current_exact_diff(project_path, allowed_paths)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if any(item.returncode != 0 for item in (status, head, refs, config, remotes)) or markers is None or diff is None:
        return None
    return (
        status.stdout, head.stdout, refs.stdout, hashlib.sha256(config.stdout.encode()).hexdigest(), diff,
        tuple(remotes.stdout.splitlines()), tuple(markers),
    )


def _post_apply_proof(
    *, project_path: Path, before: Mapping[str, Any], expected_diff: str,
    allowed_paths: Sequence[str],
) -> tuple[bool, list[str]]:
    after, blockers = _snapshot_repository(project_path, allowed_paths, allow_allowed_dirty=True)
    if after is None:
        return False, blockers or ["post_apply_proof_failed"]
    if (
        any(before[key] != after[key] for key in ("head", "branch", "refs", "config_digest", "remotes", "markers"))
        or before["unrelated_state"] != after["unrelated_state"]
        or _current_exact_diff(project_path, allowed_paths) != expected_diff
    ):
        return False, ["post_apply_proof_failed"]
    return True, []


def _reverse_patch(project_path: Path, expected_diff: str) -> bool:
    try:
        check = _run_apply_git(project_path, "reverse_check", input_text=expected_diff)
        if check.returncode != 0:
            return False
        return _run_apply_git(project_path, "reverse", input_text=expected_diff).returncode == 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _restored_to_preflight(project_path: Path, before: Mapping[str, Any], allowed_paths: Sequence[str]) -> bool:
    after, blockers = _snapshot_repository(project_path, allowed_paths)
    return not blockers and after is not None and _snapshot_matches(before, after) and _current_exact_diff(project_path, allowed_paths) == ""


def _execute_apply_request(request: object) -> dict[str, Any]:
    checked = _validate_apply_request(request)
    raw_project = Path(checked["input"]["project_path"])
    if not _safe_project_path(raw_project):
        return _apply_result(checked, status="blocked", summary="GIT_APPLY_BLOCKED", changed=False, data={}, blockers=["project_path_not_canonical"], repository_mutation_attempted=False)
    project_path = raw_project.resolve()
    expected_diff = checked["input"]["expected_diff"]
    allowed_paths = checked["input"]["allowed_paths"]
    verify_commands = checked["input"]["verify_commands"]
    assert isinstance(expected_diff, str) and isinstance(allowed_paths, list) and isinstance(verify_commands, list)
    patch_paths = _patch_paths(expected_diff)
    if set(patch_paths) != set(allowed_paths):
        return _apply_result(checked, status="blocked", summary="GIT_APPLY_BLOCKED", changed=False, data={}, blockers=["patch_path_not_allowlisted"], repository_mutation_attempted=False)
    before, blockers = _snapshot_repository(project_path, allowed_paths)
    data = {"expected_diff_sha256": hashlib.sha256(expected_diff.encode()).hexdigest(), "temporary_worktree_cleaned": False}
    if before is None:
        return _apply_result(checked, status="blocked", summary="GIT_APPLY_BLOCKED", changed=False, data=data, blockers=blockers, repository_mutation_attempted=False)
    if not _verify_command_targets_are_tracked(project_path, verify_commands):
        return _apply_result(checked, status="blocked", summary="GIT_APPLY_BLOCKED", changed=False, data=data, blockers=["verification_target_not_tracked_regular_file"], repository_mutation_attempted=False)
    data["workspace_classification"] = "mixed_separable" if before["unrelated_state"] else "task_owned_exact"
    temporary_ok, temporary_blockers, temporary_mutation_attempted, temporary_cleanup_ok, isolation_proof = _temporary_validate(
        project_path=project_path, snapshot=before, expected_diff=expected_diff,
        allowed_paths=allowed_paths, verify_commands=verify_commands,
    )
    data["temporary_worktree_cleaned"] = temporary_cleanup_ok
    data["verification_isolation"] = isolation_proof
    data["dependency_projection"] = isolation_proof.get("dependency_projection", {
        "strategy": "none", "available": True, "cleaned": True, "source_unchanged": True,
    })
    if not temporary_ok:
        if not temporary_cleanup_ok or "temporary_worktree_cleanup_failed" in temporary_blockers:
            return _apply_result(checked, status="failed", summary="GIT_APPLY_RECOVERY_REQUIRED", changed=True, data=data, blockers=[*temporary_blockers, "recovery_required"], repository_mutation_attempted=True, warnings=("Temporary Git worktree cleanup could not be proven; inspect Git worktree registrations before any further repository action.",))
        if "verification_dependency_projection_cleanup_failed" in temporary_blockers:
            return _apply_result(checked, status="failed", summary="GIT_APPLY_RECOVERY_REQUIRED", changed=True, data=data, blockers=[*temporary_blockers, "recovery_required"], repository_mutation_attempted=True, warnings=("Dependency projection cleanup could not be proven; inspect the recorded temporary validation directory before further repository action.",))
        return _apply_result(checked, status="failed" if any(item.startswith("verification_") for item in temporary_blockers) else "blocked", summary="GIT_APPLY_TEMPORARY_VALIDATION_FAILED", changed=False, data=data, blockers=temporary_blockers, repository_mutation_attempted=temporary_mutation_attempted, warnings=("Original repository was not modified.",))
    current, drift_blockers = _snapshot_repository(project_path, allowed_paths)
    if current is None or not _snapshot_matches(before, current):
        return _apply_result(checked, status="blocked", summary="GIT_APPLY_DRIFT_DETECTED", changed=False, data=data, blockers=["repository_drift_detected", *drift_blockers], repository_mutation_attempted=temporary_mutation_attempted)
    try:
        check = _run_apply_git(project_path, "apply_check", input_text=expected_diff)
        if check.returncode != 0:
            return _apply_result(checked, status="blocked", summary="GIT_APPLY_CHECK_FAILED", changed=False, data=data, blockers=["original_apply_check_failed"], repository_mutation_attempted=temporary_mutation_attempted)
        applied = _run_apply_git(project_path, "apply", input_text=expected_diff)
    except (OSError, subprocess.SubprocessError, ValueError):
        if _restored_to_preflight(project_path, before, allowed_paths):
            return _apply_result(checked, status="failed", summary="GIT_APPLY_FAILED", changed=False, data=data, blockers=["original_apply_failed"], repository_mutation_attempted=True)
        if _reverse_patch(project_path, expected_diff) and _restored_to_preflight(project_path, before, allowed_paths):
            return _apply_result(checked, status="failed", summary="GIT_APPLY_ROLLED_BACK", changed=False, data=data, blockers=["original_apply_failed", "recovery_performed"], repository_mutation_attempted=True)
        return _apply_result(checked, status="failed", summary="GIT_APPLY_RECOVERY_REQUIRED", changed=True, data=data, blockers=["original_apply_failed", "recovery_required"], repository_mutation_attempted=True)
    if applied.returncode != 0:
        if _restored_to_preflight(project_path, before, allowed_paths):
            return _apply_result(checked, status="failed", summary="GIT_APPLY_FAILED", changed=False, data=data, blockers=["original_apply_failed"], repository_mutation_attempted=True)
        if _reverse_patch(project_path, expected_diff) and _restored_to_preflight(project_path, before, allowed_paths):
            return _apply_result(checked, status="failed", summary="GIT_APPLY_ROLLED_BACK", changed=False, data=data, blockers=["original_apply_failed", "recovery_performed"], repository_mutation_attempted=True)
        return _apply_result(checked, status="failed", summary="GIT_APPLY_RECOVERY_REQUIRED", changed=True, data=data, blockers=["original_apply_failed", "recovery_required"], repository_mutation_attempted=True)
    proven, proof_blockers = _post_apply_proof(project_path=project_path, before=before, expected_diff=expected_diff, allowed_paths=allowed_paths)
    if proven:
        return _apply_result(checked, status="success", summary="GIT_APPLY_OK", changed=True, data=data, blockers=[], repository_mutation_attempted=True, warnings=("Verification ran through the recorded sandbox-exec profile with network denied.",))
    if _reverse_patch(project_path, expected_diff) and _restored_to_preflight(project_path, before, allowed_paths):
        return _apply_result(checked, status="failed", summary="GIT_APPLY_ROLLED_BACK", changed=False, data=data, blockers=[*proof_blockers, "recovery_performed"], repository_mutation_attempted=True, warnings=("Exact reverse patch was applied after post-apply proof failed.",))
    return _apply_result(checked, status="failed", summary="GIT_APPLY_RECOVERY_REQUIRED", changed=True, data=data, blockers=[*proof_blockers, "recovery_required"], repository_mutation_attempted=True, warnings=("Do not reset or clean; inspect the expected patch and recover with an exact reverse patch.",))


def execute_request(request: object) -> dict[str, Any]:
    if isinstance(request, dict) and request.get("capability") == "git.apply-local":
        return _execute_apply_request(request)
    if (
        isinstance(request, dict)
        and request.get("capability") in _ORCHESTRATED_CODE_EVIDENCE_CAPABILITIES
    ):
        return _orchestrator_required(request)
    return _execute_inspect_request(request)


def _orchestrator_required(request: object) -> dict[str, Any]:
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise ValueError("invalid capability request")
    capability = request.get("capability")
    credential_class = (
        "codex_model_access" if capability == "code.review-local" else "none"
    )
    if (
        capability not in _ORCHESTRATED_CODE_EVIDENCE_CAPABILITIES
        or request.get("schema_version") != "his-capability-request.v1"
        or request.get("provider") != "his-engineering"
        or request.get("mode") != "preview"
        or request.get("mutation_level") != "L0"
        or request.get("authorization") != {"explicit": False, "scope": []}
        or request.get("context") != {}
        or not isinstance(request.get("request_id"), str)
        or not request["request_id"].strip()
        or not isinstance(request.get("input"), dict)
    ):
        raise ValueError("invalid capability request")
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request["request_id"],
        "capability": capability,
        "provider": "his-engineering",
        "status": "blocked",
        "mutation_level": "L0",
        "changed": False,
        "summary": "CODE_EVIDENCE_ORCHESTRATOR_REQUIRED",
        "data": {"classification": "orchestrator_required"},
        "evidence": [],
        "warnings": [],
        "blockers": ["code_evidence_orchestrator_required"],
        "audit": {
            "credential_class": credential_class,
            "external_write_attempted": False,
            "repository_mutation_attempted": False,
        },
    }


def _ensure_new_output(path: Path) -> None:
    candidate = path.absolute()
    temporary_root = Path(tempfile.gettempdir()).absolute()
    trusted_temporary_ancestors = {temporary_root, *temporary_root.parents}
    trusted_system_symlinks = {Path("/var"), Path("/tmp")}
    current = Path(candidate.anchor)
    for part in candidate.parts[1:-1]:
        current = current / part
        if (
            current.is_symlink()
            and current not in trusted_temporary_ancestors
            and current not in trusted_system_symlinks
        ) or not current.is_dir():
            raise ValueError("output unavailable")
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        info = None
    if info is not None or candidate.is_symlink():
        raise ValueError("output unavailable")


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_new_output(path)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)


def main(argv: Optional[List[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 4 or arguments[0] != "--request" or arguments[2] != "--output":
        sys.stderr.write("invalid arguments\n")
        return 2
    try:
        request_path, output_path = Path(arguments[1]), Path(arguments[3])
        request_payload = json.loads(request_path.read_text(encoding="utf-8"))
        _ensure_new_output(output_path)
        result = execute_request(request_payload)
        _write_new_json(output_path, result)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        sys.stderr.write("invalid capability request or output\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
