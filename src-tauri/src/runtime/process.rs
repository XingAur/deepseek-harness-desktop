use std::{
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
    manifest::release_public_key,
    model::{RuntimeFailure, RuntimeFailureCode, RuntimeManifest},
    paths::{RuntimePaths, join_confined},
    redaction::redact_secrets,
};

pub struct ManagedRuntime {
    child: Child,
    log_tasks: Vec<JoinHandle<()>>,
    log_file: Option<String>,
}

impl ManagedRuntime {
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
    port: u16,
    session_token: &str,
) -> Result<ManagedRuntime, RuntimeFailure> {
    let runtime_dir = paths.version_dir(&manifest.version);
    let executable = join_confined(&runtime_dir, &manifest.entrypoint, "entrypoint")?;
    if !executable.is_file() {
        return Err(RuntimeFailure::new(
            RuntimeFailureCode::Process,
            format!("Runtime 入口不存在：{}", executable.display()),
        ));
    }
    let args = manifest
        .args
        .iter()
        .map(|value| {
            value
                .replace("{port}", &port.to_string())
                .replace("{sessionToken}", session_token)
        })
        .collect::<Vec<_>>();
    let mut command = Command::new(&executable);
    command
        .args(args)
        .current_dir(&runtime_dir)
        .env("DSH_HOME", paths.root.join("dsh"))
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
        .env(
            "DSH_DESKTOP_CATALOG_PATH",
            runtime_dir.join("catalog").join("community.json"),
        )
        .env("DSH_DESKTOP_DSH_VERSION", manifest.dsh_version.to_string())
        .env("DSH_DESKTOP_CATALOG_PUBLIC_KEY", release_public_key())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    command.creation_flags(0x08000000);
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

fn pipe_log<R>(reader: R, path: PathBuf, prefix: &'static str) -> JoinHandle<()>
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
async fn terminate_tree(pid: u32) {
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .status()
        .await;
}

#[cfg(unix)]
async fn terminate_tree(pid: u32) {
    unsafe {
        libc::kill(-(pid as i32), libc::SIGTERM);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_common_secret_forms() {
        assert_eq!(
            redact_secrets("Authorization: Bearer-secret"),
            "Authorization: [REDACTED]"
        );
        assert_eq!(redact_secrets("api_key=abc"), "api_key=[REDACTED]");
    }
}
