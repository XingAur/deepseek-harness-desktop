use serde::{Deserialize, Serialize};

pub const MAX_PROMPT_BYTES: usize = 24 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PromptTarget {
    Claude,
    Codex,
    Dsh,
}

impl PromptTarget {
    pub const ALL: [PromptTarget; 3] = [PromptTarget::Claude, PromptTarget::Codex, PromptTarget::Dsh];
    pub fn as_str(self) -> &'static str {
        match self {
            PromptTarget::Claude => "claude",
            PromptTarget::Codex => "codex",
            PromptTarget::Dsh => "dsh",
        }
    }

    pub fn parse(value: &str) -> Option<PromptTarget> {
        match value {
            "claude" => Some(PromptTarget::Claude),
            "codex" => Some(PromptTarget::Codex),
            "dsh" => Some(PromptTarget::Dsh),
            _ => None,
        }
    }
}

// PromptsError::TargetNotInstalled 使用 `{0}` 格式化,要求实现 Display
impl std::fmt::Display for PromptTarget {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PromptPreset {
    pub id: String,
    pub title: String,
    pub content: String,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PresetSummary {
    pub id: String,
    pub title: String,
    pub updated_at: i64,
    pub activated_targets: Vec<PromptTarget>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TargetStatus {
    pub target: PromptTarget,
    pub installed: bool,
    pub live_file_exists: bool,
    pub active_preset_id: Option<String>,
    pub live_content_sha256: Option<String>,
    pub matches_active_preset: bool,
    pub oversized: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConflictCandidate {
    pub target: PromptTarget,
    pub content: String,
    pub updated_at: i64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", rename_all_fields = "camelCase")]
pub enum SaveOutcome {
    Saved { preset: PromptPreset, projected: Vec<TargetStatus> },
    BackfillConflict { preset_id: String, candidates: Vec<ConflictCandidate> },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", rename_all_fields = "camelCase")]
pub enum ActivateOutcome {
    Ok { status: TargetStatus },
    BackfillConflict { preset_id: String, candidates: Vec<ConflictCandidate> },
}

/// 承载“正常完成 or 冲突待裁决”的结果:冲突是业务分支而非错误,不走错误通道。
/// 纯内部类型,不做序列化;commands 层手工映射冲突载荷(经 SaveOutcome::BackfillConflict)。
#[derive(Clone, Debug)]
pub enum Flow<T> {
    Done(T),
    Conflict { preset_id: String, candidates: Vec<ConflictCandidate> },
}

#[derive(Debug, thiserror::Error)]
pub enum PromptsError {
    #[error("prompts_store_error: {0}")]
    Store(String),
    #[error("prompts_target_not_installed: {0}")]
    TargetNotInstalled(PromptTarget),
    #[error("prompts_preset_active: {0}")]
    PresetActive(String),
    #[error("prompts_too_large")]
    TooLarge,
    #[error("prompts_invalid_input: {0}")]
    InvalidInput(String),
    #[error("prompts_io_error: {0}")]
    Io(String),
}

pub type Result<T> = std::result::Result<T, PromptsError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn target_serializes_to_lowercase_and_back() {
        assert_eq!(serde_json::to_string(&PromptTarget::Claude).unwrap(), "\"claude\"");
        assert_eq!(serde_json::to_string(&PromptTarget::Dsh).unwrap(), "\"dsh\"");
        let parsed: PromptTarget = serde_json::from_str("\"codex\"").unwrap();
        assert_eq!(parsed, PromptTarget::Codex);
    }

    #[test]
    fn status_uses_camel_case_keys() {
        let status = TargetStatus {
            target: PromptTarget::Claude,
            installed: true,
            live_file_exists: false,
            active_preset_id: None,
            live_content_sha256: None,
            matches_active_preset: false,
            oversized: false,
        };
        let json = serde_json::to_value(&status).unwrap();
        assert!(json.get("liveFileExists").is_some());
        assert!(json.get("activePresetId").is_some());
    }

    #[test]
    fn outcome_tags_are_kebab_case() {
        let saved = serde_json::to_value(SaveOutcome::Saved {
            preset: PromptPreset { id: "p1".into(), title: "t".into(), content: String::new(), created_at: 0, updated_at: 0 },
            projected: vec![],
        })
        .unwrap();
        assert_eq!(saved["kind"], "saved");
        let conflict = serde_json::to_value(ActivateOutcome::BackfillConflict {
            preset_id: "p1".into(),
            candidates: vec![],
        })
        .unwrap();
        assert_eq!(conflict["kind"], "backfill-conflict");
        assert_eq!(conflict["presetId"], "p1");
        assert!(conflict.get("preset_id").is_none(), "字段必须是 camelCase");
    }

    #[test]
    fn max_prompt_bytes_is_24kib() {
        assert_eq!(MAX_PROMPT_BYTES, 24576);
    }
}
