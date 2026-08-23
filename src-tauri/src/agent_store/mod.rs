pub mod migrations;
pub mod model;

use std::{
    collections::HashSet,
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

#[derive(Default)]
struct StoreCoordinator {
    active_writers: HashSet<PathBuf>,
}

static STORE_COORDINATOR: OnceLock<Mutex<StoreCoordinator>> = OnceLock::new();

#[derive(Clone, Debug)]
pub struct AgentStore {
    database_path: PathBuf,
    backup_dir: PathBuf,
    migration_backup: Option<BackupMetadata>,
}

impl AgentStore {
    pub fn open(paths: &AppPaths) -> Result<Self, AgentStoreError> {
        let controller = STORE_COORDINATOR.get_or_init(|| Mutex::new(StoreCoordinator::default()));
        let coordinator = controller
            .lock()
            .map_err(|_| AgentStoreError::recovery_required(paths.agent_database.clone(), None))?;
        if coordinator.active_writers.contains(&paths.agent_database) {
            return Err(AgentStoreError::writer_active(
                paths.agent_database.clone(),
                None,
            ));
        }

        let existed = paths.agent_database.exists();
        if !existed {
            fs::create_dir_all(&paths.state).map_err(|_| {
                AgentStoreError::recovery_required(paths.agent_database.clone(), None)
            })?;
            let mut connection = configured_connection(
                &paths.agent_database,
                OpenFlags::SQLITE_OPEN_READ_WRITE
                    | OpenFlags::SQLITE_OPEN_CREATE
                    | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
            )
            .map_err(|_| AgentStoreError::recovery_required(paths.agent_database.clone(), None))?;
            if migrations::migrate_to_v1(&mut connection).is_err() {
                return Err(AgentStoreError::migration_failed(
                    paths.agent_database.clone(),
                    None,
                ));
            }
            return Ok(Self::ready(paths, None));
        }

        if has_unresolved_rollback_journal(&paths.agent_database) {
            return Err(AgentStoreError::recovery_required(
                paths.agent_database.clone(),
                None,
            ));
        }

        let probe = configured_connection(
            &paths.agent_database,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
        )
        .map_err(|error| map_probe_error(&paths.agent_database, error))?;
        let version = migrations::user_version(&probe)
            .map_err(|error| map_probe_error(&paths.agent_database, error))?;
        if version == migrations::CURRENT_SCHEMA_VERSION {
            return Ok(Self::ready(paths, None));
        }
        if version < 0 || version > migrations::CURRENT_SCHEMA_VERSION {
            return Err(AgentStoreError::recovery_required(
                paths.agent_database.clone(),
                None,
            ));
        }

        let backup = Some(create_verified_backup(
            &probe,
            &paths.agent_database,
            &paths.agent_backups,
            version,
        )?);
        drop(probe);

        let mut connection = configured_connection(
            &paths.agent_database,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
        )
        .map_err(|_| {
            AgentStoreError::migration_failed(paths.agent_database.clone(), backup.clone())
        })?;
        if let Err(error) = migrations::migrate_to_v1(&mut connection) {
            return Err(if is_lock_error(&error) {
                AgentStoreError::writer_active(paths.agent_database.clone(), backup)
            } else {
                AgentStoreError::migration_failed(paths.agent_database.clone(), backup)
            });
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

    pub fn writer(&self) -> Result<AgentStoreWriter, AgentStoreError> {
        let controller = STORE_COORDINATOR.get_or_init(|| Mutex::new(StoreCoordinator::default()));
        let mut coordinator = controller
            .lock()
            .map_err(|_| AgentStoreError::recovery_required(self.database_path.clone(), None))?;
        if coordinator.active_writers.contains(&self.database_path) {
            return Err(AgentStoreError::writer_active(
                self.database_path.clone(),
                None,
            ));
        }
        let connection = configured_connection(
            &self.database_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
        )
        .map_err(|_| AgentStoreError::recovery_required(self.database_path.clone(), None))?;
        coordinator
            .active_writers
            .insert(self.database_path.clone());
        Ok(AgentStoreWriter {
            database_path: self.database_path.clone(),
            connection,
        })
    }
}

pub struct AgentStoreWriter {
    database_path: PathBuf,
    connection: Connection,
}

impl AgentStoreWriter {
    pub fn connection(&self) -> &Connection {
        &self.connection
    }
}

impl Drop for AgentStoreWriter {
    fn drop(&mut self) {
        if let Ok(mut coordinator) = STORE_COORDINATOR
            .get_or_init(|| Mutex::new(StoreCoordinator::default()))
            .lock()
        {
            coordinator.active_writers.remove(&self.database_path);
        }
    }
}

fn has_unresolved_rollback_journal(database_path: &Path) -> bool {
    let mut journal = database_path.as_os_str().to_os_string();
    journal.push("-journal");
    fs::metadata(PathBuf::from(journal)).is_ok_and(|metadata| metadata.len() > 0)
}

fn map_probe_error(database_path: &Path, error: rusqlite::Error) -> AgentStoreError {
    if is_lock_error(&error) {
        AgentStoreError::writer_active(database_path.to_path_buf(), None)
    } else {
        AgentStoreError::recovery_required(database_path.to_path_buf(), None)
    }
}

fn is_lock_error(error: &rusqlite::Error) -> bool {
    matches!(
        error,
        rusqlite::Error::SqliteFailure(code, _)
            if matches!(
                code.code,
                rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked
            )
    )
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
    use std::{
        fs,
        path::{Path, PathBuf},
        sync::Arc,
        thread,
    };

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

    fn quote_identifier(value: &str) -> String {
        format!("\"{}\"", value.replace('"', "\"\""))
    }

    fn all_text_values(connection: &Connection) -> Vec<String> {
        let mut values = Vec::new();
        for table in table_names(connection) {
            let table_identifier = quote_identifier(&table);
            let mut columns = connection
                .prepare(&format!("PRAGMA table_info({table_identifier})"))
                .unwrap();
            let text_columns = columns
                .query_map([], |row| {
                    Ok((row.get::<_, String>(1)?, row.get::<_, String>(2)?))
                })
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap()
                .into_iter()
                .filter_map(|(name, declared_type)| {
                    let declared_type = declared_type.to_ascii_uppercase();
                    (declared_type.contains("CHAR")
                        || declared_type.contains("CLOB")
                        || declared_type.contains("TEXT"))
                    .then_some(name)
                })
                .collect::<Vec<_>>();
            drop(columns);
            for column in text_columns {
                let query = format!(
                    "SELECT {} FROM {table_identifier} WHERE {} IS NOT NULL",
                    quote_identifier(&column),
                    quote_identifier(&column)
                );
                let mut statement = connection.prepare(&query).unwrap();
                values.extend(
                    statement
                        .query_map([], |row| row.get::<_, String>(0))
                        .unwrap()
                        .collect::<Result<Vec<_>, _>>()
                        .unwrap(),
                );
            }
        }
        values
    }

    fn sha256(path: &std::path::Path) -> String {
        let mut digest = Sha256::new();
        digest.update(fs::read(path).unwrap());
        hex::encode(digest.finalize())
    }

    fn sqlite_sidecar(path: &Path, suffix: &str) -> PathBuf {
        let mut value = path.as_os_str().to_os_string();
        value.push(suffix);
        PathBuf::from(value)
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
        let source_before = fs::read(&paths.agent_database).unwrap();
        let source_hash_before = sha256(&paths.agent_database);

        let error = AgentStore::open(&paths).unwrap_err();
        let recovery = error.recovery().expect("blocking recovery state");

        assert_eq!(error.code(), "agent_store_migration_failed");
        assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);
        assert_eq!(sha256(&paths.agent_database), source_hash_before);
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

    #[cfg(unix)]
    #[test]
    fn existing_v1_database_is_probed_read_only_without_requiring_write_access() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        AgentStore::open(&paths).unwrap();
        let before = fs::read(&paths.agent_database).unwrap();
        fs::set_permissions(&paths.agent_database, fs::Permissions::from_mode(0o444)).unwrap();

        let reopened = AgentStore::open(&paths).unwrap();
        assert_eq!(user_version(&reopened.reader().unwrap()), 1);
        assert_eq!(fs::read(&paths.agent_database).unwrap(), before);
        assert!(reopened.migration_backup().is_none());

        fs::set_permissions(&paths.agent_database, fs::Permissions::from_mode(0o600)).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn read_only_v0_source_is_backed_up_before_read_write_migration_is_attempted() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let connection = Connection::open(&paths.agent_database).unwrap();
        connection.execute_batch(V0_FIXTURE).unwrap();
        drop(connection);
        let source_before = fs::read(&paths.agent_database).unwrap();
        fs::set_permissions(&paths.agent_database, fs::Permissions::from_mode(0o444)).unwrap();

        let error = AgentStore::open(&paths).unwrap_err();
        let backup = error
            .recovery()
            .and_then(|state| state.backup.as_ref())
            .expect("verified backup must precede read-write migration open");

        assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);
        assert_eq!(backup.sha256, sha256(&backup.backup_path));
        assert!(backup.metadata_path.exists());
        let restored = Connection::open(&backup.backup_path).unwrap();
        assert_eq!(user_version(&restored), 0);
        assert_eq!(
            restored
                .query_row(
                    "SELECT setting_value FROM legacy_settings WHERE setting_key = ?1",
                    ["legacy-provider-mode"],
                    |row| row.get::<_, String>(0),
                )
                .unwrap(),
            "preserve-me"
        );

        fs::set_permissions(&paths.agent_database, fs::Permissions::from_mode(0o600)).unwrap();
    }

    #[test]
    fn application_writer_lease_blocks_migration_until_the_writer_is_closed() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let connection = Connection::open(&paths.agent_database).unwrap();
        connection.execute_batch(V0_FIXTURE).unwrap();
        drop(connection);
        let store = AgentStore::ready(&paths, None);
        let writer = store.writer().unwrap();
        assert_eq!(user_version(writer.connection()), 0);
        let source_before = fs::read(&paths.agent_database).unwrap();

        let error = AgentStore::open(&paths).unwrap_err();

        assert_eq!(error.code(), "agent_store_writer_active");
        assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);
        assert!(!paths.agent_backups.exists());

        drop(writer);
        let migrated = AgentStore::open(&paths).unwrap();
        assert_eq!(user_version(&migrated.reader().unwrap()), 1);
        assert!(migrated.migration_backup().is_some());
    }

    #[test]
    fn external_active_writer_fails_closed_before_backup_or_source_mutation() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let writer = Connection::open(&paths.agent_database).unwrap();
        writer.execute_batch(V0_FIXTURE).unwrap();
        writer.execute_batch("BEGIN EXCLUSIVE").unwrap();
        let source_before = fs::read(&paths.agent_database).unwrap();
        let source_hash_before = sha256(&paths.agent_database);

        let error = AgentStore::open(&paths).unwrap_err();

        assert_eq!(error.code(), "agent_store_writer_active");
        assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);
        assert_eq!(sha256(&paths.agent_database), source_hash_before);
        assert!(!paths.agent_backups.exists());
        writer.execute_batch("ROLLBACK").unwrap();
    }

    #[test]
    fn external_wal_writer_is_reported_active_without_source_or_wal_mutation() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let writer = Connection::open(&paths.agent_database).unwrap();
        assert_eq!(
            writer
                .query_row("PRAGMA journal_mode = WAL", [], |row| {
                    row.get::<_, String>(0)
                })
                .unwrap(),
            "wal"
        );
        writer.pragma_update(None, "wal_autocheckpoint", 0).unwrap();
        writer.execute_batch(V0_FIXTURE).unwrap();
        writer.execute_batch("BEGIN IMMEDIATE").unwrap();
        writer
            .execute(
                "INSERT INTO legacy_settings (setting_key, setting_value) VALUES (?1, ?2)",
                ["uncommitted", "preserve-uncommitted"],
            )
            .unwrap();
        let wal_path = sqlite_sidecar(&paths.agent_database, "-wal");
        let source_before = fs::read(&paths.agent_database).unwrap();
        let source_hash_before = sha256(&paths.agent_database);
        let wal_before = fs::read(&wal_path).unwrap();

        let error = AgentStore::open(&paths).unwrap_err();

        assert_eq!(error.code(), "agent_store_writer_active");
        assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);
        assert_eq!(sha256(&paths.agent_database), source_hash_before);
        assert_eq!(fs::read(&wal_path).unwrap(), wal_before);
        writer.execute_batch("ROLLBACK").unwrap();
    }

    #[test]
    fn hot_journal_blocks_open_without_recovery_or_source_mutation() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let connection = Connection::open(&paths.agent_database).unwrap();
        connection.execute_batch(V0_FIXTURE).unwrap();
        drop(connection);
        let journal = sqlite_sidecar(&paths.agent_database, "-journal");
        fs::write(&journal, b"unresolved-hot-journal").unwrap();
        let source_before = fs::read(&paths.agent_database).unwrap();
        let journal_before = fs::read(&journal).unwrap();

        let error = AgentStore::open(&paths).unwrap_err();

        assert_eq!(error.code(), "agent_store_recovery_required");
        assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);
        assert_eq!(fs::read(&journal).unwrap(), journal_before);
        assert!(!paths.agent_backups.exists());
    }

    #[test]
    fn committed_wal_database_is_backed_up_and_migrated_without_losing_rows() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let writer = Connection::open(&paths.agent_database).unwrap();
        assert_eq!(
            writer
                .query_row("PRAGMA journal_mode = WAL", [], |row| {
                    row.get::<_, String>(0)
                })
                .unwrap(),
            "wal"
        );
        writer.pragma_update(None, "wal_autocheckpoint", 0).unwrap();
        writer.execute_batch(V0_FIXTURE).unwrap();
        let reader = Connection::open(&paths.agent_database).unwrap();
        reader.execute_batch("BEGIN").unwrap();
        assert_eq!(
            reader
                .query_row("SELECT COUNT(*) FROM legacy_settings", [], |row| {
                    row.get::<_, i64>(0)
                })
                .unwrap(),
            1
        );
        drop(writer);
        assert!(sqlite_sidecar(&paths.agent_database, "-wal").exists());

        let store = AgentStore::open(&paths).unwrap();

        let migrated = store.reader().unwrap();
        assert_eq!(user_version(&migrated), 1);
        assert_eq!(
            migrated
                .query_row(
                    "SELECT setting_value FROM legacy_settings WHERE setting_key = ?1",
                    ["legacy-provider-mode"],
                    |row| row.get::<_, String>(0),
                )
                .unwrap(),
            "preserve-me"
        );
        let backup = store.migration_backup().expect("verified WAL backup");
        let backup_connection = Connection::open(&backup.backup_path).unwrap();
        assert_eq!(user_version(&backup_connection), 0);
        assert_eq!(
            backup_connection
                .query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0))
                .unwrap(),
            "ok"
        );
        drop(reader);
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
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let store = AgentStore::open(&paths).unwrap();
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
        let seeded_values = all_text_values(&connection);
        assert!(seeded_values.iter().any(|value| value == "preserve-me"));
        for value in seeded_values {
            let value = value.to_ascii_lowercase();
            assert!(!value.contains("sk-unit-test-secret"));
            assert!(!value.contains("api_key"));
            assert!(!value.contains("access_token"));
            assert!(!value.contains("password"));
        }
    }
}
