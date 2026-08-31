# Enterprise Harness Phase 0 + Phase 1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立企业级 Harness 的外部 I/O 治理基线与 MCP 控制面骨架，让云效、GitLab、数据库等外部能力具备可审计、可限权、可迁移的 MCP 契约，同时保持当前 Provider/插件路径兼容且不发生真实外部写入。

**Architecture:** Phase 0 先通过静态扫描、边界策略和基线锁定当前直接外部 I/O、Skill、Capability 与运行时之间的真实关系；Phase 1A 再引入严格的 MCP Capability Registry、统一结果信封、Gateway、证据/审计接口和现有 CapabilityRuntime 适配层。生产路由保持原状，所有首批 MCP 描述符默认禁用，后续 Phase 1B 才逐项迁移真实 Provider。

**Tech Stack:** Python 3.11+ 标准库、现有 `unittest` 测试基座、JSON Schema 文档、现有 `CapabilityRequest`/`CapabilityResult` 契约、Shell 验证入口。

## Global Constraints

- 本计划只实现已批准设计中的 Phase 0 与 Phase 1A，不迁移真实云效、GitLab、数据库 Provider。
- 不调用真实网络、云效、GitLab、GitHub、数据库或模型服务；所有 MCP 调用测试使用内存 Fake Transport。
- 不执行数据库迁移，不修改 `app/database.py` 的 schema version，不新增 SQLite 表。
- 不删除或绕过现有 `CapabilityRuntime`、Provider、插件脚本和兼容回退路径。
- 不启用任何 L2/L3 外部写能力；首批 MCP 清单仅包含 L0/L1 只读能力且生产配置全部 `enabled=false`。
- Skill 继续只表达使用说明、约束和能力声明；凭证解析、连接、重试、分页、审计与结果规范由 MCP 层承担。
- 本目录当前不是 Git 仓库；本计划不包含 commit、push、PR 或分支操作。Git 交付需另行授权。
- 所有实现均先写失败测试，再写最小实现，再执行目标测试；禁止为通过测试而放宽安全约束。
- 不把 token、密码、数据库 DSN、Authorization Header 或完整敏感 payload 写入配置、日志、证据或测试快照。
- 如果 `./scripts/verify.sh offline` 仍命中当前已知基线问题，必须保留原始失败证据；不能将 Phase 0/1A 专项通过描述为完整离线门禁通过。

---

## Deliverable Map

### New application modules

- `app/external_io_inventory.py`
- `app/external_io_policy.py`
- `app/mcp_contracts.py`
- `app/mcp_schema_validation.py`
- `app/mcp_capability_registry.py`
- `app/mcp_transport.py`
- `app/mcp_audit.py`
- `app/mcp_gateway.py`
- `app/mcp_capability_runtime.py`

### New tools and configuration

- `tools/external_io_inventory.py`
- `tools/mcp_capability_check.py`
- `config/external_io_boundaries.v1.json`
- `config/mcp_capabilities.json`
- `config/schemas/external_io_inventory.v1.json`
- `config/schemas/external_io_boundaries.v1.json`
- `config/schemas/mcp_capability_manifest.v1.json`
- `config/schemas/mcp_result_envelope.v1.json`
- `config/schemas/mcp_tools/yunxiao_workitem_read.v1.json`
- `config/schemas/mcp_tools/gitlab_read.v1.json`
- `config/schemas/mcp_tools/postgresql_inspect.v1.json`

### New tests

- `tests/test_external_io_inventory.py`
- `tests/test_external_io_policy.py`
- `tests/test_external_io_inventory_cli.py`
- `tests/test_mcp_contracts.py`
- `tests/test_mcp_schema_validation.py`
- `tests/test_mcp_capability_registry.py`
- `tests/test_mcp_capability_check_cli.py`
- `tests/test_mcp_gateway.py`
- `tests/test_mcp_capability_runtime.py`
- `tests/test_mcp_phase_1a_acceptance.py`

### Modified files

- `app/capability_service.py`
- `app/role_capability_skill_registry.py`
- `config/role_capability_skill_matrix.json`
- `scripts/verify.sh`
- `tests/test_capability_service.py`
- `tests/test_role_capability_skill_registry.py`
- `tests/test_verify_entrypoint.py`
- `README.md`
- `CHANGELOG.md`

---

## Task 1: Build a deterministic external-I/O inventory scanner

**Files:**

- Create: `app/external_io_inventory.py`
- Create: `config/schemas/external_io_inventory.v1.json`
- Create: `tests/test_external_io_inventory.py`

### Contract

The scanner must be read-only; its finding set, ordering and fingerprints must be deterministic. Report generation time is explicit metadata and is excluded from finding comparison. It uses Python AST for `.py`, conservative token scanners for `.sh`/`.bash`/`.zsh` and `.js`/`.mjs`/`.cjs`/`.ts`, and fenced-code extraction for canonical `SKILL.md` files. Unsupported executable source types under registered plugin entrypoints must be reported as inventory errors rather than silently skipped.

| Category | Recognized call/import families | Default intended boundary |
|---|---|---|
| `network` | Python HTTP/socket families; shell `curl`/`wget`/`nc`/`ssh`; JS `fetch`/`axios`/`http.request`/`https.request` | `mcp_required` or `compatibility_quarantine` |
| `database` | Python DB drivers; shell `psql`/`mysql`/`redis-cli`; JS `pg`/`mysql2` connection APIs | `mcp_required` or `compatibility_quarantine` |
| `process` | Python/JS child-process APIs and bounded shell commands such as `git`, build, test and lifecycle executables | `worker_allowed`, `control_plane_internal`, or `compatibility_quarantine` |
| `credential` | Keychain/secret-file access APIs and known credential resolver calls | `control_plane_internal` or `mcp_required` |

Each finding must include:

```python
@dataclass(frozen=True)
class ExternalIoFinding:
    root_id: str
    relative_path: str
    line: int
    category: str
    symbol: str
    occurrence: int
    file_sha256: str
    fingerprint: str
```

`fingerprint` is SHA-256 over canonical JSON containing `root_id`, `relative_path`, `category`, `symbol`, and `occurrence`. Line number and file hash are excluded from the fingerprint so harmless line movement does not create a new capability; file hash is checked independently by policy to force review when a direct-I/O file changes.

### Steps

- [ ] **Step 1: Write scanner unit tests first**

Cover all of these cases in `tests/test_external_io_inventory.py`:

```python
class ExternalIoInventoryTests(unittest.TestCase):
    def test_detects_network_database_and_process_calls(self):
        source = """
import subprocess
import urllib.request
import psycopg

urllib.request.urlopen("https://example.invalid")
psycopg.connect("postgresql://example.invalid/db")
subprocess.run(["git", "status"], check=False)
"""
        findings = scan_python_source(
            source,
            root_id="fixture",
            relative_path="sample.py",
            file_sha256="0" * 64,
        )
        self.assertEqual(
            [(item.category, item.symbol) for item in findings],
            [
                ("database", "psycopg.connect"),
                ("network", "urllib.request.urlopen"),
                ("process", "subprocess.run"),
            ],
        )

    def test_does_not_treat_url_parsing_as_network_io(self):
        source = "from urllib.parse import urlparse\nurlparse('https://example.invalid')\n"
        findings = scan_python_source(
            source,
            root_id="fixture",
            relative_path="parse_only.py",
            file_sha256="1" * 64,
        )
        self.assertEqual(findings, ())

    def test_fingerprint_is_stable_when_only_line_numbers_change(self):
        first = scan_python_source(
            "import subprocess\nsubprocess.run(['git', 'status'])\n",
            root_id="fixture",
            relative_path="worker.py",
            file_sha256="2" * 64,
        )[0]
        second = scan_python_source(
            "\n\nimport subprocess\nsubprocess.run(['git', 'status'])\n",
            root_id="fixture",
            relative_path="worker.py",
            file_sha256="3" * 64,
        )[0]
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.file_sha256, second.file_sha256)

    def test_ignores_tests_caches_virtualenv_and_generated_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in (
                "tests/test_network.py",
                ".venv/lib/site.py",
                "data/generated.py",
                "__pycache__/cached.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "import urllib.request\nurllib.request.urlopen('https://example.invalid')\n",
                    encoding="utf-8",
                )
            inventory = scan_roots((ScanRoot("fixture", root),))
            self.assertEqual(inventory.findings, ())

    def test_detects_shell_and_javascript_external_io(self):
        shell = scan_shell_source(
            "curl https://example.invalid\npsql service=readonly\ngit status\n",
            root_id="fixture",
            relative_path="check.sh",
            file_sha256="4" * 64,
        )
        javascript = scan_javascript_source(
            "await fetch(url); childProcess.spawn('git', ['status']);\n",
            root_id="fixture",
            relative_path="check.js",
            file_sha256="5" * 64,
        )
        self.assertEqual(
            [(item.category, item.symbol) for item in shell],
            [("database", "psql"), ("network", "curl"), ("process", "git")],
        )
        self.assertEqual(
            [(item.category, item.symbol) for item in javascript],
            [("network", "fetch"), ("process", "childProcess.spawn")],
        )

    def test_skill_fenced_connection_code_is_reported_separately(self):
        findings = scan_skill_markdown(
            """# Skill\n```python\nimport requests\nrequests.get(url)\n```\n""",
            root_id="fixture",
            relative_path="skills/example/SKILL.md",
            file_sha256="6" * 64,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "network")
```

- [ ] **Step 2: Run the new test and confirm the expected import failure**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_external_io_inventory -v
```

Expected: `ModuleNotFoundError: No module named 'app.external_io_inventory'`.

- [ ] **Step 3: Implement the scanner with strict, stable serialization**

`app/external_io_inventory.py` must define:

```python
@dataclass(frozen=True)
class ScanRoot:
    root_id: str
    path: Path

@dataclass(frozen=True)
class ExternalIoInventory:
    schema_version: str
    generated_at: str
    roots: tuple[dict[str, str], ...]
    findings: tuple[ExternalIoFinding, ...]

def scan_python_source(
    source: str,
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    """Return sorted AST-backed findings without executing source."""

def scan_shell_source(
    source: str,
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    """Tokenize bounded shell command positions without expanding variables."""

def scan_javascript_source(
    source: str,
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    """Scan supported JS/TS call tokens without executing or resolving imports."""

def scan_skill_markdown(
    source: str,
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    """Scan only executable fenced blocks in a canonical Skill document."""

def scan_roots(
    roots: Sequence[ScanRoot],
    *,
    generated_at: str | None = None,
) -> ExternalIoInventory:
    """Scan allowed source roots while excluding tests, caches, data, worktrees and venvs."""

def inventory_to_dict(inventory: ExternalIoInventory) -> dict[str, object]:
    """Return canonical JSON-ready data with stable finding ordering."""
```

Implementation requirements:

- Resolve import aliases, including `import urllib.request as request` and `from subprocess import run`.
- Ignore comments and string literals that are not executable arguments/calls in supported scanners.
- Treat shell variable expansion and dynamic command construction as `credential` or `process` review findings when the executable cannot be resolved statically.
- Scan only canonical `SKILL.md` fenced blocks tagged `python`, `py`, `bash`, `sh`, `zsh`, `javascript`, `js`, `typescript` or `ts`; prose is not executable evidence.
- Sort findings by `(root_id, relative_path, category, symbol, occurrence)`.
- Count repeated calls to the same symbol in source order.
- Never import or execute scanned source.
- Never follow symlinks outside the declared root.
- Exclude `.git`, `.venv`, `venv`, `tests`, `data`, `work`, `outputs`, `__pycache__`, and generated backup directories.
- Read plugin entrypoint/dependency file extensions from existing capability manifests; fail inventory validation when an executable extension is unsupported.
- Use the caller-supplied `generated_at` when present; otherwise use current UTC ISO-8601 ending in `Z`. Tests that compare complete reports must supply a fixed timestamp.

- [ ] **Step 4: Add the inventory JSON schema**

`config/schemas/external_io_inventory.v1.json` must require exactly:

```json
{
  "schema_version": "his-external-io-inventory.v1",
  "generated_at": "2026-08-30T00:00:00Z",
  "roots": [
    {"root_id": "harness", "path": "/absolute/read-only/path"}
  ],
  "findings": [
    {
      "root_id": "harness",
      "relative_path": "app/example.py",
      "line": 12,
      "category": "network",
      "symbol": "urllib.request.urlopen",
      "occurrence": 1,
      "file_sha256": "64-lowercase-hex-characters",
      "fingerprint": "64-lowercase-hex-characters"
    }
  ]
}
```

The schema must use `additionalProperties: false` at every object level and enumerate the four categories.

- [ ] **Step 5: Run the focused scanner tests**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_external_io_inventory -v
```

Expected: all tests pass with no network access and no files created under Harness.

---

## Task 2: Add boundary policy, Skill/runtime truth audit, and a no-new-direct-I/O gate

**Files:**

- Create: `app/external_io_policy.py`
- Create: `tools/external_io_inventory.py`
- Create: `config/external_io_boundaries.v1.json`
- Create: `config/schemas/external_io_boundaries.v1.json`
- Create: `tests/test_external_io_policy.py`
- Create: `tests/test_external_io_inventory_cli.py`
- Modify: `app/role_capability_skill_registry.py`
- Modify: `config/role_capability_skill_matrix.json`
- Modify: `tests/test_role_capability_skill_registry.py`
- Modify: `scripts/verify.sh`
- Modify: `tests/test_verify_entrypoint.py`

### Boundary vocabulary

The policy must use these exact dispositions:

```text
mcp_required
worker_allowed
control_plane_internal
compatibility_quarantine
forbidden
```

The policy gate passes only when:

- every current finding has an explicit disposition;
- every classified direct-I/O file has its reviewed SHA-256 recorded;
- no reviewed file has drifted;
- no finding is classified `forbidden`;
- every `mcp_skill` in the role/capability/skill matrix declares a non-empty MCP server;
- every external capability still using Provider is surfaced as compatibility debt rather than mislabeled as migrated.

Existing compatibility debt is visible and counted but does not fail the Phase 0 architecture gate. Any new or changed direct-I/O implementation fails until policy is explicitly reviewed.

The role/capability/Skill matrix must state three different facts explicitly:

- `execution_kind`: how the route executes today (`provider` or `internal` in the current production matrix);
- `required_boundary`: where the route belongs architecturally (`mcp_required`, `worker_allowed`, or `control_plane_internal`);
- `migration_state`: whether today's runtime already satisfies that boundary (`native` or `compatibility`).

This prevents a `mcp_skill` declaration from being mistaken for proof that the execution route already uses MCP.

### Steps

- [ ] **Step 1: Write policy tests before implementation**

`tests/test_external_io_policy.py` must prove:

```python
class ExternalIoPolicyTests(unittest.TestCase):
    def test_unclassified_finding_fails_closed(self):
        report = evaluate_inventory(inventory_with_one_network_call(), empty_policy())
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.unclassified_count, 1)

    def test_source_hash_drift_requires_review(self):
        policy = policy_for_fixture(file_sha256="a" * 64)
        inventory = inventory_with_one_network_call(file_sha256="b" * 64)
        report = evaluate_inventory(inventory, policy)
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.source_drift_count, 1)

    def test_known_compatibility_quarantine_is_visible_but_gate_can_pass(self):
        policy = policy_for_fixture(
            file_sha256="a" * 64,
            disposition="compatibility_quarantine",
        )
        inventory = inventory_with_one_network_call(file_sha256="a" * 64)
        report = evaluate_inventory(inventory, policy)
        self.assertEqual(report.status, "passed")
        self.assertEqual(report.compatibility_debt_count, 1)

    def test_forbidden_finding_fails_even_when_explicitly_classified(self):
        policy = policy_for_fixture(
            file_sha256="a" * 64,
            disposition="forbidden",
        )
        inventory = inventory_with_one_network_call(file_sha256="a" * 64)
        report = evaluate_inventory(inventory, policy)
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.forbidden_count, 1)
```

Also test the current Skill/runtime matrix audit:

- `knowledge.retrieve` and `knowledge.answer` are recognized as MCP-declared Skills.
- Yunxiao, GitLab and database read paths are reported as `compatibility_quarantine` until Phase 1B.
- local Git/source/build/test routes are reported as `worker_allowed`, not `mcp_required`.
- an `mcp_skill` without `mcp_server` fails closed.
- a Skill file containing direct network/DB connection code fails the documentation-only check; referenced scripts are classified separately.

Update `tests/test_role_capability_skill_registry.py` first to prove:

- matrix schema is `his-role-capability-skill-matrix.v2`;
- every capability route and binding declares `required_boundary` and `migration_state`;
- binding boundary fields exactly match its referenced capability route;
- `execution_kind="internal"` requires `control_plane_internal/native`;
- a local Git/source/build/test route requires `worker_allowed/native` even though today's compatibility executor is named `provider`;
- a cloud/database route still using `execution_kind="provider"` requires `mcp_required/compatibility`;
- an `mcp_skill` routed through Provider must be `mcp_required/compatibility`, never `native`;
- `migration_state="native"` for an MCP-required route is rejected unless `execution_kind="mcp"` and the declared MCP server matches;
- role routing propagates both fields into `RoleRoute`.

- [ ] **Step 2: Run tests and confirm they fail because the policy module does not exist**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_external_io_policy -v
```

Expected: import failure for `app.external_io_policy`.

- [ ] **Step 3: Implement strict policy loading and evaluation**

`app/external_io_policy.py` must define:

```python
@dataclass(frozen=True)
class BoundaryRule:
    root_id: str
    relative_path: str
    file_sha256: str
    findings: tuple[tuple[str, str, int], ...]
    disposition: str
    owner: str
    rationale: str

@dataclass(frozen=True)
class ExternalIoPolicy:
    schema_version: str
    roots: tuple[ScanRoot, ...]
    rules: tuple[BoundaryRule, ...]

@dataclass(frozen=True)
class ExternalIoPolicyReport:
    status: str
    finding_count: int
    unclassified_count: int
    source_drift_count: int
    forbidden_count: int
    compatibility_debt_count: int
    skill_contract_error_count: int
    details: tuple[dict[str, object], ...]

def load_external_io_policy(
    path: Path,
    *,
    harness_root: Path,
    capabilities_config_path: Path,
    plugin_inventory_path: Path,
) -> ExternalIoPolicy:
    """Resolve Harness/plugin references and reject unknown fields or unsafe paths."""

def evaluate_inventory(
    inventory: ExternalIoInventory,
    policy: ExternalIoPolicy,
    *,
    matrix_path: Path | None = None,
) -> ExternalIoPolicyReport:
    """Fail closed on new findings, source drift, forbidden I/O or invalid Skill contracts."""
```

The tuple stored in `BoundaryRule.findings` is `(category, symbol, occurrence)`. Policy loading must reject duplicate rules, duplicate findings, missing rationales, wildcard paths, glob patterns, parent traversal, unknown plugin references and unknown dispositions.

`config/schemas/external_io_boundaries.v1.json` must mirror the exact policy fields, use `additionalProperties: false` recursively, enumerate root sources and dispositions, and require non-empty owner/rationale plus 64-character lowercase SHA-256 values.

- [ ] **Step 4: Create the portable policy skeleton and policy schema**

`config/external_io_boundaries.v1.json` must use portable root references rather than hard-coded user paths:

```json
{
  "roots": [
    {"root_id": "harness", "source": "harness_root", "value": "."},
    {"root_id": "plugin:his-harness-core", "source": "capability_config", "value": "his-harness-core"},
    {"root_id": "plugin:yunxiao", "source": "capability_config", "value": "yunxiao"},
    {"root_id": "plugin:his-engineering", "source": "capability_config", "value": "his-engineering"},
    {"root_id": "plugin:his-knowledge", "source": "capability_config", "value": "his-knowledge"}
  ]
}
```

At runtime these references currently resolve to:

```text
harness -> /Users/lym/WorkCode/ai/Harness
plugin:his-harness-core -> installed source path recorded for his-harness-core
plugin:yunxiao -> installed source path recorded for yunxiao
plugin:his-engineering -> installed source path recorded for his-engineering
plugin:his-knowledge -> installed source path recorded for his-knowledge
```

The loader must resolve plugin roots from `config/capabilities.json`, read each root's `capabilities.json` to map the declared plugin name, and verify that name/version/source hashes against `config/plugin_inventory.json`. Resolved absolute paths appear only in the generated inventory report. A plugin root, identity, source hash or inventory hash change requires policy review.

The `scan` command may load a policy containing roots with an empty rule list so it can produce a candidate. The `validate` command must fail when current findings are not yet covered. This temporary empty-rule state is allowed only while completing this task and must not remain at final verification.

At minimum, review and explicitly classify all findings in these current direct-I/O files:

```text
app/model_provider_runtime.py
app/llm_client.py
app/yunxiao_read.py
app/yunxiao_transaction.py
app/providers/yunxiao.py
app/providers/gitlab.py
app/providers/github.py
app/database_probe.py
app/capability_runtime.py
plugin:yunxiao/skills/yunxiao-workitem-read/scripts/yunxiao_evidence.py
plugin:his-engineering/scripts/pg_evidence.py
```

Classification rules:

- model/network, Yunxiao, GitLab, GitHub and database direct I/O: `compatibility_quarantine`.
- `app/capability_runtime.py` process execution of plugin entrypoints: `compatibility_quarantine`.
- local worktree, local Git, source inspection, build and test subprocesses: `worker_allowed`.
- Harness lifecycle and internal governance subprocesses: `control_plane_internal`.
- any unexplained direct I/O: `forbidden` until reviewed.

- [ ] **Step 5: Make the role/capability/Skill matrix truthful without switching routes**

In `app/role_capability_skill_registry.py`:

- bump `MATRIX_SCHEMA_VERSION` to `his-role-capability-skill-matrix.v2`;
- add `required_boundary: str` and `migration_state: str` to `CapabilityRoute` and `RoleRoute`;
- allow `execution_kind="mcp"` in the schema for future migration, while keeping every Phase 1A production route on its current value;
- parse the two exact fields on every capability route and binding;
- enforce the invariants listed in Step 1;
- keep existing `execution_kind` values and runtime selection unchanged.

In `config/role_capability_skill_matrix.json`, classify routes as follows:

| Route family | required_boundary | migration_state |
|---|---|---|
| Yunxiao, GitLab, GitHub, `database.inspect` and `database.change` connectivity | `mcp_required` | `compatibility` |
| Knowledge routes currently declared as MCP Skill but executed by Provider | `mcp_required` | `compatibility` |
| Local Git inspect/diff/history/apply/commit, source read/search, build/test/review | `worker_allowed` | `native` |
| Harness governance, artifact, human-gate, learning, replay, repair and visual internals | `control_plane_internal` | `native` |

`requirement.govern / his-harness-core` and `database.change-plan / postgresql` are local governance capabilities and must be `control_plane_internal/native`. Git push is an external write boundary and must be `mcp_required/compatibility`; this classification does not enable or authorize it.

Keep Yunxiao, GitLab and database Skills as `codex_skill` during Phase 1A because their plugins do not yet expose a verified MCP server. Converting them to `mcp_skill` before Phase 1B would be false runtime metadata. Local Git/source/test Skills remain `codex_skill` permanently because they target Worker Sandbox rather than MCP.

- [ ] **Step 6: Implement a read-only CLI**

`tools/external_io_inventory.py` must support:

```bash
.venv/bin/python tools/external_io_inventory.py scan \
  --policy config/external_io_boundaries.v1.json \
  --output /private/tmp/harness-external-io-inventory.json

.venv/bin/python tools/external_io_inventory.py validate \
  --policy config/external_io_boundaries.v1.json \
  --matrix config/role_capability_skill_matrix.json \
  --format summary
```

CLI rules:

- `scan` writes only to the explicit output path.
- `validate` is read-only and returns exit code `0` only when the gate passes.
- summary output includes counts for total findings, MCP-required, worker, internal, compatibility debt, unclassified, source drift, forbidden, and Skill contract errors.
- no source line content, payload, environment variables or credentials are printed.
- unknown commands, paths outside allowed roots and missing config fail with exit code `2`.

- [ ] **Step 7: Generate the reviewed baseline and add CLI tests**

Run the new `scan` command against the portable roots, write the candidate only to `/private/tmp/harness-external-io-inventory.json`, compare every finding to the source and classifications above, then record exact finding tuples and current file hashes in `config/external_io_boundaries.v1.json`. Do not accept a broad directory wildcard, and do not copy generated timestamps or absolute host paths into policy rules.

`tests/test_external_io_inventory_cli.py` must execute the CLI in a temporary fixture tree and assert:

- passing policy returns `0`;
- a new `urllib.request.urlopen` call returns `1`;
- changed direct-I/O file hash returns `1`;
- malformed config returns `2`;
- output contains only summary metadata and no fixture secret value.

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_external_io_inventory \
  tests.test_external_io_policy \
  tests.test_external_io_inventory_cli \
  tests.test_role_capability_skill_registry -v
```

Expected: all tests pass.

- [ ] **Step 8: Add an architecture verification mode**

Extend `scripts/verify.sh` with an `architecture` mode that runs only:

```bash
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" tools/external_io_inventory.py validate \
  --policy config/external_io_boundaries.v1.json \
  --matrix config/role_capability_skill_matrix.json \
  --format summary

PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m unittest \
  tests.test_external_io_inventory \
  tests.test_external_io_policy \
  tests.test_external_io_inventory_cli \
  tests.test_role_capability_skill_registry -v
```

Update `tests/test_verify_entrypoint.py` to assert that `architecture` is accepted and that an unknown mode still fails.

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
./scripts/verify.sh architecture
```

Expected: exit `0`, with compatibility debt reported as non-zero and unclassified/source-drift/forbidden counts all `0`.

---

## Task 3: Define strict MCP capability and result contracts

**Files:**

- Create: `app/mcp_contracts.py`
- Create: `app/mcp_schema_validation.py`
- Create: `config/schemas/mcp_capability_manifest.v1.json`
- Create: `config/schemas/mcp_result_envelope.v1.json`
- Create: `tests/test_mcp_contracts.py`
- Create: `tests/test_mcp_schema_validation.py`

### Contract types

`app/mcp_contracts.py` must define immutable dataclasses for:

```python
@dataclass(frozen=True)
class McpSource:
    system: str
    object_id: str
    version: str
    observed_at: str

@dataclass(frozen=True)
class McpFreshness:
    status: str
    expires_at: str

@dataclass(frozen=True)
class McpPagination:
    truncated: bool
    next_cursor: str

@dataclass(frozen=True)
class McpRedaction:
    applied: bool
    fields: tuple[str, ...]

@dataclass(frozen=True)
class McpError:
    code: str
    retryable: bool
    recovery: str

@dataclass(frozen=True)
class McpTrace:
    mcp_server: str
    tool: str
    server_version: str
    trace_id: str

@dataclass(frozen=True)
class McpResultEnvelope:
    schema_version: str
    request_id: str
    capability: str
    provider: str
    status: str
    data: Mapping[str, Any]
    evidence_ref: str
    source: McpSource
    freshness: McpFreshness
    pagination: McpPagination
    redaction: McpRedaction
    error: McpError
    trace: McpTrace
```

### Steps

- [ ] **Step 1: Write strict parser tests**

`tests/test_mcp_contracts.py` must cover:

- a complete successful envelope parses;
- unknown top-level and nested fields are rejected;
- unsupported `schema_version` is rejected;
- request/capability/provider identity mismatch is rejected;
- success requires non-empty `evidence_ref`, source system and observed timestamp;
- failure requires non-empty `error.code` and `error.recovery`;
- `next_cursor` is allowed only when `truncated=true`;
- redaction fields are sorted and unique;
- secret-like keys such as `token`, `password`, `authorization`, `dsn` and `secret` are rejected recursively in `data` and trace metadata;
- secret/credential/PII-shaped scalar values are rejected recursively without echoing the rejected value;
- result size is measured using canonical UTF-8 JSON bytes.

`tests/test_mcp_schema_validation.py` must prove the supported schema subset validates nested objects and arrays, rejects unknown fields, enforces required fields, limits, patterns and enums, and rejects unsupported schema keywords rather than silently ignoring them.

- [ ] **Step 2: Run tests and confirm expected import failure**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_mcp_contracts -v
```

Expected: import failure for `app.mcp_contracts`.

- [ ] **Step 3: Implement strict parsing and validation**

Required public functions:

```python
def parse_mcp_result_envelope(
    payload: Mapping[str, Any],
    *,
    expected_request_id: str,
    expected_capability: str,
    expected_provider: str,
) -> McpResultEnvelope:
    """Parse exact fields, validate identity and reject secret-bearing payloads."""

def mcp_envelope_to_dict(envelope: McpResultEnvelope) -> dict[str, Any]:
    """Serialize to canonical JSON-ready data without mutating nested mappings."""

def canonical_json_size(payload: Mapping[str, Any]) -> int:
    """Return UTF-8 byte size using sorted compact JSON."""
```

Allowed result statuses are exactly `success`, `failed`, `denied`, `unavailable`, and `invalid`.

Freshness statuses are exactly `fresh`, `stale`, `unknown`, and `not_applicable`.

- [ ] **Step 4: Add strict JSON schemas**

`config/schemas/mcp_result_envelope.v1.json` must mirror the dataclass contract and use `additionalProperties: false` recursively.

`config/schemas/mcp_capability_manifest.v1.json` must define the registry document used in Task 4 and enforce:

- exact schema version `his-mcp-capabilities.v1`;
- unique `(capability, provider)` pairs at application validation time;
- mutation levels `L0` and `L1` only for this phase;
- bounded integer limits for timeout and result bytes;
- non-empty `server`, `tool`, schema paths and disabled reason when disabled;
- no credential values or environment-variable names.

Implement `app/mcp_schema_validation.py` instead of adding a new runtime dependency. It must expose:

```python
SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
    }
)

def check_supported_schema(schema: Mapping[str, Any], *, path: str = "$") -> None:
    """Reject unsupported keywords and malformed supported constraints."""

def validate_mcp_arguments(
    schema: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    """Validate the documented strict subset and raise McpSchemaValidationError."""
```

Do not claim full JSON Schema support. Registry loading must call `check_supported_schema`, and Gateway execution must call `validate_mcp_arguments` before transport invocation.

Reuse `app.sensitive_text.is_sensitive_mapping_key` and `app.sensitive_text.contains_sensitive_text` for recursive request/result safety checks. Do not use the opaque-scalar heuristic for validated commit hashes, object IDs or trace IDs; validate those fields with their bounded identifier contracts instead. Do not duplicate token/credential regexes and do not include rejected values in exception messages or audit records.

- [ ] **Step 5: Run focused contract tests**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_mcp_contracts -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_mcp_schema_validation -v
```

Expected: all tests pass.

---

## Task 4: Implement the MCP Capability Registry and read-only inspection CLI

**Files:**

- Create: `app/mcp_capability_registry.py`
- Create: `tools/mcp_capability_check.py`
- Create: `config/mcp_capabilities.json`
- Create: `config/schemas/mcp_tools/yunxiao_workitem_read.v1.json`
- Create: `config/schemas/mcp_tools/gitlab_read.v1.json`
- Create: `config/schemas/mcp_tools/postgresql_inspect.v1.json`
- Create: `tests/test_mcp_capability_registry.py`
- Create: `tests/test_mcp_capability_check_cli.py`

### Descriptor contract

```python
@dataclass(frozen=True)
class McpCapabilityDescriptor:
    capability: str
    provider: str
    server: str
    tool: str
    contract_version: str
    mutation_level: MutationLevel
    required_scopes: tuple[str, ...]
    timeout_seconds: int
    max_result_bytes: int
    input_schema_path: Path
    input_schema_sha256: str
    input_schema: Mapping[str, Any]
    result_schema_path: Path
    result_schema_sha256: str
    result_schema: Mapping[str, Any]
    enabled: bool
    disabled_reason: str
```

### Steps

- [ ] **Step 1: Write registry tests**

`tests/test_mcp_capability_registry.py` must cover:

- exact-field manifest parsing;
- duplicate `(capability, provider)` rejection;
- schema path traversal and symlink escape rejection;
- schema hash verification;
- loaded schemas are deeply immutable snapshots so later file mutation cannot change an active descriptor;
- wildcard server/tool names rejection;
- generic tools named `request`, `execute`, `proxy`, `raw_sql`, `shell`, or `command` rejection;
- L2/L3 mutation rejection in Phase 1A;
- enabled descriptors cannot have `disabled_reason`;
- disabled descriptors must have `disabled_reason`;
- timeout range `1..60` seconds;
- result limit range `1024..1048576` bytes;
- deterministic list ordering.

- [ ] **Step 2: Run tests and confirm expected import failure**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_mcp_capability_registry -v
```

Expected: import failure for `app.mcp_capability_registry`.

- [ ] **Step 3: Implement strict registry loading**

Required API:

```python
class McpCapabilityRegistry:
    @classmethod
    def from_file(cls, path: Path, *, harness_root: Path) -> "McpCapabilityRegistry":
        """Load strict manifest and verify all schema paths and hashes."""

    def resolve(self, capability: str, provider: str) -> McpCapabilityDescriptor:
        """Return an exact descriptor or raise McpCapabilityNotFound."""

    def list_capabilities(self) -> tuple[McpCapabilityDescriptor, ...]:
        """Return deterministic capability/provider ordering."""
```

No loader method may read environment variables, credentials or external paths outside the Harness root. Schema bytes must be read once, hashed, parsed, checked against the supported subset and deep-frozen into descriptor snapshots; Gateway execution must never reopen a schema file.

- [ ] **Step 4: Create bounded input schemas**

`yunxiao_workitem_read.v1.json` accepts only:

```json
{
  "work_item_id": "non-empty string",
  "include_comments": true,
  "include_attachments": true,
  "page_cursor": "string",
  "page_size": 50
}
```

`gitlab_read.v1.json` accepts only:

```json
{
  "project": "namespace/project",
  "operation": "project|repository_file|commit|merge_request",
  "ref": "branch-or-commit",
  "path": "repository-relative-path",
  "object_id": "string"
}
```

`postgresql_inspect.v1.json` accepts only:

```json
{
  "connection_alias": "configured-alias-not-a-dsn",
  "operation": "schemas|tables|columns|constraints|indexes|foreign_keys",
  "schema": "identifier",
  "table": "identifier"
}
```

All three schemas must use `additionalProperties: false`, bounded strings/arrays, and prohibit credential fields. The PostgreSQL schema is metadata-only: it contains no row sampling, filter values, raw SQL or statement field. The future PostgreSQL MCP server must independently enforce read-only transactions, identifier allowlists and catalog-only operations.

- [ ] **Step 5: Create the production Phase 1A manifest**

`config/mcp_capabilities.json` must register exactly these initial entries:

| Capability | Provider | MCP server | Tool | Level | Enabled |
|---|---|---|---|---|---|
| `workitem.read` | `yunxiao` | `yunxiao` | `workitem_get` | L1 | false |
| `gitlab.read` | `gitlab` | `gitlab` | `repository_read` | L1 | false |
| `database.inspect` | `postgresql` | `postgresql` | `readonly_inspect` | L1 | false |

Each disabled reason must be `phase_1b_transport_not_configured`. Use `timeout_seconds=30` and `max_result_bytes=262144` unless an existing stricter project limit is discovered during implementation.

Do not register write, SQL mutation, shell, credential, Git delivery or generic proxy capabilities.

- [ ] **Step 6: Implement a read-only registry CLI**

`tools/mcp_capability_check.py` must support:

```bash
.venv/bin/python tools/mcp_capability_check.py validate \
  --manifest config/mcp_capabilities.json

.venv/bin/python tools/mcp_capability_check.py list \
  --manifest config/mcp_capabilities.json \
  --format summary

.venv/bin/python tools/mcp_capability_check.py inspect \
  --manifest config/mcp_capabilities.json \
  --capability workitem.read \
  --provider yunxiao
```

The CLI must never call an MCP server. Output is metadata only and excludes schema bodies, environment variables and credentials.

- [ ] **Step 7: Run registry and CLI tests**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_mcp_capability_registry \
  tests.test_mcp_capability_check_cli -v

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/mcp_capability_check.py validate \
  --manifest config/mcp_capabilities.json
```

Expected: tests and validation pass; `list` shows three disabled read-only capabilities.

---

## Task 5: Implement a fail-closed MCP Gateway with evidence and audit interfaces

**Files:**

- Create: `app/mcp_transport.py`
- Create: `app/mcp_audit.py`
- Create: `app/mcp_gateway.py`
- Create: `tests/test_mcp_gateway.py`

### Interfaces

`app/mcp_transport.py`:

```python
class McpTransport(Protocol):
    def call(
        self,
        *,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        timeout_seconds: int,
        trace_id: str,
    ) -> Mapping[str, Any]:
        """Return one MCP result envelope as a mapping."""

class DisabledMcpTransport:
    def call(self, **kwargs: Any) -> Mapping[str, Any]:
        raise McpTransportUnavailable("MCP transport is not configured")
```

`app/mcp_audit.py`:

```python
class McpEvidenceSink(Protocol):
    def store(
        self,
        *,
        request_id: str,
        capability: str,
        provider: str,
        payload: Mapping[str, Any],
    ) -> str:
        """Return an opaque evidence reference."""

class McpAuditSink(Protocol):
    def record(self, event: Mapping[str, Any]) -> None:
        """Record metadata-only audit data."""

class InMemoryMcpEvidenceSink:
    """Test-only deterministic evidence sink."""

class InMemoryMcpAuditSink:
    """Test-only deterministic audit sink."""
```

No database-backed sink is implemented in this phase.

### Steps

- [ ] **Step 1: Write Gateway behavior tests first**

`tests/test_mcp_gateway.py` must use a Fake Transport and cover:

- disabled descriptor returns normalized `CapabilityResult.status="unsupported"` with error code `MCP_CAPABILITY_DISABLED`, without calling transport;
- missing capability returns normalized `CapabilityResult.status="unsupported"` with error code `MCP_CAPABILITY_NOT_FOUND`, without calling transport;
- missing required scope returns `denied`;
- L2/L3 request is denied before transport even if a fixture descriptor is malformed;
- transport is called exactly once for a valid enabled read request;
- request arguments never contain authorization token, credential file, DSN or raw environment;
- request arguments are rejected against the exact registered input schema before transport;
- oversized raw result is rejected before evidence storage;
- invalid envelope identity is rejected;
- secret-bearing result is rejected and not stored;
- success stores redacted evidence and returns its opaque reference;
- failure, denial, invalid response and success all create metadata-only audit events;
- transport exceptions are normalized to `unavailable` with a recovery hint;
- no retry occurs in Phase 1A.

Use request fixtures built from the existing `CapabilityRequest.from_dict` contract rather than inventing a second authorization model.

- [ ] **Step 2: Run tests and confirm expected import failure**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_mcp_gateway -v
```

Expected: import failures for the new MCP modules.

- [ ] **Step 3: Implement metadata-only in-memory sinks**

The in-memory evidence sink must deep-copy accepted payloads and return deterministic refs of the form:

```text
f"mcp-evidence:{request_id}:{digest[:16]}"
```

The audit sink must store only:

```text
request_id, capability, provider, mutation_level, status,
trace_id, server, tool, duration_ms, evidence_ref,
error_code, retryable, timestamp, task_id, run_id,
project_id, repository_id, context_pack_id
```

It must reject unknown audit keys and values containing secret-like field names.

- [ ] **Step 4: Implement `McpGateway.execute`**

`app/mcp_gateway.py` must expose:

```python
@dataclass(frozen=True)
class McpGatewayExecution:
    descriptor: McpCapabilityDescriptor | None
    result: CapabilityResult
    duration_ms: int

class McpGateway:
    def __init__(
        self,
        *,
        registry: McpCapabilityRegistry,
        transport: McpTransport,
        evidence_sink: McpEvidenceSink,
        audit_sink: McpAuditSink,
    ) -> None:
        self.registry = registry
        self.transport = transport
        self.evidence_sink = evidence_sink
        self.audit_sink = audit_sink

    def execute(self, request: CapabilityRequest) -> McpGatewayExecution:
        """Resolve, authorize, invoke once, validate, store evidence and audit."""
```

Implementation sequence is mandatory:

1. Resolve descriptor by exact capability/provider.
2. Deny anything above L1.
3. Check descriptor enabled state and request authorization/scopes.
4. Validate `request.input` against the registered supported schema subset.
5. Deep-copy only the validated `request.input` into transport arguments.
6. Invoke transport once with timeout and trace ID.
7. Enforce raw canonical JSON byte limit.
8. Parse strict envelope and verify identity.
9. Persist evidence through the sink only after validation/redaction.
10. Return an existing `CapabilityResult` with `changed=false`.
11. Record audit metadata in a `finally`-equivalent path for every outcome.

The incoming envelope's `evidence_ref` is the MCP source-system evidence reference. The sink returns a separate Harness-owned opaque evidence reference; `CapabilityResult.evidence` and Gateway audit metadata use the Harness-owned reference, while the validated source reference remains inside the stored evidence payload.

Map MCP envelope status to the existing `CapabilityResult` contract exactly as follows:

| MCP envelope status | CapabilityResult status | Required behavior |
|---|---|---|
| `success` | `success` | `changed=false`, return validated data and Harness evidence ref |
| `failed` | `failed` | preserve sanitized error code and recovery as blocker |
| `denied` | `blocked` | add authorization/scope blocker |
| `unavailable` | `unsupported` | allow existing read-only fallback policy to decide |
| `invalid` | `blocked` | use `MCP_RESULT_INVALID` and do not store evidence |

The Gateway must construct a valid existing `CapabilityResult`; it must not add new statuses to `app/capability_contracts.py`.

Do not pass `request.context` wholesale. Only allow these context keys into Harness audit metadata after scalar validation:

```text
task_id, run_id, project_id, repository_id, context_pack_id
```

Do not pass any context value to the MCP tool arguments or transport metadata in Phase 1A.

- [ ] **Step 5: Run Gateway tests**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_mcp_gateway -v
```

Expected: all tests pass; no network or filesystem evidence writes occur.

---

## Task 6: Add a compatibility adapter for the existing CapabilityService

**Files:**

- Create: `app/mcp_capability_runtime.py`
- Create: `tests/test_mcp_capability_runtime.py`
- Modify: `app/capability_service.py`
- Modify: `tests/test_capability_service.py`

### Purpose

The MCP layer must fit the existing CapabilityService contract without switching production routes. The adapter emulates the current `CapabilityRuntime.preflight` and `CapabilityRuntime.execute` methods, so Phase 1B can migrate one route at a time.

### Steps

- [ ] **Step 1: Add failing adapter tests**

`tests/test_mcp_capability_runtime.py` must cover:

- descriptor metadata is represented in an existing `CapabilityPreflight`;
- disabled Phase 1A descriptors are not executable;
- enabled fixture descriptor delegates to Gateway once;
- Gateway `CapabilityResult` is preserved exactly in `CapabilityExecution`;
- non-empty `environment` is rejected because credentials may not cross the adapter;
- `CapabilityService(mcp_runtime, routing_mode="enforce")` works with the MCP adapter in a fixture;
- existing Provider runtime tests remain unchanged and pass.

- [ ] **Step 2: Run tests and confirm expected import failure**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_mcp_capability_runtime -v
```

Expected: import failure for `app.mcp_capability_runtime`.

- [ ] **Step 3: Implement the adapter**

Required API:

```python
class McpCapabilityRuntime:
    def __init__(
        self,
        *,
        registry: McpCapabilityRegistry,
        gateway: McpGateway,
    ) -> None:
        self.registry = registry
        self.gateway = gateway

    def preflight(self, request: CapabilityRequest) -> CapabilityPreflight:
        """Return exact descriptor and permission metadata without transport I/O."""

    def execute(
        self,
        request: CapabilityRequest,
        *,
        timeout_seconds: int | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CapabilityExecution:
        """Reject environment injection, validate timeout policy and delegate once."""
```

The synthetic existing `CapabilityDescriptor` used by preflight must use:

```text
plugin = mcp:{server}
plugin_version = contract_version
provider = descriptor.provider
entrypoint = None
credential_class = none
mutation_level = descriptor.mutation_level
enabled = descriptor.enabled
```

`CapabilityDescriptor.entrypoint` is a filesystem `Path | None`; the adapter must keep it `None` rather than placing an MCP URI into a filesystem field. MCP server/tool identity remains in `McpCapabilityDescriptor` and audit metadata. The synthetic descriptor must not claim that the production route has migrated.

If `timeout_seconds` is supplied, it must equal the registry descriptor timeout; a caller cannot increase or decrease MCP policy through the compatibility API.

- [ ] **Step 4: Generalize only the CapabilityService type boundary**

In `app/capability_service.py`, replace the concrete constructor annotation with a local Protocol that requires only:

```python
class CapabilityRuntimeLike(Protocol):
    def preflight(self, request: CapabilityRequest) -> CapabilityPreflight:
        raise NotImplementedError

    def execute(
        self,
        request: CapabilityRequest,
        *,
        timeout_seconds: int | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CapabilityExecution:
        raise NotImplementedError
```

Do not change routing decisions, observe comparison, fallback behavior or result semantics.

Add one test to `tests/test_capability_service.py` proving the existing concrete `CapabilityRuntime` still satisfies the service behavior.

- [ ] **Step 5: Run adapter and regression tests**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_mcp_capability_runtime \
  tests.test_capability_service \
  tests.test_capability_runtime \
  tests.test_capability_registry -v
```

Expected: all tests pass. Production `config/capabilities.json` and role matrix still use the current compatibility runtime.

---

## Task 7: Add Phase 1A acceptance gates, documentation, and final verification

**Files:**

- Create: `tests/test_mcp_phase_1a_acceptance.py`
- Modify: `scripts/verify.sh`
- Modify: `tests/test_verify_entrypoint.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

### Steps

- [ ] **Step 1: Write a cross-contract acceptance test**

`tests/test_mcp_phase_1a_acceptance.py` must assert the production configuration has these exact properties:

```python
class McpPhase1AAcceptanceTests(unittest.TestCase):
    def test_initial_manifest_is_read_only_and_disabled(self):
        registry = McpCapabilityRegistry.from_file(
            HARNESS_ROOT / "config/mcp_capabilities.json",
            harness_root=HARNESS_ROOT,
        )
        descriptors = registry.list_capabilities()
        self.assertEqual(
            [(item.capability, item.provider) for item in descriptors],
            [
                ("database.inspect", "postgresql"),
                ("gitlab.read", "gitlab"),
                ("workitem.read", "yunxiao"),
            ],
        )
        self.assertTrue(all(item.mutation_level in {"L0", "L1"} for item in descriptors))
        self.assertTrue(all(not item.enabled for item in descriptors))
        self.assertTrue(
            all(item.disabled_reason == "phase_1b_transport_not_configured" for item in descriptors)
        )

    def test_no_database_schema_migration_is_introduced_for_phase_1a(self):
        source = (HARNESS_ROOT / "app/database.py").read_text(encoding="utf-8")
        self.assertNotIn("mcp_gateway_audit", source)
        self.assertNotIn("mcp_evidence", source)

    def test_production_capability_routes_are_not_silently_switched(self):
        matrix = json.loads(
            (HARNESS_ROOT / "config/role_capability_skill_matrix.json").read_text(
                encoding="utf-8"
            )
        )
        serialized = json.dumps(matrix, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("mcp_capability_runtime", serialized)
```

Also assert:

- no key matching secret/token/password/authorization/dsn appears anywhere in MCP config values;
- external-I/O policy validation passes with compatibility debt visible;
- MCP manifest validates and all schema hashes match;
- `DisabledMcpTransport` is the only production-safe default transport exposed by this phase;
- no automatic retry or external write capability exists.

- [ ] **Step 2: Extend the architecture verification mode**

Append these modules to `./scripts/verify.sh architecture`:

```text
tests.test_mcp_contracts
tests.test_mcp_schema_validation
tests.test_mcp_capability_registry
tests.test_mcp_capability_check_cli
tests.test_mcp_gateway
tests.test_mcp_capability_runtime
tests.test_mcp_phase_1a_acceptance
```

Keep `unit`, `offline`, and `manager-static` behavior unchanged.

- [ ] **Step 3: Update README with truthful capability status**

Add an “Enterprise MCP control plane status” section stating:

- Phase 0 inventory and no-new-direct-I/O gate are available via `./scripts/verify.sh architecture`.
- Phase 1A MCP Registry/Gateway/contracts are implemented.
- Yunxiao, GitLab and PostgreSQL MCP descriptors are intentionally disabled.
- existing Provider paths remain compatibility quarantine and are not described as migrated.
- real Provider-to-MCP migration, MCP server connection, retries, persistent audit store, token governor, ChangeContextPack and Supervisor are later phases.
- `Skill = instructions/constraints`; `MCP = connection/execution/evidence`.

- [ ] **Step 4: Update CHANGELOG**

Record the Phase 0/1A increment with explicit non-goals:

```text
- Added external-I/O inventory and source-drift architecture gate.
- Added strict MCP capability/result contracts, disabled initial registry, Gateway and test adapters.
- No production route migration, database migration, external write or real MCP connection in this increment.
```

- [ ] **Step 5: Run focused static and unit gates**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q app tools tests
./scripts/verify.sh architecture
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_capability_contracts \
  tests.test_capability_registry \
  tests.test_capability_runtime \
  tests.test_capability_service \
  tests.test_role_capability_skill_registry \
  tests.test_plugin_inventory \
  tests.test_plugin_migration_security \
  tests.test_plugin_legacy_equivalence -v
```

Expected:

- compile succeeds;
- architecture gate passes;
- compatibility debt count is non-zero and visible;
- unclassified, source drift, forbidden and Skill contract error counts are all zero;
- focused regression tests pass.

- [ ] **Step 6: Run the existing offline gate without masking baseline failures**

Run:

```bash
cd /Users/lym/WorkCode/ai/Harness
./scripts/verify.sh offline
```

Expected handling:

- if it passes, record the exact command and exit status;
- if it hits the previously documented complete-offline timeout/failure, preserve the exact failing stage and classify it as a pre-existing release blocker;
- do not loosen or skip the failing test to make this phase green;
- do not claim enterprise release readiness while the complete offline gate remains red.

- [ ] **Step 7: Perform a no-Git final review**

Because Harness is not currently a Git root, review every file in the Deliverable Map directly and record:

- created files;
- modified files;
- focused test commands and results;
- architecture inventory counts;
- unchanged database schema version;
- unchanged production capability route behavior;
- remaining compatibility-quarantined direct-I/O paths;
- the first recommended Phase 1B migration target.

The recommended first Phase 1B target is `workitem.read / yunxiao` because it is read-only, has an existing capability contract, has strong evidence expectations, and can be migrated without introducing database writes or Git delivery.

---

## Completion Criteria

Phase 0 + Phase 1A is complete only when all of the following are true:

- `./scripts/verify.sh architecture` passes.
- New direct external I/O or changes to reviewed direct-I/O files fail closed.
- Current direct Provider paths remain visible as compatibility debt.
- Skill/runtime mapping no longer implies that a direct Provider is already MCP-backed.
- MCP registry has exactly three disabled, read-only initial descriptors.
- MCP Gateway uses strict identity, size, scope, redaction, evidence and audit gates.
- Gateway tests use only Fake Transport; no real external connection occurs.
- Existing CapabilityService and Provider runtime regression tests pass.
- No database migration, production route switch, external write, Git delivery or credential change occurs.
- README and CHANGELOG describe both delivered capability and remaining boundaries truthfully.
- Existing offline baseline status is reported exactly rather than hidden.

## Explicitly Deferred to Later Phases

- Phase 1B: real bounded MCP server adapters and one-by-one Provider migration.
- Phase 2: `ChangeContextPack` hard gate, repository graph, service/API/table/relationship context.
- Phase 3: Token Governor, evidence-addressed context reuse, context delta and cache economics.
- Phase 4: Supervisor, workflow recovery, persistent evidence/audit store, re-decision semantics.
- Phase 5: enterprise evaluation suites, SLOs, multi-tenant policy, HA/DR and release certification.

## Implementation Handoff

Recommended execution mode: `subagent-driven-development`, one task at a time, with a fresh review after each task and no parallel edits to shared runtime/config files.

Alternative execution mode: execute inline in the current task using `executing-plans`, preserving the same TDD gates and completion criteria.
