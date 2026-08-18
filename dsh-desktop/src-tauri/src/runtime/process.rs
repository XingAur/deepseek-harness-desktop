use std::{path::{Path, PathBuf}, process::Stdio};

use tokio::{fs::OpenOptions, io::{AsyncBufReadExt, AsyncRead, AsyncWriteExt, BufReader}, process::{Child, Command}, task::JoinHandle};

use super::{manifest::release_public_key, model::{RuntimeFailure, RuntimeFailureCode, RuntimeManifest}, paths::{join_confined, RuntimePaths}};

pub struct ManagedRuntime {
    child: Child,
    log_tasks: Vec<JoinHandle<()>>,
}

impl ManagedRuntime {
    pub fn id(&self) -> Option<u32> { self.child.id() }

    pub async fn terminate(&mut self) -> Result<(), RuntimeFailure> {
        let Some(pid) = self.child.id() else { return Ok(()); };
        terminate_tree(pid).await;
        let _ = tokio::time::timeout(std::time::Duration::from_secs(5), self.child.wait()).await;
        let _ = self.child.kill().await;
        for task in self.log_tasks.drain(..) { task.abort(); }
        Ok(())
    }
}

pub fn reserve_loopback_port() -> Result<u16, RuntimeFailure> {
    let listener = std::net::TcpListener::bind(("127.0.0.1", 0))
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Process, cause.to_string()))?;
    let port = listener.local_addr().map_err(RuntimeFailure::internal)?.port();
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
        return Err(RuntimeFailure::new(RuntimeFailureCode::Process, format!("Runtime 入口不存在：{}", executable.display())));
    }
    let args = manifest.args.iter().map(|value| value
        .replace("{port}", &port.to_string())
        .replace("{sessionToken}", session_token))
        .collect::<Vec<_>>();
    let mut command = Command::new(&executable);
    command
        .args(args)
        .current_dir(&runtime_dir)
        .env("DSH_HOME", paths.root.join("dsh"))
        .env("DSH_DESKTOP_MODE", "advanced")
        .env("DSH_DESKTOP_PLATFORM", if cfg!(target_os = "macos") { "darwin" } else { "win32" })
        .env("DSH_DESKTOP_SESSION_TOKEN", session_token)
        .env("DSH_DESKTOP_DSH_BIN", runtime_dir.join("app").join("node_modules").join("@deepseek-ai").join("dsh").join("lib").join("bin.js"))
        .env("DSH_DESKTOP_CATALOG_PATH", runtime_dir.join("catalog").join("community.json"))
        .env("DSH_DESKTOP_DSH_VERSION", &manifest.dsh_version)
        .env("DSH_DESKTOP_CATALOG_PUBLIC_KEY", release_public_key())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    #[cfg(unix)]
    command.process_group(0);

    let mut child = command.spawn()
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Process, format!("启动 DSH Runtime 失败：{cause}")))?;
    let log_path = paths.logs.join(format!("dsh-{}.log", chrono::Utc::now().format("%Y-%m-%d")));
    let mut log_tasks = Vec::new();
    if let Some(stdout) = child.stdout.take() { log_tasks.push(pipe_log(stdout, log_path.clone(), "OUT")); }
    if let Some(stderr) = child.stderr.take() { log_tasks.push(pipe_log(stderr, log_path, "ERR")); }
    Ok(ManagedRuntime { child, log_tasks })
}

fn pipe_log<R>(reader: R, path: PathBuf, prefix: &'static str) -> JoinHandle<()>
where
    R: AsyncRead + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        let mut lines = BufReader::new(reader).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let safe = redact_line(&line);
            if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path).await {
                let rendered = format!("{} {prefix} {safe}\n", chrono::Utc::now().to_rfc3339());
                let _ = file.write_all(rendered.as_bytes()).await;
            }
        }
    })
}

fn redact_line(line: &str) -> String {
    let token_pattern = regex::Regex::new(r"(?i)(api[_-]?key|authorization|bearer|session[_-]?token)(\s*[:=]\s*|\s+)[^\s,;]+")
        .expect("static token regex");
    token_pattern.replace_all(line, "$1$2[REDACTED]").into_owned()
}

#[cfg(windows)]
async fn terminate_tree(pid: u32) {
    let _ = Command::new("taskkill").args(["/PID", &pid.to_string(), "/T", "/F"]).status().await;
}

#[cfg(unix)]
async fn terminate_tree(pid: u32) {
    unsafe { libc::kill(-(pid as i32), libc::SIGTERM); }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_common_secret_forms() {
        assert_eq!(redact_line("Authorization: Bearer-secret"), "Authorization: [REDACTED]");
        assert_eq!(redact_line("api_key=abc"), "api_key=[REDACTED]");
    }
}
