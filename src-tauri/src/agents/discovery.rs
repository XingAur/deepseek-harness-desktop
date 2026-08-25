use std::{
    collections::BTreeSet,
    fs,
    io::Read,
    path::{Component, Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use super::{
    compatibility::parse_cli_version,
    model::{
        AgentProvider, DiscoveredAgent, DiscoveryDiagnostic, DiscoveryDiagnosticCode,
        DiscoveryResult, DiscoverySource,
    },
};

const VERSION_PROBE_LIMIT: usize = 8 * 1024;
const VERSION_PROBE_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Debug)]
pub struct DiscoveryRequest {
    pub provider: AgentProvider,
    pub explicit_path: Option<PathBuf>,
    pub path_entries: Vec<PathBuf>,
    pub official_locations: Vec<PathBuf>,
}

impl DiscoveryRequest {
    pub fn for_provider(provider: AgentProvider) -> Self {
        let path_entries = std::env::var_os("PATH")
            .map(|value| std::env::split_paths(&value).collect())
            .unwrap_or_default();
        Self {
            provider,
            explicit_path: None,
            path_entries,
            official_locations: official_install_locations(provider),
        }
    }

    pub fn with_explicit_path(mut self, path: PathBuf) -> Self {
        self.explicit_path = Some(path);
        self
    }

    pub fn with_path_entries(mut self, paths: Vec<PathBuf>) -> Self {
        self.path_entries = paths;
        self
    }

    pub fn with_official_locations(mut self, paths: Vec<PathBuf>) -> Self {
        self.official_locations = paths;
        self
    }
}

pub fn discover(request: &DiscoveryRequest) -> Result<DiscoveryResult, String> {
    let mut result = DiscoveryResult {
        provider: request.provider,
        selected: None,
        candidates: Vec::new(),
        diagnostics: Vec::new(),
    };
    let mut seen = BTreeSet::new();

    if let Some(path) = request.explicit_path.as_deref() {
        inspect_candidate(
            request.provider,
            path,
            DiscoverySource::Explicit,
            true,
            &mut seen,
            &mut result,
        );
    }
    for directory in &request.path_entries {
        inspect_directory(
            request.provider,
            directory,
            DiscoverySource::Path,
            false,
            &mut seen,
            &mut result,
        );
    }
    for directory in &request.official_locations {
        inspect_directory(
            request.provider,
            directory,
            DiscoverySource::OfficialLocation,
            false,
            &mut seen,
            &mut result,
        );
    }

    result.selected = result.candidates.first().cloned();
    if result.selected.is_none() && result.diagnostics.is_empty() && request.explicit_path.is_none()
    {
        result.diagnostics.push(DiscoveryDiagnostic {
            code: DiscoveryDiagnosticCode::NotFound,
            message: format!("未找到 {} CLI", request.provider.command_name()),
        });
    }
    Ok(result)
}

fn inspect_directory(
    provider: AgentProvider,
    directory: &Path,
    source: DiscoverySource,
    explicit: bool,
    seen: &mut BTreeSet<String>,
    result: &mut DiscoveryResult,
) {
    for path in command_paths(directory, provider) {
        if path.exists() || fs::symlink_metadata(&path).is_ok() {
            inspect_candidate(provider, &path, source, explicit, seen, result);
        }
    }
}

fn command_paths(directory: &Path, provider: AgentProvider) -> Vec<PathBuf> {
    let command = provider.command_name();
    #[allow(unused_mut)]
    let mut paths = vec![directory.join(command)];
    #[cfg(windows)]
    {
        paths.push(directory.join(format!("{command}.exe")));
        paths.push(directory.join(format!("{command}.cmd")));
        paths.push(directory.join(format!("{command}.bat")));
    }
    paths
}

fn inspect_candidate(
    provider: AgentProvider,
    path: &Path,
    source: DiscoverySource,
    explicit: bool,
    seen: &mut BTreeSet<String>,
    result: &mut DiscoveryResult,
) {
    if !explicit && path_is_nested_private_app(path) {
        result.diagnostics.push(DiscoveryDiagnostic {
            code: DiscoveryDiagnosticCode::PrivateAppBundle,
            message: "自动发现已跳过私有应用包内的 CLI".to_owned(),
        });
        return;
    }

    let canonical = match fs::canonicalize(path) {
        Ok(path) => path,
        Err(_) => {
            result.diagnostics.push(DiscoveryDiagnostic {
                code: DiscoveryDiagnosticCode::SymlinkInvalid,
                message: "CLI 路径无效或符号链接无法解析".to_owned(),
            });
            return;
        }
    };
    let key = comparison_key(&canonical);
    if !seen.insert(key) {
        result.diagnostics.push(DiscoveryDiagnostic {
            code: DiscoveryDiagnosticCode::Duplicate,
            message: "重复的 CLI 路径已跳过".to_owned(),
        });
        return;
    }
    let metadata = match fs::metadata(&canonical) {
        Ok(metadata) => metadata,
        Err(_) => {
            result.diagnostics.push(DiscoveryDiagnostic {
                code: DiscoveryDiagnosticCode::InvalidPath,
                message: "CLI 文件无法读取".to_owned(),
            });
            return;
        }
    };
    if !metadata.is_file() || !is_executable(&metadata) {
        result.diagnostics.push(DiscoveryDiagnostic {
            code: DiscoveryDiagnosticCode::NonExecutable,
            message: "CLI 不是可执行文件".to_owned(),
        });
        return;
    }

    let output = match probe_version(&canonical) {
        Ok(output) => output,
        Err(ProbeError::Timeout) => {
            result.diagnostics.push(DiscoveryDiagnostic {
                code: DiscoveryDiagnosticCode::VersionProbeFailed,
                message: "CLI 版本检查超时".to_owned(),
            });
            return;
        }
        Err(ProbeError::Exit) | Err(ProbeError::Io) => {
            result.diagnostics.push(DiscoveryDiagnostic {
                code: DiscoveryDiagnosticCode::VersionProbeFailed,
                message: "CLI 版本检查失败".to_owned(),
            });
            return;
        }
    };
    let version = match parse_cli_version(&output) {
        Ok(version) => version,
        Err(_) => {
            result.diagnostics.push(DiscoveryDiagnostic {
                code: DiscoveryDiagnosticCode::VersionParseFailed,
                message: "CLI 返回的版本无法识别".to_owned(),
            });
            return;
        }
    };
    result.candidates.push(DiscoveredAgent {
        provider,
        path: canonical,
        source,
        version: Some(version),
        // CLI discovery proves only the external CLI version. The adapter protocol is
        // populated after the private worker completes its real handshake.
        protocol: None,
    });
}

#[derive(Debug)]
enum ProbeError {
    Timeout,
    Exit,
    Io,
}

fn probe_version(path: &Path) -> Result<String, ProbeError> {
    let mut command = if cfg!(windows)
        && path.extension().is_some_and(|value| {
            value.eq_ignore_ascii_case("cmd") || value.eq_ignore_ascii_case("bat")
        }) {
        let mut command = Command::new("cmd");
        command.args(["/C", &path.to_string_lossy()]);
        command
    } else {
        Command::new(path)
    };
    let mut child = command
        .args(["--version"])
        .env_clear()
        .current_dir(path.parent().unwrap_or_else(|| Path::new(".")))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| ProbeError::Io)?;
    let overflow = Arc::new(AtomicBool::new(false));
    let stdout = child.stdout.take().ok_or(ProbeError::Io)?;
    let stderr = child.stderr.take().ok_or(ProbeError::Io)?;
    let stdout_overflow = Arc::clone(&overflow);
    let stdout_thread =
        thread::spawn(move || read_bounded(stdout, VERSION_PROBE_LIMIT, stdout_overflow));
    let stderr_overflow = Arc::clone(&overflow);
    let stderr_thread =
        thread::spawn(move || read_bounded(stderr, VERSION_PROBE_LIMIT, stderr_overflow));

    let deadline = Instant::now() + VERSION_PROBE_TIMEOUT;
    let status = loop {
        if overflow.load(Ordering::Acquire) {
            let _ = child.kill();
            let _ = child.wait();
            break Err(ProbeError::Io);
        }
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(ProbeError::Timeout);
            }
            Ok(None) => thread::sleep(Duration::from_millis(5)),
            Err(_) => break Err(ProbeError::Io),
        }
    };
    let stdout = stdout_thread
        .join()
        .unwrap_or_else(|_| Err(ProbeError::Io))?;
    let stderr = stderr_thread
        .join()
        .unwrap_or_else(|_| Err(ProbeError::Io))?;
    let status = status?;
    if !status.success() {
        return Err(ProbeError::Exit);
    }
    Ok(if stdout.trim().is_empty() {
        stderr
    } else {
        stdout
    })
}

fn read_bounded<R: Read>(
    mut reader: R,
    limit: usize,
    overflow: Arc<AtomicBool>,
) -> Result<String, ProbeError> {
    let mut output = Vec::with_capacity(limit.min(1024));
    let mut buffer = [0_u8; 1024];
    loop {
        let count = reader.read(&mut buffer).map_err(|_| ProbeError::Io)?;
        if count == 0 {
            break;
        }
        if output.len().saturating_add(count) > limit {
            overflow.store(true, Ordering::Release);
            return Err(ProbeError::Io);
        }
        output.extend_from_slice(&buffer[..count]);
    }
    let text = String::from_utf8_lossy(&output);
    Ok(crate::runtime::redaction::redact_bounded(&text, limit))
}

fn is_executable(metadata: &fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        return metadata.permissions().mode() & 0o111 != 0;
    }
    #[cfg(windows)]
    {
        return true;
    }
    #[allow(unreachable_code)]
    false
}

fn path_is_nested_private_app(path: &Path) -> bool {
    path.components().any(|component| {
        matches!(component, Component::Normal(value) if value.to_string_lossy().to_ascii_lowercase().ends_with(".app"))
    })
}

fn comparison_key(path: &Path) -> String {
    let value = path.to_string_lossy().replace('\\', "/");
    if cfg!(windows) {
        value.to_ascii_lowercase()
    } else {
        value
    }
}

fn official_install_locations(provider: AgentProvider) -> Vec<PathBuf> {
    let mut locations = Vec::new();
    let home = dirs_home();
    if let Some(home) = home {
        locations.push(home.join(".local").join("bin"));
        locations.push(home.join(".npm-global").join("bin"));
        // nvm 把每个 Node 版本的全局 bin 放在 ~/.nvm/versions/node/<v>/bin，
        // `npm install -g` 默认安装到当前版本的目录；从新到旧扫描。
        let nvm_root = home.join(".nvm").join("versions").join("node");
        if let Ok(entries) = fs::read_dir(&nvm_root) {
            let mut version_bins: Vec<PathBuf> = entries
                .flatten()
                .map(|entry| entry.path().join("bin"))
                .collect();
            version_bins.sort();
            version_bins.reverse();
            locations.extend(version_bins);
        }
        // volta 的全局 bin。
        locations.push(home.join(".volta").join("bin"));
        if matches!(provider, AgentProvider::Codex) {
            locations.push(home.join(".cargo").join("bin"));
            // ChatGPT 桌面版 / codex plugin 管理的官方 app-server 副本。
            locations.push(home.join(".codex").join("plugins").join(".plugin-appserver"));
        }
    }
    if cfg!(windows) {
        if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA") {
            locations.push(PathBuf::from(local_app_data.clone()).join("Programs"));
            locations.push(PathBuf::from(local_app_data).join("npm"));
        }
        if let Some(app_data) = std::env::var_os("APPDATA") {
            locations.push(PathBuf::from(app_data).join("npm"));
        }
    } else {
        locations.push(PathBuf::from("/usr/local/bin"));
        locations.push(PathBuf::from("/opt/homebrew/bin"));
    }
    locations
}

fn dirs_home() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        std::env::var_os("USERPROFILE").map(PathBuf::from)
    }
    #[cfg(not(windows))]
    {
        std::env::var_os("HOME").map(PathBuf::from)
    }
}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        path::{Path, PathBuf},
    };

    use semver::Version;
    use tempfile::TempDir;

    use super::super::model::{AgentProvider, DiscoveryDiagnosticCode, DiscoverySource};
    use super::{DiscoveryRequest, discover};

    fn executable_fixture(root: &Path, name: &str, output: &str) -> PathBuf {
        #[cfg(unix)]
        {
            let path = root.join(name);
            fs::write(&path, format!("#!/bin/sh\nprintf '%s\\n' '{}'\n", output)).unwrap();
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
            path
        }
        #[cfg(windows)]
        {
            let path = root.join(format!("{name}.cmd"));
            fs::write(&path, format!("@echo off\necho {output}\n")).unwrap();
            path
        }
    }

    #[cfg(windows)]
    fn batch_fixture(root: &Path, name: &str, output: &str) -> PathBuf {
        let path = root.join(format!("{name}.BAT"));
        fs::write(&path, format!("@echo off\necho {output}\n")).unwrap();
        path
    }

    fn request(provider: AgentProvider) -> DiscoveryRequest {
        DiscoveryRequest::for_provider(provider)
    }

    #[cfg(windows)]
    #[test]
    fn batch_script_is_discovered_and_version_probed_case_insensitively() {
        let temp = TempDir::new().unwrap();
        let batch = batch_fixture(temp.path(), "codex", "codex 1.2.3");
        let result =
            discover(&request(AgentProvider::Codex).with_explicit_path(batch.clone())).unwrap();
        assert_eq!(
            result.selected.unwrap().version,
            Some(Version::parse("1.2.3").unwrap())
        );
    }

    #[test]
    fn explicit_path_wins_over_path_and_official_locations() {
        let temp = TempDir::new().unwrap();
        let explicit = executable_fixture(temp.path(), "explicit", "codex 1.2.3");
        let path_binary = executable_fixture(temp.path(), "codex", "codex 2.0.0");
        let official_binary = executable_fixture(temp.path(), "official", "codex 3.0.0");

        let result = discover(
            &request(AgentProvider::Codex)
                .with_explicit_path(explicit.clone())
                .with_path_entries(vec![temp.path().to_path_buf()])
                .with_official_locations(vec![official_binary.parent().unwrap().to_path_buf()]),
        )
        .unwrap();

        let selected = result.selected.unwrap();
        assert_eq!(selected.source, DiscoverySource::Explicit);
        assert_eq!(selected.path, fs::canonicalize(explicit).unwrap());
        assert_eq!(selected.version, Some(Version::parse("1.2.3").unwrap()));
        assert!(selected.protocol.is_none());
        assert_ne!(selected.path, fs::canonicalize(path_binary).unwrap());
    }

    #[test]
    fn path_resolution_is_deterministic_for_spaces_unicode_and_duplicates() {
        let temp = TempDir::new().unwrap();
        let folder = temp.path().join("路径 with spaces");
        fs::create_dir_all(&folder).unwrap();
        let binary = executable_fixture(&folder, "codex", "codex v1.4.0");
        let duplicate = temp.path().join("duplicate");
        fs::create_dir_all(&duplicate).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&binary, duplicate.join("codex")).unwrap();

        let result = discover(
            &request(AgentProvider::Codex)
                .with_path_entries(vec![folder.clone(), duplicate])
                .with_official_locations(Vec::new()),
        )
        .unwrap();

        assert_eq!(result.candidates.len(), 1);
        assert_eq!(
            result.selected.unwrap().path,
            fs::canonicalize(binary).unwrap()
        );
    }

    #[test]
    fn invalid_candidates_are_skipped_with_safe_diagnostics() {
        let temp = TempDir::new().unwrap();
        let non_executable = temp.path().join("codex");
        fs::write(&non_executable, "not executable").unwrap();
        let bundle_dir = temp.path().join("Other.app/Contents/MacOS");
        fs::create_dir_all(&bundle_dir).unwrap();
        let _ = executable_fixture(&bundle_dir, "codex", "codex 9.9.9");

        let result = discover(
            &request(AgentProvider::Codex)
                .with_path_entries(vec![temp.path().to_path_buf()])
                .with_official_locations(Vec::new()),
        )
        .unwrap();

        assert!(result.selected.is_none());
        assert!(
            result
                .diagnostics
                .iter()
                .any(|item| item.code == DiscoveryDiagnosticCode::NonExecutable)
        );
        assert!(
            result
                .diagnostics
                .iter()
                .all(|item| !item.message.contains("secret") && !item.message.contains("token"))
        );
    }

    #[test]
    fn explicit_private_bundle_path_is_allowed_but_auto_selection_is_not() {
        let temp = TempDir::new().unwrap();
        let bundle_dir = temp.path().join("Private.app/Contents/MacOS");
        fs::create_dir_all(&bundle_dir).unwrap();
        let explicit = executable_fixture(&bundle_dir, "codex", "codex 1.0.0");

        let auto = discover(
            &request(AgentProvider::Codex)
                .with_path_entries(vec![bundle_dir.clone()])
                .with_official_locations(Vec::new()),
        )
        .unwrap();
        assert!(auto.selected.is_none());

        let explicit_result = discover(
            &request(AgentProvider::Codex)
                .with_explicit_path(explicit)
                .with_path_entries(Vec::new())
                .with_official_locations(Vec::new()),
        )
        .unwrap();
        assert_eq!(
            explicit_result.selected.unwrap().source,
            DiscoverySource::Explicit
        );
    }

    #[test]
    fn version_probe_failure_is_diagnostic_and_does_not_read_credential_files() {
        let temp = TempDir::new().unwrap();
        let binary = executable_fixture(temp.path(), "codex", "not-a-version");
        fs::write(
            temp.path().join(".codex-auth.json"),
            "token=must-not-be-read",
        )
        .unwrap();

        let result = discover(
            &request(AgentProvider::Codex)
                .with_explicit_path(binary)
                .with_path_entries(Vec::new())
                .with_official_locations(Vec::new()),
        )
        .unwrap();

        assert!(result.selected.is_none());
        assert!(
            result
                .diagnostics
                .iter()
                .any(|item| item.code == DiscoveryDiagnosticCode::VersionParseFailed)
        );
        let serialized = serde_json::to_string(&result.diagnostics).unwrap();
        assert!(!serialized.contains("must-not-be-read"));
    }
}
