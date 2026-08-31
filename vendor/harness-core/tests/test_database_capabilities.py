from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.capability_contracts import CapabilityRequest, CapabilityResult
from app.capability_registry import CapabilityRegistry
from app.capability_runtime import CapabilityRuntime
from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = PLUGIN_SOURCE_ROOT / "his-engineering"
ENTRYPOINT = PLUGIN_ROOT / "scripts" / "database_read.py"
EXACT_READ_SCOPES = ["database:metadata:read", "database:rows:read"]


def load_database_read():
    module_name = "his_engineering_database_read"
    spec = importlib.util.spec_from_file_location(module_name, ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def request_payload(
    root: Path,
    policy: Path,
    *,
    outer_mode: str = "preview",
    input_mode: str = "plan",
    explicit: bool = False,
    scopes: list[str] | None = None,
    sql: str = "SELECT code, value FROM his_test.his_config WHERE code = %(code)s",
    parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "his-capability-request.v1",
        "request_id": "database-inspect-1",
        "capability": "database.inspect",
        "provider": "postgresql",
        "mode": outer_mode,
        "mutation_level": "L1",
        "authorization": {
            "explicit": explicit,
            "scope": [] if scopes is None else scopes,
        },
        "input": {
            "subject": "确认某配置字段在测试库的取值",
            "keywords": ["配置字段"],
            "sql": sql,
            "parameters": {"code": "EXAMPLE"} if parameters is None else parameters,
            "project_root": str(root),
            "profile_policy": str(policy),
            "mode": input_mode,
        },
        "context": {},
    }


def write_policy(path: Path, *profiles: str, environment: str = "test") -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0-pg-evidence-profiles",
                "default_mode": "off",
                "profiles": {
                    name: {
                        "environment": environment,
                        "enabled": True,
                        "max_rows": 2,
                        "connect_timeout_seconds": 5,
                        "query_timeout_seconds": 10,
                        "total_timeout_seconds": 45,
                        "max_metadata_queries": 3,
                        "sensitive_column_patterns": ["patient", "phone"],
                    }
                    for name in profiles
                },
            }
        ),
        encoding="utf-8",
    )


def credentials(*profiles: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in profiles:
        result.update(
            {
                f"pg_{name}_readonly_dsn": f"postgresql://secret-user:secret-password@secret-host/{name}",
                f"pg_{name}_readonly_user": "secret-user",
                f"pg_{name}_readonly_password": "secret-password",
            }
        )
    return result


class FakeExecutor:
    def __init__(
        self,
        *,
        rows: list[dict[str, object]] | None = None,
        metadata: list[dict[str, str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.rows = [] if rows is None else rows
        self.metadata = [] if metadata is None else metadata
        self.error = error
        self.calls: list[str] = []

    def discover_metadata(self, **kwargs: object) -> list[dict[str, str]]:
        self.calls.append("metadata")
        return self.metadata

    def execute_select(self, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append("select")
        if self.error is not None:
            raise self.error
        return self.rows


class DatabaseInspectCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_read = load_database_read()
        self.temp = tempfile.TemporaryDirectory(prefix="database-capability-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = self.root / "policy.json"
        write_policy(self.policy, "his_test")

    def test_preview_forces_plan_and_never_creates_driver_even_when_input_says_execute(self) -> None:
        factory_calls = 0

        def forbidden_factory(*args: object, **kwargs: object) -> object:
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("preview must not create a database driver")

        payload = request_payload(self.root, self.policy, input_mode="execute")
        result = self.database_read.execute_request(
            payload,
            executor_factory=forbidden_factory,
            environ=credentials("his_test"),
        )

        CapabilityResult.from_dict(result, request=CapabilityRequest.from_dict(payload))
        self.assertEqual("success", result["status"])
        self.assertEqual("planned", result["data"]["pg_status"])
        self.assertEqual("plan", result["data"]["effective_mode"])
        self.assertEqual(0, factory_calls)
        self.assertFalse(result["changed"])
        self.assertFalse(result["audit"]["database_connection_attempted"])

    def test_direct_main_ignores_ambient_readonly_credentials_even_with_forged_runtime_marker(self) -> None:
        request_file = self.root / "request.json"
        output_file = self.root / "result.json"
        request_file.write_text(
            json.dumps(
                request_payload(
                    self.root,
                    self.policy,
                    outer_mode="apply",
                    input_mode="execute",
                    explicit=True,
                    scopes=EXACT_READ_SCOPES,
                )
            ),
            encoding="utf-8",
        )
        factory_calls = 0

        def forbidden_factory(*args: object, **kwargs: object) -> object:
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("direct main must not consume ambient credentials")

        ambient = {
            **credentials("his_test"),
            "HARNESS_CAPABILITY_RUNTIME_INVOCATION": "his-capability-runtime.v1",
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "UNRELATED_SECRET": "must-not-be-consumed",
        }
        with mock.patch.dict(os.environ, ambient, clear=True), mock.patch.object(
            self.database_read,
            "build_psycopg_executor_factory",
            forbidden_factory,
        ):
            exit_code = self.database_read.main(
                [
                    "--request",
                    str(request_file),
                    "--output",
                    str(output_file),
                ]
            )

        result = json.loads(output_file.read_text(encoding="utf-8"))
        self.assertEqual(0, exit_code)
        self.assertEqual(0, factory_calls)
        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["audit"]["database_connection_attempted"])

    def test_registry_runtime_uses_explicit_credential_transport_without_child_database_environment(self) -> None:
        plugin_root = self.root / "runtime-plugin"
        scripts = plugin_root / "scripts"
        scripts.mkdir(parents=True)
        entrypoint_source = ENTRYPOINT.read_text(encoding="utf-8").replace(
            "import os\n",
            "import os\n\n"
            "if any(key.startswith('pg_') for key in os.environ):\n"
            "    raise RuntimeError('database credentials leaked through child environment')\n",
            1,
        )
        (scripts / "database_read.py").write_text(entrypoint_source, encoding="utf-8")
        shutil.copy2(PLUGIN_ROOT / "scripts" / "pg_evidence.py", scripts / "pg_evidence.py")
        (plugin_root / "capabilities.json").write_text(
            json.dumps(
                {
                    "schema_version": "his-capabilities.v1",
                    "plugin": "his-engineering",
                    "plugin_version": "0.1.0",
                    "capabilities": [
                        {
                            "name": "database.inspect",
                            "provider": "postgresql",
                            "contract_version": "pg-evidence.v2",
                            "mutation_level": "L1",
                            "credential_class": "database_readonly",
                            "entrypoint": "scripts/database_read.py",
                            "dependencies": ["scripts/pg_evidence.py"],
                            "enabled": True,
                            "scopes": EXACT_READ_SCOPES,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        marker_name = "HARNESS_CAPABILITY_RUNTIME_INVOCATION"
        environment = {
            **credentials("his_test"),
            marker_name: "attacker-controlled-value",
        }
        allowlist = tuple(sorted(environment))
        runtime = CapabilityRuntime(
            CapabilityRegistry.from_plugin_roots([plugin_root]),
            environment_allowlist=allowlist,
        )
        payload = request_payload(self.root, self.policy, input_mode="execute")

        execution = runtime.execute(
            CapabilityRequest.from_dict(payload),
            environment=environment,
        )

        self.assertEqual(
            "success",
            execution.result.status,
            execution.result.to_dict(),
        )
        self.assertFalse(execution.result.changed)
        self.assertEqual("planned", execution.result.data["pg_status"])
        self.assertEqual("plan", execution.result.data["effective_mode"])
        self.assertEqual("his_test", execution.result.data["plan"]["selected_profile"])
        self.assertFalse(
            execution.result.audit["provider"]["database_connection_attempted"]
        )
        self.assertEqual(
            sorted(credentials("his_test")),
            execution.result.audit["runtime"]["environment_keys"],
        )
        self.assertNotIn(
            marker_name,
            json.dumps(execution.result.to_dict(), ensure_ascii=False),
        )

    def test_runtime_blocks_database_dependency_replacement_after_registry_snapshot(self) -> None:
        plugin_root = self.root / "dependency-drift-plugin"
        scripts = plugin_root / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(ENTRYPOINT, scripts / "database_read.py")
        dependency = scripts / "pg_evidence.py"
        shutil.copy2(
            PLUGIN_ROOT / "scripts" / "pg_evidence.py",
            dependency,
        )
        (plugin_root / "capabilities.json").write_text(
            json.dumps(
                {
                    "schema_version": "his-capabilities.v1",
                    "plugin": "his-engineering",
                    "plugin_version": "0.1.0",
                    "capabilities": [
                        {
                            "name": "database.inspect",
                            "provider": "postgresql",
                            "contract_version": "pg-evidence.v2",
                            "mutation_level": "L1",
                            "credential_class": "database_readonly",
                            "entrypoint": "scripts/database_read.py",
                            "dependencies": ["scripts/pg_evidence.py"],
                            "enabled": True,
                            "scopes": EXACT_READ_SCOPES,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        environment = credentials("his_test")
        runtime = CapabilityRuntime(
            CapabilityRegistry.from_plugin_roots([plugin_root]),
            environment_allowlist=tuple(sorted(environment)),
        )
        marker = self.root / "replacement-dependency-ran.json"
        dependency.write_text(
            "import json, os, sys\n"
            "runtime_file = '--runtime-environment-file' in sys.argv\n"
            "secret_seen = any(key.startswith('pg_') for key in os.environ)\n"
            f"open({str(marker)!r}, 'w', encoding='utf-8').write("
            "json.dumps({'runtime_file': runtime_file, 'secret_seen': secret_seen}))\n"
            "raise RuntimeError('replacement dependency executed')\n",
            encoding="utf-8",
        )

        execution = runtime.execute(
            CapabilityRequest.from_dict(
                request_payload(self.root, self.policy)
            ),
            environment=environment,
        )

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual(
            "CAPABILITY_ENTRYPOINT_INVALID",
            execution.result.audit["error_code"],
        )
        self.assertFalse(marker.exists())
        self.assertNotIn(
            "secret-",
            json.dumps(execution.result.to_dict(), ensure_ascii=False),
        )

    def test_database_credentials_are_withheld_from_inexact_scope_or_entrypoint_descriptors(self) -> None:
        probe_source = """
import json
import os
import sys

arguments = sys.argv[1:]
request_path = arguments[arguments.index("--request") + 1]
output_path = arguments[arguments.index("--output") + 1]
received_runtime_file = "--runtime-environment-file" in arguments
runtime_values = []
if received_runtime_file:
    runtime_path = arguments[arguments.index("--runtime-environment-file") + 1]
    runtime_values = list(json.load(open(runtime_path, encoding="utf-8"))["environment"].values())
ambient_values = [
    value for key, value in os.environ.items()
    if key.startswith("pg_")
]
request = json.load(open(request_path, encoding="utf-8"))
result = {
    "schema_version": "his-capability-result.v1",
    "request_id": request["request_id"],
    "capability": request["capability"],
    "provider": request["provider"],
    "status": "success",
    "mutation_level": request["mutation_level"],
    "changed": False,
    "summary": "probe",
    "data": {
        "received_runtime_file": received_runtime_file,
        "secret_seen": bool(runtime_values or ambient_values),
    },
    "evidence": [],
    "warnings": [],
    "blockers": [],
    "audit": {},
}
open(output_path, "w", encoding="utf-8").write(json.dumps(result))
"""
        cases = (
            ("scripts/evil.py", EXACT_READ_SCOPES),
            ("scripts/database_read.py", ["database:metadata:read"]),
        )
        for index, (entrypoint, scopes) in enumerate(cases):
            with self.subTest(entrypoint=entrypoint, scopes=scopes):
                plugin_root = self.root / f"inexact-plugin-{index}"
                script_path = plugin_root / entrypoint
                script_path.parent.mkdir(parents=True)
                script_path.write_text(probe_source, encoding="utf-8")
                (plugin_root / "scripts" / "pg_evidence.py").write_text(
                    "VALUE = 'frozen dependency fixture'\n",
                    encoding="utf-8",
                )
                (plugin_root / "capabilities.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "his-capabilities.v1",
                            "plugin": "his-engineering",
                            "plugin_version": "0.1.0",
                            "capabilities": [
                                {
                                    "name": "database.inspect",
                                    "provider": "postgresql",
                                    "contract_version": "pg-evidence.v2",
                                    "mutation_level": "L1",
                                    "credential_class": "database_readonly",
                                    "entrypoint": entrypoint,
                                    "dependencies": ["scripts/pg_evidence.py"],
                                    "enabled": True,
                                    "scopes": scopes,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                environment = credentials("his_test")
                runtime = CapabilityRuntime(
                    CapabilityRegistry.from_plugin_roots([plugin_root]),
                    environment_allowlist=tuple(sorted(environment)),
                )

                execution = runtime.execute(
                    CapabilityRequest.from_dict(
                        request_payload(self.root, self.policy)
                    ),
                    environment=environment,
                )

                self.assertEqual("success", execution.result.status)
                self.assertFalse(execution.result.data["received_runtime_file"])
                self.assertFalse(execution.result.data["secret_seen"])
                self.assertEqual(
                    [],
                    execution.result.audit["runtime"]["environment_keys"],
                )
                self.assertNotIn(
                    "secret-",
                    json.dumps(execution.result.to_dict(), ensure_ascii=False),
                )

    def test_runtime_environment_rejects_same_size_content_replacement_with_restored_mtime(self) -> None:
        runtime_path = self.root / "database-runtime-environment.json"
        request_path = self.root / "request.json"
        output_path = self.root / "result.json"
        original = json.dumps(
            {
                "schema_version": "his-database-runtime-environment.v1",
                "environment": {
                    "pg_his_test_readonly_password": "secret-A",
                },
            },
            sort_keys=True,
        ).encode("utf-8")
        replacement = original.replace(b"secret-A", b"secret-B")
        self.assertEqual(len(original), len(replacement))
        runtime_path.write_bytes(original)
        runtime_path.chmod(0o400)
        initial = runtime_path.stat()
        expected_sha256 = hashlib.sha256(original).hexdigest()
        runtime_path.chmod(0o600)
        runtime_path.write_bytes(replacement)
        runtime_path.chmod(0o400)
        os.utime(
            runtime_path,
            ns=(initial.st_atime_ns, initial.st_mtime_ns),
        )
        changed = runtime_path.stat()
        self.assertEqual(
            (
                initial.st_dev,
                initial.st_ino,
                initial.st_mode,
                initial.st_size,
                initial.st_mtime_ns,
            ),
            (
                changed.st_dev,
                changed.st_ino,
                changed.st_mode,
                changed.st_size,
                changed.st_mtime_ns,
            ),
        )

        with self.assertRaises(ValueError):
            self.database_read._read_runtime_environment(
                runtime_path,
                request_path=request_path,
                output_path=output_path,
                expected_sha256=expected_sha256,
            )

    def test_apply_requires_explicit_l1_exact_scopes_and_uses_only_injected_fake_executor(self) -> None:
        executor = FakeExecutor(
            rows=[
                {"patient_phone": "13800138000", "value": "SAFE"},
                {"patient_phone": "13800138001", "value": "SAFE-2"},
                {"patient_phone": "13800138002", "value": "TOO-MANY"},
            ]
        )
        factory_calls = 0

        def factory(*, plan: object) -> FakeExecutor:
            nonlocal factory_calls
            factory_calls += 1
            return executor

        payload = request_payload(
            self.root,
            self.policy,
            outer_mode="apply",
            input_mode="execute",
            explicit=True,
            scopes=EXACT_READ_SCOPES,
        )
        result = self.database_read.execute_request(
            payload,
            executor_factory=factory,
            environ=credentials("his_test"),
        )

        CapabilityResult.from_dict(result, request=CapabilityRequest.from_dict(payload))
        self.assertEqual("success", result["status"])
        self.assertEqual("passed", result["data"]["pg_status"])
        self.assertEqual(["select"], executor.calls)
        self.assertEqual(1, factory_calls)
        self.assertEqual(2, result["audit"]["row_count"])
        self.assertEqual(["patient_phone"], result["audit"]["masked_columns"])
        self.assertEqual(
            [
                {"patient_phone": "[REDACTED]", "value": "SAFE"},
                {"patient_phone": "[REDACTED]", "value": "SAFE-2"},
            ],
            list(result["data"]["result"]["rows"]),
        )
        self.assertEqual(
            ["code"],
            result["data"]["plan"]["parameter_names"],
        )
        self.assertNotIn("parameters", result["data"]["plan"])
        self.assertFalse(result["changed"])
        self.assertTrue(result["audit"]["database_connection_attempted"])
        self.assertEqual(
            {
                "credential_class",
                "external_write_attempted",
                "database_connection_attempted",
                "query_template_id",
                "parameter_audit",
                "profile",
                "row_count",
                "masked_columns",
            },
            set(result["audit"]),
        )
        serialized = json.dumps(result, ensure_ascii=False)
        for secret in (
            "EXAMPLE",
            "13800138000",
            "13800138001",
            "secret-user",
            "secret-password",
            "secret-host",
        ):
            self.assertNotIn(secret, serialized)

    def test_authorization_policy_and_sql_guard_each_block_before_factory(self) -> None:
        cases: list[tuple[str, dict[str, object], dict[str, str]]] = []
        cases.append(
            (
                "authorization",
                request_payload(
                    self.root,
                    self.policy,
                    outer_mode="apply",
                    input_mode="execute",
                    explicit=False,
                    scopes=["database:rows:read"],
                ),
                credentials("his_test"),
            )
        )
        production_policy = self.root / "production.json"
        write_policy(production_policy, "production", environment="production")
        cases.append(
            (
                "profile_policy",
                request_payload(
                    self.root,
                    production_policy,
                    outer_mode="apply",
                    input_mode="execute",
                    explicit=True,
                    scopes=EXACT_READ_SCOPES,
                    sql="SELECT code FROM production.his_config",
                    parameters={},
                ),
                credentials("production"),
            )
        )
        cases.append(
            (
                "sql_guard",
                request_payload(
                    self.root,
                    self.policy,
                    outer_mode="apply",
                    input_mode="execute",
                    explicit=True,
                    scopes=EXACT_READ_SCOPES,
                    sql="UPDATE his_test.his_config SET value = %(value)s",
                    parameters={"value": "forbidden"},
                ),
                credentials("his_test"),
            )
        )

        for name, payload, environment in cases:
            with self.subTest(layer=name):
                calls = 0

                def forbidden_factory(*args: object, **kwargs: object) -> object:
                    nonlocal calls
                    calls += 1
                    raise AssertionError("blocked request must not create a driver")

                result = self.database_read.execute_request(
                    payload,
                    executor_factory=forbidden_factory,
                    environ=environment,
                )

                CapabilityResult.from_dict(result, request=CapabilityRequest.from_dict(payload))
                self.assertEqual("blocked", result["status"])
                self.assertEqual(0, calls)
                self.assertFalse(result["changed"])
                self.assertFalse(result["audit"]["database_connection_attempted"])

    def test_status_mapping_is_fail_closed_for_needs_evidence_timeout_and_failure(self) -> None:
        ambiguous_policy = self.root / "ambiguous.json"
        write_policy(ambiguous_policy, "his_a", "his_b")
        ambiguous = request_payload(
            self.root,
            ambiguous_policy,
            outer_mode="apply",
            input_mode="execute",
            explicit=True,
            scopes=EXACT_READ_SCOPES,
            sql="SELECT code FROM his_config",
            parameters={},
        )
        needs_evidence = self.database_read.execute_request(
            ambiguous,
            executor_factory=lambda *, plan: FakeExecutor(),
            environ=credentials("his_a", "his_b"),
        )
        self.assertEqual("blocked", needs_evidence["status"])
        self.assertEqual("needs_evidence", needs_evidence["data"]["pg_status"])

        for error, expected in (
            (TimeoutError("secret-host timeout"), "timeout"),
            (RuntimeError("secret-password failure"), "failed"),
        ):
            with self.subTest(expected=expected):
                payload = request_payload(
                    self.root,
                    self.policy,
                    outer_mode="apply",
                    input_mode="execute",
                    explicit=True,
                    scopes=EXACT_READ_SCOPES,
                )
                result = self.database_read.execute_request(
                    payload,
                    executor_factory=lambda *, plan, error=error: FakeExecutor(error=error),
                    environ=credentials("his_test"),
                )
                self.assertEqual("failed", result["status"])
                self.assertEqual(expected, result["data"]["pg_status"])
                self.assertNotIn("secret", json.dumps(result, ensure_ascii=False))

    def test_request_cannot_inject_executor_or_relax_identity_contract(self) -> None:
        base = request_payload(self.root, self.policy)
        cases = []
        injected = json.loads(json.dumps(base))
        injected["input"]["executor_factory"] = "attacker"
        cases.append(injected)
        wrong_provider = json.loads(json.dumps(base))
        wrong_provider["provider"] = "his-engineering"
        cases.append(wrong_provider)
        wrong_level = json.loads(json.dumps(base))
        wrong_level["mutation_level"] = "L0"
        cases.append(wrong_level)
        nonempty_context = json.loads(json.dumps(base))
        nonempty_context["context"] = {"credential": "attacker"}
        cases.append(nonempty_context)

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.database_read.execute_request(
                        payload,
                        executor_factory=lambda *, plan: FakeExecutor(),
                        environ=credentials("his_test"),
                    )


if __name__ == "__main__":
    unittest.main()
