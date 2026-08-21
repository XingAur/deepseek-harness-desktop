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
    MigrationConflict,
    RepairRequired,
    Cancelled,
    Internal,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum RuntimeFailureStage {
    ManagedRuntimeShutdown,
    ActivationFileLock,
    CandidateActivation,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeFailureContext {
    pub stage: RuntimeFailureStage,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub process_ids: Vec<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub managed_relative_path: Option<String>,
}

#[derive(Clone, Debug, Serialize, thiserror::Error)]
#[serde(rename_all = "camelCase")]
#[error("{message}")]
pub struct RuntimeFailure {
    pub code: RuntimeFailureCode,
    pub message: String,
    pub recoverable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<RuntimeFailureContext>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeDiagnosticSnapshot {
    pub operation_id: Option<String>,
    pub runtime_version: Option<Version>,
    pub target: Option<RuntimeTarget>,
    pub phase: RuntimePhase,
    pub failure_phase: Option<RuntimePhase>,
    pub failure: Option<RuntimeFailure>,
    pub exit_code: Option<i32>,
    pub log_file: Option<String>,
}

impl Default for RuntimeDiagnosticSnapshot {
    fn default() -> Self {
        Self {
            operation_id: None,
            runtime_version: None,
            target: None,
            phase: RuntimePhase::Checking,
            failure_phase: None,
            failure: None,
            exit_code: None,
            log_file: None,
        }
    }
}

impl RuntimeFailure {
    pub fn new(code: RuntimeFailureCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            recoverable: true,
            context: None,
        }
    }

    pub fn with_context(mut self, context: RuntimeFailureContext) -> Self {
        self.context = Some(context);
        self
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
    Progress {
        payload: RuntimeProgressEvent,
    },
    Ready {
        #[serde(rename = "operationId")]
        operation_id: String,
        #[serde(rename = "rendererUrl")]
        renderer_url: String,
    },
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
    pub renderer_url: Option<String>,
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
                context: None,
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

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
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
