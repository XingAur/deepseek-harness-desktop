use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum AppUpdateSource {
    Automatic,
    Manual,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum AppUpdateMode {
    InApp,
    ManualDmg,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AppUpdateEvent {
    pub source: AppUpdateSource,
    pub state: AppUpdateState,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub version: String,
    pub notes: Option<String>,
    pub size: Option<u64>,
    pub mode: AppUpdateMode,
    pub download_url: Option<String>,
    pub developer_id_signed: Option<bool>,
    pub notarized: Option<bool>,
}

impl UpdateInfo {
    #[cfg(test)]
    pub fn fixture(version: &str) -> Self {
        Self {
            version: version.to_string(),
            notes: Some("稳定性更新".into()),
            size: Some(1024),
            mode: AppUpdateMode::InApp,
            download_url: None,
            developer_id_signed: None,
            notarized: None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AppUpdateFailure {
    pub code: String,
    pub message: String,
    pub recoverable: bool,
}

impl AppUpdateFailure {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            recoverable: true,
        }
    }
}

impl std::fmt::Display for AppUpdateFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for AppUpdateFailure {}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "phase", content = "update", rename_all = "kebab-case")]
pub enum AppUpdateState {
    Idle,
    Checking,
    Available(UpdateInfo),
    Downloading(UpdateInfo),
    Ready(UpdateInfo),
    Installing(UpdateInfo),
    Restarting(UpdateInfo),
    Failed(AppUpdateFailure),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AppUpdateAction {
    Check,
    NoUpdate,
    Found(UpdateInfo),
    Download,
    DownloadReady,
    InstallNow,
    Defer,
    Fail(AppUpdateFailure),
}

impl AppUpdateState {
    pub fn transition(self, action: AppUpdateAction) -> Result<Self, AppUpdateFailure> {
        use AppUpdateAction as Action;
        use AppUpdateState as State;
        match (self, action) {
            (State::Idle | State::Failed(_), Action::Check) => Ok(State::Checking),
            (State::Checking, Action::NoUpdate) => Ok(State::Idle),
            (State::Checking, Action::Found(update)) => Ok(State::Available(update)),
            (State::Available(update), Action::Download) if update.mode == AppUpdateMode::InApp => {
                Ok(State::Downloading(update))
            }
            (State::Downloading(update), Action::DownloadReady) => Ok(State::Ready(update)),
            (State::Ready(update), Action::InstallNow) if update.mode == AppUpdateMode::InApp => {
                Ok(State::Installing(update))
            }
            (State::Installing(update), Action::DownloadReady) => Ok(State::Restarting(update)),
            (State::Available(_) | State::Ready(_), Action::Defer) => Ok(State::Idle),
            (_, Action::Fail(failure)) => Ok(State::Failed(failure)),
            (state, action) => Err(AppUpdateFailure::new(
                "invalid-transition",
                format!("应用更新状态不允许执行 {action:?}: {state:?}"),
            )),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AppUpdateReceipt {
    pub previous_version: String,
    pub target_version: String,
    pub installed_at: chrono::DateTime<chrono::Utc>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cannot_install_before_a_signed_download_is_ready() {
        let state = AppUpdateState::Available(UpdateInfo::fixture("0.2.0"));
        assert!(state.transition(AppUpdateAction::InstallNow).is_err());
    }

    #[test]
    fn signed_download_can_reach_installing_and_restarting() {
        let state = AppUpdateState::Idle
            .transition(AppUpdateAction::Check)
            .unwrap()
            .transition(AppUpdateAction::Found(UpdateInfo::fixture("0.2.0")))
            .unwrap()
            .transition(AppUpdateAction::Download)
            .unwrap()
            .transition(AppUpdateAction::DownloadReady)
            .unwrap()
            .transition(AppUpdateAction::InstallNow)
            .unwrap()
            .transition(AppUpdateAction::DownloadReady)
            .unwrap();
        assert!(matches!(state, AppUpdateState::Restarting(_)));
    }

    #[test]
    fn update_events_serialize_their_source_and_state() {
        let event = AppUpdateEvent {
            source: AppUpdateSource::Manual,
            state: AppUpdateState::Failed(AppUpdateFailure::new("check", "offline")),
        };
        let value = serde_json::to_value(event).unwrap();
        assert_eq!(value["source"], "manual");
        assert_eq!(value["state"]["phase"], "failed");
    }

    #[test]
    fn manual_dmg_cannot_enter_signed_download_or_install_transitions() {
        let update = UpdateInfo {
            version: "0.2.0".into(),
            notes: Some("稳定性更新".into()),
            size: Some(1024),
            mode: AppUpdateMode::ManualDmg,
            download_url: Some("https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.2.0/app_0.2.0_aarch64.dmg".into()),
            developer_id_signed: Some(false),
            notarized: Some(false),
        };
        assert!(
            AppUpdateState::Available(update.clone())
                .transition(AppUpdateAction::Download)
                .is_err()
        );
        assert!(
            AppUpdateState::Ready(update)
                .transition(AppUpdateAction::InstallNow)
                .is_err()
        );
    }
}
