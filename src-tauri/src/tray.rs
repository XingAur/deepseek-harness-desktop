use std::{future::Future, path::PathBuf, pin::Pin, sync::Arc};

use tauri::{
    AppHandle, Listener, WebviewWindow,
    menu::{MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
};
use tauri_plugin_opener::OpenerExt;

use crate::{
    DesktopFoundation,
    app_update::{AppUpdateController, model::AppUpdateSource},
    desktop::DesktopCoordinator,
    runtime::{activation::read_active_manifest, model::RuntimeFailure, paths::RuntimePaths},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TrayAction {
    Hide,
    ShowHide,
    RestartRuntime,
    CheckAppUpdate,
    ExportDiagnostics,
    OpenUserData,
    Quit,
}

impl TrayAction {
    fn from_menu_id(id: &str) -> Option<Self> {
        match id {
            "show-hide" => Some(Self::ShowHide),
            "restart-runtime" => Some(Self::RestartRuntime),
            "check-app-update" => Some(Self::CheckAppUpdate),
            "export-diagnostics" => Some(Self::ExportDiagnostics),
            "open-user-data" => Some(Self::OpenUserData),
            "quit" => Some(Self::Quit),
            _ => None,
        }
    }
}

pub trait TrayWindow: Send + Sync {
    fn is_visible(&self) -> Result<bool, RuntimeFailure>;
    fn show(&self) -> Result<(), RuntimeFailure>;
    fn hide(&self) -> Result<(), RuntimeFailure>;
    fn focus(&self) -> Result<(), RuntimeFailure>;
}

pub trait TrayDesktop: Send + Sync {
    fn restart<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>>;
    fn check_app_update<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>>;
    fn export_diagnostics<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>>;
    fn open_user_data(&self) -> Result<(), RuntimeFailure>;
    fn shutdown<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>>;
}

pub trait TrayApplication: Send + Sync {
    fn exit(&self);
}

pub struct TrayController {
    window: Arc<dyn TrayWindow>,
    desktop: Arc<dyn TrayDesktop>,
    app: Arc<dyn TrayApplication>,
}

impl TrayController {
    pub fn new(
        window: Arc<dyn TrayWindow>,
        desktop: Arc<dyn TrayDesktop>,
        app: Arc<dyn TrayApplication>,
    ) -> Arc<Self> {
        Arc::new(Self {
            window,
            desktop,
            app,
        })
    }

    pub async fn handle(&self, action: TrayAction) -> Result<(), RuntimeFailure> {
        match action {
            TrayAction::Hide => self.window.hide(),
            TrayAction::ShowHide => {
                if self.window.is_visible()? {
                    self.window.hide()
                } else {
                    self.window.show()?;
                    self.window.focus()
                }
            }
            TrayAction::RestartRuntime => self.desktop.restart().await,
            TrayAction::CheckAppUpdate => self.desktop.check_app_update().await,
            TrayAction::ExportDiagnostics => self.desktop.export_diagnostics().await,
            TrayAction::OpenUserData => self.desktop.open_user_data(),
            TrayAction::Quit => {
                self.desktop.shutdown().await?;
                self.app.exit();
                Ok(())
            }
        }
    }
}

struct TauriTrayWindow(WebviewWindow);

impl TrayWindow for TauriTrayWindow {
    fn is_visible(&self) -> Result<bool, RuntimeFailure> {
        self.0.is_visible().map_err(RuntimeFailure::internal)
    }

    fn show(&self) -> Result<(), RuntimeFailure> {
        self.0.show().map_err(RuntimeFailure::internal)
    }

    fn hide(&self) -> Result<(), RuntimeFailure> {
        self.0.hide().map_err(RuntimeFailure::internal)
    }

    fn focus(&self) -> Result<(), RuntimeFailure> {
        self.0.set_focus().map_err(RuntimeFailure::internal)
    }
}

struct TauriTrayDesktop {
    app: AppHandle,
    desktop: Arc<DesktopCoordinator>,
    user_data: PathBuf,
    updates: Arc<AppUpdateController>,
}

impl TrayDesktop for TauriTrayDesktop {
    fn restart<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move { self.desktop.restart().await.map(|_| ()) })
    }

    fn check_app_update<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move {
            self.updates
                .check(AppUpdateSource::Manual)
                .await
                .map(|_| ())
                .map_err(RuntimeFailure::internal)
        })
    }

    fn export_diagnostics<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move {
            let path = self.desktop.export_diagnostics().await?;
            self.app
                .opener()
                .reveal_item_in_dir(path)
                .map_err(RuntimeFailure::internal)
        })
    }

    fn open_user_data(&self) -> Result<(), RuntimeFailure> {
        self.app
            .opener()
            .open_path(self.user_data.to_string_lossy().into_owned(), None::<&str>)
            .map_err(RuntimeFailure::internal)
    }

    fn shutdown<'a>(
        &'a self,
    ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
        Box::pin(async move { self.desktop.shutdown().await })
    }
}

struct TauriTrayApplication(AppHandle);

impl TrayApplication for TauriTrayApplication {
    fn exit(&self) {
        self.0.exit(0);
    }
}

pub fn install(
    app: &tauri::App,
    window: WebviewWindow,
    desktop: Arc<DesktopCoordinator>,
    foundation: Arc<DesktopFoundation>,
    updates: Arc<AppUpdateController>,
) -> tauri::Result<()> {
    let profile = selected_profile_label(&foundation);
    let runtime = active_runtime_label(&foundation);
    let profile_item = MenuItemBuilder::with_id("status-profile", format!("Profile：{profile}"))
        .enabled(false)
        .build(app)?;
    let runtime_item = MenuItemBuilder::with_id("status-runtime", format!("Runtime：{runtime}"))
        .enabled(false)
        .build(app)?;
    let menu = MenuBuilder::new(app)
        .item(&profile_item)
        .item(&runtime_item)
        .separator()
        .text("show-hide", "显示/隐藏窗口")
        .text("restart-runtime", "重启 DeepSeek Harness")
        .text("check-app-update", "检查应用更新")
        .text("export-diagnostics", "导出诊断")
        .text("open-user-data", "打开用户数据目录")
        .separator()
        .text("quit", "退出")
        .build()?;

    let profiles = Arc::clone(&foundation.profiles);
    let dynamic_profile_item = profile_item.clone();
    let dynamic_runtime_item = runtime_item.clone();
    app.listen("desktop-event", move |event| {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(event.payload()) else {
            return;
        };
        if value["kind"] != "generation-active" {
            return;
        }
        if let Some(version) = value["snapshot"]["runtimeVersion"].as_str() {
            let _ = dynamic_runtime_item.set_text(format!("Runtime：v{version}"));
        }
        let Some(profile_id) = value["snapshot"]["profile"]["profileId"]
            .as_str()
            .and_then(|value| uuid::Uuid::parse_str(value).ok())
        else {
            return;
        };
        if let Ok(profile) = profiles.get(&profile_id) {
            let _ = dynamic_profile_item.set_text(format!("Profile：{}", profile.name));
        }
    });

    let app_handle = app.handle().clone();
    let controller = TrayController::new(
        Arc::new(TauriTrayWindow(window.clone())),
        Arc::new(TauriTrayDesktop {
            app: app_handle.clone(),
            desktop,
            user_data: foundation.paths.active_root.clone(),
            updates,
        }),
        Arc::new(TauriTrayApplication(app_handle)),
    );
    let menu_controller = Arc::clone(&controller);
    let icon = app.default_window_icon().cloned();
    let mut builder = TrayIconBuilder::with_id("deepseek-harness")
        .tooltip("DeepSeek Harness Desktop")
        .menu(&menu)
        .on_menu_event(move |_, event| {
            let Some(action) = TrayAction::from_menu_id(event.id().as_ref()) else {
                return;
            };
            let controller = Arc::clone(&menu_controller);
            tauri::async_runtime::spawn(async move {
                let _ = controller.handle(action).await;
            });
        });
    if let Some(icon) = icon {
        builder = builder.icon(icon);
    }
    builder.build(app)?;

    let close_controller = Arc::clone(&controller);
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let controller = Arc::clone(&close_controller);
            tauri::async_runtime::spawn(async move {
                let _ = controller.handle(TrayAction::Hide).await;
            });
        }
    });
    Ok(())
}

fn selected_profile_label(foundation: &DesktopFoundation) -> String {
    let selected = foundation
        .profiles
        .state()
        .ok()
        .and_then(|state| state.selected_profile)
        .and_then(|selection| foundation.profiles.get(&selection.profile_id).ok())
        .or_else(|| foundation.profiles.list().ok()?.into_iter().next());
    selected
        .map(|profile| profile.name)
        .unwrap_or_else(|| "默认".into())
}

fn active_runtime_label(foundation: &DesktopFoundation) -> String {
    RuntimePaths::from_app_paths(&foundation.paths)
        .ok()
        .and_then(|paths| read_active_manifest(&paths).ok().flatten())
        .map(|manifest| format!("v{}", manifest.version))
        .unwrap_or_else(|| "准备中".into())
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    use super::*;

    #[derive(Default)]
    struct FakeWindow {
        visible: AtomicBool,
    }

    impl TrayWindow for FakeWindow {
        fn is_visible(&self) -> Result<bool, RuntimeFailure> {
            Ok(self.visible.load(Ordering::SeqCst))
        }
        fn show(&self) -> Result<(), RuntimeFailure> {
            self.visible.store(true, Ordering::SeqCst);
            Ok(())
        }
        fn hide(&self) -> Result<(), RuntimeFailure> {
            self.visible.store(false, Ordering::SeqCst);
            Ok(())
        }
        fn focus(&self) -> Result<(), RuntimeFailure> {
            Ok(())
        }
    }

    #[derive(Default)]
    struct FakeDesktop {
        shutdowns: AtomicUsize,
    }

    impl TrayDesktop for FakeDesktop {
        fn restart<'a>(
            &'a self,
        ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
            Box::pin(async { Ok(()) })
        }
        fn check_app_update<'a>(
            &'a self,
        ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
            Box::pin(async { Ok(()) })
        }
        fn export_diagnostics<'a>(
            &'a self,
        ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
            Box::pin(async { Ok(()) })
        }
        fn open_user_data(&self) -> Result<(), RuntimeFailure> {
            Ok(())
        }
        fn shutdown<'a>(
            &'a self,
        ) -> Pin<Box<dyn Future<Output = Result<(), RuntimeFailure>> + Send + 'a>> {
            Box::pin(async move {
                self.shutdowns.fetch_add(1, Ordering::SeqCst);
                Ok(())
            })
        }
    }

    #[derive(Default)]
    struct FakeApp {
        exited: AtomicBool,
    }

    impl TrayApplication for FakeApp {
        fn exit(&self) {
            self.exited.store(true, Ordering::SeqCst);
        }
    }

    #[tokio::test]
    async fn quit_drains_generation_while_close_only_hides() {
        let window = Arc::new(FakeWindow {
            visible: AtomicBool::new(true),
        });
        let desktop = Arc::new(FakeDesktop::default());
        let app = Arc::new(FakeApp::default());
        let controller = TrayController::new(window.clone(), desktop.clone(), app.clone());

        controller.handle(TrayAction::Hide).await.unwrap();
        assert!(!window.visible.load(Ordering::SeqCst));
        assert_eq!(desktop.shutdowns.load(Ordering::SeqCst), 0);
        controller.handle(TrayAction::Quit).await.unwrap();
        assert_eq!(desktop.shutdowns.load(Ordering::SeqCst), 1);
        assert!(app.exited.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn show_hide_toggles_without_shutdown() {
        let window = Arc::new(FakeWindow::default());
        let desktop = Arc::new(FakeDesktop::default());
        let app = Arc::new(FakeApp::default());
        let controller = TrayController::new(window.clone(), desktop.clone(), app);
        controller.handle(TrayAction::ShowHide).await.unwrap();
        assert!(window.visible.load(Ordering::SeqCst));
        controller.handle(TrayAction::ShowHide).await.unwrap();
        assert!(!window.visible.load(Ordering::SeqCst));
        assert_eq!(desktop.shutdowns.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn maps_only_known_menu_ids() {
        assert_eq!(TrayAction::from_menu_id("quit"), Some(TrayAction::Quit));
        assert_eq!(TrayAction::from_menu_id("unknown"), None);
    }
}
