"""Evidence-driven service ownership and dependency inspection.

This module is intentionally static.  It reads repository names, build
manifests and public API files to establish the service boundary before a
change contract is created.  It never treats a project name as proof of a
runtime call and never edits a repository.
"""

from __future__ import annotations

import copy
import hashlib
import re
import os
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping


SERVICE_ARCHITECTURE_SCHEMA_VERSION = "service-architecture.v1"
RIGHT_PANEL_CONTRACT_PROPOSAL_SCHEMA_VERSION = "right-panel-contract-proposal.v1"
_BUILD_FILE_NAMES = {
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "pom.xml",
    "package.json",
}
_ROLE_BY_PREFIX = (
    ("df-web-", "frontend"),
    ("df-bff-", "bff"),
    ("df-mic-", "service"),
    ("mic-", "service"),
)
_SCAN_EXCLUDED_DIRS = {
    ".git", ".gradle", ".idea", ".venv", "node_modules", "dist", "build",
    "target", "out", "coverage", "vendor", "public", "static", "generated",
}
_MAX_SOURCE_FILES = 2400
_MAX_API_CONTRACTS = 1024
_PROJECT_EVIDENCE_CACHE_VERSION = "service-evidence-cache.v2"
_PROJECT_EVIDENCE_CACHE_LIMIT = 64
_PROJECT_EVIDENCE_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_GIT_EVIDENCE_TIMEOUT_SECONDS = 3


def infer_service_role(name: str, path: str = "") -> str:
    value = str(name or Path(path).name or "").strip().lower()
    for prefix, role in _ROLE_BY_PREFIX:
        if value.startswith(prefix):
            return role
    if "api" in value:
        return "api"
    return "unknown"


def build_service_architecture_catalog(
    *,
    root: str | Path,
    selected_projects: list[Mapping[str, Any]],
    include_workspace_projects: bool = True,
) -> dict[str, Any]:
    """Build a deterministic catalogue from local repository evidence.

    The catalogue includes repositories outside the selected change scope when
    they are present below ``root``.  This is important for dependency
    decisions: an evidence-only repository must be visible even when it is not
    a candidate patch target.
    """

    root_path = Path(root).expanduser().resolve()
    projects = _merge_projects(
        root_path,
        selected_projects,
        include_workspace_projects=include_workspace_projects,
    )
    project_names = {item["name"] for item in projects}
    aliases = _project_aliases(project_names)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    cache_hits = 0
    cache_misses = 0
    for project in projects:
        name = project["name"]
        path = Path(project["path"])
        role = str(project.get("role") or infer_service_role(name, str(path)))
        static_evidence, cache_hit = _project_static_evidence(path)
        cache_hits += int(cache_hit)
        cache_misses += int(not cache_hit)
        build_evidence = static_evidence["build_evidence"]
        public_api_evidence = static_evidence["public_api_evidence"]
        public_api_symbols = static_evidence["public_api_symbols"]
        public_api_definitions = static_evidence["public_api_definitions"]
        api_usage_evidence = static_evidence["api_usage_evidence"]
        data_contract_evidence = static_evidence["data_contract_evidence"]
        http_call_evidence = static_evidence["http_call_evidence"]
        verification_candidates = static_evidence["verification_candidates"]
        public_api_routes = static_evidence["public_api_routes"]
        public_api_contracts = static_evidence["public_api_contracts"]
        dependencies = _dependency_evidence(
            project_name=name,
            project_path=path,
            known_names=project_names,
            aliases=aliases,
        )
        nodes.append(
            {
                "project": name,
                "path": str(path),
                "role": role,
                "exists": bool(path.is_dir()),
                "scope": str(project.get("selection_scope") or "evidence_only"),
                "build_evidence": build_evidence,
                "public_api_evidence": public_api_evidence,
                "public_api_symbols": public_api_symbols,
                "public_api_definitions": public_api_definitions,
                "api_usage_evidence": api_usage_evidence,
                "data_contract_evidence": data_contract_evidence,
                "http_call_evidence": http_call_evidence,
                "verification_candidates": verification_candidates,
                "public_api_routes": public_api_routes,
                "public_api_contracts": public_api_contracts,
                "dependency_names": sorted({item["target_project"] for item in dependencies}),
                "worktree_evidence": _git_worktree_evidence(path),
            }
        )
        edges.extend(dependencies)

    dependency_findings = _dependency_findings(nodes=nodes, edges=edges)
    return {
        "schema_version": SERVICE_ARCHITECTURE_SCHEMA_VERSION,
        "status": "evidence_ready" if nodes else "unavailable",
        "nodes": sorted(nodes, key=lambda item: str(item.get("project") or "")),
        "edges": sorted(
            edges,
            key=lambda item: (
                str(item.get("source_project") or ""),
                str(item.get("target_project") or ""),
            ),
        ),
        "dependency_findings": dependency_findings,
        "performance": {
            "project_evidence_cache": "git_fingerprint",
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_limit": _PROJECT_EVIDENCE_CACHE_LIMIT,
        },
        "policy": {
            "frontend": "通过源码请求和服务图确认后端入口，不把构建依赖当成 HTTP 调用证据。",
            "bff": "BFF 可以聚合底层服务公共 API；禁止用跨库表读取替代已声明的公共 API。",
            "service": "业务微服务只能使用构建文件中可证明存在的公共 API/模块依赖；缺少依赖时不推断直连。",
            "unknown": "未知角色和未解析依赖只能作为证据，不得进入自动 patch 合同。",
        },
    }


def _project_static_evidence(path: Path) -> tuple[dict[str, Any], bool]:
    """Reuse static evidence only when the repository fingerprint is stable.

    The cache is intentionally process-local and bounded.  Non-Git paths are
    never cached because a directory mtime does not prove that file contents
    are unchanged.  For Git repositories, the fingerprint includes HEAD,
    porcelain status and the size/mtime/content digest of changed files, so a
    local edit invalidates the evidence before a patch contract is built.
    """

    fingerprint = _project_evidence_fingerprint(path)
    if not fingerprint:
        return _scan_project_static_evidence(path), False
    key = (str(path.expanduser().resolve()), fingerprint, _PROJECT_EVIDENCE_CACHE_VERSION)
    cached = _PROJECT_EVIDENCE_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached), True
    evidence = _scan_project_static_evidence(path)
    if len(_PROJECT_EVIDENCE_CACHE) >= _PROJECT_EVIDENCE_CACHE_LIMIT:
        _PROJECT_EVIDENCE_CACHE.pop(next(iter(_PROJECT_EVIDENCE_CACHE)))
    _PROJECT_EVIDENCE_CACHE[key] = copy.deepcopy(evidence)
    return evidence, False


def _scan_project_static_evidence(path: Path) -> dict[str, Any]:
    return {
        "build_evidence": _build_evidence(path),
        "public_api_evidence": _public_api_evidence(path),
        "public_api_symbols": _public_api_symbols(path),
        "public_api_definitions": _public_api_definitions(path),
        "api_usage_evidence": _api_usage_evidence(path),
        "data_contract_evidence": _data_contract_evidence(path),
        "http_call_evidence": _http_call_evidence(path),
        "verification_candidates": _verification_candidates(path),
        "public_api_routes": _public_api_routes(path),
        "public_api_contracts": _public_api_contracts(path),
    }


def _project_evidence_fingerprint(path: Path) -> str:
    """Return a cheap content-sensitive fingerprint for a Git repository."""

    if not path.is_dir():
        return ""
    try:
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if root_result.returncode != 0:
            return ""
        repo_root = Path((root_result.stdout or "").strip()).resolve()
        status_result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=_GIT_EVIDENCE_TIMEOUT_SECONDS,
            check=False,
        )
        if status_result.returncode != 0:
            return ""
        digest = hashlib.sha256()
        digest.update(_PROJECT_EVIDENCE_CACHE_VERSION.encode("utf-8"))
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=_GIT_EVIDENCE_TIMEOUT_SECONDS,
            check=False,
        )
        digest.update((head_result.stdout or "").strip().encode("utf-8"))
        status_text = status_result.stdout or ""
        digest.update(status_text.encode("utf-8", errors="ignore"))
        for line in status_text.splitlines():
            raw_path = line[3:] if len(line) >= 4 else ""
            raw_path = raw_path.split(" -> ", 1)[-1].strip()
            if not raw_path:
                continue
            candidate = (repo_root / raw_path).resolve()
            try:
                if not candidate.is_file() or not candidate.is_relative_to(repo_root):
                    continue
                stat = candidate.stat()
                digest.update(f"{raw_path}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
                if stat.st_size <= 2 * 1024 * 1024:
                    digest.update(candidate.read_bytes())
            except (OSError, ValueError):
                digest.update(f"{raw_path}:unreadable".encode("utf-8"))
        return digest.hexdigest()
    except Exception:
        # Fingerprinting is an optimization only.  If a restricted runner
        # blocks Git or subprocesses, fall back to a fresh scan.
        return ""


def recommend_right_panel_architecture(
    *,
    catalog: Mapping[str, Any] | None,
    bff_project: str = "df-bff-jichufw",
    consumer_project: str = "df-mic-yibaogl",
    web_project: str = "df-web-yibaogl",
    drug_source_project: str = "df-mic-yaokufang",
    charge_source_project: str = "df-mic-jichufw",
) -> dict[str, Any]:
    """Choose a source boundary from repository evidence, not user guesses."""

    data = catalog or {}
    nodes = {str(item.get("project") or ""): item for item in data.get("nodes") or []}
    bff = nodes.get(bff_project) or {}
    consumer = nodes.get(consumer_project) or {}
    web = nodes.get(web_project) or {}
    drug_source = nodes.get(drug_source_project) or {}
    charge_source = nodes.get(charge_source_project) or {}
    bff_api_text = " ".join(
        [
            *[str(item) for item in bff.get("build_evidence") or []],
            *[str(item) for item in bff.get("public_api_evidence") or []],
            *[str(item) for item in bff.get("public_api_symbols") or []],
        ]
    ).lower()
    bff_routes = [str(item) for item in bff.get("public_api_routes") or []]
    bff_contracts = [
        dict(item) for item in bff.get("public_api_contracts") or []
        if isinstance(item, Mapping)
    ]
    existing_charge_routes = [
        route for route in bff_routes
        if any(token in route.lower() for token in ("shoufeixm", "shoufei"))
    ]
    existing_drug_routes = [
        route for route in bff_routes
        if any(token in route.lower() for token in ("yaopin", "yaopinzd", "yaopinmu"))
    ]
    existing_charge_contracts = [
        item for item in bff_contracts
        if any(token in str(item.get("route") or "").lower() for token in ("shoufeixm", "shoufei"))
    ]
    existing_drug_contracts = [
        item for item in bff_contracts
        if any(token in str(item.get("route") or "").lower() for token in ("yaopin", "yaopinzd", "yaopinmu"))
    ]
    consumer_contracts = [
        dict(item) for item in consumer.get("public_api_contracts") or []
        if isinstance(item, Mapping)
        and any(
            token in str(item.get("route") or "").lower()
            for token in ("yibaospxmwh", "yiyuanmulu")
        )
    ]
    bff_api_usage = [
        dict(item) for item in bff.get("api_usage_evidence") or []
        if isinstance(item, Mapping)
    ]
    drug_api_definitions = [
        dict(item) for item in drug_source.get("public_api_definitions") or []
        if isinstance(item, Mapping)
        and str(item.get("api") or "").lower() in {"yaopinzapi", "yaopinzdapi"}
    ]
    drug_api_usage = [
        dict(item) for item in bff_api_usage
        if str(item.get("api") or "").lower() in {"yaopinzapi", "yaopinzdapi"}
    ]
    charge_api_definitions = [
        dict(item) for item in charge_source.get("public_api_definitions") or []
        if isinstance(item, Mapping)
        and str(item.get("api") or "").lower() == "shoufeixmapi"
    ]
    frontend_http_calls = [
        dict(item) for item in web.get("http_call_evidence") or []
        if isinstance(item, Mapping)
    ]
    frontend_drug_routes = [
        item for item in frontend_http_calls
        if re.search(
            r"/(?:ykf-jichuyw|winbff-[^/]*|yb-yibaogl|agg-yibaogl)/.*(?:yaopin|yaopinzd|yaopinmu)",
            str(item.get("endpoint") or ""),
            flags=re.IGNORECASE,
        )
    ]
    frontend_drug_routes = sorted(
        frontend_drug_routes,
        key=lambda item: (
            0 if "ykf-jichuyw" in str(item.get("endpoint") or "").lower() else 1,
            str(item.get("endpoint") or ""),
        ),
    )[:64]
    frontend_charge_routes = [
        item for item in frontend_http_calls
        if re.search(
            r"/(?:gy-jichufw|winbff-jichufw)/.*shoufeixm",
            str(item.get("endpoint") or ""),
            flags=re.IGNORECASE,
        )
    ]
    frontend_charge_routes = sorted(
        frontend_charge_routes,
        key=lambda item: str(item.get("endpoint") or ""),
    )[:64]
    # A typed API definition or an internal BFF call is not an HTTP contract.
    # In particular, the category tree may call YaoPinZdApi while the right
    # panel still has no drug-directory route.  Only controller contracts are
    # sufficient to mark a source as exposed through this BFF.
    has_charge_api = bool(existing_charge_contracts)
    has_drug_api = bool(existing_drug_contracts)
    drug_api_definition_proven = bool(drug_api_definitions)
    bff_drug_api_usage_proven = bool(drug_api_usage)
    consumer_worktree = consumer.get("worktree_evidence") or {}
    consumer_dirty_paths = [str(item) for item in consumer_worktree.get("changed_paths") or []]
    relevant_dirty_tokens = (
        "yibaospxmwh", "yibaomldz", "yaopin", "zhenliao", "yibaoxxxz",
        "controller", "service", "repository", ".sql",
    )
    dirty_existing_implementation = (
        consumer_worktree.get("status") == "dirty"
        and any(any(token in path.lower() for token in relevant_dirty_tokens) for path in consumer_dirty_paths)
    )
    direct_drug_dependency = any(
        edge.get("source_project") == consumer_project
        and edge.get("target_project") == drug_source_project
        for edge in data.get("edges") or []
    )

    options = [
        {
            "id": "bff_raw_sources_yibaogl_enrichment",
            "label": "BFF 提供原始目录，医保服务负责对照和审批属性",
            "status": "recommended" if has_charge_api and has_drug_api else "candidate",
            "owner_projects": [bff_project, consumer_project],
            "evidence": [
                "BFF 分类树同时使用药品字典 API 和 ShouFeiXmApi。",
                "医保服务已有多对照、字典补充和审批属性逻辑。",
                "现有 BFF 收费项目分页路由仅作为收费项目来源证据，不能直接代替药品/收费项目统一目录契约。",
            ],
            "contract_evidence": {
                "charge": existing_charge_contracts[:8],
                "drug": existing_drug_contracts[:8],
                "consumer": consumer_contracts[:8],
            },
            "auto_collected_evidence": {
                "drug_api_definition": drug_api_definitions[:16],
                "bff_drug_api_usage": drug_api_usage[:16],
                "charge_api_definition": charge_api_definitions[:16],
                "frontend_drug_routes": frontend_drug_routes[:16],
                "frontend_charge_routes": frontend_charge_routes[:16],
                "consumer_data_contracts": list(consumer.get("data_contract_evidence") or [])[:48],
            },
            "contract_gap": [
                "已有收费项目 BFF 分页契约只返回收费项目，不证明药品来源和统一分页响应。",
                "当前未发现 BFF 药品目录 HTTP 路由；分类树中的 YaoPinZdApi 调用不能直接当作右侧分页接口。",
                "尚未证明能承载医保多对照 1:N、四个审批标志和前端一条投影记录的统一响应 DTO。",
            ],
            "rule": "禁止 yibaogl 直接查询底层表或直接依赖 mic-yaokufang；原始目录通过 BFF 公共 API 进入业务聚合。",
        },
        {
            "id": "new_bff_unified_directory_api",
            "label": "新增 BFF 统一目录读取接口",
            "status": "candidate",
            "owner_projects": [bff_project, consumer_project],
            "evidence": ["当现有 BFF 原始接口无法满足药品、收费项目和分页契约时使用。"],
            "rule": "新接口仍需保留 yibaogl 的医保多对照和审批属性语义，不得丢失 1:N 数据。",
        },
        {
            "id": "direct_cross_schema_or_drug_service",
            "label": "yibaogl 直接查底层表或直连药房服务",
            "status": "rejected",
            "owner_projects": [consumer_project],
            "evidence": [
                "当前消费者构建证据未证明存在 mic-yaokufang 公共 API 依赖。",
                "直接查 gy_shoufeixm 会绕过基础服务边界。",
            ],
            "rule": "不得进入改动合同。",
        },
    ]
    evidence = {
        "bff_project_exists": bool(bff.get("exists")),
        "consumer_project_exists": bool(consumer.get("exists")),
        "charge_api_proven": has_charge_api,
        "drug_api_proven": has_drug_api,
        "existing_charge_routes": existing_charge_routes[:16],
        "existing_drug_routes": existing_drug_routes[:16],
        "existing_charge_contracts": existing_charge_contracts[:12],
        "existing_drug_contracts": existing_drug_contracts[:12],
        "drug_api_definitions": drug_api_definitions[:24],
        "bff_drug_api_usage": drug_api_usage[:24],
        "charge_api_definitions": charge_api_definitions[:24],
        "frontend_drug_routes": frontend_drug_routes[:32],
        "frontend_charge_routes": frontend_charge_routes[:32],
        "verification_candidates": {
            project: list(nodes.get(project, {}).get("verification_candidates") or [])[:16]
            for project in (web_project, bff_project, consumer_project, drug_source_project, charge_source_project)
            if nodes.get(project)
        },
        "consumer_contracts": consumer_contracts[:12],
        "consumer_data_contracts": list(consumer.get("data_contract_evidence") or [])[:64],
        "contract_gap": [
            "charge_only_existing_contract",
            "missing_drug_http_route",
            "missing_unified_mapping_projection_contract",
        ],
        "consumer_build_evidence": consumer.get("build_evidence") or [],
    }
    if dirty_existing_implementation:
        evidence["contract_gap"].append("existing_consumer_dirty_implementation_requires_reconciliation")
    return {
        "status": (
            "needs_reconciliation"
            if dirty_existing_implementation
            else ("auto_resolved" if options[0]["status"] == "recommended" else "needs_api_evidence")
        ),
        "recommended_option_id": options[0]["id"],
        "consumer_project": consumer_project,
        "source_projects": [drug_source_project, charge_source_project],
        "direct_drug_dependency_proven": direct_drug_dependency,
        "evidence": {
            **evidence,
            "drug_api_definition_proven": drug_api_definition_proven,
            "bff_drug_api_usage_proven": bff_drug_api_usage_proven,
            "existing_consumer_dirty_implementation": dirty_existing_implementation,
            "consumer_worktree": consumer_worktree,
        },
        "contract_proposal": build_right_panel_contract_proposal(evidence=evidence),
        "options": options,
        "decision_rule": "Harness 先根据本地构建和公共 API 证据选择最窄安全边界；只有候选方案同等且无法区分时才请求用户选择。",
    }


def build_right_panel_contract_proposal(*, evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a reviewable, non-executable contract proposal from evidence.

    The proposal deliberately leaves a new BFF URL and unified DTO undefined
    when the repository does not prove them.  It is therefore useful for a
    reviewer and for the later worktree planner, but it cannot authorize a
    patch by itself.
    """

    data = evidence or {}
    charge_contracts = [
        item for item in data.get("existing_charge_contracts") or []
        if isinstance(item, Mapping)
    ]
    consumer_contracts = [
        item for item in data.get("consumer_contracts") or []
        if isinstance(item, Mapping)
    ]
    drug_contracts = [
        item for item in data.get("existing_drug_contracts") or []
        if isinstance(item, Mapping)
    ]
    drug_api_definitions = [
        item for item in data.get("drug_api_definitions") or []
        if isinstance(item, Mapping)
    ]
    bff_drug_api_usage = [
        item for item in data.get("bff_drug_api_usage") or []
        if isinstance(item, Mapping)
    ]
    consumer_data_contracts = [
        item for item in data.get("consumer_data_contracts") or []
        if isinstance(item, Mapping)
    ]
    frontend_drug_routes = [
        item for item in data.get("frontend_drug_routes") or []
        if isinstance(item, Mapping)
    ]
    verification_candidates = data.get("verification_candidates") or {}
    charge_contract = _select_contract(
        charge_contracts,
        route_tokens=("getandshoufeixmjgpage",),
    )
    consumer_contract = _select_contract(
        consumer_contracts,
        route_tokens=("getyiyuanmulupage",),
    )
    charge_row_types = list((charge_contract or {}).get("response_types") or [])
    consumer_params = list((consumer_contract or {}).get("request_parameters") or [])
    missing = list(data.get("contract_gap") or [])
    if not charge_contract:
        missing.append("missing_charge_source_contract")
    if not consumer_contract:
        missing.append("missing_current_consumer_contract")
    if not drug_contracts:
        missing.append("missing_drug_http_contract")
    if frontend_drug_routes and not drug_contracts:
        missing.append("frontend_drug_route_ownership_unresolved")
    missing = sorted(set(str(item) for item in missing if str(item).strip()))
    projection_groups = {
        str(group)
        for item in consumer_data_contracts
        for group in item.get("groups") or []
    }
    auto_collected_evidence = [
        {
            "id": "drug_api_definition",
            "label": "药品公共 API 的方法、参数和返回 DTO",
            "status": "evidence_collected" if drug_api_definitions else "not_found",
            "evidence": drug_api_definitions[:32],
            "next_action": "无需用户描述；继续由 Harness 检查 BFF 是否存在 HTTP 暴露。",
        },
        {
            "id": "bff_drug_api_usage",
            "label": "BFF 对药品公共 API 的实际调用",
            "status": "evidence_collected" if bff_drug_api_usage else "not_found",
            "evidence": bff_drug_api_usage[:32],
            "next_action": "无需用户描述；调用证据只能证明分类树/服务调用，不能替代 HTTP 路由证据。",
        },
        {
            "id": "drug_bff_http_exposure",
            "label": "BFF 对药品 API 的 HTTP 暴露",
            "status": "evidence_collected" if drug_contracts else "not_proven",
            "evidence": drug_contracts[:32],
            "next_action": "继续扫描 Controller、前端请求和网关路由；没有证据时保持阻断，不要求用户猜路径。",
        },
        {
            "id": "frontend_drug_gateway_route",
            "label": "前端现有药品字典请求路径",
            "status": "evidence_collected" if frontend_drug_routes else "not_found",
            "evidence": frontend_drug_routes[:32],
            "next_action": "已找到的前端路径只证明网关入口，不自动等同于 BFF；Harness 继续核对路由归属。",
        },
        {
            "id": "yibaogl_projection_boundary",
            "label": "医保服务目录投影、对照和审批字段边界",
            "status": "partially_collected" if projection_groups else "not_found",
            "evidence": consumer_data_contracts[:64],
            "groups": sorted(projection_groups),
            "next_action": "继续定位 DTO、Mapper/Repository 和保存调用；字段命中不等于语义已证明。",
        },
        {
            "id": "repository_verification",
            "label": "各仓库编译、接口测试和运行时验证",
            "status": "pending_execution",
            "evidence": verification_candidates or list(data.get("consumer_build_evidence") or []),
            "next_action": "Harness 自动从构建文件生成候选命令并执行；运行时不可用时记录环境原因，不要求用户提供命令。",
        },
    ]
    remaining_evidence = [
        item["label"] for item in auto_collected_evidence
        if item["status"] in {"not_found", "not_proven", "partially_collected", "pending_execution"}
    ]
    return {
        "schema_version": RIGHT_PANEL_CONTRACT_PROPOSAL_SCHEMA_VERSION,
        "status": "review_required" if missing else "evidence_ready",
        "decision": "new_bff_unified_directory_contract_required",
        "write_ready": False,
        "route": {
            "status": "not_proven",
            "candidate_http_method": "POST",
            "candidate_path": None,
            "basis": [
                "当前医保医院目录接口为 POST。",
                "现有收费项目 BFF 分页接口为 POST。",
            ],
            "rule": "不得根据相似路径自动拼接新 BFF URL；必须由 Controller/前端调用契约共同证明。",
        },
        "current_consumer_contract": _contract_snapshot(consumer_contract),
        "source_contracts": {
            "charge": {
                "status": "existing_source_contract" if charge_contract else "not_proven",
                "owner_project": "df-mic-jichufw",
                "public_api": "ShouFeiXmApi",
                "bff_contract": _contract_snapshot(charge_contract),
                "row_types": charge_row_types,
            },
            "drug": {
                "status": "missing_bff_http_contract" if not drug_contracts else "existing_source_contract",
                "owner_project": "df-mic-yaokufang",
                "public_api": "YaoPinZdApi" if (data.get("drug_api_proven") or data.get("drug_api_definition_proven")) else None,
                "bff_contract": _contract_snapshot(_select_contract(drug_contracts, route_tokens=())),
                "api_definitions": drug_api_definitions[:16],
                "frontend_routes": frontend_drug_routes[:16],
                "row_types": list(
                    (_select_contract(drug_contracts, route_tokens=()) or {}).get("response_types") or []
                ),
                "category_tree_only_evidence": (
                    bool(data.get("drug_api_definition_proven") or data.get("bff_drug_api_usage_proven"))
                    and not drug_contracts
                ),
            },
        },
        "request_contract": {
            "status": "candidate_from_existing_consumer" if consumer_params else "not_proven",
            "parameters": consumer_params,
            "must_preserve": [
                "医疗保险、院区、分类、项目类型和关键字筛选语义",
                "pageIndex/pageSize 分页语义",
                "药品/卫材/诊疗项目统一列表的稳定排序语义",
            ],
        },
        "response_contract": {
            "status": "not_proven",
            "raw_sources": ["charge", "drug"],
            "projection_owner": "df-mic-yibaogl",
            "must_preserve": [
                "收费项目和药品/卫材原始来源可区分",
                "一条医院项目对应多条医保对照时不得在原始层静默覆盖",
                "前端展示聚合与多条数据库逻辑记录保存语义分离",
                "menzhenbz/zhuyuanbz 先严格等于 1，再判断 zifeibz/bushangchuanbz",
            ],
            "required_semantics": [
                "source_type/source_id/source_name/source_code",
                "医保对照编码、开始时间、结束时间及重复记录处理",
                "四个审批标志的严格组合结果",
                "total/pageIndex/pageSize 与混合来源分页一致",
            ],
        },
        "auto_collected_evidence": auto_collected_evidence,
        "remaining_evidence_before_worktree": remaining_evidence,
        "required_evidence_before_worktree": [
            "YaoPinZdApi 的实际方法、请求参数和返回 DTO，并证明可由 BFF 暴露",
            "统一原始目录接口的 Controller、Service、DTO 和分页实现",
            "yibaogl 目录投影 DTO 与多条对照/两条审批逻辑记录的保存边界",
            "逐仓库编译或接口级测试命令，以及真实运行时验证结果",
        ],
        "blocking_reasons": missing,
    }


def _select_contract(contracts: list[Mapping[str, Any]], *, route_tokens: tuple[str, ...]) -> Mapping[str, Any] | None:
    if not contracts:
        return None
    if not route_tokens:
        return contracts[0]
    return next(
        (
            item for item in contracts
            if all(token in str(item.get("route") or "").lower() for token in route_tokens)
        ),
        contracts[0],
    )


def _contract_snapshot(contract: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not contract:
        return None
    return {
        "route": contract.get("route"),
        "http_method": contract.get("http_method"),
        "controller": contract.get("controller"),
        "method": contract.get("method"),
        "return_type": contract.get("return_type"),
        "request_types": list(contract.get("request_types") or []),
        "request_parameters": list(contract.get("request_parameters") or []),
        "response_types": list(contract.get("response_types") or []),
        "service_impl": contract.get("service_impl"),
        "upstream_api_calls": list(contract.get("upstream_api_calls") or []),
        "evidence_paths": list(contract.get("evidence_paths") or [])
        + list(contract.get("service_evidence_paths") or []),
    }


def _merge_projects(
    root: Path,
    selected_projects: list[Mapping[str, Any]],
    *,
    include_workspace_projects: bool = True,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in selected_projects or []:
        name = str(item.get("name") or Path(str(item.get("path") or "")).name).strip()
        path = str(item.get("path") or "").strip()
        if name and path:
            merged[name] = {**dict(item), "name": name, "path": path}
    if include_workspace_projects and root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and not child.name.startswith(".") and (child.name.startswith("df-") or child.name.startswith("mic-")):
                merged.setdefault(
                    child.name,
                    {
                        "name": child.name,
                        "path": str(child),
                        "role": infer_service_role(child.name, str(child)),
                        "exists": True,
                        "selection_scope": "architecture_evidence",
                    },
                )
    return list(merged.values())


def _project_aliases(names: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for name in names:
        aliases[name] = name
        aliases[name.removeprefix("df-")] = name
        aliases[name.removeprefix("df-mic-")] = name
        aliases[name.removeprefix("df-bff-")] = name
        if name.startswith("df-mic-"):
            service_name = name.removeprefix("df-mic-")
            aliases[f"mic-{service_name}-api"] = name
            aliases[f"mic-gy-{service_name}-api"] = name
    return aliases


def _build_evidence(path: Path) -> list[str]:
    values: list[str] = []
    if not path.is_dir():
        return values
    for candidate in _iter_files(path, max_files=400):
        if candidate.name not in _BUILD_FILE_NAMES:
            continue
        values.append(candidate.relative_to(path).as_posix())
        if len(values) >= 32:
            break
    return values


def _git_worktree_evidence(path: Path) -> dict[str, Any]:
    """Capture bounded, read-only worktree state for architecture review.

    A dirty repository is not itself an error in readonly analysis.  It is,
    however, material evidence when the dirty files overlap the proposed
    service boundary: the current implementation must be reconciled before a
    different architecture can be turned into an automatic patch.
    """

    if not path.is_dir():
        return {"status": "unavailable", "reason": "repository path missing", "changed_paths": []}
    try:
        result = subprocess.run(
            ["git", "status", "--short", "--branch", "--untracked-files=all"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=_GIT_EVIDENCE_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception as exc:
        # Worktree state is optional evidence.  A restricted runner, a
        # missing Git executable, or a test/runtime policy that forbids
        # subprocesses must not make the whole service catalogue fail closed;
        # the unavailable evidence is recorded for the later change gate.
        return {"status": "unavailable", "reason": str(exc), "changed_paths": []}
    if result.returncode != 0:
        return {
            "status": "unavailable",
            "reason": (result.stderr or "git status failed").strip()[:240],
            "changed_paths": [],
        }
    lines = [line.rstrip() for line in (result.stdout or "").splitlines() if line.strip()]
    changed_paths: list[str] = []
    for line in lines:
        if line.startswith("## "):
            continue
        # Porcelain v1 uses two status columns followed by a space.  Keep the
        # path only; rename details are retained as a bounded raw value when
        # parsing is ambiguous.
        value = line[3:] if len(line) >= 4 else line
        if " -> " in value:
            value = value.split(" -> ", 1)[-1]
        if value:
            changed_paths.append(value)
    return {
        "status": "dirty" if changed_paths else "clean",
        "branch": next((line[3:] for line in lines if line.startswith("## ")), ""),
        "changed_paths": sorted(set(changed_paths))[:160],
    }


def _public_api_evidence(path: Path) -> list[str]:
    values: list[str] = []
    if not path.is_dir():
        return values
    for candidate in _iter_files(path, extensions={".java", ".kt", ".ts", ".graphqls", ".proto"}):
        relative = candidate.relative_to(path).as_posix()
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lower = relative.lower()
        if (
            "api" not in lower
            and "controller" not in lower
            and "graphql" not in lower
            and not any(token.lower() in text.lower() for token in ("ShouFeiXmApi", "YaoPinZdApi"))
        ):
            continue
        if any(token.lower() in text.lower() for token in ("ShouFeiXmApi", "YaoPinZdApi", "public interface", "@RequestMapping", "type DTO_")):
            values.append(relative)
        if len(values) >= 64:
            break
    return sorted(set(values))


def _public_api_symbols(path: Path) -> list[str]:
    symbols: set[str] = set()
    if not path.is_dir():
        return []
    for candidate in _iter_files(path, extensions={".java", ".kt", ".ts", ".graphqls", ".proto"}):
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in ("ShouFeiXmApi", "YaoPinZdApi", "YaoPinApi", "public interface"):
            if token.lower() in text.lower():
                symbols.add(token)
    return sorted(symbols)


def _public_api_definitions(path: Path) -> list[dict[str, Any]]:
    """Read public API interface methods from source repositories.

    A symbol such as ``YaoPinZdApi`` is not enough to establish an interface
    contract.  This bounded parser records the actual method, parameters and
    return type when the interface source is present.  The result is still
    static evidence; it never implies that a gateway exposes the API over
    HTTP.
    """

    if not path.is_dir():
        return []
    definitions: list[dict[str, Any]] = []
    for candidate in _iter_files(path, extensions={".java", ".kt"}, max_files=1600):
        text = _read_text(candidate)
        if not text or "Api" not in text:
            continue
        interface_match = re.search(
            r"\b(?:public\s+)?interface\s+(?P<api>[A-Za-z_]\w*Api)\b",
            text,
        )
        if not interface_match:
            continue
        feign_match = re.search(
            r"@FeignClient\s*\([^)]*?\bname\s*=\s*['\"](?P<name>[^'\"]+)['\"]",
            text[: interface_match.start()],
            flags=re.IGNORECASE | re.DOTALL,
        )
        client_name = feign_match.group("name") if feign_match else None
        body = text[interface_match.end() :]
        for method_match in re.finditer(
            r"(?:@[^\n]+\s*)*"
            r"(?P<return>[A-Za-z0-9_$.<>?,\[\] ]+)\s+"
            r"(?P<name>[a-zA-Z_]\w*)\s*"
            r"\((?P<params>[^;{}]{0,1600})\)\s*"
            r"(?:throws\s+[^;{]+)?;",
            body,
            flags=re.DOTALL,
        ):
            return_type = " ".join(method_match.group("return").split())
            params = method_match.group("params") or ""
            mapping_match = re.search(
                r"@(?P<kind>Get|Post|Put|Delete|Patch|Request)Mapping\s*"
                r"\([^)]*?['\"](?P<path>/[A-Za-z0-9_/${}.-]+)['\"]",
                method_match.group(0),
                flags=re.IGNORECASE | re.DOTALL,
            )
            definitions.append(
                {
                    "api": interface_match.group("api"),
                    "client_name": client_name,
                    "method": method_match.group("name"),
                    "http_method": mapping_match.group("kind").upper() if mapping_match else None,
                    "route": mapping_match.group("path") if mapping_match else None,
                    "return_type": return_type,
                    "request_types": sorted(set(_contract_types(params))),
                    "request_parameters": _contract_parameters(params),
                    "response_types": sorted(set(_contract_types(return_type))),
                    "evidence_path": candidate.relative_to(path).as_posix(),
                    "evidence_status": "static_api_definition",
                }
            )
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in definitions:
        key = (
            str(item.get("api") or ""),
            str(item.get("method") or ""),
            str(item.get("evidence_path") or ""),
        )
        unique[key] = item
    return sorted(
        unique.values(),
        key=lambda item: (str(item.get("api") or ""), str(item.get("method") or "")),
    )[:_MAX_API_CONTRACTS]


def _api_usage_evidence(path: Path) -> list[dict[str, Any]]:
    """Find typed public-API declarations and calls in a repository."""

    if not path.is_dir():
        return []
    values: list[dict[str, Any]] = []
    for candidate in _iter_files(path, extensions={".java", ".kt"}, max_files=1600):
        text = _read_text(candidate)
        if not text or not re.search(r"\b[A-Za-z_]\w*Api\b", text):
            continue
        declarations = {
            match.group("var"): match.group("api")
            for match in re.finditer(
                r"\b(?P<api>[A-Za-z_]\w*Api)\s+(?P<var>[A-Za-z_]\w*)\b",
                text,
            )
        }
        for match in re.finditer(
            r"\b(?P<var>[A-Za-z_]\w*)\s*\.\s*(?P<method>[A-Za-z_]\w*)\s*\(",
            text,
        ):
            api = declarations.get(match.group("var"))
            if not api:
                continue
            line = text.count("\n", 0, match.start()) + 1
            values.append(
                {
                    "api": api,
                    "method": match.group("method"),
                    "evidence_path": candidate.relative_to(path).as_posix(),
                    "line": line,
                    "evidence_status": "static_api_usage",
                }
            )
    unique: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for item in values:
        key = (
            str(item.get("api") or ""),
            str(item.get("method") or ""),
            str(item.get("evidence_path") or ""),
            int(item.get("line") or 0),
        )
        unique[key] = item
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item.get("api") or ""),
            str(item.get("method") or ""),
            str(item.get("evidence_path") or ""),
            int(item.get("line") or 0),
        ),
    )[:_MAX_API_CONTRACTS]


def _data_contract_evidence(path: Path) -> list[dict[str, Any]]:
    """Collect source paths that contain the fields relevant to a directory.

    This is intentionally a locator, not a semantic proof.  It lets the
    Harness show which DTO/entity/mapper files it inspected before asking for
    a runtime or business confirmation.
    """

    if not path.is_dir():
        return []
    token_groups = {
        "directory_source": ("gy_shoufeixm", "yaopin", "药品", "收费项目"),
        "mapping_fields": ("guojiaxmdm", "duizhaobm", "kaishisj", "jieshushijian", "医保对照"),
        "approval_flags": ("menzhenbz", "zhuyuanbz", "zifeibz", "bushangchuanbz"),
        "directory_projection": ("DTO_YB_YiYuanMuLuXx", "YiYuanMuLu", "YB_YiYuan"),
        "persistence": ("save(", "insert(", "update(", "repository", "mapper"),
    }
    values: list[dict[str, Any]] = []
    for candidate in _iter_files(path, extensions={".java", ".kt", ".xml", ".sql"}, max_files=1600):
        text = _read_text(candidate)
        if not text:
            continue
        lower = text.lower()
        matched = [
            group for group, tokens in token_groups.items()
            if any(str(token).lower() in lower for token in tokens)
        ]
        if not matched:
            continue
        values.append(
            {
                "evidence_path": candidate.relative_to(path).as_posix(),
                "groups": matched,
                "evidence_status": "static_data_contract_locator",
            }
        )
        if len(values) >= 256:
            break
    return sorted(values, key=lambda item: str(item.get("evidence_path") or ""))


def _http_call_evidence(path: Path) -> list[dict[str, Any]]:
    """Locate frontend/gateway URL literals without treating them as ownership proof."""

    if not path.is_dir():
        return []
    values: list[dict[str, Any]] = []
    extensions = {".js", ".jsx", ".ts", ".tsx", ".vue", ".html", ".json", ".yml", ".yaml"}
    route_hint = re.compile(
        r"(?:yaopin|yaopinzd|yaopinmu|shoufeixm|yiyuanmulu|yibaospxm|"
        r"ykf-jichuyw|gy-jichufw|winbff-jichufw|yb-yibaogl|agg-yibaogl)",
        flags=re.IGNORECASE,
    )
    url_pattern = re.compile(
        r"(?:url\s*:\s*|['\"])(?P<url>/?(?:[A-Za-z0-9_-]+/){1,}[A-Za-z0-9_?&=/${}.-]+)",
        flags=re.IGNORECASE,
    )
    for candidate in _iter_files(path, extensions=extensions, max_files=1800):
        text = _read_text(candidate)
        if not text:
            continue
        for match in url_pattern.finditer(text):
            endpoint = match.group("url")
            if not route_hint.search(endpoint):
                continue
            line = text.count("\n", 0, match.start()) + 1
            window = text[max(0, match.start() - 180) : match.end() + 120]
            method_match = re.search(r"\bmethod\s*:\s*['\"](?P<method>\w+)", window, flags=re.IGNORECASE)
            values.append(
                {
                    "endpoint": endpoint,
                    "http_method": (method_match.group("method").upper() if method_match else "UNKNOWN"),
                    "evidence_path": candidate.relative_to(path).as_posix(),
                    "line": line,
                    "evidence_status": "static_http_call_or_route",
                }
            )
        if len(values) >= 512:
            break
    unique: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for item in values:
        key = (
            str(item.get("endpoint") or ""),
            str(item.get("http_method") or ""),
            str(item.get("evidence_path") or ""),
            int(item.get("line") or 0),
        )
        unique[key] = item
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item.get("endpoint") or ""),
            str(item.get("evidence_path") or ""),
            int(item.get("line") or 0),
        ),
    )


def _verification_candidates(path: Path) -> list[dict[str, Any]]:
    """Derive safe, non-executed verification candidates from repo metadata."""

    if not path.is_dir():
        return []
    values: list[dict[str, Any]] = []
    names = {candidate.name for candidate in _iter_files(path, max_files=500)}
    if "gradlew" in names:
        values.append({"command": "./gradlew test", "basis": "gradlew", "status": "candidate"})
    elif "build.gradle" in names or "build.gradle.kts" in names:
        values.append({"command": "gradle test", "basis": "Gradle build file", "status": "candidate_requires_gradle"})
    if "mvnw" in names:
        values.append({"command": "./mvnw test", "basis": "mvnw", "status": "candidate"})
    elif "pom.xml" in names:
        values.append({"command": "mvn test", "basis": "Maven build file", "status": "candidate_requires_maven"})
    package_json = path / "package.json"
    package = _read_text(package_json) if package_json.is_file() else ""
    if package:
        try:
            scripts = (json.loads(package).get("scripts") or {})
        except (TypeError, ValueError):
            scripts = {}
        for name in ("test", "lint", "build"):
            if name in scripts:
                values.append({"command": f"npm run {name}", "basis": f"package.json scripts.{name}", "status": "candidate"})
    unique: dict[str, dict[str, Any]] = {}
    for item in values:
        unique[str(item.get("command") or "")] = item
    return [unique[key] for key in sorted(unique) if key]


def _public_api_routes(path: Path) -> list[str]:
    """Extract controller route evidence without claiming an HTTP contract.

    Route strings are only catalog evidence.  They are deliberately not used
    as executable patch targets until the frontend request, DTO and controller
    mapping are all verified by the technical decision layer.
    """

    routes: set[str] = set()
    if not path.is_dir():
        return []
    class_mapping = re.compile(
        r"@RequestMapping\s*\([^)]*?['\"](?P<path>/[A-Za-z0-9_/-]+)['\"]",
        flags=re.IGNORECASE | re.DOTALL,
    )
    method_mapping = re.compile(
        r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\s*\([^)]*?['\"](?P<path>/?[A-Za-z0-9_/-]+)['\"]",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for candidate in _iter_files(path, extensions={".java", ".kt"}, max_files=1200):
        if "controller" not in candidate.name.lower():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        class_paths = [match.group("path").rstrip("/") for match in class_mapping.finditer(text)]
        if not class_paths:
            continue
        prefix = class_paths[-1]
        for match in method_mapping.finditer(text):
            suffix = "/" + match.group("path").lstrip("/")
            routes.add(prefix + suffix)
    return sorted(routes)[:128]


def _public_api_contracts(path: Path) -> list[dict[str, Any]]:
    """Extract bounded controller-to-service API contract evidence.

    This is intentionally a lightweight Java/Kotlin parser.  It records what
    is statically visible (route, method, DTOs and direct API calls), but does
    not claim that the endpoint is a complete runtime contract.  A missing
    service implementation or response mapping therefore remains a gap for
    the change-contract layer instead of becoming a guessed patch target.
    """

    if not path.is_dir():
        return []
    controllers: list[dict[str, Any]] = []
    source_files = list(_iter_files(path, extensions={".java", ".kt"}, max_files=1600))
    for candidate in source_files:
        if "controller" not in candidate.name.lower():
            continue
        text = _read_text(candidate)
        if not text:
            continue
        class_match = re.search(r"\bclass\s+[A-Za-z_]\w*", text)
        before_class = text[: class_match.start()] if class_match else text[:4000]
        class_paths = _mapping_paths(before_class, "RequestMapping")
        prefix = class_paths[-1].rstrip("/") if class_paths else ""
        if not prefix:
            continue
        for mapping_match in re.finditer(
            r"@(?P<kind>Get|Post|Put|Delete|Patch|Request)Mapping\s*"
            r"(?:\((?P<args>[^)]*)\))?",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            mapping_path = _mapping_path_from_args(mapping_match.group("args") or "")
            if mapping_path is None:
                continue
            tail = text[mapping_match.end() : mapping_match.end() + 3600]
            method_match = re.search(
                r"\b(?:public|protected|private)\s+"
                r"(?P<return>[A-Za-z0-9_$.<>?,\[\] ]+)\s+"
                r"(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^{};]{0,1600})\)\s*\{",
                tail,
                flags=re.DOTALL,
            )
            if not method_match:
                continue
            method_name = method_match.group("name")
            body_start = method_match.end()
            body = _method_body(tail, body_start, max_chars=3600)
            params = method_match.group("params") or ""
            return_type = " ".join(method_match.group("return").split())
            service_calls = sorted(set(
                f"{match.group('owner')}.{match.group('method')}"
                for match in re.finditer(
                    r"\b(?P<owner>[A-Za-z_]\w*Service)\s*\.\s*(?P<method>[A-Za-z_]\w+)\s*\(",
                    body,
                )
            ))
            controllers.append(
                {
                    "route": _join_route(prefix, mapping_path),
                    "http_method": mapping_match.group("kind").upper(),
                    "controller": candidate.stem,
                    "method": method_name,
                    "return_type": return_type,
                    "request_types": sorted(set(_contract_types(params))),
                    "request_parameters": _contract_parameters(params),
                    "response_types": sorted(set(_contract_types(return_type))),
                    "service_calls": service_calls,
                    "evidence_paths": [candidate.relative_to(path).as_posix()],
                    "evidence_status": "static_controller_contract",
                }
            )

    if not controllers:
        return []

    # Match each controller method to a service implementation and record only
    # API calls made by that method.  This prevents a class-level import or an
    # unrelated method from being mistaken for the endpoint's data source.
    implementations: list[tuple[Path, str, dict[str, Any]]] = []
    for candidate in source_files:
        if "impl" not in candidate.as_posix().lower() and "service" not in candidate.name.lower():
            continue
        text = _read_text(candidate)
        if not text:
            continue
        api_declarations = {
            match.group("var"): match.group("type")
            for match in re.finditer(
                r"\b(?P<type>[A-Za-z_]\w*Api)\s+(?P<var>[A-Za-z_]\w*)",
                text,
            )
        }
        api_fields = sorted(set(api_declarations.values()))
        for method_match in re.finditer(
            r"\b(?:public|protected|private)\s+"
            r"[A-Za-z0-9_$.<>?,\[\] ]+\s+(?P<name>[A-Za-z_]\w*)\s*"
            r"\((?P<params>[^{};]{0,1600})\)\s*\{",
            text,
            flags=re.DOTALL,
        ):
            start = method_match.end()
            body = _method_body(text, start, max_chars=5000)
            api_calls = [
                {
                    "api": api_declarations.get(match.group("api"), match.group("api")),
                    "method": match.group("method"),
                }
                for match in re.finditer(
                    r"\b(?P<api>[A-Za-z_]\w*Api)\s*\.\s*(?P<method>[A-Za-z_]\w+)\s*\(",
                    body,
                )
            ]
            implementations.append(
                (
                    candidate,
                    method_match.group("name"),
                    {
                        "api_symbols": sorted(set(item["api"] for item in api_calls)) or api_fields,
                        "api_calls": _unique_dicts(api_calls),
                        "evidence_path": candidate.relative_to(path).as_posix(),
                    },
                )
            )

    for contract in controllers:
        service_method = next(
            (
                str(item).split(".", 1)[1]
                for item in contract.get("service_calls") or []
                if "." in str(item)
            ),
            "",
        )
        if not service_method:
            continue
        match = next((item for item in implementations if item[1] == service_method), None)
        if not match:
            contract["service_contract_status"] = "implementation_not_found"
            continue
        implementation_path, _, evidence = match
        contract["service_contract_status"] = "static_service_implementation"
        contract["service_impl"] = implementation_path.stem
        contract["service_evidence_paths"] = [evidence["evidence_path"]]
        contract["upstream_api_symbols"] = evidence["api_symbols"]
        contract["upstream_api_calls"] = evidence["api_calls"]
    return sorted(
        controllers,
        key=lambda item: (str(item.get("route") or ""), str(item.get("method") or "")),
    )[:_MAX_API_CONTRACTS]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _mapping_paths(text: str, annotation: str) -> list[str]:
    return [
        _mapping_path_from_args(match.group("args") or "")
        for match in re.finditer(
            rf"@{re.escape(annotation)}\s*\((?P<args>[^)]*)\)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if _mapping_path_from_args(match.group("args") or "") is not None
    ]


def _mapping_path_from_args(args: str) -> str | None:
    match = re.search(r"['\"](?P<path>/?[A-Za-z0-9_/${}.-]+)['\"]", args or "")
    if not match:
        return None
    return "/" + match.group("path").lstrip("/")


def _join_route(prefix: str, suffix: str) -> str:
    return "/" + "/".join(
        part.strip("/") for part in (prefix, suffix) if str(part or "").strip("/")
    )


def _contract_types(text: str) -> list[str]:
    excluded = {"RequestParam", "RequestBody", "PathVariable", "RequestHeader", "ModelAttribute"}
    return [
        value for value in re.findall(
            r"\b(?:DTO_[A-Za-z0-9_]+|[A-Z][A-Za-z0-9_]*(?:Req|Request|Query|Param))\b",
            text or "",
        )
        if value not in excluded
    ]


def _contract_parameters(text: str) -> list[dict[str, str]]:
    """Normalize Java/Kotlin method parameters while ignoring annotations."""

    values: list[dict[str, str]] = []
    for segment in _split_parameters(text or ""):
        clean = re.sub(r"@[A-Za-z_]\w*(?:\([^)]*\))?\s*", "", segment).strip()
        clean = re.sub(r"\bfinal\s+", "", clean)
        match = re.search(
            r"(?P<type>[A-Za-z0-9_$.<>?,\[\]]+)\s+(?P<name>[A-Za-z_]\w*)\s*$",
            clean,
        )
        if match:
            values.append({"type": match.group("type"), "name": match.group("name")})
    return values


def _split_parameters(text: str) -> list[str]:
    values: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            values.append(text[start:index])
            start = index + 1
    values.append(text[start:])
    return [value for value in values if value.strip()]


def _unique_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in values:
        key = (str(item.get("api") or ""), str(item.get("method") or ""))
        unique[key] = item
    return [unique[key] for key in sorted(unique)]


def _method_body(text: str, opening_index: int, *, max_chars: int) -> str:
    """Return a best-effort balanced method body for bounded static evidence."""

    body = text[opening_index : opening_index + max_chars]
    depth = 1
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return body[: index + 1]
    return body


def _iter_files(
    root: Path,
    *,
    extensions: set[str] | None = None,
    max_files: int = _MAX_SOURCE_FILES,
):
    """Yield bounded source/build files without entering generated trees."""

    yielded = 0
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for directory, directory_names, file_names in walker:
            directory_names[:] = sorted(
                name for name in directory_names
                if name not in _SCAN_EXCLUDED_DIRS and not name.startswith(".")
            )
            for name in sorted(file_names):
                candidate = Path(directory) / name
                if extensions is not None and candidate.suffix.lower() not in extensions:
                    continue
                try:
                    if not candidate.is_file():
                        continue
                except OSError:
                    continue
                yield candidate
                yielded += 1
                if yielded >= max_files:
                    return
    except OSError:
        return


def _dependency_evidence(*, project_name: str, project_path: Path, known_names: set[str], aliases: Mapping[str, str]) -> list[dict[str, Any]]:
    build_files = [project_path / name for name in _BUILD_FILE_NAMES if (project_path / name).is_file()]
    texts: list[tuple[Path, str]] = []
    for path in build_files:
        try:
            texts.append((path, path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    edges: list[dict[str, Any]] = []
    for alias, target in aliases.items():
        if target == project_name or len(alias) < 5:
            continue
        for path, text in texts:
            if not re.search(rf"(?<![A-Za-z0-9_-]){re.escape(alias)}(?![A-Za-z0-9_-])", text, flags=re.IGNORECASE):
                continue
            kind = "api_module_dependency" if "api" in alias.lower() else "module_dependency"
            edges.append(
                {
                    "source_project": project_name,
                    "target_project": target,
                    "kind": kind,
                    "evidence_paths": [f"{project_name}:{path.relative_to(project_path).as_posix()}"],
                    "status": "proven",
                }
            )
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for edge in edges:
        key = (edge["source_project"], edge["target_project"], edge["kind"])
        current = unique.get(key)
        if current is None:
            unique[key] = edge
        else:
            current["evidence_paths"] = sorted(set(current["evidence_paths"] + edge["evidence_paths"]))
    return list(unique.values())


def _dependency_findings(*, nodes: list[Mapping[str, Any]], edges: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for edge in edges:
        source = str(edge.get("source_project") or "")
        target = str(edge.get("target_project") or "")
        if source.startswith("df-mic-yibaogl") and target.startswith("df-mic-yaokufang"):
            findings.append(
                {
                    "type": "direct_drug_service_dependency",
                    "status": "conflict",
                    "source_project": source,
                    "target_project": target,
                    "message": "医保服务构建证据显示直接依赖药房服务；必须改为已声明的公共 API 或经过 BFF，不得按服务名猜测调用。",
                    "evidence_paths": list(edge.get("evidence_paths") or []),
                }
            )
    return findings
