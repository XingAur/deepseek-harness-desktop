use std::sync::Arc;

use tauri::State;

use crate::runtime::{BootstrapReply, RuntimeFailure, RuntimeManager};

#[tauri::command]
pub async fn bootstrap_runtime(state: State<'_, Arc<RuntimeManager>>) -> Result<BootstrapReply, RuntimeFailure> {
    state.inner().bootstrap(false).await
}

#[tauri::command]
pub async fn cancel_runtime(state: State<'_, Arc<RuntimeManager>>) -> Result<(), RuntimeFailure> {
    state.inner().cancel().await
}

#[tauri::command]
pub async fn repair_runtime(state: State<'_, Arc<RuntimeManager>>) -> Result<BootstrapReply, RuntimeFailure> {
    state.inner().bootstrap(true).await
}

#[tauri::command]
pub async fn export_diagnostics(state: State<'_, Arc<RuntimeManager>>) -> Result<String, RuntimeFailure> {
    state.inner().export_diagnostics().await
}
