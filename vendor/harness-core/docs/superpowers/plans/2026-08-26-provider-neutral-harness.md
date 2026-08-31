# Provider-Neutral Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Harness Core usable from terminal, Codex App, Codex CLI, and DeepSeek-Harness-Desktop without making any one AI client or CLI a mandatory dependency.

**Architecture:** Keep the existing Harness Core responsible for task intent, role routing, capability/Skill/MCP governance, worktree safety, verification, review, history, and evidence. Introduce a provider-neutral Agent Backend contract and a host bridge protocol; the current Codex CLI worker becomes one optional backend, while other clients can implement the same contract or call the bridge. Preserve the existing local-agent behavior when `codex-cli` is explicitly selected, and fail closed when no backend is configured rather than silently choosing a privileged fallback.

**Tech Stack:** Python 3, stdlib dataclasses/enums/json, existing SQLite repository, existing unittest suite, JSONL/stdio bridge.

## Global Constraints

- Do not modify the formal database `/Users/lym/WorkCode/ai/Harness/data/harness.sqlite`.
- Do not require or read provider credentials in the generic bridge.
- Do not bypass current worktree, verification, review, human-confirmation, or external-write gates.
- Keep `CodexCliWorker` compatible for existing callers and tests.
- Do not claim Codex App or DeepSeek desktop runtime integration until a real host adapter completes the protocol handshake.
- The Harness repository has no Git metadata in the current workspace; record changes and verification without commit/push.

---

### Task 1: Define the provider-neutral Agent Backend contract

**Files:**
- Create: `app/agent_backend.py`
- Create: `tests/test_agent_backend.py`
- Inspect/compatibility: `app/codex_cli_worker.py`, `app/local_agent_runner.py`, `app/local_agent_review.py`

**Interfaces:**
- `AgentBackend` protocol exposes `start(request, sink) -> AgentResult`.
- `AgentBackendDescriptor` describes backend id, transport, supported roles, and availability without exposing credentials.
- `AgentBackendRegistry` validates backend descriptors and resolves an explicit backend id.
- Existing `CodexWorkerRequest`, `CodexWorkerResult`, and `WorkerRole` remain usable through compatibility aliases or adapters.

- [x] **Step 1: Write failing tests** for descriptor validation, backend protocol compatibility, explicit backend resolution, and fail-closed unknown backend handling.
- [x] **Step 2: Run** `python3 -m unittest tests.test_agent_backend -v`; expect failures because the generic contract is not present.
- [x] **Step 3: Implement** only the dataclasses, protocol, descriptor validation, and registry needed by those tests; do not move Codex process code yet.
- [x] **Step 4: Run** the targeted test and existing `tests.test_codex_cli_worker`; both must pass.

### Task 2: Add a host bridge protocol independent of Codex CLI

**Files:**
- Create: `app/agent_backend_protocol.py`
- Create: `tools/harness_agent_bridge.py`
- Create: `tests/test_agent_backend_protocol.py`
- Modify: `README.md`
- Modify: `docs/role-capability-skill-matrix.md`

**Interfaces:**
- Request schema: `his-agent-backend-request.v1` with backend-neutral role, prompt, worktree path, timeout, output contract, and capability snapshot.
- Event schema: `his-agent-backend-event.v1` with bounded event type and sequence number; opaque model/thread identifiers are never persisted.
- Result schema: `his-agent-backend-result.v1` with exit status, safe digests, structured response, and bounded error code.
- `tools/harness_agent_bridge.py describe` emits a single JSON descriptor; `validate-request` validates a request without starting a model or touching the formal database.

- [x] **Step 1: Write failing tests** for schema validation, no secret/opaque identifier retention, role normalization, and JSON-only CLI output.
- [x] **Step 2: Run** `python3 -m unittest tests.test_agent_backend_protocol -v`; expect schema/CLI failures.
- [x] **Step 3: Implement** the versioned protocol and bridge commands using stdlib only. The bridge must not spawn Codex, call a network endpoint, read credentials, or write the Harness database.
- [x] **Step 4: Run** targeted protocol tests and verify `python3 tools/harness_agent_bridge.py describe` returns one valid JSON object.

### Task 3: Make local-agent backend selection explicit and optional

**Files:**
- Create: `config/agent_backends.json`
- Create: `app/agent_backend_factory.py`
- Modify: `app/local_agent_runner.py`
- Modify: `tools/task_manager.py`
- Create: `tests/test_agent_backend_factory.py`

**Interfaces:**
- `build_agent_backend(backend_id: str | None = None)` resolves only configured backends.
- `codex-cli` maps to the existing `CodexCliWorker` and retains its executable/signature gate.
- `host-bridge` is a protocol-only integration point; without a registered host callback/process it returns a stable `worker_backend_unavailable` result and never mutates state outside the normal failed-worker evidence path.
- CLI option `--agent-backend` is accepted for `run`, `retry`, and `auto-repair`; persisted run behavior remains bound to the selected backend.

- [x] **Step 1: Write failing tests** proving Harness Core imports and manager status do not instantiate or preflight Codex CLI, explicit `codex-cli` preserves the current path, and `host-bridge` fails closed without a host adapter.
- [x] **Step 2: Run** `python3 -m unittest tests.test_agent_backend_factory tests.test_local_agent_runner tests.test_local_agent_cli -q`; expect factory/CLI failures before implementation.
- [x] **Step 3: Implement** the factory and config validation. Change `LocalAgentRunner` to receive a backend through the factory while preserving dependency injection in existing tests.
- [x] **Step 4: Run** targeted tests and confirm the invalid current Codex signature affects only `codex-cli`, not core status, planning, bridge validation, or mock/replay flows.

### Task 4: Add client integration contracts for four entry points

**Files:**
- Modify: `README.md`
- Modify: `docs/manager-runbook.md`
- Modify: `docs/role-capability-skill-matrix.md`
- Create: `docs/host-integration-contract.md`
- Create: `tests/test_host_integration_contract.py`

**Interfaces:**
- Terminal and Codex CLI use the existing Harness CLI plus `--agent-backend`.
- Codex App and DeepSeek-Harness-Desktop use the same bridge request/result contract; they are clients, not special providers.
- Every host must advertise supported roles/capabilities and receive a deterministic rejection when a requested capability or mutation level is not supported.

- [x] **Step 1: Write failing tests** for the four host descriptors, capability negotiation, and the rule that a host name never changes authorization level.
- [x] **Step 2: Run** `python3 -m unittest tests.test_host_integration_contract -v`; expect missing integration contract failures.
- [x] **Step 3: Implement** the host descriptor/negotiation helpers and documentation examples for stdio invocation; do not claim actual desktop transport support without a host implementation.
- [x] **Step 4: Run** targeted tests and validate all examples against the bridge parser.

### Task 5: Verify the complete change and close the boundary honestly

**Files:**
- Inspect all changed files and generated test artifacts.
- Modify only if required by verification findings.

- [x] **Step 1: Run** the new targeted suite: `python3 -m unittest tests.test_agent_backend tests.test_agent_backend_protocol tests.test_agent_backend_factory tests.test_host_integration_contract -q`.
- [x] **Step 2: Run** existing capability, dynamic planning, local-agent, review, and database-governance tests with an isolated temporary database.
- [x] **Step 3: Run** `python3 tools/capability_check.py --config config/capabilities.json validate --json`.
- [x] **Step 4: Inspect the full diff and verify** no formal database, provider credentials, external writes, commits, pushes, or signature bypasses occurred.
- [x] **Step 5: Report** separately: core code status, backend status, client protocol status, actual runtime evidence, and remaining host-specific work.

## Self-Review Checklist

- The plan separates Skill instructions, Harness Core, client transports, and execution backends.
- Codex CLI is optional at the architecture level; its existing security gate remains intact when selected.
- “Learning” remains a bounded improvement subsystem and is not required for host connectivity.
- The plan does not claim a DeepSeek desktop adapter exists before its protocol is implemented and tested.
- All implementation steps have concrete files, interfaces, tests, and commands.

### Follow-up increment: host adapter session

- [x] Add `app/host_adapter.py` as the shared JSONL/in-process host adapter session.
- [x] Cover valid delivery, invalid request, handler exception, and non-generic result rejection.
- [x] Document the injection point for Codex App and DeepSeek-Harness-Desktop without claiming
  that either host SDK is already connected.

### Follow-up increment: Codex App Server backend

- [x] Confirm the locally bundled `codex app-server` runtime exposes stdio JSON-RPC.
- [x] Add `app/codex_app_server_worker.py` as an explicit, lazy `codex-app-server` backend.
- [x] Cover the fixed handshake, isolated sandbox policy, reviewer JSON result and protocol rejection
  with an injected fake process; no live model task is run by the test suite.
- [x] Map the `codex-app` host descriptor to the App Server backend while keeping the global default
  as fail-closed `host-bridge`.
