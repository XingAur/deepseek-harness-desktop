pub mod migrations;
pub mod model;

use std::{
    collections::HashSet,
    ffi::OsStr,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::Duration,
};

use chrono::Utc;
use model::{AgentStoreError, BackupMetadata, RecoveryState};
use rusqlite::{Connection, OpenFlags, TransactionBehavior, MAIN_DB};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::storage::app_paths::AppPaths;

trait BackupFilesystem {
    fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()>;
    fn sync_file(&self, path: &Path) -> std::io::Result<()>;
    fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()>;
    fn rename_bundle(
        &self,
        parent: &TrustedDirectory,
        from: &Path,
        to: &Path,
    ) -> std::io::Result<BundlePublication>;
    fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()>;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BundlePublication {
    Verified,
    DurabilityUncertain,
}

struct RealBackupFilesystem;

impl BackupFilesystem for RealBackupFilesystem {
    fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()> {
        let mut file = File::create(path)?;
        file.write_all(bytes)?;
        Ok(())
    }

    fn sync_file(&self, path: &Path) -> std::io::Result<()> {
        OpenOptions::new()
            .read(true)
            .write(true)
            .open(path)?
            .sync_all()
    }

    fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
        sync_directory(path)
    }

    fn rename_bundle(
        &self,
        parent: &TrustedDirectory,
        from: &Path,
        to: &Path,
    ) -> std::io::Result<BundlePublication> {
        atomic_publish_bundle(parent, from, to)
    }

    fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()> {
        sync_directory(path)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct FileIdentity {
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    #[cfg(unix)]
    owner: u32,
    #[cfg(windows)]
    volume: Option<u32>,
    #[cfg(windows)]
    index: Option<u64>,
}

struct TrustedDirectory {
    path: PathBuf,
    handle: File,
    identity: FileIdentity,
}

struct OwnedBundle {
    path: PathBuf,
    identity: FileIdentity,
    owner_token: String,
}

fn metadata_identity(metadata: &fs::Metadata) -> FileIdentity {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        FileIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
            owner: metadata.uid(),
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        FileIdentity {
            volume: metadata.volume_serial_number(),
            index: metadata.file_index(),
        }
    }
}

fn path_identity(path: &Path) -> std::io::Result<FileIdentity> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(std::io::Error::other("untrusted bundle path"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes()
            & windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT
            != 0
        {
            return Err(std::io::Error::other("untrusted bundle reparse point"));
        }
    }
    Ok(metadata_identity(&metadata))
}

fn open_trusted_directory(path: &Path) -> std::io::Result<TrustedDirectory> {
    let expected = path_identity(path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(
            windows_sys::Win32::Storage::FileSystem::FILE_FLAG_BACKUP_SEMANTICS
                | windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT,
        );
    }
    let handle = options.open(path)?;
    let identity = metadata_identity(&handle.metadata()?);
    if identity != expected {
        return Err(std::io::Error::other("backup directory identity changed"));
    }
    Ok(TrustedDirectory {
        path: path.to_path_buf(),
        handle,
        identity,
    })
}

fn ensure_trusted_directory(directory: &TrustedDirectory) -> std::io::Result<()> {
    if metadata_identity(&directory.handle.metadata()?) != directory.identity
        || path_identity(&directory.path)? != directory.identity
    {
        return Err(std::io::Error::other("backup directory identity changed"));
    }
    Ok(())
}

fn child_name<'a>(parent: &Path, child: &'a Path) -> std::io::Result<&'a OsStr> {
    if child.parent() != Some(parent) {
        return Err(std::io::Error::other("bundle escaped trusted directory"));
    }
    child
        .file_name()
        .filter(|name| !name.is_empty() && *name != OsStr::new(".") && *name != OsStr::new(".."))
        .ok_or_else(|| std::io::Error::other("invalid bundle name"))
}

#[cfg(any(target_os = "macos", target_os = "linux"))]
fn relative_c_string(parent: &Path, child: &Path) -> std::io::Result<std::ffi::CString> {
    use std::os::unix::ffi::OsStrExt;
    std::ffi::CString::new(child_name(parent, child)?.as_bytes())
        .map_err(|_| std::io::Error::other("bundle name contains NUL"))
}

#[cfg(target_os = "macos")]
fn atomic_publish_bundle(
    parent: &TrustedDirectory,
    from: &Path,
    to: &Path,
) -> std::io::Result<BundlePublication> {
    use std::os::fd::AsRawFd;
    let source_identity = path_identity(from)?;
    let from_name = relative_c_string(&parent.path, from)?;
    let to_name = relative_c_string(&parent.path, to)?;
    ensure_trusted_directory(parent)?;
    let result = unsafe {
        libc::renameatx_np(
            parent.handle.as_raw_fd(),
            from_name.as_ptr(),
            parent.handle.as_raw_fd(),
            to_name.as_ptr(),
            libc::RENAME_EXCL,
        )
    };
    if result == 0 {
        Ok(
            if ensure_trusted_directory(parent).is_ok()
                && path_identity(to).is_ok_and(|identity| identity == source_identity)
            {
                BundlePublication::Verified
            } else {
                BundlePublication::DurabilityUncertain
            },
        )
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(target_os = "linux")]
fn atomic_publish_bundle(
    parent: &TrustedDirectory,
    from: &Path,
    to: &Path,
) -> std::io::Result<BundlePublication> {
    use std::os::fd::AsRawFd;
    let source_identity = path_identity(from)?;
    let from_name = relative_c_string(&parent.path, from)?;
    let to_name = relative_c_string(&parent.path, to)?;
    ensure_trusted_directory(parent)?;
    let result = unsafe {
        libc::renameat2(
            parent.handle.as_raw_fd(),
            from_name.as_ptr(),
            parent.handle.as_raw_fd(),
            to_name.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    if result == 0 {
        Ok(
            if ensure_trusted_directory(parent).is_ok()
                && path_identity(to).is_ok_and(|identity| identity == source_identity)
            {
                BundlePublication::Verified
            } else {
                BundlePublication::DurabilityUncertain
            },
        )
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(all(unix, not(any(target_os = "macos", target_os = "linux"))))]
fn atomic_publish_bundle(
    _parent: &TrustedDirectory,
    _from: &Path,
    _to: &Path,
) -> std::io::Result<BundlePublication> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "atomic no-replace bundle publication is unavailable",
    ))
}

#[cfg(windows)]
fn atomic_publish_bundle(
    parent: &TrustedDirectory,
    from: &Path,
    to: &Path,
) -> std::io::Result<BundlePublication> {
    use std::os::windows::{ffi::OsStrExt, fs::OpenOptionsExt, io::AsRawHandle};
    use windows_sys::Win32::Storage::FileSystem::{
        FileRenameInfo, SetFileInformationByHandle, DELETE, FILE_FLAG_BACKUP_SEMANTICS,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_WRITE_THROUGH, FILE_RENAME_INFO, SYNCHRONIZE,
    };

    ensure_trusted_directory(parent)?;
    child_name(&parent.path, from)?;
    let target: Vec<u16> = child_name(&parent.path, to)?.encode_wide().collect();
    let mut source_options = OpenOptions::new();
    source_options
        .read(true)
        .access_mode(DELETE | SYNCHRONIZE)
        .custom_flags(
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH,
        );
    let source = source_options.open(from)?;
    let source_identity = metadata_identity(&source.metadata()?);
    if path_identity(from)? != source_identity {
        return Err(std::io::Error::other("temporary bundle identity changed"));
    }

    let header = std::mem::offset_of!(FILE_RENAME_INFO, FileName);
    let byte_len = target.len() * std::mem::size_of::<u16>();
    let total = header + byte_len;
    let mut storage = vec![0_usize; total.div_ceil(std::mem::size_of::<usize>())];
    let info = storage.as_mut_ptr().cast::<FILE_RENAME_INFO>();
    unsafe {
        (*info).Anonymous.ReplaceIfExists = false;
        (*info).RootDirectory = parent.handle.as_raw_handle().cast();
        (*info).FileNameLength = byte_len as u32;
        std::ptr::copy_nonoverlapping(
            target.as_ptr(),
            std::ptr::addr_of_mut!((*info).FileName).cast::<u16>(),
            target.len(),
        );
    }
    let result = unsafe {
        SetFileInformationByHandle(
            source.as_raw_handle().cast(),
            FileRenameInfo,
            info.cast(),
            total as u32,
        )
    };
    if result == 0 {
        return Err(std::io::Error::last_os_error());
    }
    // Windows has no supported directory-entry flush boundary here. The
    // published bundle remains available for the caller's identity-bound
    // locate and revalidation path, but must never be reported as durable.
    let _published_identity_is_current = ensure_trusted_directory(parent).is_ok()
        && path_identity(to).is_ok_and(|identity| identity == source_identity);
    Ok(BundlePublication::DurabilityUncertain)
}

fn owned_bundle(path: PathBuf, owner_token: String) -> std::io::Result<OwnedBundle> {
    if !path
        .file_name()
        .is_some_and(|name| name.to_string_lossy().contains(&owner_token))
    {
        return Err(std::io::Error::other("bundle owner token mismatch"));
    }
    Ok(OwnedBundle {
        identity: path_identity(&path)?,
        path,
        owner_token,
    })
}

fn owned_bundle_is_current(bundle: &OwnedBundle) -> bool {
    bundle
        .path
        .file_name()
        .is_some_and(|name| name.to_string_lossy().contains(&bundle.owner_token))
        && path_identity(&bundle.path).is_ok_and(|identity| identity == bundle.identity)
}

fn cleanup_owned_bundle(bundle: &OwnedBundle) -> std::io::Result<()> {
    if !owned_bundle_is_current(bundle) {
        return Err(std::io::Error::other("refusing to clean an unowned bundle"));
    }
    fs::remove_dir_all(&bundle.path)
}

const MAX_PUBLISHED_BUNDLE_SCAN_ENTRIES: usize = 256;

fn locate_owned_bundle(
    parent: &TrustedDirectory,
    bundle: &OwnedBundle,
) -> std::io::Result<PathBuf> {
    ensure_trusted_directory(parent)?;
    let mut found = None;
    for (index, entry) in fs::read_dir(&parent.path)?.enumerate() {
        if index >= MAX_PUBLISHED_BUNDLE_SCAN_ENTRIES {
            return Err(std::io::Error::other(
                "published bundle scan limit exceeded",
            ));
        }
        let path = entry?.path();
        if !path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().contains(&bundle.owner_token))
            || !path_identity(&path).is_ok_and(|identity| identity == bundle.identity)
        {
            continue;
        }
        if found.replace(path).is_some() {
            return Err(std::io::Error::other(
                "published bundle identity is ambiguous",
            ));
        }
    }
    ensure_trusted_directory(parent)?;
    found.ok_or_else(|| std::io::Error::other("published bundle evidence lost"))
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> std::io::Result<()> {
    File::open(path)?.sync_all()
}

#[cfg(windows)]
fn sync_directory(path: &Path) -> std::io::Result<()> {
    // Windows does not expose a reliable directory-fsync equivalent for these handles.
    // Regular bundle files are flushed before the write-through, handle-relative rename;
    // this boundary only revalidates that the directory is not a reparse point.
    open_trusted_directory(path).map(drop)
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BackupFailurePoint {
    WriteSidecar,
    SyncFile,
    SyncBundleDirectory,
    RenameBundle,
    SyncBackupDirectory,
}

#[cfg(test)]
struct FaultingBackupFilesystem {
    point: BackupFailurePoint,
}

#[cfg(test)]
impl FaultingBackupFilesystem {
    fn new(point: BackupFailurePoint) -> Self {
        Self { point }
    }

    fn fail(&self, point: BackupFailurePoint) -> std::io::Result<()> {
        if self.point == point {
            Err(std::io::Error::other("injected backup publication failure"))
        } else {
            Ok(())
        }
    }
}

#[cfg(test)]
impl BackupFilesystem for FaultingBackupFilesystem {
    fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()> {
        self.fail(BackupFailurePoint::WriteSidecar)?;
        RealBackupFilesystem.write_sidecar(path, bytes)
    }

    fn sync_file(&self, path: &Path) -> std::io::Result<()> {
        self.fail(BackupFailurePoint::SyncFile)?;
        RealBackupFilesystem.sync_file(path)
    }

    fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
        self.fail(BackupFailurePoint::SyncBundleDirectory)?;
        RealBackupFilesystem.sync_bundle_directory(path)
    }

    fn rename_bundle(
        &self,
        parent: &TrustedDirectory,
        from: &Path,
        to: &Path,
    ) -> std::io::Result<BundlePublication> {
        self.fail(BackupFailurePoint::RenameBundle)?;
        RealBackupFilesystem.rename_bundle(parent, from, to)
    }

    fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()> {
        self.fail(BackupFailurePoint::SyncBackupDirectory)?;
        RealBackupFilesystem.sync_backup_directory(path)
    }
}

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
        Self::open_with_backup_filesystem(paths, &RealBackupFilesystem)
    }

    fn open_with_backup_filesystem(
        paths: &AppPaths,
        backup_filesystem: &dyn BackupFilesystem,
    ) -> Result<Self, AgentStoreError> {
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
            if migrations::migrate_to_current(&mut connection).is_err()
                || migrations::validate_current_schema(&connection).is_err()
            {
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
            if migrations::validate_current_schema(&probe).is_err() {
                return Err(AgentStoreError::recovery_required(
                    paths.agent_database.clone(),
                    None,
                ));
            }
            return Ok(Self::ready(paths, None));
        }
        if version < 0 || version > migrations::CURRENT_SCHEMA_VERSION {
            return Err(AgentStoreError::recovery_required(
                paths.agent_database.clone(),
                None,
            ));
        }

        drop(probe);

        let mut connection = configured_connection(
            &paths.agent_database,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
        )
        .map_err(|_| AgentStoreError::writer_active(paths.agent_database.clone(), None))?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| {
                if is_lock_error(&error) {
                    AgentStoreError::writer_active(paths.agent_database.clone(), None)
                } else {
                    AgentStoreError::migration_failed(paths.agent_database.clone(), None)
                }
            })?;
        let locked_version = migrations::user_version(&transaction)
            .map_err(|_| AgentStoreError::migration_failed(paths.agent_database.clone(), None))?;
        if locked_version != version {
            return Err(AgentStoreError::writer_active(
                paths.agent_database.clone(),
                None,
            ));
        }
        let snapshot = configured_connection(
            &paths.agent_database,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
        )
        .map_err(|_| AgentStoreError::migration_failed(paths.agent_database.clone(), None))?;
        let backup = Some(create_verified_backup(
            &snapshot,
            &paths.agent_database,
            &paths.agent_backups,
            version,
            backup_filesystem,
        )?);
        drop(snapshot);
        if let Err(error) = migrations::migrate_to_current_in_transaction(&transaction) {
            return Err(if is_lock_error(&error) {
                AgentStoreError::writer_active(paths.agent_database.clone(), backup)
            } else {
                AgentStoreError::migration_failed(paths.agent_database.clone(), backup)
            });
        }
        if let Err(_error) = migrations::validate_current_schema(&transaction) {
            return Err(AgentStoreError::migration_failed(
                paths.agent_database.clone(),
                backup,
            ));
        }
        if let Err(error) = transaction.commit() {
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

    pub fn connection_mut(&mut self) -> &mut Connection {
        &mut self.connection
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
    backup_filesystem: &dyn BackupFilesystem,
) -> Result<BackupMetadata, AgentStoreError> {
    fs::create_dir_all(backup_dir)
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let trusted_backup_dir = open_trusted_directory(backup_dir)
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let created_at = Utc::now();
    let owner_token = Uuid::new_v4().to_string();
    let stem = format!(
        "agent-platform-v{schema_version}-{}-{}",
        created_at.format("%Y%m%dT%H%M%S%.3fZ"),
        owner_token
    );
    let final_bundle = backup_dir.join(&stem);
    let temporary_bundle = backup_dir.join(format!(".{stem}.tmp"));
    fs::create_dir(&temporary_bundle)
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let mut owned = owned_bundle(temporary_bundle.clone(), owner_token.clone())
        .map_err(|_| AgentStoreError::recovery_required(source_path.to_path_buf(), None))?;
    let temporary_backup = temporary_bundle.join("agent-platform.sqlite3");
    let temporary_metadata = temporary_bundle.join("metadata.json");
    let backup_path = final_bundle.join("agent-platform.sqlite3");
    let metadata_path = final_bundle.join("metadata.json");
    let fail = |bundle: &OwnedBundle| {
        let _ = cleanup_owned_bundle(bundle);
        AgentStoreError::recovery_required(source_path.to_path_buf(), None)
    };
    source
        .backup(MAIN_DB, &temporary_backup, None)
        .map_err(|_| fail(&owned))?;
    let backup_connection = configured_connection(
        &temporary_backup,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
    )
    .map_err(|_| fail(&owned))?;
    let mut integrity_statement = backup_connection
        .prepare("PRAGMA integrity_check")
        .map_err(|_| fail(&owned))?;
    let integrity = integrity_statement
        .query_map([], |row| row.get::<_, String>(0))
        .and_then(|rows| rows.collect::<Result<Vec<_>, _>>())
        .map_err(|_| fail(&owned))?;
    if integrity != ["ok"] {
        return Err(fail(&owned));
    }
    drop(integrity_statement);
    drop(backup_connection);

    let (byte_length, sha256) = stream_sha256(&temporary_backup).map_err(|_| fail(&owned))?;
    let metadata = BackupMetadata {
        schema_version,
        created_at,
        owner_token,
        source_path: source_path.to_path_buf(),
        backup_path,
        metadata_path: metadata_path.clone(),
        sha256,
        byte_length,
    };
    let sidecar = serde_json::to_vec_pretty(&metadata).map_err(|_| fail(&owned))?;
    backup_filesystem
        .write_sidecar(&temporary_metadata, &sidecar)
        .map_err(|_| fail(&owned))?;
    backup_filesystem
        .sync_file(&temporary_backup)
        .and_then(|_| backup_filesystem.sync_file(&temporary_metadata))
        .and_then(|_| backup_filesystem.sync_bundle_directory(&temporary_bundle))
        .map_err(|_| fail(&owned))?;
    ensure_trusted_directory(&trusted_backup_dir).map_err(|_| fail(&owned))?;
    let publication = backup_filesystem
        .rename_bundle(&trusted_backup_dir, &temporary_bundle, &final_bundle)
        .map_err(|_| fail(&owned))?;
    owned.path = final_bundle.clone();
    if publication == BundlePublication::DurabilityUncertain
        || !owned_bundle_is_current(&owned)
        || ensure_trusted_directory(&trusted_backup_dir).is_err()
    {
        return Err(published_backup_error(
            source_path,
            backup_dir,
            &trusted_backup_dir,
            &owned,
            &metadata,
        ));
    }
    if backup_filesystem.sync_backup_directory(backup_dir).is_err() {
        return Err(published_backup_error(
            source_path,
            backup_dir,
            &trusted_backup_dir,
            &owned,
            &metadata,
        ));
    }
    if !owned_bundle_is_current(&owned) || ensure_trusted_directory(&trusted_backup_dir).is_err() {
        return Err(published_backup_error(
            source_path,
            backup_dir,
            &trusted_backup_dir,
            &owned,
            &metadata,
        ));
    }
    resolve_published_metadata(backup_dir, &trusted_backup_dir, &owned, &metadata)
        .map_err(|_| AgentStoreError::backup_evidence_lost(source_path.to_path_buf()))
}

fn rebound_metadata(metadata: &BackupMetadata, bundle: &Path) -> BackupMetadata {
    let mut rebound = metadata.clone();
    rebound.backup_path = bundle.join("agent-platform.sqlite3");
    rebound.metadata_path = bundle.join("metadata.json");
    rebound
}

fn resolve_published_metadata(
    backup_root: &Path,
    trusted_backup_root: &TrustedDirectory,
    owned: &OwnedBundle,
    metadata: &BackupMetadata,
) -> Result<BackupMetadata, ()> {
    let bundle = locate_owned_bundle(trusted_backup_root, owned).map_err(|_| ())?;
    let rebound = rebound_metadata(metadata, &bundle);
    let recovery = RecoveryState {
        source_path: metadata.source_path.clone(),
        backup: Some(rebound.clone()),
    };
    validate_recovery_state_in_root(backup_root, &recovery, |_| {}).map_err(|_| ())?;
    Ok(rebound)
}

fn published_backup_error(
    source_path: &Path,
    backup_root: &Path,
    trusted_backup_root: &TrustedDirectory,
    owned: &OwnedBundle,
    metadata: &BackupMetadata,
) -> AgentStoreError {
    match resolve_published_metadata(backup_root, trusted_backup_root, owned, metadata) {
        Ok(rebound) => {
            AgentStoreError::backup_durability_uncertain(source_path.to_path_buf(), rebound)
        }
        Err(()) => AgentStoreError::backup_evidence_lost(source_path.to_path_buf()),
    }
}

fn stream_sha256(path: &Path) -> std::io::Result<(u64, String)> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut length = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
        length += read as u64;
    }
    Ok((length, hex::encode(digest.finalize())))
}

#[cfg(test)]
fn identifier_words(value: &str) -> Vec<String> {
    let characters = value.chars().collect::<Vec<_>>();
    let mut words = Vec::new();
    let mut current = String::new();
    for (index, character) in characters.iter().copied().enumerate() {
        if !character.is_ascii_alphanumeric() {
            if !current.is_empty() {
                words.push(std::mem::take(&mut current));
            }
            continue;
        }
        let previous = index.checked_sub(1).and_then(|value| characters.get(value));
        let next = characters.get(index + 1);
        let camel_boundary = character.is_ascii_uppercase()
            && !current.is_empty()
            && (previous.is_some_and(|value| value.is_ascii_lowercase() || value.is_ascii_digit())
                || (previous.is_some_and(|value| value.is_ascii_uppercase())
                    && next.is_some_and(|value| value.is_ascii_lowercase())));
        if camel_boundary {
            words.push(std::mem::take(&mut current));
        }
        current.push(character.to_ascii_lowercase());
    }
    if !current.is_empty() {
        words.push(current);
    }
    words
}

#[cfg(test)]
fn contains_secret_bearing_term(value: &str) -> bool {
    let words = identifier_words(value);
    if words.iter().any(|word| {
        matches!(
            word.as_str(),
            "secret" | "password" | "token" | "authorization" | "bearer"
        ) || matches!(
            word.as_str(),
            "secretvalue"
                | "sessiontoken"
                | "accesstoken"
                | "clientsecret"
                | "refreshtoken"
                | "apikey"
                | "privatekey"
        )
    }) {
        return true;
    }
    words.windows(2).any(|pair| {
        matches!(
            (pair[0].as_str(), pair[1].as_str()),
            ("secret", "value")
                | ("session", "token")
                | ("access", "token")
                | ("client", "secret")
                | ("refresh", "token")
                | ("api", "key")
                | ("private", "key")
        )
    })
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct FileSnapshot {
    identity: FileIdentity,
    byte_length: u64,
    #[cfg(unix)]
    mode: u32,
    #[cfg(unix)]
    links: u64,
    #[cfg(unix)]
    group: u32,
    #[cfg(unix)]
    modified_seconds: i64,
    #[cfg(unix)]
    modified_nanoseconds: i64,
    #[cfg(unix)]
    changed_seconds: i64,
    #[cfg(unix)]
    changed_nanoseconds: i64,
    #[cfg(windows)]
    attributes: u32,
    #[cfg(windows)]
    created_at: u64,
    #[cfg(windows)]
    modified_at: u64,
    #[cfg(windows)]
    links: Option<u32>,
    #[cfg(windows)]
    changed_at: Option<u64>,
}

fn file_snapshot(metadata: &fs::Metadata) -> FileSnapshot {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        FileSnapshot {
            identity: metadata_identity(metadata),
            byte_length: metadata.len(),
            mode: metadata.mode(),
            links: metadata.nlink(),
            group: metadata.gid(),
            modified_seconds: metadata.mtime(),
            modified_nanoseconds: metadata.mtime_nsec(),
            changed_seconds: metadata.ctime(),
            changed_nanoseconds: metadata.ctime_nsec(),
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        FileSnapshot {
            identity: metadata_identity(metadata),
            byte_length: metadata.file_size(),
            attributes: metadata.file_attributes(),
            created_at: metadata.creation_time(),
            modified_at: metadata.last_write_time(),
            links: metadata.number_of_links(),
            changed_at: metadata.change_time(),
        }
    }
}

fn path_file_snapshot(path: &Path) -> std::io::Result<FileSnapshot> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(std::io::Error::other("untrusted recovery source path"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes()
            & windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT
            != 0
        {
            return Err(std::io::Error::other(
                "untrusted recovery source reparse point",
            ));
        }
    }
    Ok(file_snapshot(&metadata))
}

fn open_recovery_source(path: &Path) -> std::io::Result<(File, FileSnapshot)> {
    let expected = path_file_snapshot(path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(
            windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT
                | windows_sys::Win32::Storage::FileSystem::FILE_FLAG_SEQUENTIAL_SCAN,
        );
    }
    let file = options.open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() {
        return Err(std::io::Error::other("recovery source is not a file"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes()
            & windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT
            != 0
        {
            return Err(std::io::Error::other(
                "untrusted recovery source reparse point",
            ));
        }
    }
    let actual = file_snapshot(&metadata);
    if actual != expected {
        return Err(std::io::Error::other("recovery source identity changed"));
    }
    Ok((file, actual))
}

struct VerificationCopy {
    path: PathBuf,
    file: Option<File>,
    identity: FileIdentity,
    named: bool,
}

impl VerificationCopy {
    fn create(parent: &TrustedDirectory) -> std::io::Result<Self> {
        ensure_trusted_directory(parent)?;
        let path = parent
            .path
            .join(format!(".recovery-verification-{}.sqlite3", Uuid::new_v4()));
        let mut options = OpenOptions::new();
        options.read(true).write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options
                .mode(0o600)
                .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            use windows_sys::Win32::Storage::FileSystem::{
                FILE_ATTRIBUTE_TEMPORARY, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ,
            };
            options
                .share_mode(FILE_SHARE_READ)
                .custom_flags(FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_OPEN_REPARSE_POINT);
        }
        let file = options.open(&path)?;
        let identity = metadata_identity(&file.metadata()?);
        ensure_trusted_directory(parent)?;
        Ok(Self {
            path,
            file: Some(file),
            identity,
            named: true,
        })
    }

    fn file_mut(&mut self) -> std::io::Result<&mut File> {
        self.file
            .as_mut()
            .ok_or_else(|| std::io::Error::other("verification copy handle is closed"))
    }

    fn seal_for_sqlite(&mut self) -> std::io::Result<()> {
        #[cfg(unix)]
        {
            fs::remove_file(&self.path)?;
            self.named = false;
        }
        #[cfg(windows)]
        {
            if path_file_snapshot(&self.path)?.identity != self.identity {
                return Err(std::io::Error::other("verification copy identity changed"));
            }
        }
        Ok(())
    }

    #[cfg(unix)]
    fn sqlite_path(&self) -> std::io::Result<PathBuf> {
        use std::os::fd::AsRawFd;

        let file = self
            .file
            .as_ref()
            .ok_or_else(|| std::io::Error::other("verification copy handle is closed"))?;
        let descriptor_path = if cfg!(target_os = "linux") {
            PathBuf::from(format!("/proc/self/fd/{}", file.as_raw_fd()))
        } else {
            PathBuf::from(format!("/dev/fd/{}", file.as_raw_fd()))
        };
        #[cfg(not(target_os = "macos"))]
        {
            let descriptor_metadata = fs::metadata(&descriptor_path)?;
            if !descriptor_metadata.is_file()
                || metadata_identity(&descriptor_metadata) != self.identity
            {
                return Err(std::io::Error::other(
                    "SQLite descriptor path changed verification identity",
                ));
            }
        }
        Ok(PathBuf::from(format!(
            "file:{}?mode=ro&immutable=1",
            descriptor_path.display()
        )))
    }

    #[cfg(windows)]
    fn sqlite_path(&self) -> std::io::Result<PathBuf> {
        if path_file_snapshot(&self.path)?.identity != self.identity {
            return Err(std::io::Error::other("verification copy identity changed"));
        }
        Ok(self.path.clone())
    }

    fn identity_is_current(&self) -> bool {
        let handle_matches = self
            .file
            .as_ref()
            .and_then(|file| file.metadata().ok())
            .is_some_and(|metadata| metadata_identity(&metadata) == self.identity);
        if !handle_matches {
            return false;
        }
        #[cfg(unix)]
        {
            self.sqlite_path().is_ok()
        }
        #[cfg(windows)]
        {
            path_file_snapshot(&self.path).is_ok_and(|snapshot| snapshot.identity == self.identity)
        }
    }

    fn cleanup(mut self) -> std::io::Result<()> {
        #[cfg(unix)]
        {
            drop(self.file.take());
            if self.named {
                if path_file_snapshot(&self.path)?.identity != self.identity {
                    return Err(std::io::Error::other("verification copy identity changed"));
                }
                fs::remove_file(&self.path)?;
                self.named = false;
            }
        }
        #[cfg(windows)]
        {
            drop(self.file.take());
            delete_windows_verification(&self.path, &self.identity)?;
            self.named = false;
        }
        Ok(())
    }
}

impl Drop for VerificationCopy {
    fn drop(&mut self) {
        drop(self.file.take());
        if self.named {
            #[cfg(unix)]
            if path_file_snapshot(&self.path).is_ok_and(|value| value.identity == self.identity) {
                let _ = fs::remove_file(&self.path);
            }
            #[cfg(windows)]
            let _ = delete_windows_verification(&self.path, &self.identity);
        }
    }
}

#[cfg(windows)]
fn delete_windows_verification(path: &Path, identity: &FileIdentity) -> std::io::Result<()> {
    use std::os::windows::{fs::OpenOptionsExt, io::AsRawHandle};
    use windows_sys::Win32::Storage::FileSystem::{
        FileDispositionInfo, SetFileInformationByHandle, DELETE, FILE_DISPOSITION_INFO,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ, FILE_SHARE_WRITE, SYNCHRONIZE,
    };

    let mut options = OpenOptions::new();
    options
        .access_mode(DELETE | SYNCHRONIZE)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    let file = options.open(path)?;
    if metadata_identity(&file.metadata()?) != *identity
        || path_file_snapshot(path)?.identity != *identity
    {
        return Err(std::io::Error::other("verification copy identity changed"));
    }
    let disposition = FILE_DISPOSITION_INFO { DeleteFile: true };
    let result = unsafe {
        SetFileInformationByHandle(
            file.as_raw_handle().cast(),
            FileDispositionInfo,
            std::ptr::addr_of!(disposition).cast(),
            std::mem::size_of::<FILE_DISPOSITION_INFO>() as u32,
        )
    };
    if result == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

fn copy_and_hash(source: &mut File, destination: &mut File) -> std::io::Result<(u64, String)> {
    let mut digest = Sha256::new();
    let mut length = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = source.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        destination.write_all(&buffer[..read])?;
        digest.update(&buffer[..read]);
        length += read as u64;
    }
    destination.flush()?;
    destination.sync_all()?;
    Ok((length, hex::encode(digest.finalize())))
}

pub(crate) fn validate_recovery_state(
    paths: &AppPaths,
    recovery: &RecoveryState,
) -> Result<BackupMetadata, AgentStoreError> {
    validate_recovery_state_with_barrier(paths, recovery, |_| {})
}

fn validate_recovery_state_with_barrier<F>(
    paths: &AppPaths,
    recovery: &RecoveryState,
    barrier: F,
) -> Result<BackupMetadata, AgentStoreError>
where
    F: FnOnce(&Path),
{
    validate_recovery_state_in_root(&paths.agent_backups, recovery, barrier)
}

fn metadata_paths_are_well_formed(metadata: &BackupMetadata, backup_root: &Path) -> bool {
    let Some(bundle) = metadata.backup_path.parent() else {
        return false;
    };
    metadata.metadata_path.parent() == Some(bundle)
        && bundle.parent() == Some(backup_root)
        && bundle
            .file_name()
            .is_some_and(|name| name.to_string_lossy().contains(&metadata.owner_token))
        && metadata
            .backup_path
            .file_name()
            .and_then(|name| name.to_str())
            == Some("agent-platform.sqlite3")
        && metadata
            .metadata_path
            .file_name()
            .and_then(|name| name.to_str())
            == Some("metadata.json")
}

fn metadata_matches_manifest(
    sidecar: &BackupMetadata,
    expected: &BackupMetadata,
    backup_root: &Path,
) -> bool {
    if expected.owner_token.is_empty()
        || !metadata_paths_are_well_formed(sidecar, backup_root)
        || !metadata_paths_are_well_formed(expected, backup_root)
    {
        return false;
    }
    let mut rebound = sidecar.clone();
    rebound.backup_path = expected.backup_path.clone();
    rebound.metadata_path = expected.metadata_path.clone();
    rebound == *expected
}

fn validate_recovery_state_in_root<F>(
    backup_root: &Path,
    recovery: &RecoveryState,
    barrier: F,
) -> Result<BackupMetadata, AgentStoreError>
where
    F: FnOnce(&Path),
{
    let fail = || AgentStoreError::recovery_required(recovery.source_path.clone(), None);
    let expected = recovery.backup.as_ref().ok_or_else(&fail)?;
    let backup_path = &expected.backup_path;
    let metadata_path = &expected.metadata_path;
    let bundle = backup_path.parent().ok_or_else(&fail)?;
    if !metadata_paths_are_well_formed(expected, backup_root) {
        return Err(fail());
    }
    let trusted_root = open_trusted_directory(backup_root).map_err(|_| fail())?;
    let trusted_bundle = open_trusted_directory(bundle).map_err(|_| fail())?;
    ensure_trusted_directory(&trusted_root).map_err(|_| fail())?;
    ensure_trusted_directory(&trusted_bundle).map_err(|_| fail())?;

    let (mut sidecar_file, sidecar_before) =
        open_recovery_source(metadata_path).map_err(|_| fail())?;
    if sidecar_before.byte_length > 1024 * 1024 {
        return Err(fail());
    }
    let mut sidecar_bytes = Vec::with_capacity(sidecar_before.byte_length as usize);
    sidecar_file
        .read_to_end(&mut sidecar_bytes)
        .map_err(|_| fail())?;
    if file_snapshot(&sidecar_file.metadata().map_err(|_| fail())?) != sidecar_before
        || path_file_snapshot(metadata_path).map_err(|_| fail())? != sidecar_before
    {
        return Err(fail());
    }
    let sidecar: BackupMetadata = serde_json::from_slice(&sidecar_bytes).map_err(|_| fail())?;
    if sidecar.source_path != recovery.source_path
        || !metadata_matches_manifest(&sidecar, expected, backup_root)
    {
        return Err(fail());
    }

    let (mut source, source_before) = open_recovery_source(backup_path).map_err(|_| fail())?;
    let mut verification = VerificationCopy::create(&trusted_root).map_err(|_| fail())?;
    let verification_file = verification.file_mut().map_err(|_| fail())?;
    let (length, sha256) = copy_and_hash(&mut source, verification_file).map_err(|_| fail())?;
    verification.seal_for_sqlite().map_err(|_| fail())?;
    if length != sidecar.byte_length || sha256 != sidecar.sha256 {
        return Err(fail());
    }

    #[cfg(unix)]
    barrier(&verification.path);
    let copy = configured_connection(
        &verification.sqlite_path().map_err(|_| fail())?,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_FULL_MUTEX
            | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|_| fail())?;
    #[cfg(windows)]
    barrier(&verification.path);
    let mut integrity_statement = copy.prepare("PRAGMA integrity_check").map_err(|_| fail())?;
    let integrity = integrity_statement
        .query_map([], |row| row.get::<_, String>(0))
        .and_then(|rows| rows.collect::<Result<Vec<_>, _>>())
        .map_err(|_| fail())?;
    drop(integrity_statement);
    let schema = migrations::user_version(&copy).map_err(|_| fail())?;
    drop(copy);
    if integrity != ["ok"] || schema != sidecar.schema_version {
        return Err(fail());
    }

    let source_after = file_snapshot(&source.metadata().map_err(|_| fail())?);
    let source_path_after = path_file_snapshot(backup_path).map_err(|_| fail())?;
    if source_after != source_before
        || source_path_after != source_before
        || !verification.identity_is_current()
        || ensure_trusted_directory(&trusted_root).is_err()
        || ensure_trusted_directory(&trusted_bundle).is_err()
    {
        return Err(fail());
    }
    verification.cleanup().map_err(|_| fail())?;
    Ok(expected.clone())
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

    use super::{migrations, AgentStore, BackupFailurePoint, FaultingBackupFilesystem};
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
        assert_eq!(
            user_version(&connection),
            migrations::CURRENT_SCHEMA_VERSION
        );
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

        assert_eq!(
            user_version(&reopened.reader().unwrap()),
            migrations::CURRENT_SCHEMA_VERSION
        );
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

        assert_eq!(
            user_version(&connection),
            migrations::CURRENT_SCHEMA_VERSION
        );
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
        assert_eq!(backup.backup_path.parent(), backup.metadata_path.parent());
        let bundle = backup.backup_path.parent().unwrap();
        assert_eq!(bundle.parent(), Some(paths.agent_backups.as_path()));
        assert_eq!(fs::read_dir(bundle).unwrap().count(), 2);
        assert!(fs::read_dir(&paths.agent_backups).unwrap().all(|entry| {
            !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with('.')
        }));
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
    fn backup_publication_failures_leave_no_formally_named_bundle_or_sidecar() {
        for point in [
            BackupFailurePoint::WriteSidecar,
            BackupFailurePoint::SyncFile,
            BackupFailurePoint::SyncBundleDirectory,
            BackupFailurePoint::RenameBundle,
        ] {
            let dir = tempfile::tempdir().unwrap();
            let paths = paths(&dir);
            let fixture = Connection::open(&paths.agent_database).unwrap();
            fixture.execute_batch(V0_FIXTURE).unwrap();
            drop(fixture);
            let source_before = fs::read(&paths.agent_database).unwrap();

            let error = AgentStore::open_with_backup_filesystem(
                &paths,
                &FaultingBackupFilesystem::new(point),
            )
            .unwrap_err();

            assert_eq!(error.code(), "agent_store_recovery_required", "{point:?}");
            assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);
            assert!(error.recovery().unwrap().backup.is_none());
            if paths.agent_backups.exists() {
                assert_eq!(
                    fs::read_dir(&paths.agent_backups).unwrap().count(),
                    0,
                    "{point:?}"
                );
            }
        }
    }

    #[test]
    fn parent_directory_fsync_failure_preserves_published_recovery_evidence() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);

        let error = AgentStore::open_with_backup_filesystem(
            &paths,
            &FaultingBackupFilesystem::new(BackupFailurePoint::SyncBackupDirectory),
        )
        .unwrap_err();

        assert_eq!(error.code(), "agent_store_backup_durability_uncertain");
        let recovery = error.recovery().unwrap();
        let metadata = recovery
            .backup
            .as_ref()
            .expect("published recovery evidence");
        assert!(metadata.backup_path.exists());
        assert!(metadata.metadata_path.exists());
        assert_eq!(
            super::validate_recovery_state(&paths, recovery).unwrap(),
            *metadata
        );
    }

    #[test]
    fn atomic_publication_dependencies_are_exactly_pinned_and_platform_gated() {
        let manifest = include_str!("../../Cargo.toml");
        assert!(manifest.contains("libc = \"=0.2.189\""));
        assert!(manifest.contains("windows-sys = { version = \"=0.61.2\""));

        let source = include_str!("mod.rs");
        for primitive in [
            "libc::renameatx_np",
            "libc::RENAME_EXCL",
            "libc::renameat2",
            "libc::RENAME_NOREPLACE",
            "ReplaceIfExists = false",
            "FILE_FLAG_WRITE_THROUGH",
        ] {
            assert!(source.contains(primitive), "missing {primitive}");
        }
        let production = source.split("#[cfg(test)]\nmod tests").next().unwrap();
        assert!(!production.contains("final_bundle.exists()"));
        let windows_publish = production
            .split("#[cfg(windows)]\nfn atomic_publish_bundle")
            .nth(1)
            .expect("Windows publication implementation");
        assert!(windows_publish.contains("path_identity(to).is_ok_and"));
        assert!(windows_publish.contains("BundlePublication::DurabilityUncertain"));
        assert!(!windows_publish.contains("BundlePublication::Verified"));
        assert!(!windows_publish.contains("source.sync_all()"));
        assert!(!windows_publish.contains("parent.handle.sync_all()"));

        let windows_directory_sync = production
            .split("#[cfg(windows)]\nfn sync_directory")
            .nth(1)
            .expect("Windows directory durability boundary")
            .split("#[cfg(test)]")
            .next()
            .unwrap();
        assert!(!windows_directory_sync.contains("sync_all()"));
    }

    #[cfg(windows)]
    #[test]
    fn windows_real_migration_publishes_a_revalidatable_backup() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);

        let error = AgentStore::open(&paths).unwrap_err();
        assert_eq!(error.code(), "agent_store_backup_durability_uncertain");
        let recovery = error.recovery().unwrap();
        let metadata = recovery
            .backup
            .as_ref()
            .expect("Windows must retain the published backup evidence")
            .clone();

        assert_eq!(
            user_version(&Connection::open(&paths.agent_database).unwrap()),
            0
        );
        assert_eq!(
            super::validate_recovery_state(&paths, &recovery).unwrap(),
            metadata
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_verification_handle_blocks_replacement_while_sqlite_is_open() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let error = AgentStore::open(&paths).unwrap_err();
        assert_eq!(error.code(), "agent_store_backup_durability_uncertain");
        let metadata = error.recovery().unwrap().backup.as_ref().unwrap().clone();
        let recovery = crate::agent_store::model::RecoveryState {
            source_path: paths.agent_database.clone(),
            backup: Some(metadata.clone()),
        };

        let result =
            super::validate_recovery_state_with_barrier(&paths, &recovery, |verification_path| {
                let before = super::path_file_snapshot(verification_path)
                    .unwrap()
                    .identity;
                let replacement = verification_path.with_extension("replacement.sqlite3");
                Connection::open(&replacement).unwrap();

                assert!(fs::rename(&replacement, verification_path).is_err());
                assert_eq!(
                    super::path_file_snapshot(verification_path)
                        .unwrap()
                        .identity,
                    before
                );
                fs::remove_file(replacement).unwrap();
            });

        assert_eq!(result.unwrap(), metadata);
    }

    #[cfg(unix)]
    #[test]
    fn exclusive_publication_never_replaces_a_preoccupied_final_bundle() {
        use std::os::unix::fs::MetadataExt;
        use std::sync::Mutex;

        struct PreoccupiedFinal {
            occupied: Mutex<Option<(PathBuf, u64, u64)>>,
        }

        impl super::BackupFilesystem for PreoccupiedFinal {
            fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()> {
                super::RealBackupFilesystem.write_sidecar(path, bytes)
            }
            fn sync_file(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_file(path)
            }
            fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_bundle_directory(path)
            }
            fn rename_bundle(
                &self,
                parent: &super::TrustedDirectory,
                from: &Path,
                to: &Path,
            ) -> std::io::Result<super::BundlePublication> {
                fs::create_dir(to)?;
                let identity = fs::symlink_metadata(to)?;
                *self.occupied.lock().unwrap() =
                    Some((to.to_path_buf(), identity.dev(), identity.ino()));
                super::RealBackupFilesystem.rename_bundle(parent, from, to)
            }
            fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_backup_directory(path)
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let filesystem = PreoccupiedFinal {
            occupied: Mutex::new(None),
        };

        let error = AgentStore::open_with_backup_filesystem(&paths, &filesystem).unwrap_err();

        assert_eq!(error.code(), "agent_store_recovery_required");
        let (occupied, dev, ino) = filesystem.occupied.into_inner().unwrap().unwrap();
        let identity = fs::symlink_metadata(&occupied).unwrap();
        assert_eq!((identity.dev(), identity.ino()), (dev, ino));
        assert_eq!(fs::read_dir(occupied).unwrap().count(), 0);
    }

    #[cfg(unix)]
    #[test]
    fn temp_and_final_symlink_swaps_are_rejected_without_following_them() {
        use std::os::unix::fs::symlink;
        use std::sync::Mutex;

        #[derive(Clone, Copy)]
        enum SwapPoint {
            Temp,
            Final,
        }

        struct SwappingFilesystem {
            point: SwapPoint,
            published: Mutex<Option<PathBuf>>,
        }

        impl super::BackupFilesystem for SwappingFilesystem {
            fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()> {
                super::RealBackupFilesystem.write_sidecar(path, bytes)
            }
            fn sync_file(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_file(path)
            }
            fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_bundle_directory(path)
            }
            fn rename_bundle(
                &self,
                parent: &super::TrustedDirectory,
                from: &Path,
                to: &Path,
            ) -> std::io::Result<super::BundlePublication> {
                if matches!(self.point, SwapPoint::Temp) {
                    let diverted = from.with_file_name(format!(
                        "{}-owned",
                        from.file_name().unwrap().to_string_lossy()
                    ));
                    fs::rename(from, &diverted)?;
                    symlink(&diverted, from)?;
                }
                let publication = super::RealBackupFilesystem.rename_bundle(parent, from, to)?;
                *self.published.lock().unwrap() = Some(to.to_path_buf());
                Ok(publication)
            }
            fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()> {
                if matches!(self.point, SwapPoint::Final) {
                    let published = self.published.lock().unwrap().clone().unwrap();
                    let diverted = published.with_file_name(format!(
                        "{}-owned",
                        published.file_name().unwrap().to_string_lossy()
                    ));
                    fs::rename(&published, &diverted)?;
                    symlink(&diverted, &published)?;
                }
                super::RealBackupFilesystem.sync_backup_directory(path)
            }
        }

        for point in [SwapPoint::Temp, SwapPoint::Final] {
            let dir = tempfile::tempdir().unwrap();
            let paths = paths(&dir);
            let fixture = Connection::open(&paths.agent_database).unwrap();
            fixture.execute_batch(V0_FIXTURE).unwrap();
            drop(fixture);
            let filesystem = SwappingFilesystem {
                point,
                published: Mutex::new(None),
            };

            let error = AgentStore::open_with_backup_filesystem(&paths, &filesystem).unwrap_err();

            let published = filesystem.published.into_inner().unwrap();
            if let Some(published) = published {
                assert_eq!(error.code(), "agent_store_backup_durability_uncertain");
                assert!(fs::symlink_metadata(&published)
                    .unwrap()
                    .file_type()
                    .is_symlink());
                let diverted = fs::read_link(&published).unwrap();
                assert!(diverted.join("agent-platform.sqlite3").is_file());
                let recovery = error.recovery().unwrap();
                let metadata = recovery
                    .backup
                    .as_ref()
                    .expect("the identity-bound published bundle must be relocated");
                assert_eq!(metadata.backup_path.parent(), Some(diverted.as_path()));
                assert_eq!(
                    super::validate_recovery_state(&paths, recovery).unwrap(),
                    *metadata
                );
            } else {
                assert_eq!(error.code(), "agent_store_recovery_required");
                assert!(error.recovery().unwrap().backup.is_none());
            }
        }
    }

    #[test]
    fn published_bundle_replacement_is_relocated_by_owner_and_filesystem_identity() {
        use std::sync::Mutex;

        struct ReplaceFinal {
            actual: Mutex<Option<PathBuf>>,
            replacement: Mutex<Option<PathBuf>>,
        }

        impl super::BackupFilesystem for ReplaceFinal {
            fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()> {
                super::RealBackupFilesystem.write_sidecar(path, bytes)
            }
            fn sync_file(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_file(path)
            }
            fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_bundle_directory(path)
            }
            fn rename_bundle(
                &self,
                parent: &super::TrustedDirectory,
                from: &Path,
                to: &Path,
            ) -> std::io::Result<super::BundlePublication> {
                super::RealBackupFilesystem.rename_bundle(parent, from, to)?;
                let actual = to.with_file_name(format!(
                    "{}-identity-bound-published",
                    to.file_name().unwrap().to_string_lossy()
                ));
                fs::rename(to, &actual)?;
                fs::create_dir(to)?;
                fs::write(to.join("replacement-marker"), b"do-not-trust")?;
                *self.actual.lock().unwrap() = Some(actual);
                *self.replacement.lock().unwrap() = Some(to.to_path_buf());
                Ok(super::BundlePublication::DurabilityUncertain)
            }
            fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_backup_directory(path)
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let filesystem = ReplaceFinal {
            actual: Mutex::new(None),
            replacement: Mutex::new(None),
        };

        let error = AgentStore::open_with_backup_filesystem(&paths, &filesystem).unwrap_err();

        assert_eq!(error.code(), "agent_store_backup_durability_uncertain");
        let actual = filesystem.actual.into_inner().unwrap().unwrap();
        let replacement = filesystem.replacement.into_inner().unwrap().unwrap();
        assert_eq!(
            fs::read(replacement.join("replacement-marker")).unwrap(),
            b"do-not-trust"
        );
        let recovery = error.recovery().unwrap();
        let metadata = recovery
            .backup
            .as_ref()
            .expect("relocated published evidence");
        assert_eq!(metadata.backup_path.parent(), Some(actual.as_path()));
        assert_eq!(
            super::validate_recovery_state(&paths, recovery).unwrap(),
            *metadata
        );
    }

    #[test]
    fn deleted_published_bundle_returns_distinct_evidence_lost_without_stale_metadata() {
        struct DeletePublished;

        impl super::BackupFilesystem for DeletePublished {
            fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()> {
                super::RealBackupFilesystem.write_sidecar(path, bytes)
            }
            fn sync_file(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_file(path)
            }
            fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_bundle_directory(path)
            }
            fn rename_bundle(
                &self,
                parent: &super::TrustedDirectory,
                from: &Path,
                to: &Path,
            ) -> std::io::Result<super::BundlePublication> {
                super::RealBackupFilesystem.rename_bundle(parent, from, to)?;
                fs::remove_dir_all(to)?;
                Ok(super::BundlePublication::DurabilityUncertain)
            }
            fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_backup_directory(path)
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);

        let error = AgentStore::open_with_backup_filesystem(&paths, &DeletePublished).unwrap_err();

        assert_eq!(error.code(), "agent_store_backup_evidence_lost");
        assert!(error.recovery().unwrap().backup.is_none());
    }

    #[cfg(unix)]
    #[test]
    fn cleanup_does_not_remove_a_replacement_at_the_owned_temp_name() {
        use std::sync::Mutex;

        struct CleanupSwap {
            replacement: Mutex<Option<PathBuf>>,
        }

        impl super::BackupFilesystem for CleanupSwap {
            fn write_sidecar(&self, path: &Path, _bytes: &[u8]) -> std::io::Result<()> {
                let temporary = path.parent().unwrap();
                let diverted = temporary.with_extension("owned");
                fs::rename(temporary, &diverted)?;
                fs::create_dir(temporary)?;
                fs::write(temporary.join("do-not-delete"), b"replacement")?;
                *self.replacement.lock().unwrap() = Some(temporary.to_path_buf());
                Err(std::io::Error::other("injected sidecar failure after swap"))
            }
            fn sync_file(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_file(path)
            }
            fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_bundle_directory(path)
            }
            fn rename_bundle(
                &self,
                parent: &super::TrustedDirectory,
                from: &Path,
                to: &Path,
            ) -> std::io::Result<super::BundlePublication> {
                super::RealBackupFilesystem.rename_bundle(parent, from, to)
            }
            fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_backup_directory(path)
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let filesystem = CleanupSwap {
            replacement: Mutex::new(None),
        };

        let error = AgentStore::open_with_backup_filesystem(&paths, &filesystem).unwrap_err();

        assert_eq!(error.code(), "agent_store_recovery_required");
        let replacement = filesystem.replacement.into_inner().unwrap().unwrap();
        assert_eq!(
            fs::read(replacement.join("do-not-delete")).unwrap(),
            b"replacement"
        );
    }

    #[cfg(unix)]
    #[test]
    fn parent_fsync_and_cleanup_double_failure_returns_durable_blocking_evidence() {
        use std::os::unix::fs::PermissionsExt;
        use std::sync::Mutex;

        struct DurabilityFailure {
            published: Mutex<Option<PathBuf>>,
        }

        impl super::BackupFilesystem for DurabilityFailure {
            fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()> {
                super::RealBackupFilesystem.write_sidecar(path, bytes)
            }
            fn sync_file(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_file(path)
            }
            fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_bundle_directory(path)
            }
            fn rename_bundle(
                &self,
                parent: &super::TrustedDirectory,
                from: &Path,
                to: &Path,
            ) -> std::io::Result<super::BundlePublication> {
                let publication = super::RealBackupFilesystem.rename_bundle(parent, from, to)?;
                *self.published.lock().unwrap() = Some(to.to_path_buf());
                Ok(publication)
            }
            fn sync_backup_directory(&self, _path: &Path) -> std::io::Result<()> {
                let published = self.published.lock().unwrap().clone().unwrap();
                fs::set_permissions(&published, fs::Permissions::from_mode(0o500))?;
                Err(std::io::Error::other("injected parent fsync failure"))
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let filesystem = DurabilityFailure {
            published: Mutex::new(None),
        };

        let error = AgentStore::open_with_backup_filesystem(&paths, &filesystem).unwrap_err();
        let published = filesystem.published.into_inner().unwrap().unwrap();
        assert!(
            fs::remove_dir_all(&published).is_err(),
            "the injected permissions must make cleanup fail"
        );
        fs::set_permissions(&published, fs::Permissions::from_mode(0o700)).unwrap();

        assert_eq!(error.code(), "agent_store_backup_durability_uncertain");
        let recovery = error.recovery().unwrap();
        let metadata = recovery
            .backup
            .as_ref()
            .expect("published recovery evidence");
        assert!(metadata.metadata_path.exists());
        assert_eq!(metadata.backup_path.parent(), Some(published.as_path()));
        assert_eq!(
            super::validate_recovery_state(&paths, recovery).unwrap(),
            *metadata
        );
    }

    #[test]
    fn large_backup_is_hashed_streamingly_and_revalidates_after_atomic_publication() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        fixture
            .execute("CREATE TABLE large_fixture (payload BLOB NOT NULL)", [])
            .unwrap();
        fixture
            .execute("INSERT INTO large_fixture VALUES (zeroblob(16777216))", [])
            .unwrap();
        drop(fixture);

        let store = AgentStore::open(&paths).unwrap();
        let metadata = store.migration_backup().unwrap();
        assert!(metadata.byte_length > 16 * 1024 * 1024);
        let recovery = crate::agent_store::model::RecoveryState {
            source_path: paths.agent_database.clone(),
            backup: Some(metadata.clone()),
        };
        assert_eq!(
            super::validate_recovery_state(&paths, &recovery).unwrap(),
            *metadata
        );
    }

    #[test]
    fn recovery_revalidation_rejects_sidecar_drift_and_paths_outside_the_backup_root() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let store = AgentStore::open(&paths).unwrap();
        let metadata = store.migration_backup().unwrap().clone();
        let recovery = crate::agent_store::model::RecoveryState {
            source_path: paths.agent_database.clone(),
            backup: Some(metadata.clone()),
        };

        let mut drifted = metadata.clone();
        drifted.byte_length += 1;
        fs::write(
            &metadata.metadata_path,
            serde_json::to_vec_pretty(&drifted).unwrap(),
        )
        .unwrap();
        assert!(super::validate_recovery_state(&paths, &recovery).is_err());

        fs::write(
            &metadata.metadata_path,
            serde_json::to_vec_pretty(&metadata).unwrap(),
        )
        .unwrap();
        let outside_bundle = dir.path().join("outside-bundle");
        fs::create_dir(&outside_bundle).unwrap();
        let outside_backup = outside_bundle.join("agent-platform.sqlite3");
        let outside_sidecar = outside_bundle.join("metadata.json");
        fs::copy(&metadata.backup_path, &outside_backup).unwrap();
        let mut outside_metadata = metadata;
        outside_metadata.backup_path = outside_backup;
        outside_metadata.metadata_path = outside_sidecar.clone();
        fs::write(
            &outside_sidecar,
            serde_json::to_vec_pretty(&outside_metadata).unwrap(),
        )
        .unwrap();
        let outside_recovery = crate::agent_store::model::RecoveryState {
            source_path: paths.agent_database.clone(),
            backup: Some(outside_metadata),
        };
        assert!(super::validate_recovery_state(&paths, &outside_recovery).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn recovery_validation_fails_closed_when_source_path_is_replaced_at_the_copy_barrier() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let store = AgentStore::open(&paths).unwrap();
        let metadata = store.migration_backup().unwrap().clone();
        let recovery = crate::agent_store::model::RecoveryState {
            source_path: paths.agent_database.clone(),
            backup: Some(metadata.clone()),
        };
        let replacement = metadata.backup_path.with_extension("replacement.sqlite3");
        fs::copy(&metadata.backup_path, &replacement).unwrap();
        let replacement_connection = Connection::open(&replacement).unwrap();
        replacement_connection
            .execute_batch(
                "CREATE TABLE replacement_marker (value TEXT NOT NULL);\n\
                 INSERT INTO replacement_marker VALUES ('replacement-b');",
            )
            .unwrap();
        drop(replacement_connection);
        let displaced = metadata.backup_path.with_extension("source-a.sqlite3");

        let result = super::validate_recovery_state_with_barrier(&paths, &recovery, |_| {
            fs::rename(&metadata.backup_path, &displaced).unwrap();
            fs::rename(&replacement, &metadata.backup_path).unwrap();
        });

        assert!(result.is_err());
        assert!(displaced.exists());
        let replacement_connection = Connection::open(&metadata.backup_path).unwrap();
        assert_eq!(
            replacement_connection
                .query_row("SELECT value FROM replacement_marker", [], |row| {
                    row.get::<_, String>(0)
                })
                .unwrap(),
            "replacement-b"
        );
    }

    #[cfg(unix)]
    #[test]
    fn sqlite_validation_uses_the_unlinked_verification_inode_that_was_hashed() {
        use std::sync::Mutex;

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        let store = AgentStore::open(&paths).unwrap();
        let metadata = store.migration_backup().unwrap().clone();
        let recovery = crate::agent_store::model::RecoveryState {
            source_path: paths.agent_database.clone(),
            backup: Some(metadata.clone()),
        };
        let recreated = Mutex::new(None);

        let result =
            super::validate_recovery_state_with_barrier(&paths, &recovery, |verification_path| {
                assert!(
                    !verification_path.exists(),
                    "the private verification copy must already be unlinked"
                );
                fs::write(verification_path, b"attacker replacement, not sqlite").unwrap();
                *recreated.lock().unwrap() = Some(verification_path.to_path_buf());
            });

        assert_eq!(result.unwrap(), metadata);
        let recreated = recreated.into_inner().unwrap().unwrap();
        assert_eq!(
            fs::read(&recreated).unwrap(),
            b"attacker replacement, not sqlite"
        );
        fs::remove_file(recreated).unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_migrates_a_v0_database_after_unlinked_verification() {
        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);

        let store = AgentStore::open(&paths).unwrap();

        assert!(store.migration_backup().is_some());
        assert_eq!(
            user_version(&store.reader().unwrap()),
            migrations::CURRENT_SCHEMA_VERSION
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_sqlite_opens_an_unlinked_verification_fd_with_read_only_uri_mode() {
        use std::fs::File;
        use std::os::fd::AsRawFd;

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        drop(fixture);
        fs::create_dir_all(&paths.agent_backups).unwrap();
        let trusted_root = super::open_trusted_directory(&paths.agent_backups).unwrap();
        let mut verification = super::VerificationCopy::create(&trusted_root).unwrap();
        let mut source = File::open(&paths.agent_database).unwrap();
        super::copy_and_hash(&mut source, verification.file_mut().unwrap()).unwrap();
        verification.seal_for_sqlite().unwrap();
        let file = verification.file.as_ref().unwrap();
        let uri = PathBuf::from(format!(
            "file:/dev/fd/{}?mode=ro&immutable=1",
            file.as_raw_fd()
        ));

        let copy = super::configured_connection(
            &uri,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
                | rusqlite::OpenFlags::SQLITE_OPEN_FULL_MUTEX
                | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .unwrap();

        assert_eq!(user_version(&copy), 0);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_sqlite_opens_an_unlinked_backup_api_verification_fd() {
        use std::fs::File;

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let source = Connection::open(&paths.agent_database).unwrap();
        source.execute_batch(V0_FIXTURE).unwrap();
        fs::create_dir_all(&paths.agent_backups).unwrap();
        let backup = paths.agent_backups.join("backup-api.sqlite3");
        source.backup(rusqlite::MAIN_DB, &backup, None).unwrap();

        let trusted_root = super::open_trusted_directory(&paths.agent_backups).unwrap();
        let mut verification = super::VerificationCopy::create(&trusted_root).unwrap();
        let mut backup_file = File::open(&backup).unwrap();
        let (_length, _sha256) =
            super::copy_and_hash(&mut backup_file, verification.file_mut().unwrap()).unwrap();
        verification.seal_for_sqlite().unwrap();
        let copy = super::configured_connection(
            &verification.sqlite_path().unwrap(),
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
                | rusqlite::OpenFlags::SQLITE_OPEN_FULL_MUTEX
                | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .unwrap();

        assert_eq!(user_version(&copy), 0);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_sqlite_opens_an_unlinked_wal_backup_api_verification_fd() {
        use std::fs::File;

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let writer = Connection::open(&paths.agent_database).unwrap();
        writer
            .query_row("PRAGMA journal_mode = WAL", [], |row| {
                row.get::<_, String>(0)
            })
            .unwrap();
        writer.pragma_update(None, "wal_autocheckpoint", 0).unwrap();
        writer.execute_batch(V0_FIXTURE).unwrap();
        let reader = Connection::open(&paths.agent_database).unwrap();
        reader.execute_batch("BEGIN").unwrap();
        drop(writer);
        fs::create_dir_all(&paths.agent_backups).unwrap();
        let backup = paths.agent_backups.join("backup-api.sqlite3");
        reader.backup(rusqlite::MAIN_DB, &backup, None).unwrap();

        let trusted_root = super::open_trusted_directory(&paths.agent_backups).unwrap();
        let mut verification = super::VerificationCopy::create(&trusted_root).unwrap();
        let mut backup_file = File::open(&backup).unwrap();
        let (_length, _sha256) =
            super::copy_and_hash(&mut backup_file, verification.file_mut().unwrap()).unwrap();
        verification.seal_for_sqlite().unwrap();
        let copy = super::configured_connection(
            &verification.sqlite_path().unwrap(),
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
                | rusqlite::OpenFlags::SQLITE_OPEN_FULL_MUTEX
                | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .unwrap();

        assert_eq!(user_version(&copy), 0);
    }

    #[cfg(unix)]
    #[test]
    fn recovery_source_open_rejects_a_symlink_without_following_it() {
        use std::os::unix::fs::symlink;

        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("source.sqlite3");
        let alias = dir.path().join("alias.sqlite3");
        fs::write(&source, b"source").unwrap();
        symlink(&source, &alias).unwrap();

        assert!(super::open_recovery_source(&alias).is_err());
        assert_eq!(fs::read(&source).unwrap(), b"source");
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
        assert_eq!(
            user_version(&reopened.reader().unwrap()),
            migrations::CURRENT_SCHEMA_VERSION
        );
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
        assert_eq!(
            user_version(&migrated.reader().unwrap()),
            migrations::CURRENT_SCHEMA_VERSION
        );
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
        connection
            .execute_batch(&format!(
                "{V0_FIXTURE}\nCREATE TABLE crash_fixture (id INTEGER PRIMARY KEY, payload BLOB NOT NULL);\nINSERT INTO crash_fixture VALUES (1, zeroblob(8388608));"
            ))
            .unwrap();
        drop(connection);
        let journal = sqlite_sidecar(&paths.agent_database, "-journal");
        let child = std::process::Command::new(std::env::current_exe().unwrap())
            .arg("--exact")
            .arg("agent_store::tests::hot_journal_crash_helper")
            .arg("--nocapture")
            .env("DSH_AGENT_STORE_HOT_JOURNAL_DB", &paths.agent_database)
            .status()
            .unwrap();
        assert!(!child.success(), "crash helper unexpectedly exited cleanly");
        assert!(fs::metadata(&journal).unwrap().len() > 512);
        let source_before = fs::read(&paths.agent_database).unwrap();
        let journal_before = fs::read(&journal).unwrap();

        let error = AgentStore::open(&paths).unwrap_err();

        assert_eq!(error.code(), "agent_store_recovery_required");
        assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);
        assert_eq!(fs::read(&journal).unwrap(), journal_before);
        assert!(!paths.agent_backups.exists());
    }

    #[test]
    fn hot_journal_crash_helper() {
        let Ok(database_path) = std::env::var("DSH_AGENT_STORE_HOT_JOURNAL_DB") else {
            return;
        };
        let connection = Connection::open(database_path).unwrap();
        connection
            .pragma_update(None, "journal_mode", "delete")
            .unwrap();
        connection.pragma_update(None, "cache_size", 8).unwrap();
        connection.execute_batch("BEGIN IMMEDIATE").unwrap();
        connection
            .execute(
                "UPDATE crash_fixture SET payload = randomblob(8388608) WHERE id = 1",
                [],
            )
            .unwrap();
        std::process::abort();
    }

    #[test]
    fn production_migration_barrier_blocks_a_writer_after_bundle_publish_before_schema_change() {
        use std::sync::{mpsc, Arc};

        struct BarrierFilesystem {
            published: mpsc::Sender<()>,
            resume: std::sync::Mutex<mpsc::Receiver<()>>,
        }

        impl super::BackupFilesystem for BarrierFilesystem {
            fn write_sidecar(&self, path: &Path, bytes: &[u8]) -> std::io::Result<()> {
                super::RealBackupFilesystem.write_sidecar(path, bytes)
            }
            fn sync_file(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_file(path)
            }
            fn sync_bundle_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_bundle_directory(path)
            }
            fn rename_bundle(
                &self,
                parent: &super::TrustedDirectory,
                from: &Path,
                to: &Path,
            ) -> std::io::Result<super::BundlePublication> {
                let publication = super::RealBackupFilesystem.rename_bundle(parent, from, to)?;
                self.published.send(()).unwrap();
                self.resume.lock().unwrap().recv().unwrap();
                Ok(publication)
            }
            fn sync_backup_directory(&self, path: &Path) -> std::io::Result<()> {
                super::RealBackupFilesystem.sync_backup_directory(path)
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let paths = paths(&dir);
        let fixture = Connection::open(&paths.agent_database).unwrap();
        fixture.execute_batch(V0_FIXTURE).unwrap();
        fixture
            .execute(
                "INSERT INTO legacy_settings VALUES ('committed-before', 'must-be-backed-up')",
                [],
            )
            .unwrap();
        drop(fixture);
        let (published_tx, published_rx) = mpsc::channel();
        let (resume_tx, resume_rx) = mpsc::channel();
        let filesystem = Arc::new(BarrierFilesystem {
            published: published_tx,
            resume: std::sync::Mutex::new(resume_rx),
        });
        let migration_paths = paths.clone();
        let migration_filesystem = Arc::clone(&filesystem);
        let migration = thread::spawn(move || {
            AgentStore::open_with_backup_filesystem(&migration_paths, migration_filesystem.as_ref())
        });
        published_rx.recv().unwrap();

        let racing_writer = Connection::open(&paths.agent_database).unwrap();
        racing_writer
            .busy_timeout(std::time::Duration::from_millis(150))
            .unwrap();
        let error = racing_writer
            .execute(
                "INSERT INTO legacy_settings VALUES ('racing-writer', 'must-not-slip-in')",
                [],
            )
            .unwrap_err();
        assert!(super::is_lock_error(&error));
        resume_tx.send(()).unwrap();

        let store = migration.join().unwrap().unwrap();
        let backup = Connection::open(&store.migration_backup().unwrap().backup_path).unwrap();
        assert_eq!(
            backup
                .query_row(
                    "SELECT setting_value FROM legacy_settings WHERE setting_key = 'committed-before'",
                    [],
                    |row| row.get::<_, String>(0),
                )
                .unwrap(),
            "must-be-backed-up"
        );
        assert_eq!(
            backup
                .query_row(
                    "SELECT COUNT(*) FROM legacy_settings WHERE setting_key = 'racing-writer'",
                    [],
                    |row| row.get::<_, i64>(0),
                )
                .unwrap(),
            0
        );
        assert_eq!(
            user_version(&store.reader().unwrap()),
            migrations::CURRENT_SCHEMA_VERSION
        );
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
        assert_eq!(user_version(&migrated), migrations::CURRENT_SCHEMA_VERSION);
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
                    assert_eq!(
                        user_version(&connection),
                        migrations::CURRENT_SCHEMA_VERSION
                    );
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
    fn sqlite_immediate_writer_reservation_keeps_backup_and_schema_change_in_one_writer_epoch() {
        use rusqlite::TransactionBehavior;
        use std::{sync::mpsc, time::Duration};

        for journal_mode in ["delete", "wal"] {
            let dir = tempfile::tempdir().unwrap();
            let paths = paths(&dir);
            let seed = Connection::open(&paths.agent_database).unwrap();
            seed.pragma_update(None, "journal_mode", journal_mode)
                .unwrap();
            seed.execute_batch(V0_FIXTURE).unwrap();
            drop(seed);
            let backup_path = dir.path().join("snapshot.sqlite3");
            let mut migration = Connection::open(&paths.agent_database).unwrap();
            let transaction = migration
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .unwrap();
            let snapshot = Connection::open_with_flags(
                &paths.agent_database,
                rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
            )
            .unwrap();
            snapshot
                .backup(rusqlite::MAIN_DB, &backup_path, None)
                .unwrap();
            drop(snapshot);

            let (attempted_tx, attempted_rx) = mpsc::channel();
            let (finished_tx, finished_rx) = mpsc::channel();
            let database_path = paths.agent_database.clone();
            let writer = thread::spawn(move || {
                let external = Connection::open(database_path).unwrap();
                external.busy_timeout(Duration::from_millis(150)).unwrap();
                attempted_tx.send(()).unwrap();
                let result = external.execute(
                    "INSERT INTO legacy_settings (setting_key, setting_value) VALUES ('racing', 'writer')",
                    [],
                );
                finished_tx.send(result).unwrap();
            });
            attempted_rx.recv().unwrap();
            let result = finished_rx.recv_timeout(Duration::from_secs(1)).unwrap();
            assert!(super::is_lock_error(&result.unwrap_err()), "{journal_mode}");

            transaction
                .execute_batch("CREATE TABLE migration_marker (value TEXT NOT NULL);")
                .unwrap();
            transaction.commit().unwrap();
            writer.join().unwrap();

            let backup = Connection::open(backup_path).unwrap();
            assert_eq!(user_version(&backup), 0);
            assert_eq!(
                backup
                    .query_row("SELECT COUNT(*) FROM legacy_settings", [], |row| row
                        .get::<_, i64>(0))
                    .unwrap(),
                1
            );
            let migrated = Connection::open(&paths.agent_database).unwrap();
            assert_eq!(
                migrated
                    .query_row("SELECT COUNT(*) FROM legacy_settings", [], |row| row
                        .get::<_, i64>(0))
                    .unwrap(),
                1
            );
            assert!(table_names(&migrated).contains(&"migration_marker".to_string()));
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

        assert!(!super::contains_secret_bearing_term(&schema));
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
                    !super::contains_secret_bearing_term(&column),
                    "secret-bearing column {table}.{column}"
                );
            }
        }
        let seeded_values = all_text_values(&connection);
        assert!(seeded_values.iter().any(|value| value == "preserve-me"));
        for value in seeded_values {
            let value = value.to_ascii_lowercase();
            assert!(!value.contains("sk-unit-test-secret"));
            assert!(!super::contains_secret_bearing_term(&value));
        }
    }

    #[test]
    fn secret_detector_matches_compounds_only_at_identifier_boundaries() {
        for positive in [
            "private-key",
            "private_key",
            "privateKey",
            "privatekey",
            "clientSecret",
            "refresh-token",
            "session_token",
            "accessToken",
            "apiKey",
            "authorization",
            "bearer",
            "password",
        ] {
            assert!(
                super::contains_secret_bearing_term(positive),
                "expected secret-bearing identifier: {positive}"
            );
        }
        for negative in [
            "tokenizer",
            "secretary",
            "tokenized",
            "keynote",
            "monkey",
            "publicKey",
        ] {
            assert!(
                !super::contains_secret_bearing_term(negative),
                "unexpected secret-bearing identifier: {negative}"
            );
        }
    }
}
