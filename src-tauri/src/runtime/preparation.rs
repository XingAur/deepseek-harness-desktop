use std::sync::Arc;

use tokio_util::sync::CancellationToken;

use super::{
    compatibility::LocalRuntimeDecision,
    model::{RuntimeFailure, RuntimeManifest, RuntimePhase, RuntimeSourceKind, RuntimeTarget},
    paths::RuntimePaths,
    updater::{PreparedRuntime, RuntimeUpdater, bundled_resources_present},
};

#[derive(Clone, Debug)]
pub struct RuntimePreparationProgress {
    pub phase: RuntimePhase,
    pub completed: u64,
    pub total: Option<u64>,
    pub message: String,
}

pub struct PreparedRuntimeChoice {
    pub source: RuntimeSourceKind,
    pub manifest: RuntimeManifest,
    pub prepared: Option<PreparedRuntime>,
    pub updater: Arc<RuntimeUpdater>,
    pub verified_payload: Option<VerifiedPayload>,
}

pub struct RuntimePreparationService {
    online: Arc<RuntimeUpdater>,
    bundled: Option<Arc<RuntimeUpdater>>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VerifiedPayload {
    sha256: String,
}

impl VerifiedPayload {
    pub fn new(sha256: String) -> Self {
        Self { sha256 }
    }
}

pub fn should_retry_online(failed: &VerifiedPayload, online: &RuntimeManifest) -> bool {
    !failed.sha256.eq_ignore_ascii_case(&online.sha256)
}

impl RuntimePreparationService {
    pub fn new(paths: RuntimePaths, client: reqwest::Client) -> Result<Self, RuntimeFailure> {
        let target = RuntimeTarget::current()?;
        let online = Arc::new(RuntimeUpdater::new(paths.clone(), client.clone())?);
        let bundled = if bundled_resources_present(&paths, target) {
            Some(Arc::new(RuntimeUpdater::new_bundled(paths, client)?))
        } else {
            None
        };
        Ok(Self { online, bundled })
    }

    #[cfg(test)]
    fn with_updaters(online: Arc<RuntimeUpdater>, bundled: Option<Arc<RuntimeUpdater>>) -> Self {
        Self { online, bundled }
    }

    pub fn online_updater(&self) -> Arc<RuntimeUpdater> {
        Arc::clone(&self.online)
    }

    pub async fn prepare(
        &self,
        generation_id: &str,
        repair: bool,
        cancellation: &CancellationToken,
        progress: Arc<dyn Fn(RuntimePreparationProgress) + Send + Sync>,
    ) -> Result<PreparedRuntimeChoice, RuntimeFailure> {
        if !repair
            && let Ok(LocalRuntimeDecision::FastStart(manifest)) =
                self.online.local_provisioned_decision()
        {
            return Ok(PreparedRuntimeChoice {
                source: RuntimeSourceKind::Local,
                manifest,
                prepared: None,
                updater: Arc::clone(&self.online),
                verified_payload: None,
            });
        }

        if !repair && let Some(bundled) = &self.bundled {
            if let Ok(manifest) = bundled.required_manifest().await {
                match bundled.verify_bundled_archive(&manifest).await {
                    Ok(_) => {
                        let verified = VerifiedPayload::new(manifest.sha256.clone());
                        match bundled
                            .prepare_manifest_with_progress(
                                generation_id,
                                manifest.clone(),
                                cancellation,
                                Arc::clone(&progress),
                            )
                            .await
                        {
                            Ok(prepared) => {
                                return Ok(PreparedRuntimeChoice {
                                    source: RuntimeSourceKind::Bundled,
                                    manifest,
                                    prepared: Some(prepared),
                                    updater: Arc::clone(bundled),
                                    verified_payload: Some(verified),
                                });
                            }
                            Err(failure) => {
                                return self
                                    .prepare_online_after_verified_failure(
                                        generation_id,
                                        verified,
                                        failure,
                                        cancellation,
                                        progress,
                                    )
                                    .await;
                            }
                        }
                    }
                    Err(_) => {
                        progress(RuntimePreparationProgress {
                            phase: RuntimePhase::FetchingManifest,
                            completed: 0,
                            total: None,
                            message: "内置组件不可用，正在联网修复".into(),
                        });
                    }
                }
            } else {
                progress(RuntimePreparationProgress {
                    phase: RuntimePhase::FetchingManifest,
                    completed: 0,
                    total: None,
                    message: "内置组件不可用，正在联网修复".into(),
                });
            }
        }

        self.prepare_online(generation_id, cancellation, progress)
            .await
    }

    pub async fn prepare_online_after_verified_failure(
        &self,
        generation_id: &str,
        failed_payload: VerifiedPayload,
        original_failure: RuntimeFailure,
        cancellation: &CancellationToken,
        progress: Arc<dyn Fn(RuntimePreparationProgress) + Send + Sync>,
    ) -> Result<PreparedRuntimeChoice, RuntimeFailure> {
        let manifest = self.online.required_manifest().await?;
        if !should_retry_online(&failed_payload, &manifest) {
            return Err(original_failure);
        }
        progress(RuntimePreparationProgress {
            phase: RuntimePhase::FetchingManifest,
            completed: 0,
            total: None,
            message: "内置组件不可用，正在联网修复".into(),
        });
        self.prepare_online_manifest(generation_id, manifest, cancellation, progress)
            .await
    }

    async fn prepare_online(
        &self,
        generation_id: &str,
        cancellation: &CancellationToken,
        progress: Arc<dyn Fn(RuntimePreparationProgress) + Send + Sync>,
    ) -> Result<PreparedRuntimeChoice, RuntimeFailure> {
        let manifest = self.online.required_manifest().await?;
        self.prepare_online_manifest(generation_id, manifest, cancellation, progress)
            .await
    }

    async fn prepare_online_manifest(
        &self,
        generation_id: &str,
        manifest: RuntimeManifest,
        cancellation: &CancellationToken,
        progress: Arc<dyn Fn(RuntimePreparationProgress) + Send + Sync>,
    ) -> Result<PreparedRuntimeChoice, RuntimeFailure> {
        let prepared = self
            .online
            .prepare_manifest_with_progress(generation_id, manifest.clone(), cancellation, progress)
            .await?;
        Ok(PreparedRuntimeChoice {
            source: RuntimeSourceKind::Online,
            manifest,
            prepared: Some(prepared),
            updater: Arc::clone(&self.online),
            verified_payload: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use std::{
        future::Future,
        io::Write,
        pin::Pin,
        sync::{
            Arc, Mutex,
            atomic::{AtomicUsize, Ordering},
        },
    };

    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    use ed25519_dalek::{Signer, SigningKey};
    use flate2::{Compression, write::GzEncoder};
    use semver::Version;
    use sha2::{Digest, Sha256};

    use super::*;
    use crate::runtime::{
        manifest::canonical_payload, model::ArchiveKind, updater::RuntimeManifestSource,
    };

    struct FakeManifestSource {
        manifest: Mutex<RuntimeManifest>,
        fetches: Arc<AtomicUsize>,
    }

    impl RuntimeManifestSource for FakeManifestSource {
        fn fetch<'a>(
            &'a self,
            _target: RuntimeTarget,
        ) -> Pin<Box<dyn Future<Output = Result<RuntimeManifest, RuntimeFailure>> + Send + 'a>>
        {
            self.fetches.fetch_add(1, Ordering::SeqCst);
            let manifest = self.manifest.lock().unwrap().clone();
            Box::pin(async move { Ok(manifest) })
        }
    }

    struct PreparationFixture {
        _temporary: tempfile::TempDir,
        service: RuntimePreparationService,
        online_fetches: Arc<AtomicUsize>,
    }

    impl PreparationFixture {
        fn full_package() -> Self {
            build_preparation_fixture(true, false)
        }

        fn online_package() -> Self {
            build_preparation_fixture(false, false)
        }

        fn corrupt_full_package() -> Self {
            build_preparation_fixture(true, true)
        }

        fn online_fetches(&self) -> usize {
            self.online_fetches.load(Ordering::SeqCst)
        }
    }

    fn build_preparation_fixture(bundled: bool, corrupt: bool) -> PreparationFixture {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().to_path_buf();
        let paths = RuntimePaths {
            versions: root.join("runtime/versions"),
            downloads: root.join("runtime/downloads"),
            logs: root.join("logs"),
            diagnostics: root.join("diagnostics"),
            current: root.join("runtime/current.json"),
            bundled_runtime: root.join("bundled"),
            root,
        };
        std::fs::create_dir_all(&paths.versions).unwrap();
        std::fs::create_dir_all(&paths.downloads).unwrap();
        let manifest = fixture_manifest(temporary.path());
        let online_fetches = Arc::new(AtomicUsize::new(0));
        let online = Arc::new(RuntimeUpdater::with_source(
            paths.clone(),
            reqwest::Client::new(),
            Arc::new(FakeManifestSource {
                manifest: Mutex::new(manifest.clone()),
                fetches: Arc::clone(&online_fetches),
            }),
        ));
        let bundled_updater = bundled.then(|| {
            let manifests = paths.bundled_runtime.join("manifests");
            std::fs::create_dir_all(&manifests).unwrap();
            let source = manifest.url.to_file_path().unwrap();
            let destination = paths
                .bundled_runtime
                .join("dsh-runtime-windows-x86_64.tar.gz");
            std::fs::copy(source, &destination).unwrap();
            if corrupt {
                std::fs::write(&destination, vec![0_u8; manifest.size as usize]).unwrap();
            }
            std::fs::write(
                manifests.join("runtime-windows-x86_64.json"),
                serde_json::to_vec_pretty(&manifest).unwrap(),
            )
            .unwrap();
            Arc::new(RuntimeUpdater::new_bundled(paths.clone(), reqwest::Client::new()).unwrap())
        });
        PreparationFixture {
            _temporary: temporary,
            service: RuntimePreparationService::with_updaters(online, bundled_updater),
            online_fetches,
        }
    }

    fn fixture_manifest(root: &std::path::Path) -> RuntimeManifest {
        let archive_path = root.join("runtime.tar.gz");
        let encoder = GzEncoder::new(Vec::new(), Compression::default());
        let mut archive = tar::Builder::new(encoder);
        let body = b"runtime";
        let mut header = tar::Header::new_gnu();
        header.set_size(body.len() as u64);
        header.set_mode(0o644);
        header.set_cksum();
        archive
            .append_data(&mut header, "app/runtime.txt", body.as_slice())
            .unwrap();
        let bytes = archive.into_inner().unwrap().finish().unwrap();
        std::fs::File::create(&archive_path)
            .unwrap()
            .write_all(&bytes)
            .unwrap();
        sign_manifest(RuntimeManifest {
            schema_version: 1,
            version: Version::new(1, 0, 0),
            dsh_version: Version::parse("0.1.0-rc.7").unwrap(),
            target: RuntimeTarget::WindowsX86_64,
            url: url::Url::from_file_path(&archive_path).unwrap(),
            size: bytes.len() as u64,
            sha256: hex::encode(Sha256::digest(&bytes)),
            signature: String::new(),
            archive: ArchiveKind::TarGz,
            entrypoint: "app/runtime.txt".into(),
            args: Vec::new(),
            health_path: "/health".into(),
        })
    }

    fn sign_manifest(mut manifest: RuntimeManifest) -> RuntimeManifest {
        const DEV_PRIVATE_KEY: &str = "wbAbExHsjryIT22fTuRA3W61tJdaXFC7YxoAeN9uKnQ";
        let bytes: [u8; 32] = URL_SAFE_NO_PAD
            .decode(DEV_PRIVATE_KEY)
            .unwrap()
            .try_into()
            .unwrap();
        let value = serde_json::to_value(&manifest).unwrap();
        let payload = canonical_payload(&value, "signature").unwrap();
        manifest.signature =
            URL_SAFE_NO_PAD.encode(SigningKey::from_bytes(&bytes).sign(&payload).to_bytes());
        manifest
    }

    #[tokio::test]
    async fn missing_local_prefers_verified_bundled_without_online_fetch() {
        let fixture = PreparationFixture::full_package();
        let choice = fixture
            .service
            .prepare("g-1", false, &CancellationToken::new(), Arc::new(|_| {}))
            .await
            .unwrap();
        assert_eq!(choice.source, RuntimeSourceKind::Bundled);
        assert_eq!(fixture.online_fetches(), 0);
    }

    #[tokio::test]
    async fn package_without_bundled_resources_uses_online() {
        let fixture = PreparationFixture::online_package();
        let choice = fixture
            .service
            .prepare("g-2", false, &CancellationToken::new(), Arc::new(|_| {}))
            .await
            .unwrap();
        assert_eq!(choice.source, RuntimeSourceKind::Online);
        assert_eq!(fixture.online_fetches(), 1);
    }

    #[tokio::test]
    async fn corrupt_bundled_bytes_allow_online_repair_of_the_same_identity() {
        let fixture = PreparationFixture::corrupt_full_package();
        let choice = fixture
            .service
            .prepare("g-3", false, &CancellationToken::new(), Arc::new(|_| {}))
            .await
            .unwrap();
        assert_eq!(choice.source, RuntimeSourceKind::Online);
    }

    #[test]
    fn verified_failed_payload_is_not_retried_when_online_identity_matches() {
        let manifest = fixture_manifest(tempfile::tempdir().unwrap().path());
        let failed = VerifiedPayload::new(manifest.sha256.clone());
        assert!(!should_retry_online(&failed, &manifest));
        let mut different = manifest;
        different.sha256 = "b".repeat(64);
        assert!(should_retry_online(&failed, &different));
    }

    #[test]
    fn runtime_source_kind_is_safe_to_serialize() {
        assert_eq!(
            serde_json::to_string(&RuntimeSourceKind::Bundled).unwrap(),
            "\"bundled\""
        );
    }
}
