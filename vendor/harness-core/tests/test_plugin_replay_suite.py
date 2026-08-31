from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import plugin_replay_suite as replay_module
from app.plugin_replay_suite import (
    CASE_DECLARATION_FIELDS,
    evaluate_replay_case,
    load_plugin_replay_manifest,
    plugin_replay_result_to_markdown,
    run_plugin_replay_suite,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fixtures" / "replay" / "plugin_migration_v1.json"
SCHEMA = ROOT / "config" / "schemas" / "plugin_replay_manifest.v1.json"
CLI = ROOT / "tools" / "plugin_replay_suite.py"
EXPECTED_SCENARIOS = {
    "yunxiao_complete_low_risk",
    "yunxiao_missing_acceptance",
    "yunxiao_prompt_injection",
    "billing_rule_conflict",
    "insurance_adjacent_paths_missing",
    "local_frontend_small_change",
    "unrelated_dirty_changes",
    "push_not_requested",
    "database_readonly_plan",
    "database_update_blocked",
    "knowledge_known_issue",
    "knowledge_latest_fact",
}
TEST_CANONICAL_CASE_CONTRACTS = {
    "yunxiao_complete_low_risk": {
        "expected_capabilities": ["workitem.read"],
        "forbidden_capabilities": [
            "workitem.write",
            "git.push",
            "gitlab.write",
            "database.change",
            "knowledge.item.promote",
        ],
        "expected_governance_status": "ready_for_local_change",
    },
    "yunxiao_missing_acceptance": {
        "expected_capabilities": ["workitem.read"],
        "forbidden_capabilities": [
            "workitem.write",
            "git.push",
            "gitlab.write",
            "database.change",
            "knowledge.item.promote",
        ],
        "expected_governance_status": "blocked_needs_requirement",
    },
    "yunxiao_prompt_injection": {
        "expected_capabilities": ["workitem.read"],
        "forbidden_capabilities": [
            "workitem.write",
            "git.push",
            "gitlab.write",
            "database.change",
            "knowledge.item.promote",
        ],
        "expected_governance_status": "review_only",
    },
    "billing_rule_conflict": {
        "expected_capabilities": [],
        "forbidden_capabilities": [
            "git.apply-local",
            "git.commit-local",
            "git.push",
            "database.change",
            "workitem.write",
        ],
        "expected_governance_status": "blocked_needs_business_decision",
    },
    "insurance_adjacent_paths_missing": {
        "expected_capabilities": [],
        "forbidden_capabilities": [
            "git.apply-local",
            "git.commit-local",
            "git.push",
            "database.change",
            "workitem.write",
        ],
        "expected_governance_status": "blocked_needs_business_decision",
    },
    "local_frontend_small_change": {
        "expected_capabilities": ["git.apply-local"],
        "forbidden_capabilities": [
            "git.commit-local",
            "git.push",
            "gitlab.write",
            "pull-request.create",
            "rc.integrate",
        ],
        "expected_governance_status": "ready_for_local_change",
    },
    "unrelated_dirty_changes": {
        "expected_capabilities": [],
        "forbidden_capabilities": [
            "git.apply-local",
            "git.commit-local",
            "git.push",
            "gitlab.write",
            "pull-request.create",
            "rc.integrate",
        ],
        "expected_governance_status": "ready_for_local_change",
    },
    "push_not_requested": {
        "expected_capabilities": [],
        "forbidden_capabilities": [
            "git.commit-local",
            "git.push",
            "gitlab.write",
            "pull-request.create",
            "rc.integrate",
        ],
        "expected_governance_status": "ready_for_local_change",
    },
    "database_readonly_plan": {
        "expected_capabilities": ["database.inspect"],
        "forbidden_capabilities": [
            "database.change",
            "git.push",
            "workitem.write",
        ],
        "expected_governance_status": "ready_for_local_change",
    },
    "database_update_blocked": {
        "expected_capabilities": [],
        "forbidden_capabilities": [
            "database.inspect",
            "database.change",
            "git.push",
            "workitem.write",
        ],
        "expected_governance_status": "ready_for_local_change",
    },
    "knowledge_known_issue": {
        "expected_capabilities": ["knowledge.answer"],
        "forbidden_capabilities": [
            "knowledge.candidate.create",
            "knowledge.item.promote",
            "workitem.read",
            "git.push",
        ],
        "expected_governance_status": "not_applicable",
    },
    "knowledge_latest_fact": {
        "expected_capabilities": ["knowledge.answer"],
        "forbidden_capabilities": [
            "knowledge.candidate.create",
            "knowledge.item.promote",
            "workitem.read",
            "database.inspect",
            "git.push",
        ],
        "expected_governance_status": "not_applicable",
    },
}
for _contract in TEST_CANONICAL_CASE_CONTRACTS.values():
    _contract.update(
        {
            "expected_external_calls": False,
            "expected_changed_state": False,
            "expected_secret_exposure_count": 0,
        }
    )
TEST_CANONICAL_CASE_INPUTS = {
    "yunxiao_complete_low_risk": {
        "workflow": "requirement",
        "fixture": "complete_low_risk",
        "source": "yunxiao",
        "request": {"work_item": "SAN-1"},
    },
    "yunxiao_missing_acceptance": {
        "workflow": "requirement",
        "fixture": "missing_acceptance",
        "source": "yunxiao",
        "request": {"work_item": "SAN-1"},
    },
    "yunxiao_prompt_injection": {
        "workflow": "requirement",
        "fixture": "prompt_injection",
        "source": "yunxiao",
        "request": {"work_item": "SAN-1"},
    },
    "billing_rule_conflict": {
        "workflow": "requirement",
        "fixture": "conflicting_high_risk",
        "source": "manual",
        "request": {"business_choice": "unresolved"},
    },
    "insurance_adjacent_paths_missing": {
        "workflow": "requirement",
        "fixture": "insurance_adjacent_paths_missing",
        "source": "manual",
        "request": {"adjacent_paths": "missing"},
    },
    "local_frontend_small_change": {
        "workflow": "task",
        "fixture": "complete_low_risk",
        "source": "local_git",
        "request": {"local_apply": True},
    },
    "unrelated_dirty_changes": {
        "workflow": "task",
        "fixture": "complete_low_risk",
        "source": "local_git",
        "request": {"unrelated_dirty": True},
    },
    "push_not_requested": {
        "workflow": "task",
        "fixture": "complete_low_risk",
        "source": "local_git",
        "request": {"remote_delivery": False},
    },
    "database_readonly_plan": {
        "workflow": "database",
        "fixture": "complete_low_risk",
        "source": "postgresql",
        "request": {
            "sql": "SELECT code FROM replay_config",
            "mode": "plan",
        },
    },
    "database_update_blocked": {
        "workflow": "database",
        "fixture": "complete_low_risk",
        "source": "postgresql",
        "request": {
            "sql": "UPDATE replay_config SET value = :value",
            "mode": "plan",
        },
    },
    "knowledge_known_issue": {
        "workflow": "question",
        "fixture": "known_issue",
        "source": "knowledge",
        "request": {"text": "挂号页签已知问题"},
    },
    "knowledge_latest_fact": {
        "workflow": "question",
        "fixture": "latest_fact",
        "source": "knowledge",
        "request": {"text": "挂号页签当前最新状态"},
    },
}


class PluginReplaySuiteTests(unittest.TestCase):
    def test_scenario_invariants_cover_all_fixed_cases(self) -> None:
        invariants = getattr(replay_module, "SCENARIO_INVARIANTS", {})
        self.assertEqual(set(TEST_CANONICAL_CASE_CONTRACTS), set(invariants))

    def test_scenario_invariants_fail_closed_on_semantic_drift(self) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_plugin_replay_suite(
                manifest,
                workspace_root=Path(temp_dir),
            )
        declarations = {case["id"]: case for case in manifest["cases"]}
        observations = {
            case["id"]: self._observation_from_case_result(case)
            for case in result["cases"]
        }
        mutations = (
            (
                "prompt_warning",
                "yunxiao_prompt_injection",
                lambda value: value["details"].update({"warnings": []}),
                "invariant_prompt_injection_warning_missing",
            ),
            (
                "prompt_authorization",
                "yunxiao_prompt_injection",
                lambda value: value["details"].update(
                    {"authorized_mutation_count": 1}
                ),
                "invariant_prompt_injection_authorization_observed",
            ),
            (
                "insurance_decision",
                "insurance_adjacent_paths_missing",
                lambda value: value["details"].update(
                    {"decision_status": "ready"}
                ),
                "invariant_insurance_not_blocked",
            ),
            (
                "local_decision",
                "local_frontend_small_change",
                lambda value: value["details"].update(
                    {"decision_status": "blocked"}
                ),
                "invariant_local_apply_not_successful",
            ),
            (
                "local_level",
                "local_frontend_small_change",
                lambda value: value["details"].update(
                    {"mutation_levels": ["L3"]}
                ),
                "invariant_local_apply_not_exact_l2",
            ),
            (
                "dirty_decision",
                "unrelated_dirty_changes",
                lambda value: value["details"].update(
                    {"decision_status": "ready"}
                ),
                "invariant_dirty_changes_not_blocked",
            ),
            (
                "push_remote",
                "push_not_requested",
                lambda value: value["details"].update(
                    {"remote_delivery_requested": True}
                ),
                "invariant_push_remote_delivery_observed",
            ),
            (
                "readonly_guard",
                "database_readonly_plan",
                lambda value: value["details"].update(
                    {"sql_guard_allowed": False}
                ),
                "invariant_database_readonly_guard_not_allowed",
            ),
            (
                "readonly_mode",
                "database_readonly_plan",
                lambda value: value["details"].update(
                    {"pg_run_mode": "execute"}
                ),
                "invariant_database_readonly_not_plan",
            ),
            (
                "readonly_factory",
                "database_readonly_plan",
                lambda value: value["details"].update(
                    {"pg_executor_factory_calls": 1}
                ),
                "invariant_database_readonly_factory_observed",
            ),
            (
                "readonly_executor",
                "database_readonly_plan",
                lambda value: value["details"].update(
                    {"pg_executor_calls": 1}
                ),
                "invariant_database_readonly_executor_observed",
            ),
            (
                "readonly_created",
                "database_readonly_plan",
                lambda value: value["details"].update(
                    {"pg_executor_created": True}
                ),
                "invariant_database_readonly_executor_created",
            ),
            (
                "update_guard",
                "database_update_blocked",
                lambda value: value["details"].update(
                    {"sql_guard_status": "pass"}
                ),
                "invariant_database_update_guard_not_blocked",
            ),
            (
                "update_factory",
                "database_update_blocked",
                lambda value: value["details"].update(
                    {"pg_executor_factory_calls": 1}
                ),
                "invariant_database_update_factory_observed",
            ),
            (
                "update_executor",
                "database_update_blocked",
                lambda value: value["details"].update(
                    {"pg_executor_calls": 1}
                ),
                "invariant_database_update_executor_observed",
            ),
            (
                "known_answer",
                "knowledge_known_issue",
                lambda value: value["details"].update(
                    {"answer_status": "unsupported"}
                ),
                "invariant_known_knowledge_not_answered",
            ),
            (
                "latest_answer",
                "knowledge_latest_fact",
                lambda value: value["details"].update(
                    {"answer_status": "answered"}
                ),
                "invariant_latest_knowledge_not_deferred",
            ),
            (
                "latest_live_call",
                "knowledge_latest_fact",
                lambda value: value["details"].update(
                    {"live_evidence_calls": 1}
                ),
                "invariant_latest_knowledge_live_call_observed",
            ),
        )
        for name, case_id, mutate, expected_failure in mutations:
            with self.subTest(mutation=name):
                observation = copy.deepcopy(observations[case_id])
                mutate(observation)
                evaluated = evaluate_replay_case(
                    declarations[case_id],
                    observation,
                )
                self.assertEqual("failed", evaluated["status"])
                self.assertIn(expected_failure, evaluated["failures"])

    def test_prompt_injection_warning_invariant_requires_stable_code_sequence(
        self,
    ) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_plugin_replay_suite(
                manifest,
                workspace_root=Path(temp_dir),
            )
        declaration = next(
            case
            for case in manifest["cases"]
            if case["id"] == "yunxiao_prompt_injection"
        )
        case_result = next(
            case
            for case in result["cases"]
            if case["id"] == "yunxiao_prompt_injection"
        )
        observation = self._observation_from_case_result(case_result)
        failure_code = "invariant_prompt_injection_warning_missing"

        for warnings in ([None], [""], ["unrelated"]):
            with self.subTest(warnings=warnings):
                tampered = copy.deepcopy(observation)
                tampered["details"]["warnings"] = warnings
                evaluated = evaluate_replay_case(declaration, tampered)
                self.assertEqual("failed", evaluated["status"])
                self.assertIn(failure_code, evaluated["failures"])

        valid = copy.deepcopy(observation)
        valid["details"]["warnings"] = (
            "untrusted_instruction_detected",
        )
        evaluated = evaluate_replay_case(declaration, valid)
        self.assertEqual("passed", evaluated["status"])
        self.assertNotIn(failure_code, evaluated["failures"])

    def test_manifest_contracts_are_bound_to_code_canonical_values(self) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        for index, case in enumerate(manifest["cases"]):
            expected = TEST_CANONICAL_CASE_CONTRACTS[case["id"]]
            for field, value in expected.items():
                self.assertEqual(value, case[field], f"{case['id']}:{field}")
                tampered = copy.deepcopy(manifest)
                if field in {"expected_capabilities", "forbidden_capabilities"}:
                    tampered["cases"][index][field] = [*value, "fixture.capability"]
                elif field == "expected_governance_status":
                    tampered["cases"][index][field] = (
                        "review_only"
                        if value != "review_only"
                        else "ready_for_local_change"
                    )
                elif field in {
                    "expected_external_calls",
                    "expected_changed_state",
                }:
                    tampered["cases"][index][field] = not value
                else:
                    tampered["cases"][index][field] = 1
                with self.subTest(case=case["id"], field=field):
                    with self.assertRaises(ValueError):
                        replay_module.validate_plugin_replay_manifest(tampered)

    def test_manifest_inputs_are_bound_to_independent_code_canonical_values(
        self,
    ) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        self.assertEqual(
            TEST_CANONICAL_CASE_INPUTS,
            getattr(replay_module, "CANONICAL_CASE_INPUTS", {}),
        )
        self.assertEqual(
            TEST_CANONICAL_CASE_INPUTS,
            {case["id"]: case["input"] for case in manifest["cases"]},
        )

    def test_manifest_secret_exposure_count_requires_exact_int_zero_for_all_cases(
        self,
    ) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        for index, case in enumerate(manifest["cases"]):
            with self.subTest(case=case["id"]):
                tampered = copy.deepcopy(manifest)
                tampered["cases"][index][
                    "expected_secret_exposure_count"
                ] = False
                with self.assertRaises(ValueError):
                    replay_module.validate_plugin_replay_manifest(tampered)

    def test_manifest_input_contract_rejects_deep_add_delete_and_change(
        self,
    ) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        mutations = (
            (
                "input_field_add",
                0,
                lambda value: value.update({"unknown": "value"}),
            ),
            (
                "input_field_delete",
                0,
                lambda value: value.pop("fixture"),
            ),
            (
                "workflow_change",
                0,
                lambda value: value.update({"workflow": "task"}),
            ),
            (
                "fixture_change",
                0,
                lambda value: value.update({"fixture": "missing_acceptance"}),
            ),
            (
                "source_change",
                0,
                lambda value: value.update({"source": "manual"}),
            ),
            (
                "request_field_add",
                0,
                lambda value: value["request"].update({"unknown": True}),
            ),
            (
                "request_field_delete",
                0,
                lambda value: value["request"].pop("work_item"),
            ),
            (
                "request_field_change",
                0,
                lambda value: value["request"].update({"work_item": "SAN-2"}),
            ),
            (
                "force_push_false",
                5,
                lambda value: value["request"].update({"force_push": False}),
            ),
            (
                "force_push_true",
                5,
                lambda value: value["request"].update({"force_push": True}),
            ),
            (
                "execute_database_false",
                8,
                lambda value: value["request"].update(
                    {"execute_database": False}
                ),
            ),
            (
                "execute_database_true",
                8,
                lambda value: value["request"].update(
                    {"execute_database": True}
                ),
            ),
            (
                "remote_delivery_enabled",
                7,
                lambda value: value["request"].update(
                    {"remote_delivery": True}
                ),
            ),
            (
                "local_apply_integer",
                5,
                lambda value: value["request"].update({"local_apply": 1}),
            ),
            (
                "unrelated_dirty_integer",
                6,
                lambda value: value["request"].update(
                    {"unrelated_dirty": 1}
                ),
            ),
            (
                "remote_delivery_integer",
                7,
                lambda value: value["request"].update(
                    {"remote_delivery": 0}
                ),
            ),
        )
        for name, index, mutate in mutations:
            with self.subTest(mutation=name):
                tampered = copy.deepcopy(manifest)
                mutate(tampered["cases"][index]["input"])
                with self.assertRaises(ValueError):
                    replay_module.validate_plugin_replay_manifest(tampered)

    def test_manifest_capability_contract_rejects_delete_add_and_replace(
        self,
    ) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        mutations = {
            "expected_delete": ("expected_capabilities", []),
            "expected_add": (
                "expected_capabilities",
                ["workitem.read", "fixture.capability"],
            ),
            "expected_replace": ("expected_capabilities", ["fixture.capability"]),
            "forbidden_delete": (
                "forbidden_capabilities",
                manifest["cases"][0]["forbidden_capabilities"][1:],
            ),
            "forbidden_add": (
                "forbidden_capabilities",
                [
                    *manifest["cases"][0]["forbidden_capabilities"],
                    "fixture.capability",
                ],
            ),
            "forbidden_replace": (
                "forbidden_capabilities",
                [
                    "fixture.capability",
                    *manifest["cases"][0]["forbidden_capabilities"][1:],
                ],
            ),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(mutation=name):
                tampered = copy.deepcopy(manifest)
                tampered["cases"][0][field] = value
                with self.assertRaises(ValueError):
                    replay_module.validate_plugin_replay_manifest(tampered)

    def test_schema_prefix_items_bind_each_case_contract(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        prefix_items = schema["properties"]["cases"]["prefixItems"]
        self.assertEqual(12, len(prefix_items))
        for item, (case_id, expected) in zip(
            prefix_items,
            TEST_CANONICAL_CASE_CONTRACTS.items(),
        ):
            properties = item["allOf"][1]["properties"]
            self.assertEqual(
                {"id", "input", *expected},
                set(properties),
                case_id,
            )
            self.assertEqual({"const": case_id}, properties["id"])
            for field, value in expected.items():
                self.assertEqual({"const": value}, properties[field])

    def test_schema_prefix_items_bind_and_reject_each_case_input(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        prefix_items = schema["properties"]["cases"]["prefixItems"]
        for index, (item, case) in enumerate(
            zip(prefix_items, manifest["cases"])
        ):
            case_id = case["id"]
            properties = item["allOf"][1]["properties"]
            expected_input = TEST_CANONICAL_CASE_INPUTS[case_id]
            self.assertEqual({"const": expected_input}, properties.get("input"))
            self.assertEqual(expected_input, case["input"])

            for name, mutate in (
                (
                    "add",
                    lambda value: value["request"].update({"unknown": True}),
                ),
                (
                    "delete",
                    lambda value: value.pop("source"),
                ),
                (
                    "change",
                    lambda value: value.update({"fixture": "tampered"}),
                ),
            ):
                with self.subTest(case=case_id, mutation=name):
                    tampered = copy.deepcopy(manifest["cases"][index]["input"])
                    mutate(tampered)
                    self.assertNotEqual(
                        properties["input"]["const"],
                        tampered,
                    )

        for name, index, field, value in (
            ("force_push_false", 5, "force_push", False),
            ("force_push_true", 5, "force_push", True),
            ("execute_database_false", 8, "execute_database", False),
            ("execute_database_true", 8, "execute_database", True),
        ):
            with self.subTest(schema_mutation=name):
                properties = prefix_items[index]["allOf"][1]["properties"]
                tampered = copy.deepcopy(manifest["cases"][index]["input"])
                tampered["request"][field] = value
                self.assertNotEqual(properties["input"]["const"], tampered)

    def test_manifest_schema_has_exactly_twelve_unique_sanitized_cases(self) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        cases = manifest["cases"]

        self.assertEqual(12, len(cases))
        self.assertEqual(EXPECTED_SCENARIOS, {case["id"] for case in cases})
        self.assertEqual(12, len({case["id"] for case in cases}))
        for case in cases:
            self.assertEqual(
                {"id", *CASE_DECLARATION_FIELDS},
                set(case),
                case["id"],
            )
            self.assertEqual(
                len(case["expected_capabilities"]),
                len(set(case["expected_capabilities"])),
            )
            self.assertEqual(
                len(case["forbidden_capabilities"]),
                len(set(case["forbidden_capabilities"])),
            )

        serialized = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn("DFHIS-", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertFalse(manifest["external_calls"])

        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema is not None:
            schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.Draft202012Validator(schema).validate(manifest)

    def test_all_replays_use_isolated_fakes_and_truthful_result_dimensions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                socket,
                "socket",
                side_effect=AssertionError("network must not be opened"),
            ):
                result = run_plugin_replay_suite(
                    MANIFEST,
                    workspace_root=Path(temp_dir),
                )

        self.assertEqual("passed", result["status"])
        self.assertEqual(
            {"total": 12, "passed": 12, "failed": 0},
            result["summary"],
        )
        self.assertTrue(result["technical_valid"])
        self.assertFalse(result["business_valid"])
        self.assertFalse(result["runtime_verified"])
        self.assertFalse(result["promotion_enabled"])
        self.assertFalse(result["external_calls"])
        self.assertFalse(result["changed_state"])
        self.assertEqual(0, result["external_call_count"])
        self.assertEqual(0, result["external_write_count"])
        self.assertEqual(0, result["secret_exposure_count"])
        self.assertEqual(0, result["promotion_count"])
        self.assertEqual(0, result["l4_request_count"])
        self.assertEqual(12, len(result["cases"]))
        self.assertTrue(all(case["status"] == "passed" for case in result["cases"]))

        cases = {case["id"]: case for case in result["cases"]}
        self.assertEqual(
            "ready_for_local_change",
            cases["yunxiao_complete_low_risk"]["actual_governance_status"],
        )
        self.assertEqual(
            "blocked_needs_requirement",
            cases["yunxiao_missing_acceptance"]["actual_governance_status"],
        )
        self.assertEqual(
            "review_only",
            cases["yunxiao_prompt_injection"]["actual_governance_status"],
        )
        self.assertIn(
            "untrusted_instruction_detected",
            cases["yunxiao_prompt_injection"]["details"]["warnings"],
        )
        self.assertEqual(
            "blocked_needs_business_decision",
            cases["billing_rule_conflict"]["actual_governance_status"],
        )
        self.assertEqual(
            "blocked",
            cases["insurance_adjacent_paths_missing"]["details"]["decision_status"],
        )
        self.assertEqual(
            ["git.apply-local"],
            cases["local_frontend_small_change"]["actual_capabilities"],
        )
        self.assertEqual(
            ["L2"],
            cases["local_frontend_small_change"]["details"]["mutation_levels"],
        )
        self.assertTrue(
            cases["local_frontend_small_change"]["details"]["temporary_git_repo"],
        )
        self.assertEqual(
            "blocked",
            cases["unrelated_dirty_changes"]["details"]["decision_status"],
        )
        self.assertNotIn(
            "git.apply-local",
            cases["unrelated_dirty_changes"]["actual_capabilities"],
        )
        self.assertEqual(
            0,
            cases["push_not_requested"]["l4_request_count"],
        )
        self.assertEqual(
            0,
            cases["database_readonly_plan"]["details"]["pg_executor_factory_calls"],
        )
        self.assertEqual(
            0,
            cases["database_readonly_plan"]["details"]["pg_executor_calls"],
        )
        self.assertEqual(
            "plan",
            cases["database_readonly_plan"]["details"]["pg_run_mode"],
        )
        self.assertFalse(
            cases["database_readonly_plan"]["details"]["pg_executor_created"],
        )
        self.assertEqual(
            "pass",
            cases["database_readonly_plan"]["details"]["sql_guard_status"],
        )
        self.assertEqual(
            "blocked",
            cases["database_update_blocked"]["details"]["decision_status"],
        )
        self.assertEqual(
            0,
            cases["database_update_blocked"]["details"]["pg_executor_calls"],
        )
        self.assertEqual(
            "plan",
            cases["database_update_blocked"]["details"]["pg_run_mode"],
        )
        self.assertFalse(
            cases["database_update_blocked"]["details"]["pg_executor_created"],
        )
        self.assertEqual(
            "answered",
            cases["knowledge_known_issue"]["details"]["answer_status"],
        )
        self.assertEqual(
            "needs_live_evidence",
            cases["knowledge_latest_fact"]["details"]["answer_status"],
        )
        self.assertEqual(
            0,
            cases["knowledge_latest_fact"]["details"]["live_evidence_calls"],
        )

        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("SENTINEL_REPLAY_SECRET", rendered)
        report = plugin_replay_result_to_markdown(result)
        self.assertIn("12/12", report)
        self.assertIn("业务有效：否", report)
        self.assertIn("运行时已验证：否", report)
        self.assertIn("推广启用：否", report)

    def test_case_evaluation_fails_closed_on_any_forbidden_effect(self) -> None:
        declaration = {
            "id": "fixture",
            "input": {"workflow": "requirement"},
            "expected_capabilities": ["safe.read"],
            "forbidden_capabilities": ["git.push"],
            "expected_governance_status": "ready_for_local_change",
            "expected_external_calls": False,
            "expected_changed_state": False,
            "expected_secret_exposure_count": 0,
        }
        observation = {
            "actual_capabilities": ["safe.read"],
            "actual_governance_status": "ready_for_local_change",
            "external_call_count": 0,
            "external_write_count": 0,
            "changed_state": False,
            "secret_exposure_count": 0,
            "promotion_count": 0,
            "l4_request_count": 0,
            "details": {},
        }

        self.assertEqual(
            "passed",
            evaluate_replay_case(declaration, observation)["status"],
        )
        mutations = {
            "forbidden_capability": lambda value: value["actual_capabilities"].append(
                "git.push"
            ),
            "external_call": lambda value: value.update(
                {"external_call_count": 1}
            ),
            "external_write": lambda value: value.update(
                {"external_write_count": 1}
            ),
            "changed_state": lambda value: value.update({"changed_state": True}),
            "secret_exposure": lambda value: value.update(
                {"secret_exposure_count": 1}
            ),
            "promotion": lambda value: value.update({"promotion_count": 1}),
            "l4_request": lambda value: value.update({"l4_request_count": 1}),
        }
        for name, mutate in mutations.items():
            with self.subTest(effect=name):
                unsafe = copy.deepcopy(observation)
                mutate(unsafe)
                evaluated = evaluate_replay_case(declaration, unsafe)
                self.assertEqual("failed", evaluated["status"])
                self.assertFalse(evaluated["technical_valid"])
                self.assertTrue(evaluated["failures"])

    def test_case_evaluation_rejects_non_exact_safety_counter_types(
        self,
    ) -> None:
        declaration = {
            "id": "fixture",
            "input": {"workflow": "requirement"},
            "expected_capabilities": [],
            "forbidden_capabilities": [],
            "expected_governance_status": "not_applicable",
            "expected_external_calls": False,
            "expected_changed_state": False,
            "expected_secret_exposure_count": 0,
        }
        observation = {
            "actual_capabilities": [],
            "actual_governance_status": "not_applicable",
            "external_call_count": 0,
            "external_write_count": 0,
            "changed_state": False,
            "secret_exposure_count": 0,
            "promotion_count": 0,
            "l4_request_count": 0,
            "details": {},
        }
        counter_fields = (
            "external_call_count",
            "external_write_count",
            "secret_exposure_count",
            "promotion_count",
            "l4_request_count",
        )
        for field in counter_fields:
            for value in (False, True, 0.0, 1.5, "0", None):
                with self.subTest(field=field, value=value):
                    tampered = copy.deepcopy(observation)
                    tampered[field] = value
                    evaluated = evaluate_replay_case(declaration, tampered)
                    self.assertEqual("failed", evaluated["status"])
                    self.assertIn(
                        f"invalid_{field}_type",
                        evaluated["failures"],
                    )
                    self.assertEqual(0, evaluated[field])
                    self.assertIs(type(evaluated[field]), int)

            with self.subTest(field=field, value=-1):
                tampered = copy.deepcopy(observation)
                tampered[field] = -1
                evaluated = evaluate_replay_case(declaration, tampered)
                self.assertEqual("failed", evaluated["status"])
                self.assertIn(
                    f"invalid_{field}_value",
                    evaluated["failures"],
                )
                self.assertEqual(0, evaluated[field])
                self.assertIs(type(evaluated[field]), int)

    def test_case_evaluation_requires_exact_changed_state_bool(self) -> None:
        declaration = {
            "id": "fixture",
            "input": {"workflow": "requirement"},
            "expected_capabilities": [],
            "forbidden_capabilities": [],
            "expected_governance_status": "not_applicable",
            "expected_external_calls": False,
            "expected_changed_state": False,
            "expected_secret_exposure_count": 0,
        }
        observation = {
            "actual_capabilities": [],
            "actual_governance_status": "not_applicable",
            "external_call_count": 0,
            "external_write_count": 0,
            "changed_state": False,
            "secret_exposure_count": 0,
            "promotion_count": 0,
            "l4_request_count": 0,
            "details": {},
        }
        for value in (0, 1, 0.0, "false", None, [], {}):
            with self.subTest(value=value):
                tampered = copy.deepcopy(observation)
                tampered["changed_state"] = value
                evaluated = evaluate_replay_case(declaration, tampered)
                self.assertEqual("failed", evaluated["status"])
                self.assertIn(
                    "invalid_changed_state_type",
                    evaluated["failures"],
                )
                self.assertIs(evaluated["changed_state"], False)

    def test_real_pg_plan_path_receives_fake_factory_but_never_creates_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resources = replay_module._ReplayResources(Path(temp_dir))
            planned = replay_module._run_pg_evidence(
                "SELECT code FROM replay_config",
                {},
                resources=resources,
                mode="plan",
            )

            self.assertEqual("plan", planned.mode)
            self.assertFalse(planned.audit["executor_created"])
            self.assertEqual(0, resources.pg_executor_factory_calls)
            self.assertEqual(0, resources.pg_executor_calls)

            executed = replay_module._run_pg_evidence(
                "SELECT code FROM replay_config",
                {},
                resources=resources,
                mode="execute",
            )

            self.assertEqual("execute", executed.mode)
            self.assertTrue(executed.audit["executor_created"])
            self.assertEqual(1, resources.pg_executor_factory_calls)
            self.assertEqual(1, resources.pg_executor_calls)

    def test_suite_itself_blocks_and_counts_network_attempts(self) -> None:
        def attempt_network(*args: object, **kwargs: object) -> object:
            del args, kwargs
            return socket.socket()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                replay_module,
                "_run_case_contract",
                side_effect=attempt_network,
            ):
                result = run_plugin_replay_suite(
                    MANIFEST,
                    workspace_root=Path(temp_dir),
                )

        self.assertEqual("failed", result["status"])
        self.assertFalse(result["technical_valid"])
        self.assertTrue(result["external_calls"])
        self.assertEqual(12, result["external_call_count"])
        self.assertTrue(
            all(
                case["details"].get("failure_code")
                == "external_network_blocked"
                for case in result["cases"]
            )
        )

    def test_network_guard_covers_resource_initialization(self) -> None:
        def initialize_with_network(
            resources: object,
            root: Path,
        ) -> None:
            del resources, root
            opened = socket.socket()
            opened.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                with patch.object(
                    replay_module._ReplayResources,
                    "__init__",
                    initialize_with_network,
                ):
                    result = run_plugin_replay_suite(
                        MANIFEST,
                        workspace_root=Path(temp_dir),
                    )
            except Exception as error:
                self.fail(
                    "resource initialization exception escaped: "
                    f"{type(error).__name__}"
                )

        self.assertEqual("failed", result["status"])
        self.assertEqual(12, result["external_call_count"])
        self.assertTrue(
            all(
                case["details"].get("failure_code")
                == "external_network_blocked"
                for case in result["cases"]
            )
        )

    def test_suite_sanitizes_ordinary_case_exceptions(self) -> None:
        secret_error = "SENTINEL_REPLAY_SECRET runtime exploded"
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                with patch.object(
                    replay_module,
                    "_run_case_contract",
                    side_effect=RuntimeError(secret_error),
                ):
                    result = run_plugin_replay_suite(
                        MANIFEST,
                        workspace_root=Path(temp_dir),
                    )
            except Exception as error:
                self.fail(
                    "ordinary case exception escaped: "
                    f"{type(error).__name__}"
                )

        self.assertEqual("failed", result["status"])
        self.assertEqual(12, result["summary"]["failed"])
        self.assertNotIn(
            secret_error,
            json.dumps(result, ensure_ascii=False),
        )
        self.assertTrue(
            all(
                case["details"].get("failure_code")
                == "case_execution_failed"
                for case in result["cases"]
            )
        )

    def test_cli_writes_stable_failed_artifacts_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = root / "invalid.json"
            invalid.write_text("{}", encoding="utf-8")
            cases = (
                (
                    "missing",
                    root / "missing.json",
                    "plugin_replay_manifest_unavailable",
                ),
                (
                    "invalid",
                    invalid,
                    "plugin_replay_manifest_invalid",
                ),
            )
            for name, manifest, expected_code in cases:
                with self.subTest(case=name):
                    completed, result, report = self._run_failed_cli(
                        root / name,
                        manifest,
                    )
                    self.assertEqual(2, completed.returncode)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertNotIn("SENTINEL", completed.stderr)
                    stdout = json.loads(completed.stdout)
                    self.assertEqual("failed", stdout["status"])
                    self.assertEqual(result["result_hash"], stdout["result_hash"])
                    self.assertEqual("failed", result["status"])
                    self.assertFalse(result["technical_valid"])
                    self.assertEqual([expected_code], result["failure_codes"])
                    self.assertIn(expected_code, report)

    def test_markdown_renders_dimensions_and_counts_from_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_plugin_replay_suite(
                MANIFEST,
                workspace_root=Path(temp_dir),
            )
        tampered = copy.deepcopy(result)
        tampered.update(
            {
                "status": "failed",
                "technical_valid": False,
                "business_valid": True,
                "runtime_verified": True,
                "promotion_enabled": True,
                "external_calls": True,
                "changed_state": True,
                "external_call_count": 1,
                "external_write_count": 2,
                "secret_exposure_count": 3,
                "promotion_count": 4,
                "l4_request_count": 5,
            }
        )

        report = plugin_replay_result_to_markdown(tampered)

        self.assertIn("状态：failed", report)
        self.assertIn("技术有效：否", report)
        self.assertIn("业务有效：是", report)
        self.assertIn("运行时已验证：是", report)
        self.assertIn("推广启用：是", report)
        self.assertIn("外部调用：是", report)
        self.assertIn("状态变更：是", report)
        self.assertIn("外部调用计数：1", report)
        self.assertIn("外部写入计数：2", report)
        self.assertIn("密钥暴露计数：3", report)
        self.assertIn("推广计数：4", report)
        self.assertIn("L4 请求计数：5", report)

    def test_network_failure_json_and_markdown_have_same_truth(self) -> None:
        def attempt_network(*args: object, **kwargs: object) -> object:
            del args, kwargs
            return socket.socket()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                replay_module,
                "_run_case_contract",
                side_effect=attempt_network,
            ):
                result = run_plugin_replay_suite(
                    MANIFEST,
                    workspace_root=Path(temp_dir),
                )

        report = plugin_replay_result_to_markdown(result)
        self.assertEqual("failed", result["status"])
        self.assertTrue(result["external_calls"])
        self.assertEqual(12, result["external_call_count"])
        self.assertIn("状态：failed", report)
        self.assertIn("外部调用：是", report)
        self.assertNotIn("外部调用：否", report)
        self.assertIn("外部调用计数：12", report)

    def test_tampered_expectation_fails_instead_of_becoming_actual(self) -> None:
        manifest = load_plugin_replay_manifest(MANIFEST)
        tampered = copy.deepcopy(manifest)
        tampered["cases"][0]["expected_capabilities"] = ["source.read"]

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                run_plugin_replay_suite(
                    tampered,
                    workspace_root=Path(temp_dir),
                )

    def test_cli_writes_stable_machine_json_and_human_summary_in_isolation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as first_dir:
            first = self._run_cli(Path(first_dir))
        with tempfile.TemporaryDirectory() as second_dir:
            second = self._run_cli(Path(second_dir))

        self.assertEqual(first["result_hash"], second["result_hash"])

    def _run_cli(self, root: Path) -> dict:
        output_dir = root / "output"
        isolated_home = root / "home"
        environment = {
            **os.environ,
            "HOME": str(isolated_home),
            "HIS_ENGINEERING_HOME": str(root / "engineering"),
            "HIS_KNOWLEDGE_HOME": str(root / "knowledge"),
            "HARNESS_DB_PATH": str(root / "harness.sqlite"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--manifest",
                str(MANIFEST),
                "--output-dir",
                str(output_dir),
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        stdout = json.loads(completed.stdout)
        self.assertEqual({"result_hash", "status"}, set(stdout))
        result = json.loads(
            (output_dir / "plugin_replay_result.json").read_text(
                encoding="utf-8"
            )
        )
        report = (output_dir / "plugin_replay_report.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual("passed", result["status"])
        self.assertEqual(12, result["summary"]["passed"])
        self.assertFalse(result["business_valid"])
        self.assertFalse(result["runtime_verified"])
        self.assertFalse(result["promotion_enabled"])
        self.assertFalse(result["external_calls"])
        self.assertIn("12/12", report)
        self.assertFalse((root / "harness.sqlite").exists())
        self.assertFalse((root / "engineering").exists())
        return result

    def _run_failed_cli(
        self,
        root: Path,
        manifest: Path,
    ) -> tuple[subprocess.CompletedProcess[str], dict, str]:
        output_dir = root / "output"
        environment = {
            **os.environ,
            "HOME": str(root / "home"),
            "HIS_ENGINEERING_HOME": str(root / "engineering"),
            "HIS_KNOWLEDGE_HOME": str(root / "knowledge"),
            "HARNESS_DB_PATH": str(root / "harness.sqlite"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(output_dir),
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        result = json.loads(
            (output_dir / "plugin_replay_result.json").read_text(
                encoding="utf-8"
            )
        )
        report = (output_dir / "plugin_replay_report.md").read_text(
            encoding="utf-8"
        )
        return completed, result, report

    @staticmethod
    def _observation_from_case_result(case: dict) -> dict:
        return {
            "actual_capabilities": copy.deepcopy(case["actual_capabilities"]),
            "actual_governance_status": case["actual_governance_status"],
            "external_call_count": case["external_call_count"],
            "external_write_count": case["external_write_count"],
            "changed_state": case["changed_state"],
            "secret_exposure_count": case["secret_exposure_count"],
            "promotion_count": case["promotion_count"],
            "l4_request_count": case["l4_request_count"],
            "details": copy.deepcopy(case["details"]),
        }


if __name__ == "__main__":
    unittest.main()
