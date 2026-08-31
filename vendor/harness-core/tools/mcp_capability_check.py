#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mcp_capability_registry import (  # noqa: E402
    McpCapabilityNotFound,
    McpCapabilityRegistry,
    McpCapabilityRegistryError,
)


class _ArgumentError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Validate and inspect MCP capability metadata")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--manifest", type=Path, required=True)
    list_parser.add_argument("--format", choices=("summary",), default="summary")

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--manifest", type=Path, required=True)
    inspect.add_argument("--capability", required=True)
    inspect.add_argument("--provider", required=True)
    return parser


def _harness_root(manifest: Path) -> Path:
    resolved = manifest.resolve()
    return resolved.parent.parent if resolved.parent.name == "config" else resolved.parent


def _enabled(value: bool) -> str:
    return "true" if value else "false"


def _summary(descriptor: object, *, include_reason: bool = False) -> str:
    fields = [
        f"capability={descriptor.capability}",
        f"provider={descriptor.provider}",
        f"server={descriptor.server}",
        f"tool={descriptor.tool}",
        f"contract_version={descriptor.contract_version}",
        f"mutation_level={descriptor.mutation_level.name}",
        f"enabled={_enabled(descriptor.enabled)}",
    ]
    if include_reason and descriptor.disabled_reason:
        fields.append(f"disabled_reason={descriptor.disabled_reason}")
    return " ".join(fields)


def main(arguments: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(arguments)
        registry = McpCapabilityRegistry.from_file(
            args.manifest,
            harness_root=_harness_root(args.manifest),
        )
        descriptors = registry.list_capabilities()
        if args.command == "validate":
            enabled = sum(1 for item in descriptors if item.enabled)
            print(
                "status=valid "
                f"capabilities={len(descriptors)} "
                f"enabled={enabled} disabled={len(descriptors) - enabled}"
            )
            return 0
        if args.command == "list":
            for descriptor in descriptors:
                print(_summary(descriptor))
            return 0
        descriptor = registry.resolve(args.capability, args.provider)
        print(_summary(descriptor, include_reason=True))
        return 0
    except McpCapabilityNotFound:
        print("status=not_found")
        return 1
    except (_ArgumentError, McpCapabilityRegistryError, OSError):
        print("status=invalid MCP capability metadata", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
