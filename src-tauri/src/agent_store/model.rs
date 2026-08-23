use std::path::PathBuf;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct BackupMetadata {
    pub schema_version: i64,
    pub created_at: DateTime<Utc>,
    pub source_path: PathBuf,
    pub backup_path: PathBuf,
    pub metadata_path: PathBuf,
    pub sha256: String,
    pub byte_length: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RecoveryState {
    pub source_path: PathBuf,
    pub backup: Option<BackupMetadata>,
}

#[derive(Debug)]
pub struct AgentStoreError {
    code: &'static str,
    message: &'static str,
    recovery: RecoveryState,
}

impl AgentStoreError {
    pub(super) fn recovery_required(source_path: PathBuf, backup: Option<BackupMetadata>) -> Self {
        Self {
            code: "agent_store_recovery_required",
            message: "Agent 数据库需要人工恢复",
            recovery: RecoveryState {
                source_path,
                backup,
            },
        }
    }

    pub(super) fn migration_failed(source_path: PathBuf, backup: Option<BackupMetadata>) -> Self {
        Self {
            code: "agent_store_migration_failed",
            message: "Agent 数据库迁移失败，已保留源库和备份",
            recovery: RecoveryState {
                source_path,
                backup,
            },
        }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }

    pub fn recovery(&self) -> Option<&RecoveryState> {
        Some(&self.recovery)
    }
}

impl std::fmt::Display for AgentStoreError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.message)
    }
}

impl std::error::Error for AgentStoreError {}
