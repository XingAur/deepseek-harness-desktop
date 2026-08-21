use std::{
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex as StdMutex},
    time::Duration,
};

use semver::Version;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;

use crate::{
    generation::breaker::{CrashBreaker, FailureKey},
    profile::model::{ProfileRecord, ProfileSelection},
    runtime::{
        activation::read_active_manifest,
        diagnostics,
        health::{ReadinessExpectation, wait_for_readiness},
        maintenance,
        model::{
            BootstrapReply, RuntimeDiagnosticSnapshot, RuntimeEvent, RuntimeFailure,
            RuntimeFailureCode, RuntimeManifest, RuntimePhase, RuntimeProgressEvent,
            RuntimeSourceKind,
        },
        paths::RuntimePaths,
        preparation::{
            PreparedRuntimeChoice, RuntimePreparationProgress, RuntimePreparationService,
            VerifiedPayload,
        },
        process::{ManagedRuntime, reserve_loopback_port, runtime_exit_failure, spawn_runtime},
        updater::{PreparedRuntime, RuntimeUpdater},
    },
    storage::atomic_json::{read_optional, write_atomic},
};

use super::model::{DesktopEvent, GenerationPhase, GenerationProgress, GenerationSnapshot};

pub trait DesktopEventSink: Send + Sync {
    fn runtime(&self, event: RuntimeEvent);
    fn desktop(&self, event: DesktopEvent);
}

pub struct TauriEventSink {
    app: AppHandle,
}

impl TauriEventSink {
    pub fn new(app: AppHandle) -> Arc<Self> {
        Arc::new(Self { app })
    }
}

impl DesktopEventSink for TauriEventSink {
    fn runtime(&self, event: RuntimeEvent) {
        let _ = self.app.emit("runtime-event", event);
    }

    fn desktop(&self, event: DesktopEvent) {
        let _ = self.app.emit("desktop-event", event);
    }
}

pub trait GenerationProcess: Send + Sync {
    fn terminate<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>>;

    fn poll_exit<'a>(
        &'a self,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<Option<(RuntimeFailure, Option<i32>)>, RuntimeFailure>>
                + Send
                + 'a,
        >,
    >;

    fn log_file<'a>(&'a self) -> Pin<Box<dyn Future<Output = Option<String>> + Send + 'a>>;
}

struct ManagedGenerationProcess {
    runtime: Arc<Mutex<ManagedRuntime>>,
}

impl ManagedGenerationProcess {
    fn new(runtime: Arc<Mutex<ManagedRuntime>>) -> Arc<Self> {
        Arc::new(Self { runtime })
    }
}

impl GenerationProcess for ManagedGenerationProcess {
    fn terminate<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move { self.runtime.lock().await.terminate().await })
    }

    fn poll_exit<'a>(
        &'a self,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<Option<(RuntimeFailure, Option<i32>)>, RuntimeFailure>>
                + Send
                + 'a,
        >,
    > {
        Box::pin(async move {
            let mut runtime = self.runtime.lock().await;
            match runtime.try_exit()? {
                Some(status) => {
                    let exit_code = status.code();
                    runtime.flush_logs(Duration::from_millis(500)).await;
                    Ok(Some((runtime_exit_failure(status), exit_code)))
                }
                None => Ok(None),
            }
        })
    }

    fn log_file<'a>(&'a self) -> Pin<Box<dyn Future<Output = Option<String>> + Send + 'a>> {
        Box::pin(async move { self.runtime.lock().await.log_file_name().map(str::to_owned) })
    }
}

pub struct LaunchedGeneration {
    pub runtime_version: Version,
    pub renderer_url: String,
    pub process: Arc<dyn GenerationProcess>,
    pub activation: Arc<dyn CandidateActivation>,
}

pub trait CandidateActivation: Send + Sync {
    fn activate(&self) -> Result<(), RuntimeFailure>;
    fn commit(&self) -> Result<(), RuntimeFailure>;
    fn rollback(&self) -> Result<(), RuntimeFailure>;
}

pub struct NoopCandidateActivation;

impl CandidateActivation for NoopCandidateActivation {
    fn activate(&self) -> Result<(), RuntimeFailure> {
        Ok(())
    }

    fn commit(&self) -> Result<(), RuntimeFailure> {
        Ok(())
    }

    fn rollback(&self) -> Result<(), RuntimeFailure> {
        Ok(())
    }
}

struct PreparedCandidateActivation {
    updater: Arc<RuntimeUpdater>,
    prepared: StdMutex<Option<PreparedRuntime>>,
}

impl PreparedCandidateActivation {
    fn new(updater: Arc<RuntimeUpdater>, prepared: PreparedRuntime) -> Arc<Self> {
        Arc::new(Self {
            updater,
            prepared: StdMutex::new(Some(prepared)),
        })
    }
}

impl CandidateActivation for PreparedCandidateActivation {
    fn activate(&self) -> Result<(), RuntimeFailure> {
        let prepared = self
            .prepared
            .lock()
            .map_err(|_| RuntimeFailure::internal("Runtime 激活事务锁已损坏"))?;
        if let Some(prepared) = prepared.as_ref() {
            self.updater.activate_prepared(prepared)?;
        }
        Ok(())
    }

    fn commit(&self) -> Result<(), RuntimeFailure> {
        let prepared = self
            .prepared
            .lock()
            .map_err(|_| RuntimeFailure::internal("Runtime 激活事务锁已损坏"))?
            .take();
        if let Some(prepared) = prepared {
            self.updater.finalize(prepared)?;
        }
        Ok(())
    }

    fn rollback(&self) -> Result<(), RuntimeFailure> {
        let prepared = self
            .prepared
            .lock()
            .map_err(|_| RuntimeFailure::internal("Runtime 激活事务锁已损坏"))?
            .take();
        if let Some(prepared) = prepared {
            self.updater.rollback(prepared)?;
        }
        Ok(())
    }
}

pub trait RuntimeLauncher: Send + Sync {
    fn launch<'a>(
        &'a self,
        generation_id: &'a str,
        profile: &'a ProfileRecord,
        repair: bool,
        cancellation: &'a CancellationToken,
    ) -> Pin<Box<dyn Future<Output = Result<LaunchedGeneration, RuntimeFailure>> + Send + 'a>>;

    fn check_update<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>>;
}

pub struct ProcessRuntimeLauncher {
    paths: RuntimePaths,
    client: reqwest::Client,
    preparation: Arc<RuntimePreparationService>,
    online_updater: Arc<RuntimeUpdater>,
    sink: Arc<dyn DesktopEventSink>,
}

struct PreparedLaunch {
    manifest: RuntimeManifest,
    prepared: Option<PreparedRuntime>,
    updater: Arc<RuntimeUpdater>,
    source: RuntimeSourceKind,
    verified_payload: Option<VerifiedPayload>,
    message: String,
    reused_local: bool,
}

fn should_auto_repair(
    repair_requested: bool,
    reused_local: bool,
    code: RuntimeFailureCode,
) -> bool {
    !repair_requested
        && reused_local
        && matches!(
            code,
            RuntimeFailureCode::Process | RuntimeFailureCode::HealthTimeout
        )
}

impl ProcessRuntimeLauncher {
    pub fn new(
        paths: RuntimePaths,
        sink: Arc<dyn DesktopEventSink>,
    ) -> Result<Arc<Self>, RuntimeFailure> {
        let client = reqwest::Client::builder()
            .https_only(false)
            .connect_timeout(Duration::from_secs(15))
            .user_agent("DeepSeek-Harness-Desktop/0.1.0")
            .build()
            .map_err(RuntimeFailure::internal)?;
        let preparation = Arc::new(RuntimePreparationService::new(
            paths.clone(),
            client.clone(),
        )?);
        let online_updater = preparation.online_updater();
        Ok(Arc::new(Self {
            preparation,
            online_updater,
            paths,
            client,
            sink,
        }))
    }

    fn emit_progress(
        &self,
        generation_id: &str,
        phase: RuntimePhase,
        completed: u64,
        total: Option<u64>,
        message: impl Into<String>,
    ) {
        self.sink.runtime(RuntimeEvent::Progress {
            payload: RuntimeProgressEvent {
                operation_id: generation_id.to_string(),
                phase,
                completed,
                total,
                message: message.into(),
            },
        });
    }

    fn emit_generation_progress(
        &self,
        generation_id: &str,
        message: &str,
        installed_version: Option<Version>,
        required_version: Option<Version>,
    ) {
        self.sink.desktop(DesktopEvent::GenerationProgress {
            generation_id: generation_id.to_string(),
            payload: GenerationProgress {
                phase: GenerationPhase::PreparingRuntime,
                completed: 0,
                total: None,
                message: message.to_string(),
                installed_version,
                required_version,
            },
        });
    }

    async fn prepare_runtime(
        &self,
        generation_id: &str,
        repair: bool,
        cancellation: &CancellationToken,
    ) -> Result<PreparedLaunch, RuntimeFailure> {
        let installed_version = read_active_manifest(&self.paths)?.map(|manifest| manifest.version);
        if repair || installed_version.is_none() {
            self.emit_generation_progress(
                generation_id,
                if repair {
                    "正在修复 Runtime"
                } else {
                    "正在准备 Runtime"
                },
                installed_version,
                None,
            );
        }
        let sink = Arc::clone(&self.sink);
        let operation_id = generation_id.to_string();
        let progress: Arc<dyn Fn(RuntimePreparationProgress) + Send + Sync> =
            Arc::new(move |event| {
                sink.runtime(RuntimeEvent::Progress {
                    payload: RuntimeProgressEvent {
                        operation_id: operation_id.clone(),
                        phase: event.phase,
                        completed: event.completed,
                        total: event.total,
                        message: event.message,
                    },
                });
            });
        let choice = self
            .preparation
            .prepare(generation_id, repair, cancellation, progress)
            .await?;
        Ok(self.choice_to_launch(choice))
    }

    fn choice_to_launch(&self, choice: PreparedRuntimeChoice) -> PreparedLaunch {
        let reused_local = choice.source == RuntimeSourceKind::Local;
        let message = if reused_local {
            format!("Runtime v{} 已就绪，正在快速启动…", choice.manifest.version)
        } else {
            "组件已就绪，正在启动 DeepSeek Harness…".to_string()
        };
        PreparedLaunch {
            manifest: choice.manifest,
            prepared: choice.prepared,
            updater: choice.updater,
            source: choice.source,
            verified_payload: choice.verified_payload,
            message,
            reused_local,
        }
    }

    async fn launch_prepared(
        &self,
        generation_id: &str,
        profile: &ProfileRecord,
        mut launch: PreparedLaunch,
        cancellation: &CancellationToken,
    ) -> Result<LaunchedGeneration, RuntimeFailure> {
        self.emit_progress(
            generation_id,
            RuntimePhase::Starting,
            0,
            None,
            launch.message,
        );
        let port = reserve_loopback_port()?;
        let session_token = uuid::Uuid::new_v4().simple().to_string();
        let runtime = match spawn_runtime(
            &self.paths,
            &launch.manifest,
            profile,
            generation_id,
            port,
            &session_token,
        )
        .await
        {
            Ok(runtime) => Arc::new(Mutex::new(runtime)),
            Err(error) => {
                if let Some(prepared) = launch.prepared.take() {
                    launch.updater.rollback(prepared)?;
                }
                return Err(error);
            }
        };
        let stabilization = if launch.prepared.is_some() {
            Duration::from_secs(3)
        } else {
            Duration::from_secs(2)
        };
        let renderer = wait_for_readiness(
            &self.client,
            port,
            &launch.manifest.health_path,
            Duration::from_secs(45),
            cancellation,
            &ReadinessExpectation {
                runtime_version: launch.manifest.version.clone(),
                profile_id: profile.id.to_string(),
                profile_revision: profile.revision,
                stabilization,
            },
            &runtime,
        )
        .await;
        let renderer = match renderer {
            Ok(renderer) => renderer,
            Err(error) => {
                let _ = runtime.lock().await.terminate().await;
                if let Some(prepared) = launch.prepared.take() {
                    launch.updater.rollback(prepared)?;
                }
                return Err(error);
            }
        };
        let renderer = match crate::window::runtime_renderer_url(renderer, port, &session_token) {
            Ok(renderer) => renderer,
            Err(error) => {
                let _ = runtime.lock().await.terminate().await;
                if let Some(prepared) = launch.prepared.take() {
                    launch.updater.rollback(prepared)?;
                }
                return Err(error);
            }
        };
        let activation: Arc<dyn CandidateActivation> = match launch.prepared.take() {
            Some(prepared) => {
                PreparedCandidateActivation::new(Arc::clone(&launch.updater), prepared)
            }
            None => Arc::new(NoopCandidateActivation),
        };
        Ok(LaunchedGeneration {
            runtime_version: launch.manifest.version,
            renderer_url: renderer.to_string(),
            process: ManagedGenerationProcess::new(runtime),
            activation,
        })
    }
}

impl RuntimeLauncher for ProcessRuntimeLauncher {
    fn launch<'a>(
        &'a self,
        generation_id: &'a str,
        profile: &'a ProfileRecord,
        repair: bool,
        cancellation: &'a CancellationToken,
    ) -> Pin<Box<dyn Future<Output = Result<LaunchedGeneration, RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move {
            maintenance::sweep(&self.paths).await;
            self.emit_progress(
                generation_id,
                RuntimePhase::Checking,
                0,
                None,
                "正在检查 DeepSeek Harness…",
            );
            let first = self
                .prepare_runtime(generation_id, repair, cancellation)
                .await?;
            let reused_local = first.reused_local;
            let source = first.source;
            let verified_payload = first.verified_payload.clone();
            match self
                .launch_prepared(generation_id, profile, first, cancellation)
                .await
            {
                Ok(launched) => Ok(launched),
                Err(error)
                    if !repair
                        && source == RuntimeSourceKind::Bundled
                        && verified_payload.is_some() =>
                {
                    let error = error.with_preparation(RuntimeSourceKind::Bundled, Some(100));
                    let sink = Arc::clone(&self.sink);
                    let operation_id = generation_id.to_string();
                    let progress: Arc<dyn Fn(RuntimePreparationProgress) + Send + Sync> =
                        Arc::new(move |event| {
                            sink.runtime(RuntimeEvent::Progress {
                                payload: RuntimeProgressEvent {
                                    operation_id: operation_id.clone(),
                                    phase: event.phase,
                                    completed: event.completed,
                                    total: event.total,
                                    message: event.message,
                                },
                            });
                        });
                    let choice = self
                        .preparation
                        .prepare_online_after_verified_failure(
                            generation_id,
                            verified_payload.expect("guarded above"),
                            error,
                            cancellation,
                            progress,
                        )
                        .await?;
                    self.launch_prepared(
                        generation_id,
                        profile,
                        self.choice_to_launch(choice),
                        cancellation,
                    )
                    .await
                    .map_err(|failure| {
                        failure.with_preparation(RuntimeSourceKind::Online, Some(100))
                    })
                }
                Err(error) if should_auto_repair(repair, reused_local, error.code) => {
                    self.emit_progress(
                        generation_id,
                        RuntimePhase::FetchingManifest,
                        0,
                        None,
                        "本地运行组件需要修复，正在重新下载",
                    );
                    let fresh = self
                        .prepare_runtime(generation_id, true, cancellation)
                        .await?;
                    self.launch_prepared(generation_id, profile, fresh, cancellation)
                        .await
                        .map_err(|failure| {
                            failure.with_preparation(RuntimeSourceKind::Online, Some(100))
                        })
                }
                Err(error) if source != RuntimeSourceKind::Local => {
                    Err(error.with_preparation(source, Some(100)))
                }
                Err(error) => Err(error),
            }
        })
    }

    fn check_update<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move {
            let _ = self.online_updater.check_compatible_update().await?;
            Ok(())
        })
    }
}

struct GenerationResource {
    snapshot: GenerationSnapshot,
    data_root: std::path::PathBuf,
    process: Arc<dyn GenerationProcess>,
    activation: Arc<dyn CandidateActivation>,
}

struct CoordinatorState {
    operation_id: Option<String>,
    phase: RuntimePhase,
    cancellation: Option<CancellationToken>,
    active: Option<GenerationResource>,
    candidate: Option<GenerationResource>,
    diagnostic: RuntimeDiagnosticSnapshot,
    operation_kind: String,
    attempt_profile_revision: u64,
    attempt_runtime_version: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct GenerationTimelineEntry {
    generation_id: String,
    phase: GenerationPhase,
    message: String,
    recorded_at: chrono::DateTime<chrono::Utc>,
}

pub struct GenerationCoordinator {
    paths: RuntimePaths,
    launcher: Arc<dyn RuntimeLauncher>,
    sink: Arc<dyn DesktopEventSink>,
    state: Mutex<CoordinatorState>,
    breaker: StdMutex<CrashBreaker>,
    timeline: StdMutex<Vec<GenerationTimelineEntry>>,
    timeline_path: std::path::PathBuf,
}

impl GenerationCoordinator {
    pub fn new(
        paths: RuntimePaths,
        launcher: Arc<dyn RuntimeLauncher>,
        sink: Arc<dyn DesktopEventSink>,
    ) -> Result<Arc<Self>, RuntimeFailure> {
        let breaker = CrashBreaker::open(paths.root.join("breaker.json"))?;
        let timeline_path = paths.root.join("generation-timeline.json");
        let timeline = read_optional(&timeline_path)?.unwrap_or_default();
        Ok(Arc::new(Self {
            paths,
            launcher,
            sink,
            state: Mutex::new(CoordinatorState {
                operation_id: None,
                phase: RuntimePhase::Checking,
                cancellation: None,
                active: None,
                candidate: None,
                diagnostic: RuntimeDiagnosticSnapshot::default(),
                operation_kind: "start".to_string(),
                attempt_profile_revision: 0,
                attempt_runtime_version: "missing".to_string(),
            }),
            breaker: StdMutex::new(breaker),
            timeline: StdMutex::new(timeline),
            timeline_path,
        }))
    }

    pub async fn active_reply(&self, profile: &ProfileRecord) -> Option<BootstrapReply> {
        let state = self.state.lock().await;
        let active = state.active.as_ref()?;
        if active.snapshot.profile.profile_id != profile.id
            || active.snapshot.profile.revision != profile.revision
        {
            return None;
        }
        Some(BootstrapReply {
            operation_id: active.snapshot.generation_id.clone(),
            phase: RuntimePhase::Ready,
            renderer_url: active.snapshot.renderer_url.clone(),
        })
    }

    pub async fn is_active_generation(&self, generation_id: &str) -> bool {
        self.state
            .lock()
            .await
            .active
            .as_ref()
            .is_some_and(|active| active.snapshot.generation_id == generation_id)
    }

    pub async fn current_operation(&self) -> Option<BootstrapReply> {
        let state = self.state.lock().await;
        state
            .operation_id
            .as_ref()
            .map(|operation_id| BootstrapReply {
                operation_id: operation_id.clone(),
                phase: state.phase,
                renderer_url: state
                    .active
                    .as_ref()
                    .and_then(|active| active.snapshot.renderer_url.clone()),
            })
    }

    #[cfg(test)]
    pub async fn begin(&self, generation_id: &str) -> CancellationToken {
        self.begin_operation(generation_id, "start").await
    }

    pub async fn begin_operation(
        &self,
        generation_id: &str,
        operation_kind: &str,
    ) -> CancellationToken {
        let cancellation = CancellationToken::new();
        let mut state = self.state.lock().await;
        state.operation_id = Some(generation_id.to_string());
        state.phase = RuntimePhase::Checking;
        state.cancellation = Some(cancellation.clone());
        state.candidate = None;
        state.diagnostic = RuntimeDiagnosticSnapshot {
            operation_id: Some(generation_id.to_string()),
            ..RuntimeDiagnosticSnapshot::default()
        };
        state.operation_kind = operation_kind.to_string();
        state.attempt_profile_revision = 0;
        state.attempt_runtime_version = "missing".to_string();
        drop(state);
        self.record_timeline(
            generation_id,
            GenerationPhase::ResolvingProfile,
            "开始启动请求",
        );
        cancellation
    }

    pub async fn launch_candidate(
        self: &Arc<Self>,
        generation_id: &str,
        profile: ProfileRecord,
        repair: bool,
        cancellation: &CancellationToken,
    ) -> Result<GenerationSnapshot, RuntimeFailure> {
        let runtime_version = read_active_manifest(&self.paths)?
            .map(|manifest| manifest.version.to_string())
            .unwrap_or_else(|| "missing".to_string());
        let operation_kind = {
            let mut state = self.state.lock().await;
            state.attempt_profile_revision = profile.revision;
            state.attempt_runtime_version = runtime_version.clone();
            state.operation_kind.clone()
        };
        if let Some(open) = self
            .breaker
            .lock()
            .map_err(|_| RuntimeFailure::internal("崩溃断路器锁已损坏"))?
            .open_for(
                &operation_kind,
                "candidate-readiness",
                &runtime_version,
                profile.revision,
                chrono::Utc::now(),
            )
        {
            let mut failure = RuntimeFailure::new(
                RuntimeFailureCode::Process,
                format!(
                    "已停止自动重试：相同的 {} 错误在 10 分钟内重复出现 3 次",
                    open.error_class
                ),
            );
            failure.recoverable = false;
            return Err(failure);
        }
        self.record_timeline(
            generation_id,
            GenerationPhase::PreparingRuntime,
            "准备候选 Runtime",
        );
        let same_root_active = {
            let mut state = self.state.lock().await;
            state.phase = RuntimePhase::Starting;
            if state.operation_id.as_deref() != Some(generation_id) {
                return Err(RuntimeFailure::new(
                    RuntimeFailureCode::Cancelled,
                    "启动请求已被新的操作取代",
                ));
            }
            if state
                .active
                .as_ref()
                .is_some_and(|active| active.data_root == profile.data_root)
            {
                state.active.take()
            } else {
                None
            }
        };
        if let Some(active) = same_root_active {
            active.process.terminate().await?;
        }

        let launched = self
            .launcher
            .launch(generation_id, &profile, repair, cancellation)
            .await?;
        self.record_timeline(
            generation_id,
            GenerationPhase::Probing,
            "候选实例已通过完整就绪检查",
        );
        let snapshot = GenerationSnapshot {
            generation_id: generation_id.to_string(),
            phase: GenerationPhase::Activating,
            profile: ProfileSelection {
                profile_id: profile.id,
                revision: profile.revision,
            },
            runtime_version: launched.runtime_version,
            renderer_url: Some(launched.renderer_url),
        };
        let mut state = self.state.lock().await;
        if state.operation_id.as_deref() != Some(generation_id) || cancellation.is_cancelled() {
            drop(state);
            launched.activation.rollback()?;
            launched.process.terminate().await?;
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Cancelled,
                "启动请求已取消",
            ));
        }
        state.diagnostic.runtime_version = Some(snapshot.runtime_version.clone());
        state.diagnostic.log_file = launched.process.log_file().await;
        state.candidate = Some(GenerationResource {
            snapshot: snapshot.clone(),
            data_root: profile.data_root,
            process: launched.process,
            activation: launched.activation,
        });
        Ok(snapshot)
    }

    pub async fn prepare_activation(&self, generation_id: &str) -> Result<(), RuntimeFailure> {
        let activation = {
            let state = self.state.lock().await;
            let candidate = state
                .candidate
                .as_ref()
                .filter(|candidate| candidate.snapshot.generation_id == generation_id)
                .ok_or_else(|| RuntimeFailure::internal("候选 Generation 不存在"))?;
            Arc::clone(&candidate.activation)
        };
        activation.activate()
    }

    pub async fn activate(
        self: &Arc<Self>,
        generation_id: &str,
    ) -> Result<GenerationSnapshot, RuntimeFailure> {
        let (snapshot, old, activation) = {
            let mut state = self.state.lock().await;
            let mut candidate = state
                .candidate
                .take()
                .ok_or_else(|| RuntimeFailure::internal("候选 Generation 不存在"))?;
            if candidate.snapshot.generation_id != generation_id {
                state.candidate = Some(candidate);
                return Err(RuntimeFailure::internal("候选 Generation 与激活请求不一致"));
            }
            candidate.snapshot.phase = GenerationPhase::Active;
            let snapshot = candidate.snapshot.clone();
            let activation = Arc::clone(&candidate.activation);
            let old = state.active.replace(candidate);
            state.phase = RuntimePhase::Ready;
            state.cancellation = None;
            (snapshot, old, activation)
        };
        activation.activate()?;
        activation.commit()?;
        self.breaker
            .lock()
            .map_err(|_| RuntimeFailure::internal("崩溃断路器锁已损坏"))?
            .clear_after_success()?;
        self.record_timeline(generation_id, GenerationPhase::Active, "候选实例已激活");
        self.sink.desktop(DesktopEvent::GenerationActive {
            generation_id: generation_id.to_string(),
            snapshot: snapshot.clone(),
        });
        if let Some(old) = old {
            let _ = old.process.terminate().await;
        }
        self.spawn_exit_monitor(generation_id.to_string()).await;
        let launcher = Arc::clone(&self.launcher);
        tauri::async_runtime::spawn(async move {
            let _ = launcher.check_update().await;
        });
        Ok(snapshot)
    }

    pub async fn discard_candidate(&self, generation_id: &str) {
        let candidate = {
            let mut state = self.state.lock().await;
            if state
                .candidate
                .as_ref()
                .is_some_and(|candidate| candidate.snapshot.generation_id == generation_id)
            {
                state.candidate.take()
            } else {
                None
            }
        };
        if let Some(candidate) = candidate {
            let _ = candidate.activation.rollback();
            let _ = candidate.process.terminate().await;
        }
    }

    pub async fn finish_failure(&self, generation_id: &str, failure: RuntimeFailure) {
        self.discard_candidate(generation_id).await;
        self.sink.desktop(DesktopEvent::GenerationFailed {
            generation_id: generation_id.to_string(),
            failure: failure.clone(),
        });
        self.sink.runtime(RuntimeEvent::Failure {
            operation_id: generation_id.to_string(),
            payload: failure.clone(),
        });
        let mut state = self.state.lock().await;
        if state.operation_id.as_deref() == Some(generation_id) {
            state.phase = if failure.code == RuntimeFailureCode::Cancelled {
                RuntimePhase::Cancelled
            } else {
                RuntimePhase::Failed
            };
            state.cancellation = None;
            state.diagnostic.failure_phase = Some(state.diagnostic.phase);
            state.diagnostic.phase = state.phase;
            state.diagnostic.failure = Some(failure);
            let operation = state.operation_kind.clone();
            let profile_revision = state.attempt_profile_revision;
            let runtime_version = state.attempt_runtime_version.clone();
            let recoverable = state
                .diagnostic
                .failure
                .as_ref()
                .is_some_and(|failure| failure.recoverable);
            let error_class = state
                .diagnostic
                .failure
                .as_ref()
                .map(|failure| format!("{:?}", failure.code).to_ascii_lowercase());
            drop(state);
            if recoverable {
                if let Some(error_class) = error_class {
                    let _ = self
                        .breaker
                        .lock()
                        .map_err(|_| RuntimeFailure::internal("崩溃断路器锁已损坏"))
                        .and_then(|mut breaker| {
                            breaker.record_failure(
                                FailureKey::new(
                                    operation,
                                    "candidate-readiness",
                                    runtime_version,
                                    profile_revision,
                                    error_class,
                                ),
                                chrono::Utc::now(),
                            )
                        });
                }
            }
            self.record_timeline(generation_id, GenerationPhase::Failed, "启动失败");
        }
    }

    pub async fn cancel(&self) -> Result<(), RuntimeFailure> {
        if let Some(cancellation) = &self.state.lock().await.cancellation {
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

    pub async fn shutdown(&self) -> Result<(), RuntimeFailure> {
        let (active, candidate) = {
            let mut state = self.state.lock().await;
            if let Some(cancellation) = &state.cancellation {
                cancellation.cancel();
            }
            (state.active.take(), state.candidate.take())
        };
        if let Some(candidate) = candidate {
            let _ = candidate.activation.rollback();
            candidate.process.terminate().await?;
        }
        if let Some(active) = active {
            active.process.terminate().await?;
        }
        Ok(())
    }

    #[cfg(test)]
    pub async fn active_id(&self) -> Option<String> {
        self.state
            .lock()
            .await
            .active
            .as_ref()
            .map(|active| active.snapshot.generation_id.clone())
    }

    async fn spawn_exit_monitor(self: &Arc<Self>, generation_id: String) {
        let coordinator = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_millis(500)).await;
                let process = {
                    let state = coordinator.state.lock().await;
                    state.active.as_ref().and_then(|active| {
                        (active.snapshot.generation_id == generation_id)
                            .then(|| Arc::clone(&active.process))
                    })
                };
                let Some(process) = process else {
                    return;
                };
                match process.poll_exit().await {
                    Ok(Some((failure, exit_code))) => {
                        {
                            let mut state = coordinator.state.lock().await;
                            if state.active.as_ref().is_some_and(|active| {
                                active.snapshot.generation_id == generation_id
                            }) {
                                state.active = None;
                                state.diagnostic.exit_code = exit_code;
                            }
                        }
                        coordinator.finish_failure(&generation_id, failure).await;
                        return;
                    }
                    Ok(None) => {}
                    Err(failure) => {
                        coordinator.finish_failure(&generation_id, failure).await;
                        return;
                    }
                }
            }
        });
    }

    fn record_timeline(&self, generation_id: &str, phase: GenerationPhase, message: &str) {
        let Ok(mut timeline) = self.timeline.lock() else {
            return;
        };
        timeline.push(GenerationTimelineEntry {
            generation_id: generation_id.to_string(),
            phase,
            message: message.to_string(),
            recorded_at: chrono::Utc::now(),
        });
        if timeline.len() > 200 {
            let excess = timeline.len() - 200;
            timeline.drain(..excess);
        }
        let _ = write_atomic(&self.timeline_path, &*timeline);
    }
}

#[cfg(test)]
mod tests {
    use std::{
        collections::VecDeque,
        path::PathBuf,
        sync::{Arc, Mutex as StdMutex},
    };

    use chrono::Utc;

    use super::*;
    use crate::{profile::model::PermissionMode, runtime::model::RuntimeFailureCode};

    #[test]
    fn repairs_only_a_reused_local_runtime_process_or_health_failure() {
        assert!(should_auto_repair(false, true, RuntimeFailureCode::Process));
        assert!(should_auto_repair(
            false,
            true,
            RuntimeFailureCode::HealthTimeout
        ));
        assert!(!should_auto_repair(true, true, RuntimeFailureCode::Process));
        assert!(!should_auto_repair(
            false,
            false,
            RuntimeFailureCode::Process
        ));
        assert!(!should_auto_repair(
            false,
            true,
            RuntimeFailureCode::Network
        ));
    }

    #[derive(Default)]
    struct NoopSink;

    impl DesktopEventSink for NoopSink {
        fn runtime(&self, _event: RuntimeEvent) {}
        fn desktop(&self, _event: DesktopEvent) {}
    }

    #[derive(Default)]
    struct FakeProcess {
        terminated: std::sync::atomic::AtomicBool,
    }

    impl GenerationProcess for FakeProcess {
        fn terminate<'a>(
            &'a self,
        ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
            Box::pin(async move {
                self.terminated
                    .store(true, std::sync::atomic::Ordering::SeqCst);
                Ok(())
            })
        }

        fn poll_exit<'a>(
            &'a self,
        ) -> Pin<
            Box<
                dyn Future<Output = Result<Option<(RuntimeFailure, Option<i32>)>, RuntimeFailure>>
                    + Send
                    + 'a,
            >,
        > {
            Box::pin(async { Ok(None) })
        }

        fn log_file<'a>(&'a self) -> Pin<Box<dyn Future<Output = Option<String>> + Send + 'a>> {
            Box::pin(async { None })
        }
    }

    #[derive(Default)]
    struct FakeLauncher {
        outcomes: StdMutex<VecDeque<Result<(), RuntimeFailure>>>,
    }

    impl FakeLauncher {
        fn fail_next(&self, message: &str) {
            self.outcomes
                .lock()
                .unwrap()
                .push_back(Err(RuntimeFailure::new(
                    RuntimeFailureCode::Process,
                    message,
                )));
        }
    }

    impl RuntimeLauncher for FakeLauncher {
        fn launch<'a>(
            &'a self,
            _generation_id: &'a str,
            _profile: &'a ProfileRecord,
            _repair: bool,
            _cancellation: &'a CancellationToken,
        ) -> Pin<Box<dyn Future<Output = Result<LaunchedGeneration, RuntimeFailure>> + Send + 'a>>
        {
            let outcome = self.outcomes.lock().unwrap().pop_front().unwrap_or(Ok(()));
            Box::pin(async move {
                outcome?;
                Ok(LaunchedGeneration {
                    runtime_version: Version::new(1, 8, 2),
                    renderer_url: "http://127.0.0.1:39000/".to_string(),
                    process: Arc::new(FakeProcess::default()),
                    activation: Arc::new(NoopCandidateActivation),
                })
            })
        }

        fn check_update<'a>(
            &'a self,
        ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
            Box::pin(async { Ok(()) })
        }
    }

    fn paths(root: PathBuf) -> RuntimePaths {
        RuntimePaths {
            versions: root.join("runtime/versions"),
            downloads: root.join("runtime/downloads"),
            logs: root.join("logs"),
            diagnostics: root.join("diagnostics"),
            current: root.join("runtime/current.json"),
            bundled_runtime: root.join("bundled"),
            root,
        }
    }

    fn profile(name: &str, data_root: PathBuf) -> ProfileRecord {
        let now = Utc::now();
        ProfileRecord {
            id: uuid::Uuid::new_v4(),
            name: name.to_string(),
            data_root,
            permission_mode: PermissionMode::WorkspaceWrite,
            revision: 1,
            created_at: now,
            updated_at: now,
        }
    }

    #[tokio::test]
    async fn failed_candidate_keeps_the_active_generation() {
        let temporary = tempfile::tempdir().unwrap();
        let launcher = Arc::new(FakeLauncher::default());
        let coordinator = GenerationCoordinator::new(
            paths(temporary.path().to_path_buf()),
            launcher.clone(),
            Arc::new(NoopSink),
        )
        .unwrap();
        let active = profile("A", temporary.path().join("a"));
        let token = coordinator.begin("g-1").await;
        coordinator
            .launch_candidate("g-1", active, false, &token)
            .await
            .unwrap();
        coordinator.activate("g-1").await.unwrap();

        launcher.fail_next("handshake");
        let target = profile("B", temporary.path().join("b"));
        let token = coordinator.begin("g-2").await;
        let failure = coordinator
            .launch_candidate("g-2", target, false, &token)
            .await
            .unwrap_err();
        assert_eq!(failure.code, RuntimeFailureCode::Process);
        assert_eq!(coordinator.active_id().await.as_deref(), Some("g-1"));
    }

    #[tokio::test]
    async fn activating_a_candidate_drains_the_old_generation() {
        let temporary = tempfile::tempdir().unwrap();
        let coordinator = GenerationCoordinator::new(
            paths(temporary.path().to_path_buf()),
            Arc::new(FakeLauncher::default()),
            Arc::new(NoopSink),
        )
        .unwrap();
        for (id, name) in [("g-1", "A"), ("g-2", "B")] {
            let token = coordinator.begin(id).await;
            coordinator
                .launch_candidate(
                    id,
                    profile(name, temporary.path().join(name)),
                    false,
                    &token,
                )
                .await
                .unwrap();
            coordinator.activate(id).await.unwrap();
        }
        assert_eq!(coordinator.active_id().await.as_deref(), Some("g-2"));
    }
}
