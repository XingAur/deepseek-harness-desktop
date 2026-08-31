#!/usr/bin/env python3
"""Describe and validate the provider-neutral Harness host bridge."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent_backend_protocol import parse_request, request_hash
from app.host_integration_contract import (
    build_host_integration_status,
    negotiate_host,
    parse_host_negotiation_request,
)


_SAFE_BRIDGE_ERRORS = frozenset({
    "agent_backend_request_invalid",
    "host_negotiation_invalid",
    "host_unknown",
    "host_role_unsupported",
    "host_capability_unsupported",
    "host_mutation_unsupported",
    "host_mutation_not_authorized",
})
_MAX_NEGOTIATION_BYTES = 256 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness_agent_bridge.py", add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("describe", add_help=False)
    validate = commands.add_parser("validate-request", add_help=False)
    validate.add_argument("--request-file", required=True)
    negotiate = commands.add_parser("negotiate", add_help=False)
    negotiate.add_argument("--request-file", required=True)
    negotiate.add_argument("--authorized-mutation-level", required=True)
    return parser


def _emit(value: dict[str, object], *, code: int = 0) -> int:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


def main(arguments: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(arguments)
        if args.command == "describe":
            return _emit({
                "schema_version": "his-agent-backend-bridge.v1",
                "transport": "stdio-jsonl",
                "operations": ["describe", "validate-request", "negotiate"],
                "provider_neutral": True,
                "database_access": False,
                "credential_access": False,
                "network_access": False,
                "hosts": build_host_integration_status()["hosts"],
            })
        path = Path(args.request_file)
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > _MAX_NEGOTIATION_BYTES
        ):
            return _emit({"ok": False, "error_code": "agent_backend_request_invalid"}, code=2)
        if args.command == "negotiate":
            request = parse_host_negotiation_request(
                json.loads(path.read_bytes().decode("utf-8", "strict"))
            )
            return _emit({
                "ok": True,
                "valid": True,
                "negotiation": negotiate_host(
                    request,
                    authorized_mutation_level=args.authorized_mutation_level,
                ),
            })
        request = parse_request(path.read_bytes())
        return _emit({
            "ok": True,
            "valid": True,
            "schema_version": "his-agent-backend-bridge.v1",
            "request_schema_version": "his-agent-backend-request.v1",
            "request_hash": request_hash(request),
        })
    except SystemExit:
        raise
    except ValueError as error:
        error_code = str(error)
        if error_code not in _SAFE_BRIDGE_ERRORS:
            error_code = "agent_backend_request_invalid"
        return _emit({"ok": False, "error_code": error_code}, code=2)
    except Exception:
        return _emit({"ok": False, "error_code": "agent_backend_request_invalid"}, code=2)


if __name__ == "__main__":
    raise SystemExit(main())
