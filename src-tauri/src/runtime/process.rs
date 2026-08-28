use std::{
    ffi::OsString,
    path::PathBuf,
    process::{ExitStatus, Stdio},
    time::Duration,
};

use tokio::{
    fs::OpenOptions,
    io::{AsyncBufReadExt, AsyncRead, AsyncWriteExt, BufReader},
    process::{Child, Command},
    task::JoinHandle,
};

use super::{
    model::{RuntimeFailure, RuntimeFailureCode, RuntimeManifest},
    paths::{RuntimePaths, join_confined},
    redaction::redact_secrets,
};
use crate::profile::model::ProfileRecord;

#[cfg(windows)]
pub(crate) const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub struct ManagedRuntime {
    child: Child,
    log_tasks: Vec<JoinHandle<()>>,
    log_file: Option<String>,
}

impl ManagedRuntime {
    pub fn pid(&self) -> Option<u32> {
        self.child.id()
    }

    pub fn try_exit(&mut self) -> Result<Option<ExitStatus>, RuntimeFailure> {
        self.child.try_wait().map_err(|cause| {
            RuntimeFailure::new(
                RuntimeFailureCode::Process,
                format!("读取 DeepSeek Harness Runtime 状态失败：{cause}"),
            )
        })
    }

    pub fn log_file_name(&self) -> Option<&str> {
        self.log_file.as_deref()
    }

    pub async fn flush_logs(&mut self, budget: Duration) {
        let deadline = tokio::time::Instant::now() + budget;
        for mut task in self.log_tasks.drain(..) {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if tokio::time::timeout(remaining, &mut task).await.is_err() {
                task.abort();
            }
        }
    }

    pub async fn terminate(&mut self) -> Result<(), RuntimeFailure> {
        if let Some(pid) = self.child.id() {
            terminate_tree(pid).await;
            let _ = tokio::time::timeout(Duration::from_secs(5), self.child.wait()).await;
            let _ = self.child.kill().await;
        }
        for task in self.log_tasks.drain(..) {
            task.abort();
        }
        Ok(())
    }

    #[cfg(test)]
    pub async fn spawn_test_exit(exit_code: i32) -> Result<Self, RuntimeFailure> {
        let script = format!("exit {exit_code}");
        #[cfg(windows)]
        let mut command = {
            let mut command = Command::new("cmd");
            command.args(["/C", script.as_str()]);
            command
        };
        #[cfg(unix)]
        let mut command = {
            let mut command = Command::new("sh");
            command.args(["-c", script.as_str()]);
            command
        };
        let child = command
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Process, cause.to_string()))?;
        Ok(Self {
            child,
            log_tasks: Vec::new(),
            log_file: None,
        })
    }

    #[cfg(test)]
    pub async fn spawn_test_sleep() -> Result<Self, RuntimeFailure> {
        #[cfg(windows)]
        let mut command = {
            let mut command = Command::new("cmd");
            command.args(["/C", "ping 127.0.0.1 -n 6 >NUL"]);
            command
        };
        #[cfg(unix)]
        let mut command = {
            let mut command = Command::new("sh");
            command.args(["-c", "sleep 5"]);
            command
        };
        let child = command
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Process, cause.to_string()))?;
        Ok(Self {
            child,
            log_tasks: Vec::new(),
            log_file: None,
        })
    }
}

pub fn runtime_exit_failure(status: ExitStatus) -> RuntimeFailure {
    let detail = status
        .code()
        .map(|code| format!("退出码 {code}"))
        .unwrap_or_else(|| "被系统终止".to_string());
    RuntimeFailure::new(
        RuntimeFailureCode::Process,
        format!("DeepSeek Harness Runtime 已退出（{detail}）"),
    )
}

pub fn reserve_loopback_port() -> Result<u16, RuntimeFailure> {
    let listener = std::net::TcpListener::bind(("127.0.0.1", 0))
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Process, cause.to_string()))?;
    let port = listener
        .local_addr()
        .map_err(RuntimeFailure::internal)?
        .port();
    drop(listener);
    Ok(port)
}

pub async fn spawn_runtime(
    paths: &RuntimePaths,
    manifest: &RuntimeManifest,
    profile: &ProfileRecord,
    generation_id: &str,
    port: u16,
    session_token: &str,
) -> Result<ManagedRuntime, RuntimeFailure> {
    let runtime_dir = paths.version_dir(&manifest.version);
    spawn_runtime_from_dir(
        paths,
        &runtime_dir,
        manifest,
        profile,
        generation_id,
        port,
        session_token,
    )
    .await
}

pub async fn spawn_runtime_from_dir(
    paths: &RuntimePaths,
    runtime_dir: &std::path::Path,
    manifest: &RuntimeManifest,
    profile: &ProfileRecord,
    generation_id: &str,
    port: u16,
    session_token: &str,
) -> Result<ManagedRuntime, RuntimeFailure> {
    let executable = join_confined(&runtime_dir, &manifest.entrypoint, "entrypoint")?;
    if !executable.is_file() {
        return Err(RuntimeFailure::new(
            RuntimeFailureCode::Process,
            format!("Runtime 入口不存在：{}", executable.display()),
        ));
    }
    let args = managed_args(
        manifest
            .args
            .iter()
            .map(|value| {
                value
                    .replace("{port}", &port.to_string())
                    .replace("{sessionToken}", session_token)
            })
            .collect::<Vec<_>>(),
    );
    let mut command = Command::new(&executable);
    for (key, value) in profile_environment(profile, generation_id) {
        command.env(key, value);
    }
    for (key, value) in codex_environment(profile) {
        command.env(key, value);
    }
    command
        .args(args)
        .current_dir(&runtime_dir)
        .env("DSH_DESKTOP_MODE", "advanced")
        .env(
            "DSH_DESKTOP_PLATFORM",
            if cfg!(target_os = "macos") {
                "darwin"
            } else {
                "win32"
            },
        )
        .env("DSH_DESKTOP_SESSION_TOKEN", session_token)
        .env(
            "DSH_DESKTOP_DSH_BIN",
            runtime_dir
                .join("app")
                .join("node_modules")
                .join("@deepseek-ai")
                .join("dsh")
                .join("lib")
                .join("bin.js"),
        )
        .env("DSH_DESKTOP_DSH_VERSION", manifest.dsh_version.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    #[cfg(unix)]
    command.process_group(0);

    let mut child = command.spawn().map_err(|cause| {
        RuntimeFailure::new(
            RuntimeFailureCode::Process,
            format!("启动 DeepSeek Harness Runtime 失败：{cause}"),
        )
    })?;
    let log_file = format!("dsh-{}.log", chrono::Utc::now().format("%Y-%m-%d"));
    let log_path = paths.logs.join(&log_file);
    let mut log_tasks = Vec::new();
    if let Some(stdout) = child.stdout.take() {
        log_tasks.push(pipe_log(stdout, log_path.clone(), "OUT"));
    }
    if let Some(stderr) = child.stderr.take() {
        log_tasks.push(pipe_log(stderr, log_path, "ERR"));
    }
    Ok(ManagedRuntime {
        child,
        log_tasks,
        log_file: Some(log_file),
    })
}

fn managed_args(mut args: Vec<String>) -> Vec<String> {
    if !args.iter().any(|argument| argument == "--no-open") {
        args.push("--no-open".to_string());
    }
    args
}

/// 聊天模型用的 Codex 环境：发现的 CLI 路径 + 隔离状态目录。
///
/// 运行时内的桌面插件把 provider 路由 `codex` 注册进官方 LLM 服务；
/// 注入的变量让它无需再次发现即可拉起官方 CLI，并与常驻 Codex 守护
/// 进程（如 ChatGPT 桌面版）的状态库互不干扰。
fn codex_environment(profile: &ProfileRecord) -> Vec<(OsString, OsString)> {
    let mut env = Vec::new();
    let home = profile.data_root.join("codex-home");
    super::super::agents::runtime::prepare_codex_home_for(&home);
    env.push(("CODEX_HOME".into(), home.as_os_str().to_owned()));
    if let Some(selected) = crate::agents::discovery::discover(
        &crate::agents::discovery::DiscoveryRequest::for_provider(
            crate::agents::model::AgentProvider::Codex,
        ),
    )
    .ok()
    .and_then(|result| result.selected)
    {
        env.push((
            "DSH_DESKTOP_CODEX_CLI".into(),
            selected.path.as_os_str().to_owned(),
        ));
    }
    env
}

fn profile_environment(profile: &ProfileRecord, generation_id: &str) -> Vec<(OsString, OsString)> {
    vec![
        ("DSH_HOME".into(), profile.data_root.as_os_str().to_owned()),
        (
            "DSH_DESKTOP_PROFILE_ID".into(),
            profile.id.to_string().into(),
        ),
        (
            "DSH_DESKTOP_PROFILE_REVISION".into(),
            profile.revision.to_string().into(),
        ),
        ("DSH_DESKTOP_GENERATION_ID".into(), generation_id.into()),
    ]
}

pub(crate) fn pipe_log<R>(reader: R, path: PathBuf, prefix: &'static str) -> JoinHandle<()>
where
    R: AsyncRead + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        // 句柄在整个进程生命周期内复用，避免每行日志都重新打开文件。
        let Ok(mut file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .await
        else {
            return;
        };
        let mut lines = BufReader::new(reader).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let safe = redact_secrets(&line);
            let rendered = format!("{} {prefix} {safe}\n", chrono::Utc::now().to_rfc3339());
            let _ = file.write_all(rendered.as_bytes()).await;
        }
    })
}

#[cfg(windows)]
pub(crate) async fn terminate_tree(pid: u32) {
    let mut command = Command::new("taskkill");
    command
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW);
    let _ = command.status().await;
}

#[cfg(unix)]
pub(crate) async fn terminate_tree(pid: u32) {
    unsafe {
        libc::kill(-(pid as i32), libc::SIGTERM);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::profile::model::PermissionMode;

    #[test]
    fn redacts_common_secret_forms() {
        assert_eq!(
            redact_secrets("Authorization: Bearer-secret"),
            "Authorization: [REDACTED]"
        );
        assert_eq!(redact_secrets("api_key=abc"), "api_key=[REDACTED]");
    }

    #[test]
    fn profile_environment_is_generation_scoped() {
        let now = chrono::Utc::now();
        let profile = ProfileRecord {
            id: uuid::Uuid::nil(),
            name: "测试".to_string(),
            data_root: PathBuf::from("C:/数据/profile"),
            permission_mode: PermissionMode::WorkspaceWrite,
            agent_permission_default: Default::default(),
            revision: 7,
            created_at: now,
            updated_at: now,
        };
        let environment = profile_environment(&profile, "g-9");
        assert!(environment.contains(&("DSH_HOME".into(), profile.data_root.into_os_string())));
        assert!(environment.contains(&("DSH_DESKTOP_PROFILE_REVISION".into(), "7".into())));
        assert!(environment.contains(&("DSH_DESKTOP_GENERATION_ID".into(), "g-9".into())));
    }

    #[test]
    fn managed_runtime_adds_no_open_once() {
        assert_eq!(
            managed_args(vec![
                "app/launcher.mjs".into(),
                "--port".into(),
                "9000".into(),
            ]),
            vec!["app/launcher.mjs", "--port", "9000", "--no-open"],
        );
        assert_eq!(
            managed_args(vec!["app/launcher.mjs".into(), "--no-open".into()]),
            vec!["app/launcher.mjs", "--no-open"],
        );
    }
}
