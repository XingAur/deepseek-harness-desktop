from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.project_context import DEFAULT_EXCLUDE_DIRS, TEXT_EXTENSIONS, safe_relative, unique_keep_order


IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
ENDPOINT_RE = re.compile(r"^(?:get|query|list|find|page|load)[A-Z][A-Za-z0-9_]+$")
# A method name beginning with ``get`` is not, by itself, an HTTP or service
# operation.  HIS code contains many ordinary DTO/property helpers (for
# example ``getPageIndex`` and ``getYiLiaoBxId``) that occur in unrelated
# repositories.  Treating same-name helpers as request edges creates a false
# cross-service graph.  Keep this list deliberately small and use operation
# hints below for the positive decision.
GENERIC_HELPER_NAMES = frozenset(
    {
        "getPageIndex",
        "getPageSize",
        "getDate",
        "getTime",
        "getAfterDay",
        "getValueByKey",
        "getRowKey",
        "getRequestHeaders",
        "getDeclaredFields",
        "getDataFor",
        "getSummaries",
        "getConfig",
        "getZhifuFs",
        "getBingRenXx",
        "findIndex",
        "findFirst",
        "findSelectedUploadRowIndex",
    }
)
REQUEST_OPERATION_HINTS = (
    "page",
    "list",
    "query",
    "search",
    "compare",
    "save",
    "update",
    "delete",
    "remove",
    "upload",
    "download",
    "history",
    "detail",
    "tree",
    "catalog",
    "directory",
    "duizhao",
    "matching",
    "approve",
    "shenpi",
    "yibaomulu",
    "yiyuanmulu",
)
FIELD_DECLARATION_RE = re.compile(
    r"\bprivate\s+[A-Za-z_][A-Za-z0-9_<>, ?]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*;"
)
ENUM_OPTION_RE = re.compile(
    r"(?P<value>['\"]?\d+['\"]?)\s*[-=：:]\s*['\"]?(?P<label>[\u4e00-\u9fff]{2,8})['\"]?"
)
V_MODEL_FIELD_RE = re.compile(
    r"\bv-model\s*=\s*['\"][^'\"]*?\.([A-Za-z_][A-Za-z0-9_]*)['\"]"
)
DATA_FIELD_RE = re.compile(
    r"\bdataField\s*:\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"
)
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
CJK_STOP_TERMS = {
    "增加", "新增", "查询", "筛选", "默认", "全部", "页面", "列表", "病人",
    "收费", "状态", "条件", "一个", "功能", "需求", "过滤", "显示", "修改",
}
COMMON_IDENTIFIERS = {
    "String", "Integer", "Long", "Boolean", "Object", "List", "Map", "return",
    "const", "export", "default", "request", "params", "query", "this", "true",
    "false", "null", "undefined", "private", "public", "class", "void",
}
UI_SUFFIXES = {".vue", ".jsx", ".tsx", ".html"}
DISCOVERY_SOURCE_SUFFIXES = TEXT_EXTENSIONS - {".css", ".scss", ".less", ".md", ".txt"}
MAX_SEED_FILES = 120
MAX_RELEVANT_FILES = 240
MAX_IDENTIFIERS = 120
ENUM_CONTEXT_CHARS = 640
MAX_CANDIDATE_TERMS = 512
MAX_CJK_RUN_TERM_LENGTH = 12


@dataclass(frozen=True)
class DiscoveryNode:
    project: str
    path: str
    kind: str
    identifiers: tuple[str, ...]
    matched_terms: tuple[str, ...]
    snippet: str
    declared_fields: tuple[str, ...] = ()
    ui_bound_fields: tuple[str, ...] = ()
    semantic_ui_bound_fields: tuple[str, ...] = ()
    request_identifiers: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryEdge:
    kind: str
    identifier: str
    source_path: str
    target_path: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EnumOption:
    value: str
    label: str
    project: str
    path: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryFieldCandidate:
    field: str
    score: int
    ui_paths: tuple[str, ...]
    stored_paths: tuple[str, ...]
    endpoints: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryGraph:
    nodes: tuple[DiscoveryNode, ...]
    edges: tuple[DiscoveryEdge, ...]

    def to_dict(self) -> dict:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


@dataclass(frozen=True)
class DiscoveryResult:
    graph: DiscoveryGraph
    unknowns: tuple[str, ...]
    proven_rules: tuple[str, ...] = ()
    enum_options: tuple[EnumOption, ...] = ()
    field_candidates: tuple[DiscoveryFieldCandidate, ...] = ()

    @property
    def target_field(self) -> str:
        return self.field_candidates[0].field if self.field_candidates else ""

    def find_nodes(
        self,
        *,
        kind: str | None = None,
        path_suffix: str | None = None,
        identifier: str | None = None,
    ) -> list[DiscoveryNode]:
        return [
            node
            for node in self.graph.nodes
            if (kind is None or node.kind == kind)
            and (path_suffix is None or node.path.endswith(path_suffix))
            and (identifier is None or identifier in node.identifiers)
        ]

    def find_edges(
        self,
        *,
        kind: str | None = None,
        identifier: str | None = None,
    ) -> list[DiscoveryEdge]:
        return [
            edge
            for edge in self.graph.edges
            if (kind is None or edge.kind == kind)
            and (identifier is None or edge.identifier == identifier)
        ]

    def to_dict(self) -> dict:
        return {
            "schema_version": "demand-discovery.v1",
            "evidence_graph": self.graph.to_dict(),
            "unknowns": list(self.unknowns),
            "proven_rules": list(self.proven_rules),
            "enum_options": [option.to_dict() for option in self.enum_options],
            "field_candidates": [candidate.to_dict() for candidate in self.field_candidates],
            "target_field": self.target_field,
        }


@dataclass(frozen=True)
class _SourceFile:
    project: str
    path: str
    suffix: str
    text: str


def discover_demand(
    *,
    demand_text: str,
    selected_projects: list[dict],
    max_files: int = 1800,
    max_file_bytes: int = 220_000,
) -> DiscoveryResult:
    candidate_terms = extract_candidate_terms(demand_text)
    files = bounded_source_files(
        selected_projects=selected_projects,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
    )
    seed_files = sorted(
        (
            source for source in files
            if any(term in source.text for term in candidate_terms)
        ),
        key=lambda source: (-source_match_score(source, candidate_terms), source.project, source.path),
    )[:MAX_SEED_FILES]
    identifiers = extract_identifiers(
        seed_files,
        candidate_terms=candidate_terms,
        max_identifiers=MAX_IDENTIFIERS,
    )
    seed_keys = {(source.project, source.path) for source in seed_files}
    relevant_files = sorted(
        (
            source
            for source in files
            if (source.project, source.path) in seed_keys
            or any(identifier in source.text for identifier in identifiers)
        ),
        key=lambda source: (
            0 if (source.project, source.path) in seed_keys else 1,
            -source_match_score(source, candidate_terms),
            -sum(identifier in source.text for identifier in identifiers),
            source.project,
            source.path,
        ),
    )[:MAX_RELEVANT_FILES]
    nodes = tuple(
        build_node(source, candidate_terms=candidate_terms, identifiers=identifiers)
        for source in relevant_files
    )
    edges = build_edges(nodes)
    unknowns = find_unknowns(nodes, edges)
    field_candidates = rank_field_candidates(nodes)
    target_field = field_candidates[0].field if field_candidates else ""
    enum_options = extract_enum_options(relevant_files, target_field=target_field)
    return DiscoveryResult(
        graph=DiscoveryGraph(nodes=nodes, edges=edges),
        unknowns=tuple(unknowns),
        proven_rules=tuple(
            f"{option.label}传 {option.value}，来源 {option.project}:{option.path}。"
            for option in enum_options
        ),
        enum_options=enum_options,
        field_candidates=field_candidates,
    )


def extract_candidate_terms(demand_text: str) -> tuple[str, ...]:
    text = str(demand_text or "")
    ordered_terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        value = str(term or "").strip()
        if not value or value in CJK_STOP_TERMS or value in seen:
            return
        seen.add(value)
        ordered_terms.append(value)

    for identifier in IDENTIFIER_RE.findall(text):
        if identifier not in COMMON_IDENTIFIERS:
            add(identifier)

    for run in CJK_RUN_RE.findall(text):
        if len(run) <= MAX_CJK_RUN_TERM_LENGTH:
            add(run)
        # Four-to-two-character n-grams retain compact business labels while
        # avoiding the quadratic explosion from every 2..8-character window.
        max_length = min(4, len(run))
        for length in range(max_length, 1, -1):
            for start in range(0, len(run) - length + 1):
                term = run[start : start + length]
                add(term)
                for index in range(len(term)):
                    contracted = term[:index] + term[index + 1:]
                    if len(contracted) >= 2:
                        add(contracted)
                if len(ordered_terms) >= MAX_CANDIDATE_TERMS:
                    return tuple(ordered_terms[:MAX_CANDIDATE_TERMS])
    return tuple(ordered_terms[:MAX_CANDIDATE_TERMS])


def bounded_source_files(
    *,
    selected_projects: list[dict],
    max_files: int,
    max_file_bytes: int,
) -> list[_SourceFile]:
    files: list[_SourceFile] = []
    for project in selected_projects:
        root = Path(str(project.get("path") or ""))
        if not root.is_dir():
            continue
        project_name = str(project.get("name") or root.name)
        scanned = 0
        for base, dirs, names in os.walk(root):
            dirs[:] = sorted(name for name in dirs if name not in DEFAULT_EXCLUDE_DIRS)
            for name in sorted(names):
                path = Path(base) / name
                if path.suffix.lower() not in DISCOVERY_SOURCE_SUFFIXES:
                    continue
                scanned += 1
                if scanned > max_files:
                    break
                try:
                    if path.stat().st_size > max_file_bytes:
                        continue
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    try:
                        text = path.read_text(encoding="gb18030")
                    except (UnicodeDecodeError, OSError):
                        continue
                except OSError:
                    continue
                files.append(
                    _SourceFile(
                        project=project_name,
                        path=safe_relative(path, root),
                        suffix=path.suffix.lower(),
                        text=text,
                    )
                )
            if scanned > max_files:
                break
    return files


def source_match_score(source: _SourceFile, candidate_terms: tuple[str, ...]) -> int:
    return sum(len(term) for term in candidate_terms if term in source.text)


def extract_identifiers(
    files: list[_SourceFile],
    *,
    candidate_terms: tuple[str, ...],
    max_identifiers: int = MAX_IDENTIFIERS,
) -> tuple[str, ...]:
    frequencies: dict[str, int] = {}
    semantic_fields: list[str] = []
    for source in files:
        identifiers_in_source = {
            identifier for identifier in IDENTIFIER_RE.findall(source.text)
            if identifier not in COMMON_IDENTIFIERS
            and (identifier[0].isupper() or any(char.isupper() for char in identifier[1:]))
        }
        identifiers_in_source.update(extract_ui_bound_fields(source.text))
        identifiers_in_source.update(FIELD_DECLARATION_RE.findall(source.text))
        semantic_fields.extend(
            extract_semantic_ui_bound_fields(source.text, candidate_terms)
        )
        for identifier in identifiers_in_source:
            frequencies[identifier] = frequencies.get(identifier, 0) + 1
    prioritized = unique_keep_order(semantic_fields)
    ranked = [
        identifier
        for identifier, _frequency in sorted(
            frequencies.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if identifier not in prioritized
    ]
    return tuple((prioritized + ranked)[:max_identifiers])


def build_node(
    source: _SourceFile,
    *,
    candidate_terms: tuple[str, ...],
    identifiers: tuple[str, ...],
) -> DiscoveryNode:
    matched_terms = tuple(term for term in candidate_terms if term in source.text)
    matched_identifiers = tuple(
        identifier for identifier in identifiers if identifier in source.text
    )
    declared_fields = tuple(
        field for field in unique_keep_order(FIELD_DECLARATION_RE.findall(source.text))
        if field in identifiers
    )
    ui_bound_fields = tuple(
        field for field in extract_ui_bound_fields(source.text) if field in identifiers
    )
    semantic_ui_bound_fields = tuple(
        field
        for field in extract_semantic_ui_bound_fields(source.text, candidate_terms)
        if field in identifiers
    )
    request_identifiers = extract_request_identifiers(source.text)
    if declared_fields:
        kind = "stored_field"
        node_identifiers = declared_fields
    elif source.suffix in UI_SUFFIXES and (matched_terms or matched_identifiers or ui_bound_fields):
        kind = "ui"
        node_identifiers = unique_keep_order(
            [*ui_bound_fields, *request_identifiers, *matched_identifiers]
        )
    elif "controller" in source.path.lower():
        kind = "controller"
        node_identifiers = unique_keep_order([*request_identifiers, *matched_identifiers])
    elif "repository" in source.path.lower() or "dao" in source.path.lower():
        kind = "repository"
        node_identifiers = matched_identifiers
    elif "service" in source.path.lower():
        kind = "service"
        node_identifiers = unique_keep_order([*request_identifiers, *matched_identifiers])
    else:
        kind = "source"
        node_identifiers = matched_identifiers
    match = next(iter(node_identifiers or matched_terms), "")
    return DiscoveryNode(
        project=source.project,
        path=source.path,
        kind=kind,
        identifiers=tuple(node_identifiers),
        matched_terms=matched_terms,
        snippet=snippet_for(source.text, match),
        declared_fields=declared_fields,
        ui_bound_fields=ui_bound_fields,
        semantic_ui_bound_fields=semantic_ui_bound_fields,
        request_identifiers=request_identifiers,
    )


def build_edges(nodes: tuple[DiscoveryNode, ...]) -> tuple[DiscoveryEdge, ...]:
    edges: list[DiscoveryEdge] = []
    identifier_paths: dict[str, list[str]] = {}
    for node in nodes:
        qualified_path = f"{node.project}:{node.path}"
        for identifier in node.identifiers:
            identifier_paths.setdefault(identifier, []).append(qualified_path)
    for identifier, paths in identifier_paths.items():
        unique_paths = list(dict.fromkeys(paths))
        if len(unique_paths) < 2:
            continue
        edge_kind = "request_flow" if is_request_identifier(identifier) else "field_flow"
        for source_path, target_path in zip(unique_paths, unique_paths[1:]):
            edges.append(
                DiscoveryEdge(
                    kind=edge_kind,
                    identifier=identifier,
                    source_path=source_path,
                    target_path=target_path,
                )
            )
    return tuple(edges)


def is_request_identifier(identifier: str) -> bool:
    """Return whether an identifier is strong enough for a request-flow edge.

    This is intentionally stricter than :data:`ENDPOINT_RE`.  The latter is
    retained for backwards-compatible candidate extraction, while the graph
    must not promote generic getters or IDs to service calls.
    """

    value = str(identifier or "").strip()
    if not value or not ENDPOINT_RE.fullmatch(value) or value in GENERIC_HELPER_NAMES:
        return False
    lowered = value.lower()
    return any(hint in lowered for hint in REQUEST_OPERATION_HINTS)


def extract_ui_bound_fields(text: str) -> tuple[str, ...]:
    return unique_keep_order(
        [*V_MODEL_FIELD_RE.findall(text), *DATA_FIELD_RE.findall(text)]
    )


def extract_semantic_ui_bound_fields(
    text: str,
    candidate_terms: tuple[str, ...],
    *,
    radius: int = 96,
) -> tuple[str, ...]:
    fields: list[str] = []
    strong_terms = tuple(term for term in candidate_terms if len(term) >= 3)
    if not strong_terms:
        return ()
    for pattern in (V_MODEL_FIELD_RE, DATA_FIELD_RE):
        for match in pattern.finditer(text):
            start = max(0, match.start() - radius)
            end = min(len(text), match.end() + radius)
            if any(term in text[start:end] for term in strong_terms):
                fields.append(match.group(1))
    return unique_keep_order(fields)


def extract_request_identifiers(text: str) -> tuple[str, ...]:
    return unique_keep_order(
        match.group(1)
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
        if is_request_identifier(match.group(1))
    )


def rank_field_candidates(
    nodes: tuple[DiscoveryNode, ...],
) -> tuple[DiscoveryFieldCandidate, ...]:
    ui_by_field: dict[str, list[DiscoveryNode]] = {}
    semantic_ui_by_field: dict[str, list[DiscoveryNode]] = {}
    stored_by_field: dict[str, list[DiscoveryNode]] = {}
    for node in nodes:
        if node.kind == "ui":
            for field in node.ui_bound_fields:
                ui_by_field.setdefault(field, []).append(node)
            for field in node.semantic_ui_bound_fields:
                semantic_ui_by_field.setdefault(field, []).append(node)
        if node.kind == "stored_field":
            for field in node.declared_fields:
                stored_by_field.setdefault(field, []).append(node)

    candidates: list[DiscoveryFieldCandidate] = []
    eligible_fields = set(ui_by_field).intersection(stored_by_field)
    if semantic_ui_by_field:
        eligible_fields.intersection_update(semantic_ui_by_field)
    for field in sorted(eligible_fields):
        ui_nodes = ui_by_field[field]
        stored_nodes = stored_by_field[field]
        endpoints = unique_keep_order(
            endpoint for node in ui_nodes for endpoint in node.request_identifiers
        )
        semantic_score = sum(
            len(term) for node in [*ui_nodes, *stored_nodes] for term in node.matched_terms
        )
        semantic_ui_count = len(semantic_ui_by_field.get(field, []))
        score = 1000 + 1000 * semantic_ui_count + 100 * len(ui_nodes) + 100 * len(stored_nodes) + 20 * len(endpoints) + semantic_score
        candidates.append(
            DiscoveryFieldCandidate(
                field=field,
                score=score,
                ui_paths=tuple(f"{node.project}:{node.path}" for node in ui_nodes),
                stored_paths=tuple(f"{node.project}:{node.path}" for node in stored_nodes),
                endpoints=tuple(endpoints),
            )
        )
    return tuple(sorted(candidates, key=lambda candidate: (-candidate.score, candidate.field)))


def extract_enum_options(
    files: list[_SourceFile],
    *,
    target_field: str,
) -> tuple[EnumOption, ...]:
    """Extract explicit mappings only from the local source context of the selected field."""
    if not target_field:
        return ()
    options: list[EnumOption] = []
    seen: set[tuple[str, str]] = set()
    for source in files:
        field_positions = [match.start() for match in re.finditer(rf"\b{re.escape(target_field)}\b", source.text)]
        if not field_positions:
            continue
        for start, end in enum_context_ranges(source.text, field_positions):
            for match in ENUM_OPTION_RE.finditer(source.text, start, end):
                value = match.group("value").strip("'\"")
                label = match.group("label")
                key = (value, label)
                if key in seen:
                    continue
                seen.add(key)
                options.append(
                    EnumOption(
                        value=value,
                        label=label,
                        project=source.project,
                        path=source.path,
                    )
                )
    return tuple(options)


def enum_context_ranges(text: str, field_positions: list[int]) -> tuple[tuple[int, int], ...]:
    """Prefer a field's own map/options object; use a small local fallback for comment mappings."""
    ranges: list[tuple[int, int]] = []
    for field_position in field_positions:
        search_end = min(len(text), field_position + ENUM_CONTEXT_CHARS)
        mapping = re.search(
            r"\b(?:map|options|valueMap|optionMap)\s*[:=]\s*\{",
            text[field_position:search_end],
        )
        if mapping:
            start = field_position + mapping.end() - 1
            end = matching_brace_end(text, start)
            if end is not None:
                ranges.append((start + 1, end))
                continue
        ranges.append((field_position, min(len(text), field_position + 240)))
    return tuple(dict.fromkeys(ranges))


def matching_brace_end(text: str, opening_index: int) -> int | None:
    depth = 0
    for index in range(opening_index, len(text)):
        character = text[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def find_unknowns(
    nodes: tuple[DiscoveryNode, ...],
    edges: tuple[DiscoveryEdge, ...],
) -> list[str]:
    unknowns: list[str] = []
    if not nodes:
        unknowns.append("未发现与需求文本直接相关的源码证据。")
    if not any(node.kind == "ui" for node in nodes):
        unknowns.append("未定位到页面/组件证据。")
    if not any(node.kind == "stored_field" for node in nodes):
        unknowns.append("未定位到已声明的存储字段证据。")
    if not any(edge.kind == "request_flow" for edge in edges):
        unknowns.append("未定位到可证明的请求调用链。")
    return unknowns


def snippet_for(text: str, term: str, limit: int = 240) -> str:
    if not term:
        return ""
    index = text.find(term)
    if index < 0:
        return ""
    start = max(0, index - limit // 3)
    end = min(len(text), index + len(term) + limit * 2 // 3)
    return " ".join(text[start:end].split())
