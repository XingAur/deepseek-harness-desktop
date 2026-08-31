from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.release_bundle import build_release_bundle
from app.version import VERSION


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic secret-free HIS Harness source bundle.")
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = build_release_bundle(
            project_root=PROJECT_ROOT,
            output_dir=args.output_dir,
            version=args.version,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
