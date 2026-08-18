use semver::Version;
use serde::{Deserialize, Serialize};
use url::Url;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum RuntimePhase {
    Checking,
    FetchingManifest,
    Downloading,
    Verifying,
    Activating,
    Starting,
    Ready,
    Cancelled,
    Failed,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum RuntimeFailureCode {
    Network,
    Signature,
    Archive,
    Process,
    HealthTimeout,
    Cancelled,
    Internal,
}

#[derive(Clone, Debug, Serialize, thiserror::Error)]
#[serde(rename_all = "camelCase")]
#[error("{message}")]
pub struct RuntimeFailure {
    pub code: RuntimeFailureCode,
    pub message: String,
    pub recoverable: bool,
}

impl RuntimeFailure {
    pub fn new(code: RuntimeFailureCode, message: impl Into<String>) -> Self {
        Self { code, message: message.into(), recoverable: true }
    }

    pub fn internal(cause: impl std::fmt::Display) -> Self {
        Self::new(RuntimeFailureCode::Internal, cause.to_string())
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeProgressEvent {
    pub operation_id: String,
    pub phase: RuntimePhase,
    pub completed: u64,
    pub total: Option<u64>,
    pub message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum RuntimeEvent {
    Progress { payload: RuntimeProgressEvent },
    Failure {
        #[serde(rename = "operationId")]
        operation_id: String,
        payload: RuntimeFailure,
    },
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapReply {
    pub operation_id: String,
    pub phase: RuntimePhase,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub enum RuntimeTarget {
    #[serde(rename = "windows-x86_64")]
    WindowsX86_64,
    #[serde(rename = "darwin-aarch64")]
    DarwinAarch64,
}

impl RuntimeTarget {
    pub fn current() -> Result<Self, RuntimeFailure> {
        match (std::env::consts::OS, std::env::consts::ARCH) {
            ("windows", "x86_64") => Ok(Self::WindowsX86_64),
            ("macos", "aarch64") => Ok(Self::DarwinAarch64),
            (os, arch) => Err(RuntimeFailure {
                code: RuntimeFailureCode::Internal,
                message: format!("首版不支持当前平台 {os}-{arch}"),
                recoverable: false,
            }),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::WindowsX86_64 => "windows-x86_64",
            Self::DarwinAarch64 => "darwin-aarch64",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ArchiveKind {
    Zip,
    TarGz,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeManifest {
    pub schema_version: u32,
    pub version: Version,
    pub dsh_version: Version,
    pub target: RuntimeTarget,
    pub url: Url,
    pub size: u64,
    pub sha256: String,
    pub signature: String,
    pub archive: ArchiveKind,
    pub entrypoint: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub health_path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CurrentRuntime {
    pub version: Version,
    pub previous_version: Option<Version>,
}
