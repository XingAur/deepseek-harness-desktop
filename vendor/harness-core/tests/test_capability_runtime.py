from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from unittest import mock

from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityRequest,
    MutationLevel,
)
from app.capability_registry import CapabilityRegistry, _path_identity
from app.capability_runtime import CapabilityRuntime


class CapabilityRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "plugin"
        self.scripts = self.root / "scripts"
        self.scripts.mkdir(parents=True)
        self.script = self.scripts / "runner.py"
        self._write_manifest()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_manifest(self, *, enabled: bool = True) -> None:
        payload = {
            "schema_version": "his-capabilities.v1",
            "plugin": "test-plugin",
            "plugin_version": "0.1.0",
            "capabilities": [{
                "name": "test.run",
                "provider": "local",
                "contract_version": "v1",
                "mutation_level": "L1",
                "credential_class": "none",
                "entrypoint": "scripts/runner.py" if enabled else None,
                "enabled": enabled,
                "disabled_reason": "disabled for test" if not enabled else "",
                "scopes": ["target:local"],
            }],
        }
        if not enabled:
            del payload["capabilities"][0]["entrypoint"]
        (self.root / "capabilities.json").write_text(json.dumps(payload), encoding="utf-8")

    def _write_script(self, body: str) -> None:
        self.script.write_text(
            "import json, os, sys\n"
            "request_path = sys.argv[sys.argv.index('--request') + 1]\n"
            "output_path = sys.argv[sys.argv.index('--output') + 1]\n"
            "request = json.loads(open(request_path, encoding='utf-8').read())\n"
            + body,
            encoding="utf-8",
        )

    def _result_script(self, *, changed: bool = False, request_id: str = "request['request_id']") -> str:
        return (
            (
                "result = {'schema_version': 'his-capability-result.v1', "
                f"'request_id': {request_id}, 'capability': 'test.run', 'provider': 'local', "
                "'status': 'success', 'mutation_level': 'L1', "
                f"'changed': {changed!r}, 'summary': 'ok', 'data': {{}}, 'evidence': [], "
                "'warnings': [], 'blockers': [], 'audit': {}}\n"
            )
            + ("result['audit'] = {'event': 'changed'}\n" if changed else "")
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )

    def _request(self, *, mode: str = "preview") -> CapabilityRequest:
        return CapabilityRequest(
            request_id="request-1",
            capability="test.run",
            provider="local",
            mode=mode,
            mutation_level=MutationLevel.L1,
            authorization=CapabilityAuthorization(explicit=False, scope=()),
            input={},
            context={},
        )

    def _runtime(self, **kwargs: object) -> CapabilityRuntime:
        return CapabilityRuntime(CapabilityRegistry.from_plugin_roots([self.root]), **kwargs)

    def test_executes_valid_result_and_uses_only_allowlisted_environment(self) -> None:
        self._write_script(
            self._result_script()[:-1]
            + "\nresult['data'] = {'seen': sorted(os.environ)}\n"
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )

        execution = self._runtime(environment_allowlist=("SAFE_KEY",)).execute(
            self._request(), environment={"SAFE_KEY": "safe", "SECRET_KEY": "secret"}
        )

        self.assertEqual("success", execution.result.status)
        self.assertIn("SAFE_KEY", execution.result.data["seen"])
        self.assertNotIn("SECRET_KEY", execution.result.data["seen"])
        self.assertEqual(["SAFE_KEY"], execution.result.audit["runtime"]["environment_keys"])
        self.assertNotIn("secret", json.dumps(execution.result.to_dict()))

    def test_child_uses_internal_private_tmpdir_outside_allowlist_and_audit(self) -> None:
        self._write_script(
            self._result_script()[:-1]
            + "\nresult['data'] = {"
            "'tmpdir_is_execution_dir': "
            "os.environ.get('TMPDIR') == os.path.dirname(request_path)}\n"
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )

        execution = self._runtime(
            environment_allowlist=("SAFE_KEY", "TMPDIR")
        ).execute(
            self._request(),
            environment={"SAFE_KEY": "safe", "TMPDIR": "/untrusted/tmpdir"},
        )

        self.assertEqual("success", execution.result.status)
        self.assertTrue(execution.result.data["tmpdir_is_execution_dir"])
        self.assertEqual(
            ["SAFE_KEY"],
            execution.result.audit["runtime"]["environment_keys"],
        )

    def test_resolves_execution_directory_before_passing_paths_to_provider(self) -> None:
        self._write_script(
            "if os.path.realpath(output_path) != output_path: sys.exit(9)\n"
            + self._result_script()
        )
        real_execution_directory = tempfile.TemporaryDirectory()
        alias = Path(real_execution_directory.name).parent / (
            Path(real_execution_directory.name).name + "-alias"
        )
        alias.symlink_to(real_execution_directory.name, target_is_directory=True)

        class AliasedTemporaryDirectory:
            def __enter__(self):
                return str(alias)

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        try:
            with mock.patch(
                "app.capability_runtime.tempfile.TemporaryDirectory",
                return_value=AliasedTemporaryDirectory(),
            ):
                execution = self._runtime().execute(self._request())
        finally:
            alias.unlink()
            real_execution_directory.cleanup()

        self.assertEqual("success", execution.result.status)

    def test_preflight_resolves_descriptor_and_permission_without_running_provider(self) -> None:
        marker = self.root / "ran"
        self._write_script(
            f"open({str(marker)!r}, 'w').write('ran')\n" + self._result_script()
        )

        preflight = self._runtime().preflight(self._request())

        self.assertEqual("test.run", preflight.descriptor.name)
        self.assertEqual("local", preflight.descriptor.provider)
        self.assertEqual(MutationLevel.L1, preflight.descriptor.mutation_level)
        self.assertTrue(preflight.descriptor.enabled)
        self.assertTrue(preflight.permission.allowed)
        self.assertFalse(marker.exists())

    def test_preserves_provider_audit_and_adds_runtime_environment_audit(self) -> None:
        self._write_script(
            self._result_script()[:-1]
            + "\nresult['audit'] = {'provider_event': 'read', 'runtime': {'provider_key': 'keep'}}\n"
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )

        execution = self._runtime(environment_allowlist=("SAFE_KEY",)).execute(
            self._request(), environment={"SAFE_KEY": "safe"}
        )

        self.assertEqual(
            {"provider_event": "read", "runtime": {"provider_key": "keep"}},
            execution.result.audit["provider"],
        )
        self.assertEqual({"environment_keys": ["SAFE_KEY"]}, execution.result.audit["runtime"])

    def test_blocks_provider_audit_that_echoes_injected_environment_value(self) -> None:
        self._write_script(
            self._result_script()[:-1]
            + "\nresult['audit'] = {'nested': {'message': 'prefix-do-not-leak-suffix'}}\n"
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )

        execution = self._runtime(environment_allowlist=("SECRET_KEY",)).execute(
            self._request(), environment={"SECRET_KEY": "do-not-leak"}
        )

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual("CAPABILITY_RESULT_SENSITIVE_AUDIT", execution.result.audit["error_code"])
        self.assertNotIn("do-not-leak", json.dumps(execution.result.to_dict()))

    def test_blocks_provider_audit_that_echoes_internal_tmpdir_without_leaking_path(self) -> None:
        self._write_script(
            self._result_script()[:-1]
            + "\nresult['audit'] = {'tmpdir': os.environ['TMPDIR']}\n"
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )
        execution_directory = tempfile.TemporaryDirectory()
        internal_tmpdir = execution_directory.name

        try:
            with mock.patch(
                "app.capability_runtime.tempfile.TemporaryDirectory",
                return_value=execution_directory,
            ):
                execution = self._runtime().execute(self._request())
        finally:
            execution_directory.cleanup()

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual(
            "CAPABILITY_RESULT_SENSITIVE_AUDIT",
            execution.result.audit["error_code"],
        )
        self.assertNotIn(internal_tmpdir, json.dumps(execution.result.to_dict()))

    def test_blocks_injected_environment_values_in_all_public_result_fields(self) -> None:
        assignments = {
            "summary": "result['summary'] = os.environ['SECRET_KEY']",
            "data": "result['data'] = {'value': os.environ['SECRET_KEY']}",
            "evidence": "result['evidence'] = [{'value': os.environ['SECRET_KEY']}]",
            "warnings": "result['warnings'] = [os.environ['SECRET_KEY']]",
            "blockers": "result['blockers'] = [os.environ['SECRET_KEY']]",
        }
        for field, assignment in assignments.items():
            with self.subTest(field=field):
                self._write_script(
                    self._result_script()[:-1]
                    + f"\n{assignment}\n"
                    + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
                )

                execution = self._runtime(
                    environment_allowlist=("SECRET_KEY",)
                ).execute(
                    self._request(),
                    environment={"SECRET_KEY": "provider-secret-must-not-leak"},
                )

                self.assertEqual("blocked", execution.result.status)
                self.assertEqual(
                    "CAPABILITY_RESULT_SENSITIVE_OUTPUT",
                    execution.result.audit["error_code"],
                )
                self.assertNotIn(
                    "provider-secret-must-not-leak",
                    json.dumps(execution.result.to_dict()),
                )

    def test_uses_one_environment_snapshot_for_child_and_sensitive_audit_check(self) -> None:
        class ChangingEnvironment(Mapping[str, str]):
            def __init__(self) -> None:
                self.reads = 0

            def __getitem__(self, key: str) -> str:
                if key != "SECRET_KEY":
                    raise KeyError(key)
                self.reads += 1
                return ("first-secret", "second-secret", "third-secret")[min(self.reads - 1, 2)]

            def __iter__(self):
                return iter(("SECRET_KEY",))

            def __len__(self) -> int:
                return 1

        self._write_script(
            self._result_script()[:-1]
            + "\nresult['audit'] = {'echo': os.environ['SECRET_KEY']}\n"
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )
        environment = ChangingEnvironment()

        execution = self._runtime(environment_allowlist=("SECRET_KEY",)).execute(
            self._request(), environment=environment
        )

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual("CAPABILITY_RESULT_SENSITIVE_AUDIT", execution.result.audit["error_code"])
        self.assertEqual(1, environment.reads)
        self.assertNotIn("first-secret", json.dumps(execution.result.to_dict()))

    def test_returns_stable_failure_when_environment_mapping_read_raises(self) -> None:
        class BrokenEnvironment(Mapping[str, str]):
            def __getitem__(self, key: str) -> str:
                raise RuntimeError("mapping read failed")

            def __iter__(self):
                return iter(("SAFE_KEY",))

            def __len__(self) -> int:
                return 1

        marker = self.root / "ran"
        self._write_script(f"open({str(marker)!r}, 'w').write('ran')\n" + self._result_script())

        execution = self._runtime(environment_allowlist=("SAFE_KEY",)).execute(
            self._request(), environment=BrokenEnvironment()
        )

        self.assertEqual("failed", execution.result.status)
        self.assertEqual("CAPABILITY_ENVIRONMENT_INVALID", execution.result.audit["error_code"])
        self.assertFalse(marker.exists())

    def test_ignores_empty_injected_environment_value_when_checking_provider_audit(self) -> None:
        self._write_script(
            self._result_script()[:-1]
            + "\nresult['audit'] = {'provider_event': 'read'}\n"
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )

        execution = self._runtime(environment_allowlist=("EMPTY_KEY",)).execute(
            self._request(), environment={"EMPTY_KEY": ""}
        )

        self.assertEqual("success", execution.result.status)
        self.assertEqual("read", execution.result.audit["provider"]["provider_event"])

    def test_blocks_non_whitespace_stdout_even_when_output_is_valid(self) -> None:
        self._write_script("print('log to stdout')\n" + self._result_script())

        execution = self._runtime().execute(self._request())

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual("CAPABILITY_STDOUT_NOT_EMPTY", execution.result.audit["error_code"])

    def test_blocks_non_utf8_stdout_without_raising_decode_error(self) -> None:
        self._write_script(self._result_script() + "sys.stdout.buffer.write(b'\\xff')\n")

        execution = self._runtime().execute(self._request())

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual("CAPABILITY_STDOUT_NOT_EMPTY", execution.result.audit["error_code"])

    def test_ignores_non_utf8_stderr_without_raising_decode_error(self) -> None:
        self._write_script(self._result_script() + "sys.stderr.buffer.write(b'\\xff')\n")

        execution = self._runtime().execute(self._request())

        self.assertEqual("success", execution.result.status)

    def test_returns_sanitized_failure_for_nonzero_exit(self) -> None:
        self._write_script("print(os.environ.get('SECRET_KEY', ''), file=sys.stderr)\nsys.exit(7)\n")

        execution = self._runtime(environment_allowlist=("SECRET_KEY",)).execute(
            self._request(), environment={"SECRET_KEY": "do-not-leak"}
        )

        self.assertEqual("failed", execution.result.status)
        self.assertEqual("CAPABILITY_PROCESS_FAILED", execution.result.audit["error_code"])
        self.assertNotIn("do-not-leak", json.dumps(execution.result.to_dict()))

    def test_returns_stable_timeout_failure(self) -> None:
        self._write_script("import time\ntime.sleep(5)\n")

        execution = self._runtime(default_timeout_seconds=1).execute(self._request())

        self.assertEqual("failed", execution.result.status)
        self.assertEqual("CAPABILITY_TIMEOUT", execution.result.audit["error_code"])

    def test_uses_sixty_second_default_timeout(self) -> None:
        self._write_script(self._result_script())

        runtime = self._runtime()

        self.assertEqual(60, runtime._default_timeout_seconds)

    def test_rejects_non_positive_or_non_integer_timeout_override(self) -> None:
        self._write_script(self._result_script())
        for timeout in (0, -1, True, 1.5):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "timeout_seconds 必须为正整数"):
                    self._runtime().execute(self._request(), timeout_seconds=timeout)

    def test_returns_stable_failure_for_non_json_request(self) -> None:
        marker = self.root / "ran"
        self._write_script(f"open({str(marker)!r}, 'w').write('ran')\n" + self._result_script())
        request = CapabilityRequest(
            request_id="request-invalid", capability="test.run", provider="local", mode="preview",
            mutation_level=MutationLevel.L1,
            authorization=CapabilityAuthorization(explicit=False, scope=()),
            input={"not_json": object()}, context={},
        )

        execution = self._runtime().execute(request)

        self.assertEqual("failed", execution.result.status)
        self.assertEqual("CAPABILITY_REQUEST_INVALID", execution.result.audit["error_code"])
        self.assertFalse(marker.exists())

    def test_blocks_result_with_mismatched_request_identity(self) -> None:
        self._write_script(self._result_script(request_id="'other-request'"))

        execution = self._runtime().execute(self._request())

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual("CAPABILITY_RESULT_INVALID", execution.result.audit["error_code"])

    def test_blocks_preview_result_that_reports_changes(self) -> None:
        self._write_script(self._result_script(changed=True))

        execution = self._runtime().execute(self._request())

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual("CAPABILITY_RESULT_FORBIDDEN", execution.result.audit["error_code"])

    def test_revalidates_entrypoint_after_load_and_refuses_escaping_symlink(self) -> None:
        escaped = self.root.parent / "escaped.py"
        marker = self.root.parent / "escaped-ran"
        escaped.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
        self._write_script(self._result_script())
        runtime = self._runtime()
        self.script.unlink()
        self.script.symlink_to(escaped)

        execution = runtime.execute(self._request())

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual("CAPABILITY_ENTRYPOINT_INVALID", execution.result.audit["error_code"])
        self.assertFalse(marker.exists())

    def test_revalidates_entrypoint_full_identity_before_process_start(self) -> None:
        marker = self.root.parent / "identity-drift-ran"
        self._write_script(self._result_script())
        runtime = self._runtime()
        before = self.script.stat()
        self._write_script(
            f"open({str(marker)!r}, 'w').write('ran')\n" + self._result_script()
        )
        after = self.script.stat()
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertNotEqual(
            (before.st_size, before.st_mtime_ns),
            (after.st_size, after.st_mtime_ns),
        )

        execution = runtime.execute(self._request())

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual(
            "CAPABILITY_ENTRYPOINT_INVALID",
            execution.result.audit["error_code"],
        )
        self.assertFalse(marker.exists())

    def test_revalidates_entrypoint_content_when_metadata_is_restored(self) -> None:
        marker = self.root.parent / "content-drift-ran"
        original = ("#" * 2048) + "\n" + self._result_script()
        self._write_script(original)
        runtime = self._runtime()
        before = self.script.stat()
        changed = (
            f"open({str(marker)!r}, 'w').write('ran')\n" + self._result_script()
        )
        changed += "#" * (len(original.encode("utf-8")) - len(changed.encode("utf-8")))
        self.assertEqual(len(original.encode("utf-8")), len(changed.encode("utf-8")))
        self._write_script(changed)
        os.utime(
            self.script,
            ns=(before.st_atime_ns, before.st_mtime_ns),
        )
        after = self.script.stat()
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

        execution = runtime.execute(self._request())

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual(
            "CAPABILITY_ENTRYPOINT_INVALID",
            execution.result.audit["error_code"],
        )
        self.assertFalse(marker.exists())

    def test_executes_private_verified_snapshot_if_sources_drift_at_spawn(self) -> None:
        helper = self.scripts / "helper.py"
        original_helper = ("#" * 2048) + "\nVALUE = 'verified'\n"
        original_body = (
            ("#" * 2048)
            + "\nfrom helper import VALUE\n"
            + self._result_script()[:-1]
            + "\nresult['data'] = {'value': VALUE}\n"
            + "open(output_path, 'w', encoding='utf-8').write(json.dumps(result))\n"
        )
        real_run = subprocess.run

        for target_name in ("runner.py", "helper.py"):
            with self.subTest(target=target_name):
                helper.write_text(original_helper, encoding="utf-8")
                self._write_script(original_body)
                registry = CapabilityRegistry.from_plugin_roots([self.root])
                descriptor = registry.resolve("test.run", "local")
                registry = CapabilityRegistry([
                    replace(
                        descriptor,
                        dependency_identities=((helper.resolve(), _path_identity(helper)),),
                    )
                ])
                target = self.scripts / target_name
                marker = self.root.parent / (target_name + "-spawn-ran")
                before = target.stat()
                if target_name == "runner.py":
                    malicious = f"open({str(marker)!r}, 'w').write('ran')\n".encode("utf-8")
                else:
                    malicious = (
                        f"open({str(marker)!r}, 'w').write('ran')\n"
                        "VALUE = 'tampered'\n"
                    ).encode("utf-8")
                self.assertLess(len(malicious), before.st_size)

                def drift_then_run(command, **kwargs):
                    execution_path = Path(command[1])
                    self.assertNotEqual(self.script, execution_path)
                    self.assertEqual(0, execution_path.stat().st_mode & 0o222)
                    self.assertEqual(0, execution_path.parent.stat().st_mode & 0o222)
                    target.write_bytes(
                        malicious + (b"#" * (before.st_size - len(malicious)))
                    )
                    os.utime(
                        target,
                        ns=(before.st_atime_ns, before.st_mtime_ns),
                    )
                    return real_run(command, **kwargs)

                with mock.patch(
                    "app.capability_runtime.subprocess.run",
                    side_effect=drift_then_run,
                ):
                    execution = CapabilityRuntime(registry).execute(self._request())

                self.assertEqual("success", execution.result.status)
                self.assertEqual("verified", execution.result.data["value"])
                self.assertFalse(marker.exists())

    def test_refuses_runner_when_loaded_plugin_root_is_replaced_by_symlink(self) -> None:
        marker = self.root.parent / "external-ran"
        external = self.root.parent / "external-plugin"
        (external / "scripts").mkdir(parents=True)
        (external / "scripts" / "runner.py").write_text(
            f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8"
        )
        self._write_script(self._result_script())
        runtime = self._runtime()
        original_root = self.root.parent / "original-plugin"
        self.root.rename(original_root)
        self.root.symlink_to(external, target_is_directory=True)

        execution = runtime.execute(self._request())

        self.assertEqual("blocked", execution.result.status)
        self.assertEqual("CAPABILITY_ENTRYPOINT_INVALID", execution.result.audit["error_code"])
        self.assertFalse(marker.exists())

    def test_disabled_descriptor_and_denied_permission_do_not_start_process(self) -> None:
        marker = self.root / "ran"
        self._write_script(f"open({str(marker)!r}, 'w').write('ran')\n" + self._result_script())
        self._write_manifest(enabled=False)

        disabled = self._runtime().execute(self._request())

        self.assertEqual("blocked", disabled.result.status)
        self.assertEqual("CAPABILITY_DISABLED", disabled.result.audit["error_code"])
        self.assertFalse(marker.exists())

        self._write_manifest()
        denied_request = CapabilityRequest(
            request_id="request-2", capability="test.run", provider="local", mode="apply",
            mutation_level=MutationLevel.L2,
            authorization=CapabilityAuthorization(explicit=False, scope=("unexpected",)),
            input={}, context={},
        )
        denied = self._runtime().execute(denied_request)
        self.assertEqual("blocked", denied.result.status)
        self.assertEqual("CAPABILITY_PERMISSION_DENIED", denied.result.audit["error_code"])
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
