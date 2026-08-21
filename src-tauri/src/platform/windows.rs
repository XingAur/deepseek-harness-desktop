use std::{
    ffi::{OsString, c_void},
    mem::size_of,
    os::windows::{ffi::OsStringExt, process::CommandExt},
    path::{Path, PathBuf},
    process::Command,
};

use windows_sys::Win32::{
    Foundation::{CloseHandle, INVALID_HANDLE_VALUE, WAIT_OBJECT_0, WAIT_TIMEOUT},
    System::{
        Com::CoTaskMemFree,
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW,
            TH32CS_SNAPPROCESS,
        },
        Threading::{
            OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
            QueryFullProcessImageNameW, WaitForSingleObject,
        },
    },
    UI::Shell::{FOLDERID_Documents, SHGetKnownFolderPath},
};
use windows_sys::core::GUID;

use super::{PlatformAdapter, ProcessIdentity, normalize_legacy_roots};
use crate::runtime::{RuntimeFailure, model::RuntimeFailureCode};

pub struct WindowsPlatformAdapter;

impl PlatformAdapter for WindowsPlatformAdapter {
    fn legacy_data_roots(&self, stable_root: &Path) -> Vec<PathBuf> {
        let candidates = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .map(|root| root.join("DeepSeekHarnessDesktop"))
            .into_iter()
            .collect();
        normalize_legacy_roots(stable_root, candidates)
    }

    fn documents_dir(&self) -> Result<PathBuf, RuntimeFailure> {
        known_folder_path(&FOLDERID_Documents)
    }

    fn move_to_recycle_bin(&self, path: &Path) -> Result<(), crate::runtime::RuntimeFailure> {
        trash::delete(path).map_err(|error| {
            crate::runtime::RuntimeFailure::internal(format!(
                "无法把项目目录移入回收站 {}：{error}",
                path.display()
            ))
        })
    }

    fn process_inventory(&self) -> Result<Vec<ProcessIdentity>, RuntimeFailure> {
        process_inventory()
    }

    fn terminate_process_tree(&self, pid: u32) -> Result<(), RuntimeFailure> {
        if !self.process_is_running(pid)? {
            return Ok(());
        }
        let status = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(0x08000000)
            .status()
            .map_err(RuntimeFailure::internal)?;
        if status.success() {
            Ok(())
        } else {
            Err(RuntimeFailure::new(
                RuntimeFailureCode::Process,
                format!("无法关闭受管 Runtime 进程 {pid}"),
            ))
        }
    }

    fn process_is_running(&self, pid: u32) -> Result<bool, RuntimeFailure> {
        let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
        if handle.is_null() {
            let cause = std::io::Error::last_os_error();
            return match cause.raw_os_error() {
                Some(87) => Ok(false),
                _ => Err(RuntimeFailure::internal(cause)),
            };
        }
        let result = unsafe { WaitForSingleObject(handle, 0) };
        unsafe {
            CloseHandle(handle);
        }
        match result {
            WAIT_TIMEOUT => Ok(true),
            WAIT_OBJECT_0 => Ok(false),
            _ => Err(RuntimeFailure::internal(format!(
                "读取受管 Runtime 进程 {pid} 状态失败"
            ))),
        }
    }
}

fn process_inventory() -> Result<Vec<ProcessIdentity>, RuntimeFailure> {
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(RuntimeFailure::internal(std::io::Error::last_os_error()));
    }

    let mut entry = PROCESSENTRY32W {
        dwSize: size_of::<PROCESSENTRY32W>() as u32,
        ..Default::default()
    };
    let mut processes = Vec::new();
    let mut has_entry = unsafe { Process32FirstW(snapshot, &mut entry) } != 0;
    while has_entry {
        if let Some(executable) = process_executable(entry.th32ProcessID) {
            processes.push(ProcessIdentity {
                pid: entry.th32ProcessID,
                parent_pid: entry.th32ParentProcessID,
                executable,
            });
        }
        has_entry = unsafe { Process32NextW(snapshot, &mut entry) } != 0;
    }
    unsafe {
        CloseHandle(snapshot);
    }
    Ok(processes)
}

fn process_executable(pid: u32) -> Option<PathBuf> {
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        return None;
    }
    let mut buffer = vec![0u16; 32_768];
    let mut size = buffer.len() as u32;
    let success = unsafe { QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut size) };
    unsafe {
        CloseHandle(handle);
    }
    if success == 0 || size == 0 {
        return None;
    }
    buffer.truncate(size as usize);
    Some(PathBuf::from(OsString::from_wide(&buffer)))
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::{PlatformAdapter, WindowsPlatformAdapter, process_inventory};

    #[test]
    fn inventory_resolves_the_current_process_executable() {
        let current = std::process::id();
        let processes = process_inventory().unwrap();
        let identity = processes
            .iter()
            .find(|process| process.pid == current)
            .expect("current test process must be visible in the Windows inventory");
        assert!(identity.executable.is_absolute());
        assert!(identity.executable.is_file());
    }

    #[test]
    fn documents_directory_is_absolute_and_does_not_follow_userprofile_override() {
        static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _guard = ENV_LOCK.lock().unwrap();
        let adapter = WindowsPlatformAdapter;
        let expected = adapter.documents_dir().unwrap();
        let original = std::env::var_os("USERPROFILE");
        unsafe { std::env::set_var("USERPROFILE", r"Z:\attacker-controlled") };
        let actual = adapter.documents_dir().unwrap();
        match original {
            Some(value) => unsafe { std::env::set_var("USERPROFILE", value) },
            None => unsafe { std::env::remove_var("USERPROFILE") },
        }
        assert!(actual.is_absolute());
        assert_eq!(actual, expected);
        assert_ne!(actual, PathBuf::from(r"Z:\attacker-controlled"));
    }
}

fn known_folder_path(folder_id: &GUID) -> Result<PathBuf, RuntimeFailure> {
    let mut raw = std::ptr::null_mut();
    let result = unsafe { SHGetKnownFolderPath(folder_id, 0, std::ptr::null_mut(), &mut raw) };
    if result < 0 || raw.is_null() {
        if !raw.is_null() {
            unsafe { CoTaskMemFree(raw.cast::<c_void>()) };
        }
        return Err(RuntimeFailure::internal(
            "无法找到当前用户的文档目录，请检查系统目录设置",
        ));
    }
    let mut len = 0usize;
    unsafe {
        while *raw.add(len) != 0 {
            len += 1;
        }
    }
    let value = OsString::from_wide(unsafe { std::slice::from_raw_parts(raw, len) });
    unsafe { CoTaskMemFree(raw.cast::<c_void>()) };
    let path = PathBuf::from(value);
    if !path.is_absolute() {
        return Err(RuntimeFailure::internal("系统返回的文档目录无效"));
    }
    Ok(path)
}
