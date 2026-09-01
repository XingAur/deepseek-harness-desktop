use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

/// MCP 同步目标(MVP 仅 Claude 与 Codex;DSH 官方设置已自带 MCP 管理,不重复)。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum McpTarget {
    Claude,
    Codex,
}

impl McpTarget {
    pub const ALL: [McpTarget; 2] = [McpTarget::Claude, McpTarget::Codex];

    pub fn as_str(self) -> &'static str {
        match self {
            McpTarget::Claude => "claude",
            McpTarget::Codex => "codex",
        }
    }

    pub fn parse(value: &str) -> Option<McpTarget> {
        match value {
            "claude" => Some(McpTarget::Claude),
            "codex" => Some(McpTarget::Codex),
            _ => None,
        }
    }
}

// McpManagerError::TargetNotInstalled 使用 `{0}` 格式化,要求实现 Display。
impl std::fmt::Display for McpTarget {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// 一条 MCP 服务器定义:id 缺省(空串)表示新建;targets 是该服务器要同步到的目标集合。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct McpServerDef {
    #[serde(default)]
    pub id: String,
    pub name: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default)]
    pub targets: BTreeSet<String>,
}

/// 目标 CLI 安装状态(目录存在即视为已安装)。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct McpTargetStatus {
    pub target: McpTarget,
    pub installed: bool,
}

/// 已成功写入某个目标配置的受管投影。
///
/// `fingerprint` 是命令、参数和环境变量的规范化摘要。只有目标文件中的当前值仍与
/// 该摘要匹配，后续同步或删除才允许移除/替换该条目。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct McpProjection {
    pub server_id: String,
    pub target: McpTarget,
    pub name: String,
    pub fingerprint: String,
}

#[derive(Debug, thiserror::Error)]
pub enum McpManagerError {
    #[error("mcp_store_error: {0}")]
    Store(String),
    #[error("mcp_target_not_installed: {0}")]
    TargetNotInstalled(McpTarget),
    #[error("mcp_invalid_input: {0}")]
    InvalidInput(String),
    #[error("mcp_io_error: {0}")]
    Io(String),
    #[error("mcp_external_change: {target}/{name}")]
    ExternalChange { target: McpTarget, name: String },
}

pub type Result<T> = std::result::Result<T, McpManagerError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn target_serializes_to_lowercase_and_back() {
        assert_eq!(serde_json::to_string(&McpTarget::Claude).unwrap(), "\"claude\"");
        assert_eq!(serde_json::to_string(&McpTarget::Codex).unwrap(), "\"codex\"");
        let parsed: McpTarget = serde_json::from_str("\"codex\"").unwrap();
        assert_eq!(parsed, McpTarget::Codex);
        assert!(McpTarget::parse("dsh").is_none(), "MVP 不含 DSH 目标");
    }

    #[test]
    fn server_def_uses_camel_case_keys_and_defaults() {
        let def: McpServerDef = serde_json::from_str(
            r#"{"name":"fetch","command":"npx","targets":["claude"]}"#,
        )
        .unwrap();
        assert!(def.id.is_empty(), "缺省 id 表示新建");
        assert!(def.args.is_empty());
        assert!(def.env.is_empty());
        assert_eq!(def.targets, BTreeSet::from(["claude".to_owned()]));
        let json = serde_json::to_value(&def).unwrap();
        for key in ["id", "name", "command", "args", "env", "targets"] {
            assert!(json.get(key).is_some(), "缺少键 {key}");
        }
    }
}
