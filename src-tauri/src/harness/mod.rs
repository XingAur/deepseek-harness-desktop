mod model;
mod process;

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use rusqlite::OptionalExtension;
use serde_json::json;
use tauri::{AppHandle, Emitter, Manager};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, Command},
    sync::Mutex,
};
use zeroize::Zeroizing;

use crate::{
    DesktopFoundation,
    credentials::model::CredentialId,
};

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
        let deepseek_api_key = if task_uses_deepseek(&task) {
            resolve_provider_credential(foundation, "deepseek")?
        } else {
            None
        };
        let openai_compatible_key = if task_uses_openai_compatible(&task) {
            resolve_provider_credential(foundation, "openai-compatible")?
        } else {
            None
        };
        let profile_environment = resolve_profile_environment(foundation, &task)?;
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
            "DSH_OPENAI_BASE_URL",
        ] {
            if let Some(value) = std::env::var_os(name) {
                command.env(name, value);
            }
        }
        if let Some(api_key) = deepseek_api_key.as_ref() {
            command.env("DSH_DEEPSEEK_API_KEY", api_key.as_str());
        }
        if let Some(api_key) = openai_compatible_key.as_ref() {
            command.env("DSH_OPENAI_API_KEY", api_key.as_str());
        }
        // 选中 profile 的凭证只注入宿主进程环境（与 API key 同一受信通道），
        // 不进入 JSONL 协议；Core 侧只读探测据此真实连接。
        for (name, value) in profile_environment {
            command.env(name, value);
        }
        if let Some(model_id) = task.selected_model_id.as_deref() {
            command.env("OPENAI_MODEL", model_id);
            command.env("DSH_SELECTED_MODEL_ID", model_id);
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
            intake: None,
            blockers: None,
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
                    // 归档任务的结果快照带 package_dir；普通执行任务的快照不回传给面板。
                    let intake = value
                        .payload
                        .get("snapshot")
                        .filter(|snapshot| {
                            snapshot
                                .get("package_dir")
                                .is_some_and(serde_json::Value::is_string)
                        })
                        .cloned();
                    // 理解门禁阻断时的具体业务问题/缺口，界面据此展示并收集答复。
                    let blockers = value
                        .payload
                        .get("understanding_blockers")
                        .and_then(|item| item.as_array())
                        .map(|items| {
                            items
                                .iter()
                                .filter_map(|item| item.as_str())
                                .map(str::to_owned)
                                .take(20)
                                .collect::<Vec<_>>()
                        })
                        .filter(|items: &Vec<String>| !items.is_empty());
                    let mut status = service.status.lock().await;
                    status.state = match state {
                        "completed" => "completed",
                        "blocked" => "blocked",
                        _ => "failed",
                    }
                    .to_owned();
                    status.error_code = error_code;
                    status.intake = intake;
                    status.blockers = blockers;
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

/// 把任务选中的连接 profile 解析成宿主进程环境变量。
///
/// 云效 PAT / GitLab token / 数据库 DSN 与 DeepSeek key 走同一受信通道：
/// 只存在于宿主进程环境，绝不进入 JSONL 协议帧。没有配置凭证的 profile
/// 不注入（Core 会回退到自己的本地凭证配置），凭证损坏则直接失败，
/// 不做未授权的静默连接。
fn resolve_profile_environment(
    foundation: &DesktopFoundation,
    task: &HarnessTaskStart,
) -> Result<Vec<(String, String)>, HarnessError> {
    let mut wanted: Vec<(&str, &str)> = Vec::new(); // (profile_id, provider hint)
    if let Some(id) = task.yunxiao_profile_id.as_deref() {
        wanted.push((id, "yunxiao"));
    }
    if let Some(id) = task.gitlab_profile_id.as_deref() {
        wanted.push((id, "gitlab"));
    }
    if let Some(id) = task.database_profile_id.as_deref() {
        wanted.push((id, "database"));
    }
    if wanted.is_empty() {
        return Ok(Vec::new());
    }
    let Some(store) = foundation.agent_store.as_ref() else {
        return Err(HarnessError::Process("连接 Profile 存储不可用".to_owned()));
    };
    let unavailable = || HarnessError::Process("连接 Profile 读取失败".to_owned());
    let reader = store.reader().map_err(|_| unavailable())?;
    crate::commands::ensure_harness_connection_table(&reader).map_err(|_| unavailable())?;
    let mut environment = Vec::new();
    for (profile_id, provider) in wanted {
        let row: Option<(String, String, Option<String>)> = reader
            .query_row(
                "SELECT kind, endpoint, credential_id FROM harness_connection_profiles WHERE profile_id = ?1",
                [profile_id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .optional()
            .map_err(|_| unavailable())?;
        let Some((kind, endpoint, credential_id)) = row else {
            continue; // profile 已被删除：不注入，Core 按无凭证处理
        };
        let Some(credential_id) = credential_id else {
            continue; // 未绑定凭证：Core 回退自身配置
        };
        let credential_id = CredentialId::from_string(credential_id)
            .map_err(|_| HarnessError::Process("连接凭证不可用".to_owned()))?;
        let secret = foundation
            .credential_vault
            .resolve(&credential_id)
            .map_err(|_| HarnessError::Process("连接凭证不可用".to_owned()))?;
        let value = String::from_utf8(secret.expose_bytes_for_backend().to_vec())
            .map_err(|_| HarnessError::Process("连接凭证不可用".to_owned()))?;
        if value.trim().is_empty() {
            return Err(HarnessError::Process("连接凭证不可用".to_owned()));
        }
        match (kind.as_str(), provider) {
            ("mcp", "yunxiao") => environment.push(("ALIYUN_DEVOPS_PAT".to_owned(), value)),
            ("mcp", "gitlab") => environment.push(("DSH_GITLAB_TOKEN".to_owned(), value)),
            ("database", _) => {
                let dsn = compose_readonly_dsn(&endpoint, &value)?;
                environment.push(("DSH_DATABASE_DSN".to_owned(), dsn));
            }
                _ => continue,
        }
    }
    Ok(environment)
}

/// 把 endpoint 与密码组合成只读探测 DSN；无法安全组合时返回错误而不是拼出坏连接串。
fn compose_readonly_dsn(endpoint: &str, password: &str) -> Result<String, HarnessError> {
    let mut url = url::Url::parse(endpoint)
        .map_err(|_| HarnessError::Process("数据库连接地址无效".to_owned()))?;
    if url.host_str().is_none() {
        return Err(HarnessError::Process("数据库连接地址无效".to_owned()));
    }
    url.set_password(Some(password))
        .map_err(|_| HarnessError::Process("数据库连接地址无效".to_owned()))?;
    Ok(url.to_string())
}

fn task_uses_deepseek(task: &HarnessTaskStart) -> bool {
    if task
        .selected_model_id
        .as_deref()
        .is_some_and(|model| model.to_ascii_lowercase().starts_with("deepseek"))
    {
        return true;
    }
    match task.agent_backend.as_deref() {
        Some("deepseek") => true,
        Some(value) if value != "host-bridge" => false,
        _ => std::env::var("DSH_HARNESS_EXECUTOR").ok().as_deref() == Some("deepseek"),
    }
}

/// 显式选择 openai-compatible 后端时走通用执行器（任意 OpenAI 兼容端点/模型）。
fn task_uses_openai_compatible(task: &HarnessTaskStart) -> bool {
    task.agent_backend.as_deref() == Some("openai-compatible")
        || std::env::var("DSH_HARNESS_EXECUTOR").ok().as_deref() == Some("openai-compatible")
}

fn resolve_deepseek_api_key(
    foundation: &DesktopFoundation,
) -> Result<Option<Zeroizing<String>>, HarnessError> {
    resolve_provider_credential(foundation, "deepseek")
}

/// 从模型中心的安全凭证库解析指定 provider 的 API key（不落盘、不进协议）。
fn resolve_provider_credential(
    foundation: &DesktopFoundation,
    provider_id: &str,
) -> Result<Option<Zeroizing<String>>, HarnessError> {
    let unavailable = || HarnessError::Process(format!("{provider_id} 凭证状态不可用"));
    let Some(store) = foundation.agent_store.as_ref() else {
        return Ok(None);
    };
    let connection = store.reader().map_err(|_| unavailable())?;
    let credential_id: Option<String> = connection
        .query_row(
            "SELECT credential_id FROM providers WHERE provider_id = ?1",
            [provider_id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|_| unavailable())?;
    let Some(credential_id) = credential_id else {
        return Ok(None);
    };
    let credential_id = CredentialId::from_string(credential_id)
        .map_err(|_| HarnessError::Process(format!("{provider_id} 凭证不可用")))?;
    let secret = foundation
        .credential_vault
        .resolve(&credential_id)
        .map_err(|_| HarnessError::Process(format!("{provider_id} 凭证不可用")))?;
    let value = String::from_utf8(secret.expose_bytes_for_backend().to_vec())
        .map_err(|_| HarnessError::Process(format!("{provider_id} 凭证不可用")))?;
    if value.trim().is_empty() {
        return Err(HarnessError::Process(format!("{provider_id} 凭证不可用")));
    }
    Ok(Some(Zeroizing::new(value)))
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
    fn composes_readonly_dsn_with_password_only() {
        let dsn = super::compose_readonly_dsn(
            "postgresql://db.internal:5432/his",
            "s3cret-pass",
        )
        .unwrap();
        assert!(dsn.starts_with("postgresql://:s3cret-pass@db.internal:5432/his"));
        assert!(super::compose_readonly_dsn("not a url", "x").is_err());
        assert!(super::compose_readonly_dsn("postgresql:///no-host", "x").is_err());
    }

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
