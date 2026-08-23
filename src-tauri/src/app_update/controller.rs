use std::{path::PathBuf, sync::Arc};

use chrono::Utc;
use tauri::{AppHandle, Emitter};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_updater::Update;
#[cfg(not(target_os = "macos"))]
use tauri_plugin_updater::UpdaterExt;
use tokio::sync::Mutex;

#[cfg(target_os = "macos")]
use super::manual::fetch_manual_update;
use super::model::{
    AppUpdateAction, AppUpdateEvent, AppUpdateFailure, AppUpdateMode, AppUpdateReceipt,
    AppUpdateSource, AppUpdateState, UpdateInfo,
};
use crate::{
    desktop::DesktopCoordinator,
    storage::atomic_json::{read_optional, write_atomic},
};

#[cfg_attr(target_os = "macos", allow(dead_code))]
enum PendingUpdate {
    Signed {
        update: Update,
        bytes: Option<Vec<u8>>,
    },
    ManualDmg {
        info: UpdateInfo,
        download_url: url::Url,
    },
}

pub struct AppUpdateController {
    app: AppHandle,
    state: Mutex<AppUpdateState>,
    source: Mutex<AppUpdateSource>,
    pending: Mutex<Option<PendingUpdate>>,
    install_on_exit: std::sync::atomic::AtomicBool,
    receipt_path: PathBuf,
}

impl AppUpdateController {
    pub fn new(app: AppHandle, receipt_path: PathBuf) -> Arc<Self> {
        Arc::new(Self {
            app,
            state: Mutex::new(AppUpdateState::Idle),
            source: Mutex::new(AppUpdateSource::Automatic),
            pending: Mutex::new(None),
            install_on_exit: std::sync::atomic::AtomicBool::new(false),
            receipt_path,
        })
    }

    pub async fn snapshot(&self) -> AppUpdateState {
        self.state.lock().await.clone()
    }

    pub async fn check(
        &self,
        source: AppUpdateSource,
    ) -> Result<Option<UpdateInfo>, AppUpdateFailure> {
        *self.source.lock().await = source;
        self.apply(AppUpdateAction::Check).await?;
        let result: Result<Option<UpdateInfo>, AppUpdateFailure> = async {
            let Some(info) = self.check_platform_update().await? else {
                self.apply(AppUpdateAction::NoUpdate).await?;
                return Ok(None);
            };
            self.apply(AppUpdateAction::Found(info.clone())).await?;
            Ok(Some(info))
        }
        .await;
        if let Err(cause) = &result {
            self.fail(cause.clone()).await;
        }
        result
    }

    pub async fn download(&self) -> Result<(), AppUpdateFailure> {
        self.apply(AppUpdateAction::Download).await?;
        let update = self
            .pending
            .lock()
            .await
            .as_ref()
            .and_then(|pending| match pending {
                PendingUpdate::Signed { update, .. } => Some(update.clone()),
                PendingUpdate::ManualDmg { .. } => None,
            })
            .ok_or_else(|| AppUpdateFailure::new("missing-update", "没有可下载的应用更新"))?;
        let result = update
            .download(|_, _| {}, || {})
            .await
            .map_err(|cause| failure("download", cause));
        match result {
            Ok(bytes) => {
                let stored = {
                    let mut guard = self.pending.lock().await;
                    match guard.as_mut() {
                        Some(PendingUpdate::Signed { bytes: stored, .. }) => {
                            *stored = Some(bytes);
                            true
                        }
                        _ => false,
                    }
                };
                if !stored {
                    let cause = AppUpdateFailure::new("missing-update", "应用更新已失效");
                    self.fail(cause.clone()).await;
                    return Err(cause);
                }
                self.apply(AppUpdateAction::DownloadReady).await
            }
            Err(cause) => {
                self.fail(cause.clone()).await;
                Err(cause)
            }
        }
    }

    pub async fn install_now(&self, desktop: &DesktopCoordinator) -> Result<(), AppUpdateFailure> {
        self.apply(AppUpdateAction::InstallNow).await?;
        let result: Result<(), AppUpdateFailure> = async {
            desktop
                .shutdown_barrier()
                .await
                .map_err(|cause| failure("shutdown", cause))?;
            self.install_pending().await?;
            self.apply(AppUpdateAction::DownloadReady).await?;
            self.app.restart();
        }
        .await;
        if let Err(cause) = &result {
            self.fail(cause.clone()).await;
        }
        result
    }

    pub async fn install_on_exit(&self) -> Result<(), AppUpdateFailure> {
        if !matches!(self.snapshot().await, AppUpdateState::Ready(_)) {
            return Err(AppUpdateFailure::new(
                "invalid-transition",
                "只有已完成安全下载的更新才能安排退出时安装",
            ));
        }
        self.install_on_exit
            .store(true, std::sync::atomic::Ordering::SeqCst);
        Ok(())
    }

    pub fn should_install_on_exit(&self) -> bool {
        self.install_on_exit
            .load(std::sync::atomic::Ordering::SeqCst)
    }

    pub async fn open_manual_download(&self) -> Result<(), AppUpdateFailure> {
        let url = self
            .pending
            .lock()
            .await
            .as_ref()
            .and_then(|pending| match pending {
                PendingUpdate::ManualDmg { info, download_url }
                    if info.mode == AppUpdateMode::ManualDmg =>
                {
                    Some(download_url.clone())
                }
                _ => None,
            })
            .ok_or_else(|| {
                AppUpdateFailure::new("missing-manual-update", "没有经过校验的 macOS DMG 下载地址")
            })?;
        self.app
            .opener()
            .open_url(url.as_str(), None::<&str>)
            .map_err(|cause| failure("open-download", cause))
    }

    pub async fn install_scheduled_after_shutdown(&self) -> Result<(), AppUpdateFailure> {
        if !self
            .install_on_exit
            .swap(false, std::sync::atomic::Ordering::SeqCst)
        {
            return Ok(());
        }
        self.apply(AppUpdateAction::InstallNow).await?;
        let result = self.install_pending().await;
        if let Err(cause) = &result {
            self.fail(cause.clone()).await;
        }
        result
    }

    pub fn take_completed_receipt(&self) -> Result<Option<AppUpdateReceipt>, AppUpdateFailure> {
        let Some(receipt) = read_optional::<AppUpdateReceipt>(&self.receipt_path)
            .map_err(|cause| failure("receipt", cause))?
        else {
            return Ok(None);
        };
        if receipt.target_version != env!("CARGO_PKG_VERSION") {
            return Ok(None);
        }
        std::fs::remove_file(&self.receipt_path).map_err(|cause| failure("receipt", cause))?;
        Ok(Some(receipt))
    }

    pub async fn defer(&self) -> Result<(), AppUpdateFailure> {
        self.apply(AppUpdateAction::Defer).await?;
        self.install_on_exit
            .store(false, std::sync::atomic::Ordering::SeqCst);
        *self.pending.lock().await = None;
        Ok(())
    }

    async fn apply(&self, action: AppUpdateAction) -> Result<(), AppUpdateFailure> {
        let snapshot = {
            let mut state = self.state.lock().await;
            *state = state.clone().transition(action)?;
            state.clone()
        };
        let source = *self.source.lock().await;
        let _ = self.app.emit(
            "app-update-event",
            AppUpdateEvent {
                source,
                state: snapshot,
            },
        );
        Ok(())
    }

    async fn install_pending(&self) -> Result<(), AppUpdateFailure> {
        let pending = self
            .pending
            .lock()
            .await
            .take()
            .ok_or_else(|| AppUpdateFailure::new("missing-update", "应用更新已失效"))?;
        let PendingUpdate::Signed { update, bytes } = pending else {
            return Err(AppUpdateFailure::new(
                "manual-update",
                "macOS 手动更新不能进入应用内安装流程",
            ));
        };
        let bytes = bytes
            .ok_or_else(|| AppUpdateFailure::new("missing-download", "应用更新尚未下载完成"))?;
        let receipt = AppUpdateReceipt {
            previous_version: update.current_version.clone(),
            target_version: update.version.clone(),
            installed_at: Utc::now(),
        };
        write_atomic(&self.receipt_path, &receipt).map_err(|cause| failure("receipt", cause))?;
        update
            .install(bytes)
            .map_err(|cause| failure("install", cause))
    }

    #[cfg(target_os = "macos")]
    async fn check_platform_update(&self) -> Result<Option<UpdateInfo>, AppUpdateFailure> {
        let Some(info) = fetch_manual_update().await? else {
            *self.pending.lock().await = None;
            return Ok(None);
        };
        let download_url = info
            .download_url
            .as_deref()
            .ok_or_else(|| AppUpdateFailure::new("manifest", "macOS 更新缺少 DMG 地址"))?
            .parse()
            .map_err(|cause| failure("manifest", cause))?;
        *self.pending.lock().await = Some(PendingUpdate::ManualDmg {
            info: info.clone(),
            download_url,
        });
        Ok(Some(info))
    }

    #[cfg(not(target_os = "macos"))]
    async fn check_platform_update(&self) -> Result<Option<UpdateInfo>, AppUpdateFailure> {
        let updater = self
            .app
            .updater()
            .map_err(|cause| failure("configuration", cause))?;
        let Some(update) = updater
            .check()
            .await
            .map_err(|cause| failure("check", cause))?
        else {
            *self.pending.lock().await = None;
            return Ok(None);
        };
        let info = UpdateInfo {
            version: update.version.clone(),
            notes: update.body.clone(),
            size: update.raw_json.get("size").and_then(|size| size.as_u64()),
            mode: AppUpdateMode::InApp,
            download_url: None,
            developer_id_signed: None,
            notarized: None,
        };
        *self.pending.lock().await = Some(PendingUpdate::Signed {
            update,
            bytes: None,
        });
        Ok(Some(info))
    }

    async fn fail(&self, failure: AppUpdateFailure) {
        let _ = self.apply(AppUpdateAction::Fail(failure)).await;
    }
}

fn failure(code: &str, cause: impl std::fmt::Display) -> AppUpdateFailure {
    AppUpdateFailure::new(code, cause.to_string())
}
