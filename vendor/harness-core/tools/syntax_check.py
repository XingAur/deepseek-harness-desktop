from __future__ import annotations

import ast
import sys
from pathlib import Path


SOURCE_DIRECTORIES = ("app", "tools", "harnesses", "tests")


def iter_python_files(root: Path):
    for directory_name in SOURCE_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def main() -> int:
    root = Path.cwd().resolve()
    failures: list[tuple[Path, Exception]] = []
    files_checked = 0
    for path in iter_python_files(root):
        files_checked += 1
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            failures.append((path, exc))
    for path, exc in failures:
        print(f"syntax error: {path}: {exc}", file=sys.stderr)
    if failures:
        return 1
    print(f"AST syntax OK: {files_checked} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
