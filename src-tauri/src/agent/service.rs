use std::sync::Arc;

use chrono::{DateTime, Utc};
use rusqlite::OptionalExtension;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::agent_store::{model::AgentStoreError, AgentStore};

use super::{
    audit::{redact_error_code, redact_scope},
    model::{
        AgentCapability, CapabilityKind, CapabilityRequest, Decision, DecisionReason, RiskClass,
        TaskContext,
    },
    permissions::{evaluate_capability, validate_network_destination, PermissionError},
};

pub const POLICY_VERSION: &str = "agent-policy-v1";

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ApprovalDecision {
    AllowOnce,
    AllowForTask,
    Deny,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ApprovalStatus {
    Pending,
    ApprovedOnce,
    ApprovedForTask,
    Denied,
    Consumed,
    Cancelled,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ApprovalRequest {
    pub request_id: Uuid,
    pub task_id: Uuid,
    pub generation_id: String,
    pub capability_kind: CapabilityKind,
    pub canonical_scope: String,
    pub risk_class: RiskClass,
    pub policy_version: String,
    pub requested_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ApprovalRecord {
    pub approval_id: Uuid,
    pub request: ApprovalRequest,
    pub status: ApprovalStatus,
    pub decision: Option<ApprovalDecision>,
    pub resolved_at: Option<DateTime<Utc>>,
    pub result_category: Option<String>,
    pub error_code: Option<String>,
}

#[derive(Debug)]
pub enum BrokerError {
    Store(AgentStoreError),
    Database(rusqlite::Error),
    InvalidRequest(&'static str),
    DuplicateRequest,
    NotFound,
    StaleGeneration,
    TaskInactive,
    Expired,
    AlreadyResolved,
    InvalidDecision,
    CapabilityMismatch,
    CapabilityDenied,
    ExecutionFailed,
    Permission(PermissionError),
}

impl From<AgentStoreError> for BrokerError {
    fn from(error: AgentStoreError) -> Self {
        Self::Store(error)
    }
}

impl From<rusqlite::Error> for BrokerError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Database(error)
    }
}

impl From<PermissionError> for BrokerError {
    fn from(error: PermissionError) -> Self {
        Self::Permission(error)
    }
}

pub struct PermissionBroker {
    store: Arc<AgentStore>,
}

impl PermissionBroker {
    pub fn new(store: Arc<AgentStore>) -> Self {
        Self { store }
    }

    pub fn request(
        &self,
        capability: &AgentCapability,
        request: ApprovalRequest,
    ) -> Result<ApprovalRecord, BrokerError> {
        validate_request(capability, &request)?;
        let mut writer = self.store.writer()?;
        let transaction = writer
            .connection_mut()
            .unchecked_transaction()
            .map_err(BrokerError::Database)?;
        ensure_task_generation(&transaction, request.task_id, &request.generation_id)?;
        let approval_id = Uuid::new_v4();
        transaction
            .execute(
                "INSERT INTO approvals (
                    approval_id, task_id, request_id, generation_id, capability_kind,
                    canonical_scope, risk_class, policy_version, status, requested_at,
                    resolved_at, decision, result_category, error_code, expires_at
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'pending', ?9, NULL, NULL, 'pending', NULL, ?10)",
                rusqlite::params![
                    approval_id.to_string(),
                    request.task_id.to_string(),
                    request.request_id.to_string(),
                    request.generation_id,
                    encode_kind(request.capability_kind),
                    request.canonical_scope,
                    encode_risk(request.risk_class),
                    request.policy_version,
                    request.requested_at.to_rfc3339(),
                    request.expires_at.to_rfc3339(),
                ],
            )
            .map_err(|error| {
                if is_unique_constraint(&error) {
                    BrokerError::DuplicateRequest
                } else {
                    BrokerError::Database(error)
                }
            })?;
        transaction
            .execute(
                "INSERT INTO audit_summaries (
                    audit_id, task_id, request_id, generation_id, event_kind,
                    capability_kind, canonical_scope, risk_class, policy_version,
                    decision, result_category, error_code, occurred_at
                ) VALUES (?1, ?2, ?3, ?4, 'approval-requested', ?5, ?6, ?7, ?8, NULL, 'pending', NULL, ?9)",
                rusqlite::params![
                    Uuid::new_v4().to_string(),
                    request.task_id.to_string(),
                    request.request_id.to_string(),
                    request.generation_id,
                    encode_kind(request.capability_kind),
                    redact_scope(&request.canonical_scope),
                    encode_risk(request.risk_class),
                    request.policy_version,
                    request.requested_at.to_rfc3339(),
                ],
            )?;
        transaction.commit()?;
        Ok(ApprovalRecord {
            approval_id,
            request,
            status: ApprovalStatus::Pending,
            decision: None,
            resolved_at: None,
            result_category: Some("pending".to_owned()),
            error_code: None,
        })
    }

    pub fn pending(
        &self,
        task_id: Uuid,
        generation_id: &str,
    ) -> Result<Vec<ApprovalRecord>, BrokerError> {
        let connection = self.store.reader()?;
        let active_generation: Option<String> = connection
            .query_row(
                "SELECT worker_sessions.generation_id FROM worker_sessions
                 WHERE worker_sessions.task_id = ?1
                   AND worker_sessions.generation_id = ?2
                   AND EXISTS (
                       SELECT 1 FROM tasks
                       WHERE tasks.task_id = worker_sessions.task_id
                         AND tasks.status IN ('active', 'running')
                   )",
                rusqlite::params![task_id.to_string(), generation_id],
                |row| row.get(0),
            )
            .optional()?;
        if active_generation.is_none() {
            return Err(BrokerError::StaleGeneration);
        }
        let mut statement = connection.prepare(
            "SELECT approval_id, request_id, task_id, generation_id, capability_kind,
                    canonical_scope, risk_class, policy_version, status, requested_at,
                    resolved_at, decision, result_category, error_code, expires_at
             FROM approvals
             WHERE task_id = ?1 AND generation_id = ?2 AND status = 'pending'
             ORDER BY requested_at, approval_id",
        )?;
        let records = statement
            .query_map(
                rusqlite::params![task_id.to_string(), generation_id],
                |row| decode_record(row),
            )?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(records)
    }

    pub fn by_request_id(
        &self,
        task_id: Uuid,
        generation_id: &str,
        request_id: Uuid,
    ) -> Result<Option<ApprovalRecord>, BrokerError> {
        let connection = self.store.reader()?;
        connection
            .query_row(
                "SELECT approval_id, request_id, task_id, generation_id, capability_kind,
                        canonical_scope, risk_class, policy_version, status, requested_at,
                        resolved_at, decision, result_category, error_code, expires_at
                 FROM approvals
                 WHERE task_id = ?1 AND generation_id = ?2 AND request_id = ?3",
                rusqlite::params![task_id.to_string(), generation_id, request_id.to_string()],
                decode_record,
            )
            .optional()
            .map_err(BrokerError::Database)
    }

    pub fn has_grant(
        &self,
        task_id: Uuid,
        generation_id: &str,
        capability: &AgentCapability,
        policy_version: &str,
        now: DateTime<Utc>,
    ) -> Result<bool, BrokerError> {
        let connection = self.store.reader()?;
        let found: Option<String> = connection
            .query_row(
                "SELECT grant_id FROM task_grants
                 WHERE task_id = ?1 AND generation_id = ?2
                   AND capability_kind = ?3 AND canonical_scope = ?4
                   AND policy_version = ?5
                   AND EXISTS (
                       SELECT 1 FROM tasks
                       WHERE tasks.task_id = task_grants.task_id
                         AND tasks.status IN ('active', 'running')
                   )
                   AND EXISTS (
                       SELECT 1 FROM worker_sessions
                       WHERE worker_sessions.task_id = task_grants.task_id
                         AND worker_sessions.generation_id = task_grants.generation_id
                   )
                   AND (expires_at IS NULL OR expires_at > ?6)",
                rusqlite::params![
                    task_id.to_string(),
                    generation_id,
                    encode_kind(capability.kind()),
                    capability.canonical_scope(),
                    policy_version,
                    now.to_rfc3339(),
                ],
                |row| row.get(0),
            )
            .optional()?;
        Ok(found.is_some())
    }

    fn record_decision(
        &self,
        request: &CapabilityRequest,
        capability: &AgentCapability,
        decision: Decision,
        result_category: &str,
    ) -> Result<(), BrokerError> {
        let mut writer = self.store.writer()?;
        let transaction = writer.connection_mut().unchecked_transaction()?;
        transaction.execute(
            "INSERT INTO audit_summaries (
                audit_id, task_id, request_id, generation_id, event_kind,
                capability_kind, canonical_scope, risk_class, policy_version,
                decision, result_category, error_code, occurred_at
             ) VALUES (?1, ?2, ?3, ?4, 'authorization-decision', ?5, ?6, ?7, ?8,
                       ?9, ?10, ?11, ?12)",
            rusqlite::params![
                Uuid::new_v4().to_string(),
                request.task_id.to_string(),
                request.request_id.to_string(),
                request.generation_id,
                encode_kind(capability.kind()),
                redact_scope(capability.canonical_scope()),
                encode_risk(capability.risk_class()),
                POLICY_VERSION,
                authorization_decision_code(decision),
                result_category,
                decision_error_code(decision),
                request.issued_at.to_rfc3339(),
            ],
        )?;
        transaction.commit()?;
        Ok(())
    }

    fn record_execution_failure(
        &self,
        request: &CapabilityRequest,
        capability: &AgentCapability,
        error: &str,
    ) -> Result<(), BrokerError> {
        let mut writer = self.store.writer()?;
        let transaction = writer.connection_mut().unchecked_transaction()?;
        transaction.execute(
            "INSERT INTO audit_summaries (
                audit_id, task_id, request_id, generation_id, event_kind,
                capability_kind, canonical_scope, risk_class, policy_version,
                decision, result_category, error_code, occurred_at
             ) VALUES (?1, ?2, ?3, ?4, 'execution-failed', ?5, ?6, ?7, ?8,
                       NULL, 'failed', ?9, ?10)",
            rusqlite::params![
                Uuid::new_v4().to_string(),
                request.task_id.to_string(),
                request.request_id.to_string(),
                request.generation_id,
                encode_kind(capability.kind()),
                redact_scope(capability.canonical_scope()),
                encode_risk(capability.risk_class()),
                POLICY_VERSION,
                redact_error_code(error),
                request.issued_at.to_rfc3339(),
            ],
        )?;
        transaction.commit()?;
        Ok(())
    }

    fn record_permission_error(
        &self,
        request: &CapabilityRequest,
        capability: &AgentCapability,
        error: &PermissionError,
    ) -> Result<(), BrokerError> {
        let mut writer = self.store.writer()?;
        let transaction = writer.connection_mut().unchecked_transaction()?;
        transaction.execute(
            "INSERT INTO audit_summaries (
                audit_id, task_id, request_id, generation_id, event_kind,
                capability_kind, canonical_scope, risk_class, policy_version,
                decision, result_category, error_code, occurred_at
             ) VALUES (?1, ?2, ?3, ?4, 'permission-rejected', ?5, ?6, ?7, ?8,
                       'deny', 'denied', ?9, ?10)",
            rusqlite::params![
                Uuid::new_v4().to_string(),
                request.task_id.to_string(),
                request.request_id.to_string(),
                request.generation_id,
                encode_kind(capability.kind()),
                redact_scope(capability.canonical_scope()),
                encode_risk(capability.risk_class()),
                POLICY_VERSION,
                permission_error_code(error),
                request.issued_at.to_rfc3339(),
            ],
        )?;
        transaction.commit()?;
        Ok(())
    }

    pub fn resolve(
        &self,
        approval_id: Uuid,
        task_id: Uuid,
        generation_id: &str,
        decision: ApprovalDecision,
        resolved_at: DateTime<Utc>,
    ) -> Result<ApprovalRecord, BrokerError> {
        let mut writer = self.store.writer()?;
        let transaction = writer.connection_mut().unchecked_transaction()?;
        let record = load_record(&transaction, approval_id)?;
        if record.request.task_id != task_id || record.request.generation_id != generation_id {
            return Err(BrokerError::StaleGeneration);
        }
        if record.status != ApprovalStatus::Pending {
            return Err(BrokerError::AlreadyResolved);
        }
        ensure_task_generation(&transaction, task_id, generation_id)?;
        if record.request.expires_at <= resolved_at {
            transaction.execute(
                "UPDATE approvals
                 SET status = 'cancelled', resolved_at = ?1,
                     result_category = 'expired', error_code = 'approval-expired'
                 WHERE approval_id = ?2 AND status = 'pending'",
                rusqlite::params![resolved_at.to_rfc3339(), approval_id.to_string()],
            )?;
            transaction.execute(
                "INSERT INTO audit_summaries (
                    audit_id, task_id, request_id, generation_id, event_kind,
                    capability_kind, canonical_scope, risk_class, policy_version,
                    decision, result_category, error_code, occurred_at
                 ) VALUES (?1, ?2, ?3, ?4, 'approval-expired', ?5, ?6, ?7, ?8,
                           NULL, 'expired', 'approval-expired', ?9)",
                rusqlite::params![
                    Uuid::new_v4().to_string(),
                    task_id.to_string(),
                    record.request.request_id.to_string(),
                    generation_id,
                    encode_kind(record.request.capability_kind),
                    redact_scope(&record.request.canonical_scope),
                    encode_risk(record.request.risk_class),
                    record.request.policy_version,
                    resolved_at.to_rfc3339(),
                ],
            )?;
            transaction.commit()?;
            return Err(BrokerError::Expired);
        }
        let status = match decision {
            ApprovalDecision::AllowOnce => ApprovalStatus::ApprovedOnce,
            ApprovalDecision::AllowForTask => ApprovalStatus::ApprovedForTask,
            ApprovalDecision::Deny => ApprovalStatus::Denied,
        };
        let result = match decision {
            ApprovalDecision::Deny => "denied",
            ApprovalDecision::AllowOnce | ApprovalDecision::AllowForTask => "approved",
        };
        let changed = transaction.execute(
            "UPDATE approvals
             SET status = ?1, decision = ?2, resolved_at = ?3, result_category = ?4
             WHERE approval_id = ?5 AND status = 'pending'",
            rusqlite::params![
                encode_status(status),
                encode_decision(decision),
                resolved_at.to_rfc3339(),
                result,
                approval_id.to_string(),
            ],
        )?;
        if changed != 1 {
            return Err(BrokerError::AlreadyResolved);
        }
        if decision == ApprovalDecision::AllowForTask {
            transaction.execute(
                "INSERT OR IGNORE INTO task_grants (
                    grant_id, task_id, generation_id, capability_kind, canonical_scope,
                    policy_version, expires_at, created_at
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                rusqlite::params![
                    Uuid::new_v4().to_string(),
                    task_id.to_string(),
                    generation_id,
                    encode_kind(record.request.capability_kind),
                    record.request.canonical_scope,
                    record.request.policy_version,
                    Some(record.request.expires_at.to_rfc3339()),
                    resolved_at.to_rfc3339(),
                ],
            )?;
        }
        transaction.execute(
            "INSERT INTO audit_summaries (
                audit_id, task_id, request_id, generation_id, event_kind,
                capability_kind, canonical_scope, risk_class, policy_version,
                decision, result_category, error_code, occurred_at
             ) VALUES (?1, ?2, ?3, ?4, 'approval-resolved', ?5, ?6, ?7, ?8, ?9, ?10, NULL, ?11)",
            rusqlite::params![
                Uuid::new_v4().to_string(),
                task_id.to_string(),
                record.request.request_id.to_string(),
                generation_id,
                encode_kind(record.request.capability_kind),
                redact_scope(&record.request.canonical_scope),
                encode_risk(record.request.risk_class),
                record.request.policy_version,
                encode_decision(decision),
                result,
                resolved_at.to_rfc3339(),
            ],
        )?;
        transaction.commit()?;
        Ok(ApprovalRecord {
            status,
            decision: Some(decision),
            resolved_at: Some(resolved_at),
            result_category: Some(result.to_owned()),
            ..record
        })
    }

    pub fn consume_once(
        &self,
        approval_id: Uuid,
        task_id: Uuid,
        generation_id: &str,
        capability: &AgentCapability,
        policy_version: &str,
        consumed_at: DateTime<Utc>,
    ) -> Result<(), BrokerError> {
        let mut writer = self.store.writer()?;
        let transaction = writer.connection_mut().unchecked_transaction()?;
        let record = load_record(&transaction, approval_id)?;
        if record.request.task_id != task_id || record.request.generation_id != generation_id {
            return Err(BrokerError::StaleGeneration);
        }
        if record.request.expires_at <= consumed_at {
            return Err(BrokerError::Expired);
        }
        validate_capability_summary(capability, &record.request, policy_version)?;
        ensure_task_generation(&transaction, task_id, generation_id)?;
        let changed = transaction.execute(
            "UPDATE approvals SET status = 'consumed', result_category = 'consumed', resolved_at = ?1
             WHERE approval_id = ?2 AND task_id = ?3 AND generation_id = ?4 AND status = 'approved-once'",
            rusqlite::params![consumed_at.to_rfc3339(), approval_id.to_string(), task_id.to_string(), generation_id],
        )?;
        if changed == 0 {
            return Err(BrokerError::AlreadyResolved);
        }
        transaction.execute(
            "INSERT INTO audit_summaries (
                audit_id, task_id, request_id, generation_id, event_kind,
                capability_kind, canonical_scope, risk_class, policy_version,
                decision, result_category, error_code, occurred_at
             ) VALUES (?1, ?2, ?3, ?4, 'approval-consumed', ?5, ?6, ?7, ?8,
                       'allow-once', 'consumed', NULL, ?9)",
            rusqlite::params![
                Uuid::new_v4().to_string(),
                task_id.to_string(),
                record.request.request_id.to_string(),
                generation_id,
                encode_kind(record.request.capability_kind),
                redact_scope(&record.request.canonical_scope),
                encode_risk(record.request.risk_class),
                policy_version,
                consumed_at.to_rfc3339(),
            ],
        )?;
        transaction.commit()?;
        Ok(())
    }

    pub fn cancel_task(
        &self,
        task_id: Uuid,
        generation_id: &str,
        cancelled_at: DateTime<Utc>,
    ) -> Result<(), BrokerError> {
        self.finalize_task(
            task_id,
            generation_id,
            cancelled_at,
            "cancelled",
            "task-cancelled",
            "cancelled",
        )
    }

    pub fn complete_task(
        &self,
        task_id: Uuid,
        generation_id: &str,
        completed_at: DateTime<Utc>,
    ) -> Result<(), BrokerError> {
        self.finalize_task(
            task_id,
            generation_id,
            completed_at,
            "completed",
            "task-completed",
            "completed",
        )
    }

    fn finalize_task(
        &self,
        task_id: Uuid,
        generation_id: &str,
        finished_at: DateTime<Utc>,
        task_status: &str,
        event_kind: &str,
        result_category: &str,
    ) -> Result<(), BrokerError> {
        let mut writer = self.store.writer()?;
        let transaction = writer.connection_mut().unchecked_transaction()?;
        ensure_task_active(&transaction, task_id)?;
        ensure_task_generation(&transaction, task_id, generation_id)?;
        transaction.execute(
            "UPDATE approvals SET status = 'cancelled', result_category = ?1, resolved_at = ?2
             WHERE task_id = ?3 AND status IN ('pending', 'approved-once', 'approved-for-task')",
            rusqlite::params![
                result_category,
                finished_at.to_rfc3339(),
                task_id.to_string()
            ],
        )?;
        transaction.execute(
            "UPDATE task_grants SET expires_at = ?1 WHERE task_id = ?2 AND (expires_at IS NULL OR expires_at > ?1)",
            rusqlite::params![finished_at.to_rfc3339(), task_id.to_string()],
        )?;
        transaction.execute(
            "UPDATE tasks SET status = ?1, updated_at = ?2
             WHERE task_id = ?3 AND status IN ('active', 'running')",
            rusqlite::params![task_status, finished_at.to_rfc3339(), task_id.to_string()],
        )?;
        transaction.execute(
            "INSERT INTO audit_summaries (
                audit_id, task_id, request_id, generation_id, event_kind,
                capability_kind, canonical_scope, risk_class, policy_version,
                decision, result_category, error_code, occurred_at
             ) VALUES (?1, ?2, NULL, ?3, ?4, NULL, NULL, 'unknown',
                       ?5, ?6, ?6, NULL, ?7)",
            rusqlite::params![
                Uuid::new_v4().to_string(),
                task_id.to_string(),
                generation_id,
                event_kind,
                POLICY_VERSION,
                result_category,
                finished_at.to_rfc3339(),
            ],
        )?;
        transaction.commit()?;
        Ok(())
    }
}

#[derive(Debug)]
pub enum AuthorizationOutcome {
    Allowed {
        capability: AgentCapability,
        decision: Decision,
    },
    Approved {
        capability: AgentCapability,
        approval: ApprovalRecord,
    },
    ApprovalRequired {
        capability: AgentCapability,
        approval: ApprovalRecord,
    },
    Denied {
        capability: AgentCapability,
        decision: Decision,
    },
}

#[derive(Debug)]
pub enum ExecutionOutcome {
    Completed,
    ApprovalRequired { approval: ApprovalRecord },
    Denied { decision: Decision },
}

pub trait AgentActionExecutor {
    fn execute(&mut self, capability: &AgentCapability) -> Result<(), String>;
}

pub struct AgentService {
    broker: PermissionBroker,
}

impl AgentService {
    pub fn new(store: Arc<AgentStore>) -> Self {
        Self {
            broker: PermissionBroker::new(store),
        }
    }

    pub fn authorize(
        &self,
        context: &TaskContext,
        request: &CapabilityRequest,
    ) -> Result<AuthorizationOutcome, BrokerError> {
        let (decision, capability) = match evaluate_capability(context, request) {
            Ok(result) => result,
            Err(error) => {
                self.broker
                    .record_permission_error(request, &request.capability, &error)?;
                return Ok(AuthorizationOutcome::Denied {
                    capability: request.capability.clone(),
                    decision: Decision::Denied {
                        reason: DecisionReason::ProductBoundary,
                    },
                });
            }
        };
        if matches!(decision, Decision::Denied { .. }) {
            self.broker
                .record_decision(request, &capability, decision, "denied")?;
            return Ok(AuthorizationOutcome::Denied {
                capability,
                decision,
            });
        }
        if self.broker.has_grant(
            context.task_id,
            &context.generation_id,
            &capability,
            POLICY_VERSION,
            context.now,
        )? {
            self.broker
                .record_decision(request, &capability, Decision::AllowForTask, "allowed")?;
            return Ok(AuthorizationOutcome::Allowed {
                capability,
                decision: Decision::AllowForTask,
            });
        }
        if !matches!(decision, Decision::RequestApproval { .. }) {
            self.broker
                .record_decision(request, &capability, decision, "allowed")?;
            return Ok(AuthorizationOutcome::Allowed {
                capability,
                decision,
            });
        }
        let approval = match self.broker.request(
            &capability,
            ApprovalRequest {
                request_id: request.request_id,
                task_id: request.task_id,
                generation_id: request.generation_id.clone(),
                capability_kind: capability.kind(),
                canonical_scope: capability.canonical_scope(),
                risk_class: capability.risk_class(),
                policy_version: POLICY_VERSION.to_owned(),
                requested_at: context.now,
                expires_at: request.expires_at,
            },
        ) {
            Ok(approval) => approval,
            Err(BrokerError::DuplicateRequest) => {
                let existing = self
                    .broker
                    .by_request_id(context.task_id, &context.generation_id, request.request_id)?
                    .ok_or(BrokerError::NotFound)?;
                validate_capability_summary(&capability, &existing.request, POLICY_VERSION)?;
                existing
            }
            Err(error) => return Err(error),
        };
        match approval.status {
            ApprovalStatus::Pending => Ok(AuthorizationOutcome::ApprovalRequired {
                capability,
                approval,
            }),
            ApprovalStatus::ApprovedOnce => Ok(AuthorizationOutcome::Approved {
                capability,
                approval,
            }),
            ApprovalStatus::ApprovedForTask => Ok(AuthorizationOutcome::Denied {
                capability,
                decision: Decision::Denied {
                    reason: DecisionReason::ProductBoundary,
                },
            }),
            ApprovalStatus::Denied | ApprovalStatus::Consumed | ApprovalStatus::Cancelled => {
                Ok(AuthorizationOutcome::Denied {
                    capability,
                    decision: Decision::Denied {
                        reason: super::model::DecisionReason::ProductBoundary,
                    },
                })
            }
        }
    }

    pub fn execute<E: AgentActionExecutor>(
        &self,
        context: &TaskContext,
        request: &CapabilityRequest,
        executor: &mut E,
    ) -> Result<ExecutionOutcome, BrokerError> {
        match self.authorize(context, request)? {
            AuthorizationOutcome::Allowed { capability, .. } => {
                if !self.validate_execution_target(request, &capability)? {
                    return Ok(ExecutionOutcome::Denied {
                        decision: Decision::Denied {
                            reason: DecisionReason::ProductBoundary,
                        },
                    });
                }
                self.run_action(request, &capability, executor)
            }
            AuthorizationOutcome::Approved {
                capability,
                approval,
            } => {
                if !self.validate_execution_target(request, &capability)? {
                    return Ok(ExecutionOutcome::Denied {
                        decision: Decision::Denied {
                            reason: DecisionReason::ProductBoundary,
                        },
                    });
                }
                self.broker.consume_once(
                    approval.approval_id,
                    request.task_id,
                    &request.generation_id,
                    &capability,
                    POLICY_VERSION,
                    context.now,
                )?;
                self.run_action(request, &capability, executor)
            }
            AuthorizationOutcome::ApprovalRequired { approval, .. } => {
                Ok(ExecutionOutcome::ApprovalRequired { approval })
            }
            AuthorizationOutcome::Denied { decision, .. } => {
                Ok(ExecutionOutcome::Denied { decision })
            }
        }
    }

    fn validate_execution_target(
        &self,
        request: &CapabilityRequest,
        capability: &AgentCapability,
    ) -> Result<bool, BrokerError> {
        match validate_network_destination(capability) {
            Ok(()) => Ok(true),
            Err(error) => {
                self.broker
                    .record_permission_error(request, capability, &error)?;
                Ok(false)
            }
        }
    }

    fn run_action<E: AgentActionExecutor>(
        &self,
        request: &CapabilityRequest,
        capability: &AgentCapability,
        executor: &mut E,
    ) -> Result<ExecutionOutcome, BrokerError> {
        match executor.execute(capability) {
            Ok(()) => Ok(ExecutionOutcome::Completed),
            Err(error) => {
                self.broker
                    .record_execution_failure(request, capability, &error)?;
                Err(BrokerError::ExecutionFailed)
            }
        }
    }

    pub fn consume_once(
        &self,
        context: &TaskContext,
        request: &CapabilityRequest,
        approval_id: Uuid,
        consumed_at: DateTime<Utc>,
    ) -> Result<AgentCapability, BrokerError> {
        let (decision, capability) = match evaluate_capability(context, request) {
            Ok(result) => result,
            Err(error) => {
                self.broker
                    .record_permission_error(request, &request.capability, &error)?;
                return Err(BrokerError::Permission(error));
            }
        };
        if matches!(decision, Decision::Denied { .. }) {
            return Err(BrokerError::CapabilityDenied);
        }
        self.broker.consume_once(
            approval_id,
            request.task_id,
            &request.generation_id,
            &capability,
            POLICY_VERSION,
            consumed_at,
        )?;
        Ok(capability)
    }

    pub fn pending(
        &self,
        task_id: Uuid,
        generation_id: &str,
    ) -> Result<Vec<ApprovalRecord>, BrokerError> {
        self.broker.pending(task_id, generation_id)
    }

    pub fn resolve(
        &self,
        approval_id: Uuid,
        task_id: Uuid,
        generation_id: &str,
        decision: ApprovalDecision,
        resolved_at: DateTime<Utc>,
    ) -> Result<ApprovalRecord, BrokerError> {
        self.broker
            .resolve(approval_id, task_id, generation_id, decision, resolved_at)
    }

    pub fn cancel_task(
        &self,
        task_id: Uuid,
        generation_id: &str,
        cancelled_at: DateTime<Utc>,
    ) -> Result<(), BrokerError> {
        self.broker
            .cancel_task(task_id, generation_id, cancelled_at)
    }

    pub fn complete_task(
        &self,
        task_id: Uuid,
        generation_id: &str,
        completed_at: DateTime<Utc>,
    ) -> Result<(), BrokerError> {
        self.broker
            .complete_task(task_id, generation_id, completed_at)
    }
}

fn is_unique_constraint(error: &rusqlite::Error) -> bool {
    matches!(
        error,
        rusqlite::Error::SqliteFailure(sqlite_error, _)
            if sqlite_error.extended_code == rusqlite::ffi::SQLITE_CONSTRAINT_PRIMARYKEY
                || sqlite_error.extended_code == rusqlite::ffi::SQLITE_CONSTRAINT_UNIQUE
    )
}

fn validate_request(
    capability: &AgentCapability,
    request: &ApprovalRequest,
) -> Result<(), BrokerError> {
    if request.request_id.is_nil() || request.task_id.is_nil() {
        return Err(BrokerError::InvalidRequest(
            "approval identifiers are required",
        ));
    }
    if request.generation_id.trim().is_empty() || request.policy_version.trim().is_empty() {
        return Err(BrokerError::InvalidRequest(
            "generation and policy version are required",
        ));
    }
    if request.expires_at <= request.requested_at {
        return Err(BrokerError::InvalidRequest("approval expiry is invalid"));
    }
    if request.canonical_scope.is_empty()
        || request.canonical_scope.len() > 512
        || request.canonical_scope.chars().any(char::is_control)
    {
        return Err(BrokerError::InvalidRequest("canonical scope is invalid"));
    }
    if redact_scope(&request.canonical_scope) != request.canonical_scope {
        return Err(BrokerError::InvalidRequest(
            "sensitive scope is not allowed",
        ));
    }
    validate_capability_summary(capability, request, &request.policy_version)?;
    Ok(())
}

fn validate_capability_summary(
    capability: &AgentCapability,
    request: &ApprovalRequest,
    policy_version: &str,
) -> Result<(), BrokerError> {
    if capability.kind() != request.capability_kind
        || capability.canonical_scope() != request.canonical_scope
        || capability.risk_class() != request.risk_class
        || request.policy_version != policy_version
    {
        return Err(BrokerError::CapabilityMismatch);
    }
    Ok(())
}

fn ensure_task_active(
    transaction: &rusqlite::Transaction<'_>,
    task_id: Uuid,
) -> Result<(), BrokerError> {
    let active: Option<String> = transaction
        .query_row(
            "SELECT status FROM tasks WHERE task_id = ?1 AND status IN ('active', 'running')",
            [task_id.to_string()],
            |row| row.get(0),
        )
        .optional()?;
    if active.is_some() {
        Ok(())
    } else {
        Err(BrokerError::TaskInactive)
    }
}

fn ensure_task_generation(
    transaction: &rusqlite::Transaction<'_>,
    task_id: Uuid,
    generation_id: &str,
) -> Result<(), BrokerError> {
    let generation: Option<String> = transaction
        .query_row(
            "SELECT worker_sessions.generation_id FROM worker_sessions
             WHERE task_id = ?1 AND worker_sessions.generation_id = ?2
               AND EXISTS (SELECT 1 FROM tasks WHERE task_id = ?1 AND status IN ('active', 'running'))",
            rusqlite::params![task_id.to_string(), generation_id],
            |row| row.get(0),
        )
        .optional()?;
    if generation.is_some() {
        Ok(())
    } else {
        Err(BrokerError::StaleGeneration)
    }
}

fn load_record(
    transaction: &rusqlite::Transaction<'_>,
    approval_id: Uuid,
) -> Result<ApprovalRecord, BrokerError> {
    transaction
        .query_row(
            "SELECT approval_id, request_id, task_id, generation_id, capability_kind,
                    canonical_scope, risk_class, policy_version, status, requested_at,
                    resolved_at, decision, result_category, error_code, expires_at
             FROM approvals WHERE approval_id = ?1",
            [approval_id.to_string()],
            decode_record,
        )
        .optional()?
        .ok_or(BrokerError::NotFound)
}

fn decode_record(row: &rusqlite::Row<'_>) -> rusqlite::Result<ApprovalRecord> {
    let approval_id = parse_uuid(row.get::<_, String>(0)?)?;
    let request = ApprovalRequest {
        request_id: parse_uuid(row.get::<_, String>(1)?)?,
        task_id: parse_uuid(row.get::<_, String>(2)?)?,
        generation_id: row.get(3)?,
        capability_kind: decode_kind(&row.get::<_, String>(4)?)?,
        canonical_scope: row.get(5)?,
        risk_class: decode_risk(&row.get::<_, String>(6)?)?,
        policy_version: row.get(7)?,
        requested_at: parse_time(&row.get::<_, String>(9)?)?,
        expires_at: parse_time(&row.get::<_, String>(14)?)?,
    };
    Ok(ApprovalRecord {
        approval_id,
        request,
        status: decode_status(&row.get::<_, String>(8)?)?,
        decision: row
            .get::<_, Option<String>>(11)?
            .as_deref()
            .map(decode_decision)
            .transpose()?,
        resolved_at: row
            .get::<_, Option<String>>(10)?
            .as_deref()
            .map(parse_time)
            .transpose()?,
        result_category: row.get(12)?,
        error_code: row.get(13)?,
    })
}

fn parse_uuid(value: String) -> rusqlite::Result<Uuid> {
    Uuid::parse_str(&value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            value.len(),
            rusqlite::types::Type::Text,
            Box::new(error),
        )
    })
}

fn parse_time(value: &str) -> rusqlite::Result<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .map(|time| time.with_timezone(&Utc))
        .map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(
                value.len(),
                rusqlite::types::Type::Text,
                Box::new(error),
            )
        })
}

fn encode_kind(kind: CapabilityKind) -> &'static str {
    match kind {
        CapabilityKind::FileRead => "file-read",
        CapabilityKind::FileWrite => "file-write",
        CapabilityKind::FileDelete => "file-delete",
        CapabilityKind::Terminal => "terminal",
        CapabilityKind::Network => "network",
        CapabilityKind::PackageInstall => "package-install",
        CapabilityKind::ProcessLaunch => "process-launch",
        CapabilityKind::ExternalWrite => "external-write",
        CapabilityKind::GitCommit => "git-commit",
        CapabilityKind::GitPush => "git-push",
        CapabilityKind::Deploy => "deploy",
        CapabilityKind::CredentialUse => "credential-use",
        CapabilityKind::CredentialExport => "credential-export",
        CapabilityKind::ExtensionCall => "extension-call",
        CapabilityKind::McpCall => "mcp-call",
        CapabilityKind::AuditDisable => "audit-disable",
        CapabilityKind::BridgeBypass => "bridge-bypass",
        CapabilityKind::Unknown => "unknown",
    }
}

fn decode_kind(value: &str) -> rusqlite::Result<CapabilityKind> {
    let kind = match value {
        "file-read" => CapabilityKind::FileRead,
        "file-write" => CapabilityKind::FileWrite,
        "file-delete" => CapabilityKind::FileDelete,
        "terminal" => CapabilityKind::Terminal,
        "network" => CapabilityKind::Network,
        "package-install" => CapabilityKind::PackageInstall,
        "process-launch" => CapabilityKind::ProcessLaunch,
        "external-write" => CapabilityKind::ExternalWrite,
        "git-commit" => CapabilityKind::GitCommit,
        "git-push" => CapabilityKind::GitPush,
        "deploy" => CapabilityKind::Deploy,
        "credential-use" => CapabilityKind::CredentialUse,
        "credential-export" => CapabilityKind::CredentialExport,
        "extension-call" => CapabilityKind::ExtensionCall,
        "mcp-call" => CapabilityKind::McpCall,
        "audit-disable" => CapabilityKind::AuditDisable,
        "bridge-bypass" => CapabilityKind::BridgeBypass,
        "unknown" => CapabilityKind::Unknown,
        _ => {
            return Err(rusqlite::Error::InvalidColumnType(
                4,
                "capability_kind".into(),
                rusqlite::types::Type::Text,
            ))
        }
    };
    Ok(kind)
}

fn encode_risk(risk: RiskClass) -> &'static str {
    match risk {
        RiskClass::Observation => "observation",
        RiskClass::WorkspaceWrite => "workspace-write",
        RiskClass::Destructive => "destructive",
        RiskClass::ExternalWrite => "external-write",
        RiskClass::SecuritySensitive => "security-sensitive",
        RiskClass::Unknown => "unknown",
    }
}

fn decode_risk(value: &str) -> rusqlite::Result<RiskClass> {
    match value {
        "observation" => Ok(RiskClass::Observation),
        "workspace-write" => Ok(RiskClass::WorkspaceWrite),
        "destructive" => Ok(RiskClass::Destructive),
        "external-write" => Ok(RiskClass::ExternalWrite),
        "security-sensitive" => Ok(RiskClass::SecuritySensitive),
        "unknown" => Ok(RiskClass::Unknown),
        _ => Err(rusqlite::Error::InvalidColumnType(
            6,
            "risk_class".into(),
            rusqlite::types::Type::Text,
        )),
    }
}

fn encode_status(status: ApprovalStatus) -> &'static str {
    match status {
        ApprovalStatus::Pending => "pending",
        ApprovalStatus::ApprovedOnce => "approved-once",
        ApprovalStatus::ApprovedForTask => "approved-for-task",
        ApprovalStatus::Denied => "denied",
        ApprovalStatus::Consumed => "consumed",
        ApprovalStatus::Cancelled => "cancelled",
    }
}

fn decode_status(value: &str) -> rusqlite::Result<ApprovalStatus> {
    match value {
        "pending" => Ok(ApprovalStatus::Pending),
        "approved-once" => Ok(ApprovalStatus::ApprovedOnce),
        "approved-for-task" => Ok(ApprovalStatus::ApprovedForTask),
        "denied" => Ok(ApprovalStatus::Denied),
        "consumed" => Ok(ApprovalStatus::Consumed),
        "cancelled" => Ok(ApprovalStatus::Cancelled),
        _ => Err(rusqlite::Error::InvalidColumnType(
            8,
            "status".into(),
            rusqlite::types::Type::Text,
        )),
    }
}

fn encode_decision(decision: ApprovalDecision) -> &'static str {
    match decision {
        ApprovalDecision::AllowOnce => "allow-once",
        ApprovalDecision::AllowForTask => "allow-for-task",
        ApprovalDecision::Deny => "deny",
    }
}

fn decode_decision(value: &str) -> rusqlite::Result<ApprovalDecision> {
    match value {
        "allow-once" => Ok(ApprovalDecision::AllowOnce),
        "allow-for-task" => Ok(ApprovalDecision::AllowForTask),
        "deny" => Ok(ApprovalDecision::Deny),
        _ => Err(rusqlite::Error::InvalidColumnType(
            11,
            "decision".into(),
            rusqlite::types::Type::Text,
        )),
    }
}

fn decision_error_code(decision: Decision) -> Option<&'static str> {
    match decision {
        Decision::Denied { reason } => Some(match reason {
            DecisionReason::MalformedRequest => "malformed-request",
            DecisionReason::ExpiredRequest => "expired-request",
            DecisionReason::TaskMismatch => "task-mismatch",
            DecisionReason::GenerationMismatch => "generation-mismatch",
            DecisionReason::UndisclosedCapability => "undisclosed-capability",
            DecisionReason::TaskNotActive => "task-not-active",
            DecisionReason::ProductBoundary => "product-boundary",
            DecisionReason::ProfileReadOnly => "profile-read-only",
            DecisionReason::CapabilityNotDeclared => "capability-not-declared",
            DecisionReason::ExplicitGrant => "explicit-grant",
            DecisionReason::LowRiskObservation => "low-risk-observation",
            DecisionReason::SmartPolicy => "smart-policy",
            DecisionReason::FullAccess => "full-access",
            DecisionReason::UserApprovalRequired => "user-approval-required",
        }),
        Decision::AllowOnce | Decision::AllowForTask | Decision::RequestApproval { .. } => None,
    }
}

fn authorization_decision_code(decision: Decision) -> &'static str {
    match decision {
        Decision::AllowOnce => "allow-once",
        Decision::AllowForTask => "allow-for-task",
        Decision::RequestApproval { .. } => "request-approval",
        Decision::Denied { .. } => "deny",
    }
}

fn permission_error_code(error: &PermissionError) -> &'static str {
    match error {
        PermissionError::InvalidPath => "invalid-path",
        PermissionError::OutsideWorkspace => "outside-workspace",
        PermissionError::SymlinkNotAllowed => "symlink-not-allowed",
        PermissionError::UnapprovedExecutable => "unapproved-executable",
        PermissionError::NetworkAddressBlocked => "network-address-blocked",
        PermissionError::Io(_) => "filesystem-error",
    }
}

#[allow(dead_code)]
fn capability_risk(capability: &AgentCapability) -> RiskClass {
    if capability.is_always_denied() {
        RiskClass::SecuritySensitive
    } else if capability.is_observation() {
        RiskClass::Observation
    } else if matches!(capability, AgentCapability::FileWrite { .. }) {
        RiskClass::WorkspaceWrite
    } else if matches!(capability, AgentCapability::FileDelete { .. }) {
        RiskClass::Destructive
    } else {
        RiskClass::ExternalWrite
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use chrono::{Duration, Utc};
    use tempfile::tempdir;
    use uuid::Uuid;

    use super::{
        AgentActionExecutor, AgentService, ApprovalDecision, ApprovalRequest, ApprovalStatus,
        AuthorizationOutcome, BrokerError, ExecutionOutcome, PermissionBroker, RiskClass,
        POLICY_VERSION,
    };
    use crate::{
        agent::model::{
            AgentCapability, AgentPermissionMode, CapabilityKind, CapabilityRequest,
            ProfileBoundary, TaskContext, TaskLifecycle,
        },
        agent_store::AgentStore,
        storage::app_paths::AppPaths,
    };

    fn fixture() -> (tempfile::TempDir, Arc<AgentStore>, Uuid, String) {
        let root = tempdir().unwrap();
        let paths = AppPaths::from_roots(root.path().to_path_buf(), root.path().to_path_buf());
        std::fs::create_dir_all(&paths.state).unwrap();
        let store = Arc::new(AgentStore::open(&paths).unwrap());
        let task_id = Uuid::new_v4();
        let agent_id = Uuid::new_v4();
        let generation_id = "generation-a".to_owned();
        let now = Utc::now().to_rfc3339();
        let writer = store.writer().unwrap();
        writer
            .connection()
            .execute(
                "INSERT INTO agents (agent_id, adapter_kind, display_name, status, created_at, updated_at)
                 VALUES (?1, 'mock', 'Mock Agent', 'active', ?2, ?2)",
                rusqlite::params![agent_id.to_string(), now],
            )
            .unwrap();
        writer
            .connection()
            .execute(
                "INSERT INTO tasks (task_id, agent_id, workspace_path, permission_mode, status, created_at, updated_at)
                 VALUES (?1, ?2, '/workspace', 'request-approval', 'active', ?3, ?3)",
                rusqlite::params![task_id.to_string(), agent_id.to_string(), now],
            )
            .unwrap();
        writer
            .connection()
            .execute(
                "INSERT INTO worker_sessions (task_id, worker_session_id, adapter_kind, generation_id, updated_at)
                 VALUES (?1, ?2, 'mock', ?3, ?4)",
                rusqlite::params![task_id.to_string(), Uuid::new_v4().to_string(), generation_id, now],
            )
            .unwrap();
        (root, store, task_id, generation_id)
    }

    fn request(task_id: Uuid, generation_id: &str) -> ApprovalRequest {
        let requested_at = Utc::now();
        ApprovalRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id: generation_id.to_owned(),
            capability_kind: CapabilityKind::FileWrite,
            canonical_scope: "/workspace/notes.md".to_owned(),
            risk_class: RiskClass::WorkspaceWrite,
            policy_version: POLICY_VERSION.to_owned(),
            requested_at,
            expires_at: requested_at + Duration::minutes(5),
        }
    }

    fn capability() -> super::AgentCapability {
        super::AgentCapability::FileWrite {
            path: "/workspace/notes.md".into(),
        }
    }

    struct RecordingExecutor {
        calls: usize,
        error: Option<String>,
    }

    impl AgentActionExecutor for RecordingExecutor {
        fn execute(&mut self, _capability: &super::AgentCapability) -> Result<(), String> {
            self.calls += 1;
            match self.error.take() {
                Some(error) => Err(error),
                None => Ok(()),
            }
        }
    }

    #[test]
    fn persists_pending_approval_and_survives_service_restart() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store.clone());
        let created = broker
            .request(&capability(), request(task_id, &generation_id))
            .unwrap();
        assert_eq!(created.status, ApprovalStatus::Pending);

        let restarted = PermissionBroker::new(store);
        let pending = restarted.pending(task_id, &generation_id).unwrap();
        assert_eq!(pending, vec![created]);
    }

    #[test]
    fn pending_approvals_require_the_active_task_generation() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store);
        assert!(matches!(
            broker.pending(task_id, "stale-generation"),
            Err(BrokerError::StaleGeneration)
        ));
    }

    #[test]
    fn duplicate_request_and_stale_generation_are_rejected() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store);
        let first_request = request(task_id, &generation_id);
        broker
            .request(&capability(), first_request.clone())
            .unwrap();
        assert!(matches!(
            broker.request(&capability(), first_request),
            Err(super::BrokerError::DuplicateRequest)
        ));
        assert!(matches!(
            broker.request(&capability(), request(task_id, "stale-generation")),
            Err(super::BrokerError::StaleGeneration)
        ));
    }

    #[test]
    fn two_responses_race_and_allow_once_can_be_consumed_exactly_once() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store);
        let created = broker
            .request(&capability(), request(task_id, &generation_id))
            .unwrap();
        let resolved = broker
            .resolve(
                created.approval_id,
                task_id,
                &generation_id,
                ApprovalDecision::AllowOnce,
                Utc::now(),
            )
            .unwrap();
        assert_eq!(resolved.status, ApprovalStatus::ApprovedOnce);
        assert!(matches!(
            broker.resolve(
                created.approval_id,
                task_id,
                &generation_id,
                ApprovalDecision::Deny,
                Utc::now()
            ),
            Err(super::BrokerError::AlreadyResolved)
        ));
        broker
            .consume_once(
                created.approval_id,
                task_id,
                &generation_id,
                &capability(),
                POLICY_VERSION,
                Utc::now(),
            )
            .unwrap();
        assert!(matches!(
            broker.consume_once(
                created.approval_id,
                task_id,
                &generation_id,
                &capability(),
                POLICY_VERSION,
                Utc::now() + Duration::seconds(1)
            ),
            Err(super::BrokerError::AlreadyResolved)
        ));
    }

    #[test]
    fn approval_and_consume_are_bound_to_the_typed_capability_summary() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store);
        let approval_request = request(task_id, &generation_id);
        assert!(matches!(
            broker.request(
                &super::AgentCapability::FileWrite {
                    path: "/workspace/other.md".into(),
                },
                approval_request.clone(),
            ),
            Err(super::BrokerError::CapabilityMismatch)
        ));
        let created = broker.request(&capability(), approval_request).unwrap();
        broker
            .resolve(
                created.approval_id,
                task_id,
                &generation_id,
                ApprovalDecision::AllowOnce,
                Utc::now(),
            )
            .unwrap();
        assert!(matches!(
            broker.consume_once(
                created.approval_id,
                task_id,
                &generation_id,
                &super::AgentCapability::FileWrite {
                    path: "/workspace/other.md".into(),
                },
                POLICY_VERSION,
                Utc::now(),
            ),
            Err(super::BrokerError::CapabilityMismatch)
        ));
    }

    #[test]
    fn durable_audit_rows_use_the_shared_redaction_boundary() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store.clone());
        let capability = super::AgentCapability::ExternalWrite {
            service: "http".to_owned(),
            action: "POST".to_owned(),
            target: "https://api.example.com/deploy".to_owned(),
        };
        let created = broker
            .request(
                &capability,
                ApprovalRequest {
                    request_id: Uuid::new_v4(),
                    task_id,
                    generation_id: generation_id.clone(),
                    capability_kind: capability.kind(),
                    canonical_scope: capability.canonical_scope(),
                    risk_class: capability.risk_class(),
                    policy_version: POLICY_VERSION.to_owned(),
                    requested_at: Utc::now(),
                    expires_at: Utc::now() + Duration::minutes(5),
                },
            )
            .unwrap();
        assert_eq!(created.status, ApprovalStatus::Pending);
        let connection = store.reader().unwrap();
        let scopes = connection
            .prepare("SELECT canonical_scope FROM audit_summaries WHERE task_id = ?1")
            .unwrap()
            .query_map([task_id.to_string()], |row| row.get::<_, Option<String>>(0))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        assert!(scopes
            .iter()
            .flatten()
            .all(|scope| scope.contains("https://api.example.com/deploy")));
    }

    #[test]
    fn automatic_authorization_decisions_are_durably_audited() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store.clone());
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id,
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::FileRead {
                path: root.path().join("README.md"),
            },
            disclosed: true,
        };
        assert!(matches!(
            service.authorize(&context, &request).unwrap(),
            AuthorizationOutcome::Allowed { .. }
        ));
        let connection = store.reader().unwrap();
        let (event_kind, result): (String, String) = connection
            .query_row(
                "SELECT event_kind, result_category FROM audit_summaries
                 WHERE task_id = ?1 AND request_id = ?2
                 ORDER BY occurred_at DESC LIMIT 1",
                rusqlite::params![task_id.to_string(), request.request_id.to_string()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(event_kind, "authorization-decision");
        assert_eq!(result, "allowed");
    }

    #[test]
    fn execution_gateway_blocks_side_effects_until_approval_and_consumes_once() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store);
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id,
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::FileWrite {
                path: root.path().join("notes.md"),
            },
            disclosed: true,
        };
        let mut executor = RecordingExecutor {
            calls: 0,
            error: None,
        };
        let first = service.execute(&context, &request, &mut executor).unwrap();
        let approval = match first {
            ExecutionOutcome::ApprovalRequired { approval } => approval,
            other => panic!("expected approval, got {other:?}"),
        };
        assert_eq!(executor.calls, 0);
        service
            .resolve(
                approval.approval_id,
                task_id,
                &context.generation_id,
                ApprovalDecision::AllowOnce,
                now,
            )
            .unwrap();
        assert!(matches!(
            service.execute(&context, &request, &mut executor).unwrap(),
            ExecutionOutcome::Completed
        ));
        assert_eq!(executor.calls, 1);
        assert!(matches!(
            service.execute(&context, &request, &mut executor).unwrap(),
            ExecutionOutcome::Denied { .. }
        ));
        assert_eq!(executor.calls, 1);
    }

    #[test]
    fn execution_gateway_rechecks_resolved_network_addresses() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store);
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id,
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::Network {
                host: "127.0.0.1".to_owned(),
                port: 80,
                operation: crate::agent::model::NetworkOperation::Read,
            },
            disclosed: true,
        };
        let mut executor = RecordingExecutor {
            calls: 0,
            error: None,
        };
        let approval = match service.execute(&context, &request, &mut executor).unwrap() {
            ExecutionOutcome::ApprovalRequired { approval } => approval,
            other => panic!("expected approval, got {other:?}"),
        };
        service
            .resolve(
                approval.approval_id,
                task_id,
                &context.generation_id,
                ApprovalDecision::AllowOnce,
                now,
            )
            .unwrap();
        assert!(matches!(
            service.execute(&context, &request, &mut executor).unwrap(),
            ExecutionOutcome::Denied { .. }
        ));
        assert_eq!(executor.calls, 0);
    }

    #[test]
    fn execution_failure_is_audited_with_a_redacted_error_code() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store.clone());
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id,
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::FileRead {
                path: root.path().join("README.md"),
            },
            disclosed: true,
        };
        let mut executor = RecordingExecutor {
            calls: 0,
            error: Some("api-key=super-secret".to_owned()),
        };
        assert!(matches!(
            service.execute(&context, &request, &mut executor),
            Err(BrokerError::ExecutionFailed)
        ));
        let connection = store.reader().unwrap();
        let error_code: String = connection
            .query_row(
                "SELECT error_code FROM audit_summaries
                 WHERE task_id = ?1 AND event_kind = 'execution-failed'",
                [task_id.to_string()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(error_code, "sensitive-error");
    }

    #[test]
    fn expired_pending_approval_is_cancelled_and_cannot_be_resolved() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store.clone());
        let created = broker
            .request(&capability(), request(task_id, &generation_id))
            .unwrap();
        let connection = broker.store.writer().unwrap();
        connection
            .connection()
            .execute(
                "UPDATE approvals SET expires_at = '2020-01-01T00:00:00Z' WHERE approval_id = ?1",
                [created.approval_id.to_string()],
            )
            .unwrap();
        drop(connection);
        assert!(matches!(
            broker.resolve(
                created.approval_id,
                task_id,
                &generation_id,
                ApprovalDecision::AllowForTask,
                Utc::now(),
            ),
            Err(BrokerError::Expired)
        ));
        let connection = broker.store.reader().unwrap();
        let status: String = connection
            .query_row(
                "SELECT status FROM approvals WHERE approval_id = ?1",
                [created.approval_id.to_string()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(status, "cancelled");
    }

    #[test]
    fn expired_or_finished_task_grants_cannot_reenter_through_duplicate_approval() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store.clone());
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let capability = AgentCapability::ExternalWrite {
            service: "http".to_owned(),
            action: "POST".to_owned(),
            target: "https://api.example.com/deploy".to_owned(),
        };
        let capability_request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id: generation_id.clone(),
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability,
            disclosed: true,
        };
        let approval = match service.authorize(&context, &capability_request).unwrap() {
            AuthorizationOutcome::ApprovalRequired { approval, .. } => approval,
            other => panic!("expected approval, got {other:?}"),
        };
        service
            .resolve(
                approval.approval_id,
                task_id,
                &generation_id,
                ApprovalDecision::AllowForTask,
                now,
            )
            .unwrap();
        let writer = store.writer().unwrap();
        writer
            .connection()
            .execute(
                "UPDATE task_grants SET expires_at = '1970-01-01T00:00:00Z' WHERE task_id = ?1",
                [task_id.to_string()],
            )
            .unwrap();
        drop(writer);

        assert!(matches!(
            service.authorize(&context, &capability_request),
            Ok(AuthorizationOutcome::Denied { .. })
        ));

        let writer = store.writer().unwrap();
        writer
            .connection()
            .execute(
                "UPDATE tasks SET status = 'completed' WHERE task_id = ?1",
                [task_id.to_string()],
            )
            .unwrap();
        drop(writer);
        assert!(matches!(
            service.authorize(&context, &capability_request),
            Err(BrokerError::StaleGeneration) | Ok(AuthorizationOutcome::Denied { .. })
        ));
    }

    #[test]
    fn permission_rejection_is_durably_audited_without_raw_path_or_error() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store.clone());
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::FullAccess,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: vec![CapabilityKind::ProcessLaunch],
            explicit_full_access: true,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id,
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::ProcessLaunch {
                executable: root.path().join("/tmp/secret-token-binary"),
                cwd: root.path().to_path_buf(),
            },
            disclosed: true,
        };
        assert!(matches!(
            service.authorize(&context, &request),
            Ok(AuthorizationOutcome::Denied { .. })
        ));
        let connection = store.reader().unwrap();
        let (event_kind, error_code): (String, String) = connection
            .query_row(
                "SELECT event_kind, error_code FROM audit_summaries
                 WHERE task_id = ?1 AND request_id = ?2
                 ORDER BY occurred_at DESC LIMIT 1",
                rusqlite::params![task_id.to_string(), request.request_id.to_string()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(event_kind, "permission-rejected");
        assert_eq!(error_code, "invalid-path");
    }

    #[test]
    fn agent_service_is_the_runtime_authorization_entrypoint() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store.clone());
        let workspace = root.path().to_path_buf();
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: workspace.clone(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id,
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::FileWrite {
                path: workspace.join("notes.md"),
            },
            disclosed: true,
        };
        let outcome = service.authorize(&context, &request).unwrap();
        assert!(matches!(
            outcome,
            AuthorizationOutcome::ApprovalRequired { .. }
        ));
        assert_eq!(
            service
                .pending(task_id, &context.generation_id)
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn reusing_a_request_id_for_a_different_capability_fails_closed() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store);
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request_id = Uuid::new_v4();
        let first = CapabilityRequest {
            request_id,
            task_id,
            generation_id: generation_id.clone(),
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::FileWrite {
                path: root.path().join("notes.md"),
            },
            disclosed: true,
        };
        service.authorize(&context, &first).unwrap();
        let mut reused = first.clone();
        reused.capability = AgentCapability::FileWrite {
            path: root.path().join("other.md"),
        };
        assert!(matches!(
            service.authorize(&context, &reused),
            Err(BrokerError::CapabilityMismatch)
        ));
    }

    #[test]
    fn retrying_an_approved_once_request_returns_the_existing_approval() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store);
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id,
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::FileWrite {
                path: root.path().join("notes.md"),
            },
            disclosed: true,
        };
        let approval = match service.authorize(&context, &request).unwrap() {
            AuthorizationOutcome::ApprovalRequired { approval, .. } => approval,
            other => panic!("expected approval, got {other:?}"),
        };
        service
            .resolve(
                approval.approval_id,
                task_id,
                &context.generation_id,
                ApprovalDecision::AllowOnce,
                now,
            )
            .unwrap();
        let retry = service.authorize(&context, &request).unwrap();
        assert!(matches!(
            retry,
            AuthorizationOutcome::Approved { approval: existing, .. }
                if existing.approval_id == approval.approval_id
        ));
    }

    #[test]
    fn consuming_an_approval_revalidates_request_lifecycle() {
        let (root, store, task_id, generation_id) = fixture();
        let service = AgentService::new(store);
        let now = Utc::now();
        let context = TaskContext {
            task_id,
            generation_id: generation_id.clone(),
            workspace_root: root.path().to_path_buf(),
            permission_mode: AgentPermissionMode::RequestApproval,
            profile_boundary: ProfileBoundary::WorkspaceWrite,
            lifecycle: TaskLifecycle::Active,
            now,
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        };
        let request = CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id,
            generation_id,
            issued_at: now,
            expires_at: now + Duration::minutes(5),
            capability: AgentCapability::FileWrite {
                path: root.path().join("notes.md"),
            },
            disclosed: true,
        };
        let approval = match service.authorize(&context, &request).unwrap() {
            AuthorizationOutcome::ApprovalRequired { approval, .. } => approval,
            other => panic!("expected approval, got {other:?}"),
        };
        service
            .resolve(
                approval.approval_id,
                task_id,
                &context.generation_id,
                ApprovalDecision::AllowOnce,
                now,
            )
            .unwrap();
        let mut expired = request;
        expired.expires_at = now - Duration::seconds(1);
        assert!(matches!(
            service.consume_once(&context, &expired, approval.approval_id, now),
            Err(BrokerError::CapabilityDenied)
        ));
    }

    #[test]
    fn stale_generation_cannot_finalize_the_current_task() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store);
        assert!(matches!(
            broker.cancel_task(task_id, "stale-generation", Utc::now()),
            Err(BrokerError::StaleGeneration)
        ));
        let connection = broker.store.reader().unwrap();
        let status: String = connection
            .query_row(
                "SELECT status FROM tasks WHERE task_id = ?1",
                [task_id.to_string()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(status, "active");
        assert_eq!(generation_id, "generation-a");
    }

    #[test]
    fn allow_for_task_is_revoked_when_task_is_cancelled() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store);
        let created = broker
            .request(&capability(), request(task_id, &generation_id))
            .unwrap();
        broker
            .resolve(
                created.approval_id,
                task_id,
                &generation_id,
                ApprovalDecision::AllowForTask,
                Utc::now(),
            )
            .unwrap();
        broker
            .cancel_task(task_id, &generation_id, Utc::now())
            .unwrap();
        let connection = broker.store.reader().unwrap();
        let status: String = connection
            .query_row(
                "SELECT status FROM approvals WHERE approval_id = ?1",
                [created.approval_id.to_string()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(status, "cancelled");
        let task_status: String = connection
            .query_row(
                "SELECT status FROM tasks WHERE task_id = ?1",
                [task_id.to_string()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(task_status, "cancelled");
        let cancellation_audit: String = connection
            .query_row(
                "SELECT event_kind FROM audit_summaries
                 WHERE task_id = ?1 AND generation_id = ?2
                 ORDER BY occurred_at DESC LIMIT 1",
                rusqlite::params![task_id.to_string(), generation_id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(cancellation_audit, "task-cancelled");
    }

    #[test]
    fn task_completion_expires_grants_and_cancels_unconsumed_approvals() {
        let (_root, store, task_id, generation_id) = fixture();
        let broker = PermissionBroker::new(store);
        let created = broker
            .request(&capability(), request(task_id, &generation_id))
            .unwrap();
        broker
            .resolve(
                created.approval_id,
                task_id,
                &generation_id,
                ApprovalDecision::AllowForTask,
                Utc::now(),
            )
            .unwrap();
        broker
            .complete_task(task_id, &generation_id, Utc::now())
            .unwrap();

        let connection = broker.store.reader().unwrap();
        let task_status: String = connection
            .query_row(
                "SELECT status FROM tasks WHERE task_id = ?1",
                [task_id.to_string()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(task_status, "completed");
        let (approval_status, grant_expiry): (String, Option<String>) = connection
            .query_row(
                "SELECT approvals.status, task_grants.expires_at
                 FROM approvals JOIN task_grants ON task_grants.task_id = approvals.task_id
                 WHERE approvals.approval_id = ?1",
                [created.approval_id.to_string()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(approval_status, "cancelled");
        assert!(grant_expiry.is_some());
    }
}
