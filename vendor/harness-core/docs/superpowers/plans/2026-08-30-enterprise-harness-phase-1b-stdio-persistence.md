# Enterprise Harness Phase 1B Stdio Transport and Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Harness 增加可真实启动冻结插件 MCP Server 的有界 stdio transport，以及可跨进程恢复、追加写、可校验的本地 MCP Evidence Store 与 Audit Ledger；本增量不切换任何生产 capability 路由。

**Architecture:** Harness 从已通过 `plugin_inventory.json` 哈希校验的插件 `.mcp.json` 生成只读启动描述，使用单次进程、JSONL、固定 initialize/list/call 序列执行 MCP。Gateway 保持唯一调用入口，成功 envelope 写入独立 `mcp.sqlite`，所有终态写入哈希链审计账本；现有 `harness.sqlite` schema 72 和 Provider compatibility 路由保持不变。

**Tech Stack:** Python 3.11+ 标准库、`subprocess`/`selectors`/POSIX process group、JSON-RPC 2.0/MCP stdio、SQLite WAL、现有 `unittest`、PluginInventory 与 MCP Gateway 合同。

## Global Constraints

- 不修改、删除、重建或迁移现有 `data/harness.sqlite`、运行记录、插件目录、凭证、缓存或备份。
- MCP 持久化使用调用者显式指定的新文件 `mcp.sqlite`；遇到未知 schema 或损坏数据必须 fail closed，不自动覆盖。
- 只接受已冻结插件 `.mcp.json` 中的 `python3 + 单个相对 .py 入口`；运行时使用当前受控 Python 的绝对解析路径，不执行 shell。
- 子进程环境仅包含固定安全变量和 `.mcp.json.env_vars` 指名的值；不得透传完整父进程环境，不记录值。
- 每次调用只启动一次进程，不自动重试；执行 initialize、tools/list、tools/call 后关闭 stdin 并等待退出。
- 超时、取消、输出超限、协议漂移和非零退出都必须清理整个进程组并返回统一不可用错误，不返回 stderr 或异常原文。
- MCP stdout 与 stderr 都有独立字节上限；最终只把 `structuredContent` 交给现有 Gateway 校验。
- Evidence 与 Audit 只接受现有 `mcp_audit.py` 的严格安全合同；Evidence 按 request/payload 哈希幂等，Audit 追加写并形成 SHA-256 链。
- 云效描述符继续 `enabled=false`，路由继续 `provider/compatibility`；GitLab/PostgreSQL 描述符继续禁用。
- Harness 与插件源目录都不是 Git 仓库；以预修改文件清单、逐文件审查、测试和哈希结果替代 commit。

---

## File Map

- Modify: `app/mcp_audit.py` — 提取供内存与 SQLite 实现共同使用的 evidence/audit 安全规范化函数。
- Create: `app/mcp_stdio_transport.py` — 冻结插件 MCP 启动描述解析、单次有界 stdio 执行、超时/取消和进程组清理。
- Create: `app/mcp_persistence.py` — 独立 SQLite Evidence Store 与 hash-chained Audit Ledger。
- Create: `app/mcp_runtime_factory.py` — 从 MCP manifest、PluginInventory、插件根和显式状态目录组装 runtime bundle。
- Create: `tests/fixtures/mcp_stdio_fixture_server.py` — 离线健康、协议错误、超时、超限和 stderr fixture。
- Create: `tests/test_mcp_stdio_transport.py` — transport 配置、协议、边界和清理测试。
- Create: `tests/test_mcp_persistence.py` — 持久化、幂等、追加写、篡改检测和恢复测试。
- Create: `tests/test_mcp_runtime_factory.py` — 冻结插件到 runtime 的完整组装测试。
- Create: `tests/test_mcp_phase_1b_runtime_acceptance.py` — 跨模块发布边界验收。
- Modify: `config/external_io_boundaries.v1.json` — 仅登记新的 MCP control-plane `subprocess.Popen` finding 与实际哈希。
- Modify: `scripts/verify.sh` — 将新测试加入 architecture gate。
- Modify: `tests/test_verify_entrypoint.py` — 冻结新增 architecture 模块。
- Modify: `README.md`、`CHANGELOG.md` — 分别报告 transport、持久化、真实路由和剩余边界。

---

### Task 1: Freeze the Stdio Launch and Protocol Contract

**Interfaces:**

- Consumes: `VerifiedPlugin.root`, `VerifiedPlugin.source(".mcp.json")` 和冻结入口源码。
- Produces: `StdioMcpServerConfig`, `load_stdio_server_configs(...)`, `StdioMcpTransport.call(...)`。

- [x] **Step 1: Write failing configuration tests**

在 `tests/test_mcp_stdio_transport.py` 先定义以下期望：

```python
configs = load_stdio_server_configs({"fixture": verified_plugin})
config = configs["fixture"]
self.assertEqual("fixture", config.server)
self.assertEqual(("scripts/mcp_stdio_fixture_server.py",), config.args)
self.assertEqual(("MCP_FIXTURE_MODE",), config.env_vars)
```

负向用例必须覆盖未知字段、重复 server、shell command、绝对/逃逸参数、非 `.py` 入口、入口未冻结、符号链接、危险环境变量和源码哈希漂移。

- [x] **Step 2: Run RED configuration tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_mcp_stdio_transport.StdioMcpConfigurationTests -v
```

Expected: FAIL because `app.mcp_stdio_transport` does not exist.

- [x] **Step 3: Implement the strict launch descriptor**

Create these public contracts:

```python
@dataclass(frozen=True)
class StdioMcpServerConfig:
    server: str
    root: Path
    args: tuple[str, ...]
    env_vars: tuple[str, ...]
    source_sha256: str

def load_stdio_server_configs(
    verified_plugins: Mapping[str, VerifiedPlugin],
) -> Mapping[str, StdioMcpServerConfig]: ...
```

Parser accepts exactly `{"mcpServers": {...}}`; each server accepts exactly `command,args,cwd,env_vars`, with `command="python3"`, `cwd="."`, one safe relative `.py` argument and no dangerous environment key. Store the frozen source digest and re-check it immediately before process launch.

- [x] **Step 4: Write failing protocol and lifecycle tests**

Tests must prove:

- one process receives exactly initialize, tools/list and tools/call;
- initialize server name, requested read-only tool and response IDs match;
- only `structuredContent` is returned;
- unknown server/tool, malformed JSON, duplicate/extra response, missing read-only annotations, output/stderr overflow and nonzero exit fail closed;
- timeout and cancellation terminate the process group;
- process command is an argv list with `shell=False`, `start_new_session=True` and a minimal allowlisted environment;
- no exception contains fixture stderr or environment values;
- no retry occurs.

- [x] **Step 5: Run RED protocol tests**

Run the full `tests.test_mcp_stdio_transport`; expected failure is the missing concrete transport behavior, not fixture syntax.

- [x] **Step 6: Implement minimal bounded stdio execution**

Expose:

```python
class StdioMcpTransport:
    def __init__(
        self,
        *,
        servers: Mapping[str, StdioMcpServerConfig],
        environment: Mapping[str, str] | None = None,
        python_executable: str | Path | None = None,
        cancelled: Callable[[], bool] | None = None,
        max_stdout_bytes: int = 1_048_576,
        max_stderr_bytes: int = 65_536,
    ) -> None: ...

    def call(
        self,
        *,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        timeout_seconds: int,
        trace_id: str,
    ) -> Mapping[str, Any]: ...
```

Use binary pipes and bounded selector reads. On every exit path close streams and reap/terminate the process group. Return only the call result's `structuredContent` mapping.

- [x] **Step 7: Run GREEN transport tests**

Run `tests.test_mcp_stdio_transport -v`; expected all tests pass with no network access.

---

### Task 2: Add Persistent Evidence and Audit Storage

**Interfaces:**

- Consumes: strict normalization from `app.mcp_audit`.
- Produces: `SqliteMcpStore.store`, `record`, `load_evidence`, `list_audit_events`, `verify_integrity`。

- [x] **Step 1: Write failing persistence tests**

`tests/test_mcp_persistence.py` must cover:

```python
first = SqliteMcpStore(path)
ref = first.store(request_id="request-1", capability="workitem.read", provider="yunxiao", payload=envelope)
first.record(audit_event(evidence_ref=ref))
second = SqliteMcpStore(path)
self.assertEqual(envelope, second.load_evidence(ref))
self.assertEqual("passed", second.verify_integrity()["status"])
```

Also cover identical replay idempotency, conflicting payload rejection, exact audit fields, append ordering, process restart, update/delete trigger rejection, unknown schema rejection, evidence hash mismatch, audit-chain tampering, SQLite file mode, and no writes to `database.DB_PATH`.

- [x] **Step 2: Run RED persistence tests**

Run `tests.test_mcp_persistence -v`; expected import failure for `app.mcp_persistence`.

- [x] **Step 3: Refactor shared safety preparation**

In `app/mcp_audit.py` add:

```python
def prepare_mcp_evidence(...) -> tuple[dict[str, Any], bytes, str]: ...
def prepare_mcp_audit_event(event: Mapping[str, Any]) -> dict[str, Any]: ...
```

The existing in-memory sinks must call these helpers so their externally observed behavior remains unchanged.

- [x] **Step 4: Implement an independent append-only SQLite store**

`SqliteMcpStore` accepts one explicit absolute path, creates only that database and uses schema version `his-mcp-store.v1`. Tables are:

```text
mcp_store_meta(schema_version, created_at)
mcp_evidence_records(evidence_ref, request_id, capability, provider,
                     payload_json, payload_sha256, created_at)
mcp_audit_events(id, event_json, previous_event_hash, event_hash,
                 request_id, trace_id, task_id, run_id, created_at)
```

Add indexes for request/trace/task/run lookup and triggers rejecting update/delete on evidence and audit tables. Audit hash is SHA-256 over `previous_event_hash + "\n" + canonical_event_bytes`.

- [x] **Step 5: Implement recovery reads and integrity verification**

`load_evidence` recomputes payload hash/reference before returning a deep copy. `list_audit_events` verifies every link and event hash before returning immutable snapshots. `verify_integrity` runs SQLite `integrity_check`, evidence verification and audit-chain verification without repairing data.

- [x] **Step 6: Run GREEN persistence and Gateway tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_mcp_persistence tests.test_mcp_gateway -v
```

Expected: persistent tests pass and all existing in-memory Gateway behavior remains unchanged.

---

### Task 3: Compose a Production-Safe but Disabled Runtime

**Interfaces:**

- Consumes: MCP capability manifest, frozen plugin inventory, explicit plugin roots and explicit state root.
- Produces: `PersistentMcpRuntimeBundle` and `build_persistent_mcp_runtime(...)`。

- [x] **Step 1: Write failing factory tests**

Tests require successful assembly from a temporary frozen plugin, persistent store reuse after a second build, and an unchanged disabled descriptor result that never launches a process. Negative cases cover inventory drift, missing server config, unsafe state path and nonmatching plugin roots.

- [x] **Step 2: Run RED factory tests**

Run `tests.test_mcp_runtime_factory -v`; expected import failure for `app.mcp_runtime_factory`.

- [x] **Step 3: Implement the explicit factory**

Expose:

```python
@dataclass(frozen=True)
class PersistentMcpRuntimeBundle:
    registry: McpCapabilityRegistry
    store: SqliteMcpStore
    transport: StdioMcpTransport
    gateway: McpGateway
    runtime: McpCapabilityRuntime

def build_persistent_mcp_runtime(
    *,
    harness_root: Path,
    manifest_path: Path,
    plugin_inventory_path: Path,
    plugin_roots: Sequence[Path],
    state_root: Path,
    environment: Mapping[str, str] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PersistentMcpRuntimeBundle: ...
```

The factory verifies inventory before reading `.mcp.json`, requires an absolute non-symlink state root, creates `state_root/mcp.sqlite`, and performs no capability enablement or route mutation.

- [x] **Step 4: Prove Gateway-to-real-stdio-to-persistence offline**

Using an enabled descriptor copy and fixture MCP server, execute one Gateway request, rebuild the store, load the evidence by returned ref and verify one linked audit event. This test must use the real subprocess transport and temporary SQLite but no network.

- [x] **Step 5: Run GREEN factory tests**

Run `tests.test_mcp_runtime_factory -v`; expected all pass.

---

### Task 4: Add Cross-Module Acceptance and Truthful Status

**Interfaces:**

- Consumes: all Task 1-3 components and current runtime metadata.
- Produces: Phase 1B runtime architecture gate without production route switch.

- [x] **Step 1: Write failing acceptance tests**

`tests/test_mcp_phase_1b_runtime_acceptance.py` must assert:

- concrete stdio transport exists outside the Phase 1A default module;
- only frozen plugin sources can create launch configs;
- persistent store is separate from `database.DB_PATH` and append-only;
- Gateway still has one transport call and no retry loop;
- `workitem.read/yunxiao` remains disabled with reason `phase_1b_gateway_transport_pending`;
- role route remains `provider/compatibility`;
- GitLab/PostgreSQL remain disabled;
- no MCP write tool, generic proxy, shell, raw SQL or database migration is introduced;
- architecture gate includes all new tests.

- [x] **Step 2: Register reviewed external-I/O finding**

Run the inventory scanner, then add only the exact `app/mcp_stdio_transport.py` `subprocess.Popen` finding and file SHA-256 to `config/external_io_boundaries.v1.json` with disposition `control_plane_internal`. The rationale must state that this is the hash-pinned MCP process boundary, not Provider direct I/O.

- [x] **Step 3: Extend architecture verification**

Append these modules to `scripts/verify.sh architecture` and freeze them in `tests/test_verify_entrypoint.py`:

```text
tests.test_mcp_stdio_transport
tests.test_mcp_persistence
tests.test_mcp_runtime_factory
tests.test_mcp_phase_1b_runtime_acceptance
```

- [x] **Step 4: Update docs without overstating readiness**

README/CHANGELOG must distinguish: concrete stdio transport implemented; persistent local MCP ledger implemented; Yunxiao plugin connectivity previously smoke-tested; Harness production route still disabled; installed tool catalog and end-to-end live Gateway remain separate validation; GitLab/PostgreSQL MCPs remain future increments.

- [x] **Step 5: Run focused and architecture gates**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_mcp_stdio_transport \
  tests.test_mcp_persistence \
  tests.test_mcp_runtime_factory \
  tests.test_mcp_phase_1b_runtime_acceptance -v
./scripts/verify.sh architecture
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/syntax_check.py
```

Expected: all new tests, architecture gate and syntax check pass; compatibility debt remains visible and no route is enabled.

---

## Final Verification and Handoff

- [x] Re-read this plan and map every requirement to a test or reviewed file.
- [x] Run plugin Yunxiao regression tests to prove the server contract remains compatible.
- [x] Run the existing focused Gateway/Runtime/PluginInventory tests.
- [x] Run `./scripts/verify.sh architecture` fresh and record exact counts.
- [x] Run syntax check fresh.
- [x] Attempt the global offline gate only as a separately bounded readiness snapshot; do not convert timeout or known unrelated blockers into a Phase 1B failure.
- [x] Report code-level completion, offline subprocess proof, persistence recovery proof, production route state, real network state, global release gate and remaining E1 work separately.

## Self-Review Result

- Spec coverage: transport, timeout/cancellation, process cleanup, frozen launch config, bounded output, persistent evidence, append-only audit, recovery, Gateway composition and truthful route status are each mapped to a task.
- Scope: one MCP control-plane increment; no GitLab/PostgreSQL implementation, Provider removal, main database migration, write capability, deployment or external mutation.
- Type consistency: existing `McpTransport`, `McpEvidenceSink`, `McpAuditSink`, `McpGateway`, `McpCapabilityRuntime` contracts remain authoritative.
- Data safety: new state is isolated in explicit `mcp.sqlite`; existing databases and user data are never reset or overwritten.
- Token control: Gateway continues returning compact `CapabilityResult` plus `evidence_ref`; full envelope is recovered only on demand from Evidence Store.
- Placeholder scan: no TBD, TODO, generic proxy, raw SQL execution or unspecified error-handling step remains.

## Execution Record

- Phase 1B focused suite: 42 tests passed, including real fixture subprocess, bounded timeout/cancellation cleanup, persistence restart recovery, append-only guards, tamper detection, runtime factory composition, and unchanged disabled routes.
- Persistence self-review added a RED/GREEN regression proving audit listing and integrity verification stream rows instead of using unbounded `fetchall`; full audit-chain validation remains intact while retained results are capped by the caller limit.
- Architecture gate: 139 tests passed; external-I/O policy reported 75 findings, 0 unclassified, 0 source drift, 0 forbidden, and 0 skill-contract errors. Existing compatibility debt remains visible.
- Syntax gate: 385 files passed AST parsing.
- Existing PluginInventory/Gateway/Runtime/verify focused regression: 32 tests passed. Yunxiao plugin offline regression: 59 tests passed.
- Frozen real plugin-layout factory smoke passed without launching a production process: 3 descriptors loaded, only registry-declared `yunxiao` was routable, store integrity passed, and the Yunxiao descriptor remained disabled.
- Global offline enterprise gate was attempted for 60 seconds and safely interrupted while its captured `unit` subprocess was still running. The checkpoint remains incomplete (`status=running`, zero completed iterations); therefore this increment is not a release-readiness claim.
- No network write, Yunxiao/GitLab/database mutation, production route switch, main database migration, Git push, deployment, or data reset was performed.
