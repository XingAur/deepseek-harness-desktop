from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import database
from app.dynamic_plan_registry import DynamicPlanRegistry, write_dynamic_registry_outputs
from app.dynamic_planning import DynamicPlanningRequest, PlanningSignals, build_dynamic_plan
from tools.self_check import run_dynamic_plan_registry_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DynamicPlanRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "harness.sqlite"
        database.init_db()
        self.registry = DynamicPlanRegistry()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_schema_version_and_idempotent_plan_registration(self) -> None:
        plan = build_dynamic_plan(simple_request(), enabled=True)

        first = self.registry.register_plan(plan.to_dict())
        second = self.registry.register_plan(plan.to_dict())
        snapshot = self.registry.get_plan(first["plan_id"])

        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual("1.0-dynamic-plan-registry", database.get_schema_meta("dynamic_plan_registry"))
        self.assertEqual(len(plan.graph.nodes), len(snapshot["subtasks"]))
        self.assertEqual(len(plan.graph.edges), len(snapshot["edges"]))
        self.assertEqual(len(plan.handoffs), len(snapshot["contracts"]))
        self.assertEqual("requirement_analysis", snapshot["recovery_preview"]["ready_nodes"][0])

    def test_registration_rejects_non_readonly_or_inconsistent_plan(self) -> None:
        payload = build_dynamic_plan(simple_request(), enabled=True).to_dict()
        payload["external_actions_enabled"] = True

        with self.assertRaisesRegex(ValueError, "只读"):
            self.registry.register_plan(payload)

        payload = build_dynamic_plan(simple_request(), enabled=True).to_dict()
        payload["handoffs"][0]["producer"] = "frontend_developer"
        with self.assertRaisesRegex(ValueError, "producer"):
            self.registry.register_plan(payload)

    def test_registration_rejects_cyclic_graph(self) -> None:
        payload = build_dynamic_plan(simple_request(), enabled=True).to_dict()
        payload["graph"]["edges"].append(
            {
                "source": "verify",
                "target": "requirement_analysis",
                "dependency_type": "requires",
                "artifact_schema": "VerificationResult",
                "reason": "fixture_cycle",
            }
        )

        with self.assertRaisesRegex(ValueError, "环"):
            self.registry.register_plan(payload)

    def test_contract_update_validates_schema_producer_and_inputs(self) -> None:
        registration = self.registry.register_plan(build_dynamic_plan(medium_request(), enabled=True).to_dict())
        plan_id = registration["plan_id"]

        with self.assertRaisesRegex(ValueError, "schema"):
            self.registry.record_contract(
                plan_id=plan_id,
                node_id="requirement_analysis",
                schema_name="WrongContract",
                schema_version="1.0",
                producer="product_analyst",
                content={"scope": "x"},
                input_artifact_ids=(),
            )
        with self.assertRaisesRegex(ValueError, "producer"):
            self.registry.record_contract(
                plan_id=plan_id,
                node_id="requirement_analysis",
                schema_name="RequirementContract",
                schema_version="1.0",
                producer="frontend_developer",
                content={"scope": "x"},
                input_artifact_ids=(),
            )
        with self.assertRaisesRegex(ValueError, "input_artifact"):
            self.registry.record_contract(
                plan_id=plan_id,
                node_id="architecture",
                schema_name="ArchitectureContract",
                schema_version="1.0",
                producer="architect",
                content={"modules": []},
                input_artifact_ids=("unknown-artifact",),
            )

    def test_contract_rejects_credential_fields(self) -> None:
        registration = self.registry.register_plan(build_dynamic_plan(simple_request(), enabled=True).to_dict())

        with self.assertRaisesRegex(ValueError, "凭证字段"):
            self.registry.record_contract(
                plan_id=registration["plan_id"],
                node_id="requirement_analysis",
                schema_name="RequirementContract",
                schema_version="1.0",
                producer="product_analyst",
                content={"configuration": {"api_key": "must-not-be-stored"}},
                input_artifact_ids=(),
            )

    def test_upstream_change_marks_only_reachable_downstream_stale(self) -> None:
        registration = self.registry.register_plan(build_dynamic_plan(medium_request(), enabled=True).to_dict())
        plan_id = registration["plan_id"]
        requirement = self.record_node(plan_id, "requirement_analysis", "RequirementContract", "product_analyst", {"scope": 1})
        architecture = self.record_node(
            plan_id,
            "architecture",
            "ArchitectureContract",
            "architect",
            {"modules": ["web", "service"]},
            (requirement["artifact_id"],),
        )
        frontend = self.record_node(
            plan_id,
            "frontend_implementation",
            "ImplementationResult",
            "frontend_developer",
            {"files": ["web/src/query.vue"]},
            (architecture["artifact_id"],),
        )
        self.record_node(
            plan_id,
            "backend_implementation",
            "ImplementationResult",
            "backend_developer",
            {"files": ["service/src/Query.java"]},
            (architecture["artifact_id"],),
        )

        updated = self.record_node(
            plan_id,
            "frontend_implementation",
            "ImplementationResult",
            "frontend_developer",
            {"files": ["web/src/query.vue"], "revision": 2},
            (architecture["artifact_id"],),
        )
        snapshot = self.registry.get_plan(plan_id)
        contracts = snapshot["contracts_by_node"]

        self.assertEqual(2, updated["artifact_version"])
        self.assertEqual("current", contracts["frontend_implementation"]["status"])
        self.assertEqual("current", contracts["backend_implementation"]["status"])
        self.assertEqual("stale", contracts["code_review"]["status"])
        self.assertEqual("stale", contracts["verify"]["status"])
        self.assertNotEqual("stale", contracts["requirement_analysis"]["status"])
        self.assertEqual(frontend["artifact_id"], updated["supersedes_artifact_id"])

    def test_recovery_preview_advances_without_executing_nodes(self) -> None:
        registration = self.registry.register_plan(build_dynamic_plan(medium_request(), enabled=True).to_dict())
        plan_id = registration["plan_id"]
        before = self.registry.get_plan(plan_id)["recovery_preview"]
        requirement = self.record_node(
            plan_id,
            "requirement_analysis",
            "RequirementContract",
            "product_analyst",
            {"scope": "confirmed"},
        )
        after = self.registry.get_plan(plan_id)["recovery_preview"]

        self.assertEqual(["requirement_analysis"], before["ready_nodes"])
        self.assertIn("requirement_analysis", after["completed_nodes"])
        self.assertIn("architecture", after["ready_nodes"])
        self.assertNotIn("architecture", after["completed_nodes"])
        self.assertTrue(after["readonly"])
        self.assertFalse(after["execution_enabled"])
        self.assertTrue(requirement["content_hash"].startswith("sha256:"))

    def test_registry_outputs_are_readonly(self) -> None:
        registration = self.registry.register_plan(build_dynamic_plan(simple_request(), enabled=True).to_dict())
        snapshot = self.registry.get_plan(registration["plan_id"])
        output_dir = Path(self.temp_dir.name) / "outputs"

        files = write_dynamic_registry_outputs(output_dir, snapshot)

        self.assertEqual(3, len(files))
        self.assertTrue(all(path.exists() for path in files))
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertIn("只读恢复预览", rendered)
        self.assertNotIn("自动执行成功", rendered)

    def test_registry_self_check_is_repeatable_with_retained_database(self) -> None:
        output_dir = Path(self.temp_dir.name) / "self_check"

        first = run_dynamic_plan_registry_checks(output_dir=output_dir)
        second = run_dynamic_plan_registry_checks(output_dir=output_dir)

        self.assertTrue(all(item["status"] == "pass" for item in first))
        self.assertTrue(all(item["status"] == "pass" for item in second))

    def record_node(
        self,
        plan_id: int,
        node_id: str,
        schema_name: str,
        producer: str,
        content: dict,
        input_artifact_ids: tuple[str, ...] = (),
    ) -> dict:
        return self.registry.record_contract(
            plan_id=plan_id,
            node_id=node_id,
            schema_name=schema_name,
            schema_version="1.0",
            producer=producer,
            content=content,
            input_artifact_ids=input_artifact_ids,
        )


class DynamicPlanRegistryCliTests(unittest.TestCase):
    def test_task_manager_cli_registers_plan_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan_path = root / "dynamic_plan.json"
            output_dir = root / "registry_output"
            db_path = root / "harness.sqlite"
            plan_path.write_text(
                json.dumps(build_dynamic_plan(simple_request(), enabled=True).to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["HARNESS_DB_PATH"] = str(db_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/task_manager.py",
                    "register-dynamic-plan",
                    "--plan-file",
                    str(plan_path),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["registration"]["idempotent"])
            self.assertTrue((output_dir / "dynamic_plan_registry.json").exists())
            self.assertTrue(db_path.exists())

    def test_task_manager_cli_records_contract_and_exports_recovery_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan_path = root / "dynamic_plan.json"
            contract_path = root / "requirement_contract.json"
            output_dir = root / "registry_output"
            db_path = root / "harness.sqlite"
            plan_path.write_text(
                json.dumps(build_dynamic_plan(simple_request(), enabled=True).to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            contract_path.write_text(
                json.dumps(
                    {
                        "schema_name": "RequirementContract",
                        "schema_version": "1.0",
                        "producer": "product_analyst",
                        "input_artifact_ids": [],
                        "content": {"scope": "fixture-only"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["HARNESS_DB_PATH"] = str(db_path)

            registration = run_task_manager(
                [
                    "register-dynamic-plan",
                    "--plan-file",
                    str(plan_path),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )
            plan_id = json.loads(registration.stdout)["registration"]["plan_id"]
            recorded = run_task_manager(
                [
                    "record-dynamic-contract",
                    "--plan-id",
                    str(plan_id),
                    "--node-id",
                    "requirement_analysis",
                    "--contract-file",
                    str(contract_path),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )
            shown = run_task_manager(
                [
                    "show-dynamic-plan",
                    "--plan-id",
                    str(plan_id),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )

            recorded_payload = json.loads(recorded.stdout)
            shown_payload = json.loads(shown.stdout)
            self.assertEqual("current", recorded_payload["artifact"]["status"])
            self.assertIn(
                "requirement_analysis",
                shown_payload["snapshot"]["recovery_preview"]["completed_nodes"],
            )
            self.assertIn(
                "frontend_implementation",
                shown_payload["snapshot"]["recovery_preview"]["ready_nodes"],
            )


def run_task_manager(arguments: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "tools/task_manager.py", *arguments],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def simple_request() -> DynamicPlanningRequest:
    return DynamicPlanningRequest(
        requirement_id="DFHIS-REGISTRY-SIMPLE",
        title="挂号页面默认值调整",
        demand_text="前端单页面默认值与档案管理保持一致。",
        signals=PlanningSignals(
            affected_layers=("frontend",),
            estimated_file_count=2,
            evidence_status="complete",
            allowed_paths={"frontend": ("src/views/register.vue",)},
        ),
    )


def medium_request() -> DynamicPlanningRequest:
    return DynamicPlanningRequest(
        requirement_id="DFHIS-REGISTRY-MEDIUM",
        title="前后端查询条件联动",
        demand_text="前端请求与后端接口同步新增筛选参数，保持历史默认行为。",
        signals=PlanningSignals(
            affected_layers=("frontend", "backend"),
            estimated_file_count=6,
            dependency_mode="serial",
            evidence_status="partial",
            verification_mode="integration",
            allowed_paths={
                "frontend": ("web/src/query.vue",),
                "backend": ("service/src/Query.java",),
            },
        ),
    )


if __name__ == "__main__":
    unittest.main()
