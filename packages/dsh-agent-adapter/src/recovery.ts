export type StoredTaskStatus = 'queued' | 'running' | 'waiting-approval' | 'completed' | 'cancelled' | 'failed'
export type RecoveryStatus = StoredTaskStatus | 'interrupted' | 'recoverable' | 'needs-review'
export type ExternalResult = 'none' | 'confirmed' | 'unknown'

export interface RecoveryInput {
  storedStatus: StoredTaskStatus
  workerAlive: boolean
  workerIdentityKnownDead: boolean
  pendingApproval: boolean
  lastAcknowledgedSequence: number
  observedSequence: number
  externalResult: ExternalResult
}

export interface RecoveryDecision {
  status: RecoveryStatus
  reason: 'terminal' | 'worker-alive' | 'approval-pending' | 'event-gap' | 'external-result-unknown' | 'worker-interrupted' | 'not-started'
  canStartReplacement: boolean
  replayExternalOperation: false
}

export function reconcileTask(input: RecoveryInput): RecoveryDecision {
  if (input.externalResult === 'unknown') return decision('needs-review', 'external-result-unknown', false)
  if (input.storedStatus === 'completed' || input.storedStatus === 'cancelled' || input.storedStatus === 'failed') return decision(input.storedStatus, 'terminal', false)
  if (input.pendingApproval) return decision('waiting-approval', 'approval-pending', false)
  if (input.observedSequence > input.lastAcknowledgedSequence + 1) return decision('needs-review', 'event-gap', false)
  if (input.workerAlive || !input.workerIdentityKnownDead) return decision('running', 'worker-alive', false)
  if (input.storedStatus === 'queued') return decision('queued', 'not-started', true)
  return decision('recoverable', 'worker-interrupted', true)
}

export class OperationLedger {
  private readonly operations = new Map<string, 'started' | 'acknowledged'>()

  begin(key: string): 'new' | 'duplicate' {
    if (!isSafeKey(key)) throw new Error('idempotency key is invalid')
    if (this.operations.has(key)) return 'duplicate'
    this.operations.set(key, 'started')
    return 'new'
  }

  acknowledge(key: string): boolean {
    if (this.operations.get(key) !== 'started') return false
    this.operations.set(key, 'acknowledged')
    return true
  }
}

function decision(status: RecoveryStatus, reason: RecoveryDecision['reason'], canStartReplacement: boolean): RecoveryDecision {
  return { status, reason, canStartReplacement, replayExternalOperation: false }
}

function isSafeKey(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(value)
}
