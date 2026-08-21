use std::sync::Arc;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, State, WebviewWindow};
use tauri_plugin_opener::OpenerExt;

use crate::{
    DesktopFoundation, FoundationBootstrapState,
    app_update::{
        AppUpdateController,
        model::{AppUpdateFailure, AppUpdateReceipt, AppUpdateSource, AppUpdateState},
    },
    desktop::DesktopCoordinator,
    profile::model::{
        PermissionMode, ProfileDraft, ProfileListSnapshot, ProfilePatch, ProfileRecord,
    },
    projects::{
        active_profile,
        location::{ProjectLocationPreview, create_project_location, preview_project_location},
        metadata::{ProjectMetadataPatch, ProjectMetadataSnapshot},
        metadata_repository,
        recycle::{ProtectedRoots, resolve_registered_workspace, validate_recycle_target},
    },
    runtime::{BootstrapReply, RuntimeFailure},
};

#[tauri::command]
pub async fn check_app_update(
    state: State<'_, Arc<AppUpdateController>>,
    source: AppUpdateSource,
) -> Result<AppUpdateState, AppUpdateFailure> {
    state.check(source).await?;
    Ok(state.snapshot().await)
}

#[tauri::command]
pub async fn download_app_update(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<AppUpdateState, AppUpdateFailure> {
    state.download().await?;
    Ok(state.snapshot().await)
}

#[tauri::command]
pub async fn install_app_update_now(
    state: State<'_, Arc<AppUpdateController>>,
    desktop: State<'_, Arc<DesktopCoordinator>>,
) -> Result<(), AppUpdateFailure> {
    state.install_now(desktop.inner()).await
}

#[tauri::command]
pub async fn install_app_update_on_exit(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<AppUpdateState, AppUpdateFailure> {
    state.install_on_exit().await?;
    Ok(state.snapshot().await)
}

#[tauri::command]
pub async fn defer_app_update(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<AppUpdateState, AppUpdateFailure> {
    state.defer().await?;
    Ok(state.snapshot().await)
}

#[tauri::command]
pub fn take_app_update_receipt(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<Option<AppUpdateReceipt>, AppUpdateFailure> {
    state.take_completed_receipt()
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfileDraftInput {
    name: String,
    data_root: std::path::PathBuf,
    #[serde(default)]
    permission_mode: PermissionMode,
}

impl From<ProfileDraftInput> for ProfileDraft {
    fn from(value: ProfileDraftInput) -> Self {
        Self {
            name: value.name,
            data_root: value.data_root,
            permission_mode: value.permission_mode,
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfilePatchInput {
    name: Option<String>,
    data_root: Option<std::path::PathBuf>,
    permission_mode: Option<PermissionMode>,
}

impl From<ProfilePatchInput> for ProfilePatch {
    fn from(value: ProfilePatchInput) -> Self {
        Self {
            name: value.name,
            data_root: value.data_root,
            permission_mode: value.permission_mode,
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationStatusReply {
    phase: &'static str,
    source: Option<std::path::PathBuf>,
    target: Option<std::path::PathBuf>,
    bytes: Option<u64>,
    profiles: Option<usize>,
    workspaces: Option<usize>,
}

impl MigrationStatusReply {
    fn ready() -> Self {
        Self {
            phase: "ready",
            source: None,
            target: None,
            bytes: None,
            profiles: None,
            workspaces: None,
        }
    }

    fn candidate(
        phase: &'static str,
        candidate: &crate::migration::model::MigrationCandidate,
    ) -> Self {
        Self {
            phase,
            source: Some(candidate.source.clone()),
            target: Some(candidate.target.clone()),
            bytes: Some(candidate.bytes),
            profiles: Some(candidate.profiles),
            workspaces: Some(candidate.workspaces),
        }
    }
}

#[tauri::command]
pub fn migration_status(foundation: State<'_, Arc<DesktopFoundation>>) -> MigrationStatusReply {
    if foundation
        .migration_deferred
        .load(std::sync::atomic::Ordering::SeqCst)
    {
        return MigrationStatusReply::ready();
    }
    match &foundation.bootstrap_state {
        FoundationBootstrapState::Ready => MigrationStatusReply::ready(),
        FoundationBootstrapState::MigrationRequired(candidate) => {
            MigrationStatusReply::candidate("candidate", candidate)
        }
        FoundationBootstrapState::MigrationConflict(candidate) => {
            MigrationStatusReply::candidate("conflict", candidate)
        }
    }
}

#[tauri::command]
pub async fn confirm_migration(
    app: AppHandle,
    foundation: State<'_, Arc<DesktopFoundation>>,
) -> Result<(), RuntimeFailure> {
    let candidate = match &foundation.bootstrap_state {
        FoundationBootstrapState::MigrationRequired(candidate) => candidate.clone(),
        FoundationBootstrapState::Ready => return Ok(()),
        FoundationBootstrapState::MigrationConflict(_) => {
            return Err(RuntimeFailure::new(
                crate::runtime::model::RuntimeFailureCode::MigrationConflict,
                "新旧目录都有数据，不能自动迁移",
            ));
        }
    };
    let migration = Arc::clone(&foundation.migration);
    tauri::async_runtime::spawn_blocking(move || {
        let plan = migration.plan(&candidate.source)?;
        migration.execute(&plan)
    })
    .await
    .map_err(RuntimeFailure::internal)??;
    app.restart();
}

#[tauri::command]
pub fn defer_migration(foundation: State<'_, Arc<DesktopFoundation>>) {
    foundation
        .migration_deferred
        .store(true, std::sync::atomic::Ordering::SeqCst);
}

#[tauri::command]
pub async fn bootstrap_runtime(
    state: State<'_, Arc<DesktopCoordinator>>,
) -> Result<BootstrapReply, RuntimeFailure> {
    state.inner().start().await
}

#[tauri::command]
pub async fn cancel_runtime(
    state: State<'_, Arc<DesktopCoordinator>>,
) -> Result<(), RuntimeFailure> {
    state.inner().cancel().await
}

#[tauri::command]
pub async fn repair_runtime(
    state: State<'_, Arc<DesktopCoordinator>>,
) -> Result<BootstrapReply, RuntimeFailure> {
    state.inner().repair().await
}

#[tauri::command]
pub async fn export_diagnostics(
    state: State<'_, Arc<DesktopCoordinator>>,
    generation_id: Option<String>,
) -> Result<String, RuntimeFailure> {
    if let Some(generation_id) = generation_id {
        state.validate_generation(&generation_id).await?;
    }
    state.inner().export_diagnostics().await
}

#[tauri::command]
pub async fn switch_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    profile_id: uuid::Uuid,
    generation_id: Option<String>,
) -> Result<BootstrapReply, RuntimeFailure> {
    if let Some(generation_id) = generation_id {
        state.validate_generation(&generation_id).await?;
    }
    state.inner().switch_profile(profile_id).await
}

#[tauri::command]
pub async fn list_profiles(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
) -> Result<ProfileListSnapshot, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation.profiles.snapshot()
}

#[tauri::command]
pub async fn list_project_metadata(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    metadata_repository(&foundation)?.snapshot()
}

#[tauri::command]
pub async fn patch_project_metadata(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    workspace_id: String,
    patch: ProjectMetadataPatch,
) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    metadata_repository(&foundation)?.patch(&workspace_id, patch)
}

#[tauri::command]
pub async fn remove_project_metadata(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    workspace_id: String,
) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    metadata_repository(&foundation)?.remove(&workspace_id)
}

#[tauri::command]
pub async fn recycle_project_directory(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    workspace_id: String,
) -> Result<std::path::PathBuf, RuntimeFailure> {
    coordinator.validate_generation(&generation_id).await?;
    let profile = active_profile(&foundation)?;
    let target = resolve_registered_workspace(&profile.data_root, &workspace_id)?;
    let protected = ProtectedRoots::detect(
        &target,
        foundation.paths.active_root.clone(),
        profile.data_root,
        foundation.paths.runtime.clone(),
    )?;
    validate_recycle_target(&target, &protected)?;

    let platform = Arc::clone(&foundation.platform);
    let recycled = target.clone();
    tokio::task::spawn_blocking(move || platform.move_to_recycle_bin(&target))
        .await
        .map_err(RuntimeFailure::internal)??;
    Ok(recycled)
}

#[tauri::command]
pub async fn preview_default_project_directory(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    idea: String,
) -> Result<ProjectLocationPreview, RuntimeFailure> {
    coordinator.validate_generation(&generation_id).await?;
    active_profile(&foundation)?;
    let documents = foundation.platform.documents_dir()?;
    tokio::task::spawn_blocking(move || preview_project_location(&idea, &documents))
        .await
        .map_err(RuntimeFailure::internal)?
}

#[tauri::command]
pub async fn create_default_project_directory(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    project_name: String,
) -> Result<std::path::PathBuf, RuntimeFailure> {
    coordinator.validate_generation(&generation_id).await?;
    active_profile(&foundation)?;
    let documents = foundation.platform.documents_dir()?;
    tokio::task::spawn_blocking(move || create_project_location(&project_name, &documents))
        .await
        .map_err(RuntimeFailure::internal)?
}

#[tauri::command]
pub async fn create_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    draft: ProfileDraftInput,
) -> Result<ProfileRecord, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation.profiles.create(draft.into())
}

#[tauri::command]
pub async fn update_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    profile_id: uuid::Uuid,
    expected_revision: u64,
    patch: ProfilePatchInput,
) -> Result<ProfileRecord, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation
        .profiles
        .update(&profile_id, expected_revision, patch.into())
}

#[tauri::command]
pub async fn duplicate_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    profile_id: uuid::Uuid,
    draft: ProfileDraftInput,
) -> Result<ProfileRecord, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation.profiles.duplicate(&profile_id, draft.into())
}

#[tauri::command]
pub async fn delete_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    profile_id: uuid::Uuid,
) -> Result<(), RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation.profiles.delete(&profile_id)
}

#[tauri::command]
pub async fn open_external_https(
    app: AppHandle,
    state: State<'_, Arc<DesktopCoordinator>>,
    generation_id: String,
    url: String,
) -> Result<(), RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    let url = crate::navigation::validated_https(&url)?;
    app.opener()
        .open_url(url.as_str(), None::<&str>)
        .map_err(RuntimeFailure::internal)
}

#[tauri::command]
pub fn open_user_data(
    app: AppHandle,
    foundation: State<'_, Arc<DesktopFoundation>>,
) -> Result<(), RuntimeFailure> {
    let root = foundation
        .paths
        .active_root
        .canonicalize()
        .map_err(RuntimeFailure::internal)?;
    app.opener()
        .open_path(root.to_string_lossy().into_owned(), None::<&str>)
        .map_err(RuntimeFailure::internal)
}

#[tauri::command]
pub async fn restart_runtime(
    state: State<'_, Arc<DesktopCoordinator>>,
) -> Result<BootstrapReply, RuntimeFailure> {
    state.inner().restart().await
}

#[tauri::command]
pub async fn orderly_quit(
    app: AppHandle,
    state: State<'_, Arc<DesktopCoordinator>>,
) -> Result<(), RuntimeFailure> {
    state.inner().shutdown().await?;
    // Let the invoke response reach the caller before the WebView and its driver
    // disappear. This also gives packaged E2E teardown a chance to close cleanly.
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        app.exit(0);
    });
    Ok(())
}

#[tauri::command]
pub fn hide_window(window: WebviewWindow) -> Result<(), String> {
    window.hide().map_err(|cause| cause.to_string())
}

#[tauri::command]
pub fn minimize_window(window: WebviewWindow) -> Result<(), String> {
    window.minimize().map_err(|cause| cause.to_string())
}

#[tauri::command]
pub fn toggle_maximize_window(window: WebviewWindow) -> Result<(), String> {
    if window.is_maximized().map_err(|cause| cause.to_string())? {
        window.unmaximize().map_err(|cause| cause.to_string())
    } else {
        window.maximize().map_err(|cause| cause.to_string())
    }
}

#[tauri::command]
pub fn start_drag(window: WebviewWindow) -> Result<(), String> {
    window.start_dragging().map_err(|cause| cause.to_string())
}
