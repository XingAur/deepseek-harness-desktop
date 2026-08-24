#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StoredTaskStatus {
    Queued,
    Running,
    WaitingApproval,
    Completed,
    Cancelled,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RecoveryStatus {
    Queued,
    Running,
    WaitingApproval,
    Completed,
    Cancelled,
    Failed,
    Recoverable,
    NeedsReview,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ExternalResult {
    None,
    Confirmed,
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RecoveryInput {
    pub stored_status: StoredTaskStatus,
    pub worker_alive: bool,
    pub worker_identity_known_dead: bool,
    pub pending_approval: bool,
    pub last_acknowledged_sequence: u64,
    pub observed_sequence: u64,
    pub external_result: ExternalResult,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RecoveryDecision {
    pub status: RecoveryStatus,
    pub can_start_replacement: bool,
    pub replay_external_operation: bool,
}

pub fn reconcile(input: &RecoveryInput) -> RecoveryDecision {
    if input.external_result == ExternalResult::Unknown {
        return decision(RecoveryStatus::NeedsReview, false);
    }
    let terminal = match input.stored_status {
        StoredTaskStatus::Completed => Some(RecoveryStatus::Completed),
        StoredTaskStatus::Cancelled => Some(RecoveryStatus::Cancelled),
        StoredTaskStatus::Failed => Some(RecoveryStatus::Failed),
        _ => None,
    };
    if let Some(status) = terminal {
        return decision(status, false);
    }
    if input.pending_approval {
        return decision(RecoveryStatus::WaitingApproval, false);
    }
    if input.observed_sequence > input.last_acknowledged_sequence.saturating_add(1) {
        return decision(RecoveryStatus::NeedsReview, false);
    }
    if input.worker_alive || !input.worker_identity_known_dead {
        return decision(RecoveryStatus::Running, false);
    }
    if input.stored_status == StoredTaskStatus::Queued {
        return decision(RecoveryStatus::Queued, true);
    }
    decision(RecoveryStatus::Recoverable, true)
}

fn decision(status: RecoveryStatus, can_start_replacement: bool) -> RecoveryDecision {
    RecoveryDecision {
        status,
        can_start_replacement,
        replay_external_operation: false,
    }
}

#[cfg(test)]
mod tests {
    use super::{reconcile, ExternalResult, RecoveryInput, RecoveryStatus, StoredTaskStatus};

    fn base() -> RecoveryInput {
        RecoveryInput {
            stored_status: StoredTaskStatus::Running,
            worker_alive: false,
            worker_identity_known_dead: true,
            pending_approval: false,
            last_acknowledged_sequence: 4,
            observed_sequence: 4,
            external_result: ExternalResult::None,
        }
    }

    #[test]
    fn terminal_and_interrupted_states_are_deterministic() {
        let mut completed = base();
        completed.stored_status = StoredTaskStatus::Completed;
        assert_eq!(reconcile(&completed).status, RecoveryStatus::Completed);
        assert_eq!(reconcile(&base()).status, RecoveryStatus::Recoverable);
    }

    #[test]
    fn old_worker_or_unknown_external_result_blocks_replay() {
        let mut old_worker = base();
        old_worker.worker_identity_known_dead = false;
        assert!(!reconcile(&old_worker).can_start_replacement);
        let mut unknown = base();
        unknown.external_result = ExternalResult::Unknown;
        assert_eq!(reconcile(&unknown).status, RecoveryStatus::NeedsReview);
        assert!(!reconcile(&unknown).replay_external_operation);
    }

    #[test]
    fn approval_and_event_gap_require_reconciliation() {
        let mut approval = base();
        approval.pending_approval = true;
        assert_eq!(reconcile(&approval).status, RecoveryStatus::WaitingApproval);
        let mut gap = base();
        gap.observed_sequence = 7;
        assert_eq!(reconcile(&gap).status, RecoveryStatus::NeedsReview);
    }
}
