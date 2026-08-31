"""Keep supported Harness command-line entrypoints on the project runtime.

The project deliberately keeps third-party dependencies in ``.venv``.  A
developer should still be able to run a documented command with ``python3``:
the command re-executes itself once with the local virtual environment instead
of producing a misleading "dependency missing" diagnosis.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence


_REEXEC_MARKER = "HARNESS_VENV_REEXECED"
_DISABLE_REEXEC_MARKER = "HARNESS_DISABLE_VENV_REEXEC"


def reexec_in_project_venv(
    project_root: Path,
    *,
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
    current_executable: str | None = None,
    execve: Callable[[str, list[str], dict[str, str]], object] = os.execve,
) -> bool:
    """Re-execute this CLI under ``project_root/.venv/bin/python`` when present.

    Returns ``False`` when the current process is already suitable or automatic
    re-execution is explicitly disabled.  ``os.execve`` normally never returns;
    the return value makes the helper deterministic in unit tests.
    """

    # Do not resolve the runtime symlink here.  A venv Python commonly points
    # at the system binary; resolving both paths would incorrectly conclude
    # that a system-Python invocation is already inside the venv.
    runtime = Path(project_root).resolve() / ".venv" / "bin" / "python"
    active_environment = dict(os.environ if environment is None else environment)
    if (
        not runtime.is_file()
        or active_environment.get(_REEXEC_MARKER) == "1"
        or active_environment.get(_DISABLE_REEXEC_MARKER) == "1"
    ):
        return False

    active = Path(current_executable or sys.executable).absolute()
    if active == runtime:
        return False

    command = [str(runtime), *(list(sys.argv) if argv is None else list(argv))]
    active_environment[_REEXEC_MARKER] = "1"
    execve(str(runtime), command, active_environment)
    return True
