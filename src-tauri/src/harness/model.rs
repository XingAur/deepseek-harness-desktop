use std::path::PathBuf;

use serde::{Deserialize, Serialize};

pub const HARNESS_EVENT: &str = "harness-event";
pub const HARNESS_HOST_SCHEMA: &str = "harness-host-session.v1";

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HarnessStatus {
    pub state: String,
    pub pid: Option<u32>,
    pub request_id: Option<String>,
    pub error_code: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HarnessTaskStart {
    pub task_contract_path: PathBuf,
    pub understanding_path: PathBuf,
    pub worktree_root: PathBuf,
    pub knowledge_home: PathBuf,
    pub authorization_id: String,
    pub agent_backend: Option<String>,
}

impl HarnessTaskStart {
    pub fn validate(&self) -> Result<(), HarnessError> {
        if self.authorization_id.is_empty()
            || self.authorization_id.len() > 256
            || !self
                .authorization_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
        {
            return Err(HarnessError::InvalidRequest);
        }
        for path in [
            &self.task_contract_path,
            &self.understanding_path,
            &self.worktree_root,
            &self.knowledge_home,
        ] {
            if !path.is_absolute() || path.to_string_lossy().contains('\0') {
                return Err(HarnessError::InvalidRequest);
            }
        }
        if let Some(agent) = self.agent_backend.as_deref()
            && (agent.is_empty()
                || agent.len() > 64
                || !agent.bytes().all(|byte| {
                    byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"._-".contains(&byte)
                }))
        {
            return Err(HarnessError::InvalidRequest);
        }
        Ok(())
    }

    pub fn payload(&self) -> serde_json::Value {
        serde_json::json!({
            "schema_version": "harness-external-task.v1",
            "task_contract_path": self.task_contract_path.to_string_lossy(),
            "understanding_path": self.understanding_path.to_string_lossy(),
            "worktree_root": self.worktree_root.to_string_lossy(),
            "knowledge_home": self.knowledge_home.to_string_lossy(),
            "authorization_id": &self.authorization_id,
            "agent_backend": &self.agent_backend,
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HarnessError {
    InvalidRequest,
    SidecarUnavailable,
    SidecarPathNotAllowed,
    AlreadyRunning,
    NotRunning,
    Process(String),
}

impl std::fmt::Display for HarnessError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::InvalidRequest => "Harness 任务参数无效",
            Self::SidecarUnavailable => "Harness Host 当前不可用",
            Self::SidecarPathNotAllowed => "Harness Host 路径未通过安全校验",
            Self::AlreadyRunning => "已有 Harness 任务正在运行",
            Self::NotRunning => "当前没有运行中的 Harness 任务",
            Self::Process(message) => message,
        })
    }
}

impl std::error::Error for HarnessError {}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HarnessHostMessage {
    pub schema_version: String,
    #[serde(rename = "type")]
    pub message_type: String,
    pub request_id: String,
    pub payload: serde_json::Value,
}

impl HarnessHostMessage {
    pub fn validate(&self) -> Result<(), HarnessError> {
        if self.schema_version != HARNESS_HOST_SCHEMA
            || self.request_id.is_empty()
            || self.request_id.len() > 128
            || self.message_type.is_empty()
            || !matches!(
                self.message_type.as_str(),
                "session.event" | "task.result" | "agent.request" | "agent.result"
            )
            || !self.payload.is_object()
        {
            return Err(HarnessError::InvalidRequest);
        }
        Ok(())
    }
}
