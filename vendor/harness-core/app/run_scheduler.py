from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


@dataclass
class ScheduledRun:
    job_id: str
    title: str
    status: str = "queued"
    stage: str = "queued"
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    run_id: int | None = None
    error: str = ""
    recovery_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class RunScheduler:
    """Small process-local scheduler for the web UI.

    The database remains the durable source of run artifacts; this object only
    tracks the HTTP job until the worker has created or finalized that record.
    """

    def __init__(self, *, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="harness-run")
        self._lock = threading.RLock()
        self._jobs: dict[str, ScheduledRun] = {}
        self._futures: dict[str, Future] = {}

    def submit(self, *, title: str, demand_text: str, project_path: str = "") -> str:
        job_id = uuid4().hex
        record = ScheduledRun(job_id=job_id, title=title)
        with self._lock:
            self._jobs[job_id] = record
        future = self._executor.submit(self._run, job_id, title, demand_text, project_path)
        with self._lock:
            self._futures[job_id] = future
        return job_id

    def _run(self, job_id: str, title: str, demand_text: str, project_path: str) -> None:
        self._update(job_id, status="running", stage="workflow_start", started_at=_now())
        try:
            from app.harness import RequirementWorkflowRunner

            self._update(job_id, stage="analysis")
            result = RequirementWorkflowRunner(mode="mock", allow_mock=True).run(
                title=title,
                demand_text=demand_text,
                source_type="manual",
                project_path=project_path or None,
            )
            self._update(job_id, status=result.status, stage="completed", run_id=result.run_id, finished_at=_now())
        except Exception as exc:  # worker failures must become visible records
            self._update(
                job_id,
                status="failed",
                stage="failed",
                finished_at=_now(),
                error=f"{type(exc).__name__}: {exc}",
                recovery_action="检查运行前诊断、项目路径和模型配置后重试；不自动删除已有产物。",
            )

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            for key, value in fields.items():
                setattr(record, key, value)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._jobs.get(job_id)
            return record.to_dict() if record else None

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._jobs.get(job_id)
            future = self._futures.get(job_id)
            if record is None:
                return None
            if future is not None and future.cancel():
                record.status = "cancelled"
                record.stage = "cancelled"
                record.finished_at = _now()
            else:
                record.error = "运行已开始，不能强制终止；请等待失败/完成记录。"
            return record.to_dict()


WEB_RUN_SCHEDULER = RunScheduler()
