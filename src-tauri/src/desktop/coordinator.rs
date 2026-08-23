use std::sync::Arc;

use tokio::sync::Mutex;
use uuid::Uuid;

use crate::{
    generation::{
        coordinator::{DesktopEventSink, GenerationCoordinator},
        model::DesktopEvent,
    },
    profile::{
        model::{ActivationReason, ProfileRecord, ProfileSelection},
        repository::ProfileRepository,
    },
    runtime::model::{BootstrapReply, RuntimeEvent, RuntimeFailure, RuntimePhase},
};

pub struct DesktopCoordinator {
    operations: Mutex<()>,
    generations: Arc<GenerationCoordinator>,
    profiles: Arc<ProfileRepository>,
    sink: Arc<dyn DesktopEventSink>,
}

impl DesktopCoordinator {
    pub fn new(
        generations: Arc<GenerationCoordinator>,
        profiles: Arc<ProfileRepository>,
        sink: Arc<dyn DesktopEventSink>,
    ) -> Arc<Self> {
        Arc::new(Self {
            operations: Mutex::new(()),
            generations,
            profiles,
            sink,
        })
    }

    pub async fn start(self: &Arc<Self>) -> Result<BootstrapReply, RuntimeFailure> {
        let profile = self.selected_profile()?;
        if let Some(reply) = self.generations.active_reply(&profile).await {
            return Ok(reply);
        }
        self.schedule(profile, ActivationReason::Startup, false)
            .await
    }

    pub async fn switch_profile(
        self: &Arc<Self>,
        profile_id: Uuid,
    ) -> Result<BootstrapReply, RuntimeFailure> {
        let profile = self.profiles.get(&profile_id)?;
        if let Some(reply) = self.generations.active_reply(&profile).await {
            return Ok(reply);
        }
        self.schedule(profile, ActivationReason::UserSwitch, false)
            .await
    }

    pub async fn restart(self: &Arc<Self>) -> Result<BootstrapReply, RuntimeFailure> {
        self.schedule(self.selected_profile()?, ActivationReason::Recovery, false)
            .await
    }

    pub async fn repair(self: &Arc<Self>) -> Result<BootstrapReply, RuntimeFailure> {
        self.schedule(self.selected_profile()?, ActivationReason::Recovery, true)
            .await
    }

    async fn schedule(
        self: &Arc<Self>,
        profile: ProfileRecord,
        reason: ActivationReason,
        repair: bool,
    ) -> Result<BootstrapReply, RuntimeFailure> {
        let _operation = self.operations.lock().await;
        if let Some(current) = self.generations.current_operation().await {
            if !matches!(
                current.phase,
                RuntimePhase::Ready | RuntimePhase::Failed | RuntimePhase::Cancelled
            ) {
                return Ok(current);
            }
        }
        let generation_id = Uuid::new_v4().to_string();
        self.profiles
            .begin_activation(&profile.id, profile.revision, &generation_id, reason)?;
        let operation_kind = match reason {
            ActivationReason::Startup => "start",
            ActivationReason::UserSwitch => "switch-profile",
            ActivationReason::ProfileUpdated => "update-profile",
            ActivationReason::Recovery => "restart",
        };
        let cancellation = self
            .generations
            .begin_operation(&generation_id, operation_kind)
            .await;
        let coordinator = Arc::clone(self);
        let task_generation_id = generation_id.clone();
        tauri::async_runtime::spawn(async move {
            let result = coordinator
                .generations
                .launch_candidate(&task_generation_id, profile, repair, &cancellation)
                .await;
            match result {
                Ok(candidate) => {
                    if let Err(error) = coordinator
                        .generations
                        .prepare_activation(&task_generation_id)
                        .await
                    {
                        let _ = coordinator.profiles.fail_pending(
                            &task_generation_id,
                            "runtime-activation",
                            error.to_string(),
                        );
                        coordinator
                            .generations
                            .finish_failure(&task_generation_id, error)
                            .await;
                        return;
                    }
                    if let Err(error) = coordinator
                        .profiles
                        .commit_pending(&task_generation_id, candidate.runtime_version.clone())
                    {
                        let _ = coordinator.profiles.fail_pending(
                            &task_generation_id,
                            "profile-commit",
                            error.to_string(),
                        );
                        coordinator
                            .generations
                            .finish_failure(&task_generation_id, error)
                            .await;
                        return;
                    }
                    match coordinator.generations.activate(&task_generation_id).await {
                        Ok(snapshot) => {
                            let renderer_url = snapshot.renderer_url.unwrap_or_default();
                            coordinator.sink.runtime(RuntimeEvent::Ready {
                                operation_id: task_generation_id.clone(),
                                renderer_url,
                            });
                        }
                        Err(error) => {
                            coordinator
                                .generations
                                .finish_failure(&task_generation_id, error)
                                .await;
                        }
                    }
                }
                Err(error) => {
                    let _ = coordinator.profiles.fail_pending(
                        &task_generation_id,
                        "candidate-readiness",
                        error.to_string(),
                    );
                    if let Ok(state) = coordinator.profiles.state() {
                        if let Some(lkg) = state.last_known_good {
                            coordinator.sink.desktop(DesktopEvent::ProfileRecovered {
                                generation_id: task_generation_id.clone(),
                                profile: ProfileSelection {
                                    profile_id: lkg.profile_id,
                                    revision: lkg.revision,
                                },
                                reason: "目标 Profile 启动失败，已保留上一个可用工作台".to_string(),
                            });
                        }
                    }
                    coordinator
                        .generations
                        .finish_failure(&task_generation_id, error)
                        .await;
                }
            }
        });
        Ok(BootstrapReply {
            operation_id: generation_id,
            phase: RuntimePhase::Checking,
            renderer_url: None,
        })
    }

    pub async fn cancel(&self) -> Result<(), RuntimeFailure> {
        self.generations.cancel().await
    }

    pub async fn export_diagnostics(&self) -> Result<String, RuntimeFailure> {
        self.generations.export_diagnostics().await
    }

    pub async fn validate_generation(&self, generation_id: &str) -> Result<(), RuntimeFailure> {
        if self.generations.is_active_generation(generation_id).await {
            return Ok(());
        }
        let mut failure = RuntimeFailure::internal("请求不属于当前活动工作台");
        failure.recoverable = false;
        Err(failure)
    }

    pub async fn shutdown(&self) -> Result<(), RuntimeFailure> {
        self.generations.shutdown().await
    }

    pub async fn shutdown_barrier(&self) -> Result<(), RuntimeFailure> {
        self.shutdown().await
    }

    fn selected_profile(&self) -> Result<ProfileRecord, RuntimeFailure> {
        if let Some(selected) = self.profiles.state()?.selected_profile {
            return self.profiles.get(&selected.profile_id);
        }
        self.profiles
            .list()?
            .into_iter()
            .next()
            .ok_or_else(|| RuntimeFailure::internal("没有可启动的 Profile"))
    }
}

#[cfg(test)]
mod tests {
    use std::{
        collections::VecDeque,
        future::Future,
        path::PathBuf,
        pin::Pin,
        sync::{Arc, Mutex as StdMutex},
        time::Duration,
    };

    use semver::Version;
    use tokio_util::sync::CancellationToken;

    use super::*;
    use crate::{
        generation::{
            coordinator::{GenerationProcess, LaunchedGeneration, RuntimeLauncher},
            model::DesktopEvent,
        },
        profile::model::ProfileDraft,
        runtime::{
            model::{RuntimeEvent, RuntimeFailureCode},
            paths::RuntimePaths,
        },
    };

    #[derive(Default)]
    struct NoopSink;

    impl DesktopEventSink for NoopSink {
        fn runtime(&self, _event: RuntimeEvent) {}
        fn desktop(&self, _event: DesktopEvent) {}
    }

    #[derive(Default)]
    struct FakeProcess;

    impl GenerationProcess for FakeProcess {
        fn terminate<'a>(
            &'a self,
        ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
            Box::pin(async { Ok(()) })
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
        launches: std::sync::atomic::AtomicUsize,
    }

    impl FakeLauncher {
        fn fail_next(&self) {
            self.outcomes
                .lock()
                .unwrap()
                .push_back(Err(RuntimeFailure::new(
                    RuntimeFailureCode::Process,
                    "handshake",
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
            self.launches
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let outcome = self.outcomes.lock().unwrap().pop_front().unwrap_or(Ok(()));
            Box::pin(async move {
                outcome?;
                Ok(LaunchedGeneration {
                    runtime_version: Version::new(1, 8, 2),
                    renderer_url: "http://127.0.0.1:39000/".to_string(),
                    process: Arc::new(FakeProcess),
                    activation: Arc::new(crate::generation::coordinator::NoopCandidateActivation),
                })
            })
        }

        fn check_update<'a>(
            &'a self,
        ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
            Box::pin(async { Ok(()) })
        }
    }

    fn runtime_paths(root: PathBuf) -> RuntimePaths {
        RuntimePaths {
            versions: root.join("runtime/versions"),
            downloads: root.join("runtime/downloads"),
            user_downloads: root.join("Downloads"),
            logs: root.join("logs"),
            diagnostics: root.join("diagnostics"),
            current: root.join("runtime/current.json"),
            bundled_runtime: root.join("bundled"),
            root,
        }
    }

    async fn wait_until(mut predicate: impl FnMut() -> bool) {
        for _ in 0..50 {
            if predicate() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("coordinator did not settle");
    }

    #[tokio::test]
    async fn failed_candidate_keeps_active_and_restores_profile_lkg() {
        let temporary = tempfile::tempdir().unwrap();
        let profiles =
            Arc::new(ProfileRepository::open(temporary.path().join("profiles")).unwrap());
        let active = profiles
            .create(ProfileDraft::named("A", temporary.path().join("a")))
            .unwrap();
        let target = profiles
            .create(ProfileDraft::named("B", temporary.path().join("b")))
            .unwrap();
        let launcher = Arc::new(FakeLauncher::default());
        let sink: Arc<dyn DesktopEventSink> = Arc::new(NoopSink);
        let generations = GenerationCoordinator::new(
            runtime_paths(temporary.path().to_path_buf()),
            launcher.clone(),
            sink.clone(),
        )
        .unwrap();
        let desktop = DesktopCoordinator::new(generations.clone(), profiles.clone(), sink);

        desktop.start().await.unwrap();
        wait_until(|| profiles.state().unwrap().last_known_good.is_some()).await;
        let active_generation = generations.active_id().await.unwrap();
        assert_eq!(
            profiles
                .state()
                .unwrap()
                .last_known_good
                .unwrap()
                .profile_id,
            active.id
        );

        launcher.fail_next();
        desktop.switch_profile(target.id).await.unwrap();
        wait_until(|| {
            let state = profiles.state().unwrap();
            state.pending.is_none() && !state.failed_attempts.is_empty()
        })
        .await;

        let state = profiles.state().unwrap();
        assert_eq!(state.last_known_good.unwrap().profile_id, active.id);
        assert_eq!(generations.active_id().await.unwrap(), active_generation);
    }

    #[tokio::test]
    async fn fourth_equivalent_failure_is_blocked_before_launch() {
        let temporary = tempfile::tempdir().unwrap();
        let profiles =
            Arc::new(ProfileRepository::open(temporary.path().join("profiles")).unwrap());
        profiles
            .create(ProfileDraft::named("A", temporary.path().join("a")))
            .unwrap();
        let target = profiles
            .create(ProfileDraft::named("B", temporary.path().join("b")))
            .unwrap();
        let launcher = Arc::new(FakeLauncher::default());
        let sink: Arc<dyn DesktopEventSink> = Arc::new(NoopSink);
        let generations = GenerationCoordinator::new(
            runtime_paths(temporary.path().to_path_buf()),
            launcher.clone(),
            sink.clone(),
        )
        .unwrap();
        let desktop = DesktopCoordinator::new(generations, profiles.clone(), sink);
        desktop.start().await.unwrap();
        wait_until(|| profiles.state().unwrap().last_known_good.is_some()).await;

        for expected_failures in 1..=3 {
            launcher.fail_next();
            desktop.switch_profile(target.id).await.unwrap();
            wait_until(|| profiles.state().unwrap().failed_attempts.len() >= expected_failures)
                .await;
        }
        assert_eq!(
            launcher.launches.load(std::sync::atomic::Ordering::SeqCst),
            4
        );

        desktop.switch_profile(target.id).await.unwrap();
        wait_until(|| profiles.state().unwrap().failed_attempts.len() >= 4).await;
        assert_eq!(
            launcher.launches.load(std::sync::atomic::Ordering::SeqCst),
            4,
            "breaker must stop the fourth equivalent candidate before launch"
        );
    }

    #[tokio::test]
    async fn deferred_read_only_repository_fails_before_coordinator_launch_or_state_write() {
        let temporary = tempfile::tempdir().unwrap();
        let profile_root = temporary.path().join("profiles");
        let writable = ProfileRepository::open(profile_root.clone()).unwrap();
        writable
            .create(ProfileDraft::named("A", temporary.path().join("a")))
            .unwrap();
        drop(writable);
        let profiles_path = profile_root.join("profiles.json");
        let state_path = profile_root.join("state.json");
        let profiles_before = std::fs::read(&profiles_path).unwrap();
        let state_before = std::fs::read(&state_path).unwrap_or_default();
        let profiles = Arc::new(ProfileRepository::open_read_only(profile_root).unwrap());
        let launcher = Arc::new(FakeLauncher::default());
        let sink: Arc<dyn DesktopEventSink> = Arc::new(NoopSink);
        let generations = GenerationCoordinator::new(
            runtime_paths(temporary.path().to_path_buf()),
            launcher.clone(),
            sink.clone(),
        )
        .unwrap();
        let desktop = DesktopCoordinator::new(generations, profiles, sink);

        assert!(desktop.start().await.is_err());
        assert_eq!(
            launcher.launches.load(std::sync::atomic::Ordering::SeqCst),
            0
        );
        assert_eq!(std::fs::read(&profiles_path).unwrap(), profiles_before);
        assert_eq!(std::fs::read(&state_path).unwrap_or_default(), state_before);
    }
}
