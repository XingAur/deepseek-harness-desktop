/** Wire types shared by the mobile page components; mirrors mobile-api.ts. */

export interface SessionRow {
  readonly id: string
  readonly title: string | null
  readonly running: boolean
  readonly updatedAt: number
  readonly cwd: string | null
}

export interface QuestionOption {
  readonly label: string
  readonly description?: string
}

export interface MobileQuestion {
  readonly id: string
  readonly question: string
  readonly detail?: string
  readonly header?: string
  readonly options?: readonly QuestionOption[]
  readonly multiSelect?: boolean
  readonly intent?: { readonly kind: 'plan-review'; readonly approve: string }
}

/** One pending approval or structured question the phone can act on. */
export interface InterruptionRecord {
  readonly kind: 'approval' | 'question'
  readonly key: string
  readonly sessionId: string | null
  readonly toolName: string | null
  readonly callId: string | null
  readonly reason: string | null
  readonly questions: readonly MobileQuestion[] | null
  readonly at: number
  readonly delegated: boolean
}

export interface StateResponse {
  readonly sessions: readonly SessionRow[]
  readonly groups: readonly { readonly name: string; readonly sessionIds: readonly string[] }[]
  readonly interruptions: readonly InterruptionRecord[]
  readonly relay: { readonly active: boolean }
}

export interface TodoRow {
  readonly content: string
  readonly status: 'pending' | 'in_progress' | 'completed'
}

export interface DiffSummary {
  readonly path: string
  readonly added: number | null
  readonly removed: number | null
}

export type TranscriptItem =
  | { readonly kind: 'user'; readonly text: string; readonly time: number }
  | { readonly kind: 'assistant'; readonly text: string; readonly time: number }
  | { readonly kind: 'reasoning'; readonly text: string; readonly time: number }
  | {
    readonly kind: 'tool'
    readonly callId: string
    readonly name: string
    readonly title: string
    readonly time: number
    readonly end: number | null
    readonly status: 'running' | 'ok' | 'error'
    readonly diffs: readonly DiffSummary[] | null
  }
  | { readonly kind: 'todo'; readonly todos: readonly TodoRow[]; readonly time: number }

export interface PermissionOption {
  readonly value: string
  readonly name: string
  readonly description?: string
}

export interface PermissionSelect {
  readonly options: readonly PermissionOption[]
  readonly currentValue: string
}

export interface ModelSelectionValue {
  readonly provider: string
  readonly model: string
  readonly reasoningEffort?: string
}

export interface ContextSummary {
  readonly percent: number | null
  readonly usedTokens: number | null
  readonly contextWindow: number | null
}

export interface TokenSummary {
  readonly inputTokens: number
  readonly outputTokens: number
  readonly cacheReadTokens: number
  readonly cacheWriteTokens: number
}

export interface QueueEntry {
  readonly id: string
  readonly placement: string
  readonly preview: string
}

export interface SessionInfo {
  readonly permissions: PermissionSelect | null
  readonly model: ModelSelectionValue | null
  readonly context: ContextSummary | null
  readonly tokens: TokenSummary | null
  readonly queue: readonly QueueEntry[]
}

export interface ReasoningEffort {
  readonly id: string
  readonly name: string
  readonly description?: string
}

export interface CatalogModel {
  readonly id: string
  readonly name: string
  readonly reasoning?: { readonly efforts: readonly ReasoningEffort[]; readonly defaultEffort?: string }
}

export interface ModelCatalog {
  readonly default?: ModelSelectionValue
  readonly routableProviders?: readonly string[]
  readonly groups?: ReadonlyArray<{ readonly id: string; readonly name: string; readonly models: readonly CatalogModel[] }>
  readonly failures?: ReadonlyArray<{ readonly id: string; readonly name: string; readonly message: string }>
}
