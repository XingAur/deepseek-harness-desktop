from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.external_io_inventory import (
    ExternalIoScanError,
    inventory_to_dict,
    scan_roots,
)
from app.external_io_policy import (
    ExternalIoPolicyError,
    evaluate_inventory,
    load_external_io_policy,
)


class CliError(ValueError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError("invalid command arguments")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description="Read-only external I/O architecture inventory.")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("--policy", required=True)
    scan.add_argument("--output", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--policy", required=True)
    validate.add_argument("--matrix", required=True)
    validate.add_argument("--format", choices=("summary",), default="summary")
    return parser


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _harness_root_for_policy(policy_path: Path) -> Path:
    parent = policy_path.resolve().parent
    return parent.parent if parent.name == "config" else parent


def _load_policy(policy_value: str):
    policy_path = Path(policy_value).resolve()
    if not policy_path.is_file():
        raise CliError("policy file is missing")
    harness_root = _harness_root_for_policy(policy_path)
    return (
        load_external_io_policy(
            policy_path,
            harness_root=harness_root,
            capabilities_config_path=harness_root / "config/capabilities.json",
            plugin_inventory_path=harness_root / "config/plugin_inventory.json",
        ),
        harness_root,
    )


def _validate_output_path(output_value: str, harness_root: Path) -> Path:
    output = Path(output_value)
    if not output.is_absolute():
        raise CliError("output path must be absolute")
    output = output.resolve()
    temporary_root = Path("/private/tmp").resolve()
    if not (_within(output, harness_root) or _within(output, temporary_root)):
        raise CliError("output path is outside the allowed roots")
    if not output.parent.is_dir() or output.is_dir():
        raise CliError("output parent is unavailable")
    return output


def _summary(report) -> str:
    dispositions = [
        item.get("disposition")
        for item in report.details
        if item.get("kind") in {"finding", "matrix_route"}
    ]
    fields = (
        ("status", report.status),
        ("findings", report.finding_count),
        ("mcp_required", dispositions.count("mcp_required")),
        ("worker", dispositions.count("worker_allowed")),
        ("internal", dispositions.count("control_plane_internal")),
        ("compatibility_debt", report.compatibility_debt_count),
        ("unclassified", report.unclassified_count),
        ("source_drift", report.source_drift_count),
        ("forbidden", report.forbidden_count),
        ("skill_contract_errors", report.skill_contract_error_count),
    )
    return " ".join(f"{name}={value}" for name, value in fields)


def execute(arguments: argparse.Namespace) -> int:
    policy, harness_root = _load_policy(arguments.policy)
    inventory = scan_roots(policy.roots)
    if arguments.command == "scan":
        output = _validate_output_path(arguments.output, harness_root)
        output.write_text(
            json.dumps(
                inventory_to_dict(inventory),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"status=scanned findings={len(inventory.findings)}")
        return 0

    matrix_path = Path(arguments.matrix).resolve()
    if not matrix_path.is_file() or not (
        _within(matrix_path, harness_root) or _within(matrix_path, PROJECT_ROOT)
    ):
        raise CliError("matrix path is outside the allowed roots")
    report = evaluate_inventory(inventory, policy, matrix_path=matrix_path)
    print(_summary(report))
    return 0 if report.status == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
        return execute(arguments)
    except (CliError, ExternalIoPolicyError, ExternalIoScanError, OSError):
        print("external I/O inventory configuration is invalid", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
