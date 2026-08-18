use tauri::{AppHandle, Manager};
use url::Url;

use crate::runtime::RuntimeFailure;

pub fn navigate_to_runtime(
    app: &AppHandle,
    mut renderer: Url,
    expected_port: u16,
    session_token: &str,
) -> Result<(), RuntimeFailure> {
    if renderer.scheme() != "http"
        || renderer.host_str() != Some("127.0.0.1")
        || renderer.port() != Some(expected_port)
    {
        return Err(RuntimeFailure::internal("拒绝导航到非受管 DSH 地址"));
    }
    renderer.query_pairs_mut()
        .append_pair("dsh-desktop-mode", "advanced")
        .append_pair("dsh-desktop-platform", if cfg!(target_os = "macos") { "darwin" } else { "win32" })
        .append_pair("dsh-desktop-token", session_token);
    let window = app.get_webview_window("main").ok_or_else(|| RuntimeFailure::internal("主窗口不存在"))?;
    window.navigate(renderer).map_err(RuntimeFailure::internal)?;
    window.show().map_err(RuntimeFailure::internal)?;
    window.set_focus().map_err(RuntimeFailure::internal)
}
