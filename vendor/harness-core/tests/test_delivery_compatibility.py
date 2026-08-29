from __future__ import annotations

import json
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from contextlib import contextmanager, redirect_stderr

from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PLUGIN_SOURCE_ROOT / "his-engineering"
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("HARNESS_ENABLE_STAGED_PLUGIN_TESTS", "1")
os.environ.setdefault(
    "HARNESS_STAGED_PLUGIN_ROOT",
    str(PLUGIN_SOURCE_ROOT),
)

import app.delivery_closure as adapter
from app.delivery_closure import (
    DeliveryClosure,
    DeliveryError,
    DeliveryPolicy,
    DeliveryRequest,
    build_delivery_plan,
    delivery_plan_to_markdown,
    inspect_repository,
    stable_hash,
)


class GitRepository:
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()
        self.run("init", "-b", "release_2.15.3_250515")
        self.run("config", "user.email", "compatibility@example.test")
        self.run("config", "user.name", "Compatibility Test")
        (self.root / "app.txt").write_text("line one\nline two\n", encoding="utf-8")
        (self.root / "other.txt").write_text("other one\n", encoding="utf-8")
        self.run("add", "--", "app.txt", "other.txt")
        self.run("commit", "-m", "initial")

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=check,
        )

    def diff(self, *paths: str) -> str:
        return self.run("diff", "--binary", "--no-ext-diff", "--", *paths).stdout

    def close(self) -> None:
        self._temporary.cleanup()


class DeliveryCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_default_home_snapshot = _snapshot_real_default_home()
        self.repo = GitRepository()
        self._state = tempfile.TemporaryDirectory()
        self.state_root = Path(self._state.name).resolve()
        self.store = adapter._store.SQLiteDeliveryStore(self.state_root / "delivery.sqlite")
        self.policy = DeliveryPolicy.from_payload({})

    def tearDown(self) -> None:
        self.repo.close()
        self._state.cleanup()
        self.assertEqual(
            self.real_default_home_snapshot,
            _snapshot_real_default_home(),
            "focused compatibility tests must not mutate the real plugin home",
        )

    def request(
        self,
        *,
        project: Path | None = None,
        expected_diff: str | None = None,
        allowed_paths: list[str] | None = None,
    ) -> DeliveryRequest:
        return DeliveryRequest(
            entity_kind="requirement",
            entity_id="DFHIS-31557",
            title="挂号处理界面证件类型需要默认成身份证。",
            url="https://devops.aliyun.com/projex/req/DFHIS-31557#",
            project_path=str(project or self.repo.root),
            expected_diff=expected_diff if expected_diff is not None else self.repo.diff("app.txt"),
            allowed_paths=allowed_paths or ["app.txt"],
            output_dir=str(self.state_root / "output"),
        )

    def test_adapter_exposes_canonical_public_types_without_harness_database(self) -> None:
        self.assertTrue(DeliveryPolicy.__module__.startswith("_harness_his_engineering"))
        self.assertTrue(DeliveryError.__module__.startswith("_harness_his_engineering"))
        closure = DeliveryClosure(store=self.store, policy=self.policy)
        self.assertEqual(self.store.path, closure.store.path)
        canonical_source = Path(adapter._closure.__file__).read_text(encoding="utf-8")
        store_source = Path(adapter._store.__file__).read_text(encoding="utf-8")
        self.assertNotIn("app.database", canonical_source)
        self.assertNotIn("app.database", store_source)

    def test_repository_classification_contract(self) -> None:
        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        expected = self.repo.diff("app.txt")
        exact = inspect_repository(self.request(expected_diff=expected), self.policy)
        self.assertEqual("task_owned_exact", exact["classification"])
        self.assertEqual(["app.txt"], exact["task_changed_paths"])

        (self.repo.root / "other.txt").write_text("unrelated\n", encoding="utf-8")
        mixed = inspect_repository(self.request(expected_diff=expected), self.policy)
        self.assertEqual("mixed_separable", mixed["classification"])
        self.assertEqual(["other.txt"], mixed["unrelated_changed_paths"])

        (self.repo.root / "app.txt").write_text("drift\nline two changed\n", encoding="utf-8")
        ambiguous = inspect_repository(self.request(expected_diff=expected), self.policy)
        self.assertEqual("ambiguous_overlap", ambiguous["classification"])
        self.assertIn("task_patch_mismatch", ambiguous["blockers"])

        self.repo.run("switch", "-c", "wrong-base")
        wrong = inspect_repository(self.request(expected_diff=self.repo.diff("app.txt")), self.policy)
        self.assertEqual("unsafe_repository_state", wrong["classification"])
        self.assertIn("wrong_base_branch", wrong["blockers"])

        with tempfile.TemporaryDirectory() as directory:
            non_git = inspect_repository(
                self.request(project=Path(directory), expected_diff=""),
                self.policy,
            )
        self.assertEqual("unsafe_repository_state", non_git["classification"])
        self.assertIn("not_git_repository", non_git["blockers"])

    def test_linked_worktree_keeps_published_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            linked = Path(directory).resolve() / "linked"
            self.repo.run(
                "worktree",
                "add",
                "--force",
                str(linked),
                "release_2.15.3_250515",
            )
            try:
                (linked / "app.txt").write_text(
                    "line one\nline two changed\n",
                    encoding="utf-8",
                )
                expected = subprocess.run(
                    ["git", "diff", "--binary", "--no-ext-diff", "--", "app.txt"],
                    cwd=linked,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout
                snapshot = inspect_repository(
                    self.request(project=linked, expected_diff=expected),
                    self.policy,
                )
            finally:
                self.repo.run("worktree", "remove", "--force", str(linked), check=False)
        self.assertEqual("unsafe_repository_state", snapshot["classification"])
        self.assertIn("delivery_project_linked_worktree", snapshot["blockers"])

    def test_plan_fields_actions_hash_and_markdown_contract(self) -> None:
        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        request = self.request()
        request.verify_commands = ["npm run lint", "node tests/task-check.js"]
        snapshot = inspect_repository(request, self.policy)
        plan = build_delivery_plan(request, self.policy, snapshot)

        self.assertEqual("feature-DFHIS-31557", plan["task_branch"])
        self.assertEqual(
            "feat: DFHIS-31557-https://devops.aliyun.com/projex/req/DFHIS-31557# 《挂号处理界面证件类型需要默认成身份证。》",
            plan["commit_message"],
        )
        self.assertTrue(plan["actions"]["create_task_branch"])
        self.assertTrue(plan["actions"]["commit"])
        for action in (
            "push_feature",
            "cherry_pick_integration",
            "push_integration",
            "yunxiao_comment",
            "yunxiao_transition",
        ):
            self.assertFalse(plan["actions"][action])
        self.assertEqual(
            stable_hash({key: value for key, value in plan.items() if key != "plan_hash"}),
            plan["plan_hash"],
        )
        markdown = delivery_plan_to_markdown(plan)
        self.assertIn("# HIS Harness Git 交付计划", markdown)
        self.assertIn("## 专项验证命令", markdown)
        self.assertIn("`npm run lint`", markdown)
        self.assertIn("`node tests/task-check.js`", markdown)

    def test_prepare_initial_state_show_and_idempotence(self) -> None:
        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        closure = DeliveryClosure(store=self.store, policy=self.policy)
        first = closure.prepare(self.request())
        second = closure.prepare(self.request())
        shown = closure.show(int(first["transaction"]["id"]))

        self.assertEqual(
            "waiting_release_runtime_acceptance",
            first["transaction"]["state"],
        )
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["transaction"]["id"], second["transaction"]["id"])
        self.assertEqual(first["plan"]["plan_hash"], shown["plan"]["plan_hash"])
        self.assertEqual("planned", shown["events"][0]["event_type"])

    def test_workspace_failure_keeps_stable_error_code(self) -> None:
        closure = DeliveryClosure(store=self.store, policy=self.policy)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DeliveryError) as caught:
                closure.prepare(self.request(project=Path(directory), expected_diff=""))
        self.assertEqual("workspace_blocked", caught.exception.code)

    def test_external_write_verification_error_text_is_compatible(self) -> None:
        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        request = self.request()
        request.verify_commands = ["npm test && git push origin HEAD"]
        snapshot = inspect_repository(request, self.policy)
        with self.assertRaisesRegex(DeliveryError, "验证命令包含外部写入"):
            build_delivery_plan(request, self.policy, snapshot)

    def test_complete_manifest_loads_before_commit_capability_resolution(self) -> None:
        from app.capability_registry import CapabilityRegistry

        registry = CapabilityRegistry.from_plugin_roots([PLUGIN_ROOT])
        descriptor = registry.resolve(
            "git.commit-local",
            "his-engineering",
        )
        self.assertEqual("L3", descriptor.mutation_level.name)
        self.assertEqual(("repository:commit-local",), descriptor.scopes)
        self.assertEqual(
            (PLUGIN_ROOT / "scripts" / "git_delivery.py").resolve(),
            descriptor.entrypoint,
        )
        secured = adapter.commit_capability_registry().resolve(
            "git.commit-local",
            "his-engineering",
        )
        self.assertEqual(
            {
                (PLUGIN_ROOT / "scripts" / "delivery_closure.py").resolve(),
                (PLUGIN_ROOT / "scripts" / "delivery_store.py").resolve(),
            },
            {path for path, _identity in secured.dependency_identities},
        )

    def test_complete_delivery_manifest_exposes_remote_capability_entrypoints(self) -> None:
        """A plan-authorized delivery never bypasses disabled L4 capabilities."""
        from app.capability_registry import CapabilityRegistry

        registry = CapabilityRegistry.from_plugin_roots([PLUGIN_ROOT])
        for capability in ("git.push", "gitlab.write", "github.write"):
            descriptor = registry.resolve(capability, "his-engineering")
            self.assertTrue(descriptor.enabled)
            self.assertEqual("L4", descriptor.mutation_level.name)
            self.assertIsNotNone(descriptor.entrypoint)
            self.assertTrue(descriptor.entrypoint.is_file())

    def test_delivery_service_permits_the_two_plan_bound_l4_capabilities(self) -> None:
        from app.capability_contracts import (
            CapabilityAuthorization,
            CapabilityRequest,
            MutationLevel,
        )

        service = adapter.build_delivery_capability_service()
        cases = (
            (
                "git.push",
                {"delivery_db": "/private/tmp/missing-delivery.sqlite", "transaction_id": 1, "approved_plan_hash": "a" * 64, "phase": "pre_rc"},
                ("repository:push", "capability:git.push"),
                "GIT_PUSH_BLOCKED",
            ),
            (
                "gitlab.write",
                {"delivery_db": "/private/tmp/missing-delivery.sqlite", "transaction_id": 1, "approved_plan_hash": "a" * 64},
                ("gitlab:write", "capability:gitlab.write"),
                "GITLAB_WRITE_BLOCKED",
            ),
            (
                "github.write",
                {"delivery_db": "/private/tmp/missing-delivery.sqlite", "transaction_id": 1, "approved_plan_hash": "a" * 64},
                ("github:write", "capability:github.write"),
                "GITHUB_WRITE_BLOCKED",
            ),
        )
        for capability, input_data, scope, summary in cases:
            with self.subTest(capability=capability):
                result = service.route(
                    CapabilityRequest(
                        request_id="delivery-l4-" + capability,
                        capability=capability,
                        provider="his-engineering",
                        mode="apply",
                        mutation_level=MutationLevel.L4,
                        authorization=CapabilityAuthorization(explicit=True, scope=scope),
                        input=input_data,
                        context={},
                    )
                ).result
                self.assertEqual(summary, result["summary"])

    def test_gitlab_l4_gate_accepts_the_exact_pending_delivery_transaction(self) -> None:
        from app.capability_contracts import (
            CapabilityAuthorization,
            CapabilityRequest,
            MutationLevel,
        )

        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        request = self.request()
        request.gitlab_action = {
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
        closure = DeliveryClosure(store=self.store, policy=self.policy)
        prepared = closure.prepare(request)
        transaction_id = int(prepared["transaction"]["id"])
        self.store.update_transaction(transaction_id, state="gitlab_delivery_pending")

        result = adapter.build_delivery_capability_service().route(
            CapabilityRequest(
                request_id="delivery-gitlab-ready",
                capability="gitlab.write",
                provider="his-engineering",
                mode="apply",
                mutation_level=MutationLevel.L4,
                authorization=CapabilityAuthorization(
                    explicit=True,
                    scope=("gitlab:write", "capability:gitlab.write"),
                ),
                input={
                    "delivery_db": str(self.store.path),
                    "transaction_id": transaction_id,
                    "approved_plan_hash": prepared["plan"]["plan_hash"],
                },
                context={},
            )
        ).result

        self.assertEqual("success", result["status"])
        self.assertEqual("GITLAB_WRITE_READY", result["summary"])

    def test_github_l4_gate_accepts_only_the_exact_pending_delivery_transaction(self) -> None:
        from app.capability_contracts import (
            CapabilityAuthorization,
            CapabilityRequest,
            MutationLevel,
        )

        (self.repo.root / "app.txt").write_text(
            "line one\nline two changed\n",
            encoding="utf-8",
        )
        request = self.request()
        request.github_action = {
            "action": "github.pull_request.create",
            "parameters": {
                "owner": "dfhis",
                "repository": "guahao",
                "head": "feature-DFHIS-31557",
                "base": "RC_2.16.1_250514",
                "title": "DFHIS-31557 title",
            },
        }
        closure = DeliveryClosure(store=self.store, policy=self.policy)
        prepared = closure.prepare(request)
        transaction_id = int(prepared["transaction"]["id"])
        self.store.update_transaction(transaction_id, state="github_delivery_pending")

        def invoke(plan_hash: str) -> dict[str, object]:
            return adapter.build_delivery_capability_service().route(
                CapabilityRequest(
                    request_id="delivery-github-ready",
                    capability="github.write",
                    provider="his-engineering",
                    mode="apply",
                    mutation_level=MutationLevel.L4,
                    authorization=CapabilityAuthorization(
                        explicit=True,
                        scope=("github:write", "capability:github.write"),
                    ),
                    input={
                        "delivery_db": str(self.store.path),
                        "transaction_id": transaction_id,
                        "approved_plan_hash": plan_hash,
                    },
                    context={},
                )
            ).result

        result = invoke(prepared["plan"]["plan_hash"])
        mismatched = invoke("f" * 64)

        self.assertEqual("success", result["status"])
        self.assertEqual("GITHUB_WRITE_READY", result["summary"])
        self.assertEqual("blocked", mismatched["status"])
        self.assertEqual("GITHUB_WRITE_BLOCKED", mismatched["summary"])

    def test_cli_converts_missing_plugin_constructor_to_stable_nonzero_error(self) -> None:
        from tools import delivery as delivery_cli

        error = adapter.DeliveryError("his_engineering_plugin_required", adapter._ERROR)
        stderr = io.StringIO()
        with mock.patch.object(
            delivery_cli,
            "DeliveryClosure",
            side_effect=error,
        ), mock.patch.object(
            sys,
            "argv",
            ["delivery.py", "show", "--transaction-id", "1", "--json"],
        ), redirect_stderr(stderr):
            status = delivery_cli.main()
        self.assertEqual(2, status)
        self.assertEqual(
            "delivery blocked [his_engineering_plugin_required]: "
            + adapter._ERROR
            + "\n",
            stderr.getvalue(),
        )

    def test_cli_converts_runtime_identity_failure_to_stable_nonzero_error(self) -> None:
        from tools import delivery as delivery_cli

        closure = mock.Mock()
        closure.store.path = self.state_root / "delivery.sqlite"
        closure.show.return_value = {"plan": {"plan_hash": "a" * 64}}
        service = mock.Mock()
        service.route.return_value.result = {
            "status": "blocked",
            "summary": "CAPABILITY_ENTRYPOINT_INVALID",
            "changed": False,
            "audit": {"error_code": "CAPABILITY_ENTRYPOINT_INVALID"},
        }
        stderr = io.StringIO()
        with mock.patch.object(
            delivery_cli,
            "DeliveryClosure",
            return_value=closure,
        ), mock.patch.object(
            delivery_cli,
            "build_delivery_capability_service",
            return_value=service,
        ), mock.patch.object(
            sys,
            "argv",
            [
                "delivery.py",
                "first-confirmation",
                "--transaction-id",
                "1",
                "--confirm",
            ],
        ), redirect_stderr(stderr):
            status = delivery_cli.main()
        self.assertEqual(2, status)
        self.assertEqual(
            "delivery blocked [his_engineering_plugin_required]: "
            + adapter._ERROR
            + "\n",
            stderr.getvalue(),
        )
        service.route.assert_called_once()
        request = service.route.call_args.args[0]
        self.assertEqual("git.commit-local", request.capability)
        self.assertEqual("apply", request.mode)

    def test_rule_pack_translation_keeps_local_safe_defaults(self) -> None:
        policy = DeliveryPolicy.from_rule_pack()
        self.assertEqual("release_2.15.3_250515", policy.base_branch)
        self.assertEqual("RC_2.16.1_250514", policy.integration_branch)
        self.assertFalse(policy.push_feature_default)
        self.assertFalse(policy.cherry_pick_integration_default)
        self.assertFalse(policy.push_integration_default)


class PluginResolutionCompatibilityTests(unittest.TestCase):
    def plugin_copy(self, parent: Path) -> Path:
        target = parent / "his-engineering"
        shutil.copytree(
            PLUGIN_ROOT,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return target

    @contextmanager
    def patched_roots(self, root: Path):
        with (
            mock.patch.object(
                adapter,
                "_FIXED_ROOT",
                root.parent / "missing-fixed-plugin",
            ),
            mock.patch.dict(
                os.environ,
                {
                    "HARNESS_ENABLE_STAGED_PLUGIN_TESTS": "1",
                    "HARNESS_STAGED_PLUGIN_ROOT": str(root.parent),
                },
                clear=False,
            ),
        ):
            yield

    def test_fixed_layout_resolution_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.plugin_copy(Path(directory).resolve())
            with (
                mock.patch.object(adapter, "_FIXED_ROOT", root),
                mock.patch.dict(
                    os.environ,
                    {
                        "HARNESS_ENABLE_STAGED_PLUGIN_TESTS": "1",
                        "HARNESS_STAGED_PLUGIN_ROOT": str(
                            root.parent / "missing-staging-plugin"
                        ),
                    },
                    clear=False,
                ),
            ):
                self.assertEqual(root, adapter._root())

    def test_staged_layout_requires_explicit_test_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.plugin_copy(Path(directory).resolve())
            with (
                mock.patch.object(
                    adapter,
                    "_FIXED_ROOT",
                    root.parent / "missing-fixed-plugin",
                ),
                mock.patch.dict(
                    os.environ,
                    {
                        "HARNESS_ENABLE_STAGED_PLUGIN_TESTS": "0",
                        "HARNESS_STAGED_PLUGIN_ROOT": str(root.parent),
                    },
                    clear=False,
                ),
            ):
                with self.assertRaisesRegex(
                    adapter._PluginResolutionError,
                    "^" + adapter._ERROR + "$",
                ):
                    adapter._root()

    def test_missing_malformed_identity_and_symlink_roots_fail_stably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            missing = parent / "missing"
            with self.patched_roots(missing):
                with self.assertRaisesRegex(adapter._PluginResolutionError, "^" + adapter._ERROR + "$"):
                    adapter._root()

            root = self.plugin_copy(parent)
            manifest_path = root / "capabilities.json"
            manifest_path.write_text("{", encoding="utf-8")
            with self.patched_roots(root):
                with self.assertRaisesRegex(adapter._PluginResolutionError, "^" + adapter._ERROR + "$"):
                    adapter._root()

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = self.plugin_copy(parent)
            manifest_path = root / "capabilities.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plugin_version"] = "9.9.9"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.patched_roots(root):
                with self.assertRaisesRegex(adapter._PluginResolutionError, "^" + adapter._ERROR + "$"):
                    adapter._root()

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = self.plugin_copy(parent)
            alias = parent / "plugin-alias"
            alias.symlink_to(root, target_is_directory=True)
            with (
                mock.patch.object(adapter, "_FIXED_ROOT", alias),
                mock.patch.dict(
                    os.environ,
                    {"HARNESS_ENABLE_STAGED_PLUGIN_TESTS": "0"},
                    clear=False,
                ),
            ):
                with self.assertRaisesRegex(adapter._PluginResolutionError, "^" + adapter._ERROR + "$"):
                    adapter._root()

    def test_incomplete_syntax_corrupt_and_symlinked_sources_fail_stably(self) -> None:
        fixtures = ("incomplete", "syntax", "symlink")
        for fixture in fixtures:
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory).resolve()
                root = self.plugin_copy(parent)
                closure_path = root / "scripts" / "delivery_closure.py"
                if fixture == "incomplete":
                    closure_path.write_text("ONLY = True\n", encoding="utf-8")
                elif fixture == "syntax":
                    closure_path.write_text("def broken(:\n", encoding="utf-8")
                else:
                    external = parent / "external.py"
                    external.write_text("ONLY = True\n", encoding="utf-8")
                    closure_path.unlink()
                    closure_path.symlink_to(external)
                with self.patched_roots(root):
                    with self.assertRaisesRegex(adapter._PluginResolutionError, "^" + adapter._ERROR + "$"):
                        adapter._canonical()
                self.assertFalse(
                    any(
                        name == "_harness_his_engineering"
                        or name.startswith("_harness_his_engineering.")
                        for name in sys.modules
                        if sys.modules[name] is not adapter._closure
                    )
                )

    def test_complete_registry_rejects_missing_enabled_entrypoint_stably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.plugin_copy(Path(directory).resolve())
            (root / "scripts" / "gitlab_read.py").unlink()
            with self.patched_roots(root):
                with self.assertRaises(adapter.DeliveryError) as caught:
                    adapter.commit_capability_registry()
        self.assertEqual("his_engineering_plugin_required", caught.exception.code)
        self.assertEqual(adapter._ERROR, str(caught.exception))

    def test_commit_registry_rejects_syntax_corrupt_l3_sources_stably(self) -> None:
        for source_name in (
            "git_delivery.py",
            "delivery_closure.py",
            "delivery_store.py",
        ):
            with self.subTest(source=source_name), tempfile.TemporaryDirectory() as directory:
                root = self.plugin_copy(Path(directory).resolve())
                (root / "scripts" / source_name).write_text(
                    "def broken(:\n",
                    encoding="utf-8",
                )
                with self.patched_roots(root):
                    with self.assertRaises(adapter.DeliveryError) as caught:
                        adapter.commit_capability_registry()
                self.assertEqual(
                    "his_engineering_plugin_required",
                    caught.exception.code,
                )
                self.assertEqual(adapter._ERROR, str(caught.exception))

    def test_runtime_blocks_entrypoint_and_dependency_identity_drift_before_process(self) -> None:
        from app.capability_contracts import (
            CapabilityAuthorization,
            CapabilityRequest,
            MutationLevel,
        )
        from app.capability_runtime import CapabilityRuntime

        for target_name in ("git_delivery.py", "delivery_closure.py", "delivery_store.py"):
            with self.subTest(target=target_name), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory).resolve()
                root = self.plugin_copy(parent)
                marker = parent / (target_name + ".ran")
                with self.patched_roots(root):
                    registry = adapter.commit_capability_registry()
                target = root / "scripts" / target_name
                before = target.stat()
                malicious = (
                    f"open({str(marker)!r}, 'w').write('ran')\n".encode("utf-8")
                )
                self.assertLess(len(malicious), before.st_size)
                target.write_bytes(
                    malicious + (b"#" * (before.st_size - len(malicious)))
                )
                os.utime(
                    target,
                    ns=(before.st_atime_ns, before.st_mtime_ns),
                )
                after = target.stat()
                self.assertEqual(
                    (
                        before.st_dev,
                        before.st_ino,
                        before.st_mode,
                        before.st_size,
                        before.st_mtime_ns,
                    ),
                    (
                        after.st_dev,
                        after.st_ino,
                        after.st_mode,
                        after.st_size,
                        after.st_mtime_ns,
                    ),
                )
                request = CapabilityRequest(
                    request_id="identity-drift",
                    capability="git.commit-local",
                    provider="his-engineering",
                    mode="apply",
                    mutation_level=MutationLevel.L3,
                    authorization=CapabilityAuthorization(
                        explicit=True,
                        scope=("repository:commit-local",),
                    ),
                    input={
                        "delivery_db": str(parent / "missing.sqlite"),
                        "transaction_id": 1,
                        "approved_plan_hash": "a" * 64,
                    },
                    context={},
                )
                execution = CapabilityRuntime(registry).execute(request)
                self.assertEqual("blocked", execution.result.status)
                self.assertEqual(
                    "CAPABILITY_ENTRYPOINT_INVALID",
                    execution.result.audit["error_code"],
                )
                self.assertFalse(marker.exists())

    def test_runtime_executes_private_l3_snapshot_when_sources_drift_at_spawn(self) -> None:
        from app.capability_contracts import (
            CapabilityAuthorization,
            CapabilityRequest,
            MutationLevel,
        )
        from app.capability_runtime import CapabilityRuntime

        real_run = subprocess.run
        for target_name in ("git_delivery.py", "delivery_closure.py", "delivery_store.py"):
            with self.subTest(target=target_name), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory).resolve()
                root = self.plugin_copy(parent)
                marker = parent / (target_name + ".spawn-ran")
                with self.patched_roots(root):
                    registry = adapter.commit_capability_registry()
                target = root / "scripts" / target_name
                before = target.stat()
                malicious = (
                    f"open({str(marker)!r}, 'w').write('ran')\n".encode("utf-8")
                )
                self.assertLess(len(malicious), before.st_size)

                def drift_then_run(command, **kwargs):
                    target.write_bytes(
                        malicious + (b"#" * (before.st_size - len(malicious)))
                    )
                    os.utime(
                        target,
                        ns=(before.st_atime_ns, before.st_mtime_ns),
                    )
                    return real_run(command, **kwargs)

                request = CapabilityRequest(
                    request_id="spawn-drift",
                    capability="git.commit-local",
                    provider="his-engineering",
                    mode="apply",
                    mutation_level=MutationLevel.L3,
                    authorization=CapabilityAuthorization(
                        explicit=True,
                        scope=("repository:commit-local",),
                    ),
                    input={
                        "delivery_db": str(parent / "missing.sqlite"),
                        "transaction_id": 1,
                        "approved_plan_hash": "a" * 64,
                    },
                    context={},
                )
                with mock.patch(
                    "app.capability_runtime.subprocess.run",
                    side_effect=drift_then_run,
                ):
                    execution = CapabilityRuntime(registry).execute(request)

                self.assertEqual("blocked", execution.result.status)
                self.assertEqual("GIT_COMMIT_LOCAL_BLOCKED", execution.result.summary)
                self.assertFalse(execution.result.changed)
                self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
