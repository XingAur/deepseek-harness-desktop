import type { BootstrapReply, DesktopEvent, GenerationPhase, RuntimeEvent, RuntimeFailure, RuntimePhase } from './runtime-contract'

export interface RuntimeViewState {
  phase: RuntimePhase | GenerationPhase
  operationId: string | null
  generationId: string | null
  progress: { completed: number; total: number | null } | null
  message: string
  error: RuntimeFailure | null
  diagnosticPath: string | null
  rendererUrl: string | null
  recoveryNotice: string | null
  versionTransition: string | null
}

export type RuntimeAction =
  | { type: 'bootstrap-started'; reply: BootstrapReply }
  | { type: 'runtime-event'; event: RuntimeEvent }
  | { type: 'desktop-event'; event: DesktopEvent }
  | { type: 'request-failed'; error: RuntimeFailure }
  | { type: 'diagnostics-exported'; path: string }
  | { type: 'clear-diagnostics' }

export const initialRuntimeState: RuntimeViewState = {
  phase: 'checking',
  operationId: null,
  generationId: null,
  progress: null,
  message: '正在检查 DeepSeek Harness…',
  error: null,
  diagnosticPath: null,
  rendererUrl: null,
  recoveryNotice: null,
  versionTransition: null,
}

export function runtimeReducer(state: RuntimeViewState, action: RuntimeAction): RuntimeViewState {
  if (action.type === 'bootstrap-started') {
    return {
      ...state,
      phase: action.reply.phase,
      operationId: action.reply.operationId,
      generationId: action.reply.generationId ?? action.reply.operationId,
      progress: null,
      message: '正在检查 DeepSeek Harness…',
      error: null,
      diagnosticPath: null,
      rendererUrl: action.reply.rendererUrl,
      recoveryNotice: null,
      versionTransition: null,
    }
  }
  if (action.type === 'request-failed') {
    return { ...state, phase: 'failed', error: action.error, message: action.error.message, rendererUrl: null }
  }
  if (action.type === 'diagnostics-exported') return { ...state, diagnosticPath: action.path }
  if (action.type === 'clear-diagnostics') return { ...state, diagnosticPath: null }

  if (action.type === 'desktop-event') {
    const event = action.event
    if (state.generationId !== null && event.generationId !== state.generationId) return state
    if (event.kind === 'generation-failed') {
      return {
        ...state,
        generationId: event.generationId,
        phase: 'failed',
        message: event.failure.message,
        error: event.failure,
        rendererUrl: null,
      }
    }
    if (event.kind === 'generation-active') {
      return {
        ...state,
        generationId: event.generationId,
        phase: 'active',
        progress: { completed: 1, total: 1 },
        message: 'DeepSeek Harness 工作台已准备完成',
        error: null,
        rendererUrl: event.snapshot.rendererUrl,
      }
    }
    if (event.kind === 'profile-recovered') {
      return {
        ...state,
        generationId: event.generationId,
        recoveryNotice: `已恢复到${event.profile.name ?? '上一个可用 Profile'}`,
      }
    }
    return {
      ...state,
      generationId: event.generationId,
      phase: event.payload.phase,
      progress: { completed: event.payload.completed, total: event.payload.total },
      message: event.payload.message,
      error: null,
      versionTransition: event.payload.installedVersion && event.payload.requiredVersion
        ? `Runtime v${event.payload.installedVersion} → v${event.payload.requiredVersion}`
        : state.versionTransition,
    }
  }

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
        source: candidate.source,
        extractionPercent: candidate.extractionPercent,
      }
    }
  }
  return { code: 'internal', message: cause instanceof Error ? cause.message : String(cause), recoverable: true }
}
