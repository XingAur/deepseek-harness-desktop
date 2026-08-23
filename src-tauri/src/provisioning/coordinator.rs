use std::{collections::BTreeMap, future::Future, path::Path, pin::Pin, sync::Arc, time::Duration};

use chrono::Utc;
use semver::Version;
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use super::{
    model::{
        PreparedProvisioning, ProbeReceipt, ProvisioningEvent, ProvisioningPhase,
        ProvisioningReceipt, ProvisioningSession,
    },
    receipt::ProvisioningReceiptStore,
};
use crate::{
    profile::model::{PermissionMode, ProfileRecord},
    runtime::{
        health::{ReadinessExpectation, wait_for_readiness},
        manifest::{parse_and_verify_manifest, release_public_key},
        model::{RuntimeFailure, RuntimeTarget},
        paths::RuntimePaths,
        process::{reserve_loopback_port, spawn_runtime_from_dir},
        updater::{ActivatedProvisioningCandidate, RuntimeUpdater},
    },
};

struct RuntimeCandidateActivation(ActivatedProvisioningCandidate);

impl CandidateActivation for RuntimeCandidateActivation {
    fn active_dir(&self) -> &Path {
        &self.0.active_dir
    }

    fn commit(self: Box<Self>) -> Result<(), RuntimeFailure> {
        self.0.commit()
    }

    fn rollback(self: Box<Self>) -> Result<(), RuntimeFailure> {
        self.0.rollback()
    }
}

impl ProvisioningBackend for RuntimeUpdater {
    fn prepare_local(
        &self,
        session: &ProvisioningSession,
    ) -> Result<Option<PreparedProvisioning>, RuntimeFailure> {
        self.prepare_local_candidate(session)
    }

    fn prepare<'a>(
        &'a self,
        session: &'a ProvisioningSession,
        cancellation: &'a CancellationToken,
        progress: &'a (dyn Fn(ProvisioningEvent) + Send + Sync),
    ) -> Pin<Box<dyn Future<Output = Result<PreparedProvisioning, RuntimeFailure>> + Send + 'a>>
    {
        Box::pin(self.prepare_candidate_with_progress(session, cancellation, progress))
    }

    fn activate(
        &self,
        prepared: &PreparedProvisioning,
    ) -> Result<Box<dyn CandidateActivation>, RuntimeFailure> {
        Ok(Box::new(RuntimeCandidateActivation(
            self.activate_candidate(prepared)?,
        )))
    }

    fn discard(&self, prepared: &PreparedProvisioning) -> Result<(), RuntimeFailure> {
        self.discard_candidate(prepared)
    }
}

pub struct RuntimeCandidateProbe {
    paths: RuntimePaths,
    client: reqwest::Client,
    deadline: Duration,
    stabilization: Duration,
}

impl RuntimeCandidateProbe {
    pub fn new(paths: RuntimePaths, client: reqwest::Client) -> Self {
        Self {
            paths,
            client,
            deadline: Duration::from_secs(45),
            stabilization: Duration::from_secs(3),
        }
    }
}

impl CandidateProbe for RuntimeCandidateProbe {
    fn probe<'a>(
        &'a self,
        candidate: &'a PreparedProvisioning,
        session: &'a ProvisioningSession,
        cancellation: &'a CancellationToken,
    ) -> Pin<Box<dyn Future<Output = Result<ProbeReceipt, RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move {
            let manifest_bytes = tokio::fs::read(candidate.candidate_dir.join("manifest.json"))
                .await
                .map_err(RuntimeFailure::internal)?;
            let manifest =
                parse_and_verify_manifest(&manifest_bytes, candidate.target, release_public_key())?;
            if manifest.version != candidate.runtime_version || session.id != candidate.session_id {
                return Err(RuntimeFailure::internal(
                    "Probe candidate 与 provisioning receipt 不匹配",
                ));
            }
            let profile_root = self
                .paths
                .root
                .join("profiles")
                .join(format!("provisioning-probe-{}", session.id));
            if profile_root.exists() {
                tokio::fs::remove_dir_all(&profile_root)
                    .await
                    .map_err(RuntimeFailure::internal)?;
            }
            tokio::fs::create_dir_all(&profile_root)
                .await
                .map_err(RuntimeFailure::internal)?;
            let now = Utc::now();
            let profile = ProfileRecord {
                id: session.id,
                name: "Runtime provisioning probe".into(),
                data_root: profile_root.clone(),
                permission_mode: PermissionMode::ReadOnly,
                agent_permission_default: Default::default(),
                revision: 1,
                created_at: now,
                updated_at: now,
            };
            let port = reserve_loopback_port()?;
            let session_token = Uuid::new_v4().to_string();
            let runtime = match spawn_runtime_from_dir(
                &self.paths,
                &candidate.candidate_dir,
                &manifest,
                &profile,
                &format!("provisioning-probe-{}", session.id),
                port,
                &session_token,
            )
            .await
            {
                Ok(runtime) => Arc::new(Mutex::new(runtime)),
                Err(cause) => {
                    let _ = tokio::fs::remove_dir_all(&profile_root).await;
                    return Err(cause);
                }
            };
            let readiness = wait_for_readiness(
                &self.client,
                port,
                &manifest.health_path,
                self.deadline,
                cancellation,
                &ReadinessExpectation {
                    runtime_version: manifest.version.clone(),
                    profile_id: profile.id.to_string(),
                    profile_revision: profile.revision,
                    stabilization: self.stabilization,
                },
                &runtime,
            )
            .await;
            let terminate = runtime.lock().await.terminate().await;
            let cleanup = tokio::fs::remove_dir_all(&profile_root).await;
            readiness?;
            terminate?;
            cleanup.map_err(RuntimeFailure::internal)?;
            Ok(ProbeReceipt {
                contract_version: 1,
                runtime_version: manifest.version,
                completed_at: Utc::now(),
            })
        })
    }
}

pub trait CandidateActivation: Send {
    fn active_dir(&self) -> &Path;
    fn commit(self: Box<Self>) -> Result<(), RuntimeFailure>;
    fn rollback(self: Box<Self>) -> Result<(), RuntimeFailure>;
}

pub trait ProvisioningBackend: Send + Sync {
    fn prepare_local(
        &self,
        _session: &ProvisioningSession,
    ) -> Result<Option<PreparedProvisioning>, RuntimeFailure> {
        Ok(None)
    }

    fn prepare<'a>(
        &'a self,
        session: &'a ProvisioningSession,
        cancellation: &'a CancellationToken,
        progress: &'a (dyn Fn(ProvisioningEvent) + Send + Sync),
    ) -> Pin<Box<dyn Future<Output = Result<PreparedProvisioning, RuntimeFailure>> + Send + 'a>>;

    fn activate(
        &self,
        prepared: &PreparedProvisioning,
    ) -> Result<Box<dyn CandidateActivation>, RuntimeFailure>;

    fn discard(&self, prepared: &PreparedProvisioning) -> Result<(), RuntimeFailure>;
}

pub trait CandidateProbe: Send + Sync {
    fn probe<'a>(
        &'a self,
        candidate: &'a PreparedProvisioning,
        session: &'a ProvisioningSession,
        cancellation: &'a CancellationToken,
    ) -> Pin<Box<dyn Future<Output = Result<ProbeReceipt, RuntimeFailure>> + Send + 'a>>;
}

pub trait ProvisioningEventSink: Send + Sync {
    fn emit(&self, event: ProvisioningEvent) -> Result<(), RuntimeFailure>;
}

pub struct ProvisioningCoordinator {
    operations: Mutex<()>,
    backend: Arc<dyn ProvisioningBackend>,
    probe: Arc<dyn CandidateProbe>,
    receipts: Arc<ProvisioningReceiptStore>,
    sessions: Mutex<BTreeMap<Uuid, CancellationToken>>,
    events: Arc<dyn ProvisioningEventSink>,
}

impl ProvisioningCoordinator {
    pub fn new(
        backend: Arc<dyn ProvisioningBackend>,
        probe: Arc<dyn CandidateProbe>,
        receipts: Arc<ProvisioningReceiptStore>,
        events: Arc<dyn ProvisioningEventSink>,
    ) -> Arc<Self> {
        Arc::new(Self {
            operations: Mutex::new(()),
            backend,
            probe,
            receipts,
            sessions: Mutex::new(BTreeMap::new()),
            events,
        })
    }

    pub async fn start_session(&self) -> Result<ProvisioningSession, RuntimeFailure> {
        let session = ProvisioningSession {
            id: Uuid::new_v4(),
            desktop_version: Version::parse(env!("CARGO_PKG_VERSION"))
                .map_err(RuntimeFailure::internal)?,
            target: RuntimeTarget::current()?,
            started_at: Utc::now(),
        };
        self.sessions
            .lock()
            .await
            .insert(session.id, CancellationToken::new());
        Ok(session)
    }

    pub async fn prepare(
        &self,
        session: &ProvisioningSession,
    ) -> Result<PreparedProvisioning, RuntimeFailure> {
        self.prepare_with_policy(session, true).await
    }

    pub async fn prepare_fresh(
        &self,
        session: &ProvisioningSession,
    ) -> Result<PreparedProvisioning, RuntimeFailure> {
        self.prepare_with_policy(session, false).await
    }

    async fn prepare_with_policy(
        &self,
        session: &ProvisioningSession,
        allow_reuse: bool,
    ) -> Result<PreparedProvisioning, RuntimeFailure> {
        let _operation = self.operations.lock().await;
        let cancellation = self.session_token(session.id).await?;
        self.emit(
            session.id,
            ProvisioningPhase::Checking,
            "正在检查运行组件",
            true,
        );
        let local = if allow_reuse {
            self.backend.prepare_local(session)?
        } else {
            None
        };
        let mut prepared = match local {
            Some(prepared) => prepared,
            None => {
                let report = |event| {
                    let _ = self.events.emit(event);
                };
                self.backend
                    .prepare(session, &cancellation, &report)
                    .await?
            }
        };
        self.emit(
            session.id,
            ProvisioningPhase::Probing,
            "正在验证运行组件",
            true,
        );
        let probe = match self.probe.probe(&prepared, session, &cancellation).await {
            Ok(probe) => probe,
            Err(error) => {
                let _ = self.backend.discard(&prepared);
                return Err(error);
            }
        };
        if probe.runtime_version != prepared.runtime_version {
            let _ = self.backend.discard(&prepared);
            return Err(RuntimeFailure::internal("Probe 返回了错误的 Runtime 版本"));
        }
        prepared.probe_contract_version = probe.contract_version;
        self.receipts.write_prepared(&prepared)?;
        self.emit(
            session.id,
            ProvisioningPhase::Prepared,
            "运行组件已准备",
            true,
        );
        Ok(prepared)
    }

    pub async fn commit(
        &self,
        session_id: Uuid,
        manifest_hash: &str,
    ) -> Result<ProvisioningReceipt, RuntimeFailure> {
        let _operation = self.operations.lock().await;
        let prepared = self.receipts.validate_prepared(session_id, manifest_hash)?;
        self.emit(
            session_id,
            ProvisioningPhase::Committing,
            "正在完成安装",
            false,
        );
        let activation = self.backend.activate(&prepared)?;
        let active_dir = activation.active_dir().to_path_buf();
        let receipt = match self.receipts.finalize(&prepared, active_dir) {
            Ok(receipt) => receipt,
            Err(error) => {
                let _ = activation.rollback();
                return Err(error);
            }
        };
        activation.commit()?;
        self.sessions.lock().await.remove(&session_id);
        self.emit(
            session_id,
            ProvisioningPhase::Completed,
            "安装已完成",
            false,
        );
        Ok(receipt)
    }

    pub async fn cancel(&self, session_id: Uuid) -> Result<(), RuntimeFailure> {
        let token = self.session_token(session_id).await?;
        token.cancel();
        self.emit(session_id, ProvisioningPhase::Cancelled, "安装已取消", true);
        Ok(())
    }

    pub async fn retry(
        &self,
        session: &ProvisioningSession,
    ) -> Result<PreparedProvisioning, RuntimeFailure> {
        self.reset_session(session.id).await;
        self.prepare(session).await
    }

    pub async fn retry_fresh(
        &self,
        session: &ProvisioningSession,
    ) -> Result<PreparedProvisioning, RuntimeFailure> {
        self.reset_session(session.id).await;
        self.prepare_fresh(session).await
    }

    async fn reset_session(&self, session_id: Uuid) {
        self.sessions
            .lock()
            .await
            .insert(session_id, CancellationToken::new());
    }

    async fn session_token(&self, session_id: Uuid) -> Result<CancellationToken, RuntimeFailure> {
        self.sessions
            .lock()
            .await
            .get(&session_id)
            .cloned()
            .ok_or_else(|| RuntimeFailure::internal("Provisioning session 不存在"))
    }

    fn emit(&self, session_id: Uuid, phase: ProvisioningPhase, message: &str, recoverable: bool) {
        let _ = self.events.emit(ProvisioningEvent {
            session_id,
            phase,
            message: message.to_string(),
            recoverable,
            completed: None,
            total: None,
            bytes_per_second: None,
        });
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::{
        Mutex as StdMutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    };

    use super::*;
    use crate::runtime::model::{RuntimeFailureCode, RuntimeTarget};

    struct FakeBackend {
        root: PathBuf,
        pointer: Arc<StdMutex<Option<Version>>>,
        discarded: AtomicBool,
    }

    struct FakeActivation {
        active_dir: PathBuf,
        pointer: Arc<StdMutex<Option<Version>>>,
        previous: Option<Version>,
    }

    impl CandidateActivation for FakeActivation {
        fn active_dir(&self) -> &Path {
            &self.active_dir
        }
        fn commit(self: Box<Self>) -> Result<(), RuntimeFailure> {
            Ok(())
        }
        fn rollback(self: Box<Self>) -> Result<(), RuntimeFailure> {
            *self.pointer.lock().unwrap() = self.previous;
            Ok(())
        }
    }

    impl ProvisioningBackend for FakeBackend {
        fn prepare<'a>(
            &'a self,
            session: &'a ProvisioningSession,
            _cancellation: &'a CancellationToken,
            _progress: &'a (dyn Fn(ProvisioningEvent) + Send + Sync),
        ) -> Pin<Box<dyn Future<Output = Result<PreparedProvisioning, RuntimeFailure>> + Send + 'a>>
        {
            Box::pin(async move {
                let candidate = self.root.join(format!("runtime/candidates/{}", session.id));
                std::fs::create_dir_all(&candidate).unwrap();
                Ok(PreparedProvisioning {
                    schema_version: 1,
                    session_id: session.id,
                    desktop_version: session.desktop_version.clone(),
                    target: RuntimeTarget::WindowsX86_64,
                    runtime_version: Version::new(1, 8, 2),
                    manifest_sha256: "manifest-a".into(),
                    payload_sha256: "payload-a".into(),
                    candidate_dir: candidate,
                    reused_active: false,
                    probe_contract_version: 0,
                    prepared_at: Utc::now(),
                })
            })
        }

        fn activate(
            &self,
            prepared: &PreparedProvisioning,
        ) -> Result<Box<dyn CandidateActivation>, RuntimeFailure> {
            let active_dir = self
                .root
                .join(format!("runtime/versions/{}", prepared.runtime_version));
            std::fs::create_dir_all(&active_dir).map_err(RuntimeFailure::internal)?;
            let previous = self
                .pointer
                .lock()
                .unwrap()
                .replace(prepared.runtime_version.clone());
            Ok(Box::new(FakeActivation {
                active_dir,
                pointer: self.pointer.clone(),
                previous,
            }))
        }

        fn discard(&self, prepared: &PreparedProvisioning) -> Result<(), RuntimeFailure> {
            self.discarded.store(true, Ordering::SeqCst);
            if prepared.candidate_dir.exists() {
                std::fs::remove_dir_all(&prepared.candidate_dir)
                    .map_err(RuntimeFailure::internal)?;
            }
            Ok(())
        }
    }

    struct FakeProbe {
        calls: AtomicUsize,
        fail: AtomicBool,
    }
    impl CandidateProbe for FakeProbe {
        fn probe<'a>(
            &'a self,
            candidate: &'a PreparedProvisioning,
            _session: &'a ProvisioningSession,
            _cancellation: &'a CancellationToken,
        ) -> Pin<Box<dyn Future<Output = Result<ProbeReceipt, RuntimeFailure>> + Send + 'a>>
        {
            self.calls.fetch_add(1, Ordering::SeqCst);
            let fail = self.fail.load(Ordering::SeqCst);
            let version = candidate.runtime_version.clone();
            Box::pin(async move {
                if fail {
                    return Err(RuntimeFailure::new(
                        RuntimeFailureCode::Process,
                        "control-api",
                    ));
                }
                Ok(ProbeReceipt {
                    contract_version: 1,
                    runtime_version: version,
                    completed_at: Utc::now(),
                })
            })
        }
    }

    struct NoopEvents;
    impl ProvisioningEventSink for NoopEvents {
        fn emit(&self, _event: ProvisioningEvent) -> Result<(), RuntimeFailure> {
            Ok(())
        }
    }

    struct Fixture {
        _dir: tempfile::TempDir,
        coordinator: Arc<ProvisioningCoordinator>,
        backend: Arc<FakeBackend>,
        probe: Arc<FakeProbe>,
    }

    impl Fixture {
        fn new(previous: Option<Version>) -> Self {
            let dir = tempfile::tempdir().unwrap();
            std::fs::create_dir_all(dir.path().join("state")).unwrap();
            std::fs::create_dir_all(dir.path().join("runtime")).unwrap();
            let backend = Arc::new(FakeBackend {
                root: dir.path().to_path_buf(),
                pointer: Arc::new(StdMutex::new(previous)),
                discarded: AtomicBool::new(false),
            });
            let probe = Arc::new(FakeProbe {
                calls: AtomicUsize::new(0),
                fail: AtomicBool::new(false),
            });
            let coordinator = ProvisioningCoordinator::new(
                backend.clone(),
                probe.clone(),
                Arc::new(ProvisioningReceiptStore::new(dir.path().join("state"))),
                Arc::new(NoopEvents),
            );
            Self {
                _dir: dir,
                coordinator,
                backend,
                probe,
            }
        }
    }

    #[tokio::test]
    async fn prepare_probes_without_publishing_and_commit_publishes_once() {
        let fixture = Fixture::new(None);
        let session = fixture.coordinator.start_session().await.unwrap();
        let prepared = fixture.coordinator.prepare(&session).await.unwrap();
        assert_eq!(fixture.probe.calls.load(Ordering::SeqCst), 1);
        assert!(fixture.backend.pointer.lock().unwrap().is_none());
        let receipt = fixture
            .coordinator
            .commit(session.id, &prepared.manifest_sha256)
            .await
            .unwrap();
        assert_eq!(
            fixture.backend.pointer.lock().unwrap().clone().unwrap(),
            receipt.runtime_version
        );
        assert!(
            fixture
                .coordinator
                .commit(session.id, &prepared.manifest_sha256)
                .await
                .is_err()
        );
    }

    #[tokio::test]
    async fn failed_probe_discards_candidate_and_preserves_previous_runtime() {
        let fixture = Fixture::new(Some(Version::new(1, 7, 0)));
        fixture.probe.fail.store(true, Ordering::SeqCst);
        let session = fixture.coordinator.start_session().await.unwrap();
        assert!(fixture.coordinator.prepare(&session).await.is_err());
        assert_eq!(
            fixture.backend.pointer.lock().unwrap().clone(),
            Some(Version::new(1, 7, 0))
        );
        assert!(fixture.backend.discarded.load(Ordering::SeqCst));
    }
}
