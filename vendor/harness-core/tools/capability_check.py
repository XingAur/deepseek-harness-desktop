from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.capability_contracts import CapabilityContractError, CapabilityRequest
from app.capability_permissions import PermissionDecision, evaluate_capability_permission
from app.capability_registry import CapabilityDescriptor, CapabilityRegistry, CapabilityRegistryError
from app.capability_runtime import CapabilityExecution, CapabilityRuntime


CHECK_SCHEMA_VERSION = "his-capability-check.v1"
CONFIG_SCHEMA_VERSION = "his-capability-runtime-config.v1"
CONFIG_REQUIRED_FIELDS = frozenset({
    "schema_version", "routing_mode", "plugin_roots", "external_writes_default",
    "default_timeout_seconds",
})
CONFIG_OPTIONAL_FIELDS = frozenset({"knowledge_home"})
CONFIG_FIELDS = CONFIG_REQUIRED_FIELDS | CONFIG_OPTIONAL_FIELDS
ROUTING_MODES = frozenset({"legacy", "observe", "enforce"})


class CliError(ValueError):
    pass


class CheckArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError("命令参数无效。")


@dataclass(frozen=True)
class RuntimeConfig:
    routing_mode: str
    plugin_roots: tuple[str, ...]
    external_writes_default: bool
    default_timeout_seconds: int
    knowledge_home: str


def build_parser() -> argparse.ArgumentParser:
    parser = CheckArgumentParser(description="Read-only capability diagnostics.")
    parser.add_argument("--config", required=True, help="runtime capability config JSON")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "validate"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true", help="emit stable JSON")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--capability", required=True)
    inspect.add_argument("--provider", default="")
    inspect.add_argument("--json", action="store_true", help="emit stable JSON")
    preview = commands.add_parser("preview")
    preview.add_argument("--request", required=True, help="preview request JSON")
    preview.add_argument("--json", action="store_true", help="emit stable JSON")
    return parser


def load_runtime_config(path_value: str) -> RuntimeConfig:
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CliError("运行时配置无法解析。") from exc
    if not isinstance(payload, dict):
        raise CliError("运行时配置必须是对象。")
    missing = CONFIG_REQUIRED_FIELDS - payload.keys()
    unexpected = payload.keys() - CONFIG_FIELDS
    if missing:
        raise CliError("运行时配置缺少字段：" + ", ".join(sorted(missing)) + "。")
    if unexpected:
        raise CliError("运行时配置存在未知字段：" + ", ".join(sorted(unexpected)) + "。")
    if payload["schema_version"] != CONFIG_SCHEMA_VERSION:
        raise CliError(f"schema_version 必须为 {CONFIG_SCHEMA_VERSION}。")
    routing_mode = payload["routing_mode"]
    if not isinstance(routing_mode, str) or routing_mode not in ROUTING_MODES:
        raise CliError("routing_mode 必须是 legacy、observe 或 enforce。")
    roots = payload["plugin_roots"]
    if (
        not isinstance(roots, list)
        or not roots
        or any(not isinstance(item, str) or not item.strip() for item in roots)
        or len(set(roots)) != len(roots)
    ):
        raise CliError("plugin_roots 必须是唯一非空字符串数组。")
    external_writes_default = payload["external_writes_default"]
    if not isinstance(external_writes_default, bool):
        raise CliError("external_writes_default 必须是布尔值。")
    timeout = payload["default_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise CliError("default_timeout_seconds 必须是正整数。")
    knowledge_home = payload.get(
        "knowledge_home",
        "/Users/lym/.local/share/his-knowledge",
    )
    if (
        not isinstance(knowledge_home, str)
        or not knowledge_home.strip()
        or not Path(knowledge_home).is_absolute()
    ):
        raise CliError("knowledge_home 必须是绝对路径。")
    config_directory = Path(path_value).expanduser().resolve().parent
    resolved_roots = tuple(
        str(
            root.expanduser().resolve()
            if root.is_absolute()
            else (config_directory / root).resolve()
        )
        for root in (Path(item) for item in roots)
    )
    return RuntimeConfig(
        routing_mode,
        resolved_roots,
        external_writes_default,
        timeout,
        knowledge_home,
    )


def _descriptor_data(descriptor: CapabilityDescriptor) -> dict[str, Any]:
    return {
        "capability": descriptor.name,
        "provider": descriptor.provider,
        "plugin": descriptor.plugin,
        "plugin_version": descriptor.plugin_version,
        "contract_version": descriptor.contract_version,
        "mutation_level": descriptor.mutation_level.name,
        "credential_class": descriptor.credential_class,
        "enabled": descriptor.enabled,
        "disabled_reason": descriptor.disabled_reason,
        "scopes": list(descriptor.scopes),
        "entrypoint": str(descriptor.entrypoint) if descriptor.entrypoint is not None else None,
    }


def _permission_data(permission: PermissionDecision) -> dict[str, Any]:
    return {
        "status": permission.status,
        "allowed": permission.allowed,
        "required_level": permission.required_level.name,
        "blockers": list(permission.blockers),
    }


def _execution_data(execution: CapabilityExecution) -> dict[str, Any]:
    return {
        "descriptor": _descriptor_data(execution.descriptor),
        "duration_ms": execution.duration_ms,
        "result": execution.result.to_dict(),
    }


def _read_request(path_value: str) -> CapabilityRequest:
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        request = CapabilityRequest.from_dict(payload)
    except (OSError, json.JSONDecodeError, CapabilityContractError, ValueError) as exc:
        raise CliError("预览请求无法解析。") from exc
    if request.mode != "preview":
        raise CliError("preview 只接受 mode=preview 的请求。")
    return request


def _response(command: str, status: str, *, data: Mapping[str, Any] | None = None, error: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA_VERSION,
        "command": command,
        "status": status,
    }
    if data is not None:
        payload["data"] = dict(data)
    if error:
        payload["error"] = {"code": "CAPABILITY_CHECK_FAILED", "message": error}
    return payload


def execute(args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    request = _read_request(args.request) if command == "preview" else None
    config = load_runtime_config(args.config)
    registry = CapabilityRegistry.from_plugin_roots(config.plugin_roots)
    if command == "list":
        descriptors = sorted(registry.descriptors, key=lambda item: (item.name, item.provider))
        return _response(command, "success", data={"capabilities": [_descriptor_data(item) for item in descriptors]})
    if command == "inspect":
        descriptor = registry.resolve(args.capability, args.provider)
        return _response(command, "success", data={"capability": _descriptor_data(descriptor)})
    if command == "validate":
        return _response(command, "success", data={"capability_count": len(registry)})
    if command == "preview" and request is not None:
        descriptor = registry.resolve(request.capability, request.provider)
        permission = evaluate_capability_permission(
            request=request,
            declared_level=descriptor.mutation_level,
            declared_scopes=descriptor.scopes,
            external_writes_default=config.external_writes_default,
        )
        runtime = CapabilityRuntime(
            registry,
            external_writes_default=config.external_writes_default,
            default_timeout_seconds=config.default_timeout_seconds,
        )
        execution = runtime.execute(request)
        status = execution.result.status
        return _response(command, status, data={
            "permission": _permission_data(permission),
            "execution": _execution_data(execution),
        })
    raise CliError("不支持的只读诊断命令。")


def _is_json_requested(argv: list[str]) -> bool:
    return "--json" in argv


def _command_from_argv(argv: list[str]) -> str:
    commands = frozenset({"list", "inspect", "validate", "preview"})
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--config":
            index += 2
            continue
        if item.startswith("--config="):
            index += 1
            continue
        if item in commands:
            return item
        if not item.startswith("-"):
            return "unknown"
        index += 1
    return "unknown"


def _render(payload: Mapping[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return
    status = payload["status"]
    if status == "success":
        print(f"{payload['command']}: success")
    else:
        print(f"{payload['command']}: {status}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    as_json = _is_json_requested(raw_argv)
    command = _command_from_argv(raw_argv)
    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
        payload = execute(args)
    except CliError as exc:
        payload = _response(command, "failed", error=str(exc))
    except CapabilityRegistryError:
        payload = _response(command, "failed", error="能力清单校验失败。")
    _render(payload, as_json)
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
