"""Bounded entrypoint for a desktop-hosted Harness task session.

The desktop host may provide the model and tools, but it cannot decide when a
task is ready for code changes.  This module keeps that decision on the
Harness side: the persisted understanding artifact must be complete before a
runner is even constructed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.local_agent_contract import load_local_agent_task
from app.runtime_policy import assert_local_agent_run_allowed


EXTERNAL_TASK_SCHEMA_VERSION = "harness-external-task.v1"
UNDERSTANDING_SCHEMA_VERSION = "requirement-understanding.v1"
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_AUTHORIZATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_REQUIRED_KEYS = frozenset({
    "schema_version",
    "worktree_root",
    "knowledge_home",
    "authorization_id",
})
_UNDERSTANDING_CHECKS = frozenset({
    "business_background",
    "usage_scenario",
    "target_and_boundary",
    "project_selection",
    "entry_and_call_chain",
    "conversation_alignment",
    "error_chain_closure",
    "change_and_impact_scope",
    "verification_baseline",
})


@dataclass(frozen=True)
class ExternalTaskStart:
    task_contract_path: Path
    understanding_path: Path
    worktree_root: Path
    knowledge_home: Path
    authorization_id: str
    agent_backend: str | None = None
    archive_root: Path | None = None
    selected_model_id: str | None = None


class ExternalTaskSession:
    """Validate a desktop task and construct its runner only after the gate."""

    def __init__(
        self,
        *,
        runner_factory: Callable[..., Any],
        task_loader: Callable[[Path], Any] = load_local_agent_task,
        preflight_factory: Callable[..., Any] = assert_local_agent_run_allowed,
    ) -> None:
        if not callable(runner_factory):
            raise TypeError("external_task_runner_factory_invalid")
        if not callable(task_loader) or not callable(preflight_factory):
            raise TypeError("external_task_session_invalid")
        self._runner_factory = runner_factory
        self._task_loader = task_loader
        self._preflight_factory = preflight_factory

    def start(self, value: object) -> dict[str, object]:
        prepared = self._prepare(value)
        if isinstance(prepared, dict):
            return prepared
        request, _, understanding_bytes = prepared
        return {
            "schema_version": EXTERNAL_TASK_SCHEMA_VERSION,
            "status": "accepted",
            "error_code": "",
            "understanding_sha256": _sha256(understanding_bytes),
        }

    def execute(self, value: object, *, host_handler: Callable[..., Any] | None = None) -> dict[str, object]:
        """Run one already-understood task through the existing governed runner.

        When the archived understanding is not ready yet and the desktop host
        provides a model executor, the understanding-completion phase runs
        first: it turns the archived evidence plus the selected project into
        the gated ``requirement_understanding.json`` / ``task_contract.json``,
        or reports concrete blockers back to the user.
        """

        prepared = self._prepare(value)
        if isinstance(prepared, dict):
            # pending 占位、损坏或未就绪的理解都先走一次模型补齐（可恢复内部失败），
            # 只有补齐后仍不满足门禁才把具体 blockers 交回用户。
            if (
                prepared.get("error_code") in {
                    "requirement_understanding_incomplete",
                    "requirement_understanding_invalid",
                }
                and host_handler is not None
            ):
                prepared = self._complete_understanding(value, host_handler=host_handler)
            if isinstance(prepared, dict):
                return prepared
        request, task, understanding_bytes = prepared
        try:
            preflight = self._preflight_factory(
                allow_real_agent=True,
                authorization_id=request.authorization_id,
            )
            runner = self._runner_factory(request, host_handler=host_handler)
            snapshot = runner.execute(task, preflight)
            safe_snapshot = _safe_snapshot(snapshot)
        except Exception:
            return {
                "schema_version": EXTERNAL_TASK_SCHEMA_VERSION,
                "status": "failed",
                "error_code": "external_task_execution_failed",
                "understanding_sha256": _sha256(understanding_bytes),
            }
        return {
            "schema_version": EXTERNAL_TASK_SCHEMA_VERSION,
            "status": "completed",
            "error_code": "",
            "understanding_sha256": _sha256(understanding_bytes),
            "snapshot": safe_snapshot,
        }

    def _complete_understanding(
        self,
        value: object,
        *,
        host_handler: Callable[..., Any],
    ) -> dict[str, object] | tuple[Any, Any, bytes]:
        """Fill the gated understanding with the host model, then re-prepare."""

        from app.requirement_understanding_completion import complete_task_understanding

        try:
            request = _parse_start(value)
        except ValueError:
            return _blocked("external_task_request_invalid")
        if request.archive_root is None:
            return _blocked("requirement_understanding_incomplete")
        try:
            completion = complete_task_understanding(
                package_dir=request.archive_root,
                worktree_root=request.worktree_root,
                authorization_id=request.authorization_id,
                host_execute=host_handler,
                selected_model_id=request.selected_model_id,
            )
        except Exception:
            return _blocked("requirement_understanding_completion_failed")
        if completion.get("status") != "ready":
            blocked = _blocked("requirement_understanding_incomplete")
            blockers = completion.get("blockers")
            if isinstance(blockers, list) and blockers:
                blocked["understanding_blockers"] = [str(item)[:200] for item in blockers[:20]]
            return blocked
        return self._prepare(value)

    def _prepare(self, value: object) -> tuple[ExternalTaskStart, Any, bytes] | dict[str, object]:
        try:
            request = _parse_start(value)
            understanding_bytes = _read_regular_file(request.understanding_path)
            understanding = _parse_understanding(understanding_bytes)
        except ValueError as error:
            return _blocked(str(error))

        if not _understanding_is_ready(understanding):
            return _blocked(
                "requirement_understanding_incomplete",
                understanding_sha256=_sha256(understanding_bytes),
            )
        try:
            task = self._task_loader(request.task_contract_path)
        except Exception:
            return _blocked(
                "task_contract_invalid",
                understanding_sha256=_sha256(understanding_bytes),
            )
        return request, task, understanding_bytes


def _parse_start(value: object) -> ExternalTaskStart:
    if not isinstance(value, Mapping):
        raise ValueError("external_task_request_invalid")
    optional_keys = {
        "task_contract_path", "understanding_path", "agent_backend", "archive_root",
        "selected_model_id", "yunxiao_profile_id", "gitlab_profile_id", "database_profile_id",
    }
    if set(value) - (_REQUIRED_KEYS | optional_keys) != set() or not _REQUIRED_KEYS <= set(value):
        raise ValueError("external_task_request_invalid")
    if value.get("schema_version") != EXTERNAL_TASK_SCHEMA_VERSION:
        raise ValueError("external_task_request_invalid")

    def path_value(key: str) -> Path:
        raw = value.get(key)
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError("external_task_request_invalid")
        path = Path(raw).expanduser()
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("external_task_request_invalid")
        return path

    worktree_root = path_value("worktree_root")
    knowledge_home = path_value("knowledge_home")

    authorization_id = value.get("authorization_id")
    if not isinstance(authorization_id, str) or _AUTHORIZATION_ID.fullmatch(authorization_id) is None:
        raise ValueError("external_task_request_invalid")
    agent_backend = value.get("agent_backend")
    if agent_backend is not None and (
        not isinstance(agent_backend, str)
        or re.fullmatch(r"[a-z][a-z0-9._-]{1,63}", agent_backend) is None
    ):
        raise ValueError("external_task_request_invalid")
    selected_model_id = value.get("selected_model_id")
    if selected_model_id is not None and (
        not isinstance(selected_model_id, str)
        or _MODEL_ID.fullmatch(selected_model_id) is None
    ):
        raise ValueError("external_task_request_invalid")
    archive_root = None
    if value.get("archive_root") is not None:
        archive_root = path_value("archive_root")
        if archive_root.is_symlink():
            raise ValueError("external_task_request_invalid")
    has_contract = value.get("task_contract_path") is not None
    has_understanding = value.get("understanding_path") is not None
    if has_contract != has_understanding:
        raise ValueError("external_task_request_invalid")
    if has_contract:
        task_contract_path = path_value("task_contract_path")
        understanding_path = path_value("understanding_path")
    elif archive_root is not None:
        task_contract_path = archive_root / "engineering" / "task_contract.json"
        understanding_path = archive_root / "analysis" / "requirement_understanding.json"
    else:
        raise ValueError("external_task_request_invalid")
    return ExternalTaskStart(
        task_contract_path=task_contract_path,
        understanding_path=understanding_path,
        worktree_root=worktree_root,
        knowledge_home=knowledge_home,
        authorization_id=authorization_id,
        agent_backend=agent_backend,
        archive_root=archive_root,
        selected_model_id=selected_model_id,
    )


def _read_regular_file(path: Path) -> bytes:
    try:
        item = path.stat()
        if not path.is_file() or path.is_symlink() or item.st_size <= 0 or item.st_size > 65_536:
            raise ValueError("requirement_understanding_invalid")
        return path.read_bytes()
    except (OSError, ValueError):
        raise ValueError("requirement_understanding_invalid") from None


def _parse_understanding(raw: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("requirement_understanding_invalid") from None
    if not isinstance(value, Mapping) or value.get("schema_version") != UNDERSTANDING_SCHEMA_VERSION:
        raise ValueError("requirement_understanding_invalid")
    if not isinstance(value.get("status"), str) or not isinstance(value.get("can_modify"), bool):
        raise ValueError("requirement_understanding_invalid")
    if not isinstance(value.get("checks"), list) or not isinstance(value.get("blockers"), list):
        raise ValueError("requirement_understanding_invalid")
    return value


def _understanding_is_ready(value: Mapping[str, object]) -> bool:
    if value.get("status") != "ready_for_change" or value.get("can_modify") is not True or value.get("blockers") != []:
        return False
    checks = value.get("checks")
    if not isinstance(checks, list):
        return False
    names: set[str] = set()
    for item in checks:
        if not isinstance(item, Mapping) or item.get("status") != "pass" or not isinstance(item.get("name"), str):
            return False
        names.add(item["name"])
    return names == _UNDERSTANDING_CHECKS and len(checks) == len(_UNDERSTANDING_CHECKS)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _blocked(error_code: str, *, understanding_sha256: str = "") -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": EXTERNAL_TASK_SCHEMA_VERSION,
        "status": "blocked",
        "error_code": error_code,
    }
    if understanding_sha256:
        result["understanding_sha256"] = understanding_sha256
    return result


def _safe_snapshot(value: object) -> dict[str, object]:
    """Return only the stable run projection; never send raw prompts or paths."""

    if not isinstance(value, Mapping):
        raise ValueError("external_task_snapshot_invalid")
    run = value.get("run")
    attempts = value.get("attempts")
    events = value.get("events")
    artifacts = value.get("artifacts")
    if not isinstance(run, Mapping) or not isinstance(attempts, list) or not isinstance(events, list) or not isinstance(artifacts, list):
        raise ValueError("external_task_snapshot_invalid")
    safe: dict[str, object] = {
        "run_id": run.get("id"),
        "task_key": run.get("task_key"),
        "status": run.get("status"),
        "contract_hash": run.get("contract_hash"),
        "initial_head": run.get("initial_head"),
        "attempts": [
            {"attempt_no": item.get("attempt_no"), "status": item.get("status"), "error_code": item.get("error_code")}
            for item in attempts if isinstance(item, Mapping)
        ],
        "event_types": [item.get("event_type") for item in events if isinstance(item, Mapping)],
        "artifacts": [
            {"kind": item.get("kind"), "sha256": item.get("sha256"), "size_bytes": item.get("size_bytes")}
            for item in artifacts if isinstance(item, Mapping)
        ],
        "external_writes": False,
        "database_business_writes": False,
        "git_commit_push": False,
    }
    json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return safe
