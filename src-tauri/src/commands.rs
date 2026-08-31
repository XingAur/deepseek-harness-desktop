use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::PathBuf,
    sync::Arc,
};

use chrono::Utc;
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, State, WebviewWindow};
use tauri_plugin_opener::OpenerExt;
use uuid::Uuid;

use crate::{
    DesktopFoundation, FoundationBootstrapState,
    agent::service::{ApprovalDecision, ApprovalRecord},
    agents::{
        discovery::{DiscoveryRequest, discover},
        model::{AgentProvider, DiscoveryResult},
        runtime::AgentRuntime,
    },
    app_update::{
        AppUpdateController,
        model::{AppUpdateFailure, AppUpdateReceipt, AppUpdateSource, AppUpdateState},
    },
    apps::{AppLauncher, AppStatusReply, LaunchReply},
    credentials::{
        model::{CredentialId, CredentialMetadata, CredentialStatus, SecretValue},
    },
    desktop::DesktopCoordinator,
    harness::{HarnessService, HarnessStatus, HarnessTaskStart},
    profile::model::{
        PermissionMode, ProfileDraft, ProfileListSnapshot, ProfilePatch, ProfileRecord,
    },
    projects::{
        active_profile,
        location::{ProjectLocationPreview, create_project_location, preview_project_location},
        metadata::{ProjectMetadataPatch, ProjectMetadataSnapshot},
        metadata_repository,
        recycle::{ProtectedRoots, resolve_registered_workspace, validate_recycle_target},
    },
    runtime::{BootstrapReply, RuntimeFailure},
};

pub(crate) const VERSIONED_AGENT_COMMAND_NAMES: &[&str] = &[
    "agent_capability_inventory",
    "agent_provider_metadata",
    "agent_credential_put",
    "agent_credential_delete",
    "agent_credential_status",
    "agent_credential_test",
    "agent_cli_path_select",
    "agent_cli_path_status",
    "agent_cli_install_status",
    "agent_cli_install_start",
    "agent_cli_login_status",
    "agent_cli_login_start",
    "agent_plugin_catalog",
    "agent_plugin_install_start",
    "agent_plugin_install_status",
    "agent_task_create",
    "agent_task_list",
    "agent_task_recover",
    "agent_task_start",
    "agent_task_cancel",
    "agent_task_resume",
    "agent_pending_approvals",
    "agent_resolve_approval",
    "agent_content_reference_read",
    "agent_extension_inventory",
    "agent_extension_install",
    "agent_extension_enable",
    "agent_extension_disable",
    "agent_extension_uninstall",
    "harness_connection_list",
    "harness_connection_save",
    "harness_connection_delete",
    "harness_connection_test",
];

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentPendingApprovalsInput {
    task_id: uuid::Uuid,
    generation_id: String,
    session_id: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentResolveApprovalInput {
    approval_id: uuid::Uuid,
    task_id: uuid::Uuid,
    generation_id: String,
    session_id: String,
    decision: ApprovalDecision,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentApprovalReply {
    approval_id: uuid::Uuid,
    request_id: uuid::Uuid,
    task_id: uuid::Uuid,
    generation_id: String,
    capability_kind: crate::agent::model::CapabilityKind,
    scope: String,
    risk_class: crate::agent::model::RiskClass,
    policy_version: String,
    expires_at: String,
    status: ApprovalStatusReply,
    decision: Option<ApprovalDecision>,
    result_category: Option<String>,
    error_code: Option<String>,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ApprovalStatusReply {
    Pending,
    ApprovedOnce,
    ApprovedForTask,
    Denied,
    Consumed,
    Cancelled,
}

impl From<ApprovalRecord> for AgentApprovalReply {
    fn from(record: ApprovalRecord) -> Self {
        Self {
            approval_id: record.approval_id,
            request_id: record.request.request_id,
            task_id: record.request.task_id,
            generation_id: record.request.generation_id,
            capability_kind: record.request.capability_kind,
            scope: crate::agent::audit::redact_scope(&record.request.canonical_scope),
            risk_class: record.request.risk_class,
            policy_version: record.request.policy_version,
            expires_at: record.request.expires_at.to_rfc3339(),
            status: match record.status {
                crate::agent::service::ApprovalStatus::Pending => ApprovalStatusReply::Pending,
                crate::agent::service::ApprovalStatus::ApprovedOnce => {
                    ApprovalStatusReply::ApprovedOnce
                }
                crate::agent::service::ApprovalStatus::ApprovedForTask => {
                    ApprovalStatusReply::ApprovedForTask
                }
                crate::agent::service::ApprovalStatus::Denied => ApprovalStatusReply::Denied,
                crate::agent::service::ApprovalStatus::Consumed => ApprovalStatusReply::Consumed,
                crate::agent::service::ApprovalStatus::Cancelled => ApprovalStatusReply::Cancelled,
            },
            decision: record.decision,
            result_category: record.result_category,
            error_code: record
                .error_code
                .as_deref()
                .map(crate::agent::audit::redact_error_code),
        }
    }
}

#[tauri::command]
pub async fn agent_pending_approvals(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    input: AgentPendingApprovalsInput,
) -> Result<Vec<AgentApprovalReply>, String> {
    coordinator
        .validate_generation(&input.generation_id)
        .await
        .map_err(|error| error.to_string())?;
    ensure_task_session(
        &foundation,
        input.task_id,
        &input.generation_id,
        &input.session_id,
    )?;
    let service = foundation
        .agent_service
        .as_ref()
        .ok_or_else(|| "权限服务当前不可用".to_owned())?;
    service
        .pending_for_recovery(input.task_id, &input.generation_id)
        .map(|records| records.into_iter().map(Into::into).collect())
        .map_err(|_| "读取审批列表失败".to_owned())
}

#[tauri::command]
pub async fn agent_resolve_approval(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    runtime: State<'_, Arc<AgentRuntime>>,
    input: AgentResolveApprovalInput,
) -> Result<AgentApprovalReply, String> {
    coordinator
        .validate_generation(&input.generation_id)
        .await
        .map_err(|error| error.to_string())?;
    ensure_task_session(
        &foundation,
        input.task_id,
        &input.generation_id,
        &input.session_id,
    )?;
    let service = foundation
        .agent_service
        .as_ref()
        .ok_or_else(|| "权限服务当前不可用".to_owned())?;
    runtime
        .ensure_approval_control(input.task_id, &input.session_id)
        .await?;
    let record = service
        .resolve(
            input.approval_id,
            input.task_id,
            &input.generation_id,
            input.decision,
            chrono::Utc::now(),
        )
        .map_err(|_| "审批已失效或无法处理".to_owned())
        ?;
    if let Err(error) = runtime
        .resolve_approval(
            input.task_id,
            &input.session_id,
            &input.approval_id.to_string(),
            input.decision != ApprovalDecision::Deny,
        )
        .await
    {
        if service
            .mark_approval_delivery_failed(
                input.approval_id,
                input.task_id,
                chrono::Utc::now(),
            )
            .is_err()
        {
            return Err("审批结果未送达且失败状态无法落库，请立即复核任务".to_owned());
        }
        return Err(format!("审批结果未送达，任务已转为待复核：{error}"));
    }
    Ok(record.into())
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentCapabilityDescriptor {
    id: &'static str,
    display_name: &'static str,
    mutating: bool,
    approval_required: bool,
}

#[tauri::command]
pub async fn agent_capability_inventory(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<AgentCapabilityDescriptor>, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    Ok(vec![
        AgentCapabilityDescriptor {
            id: "file-read",
            display_name: "读取文件",
            mutating: false,
            approval_required: false,
        },
        AgentCapabilityDescriptor {
            id: "file-write",
            display_name: "写入工作区文件",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "file-delete",
            display_name: "删除文件",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "terminal",
            display_name: "执行终端命令",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "network",
            display_name: "访问网络",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "package-install",
            display_name: "安装依赖",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "process-launch",
            display_name: "启动进程",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "external-write",
            display_name: "写入外部服务",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "git-commit",
            display_name: "创建 Git 提交",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "git-push",
            display_name: "推送 Git 远端",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "deploy",
            display_name: "部署",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "credential-use",
            display_name: "使用凭证",
            mutating: false,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "extension-call",
            display_name: "调用扩展",
            mutating: true,
            approval_required: true,
        },
        AgentCapabilityDescriptor {
            id: "mcp-call",
            display_name: "调用 MCP",
            mutating: true,
            approval_required: true,
        },
    ])
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentProviderMetadataReply {
    provider_id: String,
    display_name: String,
    cli_command: String,
    kind: String,
    adapter_protocol: String,
    credential_supported: bool,
    developer_only: bool,
    credential_id: Option<String>,
    credential_status: Option<String>,
}

#[tauri::command]
pub async fn agent_provider_metadata(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<AgentProviderMetadataReply>, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let connection = store.reader().map_err(|error| error.to_string())?;
    [
        ("codex", "Codex", "codex", "cli", false),
        ("claude", "Claude", "claude", "cli", false),
        ("deepseek", "DeepSeek", "api", "api", false),
    ]
    .into_iter()
    .map(|(provider_id, display_name, cli_command, kind, developer_only)| {
        let credential: Option<(Option<String>, Option<String>)> = connection
            .query_row(
                "SELECT providers.credential_id, credential_metadata.status
                 FROM providers
                 LEFT JOIN credential_metadata ON credential_metadata.credential_id = providers.credential_id
                 WHERE providers.provider_id = ?1",
                params![provider_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .map_err(|_| "Provider 凭证状态读取失败".to_owned())?;
        Ok(AgentProviderMetadataReply {
            provider_id: provider_id.to_owned(),
            display_name: display_name.to_owned(),
            cli_command: cli_command.to_owned(),
            kind: kind.to_owned(),
            adapter_protocol: crate::agents::model::ADAPTER_PROTOCOL_VERSION.to_owned(),
            credential_supported: true,
            developer_only,
            credential_id: credential.as_ref().and_then(|value| value.0.clone()),
            credential_status: credential.and_then(|value| value.1),
        })
    })
    .collect()
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentCredentialStatusReply {
    credential_id: CredentialId,
    status: CredentialStatus,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentCredentialTestReply {
    credential_id: CredentialId,
    status: CredentialStatus,
    test_kind: &'static str,
    tested: bool,
}

#[tauri::command]
pub async fn agent_credential_put(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    provider_id: Option<String>,
    credential_id: Option<String>,
    secret: String,
) -> Result<CredentialMetadata, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    ensure_agent_store(&foundation)?;
    let provider_id = provider_id
        .map(|value| {
            validate_agent_identifier(&value, "Provider ID")?;
            validate_credential_provider(&value).map(|_| value)
        })
        .transpose()?;
    if secret.is_empty() || secret.len() > 16 * 1024 {
        return Err("凭证内容无效".to_owned());
    }
    let parsed_id = credential_id.map(CredentialId::from_string).transpose()?;
    let metadata = foundation
        .credential_vault
        .put(parsed_id.as_ref(), SecretValue::new(secret))
        .map_err(|error| error.code().to_owned())?;
    sync_credential_metadata(&foundation, &metadata.credential_id, metadata.status, None)?;
    if let Some(provider_id) = provider_id {
        bind_provider_credential(&foundation, &provider_id, &metadata.credential_id)?;
    }
    Ok(metadata)
}

#[tauri::command]
pub async fn agent_credential_delete(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    credential_id: String,
) -> Result<AgentCredentialStatusReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    ensure_agent_store(&foundation)?;
    let credential_id = CredentialId::from_string(credential_id)?;
    let status = foundation
        .credential_vault
        .delete(&credential_id)
        .map_err(|error| error.code().to_owned())?;
    sync_credential_metadata(&foundation, &credential_id, status, None)?;
    clear_provider_credential(&foundation, &credential_id)?;
    Ok(AgentCredentialStatusReply {
        credential_id,
        status,
    })
}

#[tauri::command]
pub async fn agent_credential_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    credential_id: String,
) -> Result<AgentCredentialStatusReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    ensure_agent_store(&foundation)?;
    let credential_id = CredentialId::from_string(credential_id)?;
    let status = foundation
        .credential_vault
        .status(&credential_id)
        .map_err(|error| error.code().to_owned())?;
    sync_credential_metadata(&foundation, &credential_id, status, None)?;
    Ok(AgentCredentialStatusReply {
        credential_id,
        status,
    })
}

#[tauri::command]
pub async fn agent_credential_test(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    credential_id: String,
) -> Result<AgentCredentialTestReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    ensure_agent_store(&foundation)?;
    let credential_id = CredentialId::from_string(credential_id)?;
    let status = foundation
        .credential_vault
        .status(&credential_id)
        .map_err(|error| error.code().to_owned())?;
    let tested = status == CredentialStatus::Configured;
    sync_credential_metadata(&foundation, &credential_id, status, None)?;
    Ok(AgentCredentialTestReply {
        credential_id,
        status,
        test_kind: "secure-store-presence",
        tested,
    })
}

fn sync_credential_metadata(
    foundation: &DesktopFoundation,
    credential_id: &CredentialId,
    status: CredentialStatus,
    verified_at: Option<&str>,
) -> Result<(), String> {
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let writer = store.writer().map_err(|error| error.to_string())?;
    let now = Utc::now().to_rfc3339();
    let status = match status {
        CredentialStatus::Configured => "configured",
        CredentialStatus::NotConfigured => "not-configured",
    };
    writer
        .connection()
        .execute(
            "INSERT INTO credential_metadata (credential_id, status, created_at, updated_at, last_verified_at)
             VALUES (?1, ?2, ?3, ?3, ?4)
             ON CONFLICT (credential_id) DO UPDATE SET status = excluded.status,
               updated_at = excluded.updated_at, last_verified_at = excluded.last_verified_at",
            params![credential_id.as_str(), status, now, verified_at],
        )
        .map_err(|_| "凭证状态保存失败".to_owned())?;
    Ok(())
}

fn bind_provider_credential(
    foundation: &DesktopFoundation,
    provider_id: &str,
    credential_id: &CredentialId,
) -> Result<(), String> {
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let writer = store.writer().map_err(|error| error.to_string())?;
    let now = Utc::now().to_rfc3339();
    writer
        .connection()
        .execute(
            "INSERT OR IGNORE INTO providers (provider_id, provider_kind, display_name, status, created_at, updated_at)
             VALUES (?1, ?1, ?2, 'available', ?3, ?3)",
            params![provider_id, credential_provider_display_name(provider_id)?, now],
        )
        .map_err(|_| "Provider 凭证关联失败".to_owned())?;
    writer
        .connection()
        .execute(
            "UPDATE providers SET credential_id = ?1, updated_at = ?2 WHERE provider_id = ?3",
            params![credential_id.as_str(), now, provider_id],
        )
        .map_err(|_| "Provider 凭证关联失败".to_owned())?;
    Ok(())
}

fn clear_provider_credential(
    foundation: &DesktopFoundation,
    credential_id: &CredentialId,
) -> Result<(), String> {
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let writer = store.writer().map_err(|error| error.to_string())?;
    writer
        .connection()
        .execute(
            "UPDATE providers SET credential_id = NULL, updated_at = ?1 WHERE credential_id = ?2",
            params![Utc::now().to_rfc3339(), credential_id.as_str()],
        )
        .map_err(|_| "Provider 凭证解绑失败".to_owned())?;
    Ok(())
}

fn ensure_agent_store(foundation: &DesktopFoundation) -> Result<(), String> {
    if foundation.agent_store.is_some() {
        Ok(())
    } else {
        Err("Agent 数据服务当前不可用".to_owned())
    }
}

#[tauri::command]
pub async fn agent_cli_path_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    generation_id: String,
    session_id: String,
    provider_id: String,
) -> Result<DiscoveryResult, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    discover(&DiscoveryRequest::for_provider(parse_provider(
        &provider_id,
    )?))
}

#[tauri::command]
pub async fn agent_cli_path_select(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    jobs: State<'_, Arc<crate::agents::cli_ops::AgentCliJobState>>,
    generation_id: String,
    session_id: String,
    provider_id: String,
    path: String,
) -> Result<DiscoveryResult, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    if path.is_empty() || path.len() > 4096 {
        return Err("CLI 路径无效".to_owned());
    }
    let path = PathBuf::from(&path);
    if !path.is_absolute() {
        return Err("CLI 路径必须是绝对路径".to_owned());
    }
    let provider = parse_provider(&provider_id)?;
    let result = discover(&DiscoveryRequest::for_provider(provider).with_explicit_path(path))?;
    if result.selected.is_none() {
        return Err("选择的路径不是可用的 CLI：请确认文件存在、可执行，并且能返回版本号".to_owned());
    }
    let selected = result.selected.as_ref().expect("checked").path.clone();
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let writer = store.writer().map_err(|error| error.to_string())?;
    let now = Utc::now().to_rfc3339();
    writer
        .connection()
        .execute(
            "INSERT INTO providers (provider_id, provider_kind, display_name, cli_path, status, created_at, updated_at)
             VALUES (?1, 'cli', ?2, ?3, 'active', ?4, ?4)
             ON CONFLICT(provider_id) DO UPDATE SET cli_path = excluded.cli_path, updated_at = excluded.updated_at",
            params![provider_id, provider.command_name(), selected.to_string_lossy(), now],
        )
        .map_err(|_| "CLI 路径保存失败".to_owned())?;
    jobs.invalidate_probes(&provider_id);
    Ok(result)
}

#[tauri::command]
pub async fn agent_cli_install_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    jobs: State<'_, Arc<crate::agents::cli_ops::AgentCliJobState>>,
    generation_id: String,
    session_id: String,
    provider_id: String,
) -> Result<crate::agents::cli_ops::CliInstallStatusReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    crate::agents::cli_ops::install_status(&jobs, foundation.agent_store.as_ref(), &provider_id)
}

#[tauri::command]
pub async fn agent_cli_install_start(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    jobs: State<'_, Arc<crate::agents::cli_ops::AgentCliJobState>>,
    generation_id: String,
    session_id: String,
    provider_id: String,
) -> Result<crate::agents::cli_ops::CliInstallStatusReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    crate::agents::cli_ops::install_start(&jobs, foundation.agent_store.as_ref(), &provider_id)
}

#[tauri::command]
pub async fn agent_cli_login_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    jobs: State<'_, Arc<crate::agents::cli_ops::AgentCliJobState>>,
    generation_id: String,
    session_id: String,
    provider_id: String,
) -> Result<crate::agents::cli_ops::CliLoginStatusReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    crate::agents::cli_ops::login_status(&jobs, foundation.agent_store.as_ref(), &provider_id)
}

#[tauri::command]
pub async fn agent_cli_login_start(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    jobs: State<'_, Arc<crate::agents::cli_ops::AgentCliJobState>>,
    generation_id: String,
    session_id: String,
    provider_id: String,
) -> Result<crate::agents::cli_ops::CliLoginStatusReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    crate::agents::cli_ops::login_start(&jobs, foundation.agent_store.as_ref(), &provider_id)
}

fn parse_provider(value: &str) -> Result<AgentProvider, String> {
    match value {
        "codex" => Ok(AgentProvider::Codex),
        "claude" => Ok(AgentProvider::Claude),
        _ => Err("Provider 不受支持".to_owned()),
    }
}

fn validate_credential_provider(value: &str) -> Result<(), String> {
    match value {
        "codex" | "claude" | "deepseek" | "openai-compatible" => Ok(()),
        _ => Err("Provider 不受支持".to_owned()),
    }
}

fn credential_provider_display_name(value: &str) -> Result<&'static str, String> {
    match value {
        "codex" => Ok("Codex"),
        "claude" => Ok("Claude"),
        "deepseek" => Ok("DeepSeek"),
        "openai-compatible" => Ok("OpenAI 兼容"),
        _ => Err("Provider 不受支持".to_owned()),
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentTaskReply {
    task_id: Uuid,
    worker_session_id: String,
    generation_id: String,
    provider_id: String,
    agent_id: String,
    workspace_id: String,
    prompt: String,
    permission: String,
    status: String,
}

#[derive(Clone, Debug)]
struct AgentSelection {
    provider_id: String,
    agent_id: String,
    provider: AgentProvider,
}

fn parse_agent_selection(
    provider_id: Option<String>,
    agent_id: Option<String>,
) -> Result<AgentSelection, String> {
    if provider_id.is_none() != agent_id.is_none() {
        return Err("Provider 与 Agent 必须成对提供".to_owned());
    }
    let provider_id = provider_id.unwrap_or_else(|| "codex".to_owned());
    let provider = parse_provider(&provider_id)?;
    let expected_agent_id = format!("{provider_id}:default");
    let agent_id = agent_id.unwrap_or_else(|| expected_agent_id.clone());
    if agent_id != expected_agent_id {
        return Err("Agent 与 Provider 不匹配".to_owned());
    }
    Ok(AgentSelection {
        provider_id,
        agent_id,
        provider,
    })
}

fn provider_display_name(provider: AgentProvider) -> &'static str {
    match provider {
        AgentProvider::Codex => "Codex",
        AgentProvider::Claude => "Claude",
    }
}

#[tauri::command]
pub async fn agent_plugin_catalog(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    market: State<'_, Arc<crate::plugin_market::PluginMarketState>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
    query: Option<String>,
    category: Option<String>,
    offset: Option<u64>,
    limit: Option<u64>,
    refresh: Option<bool>,
) -> Result<crate::plugin_market::CatalogPage, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|_| "应用资源目录不可用".to_owned())?;
    let _ = &foundation;
    crate::plugin_market::catalog_page(
        &market,
        &resource_dir,
        query.as_deref().unwrap_or(""),
        category.as_deref().unwrap_or(""),
        offset.unwrap_or(0).min(10_000) as usize,
        limit.unwrap_or(30).min(crate::plugin_market::CATALOG_PAGE_MAX as u64) as usize,
        refresh.unwrap_or(false),
    )
}

#[tauri::command]
pub async fn agent_plugin_install_start(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    market: State<'_, Arc<crate::plugin_market::PluginMarketState>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
    plugin_id: String,
) -> Result<crate::plugin_market::PluginInstallStatusReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|_| "应用资源目录不可用".to_owned())?;
    let profile = active_profile(&foundation).map_err(|error| error.to_string())?;
    crate::plugin_market::install_start(
        &market,
        &resource_dir,
        &foundation.paths.runtime,
        &profile.data_root,
        &plugin_id,
    )
}

#[tauri::command]
pub async fn agent_plugin_install_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    market: State<'_, Arc<crate::plugin_market::PluginMarketState>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
    plugin_id: String,
) -> Result<crate::plugin_market::PluginInstallStatusReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|_| "应用资源目录不可用".to_owned())?;
    crate::plugin_market::install_status(&market, &resource_dir, &plugin_id)
}

#[tauri::command]
pub async fn agent_task_create(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    workspace_id: String,
    prompt: String,
    permission: String,
    provider_id: Option<String>,
    agent_id: Option<String>,
) -> Result<AgentTaskReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_permission(&permission)?;
    if prompt.trim().is_empty() || prompt.len() > 16 * 1024 {
        return Err("任务提示无效或超出 16 KiB 限制".to_owned());
    }
    if generation_id.is_empty() || generation_id.len() > 128 {
        return Err("Generation ID 无效".to_owned());
    }
    validate_agent_identifier(&session_id, "Session ID")?;
    let selection = parse_agent_selection(provider_id, agent_id)?;
    let profile = active_profile(&foundation).map_err(|error| error.to_string())?;
    let workspace = resolve_registered_workspace(&profile.data_root, &workspace_id)
        .map_err(|error| error.to_string())?;
    let workspace = workspace
        .canonicalize()
        .map_err(|_| "Workspace 不可用".to_owned())?;
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let task_id = Uuid::new_v4();
    let worker_session_id = session_id.clone();
    let now = Utc::now().to_rfc3339();
    let writer = store.writer().map_err(|error| error.to_string())?;
    writer
        .connection()
        .execute_batch("BEGIN IMMEDIATE")
        .map_err(|_| "Agent 任务写入失败".to_owned())?;
    let result = (|| {
        writer.connection().execute(
            "INSERT OR IGNORE INTO providers (provider_id, provider_kind, display_name, status, created_at, updated_at)
             VALUES (?1, ?1, ?2, 'available', ?3, ?3)",
            params![selection.provider_id, provider_display_name(selection.provider), now],
        )?;
        writer.connection().execute(
            "INSERT OR IGNORE INTO agents (agent_id, provider_id, adapter_kind, display_name, status, created_at, updated_at)
             VALUES (?1, ?2, ?2, ?3, 'available', ?4, ?4)",
            params![selection.agent_id, selection.provider_id, provider_display_name(selection.provider), now],
        )?;
        writer.connection().execute(
            "INSERT INTO tasks (task_id, agent_id, workspace_path, workspace_id, prompt, permission_mode, status, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'active', ?7, ?7)",
            params![
                task_id.to_string(),
                selection.agent_id,
                workspace.to_string_lossy(),
                workspace_id,
                prompt,
                permission,
                now
            ],
        )?;
        writer.connection().execute(
            "INSERT INTO worker_sessions (task_id, worker_session_id, desktop_session_id, adapter_kind, generation_id, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                task_id.to_string(),
                worker_session_id,
                session_id,
                selection.provider_id,
                generation_id,
                now
            ],
        )?;
        Ok::<(), rusqlite::Error>(())
    })();
    match result {
        Ok(()) => writer
            .connection()
            .execute_batch("COMMIT")
            .map_err(|_| "Agent 任务提交失败".to_owned())?,
        Err(_) => {
            let _ = writer.connection().execute_batch("ROLLBACK");
            return Err("Agent 任务创建失败".to_owned());
        }
    }
    Ok(AgentTaskReply {
        task_id,
        worker_session_id,
        generation_id,
        provider_id: selection.provider_id,
        agent_id: selection.agent_id,
        workspace_id,
        prompt,
        permission,
        status: "active".to_owned(),
    })
}

#[tauri::command]
pub async fn agent_task_list(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    workspace_id: String,
) -> Result<Vec<AgentTaskReply>, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let connection = store.reader().map_err(|error| error.to_string())?;
    let mut statement = connection
        .prepare(
            "SELECT tasks.task_id, worker_sessions.worker_session_id, worker_sessions.generation_id,
                    COALESCE(agents.provider_id, ''), tasks.agent_id, tasks.workspace_id,
                    tasks.prompt, tasks.permission_mode, tasks.status
             FROM tasks
             JOIN agents ON agents.agent_id = tasks.agent_id
             JOIN worker_sessions ON worker_sessions.task_id = tasks.task_id
             WHERE tasks.workspace_id = ?3
               AND (
                   (worker_sessions.generation_id = ?1
                    AND worker_sessions.desktop_session_id = ?2)
                   OR tasks.status IN ('waiting-approval', 'needs-review')
               )
             ORDER BY tasks.updated_at DESC LIMIT 100",
        )
        .map_err(|_| "Agent 任务列表读取失败".to_owned())?;
    statement
        .query_map(params![generation_id, session_id, workspace_id], |row| {
            let task_id: String = row.get(0)?;
            Ok(AgentTaskReply {
                task_id: Uuid::parse_str(&task_id).map_err(|_| rusqlite::Error::InvalidQuery)?,
                worker_session_id: row.get(1)?,
                generation_id: row.get(2)?,
                provider_id: row.get(3)?,
                agent_id: row.get(4)?,
                workspace_id: row.get(5)?,
                prompt: row.get(6)?,
                permission: row.get(7)?,
                status: row.get(8)?,
            })
        })
        .map_err(|_| "Agent 任务列表读取失败".to_owned())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "Agent 任务列表读取失败".to_owned())
}

#[tauri::command]
pub async fn agent_task_recover(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    task_id: Uuid,
    workspace_id: String,
    source_session_id: String,
) -> Result<AgentTaskReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    validate_agent_identifier(&source_session_id, "原会话 ID")?;
    validate_agent_identifier(&workspace_id, "Workspace ID")?;
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let writer = store.writer().map_err(|error| error.to_string())?;
    let transaction = writer
        .connection()
        .unchecked_transaction()
        .map_err(|_| "Agent 恢复任务接管失败".to_owned())?;
    let now = Utc::now().to_rfc3339();
    let updated = transaction
        .execute(
            "UPDATE worker_sessions
             SET worker_session_id = ?1, desktop_session_id = ?1,
                 generation_id = ?2, updated_at = ?3
             WHERE task_id = ?4
               AND desktop_session_id = ?5
               AND EXISTS (
                   SELECT 1 FROM tasks
                   WHERE tasks.task_id = ?4
                     AND tasks.workspace_id = ?6
                     AND tasks.status IN ('waiting-approval', 'needs-review')
               )",
            params![
                session_id,
                generation_id,
                now,
                task_id.to_string(),
                source_session_id,
                workspace_id
            ],
        )
        .map_err(|_| "Agent 恢复任务接管失败".to_owned())?;
    if updated != 1 {
        return Err("Agent 任务不存在、原会话不匹配或当前状态不可接管".to_owned());
    }
    transaction
        .execute(
            "UPDATE approvals SET generation_id = ?1
             WHERE task_id = ?2 AND status = 'pending'",
            params![generation_id, task_id.to_string()],
        )
        .map_err(|_| "Agent 恢复审批接管失败".to_owned())?;
    transaction
        .commit()
        .map_err(|_| "Agent 恢复任务接管提交失败".to_owned())?;
    task_reply(&foundation, task_id, &generation_id, &session_id)
}

#[tauri::command]
pub async fn agent_task_start(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    runtime: State<'_, Arc<AgentRuntime>>,
    generation_id: String,
    session_id: String,
    task_id: Uuid,
) -> Result<AgentTaskReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    let reply = update_task_status(
        &foundation,
        generation_id,
        session_id,
        task_id,
        &["active"],
        "running",
    )?;
    if let Err(error) = runtime
        .start_task(
            task_id,
            &reply.generation_id,
            &reply.worker_session_id,
            codex_home_for(&foundation).as_deref(),
        )
        .await
    {
        let _ = update_task_status(
            &foundation,
            reply.generation_id.clone(),
            reply.worker_session_id.clone(),
            task_id,
            &["running"],
            "failed",
        );
        return Err(error);
    }
    task_reply(&foundation, task_id, &reply.generation_id, &reply.worker_session_id)
}

#[tauri::command]
pub async fn agent_task_resume(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    runtime: State<'_, Arc<AgentRuntime>>,
    generation_id: String,
    session_id: String,
    task_id: Uuid,
) -> Result<AgentTaskReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    let reply = update_task_status(
        &foundation,
        generation_id,
        session_id,
        task_id,
        &["paused"],
        "running",
    )?;
    if let Err(error) = runtime
        .start_task(
            task_id,
            &reply.generation_id,
            &reply.worker_session_id,
            codex_home_for(&foundation).as_deref(),
        )
        .await
    {
        let _ = update_task_status(
            &foundation,
            reply.generation_id.clone(),
            reply.worker_session_id.clone(),
            task_id,
            &["running"],
            "failed",
        );
        return Err(error);
    }
    task_reply(&foundation, task_id, &reply.generation_id, &reply.worker_session_id)
}

#[tauri::command]
pub async fn agent_task_cancel(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    runtime: State<'_, Arc<AgentRuntime>>,
    generation_id: String,
    session_id: String,
    task_id: Uuid,
) -> Result<AgentTaskReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    ensure_task_session(&foundation, task_id, &generation_id, &session_id)?;
    if let Some(service) = foundation.agent_service.as_ref() {
        service
            .cancel_task(task_id, &generation_id, Utc::now())
            .map_err(|_| "Agent 任务无法取消".to_owned())?;
    } else {
        return Err("Agent 权限服务当前不可用".to_owned());
    }
    runtime.cancel_task(task_id).await?;
    task_reply(&foundation, task_id, &generation_id, &session_id)
}

#[tauri::command]
pub async fn harness_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    harness: State<'_, Arc<HarnessService>>,
    generation_id: String,
    session_id: String,
) -> Result<HarnessStatus, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    Ok(harness.status().await)
}

#[tauri::command]
pub async fn harness_start(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    harness: State<'_, Arc<HarnessService>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
    task_contract_path: Option<String>,
    understanding_path: Option<String>,
    worktree_root: String,
    knowledge_home: String,
    authorization_id: String,
    agent_backend: Option<String>,
    archive_root: Option<String>,
    selected_model_id: Option<String>,
    yunxiao_profile_id: Option<String>,
    gitlab_profile_id: Option<String>,
    database_profile_id: Option<String>,
) -> Result<HarnessStatus, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let task = HarnessTaskStart {
        task_contract_path: task_contract_path.map(PathBuf::from),
        understanding_path: understanding_path.map(PathBuf::from),
        worktree_root: PathBuf::from(worktree_root),
        knowledge_home: PathBuf::from(knowledge_home),
        authorization_id,
        agent_backend,
        archive_root: archive_root.map(PathBuf::from),
        intake_source: None,
        intake_include_comments: None,
        chat_prompt: None,
        chat_evidence_paths: None,
        selected_model_id,
        yunxiao_profile_id,
        gitlab_profile_id,
        database_profile_id,
    };
    harness
        .start(app, &foundation, Uuid::new_v4().to_string(), task)
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn harness_intake(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    harness: State<'_, Arc<HarnessService>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
    source: String,
    archive_root: String,
    include_comments: Option<bool>,
    yunxiao_profile_id: Option<String>,
    selected_model_id: Option<String>,
    agent_backend: Option<String>,
) -> Result<HarnessStatus, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let task = HarnessTaskStart {
        task_contract_path: None,
        understanding_path: None,
        worktree_root: std::env::temp_dir().join("deepseek-harness-intake-worktree"),
        knowledge_home: std::env::temp_dir().join("deepseek-harness-intake-knowledge"),
        authorization_id: "harness-intake".to_owned(),
        agent_backend: agent_backend.filter(|value| !value.trim().is_empty()),
        archive_root: Some(PathBuf::from(archive_root)),
        intake_source: Some(source),
        intake_include_comments: Some(include_comments.unwrap_or(true)),
        chat_prompt: None,
        chat_evidence_paths: None,
        selected_model_id: selected_model_id.filter(|value| !value.trim().is_empty()),
        yunxiao_profile_id,
        gitlab_profile_id: None,
        database_profile_id: None,
    };
    harness
        .start(app, &foundation, Uuid::new_v4().to_string(), task)
        .await
        .map_err(|error| error.to_string())
}

/// 从主聊天创建 Harness 任务。归档目录默认落在当前 Profile 的任务目录，
/// 用户只在需要时通过主聊天里的高级选项选择其他目录；内部 contract/understanding
/// 路径由 Harness 自己生成，不暴露给用户。
#[tauri::command]
pub async fn harness_chat_start(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    harness: State<'_, Arc<HarnessService>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
    prompt: String,
    workspace_id: Option<String>,
    archive_root: Option<String>,
    intake_source: Option<String>,
    chat_evidence_paths: Option<Vec<String>>,
    selected_model_id: Option<String>,
    yunxiao_profile_id: Option<String>,
    gitlab_profile_id: Option<String>,
    database_profile_id: Option<String>,
) -> Result<HarnessStatus, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    if prompt.trim().is_empty() || prompt.len() > 16 * 1024 || prompt.contains('\0') {
        return Err("Harness 任务描述无效".to_owned());
    }
    let evidence_paths = validate_harness_chat_evidence_paths(chat_evidence_paths)?;
    let profile = active_profile(&foundation).map_err(|error| error.to_string())?;
    let request_id = Uuid::new_v4().to_string();
    let worktree_root = match workspace_id.as_deref() {
        Some(workspace_id) => resolve_registered_workspace(&profile.data_root, workspace_id)
            .map_err(|error| error.to_string())?,
        None => std::env::temp_dir().join("deepseek-harness-chat-worktree"),
    };
    let root = archive_root
        .map(PathBuf::from)
        .unwrap_or_else(|| profile.data_root.join("harness").join("tasks").join(&request_id));
    let task = HarnessTaskStart {
        task_contract_path: None,
        understanding_path: None,
        worktree_root,
        knowledge_home: profile.data_root.join("harness").join("knowledge"),
        authorization_id: "harness-chat".to_owned(),
        agent_backend: None,
        archive_root: Some(root),
        intake_source,
        intake_include_comments: None,
        chat_prompt: Some(prompt),
        chat_evidence_paths: Some(evidence_paths),
        selected_model_id,
        yunxiao_profile_id,
        gitlab_profile_id,
        database_profile_id,
    };
    harness
        .start(app, &foundation, request_id, task)
        .await
        .map_err(|error| error.to_string())
}

fn validate_harness_chat_evidence_paths(paths: Option<Vec<String>>) -> Result<Vec<PathBuf>, String> {
    let paths = paths.unwrap_or_default();
    if paths.len() > 20 {
        return Err("本地需求材料最多选择 20 个文件".to_owned());
    }
    let mut result = Vec::with_capacity(paths.len());
    for raw in paths {
        let path = PathBuf::from(raw);
        if !path.is_absolute() || path.to_string_lossy().contains('\0') || path.is_symlink() {
            return Err("本地需求材料路径无效".to_owned());
        }
        let canonical = path.canonicalize().map_err(|_| "本地需求材料不可读取".to_owned())?;
        let metadata = std::fs::metadata(&canonical).map_err(|_| "本地需求材料不可读取".to_owned())?;
        if !metadata.is_file() || metadata.len() > 100 * 1024 * 1024 {
            return Err("本地需求材料必须是 100MB 以内的普通文件".to_owned());
        }
        result.push(canonical);
    }
    Ok(result)
}

/// 从主聊天选择本次需求的图片、文档或附件；实际内容只在用户点击开始任务后归档。
#[tauri::command]
pub async fn harness_pick_evidence_files(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
) -> Result<Vec<String>, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let handle = app.clone();
    let picked = tauri::async_runtime::spawn_blocking(move || {
        use tauri_plugin_dialog::DialogExt;
        handle
            .dialog()
            .file()
            .set_title("添加需求图片、文档或附件")
            .blocking_pick_files()
    })
    .await
    .map_err(|_| "需求材料选择器无法打开".to_owned())?;
    let Some(files) = picked else {
        return Ok(Vec::new());
    };
    let paths = files
        .into_iter()
        .map(|file| file.into_path().map_err(|_| "需求材料路径无效".to_owned()))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(validate_harness_chat_evidence_paths(Some(
        paths.iter().map(|path| path.to_string_lossy().into_owned()).collect(),
    ))?
    .into_iter()
    .map(|path| path.to_string_lossy().into_owned())
    .collect())
}

/// 打开原生目录选择器，返回用户选择的 Harness 归档根目录。
///
/// 返回 `None` 表示用户取消；返回的路径始终是已存在目录的规范化绝对路径，
/// 供任务包直接落盘，不允许通过该命令选取文件或符号链接。
#[tauri::command]
pub async fn harness_pick_archive_root(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
) -> Result<Option<String>, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let handle = app.clone();
    let picked = tauri::async_runtime::spawn_blocking(move || {
        use tauri_plugin_dialog::DialogExt;
        handle
            .dialog()
            .file()
            .set_title("选择 Harness 归档根目录")
            .set_can_create_directories(true)
            .blocking_pick_folder()
    })
    .await
    .map_err(|_| "Harness 归档目录选择器无法打开".to_owned())?;
    let Some(file_path) = picked else {
        return Ok(None);
    };
    let path = file_path
        .into_path()
        .map_err(|_| "选择的目录路径无效".to_owned())?;
    if !path.is_absolute() || path.to_string_lossy().contains('\0') {
        return Err("选择的目录路径无效".to_owned());
    }
    let canonical = path
        .canonicalize()
        .map_err(|_| "选择的目录不可读取".to_owned())?;
    if !canonical.is_dir() {
        return Err("选择的路径不是目录".to_owned());
    }
    Ok(Some(canonical.to_string_lossy().into_owned()))
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HarnessConnectionProfileReply {
    profile_id: String,
    kind: String,
    provider_id: String,
    display_name: String,
    endpoint: String,
    read_only: bool,
    enabled: bool,
    credential_id: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HarnessConnectionTestReply {
    profile_id: String,
    tested: bool,
    test_kind: String,
    message: String,
}

fn validate_harness_connection_kind(value: &str) -> Result<(), String> {
    if matches!(value, "mcp" | "database") {
        Ok(())
    } else {
        Err("Harness 连接类型无效，只支持 mcp 或 database".to_owned())
    }
}

fn validate_harness_connection_provider(value: &str, kind: &str) -> Result<(), String> {
    let valid = match kind {
        "database" => value == "generic",
        "mcp" => matches!(value, "yunxiao" | "gitlab" | "generic"),
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err("Harness 连接归属无效：数据库使用 generic，MCP 使用 yunxiao、gitlab 或 generic".to_owned())
    }
}

fn validate_harness_connection_endpoint(value: &str) -> Result<(), String> {
    if value.len() > 4096 || value.contains('\0') || value.contains("@") {
        return Err("连接地址无效，不能包含用户密码信息".to_owned());
    }
    Ok(())
}

pub(crate) fn ensure_harness_connection_table(connection: &rusqlite::Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS harness_connection_profiles (
                profile_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('mcp', 'database')),
                provider_id TEXT NOT NULL DEFAULT 'generic',
                display_name TEXT NOT NULL,
                endpoint TEXT NOT NULL DEFAULT '',
                read_only INTEGER NOT NULL CHECK(read_only IN (0, 1)),
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                credential_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )",
        )
        .map_err(|_| "Harness 连接配置表不可用".to_owned())?;
    let _ = connection.execute(
        "ALTER TABLE harness_connection_profiles ADD COLUMN provider_id TEXT NOT NULL DEFAULT 'generic'",
        [],
    );
    Ok(())
}

fn validate_harness_profile_id(value: &str) -> Result<(), String> {
    validate_agent_identifier(value, "连接 Profile ID")
}

#[tauri::command]
pub async fn harness_connection_list(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    kind: Option<String>,
) -> Result<Vec<HarnessConnectionProfileReply>, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    if let Some(kind) = kind.as_deref() {
        validate_harness_connection_kind(kind)?;
    }
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let writer = store.writer().map_err(|error| error.to_string())?;
    ensure_harness_connection_table(writer.connection())?;
    drop(writer);
    let connection = store.reader().map_err(|error| error.to_string())?;
    let mut statement = connection
        .prepare(
            "SELECT profile_id, kind, provider_id, display_name, endpoint, read_only, enabled, credential_id
             FROM harness_connection_profiles
             WHERE (?1 IS NULL OR kind = ?1)
             ORDER BY kind, display_name, profile_id",
        )
        .map_err(|_| "Harness 连接配置读取失败".to_owned())?;
    let rows = statement
        .query_map([kind.as_deref()], |row| {
            Ok(HarnessConnectionProfileReply {
                profile_id: row.get(0)?,
                kind: row.get(1)?,
                provider_id: row.get(2)?,
                display_name: row.get(3)?,
                endpoint: row.get(4)?,
                read_only: row.get::<_, i64>(5)? != 0,
                enabled: row.get::<_, i64>(6)? != 0,
                credential_id: row.get(7)?,
            })
        })
        .map_err(|_| "Harness 连接配置读取失败".to_owned())?;
    rows.collect::<Result<Vec<_>, _>>().map_err(|_| "Harness 连接配置读取失败".to_owned())
}

#[tauri::command]
pub async fn harness_connection_save(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    profile_id: Option<String>,
    kind: String,
    provider_id: Option<String>,
    display_name: String,
    endpoint: String,
    read_only: bool,
    enabled: bool,
    credential_id: Option<String>,
) -> Result<HarnessConnectionProfileReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    validate_harness_connection_kind(&kind)?;
    let provider_id = provider_id.unwrap_or_else(|| "generic".to_owned());
    validate_harness_connection_provider(&provider_id, &kind)?;
    if display_name.trim().is_empty() || display_name.len() > 120 || display_name.contains('\0') {
        return Err("连接 Profile 名称无效".to_owned());
    }
    validate_harness_connection_endpoint(&endpoint)?;
    let profile_id = profile_id.unwrap_or_else(|| Uuid::new_v4().to_string());
    validate_harness_profile_id(&profile_id)?;
    if let Some(credential_id) = credential_id.as_deref() {
        validate_agent_identifier(credential_id, "Credential ID")?;
    }
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let read_only = read_only || kind == "database";
    let mut writer = store.writer().map_err(|error| error.to_string())?;
    ensure_harness_connection_table(writer.connection())?;
    let now = Utc::now().to_rfc3339();
    writer
        .connection_mut()
        .execute(
            "INSERT INTO harness_connection_profiles
                (profile_id, kind, provider_id, display_name, endpoint, read_only, enabled, credential_id, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?9)
             ON CONFLICT(profile_id) DO UPDATE SET
                kind = excluded.kind, provider_id = excluded.provider_id, display_name = excluded.display_name,
                endpoint = excluded.endpoint, read_only = excluded.read_only,
                enabled = excluded.enabled, credential_id = excluded.credential_id,
                updated_at = excluded.updated_at",
            rusqlite::params![profile_id, kind, provider_id, display_name.trim(), endpoint, read_only as i64, enabled as i64, credential_id, now],
        )
        .map_err(|_| "Harness 连接配置保存失败".to_owned())?;
    Ok(HarnessConnectionProfileReply {
        profile_id,
        kind,
        provider_id,
        display_name: display_name.trim().to_owned(),
        endpoint,
        read_only,
        enabled,
        credential_id,
    })
}

#[tauri::command]
pub async fn harness_connection_delete(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    profile_id: String,
) -> Result<(), String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    validate_harness_profile_id(&profile_id)?;
    let store = foundation.agent_store.as_ref().ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let mut writer = store.writer().map_err(|error| error.to_string())?;
    ensure_harness_connection_table(writer.connection())?;
    writer.connection_mut().execute("DELETE FROM harness_connection_profiles WHERE profile_id = ?1", [&profile_id]).map_err(|_| "Harness 连接配置删除失败".to_owned())?;
    Ok(())
}

#[tauri::command]
pub async fn harness_connection_test(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    profile_id: String,
) -> Result<HarnessConnectionTestReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    validate_harness_profile_id(&profile_id)?;
    let profiles = harness_connection_list(coordinator, foundation, generation_id, session_id, None).await?;
    let profile = profiles
        .iter()
        .find(|profile| profile.profile_id == profile_id)
        .ok_or_else(|| "连接 Profile 不存在".to_owned())?;
    let probe = tokio::time::timeout(
        std::time::Duration::from_secs(6),
        probe_endpoint_reachability(&profile.endpoint),
    )
    .await
    .unwrap_or(Err("探测超时".to_owned()));
    let (test_kind, message) = match probe {
        Ok(elapsed) => (
            "network-reachability",
            format!(
                "已真实连通 {endpoint}（TCP {ms}ms）；认证级验证（云效 PAT/数据库只读查询）由 Harness 只读探测阶段执行。",
                endpoint = profile.endpoint,
                ms = elapsed.as_millis()
            ),
        ),
        Err(reason) => (
            "network-unreachable",
            format!("无法连通 {endpoint}：{reason}", endpoint = profile.endpoint),
        ),
    };
    Ok(HarnessConnectionTestReply {
        profile_id,
        tested: true,
        test_kind: test_kind.to_owned(),
        message,
    })
}

/// 对 profile endpoint 做一次真实的 TCP 可达性探测（不发送凭证）。
async fn probe_endpoint_reachability(endpoint: &str) -> Result<std::time::Duration, String> {
    let (host, port) = parse_host_port(endpoint).ok_or_else(|| "地址格式无效".to_owned())?;
    let started = std::time::Instant::now();
    let stream = tokio::net::TcpStream::connect((host.as_str(), port))
        .await
        .map_err(|error| error.to_string())?;
    drop(stream);
    Ok(started.elapsed())
}

fn parse_host_port(endpoint: &str) -> Option<(String, u16)> {
    let url = url::Url::parse(endpoint).ok()?;
    let port = url.port_or_known_default()?;
    let host = url.host_str()?.to_owned();
    if host.is_empty() {
        return None;
    }
    Some((host, port))
}

/// 把用户对业务问题的答复写入任务包（analysis/business_answers.md）。
///
/// 答复是用户已确认的业务口径：下一次执行的理解补齐会把它作为最高
/// 优先级证据，已答复的问题不得再次向用户重复提问。
#[tauri::command]
pub async fn harness_archive_answers(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    archive_root: String,
    answers: String,
) -> Result<String, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let root = std::path::PathBuf::from(&archive_root);
    if !root.is_absolute()
        || root.to_string_lossy().contains('\0')
        || !root.is_dir()
        || root.is_symlink()
    {
        return Err("Harness 任务包目录无效".to_owned());
    }
    let answers = answers.trim().to_owned();
    if answers.is_empty() || answers.len() > 8_000 || answers.contains('\0') {
        return Err("业务答复内容无效（需 1-8000 字符）".to_owned());
    }
    let answers_dir = root.join("analysis");
    std::fs::create_dir_all(&answers_dir).map_err(|_| "任务包目录不可写".to_owned())?;
    let path = answers_dir.join("business_answers.md");
    let content = format!(
        "# 业务答复（用户已确认）\n\n- 记录时间：{}\n- 说明：以下为用户对理解门禁所提业务问题的答复，是最高优先级的业务口径。\n\n{}\n",
        Utc::now().to_rfc3339(),
        answers
    );
    std::fs::write(&path, content).map_err(|_| "业务答复写入失败".to_owned())?;
    let _ = &foundation.paths;
    Ok(path.to_string_lossy().into_owned())
}

#[tauri::command]
pub async fn harness_cancel(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    harness: State<'_, Arc<HarnessService>>,
    generation_id: String,
    session_id: String,
) -> Result<HarnessStatus, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    harness.cancel().await.map_err(|error| error.to_string())
}

/// 每个数据根（Profile）独立一份隔离 Codex 目录，随 Profile 切换自然分离。
fn codex_home_for(foundation: &DesktopFoundation) -> Option<std::path::PathBuf> {
    let profile = active_profile(foundation).ok()?;
    Some(profile.data_root.join("codex-home"))
}

fn validate_permission(value: &str) -> Result<(), String> {
    if ["request-approval", "smart-approval", "full-access"].contains(&value) {
        Ok(())
    } else {
        Err("权限模式无效".to_owned())
    }
}

fn validate_agent_identifier(value: &str, label: &str) -> Result<(), String> {
    let mut chars = value.chars();
    let valid = value.len() <= 128
        && chars
            .next()
            .is_some_and(|character| character.is_ascii_alphanumeric())
        && chars.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | ':' | '-')
        });
    if valid {
        Ok(())
    } else {
        Err(format!("{label} 无效"))
    }
}

fn ensure_task_session(
    foundation: &DesktopFoundation,
    task_id: Uuid,
    generation_id: &str,
    session_id: &str,
) -> Result<(), String> {
    validate_agent_identifier(session_id, "Session ID")?;
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let connection = store.reader().map_err(|error| error.to_string())?;
    let matches: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM worker_sessions
             WHERE task_id = ?1 AND generation_id = ?2 AND desktop_session_id = ?3",
            params![task_id.to_string(), generation_id, session_id],
            |row| row.get(0),
        )
        .map_err(|_| "Agent 会话不存在或 Generation 已失效".to_owned())?;
    if matches == 1 {
        Ok(())
    } else {
        Err("Agent 会话不存在或 Generation 已失效".to_owned())
    }
}

fn update_task_status(
    foundation: &DesktopFoundation,
    generation_id: String,
    session_id: String,
    task_id: Uuid,
    previous: &[&str],
    next: &str,
) -> Result<AgentTaskReply, String> {
    validate_agent_identifier(&session_id, "Session ID")?;
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let writer = store.writer().map_err(|error| error.to_string())?;
    let now = Utc::now().to_rfc3339();
    let status_placeholders = (0..previous.len())
        .map(|index| format!("?{}", index + 4))
        .collect::<Vec<_>>()
        .join(",");
    let generation_index = previous.len() + 4;
    let session_index = previous.len() + 5;
    let updated = writer
        .connection()
        .execute(
            &format!(
                "UPDATE tasks SET status = ?1, updated_at = ?2 WHERE task_id = ?3 AND status IN ({status_placeholders})
               AND EXISTS (SELECT 1 FROM worker_sessions WHERE task_id = ?3 AND generation_id = ?{generation_index} AND desktop_session_id = ?{session_index})",
            ),
            rusqlite::params_from_iter(
                std::iter::once(next.to_owned())
                    .chain(std::iter::once(now))
                    .chain(std::iter::once(task_id.to_string()))
                    .chain(previous.iter().map(|value| (*value).to_owned()))
                    .chain(std::iter::once(generation_id.clone()))
                    .chain(std::iter::once(session_id.clone())),
            ),
        )
        .map_err(|_| "Agent 任务状态更新失败".to_owned())?;
    if updated != 1 {
        return Err("Agent 任务不存在、状态不允许或 Generation 已失效".to_owned());
    }
    task_reply(foundation, task_id, &generation_id, &session_id)
}

fn task_reply(
    foundation: &DesktopFoundation,
    task_id: Uuid,
    generation_id: &str,
    session_id: &str,
) -> Result<AgentTaskReply, String> {
    validate_agent_identifier(session_id, "Session ID")?;
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let connection = store.reader().map_err(|error| error.to_string())?;
    connection
        .query_row(
            "SELECT tasks.task_id, worker_sessions.desktop_session_id, worker_sessions.generation_id,
                    agents.provider_id, tasks.agent_id, tasks.workspace_id, tasks.prompt,
                    tasks.permission_mode, tasks.status
             FROM tasks
             JOIN worker_sessions ON worker_sessions.task_id = tasks.task_id
             JOIN agents ON agents.agent_id = tasks.agent_id
             WHERE tasks.task_id = ?1 AND worker_sessions.generation_id = ?2 AND worker_sessions.desktop_session_id = ?3",
            params![task_id.to_string(), generation_id, session_id],
            |row| {
                Ok(AgentTaskReply {
                    task_id: Uuid::parse_str(&row.get::<_, String>(0)?).map_err(|_| rusqlite::Error::InvalidQuery)?,
                    worker_session_id: row.get(1)?,
                    generation_id: row.get(2)?,
                    provider_id: row.get::<_, Option<String>>(3)?.unwrap_or_else(|| "unknown".to_owned()),
                    agent_id: row.get(4)?,
                    workspace_id: row.get(5)?,
                    prompt: row.get(6)?,
                    permission: row.get(7)?,
                    status: row.get(8)?,
                })
            },
        )
        .map_err(|_| "Agent 任务不存在或 Generation 已失效".to_owned())
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentContentReferenceReply {
    content_ref_id: String,
    offset: u64,
    length: u64,
    content: String,
}

#[tauri::command]
pub async fn agent_content_reference_read(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    content_ref_id: String,
    task_id: Uuid,
    generation_id: String,
    session_id: String,
    offset: u64,
    length: u64,
) -> Result<AgentContentReferenceReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    if length > 16 * 1024 {
        return Err("内容引用读取范围过大".to_owned());
    }
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let connection = store.reader().map_err(|error| error.to_string())?;
    let (storage_path, byte_length): (String, u64) = connection
        .query_row(
            "SELECT content_references.storage_path, content_references.byte_length
             FROM content_references
             JOIN event_checkpoints ON event_checkpoints.content_ref_id = content_references.content_ref_id
             JOIN worker_sessions ON worker_sessions.task_id = event_checkpoints.task_id
               WHERE content_references.content_ref_id = ?1
               AND event_checkpoints.task_id = ?2
               AND worker_sessions.generation_id = ?3
               AND worker_sessions.desktop_session_id = ?4",
            params![content_ref_id, task_id.to_string(), generation_id, session_id],
            |row| Ok((row.get(0)?, row.get::<_, i64>(1)? as u64)),
        )
        .map_err(|_| "内容引用不存在或不属于当前任务".to_owned())?;
    let end = offset
        .checked_add(length)
        .ok_or_else(|| "内容引用范围无效".to_owned())?;
    if offset > byte_length || end > byte_length {
        return Err("内容引用范围无效".to_owned());
    }
    let path = PathBuf::from(storage_path);
    let canonical = path
        .canonicalize()
        .map_err(|_| "内容引用文件不可用".to_owned())?;
    let state_root = foundation
        .paths
        .state
        .canonicalize()
        .map_err(|_| "内容引用存储不可用".to_owned())?;
    if !canonical.starts_with(&state_root) {
        return Err("内容引用路径不受支持".to_owned());
    }
    let mut file = File::open(canonical).map_err(|_| "内容引用文件不可用".to_owned())?;
    file.seek(SeekFrom::Start(offset))
        .map_err(|_| "内容引用读取失败".to_owned())?;
    let mut bytes = vec![0_u8; length as usize];
    file.read_exact(&mut bytes)
        .map_err(|_| "内容引用读取失败".to_owned())?;
    let content = String::from_utf8(bytes).map_err(|_| "内容引用不是可显示文本".to_owned())?;
    Ok(AgentContentReferenceReply {
        content_ref_id,
        offset,
        length,
        content,
    })
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentExtensionReply {
    extension_id: String,
    extension_kind: String,
    display_name: String,
    source_kind: String,
    status: String,
    updated_at: String,
}

#[tauri::command]
pub async fn agent_extension_inventory(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<AgentExtensionReply>, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    agent_extension_inventory_sync(&foundation)
}

fn agent_extension_inventory_sync(
    foundation: &DesktopFoundation,
) -> Result<Vec<AgentExtensionReply>, String> {
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let connection = store.reader().map_err(|error| error.to_string())?;
    let mut statement = connection
        .prepare(
            "SELECT extension_id, extension_kind, display_name, source_kind, status, updated_at
             FROM extensions ORDER BY extension_id",
        )
        .map_err(|_| "扩展列表读取失败".to_owned())?;
    statement
        .query_map([], |row| {
            Ok(AgentExtensionReply {
                extension_id: row.get(0)?,
                extension_kind: row.get(1)?,
                display_name: row.get(2)?,
                source_kind: row.get(3)?,
                status: row.get(4)?,
                updated_at: row.get(5)?,
            })
        })
        .map_err(|_| "扩展列表读取失败".to_owned())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "扩展列表读取失败".to_owned())
}

#[tauri::command]
pub async fn agent_extension_install(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    generation_id: String,
    session_id: String,
    _extension_id: String,
) -> Result<(), String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    Err("扩展安装必须通过固定来源和用户确认流程".to_owned())
}

#[tauri::command]
pub async fn agent_extension_enable(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    extension_id: String,
) -> Result<AgentExtensionReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    update_extension_status(&foundation, &extension_id, "enabled")
}

#[tauri::command]
pub async fn agent_extension_disable(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    extension_id: String,
) -> Result<AgentExtensionReply, String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    update_extension_status(&foundation, &extension_id, "disabled")
}

#[tauri::command]
pub async fn agent_extension_uninstall(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    generation_id: String,
    session_id: String,
    _extension_id: String,
) -> Result<(), String> {
    coordinator
        .validate_generation(&generation_id)
        .await
        .map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    Err("扩展卸载将在可恢复的扩展管理流程中开放".to_owned())
}

fn update_extension_status(
    foundation: &DesktopFoundation,
    extension_id: &str,
    status: &str,
) -> Result<AgentExtensionReply, String> {
    if extension_id.is_empty() || extension_id.len() > 128 {
        return Err("扩展 ID 无效".to_owned());
    }
    let store = foundation
        .agent_store
        .as_ref()
        .ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let writer = store.writer().map_err(|error| error.to_string())?;
    let now = Utc::now().to_rfc3339();
    let updated = writer
        .connection()
        .execute(
            "UPDATE extensions SET status = ?1, updated_at = ?2 WHERE extension_id = ?3",
            params![status, now, extension_id],
        )
        .map_err(|_| "扩展状态更新失败".to_owned())?;
    if updated != 1 {
        return Err("扩展不存在".to_owned());
    }
    let connection = store.reader().map_err(|error| error.to_string())?;
    connection
        .query_row(
            "SELECT extension_id, extension_kind, display_name, source_kind, status, updated_at
             FROM extensions WHERE extension_id = ?1",
            params![extension_id],
            |row| {
                Ok(AgentExtensionReply {
                    extension_id: row.get(0)?,
                    extension_kind: row.get(1)?,
                    display_name: row.get(2)?,
                    source_kind: row.get(3)?,
                    status: row.get(4)?,
                    updated_at: row.get(5)?,
                })
            },
        )
        .map_err(|_| "扩展状态读取失败".to_owned())
}

#[tauri::command]
pub async fn check_app_update(
    state: State<'_, Arc<AppUpdateController>>,
    source: AppUpdateSource,
) -> Result<AppUpdateState, AppUpdateFailure> {
    state.check(source).await?;
    Ok(state.snapshot().await)
}

#[tauri::command]
pub async fn download_app_update(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<AppUpdateState, AppUpdateFailure> {
    state.download().await?;
    Ok(state.snapshot().await)
}

#[tauri::command]
pub async fn install_app_update_now(
    state: State<'_, Arc<AppUpdateController>>,
    desktop: State<'_, Arc<DesktopCoordinator>>,
) -> Result<(), AppUpdateFailure> {
    state.install_now(desktop.inner()).await
}

#[tauri::command]
pub async fn install_app_update_on_exit(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<AppUpdateState, AppUpdateFailure> {
    state.install_on_exit().await?;
    Ok(state.snapshot().await)
}

#[tauri::command]
pub async fn defer_app_update(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<AppUpdateState, AppUpdateFailure> {
    state.defer().await?;
    Ok(state.snapshot().await)
}

#[tauri::command]
pub async fn open_app_update_download(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<(), AppUpdateFailure> {
    state.open_manual_download().await
}

#[tauri::command]
pub fn take_app_update_receipt(
    state: State<'_, Arc<AppUpdateController>>,
) -> Result<Option<AppUpdateReceipt>, AppUpdateFailure> {
    state.take_completed_receipt()
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfileDraftInput {
    name: String,
    data_root: std::path::PathBuf,
    #[serde(default)]
    permission_mode: PermissionMode,
}

impl From<ProfileDraftInput> for ProfileDraft {
    fn from(value: ProfileDraftInput) -> Self {
        Self {
            name: value.name,
            data_root: value.data_root,
            permission_mode: value.permission_mode,
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfilePatchInput {
    name: Option<String>,
    data_root: Option<std::path::PathBuf>,
    permission_mode: Option<PermissionMode>,
}

impl From<ProfilePatchInput> for ProfilePatch {
    fn from(value: ProfilePatchInput) -> Self {
        Self {
            name: value.name,
            data_root: value.data_root,
            permission_mode: value.permission_mode,
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationStatusReply {
    phase: &'static str,
    source: Option<std::path::PathBuf>,
    target: Option<std::path::PathBuf>,
    bytes: Option<u64>,
    profiles: Option<usize>,
    workspaces: Option<usize>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RecoveryStatusReply {
    pub source: std::path::PathBuf,
    pub backup: std::path::PathBuf,
    pub sha256: String,
    pub length: u64,
    pub schema: i64,
    pub sidecar: std::path::PathBuf,
}

impl MigrationStatusReply {
    fn ready() -> Self {
        Self {
            phase: "ready",
            source: None,
            target: None,
            bytes: None,
            profiles: None,
            workspaces: None,
        }
    }

    fn candidate(
        phase: &'static str,
        candidate: &crate::migration::model::MigrationCandidate,
    ) -> Self {
        Self {
            phase,
            source: Some(candidate.source.clone()),
            target: Some(candidate.target.clone()),
            bytes: Some(candidate.bytes),
            profiles: Some(candidate.profiles),
            workspaces: Some(candidate.workspaces),
        }
    }
}

#[tauri::command]
pub fn migration_status(foundation: State<'_, Arc<DesktopFoundation>>) -> MigrationStatusReply {
    if foundation
        .migration_deferred
        .load(std::sync::atomic::Ordering::SeqCst)
    {
        return MigrationStatusReply {
            phase: "deferred",
            source: None,
            target: None,
            bytes: None,
            profiles: None,
            workspaces: None,
        };
    }
    match &foundation.bootstrap_state {
        FoundationBootstrapState::Ready => MigrationStatusReply::ready(),
        FoundationBootstrapState::MigrationRequired(candidate) => {
            MigrationStatusReply::candidate("candidate", candidate)
        }
        FoundationBootstrapState::MigrationConflict(candidate) => {
            MigrationStatusReply::candidate("conflict", candidate)
        }
        FoundationBootstrapState::RecoveryBlocked(_) => MigrationStatusReply::ready(),
    }
}

pub(crate) fn recovery_status_for(
    foundation: &DesktopFoundation,
) -> Result<Option<RecoveryStatusReply>, RuntimeFailure> {
    let FoundationBootstrapState::RecoveryBlocked(recovery) = &foundation.bootstrap_state else {
        return Ok(None);
    };
    if recovery.backup.is_none() {
        let mut error = RuntimeFailure::new(
            crate::runtime::model::RuntimeFailureCode::RepairRequired,
            "Agent 数据库恢复证据已丢失，已阻止启动",
        );
        error.recoverable = false;
        return Err(error);
    }
    let metadata = crate::agent_store::validate_recovery_state(&foundation.paths, recovery)
        .map_err(|_| {
            let mut error = RuntimeFailure::new(
                crate::runtime::model::RuntimeFailureCode::RepairRequired,
                "恢复证据验证失败",
            );
            error.recoverable = false;
            error
        })?;
    Ok(Some(RecoveryStatusReply {
        source: recovery.source_path.clone(),
        backup: metadata.backup_path,
        sha256: metadata.sha256,
        length: metadata.byte_length,
        schema: metadata.schema_version,
        sidecar: metadata.metadata_path,
    }))
}

#[tauri::command]
pub fn recovery_status(
    foundation: State<'_, Arc<DesktopFoundation>>,
) -> Result<Option<RecoveryStatusReply>, RuntimeFailure> {
    recovery_status_for(&foundation)
}

#[tauri::command]
pub async fn confirm_migration(
    app: AppHandle,
    foundation: State<'_, Arc<DesktopFoundation>>,
) -> Result<(), RuntimeFailure> {
    let candidate = match &foundation.bootstrap_state {
        FoundationBootstrapState::MigrationRequired(candidate) => candidate.clone(),
        FoundationBootstrapState::Ready => return Ok(()),
        FoundationBootstrapState::MigrationConflict(_) => {
            return Err(RuntimeFailure::new(
                crate::runtime::model::RuntimeFailureCode::MigrationConflict,
                "新旧目录都有数据，不能自动迁移",
            ));
        }
        FoundationBootstrapState::RecoveryBlocked(_) => {
            foundation.runtime_allowed()?;
            unreachable!("blocked foundation cannot confirm migration")
        }
    };
    let migration = Arc::clone(&foundation.migration);
    tauri::async_runtime::spawn_blocking(move || {
        let plan = migration.plan(&candidate.source)?;
        migration.execute(&plan)
    })
    .await
    .map_err(RuntimeFailure::internal)??;
    app.restart();
}

#[tauri::command]
pub fn defer_migration(foundation: State<'_, Arc<DesktopFoundation>>) {
    foundation
        .migration_deferred
        .store(true, std::sync::atomic::Ordering::SeqCst);
}

#[tauri::command]
pub async fn bootstrap_runtime(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
) -> Result<BootstrapReply, RuntimeFailure> {
    foundation.runtime_allowed()?;
    state.inner().start().await
}

#[cfg(feature = "e2e")]
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct E2eRuntimeIdentityReply {
    runtime_pid: u32,
}

#[cfg(feature = "e2e")]
#[tauri::command]
pub async fn e2e_runtime_identity(
    state: State<'_, Arc<DesktopCoordinator>>,
    generation_id: String,
) -> Result<E2eRuntimeIdentityReply, RuntimeFailure> {
    Ok(E2eRuntimeIdentityReply {
        runtime_pid: state.active_runtime_pid(&generation_id).await?,
    })
}

#[tauri::command]
pub async fn cancel_runtime(
    state: State<'_, Arc<DesktopCoordinator>>,
) -> Result<(), RuntimeFailure> {
    state.inner().cancel().await
}

#[tauri::command]
pub async fn repair_runtime(
    state: State<'_, Arc<DesktopCoordinator>>,
    launcher: State<'_, Arc<AppLauncher>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
) -> Result<BootstrapReply, RuntimeFailure> {
    foundation.runtime_allowed()?;
    // 修复运行时会重建受管进程环境，先停掉所有本地应用避免悬挂引用。
    launcher.inner().stop_all().await;
    state.inner().repair().await
}

#[tauri::command]
pub async fn export_diagnostics(
    state: State<'_, Arc<DesktopCoordinator>>,
    generation_id: Option<String>,
) -> Result<String, RuntimeFailure> {
    if let Some(generation_id) = generation_id {
        state.validate_generation(&generation_id).await?;
    }
    state.inner().export_diagnostics().await
}

#[tauri::command]
pub async fn switch_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    launcher: State<'_, Arc<AppLauncher>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    profile_id: uuid::Uuid,
    generation_id: Option<String>,
) -> Result<BootstrapReply, RuntimeFailure> {
    foundation.runtime_allowed()?;
    if let Some(generation_id) = generation_id {
        state.validate_generation(&generation_id).await?;
    }
    // 切换 Profile 会更换 data_root，通过校验后先停止全部本地应用再切换。
    launcher.inner().stop_all().await;
    state.inner().switch_profile(profile_id).await
}

#[tauri::command]
pub async fn list_profiles(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
) -> Result<ProfileListSnapshot, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation.profiles.snapshot()
}

#[tauri::command]
pub async fn list_project_metadata(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    metadata_repository(&foundation)?.snapshot()
}

#[tauri::command]
pub async fn patch_project_metadata(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    workspace_id: String,
    patch: ProjectMetadataPatch,
) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    metadata_repository(&foundation)?.patch(&workspace_id, patch)
}

#[tauri::command]
pub async fn remove_project_metadata(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    workspace_id: String,
) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    metadata_repository(&foundation)?.remove(&workspace_id)
}

#[tauri::command]
pub async fn recycle_project_directory(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    workspace_id: String,
) -> Result<std::path::PathBuf, RuntimeFailure> {
    coordinator.validate_generation(&generation_id).await?;
    let profile = active_profile(&foundation)?;
    let target = resolve_registered_workspace(&profile.data_root, &workspace_id)?;
    let protected = ProtectedRoots::detect(
        &target,
        foundation.paths.active_root.clone(),
        profile.data_root,
        foundation.paths.runtime.clone(),
    )?;
    validate_recycle_target(&target, &protected)?;

    let platform = Arc::clone(&foundation.platform);
    let recycled = target.clone();
    tokio::task::spawn_blocking(move || platform.move_to_recycle_bin(&target))
        .await
        .map_err(RuntimeFailure::internal)??;
    Ok(recycled)
}

#[tauri::command]
pub async fn app_launch(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    launcher: State<'_, Arc<AppLauncher>>,
    generation_id: String,
    workspace_id: String,
) -> Result<LaunchReply, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    let profile = active_profile(&foundation)?;
    let documents = foundation.platform.documents_dir()?;
    Arc::clone(launcher.inner())
        .launch(&profile, &documents, &workspace_id)
        .await
}

#[tauri::command]
pub async fn app_stop(
    state: State<'_, Arc<DesktopCoordinator>>,
    launcher: State<'_, Arc<AppLauncher>>,
    generation_id: String,
    workspace_id: String,
) -> Result<(), RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    launcher.inner().stop(&workspace_id).await
}

#[tauri::command]
pub async fn app_status(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    launcher: State<'_, Arc<AppLauncher>>,
    generation_id: String,
) -> Result<AppStatusReply, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    let profile = active_profile(&foundation)?;
    let documents = foundation.platform.documents_dir()?;
    Ok(launcher.inner().status(&profile, &documents))
}

#[tauri::command]
pub async fn preview_default_project_directory(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    idea: String,
) -> Result<ProjectLocationPreview, RuntimeFailure> {
    coordinator.validate_generation(&generation_id).await?;
    active_profile(&foundation)?;
    let documents = foundation.platform.documents_dir()?;
    tokio::task::spawn_blocking(move || preview_project_location(&idea, &documents))
        .await
        .map_err(RuntimeFailure::internal)?
}

#[tauri::command]
pub async fn create_default_project_directory(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    project_name: String,
) -> Result<std::path::PathBuf, RuntimeFailure> {
    coordinator.validate_generation(&generation_id).await?;
    active_profile(&foundation)?;
    let documents = foundation.platform.documents_dir()?;
    tokio::task::spawn_blocking(move || create_project_location(&project_name, &documents))
        .await
        .map_err(RuntimeFailure::internal)?
}

#[tauri::command]
pub async fn create_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    draft: ProfileDraftInput,
) -> Result<ProfileRecord, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation.profiles.create(draft.into())
}

#[tauri::command]
pub async fn update_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    profile_id: uuid::Uuid,
    expected_revision: u64,
    patch: ProfilePatchInput,
) -> Result<ProfileRecord, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation
        .profiles
        .update(&profile_id, expected_revision, patch.into())
}

#[tauri::command]
pub async fn duplicate_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    profile_id: uuid::Uuid,
    draft: ProfileDraftInput,
) -> Result<ProfileRecord, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation.profiles.duplicate(&profile_id, draft.into())
}

#[tauri::command]
pub async fn delete_profile(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    profile_id: uuid::Uuid,
) -> Result<(), RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    foundation.profiles.delete(&profile_id)
}

#[tauri::command]
pub async fn open_external_https(
    app: AppHandle,
    state: State<'_, Arc<DesktopCoordinator>>,
    generation_id: String,
    url: String,
) -> Result<(), RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    let url = crate::navigation::validated_https(&url)?;
    app.opener()
        .open_url(url.as_str(), None::<&str>)
        .map_err(RuntimeFailure::internal)
}

#[tauri::command]
pub fn open_user_data(
    app: AppHandle,
    foundation: State<'_, Arc<DesktopFoundation>>,
) -> Result<(), RuntimeFailure> {
    let root = foundation
        .paths
        .active_root
        .canonicalize()
        .map_err(RuntimeFailure::internal)?;
    app.opener()
        .open_path(root.to_string_lossy().into_owned(), None::<&str>)
        .map_err(RuntimeFailure::internal)
}

#[tauri::command]
pub async fn restart_runtime(
    state: State<'_, Arc<DesktopCoordinator>>,
    launcher: State<'_, Arc<AppLauncher>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
) -> Result<BootstrapReply, RuntimeFailure> {
    foundation.runtime_allowed()?;
    // 重启运行时前先停掉所有本地应用，避免残留进程占用旧运行时。
    launcher.inner().stop_all().await;
    state.inner().restart().await
}

#[tauri::command]
pub async fn orderly_quit(
    app: AppHandle,
    state: State<'_, Arc<DesktopCoordinator>>,
    launcher: State<'_, Arc<AppLauncher>>,
) -> Result<(), RuntimeFailure> {
    // 有序退出：先停本地应用，再关闭受管运行时。
    launcher.inner().stop_all().await;
    state.inner().shutdown().await?;
    // Let the invoke response reach the caller before the WebView and its driver
    // disappear. This also gives packaged E2E teardown a chance to close cleanly.
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        app.exit(0);
    });
    Ok(())
}

#[tauri::command]
pub fn hide_window(window: WebviewWindow) -> Result<(), String> {
    window.hide().map_err(|cause| cause.to_string())
}

#[tauri::command]
pub fn minimize_window(window: WebviewWindow) -> Result<(), String> {
    window.minimize().map_err(|cause| cause.to_string())
}

#[tauri::command]
pub fn toggle_maximize_window(window: WebviewWindow) -> Result<(), String> {
    if window.is_maximized().map_err(|cause| cause.to_string())? {
        window.unmaximize().map_err(|cause| cause.to_string())
    } else {
        window.maximize().map_err(|cause| cause.to_string())
    }
}

#[tauri::command]
pub fn start_drag(window: WebviewWindow) -> Result<(), String> {
    window.start_dragging().map_err(|cause| cause.to_string())
}

fn parse_prompt_target(value: &str) -> Result<crate::prompts::model::PromptTarget, String> {
    crate::prompts::model::PromptTarget::parse(value)
        .ok_or_else(|| format!("未知提示词目标: {value}"))
}

fn flow_to_outcome_value(
    flow: crate::prompts::model::Flow<crate::prompts::model::SaveOutcome>,
) -> Result<serde_json::Value, String> {
    match flow {
        crate::prompts::model::Flow::Done(outcome) => {
            serde_json::to_value(outcome).map_err(|error| error.to_string())
        }
        crate::prompts::model::Flow::Conflict { preset_id, candidates } => serde_json::to_value(
            crate::prompts::model::SaveOutcome::BackfillConflict { preset_id, candidates },
        )
        .map_err(|error| error.to_string()),
    }
}

#[tauri::command]
pub async fn prompts_list(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<crate::prompts::model::PresetSummary>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.list().map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_get(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: String,
) -> Result<crate::prompts::model::PromptPreset, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.get(&preset_id).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_save(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: Option<String>,
    title: String,
    content: String,
) -> Result<serde_json::Value, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let flow = service.save(preset_id.as_deref(), &title, &content).map_err(|error| error.to_string())?;
    flow_to_outcome_value(flow)
}

#[tauri::command]
pub async fn prompts_resolve_conflict(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: String,
    title: String,
    content: String,
) -> Result<serde_json::Value, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let flow = service.resolve_save_conflict(&preset_id, &title, &content).map_err(|error| error.to_string())?;
    flow_to_outcome_value(flow)
}

#[tauri::command]
pub async fn prompts_delete(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: String,
) -> Result<(), String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.delete(&preset_id).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_activate(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    preset_id: String,
    target: String,
) -> Result<crate::prompts::model::ActivateOutcome, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let target = parse_prompt_target(&target)?;
    service.activate(&preset_id, target).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_deactivate(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    target: String,
) -> Result<crate::prompts::model::TargetStatus, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let target = parse_prompt_target(&target)?;
    service.deactivate(target).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<crate::prompts::model::TargetStatus>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.status().map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn prompts_import(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::prompts::service::PromptsService>>,
    generation_id: String,
    session_id: String,
    targets: Vec<String>,
) -> Result<Vec<crate::prompts::model::PresetSummary>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let parsed = targets
        .iter()
        .map(|value| parse_prompt_target(value))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    service.import(&parsed).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::{
        VERSIONED_AGENT_COMMAND_NAMES, credential_provider_display_name, parse_agent_selection,
        validate_credential_provider,
    };

    #[test]
    fn agent_selection_defaults_to_codex_and_rejects_mismatched_ids() {
        let selection = parse_agent_selection(None, None).unwrap();
        assert_eq!(selection.provider_id, "codex");
        assert_eq!(selection.agent_id, "codex:default");

        let selection =
            parse_agent_selection(Some("claude".to_owned()), Some("claude:default".to_owned()))
                .unwrap();
        assert_eq!(selection.provider_id, "claude");
        assert!(parse_agent_selection(Some("claude".to_owned()), None).is_err());
        assert!(
            parse_agent_selection(Some("claude".to_owned()), Some("codex:default".to_owned()))
                .is_err()
        );
    }

    #[test]
    fn versioned_agent_command_inventory_covers_every_bridge_action() {
        for command in [
            "agent_capability_inventory",
            "agent_provider_metadata",
            "agent_credential_put",
            "agent_credential_delete",
            "agent_credential_status",
            "agent_credential_test",
            "agent_cli_path_select",
            "agent_cli_path_status",
            "agent_task_create",
            "agent_task_list",
            "agent_task_recover",
            "agent_task_start",
            "agent_task_cancel",
            "agent_task_resume",
            "agent_pending_approvals",
            "agent_resolve_approval",
            "agent_content_reference_read",
            "agent_extension_inventory",
            "agent_extension_install",
            "agent_extension_enable",
            "agent_extension_disable",
            "agent_extension_uninstall",
            "harness_connection_list",
            "harness_connection_save",
            "harness_connection_delete",
            "harness_connection_test",
        ] {
            assert!(
                VERSIONED_AGENT_COMMAND_NAMES.contains(&command),
                "missing {command}"
            );
        }
    }

    #[test]
    fn deepseek_is_a_credential_provider_but_not_a_cli_agent_provider() {
        assert!(validate_credential_provider("deepseek").is_ok());
        assert_eq!(credential_provider_display_name("deepseek"), Ok("DeepSeek"));
        assert!(super::parse_provider("deepseek").is_err());
    }
}
