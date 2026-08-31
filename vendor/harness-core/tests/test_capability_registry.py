from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.capability_contracts import MutationLevel
from app.capability_registry import (
    CapabilityAmbiguityError,
    CapabilityManifestError,
    CapabilityRegistry,
)


FIXTURES = Path(__file__).parent / "fixtures" / "capabilities"


class CapabilityRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        shutil.copytree(FIXTURES / "yunxiao", self.root / "yunxiao")
        self.plugin_root = self.root / "yunxiao"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _payload(self) -> dict:
        return json.loads((self.plugin_root / "capabilities.json").read_text())

    def _write_payload(self, payload: dict) -> None:
        (self.plugin_root / "capabilities.json").write_text(json.dumps(payload))

    def _load(self) -> CapabilityRegistry:
        return CapabilityRegistry.from_plugin_roots([self.plugin_root])

    def test_loads_fixed_manifest_and_resolves_exact_provider(self) -> None:
        registry = self._load()
        descriptor = registry.resolve("workitem.read", "yunxiao")

        self.assertEqual("yunxiao", descriptor.plugin)
        self.assertEqual("0.1.0", descriptor.plugin_version)
        self.assertEqual(MutationLevel.L1, descriptor.mutation_level)
        self.assertEqual((self.plugin_root / "scripts/workitem_read.py").resolve(), descriptor.entrypoint)
        self.assertTrue(descriptor.enabled)
        self.assertEqual(("workitem:read",), descriptor.scopes)
        self.assertEqual((), descriptor.dependency_identities)
        self.assertEqual((descriptor,), registry.descriptors)
        self.assertEqual(1, len(registry))

    def test_loads_declared_dependency_with_frozen_identity(self) -> None:
        dependency = self.plugin_root / "scripts" / "helper.py"
        dependency.write_text("VALUE = 'original'\n", encoding="utf-8")
        payload = self._payload()
        payload["capabilities"][0]["dependencies"] = ["scripts/helper.py"]
        self._write_payload(payload)

        descriptor = self._load().resolve("workitem.read", "yunxiao")

        self.assertEqual(1, len(descriptor.dependency_identities))
        path, identity = descriptor.dependency_identities[0]
        self.assertEqual(dependency.resolve(), path)
        self.assertEqual(64, len(identity[5]))

    def test_rejects_invalid_dependency_declarations(self) -> None:
        outside = self.root / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        internal = self.plugin_root / "scripts" / "helper.py"
        internal.write_text("helper\n", encoding="utf-8")
        symlink = self.plugin_root / "scripts" / "helper-link.py"
        symlink.symlink_to("helper.py")
        cases = (
            (["scripts/helper.py", "scripts/helper.py"], "dependencies"),
            (["../outside.py"], "dependencies"),
            ([str(outside)], "dependencies"),
            (["scripts/missing.py"], "dependencies"),
            (["scripts"], "dependencies"),
            (["scripts/helper-link.py"], "dependencies"),
            ([""], "dependencies"),
            ([1], "dependencies"),
        )
        for dependencies, message in cases:
            with self.subTest(dependencies=dependencies):
                payload = self._payload()
                payload["capabilities"][0]["dependencies"] = dependencies
                self._write_payload(payload)

                with self.assertRaisesRegex(CapabilityManifestError, message):
                    self._load()

    def test_rejects_dependency_that_resolves_to_entrypoint(self) -> None:
        for dependency in (
            "scripts/workitem_read.py",
            "scripts/./workitem_read.py",
        ):
            with self.subTest(dependency=dependency):
                payload = self._payload()
                payload["capabilities"][0]["dependencies"] = [dependency]
                self._write_payload(payload)

                with self.assertRaisesRegex(
                    CapabilityManifestError,
                    "dependencies 不能包含 entrypoint",
                ):
                    self._load()

    def test_rejects_dependencies_when_disabled_capability_omits_entrypoint(self) -> None:
        payload = self._payload()
        capability = payload["capabilities"][0]
        capability["enabled"] = False
        capability["disabled_reason"] = "not configured"
        del capability["entrypoint"]
        capability["dependencies"] = ["scripts/workitem_read.py"]
        self._write_payload(payload)

        with self.assertRaisesRegex(
            CapabilityManifestError,
            "声明 dependencies 时必须声明 entrypoint",
        ):
            self._load()

    def test_rejects_duplicate_capability_provider(self) -> None:
        payload = self._payload()
        payload["capabilities"].append(dict(payload["capabilities"][0]))
        self._write_payload(payload)

        with self.assertRaisesRegex(CapabilityManifestError, "重复 capability/provider"):
            self._load()

    def test_rejects_unknown_mutation_level(self) -> None:
        payload = self._payload()
        payload["capabilities"][0]["mutation_level"] = "L9"
        self._write_payload(payload)

        with self.assertRaisesRegex(CapabilityManifestError, "mutation_level"):
            self._load()

    def test_rejects_enabled_capability_without_existing_entrypoint(self) -> None:
        payload = self._payload()
        payload["capabilities"][0]["entrypoint"] = "scripts/missing.py"
        self._write_payload(payload)

        with self.assertRaisesRegex(CapabilityManifestError, "entrypoint"):
            self._load()

    def test_rejects_enabled_capability_with_directory_entrypoint(self) -> None:
        payload = self._payload()
        payload["capabilities"][0]["entrypoint"] = "scripts"
        self._write_payload(payload)

        with self.assertRaisesRegex(CapabilityManifestError, "entrypoint 必须是文件"):
            self._load()

    def test_rejects_absolute_parent_and_escaping_symlink_entrypoints(self) -> None:
        for entrypoint in ("/tmp/workitem_read.py", "../outside.py"):
            with self.subTest(entrypoint=entrypoint):
                payload = self._payload()
                payload["capabilities"][0]["entrypoint"] = entrypoint
                self._write_payload(payload)
                with self.assertRaisesRegex(CapabilityManifestError, "entrypoint"):
                    self._load()

        outside = self.root / "outside.py"
        outside.write_text("outside")
        (self.plugin_root / "scripts/escape.py").symlink_to(outside)
        payload = self._payload()
        payload["capabilities"][0]["entrypoint"] = "scripts/escape.py"
        self._write_payload(payload)
        with self.assertRaisesRegex(CapabilityManifestError, "entrypoint"):
            self._load()

    def test_accepts_internal_symlink_entrypoint(self) -> None:
        (self.plugin_root / "scripts/internal.py").symlink_to("workitem_read.py")
        payload = self._payload()
        payload["capabilities"][0]["entrypoint"] = "scripts/internal.py"
        self._write_payload(payload)

        descriptor = self._load().resolve("workitem.read", "yunxiao")
        self.assertEqual((self.plugin_root / "scripts/internal.py").resolve(), descriptor.entrypoint)

    def test_disabled_capability_may_omit_entrypoint_but_requires_reason(self) -> None:
        payload = self._payload()
        capability = payload["capabilities"][0]
        capability["enabled"] = False
        del capability["entrypoint"]
        capability["disabled_reason"] = "not configured"
        self._write_payload(payload)

        descriptor = self._load().resolve("workitem.read", "yunxiao")
        self.assertIsNone(descriptor.entrypoint)
        self.assertEqual("not configured", descriptor.disabled_reason)

        capability["disabled_reason"] = ""
        self._write_payload(payload)
        with self.assertRaisesRegex(CapabilityManifestError, "disabled_reason"):
            self._load()

    def test_rejects_explicit_null_entrypoint_for_disabled_capability(self) -> None:
        payload = self._payload()
        capability = payload["capabilities"][0]
        capability["enabled"] = False
        capability["disabled_reason"] = "not configured"
        capability["entrypoint"] = None
        self._write_payload(payload)

        with self.assertRaisesRegex(CapabilityManifestError, "entrypoint"):
            self._load()

    def test_capability_only_resolution_is_deterministically_ambiguous(self) -> None:
        second_root = self.root / "second"
        shutil.copytree(self.plugin_root, second_root)
        payload = json.loads((second_root / "capabilities.json").read_text())
        payload["plugin"] = "second"
        payload["capabilities"][0]["provider"] = "other"
        (second_root / "capabilities.json").write_text(json.dumps(payload))
        registry = CapabilityRegistry.from_plugin_roots([self.plugin_root, second_root])

        with self.assertRaisesRegex(
            CapabilityAmbiguityError,
            r"capability 'workitem.read' 存在多个 provider: other, yunxiao。",
        ):
            registry.resolve("workitem.read")

    def test_rejects_missing_plugin_root_or_manifest_and_invalid_scopes(self) -> None:
        with self.assertRaisesRegex(CapabilityManifestError, "插件根目录不存在"):
            CapabilityRegistry.from_plugin_roots([self.root / "missing"])

        (self.plugin_root / "capabilities.json").unlink()
        with self.assertRaisesRegex(CapabilityManifestError, "capabilities.json 不存在"):
            self._load()

        shutil.copy2(FIXTURES / "yunxiao" / "capabilities.json", self.plugin_root / "capabilities.json")
        for scopes in (["workitem:read", "workitem:read"], [" "], []):
            with self.subTest(scopes=scopes):
                payload = self._payload()
                payload["capabilities"][0]["scopes"] = scopes
                self._write_payload(payload)
                with self.assertRaisesRegex(CapabilityManifestError, "scopes"):
                    self._load()

    def test_rejects_unknown_fields_and_blank_name_fields(self) -> None:
        payload = self._payload()
        payload["extra"] = True
        self._write_payload(payload)
        with self.assertRaisesRegex(CapabilityManifestError, "未知字段"):
            self._load()

        del payload["extra"]
        payload["capabilities"][0]["credential_class"] = ""
        self._write_payload(payload)
        with self.assertRaisesRegex(CapabilityManifestError, "credential_class"):
            self._load()


if __name__ == "__main__":
    unittest.main()
