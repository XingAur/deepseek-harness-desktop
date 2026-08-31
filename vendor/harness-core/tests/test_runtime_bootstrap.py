from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.runtime_bootstrap import reexec_in_project_venv


class RuntimeBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime = self.root / ".venv" / "bin" / "python"
        self.runtime.parent.mkdir(parents=True)
        self.runtime.touch()
        self.expected_runtime = self.root.resolve() / ".venv" / "bin" / "python"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reexecs_supported_command_in_project_venv(self) -> None:
        calls: list[tuple[str, list[str], dict[str, str]]] = []

        def fake_execve(path: str, argv: list[str], environment: dict[str, str]) -> None:
            calls.append((path, argv, environment))

        reexecuted = reexec_in_project_venv(
            self.root,
            argv=("tools/harness_doctor.py", "--json"),
            environment={"SAFE_VALUE": "yes"},
            current_executable="/usr/bin/python3",
            execve=fake_execve,
        )

        self.assertTrue(reexecuted)
        self.assertEqual(1, len(calls))
        path, argv, environment = calls[0]
        self.assertEqual(str(self.expected_runtime), path)
        self.assertEqual([str(self.expected_runtime), "tools/harness_doctor.py", "--json"], argv)
        self.assertEqual("1", environment["HARNESS_VENV_REEXECED"])

    def test_does_not_reexec_when_already_in_venv_or_disabled(self) -> None:
        calls: list[object] = []

        self.assertFalse(
            reexec_in_project_venv(
                self.root,
                environment={},
                current_executable=str(self.expected_runtime),
                execve=lambda *_: calls.append("unexpected"),
            )
        )
        self.assertFalse(
            reexec_in_project_venv(
                self.root,
                environment={"HARNESS_DISABLE_VENV_REEXEC": "1"},
                current_executable="/usr/bin/python3",
                execve=lambda *_: calls.append("unexpected"),
            )
        )
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
