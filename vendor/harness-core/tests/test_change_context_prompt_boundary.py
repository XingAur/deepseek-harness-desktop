from __future__ import annotations

import ast
import inspect
import textwrap
import unittest

from app.harness import RequirementWorkflowRunner


class ChangeContextPromptBoundaryTests(unittest.TestCase):
    def test_mutating_executors_receive_original_demand_not_composed_evidence_text(self) -> None:
        source = textwrap.dedent(inspect.getsource(RequirementWorkflowRunner.run))
        tree = ast.parse(source)
        guarded_calls = {
            "_run_core_closure_trial",
            "_run_worktree_execution",
            "_run_fullstack_execution",
        }
        seen: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in guarded_calls:
                continue
            demand = next((item.value for item in node.keywords if item.arg == "demand_text"), None)
            self.assertIsInstance(demand, ast.Name, node.func.attr)
            self.assertEqual("demand_text", demand.id, node.func.attr)
            seen.append(node.func.attr)
        self.assertGreaterEqual(seen.count("_run_worktree_execution"), 2)
        self.assertIn("_run_core_closure_trial", seen)
        self.assertIn("_run_fullstack_execution", seen)

    def test_core_closure_keeps_governance_evidence_separate_from_worker_demand(self) -> None:
        source = inspect.getsource(RequirementWorkflowRunner._run_core_closure_trial)
        self.assertIn("contract_demand_text", source)
        self.assertIn("demand_text=contract_demand_text", source)
        self.assertIn("demand_text=demand_text", source)


if __name__ == "__main__":
    unittest.main()
