use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

use uuid::Uuid;

use crate::{
    runtime::model::RuntimeFailure, safe_remove::remove_tree_without_following_reparse_points,
};

#[cfg(feature = "e2e")]
pub const APP_IDENTIFIER: &str = "ai.deepseek.harness.desktop.e2e";
#[cfg(not(feature = "e2e"))]
pub const APP_IDENTIFIER: &str = "ai.deepseek.harness.desktop";
#[cfg(feature = "e2e")]
const PENDING_PREFIX: &str = "ai.deepseek.harness.desktop.e2e.pending-delete-";
#[cfg(not(feature = "e2e"))]
const PENDING_PREFIX: &str = "ai.deepseek.harness.desktop.pending-delete-";

pub fn prepare_and_spawn() -> Result<(), RuntimeFailure> {
    let local = local_app_data()?;
    let live = live_app_data_root()?;
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

fn known_folder_result(
    resolver: impl FnOnce() -> Result<PathBuf, RuntimeFailure>,
) -> Result<PathBuf, RuntimeFailure> {
    let path = resolver()?;
    if !path.is_absolute() || path.as_os_str().is_empty() {
        return Err(RuntimeFailure::internal("系统返回的用户目录无效"));
    }
    Ok(path)
}

#[cfg(windows)]
fn local_app_data() -> Result<PathBuf, RuntimeFailure> {
    known_folder_result(|| {
        windows_known_folder(&windows_sys::Win32::UI::Shell::FOLDERID_LocalAppData)
    })
}

#[cfg(not(windows))]
fn local_app_data() -> Result<PathBuf, RuntimeFailure> {
    known_folder_result(|| {
        std::env::var_os("XDG_DATA_HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| RuntimeFailure::internal("无法确定应用数据目录"))
    })
}

#[cfg(windows)]
pub(crate) fn current_user_profile_root() -> Result<PathBuf, RuntimeFailure> {
    known_folder_result(|| windows_known_folder(&windows_sys::Win32::UI::Shell::FOLDERID_Profile))
}

#[cfg(not(windows))]
pub(crate) fn current_user_profile_root() -> Result<PathBuf, RuntimeFailure> {
    known_folder_result(|| {
        std::env::var_os("HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| RuntimeFailure::internal("无法确定当前用户目录，已拒绝删除"))
    })
}

/// 解析用户「文档」已知文件夹（只查询，不创建目录）。
/// 目前仅卸载器用它定位受管 Projects 根（见 projects/uninstall.rs）；
/// 非 Windows 卸载器不存在该流程，因此直接报错。
/// Windows 上必须走 platform 的共用解析器：e2e 构建下 DSH_E2E_DOCUMENTS_ROOT
/// 会重定向「文档」目录，卸载助手若直连 SHGetKnownFolderPath 就会看到另一个根，
/// 受管 Projects 过滤会把全部登记项静默排除。
#[cfg(windows)]
pub(crate) fn documents_folder() -> Result<PathBuf, RuntimeFailure> {
    crate::platform::current().documents_dir()
}

#[cfg(not(windows))]
pub(crate) fn documents_folder() -> Result<PathBuf, RuntimeFailure> {
    Err(RuntimeFailure::internal(
        "「文档」目录解析仅在 Windows 卸载器中受支持",
    ))
}

#[cfg(windows)]
fn windows_known_folder(folder_id: &windows_sys::core::GUID) -> Result<PathBuf, RuntimeFailure> {
    use std::{ffi::c_void, os::windows::ffi::OsStringExt};

    use windows_sys::Win32::{System::Com::CoTaskMemFree, UI::Shell::SHGetKnownFolderPath};

    let mut raw = std::ptr::null_mut();
    let result = unsafe { SHGetKnownFolderPath(folder_id, 0, std::ptr::null_mut(), &mut raw) };
    if result < 0 || raw.is_null() {
        if !raw.is_null() {
            unsafe { CoTaskMemFree(raw.cast::<c_void>()) };
        }
        return Err(RuntimeFailure::internal(format!(
            "无法从 Windows Known Folder 获取用户目录（HRESULT {result:#x}）"
        )));
    }

    let mut len = 0usize;
    unsafe {
        while *raw.add(len) != 0 {
            len += 1;
        }
    }
    let value = std::ffi::OsString::from_wide(unsafe { std::slice::from_raw_parts(raw, len) });
    unsafe { CoTaskMemFree(raw.cast::<c_void>()) };
    Ok(PathBuf::from(value))
}

pub(crate) fn live_app_data_root() -> Result<PathBuf, RuntimeFailure> {
    let local = local_app_data()?;
    live_root(&local)
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

    struct EnvGuard(&'static str, Option<std::ffi::OsString>);
    impl Drop for EnvGuard { fn drop(&mut self) { match self.1.take() { Some(v) => unsafe { std::env::set_var(self.0, v) }, None => unsafe { std::env::remove_var(self.0) } } } }

    #[test]
    fn injected_known_folder_must_be_absolute_and_non_empty() {
        assert!(known_folder_result(|| Ok(PathBuf::new())).is_err());
        assert!(known_folder_result(|| Ok(PathBuf::from("relative"))).is_err());
        let expected = if cfg!(windows) {
            PathBuf::from(r"C:\Users\test\AppData\Local")
        } else {
            PathBuf::from("/var/tmp/app-data")
        };
        assert_eq!(
            known_folder_result(|| Ok(expected.clone())).unwrap(),
            expected
        );
    }

    #[cfg(windows)]
    #[test]
    fn local_app_data_does_not_trust_the_environment_variable() {
        static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _guard = ENV_LOCK.lock().unwrap();
        let _env = EnvGuard("LOCALAPPDATA", std::env::var_os("LOCALAPPDATA"));
        let expected = local_app_data().unwrap();
        unsafe { std::env::set_var("LOCALAPPDATA", r"Z:\attacker-controlled") };
        let actual = local_app_data().unwrap();
        assert_eq!(actual, expected);
        assert_ne!(actual, PathBuf::from(r"Z:\attacker-controlled"));
    }

    #[test]
    fn pending_target_is_derived_from_a_fixed_parent_and_nonce() {
        let local = PathBuf::from(r"C:\Users\test\AppData\Local");
        let nonce = Uuid::parse_str("4b8bbca3-fd7f-4c6d-9111-2d955457047a").unwrap();
        assert_eq!(
            pending_root(&local, nonce).unwrap(),
            local.join(
                format!("{PENDING_PREFIX}4b8bbca3-fd7f-4c6d-9111-2d955457047a")
            )
        );
        assert!(validate_pending_root(&local, &local.join(APP_IDENTIFIER)).is_err());
        assert!(validate_pending_root(&local, &PathBuf::from(r"C:\Users\test")).is_err());
    }

    #[cfg(not(feature = "e2e"))]
    #[test]
    fn production_cleanup_identifiers_are_fixed() {
        assert_eq!(APP_IDENTIFIER, "ai.deepseek.harness.desktop");
        assert_eq!(PENDING_PREFIX, "ai.deepseek.harness.desktop.pending-delete-");
    }

    #[cfg(feature = "e2e")]
    #[test]
    fn e2e_cleanup_identifier_cannot_target_production_data() {
        assert_eq!(APP_IDENTIFIER, "ai.deepseek.harness.desktop.e2e");
        assert_ne!(APP_IDENTIFIER, "ai.deepseek.harness.desktop");
        assert_eq!(PENDING_PREFIX, "ai.deepseek.harness.desktop.e2e.pending-delete-");
    }
}
