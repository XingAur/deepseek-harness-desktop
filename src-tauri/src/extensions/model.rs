use semver::Version;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ExtensionKind {
    Plugin,
    Skill,
    Mcp,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ExtensionSourceKind {
    Builtin,
    Local,
    Git,
    Registry,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ExtensionStatus {
    Staged,
    Enabled,
    Disabled,
    Quarantined,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ExtensionManifest {
    pub schema_version: u32,
    pub id: String,
    pub kind: ExtensionKind,
    pub source: ExtensionSourceKind,
    pub version: Version,
    pub protocol_range: String,
    pub integrity_sha256: String,
    pub entrypoint: String,
    pub platforms: Vec<String>,
    pub capabilities: Vec<String>,
    pub credential_references: Vec<String>,
    pub health_check: HealthCheck,
    pub update: UpdateMetadata,
    pub rollback: RollbackMetadata,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HealthCheck {
    pub kind: String,
    pub timeout_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UpdateMetadata {
    pub source: String,
    pub exact_version: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RollbackMetadata {
    pub last_known_good_version: Option<Version>,
    pub quarantine_on_crash_loop: bool,
}
