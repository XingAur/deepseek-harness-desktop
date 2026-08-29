from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.requirement_provider import (
    normalize_requirement_evidence,
    normalize_requirement_evidence_file,
    requirement_evidence_to_markdown,
    write_requirement_evidence_outputs,
)


STRICT_WARNING_CODES = {"unsupported_source_type", "title_missing", "description_missing", "source_read_failed"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize requirement source evidence into HIS Harness v0.23 readonly schema.")
    parser.add_argument("--source-type", default="", help="source type: yunxiao, tapd, jira, github_issue, manual or file")
    parser.add_argument("--input-file", default="", help="JSON or text file to normalize")
    parser.add_argument("--source-url", default="", help="original requirement URL or local source reference")
    parser.add_argument("--external-id", default="", help="external requirement id")
    parser.add_argument("--title", default="", help="manual requirement title")
    parser.add_argument("--description", default="", help="manual requirement description")
    parser.add_argument("--status", default="", help="manual requirement status")
    parser.add_argument("--assignee", default="", help="manual requirement assignee")
    parser.add_argument("--output-dir", default="", help="optional directory to write requirement_evidence.json/md")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when normalization has blocking warnings")
    args = parser.parse_args()

    if args.input_file:
        evidence = normalize_requirement_evidence_file(args.input_file, source_type=args.source_type)
    else:
        source_type = args.source_type or "manual"
        payload = {
            "source_type": source_type,
            "source_url": args.source_url,
            "external_id": args.external_id,
            "title": args.title,
            "description_text": args.description,
            "status": args.status,
            "assignee": args.assignee,
        }
        evidence = normalize_requirement_evidence(source_type=source_type, payload=payload, source_url=args.source_url)

    files = {}
    if args.output_dir:
        files = write_requirement_evidence_outputs(output_dir=args.output_dir, evidence=evidence)
        evidence = dict(evidence)
        evidence["files"] = files

    if args.json:
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    else:
        print(requirement_evidence_to_markdown(evidence))
        if files:
            print("")
            print(f"JSON: {files['json']}")
            print(f"Markdown: {files['markdown']}")

    if args.strict and strict_failed(evidence):
        raise SystemExit(1)


def strict_failed(evidence: dict) -> bool:
    warnings = evidence.get("warnings") or []
    return any(item.get("code") in STRICT_WARNING_CODES for item in warnings if isinstance(item, dict))


if __name__ == "__main__":
    main()
