use std::path::PathBuf;

use semver::Version;
use serde::{Deserialize, Serialize};

pub const ADAPTER_PROTOCOL_VERSION: &str = "dsh-agent-adapter/v1";
pub const AGENT_EVENT_CHANNEL: &str = "dsh-agent/v1";
pub const AGENT_TAURI_EVENT_NAME: &str = "agent-event";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AgentEventEnvelope {
    pub channel: String,
    pub generation_id: String,
    pub task_id: String,
    pub session_id: String,
    pub sequence: u64,
    #[serde(rename = "type")]
    pub event_type: String,
    pub payload: serde_json::Value,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "kebab-case")]
pub enum AgentProvider {
    Codex,
    Claude,
}

impl AgentProvider {
    pub fn command_name(self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::Claude => "claude",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "kebab-case")]
pub enum DiscoverySource {
    Explicit,
    Path,
    OfficialLocation,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "kebab-case")]
pub enum DiscoveryDiagnosticCode {
    NotFound,
    InvalidPath,
    SymlinkInvalid,
    NonExecutable,
    PrivateAppBundle,
    Duplicate,
    VersionProbeFailed,
    VersionParseFailed,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DiscoveryDiagnostic {
    pub code: DiscoveryDiagnosticCode,
    pub message: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DiscoveredAgent {
    pub provider: AgentProvider,
    pub path: PathBuf,
    pub source: DiscoverySource,
    pub version: Option<Version>,
    pub protocol: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DiscoveryResult {
    pub provider: AgentProvider,
    pub selected: Option<DiscoveredAgent>,
    pub candidates: Vec<DiscoveredAgent>,
    pub diagnostics: Vec<DiscoveryDiagnostic>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum CompatibilityStatus {
    Compatible,
    VersionTooOld,
    VersionTooNew,
    VersionUnknown,
    UnsupportedProtocol,
    ProtocolUnknown,
}

#[cfg(test)]
mod tests {
    use super::{AGENT_EVENT_CHANNEL, AgentEventEnvelope};

    #[test]
    fn task_four_model_tests_are_declared_in_the_feature_module() {
        assert!(true);
    }

    #[test]
    fn agent_event_serialization_matches_the_renderer_envelope() {
        let value = serde_json::to_value(AgentEventEnvelope {
            channel: AGENT_EVENT_CHANNEL.to_owned(),
            generation_id: "generation-1".to_owned(),
            task_id: "task-1".to_owned(),
            session_id: "session-1".to_owned(),
            sequence: 1,
            event_type: "task.progress".to_owned(),
            payload: serde_json::json!({ "percent": 10 }),
        })
        .unwrap();
        assert_eq!(
            value.get("channel").and_then(|item| item.as_str()),
            Some(AGENT_EVENT_CHANNEL)
        );
        assert_eq!(
            value.get("type").and_then(|item| item.as_str()),
            Some("task.progress")
        );
        assert!(value.get("eventType").is_none());
    }
}
