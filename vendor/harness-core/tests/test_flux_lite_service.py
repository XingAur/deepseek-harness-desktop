from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.flux_lite_learning import ReviewerOpinion
from app.flux_lite_service import FluxLiteExperienceService
from app.local_agent_repository import LocalAgentRunRepository
from app.local_agent_contract import load_local_agent_task
from app.runtime_policy import assert_local_agent_run_allowed


class FluxLiteExperienceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "harness.sqlite"
        self.project = self.root / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / "calculator.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", "calculator.py"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.project), "-c", "user.email=fixture@example.invalid",
                "-c", "user.name=Fixture", "commit", "--quiet", "-m", "fixture",
            ],
            check=True,
        )
        contract = self.root / "task.json"
        contract.write_text(
            json.dumps({
                "schema_version": "his-local-agent-task.v1",
                "task_key": "calculator",
                "project_path": str(self.project),
                "request": "Fix a Python validation path.",
                "allowed_paths": ["calculator.py"],
                "verification_commands": [[sys.executable, "-m", "unittest", "-q"]],
                "acceptance_criteria": ["Run the bounded verification."],
                "timeout_seconds": 120,
            }),
            encoding="utf-8",
        )
        self.task = load_local_agent_task(contract)

        def connection_factory() -> sqlite3.Connection:
            return database.connect_database(self.database_path)

        database.init_db(connection_factory=connection_factory)
        self.local_repository = LocalAgentRunRepository(self.database_path, connection_factory=connection_factory)
        self.service = FluxLiteExperienceService(self.local_repository)
        preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id="flux-lite-service-fixture",
        )
        run = self.local_repository.consume_preflight(self.task, preflight)
        self.run_id = int(run["id"])
        worktree_root = f"/private/tmp/his_harness_stage_f_flux_lite/run_{self.run_id}"
        self.local_repository.bind_workspace(
            self.run_id,
            {
                "worktree_path": worktree_root,
                "source_metadata": {},
                "source_worktrees": [],
                "worktree_identity": [17, self.run_id, 16_384],
                "worktree_git_identity": [17, self.run_id + 100, 32_768],
                "marker_path": f"/private/tmp/his_harness_stage_f_flux_lite/.harness_worktree_markers/{hashlib.sha256(worktree_root.encode()).hexdigest()}.json",
                "task_artifact": f".harness_local_agent_control/run_{self.run_id}/task.json",
                "task_sha256": "a" * 64,
            },
        )
        attempt = self.local_repository.start_attempt(self.run_id)
        self.attempt_id = int(attempt["id"])
        with patch(
            "app.local_agent_repository._read_process_start_identity",
            return_value="darwin-proc-bsdinfo-v1:1:2",
        ):
            self.local_repository.bind_worker_identity(
                self.attempt_id, 12345, "darwin-proc-bsdinfo-v1:1:2",
            )
        self.local_repository.complete_attempt(self.attempt_id, "completed")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _opinion(reviewer_id: str) -> ReviewerOpinion:
        return ReviewerOpinion(
            reviewer_id=reviewer_id,
            scope_key="python:calculator",
            root_cause="verification_failure",
            focus_actions=("verification_replay",),
            verdict="changes_requested",
            evidence_refs=("sha256:" + hashlib.sha256(reviewer_id.encode()).hexdigest(),),
        )

    def test_consensus_candidate_is_available_as_a_canonical_learning_check(self) -> None:
        self.service.record_reviewer_opinions(
            task=self.task,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            opinions=(self._opinion("reviewer-a"), self._opinion("reviewer-b")),
        )

        checks = self.service.matched_checks_for_attempt(self.task, run_id=self.run_id)

        self.assertEqual(1, len(checks))
        self.assertEqual(("verification_replay",), checks[0].rule.actions)

    def test_single_reviewer_and_high_risk_candidate_are_evidence_only(self) -> None:
        self.service.record_reviewer_opinions(
            task=self.task,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            opinions=(self._opinion("reviewer-a"),),
        )
        self.assertEqual((), self.service.matched_checks_for_attempt(self.task, run_id=self.run_id))


if __name__ == "__main__":
    unittest.main()
