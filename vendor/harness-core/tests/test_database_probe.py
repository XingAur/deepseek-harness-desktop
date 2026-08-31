from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DatabaseProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.package = Path(self._temp.name)
        (self.package / "analysis").mkdir(parents=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_skips_without_dsn(self) -> None:
        from app.database_probe import probe_readonly_database

        with patch.dict(os.environ, {"DSH_DATABASE_DSN": ""}, clear=False):
            os.environ.pop("DSH_DATABASE_DSN", None)
            self.assertIsNone(probe_readonly_database(package_dir=self.package))

    def test_records_connection_failure_as_bounded_evidence(self) -> None:
        from app.database_probe import probe_readonly_database

        with patch.dict(os.environ, {"DSH_DATABASE_DSN": "postgresql://nobody:nopass@127.0.0.1:1/none"}):
            result = probe_readonly_database(package_dir=self.package)

        self.assertEqual(result["status"], "failed")
        self.assertNotEqual(result["error"], "")
        evidence = json.loads((self.package / "engineering" / "database_probe.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", evidence["status"])
        self.assertEqual("readonly", evidence["mode"])

    def test_error_is_redacted_before_crossing_the_protocol(self) -> None:
        from app.database_probe import probe_readonly_database
        from app.sensitive_text import contains_sensitive_text

        with patch.dict(os.environ, {"DSH_DATABASE_DSN": "postgresql://nobody:nopass@127.0.0.1:1/none"}):
            result = probe_readonly_database(package_dir=self.package)

        self.assertFalse(contains_sensitive_text(str(result["error"])))


if __name__ == "__main__":
    unittest.main()
