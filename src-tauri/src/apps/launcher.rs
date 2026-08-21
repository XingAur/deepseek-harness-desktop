//! 本地应用启动器：负责把带 `dsh-app.json` 的已注册工作区变成一个
//! 只监听回环地址的本地应用（web 进程或内置静态服务），并维护运行注册表、
//! 健康门禁与生命周期事件。

use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    process::Stdio,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use chrono::{DateTime, Utc};
use serde::Serialize;
use tokio::process::{Child, Command};
use tokio::sync::Mutex as AsyncMutex;

use crate::apps::manifest::{AppKind, AppManifest, read_manifest};
use crate::apps::static_server::serve_static;
use crate::profile::model::ProfileRecord;
use crate::projects::recycle::{registered_workspace_records, resolve_registered_workspace};
use crate::runtime::{
    RuntimeFailure,
    paths::RuntimePaths,
    process::{pipe_log, reserve_loopback_port, terminate_tree},
};

#[cfg(windows)]
use crate::runtime::process::CREATE_NO_WINDOW;

/// 本地应用生命周期事件通过该常量广播给壳层。
pub const LOCAL_APP_EVENT: &str = "local-app-event";
const MAX_CONCURRENT_APPS: usize = 5;
const HEALTH_TIMEOUT: Duration = Duration::from_secs(60);

/// 事件回调；由上层（Tauri 事件桥）注入。
pub type EventSink = Box<dyn Fn(&LocalAppEvent) + Send + Sync>;

enum Supervision {
    Process {
        pid: u32,
        #[allow(dead_code)]
        child: Arc<AsyncMutex<Child>>,
    },
    Static {
        task: tokio::task::JoinHandle<()>,
    },
}

enum Watcher {
    Process(Arc<AsyncMutex<Child>>),
}

struct RunningApp {
    info: RunningAppInfo,
    supervision: Supervision,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunningAppInfo {
    pub workspace_id: String,
    pub origin: String,
    pub title: String,
    pub started_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppStatusReply {
    pub projects_root: PathBuf,
    pub running: Vec<RunningAppInfo>,
    pub launchable: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LaunchReply {
    pub workspace_id: String,
    pub origin: String,
    pub title: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum LocalAppEventKind {
    Launched,
    Stopped,
    Exited,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalAppEvent {
    pub kind: LocalAppEventKind,
    pub workspace_id: String,
    pub origin: Option<String>,
    pub title: Option<String>,
}

pub struct AppLauncher {
    paths: RuntimePaths,
    running: Mutex<HashMap<String, RunningApp>>,
    sink: EventSink,
}

impl AppLauncher {
    pub fn new(paths: RuntimePaths, sink: EventSink) -> Self {
        Self {
            paths,
            running: Mutex::new(HashMap::new()),
            sink,
        }
    }

    fn emit(&self, event: LocalAppEvent) {
        (self.sink)(&event);
    }

    pub fn status(&self, profile: &ProfileRecord, documents: &Path) -> AppStatusReply {
        let running = self
            .running
            .lock()
            .unwrap()
            .values()
            .map(|app| app.info.clone())
            .collect();
        let projects_root = documents.join("DeepSeek Harness").join("Projects");
        let launchable = registered_workspace_records(&profile.data_root)
            .unwrap_or_default()
            .into_iter()
            .filter(|(_, path)| matches!(read_manifest(path), Ok(Some(_))))
            .map(|(workspace_id, _)| workspace_id)
            .collect();
        AppStatusReply {
            projects_root,
            running,
            launchable,
        }
    }

    pub async fn launch(
        self: Arc<Self>,
        profile: &ProfileRecord,
        documents: &Path,
        workspace_id: &str,
    ) -> Result<LaunchReply, RuntimeFailure> {
        let _ = documents;
        // 1. 幂等：已在册 → 再次广播 launched（壳层切回应用视图），返回既有信息。
        if let Some(reply) = {
            let running = self.running.lock().unwrap();
            running.get(workspace_id).map(|existing| LaunchReply {
                workspace_id: existing.info.workspace_id.clone(),
                origin: existing.info.origin.clone(),
                title: existing.info.title.clone(),
            })
        } {
            self.emit(LocalAppEvent {
                kind: LocalAppEventKind::Launched,
                workspace_id: reply.workspace_id.clone(),
                origin: Some(reply.origin.clone()),
                title: Some(reply.title.clone()),
            });
            return Ok(reply);
        }
        if self.running.lock().unwrap().len() >= MAX_CONCURRENT_APPS {
            return Err(RuntimeFailure::internal(
                "同时运行的本地应用过多，请先停止部分应用",
            ));
        }
        let project_dir = resolve_registered_workspace(&profile.data_root, workspace_id)?;
        let Some(manifest) = read_manifest(&project_dir)? else {
            return Err(RuntimeFailure::internal(
                "该项目没有有效的 dsh-app.json，无法作为本地应用启动",
            ));
        };
        let title = project_dir
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| workspace_id.to_owned());
        let port = reserve_loopback_port()?;
        let origin = format!("http://127.0.0.1:{port}");
        let log_path = self.paths.logs.join("apps").join(format!("{workspace_id}.log"));
        if let Some(parent) = log_path.parent() {
            std::fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?;
        }

        let (supervision, watcher) = match manifest.kind {
            AppKind::Web => {
                let child = spawn_web(&self.paths, &project_dir, &manifest, port, &log_path).await?;
                let pid = child.id().unwrap_or_default();
                let shared = Arc::new(AsyncMutex::new(child));
                (
                    Supervision::Process {
                        pid,
                        child: Arc::clone(&shared),
                    },
                    Some(Watcher::Process(shared)),
                )
            }
            AppKind::Static => {
                let dir = project_dir.join(manifest.static_dir.clone().unwrap_or_default());
                let task = tokio::spawn(async move {
                    let _ = serve_static(dir, port).await;
                });
                (Supervision::Static { task }, None)
            }
        };

        // 2. 健康门禁：未就绪的应用绝不进入注册表，失败即回收新实例。
        if let Err(cause) = wait_healthy(&origin, &manifest.health_path).await {
            let log = log_path.display().to_string();
            shutdown_supervision(&supervision).await;
            return Err(RuntimeFailure::internal(format!(
                "本地应用启动超时或无响应：{cause}；日志：{log}"
            )));
        }

        let info = RunningAppInfo {
            workspace_id: workspace_id.to_owned(),
            origin: origin.clone(),
            title: title.clone(),
            started_at: Utc::now(),
        };
        {
            let mut running = self.running.lock().unwrap();
            if let Some(existing) = running.get(workspace_id) {
                // 并发二次点击：保留先到者，回收新实例。
                let reply = LaunchReply {
                    workspace_id: existing.info.workspace_id.clone(),
                    origin: existing.info.origin.clone(),
                    title: existing.info.title.clone(),
                };
                drop(running);
                shutdown_supervision(&supervision).await;
                return Ok(reply);
            }
            running.insert(
                workspace_id.to_owned(),
                RunningApp {
                    info: info.clone(),
                    supervision,
                },
            );
        }

        // 3. 进程退出看护：外部退出时从注册表移除并广播 exited。
        if let Some(watcher) = watcher {
            let launcher = Arc::clone(&self);
            let watched = workspace_id.to_owned();
            tokio::spawn(async move {
                loop {
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    let exited = match &watcher {
                        Watcher::Process(child) => {
                            matches!(child.lock().await.try_wait(), Ok(Some(_)))
                        }
                    };
                    if !exited {
                        continue;
                    }
                    let removed = launcher.running.lock().unwrap().remove(&watched);
                    if let Some(app) = removed {
                        launcher.emit(LocalAppEvent {
                            kind: LocalAppEventKind::Exited,
                            workspace_id: watched.clone(),
                            origin: Some(app.info.origin.clone()),
                            title: Some(app.info.title.clone()),
                        });
                    }
                    break;
                }
            });
        }

        self.emit(LocalAppEvent {
            kind: LocalAppEventKind::Launched,
            workspace_id: workspace_id.to_owned(),
            origin: Some(origin.clone()),
            title: Some(title.clone()),
        });
        Ok(LaunchReply {
            workspace_id: workspace_id.to_owned(),
            origin,
            title,
        })
    }

    pub async fn stop(&self, workspace_id: &str) -> Result<(), RuntimeFailure> {
        let Some(entry) = self.running.lock().unwrap().remove(workspace_id) else {
            return Ok(());
        };
        let mut failure: Option<String> = None;
        match &entry.supervision {
            Supervision::Process { pid, .. } => {
                for _ in 0..2 {
                    terminate_tree(*pid).await;
                    tokio::time::sleep(Duration::from_millis(300)).await;
                    if self.is_process_gone(*pid).await {
                        failure = None;
                        break;
                    }
                    failure = Some(format!("进程 {pid} 未能终止"));
                }
            }
            Supervision::Static { task } => {
                task.abort();
            }
        }
        if let Some(message) = failure {
            // 终止失败：记录放回注册表，保持“运行中”可重试；watcher 会在进程真正退出后自行收尾。
            self.running
                .lock()
                .unwrap()
                .insert(workspace_id.to_owned(), entry);
            return Err(RuntimeFailure::internal(message));
        }
        self.emit(LocalAppEvent {
            kind: LocalAppEventKind::Stopped,
            workspace_id: workspace_id.to_owned(),
            origin: Some(entry.info.origin.clone()),
            title: Some(entry.info.title.clone()),
        });
        Ok(())
    }

    pub async fn stop_all(&self) {
        let ids: Vec<String> = self.running.lock().unwrap().keys().cloned().collect();
        for workspace_id in ids {
            let _ = self.stop(&workspace_id).await;
        }
    }

    /// 判定外部进程是否已经消失；无法判定时按“仍存活”处理以便重试。
    async fn is_process_gone(&self, pid: u32) -> bool {
        #[cfg(windows)]
        {
            let output = tokio::process::Command::new("tasklist")
                .args(["/FO", "CSV", "/NH", "/FI", &format!("PID eq {pid}")])
                .creation_flags(CREATE_NO_WINDOW)
                .output()
                .await;
            match output {
                // CSV 形如 "node.exe","1234","Console",...；用引号包裹的精确串避免 1234 命中 12345。
                Ok(result) => !String::from_utf8_lossy(&result.stdout)
                    .contains(&format!("\"{pid}\"")),
                Err(_) => false,
            }
        }
        #[cfg(unix)]
        {
            !Path::new("/proc").join(pid.to_string()).exists()
        }
    }
}

/// 终止一个受管实例（进程树或静态服务任务）。
async fn shutdown_supervision(supervision: &Supervision) {
    match supervision {
        Supervision::Process { pid, .. } => terminate_tree(*pid).await,
        Supervision::Static { task } => task.abort(),
    }
}

async fn spawn_web(
    paths: &RuntimePaths,
    project_dir: &Path,
    manifest: &AppManifest,
    port: u16,
    log_path: &Path,
) -> Result<Child, RuntimeFailure> {
    let runtime_dir = active_runtime_dir(paths)?;
    let node = if cfg!(windows) {
        runtime_dir.join("node.exe")
    } else {
        runtime_dir.join("bin").join("node")
    };
    if !node.is_file() {
        return Err(RuntimeFailure::internal(format!(
            "受管 Runtime 缺少 Node：{}",
            node.display()
        )));
    }
    let (command_alias, rest) = manifest
        .start
        .split_first()
        .ok_or_else(|| RuntimeFailure::internal("dsh-app.json 缺少 start"))?;
    let mut args: Vec<String> = Vec::new();
    if command_alias == "pnpm" {
        args.push(
            runtime_dir
                .join("app")
                .join("node_modules")
                .join("pnpm")
                .join("bin")
                .join("pnpm.cjs")
                .to_string_lossy()
                .into_owned(),
        );
    }
    args.extend(rest.iter().cloned());

    let mut command = Command::new(&node);
    command
        .args(args)
        .current_dir(project_dir)
        .env_clear()
        .env(
            "PATH",
            if cfg!(windows) {
                runtime_dir.clone()
            } else {
                runtime_dir.join("bin")
            },
        )
        .env(&manifest.port_env, port.to_string())
        .env("DSH_APP_PROJECT_DIR", project_dir)
        .env("DSH_APP_DATA_DIR", project_dir.join(&manifest.data_dir))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        for key in ["SYSTEMROOT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA"] {
            if let Ok(value) = std::env::var(key) {
                command.env(key, value);
            }
        }
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        if let Ok(home) = std::env::var("HOME") {
            command.env("HOME", home);
        }
        command.process_group(0);
    }
    let mut child = command.spawn().map_err(|cause| {
        RuntimeFailure::internal(format!("启动本地应用失败：{cause}"))
    })?;
    if let Some(stdout) = child.stdout.take() {
        pipe_log(stdout, log_path.to_path_buf(), "APP-OUT");
    }
    if let Some(stderr) = child.stderr.take() {
        pipe_log(stderr, log_path.to_path_buf(), "APP-ERR");
    }
    Ok(child)
}

fn active_runtime_dir(paths: &RuntimePaths) -> Result<PathBuf, RuntimeFailure> {
    #[derive(serde::Deserialize)]
    struct Current {
        version: semver::Version,
    }
    let bytes = std::fs::read(&paths.current).map_err(|_| {
        RuntimeFailure::internal("受管 Runtime 尚未激活，无法启动本地应用")
    })?;
    let current: Current = serde_json::from_slice(&bytes)
        .map_err(|cause| RuntimeFailure::internal(format!("current.json 无效：{cause}")))?;
    Ok(paths.version_dir(&current.version))
}

async fn wait_healthy(origin: &str, health_path: &str) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_millis(1500))
        .build()
        .map_err(|cause| cause.to_string())?;
    let url = format!("{origin}{health_path}");
    let deadline = Instant::now() + HEALTH_TIMEOUT;
    while Instant::now() < deadline {
        if client.get(&url).send().await.is_ok() {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    Err("健康检查超时".to_owned())
}

#[cfg(test)]
mod tests {
    use super::{AppLauncher, EventSink, LaunchReply, LocalAppEvent};
    use crate::profile::model::{PermissionMode, ProfileRecord};
    use crate::runtime::paths::RuntimePaths;
    use crate::storage::app_paths::AppPaths;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn temp_runtime_paths(dir: &std::path::Path) -> RuntimePaths {
        let app_paths = AppPaths::from_roots(dir.join("app"), dir.join("resources"));
        RuntimePaths::from_app_paths(&app_paths).unwrap()
    }

    fn profile_root_with_workspace(
        dir: &std::path::Path,
        workspace_id: &str,
        project: &std::path::Path,
    ) -> std::path::PathBuf {
        let root = dir.join("profile");
        let storage = root.join("storages").join("workspace.json");
        std::fs::create_dir_all(storage.parent().unwrap()).unwrap();
        std::fs::write(
            &storage,
            serde_json::to_vec(&serde_json::json!({
                "global": { "workspaceIds": [workspace_id] },
                "tables": { "workspaces": { (workspace_id): { "path": project } } }
            }))
            .unwrap(),
        )
        .unwrap();
        root
    }

    fn profile_for(root: &std::path::Path) -> ProfileRecord {
        let now = chrono::Utc::now();
        ProfileRecord {
            id: uuid::Uuid::nil(),
            name: "测试".to_string(),
            data_root: root.to_path_buf(),
            permission_mode: PermissionMode::WorkspaceWrite,
            revision: 1,
            created_at: now,
            updated_at: now,
        }
    }

    #[tokio::test]
    async fn static_manifest_launches_stops_and_reports_status() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path().join("demo-app");
        std::fs::create_dir_all(project.join("dist")).unwrap();
        std::fs::write(project.join("dist").join("index.html"), b"<h1>local app</h1>").unwrap();
        std::fs::write(
            project.join("dsh-app.json"),
            br#"{"schemaVersion":1,"type":"static","staticDir":"dist","healthPath":"/"}"#,
        )
        .unwrap();
        let profile_root = profile_root_with_workspace(dir.path(), "w-1", &project);
        let profile = profile_for(&profile_root);
        let documents = dir.path().join("Documents");
        std::fs::create_dir_all(&documents).unwrap();
        let paths = temp_runtime_paths(dir.path());

        let events = std::sync::Arc::new(AtomicUsize::new(0));
        let counter = std::sync::Arc::clone(&events);
        let sink: EventSink = Box::new(move |_event: &LocalAppEvent| {
            counter.fetch_add(1, Ordering::SeqCst);
        });
        let launcher = std::sync::Arc::new(AppLauncher::new(paths, sink));

        let reply: LaunchReply = launcher
            .clone()
            .launch(&profile, &documents, "w-1")
            .await
            .unwrap();
        assert!(reply.origin.starts_with("http://127.0.0.1:"));
        assert_eq!(reply.title, "demo-app");

        // 静态应用真实可访问。
        let page = reqwest::Client::new()
            .get(format!("{}/", reply.origin))
            .send()
            .await
            .unwrap();
        assert_eq!(page.status(), 200);
        assert!(page.text().await.unwrap().contains("local app"));

        let status = launcher.status(&profile, &documents);
        assert_eq!(status.running.len(), 1);
        assert_eq!(status.running[0].workspace_id, "w-1");
        assert_eq!(status.running[0].title, "demo-app");
        assert!(status.launchable.iter().any(|id| id == "w-1"));

        // 幂等：再次启动返回同一 origin，并再次广播 launched。
        let again = launcher
            .clone()
            .launch(&profile, &documents, "w-1")
            .await
            .unwrap();
        assert_eq!(again.origin, reply.origin);

        launcher.stop("w-1").await.unwrap();
        assert!(launcher.status(&profile, &documents).running.is_empty());
        // launched + launched + stopped 至少 3 个事件。
        assert!(events.load(Ordering::SeqCst) >= 3);
    }

    #[tokio::test]
    async fn launch_rejects_project_without_manifest() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path().join("empty");
        std::fs::create_dir_all(&project).unwrap();
        let profile_root = profile_root_with_workspace(dir.path(), "w-2", &project);
        let profile = profile_for(&profile_root);
        let documents = dir.path().join("Documents");
        let paths = temp_runtime_paths(dir.path());
        let launcher = std::sync::Arc::new(AppLauncher::new(paths, Box::new(|_| {})));

        let result = launcher.clone().launch(&profile, &documents, "w-2").await;

        assert!(result.is_err());
        assert!(launcher.status(&profile, &documents).running.is_empty());
    }
}
