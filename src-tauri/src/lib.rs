pub mod app_mode;
mod app_update;
mod commands;
mod data_cleanup;
mod desktop;
mod generation;
mod migration;
mod navigation;
mod platform;
mod profile;
mod projects;
mod provisioning;
mod runtime;
mod safe_remove;
mod storage;
mod tray;
mod window;

use std::sync::{Arc, atomic::AtomicBool};

use desktop::DesktopCoordinator;
use generation::coordinator::{GenerationCoordinator, ProcessRuntimeLauncher, TauriEventSink};
use migration::{model::MigrationCandidate, service::MigrationService};
use platform::PlatformAdapter;
use profile::{model::ProfileDraft, repository::ProfileRepository};
use runtime::paths::RuntimePaths;
use storage::app_paths::AppPaths;
use tauri::{Manager, webview::WebviewWindowBuilder};

#[derive(Clone, Debug)]
pub enum FoundationBootstrapState {
    Ready,
    MigrationRequired(MigrationCandidate),
    MigrationConflict(MigrationCandidate),
}

pub struct DesktopFoundation {
    pub paths: AppPaths,
    pub platform: Arc<dyn PlatformAdapter>,
    pub profiles: Arc<ProfileRepository>,
    pub migration: Arc<MigrationService>,
    pub bootstrap_state: FoundationBootstrapState,
    pub migration_deferred: AtomicBool,
}

impl DesktopFoundation {
    pub fn from_parts(
        paths: AppPaths,
        platform: Arc<dyn PlatformAdapter>,
        profiles: Arc<ProfileRepository>,
        migration: Arc<MigrationService>,
    ) -> Result<Self, runtime::model::RuntimeFailure> {
        Self::from_parts_with_state(
            paths,
            platform,
            profiles,
            migration,
            FoundationBootstrapState::Ready,
        )
    }

    fn from_parts_with_state(
        paths: AppPaths,
        platform: Arc<dyn PlatformAdapter>,
        profiles: Arc<ProfileRepository>,
        migration: Arc<MigrationService>,
        bootstrap_state: FoundationBootstrapState,
    ) -> Result<Self, runtime::model::RuntimeFailure> {
        paths.create_owned_directories()?;
        if profiles.list()?.is_empty() {
            let default_root = paths.profiles.join("default");
            std::fs::create_dir_all(&default_root)
                .map_err(runtime::model::RuntimeFailure::internal)?;
            profiles.create(ProfileDraft::named("默认", default_root))?;
        }
        profiles.recover_interrupted()?;
        Ok(Self {
            paths,
            platform,
            profiles,
            migration,
            bootstrap_state,
            migration_deferred: AtomicBool::new(false),
        })
    }

    fn resolve(app: &tauri::AppHandle) -> Result<Self, runtime::model::RuntimeFailure> {
        let stable_paths = AppPaths::resolve(app)?;
        let platform = platform::current();
        let migration = Arc::new(MigrationService::new(
            stable_paths.stable_root.clone(),
            stable_paths.backups.clone(),
        ));
        let candidates =
            migration.discover(&platform.legacy_data_roots(&stable_paths.stable_root))?;
        let resource_root = stable_paths
            .bundled_runtime
            .parent()
            .ok_or_else(|| runtime::model::RuntimeFailure::internal("资源目录无效"))?
            .to_path_buf();

        let (paths, bootstrap_state) = match candidates.as_slice() {
            [] => (stable_paths, FoundationBootstrapState::Ready),
            [candidate] => match migration.plan(&candidate.source) {
                Ok(_) => (
                    AppPaths::with_active_root(
                        stable_paths.stable_root.clone(),
                        candidate.source.clone(),
                        resource_root,
                    ),
                    FoundationBootstrapState::MigrationRequired(candidate.clone()),
                ),
                Err(error)
                    if error.code == runtime::model::RuntimeFailureCode::MigrationConflict =>
                {
                    (
                        stable_paths,
                        FoundationBootstrapState::MigrationConflict(candidate.clone()),
                    )
                }
                Err(error) => return Err(error),
            },
            [candidate, ..] => (
                stable_paths,
                FoundationBootstrapState::MigrationConflict(candidate.clone()),
            ),
        };
        paths.create_owned_directories()?;
        let profiles = Arc::new(ProfileRepository::open(paths.profiles.clone())?);
        Self::from_parts_with_state(paths, platform, profiles, migration, bootstrap_state)
    }
}

pub fn run(mode: app_mode::ApplicationMode) {
    match mode {
        app_mode::ApplicationMode::Desktop => run_desktop(),
        app_mode::ApplicationMode::PrepareDataCleanup => {
            exit_after_cleanup(data_cleanup::prepare_and_spawn())
        }
        app_mode::ApplicationMode::CleanupPending(nonce) => {
            exit_after_cleanup(data_cleanup::cleanup_pending(nonce))
        }
        app_mode::ApplicationMode::ListUninstallProjects(token) => {
            exit_after_cleanup(projects::uninstall::write_preview(token))
        }
        app_mode::ApplicationMode::CleanupProjects(token) => {
            exit_after_cleanup(projects::uninstall::cleanup_projects(token))
        }
    }
}

fn exit_after_cleanup(result: Result<(), runtime::model::RuntimeFailure>) -> ! {
    match result {
        Ok(()) => std::process::exit(0),
        Err(cause) => {
            eprintln!("{}", cause.message);
            std::process::exit(30);
        }
    }
}

fn run_desktop() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }));
    #[cfg(feature = "e2e")]
    let builder = builder
        .plugin(tauri_plugin_wdio::init())
        .plugin(tauri_plugin_wdio_webdriver::init());
    let builder = builder
        .setup(|app| {
            let foundation = Arc::new(
                DesktopFoundation::resolve(app.handle())
                    .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?,
            );
            let runtime_paths = RuntimePaths::from_app_paths(&foundation.paths)
                .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?;
            let sink = TauriEventSink::new(app.handle().clone());
            let launcher = ProcessRuntimeLauncher::new(runtime_paths.clone(), sink.clone())
                .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?;
            let generations = GenerationCoordinator::new(runtime_paths, launcher, sink.clone())
                .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?;
            let coordinator =
                DesktopCoordinator::new(generations, Arc::clone(&foundation.profiles), sink);
            let app_updates = app_update::AppUpdateController::new(
                app.handle().clone(),
                foundation.paths.state.join("app-update-receipt.json"),
            );
            app.manage(Arc::clone(&foundation));
            app.manage(Arc::clone(&coordinator));
            app.manage(Arc::clone(&app_updates));
            let config = app
                .config()
                .app
                .windows
                .iter()
                .find(|config| config.label == "main")
                .ok_or("缺少 main window 配置")?;
            let window = WebviewWindowBuilder::from_config(app, config)?
                .on_navigation(navigation::NavigationPolicy::top_level)
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .on_download(|_, _| false)
                .build()?;
            tray::install(app, window.clone(), coordinator, foundation, app_updates)?;
            window.show()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::bootstrap_runtime,
            commands::cancel_runtime,
            commands::repair_runtime,
            commands::restart_runtime,
            commands::switch_profile,
            commands::list_profiles,
            commands::list_project_metadata,
            commands::patch_project_metadata,
            commands::remove_project_metadata,
            commands::preview_default_project_directory,
            commands::create_default_project_directory,
            commands::recycle_project_directory,
            commands::create_profile,
            commands::update_profile,
            commands::duplicate_profile,
            commands::delete_profile,
            commands::open_external_https,
            commands::open_user_data,
            commands::export_diagnostics,
            commands::migration_status,
            commands::confirm_migration,
            commands::defer_migration,
            commands::check_app_update,
            commands::download_app_update,
            commands::install_app_update_now,
            commands::install_app_update_on_exit,
            commands::defer_app_update,
            commands::take_app_update_receipt,
            commands::orderly_quit,
            commands::hide_window,
            commands::minimize_window,
            commands::toggle_maximize_window,
            commands::start_drag,
        ]);

    let app = builder
        .build(tauri::generate_context!())
        .expect("failed to build DeepSeek Harness Desktop");
    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
            let coordinator = app_handle
                .try_state::<Arc<DesktopCoordinator>>()
                .map(|state| Arc::clone(state.inner()));
            let app_updates = app_handle
                .try_state::<Arc<app_update::AppUpdateController>>()
                .map(|state| Arc::clone(state.inner()));
            tauri::async_runtime::block_on(async move {
                if let Some(coordinator) = coordinator {
                    let _ = coordinator.shutdown().await;
                }
                if let Some(app_updates) = app_updates
                    && app_updates.should_install_on_exit()
                {
                    let _ = app_updates.install_scheduled_after_shutdown().await;
                }
            });
        }
    });
}

#[cfg(test)]
mod foundation_tests {
    use std::{
        path::{Path, PathBuf},
        sync::Arc,
    };

    use super::{
        DesktopFoundation,
        migration::service::MigrationService,
        platform::PlatformAdapter,
        profile::{
            model::{ActivationReason, ProfileDraft},
            repository::ProfileRepository,
        },
        runtime::paths::RuntimePaths,
        storage::app_paths::AppPaths,
    };

    struct TestPlatform;

    impl PlatformAdapter for TestPlatform {
        fn legacy_data_roots(&self, _stable_root: &Path) -> Vec<PathBuf> {
            Vec::new()
        }
    }

    #[test]
    fn desktop_foundation_creates_a_default_profile_and_shared_runtime_paths() {
        let dir = tempfile::tempdir().unwrap();
        let paths = AppPaths::from_roots(dir.path().join("data"), dir.path().join("resources"));
        paths.create_owned_directories().unwrap();
        let profiles = Arc::new(ProfileRepository::open(paths.profiles.clone()).unwrap());
        let migration = Arc::new(MigrationService::new(
            paths.stable_root.clone(),
            paths.backups.clone(),
        ));
        let foundation = DesktopFoundation::from_parts(
            paths.clone(),
            Arc::new(TestPlatform),
            profiles,
            migration,
        )
        .unwrap();
        assert_eq!(foundation.profiles.list().unwrap().len(), 1);
        let runtime_paths = RuntimePaths::from_app_paths(&foundation.paths).unwrap();
        assert_eq!(
            runtime_paths.versions.parent().unwrap(),
            foundation.paths.runtime
        );
    }

    #[test]
    fn desktop_foundation_recovers_an_interrupted_profile_activation() {
        let dir = tempfile::tempdir().unwrap();
        let paths = AppPaths::from_roots(dir.path().join("data"), dir.path().join("resources"));
        paths.create_owned_directories().unwrap();
        let profiles = Arc::new(ProfileRepository::open(paths.profiles.clone()).unwrap());
        let profile = profiles
            .create(ProfileDraft::named("默认", paths.profiles.join("default")))
            .unwrap();
        profiles
            .begin_activation(
                &profile.id,
                profile.revision,
                "interrupted-generation",
                ActivationReason::Startup,
            )
            .unwrap();
        let migration = Arc::new(MigrationService::new(
            paths.stable_root.clone(),
            paths.backups.clone(),
        ));

        DesktopFoundation::from_parts(
            paths,
            Arc::new(TestPlatform),
            Arc::clone(&profiles),
            migration,
        )
        .unwrap();

        let state = profiles.state().unwrap();
        assert!(state.pending.is_none());
        assert_eq!(state.failed_attempts.len(), 1);
        assert_eq!(
            state.failed_attempts[0].generation_id,
            "interrupted-generation"
        );
        assert_eq!(profiles.list().unwrap().len(), 1);
    }
}
