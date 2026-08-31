# Enterprise Harness Phase 1B Yunxiao MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `workitem.read / yunxiao` 从 Skill 内的 Provider 直连迁移为真实、只读、可审计的 Yunxiao MCP Server，并以影子等价性证明新旧读取结果一致；本增量不开放任何云效写能力。

**Architecture:** `yunxiao-workitem-read` Skill 只保留触发条件、输入、调用顺序、停止条件、证据和 Token 策略；认证、OpenAPI GET、规范化、分页、脱敏和 MCP JSON-RPC 全部归入 `yunxiao` 插件的 MCP Server。原 `workitem.read` Provider 入口在影子期继续存在，但复用同一只读核心；Harness 仅在插件 MCP 声明、离线契约、负向安全、影子等价和只读运行时 smoke 都通过后，才把 Skill 元数据标为 MCP-backed，Python Gateway 生产路由仍保持 compatibility，直到后续 Stdio transport 与持久证据仓完成。

**Tech Stack:** Python 3.11+ 标准库、JSON-RPC 2.0/MCP stdio、现有 `unittest`、现有 `requirement-evidence.v2`、Harness `McpResultEnvelope` v1、Codex 本地插件 `.mcp.json`。

## Global Constraints

- Skill 是说明书：`skills/yunxiao-workitem-read/` 最终只保留 `SKILL.md` 与 `agents/openai.yaml`，不得保留 Python、HTTP 客户端、凭证解析或可执行脚本。
- MCP Server 是唯一新增真实连接：只允许 `https://openapi-rdc.aliyuncs.com`、`GET` 和 `yunxiao_read`；不得读取 `aliyun_devops_write_pat`。
- 本增量只有 `workitem_get` 一个正式工具；禁止万能 `request(method, path, body)` 工具。
- `workitem.write`、评论、状态流转、负责人、迭代和上传保持 disabled；不修改云效数据。
- 旧 Provider 入口只用于影子回放和安全回退，必须与 MCP 复用同一只读核心，不得复制第二套 HTTP 逻辑。
- 不修改 Harness SQLite schema，不删除运行记录、缓存、凭证、插件安装数据或用户文件。
- 插件源和 Harness 均不是 Git 仓库；以预修改备份、文件哈希、逐文件审计和测试证据替代 commit 步骤。
- 真实 smoke 只读取一个已知工作项；命令行、日志和结果不得出现 token 或组织 ID 原文。

---

## File Map

### Yunxiao plugin source

- Create: `/Users/lym/plugins/yunxiao/.mcp.json` — Codex MCP server declaration.
- Create: `/Users/lym/plugins/yunxiao/scripts/yunxiao_mcp_server.py` — dependency-free stdio MCP server and envelope builder.
- Move: `/Users/lym/plugins/yunxiao/skills/yunxiao-workitem-read/scripts/yunxiao_evidence.py` -> `/Users/lym/plugins/yunxiao/scripts/yunxiao_evidence.py` — canonical read-only Provider core.
- Move: `/Users/lym/plugins/yunxiao/skills/yunxiao-workitem-read/references/requirement-evidence.v2.schema.json` -> `/Users/lym/plugins/yunxiao/schemas/requirement-evidence.v2.schema.json` — Provider/MCP output schema outside Skill.
- Move: `/Users/lym/plugins/yunxiao/skills/yunxiao-workitem-read/tests/*.py` -> `/Users/lym/plugins/yunxiao/tests/` — plugin tests outside Skill.
- Modify: `/Users/lym/plugins/yunxiao/.codex-plugin/plugin.json` — declare `mcpServers`.
- Modify: `/Users/lym/plugins/yunxiao/capabilities.json` — point compatibility capability dependencies at canonical plugin scripts/schema.
- Modify: `/Users/lym/plugins/yunxiao/scripts/workitem_read.py` — import the canonical read core from `scripts/`.
- Modify: `/Users/lym/plugins/yunxiao/skills/yunxiao-workitem-read/SKILL.md` — capability-only MCP instructions and bounded evidence/token contract.
- Create: `/Users/lym/plugins/yunxiao/tests/test_yunxiao_mcp_server.py` — protocol, read-only, envelope, pagination and error tests.
- Create: `/Users/lym/plugins/yunxiao/tests/test_yunxiao_mcp_shadow.py` — old Provider vs MCP fixture equivalence.

### Harness control plane

- Modify: `config/mcp_capabilities.json` — update Yunxiao descriptor reason after shadow/live validation; keep `enabled=false` until Harness transport is configured.
- Modify: `config/role_capability_skill_matrix.json` — declare the Skill as `mcp_skill` with server `yunxiao`, while the existing Python execution route remains `provider/compatibility`.
- Modify: `config/external_io_boundaries.v1.json` — refresh only reviewed plugin source hashes/finding tuples caused by relocation.
- Modify: `tests/test_mcp_phase_1a_acceptance.py` — preserve Phase 1A no-route-switch assertions while allowing verified MCP-backed Skill metadata.
- Create: `tests/test_mcp_phase_1b_yunxiao_acceptance.py` — cross-project manifest/tool/Skill/boundary assertions.
- Modify: `scripts/verify.sh` and `tests/test_verify_entrypoint.py` — include the Phase 1B acceptance module in `architecture`.
- Modify: `README.md` and `CHANGELOG.md` — report code, offline, live and production-route status separately.

---

### Task 1: Establish the Plugin MCP and Skill Boundary Tests

**Interfaces:**

- Consumes: current plugin manifest, Skill, Provider entrypoint and `requirement-evidence.v2` fixtures.
- Produces: failing tests that require `.mcp.json`, exactly one `workitem_get` tool, read-only annotations, and a documentation-only Skill directory.

- [ ] **Step 1: Back up the exact plugin source before relocation**

Create a timestamped copy under `/Users/lym/WorkCode/ai/.his-harness-backups/` and record a SHA-256 inventory. Do not overwrite an existing backup.

- [ ] **Step 2: Add the failing plugin contract test**

`test_manifest_declares_yunxiao_mcp` must require:

```python
manifest["mcpServers"] == "./.mcp.json"
config == {
    "mcpServers": {
        "yunxiao": {
            "command": "python3",
            "args": ["./scripts/yunxiao_mcp_server.py"],
            "cwd": ".",
            "env_vars": [
                "HARNESS_CREDENTIALS_FILE",
                "ALIYUN_DEVOPS_PAT",
                "ALIYUN_DEVOPS_ORGANIZATION_ID",
            ],
        }
    }
}
```

`test_skill_tree_is_documentation_only` must reject `.py`, `.sh`, `.js`, executable bits, URLs, credential-file paths and HTTP/SQL client code anywhere below the Skill root.

- [ ] **Step 3: Add the failing MCP tool catalog test**

The expected tool is exactly:

```python
{
    "name": "workitem_get",
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
}
```

Its input must require `work_item_id`, `include_comments`, `include_attachments`, `page_cursor` and `page_size`, with no additional properties.

- [ ] **Step 4: Run RED tests**

Run:

```bash
/usr/bin/python3 -m unittest \
  tests.test_yunxiao_mcp_server \
  tests.test_skill_contract -v
```

Expected: FAIL because `.mcp.json` and `yunxiao_mcp_server.py` do not exist and executable files still live below the Skill directory.

### Task 2: Relocate the Read Core and Implement the MCP Server

**Interfaces:**

- Consumes: `load_credentials`, `YunxiaoClient`, `collect_evidence` from `scripts/yunxiao_evidence.py`.
- Produces: `YunxiaoMcpServer.handle(message)`, `YunxiaoMcpServer.call_tool(name, arguments, metadata)` and stdio `main()`.

- [ ] **Step 1: Move Provider code, schema and tests out of the Skill**

Move the exact files listed in File Map. Update the schema lookup to:

```python
Path(__file__).resolve().parents[1] / "schemas" / "requirement-evidence.v2.schema.json"
```

Update `scripts/workitem_read.py` to load `scripts/yunxiao_evidence.py`. Preserve the compatibility CLI input/output contract byte-for-byte except dependency paths.

- [ ] **Step 2: Add attachment opt-out to the shared read core**

Extend:

```python
def collect_evidence(
    *,
    source: str,
    client: Any,
    include_comments: bool = True,
    include_attachments: bool = True,
    ...,
) -> dict:
```

When false, do not call attachment or inline-file endpoints; return `attachments_status="skipped"`, empty `attachments`, and empty `inline_files`. The compatibility Provider omits the new argument and therefore retains the historical default.

- [ ] **Step 3: Implement the exact MCP server surface**

Use:

```python
class YunxiaoMcpServer:
    def __init__(
        self,
        *,
        credential_loader: Callable[..., Mapping[str, Any]] | None = None,
        client_factory: Callable[[Mapping[str, Any]], Any] | None = None,
        collector: Callable[..., Mapping[str, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None: ...

    def call_tool(
        self,
        name: str,
        arguments: object,
        metadata: object = None,
    ) -> dict[str, object]: ...

    def handle(self, message: object) -> dict[str, object] | None: ...

    def serve(self, source: TextIO, target: TextIO) -> None: ...
```

`tools/call.params._meta` may contain only `request_id` and `trace_id`; missing values are generated as bounded UUID hex identifiers. `_meta` never carries credentials or project context.

- [ ] **Step 4: Return the strict Harness MCP envelope**

Every `structuredContent` is `his-mcp-result-envelope.v1` with:

```python
{
    "capability": "workitem.read",
    "provider": "yunxiao",
    "trace": {
        "mcp_server": "yunxiao",
        "tool": "workitem_get",
        "server_version": SERVER_VERSION,
        "trace_id": trace_id,
    },
}
```

Successful primary-item retrieval returns `status="success"`, including a partial optional-evidence decision gate in `data`. Credential absence maps to `unavailable`; invalid arguments/cursor map to `invalid`; provider primary-read failure maps to `failed`. No exception text, token, organization ID, local path or raw authorization header is returned.

- [ ] **Step 5: Add bounded pagination and output safety**

Accept only `page_cursor=""` or `page_cursor="v1:<non-negative integer>"`. Apply the same offset/page window to each work item's comments, attachments and inline files. Set `pagination.truncated` and `next_cursor` consistently. Reject a canonical envelope larger than 262144 bytes with `MCP_RESULT_TOO_LARGE` and a non-sensitive recovery message.

- [ ] **Step 6: Run GREEN protocol tests**

Run the Task 1 command. Expected: PASS, with no network access because clients are injected fixtures.

### Task 3: Prove Security and Shadow Equivalence

**Interfaces:**

- Consumes: compatibility `execute_request`, shared read core and MCP `call_tool`.
- Produces: deterministic parity result for the same fake GET transport.

- [ ] **Step 1: Add negative tests before implementation adjustments**

Cover unknown tools, write-shaped names, extra arguments, non-empty unsupported cursor forms, oversized results, credential-loader failure, secret-shaped data, HTTP method capture and stdio parse errors. Assert all observed Provider methods are exactly `GET`.

- [ ] **Step 2: Run RED security tests**

Run:

```bash
/usr/bin/python3 -m unittest tests.test_yunxiao_mcp_server -v
```

Expected: new cases fail on the missing guard or sanitizer being tested.

- [ ] **Step 3: Implement only the missing guards**

Do not add retries. Do not introduce write clients. Keep result text metadata-only; large evidence stays in `structuredContent`.

- [ ] **Step 4: Add and run shadow equivalence**

For identical fixture credentials and fake GET responses, assert:

```python
mcp["data"] == provider["data"]
mcp["source"]["object_id"] == provider["data"]["source"]["resolved_work_item_id"]
mcp["status"] == "success"
provider["status"] in {"success", "partial"}
```

Also compare decision gate, warnings/error codes, relation graph, comments and attachment metadata.

Run:

```bash
/usr/bin/python3 -m unittest \
  tests.test_capability_entrypoint \
  tests.test_yunxiao_evidence \
  tests.test_yunxiao_mcp_server \
  tests.test_yunxiao_mcp_shadow \
  tests.test_skill_contract -v
```

Expected: all pass.

### Task 4: Integrate Truthful Harness Metadata

**Interfaces:**

- Consumes: plugin `.mcp.json`, tool catalog, Harness MCP descriptor and role/capability matrix.
- Produces: a cross-project acceptance gate with no false production-route claim.

- [ ] **Step 1: Add the failing Phase 1B acceptance test**

Require:

- plugin manifest declares server `yunxiao`;
- server lists exactly `workitem_get` as read-only;
- Harness descriptor server/tool/schema hash match plugin tool;
- `yunxiao-workitem-read` is an `mcp_skill` with `mcp_server="yunxiao"`;
- current Python route remains `provider/compatibility` until Harness Stdio transport is implemented;
- `workitem.write` remains disabled and has no MCP write tool;
- no executable file remains under the read Skill;
- Harness schema version remains 72.

- [ ] **Step 2: Run RED acceptance**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_mcp_phase_1b_yunxiao_acceptance -v
```

Expected: FAIL until matrix and descriptor metadata are updated.

- [ ] **Step 3: Update only truthful metadata**

Change the Skill declaration to `mcp_skill` and declare server `yunxiao`. Keep the route `provider/compatibility`; keep the MCP descriptor disabled with a reason that names the remaining Harness transport boundary. Refresh schema hashes and the reviewed external-I/O policy only for actual changed files/findings.

- [ ] **Step 4: Extend the architecture gate**

Add the new acceptance module to `scripts/verify.sh architecture` and assert its presence in `tests/test_verify_entrypoint.py`.

- [ ] **Step 5: Run architecture and regression gates**

Run:

```bash
./scripts/verify.sh architecture
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_capability_service \
  tests.test_role_capability_skill_registry \
  tests.test_plugin_inventory \
  tests.test_plugin_migration_security \
  tests.test_plugin_legacy_equivalence -v
```

Expected: all pass; compatibility debt remains visible rather than being set to zero.

### Task 5: Validate, Reinstall and Run a Read-only Smoke

**Interfaces:**

- Consumes: complete plugin source and personal marketplace entry `yunxiao@personal`.
- Produces: plugin validation, fresh cachebuster install and read-only runtime evidence.

- [ ] **Step 1: Validate source and Skill**

Run:

```bash
python3 /Users/lym/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/lym/plugins/yunxiao
python3 /Users/lym/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/lym/plugins/yunxiao/skills/yunxiao-workitem-read
```

- [ ] **Step 2: Run the MCP stdio fixture smoke**

Send `initialize`, `tools/list` and fixture-backed `tools/call`; assert one JSON response per request, no stderr secrets and no mutation surface.

- [ ] **Step 3: Run the real read-only smoke**

Invoke `scripts/yunxiao_mcp_server.py` over stdio with a known work-item ID and `_meta` identifiers. The MCP server resolves only the read credential internally. Save only status, tool identity, source object ID, decision gate and hashes; do not save raw credentials or full cloud payload in the Harness repository.

If credentials/network are unavailable, keep live status `unverified` and do not change the MCP descriptor to enabled.

- [ ] **Step 4: Update and reinstall the local plugin**

Run the plugin-creator helpers, then reinstall:

```bash
python3 /Users/lym/.codex/skills/.system/plugin-creator/scripts/read_marketplace_name.py
python3 /Users/lym/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/lym/plugins/yunxiao
codex plugin add yunxiao@personal
```

Re-run plugin validation against source and the installed cache copy. A new Codex task is required before claiming the newly installed MCP tool is visible in the app tool catalog.

- [ ] **Step 5: Final verification and handoff**

Run fresh:

```bash
./scripts/verify.sh architecture
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/syntax_check.py
```

Report separately: code complete, plugin tests, shadow equivalence, real MCP connectivity, installed-tool visibility, Harness Gateway route status, remaining compatibility debt and next Phase 1B increment.

---

## Self-Review Result

- Spec coverage: covers Skill/MCP separation, exact read-only tool, strict envelope, schema/permission/timeout-size/pagination/security tests, shadow equivalence and staged migration.
- Explicit non-goals: no Yunxiao write, no SQLite migration, no Provider deletion, no false Harness production-route switch.
- Type consistency: plugin tool is `workitem_get`; Harness capability/provider remain `workitem.read/yunxiao`; result is `his-mcp-result-envelope.v1`.
- Recovery: the existing Provider remains available during shadow mode; plugin source backup and hashes precede relocation.
- Token control: MCP text output is metadata-only; bounded structured evidence and pagination prevent unbounded prompt injection.
