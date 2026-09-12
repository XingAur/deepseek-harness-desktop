/**
 * Live control-state cache backing the mobile session-info route.
 *
 * The session controller exposes a host-wide `control()` stream whose first
 * frame carries a complete baseline (per-session queues and projection
 * values) followed by incremental replacement frames. One long-lived
 * subscription here lets the phone poll a cheap in-memory snapshot instead
 * of opening a stream per request.
 */

/** Queued-inbox row the phone renders as an undoable pending prompt. */
export interface MobileQueueItem {
  readonly id: string
  readonly placement: string
  readonly preview: string
}

/** Projection-derived facts the session-info route serves. */
export interface MobileSessionControl {
  readonly queue: readonly MobileQueueItem[]
  readonly permissions: unknown
  readonly modelSelection: unknown
  readonly contextPressure: unknown
  readonly tokenUsage: unknown
}

/** Structural slice of the Host session controller the cache consumes. */
export interface MobileControlControllerFace {
  control: (signal: AbortSignal) => AsyncIterable<unknown>
}

interface CachedSession {
  queue: readonly MobileQueueItem[]
  values: Record<string, unknown>
}

const QUEUE_PREVIEW_CHARS = 160
const MAX_QUEUE_ITEMS = 20

function previewOf(content: unknown): string {
  if (typeof content === 'string') return content.slice(0, QUEUE_PREVIEW_CHARS)
  if (!Array.isArray(content)) return ''
  const parts: string[] = []
  for (const part of content) {
    if (typeof part === 'string') parts.push(part)
    else if (part !== null && typeof part === 'object' && !Array.isArray(part)) {
      const row = part as Record<string, unknown>
      if (typeof row.text === 'string') parts.push(row.text)
    }
    if (parts.join('').length >= QUEUE_PREVIEW_CHARS) break
  }
  return parts.join('').slice(0, QUEUE_PREVIEW_CHARS)
}

function queueItemsOf(value: unknown): readonly MobileQueueItem[] {
  if (!Array.isArray(value)) return []
  const items: MobileQueueItem[] = []
  for (const row of value) {
    if (items.length >= MAX_QUEUE_ITEMS) break
    if (row === null || typeof row !== 'object') continue
    const item = row as Record<string, unknown>
    if (typeof item.id !== 'string') continue
    const message = (item.message ?? null) as Record<string, unknown> | null
    items.push({
      id: item.id,
      placement: typeof item.placement === 'string' ? item.placement : 'queued',
      preview: previewOf(message?.content),
    })
  }
  return items
}

function baselineOf(frame: unknown): { queues: Record<string, readonly MobileQueueItem[]>; sessions: Map<string, CachedSession> } | null {
  const row = frame as Record<string, unknown> | null
  const value = (row?.value ?? null) as Record<string, unknown> | null
  if (row === null || value === null || row.type !== 'baseline') return null
  const queues: Record<string, readonly MobileQueueItem[]> = {}
  const sessions = new Map<string, CachedSession>()
  const queueRows = value.queues
  if (queueRows !== null && typeof queueRows === 'object') {
    for (const [sessionId, items] of Object.entries(queueRows as Record<string, unknown>)) {
      queues[sessionId] = queueItemsOf(items)
    }
  }
  const projectionRows = value.projections
  if (projectionRows !== null && typeof projectionRows === 'object') {
    for (const [sessionId, baseline] of Object.entries(projectionRows as Record<string, unknown>)) {
      const values = (baseline as Record<string, unknown> | null)?.values
      const cached: CachedSession = {
        queue: queues[sessionId] ?? [],
        values: values !== null && typeof values === 'object' ? { ...(values as Record<string, unknown>) } : {},
      }
      sessions.set(sessionId, cached)
      queues[sessionId] = cached.queue
    }
  }
  for (const [sessionId, queue] of Object.entries(queues)) {
    if (!sessions.has(sessionId)) sessions.set(sessionId, { queue, values: {} })
  }
  return { queues, sessions }
}

/**
 * Owns one control-stream subscription; safe to construct per plugin
 * generation and cheap to consult from request handlers.
 */
export class MobileControlCache {
  private sessions = new Map<string, CachedSession>()
  private abort: AbortController | null = null
  private failures = 0

  constructor(private readonly log: (message: string) => void) {}

  /** Ensure the subscription is running; a crashed stream retries lazily. */
  ensure(controller: MobileControlControllerFace | undefined): void {
    if (this.abort !== null || controller === undefined) return
    const abort = new AbortController()
    this.abort = abort
    void (async () => {
      try {
        for await (const frame of controller.control(abort.signal)) {
          const baseline = baselineOf(frame)
          if (baseline !== null) {
            this.sessions = baseline.sessions
            continue
          }
          const row = frame as Record<string, unknown>
          if (row.type === 'queue' && typeof row.sessionId === 'string') {
            const cached = this.sessions.get(row.sessionId)
            const queue = queueItemsOf(row.items)
            this.sessions.set(row.sessionId, { queue, values: cached?.values ?? {} })
          } else if (row.type === 'projection' && typeof row.sessionId === 'string' && typeof row.key === 'string') {
            const cached = this.sessions.get(row.sessionId) ?? { queue: [], values: {} }
            cached.values[row.key] = row.value
          }
        }
      } catch (cause) {
        this.failures += 1
        this.log(`dsh-plugin-desktop: mobile control stream ended (${cause instanceof Error ? cause.message : String(cause)})`)
      } finally {
        // Retry on the next consult; back off briefly after failures.
        this.abort = null
        const delay = Math.min(30_000, 1_000 * 2 ** Math.min(this.failures, 5))
        setTimeout(() => { if (this.abort === null) this.retry(controller) }, delay)
      }
    })()
  }

  private retry(controller: MobileControlControllerFace): void {
    this.ensure(controller)
  }

  /** Stop the subscription (plugin teardown). */
  dispose(): void {
    this.abort?.abort()
    this.abort = null
    this.sessions = new Map()
  }

  /** Phone-facing projection slice for one session, when cached. */
  sessionInfo(sessionId: string): MobileSessionControl | null {
    const cached = this.sessions.get(sessionId)
    if (cached === undefined) return null
    return {
      queue: cached.queue,
      permissions: cached.values.permissions ?? null,
      modelSelection: cached.values.modelSelection ?? null,
      contextPressure: cached.values.contextPressure ?? null,
      tokenUsage: cached.values.tokenUsage ?? null,
    }
  }
}
