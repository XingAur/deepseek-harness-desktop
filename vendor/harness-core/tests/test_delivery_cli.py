from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import tools.delivery as delivery_cli
from tests.test_delivery_closure import GitRepositoryFixture


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_DEFAULT_HOME = Path("/Users/lym/.local/share/his-engineering")


def _snapshot_real_default_home() -> tuple[tuple[str, str, str], ...]:
    if not REAL_DEFAULT_HOME.exists():
        return ()
    snapshot: list[tuple[str, str, str]] = []
    for path in (REAL_DEFAULT_HOME, *sorted(REAL_DEFAULT_HOME.rglob("*"))):
        relative = "." if path == REAL_DEFAULT_HOME else path.relative_to(REAL_DEFAULT_HOME).as_posix()
        if path.is_symlink():
            snapshot.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            snapshot.append((relative, "directory", ""))
        elif path.is_file():
            snapshot.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            snapshot.append((relative, "other", ""))
    return tuple(snapshot)


class DeliveryCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_default_home_snapshot = _snapshot_real_default_home()
        self.repo = GitRepositoryFixture()
        self.state_temp = tempfile.TemporaryDirectory()
        self.state_root = Path(self.state_temp.name)
        self.db_path = self.state_root / "harness.sqlite"
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        self.diff_path = self.state_root / "final.diff"
        self.diff_path.write_text(self.repo.diff("app.txt"), encoding="utf-8")
        self.output_dir = self.state_root / "output"

    def tearDown(self) -> None:
        self.state_temp.cleanup()
        self.repo.close()
        self.assertEqual(
            self.real_default_home_snapshot,
            _snapshot_real_default_home(),
            "delivery CLI tests must not mutate the real plugin home",
        )

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["HARNESS_DB_PATH"] = str(self.db_path)
        environment["HIS_ENGINEERING_HOME"] = str(
            self.state_root / "his-engineering-home"
        )
        return subprocess.run(
            [sys.executable, "tools/delivery.py", *args],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_cli_first_delivery_requires_the_bound_confirm_flag(self) -> None:
        initial_head = self.repo.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        prepared = self.run_cli(
            "prepare",
            "--entity-kind",
            "requirement",
            "--entity-id",
            "DFHIS-31557",
            "--title",
            "挂号处理界面证件类型需要默认成身份证。",
            "--url",
            "https://devops.aliyun.com/projex/req/DFHIS-31557#",
            "--project-path",
            str(self.repo.root),
            "--diff-file",
            str(self.diff_path),
            "--allowed-path",
            "app.txt",
            "--output-dir",
            str(self.output_dir),
            "--json",
        )
        self.assertEqual(0, prepared.returncode, prepared.stderr)
        transaction_id = int(json.loads(prepared.stdout)["transaction"]["id"])

        accepted = self.run_cli(
            "accept-release",
            "--transaction-id",
            str(transaction_id),
            "--summary",
            "release 页面验证通过",
            "--json",
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        executed = self.run_cli(
            "first-confirmation",
            "--transaction-id",
            str(transaction_id),
            "--confirm",
            "--json",
        )

        self.assertEqual(0, executed.returncode, executed.stderr)
        payload = json.loads(executed.stdout)
        self.assertEqual("task_commit_created", payload["stage_one"]["state"])
        self.assertEqual(
            "release_2.15.3_250515",
            self.repo.run(["git", "branch", "--show-current"]).stdout.strip(),
        )
        self.assertEqual(
            initial_head,
            self.repo.run(["git", "rev-parse", "HEAD"]).stdout.strip(),
        )
        task_commit = self.repo.run(
            ["git", "rev-parse", "refs/heads/feature-DFHIS-31557"]
        ).stdout.strip()
        self.assertEqual(task_commit, payload["stage_one"]["commit"]["commit"])
        self.assertEqual(
            "line one\nline two changed\n",
            (self.repo.root / "app.txt").read_text(encoding="utf-8"),
        )
        self.assertEqual("not_requested", payload["task_push"]["status"])
        self.assertEqual("not_requested", payload["integration"]["status"])
        self.assertFalse(payload["rc_push_executed"])
        self.assertNotIn("plan_hash", executed.stderr)

        repeated = self.run_cli(
            "first-confirmation",
            "--transaction-id",
            str(transaction_id),
            "--confirm",
            "--json",
        )
        self.assertEqual(0, repeated.returncode, repeated.stderr)
        repeated_payload = json.loads(repeated.stdout)
        self.assertTrue(repeated_payload["stage_one"]["idempotent"])
        self.assertEqual(
            task_commit,
            repeated_payload["stage_one"]["commit"]["commit"],
        )

    def test_first_delivery_rejects_command_without_confirm_flag(self) -> None:
        result = self.run_cli("first-confirmation", "--transaction-id", "1", "--json")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("--confirm", result.stderr)

    def test_explicit_full_delivery_flags_are_persisted_in_the_plan(self) -> None:
        remote = self.run_cli(
            "prepare",
            "--entity-kind",
            "requirement",
            "--entity-id",
            "DFHIS-31557",
            "--title",
            "title",
            "--url",
            "https://example.test/item",
            "--project-path",
            str(self.repo.root),
            "--diff-file",
            str(self.diff_path),
            "--allowed-path",
            "app.txt",
            "--output-dir",
            str(self.output_dir),
            "--push-feature",
            "--integrate-rc",
            "--push-rc",
            "--json",
        )
        self.assertEqual(0, remote.returncode, remote.stderr)
        payload = json.loads(remote.stdout)
        self.assertTrue(payload["plan"]["remote_actions_enabled"])
        self.assertTrue(payload["plan"]["actions"]["push_feature"])
        self.assertTrue(payload["plan"]["actions"]["cherry_pick_integration"])
        self.assertTrue(payload["plan"]["actions"]["push_integration"])

    def test_prepare_accepts_an_explicit_rc_base_branch(self) -> None:
        self.repo.run(["git", "branch", "-m", "RC_2.16.1_250514"])

        prepared = self.run_cli(
            "prepare",
            "--entity-kind", "requirement",
            "--entity-id", "DFHIS-31557",
            "--title", "title",
            "--url", "https://example.test/item",
            "--project-path", str(self.repo.root),
            "--diff-file", str(self.diff_path),
            "--allowed-path", "app.txt",
            "--output-dir", str(self.output_dir),
            "--base-branch", "RC_2.16.1_250514",
            "--json",
        )

        self.assertEqual(0, prepared.returncode, prepared.stderr)
        self.assertEqual(
            "RC_2.16.1_250514",
            json.loads(prepared.stdout)["plan"]["base_branch"],
        )

    def test_prepare_persists_a_declared_gitlab_action_file(self) -> None:
        action_path = self.state_root / "gitlab-action.json"
        action = {
            "action": "merge_request.comment.write",
            "parameters": {
                "host_alias": "company",
                "project_alias": "dfhis/guahao",
                "merge_request_iid": 31557,
                "body": "已完成验证，请评审。",
            },
        }
        action_path.write_text(json.dumps(action, ensure_ascii=False), encoding="utf-8")

        prepared = self.run_cli(
            "prepare",
            "--entity-kind", "requirement",
            "--entity-id", "DFHIS-31557",
            "--title", "title",
            "--url", "https://example.test/item",
            "--project-path", str(self.repo.root),
            "--diff-file", str(self.diff_path),
            "--allowed-path", "app.txt",
            "--output-dir", str(self.output_dir),
            "--gitlab-action-file", str(action_path),
            "--json",
        )

        self.assertEqual(0, prepared.returncode, prepared.stderr)
        payload = json.loads(prepared.stdout)
        self.assertEqual(action, payload["plan"]["actions"]["gitlab_write"])

    def test_prepare_derives_gitlab_project_from_origin_when_mr_is_requested(self) -> None:
        self.repo.run(
            [
                "git", "remote", "add", "origin",
                "https://gitlab.example.test/dfhis/guahao.git",
            ]
        )

        prepared = self.run_cli(
            "prepare",
            "--entity-kind", "requirement",
            "--entity-id", "DFHIS-31557",
            "--title", "title",
            "--url", "https://example.test/item",
            "--project-path", str(self.repo.root),
            "--diff-file", str(self.diff_path),
            "--allowed-path", "app.txt",
            "--output-dir", str(self.output_dir),
            "--create-gitlab-mr",
            "--json",
        )

        self.assertEqual(0, prepared.returncode, prepared.stderr)
        parameters = json.loads(prepared.stdout)["plan"]["actions"]["gitlab_write"]["parameters"]
        self.assertEqual("gitlab.example.test", parameters["gitlab_host"])
        self.assertEqual("dfhis/guahao", parameters["project_alias"])

    def test_prepare_persists_a_declared_github_action_file(self) -> None:
        action_path = self.state_root / "github-action.json"
        action = {
            "action": "github.pull_request.comment.write",
            "parameters": {
                "owner": "dfhis",
                "repository": "guahao",
                "pull_request_number": 31557,
                "body": "已完成验证，请评审。",
            },
        }
        action_path.write_text(json.dumps(action, ensure_ascii=False), encoding="utf-8")

        prepared = self.run_cli(
            "prepare",
            "--entity-kind", "requirement",
            "--entity-id", "DFHIS-31557",
            "--title", "title",
            "--url", "https://example.test/item",
            "--project-path", str(self.repo.root),
            "--diff-file", str(self.diff_path),
            "--allowed-path", "app.txt",
            "--output-dir", str(self.output_dir),
            "--github-action-file", str(action_path),
            "--json",
        )

        self.assertEqual(0, prepared.returncode, prepared.stderr)
        self.assertEqual(action, json.loads(prepared.stdout)["plan"]["actions"]["github_write"])

    def test_prepare_derives_github_project_from_origin_when_pr_is_requested(self) -> None:
        self.repo.run(
            ["git", "remote", "add", "origin", "https://github.com/dfhis/guahao.git"]
        )

        prepared = self.run_cli(
            "prepare",
            "--entity-kind", "requirement",
            "--entity-id", "DFHIS-31557",
            "--title", "title",
            "--url", "https://example.test/item",
            "--project-path", str(self.repo.root),
            "--diff-file", str(self.diff_path),
            "--allowed-path", "app.txt",
            "--output-dir", str(self.output_dir),
            "--create-github-pr",
            "--json",
        )

        self.assertEqual(0, prepared.returncode, prepared.stderr)
        parameters = json.loads(prepared.stdout)["plan"]["actions"]["github_write"]["parameters"]
        self.assertEqual("dfhis", parameters["owner"])
        self.assertEqual("guahao", parameters["repository"])

    def test_rc_stage_routes_the_origin_derived_mr_through_l4_capabilities(self) -> None:
        self.assertTrue(
            hasattr(delivery_cli, "execute_stage_two"),
            "RC 阶段需要 origin 派生 MR 的受控执行入口",
        )
        executor = unittest.mock.Mock(
            return_value={
                "action": "merge_request.create",
                "status": "success",
                "write_effect_status": "verified",
                "target_alias": "gl-h7-gitlab-example-test-g5-dfhis-p6-guahao-m9",
                "remote_dispatch_attempted": True,
            }
        )

        class _Closure:
            def __init__(self) -> None:
                self.calls: list[dict] = []
                self.store = SimpleNamespace(path=Path("/private/tmp/delivery.sqlite"))

            def complete_declared_gitlab_action(self, transaction_id, **kwargs):
                self.calls.append({"transaction_id": transaction_id, **kwargs})
                return {"state": "completed"}

        class _Service:
            def __init__(self) -> None:
                self.requests = []

            def route(self, request):
                self.requests.append(request)
                data = (
                    {"state": "gitlab_delivery_pending"}
                    if request.capability == "git.push"
                    else {"gitlab_action": {"action": "merge_request.create"}}
                )
                return SimpleNamespace(result={"status": "success", "data": data})

        closure = _Closure()
        service = _Service()
        plan = {
            "plan_hash": "a" * 64,
            "actions": {
                "gitlab_write": {
                    "action": "merge_request.create",
                    "parameters": {
                        "host_alias": "gitlab-example-test",
                        "gitlab_host": "gitlab.example.test",
                        "project_alias": "dfhis/guahao",
                        "source_branch": "feature-DFHIS-31557",
                        "target_branch": "RC_2.16.1_250514",
                        "title": "DFHIS-31557 title",
                    },
                }
            },
        }

        result = delivery_cli.execute_stage_two(
            closure,
            transaction_id=22,
            plan=plan,
            service=service,
            executor_factory=lambda: executor,
        )

        self.assertEqual("completed", result["state"])
        self.assertEqual(22, closure.calls[0]["transaction_id"])
        self.assertEqual("a" * 64, closure.calls[0]["approved_plan_hash"])
        self.assertEqual(
            ["git.push", "gitlab.write"],
            [request.capability for request in service.requests],
        )
        self.assertEqual("rc", service.requests[0].input["phase"])
        self.assertEqual(
            ("repository:push", "capability:git.push"),
            service.requests[0].authorization.scope,
        )
        self.assertEqual(
            ("gitlab:write", "capability:gitlab.write"),
            service.requests[1].authorization.scope,
        )

    def test_rc_stage_routes_declared_github_action_through_l4_capabilities(self) -> None:
        executor = unittest.mock.Mock(
            return_value={
                "action": "github.pull_request.create",
                "status": "success",
                "write_effect_status": "verified_applied",
                "target_alias": "gh-o5-dfhis-r6-guahao-p9",
                "remote_dispatch_attempted": True,
            }
        )

        class _Closure:
            def __init__(self) -> None:
                self.calls: list[dict] = []
                self.store = SimpleNamespace(path=Path("/private/tmp/delivery.sqlite"))

            def complete_declared_github_action(self, transaction_id, **kwargs):
                self.calls.append({"transaction_id": transaction_id, **kwargs})
                return {"state": "completed"}

        class _Service:
            def __init__(self) -> None:
                self.requests = []

            def route(self, request):
                self.requests.append(request)
                data = (
                    {"state": "github_delivery_pending"}
                    if request.capability == "git.push"
                    else {"github_action": {"action": "github.pull_request.create"}}
                )
                return SimpleNamespace(result={"status": "success", "data": data})

        closure = _Closure()
        service = _Service()
        plan = {
            "plan_hash": "a" * 64,
            "actions": {
                "github_write": {
                    "action": "github.pull_request.create",
                    "parameters": {
                        "owner": "dfhis",
                        "repository": "guahao",
                        "head": "feature-DFHIS-31557",
                        "base": "RC_2.16.1_250514",
                        "title": "DFHIS-31557 title",
                    },
                }
            },
        }

        result = delivery_cli.execute_stage_two(
            closure,
            transaction_id=22,
            plan=plan,
            service=service,
            github_executor_factory=lambda: executor,
        )

        self.assertEqual("completed", result["state"])
        self.assertEqual(22, closure.calls[0]["transaction_id"])
        self.assertEqual(
            ["git.push", "github.write"],
            [request.capability for request in service.requests],
        )
        self.assertEqual(
            ("github:write", "capability:github.write"),
            service.requests[1].authorization.scope,
        )

    def test_first_confirmation_block_is_nonzero_and_does_not_publish_branch(self) -> None:
        prepared = self.run_cli(
            "prepare",
            "--entity-kind",
            "requirement",
            "--entity-id",
            "DFHIS-31557",
            "--title",
            "title",
            "--url",
            "https://example.test/item",
            "--project-path",
            str(self.repo.root),
            "--diff-file",
            str(self.diff_path),
            "--allowed-path",
            "app.txt",
            "--output-dir",
            str(self.output_dir),
            "--json",
        )
        self.assertEqual(0, prepared.returncode, prepared.stderr)
        transaction_id = int(json.loads(prepared.stdout)["transaction"]["id"])

        blocked = self.run_cli(
            "first-confirmation",
            "--transaction-id",
            str(transaction_id),
            "--confirm",
            "--json",
        )
        self.assertEqual(2, blocked.returncode)
        self.assertIn("git_commit_local_blocked", blocked.stderr)
        branch = self.repo.run(
            ["git", "show-ref", "--verify", "refs/heads/feature-DFHIS-31557"],
            check=False,
        )
        self.assertNotEqual(0, branch.returncode)


if __name__ == "__main__":
    unittest.main()
