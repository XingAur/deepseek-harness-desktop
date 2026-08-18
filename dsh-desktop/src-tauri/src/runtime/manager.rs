use std::{path::PathBuf, sync::Arc, time::Duration};

use tauri::{AppHandle, Emitter};
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use super::{
    activation::{self, ActivationReceipt, read_active_manifest},
    diagnostics,
    download::download_runtime,
    health::wait_for_health,
    manifest::{parse_and_verify_manifest, release_public_key},
    model::{BootstrapReply, RuntimeEvent, RuntimeFailure, RuntimeFailureCode, RuntimeManifest, RuntimePhase, RuntimeProgressEvent, RuntimeTarget},
    paths::RuntimePaths,
    process::{reserve_loopback_port, spawn_runtime, ManagedRuntime},
};

struct ManagerState {
    operation_id: Option<String>,
    phase: RuntimePhase,
    cancellation: Option<CancellationToken>,
    child: Option<ManagedRuntime>,
}

pub struct RuntimeManager {
    app: AppHandle,
    paths: RuntimePaths,
    client: reqwest::Client,
    state: Mutex<ManagerState>,
}

impl RuntimeManager {
    pub fn new(app: AppHandle) -> Result<Arc<Self>, RuntimeFailure> {
        let paths = RuntimePaths::resolve(&app)?;
        let client = reqwest::Client::builder()
            .https_only(false)
            .connect_timeout(Duration::from_secs(15))
            .user_agent("DSH-Desktop/0.1.0")
            .build()
            .map_err(RuntimeFailure::internal)?;
        Ok(Arc::new(Self {
            app,
            paths,
            client,
            state: Mutex::new(ManagerState {
                operation_id: None,
                phase: RuntimePhase::Checking,
                cancellation: None,
                child: None,
            }),
        }))
    }

    pub async fn bootstrap(self: &Arc<Self>, repair: bool) -> Result<BootstrapReply, RuntimeFailure> {
        let mut state = self.state.lock().await;
        if let Some(operation_id) = &state.operation_id {
            if !repair && state.phase == RuntimePhase::Ready {
                return Ok(BootstrapReply { operation_id: operation_id.clone(), phase: RuntimePhase::Ready });
            }
            if state.cancellation.is_some() {
                return Ok(BootstrapReply { operation_id: operation_id.clone(), phase: state.phase });
            }
        }
        if repair {
            if let Some(child) = state.child.as_mut() { child.terminate().await?; }
            state.child = None;
        }
        let operation_id = Uuid::new_v4().to_string();
        let cancellation = CancellationToken::new();
        state.operation_id = Some(operation_id.clone());
        state.phase = RuntimePhase::Checking;
        state.cancellation = Some(cancellation.clone());
        drop(state);

        let manager = Arc::clone(self);
        let task_operation_id = operation_id.clone();
        tauri::async_runtime::spawn(async move {
            if let Err(error) = manager.run_operation(&task_operation_id, repair, cancellation).await {
                manager.finish_failure(&task_operation_id, error).await;
            }
        });
        Ok(BootstrapReply { operation_id, phase: RuntimePhase::Checking })
    }

    pub async fn cancel(&self) -> Result<(), RuntimeFailure> {
        let state = self.state.lock().await;
        if let Some(cancellation) = &state.cancellation { cancellation.cancel(); }
        Ok(())
    }

    pub async fn export_diagnostics(&self) -> Result<String, RuntimeFailure> {
        let paths = self.paths.clone();
        let output = tauri::async_runtime::spawn_blocking(move || diagnostics::export(&paths))
            .await.map_err(RuntimeFailure::internal)??;
        Ok(output.to_string_lossy().to_string())
    }

    pub async fn shutdown(&self) {
        let mut state = self.state.lock().await;
        if let Some(cancellation) = &state.cancellation { cancellation.cancel(); }
        if let Some(child) = state.child.as_mut() { let _ = child.terminate().await; }
        state.child = None;
    }

    async fn run_operation(
        &self,
        operation_id: &str,
        repair: bool,
        cancellation: CancellationToken,
    ) -> Result<(), RuntimeFailure> {
        self.emit_progress(operation_id, RuntimePhase::Checking, 0, None, "正在检查运行环境").await;
        let target = RuntimeTarget::current()?;
        let mut activated: Option<ActivationReceipt> = None;
        let stored = read_active_manifest(&self.paths)?.map(|manifest| {
            let encoded = serde_json::to_vec(&manifest).map_err(RuntimeFailure::internal)?;
            parse_and_verify_manifest(&encoded, target, release_public_key())
        }).transpose()?;
        let manifest = match (repair, stored) {
            (true, _) | (false, None) => {
                self.fetch_and_install(operation_id, target, &cancellation, &mut activated).await?
            }
            (false, Some(current)) => {
                match self.fetch_manifest(target).await {
                    Ok(candidate) if candidate.version > current.version => {
                        self.install_manifest(operation_id, candidate, &cancellation, &mut activated).await?
                    }
                    Ok(_) | Err(_) => current,
                }
            }
        };

        self.emit_progress(operation_id, RuntimePhase::Starting, 0, None, "正在启动 DeepSeek Harness").await;
        let port = reserve_loopback_port()?;
        let session_token = Uuid::new_v4().simple().to_string();
        let child = spawn_runtime(&self.paths, &manifest, port, &session_token).await?;
        {
            let mut state = self.state.lock().await;
            state.child = Some(child);
        }
        let renderer = match wait_for_health(&self.client, port, &manifest.health_path, Duration::from_secs(45), &cancellation).await {
            Ok(url) => url,
            Err(cause) => {
                let mut state = self.state.lock().await;
                if let Some(child) = state.child.as_mut() { let _ = child.terminate().await; }
                state.child = None;
                drop(state);
                if let Some(receipt) = activated.take() { activation::rollback(&self.paths, receipt)?; }
                return Err(cause);
            }
        };
        if let Some(receipt) = activated.take() { let _ = activation::commit(receipt); }
        crate::window::navigate_to_runtime(&self.app, renderer, port, &session_token)?;
        self.emit_progress(operation_id, RuntimePhase::Ready, 1, Some(1), "DSH 工作台已准备完成").await;
        let mut state = self.state.lock().await;
        state.phase = RuntimePhase::Ready;
        state.cancellation = None;
        Ok(())
    }

    async fn fetch_and_install(
        &self,
        operation_id: &str,
        target: RuntimeTarget,
        cancellation: &CancellationToken,
        activated: &mut Option<ActivationReceipt>,
    ) -> Result<RuntimeManifest, RuntimeFailure> {
        self.emit_progress(operation_id, RuntimePhase::FetchingManifest, 0, None, "正在获取签名运行时清单").await;
        let manifest = self.fetch_manifest(target).await?;
        self.install_manifest(operation_id, manifest, cancellation, activated).await
    }

    async fn fetch_manifest(&self, target: RuntimeTarget) -> Result<RuntimeManifest, RuntimeFailure> {
        let bytes = self.fetch_manifest_bytes(target).await?;
        parse_and_verify_manifest(&bytes, target, release_public_key())
    }

    async fn install_manifest(
        &self,
        operation_id: &str,
        manifest: RuntimeManifest,
        cancellation: &CancellationToken,
        activated: &mut Option<ActivationReceipt>,
    ) -> Result<RuntimeManifest, RuntimeFailure> {
        let target = manifest.target;
        let extension = match manifest.archive { super::model::ArchiveKind::Zip => "zip", super::model::ArchiveKind::TarGz => "tar.gz" };
        let archive = self.paths.downloads.join(format!("{}-{target_name}.{extension}.part", manifest.version, target_name = target.as_str()));
        self.emit_progress(operation_id, RuntimePhase::Downloading, 0, Some(manifest.size), "正在下载 Runtime").await;
        let app = self.app.clone();
        let operation = operation_id.to_string();
        download_runtime(&self.client, &manifest, &archive, cancellation, move |completed, total| {
            let _ = app.emit("runtime-event", RuntimeEvent::Progress { payload: RuntimeProgressEvent {
                operation_id: operation.clone(), phase: RuntimePhase::Downloading, completed, total: Some(total), message: "正在下载 Runtime".into(),
            }});
        }).await?;
        self.emit_progress(operation_id, RuntimePhase::Verifying, manifest.size, Some(manifest.size), "正在验证 Runtime").await;
        self.emit_progress(operation_id, RuntimePhase::Activating, 0, None, "正在激活 Runtime").await;
        let paths = self.paths.clone();
        let archive_copy = archive.clone();
        let manifest_copy = manifest.clone();
        let operation = operation_id.to_string();
        let current = tauri::async_runtime::spawn_blocking(move || activation::stage_and_activate(&paths, &archive_copy, &manifest_copy, &operation))
            .await.map_err(RuntimeFailure::internal)??;
        *activated = Some(current);
        Ok(manifest)
    }

    async fn fetch_manifest_bytes(&self, target: RuntimeTarget) -> Result<Vec<u8>, RuntimeFailure> {
        if let Ok(endpoint) = std::env::var("DSH_DESKTOP_RUNTIME_MANIFEST_URL") {
            let url = endpoint.replace("{target}", target.as_str());
            let parsed = url::Url::parse(&url).map_err(RuntimeFailure::internal)?;
            if parsed.scheme() != "https" {
                return Err(RuntimeFailure::new(RuntimeFailureCode::Network, "生产运行时清单必须使用 HTTPS"));
            }
            return self.client.get(parsed).send().await
                .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string()))?
                .error_for_status().map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string()))?
                .bytes().await.map(|bytes| bytes.to_vec())
                .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string()));
        }
        let path: PathBuf = self.paths.bundled_runtime.join("manifests").join(format!("runtime-{}.json", target.as_str()));
        tokio::fs::read(path).await.map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Network, format!("找不到捆绑运行时清单：{cause}")))
    }

    async fn emit_progress(&self, operation_id: &str, phase: RuntimePhase, completed: u64, total: Option<u64>, message: &str) {
        {
            let mut state = self.state.lock().await;
            state.phase = phase;
        }
        let _ = self.app.emit("runtime-event", RuntimeEvent::Progress { payload: RuntimeProgressEvent {
            operation_id: operation_id.to_string(), phase, completed, total, message: message.to_string(),
        }});
    }

    async fn finish_failure(&self, operation_id: &str, error: RuntimeFailure) {
        let phase = if error.code == RuntimeFailureCode::Cancelled { RuntimePhase::Cancelled } else { RuntimePhase::Failed };
        let _ = self.app.emit("runtime-event", RuntimeEvent::Failure {
            operation_id: operation_id.to_string(), payload: error,
        });
        let mut state = self.state.lock().await;
        state.phase = phase;
        state.cancellation = None;
    }
}
