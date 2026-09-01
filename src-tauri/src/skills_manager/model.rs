use serde::{Deserialize, Serialize};

/// Skills 安装目标(MVP 仅 Claude 与 Codex;GitHub 仓库直装、自动更新与 DSH 目标后置)。
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SkillTarget {
    Claude,
    Codex,
}

impl SkillTarget {
    pub fn as_str(self) -> &'static str {
        match self {
            SkillTarget::Claude => "claude",
            SkillTarget::Codex => "codex",
        }
    }

    pub fn parse(value: &str) -> Option<SkillTarget> {
        match value {
            "claude" => Some(SkillTarget::Claude),
            "codex" => Some(SkillTarget::Codex),
            _ => None,
        }
    }
}

// SkillsError::TargetNotInstalled 使用 `{0}` 格式化,要求实现 Display。
impl std::fmt::Display for SkillTarget {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// 一个已安装的 skill:目标 skills 目录下含 SKILL.md 的子目录。
/// `skill_md_sha256` 用于跨目标比对两份安装是否一致。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstalledSkill {
    pub name: String,
    pub target: SkillTarget,
    pub path: String,
    pub skill_md_sha256: String,
}

#[derive(Debug, thiserror::Error)]
pub enum SkillsError {
    #[error("skills_target_not_installed: {0}")]
    TargetNotInstalled(SkillTarget),
    #[error("skills_invalid_input: {0}")]
    InvalidInput(String),
    #[error("skills_zip_error: {0}")]
    Zip(String),
    #[error("skills_io_error: {0}")]
    Io(String),
}

impl From<std::io::Error> for SkillsError {
    fn from(error: std::io::Error) -> Self {
        SkillsError::Io(error.to_string())
    }
}

pub type Result<T> = std::result::Result<T, SkillsError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn target_serializes_to_lowercase_and_back() {
        assert_eq!(serde_json::to_string(&SkillTarget::Claude).unwrap(), "\"claude\"");
        assert_eq!(serde_json::to_string(&SkillTarget::Codex).unwrap(), "\"codex\"");
        let parsed: SkillTarget = serde_json::from_str("\"claude\"").unwrap();
        assert_eq!(parsed, SkillTarget::Claude);
        assert!(SkillTarget::parse("dsh").is_none(), "MVP 不含 DSH 目标");
    }

    #[test]
    fn installed_skill_uses_camel_case_keys() {
        let skill = InstalledSkill {
            name: "demo".to_owned(),
            target: SkillTarget::Claude,
            path: "C:\\home\\.claude\\skills\\demo".to_owned(),
            skill_md_sha256: "ba7816bf".to_owned(),
        };
        let json = serde_json::to_value(&skill).unwrap();
        for key in ["name", "target", "path", "skillMdSha256"] {
            assert!(json.get(key).is_some(), "缺少键 {key}");
        }
    }
}
