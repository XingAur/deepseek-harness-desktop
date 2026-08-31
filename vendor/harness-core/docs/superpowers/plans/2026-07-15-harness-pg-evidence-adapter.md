# HIS Harness PostgreSQL 数据证据适配器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Harness 增加一个独立、按需、只读、限时且可自主定位候选库表的 PostgreSQL 数据证据 CLI；普通需求 workflow 默认零数据库连接。

**Architecture:** `app/pg_evidence.py` 负责 Profile 发现、策略校验、代码证据候选、SQL 守卫、限时执行和脱敏产物；`tools/pg_evidence.py` 只解析显式请求并写入独立输出目录。实际连接通过可注入的 `PostgresExecutor` 边界实现，测试只使用 fake executor，开发期不连接真实数据库。

**Tech Stack:** Python 3 标准库、现有 `app.yunxiao_read` 凭证读取/`redact_secrets`、`unittest`；运行时 PostgreSQL 驱动采用可选 `psycopg` 适配，未安装时快速返回结构化 `blocked`。

## Global Constraints

- 只改 `/Users/lym/WorkCode/ai/Harness`，不改 DFHIS 业务仓库。
- 不读取、打印、记录或测试真实 DSN、用户、密码和数据库数据。
- 新 CLI 只有显式 `--mode execute` 才可尝试连接；默认 `plan` 与普通 workflow 都不得连接。
- 只支持 `test`/`development` Profile，生产/未知环境一律拒绝执行。
- 限制为连接 5 秒、单查询 10 秒、总流程 45 秒、最多 3 次元数据查询、最多 50 行结果；失败不重试。
- 禁止数据库、云效、TAPD、Git、部署和业务仓库写入。
- Harness 根目录不是 Git 仓库；本计划不包含提交、推送或合并步骤。

---

### Task 1: Profile 发现、策略模型与只读 SQL 守卫

**Files:**
- Create: `app/pg_evidence.py`
- Create: `tests/test_pg_evidence.py`
- Create: `config/pg_evidence_profiles.example.json`

**Interfaces:**
- Exports `discover_pg_profiles(credentials: Mapping[str, Any]) -> list[PgProfile]`。
- Exports `load_pg_policy(path: str | Path) -> PgEvidencePolicy`。
- Exports `validate_readonly_sql(sql: str, parameters: Mapping[str, Any]) -> SqlGuardResult`。
- `PgProfile` only contains a profile name and credential presence/source metadata; it never serializes credential values.

- [ ] **Step 1: 写 Profile 发现和 SQL 拒绝测试**

在 `tests/test_pg_evidence.py` 增加：

```python
def test_discovers_named_readonly_profile_without_exposing_values(self) -> None:
    profiles = discover_pg_profiles({
        "pg_df_jj_menzhen_readonly_dsn": "postgresql://secret-host/db",
        "pg_df_jj_menzhen_readonly_user": "readonly",
        "pg_df_jj_menzhen_readonly_password": "secret-password",
    })
    self.assertEqual(["df_jj_menzhen"], [profile.name for profile in profiles])
    self.assertNotIn("secret-host", json.dumps([profile.to_dict() for profile in profiles]))

def test_sql_guard_allows_parameterized_select_and_blocks_write_or_multi_statement(self) -> None:
    self.assertEqual("pass", validate_readonly_sql(
        "SELECT keshiid FROM df_jj_menzhen.mz_guahaob WHERE bingrenid = %(patient_id)s",
        {"patient_id": "x"},
    ).status)
    self.assertEqual("blocked", validate_readonly_sql("DELETE FROM mz_guahaob", {}).status)
    self.assertEqual("blocked", validate_readonly_sql("SELECT 1; UPDATE mz_guahaob SET x = 1", {}).status)
```

- [ ] **Step 2: 运行失败测试**

Run:

```bash
python3 -m unittest tests.test_pg_evidence.PgEvidenceProfileTests -v
```

Expected: FAIL，因为 `app.pg_evidence` 尚不存在。

- [ ] **Step 3: 实现非密钥模型与保守 SQL 守卫**

在 `app/pg_evidence.py` 新增以下稳定结构：

```python
@dataclass(frozen=True)
class PgProfile:
    name: str
    dsn_configured: bool
    user_configured: bool
    password_configured: bool
    credential_prefix: str

@dataclass(frozen=True)
class SqlGuardResult:
    status: str
    blockers: tuple[str, ...] = ()
    parameter_names: tuple[str, ...] = ()
```

用正则和字符串扫描实现单语句顶层 `SELECT` 白名单，拒绝写入关键字、多语句、锁、事务和未提供的命名参数。不要把 SQL 原文或参数值加入 `SqlGuardResult`。

创建 `config/pg_evidence_profiles.example.json`，包含 `his_test` 的 `test` 环境、限制值和敏感列模式；该文件不含 DSN 或任何密码。

- [ ] **Step 4: 运行 Task 1 测试**

Run:

```bash
python3 -m unittest tests.test_pg_evidence.PgEvidenceProfileTests tests.test_pg_evidence.PgEvidenceSqlGuardTests -v
python3 -m py_compile app/pg_evidence.py
```

Expected: PASS。

### Task 2: 代码证据候选与限时假执行器

**Files:**
- Modify: `app/pg_evidence.py`
- Modify: `tests/test_pg_evidence.py`

**Interfaces:**
- Exports `build_pg_evidence_plan(request: PgEvidenceRequest, policy: PgEvidencePolicy, profiles: Sequence[PgProfile], project_root: Path) -> PgEvidencePlan`。
- Exports `execute_pg_evidence_plan(plan: PgEvidencePlan, executor: PostgresExecutor) -> PgEvidenceResult`。
- `PostgresExecutor` provides `discover_metadata()` and `execute_select()`; production driver is never required by unit tests.

- [ ] **Step 1: 写唯一候选、歧义与时限测试**

在 `tests/test_pg_evidence.py` 增加 fake executor：

```python
class FakePostgresExecutor:
    def __init__(self, metadata: list[dict[str, str]], rows: list[dict[str, object]]) -> None:
        self.metadata = metadata
        self.rows = rows
        self.calls: list[str] = []

    def discover_metadata(self, **kwargs):
        self.calls.append("metadata")
        return self.metadata

    def execute_select(self, **kwargs):
        self.calls.append("select")
        return self.rows
```

覆盖：源码 `@Table(name="mz_guahaob", schema="df_jj_menzhen")` 让 `df_jj_menzhen` 成为唯一候选；两个同分候选返回 `needs_evidence` 且不调用 `execute_select`；超过 `max_metadata_queries` 的计划不再调用 executor。

- [ ] **Step 2: 运行失败测试**

Run:

```bash
python3 -m unittest tests.test_pg_evidence.PgEvidencePlanningTests -v
```

Expected: FAIL，因为计划与 executor 接口尚不存在。

- [ ] **Step 3: 实现候选评分、探测预算和脱敏结果**

实现只读源码扫描：只读取传入 `project_root` 下 `.java`、`.xml`、`.sql`、`.yml`、`.yaml`、`.properties` 文件的有限文本，提取 schema/table 和 Profile 名词命中；不执行项目命令。

候选评分必须记录来源：`source_schema_match`、`source_table_match`、`profile_name_match`、`metadata_table_match`。仅当最高分唯一且达到阈值时允许生成可执行 `SELECT` 计划；否则返回 `needs_evidence`。

对每个结果列按 `sensitive_column_patterns` 掩码。`PgEvidenceResult` 只保存掩码后的 rows、行数、耗时、查询模板 ID 和错误摘要；参数仅保存名称、类型和 SHA-256 摘要。

- [ ] **Step 4: 运行 Task 2 测试**

Run:

```bash
python3 -m unittest tests.test_pg_evidence.PgEvidencePlanningTests tests.test_pg_evidence.PgEvidenceExecutionTests -v
```

Expected: PASS。

### Task 3: 独立 CLI、产物脱敏与可选驱动失败收敛

**Files:**
- Create: `tools/pg_evidence.py`
- Modify: `app/pg_evidence.py`
- Modify: `tests/test_pg_evidence.py`
- Modify: `tools/self_check.py`

**Interfaces:**
- CLI arguments: `--request-file`, `--profile-policy`, `--credentials-file`, `--mode {plan,execute}`, `--project-root`, `--output-dir`。
- Outputs: `pg_evidence_plan.json/md`, `pg_evidence_result.json/md`, `pg_evidence_audit.json`。
- `--mode plan` must not instantiate a driver or call an executor.

- [ ] **Step 1: 写 CLI 零连接、脱敏与驱动缺失测试**

在 `tests/test_pg_evidence.py` 增加：

```python
def test_plan_mode_never_calls_executor_or_loads_driver(self) -> None:
    result = run_pg_evidence(..., mode="plan", executor_factory=fail_if_called)
    self.assertEqual("planned", result.status)

def test_audit_redacts_dsn_password_parameter_and_sensitive_row_values(self) -> None:
    serialized = render_pg_evidence_outputs(result_with_sensitive_values)
    self.assertNotIn("postgresql://", serialized)
    self.assertNotIn("secret-password", serialized)
    self.assertNotIn("13800138000", serialized)

def test_execute_without_psycopg_returns_blocked_within_budget(self) -> None:
    result = run_pg_evidence(..., mode="execute", executor_factory=missing_driver_factory)
    self.assertEqual("blocked", result.status)
    self.assertIn("驱动", "\n".join(result.blockers))
```

- [ ] **Step 2: 运行失败测试**

Run:

```bash
python3 -m unittest tests.test_pg_evidence.PgEvidenceCliTests -v
```

Expected: FAIL，因为独立 CLI 和产物渲染器尚不存在。

- [ ] **Step 3: 实现 CLI 与安全输出**

`tools/pg_evidence.py` 使用现有凭证文件路径约定，但只将 key-value 映射传给 `discover_pg_profiles()`，禁止打印映射。

`plan` 模式仅写计划和 audit；`execute` 模式在 Profile 环境、SQL 守卫、候选唯一性都通过后才尝试加载可选 `psycopg`。驱动缺失、超时或连接失败必须生成结构化 `blocked`/`timeout`/`failed` 结果并退出非零，不重试。

在 `tools/self_check.py` 加入假凭证和 fake executor 的数据库适配器自检；不得使用真实 `HARNESS_CREDENTIALS_FILE` 或网络连接。

- [ ] **Step 4: 运行 Task 3 测试与自检**

Run:

```bash
python3 -m unittest tests.test_pg_evidence -v
python3 tools/self_check.py --mode mock --retain-output --output-dir /tmp/his_harness_v048_self_check
```

Expected: PASS，且输出不包含 self-check 中的假 secret。

### Task 4: 文档化 v0.48 边界并做完整回归

**Files:**
- Modify: `README.md`
- Modify: `HANDOFF.md`
- Modify: `docs/superpowers/specs/2026-07-15-harness-pg-evidence-adapter-design.md`

**Interfaces:**
- README must document that database access is opt-in and `plan` mode never connects.
- HANDOFF must record v0.48 scope, guardrails, artifacts and the later real-smoke prerequisite.

- [ ] **Step 1: 写文档与示例请求**

在 README 增加一个不含真实连接信息的 `pg_evidence_request.json` 示例：包含业务关键词、项目路径、接口/字段线索和命名参数；明确 `--mode execute` 只在用户明确要求查数据时使用。

HANDOFF 记录：普通需求零连接、Profile 自动发现、只读 SQL 守卫、45 秒预算、脱敏审计和“真实 smoke 仍需用户明确请求”的边界。

- [ ] **Step 2: 运行最终回归**

Run:

```bash
python3 -m py_compile app/pg_evidence.py tools/pg_evidence.py app/harness.py tools/self_check.py
python3 -m unittest discover -s tests -v
python3 tools/self_check.py --mode mock --retain-output --output-dir /tmp/his_harness_v048_final_self_check
python3 tools/pg_evidence.py --help
```

Expected: 全部 PASS；mock 自检和 CLI help 不读取真实 PG 凭证、不建立网络连接。

- [ ] **Step 3: 最终安全检查**

Run:

```bash
rg -n "pg_his_test_readonly|postgresql://|secret-password|13800138000" /tmp/his_harness_v048_final_self_check README.md HANDOFF.md || true
```

Expected: 不出现真实凭证；示例仅允许占位符或 key 名。
