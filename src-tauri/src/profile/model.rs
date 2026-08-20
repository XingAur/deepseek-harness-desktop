use std::path::PathBuf;

use chrono::{DateTime, Utc};
use semver::Version;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum PermissionMode {
    ReadOnly,
    #[default]
    WorkspaceWrite,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProfileRecord {
    pub id: Uuid,
    pub name: String,
    pub data_root: PathBuf,
    pub permission_mode: PermissionMode,
    pub revision: u64,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Clone, Debug)]
pub struct ProfileDraft {
    pub name: String,
    pub data_root: PathBuf,
    pub permission_mode: PermissionMode,
}

impl ProfileDraft {
    pub fn named(name: impl Into<String>, data_root: PathBuf) -> Self {
        Self {
            name: name.into(),
            data_root,
            permission_mode: PermissionMode::WorkspaceWrite,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct ProfilePatch {
    pub name: Option<String>,
    pub data_root: Option<PathBuf>,
    pub permission_mode: Option<PermissionMode>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProfileSelection {
    pub profile_id: Uuid,
    pub revision: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ActivationReason {
    Startup,
    UserSwitch,
    ProfileUpdated,
    Recovery,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PendingActivation {
    pub target: ProfileSelection,
    pub previous: Option<ProfileSelection>,
    pub generation_id: String,
    pub reason: ActivationReason,
    pub requested_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct LastKnownGood {
    pub profile_id: Uuid,
    pub revision: u64,
    pub runtime_version: Version,
    pub verified_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct FailedActivation {
    pub target: ProfileSelection,
    pub generation_id: String,
    pub phase: String,
    pub cause: String,
    pub failed_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProfileState {
    pub selected_profile: Option<ProfileSelection>,
    pub pending: Option<PendingActivation>,
    pub last_known_good: Option<LastKnownGood>,
    pub failed_attempts: Vec<FailedActivation>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ProfileStatus {
    Active,
    Switching,
    Recovered,
    Invalid,
    Ready,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProfileSummary {
    #[serde(flatten)]
    pub profile: ProfileRecord,
    pub runtime_version: Option<Version>,
    pub status: ProfileStatus,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProfileListSnapshot {
    pub selected_profile_id: Option<Uuid>,
    pub pending_profile_id: Option<Uuid>,
    pub last_known_good_profile_id: Option<Uuid>,
    pub profiles: Vec<ProfileSummary>,
}
