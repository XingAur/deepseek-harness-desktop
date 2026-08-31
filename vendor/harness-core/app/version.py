from __future__ import annotations

import re
from pathlib import Path


VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9A-Za-z-]+)+$")


def read_version(path: str | Path = VERSION_FILE) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError("版本文件必须包含类似 0.66.0 的安全版本号。")
    return value


VERSION = read_version()
