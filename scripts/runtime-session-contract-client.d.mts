import type { RuntimeSessionContractDriver } from './runtime-session-contract.mjs'

export interface CandidateSessionOperationsOptions {
  sessions: {
    binding(sessionId: string): { sessionId: string; session: CandidateSessionFace } | undefined
    open(sessionId: string): void
    clear(): void
  }
  workspaces: {
    create(input: { path: string }): Promise<{ workspaceId: string }>
    connectWorkspace(workspaceId: string): Promise<string>
  }
  workspacePath: string
  promptMarker: string
  replyMarker: string
  eventTimeoutMs?: number
}

export interface CandidateSessionFace {
  prompt(content: Array<{ type: 'text'; text: string }>, mode: 'queue'): Promise<{
    ok: boolean
    value?: { accepted: true }
  }>
  getSnapshot(): unknown
  subscribe(listener: () => void): () => void
}

export interface CandidateRuntimeLifecycle {
  start(): Promise<void>
  ready(): Promise<void>
  cleanup(): Promise<void>
}

export interface CandidateSessionDriverOptions {
  appDirectory: string
  origin: string
  workspacePath: string
  promptMarker: string
  replyMarker: string
  eventTimeoutMs?: number
  lifecycle?: CandidateRuntimeLifecycle
}

export interface CandidateSessionOperations {
  createWorkspace(): Promise<string>
  createSession(workspaceId: string): Promise<string>
  requireBinding(sessionId: string): Promise<void>
  prompt(sessionId: string): Promise<void>
  open(sessionId: string): Promise<void>
  waitForEvents(sessionId: string): Promise<void>
  closeSession(sessionId: string): Promise<void>
}

export interface CandidateClientPaths {
  cordis: string
  connection: string
  typert: string
  gateway: string
  remotes: string
  runtime: string
}

export function resolveCandidateClientPaths(appDirectory: string): Readonly<CandidateClientPaths>
export function createCandidateSessionOperations(options: CandidateSessionOperationsOptions): Readonly<CandidateSessionOperations>
export function createCandidateSessionDriver(options: CandidateSessionDriverOptions): RuntimeSessionContractDriver
export function loadCandidateClientModules(appDirectory: string): Promise<Readonly<Record<string, unknown>>>
export function installClientGlobals(origin: string, appDirectory: string): () => void
