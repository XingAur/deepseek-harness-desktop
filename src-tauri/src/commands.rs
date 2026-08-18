use std::sync::Arc;

use tauri::{State, WebviewWindow};

use crate::runtime::{BootstrapReply, RuntimeFailure, RuntimeManager};

#[tauri::command]
pub async fn bootstrap_runtime(
    state: State<'_, Arc<RuntimeManager>>,
) -> Result<BootstrapReply, RuntimeFailure> {
    state.inner().bootstrap(false).await
}

#[tauri::command]
pub async fn cancel_runtime(state: State<'_, Arc<RuntimeManager>>) -> Result<(), RuntimeFailure> {
    state.inner().cancel().await
}

#[tauri::command]
pub async fn repair_runtime(
    state: State<'_, Arc<RuntimeManager>>,
) -> Result<BootstrapReply, RuntimeFailure> {
    state.inner().bootstrap(true).await
}

#[tauri::command]
pub async fn export_diagnostics(
    state: State<'_, Arc<RuntimeManager>>,
) -> Result<String, RuntimeFailure> {
    state.inner().export_diagnostics().await
}

#[tauri::command]
pub fn close_window(window: WebviewWindow) -> Result<(), String> {
    window.close().map_err(|cause| cause.to_string())
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
