from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.external_io_inventory import ExternalIoFinding, ExternalIoInventory
from app.external_io_policy import BoundaryRule, ExternalIoPolicy, evaluate_inventory


ROOT = Path(__file__).resolve().parents[1]


def _finding(*, file_sha256: str = "a" * 64, relative_path: str = "worker.py") -> ExternalIoFinding:
    return ExternalIoFinding(
        root_id="fixture",
        relative_path=relative_path,
        line=4,
        category="network",
        symbol="urllib.request.urlopen",
        occurrence=1,
        file_sha256=file_sha256,
        fingerprint="f" * 64,
    )


def _inventory(*, file_sha256: str = "a" * 64, relative_path: str = "worker.py") -> ExternalIoInventory:
    finding = _finding(file_sha256=file_sha256, relative_path=relative_path)
    return ExternalIoInventory(
        schema_version="his-external-io-inventory.v1",
        generated_at="2026-08-30T00:00:00Z",
        roots=({"root_id": "fixture", "path": "/fixture"},),
        findings=(finding,),
    )


def _policy(
    *,
    file_sha256: str | None = None,
    disposition: str = "compatibility_quarantine",
    relative_path: str = "worker.py",
) -> ExternalIoPolicy:
    rules = ()
    if file_sha256 is not None:
        rules = (
            BoundaryRule(
                root_id="fixture",
                relative_path=relative_path,
                file_sha256=file_sha256,
                findings=(("network", "urllib.request.urlopen", 1),),
                disposition=disposition,
                owner="harness",
                rationale="Reviewed fixture boundary.",
            ),
        )
    return ExternalIoPolicy(
        schema_version="his-external-io-boundaries.v1",
        roots=(),
        rules=rules,
    )


class ExternalIoPolicyTests(unittest.TestCase):
    def test_unclassified_finding_fails_closed(self) -> None:
        report = evaluate_inventory(_inventory(), _policy())

        self.assertEqual("failed", report.status)
        self.assertEqual(1, report.unclassified_count)

    def test_source_hash_drift_requires_review(self) -> None:
        report = evaluate_inventory(
            _inventory(file_sha256="b" * 64),
            _policy(file_sha256="a" * 64),
        )

        self.assertEqual("failed", report.status)
        self.assertEqual(1, report.source_drift_count)

    def test_known_compatibility_quarantine_is_visible_but_gate_can_pass(self) -> None:
        report = evaluate_inventory(
            _inventory(),
            _policy(file_sha256="a" * 64),
        )

        self.assertEqual("passed", report.status)
        self.assertEqual(1, report.compatibility_debt_count)

    def test_forbidden_finding_fails_even_when_explicitly_classified(self) -> None:
        report = evaluate_inventory(
            _inventory(),
            _policy(file_sha256="a" * 64, disposition="forbidden"),
        )

        self.assertEqual("failed", report.status)
        self.assertEqual(1, report.forbidden_count)

    def test_skill_executable_connection_code_fails_documentation_only_contract(self) -> None:
        report = evaluate_inventory(
            _inventory(relative_path="skills/example/SKILL.md"),
            _policy(file_sha256="a" * 64, relative_path="skills/example/SKILL.md"),
        )

        self.assertEqual("failed", report.status)
        self.assertEqual(1, report.skill_contract_error_count)

    def test_current_matrix_reports_native_mcp_and_remaining_compatibility_truth(self) -> None:
        report = evaluate_inventory(
            ExternalIoInventory(
                schema_version="his-external-io-inventory.v1",
                generated_at="2026-08-30T00:00:00Z",
                roots=(),
                findings=(),
            ),
            _policy(),
            matrix_path=ROOT / "config/role_capability_skill_matrix.json",
        )
        routes = {
            (item["capability"], item["provider"]): item
            for item in report.details
            if item.get("kind") == "matrix_route"
        }

        for capability, provider in (
            ("workitem.read", "yunxiao"),
            ("gitlab.read", "gitlab"),
            ("database.inspect", "postgresql"),
        ):
            self.assertEqual(
                "mcp_required",
                routes[(capability, provider)]["disposition"],
            )
        for capability in (
            "git.inspect",
            "git.diff",
            "source.read",
            "source.search",
            "verification.run-local",
        ):
            self.assertEqual(
                "worker_allowed",
                routes[(capability, "his-engineering")]["disposition"],
            )
        for capability in ("knowledge.retrieve", "knowledge.answer"):
            detail = routes[(capability, "his-knowledge")]
            self.assertEqual("mcp_skill", detail["skill_kind"])
            self.assertEqual("compatibility_quarantine", detail["disposition"])

    def test_mcp_skill_without_server_fails_closed_in_matrix_audit(self) -> None:
        payload = json.loads(
            (ROOT / "config/role_capability_skill_matrix.json").read_text(encoding="utf-8")
        )
        skill = next(item for item in payload["skills"] if item["kind"] == "mcp_skill")
        skill.pop("mcp_server")
        for route in payload["capability_routes"]:
            if route["skill"] == skill["name"]:
                route.pop("mcp_server", None)
        with tempfile.TemporaryDirectory() as temp_dir:
            matrix_path = Path(temp_dir) / "matrix.json"
            matrix_path.write_text(json.dumps(payload), encoding="utf-8")
            report = evaluate_inventory(
                ExternalIoInventory(
                    schema_version="his-external-io-inventory.v1",
                    generated_at="2026-08-30T00:00:00Z",
                    roots=(),
                    findings=(),
                ),
                _policy(),
                matrix_path=matrix_path,
            )

        self.assertEqual("failed", report.status)
        self.assertGreaterEqual(report.skill_contract_error_count, 1)


if __name__ == "__main__":
    unittest.main()
