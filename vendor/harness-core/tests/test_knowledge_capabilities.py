from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT, REPOSITORY_ROOT


ROOT = REPOSITORY_ROOT
PLUGIN_ROOT = PLUGIN_SOURCE_ROOT / "his-knowledge"
REAL_HOME = Path("/Users/lym/.local/share/his-knowledge")
sys.path.insert(0, str(ROOT / "Harness"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from app.capability_contracts import CapabilityAuthorization, CapabilityRequest, MutationLevel
from app.capability_registry import CapabilityRegistry
from app.capability_runtime import CapabilityRuntime
import knowledge_capability


def request(name: str, *, mode: str, level: MutationLevel, scope: tuple[str, ...], payload: dict[str, object]) -> CapabilityRequest:
    return CapabilityRequest(
        request_id="knowledge-capability-" + name.replace(".", "-"),
        capability=name,
        provider="his-knowledge",
        mode=mode,
        mutation_level=level,
        authorization=CapabilityAuthorization(explicit=mode == "apply", scope=scope),
        input=payload,
        context={},
    )


class KnowledgeCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertFalse(os.path.lexists(REAL_HOME))
        self.temp = tempfile.TemporaryDirectory(prefix="his-knowledge-capability-")
        self.home = Path(self.temp.name) / "knowledge-home"
        self.registry = CapabilityRegistry.from_plugin_roots([PLUGIN_ROOT])
        self.runtime = CapabilityRuntime(self.registry, environment_allowlist=("HIS_KNOWLEDGE_HOME",))

    def tearDown(self) -> None:
        self.temp.cleanup()
        self.assertFalse(os.path.lexists(REAL_HOME))

    def execute(self, item: CapabilityRequest):
        return self.runtime.execute(item, environment={"HIS_KNOWLEDGE_HOME": str(self.home), "UNTRUSTED": "must-not-pass"})

    def cli_environment(self, *, home: Path | None = None) -> dict[str, str]:
        return {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "TMPDIR": self.temp.name,
            "HIS_KNOWLEDGE_HOME": str(self.home if home is None else home),
        }

    def raw_request(self, capability: str, *, mode: str = "preview", level: str = "L0", scope: list[str] | None = None, payload: dict[str, object] | None = None) -> dict[str, object]:
        return {
            "schema_version": "his-capability-request.v1", "request_id": "raw-request", "capability": capability,
            "provider": "his-knowledge", "mode": mode, "mutation_level": level,
            "authorization": {
                "explicit": mode == "apply",
                "scope": [] if scope is None else scope,
            },
            "input": {} if payload is None else payload, "context": {},
        }

    def run_entrypoint(self, entrypoint: str, payload: dict[str, object], *, output_name: str = "output.json") -> subprocess.CompletedProcess[bytes]:
        request_path = Path(self.temp.name) / "request.json"
        output_path = Path(self.temp.name) / output_name
        request_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            ["/usr/bin/python3", str(PLUGIN_ROOT / "scripts" / entrypoint), "--request", str(request_path), "--output", str(output_path)],
            capture_output=True, env=self.cli_environment(), check=False,
        )

    @staticmethod
    def database_state(path: Path) -> tuple[str, int, int, tuple[int, ...]]:
        raw = path.read_bytes()
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            counts = tuple(connection.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in (
                "knowledge_items", "knowledge_versions", "knowledge_candidates", "knowledge_relations",
            ))
        finally:
            connection.close()
        return hashlib.sha256(raw).hexdigest(), len(raw), path.stat().st_mtime_ns, counts

    def test_all_five_descriptors_are_executable_with_exact_levels_and_scopes(self) -> None:
        expected = {
            "knowledge.retrieve": (MutationLevel.L0, ("knowledge:retrieve",)),
            "knowledge.answer": (MutationLevel.L0, ("knowledge:answer",)),
            "knowledge.candidate.create": (MutationLevel.L2, ("knowledge:candidate:create",)),
            "knowledge.candidate.review": (MutationLevel.L2, ("knowledge:candidate:review",)),
            "knowledge.item.promote": (MutationLevel.L2, ("knowledge:item:promote",)),
        }
        self.assertEqual(expected.keys(), {item.name for item in self.registry.descriptors})
        for name, (level, scopes) in expected.items():
            descriptor = self.registry.resolve(name, "his-knowledge")
            self.assertTrue(descriptor.entrypoint and descriptor.entrypoint.is_file())
            self.assertEqual(level, descriptor.mutation_level)
            self.assertEqual(scopes, descriptor.scopes)

    def test_retrieve_and_answer_preview_are_read_only_and_absent_home_stays_absent(self) -> None:
        for item in (
            request("knowledge.retrieve", mode="preview", level=MutationLevel.L0, scope=(), payload={"text": "结算"}),
            request("knowledge.answer", mode="preview", level=MutationLevel.L0, scope=(), payload={"text": "最新云效"}),
        ):
            execution = self.execute(item)
            self.assertEqual("success", execution.result.status)
            self.assertFalse(execution.result.changed)
            self.assertFalse(self.home.exists())
            self.assertEqual(["HIS_KNOWLEDGE_HOME"], execution.result.audit["runtime"]["environment_keys"])

    def test_maintenance_create_review_promote_is_local_and_redacted(self) -> None:
        payload = {
            "stable_key": "billing.rule", "title": "结算规则", "body": "按规则结算。", "kind": "workflow",
            "authority": "verified_code", "status": "active", "hospital_scope": "h1", "source_refs": [{"ref": "DFHIS-1", "claim_level": "code"}],
        }
        created = self.execute(request("knowledge.candidate.create", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:create",), payload={"payload": payload, "provenance": {"source": "test"}, "allow_personal_memory": False}))
        self.assertEqual("success", created.result.status)
        self.assertTrue(created.result.changed)
        candidate_id = created.result.data["candidate_id"]
        review = self.execute(request("knowledge.candidate.review", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:review",), payload={"candidate_id": candidate_id, "status": "approved", "reviewer": "reviewer", "reason": "verified"}))
        self.assertTrue(review.result.changed)
        promoted = self.execute(request("knowledge.item.promote", mode="apply", level=MutationLevel.L2, scope=("knowledge:item:promote",), payload={"candidate_id": candidate_id, "reviewer": "reviewer", "review_reason": "verified"}))
        self.assertTrue(promoted.result.changed)
        rendered = str(promoted.result.to_dict())
        for forbidden in ("结算规则", "按规则结算", "verified", "DFHIS-1"):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("candidate_id", promoted.result.audit["provider"])
        self.assertIn("local_sqlite_path", promoted.result.audit["provider"])
        self.assertEqual("$HIS_KNOWLEDGE_HOME/knowledge.sqlite", promoted.result.audit["provider"]["local_sqlite_path"])
        self.assertNotIn(str(self.home), str(promoted.result.to_dict()))

    def test_invalid_modes_levels_scopes_and_input_fail_closed(self) -> None:
        cases = (
            request("knowledge.retrieve", mode="apply", level=MutationLevel.L0, scope=(), payload={"text": "结算"}),
            request("knowledge.answer", mode="preview", level=MutationLevel.L2, scope=(), payload={"text": "结算"}),
            request("knowledge.candidate.create", mode="preview", level=MutationLevel.L2, scope=("knowledge:candidate:create",), payload={"payload": {}, "provenance": {}, "allow_personal_memory": False}),
            request("knowledge.candidate.review", mode="apply", level=MutationLevel.L2, scope=("extra",), payload={"candidate_id": 1, "status": "approved", "reviewer": "r", "reason": "ok"}),
            request("knowledge.retrieve", mode="preview", level=MutationLevel.L0, scope=(), payload={"text": "结算", "home": "/tmp/escape"}),
        )
        for item in cases:
            with self.subTest(item=item):
                execution = self.execute(item)
                self.assertIn(execution.result.status, {"blocked", "failed"})
                self.assertFalse(execution.result.changed)
                self.assertFalse(self.home.exists())

    def test_sensitive_candidate_is_blocked_before_database_creation(self) -> None:
        execution = self.execute(request("knowledge.candidate.create", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:create",), payload={
            "payload": {"stable_key": "secret", "token": "token=abc12345678"}, "provenance": {}, "allow_personal_memory": False,
        }))
        self.assertEqual("blocked", execution.result.status)
        self.assertFalse(execution.result.changed)
        self.assertFalse(self.home.exists())

    def test_preview_database_is_byte_invariant_and_live_suggestions_are_data_only(self) -> None:
        payload = {
            "stable_key": "billing.rule", "title": "结算规则", "body": "按规则结算。", "kind": "workflow",
            "authority": "verified_code", "status": "active", "hospital_scope": "h1", "source_refs": [{"ref": "DFHIS-1", "claim_level": "code"}],
        }
        created = self.execute(request("knowledge.candidate.create", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:create",), payload={"payload": payload, "provenance": {"source": "test"}, "allow_personal_memory": False}))
        self.execute(request("knowledge.candidate.review", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:review",), payload={"candidate_id": created.result.data["candidate_id"], "status": "approved", "reviewer": "reviewer", "reason": "verified"}))
        self.execute(request("knowledge.item.promote", mode="apply", level=MutationLevel.L2, scope=("knowledge:item:promote",), payload={"candidate_id": created.result.data["candidate_id"], "reviewer": "reviewer", "review_reason": "verified"}))
        before = self.database_state(self.home / "knowledge.sqlite")
        retrieved = self.execute(request("knowledge.retrieve", mode="preview", level=MutationLevel.L0, scope=(), payload={"text": "结算", "hospital": "h1"}))
        answered = self.execute(request("knowledge.answer", mode="preview", level=MutationLevel.L0, scope=(), payload={"text": "最新云效"}))
        self.assertEqual("success", retrieved.result.status)
        self.assertFalse(retrieved.result.changed)
        self.assertEqual(["workitem.read"], answered.result.data["suggested_capabilities"])
        self.assertFalse(answered.result.audit["provider"]["suggestions_executed"])
        self.assertEqual(before, self.database_state(self.home / "knowledge.sqlite"))

    def test_raw_transport_rejects_wrong_identity_unknown_fields_and_untrusted_input(self) -> None:
        cases = []
        wrong_provider = self.raw_request("knowledge.retrieve", payload={"text": "结算"})
        wrong_provider["provider"] = "other"
        cases.append(wrong_provider)
        unknown_field = self.raw_request("knowledge.retrieve", payload={"text": "结算"})
        unknown_field["unknown"] = True
        cases.append(unknown_field)
        forbidden_input = self.raw_request("knowledge.retrieve", payload={"text": "结算", "home": "/tmp/escape"})
        cases.append(forbidden_input)
        wrong_scope = self.raw_request("knowledge.retrieve", scope=["knowledge:retrieve"], payload={"text": "结算"})
        cases.append(wrong_scope)
        nonempty_context = self.raw_request("knowledge.retrieve", payload={"text": "结算"})
        nonempty_context["context"] = {"unexpected": True}
        cases.append(nonempty_context)
        for payload in cases:
            with self.subTest(payload=payload):
                completed = self.run_entrypoint("knowledge_retrieve.py", payload)
                self.assertEqual(2, completed.returncode)
                self.assertEqual(b"", completed.stdout)
                self.assertEqual(b"invalid capability request or output\n", completed.stderr)
                self.assertFalse(self.home.exists())

    def test_raw_transport_rejects_existing_symlink_and_alias_output_without_stdout(self) -> None:
        payload = self.raw_request("knowledge.retrieve", payload={"text": "结算"})
        request_path = Path(self.temp.name) / "request.json"
        request_path.write_text(json.dumps(payload), encoding="utf-8")
        existing = Path(self.temp.name) / "existing.json"
        existing.write_text("old", encoding="utf-8")
        symlink = Path(self.temp.name) / "symlink.json"
        symlink.symlink_to(existing)
        for output in (existing, symlink, request_path):
            with self.subTest(output=output.name):
                completed = subprocess.run(
                    ["/usr/bin/python3", str(PLUGIN_ROOT / "scripts" / "knowledge_retrieve.py"), "--request", str(request_path), "--output", str(output)],
                    capture_output=True, env=self.cli_environment(), check=False,
                )
                self.assertEqual(2, completed.returncode)
                self.assertEqual(b"", completed.stdout)
                self.assertEqual(b"invalid capability request or output\n", completed.stderr)
        self.assertEqual("old", existing.read_text(encoding="utf-8"))
        self.assertFalse(self.home.exists())

    def test_raw_transport_rejects_symlinked_request_parent_and_home_parent(self) -> None:
        target = Path(self.temp.name) / "target"
        target.mkdir()
        request_parent = Path(self.temp.name) / "request-link"
        request_parent.symlink_to(target, target_is_directory=True)
        request_path = request_parent / "request.json"
        request_path.write_text(json.dumps(self.raw_request("knowledge.retrieve", payload={"text": "结算"})), encoding="utf-8")
        output = Path(self.temp.name) / "output.json"
        completed = subprocess.run(
            ["/usr/bin/python3", str(PLUGIN_ROOT / "scripts" / "knowledge_retrieve.py"), "--request", str(request_path), "--output", str(output)],
            capture_output=True, env=self.cli_environment(), check=False,
        )
        self.assertEqual(2, completed.returncode)
        home_parent = Path(self.temp.name) / "home-link"
        home_parent.symlink_to(target, target_is_directory=True)
        completed = self.run_entrypoint("knowledge_retrieve.py", self.raw_request("knowledge.retrieve", payload={"text": "结算"}), output_name="home-parent.json")
        self.assertEqual(0, completed.returncode)
        completed = subprocess.run(
            ["/usr/bin/python3", str(PLUGIN_ROOT / "scripts" / "knowledge_retrieve.py"), "--request", str(Path(self.temp.name) / "request.json"), "--output", str(Path(self.temp.name) / "home-link-result.json")],
            capture_output=True, env=self.cli_environment(home=home_parent / "db"), check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertEqual(b"", completed.stdout)

    def test_partial_output_write_is_removed(self) -> None:
        output = Path(self.temp.name) / "partial.json"
        with patch.object(knowledge_capability.os, "write", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                knowledge_capability._write_new_json(output, {"safe": True})
        self.assertFalse(output.exists())

    def test_maintenance_runtime_exception_never_prints_a_traceback(self) -> None:
        completed = self.run_entrypoint(
            "knowledge_maintain.py",
            self.raw_request(
                "knowledge.item.promote", mode="apply", level="L2", scope=["knowledge:item:promote"],
                payload={"candidate_id": 999, "reviewer": "reviewer", "review_reason": "verified"},
            ),
        )
        self.assertEqual(2, completed.returncode)
        self.assertEqual(b"", completed.stdout)
        self.assertEqual(b"invalid capability request or output\n", completed.stderr)

    def test_direct_cli_rejects_malformed_json_and_request_file_symlink(self) -> None:
        malformed = Path(self.temp.name) / "malformed.json"
        malformed.write_text("{not-json", encoding="utf-8")
        output = Path(self.temp.name) / "malformed-output.json"
        completed = subprocess.run(
            ["/usr/bin/python3", str(PLUGIN_ROOT / "scripts" / "knowledge_retrieve.py"), "--request", str(malformed), "--output", str(output)],
            capture_output=True, env=self.cli_environment(), check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertEqual(b"", completed.stdout)
        self.assertEqual(b"invalid capability request or output\n", completed.stderr)
        regular = Path(self.temp.name) / "regular.json"
        regular.write_text(json.dumps(self.raw_request("knowledge.retrieve", payload={"text": "结算"})), encoding="utf-8")
        link = Path(self.temp.name) / "request-file-link.json"
        link.symlink_to(regular)
        completed = subprocess.run(
            ["/usr/bin/python3", str(PLUGIN_ROOT / "scripts" / "knowledge_retrieve.py"), "--request", str(link), "--output", str(Path(self.temp.name) / "link-output.json")],
            capture_output=True, env=self.cli_environment(), check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertEqual(b"", completed.stdout)
        self.assertEqual(b"invalid capability request or output\n", completed.stderr)

    def test_direct_cli_rejects_symlinked_output_parent(self) -> None:
        target = Path(self.temp.name) / "target-output"
        target.mkdir()
        parent = Path(self.temp.name) / "output-link"
        parent.symlink_to(target, target_is_directory=True)
        completed = self.run_entrypoint("knowledge_retrieve.py", self.raw_request("knowledge.retrieve", payload={"text": "结算"}), output_name="normal.json")
        self.assertEqual(0, completed.returncode)
        request_path = Path(self.temp.name) / "request.json"
        completed = subprocess.run(
            ["/usr/bin/python3", str(PLUGIN_ROOT / "scripts" / "knowledge_retrieve.py"), "--request", str(request_path), "--output", str(parent / "result.json")],
            capture_output=True, env=self.cli_environment(), check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertEqual(b"", completed.stdout)
        self.assertEqual(b"invalid capability request or output\n", completed.stderr)

    def test_answer_suggestions_are_all_inert_data(self) -> None:
        cases = (
            ("最新云效", ["workitem.read"]),
            ("生产运行时数据库状态", ["database.inspect"]),
            ("请帮我修改代码", ["harness.task"]),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                execution = self.execute(request("knowledge.answer", mode="preview", level=MutationLevel.L0, scope=(), payload={"text": text}))
                self.assertEqual("success", execution.result.status)
                self.assertFalse(execution.result.changed)
                self.assertEqual(expected, execution.result.data["suggested_capabilities"])
                self.assertFalse(execution.result.audit["provider"]["suggestions_executed"])
                self.assertFalse(self.home.exists())

    def test_promotion_audit_change_is_truthful_and_identical_retry_is_not_changed(self) -> None:
        payload = {
            "stable_key": "billing.audit", "title": "审核规则", "body": "按规则结算。", "kind": "workflow",
            "authority": "verified_code", "status": "active", "hospital_scope": "h1", "source_refs": [{"ref": "DFHIS-3", "claim_level": "code"}],
        }
        created = self.execute(request("knowledge.candidate.create", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:create",), payload={"payload": payload, "provenance": {"source": "test"}, "allow_personal_memory": False}))
        candidate_id = created.result.data["candidate_id"]
        self.execute(request("knowledge.candidate.review", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:review",), payload={"candidate_id": candidate_id, "status": "approved", "reviewer": "reviewer", "reason": "approved"}))
        first = request("knowledge.item.promote", mode="apply", level=MutationLevel.L2, scope=("knowledge:item:promote",), payload={"candidate_id": candidate_id, "reviewer": "reviewer-a", "review_reason": "promote-a"})
        self.assertTrue(self.execute(first).result.changed)
        before_changed_audit = self.database_state(self.home / "knowledge.sqlite")
        changed_audit = request("knowledge.item.promote", mode="apply", level=MutationLevel.L2, scope=("knowledge:item:promote",), payload={"candidate_id": candidate_id, "reviewer": "reviewer-b", "review_reason": "promote-b"})
        self.assertTrue(self.execute(changed_audit).result.changed)
        after_changed_audit = self.database_state(self.home / "knowledge.sqlite")
        self.assertNotEqual(before_changed_audit, after_changed_audit)
        before_retry = self.database_state(self.home / "knowledge.sqlite")
        retry = self.execute(changed_audit)
        self.assertFalse(retry.result.changed)
        self.assertEqual(before_retry, self.database_state(self.home / "knowledge.sqlite"))

    def test_damaged_sqlite_never_leaks_traceback_or_database_content(self) -> None:
        self.home.mkdir()
        (self.home / "knowledge.sqlite").write_text("token=super-secret-corrupt-db", encoding="utf-8")
        completed = self.run_entrypoint(
            "knowledge_maintain.py",
            self.raw_request(
                "knowledge.candidate.create", mode="apply", level="L2", scope=["knowledge:candidate:create"],
                payload={"payload": {"stable_key": "safe", "title": "safe", "body": "safe", "kind": "workflow", "authority": "verified_code", "status": "active", "hospital_scope": "h", "source_refs": [{"ref": "x", "claim_level": "code"}]}, "provenance": {}, "allow_personal_memory": False},
            ),
        )
        self.assertEqual(2, completed.returncode)
        self.assertEqual(b"", completed.stdout)
        self.assertEqual(b"invalid capability request or output\n", completed.stderr)
        self.assertNotIn(b"Traceback", completed.stderr)
        self.assertNotIn(b"super-secret", completed.stderr)

    def test_duplicate_create_and_promotion_retry_report_changed_false(self) -> None:
        payload = {
            "stable_key": "billing.retry", "title": "重试规则", "body": "按规则结算。", "kind": "workflow",
            "authority": "verified_code", "status": "active", "hospital_scope": "h1", "source_refs": [{"ref": "DFHIS-2", "claim_level": "code"}],
        }
        create = request("knowledge.candidate.create", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:create",), payload={"payload": payload, "provenance": {"source": "test"}, "allow_personal_memory": False})
        first = self.execute(create)
        duplicate = self.execute(create)
        self.assertTrue(first.result.changed)
        self.assertFalse(duplicate.result.changed)
        candidate_id = first.result.data["candidate_id"]
        self.execute(request("knowledge.candidate.review", mode="apply", level=MutationLevel.L2, scope=("knowledge:candidate:review",), payload={"candidate_id": candidate_id, "status": "approved", "reviewer": "reviewer", "reason": "verified"}))
        promote = request("knowledge.item.promote", mode="apply", level=MutationLevel.L2, scope=("knowledge:item:promote",), payload={"candidate_id": candidate_id, "reviewer": "reviewer", "review_reason": "verified"})
        self.assertTrue(self.execute(promote).result.changed)
        self.assertFalse(self.execute(promote).result.changed)


if __name__ == "__main__":
    unittest.main()
