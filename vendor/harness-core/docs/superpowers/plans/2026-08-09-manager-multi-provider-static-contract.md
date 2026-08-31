# Manager Multi-Provider Static Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Harness Manager 只读展示云效、Git、GitLab、数据库和知识库五类 Provider 的正式 capability contract，同时明确所有真实执行、凭证读取、连接和写操作仍未开放。

**Architecture:** 扩展现有 `app/provider_capability_status.py` 的静态 manifest 归一化能力；每个 profile 只匹配固定的 canonical plugin、Skill 与 capability 名称，逐项返回 contract 状态和执行阻断状态。`app/server.py` 只传入固定的已安装 manifest 映射并渲染一个脱敏摘要表，绝不导入 Provider entrypoint 或执行它。

**Tech Stack:** Python 标准库、`unittest`、现有 Provider Profile/Manager 页面、已安装的 `/Users/lym/plugins/*/capabilities.json`。

## Global Constraints

- Harness 只读取静态 JSON manifest；不得执行 Git、GitLab、云效、数据库或知识库能力。
- 不读取凭证、不读取用户仓库路径、不访问网络、不连接数据库、不执行 `subprocess`、不动态导入 plugin entrypoint。
- `workitem.write`、`git.push`、`gitlab.write`、`database.change` 必须保持 contract `disabled`，不得生成可执行写请求。
- `workitem.read`、`gitlab.read`、`database.inspect`、知识检索/回答即使 contract `enabled`，Manager 也只显示为 executor 未登记的 `blocked`，不声称连接成功。
- 必须保留 Git bridge 的现有顶层兼容字段：`provider_plugin`、`skill`、`inspect_capability`、`status`、`execution_status`、`execution_reason`。
- 所有输出必须保持 `changed=false`、`credentials_read=false`、`external_calls=false`、`write_performed=false`，并且不含 manifest/仓库/凭证路径或 secret。
- `model` profile 没有正式 canonical Provider contract，必须明确显示 `canonical_provider_contract_unregistered`，不能伪造模型能力。
- 不 stage、commit、push、安装插件或修改 `/Users/lym/plugins`；完成后仅在无冲突时把已验证的最小 diff 同步到 `/Users/lym/WorkCode/ai/Harness`。

---

### Task 1: Static Multi-Provider Contract Normalizer

**Files:**
- Modify: `app/provider_capability_status.py`
- Modify: `tests/test_provider_capability_status.py`

**Interfaces:**
- Consumes: `build_provider_profile_status(profiles)` and optional `manifest_paths: Mapping[str, str]` keyed by canonical plugin name.
- Produces: `build_provider_capability_status(profiles, manifest_path=None, *, manifest_paths=None) -> dict[str, Any]`.
- Each supported item adds `capabilities: list[dict[str, str | bool]]`; existing Git top-level compatibility fields remain unchanged.

- [x] **Step 1: Write failing contract tests**

```python
def write_manifests(self, payloads: dict[str, dict[str, object]]) -> dict[str, str]:
    directory = Path(self.temp_dir.name)
    paths = {}
    for plugin, payload in payloads.items():
        manifest_path = directory / plugin / "capabilities.json"
        manifest_path.parent.mkdir()
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        paths[plugin] = str(manifest_path)
    return paths

def test_supported_provider_profiles_report_declared_read_and_write_boundaries(self) -> None:
    manifests = self.write_manifests({
        "yunxiao": self.manifest("yunxiao", [
            {"name": "workitem.read", "enabled": True},
            {"name": "workitem.write", "enabled": False},
        ]),
        "his-engineering": self.manifest("his-engineering", [
            {"name": "git.inspect", "enabled": True},
            {"name": "git.push", "enabled": False},
            {"name": "gitlab.read", "enabled": True},
            {"name": "gitlab.write", "enabled": False},
            {"name": "database.inspect", "enabled": True},
            {"name": "database.change", "enabled": False},
        ]),
        "his-knowledge": self.manifest("his-knowledge", [
            {"name": "knowledge.retrieve", "enabled": True},
            {"name": "knowledge.answer", "enabled": True},
        ]),
    })
    result = build_provider_capability_status(self.provider_profiles(), manifest_paths=manifests)
    by_provider = {item["provider"]: item for item in result["items"]}
    self.assertEqual("enabled", by_provider["yunxiao"]["capabilities"][0]["contract_status"])
    self.assertEqual("disabled", by_provider["yunxiao"]["capabilities"][1]["contract_status"])
    self.assertEqual("blocked", by_provider["database"]["execution_status"])
    self.assertEqual("canonical_provider_contract_unregistered", by_provider["model"]["reason"])
```

Add a redaction test that puts sentinel repository and credential-looking strings in profile connection values and asserts they do not occur in the result. Add malformed `yunxiao` and missing `his-knowledge` manifest cases; both must fail closed without exposing a manifest path.

- [x] **Step 2: Run focused tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_provider_capability_status -v`

Expected: failures because `manifest_paths`, provider capability lists and unregistered model status do not exist.

- [x] **Step 3: Implement the fixed bridge registry and static parser**

```python
_PROVIDER_BRIDGES = {
    "yunxiao": {"plugin": "yunxiao", "primary": "workitem.read", "skills": {
        "workitem.read": "yunxiao-workitem-read", "workitem.write": "yunxiao-workitem-write"}},
    "git": {"plugin": "his-engineering", "primary": "git.inspect", "skills": {
        "git.inspect": "his-git-local", "git.apply-local": "his-git-local",
        "git.commit-local": "his-git-delivery", "git.push": "his-git-delivery"}},
    "gitlab": {"plugin": "his-engineering", "primary": "gitlab.read", "skills": {
        "gitlab.read": "his-gitlab", "gitlab.write": "his-gitlab"}},
    "database": {"plugin": "his-engineering", "primary": "database.inspect", "skills": {
        "database.inspect": "his-database-read", "database.change-plan": "his-database-change", "database.change": "his-database-change"}},
    "knowledge": {"plugin": "his-knowledge", "primary": "knowledge.retrieve", "skills": {
        "knowledge.retrieve": "his-knowledge-retrieve", "knowledge.answer": "his-knowledge-answer",
        "knowledge.candidate.create": "his-knowledge-maintain", "knowledge.candidate.review": "his-knowledge-maintain",
        "knowledge.item.promote": "his-knowledge-maintain"}},
}
```

Parse each allowlisted manifest only when its declared `plugin` matches the bridge. For every normalized profile, return its safe `provider` and `profile_key`; for every capability declared in the fixed bridge, return `name`, `skill`, `contract_status` (`enabled`/`disabled`/`missing`/`unavailable`/`malformed`), `execution_status="blocked"`, a fixed executor-unregistered reason for enabled read/local capabilities, and the manifest disabled reason category for disabled write capabilities. Do not return `entrypoint`, `dependencies`, `scopes`, `credential_class`, a path, or raw profile connection values.

For existing Git callers, derive the legacy fields from `git.inspect` exactly as before. For model, return an empty capability list and the unregistered-contract reasons without touching a manifest.

- [x] **Step 4: Run Task 1 verification**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_provider_capability_status tests.test_provider_profiles -v`

Expected: all tests pass; no filesystem access except static temporary/manifest files used by the tests.

---

### Task 2: Manager API and Readable Contract Summary

**Files:**
- Modify: `app/server.py`
- Modify: `tests/test_server_core_status_api.py`
- Modify: `tests/test_manager_readiness_card.py`

**Interfaces:**
- Consumes: Task 1 `build_provider_capability_status(..., manifest_paths=CANONICAL_PROVIDER_MANIFESTS)`.
- Produces: unchanged `GET /api/provider-profiles/capability-status` schema with all profile items, and a Manager HTML summary table with one row per declared capability.

- [x] **Step 1: Write failing API and UI tests**

```python
def test_provider_capability_api_covers_all_static_provider_contracts(self) -> None:
    payload = invoke_get("/api/provider-profiles/capability-status")
    by_provider = {item["provider"]: item for item in payload["items"]}
    self.assertIn("workitem.read", {capability["name"] for capability in by_provider["yunxiao"]["capabilities"]})
    self.assertIn("database.change", {capability["name"] for capability in by_provider["database"]["capabilities"]})
    self.assertEqual("disabled", next(item for item in by_provider["yunxiao"]["capabilities"] if item["name"] == "workitem.write")["contract_status"])
    self.assertFalse(payload["credentials_read"])
    self.assertFalse(payload["external_calls"])

def test_provider_profiles_page_renders_multi_provider_contract_summary_without_secrets(self) -> None:
    html = render_provider_profiles_page()
    self.assertIn("云效", html)
    self.assertIn("GitLab", html)
    self.assertIn("数据库", html)
    self.assertIn("知识库", html)
    self.assertIn("workitem.write", html)
    self.assertIn("database.change", html)
    self.assertIn("未登记", html)
```

Use sentinel profile repository/secret-like values in the API test and assert neither raw value is rendered. Assert disabled write capability is only an informational static status, with no POST/write route added.

- [x] **Step 2: Run focused tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_server_core_status_api tests.test_manager_readiness_card -v`

Expected: the current API only maps Git and the page has no all-provider capability table.

- [x] **Step 3: Implement trusted manifest mapping and HTML summary**

```python
CANONICAL_PROVIDER_MANIFESTS = {
    "yunxiao": "/Users/lym/plugins/yunxiao/capabilities.json",
    "his-engineering": "/Users/lym/plugins/his-engineering/capabilities.json",
    "his-knowledge": "/Users/lym/plugins/his-knowledge/capabilities.json",
}

def _render_provider_capability_rows(items: Sequence[Mapping[str, object]]) -> str:
    rows = []
    for item in items:
        capabilities = item.get("capabilities") or ()
        if not capabilities:
            rows.append(_render_unregistered_contract_row(item))
            continue
        for capability in capabilities:
            rows.append(_render_declared_capability_row(item, capability))
    return "".join(rows)
```

Pass only this fixed mapping to the status builder for both API and page rendering. Render provider label, capability name, canonical Skill, contract status, and execution status/reason. Escape every rendered field. For unregistered model render one explicit blocked row. Do not render paths, entrypoints, scopes, credential refs, raw connections, full capability JSON, or write action buttons.

- [x] **Step 4: Run Task 2 verification**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_provider_capability_status tests.test_server_core_status_api tests.test_manager_readiness_card -v`

Expected: all tests pass; API/page show static contracts only and all enabled capabilities still show executor blocked.

---

### Task 3: Current-State Documentation and End-to-End Boundary Check

**Files:**
- Modify: `README.md`
- Modify: `tests/test_provider_capability_status.py`

**Interfaces:**
- Documents only static discovery and Manager display; it must not alter any Provider execution behavior.

- [x] **Step 1: Write the failing documentation assertion**

```python
def test_readme_describes_static_multi_provider_manager_contract(self) -> None:
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for value in ("yunxiao", "GitLab", "数据库", "his-knowledge", "静态 capability contract"):
        self.assertIn(value, text)
    self.assertIn("不读取凭证", text)
    self.assertIn("不连接外部系统", text)
    self.assertIn("workitem.write", text)
    self.assertIn("database.change", text)
```

- [x] **Step 2: Run the assertion to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_provider_capability_status.ProviderCapabilityStatusTests.test_readme_describes_static_multi_provider_manager_contract -v`

Expected: failure because the current Manager documentation only names the Git bridge.

- [x] **Step 3: Add one concise current-boundary section**

Document that Manager statically discovers contracts for Yunxiao, Git/GitLab, database and knowledge; contract `enabled` is not execution authorization; all executors are unregistered in Manager; `workitem.write`, `git.push`, `gitlab.write` and `database.change` remain disabled. State that credential maintenance/real connection tests need a later explicit authorization phase.

- [x] **Step 4: Run complete scoped verification and formal-source sync check**

Run from the implementation worktree `Harness/`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_provider_capability_status tests.test_provider_readonly_smoke \
  tests.test_server_core_status_api tests.test_manager_readiness_card \
  tests.test_provider_profiles tests.test_provider_connection_tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  app/provider_capability_status.py app/provider_readonly_smoke.py app/server.py
```

Inspect the scoped diff for forbidden imports/calls (`subprocess`, `importlib`, `Popen`, `os.system`, Provider entrypoints) and verify all rendered payloads remain redacted. After independent review is clean, compare the scoped source/target files; synchronize only the reviewed diff to `/Users/lym/WorkCode/ai/Harness`, preserve unrelated target changes, and repeat the same focused verification there.

## Completion State

- [x] All five declared non-model Providers expose fixed, static canonical capability lists in Manager.
- [x] Model explicitly reports no canonical Provider contract rather than a fabricated capability.
- [x] Enabled contracts remain execution-blocked; write contracts remain disabled.
- [x] API and UI are redacted and read-only with no added connection or write route.
- [x] Scoped tests, compile check, independent task review, final review and formal-source synchronization have evidence.
