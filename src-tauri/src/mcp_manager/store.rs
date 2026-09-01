use std::{path::Path, sync::Mutex};

use rusqlite::{Connection, OpenFlags};

use crate::mcp_manager::model::{McpServerDef, McpManagerError, Result};

const CREATE_TABLE: &str = "
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    args_json TEXT NOT NULL,
    env_json TEXT NOT NULL,
    targets_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_name ON mcp_servers (name);
";

/// 单表无版本迁移:直接 CREATE TABLE IF NOT EXISTS,保持与 MVP 规模相称的简单。
pub struct McpStore {
    connection: Mutex<Connection>,
}

impl McpStore {
    pub fn open(database_path: &Path) -> Result<Self> {
        if let Some(parent) = database_path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| McpManagerError::Io(error.to_string()))?;
        }
        let connection = Connection::open_with_flags(
            database_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
        )
        .map_err(|error| McpManagerError::Store(error.to_string()))?;
        connection
            .busy_timeout(std::time::Duration::from_secs(5))
            .map_err(|error| McpManagerError::Store(error.to_string()))?;
        connection
            .execute_batch(CREATE_TABLE)
            .map_err(|error| McpManagerError::Store(error.to_string()))?;
        Ok(Self { connection: Mutex::new(connection) })
    }

    /// 内存库兜底:主库打不开时使用,生命周期仅限本次进程。
    pub fn open_ephemeral() -> Result<Self> {
        let connection = Connection::open_in_memory()
            .map_err(|error| McpManagerError::Store(error.to_string()))?;
        connection
            .execute_batch(CREATE_TABLE)
            .map_err(|error| McpManagerError::Store(error.to_string()))?;
        Ok(Self { connection: Mutex::new(connection) })
    }

    fn with_lock<T>(&self, operation: impl FnOnce(&Connection) -> Result<T>) -> Result<T> {
        let guard = self.connection.lock().map_err(|_| McpManagerError::Store("存储锁中毒".into()))?;
        operation(&guard)
    }

    pub fn upsert(&self, def: &McpServerDef, updated_at: i64) -> Result<()> {
        let args_json = serde_json::to_string(&def.args).map_err(|error| McpManagerError::Store(error.to_string()))?;
        let env_json = serde_json::to_string(&def.env).map_err(|error| McpManagerError::Store(error.to_string()))?;
        let targets_json =
            serde_json::to_string(&def.targets).map_err(|error| McpManagerError::Store(error.to_string()))?;
        self.with_lock(|connection| {
            connection
                .execute(
                    "INSERT INTO mcp_servers (id, name, command, args_json, env_json, targets_json, updated_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
                     ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        command = excluded.command,
                        args_json = excluded.args_json,
                        env_json = excluded.env_json,
                        targets_json = excluded.targets_json,
                        updated_at = excluded.updated_at",
                    rusqlite::params![
                        def.id,
                        def.name,
                        def.command,
                        args_json,
                        env_json,
                        targets_json,
                        updated_at
                    ],
                )
                .map_err(|error| McpManagerError::Store(error.to_string()))?;
            Ok(())
        })
    }

    pub fn get(&self, id: &str) -> Result<Option<McpServerDef>> {
        self.with_lock(|connection| {
            connection
                .query_row(
                    "SELECT id, name, command, args_json, env_json, targets_json FROM mcp_servers WHERE id = ?1",
                    [id],
                    row_to_def,
                )
                .map(Some)
                .or_else(|error| match error {
                    rusqlite::Error::QueryReturnedNoRows => Ok(None),
                    other => Err(McpManagerError::Store(other.to_string())),
                })
        })
    }

    pub fn get_by_name(&self, name: &str) -> Result<Option<McpServerDef>> {
        self.with_lock(|connection| {
            connection
                .query_row(
                    "SELECT id, name, command, args_json, env_json, targets_json FROM mcp_servers WHERE name = ?1",
                    [name],
                    row_to_def,
                )
                .map(Some)
                .or_else(|error| match error {
                    rusqlite::Error::QueryReturnedNoRows => Ok(None),
                    other => Err(McpManagerError::Store(other.to_string())),
                })
        })
    }

    pub fn list(&self) -> Result<Vec<McpServerDef>> {
        self.with_lock(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT id, name, command, args_json, env_json, targets_json FROM mcp_servers ORDER BY name ASC",
                )
                .map_err(|error| McpManagerError::Store(error.to_string()))?;
            let rows = statement
                .query_map([], row_to_def)
                .map_err(|error| McpManagerError::Store(error.to_string()))?;
            rows.collect::<std::result::Result<Vec<_>, _>>()
                .map_err(|error| McpManagerError::Store(error.to_string()))
        })
    }

    pub fn delete(&self, id: &str) -> Result<()> {
        self.with_lock(|connection| {
            connection
                .execute("DELETE FROM mcp_servers WHERE id = ?1", [id])
                .map_err(|error| McpManagerError::Store(error.to_string()))?;
            Ok(())
        })
    }
}

fn row_to_def(row: &rusqlite::Row<'_>) -> std::result::Result<McpServerDef, rusqlite::Error> {
    let args_json: String = row.get(3)?;
    let env_json: String = row.get(4)?;
    let targets_json: String = row.get(5)?;
    Ok(McpServerDef {
        id: row.get(0)?,
        name: row.get(1)?,
        command: row.get(2)?,
        args: serde_json::from_str(&args_json).map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(3, rusqlite::types::Type::Text, error.to_string().into())
        })?,
        env: serde_json::from_str(&env_json).map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(4, rusqlite::types::Type::Text, error.to_string().into())
        })?,
        targets: serde_json::from_str(&targets_json).map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(5, rusqlite::types::Type::Text, error.to_string().into())
        })?,
    })
}

#[cfg(test)]
mod tests {
    use super::McpStore;
    use crate::mcp_manager::model::McpServerDef;
    use std::collections::{BTreeMap, BTreeSet};

    fn def(id: &str, name: &str) -> McpServerDef {
        McpServerDef {
            id: id.to_owned(),
            name: name.to_owned(),
            command: "npx".to_owned(),
            args: vec!["-y".to_owned(), "server-fetch".to_owned()],
            env: BTreeMap::from([("NO_PROXY".to_owned(), "127.0.0.1".to_owned())]),
            targets: BTreeSet::from(["claude".to_owned(), "codex".to_owned()]),
        }
    }

    #[test]
    fn open_is_idempotent_and_survives_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state/mcp-manager.db");
        {
            let store = McpStore::open(&path).unwrap();
            store.upsert(&def("m1", "fetch"), 1).unwrap();
        }
        let reopened = McpStore::open(&path).unwrap();
        let loaded = reopened.get("m1").unwrap().unwrap();
        assert_eq!(loaded.name, "fetch");
        assert_eq!(loaded.args, vec!["-y".to_owned(), "server-fetch".to_owned()]);
        assert_eq!(loaded.targets.len(), 2);
    }

    #[test]
    fn upsert_updates_by_id_and_lists_sorted_by_name() {
        let dir = tempfile::tempdir().unwrap();
        let store = McpStore::open(&dir.path().join("mcp-manager.db")).unwrap();
        store.upsert(&def("m1", "zeta"), 1).unwrap();
        store.upsert(&def("m2", "alpha"), 2).unwrap();
        let mut updated = def("m1", "zeta");
        updated.command = "node".to_owned();
        store.upsert(&updated, 3).unwrap();
        let list = store.list().unwrap();
        assert_eq!(list.len(), 2);
        assert_eq!(list[0].id, "m2", "按 name 升序");
        assert_eq!(list[1].command, "node");
        assert_eq!(store.get_by_name("zeta").unwrap().unwrap().command, "node");
    }

    #[test]
    fn get_missing_and_delete_are_idempotent() {
        let store = McpStore::open_ephemeral().unwrap();
        assert!(store.get("nope").unwrap().is_none());
        store.upsert(&def("m1", "fetch"), 1).unwrap();
        store.delete("m1").unwrap();
        store.delete("m1").unwrap();
        assert!(store.get("m1").unwrap().is_none());
    }

    #[test]
    fn ephemeral_store_supports_crud_in_memory() {
        let store = McpStore::open_ephemeral().unwrap();
        store.upsert(&def("m1", "fetch"), 1).unwrap();
        assert_eq!(store.list().unwrap().len(), 1);
    }
}
