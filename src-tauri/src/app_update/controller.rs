use std::{path::PathBuf, sync::Arc};

use chrono::Utc;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_updater::Update;
#[cfg(not(target_os = "macos"))]
use tauri_plugin_updater::UpdaterExt;
use tokio::sync::Mutex;

#[cfg(target_os = "macos")]
use super::manual::fetch_manual_update;
use super::manual::download_manual_dmg;
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
            .map_err(download_failure);
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
        let (info, url) = self
            .pending
            .lock()
            .await
            .as_ref()
            .and_then(|pending| match pending {
                PendingUpdate::ManualDmg { info, download_url }
                    if info.mode == AppUpdateMode::ManualDmg =>
                {
                    Some((info.clone(), download_url.clone()))
                }
                _ => None,
            })
            .ok_or_else(|| {
                AppUpdateFailure::new("missing-manual-update", "没有经过校验的 macOS DMG 下载地址")
            })?;
        let expected_sha256 = info
            .sha256
            .as_deref()
            .ok_or_else(|| AppUpdateFailure::new("manifest", "macOS 更新缺少 SHA-256"))?;
        let expected_size = info
            .size
            .ok_or_else(|| AppUpdateFailure::new("manifest", "macOS 更新缺少文件大小"))?;
        let update_dir = self
            .app
            .path()
            .app_data_dir()
            .map_err(|cause| failure("download-file", cause))?
            .join("updates");
        let local_path = download_manual_dmg(
            &url,
            expected_sha256,
            expected_size,
            &update_dir,
        )
        .await?;
        self.app
            .opener()
            .open_path(local_path.to_string_lossy().into_owned(), None::<&str>)
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
        let mut builder = self.app.updater_builder();
        // tauri-plugin-updater 未启用 reqwest 的 system-proxy 特性，读不到
        // Windows“系统代理”；这里读取注册表代理并显式注入，让检查与下载
        // 都走该代理。设置显式代理后 reqwest 不再回退到环境变量代理。
        if let Some(proxy) = crate::platform::updater_proxy() {
            builder = builder.proxy(proxy);
        }
        let updater = builder
            .build()
            .map_err(|cause| failure("configuration", cause))?;
        let Some(update) = updater
            .check()
            .await
            .map_err(check_failure)?
        else {
            *self.pending.lock().await = None;
            return Ok(None);
        };
        let info = UpdateInfo {
            version: update.version.clone(),
            notes: update.body.clone(),
            size: update.raw_json.get("size").and_then(|size| size.as_u64()),
            sha256: None,
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

#[cfg_attr(target_os = "macos", allow(dead_code))]
fn check_failure(cause: tauri_plugin_updater::Error) -> AppUpdateFailure {
    match cause {
        tauri_plugin_updater::Error::Reqwest(_) => AppUpdateFailure::new(
            "check-network",
            format!("无法连接更新服务器，请检查网络或系统代理设置（{cause}）"),
        ),
        tauri_plugin_updater::Error::ReleaseNotFound | tauri_plugin_updater::Error::Serialization(_) => {
            AppUpdateFailure::new(
                "check-manifest",
                format!("更新服务器返回的清单不可用，请稍后重试（{cause}）"),
            )
        }
        _ => failure("check", cause),
    }
}

#[cfg_attr(target_os = "macos", allow(dead_code))]
fn download_failure(cause: tauri_plugin_updater::Error) -> AppUpdateFailure {
    match cause {
        tauri_plugin_updater::Error::Reqwest(_) => AppUpdateFailure::new(
            "download-network",
            format!("无法下载更新，请检查网络或系统代理设置（{cause}）"),
        ),
        tauri_plugin_updater::Error::Network(_) => AppUpdateFailure::new(
            "download-http",
            format!("更新下载失败，请稍后重试（{cause}）"),
        ),
        tauri_plugin_updater::Error::Minisign(_)
        | tauri_plugin_updater::Error::Base64(_)
        | tauri_plugin_updater::Error::SignatureUtf8(_) => AppUpdateFailure::new(
            "download-signature",
            "更新包签名校验失败，为安全起见已中止安装",
        ),
        _ => failure("download", cause),
    }
}

#[cfg(all(test, not(target_os = "macos")))]
mod failure_tests {
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use minisign_verify::PublicKey;

    use super::{check_failure, download_failure};

    fn check_cause(cause: tauri_plugin_updater::Error) -> (String, String) {
        let failure = check_failure(cause);
        (failure.code, failure.message)
    }

    fn download_cause(cause: tauri_plugin_updater::Error) -> (String, String) {
        let failure = download_failure(cause);
        (failure.code, failure.message)
    }

    #[test]
    fn manifest_failures_are_classified_separately() {
        for cause in [
            tauri_plugin_updater::Error::ReleaseNotFound,
            tauri_plugin_updater::Error::Serialization(serde_json::from_str::<u8>("x").unwrap_err()),
        ] {
            let (code, _) = check_cause(cause);
            assert_eq!(code, "check-manifest");
        }
    }

    #[test]
    fn download_status_failures_report_the_http_error() {
        let (code, message) = download_cause(tauri_plugin_updater::Error::Network(
            "Download request failed with status: 403".into(),
        ));
        assert_eq!(code, "download-http");
        assert!(message.contains("下载失败"));
    }

    #[test]
    fn signature_failures_abort_with_a_dedicated_code() {
        for cause in [
            tauri_plugin_updater::Error::SignatureUtf8("not-base64".into()),
            tauri_plugin_updater::Error::Base64(STANDARD.decode("!!!").unwrap_err()),
            tauri_plugin_updater::Error::Minisign(PublicKey::decode("garbage").unwrap_err()),
        ] {
            let (code, message) = download_cause(cause);
            assert_eq!(code, "download-signature");
            assert!(message.contains("签名校验失败"));
        }
    }

    #[test]
    fn unknown_causes_keep_the_existing_codes() {
        assert_eq!(
            check_cause(tauri_plugin_updater::Error::EmptyEndpoints).0,
            "check"
        );
        assert_eq!(
            download_cause(tauri_plugin_updater::Error::EmptyEndpoints).0,
            "download"
        );
    }
}
