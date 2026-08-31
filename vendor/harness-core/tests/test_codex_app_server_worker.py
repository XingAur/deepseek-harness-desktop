import io
import json
import tempfile
import unittest
from pathlib import Path

from app.codex_cli_worker import CodexWorkerRequest


class _Sink:
    def __init__(self):
        self.started = None
        self.events = []

    def on_started(self, pid, identity):
        self.started = (pid, identity)

    def on_event(self, event):
        self.events.append(event)


class _Process:
    def __init__(self, messages):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"".join(
            json.dumps(item, separators=(",", ":")).encode("utf-8") + b"\n"
            for item in messages
        ))
        self.stderr = io.BytesIO()
        self.pid = 4242
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1


class CodexAppServerWorkerTests(unittest.TestCase):
    def test_reviewer_uses_fixed_stdio_rpc_sequence_and_returns_only_validated_json(self):
        from app.codex_app_server_worker import CodexAppServerWorker

        process = _Process([
            {"id": 1, "result": {"userAgent": "codex", "platformFamily": "unix", "platformOs": "macos", "codexHome": "/tmp"}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}},
            {"method": "remoteControl/status/changed", "params": {"environmentId": "local", "installationId": "local", "serverName": "codex", "status": "disabled"}},
            {"jsonrpc": "2.0", "method": "item/completed", "params": {"threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 1, "item": {"id": "item-1", "type": "agentMessage", "text": '{"verdict":"approved","findings":[],"summary":"ok","review_hash":"h"}'}}},
            {"method": "account/rateLimits/updated", "params": {"rateLimits": {}}},
            {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed", "items": []}}},
        ])
        calls = []
        preflight_calls = []
        revalidation_calls = []

        def factory(argv, **kwargs):
            calls.append((argv, kwargs))
            return process

        with tempfile.TemporaryDirectory() as worktree, tempfile.TemporaryDirectory() as schemas:
            schema = Path(schemas) / "review.json"
            schema.write_text('{"type":"object"}', encoding="utf-8")
            request = CodexWorkerRequest.reviewer(
                Path(worktree), "只做本地审查", 30, schema,
                __import__("hashlib").sha256(schema.read_bytes()).hexdigest(),
            )
            sink = _Sink()
            worker = CodexAppServerWorker(
                process_factory=factory,
                executable="/trusted/codex",
                process_identity_reader=lambda pid: f"darwin-proc-bsdinfo-v1:{pid}:1",
                executable_preflight=lambda deadline: preflight_calls.append(deadline) or object(),
                executable_revalidator=lambda binding: revalidation_calls.append(binding),
            )
            result = worker.start(request, sink)

        self.assertEqual(["/trusted/codex", "app-server", "--stdio"], calls[0][0])
        self.assertEqual(1, len(preflight_calls))
        self.assertEqual(2, len(revalidation_calls))
        self.assertEqual((4242, "darwin-proc-bsdinfo-v1:4242:1"), sink.started)
        messages = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(["initialize", "initialized", "thread/start", "turn/start"], [item["method"] for item in messages])
        self.assertEqual("workspace-write", messages[2]["params"]["sandbox"])
        self.assertEqual({"type": "workspaceWrite", "networkAccess": False, "writableRoots": []}, messages[3]["params"]["sandboxPolicy"])
        self.assertEqual("thread-1", messages[3]["params"]["threadId"])
        self.assertNotIn("outputSchema", messages[3]["params"])
        self.assertEqual({"verdict": "approved", "findings": [], "summary": "ok", "review_hash": "h"}, result.final_response)
        self.assertFalse(result.final_response_validated)
        self.assertTrue(result.untrusted_final_response)
        self.assertEqual("", result.error_code)
        self.assertEqual(["item.completed", "turn.completed"], [item["type"] for item in sink.events])
        self.assertNotIn("thread-1", json.dumps(sink.events))

    def test_process_protocol_error_fails_closed_without_provider_text(self):
        from app.codex_app_server_worker import CodexAppServerWorker

        process = _Process([
            {"jsonrpc": "2.0", "id": 1, "result": {}},
            {"jsonrpc": "2.0", "id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"jsonrpc": "2.0", "id": 3, "result": {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}},
            {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "other-thread", "turn": {"id": "turn-1", "status": "completed", "items": []}}},
        ])
        with tempfile.TemporaryDirectory() as worktree:
            request = CodexWorkerRequest.worker(Path(worktree), "只读检查", 30)
            result = CodexAppServerWorker(
                process_factory=lambda *args, **kwargs: process,
                executable="/trusted/codex",
                process_identity_reader=lambda pid: f"darwin-proc-bsdinfo-v1:{pid}:1",
                executable_preflight=lambda deadline: None,
                executable_revalidator=lambda binding: None,
            ).start(request, _Sink())

        self.assertEqual("worker_protocol_invalid", result.error_code)
        self.assertIsNone(result.final_response)

if __name__ == "__main__":
    unittest.main()
