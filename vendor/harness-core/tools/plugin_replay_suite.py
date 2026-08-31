from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.plugin_replay_suite import (  # noqa: E402
    build_plugin_replay_failure_result,
    plugin_replay_result_to_markdown,
    run_plugin_replay_suite,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the isolated HIS plugin-migration replay suite."
    )
    parser.add_argument(
        "--manifest",
        default=str(
            PROJECT_ROOT
            / "fixtures"
            / "replay"
            / "plugin_migration_v1.json"
        ),
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_plugin_replay_suite(
            Path(args.manifest),
            workspace_root=output_dir,
        )
    except OSError:
        result = build_plugin_replay_failure_result(
            "plugin_replay_manifest_unavailable"
        )
    except (ValueError, TypeError):
        result = build_plugin_replay_failure_result(
            "plugin_replay_manifest_invalid"
        )
    except Exception:
        result = build_plugin_replay_failure_result(
            "plugin_replay_suite_failed"
        )
    (output_dir / "plugin_replay_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "plugin_replay_report.md").write_text(
        plugin_replay_result_to_markdown(result),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "result_hash": result["result_hash"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
