use std::{
    fs,
    path::{Path, PathBuf},
};

use chrono::Utc;
use uuid::Uuid;

use super::model::{MigrationCandidate, MigrationManifest, MigrationPlan, MigrationReceipt};
use crate::{
    runtime::model::{RuntimeFailure, RuntimeFailureCode},
    storage::atomic_json::write_atomic,
};

pub struct MigrationService {
    target: PathBuf,
    backups: PathBuf,
}

impl MigrationService {
    pub fn new(target: PathBuf, backups: PathBuf) -> Self {
        Self { target, backups }
    }

    pub fn discover(&self, roots: &[PathBuf]) -> Result<Vec<MigrationCandidate>, RuntimeFailure> {
        let mut candidates = Vec::new();
        for source in roots {
            if !is_populated(source)? {
                continue;
            }
            candidates.push(MigrationCandidate {
                source: source.clone(),
                target: self.target.clone(),
                bytes: tree_size(source)?,
                profiles: child_count(&source.join("profiles"))?,
                workspaces: child_count(&source.join("workspaces"))?,
            });
        }
        Ok(candidates)
    }

    pub fn plan(&self, source: &Path) -> Result<MigrationPlan, RuntimeFailure> {
        if fs::symlink_metadata(source)
            .map_err(RuntimeFailure::internal)?
            .file_type()
            .is_symlink()
        {
            return Err(conflict("迁移源不能是符号链接"));
        }
        if !source.is_dir() || !is_populated(source)? {
            return Err(conflict("旧数据目录不存在或为空"));
        }
        if is_populated(&self.target)? {
            return Err(conflict("新旧数据目录都有内容，拒绝自动合并"));
        }

        let source = source.canonicalize().map_err(RuntimeFailure::internal)?;
        let target_parent = self
            .target
            .parent()
            .ok_or_else(|| RuntimeFailure::internal("迁移目标没有父目录"))?;
        fs::create_dir_all(target_parent).map_err(RuntimeFailure::internal)?;
        let target_parent = target_parent
            .canonicalize()
            .map_err(RuntimeFailure::internal)?;
        let target_name = self
            .target
            .file_name()
            .ok_or_else(|| RuntimeFailure::internal("迁移目标没有目录名"))?;
        let target = target_parent.join(target_name);
        if target.starts_with(&source) || source.starts_with(&target) {
            return Err(conflict("迁移源和目标不能互相包含"));
        }

        let id = Uuid::new_v4();
        let name = target_name.to_string_lossy();
        let bytes = tree_size(&source)?;
        Ok(MigrationPlan {
            id,
            source,
            target: target.clone(),
            staging: target_parent.join(format!("{name}.staging-{id}")),
            backup: self.backups.join(format!(
                "legacy-{}-{id}",
                Utc::now().format("%Y%m%dT%H%M%SZ")
            )),
            required_bytes: bytes.saturating_mul(2).saturating_add(16 * 1024 * 1024),
        })
    }

    pub fn execute(&self, plan: &MigrationPlan) -> Result<MigrationReceipt, RuntimeFailure> {
        if !same_target(&plan.target, &self.target)? {
            return Err(conflict("迁移计划目标与服务目标不一致"));
        }
        if plan.staging.exists() || plan.backup.exists() {
            return Err(conflict("迁移暂存或备份目录已存在"));
        }
        let available = available_space(
            plan.target
                .parent()
                .ok_or_else(|| RuntimeFailure::internal("迁移目标没有父目录"))?,
        )?;
        if available < plan.required_bytes {
            return Err(RuntimeFailure::internal(format!(
                "迁移空间不足：需要 {} 字节，可用 {} 字节",
                plan.required_bytes, available
            )));
        }

        let target_was_empty = plan.target.is_dir() && !is_populated(&plan.target)?;
        match self.execute_inner(plan, target_was_empty) {
            Ok(receipt) => Ok(receipt),
            Err(error) => {
                if plan.staging.exists() {
                    let failed = failed_staging_path(plan);
                    let _ = fs::rename(&plan.staging, failed);
                }
                if target_was_empty && !plan.target.exists() {
                    let _ = fs::create_dir_all(&plan.target);
                }
                Err(error)
            }
        }
    }

    fn execute_inner(
        &self,
        plan: &MigrationPlan,
        target_was_empty: bool,
    ) -> Result<MigrationReceipt, RuntimeFailure> {
        copy_tree(&plan.source, &plan.staging)?;
        validate_json_tree(&plan.staging)?;
        let copied_bytes = tree_size(&plan.staging)?;
        let staging_manifest = plan.staging.join("migration-manifest.json");
        write_atomic(
            &staging_manifest,
            &MigrationManifest {
                id: plan.id,
                source: &plan.source,
                target: &plan.target,
                copied_bytes,
                completed_at: Utc::now(),
            },
        )?;

        fs::create_dir_all(&self.backups).map_err(RuntimeFailure::internal)?;
        copy_tree(&plan.source, &plan.backup)?;
        if target_was_empty {
            fs::remove_dir(&plan.target).map_err(RuntimeFailure::internal)?;
        }
        fs::rename(&plan.staging, &plan.target).map_err(RuntimeFailure::internal)?;

        Ok(MigrationReceipt {
            backup_path: plan.backup.clone(),
            staging_path: plan.staging.clone(),
            manifest_path: plan.target.join("migration-manifest.json"),
        })
    }
}

fn copy_tree(source: &Path, destination: &Path) -> Result<(), RuntimeFailure> {
    let metadata = fs::symlink_metadata(source).map_err(RuntimeFailure::internal)?;
    if metadata.file_type().is_symlink() {
        return Err(conflict(format!(
            "迁移目录包含符号链接：{}",
            source.display()
        )));
    }
    if metadata.is_file() {
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?;
        }
        fs::copy(source, destination).map_err(RuntimeFailure::internal)?;
        return Ok(());
    }
    fs::create_dir_all(destination).map_err(RuntimeFailure::internal)?;
    for entry in fs::read_dir(source).map_err(RuntimeFailure::internal)? {
        let entry = entry.map_err(RuntimeFailure::internal)?;
        copy_tree(&entry.path(), &destination.join(entry.file_name()))?;
    }
    Ok(())
}

fn validate_json_tree(root: &Path) -> Result<(), RuntimeFailure> {
    for entry in fs::read_dir(root).map_err(RuntimeFailure::internal)? {
        let entry = entry.map_err(RuntimeFailure::internal)?;
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path).map_err(RuntimeFailure::internal)?;
        if metadata.file_type().is_symlink() {
            return Err(conflict(format!(
                "暂存目录包含符号链接：{}",
                path.display()
            )));
        }
        if metadata.is_dir() {
            validate_json_tree(&path)?;
        } else if path.extension().and_then(|value| value.to_str()) == Some("json") {
            let bytes = fs::read(&path).map_err(RuntimeFailure::internal)?;
            serde_json::from_slice::<serde_json::Value>(&bytes).map_err(|cause| {
                RuntimeFailure::internal(format!("校验 {} 失败: {cause}", path.display()))
            })?;
        }
    }
    Ok(())
}

fn tree_size(root: &Path) -> Result<u64, RuntimeFailure> {
    let metadata = fs::symlink_metadata(root).map_err(RuntimeFailure::internal)?;
    if metadata.file_type().is_symlink() {
        return Err(conflict(format!("目录包含符号链接：{}", root.display())));
    }
    if metadata.is_file() {
        return Ok(metadata.len());
    }
    let mut bytes = 0_u64;
    for entry in fs::read_dir(root).map_err(RuntimeFailure::internal)? {
        bytes = bytes.saturating_add(tree_size(&entry.map_err(RuntimeFailure::internal)?.path())?);
    }
    Ok(bytes)
}

fn child_count(path: &Path) -> Result<usize, RuntimeFailure> {
    match fs::read_dir(path) {
        Ok(entries) => entries
            .map(|entry| entry.map(|_| ()).map_err(RuntimeFailure::internal))
            .collect::<Result<Vec<_>, _>>()
            .map(|entries| entries.len()),
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => Ok(0),
        Err(cause) => Err(RuntimeFailure::internal(cause)),
    }
}

fn is_populated(path: &Path) -> Result<bool, RuntimeFailure> {
    match fs::read_dir(path) {
        Ok(mut entries) => entries
            .next()
            .transpose()
            .map(|entry| entry.is_some())
            .map_err(RuntimeFailure::internal),
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(cause) => Err(RuntimeFailure::internal(cause)),
    }
}

fn failed_staging_path(plan: &MigrationPlan) -> PathBuf {
    let name = plan
        .target
        .file_name()
        .map(|name| name.to_string_lossy())
        .unwrap_or_default();
    plan.target
        .with_file_name(format!("{name}.failed-{}", plan.id))
}

fn same_target(left: &Path, right: &Path) -> Result<bool, RuntimeFailure> {
    let left_parent = left
        .parent()
        .ok_or_else(|| RuntimeFailure::internal("迁移目标没有父目录"))?
        .canonicalize()
        .map_err(RuntimeFailure::internal)?;
    let right_parent = right
        .parent()
        .ok_or_else(|| RuntimeFailure::internal("迁移目标没有父目录"))?
        .canonicalize()
        .map_err(RuntimeFailure::internal)?;
    Ok(left_parent == right_parent && left.file_name() == right.file_name())
}

fn conflict(message: impl Into<String>) -> RuntimeFailure {
    let mut failure = RuntimeFailure::new(RuntimeFailureCode::MigrationConflict, message);
    failure.recoverable = false;
    failure
}

#[cfg(target_os = "windows")]
fn available_space(path: &Path) -> Result<u64, RuntimeFailure> {
    use std::os::windows::ffi::OsStrExt;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetDiskFreeSpaceExW(
            directory_name: *const u16,
            free_bytes_available: *mut u64,
            total_bytes: *mut u64,
            total_free_bytes: *mut u64,
        ) -> i32;
    }

    let mut wide: Vec<u16> = path.as_os_str().encode_wide().collect();
    wide.push(0);
    let mut available = 0_u64;
    let result = unsafe {
        GetDiskFreeSpaceExW(
            wide.as_ptr(),
            &mut available,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    if result == 0 {
        return Err(RuntimeFailure::internal(std::io::Error::last_os_error()));
    }
    Ok(available)
}

#[cfg(target_os = "macos")]
fn available_space(path: &Path) -> Result<u64, RuntimeFailure> {
    use std::{ffi::CString, os::unix::ffi::OsStrExt};

    let path = CString::new(path.as_os_str().as_bytes()).map_err(RuntimeFailure::internal)?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    let result = unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) };
    if result != 0 {
        return Err(RuntimeFailure::internal(std::io::Error::last_os_error()));
    }
    let stats = unsafe { stats.assume_init() };
    // Darwin 的 statvfs 字段是 u32，先升宽再相乘，保持返回 u64。
    Ok(u64::from(stats.f_bavail).saturating_mul(u64::from(stats.f_frsize)))
}

#[cfg(test)]
mod tests {
    use super::MigrationService;
    use crate::runtime::model::RuntimeFailureCode;

    #[test]
    fn migrates_unicode_data_and_keeps_the_source_backup() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("旧 数据");
        let target = dir.path().join("ai.deepseek.harness.desktop");
        std::fs::create_dir_all(source.join("profiles")).unwrap();
        std::fs::write(source.join("profiles/index.json"), b"{}").unwrap();
        let service = MigrationService::new(target.clone(), dir.path().join("backups"));
        let plan = service.plan(&source).unwrap();
        let receipt = service.execute(&plan).unwrap();
        assert!(target.join("profiles/index.json").is_file());
        assert!(receipt.backup_path.exists());
        assert!(!receipt.staging_path.exists());
    }

    #[test]
    fn refuses_to_merge_two_populated_roots() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("legacy");
        let target = dir.path().join("ai.deepseek.harness.desktop");
        std::fs::create_dir_all(&source).unwrap();
        std::fs::create_dir_all(&target).unwrap();
        std::fs::write(source.join("source.json"), b"{}").unwrap();
        std::fs::write(target.join("target.json"), b"{}").unwrap();
        let service = MigrationService::new(target, dir.path().join("backups"));
        let error = service.plan(&source).unwrap_err();
        assert_eq!(error.code, RuntimeFailureCode::MigrationConflict);
    }

    #[test]
    fn invalid_json_quarantines_staging_and_preserves_source() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("legacy");
        let target = dir.path().join("ai.deepseek.harness.desktop");
        std::fs::create_dir_all(&source).unwrap();
        std::fs::write(source.join("broken.json"), b"{").unwrap();
        let service = MigrationService::new(target.clone(), dir.path().join("backups"));
        let plan = service.plan(&source).unwrap();
        assert!(service.execute(&plan).is_err());
        assert!(source.join("broken.json").exists());
        assert!(super::failed_staging_path(&plan).exists());
        assert!(!target.exists());
    }
}
