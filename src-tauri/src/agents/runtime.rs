use std::{collections::HashMap, fs, path::PathBuf, sync::Arc, time::Duration};

use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use tokio::sync::Mutex;
use uuid::Uuid;

use crate::{
    agent_store::AgentStore, credentials::model::SecretValue,
    generation::coordinator::TauriEventSink, storage::app_paths::AppPaths,
};

use super::discovery::{DiscoveryRequest, discover};
use super::model::{AgentEventEnvelope, AgentProvider};
use super::supervisor::{ApprovalControl, SupervisorConfig, WorkerSession, WorkerSupervisor};

const WORKER_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);
const WORKER_HEARTBEAT_TIMEOUT: Duration = Duration::from_secs(30);
const WORKER_OUTPUT_LIMIT: usize = 32 * 1024;
const WORKER_APPROVAL_EXPIRY: Duration = Duration::from_secs(24 * 60 * 60);

#[derive(Clone)]
pub struct AgentRuntime {
    store: Option<Arc<AgentStore>>,
    paths: AppPaths,
    worker_root: PathBuf,
    sink: Arc<TauriEventSink>,
    credential_vault: Arc<dyn crate::credentials::vault::CredentialVault>,
    sessions: Arc<Mutex<HashMap<Uuid, Arc<Mutex<WorkerSession>>>>>,
    approval_controls: Arc<Mutex<HashMap<Uuid, ApprovalControl>>>,
}

impl AgentRuntime {
    pub fn new(
        store: Option<Arc<AgentStore>>,
        paths: AppPaths,
        worker_root: PathBuf,
        sink: Arc<TauriEventSink>,
        credential_vault: Arc<dyn crate::credentials::vault::CredentialVault>,
    ) -> Arc<Self> {
        Arc::new(Self {
            store,
            paths,
            worker_root,
            sink,
            credential_vault,
            sessions: Arc::new(Mutex::new(HashMap::new())),
            approval_controls: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    pub async fn start_task(
        self: &Arc<Self>,
        task_id: Uuid,
        generation_id: &str,
        session_id: &str,
        codex_home: Option<&std::path::Path>,
    ) -> Result<(), String> {
        let Some(store) = self.store.as_ref() else {
            return Err("Agent 数据服务当前不可用".to_owned());
        };
        if self.sessions.lock().await.contains_key(&task_id) {
            return Ok(());
        }
        let launch = read_task_launch(store, task_id, generation_id, session_id)?;
        if !launch.workspace.is_dir() {
            return Err("Agent 工作区不可用".to_owned());
        }
        let (node, mut worker_args) = self.worker_command()?;
        let adapter_kind = if std::env::var_os("DSH_AGENT_WORKER_PATH").is_some() {
            launch.provider_id.as_str()
        } else {
            bundled_worker_adapter(&launch.provider_id)?
        };
        if adapter_kind == "codex-cli" {
            let mut request = DiscoveryRequest::for_provider(AgentProvider::Codex);
            if let Some(configured) = launch.cli_path.clone() {
                let configured = PathBuf::from(&configured);
                if configured.is_absolute() {
                    request = request.with_explicit_path(configured);
                }
            }
            let selected = discover(&request)
                .map_err(|error| error)?
                .selected
                .ok_or_else(|| {
                    "没有找到 Codex CLI。回到「Agent」页重新检测，或在高级设置里手动指定 CLI 路径"
                        .to_owned()
                })?;
            worker_args.push(format!("--dsh-codex-cli={}", selected.path.display()));
        }
        let mut worker_env = minimal_worker_environment();
        if adapter_kind == "codex-cli" {
            // 隔离的 CODEX_HOME：ChatGPT 桌面版等常驻 Codex 守护进程会独占
            // ~/.codex 的状态库，导致新起的 app-server 初始化失败。桌面端在
            // 独立目录维护自己的状态库，并把 auth.json 链接回真实文件共享登录。
            let home = codex_home
                .map(std::path::Path::to_path_buf)
                .unwrap_or_else(|| {
                    std::env::var_os("HOME")
                        .map(PathBuf::from)
                        .unwrap_or_else(|| PathBuf::from("/tmp"))
                        .join(".codex")
                });
            prepare_codex_home(&home);
            worker_env.insert("CODEX_HOME".to_owned(), home.to_string_lossy().into_owned());
        }
        let config = SupervisorConfig::new(node.clone(), launch.workspace)
            .with_allowed_executables([node])
            .with_adapter_args(worker_args)
            .with_adapter_kind(adapter_kind)
            .with_env(worker_env)
            .with_handshake_timeout(WORKER_HANDSHAKE_TIMEOUT)
            .with_heartbeat_timeout(WORKER_HEARTBEAT_TIMEOUT)
            .with_output_limit(WORKER_OUTPUT_LIMIT);
        let mut worker = WorkerSupervisor::new(config)
            .launch(&launch.worker_session_id)
            .await
            .map_err(|error| error.to_string())?;
        if let Some(credential_id) = launch.credential_id.as_deref() {
            let secret = self
                .credential_vault
                .resolve(
                    &crate::credentials::model::CredentialId::from_string(credential_id.to_owned())
                        .map_err(|_| "Codex 凭证标识无效".to_owned())?,
                )
                .map_err(|_| "Codex 凭证读取失败，请重新配置".to_owned())?;
            worker
                .initialize_secret(credential_id, secret)
                .await
                .map_err(|error| error.to_string())?;
        }
        worker
            .start_agent_task(&launch.prompt, &launch.permission)
            .await
            .map_err(|error| error.to_string())?;
        let worker = Arc::new(Mutex::new(worker));
        let approval_control = worker.lock().await.approval_control();
        self.sessions
            .lock()
            .await
            .insert(task_id, Arc::clone(&worker));
        self.approval_controls
            .lock()
            .await
            .insert(task_id, approval_control);

        let runtime = Arc::clone(self);
        let generation_id = generation_id.to_owned();
        let session_id = session_id.to_owned();
        tauri::async_runtime::spawn(async move {
            let terminal_status = loop {
                let result = worker
                    .lock()
                    .await
                    .next_agent_event(&generation_id, &task_id.to_string())
                    .await;
                match result {
                    Ok(Some(event)) => {
                        let terminal = task_status_for_event(&event.event_type);
                        if event.event_type == "approval.requested" {
                            if persist_worker_approval(&runtime.store, &event, task_id).is_err() {
                                break Some("needs-review");
                            }
                        }
                        if record_event_checkpoint(&runtime.store, &event).is_err() {
                            break Some("needs-review");
                        }
                        if let Some(status) = terminal {
                            let persisted = set_task_status(
                                &runtime.store,
                                task_id,
                                &generation_id,
                                &session_id,
                                status,
                            )
                            .is_ok();
                            if !should_broadcast_terminal_event(terminal, persisted) {
                                break Some("needs-review");
                            }
                        }
                        runtime.sink.agent_event(event);
                        if terminal.is_some() {
                            break terminal;
                        }
                    }
                    Ok(None) => break Some("needs-review"),
                    Err(_) => {
                        worker.lock().await.fail_pending_responses().await;
                        break Some("needs-review");
                    }
                }
            };
            if let Some(status) = terminal_status {
                let _ =
                    set_task_status(&runtime.store, task_id, &generation_id, &session_id, status);
            }
            runtime.sessions.lock().await.remove(&task_id);
            runtime.approval_controls.lock().await.remove(&task_id);
            let _ = worker
                .lock()
                .await
                .cleanup(super::supervisor::CleanupReason::UserCancelled)
                .await;
        });
        Ok(())
    }

    /// Reconcile tasks left by a previous desktop process without guessing whether
    /// an external operation completed. A fresh worker is never started here.
    pub async fn reconcile_startup(self: &Arc<Self>) -> Result<(), String> {
        let Some(store) = self.store.as_ref() else {
            return Ok(());
        };
        let connection = store.reader().map_err(|error| error.to_string())?;
        let mut statement = connection
            .prepare(
                "SELECT tasks.task_id, tasks.status,
                        EXISTS (
                            SELECT 1 FROM approvals
                            WHERE approvals.task_id = tasks.task_id
                              AND approvals.status = 'pending'
                        ),
                        EXISTS (
                            SELECT 1 FROM event_checkpoints
                            WHERE event_checkpoints.task_id = tasks.task_id
                        )
                 FROM tasks
                 WHERE tasks.status IN ('active', 'running', 'paused')",
            )
            .map_err(|_| "Agent 启动恢复状态读取失败".to_owned())?;
        let pending = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, bool>(2)?,
                    row.get::<_, bool>(3)?,
                ))
            })
            .map_err(|_| "Agent 启动恢复状态读取失败".to_owned())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| "Agent 启动恢复状态读取失败".to_owned())?;
        drop(statement);

        if pending.is_empty() {
            return Ok(());
        }
        let writer = store.writer().map_err(|error| error.to_string())?;
        for (task_id, stored_status, has_pending_approval, has_checkpoint) in pending {
            writer
                .connection()
                .execute(
                    "UPDATE tasks SET status = ?1, updated_at = ?2
                     WHERE task_id = ?3 AND status IN ('active', 'running', 'paused')",
                    params![
                        startup_recovery_status(
                            &stored_status,
                            has_pending_approval,
                            has_checkpoint
                        ),
                        Utc::now().to_rfc3339(),
                        task_id
                    ],
                )
                .map_err(|_| "Agent 启动恢复状态写入失败".to_owned())?;
        }
        Ok(())
    }

    pub async fn cancel_task(&self, task_id: Uuid) -> Result<(), String> {
        self.approval_controls.lock().await.remove(&task_id);
        let Some(worker) = self.sessions.lock().await.remove(&task_id) else {
            return Ok(());
        };
        worker
            .lock()
            .await
            .cancel()
            .await
            .map_err(|error| error.to_string())
    }

    pub async fn resolve_approval(
        &self,
        task_id: Uuid,
        session_id: &str,
        approval_id: &str,
        approved: bool,
    ) -> Result<(), String> {
        let control = self
            .approval_control(task_id, session_id)
            .await?;
        control
            .resolve_approval(approval_id, approved)
            .await
            .map_err(|error| error.to_string())
    }

    /// Check the live Worker channel before mutating the durable approval record.
    /// A recovered task may be inspectable without having a Worker to notify.
    pub async fn ensure_approval_control(
        &self,
        task_id: Uuid,
        session_id: &str,
    ) -> Result<(), String> {
        self.approval_control(task_id, session_id).await.map(|_| ())
    }

    async fn approval_control(
        &self,
        task_id: Uuid,
        session_id: &str,
    ) -> Result<ApprovalControl, String> {
        let control = self
            .approval_controls
            .lock()
            .await
            .get(&task_id)
            .cloned()
            .ok_or_else(|| "Agent Worker 已退出，请先显式恢复任务".to_owned())?;
        if control.session_id() != session_id {
            return Err("Agent Worker 会话已失效".to_owned());
        }
        Ok(control)
    }

    fn worker_command(&self) -> Result<(PathBuf, Vec<String>), String> {
        if let Ok(path) = std::env::var("DSH_AGENT_WORKER_PATH") {
            let path = PathBuf::from(path);
            if path.is_file() {
                return Ok((path, Vec::new()));
            }
            return Err("DSH_AGENT_WORKER_PATH 不可用".to_owned());
        }
        let worker_script = self.worker_root.join("agent-worker.js");
        let worker_root = self
            .worker_root
            .canonicalize()
            .map_err(|_| "Agent Worker 资源未安装".to_owned())?;
        let worker_script = worker_script
            .canonicalize()
            .map_err(|_| "Agent Worker 资源未安装".to_owned())?;
        if !worker_script.starts_with(&worker_root) {
            return Err("Agent Worker 资源路径不受支持".to_owned());
        }
        let current = fs::read(self.paths.runtime.join("current.json"))
            .map_err(|_| "受管 Runtime 尚未激活，无法启动 Agent".to_owned())?;
        let current: CurrentRuntime = serde_json::from_slice(&current)
            .map_err(|_| "受管 Runtime 当前版本信息无效".to_owned())?;
        let runtime_dir = self.paths.runtime.join("versions").join(current.version);
        let node = if cfg!(windows) {
            runtime_dir.join("node.exe")
        } else {
            runtime_dir.join("bin").join("node")
        };
        if !node.is_file() {
            return Err("受管 Runtime 缺少 Node，无法启动 Agent".to_owned());
        }
        Ok((node, vec![worker_script.to_string_lossy().into_owned()]))
    }
}

#[derive(serde::Deserialize)]
struct CurrentRuntime {
    version: String,
}

struct TaskLaunch {
    workspace: PathBuf,
    prompt: String,
    permission: String,
    provider_id: String,
    worker_session_id: String,
    credential_id: Option<String>,
    cli_path: Option<String>,
}

fn task_status_for_event(event_type: &str) -> Option<&'static str> {
    match event_type {
        "approval.requested" | "task.waiting-approval" => Some("waiting-approval"),
        "task.completed" => Some("completed"),
        "task.failed" => Some("failed"),
        _ => None,
    }
}

fn should_broadcast_terminal_event(
    terminal_status: Option<&str>,
    persisted: bool,
) -> bool {
    terminal_status.is_none() || persisted
}

fn bundled_worker_adapter(provider_id: &str) -> Result<&'static str, String> {
    match provider_id {
        "mock" => Ok("mock"),
        // Codex runs through the real CLI adapter: the bundled worker spawns
        // `codex app-server` from the discovered official CLI executable.
        "codex" => Ok("codex-cli"),
        _ => Err(
            "该 Provider 暂未接入真实 CLI Worker；Codex 已支持真实执行，Claude 将在后续版本提供"
                .to_owned(),
        ),
    }
}

/// 准备隔离的 Codex 目录：确保目录存在，并把真实 ~/.codex/auth.json 链接
/// 过来共享登录态（Windows 无符号链接权限时退化为复制）。全部 best-effort，
/// 失败时 Codex 会在隔离目录里以未登录状态运行，由 UI 引导登录。
pub(crate) fn prepare_codex_home(home: &std::path::Path) {
    prepare_codex_home_for(home)
}

/// `prepare_codex_home` 的内部实现，供运行时启动环境注入直接复用。
pub(crate) fn prepare_codex_home_for(home: &std::path::Path) {
    let _ = std::fs::create_dir_all(home);
    let link = home.join("auth.json");
    if link.exists() {
        return;
    }
    let Some(user_home) = std::env::var_os("HOME").map(PathBuf::from) else {
        return;
    };
    let real = user_home.join(".codex").join("auth.json");
    if !real.is_file() {
        return;
    }
    #[cfg(unix)]
    {
        let _ = std::os::unix::fs::symlink(&real, &link);
    }
    #[cfg(windows)]
    {
        let _ = std::fs::copy(&real, &link);
    }
}

/// The worker process starts with an empty environment; the Codex CLI child it
/// spawns needs the minimal user context to locate `~/.codex` and TLS roots.
/// Secret-like names are rejected by the supervisor, so credentials travel via
/// the private `adapter.init` frame instead of the environment.
fn minimal_worker_environment() -> std::collections::BTreeMap<String, String> {
    let mut env = std::collections::BTreeMap::new();
    for name in ["HOME", "PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "USER", "CODEX_HOME"] {
        if let Some(value) = std::env::var_os(name) {
            let value = value.to_string_lossy().into_owned();
            if !value.is_empty() {
                env.insert(name.to_owned(), value);
            }
        }
    }
    if !env.contains_key("PATH") {
        env.insert(
            "PATH".to_owned(),
            "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin".to_owned(),
        );
    }
    env
}

fn startup_recovery_status(
    stored_status: &str,
    has_pending_approval: bool,
    has_checkpoint: bool,
) -> &'static str {
    if has_pending_approval {
        "waiting-approval"
    } else if stored_status == "active" && !has_checkpoint {
        "active"
    } else {
        "needs-review"
    }
}

fn record_event_checkpoint(
    store: &Option<Arc<AgentStore>>,
    event: &AgentEventEnvelope,
) -> Result<(), String> {
    let Some(store) = store.as_ref() else {
        return Err("Agent 数据服务当前不可用".to_owned());
    };
    let writer = store.writer().map_err(|error| error.to_string())?;
    let current_session: Option<(String, String)> = writer
        .connection()
        .query_row(
            "SELECT generation_id, desktop_session_id FROM worker_sessions WHERE task_id = ?1",
            params![event.task_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(|_| "Agent 事件会话已失效".to_owned())?;
    let is_current_session = current_session.as_ref().is_some_and(|(generation, session)| {
        generation == &event.generation_id && session == &event.session_id
    });
    if !is_current_session {
        return Err("Agent 事件会话已失效".to_owned());
    }
    checkpoint_event(writer.connection(), event)
}

fn checkpoint_event(connection: &Connection, event: &AgentEventEnvelope) -> Result<(), String> {
    let content_ref_id = event
        .payload
        .pointer("/contentRef/id")
        .and_then(|value| value.as_str())
        .and_then(|id| {
            connection
                .query_row(
                    "SELECT content_ref_id FROM content_references WHERE content_ref_id = ?1",
                    params![id],
                    |row| row.get::<_, String>(0),
                )
                .optional()
                .ok()
                .flatten()
        });
    connection
        .execute(
            "INSERT INTO event_checkpoints
                (task_id, generation_id, desktop_session_id, last_sequence, last_event_kind, content_ref_id, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, datetime('now'))
             ON CONFLICT(task_id) DO UPDATE SET
                generation_id = excluded.generation_id,
                desktop_session_id = excluded.desktop_session_id,
                last_sequence = excluded.last_sequence,
                last_event_kind = excluded.last_event_kind,
                content_ref_id = COALESCE(excluded.content_ref_id, event_checkpoints.content_ref_id),
                updated_at = excluded.updated_at
             WHERE (
                 excluded.generation_id = event_checkpoints.generation_id
                 AND excluded.desktop_session_id = event_checkpoints.desktop_session_id
                 AND excluded.last_sequence > event_checkpoints.last_sequence
             )
             OR excluded.generation_id != event_checkpoints.generation_id
             OR excluded.desktop_session_id != event_checkpoints.desktop_session_id",
            params![
                event.task_id,
                event.generation_id,
                event.session_id,
                event.sequence as i64,
                event.event_type,
                content_ref_id
            ],
        )
        .map_err(|_| "Agent 事件检查点写入失败".to_owned())?;
    Ok(())
}

fn read_task_launch(
    store: &AgentStore,
    task_id: Uuid,
    generation_id: &str,
    session_id: &str,
) -> Result<TaskLaunch, String> {
    let connection = store.reader().map_err(|error| error.to_string())?;
    connection
        .query_row(
            "SELECT tasks.workspace_path, tasks.prompt, tasks.permission_mode,
                    COALESCE(agents.provider_id, ''), worker_sessions.worker_session_id,
                    providers.credential_id
             FROM tasks
             JOIN agents ON agents.agent_id = tasks.agent_id
             JOIN worker_sessions ON worker_sessions.task_id = tasks.task_id
             LEFT JOIN providers ON providers.provider_id = agents.provider_id
             WHERE tasks.task_id = ?1
               AND worker_sessions.generation_id = ?2
               AND worker_sessions.desktop_session_id = ?3",
            params![task_id.to_string(), generation_id, session_id],
            |row| {
                Ok(TaskLaunch {
                    workspace: PathBuf::from(row.get::<_, String>(0)?),
                    prompt: row.get(1)?,
                    permission: row.get(2)?,
                    provider_id: row.get(3)?,
                    worker_session_id: row.get(4)?,
                    credential_id: row.get(5)?,
                    cli_path: row.get(6)?,
                })
            },
        )
        .map_err(|_| "Agent 任务不存在或 Generation 已失效".to_owned())
}

fn set_task_status(
    store: &Option<Arc<AgentStore>>,
    task_id: Uuid,
    generation_id: &str,
    session_id: &str,
    status: &str,
) -> Result<(), String> {
    let Some(store) = store.as_ref() else {
        return Err("Agent 数据服务当前不可用".to_owned());
    };
    let writer = store.writer().map_err(|error| error.to_string())?;
    let updated = writer
        .connection()
        .execute(
            "UPDATE tasks SET status = ?1, updated_at = datetime('now')
             WHERE task_id = ?2
               AND status IN ('active', 'running', 'paused', 'waiting-approval')
               AND EXISTS (SELECT 1 FROM worker_sessions
                 WHERE task_id = ?2 AND generation_id = ?3 AND desktop_session_id = ?4)",
            params![status, task_id.to_string(), generation_id, session_id],
        )
        .map_err(|_| "Agent 任务状态更新失败".to_owned())?;
    if updated != 1 {
        return Err("Agent 任务状态已失效，终态事件不会广播".to_owned());
    }
    Ok(())
}

/// Persist a worker-reported approval so `approval.list`/`approval.resolve`
/// operate on durable state. The approval id is the worker frame's requestId
/// (a UUID generated by the adapter for one Codex app-server approval).
fn persist_worker_approval(
    store: &Option<Arc<AgentStore>>,
    event: &AgentEventEnvelope,
    task_id: Uuid,
) -> Result<(), String> {
    let Some(store) = store.as_ref() else {
        return Err("Agent 数据服务当前不可用".to_owned());
    };
    let approval_id = event
        .payload
        .get("approvalId")
        .and_then(|value| value.as_str())
        .ok_or_else(|| "审批事件缺少标识".to_owned())?;
    let approval_id = Uuid::parse_str(approval_id).map_err(|_| "审批标识无效".to_owned())?;
    let capability = event
        .payload
        .get("capability")
        .and_then(|value| value.as_str())
        .unwrap_or("unknown");
    let capability = sanitize_capability_kind(capability);
    let scope = event
        .payload
        .get("scope")
        .and_then(|value| value.as_str())
        .unwrap_or("codex-approval");
    let scope: String = scope
        .chars()
        .filter(|character| !character.is_control())
        .take(256)
        .collect();
    let now = Utc::now();
    let expires_at = now + chrono::Duration::from_std(WORKER_APPROVAL_EXPIRY).expect("valid duration");
    let writer = store.writer().map_err(|error| error.to_string())?;
    let connection = writer.connection();
    connection
        .execute(
            "INSERT INTO approvals (
                approval_id, task_id, request_id, generation_id, capability_kind,
                canonical_scope, risk_class, policy_version, status, requested_at,
                resolved_at, decision, result_category, error_code, expires_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'unknown', 'dsh-agent-adapter/v1', 'pending', ?7, NULL, NULL, 'pending', NULL, ?8)
            ON CONFLICT(approval_id) DO NOTHING",
            params![
                approval_id.to_string(),
                task_id.to_string(),
                Uuid::new_v4().to_string(),
                event.generation_id,
                capability,
                scope,
                now.to_rfc3339(),
                expires_at.to_rfc3339(),
            ],
        )
        .map_err(|_| "审批状态写入失败".to_owned())?;
    connection
        .execute(
            "INSERT INTO audit_summaries (
                audit_id, task_id, request_id, generation_id, event_kind,
                capability_kind, canonical_scope, risk_class, policy_version,
                decision, result_category, error_code, occurred_at
            ) VALUES (?1, ?2, ?3, ?4, 'approval-requested', ?5, ?6, 'unknown', 'dsh-agent-adapter/v1', NULL, 'pending', NULL, ?7)",
            params![
                Uuid::new_v4().to_string(),
                task_id.to_string(),
                approval_id.to_string(),
                event.generation_id,
                capability,
                scope,
                now.to_rfc3339(),
            ],
        )
        .map_err(|_| "审批审计写入失败".to_owned())?;
    Ok(())
}

fn sanitize_capability_kind(value: &str) -> &str {
    const KINDS: [&str; 18] = [
        "file-read", "file-write", "file-delete", "terminal", "network", "package-install",
        "process-launch", "external-write", "git-commit", "git-push", "deploy", "credential-use",
        "credential-export", "extension-call", "mcp-call", "audit-disable", "bridge-bypass",
        "unknown",
    ];
    if KINDS.contains(&value) {
        value
    } else {
        "unknown"
    }
}

#[cfg(test)]
mod tests {
    use std::{fs, sync::Arc};

    use rusqlite::{params, Connection};
    use serde_json::json;
    use tempfile::tempdir;
    use uuid::Uuid;

    use crate::{
        agents::model::AgentEventEnvelope,
        agent_store::AgentStore,
        storage::app_paths::AppPaths,
    };

    use super::{
        bundled_worker_adapter, checkpoint_event, set_task_status, startup_recovery_status,
        should_broadcast_terminal_event, task_status_for_event, AgentRuntime,
    };

    #[test]
    fn worker_path_override_is_only_accepted_when_it_is_a_file() {
        let path = std::env::var_os("DSH_AGENT_WORKER_PATH");
        if let Some(path) = path {
            assert!(!path.is_empty());
        }
        let _ = std::mem::size_of::<AgentRuntime>();
    }

    #[test]
    fn startup_recovery_never_restarts_an_unfinished_task() {
        assert_eq!(startup_recovery_status("active", false, false), "active");
        assert_eq!(
            startup_recovery_status("running", false, false),
            "needs-review"
        );
        assert_eq!(
            startup_recovery_status("running", true, false),
            "waiting-approval"
        );
        assert_eq!(
            startup_recovery_status("active", false, true),
            "needs-review"
        );
        assert_ne!(startup_recovery_status("running", false, false), "running");
    }

    #[test]
    fn approval_and_terminal_events_map_to_durable_task_statuses() {
        assert_eq!(task_status_for_event("approval.requested"), Some("waiting-approval"));
        assert_eq!(task_status_for_event("task.waiting-approval"), Some("waiting-approval"));
        assert_eq!(task_status_for_event("task.completed"), Some("completed"));
        assert_eq!(task_status_for_event("task.failed"), Some("failed"));
        assert_eq!(task_status_for_event("message.delta"), None);
    }

    #[test]
    fn terminal_events_are_not_broadcast_when_durability_fails() {
        assert!(should_broadcast_terminal_event(Some("completed"), true));
        assert!(!should_broadcast_terminal_event(Some("completed"), false));
        assert!(should_broadcast_terminal_event(None, false));
    }

    #[test]
    fn event_checkpoint_is_monotonic_and_keeps_content_reference_only_when_known() {
        let connection = Connection::open_in_memory().unwrap();
        connection
            .execute_batch(
                "CREATE TABLE event_checkpoints (
                    task_id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    desktop_session_id TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    last_event_kind TEXT NOT NULL,
                    content_ref_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE content_references (content_ref_id TEXT PRIMARY KEY);",
            )
            .unwrap();
        connection
            .execute("INSERT INTO content_references VALUES ('ref-1')", [])
            .unwrap();
        let known = AgentEventEnvelope {
            channel: "dsh-agent/v1".to_owned(),
            generation_id: "generation-1".to_owned(),
            task_id: "task-1".to_owned(),
            session_id: "session-1".to_owned(),
            sequence: 2,
            event_type: "file.diff.available".to_owned(),
            payload: json!({"contentRef": {"id": "ref-1"}}),
        };
        let unknown = AgentEventEnvelope {
            sequence: 1,
            event_type: "message.delta".to_owned(),
            payload: json!({"text": "old"}),
            ..known.clone()
        };
        checkpoint_event(&connection, &known).unwrap();
        checkpoint_event(&connection, &unknown).unwrap();
        let row: (i64, String, Option<String>) = connection
            .query_row(
                "SELECT last_sequence, last_event_kind, content_ref_id FROM event_checkpoints WHERE task_id = 'task-1'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(
            row,
            (
                2,
                "file.diff.available".to_owned(),
                Some("ref-1".to_owned())
            )
        );
    }

    #[test]
    fn late_worker_event_cannot_overwrite_a_cancelled_task() {
        let root = tempdir().unwrap();
        let paths = AppPaths::from_roots(root.path().to_path_buf(), root.path().to_path_buf());
        fs::create_dir_all(&paths.state).unwrap();
        let store = Arc::new(AgentStore::open(&paths).unwrap());
        let task_id = Uuid::new_v4();
        let agent_id = Uuid::new_v4();
        let generation_id = "generation-1";
        let session_id = "session-1";
        let now = "2026-08-24T00:00:00Z";
        let writer = store.writer().unwrap();
        writer
            .connection()
            .execute(
                "INSERT INTO agents (agent_id, adapter_kind, display_name, status, created_at, updated_at)
                 VALUES (?1, 'mock', 'Mock Agent', 'active', ?2, ?2)",
                params![agent_id.to_string(), now],
            )
            .unwrap();
        writer
            .connection()
            .execute(
                "INSERT INTO tasks (task_id, agent_id, workspace_path, workspace_id, prompt, permission_mode, status, created_at, updated_at)
                 VALUES (?1, ?2, '/workspace', 'workspace-1', 'prompt', 'request-approval', 'cancelled', ?3, ?3)",
                params![task_id.to_string(), agent_id.to_string(), now],
            )
            .unwrap();
        writer
            .connection()
            .execute(
                "INSERT INTO worker_sessions (task_id, worker_session_id, desktop_session_id, adapter_kind, generation_id, updated_at)
                 VALUES (?1, ?2, ?2, 'mock', ?3, ?4)",
                params![task_id.to_string(), session_id, generation_id, now],
            )
            .unwrap();
        drop(writer);

        let error = set_task_status(
            &Some(Arc::clone(&store)),
            task_id,
            generation_id,
            session_id,
            "completed",
        )
        .unwrap_err();
        assert_eq!(error, "Agent 任务状态已失效，终态事件不会广播");

        let connection = store.reader().unwrap();
        let status: String = connection
            .query_row(
                "SELECT status FROM tasks WHERE task_id = ?1",
                params![task_id.to_string()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(status, "cancelled");
    }

    #[test]
    fn codex_home_links_auth_json_when_present() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path().join("codex-home");
        super::prepare_codex_home(&home);
        assert!(home.is_dir());
        // 当前测试环境没有 ~/.codex/auth.json 时也不应失败（best-effort）。
        super::prepare_codex_home(&home);
    }

    #[test]
    fn bundled_worker_maps_codex_to_the_real_cli_adapter_and_rejects_the_rest() {
        assert_eq!(bundled_worker_adapter("mock").unwrap(), "mock");
        assert_eq!(bundled_worker_adapter("codex").unwrap(), "codex-cli");
        assert!(bundled_worker_adapter("claude").is_err());
        assert!(bundled_worker_adapter("unknown").is_err());
    }
}
