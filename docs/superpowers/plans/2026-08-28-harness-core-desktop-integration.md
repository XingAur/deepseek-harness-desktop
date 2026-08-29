# Harness Core Desktop Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `/Users/lym/WorkCode/ai/Harness` 作为决策与治理内核接入 `deepseek-harness-desktop`，由 Harness 负责需求理解、证据闭环、方案决策、验证与失败重规划，由桌面端当前选定的 DeepSeek/Agent 负责执行。

**Architecture:** Harness Core 作为独立、可版本化的 Python Sidecar，通过双向 JSONL Host Bridge 与桌面端通信。桌面端只提供宿主适配、模型执行、MCP/Skill 工具和用户界面，不得自行改写 Harness 决策；所有模型结果、验证结果和用户纠正都回传 Harness，由 Harness 产生下一版权威决策。截图、附件和对话证据由桌面端显式导出到受控任务工作区，不能依赖抓取任意聊天历史。

**Tech Stack:** Harness Python 3 标准库、版本化 JSON/JSONL 合同、DeepSeek-Harness-Desktop Tauri 2 + Rust、Managed Node 24、React、TypeScript、现有 `@dsh/agent-adapter`、现有 Tauri IPC、Vitest、Rust unit tests。

## Global Constraints

- Harness Core 是唯一的治理与决策来源；模型/Agent 只能执行 Harness 下发的 execute-only 决策。
- Harness 不依赖 Codex CLI、Codex App 或 DeepSeek；这些只是可替换的宿主执行后端。
- 云效、GitLab、数据库、截图和附件能力由宿主提供的 Provider/MCP 接入；Harness 负责 capability、权限、证据和顺序治理。
- 未完成需求背景、场景、目标、项目和调用链证据时，不得进入改码执行。
- 医保、收费、退费、结算和外部写入保持 fail closed；外部写入、数据库写入、Git push、部署和云效写入必须保持独立授权。
- Token、模型名、线程号、原始 Provider payload 不进入 Harness 审计、提示词证据或错误返回。
- 不覆盖 `deepseek-harness-desktop` 当前未提交修改；当前已有 Agent Adapter 和 Codex 相关工作区修改，实施前必须完成相关 diff 分类。
- 现阶段先支持一个本机用户的单任务/单会话闭环；并发、多用户服务和远程部署另立计划。

---

### Task 1: 固化跨进程 Harness Host Bridge v1 合同

**Files:**
- Modify: `/Users/lym/WorkCode/ai/Harness/app/agent_backend_protocol.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/host_adapter.py`
- Create: `/Users/lym/WorkCode/ai/Harness/app/host_bridge_session.py`
- Create: `/Users/lym/WorkCode/ai/Harness/tools/harness_host_server.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_host_bridge_session.py`

**Interfaces:**
- Consumes: `AgentBackendRequest`, `AgentBackendResult`, `HostAdapterSession`.
- Produces: `harness-host-session.v1` 的 `session.start`、`agent.request`、`agent.result`、`session.event`、`session.cancel`、`session.end` JSONL 消息。

- [ ] **Step 1: Write the failing test**

```python
def test_session_round_trip_sends_agent_request_and_accepts_result():
    session = HostBridgeSession(
        send=lambda message: sent.append(message),
        receive=lambda: {
            "schema_version": "harness-host-session.v1",
            "type": "agent.result",
            "request_id": "req-1",
            "payload": {
                "exit_code": 0,
                "error_code": "",
                "event_count": 1,
                "final_response": {"schema_version": "his-local-agent-review.v1"},
                "final_response_sha256": "a" * 64,
                "canonical_final_response_sha256": "b" * 64,
                "final_response_validated": True,
            },
        },
    )
    result = session.execute(agent_request)
    assert result.exit_code == 0
    assert sent[0]["type"] == "agent.request"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_host_bridge_session -v`

Expected: FAIL because `HostBridgeSession` and the bidirectional message contract do not exist.

- [ ] **Step 3: Write minimal implementation**

Implement `HostBridgeSession.execute(request, sink)` with these fixed rules:

```python
class HostBridgeSession:
    def execute(self, request, sink):
        request_id = stable_request_id(request)
        self._send({"schema_version": HOST_SESSION_SCHEMA, "type": "agent.request", "request_id": request_id, "payload": request.to_dict()})
        response = self._receive_bounded()
        return parse_agent_result_response(response, request_id=request_id)
```

Reject unknown fields, oversized frames, mismatched request IDs, sensitive keys, duplicate responses, and unsupported result shapes with deterministic non-secret error codes. The server must handle one active session, `SIGTERM`, EOF, cancellation, and handler failures without printing exception text or credentials.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_host_bridge_session tests.test_host_adapter tests.test_agent_backend_protocol -v`

Expected: all tests pass and no test output contains `token`, `secret`, `api_key`, or raw provider payload.

- [ ] **Step 5: Commit**

Commit only in the Harness repository after the user authorizes Git delivery: `feat: add bidirectional desktop host bridge`.

### Task 2: Add a standalone Harness task-session entrypoint

**Files:**
- Create: `/Users/lym/WorkCode/ai/Harness/app/external_task_session.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/tools/harness_host_server.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/local_agent_runner.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_external_task_session.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_local_agent_runner.py`

**Interfaces:**
- Consumes: a validated `task.start` envelope containing an existing Harness task contract path, explicit worktree root, knowledge home, authorization ID, and selected role.
- Produces: `task.accepted`, `harness.decision`, `agent.request`, `agent.result`, `verification.updated`, `harness.replan`, and `task.completed`/`task.failed` events.

- [ ] **Step 1: Write the failing test**

```python
def test_task_session_does_not_execute_before_understanding_gate():
    result = run_session({"type": "task.start", "payload": incomplete_contract})
    assert result.error_code == "requirement_understanding_incomplete"
    assert sent_agent_requests == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_external_task_session -v`

Expected: FAIL because there is no external task-session dispatcher.

- [ ] **Step 3: Write minimal implementation**

Add a dispatcher that accepts only a validated task contract and delegates to the existing `LocalAgentRunner`. Before constructing a worker request it must load the persisted understanding, learning guard, role/capability route, and one-time authorization. Every retry must call `build_replan_decision` and persist a new plan version. The dispatcher must never accept arbitrary executable shell text or a model-owned plan.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_external_task_session tests.test_harness_learning_guard tests.test_local_agent_runner -v`

Expected: existing learning/replan tests remain green and incomplete understanding produces zero Agent requests.

- [ ] **Step 5: Commit**

Commit only after explicit Git delivery authorization: `feat: expose governed Harness task session`.

### Task 3: Create the desktop TypeScript Host Bridge client

**Files:**
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/harness-bridge.ts`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/package.json`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/protocol.ts`
- Test: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/harness-bridge.test.ts`

**Interfaces:**
- Consumes: `harness-host-session.v1` JSONL from the Python sidecar.
- Produces: `HarnessBridgeClient.startTask`, `cancelTask`, `sendAgentResult`, `onEvent`, and bounded process lifecycle errors.

- [ ] **Step 1: Write the failing test**

```ts
it('rejects a response with a mismatched request id', async () => {
  const client = new HarnessBridgeClient(fakeTransport([
    { schema_version: 'harness-host-session.v1', type: 'agent.result', request_id: 'other', payload: validResult },
  ]))
  await expect(client.awaitAgentResult('req-1')).rejects.toThrow('请求关联失败')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -w @dsh/agent-adapter -- harness-bridge.test.ts`

Expected: FAIL because the desktop client and protocol types do not exist.

- [ ] **Step 3: Write minimal implementation**

Implement a child-process transport with fixed executable/argument arrays, bounded frame size, UTF-8 JSONL parsing, timeout, cancellation, exit handling, and redacted diagnostics. Do not pass tokens or raw model/provider payloads to the sidecar. Keep the process transport injectable so tests use an in-memory fake.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -w @dsh/agent-adapter -- harness-bridge.test.ts && npm run agent:build`

Expected: bridge tests pass and package declarations/build complete.

- [ ] **Step 5: Commit**

Do not commit yet if the repository contains unrelated user changes; record the diff boundary first.

### Task 4: Implement the Desktop Host Handler using the selected DeepSeek Agent

**Files:**
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/harness-host-handler.ts`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/adapters/index.ts`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/protocol.ts`
- Test: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/harness-host-handler.test.ts`

**Interfaces:**
- Consumes: Harness `agent.request` with role, worktree, prompt, output contract and capability list.
- Produces: sanitized `agent.result` plus semantic events; uses the existing selected DeepSeek/OpenAI-compatible adapter or an explicitly configured Agent adapter.

- [ ] **Step 1: Write the failing test**

```ts
it('executes only the Harness prompt and returns a validated result', async () => {
  const handler = createHarnessHostHandler({ execute: async (request) => fakeResultFor(request) })
  const result = await handler(validAgentRequest)
  expect(result.final_response_validated).toBe(true)
  expect(result.error_code).toBe('')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -w @dsh/agent-adapter -- harness-host-handler.test.ts`

Expected: FAIL because no Harness-specific host handler exists.

- [ ] **Step 3: Write minimal implementation**

Adapt the existing Agent adapter interfaces without creating a second model provider. The handler must not re-plan, add files, broaden capabilities, or change the worktree. It forwards the exact execute-only prompt, maps model/tool events into the Harness result contract, and classifies timeout, provider, cancellation, protocol, and permission failures without returning secret-bearing text.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run agent:test && npm run agent:build`

Expected: all existing Agent adapter tests and the new Handler tests pass.

- [ ] **Step 5: Commit**

Keep the existing Codex-related dirty files intact; commit only the Harness adapter files after review.

### Task 5: Add Tauri lifecycle and user authorization boundary

**Files:**
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/src/harness/mod.rs`
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/src/harness/model.rs`
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/src/harness/process.rs`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/src/lib.rs`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/src/commands.rs`
- Test: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/src/harness/mod.rs`

**Interfaces:**
- Consumes: desktop-side `HarnessBridgeClient` lifecycle requests.
- Produces: typed Tauri commands for `harness_status`, `harness_start`, `harness_cancel`, and one-time local authorization; no arbitrary command execution from the renderer.

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn harness_command_rejects_unregistered_sidecar_path() {
    let result = validate_sidecar_path(Path::new("/tmp/untrusted-harness"), &allowed_root());
    assert_eq!(result, Err(HarnessError::SidecarPathNotAllowed));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --manifest-path src-tauri/Cargo.toml harness::`

Expected: FAIL because the Harness lifecycle module does not exist.

- [ ] **Step 3: Write minimal implementation**

Follow the existing Rust runtime/process patterns: resolve only a bundled or explicitly configured development sidecar, pass a minimal environment, use fixed arguments, bind stdin/stdout, terminate on cancellation and application shutdown, and store only redacted status. The renderer must call typed commands from the centrally registered command list.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --manifest-path src-tauri/Cargo.toml harness:: && npm run build:web`

Expected: Rust Harness tests and the web build pass.

- [ ] **Step 5: Commit**

Do not add a production sidecar binary until the packaging and signature task is approved.

### Task 6: Add a desktop “Harness 任务” entry and evidence export

**Files:**
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/harness/HarnessTaskPanel.tsx`
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/harness/harness-task-state.ts`
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/harness/evidence-export.ts`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/advanced-shell.tsx`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/bridge-contract.ts`
- Test: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/harness/harness-task-state.test.ts`
- Test: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/harness/evidence-export.test.ts`

**Interfaces:**
- Consumes: user-selected requirement text, project path, explicit image/file attachments, and Harness events.
- Produces: a structured `task.start` envelope and a visible state machine: collecting → understanding → deciding → executing → verifying → replanning → completed/needs-user.

- [ ] **Step 1: Write the failing test**

```ts
it('does not start execution when background, scenario, goal, or project is missing', () => {
  expect(canStartHarnessTask({ background: '', scenario: 'x', goal: 'x', projectPath: '/p' })).toBe(false)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -w @dsh/desktop-plugin -- harness-task-state.test.ts evidence-export.test.ts`

Expected: FAIL because the Harness task state and evidence exporter do not exist.

- [ ] **Step 3: Write minimal implementation**

Create a task-specific UI entry, not a replacement for ordinary chat. The panel must collect or import the requirement background, scenarios, goal, desired outcome, target project, screenshots, documents, and conversation evidence. It must copy selected files into a bounded task workspace, record media type/size/hash, and send references rather than embedding large binary data in prompts. It must display missing evidence and Harness replan reasons in Chinese.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -w @dsh/desktop-plugin && npm run plugin:build && npm run build:web`

Expected: plugin tests, plugin build, and desktop web build pass.

- [ ] **Step 5: Commit**

Keep the ordinary DeepSeek chat path unchanged; Harness must be an explicit governed task mode.

### Task 7: Wire Skills/MCP/Provider credentials without bypassing Harness

**Files:**
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/model-agent/state.ts`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/extensions/McpServerDialog.tsx`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/mcp/client.ts`
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/harness-capability-map.ts`
- Test: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/harness-capability-map.test.ts`

**Interfaces:**
- Consumes: desktop Profile's enabled MCP/Skill/Provider descriptors and OS credential references.
- Produces: a non-secret capability advertisement and controlled tool-result envelopes consumed by Harness.

- [ ] **Step 1: Write the failing test**

```ts
it('never advertises a credential value or an unapproved mutation capability', () => {
  const advertised = buildHarnessCapabilityMap({ token: 'secret', enabledMcp: ['yunxiao.read'], allowWrite: false })
  expect(JSON.stringify(advertised)).not.toContain('secret')
  expect(advertised.capabilities).not.toContain('yunxiao.write')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -w @dsh/agent-adapter -- harness-capability-map.test.ts`

Expected: FAIL because no Harness capability projection exists.

- [ ] **Step 3: Write minimal implementation**

Expose only capability IDs, provider IDs, scopes and mutation levels. Resolve Yunxiao/GitLab/database credentials inside the desktop provider boundary; never serialize values into Harness requests. Read-only Yunxiao, GitLab and database capabilities may be advertised only after their provider checks pass. Write capabilities remain disabled unless a separate user confirmation path exists.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run agent:test && npm run plugin:test && cargo test --manifest-path src-tauri/Cargo.toml`

Expected: no credential leak, capability tests pass, and Rust permission tests remain green.

- [ ] **Step 5: Commit**

Review the capability diff separately from the transport diff.

### Task 8: Implement screenshot/conversation evidence handoff and requirement archive mapping

**Files:**
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/harness/evidence-export.ts`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-agent-adapter/src/harness-bridge.ts`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/conversation_evidence.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/error_chain_closure.py`
- Test: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/packages/dsh-plugin-desktop/src/client/harness/evidence-export.test.ts`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_conversation_evidence.py`

**Interfaces:**
- Consumes: explicitly selected chat images, local attachments, exported conversation text and Yunxiao archive references.
- Produces: `conversation-evidence.v1` plus `visual.extract` input references and verified local hashes.

- [ ] **Step 1: Write the failing test**

```ts
it('exports an image as a bounded local evidence reference, not just a path string', async () => {
  const evidence = await exportEvidence([imageFile])
  expect(evidence.files[0].sha256).toMatch(/^[0-9a-f]{64}$/)
  expect(evidence.files[0].mediaType).toBe('image/png')
  expect(evidence.files[0].byteLength).toBeGreaterThan(0)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -w @dsh/desktop-plugin -- evidence-export.test.ts && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_conversation_evidence -v`

Expected: FAIL if the evidence export does not create a verified content reference.

- [ ] **Step 3: Write minimal implementation**

Copy selected evidence into a task-owned directory, calculate SHA-256, preserve the original filename as metadata, reject symlinks/oversized files, and create one JSON manifest. The desktop client must explicitly mark whether the evidence came from the current chat, Yunxiao archive, or local selection. Harness visual evidence must fail closed when the host has not actually supplied readable image content.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run plugin:test && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_conversation_evidence tests.test_error_chain_closure -v`

Expected: evidence hashes and provenance pass; missing visual evidence remains blocked instead of becoming a guessed code path.

- [ ] **Step 5: Commit**

Do not upload evidence or write Yunxiao; this task is local handoff only.

### Task 9: Add packaging, development configuration, and real smoke verification

**Files:**
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/resources/harness/README.md`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/tauri.conf.json`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/src-tauri/capabilities/bootstrap.json`
- Create: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/scripts/harness-smoke.mjs`
- Modify: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/package.json`
- Test: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/e2e/specs/harness-smoke.e2e.ts`

**Interfaces:**
- Consumes: built Harness Sidecar, signed desktop runtime, configured local model Provider and fake evidence fixture.
- Produces: development run mode, packaged sidecar manifest, protocol handshake smoke result and a release readiness report.

- [ ] **Step 1: Write the failing test**

```ts
it('reports harness unavailable when the sidecar signature or health check fails', async () => {
  const result = await startHarnessWithFixture({ signature: 'invalid' })
  expect(result.status).toBe('unavailable')
  expect(result.canExecute).toBe(false)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- harness-smoke.e2e.ts`

Expected: FAIL because no packaged Harness lifecycle or smoke harness exists.

- [ ] **Step 3: Write minimal implementation**

Add a development-only configuration pointing to the local Harness checkout and a production manifest for a bundled, versioned, signed Sidecar. Verify hash/signature before activation, keep last-known-good version, and never silently fall back to an arbitrary Python executable in production. Add a fake sidecar to the E2E fixture so the protocol handshake, requirement gate, image evidence handoff, model execution result, verification failure, and Harness replan can be tested deterministically.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
npm run agent:test
npm run plugin:test
npm run build:web
cargo test --manifest-path src-tauri/Cargo.toml --locked
npm run e2e:fixtures
npm run e2e:build
```

Expected: all targeted tests pass; real model smoke remains a separately authorized test and is never implied by the fake-sidecar result.

- [ ] **Step 5: Commit**

Before any release commit, inspect the full diff, verify no credentials or user data are packaged, and obtain separate authorization for signing/releasing.

## Verification and Acceptance

The integration is complete only when all of these are true:

1. Desktop can start/stop a version-verified Harness Sidecar without Codex CLI.
2. Harness receives the full requirement context and blocks execution when background, scenario, goal, project or call-chain evidence is missing.
3. Harness emits a persisted, versioned decision before every Agent execution.
4. The desktop model executes only the received decision and cannot re-plan or broaden scope.
5. A failed model/verification result returns to Harness and produces a new decision version within the configured repair budget.
6. A user correction is persisted and prevents the same matched error from recurring on the next compatible task.
7. Explicitly selected screenshots, attachments and conversation evidence reach Harness as verified content references with hashes and provenance.
8. Yunxiao/GitLab/database/MCP/Skill credentials remain in the desktop credential boundary and never enter prompts or audit payloads.
9. Ordinary DeepSeek chat continues to work unchanged; Harness is an explicit governed task path.
10. Fake-sidecar E2E passes, and any real DeepSeek smoke is separately labeled as runtime evidence rather than code-level support.

## Plan Self-Review

- Spec coverage: governance core, bidirectional transport, model execution, Tauri lifecycle, UI, MCP/Skill capability mapping, evidence export, self-learning/replan and packaging are covered by Tasks 1–9.
- Known gap: multi-user remote service, team authentication, auto-update distribution and cloud-hosted Harness are intentionally excluded from this first integration and require a separate plan.
- No code step depends on an undefined provider API; the new boundaries are named in each task and remain injectable for tests.
- Existing dirty Codex/Agent changes in the Desktop repository must be preserved and reviewed before any overlapping file is edited.
