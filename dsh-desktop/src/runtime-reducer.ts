import type { BootstrapReply, RuntimeEvent, RuntimeFailure, RuntimePhase } from './runtime-contract'

export interface RuntimeViewState {
  phase: RuntimePhase
  operationId: string | null
  progress: { completed: number; total: number | null } | null
  message: string
  error: RuntimeFailure | null
  diagnosticPath: string | null
}

export type RuntimeAction =
  | { type: 'bootstrap-started'; reply: BootstrapReply }
  | { type: 'runtime-event'; event: RuntimeEvent }
  | { type: 'request-failed'; error: RuntimeFailure }
  | { type: 'diagnostics-exported'; path: string }
  | { type: 'clear-diagnostics' }

export const initialRuntimeState: RuntimeViewState = {
  phase: 'checking',
  operationId: null,
  progress: null,
  message: '正在检查运行环境…',
  error: null,
  diagnosticPath: null,
}

export function runtimeReducer(state: RuntimeViewState, action: RuntimeAction): RuntimeViewState {
  if (action.type === 'bootstrap-started') {
    return {
      ...state,
      phase: action.reply.phase,
      operationId: action.reply.operationId,
      error: null,
      diagnosticPath: null,
    }
  }
  if (action.type === 'request-failed') {
    return { ...state, phase: 'failed', error: action.error, message: action.error.message }
  }
  if (action.type === 'diagnostics-exported') return { ...state, diagnosticPath: action.path }
  if (action.type === 'clear-diagnostics') return { ...state, diagnosticPath: null }

  const envelope = action.event
  const eventOperationId = envelope.kind === 'failure' ? envelope.operationId : envelope.payload.operationId
  if (state.operationId !== null && eventOperationId !== state.operationId) {
    return state
  }
  if (envelope.kind === 'failure') {
    return {
      ...state,
      operationId: envelope.operationId,
      phase: 'failed',
      message: envelope.payload.message,
      error: envelope.payload,
    }
  }
  const progress = envelope.payload
  return {
    ...state,
    operationId: progress.operationId,
    phase: progress.phase,
    progress: { completed: progress.completed, total: progress.total },
    message: progress.message,
    error: null,
  }
}

export function failureFromUnknown(cause: unknown): RuntimeFailure {
  if (typeof cause === 'object' && cause !== null) {
    const candidate = cause as Partial<RuntimeFailure>
    if (typeof candidate.message === 'string') {
      return {
        code: candidate.code ?? 'internal',
        message: candidate.message,
        recoverable: candidate.recoverable ?? true,
      }
    }
  }
  return { code: 'internal', message: cause instanceof Error ? cause.message : String(cause), recoverable: true }
}
