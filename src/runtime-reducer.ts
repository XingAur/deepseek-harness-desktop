import type { BootstrapReply, RuntimeEvent, RuntimeFailure, RuntimePhase } from './runtime-contract'

export interface RuntimeViewState {
  phase: RuntimePhase
  operationId: string | null
  progress: { completed: number; total: number | null } | null
  message: string
  error: RuntimeFailure | null
  diagnosticPath: string | null
  rendererUrl: string | null
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
  rendererUrl: null,
}

export function runtimeReducer(state: RuntimeViewState, action: RuntimeAction): RuntimeViewState {
  if (action.type === 'bootstrap-started') {
    return {
      ...state,
      phase: action.reply.phase,
      operationId: action.reply.operationId,
      progress: null,
      error: null,
      diagnosticPath: null,
      rendererUrl: action.reply.rendererUrl,
    }
  }
  if (action.type === 'request-failed') {
    return { ...state, phase: 'failed', error: action.error, message: action.error.message, rendererUrl: null }
  }
  if (action.type === 'diagnostics-exported') return { ...state, diagnosticPath: action.path }
  if (action.type === 'clear-diagnostics') return { ...state, diagnosticPath: null }

  const envelope = action.event
  const eventOperationId = envelope.kind === 'progress' ? envelope.payload.operationId : envelope.operationId
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
      rendererUrl: null,
    }
  }
  if (envelope.kind === 'ready') {
    return {
      ...state,
      operationId: envelope.operationId,
      phase: 'ready',
      progress: { completed: 1, total: 1 },
      message: 'DeepSeek Harness 工作台已准备完成',
      error: null,
      rendererUrl: envelope.rendererUrl,
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
