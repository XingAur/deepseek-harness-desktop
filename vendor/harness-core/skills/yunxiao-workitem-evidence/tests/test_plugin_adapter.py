from __future__ import annotations

import importlib.util
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
LEGACY_SCRIPT = SKILL_DIR / "scripts" / "yunxiao_evidence.py"
REPOSITORY_ROOT = SKILL_DIR.parents[1]
PLUGIN_SCRIPT = (
    REPOSITORY_ROOT
    / "plugins"
    / "yunxiao"
    / "skills"
    / "yunxiao-workitem-read"
    / "scripts"
    / "yunxiao_evidence.py"
)
os.environ.setdefault("HARNESS_ENABLE_STAGED_PLUGIN_TESTS", "1")
os.environ.setdefault(
    "HARNESS_STAGED_PLUGIN_ROOT",
    str(REPOSITORY_ROOT / "plugins"),
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


legacy = _load("legacy_yunxiao_evidence_for_parity", LEGACY_SCRIPT)
plugin = _load("plugin_yunxiao_evidence_for_parity", PLUGIN_SCRIPT)


class FakeResponse:
    def __init__(self, payload, *, content_type="application/json"):
        self._body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        )
        self.status = 200
        self.headers = {"content-type": content_type}

    def read(self, _limit=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FixedFakeOpener:
    def __init__(self):
        self.methods: list[str] = []

    def __call__(self, request, timeout):
        del timeout
        self.methods.append(request.get_method())
        url = request.full_url
        if url.startswith("https://files.example/fixed-attachment.txt"):
            return FakeResponse(b"attachment-content", content_type="text/plain")
        if url.startswith("https://files.example/fixed-inline.png"):
            return FakeResponse(b"inline-content", content_type="image/png")
        if url.endswith("/comments"):
            return FakeResponse([{"id": "comment-1", "content": "固定评论"}])
        if url.endswith("/attachments"):
            return FakeResponse([
                {
                    "id": "attachment-1",
                    "fileName": "固定附件.txt",
                    "url": "https://files.example/fixed-attachment.txt?signature=redacted",
                }
            ])
        if "relationRecords" in url:
            return FakeResponse([])
        return FakeResponse(
            {
                "id": "workitem-1",
                "serialNumber": "DFHIS-90001",
                "title": "固定工作项",
                "description": (
                    '<p>固定正文</p><img src="https://files.example/'
                    'fixed-inline.png?signature=redacted">'
                ),
                "categoryId": "Req",
            }
        )


def _projection(evidence):
    item = evidence["work_items"][0]
    return {
        "work_item": {
            "id": item["id"],
            "title": item["title"],
            "body": item["description"]["text"],
        },
        "comments": item["comments"],
        "attachments": item["attachments"],
        "inline_files": item["inline_files"],
        "warning_codes": [entry["code"] for entry in evidence["warnings"]],
        "error_codes": [entry["code"] for entry in evidence["errors"]],
        "decision_gate": evidence["decision_gate"],
        "readonly_boundary": evidence["policy"],
    }


EXPECTED_PROJECTION = {
    "work_item": {"id": "workitem-1", "title": "固定工作项", "body": "固定正文"},
    "comments": [
        {
            "id": "comment-1",
            "author": "",
            "created_at": "",
            "format": "UNKNOWN",
            "raw": "固定评论",
            "content": "固定评论",
        }
    ],
    "attachments": [
        {
            "id": "attachment-1",
            "file_id": "",
            "name": "固定附件.txt",
            "suffix": "",
            "source_url": "https://files.example/fixed-attachment.txt",
            "size": len(b"attachment-content"),
            "created_at": "",
            "download_status": "success",
            "local_path": "files/workitem-1/固定附件.txt",
            "content_type": "text/plain",
            "sha256": hashlib.sha256(b"attachment-content").hexdigest(),
        }
    ],
    "inline_files": [
        {
            "file_id": "",
            "name": "fixed-inline.png",
            "source_url": "https://files.example/fixed-inline.png",
            "download_status": "success",
            "local_path": "files/workitem-1/fixed-inline.png",
            "content_type": "image/png",
            "size": len(b"inline-content"),
            "sha256": hashlib.sha256(b"inline-content").hexdigest(),
        }
    ],
    "warning_codes": [],
    "error_codes": [],
    "decision_gate": {
        "state": "ready_for_analysis",
        "reasons": [],
    },
    "readonly_boundary": {
        "allowed_actions": ["read"],
        "blocked_actions": [
            "comment",
            "upload_attachment",
            "assign",
            "transition",
            "update",
            "create",
            "delete",
            "close",
        ],
    },
}


class LegacyPluginCharacterizationTests(unittest.TestCase):
    def test_legacy_and_plugin_collect_fixed_readonly_payload_equivalently(self):
        legacy_opener = FixedFakeOpener()
        plugin_opener = FixedFakeOpener()
        legacy_client = legacy.YunxiaoClient(
            token="fixture-token",
            organization_id="fixture-org",
            opener=legacy_opener,
        )
        plugin_client = plugin.YunxiaoClient(
            token="fixture-token",
            organization_id="fixture-org",
            opener=plugin_opener,
        )

        with tempfile.TemporaryDirectory() as old_dir, tempfile.TemporaryDirectory() as new_dir:
            old = legacy.collect_evidence(
                source="DFHIS-90001",
                client=legacy_client,
                output_dir=Path(old_dir).resolve() / "evidence",
                download_files=True,
                fetched_at="2026-07-27T00:00:00+00:00",
            )
            new = plugin.collect_evidence(
                source="DFHIS-90001",
                client=plugin_client,
                output_dir=Path(new_dir).resolve() / "evidence",
                download_files=True,
                fetched_at="2026-07-27T00:00:00+00:00",
            )

        self.assertEqual(EXPECTED_PROJECTION, _projection(old))
        self.assertEqual(EXPECTED_PROJECTION, _projection(new))
        self.assertEqual({"GET"}, set(legacy_opener.methods))
        self.assertEqual({"GET"}, set(plugin_opener.methods))

    def test_legacy_public_api_is_explicitly_delegated_from_the_plugin_module(self):
        self.assertTrue(legacy.PLUGIN_IMPLEMENTATION_PATH.is_file())
        self.assertEqual(
            legacy.collect_evidence.__module__,
            legacy.YunxiaoClient.__module__,
        )
        self.assertTrue(
            legacy.collect_evidence.__module__.startswith(
                "_yunxiao_plugin_evidence"
            )
        )

    def test_loader_has_no_caller_selected_root_or_path_parameter(self):
        self.assertEqual(
            (),
            tuple(inspect.signature(legacy._load_plugin_module).parameters),
        )
        self.assertFalse(hasattr(legacy, "_load_plugin_module_from_roots"))

    def test_corrupt_plugin_error_is_stable_and_does_not_leak_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / legacy._RELATIVE_IMPLEMENTATION
            target.parent.mkdir(parents=True)
            target.write_text("raise ValueError('fixture-secret')\n", encoding="utf-8")
            with patch.object(legacy, "PLUGIN_ROOT_CANDIDATES", (root,)):
                with self.assertRaisesRegex(RuntimeError, "Yunxiao plugin is not installed") as error:
                    legacy._load_plugin_module()
        message = str(error.exception)
        self.assertNotIn("fixture-secret", message)
        self.assertNotIn(str(target), message)
        self.assertNotIn(legacy._PRIVATE_MODULE_NAME, sys.modules)

    def test_missing_plugin_export_has_a_stable_non_reflective_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / legacy._RELATIVE_IMPLEMENTATION
            target.parent.mkdir(parents=True)
            target.write_text("CONTRACT_VERSION = 'fixture'\n", encoding="utf-8")
            with patch.object(legacy, "PLUGIN_ROOT_CANDIDATES", (root,)):
                with self.assertRaisesRegex(RuntimeError, "Yunxiao plugin is not installed") as error:
                    legacy._load_plugin_module()
        message = str(error.exception)
        self.assertNotIn("DEFAULT_BASE_URL", message)
        self.assertNotIn(str(target), message)
        self.assertNotIn(legacy._PRIVATE_MODULE_NAME, sys.modules)

    def test_legacy_skill_routes_new_work_to_the_read_plugin_without_write_pat_advice(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(
            "description: Use when maintaining existing automation or old commands",
            text,
        )
        self.assertNotIn("Compatibility entry for existing automation", text)
        self.assertIn("This legacy compatibility path is not for new", text)
        body = text.split("---", 2)[-1]
        self.assertIn("兼容入口", body)
        self.assertIn("$yunxiao-workitem-read", body)
        self.assertIn("$yunxiao-workitem-write", body)
        self.assertIn("GET", body)
        self.assertNotIn("--credential-kind write", body)
        self.assertNotIn("aliyun_devops_write_pat", body)

    def test_legacy_cli_retains_hidden_write_syntax_without_advertising_it(self):
        script = SKILL_DIR / "scripts" / "collect_evidence.py"
        help_result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, help_result.returncode)
        self.assertNotIn("credential-kind", help_result.stdout)
        self.assertNotIn("write", help_result.stdout.lower())

        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        module = _load("legacy_collect_evidence_for_parser", script)
        parsed = module.build_parser().parse_args(
            ["DFHIS-1", "--output-dir", "out", "--credential-kind", "write"]
        )
        self.assertEqual("write", parsed.credential_kind)


if __name__ == "__main__":
    unittest.main()
