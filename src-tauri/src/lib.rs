mod commands;
mod runtime;
mod window;

use std::sync::Arc;

use runtime::RuntimeManager;
use tauri::Manager;

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let manager = RuntimeManager::new(app.handle().clone())
                .map_err(|cause| Box::<dyn std::error::Error>::from(cause))?;
            app.manage(manager);
            if let Some(window) = app.get_webview_window("main") {
                window.show()?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::bootstrap_runtime,
            commands::cancel_runtime,
            commands::repair_runtime,
            commands::export_diagnostics,
            commands::close_window,
            commands::minimize_window,
            commands::toggle_maximize_window,
            commands::start_drag,
        ]);

    let app = builder
        .build(tauri::generate_context!())
        .expect("failed to build DeepSeek Harness Desktop");
    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
            if let Some(manager) = app_handle.try_state::<Arc<RuntimeManager>>() {
                let manager = Arc::clone(manager.inner());
                tauri::async_runtime::block_on(async move { manager.shutdown().await });
            }
        }
    });
}
