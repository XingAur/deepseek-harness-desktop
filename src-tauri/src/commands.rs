use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
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
    "agent_skill_create",
    "agent_skill_import",
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
    transport: String,
    source: String,
    template_id: String,
    provider_id: String,
    display_name: String,
    endpoint: String,
    command: String,
    args: Vec<String>,
    environment_keys: Vec<String>,
    working_directory_policy: String,
    health_path: String,
    database_type: String,
    host: String,
    port: u16,
    database_name: String,
    username: String,
    encoding: String,
    test_query: String,
    read_only: bool,
    enabled: bool,
    credential_id: Option<String>,
    latest_test: Option<serde_json::Value>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HarnessConnectionTestLayerReply {
    id: String,
    label: String,
    state: String,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HarnessConnectionTestReply {
    profile_id: String,
    tested: bool,
    test_kind: String,
    message: String,
    summary: String,
    layers: Vec<HarnessConnectionTestLayerReply>,
}

fn validate_harness_connection_kind(value: &str) -> Result<(), String> {
    if matches!(value, "mcp" | "http-api" | "database") {
        Ok(())
    } else {
        Err("Harness 连接类型无效，只支持 mcp、http-api 或 database".to_owned())
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

pub(crate) fn ensure_harness_connection_tables(connection: &rusqlite::Connection) -> Result<(), String> {
    ensure_harness_connection_table(connection)?;
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS harness_connection_profiles_v2 (
                profile_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('mcp', 'http-api', 'database')),
                transport TEXT NOT NULL CHECK(transport IN ('stdio', 'http', 'sse', 'database')),
                source TEXT NOT NULL DEFAULT 'custom',
                template_id TEXT NOT NULL DEFAULT 'custom',
                provider_id TEXT NOT NULL DEFAULT 'generic',
                display_name TEXT NOT NULL,
                endpoint TEXT NOT NULL DEFAULT '',
                command TEXT NOT NULL DEFAULT '',
                args_json TEXT NOT NULL DEFAULT '[]',
                environment_keys_json TEXT NOT NULL DEFAULT '[]',
                working_directory_policy TEXT NOT NULL DEFAULT 'none'
                    CHECK(working_directory_policy IN ('workspace', 'inherit', 'none')),
                health_path TEXT NOT NULL DEFAULT '',
                database_type TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL DEFAULT '',
                port INTEGER NOT NULL DEFAULT 0,
                database_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                encoding TEXT NOT NULL DEFAULT '',
                test_query TEXT NOT NULL DEFAULT '',
                read_only INTEGER NOT NULL CHECK(read_only IN (0, 1)),
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                credential_id TEXT,
                last_test_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )",
        )
        .map_err(|_| "Harness 通用连接配置表不可用".to_owned())?;
    for statement in [
        "ALTER TABLE harness_connection_profiles_v2 ADD COLUMN database_type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE harness_connection_profiles_v2 ADD COLUMN host TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE harness_connection_profiles_v2 ADD COLUMN port INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE harness_connection_profiles_v2 ADD COLUMN database_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE harness_connection_profiles_v2 ADD COLUMN username TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE harness_connection_profiles_v2 ADD COLUMN encoding TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE harness_connection_profiles_v2 ADD COLUMN test_query TEXT NOT NULL DEFAULT ''",
    ] {
        let _ = connection.execute(statement, []);
    }
    Ok(())
}

fn canonical_database_endpoint(database_type: &str, host: &str, port: u16, database_name: &str) -> Result<String, String> {
    if !matches!(database_type, "postgresql" | "mysql" | "sqlserver" | "oracle") {
        return Err("数据库类型无效".to_owned());
    }
    if host.trim().is_empty() || host.len() > 253 || host.contains(['\0', '/', '@']) {
        return Err("数据库主机无效".to_owned());
    }
    if database_name.trim().is_empty() || database_name.len() > 256 || database_name.contains(['\0', '/', '?', '#']) {
        return Err("数据库名称无效".to_owned());
    }
    let mut endpoint = url::Url::parse(&format!("{database_type}://localhost")).map_err(|_| "数据库类型无效".to_owned())?;
    endpoint.set_host(Some(host.trim())).map_err(|_| "数据库主机无效".to_owned())?;
    endpoint.set_port(Some(port)).map_err(|_| "数据库端口无效".to_owned())?;
    endpoint.set_path(database_name.trim());
    Ok(endpoint.to_string())
}

fn validate_harness_connection_transport(kind: &str, transport: &str) -> Result<(), String> {
    let valid = match kind {
        "mcp" => matches!(transport, "stdio" | "http" | "sse"),
        "http-api" => transport == "http",
        "database" => transport == "database",
        _ => false,
    };
    valid.then_some(()).ok_or_else(|| "连接类型与传输方式不匹配".to_owned())
}

fn validate_harness_connection_command(value: &str) -> Result<(), String> {
    if value.len() > 4096 || value.contains(['\0', '\r', '\n']) {
        Err("连接命令无效".to_owned())
    } else {
        Ok(())
    }
}

fn validate_harness_connection_arguments(values: &[String]) -> Result<(), String> {
    if values.len() > 32 || values.iter().any(|value| value.len() > 512 || value.contains(['\0', '\r', '\n'])) {
        Err("连接命令参数超出安全限制".to_owned())
    } else {
        Ok(())
    }
}

fn validate_harness_environment_keys(values: &[String]) -> Result<(), String> {
    let valid = values.len() <= 32 && values.iter().all(|value| {
        value.len() <= 128
            && value.chars().next().is_some_and(|character| character.is_ascii_alphabetic() || character == '_')
            && value.chars().all(|character| character.is_ascii_alphanumeric() || character == '_')
    });
    valid.then_some(()).ok_or_else(|| "环境变量只能填写名称，不能填写值".to_owned())
}

fn validate_generalized_endpoint(value: &str, transport: &str) -> Result<(), String> {
    validate_harness_connection_endpoint(value)?;
    if transport == "stdio" {
        return value.is_empty().then_some(()).ok_or_else(|| "stdio 连接不能填写网络地址".to_owned());
    }
    let parsed = url::Url::parse(value).map_err(|_| "连接地址格式无效".to_owned())?;
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("连接地址不能包含用户密码信息".to_owned());
    }
    if matches!(transport, "http" | "sse") {
        let loopback = matches!(parsed.host_str(), Some("127.0.0.1" | "localhost" | "::1"));
        if parsed.scheme() != "https" && !(parsed.scheme() == "http" && loopback) {
            return Err("仅允许 HTTPS 地址，回环地址可使用 HTTP".to_owned());
        }
    }
    Ok(())
}

fn validate_health_path(value: &str) -> Result<(), String> {
    if value.len() > 512 || value.contains('\0') || (!value.is_empty() && !value.starts_with('/')) {
        Err("健康检查路径无效".to_owned())
    } else {
        Ok(())
    }
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
    ensure_harness_connection_tables(writer.connection())?;
    drop(writer);
    let connection = store.reader().map_err(|error| error.to_string())?;
    let mut generalized_statement = connection
        .prepare(
            "SELECT profile_id, kind, transport, source, template_id, provider_id, display_name, endpoint,
                    command, args_json, environment_keys_json, working_directory_policy, health_path,
                    database_type, host, port, database_name, username, encoding, test_query,
                    read_only, enabled, credential_id, last_test_json
             FROM harness_connection_profiles_v2
             WHERE (?1 IS NULL OR kind = ?1)
             ORDER BY kind, display_name, profile_id",
        )
        .map_err(|_| "Harness 通用连接配置读取失败".to_owned())?;
    let mut profiles = generalized_statement
        .query_map([kind.as_deref()], |row| {
            let args_json: String = row.get(9)?;
            let environment_keys_json: String = row.get(10)?;
            let last_test_json: Option<String> = row.get(23)?;
            Ok(HarnessConnectionProfileReply {
                profile_id: row.get(0)?,
                kind: row.get(1)?,
                transport: row.get(2)?,
                source: row.get(3)?,
                template_id: row.get(4)?,
                provider_id: row.get(5)?,
                display_name: row.get(6)?,
                endpoint: row.get(7)?,
                command: row.get(8)?,
                args: serde_json::from_str(&args_json).unwrap_or_default(),
                environment_keys: serde_json::from_str(&environment_keys_json).unwrap_or_default(),
                working_directory_policy: row.get(11)?,
                health_path: row.get(12)?,
                database_type: row.get(13)?,
                host: row.get(14)?,
                port: row.get::<_, i64>(15)?.try_into().unwrap_or_default(),
                database_name: row.get(16)?,
                username: row.get(17)?,
                encoding: row.get(18)?,
                test_query: row.get(19)?,
                read_only: row.get::<_, i64>(20)? != 0,
                enabled: row.get::<_, i64>(21)? != 0,
                credential_id: row.get(22)?,
                latest_test: last_test_json.and_then(|value| serde_json::from_str(&value).ok()),
            })
        })
        .map_err(|_| "Harness 通用连接配置读取失败".to_owned())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "Harness 通用连接配置读取失败".to_owned())?;
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
                transport: if row.get::<_, String>(1)? == "database" { "database".to_owned() } else { "http".to_owned() },
                source: "legacy".to_owned(),
                template_id: row.get(2)?,
                provider_id: row.get(2)?,
                display_name: row.get(3)?,
                endpoint: row.get(4)?,
                command: String::new(),
                args: Vec::new(),
                environment_keys: Vec::new(),
                working_directory_policy: "none".to_owned(),
                health_path: String::new(),
                database_type: String::new(),
                host: String::new(),
                port: 0,
                database_name: String::new(),
                username: String::new(),
                encoding: String::new(),
                test_query: String::new(),
                read_only: row.get::<_, i64>(5)? != 0,
                enabled: row.get::<_, i64>(6)? != 0,
                credential_id: row.get(7)?,
                latest_test: None,
            })
        })
        .map_err(|_| "Harness 连接配置读取失败".to_owned())?;
    let legacy = rows.collect::<Result<Vec<_>, _>>().map_err(|_| "Harness 连接配置读取失败".to_owned())?;
    let generalized_ids = profiles.iter().map(|profile| profile.profile_id.clone()).collect::<std::collections::HashSet<_>>();
    profiles.extend(legacy.into_iter().filter(|legacy| !generalized_ids.contains(&legacy.profile_id)));
    profiles.sort_by(|left, right| (&left.kind, &left.display_name, &left.profile_id).cmp(&(&right.kind, &right.display_name, &right.profile_id)));
    Ok(profiles)
}

#[tauri::command]
pub async fn harness_connection_save(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    profile_id: Option<String>,
    kind: String,
    transport: Option<String>,
    template_id: Option<String>,
    provider_id: Option<String>,
    display_name: String,
    mut endpoint: String,
    command: Option<String>,
    args: Option<Vec<String>>,
    environment_keys: Option<Vec<String>>,
    working_directory_policy: Option<String>,
    health_path: Option<String>,
    database_type: Option<String>,
    host: Option<String>,
    port: Option<u16>,
    database_name: Option<String>,
    username: Option<String>,
    encoding: Option<String>,
    test_query: Option<String>,
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
    let generalized = transport.is_some() || kind == "http-api" || template_id.is_some() || command.is_some();
    let provider_id = provider_id.unwrap_or_else(|| "generic".to_owned());
    validate_agent_identifier(&provider_id, "Harness 连接归属")?;
    if !generalized {
        validate_harness_connection_provider(&provider_id, &kind)?;
    }
    if display_name.trim().is_empty() || display_name.len() > 120 || display_name.contains('\0') {
        return Err("连接 Profile 名称无效".to_owned());
    }
    let transport = transport.unwrap_or_else(|| if kind == "database" { "database".to_owned() } else { "http".to_owned() });
    let template_id = template_id.unwrap_or_else(|| provider_id.clone());
    validate_agent_identifier(&template_id, "连接模板 ID")?;
    let command = command.unwrap_or_default();
    let args = args.unwrap_or_default();
    let environment_keys = environment_keys.unwrap_or_default();
    let working_directory_policy = working_directory_policy.unwrap_or_else(|| if transport == "stdio" { "workspace".to_owned() } else { "none".to_owned() });
    let health_path = health_path.unwrap_or_default();
    let database_type = database_type.unwrap_or_default();
    let host = host.unwrap_or_default();
    let port = port.unwrap_or_default();
    let database_name = database_name.unwrap_or_default();
    let username = username.unwrap_or_default();
    let encoding = encoding.unwrap_or_default();
    let test_query = test_query.unwrap_or_default();
    if kind == "database" && generalized {
        endpoint = canonical_database_endpoint(&database_type, &host, port, &database_name)?;
        if username.trim().is_empty() || username.len() > 256 || username.contains('\0') {
            return Err("数据库用户名无效".to_owned());
        }
        if encoding.is_empty() || encoding.len() > 32 || !encoding.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-')) {
            return Err("数据库编码无效".to_owned());
        }
        if test_query.trim().is_empty() || test_query.len() > 1024 || test_query.contains('\0') {
            return Err("数据库连接测试语句无效".to_owned());
        }
    }
    if generalized {
        validate_harness_connection_transport(&kind, &transport)?;
        validate_generalized_endpoint(&endpoint, &transport)?;
        validate_harness_connection_command(&command)?;
        if transport == "stdio" && command.trim().is_empty() { return Err("MCP stdio 连接必须填写命令".to_owned()); }
        validate_harness_connection_arguments(&args)?;
        validate_harness_environment_keys(&environment_keys)?;
        if !matches!(working_directory_policy.as_str(), "workspace" | "inherit" | "none") { return Err("工作目录策略无效".to_owned()); }
        validate_health_path(&health_path)?;
    } else {
        validate_harness_connection_endpoint(&endpoint)?;
    }
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
    ensure_harness_connection_tables(writer.connection())?;
    let now = Utc::now().to_rfc3339();
    if generalized {
        let args_json = serde_json::to_string(&args).map_err(|_| "连接命令参数无效".to_owned())?;
        let environment_keys_json = serde_json::to_string(&environment_keys).map_err(|_| "环境变量名称无效".to_owned())?;
        writer.connection_mut().execute(
            "INSERT INTO harness_connection_profiles_v2
                (profile_id, kind, transport, source, template_id, provider_id, display_name, endpoint, command,
                 args_json, environment_keys_json, working_directory_policy, health_path, database_type, host, port,
                 database_name, username, encoding, test_query, read_only, enabled, credential_id, created_at, updated_at)
             VALUES (?1, ?2, ?3, 'custom', ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23, ?23)
             ON CONFLICT(profile_id) DO UPDATE SET
                kind = excluded.kind, transport = excluded.transport, source = excluded.source,
                template_id = excluded.template_id, provider_id = excluded.provider_id,
                display_name = excluded.display_name, endpoint = excluded.endpoint, command = excluded.command,
                args_json = excluded.args_json, environment_keys_json = excluded.environment_keys_json,
                working_directory_policy = excluded.working_directory_policy, health_path = excluded.health_path,
                database_type = excluded.database_type, host = excluded.host, port = excluded.port,
                database_name = excluded.database_name, username = excluded.username, encoding = excluded.encoding,
                test_query = excluded.test_query,
                read_only = excluded.read_only, enabled = excluded.enabled, credential_id = excluded.credential_id,
                updated_at = excluded.updated_at",
            rusqlite::params![profile_id, kind, transport, template_id, provider_id, display_name.trim(), endpoint, command, args_json, environment_keys_json, working_directory_policy, health_path, database_type, host, port, database_name, username, encoding, test_query, read_only as i64, enabled as i64, credential_id, now],
        ).map_err(|_| "Harness 通用连接配置保存失败".to_owned())?;
    } else {
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
    }
    Ok(HarnessConnectionProfileReply {
        profile_id,
        kind,
        transport,
        source: if generalized { "custom".to_owned() } else { "legacy".to_owned() },
        template_id,
        provider_id,
        display_name: display_name.trim().to_owned(),
        endpoint,
        command,
        args,
        environment_keys,
        working_directory_policy,
        health_path,
        database_type,
        host,
        port,
        database_name,
        username,
        encoding,
        test_query,
        read_only,
        enabled,
        credential_id,
        latest_test: None,
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
    ensure_harness_connection_tables(writer.connection())?;
    let deleted = writer.connection_mut().execute("DELETE FROM harness_connection_profiles_v2 WHERE profile_id = ?1", [&profile_id]).map_err(|_| "Harness 通用连接配置删除失败".to_owned())?;
    if deleted == 0 {
        writer.connection_mut().execute("DELETE FROM harness_connection_profiles WHERE profile_id = ?1", [&profile_id]).map_err(|_| "Harness 连接配置删除失败".to_owned())?;
    }
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
    let foundation_for_update = foundation.inner().clone();
    let profiles = harness_connection_list(coordinator, foundation, generation_id, session_id, None).await?;
    let profile = profiles
        .iter()
        .find(|profile| profile.profile_id == profile_id)
        .cloned()
        .ok_or_else(|| "连接 Profile 不存在".to_owned())?;
    let reply = if profile.source == "legacy" {
        let probe = tokio::time::timeout(
            std::time::Duration::from_secs(6),
            probe_endpoint_reachability(&profile.endpoint),
        )
        .await
        .unwrap_or(Err("探测超时".to_owned()));
        legacy_connection_test_reply(profile_id.clone(), probe, &profile.endpoint)
    } else if profile.kind == "http-api" {
        let network = bounded_endpoint_probe(&profile.endpoint).await;
        let health = if network.is_ok() && !profile.health_path.is_empty() {
            Some(bounded_http_health_probe(&profile.endpoint, &profile.health_path).await)
        } else {
            None
        };
        http_api_connection_test_reply(profile_id.clone(), profile.endpoint.clone(), profile.credential_id.is_some(), network, health)
    } else if profile.kind == "mcp" && matches!(profile.template_id.as_str(), "yunxiao" | "gitlab") {
        let provider_label = if profile.template_id == "yunxiao" { "云效" } else { "GitLab" };
        let network = bounded_endpoint_probe(&profile.endpoint).await;
        provider_connection_test_reply(profile_id.clone(), provider_label, &profile.endpoint, profile.credential_id.is_some(), network)
    } else if profile.kind == "mcp" {
        let (transport, network, handshake) = if profile.transport == "stdio" {
            match tokio::time::timeout(std::time::Duration::from_secs(6), probe_mcp_stdio(&profile)).await {
                Ok(Ok((elapsed, outcome))) => ("stdio".to_owned(), Ok(elapsed), Ok(outcome)),
                Ok(Err(reason)) => ("stdio".to_owned(), Err(reason.clone()), Err(reason)),
                Err(_) => ("stdio".to_owned(), Err("探测超时".to_owned()), Err("MCP stdio 握手超时".to_owned())),
            }
        } else {
            let network = bounded_endpoint_probe(&profile.endpoint).await;
            let handshake = if network.is_ok() {
                tokio::time::timeout(std::time::Duration::from_secs(6), probe_mcp_http(&profile.endpoint)).await
                    .unwrap_or(Err("MCP 远程握手超时".to_owned()))
            } else {
                Err("网络不可达，未执行 MCP 握手".to_owned())
            };
            (profile.transport.to_uppercase(), network, handshake)
        };
        mcp_connection_test_reply(profile_id.clone(), transport, profile.credential_id.is_some(), network, handshake)
    } else {
        let probe = bounded_endpoint_probe(&profile.endpoint).await;
        legacy_connection_test_reply(profile_id.clone(), probe, &profile.endpoint)
    };
    persist_generalized_connection_test(&foundation_for_update, &profile_id, &reply)?;
    Ok(reply)
}

fn connection_test_layer(id: &str, label: &str, state: &str, message: impl Into<String>) -> HarnessConnectionTestLayerReply {
    HarnessConnectionTestLayerReply { id: id.to_owned(), label: label.to_owned(), state: state.to_owned(), message: message.into() }
}

fn legacy_connection_test_reply(profile_id: String, probe: Result<std::time::Duration, String>, endpoint: &str) -> HarnessConnectionTestReply {
    let (network_state, test_kind, message, summary) = match probe {
        Ok(elapsed) => (
            "passed", "network-reachability",
            format!("已真实连通 {endpoint}（TCP {ms}ms）", ms = elapsed.as_millis()),
            "网络可达，其他层未验证",
        ),
        Err(reason) => (
            "failed", "network-unreachable",
            format!("无法连通 {endpoint}：{reason}"),
            "网络不可达",
        ),
    };
    HarnessConnectionTestReply {
        profile_id,
        tested: true,
        test_kind: test_kind.to_owned(),
        message: message.clone(),
        summary: summary.to_owned(),
        layers: vec![
            connection_test_layer("configuration", "配置", "passed", "Profile 结构与地址格式有效"),
            connection_test_layer("network", "网络", network_state, message),
            connection_test_layer("protocol", "协议", "not-tested", "兼容连接未提供协议级探测"),
            connection_test_layer("authentication", "认证", "not-tested", "网络结果不能代表认证成功"),
            connection_test_layer("permission", "权限", "not-tested", "权限范围需由实际连接器或 Harness 评估"),
        ],
    }
}

#[derive(Clone, Debug)]
struct McpProbeOutcome {
    protocol_version: String,
    tool_count: usize,
}

fn http_api_connection_test_reply(
    profile_id: String,
    endpoint: String,
    has_credential: bool,
    network: Result<std::time::Duration, String>,
    health: Option<Result<u16, String>>,
) -> HarnessConnectionTestReply {
    let (network_state, network_message) = match network {
        Ok(elapsed) => ("passed", format!("已真实连通 {endpoint}（TCP {}ms）", elapsed.as_millis())),
        Err(reason) => ("failed", format!("无法连通 {endpoint}：{reason}")),
    };
    let (protocol_state, protocol_message, summary) = match health {
        Some(Ok(status)) if (200..300).contains(&status) => ("passed", format!("只读健康检查返回 HTTP {status}"), "HTTP 健康检查通过"),
        Some(Ok(status)) => ("failed", format!("只读健康检查返回 HTTP {status}"), "HTTP 健康检查失败"),
        Some(Err(reason)) => ("failed", format!("只读健康检查失败：{reason}"), "HTTP 健康检查失败"),
        None if network_state == "passed" => ("not-tested", "未配置健康检查路径".to_owned(), "网络可达，未配置健康检查"),
        None => ("not-tested", "网络不可达，未执行 HTTP 检查".to_owned(), "网络不可达"),
    };
    HarnessConnectionTestReply {
        profile_id,
        tested: true,
        test_kind: "http-health".to_owned(),
        message: protocol_message.to_owned(),
        summary: summary.to_owned(),
        layers: vec![
            connection_test_layer("configuration", "配置", "passed", "HTTP API Profile 与地址格式有效"),
            connection_test_layer("network", "网络", network_state, network_message),
            connection_test_layer("protocol", "协议", protocol_state, protocol_message),
            connection_test_layer("authentication", "认证", if has_credential { "not-tested" } else { "not-configured" }, if has_credential { "健康检查未注入凭证，因此不能证明认证成功" } else { "未绑定安全凭证" }),
            connection_test_layer("permission", "权限", "not-tested", "HTTP 健康检查不代表业务数据权限"),
        ],
    }
}

fn provider_connection_test_reply(
    profile_id: String,
    provider_label: &str,
    endpoint: &str,
    has_credential: bool,
    network: Result<std::time::Duration, String>,
) -> HarnessConnectionTestReply {
    let (network_state, network_message, summary) = match network {
        Ok(elapsed) => (
            "passed",
            format!("已真实连通 {endpoint}（TCP {}ms）", elapsed.as_millis()),
            if has_credential {
                format!("{provider_label}网络可达，令牌已配置但未验证")
            } else {
                format!("{provider_label}网络可达，尚未配置令牌")
            },
        ),
        Err(reason) => (
            "failed",
            format!("无法连通 {endpoint}：{reason}"),
            format!("{provider_label}网络不可达"),
        ),
    };
    HarnessConnectionTestReply {
        profile_id,
        tested: true,
        test_kind: "provider-network-reachability".to_owned(),
        message: network_message.clone(),
        summary,
        layers: vec![
            connection_test_layer("configuration", "配置", "passed", format!("{provider_label}地址与连接 Profile 有效")),
            connection_test_layer("network", "网络", network_state, network_message),
            connection_test_layer("protocol", "协议", "not-tested", format!("未调用{provider_label}业务 API")),
            connection_test_layer(
                "authentication",
                "认证",
                if has_credential { "not-tested" } else { "not-configured" },
                if has_credential { "令牌已安全保存；网络探测不验证令牌有效性" } else { "尚未配置个人令牌" },
            ),
            connection_test_layer("permission", "权限", "not-tested", "未发起业务 API 请求，权限范围未验证"),
        ],
    }
}

fn mcp_connection_test_reply(
    profile_id: String,
    transport: String,
    has_credential: bool,
    network: Result<std::time::Duration, String>,
    handshake: Result<McpProbeOutcome, String>,
) -> HarnessConnectionTestReply {
    let (network_state, network_message) = match network {
        Ok(elapsed) => ("passed", format!("{transport} 传输已建立（{}ms）", elapsed.as_millis())),
        Err(reason) => ("failed", format!("{transport} 传输不可用：{reason}")),
    };
    let (protocol_state, protocol_message, permission_state, permission_message, summary, test_kind) = match handshake {
        Ok(outcome) => (
            "passed",
            format!("initialize {} 成功，tools/list 返回 {} 个工具", outcome.protocol_version, outcome.tool_count),
            if outcome.tool_count == 0 { "passed" } else { "approval-required" },
            if outcome.tool_count == 0 { "服务未声明工具".to_owned() } else { format!("发现 {} 个工具；实际调用仍由 Harness 权限审批", outcome.tool_count) },
            format!("MCP 握手成功，发现 {} 个工具", outcome.tool_count),
            "mcp-handshake",
        ),
        Err(reason) => ("failed", reason, "not-tested", "握手失败，无法评估工具权限".to_owned(), "MCP 握手失败".to_owned(), "mcp-protocol-error"),
    };
    HarnessConnectionTestReply {
        profile_id,
        tested: true,
        test_kind: test_kind.to_owned(),
        message: protocol_message.clone(),
        summary,
        layers: vec![
            connection_test_layer("configuration", "配置", "passed", "MCP Profile、传输和安全字段格式有效"),
            connection_test_layer("network", "网络", network_state, network_message),
            connection_test_layer("protocol", "协议", protocol_state, protocol_message),
            connection_test_layer("authentication", "认证", if has_credential { "not-tested" } else { "not-configured" }, if has_credential { "本次通用探测未注入凭证，不能证明认证成功" } else { "未绑定安全凭证" }),
            connection_test_layer("permission", "权限", permission_state, permission_message),
        ],
    }
}

async fn bounded_endpoint_probe(endpoint: &str) -> Result<std::time::Duration, String> {
    tokio::time::timeout(std::time::Duration::from_secs(6), probe_endpoint_reachability(endpoint))
        .await
        .unwrap_or(Err("探测超时".to_owned()))
}

async fn bounded_http_health_probe(endpoint: &str, health_path: &str) -> Result<u16, String> {
    let mut url = url::Url::parse(endpoint).map_err(|_| "地址格式无效".to_owned())?;
    url.set_path(health_path);
    url.set_query(None);
    url.set_fragment(None);
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(std::time::Duration::from_secs(6))
        .build()
        .map_err(|_| "HTTP 探测客户端不可用".to_owned())?;
    client.get(url).send().await.map(|response| response.status().as_u16()).map_err(|error| error.to_string())
}

async fn probe_mcp_http(endpoint: &str) -> Result<McpProbeOutcome, String> {
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(std::time::Duration::from_secs(6))
        .build()
        .map_err(|_| "MCP HTTP 探测客户端不可用".to_owned())?;
    let (initialized, session_id) = mcp_http_request(
        &client,
        endpoint,
        serde_json::json!({"jsonrpc":"2.0","id":"dsh-test-init","method":"initialize","params":{"protocolVersion":"mcp/v1"}}),
        None,
    ).await?;
    let initialized = initialized.get("result").ok_or_else(|| "MCP initialize 响应缺少 result".to_owned())?;
    let protocol_version = initialized.get("protocolVersion").and_then(serde_json::Value::as_str).ok_or_else(|| "MCP initialize 未返回协议版本".to_owned())?;
    if protocol_version != "mcp/v1" {
        return Err(format!("MCP 协议版本不兼容：{protocol_version}"));
    }
    let (listed, _) = mcp_http_request(
        &client,
        endpoint,
        serde_json::json!({"jsonrpc":"2.0","id":"dsh-test-tools","method":"tools/list","params":{}}),
        session_id.as_deref(),
    ).await?;
    let tools = listed.get("result").and_then(|value| value.get("tools")).and_then(serde_json::Value::as_array).ok_or_else(|| "MCP tools/list 响应无效".to_owned())?;
    if tools.len() > 256 { return Err("MCP 工具列表超过 256 项安全上限".to_owned()); }
    Ok(McpProbeOutcome { protocol_version: protocol_version.to_owned(), tool_count: tools.len() })
}

async fn mcp_http_request(
    client: &reqwest::Client,
    endpoint: &str,
    body: serde_json::Value,
    session_id: Option<&str>,
) -> Result<(serde_json::Value, Option<String>), String> {
    use futures_util::StreamExt;
    let mut request = client.post(endpoint)
        .header(reqwest::header::ACCEPT, "application/json, text/event-stream")
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .json(&body);
    if let Some(session_id) = session_id {
        request = request.header("Mcp-Session-Id", session_id);
    }
    let response = request.send().await.map_err(|error| error.to_string())?;
    let status = response.status();
    let returned_session = response.headers().get("Mcp-Session-Id").and_then(|value| value.to_str().ok()).map(str::to_owned);
    if !status.is_success() { return Err(format!("MCP HTTP 返回 {status}")); }
    let mut bytes = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|error| error.to_string())?;
        if bytes.len() + chunk.len() > 32 * 1024 { return Err("MCP 响应超过 32KB 安全上限".to_owned()); }
        bytes.extend_from_slice(&chunk);
    }
    let text = std::str::from_utf8(&bytes).map_err(|_| "MCP 响应不是 UTF-8".to_owned())?;
    let payload = text.lines().find_map(|line| line.strip_prefix("data:").map(str::trim)).unwrap_or(text.trim());
    let value = serde_json::from_str(payload).map_err(|_| "MCP 响应不是有效 JSON".to_owned())?;
    Ok((value, returned_session))
}

async fn probe_mcp_stdio(profile: &HarnessConnectionProfileReply) -> Result<(std::time::Duration, McpProbeOutcome), String> {
    use std::process::Stdio;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    if profile.working_directory_policy == "workspace" {
        return Err("工作区策略需要在具体任务上下文中测试；请改为继承宿主或在任务内运行".to_owned());
    }
    let started = std::time::Instant::now();
    let mut command = tokio::process::Command::new(&profile.command);
    command.args(&profile.args).env_clear().stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
    for key in &profile.environment_keys {
        let value = std::env::var(key).map_err(|_| format!("环境变量 {key} 当前未配置"))?;
        command.env(key, value);
    }
    let mut child = command.spawn().map_err(|error| format!("MCP stdio 进程无法启动：{error}"))?;
    let elapsed = started.elapsed();
    let mut stdin = child.stdin.take().ok_or_else(|| "MCP stdio 输入不可用".to_owned())?;
    let stdout = child.stdout.take().ok_or_else(|| "MCP stdio 输出不可用".to_owned())?;
    let mut lines = BufReader::new(stdout).lines();
    stdin.write_all(b"{\"jsonrpc\":\"2.0\",\"id\":\"dsh-test-init\",\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"mcp/v1\"}}\n").await.map_err(|error| error.to_string())?;
    stdin.flush().await.map_err(|error| error.to_string())?;
    let initialized = read_mcp_stdio_response(&mut lines, "dsh-test-init").await?;
    let result = initialized.get("result").ok_or_else(|| "MCP initialize 响应缺少 result".to_owned())?;
    let protocol_version = result.get("protocolVersion").and_then(serde_json::Value::as_str).ok_or_else(|| "MCP initialize 未返回协议版本".to_owned())?;
    if protocol_version != "mcp/v1" { return Err(format!("MCP 协议版本不兼容：{protocol_version}")); }
    stdin.write_all(b"{\"jsonrpc\":\"2.0\",\"id\":\"dsh-test-tools\",\"method\":\"tools/list\",\"params\":{}}\n").await.map_err(|error| error.to_string())?;
    stdin.flush().await.map_err(|error| error.to_string())?;
    let listed = read_mcp_stdio_response(&mut lines, "dsh-test-tools").await?;
    let tools = listed.get("result").and_then(|value| value.get("tools")).and_then(serde_json::Value::as_array).ok_or_else(|| "MCP tools/list 响应无效".to_owned())?;
    if tools.len() > 256 { return Err("MCP 工具列表超过 256 项安全上限".to_owned()); }
    let _ = child.kill().await;
    Ok((elapsed, McpProbeOutcome { protocol_version: protocol_version.to_owned(), tool_count: tools.len() }))
}

async fn read_mcp_stdio_response(
    lines: &mut tokio::io::Lines<tokio::io::BufReader<tokio::process::ChildStdout>>,
    expected_id: &str,
) -> Result<serde_json::Value, String> {
    let line = lines.next_line().await.map_err(|error| error.to_string())?.ok_or_else(|| "MCP stdio 服务未返回响应".to_owned())?;
    if line.len() > 32 * 1024 { return Err("MCP stdio 响应超过 32KB 安全上限".to_owned()); }
    let value: serde_json::Value = serde_json::from_str(&line).map_err(|_| "MCP stdio 响应不是有效 JSON".to_owned())?;
    if value.get("id").and_then(serde_json::Value::as_str) != Some(expected_id) { return Err("MCP stdio 响应 ID 不匹配".to_owned()); }
    if value.get("error").is_some() { return Err("MCP stdio 服务返回协议错误".to_owned()); }
    Ok(value)
}

fn persist_generalized_connection_test(
    foundation: &DesktopFoundation,
    profile_id: &str,
    reply: &HarnessConnectionTestReply,
) -> Result<(), String> {
    let store = foundation.agent_store.as_ref().ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let mut writer = store.writer().map_err(|error| error.to_string())?;
    ensure_harness_connection_tables(writer.connection())?;
    let json = serde_json::to_string(reply).map_err(|_| "连接测试结果序列化失败".to_owned())?;
    writer.connection_mut().execute(
        "UPDATE harness_connection_profiles_v2 SET last_test_json = ?1, updated_at = ?2 WHERE profile_id = ?3",
        rusqlite::params![json, Utc::now().to_rfc3339(), profile_id],
    ).map_err(|_| "连接测试结果保存失败".to_owned())?;
    Ok(())
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

fn archive_harness_answers(root: &Path, answers: &str) -> Result<PathBuf, String> {
    let root = root
        .canonicalize()
        .map_err(|_| "Harness 任务包目录无效".to_owned())?;
    let answers_dir = root.join("analysis");
    if answers_dir.exists() && answers_dir.is_symlink() {
        return Err("任务包答复目录不能是符号链接".to_owned());
    }
    fs::create_dir_all(&answers_dir).map_err(|_| "任务包目录不可写".to_owned())?;
    let answers_dir = answers_dir
        .canonicalize()
        .map_err(|_| "任务包目录不可写".to_owned())?;
    if !answers_dir.starts_with(&root) {
        return Err("任务包答复目录越界".to_owned());
    }

    let versions_dir = answers_dir.join("business_answers");
    if versions_dir.exists() && versions_dir.is_symlink() {
        return Err("任务包答复历史目录不能是符号链接".to_owned());
    }
    fs::create_dir_all(&versions_dir).map_err(|_| "任务包答复历史目录不可写".to_owned())?;
    let versions_dir = versions_dir
        .canonicalize()
        .map_err(|_| "任务包答复历史目录不可写".to_owned())?;
    if !versions_dir.starts_with(&root) {
        return Err("任务包答复历史目录越界".to_owned());
    }

    let recorded_at = Utc::now();
    let entry = format!(
        "## 答复记录\n\n- 记录时间：{}\n- 说明：以下为用户对理解门禁所提业务问题的答复，是最高优先级的业务口径。\n\n{}\n",
        recorded_at.to_rfc3339(),
        answers
    );
    let path = answers_dir.join("business_answers.md");
    let existing_length = if path.exists() {
        let metadata = fs::symlink_metadata(&path)
            .map_err(|_| "业务答复历史不可读取".to_owned())?;
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return Err("业务答复历史文件无效".to_owned());
        }
        metadata.len()
    } else {
        0
    };
    let new_file = !path.exists();
    let framing_length = if new_file {
        "# 业务答复（用户已确认）\n\n".len()
    } else {
        "\n---\n\n".len()
    };
    if existing_length
        .saturating_add(framing_length as u64)
        .saturating_add(entry.len() as u64)
        > 512 * 1024
    {
        return Err("业务答复历史文件无效".to_owned());
    }

    let version_path = versions_dir.join(format!(
        "{}-{}.md",
        recorded_at.format("%Y%m%dT%H%M%S%.3fZ"),
        Uuid::new_v4()
    ));
    let mut version = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&version_path)
        .map_err(|_| "业务答复版本写入失败".to_owned())?;
    version
        .write_all(entry.as_bytes())
        .map_err(|_| "业务答复版本写入失败".to_owned())?;
    version
        .sync_data()
        .map_err(|_| "业务答复版本写入失败".to_owned())?;

    let mut summary = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|_| "业务答复写入失败".to_owned())?;
    if new_file {
        summary
            .write_all("# 业务答复（用户已确认）\n\n".as_bytes())
            .map_err(|_| "业务答复写入失败".to_owned())?;
    } else {
        summary
            .write_all(b"\n---\n\n")
            .map_err(|_| "业务答复写入失败".to_owned())?;
    }
    summary
        .write_all(entry.as_bytes())
        .map_err(|_| "业务答复写入失败".to_owned())?;
    summary
        .sync_data()
        .map_err(|_| "业务答复写入失败".to_owned())?;
    Ok(path)
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
    let path = archive_harness_answers(&root, &answers)?;
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

const MAX_SKILL_FILES: usize = 512;
const MAX_SKILL_BYTES: u64 = 10 * 1024 * 1024;

fn validate_skill_id(value: &str) -> Result<(), String> {
    let mut chars = value.chars();
    let valid = (2..=64).contains(&value.len())
        && chars.next().is_some_and(|character| character.is_ascii_lowercase() || character.is_ascii_digit())
        && chars.all(|character| character.is_ascii_lowercase() || character.is_ascii_digit() || matches!(character, '.' | '_' | '-'));
    valid.then_some(()).ok_or_else(|| "技能 ID 只能使用 2-64 位小写字母、数字、点、下划线或连字符".to_owned())
}

fn render_skill_markdown(skill_id: &str, display_name: &str, description: &str, instructions: &str) -> Result<String, String> {
    validate_skill_id(skill_id)?;
    let display_name = display_name.trim();
    let description = description.trim();
    let instructions = instructions.trim();
    if display_name.is_empty() || display_name.len() > 120 || display_name.contains(['\0', '\r', '\n']) {
        return Err("技能名称无效".to_owned());
    }
    if description.len() > 500 || description.contains('\0') {
        return Err("技能描述无效".to_owned());
    }
    if instructions.is_empty() || instructions.len() > 16 * 1024 || instructions.contains('\0') {
        return Err("技能说明无效（需 1-16384 字符）".to_owned());
    }
    let yaml_description = if description.is_empty() { display_name } else { description };
    let yaml_description = yaml_description.replace('\\', "\\\\").replace('"', "\\\"").replace(['\r', '\n'], " ");
    Ok(format!("---\nname: {skill_id}\ndescription: \"{yaml_description}\"\n---\n\n# {display_name}\n\n{instructions}\n"))
}

fn skill_display_name(markdown: &str, fallback: &str) -> String {
    markdown.lines().find_map(|line| line.strip_prefix("# ").map(str::trim))
        .filter(|value| !value.is_empty() && value.len() <= 120 && !value.contains('\0'))
        .unwrap_or(fallback)
        .to_owned()
}

fn inspect_skill_tree(source: &Path, files: &mut usize, bytes: &mut u64) -> Result<(), String> {
    for entry in fs::read_dir(source).map_err(|_| "技能目录不可读取".to_owned())? {
        let entry = entry.map_err(|_| "技能目录不可读取".to_owned())?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(|_| "技能文件不可读取".to_owned())?;
        if metadata.file_type().is_symlink() {
            return Err("技能目录不能包含符号链接".to_owned());
        }
        if metadata.is_dir() {
            inspect_skill_tree(&entry.path(), files, bytes)?;
        } else if metadata.is_file() {
            *files += 1;
            *bytes = bytes.saturating_add(metadata.len());
            if *files > MAX_SKILL_FILES || *bytes > MAX_SKILL_BYTES {
                return Err("技能目录超出 512 个文件或 10MB 的导入限制".to_owned());
            }
        } else {
            return Err("技能目录包含不支持的文件类型".to_owned());
        }
    }
    Ok(())
}

fn copy_skill_tree(source: &Path, target: &Path) -> Result<(), String> {
    fs::create_dir(target).map_err(|_| "技能暂存目录创建失败".to_owned())?;
    for entry in fs::read_dir(source).map_err(|_| "技能目录不可读取".to_owned())? {
        let entry = entry.map_err(|_| "技能目录不可读取".to_owned())?;
        let source_path = entry.path();
        let target_path = target.join(entry.file_name());
        let metadata = fs::symlink_metadata(&source_path).map_err(|_| "技能文件不可读取".to_owned())?;
        if metadata.is_dir() {
            copy_skill_tree(&source_path, &target_path)?;
        } else if metadata.is_file() {
            fs::copy(&source_path, &target_path).map_err(|_| "技能文件复制失败".to_owned())?;
        } else {
            return Err("技能目录包含不支持的文件类型".to_owned());
        }
    }
    Ok(())
}

fn register_local_skill(foundation: &DesktopFoundation, skill_id: &str, display_name: &str, integrity: &str) -> Result<AgentExtensionReply, String> {
    let extension_id = format!("skill.{skill_id}");
    let now = Utc::now().to_rfc3339();
    let store = foundation.agent_store.as_ref().ok_or_else(|| "Agent 数据服务当前不可用".to_owned())?;
    let mut writer = store.writer().map_err(|error| error.to_string())?;
    let transaction = writer.connection_mut().transaction().map_err(|_| "技能登记失败".to_owned())?;
    transaction.execute(
        "INSERT INTO extensions (extension_id, extension_kind, display_name, source_kind, status, created_at, updated_at)
         VALUES (?1, 'skill', ?2, 'local', 'enabled', ?3, ?3)",
        params![extension_id, display_name, now],
    ).map_err(|_| "技能登记失败，技能 ID 可能已经存在".to_owned())?;
    transaction.execute(
        "INSERT INTO extension_versions (extension_id, version, integrity_sha256, manifest_ref_id, installed_at)
         VALUES (?1, 'local', ?2, NULL, ?3)",
        params![extension_id, integrity, now],
    ).map_err(|_| "技能版本登记失败".to_owned())?;
    transaction.commit().map_err(|_| "技能登记失败".to_owned())?;
    Ok(AgentExtensionReply { extension_id, extension_kind: "skill".to_owned(), display_name: display_name.to_owned(), source_kind: "local".to_owned(), status: "enabled".to_owned(), updated_at: now })
}

#[tauri::command]
pub async fn agent_skill_create(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    generation_id: String,
    session_id: String,
    skill_id: String,
    display_name: String,
    description: String,
    instructions: String,
) -> Result<AgentExtensionReply, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let markdown = render_skill_markdown(&skill_id, &display_name, &description, &instructions)?;
    let skills_root = codex_home_for(&foundation).ok_or_else(|| "当前没有活动 Profile".to_owned())?.join("skills");
    fs::create_dir_all(&skills_root).map_err(|_| "技能目录不可写".to_owned())?;
    let target = skills_root.join(&skill_id);
    if target.exists() { return Err("技能 ID 已存在，不会覆盖原技能".to_owned()); }
    let staging = skills_root.join(format!(".create-{}", Uuid::new_v4()));
    fs::create_dir(&staging).map_err(|_| "技能暂存目录创建失败".to_owned())?;
    let mut installed = false;
    let result = (|| {
        fs::write(staging.join("SKILL.md"), markdown.as_bytes()).map_err(|_| "技能文件写入失败".to_owned())?;
        fs::rename(&staging, &target).map_err(|_| "技能安装失败".to_owned())?;
        installed = true;
        let integrity = crate::extensions::manifest::sha256_hex(markdown.as_bytes());
        register_local_skill(&foundation, &skill_id, display_name.trim(), &integrity)
    })();
    if result.is_err() {
        if staging.exists() { let _ = fs::remove_dir_all(&staging); }
        if installed && target.exists() { let _ = fs::remove_dir_all(&target); }
    }
    result
}

#[tauri::command]
pub async fn agent_skill_import(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    app: AppHandle,
    generation_id: String,
    session_id: String,
) -> Result<Option<AgentExtensionReply>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let handle = app.clone();
    let picked = tauri::async_runtime::spawn_blocking(move || {
        use tauri_plugin_dialog::DialogExt;
        handle.dialog().file().set_title("选择包含 SKILL.md 的技能目录").blocking_pick_folder()
    }).await.map_err(|_| "技能目录选择器无法打开".to_owned())?;
    let Some(picked) = picked else { return Ok(None); };
    let source = picked.into_path().map_err(|_| "技能目录路径无效".to_owned())?;
    if source.is_symlink() { return Err("请选择普通技能目录，不能导入符号链接".to_owned()); }
    let source = source.canonicalize().map_err(|_| "技能目录不可读取".to_owned())?;
    if !source.is_dir() { return Err("请选择普通技能目录".to_owned()); }
    let skill_id = source.file_name().and_then(|value| value.to_str()).ok_or_else(|| "技能目录名称无效".to_owned())?.to_ascii_lowercase();
    validate_skill_id(&skill_id)?;
    let skill_path = source.join("SKILL.md");
    let markdown = fs::read_to_string(&skill_path).map_err(|_| "所选目录缺少可读取的 SKILL.md".to_owned())?;
    if markdown.len() > 16 * 1024 { return Err("SKILL.md 超出 16KB 限制".to_owned()); }
    let mut file_count = 0;
    let mut byte_count = 0;
    inspect_skill_tree(&source, &mut file_count, &mut byte_count)?;
    let display_name = skill_display_name(&markdown, &skill_id);
    let skills_root = codex_home_for(&foundation).ok_or_else(|| "当前没有活动 Profile".to_owned())?.join("skills");
    fs::create_dir_all(&skills_root).map_err(|_| "技能目录不可写".to_owned())?;
    let skills_root = skills_root.canonicalize().map_err(|_| "技能目录不可读取".to_owned())?;
    if skills_root.starts_with(&source) { return Err("不能导入包含当前 Skills 目录的上级目录".to_owned()); }
    let target = skills_root.join(&skill_id);
    if target.exists() { return Err("技能 ID 已存在，不会覆盖原技能".to_owned()); }
    let staging = skills_root.join(format!(".import-{}", Uuid::new_v4()));
    let mut installed = false;
    let result = (|| {
        copy_skill_tree(&source, &staging)?;
        fs::rename(&staging, &target).map_err(|_| "技能导入失败".to_owned())?;
        installed = true;
        let integrity = crate::extensions::manifest::sha256_hex(markdown.as_bytes());
        register_local_skill(&foundation, &skill_id, &display_name, &integrity).map(Some)
    })();
    if result.is_err() {
        if staging.exists() { let _ = fs::remove_dir_all(&staging); }
        if installed && target.exists() { let _ = fs::remove_dir_all(&target); }
    }
    result
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

fn parse_mcp_target(value: &str) -> Result<crate::mcp_manager::model::McpTarget, String> {
    crate::mcp_manager::model::McpTarget::parse(value).ok_or_else(|| format!("未知 MCP 同步目标: {value}"))
}

#[tauri::command]
pub async fn mcp_manager_list(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::mcp_manager::service::McpManagerService>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<crate::mcp_manager::model::McpServerDef>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.list().map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn mcp_manager_upsert(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::mcp_manager::service::McpManagerService>>,
    generation_id: String,
    session_id: String,
    def: crate::mcp_manager::model::McpServerDef,
) -> Result<crate::mcp_manager::model::McpServerDef, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.upsert(def).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn mcp_manager_delete(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::mcp_manager::service::McpManagerService>>,
    generation_id: String,
    session_id: String,
    id: String,
) -> Result<(), String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.delete(&id).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn mcp_manager_sync(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::mcp_manager::service::McpManagerService>>,
    generation_id: String,
    session_id: String,
    target: String,
) -> Result<(), String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let target = parse_mcp_target(&target)?;
    service.sync_target(target).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn mcp_manager_import(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::mcp_manager::service::McpManagerService>>,
    generation_id: String,
    session_id: String,
    target: String,
) -> Result<Vec<crate::mcp_manager::model::McpServerDef>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let target = parse_mcp_target(&target)?;
    service.import_target(target).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn mcp_manager_status(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::mcp_manager::service::McpManagerService>>,
    generation_id: String,
    session_id: String,
) -> Result<Vec<crate::mcp_manager::model::McpTargetStatus>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    service.status().map_err(|error| error.to_string())
}

fn parse_skill_target(value: &str) -> Result<crate::skills_manager::model::SkillTarget, String> {
    crate::skills_manager::model::SkillTarget::parse(value)
        .ok_or_else(|| format!("未知 Skills 目标: {value}"))
}

#[tauri::command]
pub async fn skills_list(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::skills_manager::service::SkillsManagerService>>,
    generation_id: String,
    session_id: String,
    target: String,
) -> Result<Vec<crate::skills_manager::model::InstalledSkill>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let target = parse_skill_target(&target)?;
    service.list_target(target).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn skills_install_zip(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::skills_manager::service::SkillsManagerService>>,
    generation_id: String,
    session_id: String,
    zip_path: String,
    targets: Vec<String>,
) -> Result<Vec<crate::skills_manager::model::InstalledSkill>, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    if zip_path.is_empty() || zip_path.len() > 4096 {
        return Err("ZIP 路径无效(须为 1-4096 字符)".to_owned());
    }
    let parsed = targets
        .iter()
        .map(|value| parse_skill_target(value))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    service
        .install_from_zip(Path::new(&zip_path), &parsed)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn skills_uninstall(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::skills_manager::service::SkillsManagerService>>,
    generation_id: String,
    session_id: String,
    target: String,
    name: String,
) -> Result<(), String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let target = parse_skill_target(&target)?;
    service.uninstall(target, &name).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn skills_sync(
    coordinator: State<'_, Arc<DesktopCoordinator>>,
    service: State<'_, Arc<crate::skills_manager::service::SkillsManagerService>>,
    generation_id: String,
    session_id: String,
    src_target: String,
    dst_target: String,
    name: String,
) -> Result<crate::skills_manager::model::InstalledSkill, String> {
    coordinator.validate_generation(&generation_id).await.map_err(|error| error.to_string())?;
    validate_agent_identifier(&session_id, "Session ID")?;
    let src_target = parse_skill_target(&src_target)?;
    let dst_target = parse_skill_target(&dst_target)?;
    service.sync(src_target, dst_target, &name).map_err(|error| error.to_string())
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
            "agent_skill_create",
            "agent_skill_import",
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

    #[test]
    fn local_skill_markdown_is_bounded_and_uses_valid_frontmatter() {
        let markdown = super::render_skill_markdown("code-review", "代码审查", "只读审查", "先读取文件，再输出结论。").unwrap();
        assert!(markdown.starts_with("---\nname: code-review\ndescription: \"只读审查\"\n---"));
        assert!(markdown.contains("# 代码审查"));
        assert!(super::render_skill_markdown("../escape", "无效", "", "内容").is_err());
        assert!(super::render_skill_markdown("valid-skill", "有效", "", "x".repeat(16 * 1024 + 1).as_str()).is_err());
    }

    #[test]
    fn imported_skill_tree_rejects_symlinks_and_oversized_content() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("SKILL.md"), "# Demo").unwrap();
        let mut files = 0;
        let mut bytes = 0;
        super::inspect_skill_tree(root.path(), &mut files, &mut bytes).unwrap();
        assert_eq!(files, 1);
        assert_eq!(super::skill_display_name("---\nname: demo\n---\n# Demo 技能\n", "demo"), "Demo 技能");

        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(root.path().join("SKILL.md"), root.path().join("link.md")).unwrap();
            assert!(super::inspect_skill_tree(root.path(), &mut 0, &mut 0).is_err());
        }
    }

    #[test]
    fn generalized_connection_store_is_additive_and_preserves_legacy_rows() {
        let connection = rusqlite::Connection::open_in_memory().unwrap();
        super::ensure_harness_connection_table(&connection).unwrap();
        connection.execute(
            "INSERT INTO harness_connection_profiles
             (profile_id, kind, provider_id, display_name, endpoint, read_only, enabled, created_at, updated_at)
             VALUES ('legacy-yunxiao', 'mcp', 'yunxiao', '云效', 'https://devops.aliyun.com', 1, 1, 'now', 'now')",
            [],
        ).unwrap();

        super::ensure_harness_connection_tables(&connection).unwrap();

        let legacy_count: i64 = connection.query_row(
            "SELECT COUNT(*) FROM harness_connection_profiles WHERE profile_id = 'legacy-yunxiao'",
            [],
            |row| row.get(0),
        ).unwrap();
        let generalized_count: i64 = connection.query_row(
            "SELECT COUNT(*) FROM harness_connection_profiles_v2",
            [],
            |row| row.get(0),
        ).unwrap();
        assert_eq!(legacy_count, 1, "legacy rows must never be copied, rewritten, or deleted automatically");
        assert_eq!(generalized_count, 0, "the additive store starts empty");
    }

    #[test]
    fn network_probe_result_does_not_claim_protocol_authentication_or_permission_success() {
        let reply = super::legacy_connection_test_reply(
            "legacy-profile".to_owned(),
            Ok(std::time::Duration::from_millis(12)),
            "https://example.test",
        );
        assert_eq!(reply.summary, "网络可达，其他层未验证");
        assert_eq!(reply.layers[0].state, "passed");
        assert_eq!(reply.layers[1].state, "passed");
        assert_eq!(reply.layers[2].state, "not-tested");
        assert_eq!(reply.layers[3].state, "not-tested");
        assert_eq!(reply.layers[4].state, "not-tested");
    }

    #[test]
    fn http_health_probe_reports_only_the_layers_it_actually_validated() {
        let reply = super::http_api_connection_test_reply(
            "http-profile".to_owned(),
            "https://api.example.test".to_owned(),
            true,
            Ok(std::time::Duration::from_millis(8)),
            Some(Ok(204)),
        );
        assert_eq!(reply.summary, "HTTP 健康检查通过");
        assert_eq!(reply.layers[0].state, "passed");
        assert_eq!(reply.layers[1].state, "passed");
        assert_eq!(reply.layers[2].state, "passed");
        assert_eq!(reply.layers[3].state, "not-tested", "a request without credential injection must not claim authentication success");
        assert_eq!(reply.layers[4].state, "not-tested");
    }

    #[test]
    fn provider_connection_probe_does_not_treat_yunxiao_or_gitlab_as_mcp_servers() {
        let reply = super::provider_connection_test_reply(
            "yunxiao-profile".to_owned(),
            "云效",
            "https://openapi-rdc.aliyuncs.com",
            true,
            Ok(std::time::Duration::from_millis(9)),
        );
        assert_eq!(reply.summary, "云效网络可达，令牌已配置但未验证");
        assert_eq!(reply.layers[0].state, "passed");
        assert_eq!(reply.layers[1].state, "passed");
        assert_eq!(reply.layers[2].state, "not-tested", "provider APIs are not MCP protocol endpoints");
        assert_eq!(reply.layers[3].state, "not-tested", "a network probe must not claim token validity");
        assert_eq!(reply.layers[4].state, "not-tested");
    }

    #[test]
    fn mcp_handshake_and_tool_discovery_are_reported_separately_from_approval() {
        let reply = super::mcp_connection_test_reply(
            "mcp-profile".to_owned(),
            "HTTP".to_owned(),
            false,
            Ok(std::time::Duration::from_millis(10)),
            Ok(super::McpProbeOutcome { protocol_version: "mcp/v1".to_owned(), tool_count: 3 }),
        );
        assert_eq!(reply.summary, "MCP 握手成功，发现 3 个工具");
        assert_eq!(reply.layers[2].state, "passed");
        assert_eq!(reply.layers[3].state, "not-configured");
        assert_eq!(reply.layers[4].state, "approval-required", "tool discovery does not bypass Harness permission review");
    }

    #[test]
    fn harness_business_answers_are_versioned_and_never_overwrite_prior_confirmation() {
        let root = tempfile::tempdir().unwrap();

        super::archive_harness_answers(root.path(), "第一次确认").unwrap();
        super::archive_harness_answers(root.path(), "第二次确认").unwrap();

        let summary = std::fs::read_to_string(root.path().join("analysis/business_answers.md")).unwrap();
        assert!(summary.contains("第一次确认"));
        assert!(summary.contains("第二次确认"));
        let versions = std::fs::read_dir(root.path().join("analysis/business_answers"))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        assert_eq!(versions.len(), 2);
    }

    #[test]
    fn invalid_business_answer_summary_does_not_leave_a_partial_version() {
        let root = tempfile::tempdir().unwrap();
        let analysis = root.path().join("analysis");
        std::fs::create_dir_all(&analysis).unwrap();
        std::fs::write(analysis.join("business_answers.md"), vec![b'x'; 512 * 1024 + 1]).unwrap();

        let error = super::archive_harness_answers(root.path(), "新的确认").unwrap_err();

        assert_eq!(error, "业务答复历史文件无效");
        let versions = analysis.join("business_answers");
        assert!(!versions.exists() || std::fs::read_dir(versions).unwrap().next().is_none());
    }
}
