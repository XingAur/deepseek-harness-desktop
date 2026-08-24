import type {
  RuntimeSessionContractFailureCategory,
  RuntimeSessionContractStage,
  RuntimeSessionContractStageResult,
} from './runtime-session-contract.mjs'

export interface RuntimeSessionContractCommandOptions {
  runtimeRoot: string
  reportPath: string
  runtimeVersion: string
  timeoutMs: number
}

export interface CandidateRuntimeLayout {
  root: string
  nodeExecutable: string
  appDirectory: string
  launcher: string
}

export interface RuntimeSessionContractReport {
  schemaVersion: 1
  runtimeVersion: string
  platform: string
  ok: boolean
  durationMs: number
  providerRequestObserved?: boolean
  failedStage?: RuntimeSessionContractStage
  category?: RuntimeSessionContractFailureCategory
  processExitCode?: number
  cleanupFailure?: { category: 'cleanup-failed' }
  stages: RuntimeSessionContractStageResult[]
}

export interface CandidateRuntimeLifecycle {
  start(): Promise<void>
  ready(): Promise<void>
  cleanup(): Promise<void>
  processExitCode(): number | undefined
}

export function parseRuntimeSessionContractArgs(values: string[]): Readonly<RuntimeSessionContractCommandOptions>
export function resolveCandidateRuntimeLayout(runtimeRoot: string): Readonly<CandidateRuntimeLayout>
export function sanitizeRuntimeSessionContractReport(
  result: {
    ok: boolean
    durationMs: number
    failedStage?: RuntimeSessionContractStage
    category?: RuntimeSessionContractFailureCategory
    cleanupFailure?: { category: 'cleanup-failed' }
    stages: Array<RuntimeSessionContractStageResult & Record<string, unknown>>
  },
  metadata: {
    runtimeVersion: string
    processExitCode?: number
    providerRequestObserved?: boolean
  },
): RuntimeSessionContractReport
export function runRuntimeSessionContractCommand(
  options: RuntimeSessionContractCommandOptions,
): Promise<RuntimeSessionContractReport>
export function createCandidateRuntimeLifecycle(options: {
  layout: CandidateRuntimeLayout
  port: number
  healthTimeoutMs: number
  environment: NodeJS.ProcessEnv
}): Readonly<CandidateRuntimeLifecycle>
