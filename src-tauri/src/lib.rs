mod agent;
mod agent_store;
mod agents;
pub mod app_mode;
mod app_update;
mod apps;
mod commands;
mod credentials;
mod data_cleanup;
mod desktop;
mod extensions;
mod generation;
mod migration;
mod mcp;
mod navigation;
mod platform;
mod plugin_market;
mod profile;
mod projects;
mod provisioning;
mod runtime;
mod safe_remove;
mod storage;
mod tray;
pub mod updater_signature_verifier;
mod window;

use std::sync::{Arc, atomic::AtomicBool};

use agent::service::AgentService;
use agent_store::{AgentStore, model::RecoveryState};
use agents::runtime::AgentRuntime;
use credentials::vault::{CredentialVault, NativeCredentialVault};
use desktop::DesktopCoordinator;
use generation::coordinator::{GenerationCoordinator, ProcessRuntimeLauncher, TauriEventSink};
use migration::{model::MigrationCandidate, service::MigrationService};
use platform::PlatformAdapter;
use profile::{model::ProfileDraft, repository::ProfileRepository};
use runtime::paths::RuntimePaths;
use storage::app_paths::AppPaths;
use tauri::{Emitter, Manager, webview::WebviewWindowBuilder};

// 仅编译进安装包级 E2E 候选应用。Windows WebDriver 不能可靠地穿透跨域工作台 iframe，
// 因此测试通过 WebView2 的 CDP 端口连接工作台；该配置必须在创建 WebView2 环境时传入。
#[cfg(feature = "e2e")]
const E2E_WEBVIEW_ADDITIONAL_BROWSER_ARGS: &str = "--remote-debugging-port=9229";

macro_rules! renderer_commands {
    ($callback:ident) => {
        $callback! {
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
            commands::app_launch,
            commands::app_stop,
            commands::app_status,
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
            commands::recovery_status,
            commands::check_app_update,
            commands::download_app_update,
            commands::install_app_update_now,
            commands::install_app_update_on_exit,
            commands::defer_app_update,
            commands::open_app_update_download,
            commands::take_app_update_receipt,
            commands::agent_capability_inventory,
            commands::agent_provider_metadata,
            commands::agent_credential_put,
            commands::agent_credential_delete,
            commands::agent_credential_status,
            commands::agent_credential_test,
            commands::agent_cli_path_select,
            commands::agent_cli_path_status,
            commands::agent_cli_install_status,
            commands::agent_cli_install_start,
            commands::agent_cli_login_status,
            commands::agent_cli_login_start,
            commands::agent_plugin_catalog,
            commands::agent_plugin_install_start,
            commands::agent_plugin_install_status,
            commands::agent_task_create,
            commands::agent_task_list,
            commands::agent_task_recover,
            commands::agent_task_start,
            commands::agent_task_cancel,
            commands::agent_task_resume,
            commands::agent_pending_approvals,
            commands::agent_resolve_approval,
            commands::agent_content_reference_read,
            commands::agent_extension_inventory,
            commands::agent_extension_install,
            commands::agent_extension_enable,
            commands::agent_extension_disable,
            commands::agent_extension_uninstall,
            commands::orderly_quit,
            commands::hide_window,
            commands::minimize_window,
            commands::toggle_maximize_window,
            commands::start_drag,
        }
    };
}

#[cfg(feature = "e2e")]
macro_rules! renderer_command_names {
    ($($command:path),* $(,)?) => {
        &[$(stringify!($command)),*, stringify!(commands::e2e_runtime_identity)]
    };
}

#[cfg(not(feature = "e2e"))]
macro_rules! renderer_command_names {
    ($($command:path),* $(,)?) => {
        &[$(stringify!($command)),*]
    };
}

#[cfg(feature = "e2e")]
macro_rules! renderer_handler {
    ($($command:path),* $(,)?) => {
        tauri::generate_handler![$($command),*, commands::e2e_runtime_identity]
    };
}

#[cfg(not(feature = "e2e"))]
macro_rules! renderer_handler {
    ($($command:path),* $(,)?) => {
        tauri::generate_handler![$($command),*]
    };
}

pub(crate) const RENDERER_COMMAND_NAMES: &[&str] = renderer_commands!(renderer_command_names);

#[cfg(test)]
mod renderer_command_tests {
    #[test]
    fn e2e_runtime_identity_is_only_registered_for_e2e_builds() {
        let registered = super::RENDERER_COMMAND_NAMES
            .contains(&"commands::e2e_runtime_identity");
        assert_eq!(registered, cfg!(feature = "e2e"));
    }

    #[cfg(feature = "e2e")]
    #[test]
    fn e2e_build_enables_the_fixed_webview2_cdp_port() {
        assert_eq!(
            super::E2E_WEBVIEW_ADDITIONAL_BROWSER_ARGS,
            "--remote-debugging-port=9229"
        );
    }
}

#[derive(Clone, Debug)]
pub enum FoundationBootstrapState {
    Ready,
    MigrationRequired(MigrationCandidate),
    MigrationConflict(MigrationCandidate),
    RecoveryBlocked(RecoveryState),
}

#[derive(Debug)]
pub enum FoundationBootstrapError {
    Runtime(runtime::model::RuntimeFailure),
    AgentStore(agent_store::model::AgentStoreError),
}

impl FoundationBootstrapError {
    pub fn recovery(&self) -> Option<&RecoveryState> {
        match self {
            Self::Runtime(_) => None,
            Self::AgentStore(error) => error.recovery(),
        }
    }
}

impl From<runtime::model::RuntimeFailure> for FoundationBootstrapError {
    fn from(error: runtime::model::RuntimeFailure) -> Self {
        Self::Runtime(error)
    }
}

impl From<agent_store::model::AgentStoreError> for FoundationBootstrapError {
    fn from(error: agent_store::model::AgentStoreError) -> Self {
        Self::AgentStore(error)
    }
}

impl std::fmt::Display for FoundationBootstrapError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Runtime(error) => std::fmt::Display::fmt(error, formatter),
            Self::AgentStore(error) => std::fmt::Display::fmt(error, formatter),
        }
    }
}

impl std::error::Error for FoundationBootstrapError {}

pub struct DesktopFoundation {
    pub paths: AppPaths,
    pub platform: Arc<dyn PlatformAdapter>,
    pub profiles: Arc<ProfileRepository>,
    pub agent_store: Option<Arc<AgentStore>>,
    pub agent_service: Option<Arc<AgentService>>,
    pub(crate) credential_vault: Arc<dyn CredentialVault>,
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
    ) -> Result<Self, FoundationBootstrapError> {
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
    ) -> Result<Self, FoundationBootstrapError> {
        let credential_vault: Arc<dyn CredentialVault> = Arc::new(NativeCredentialVault::new());
        let mut bootstrap_state = bootstrap_state;
        let mut profiles = profiles;
        let agent_store = if matches!(bootstrap_state, FoundationBootstrapState::Ready) {
            paths.create_owned_directories()?;
            let agent_store = match AgentStore::open(&paths) {
                Ok(store) => Arc::new(store),
                Err(error) => {
                    let recovery = error
                        .recovery()
                        .expect("AgentStore errors always carry recovery state")
                        .clone();
                    profiles = Arc::new(ProfileRepository::open_read_only(paths.profiles.clone())?);
                    bootstrap_state = FoundationBootstrapState::RecoveryBlocked(recovery);
                    return Ok(Self {
                        paths,
                        platform,
                        profiles,
                        agent_store: None,
                        agent_service: None,
                        credential_vault,
                        migration,
                        bootstrap_state,
                        migration_deferred: AtomicBool::new(false),
                    });
                }
            };
            if profiles.list()?.is_empty() {
                let default_root = paths.profiles.join("default");
                std::fs::create_dir_all(&default_root)
                    .map_err(runtime::model::RuntimeFailure::internal)?;
                profiles.create(ProfileDraft::named("默认", default_root))?;
            }
            profiles.recover_interrupted()?;
            Some(agent_store)
        } else {
            None
        };
        let agent_service = agent_store
            .as_ref()
            .map(|store| Arc::new(AgentService::new(Arc::clone(store))));
        Ok(Self {
            paths,
            platform,
            profiles,
            agent_store,
            agent_service,
            credential_vault,
            migration,
            bootstrap_state,
            migration_deferred: AtomicBool::new(false),
        })
    }

    pub(crate) fn runtime_allowed(&self) -> Result<(), runtime::model::RuntimeFailure> {
        if matches!(self.bootstrap_state, FoundationBootstrapState::Ready)
            && !self
                .migration_deferred
                .load(std::sync::atomic::Ordering::SeqCst)
        {
            return Ok(());
        }
        let mut failure = runtime::model::RuntimeFailure::new(
            runtime::model::RuntimeFailureCode::MigrationConflict,
            "应用处于数据安全阻断状态",
        );
        failure.recoverable = false;
        Err(failure)
    }

    fn resolve(app: &tauri::AppHandle) -> Result<Self, FoundationBootstrapError> {
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
                    AppPaths::with_active_root_and_downloads(
                        stable_paths.stable_root.clone(),
                        candidate.source.clone(),
                        resource_root,
                        stable_paths.user_downloads.clone(),
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
                Err(error) => return Err(error.into()),
            },
            [candidate, ..] => (
                stable_paths,
                FoundationBootstrapState::MigrationConflict(candidate.clone()),
            ),
        };
        let profiles = Arc::new(
            if matches!(bootstrap_state, FoundationBootstrapState::Ready) {
                ProfileRepository::open(paths.profiles.clone())?
            } else {
                ProfileRepository::open_read_only(paths.profiles.clone())?
            },
        );
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
            let sink = TauriEventSink::new(app.handle().clone());
            let agent_worker_root = app
                .path()
                .resource_dir()
                .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?
                .join("agent-adapter");
            let agent_runtime = AgentRuntime::new(
                foundation.agent_store.clone(),
                foundation.paths.clone(),
                agent_worker_root,
                Arc::clone(&sink),
                Arc::clone(&foundation.credential_vault),
            );
            app.manage(Arc::clone(&agent_runtime));
            let startup_runtime = Arc::clone(&agent_runtime);
            tauri::async_runtime::spawn(async move {
                let _ = startup_runtime.reconcile_startup().await;
            });
            app.manage(Arc::new(agents::cli_ops::AgentCliJobState::new()));
            app.manage(Arc::new(plugin_market::PluginMarketState::new()));
            let runtime_services = if foundation.runtime_allowed().is_ok() {
                let runtime_paths = RuntimePaths::from_app_paths(&foundation.paths)
                    .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?;
                let launcher = ProcessRuntimeLauncher::new(runtime_paths.clone(), sink.clone())
                    .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?;
                // 本地应用启动器：与运行时共享 RuntimePaths，生命周期事件广播给壳层。
                // 注意 runtime_paths 随后会被 move 进 GenerationCoordinator，必须在此之前 clone。
                let app_events = app.handle().clone();
                let app_launcher = Arc::new(apps::AppLauncher::new(
                    runtime_paths.clone(),
                    Box::new(move |event| {
                        let _ = app_events.emit(apps::launcher::LOCAL_APP_EVENT, event);
                    }),
                ));
                let generations = GenerationCoordinator::new(runtime_paths, launcher, sink.clone())
                    .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?;
                let coordinator =
                    DesktopCoordinator::new(generations, Arc::clone(&foundation.profiles), sink);
                Some((app_launcher, coordinator))
            } else {
                None
            };
            let app_updates = app_update::AppUpdateController::new(
                app.handle().clone(),
                foundation.paths.state.join("app-update-receipt.json"),
            );
            app.manage(Arc::clone(&foundation));
            app.manage(Arc::clone(&app_updates));
            let config = app
                .config()
                .app
                .windows
                .iter()
                .find(|config| config.label == "main")
                .ok_or("缺少 main window 配置")?;
            let window_builder = WebviewWindowBuilder::from_config(app, config)?
                .on_navigation(navigation::NavigationPolicy::desktop_webview)
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .on_download(|_, _| false);
            #[cfg(feature = "e2e")]
            let window_builder =
                window_builder.additional_browser_args(E2E_WEBVIEW_ADDITIONAL_BROWSER_ARGS);
            let window = window_builder.build()?;
            if let Some((app_launcher, coordinator)) = runtime_services {
                app.manage(Arc::clone(&app_launcher));
                app.manage(Arc::clone(&coordinator));
                tray::install(app, window.clone(), coordinator, foundation, app_updates)?;
            }
            window.show()?;
            Ok(())
        })
        .invoke_handler(renderer_commands!(renderer_handler));

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
            let app_launcher = app_handle
                .try_state::<Arc<apps::AppLauncher>>()
                .map(|state| Arc::clone(state.inner()));
            tauri::async_runtime::block_on(async move {
                // 退出前先停掉所有本地应用，再关闭受管运行时。
                if let Some(app_launcher) = app_launcher {
                    app_launcher.stop_all().await;
                }
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
        fs,
        path::{Path, PathBuf},
        sync::{Arc, atomic::Ordering},
    };

    use sha2::{Digest, Sha256};

    use super::{
        DesktopFoundation, FoundationBootstrapState,
        agent_store::model::BackupMetadata,
        migration::{model::MigrationCandidate, service::MigrationService},
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

    fn migration_candidate(paths: &AppPaths) -> MigrationCandidate {
        MigrationCandidate {
            source: paths.active_root.clone(),
            target: paths.stable_root.clone(),
            bytes: 0,
            profiles: 0,
            workspaces: 0,
        }
    }

    fn tree_snapshot(root: &Path) -> Vec<(PathBuf, Option<Vec<u8>>)> {
        fn visit(root: &Path, current: &Path, entries: &mut Vec<(PathBuf, Option<Vec<u8>>)>) {
            let Ok(children) = fs::read_dir(current) else {
                return;
            };
            let mut children = children.map(Result::unwrap).collect::<Vec<_>>();
            children.sort_by_key(|entry| entry.file_name());
            for child in children {
                let path = child.path();
                let relative = path.strip_prefix(root).unwrap().to_path_buf();
                if path.is_dir() {
                    entries.push((relative, None));
                    visit(root, &path, entries);
                } else {
                    entries.push((relative, Some(fs::read(&path).unwrap())));
                }
            }
        }

        let mut entries = Vec::new();
        visit(root, root, &mut entries);
        entries
    }

    fn sha256(path: &Path) -> String {
        hex::encode(Sha256::digest(fs::read(path).unwrap()))
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
        assert!(foundation.paths.agent_database.exists());
        assert!(foundation.agent_service.is_some());
        assert_eq!(
            rusqlite::Connection::open(&foundation.paths.agent_database)
                .unwrap()
                .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            crate::agent_store::migrations::CURRENT_SCHEMA_VERSION
        );
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

    #[test]
    fn non_ready_foundation_states_leave_the_entire_data_tree_unchanged_and_read_only() {
        for migration_conflict in [false, true] {
            let dir = tempfile::tempdir().unwrap();
            let paths =
                AppPaths::from_roots(dir.path().join("app-data"), dir.path().join("resources"));
            fs::create_dir_all(&paths.active_root).unwrap();
            fs::write(paths.active_root.join("legacy-marker.txt"), b"preserve-me").unwrap();
            let profiles =
                Arc::new(ProfileRepository::open_read_only(paths.profiles.clone()).unwrap());
            let migration = Arc::new(MigrationService::new(
                paths.stable_root.clone(),
                paths.backups.clone(),
            ));
            let candidate = migration_candidate(&paths);
            let state = if migration_conflict {
                FoundationBootstrapState::MigrationConflict(candidate)
            } else {
                FoundationBootstrapState::MigrationRequired(candidate)
            };
            let before = tree_snapshot(&paths.active_root);

            let foundation = DesktopFoundation::from_parts_with_state(
                paths.clone(),
                Arc::new(TestPlatform),
                profiles,
                migration,
                state,
            )
            .unwrap();

            assert!(foundation.agent_store.is_none());
            assert_eq!(tree_snapshot(&paths.active_root), before);
            assert!(!paths.agent_database.exists());
            assert!(!paths.agent_backups.exists());
            assert!(!paths.profiles.exists());
            assert!(
                foundation
                    .profiles
                    .create(ProfileDraft::named(
                        "blocked",
                        paths.active_root.join("blocked")
                    ))
                    .is_err()
            );
            assert_eq!(tree_snapshot(&paths.active_root), before);
        }
    }

    #[test]
    fn deferred_migration_does_not_initialize_agent_state_or_make_the_repository_writable() {
        let dir = tempfile::tempdir().unwrap();
        let paths = AppPaths::from_roots(dir.path().join("app-data"), dir.path().join("resources"));
        fs::create_dir_all(&paths.active_root).unwrap();
        fs::write(paths.active_root.join("legacy-marker.txt"), b"preserve-me").unwrap();
        let before = tree_snapshot(&paths.active_root);
        let profiles = Arc::new(ProfileRepository::open_read_only(paths.profiles.clone()).unwrap());
        let migration = Arc::new(MigrationService::new(
            paths.stable_root.clone(),
            paths.backups.clone(),
        ));

        let foundation = Arc::new(
            DesktopFoundation::from_parts_with_state(
                paths.clone(),
                Arc::new(TestPlatform),
                profiles,
                migration,
                FoundationBootstrapState::MigrationRequired(migration_candidate(&paths)),
            )
            .unwrap(),
        );
        foundation.migration_deferred.store(true, Ordering::SeqCst);

        assert!(foundation.agent_store.is_none());
        assert!(foundation.runtime_allowed().is_err());
        assert_eq!(tree_snapshot(&paths.active_root), before);
        assert!(!paths.agent_database.exists());
        assert!(!paths.profiles.exists());
        assert!(
            foundation
                .profiles
                .create(ProfileDraft::named(
                    "blocked",
                    paths.active_root.join("blocked")
                ))
                .is_err()
        );
        assert_eq!(tree_snapshot(&paths.active_root), before);

        let app = tauri::test::mock_builder()
            .manage(Arc::clone(&foundation))
            .invoke_handler(tauri::generate_handler![crate::commands::migration_status])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();
        let webview = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .unwrap();
        let response = tauri::test::get_ipc_response(
            &webview,
            tauri::webview::InvokeRequest {
                cmd: "migration_status".into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: "tauri://localhost".parse().unwrap(),
                body: tauri::ipc::InvokeBody::default(),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        )
        .unwrap()
        .deserialize::<serde_json::Value>()
        .unwrap();
        assert_eq!(response["phase"], "deferred");
    }

    #[test]
    fn migration_required_with_separate_legacy_root_changes_neither_tree() {
        let dir = tempfile::tempdir().unwrap();
        let stable_root = dir.path().join("stable-data");
        let legacy_root = dir.path().join("legacy-data");
        fs::create_dir_all(&stable_root).unwrap();
        fs::create_dir_all(&legacy_root).unwrap();
        fs::write(stable_root.join("stable-marker.txt"), b"stable-preserve").unwrap();
        fs::write(legacy_root.join("legacy-marker.txt"), b"legacy-preserve").unwrap();
        let paths = AppPaths::with_active_root(
            stable_root.clone(),
            legacy_root.clone(),
            dir.path().join("resources"),
        );
        let stable_before = tree_snapshot(&stable_root);
        let legacy_before = tree_snapshot(&legacy_root);
        let profiles = Arc::new(ProfileRepository::open_read_only(paths.profiles.clone()).unwrap());
        let migration = Arc::new(MigrationService::new(
            paths.stable_root.clone(),
            paths.backups.clone(),
        ));

        let foundation = DesktopFoundation::from_parts_with_state(
            paths.clone(),
            Arc::new(TestPlatform),
            profiles,
            migration,
            FoundationBootstrapState::MigrationRequired(migration_candidate(&paths)),
        )
        .unwrap();

        assert!(foundation.agent_store.is_none());
        assert_eq!(tree_snapshot(&stable_root), stable_before);
        assert_eq!(tree_snapshot(&legacy_root), legacy_before);
        assert!(!paths.agent_database.exists());
        assert!(!paths.agent_backups.exists());
        assert!(!paths.profiles.exists());
    }

    #[test]
    fn bootstrap_preserves_structured_recovery_and_verified_backup_can_restore_elsewhere() {
        let dir = tempfile::tempdir().unwrap();
        let paths = AppPaths::from_roots(dir.path().join("app-data"), dir.path().join("resources"));
        paths.create_owned_directories().unwrap();
        let profiles = Arc::new(ProfileRepository::open(paths.profiles.clone()).unwrap());
        let source = rusqlite::Connection::open(&paths.agent_database).unwrap();
        source
            .execute_batch(
                "PRAGMA user_version = 0;
                 CREATE TABLE providers (broken_fixture TEXT NOT NULL);
                 INSERT INTO providers VALUES ('preserve-me');",
            )
            .unwrap();
        drop(source);
        let source_before = fs::read(&paths.agent_database).unwrap();
        let migration = Arc::new(MigrationService::new(
            paths.stable_root.clone(),
            paths.backups.clone(),
        ));

        let foundation = Arc::new(
            DesktopFoundation::from_parts(
                paths.clone(),
                Arc::new(TestPlatform),
                profiles,
                migration,
            )
            .expect("recovery-blocked bootstrap must keep the application shell alive"),
        );
        let FoundationBootstrapState::RecoveryBlocked(recovery) = &foundation.bootstrap_state
        else {
            panic!("broken v0 schema did not produce a recovery-blocked shell")
        };
        let backup = recovery.backup.as_ref().expect("verified backup evidence");

        assert_eq!(recovery.source_path, paths.agent_database);
        assert_eq!(backup.source_path, paths.agent_database);
        assert!(backup.backup_path.starts_with(&paths.agent_backups));
        assert_eq!(backup.sha256, sha256(&backup.backup_path));
        assert_eq!(
            backup.byte_length,
            fs::metadata(&backup.backup_path).unwrap().len()
        );
        assert!(backup.metadata_path.exists());
        let sidecar: BackupMetadata =
            serde_json::from_slice(&fs::read(&backup.metadata_path).unwrap()).unwrap();
        assert_eq!(&sidecar, backup);
        assert_eq!(fs::read(&paths.agent_database).unwrap(), source_before);

        let restored_path = dir
            .path()
            .join("manual-recovery/agent-platform-restored.sqlite3");
        fs::create_dir_all(restored_path.parent().unwrap()).unwrap();
        fs::copy(&backup.backup_path, &restored_path).unwrap();
        let restored = rusqlite::Connection::open(&restored_path).unwrap();
        assert_eq!(
            restored
                .query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0))
                .unwrap(),
            "ok"
        );
        assert_eq!(
            restored
                .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            0
        );
        assert_eq!(
            restored
                .query_row("SELECT broken_fixture FROM providers", [], |row| {
                    row.get::<_, String>(0)
                })
                .unwrap(),
            "preserve-me"
        );
        assert!(foundation.agent_store.is_none());
        assert!(
            foundation
                .profiles
                .create(ProfileDraft::named(
                    "blocked",
                    paths.profiles.join("blocked")
                ))
                .is_err()
        );

        let dto = crate::commands::recovery_status_for(&foundation)
            .expect("revalidated recovery DTO")
            .expect("recovery DTO");
        assert_eq!(dto.source, paths.agent_database);
        assert_eq!(dto.backup, backup.backup_path);
        assert_eq!(dto.sha256, backup.sha256);
        assert_eq!(dto.length, backup.byte_length);
        assert_eq!(dto.schema, backup.schema_version);
        assert_eq!(dto.sidecar, backup.metadata_path);

        let app = tauri::test::mock_builder()
            .manage(Arc::clone(&foundation))
            .invoke_handler(tauri::generate_handler![crate::commands::recovery_status])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .expect("recovery-blocked Tauri setup must keep the shell alive");
        let webview = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .unwrap();
        let response = tauri::test::get_ipc_response(
            &webview,
            tauri::webview::InvokeRequest {
                cmd: "recovery_status".into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: "tauri://localhost".parse().unwrap(),
                body: tauri::ipc::InvokeBody::default(),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        )
        .expect("recovery command returned a fixed error unexpectedly")
        .deserialize::<serde_json::Value>()
        .unwrap();
        assert_eq!(response["sha256"], backup.sha256);
        assert_eq!(response["length"], backup.byte_length);

        fs::write(&backup.backup_path, b"tampered after bootstrap").unwrap();
        let response = tauri::test::get_ipc_response(
            &webview,
            tauri::webview::InvokeRequest {
                cmd: "recovery_status".into(),
                callback: tauri::ipc::CallbackFn(2),
                error: tauri::ipc::CallbackFn(3),
                url: "tauri://localhost".parse().unwrap(),
                body: tauri::ipc::InvokeBody::default(),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        )
        .unwrap_err();
        assert_eq!(response["message"], "恢复证据验证失败");
        let error = crate::commands::recovery_status_for(&foundation).unwrap_err();
        assert_eq!(error.message, "恢复证据验证失败");
    }

    #[test]
    fn recovery_without_published_evidence_returns_the_fixed_blocking_message() {
        let dir = tempfile::tempdir().unwrap();
        let paths = AppPaths::from_roots(dir.path().join("app-data"), dir.path().join("resources"));
        paths.create_owned_directories().unwrap();
        let profiles = Arc::new(ProfileRepository::open_read_only(paths.profiles.clone()).unwrap());
        let migration = Arc::new(MigrationService::new(
            paths.stable_root.clone(),
            paths.backups.clone(),
        ));
        let recovery = crate::agent_store::model::RecoveryState {
            source_path: paths.agent_database.clone(),
            backup: None,
        };
        let foundation = DesktopFoundation::from_parts_with_state(
            paths,
            Arc::new(TestPlatform),
            profiles,
            migration,
            FoundationBootstrapState::RecoveryBlocked(recovery),
        )
        .unwrap();

        let error = crate::commands::recovery_status_for(&foundation).unwrap_err();

        assert_eq!(error.message, "Agent 数据库恢复证据已丢失，已阻止启动");
        assert!(!error.recoverable);
    }
}
