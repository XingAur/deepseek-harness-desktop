use std::path::PathBuf;

use chrono::{DateTime, Utc};
use semver::{Version, VersionReq};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::runtime::model::RuntimeTarget;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ProvisioningPhase {
    Checking,
    FetchingManifest,
    Downloading,
    Verifying,
    Extracting,
    Probing,
    Prepared,
    Committing,
    Completed,
    Failed,
    Cancelled,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PreparedProvisioning {
    pub schema_version: u32,
    pub session_id: Uuid,
    pub desktop_version: Version,
    pub target: RuntimeTarget,
    pub runtime_version: Version,
    pub manifest_sha256: String,
    pub payload_sha256: String,
    pub candidate_dir: PathBuf,
    #[serde(default)]
    pub reused_active: bool,
    pub probe_contract_version: u32,
    pub prepared_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProvisioningReceipt {
    pub schema_version: u32,
    pub verifier_version: u32,
    pub session_id: Uuid,
    pub desktop_version: Version,
    pub compatibility_requirement: VersionReq,
    pub target: RuntimeTarget,
    pub runtime_version: Version,
    pub manifest_sha256: String,
    pub payload_sha256: String,
    pub active_dir: PathBuf,
    pub probe_contract_version: u32,
    pub completed_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProvisioningSession {
    pub id: Uuid,
    pub desktop_version: Version,
    pub target: RuntimeTarget,
    pub started_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProbeReceipt {
    pub contract_version: u32,
    pub runtime_version: Version,
    pub completed_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProvisioningEvent {
    pub session_id: Uuid,
    pub phase: ProvisioningPhase,
    pub message: String,
    pub recoverable: bool,
    pub completed: Option<u64>,
    pub total: Option<u64>,
    pub bytes_per_second: Option<u64>,
}

#[cfg(test)]
impl PreparedProvisioning {
    pub fn fixture(
        session: &str,
        version: &str,
        manifest_hash: &str,
        candidate_dir: PathBuf,
    ) -> Self {
        Self {
            schema_version: 1,
            session_id: Uuid::parse_str(match session {
                "session-a" => "00000000-0000-0000-0000-00000000000a",
                _ => "00000000-0000-0000-0000-00000000000b",
            })
            .unwrap(),
            desktop_version: Version::new(0, 1, 0),
            target: RuntimeTarget::WindowsX86_64,
            runtime_version: Version::parse(version).unwrap(),
            manifest_sha256: manifest_hash.to_string(),
            payload_sha256: "payload-a".to_string(),
            candidate_dir,
            reused_active: false,
            probe_contract_version: 1,
            prepared_at: Utc::now(),
        }
    }
}
