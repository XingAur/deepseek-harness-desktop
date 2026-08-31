"""Explainable, scope-aware read-only retrieval for formal HIS knowledge."""
from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Optional


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from knowledge_store import KnowledgeStore  # noqa: E402
from knowledge_capability import knowledge_home, result as capability_result, run_main, validate_request  # noqa: E402


MIN_LIMIT = 1
MAX_LIMIT = 50
AUTHORITY_WEIGHTS = {
    "official_policy": 50,
    "verified_runtime": 40,
    "verified_code": 30,
    "reviewed_team_knowledge": 20,
    "personal_preference": 10,
}
HIGH_AUTHORITIES = {"official_policy", "verified_runtime", "verified_code"}
SCOPE_EXACT_WEIGHT = 5
CURRENT_FRESHNESS_WEIGHT = 10
STALE_PENALTY = 25
CONFLICT_PENALTY = 35
_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)
_SCOPES = (
    ("hospital", "hospital_scope"), ("region", "region_scope"), ("module", "module_scope"),
    ("repo", "repo_scope"), ("branch", "branch_scope"),
)


@dataclass(frozen=True)
class KnowledgeQuery:
    text: str
    hospital: str = ""
    region: str = ""
    module: str = ""
    repo: str = ""
    branch: str = ""
    as_of: str = ""
    limit: int = 8


@dataclass(frozen=True)
class KnowledgeScopes:
    hospital: str
    region: str
    module: str
    repo: str
    branch: str


@dataclass(frozen=True)
class ScoreBreakdown:
    lexical_score: int
    authority_weight: int
    scope_match_weight: int
    freshness_weight: int
    stale_penalty: int
    conflict_penalty: int
    final_score: int


@dataclass(frozen=True)
class KnowledgeMatch:
    stable_key: str
    title: str
    body: str
    kind: str
    authority: str
    scopes: KnowledgeScopes
    version_label: str
    valid_from: str
    valid_until: str
    source_refs: tuple[Mapping[str, object], ...]
    tags: tuple[object, ...]
    temporal_state: str
    lexical_explanations: tuple[str, ...]
    score_breakdown: ScoreBreakdown
    conflict_peer_keys: tuple[str, ...]
    can_support_direct_answer: bool


@dataclass(frozen=True)
class RetrievalCounts:
    snapshot_items: int
    lexical_matches: int
    scope_eligible: int
    returned: int


@dataclass(frozen=True)
class KnowledgeRetrieval:
    evidence_status: str
    can_answer: bool
    items: tuple[KnowledgeMatch, ...]
    audit_backend: str
    counts: RetrievalCounts


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token.lower() for token in _TOKEN.findall(value.lower())))


def _parse_date(value: object) -> Optional[date]:
    if value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("dates must be strings")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("dates must use YYYY-MM-DD") from None


def _stored_temporal_state(item: Mapping[str, object], as_of: date) -> str:
    try:
        valid_from = _parse_date(item.get("valid_from", ""))
        valid_until = _parse_date(item.get("valid_until", ""))
    except ValueError:
        return "invalid"
    if valid_from is not None and valid_from > as_of:
        return "future"
    if valid_until is not None and valid_until < as_of:
        return "stale"
    return "current"


def _lexical(item: Mapping[str, object], query_text: str, query_tokens: tuple[str, ...]) -> tuple[int, tuple[str, ...]]:
    title = str(item["title"]).lower()
    body = str(item["body"]).lower()
    tags = tuple(str(tag).lower() for tag in item.get("tags", ()))
    score = 0
    explanations = []
    if query_text == title:
        score += 100
        explanations.append("title:exact")
    else:
        title_tokens = set(_tokens(title))
        for token in query_tokens:
            if token in title_tokens:
                score += 20
                explanations.append("title:token:" + token)
    if query_text in body:
        score += 10
        explanations.append("body:phrase")
    else:
        body_tokens = set(_tokens(body))
        for token in query_tokens:
            if token in body_tokens:
                score += 4
                explanations.append("body:token:" + token)
    for token in query_tokens:
        if token in tags:
            score += 2
            explanations.append("tag:" + token)
    return score, tuple(explanations)


class KnowledgeRetriever:
    """Read a Store snapshot and return deterministic, evidence-bearing matches."""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        utc_date: Optional[Callable[[], date]] = None,
        backend_preference: str = "auto",
    ) -> None:
        if not isinstance(store, KnowledgeStore):
            raise ValueError("store must be a KnowledgeStore")
        if backend_preference not in {"auto", "fts5", "like_fallback"}:
            raise ValueError("backend_preference must be auto, fts5, or like_fallback")
        self.store = store
        self._utc_date = utc_date or _utc_today
        self.backend_preference = backend_preference

    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrieval:
        text, as_of, scope_values = self._validate(query)
        snapshot = self.store.read_retrieval_snapshot()
        if snapshot is None:
            return KnowledgeRetrieval("absent", False, (), "absent", RetrievalCounts(0, 0, 0, 0))
        items, relations = snapshot
        tokens = _tokens(text)
        backend, fts_keys = self._backend(items, tokens)
        preliminary = []
        lexical_matches = 0
        for item in items:
            if fts_keys is not None and str(item["stable_key"]) not in fts_keys:
                continue
            lexical_score, explanations = _lexical(item, text, tokens)
            if lexical_score == 0:
                continue
            lexical_matches += 1
            scope_weight = self._scope_weight(item, scope_values)
            if scope_weight is None:
                continue
            temporal_state = _stored_temporal_state(item, as_of)
            preliminary.append((item, lexical_score, explanations, scope_weight, temporal_state))
        conflicts = self._conflicts(preliminary, relations)
        matches = [self._match(*entry, conflicts.get(str(entry[0]["stable_key"]), ())) for entry in preliminary]
        matches.sort(key=lambda item: (-item.score_breakdown.final_score, -AUTHORITY_WEIGHTS[item.authority], item.stable_key))
        selected = list(matches[:query.limit])
        selected_keys = {item.stable_key for item in selected}
        for item in matches:
            if item.conflict_peer_keys and item.stable_key not in selected_keys:
                selected.append(item)
                selected_keys.add(item.stable_key)
        has_conflict = any(item.conflict_peer_keys for item in matches)
        can_answer = bool(matches) and not has_conflict and any(item.can_support_direct_answer for item in matches)
        if not matches:
            evidence_status = "no_match"
        elif has_conflict:
            evidence_status = "conflict"
        elif can_answer:
            evidence_status = "current"
        elif all(item.temporal_state == "stale" for item in matches):
            evidence_status = "stale"
        else:
            evidence_status = "not_current"
        return KnowledgeRetrieval(
            evidence_status, can_answer, tuple(selected), backend,
            RetrievalCounts(len(items), lexical_matches, len(preliminary), len(selected)),
        )

    def _validate(self, query: KnowledgeQuery) -> tuple[str, date, dict[str, str]]:
        if not isinstance(query, KnowledgeQuery) or not isinstance(query.text, str):
            raise ValueError("query must be a KnowledgeQuery with text")
        text = query.text.strip().lower()
        if not text:
            raise ValueError("query text must be non-empty")
        scope_values = {}
        for name, _ in _SCOPES:
            value = getattr(query, name)
            if not isinstance(value, str):
                raise ValueError("query scope fields must be strings")
            scope_values[name] = value.strip()
        if not isinstance(query.as_of, str):
            raise ValueError("query as_of must be a string")
        if not isinstance(query.limit, int) or isinstance(query.limit, bool) or not MIN_LIMIT <= query.limit <= MAX_LIMIT:
            raise ValueError("query limit must be an integer from 1 through 50")
        as_of = self._utc_date() if not query.as_of else _parse_date(query.as_of)
        if not isinstance(as_of, date):
            raise ValueError("utc_date must return a date")
        return text, as_of, scope_values

    def _scope_weight(self, item: Mapping[str, object], scopes: Mapping[str, str]) -> Optional[int]:
        weight = 0
        for query_name, item_name in _SCOPES:
            query_value = scopes[query_name]
            item_value = item[item_name]
            if query_value:
                if item_value and item_value != query_value:
                    return None
                if item_value == query_value:
                    weight += SCOPE_EXACT_WEIGHT
        return weight

    def _backend(self, items: tuple[dict[str, object], ...], tokens: tuple[str, ...]) -> tuple[str, Optional[set[str]]]:
        if not tokens:
            return "like_fallback", None
        if self.backend_preference != "like_fallback":
            keys = self._fts5_keys(items, tokens)
            if keys is not None:
                return "fts5", keys
        return "like_fallback", None

    @staticmethod
    def _fts5_keys(items: tuple[dict[str, object], ...], tokens: tuple[str, ...]) -> Optional[set[str]]:
        if not tokens:
            return set()
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE retrieval_documents USING fts5(stable_key UNINDEXED, title, body, tags)")
            connection.executemany(
                "INSERT INTO retrieval_documents(stable_key, title, body, tags) VALUES (?, ?, ?, ?)",
                [
                    (
                        str(item["stable_key"]),
                        " ".join(_tokens(str(item["title"]))),
                        " ".join(_tokens(str(item["body"]))),
                        " ".join(token for tag in item.get("tags", ()) for token in _tokens(str(tag))),
                    )
                    for item in items
                ],
            )
            match_query = " OR ".join('"' + token + '"' for token in tokens)
            return {str(row[0]) for row in connection.execute(
                "SELECT stable_key FROM retrieval_documents WHERE retrieval_documents MATCH ?", (match_query,)
            )}
        except sqlite3.Error:
            return None
        finally:
            connection.close()

    @staticmethod
    def _conflicts(
        preliminary: list[tuple[dict[str, object], int, tuple[str, ...], int, str]],
        relations: tuple[tuple[str, str, str], ...],
    ) -> dict[str, tuple[str, ...]]:
        authorities = {str(item["stable_key"]): str(item["authority"]) for item, *_ in preliminary}
        conflicts: dict[str, set[str]] = {}
        for source_key, relation, target_key in relations:
            if relation != "conflicts_with":
                continue
            if source_key in authorities and target_key in authorities and {
                authorities[source_key], authorities[target_key]
            }.issubset(HIGH_AUTHORITIES):
                conflicts.setdefault(source_key, set()).add(target_key)
                conflicts.setdefault(target_key, set()).add(source_key)
        return {key: tuple(sorted(peers)) for key, peers in conflicts.items()}

    @staticmethod
    def _match(
        item: Mapping[str, object],
        lexical_score: int,
        explanations: tuple[str, ...],
        scope_weight: int,
        temporal_state: str,
        conflict_peers: tuple[str, ...],
    ) -> KnowledgeMatch:
        authority = str(item["authority"])
        freshness_weight = CURRENT_FRESHNESS_WEIGHT if temporal_state == "current" else 0
        stale_penalty = STALE_PENALTY if temporal_state == "stale" else 0
        conflict_penalty = CONFLICT_PENALTY if conflict_peers else 0
        score = ScoreBreakdown(
            lexical_score, AUTHORITY_WEIGHTS[authority], scope_weight, freshness_weight,
            stale_penalty, conflict_penalty,
            lexical_score + AUTHORITY_WEIGHTS[authority] + scope_weight + freshness_weight - stale_penalty - conflict_penalty,
        )
        source_refs = tuple(
            _freeze(source) for source in item.get("source_refs", ()) if isinstance(source, Mapping)
        )
        return KnowledgeMatch(
            str(item["stable_key"]), str(item["title"]), str(item["body"]), str(item["kind"]), authority,
            KnowledgeScopes(
                str(item["hospital_scope"]), str(item["region_scope"]), str(item["module_scope"]),
                str(item["repo_scope"]), str(item["branch_scope"]),
            ),
            str(item["version_label"]), str(item["valid_from"]), str(item["valid_until"]),
            source_refs, tuple(_freeze(tag) for tag in item.get("tags", ())), temporal_state, explanations,
            score, conflict_peers, temporal_state == "current" and not conflict_peers,
        )


def retrieve(
    query: KnowledgeQuery,
    *,
    store: Optional[KnowledgeStore] = None,
    utc_date: Optional[Callable[[], date]] = None,
    backend_preference: str = "auto",
) -> KnowledgeRetrieval:
    """Retrieve from an injected store, constructing the default store lazily when omitted."""
    return KnowledgeRetriever(store or KnowledgeStore(), utc_date=utc_date, backend_preference=backend_preference).retrieve(query)


_CAPABILITY_INPUT = frozenset(("text", "hospital", "region", "module", "repo", "branch", "as_of", "limit"))


def _validate_capability_input(value: Mapping[str, object]) -> None:
    if not isinstance(value.get("text"), str) or not value["text"].strip():
        raise ValueError("invalid capability request")
    for key in _CAPABILITY_INPUT - {"text", "limit"}:
        if key in value and not isinstance(value[key], str):
            raise ValueError("invalid capability request")
    if "limit" in value and (isinstance(value["limit"], bool) or not isinstance(value["limit"], int)):
        raise ValueError("invalid capability request")


def _json_safe(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {name: _json_safe(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def execute_request(request: object) -> dict[str, object]:
    """Run the L0 retrieval capability without creating the knowledge home."""
    checked = validate_request(
        request, capability="knowledge.retrieve", mode="preview", mutation_level="L0", scope=(),
        input_fields=_CAPABILITY_INPUT, validator=_validate_capability_input,
    )
    values = checked["input"]
    retrieval = KnowledgeRetriever(KnowledgeStore(home=knowledge_home())).retrieve(KnowledgeQuery(
        text=str(values["text"]), hospital=str(values.get("hospital", "")), region=str(values.get("region", "")),
        module=str(values.get("module", "")), repo=str(values.get("repo", "")), branch=str(values.get("branch", "")),
        as_of=str(values.get("as_of", "")), limit=int(values.get("limit", 8)),
    ))
    return capability_result(
        checked, status="success", summary="KNOWLEDGE_RETRIEVE_OK", mutation_level="L0", changed=False,
        data={"retrieval": _json_safe(retrieval)}, audit={
            "credential_class": "none", "external_write_attempted": False, "audit_backend": retrieval.audit_backend,
        },
    )


def main(argv: list[str] | None = None) -> int:
    return run_main(argv, execute_request)


if __name__ == "__main__":
    raise SystemExit(main())
