mod model;
mod process;

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use serde_json::json;
use tauri::{AppHandle, Emitter, Manager};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, Command},
    sync::Mutex,
};

use crate::DesktopFoundation;

use model::HARNESS_HOST_SCHEMA;
pub use model::{HARNESS_EVENT, HarnessError, HarnessHostMessage, HarnessStatus, HarnessTaskStart};
use process::{validate_development_host_path, validate_sidecar_path};

const MAX_FRAME_BYTES: usize = 256 * 1024;

#[derive(Clone)]
pub struct HarnessService {
    process: Arc<Mutex<Option<Arc<Mutex<Child>>>>>,
    status: Arc<Mutex<HarnessStatus>>,
}

impl HarnessService {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            process: Arc::new(Mutex::new(None)),
            status: Arc::new(Mutex::new(HarnessStatus {
                state: "idle".to_owned(),
                ..HarnessStatus::default()
            })),
        })
    }

    pub async fn status(&self) -> HarnessStatus {
        self.status.lock().await.clone()
    }

    pub async fn start(
        self: &Arc<Self>,
        app: AppHandle,
        foundation: &DesktopFoundation,
        request_id: String,
        task: HarnessTaskStart,
    ) -> Result<HarnessStatus, HarnessError> {
        task.validate()?;
        if !valid_request_id(&request_id) {
            return Err(HarnessError::InvalidRequest);
        }
        if self.process.lock().await.is_some() {
            return Err(HarnessError::AlreadyRunning);
        }

        let resource_root = app
            .path()
            .resource_dir()
            .map_err(|_| HarnessError::SidecarUnavailable)?;
        let development_mode = cfg!(debug_assertions)
            && std::env::var("DSH_HARNESS_DEV_MODE").ok().as_deref() == Some("1");
        let host_path = resolve_host_path(&resource_root, development_mode)?;
        let node_path = resolve_node_path(foundation, development_mode)?;
        let core_root = resolve_core_root(&resource_root, development_mode)?;
        let database_path = foundation.paths.agent_database.clone();
        let payload = json!({
            "schema_version": HARNESS_HOST_SCHEMA,
            "type": "task.start",
            "request_id": request_id,
            "payload": task.payload(),
        });
        let serialized =
            serde_json::to_string(&payload).map_err(|_| HarnessError::InvalidRequest)?;
        if serialized.len() > MAX_FRAME_BYTES {
            return Err(HarnessError::InvalidRequest);
        }

        let mut command = Command::new(node_path);
        command
            .arg(&host_path)
            .current_dir(host_path.parent().unwrap_or(Path::new("/")))
            .env_clear()
            .env("HARNESS_CORE_ROOT", core_root)
            .env("HARNESS_DB_PATH", database_path)
            .env("PATH", safe_path())
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null());
        for name in [
            "HOME",
            "LANG",
            "LC_ALL",
            "TZ",
            "TMPDIR",
            "USER",
            "DSH_DESKTOP_CODEX_CLI",
            "CODEX_HOME",
            "DSH_HARNESS_EXECUTOR",
        ] {
            if let Some(value) = std::env::var_os(name) {
                command.env(name, value);
            }
        }
        if development_mode {
            if let Some(value) = std::env::var_os("HARNESS_PYTHON") {
                command.env("HARNESS_PYTHON", value);
            }
        }
        let mut child = command
            .spawn()
            .map_err(|_| HarnessError::SidecarUnavailable)?;
        let pid = child.id();
        let mut stdin = child.stdin.take().ok_or(HarnessError::SidecarUnavailable)?;
        stdin
            .write_all(format!("{serialized}\n").as_bytes())
            .await
            .map_err(|_| HarnessError::Process("Harness Host 启动失败".to_owned()))?;
        stdin.flush().await.ok();
        drop(stdin);
        let stdout = child
            .stdout
            .take()
            .ok_or(HarnessError::SidecarUnavailable)?;
        let child = Arc::new(Mutex::new(child));
        *self.process.lock().await = Some(Arc::clone(&child));
        *self.status.lock().await = HarnessStatus {
            state: "running".to_owned(),
            pid,
            request_id: Some(
                payload["request_id"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned(),
            ),
            error_code: None,
        };
        let service = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if line.len() > MAX_FRAME_BYTES {
                    set_failed(&service, "harness_frame_invalid").await;
                    break;
                }
                let Ok(value) = serde_json::from_str::<HarnessHostMessage>(&line) else {
                    set_failed(&service, "harness_message_invalid").await;
                    break;
                };
                if value.validate().is_err() {
                    set_failed(&service, "harness_message_invalid").await;
                    break;
                }
                if value.message_type == "task.result" {
                    let state = value
                        .payload
                        .get("status")
                        .and_then(|item| item.as_str())
                        .unwrap_or("failed");
                    let error_code = value
                        .payload
                        .get("error_code")
                        .and_then(|item| item.as_str())
                        .filter(|item| !item.is_empty())
                        .map(str::to_owned);
                    let mut status = service.status.lock().await;
                    status.state = match state {
                        "completed" => "completed",
                        "blocked" => "blocked",
                        _ => "failed",
                    }
                    .to_owned();
                    status.error_code = error_code;
                }
                let _ = app.emit(HARNESS_EVENT, value);
            }
            let _ = child.lock().await.wait().await;
            *service.process.lock().await = None;
            let mut status = service.status.lock().await;
            if status.state == "running" {
                status.state = "failed".to_owned();
                status.error_code = Some("harness_host_exited".to_owned());
            }
        });
        Ok(self.status().await)
    }

    pub async fn cancel(&self) -> Result<HarnessStatus, HarnessError> {
        let process = self
            .process
            .lock()
            .await
            .clone()
            .ok_or(HarnessError::NotRunning)?;
        process
            .lock()
            .await
            .kill()
            .await
            .map_err(|_| HarnessError::Process("Harness Host 停止失败".to_owned()))?;
        let mut status = self.status.lock().await;
        status.state = "cancelled".to_owned();
        status.error_code = Some("cancelled".to_owned());
        Ok(status.clone())
    }
}

async fn set_failed(service: &HarnessService, code: &str) {
    let mut status = service.status.lock().await;
    status.state = "failed".to_owned();
    status.error_code = Some(code.to_owned());
}

fn resolve_host_path(
    resource_root: &Path,
    development_mode: bool,
) -> Result<PathBuf, HarnessError> {
    if let Some(raw) = std::env::var_os("DSH_HARNESS_HOST_PATH") {
        let path = PathBuf::from(raw);
        if development_mode {
            return validate_development_host_path(&path);
        }
        return validate_sidecar_path(&path, resource_root);
    }
    validate_sidecar_path(
        &resource_root.join("harness").join("harness-host.mjs"),
        resource_root,
    )
}

fn resolve_core_root(
    resource_root: &Path,
    development_mode: bool,
) -> Result<PathBuf, HarnessError> {
    let path = if development_mode {
        std::env::var_os("HARNESS_CORE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| resource_root.join("harness").join("core"))
    } else {
        resource_root.join("harness").join("core")
    };
    if !path.is_absolute() || !path.is_dir() || path.is_symlink() {
        return Err(HarnessError::SidecarUnavailable);
    }
    path.canonicalize()
        .map_err(|_| HarnessError::SidecarUnavailable)
}

fn resolve_node_path(
    foundation: &DesktopFoundation,
    development_mode: bool,
) -> Result<PathBuf, HarnessError> {
    if development_mode {
        if let Some(raw) = std::env::var_os("DSH_HARNESS_NODE") {
            return validate_development_host_path(&PathBuf::from(raw));
        }
    }
    let runtime = crate::runtime::paths::RuntimePaths::from_app_paths(&foundation.paths)
        .map_err(|_| HarnessError::SidecarUnavailable)?;
    let current = std::fs::read(runtime.current).map_err(|_| HarnessError::SidecarUnavailable)?;
    let value: serde_json::Value =
        serde_json::from_slice(&current).map_err(|_| HarnessError::SidecarUnavailable)?;
    let version = value
        .get("version")
        .and_then(|item| item.as_str())
        .ok_or(HarnessError::SidecarUnavailable)?;
    let node = if cfg!(windows) {
        runtime.versions.join(version).join("node.exe")
    } else {
        runtime.versions.join(version).join("bin").join("node")
    };
    if !node.is_file() || node.is_symlink() {
        return Err(HarnessError::SidecarUnavailable);
    }
    Ok(node)
}

fn safe_path() -> &'static str {
    if cfg!(windows) {
        "C:\\Windows\\System32;C:\\Windows"
    } else {
        "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    }
}

fn valid_request_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::process::validate_sidecar_path;

    #[test]
    fn rejects_unregistered_sidecar_path() {
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::NamedTempFile::new().unwrap();
        assert_eq!(
            validate_sidecar_path(outside.path(), root.path()),
            Err(super::HarnessError::SidecarPathNotAllowed)
        );
    }

    #[test]
    fn accepts_a_regular_sidecar_inside_the_resource_root() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("harness-host");
        fs::write(&path, b"host").unwrap();
        assert!(validate_sidecar_path(&path, root.path()).is_ok());
    }
}
