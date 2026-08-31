from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VerifyEntrypointTests(unittest.TestCase):
    def test_verify_script_uses_packaged_runtime_with_project_venv_fallback(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        self.assertIn('PACKAGED_PYTHON="$ROOT_DIR/runtime/bin/python3"', script)
        self.assertIn('VENV_PYTHON="$ROOT_DIR/.venv/bin/python"', script)
        self.assertIn('export PYTHONDONTWRITEBYTECODE="1"', script)
        self.assertNotIn("python3 -m unittest", script)

    def test_verify_script_exposes_supported_gates(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        for command in ("unit", "offline", "manager-static", "architecture"):
            self.assertIn(f"  {command})", script)
        self.assertIn("unknown verification command", script)

    def test_test_gates_force_a_fresh_private_control_database_before_import(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        self.assertIn("prepare_isolated_test_runtime()", script)
        self.assertIn("mktemp -d /private/tmp/his-harness-verify.XXXXXX", script)
        self.assertIn(
            'export HARNESS_DB_PATH="$HARNESS_VERIFY_RUNTIME_DIR/harness.sqlite"',
            script,
        )
        self.assertIn(
            'export HIS_KNOWLEDGE_HOME="$HARNESS_VERIFY_RUNTIME_DIR/knowledge"',
            script,
        )
        for command in ("unit", "offline", "manager-static", "architecture"):
            branch = script.split(f"  {command})", 1)[1].split("    ;;", 1)[0]
            self.assertIn("prepare_isolated_test_runtime", branch)

    def test_architecture_gate_includes_change_context_boundaries(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        for module in (
            "tests.test_change_context_external_collectors",
            "tests.test_change_context_database_collector",
            "tests.test_change_context_prompt_boundary",
            "tests.test_change_context_worker_binding",
            "tests.test_pg_evidence_mcp_boundary",
        ):
            self.assertIn(module, script)

    def test_architecture_gate_includes_phase_1a_mcp_contracts(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        for module in (
            "tests.test_mcp_contracts",
            "tests.test_mcp_schema_validation",
            "tests.test_mcp_capability_registry",
            "tests.test_mcp_capability_check_cli",
            "tests.test_mcp_gateway",
            "tests.test_mcp_capability_runtime",
            "tests.test_mcp_phase_1a_acceptance",
        ):
            self.assertIn(module, script)

    def test_architecture_gate_includes_phase_1b_mcp_runtime(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        for module in (
            "tests.test_mcp_stdio_transport",
            "tests.test_mcp_persistence",
            "tests.test_mcp_runtime_factory",
            "tests.test_mcp_phase_1b_runtime_acceptance",
        ):
            self.assertIn(module, script)

    def test_architecture_gate_includes_phase_1c_provider_authority(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        for module in (
            "tests.test_provider_authority_policy",
            "tests.test_provider_action_authorization",
            "tests.test_provider_authority_acceptance",
        ):
            self.assertIn(module, script)

    def test_architecture_gate_includes_phase_1d_mcp_primary_route(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        for module in (
            "tests.test_mcp_phase_1d_primary_activation",
            "tests.test_mcp_primary_provider_adapter",
            "tests.test_mcp_connector_server_contracts",
        ):
            self.assertIn(module, script)

    def test_unknown_verification_mode_still_fails_closed(self) -> None:
        completed = subprocess.run(
            [str(ROOT / "scripts/verify.sh"), "not-a-real-mode"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(2, completed.returncode)
        self.assertIn("unknown verification command", completed.stderr)


    def test_offline_gate_uses_unique_output_by_default(self) -> None:
        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

        self.assertIn('if [ -n "${HARNESS_GATE_OUTPUT_DIR:-}" ]; then', script)
        self.assertIn('mktemp -d "${TMPDIR:-/private/tmp}/his-harness-enterprise-gate.XXXXXX"', script)
        self.assertNotIn('${HARNESS_GATE_OUTPUT_DIR:-/private/tmp/his-harness-enterprise-gate}', script)

if __name__ == "__main__":
    unittest.main()
