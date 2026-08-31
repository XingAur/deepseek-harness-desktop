# Manager Provider 数据库配置中心阶段 A 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Harness Manager 自身数据库替代 Provider JSON 文件，提供六类 Provider 的类型化配置和加密凭证维护，并建立数据库永久只读、知识检索优先和模型 smoke 前置检查的统一合同。

**Architecture:** 新增的配置域只通过 `ManagerProviderRepository` 读写 Manager 数据库；既有任务/运行数据和现有 CLI 保持不变。凭证单独保存为 AES-GCM 密文，主密钥仅由启动 Manager 的部署环境提供。`/providers` 继续是本地回环地址页面，但改为消费类型化字段和 Repository，不再要求填写 JSON；所有外部调用、真实凭证读取和写入 Provider 仍不在本阶段执行。

**Tech Stack:** Python 3、SQLite WAL、`cryptography` 的 AES-GCM、Python `unittest`、现有 `ThreadingHTTPServer`。

## Global Constraints

- 只修改 Harness 本地源工作树；不提交、不 push、不安装插件、不读取真实 token、不连接云效、Git/GitLab、模型或业务数据库。
- Provider 配置、密文凭证、审计和知识咨询记录写入 Manager 数据库；不再把新配置写入 `/Users/lym/WorkCode/ai/his-knowledge/config/provider_profiles.json`。
- `HARNESS_MANAGER_CREDENTIAL_MASTER_KEY` 必须是 URL-safe Base64 编码的 32-byte 密钥；不存在或无效时凭证维护返回 `encryption_unavailable`，绝不自动生成或写入密钥。
- 数据库能力永久只读：不得新增数据库 DDL/DML/存储过程执行器、执行 API、任务队列或写按钮；修改 SQL 只能是不可执行草案。
- 云效、Git、GitLab、模型的外部调用与写动作继续 disabled；本阶段只保存配置、输出前置检查和记录本地审计。
- 所有 HTML、JSON、异常和审计不得包含密钥明文、掩码尾号、Authorization header、私钥、数据库密码或完整连接字符串。
- Manager 继续只监听 `127.0.0.1`；新 HTTP POST 的请求体不得被写入日志。
- 现有 JSON Profile 仅可导入；导入失败或迁移失败不得删除、覆盖或修改源文件。

---

### Task 1: 配置域数据库 schema 与迁移保护

**Files:**
- Modify: `app/database.py`
- Modify: `tests/test_database_governance.py`
- Create: `tests/test_manager_provider_repository.py`

**Interfaces:**
- Consumes: `database.init_db()`, `database.connect()`, `database.now_iso()`.
- Produces: schema version `63` and the tables `manager_provider_scopes`, `manager_provider_profiles`, `manager_provider_credentials`, `manager_provider_action_audits`, `manager_knowledge_consultations`, `manager_provider_imports`.

- [ ] **Step 1: Write the failing migration test**

```python
def test_schema_v63_creates_manager_configuration_tables(self) -> None:
    database.init_db()
    with database.connect() as conn:
        names = {
            row[0] for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
    self.assertEqual(63, database.HARNESS_SCHEMA_VERSION)
    self.assertTrue({
        "manager_provider_scopes", "manager_provider_profiles",
        "manager_provider_credentials", "manager_provider_action_audits",
        "manager_knowledge_consultations", "manager_provider_imports",
    }.issubset(names))
```

- [ ] **Step 2: Run the targeted test and verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_database_governance.DatabaseGovernanceTests.test_schema_v63_creates_manager_configuration_tables -v`

Expected: FAIL because schema version is `62` and the Manager configuration tables do not exist.

- [ ] **Step 3: Add the versioned schema**

Set `HARNESS_SCHEMA_VERSION = 63`. Add the following DDL inside the existing `executescript`, before the schema migration row is recorded:

```sql
create table if not exists manager_provider_scopes (
    id integer primary key autoincrement,
    scope_type text not null check(scope_type in ('local', 'team', 'project', 'user')),
    scope_key text not null,
    display_name text not null default '',
    created_at text not null,
    unique(scope_type, scope_key)
);

create table if not exists manager_provider_profiles (
    id integer primary key autoincrement,
    scope_id integer not null,
    provider text not null,
    profile_key text not null,
    display_name text not null default '',
    enabled integer not null default 1 check(enabled in (0, 1)),
    connection_json text not null default '{}',
    created_at text not null,
    updated_at text not null,
    unique(scope_id, provider, profile_key),
    foreign key(scope_id) references manager_provider_scopes(id)
);

create table if not exists manager_provider_credentials (
    id integer primary key autoincrement,
    profile_id integer not null,
    credential_field text not null,
    cipher_version text not null,
    ciphertext text not null,
    created_at text not null,
    updated_at text not null,
    unique(profile_id, credential_field),
    foreign key(profile_id) references manager_provider_profiles(id)
);

create table if not exists manager_provider_action_audits (
    id integer primary key autoincrement,
    profile_id integer,
    action_type text not null,
    authorization_id_hash text not null default '',
    status text not null,
    details_json text not null default '{}',
    created_at text not null,
    foreign key(profile_id) references manager_provider_profiles(id)
);

create table if not exists manager_knowledge_consultations (
    id integer primary key autoincrement,
    scope_id integer not null,
    query_redacted text not null,
    query_hash text not null,
    retrieval_status text not null,
    citations_json text not null default '[]',
    model_used integer not null default 0 check(model_used in (0, 1)),
    created_at text not null,
    foreign key(scope_id) references manager_provider_scopes(id)
);

create table if not exists manager_provider_imports (
    id integer primary key autoincrement,
    source_sha256 text not null unique,
    imported_count integer not null,
    status text not null,
    created_at text not null
);
```

Record `v0.63-manager-provider-config` as the migration name. Do not remove any existing table, column, migration, default seed or database backup behavior.

- [ ] **Step 4: Run Task 1 verification**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_database_governance -v`

Expected: PASS, including backup-before-migration, failed-migration restoration and new schema assertions.

---

### Task 2: 加密凭证边界与数据库仓库

**Files:**
- Modify: `requirements.txt`
- Create: `app/manager_credential_crypto.py`
- Create: `app/manager_provider_repository.py`
- Create: `tests/test_manager_credential_crypto.py`
- Modify: `tests/test_manager_provider_repository.py`

**Interfaces:**
- Consumes: Task 1 tables and `HARNESS_MANAGER_CREDENTIAL_MASTER_KEY`.
- Produces: `AesGcmCredentialCipher`, `ManagerProviderRepository`, `ProviderProfileRecord`, `CredentialStatus` and `DEFAULT_LOCAL_SCOPE = ('local', 'default')`.

- [ ] **Step 1: Write failing crypto and repository tests**

```python
def test_aes_gcm_cipher_never_returns_plaintext_and_requires_valid_master_key(self) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    with mock.patch.dict(os.environ, {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": key}, clear=False):
        cipher = AesGcmCredentialCipher.from_environment()
        encrypted = cipher.encrypt("SENTINEL_SECRET", aad=b"local/default/model/demo/api_key")
        self.assertTrue(encrypted.startswith("aesgcm.v1."))
        self.assertNotIn("SENTINEL_SECRET", encrypted)
        self.assertEqual("SENTINEL_SECRET", cipher.decrypt(encrypted, aad=b"local/default/model/demo/api_key"))

def test_repository_saves_config_and_only_reports_credential_status(self) -> None:
    repository = ManagerProviderRepository()
    profile = repository.upsert_profile(
        scope_type="local", scope_key="default", provider="model", profile_key="demo",
        display_name="Demo", enabled=True,
        connection={"provider_kind": "openai_compatible", "model": "demo-model"},
    )
    repository.upsert_credential(profile_id=profile.id, field="api_key", plaintext="SENTINEL_SECRET")
    status = repository.profile_status(profile.id)
    self.assertEqual("configured", status["credentials"]["api_key"])
    self.assertNotIn("SENTINEL_SECRET", json.dumps(status, ensure_ascii=False))
```

- [ ] **Step 2: Run tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_manager_credential_crypto tests.test_manager_provider_repository -v`

Expected: FAIL because the cipher and repository modules do not exist.

- [ ] **Step 3: Add AES-GCM, never a home-grown cipher**

Add `cryptography>=42,<46` to `requirements.txt`. In `app/manager_credential_crypto.py`, use `cryptography.hazmat.primitives.ciphers.aead.AESGCM`; validate the URL-safe Base64 environment key decodes to exactly 32 bytes. Encrypt with a fresh 12-byte nonce and return only `aesgcm.v1.<base64(nonce+ciphertext)>`. Raise `CredentialEncryptionUnavailable("encryption_unavailable")` for a missing/invalid key and `CredentialDecryptError("credential_decrypt_failed")` for malformed/tampered ciphertext. Do not generate a key or fall back to plaintext.

```python
def credential_aad(*, scope_type: str, scope_key: str, provider: str, profile_key: str, field: str) -> bytes:
    return f"{scope_type}/{scope_key}/{provider}/{profile_key}/{field}".encode("utf-8")
```

- [ ] **Step 4: Implement the narrow repository**

`ManagerProviderRepository` must initialize the database, create/find the requested scope, serialize only already validated non-secret `connection` mappings, and use `credential_aad` when writing or resolving a credential. Its public methods are:

```python
def upsert_profile(self, *, scope_type: str, scope_key: str, provider: str,
                   profile_key: str, display_name: str, enabled: bool,
                   connection: Mapping[str, object]) -> ProviderProfileRecord: ...
def list_profiles(self, *, scope_type: str = "local", scope_key: str = "default") -> list[ProviderProfileRecord]: ...
def upsert_credential(self, *, profile_id: int, field: str, plaintext: str) -> CredentialStatus: ...
def credential_statuses(self, *, profile_id: int) -> dict[str, str]: ...
def resolve_credential_for_authorized_executor(self, *, profile_id: int, field: str) -> str: ...
def record_action(self, *, profile_id: int | None, action_type: str,
                  status: str, details: Mapping[str, object], authorization_id: str = "") -> None: ...
```

`resolve_credential_for_authorized_executor` is not called from rendering, status endpoints, import code or any existing external executor in this task. Validate audit details with the existing secret-shape guard before persistence and store only the SHA-256 hash of `authorization_id`.

- [ ] **Step 5: Install declared dependency and run GREEN**

Run: `python3 -m pip install -r requirements.txt`

Then run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_manager_credential_crypto tests.test_manager_provider_repository -v`

Expected: PASS. The test output and temporary SQLite database must not contain `SENTINEL_SECRET`.

---

### Task 3: 类型化 Provider schema、JSON 导入与数据库只读策略

**Files:**
- Create: `app/provider_field_schema.py`
- Create: `app/database_read_policy.py`
- Modify: `app/provider_profiles.py`
- Modify: `tests/test_provider_profiles.py`
- Create: `tests/test_provider_field_schema.py`
- Create: `tests/test_database_read_policy.py`

**Interfaces:**
- Consumes: Task 2 `ManagerProviderRepository` and current `build_provider_profile_status` consumers.
- Produces: `provider_field_specs(provider)`, `provider_profile_from_typed_form(data)`, `validate_provider_connection(provider, connection)`, `validate_readonly_sql(sql)` and `import_legacy_provider_profiles(path, repository)`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_model_form_accepts_only_declared_non_secret_fields(self) -> None:
    result = provider_profile_from_typed_form({
        "provider": ["model"], "profile_key": ["deepseek"],
        "provider_kind": ["openai_compatible"], "base_url": ["https://api.example.test/v1"],
        "model": ["deepseek-chat"], "timeout_seconds": ["20"],
        "api_key": ["SENTINEL_SECRET"],
    })
    self.assertNotIn("api_key", result.connection)
    self.assertEqual("model", result.provider)

def test_database_policy_rejects_all_change_statements(self) -> None:
    for sql in ("update patient set name='x'", "drop table t", "with x as (delete from t returning id) select * from x"):
        with self.assertRaisesRegex(ValueError, "database_readonly_policy"):
            validate_readonly_sql(sql)
    self.assertEqual("select", validate_readonly_sql("select * from patient limit 1").statement_kind)
```

- [ ] **Step 2: Run tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_provider_field_schema tests.test_database_read_policy tests.test_provider_profiles -v`

Expected: FAIL because typed schemas, the read policy and legacy importer do not exist.

- [ ] **Step 3: Implement exact field allowlists**

Define only these connection fields:

```python
PROVIDER_CONNECTION_FIELDS = {
    "yunxiao": ("organization_id", "project_id", "project_key", "workitem_scope"),
    "git": ("repository_path", "remote", "branch_policy", "allowed_paths"),
    "gitlab": ("host", "group", "project", "target_branch"),
    "database": ("driver", "host", "port", "database", "schema", "username", "readonly_policy"),
    "model": ("provider_kind", "base_url", "model", "allowed_endpoint_host", "timeout_seconds", "max_output_tokens"),
    "knowledge": ("knowledge_home", "obsidian_vault", "index_path", "allowed_sources"),
}
```

All forms reject unknown and secret-looking keys. Credentials are returned in a separate `credential_inputs` mapping and are never added to `connection`. For database, derive the test identity from the one saved connection and retain `DATABASE_CONNECTION_IDENTITY_FIELDS` as an equality contract. The old JSON parser remains only for importing an existing v1 file; no new HTML route may use it.

`database_read_policy.py` must strip SQL comments and string literals before keyword inspection, reject multi-statement input, and reject any token in `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `GRANT`, `REVOKE`, `CALL`, `EXEC`, `VACUUM`, `ATTACH`, `DETACH`, `PRAGMA`, `COPY`, `LOAD`, `INTO OUTFILE`. Only one top-level `SELECT`, `EXPLAIN SELECT`, or `WITH ... SELECT` statement is valid. This policy is a second guard; a later database executor must still use a database account with read-only permissions.

- [ ] **Step 4: Add one-way legacy import**

Read the old JSON only when the repository has no Profiles for the default scope and its source hash is absent from `manager_provider_imports`. Sanitize every legacy record using existing secret rejection, save only allowed fields, create no credential value, record source SHA-256 and imported count, and return a redacted result. A second import with the same hash must return `status="already_imported"` without duplicate Profiles. Never delete or rewrite the JSON file.

Change the existing `load_provider_profiles()` compatibility facade to list default-scope records from `ManagerProviderRepository`; only when that scope has no records may it attempt the one-way legacy import, then fall back to the current in-memory default templates. `save_provider_profiles()` and `upsert_provider_profile()` become legacy-import helpers only and must not be called by any new Manager POST route. Preserve the existing `build_provider_profile_status()` and `build_provider_connection_test_plan()` input/output contracts so static capability and audit callers remain compatible.

- [ ] **Step 5: Run Task 3 verification**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_provider_field_schema tests.test_database_read_policy tests.test_provider_profiles tests.test_manager_provider_repository -v`

Expected: PASS. Verify `rg -n "SENTINEL_SECRET" /tmp` is not used; assert secret sentinels are absent from repository rows and status JSON in tests instead.

---

### Task 4: Manager 页面、API 与模型 Smoke 前置检查

**Files:**
- Modify: `app/server.py`
- Modify: `app/core_status.py`
- Create: `app/manager_model_smoke_preflight.py`
- Modify: `tests/test_server_core_status_api.py`
- Modify: `tests/test_manager_readiness_card.py`
- Create: `tests/test_manager_model_smoke_preflight.py`

**Interfaces:**
- Consumes: Tasks 2-3 repository, typed form result and `ControlledModelProviderRuntime` safety contract.
- Produces: typed `/providers` HTML, `GET /api/manager/providers`, `POST /providers`, `POST /providers/credentials`, `GET /api/manager/model-smoke-preflight`.

- [ ] **Step 1: Write failing local HTTP tests**

```python
def test_provider_form_saves_model_config_and_never_returns_api_key(self) -> None:
    response = post_form("/providers", {
        "provider": "model", "profile_key": "demo", "display_name": "Demo",
        "provider_kind": "openai_compatible", "base_url": "https://api.example.test/v1",
        "model": "demo-model", "api_key": "SENTINEL_SECRET",
    })
    payload = get_json("/api/manager/providers")
    self.assertEqual(303, response.status)
    self.assertIn("model", {item["provider"] for item in payload["profiles"]})
    self.assertNotIn("SENTINEL_SECRET", json.dumps(payload, ensure_ascii=False))

def test_model_smoke_preflight_is_blocked_without_master_key_and_never_calls_network(self) -> None:
    payload = get_json("/api/manager/model-smoke-preflight?profile_key=demo")
    self.assertEqual("blocked", payload["status"])
    self.assertEqual("encryption_unavailable", payload["reason"])
    self.assertFalse(payload["credentials_read"])
    self.assertFalse(payload["external_calls"])
```

- [ ] **Step 2: Run tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_server_core_status_api tests.test_manager_readiness_card tests.test_manager_model_smoke_preflight -v`

Expected: FAIL because the typed routes, persisted Profile API and smoke preflight do not exist.

- [ ] **Step 3: Replace only the raw edit section of `/providers`**

Keep the existing static capability table and read-only audit sections. Replace the “连接配置 JSON/测试连接 JSON” fields with provider selector, profile key, display name, enabled checkbox and the allowed typed fields from Task 3. Render the credential field as password input only on the write form; after POST redirect, render its status only. Use `html.escape` for all output and do not place existing ciphertext in hidden inputs.

On `POST /providers`, validate fields, write Profile and then invoke `upsert_credential` only for supplied non-empty credential fields. If encryption is unavailable, save no credential and render an error without echoing the submitted secret. `POST /providers/credentials` changes only one selected credential field and follows the same rule. `GET /api/manager/providers` returns Profile metadata, connection fields, credential statuses and action readiness, never ciphertext.

`build_model_smoke_preflight(profile)` returns only `ready` or `blocked`, prerequisites, profile identity, credential configured status, `credentials_read=false`, `external_calls=false` and `write_performed=false`. It must not call `resolve_credential_for_authorized_executor`, `ControlledModelProviderRuntime.run_smoke`, `urllib`, or a provider transport. Add its summary to `core_status` as preparation evidence, not runtime verification.

- [ ] **Step 4: Run Task 4 verification**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_server_core_status_api tests.test_manager_readiness_card tests.test_manager_model_smoke_preflight tests.test_manager_provider_repository -v`

Expected: PASS. Run `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile app/server.py app/manager_model_smoke_preflight.py app/manager_provider_repository.py` and confirm it exits `0`.

---

### Task 5: 知识检索优先与人工咨询日志

**Files:**
- Create: `app/knowledge_consultation.py`
- Modify: `app/knowledge_index.py`
- Modify: `app/server.py`
- Create: `tests/test_knowledge_consultation.py`
- Modify: `tests/test_knowledge_index.py`
- Modify: `tests/test_server_core_status_api.py`

**Interfaces:**
- Consumes: `query_knowledge_index`, Task 1 `manager_knowledge_consultations`, default local scope.
- Produces: `consult_knowledge(query, *, knowledge_home, repository) -> dict[str, object]` and `POST /knowledge/consult`.

- [ ] **Step 1: Write failing retrieval-first tests**

```python
def test_verified_local_match_returns_without_model_and_records_redacted_consultation(self) -> None:
    result = consult_knowledge("门诊收费如何处理", knowledge_home=self.home, repository=self.repository)
    self.assertTrue(result["answerable"])
    self.assertFalse(result["model_used"])
    self.assertEqual("knowledge_hit", result["retrieval_status"])
    self.assertEqual(1, self.repository.count_knowledge_consultations())

def test_candidate_or_expired_note_is_not_returned_as_direct_answer(self) -> None:
    result = consult_knowledge("过期规则", knowledge_home=self.home, repository=self.repository)
    self.assertFalse(result["answerable"])
    self.assertFalse(result["model_used"])
    self.assertEqual("knowledge_insufficient", result["retrieval_status"])

def test_sensitive_consultation_is_recorded_redacted(self) -> None:
    consult_knowledge("token=SENTINEL_SECRET 怎么配置", knowledge_home=self.home, repository=self.repository)
    rendered = self.repository.list_knowledge_consultations()
    self.assertNotIn("SENTINEL_SECRET", json.dumps(rendered, ensure_ascii=False))
```

- [ ] **Step 2: Run tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_knowledge_consultation tests.test_knowledge_index -v`

Expected: FAIL because consultation logging and the retrieval-first service do not exist.

- [ ] **Step 3: Implement deterministic retrieval before any model path**

Extend the local Obsidian index to parse frontmatter `status`, `evidence_level` and `valid_until`, and persist these three fields alongside every markdown row. `query_knowledge_index` may return a direct answer only when at least one matched source has `status=approved`, a non-empty evidence level and no expired `valid_until`; candidate, conflicted, unknown or expired notes are returned only as insufficient context. `consult_knowledge` must first call this verified query; when a direct answer exists, return citations/snippets with `model_used=false` and write a redacted consultation row. When no verified result exists, return `answerable=false`, `model_used=false`, a knowledge-gap message and a candidate suggestion; it must not automatically invoke a model. Redact secret patterns, Authorization values, private keys, Chinese mainland mobile numbers and 18-digit identity-card-like values before storing the query. Store the SHA-256 hash of the original transient query for deduplication but do not store it in rendered output.

Add a small Manager knowledge form and API response only after the service is green. The form provides a local consultation/search, shows citations, and states whether model escalation is necessary; it does not create formal knowledge, mutate Obsidian notes or call an external model.

- [ ] **Step 4: Run Task 5 verification**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_knowledge_consultation tests.test_knowledge_index tests.test_server_core_status_api -v`

Expected: PASS with consultation entries persisted in the temporary Manager database and no secret sentinel in output.

---

### Task 6: 文档、完整专项验证与安全审查

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-09-manager-provider-configuration-design.md`
- Modify: `docs/superpowers/plans/2026-08-09-manager-provider-config-stage-a.md`

**Interfaces:**
- Documents only the implemented stage-A contract; it must not advertise real Provider execution.

- [x] **Step 1: Add current-state documentation tests**

Add one README assertion to `tests/test_manager_provider_repository.py` requiring the phrases `Manager 数据库`, `加密凭证`, `数据库永久只读`, `SQLite 本地` and `PostgreSQL 团队部署`, and requiring that README does not say Provider configuration is stored in Keychain.

- [x] **Step 2: Run it to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_manager_provider_repository.ManagerProviderRepositoryTests.test_readme_describes_database_backed_provider_storage -v`

Expected: FAIL before the README is updated.

- [x] **Step 3: Document the exact boundary**

Document that Profile configuration and encrypted credentials live in Manager DB; the master key is deployment-only; SQLite remains local-first and PostgreSQL is the team deployment target; knowledge uses retrieval before model escalation; and no current route executes a cloud action, remote Git action, model network request or database write. Link to the design and this plan.

- [x] **Step 4: Run complete verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_database_governance \
  tests.test_manager_credential_crypto \
  tests.test_manager_provider_repository \
  tests.test_provider_field_schema \
  tests.test_database_read_policy \
  tests.test_provider_profiles \
  tests.test_manager_model_smoke_preflight \
  tests.test_knowledge_consultation \
  tests.test_knowledge_index \
  tests.test_server_core_status_api \
  tests.test_manager_readiness_card -v
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  app/database.py app/manager_credential_crypto.py \
  app/manager_provider_repository.py app/provider_field_schema.py \
  app/database_read_policy.py app/manager_model_smoke_preflight.py \
  app/knowledge_consultation.py app/knowledge_index.py app/server.py
git diff --check
```

Expected: all tests and compilation pass; `git diff --check` produces no output.

- [x] **Step 5: Independent review gate**

Review every new route and repository method against Global Constraints. Specifically search the scoped diff for `urlopen`, `subprocess`, `Popen`, `os.system`, `DELETE FROM`, `DROP`, `UPDATE `, `INSERT ` and verify any expected occurrences are schema/repository code only, never Provider execution. Confirm no test or source output includes its credential sentinel.

## Deferred, separately planned phases

1. **云效执行器**：只读拉取先行，再引入评论/负责人/状态变更的 dry-run、逐动作人工确认、审计和回读验证。
2. **Git / GitLab 执行器**：受控 worktree 拉取、建分支和本地提交，再到每次明确确认的远端 push/MR/评论。
3. **模型执行器**：由 Task 4 的 Profile 和预检合同驱动既有单节点 smoke；真实网络请求仍需要用户提供测试 Profile、授权 ID 和单次确认。
4. **团队 PostgreSQL 部署**：先完成备份、SQLite 导出校验、PostgreSQL 导入校验、角色模型和回滚演练，再切换中心 Manager 数据库。该阶段不删除任何本机 SQLite 历史。

## Completion State

当前实现说明：阶段 A 只完成本地 Manager 配置域、类型化页面/API、安全存储、知识检索优先合同和模型 smoke 前置检查。它没有真实 Provider executor，不会执行云效动作、远端 Git/GitLab 动作、模型网络请求或业务数据库写入。数据库修改 SQL 永远只生成草案，并由用户在 Harness 外人工执行。

- [x] Provider 配置与凭证已在 Manager 数据库保存，JSON 只作为可恢复导入源。
- [x] 六类 Provider 有类型化表单，凭证值不在 UI/API/日志/审计中出现。
- [x] 数据库只读策略有代码与合同双重保护，修改 SQL 只能作为草案。
- [x] 知识检索优先、人工咨询脱敏记录，当前不自动调用模型。
- [x] 模型 Smoke 只具备无网络、无凭证读取的预检；常规真实 runtime 仍冻结。
- [x] 通过专项测试、编译、diff 检查和独立审查；未执行任何外部动作。
