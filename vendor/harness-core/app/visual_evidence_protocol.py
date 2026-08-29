"""Provider-neutral, local-only visual evidence handoff for Harness hosts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VISUAL_EVIDENCE_PROTOCOL_VERSION = "his-visual-evidence.v1"
_MAX_IMAGES = 4
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_TITLE_CHARS = 500
_MAX_DESCRIPTION_CHARS = 2_000
_REQUIRED_FACT_FIELDS = ("error_text", "menu", "action", "business_scene")
_REQUIRED_DOCUMENT_FACT_FIELDS = ("document_type", "visible_text", "key_facts")


@dataclass(frozen=True)
class VisualEvidenceExtractionRequest:
    title: str
    description: str
    image_paths: tuple[Path, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.title, str)
            or len(self.title) > _MAX_TITLE_CHARS
            or not isinstance(self.description, str)
            or len(self.description) > _MAX_DESCRIPTION_CHARS
            or not isinstance(self.image_paths, tuple)
            or not self.image_paths
            or len(self.image_paths) > _MAX_IMAGES
            or any(not _safe_image_path(path) for path in self.image_paths)
        ):
            raise ValueError("visual_evidence_request_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": VISUAL_EVIDENCE_PROTOCOL_VERSION,
            "title": self.title,
            "description": self.description,
            "image_paths": [str(path) for path in self.image_paths],
        }


@dataclass(frozen=True)
class VisualEvidenceExtractionResult:
    facts: tuple[dict[str, str], ...]
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.facts, tuple)
            or not isinstance(self.blockers, tuple)
            or any(not _valid_fact(fact) for fact in self.facts)
            or any(not isinstance(item, str) or not item for item in self.blockers)
        ):
            raise ValueError("visual_evidence_result_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": VISUAL_EVIDENCE_PROTOCOL_VERSION,
            "facts": [dict(item) for item in self.facts],
            "blockers": list(self.blockers),
        }


class VisualEvidenceHostSession:
    """Invoke one explicitly supplied local host visual adapter.

    The core never picks a model or launches a provider here.  A host that
    actually supports image input supplies ``handler`` and receives only the
    sealed local image paths plus bounded, read-only task text.
    """

    def __init__(self, handler: Callable[[VisualEvidenceExtractionRequest], Any]) -> None:
        if not callable(handler):
            raise TypeError("visual_evidence_adapter_invalid")
        self._handler = handler

    def extract(self, request: VisualEvidenceExtractionRequest) -> VisualEvidenceExtractionResult:
        if not isinstance(request, VisualEvidenceExtractionRequest):
            raise ValueError("visual_evidence_request_invalid")
        try:
            result = self._handler(request)
        except Exception:
            return VisualEvidenceExtractionResult((), ("visual_evidence_adapter_rejected",))
        if isinstance(result, VisualEvidenceExtractionResult):
            return result
        if not isinstance(result, Mapping):
            return VisualEvidenceExtractionResult((), ("visual_evidence_adapter_invalid",))
        facts = result.get("facts")
        blockers = result.get("blockers")
        if not isinstance(facts, (list, tuple)) or not isinstance(blockers, (list, tuple)):
            return VisualEvidenceExtractionResult((), ("visual_evidence_adapter_invalid",))
        normalized = tuple(_normalize_fact(item, request.image_paths) for item in facts)
        if any(item is None for item in normalized):
            return VisualEvidenceExtractionResult((), ("visual_evidence_adapter_invalid",))
        try:
            return VisualEvidenceExtractionResult(
                tuple(item for item in normalized if item is not None),
                tuple(str(item) for item in blockers),
            )
        except ValueError:
            return VisualEvidenceExtractionResult((), ("visual_evidence_adapter_invalid",))


def parse_visual_evidence_request(value: object) -> VisualEvidenceExtractionRequest:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "title", "description", "image_paths"
    } or value.get("schema_version") != VISUAL_EVIDENCE_PROTOCOL_VERSION:
        raise ValueError("visual_evidence_request_invalid")
    paths = value.get("image_paths")
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise ValueError("visual_evidence_request_invalid")
    return VisualEvidenceExtractionRequest(
        title=value.get("title") if isinstance(value.get("title"), str) else "",
        description=value.get("description") if isinstance(value.get("description"), str) else "",
        image_paths=tuple(Path(path) for path in paths),
    )


def parse_visual_evidence_result(
    value: object,
    *,
    image_paths: tuple[Path, ...],
) -> VisualEvidenceExtractionResult:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "facts", "blockers"
    } or value.get("schema_version") != VISUAL_EVIDENCE_PROTOCOL_VERSION:
        raise ValueError("visual_evidence_result_invalid")
    facts = value.get("facts")
    blockers = value.get("blockers")
    if not isinstance(facts, list) or not isinstance(blockers, list):
        raise ValueError("visual_evidence_result_invalid")
    normalized = tuple(_normalize_fact(item, image_paths) for item in facts)
    if any(item is None for item in normalized):
        raise ValueError("visual_evidence_result_invalid")
    return VisualEvidenceExtractionResult(
        tuple(item for item in normalized if item is not None),
        tuple(blockers),
    )


def _safe_image_path(path: object) -> bool:
    return (
        isinstance(path, Path)
        and path.is_absolute()
        and not path.is_symlink()
        and path.is_file()
        and path.stat().st_size <= _MAX_IMAGE_BYTES
    )


def _valid_fact(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if not valid_visual_fact(value):
        return False
    return isinstance(value.get("image_path"), str) and isinstance(value.get("target_module"), str)


def valid_visual_fact(value: object) -> bool:
    """Validate visible fact content before a host binds it to an image path."""
    if not isinstance(value, dict):
        return False
    fact_type = str(value.get("fact_type") or "ui_trace").strip().lower()
    if fact_type == "document":
        return all(
            isinstance(value.get(field), str) and value[field].strip()
            for field in _REQUIRED_DOCUMENT_FACT_FIELDS
        )
    return fact_type == "ui_trace" and all(
        isinstance(value.get(field), str) and value[field].strip()
        for field in _REQUIRED_FACT_FIELDS
    )


def _normalize_fact(value: object, image_paths: tuple[Path, ...]) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    image_path = str(value.get("image_path") or image_paths[0]).strip()
    if image_path not in {str(path) for path in image_paths}:
        return None
    fact_type = str(value.get("fact_type") or "ui_trace").strip().lower()
    fact = {
        "image_path": image_path,
        "error_text": str(value.get("error_text") or "").strip(),
        "menu": str(value.get("menu") or "").strip(),
        "action": str(value.get("action") or "").strip(),
        "business_scene": str(value.get("business_scene") or "").strip(),
        "target_module": str(value.get("target_module") or "").strip(),
    }
    if fact_type == "document":
        fact.update(
            {
                "fact_type": "document",
                "document_type": str(value.get("document_type") or "").strip(),
                "visible_text": str(value.get("visible_text") or "").strip(),
                "key_facts": str(value.get("key_facts") or "").strip(),
            }
        )
    return fact if _valid_fact(fact) else None
