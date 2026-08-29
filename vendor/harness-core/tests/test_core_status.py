from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core_status import build_core_status_snapshot


HARNESS_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAMES = (
    "his-harness-core",
    "yunxiao",
    "his-engineering",
    "his-knowledge",
)
PLUGIN_REQUIRED_FILES = (
    "capabilities.json",
    ".codex-plugin/plugin.json",
)


def _fixture_plugin_roots(harness_root: Path) -> list[str]:
    sibling_roots = [harness_root.parent / "plugins" / name for name in PLUGIN_NAMES]
    if all(
        root.is_dir() and all((root / relative).is_file() for relative in PLUGIN_REQUIRED_FILES)
        for root in sibling_roots
    ):
        return [str(root) for root in sibling_roots]

    try:
        payload = json.loads(
            (harness_root / "config" / "capabilities.json").read_text(encoding="utf-8")
        )
        roots = payload["plugin_roots"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("Core 测试插件根目录配置无效。") from exc
    if not isinstance(roots, list) or len(roots) != len(PLUGIN_NAMES):
        raise ValueError("Core 测试插件根目录配置无效。")

    configured_roots = [Path(root) for root in roots if isinstance(root, str)]
    if len(configured_roots) != len(PLUGIN_NAMES) or any(
        not root.is_absolute() for root in configured_roots
    ):
        raise ValueError("Core 测试插件根目录配置无效。")
    parent = configured_roots[0].parent
    if (
        [root.name for root in configured_roots] != list(PLUGIN_NAMES)
        or any(root.parent != parent for root in configured_roots)
        or any(
            not root.is_dir()
            or any(not (root / relative).is_file() for relative in PLUGIN_REQUIRED_FILES)
            for root in configured_roots
        )
    ):
        raise ValueError("Core 测试插件根目录配置无效。")
    return [str(root) for root in configured_roots]


def _write_fixture_plugin_roots(parent: Path) -> list[str]:
    roots: list[str] = []
    for name in PLUGIN_NAMES:
        root = parent / name
        (root / ".codex-plugin").mkdir(parents=True)
        (root / "capabilities.json").write_text("{}", encoding="utf-8")
        (root / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
        roots.append(str(root))
    return roots


class FixturePluginRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.harness_root = self.root / "Harness"
        (self.harness_root / "config").mkdir(parents=True)

    def _write_config(self, roots: list[str] | str) -> None:
        (self.harness_root / "config" / "capabilities.json").write_text(
            json.dumps({"plugin_roots": roots}),
            encoding="utf-8",
        )

    def test_sibling_plugins_take_priority_when_complete(self) -> None:
        sibling_roots = _write_fixture_plugin_roots(self.root / "plugins")
        fallback_roots = _write_fixture_plugin_roots(self.root / "fallback-plugins")
        self._write_config(fallback_roots)

        self.assertEqual(sibling_roots, _fixture_plugin_roots(self.harness_root))

    def test_missing_sibling_plugins_use_exact_configured_roots(self) -> None:
        configured_roots = _write_fixture_plugin_roots(self.root / "configured-plugins")
        self._write_config(configured_roots)

        self.assertEqual(configured_roots, _fixture_plugin_roots(self.harness_root))

    def test_malformed_plugin_root_config_fails_fast(self) -> None:
        self._write_config(["relative-plugin-root"])

        with self.assertRaisesRegex(ValueError, "插件根目录配置无效"):
            _fixture_plugin_roots(self.harness_root)


class CoreStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.missing_database = self.root / "missing.sqlite"
        self.capability_config = self.root / "capabilities.json"
        self.capability_config.write_text(
            json.dumps(
                {
                    "schema_version": "his-capability-runtime-config.v1",
                    "routing_mode": "enforce",
                    "plugin_roots": _fixture_plugin_roots(HARNESS_ROOT),
                    "external_writes_default": False,
                    "default_timeout_seconds": 60,
                    "knowledge_home": str(self.root / "knowledge"),
                }
            ),
            encoding="utf-8",
        )

    def _snapshot(self, *, database_path: Path | None = None) -> dict:
        return build_core_status_snapshot(
            harness_root=HARNESS_ROOT,
            database_path=database_path or self.missing_database,
            capability_config_path=self.capability_config,
            plugin_inventory_path=HARNESS_ROOT / "config" / "plugin_inventory.json",
        )

    def _write_runtime_config(self, plugin_roots: list[str]) -> None:
        self.capability_config.write_text(
            json.dumps(
                {
                    "schema_version": "his-capability-runtime-config.v1",
                    "routing_mode": "enforce",
                    "plugin_roots": plugin_roots,
                    "external_writes_default": False,
                    "default_timeout_seconds": 60,
                    "knowledge_home": str(self.root / "knowledge"),
                }
            ),
            encoding="utf-8",
        )

    def test_snapshot_reports_verified_enforce_plugins_without_external_access(self) -> None:
        snapshot = self._snapshot()

        self.assertEqual("his-core-status.v1", snapshot["schema_version"])
        self.assertEqual("ready", snapshot["status"])
        self.assertEqual("0.66.0", snapshot["core_version"])
        self.assertEqual("enforce", snapshot["routing_mode"])
        self.assertFalse(snapshot["external_writes_default"])
        self.assertTrue(snapshot["plugin_inventory"]["verified"])
        self.assertRegex(snapshot["plugin_inventory"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            ["his-harness-core", "yunxiao", "his-engineering", "his-knowledge"],
            [item["name"] for item in snapshot["plugins"]],
        )
        self.assertIn("workitem.read", [item["name"] for item in snapshot["capabilities"]])
        self.assertIn("git.commit-local", [item["name"] for item in snapshot["capabilities"]])
        self.assertEqual({"status": "missing"}, snapshot["database"])
        self.assertFalse(snapshot["credentials_read"])
        self.assertFalse(snapshot["external_calls"])
        self.assertEqual([], snapshot["blockers"])
        readiness = snapshot["readiness"]
        self.assertEqual("his-readiness.v1", readiness["schema_version"])
        self.assertEqual(
            {
                "real_model_worker",
                "learning_loop",
                "business_acceptance",
                "external_writes",
                "knowledge_home",
            },
            {item["id"] for item in readiness["items"]},
        )
        readiness_by_id = {item["id"]: item for item in readiness["items"]}
        self.assertEqual("single_node_smoke_ready", readiness_by_id["real_model_worker"]["state"])
        self.assertEqual(
            "his-model-worker-smoke-readiness.v1",
            readiness_by_id["real_model_worker"]["smoke_contract_schema"],
        )
        self.assertFalse(
            readiness_by_id["real_model_worker"]["smoke_readiness"]["credentials_read"]
        )
        self.assertFalse(
            readiness_by_id["real_model_worker"]["smoke_readiness"]["network_called"]
        )
        self.assertFalse(
            readiness_by_id["real_model_worker"]["smoke_readiness"]["real_model_dag_enabled"]
        )
        self.assertIn(
            {"id": "single_node_smoke_contract", "status": "passed"},
            readiness_by_id["real_model_worker"]["prerequisites"],
        )
        self.assertEqual("candidate_only", readiness_by_id["learning_loop"]["state"])
        self.assertEqual("not_evaluated", readiness_by_id["business_acceptance"]["state"])
        self.assertEqual("review_required", readiness_by_id["external_writes"]["state"])
        self.assertEqual("missing", readiness_by_id["knowledge_home"]["state"])
        self.assertIn(
            {"id": "business_acceptance_contract", "status": "passed"},
            readiness_by_id["business_acceptance"]["prerequisites"],
        )
        self.assertEqual(
            ["knowledge.candidate.create", "knowledge.candidate.review", "knowledge.item.promote"],
            readiness_by_id["learning_loop"]["capabilities"],
        )
        self.assertIn(
            {"id": "failed_sample_to_candidate_contract", "status": "passed"},
            readiness_by_id["learning_loop"]["prerequisites"],
        )
        self.assertEqual(
            ["database.change", "git.push", "gitlab.write", "github.write", "workitem.write"],
            readiness_by_id["external_writes"]["capabilities"],
        )
        self.assertEqual(
            ["git.push", "gitlab.write", "github.write"],
            readiness_by_id["external_writes"]["enabled_allowlist"],
        )
        self.assertIn(
            {"id": "dry_run_transaction_plan", "status": "passed"},
            readiness_by_id["external_writes"]["prerequisites"],
        )
        self.assertEqual(
            "his-external-write-dry-run-plan.v1",
            readiness_by_id["external_writes"]["dry_run_plan_schema"],
        )
        self.assertIn("manager_ui", readiness_by_id["knowledge_home"])
        self.assertNotIn("harness_root", snapshot)
        self.assertNotIn(str(HARNESS_ROOT), json.dumps(snapshot, ensure_ascii=False))

    def test_snapshot_does_not_modify_an_existing_temporary_database(self) -> None:
        database_path = self.root / "read-only-health.sqlite"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("create table fixture (id integer primary key)")
        finally:
            connection.close()

        before = self._sqlite_sidecar_stats(database_path)

        with mock.patch("app.database.sqlite3.connect", wraps=sqlite3.connect) as connect:
            snapshot = self._snapshot(database_path=database_path)

        self.assertEqual(before, self._sqlite_sidecar_stats(database_path))
        self.assertEqual("healthy", snapshot["database"]["status"])
        self.assertEqual("sqlite_immutable", snapshot["database"]["probe"])
        self.assertEqual("ok", snapshot["database"]["integrity_check"])
        self.assertEqual("main_file_only", snapshot["database"]["integrity_scope"])
        self.assertEqual("checkpointed_snapshot", snapshot["database"]["freshness"])
        self.assertIn("mode=ro&immutable=1", str(connect.call_args.args[0]))

    def test_wal_sidecars_are_metadata_only_and_never_open_sqlite(self) -> None:
        database_path = self.root / "wal-live.sqlite"
        connection = sqlite3.connect(database_path)
        self.addCleanup(connection.close)
        connection.execute("pragma journal_mode = wal")
        connection.execute("create table fixture (id integer primary key, value text not null)")
        connection.execute("insert into fixture(value) values ('not-checkpointed')")
        connection.commit()

        before = self._sqlite_sidecar_stats(database_path)
        self.assertTrue((Path(str(database_path) + "-wal")).exists())
        with mock.patch("app.database.sqlite3.connect", wraps=sqlite3.connect) as connect:
            snapshot = self._snapshot(database_path=database_path)

        self.assertEqual(before, self._sqlite_sidecar_stats(database_path))
        self.assertEqual(
            {
                "status": "unknown",
                "probe": "metadata_only",
                "integrity_check": "not_run",
                "freshness": "unknown",
                "reason": "wal_sidecars_present",
            },
            snapshot["database"],
        )
        connect.assert_not_called()

    def test_dangling_wal_sidecar_is_blocked_without_opening_sqlite(self) -> None:
        database_path = self.root / "dangling-sidecar.sqlite"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("create table fixture (id integer primary key)")
        finally:
            connection.close()
        Path(str(database_path) + "-wal").symlink_to("missing-wal")

        with mock.patch("app.database.sqlite3.connect", wraps=sqlite3.connect) as connect:
            snapshot = self._snapshot(database_path=database_path)

        self.assertEqual("unknown", snapshot["database"]["status"])
        self.assertEqual("wal_sidecars_present", snapshot["database"]["reason"])
        connect.assert_not_called()

    def test_symlink_database_is_unavailable_without_opening_sqlite(self) -> None:
        database_path = self.root / "real.sqlite"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("create table fixture (id integer primary key)")
        finally:
            connection.close()
        symlink_path = self.root / "database-link.sqlite"
        symlink_path.symlink_to(database_path)

        with mock.patch("app.database.sqlite3.connect", wraps=sqlite3.connect) as connect:
            snapshot = self._snapshot(database_path=symlink_path)

        self.assertEqual({"status": "unavailable"}, snapshot["database"])
        connect.assert_not_called()

    def test_special_database_entry_is_unavailable_without_opening_sqlite(self) -> None:
        database_path = self.root / "special.sqlite"
        os.mkfifo(database_path)

        with mock.patch("app.database.sqlite3.connect", wraps=sqlite3.connect) as connect:
            snapshot = self._snapshot(database_path=database_path)

        self.assertEqual({"status": "unavailable"}, snapshot["database"])
        connect.assert_not_called()

    def test_unreadable_database_is_unavailable_without_opening_sqlite(self) -> None:
        database_path = self.root / "unreadable.sqlite"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("create table fixture (id integer primary key)")
        finally:
            connection.close()

        with (
            mock.patch("app.database.os.open", side_effect=PermissionError),
            mock.patch("app.database.sqlite3.connect", wraps=sqlite3.connect) as connect,
        ):
            snapshot = self._snapshot(database_path=database_path)

        self.assertEqual({"status": "unavailable"}, snapshot["database"])
        connect.assert_not_called()

    def test_invalid_inventory_is_blocked_without_exception_details(self) -> None:
        broken_inventory = self.root / "plugin_inventory.json"
        broken_inventory.write_text(
            '{"schema_version":"broken","plugins":[]}',
            encoding="utf-8",
        )

        snapshot = build_core_status_snapshot(
            harness_root=HARNESS_ROOT,
            database_path=self.missing_database,
            capability_config_path=self.capability_config,
            plugin_inventory_path=broken_inventory,
        )

        self.assertEqual("blocked", snapshot["status"])
        self.assertEqual("plugin_inventory_invalid", snapshot["blockers"][0]["code"])
        self.assertEqual([], snapshot["plugins"])
        self.assertEqual([], snapshot["capabilities"])

    def test_verification_levels_never_open_default_manager_database(self) -> None:
        explicit_database = self.root / "explicit-core-status.sqlite"
        with sqlite3.connect(explicit_database) as connection:
            connection.execute("create table fixture (id integer primary key)")

        default_manager_database = HARNESS_ROOT / "data" / "harness.sqlite"
        with mock.patch(
            "app.database.sqlite3.connect", wraps=sqlite3.connect
        ) as connect:
            snapshot = self._snapshot(database_path=explicit_database)

        self.assertEqual(
            [
                "code_ready",
                "configured",
                "locally_tested",
                "externally_verified",
                "business_accepted",
            ],
            [item["id"] for item in snapshot["readiness"]["verification_levels"]],
        )
        self.assertTrue(connect.call_args_list)
        self.assertTrue(
            all(
                str(default_manager_database) not in str(call.args[0])
                for call in connect.call_args_list
            )
        )

    def test_snapshot_without_explicit_database_path_never_runs_health_probe(self) -> None:
        with (
            mock.patch(
                "app.database.database_read_only_health_snapshot",
                side_effect=AssertionError("default database health probe is forbidden"),
            ) as health_probe,
            mock.patch(
                "app.database.sqlite3.connect",
                side_effect=AssertionError("default sqlite connect is forbidden"),
            ) as sqlite_connect,
        ):
            snapshot = build_core_status_snapshot(
                harness_root=HARNESS_ROOT,
                capability_config_path=self.capability_config,
                plugin_inventory_path=HARNESS_ROOT / "config" / "plugin_inventory.json",
            )

        self.assertEqual("ready", snapshot["status"])
        self.assertEqual("not_probed", snapshot["database"]["status"])
        health_probe.assert_not_called()
        sqlite_connect.assert_not_called()

    def test_invalid_runtime_config_is_blocked_without_exception_details(self) -> None:
        self.capability_config.write_text(
            '{"schema_version":"broken"}',
            encoding="utf-8",
        )

        snapshot = self._snapshot()

        self.assertEqual("blocked", snapshot["status"])
        self.assertEqual("core_config_invalid", snapshot["blockers"][0]["code"])
        self.assertEqual([], snapshot["plugins"])
        self.assertEqual([], snapshot["capabilities"])

    def test_invalid_capability_registry_is_blocked_without_exception_details(self) -> None:
        self._write_runtime_config([str(self.root / "missing-plugin-root")])

        snapshot = self._snapshot()

        self.assertEqual("blocked", snapshot["status"])
        self.assertEqual("capability_registry_invalid", snapshot["blockers"][0]["code"])
        self.assertEqual([], snapshot["plugins"])
        self.assertEqual([], snapshot["capabilities"])

    def test_snapshot_never_contains_environment_secret_values(self) -> None:
        sentinel = "SENTINEL_HARNESS_MANAGER_SECRET"
        with mock.patch.dict(
            os.environ,
            {"aliyun_devops_pat": sentinel, "GITLAB_TOKEN": sentinel},
        ):
            snapshot = self._snapshot()

        rendered = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn(sentinel, rendered)
        self.assertFalse(snapshot["credentials_read"])

    @staticmethod
    def _sqlite_sidecar_stats(database_path: Path) -> dict[str, tuple[int, int, int, int] | None]:
        values: dict[str, tuple[int, int, int, int] | None] = {}
        for label, target in (
            ("main", database_path),
            ("wal", Path(str(database_path) + "-wal")),
            ("shm", Path(str(database_path) + "-shm")),
        ):
            try:
                snapshot = target.lstat()
            except FileNotFoundError:
                values[label] = None
            else:
                values[label] = (
                    snapshot.st_ino,
                    snapshot.st_size,
                    snapshot.st_mtime_ns,
                    snapshot.st_ctime_ns,
                )
        return values


if __name__ == "__main__":
    unittest.main()
