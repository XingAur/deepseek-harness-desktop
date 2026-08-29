from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ui_capture_template import (
    template_result_to_json,
    template_result_to_markdown,
    write_playwright_capture_template,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HIS Harness v0.10.3C Playwright/Chrome UI capture template files.")
    parser.add_argument("--output-dir", required=True, help="directory to write template files")
    parser.add_argument("--entity-id", default="", help="DFHIS id")
    parser.add_argument("--title", default="", help="demand or bug title")
    parser.add_argument("--route", default="", help="target HIS front-end route")
    parser.add_argument("--scenario-name", default="", help="UI capture scenario name")
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = parser.parse_args()

    result = write_playwright_capture_template(
        output_dir=args.output_dir,
        entity_id=args.entity_id,
        title=args.title,
        route=args.route,
        scenario_name=args.scenario_name,
    )
    if args.json:
        print(template_result_to_json(result))
    else:
        print(template_result_to_markdown(result))


if __name__ == "__main__":
    main()
