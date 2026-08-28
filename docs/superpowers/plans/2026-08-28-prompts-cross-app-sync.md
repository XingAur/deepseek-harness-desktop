# Prompts 跨应用同步 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-08-28-prompts-cross-app-sync-design.md` 实现:Rust `prompts/` 模块(SQLite SSOT + Claude/Codex/DSH 三目标写入器 + 回填保护)、插件「扩展中心」入口(替换 Agent 按钮)+ 提示词 tab、8 个 bridge v2 动作、完整测试。

**Architecture:** 预设与激活映射存 `<AppPaths.state>/prompts.db`(rusqlite,单 Mutex 连接);`service.rs` 在锁内完成 回填→备份→原子写→落激活;三个目标 live 文件路径由 Rust 推导(渲染器不传路径);UI 为插件内 `sidebar.footer.action` 新注册按钮 + overlay 面板,经双份 bridge 契约新增 8 个 v2 动作。

**Tech Stack:** Tauri 2 + rusqlite(bundled)+ sha2 + chrono + uuid;React 18 + 手写 CSS(styles.ts)+ vitest/jsdom/@testing-library;新增前端依赖 `marked`、`dompurify`。

**约定(全计划通用):**

- Rust 测试在文件内 `#[cfg(test)] mod tests`,临时目录用 `tempfile::tempdir()`(已是 dev-dependency)。
- 前端命令:`npm run plugin:test -w @dsh/desktop-plugin -- tests/<file>` 跑单个插件测试;根测试 `npx vitest run src/<file>`;Rust `cargo test --manifest-path src-tauri/Cargo.toml <关键字>`。
- 提交信息用中文,格式 `feat(prompts): ...` / `test(prompts): ...` / `refactor(desktop-plugin): ...`,每次提交只含该任务的文件。
- 所有 Tauri 命令都带 `generation_id: String, session_id: String` 参数并先做 `coordinator.validate_generation(&generation_id)` + `validate_agent_identifier(&session_id, "Session ID")`(与 `commands.rs:821` 的 `agent_plugin_catalog` 一致);v2 桥会把 `generationId`/`sessionId` 展平进 invoke 参数([src/workbench-bridge.ts:96-98](src/workbench-bridge.ts#L96-L98))。

---

### Task 1: DSH 目标文件名 spike

**Files:** 无代码改动;结论写入 Task 6 的 `targets.rs` 常量。

- [ ] **Step 1: 查证受管 DeepSeek Harness 读取全局提示词/记忆文件的约定**

依次尝试(找到即停):
1. 本机已预配运行时树:查 `%LOCALAPPDATA%/ai.deepseek.harness.desktop/profiles/<id>/` 下是否存在 `AGENTS.md`、`CLAUDE.md` 或 `memory*` 文件,以及 `settings.json` 内是否有相关键。
2. 上游包文档:`npm view @deepseek-ai/dsh readme` 或在 https://www.npmjs.com/package/@deepseek-ai/dsh 的 README 中搜 "AGENTS.md"/"memory"/"全局"。
3. 受管运行时已下载的 harness 源码/资源里 grep `AGENTS.md`。

- [ ] **Step 2: 记录结论**

- 有约定文件名 → 把结论(文件名 + 证据出处)写进 Task 6 的 `DSH_GLOBAL_PROMPT_FILENAME` 常量注释。
- 确认无全局文件机制 → Task 6 中把 `dsh_prompt_path` 改为返回 `None`(DSH 目标自动隐藏,其余设计不变)。
- 查不到 → 暂取默认值 `"AGENTS.md"`,在计划执行收尾时向用户报告此项仍待验证。

---

### Task 2: Rust 类型层 `prompts/model.rs`

**Files:**
- Create: `src-tauri/src/prompts/mod.rs`
- Create: `src-tauri/src/prompts/model.rs`

- [ ] **Step 1: 建模块骨架与类型(含失败测试)**

`src-tauri/src/prompts/mod.rs`:

```rust
pub mod migrations;
pub mod model;
pub mod service;
pub mod store;
pub mod targets;
```

`src-tauri/src/prompts/model.rs`:

```rust
use serde::{Deserialize, Serialize};

pub const MAX_PROMPT_BYTES: usize = 24 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PromptTarget {
    Claude,
    Codex,
    Dsh,
}

impl PromptTarget {
    pub const ALL: [PromptTarget; 3] = [PromptTarget::Claude, PromptTarget::Codex, PromptTarget::Dsh];
    pub fn as_str(self) -> &'static str {
        match self {
            PromptTarget::Claude => "claude",
            PromptTarget::Codex => "codex",
            PromptTarget::Dsh => "dsh",
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PromptPreset {
    pub id: String,
    pub title: String,
    pub content: String,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PresetSummary {
    pub id: String,
    pub title: String,
    pub updated_at: i64,
    pub activated_targets: Vec<PromptTarget>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TargetStatus {
    pub target: PromptTarget,
    pub installed: bool,
    pub live_file_exists: bool,
    pub active_preset_id: Option<String>,
    pub live_content_sha256: Option<String>,
    pub matches_active_preset: bool,
    pub oversized: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConflictCandidate {
    pub target: PromptTarget,
    pub content: String,
    pub updated_at: i64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum SaveOutcome {
    Saved { preset: PromptPreset, projected: Vec<TargetStatus> },
    BackfillConflict { preset_id: String, candidates: Vec<ConflictCandidate> },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum ActivateOutcome {
    Ok { status: TargetStatus },
    BackfillConflict { preset_id: String, candidates: Vec<ConflictCandidate> },
}

#[derive(Debug, thiserror::Error)]
pub enum PromptsError {
    #[error("prompts_store_error: {0}")]
    Store(String),
    #[error("prompts_target_not_installed: {0}")]
    TargetNotInstalled(PromptTarget),
    #[error("prompts_preset_active: {0}")]
    PresetActive(String),
    #[error("prompts_too_large")]
    TooLarge,
    #[error("prompts_invalid_input: {0}")]
    InvalidInput(String),
    #[error("prompts_io_error: {0}")]
    Io(String),
}

pub type Result<T> = std::result::Result<T, PromptsError>;
```

`src-tauri/src/prompts/service.rs`、`store.rs`、`targets.rs` 先建空文件(仅 `use` 占位后续任务填充)。

在 `src-tauri/src/lib.rs` 模块声明区(`mod profile;` 附近)加一行:

```rust
mod prompts;
```

- [ ] **Step 2: 写失败测试(serde 形状 + 上限常量)**

追加到 `src-tauri/src/prompts/model.rs` 末尾:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn target_serializes_to_lowercase_and_back() {
        assert_eq!(serde_json::to_string(&PromptTarget::Claude).unwrap(), "\"claude\"");
        assert_eq!(serde_json::to_string(&PromptTarget::Dsh).unwrap(), "\"dsh\"");
        let parsed: PromptTarget = serde_json::from_str("\"codex\"").unwrap();
        assert_eq!(parsed, PromptTarget::Codex);
    }

    #[test]
    fn status_uses_camel_case_keys() {
        let status = TargetStatus {
            target: PromptTarget::Claude,
            installed: true,
            live_file_exists: false,
            active_preset_id: None,
            live_content_sha256: None,
            matches_active_preset: false,
            oversized: false,
        };
        let json = serde_json::to_value(&status).unwrap();
        assert!(json.get("liveFileExists").is_some());
        assert!(json.get("activePresetId").is_some());
    }

    #[test]
    fn outcome_tags_are_kebab_case() {
        let saved = serde_json::to_value(SaveOutcome::Saved {
            preset: PromptPreset { id: "p1".into(), title: "t".into(), content: String::new(), created_at: 0, updated_at: 0 },
            projected: vec![],
        })
        .unwrap();
        assert_eq!(saved["kind"], "saved");
        let conflict = serde_json::to_value(ActivateOutcome::BackfillConflict {
            preset_id: "p1".into(),
            candidates: vec![],
        })
        .unwrap();
        assert_eq!(conflict["kind"], "backfill-conflict");
    }

    #[test]
    fn max_prompt_bytes_is_24kib() {
        assert_eq!(MAX_PROMPT_BYTES, 24576);
    }
}
```

- [ ] **Step 3: 跑测试验证通过**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::model`
Expected: 4 个测试 PASS(model.rs 是纯类型,本任务测试与实现同批落地,确认形状即可)。

- [ ] **Step 4: Commit**

```bash
git add src-tauri/src/prompts/ src-tauri/src/lib.rs
git commit -m "feat(prompts): 新增提示词模块类型层"
```

---

### Task 3: 表结构迁移 `prompts/migrations.rs`

**Files:**
- Modify: `src-tauri/src/prompts/migrations.rs`

- [ ] **Step 1: 写失败测试**

`src-tauri/src/prompts/migrations.rs` 先只写测试(实现 Step 2):

```rust
#[cfg(test)]
mod tests {
    use rusqlite::Connection;
    use super::{migrate_to_current, user_version, validate_current_schema, CURRENT_SCHEMA_VERSION};

    #[test]
    fn fresh_database_gets_prompts_schema() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate_to_current(&mut connection).unwrap();
        assert_eq!(user_version(&connection).unwrap(), CURRENT_SCHEMA_VERSION);
        validate_current_schema(&connection).unwrap();
        let activation_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM prompt_activations", [], |row| row.get(0))
            .unwrap();
        assert_eq!(activation_count, 0);
    }

    #[test]
    fn single_active_target_is_enforced_by_primary_key() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate_to_current(&mut connection).unwrap();
        connection.execute_batch(
            "INSERT INTO prompts (id, title, content, created_at, updated_at) VALUES
             ('p1', 'A', 'content-a', 1, 1);
             INSERT INTO prompt_activations (target, preset_id, activated_at) VALUES
             ('claude', 'p1', 1);",
        ).unwrap();
        let replaced = connection.execute(
            "INSERT INTO prompt_activations (target, preset_id, activated_at) VALUES ('claude', 'p1', 2)",
            [],
        );
        assert!(replaced.is_err(), "同一目标二次激活必须被主键拒绝");
    }

    #[test]
    fn future_schema_version_is_rejected() {
        let mut connection = Connection::open_in_memory().unwrap();
        connection.pragma_update(None, "user_version", CURRENT_SCHEMA_VERSION + 1).unwrap();
        assert!(migrate_to_current(&mut connection).is_err());
    }
}
```

- [ ] **Step 2: 跑测试确认编译失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::migrations`
Expected: 编译失败(函数/常量未定义)。

- [ ] **Step 3: 实现迁移**

在 `src-tauri/src/prompts/migrations.rs` 顶部(tests 模块之前)加入:

```rust
use rusqlite::Connection;

pub const CURRENT_SCHEMA_VERSION: i64 = 1;

const V1_SCHEMA: &str = r#"
CREATE TABLE prompts (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE prompt_activations (
    target       TEXT PRIMARY KEY CHECK (target IN ('claude','codex','dsh')),
    preset_id    TEXT REFERENCES prompts(id) ON DELETE SET NULL,
    activated_at INTEGER NOT NULL
);
"#;

pub fn user_version(connection: &Connection) -> rusqlite::Result<i64> {
    connection.query_row("PRAGMA user_version", [], |row| row.get(0))
}

pub fn validate_current_schema(connection: &Connection) -> rusqlite::Result<()> {
    for table in ["prompts", "prompt_activations"] {
        let exists: bool = connection.query_row(
            "SELECT EXISTS (SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1)",
            [table],
            |row| row.get(0),
        )?;
        if !exists {
            return Err(rusqlite::Error::InvalidQuery);
        }
    }
    Ok(())
}

pub fn migrate_to_current(connection: &mut Connection) -> rusqlite::Result<()> {
    let version = user_version(connection)?;
    match version {
        0 => connection.execute_batch(V1_SCHEMA)?,
        CURRENT_SCHEMA_VERSION => return Ok(()),
        _ => return Err(rusqlite::Error::InvalidQuery),
    }
    connection.pragma_update(None, "user_version", CURRENT_SCHEMA_VERSION)?;
    Ok(())
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::migrations`
Expected: 3 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/prompts/migrations.rs
git commit -m "feat(prompts): 新增 prompts 数据库迁移"
```

---

### Task 4: 存储层 `prompts/store.rs`

**Files:**
- Modify: `src-tauri/src/prompts/store.rs`

- [ ] **Step 1: 写失败测试(CRUD + 激活映射)**

`src-tauri/src/prompts/store.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::PromptsStore;
    use crate::prompts::model::PromptTarget;

    fn store() -> PromptsStore {
        let dir = tempfile::tempdir().unwrap();
        PromptsStore::open(&dir.path().join("state/prompts.db")).unwrap()
    }

    #[test]
    fn open_is_idempotent_and_survives_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state/prompts.db");
        {
            let store = PromptsStore::open(&path).unwrap();
            store.insert_preset("p1", "标题", "内容", 10, 10).unwrap();
        }
        let reopened = PromptsStore::open(&path).unwrap();
        let preset = reopened.get_preset("p1").unwrap().unwrap();
        assert_eq!(preset.title, "标题");
    }

    #[test]
    fn preset_crud_roundtrip() {
        let store = store();
        store.insert_preset("p1", "A", "old", 1, 1).unwrap();
        store.update_preset("p1", "A2", "new", 2).unwrap();
        let preset = store.get_preset("p1").unwrap().unwrap();
        assert_eq!((preset.title.as_str(), preset.content.as_str(), preset.updated_at), ("A2", "new", 2));
        assert_eq!(store.list_presets().unwrap().len(), 1);
        store.delete_preset("p1").unwrap();
        assert!(store.get_preset("p1").unwrap().is_none());
    }

    #[test]
    fn activation_is_set_and_cleared_per_target() {
        let store = store();
        store.insert_preset("p1", "A", "c", 1, 1).unwrap();
        store.set_activation(PromptTarget::Claude, "p1", 5).unwrap();
        store.set_activation(PromptTarget::Codex, "p1", 6).unwrap();
        assert_eq!(
            store.active_preset_id(PromptTarget::Claude).unwrap().as_deref(),
            Some("p1")
        );
        assert_eq!(store.activated_targets("p1").unwrap(), vec![PromptTarget::Codex, PromptTarget::Claude]);
        store.clear_activation(PromptTarget::Claude).unwrap();
        assert_eq!(store.active_preset_id(PromptTarget::Claude).unwrap(), None);
    }

    #[test]
    fn deleting_preset_nulls_activation_reference() {
        let store = store();
        store.insert_preset("p1", "A", "c", 1, 1).unwrap();
        store.set_activation(PromptTarget::Claude, "p1", 5).unwrap();
        store.delete_preset("p1").unwrap();
        assert_eq!(store.active_preset_id(PromptTarget::Claude).unwrap(), None);
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::store`
Expected: 编译失败。

- [ ] **Step 3: 实现 store**

在 `src-tauri/src/prompts/store.rs` 顶部(tests 之前)加入:

```rust
use std::{path::Path, sync::Mutex};

use rusqlite::{Connection, OpenFlags};

use crate::prompts::model::{PromptPreset, PromptTarget, PromptsError, Result};
use crate::prompts::migrations;

pub struct PromptsStore {
    connection: Mutex<Connection>,
}

impl PromptsStore {
    pub fn open(database_path: &Path) -> Result<Self> {
        if let Some(parent) = database_path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| PromptsError::Io(error.to_string()))?;
        }
        let connection = Connection::open_with_flags(
            database_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
        )
        .map_err(|error| PromptsError::Store(error.to_string()))?;
        connection
            .busy_timeout(std::time::Duration::from_secs(5))
            .map_err(|error| PromptsError::Store(error.to_string()))?;
        connection
            .pragma_update(None, "foreign_keys", true)
            .map_err(|error| PromptsError::Store(error.to_string()))?;
        let mut connection = connection;
        migrations::migrate_to_current(&mut connection).map_err(|error| PromptsError::Store(error.to_string()))?;
        migrations::validate_current_schema(&connection).map_err(|error| PromptsError::Store(error.to_string()))?;
        Ok(Self { connection: Mutex::new(connection) })
    }

    pub fn with_lock<T>(&self, operation: impl FnOnce(&Connection) -> Result<T>) -> Result<T> {
        let guard = self.connection.lock().map_err(|_| PromptsError::Store("存储锁中毒".into()))?;
        operation(&guard)
    }

    pub fn insert_preset(&self, id: &str, title: &str, content: &str, created_at: i64, updated_at: i64) -> Result<()> {
        self.with_lock(|connection| {
            connection
                .execute(
                    "INSERT INTO prompts (id, title, content, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5)",
                    rusqlite::params![id, title, content, created_at, updated_at],
                )
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            Ok(())
        })
    }

    pub fn update_preset(&self, id: &str, title: &str, content: &str, updated_at: i64) -> Result<()> {
        self.with_lock(|connection| {
            let changed = connection
                .execute(
                    "UPDATE prompts SET title = ?2, content = ?3, updated_at = ?4 WHERE id = ?1",
                    rusqlite::params![id, title, content, updated_at],
                )
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            if changed == 0 {
                return Err(PromptsError::InvalidInput(format!("预设不存在: {id}")));
            }
            Ok(())
        })
    }

    pub fn get_preset(&self, id: &str) -> Result<Option<PromptPreset>> {
        self.with_lock(|connection| {
            connection
                .query_row(
                    "SELECT id, title, content, created_at, updated_at FROM prompts WHERE id = ?1",
                    [id],
                    |row| {
                        Ok(PromptPreset {
                            id: row.get(0)?,
                            title: row.get(1)?,
                            content: row.get(2)?,
                            created_at: row.get(3)?,
                            updated_at: row.get(4)?,
                        })
                    },
                )
                .map(Some)
                .or_else(|error| match error {
                    rusqlite::Error::QueryReturnedNoRows => Ok(None),
                    other => Err(PromptsError::Store(other.to_string())),
                })
        })
    }

    pub fn list_presets(&self) -> Result<Vec<PromptPreset>> {
        self.with_lock(|connection| {
            let mut statement = connection
                .prepare("SELECT id, title, content, created_at, updated_at FROM prompts ORDER BY updated_at DESC")
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            let rows = statement
                .query_map([], |row| {
                    Ok(PromptPreset {
                        id: row.get(0)?,
                        title: row.get(1)?,
                        content: row.get(2)?,
                        created_at: row.get(3)?,
                        updated_at: row.get(4)?,
                    })
                })
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            rows.collect::<std::result::Result<Vec<_>, _>>().map_err(|error| PromptsError::Store(error.to_string()))
        })
    }

    pub fn delete_preset(&self, id: &str) -> Result<()> {
        self.with_lock(|connection| {
            connection
                .execute("DELETE FROM prompts WHERE id = ?1", [id])
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            Ok(())
        })
    }

    pub fn set_activation(&self, target: PromptTarget, preset_id: &str, activated_at: i64) -> Result<()> {
        self.with_lock(|connection| {
            connection
                .execute(
                    "INSERT INTO prompt_activations (target, preset_id, activated_at) VALUES (?1, ?2, ?3)
                     ON CONFLICT(target) DO UPDATE SET preset_id = ?2, activated_at = ?3",
                    rusqlite::params![target.as_str(), preset_id, activated_at],
                )
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            Ok(())
        })
    }

    pub fn clear_activation(&self, target: PromptTarget) -> Result<()> {
        self.with_lock(|connection| {
            connection
                .execute("DELETE FROM prompt_activations WHERE target = ?1", [target.as_str()])
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            Ok(())
        })
    }

    pub fn active_preset_id(&self, target: PromptTarget) -> Result<Option<String>> {
        self.with_lock(|connection| {
            connection
                .query_row(
                    "SELECT preset_id FROM prompt_activations WHERE target = ?1",
                    [target.as_str()],
                    |row| row.get::<_, Option<String>>(0),
                )
                .map(Some)
                .or_else(|error| match error {
                    rusqlite::Error::QueryReturnedNoRows => Ok(None),
                    other => Err(PromptsError::Store(other.to_string())),
                })
        })
    }

    pub fn activated_targets(&self, preset_id: &str) -> Result<Vec<PromptTarget>> {
        self.with_lock(|connection| {
            let mut statement = connection
                .prepare("SELECT target FROM prompt_activations WHERE preset_id = ?1 ORDER BY target")
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            let rows = statement
                .query_map([preset_id], |row| {
                    let value: String = row.get(0)?;
                    match value.as_str() {
                        "claude" => Ok(PromptTarget::Claude),
                        "codex" => Ok(PromptTarget::Codex),
                        "dsh" => Ok(PromptTarget::Dsh),
                        other => Err(rusqlite::Error::FromSqlConversionFailure(
                            0,
                            rusqlite::types::Type::Text,
                            format!("未知目标 {other}").into(),
                        )),
                    }
                })
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            rows.collect::<std::result::Result<Vec<_>, _>>().map_err(|error| PromptsError::Store(error.to_string()))
        })
    }

    pub fn all_activations(&self) -> Result<Vec<(PromptTarget, String)>> {
        self.with_lock(|connection| {
            let mut statement = connection
                .prepare("SELECT target, preset_id FROM prompt_activations WHERE preset_id IS NOT NULL ORDER BY target")
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            let rows = statement
                .query_map([], |row| {
                    let value: String = row.get(0)?;
                    let preset_id: String = row.get(1)?;
                    let target = match value.as_str() {
                        "claude" => PromptTarget::Claude,
                        "codex" => PromptTarget::Codex,
                        "dsh" => PromptTarget::Dsh,
                        other => unreachable!("CHECK 约束保证目标合法: {other}"),
                    };
                    Ok((target, preset_id))
                })
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            rows.collect::<std::result::Result<Vec<_>, _>>().map_err(|error| PromptsError::Store(error.to_string()))
        })
    }
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::store`
Expected: 4 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/prompts/store.rs
git commit -m "feat(prompts): 新增 prompts SQLite 存储层"
```

---

### Task 5: 备份环 `prompts/backup.rs`

**Files:**
- Create: `src-tauri/src/prompts/backup.rs`
- Modify: `src-tauri/src/prompts/mod.rs`(加 `pub mod backup;`)

- [ ] **Step 1: 写失败测试**

`src-tauri/src/prompts/backup.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::{backup_live_file, rotate_backups};

    #[test]
    fn backup_copies_current_content_with_target_directory() {
        let dir = tempfile::tempdir().unwrap();
        let live = dir.path().join("home/.claude/CLAUDE.md");
        std::fs::create_dir_all(live.parent().unwrap()).unwrap();
        std::fs::write(&live, b"current").unwrap();
        let backup_root = dir.path().join("backups/claude");

        let backup_path = backup_live_file(&live, &backup_root).unwrap();

        assert!(backup_path.starts_with(&backup_root));
        assert_eq!(std::fs::read(&backup_path).unwrap(), b"current");
    }

    #[test]
    fn missing_live_file_is_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let live = dir.path().join("home/.claude/CLAUDE.md");
        let backup_root = dir.path().join("backups/claude");
        assert!(backup_live_file(&live, &backup_root).is_none());
    }

    #[test]
    fn rotation_keeps_only_the_latest_ten_backups() {
        let dir = tempfile::tempdir().unwrap();
        let backup_root = dir.path().join("backups/claude");
        std::fs::create_dir_all(&backup_root).unwrap();
        for index in 0..14 {
            std::fs::write(backup_root.join(format!("20260101T00000{index:02}Z-{index:08}.md")), b"x").unwrap();
        }
        rotate_backups(&backup_root, 10).unwrap();
        let mut remaining: Vec<String> = std::fs::read_dir(&backup_root)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        remaining.sort();
        assert_eq!(remaining.len(), 10);
        assert_eq!(remaining[0], "20260101T000004Z-00000004.md");
        assert_eq!(remaining[9], "20260101T0000013Z-0000013.md");
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test --manifest_path src-tauri/Cargo.toml prompts::backup 2>/dev/null || cargo test --manifest-path src-tauri/Cargo.toml prompts::backup`
Expected: 编译失败。

- [ ] **Step 3: 实现**

`src-tauri/src/prompts/backup.rs`(tests 之前):

```rust
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::prompts::model::{PromptsError, Result};

pub const MAX_BACKUPS_PER_TARGET: usize = 10;

/// 备份 live 文件当前内容;文件不存在时返回 Ok(None)(无需备份)。
pub fn backup_live_file(live_path: &Path, backup_root: &Path) -> Result<Option<PathBuf>> {
    let Ok(bytes) = std::fs::read(live_path) else {
        return Ok(None);
    };
    std::fs::create_dir_all(backup_root).map_err(|error| PromptsError::Io(error.to_string()))?;
    let timestamp = chrono::Utc::now().format("%Y%m%dT%H%M%S%.3fZ");
    let mut digest = Sha256::new();
    digest.update(&bytes);
    let name = format!("{timestamp}-{}.md", &hex::encode(digest.finalize())[..8]);
    let destination = backup_root.join(name);
    std::fs::write(&destination, &bytes).map_err(|error| PromptsError::Io(error.to_string()))?;
    rotate_backups(backup_root, MAX_BACKUPS_PER_TARGET)?;
    Ok(Some(destination))
}

pub fn rotate_backups(backup_root: &Path, keep: usize) -> Result<()> {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(backup_root)
        .map_err(|error| PromptsError::Io(error.to_string()))?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .collect();
    entries.sort();
    while entries.len() > keep {
        let oldest = entries.remove(0);
        std::fs::remove_file(&oldest).map_err(|error| PromptsError::Io(error.to_string()))?;
    }
    Ok(())
}
```

`src-tauri/src/prompts/mod.rs` 改为:

```rust
pub mod backup;
pub mod migrations;
pub mod model;
pub mod service;
pub mod store;
pub mod targets;
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::backup`
Expected: 3 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/prompts/backup.rs src-tauri/src/prompts/mod.rs
git commit -m "feat(prompts): 新增 live 文件写前备份环"
```

---

### Task 6: 目标写入器 `prompts/targets.rs`

**Files:**
- Modify: `src-tauri/src/prompts/targets.rs`

- [ ] **Step 1: 写失败测试**

`src-tauri/src/prompts/targets.rs`(tests 之前为空,tests 先行):

```rust
#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use crate::prompts::model::PromptTarget;
    use super::{atomic_write, detect_home, dsh_prompt_path, install_root, live_prompt_path, read_live_prompt, sha256_hex};

    fn home() -> PathBuf {
        let dir = tempfile::tempdir().unwrap();
        dir.keep()
    }

    #[test]
    fn install_root_requires_existing_directory() {
        let root = home();
        assert!(!install_root(PromptTarget::Claude, &root).unwrap().installed);
        std::fs::create_dir_all(root.join(".claude")).unwrap();
        assert!(install_root(PromptTarget::Claude, &root).unwrap().installed);
    }

    #[test]
    fn live_paths_follow_convention() {
        let root = home();
        std::fs::create_dir_all(root.join(".codex")).unwrap();
        let paths = install_root(PromptTarget::Codex, &root).unwrap();
        assert_eq!(paths.prompt_file, root.join(".codex/AGENTS.md"));
        assert_eq!(dsh_prompt_path(&root.join("profiles/p1")), Some(root.join("profiles/p1").join(super::DSH_GLOBAL_PROMPT_FILENAME)));
    }

    #[test]
    fn read_live_reports_missing_and_existing() {
        let root = home();
        std::fs::create_dir_all(root.join(".claude")).unwrap();
        let paths = install_root(PromptTarget::Claude, &root).unwrap();
        assert_eq!(read_live_prompt(&paths.prompt_file).unwrap(), None);
        std::fs::write(&paths.prompt_file, b"hello").unwrap();
        assert_eq!(read_live_prompt(&paths.prompt_file).unwrap(), Some("hello".to_owned()));
    }

    #[test]
    fn atomic_write_replaces_content_and_is_visible_afterwards() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("CLAUDE.md");
        std::fs::write(&target, b"old").unwrap();
        atomic_write(&target, b"new-content").unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), b"new-content");
        let leftovers: Vec<_> = std::fs::read_dir(dir.path()).unwrap().collect::<Result<Vec<_>, _>>().unwrap();
        assert_eq!(leftovers.len(), 1, "不能留下临时文件");
    }

    #[test]
    fn sha256_hex_matches_known_vector() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn detect_home_falls_back_between_envs() {
        // 仅验证不 panic 且在测试机上多数能解析;精确值取决于运行环境。
        let _ = detect_home();
    }
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::targets`
Expected: 编译失败。

- [ ] **Step 3: 实现**

`src-tauri/src/prompts/targets.rs`(tests 之前)。注意:`DSH_GLOBAL_PROMPT_FILENAME` 的值以 Task 1 spike 结论为准,默认 `"AGENTS.md"`:

```rust
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::prompts::model::{PromptTarget, PromptsError, Result};

/// Task 1 spike 结论落点:受管 DeepSeek Harness 的全局提示词文件名。
/// 若 spike 确认 harness 无全局文件机制,把 `dsh_prompt_path` 改为直接返回 None。
pub const DSH_GLOBAL_PROMPT_FILENAME: &str = "AGENTS.md";

#[derive(Clone, Debug)]
pub struct TargetPaths {
    pub installed: bool,
    pub prompt_file: PathBuf,
}

pub fn detect_home() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .filter(|value| !value.is_empty())
        .or_else(|| std::env::var_os("HOME").filter(|value| !value.is_empty()))
        .map(PathBuf::from)
        .filter(|path| path.is_dir())
}

pub fn claude_dir(home: &Path) -> PathBuf {
    home.join(".claude")
}

pub fn codex_dir(home: &Path) -> PathBuf {
    home.join(".codex")
}

pub fn dsh_prompt_path(profile_data_root: &Path) -> Option<PathBuf> {
    Some(profile_data_root.join(DSH_GLOBAL_PROMPT_FILENAME))
}

pub fn install_root(target: PromptTarget, home: &Path) -> Result<TargetPaths> {
    let directory = match target {
        PromptTarget::Claude => claude_dir(home),
        PromptTarget::Codex => codex_dir(home),
        PromptTarget::Dsh => return Err(PromptsError::InvalidInput("DSH 目标须用 dsh_prompt_path".into())),
    };
    let installed = directory.is_dir();
    Ok(TargetPaths { installed, prompt_file: directory.join(target_filename(target)) })
}

fn target_filename(target: PromptTarget) -> &'static str {
    match target {
        PromptTarget::Claude => "CLAUDE.md",
        PromptTarget::Codex => "AGENTS.md",
        PromptTarget::Dsh => DSH_GLOBAL_PROMPT_FILENAME,
    }
}

/// live 文件内容;None = 文件不存在。读取失败向上报错(由调用方决定是否跳过回填)。
pub fn read_live_prompt(path: &Path) -> Result<Option<String>> {
    match std::fs::read(path) {
        Ok(bytes) => Ok(Some(String::from_utf8(bytes).map_err(|error| PromptsError::Io(error.to_string()))?)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(PromptsError::Io(error.to_string())),
    }
}

/// temp 写 + 同卷 rename 原子替换(std::fs::rename 在 Windows 上替换已存在目标)。
pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().ok_or_else(|| PromptsError::Io("目标路径无父目录".into()))?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name().and_then(|name| name.to_str()).unwrap_or("prompt"),
        uuid::Uuid::new_v4()
    ));
    std::fs::write(&temporary, bytes).map_err(|error| PromptsError::Io(error.to_string()))?;
    let file = std::fs::File::open(&temporary).map_err(|error| PromptsError::Io(error.to_string()))?;
    file.sync_all().map_err(|error| PromptsError::Io(error.to_string()))?;
    drop(file);
    std::fs::rename(&temporary, path).map_err(|error| {
        let _ = std::fs::remove_file(&temporary);
        PromptsError::Io(error.to_string())
    })
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(bytes);
    hex::encode(digest.finalize())
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::targets`
Expected: 6 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/prompts/targets.rs
git commit -m "feat(prompts): 新增目标路径解析与原子写入器"
```

---

### Task 7: 服务层 · CRUD 与重投影 `prompts/service.rs`(上)

**Files:**
- Modify: `src-tauri/src/prompts/service.rs`

- [ ] **Step 1: 写失败测试(CRUD 基线)**

`src-tauri/src/prompts/service.rs`:

```rust
#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use crate::prompts::model::{PromptTarget, PromptsError, MAX_PROMPT_BYTES};
    use crate::prompts::service::PromptsService;

    /// 测试环境:隔离 home(仅建 .claude/.codex 目录)+ 独立 db + 独立 profile 根。
    struct Env {
        _dir: tempfile::TempDir,
        home: PathBuf,
        profiles: PathBuf,
        db: PathBuf,
        backups: PathBuf,
    }

    fn env() -> Env {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("home");
        std::fs::create_dir_all(home.join(".claude")).unwrap();
        std::fs::create_dir_all(home.join(".codex")).unwrap();
        let profiles = dir.path().join("profiles");
        std::fs::create_dir_all(&profiles).unwrap();
        let env = Env {
            home,
            profiles,
            db: dir.path().join("state/prompts.db"),
            backups: dir.path().join("backups"),
            _dir: dir,
        };
        // DSH profile 数据根(空目录即可,无 profile 时 DSH 目标未安装)
        std::fs::create_dir_all(env.profiles.join("p-default")).unwrap();
        env
    }

    fn service(env: &Env) -> PromptsService {
        PromptsService::open_with_env(&env.home, &env.profiles, &env.db, &env.backups).unwrap()
    }

    #[test]
    fn save_create_update_and_delete_roundtrip() {
        let env = env();
        let service = service(&env);
        let outcome = service.save(None, "第一版", "内容 A").unwrap();
        let SaveOutcomeShape!(preset) = outcome else { panic!("期望 saved") };
        // ↑ 见下方宏说明:测试辅助解构,实际写法在 Step 1 末尾给出
        assert!(!preset.id.is_empty());
        let saved = service.list().unwrap();
        assert_eq!(saved.len(), 1);
        let outcome = service.save(Some(&preset.id), "第二版", "内容 B").unwrap();
        let saved = match outcome {
            crate::prompts::model::SaveOutcome::Saved { preset, .. } => preset,
            _ => panic!("期望 saved"),
        };
        assert_eq!(saved.title, "第二版");
        service.delete(&preset.id).unwrap();
        assert!(service.list().unwrap().is_empty());
    }

    #[test]
    fn delete_is_rejected_while_any_target_activates_the_preset() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "A", "C").unwrap() {
            crate::prompts::model::SaveOutcome::Saved { preset, .. } => preset,
            _ => panic!(),
        };
        service.activate(&saved.id, PromptTarget::Claude).unwrap();
        let error = service.delete(&saved.id).unwrap_err();
        assert!(matches!(error, PromptsError::PresetActive(_)));
    }

    #[test]
    fn oversized_content_is_rejected() {
        let env = env();
        let service = service(&env);
        let content = "x".repeat(MAX_PROMPT_BYTES + 1);
        let error = service.save(None, "too big", &content).unwrap_err();
        assert!(matches!(error, PromptsError::TooLarge));
    }

    #[test]
    fn empty_title_is_rejected() {
        let env = env();
        let service = service(&env);
        assert!(service.save(None, "   ", "c").is_err());
    }
}
```

> 注:上面第一处 `SaveOutcomeShape!` 宏是占位写法,请直接改成与后续断言一致的 match:
> `let crate::prompts::model::SaveOutcome::Saved { preset, .. } = outcome else { panic!("期望 saved") };`

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::service`
Expected: 编译失败。

- [ ] **Step 3: 实现服务骨架与 CRUD**

`src-tauri/src/prompts/service.rs`(tests 之前):

```rust
use std::path::{Path, PathBuf};

use crate::prompts::backup::backup_live_file;
use crate::prompts::model::{
    ActivateOutcome, ConflictCandidate, PresetSummary, PromptPreset, PromptTarget, PromptsError,
    Result, SaveOutcome, TargetStatus, MAX_PROMPT_BYTES,
};
use crate::prompts::store::PromptsStore;
use crate::prompts::targets;

pub struct PromptsService {
    store: PromptsStore,
    backup_root: PathBuf,
    home: Option<PathBuf>,
    profiles_root: PathBuf,
}

impl PromptsService {
    pub fn open(paths: &crate::storage::app_paths::AppPaths) -> Result<Self> {
        Self::open_with_env(
            &targets::detect_home().unwrap_or_default(),
            &paths.profiles,
            &paths.state.join("prompts.db"),
            &paths.backups.join("prompts"),
        )
    }

    pub fn open_with_env(
        home: &Path,
        profiles_root: &Path,
        database_path: &Path,
        backup_root: &Path,
    ) -> Result<Self> {
        Ok(Self {
            store: PromptsStore::open(database_path)?,
            backup_root: backup_root.to_path_buf(),
            home: if home.as_os_str().is_empty() { None } else { Some(home.to_path_buf()) },
            profiles_root: profiles_root.to_path_buf(),
        })
    }

    fn now_ms() -> i64 {
        chrono::Utc::now().timestamp_millis()
    }

    fn validate_content(content: &str) -> Result<()> {
        if content.len() > MAX_PROMPT_BYTES {
            return Err(PromptsError::TooLarge);
        }
        Ok(())
    }

    fn validate_title(title: &str) -> Result<()> {
        let trimmed = title.trim();
        if trimmed.is_empty() || trimmed.len() > 200 {
            return Err(PromptsError::InvalidInput("标题须为 1-200 字符".into()));
        }
        Ok(())
    }

    pub fn list(&self) -> Result<Vec<PresetSummary>> {
        let mut summaries = Vec::new();
        for preset in self.store.list_presets()? {
            summaries.push(PresetSummary {
                id: preset.id,
                title: preset.title,
                updated_at: preset.updated_at,
                activated_targets: self.store.activated_targets(preset.id.as_str())?,
            });
        }
        Ok(summaries)
    }

    pub fn get(&self, preset_id: &str) -> Result<PromptPreset> {
        self.store
            .get_preset(preset_id)?
            .ok_or_else(|| PromptsError::InvalidInput(format!("预设不存在: {preset_id}")))
    }

    pub fn save(&self, preset_id: Option<&str>, title: &str, content: &str) -> Result<SaveOutcome> {
        self.validate_title(title)?;
        self.validate_content(content)?;
        match preset_id {
            None => {
                let id = uuid::Uuid::new_v4().to_string();
                let now = Self::now_ms();
                self.store.insert_preset(&id, title.trim(), content, now, now)?;
                let preset = PromptPreset { id, title: title.trim().to_owned(), content: content.to_owned(), created_at: now, updated_at: now };
                let projected = self.project_active_targets(&preset)?;
                Ok(SaveOutcome::Saved { preset, projected })
            }
            Some(existing_id) => {
                let existing = self.get(existing_id)?;
                let merged_content = self.backfill_merged_content(existing_id, &existing.content)?;
                let effective = if merged_content == content { content.to_owned() } else { content.to_owned() };
                let now = Self::now_ms();
                self.store.update_preset(existing_id, title.trim(), &effective, now)?;
                let preset = PromptPreset { id: existing.id, title: title.trim().to_owned(), content: effective, created_at: existing.created_at, updated_at: now };
                let projected = self.project_active_targets(&preset)?;
                Ok(SaveOutcome::Saved { preset, projected })
            }
        }
    }

    pub fn delete(&self, preset_id: &str) -> Result<()> {
        if !self.store.activated_targets(preset_id)?.is_empty() {
            return Err(PromptsError::PresetActive(preset_id.to_owned()));
        }
        self.store.delete_preset(preset_id)
    }

    /// 把预设内容写入所有激活它的目标(先备份,再原子写)。
    /// 单目标且内容被外部改动时先返回冲突;多目标分歧时同样返回冲突(spec §5)。
    fn project_active_targets(&self, preset: &PromptPreset) -> Result<Vec<TargetStatus>> {
        let activated = self.store.activated_targets(&preset.id)?;
        let mut projected = Vec::new();
        if activated.is_empty() {
            return Ok(projected);
        }
        // 冲突检测:存在任一激活目标的 live 内容既 ≠ DB 内容又 ≠ 新内容 → 回填冲突
        let mut candidates = Vec::new();
        for target in &activated {
            let Some(live) = self.live_content(*target)? else { continue };
            if live != preset.content {
                candidates.push(ConflictCandidate { target: *target, content: live, updated_at: Self::now_ms() });
            }
        }
        if !candidates.is_empty() {
            return Err(PromptsError::Io("__conflict__".into())); // 由 write_project_with_conflict 包装,见下
        }
        for target in &activated {
            self.write_target(*target, &preset.content)?;
            projected.push(self.status_of(*target)?);
        }
        Ok(projected)
    }

    /// 供 save 使用:检测冲突后抛专用载荷。真实错误通道见 `conflict_of`。
    fn backfill_merged_content(&self, preset_id: &str, fallback: &str) -> Result<String> {
        Ok(fallback.to_owned())
    }

    fn live_content(&self, target: PromptTarget) -> Result<Option<String>> {
        Ok(match self.prompt_file_for(target)? {
            Some(path) => targets::read_live_prompt(&path)?,
            None => None,
        })
    }

    pub(crate) fn prompt_file_for(&self, target: PromptTarget) -> Result<Option<PathBuf>> {
        match target {
            PromptTarget::Claude | PromptTarget::Codex => {
                let Some(home) = &self.home else { return Ok(None) };
                Ok(Some(targets::install_root(target, home)?.prompt_file).filter(|_| targets::install_root(target, home)?.installed))
            }
            PromptTarget::Dsh => Ok(self.active_profile_data_root().and_then(|root| targets::dsh_prompt_path(&root)).filter(|path| path.parent().is_some_and(|parent| parent.is_dir()))),
        }
    }

    fn active_profile_data_root(&self) -> Option<PathBuf> {
        let repository = crate::profile::repository::ProfileRepository::open_read_only(self.profiles_root.clone()).ok()?;
        let snapshot = repository.snapshot().ok()?;
        let profile_id = snapshot.selected_profile_id.or_else(|| snapshot.pending_profile_id)?;
        repository.get(&profile_id).ok().map(|record| record.data_root).ok()
    }

    fn write_target(&self, target: PromptTarget, content: &str) -> Result<()> {
        let Some(path) = self.prompt_file_for(target)? else {
            return Err(PromptsError::TargetNotInstalled(target));
        };
        backup_live_file(&path, &self.backup_root.join(target.as_str()))?;
        targets::atomic_write(&path, content.as_bytes())
    }

    pub(crate) fn status_of(&self, target: PromptTarget) -> Result<TargetStatus> {
        // Task 9 填充
        unimplemented!("Task 9")
    }
}
```

> **重要修正(执行时以此为准):** `project_active_targets` 里用 `PromptsError::Io("__conflict__")` 传递冲突是**错误设计**。Rust 错误不能承载结构化冲突载荷,把 `SaveOutcome`/`ActivateOutcome` 的冲突分支改为在 `Result` 之上再包一层枚举:

```rust
pub enum Flow<T> {
    Done(T),
    Conflict { preset_id: String, candidates: Vec<ConflictCandidate> },
}
```

`save` / `activate` / `apply_save_resolution` 的返回类型改为 `Result<Flow<SaveOutcome>>` 等;`commands.rs` 把 `Flow::Conflict` 映射为 serde 标签 `{ "kind": "backfill-conflict", ... }`(Task 10 给出最终签名)。测试 Step 1 中的匹配随之改为 `Flow::Done(SaveOutcome::Saved { .. })`。**删除** `backfill_merged_content` 占位(其真实逻辑在 Task 8 的 `detect_backfill` 中实现)。

- [ ] **Step 4: 跑测试验证通过**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::service`
Expected: 4 个测试 PASS(CRUD 部分;激活相关测试在 Task 8 扩充)。

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/prompts/service.rs
git commit -m "feat(prompts): 服务层 CRUD 与目标重投影骨架"
```

---

### Task 8: 服务层 · 回填保护与激活/停用 `prompts/service.rs`(下)

**Files:**
- Modify: `src-tauri/src/prompts/service.rs`

- [ ] **Step 1: 写失败测试(回填矩阵)**

在 `tests` 模块内追加:

```rust
use crate::prompts::model::{Flow, SaveOutcome};

fn write_live(env: &Env, target: PromptTarget, content: &str) {
    let service = service(env);
    let path = service.prompt_file_for(target).unwrap().unwrap();
    std::fs::write(path, content).unwrap();
}

fn read_live(env: &Env, target: PromptTarget) -> Option<String> {
    let service = service(env);
    let path = service.prompt_file_for(target).unwrap().unwrap();
    std::fs::read_to_string(path).ok()
}

#[test]
fn activate_writes_file_and_records_activation_with_backup() {
    let env = env();
    let service = service(&env);
    write_live(&env, PromptTarget::Claude, "外部原有内容");
    let saved = match service.save(None, "P", "新内容").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    match service.activate(&saved.id, PromptTarget::Claude).unwrap() {
        Flow::Done(crate::prompts::model::ActivateOutcome::Ok { .. }) => {}
        _ => panic!("单目标、内容一致时不该有冲突"),
    }
    assert_eq!(read_live(&env, PromptTarget::Claude).unwrap(), "新内容");
    assert_eq!(
        service.store.active_preset_id(PromptTarget::Claude).unwrap().as_deref(),
        Some(saved.id.as_str())
    );
    let backups = std::fs::read_dir(env.backups.join("claude")).unwrap().count();
    assert_eq!(backups, 1, "激活前必须留一份外部内容备份");
}

#[test]
fn activate_with_external_edit_single_target_backfills_then_writes() {
    let env = env();
    let service = service(&env);
    let saved = match service.save(None, "P", "v1").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    service.activate(&saved.id, PromptTarget::Claude).unwrap();
    write_live(&env, PromptTarget::Claude, "外部修改");

    // save 触发回填:外部修改进入 DB,再投影
    match service.save(Some(&saved.id), "P", "外部修改\n追加").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => {
            assert_eq!(preset.content, "外部修改\n追加");
            assert_eq!(read_live(&env, PromptTarget::Claude).unwrap(), "外部修改\n追加");
        }
        _ => panic!("单目标静默回填,不该冲突"),
    }
}

#[test]
fn save_with_divergent_multi_target_live_content_returns_conflict() {
    let env = env();
    let service = service(&env);
    let saved = match service.save(None, "P", "v1").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    service.activate(&saved.id, PromptTarget::Claude).unwrap();
    service.activate(&saved.id, PromptTarget::Codex).unwrap();
    write_live(&env, PromptTarget::Claude, "claude 端外部修改");
    write_live(&env, PromptTarget::Codex, "codex 端外部修改");

    let conflict = match service.save(Some(&saved.id), "P", "v2").unwrap() {
        Flow::Conflict { preset_id, candidates } => (preset_id, candidates),
        _ => panic!("多目标分歧必须报冲突"),
    };
    assert_eq!(conflict.0, saved.id);
    assert_eq!(conflict.1.len(), 2);

    // 用户选择以 claude 端为准 → resolve 后写入并重投影
    match service.resolve_save_conflict(&saved.id, "P", &conflict.1[0].content, Some(conflict.1[0].target)).unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, projected }) => {
            assert_eq!(preset.content, "claude 端外部修改");
            assert_eq!(projected.len(), 2);
            assert_eq!(read_live(&env, PromptTarget::Codex).unwrap(), "claude 端外部修改");
        }
        _ => panic!(),
    }
}

#[test]
fn activate_without_activation_creates_backup_preset() {
    let env = env();
    let service = service(&env);
    write_live(&env, PromptTarget::Codex, "用户手写内容");
    let saved = match service.save(None, "P", "v1").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    service.activate(&saved.id, PromptTarget::Codex).unwrap();
    let titles: Vec<String> = service.list().unwrap().into_iter().map(|summary| summary.title).collect();
    assert!(titles.iter().any(|title| title.starts_with("backup-")), "无激活项时外部内容须落为备份预设");
}

#[test]
fn activate_skips_uninstalled_target() {
    let env = env();
    let service = service(&env);
    let saved = match service.save(None, "P", "v1").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    let error = service.activate(&saved.id, PromptTarget::Dsh).unwrap_err();
    assert!(matches!(error, PromptsError::TargetNotInstalled(PromptTarget::Dsh)));
}

#[test]
fn deactivate_clears_file_and_record() {
    let env = env();
    let service = service(&env);
    let saved = match service.save(None, "P", "v1").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    service.activate(&saved.id, PromptTarget::Claude).unwrap();
    service.deactivate(PromptTarget::Claude).unwrap();
    assert_eq!(read_live(&env, PromptTarget::Claude).unwrap(), Some(String::new()));
    assert_eq!(service.store.active_preset_id(PromptTarget::Claude).unwrap(), None);
}

#[test]
fn switching_activation_replaces_previous_preset_atomically() {
    let env = env();
    let service = service(&env);
    let first = match service.save(None, "A", "a").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    let second = match service.save(None, "B", "b").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    service.activate(&first.id, PromptTarget::Claude).unwrap();
    service.activate(&second.id, PromptTarget::Claude).unwrap();
    assert_eq!(service.store.active_preset_id(PromptTarget::Claude).unwrap().as_deref(), Some(second.id.as_str()));
    assert!(service.store.activated_targets(&first.id).unwrap().is_empty());
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::service`
Expected: 编译失败(`Flow`、`activate`、`deactivate`、`resolve_save_conflict` 未定义)。

- [ ] **Step 3: 实现**

`src-tauri/src/prompts/model.rs` 追加:

```rust
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "resolution", rename_all = "kebab-case")]
pub enum Flow<T> {
    Done(T),
    Conflict { preset_id: String, candidates: Vec<ConflictCandidate> },
}
```

`src-tauri/src/prompts/service.rs` 内:把 Task 7 的 `save` 与 `project_active_targets` 改写为 `Flow` 语义(删除 `PromptsError::Io("__conflict__")` 与 `backfill_merged_content` 占位),并新增:

```rust
impl PromptsService {
    /// 回填检测(spec §5):
    /// - 预设无激活目标 → Ok(None)(无回填诉求)
    /// - 恰一个激活目标且 live ≠ DB → Ok(Some(live))(静默回填源)
    /// - ≥2 个激活目标且 live 内容分歧(≠ DB 且互不相等)→ Err(candidates) 冲突
    /// - 其余(全为空 / 与 DB 一致)→ Ok(None)
    fn detect_backfill(&self, preset_id: &str, db_content: &str) -> std::result::Result<Option<String>, Vec<ConflictCandidate>> {
        let activated = self.store.activated_targets(preset_id).map_err(|_| Vec::new())?;
        let mut candidates = Vec::new();
        for target in activated {
            let Some(Ok(live)) = self.live_content(target).map(Some).unwrap_or_else(|error| Err(error)).ok().map(|value| value).map(Ok) else { continue };
            let _ = &live;
            if live != db_content {
                candidates.push(ConflictCandidate { target, content: live, updated_at: Self::now_ms() });
            }
        }
        if candidates.is_empty() {
            return Ok(None);
        }
        if candidates.len() == 1 {
            return Ok(Some(candidates.remove(0).content));
        }
        Err(candidates)
    }

    pub fn save(&self, preset_id: Option<&str>, title: &str, content: &str) -> Result<Flow<SaveOutcome>> {
        self.validate_title(title)?;
        self.validate_content(content)?;
        let now = Self::now_ms();
        let (id, merged_content) = match preset_id {
            None => (uuid::Uuid::new_v4().to_string(), content.to_owned()),
            Some(existing_id) => {
                let existing = self.get(existing_id)?;
                let merged = match self.detect_backfill(existing_id, &existing.content) {
                    Ok(Some(live)) => live,
                    Ok(None) => content.to_owned(),
                    Err(candidates) => return Ok(Flow::Conflict { preset_id: existing_id.to_owned(), candidates }),
                };
                (existing_id.to_owned(), merged)
            }
        };
        self.validate_content(&merged_content)?;
        let content_to_store = if preset_id.is_none() { content.to_owned() } else { content.to_owned() };
        let stored = if preset_id.is_none() {
            self.store.insert_preset(&id, title.trim(), &content_to_store, now, now)?;
            PromptPreset { id, title: title.trim().to_owned(), content: content_to_store, created_at: now, updated_at: now }
        } else {
            self.store.update_preset(&id, title.trim(), &content_to_store, now)?;
            PromptPreset { id, title: title.trim().to_owned(), content: content_to_store, created_at: now, updated_at: now }
        };
        let projected = match self.project_active_targets(&stored)? {
            Flow::Done(projected) => projected,
            Flow::Conflict { candidates, .. } => return Ok(Flow::Conflict { preset_id: stored.id.clone(), candidates }),
        };
        Ok(Flow::Done(SaveOutcome::Saved { preset: stored, projected }))
    }

    /// 冲突解决后的人工落点:以用户选定内容保存并重投影。
    pub fn resolve_save_conflict(
        &self,
        preset_id: &str,
        title: &str,
        content: &str,
        chosen_target: Option<PromptTarget>,
    ) -> Result<Flow<SaveOutcome>> {
        let _ = chosen_target; // 选择即内容,已由调用方传入候选内容
        self.save_with_forced_content(preset_id, title, content)
    }

    fn save_with_forced_content(&self, preset_id: &str, title: &str, content: &str) -> Result<Flow<SaveOutcome>> {
        self.validate_title(title)?;
        self.validate_content(content)?;
        let existing = self.get(preset_id)?;
        let now = Self::now_ms();
        self.store.update_preset(preset_id, title.trim(), content, now)?;
        let stored = PromptPreset { id: existing.id, title: title.trim().to_owned(), content: content.to_owned(), created_at: existing.created_at, updated_at: now };
        let projected = match self.project_active_targets(&stored)? {
            Flow::Done(projected) => projected,
            Flow::Conflict { candidates, .. } => return Ok(Flow::Conflict { preset_id: stored.id.clone(), candidates }),
        };
        Ok(Flow::Done(SaveOutcome::Saved { preset: stored, projected }))
    }

    pub fn activate(&self, preset_id: &str, target: PromptTarget) -> Result<Flow<ActivateOutcome>> {
        let preset = self.get(preset_id)?;
        let Some(path) = self.prompt_file_for(target)? else {
            return Err(PromptsError::TargetNotInstalled(target));
        };
        // ① 回填:无激活项且 live 非空 → 备份预设;有激活项且外部改动 → 回写 DB
        let live = targets::read_live_prompt(&path)?;
        let previous_active = self.store.active_preset_id(target)?;
        if let Some(live_text) = live.filter(|text| !text.is_empty()) {
            match previous_active {
                None => {
                    let now = Self::now_ms();
                    let backup_id = uuid::Uuid::new_v4().to_string();
                    self.store.insert_preset(&backup_id, &format!("backup-{}", now), &live_text, now, now)?;
                }
                Some(previous_id) => {
                    if live_text != preset.content {
                        let previous = self.store.get_preset(&previous_id)?;
                        if let Some(previous) = previous.filter(|previous| previous.id == preset.id) {
                            self.store.update_preset(&previous.id, &previous.title, &live_text, Self::now_ms())?;
                        }
                    }
                }
            }
        }
        // ② 备份 → ③ 原子写 → ④ 落激活
        backup_live_file(&path, &self.backup_root.join(target.as_str()))?;
        targets::atomic_write(&path, preset.content.as_bytes())?;
        self.store.set_activation(target, preset_id, Self::now_ms())?;
        Ok(Flow::Done(ActivateOutcome::Ok { status: self.status_of(target)? }))
    }

    pub fn deactivate(&self, target: PromptTarget) -> Result<TargetStatus> {
        if let Some(path) = self.prompt_file_for(target)? {
            backup_live_file(&path, &self.backup_root.join(target.as_str()))?;
            targets::atomic_write(&path, b"")?;
        }
        self.store.clear_activation(target)?;
        self.status_of(target)
    }
}
```

同时把 `project_active_targets` 改为返回 `Result<Flow<Vec<TargetStatus>>>`(冲突时返回 `Flow::Conflict`),`save` 相应解包;`write_target`、`live_content`、`prompt_file_for`、`active_profile_data_root` 保持 Task 7 实现。

- [ ] **Step 4: 跑测试验证通过**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::service`
Expected: 全部 service 测试 PASS(含 Task 7 的 4 个)。

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/prompts/service.rs src-tauri/src/prompts/model.rs
git commit -m "feat(prompts): 回填保护与激活停用语义"
```

---

### Task 9: 服务层 · status 与导入 `prompts/service.rs`(收尾)

**Files:**
- Modify: `src-tauri/src/prompts/service.rs`

- [ ] **Step 1: 写失败测试**

`tests` 模块内追加:

```rust
#[test]
fn status_reports_installed_hash_and_mismatch() {
    let env = env();
    let service = service(&env);
    let saved = match service.save(None, "P", "v1").unwrap() {
        Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
        _ => panic!(),
    };
    service.activate(&saved.id, PromptTarget::Claude).unwrap();
    let statuses = service.status().unwrap();
    let claude = statuses.iter().find(|status| status.target == PromptTarget::Claude).unwrap();
    assert!(claude.installed && claude.live_file_exists && claude.matches_active_preset);
    write_live(&env, PromptTarget::Claude, "外部改了");
    let statuses = service.status().unwrap();
    let claude = statuses.iter().find(|status| status.target == PromptTarget::Claude).unwrap();
    assert!(!claude.matches_active_preset);
    let codex = statuses.iter().find(|status| status.target == PromptTarget::Codex).unwrap();
    assert!(codex.installed && !codex.live_file_exists && codex.active_preset_id.is_none());
}

#[test]
fn status_marks_oversized_live_file() {
    let env = env();
    let service = service(&env);
    write_live(&env, PromptTarget::Claude, &"x".repeat(MAX_PROMPT_BYTES + 1));
    let statuses = service.status().unwrap();
    let claude = statuses.iter().find(|status| status.target == PromptTarget::Claude).unwrap();
    assert!(claude.oversized);
}

#[test]
fn import_pulls_live_files_into_presets_and_activates() {
    let env = env();
    let service = service(&env);
    write_live(&env, PromptTarget::Claude, "claude 既有提示词");
    write_live(&env, PromptTarget::Codex, "codex 既有提示词");
    let imported = service.import(&[PromptTarget::Claude, PromptTarget::Codex]).unwrap();
    assert_eq!(imported.len(), 2);
    assert_eq!(service.store.active_preset_id(PromptTarget::Claude).unwrap().as_deref(), Some(imported[0].id.as_str()));
    assert_eq!(read_live(&env, PromptTarget::Claude).unwrap(), "claude 既有提示词");
}

#[test]
fn import_skips_empty_or_missing_files() {
    let env = env();
    let service = service(&env);
    let imported = service.import(&[PromptTarget::Claude]).unwrap();
    assert!(imported.is_empty());
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts::service`
Expected: 编译失败(`status`、`import` 未实现——Task 7 里 `status_of` 是 `unimplemented!`)。

- [ ] **Step 3: 实现**

替换 Task 7 的 `status_of` 占位并新增:

```rust
impl PromptsService {
    pub(crate) fn status_of(&self, target: PromptTarget) -> Result<TargetStatus> {
        let Some(path) = self.prompt_file_for(target)? else {
            return Ok(TargetStatus {
                target,
                installed: false,
                live_file_exists: false,
                active_preset_id: self.store.active_preset_id(target)?,
                live_content_sha256: None,
                matches_active_preset: false,
                oversized: false,
            });
        };
        let live = targets::read_live_prompt(&path)?;
        let live_hash = live.as_ref().map(|text| targets::sha256_hex(text.as_bytes()));
        let oversized = live.as_ref().is_some_and(|text| text.len() > MAX_PROMPT_BYTES);
        let active_preset_id = self.store.active_preset_id(target)?;
        let matches = match (&active_preset_id, &live) {
            (Some(preset_id), Some(text)) => self
                .store
                .get_preset(preset_id)?
                .is_some_and(|preset| preset.content == *text),
            _ => false,
        };
        Ok(TargetStatus {
            target,
            installed: true,
            live_file_exists: live.is_some(),
            active_preset_id,
            live_content_sha256: live_hash,
            matches_active_preset: matches,
            oversized,
        })
    }

    pub fn status(&self) -> Result<Vec<TargetStatus>> {
        PromptTarget::ALL.iter().map(|target| self.status_of(*target)).collect()
    }

    pub fn import(&self, targets_to_import: &[PromptTarget]) -> Result<Vec<PresetSummary>> {
        let mut imported = Vec::new();
        for target in targets_to_import.iter().copied() {
            let Some(path) = self.prompt_file_for(target)? else { continue };
            let Some(content) = targets::read_live_prompt(&path)?.filter(|text| !text.is_empty()) else { continue };
            if content.len() > MAX_PROMPT_BYTES { continue; }
            let id = uuid::Uuid::new_v4().to_string();
            let now = Self::now_ms();
            let title = format!("导入-{}/{}", target.as_str(), chrono::Utc::now().format("%m%d %H:%M"));
            self.store.insert_preset(&id, &title, &content, now, now)?;
            self.store.set_activation(target, &id, now)?;
            imported.push(PresetSummary { id, title, updated_at: now, activated_targets: vec![target] });
        }
        Ok(imported)
    }
}
```

- [ ] **Step 4: 跑全部 Rust 测试**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/prompts/service.rs
git commit -m "feat(prompts): 状态查询与首启导入"
```

---

### Task 10: Tauri 命令与注册

**Files:**
- Modify: `src-tauri/src/commands.rs`(文件尾部追加 8 个命令)
- Modify: `src-tauri/src/lib.rs`(`renderer_commands!` 清单 + setup 里 manage)

- [ ] **Step 1: 写失败测试(注册断言)**

`src-tauri/src/lib.rs` 的 `renderer_command_tests` 模块内追加:

```rust
    #[test]
    fn prompts_commands_are_registered() {
        for name in [
            "commands::prompts_list",
            "commands::prompts_get",
            "commands::prompts_save",
            "commands::prompts_delete",
            "commands::prompts_activate",
            "commands::prompts_deactivate",
            "commands::prompts_status",
            "commands::prompts_import",
        ] {
            assert!(super::RENDERER_COMMAND_NAMES.contains(&name), "缺少 {name}");
        }
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts_commands_are_registered`
Expected: FAIL(尚未注册)。

- [ ] **Step 3: 实现命令**

`src-tauri/src/commands.rs` 尾部追加(`use` 区按现有文件风格补 `use tauri::State;` 等已有项):

```rust
use crate::prompts::model::{Flow, PromptTarget};

fn parse_prompt_target(value: &str) -> Result<PromptTarget, String> {
    match value {
        "claude" => Ok(PromptTarget::Claude),
        "codex" => Ok(PromptTarget::Codex),
        "dsh" => Ok(PromptTarget::Dsh),
        other => Err(format!("未知提示词目标: {other}")),
    }
}

#[tauri::command]
pub async fn prompts_list(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<crate::prompts::model::PresetSummary>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.list().map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_get(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: String,
) -> Result<crate::prompts::model::PromptPreset, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.get(&preset_id).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_save(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: Option<String>,
    title: String,
    content: String,
) -> Result<serde_json::Value, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let flow = service.save(preset_id.as_deref(), &title, &content).map_err(|error| error.to_string())?;
    flow_to_value(flow, |saved| serde_json::to_value(saved).map_err(|error| error.to_string()))
}

#[tauri::command]
pub async fn prompts_delete(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: String,
) -> Result<(), String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.delete(&preset_id).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_activate(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: String,
    target: String,
) -> Result<serde_json::Value, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let target = parse_prompt_target(&target)?;
    let flow = service.activate(&preset_id, target).map_err(|error| error.to_string())?;
    flow_to_value(flow, |outcome| serde_json::to_value(outcome).map_err(|error| error.to_string()))
}

#[tauri::command]
pub async fn prompts_deactivate(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    target: String,
) -> Result<crate::prompts::model::TargetStatus, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let target = parse_prompt_target(&target)?;
    service.deactivate(target).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<crate::prompts::model::TargetStatus>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.status().map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_import(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    targets: Vec<String>,
) -> Result<Vec<crate::prompts::model::PresetSummary>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let parsed = targets.iter().map(|value| parse_prompt_target(value)).collect::<std::result::Result<Vec<_>, _>>()?;
    service.import(&parsed).map_err(|error| error.to_string())
}

fn flow_to_value<T>(
    flow: Flow<T>,
    map_done: impl FnOnce(T) -> Result<serde_json::Value, String>,
) -> Result<serde_json::Value, String> {
    match flow {
        Flow::Done(done) => map_done(done),
        Flow::Conflict { preset_id, candidates } => serde_json::to_value(
            crate::prompts::model::SaveOutcome::BackfillConflict { preset_id, candidates },
        )
        .map_err(|error| error.to_string()),
    }
}
```

- [ ] **Step 4: 注册命令 + manage 服务**

`src-tauri/src/lib.rs`:

1. `renderer_commands!` 清单中(`commands::list_profiles,` 之后)追加 8 行:

```rust
            commands::prompts_list,
            commands::prompts_get,
            commands::prompts_save,
            commands::prompts_delete,
            commands::prompts_activate,
            commands::prompts_deactivate,
            commands::prompts_status,
            commands::prompts_import,
```

2. setup 中(`app.manage(Arc::new(plugin_market::PluginMarketState::new()));` 之后):

```rust
            let prompts_service = prompts::service::PromptsService::open(&foundation.paths)
                .map_err(|cause| Box::<dyn std::error::Error>::from(cause.to_string()))?;
            app.manage(Arc::new(prompts_service));
```

> 注意:`foundation` 在 setup 中的可用时点与现有 `RuntimePaths::from_app_paths(&foundation.paths)`(lib.rs:444)一致;若 `foundation` 此处尚未构造,则放到其构造之后、`runtime_services` 之前。

- [ ] **Step 5: 跑测试与编译验证**

Run: `cargo test --manifest-path src-tauri/Cargo.toml prompts_commands_are_registered && cargo check --manifest-path src-tauri/Cargo.toml`
Expected: 测试 PASS;`cargo check` 无错误。

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src/commands.rs src-tauri/src/lib.rs
git commit -m "feat(prompts): 暴露 8 个渲染器命令"
```

---

### Task 11: 宿主桥契约 `src/bridge-contract.ts`

**Files:**
- Modify: `src/bridge-contract.ts`
- Test: `src/bridge-contract.test.ts`(追加用例)

- [ ] **Step 1: 写失败测试**

`src/bridge-contract.test.ts` 追加:

```ts
describe('prompts v2 actions', () => {
  it('accepts prompts.save with bounded title and content', () => {
    expect(isVersionedBridgePayload('prompts.save', { presetId: undefined, title: '标题', content: '正文' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.save', { presetId: 'p1', title: '标题', content: 'x'.repeat(24 * 1024) })).toBe(true)
    expect(isVersionedBridgePayload('prompts.save', { title: '标题', content: 'x'.repeat(24 * 1024 + 1) })).toBe(false)
    expect(isVersionedBridgePayload('prompts.save', { title: '', content: '正文' })).toBe(false)
    expect(isVersionedBridgePayload('prompts.save', { title: '标题', content: '正文', extra: 1 })).toBe(false)
  })

  it('accepts prompts.activate/deactivate with known targets only', () => {
    expect(isVersionedBridgePayload('prompts.activate', { presetId: 'p1', target: 'claude' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.activate', { presetId: 'p1', target: 'gemini' })).toBe(false)
    expect(isVersionedBridgePayload('prompts.deactivate', { target: 'dsh' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.deactivate', {})).toBe(false)
  })

  it('accepts prompts.import with a deduplicated target list', () => {
    expect(isVersionedBridgePayload('prompts.import', { targets: ['claude', 'codex'] })).toBe(true)
    expect(isVersionedBridgePayload('prompts.import', { targets: ['claude', 'claude'] })).toBe(false)
    expect(isVersionedBridgePayload('prompts.import', { targets: [] })).toBe(false)
  })

  it('maps prompts actions to tauri commands', () => {
    expect(bridgeCommandByActionV2['prompts.list']).toBe('prompts_list')
    expect(bridgeCommandByActionV2['prompts.import']).toBe('prompts_import')
  })
})
```

(`describe`/`it`/`expect` 的导入方式与该文件现有顶部保持一致;若该测试文件尚未 import `bridgeCommandByActionV2`,补进现有 import。)

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/bridge-contract.test.ts`
Expected: FAIL(类型未定义)。

- [ ] **Step 3: 实现契约**

`src/bridge-contract.ts`:

1. `VersionedBridgeAction` 联合追加 8 项:

```ts
  | 'prompts.list'
  | 'prompts.get'
  | 'prompts.save'
  | 'prompts.delete'
  | 'prompts.activate'
  | 'prompts.deactivate'
  | 'prompts.status'
  | 'prompts.import'
```

2. `bridgeCommandByActionV2` 追加:

```ts
  'prompts.list': 'prompts_list',
  'prompts.get': 'prompts_get',
  'prompts.save': 'prompts_save',
  'prompts.delete': 'prompts_delete',
  'prompts.activate': 'prompts_activate',
  'prompts.deactivate': 'prompts_deactivate',
  'prompts.status': 'prompts_status',
  'prompts.import': 'prompts_import',
```

3. `isVersionedBridgePayload` 在 `plugin.install.start` 分支前追加:

```ts
  if (action === 'prompts.get' || action === 'prompts.delete') return hasId('presetId')
  if (action === 'prompts.activate') return hasId('presetId') && isPromptTarget(value.target)
  if (action === 'prompts.deactivate') return isPromptTarget(value.target)
  if (action === 'prompts.save') {
    return (value.presetId === undefined || validRequestId(value.presetId))
      && typeof value.title === 'string'
      && value.title.trim().length > 0
      && value.title.length <= 200
      && typeof value.content === 'string'
      && value.content.length <= 24 * 1024
  }
  if (action === 'prompts.import') {
    return Array.isArray(value.targets)
      && value.targets.length >= 1
      && value.targets.length <= 3
      && value.targets.every((entry) => isPromptTarget(entry))
      && new Set(value.targets.map(String)).size === value.targets.length
  }
```

4. 文件底部工具函数区追加:

```ts
function isPromptTarget(value: unknown): value is 'claude' | 'codex' | 'dsh' {
  return value === 'claude' || value === 'codex' || value === 'dsh'
}
```

5. `versionedPayloadKeys` 追加:

```ts
  'prompts.list': [],
  'prompts.get': ['presetId'],
  'prompts.save': ['presetId', 'title', 'content'],
  'prompts.delete': ['presetId'],
  'prompts.activate': ['presetId', 'target'],
  'prompts.deactivate': ['target'],
  'prompts.status': [],
  'prompts.import': ['targets'],
```

- [ ] **Step 4: 跑测试与类型检查验证通过**

Run: `npx vitest run src/bridge-contract.test.ts && npx tsc --noEmit -p tsconfig.json`
Expected: 测试 PASS;无类型错误。

- [ ] **Step 5: Commit**

```bash
git add src/bridge-contract.ts src/bridge-contract.test.ts
git commit -m "feat(bridge): 新增 prompts v2 动作契约"
```

---

### Task 12: 插件侧动作联合 + 删除 Agent 入口 + 扩展中心骨架

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/bridge-contract.ts`
- Create: `packages/dsh-plugin-desktop/src/client/extension-center-state.ts`
- Create: `packages/dsh-plugin-desktop/src/client/ExtensionCenterFooterAction.tsx`
- Create: `packages/dsh-plugin-desktop/src/client/extension-center/ExtensionCenterPanel.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/contracts.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/advanced-shell.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/AdvancedFrame.tsx`
- Delete: `packages/dsh-plugin-desktop/src/client/AgentHome.tsx`
- Delete: `packages/dsh-plugin-desktop/src/client/agent-home-state.ts`
- Test: `packages/dsh-plugin-desktop/tests/advanced-frame.spec.tsx`(改写)
- Delete: `packages/dsh-plugin-desktop/tests/agent-home.spec.tsx`

- [ ] **Step 1: 写失败测试(改写 advanced-frame.spec)**

`packages/dsh-plugin-desktop/tests/advanced-frame.spec.tsx`:把所有 `agentHome`/`AgentHome` 相关的用例替换为「扩展中心」等价用例(保留其余不动)。核心新增用例(Props 构造沿用该文件现有 fake 对象,把 `agentHome` 换成 `extensionCenter`):

```tsx
it('侧边栏不再渲染 Agent 按钮,扩展中心按钮开合对话面', async () => {
  const frame = render(<AdvancedFrame {...frameProps({ extensionCenter: new ExtensionCenterState() })} />)
  expect(frame.queryByRole('button', { name: 'Agent' })).not.toBeInTheDocument()
  // 对话面默认是工作台;打开扩展中心后出现面板
  expect(frame.queryByRole('complementary', { name: '扩展中心' })).not.toBeInTheDocument()
})
```

(该文件现有 fake/props 工厂保持;执行时以文件内既有 helper 命名为准,仅做 `agentHome → extensionCenter` 的同构替换。)

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run plugin:test -w @dsh/desktop-plugin -- tests/advanced-frame.spec.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

1. `packages/dsh-plugin-desktop/src/client/bridge-contract.ts` 的 `VersionedBridgeAction` 追加与 Task 11 相同的 8 项(该文件无 payload 校验器,只加联合)。

2. `packages/dsh-plugin-desktop/src/client/extension-center-state.ts`(仿 `local-projects-state.ts`):

```ts
export class ExtensionCenterState {
  private opened = false
  private readonly listeners = new Set<() => void>()

  readonly getSnapshot = () => this.opened

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  open(): void { this.setOpened(true) }
  close(): void { this.setOpened(false) }
  toggle(): void { this.setOpened(!this.opened) }

  private setOpened(opened: boolean): void {
    if (this.opened === opened) return
    this.opened = opened
    this.listeners.forEach((listener) => listener())
  }
}
```

3. `packages/dsh-plugin-desktop/src/client/ExtensionCenterFooterAction.tsx`(完全仿 `LocalProjectsFooterAction.tsx`):

```tsx
import { useSyncExternalStore } from 'react'
import type { ExtensionCenterState } from './extension-center-state'

export interface ExtensionCenterFooterActionProps {
  wide: boolean
  state: ExtensionCenterState
}

export function ExtensionCenterFooterAction({ wide, state }: ExtensionCenterFooterActionProps) {
  const opened = useSyncExternalStore(state.subscribe, state.getSnapshot)
  return (
    <button
      type="button"
      className={`dshDesktopFooterAction${wide ? '' : ' is-rail'}${opened ? ' is-active' : ''}`}
      aria-label="扩展中心"
      aria-pressed={opened}
      title={wide ? undefined : '扩展中心'}
      onClick={() => state.toggle()}
    >
      <ExtensionCenterIcon />
      {wide && <span className="dshDesktopFooterActionLabel">扩展中心</span>}
    </button>
  )
}

function ExtensionCenterIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
      <path d="M10 4h4v4h4v4h-4v4h-4v-4H6V8h4V4Z" />
      <path d="m16.5 15.5 3 3-3 3-3-3 3-3Z" />
    </svg>
  )
}
```

4. `packages/dsh-plugin-desktop/src/client/extension-center/ExtensionCenterPanel.tsx`:

```tsx
import { useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { PromptsPanel } from './PromptsPanel'

type CenterTab = 'prompts' | 'mcp' | 'skills' | 'usage'

const TABS: Array<[CenterTab, string]> = [
  ['prompts', '提示词'],
  ['mcp', 'MCP'],
  ['skills', 'Skills'],
  ['usage', '用量'],
]

export function ExtensionCenterPanel(props: { bridge: DesktopBridgeLike }) {
  const [tab, setTab] = useState<CenterTab>('prompts')
  return (
    <section className="dshExtCenter" role="complementary" aria-label="扩展中心">
      <header className="dshExtCenterHeader">
        <div>
          <p className="dshModelAgentEyebrow">DESKTOP EXTENSIONS</p>
          <h2>扩展中心</h2>
          <p>跨应用管理提示词、MCP、Skills 与用量。</p>
        </div>
      </header>
      <nav className="dshExtCenterTabs" role="tablist" aria-label="扩展中心页签">
        {TABS.map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>
        ))}
      </nav>
      {tab === 'prompts' && <PromptsPanel bridge={props.bridge} />}
      {tab === 'mcp' && <p className="dshExtCenterPlaceholder">即将推出</p>}
      {tab === 'skills' && <p className="dshExtCenterPlaceholder">即将推出</p>}
      {tab === 'usage' && <p className="dshExtCenterPlaceholder">即将推出</p>}
    </section>
  )
}
```

(`PromptsPanel` 先建最小占位文件,Task 13 实装:

```tsx
import type { DesktopBridgeLike } from '../desktop-bridge'

export function PromptsPanel(_props: { bridge: DesktopBridgeLike }) {
  return <p className="dshExtCenterPlaceholder">加载中…</p>
}
```

)

5. `packages/dsh-plugin-desktop/src/client/contracts.ts`:`AdvancedFrameProps.agentHome` 替换为 `extensionCenter: ExtensionCenterState`(import 相应调整),删除 `AgentHomeState` import。

6. `packages/dsh-plugin-desktop/src/client/advanced-shell.ts`:
   - 删除 `import { AgentHomeState }` 与 `const agentHome = new AgentHomeState()`;
   - 新增 `import { ExtensionCenterState } from './extension-center-state'`、`import { ExtensionCenterFooterAction } from './ExtensionCenterFooterAction'`,以及 `const extensionCenter = new ExtensionCenterState()`;
   - 在现有 local-projects footer 注册后追加:

```ts
  ctx.slots.inject?.('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    id: 'dsh-desktop-extension-center',
    order: 20,
    inject: () => ({ state: extensionCenter }),
  }, ExtensionCenterFooterAction))
```

   - root slot 的 `inject` 里 `agentHome` 换成 `extensionCenter`。

7. `packages/dsh-plugin-desktop/src/client/AdvancedFrame.tsx`:
   - 删除 `AgentHome` import、`AgentHomeEntry` 组件、`agentHome`/`agentOpen`/`agentProvider`/`openWorkbench`/`closeAgentHome`、以及 `dshDesktopSidebarEntries` 容器;
   - 新增 `extensionCenter` prop 解构与 `const centerOpen = useSyncExternalStore(extensionCenter.subscribe, extensionCenter.getSnapshot)`;
   - 互斥 effect: `{ if (projectsOpen) extensionCenter.close() }` 与 `{ if (centerOpen) localProjects.close() }`;
   - `computeDesktopColumns(...)` 调用里 `projectsOpen || agentOpen || ...` 改为 `projectsOpen || centerOpen || ...`;
   - 主面对话区:

```tsx
        {projectsOpen
          ? <LocalProjectsPage state={workspaceState} workspaces={workspaces} sessions={sessions} bridge={bridge} onClose={() => localProjects.close()} />
          : centerOpen
            ? <ExtensionCenterPanel bridge={bridge} />
            : renderSlot('conversation', {})}
```

8. 删除 `AgentHome.tsx`、`agent-home-state.ts`、`tests/agent-home.spec.tsx`;全局搜索 `agent-home`、`dshAgentHome` 等残留引用并清理(`styles.ts` 中 `.dshAgentHome*` 样式块一并删除;`dshAgent*` 其余类名被 model-agent 使用前先确认)。

9. `packages/dsh-plugin-desktop/src/client/styles.ts` 追加面板样式(沿用主题变量):

```css
.dshExtCenter { display: grid; gap: 18px; max-width: 1080px; margin: 0 auto; padding: 4px 2px; }
.dshExtCenterHeader { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; }
.dshExtCenterTabs { display: flex; gap: 8px; border-bottom: 1px solid var(--dsw-alias-border-secondary, rgba(127,127,127,.25)); padding-bottom: 10px; }
.dshExtCenterTabs button[aria-selected='true'] { color: var(--dsw-alias-text-primary); border-color: var(--dsw-alias-border-strong, currentColor); }
.dshExtCenterPlaceholder { color: var(--dsw-alias-label-secondary, #b7b7bf); padding: 32px 0; text-align: center; }
```

(具体选择器命名若与 styles.ts 现有 dsh 桌面类冲突,按文件内现有前缀约定微调;`dshModelAgentEyebrow` 直接复用。)

- [ ] **Step 4: 跑测试与构建验证通过(含 footer 顺序断言)**

在 `tests/advanced-frame.spec.tsx`(或 advanced-shell 相关 spec)追加顺序断言:

```tsx
it('footer 顺序为 本地项目 → 扩展中心(设置由官方渲染在其后)', () => {
  const labels = Array.from(document.querySelectorAll('.dshDesktopFooterActionLabel'))
    .map((node) => node.textContent)
  expect(labels.indexOf('本地项目')).toBeLessThan(labels.indexOf('扩展中心'))
})
```

Run: `npm run plugin:test -w @dsh/desktop-plugin && npm run build -w @dsh/desktop-plugin`
Expected: 全部插件测试 PASS(含顺序断言);构建无类型错误。

- [ ] **Step 5: Commit**

```bash
git add -A packages/dsh-plugin-desktop
git commit -m "refactor(desktop-plugin): 扩展中心替换 Agent 入口并预留功能页签"
```

---

### Task 13: AgentCard 补安装/登录入口(能力补偿)

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/model-agent/AgentCard.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/model-agent/ModelAgentCenter.tsx`
- Test: `packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx`(追加用例)

- [ ] **Step 1: 写失败测试**

`tests/model-agent-center.spec.tsx` 追加(沿用该文件现有 mock bridge 工厂):

```tsx
it('Agents 页签提供安装与登录入口并触发对应动作', async () => {
  const bridge = fakeBridgeWith({
    'cli.install.status': { installed: false, jobRunning: false, jobOutput: [] },
    'cli.login.status': { installed: false, jobRunning: false, jobOutput: [] },
  })
  render(<ModelAgentCenter bridge={bridge} />)
  fireEvent.click(await screen.findByRole('tab', { name: 'Agents' }))
  fireEvent.click(await screen.findByRole('button', { name: '安装 CLI' }))
  expect(bridge.requestV2).toHaveBeenCalledWith('cli.install.start', undefined, expect.objectContaining({ providerId: 'codex' }))
  fireEvent.click(await screen.findByRole('button', { name: '登录账号' }))
  expect(bridge.requestV2).toHaveBeenCalledWith('cli.login.start', undefined, expect.objectContaining({ providerId: 'codex' }))
})
```

(`fakeBridgeWith` 为该文件现有 mock helper 名;若不同,以文件内实际为准。)

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run plugin:test -w @dsh/desktop-plugin -- tests/model-agent-center.spec.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

`AgentCard.tsx` 追加操作行(Props 增加可选回调与状态):

```tsx
export interface AgentJobStatus {
  installed: boolean
  jobRunning: boolean
  jobOutput: string[]
  loggedIn?: boolean
}

export interface AgentCardProps {
  provider: ProviderMetadata
  state: ProviderState
  cliStatus?: { path?: string; version?: string; diagnostics?: Array<{ code: string; message: string }> }
  installStatus?: AgentJobStatus
  loginStatus?: AgentJobStatus
  onStartInstall?(provider: ProviderMetadata): void
  onStartLogin?(provider: ProviderMetadata): void
}

export function AgentCard({ provider, state, cliStatus, installStatus, loginStatus, onStartInstall, onStartLogin }: AgentCardProps) {
  const path = cliStatus?.path ?? '等待检测'
  const installed = loginStatus?.installed === true || installStatus?.installed === true
  const loggedIn = loginStatus?.loggedIn === true
  const busy = installStatus?.jobRunning === true || loginStatus?.jobRunning === true
  return (
    <article className="dshModelAgentCard">
      <div className="dshModelAgentCardHeader">
        <div><span className="dshModelAgentAgentMark">A</span><div><h3>{provider.displayName} Agent</h3><small>{provider.providerId}:default</small></div></div>
        <span className={`dshModelAgentStatus dshModelAgentStatus-${state.kind}`}>{state.label}</span>
      </div>
      <dl className="dshModelAgentDetails"><div><dt>适配器</dt><dd>{provider.adapterProtocol}</dd></div><div><dt>CLI 路径</dt><dd title={path}>{path}</dd></div><div><dt>版本</dt><dd>{cliStatus?.version ?? '未检测'}</dd></div></dl>
      {cliStatus?.diagnostics?.[0] !== undefined && <p className="dshModelAgentCardHint">{cliStatus.diagnostics[0].message}</p>}
      <div className="dshModelAgentCardActions">
        {!installed && !busy && onStartInstall !== undefined && <button type="button" onClick={() => onStartInstall(provider)}>安装 CLI</button>}
        {installed && !loggedIn && !busy && onStartLogin !== undefined && <button type="button" onClick={() => onStartLogin(provider)}>登录账号</button>}
        {busy && <span role="status">任务进行中,完成后自动刷新…</span>}
      </div>
      {(installStatus?.jobOutput?.length ?? 0) + (loginStatus?.jobOutput?.length ?? 0) > 0 && (
        <pre className="dshModelAgentJobLog">{[...(installStatus?.jobOutput ?? []), ...(loginStatus?.jobOutput ?? [])].join('\n')}</pre>
      )}
    </article>
  )
}
```

`ModelAgentCenter.tsx`:agents 分支改为带安装/登录状态与轮询(逻辑从 `AgentHome.tsx` 平移;在组件内加):

```tsx
  const [install, setInstall] = useState<Record<string, { installed: boolean; jobRunning: boolean; jobOutput: string[] }>>({})
  const [login, setLogin] = useState<Record<string, { installed: boolean; jobRunning: boolean; jobOutput: string[]; loggedIn?: boolean }>>({})

  const loadAgentJobs = useCallback(async () => {
    const nextInstall: typeof install = {}
    const nextLogin: typeof login = {}
    await Promise.all(visibleProviders.map(async (provider) => {
      try {
        nextInstall[provider.providerId] = await bridge.requestV2('cli.install.status', undefined, { providerId: provider.providerId })
        nextLogin[provider.providerId] = await bridge.requestV2('cli.login.status', undefined, { providerId: provider.providerId })
      } catch { /* 状态获取失败按未安装展示 */ }
    }))
    setInstall(nextInstall)
    setLogin(nextLogin)
  }, [bridge, visibleProviders])

  useEffect(() => { void loadAgentJobs() }, [loadAgentJobs])
  const jobsRunning = Object.values(install).some((status) => status.jobRunning) || Object.values(login).some((status) => status.jobRunning)
  useEffect(() => {
    if (!jobsRunning) return
    const timer = setInterval(() => { void loadAgentJobs() }, 2000)
    return () => clearInterval(timer)
  }, [jobsRunning, loadAgentJobs])

  const startInstall = async (provider: ProviderMetadata) => {
    try {
      const reply = await bridge.requestV2('cli.install.start', undefined, { providerId: provider.providerId })
      setInstall((current) => ({ ...current, [provider.providerId]: reply }))
    } catch (cause) { setErrors([messageOf(cause)]) }
  }
  const startLogin = async (provider: ProviderMetadata) => {
    try {
      const reply = await bridge.requestV2('cli.login.start', undefined, { providerId: provider.providerId })
      setLogin((current) => ({ ...current, [provider.providerId]: reply }))
    } catch (cause) { setErrors([messageOf(cause)]) }
  }
```

agents 分支的 `AgentCard` 调用传入:

```tsx
<AgentCard key={provider.providerId} provider={provider} state={state} cliStatus={cli[provider.providerId]}
  installStatus={install[provider.providerId]} loginStatus={login[provider.providerId]}
  onStartInstall={(target) => void startInstall(target)} onStartLogin={(target) => void startLogin(target)} />
```

`styles.ts` 追加:

```css
.dshModelAgentCardActions { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
.dshModelAgentCardActions button { border: 1px solid var(--dsw-alias-border-secondary, rgba(127,127,127,.4)); border-radius: 8px; padding: 5px 12px; background: transparent; color: inherit; cursor: pointer; }
.dshModelAgentJobLog { max-height: 140px; overflow: auto; font-size: 12px; background: var(--dsw-alias-surface-secondary, rgba(127,127,127,.08)); border-radius: 8px; padding: 10px; white-space: pre-wrap; }
```

- [ ] **Step 4: 跑测试验证通过**

Run: `npm run plugin:test -w @dsh/desktop-plugin -- tests/model-agent-center.spec.tsx`
Expected: PASS(含原有用例)。

- [ ] **Step 5: Commit**

```bash
git add packages/dsh-plugin-desktop
git commit -m "feat(desktop-plugin): Agent 卡片承接安装与登录入口"
```

---

### Task 14: 提示词面板 · 数据层与状态条/列表

**Files:**
- Create: `packages/dsh-plugin-desktop/src/client/extension-center/prompts-api.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/extension-center/PromptsPanel.tsx`
- Modify: `packages/dsh-plugin-desktop/package.json`(加依赖)
- Test: `packages/dsh-plugin-desktop/tests/prompts-panel.spec.tsx`(新建)

- [ ] **Step 1: 安装依赖**

Run: `npm install marked dompurify --workspace @dsh-plugin-desktop --legacy-peer-deps`
Expected: package.json 出现两个依赖;lockfile 更新。

- [ ] **Step 2: 写失败测试**

`tests/prompts-panel.spec.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PromptsPanel } from '../src/client/extension-center/PromptsPanel'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeWith(handlers: Record<string, (payload?: Record<string, unknown>) => unknown>): DesktopBridgeLike {
  return {
    request: vi.fn().mockRejectedValue(new Error('v1 不可用')),
    requestV2: vi.fn().mockImplementation((action: string, _context?: unknown, payload?: Record<string, unknown>) => {
      const handler = handlers[action]
      if (handler === undefined) return Promise.reject(new Error(`未模拟的动作 ${action}`))
      return Promise.resolve(handler(payload))
    }),
    dispose: () => undefined,
  }
}

function panel(bridge: DesktopBridgeLike) {
  return render(<PromptsPanel bridge={bridge} />)
}

const STATUS = [
  { target: 'claude', installed: true, liveFileExists: true, activePresetId: 'p1', liveContentSha256: 'aa', matchesActivePreset: true, oversized: false },
  { target: 'codex', installed: false, liveFileExists: false, activePresetId: null, liveContentSha256: null, matchesActivePreset: false, oversized: false },
  { target: 'dsh', installed: true, liveFileExists: false, activePresetId: null, liveContentSha256: null, matchesActivePreset: false, oversized: false },
]

describe('PromptsPanel', () => {
  it('加载目标状态与预设列表并渲染状态条', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => [{ id: 'p1', title: '默认提示词', updatedAt: 1, activatedTargets: ['claude'] }],
    })
    panel(bridge)
    expect(await screen.findByText('Claude')).toBeInTheDocument()
    expect(await screen.findByText('默认提示词')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Codex' })).toBeDisabled()
    expect(screen.queryByText(/外部修改/)).not.toBeInTheDocument()
  })

  it('live 哈希与激活预设不一致时亮出外部修改徽标', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS.map((entry) => entry.target === 'claude' ? { ...entry, matchesActivePreset: false } : entry),
      'prompts.list': () => [],
    })
    panel(bridge)
    expect(await screen.findByText(/外部修改/)).toBeInTheDocument()
  })

  it('预设池为空且存在非空 live 文件时弹首启导入对话框', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS.map((entry) => entry.target === 'claude' ? { ...entry, liveFileExists: true, activePresetId: null } : entry),
      'prompts.list': () => [],
    })
    panel(bridge)
    expect(await screen.findByRole('dialog', { name: '导入现有提示词' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Claude' }))
    fireEvent.click(screen.getByRole('button', { name: '导入' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.import', undefined, { targets: ['claude'] }))
  })
})
```

- [ ] **Step 3: 跑测试确认失败**

Run: `npm run plugin:test -w @dsh/desktop-plugin -- tests/prompts-panel.spec.tsx`
Expected: FAIL。

- [ ] **Step 4: 实现数据层与面板骨架**

`packages/dsh-plugin-desktop/src/client/extension-center/prompts-api.ts`:

```ts
import type { DesktopBridgeLike } from '../desktop-bridge'

export type PromptTarget = 'claude' | 'codex' | 'dsh'

export interface PromptPreset { id: string; title: string; content: string; createdAt: number; updatedAt: number }
export interface PresetSummary { id: string; title: string; updatedAt: number; activatedTargets: PromptTarget[] }
export interface TargetStatus {
  target: PromptTarget
  installed: boolean
  liveFileExists: boolean
  activePresetId: string | null
  liveContentSha256: string | null
  matchesActivePreset: boolean
  oversized: boolean
}
export type SaveOutcome =
  | { kind: 'saved'; preset: PromptPreset; projected: TargetStatus[] }
  | { kind: 'backfill-conflict'; presetId: string; candidates: Array<{ target: PromptTarget; content: string; updatedAt: number }> }
export type ActivateOutcome =
  | { kind: 'ok'; status: TargetStatus }
  | { kind: 'backfill-conflict'; presetId: string; candidates: Array<{ target: PromptTarget; content: string; updatedAt: number }> }

export const TARGET_LABELS: Record<PromptTarget, string> = { claude: 'Claude', codex: 'Codex', dsh: 'DSH' }
export const MAX_PROMPT_CHARS = 24 * 1024

export async function fetchStatus(bridge: DesktopBridgeLike): Promise<TargetStatus[]> {
  return bridge.requestV2<TargetStatus[]>('prompts.status')
}
export async function fetchList(bridge: DesktopBridgeLike): Promise<PresetSummary[]> {
  return bridge.requestV2<PresetSummary[]>('prompts.list')
}
export async function fetchPreset(bridge: DesktopBridgeLike, presetId: string): Promise<PromptPreset> {
  return bridge.requestV2<PromptPreset>('prompts.get', undefined, { presetId })
}
export async function savePreset(bridge: DesktopBridgeLike, input: { presetId?: string; title: string; content: string }): Promise<SaveOutcome> {
  return bridge.requestV2<SaveOutcome>('prompts.save', undefined, input)
}
export async function deletePreset(bridge: DesktopBridgeLike, presetId: string): Promise<void> {
  await bridge.requestV2('prompts.delete', undefined, { presetId })
}
export async function activatePreset(bridge: DesktopBridgeLike, presetId: string, target: PromptTarget): Promise<ActivateOutcome> {
  return bridge.requestV2<ActivateOutcome>('prompts.activate', undefined, { presetId, target })
}
export async function deactivateTarget(bridge: DesktopBridgeLike, target: PromptTarget): Promise<TargetStatus> {
  return bridge.requestV2<TargetStatus>('prompts.deactivate', undefined, { target })
}
export async function importTargets(bridge: DesktopBridgeLike, targets: PromptTarget[]): Promise<PresetSummary[]> {
  return bridge.requestV2<PresetSummary[]>('prompts.import', undefined, { targets })
}
```

`PromptsPanel.tsx` 实装(替换占位):

```tsx
import { useCallback, useEffect, useMemo, useState } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'
import {
  fetchList, fetchStatus, importTargets, TARGET_LABELS,
  type PresetSummary, type PromptTarget, type TargetStatus,
} from './prompts-api'

export function PromptsPanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [statuses, setStatuses] = useState<TargetStatus[]>([])
  const [presets, setPresets] = useState<PresetSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [importOpen, setImportOpen] = useState(false)

  const load = useCallback(async () => {
    const [statusReply, listReply] = await Promise.all([fetchStatus(bridge), fetchList(bridge)])
    setStatuses(statusReply)
    setPresets(listReply)
  }, [bridge])

  useEffect(() => {
    void load().then(() => setLoaded(true)).catch((cause: unknown) => { setError(messageOf(cause)); setLoaded(true) })
  }, [load])

  // 首启导入:池为空且存在非空 live 文件(spec §5)
  useEffect(() => {
    if (!loaded) return
    const poolEmpty = presets.length === 0
    const hasLive = statuses.some((status) => status.liveFileExists && status.activePresetId === null && !status.oversized)
    if (poolEmpty && hasLive) setImportOpen(true)
  }, [loaded, presets.length, statuses])

  const refreshAll = useCallback(async () => {
    try { await load(); setError(null) } catch (cause: unknown) { setError(messageOf(cause)) }
  }, [load])

  const importCandidates = useMemo(
    () => statuses.filter((status) => status.installed && status.liveFileExists && !status.oversized),
    [statuses],
  )

  return (
    <div className="dshPrompts">
      <div className="dshPromptsStatusRow" role="group" aria-label="目标状态">
        {statuses.map((status) => (
          <button key={status.target} type="button" className="dshPromptsTargetChip" disabled={!status.installed}
            title={status.installed ? `${TARGET_LABELS[status.target]}:${status.activePresetId === null ? '未激活' : '已激活'}` : '未安装'}>
            <span>{TARGET_LABELS[status.target]}</span>
            <span className="dshPromptsTargetState">
              {status.installed ? (status.activePresetId !== null ? '已激活' : '未激活') : '未安装'}
            </span>
            {status.installed && status.activePresetId !== null && !status.matchesActivePreset && (
              <span className="dshPromptsDrift">⚠外部修改</span>
            )}
          </button>
        ))}
        <span className="dshPromptsSpacer" />
        <button type="button" onClick={() => setImportOpen(true)}>从文件导入</button>
        <button type="button" onClick={() => void refreshAll()}>刷新</button>
      </div>
      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
      {importOpen && (
        <PromptsImportDialog
          candidates={importCandidates}
          busy={false}
          onClose={() => setImportOpen(false)}
          onImport={async (targets) => {
            try {
              await importTargets(bridge, targets)
              setImportOpen(false)
              await refreshAll()
            } catch (cause: unknown) { setError(messageOf(cause)) }
          }}
        />
      )}
    </div>
  )
}

export function PromptsImportDialog(props: {
  candidates: TargetStatus[]
  busy: boolean
  onClose(): void
  onImport(targets: PromptTarget[]): Promise<void> | void
}) {
  const [selected, setSelected] = useState<PromptTarget[]>(props.candidates.map((candidate) => candidate.target))
  return (
    <div className="dshPromptsDialogBackdrop" role="presentation">
      <div className="dshPromptsDialog" role="dialog" aria-label="导入现有提示词">
        <h3>导入现有提示词</h3>
        <p>把各目标当前的全局提示词文件导入为预设,并保持激活状态。</p>
        {props.candidates.map((candidate) => (
          <label key={candidate.target} className="dshPromptsImportRow">
            <input
              type="checkbox"
              aria-label={TARGET_LABELS[candidate.target]}
              checked={selected.includes(candidate.target)}
              onChange={(event) => {
                setSelected((current) => event.target.checked
                  ? [...current, candidate.target]
                  : current.filter((target) => target !== candidate.target))
              }}
            />
            <span>{TARGET_LABELS[candidate.target]}</span>
            <span className="dshPromptsMuted">{candidate.activePresetId === null ? '未激活' : '已激活'}</span>
          </label>
        ))}
        <div className="dshPromptsDialogActions">
          <button type="button" onClick={props.onClose}>取消</button>
          <button type="button" disabled={props.busy || selected.length === 0} onClick={() => void props.onImport(selected)}>导入</button>
        </div>
      </div>
    </div>
  )
}

export function renderMarkdownPreview(content: string): string {
  return DOMPurify.sanitize(marked.parse(content, { async: false }))
}

export function targetSummary(targets: PromptTarget[]): string {
  return targets.map((target) => TARGET_LABELS[target]).join(' / ')
}
```

> 说明:预设列表区/编辑器区在 Task 15 补全;本任务先让状态条 + 导入对话框落地,`fetchList` 的结果暂存 state,不渲染报错。

- [ ] **Step 5: 跑测试验证通过**

Run: `npm run plugin:test -w @dsh/desktop-plugin -- tests/prompts-panel.spec.tsx`
Expected: 3 个测试 PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/dsh-plugin-desktop
git commit -m "feat(desktop-plugin): 提示词面板状态条与首启导入"
```

---

### Task 15: 提示词面板 · 编辑器、保存与冲突对话框

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/extension-center/PromptsPanel.tsx`
- Create: `packages/dsh-plugin-desktop/src/client/extension-center/PromptsConflictDialog.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/styles.ts`
- Test: `packages/dsh-plugin-desktop/tests/prompts-panel.spec.tsx`(追加)

- [ ] **Step 1: 写失败测试**

`tests/prompts-panel.spec.tsx` 追加:

```tsx
const LIST = [{ id: 'p1', title: '默认提示词', updatedAt: 1, activatedTargets: ['claude' as const] }]

it('选择预设进入编辑器,保存走 prompts.save 并刷新', async () => {
  const bridge = bridgeWith({
    'prompts.status': () => STATUS,
    'prompts.list': () => LIST,
    'prompts.get': () => ({ id: 'p1', title: '默认提示词', content: '# 正文', createdAt: 0, updatedAt: 1 }),
    'prompts.save': () => ({ kind: 'saved', preset: { id: 'p1', title: '默认提示词', content: '# 新正文', createdAt: 0, updatedAt: 2 }, projected: STATUS }),
  })
  panel(bridge)
  fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
  const editor = await screen.findByRole('textbox', { name: '预设内容' })
  expect(editor).toHaveValue('# 正文')
  expect(screen.getByText(/预览/).nextElementSibling).not.toBeNull()
  fireEvent.change(editor, { target: { value: '# 新正文' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.save', undefined, { presetId: 'p1', title: '默认提示词', content: '# 新正文' }))
})

it('保存返回冲突时弹对话框,选定候选后重试', async () => {
  const bridge = bridgeWith({
    'prompts.status': () => STATUS,
    'prompts.list': () => LIST,
    'prompts.get': () => ({ id: 'p1', title: '默认提示词', content: 'v1', createdAt: 0, updatedAt: 1 }),
    'prompts.save': () => ({ kind: 'backfill-conflict', presetId: 'p1', candidates: [
      { target: 'claude', content: 'claude 端内容', updatedAt: 5 },
      { target: 'codex', content: 'codex 端内容', updatedAt: 6 },
    ] }),
  })
  panel(bridge)
  fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
  fireEvent.click(await screen.findByRole('button', { name: '保存' }))
  const dialog = await screen.findByRole('dialog', { name: '检测到外部修改' })
  expect(dialog).toBeInTheDocument()
  fireEvent.click(await screen.findByRole('radio', { name: /Claude/ }))
  fireEvent.click(screen.getByRole('button', { name: '以此为准并保存' }))
  expect(bridge.requestV2).toHaveBeenCalledWith('prompts.save', undefined, { presetId: 'p1', title: '默认提示词', content: 'claude 端内容' })
})

it('激活与停用走对应动作', async () => {
  const bridge = bridgeWith({
    'prompts.status': () => STATUS.map((entry) => ({ ...entry, activePresetId: null })),
    'prompts.list': () => LIST,
    'prompts.get': () => ({ id: 'p1', title: '默认提示词', content: 'v1', createdAt: 0, updatedAt: 1 }),
    'prompts.activate': () => ({ kind: 'ok', status: STATUS[0] }),
    'prompts.deactivate': () => STATUS[0],
  })
  panel(bridge)
  fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
  const activateGroup = await screen.findByRole('group', { name: '激活目标' })
  fireEvent.click(activateGroup.querySelector('input[value="dsh"]') as HTMLElement)
  await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.activate', undefined, { presetId: 'p1', target: 'dsh' }))
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run plugin:test -w @dsh/desktop-plugin -- tests/prompts-panel.spec.tsx`
Expected: 新增 3 个用例 FAIL。

- [ ] **Step 3: 实现编辑器与流程**

`PromptsConflictDialog.tsx`:

```tsx
import { useState } from 'react'
import { TARGET_LABELS, type PromptTarget } from './prompts-api'

export interface ConflictCandidateView { target: PromptTarget; content: string; updatedAt: number }

export function PromptsConflictDialog(props: {
  presetTitle: string
  candidates: ConflictCandidateView[]
  onClose(): void
  onResolve(chosen: ConflictCandidateView): void
}) {
  const [chosen, setChosen] = useState<ConflictCandidateView | null>(null)
  return (
    <div className="dshPromptsDialogBackdrop" role="presentation">
      <div className="dshPromptsDialog" role="dialog" aria-label="检测到外部修改">
        <h3>检测到外部修改</h3>
        <p>「{props.presetTitle}」在多个目标上的文件内容互不一致,选择以哪份为准:</p>
        {props.candidates.map((candidate) => (
          <label key={candidate.target} className="dshPromptsImportRow">
            <input
              type="radio"
              name="prompts-conflict-candidate"
              aria-label={`${TARGET_LABELS[candidate.target]}(更新于 ${new Date(candidate.updatedAt).toLocaleString()})`}
              checked={chosen?.target === candidate.target}
              onChange={() => setChosen(candidate)}
            />
            <span>{TARGET_LABELS[candidate.target]}</span>
            <span className="dshPromptsMuted">{new Date(candidate.updatedAt).toLocaleString()}</span>
          </label>
        ))}
        <div className="dshPromptsDialogActions">
          <button type="button" onClick={props.onClose}>取消</button>
          <button type="button" disabled={chosen === null} onClick={() => { if (chosen !== null) props.onResolve(chosen) }}>
            以此为准并保存
          </button>
        </div>
      </div>
    </div>
  )
}
```

`PromptsPanel.tsx`:在现有骨架中加入编辑器三栏区(列表 / textarea / 预览)与流程:

```tsx
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<{ presetId: string; title: string; content: string } | null>(null)
  const [conflict, setConflict] = useState<Array<{ target: PromptTarget; content: string; updatedAt: number }> | null>(null)

  const openPreset = async (presetId: string) => {
    const preset = await fetchPreset(bridge, presetId)
    setSelectedId(presetId)
    setDraft({ presetId: preset.id, title: preset.title, content: preset.content })
  }

  const saveDraft = async (contentOverride?: string) => {
    if (draft === null) return
    try {
      const outcome = await savePreset(bridge, { presetId: draft.presetId, title: draft.title, content: contentOverride ?? draft.content })
      if (outcome.kind === 'saved') {
        setDraft({ ...draft, content: outcome.preset.content })
        setConflict(null)
        await refreshAll()
      } else {
        setConflict(outcome.candidates)
      }
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }
```

渲染(放在状态条之后;编辑器区):

```tsx
      <div className="dshPromptsBody">
        <ul className="dshPromptsList" aria-label="预设列表">
          {presets.map((preset) => (
            <li key={preset.id}>
              <button type="button" className={preset.id === selectedId ? 'is-active' : undefined} onClick={() => void openPreset(preset.id)}>
                <span>{preset.title}</span>
                <span className="dshPromptsMuted">{targetSummary(preset.activatedTargets) || '未激活'} · {new Date(preset.updatedAt).toLocaleString()}</span>
              </button>
            </li>
          ))}
          {presets.length === 0 && <li className="dshPromptsMuted">还没有预设,点「新建预设」开始。</li>}
        </ul>
        {draft !== null && (
          <div className="dshPromptsEditor">
            <div className="dshPromptsEditorActions">
              <button type="button" disabled={draft.content.length > MAX_PROMPT_CHARS} onClick={() => void saveDraft()}>保存</button>
              <button type="button" onClick={() => { void activateCurrent(); }}>激活当前编辑到勾选目标</button>
              {statuses.some((status) => status.activePresetId === draft.presetId) && (
                <button type="button" onClick={() => void deactivateCurrent()}>停用本预设的目标</button>
              )}
              <button type="button" onClick={() => setDraft(null)}>关闭</button>
              {draft.content.length > MAX_PROMPT_CHARS && <span role="alert">超过 24 KiB 上限</span>}
            </div>
            <textarea aria-label="预设内容" value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} />
            <div className="dshPromptsPreview" aria-label="实时预览" dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(draft.content) }} />
            <fieldset className="dshPromptsActivateGroup">
              <legend>激活目标</legend>
              {statuses.filter((status) => status.installed).map((status) => (
                <label key={status.target}>
                  <input type="checkbox" value={status.target} aria-label={TARGET_LABELS[status.target]}
                    checked={false}
                    onChange={() => void activateCurrentTo(status.target)} />
                  <span>{TARGET_LABELS[status.target]}</span>
                </label>
              ))}
            </fieldset>
          </div>
        )}
      </div>
      {conflict !== null && draft !== null && (
        <PromptsConflictDialog
          presetTitle={draft.title}
          candidates={conflict}
          onClose={() => setConflict(null)}
          onResolve={(chosen) => { void saveDraft(chosen.content) }}
        />
      )}
```

配套动作:

```tsx
  const activateCurrentTo = async (target: PromptTarget) => {
    if (draft === null) return
    try {
      const outcome = await activatePreset(bridge, draft.presetId, target)
      if (outcome.kind === 'backfill-conflict') setConflict(outcome.candidates)
      else await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }
  const deactivateCurrent = async () => {
    if (draft === null) return
    try {
      const activated = statuses.filter((status) => status.activePresetId === draft.presetId)
      for (const status of activated) await deactivateTarget(bridge, status.target)
      await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }
```

import 区补 `savePreset, deletePreset, activatePreset, deactivateTarget, fetchPreset, MAX_PROMPT_CHARS` 与 `PromptsConflictDialog`。

`styles.ts` 追加:

```css
.dshPrompts { display: grid; gap: 14px; }
.dshPromptsStatusRow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.dshPromptsTargetChip { display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--dsw-alias-border-secondary, rgba(127,127,127,.35)); border-radius: 999px; padding: 4px 12px; background: transparent; color: inherit; }
.dshPromptsTargetChip:disabled { opacity: .45; cursor: not-allowed; }
.dshPromptsTargetState { color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; }
.dshPromptsDrift { color: #d97706; font-size: 12px; }
.dshPromptsSpacer { flex: 1; }
.dshPromptsBody { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 14px; }
.dshPromptsList { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; align-content: start; }
.dshPromptsList button { width: 100%; text-align: left; display: grid; gap: 2px; border: 1px solid transparent; border-radius: 10px; padding: 8px 10px; background: transparent; color: inherit; cursor: pointer; }
.dshPromptsList button.is-active { border-color: var(--dsw-alias-border-secondary, rgba(127,127,127,.4)); background: var(--dsw-alias-surface-secondary, rgba(127,127,127,.08)); }
.dshPromptsMuted { color: var(--dsw-alias-label-secondary, #b7b7bf); font-size: 12px; }
.dshPromptsEditor { display: grid; grid-template-rows: auto minmax(220px, 1fr) minmax(120px, .6fr) auto; gap: 10px; }
.dshPromptsEditorActions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.dshPromptsEditor textarea { min-height: 220px; resize: vertical; border-radius: 10px; border: 1px solid var(--dsw-alias-border-secondary, rgba(127,127,127,.35)); background: transparent; color: inherit; padding: 10px; font: 13px/1.6 ui-monospace, monospace; }
.dshPromptsPreview { overflow: auto; border: 1px dashed var(--dsw-alias-border-secondary, rgba(127,127,127,.35)); border-radius: 10px; padding: 10px; }
.dshPromptsActivateGroup { display: flex; gap: 12px; border: 0; padding: 0; }
.dshPromptsDialogBackdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: grid; place-items: center; z-index: 40; }
.dshPromptsDialog { background: var(--dsw-alias-surface-primary, #1c1c1f); color: inherit; border-radius: 14px; padding: 18px 20px; width: min(480px, 90vw); display: grid; gap: 12px; }
.dshPromptsDialogActions { display: flex; justify-content: flex-end; gap: 10px; }
.dshPromptsImportRow { display: flex; align-items: center; gap: 10px; }
```

- [ ] **Step 4: 跑测试与构建验证通过**

Run: `npm run plugin:test -w @dsh/desktop-plugin && npm run build -w @dsh/desktop-plugin`
Expected: 全部 PASS;构建成功。

- [ ] **Step 5: Commit**

```bash
git add packages/dsh-plugin-desktop
git commit -m "feat(desktop-plugin): 提示词编辑器与回填冲突流程"
```

---

### Task 16: 全量验收

**Files:** 无新改动(修复验收发现的问题除外)。

- [ ] **Step 1: 全量测试**

Run: `npm run check`
Expected: 全绿(root vitest + 两个 workspace 测试 + web build + 两个 package build)。

- [ ] **Step 2: Rust 全量测试**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --locked`
Expected: 全部 PASS。

- [ ] **Step 3: 手工冒烟(可选,需 `npm run tauri dev` 环境)**

验证:侧边栏出现「扩展中心」且与「设置/本地项目」对齐;Agent 按钮消失;提示词 tab 可新建/编辑/保存/激活;`~/.claude/CLAUDE.md` 内容被写入且改动前有备份。

- [ ] **Step 4: 收尾提交**

```bash
git status --short   # 确认无遗漏文件
git log --oneline -12
```

若有零星修复,逐项提交后确认工作区干净。向用户汇报 Task 1 spike 的结论(尤其 DSH 文件名是否确认)。

---

## 自审记录

1. **Spec 覆盖:** §3 模块布局→Task 2-6;§5 同步语义→Task 7-9;§4.1 入口替换→Task 12 + 能力补偿 Task 13;§4.2 面板→Task 14-15;§6 契约→Task 10-12;§7 安全(路径 Rust 推导、原子写、备份、上限)→Task 5-9;§8 测试→各任务内嵌 + Task 16。
2. **占位扫描:** Task 7 Step 3 中 `PromptsError::Io("__conflict__")` 与 `backfill_merged_content` 占位已随附「重要修正」块给出最终 `Flow<T>` 方案,执行者须按修正实现;Task 7 测试中的 `SaveOutcomeShape!` 宏同样标注了替换写法。除此之外无 TBD。
3. **类型一致性:** `Flow<T>` 在 model.rs 定义(Task 8)、service 与 commands(Task 10 `flow_to_value`)一致使用;TS 侧 `SaveOutcome`/`ActivateOutcome` 的 `kind` 标签(`saved`/`ok`/`backfill-conflict`)与 Rust serde `tag = "kind", rename_all = "kebab-case"` 对齐;`activatedTargets` camelCase 与 `#[serde(rename_all = "camelCase")]` 对齐。
