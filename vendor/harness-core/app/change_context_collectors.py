from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from app.capability_contracts import CapabilityRequest, CapabilityResult
from app.change_context_contracts import McpEvidenceReceipt, content_hash
from app.mcp_capability_runtime import McpCapabilityRuntime
from app.task_context import TaskIntentContext
from app.technical_decision import TechnicalContextDiscovery


_MAX_PROJECTS = 16
_MAX_RELATIONSHIPS = 64
_MAX_TESTS_PER_PROJECT = 16
_MAX_FILE_BYTES = 2 * 1024 * 1024
_TEST_SUFFIXES = (".test.js", ".spec.js", ".test.ts", ".spec.ts", "_test.py", "Test.java", "Tests.java")


@dataclass(frozen=True)
class CollectedContextLayer:
    layer_type: str
    status: str
    payload: dict[str, object]
    source_fingerprint: str
    evidence_refs: tuple[str, ...] = ()
    policy_rule_ids: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


class ProjectGraphCollector:
    """Build a bounded topology snapshot without leaking local absolute paths."""

    def collect(self, discovery: TechnicalContextDiscovery) -> CollectedContextLayer:
        projects: list[dict[str, object]] = []
        blockers: list[str] = []
        known_names: set[str] = set()
        for project in discovery.selected_projects[:_MAX_PROJECTS]:
            path = Path(str(project.get("path") or ""))
            name = str(project.get("name") or path.name).strip()
            if not name:
                continue
            known_names.add(name)
            exists = path.is_dir()
            if not exists:
                blockers.append(f"项目不可用：{name}")
            projects.append(
                {
                    "name": name,
                    "role": str(project.get("role") or "unknown"),
                    "exists": exists,
                    "selection_scope": str(project.get("selection_scope") or "candidate_only"),
                }
            )
        relationships = _bounded_relationships(discovery.service_graph, known_names=known_names)
        if not projects:
            blockers.append("未发现可绑定的项目。")
        payload: dict[str, object] = {
            "schema_version": "project-graph.v1",
            "projects": projects,
            "relationships": relationships,
            "explicit_scope": discovery.explicit_scope,
        }
        return _collected(
            layer_type="project_graph",
            payload=payload,
            blockers=blockers,
            policy_rule_ids=("CTX-PROJECT-GRAPH-REQUIRED",),
        )


class ChangeScopeCollector:
    """Bind intent, requirement revision, corrections, and calibrated scope."""

    def __init__(self, *, runtime: McpCapabilityRuntime | None = None) -> None:
        self.runtime = runtime

    def collect(
        self,
        *,
        task_context: TaskIntentContext,
        normalized_requirement_evidence: Mapping[str, object],
        current_user_correction: str,
        calibrated_scope: Mapping[str, object],
        task_id: str = "",
        run_id: str = "",
        mcp_receipt: McpEvidenceReceipt | None = None,
    ) -> CollectedContextLayer:
        evidence = normalized_requirement_evidence
        provider = str(evidence.get("source_type") or evidence.get("provider") or "manual").strip()
        ticket_id = str(evidence.get("ticket_id") or "local").strip()
        revision = str(evidence.get("revision") or evidence.get("requirement_revision") or "").strip()
        comments = _hashes_from_items(evidence.get("comments"))
        attachments = _hashes_from_items(evidence.get("attachments"))
        blockers: list[str] = []
        if not task_context.is_complete:
            blockers.append("任务意图不完整：" + ", ".join(task_context.missing_fields))
        if not revision:
            blockers.append("需求修订标识缺失。")
        receipt = mcp_receipt
        if provider.casefold() == "yunxiao":
            receipt, receipt_blocker = self._yunxiao_receipt(
                ticket_id=ticket_id,
                task_id=task_id,
                run_id=run_id,
                existing=receipt,
            )
            if receipt_blocker:
                blockers.append(receipt_blocker)
        payload: dict[str, object] = {
            "schema_version": "change-scope.v1",
            "task_intent_hash": task_context.content_hash,
            "task_intent_status": "complete" if task_context.is_complete else "incomplete",
            "provider": provider or "manual",
            "ticket_id": ticket_id or "local",
            "requirement_revision": revision,
            "comment_hashes": comments,
            "attachment_hashes": attachments,
            "current_user_correction": str(current_user_correction or "").strip()[:4096],
            "calibrated_scope": _bounded_scope(calibrated_scope),
            "mcp_receipt": receipt.identity_payload() if receipt is not None else None,
        }
        return _collected(
            layer_type="change_scope",
            payload=payload,
            blockers=blockers,
            evidence_refs=tuple(dict.fromkeys((*task_context.source_refs, *(receipt.evidence_refs if receipt else ())))),
            policy_rule_ids=("CTX-CHANGE-SCOPE-REQUIRED", "CTX-LATEST-CORRECTION-WINS"),
        )

    def _yunxiao_receipt(
        self,
        *,
        ticket_id: str,
        task_id: str,
        run_id: str,
        existing: McpEvidenceReceipt | None,
    ) -> tuple[McpEvidenceReceipt | None, str]:
        if _receipt_matches(
            existing,
            capability="workitem.read",
            provider="yunxiao",
            source_identity=f"yunxiao:{ticket_id}",
        ):
            return existing, ""
        if self.runtime is None:
            return None, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: current workitem.read MCP receipt is required."
        request = CapabilityRequest.from_dict(
            {
                "schema_version": "his-capability-request.v1",
                "request_id": _request_id("cc-yunxiao", task_id, ticket_id),
                "capability": "workitem.read",
                "provider": "yunxiao",
                "mode": "preview",
                "mutation_level": "L1",
                "authorization": {"explicit": False, "scope": ["workitem:read"]},
                "input": {
                    "work_item_id": ticket_id,
                    "include_comments": True,
                    "include_attachments": True,
                    "page_cursor": "",
                    "page_size": 50,
                },
                "context": {"task_id": task_id, "run_id": run_id},
            }
        )
        return _execute_receipt(
            self.runtime,
            request,
            expected_source=f"yunxiao:{ticket_id}",
        )


class GitLabProjectGraphCollector:
    """Collect exact remote baseline identities through gitlab.read MCP only."""

    def __init__(self, *, runtime: McpCapabilityRuntime) -> None:
        self.runtime = runtime

    def collect(
        self,
        *,
        project: str,
        ref: str,
        object_id: str,
        task_id: str,
        run_id: str,
        remote_baseline_required: bool,
        existing_receipts: Mapping[str, McpEvidenceReceipt] | None = None,
    ) -> CollectedContextLayer:
        if not remote_baseline_required:
            return _collected(
                layer_type="project_graph",
                payload={
                    "schema_version": "gitlab-project-graph.v1",
                    "remote_baseline_required": False,
                    "project": project,
                    "ref": ref,
                    "object_id": object_id,
                    "mcp_receipts": [],
                },
                blockers=(),
                policy_rule_ids=("CTX-GITLAB-REMOTE-NOT-REQUIRED",),
            )
        receipts: list[McpEvidenceReceipt] = []
        blockers: list[str] = []
        for operation in ("project", "commit"):
            existing = (existing_receipts or {}).get(operation)
            expected_source = f"gitlab:{project}"
            if _receipt_matches(existing, capability="gitlab.read", provider="gitlab", source_identity=expected_source):
                receipts.append(existing)  # type: ignore[arg-type]
                continue
            input_payload = {
                "project": project,
                "operation": operation,
                "ref": "",
                "path": "",
                "object_id": object_id if operation == "commit" else "",
            }
            request = CapabilityRequest.from_dict(
                {
                    "schema_version": "his-capability-request.v1",
                    "request_id": _request_id("cc-gitlab", task_id, project, operation, object_id),
                    "capability": "gitlab.read",
                    "provider": "gitlab",
                    "mode": "preview",
                    "mutation_level": "L1",
                    "authorization": {"explicit": False, "scope": ["gitlab:read"]},
                    "input": input_payload,
                    "context": {"task_id": task_id, "run_id": run_id},
                }
            )
            receipt, blocker = _execute_receipt(self.runtime, request, expected_source=expected_source)
            if blocker:
                blockers.append(blocker)
                break
            receipts.append(receipt)  # type: ignore[arg-type]
        payload: dict[str, object] = {
            "schema_version": "gitlab-project-graph.v1",
            "remote_baseline_required": True,
            "project": project,
            "ref": ref,
            "object_id": object_id,
            "mcp_receipts": [item.identity_payload() for item in receipts],
        }
        return _collected(
            layer_type="project_graph",
            payload=payload,
            blockers=blockers,
            evidence_refs=tuple(ref for receipt in receipts for ref in receipt.evidence_refs),
            policy_rule_ids=("CTX-GITLAB-MCP-ONLY",),
        )


class CodeGraphCollector:
    """Hash only approved targets, connected discovery edges, and relevant tests."""

    def __init__(self, *, max_paths: int = 64) -> None:
        if max_paths < 1 or max_paths > 256:
            raise ValueError("change_context_code_graph_max_paths_invalid")
        self.max_paths = max_paths

    def collect(self, discovery: TechnicalContextDiscovery) -> CollectedContextLayer:
        projects = {str(item.get("name") or ""): dict(item) for item in discovery.selected_projects}
        target_paths: list[str] = []
        tests: list[str] = []
        file_hashes: list[dict[str, object]] = []
        blockers: list[str] = []
        for project_name, project in list(projects.items())[:_MAX_PROJECTS]:
            project_root = Path(str(project.get("path") or ""))
            if not project_name or not project_root.is_dir():
                blockers.append(f"项目不可用：{project_name or 'unknown'}")
                continue
            approved = _target_paths_for_project(discovery, project_name=project_name)
            for relative_path in approved:
                if len(target_paths) >= self.max_paths:
                    blockers.append("目标路径超过上下文上限。")
                    break
                target = _safe_project_file(project_root, relative_path)
                display = _display_path(project_name, relative_path, multi_project=len(projects) > 1)
                if target is None:
                    blockers.append(f"目标源码不可用：{display}")
                    continue
                target_paths.append(display)
                file_hashes.append(_file_fact(target, display))
                for test_path in _find_relevant_tests(project_root, relative_path):
                    test_display = _display_path(project_name, test_path, multi_project=len(projects) > 1)
                    if test_display in tests or len(tests) >= self.max_paths:
                        continue
                    test_file = _safe_project_file(project_root, test_path)
                    if test_file is None:
                        continue
                    tests.append(test_display)
                    file_hashes.append(_file_fact(test_file, test_display))
        if not target_paths:
            blockers.append("未形成可验证的目标源码路径。")
        payload: dict[str, object] = {
            "schema_version": "code-graph.v1",
            "target_paths": sorted(set(target_paths)),
            "tests": sorted(set(tests)),
            "file_hashes": sorted(file_hashes, key=lambda item: str(item["path"])),
            "call_edges": _bounded_code_edges(discovery, projects=set(projects)),
        }
        return _collected(
            layer_type="code_graph",
            payload=payload,
            blockers=blockers,
            policy_rule_ids=("CTX-CODE-GRAPH-REQUIRED", "CTX-CODE-HASH-BOUND"),
        )


class DataGraphCollector:
    """Collect bounded PostgreSQL catalog facts through database.inspect MCP only."""

    COLLECTOR_VERSION = "data-graph-collector.v1"
    OPERATIONS = ("tables", "columns", "constraints", "indexes", "foreign_keys")

    def __init__(self, *, runtime: McpCapabilityRuntime) -> None:
        self.runtime = runtime

    def collect(
        self,
        *,
        connection_alias: str,
        schema: str,
        tables: Sequence[str],
        task_id: str,
        run_id: str,
    ) -> CollectedContextLayer:
        candidates = tuple(dict.fromkeys(str(item).strip() for item in tables if str(item).strip()))
        validation_blockers = _database_scope_blockers(connection_alias, schema, candidates)
        if validation_blockers:
            return _collected(
                layer_type="data_graph",
                payload=_empty_data_graph_payload(connection_alias, schema, candidates),
                blockers=validation_blockers,
                policy_rule_ids=("CTX-DATA-MCP-ONLY", "CTX-DATA-READONLY-ALIAS"),
            )
        receipts: list[McpEvidenceReceipt] = []
        evidence_refs: list[str] = []
        table_receipt, table_data, blocker = self._execute(
            operation="tables",
            connection_alias=connection_alias,
            schema=schema,
            table="",
            task_id=task_id,
            run_id=run_id,
        )
        if blocker:
            return self._blocked(connection_alias, schema, candidates, receipts, evidence_refs, blocker)
        receipts.append(table_receipt)  # type: ignore[arg-type]
        evidence_refs.extend(table_receipt.evidence_refs)  # type: ignore[union-attr]
        try:
            catalog_tables = _catalog_rows(
                table_data,
                operation="tables",
                required=("table_schema", "table_name", "table_type"),
            )
        except ValueError as exc:
            return self._blocked(connection_alias, schema, candidates, receipts, evidence_refs, str(exc))
        available = {(str(item["table_schema"]), str(item["table_name"])) for item in catalog_tables}
        missing = [table for table in candidates if (schema, table) not in available]
        if missing:
            return self._blocked(
                connection_alias,
                schema,
                candidates,
                receipts,
                evidence_refs,
                "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: candidate table missing: " + ", ".join(missing),
            )
        normalized_tables: list[dict[str, object]] = []
        for table in candidates:
            operation_rows: dict[str, list[dict[str, object]]] = {}
            for operation in self.OPERATIONS[1:]:
                receipt, data, blocker = self._execute(
                    operation=operation,
                    connection_alias=connection_alias,
                    schema=schema,
                    table=table,
                    task_id=task_id,
                    run_id=run_id,
                )
                if blocker:
                    return self._blocked(connection_alias, schema, candidates, receipts, evidence_refs, blocker)
                receipts.append(receipt)  # type: ignore[arg-type]
                evidence_refs.extend(receipt.evidence_refs)  # type: ignore[union-attr]
                try:
                    operation_rows[operation] = _catalog_rows(
                        data,
                        operation=operation,
                        required=_required_catalog_columns(operation),
                    )
                except ValueError as exc:
                    return self._blocked(connection_alias, schema, candidates, receipts, evidence_refs, str(exc))
            contradiction = _foreign_key_contradiction(operation_rows["foreign_keys"])
            if contradiction:
                return self._blocked(connection_alias, schema, candidates, receipts, evidence_refs, contradiction)
            normalized_tables.append(_normalized_table(table, operation_rows))
        versions = sorted({receipt.source_version for receipt in receipts})
        catalog = {"schema": schema, "tables": normalized_tables}
        payload: dict[str, object] = {
            "schema_version": "data-graph.v1",
            "collector_version": self.COLLECTOR_VERSION,
            "connection_alias": connection_alias,
            "schema": schema,
            "tables": normalized_tables,
            "catalog_hash": content_hash(catalog),
            "mcp_content_versions": versions,
            "mcp_receipts": [receipt.identity_payload() for receipt in receipts],
        }
        fingerprint = content_hash(
            {
                "collector_version": self.COLLECTOR_VERSION,
                "connection_alias": connection_alias,
                "schema": schema,
                "table_scope": list(candidates),
                "catalog_hash": payload["catalog_hash"],
                "mcp_content_versions": versions,
            }
        )
        return _collected(
            layer_type="data_graph",
            payload=payload,
            blockers=(),
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
            policy_rule_ids=("CTX-DATA-MCP-ONLY", "CTX-DATA-CATALOG-CURRENT"),
            source_fingerprint=fingerprint,
        )

    def _execute(
        self,
        *,
        operation: str,
        connection_alias: str,
        schema: str,
        table: str,
        task_id: str,
        run_id: str,
    ) -> tuple[McpEvidenceReceipt | None, Mapping[str, object], str]:
        request = CapabilityRequest.from_dict(
            {
                "schema_version": "his-capability-request.v1",
                "request_id": _request_id("cc-database", task_id, connection_alias, schema, table, operation),
                "capability": "database.inspect",
                "provider": "postgresql",
                "mode": "preview",
                "mutation_level": "L1",
                "authorization": {"explicit": False, "scope": ["database:inspect"]},
                "input": {
                    "connection_alias": connection_alias,
                    "operation": operation,
                    "schema": schema,
                    "table": table,
                },
                "context": {"task_id": task_id, "run_id": run_id},
            }
        )
        try:
            execution = self.runtime.execute(request)
            result = execution.result
        except Exception:
            return None, {}, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: MCP_RUNTIME_UNAVAILABLE."
        if not isinstance(result, CapabilityResult):
            return None, {}, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: MCP_RESULT_INVALID."
        if result.status != "success" or result.changed:
            error_code = str(result.audit.get("error_code") or "DATABASE_INSPECT_FAILED")
            return None, {}, f"BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: {error_code}."
        try:
            receipt = McpEvidenceReceipt.from_capability_result(result)
        except Exception:
            return None, {}, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: MCP_RECEIPT_INVALID."
        expected_prefix = f"postgresql:{connection_alias}:{operation}"
        if not _receipt_matches_prefix(receipt, capability="database.inspect", provider="postgresql", source_prefix=expected_prefix):
            return None, {}, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: stale or mismatched database.inspect MCP receipt."
        if not isinstance(result.data, Mapping):
            return None, {}, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: database catalog result is invalid."
        return receipt, result.data, ""

    def _blocked(
        self,
        connection_alias: str,
        schema: str,
        tables: Sequence[str],
        receipts: Sequence[McpEvidenceReceipt],
        evidence_refs: Sequence[str],
        blocker: str,
    ) -> CollectedContextLayer:
        message = blocker if blocker.startswith("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE") else f"BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: {blocker}"
        payload = _empty_data_graph_payload(connection_alias, schema, tables)
        payload["historical_receipt_count"] = len(receipts)
        return _collected(
            layer_type="data_graph",
            payload=payload,
            blockers=(message,),
            evidence_refs=(),
            policy_rule_ids=("CTX-DATA-MCP-ONLY", "CTX-DATA-CATALOG-CURRENT"),
        )


def _collected(
    *,
    layer_type: str,
    payload: dict[str, object],
    blockers: Sequence[str],
    evidence_refs: Sequence[str] = (),
    policy_rule_ids: Sequence[str] = (),
    source_fingerprint: str | None = None,
) -> CollectedContextLayer:
    unique_blockers = tuple(dict.fromkeys(str(item) for item in blockers if str(item).strip()))
    return CollectedContextLayer(
        layer_type=layer_type,
        status="incomplete" if unique_blockers else "complete",
        payload=payload,
        source_fingerprint=source_fingerprint or content_hash(payload),
        evidence_refs=tuple(evidence_refs),
        policy_rule_ids=tuple(policy_rule_ids),
        blockers=unique_blockers,
    )


def _bounded_relationships(graph: Mapping[str, object], *, known_names: set[str]) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    for branch in list(graph.get("branches") or [])[:_MAX_RELATIONSHIPS]:
        if not isinstance(branch, Mapping):
            continue
        source = str(branch.get("source_project") or branch.get("source") or "").strip()
        target = str(branch.get("target_project") or branch.get("target") or "").strip()
        endpoint = str(branch.get("endpoint") or branch.get("identifier") or "").strip()[:256]
        if source and target and source in known_names and target in known_names:
            relationships.append({"source": source, "target": target, "kind": "service_call", "endpoint": endpoint})
    return sorted(relationships, key=lambda item: (item["source"], item["target"], item["endpoint"]))


def _hashes_from_items(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    hashes: list[str] = []
    for item in value[:64]:
        if not isinstance(item, Mapping):
            continue
        digest = str(item.get("content_hash") or item.get("hash") or "").strip()
        if digest and digest not in hashes:
            hashes.append(digest)
    return hashes


def _bounded_scope(value: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in sorted(value)[:32]:
        raw = value[key]
        name = str(key)[:128]
        if isinstance(raw, str):
            result[name] = raw[:4096]
        elif isinstance(raw, bool) or raw is None or isinstance(raw, (int, float)):
            result[name] = raw
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            result[name] = [str(item)[:1024] for item in raw[:32]]
    return result


def _target_paths_for_project(discovery: TechnicalContextDiscovery, *, project_name: str) -> list[str]:
    targets = list(discovery.explicit_allowed_paths)
    if not targets:
        targets = [
            str(node.path)
            for node in discovery.demand_discovery.graph.nodes
            if node.project == project_name
        ]
    result: list[str] = []
    for raw in targets:
        normalized = _normalized_relative_path(raw)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _normalized_relative_path(raw: object) -> str:
    value = str(raw or "").replace("\\", "/").strip()
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        return ""
    normalized = path.as_posix().lstrip("./")
    return normalized if normalized and normalized != "." else ""


def _safe_project_file(project_root: Path, relative_path: str) -> Path | None:
    normalized = _normalized_relative_path(relative_path)
    if not normalized:
        return None
    candidate = project_root / normalized
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        candidate.resolve().relative_to(project_root.resolve())
        stat = candidate.stat()
    except (OSError, ValueError):
        return None
    if stat.st_size > _MAX_FILE_BYTES:
        return None
    return candidate


def _display_path(project_name: str, relative_path: str, *, multi_project: bool) -> str:
    return f"{project_name}/{relative_path}" if multi_project else relative_path


def _file_fact(path: Path, display_path: str) -> dict[str, object]:
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": display_path, "content_hash": digest, "size_bytes": path.stat().st_size}


def _find_relevant_tests(project_root: Path, target_path: str) -> list[str]:
    stem = Path(target_path).stem.lower()
    candidates: list[str] = []
    scanned = 0
    for current_root, dirs, files in os.walk(project_root, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs
            if name not in {".git", "node_modules", "dist", "build", "target", ".venv", "data", "runs"}
            and not (Path(current_root) / name).is_symlink()
        )
        for name in sorted(files):
            scanned += 1
            if scanned > 4000:
                return candidates
            lower = name.lower()
            if not (any(lower.endswith(suffix.lower()) for suffix in _TEST_SUFFIXES) or "test" in Path(current_root).parts):
                continue
            if stem and stem not in lower and stem not in str(Path(current_root).relative_to(project_root)).lower():
                continue
            candidate = Path(current_root) / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            candidates.append(candidate.relative_to(project_root).as_posix())
            if len(candidates) >= _MAX_TESTS_PER_PROJECT:
                return candidates
    return candidates


def _bounded_code_edges(discovery: TechnicalContextDiscovery, *, projects: set[str]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for edge in discovery.demand_discovery.graph.edges[:_MAX_RELATIONSHIPS]:
        source = _normalized_relative_path(edge.source_path)
        target = _normalized_relative_path(edge.target_path)
        if not source or not target:
            continue
        edges.append(
            {
                "kind": str(edge.kind)[:64],
                "identifier": str(edge.identifier)[:256],
                "source_path": source,
                "target_path": target,
            }
        )
    return sorted(edges, key=lambda item: (item["source_path"], item["target_path"], item["identifier"]))


def _request_id(prefix: str, *parts: str) -> str:
    digest = content_hash({"prefix": prefix, "parts": list(parts)}).removeprefix("sha256:")[:24]
    return f"{prefix}-{digest}"


def _execute_receipt(
    runtime: McpCapabilityRuntime,
    request: CapabilityRequest,
    *,
    expected_source: str,
) -> tuple[McpEvidenceReceipt | None, str]:
    try:
        execution = runtime.execute(request)
        result = execution.result
    except Exception:
        return None, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: MCP_RUNTIME_UNAVAILABLE."
    if not isinstance(result, CapabilityResult):
        return None, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: MCP_RESULT_INVALID."
    if result.status != "success" or result.changed:
        error_code = str(result.audit.get("error_code") or "MCP_READ_FAILED")
        return None, f"BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: {error_code}."
    try:
        receipt = McpEvidenceReceipt.from_capability_result(result)
    except Exception:
        return None, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: MCP_RECEIPT_INVALID."
    if not _receipt_matches(
        receipt,
        capability=request.capability,
        provider=request.provider,
        source_identity=expected_source,
    ):
        return None, "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: stale or mismatched MCP receipt."
    return receipt, ""


def _receipt_matches(
    receipt: McpEvidenceReceipt | None,
    *,
    capability: str,
    provider: str,
    source_identity: str,
) -> bool:
    return bool(
        isinstance(receipt, McpEvidenceReceipt)
        and receipt.execution_kind == "mcp"
        and receipt.capability == capability
        and receipt.provider == provider
        and receipt.source_identity == source_identity
        and receipt.is_current
        and receipt.evidence_refs
    )


def _receipt_matches_prefix(
    receipt: McpEvidenceReceipt | None,
    *,
    capability: str,
    provider: str,
    source_prefix: str,
) -> bool:
    return bool(
        isinstance(receipt, McpEvidenceReceipt)
        and receipt.execution_kind == "mcp"
        and receipt.capability == capability
        and receipt.provider == provider
        and receipt.source_identity.startswith(source_prefix)
        and receipt.is_current
        and receipt.evidence_refs
    )


def _database_scope_blockers(connection_alias: str, schema: str, tables: Sequence[str]) -> tuple[str, ...]:
    identifier = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
    blockers: list[str] = []
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}_readonly", connection_alias):
        blockers.append("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: database connection alias must end in _readonly.")
    if identifier.fullmatch(schema) is None:
        blockers.append("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: database schema is invalid.")
    if not tables:
        blockers.append("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: candidate table scope is empty.")
    elif len(tables) > 32 or any(identifier.fullmatch(table) is None for table in tables):
        blockers.append("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: candidate table scope is invalid.")
    return tuple(blockers)


def _empty_data_graph_payload(connection_alias: str, schema: str, tables: Sequence[str]) -> dict[str, object]:
    return {
        "schema_version": "data-graph.v1",
        "collector_version": DataGraphCollector.COLLECTOR_VERSION,
        "connection_alias": connection_alias,
        "schema": schema,
        "table_scope": list(tables),
        "tables": [],
        "mcp_content_versions": [],
        "mcp_receipts": [],
    }


def _required_catalog_columns(operation: str) -> tuple[str, ...]:
    values = {
        "columns": (
            "table_schema", "table_name", "ordinal_position", "column_name", "data_type",
            "is_nullable", "column_default",
        ),
        "constraints": ("constraint_name", "constraint_type", "column_name", "ordinal_position"),
        "indexes": ("schemaname", "tablename", "indexname", "indexdef"),
        "foreign_keys": (
            "constraint_name", "table_schema", "table_name", "column_name",
            "foreign_table_schema", "foreign_table_name", "foreign_column_name",
        ),
    }
    return values[operation]


def _catalog_rows(
    data: Mapping[str, object],
    *,
    operation: str,
    required: Sequence[str],
) -> list[dict[str, object]]:
    if data.get("operation") != operation:
        raise ValueError("database catalog operation mismatch.")
    columns = data.get("columns")
    rows = data.get("rows")
    if (
        not isinstance(columns, (list, tuple))
        or any(not isinstance(item, str) for item in columns)
        or not set(required).issubset(columns)
        or not isinstance(rows, (list, tuple))
    ):
        raise ValueError(f"database catalog {operation} metadata is partial.")
    result: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != len(columns):
            raise ValueError(f"database catalog {operation} row shape is invalid.")
        item = {str(columns[index]): row[index] for index in range(len(columns))}
        result.append({name: item[name] for name in required})
    return result


def _normalized_table(table: str, rows: Mapping[str, list[dict[str, object]]]) -> dict[str, object]:
    return {
        "name": table,
        "columns": [
            {
                "name": str(item["column_name"]),
                "type": str(item["data_type"]),
                "nullable": str(item["is_nullable"]).upper() == "YES",
                "ordinal": int(item["ordinal_position"]),
                "default_class": _default_class(item["column_default"]),
            }
            for item in rows["columns"]
        ],
        "constraints": [
            {
                "name": str(item["constraint_name"]),
                "type": str(item["constraint_type"]),
                "column": "" if item["column_name"] is None else str(item["column_name"]),
                "ordinal": 0 if item["ordinal_position"] is None else int(item["ordinal_position"]),
            }
            for item in rows["constraints"]
        ],
        "indexes": [
            {
                "name": str(item["indexname"]),
                "definition": str(item["indexdef"])[:4096],
            }
            for item in rows["indexes"]
        ],
        "foreign_keys": [
            {
                "name": str(item["constraint_name"]),
                "column": str(item["column_name"]),
                "target_schema": str(item["foreign_table_schema"]),
                "target_table": str(item["foreign_table_name"]),
                "target_column": str(item["foreign_column_name"]),
            }
            for item in rows["foreign_keys"]
        ],
    }


def _default_class(value: object) -> str:
    if value is None or str(value).strip() == "":
        return "none"
    text = str(value).strip().casefold()
    if text.startswith("nextval(") or "(" in text or "::" in text:
        return "expression"
    return "literal"


def _foreign_key_contradiction(rows: Sequence[Mapping[str, object]]) -> str:
    targets: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    for item in rows:
        key = (str(item["constraint_name"]), str(item["column_name"]))
        target = (
            str(item["foreign_table_schema"]),
            str(item["foreign_table_name"]),
            str(item["foreign_column_name"]),
        )
        targets.setdefault(key, set()).add(target)
    if any(len(values) > 1 for values in targets.values()):
        return "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: contradictory foreign-key relationships."
    return ""
