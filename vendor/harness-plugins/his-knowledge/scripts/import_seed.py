"""Validated, local-only importer for the fixed HIS governance seed."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from knowledge_maintain import _signals  # noqa: E402
from knowledge_store import AUTHORITIES, ITEM_STATUSES, KINDS, DEFAULT_KNOWLEDGE_HOME, KnowledgeStore  # noqa: E402


SEED_SCHEMA_VERSION = "his-knowledge-seed.v1"
SEED_PATH = SCRIPT_ROOT.parent / "assets" / "seed_knowledge.json"
ITEM_FIELDS = frozenset((
    "stable_key", "title", "body", "kind", "authority", "status",
    "hospital_scope", "region_scope", "module_scope", "repo_scope", "branch_scope",
    "version_label", "valid_from", "valid_until", "source_refs", "tags",
))
SCOPE_FIELDS = ("hospital_scope", "region_scope", "module_scope", "repo_scope", "branch_scope")
CLAIM_LEVELS = frozenset(("governance", "support"))
LOGICAL_SQLITE_PATH = "$HIS_KNOWLEDGE_HOME/knowledge.sqlite"


@dataclass(frozen=True)
class SeedItemReport:
    stable_key: str
    item_id: int
    content_hash: str


@dataclass(frozen=True)
class SeedImportReport:
    schema_version: str
    item_count: int
    items: tuple[SeedItemReport, ...]
    logical_local_sqlite_path: str = LOGICAL_SQLITE_PATH

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "item_count": self.item_count,
            "items": [
                {"stable_key": item.stable_key, "item_id": item.item_id, "content_hash": item.content_hash}
                for item in self.items
            ],
            "logical_local_sqlite_path": self.logical_local_sqlite_path,
        }


def load_seed(seed_path: Path = SEED_PATH) -> dict[str, object]:
    """Load and fully validate the asset without creating a knowledge home or database."""
    try:
        payload = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid seed asset") from error
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "items"}:
        raise ValueError("invalid seed asset")
    if payload.get("schema_version") != SEED_SCHEMA_VERSION or not isinstance(payload.get("items"), list):
        raise ValueError("invalid seed asset")
    items = payload["items"]
    if len(items) != 5:
        raise ValueError("invalid seed asset")
    keys: set[str] = set()
    validated = []
    for item in items:
        validated.append(_validate_item(item, keys))
    return {"schema_version": SEED_SCHEMA_VERSION, "items": validated}


def _validate_item(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != ITEM_FIELDS:
        raise ValueError("invalid seed item")
    item = dict(value)
    if not all(isinstance(item[field], str) and item[field].strip() for field in ("stable_key", "title", "body", "version_label")):
        raise ValueError("invalid seed item")
    if (
        item["stable_key"] in keys
        or not isinstance(item["kind"], str)
        or item["kind"] not in KINDS
        or not isinstance(item["authority"], str)
        or item["authority"] not in AUTHORITIES
        or not isinstance(item["status"], str)
    ):
        raise ValueError("invalid seed item")
    if item["kind"] == "personal_memory" or item["authority"] == "personal_preference" or item["status"] != "active" or item["status"] not in ITEM_STATUSES:
        raise ValueError("invalid seed item")
    if not all(isinstance(item[field], str) for field in (*SCOPE_FIELDS, "valid_from", "valid_until")):
        raise ValueError("invalid seed item")
    if not any(item[field].strip() for field in SCOPE_FIELDS) or item["valid_from"] or item["valid_until"]:
        raise ValueError("invalid seed item")
    sources = item["source_refs"]
    if not isinstance(sources, list) or not sources or any(not _valid_source(source) for source in sources):
        raise ValueError("invalid seed item")
    tags = item["tags"]
    if (
        not isinstance(tags, list)
        or not tags
        or not all(isinstance(tag, str) and tag.strip() for tag in tags)
        or tags != sorted(tags)
        or len(set(tags)) != len(tags)
    ):
        raise ValueError("invalid seed item")
    if _signals(item, "$.seed"):
        raise ValueError("invalid seed item")
    keys.add(item["stable_key"])
    return item


def _valid_source(source: object) -> bool:
    return (
        isinstance(source, dict)
        and set(source) == {"ref", "claim_level"}
        and isinstance(source["ref"], str)
        and bool(source["ref"].strip())
        and isinstance(source["claim_level"], str)
        and source["claim_level"] in CLAIM_LEVELS
    )


def import_seed(
    *,
    store: KnowledgeStore,
    seed_path: Path = SEED_PATH,
) -> SeedImportReport:
    """Import a fully validated fixed seed through an injected local store only."""
    if not callable(getattr(store, "import_items_atomically", None)):
        raise ValueError("invalid store")
    seed = load_seed(seed_path)
    imported = store.import_items_atomically(seed["items"])
    reports = tuple(
        SeedItemReport(str(item["stable_key"]), int(item["id"]), str(item["content_hash"]))
        for item in imported
    )
    return SeedImportReport(SEED_SCHEMA_VERSION, len(reports), reports)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="import_seed")
    parser.add_argument("--home", required=True)
    parser.add_argument("--seed", default=str(SEED_PATH))
    arguments = parser.parse_args(argv)
    home = Path(arguments.home)
    if not home.is_absolute() or home.resolve() == DEFAULT_KNOWLEDGE_HOME:
        parser.error("--home must be an explicit non-default absolute local path")
    try:
        report = import_seed(store=KnowledgeStore(home=home), seed_path=Path(arguments.seed))
    except Exception:
        sys.stderr.write("invalid seed import\n")
        return 2
    sys.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
