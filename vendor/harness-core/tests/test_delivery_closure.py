from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT


os.environ.setdefault("HARNESS_ENABLE_STAGED_PLUGIN_TESTS", "1")
os.environ.setdefault(
    "HARNESS_STAGED_PLUGIN_ROOT",
    str(PLUGIN_SOURCE_ROOT),
)
from app import delivery_closure as delivery_adapter
from app.delivery_closure import (
    DeliveryClosure,
    DeliveryError,
    DeliveryPolicy,
    DeliveryRequest,
    audit_cherry_pick_parity,
    build_delivery_plan,
    inspect_repository,
    stable_hash,
)


class GitRepositoryFixture:
    def __init__(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.run(["git", "init", "-b", "release_2.15.3_250515"])
        self.run(["git", "config", "user.email", "harness@example.test"])
        self.run(["git", "config", "user.name", "Harness Test"])
        (self.root / "app.txt").write_text("line one\nline two\n", encoding="utf-8")
        (self.root / "other.txt").write_text("other one\n", encoding="utf-8")
        self.run(["git", "add", "--", "app.txt", "other.txt"])
        self.run(["git", "commit", "-m", "initial"])

    def close(self) -> None:
        self._temp.cleanup()

    def run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def diff(self, *paths: str) -> str:
        command = ["git", "diff", "--binary", "--no-ext-diff"]
        if paths:
            command.extend(["--", *paths])
        return self.run(command).stdout


class DeliveryPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = GitRepositoryFixture()
        self.output_dir = self.repo.root / "delivery-output"

    def tearDown(self) -> None:
        self.repo.close()

    def request(self, *, expected_diff: str, allowed_paths: list[str] | None = None) -> DeliveryRequest:
        return DeliveryRequest(
            entity_kind="requirement",
            entity_id="DFHIS-31557",
            title="挂号处理界面证件类型需要默认成身份证。",
            url="https://devops.aliyun.com/projex/req/DFHIS-31557#",
            project_path=str(self.repo.root),
            expected_diff=expected_diff,
            allowed_paths=allowed_paths or ["app.txt"],
            output_dir=str(self.output_dir),
        )

    def test_exact_task_diff_is_classified_without_mutating_repository(self) -> None:
        before_head = self.repo.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        expected_diff = self.repo.diff("app.txt")

        snapshot = inspect_repository(self.request(expected_diff=expected_diff), DeliveryPolicy())

        self.assertEqual("task_owned_exact", snapshot["classification"])
        self.assertEqual("release_2.15.3_250515", snapshot["branch"])
        self.assertEqual(before_head, snapshot["head"])
        self.assertEqual(["app.txt"], snapshot["task_changed_paths"])
        self.assertEqual([], snapshot["unrelated_changed_paths"])
        self.assertEqual(expected_diff, self.repo.diff("app.txt"))
        self.assertEqual(before_head, self.repo.run(["git", "rev-parse", "HEAD"]).stdout.strip())

    def test_config_audit_has_clean_stderr_and_preserves_exact_classification(self) -> None:
        canonical = delivery_adapter._closure
        self.assertIsNotNone(canonical)
        query = canonical._git_config_query(
            self.repo.root,
            ["--includes", "--null", "--name-only", "--list"],
        )

        self.assertEqual(0, query.returncode)
        self.assertEqual("", query.stderr)
        with canonical._private_subprocess_environment() as environment:
            subprocess_tmpdir = Path(environment["TMPDIR"])
            self.assertEqual(
                {
                    "PATH",
                    "LANG",
                    "LC_ALL",
                    "TMPDIR",
                    "GIT_CONFIG_GLOBAL",
                    "GIT_CONFIG_SYSTEM",
                    "GIT_CONFIG_NOSYSTEM",
                    "GIT_TERMINAL_PROMPT",
                    "GIT_PAGER",
                    "GIT_ALLOW_PROTOCOL",
                },
                set(environment),
            )
            self.assertTrue(subprocess_tmpdir.is_dir())
            self.assertEqual(
                0o700,
                stat.S_IMODE(subprocess_tmpdir.stat().st_mode),
            )
        self.assertFalse(subprocess_tmpdir.exists())

        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        expected_diff = self.repo.diff("app.txt")
        snapshot = inspect_repository(
            self.request(expected_diff=expected_diff),
            DeliveryPolicy(),
        )

        self.assertEqual("task_owned_exact", snapshot["classification"])
        self.assertEqual([], snapshot["blockers"])

    def test_git_subprocesses_replace_hostile_parent_tmpdir_with_private_children(self) -> None:
        canonical = delivery_adapter._closure
        self.assertIsNotNone(canonical)
        hostile_directory = tempfile.TemporaryDirectory(
            prefix="harness-hostile-tmpdir-",
        )
        self.addCleanup(hostile_directory.cleanup)
        hostile_tmpdir = Path(hostile_directory.name)
        hostile_tmpdir.chmod(0o777)
        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        expected_diff = self.repo.diff("app.txt")
        observed_tmpdirs: list[Path] = []
        real_run = subprocess.run
        real_popen = subprocess.Popen

        def verify_outside_hostile_root(path: Path) -> None:
            resolved_path = path.resolve()
            self.assertNotEqual(hostile_tmpdir.resolve(), resolved_path)
            self.assertNotIn(hostile_tmpdir.resolve(), resolved_path.parents)

        def verify_environment(environment: dict[str, str]) -> None:
            child_tmpdir = Path(environment["TMPDIR"])
            observed_tmpdirs.append(child_tmpdir)
            verify_outside_hostile_root(child_tmpdir)
            self.assertTrue(child_tmpdir.is_dir())
            self.assertEqual(0o700, stat.S_IMODE(child_tmpdir.stat().st_mode))

        def recording_run(*args: object, **kwargs: object):
            verify_environment(kwargs["env"])
            environment = kwargs["env"]
            if "GIT_DIR" in environment:
                verify_outside_hostile_root(Path(environment["GIT_DIR"]))
            command = args[0] if args else kwargs.get("args")
            if (
                isinstance(command, (list, tuple))
                and "init" in command
                and command
            ):
                verify_outside_hostile_root(Path(str(command[-1])))
            return real_run(*args, **kwargs)

        def recording_popen(*args: object, **kwargs: object):
            verify_environment(kwargs["env"])
            return real_popen(*args, **kwargs)

        with (
            mock.patch.dict(os.environ, {"TMPDIR": str(hostile_tmpdir)}),
            mock.patch.object(canonical.tempfile, "tempdir", str(hostile_tmpdir)),
            mock.patch.object(canonical.subprocess, "run", side_effect=recording_run),
            mock.patch.object(canonical.subprocess, "Popen", side_effect=recording_popen),
        ):
            query = canonical._git_config_query(
                self.repo.root,
                ["--includes", "--null", "--name-only", "--list"],
            )
            snapshot = inspect_repository(
                self.request(expected_diff=expected_diff),
                DeliveryPolicy(),
            )

        self.assertEqual(0, query.returncode)
        self.assertEqual("task_owned_exact", snapshot["classification"])
        self.assertTrue(observed_tmpdirs)
        for child_tmpdir in observed_tmpdirs:
            self.assertFalse(child_tmpdir.exists())

    def test_unrelated_file_is_mixed_separable(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        expected_diff = self.repo.diff("app.txt")
        (self.repo.root / "other.txt").write_text("other changed\n", encoding="utf-8")

        snapshot = inspect_repository(self.request(expected_diff=expected_diff), DeliveryPolicy())

        self.assertEqual("mixed_separable", snapshot["classification"])
        self.assertEqual(["app.txt"], snapshot["task_changed_paths"])
        self.assertEqual(["other.txt"], snapshot["unrelated_changed_paths"])

    def test_same_file_drift_is_ambiguous_overlap(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        expected_diff = self.repo.diff("app.txt")
        (self.repo.root / "app.txt").write_text(
            "line one changed by someone else\nline two changed\n",
            encoding="utf-8",
        )

        snapshot = inspect_repository(self.request(expected_diff=expected_diff), DeliveryPolicy())

        self.assertEqual("ambiguous_overlap", snapshot["classification"])
        self.assertIn("task_patch_mismatch", snapshot["blockers"])

    def test_wrong_base_branch_is_blocked(self) -> None:
        self.repo.run(["git", "switch", "-c", "other-branch"])
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        expected_diff = self.repo.diff("app.txt")

        snapshot = inspect_repository(self.request(expected_diff=expected_diff), DeliveryPolicy())

        self.assertEqual("unsafe_repository_state", snapshot["classification"])
        self.assertIn("wrong_base_branch", snapshot["blockers"])

    def test_non_git_path_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = self.request(expected_diff="")
            request.project_path = temp_dir

            snapshot = inspect_repository(request, DeliveryPolicy())

        self.assertEqual("unsafe_repository_state", snapshot["classification"])
        self.assertIn("not_git_repository", snapshot["blockers"])

    def test_linked_worktree_is_blocked_for_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            linked_root = Path(temp_dir) / "linked"
            self.repo.run(
                [
                    "git",
                    "worktree",
                    "add",
                    "--force",
                    str(linked_root),
                    "release_2.15.3_250515",
                ]
            )
            try:
                (linked_root / "app.txt").write_text(
                    "line one\nline two changed\n",
                    encoding="utf-8",
                )
                expected_diff = subprocess.run(
                    ["git", "diff", "--binary", "--no-ext-diff", "--", "app.txt"],
                    cwd=linked_root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                ).stdout
                request = self.request(expected_diff=expected_diff)
                request.project_path = str(linked_root)

                snapshot = inspect_repository(request, DeliveryPolicy())
            finally:
                self.repo.run(
                    ["git", "worktree", "remove", "--force", str(linked_root)],
                    check=False,
                )

        self.assertEqual("unsafe_repository_state", snapshot["classification"])
        self.assertIn("delivery_project_linked_worktree", snapshot["blockers"])

    def test_unsafe_allowed_path_blocks_even_when_task_path_is_valid(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(
            expected_diff=self.repo.diff("app.txt"),
            allowed_paths=["app.txt", "../outside.txt"],
        )

        snapshot = inspect_repository(request, DeliveryPolicy())

        self.assertEqual("unsafe_repository_state", snapshot["classification"])
        self.assertIn("unsafe_allowed_path", snapshot["blockers"])

    def test_plan_hash_is_immutable_and_remote_actions_default_off(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        snapshot = inspect_repository(request, DeliveryPolicy())

        plan = build_delivery_plan(request, DeliveryPolicy(), snapshot)

        payload_without_hash = {key: value for key, value in plan.items() if key != "plan_hash"}
        self.assertEqual(stable_hash(payload_without_hash), plan["plan_hash"])
        self.assertFalse(plan["actions"]["push_feature"])
        self.assertFalse(plan["actions"]["push_integration"])
        self.assertFalse(plan["actions"]["yunxiao_comment"])
        self.assertEqual("feature-DFHIS-31557", plan["task_branch"])
        self.assertEqual(
            "feat: DFHIS-31557-https://devops.aliyun.com/projex/req/DFHIS-31557# 《挂号处理界面证件类型需要默认成身份证。》",
            plan["commit_message"],
        )
        changed = dict(plan)
        changed["actions"] = {**plan["actions"], "push_feature": True}
        self.assertNotEqual(plan["plan_hash"], stable_hash({key: value for key, value in changed.items() if key != "plan_hash"}))

    def test_plan_includes_full_delivery_actions_when_explicitly_requested(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.push_feature = True
        request.cherry_pick_integration = True
        request.push_integration = True
        snapshot = inspect_repository(request, DeliveryPolicy())

        plan = build_delivery_plan(request, DeliveryPolicy(), snapshot)

        self.assertTrue(plan["remote_actions_enabled"])
        self.assertTrue(plan["actions"]["push_feature"])
        self.assertTrue(plan["actions"]["cherry_pick_integration"])
        self.assertTrue(plan["actions"]["push_integration"])

    def test_plan_keeps_only_a_declared_structured_gitlab_write(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.gitlab_action = {
            "action": "merge_request.create",
            "parameters": {
                "host_alias": "company",
                "project_alias": "dfhis/guahao",
                "source_branch": "feature-DFHIS-31557",
                "target_branch": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号处理界面证件类型需要默认成身份证",
            },
        }

        plan = build_delivery_plan(request, DeliveryPolicy(), inspect_repository(request, DeliveryPolicy()))

        self.assertEqual(request.gitlab_action, plan["actions"]["gitlab_write"])
        self.assertTrue(plan["remote_actions_enabled"])

    def test_plan_keeps_only_a_declared_structured_github_write(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.github_action = {
            "action": "github.pull_request.create",
            "parameters": {
                "owner": "dfhis",
                "repository": "guahao",
                "head": "feature-DFHIS-31557",
                "base": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号默认身份证",
            },
        }

        plan = build_delivery_plan(request, DeliveryPolicy(), inspect_repository(request, DeliveryPolicy()))

        self.assertEqual(request.github_action, plan["actions"]["github_write"])
        self.assertTrue(plan["remote_actions_enabled"])

    def test_plan_derives_default_github_pull_request_from_origin(self) -> None:
        self.repo.run(
            ["git", "remote", "add", "origin", "https://github.com/dfhis/guahao.git"]
        )
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.create_github_pull_request = True

        plan = build_delivery_plan(
            request,
            DeliveryPolicy(),
            inspect_repository(request, DeliveryPolicy()),
        )

        self.assertEqual(
            {
                "action": "github.pull_request.create",
                "parameters": {
                    "owner": "dfhis",
                    "repository": "guahao",
                    "head": "feature-DFHIS-31557",
                    "base": "RC_2.16.1_250514",
                    "title": "DFHIS-31557 挂号处理界面证件类型需要默认成身份证。",
                },
            },
            plan["actions"]["github_write"],
        )
        self.assertTrue(plan["actions"]["push_feature"])

    def test_plan_rejects_gitlab_and_github_writes_in_the_same_delivery(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.gitlab_action = {
            "action": "merge_request.comment.write",
            "parameters": {
                "host_alias": "company",
                "project_alias": "dfhis/guahao",
                "merge_request_iid": 8,
                "body": "验证通过",
            },
        }
        request.github_action = {
            "action": "github.pull_request.comment.write",
            "parameters": {
                "owner": "dfhis",
                "repository": "guahao",
                "pull_request_number": 8,
                "body": "验证通过",
            },
        }

        with self.assertRaises(DeliveryError) as raised:
            build_delivery_plan(request, DeliveryPolicy(), inspect_repository(request, DeliveryPolicy()))

        self.assertEqual("multiple_hosting_writes_not_allowed", raised.exception.code)

    def test_plan_derives_default_merge_request_target_from_origin(self) -> None:
        self.repo.run(
            [
                "git", "remote", "add", "origin",
                "https://gitlab.example.test/dfhis/guahao.git",
            ]
        )
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.create_gitlab_merge_request = True

        plan = build_delivery_plan(
            request,
            DeliveryPolicy(),
            inspect_repository(request, DeliveryPolicy()),
        )

        self.assertEqual(
            {
                "action": "merge_request.create",
                "parameters": {
                    "host_alias": "gitlab-example-test",
                    "gitlab_host": "gitlab.example.test",
                    "project_alias": "dfhis/guahao",
                    "source_branch": "feature-DFHIS-31557",
                    "target_branch": "RC_2.16.1_250514",
                    "title": "DFHIS-31557 挂号处理界面证件类型需要默认成身份证。",
                },
            },
            plan["actions"]["gitlab_write"],
        )

    def test_origin_derived_merge_request_always_pushes_its_source_branch(self) -> None:
        self.repo.run(
            [
                "git", "remote", "add", "origin",
                "https://gitlab.example.test/dfhis/guahao.git",
            ]
        )
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.create_gitlab_merge_request = True

        plan = build_delivery_plan(
            request,
            DeliveryPolicy(),
            inspect_repository(request, DeliveryPolicy()),
        )

        self.assertTrue(plan["actions"]["push_feature"])

    def test_plan_contains_patch_and_file_state_hashes_without_embedding_patch(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        expected_diff = self.repo.diff("app.txt")
        request = self.request(expected_diff=expected_diff)
        snapshot = inspect_repository(request, DeliveryPolicy())

        plan = build_delivery_plan(request, DeliveryPolicy(), snapshot)
        serialized = json.dumps(plan, ensure_ascii=False)

        self.assertEqual(hashlib.sha256(expected_diff.encode("utf-8")).hexdigest(), plan["task_patch_hash"])
        self.assertRegex(plan["task_file_state_hash"], r"^[0-9a-f]{64}$")
        self.assertNotIn("line two changed", serialized)

    def test_plan_rejects_external_write_hidden_in_verification_command(self) -> None:
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.verify_commands = ["npm test && git push origin HEAD"]
        snapshot = inspect_repository(request, DeliveryPolicy())

        with self.assertRaisesRegex(DeliveryError, "验证命令包含外部写入"):
            build_delivery_plan(request, DeliveryPolicy(), snapshot)

    def test_plan_markdown_displays_every_verification_command(self) -> None:
        from app.delivery_closure import delivery_plan_to_markdown

        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.verify_commands = ["npm run lint", "node tests/task-check.js"]
        snapshot = inspect_repository(request, DeliveryPolicy())

        markdown = delivery_plan_to_markdown(build_delivery_plan(request, DeliveryPolicy(), snapshot))

        self.assertIn("## 专项验证命令", markdown)
        self.assertIn("`npm run lint`", markdown)
        self.assertIn("`node tests/task-check.js`", markdown)

    def test_plan_markdown_displays_declared_github_write(self) -> None:
        from app.delivery_closure import delivery_plan_to_markdown

        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        request = self.request(expected_diff=self.repo.diff("app.txt"))
        request.github_action = {
            "action": "github.pull_request.create",
            "parameters": {
                "owner": "acme",
                "repository": "his",
                "head": "feature-DFHIS-1",
                "base": "main",
                "title": "DFHIS-1",
            },
        }
        snapshot = inspect_repository(request, DeliveryPolicy())

        markdown = delivery_plan_to_markdown(build_delivery_plan(request, DeliveryPolicy(), snapshot))

        self.assertIn("GitHub 写入：github.pull_request.create", markdown)

    def test_delivery_policy_loads_git_conventions_from_rule_pack(self) -> None:
        policy = DeliveryPolicy.from_rule_pack()

        self.assertEqual("release_2.15.3_250515", policy.base_branch)
        self.assertEqual("RC_2.16.1_250514", policy.integration_branch)
        self.assertEqual(
            "feature-DFHIS-31557",
            policy.task_branch(entity_kind="requirement", entity_id="DFHIS-31557"),
        )
        self.assertEqual(
            "feat: DFHIS-31557-url 《title》",
            policy.commit_message(
                entity_kind="requirement",
                entity_id="DFHIS-31557",
                url="url",
                title="title",
            ),
        )
        self.assertFalse(policy.push_feature_default)
        self.assertFalse(policy.push_integration_default)


class DeliveryPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = GitRepositoryFixture()
        self.state_temp = tempfile.TemporaryDirectory()
        self.state_root = Path(self.state_temp.name)
        self.store = delivery_adapter._store.SQLiteDeliveryStore(
            self.state_root / "delivery.sqlite"
        )
        self.closure = DeliveryClosure(store=self.store)
        (self.repo.root / "app.txt").write_text("line one\nline two changed\n", encoding="utf-8")
        self.request = DeliveryRequest(
            entity_kind="requirement",
            entity_id="DFHIS-31557",
            title="挂号处理界面证件类型需要默认成身份证。",
            url="https://devops.aliyun.com/projex/req/DFHIS-31557#",
            project_path=str(self.repo.root),
            expected_diff=self.repo.diff("app.txt"),
            allowed_paths=["app.txt"],
            output_dir=str(self.state_root / "output"),
        )

    def tearDown(self) -> None:
        self.state_temp.cleanup()
        self.repo.close()

    def test_prepare_persists_plan_journal_and_database_without_worktree_changes(self) -> None:
        before_status = self.repo.run(["git", "status", "--porcelain=v1"]).stdout

        result = self.closure.prepare(self.request)

        transaction = result["transaction"]
        self.assertEqual("waiting_release_runtime_acceptance", transaction["state"])
        self.assertEqual(result["plan"]["plan_hash"], transaction["plan_hash"])
        journal_path = Path(transaction["journal_path"])
        self.assertTrue(journal_path.is_file())
        self.assertTrue((journal_path.parent / "delivery_plan.json").is_file())
        self.assertTrue((journal_path.parent / "repository_snapshot.json").is_file())
        self.assertTrue((journal_path.parent / "expected_task.diff").is_file())
        self.assertEqual(before_status, self.repo.run(["git", "status", "--porcelain=v1"]).stdout)
        events = self.store.get_events(int(transaction["id"]))
        self.assertEqual(["planned"], [item["event_type"] for item in events])

    def test_prepare_is_idempotent_for_the_same_repository_state_and_plan(self) -> None:
        first = self.closure.prepare(self.request)
        second = self.closure.prepare(self.request)

        self.assertEqual(first["transaction"]["id"], second["transaction"]["id"])
        self.assertEqual(first["plan"]["plan_hash"], second["plan"]["plan_hash"])
        self.assertEqual(1, len(self.store.get_events(int(first["transaction"]["id"]))))

    def test_prepare_records_explicit_full_delivery_actions_in_its_immutable_plan(self) -> None:
        first = self.closure.prepare(self.request)
        self.request.push_feature = True
        self.request.cherry_pick_integration = True
        self.request.push_integration = True
        second = self.closure.prepare(self.request)

        self.assertNotEqual(first["transaction"]["id"], second["transaction"]["id"])
        self.assertFalse(second["idempotent"])
        self.assertFalse(first["plan"]["remote_actions_enabled"])
        self.assertTrue(second["plan"]["remote_actions_enabled"])
        for action in ("push_feature", "cherry_pick_integration", "push_integration"):
            self.assertFalse(first["plan"]["actions"][action])
            self.assertTrue(second["plan"]["actions"][action])

    def test_tampered_plan_file_is_rejected(self) -> None:
        prepared = self.closure.prepare(self.request)
        journal = json.loads(
            Path(prepared["transaction"]["journal_path"]).read_text(encoding="utf-8")
        )
        plan_path = Path(journal["plan_path"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["task_branch"] = "feature-DFHIS-TAMPERED"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

        with self.assertRaises(DeliveryError) as raised:
            self.closure.show(int(prepared["transaction"]["id"]))
        self.assertEqual("delivery_plan_hash_mismatch", raised.exception.code)


class DeliveryRuntimeAcceptanceTests(DeliveryPersistenceTests):
    def test_release_acceptance_binds_branch_head_patch_and_file_state(self) -> None:
        prepared = self.closure.prepare(self.request)
        transaction_id = int(prepared["transaction"]["id"])

        acceptance = self.closure.record_runtime_acceptance(
            transaction_id,
            phase="release",
            status="passed",
            summary="用户已验证身份证默认值和其他档案默认值一致。",
            verifier="user",
        )

        self.assertEqual("passed", acceptance["status"])
        self.assertEqual("release", acceptance["phase"])
        self.assertEqual("release_2.15.3_250515", acceptance["branch"])
        self.assertRegex(acceptance["head"], r"^[0-9a-f]{40}$")
        self.assertRegex(acceptance["task_patch_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(acceptance["task_file_state_hash"], r"^[0-9a-f]{64}$")
        transaction = self.store.get_transaction(transaction_id)
        self.assertEqual("release_runtime_accepted", transaction["state"])
        self.assertEqual(acceptance, transaction["release_acceptance"])

    def test_release_acceptance_requires_summary(self) -> None:
        prepared = self.closure.prepare(self.request)

        with self.assertRaises(DeliveryError) as raised:
            self.closure.record_runtime_acceptance(
                int(prepared["transaction"]["id"]),
                phase="release",
                status="passed",
                summary="",
            )
        self.assertEqual("invalid_acceptance", raised.exception.code)

    def test_release_acceptance_rejects_patch_drift(self) -> None:
        prepared = self.closure.prepare(self.request)
        (self.repo.root / "app.txt").write_text(
            "line one changed after plan\nline two changed\n",
            encoding="utf-8",
        )

        with self.assertRaises(DeliveryError) as raised:
            self.closure.record_runtime_acceptance(
                int(prepared["transaction"]["id"]),
                phase="release",
                status="passed",
                summary="不应被登记",
            )
        self.assertEqual("release_acceptance_drift", raised.exception.code)

    def test_release_acceptance_rejects_wrong_branch(self) -> None:
        prepared = self.closure.prepare(self.request)
        self.repo.run(["git", "switch", "-c", "other-branch"])

        with self.assertRaises(DeliveryError) as raised:
            self.closure.record_runtime_acceptance(
                int(prepared["transaction"]["id"]),
                phase="release",
                status="passed",
                summary="不应被登记",
            )
        self.assertEqual("release_acceptance_drift", raised.exception.code)

    def test_recorded_acceptance_becomes_invalid_after_file_drift(self) -> None:
        prepared = self.closure.prepare(self.request)
        transaction_id = int(prepared["transaction"]["id"])
        self.closure.record_runtime_acceptance(
            transaction_id,
            phase="release",
            status="passed",
            summary="验收通过",
        )
        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed again\n",
            encoding="utf-8",
        )

        validation = self.closure.validate_runtime_acceptance(transaction_id, phase="release")

        self.assertFalse(validation["valid"])
        self.assertIn("task_patch_drift", validation["reasons"])

    def test_validate_runtime_acceptance_rejects_unknown_phase(self) -> None:
        prepared = self.closure.prepare(self.request)

        with self.assertRaises(DeliveryError) as raised:
            self.closure.validate_runtime_acceptance(
                int(prepared["transaction"]["id"]),
                phase="unknown",
            )
        self.assertEqual("invalid_acceptance_phase", raised.exception.code)


class DeliveryStageOneTests(DeliveryPersistenceTests):
    def accept_release(self) -> dict:
        prepared = self.closure.prepare(self.request)
        self.closure.record_runtime_acceptance(
            int(prepared["transaction"]["id"]),
            phase="release",
            status="passed",
            summary="release 页面验证通过",
        )
        return prepared

    def test_exact_task_diff_creates_task_branch_and_exact_commit(self) -> None:
        prepared = self.accept_release()
        transaction_id = int(prepared["transaction"]["id"])
        base_head = self.repo.run(["git", "rev-parse", "HEAD"]).stdout.strip()

        result = self.closure.execute_stage_one(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
        )

        self.assertEqual("task_commit_created", result["state"])
        self.assertTrue(result["remote_actions_blocked"])
        self.assertEqual("release_2.15.3_250515", self.repo.run(["git", "branch", "--show-current"]).stdout.strip())
        self.assertEqual(base_head, self.repo.run(["git", "rev-parse", "release_2.15.3_250515"]).stdout.strip())
        self.assertEqual(base_head, self.repo.run(["git", "rev-parse", "HEAD"]).stdout.strip())
        commit = self.repo.run(["git", "rev-parse", "feature-DFHIS-31557"]).stdout.strip()
        self.assertEqual(commit, result["commit"]["commit"])
        self.assertEqual(base_head, self.repo.run(["git", "rev-parse", f"{commit}^"]).stdout.strip())
        self.assertEqual(
            prepared["plan"]["commit_message"],
            self.repo.run(["git", "log", "-1", "--pretty=%B", commit]).stdout.strip(),
        )
        self.assertEqual(" M app.txt\n", self.repo.run(["git", "status", "--porcelain=v1"]).stdout)
        committed_diff = self.repo.run(
            ["git", "diff", "--binary", "--no-ext-diff", f"{commit}^", commit, "--", "app.txt"]
        ).stdout
        self.assertEqual(self.request.expected_diff, committed_diff)

    def test_stage_one_requires_current_release_acceptance_and_plan_hash(self) -> None:
        prepared = self.closure.prepare(self.request)

        with self.assertRaises(DeliveryError) as raised:
            self.closure.execute_stage_one(
                int(prepared["transaction"]["id"]),
                approved_plan_hash=prepared["plan"]["plan_hash"],
            )
        self.assertEqual("release_acceptance_invalid", raised.exception.code)
        self.closure.record_runtime_acceptance(
            int(prepared["transaction"]["id"]),
            phase="release",
            status="passed",
            summary="release 页面验证通过",
        )
        with self.assertRaises(DeliveryError) as raised:
            self.closure.execute_stage_one(
                int(prepared["transaction"]["id"]),
                approved_plan_hash="wrong-plan-hash",
            )
        self.assertEqual("plan_hash_not_approved", raised.exception.code)
        self.assertEqual(
            "release_2.15.3_250515",
            self.repo.run(["git", "branch", "--show-current"]).stdout.strip(),
        )

    def test_verification_failure_restores_original_release_workspace(self) -> None:
        self.request.verify_commands = ["python3 -c \"raise SystemExit(7)\""]
        prepared = self.accept_release()
        before_status = self.repo.run(["git", "status", "--porcelain=v1", "-z"]).stdout
        before_diff = self.repo.diff("app.txt")

        with self.assertRaises(DeliveryError) as raised:
            self.closure.execute_stage_one(
                int(prepared["transaction"]["id"]),
                approved_plan_hash=prepared["plan"]["plan_hash"],
            )
        self.assertEqual("verification_unavailable", raised.exception.code)

        self.assertEqual(
            "release_2.15.3_250515",
            self.repo.run(["git", "branch", "--show-current"]).stdout.strip(),
        )
        self.assertEqual(before_status, self.repo.run(["git", "status", "--porcelain=v1", "-z"]).stdout)
        self.assertEqual(before_diff, self.repo.diff("app.txt"))
        self.assertFalse(
            self.repo.run(["git", "show-ref", "--verify", "--quiet", "refs/heads/feature-DFHIS-31557"], check=False).returncode
            == 0
        )

    def test_verification_side_effect_outside_allowlist_is_removed_during_recovery(self) -> None:
        self.request.verify_commands = ["printf 'generated\\n' > unexpected.tmp"]
        prepared = self.accept_release()
        before_diff = self.repo.diff("app.txt")

        with self.assertRaises(DeliveryError) as raised:
            self.closure.execute_stage_one(
                int(prepared["transaction"]["id"]),
                approved_plan_hash=prepared["plan"]["plan_hash"],
            )
        self.assertEqual("verification_unavailable", raised.exception.code)

        self.assertEqual(
            "release_2.15.3_250515",
            self.repo.run(["git", "branch", "--show-current"]).stdout.strip(),
        )
        self.assertEqual(before_diff, self.repo.diff("app.txt"))
        self.assertFalse((self.repo.root / "unexpected.tmp").exists())

    def test_stage_one_uses_persisted_policy_snapshot_after_runtime_policy_changes(self) -> None:
        prepared = self.accept_release()
        self.closure.policy = DeliveryPolicy(
            base_branch="different-release",
            integration_branch="different-rc",
        )

        result = self.closure.execute_stage_one(
            int(prepared["transaction"]["id"]),
            approved_plan_hash=prepared["plan"]["plan_hash"],
        )

        self.assertEqual("task_commit_created", result["state"])
        self.assertEqual(
            "release_2.15.3_250515",
            self.repo.run(["git", "branch", "--show-current"]).stdout.strip(),
        )

    def test_mixed_changes_and_index_identity_are_preserved(self) -> None:
        (self.repo.root / "other.txt").write_text("other staged\n", encoding="utf-8")
        self.repo.run(["git", "add", "--", "other.txt"])
        (self.repo.root / "other.txt").write_text("other unstaged\n", encoding="utf-8")
        (self.repo.root / "notes.tmp").write_text("local notes\n", encoding="utf-8")
        self.request.expected_diff = self.repo.diff("app.txt")
        prepared = self.accept_release()
        before_other = (self.repo.root / "other.txt").read_text(encoding="utf-8")
        before_index = self.repo.run(["git", "show", ":other.txt"]).stdout
        before_status = self.repo.run(["git", "status", "--porcelain=v1", "-z"]).stdout

        result = self.closure.execute_stage_one(
            int(prepared["transaction"]["id"]),
            approved_plan_hash=prepared["plan"]["plan_hash"],
        )

        self.assertEqual("task_commit_created", result["state"])
        self.assertEqual(
            "release_2.15.3_250515",
            self.repo.run(["git", "branch", "--show-current"]).stdout.strip(),
        )
        self.assertEqual(before_other, (self.repo.root / "other.txt").read_text(encoding="utf-8"))
        self.assertEqual(before_index, self.repo.run(["git", "show", ":other.txt"]).stdout)
        self.assertEqual("local notes\n", (self.repo.root / "notes.tmp").read_text(encoding="utf-8"))
        after_status = self.repo.run(["git", "status", "--porcelain=v1", "-z"]).stdout
        self.assertIn("app.txt", after_status)
        self.assertEqual(
            sorted(item for item in before_status.split("\0") if item),
            sorted(item for item in after_status.split("\0") if item),
        )
        commit_paths = self.repo.run(
            ["git", "show", "--pretty=format:", "--name-only", "feature-DFHIS-31557"]
        ).stdout.split()
        self.assertEqual(["app.txt"], commit_paths)

    def test_task_commit_created_stage_is_idempotent(self) -> None:
        prepared = self.accept_release()
        transaction_id = int(prepared["transaction"]["id"])
        first = self.closure.execute_stage_one(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
        )
        first_event_count = len(self.store.get_events(transaction_id))

        second = self.closure.execute_stage_one(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
        )

        self.assertEqual(first["commit"], second["commit"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first_event_count, len(self.store.get_events(transaction_id)))

    def test_stage_one_rejects_retry_from_removed_rc_integration_state(self) -> None:
        prepared = self.accept_release()
        transaction_id = int(prepared["transaction"]["id"])
        first = self.closure.execute_stage_one(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
        )
        self.store.update_transaction(
            transaction_id,
            state="rc_integration_failed",
            last_error="RC 一致性审计失败",
        )
        self.repo.run(["git", "switch", "release_2.15.3_250515"])
        first_event_count = len(self.store.get_events(transaction_id))

        with self.assertRaises(DeliveryError) as raised:
            self.closure.execute_stage_one(
                transaction_id,
                approved_plan_hash=prepared["plan"]["plan_hash"],
            )

        self.assertEqual("idempotent_state_incompatible", raised.exception.code)
        self.assertEqual(first_event_count, len(self.store.get_events(transaction_id)))
        self.assertEqual(
            first["commit"]["commit"],
            self.repo.run(["git", "rev-parse", "feature-DFHIS-31557"]).stdout.strip(),
        )
        self.assertEqual(
            "release_2.15.3_250515",
            self.repo.run(["git", "branch", "--show-current"]).stdout.strip(),
        )

class DeliveryParityAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = GitRepositoryFixture()

    def tearDown(self) -> None:
        self.repo.close()

    def git_snapshot(self) -> dict[str, str]:
        return {
            "head": self.repo.run(["git", "rev-parse", "HEAD"]).stdout,
            "branch": self.repo.run(["git", "branch", "--show-current"]).stdout,
            "status": self.repo.run(["git", "status", "--porcelain=v1", "-z"]).stdout,
            "refs": self.repo.run(["git", "show-ref", "--head"]).stdout,
        }

    def test_public_parity_audit_is_fail_closed_without_git_changes(self) -> None:
        before = self.git_snapshot()

        with self.assertRaises(DeliveryError) as raised:
            audit_cherry_pick_parity(
                project_path=self.repo.root,
                commits=["1" * 40],
                rc_pre_head="2" * 40,
                rc_post_head="3" * 40,
                allowed_paths=["app.txt"],
            )

        self.assertEqual("git_remote_delivery_disabled", raised.exception.code)
        self.assertEqual(before, self.git_snapshot())


class DeliveryV1LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = GitRepositoryFixture()
        self.state_temp = tempfile.TemporaryDirectory()
        self.state_root = Path(self.state_temp.name)
        self.store = delivery_adapter._store.SQLiteDeliveryStore(
            self.state_root / "delivery.sqlite"
        )
        self.closure = DeliveryClosure(store=self.store)
        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        self.request = DeliveryRequest(
            entity_kind="requirement",
            entity_id="DFHIS-31557",
            title="挂号处理界面证件类型需要默认成身份证。",
            url="https://devops.aliyun.com/projex/req/DFHIS-31557#",
            project_path=str(self.repo.root),
            expected_diff=self.repo.diff("app.txt"),
            allowed_paths=["app.txt"],
            output_dir=str(self.state_root / "output"),
            push_feature=True,
            cherry_pick_integration=True,
            push_integration=True,
        )

    def tearDown(self) -> None:
        self.state_temp.cleanup()
        self.repo.close()

    def git_snapshot(self) -> dict[str, str]:
        return {
            "head": self.repo.run(["git", "rev-parse", "HEAD"]).stdout,
            "branch": self.repo.run(["git", "branch", "--show-current"]).stdout,
            "status": self.repo.run(["git", "status", "--porcelain=v1", "-z"]).stdout,
            "refs": self.repo.run(["git", "show-ref", "--head"]).stdout,
            "remote_refs": self.repo.run(
                ["git", "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes"]
            ).stdout,
        }

    def prepare_stage_one(self) -> tuple[dict, dict]:
        prepared = self.closure.prepare(self.request)
        transaction_id = int(prepared["transaction"]["id"])
        self.closure.record_runtime_acceptance(
            transaction_id,
            phase="release",
            status="passed",
            summary="release 页面验证通过",
        )
        stage_one = self.closure.execute_stage_one(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
        )
        return prepared, stage_one

    def valid_rc_evidence(self, prepared: dict, stage_one: dict) -> dict:
        commit = stage_one["commit"]["commit"]
        integration_branch = prepared["plan"]["integration_branch"]
        self.repo.run(
            ["git", "branch", integration_branch, commit]
        )
        self.repo.run(["git", "switch", "--merge", integration_branch])
        head = self.repo.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        return {
            "integration_head": head,
            "parity": {
                "schema_version": "1.0-rc-parity",
                "rc_post_head": head,
                "task_commit": commit,
                "task_patch_hash": prepared["plan"]["task_patch_hash"],
                "changed_paths": ["app.txt"],
            },
        }

    def test_checkpoint_rejects_malformed_input_without_git_changes(self) -> None:
        prepared = self.closure.prepare(self.request)
        before = self.git_snapshot()

        with self.assertRaises(DeliveryError) as raised:
            self.closure.record_rc_integration_checkpoint(
                int(prepared["transaction"]["id"]),
                evidence={"integration_head": "untrusted"},
            )

        self.assertEqual("invalid_rc_checkpoint", raised.exception.code)
        self.assertEqual(before, self.git_snapshot())
        self.assertEqual(
            "waiting_release_runtime_acceptance",
            self.store.get_transaction(int(prepared["transaction"]["id"]))["state"],
        )

    def test_checkpoint_requires_audited_task_commit_without_git_changes(self) -> None:
        prepared = self.closure.prepare(self.request)
        before = self.git_snapshot()
        head = before["head"].strip()
        evidence = {
            "integration_head": head,
            "parity": {
                "schema_version": "1.0-rc-parity",
                "rc_post_head": head,
                "task_commit": head,
                "task_patch_hash": prepared["plan"]["task_patch_hash"],
                "changed_paths": ["app.txt"],
            },
        }

        with self.assertRaises(DeliveryError) as raised:
            self.closure.record_rc_integration_checkpoint(
                int(prepared["transaction"]["id"]),
                evidence=evidence,
            )

        self.assertEqual("rc_checkpoint_not_ready", raised.exception.code)
        self.assertEqual(before, self.git_snapshot())

    def test_checkpoint_rejects_unbound_rc_head_without_git_changes(self) -> None:
        prepared, stage_one = self.prepare_stage_one()
        transaction_id = int(prepared["transaction"]["id"])
        before = self.git_snapshot()
        evidence = {
            "integration_head": before["head"].strip(),
            "parity": {
                "schema_version": "1.0-rc-parity",
                "rc_post_head": before["head"].strip(),
                "task_commit": stage_one["commit"]["commit"],
                "task_patch_hash": prepared["plan"]["task_patch_hash"],
                "changed_paths": ["app.txt"],
            },
        }

        with self.assertRaises(DeliveryError) as raised:
            self.closure.record_rc_integration_checkpoint(
                transaction_id,
                evidence=evidence,
            )

        self.assertEqual("rc_checkpoint_invalid", raised.exception.code)
        self.assertEqual(before, self.git_snapshot())
        self.assertEqual("task_commit_created", self.store.get_transaction(transaction_id)["state"])

    def test_rc_acceptance_requires_verified_checkpoint_without_git_changes(self) -> None:
        prepared, _stage_one = self.prepare_stage_one()
        transaction_id = int(prepared["transaction"]["id"])
        before = self.git_snapshot()

        with self.assertRaises(DeliveryError) as raised:
            self.closure.record_runtime_acceptance(
                transaction_id,
                phase="rc",
                status="passed",
                summary="不应登记",
            )

        self.assertEqual("rc_acceptance_not_ready", raised.exception.code)
        self.assertEqual(before, self.git_snapshot())
        self.assertEqual("task_commit_created", self.store.get_transaction(transaction_id)["state"])

    def test_explicit_plan_pushes_task_and_builds_rc_before_rc_acceptance(self) -> None:
        self.repo.run(["git", "branch", "RC_2.16.1_250514"])
        self.repo.run(
            [
                "git", "remote", "add", "origin",
                "https://gitlab.example.test/dfhis/guahao.git",
            ]
        )
        prepared, stage_one = self.prepare_stage_one()
        transaction_id = int(prepared["transaction"]["id"])
        task_commit = stage_one["commit"]["commit"]
        rc_pre_head = self.repo.run(
            ["git", "rev-parse", "refs/heads/RC_2.16.1_250514"]
        ).stdout.strip()
        canonical = delivery_adapter._closure
        self.assertIsNotNone(canonical)
        remote_results = iter(
            (
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, f"{task_commit}\trefs/heads/feature-DFHIS-31557\n", ""),
                subprocess.CompletedProcess([], 0, f"{rc_pre_head}\trefs/heads/RC_2.16.1_250514\n", ""),
            )
        )

        with mock.patch.object(canonical, "_remote_git", side_effect=lambda *_args, **_kwargs: next(remote_results)) as remote_git:
            outcome = self.closure.execute_pre_rc_remote_phase(
                transaction_id,
                approved_plan_hash=prepared["plan"]["plan_hash"],
            )

        self.assertEqual("waiting_rc_runtime_acceptance", outcome["state"])
        self.assertTrue(outcome["task_push"]["pushed"])
        self.assertEqual(task_commit, outcome["integration"]["task_commit"])
        integration_head = self.repo.run(
            ["git", "rev-parse", "refs/heads/RC_2.16.1_250514"]
        ).stdout.strip()
        self.assertNotEqual(rc_pre_head, integration_head)
        self.assertEqual(
            integration_head,
            self.store.get_transaction(transaction_id)["parity_result"]["integration_head"],
        )
        self.assertEqual(4, remote_git.call_count)

    def test_rc_acceptance_finishes_declared_rc_push_without_another_confirmation(self) -> None:
        self.repo.run(["git", "branch", "RC_2.16.1_250514"])
        self.repo.run(
            [
                "git", "remote", "add", "origin",
                "https://gitlab.example.test/dfhis/guahao.git",
            ]
        )
        prepared, stage_one = self.prepare_stage_one()
        transaction_id = int(prepared["transaction"]["id"])
        task_commit = stage_one["commit"]["commit"]
        rc_pre_head = self.repo.run(
            ["git", "rev-parse", "refs/heads/RC_2.16.1_250514"]
        ).stdout.strip()
        canonical = delivery_adapter._closure
        self.assertIsNotNone(canonical)
        pre_rc_results = iter(
            (
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, f"{task_commit}\trefs/heads/feature-DFHIS-31557\n", ""),
                subprocess.CompletedProcess([], 0, f"{rc_pre_head}\trefs/heads/RC_2.16.1_250514\n", ""),
            )
        )
        with mock.patch.object(canonical, "_remote_git", side_effect=lambda *_args, **_kwargs: next(pre_rc_results)):
            self.closure.execute_pre_rc_remote_phase(
                transaction_id,
                approved_plan_hash=prepared["plan"]["plan_hash"],
            )
        self.repo.run(["git", "switch", "--merge", "RC_2.16.1_250514"])
        self.closure.record_runtime_acceptance(
            transaction_id,
            phase="rc",
            status="passed",
            summary="RC 页面验证通过",
        )
        rc_post_head = self.repo.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        post_rc_results = iter(
            (
                subprocess.CompletedProcess([], 0, f"{rc_pre_head}\trefs/heads/RC_2.16.1_250514\n", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, f"{rc_post_head}\trefs/heads/RC_2.16.1_250514\n", ""),
            )
        )

        with mock.patch.object(canonical, "_remote_git", side_effect=lambda *_args, **_kwargs: next(post_rc_results)) as remote_git:
            outcome = self.closure.execute_stage_two(
                transaction_id,
                approved_plan_hash=prepared["plan"]["plan_hash"],
            )

        self.assertEqual("completed", outcome["state"])
        self.assertTrue(outcome["rc_push"]["pushed"])
        self.assertEqual(3, remote_git.call_count)
        transaction = self.store.get_transaction(transaction_id)
        self.assertEqual("completed", transaction["state"])
        self.assertIn("rc_push", [item["action"] for item in transaction["remote_results"]])

    def test_declared_gitlab_action_closes_only_with_a_verified_receipt(self) -> None:
        self.request.gitlab_action = {
            "action": "merge_request.create",
            "parameters": {
                "host_alias": "company",
                "project_alias": "dfhis/guahao",
                "source_branch": "feature-DFHIS-31557",
                "target_branch": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号默认身份证",
            },
        }
        prepared = self.closure.prepare(self.request)
        transaction_id = int(prepared["transaction"]["id"])
        self.store.update_transaction(transaction_id, state="gitlab_delivery_pending")

        with self.assertRaises(DeliveryError) as raised:
            self.closure.complete_declared_gitlab_action(
                transaction_id,
                approved_plan_hash=prepared["plan"]["plan_hash"],
                receipt={
                    "action": "merge_request.create",
                    "status": "success",
                    "write_effect_status": "verified_applied",
                    "target_alias": "gl-h7-company-g5-other-p7-project-m42",
                },
            )

        self.assertEqual("gitlab_write_unverified", raised.exception.code)
        self.assertEqual("gitlab_delivery_pending", self.store.get_transaction(transaction_id)["state"])

        result = self.closure.complete_declared_gitlab_action(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
            receipt={
                "action": "merge_request.create",
                "status": "success",
                "write_effect_status": "verified_applied",
                "target_alias": "gl-h7-company-g5-dfhis-p6-guahao-m42",
            },
        )

        self.assertEqual("completed", result["state"])
        self.assertEqual("completed", self.store.get_transaction(transaction_id)["state"])

    def test_declared_github_action_closes_only_with_exact_verified_readback(self) -> None:
        self.request.github_action = {
            "action": "github.pull_request.create",
            "parameters": {
                "owner": "dfhis",
                "repository": "guahao",
                "head": "feature-DFHIS-31557",
                "base": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号默认身份证",
            },
        }
        prepared = self.closure.prepare(self.request)
        transaction_id = int(prepared["transaction"]["id"])
        self.store.update_transaction(transaction_id, state="github_delivery_pending")

        with self.assertRaises(DeliveryError) as raised:
            self.closure.complete_declared_github_action(
                transaction_id,
                approved_plan_hash=prepared["plan"]["plan_hash"],
                receipt={
                    "action": "github.pull_request.create",
                    "status": "success",
                    "write_effect_status": "verified_applied",
                    "target_alias": "gh-o5-other-r7-project-p42",
                },
            )

        self.assertEqual("github_write_unverified", raised.exception.code)
        self.assertEqual("github_delivery_pending", self.store.get_transaction(transaction_id)["state"])

        result = self.closure.complete_declared_github_action(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
            receipt={
                "action": "github.pull_request.create",
                "status": "success",
                "write_effect_status": "verified_applied",
                "target_alias": "gh-o5-dfhis-r6-guahao-p42",
            },
        )

        self.assertEqual("completed", result["state"])
        self.assertEqual("completed", self.store.get_transaction(transaction_id)["state"])

    def test_stage_two_executes_only_the_declared_github_action(self) -> None:
        self.request.push_feature = False
        self.request.cherry_pick_integration = False
        self.request.push_integration = False
        self.request.github_action = {
            "action": "github.pull_request.comment.write",
            "parameters": {
                "owner": "dfhis",
                "repository": "guahao",
                "pull_request_number": 42,
                "body": "验证通过",
            },
        }
        prepared = self.closure.prepare(self.request)
        transaction_id = int(prepared["transaction"]["id"])
        self.store.update_transaction(transaction_id, state="rc_runtime_accepted")
        calls: list[dict] = []

        def execute_github_action(**kwargs: object) -> dict:
            calls.append(dict(kwargs))
            return {
                "action": "github.pull_request.comment.write",
                "status": "success",
                "write_effect_status": "verified_applied",
                "target_alias": "gh-o5-dfhis-r6-guahao-p42",
            }

        result = self.closure.execute_stage_two(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
            execute_github_action=execute_github_action,
        )

        self.assertEqual("completed", result["state"])
        self.assertEqual(1, len(calls))
        self.assertEqual(self.request.github_action, calls[0]["github_action"])

    def test_rc_acceptance_automatically_executes_the_declared_gitlab_action(self) -> None:
        self.request.push_feature = False
        self.request.cherry_pick_integration = False
        self.request.push_integration = False
        self.request.gitlab_action = {
            "action": "merge_request.create",
            "parameters": {
                "host_alias": "company",
                "project_alias": "dfhis/guahao",
                "source_branch": "feature-DFHIS-31557",
                "target_branch": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号默认身份证",
            },
        }
        prepared = self.closure.prepare(self.request)
        transaction_id = int(prepared["transaction"]["id"])
        self.store.update_transaction(transaction_id, state="rc_runtime_accepted")
        calls: list[dict] = []

        def execute_gitlab_action(**kwargs: object) -> dict:
            calls.append(dict(kwargs))
            return {
                "action": "merge_request.create",
                "status": "success",
                "write_effect_status": "verified_applied",
                "target_alias": "gl-h7-company-g5-dfhis-p6-guahao-m42",
            }

        result = self.closure.execute_stage_two(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
            execute_gitlab_action=execute_gitlab_action,
        )

        self.assertEqual("completed", result["state"])
        self.assertEqual(1, len(calls))
        self.assertEqual(transaction_id, calls[0]["transaction_id"])
        self.assertEqual(
            self.request.gitlab_action,
            calls[0]["gitlab_action"],
        )

    def test_unverified_automatic_gitlab_write_requires_recovery(self) -> None:
        self.request.push_feature = False
        self.request.cherry_pick_integration = False
        self.request.push_integration = False
        self.request.gitlab_action = {
            "action": "merge_request.create",
            "parameters": {
                "host_alias": "company",
                "project_alias": "dfhis/guahao",
                "source_branch": "feature-DFHIS-31557",
                "target_branch": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号默认身份证",
            },
        }
        prepared = self.closure.prepare(self.request)
        transaction_id = int(prepared["transaction"]["id"])
        self.store.update_transaction(transaction_id, state="rc_runtime_accepted")

        with self.assertRaises(DeliveryError) as raised:
            self.closure.execute_stage_two(
                transaction_id,
                approved_plan_hash=prepared["plan"]["plan_hash"],
                execute_gitlab_action=lambda **_kwargs: {
                    "action": "merge_request.create",
                    "status": "failed",
                    "write_effect_status": "unknown",
                    "target_alias": "gl-h7-company-g5-dfhis-p6-guahao-m42",
                    "remote_dispatch_attempted": True,
                },
            )

        self.assertEqual("gitlab_write_unverified", raised.exception.code)
        self.assertEqual(
            "recovery_required",
            self.store.get_transaction(transaction_id)["state"],
        )

    def test_stage_two_is_blocked_before_rc_acceptance_without_git_changes(self) -> None:
        prepared, _stage_one = self.prepare_stage_one()
        transaction_id = int(prepared["transaction"]["id"])
        before = self.git_snapshot()

        result = self.closure.execute_stage_two(
            transaction_id,
            approved_plan_hash=prepared["plan"]["plan_hash"],
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual("rc_runtime_acceptance_pending", result["code"])
        self.assertEqual("task_commit_created", result["state"])
        self.assertEqual(before, self.git_snapshot())
        self.assertEqual("task_commit_created", self.store.get_transaction(transaction_id)["state"])

    def test_stage_two_is_fixed_blocked_without_remote_or_git_changes(self) -> None:
        prepared, stage_one = self.prepare_stage_one()
        transaction_id = int(prepared["transaction"]["id"])
        evidence = self.valid_rc_evidence(prepared, stage_one)
        self.closure.record_rc_integration_checkpoint(
            transaction_id,
            evidence=evidence,
        )
        self.closure.record_runtime_acceptance(
            transaction_id,
            phase="rc",
            status="passed",
            summary="RC 页面验证通过",
        )
        before = self.git_snapshot()
        canonical = delivery_adapter._closure
        self.assertIsNotNone(canonical)
        real_subprocess_run = canonical.subprocess.run
        real_subprocess_popen = canonical.subprocess.Popen
        git_calls: list[list[str]] = []
        external_process_calls: list[list[str]] = []
        external_popen_calls: list[list[str]] = []
        network_clients = frozenset(
            ("curl", "scp", "sftp", "ssh", "wget", "rsync"),
        )

        def is_external_command(command: object) -> bool:
            return bool(
                isinstance(command, (list, tuple))
                and command
                and (
                    Path(str(command[0])).name in network_clients
                    or (
                        Path(str(command[0])).name == "git"
                        and any(
                            str(item) in canonical._REMOTE_GIT_VERBS
                            for item in command
                        )
                    )
                )
            )

        def guarded_git(
            cwd: Path,
            args: list[str],
            *,
            input_text: str | None = None,
        ):
            del cwd, input_text
            git_calls.append(list(args))
            raise AssertionError("Stage2 attempted Git")

        def guarded_subprocess_run(*args: object, **kwargs: object):
            command = args[0] if args else kwargs.get("args")
            if is_external_command(command):
                external_process_calls.append([str(item) for item in command])
                raise AssertionError("Stage2 attempted an external process")
            return real_subprocess_run(*args, **kwargs)

        def guarded_subprocess_popen(*args: object, **kwargs: object):
            command = args[0] if args else kwargs.get("args")
            if is_external_command(command):
                external_popen_calls.append([str(item) for item in command])
                raise AssertionError("Stage2 attempted an external Popen process")
            return real_subprocess_popen(*args, **kwargs)

        with (
            mock.patch.object(canonical, "_git", side_effect=guarded_git),
            mock.patch.object(
                canonical.subprocess,
                "run",
                side_effect=guarded_subprocess_run,
            ),
            mock.patch.object(
                canonical.subprocess,
                "Popen",
                side_effect=guarded_subprocess_popen,
            ),
            mock.patch(
                "socket.create_connection",
                side_effect=AssertionError("Stage2 attempted a network connection"),
            ) as socket_connection,
            mock.patch(
                "socket.getaddrinfo",
                side_effect=AssertionError("Stage2 attempted DNS resolution"),
            ) as dns_resolution,
            mock.patch(
                "socket.socket",
                side_effect=AssertionError("Stage2 attempted to create a network socket"),
            ) as socket_factory,
        ):
            with self.assertRaises(DeliveryError) as raised:
                self.closure.execute_stage_two(
                    transaction_id,
                    approved_plan_hash=prepared["plan"]["plan_hash"],
                )

        self.assertEqual([], git_calls)
        self.assertEqual([], external_process_calls)
        self.assertEqual([], external_popen_calls)
        self.assertEqual(0, socket_connection.call_count)
        self.assertEqual(0, dns_resolution.call_count)
        self.assertEqual(0, socket_factory.call_count)

        self.assertEqual("remote_url_unreadable", raised.exception.code)
        self.assertEqual(before, self.git_snapshot())
        self.assertEqual("", before["remote_refs"])
        self.assertEqual("", self.repo.run(["git", "remote"]).stdout)
        transaction = self.store.get_transaction(transaction_id)
        self.assertEqual("rc_runtime_accepted", transaction["state"])
        self.assertNotEqual("completed", transaction["state"])
        self.assertNotIn(
            "completed",
            [event["event_type"] for event in self.store.get_events(transaction_id)],
        )


if __name__ == "__main__":
    unittest.main()
