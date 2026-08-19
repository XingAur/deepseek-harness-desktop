use std::{
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};

use tauri::{AppHandle, Emitter};
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use super::{
    activation::{self, ActivationReceipt, read_active_manifest},
    diagnostics,
    download::download_runtime,
    health::wait_for_health,
    maintenance,
    manifest::{parse_and_verify_manifest, release_public_key},
    model::{
        ArchiveKind, BootstrapReply, RuntimeDiagnosticSnapshot, RuntimeEvent, RuntimeFailure,
        RuntimeFailureCode, RuntimeManifest, RuntimePhase, RuntimeProgressEvent, RuntimeTarget,
    },
    paths::RuntimePaths,
    process::{ManagedRuntime, reserve_loopback_port, runtime_exit_failure, spawn_runtime},
};

const MANIFEST_FETCH_TIMEOUT: Duration = Duration::from_secs(30);
const UPDATE_CHECK_TIMEOUT: Duration = Duration::from_secs(5);
const PROGRESS_EMIT_INTERVAL: Duration = Duration::from_millis(150);

struct ManagerState {
    operation_id: Option<String>,
    phase: RuntimePhase,
    cancellation: Option<CancellationToken>,
    child: Option<Arc<Mutex<ManagedRuntime>>>,
    renderer_url: Option<String>,
    diagnostic: RuntimeDiagnosticSnapshot,
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
            .user_agent("DeepSeek-Harness-Desktop/0.1.0")
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
                renderer_url: None,
                diagnostic: RuntimeDiagnosticSnapshot::default(),
            }),
        }))
    }

    pub async fn bootstrap(
        self: &Arc<Self>,
        repair: bool,
    ) -> Result<BootstrapReply, RuntimeFailure> {
        let mut state = self.state.lock().await;
        if let Some(operation_id) = &state.operation_id {
            if !repair && state.phase == RuntimePhase::Ready {
                return Ok(BootstrapReply {
                    operation_id: operation_id.clone(),
                    phase: RuntimePhase::Ready,
                    renderer_url: state.renderer_url.clone(),
                });
            }
            // 已请求取消但后台任务尚未收尾时，允许新操作直接顶替旧操作。
            let superseded = state
                .cancellation
                .as_ref()
                .is_some_and(|token| token.is_cancelled());
            if state.cancellation.is_some() && !superseded {
                return Ok(BootstrapReply {
                    operation_id: operation_id.clone(),
                    phase: state.phase,
                    renderer_url: None,
                });
            }
        }
        if repair {
            if let Some(child) = state.child.as_ref() {
                child.lock().await.terminate().await?;
            }
            state.child = None;
            state.renderer_url = None;
        }
        let operation_id = Uuid::new_v4().to_string();
        let cancellation = CancellationToken::new();
        state.operation_id = Some(operation_id.clone());
        state.phase = RuntimePhase::Checking;
        state.cancellation = Some(cancellation.clone());
        state.renderer_url = None;
        state.diagnostic = RuntimeDiagnosticSnapshot {
            operation_id: Some(operation_id.clone()),
            ..RuntimeDiagnosticSnapshot::default()
        };
        drop(state);

        let manager = Arc::clone(self);
        let task_operation_id = operation_id.clone();
        tauri::async_runtime::spawn(async move {
            if let Err(error) = manager
                .run_operation(&task_operation_id, repair, cancellation)
                .await
            {
                manager.finish_failure(&task_operation_id, error).await;
            }
        });
        Ok(BootstrapReply {
            operation_id,
            phase: RuntimePhase::Checking,
            renderer_url: None,
        })
    }

    pub async fn cancel(&self) -> Result<(), RuntimeFailure> {
        let state = self.state.lock().await;
        if let Some(cancellation) = &state.cancellation {
            cancellation.cancel();
        }
        Ok(())
    }

    pub async fn export_diagnostics(&self) -> Result<String, RuntimeFailure> {
        let paths = self.paths.clone();
        let snapshot = self.state.lock().await.diagnostic.clone();
        let output =
            tauri::async_runtime::spawn_blocking(move || diagnostics::export(&paths, &snapshot))
                .await
                .map_err(RuntimeFailure::internal)??;
        Ok(output.to_string_lossy().to_string())
    }

    pub async fn shutdown(&self) {
        let mut state = self.state.lock().await;
        if let Some(cancellation) = &state.cancellation {
            cancellation.cancel();
        }
        if let Some(child) = state.child.as_ref() {
            let _ = child.lock().await.terminate().await;
        }
        state.child = None;
    }

    async fn run_operation(
        self: &Arc<Self>,
        operation_id: &str,
        repair: bool,
        cancellation: CancellationToken,
    ) -> Result<(), RuntimeFailure> {
        self.emit_progress(
            operation_id,
            RuntimePhase::Checking,
            0,
            None,
            "正在检查运行环境",
        )
        .await;
        maintenance::sweep(&self.paths).await;
        let target = RuntimeTarget::current()?;
        {
            let mut state = self.state.lock().await;
            if state.operation_id.as_deref() == Some(operation_id) {
                state.diagnostic.target = Some(target);
            }
        }
        let mut activated: Option<ActivationReceipt> = None;
        let stored = read_active_manifest(&self.paths)?
            .map(|manifest| {
                let encoded = serde_json::to_vec(&manifest).map_err(RuntimeFailure::internal)?;
                parse_and_verify_manifest(&encoded, target, release_public_key())
            })
            .transpose()?;
        let manifest = match (repair, stored) {
            (true, _) | (false, None) => {
                self.fetch_and_install(operation_id, target, &cancellation, &mut activated)
                    .await?
            }
            (false, Some(current)) => {
                // 更新检查失败或超时不阻塞启动，继续使用已验证的本地版本。
                let candidate =
                    tokio::time::timeout(UPDATE_CHECK_TIMEOUT, self.fetch_manifest(target)).await;
                match candidate {
                    Ok(Ok(manifest)) if manifest.version > current.version => {
                        self.install_manifest(operation_id, manifest, &cancellation, &mut activated)
                            .await?
                    }
                    _ => current,
                }
            }
        };
        {
            let mut state = self.state.lock().await;
            if state.operation_id.as_deref() == Some(operation_id) {
                state.diagnostic.runtime_version = Some(manifest.version.clone());
                state.diagnostic.target = Some(manifest.target);
            }
        }

        self.emit_progress(
            operation_id,
            RuntimePhase::Starting,
            0,
            None,
            "正在启动 DeepSeek Harness",
        )
        .await;
        let port = reserve_loopback_port()?;
        let session_token = Uuid::new_v4().simple().to_string();
        let child = Arc::new(Mutex::new(
            spawn_runtime(&self.paths, &manifest, port, &session_token).await?,
        ));
        {
            let mut state = self.state.lock().await;
            if state.operation_id.as_deref() != Some(operation_id) {
                drop(state);
                child.lock().await.terminate().await?;
                return Err(RuntimeFailure::new(
                    RuntimeFailureCode::Cancelled,
                    "启动请求已被新的操作取代",
                ));
            }
            state.diagnostic.log_file = child.lock().await.log_file_name().map(str::to_owned);
            state.child = Some(Arc::clone(&child));
        }
        let renderer = match wait_for_health(
            &self.client,
            port,
            &manifest.health_path,
            Duration::from_secs(45),
            &cancellation,
            &child,
        )
        .await
        {
            Ok(url) => url,
            Err(cause) => {
                let exit_code = child
                    .lock()
                    .await
                    .try_exit()
                    .ok()
                    .flatten()
                    .and_then(|status| status.code());
                let _ = child.lock().await.terminate().await;
                let mut state = self.state.lock().await;
                if state.operation_id.as_deref() == Some(operation_id) {
                    state.child = None;
                    state.diagnostic.exit_code = exit_code;
                }
                drop(state);
                if let Some(receipt) = activated.take() {
                    activation::rollback(&self.paths, receipt)?;
                }
                return Err(cause);
            }
        };
        if let Some(receipt) = activated.take() {
            let _ = activation::commit(receipt);
        }
        // 健康检查通过后归档已完成使命，及时释放磁盘空间。
        let _ = tokio::fs::remove_file(archive_path(&self.paths, &manifest)).await;
        let renderer = crate::window::runtime_renderer_url(renderer, port, &session_token)?;
        self.emit_progress(
            operation_id,
            RuntimePhase::Ready,
            1,
            Some(1),
            "DeepSeek Harness 工作台已准备完成",
        )
        .await;
        let renderer_url = renderer.to_string();
        let mut state = self.state.lock().await;
        if state.operation_id.as_deref() != Some(operation_id) {
            return Ok(());
        }
        state.phase = RuntimePhase::Ready;
        state.cancellation = None;
        state.renderer_url = Some(renderer_url.clone());
        drop(state);
        let _ = self.app.emit(
            "runtime-event",
            RuntimeEvent::Ready {
                operation_id: operation_id.to_string(),
                renderer_url,
            },
        );

        let manager = Arc::clone(self);
        let monitored_operation_id = operation_id.to_string();
        tauri::async_runtime::spawn(async move {
            manager
                .monitor_runtime_exit(monitored_operation_id, child)
                .await;
        });
        Ok(())
    }

    async fn monitor_runtime_exit(
        self: Arc<Self>,
        operation_id: String,
        child: Arc<Mutex<ManagedRuntime>>,
    ) {
        loop {
            tokio::time::sleep(Duration::from_millis(500)).await;
            let is_current = {
                let state = self.state.lock().await;
                state.operation_id.as_deref() == Some(operation_id.as_str())
                    && state
                        .child
                        .as_ref()
                        .is_some_and(|current| Arc::ptr_eq(current, &child))
            };
            if !is_current {
                return;
            }

            let failure = {
                let mut runtime = child.lock().await;
                match runtime.try_exit() {
                    Ok(Some(status)) => {
                        let exit_code = status.code();
                        runtime.flush_logs(Duration::from_millis(500)).await;
                        Some((runtime_exit_failure(status), exit_code))
                    }
                    Ok(None) => None,
                    Err(failure) => Some((failure, None)),
                }
            };
            let Some((failure, exit_code)) = failure else {
                continue;
            };

            let should_report = {
                let mut state = self.state.lock().await;
                let current = state.operation_id.as_deref() == Some(operation_id.as_str())
                    && state
                        .child
                        .as_ref()
                        .is_some_and(|current| Arc::ptr_eq(current, &child));
                if current {
                    state.child = None;
                    state.diagnostic.exit_code = exit_code;
                }
                current
            };
            if should_report {
                self.finish_failure(&operation_id, failure).await;
            }
            return;
        }
    }

    async fn fetch_and_install(
        &self,
        operation_id: &str,
        target: RuntimeTarget,
        cancellation: &CancellationToken,
        activated: &mut Option<ActivationReceipt>,
    ) -> Result<RuntimeManifest, RuntimeFailure> {
        self.emit_progress(
            operation_id,
            RuntimePhase::FetchingManifest,
            0,
            None,
            "正在获取签名运行时清单",
        )
        .await;
        let manifest = tokio::time::timeout(MANIFEST_FETCH_TIMEOUT, self.fetch_manifest(target))
            .await
            .map_err(|_| {
                RuntimeFailure::new(RuntimeFailureCode::Network, "获取运行时清单超时")
            })??;
        self.install_manifest(operation_id, manifest, cancellation, activated)
            .await
    }

    async fn fetch_manifest(
        &self,
        target: RuntimeTarget,
    ) -> Result<RuntimeManifest, RuntimeFailure> {
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
        let archive = archive_path(&self.paths, &manifest);
        self.emit_progress(
            operation_id,
            RuntimePhase::Downloading,
            0,
            Some(manifest.size),
            "正在下载 Runtime",
        )
        .await;
        let app = self.app.clone();
        let operation = operation_id.to_string();
        let mut last_emitted = 0_u64;
        let mut last_emitted_at = Instant::now();
        download_runtime(
            &self.client,
            &manifest,
            &archive,
            cancellation,
            move |completed, total| {
                // 每 1% 或 150ms 至多发送一次进度，避免大归档下载时的事件风暴。
                let now = Instant::now();
                let step_reached = completed.saturating_sub(last_emitted) >= total / 100;
                if completed != total
                    && !step_reached
                    && now.duration_since(last_emitted_at) < PROGRESS_EMIT_INTERVAL
                {
                    return;
                }
                last_emitted = completed;
                last_emitted_at = now;
                let _ = app.emit(
                    "runtime-event",
                    RuntimeEvent::Progress {
                        payload: RuntimeProgressEvent {
                            operation_id: operation.clone(),
                            phase: RuntimePhase::Downloading,
                            completed,
                            total: Some(total),
                            message: "正在下载 Runtime".into(),
                        },
                    },
                );
            },
        )
        .await?;
        self.emit_progress(
            operation_id,
            RuntimePhase::Verifying,
            manifest.size,
            Some(manifest.size),
            "正在验证 Runtime",
        )
        .await;
        self.emit_progress(
            operation_id,
            RuntimePhase::Activating,
            0,
            None,
            "正在激活 Runtime",
        )
        .await;
        let paths = self.paths.clone();
        let archive_copy = archive.clone();
        let manifest_copy = manifest.clone();
        let operation = operation_id.to_string();
        let current = tauri::async_runtime::spawn_blocking(move || {
            activation::stage_and_activate(&paths, &archive_copy, &manifest_copy, &operation)
        })
        .await
        .map_err(RuntimeFailure::internal)??;
        *activated = Some(current);
        Ok(manifest)
    }

    async fn fetch_manifest_bytes(&self, target: RuntimeTarget) -> Result<Vec<u8>, RuntimeFailure> {
        if let Ok(endpoint) = std::env::var("DSH_DESKTOP_RUNTIME_MANIFEST_URL") {
            let url = endpoint.replace("{target}", target.as_str());
            let parsed = url::Url::parse(&url).map_err(RuntimeFailure::internal)?;
            if parsed.scheme() != "https" {
                return Err(RuntimeFailure::new(
                    RuntimeFailureCode::Network,
                    "生产运行时清单必须使用 HTTPS",
                ));
            }
            return self
                .client
                .get(parsed)
                .send()
                .await
                .map_err(|cause| {
                    RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string())
                })?
                .error_for_status()
                .map_err(|cause| {
                    RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string())
                })?
                .bytes()
                .await
                .map(|bytes| bytes.to_vec())
                .map_err(|cause| {
                    RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string())
                });
        }
        let path: PathBuf = self
            .paths
            .bundled_runtime
            .join("manifests")
            .join(format!("runtime-{}.json", target.as_str()));
        tokio::fs::read(path).await.map_err(|cause| {
            RuntimeFailure::new(
                RuntimeFailureCode::Network,
                format!("找不到捆绑运行时清单：{cause}"),
            )
        })
    }

    async fn emit_progress(
        &self,
        operation_id: &str,
        phase: RuntimePhase,
        completed: u64,
        total: Option<u64>,
        message: &str,
    ) {
        {
            let mut state = self.state.lock().await;
            if state.operation_id.as_deref() == Some(operation_id) {
                state.phase = phase;
                state.diagnostic.phase = phase;
            }
        }
        let _ = self.app.emit(
            "runtime-event",
            RuntimeEvent::Progress {
                payload: RuntimeProgressEvent {
                    operation_id: operation_id.to_string(),
                    phase,
                    completed,
                    total,
                    message: message.to_string(),
                },
            },
        );
    }

    async fn finish_failure(&self, operation_id: &str, error: RuntimeFailure) {
        let phase = if error.code == RuntimeFailureCode::Cancelled {
            RuntimePhase::Cancelled
        } else {
            RuntimePhase::Failed
        };
        let _ = self.app.emit(
            "runtime-event",
            RuntimeEvent::Failure {
                operation_id: operation_id.to_string(),
                payload: error.clone(),
            },
        );
        let mut state = self.state.lock().await;
        // 旧操作收尾时不覆盖已顶替它的新操作状态。
        if state.operation_id.as_deref() != Some(operation_id) {
            return;
        }
        state.phase = phase;
        state.cancellation = None;
        state.renderer_url = None;
        state.diagnostic.failure_phase = Some(state.diagnostic.phase);
        state.diagnostic.phase = phase;
        state.diagnostic.failure = Some(error);
    }
}

fn archive_path(paths: &RuntimePaths, manifest: &RuntimeManifest) -> PathBuf {
    let extension = match manifest.archive {
        ArchiveKind::Zip => "zip",
        ArchiveKind::TarGz => "tar.gz",
    };
    paths.downloads.join(format!(
        "{}-{}.{}.part",
        manifest.version,
        manifest.target.as_str(),
        extension
    ))
}
