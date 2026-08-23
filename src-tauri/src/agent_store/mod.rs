pub mod migrations;
pub mod model;

use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::Duration,
};

use chrono::Utc;
use model::{AgentStoreError, BackupMetadata};
use rusqlite::{Connection, MAIN_DB, OpenFlags};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{storage::app_paths::AppPaths, storage::atomic_json::write_atomic};

static MIGRATION_CONTROLLER: OnceLock<Mutex<()>> = OnceLock::new();

#[derive(Clone, Debug)]
pub struct AgentStore {
    database_path: PathBuf,
    backup_dir: PathBuf,
    migration_backup: Option<BackupMetadata>,
}

impl AgentStore {
    pub fn open(paths: &AppPaths) -> Result<Self, AgentStoreError> {
        let controller = MIGRATION_CONTROLLER.get_or_init(|| Mutex::new(()));
        let _guard = controller
            .lock()
            .map_err(|_| AgentStoreError::recovery_required(paths.agent_database.clone(), None))?;
        fs::create_dir_all(&paths.state)
            .map_err(|_| AgentStoreError::recovery_required(paths.agent_database.clone(), None))?;

        let existed = paths.agent_database.exists();
        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_FULL_MUTEX
            | if existed {
                OpenFlags::empty()
            } else {
                OpenFlags::SQLITE_OPEN_CREATE
            };
        let mut connection = configured_connection(&paths.agent_database, flags)
            .map_err(|_| AgentStoreError::recovery_required(paths.agent_database.clone(), None))?;
        let version = migrations::user_version(&connection)
            .map_err(|_| AgentStoreError::recovery_required(paths.agent_database.clone(), None))?;
        if version == migrations::CURRENT_SCHEMA_VERSION {
            return Ok(Self::ready(paths, None));
        }
        if version < 0 || version > migrations::CURRENT_SCHEMA_VERSION {
            return Err(AgentStoreError::recovery_required(
                paths.agent_database.clone(),
                None,
            ));
        }

        let backup = if existed {
            Some(create_verified_backup(
                &connection,
                &paths.agent_database,
                &paths.agent_backups,
                version,
            )?)
        } else {
            None
        };
        if migrations::migrate_to_v1(&mut connection).is_err() {
            return Err(AgentStoreError::migration_failed(
                paths.agent_database.clone(),
                backup,
            ));
        }
        Ok(Self::ready(paths, backup))
    }

    fn ready(paths: &AppPaths, migration_backup: Option<BackupMetadata>) -> Self {
        Self {
            database_path: paths.agent_database.clone(),
            backup_dir: paths.agent_backups.clone(),
            migration_backup,
        }
    }

    pub fn database_path(&self) -> &Path {
        &self.database_path
    }

    pub fn backup_dir(&self) -> &Path {
        &self.backup_dir
    }

    pub fn migration_backup(&self) -> Option<&BackupMetadata> {
        self.migration_backup.as_ref()
    }

    pub fn reader(&self) -> Result<Connection, AgentStoreError> {
        configured_connection(
            &self.database_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
        )
        .map_err(|_| AgentStoreError::recovery_required(self.database_path.clone(), None))
    }
}

fn configured_connection(path: &Path, flags: OpenFlags) -> rusqlite::Result<Connection> {
    let connection = Connection::open_with_flags(path, flags)?;
    connection.busy_timeout(Duration::from_secs(5))?;
    connection.pragma_update(None, "foreign_keys", true)?;
    Ok(connection)
}

fn create_verified_backup(
    source: &Connection,
    source_path: &Path,
    backup_dir: &Path,
    schema_version: i64,
) -> Result<BackupMetadata, AgentStoreError> {
    fs::create_dir_all(backup_dir)
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let created_at = Utc::now();
    let stem = format!(
        "agent-platform-v{schema_version}-{}-{}",
        created_at.format("%Y%m%dT%H%M%S%.3fZ"),
        Uuid::new_v4()
    );
    let backup_path = backup_dir.join(format!("{stem}.sqlite3"));
    let metadata_path = backup_dir.join(format!("{stem}.metadata.json"));
    source
        .backup(MAIN_DB, &backup_path, None)
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let backup_connection = configured_connection(
        &backup_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
    )
    .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let mut integrity_statement = backup_connection
        .prepare("PRAGMA integrity_check")
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let integrity = integrity_statement
        .query_map([], |row| row.get::<_, String>(0))
        .and_then(|rows| rows.collect::<Result<Vec<_>, _>>())
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    if integrity != ["ok"] {
        return Err(AgentStoreError::recovery_required(
            source_path.to_path_buf(),
            None,
        ));
    }
    drop(integrity_statement);
    drop(backup_connection);

    let bytes = fs::read(&backup_path)
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let byte_length = bytes.len() as u64;
    let sha256 = hex::encode(Sha256::digest(&bytes));
    let metadata = BackupMetadata {
        schema_version,
        created_at,
        source_path: source_path.to_path_buf(),
        backup_path,
        metadata_path: metadata_path.clone(),
        sha256,
        byte_length,
    };
    write_atomic(&metadata_path, &metadata).map_err(|_| {
        AgentStoreError::recovery_required(source_path.to_path_buf(), Some(metadata.clone()))
    })?;
    Ok(metadata)
}

#[cfg(test)]
mod tests {
    use std::{fs, sync::Arc, thread};

    use rusqlite::Connection;
    use sha2::{Digest, Sha256};

    use super::AgentStore;
    use crate::storage::app_paths::AppPaths;

    const V0_FIXTURE: &str = include_str!("fixtures/v0.sql");
    const REQUIRED_TABLES: &[&str] = &[
        "providers",
        "agents",
        "tasks",
        "worker_sessions",
        "event_checkpoints",
        "content_references",
        "approvals",
        "task_grants",
        "extensions",
        "extension_versions",
        "compatibility_results",
        "credential_metadata",
        "audit_summaries",
    ];

    fn paths(dir: &tempfile::TempDir) -> AppPaths {
        let paths = AppPaths::from_roots(dir.path().join("app-data"), dir.path().join("resources"));
        paths.create_owned_directories().unwrap();
        paths
    }

    fn user_version(connection: &Connection) -> i64 {
        connection
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap()
    }

    fn table_names(connection: &Connection) -> Vec<String> {
        let mut statement = connection
            .prepare(
                "SELECT name FROM sqlite_master \
                 WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            )
            .unwrap();
        statement
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap()
    }

    fn sha256(path: &std::path::Path) -> String {
        let mut digest = Sha256::new();
        digest.update(fs::read(path).unwrap());
        hex::encode(digest.finalize())
    }

    #[test]
    fn clean_start_creates_v1_at_fixed_paths_with_safe_pragmas() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);

        let store = AgentStore::open(&paths).unwrap();
        let connection = store.reader().unwrap();

        assert_eq!(
            store.database_path(),
            paths.state.join("agent-platform.sqlite3")
        );
        assert_eq!(store.backup_dir(), paths.backups.join("agent-platform"));
        assert_eq!(user_version(&connection), 1);
        assert_eq!(
            connection
                .query_row("PRAGMA foreign_keys", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            1
        );
        assert_eq!(
            connection
                .query_row("PRAGMA busy_timeout", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            5_000
        );
        let tables = table_names(&connection);
        for table in REQUIRED_TABLES {
            assert!(tables.iter().any(|value| value == table), "missing {table}");
        }
        assert!(store.migration_backup().is_none());
    }

    #[test]
    fn repeated_startup_is_idempotent_and_does_not_create_a_backup() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        AgentStore::open(&paths).unwrap();
        let before = fs::read(&paths.agent_database).unwrap();

        let reopened = AgentStore::open(&paths).unwrap();

        assert_eq!(user_version(&reopened.reader().unwrap()), 1);
        assert_eq!(fs::read(&paths.agent_database).unwrap(), before);
        assert!(reopened.migration_backup().is_none());
        assert!(!paths.agent_backups.exists());
    }

    #[test]
    fn v0_fixture_migrates_after_verified_backup_and_preserves_legacy_data() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let connection = Connection::open(&paths.agent_database).unwrap();
        connection.execute_batch(V0_FIXTURE).unwrap();
        drop(connection);

        let store = AgentStore::open(&paths).unwrap();
        let backup = store.migration_backup().expect("migration backup");
        let connection = store.reader().unwrap();

        assert_eq!(user_version(&connection), 1);
        assert_eq!(
            connection
                .query_row(
                    "SELECT setting_value FROM legacy_settings WHERE setting_key = ?1",
                    ["legacy-provider-mode"],
                    |row| row.get::<_, String>(0),
                )
                .unwrap(),
            "preserve-me"
        );
        assert_eq!(backup.source_path, paths.agent_database);
        assert!(backup.backup_path.starts_with(&paths.agent_backups));
        assert_eq!(backup.sha256, sha256(&backup.backup_path));
        assert_eq!(
            backup.byte_length,
            fs::metadata(&backup.backup_path).unwrap().len()
        );
        assert_eq!(backup.schema_version, 0);
        assert!(backup.metadata_path.exists());
        let backup_connection = Connection::open(&backup.backup_path).unwrap();
        assert_eq!(
            backup_connection
                .query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0))
                .unwrap(),
            "ok"
        );
        assert_eq!(user_version(&backup_connection), 0);
    }

    #[test]
    fn forced_migration_failure_rolls_back_and_returns_recoverable_backup() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let connection = Connection::open(&paths.agent_database).unwrap();
        connection
            .execute_batch(
                "PRAGMA user_version = 0;
                 CREATE TABLE providers (broken_fixture TEXT NOT NULL);
                 INSERT INTO providers VALUES ('preserve-me');",
            )
            .unwrap();
        drop(connection);

        let error = AgentStore::open(&paths).unwrap_err();
        let recovery = error.recovery().expect("blocking recovery state");

        assert_eq!(error.code(), "agent_store_migration_failed");
        assert_eq!(recovery.source_path, paths.agent_database);
        let backup = recovery.backup.as_ref().expect("verified backup");
        assert_eq!(backup.sha256, sha256(&backup.backup_path));
        assert!(backup.metadata_path.exists());
        let source = Connection::open(&paths.agent_database).unwrap();
        assert_eq!(user_version(&source), 0);
        assert_eq!(
            source
                .query_row("SELECT broken_fixture FROM providers", [], |row| {
                    row.get::<_, String>(0)
                })
                .unwrap(),
            "preserve-me"
        );
        assert!(!table_names(&source).iter().any(|name| name == "agents"));
        let untouched_backup = Connection::open(&backup.backup_path).unwrap();
        assert_eq!(
            untouched_backup
                .query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0))
                .unwrap(),
            "ok"
        );
        assert_eq!(user_version(&untouched_backup), 0);
    }

    #[test]
    fn corrupt_existing_database_is_preserved_without_replacement() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let corrupt = b"not a sqlite database; preserve these bytes";
        fs::write(&paths.agent_database, corrupt).unwrap();

        let error = AgentStore::open(&paths).unwrap_err();

        assert_eq!(error.code(), "agent_store_recovery_required");
        assert_eq!(fs::read(&paths.agent_database).unwrap(), corrupt);
        assert!(error.recovery().is_some());
    }

    #[test]
    fn independently_configured_connections_support_concurrent_readers() {
        let dir = tempfile::tempdir().unwrap();
        let store = Arc::new(AgentStore::open(&paths(&dir)).unwrap());
        let handles = (0..8)
            .map(|_| {
                let store = Arc::clone(&store);
                thread::spawn(move || {
                    let connection = store.reader().unwrap();
                    assert_eq!(user_version(&connection), 1);
                    assert_eq!(
                        connection
                            .query_row("SELECT COUNT(*) FROM tasks", [], |row| {
                                row.get::<_, i64>(0)
                            })
                            .unwrap(),
                        0
                    );
                })
            })
            .collect::<Vec<_>>();

        for handle in handles {
            handle.join().unwrap();
        }
    }

    #[test]
    fn schema_and_seeded_values_have_no_secret_bearing_columns_or_values() {
        let dir = tempfile::tempdir().unwrap();
        let store = AgentStore::open(&paths(&dir)).unwrap();
        let connection = store.reader().unwrap();
        let schema = connection
            .query_row(
                "SELECT group_concat(sql, '\n') FROM sqlite_master WHERE sql IS NOT NULL",
                [],
                |row| row.get::<_, String>(0),
            )
            .unwrap()
            .to_ascii_lowercase();

        for forbidden in [
            "api_key",
            "apikey",
            "password",
            "access_token",
            "secret_value",
        ] {
            assert!(!schema.contains(forbidden), "schema contains {forbidden}");
        }
        for table in REQUIRED_TABLES {
            let pragma = format!("PRAGMA table_info({table})");
            let mut statement = connection.prepare(&pragma).unwrap();
            let columns = statement
                .query_map([], |row| row.get::<_, String>(1))
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap();
            for column in columns {
                let column = column.to_ascii_lowercase();
                assert!(
                    !["secret", "token", "password", "authorization", "api_key"]
                        .iter()
                        .any(|forbidden| column.contains(forbidden)),
                    "secret-bearing column {table}.{column}"
                );
            }
        }
        let seeded_text = connection
            .query_row(
                "SELECT group_concat(name, '|') FROM sqlite_master",
                [],
                |row| row.get::<_, String>(0),
            )
            .unwrap();
        assert!(!seeded_text.contains("sk-unit-test-secret"));
    }
}
