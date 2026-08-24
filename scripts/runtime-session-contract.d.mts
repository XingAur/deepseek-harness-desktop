export type RuntimeSessionContractStage =
  | 'runtime-start'
  | 'runtime-ready'
  | 'workspace-create'
  | 'session-create'
  | 'session-binding'
  | 'session-prompt'
  | 'session-open'
  | 'session-event'
  | 'session-close'
  | 'cleanup'

export type RuntimeSessionContractFailureCategory =
  | 'timeout'
  | 'process-exited'
  | 'protocol-mismatch'
  | 'binding-missing'
  | 'event-missing'
  | 'cleanup-failed'
  | 'internal'

export interface RuntimeSessionContractDriver {
  start(): Promise<void>
  ready(): Promise<void>
  createWorkspace(): Promise<string>
  createSession(workspaceId: string): Promise<string>
  requireBinding(sessionId: string): Promise<void>
  prompt(sessionId: string): Promise<void>
  open(sessionId: string): Promise<void>
  waitForEvents(sessionId: string): Promise<void>
  closeSession(sessionId: string): Promise<void>
  cleanup(): Promise<void>
}

export interface RuntimeSessionContractStageResult {
  stage: RuntimeSessionContractStage
  ok: boolean
  durationMs: number
  category?: RuntimeSessionContractFailureCategory
}

export interface RuntimeSessionContractSuccess {
  ok: true
  durationMs: number
  stages: RuntimeSessionContractStageResult[]
}

export interface RuntimeSessionContractFailure {
  ok: false
  durationMs: number
  stages: RuntimeSessionContractStageResult[]
  failedStage: RuntimeSessionContractStage
  category: RuntimeSessionContractFailureCategory
  cleanupFailure?: { category: 'cleanup-failed' }
}

export const CONTRACT_STAGES: readonly RuntimeSessionContractStage[]
export const FAILURE_CATEGORIES: readonly RuntimeSessionContractFailureCategory[]

export class RuntimeSessionContractError extends Error {
  readonly category: RuntimeSessionContractFailureCategory
  constructor(category: RuntimeSessionContractFailureCategory, message: string, options?: ErrorOptions)
}

export function runRuntimeSessionContract(
  driver: RuntimeSessionContractDriver,
  options?: { timeoutMs?: number },
): Promise<RuntimeSessionContractSuccess | RuntimeSessionContractFailure>
