use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

use uuid::Uuid;

use crate::runtime::model::RuntimeFailure;

pub const APP_IDENTIFIER: &str = "ai.deepseek.harness.desktop";
const PENDING_PREFIX: &str = "ai.deepseek.harness.desktop.pending-delete-";

pub fn prepare_and_spawn() -> Result<(), RuntimeFailure> {
    let local = local_app_data()?;
    let live = live_root(&local)?;
    if !live.exists() {
        return Ok(());
    }

    validate_live_root(&local, &live)?;
    let nonce = Uuid::new_v4();
    let pending = pending_root(&local, nonce)?;
    fs::rename(&live, &pending)
        .map_err(|cause| RuntimeFailure::internal(format!("无法移出应用数据目录：{cause}")))?;

    if let Err(cause) = spawn_cleanup_copy(nonce) {
        if let Err(rollback) = fs::rename(&pending, &live) {
            return Err(RuntimeFailure::internal(format!(
                "启动后台清理失败：{}；恢复应用数据目录也失败：{rollback}",
                cause.message
            )));
        }
        return Err(cause);
    }
    Ok(())
}

pub fn cleanup_pending(nonce: Uuid) -> Result<(), RuntimeFailure> {
    let local = local_app_data()?;
    let pending = pending_root(&local, nonce)?;
    validate_pending_root(&local, &pending)?;

    let cleanup_result = if pending.exists() {
        remove_tree_without_following_reparse_points(&pending)
    } else {
        Ok(())
    };
    let schedule_result = schedule_current_exe_delete_on_reboot();
    write_cleanup_log(
        nonce,
        cleanup_result.as_ref().err(),
        schedule_result.as_ref().err(),
    );
    cleanup_result?;
    schedule_result
}

fn local_app_data() -> Result<PathBuf, RuntimeFailure> {
    std::env::var_os("LOCALAPPDATA")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| RuntimeFailure::internal("无法确定 LOCALAPPDATA"))
}

fn live_root(local: &Path) -> Result<PathBuf, RuntimeFailure> {
    let path = local.join(APP_IDENTIFIER);
    validate_live_root(local, &path)?;
    Ok(path)
}

fn pending_root(local: &Path, nonce: Uuid) -> Result<PathBuf, RuntimeFailure> {
    let path = local.join(format!("{PENDING_PREFIX}{nonce}"));
    validate_pending_root(local, &path)?;
    Ok(path)
}

fn validate_live_root(local: &Path, candidate: &Path) -> Result<(), RuntimeFailure> {
    validate_direct_child(local, candidate, APP_IDENTIFIER)
}

fn validate_pending_root(local: &Path, candidate: &Path) -> Result<(), RuntimeFailure> {
    let name = candidate
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| RuntimeFailure::internal("待清理目录名称无效"))?;
    let nonce = name
        .strip_prefix(PENDING_PREFIX)
        .ok_or_else(|| RuntimeFailure::internal("待清理目录前缀无效"))?;
    Uuid::parse_str(nonce).map_err(|_| RuntimeFailure::internal("待清理目录标识无效"))?;
    validate_direct_child(local, candidate, name)
}

fn validate_direct_child(
    local: &Path,
    candidate: &Path,
    expected_name: &str,
) -> Result<(), RuntimeFailure> {
    if candidate.parent() != Some(local)
        || candidate.file_name().and_then(|value| value.to_str()) != Some(expected_name)
    {
        return Err(RuntimeFailure::internal("拒绝清理非应用数据目录"));
    }
    Ok(())
}

fn spawn_cleanup_copy(nonce: Uuid) -> Result<(), RuntimeFailure> {
    let source = std::env::current_exe().map_err(RuntimeFailure::internal)?;
    let helper = std::env::temp_dir().join(format!("deepseek-harness-cleanup-{nonce}.exe"));
    fs::copy(&source, &helper)
        .map_err(|cause| RuntimeFailure::internal(format!("无法创建后台清理程序：{cause}")))?;

    let result = spawn_detached(&helper, nonce);
    if result.is_err() {
        let _ = fs::remove_file(&helper);
    }
    result
}

#[cfg(windows)]
fn spawn_detached(helper: &Path, nonce: Uuid) -> Result<(), RuntimeFailure> {
    use std::os::windows::process::CommandExt;

    const DETACHED_PROCESS: u32 = 0x0000_0008;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    Command::new(helper)
        .arg("--cleanup-pending")
        .arg(nonce.to_string())
        .creation_flags(DETACHED_PROCESS | CREATE_NO_WINDOW)
        .spawn()
        .map(|_| ())
        .map_err(|cause| RuntimeFailure::internal(format!("无法启动后台清理程序：{cause}")))
}

#[cfg(not(windows))]
fn spawn_detached(helper: &Path, nonce: Uuid) -> Result<(), RuntimeFailure> {
    Command::new(helper)
        .arg("--cleanup-pending")
        .arg(nonce.to_string())
        .spawn()
        .map(|_| ())
        .map_err(|cause| RuntimeFailure::internal(format!("无法启动后台清理程序：{cause}")))
}

fn remove_tree_without_following_reparse_points(path: &Path) -> Result<(), RuntimeFailure> {
    let metadata = fs::symlink_metadata(path).map_err(RuntimeFailure::internal)?;
    if is_link_or_reparse_point(&metadata) {
        return remove_link(path, metadata.is_dir());
    }
    if !metadata.is_dir() {
        clear_readonly(path, &metadata)?;
        return fs::remove_file(path).map_err(RuntimeFailure::internal);
    }

    for entry in fs::read_dir(path).map_err(RuntimeFailure::internal)? {
        let entry = entry.map_err(RuntimeFailure::internal)?;
        remove_tree_without_following_reparse_points(&entry.path())?;
    }
    fs::remove_dir(path).map_err(RuntimeFailure::internal)
}

#[cfg(windows)]
fn is_link_or_reparse_point(metadata: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    metadata.file_type().is_symlink()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

#[cfg(not(windows))]
fn is_link_or_reparse_point(metadata: &fs::Metadata) -> bool {
    metadata.file_type().is_symlink()
}

fn remove_link(path: &Path, is_dir: bool) -> Result<(), RuntimeFailure> {
    let result = if is_dir {
        fs::remove_dir(path)
    } else {
        fs::remove_file(path)
    };
    result.map_err(RuntimeFailure::internal)
}

fn clear_readonly(path: &Path, metadata: &fs::Metadata) -> Result<(), RuntimeFailure> {
    let mut permissions = metadata.permissions();
    if permissions.readonly() {
        permissions.set_readonly(false);
        fs::set_permissions(path, permissions).map_err(RuntimeFailure::internal)?;
    }
    Ok(())
}

#[cfg(windows)]
fn schedule_current_exe_delete_on_reboot() -> Result<(), RuntimeFailure> {
    use std::os::windows::ffi::OsStrExt;

    use windows_sys::Win32::Storage::FileSystem::{MOVEFILE_DELAY_UNTIL_REBOOT, MoveFileExW};

    let current = std::env::current_exe().map_err(RuntimeFailure::internal)?;
    let mut wide = current.as_os_str().encode_wide().collect::<Vec<_>>();
    wide.push(0);
    let scheduled =
        unsafe { MoveFileExW(wide.as_ptr(), std::ptr::null(), MOVEFILE_DELAY_UNTIL_REBOOT) };
    if scheduled == 0 {
        return Err(RuntimeFailure::internal(format!(
            "无法安排后台清理程序自删除：{}",
            std::io::Error::last_os_error()
        )));
    }
    Ok(())
}

#[cfg(not(windows))]
fn schedule_current_exe_delete_on_reboot() -> Result<(), RuntimeFailure> {
    Ok(())
}

fn write_cleanup_log(
    nonce: Uuid,
    cleanup_error: Option<&RuntimeFailure>,
    schedule_error: Option<&RuntimeFailure>,
) {
    let status = if cleanup_error.is_none() && schedule_error.is_none() {
        "ok".to_string()
    } else {
        format!(
            "cleanup_error={}; schedule_error={}",
            cleanup_error
                .map(|value| value.message.as_str())
                .unwrap_or("none"),
            schedule_error
                .map(|value| value.message.as_str())
                .unwrap_or("none")
        )
    };
    let path = std::env::temp_dir().join(format!("deepseek-harness-cleanup-{nonce}.log"));
    let _ = fs::write(path, status);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pending_target_is_derived_from_a_fixed_parent_and_nonce() {
        let local = PathBuf::from(r"C:\Users\test\AppData\Local");
        let nonce = Uuid::parse_str("4b8bbca3-fd7f-4c6d-9111-2d955457047a").unwrap();
        assert_eq!(
            pending_root(&local, nonce).unwrap(),
            local.join(
                "ai.deepseek.harness.desktop.pending-delete-4b8bbca3-fd7f-4c6d-9111-2d955457047a"
            )
        );
        assert!(validate_pending_root(&local, &local.join(APP_IDENTIFIER)).is_err());
        assert!(validate_pending_root(&local, &PathBuf::from(r"C:\Users\test")).is_err());
    }

    #[test]
    fn removes_a_tree_without_following_directory_links() {
        let root = tempfile::tempdir().unwrap();
        let pending = root
            .path()
            .join(format!("{PENDING_PREFIX}{}", Uuid::new_v4()));
        let outside = root.path().join("outside");
        fs::create_dir_all(pending.join("nested")).unwrap();
        fs::create_dir_all(&outside).unwrap();
        fs::write(pending.join("nested/file.txt"), "delete").unwrap();
        fs::write(outside.join("keep.txt"), "keep").unwrap();

        #[cfg(windows)]
        let link_created =
            std::os::windows::fs::symlink_dir(&outside, pending.join("outside-link")).is_ok();
        #[cfg(unix)]
        let link_created =
            std::os::unix::fs::symlink(&outside, pending.join("outside-link")).is_ok();

        remove_tree_without_following_reparse_points(&pending).unwrap();
        assert!(!pending.exists());
        assert_eq!(
            fs::read_to_string(outside.join("keep.txt")).unwrap(),
            "keep"
        );
        if link_created {
            assert!(outside.exists());
        }
    }
}
