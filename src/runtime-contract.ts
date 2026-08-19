export type RuntimePhase =
  | 'checking'
  | 'fetching-manifest'
  | 'downloading'
  | 'verifying'
  | 'activating'
  | 'starting'
  | 'ready'
  | 'cancelled'
  | 'failed'

export type RuntimeFailureCode =
  | 'network'
  | 'signature'
  | 'archive'
  | 'process'
  | 'health-timeout'
  | 'cancelled'
  | 'internal'

export interface RuntimeProgressEvent {
  operationId: string
  phase: RuntimePhase
  completed: number
  total: number | null
  message: string
}

export interface RuntimeFailure {
  code: RuntimeFailureCode
  message: string
  recoverable: boolean
}

export interface BootstrapReply {
  operationId: string
  phase: RuntimePhase
  rendererUrl: string | null
}

export interface RuntimeProgressEnvelope {
  kind: 'progress'
  payload: RuntimeProgressEvent
}

export interface RuntimeFailureEnvelope {
  kind: 'failure'
  operationId: string
  payload: RuntimeFailure
}

export interface RuntimeReadyEnvelope {
  kind: 'ready'
  operationId: string
  rendererUrl: string
}

export type RuntimeEvent = RuntimeProgressEnvelope | RuntimeReadyEnvelope | RuntimeFailureEnvelope

export interface RuntimeClient {
  bootstrapRuntime(): Promise<BootstrapReply>
  cancelRuntime(): Promise<void>
  repairRuntime(): Promise<BootstrapReply>
  exportDiagnostics(): Promise<string>
  subscribeRuntimeProgress(listener: (event: RuntimeEvent) => void): Promise<() => void>
}
