from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import self_check


class SelfCheckPluginMigrationTests(unittest.TestCase):
    SECTIONS = (
        "capability_registry_checks",
        "capability_permission_checks",
        "yunxiao_readonly_plugin_checks",
        "requirement_governance_checks",
        "git_plugin_checks",
        "database_plugin_checks",
        "knowledge_plugin_checks",
        "plugin_replay_checks",
    )
    MAIN_CHECK_RUNNERS = (
        "run_acceptance_matrix_checks",
        "run_requirement_calibration_checks",
        "run_core_closure_checks",
        "run_acceptance_contract_checks",
        "run_behavior_acceptance_checks",
        "run_interaction_evidence_checks",
        "run_configuration_checks",
        "run_requirement_provider_checks",
        "run_dynamic_planning_checks",
        "run_dynamic_plan_registry_checks",
        "run_dynamic_scheduler_checks",
        "run_node_runtime_checks",
        "run_sandbox_executor_checks",
        "run_mock_agent_checks",
        "run_model_invocation_checks",
        "run_model_dag_checks",
        "run_model_provider_checks",
        "run_pg_evidence_checks",
        "run_task_manager_checks",
        "run_worktree_checks",
        "run_patch_readiness_checks",
        "run_yunxiao_transaction_dry_run_checks",
        "run_review_checks",
    )
    BOUNDARY_LINES = (
        "本结果仅表示插件契约和本地技术链路通过；",
        "未证明真实云效、GitLab、数据库、业务运行时或生产环境通过。",
    )

    def assert_exact_boundary_lines(self, markdown: str) -> None:
        lines = markdown.splitlines()
        for line in self.BOUNDARY_LINES:
            self.assertEqual(1, lines.count(line))
        first_line_index = lines.index(self.BOUNDARY_LINES[0])
        self.assertEqual(
            list(self.BOUNDARY_LINES),
            lines[first_line_index : first_line_index + 2],
        )

    def test_cli_bootstraps_repo_plugins_without_test_environment(self) -> None:
        environment = os.environ.copy()
        environment.pop("HARNESS_ENABLE_STAGED_PLUGIN_TESTS", None)
        environment.pop("HARNESS_STAGED_PLUGIN_ROOT", None)

        completed = subprocess.run(
            [sys.executable, str(Path(self_check.__file__)), "--help"],
            cwd=Path(self_check.__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_cli_uses_temporary_database_without_explicit_database_path(self) -> None:
        environment = os.environ.copy()
        environment.pop("HARNESS_DB_PATH", None)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "from tools import self_check; "
                    "project = self_check.PROJECT_ROOT.resolve(); "
                    "database = self_check.database.DB_PATH.resolve(); "
                    "assert project not in database.parents, database; "
                    "print(database)"
                ),
            ],
            cwd=Path(self_check.__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_required_files_cover_plugin_migration_contracts(self) -> None:
        required = {
            "app/capability_contracts.py",
            "app/capability_permissions.py",
            "app/capability_registry.py",
            "app/capability_runtime.py",
            "app/capability_service.py",
            "app/task_capability_routing.py",
            "app/requirement_governance.py",
            "app/plugin_replay_suite.py",
            "tools/plugin_replay_suite.py",
            "config/schemas/capability_manifest.v1.json",
            "config/schemas/capability_request.v1.json",
            "config/schemas/capability_result.v1.json",
            "config/schemas/requirement_governance.v1.json",
            "config/schemas/plugin_replay_manifest.v1.json",
            "fixtures/replay/plugin_migration_v1.json",
        }

        self.assertTrue(required.issubset(set(self_check.REQUIRED_FILES)))
        self.assertEqual(
            {
                "his-harness-core",
                "his-engineering",
                "his-knowledge",
                "yunxiao",
            },
            set(self_check.REQUIRED_PLUGIN_FILES),
        )

    def test_required_plugin_files_fall_back_to_formal_installation_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository_root = root / "repository"
            formal_root = root / "formal-plugins"
            repository_root.mkdir()
            for plugin_name, relative_files in self_check.REQUIRED_PLUGIN_FILES.items():
                for relative_file in relative_files:
                    path = formal_root / plugin_name / relative_file
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("{}", encoding="utf-8")

            resolved = self_check.resolve_required_plugin_files(
                repository_root=repository_root,
                formal_plugin_root=formal_root,
            )

        self.assertTrue(resolved)
        self.assertTrue(
            all(path.is_relative_to(formal_root) for _, path in resolved)
        )
        self.assertEqual(
            {
                f"plugin:{plugin_name}/{relative_file}"
                for plugin_name, relative_files in self_check.REQUIRED_PLUGIN_FILES.items()
                for relative_file in relative_files
            },
            {label for label, _ in resolved},
        )

    def test_all_plugin_migration_sections_run_with_only_local_fakes(self) -> None:
        real_run = subprocess.run

        def local_git_only(args: object, *values: object, **kwargs: object):
            argv = [str(item) for item in args]
            self.assertEqual("/usr/bin/git", argv[0])
            self.assertFalse({"clone", "fetch", "pull", "push", "ls-remote"} & set(argv))
            return real_run(args, *values, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(socket, "socket", side_effect=AssertionError("network forbidden")),
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=AssertionError("network forbidden"),
                ),
                patch.object(
                    socket,
                    "getaddrinfo",
                    side_effect=AssertionError("network forbidden"),
                ),
                patch.object(
                    urllib.request,
                    "urlopen",
                    side_effect=AssertionError("network forbidden"),
                ),
                patch.object(subprocess, "run", side_effect=local_git_only),
            ):
                sections = self_check.run_plugin_migration_check_sections(
                    output_dir=Path(temp_dir)
                )

        self.assertEqual(self.SECTIONS, tuple(sections))
        self.assertTrue(self_check.plugin_migration_checks_pass(sections))
        for section in self.SECTIONS:
            with self.subTest(section=section):
                self.assertTrue(sections[section])
                self.assertTrue(
                    all(item["status"] == "pass" for item in sections[section])
                )
        rendered = json.dumps(sections, ensure_ascii=False)
        self.assertNotIn("SENTINEL_SELF_CHECK_SECRET", rendered)

    def test_section_exception_is_stable_failed_and_part_of_all_passed(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                self_check,
                "run_database_plugin_checks",
                side_effect=RuntimeError("SENTINEL_SELF_CHECK_SECRET"),
            ),
        ):
            sections = self_check.run_plugin_migration_check_sections(
                output_dir=Path(temp_dir)
            )

        self.assertEqual(
            [
                {
                    "name": "database_plugin_checks_failed_closed",
                    "status": "failed",
                    "message": "Plugin self-check failed closed.",
                }
            ],
            sections["database_plugin_checks"],
        )
        self.assertFalse(self_check.plugin_migration_checks_pass(sections))
        self.assertNotIn(
            "SENTINEL_SELF_CHECK_SECRET",
            json.dumps(sections, ensure_ascii=False),
        )

    def test_missing_section_runner_is_stable_failed_closed(self) -> None:
        missing_runner = "run_capability_registry_checks"
        runner = self_check.__dict__.pop(missing_runner)
        try:
            with ExitStack() as stack:
                for section in self.SECTIONS[1:]:
                    stack.enter_context(
                        patch.object(
                            self_check,
                            f"run_{section}",
                            return_value=[
                                {
                                    "name": f"{section}_fixture",
                                    "status": "pass",
                                    "message": "fixture",
                                }
                            ],
                        )
                    )
                with tempfile.TemporaryDirectory() as temp_dir:
                    try:
                        sections = self_check.run_plugin_migration_check_sections(
                            output_dir=Path(temp_dir)
                        )
                    except Exception as exc:
                        self.fail(
                            "missing runner escaped fail-closed boundary: "
                            f"{type(exc).__name__}"
                        )
        finally:
            setattr(self_check, missing_runner, runner)

        self.assertEqual(
            [
                {
                    "name": "capability_registry_checks_failed_closed",
                    "status": "failed",
                    "message": "Plugin self-check failed closed.",
                }
            ],
            sections["capability_registry_checks"],
        )
        self.assertFalse(self_check.plugin_migration_checks_pass(sections))
        self.assertNotIn(missing_runner, json.dumps(sections, ensure_ascii=False))

    def test_main_propagates_plugin_section_failure_to_status_and_exit(self) -> None:
        failed_sections = {
            section: [
                {
                    "name": f"{section}_fixture",
                    "status": "failed"
                    if section == "database_plugin_checks"
                    else "pass",
                    "message": "fixture",
                }
            ]
            for section in self.SECTIONS
        }
        captured_result: dict = {}

        def capture_result(output_dir: Path, result: dict) -> None:
            del output_dir
            captured_result.update(result)

        passing_checks = [{"status": "pass"}]
        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            stack.enter_context(patch.object(self_check, "SAMPLES", ()))
            stack.enter_context(
                patch.object(self_check, "run_preflight", return_value=True)
            )
            stack.enter_context(
                patch.object(
                    self_check,
                    "create_fixture_project",
                    return_value=Path(temp_dir) / "fixture",
                )
            )
            for runner_name in self.MAIN_CHECK_RUNNERS:
                stack.enter_context(
                    patch.object(
                        self_check,
                        runner_name,
                        return_value=passing_checks,
                    )
                )
            plugin_runner = stack.enter_context(
                patch.object(
                    self_check,
                    "run_plugin_migration_check_sections",
                    return_value=failed_sections,
                )
            )
            stack.enter_context(
                patch.object(
                    self_check,
                    "write_self_check_outputs",
                    side_effect=capture_result,
                )
            )
            stack.enter_context(
                patch.object(
                    sys,
                    "argv",
                    [
                        "self_check.py",
                        "--mode",
                        "mock",
                        "--retain-output",
                        "--output-dir",
                        temp_dir,
                    ],
                )
            )
            stack.enter_context(patch("builtins.print"))

            with self.assertRaises(SystemExit) as exit_context:
                self_check.main()

        plugin_runner.assert_called_once_with(output_dir=Path(temp_dir))
        self.assertEqual(1, exit_context.exception.code)
        self.assertEqual("failed", captured_result["status"])
        self.assertEqual(
            failed_sections["database_plugin_checks"],
            captured_result["database_plugin_checks"],
        )

    def test_database_and_knowledge_checks_use_public_execute_request(self) -> None:
        database_calls: list[str] = []
        knowledge_calls: list[str] = []

        def database_read_execute(request: dict, **kwargs: object) -> dict:
            database_calls.append(request["input"]["sql"].split()[0])
            is_write = request["input"]["sql"].startswith("UPDATE")
            return {
                "status": "blocked" if is_write else "success",
                "changed": False,
                "data": {"pg_status": "blocked" if is_write else "planned"},
                "audit": {
                    "external_write_attempted": False,
                    "database_connection_attempted": False,
                },
            }

        def database_change_execute(request: dict) -> dict:
            database_calls.append(request["capability"])
            return {
                "status": "blocked",
                "changed": False,
                "audit": {
                    "external_write_attempted": False,
                    "credential_loaded": False,
                    "database_connection_attempted": False,
                    "database_execution_attempted": False,
                },
            }

        def database_loader(relative_path: str, module_name: str):
            del module_name
            if relative_path.endswith("database_read.py"):
                return SimpleNamespace(execute_request=database_read_execute)
            if relative_path.endswith("database_change.py"):
                return SimpleNamespace(execute_request=database_change_execute)
            raise AssertionError(relative_path)

        def knowledge_execute(request: dict) -> dict:
            knowledge_calls.append(request["capability"])
            return {
                "status": "success",
                "changed": False,
                "data": {
                    "answer_status": "needs_live_evidence",
                    "suggested_capabilities": ["workitem.read"],
                },
                "evidence": [],
                "audit": {
                    "external_write_attempted": False,
                    "suggestions_executed": False,
                },
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                self_check,
                "_load_plugin_module",
                side_effect=database_loader,
            ):
                database = self_check.run_database_plugin_checks(
                    output_dir=Path(temp_dir)
                )
            with patch.object(
                self_check,
                "_load_plugin_module",
                return_value=SimpleNamespace(execute_request=knowledge_execute),
            ):
                knowledge = self_check.run_knowledge_plugin_checks(
                    output_dir=Path(temp_dir)
                )

        self.assertEqual(["SELECT", "UPDATE", "database.change"], database_calls)
        self.assertEqual(["knowledge.answer"], knowledge_calls)
        self.assertEqual(["pass"], [item["status"] for item in database])
        self.assertEqual(["pass"], [item["status"] for item in knowledge])

    def test_markdown_contains_plugin_sections_and_truthful_boundary_lines(self) -> None:
        result = {
            "mode": "mock",
            "business_valid": False,
            "status": "passed",
            "summary": "fixture",
            "project_context": {},
            "preflight": [],
            "samples": [],
        }
        result.update({section: [] for section in self.SECTIONS})

        markdown = self_check.build_report(result)

        for heading in (
            "## Capability Registry Checks",
            "## Capability Permission Checks",
            "## Yunxiao Read-only Plugin Checks",
            "## Requirement Governance Checks",
            "## Git Plugin Checks",
            "## Database Plugin Checks",
            "## Knowledge Plugin Checks",
            "## Plugin Replay Checks",
        ):
            self.assertIn(heading, markdown)
        self.assert_exact_boundary_lines(markdown)

        appended_text_mutation = markdown.replace(
            self.BOUNDARY_LINES[0],
            f"{self.BOUNDARY_LINES[0]}MUTATION",
        )
        with self.assertRaises(AssertionError):
            self.assert_exact_boundary_lines(appended_text_mutation)

        duplicate_mutation = f"{markdown}\n{self.BOUNDARY_LINES[0]}"
        with self.assertRaises(AssertionError):
            self.assert_exact_boundary_lines(duplicate_mutation)

        inserted_line_mutation = markdown.replace(
            "\n".join(self.BOUNDARY_LINES),
            f"{self.BOUNDARY_LINES[0]}\nMUTATION\n{self.BOUNDARY_LINES[1]}",
        )
        with self.assertRaises(AssertionError):
            self.assert_exact_boundary_lines(inserted_line_mutation)


if __name__ == "__main__":
    unittest.main()
