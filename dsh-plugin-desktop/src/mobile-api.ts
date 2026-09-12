/** Cookie-authenticated JSON API behind the custom mobile task page. */

import { randomUUID } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { MobileInterruptions } from './mobile-interruptions.ts'
import { MobileControlCache, type MobileControlControllerFace } from './mobile-control.ts'

/** One interruption (approval or structured question) awaiting an answer. */
export type { MobileInterruption } from './mobile-interruptions.ts'

/** Values the mobile routes read from their owner for every request. */
export interface MobileApiOptions {
  /** Cordis context carrying the webServer, connection fence, and controllers. */
  readonly ctx: Context
  /** Phone-answerable approvals and questions held off the desktop waterfall. */
  readonly interruptions: MobileInterruptions
  /** Live control-state cache feeding the session-info route. */
  readonly controlCache: MobileControlCache
  /** Whether the remote relay tunnel is currently active. */
  readonly relayActive: () => boolean
  /** Presence sink stamped on every poll so the shell can tint its phone entry. */
  readonly presence: { lastSeen: number }
}

/** One +/- count pair for a file a write/edit tool changed. */
export interface MobileDiffSummary {
  readonly path: string
  readonly added: number | null
  readonly removed: number | null
}

/** One todo row inside a task-list snapshot item. */
export interface MobileTodoItem {
  readonly content: string
  readonly status: 'pending' | 'in_progress' | 'completed'
}

/**
 * Transcript item the phone renders. Plain chat balloons plus the rich rows
 * borrowed from the desktop conversation: collapsible reasoning, tool calls
 * with diff counts, and task-list snapshots.
 */
export type MobileTranscriptItem =
  | { readonly kind: 'user'; readonly text: string; readonly time: number; readonly imageCount: number }
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
    readonly diffs: readonly MobileDiffSummary[] | null
  }
  | { readonly kind: 'todo'; readonly todos: readonly MobileTodoItem[]; readonly time: number }

const MAX_BODY_BYTES = 64 * 1024
/** Prompt bodies may carry base64 image attachments, so they get their own ceiling. */
const MAX_PROMPT_BODY_BYTES = 12 * 1024 * 1024
const MAX_TRANSCRIPT_ITEMS = 120
const MAX_TEXT_CHARS = 2_000
const MAX_TOOL_TITLE_CHARS = 200
const MAX_PATH_CHARS = 160
const MAX_DIFFS_PER_CALL = 8
const MAX_DIFF_LINES_PER_SIDE = 200
const IMAGE_MEDIA_TYPES: ReadonlySet<string> = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif'])
const MAX_IMAGES_PER_PROMPT = 4

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
  selectModel?: (payload: Record<string, unknown>) => Promise<{ selected?: unknown }>
  modelCatalog?: () => Promise<unknown>
  rename?: (payload: { sessionId: string; title: string }) => Promise<unknown>
  updateQueue?: (payload: { sessionId: string; itemId: string; action: { kind: 'remove' } }) => unknown
  resolveAgent?: (sessionId: string) => Promise<{ agent?: { session?: { id?: unknown } } } | { error?: unknown }>
  control?: (signal: AbortSignal) => AsyncIterable<unknown>
}

/** Structural slice of the Host commands service the compact route uses. */
interface CommandsFace {
  execute?: (agent: unknown, line: string, images: readonly unknown[], signal: AbortSignal) => Promise<unknown>
}

/** Structural slice of the permission preset service the permission route uses. */
interface PermissionPresetsFace {
  set?: (session: unknown, preset: string) => void
}

/** Read-side slice of the same service for the options route. */
interface PermissionPresetsReadFace {
  readonly names: readonly string[]
  optionOf: (name: string) => unknown
  readonly defaultPreset?: string
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

/** One phone-attached image normalized into a Host prompt content part. */
export interface MobileImagePart {
  readonly type: 'image'
  readonly mediaType: string
  readonly data: string
  readonly name?: string
}

/**
 * Validate a phone-submitted attachment list into Host image content parts.
 * Returns null for any malformed entry so callers reject the whole payload
 * instead of silently dropping images the user believes were sent.
 */
export function mobileImageParts(value: unknown): readonly MobileImagePart[] | null {
  if (value === undefined || value === null) return []
  if (!Array.isArray(value) || value.length > MAX_IMAGES_PER_PROMPT) return null
  const parts: MobileImagePart[] = []
  for (const row of value) {
    const item = asRecord(row)
    if (item === null) return null
    if (typeof item.mediaType !== 'string' || !IMAGE_MEDIA_TYPES.has(item.mediaType)) return null
    if (typeof item.data !== 'string' || item.data === '') return null
    const name = typeof item.name === 'string' && item.name !== '' ? item.name.slice(0, 200) : undefined
    parts.push(name === undefined
      ? { type: 'image', mediaType: item.mediaType, data: item.data }
      : { type: 'image', mediaType: item.mediaType, data: item.data, name })
  }
  return parts
}

/** Build the full prompt content: text part plus validated image parts. */
export function mobilePromptContent(text: string, images: readonly MobileImagePart[]): ReadonlyArray<{ readonly type: 'text'; readonly text: string } | MobileImagePart> {
  return [...mobilePromptParts(text), ...images]
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

/** Count image blocks in a user message content array (for the 🖼 badge). */
export function countImageParts(content: unknown): number {
  if (!Array.isArray(content)) return 0
  let count = 0
  for (const part of content) {
    const block = asRecord(part)
    if (block?.type === 'image') count += 1
  }
  return count
}

function clipText(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max)}…`
}

/**
 * Count added/removed lines between two hunk texts with a bounded LCS. Hunks
 * carry 3 context lines on each side, so raw line totals would double-count;
 * the alignment here prices only real changes. Oversized hunks report null
 * rather than burn the request budget.
 */
export function diffLineCounts(oldText: string | null, newText: string): { added: number | null; removed: number | null } {
  // A null old text is a fresh file (or an overwrite with no prior content):
  // every new line counts as added and nothing as removed.
  const oldLines = oldText === null ? [] : oldText.split('\n')
  const newLines = newText.split('\n')
  if (oldLines.length > MAX_DIFF_LINES_PER_SIDE || newLines.length > MAX_DIFF_LINES_PER_SIDE) {
    return { added: null, removed: null }
  }
  // Trim the shared context prefix/suffix first; typical hunks then align trivially.
  let start = 0
  while (start < oldLines.length && start < newLines.length && oldLines[start] === newLines[start]) start += 1
  let oldEnd = oldLines.length
  let newEnd = newLines.length
  while (oldEnd > start && newEnd > start && oldLines[oldEnd - 1] === newLines[newEnd - 1]) {
    oldEnd -= 1
    newEnd -= 1
  }
  const a = oldLines.slice(start, oldEnd)
  const b = newLines.slice(start, newEnd)
  const rows = a.length + 1
  const cols = b.length + 1
  if (rows * cols > 160_000) return { added: null, removed: null }
  const table = new Uint32Array(rows * cols)
  const at = (i: number, j: number): number => table[i * cols + j] ?? 0
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * cols + j] = a[i] === b[j]
        ? at(i + 1, j + 1) + 1
        : Math.max(at(i + 1, j), at(i, j + 1))
    }
  }
  const common = at(0, 0)
  return { added: b.length - common, removed: a.length - common }
}

/** Narrow a tool/result meta payload into bounded file-diff summaries. */
export function mobileDiffSummaries(meta: unknown): readonly MobileDiffSummary[] | null {
  const diffs = asRecord(meta)?.diffs
  if (!Array.isArray(diffs) || diffs.length === 0) return null
  const summaries: MobileDiffSummary[] = []
  for (const row of diffs) {
    if (summaries.length >= MAX_DIFFS_PER_CALL) break
    const diff = asRecord(row)
    if (diff === null || typeof diff.path !== 'string' || typeof diff.newText !== 'string') continue
    const counts = diffLineCounts(typeof diff.oldText === 'string' ? diff.oldText : null, diff.newText)
    summaries.push({
      path: clipText(diff.path, MAX_PATH_CHARS),
      added: counts.added,
      removed: counts.removed,
    })
  }
  return summaries.length > 0 ? summaries : null
}

/** Derive a one-line title for a tool call from its raw JSON arguments. */
export function mobileToolTitle(name: string, argsRaw: unknown): string {
  if (typeof argsRaw === 'string' && argsRaw !== '') {
    try {
      const args = asRecord(JSON.parse(argsRaw))
      if (args !== null) {
        for (const key of ['command', 'cmd', 'file_path', 'filePath', 'path', 'pattern', 'url', 'query', 'name']) {
          const value = args[key]
          if (typeof value === 'string' && value !== '') {
            const tail = key === 'path' || key === 'file_path' || key === 'filePath'
              ? value.split(/[\\/]/u).filter(part => part !== '').slice(-2).join('/')
              : value
            return clipText(tail.replace(/\s+/gu, ' ').trim(), MAX_TOOL_TITLE_CHARS)
          }
        }
      }
    } catch { /* opaque arguments; fall back to the tool name */ }
  }
  return clipText(name, MAX_TOOL_TITLE_CHARS)
}

/** Normalize one todo/write payload into bounded phone rows. */
export function mobileTodoItems(value: unknown): readonly MobileTodoItem[] | null {
  if (!Array.isArray(value)) return null
  const todos: MobileTodoItem[] = []
  for (const row of value) {
    const item = asRecord(row)
    if (item === null || typeof item.content !== 'string' || item.content === '') continue
    const status = item.status === 'completed' || item.status === 'in_progress' ? item.status : 'pending'
    todos.push({ content: clipText(item.content, 400), status })
    if (todos.length >= 32) break
  }
  return todos.length > 0 ? todos : null
}

/**
 * Fold a Session inspect/page log into the rich transcript the phone page
 * renders. Injected context (file notices, skills) is skipped so the thread
 * matches what a person typed and what the assistant answered; reasoning
 * blocks, tool calls (with diff counts), and task lists keep the shape the
 * desktop conversation shows.
 */
export function mobileTranscriptFromEvents(events: readonly unknown[]): readonly MobileTranscriptItem[] {
  const items: MobileTranscriptItem[] = []
  let lastTodoJson = ''
  for (const record of events) {
    const event = asWireEvent(record)
    if (event === null) continue
    const data = asRecord(event.data)
    if (event.type === 'user/message') {
      const source = asRecord(data?.source)
      const kind = source?.kind
      if (kind !== undefined && kind !== 'user') continue
      const text = textFromPromptContent(data?.content)
      const imageCount = countImageParts(data?.content)
      if (text !== '' || imageCount > 0) {
        items.push({ kind: 'user', text: clipText(text, MAX_TEXT_CHARS), time: event.time, imageCount })
      }
      continue
    }
    if (event.type === 'assistant/message') {
      const message = asRecord(data?.message)
      const content = message?.content ?? data?.content
      if (Array.isArray(content)) {
        const textParts: string[] = []
        for (const part of content) {
          const block = asRecord(part)
          if (block === null) continue
          if (block.type === 'reasoning' && typeof block.text === 'string' && block.text.trim() !== '') {
            items.push({ kind: 'reasoning', text: clipText(block.text.trim(), MAX_TEXT_CHARS), time: event.time })
          } else if (block.type === 'text' && typeof block.text === 'string') {
            textParts.push(block.text)
          }
        }
        const text = textParts.join('').trim()
        if (text !== '') items.push({ kind: 'assistant', text: clipText(text, MAX_TEXT_CHARS), time: event.time })
      } else {
        const text = textFromPromptContent(content)
        if (text !== '') items.push({ kind: 'assistant', text: clipText(text, MAX_TEXT_CHARS), time: event.time })
      }
      continue
    }
    if (event.type === 'tool/call') {
      const callId = typeof data?.callId === 'string' ? data.callId : ''
      const name = typeof data?.name === 'string' ? data.name : 'tool'
      if (callId === '') continue
      items.push({
        kind: 'tool',
        callId,
        name,
        title: mobileToolTitle(name, data?.arguments),
        time: event.time,
        end: null,
        status: 'running',
        diffs: null,
      })
      continue
    }
    if (event.type === 'tool/result') {
      // The wire carries the correlation id on the result message's tool
      // source (and on each tool-result content block), never as a sibling.
      const message = asRecord(data?.message)
      const source = asRecord(message?.source)
      let callId = typeof source?.callId === 'string' ? source.callId : ''
      if (callId === '' && Array.isArray(message?.content)) {
        for (const part of message.content) {
          const block = asRecord(part)
          if (typeof block?.toolCallId === 'string') {
            callId = block.toolCallId
            break
          }
        }
      }
      if (callId === '' && typeof data?.callId === 'string') callId = data.callId
      if (callId === '') continue
      for (let index = items.length - 1; index >= 0; index -= 1) {
        const item = items[index]
        if (item === undefined || item.kind !== 'tool' || item.callId !== callId) continue
        items[index] = {
          ...item,
          end: event.time,
          status: data?.error !== undefined && data?.error !== null ? 'error' : 'ok',
          diffs: mobileDiffSummaries(data?.meta),
        }
        break
      }
      continue
    }
    if (event.type === 'todo/write') {
      const todos = mobileTodoItems(data?.todos)
      if (todos === null) continue
      const json = JSON.stringify(todos)
      if (json === lastTodoJson) continue
      lastTodoJson = json
      items.push({ kind: 'todo', todos, time: event.time })
    }
  }
  return items.length > MAX_TRANSCRIPT_ITEMS ? items.slice(-MAX_TRANSCRIPT_ITEMS) : items
}

/** Session facts derived from the durable log rather than live projections. */
export interface MobileSessionFacts {
  readonly model: { readonly provider: string; readonly model: string; readonly reasoningEffort?: string } | null
  readonly permissionPreset: string | null
}

/**
 * Fold the log's model-selection and permission-preset events into the
 * phone-side current values. The control stream's projection values cover
 * only live sessions reliably; the log is authoritative for both.
 */
export function mobileFactsFromEvents(events: readonly unknown[]): MobileSessionFacts {
  let model: MobileSessionFacts['model'] = null
  let permissionPreset: string | null = null
  for (const record of events) {
    const event = asWireEvent(record)
    if (event === null) continue
    const data = asRecord(event.data)
    if (event.type === 'model/selection' || event.type === 'request/context') {
      const provider = data !== null && typeof data.provider === 'string' ? data.provider : ''
      const name = data !== null && typeof data.model === 'string' ? data.model : ''
      if (provider !== '' && name !== '') {
        model = data !== null && typeof data.reasoningEffort === 'string' && data.reasoningEffort !== ''
          ? { provider, model: name, reasoningEffort: data.reasoningEffort }
          : { provider, model: name }
      }
      continue
    }
    if (event.type === 'permission/preset' && data !== null && typeof data.preset === 'string' && data.preset !== '') {
      permissionPreset = data.preset
    }
  }
  return { model, permissionPreset }
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

/** Phone-safe permission select normalized from the projection value. */
export function mobilePermissionsOf(value: unknown): { options: ReadonlyArray<{ value: string; name: string; description?: string }>; currentValue: string } | null {
  const root = asRecord(value)
  if (root === null || typeof root.currentValue !== 'string') return null
  if (!Array.isArray(root.options)) return null
  const options: Array<{ value: string; name: string; description?: string }> = []
  for (const row of root.options) {
    const option = asRecord(row)
    if (option === null || typeof option.value !== 'string' || typeof option.name !== 'string') continue
    const normalized: { value: string; name: string; description?: string } = { value: option.value, name: option.name }
    if (typeof option.description === 'string') normalized.description = clipText(option.description, 200)
    options.push(normalized)
  }
  if (options.length === 0) return null
  return { options, currentValue: root.currentValue }
}

/** Phone-safe model selection normalized from the modelSelection projection. */
export function mobileModelOf(value: unknown): { provider: string; model: string; reasoningEffort?: string } | null {
  const root = asRecord(value)
  if (root === null) return null
  for (const key of ['next', 'lastUsed']) {
    const selection = asRecord(root[key])
    if (selection === null || typeof selection.provider !== 'string' || typeof selection.model !== 'string') continue
    const normalized: { provider: string; model: string; reasoningEffort?: string } = {
      provider: selection.provider,
      model: selection.model,
    }
    if (typeof selection.reasoningEffort === 'string') normalized.reasoningEffort = selection.reasoningEffort
    return normalized
  }
  return null
}

/** Context occupancy for the capacity sheet, from the pressure projection. */
export function mobileContextOf(value: unknown): { percent: number | null; usedTokens: number | null; contextWindow: number | null } | null {
  const root = asRecord(value)
  if (root === null) return null
  const usedTokens = typeof root.projectedTokens === 'number'
    ? root.projectedTokens
    : (typeof root.pressureTokens === 'number' ? root.pressureTokens : null)
  const contextWindow = typeof root.contextWindow === 'number' ? root.contextWindow : null
  if (usedTokens === null && contextWindow === null) return null
  const percent = usedTokens !== null && contextWindow !== null && contextWindow > 0
    ? Math.min(100, Math.round((usedTokens / contextWindow) * 100))
    : null
  return { percent, usedTokens, contextWindow }
}

/** Cumulative token usage normalized for the capacity sheet. */
export function mobileTokensOf(value: unknown): { inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number } | null {
  const root = asRecord(value)
  if (root === null) return null
  const read = (key: string): number => (typeof root[key] === 'number' ? root[key] as number : 0)
  if (root.uncachedInputTokens === undefined && root.outputTokens === undefined) return null
  return {
    inputTokens: read('uncachedInputTokens'),
    outputTokens: read('outputTokens'),
    cacheReadTokens: read('cacheReadTokens'),
    cacheWriteTokens: read('cacheWriteTokens'),
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

async function readJsonBody(req: IncomingMessage, limitBytes: number = MAX_BODY_BYTES): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    size += (chunk as Buffer).length
    if (size > limitBytes) throw new Error('request body too large')
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

/** Shared POST-route preamble: method, CSRF, and body parsing. */
async function readMutatingBody(req: IncomingMessage, res: ServerResponse, limitBytes: number = MAX_BODY_BYTES): Promise<Record<string, unknown> | null> {
  if (req.method !== 'POST') {
    res.writeHead(405, { allow: 'POST', 'cache-control': 'no-store' })
    res.end('method not allowed')
    return null
  }
  if (!sameOriginMutatingRequest(req)) {
    res.writeHead(403, { 'cache-control': 'no-store' })
    res.end('forbidden')
    return null
  }
  try {
    return await readJsonBody(req, limitBytes)
  } catch (cause) {
    json(res, 400, { error: 'invalid-request', message: cause instanceof Error ? cause.message : String(cause) })
    return null
  }
}

async function loadTranscript(controller: SessionControllerFace, sessionId: string): Promise<{ messages: readonly MobileTranscriptItem[]; facts: MobileSessionFacts }> {
  let events: readonly unknown[] = []
  if (typeof controller.inspect === 'function') {
    const inspected = await controller.inspect(sessionId)
    events = inspected.events ?? []
  } else if (typeof controller.page === 'function') {
    const page = await controller.page({
      address: { kind: 'session', sessionId },
      throughSeq: -1,
      maxMessages: MAX_TRANSCRIPT_ITEMS,
    })
    events = page.records ?? []
  }
  return { messages: mobileTranscriptFromEvents(events), facts: mobileFactsFromEvents(events) }
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
              interruptions: options.interruptions.snapshot(),
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
        loaded => { json(res, 200, { sessionId, messages: loaded.messages, facts: loaded.facts }) },
        cause => { fail(cause, res, 'mobile transcript read failed', 'transcript-unavailable') },
      )
    },
  }), 'dsh-plugin-desktop: mobile transcript route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/session-info',
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
      options.controlCache.ensure(controller as MobileControlControllerFace)
      const info = options.controlCache.sessionInfo(sessionId)
      json(res, 200, {
        sessionId,
        permissions: info === null ? null : mobilePermissionsOf(info.permissions),
        model: info === null ? null : mobileModelOf(info.modelSelection),
        context: info === null ? null : mobileContextOf(info.contextPressure),
        tokens: info === null ? null : mobileTokensOf(info.tokenUsage),
        queue: info === null ? [] : info.queue,
      })
    },
  }), 'dsh-plugin-desktop: mobile session-info route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/model-catalog',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      const controller = requireController(ctx, res)
      if (controller === undefined || typeof controller.modelCatalog !== 'function') {
        json(res, 503, { error: 'model-catalog-unavailable' })
        return
      }
      options.presence.lastSeen = Date.now()
      void controller.modelCatalog().then(
        catalog => { json(res, 200, catalog) },
        cause => { fail(cause, res, 'mobile model catalog read failed', 'model-catalog-unavailable') },
      )
    },
  }), 'dsh-plugin-desktop: mobile model-catalog route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/create',
    handler: (req, res) => {
      if (reject(req, res)) return
      void readMutatingBody(req, res, MAX_PROMPT_BODY_BYTES).then(async body => {
        if (body === null) return
        const controller = requireController(ctx, res)
        if (controller === undefined) return
        const content = typeof body.content === 'string' ? body.content.trim() : ''
        const images = mobileImageParts(body.images)
        const cwd = typeof body.cwd === 'string' && body.cwd !== '' ? body.cwd : undefined
        if ((content === '' && (images === null || images.length === 0))) {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        if (images === null) {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        const created = await controller.create(cwd === undefined ? {} : { cwd })
        if (content !== '' || images.length > 0) {
          await controller.prompt({
            sessionId: created.sessionId,
            requestId: randomUUID(),
            mode: 'queue',
            content: mobilePromptContent(content, images),
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
      void readMutatingBody(req, res, MAX_PROMPT_BODY_BYTES).then(async body => {
        if (body === null) return
        const controller = requireController(ctx, res)
        if (controller === undefined) return
        const sessionId = body.sessionId
        const content = typeof body.content === 'string' ? body.content.trim() : ''
        const images = mobileImageParts(body.images)
        if (typeof sessionId !== 'string' || sessionId === '' || images === null || (content === '' && images.length === 0)) {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        await controller.prompt({
          sessionId,
          requestId: randomUUID(),
          mode: 'queue',
          content: mobilePromptContent(content, images),
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
      void readMutatingBody(req, res).then(async body => {
        if (body === null) return
        const controller = requireController(ctx, res)
        if (controller === undefined) return
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

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/select-model',
    handler: (req, res) => {
      if (reject(req, res)) return
      void readMutatingBody(req, res).then(async body => {
        if (body === null) return
        const controller = requireController(ctx, res)
        if (controller === undefined || typeof controller.selectModel !== 'function') {
          json(res, 503, { error: 'select-model-unavailable' })
          return
        }
        const sessionId = body.sessionId
        const provider = body.provider
        const model = body.model
        if (typeof sessionId !== 'string' || sessionId === '' || typeof provider !== 'string' || provider === '' || typeof model !== 'string' || model === '') {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        const request: Record<string, unknown> = { sessionId, provider, model }
        if (typeof body.reasoningEffort === 'string' && body.reasoningEffort !== '') request.reasoningEffort = body.reasoningEffort
        const selected = await controller.selectModel(request)
        json(res, 200, { selected: selected.selected ?? null })
      }).catch(cause => { fail(cause, res, 'mobile model select failed', 'select-model-failed') })
    },
  }), 'dsh-plugin-desktop: mobile select-model route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/permission',
    handler: (req, res) => {
      if (reject(req, res)) return
      void readMutatingBody(req, res).then(async body => {
        if (body === null) return
        const controller = requireController(ctx, res)
        if (controller === undefined || typeof controller.resolveAgent !== 'function') {
          json(res, 503, { error: 'permission-unavailable' })
          return
        }
        const sessionId = body.sessionId
        const preset = body.preset
        if (typeof sessionId !== 'string' || sessionId === '' || typeof preset !== 'string' || preset === '') {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        const presets = ctx.get('permissionPresets') as PermissionPresetsFace | undefined
        if (presets === undefined || typeof presets.set !== 'function') {
          json(res, 503, { error: 'permission-unavailable' })
          return
        }
        const resolved = await controller.resolveAgent(sessionId)
        const agent = (resolved as { agent?: unknown }).agent as { session?: unknown } | undefined
        if (agent === undefined || agent.session === undefined || agent.session === null) {
          json(res, 503, { error: 'session-unavailable' })
          return
        }
        presets.set(agent.session, preset)
        json(res, 200, { accepted: true })
      }).catch(cause => { fail(cause, res, 'mobile permission switch failed', 'permission-failed') })
    },
  }), 'dsh-plugin-desktop: mobile permission route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/queue-remove',
    handler: (req, res) => {
      if (reject(req, res)) return
      void readMutatingBody(req, res).then(async body => {
        if (body === null) return
        const controller = requireController(ctx, res)
        if (controller === undefined || typeof controller.updateQueue !== 'function') {
          json(res, 503, { error: 'queue-unavailable' })
          return
        }
        const sessionId = body.sessionId
        const itemId = body.itemId
        if (typeof sessionId !== 'string' || sessionId === '' || typeof itemId !== 'string' || itemId === '') {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        await controller.updateQueue({ sessionId, itemId, action: { kind: 'remove' } })
        json(res, 200, { accepted: true })
      }).catch(cause => { fail(cause, res, 'mobile queue remove failed', 'queue-remove-failed') })
    },
  }), 'dsh-plugin-desktop: mobile queue-remove route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/rename',
    handler: (req, res) => {
      if (reject(req, res)) return
      void readMutatingBody(req, res).then(async body => {
        if (body === null) return
        const controller = requireController(ctx, res)
        if (controller === undefined || typeof controller.rename !== 'function') {
          json(res, 503, { error: 'rename-unavailable' })
          return
        }
        const sessionId = body.sessionId
        const title = typeof body.title === 'string' ? body.title.trim().slice(0, 200) : ''
        if (typeof sessionId !== 'string' || sessionId === '' || title === '') {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        await controller.rename({ sessionId, title })
        json(res, 200, { accepted: true })
      }).catch(cause => { fail(cause, res, 'mobile rename failed', 'rename-failed') })
    },
  }), 'dsh-plugin-desktop: mobile rename route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/compact',
    handler: (req, res) => {
      if (reject(req, res)) return
      void readMutatingBody(req, res).then(async body => {
        if (body === null) return
        const controller = requireController(ctx, res)
        if (controller === undefined || typeof controller.resolveAgent !== 'function') {
          json(res, 503, { error: 'compact-unavailable' })
          return
        }
        const sessionId = body.sessionId
        if (typeof sessionId !== 'string' || sessionId === '') {
          json(res, 400, { error: 'invalid-request' })
          return
        }
        const commands = ctx.get('commands') as CommandsFace | undefined
        if (commands === undefined || typeof commands.execute !== 'function') {
          json(res, 503, { error: 'compact-unavailable' })
          return
        }
        const resolved = await controller.resolveAgent(sessionId)
        const agent = (resolved as { agent?: unknown }).agent
        if (agent === undefined) {
          json(res, 503, { error: 'session-unavailable' })
          return
        }
        await commands.execute(agent, '/compact', [], freshSignal(120_000))
        json(res, 200, { accepted: true })
      }).catch(cause => { fail(cause, res, 'mobile compact failed', 'compact-failed') })
    },
  }), 'dsh-plugin-desktop: mobile compact route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/mobile/permission-presets',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      options.presence.lastSeen = Date.now()
      const presets = ctx.get('permissionPresets') as PermissionPresetsReadFace | undefined
      if (presets === undefined || typeof presets.optionOf !== 'function' || typeof presets.names !== 'object') {
        json(res, 200, { options: [], defaultPreset: null })
        return
      }
      const optionsList: Array<{ value: string; name: string; description?: string }> = []
      try {
        for (const name of presets.names) {
          const option = presets.optionOf(name) as { value?: unknown; name?: unknown; description?: unknown } | undefined
          if (option === undefined || typeof option.value !== 'string' || typeof option.name !== 'string') continue
          const normalized: { value: string; name: string; description?: string } = { value: option.value, name: option.name }
          if (typeof option.description === 'string') normalized.description = clipText(option.description, 200)
          optionsList.push(normalized)
        }
      } catch {
        // A missing preset table leaves the phone without the switch, not without the page.
      }
      const defaultPreset = typeof presets.defaultPreset === 'string' ? presets.defaultPreset : null
      json(res, 200, { options: optionsList, defaultPreset })
    },
  }), 'dsh-plugin-desktop: mobile permission-presets route')

  const decideRoute = (path: string, label: string, code: string): void => {
    ctx.effect(() => ctx.webServer.register({
      kind: 'exact',
      path,
      handler: (req, res) => {
        if (reject(req, res)) return
        void readMutatingBody(req, res).then(body => {
          if (body === null) return
          const key = body.key
          if (typeof key !== 'string' || key === '') {
            json(res, 400, { error: 'invalid-request' })
            return
          }
          const outcome = options.interruptions.decide(key, body)
          if (outcome === null) {
            json(res, 400, { error: 'invalid-request' })
            return
          }
          if (outcome === 'unknown') {
            json(res, 409, { error: 'already-settled' })
            return
          }
          options.presence.lastSeen = Date.now()
          json(res, 200, { accepted: true })
        }).catch(cause => { fail(cause, res, label, code) })
      },
    }), `dsh-plugin-desktop: ${label} route`)
  }
  decideRoute('/api/desktop/mobile/approve', 'mobile approval decide', 'approve-failed')
  decideRoute('/api/desktop/mobile/answer', 'mobile question answer', 'answer-failed')
}
