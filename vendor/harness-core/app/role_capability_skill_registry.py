from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.capability_registry import CapabilityRegistry, CapabilityRegistryError
from app.task_context import TaskIntentContext


MATRIX_SCHEMA_VERSION = "his-role-capability-skill-matrix.v2"
_MUTATION_LEVELS = frozenset({"L0", "L1", "L2", "L3", "L4", "L5"})
_EXECUTION_KINDS = frozenset({"provider", "internal", "mcp"})
_REQUIRED_BOUNDARIES = frozenset(
    {"mcp_required", "worker_allowed", "control_plane_internal"}
)
_MIGRATION_STATES = frozenset({"native", "compatibility"})
_WORKER_ALLOWED_CAPABILITIES = frozenset(
    {
        "code.review-local",
        "git.apply-local",
        "git.commit-local",
        "git.diff",
        "git.history",
        "git.inspect",
        "source.read",
        "source.search",
        "verification.run-local",
    }
)
_CONTROL_PLANE_CAPABILITIES = frozenset(
    {"database.change-plan", "requirement.govern", "visual.extract"}
)
_MCP_REQUIRED_CAPABILITIES = frozenset(
    {
        "database.change",
        "database.inspect",
        "git.push",
        "github.read",
        "github.write",
        "gitlab.read",
        "gitlab.write",
        "workitem.read",
        "workitem.write",
    }
)


class RoleCapabilitySkillRegistryError(ValueError):
    """角色、能力和 Skill 注册表不安全或不完整。"""


class RoleRoutingError(RoleCapabilitySkillRegistryError):
    """角色路由无法在当前任务上下文中安全解析。"""


@dataclass(frozen=True)
class SkillDeclaration:
    name: str
    kind: str
    plugin: str
    path: str
    canonical: bool
    mcp_server: str | None = None


@dataclass(frozen=True)
class CapabilityRoute:
    capability: str
    provider: str
    skill: str
    mutation_level: str
    execution_kind: str
    required_boundary: str
    migration_state: str
    external_executable: bool
    mcp_server: str | None = None


@dataclass(frozen=True)
class RoleRoute:
    role_id: str
    tool: str
    capability: str
    provider: str
    skill: str
    execution_kind: str
    required_boundary: str
    migration_state: str
    mutation_level: str
    external_executable: bool
    mcp_server: str | None = None


class RoleCapabilitySkillRegistry:
    def __init__(
        self,
        *,
        harness_root: Path,
        plugin_roots: Mapping[str, Path],
        skills: Mapping[str, SkillDeclaration],
        capability_routes: Sequence[CapabilityRoute],
        bindings: Mapping[str, CapabilityRoute],
        roles: Mapping[str, tuple[str, ...]],
    ) -> None:
        self.harness_root = harness_root
        self.plugin_roots = dict(plugin_roots)
        self.skills = dict(skills)
        self._capability_routes = tuple(capability_routes)
        self._routes = {(item.capability, item.provider): item for item in capability_routes}
        self._bindings = dict(bindings)
        self.roles = dict(roles)

    @property
    def capability_routes(self) -> tuple[CapabilityRoute, ...]:
        return self._capability_routes

    def resolve_capability(self, capability: str, provider: str) -> CapabilityRoute:
        try:
            route = self._routes[(capability, provider)]
        except KeyError as exc:
            raise RoleCapabilitySkillRegistryError(
                f"未注册 capability/provider：{capability}/{provider}。"
            ) from exc
        return route

    def resolve_internal_capability(self, capability: str) -> CapabilityRoute:
        matches = [
            item for item in self._capability_routes
            if item.capability == capability and item.execution_kind == "internal"
        ]
        if len(matches) != 1:
            raise RoleCapabilitySkillRegistryError(
                f"internal capability 必须唯一：{capability}。"
            )
        return matches[0]

    def route_role(
        self,
        role_id: str,
        allowed_tools: Sequence[str],
        *,
        task_context: TaskIntentContext,
    ) -> tuple[RoleRoute, ...]:
        if not isinstance(task_context, TaskIntentContext) or not task_context.is_complete:
            missing = (
                task_context.missing_fields
                if isinstance(task_context, TaskIntentContext)
                else ("background", "goal", "scenarios", "desired_outcome")
            )
            raise RoleRoutingError(
                "task_context_incomplete:" + ",".join(missing)
            )
        configured = self.roles.get(role_id)
        if configured is None:
            raise RoleRoutingError(f"未知 role：{role_id}。")
        requested = tuple(allowed_tools)
        if requested != configured:
            raise RoleRoutingError(
                f"role tools 与注册表不一致：{role_id}。"
            )
        routes: list[RoleRoute] = []
        for tool in requested:
            binding = self._bindings.get(tool)
            if binding is None:
                raise RoleRoutingError(f"tool 没有 capability 路由：{tool}。")
            routes.append(
                RoleRoute(
                    role_id=role_id,
                    tool=tool,
                    capability=binding.capability,
                    provider=binding.provider,
                    skill=binding.skill,
                    execution_kind=binding.execution_kind,
                    required_boundary=binding.required_boundary,
                    migration_state=binding.migration_state,
                    mutation_level=binding.mutation_level,
                    external_executable=binding.external_executable,
                    mcp_server=binding.mcp_server,
                )
            )
        return tuple(routes)

    def validate_role_catalog(self, role_catalog: Mapping[str, Any]) -> None:
        if set(role_catalog) != set(self.roles):
            missing = sorted(set(role_catalog) - set(self.roles))
            extra = sorted(set(self.roles) - set(role_catalog))
            raise RoleCapabilitySkillRegistryError(
                f"role catalog 与注册表不一致：missing={missing}, extra={extra}。"
            )
        for role_id, spec in role_catalog.items():
            expected = tuple(spec.allowed_tools)
            if expected != self.roles[role_id]:
                raise RoleCapabilitySkillRegistryError(
                    f"role allowed_tools 与注册表不一致：{role_id}。"
                )
            if getattr(spec, "human_only", False) and self.roles[role_id]:
                raise RoleCapabilitySkillRegistryError(
                    f"human_only role 不得有自动工具：{role_id}。"
                )


def load_role_capability_skill_registry(
    matrix_path: str | Path | None = None,
    *,
    harness_root: str | Path | None = None,
    plugin_roots: Mapping[str, str | Path] | None = None,
) -> RoleCapabilitySkillRegistry:
    root = Path(harness_root or Path(__file__).resolve().parents[1]).resolve()
    path = Path(matrix_path or root / "config" / "role_capability_skill_matrix.json").resolve()
    payload = _read_json(path)
    if set(payload) != {
        "schema_version", "task_context_required", "skills", "capability_routes", "bindings", "roles"
    }:
        raise RoleCapabilitySkillRegistryError("role capability skill matrix 顶层字段不完整。")
    if payload["schema_version"] != MATRIX_SCHEMA_VERSION:
        raise RoleCapabilitySkillRegistryError(
            f"schema_version 必须为 {MATRIX_SCHEMA_VERSION}。"
        )
    required_context = tuple(payload["task_context_required"])
    if required_context != ("background", "goal", "scenarios", "desired_outcome"):
        raise RoleCapabilitySkillRegistryError("task_context_required 必须覆盖四项核心意图。")
    roots = _load_plugin_roots(root, plugin_roots)
    provider_registry = _load_provider_registry(roots)
    skills = _parse_skills(payload["skills"], root, roots)
    routes = _parse_capability_routes(payload["capability_routes"], skills, provider_registry)
    route_by_key = {(item.capability, item.provider): item for item in routes}
    bindings = _parse_bindings(payload["bindings"], skills, route_by_key)
    roles = _parse_roles(payload["roles"], bindings)
    return RoleCapabilitySkillRegistry(
        harness_root=root,
        plugin_roots=roots,
        skills=skills,
        capability_routes=routes,
        bindings=bindings,
        roles=roles,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RoleCapabilitySkillRegistryError(f"注册表无法读取：{path}。") from exc
    if not isinstance(payload, dict):
        raise RoleCapabilitySkillRegistryError("注册表必须是对象。")
    return payload


def _load_plugin_roots(
    harness_root: Path,
    supplied: Mapping[str, str | Path] | None,
) -> dict[str, Path]:
    if supplied is not None:
        roots = {str(name): Path(value).resolve() for name, value in supplied.items()}
    else:
        config_path = harness_root / "config" / "capabilities.json"
        config = _read_json(config_path)
        raw_roots = config.get("plugin_roots")
        if not isinstance(raw_roots, list):
            raise RoleCapabilitySkillRegistryError("capabilities.json 缺少 plugin_roots。")
        roots = {}
        for value in raw_roots:
            candidate = Path(value).expanduser()
            path = (
                candidate.resolve()
                if candidate.is_absolute()
                else (config_path.parent / candidate).resolve()
            )
            manifest_path = path / "capabilities.json"
            try:
                plugin = json.loads(manifest_path.read_text(encoding="utf-8"))["plugin"]
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise RoleCapabilitySkillRegistryError(
                    f"插件 capability manifest 无法读取：{manifest_path}。"
                ) from exc
            roots[str(plugin)] = path
    if "harness-internal" in roots:
        raise RoleCapabilitySkillRegistryError("harness-internal 不得伪装为外部插件。")
    return roots


def _load_provider_registry(roots: Mapping[str, Path]) -> CapabilityRegistry:
    try:
        return CapabilityRegistry.from_plugin_roots(tuple(roots.values()))
    except CapabilityRegistryError as exc:
        raise RoleCapabilitySkillRegistryError("正式插件 capability registry 无法加载。") from exc


def _parse_skills(
    payload: Any,
    harness_root: Path,
    plugin_roots: Mapping[str, Path],
) -> dict[str, SkillDeclaration]:
    if not isinstance(payload, list):
        raise RoleCapabilitySkillRegistryError("skills 必须是数组。")
    result: dict[str, SkillDeclaration] = {}
    for index, item in enumerate(payload):
        data = _mapping(item, f"skills[{index}]")
        name = _text(data.get("name"), f"skills[{index}].name")
        if name in result:
            raise RoleCapabilitySkillRegistryError(f"Skill 重复：{name}。")
        kind = _text(data.get("kind"), f"skills[{index}].kind")
        if kind not in {"codex_skill", "mcp_skill", "internal_skill"}:
            raise RoleCapabilitySkillRegistryError(f"Skill kind 不支持：{kind}。")
        plugin = _text(data.get("plugin"), f"skills[{index}].plugin")
        relative_path = _safe_relative_path(data.get("path"), f"skills[{index}].path")
        canonical = data.get("canonical")
        if not isinstance(canonical, bool) or not canonical:
            raise RoleCapabilitySkillRegistryError(f"Skill 必须声明 canonical：{name}。")
        mcp_server = data.get("mcp_server")
        if mcp_server is not None:
            mcp_server = _text(mcp_server, f"skills[{index}].mcp_server")
        declaration = SkillDeclaration(name, kind, plugin, relative_path, canonical, mcp_server)
        _verify_skill(declaration, harness_root, plugin_roots)
        result[name] = declaration
    return result


def _verify_skill(
    skill: SkillDeclaration,
    harness_root: Path,
    plugin_roots: Mapping[str, Path],
) -> None:
    if skill.kind == "internal_skill":
        if skill.plugin != "harness-internal":
            raise RoleCapabilitySkillRegistryError(f"内部 Skill plugin 非法：{skill.name}。")
        target = (harness_root / skill.path).resolve()
        if not _within(target, harness_root) or not target.is_file():
            raise RoleCapabilitySkillRegistryError(f"内部 Skill 不存在：{target}。")
        return
    plugin_root = plugin_roots.get(skill.plugin)
    if plugin_root is None:
        raise RoleCapabilitySkillRegistryError(f"Skill plugin 未注册：{skill.plugin}。")
    target = (plugin_root / skill.path).resolve()
    if not _within(target, plugin_root) or not target.is_file():
        raise RoleCapabilitySkillRegistryError(f"canonical Skill 不存在：{target}。")
    if skill.kind == "mcp_skill":
        if not skill.mcp_server:
            raise RoleCapabilitySkillRegistryError(f"MCP Skill 缺少 mcp_server：{skill.name}。")
        mcp_path = plugin_root / ".mcp.json"
        try:
            servers = json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RoleCapabilitySkillRegistryError(f"MCP 配置无法读取：{mcp_path}。") from exc
        if not isinstance(servers, dict) or skill.mcp_server not in servers:
            raise RoleCapabilitySkillRegistryError(
                f"MCP server 未注册：{skill.mcp_server}。"
            )


def _parse_capability_routes(
    payload: Any,
    skills: Mapping[str, SkillDeclaration],
    provider_registry: CapabilityRegistry,
) -> tuple[CapabilityRoute, ...]:
    if not isinstance(payload, list):
        raise RoleCapabilitySkillRegistryError("capability_routes 必须是数组。")
    result: list[CapabilityRoute] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(payload):
        data = _mapping(item, f"capability_routes[{index}]")
        capability = _text(data.get("capability"), f"capability_routes[{index}].capability")
        provider = _text(data.get("provider"), f"capability_routes[{index}].provider")
        key = (capability, provider)
        if key in seen:
            raise RoleCapabilitySkillRegistryError(f"capability route 重复：{capability}/{provider}。")
        seen.add(key)
        skill_name = _text(data.get("skill"), f"capability_routes[{index}].skill")
        skill = skills.get(skill_name)
        if skill is None:
            raise RoleCapabilitySkillRegistryError(f"capability route Skill 未注册：{skill_name}。")
        mutation_level = _text(data.get("mutation_level"), f"capability_routes[{index}].mutation_level")
        if mutation_level not in _MUTATION_LEVELS:
            raise RoleCapabilitySkillRegistryError(f"mutation_level 非法：{mutation_level}。")
        execution_kind = _text(data.get("execution_kind"), f"capability_routes[{index}].execution_kind")
        if execution_kind not in _EXECUTION_KINDS:
            raise RoleCapabilitySkillRegistryError(f"execution_kind 非法：{execution_kind}。")
        required_boundary = _text(
            data.get("required_boundary"),
            f"capability_routes[{index}].required_boundary",
        )
        if required_boundary not in _REQUIRED_BOUNDARIES:
            raise RoleCapabilitySkillRegistryError(
                f"required_boundary 非法：{required_boundary}。"
            )
        migration_state = _text(
            data.get("migration_state"),
            f"capability_routes[{index}].migration_state",
        )
        if migration_state not in _MIGRATION_STATES:
            raise RoleCapabilitySkillRegistryError(
                f"migration_state 非法：{migration_state}。"
            )
        external_executable = data.get("external_executable")
        if not isinstance(external_executable, bool):
            raise RoleCapabilitySkillRegistryError("external_executable 必须是布尔值。")
        mcp_server = data.get("mcp_server")
        if mcp_server is not None:
            mcp_server = _text(mcp_server, f"capability_routes[{index}].mcp_server")
        if execution_kind == "internal":
            if skill.kind != "internal_skill" or provider != "harness" or external_executable:
                raise RoleCapabilitySkillRegistryError(
                    f"内部 capability 必须绑定 internal Skill 且禁止外部执行：{capability}。"
                )
        elif execution_kind == "provider":
            try:
                descriptor = provider_registry.resolve(capability, provider)
            except CapabilityRegistryError as exc:
                raise RoleCapabilitySkillRegistryError(
                    f"capability route 不存在于正式 manifest：{capability}/{provider}。"
                ) from exc
            if descriptor.plugin != skill.plugin:
                raise RoleCapabilitySkillRegistryError(
                    f"capability 与 Skill plugin 不一致：{capability}/{provider}。"
                )
            if skill.kind == "internal_skill":
                raise RoleCapabilitySkillRegistryError(f"provider capability 不得绑定 internal Skill：{capability}。")
        else:
            if skill.kind != "mcp_skill" or not mcp_server:
                raise RoleCapabilitySkillRegistryError(
                    f"MCP capability 必须绑定声明 server 的 MCP Skill：{capability}。"
                )
        if mcp_server != skill.mcp_server:
            raise RoleCapabilitySkillRegistryError(
                f"MCP route 与 Skill 声明不一致：{capability}。"
            )
        _validate_boundary_contract(
            capability=capability,
            execution_kind=execution_kind,
            required_boundary=required_boundary,
            migration_state=migration_state,
            skill=skill,
            mcp_server=mcp_server,
        )
        result.append(
            CapabilityRoute(
                capability=capability,
                provider=provider,
                skill=skill_name,
                mutation_level=mutation_level,
                execution_kind=execution_kind,
                required_boundary=required_boundary,
                migration_state=migration_state,
                external_executable=external_executable,
                mcp_server=mcp_server,
            )
        )
    return tuple(result)


def _parse_bindings(
    payload: Any,
    skills: Mapping[str, SkillDeclaration],
    routes: Mapping[tuple[str, str], CapabilityRoute],
) -> dict[str, CapabilityRoute]:
    if not isinstance(payload, dict):
        raise RoleCapabilitySkillRegistryError("bindings 必须是对象。")
    result: dict[str, CapabilityRoute] = {}
    for tool, item in payload.items():
        data = _mapping(item, f"bindings.{tool}")
        capability = _text(data.get("capability"), f"bindings.{tool}.capability")
        provider = _text(data.get("provider"), f"bindings.{tool}.provider")
        route = routes.get((capability, provider))
        if route is None:
            raise RoleCapabilitySkillRegistryError(
                f"tool binding 未指向 capability route：{tool}。"
            )
        for key in (
            "skill",
            "execution_kind",
            "required_boundary",
            "migration_state",
            "mutation_level",
            "external_executable",
        ):
            if data.get(key) != getattr(route, key):
                raise RoleCapabilitySkillRegistryError(
                    f"tool binding 与 capability route 不一致：{tool}。"
                )
        if data.get("mcp_server") != route.mcp_server:
            raise RoleCapabilitySkillRegistryError(f"tool binding MCP 不一致：{tool}。")
        if route.skill not in skills:
            raise RoleCapabilitySkillRegistryError(f"tool binding Skill 未注册：{tool}。")
        result[str(tool)] = route
    return result


def _validate_boundary_contract(
    *,
    capability: str,
    execution_kind: str,
    required_boundary: str,
    migration_state: str,
    skill: SkillDeclaration,
    mcp_server: str | None,
) -> None:
    if capability.startswith("harness.") or capability in _CONTROL_PLANE_CAPABILITIES:
        expected_boundary = "control_plane_internal"
    elif capability.startswith("knowledge.") or capability in _MCP_REQUIRED_CAPABILITIES:
        expected_boundary = "mcp_required"
    elif capability in _WORKER_ALLOWED_CAPABILITIES:
        expected_boundary = "worker_allowed"
    else:
        raise RoleCapabilitySkillRegistryError(
            f"capability 尚未声明企业边界分类：{capability}。"
        )
    if required_boundary != expected_boundary:
        raise RoleCapabilitySkillRegistryError(
            f"capability 边界分类不正确：{capability}/{required_boundary}。"
        )

    if execution_kind == "internal":
        valid = required_boundary == "control_plane_internal" and migration_state == "native"
    elif execution_kind == "mcp":
        valid = (
            required_boundary == "mcp_required"
            and migration_state == "native"
            and skill.kind == "mcp_skill"
            and bool(mcp_server)
            and mcp_server == skill.mcp_server
        )
    elif required_boundary == "mcp_required":
        valid = migration_state == "compatibility"
    else:
        valid = migration_state == "native"
    if not valid:
        raise RoleCapabilitySkillRegistryError(
            f"执行事实与目标边界不一致：{capability}/{execution_kind}/"
            f"{required_boundary}/{migration_state}。"
        )
    if skill.kind == "mcp_skill" and execution_kind == "provider":
        if required_boundary != "mcp_required" or migration_state != "compatibility":
            raise RoleCapabilitySkillRegistryError(
                f"Provider 执行的 MCP Skill 必须标记 compatibility：{capability}。"
            )


def _parse_roles(payload: Any, bindings: Mapping[str, CapabilityRoute]) -> dict[str, tuple[str, ...]]:
    if not isinstance(payload, dict):
        raise RoleCapabilitySkillRegistryError("roles 必须是对象。")
    result: dict[str, tuple[str, ...]] = {}
    for role_id, item in payload.items():
        data = _mapping(item, f"roles.{role_id}")
        raw_bindings = data.get("bindings")
        if not isinstance(raw_bindings, list) or any(not isinstance(value, str) for value in raw_bindings):
            raise RoleCapabilitySkillRegistryError(f"roles.{role_id}.bindings 必须是字符串数组。")
        if len(raw_bindings) != len(set(raw_bindings)):
            raise RoleCapabilitySkillRegistryError(f"roles.{role_id}.bindings 不得重复。")
        for binding in raw_bindings:
            if binding not in bindings:
                raise RoleCapabilitySkillRegistryError(
                    f"role binding 未注册：{role_id}/{binding}。"
                )
        result[str(role_id)] = tuple(raw_bindings)
    return result


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RoleCapabilitySkillRegistryError(f"{label} 必须是对象。")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoleCapabilitySkillRegistryError(f"{label} 必须是非空字符串。")
    return value


def _safe_relative_path(value: Any, label: str) -> str:
    path = _text(value, label)
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RoleCapabilitySkillRegistryError(f"{label} 必须是安全相对路径。")
    return path


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
