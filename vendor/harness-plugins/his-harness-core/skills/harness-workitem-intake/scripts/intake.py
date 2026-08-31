#!/usr/bin/env python3
"""Canonical provider-neutral intake implementation owned by his-harness-core."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


SKILLS_ROOT = Path(__file__).resolve().parents[2]
HISTORY_SCRIPTS = SKILLS_ROOT / "harness-history" / "scripts"
sys.path.insert(0, str(HISTORY_SCRIPTS))

from history_manager import archive_evidence, record_stage  # noqa: E402


CollectAdapter = Callable[..., dict[str, Any]]
ArchiveAdapter = Callable[..., dict[str, str]]
StageAdapter = Callable[..., dict[str, Any]]


def detect_provider(source: str) -> tuple[str, str]:
    value = str(source or "").strip()
    parsed = urllib.parse.urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme and parsed.netloc:
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials in work item URL are not allowed")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("unsupported work item provider: invalid URL port") from exc
        if (
            parsed.scheme.lower() != "https"
            or host != "devops.aliyun.com"
            or port not in {None, 443}
        ):
            raise ValueError("unsupported work item provider")
        match = re.search(
            r"(?:^|/)(DFHIS-\d+)(?:/|$)",
            parsed.path,
            re.IGNORECASE,
        )
        if match is None:
            raise ValueError("Yunxiao work item ID could not be parsed")
        return "YUNXIAO", match.group(1).upper()
    ticket_id = value.upper()
    if re.fullmatch(r"DFHIS-\d+", ticket_id):
        return "YUNXIAO", ticket_id
    raise ValueError("invalid work item input; use one URL or one DFHIS ticket ID")


def process_intake(
    *,
    source: str,
    history_root: str | Path,
    run_id: str | None = None,
    provider_evidence_dir: str | Path | None = None,
    collect_adapter: CollectAdapter | None = None,
    archive_adapter: ArchiveAdapter | None = None,
    stage_adapter: StageAdapter | None = None,
) -> dict[str, Any]:
    if (provider_evidence_dir is None) == (collect_adapter is None):
        raise ValueError(
            "exactly one provider evidence input is required: use "
            "provider_evidence_dir from $yunxiao-workitem-read or one "
            "provider-neutral collect_adapter"
        )
    provider, ticket_id = detect_provider(source)
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(history_root).resolve()
    archiver = archive_adapter or archive_evidence
    stage_recorder = stage_adapter or record_stage
    staging_dir: Path | None = None
    try:
        collection: dict[str, Any] = {}
        if collect_adapter is not None:
            staging_root = root / ".staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(
                tempfile.mkdtemp(
                    prefix=f"{provider}-{ticket_id}-{run_id}-",
                    dir=str(staging_root),
                )
            )
            collection = collect_adapter(
                source=ticket_id,
                output_dir=staging_dir,
            )
            if not isinstance(collection, dict):
                raise ValueError(
                    "provider-neutral collect_adapter must return an object"
                )
            evidence_source = staging_dir
        else:
            evidence_source = Path(provider_evidence_dir)
        archive = archiver(
            source_dir=evidence_source,
            history_root=root,
            provider=provider,
            ticket_id=ticket_id,
            run_id=run_id,
            intake_required=True,
        )
        gate = str(archive.get("decision_gate") or collection.get("decision_gate") or "")
        completeness = str(
            archive.get("completeness") or collection.get("completeness") or ""
        )
        mutation_allowed = gate == "ready_for_analysis"
        readonly_discovery_allowed = gate in {
            "ready_for_analysis",
            "needs_requirement_confirmation",
        }
        intake_status = (
            "accepted"
            if mutation_allowed
            else (
                "accepted_for_readonly_discovery"
                if readonly_discovery_allowed
                else "blocked"
            )
        )
        request_record = {
            "contract_version": "harness-intake.v1",
            "provider": provider,
            "ticket_id": ticket_id,
            "run_id": run_id,
            "source": ticket_id,
            "adapter_skill": "yunxiao-workitem-read",
            "requested_at": datetime.now().astimezone().isoformat(),
            "decision_gate": gate,
            "completeness": completeness,
            "intake_status": intake_status,
            "mutation_allowed": mutation_allowed,
            "readonly_discovery_allowed": readonly_discovery_allowed,
            "next_action": (
                "start_readonly_analysis"
                if mutation_allowed
                else (
                    "start_readonly_discovery"
                    if readonly_discovery_allowed
                    else "complete_or_confirm_requirement_evidence"
                )
            ),
        }
        run_dir = Path(archive["run_dir"])
        _create_json(run_dir / "intake" / "request.json", request_record)
        if gate == "needs_requirement_confirmation":
            stage_recorder(
                task_dir=archive["task_dir"],
                run_id=run_id,
                stage="analysis",
                status="pending",
                summary=(
                    f"需求证据门禁为 {gate}，完整性为 {completeness}；"
                    "允许先做只读代码侦查，禁止生成 patch 或进入修改。"
                ),
            )
        elif not mutation_allowed:
            stage_recorder(
                task_dir=archive["task_dir"],
                run_id=run_id,
                stage="analysis",
                status="blocked",
                summary=(
                    f"需求证据门禁为 {gate}，完整性为 {completeness}；"
                    "仅允许补证和只读定位。"
                ),
            )
        return {
            "status": gate,
            "intake_status": intake_status,
            "provider": provider,
            "ticket_id": ticket_id,
            "run_id": run_id,
            **archive,
        }
    finally:
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir)


def _create_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class _HistoryRootAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, "history_root_explicit", True)


class _IntakeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        super().error("invalid arguments")

    def parse_args(
        self,
        args: list[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)
        if parsed.legacy_source and parsed.canonical_source:
            self.error("choose either positional source or --source, not both")
        if not parsed.legacy_source and not parsed.canonical_source:
            self.error("work item source is required")
        if parsed.output_dir and parsed.history_root_explicit:
            self.error("choose either --history-root or --output-dir, not both")
        parsed.source = parsed.canonical_source or parsed.legacy_source
        if parsed.output_dir:
            parsed.history_root = parsed.output_dir
        del parsed.legacy_source
        del parsed.canonical_source
        del parsed.output_dir
        del parsed.history_root_explicit
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = _IntakeArgumentParser(
        description="Accept provider evidence and archive one governed work-item run."
    )
    parser.add_argument("legacy_source", nargs="?", help="Work item URL or ID")
    parser.add_argument("--source", dest="canonical_source", help="Work item URL or ID")
    parser.add_argument(
        "--history-root",
        default="/Users/lym/WorkCode/ai/HarnessHistory",
        action=_HistoryRootAction,
    )
    parser.add_argument(
        "--output-dir",
        help="Canonical alias for the intake history/output root",
    )
    parser.add_argument(
        "--provider-evidence-dir",
        required=True,
        help="Sanitized readonly evidence directory produced by the provider skill",
    )
    parser.add_argument("--run-id")
    parser.set_defaults(history_root_explicit=False)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = process_intake(
            source=args.source,
            history_root=args.history_root,
            run_id=args.run_id,
            provider_evidence_dir=args.provider_evidence_dir,
        )
    except Exception:
        print(
            json.dumps(
                {
                    "status": "fetch_failed",
                    "error": "intake failed; inspect safe local configuration and evidence",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    if result["intake_status"] in {
        "accepted",
        "accepted_for_readonly_discovery",
    }:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
