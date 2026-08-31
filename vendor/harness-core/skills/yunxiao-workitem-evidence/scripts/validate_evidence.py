#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from yunxiao_evidence import validate_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 requirement-evidence.v2 JSON。")
    parser.add_argument("evidence_json", help="待校验的证据 JSON 文件")
    args = parser.parse_args()

    path = Path(args.evidence_json).expanduser()
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}")
        return 1
    errors = validate_evidence(evidence)
    if errors:
        for error in errors:
            print(f"INVALID: {error}")
        return 1
    print("VALID: requirement-evidence.v2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
