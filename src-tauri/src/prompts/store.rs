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

    fn with_lock<T>(&self, operation: impl FnOnce(&Connection) -> Result<T>) -> Result<T> {
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
                // 契约:更新不存在的预设视为参数错误;delete_preset 则为幂等静默成功(服务层自行前置校验)。
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
                    PromptTarget::parse(&value).ok_or_else(|| {
                        rusqlite::Error::FromSqlConversionFailure(
                            0,
                            rusqlite::types::Type::Text,
                            format!("未知目标 {value}").into(),
                        )
                    })
                })
                .map_err(|error| PromptsError::Store(error.to_string()))?;
            rows.collect::<std::result::Result<Vec<_>, _>>().map_err(|error| PromptsError::Store(error.to_string()))
        })
    }

}

#[cfg(test)]
mod tests {
    use super::PromptsStore;
    use crate::prompts::model::PromptTarget;

    fn store() -> (tempfile::TempDir, PromptsStore) {
        let dir = tempfile::tempdir().unwrap();
        let store = PromptsStore::open(&dir.path().join("state/prompts.db")).unwrap();
        (dir, store)
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
        let (_dir, store) = store();
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
        let (_dir, store) = store();
        store.insert_preset("p1", "A", "c", 1, 1).unwrap();
        store.set_activation(PromptTarget::Claude, "p1", 5).unwrap();
        store.set_activation(PromptTarget::Codex, "p1", 6).unwrap();
        assert_eq!(
            store.active_preset_id(PromptTarget::Claude).unwrap().as_deref(),
            Some("p1")
        );
        assert_eq!(store.activated_targets("p1").unwrap(), vec![PromptTarget::Claude, PromptTarget::Codex]);
        store.clear_activation(PromptTarget::Claude).unwrap();
        assert_eq!(store.active_preset_id(PromptTarget::Claude).unwrap(), None);
    }

    #[test]
    fn deleting_preset_nulls_activation_reference() {
        let (_dir, store) = store();
        store.insert_preset("p1", "A", "c", 1, 1).unwrap();
        store.set_activation(PromptTarget::Claude, "p1", 5).unwrap();
        store.delete_preset("p1").unwrap();
        assert_eq!(store.active_preset_id(PromptTarget::Claude).unwrap(), None);
    }
}
