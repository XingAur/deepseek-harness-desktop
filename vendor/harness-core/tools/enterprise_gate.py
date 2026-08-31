from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.enterprise_gate import (
    DEFAULT_GATE_STAGES,
    enterprise_gate_to_markdown,
    run_enterprise_gate,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline HIS Harness enterprise core gate.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--stages", default=",".join(DEFAULT_GATE_STAGES))
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_enterprise_gate(
            project_root=PROJECT_ROOT,
            output_dir=output_dir,
            iterations=args.iterations,
            stages=args.stages.split(","),
        )
    except ValueError as exc:
        parser.error(str(exc))
    (output_dir / "enterprise_gate_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "enterprise_gate_report.md").write_text(
        enterprise_gate_to_markdown(result),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "iterations": result["iterations_requested"],
                "result_hash": result["result_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
