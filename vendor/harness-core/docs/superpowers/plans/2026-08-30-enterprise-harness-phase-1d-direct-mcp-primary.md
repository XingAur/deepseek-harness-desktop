# Enterprise Harness Phase 1D Direct MCP Primary Route Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Activate Yunxiao, GitLab, and PostgreSQL read capabilities as executable MCP-primary routes while keeping Skills as manuals, credentials inside connector configuration, database mutation unavailable, and legacy Provider adapters available only through an explicit rollback mode.

**Architecture:** The Harness owns capability selection, target and parameter validation, budgets, audit, evidence, and fail-closed routing. Frozen plugin manifests pin each executable MCP server by version, entrypoint, and SHA-256; they do not disable execution. MCP servers resolve their own narrowly named credential handles and perform bounded external reads. The Manager reaches external reads only through a lazy MCP adapter and never silently falls back to a Provider after an MCP error.

**Tech Stack:** Python 3 standard library, JSON-RPC MCP over stdio, SQLite audit/evidence store, JSON Schema, `unittest`, optional PostgreSQL DB-API driver for live readonly connections.

**Delivery constraint:** `/Users/lym/WorkCode/ai/Harness` is not a Git repository. This plan therefore uses exact file backups, diff review, hashes, and tests instead of branch or commit steps.

---

### Task 1: Lock the activation contract with failing tests

**Files:**
- Add: `tests/test_mcp_phase_1d_primary_activation.py`
- Add: `tests/test_mcp_primary_provider_adapter.py`
- Add: `tests/test_gitlab_mcp_server.py`
- Add: `tests/test_postgresql_mcp_server.py`
- Modify: `/Users/lym/plugins/his-engineering/tests/test_skill_contracts.py`

- [ ] Assert `workitem.read`, `gitlab.read`, and `database.inspect` are enabled, native MCP routes.
- [ ] Assert the default Manager registry constructs MCP adapters for those reads and requires an explicit `provider_rollback` mode for legacy direct adapters.
- [ ] Assert MCP failure is returned as failure and never invokes a legacy adapter.
- [ ] Assert GitLab and PostgreSQL MCP tool arguments contain no token, password, DSN, or connection string.
- [ ] Assert database MCP exposes bounded readonly metadata/relationship operations and rejects mutation.
- [ ] Assert GitLab and database Skills state that they are manuals, MCP is the connector, read needs no Harness human confirmation, and no provider/browser/direct-client fallback exists.
- [ ] Run the new tests and preserve the expected RED failures before implementation.

### Task 2: Implement frozen GitLab and PostgreSQL MCP servers

**Files:**
- Add: `/Users/lym/plugins/his-engineering/.mcp.json`
- Add: `/Users/lym/plugins/his-engineering/scripts/gitlab_mcp_server.py`
- Add: `/Users/lym/plugins/his-engineering/scripts/postgresql_mcp_server.py`
- Modify: `config/schemas/mcp_tools/gitlab_read.v1.json`
- Modify: `config/schemas/mcp_tools/postgresql_inspect.v1.json`

- [ ] Implement dependency-free JSON-RPC stdio lifecycle and tool dispatch.
- [ ] Resolve only approved credential key names from `HARNESS_CREDENTIALS_FILE` or narrowly allowlisted environment variables; never accept credential values in model-visible arguments.
- [ ] Map GitLab operations to bounded GET-only API calls with redirect disabled, response size limits, pagination metadata, timeout, and sensitive-path checks.
- [ ] Map PostgreSQL operations to allowlisted catalog queries for schemas, tables, columns, constraints, indexes, and foreign keys.
- [ ] Enforce readonly transaction, one statement, row/time/schema limits, and reject SQL mutation or arbitrary multi-statement execution.
- [ ] Return the strict Harness MCP result envelope with trace, freshness, truncation, redaction, and stable source identity.

### Task 3: Switch Manager external reads to MCP primary

**Files:**
- Add: `app/providers/mcp_readonly.py`
- Modify: `app/providers/registry.py`
- Modify: `app/mcp_capability_runtime.py`
- Modify: `app/mcp_runtime_factory.py`
- Modify: `app/provider_capability_status.py`

- [ ] Add a lazy adapter that translates canonical Manager read actions to `CapabilityRequest` and executes them through `McpCapabilityRuntime`.
- [ ] Ensure the adapter never asks Harness to resolve or pass Yunxiao, GitLab, or database credential values.
- [ ] Block remote write actions in MCP mode with a stable unavailable code; retain existing write governance without enabling a write connector.
- [ ] Make MCP the default for Yunxiao, GitLab, and database registry entries.
- [ ] Permit legacy direct Provider adapters only when the caller explicitly selects `compatibility_mode="provider_rollback"`; never choose this mode from environment state or after an MCP error.
- [ ] Update capability status to report executable native MCP routes.

### Task 4: Activate and freeze the plugin inventory

**Files:**
- Modify: `config/mcp_capabilities.json`
- Modify: `config/role_capability_skill_matrix.json`
- Modify: `config/plugin_inventory.json`
- Modify: `config/external_io_boundary.json`
- Modify: `/Users/lym/plugins/yunxiao/.mcp.json`
- Modify: `/Users/lym/plugins/his-engineering/.mcp.json`

- [ ] Set all three read descriptors to `enabled=true` with no disabled reason.
- [ ] Set route ownership to `execution_kind=mcp` and `migration_state=native`.
- [ ] Pin every server command, relative entrypoint, environment allowlist, plugin version, source list, and SHA-256.
- [ ] Update external-I/O boundary hashes so only reviewed connector entrypoints may access network or database APIs.
- [ ] Verify an enabled descriptor without a frozen server configuration fails startup.

### Task 5: Rewrite Skills as connector manuals

**Files:**
- Modify: `/Users/lym/plugins/yunxiao/skills/yunxiao-workitem-read/SKILL.md`
- Modify: `/Users/lym/plugins/his-engineering/skills/his-gitlab/SKILL.md`
- Modify: `/Users/lym/plugins/his-engineering/skills/his-database-read/SKILL.md`
- Modify: corresponding `agents/openai.yaml` only if the user-facing description changes.

- [ ] State explicitly that Skill selects and explains a semantic capability but never opens a network or database connection.
- [ ] State explicitly that MCP performs the external read and credential resolution.
- [ ] State that readonly operations do not require Harness human confirmation; target/scope/budget/audit policy still applies.
- [ ] State that database write, delete, DDL, migration, and privilege changes are unavailable unless a future separately designed connector and exact current-user authorization are both present.
- [ ] Remove instructions that describe direct Provider transport, browser scraping, ambient credentials, or ad-hoc direct clients as normal routes.
- [ ] Refresh canonical Skill hashes only after semantic tests pass.

### Task 6: Verify the direct route and enterprise safety gates

**Files:**
- Modify: `docs/superpowers/specs/2026-08-30-enterprise-harness-control-plane-mcp-design.md`
- Modify: `README.md` or phase acceptance documentation only where activation status is stated.
- Add or update: Harness History stage evidence for task `LOCAL-20260830`, run `20260830-160001`.

- [ ] Run server unit tests with fake transports/executors and prove credentials never appear in arguments, results, audit, or errors.
- [ ] Run Manager adapter tests and prove MCP errors do not trigger Provider fallback.
- [ ] Run MCP registry, runtime, transport, gateway, persistence, schema, provider execution, and Skill contract suites.
- [ ] Run architecture and external-I/O integrity checks.
- [ ] If a configured readonly target is available, perform one bounded metadata-only live read; otherwise report the missing profile/driver/network as an explicit validation boundary.
- [ ] Review all changed files, hashes, and backup locations; report completed scope, unresolved enterprise phases, and residual risk without claiming full E4 completion.
