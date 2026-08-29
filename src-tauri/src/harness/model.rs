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
    /// 云效只读归档任务的结果快照（任务包目录、生成状态、待补计数等）。
    /// 仅当 task.result 携带 package_dir 时存在；普通执行任务不设置。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub intake: Option<serde_json::Value>,
    /// 执行被理解门禁阻断时的具体原因/业务问题，供界面展示与答复。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub blockers: Option<Vec<String>>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HarnessTaskStart {
    pub task_contract_path: Option<PathBuf>,
    pub understanding_path: Option<PathBuf>,
    pub worktree_root: PathBuf,
    pub knowledge_home: PathBuf,
    pub authorization_id: String,
    pub agent_backend: Option<String>,
    pub archive_root: Option<PathBuf>,
    pub intake_source: Option<String>,
    pub intake_include_comments: Option<bool>,
    pub selected_model_id: Option<String>,
    pub yunxiao_profile_id: Option<String>,
    pub gitlab_profile_id: Option<String>,
    pub database_profile_id: Option<String>,
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
        for path in [&self.worktree_root, &self.knowledge_home] {
            if !path.is_absolute() || path.to_string_lossy().contains('\0') {
                return Err(HarnessError::InvalidRequest);
            }
        }
        if self.task_contract_path.is_some() != self.understanding_path.is_some()
            || (self.task_contract_path.is_none() && self.archive_root.is_none())
        {
            return Err(HarnessError::InvalidRequest);
        }
        for path in self.task_contract_path.iter().chain(self.understanding_path.iter()) {
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
        for path in self.archive_root.iter() {
            if !path.is_absolute() || path.to_string_lossy().contains('\0') {
                return Err(HarnessError::InvalidRequest);
            }
        }
        for value in [
            self.intake_source.as_deref(),
            self.selected_model_id.as_deref(),
            self.yunxiao_profile_id.as_deref(),
            self.gitlab_profile_id.as_deref(),
            self.database_profile_id.as_deref(),
        ]
        .into_iter()
        .flatten()
        {
            if value.is_empty() || value.len() > 128 || !value.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || b"._:-".contains(&byte)
            }) {
                return Err(HarnessError::InvalidRequest);
            }
        }
        if let Some(source) = self.intake_source.as_deref()
            && !is_yunxiao_source(source)
        {
            return Err(HarnessError::InvalidRequest);
        }
        if self.intake_source.is_some() && self.archive_root.is_none() {
            return Err(HarnessError::InvalidRequest);
        }
        Ok(())
    }

    pub fn payload(&self) -> serde_json::Value {
        let mut payload = serde_json::json!({
            "schema_version": "harness-external-task.v1",
            "worktree_root": self.worktree_root.to_string_lossy(),
            "knowledge_home": self.knowledge_home.to_string_lossy(),
            "authorization_id": &self.authorization_id,
        });
        let object = payload.as_object_mut().expect("Harness payload object");
        if let Some(path) = self.task_contract_path.as_ref() {
            object.insert("task_contract_path".to_owned(), serde_json::json!(path.to_string_lossy()));
        }
        if let Some(path) = self.understanding_path.as_ref() {
            object.insert("understanding_path".to_owned(), serde_json::json!(path.to_string_lossy()));
        }
        for (key, value) in [
            ("agent_backend", self.agent_backend.as_ref()),
            ("selected_model_id", self.selected_model_id.as_ref()),
            ("yunxiao_profile_id", self.yunxiao_profile_id.as_ref()),
            ("gitlab_profile_id", self.gitlab_profile_id.as_ref()),
            ("database_profile_id", self.database_profile_id.as_ref()),
        ] {
            if let Some(value) = value {
                object.insert(key.to_owned(), serde_json::json!(value));
            }
        }
        if let Some(path) = self.archive_root.as_ref() {
            object.insert("archive_root".to_owned(), serde_json::json!(path.to_string_lossy()));
        }
        if let Some(source) = self.intake_source.as_ref() {
            object.insert("intake_source".to_owned(), serde_json::json!(source));
        }
        if let Some(include_comments) = self.intake_include_comments {
            object.insert("intake_include_comments".to_owned(), serde_json::json!(include_comments));
        }
        payload
    }
}

fn is_yunxiao_source(value: &str) -> bool {
    if value.is_empty() || value.len() > 4096 || value.chars().any(|character| matches!(character, '?' | '#' | '\0')) {
        return false;
    }
    let id = value
        .split_once('-')
        .is_some_and(|(prefix, number)| {
            !prefix.is_empty()
                && prefix.len() <= 32
                && prefix.bytes().all(|byte| byte.is_ascii_alphanumeric())
                && !number.is_empty()
                && number.len() <= 20
                && number.bytes().all(|byte| byte.is_ascii_digit())
        });
    id || (value.starts_with("https://")
        && value[8..].chars().next().is_some_and(|character| !character.is_whitespace() && character != '/'))
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
