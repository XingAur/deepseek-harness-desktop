export type RuntimePhase =
  | 'checking'
  | 'fetching-manifest'
  | 'downloading'
  | 'extracting'
  | 'verifying'
  | 'activating'
  | 'starting'
  | 'ready'
  | 'cancelled'
  | 'failed'

export type GenerationPhase =
  | 'idle'
  | 'resolving-profile'
  | 'preparing-runtime'
  | 'starting'
  | 'probing'
  | 'activating'
  | 'active'
  | 'draining'
  | 'stopped'
  | 'failed'

export type RuntimeFailureCode =
  | 'network'
  | 'signature'
  | 'archive'
  | 'process'
  | 'health-timeout'
  | 'migration-conflict'
  | 'repair-required'
  | 'cancelled'
  | 'internal'

export type RuntimeSourceKind = 'local' | 'bundled' | 'online'

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
  source?: RuntimeSourceKind
  extractionPercent?: number
}

export interface BootstrapReply {
  operationId: string
  generationId?: string
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

export interface ProfileSelection {
  profileId: string
  revision: number
  name?: string
}

export interface GenerationSnapshot {
  generationId: string
  phase: GenerationPhase
  profile: ProfileSelection
  runtimeVersion: string
  rendererUrl: string | null
}

export interface GenerationProgress {
  phase: GenerationPhase
  completed: number
  total: number | null
  message: string
  installedVersion?: string
  requiredVersion?: string
}

export type DesktopEvent =
  | { kind: 'generation-progress'; generationId: string; payload: GenerationProgress }
  | { kind: 'generation-active'; generationId: string; snapshot: GenerationSnapshot }
  | { kind: 'generation-failed'; generationId: string; failure: RuntimeFailure }
  | { kind: 'profile-recovered'; generationId: string; profile: ProfileSelection; reason: string }

export type MigrationStatus =
  | { phase: 'ready' }
  | {
      phase: 'candidate' | 'conflict'
      source: string
      target: string
      bytes: number
      profiles: number
      workspaces: number
    }

export interface AppUpdateInfo {
  version: string
  notes: string | null
  size: number | null
  mode?: 'in-app' | 'manual-dmg'
  downloadUrl?: string | null
  developerIdSigned?: boolean | null
  notarized?: boolean | null
}

export interface AppUpdateFailure {
  code: string
  message: string
  recoverable: boolean
}

export type AppUpdateState =
  | { phase: 'idle' }
  | { phase: 'checking' }
  | { phase: 'available' | 'downloading' | 'ready' | 'installing' | 'restarting'; update: AppUpdateInfo }
  | { phase: 'failed'; update: AppUpdateFailure }

export type AppUpdateSource = 'automatic' | 'manual'

export interface AppUpdateEvent {
  source: AppUpdateSource
  state: AppUpdateState
}

export interface AppUpdateReceipt {
  previousVersion: string
  targetVersion: string
  installedAt: string
}

export interface LocalAppEvent {
  kind: 'launched' | 'stopped' | 'exited'
  workspaceId: string
  origin: string | null
  title: string | null
}

export interface RuntimeClient {
  bootstrapRuntime(): Promise<BootstrapReply>
  cancelRuntime(): Promise<void>
  repairRuntime(): Promise<BootstrapReply>
  exportDiagnostics(): Promise<string>
  migrationStatus(): Promise<MigrationStatus>
  confirmMigration(): Promise<void>
  deferMigration(): Promise<void>
  checkAppUpdate(source: AppUpdateSource): Promise<AppUpdateState>
  downloadAppUpdate(): Promise<AppUpdateState>
  installAppUpdateNow(): Promise<void>
  installAppUpdateOnExit(): Promise<AppUpdateState>
  deferAppUpdate(): Promise<AppUpdateState>
  openAppUpdateDownload(): Promise<void>
  takeAppUpdateReceipt(): Promise<AppUpdateReceipt | null>
  subscribeRuntimeProgress(listener: (event: RuntimeEvent) => void): Promise<() => void>
  subscribeDesktopEvents(listener: (event: DesktopEvent) => void): Promise<() => void>
  subscribeAppUpdates(listener: (event: AppUpdateEvent) => void): Promise<() => void>
  subscribeLocalAppEvents(listener: (event: LocalAppEvent) => void): Promise<() => void>
}
