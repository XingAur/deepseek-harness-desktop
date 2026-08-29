from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.codex_cli_worker import CodexCliWorker, CodexWorkerRequest, ProtocolRejectionAudit, WorkerRole, _Protocol, validate_protocol_rejection_audit


class _Stdin:
    def __init__(self, *, blocks: bool = False) -> None:
        self.data = bytearray()
        self.blocks = blocks
        self.closed = False

    def write_chunk(self, value: bytes) -> int | None:
        if self.blocks:
            return None
        self.data.extend(value)
        return len(value)

    def close(self) -> None:
        self.closed = True


class _Stream:
    def __init__(self, chunks: list[bytes | None], *, hold_open: bool = False) -> None:
        self.chunks = list(chunks)
        self.hold_open = hold_open
        self.closed = False

    def read_chunk(self, _maximum: int) -> bytes | None:
        if self.chunks:
            return self.chunks.pop(0)
        return None if self.hold_open else b""

    def close(self) -> None:
        self.closed = True


class _Process:
    def __init__(self, stdout: list[bytes | None], *, stdin_blocks: bool = False, hold_open: bool = False, leader_exited: bool = False) -> None:
        self.pid = 4242
        self.stdin = _Stdin(blocks=stdin_blocks)
        self.stdout = _Stream(stdout, hold_open=hold_open)
        self.stderr = _Stream([])
        self.returncode: int | None = None
        self.wait_called = False
        self.leader_exited = leader_exited

    def poll(self) -> int | None:
        if self.leader_exited:
            return 0
        if not self.stdout.chunks and not self.stdout.hold_open:
            return 0
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_called = True
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


class _Factory:
    def __init__(self, process: _Process) -> None:
        self.process = process
        self.argv: list[str] = []
        self.kwargs: dict[str, object] = {}
        self.group_alive = True
        self.terminated = False
        self.killed = False

    def __call__(self, argv: list[str], **kwargs: object) -> _Process:
        self.argv = list(argv)
        self.kwargs = kwargs
        return self.process

    def signal_group(self, _pgid: int, signal_name: str) -> None:
        self.terminated = self.terminated or signal_name == "TERM"
        self.killed = self.killed or signal_name == "KILL"
        if signal_name == "KILL":
            self.group_alive = False

    def group_exists(self, _pgid: int) -> bool:
        return self.group_alive


class _Sink:
    def __init__(self, *, fail_started: bool = False, fail_event: bool = False) -> None:
        self.started: list[tuple[int, str]] = []
        self.events: list[dict[str, object]] = []
        self.fail_started = fail_started
        self.fail_event = fail_event

    def on_started(self, pid: int, start_identity: str) -> None:
        if self.fail_started:
            raise RuntimeError("started sink failure")
        self.started.append((pid, start_identity))

    def on_event(self, event: dict[str, object]) -> None:
        if self.fail_event:
            raise RuntimeError("event sink failure")
        self.events.append(event)


def _events(*, reviewer_text: str | None = None) -> list[bytes]:
    events = [
        b'{"type":"thread.started","thread_id":"thread-1"}\n',
        b'{"type":"turn.started"}\n',
    ]
    if reviewer_text is not None:
        events.append(
            (
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "id": "item-1",
                            "text": reviewer_text,
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
        )
    events.append(b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n')
    return events


class CodexCliWorkerTests(unittest.TestCase):
    def test_bundled_uuid_thread_identity_is_used_only_in_memory(self) -> None:
        protocol = _Protocol(WorkerRole.WORKER)
        thread_id = "019c9d85-1d4c-7123-8f2a-123456789abc"

        safe, final = protocol.accept(
            json.dumps({"type": "thread.started", "thread_id": thread_id}).encode(),
            1,
        )

        self.assertIsNone(final)
        self.assertEqual({"type": "thread.started", "sequence_no": 1, "raw_line_sha256": hashlib.sha256(json.dumps({"type": "thread.started", "thread_id": thread_id}).encode()).hexdigest()}, safe)
        self.assertEqual(thread_id, protocol.thread_id)
        self.assertNotIn(thread_id, repr(safe))

    def test_protocol_rejection_audit_contains_only_bounded_classification(self) -> None:
        protocol = _Protocol(WorkerRole.WORKER)
        protocol.accept(b'{"type":"thread.started","thread_id":"safe"}', 1)
        protocol.accept(b'{"type":"turn.started"}', 2)
        raw = b'{"type":"future.event","item":{"type":"future_item","text":"Bearer secret-value"},"thread_id":"opaque"}'
        with self.assertRaisesRegex(Exception, "worker_protocol_invalid") as raised:
            protocol.accept(raw, 3, elapsed_seconds=61.0)
        audit = raised.exception.rejection_audit
        self.assertEqual("unknown_event_type", audit.candidate_event_type)
        self.assertEqual("unknown_item_type", audit.candidate_item_type)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), audit.raw_line_sha256)
        self.assertEqual(3, audit.sequence_no)
        self.assertEqual("turn_active", audit.fsm_state)
        self.assertEqual("60_179s", audit.elapsed_bucket)
        rendered = json.dumps(audit.as_mapping(), sort_keys=True)
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("opaque", rendered)

    def test_protocol_rejection_categories_invalid_candidate_values(self) -> None:
        protocol = _Protocol(WorkerRole.WORKER)
        for raw, event_category, item_category in (
            (b'{"item":{}}', "missing", "missing"),
            (b'{"type":7,"item":{"type":[]}}', "non_string", "non_string"),
            (json.dumps({"type": "x" * 65, "item": {"type": "Bearer abcdefghijklmnop"}}).encode(), "too_long", "sensitive"),
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(Exception) as raised:
                    protocol.accept(raw, 1, elapsed_seconds=361.0)
                audit = raised.exception.rejection_audit
                self.assertEqual(event_category, audit.candidate_event_type)
                self.assertEqual(item_category, audit.candidate_item_type)
                self.assertEqual("over_360s", audit.elapsed_bucket)

    def test_protocol_rejection_never_persists_opaque_or_token_like_candidates(self) -> None:
        for candidate in (
            "abcdefghijklmnopqrstuvwxzy012345",
            "token_abcdefghijklmnopqrstuvwx",
            "sk_abcdefghijklmnopqrstuvwx",
            "019c9d85-1d4c-7123-8f2a-123456789abc",
        ):
            with self.subTest(candidate=candidate):
                raw = json.dumps({"type": candidate, "item": {"type": candidate}}).encode()
                with self.assertRaises(Exception) as raised:
                    _Protocol(WorkerRole.WORKER).accept(raw, 1)
                audit = raised.exception.rejection_audit
                self.assertEqual("unknown_event_type", audit.candidate_event_type)
                self.assertEqual("unknown_item_type", audit.candidate_item_type)
                self.assertNotIn(candidate, json.dumps(audit.as_mapping()))

    def setUp(self) -> None:
        Path("/private/tmp/his_harness").mkdir(parents=True, exist_ok=True)
        self.temporary_directory = tempfile.TemporaryDirectory(dir="/private/tmp/his_harness")
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.schema_root = self.root / "harness-schemas"
        self.schema_root.mkdir()
        self.schema = self.schema_root / "review.json"
        self.schema.write_text('{"type":"object"}', encoding="utf-8")
        self.schema_hash = hashlib.sha256(self.schema.read_bytes()).hexdigest()
        self.schema_patch = patch("app.codex_cli_worker.HARNESS_SCHEMA_ROOT", self.schema_root)
        self.schema_patch.start()
        self.addCleanup(self.schema_patch.stop)

    def worker(self, factory: _Factory, **kwargs: object) -> CodexCliWorker:
        options: dict[str, object] = {
            "process_factory": factory,
            "process_identity_reader": lambda pid: f"darwin-proc-bsdinfo-v1:{pid}:9",
            "process_group_reader": lambda pid: pid,
            "process_group_signaler": factory.signal_group,
            "process_group_exists": factory.group_exists,
            "executable_preflight": lambda deadline: None,
        }
        options.update(kwargs)
        return CodexCliWorker(**options)  # type: ignore[arg-type]

    def worker_request(self) -> CodexWorkerRequest:
        return CodexWorkerRequest.worker(self.worktree, "Make the narrow change.", 3)

    def reviewer_request(self) -> CodexWorkerRequest:
        return CodexWorkerRequest.reviewer(
            self.worktree, "Review the change.", 3, self.schema, self.schema_hash
        )

    def test_role_constructors_make_exact_non_escalating_argv(self) -> None:
        worker_factory = _Factory(_Process(_events()))
        reviewer_factory = _Factory(_Process(_events(reviewer_text='{}')))

        worker_result = self.worker(worker_factory).start(self.worker_request(), _Sink())
        reviewer_result = self.worker(reviewer_factory).start(self.reviewer_request(), _Sink())

        self.assertEqual("", worker_result.error_code)
        self.assertEqual("", reviewer_result.error_code)
        self.assertIn("--approve-for-me", worker_factory.argv)
        self.assertNotIn("--approve-for-me", reviewer_factory.argv)
        self.assertNotIn("--sandbox", worker_factory.argv)
        self.assertEqual("read-only", reviewer_factory.argv[reviewer_factory.argv.index("--sandbox") + 1])
        self.assertNotIn("--output-schema", reviewer_factory.argv)
        self.assertIsNotNone(self.reviewer_request().output_schema_path)
        self.assertEqual(self.schema_hash, self.reviewer_request().expected_schema_sha256)
        self.assertEqual(WorkerRole.REVIEWER, self.reviewer_request().role)

    def test_reviewer_output_schema_uses_only_provider_supported_keywords(self) -> None:
        from app.codex_cli_worker import _validate_output_schema_compatibility

        unsupported = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"value": {"type": "string", "pattern": "^[a-z]+$"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        with self.assertRaisesRegex(ValueError, "worker_request_invalid"):
            _validate_output_schema_compatibility(unsupported)

        production = json.loads((Path(__file__).parents[1] / "config" / "schemas" / "his-local-agent-review.v1.json").read_text(encoding="utf-8"))
        _validate_output_schema_compatibility(production)

    def test_worker_argv_does_not_combine_mutually_exclusive_approval_and_sandbox_flags(self) -> None:
        worker_factory = _Factory(_Process(_events()))

        result = self.worker(worker_factory).start(self.worker_request(), _Sink())

        self.assertEqual("", result.error_code)
        self.assertIn("--approve-for-me", worker_factory.argv)
        self.assertNotIn("--sandbox", worker_factory.argv)

    def test_real_local_cwd_and_inherited_schema_fd_use_production_popen_kwargs(self) -> None:
        captured: dict[str, object] = {}

        def local_factory(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
            captured["argv"] = list(argv)
            captured["kwargs"] = dict(kwargs)
            schema_fd = tuple(kwargs["pass_fds"])[0]  # type: ignore[index]
            events = b"".join(_events(reviewer_text="{}"))
            script = (
                "import os,sys;"
                "sys.stdin.buffer.read();"
                f"assert os.path.samefile(os.getcwd(),{str(self.worktree)!r});"
                f"assert open('/dev/fd/'+sys.argv[1],'rb').read()=={self.schema.read_bytes()!r};"
                f"sys.stdout.buffer.write({events!r});"
                "sys.stdout.buffer.flush()"
            )
            return subprocess.Popen(
                [sys.executable, "-c", script, str(schema_fd)],
                **kwargs,  # type: ignore[arg-type]
            )

        local_worker = CodexCliWorker(
            process_factory=local_factory,
            process_identity_reader=lambda pid: f"darwin-proc-bsdinfo-v1:{pid}:9",
            executable_preflight=lambda _deadline: None,
        )
        result = local_worker.start(self.reviewer_request(), _Sink())

        self.assertEqual("", result.error_code)
        self.assertEqual({}, result.final_response)
        self.assertFalse(result.final_response_validated)
        self.assertTrue(result.untrusted_final_response)
        kwargs = captured["kwargs"]
        self.assertEqual(str(self.worktree), kwargs["cwd"])
        self.assertEqual(str(self.worktree), captured["argv"][captured["argv"].index("--cd") + 1])  # type: ignore[index]
        self.assertEqual(1, len(tuple(kwargs["pass_fds"])))  # type: ignore[index]

    def test_postspawn_path_or_executable_revalidation_blocks_prompt(self) -> None:
        replacement = self.root / "replacement"
        replacement.mkdir()
        fake_factory = _Factory(_Process(_events()))

        def swap_worktree(argv: list[str], **kwargs: object) -> _Process:
            self.worktree.rename(self.root / "moved-worktree")
            replacement.rename(self.worktree)
            return fake_factory(argv, **kwargs)

        worker = CodexCliWorker(
            process_factory=swap_worktree,
            process_identity_reader=lambda pid: f"darwin-proc-bsdinfo-v1:{pid}:9",
            process_group_reader=lambda pid: pid,
            process_group_signaler=fake_factory.signal_group,
            process_group_exists=fake_factory.group_exists,
            executable_preflight=lambda _deadline: None,
        )
        result = worker.start(self.worker_request(), _Sink())
        self.assertEqual("worker_anchor_changed", result.error_code)
        self.assertEqual(b"", bytes(fake_factory.process.stdin.data))
        self.assertTrue(fake_factory.killed)

        from app.codex_cli_worker import _Anchor, _ExecutableBinding

        fd = os.open(self.schema, os.O_RDONLY)
        binding = _ExecutableBinding(_Anchor(fd, self.schema, (os.fstat(fd).st_dev, os.fstat(fd).st_ino, stat.S_IFMT(os.fstat(fd).st_mode), stat.S_IMODE(os.fstat(fd).st_mode), os.fstat(fd).st_uid)), "0" * 64)
        fake_factory = _Factory(_Process(_events()))
        checks = [0]

        def revalidate(_binding: object) -> None:
            checks[0] += 1
            if checks[0] == 2:
                from app.codex_cli_worker import _WorkerPhaseError
                raise _WorkerPhaseError("worker_executable_changed")

        worker = CodexCliWorker(
            process_factory=fake_factory,
            process_identity_reader=lambda pid: f"darwin-proc-bsdinfo-v1:{pid}:9",
            process_group_reader=lambda pid: pid,
            process_group_signaler=fake_factory.signal_group,
            process_group_exists=fake_factory.group_exists,
            executable_preflight=lambda _deadline: binding,
            executable_revalidator=revalidate,
        )
        result = worker.start(CodexWorkerRequest.worker(self.root / "moved-worktree", "No prompt.", 3), _Sink())
        self.assertEqual("worker_executable_changed", result.error_code)
        self.assertEqual(b"", bytes(fake_factory.process.stdin.data))
        self.assertTrue(fake_factory.killed)

    def test_identity_and_started_callback_precede_any_prompt_bytes(self) -> None:
        factory = _Factory(_Process(_events()))
        sink = _Sink(fail_started=True)

        result = self.worker(factory).start(self.worker_request(), sink)

        self.assertEqual("worker_started_sink_failed", result.error_code)
        self.assertEqual(b"", bytes(factory.process.stdin.data))
        self.assertTrue(factory.process.stdin.closed)
        self.assertTrue(factory.killed)

    def test_deadline_cancellation_and_blocked_stdin_are_before_output_drain(self) -> None:
        factory = _Factory(_Process(_events(), stdin_blocks=True))
        ticks = [0.0, 0.0, 0.0, 4.0]

        result = self.worker(factory, monotonic_clock=lambda: ticks.pop(0) if ticks else 4.0).start(
            self.worker_request(), _Sink()
        )

        self.assertEqual("worker_timeout", result.error_code)
        self.assertTrue(factory.killed)

    def test_strict_schema_and_every_anchored_parent_fail_closed(self) -> None:
        self.schema.write_text('{"type":"object","type":"array"}', encoding="utf-8")
        duplicate_hash = hashlib.sha256(self.schema.read_bytes()).hexdigest()
        request = CodexWorkerRequest.reviewer(self.worktree, "Review.", 3, self.schema, duplicate_hash)
        result = self.worker(_Factory(_Process(_events(reviewer_text='{}')))).start(request, _Sink())
        self.assertEqual("worker_request_invalid", result.error_code)
        self.schema.write_text('{"type":NaN}', encoding="utf-8")
        nan_hash = hashlib.sha256(self.schema.read_bytes()).hexdigest()
        request = CodexWorkerRequest.reviewer(self.worktree, "Review.", 3, self.schema, nan_hash)
        result = self.worker(_Factory(_Process(_events(reviewer_text='{}')))).start(request, _Sink())
        self.assertEqual("worker_request_invalid", result.error_code)
        linked = self.root / "linked-worktree"
        linked.symlink_to(self.worktree, target_is_directory=True)
        linked_factory = _Factory(_Process(_events()))
        linked_result = self.worker(linked_factory).start(
            CodexWorkerRequest.worker(linked, "No symlink cwd.", 3), _Sink()
        )
        self.assertEqual("worker_request_invalid", linked_result.error_code)
        self.assertEqual([], linked_factory.argv)

    def test_secret_identifiers_and_lifecycle_violations_never_reach_sink(self) -> None:
        bad_id = "ghp_" + "A" * 40
        cases = (
            [f'{{"type":"thread.started","thread_id":"{bad_id}"}}\n'.encode()],
            [b'{"type":"turn.failed","thread_id":"thread-1","turn_id":"turn-1"}\n'],
            [b'{"type":"thread.started","thread_id":"thread-1"}\n'],
            _events() + [b'{"type":"turn.completed","thread_id":"thread-1","turn_id":"turn-1"}\n'],
        )
        for stdout in cases:
            with self.subTest(stdout=stdout):
                factory = _Factory(_Process(stdout))
                sink = _Sink()
                result = self.worker(factory).start(self.worker_request(), sink)
                self.assertNotEqual("", result.error_code)
                self.assertNotIn(bad_id, repr(sink.events))
                self.assertTrue(factory.killed)

    def test_turn_failed_returns_only_bounded_protocol_audit(self) -> None:
        stdout = [
            b'{"type":"thread.started","thread_id":"thread-1"}\n',
            b'{"type":"turn.started","thread_id":"thread-1","turn_id":"turn-1"}\n',
            b'{"type":"turn.failed","thread_id":"thread-1","turn_id":"turn-1","error":{"message":"Bearer hidden-value"}}\n',
        ]

        result = self.worker(_Factory(_Process(stdout))).start(self.worker_request(), _Sink())

        self.assertEqual("worker_protocol_failed", result.error_code)
        self.assertIsInstance(result.protocol_rejection, ProtocolRejectionAudit)
        audit = result.protocol_rejection.as_mapping()
        self.assertEqual("turn.failed", audit["candidate_event_type"])
        self.assertEqual("missing", audit["candidate_item_type"])
        self.assertEqual("object", audit["error_container_kind"])
        self.assertEqual(1, audit["error_known_keys"])
        self.assertEqual(1, audit["error_field_count"])
        self.assertNotIn("message", repr(audit))
        self.assertNotIn("hidden-value", repr(audit))

    def test_turn_failed_error_shape_is_bounded_without_values_or_unknown_keys(self) -> None:
        stdout = [
            b'{"type":"thread.started","thread_id":"thread-1"}\n',
            b'{"type":"turn.started","thread_id":"thread-1","turn_id":"turn-1"}\n',
            b'{"type":"turn.failed","error":{"message":"Bearer hidden-value","code":"token_abcdefghijklmnopqrstuvwx","future_secret":"sk-abcdefghijklmnop"}}\n',
        ]

        result = self.worker(_Factory(_Process(stdout))).start(self.worker_request(), _Sink())

        audit = result.protocol_rejection.as_mapping()
        self.assertEqual("object", audit["error_container_kind"])
        self.assertEqual(5, audit["error_known_keys"])
        self.assertEqual(3, audit["error_field_count"])
        self.assertNotIn("future_secret", repr(audit))
        self.assertNotIn("token_", repr(audit))
        self.assertNotIn("sk-", repr(audit))

        with self.assertRaisesRegex(ValueError, "worker_protocol_audit_invalid"):
            validate_protocol_rejection_audit({**audit, "error_known_keys": 15, "error_field_count": 0})

    def test_bundled_jsonl_contract_allows_optional_turn_ids_and_safe_item_metadata(self) -> None:
        items = [
            {"type": "reasoning", "id": "reasoning-1", "text": "discarded"},
            {"type": "command_execution", "id": "command-1", "command": "discarded"},
            {"type": "file_change", "id": "file-1", "patch": "discarded"},
            {"type": "agent_message", "id": "message-1", "text": "discarded"},
        ]
        lines = [
            b'{"type":"thread.started","thread_id":"thread-1"}\n',
            b'{"type":"turn.started"}\n',
            *[
                (json.dumps({"type": "item.completed", "item": item}, separators=(",", ":")) + "\n").encode()
                for item in items
            ],
            b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n',
        ]
        sink = _Sink()
        result = self.worker(_Factory(_Process(lines))).start(self.worker_request(), sink)

        self.assertEqual("", result.error_code)
        self.assertEqual(7, result.event_count)
        self.assertEqual(
            ["thread.started", "turn.started", "item.completed", "item.completed",
             "item.completed", "item.completed", "turn.completed"],
            [event["type"] for event in sink.events],
        )
        self.assertTrue(all("discarded" not in repr(event) for event in sink.events))

    def test_recoverable_error_event_is_reduced_and_does_not_abort_a_completed_turn(self) -> None:
        stdout = [
            b'{"type":"thread.started","thread_id":"thread-1"}\n',
            b'{"type":"turn.started"}\n',
            b'{"type":"error","message":"untrusted transient transport detail"}\n',
            b'{"type":"turn.completed"}\n',
        ]
        sink = _Sink()

        result = self.worker(_Factory(_Process(stdout))).start(self.worker_request(), sink)

        self.assertEqual("", result.error_code)
        self.assertEqual("error", sink.events[2]["type"])
        self.assertNotIn("message", sink.events[2])

    def test_completed_error_item_is_reduced_and_does_not_abort_a_completed_turn(self) -> None:
        stdout = [
            b'{"type":"thread.started","thread_id":"thread-1"}\n',
            b'{"type":"turn.started"}\n',
            b'{"type":"item.completed","item":{"type":"error","message":"untrusted tool detail"}}\n',
            b'{"type":"turn.completed"}\n',
        ]
        sink = _Sink()

        result = self.worker(_Factory(_Process(stdout))).start(self.worker_request(), sink)

        self.assertEqual("", result.error_code)
        self.assertEqual({"type": "item.completed", "item_type": "error", "sequence_no": 3, "raw_line_sha256": sink.events[2]["raw_line_sha256"]}, sink.events[2])
        self.assertNotIn("message", sink.events[2])

    def test_reviewer_uses_last_agent_message_only_after_completed_turn(self) -> None:
        payload = '{"verdict":"approved","findings":[],"summary":"safe"}'
        factory = _Factory(_Process(_events(reviewer_text=payload)))

        result = self.worker(factory).start(self.reviewer_request(), _Sink())

        self.assertEqual("", result.error_code)
        self.assertEqual({"verdict": "approved", "findings": [], "summary": "safe"}, result.final_response)
        self.assertRegex(result.final_response_sha256, r"^[0-9a-f]{64}$")
        messages = _events(reviewer_text=payload)
        messages.insert(2, (
            json.dumps({"type":"item.completed","item":{"type":"agent_message","id":"item-progress","text":"Review in progress."}}, separators=(",", ":")) + "\n"
        ).encode())
        result = self.worker(_Factory(_Process(messages))).start(self.reviewer_request(), _Sink())
        self.assertEqual("", result.error_code)
        self.assertEqual({"verdict": "approved", "findings": [], "summary": "safe"}, result.final_response)

        no_terminal = messages[:-1]
        result = self.worker(_Factory(_Process(no_terminal))).start(self.reviewer_request(), _Sink())
        self.assertNotEqual("", result.error_code)
        secret_payload = '{"verdict":"approved","summary":"Bearer token-value-secret"}'
        result = self.worker(_Factory(_Process(_events(reviewer_text=secret_payload)))).start(
            self.reviewer_request(), _Sink()
        )
        self.assertEqual("worker_reviewer_response_invalid", result.error_code)
        overflow = '{"score":1e999}'
        result = self.worker(_Factory(_Process(_events(reviewer_text=overflow)))).start(
            self.reviewer_request(), _Sink()
        )
        self.assertEqual("worker_reviewer_response_invalid", result.error_code)

    def test_audit_hashes_sequences_and_sink_count_describe_delivered_events(self) -> None:
        factory = _Factory(_Process(_events()))
        sink = _Sink()

        result = self.worker(factory).start(self.worker_request(), sink)

        self.assertEqual(3, result.event_count)
        self.assertRegex(result.stdout_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(result.stderr_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual([1, 2, 3], [event["sequence_no"] for event in sink.events])
        self.assertTrue(all("raw_line_sha256" in event for event in sink.events))
        failed_sink = _Sink(fail_event=True)
        failed = self.worker(_Factory(_Process(_events()))).start(self.worker_request(), failed_sink)
        self.assertEqual("worker_event_sink_failed", failed.error_code)
        self.assertEqual(0, failed.event_count)

    def test_heartbeat_is_bounded_and_counted_only_after_delivery(self) -> None:
        from app.codex_cli_worker import _drain_events

        ticks = iter(value / 10 for value in range(80))
        sink = _Sink()
        response, response_hash, delivered, error = _drain_events(
            _Process([None] + _events()),
            WorkerRole.WORKER,
            sink,
            20.0,
            lambda: next(ticks),
            lambda: False,
            hashlib.sha256(),
            hashlib.sha256(),
            0,
        )
        self.assertEqual("", error)
        self.assertIsNone(response)
        self.assertEqual("", response_hash)
        self.assertEqual(delivered, len(sink.events))
        self.assertTrue(any(event["type"] == "worker.heartbeat" for event in sink.events))
        self.assertEqual(list(range(1, delivered + 1)), [event["sequence_no"] for event in sink.events])

    def test_event_limit_and_long_valid_timeout_heartbeat_budget(self) -> None:
        from app.codex_cli_worker import _drain_events

        many = [
            b'{"type":"thread.started","thread_id":"thread-1"}\n',
            b'{"type":"turn.started"}\n',
        ]
        many.extend(
            b'{"type":"item.started","item":{"type":"reasoning","id":"item-%d"}}\n' % index
            for index in range(258)
        )
        many.append(b'{"type":"turn.completed","usage":{}}\n')
        result = self.worker(_Factory(_Process(many))).start(self.worker_request(), _Sink())
        self.assertEqual("worker_event_overflow", result.error_code)
        self.assertEqual(256, result.event_count)

        value = [-1.0]
        def long_clock() -> float:
            value[0] += 1.0
            return value[0]
        sink = _Sink()
        response, response_hash, delivered, error = _drain_events(
            _Process([None] * 70 + _events()), WorkerRole.WORKER, sink, 3_600.0,
            long_clock, lambda: False, hashlib.sha256(), hashlib.sha256(), 0,
        )
        self.assertEqual("", error)
        self.assertIsNone(response)
        self.assertEqual("", response_hash)
        self.assertGreater(sum(event["type"] == "worker.heartbeat" for event in sink.events), 60)
        self.assertEqual(delivered, len(sink.events))

    def test_selectable_stdout_and_stderr_pipes_are_drained_fairly(self) -> None:
        from app.codex_cli_worker import _drain_events

        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        stdout = os.fdopen(stdout_read, "rb", buffering=0)
        stderr = os.fdopen(stderr_read, "rb", buffering=0)

        class PipeProcess:
            def __init__(self) -> None:
                self.stdout = stdout
                self.stderr = stderr

            def poll(self) -> int:
                return 0

        try:
            # Force framing to cross production `os.read` chunks without ever
            # splitting the final newline from its logical line.
            payload = b"".join(_events())
            os.write(stdout_write, payload[:17])
            os.write(stdout_write, payload[17:])
            os.write(stderr_write, b"diagnostic-one\ndiagnostic-two\n")
        finally:
            os.close(stdout_write)
            os.close(stderr_write)
        try:
            stdout_hash = hashlib.sha256()
            stderr_hash = hashlib.sha256()
            _, _, delivered, error = _drain_events(
                PipeProcess(), WorkerRole.WORKER, _Sink(), time.monotonic() + 2,
                time.monotonic, lambda: False, stdout_hash, stderr_hash, 0,
            )
            self.assertEqual("", error)
            self.assertEqual(3, delivered)
            self.assertNotEqual(hashlib.sha256().hexdigest(), stdout_hash.hexdigest())
            self.assertNotEqual(hashlib.sha256().hexdigest(), stderr_hash.hexdigest())
        finally:
            stdout.close()
            stderr.close()

    def test_bundled_version_preflight_uses_the_minimal_environment(self) -> None:
        from app.codex_cli_worker import _close_anchor, _minimal_environment, _preflight_executable

        self.assertEqual({
            "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TERM": "dumb",
            "PYTHONDONTWRITEBYTECODE": "1",
        }, _minimal_environment())
        binding = _preflight_executable(time.monotonic() + 3)
        self.addCleanup(_close_anchor, binding.anchor)

    def test_cleanup_failure_and_descendant_group_are_never_reported_as_success(self) -> None:
        factory = _Factory(_Process([None], hold_open=True, leader_exited=True))
        result = self.worker(factory).start(self.worker_request(), _Sink())
        self.assertEqual("worker_stream_unclosed", result.error_code)
        self.assertTrue(factory.terminated)
        self.assertTrue(factory.killed)
        failing_factory = _Factory(_Process(_events()))
        result = self.worker(
            failing_factory, process_group_exists=lambda _pgid: True
        ).start(self.worker_request(), _Sink())
        self.assertEqual("worker_cleanup_failed", result.error_code)
        self.assertNotEqual("", result.cleanup_error_code)

    def test_real_local_descendant_cleanup_is_no_network(self) -> None:
        script = (
            "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "print('ready',flush=True);time.sleep(30)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
        try:
            from app.codex_cli_worker import _cleanup_process_group

            self.assertEqual(b"ready\n", process.stdout.readline())
            outcome = _cleanup_process_group(
                process, process.pid, primary_error="worker_stream_unclosed", deadline=time.monotonic() + 2
            )
            self.assertTrue(outcome.group_extinct)
            self.assertTrue(outcome.leader_reaped)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


if __name__ == "__main__":
    unittest.main()
