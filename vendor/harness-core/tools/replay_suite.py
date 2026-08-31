from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.real_replay_suite import replay_result_to_markdown, run_replay_suite


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local desensitized HIS real-requirement replay suite.")
    parser.add_argument(
        "--manifest",
        default=str(PROJECT_ROOT / "fixtures" / "replay" / "real_requirements_v1.json"),
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_replay_suite(Path(args.manifest))
    (output_dir / "real_replay_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "real_replay_report.md").write_text(
        replay_result_to_markdown(result),
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "result_hash": result["result_hash"]}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
