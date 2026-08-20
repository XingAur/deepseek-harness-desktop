use std::{path::PathBuf, sync::Arc};

use chrono::Utc;
use tauri::{AppHandle, Emitter};
use tauri_plugin_updater::{Update, UpdaterExt};
use tokio::sync::Mutex;

use super::model::{
    AppUpdateAction, AppUpdateEvent, AppUpdateFailure, AppUpdateReceipt, AppUpdateSource,
    AppUpdateState, UpdateInfo,
};
use crate::{
    desktop::DesktopCoordinator,
    storage::atomic_json::{read_optional, write_atomic},
};

struct PendingUpdate {
    update: Update,
    bytes: Option<Vec<u8>>,
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
            let updater = self
                .app
                .updater()
                .map_err(|cause| failure("configuration", cause))?;
            let Some(update) = updater
                .check()
                .await
                .map_err(|cause| failure("check", cause))?
            else {
                self.apply(AppUpdateAction::NoUpdate).await?;
                return Ok(None);
            };
            let info = UpdateInfo {
                version: update.version.clone(),
                notes: update.body.clone(),
                size: update.raw_json.get("size").and_then(|size| size.as_u64()),
            };
            *self.pending.lock().await = Some(PendingUpdate {
                update,
                bytes: None,
            });
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
            .map(|pending| pending.update.clone())
            .ok_or_else(|| AppUpdateFailure::new("missing-update", "没有可下载的应用更新"))?;
        let result = update
            .download(|_, _| {}, || {})
            .await
            .map_err(|cause| failure("download", cause));
        match result {
            Ok(bytes) => {
                let stored = {
                    let mut guard = self.pending.lock().await;
                    guard.as_mut().is_some_and(|pending| {
                        pending.bytes = Some(bytes);
                        true
                    })
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
        let _ = self.app.emit("app-update-event", AppUpdateEvent { source, state: snapshot });
        Ok(())
    }

    async fn install_pending(&self) -> Result<(), AppUpdateFailure> {
        let pending = self
            .pending
            .lock()
            .await
            .take()
            .ok_or_else(|| AppUpdateFailure::new("missing-update", "应用更新已失效"))?;
        let bytes = pending
            .bytes
            .ok_or_else(|| AppUpdateFailure::new("missing-download", "应用更新尚未下载完成"))?;
        let receipt = AppUpdateReceipt {
            previous_version: pending.update.current_version.clone(),
            target_version: pending.update.version.clone(),
            installed_at: Utc::now(),
        };
        write_atomic(&self.receipt_path, &receipt).map_err(|cause| failure("receipt", cause))?;
        pending
            .update
            .install(bytes)
            .map_err(|cause| failure("install", cause))
    }

    async fn fail(&self, failure: AppUpdateFailure) {
        let _ = self.apply(AppUpdateAction::Fail(failure)).await;
    }
}

fn failure(code: &str, cause: impl std::fmt::Display) -> AppUpdateFailure {
    AppUpdateFailure::new(code, cause.to_string())
}
