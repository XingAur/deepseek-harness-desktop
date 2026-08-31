from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.external_io_inventory import (
    INVENTORY_SCHEMA_VERSION,
    ExternalIoScanError,
    ScanRoot,
    inventory_to_dict,
    scan_javascript_source,
    scan_python_source,
    scan_roots,
    scan_shell_source,
    scan_skill_markdown,
)


HARNESS_ROOT = Path(__file__).resolve().parents[1]


class ExternalIoInventoryTests(unittest.TestCase):
    def test_detects_network_database_and_process_calls(self) -> None:
        source = """
import subprocess
import urllib.request
import psycopg

urllib.request.urlopen("https://example.invalid")
psycopg.connect("postgresql://example.invalid/db")
subprocess.run(["git", "status"], check=False)
"""

        findings = scan_python_source(
            source,
            root_id="fixture",
            relative_path="sample.py",
            file_sha256="0" * 64,
        )

        self.assertEqual(
            [(item.category, item.symbol) for item in findings],
            [
                ("database", "psycopg.connect"),
                ("network", "urllib.request.urlopen"),
                ("process", "subprocess.run"),
            ],
        )

    def test_resolves_python_import_aliases(self) -> None:
        source = """
import urllib.request as request
from subprocess import run as execute

request.urlopen("https://example.invalid")
execute(["git", "status"])
"""

        findings = scan_python_source(
            source,
            root_id="fixture",
            relative_path="aliases.py",
            file_sha256="1" * 64,
        )

        self.assertEqual(
            [(item.category, item.symbol) for item in findings],
            [
                ("network", "urllib.request.urlopen"),
                ("process", "subprocess.run"),
            ],
        )

    def test_detects_bounded_opener_and_dynamic_psycopg_connection_paths(self) -> None:
        source = """
import urllib.request

opener = urllib.request.build_opener()
opener.open(request)
self._psycopg.connect(dsn)
"""

        findings = scan_python_source(
            source,
            root_id="fixture",
            relative_path="adapters.py",
            file_sha256="c" * 64,
        )

        self.assertEqual(
            [
                ("database", "self._psycopg.connect"),
                ("network", "opener.open"),
                ("network", "urllib.request.build_opener"),
            ],
            [(item.category, item.symbol) for item in findings],
        )

    def test_does_not_treat_url_parsing_as_network_io(self) -> None:
        source = "from urllib.parse import urlparse\nurlparse('https://example.invalid')\n"

        findings = scan_python_source(
            source,
            root_id="fixture",
            relative_path="parse_only.py",
            file_sha256="2" * 64,
        )

        self.assertEqual(findings, ())

    def test_fingerprint_is_stable_when_only_line_numbers_change(self) -> None:
        first = scan_python_source(
            "import subprocess\nsubprocess.run(['git', 'status'])\n",
            root_id="fixture",
            relative_path="worker.py",
            file_sha256="3" * 64,
        )[0]
        second = scan_python_source(
            "\n\nimport subprocess\nsubprocess.run(['git', 'status'])\n",
            root_id="fixture",
            relative_path="worker.py",
            file_sha256="4" * 64,
        )[0]

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.line, second.line)
        self.assertNotEqual(first.file_sha256, second.file_sha256)

    def test_detects_shell_and_javascript_external_io(self) -> None:
        shell = scan_shell_source(
            "curl https://example.invalid\npsql service=readonly\ngit status\n",
            root_id="fixture",
            relative_path="check.sh",
            file_sha256="5" * 64,
        )
        javascript = scan_javascript_source(
            "await fetch(url); childProcess.spawn('git', ['status']);\n",
            root_id="fixture",
            relative_path="check.js",
            file_sha256="6" * 64,
        )

        self.assertEqual(
            [(item.category, item.symbol) for item in shell],
            [("database", "psql"), ("network", "curl"), ("process", "git")],
        )
        self.assertEqual(
            [(item.category, item.symbol) for item in javascript],
            [("network", "fetch"), ("process", "childProcess.spawn")],
        )

    def test_ignores_comments_and_non_executable_string_literals(self) -> None:
        shell = scan_shell_source(
            "# curl https://example.invalid\nprintf '%s' 'psql service=readonly'\n",
            root_id="fixture",
            relative_path="comments.sh",
            file_sha256="7" * 64,
        )
        javascript = scan_javascript_source(
            "// fetch(url)\nconst example = \"axios.get(url)\";\n",
            root_id="fixture",
            relative_path="comments.js",
            file_sha256="8" * 64,
        )

        self.assertEqual(shell, ())
        self.assertEqual(javascript, ())

    def test_unresolved_shell_command_is_a_review_finding_without_variable_value(self) -> None:
        findings = scan_shell_source(
            "$HARNESS_COMMAND --check\n$DATABASE_PASSWORD_HELPER --read\n",
            root_id="fixture",
            relative_path="dynamic.sh",
            file_sha256="a" * 64,
        )

        self.assertEqual(
            [(item.category, item.symbol) for item in findings],
            [("credential", "dynamic-command"), ("process", "dynamic-command")],
        )
        self.assertNotIn("PASSWORD", json.dumps(inventory_to_dict(
            type("Inventory", (), {
                "schema_version": INVENTORY_SCHEMA_VERSION,
                "generated_at": "2026-08-30T00:00:00Z",
                "roots": (),
                "findings": findings,
            })()
        )))

    def test_shell_line_continuation_preserves_the_command_finding(self) -> None:
        findings = scan_shell_source(
            "curl \\\n+  https://example.invalid\n",
            root_id="fixture",
            relative_path="continued.sh",
            file_sha256="b" * 64,
        )

        self.assertEqual([("network", "curl")], [
            (item.category, item.symbol) for item in findings
        ])
        self.assertEqual(1, findings[0].line)

    def test_skill_fenced_connection_code_is_reported_separately(self) -> None:
        findings = scan_skill_markdown(
            """# Skill

Prose mentioning requests.get is not executable.

```python
import requests
requests.get(url)
```
""",
            root_id="fixture",
            relative_path="skills/example/SKILL.md",
            file_sha256="9" * 64,
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "network")
        self.assertEqual(findings[0].symbol, "requests.get")

    def test_scan_roots_ignores_tests_caches_data_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative in (
                "tests/test_network.py",
                ".venv/lib/site.py",
                "data/generated.py",
                "__pycache__/cached.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "import urllib.request\nurllib.request.urlopen('https://example.invalid')\n",
                    encoding="utf-8",
                )
            outside = root.parent / f"{root.name}-outside.py"
            outside.write_text(
                "import urllib.request\nurllib.request.urlopen('https://example.invalid')\n",
                encoding="utf-8",
            )
            try:
                (root / "linked.py").symlink_to(outside)
                inventory = scan_roots(
                    (ScanRoot("fixture", root),),
                    generated_at="2026-08-30T00:00:00Z",
                )
            finally:
                outside.unlink(missing_ok=True)

        self.assertEqual(inventory.findings, ())

    def test_scan_roots_rejects_unsupported_plugin_entrypoint_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "capabilities.json").write_text(
                json.dumps(
                    {
                        "capabilities": [
                            {"name": "demo.read", "entrypoint": "scripts/demo.rb"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "scripts").mkdir()
            (root / "scripts" / "demo.rb").write_text("puts 'demo'\n", encoding="utf-8")

            with self.assertRaisesRegex(ExternalIoScanError, "unsupported executable"):
                scan_roots((ScanRoot("plugin:demo", root),))

    def test_inventory_serialization_is_stable_and_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "network.py"
            source.write_text(
                "import urllib.request\nurllib.request.urlopen('https://example.invalid')\n",
                encoding="utf-8",
            )
            expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            inventory = scan_roots(
                (ScanRoot("fixture", root),),
                generated_at="2026-08-30T00:00:00Z",
            )

        payload = inventory_to_dict(inventory)
        self.assertEqual(INVENTORY_SCHEMA_VERSION, payload["schema_version"])
        self.assertEqual("2026-08-30T00:00:00Z", payload["generated_at"])
        self.assertEqual(1, len(payload["findings"]))
        self.assertEqual(expected_hash, payload["findings"][0]["file_sha256"])
        json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def test_inventory_schema_is_strict(self) -> None:
        schema = json.loads(
            (HARNESS_ROOT / "config/schemas/external_io_inventory.v1.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertFalse(schema["additionalProperties"])
        finding = schema["properties"]["findings"]["items"]
        self.assertFalse(finding["additionalProperties"])
        self.assertEqual(
            ["credential", "database", "network", "process"],
            sorted(finding["properties"]["category"]["enum"]),
        )


if __name__ == "__main__":
    unittest.main()
