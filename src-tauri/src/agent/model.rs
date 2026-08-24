use std::{net::IpAddr, path::PathBuf};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub use crate::profile::model::AgentPermissionMode;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ProfileBoundary {
    ReadOnly,
    WorkspaceWrite,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TaskLifecycle {
    Active,
    Cancelled,
    Completed,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "kebab-case")]
pub enum CapabilityKind {
    FileRead,
    FileWrite,
    FileDelete,
    Terminal,
    Network,
    PackageInstall,
    ProcessLaunch,
    ExternalWrite,
    GitCommit,
    GitPush,
    Deploy,
    CredentialUse,
    CredentialExport,
    ExtensionCall,
    McpCall,
    AuditDisable,
    BridgeBypass,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum NetworkOperation {
    Read,
    Write,
    External,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum RiskClass {
    Observation,
    WorkspaceWrite,
    Destructive,
    ExternalWrite,
    SecuritySensitive,
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AgentCapability {
    FileRead {
        path: PathBuf,
    },
    FileWrite {
        path: PathBuf,
    },
    FileDelete {
        path: PathBuf,
    },
    Terminal {
        executable: String,
        args: Vec<String>,
        cwd: PathBuf,
    },
    Network {
        host: String,
        port: u16,
        operation: NetworkOperation,
    },
    PackageInstall {
        manager: String,
        package: String,
    },
    ProcessLaunch {
        executable: PathBuf,
        cwd: PathBuf,
    },
    ExternalWrite {
        service: String,
        action: String,
        target: String,
    },
    GitCommit {
        repository: PathBuf,
    },
    GitPush {
        repository: PathBuf,
        remote: String,
    },
    Deploy {
        target: String,
    },
    CredentialUse {
        credential_id: String,
    },
    CredentialExport {
        credential_id: String,
    },
    ExtensionCall {
        extension_id: String,
        capability: String,
    },
    McpCall {
        server_id: String,
        tool: String,
    },
    AuditDisable,
    BridgeBypass,
    Unknown {
        name: String,
    },
}

impl AgentCapability {
    pub fn kind(&self) -> CapabilityKind {
        match self {
            Self::FileRead { .. } => CapabilityKind::FileRead,
            Self::FileWrite { .. } => CapabilityKind::FileWrite,
            Self::FileDelete { .. } => CapabilityKind::FileDelete,
            Self::Terminal { .. } => CapabilityKind::Terminal,
            Self::Network { .. } => CapabilityKind::Network,
            Self::PackageInstall { .. } => CapabilityKind::PackageInstall,
            Self::ProcessLaunch { .. } => CapabilityKind::ProcessLaunch,
            Self::ExternalWrite { .. } => CapabilityKind::ExternalWrite,
            Self::GitCommit { .. } => CapabilityKind::GitCommit,
            Self::GitPush { .. } => CapabilityKind::GitPush,
            Self::Deploy { .. } => CapabilityKind::Deploy,
            Self::CredentialUse { .. } => CapabilityKind::CredentialUse,
            Self::CredentialExport { .. } => CapabilityKind::CredentialExport,
            Self::ExtensionCall { .. } => CapabilityKind::ExtensionCall,
            Self::McpCall { .. } => CapabilityKind::McpCall,
            Self::AuditDisable => CapabilityKind::AuditDisable,
            Self::BridgeBypass => CapabilityKind::BridgeBypass,
            Self::Unknown { .. } => CapabilityKind::Unknown,
        }
    }

    pub fn is_mutating(&self) -> bool {
        !matches!(
            self,
            Self::FileRead { path } if !is_sensitive_path(path)
        ) && !matches!(
            self,
            Self::Network {
                operation: NetworkOperation::Read,
                host,
                ..
            } if is_public_network_host(host)
        )
    }

    pub fn is_always_denied(&self) -> bool {
        matches!(
            self,
            Self::CredentialExport { .. } | Self::AuditDisable | Self::BridgeBypass
        )
    }

    pub fn is_observation(&self) -> bool {
        matches!(self, Self::FileRead { path } if !is_sensitive_path(path))
            || matches!(
                self,
                Self::Network {
                    operation: NetworkOperation::Read,
                    host,
                    ..
                } if is_public_network_host(host)
            )
    }

    pub fn is_smart_approval_safe(&self) -> bool {
        matches!(
            self,
            Self::FileRead { path } if !is_sensitive_path(path)
        ) || matches!(self, Self::FileWrite { .. })
            || matches!(
                self,
                Self::Network {
                    operation: NetworkOperation::Read,
                    host,
                    ..
                } if is_public_network_host(host)
            )
    }

    pub fn path(&self) -> Option<&PathBuf> {
        match self {
            Self::FileRead { path }
            | Self::FileWrite { path }
            | Self::FileDelete { path }
            | Self::GitCommit { repository: path }
            | Self::GitPush {
                repository: path, ..
            }
            | Self::ProcessLaunch { cwd: path, .. } => Some(path),
            Self::Terminal { cwd, .. } => Some(cwd),
            _ => None,
        }
    }

    pub fn paths(&self) -> Vec<&PathBuf> {
        match self {
            Self::ProcessLaunch { executable, cwd } => vec![executable, cwd],
            _ => self.path().into_iter().collect(),
        }
    }

    pub fn canonical_scope(&self) -> String {
        match self {
            Self::FileRead { path }
            | Self::FileWrite { path }
            | Self::FileDelete { path }
            | Self::GitCommit { repository: path } => path.display().to_string(),
            Self::GitPush { repository, remote } => {
                scope_parts(&[repository.display().to_string(), remote.clone()])
            }
            Self::Terminal {
                executable,
                args,
                cwd,
            } => {
                let mut parts = vec![cwd.display().to_string(), executable.clone()];
                parts.extend(args.iter().cloned());
                scope_parts(&parts)
            }
            Self::Network {
                host,
                port,
                operation,
            } => scope_parts(&[host.clone(), port.to_string(), format!("{operation:?}")]),
            Self::ProcessLaunch { executable, cwd } => {
                scope_parts(&[cwd.display().to_string(), executable.display().to_string()])
            }
            Self::PackageInstall { manager, package } => {
                scope_parts(&[manager.clone(), package.clone()])
            }
            Self::ExternalWrite {
                service,
                action,
                target,
            } => scope_parts(&[service.clone(), action.clone(), target.clone()]),
            Self::Deploy { target } => target.clone(),
            Self::CredentialUse { credential_id } | Self::CredentialExport { credential_id } => {
                credential_id.clone()
            }
            Self::ExtensionCall {
                extension_id,
                capability,
            } => scope_parts(&[extension_id.clone(), capability.clone()]),
            Self::McpCall { server_id, tool } => scope_parts(&[server_id.clone(), tool.clone()]),
            Self::AuditDisable => "product-boundary".to_owned(),
            Self::BridgeBypass => "product-boundary".to_owned(),
            Self::Unknown { name } => name.clone(),
        }
    }

    pub fn risk_class(&self) -> RiskClass {
        if self.is_always_denied() {
            RiskClass::SecuritySensitive
        } else if self.is_observation() {
            RiskClass::Observation
        } else if matches!(self, Self::FileWrite { .. }) {
            RiskClass::WorkspaceWrite
        } else if matches!(self, Self::FileDelete { .. }) {
            RiskClass::Destructive
        } else {
            RiskClass::ExternalWrite
        }
    }
}

fn scope_parts(parts: &[String]) -> String {
    parts
        .iter()
        .map(|part| format!("{}:{part}", part.len()))
        .collect::<Vec<_>>()
        .join("|")
}

fn is_sensitive_path(path: &PathBuf) -> bool {
    let normalized = path.to_string_lossy().to_ascii_lowercase();
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    normalized.split(['/', '\\']).any(|part| {
        matches!(part, ".ssh" | ".gnupg" | ".aws" | ".config" | ".kube")
            || part == "credentials"
            || part == "secrets"
    }) || file_name == ".env"
        || file_name.starts_with(".env.")
        || file_name == "id_rsa"
        || file_name == "id_ed25519"
        || file_name == "id_ecdsa"
        || file_name == "id_dsa"
        || file_name == ".netrc"
        || file_name == "token.json"
        || file_name.ends_with(".pem")
        || file_name.ends_with(".key")
        || file_name.ends_with(".p12")
        || file_name.ends_with(".pfx")
        || file_name.ends_with(".jks")
        || file_name.contains("credential")
        || file_name.contains("secret")
        || file_name.contains("password")
}

fn is_public_network_host(host: &str) -> bool {
    let normalized = host.trim_end_matches('.').to_ascii_lowercase();
    if matches!(
        normalized.as_str(),
        "localhost"
            | "metadata.google.internal"
            | "metadata.azure.internal"
            | "instance-data.ec2.internal"
    ) || normalized.ends_with(".localhost")
        || normalized.ends_with(".local")
        || normalized.ends_with(".internal")
    {
        return false;
    }
    let Ok(address) = normalized.parse::<IpAddr>() else {
        return true;
    };
    is_public_network_ip(address)
}

pub(crate) fn is_public_network_ip(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(address) => {
            !(address.is_private()
                || address.is_loopback()
                || address.is_link_local()
                || address.is_unspecified()
                || address.octets() == [169, 254, 169, 254])
        }
        IpAddr::V6(address) => {
            !(address.is_loopback()
                || address.is_unspecified()
                || address.is_unique_local()
                || address.is_unicast_link_local())
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CapabilityRequest {
    pub request_id: Uuid,
    pub task_id: Uuid,
    pub generation_id: String,
    pub issued_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub capability: AgentCapability,
    pub disclosed: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TaskGrant {
    pub task_id: Uuid,
    pub generation_id: String,
    pub kind: CapabilityKind,
    pub path: Option<PathBuf>,
    pub scope: String,
    pub expires_at: Option<DateTime<Utc>>,
}

impl TaskGrant {
    pub fn matches(
        &self,
        task_id: Uuid,
        generation_id: &str,
        capability: &AgentCapability,
        now: DateTime<Utc>,
    ) -> bool {
        if self.task_id != task_id
            || self.generation_id != generation_id
            || self.kind != capability.kind()
            || self.scope != capability.canonical_scope()
            || self.expires_at.is_some_and(|expires_at| expires_at <= now)
        {
            return false;
        }
        match (&self.path, capability.path()) {
            (Some(granted), Some(requested)) => requested == granted,
            (None, None) => true,
            _ => false,
        }
    }
}

#[derive(Clone, Debug)]
pub struct TaskContext {
    pub task_id: Uuid,
    pub generation_id: String,
    pub workspace_root: PathBuf,
    pub permission_mode: AgentPermissionMode,
    pub profile_boundary: ProfileBoundary,
    pub lifecycle: TaskLifecycle,
    pub now: DateTime<Utc>,
    pub explicit_grants: Vec<TaskGrant>,
    pub declared_capabilities: Vec<CapabilityKind>,
    pub explicit_full_access: bool,
    pub approved_processes: Vec<PathBuf>,
    pub approved_terminal_tools: Vec<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DecisionReason {
    MalformedRequest,
    ExpiredRequest,
    TaskMismatch,
    GenerationMismatch,
    UndisclosedCapability,
    TaskNotActive,
    ProductBoundary,
    ProfileReadOnly,
    CapabilityNotDeclared,
    ExplicitGrant,
    LowRiskObservation,
    SmartPolicy,
    FullAccess,
    UserApprovalRequired,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decision {
    AllowOnce,
    AllowForTask,
    RequestApproval { reason: DecisionReason },
    Denied { reason: DecisionReason },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AuditResult {
    Allowed,
    Denied,
    ApprovalPending,
    Expired,
    Cancelled,
    Failed,
}
