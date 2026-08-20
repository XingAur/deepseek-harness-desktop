use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct MigrationCandidate {
    pub source: PathBuf,
    pub target: PathBuf,
    pub bytes: u64,
    pub profiles: usize,
    pub workspaces: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct MigrationPlan {
    pub id: Uuid,
    pub source: PathBuf,
    pub target: PathBuf,
    pub staging: PathBuf,
    pub backup: PathBuf,
    pub required_bytes: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct MigrationReceipt {
    pub backup_path: PathBuf,
    pub staging_path: PathBuf,
    pub manifest_path: PathBuf,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct MigrationManifest<'a> {
    pub id: Uuid,
    pub source: &'a std::path::Path,
    pub target: &'a std::path::Path,
    pub copied_bytes: u64,
    pub completed_at: chrono::DateTime<chrono::Utc>,
}
