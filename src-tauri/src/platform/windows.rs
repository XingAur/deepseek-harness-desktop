use std::{
    ffi::{OsString, c_void},
    mem::size_of,
    os::windows::{ffi::OsStringExt, process::CommandExt},
    os::windows::fs::MetadataExt,
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

const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;

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
        resolve_documents_dir()
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

/// 解析用户「文档」目录（只查询，不创建）。运行时与卸载助手
/// （data_cleanup::documents_folder 经 platform::current() 走到这里）共用本实现：
/// e2e 构建下 DSH_E2E_DOCUMENTS_ROOT 会把「文档」重定向到带所有权标记的测试根，
/// 两侧必须解析到同一目录，否则卸载时的受管 Projects 过滤会把全部登记项静默排除；
/// 非 e2e 构建不读该环境变量，直接回退系统 Known Folder。
pub(crate) fn resolve_documents_dir() -> Result<PathBuf, RuntimeFailure> {
    #[cfg(feature = "e2e")]
    if let Some(root) = std::env::var_os("DSH_E2E_DOCUMENTS_ROOT") {
        let root = PathBuf::from(root);
        if !root.is_absolute() {
            return Err(RuntimeFailure::internal("E2E 文档目录必须是绝对路径"));
        }
        let marker = root.join(".dsh-e2e-documents-owned");
        if has_reparse_components(&root)
            || has_reparse_components(&marker)
            || std::fs::read_to_string(&marker).map(|v| v != "E2E-owned").unwrap_or(true) {
            return Err(RuntimeFailure::internal("E2E 文档目录缺少所有权标记"));
        }
        return Ok(root);
    }
    known_folder_path(&FOLDERID_Documents)
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

const INTERNET_SETTINGS_SUBKEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings";

/// 读取用户在系统中配置的 HTTP 代理，供应用内更新器使用。
/// tauri-plugin-updater 未启用 reqwest 的 system-proxy 特性，读不到
/// Windows“系统代理”（Clash/v2rayN 等写入注册表的配置）。
/// 这里只把更新器流量路由到用户自己配置的代理；更新端点、TLS 与
/// minisign 签名校验全部保持不变。AutoConfigURL（PAC）无法在本地
/// 求值，暂不支持、静默跳过。
pub(crate) fn updater_proxy() -> Option<url::Url> {
    let settings = winreg::RegKey::predef(winreg::enums::HKEY_CURRENT_USER)
        .open_subkey(INTERNET_SETTINGS_SUBKEY)
        .ok()?;
    let enable: u32 = settings.get_value("ProxyEnable").ok()?;
    let server: String = settings.get_value("ProxyServer").ok()?;
    resolve_registry_proxy(enable, &server).and_then(|value| url::Url::parse(&value).ok())
}

fn resolve_registry_proxy(proxy_enable: u32, proxy_server: &str) -> Option<String> {
    if proxy_enable == 0 {
        return None;
    }
    parse_proxy_server(proxy_server)
}

fn parse_proxy_server(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    let lowered = value.to_ascii_lowercase();
    // reqwest 未启用 socks 特性，纯 socks 代理用不了，回退为直连。
    if lowered.starts_with("socks://")
        || lowered.starts_with("socks4://")
        || lowered.starts_with("socks5://")
    {
        return None;
    }
    if lowered.starts_with("http://") || lowered.starts_with("https://") {
        return Some(value.to_string());
    }
    if value.contains('=') {
        return parse_per_scheme_proxy(value);
    }
    parse_host_port(value)
}

/// ProxyServer 形如 `http=1.2.3.4:80;https=5.6.7.8:443` 时按协议挑选；
/// 只支持 http/https 条目，代理 URL 统一用 http://（HTTP CONNECT）。
fn parse_per_scheme_proxy(value: &str) -> Option<String> {
    let mut http = None;
    let mut https = None;
    for entry in value.split(';') {
        let entry = entry.trim();
        if entry.is_empty() || entry.contains('<') {
            continue;
        }
        let Some((scheme, authority)) = entry.split_once('=') else {
            continue;
        };
        let validated = parse_host_port(authority);
        match scheme.trim().to_ascii_lowercase().as_str() {
            "https" => https = https.or(validated),
            "http" => http = http.or(validated),
            _ => {}
        }
    }
    https.or(http)
}

fn parse_host_port(value: &str) -> Option<String> {
    let value = value.trim();
    let (host, port) = value.rsplit_once(':')?;
    let port: u16 = port.trim().parse().ok()?;
    let host = host.trim();
    if host.is_empty() || (host.contains(':') && !host.starts_with('[')) {
        // 未加方括号的 IPv6 无法表达为代理 URL。
        return None;
    }
    Some(format!("http://{host}:{port}"))
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::{
        PlatformAdapter, WindowsPlatformAdapter, parse_proxy_server, process_inventory,
        resolve_documents_dir, resolve_registry_proxy,
    };

    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    struct EnvGuard(&'static str, Option<std::ffi::OsString>);
    impl Drop for EnvGuard { fn drop(&mut self) { match self.1.take() { Some(v) => unsafe { std::env::set_var(self.0, v) }, None => unsafe { std::env::remove_var(self.0) } } } }

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
        let _guard = ENV_LOCK.lock().unwrap();
        let adapter = WindowsPlatformAdapter;
        let expected = adapter.documents_dir().unwrap();
        let _env = EnvGuard("USERPROFILE", std::env::var_os("USERPROFILE"));
        unsafe { std::env::set_var("USERPROFILE", r"Z:\attacker-controlled") };
        let actual = adapter.documents_dir().unwrap();
        assert!(actual.is_absolute());
        assert_eq!(actual, expected);
        assert_ne!(actual, PathBuf::from(r"Z:\attacker-controlled"));
    }

    // 非 e2e 构建必须完全不读 DSH_E2E_DOCUMENTS_ROOT：卸载助手与运行时都直连
    // 系统 Known Folder，环境变量不能影响「文档」目录解析。
    #[cfg(not(feature = "e2e"))]
    #[test]
    fn documents_resolution_ignores_the_e2e_override_outside_e2e_builds() {
        let _guard = ENV_LOCK.lock().unwrap();
        let expected = resolve_documents_dir().unwrap();
        let _env = EnvGuard("DSH_E2E_DOCUMENTS_ROOT", std::env::var_os("DSH_E2E_DOCUMENTS_ROOT"));
        unsafe { std::env::set_var("DSH_E2E_DOCUMENTS_ROOT", r"Z:\attacker-controlled") };
        let actual = resolve_documents_dir().unwrap();
        assert!(actual.is_absolute());
        assert_eq!(actual, expected);
        assert_ne!(actual, PathBuf::from(r"Z:\attacker-controlled"));
    }

    // 卸载助手（documents_folder）与运行时必须同源解析「文档」；若它退回直连
    // SHGetKnownFolderPath，e2e 构建下会错过重定向根，受管 Projects 过滤会把
    // 全部登记项静默排除。
    #[test]
    fn uninstall_documents_resolution_shares_the_platform_resolver() {
        let _guard = ENV_LOCK.lock().unwrap();
        let via_platform = resolve_documents_dir().unwrap();
        assert!(via_platform.is_absolute());
        assert_eq!(
            crate::data_cleanup::documents_folder().unwrap(),
            via_platform
        );
    }

    #[cfg(feature = "e2e")]
    #[test]
    fn e2e_documents_root_requires_an_absolute_owned_directory() {
        let _guard = ENV_LOCK.lock().unwrap();
        let root = std::env::temp_dir().join(format!("dsh-documents-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let _env = EnvGuard("DSH_E2E_DOCUMENTS_ROOT", std::env::var_os("DSH_E2E_DOCUMENTS_ROOT"));
        unsafe { std::env::set_var("DSH_E2E_DOCUMENTS_ROOT", &root) };
        assert!(WindowsPlatformAdapter.documents_dir().is_err());
        std::fs::write(root.join(".dsh-e2e-documents-owned"), b"wrong").unwrap();
        assert!(WindowsPlatformAdapter.documents_dir().is_err());
        std::fs::write(root.join(".dsh-e2e-documents-owned"), b"E2E-owned").unwrap();
        assert_eq!(WindowsPlatformAdapter.documents_dir().unwrap(), root);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn parses_usable_proxy_server_values() {
        assert_eq!(
            parse_proxy_server("127.0.0.1:7890").as_deref(),
            Some("http://127.0.0.1:7890")
        );
        assert_eq!(
            parse_proxy_server(" 127.0.0.1:7890 ").as_deref(),
            Some("http://127.0.0.1:7890")
        );
        assert_eq!(
            parse_proxy_server("[::1]:8080").as_deref(),
            Some("http://[::1]:8080")
        );
        assert_eq!(
            parse_proxy_server("http://proxy.lan:8080").as_deref(),
            Some("http://proxy.lan:8080")
        );
    }

    #[test]
    fn prefers_the_https_entry_in_per_scheme_proxy_lists() {
        assert_eq!(
            parse_proxy_server("http=1.2.3.4:80;https=5.6.7.8:443;ftp=9.9.9.9:21").as_deref(),
            Some("http://5.6.7.8:443")
        );
        assert_eq!(
            parse_proxy_server("https=;http=1.2.3.4:80").as_deref(),
            Some("http://1.2.3.4:80")
        );
        assert_eq!(parse_proxy_server("ftp=9.9.9.9:21;<local>"), None);
    }

    #[test]
    fn rejects_unusable_proxy_server_values() {
        assert_eq!(parse_proxy_server(""), None);
        assert_eq!(parse_proxy_server("   "), None);
        assert_eq!(parse_proxy_server("localhost"), None);
        assert_eq!(parse_proxy_server("abc:def"), None);
        assert_eq!(parse_proxy_server("::1:8080"), None);
        assert_eq!(parse_proxy_server("socks5://127.0.0.1:1080"), None);
    }

    #[test]
    fn a_disabled_proxy_wins_over_the_server_value() {
        assert_eq!(resolve_registry_proxy(0, "127.0.0.1:7890"), None);
        assert_eq!(
            resolve_registry_proxy(1, "127.0.0.1:7890").as_deref(),
            Some("http://127.0.0.1:7890")
        );
        assert_eq!(resolve_registry_proxy(1, ""), None);
    }
}

fn has_reparse_components(path: &Path) -> bool {
    path.ancestors().any(|component| std::fs::symlink_metadata(component)
        .map(|metadata| metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0)
        .unwrap_or(false))
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
