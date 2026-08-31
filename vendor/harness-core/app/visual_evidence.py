"""Read archived requirement screenshots before technical discovery.

Retrieving a Yunxiao image and interpreting its visible content are separate
steps.  A successful local archive must never be invalidated by another,
expired duplicate image reference.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Protocol

from app.codex_cli_worker import CODEX_EXECUTABLE, CodexCliWorker, CodexWorkerRequest
from app.requirement_provider import refresh_evidence_quality
from app.visual_evidence_protocol import (
    VisualEvidenceExtractionRequest,
    VisualEvidenceHostSession,
    parse_visual_evidence_result,
    valid_visual_fact,
)


class VisualEvidenceAnalyzer(Protocol):
    def analyze(self, *, title: str, description: str, image_paths: tuple[Path, ...]) -> Mapping[str, Any]: ...


class _SilentVisualWorkerSink:
    def on_started(self, pid: int, start_identity: str) -> None:
        del pid, start_identity

    def on_event(self, event: dict[str, object]) -> None:
        del event


class CodexCliVisualEvidenceAnalyzer:
    """Run an explicitly selected, read-only Codex visual reviewer."""

    def __init__(
        self,
        *,
        worker: CodexCliWorker | None = None,
        timeout_seconds: int = 120,
        schema_path: str | Path | None = None,
    ) -> None:
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 3600:
            raise ValueError("visual_evidence_timeout_invalid")
        target = Path(schema_path or Path(__file__).resolve().parents[1] / "config" / "schemas" / "visual_evidence.v1.json")
        if not target.is_absolute() or target.is_symlink() or not target.is_file():
            raise ValueError("visual_evidence_schema_invalid")
        self._worker = worker or CodexCliWorker()
        self._timeout_seconds = timeout_seconds
        self._schema_path = target.resolve()
        self._schema_sha256 = hashlib.sha256(self._schema_path.read_bytes()).hexdigest()

    def analyze(
        self,
        *,
        title: str,
        description: str,
        image_paths: tuple[Path, ...],
    ) -> Mapping[str, Any]:
        try:
            extraction_request = VisualEvidenceExtractionRequest(
                title=title[:500],
                description=description[:2000],
                image_paths=image_paths,
            )
            worker_request = CodexWorkerRequest.visual_reviewer(
                Path(__file__).resolve().parents[1],
                _prompt(extraction_request.title, extraction_request.description),
                self._timeout_seconds,
                self._schema_path,
                self._schema_sha256,
                extraction_request.image_paths,
            )
            worker_result = self._worker.start(worker_request, _SilentVisualWorkerSink())
            if str(getattr(worker_result, "error_code", "") or ""):
                return {"facts": [], "blockers": ["视觉证据读取失败；已保持改码门禁关闭。"]}
            result = parse_visual_evidence_result(
                getattr(worker_result, "final_response", None),
                image_paths=extraction_request.image_paths,
            )
        except (OSError, UnicodeError, ValueError, TypeError):
            return {"facts": [], "blockers": ["视觉证据结果无效；已保持改码门禁关闭。"]}
        return {
            "facts": [dict(item) for item in result.facts],
            "blockers": list(result.blockers),
            "host": {"type": "codex_cli", "executable": str(CODEX_EXECUTABLE)},
        }

class HostVisualEvidenceAnalyzer:
    """Adapt one host-declared local image reader to the Harness gate."""

    def __init__(self, session: VisualEvidenceHostSession) -> None:
        if not isinstance(session, VisualEvidenceHostSession):
            raise TypeError("visual_evidence_adapter_invalid")
        self._session = session

    def analyze(
        self,
        *,
        title: str,
        description: str,
        image_paths: tuple[Path, ...],
    ) -> Mapping[str, Any]:
        result = self._session.extract(
            VisualEvidenceExtractionRequest(
                title=title[:500],
                description=description[:2000],
                image_paths=image_paths,
            )
        )
        return {"facts": list(result.facts), "blockers": list(result.blockers)}


class FileVisualEvidenceAnalyzer:
    """Read one host-produced visual result without selecting a model/provider."""

    def __init__(self, result_path: str | Path) -> None:
        target = Path(result_path).expanduser()
        if not target.is_absolute() or target.is_symlink() or not target.is_file():
            raise ValueError("visual_evidence_result_file_invalid")
        self._path = target.resolve()

    def analyze(
        self,
        *,
        title: str,
        description: str,
        image_paths: tuple[Path, ...],
    ) -> Mapping[str, Any]:
        del title, description
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            result = parse_visual_evidence_result(payload, image_paths=image_paths)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return {
                "facts": [],
                "blockers": ["视觉宿主结果文件无效；已保持改码门禁关闭。"],
                "host": {"type": "file", "path": str(self._path)},
            }
        return {
            "facts": [dict(item) for item in result.facts],
            "blockers": list(result.blockers),
            "host": {"type": "file", "path": str(self._path)},
        }


def configured_visual_evidence_analyzer() -> VisualEvidenceAnalyzer | None:
    """Return no implicit adapter.

    A visual model may be local or remote, and screenshots can include
    sensitive HIS data.  The core therefore accepts only an explicit host
    adapter; it never launches Codex or uploads the archived image itself.
    """
    return None


def analyze_requirement_visual_evidence(evidence: dict[str, Any], *, analyzer: VisualEvidenceAnalyzer | None = None) -> dict[str, Any]:
    visual = evidence.get("visual_evidence") if isinstance(evidence.get("visual_evidence"), dict) else {}
    if visual.get("required") is not True or visual.get("status") == "analyzed":
        return evidence
    paths = tuple(_image_paths(evidence.get("images")))
    if not paths:
        visual = {**visual, "status": "required", "can_begin_analysis": False, "blockers": ["高风险截图缺失或无法读取；不得开始项目定位、调用链分析或改码。"]}
    else:
        selected_analyzer = analyzer or configured_visual_evidence_analyzer()
        if selected_analyzer is None:
            visual = {
                **visual,
                "status": "ready_for_extraction",
                "can_begin_analysis": False,
                "blockers": [
                    "截图已成功归档，但当前运行宿主未声明视觉证据能力；不得跳过截图事实直接定位项目或改码。"
                ],
            }
            evidence["visual_evidence"] = visual
            refresh_evidence_quality(evidence)
            return evidence
        outcome = selected_analyzer.analyze(
            title=str(evidence.get("title") or ""),
            description=str(evidence.get("description_text") or ""),
            image_paths=paths,
        )
        facts = outcome.get("facts") if isinstance(outcome.get("facts"), list) else []
        valid = [item for item in facts if _valid_fact(item)]
        visual = {
            **visual,
            "status": "analyzed" if valid else "required",
            "can_begin_analysis": bool(valid),
            "facts": valid,
            "blockers": [] if valid else list(outcome.get("blockers") or ["截图未能解析为完整可见事实。"]),
        }
        if isinstance(outcome.get("host"), Mapping):
            visual["host"] = dict(outcome["host"])
    evidence["visual_evidence"] = visual
    refresh_evidence_quality(evidence)
    return evidence


def _image_paths(images: object) -> list[Path]:
    values: list[Path] = []
    for item in images if isinstance(images, list) else []:
        if not isinstance(item, Mapping):
            continue
        raw = str(item.get("path") or "").strip()
        path = Path(raw)
        if raw and path.is_absolute() and not path.is_symlink() and path.is_file() and path.stat().st_size <= 20 * 1024 * 1024:
            values.append(path)
    return values[:4]


def _valid_fact(value: object) -> bool:
    return valid_visual_fact(value)


def _parse_result(text: str, image_paths: tuple[Path, ...]) -> Mapping[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"status": "failed", "facts": [], "blockers": ["截图视觉解析结果不是 JSON。"]}
    facts = payload.get("facts") if isinstance(payload, dict) and isinstance(payload.get("facts"), list) else []
    normalized = []
    for fact in facts:
        if not _valid_fact(fact):
            continue
        normalized.append({"image_path": str(image_paths[0]), "error_text": str(fact["error_text"]).strip(), "menu": str(fact["menu"]).strip(), "action": str(fact["action"]).strip(), "business_scene": str(fact["business_scene"]).strip(), "target_module": str(fact.get("target_module") or "").strip()})
    return {"status": "analyzed" if normalized else "failed", "facts": normalized, "blockers": [] if normalized else ["截图中未提取到完整的错误、菜单、操作和场景事实。"]}


def _prompt(title: str, description: str) -> str:
    return (
        "你是 HIS 截图证据提取器。只读取随消息提供的本地图片中可见的文字和 UI，不得根据需求背景、文件名或医学常识猜测。"
        "返回且只返回 JSON：{\"facts\":[{\"error_text\":\"截图可见的完整错误文本\",\"menu\":\"截图可见菜单/页面\",\"action\":\"截图可见触发动作\",\"business_scene\":\"截图直接证明的业务场景\",\"target_module\":\"截图可见模块名，可为空\"}]}。"
        "任一必填事实不可见时返回 facts 空数组。"
        f"需求标题：{title[:500]}。需求正文：{description[:2000]}。"
    )
