use std::{sync::Arc, time::Duration};

use crate::{
    provisioning::{
        coordinator::{
            ProvisioningCoordinator, ProvisioningEventSink, RuntimeCandidateProbe,
        },
        model::ProvisioningEvent,
        receipt::ProvisioningReceiptStore,
    },
    runtime::{
        compatibility::LocalRuntimeDecision,
        diagnostics,
        model::{RuntimeDiagnosticSnapshot, RuntimeFailure, RuntimeManifest, RuntimePhase},
        paths::RuntimePaths,
        process_cleanup::shutdown_managed_runtimes,
        redaction::redact_secrets,
        updater::RuntimeUpdater,
    },
    storage::app_paths::AppPaths,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BundledInstallDecision {
    AlreadyInstalled,
    Provision,
}

fn install_decision(
    local: LocalRuntimeDecision,
    bundled: &RuntimeManifest,
) -> BundledInstallDecision {
    match local {
        LocalRuntimeDecision::FastStart(active)
            if active.target == bundled.target
                && active.version == bundled.version
                && active.sha256.eq_ignore_ascii_case(&bundled.sha256)
                && active.archive == bundled.archive =>
        {
            BundledInstallDecision::AlreadyInstalled
        }
        _ => BundledInstallDecision::Provision,
    }
}

struct InstallerEventSink;

impl ProvisioningEventSink for InstallerEventSink {
    fn emit(&self, event: ProvisioningEvent) -> Result<(), RuntimeFailure> {
        eprintln!("[runtime-installer] {:?}: {}", event.phase, event.message);
        Ok(())
    }
}

pub fn install() -> Result<(), RuntimeFailure> {
    let app = tauri::Builder::default()
        .build(tauri::generate_context!())
        .map_err(RuntimeFailure::internal)?;
    let app_paths = AppPaths::resolve(app.handle())?;
    app_paths.create_owned_directories()?;
    let runtime_paths = RuntimePaths::from_app_paths(&app_paths)?;
    let result = tauri::async_runtime::block_on(install_from_paths(runtime_paths.clone()));
    if let Err(cause) = &result {
        let snapshot = failed_snapshot(cause);
        let _ = diagnostics::export(&runtime_paths, &snapshot);
    }
    result
}

fn failed_snapshot(cause: &RuntimeFailure) -> RuntimeDiagnosticSnapshot {
    let safe_failure = RuntimeFailure {
        message: redact_secrets(&cause.message),
        ..cause.clone()
    };
    RuntimeDiagnosticSnapshot {
        phase: RuntimePhase::Failed,
        failure_phase: cause.context.as_ref().map(|_| RuntimePhase::Activating),
        failure: Some(safe_failure),
        ..RuntimeDiagnosticSnapshot::default()
    }
}

async fn install_from_paths(paths: RuntimePaths) -> Result<(), RuntimeFailure> {
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .build()
        .map_err(RuntimeFailure::internal)?;
    let updater = Arc::new(RuntimeUpdater::new_bundled(
        paths.clone(),
        client.clone(),
    )?);
    let bundled = updater.required_bundled_manifest().await?;
    if install_decision(updater.local_provisioned_decision()?, &bundled)
        == BundledInstallDecision::AlreadyInstalled
    {
        return Ok(());
    }

    let probe = Arc::new(RuntimeCandidateProbe::new(paths.clone(), client));
    let receipts = Arc::new(ProvisioningReceiptStore::new(paths.root.join("state")));
    let coordinator = ProvisioningCoordinator::new(
        updater,
        probe,
        receipts,
        Arc::new(InstallerEventSink),
    );
    let session = coordinator.start_session().await?;
    let prepared = coordinator.prepare_fresh(&session).await?;
    shutdown_managed_runtimes(&paths)?;
    coordinator
        .commit(session.id, &prepared.manifest_sha256)
        .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use semver::Version;
    use url::Url;

    use super::{BundledInstallDecision, failed_snapshot, install_decision};
    use crate::runtime::{
        compatibility::LocalRuntimeDecision,
        model::{
            ArchiveKind, RuntimeFailure, RuntimeFailureCode, RuntimeFailureContext,
            RuntimeFailureStage, RuntimeManifest, RuntimeTarget,
        },
    };

    fn manifest(version: &str, sha256: &str) -> RuntimeManifest {
        RuntimeManifest {
            schema_version: 1,
            version: Version::parse(version).unwrap(),
            dsh_version: Version::parse("0.1.0-rc.8").unwrap(),
            target: RuntimeTarget::WindowsX86_64,
            url: Url::parse("https://github.com/example/runtime.zip").unwrap(),
            size: 1,
            sha256: sha256.to_string(),
            signature: "signature".to_string(),
            archive: ArchiveKind::Zip,
            entrypoint: "node.exe".to_string(),
            args: Vec::new(),
            health_path: "/__desktop/health".to_string(),
        }
    }

    #[test]
    fn matching_receipt_and_bundled_manifest_skip_reinstall() {
        let bundled = manifest("0.1.0-preview", &"a".repeat(64));
        assert_eq!(
            install_decision(
                LocalRuntimeDecision::FastStart(bundled.clone()),
                &bundled,
            ),
            BundledInstallDecision::AlreadyInstalled,
        );
    }

    #[test]
    fn missing_or_different_runtime_requires_bundled_provisioning() {
        let bundled = manifest("0.1.0-preview", &"a".repeat(64));
        let different = manifest("0.1.0-preview", &"b".repeat(64));
        assert_eq!(
            install_decision(LocalRuntimeDecision::UpgradeRequired, &bundled),
            BundledInstallDecision::Provision,
        );
        assert_eq!(
            install_decision(LocalRuntimeDecision::FastStart(different), &bundled),
            BundledInstallDecision::Provision,
        );
    }

    #[test]
    fn installer_snapshot_preserves_safe_context_and_redacts_the_message() {
        let cause =
            RuntimeFailure::new(RuntimeFailureCode::Process, "Authorization: Bearer secret")
                .with_context(RuntimeFailureContext {
                    stage: RuntimeFailureStage::ManagedRuntimeShutdown,
                    process_ids: vec![41],
                    managed_relative_path: None,
                });

        let snapshot = failed_snapshot(&cause);

        let failure = snapshot.failure.unwrap();
        assert_eq!(failure.message, "Authorization: [REDACTED]");
        assert_eq!(failure.context, cause.context);
    }
}
