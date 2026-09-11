/** Cookie-authenticated JSON API behind the custom mobile task page. */

import { randomUUID } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'

/** One approval the Host waterfall observed without answering it ourselves. */
export interface MobilePendingApproval {
  readonly sessionId: string | null
  readonly toolName: string
  readonly callId: string | null
  readonly reason: string | null
  readonly at: number
}

/** Values the mobile routes read from their owner for every request. */
export interface MobileApiOptions {
  /** Cordis context carrying the webServer, connection fence, and controllers. */
  readonly ctx: Context
  /** Live mirror of approvals observed by the Host waterfall. */
  readonly pendingApprovals: () => readonly MobilePendingApproval[]
  /** Whether the remote relay tunnel is currently active. */
  readonly relayActive: () => boolean
  /** Presence sink stamped on every poll so the shell can tint its phone entry. */
  readonly presence: { lastSeen: number }
}

/** One user or assistant line the mobile transcript view can render. */
export interface MobileTranscriptLine {
  readonly role: 'user' | 'assistant'
  readonly text: string
  readonly time: number
}

const MAX_BODY_BYTES = 64 * 1024
const MAX_TRANSCRIPT_MESSAGES = 80

/** Session summary fields the mobile page consumes; kept deliberately narrow. */
interface MobileSessionRow {
  readonly id: string
  readonly title: string | null
  readonly running: boolean
  readonly updatedAt: number
  readonly cwd: string | null
}

/** Workspace grouping mirrored from the desktop sidebar's own source. */
interface MobileWorkspaceGroup {
  readonly name: string
  readonly path: string
  readonly sessionIds: readonly string[]
}

interface SessionControllerFace {
  list: (request: unknown, signal?: AbortSignal) => Promise<{ items: ReadonlyArray<Record<string, unknown>> }>
  create: (payload: Record<string, unknown>) => Promise<{ sessionId: string }>
  prompt: (payload: Record<string, unknown>, signal?: AbortSignal) => Promise<{ accepted: boolean }>
  cancel: (payload: Record<string, unknown>) => Promise<{ accepted: boolean }>
  inspect?: (sessionId: string, signal?: AbortSignal) => Promise<{ events?: readonly unknown[] }>
  page?: (request: {
    address: { kind: 'session'; sessionId: string }
    throughSeq: number
    maxMessages?: number
  }, signal?: AbortSignal) => Promise<{ records?: readonly unknown[] }>
}

function freshSignal(ms: number): AbortSignal {
  return AbortSignal.timeout(ms)
}

interface WorkspaceFeedFace {
  baseline: () => {
    items: ReadonlyArray<{
      workspaceId: string
      path: string
      title: string
      sessionIds: readonly string[]
    }>
    archivedSessionIds: readonly string[]
  }
}

/**
 * Host prompt RPC only accepts content parts. Passing a raw string throws
 * inside `content.some(...)` after the Agent is already resumed; that used to
 * surface as an unhandled rejection and take the Electron process down.
 */
export function mobilePromptParts(text: string): ReadonlyArray<{ readonly type: 'text'; readonly text: string }> {
  return [{ type: 'text', text }]
}

/** Pull visible text out of a message content array or a lone string. */
export function textFromPromptContent(content: unknown): string {
  if (typeof content === 'string') return content.trim()
  if (!Array.isArray(content)) return ''
  const parts: string[] = []
  for (const part of content) {
    if (typeof part === 'string') {
      if (part !== '') parts.push(part)
      continue
    }
    if (part === null || typeof part !== 'object' || Array.isArray(part)) continue
    const row = part as Record<string, unknown>
    if (row.type === 'text' && typeof row.text === 'string' && row.text !== '') parts.push(row.text)
  }
  return parts.join('').trim()
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function asWireEvent(record: unknown): { type: string; time: number; data: unknown } | null {
  const row = asRecord(record)
  if (row === null) return null
  if (row.type === 'event') {
    const event = asRecord(row.event)
    if (event === null || typeof event.type !== 'string') return null
    return {
      type: event.type,
      time: typeof event.time === 'number' ? event.time : 0,
      data: event.data,
    }
  }
  if (typeof row.type === 'string' && row.type !== 'chunks') {
    return {
      type: row.type,
      time: typeof row.time === 'number' ? row.time : 0,
      data: row.data,
    }
  }
  return null
}

/**
 * Fold a Session inspect/page log into the compact chat lines the phone page
 * renders. Injected context (file notices, skills) is skipped so the thread
 * matches what a person typed and what the assistant answered.
 */
export function mobileTranscriptFromEvents(events: readonly unknown[]): readonly MobileTranscriptLine[] {
  const lines: MobileTranscriptLine[] = []
  for (const record of events) {
    const event = asWireEvent(record)
    if (event === null) continue
    const data = asRecord(event.data)
    if (event.type === 'user/message') {
      const source = asRecord(data?.source)
      const kind = source?.kind
      if (kind !== undefined && kind !== 'user') continue
      const text = textFromPromptContent(data?.content)
      if (text !== '') lines.push({ role: 'user', text, time: event.time })
      continue
    }
    if (event.type === 'assistant/message') {
      const message = asRecord(data?.message)
      const text = textFromPromptContent(message?.content ?? data?.content)
      if (text !== '') lines.push({ role: 'assistant', text, time: event.time })
    }
  }
  return lines.length > MAX_TRANSCRIPT_MESSAGES ? lines.slice(-MAX_TRANSCRIPT_MESSAGES) : lines
}

function sessionController(ctx: Context): SessionControllerFace | undefined {
  const value = ctx.get('sessionController')
  if (value === undefined || value === null || typeof value !== 'object') return undefined
  return value as SessionControllerFace
}

function workspaceFeed(ctx: Context): WorkspaceFeedFace | undefined {
  const controller = ctx.get('workspaceController') as { feed?: WorkspaceFeedFace } | undefined
  if (controller === undefined || controller === null || typeof controller !== 'object') return undefined
  return controller.feed
}

/** Folder tail used as the group label when a Workspace has no title. */
function workspaceName(path: string, title: string): string {
  if (title !== '') return title
  const parts = path.split(/[\\/]/u).filter(part => part !== '')
  return parts.at(-1) ?? path
}

function titleFromProjections(projections: unknown): unknown {
  const root = asRecord(projections)
  if (root === null) return undefined
  const values = asRecord(root.values) ?? root
  if (typeof values.title === 'string') return values.title
  const nested = asRecord(values.title)
  if (nested !== null && typeof nested.title === 'string') return nested.title
  if (nested !== null && typeof nested.value === 'string') return nested.value
  if (typeof values.displayTitle === 'string') return values.displayTitle
  return undefined
}

/** Display title with the sidebar's own fallback chain: title, cwd basename, id. */
export function displayTitleOf(title: unknown, cwd: string | null, id: string): string | null {
  if (typeof title === 'string' && title !== '') return title
  if (cwd !== null && cwd !== '') {
    const parts = cwd.split(/[\\/]/u).filter(part => part !== '')
    const base = parts.at(-1)
    if (base !== undefined && base !== '') return base
  }
  return id === '' ? null : id.slice(0, 8)
}

function mobileSessionRow(summary: Record<string, unknown>): MobileSessionRow {
  const id = typeof summary.sessionId === 'string' ? summary.sessionId : ''
  const cwd = typeof summary.cwd === 'string' ? summary.cwd : null
  return {
    id,
    title: displayTitleOf(titleFromProjections(summary.projections), cwd, id),
    running: summary.running === true,
    updatedAt: typeof summary.updatedAt === 'number' ? summary.updatedAt : 0,
    cwd,
  }
}

function json(res: ServerResponse, status: number, value: unknown): void {
  if (res.headersSent || res.writableEnded) return
  const body = `${JSON.stringify(value)}\n`
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'content-length': String(Buffer.byteLength(body)),
  })
  res.end(body)
}

function authorityOf(url: string): string | null {
  try {
    return new URL(url).host
  } catch {
    return null
  }
}

/**
 * Mutating methods must be same-origin: the cookie is SameSite=Strict, and an
 * exact Origin match (whose authority equals the request Host) closes CSRF
 * for POSTs the way the desktop settings routes do for the loopback renderer.
 */
function sameOriginMutatingRequest(req: IncomingMessage): boolean {
  const origin = req.headers.origin
  const host = req.headers.host
  if (typeof origin !== 'string' || origin === '') return false
  const originAuthority = authorityOf(origin)
  return originAuthority !== null && originAuthority === (host ?? '')
}

async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    size += (chunk as Buffer).length
    if (size > MAX_BODY_BYTES) throw new Error('request body too large')
    chunks.push(chunk as Buffer)
  }
  if (chunks.length === 0) return {}
  const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('request body must be a JSON object')
  }
  return parsed as Record<string, unknown>
}

function requireController(ctx: Context, res: ServerResponse): SessionControllerFace | undefined {
  const controller = sessionController(ctx)
  if (controller === undefined) {
    json(res, 503, { error: 'session-controller-unavailable' })
    return undefined
  }
  return controller
}

async function loadTranscript(controller: SessionControllerFace, sessionId: string): Promise<readonly MobileTranscriptLine[]> {
  if (typeof controller.inspect === 'function') {
    const inspected = await controller.inspect(sessionId)
    return mobileTranscriptFromEvents(inspected.events ?? [])
  }
  if (typeof controller.page === 'function') {
    const page = await controller.page({
      address: { kind: 'session', sessionId },
      throughSeq: -1,
      maxMessages: MAX_TRANSCRIPT_MESSAGES,
    })
    return mobileTranscriptFromEvents(page.records ?? [])
  }
  return []
}

/**
 * Register the mobile page's JSON API on the Host webServer. Every route sits
 * behind the standard connection fence (Host trust plus browser cookie) and
 * answers plain JSON; session operations delegate to the Host's own
 * session controller rather than a parallel data path.
 */
export function registerMobileApi(options: MobileApiOptions): void {
  const { ctx } = options
  const reject = (req: IncomingMessage, res: ServerResponse): boolean => {
    const rejection = ctx.connection.requestRejection(req)
    if (rejection === undefined) return false
    res.writeHead(rejection)
    res.end(rejection === 401 ? 'unauthorized' : 'forbidden')
    return true
  }

  const fail = (cause: unknown, res: ServerResponse, label: string, code: string): void => {
    const message = cause instanceof Error ? cause.message : String(cause)
    ctx.logger.error(`dsh-plugin-desktop: ${label}: ${message}`)
    json(res, 503, { error: code, message })
  }

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/state',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      const controller = requireController(ctx, res)
      if (controller === undefined) return
      options.presence.lastSeen = Date.now()
      void controller.list({}, freshSignal(15_000)).then(
        result => {
          try {
            const feed = workspaceFeed(ctx)
            const baseline = feed?.baseline()
            const archived = new Set(baseline?.archivedSessionIds ?? [])
            const rows = result.items
              .filter(item =>
                item.origin !== 'subagent'
                && item.blank !== true
                && (typeof item.sessionId === 'string' && !archived.has(item.sessionId)))
              .map(mobileSessionRow)
            const byId = new Map(rows.map(row => [row.id, row]))
            const groups: MobileWorkspaceGroup[] = []
            const accounted = new Set<string>()
            for (const workspace of baseline?.items ?? []) {
              const ids = workspace.sessionIds.filter(id => byId.has(id))
              ids.forEach(id => accounted.add(id))
              if (ids.length > 0) {
                groups.push({
                  name: workspaceName(workspace.path, workspace.title),
                  path: workspace.path,
                  sessionIds: ids,
                })
              }
            }
            const stray = rows.filter(row => !accounted.has(row.id))
            if (stray.length > 0) {
              groups.push({ name: '', path: '', sessionIds: stray.map(row => row.id) })
            }
            json(res, 200, {
              sessions: rows,
              groups,
              approvals: options.pendingApprovals(),
              relay: { active: options.relayActive() },
            })
          } catch (cause) {
            fail(cause, res, 'mobile state assemble failed', 'state-unavailable')
          }
        },
        cause => { fail(cause, res, 'mobile state read failed', 'state-unavailable') },
      )
    },
  }), 'dsh-plugin-desktop: mobile state route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/transcript',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      const controller = requireController(ctx, res)
      if (controller === undefined) return
      const url = new URL(req.url ?? '/', 'http://localhost')
      const sessionId = url.searchParams.get('sessionId') ?? ''
      if (sessionId === '') {
        json(res, 400, { error: 'invalid-request' })
        return
      }
      options.presence.lastSeen = Date.now()
      void loadTranscript(controller, sessionId).then(
        messages => { json(res, 200, { sessionId, messages }) },
        cause => { fail(cause, res, 'mobile transcript read failed', 'transcript-unavailable') },
      )
    },
  }), 'dsh-plugin-desktop: mobile transcript route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/create',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      if (!sameOriginMutatingRequest(req)) {
        if (!res.headersSent) {
          res.writeHead(403, { 'cache-control': 'no-store' })
          res.end('forbidden')
        }
        return
      }
      const controller = requireController(ctx, res)
      if (controller === undefined) return
      void readJsonBody(req).then(async body => {
        const content = typeof body.content === 'string' ? body.content.trim() : ''
        const cwd = typeof body.cwd === 'string' && body.cwd !== '' ? body.cwd : undefined
        const created = await controller.create(cwd === undefined ? {} : { cwd })
        if (content !== '') {
          await controller.prompt({
            sessionId: created.sessionId,
            requestId: randomUUID(),
            mode: 'queue',
            content: mobilePromptParts(content),
          }, freshSignal(120_000))
        }
        json(res, 200, { sessionId: created.sessionId })
      }).catch(cause => { fail(cause, res, 'mobile session create failed', 'create-failed') })
    },
  }), 'dsh-plugin-desktop: mobile create route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/prompt',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      if (!sameOriginMutatingRequest(req)) {
        if (!res.headersSent) {
          res.writeHead(403, { 'cache-control': 'no-store' })
          res.end('forbidden')
        }
        return
      }
      const controller = requireController(ctx, res)
      if (controller === undefined) return
      void readJsonBody(req).then(async body => {
        const sessionId = body.sessionId
        const content = typeof body.content === 'string' ? body.content.trim() : ''
        if (typeof sessionId !== 'string' || sessionId === '' || content === '') {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        await controller.prompt({
          sessionId,
          requestId: randomUUID(),
          mode: 'queue',
          content: mobilePromptParts(content),
        }, freshSignal(120_000))
        json(res, 200, { accepted: true })
      }).catch(cause => { fail(cause, res, 'mobile prompt failed', 'prompt-failed') })
    },
  }), 'dsh-plugin-desktop: mobile prompt route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/cancel',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      if (!sameOriginMutatingRequest(req)) {
        if (!res.headersSent) {
          res.writeHead(403, { 'cache-control': 'no-store' })
          res.end('forbidden')
        }
        return
      }
      const controller = requireController(ctx, res)
      if (controller === undefined) return
      void readJsonBody(req).then(async body => {
        const sessionId = body.sessionId
        if (typeof sessionId !== 'string' || sessionId === '') {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        const result = await controller.cancel({ sessionId })
        json(res, 200, { accepted: result.accepted === true })
      }).catch(cause => { fail(cause, res, 'mobile cancel failed', 'cancel-failed') })
    },
  }), 'dsh-plugin-desktop: mobile cancel route')
}
