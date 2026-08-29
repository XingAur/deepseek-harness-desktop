from __future__ import annotations

import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app import database


@dataclass(frozen=True)
class RuntimeStorage:
    root: Path
    output_dir: Path
    database_path: Path
    original_database_path: Path
    retained: bool
    run_namespace: str = ""


@contextmanager
def ephemeral_runtime_storage(
    *,
    prefix: str,
    retain_output: bool = False,
    output_dir: str | Path | None = None,
) -> Iterator[RuntimeStorage]:
    """Use disposable output and SQLite storage unless retention is explicit."""
    original_database_path = database.DB_PATH
    if retain_output:
        persistent_output_dir = Path(output_dir or "runs").expanduser()
        persistent_output_dir.mkdir(parents=True, exist_ok=True)
        persistent_database_path = persistent_output_dir / "harness.sqlite"
        database.DB_PATH = persistent_database_path
        try:
            yield RuntimeStorage(
                root=persistent_output_dir,
                output_dir=persistent_output_dir,
                database_path=persistent_database_path,
                original_database_path=original_database_path,
                retained=True,
                run_namespace=uuid.uuid4().hex,
            )
        finally:
            database.DB_PATH = original_database_path
        return

    with tempfile.TemporaryDirectory(prefix=f"his_harness_{prefix}_") as temporary_root:
        root = Path(temporary_root)
        temporary_database_path = root / "harness.sqlite"
        database.DB_PATH = temporary_database_path
        try:
            yield RuntimeStorage(
                root=root,
                output_dir=root / "output",
                database_path=temporary_database_path,
                original_database_path=original_database_path,
                retained=False,
                run_namespace=uuid.uuid4().hex,
            )
        finally:
            database.DB_PATH = original_database_path
