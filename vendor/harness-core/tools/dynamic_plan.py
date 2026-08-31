from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.dynamic_planning import DynamicPlanningRequest, build_dynamic_plan, write_dynamic_plan_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 HIS Harness 只读动态团队与子任务 DAG 计划。")
    parser.add_argument("--request-file", required=True, help="DynamicPlanningRequest JSON 文件。")
    parser.add_argument("--output-dir", required=True, help="规划产物目录。")
    parser.add_argument("--enable", action="store_true", help="显式启用 dynamic-plan；默认关闭以保持旧流程不变。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    request_payload = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    request = DynamicPlanningRequest.from_dict(request_payload)
    plan = build_dynamic_plan(request, enabled=args.enable)
    paths = write_dynamic_plan_outputs(Path(args.output_dir), plan)
    print(
        json.dumps(
            {
                "status": plan.status,
                "planning_mode": plan.planning_mode,
                "complexity": plan.assessment.level,
                "output_files": [str(path) for path in paths],
            },
            ensure_ascii=False,
        )
    )
    return 0 if plan.status in {"disabled", "ready", "needs_evidence", "needs_human_confirmation"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
