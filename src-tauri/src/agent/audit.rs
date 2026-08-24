use std::collections::VecDeque;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use super::model::{AgentCapability, AuditResult, Decision, RiskClass};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AuditSummary {
    pub audit_id: Uuid,
    pub task_id: Uuid,
    pub generation_id: String,
    pub capability_kind: String,
    pub risk_class: RiskClass,
    pub policy_version: String,
    pub scope: String,
    pub decision: String,
    pub result: String,
    pub error_code: Option<String>,
    pub occurred_at: DateTime<Utc>,
}

impl AuditSummary {
    pub fn from_decision(
        task_id: Uuid,
        generation_id: impl Into<String>,
        capability: &AgentCapability,
        risk_class: RiskClass,
        policy_version: impl Into<String>,
        decision: Decision,
        result: AuditResult,
        error_code: Option<&str>,
        occurred_at: DateTime<Utc>,
    ) -> Self {
        Self {
            audit_id: Uuid::new_v4(),
            task_id,
            generation_id: generation_id.into(),
            capability_kind: format!("{:?}", capability.kind()),
            risk_class,
            policy_version: policy_version.into(),
            scope: redact_scope(scope_for(capability)),
            decision: decision_code(decision).to_owned(),
            result: result_code(result).to_owned(),
            error_code: error_code.map(redact_error_code),
            occurred_at,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum AuditError {
    Immutable,
    EntryTooLarge,
    InvalidCapacity,
}

pub struct AuditLog {
    entries: VecDeque<AuditSummary>,
    capacity: usize,
    bytes: usize,
}

impl AuditLog {
    const MAX_ENTRY_BYTES: usize = 16 * 1024;
    const MAX_BYTES: usize = 512 * 1024;

    pub fn new(capacity: usize) -> Result<Self, AuditError> {
        if capacity == 0 {
            return Err(AuditError::InvalidCapacity);
        }
        Ok(Self {
            entries: VecDeque::with_capacity(capacity),
            capacity,
            bytes: 0,
        })
    }

    pub fn record(&mut self, entry: AuditSummary) -> Result<(), AuditError> {
        let entry_bytes = serde_json::to_vec(&entry).map_err(|_| AuditError::EntryTooLarge)?;
        let entry_size = entry_bytes.len();
        if entry_size > Self::MAX_ENTRY_BYTES || entry_size > Self::MAX_BYTES {
            return Err(AuditError::EntryTooLarge);
        }
        while self.entries.len() == self.capacity || self.bytes + entry_size > Self::MAX_BYTES {
            if let Some(evicted) = self.entries.pop_front() {
                self.bytes = self
                    .bytes
                    .saturating_sub(serde_json::to_vec(&evicted).unwrap_or_default().len());
            } else {
                break;
            }
        }
        self.entries.push_back(entry);
        self.bytes += entry_size;
        Ok(())
    }

    pub fn disable(&mut self) -> Result<(), AuditError> {
        Err(AuditError::Immutable)
    }

    pub fn entries(&self) -> impl Iterator<Item = &AuditSummary> {
        self.entries.iter()
    }

    #[cfg(test)]
    fn bytes(&self) -> usize {
        self.bytes
    }
}

fn scope_for(capability: &AgentCapability) -> String {
    capability.canonical_scope()
}

pub(crate) fn redact_scope(value: impl AsRef<str>) -> String {
    let mut value = value
        .as_ref()
        .chars()
        .filter(|character| !character.is_control())
        .collect::<String>();
    let earliest_secret = [
        "authorization",
        "bearer",
        "api_key",
        "api-key",
        "api key",
        "apikey",
        "access_token",
        "access-token",
        "client_secret",
        "client-secret",
        "password",
        "secret",
        "token",
    ]
    .iter()
    .filter_map(|marker| {
        value
            .to_ascii_lowercase()
            .find(marker)
            .map(|index| (index, *marker))
    })
    .min_by_key(|(index, _)| *index);
    if let Some((index, _)) = earliest_secret {
        value.truncate(index);
        value.push_str("[REDACTED]");
    }
    if value.len() > 512 {
        value.truncate(512);
    }
    value
}

pub(crate) fn redact_error_code(value: &str) -> String {
    let lower = value.to_ascii_lowercase();
    if [
        "authorization",
        "bearer",
        "api_key",
        "api-key",
        "api key",
        "apikey",
        "access_token",
        "access-token",
        "client_secret",
        "client-secret",
        "password",
        "secret",
        "token",
    ]
    .iter()
    .any(|marker| lower.contains(marker))
    {
        return "sensitive-error".to_owned();
    }
    let sanitized = value
        .chars()
        .filter(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '_' | '-' | '.')
        })
        .take(96)
        .collect::<String>();
    if sanitized.is_empty() {
        "redacted-error".to_owned()
    } else {
        sanitized
    }
}

fn decision_code(decision: Decision) -> &'static str {
    match decision {
        Decision::AllowOnce => "allow-once",
        Decision::AllowForTask => "allow-for-task",
        Decision::RequestApproval { .. } => "request-approval",
        Decision::Denied { .. } => "deny",
    }
}

fn result_code(result: AuditResult) -> &'static str {
    match result {
        AuditResult::Allowed => "allowed",
        AuditResult::Denied => "denied",
        AuditResult::ApprovalPending => "approval-pending",
        AuditResult::Expired => "expired",
        AuditResult::Cancelled => "cancelled",
        AuditResult::Failed => "failed",
    }
}

#[cfg(test)]
mod tests {
    use chrono::Utc;
    use uuid::Uuid;

    use super::{AuditError, AuditLog, AuditSummary};
    use crate::agent::model::{AgentCapability, AuditResult, Decision, RiskClass};

    #[test]
    fn audit_summary_is_bounded_and_redacts_secret_like_scope() {
        let entry = AuditSummary::from_decision(
            Uuid::new_v4(),
            "generation-a",
            &AgentCapability::ExternalWrite {
                service: "deploy".into(),
                action: "POST".into(),
                target: "Authorization: Bearer super-secret-token".into(),
            },
            RiskClass::ExternalWrite,
            "agent-policy-v1",
            Decision::RequestApproval {
                reason: crate::agent::model::DecisionReason::UserApprovalRequired,
            },
            AuditResult::ApprovalPending,
            Some("authorization_header_contains_secret"),
            Utc::now(),
        );
        let serialized = serde_json::to_string(&entry).unwrap();
        assert!(!serialized.contains("super-secret-token"));
        assert!(!serialized.contains("authorization_header_contains_secret"));
        assert!(entry.scope.len() <= 512);
    }

    #[test]
    fn audit_log_is_bounded_and_cannot_be_disabled() {
        let mut log = AuditLog::new(1).unwrap();
        let entry = AuditSummary::from_decision(
            Uuid::new_v4(),
            "generation-a",
            &AgentCapability::FileRead {
                path: "/tmp/a".into(),
            },
            RiskClass::Observation,
            "agent-policy-v1",
            Decision::AllowOnce,
            AuditResult::Allowed,
            None,
            Utc::now(),
        );
        log.record(entry.clone()).unwrap();
        log.record(entry).unwrap();
        assert_eq!(log.entries().count(), 1);
        assert_eq!(log.disable(), Err(AuditError::Immutable));
    }

    #[test]
    fn zero_capacity_is_rejected_and_secret_values_are_not_retained() {
        assert!(matches!(AuditLog::new(0), Err(AuditError::InvalidCapacity)));
        let entry = AuditSummary::from_decision(
            Uuid::new_v4(),
            "generation-a",
            &AgentCapability::ExternalWrite {
                service: "http".into(),
                action: "POST".into(),
                target: "password=swordfish; Authorization: Bearer abc123".into(),
            },
            RiskClass::SecuritySensitive,
            "agent-policy-v1",
            Decision::Denied {
                reason: crate::agent::model::DecisionReason::ProductBoundary,
            },
            AuditResult::Failed,
            Some("password_value_exposed"),
            Utc::now(),
        );
        let serialized = serde_json::to_string(&entry).unwrap();
        assert!(!serialized.contains("swordfish"));
        assert!(!serialized.contains("abc123"));
        assert_eq!(entry.error_code.as_deref(), Some("sensitive-error"));
    }

    #[test]
    fn audit_log_enforces_a_total_memory_budget() {
        let mut log = AuditLog::new(10_000).unwrap();
        for _ in 0..2_000 {
            let entry = AuditSummary::from_decision(
                Uuid::new_v4(),
                "generation-a",
                &AgentCapability::ExternalWrite {
                    service: "deploy".into(),
                    action: "POST".into(),
                    target: "x".repeat(512),
                },
                RiskClass::ExternalWrite,
                "agent-policy-v1",
                Decision::RequestApproval {
                    reason: crate::agent::model::DecisionReason::UserApprovalRequired,
                },
                AuditResult::ApprovalPending,
                None,
                Utc::now(),
            );
            log.record(entry).unwrap();
        }
        assert!(log.bytes() <= AuditLog::MAX_BYTES);
        assert!(log.entries().count() < 2_000);
    }
}
