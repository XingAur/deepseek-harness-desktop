use std::{
    future::Future,
    path::{Path, PathBuf},
    pin::Pin,
    sync::Arc,
    time::Duration,
};

use chrono::Utc;
use semver::Version;
use sha2::{Digest, Sha256};
use tokio_util::sync::CancellationToken;

use crate::provisioning::{
    model::{PreparedProvisioning, ProvisioningEvent, ProvisioningPhase, ProvisioningSession},
    receipt::ProvisioningReceiptStore,
    source::{manifest_endpoint, runtime_source_policy},
};

use super::{
    activation::{self, ActivationReceipt, read_active_manifest, read_current},
    compatibility::{LocalRuntimeDecision, RuntimeRequirement, decide_local},
    download::{download_runtime, verify_file},
    manifest::{parse_and_verify_manifest, release_public_key},
    model::{
        ArchiveKind, RuntimeFailure, RuntimeFailureCode, RuntimeManifest, RuntimePhase,
        RuntimeSourceKind, RuntimeTarget,
    },
    paths::RuntimePaths,
    preparation::RuntimePreparationProgress,
};

pub trait RuntimeManifestSource: Send + Sync {
    fn fetch<'a>(
        &'a self,
        target: RuntimeTarget,
    ) -> Pin<Box<dyn Future<Output = Result<RuntimeManifest, RuntimeFailure>> + Send + 'a>>;
}

pub struct PreparedRuntime {
    pub manifest: RuntimeManifest,
    pub receipt: Option<ActivationReceipt>,
    pub archive: Option<PathBuf>,
    pub source: RuntimeSourceKind,
}

pub struct ActivatedProvisioningCandidate {
    pub active_dir: PathBuf,
    paths: RuntimePaths,
    receipt: Option<ActivationReceipt>,
}

pub struct RuntimeUpdater {
    paths: RuntimePaths,
    client: reqwest::Client,
    source: Arc<dyn RuntimeManifestSource>,
    archive_source: CandidateArchiveSource,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CandidateArchiveSource {
    Download,
    Bundled,
}

pub fn bundled_resources_present(paths: &RuntimePaths, target: RuntimeTarget) -> bool {
    paths
        .bundled_runtime
        .join("manifests")
        .join(format!("runtime-{}.json", target.as_str()))
        .is_file()
}

impl RuntimeUpdater {
    pub fn new(
        paths: RuntimePaths,
        fallback_client: reqwest::Client,
    ) -> Result<Self, RuntimeFailure> {
        let client = match manifest_endpoint()? {
            Some(endpoint) => {
                let policy = runtime_source_policy(endpoint.clone())?;
                let builder = reqwest::Client::builder()
                    .redirect(policy.redirect_policy())
                    .connect_timeout(Duration::from_secs(15));
                #[cfg(feature = "e2e")]
                let builder = if crate::provisioning::source::is_e2e_manifest_endpoint(&endpoint)? {
                    builder.danger_accept_invalid_certs(true)
                } else {
                    builder
                };
                builder.build().map_err(RuntimeFailure::internal)?
            }
            None => fallback_client,
        };
        let source = Arc::new(ReleaseManifestSource {
            paths: paths.clone(),
            client: client.clone(),
        });
        Ok(Self {
            paths,
            client,
            source,
            archive_source: CandidateArchiveSource::Download,
        })
    }

    pub fn new_bundled(
        paths: RuntimePaths,
        client: reqwest::Client,
    ) -> Result<Self, RuntimeFailure> {
        let source = Arc::new(BundledManifestSource {
            paths: paths.clone(),
        });
        Ok(Self {
            paths,
            client,
            source,
            archive_source: CandidateArchiveSource::Bundled,
        })
    }

    pub fn with_source(
        paths: RuntimePaths,
        client: reqwest::Client,
        source: Arc<dyn RuntimeManifestSource>,
    ) -> Self {
        Self {
            paths,
            client,
            source,
            archive_source: CandidateArchiveSource::Download,
        }
    }

    pub async fn required_bundled_manifest(&self) -> Result<RuntimeManifest, RuntimeFailure> {
        if self.archive_source != CandidateArchiveSource::Bundled {
            return Err(RuntimeFailure::internal(
                "只有捆绑 Runtime 更新器可以读取内置清单",
            ));
        }
        self.source.fetch(RuntimeTarget::current()?).await
    }

    pub async fn required_manifest(&self) -> Result<RuntimeManifest, RuntimeFailure> {
        let target = RuntimeTarget::current()?;
        let manifest = tokio::time::timeout(Duration::from_secs(30), self.source.fetch(target))
            .await
            .map_err(|_| {
                RuntimeFailure::new(RuntimeFailureCode::Network, "获取运行时清单超时")
            })??;
        if manifest.target != target {
            return Err(RuntimeFailure::internal(
                "Runtime 清单 target 与当前平台不匹配",
            ));
        }
        if self.archive_source == CandidateArchiveSource::Download
            && let Some(endpoint) = manifest_endpoint()?
        {
            runtime_source_policy(endpoint)?.validate_redirect(&manifest.url)?;
        }
        Ok(manifest)
    }

    pub async fn verify_bundled_archive(
        &self,
        manifest: &RuntimeManifest,
    ) -> Result<PathBuf, RuntimeFailure> {
        if self.archive_source != CandidateArchiveSource::Bundled {
            return Err(RuntimeFailure::internal(
                "只有捆绑 Runtime 更新器可以校验内置归档",
            ));
        }
        let archive = self
            .paths
            .bundled_runtime
            .join(bundled_archive_name(manifest.target, manifest.archive));
        let size = tokio::fs::metadata(&archive)
            .await
            .map_err(|cause| {
                RuntimeFailure::new(
                    RuntimeFailureCode::Archive,
                    format!("找不到捆绑 Runtime：{cause}"),
                )
            })?
            .len();
        if size != manifest.size {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Archive,
                format!("捆绑 Runtime 大小不匹配：{size}/{}", manifest.size),
            ));
        }
        verify_file(&archive, manifest).await?;
        Ok(archive)
    }

    pub fn local_decision(
        &self,
        requirement: &RuntimeRequirement,
    ) -> Result<LocalRuntimeDecision, RuntimeFailure> {
        let target = RuntimeTarget::current()?;
        let installed = self.read_verified_active(target)?;
        let decision = decide_local(
            requirement,
            installed.as_ref().map(|(manifest, _)| manifest),
            true,
        );
        if let (LocalRuntimeDecision::FastStart(manifest), Some((_, bytes))) =
            (&decision, installed.as_ref())
        {
            self.ensure_provisioning_receipt(manifest, bytes, target)?;
        }
        Ok(decision)
    }

    pub fn local_provisioned_decision(&self) -> Result<LocalRuntimeDecision, RuntimeFailure> {
        let target = RuntimeTarget::current()?;
        let Some((manifest, bytes)) = self.read_verified_active(target)? else {
            return Ok(LocalRuntimeDecision::UpgradeRequired);
        };
        let store = ProvisioningReceiptStore::new(self.paths.root.join("state"));
        let Some(receipt) = store.read_final().map_err(repair_required)? else {
            return Ok(LocalRuntimeDecision::UpgradeRequired);
        };
        let desktop_version =
            Version::parse(env!("CARGO_PKG_VERSION")).map_err(RuntimeFailure::internal)?;
        if receipt.desktop_version != desktop_version || receipt.target != target {
            return Ok(LocalRuntimeDecision::UpgradeRequired);
        }
        validate_receipt(&self.paths, &receipt, &manifest, &bytes)?;
        Ok(LocalRuntimeDecision::FastStart(manifest))
    }

    pub fn prepare_local_candidate(
        &self,
        session: &ProvisioningSession,
    ) -> Result<Option<PreparedProvisioning>, RuntimeFailure> {
        let store = ProvisioningReceiptStore::new(self.paths.root.join("state"));
        let Some(receipt) = store.read_final().ok().flatten() else {
            return Ok(None);
        };
        if receipt.desktop_version != session.desktop_version || receipt.target != session.target {
            return Ok(None);
        }
        let Some((manifest, bytes)) = self.read_verified_active(session.target).ok().flatten()
        else {
            return Ok(None);
        };
        if validate_receipt(&self.paths, &receipt, &manifest, &bytes).is_err() {
            return Ok(None);
        }
        Ok(Some(PreparedProvisioning {
            schema_version: 1,
            session_id: session.id,
            desktop_version: session.desktop_version.clone(),
            target: session.target,
            runtime_version: manifest.version,
            manifest_sha256: sha256(&bytes),
            payload_sha256: manifest.sha256,
            candidate_dir: receipt.active_dir,
            reused_active: true,
            probe_contract_version: receipt.probe_contract_version,
            prepared_at: Utc::now(),
        }))
    }

    pub async fn prepare_required(
        &self,
        generation_id: &str,
        cancellation: &CancellationToken,
    ) -> Result<PreparedRuntime, RuntimeFailure> {
        let manifest = self.required_manifest().await?;
        self.prepare_manifest_with_progress(generation_id, manifest, cancellation, Arc::new(|_| {}))
            .await
    }

    pub async fn prepare_manifest_with_progress(
        &self,
        generation_id: &str,
        manifest: RuntimeManifest,
        cancellation: &CancellationToken,
        progress: Arc<dyn Fn(RuntimePreparationProgress) + Send + Sync>,
    ) -> Result<PreparedRuntime, RuntimeFailure> {
        if cancellation.is_cancelled() {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Cancelled,
                "Runtime 准备已取消",
            ));
        }
        let (archive, remove_archive_after_prepare, source) = match self.archive_source {
            CandidateArchiveSource::Download => {
                let archive = archive_path(&self.paths, &manifest);
                progress(RuntimePreparationProgress {
                    phase: RuntimePhase::Downloading,
                    completed: 0,
                    total: Some(manifest.size),
                    message: "正在下载运行组件".into(),
                });
                let download_progress = Arc::clone(&progress);
                download_runtime(
                    &self.client,
                    &manifest,
                    &archive,
                    cancellation,
                    move |completed, total| {
                        download_progress(RuntimePreparationProgress {
                            phase: RuntimePhase::Downloading,
                            completed,
                            total: Some(total),
                            message: "正在下载运行组件".into(),
                        });
                    },
                )
                .await?;
                (archive, true, RuntimeSourceKind::Online)
            }
            CandidateArchiveSource::Bundled => (
                self.verify_bundled_archive(&manifest).await?,
                false,
                RuntimeSourceKind::Bundled,
            ),
        };
        let paths = self.paths.clone();
        let archive_for_stage = archive.clone();
        let manifest_for_stage = manifest.clone();
        let generation_id = generation_id.to_string();
        let extraction_progress = Arc::clone(&progress);
        let receipt = tokio::task::spawn_blocking(move || {
            activation::stage_with_progress(
                &paths,
                &archive_for_stage,
                &manifest_for_stage,
                &generation_id,
                &move |completed, total| {
                    let percent = if total == 0 {
                        100
                    } else {
                        completed.saturating_mul(100) / total
                    };
                    extraction_progress(RuntimePreparationProgress {
                        phase: RuntimePhase::Extracting,
                        completed,
                        total: Some(total),
                        message: format!("正在解压内置组件 {}%", percent.min(100)),
                    });
                },
            )
        })
        .await
        .map_err(RuntimeFailure::internal);
        let receipt = match receipt {
            Ok(Ok(receipt)) => receipt,
            Ok(Err(cause)) => {
                if remove_archive_after_prepare {
                    let _ = std::fs::remove_file(&archive);
                }
                return Err(cause);
            }
            Err(cause) => {
                if remove_archive_after_prepare {
                    let _ = std::fs::remove_file(&archive);
                }
                return Err(cause);
            }
        };
        progress(RuntimePreparationProgress {
            phase: RuntimePhase::Verifying,
            completed: manifest.size,
            total: Some(manifest.size),
            message: "正在验证组件".into(),
        });
        Ok(PreparedRuntime {
            manifest,
            receipt: Some(receipt),
            archive: remove_archive_after_prepare.then_some(archive),
            source,
        })
    }

    pub async fn prepare_candidate(
        &self,
        session: &ProvisioningSession,
        cancellation: &CancellationToken,
    ) -> Result<PreparedProvisioning, RuntimeFailure> {
        self.prepare_candidate_with_progress(session, cancellation, &|_| {})
            .await
    }

    pub async fn prepare_candidate_with_progress(
        &self,
        session: &ProvisioningSession,
        cancellation: &CancellationToken,
        progress: &(dyn Fn(ProvisioningEvent) + Send + Sync),
    ) -> Result<PreparedProvisioning, RuntimeFailure> {
        if cancellation.is_cancelled() {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Cancelled,
                "Runtime provisioning 已取消",
            ));
        }
        progress(provisioning_event(
            session.id,
            ProvisioningPhase::FetchingManifest,
            "正在获取安装信息",
            None,
            None,
            None,
        ));
        let manifest =
            tokio::time::timeout(Duration::from_secs(30), self.source.fetch(session.target))
                .await
                .map_err(|_| {
                    RuntimeFailure::new(RuntimeFailureCode::Network, "获取运行时清单超时")
                })??;
        if manifest.target != session.target {
            return Err(RuntimeFailure::internal(
                "Runtime 清单 target 与安装会话不匹配",
            ));
        }
        if let Some(endpoint) = manifest_endpoint()? {
            runtime_source_policy(endpoint)?.validate_redirect(&manifest.url)?;
        }

        let downloads = self.paths.root.join("runtime/provisioning/downloads");
        let candidates = self.paths.root.join("runtime/provisioning/candidates");
        std::fs::create_dir_all(&downloads).map_err(RuntimeFailure::internal)?;
        std::fs::create_dir_all(&candidates).map_err(RuntimeFailure::internal)?;
        let download_started = std::time::Instant::now();
        progress(provisioning_event(
            session.id,
            ProvisioningPhase::Downloading,
            "正在下载运行组件",
            Some(0),
            Some(manifest.size),
            None,
        ));
        let (archive, remove_archive_after_prepare) = match self.archive_source {
            CandidateArchiveSource::Download => {
                let archive = provisioning_archive_path(&downloads, session, &manifest);
                download_runtime(
                    &self.client,
                    &manifest,
                    &archive,
                    cancellation,
                    |completed, total| {
                        let elapsed = download_started.elapsed().as_secs_f64().max(0.001);
                        progress(provisioning_event(
                            session.id,
                            ProvisioningPhase::Downloading,
                            "正在下载运行组件",
                            Some(completed),
                            Some(total),
                            Some((completed as f64 / elapsed) as u64),
                        ));
                    },
                )
                .await?;
                (archive, true)
            }
            CandidateArchiveSource::Bundled => {
                let archive = self
                    .paths
                    .bundled_runtime
                    .join(bundled_archive_name(manifest.target, manifest.archive));
                let size = tokio::fs::metadata(&archive)
                    .await
                    .map_err(|cause| {
                        RuntimeFailure::new(
                            RuntimeFailureCode::Archive,
                            format!("找不到捆绑 Runtime：{cause}"),
                        )
                    })?
                    .len();
                if size != manifest.size {
                    return Err(RuntimeFailure::new(
                        RuntimeFailureCode::Archive,
                        format!("捆绑 Runtime 大小不匹配：{size}/{}", manifest.size),
                    ));
                }
                verify_file(&archive, &manifest).await?;
                progress(provisioning_event(
                    session.id,
                    ProvisioningPhase::Downloading,
                    "正在读取内置运行组件",
                    Some(manifest.size),
                    Some(manifest.size),
                    None,
                ));
                (archive, false)
            }
        };
        progress(provisioning_event(
            session.id,
            ProvisioningPhase::Verifying,
            "正在校验运行组件",
            Some(manifest.size),
            Some(manifest.size),
            None,
        ));

        let candidate = candidates.join(session.id.to_string());
        let manifest_bytes =
            serde_json::to_vec_pretty(&manifest).map_err(RuntimeFailure::internal)?;
        let archive_for_extract = archive.clone();
        let candidate_for_extract = candidate.clone();
        let manifest_for_write = manifest_bytes.clone();
        let archive_kind = manifest.archive;
        progress(provisioning_event(
            session.id,
            ProvisioningPhase::Extracting,
            "正在安装运行组件",
            None,
            None,
            None,
        ));
        let prepared = tokio::task::spawn_blocking(move || {
            if candidate_for_extract.exists() {
                std::fs::remove_dir_all(&candidate_for_extract)
                    .map_err(RuntimeFailure::internal)?;
            }
            std::fs::create_dir_all(&candidate_for_extract).map_err(RuntimeFailure::internal)?;
            if let Err(cause) = super::archive::extract_archive(
                &archive_for_extract,
                &candidate_for_extract,
                archive_kind,
            ) {
                let _ = std::fs::remove_dir_all(&candidate_for_extract);
                return Err(cause);
            }
            std::fs::write(
                candidate_for_extract.join("manifest.json"),
                manifest_for_write,
            )
            .map_err(RuntimeFailure::internal)
        })
        .await
        .map_err(RuntimeFailure::internal)?;
        if remove_archive_after_prepare {
            let _ = std::fs::remove_file(&archive);
        }
        if let Err(cause) = prepared {
            return Err(cause);
        }

        Ok(PreparedProvisioning {
            schema_version: 1,
            session_id: session.id,
            desktop_version: session.desktop_version.clone(),
            target: session.target,
            runtime_version: manifest.version,
            manifest_sha256: sha256(&manifest_bytes),
            payload_sha256: manifest.sha256,
            candidate_dir: candidate,
            reused_active: false,
            probe_contract_version: 0,
            prepared_at: Utc::now(),
        })
    }

    pub fn activate_candidate(
        &self,
        prepared: &PreparedProvisioning,
    ) -> Result<ActivatedProvisioningCandidate, RuntimeFailure> {
        if prepared.reused_active {
            self.validate_reused_candidate(prepared)?;
            return Ok(ActivatedProvisioningCandidate {
                active_dir: prepared.candidate_dir.clone(),
                paths: self.paths.clone(),
                receipt: None,
            });
        }
        let expected_candidate = self
            .paths
            .root
            .join("runtime/provisioning/candidates")
            .join(prepared.session_id.to_string());
        validate_exact_candidate(&expected_candidate, &prepared.candidate_dir)?;
        let manifest_bytes = std::fs::read(prepared.candidate_dir.join("manifest.json"))
            .map_err(RuntimeFailure::internal)?;
        if sha256(&manifest_bytes) != prepared.manifest_sha256 {
            return Err(RuntimeFailure::internal(
                "Runtime candidate manifest hash 不匹配",
            ));
        }
        let manifest =
            parse_and_verify_manifest(&manifest_bytes, prepared.target, release_public_key())?;
        if manifest.version != prepared.runtime_version
            || manifest.sha256 != prepared.payload_sha256
        {
            return Err(RuntimeFailure::internal(
                "Runtime candidate receipt 与清单不匹配",
            ));
        }
        let receipt = activation::stage_candidate(
            &self.paths,
            &prepared.candidate_dir,
            &manifest,
            &prepared.session_id.to_string(),
        )?;
        if let Err(cause) = activation::activate(&self.paths, &receipt) {
            let _ = activation::rollback(&self.paths, receipt);
            return Err(cause);
        }
        Ok(ActivatedProvisioningCandidate {
            active_dir: self.paths.version_dir(&manifest.version),
            paths: self.paths.clone(),
            receipt: Some(receipt),
        })
    }

    pub fn discard_candidate(&self, prepared: &PreparedProvisioning) -> Result<(), RuntimeFailure> {
        if prepared.reused_active {
            return Ok(());
        }
        let expected = self
            .paths
            .root
            .join("runtime/provisioning/candidates")
            .join(prepared.session_id.to_string());
        validate_exact_candidate(&expected, &prepared.candidate_dir)?;
        std::fs::remove_dir_all(&prepared.candidate_dir).map_err(RuntimeFailure::internal)
    }

    pub fn commit(&self, prepared: PreparedRuntime) -> Result<RuntimeManifest, RuntimeFailure> {
        if let Err(cause) = self.activate_prepared(&prepared) {
            let _ = self.rollback(prepared);
            return Err(cause);
        }
        self.finalize(prepared)
    }

    pub fn activate_prepared(&self, prepared: &PreparedRuntime) -> Result<(), RuntimeFailure> {
        if let Some(receipt) = prepared.receipt.as_ref() {
            activation::activate(&self.paths, receipt)?;
        }
        Ok(())
    }

    pub fn finalize(
        &self,
        mut prepared: PreparedRuntime,
    ) -> Result<RuntimeManifest, RuntimeFailure> {
        if let Err(cause) = self.write_active_provisioning_receipt(&prepared.manifest) {
            if let Some(receipt) = prepared.receipt.take() {
                let _ = activation::rollback(&self.paths, receipt);
            }
            if let Some(archive) = prepared.archive.take() {
                let _ = std::fs::remove_file(archive);
            }
            return Err(cause);
        }
        if let Some(receipt) = prepared.receipt.take() {
            activation::commit(receipt)?;
        }
        if let Some(archive) = prepared.archive.take() {
            let _ = std::fs::remove_file(archive);
        }
        Ok(prepared.manifest)
    }

    pub fn rollback(&self, mut prepared: PreparedRuntime) -> Result<(), RuntimeFailure> {
        if let Some(receipt) = prepared.receipt.take() {
            activation::rollback(&self.paths, receipt)?;
        }
        if let Some(archive) = prepared.archive.take() {
            let _ = std::fs::remove_file(archive);
        }
        Ok(())
    }

    pub async fn check_compatible_update(&self) -> Result<Option<RuntimeManifest>, RuntimeFailure> {
        let target = RuntimeTarget::current()?;
        let candidate = self.source.fetch(target).await?;
        let Some(current) = read_active_manifest(&self.paths)? else {
            return Ok(None);
        };
        let requirement = RuntimeRequirement::from_bundled_manifest(&current);
        if candidate.version > current.version && requirement.dsh.matches(&candidate.dsh_version) {
            Ok(Some(candidate))
        } else {
            Ok(None)
        }
    }

    pub fn bundled_requirement(
        &self,
        target: RuntimeTarget,
    ) -> Result<RuntimeRequirement, RuntimeFailure> {
        let path = self
            .paths
            .bundled_runtime
            .join("manifests")
            .join(format!("runtime-{}.json", target.as_str()));
        let bytes = std::fs::read(path).map_err(RuntimeFailure::internal)?;
        let manifest = parse_and_verify_manifest(&bytes, target, release_public_key())?;
        Ok(RuntimeRequirement::from_bundled_manifest(&manifest))
    }

    fn read_verified_active(
        &self,
        target: RuntimeTarget,
    ) -> Result<Option<(RuntimeManifest, Vec<u8>)>, RuntimeFailure> {
        let Some(current) = read_current(&self.paths).map_err(repair_required)? else {
            return Ok(None);
        };
        let bytes = std::fs::read(
            self.paths
                .version_dir(&current.version)
                .join("manifest.json"),
        )
        .map_err(repair_required)?;
        let manifest = parse_and_verify_manifest(&bytes, target, release_public_key())
            .map_err(repair_required)?;
        if manifest.version != current.version {
            return Err(repair_required("Runtime pointer 与清单版本不匹配"));
        }
        Ok(Some((manifest, bytes)))
    }

    fn ensure_provisioning_receipt(
        &self,
        manifest: &RuntimeManifest,
        manifest_bytes: &[u8],
        target: RuntimeTarget,
    ) -> Result<(), RuntimeFailure> {
        let store = ProvisioningReceiptStore::new(self.paths.root.join("state"));
        match store.read_final().map_err(repair_required)? {
            Some(receipt) => validate_receipt(&self.paths, &receipt, manifest, manifest_bytes),
            None => {
                let active_dir = self.paths.version_dir(&manifest.version);
                let prepared = PreparedProvisioning {
                    schema_version: 1,
                    session_id: uuid::Uuid::new_v4(),
                    desktop_version: semver::Version::parse(env!("CARGO_PKG_VERSION"))
                        .map_err(RuntimeFailure::internal)?,
                    target,
                    runtime_version: manifest.version.clone(),
                    manifest_sha256: sha256(manifest_bytes),
                    payload_sha256: manifest.sha256.clone(),
                    candidate_dir: active_dir.clone(),
                    reused_active: true,
                    probe_contract_version: 1,
                    prepared_at: Utc::now(),
                };
                store.finalize(&prepared, active_dir)?;
                Ok(())
            }
        }
    }

    fn write_active_provisioning_receipt(
        &self,
        manifest: &RuntimeManifest,
    ) -> Result<(), RuntimeFailure> {
        let active_dir = self.paths.version_dir(&manifest.version);
        let manifest_bytes =
            std::fs::read(active_dir.join("manifest.json")).map_err(RuntimeFailure::internal)?;
        let prepared = PreparedProvisioning {
            schema_version: 1,
            session_id: uuid::Uuid::new_v4(),
            desktop_version: semver::Version::parse(env!("CARGO_PKG_VERSION"))
                .map_err(RuntimeFailure::internal)?,
            target: manifest.target,
            runtime_version: manifest.version.clone(),
            manifest_sha256: sha256(&manifest_bytes),
            payload_sha256: manifest.sha256.clone(),
            candidate_dir: active_dir.clone(),
            reused_active: true,
            probe_contract_version: 1,
            prepared_at: Utc::now(),
        };
        ProvisioningReceiptStore::new(self.paths.root.join("state"))
            .finalize(&prepared, active_dir)?;
        Ok(())
    }

    fn validate_reused_candidate(
        &self,
        prepared: &PreparedProvisioning,
    ) -> Result<(), RuntimeFailure> {
        let expected = self.paths.version_dir(&prepared.runtime_version);
        validate_exact_candidate(&expected, &prepared.candidate_dir)?;
        let Some((manifest, bytes)) = self.read_verified_active(prepared.target)? else {
            return Err(repair_required("找不到已安装的 Runtime"));
        };
        if manifest.version != prepared.runtime_version
            || manifest.sha256 != prepared.payload_sha256
            || sha256(&bytes) != prepared.manifest_sha256
        {
            return Err(repair_required(
                "已安装 Runtime 与 provisioning receipt 不匹配",
            ));
        }
        Ok(())
    }
}

impl ActivatedProvisioningCandidate {
    pub fn commit(mut self) -> Result<(), RuntimeFailure> {
        if let Some(receipt) = self.receipt.take() {
            activation::commit(receipt)?;
        }
        Ok(())
    }

    pub fn rollback(mut self) -> Result<(), RuntimeFailure> {
        if let Some(receipt) = self.receipt.take() {
            activation::rollback(&self.paths, receipt)?;
        }
        Ok(())
    }
}

struct ReleaseManifestSource {
    paths: RuntimePaths,
    client: reqwest::Client,
}

struct BundledManifestSource {
    paths: RuntimePaths,
}

impl RuntimeManifestSource for BundledManifestSource {
    fn fetch<'a>(
        &'a self,
        target: RuntimeTarget,
    ) -> Pin<Box<dyn Future<Output = Result<RuntimeManifest, RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move {
            let path = self
                .paths
                .bundled_runtime
                .join("manifests")
                .join(format!("runtime-{}.json", target.as_str()));
            let bytes = tokio::fs::read(path).await.map_err(|cause| {
                RuntimeFailure::new(
                    RuntimeFailureCode::Archive,
                    format!("找不到捆绑运行时清单：{cause}"),
                )
            })?;
            parse_and_verify_manifest(&bytes, target, release_public_key())
        })
    }
}

impl RuntimeManifestSource for ReleaseManifestSource {
    fn fetch<'a>(
        &'a self,
        target: RuntimeTarget,
    ) -> Pin<Box<dyn Future<Output = Result<RuntimeManifest, RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move {
            let bytes = if let Some(endpoint) = manifest_endpoint()? {
                let url = endpoint.as_str().replace("{target}", target.as_str());
                let parsed = url::Url::parse(&url).map_err(RuntimeFailure::internal)?;
                runtime_source_policy(parsed.clone())?;
                self.client
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
                    .map_err(|cause| {
                        RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string())
                    })?
                    .to_vec()
            } else {
                let path = self
                    .paths
                    .bundled_runtime
                    .join("manifests")
                    .join(format!("runtime-{}.json", target.as_str()));
                tokio::fs::read(path).await.map_err(|cause| {
                    RuntimeFailure::new(
                        RuntimeFailureCode::Network,
                        format!("找不到捆绑运行时清单：{cause}"),
                    )
                })?
            };
            parse_and_verify_manifest(&bytes, target, release_public_key())
        })
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

fn provisioning_archive_path(
    downloads: &Path,
    session: &ProvisioningSession,
    manifest: &RuntimeManifest,
) -> PathBuf {
    let extension = match manifest.archive {
        ArchiveKind::Zip => "zip",
        ArchiveKind::TarGz => "tar.gz",
    };
    downloads.join(format!("{}.{extension}.part", session.id))
}

fn bundled_archive_name(target: RuntimeTarget, archive: ArchiveKind) -> String {
    let extension = match archive {
        ArchiveKind::Zip => "zip",
        ArchiveKind::TarGz => "tar.gz",
    };
    format!("dsh-runtime-{}.{extension}", target.as_str())
}

fn validate_exact_candidate(expected: &Path, actual: &Path) -> Result<(), RuntimeFailure> {
    let expected = expected.canonicalize().map_err(RuntimeFailure::internal)?;
    let actual = actual.canonicalize().map_err(RuntimeFailure::internal)?;
    if expected != actual {
        return Err(RuntimeFailure::internal(
            "Runtime candidate 路径与 provisioning session 不匹配",
        ));
    }
    Ok(())
}

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn validate_receipt(
    paths: &RuntimePaths,
    receipt: &crate::provisioning::model::ProvisioningReceipt,
    manifest: &RuntimeManifest,
    manifest_bytes: &[u8],
) -> Result<(), RuntimeFailure> {
    let expected_dir = paths
        .version_dir(&manifest.version)
        .canonicalize()
        .map_err(repair_required)?;
    let receipt_dir = receipt.active_dir.canonicalize().map_err(repair_required)?;
    if receipt.target != manifest.target
        || receipt.runtime_version != manifest.version
        || receipt.payload_sha256 != manifest.sha256
        || receipt.manifest_sha256 != sha256(manifest_bytes)
        || receipt.probe_contract_version == 0
        || !receipt.compatibility_requirement.matches(&manifest.version)
        || receipt_dir != expected_dir
    {
        return Err(repair_required(
            "Runtime provisioning receipt 与本地安装不匹配",
        ));
    }
    Ok(())
}

fn repair_required(cause: impl std::fmt::Display) -> RuntimeFailure {
    RuntimeFailure::new(
        RuntimeFailureCode::RepairRequired,
        format!("本地 Runtime 安装记录需要修复：{cause}"),
    )
}

fn provisioning_event(
    session_id: uuid::Uuid,
    phase: ProvisioningPhase,
    message: &str,
    completed: Option<u64>,
    total: Option<u64>,
    bytes_per_second: Option<u64>,
) -> ProvisioningEvent {
    ProvisioningEvent {
        session_id,
        phase,
        message: message.into(),
        recoverable: true,
        completed,
        total,
        bytes_per_second,
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
    use tokio_util::sync::CancellationToken;

    use super::{RuntimeManifestSource, RuntimeUpdater};
    use crate::{
        provisioning::{model::ProvisioningSession, receipt::ProvisioningReceiptStore},
        runtime::{
            activation::{read_active_manifest, read_current},
            compatibility::{LocalRuntimeDecision, RuntimeRequirement},
            manifest::canonical_payload,
            model::{
                ArchiveKind, CurrentRuntime, RuntimeFailure, RuntimeFailureCode, RuntimeManifest,
                RuntimeTarget,
            },
            paths::RuntimePaths,
        },
    };

    #[derive(Default)]
    struct FakeManifestSource {
        next: Mutex<Option<RuntimeManifest>>,
        fetches: AtomicUsize,
    }

    impl FakeManifestSource {
        fn serve(&self, manifest: RuntimeManifest) {
            *self.next.lock().unwrap() = Some(manifest);
        }
    }

    impl RuntimeManifestSource for FakeManifestSource {
        fn fetch<'a>(
            &'a self,
            _target: RuntimeTarget,
        ) -> Pin<Box<dyn Future<Output = Result<RuntimeManifest, RuntimeFailure>> + Send + 'a>>
        {
            self.fetches.fetch_add(1, Ordering::SeqCst);
            let result = self
                .next
                .lock()
                .unwrap()
                .take()
                .ok_or_else(|| RuntimeFailure::internal("fake manifest not configured"));
            Box::pin(async move { result })
        }
    }

    struct UpdaterFixture {
        _temporary: tempfile::TempDir,
        paths: RuntimePaths,
        updater: RuntimeUpdater,
        manifest_source: Arc<FakeManifestSource>,
    }

    impl UpdaterFixture {
        async fn with_current(version: &str) -> Self {
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
            let version = Version::parse(version).unwrap();
            let current_manifest =
                manifest_for_archive(temporary.path(), version.to_string().as_str(), b"current");
            let current_dir = paths.version_dir(&version);
            std::fs::create_dir_all(&current_dir).unwrap();
            std::fs::write(
                current_dir.join("manifest.json"),
                serde_json::to_vec_pretty(&current_manifest).unwrap(),
            )
            .unwrap();
            std::fs::write(
                &paths.current,
                serde_json::to_vec_pretty(&CurrentRuntime {
                    version,
                    previous_version: None,
                })
                .unwrap(),
            )
            .unwrap();
            let manifest_source = Arc::new(FakeManifestSource::default());
            let updater = RuntimeUpdater::with_source(
                paths.clone(),
                reqwest::Client::new(),
                manifest_source.clone(),
            );
            Self {
                _temporary: temporary,
                paths,
                updater,
                manifest_source,
            }
        }

        fn manifest(&self, version: &str) -> RuntimeManifest {
            manifest_for_archive(self._temporary.path(), version, b"candidate")
        }

        fn current_version(&self) -> Version {
            read_current(&self.paths).unwrap().unwrap().version
        }
    }

    fn manifest_for_archive(root: &std::path::Path, version: &str, body: &[u8]) -> RuntimeManifest {
        let archive_path = root.join(format!("runtime-{version}.tar.gz"));
        let encoder = GzEncoder::new(Vec::new(), Compression::default());
        let mut archive = tar::Builder::new(encoder);
        let mut header = tar::Header::new_gnu();
        header.set_size(body.len() as u64);
        header.set_mode(0o644);
        header.set_cksum();
        archive
            .append_data(&mut header, "app/runtime.txt", body)
            .unwrap();
        let encoder = archive.into_inner().unwrap();
        let bytes = encoder.finish().unwrap();
        std::fs::File::create(&archive_path)
            .unwrap()
            .write_all(&bytes)
            .unwrap();
        sign_manifest(RuntimeManifest {
            schema_version: 1,
            version: Version::parse(version).unwrap(),
            dsh_version: Version::parse("0.1.0-rc.7").unwrap(),
            target: RuntimeTarget::WindowsX86_64,
            url: url::Url::from_file_path(&archive_path).unwrap(),
            size: bytes.len() as u64,
            sha256: hex::encode(Sha256::digest(&bytes)),
            signature: String::new(),
            archive: ArchiveKind::TarGz,
            entrypoint: "app/runtime.txt".to_string(),
            args: Vec::new(),
            health_path: "/health".to_string(),
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

    fn requirement(version: &str) -> RuntimeRequirement {
        RuntimeRequirement {
            minimum_runtime: Version::parse(version).unwrap(),
            dsh: semver::VersionReq::parse("^0.1.0-rc.7").unwrap(),
        }
    }

    fn write_bundled_runtime(fixture: &UpdaterFixture, manifest: &RuntimeManifest) {
        let manifests = fixture.paths.bundled_runtime.join("manifests");
        std::fs::create_dir_all(&manifests).unwrap();
        std::fs::copy(
            manifest.url.to_file_path().unwrap(),
            fixture
                .paths
                .bundled_runtime
                .join("dsh-runtime-windows-x86_64.tar.gz"),
        )
        .unwrap();
        std::fs::write(
            manifests.join("runtime-windows-x86_64.json"),
            serde_json::to_vec_pretty(manifest).unwrap(),
        )
        .unwrap();
    }

    #[tokio::test]
    async fn bundled_candidate_uses_packaged_archive_without_network() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        let manifest = fixture.manifest("1.1.0");
        write_bundled_runtime(&fixture, &manifest);
        let updater =
            RuntimeUpdater::new_bundled(fixture.paths.clone(), reqwest::Client::new()).unwrap();
        let session = ProvisioningSession {
            id: uuid::Uuid::new_v4(),
            desktop_version: Version::new(0, 1, 0),
            target: RuntimeTarget::WindowsX86_64,
            started_at: chrono::Utc::now(),
        };

        let prepared = updater
            .prepare_candidate(&session, &CancellationToken::new())
            .await
            .unwrap();

        assert!(prepared.candidate_dir.join("app/runtime.txt").is_file());
        assert!(
            fixture
                .paths
                .bundled_runtime
                .join("dsh-runtime-windows-x86_64.tar.gz")
                .is_file()
        );
        assert_eq!(
            std::fs::read_dir(&fixture.paths.downloads).unwrap().count(),
            0
        );
        assert_eq!(fixture.manifest_source.fetches.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn bundled_candidate_rejects_hash_mismatch_and_preserves_source() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        let manifest = fixture.manifest("1.1.0");
        write_bundled_runtime(&fixture, &manifest);
        let bundled_archive = fixture
            .paths
            .bundled_runtime
            .join("dsh-runtime-windows-x86_64.tar.gz");
        std::fs::write(&bundled_archive, vec![0_u8; manifest.size as usize]).unwrap();
        let updater =
            RuntimeUpdater::new_bundled(fixture.paths.clone(), reqwest::Client::new()).unwrap();
        let session = ProvisioningSession {
            id: uuid::Uuid::new_v4(),
            desktop_version: Version::new(0, 1, 0),
            target: RuntimeTarget::WindowsX86_64,
            started_at: chrono::Utc::now(),
        };

        let failure = updater
            .prepare_candidate(&session, &CancellationToken::new())
            .await
            .unwrap_err();

        assert_eq!(failure.code, RuntimeFailureCode::Signature);
        assert!(bundled_archive.is_file());
        assert_eq!(fixture.manifest_source.fetches.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn activation_pointer_commits_only_after_readiness() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        fixture.manifest_source.serve(fixture.manifest("1.1.0"));
        let prepared = fixture
            .updater
            .prepare_required("g-2", &CancellationToken::new())
            .await
            .unwrap();
        assert_eq!(fixture.current_version(), Version::new(1, 0, 0));
        fixture.updater.commit(prepared).unwrap();
        assert_eq!(fixture.current_version(), Version::new(1, 1, 0));
        assert_eq!(
            read_active_manifest(&fixture.paths)
                .unwrap()
                .unwrap()
                .version,
            Version::new(1, 1, 0)
        );
    }

    #[tokio::test]
    async fn rollback_restores_the_previous_runtime() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        fixture.manifest_source.serve(fixture.manifest("1.1.0"));
        let prepared = fixture
            .updater
            .prepare_required("g-rollback", &CancellationToken::new())
            .await
            .unwrap();
        fixture.updater.rollback(prepared).unwrap();
        assert_eq!(fixture.current_version(), Version::new(1, 0, 0));
        assert!(!fixture.paths.version_dir(&Version::new(1, 1, 0)).exists());
    }

    #[tokio::test]
    async fn rollback_after_pointer_activation_restores_the_previous_runtime() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        fixture.manifest_source.serve(fixture.manifest("1.1.0"));
        let prepared = fixture
            .updater
            .prepare_required("g-profile-commit", &CancellationToken::new())
            .await
            .unwrap();
        fixture.updater.activate_prepared(&prepared).unwrap();
        assert_eq!(fixture.current_version(), Version::new(1, 1, 0));
        fixture.updater.rollback(prepared).unwrap();
        assert_eq!(fixture.current_version(), Version::new(1, 0, 0));
    }

    #[tokio::test]
    async fn compatible_background_check_never_changes_the_pointer() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        fixture.manifest_source.serve(fixture.manifest("1.1.0"));
        let candidate = fixture
            .updater
            .check_compatible_update()
            .await
            .unwrap()
            .unwrap();
        assert_eq!(candidate.version, Version::new(1, 1, 0));
        assert_eq!(fixture.current_version(), Version::new(1, 0, 0));
    }

    #[tokio::test]
    async fn provisioning_candidate_is_reversible_until_commit() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        fixture.manifest_source.serve(fixture.manifest("1.1.0"));
        let session = ProvisioningSession {
            id: uuid::Uuid::new_v4(),
            desktop_version: Version::new(0, 1, 0),
            target: RuntimeTarget::WindowsX86_64,
            started_at: chrono::Utc::now(),
        };
        let prepared = fixture
            .updater
            .prepare_candidate(&session, &CancellationToken::new())
            .await
            .unwrap();

        assert!(prepared.candidate_dir.join("app/runtime.txt").is_file());
        assert_eq!(fixture.current_version(), Version::new(1, 0, 0));
        let activation = fixture.updater.activate_candidate(&prepared).unwrap();
        assert_eq!(fixture.current_version(), Version::new(1, 1, 0));
        activation.rollback().unwrap();
        assert_eq!(fixture.current_version(), Version::new(1, 0, 0));
        assert!(prepared.candidate_dir.is_dir());

        fixture
            .updater
            .activate_candidate(&prepared)
            .unwrap()
            .commit()
            .unwrap();
        assert_eq!(fixture.current_version(), Version::new(1, 1, 0));
        assert!(!prepared.candidate_dir.exists());
    }

    #[tokio::test]
    async fn compatible_receipt_and_reinstall_stay_network_free() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        let decision = fixture
            .updater
            .local_decision(&requirement("1.0.0"))
            .unwrap();
        assert!(matches!(decision, LocalRuntimeDecision::FastStart(_)));
        assert!(matches!(
            fixture.updater.local_provisioned_decision().unwrap(),
            LocalRuntimeDecision::FastStart(_)
        ));
        let receipt = ProvisioningReceiptStore::new(fixture.paths.root.join("state"))
            .read_final()
            .unwrap()
            .unwrap();
        let session = ProvisioningSession {
            id: uuid::Uuid::new_v4(),
            desktop_version: receipt.desktop_version,
            target: RuntimeTarget::WindowsX86_64,
            started_at: chrono::Utc::now(),
        };
        let prepared = fixture
            .updater
            .prepare_local_candidate(&session)
            .unwrap()
            .unwrap();
        assert!(prepared.reused_active);
        assert_eq!(
            prepared.candidate_dir.canonicalize().unwrap(),
            fixture
                .paths
                .version_dir(&Version::new(1, 0, 0))
                .canonicalize()
                .unwrap()
        );
        assert_eq!(fixture.manifest_source.fetches.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn corrupt_receipt_requires_repair_without_fetching() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        fixture
            .updater
            .local_decision(&requirement("1.0.0"))
            .unwrap();
        std::fs::write(fixture.paths.root.join("state/provisioning.json"), b"{").unwrap();
        let failure = fixture.updater.local_provisioned_decision().unwrap_err();
        assert_eq!(failure.code, RuntimeFailureCode::RepairRequired);
        assert_eq!(fixture.manifest_source.fetches.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn fresh_activation_replaces_a_corrupt_receipt() {
        let fixture = UpdaterFixture::with_current("1.0.0").await;
        fixture
            .updater
            .local_decision(&requirement("1.0.0"))
            .unwrap();
        std::fs::write(fixture.paths.root.join("state/provisioning.json"), b"{").unwrap();
        fixture.manifest_source.serve(fixture.manifest("1.1.0"));
        let prepared = fixture
            .updater
            .prepare_required("repair-corrupt-receipt", &CancellationToken::new())
            .await
            .unwrap();
        fixture.updater.commit(prepared).unwrap();

        assert!(matches!(
            fixture
                .updater
                .local_decision(&requirement("1.1.0"))
                .unwrap(),
            LocalRuntimeDecision::FastStart(_)
        ));
        assert_eq!(
            ProvisioningReceiptStore::new(fixture.paths.root.join("state"))
                .read_final()
                .unwrap()
                .unwrap()
                .runtime_version,
            Version::new(1, 1, 0)
        );
    }
}
