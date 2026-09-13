/** Remote-control activation: relay tunnel, pairing state, mobile surface, tray entry. */

import { randomBytes } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { readFile } from 'node:fs/promises'
import { dirname, join, normalize, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import { canonicalRelayOrigin } from './remote-relay-origin.ts'
export { canonicalRelayOrigin } from './remote-relay-origin.ts'
import { RemoteRelayTunnel, type RemoteRelayTunnelState } from './remote-relay-tunnel.ts'
import { registerMobileApi } from './mobile-api.ts'
import { MobileInterruptions, normalizeQuestions } from './mobile-interruptions.ts'
import { MobileControlCache } from './mobile-control.ts'
import { desktopTrayLabel } from './tray-locale.ts'

/** States surfaced to the pairing dialog and the sidebar entry. */
export type DesktopRemoteRelayState = 'disabled' | 'off' | 'connecting' | 'ready' | 'failed'

/** Renderer-safe pairing state consumed by the QR window. */
export interface DesktopRemoteRelaySnapshot {
  readonly state: DesktopRemoteRelayState
  readonly relayOrigin: string | null
  readonly pairUrl: string | null
  readonly error: string | null
  /** Whether a phone page has polled the mobile API recently. */
  readonly connected: boolean
}

/** Shared flag the browser-access refresh consults for tunnel needs. */
export interface RemoteRelayAccessHandle {
  required: boolean
}

export interface RemoteRelayOptions {
  /** Browser-access handle OR-ed into the ordinary-browser permission. */
  readonly access: RemoteRelayAccessHandle
  /** Re-apply the browser permission after the handle flips. */
  readonly refreshAccess: () => void
  /** Configured relay origin; empty means the tunnel stays disabled. */
  readonly relayOrigin: string
}

interface RelayPairing {
  readonly pairId: string
  readonly secret: string
  readonly token: string | null
}

const MOBILE_CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "connect-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "frame-ancestors 'none'",
].join('; ')

const MIME_TYPES: Readonly<Record<string, string>> = Object.freeze({
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
})

function base32Identifier(): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz234567'
  return Array.from(randomBytes(10), byte => alphabet[byte % 32]).join('')
}

/**
 * Activate the remote-control surface for one Host generation: the mobile
 * page and its API are always mounted, while the outbound relay tunnel runs
 * only when a relay origin is configured. Approvals and structured questions
 * are phone-first through bounded holds on the Host waterfalls, degrading to
 * the desktop answerer when no phone is around.
 */
export function applyRemoteRelay(ctx: Context, options: RemoteRelayOptions): void {
  const mobileRoot = join(dirname(fileURLToPath(import.meta.url)), 'native-ui')
  const origin = canonicalRelayOrigin(options.relayOrigin)

  let tunnel: RemoteRelayTunnel | null = null
  const mobilePresence = { lastSeen: 0 }
  const phoneActive = (): boolean => Date.now() - mobilePresence.lastSeen < 15_000

  /**
   * Interruptions (tool approvals, structured questions) run in PARALLEL on
   * phone and desktop: the ask is delegated to the desktop answerer
   * immediately while the phone gets its own actionable record; whichever
   * side answers first settles the waterfall, the other side's record or
   * popup is simply retired/dismissed.
   */
  const interruptions = new MobileInterruptions({
    phoneActive,
    now: () => Date.now(),
    log: message => ctx.logger.info(message),
    parallel: true,
  })
  const controlCache = new MobileControlCache(message => ctx.logger.warn(message))
  ctx.effect(() => () => { controlCache.dispose() }, 'dsh-plugin-desktop: mobile control cache lifetime')
  const clientBinding = { clientId: null as string | null }

  function sessionKeyOf(request: { agent?: { session?: { id?: unknown } } }): string | null {
    return typeof request.agent?.session?.id === 'string' ? request.agent.session.id : null
  }

  type ApprovalWaterfall = {
    on(name: 'approval/request', listener: (request: {
      agent?: { session?: { id?: unknown } }
      toolName?: unknown
      callId?: unknown
      reason?: unknown
      signal?: AbortSignal
    }, next: () => Promise<string> | string) => Promise<string>, options?: { prepend?: boolean }): () => void
  }
  ctx.effect(() => (ctx as unknown as ApprovalWaterfall).on(
    'approval/request',
    (request, next) => {
      // Desktop keeps its popup: delegate down the chain right away.
      const desktop: Promise<string> = typeof next === 'function'
        ? Promise.resolve(next())
        : Promise.resolve('unavailable')
      try {
        const handle = interruptions.hold({
          sessionId: sessionKeyOf(request),
          toolName: typeof request.toolName === 'string' ? request.toolName : 'unknown-tool',
          callId: typeof request.callId === 'string' ? request.callId : null,
          reason: typeof request.reason === 'string' ? request.reason : null,
          questions: null,
        }, request.signal)
        return new Promise<string>((resolve, reject) => {
          let done = false
          const finish = (settle: () => void): void => {
            if (done) return
            done = true
            handle.retire()
            settle()
          }
          void handle.settled.then(settlement => {
            if (settlement.kind === 'phone-decision') {
              finish(() => { resolve(settlement.decision === 'allow' ? 'allowed-once' : 'rejected') })
            } else if (settlement.kind === 'abort') {
              finish(() => { resolve('cancelled') })
            } else {
              // Phone side dismissed its card; the desktop answer still settles the ask.
              handle.retire()
            }
          })
          void desktop.then(outcome => { finish(() => { resolve(outcome) }) }, cause => { finish(() => { reject(cause) }) })
        })
      } catch (cause) {
        ctx.logger.error(`dsh-plugin-desktop: approval phone hold failed: ${cause instanceof Error ? cause.message : String(cause)}`)
        return desktop
      }
    },
    // Prepend: the runtime's api-remotes forwarder registers earlier and
    // claims agent-scoped asks for the desktop browser; only a listener ahead
    // of it can register the phone's parallel record before delegating.
    { prepend: true },
  ), 'dsh-plugin-desktop: remote relay approval hold')

  type QuestionWaterfall = {
    on(name: 'user-questions/request', listener: (request: {
      agent?: { session?: { id?: unknown } }
      questions?: unknown
      signal?: AbortSignal
    }, next: () => Promise<{ answers: readonly { id: string; selected: readonly string[]; custom?: string }[] }> | { answers: readonly { id: string; selected: readonly string[]; custom?: string }[] }) => Promise<{ answers: readonly { id: string; selected: readonly string[]; custom?: string }[] }>, options?: { prepend?: boolean }): () => void
  }
  ctx.effect(() => (ctx as unknown as QuestionWaterfall).on(
    'user-questions/request',
    (request, next) => {
      const questions = normalizeQuestions(request.questions)
      if (questions === null) {
        // Not phone-renderable (empty or oversized): the desktop keeps it.
        return typeof next === 'function' ? Promise.resolve(next()) : Promise.reject(new Error('no user-questions answerer accepted the request'))
      }
      // Desktop keeps its dialog: delegate down the chain right away.
      const desktop = typeof next === 'function' ? Promise.resolve(next()) : undefined
      const handle = interruptions.hold({
        sessionId: sessionKeyOf(request),
        toolName: 'ask-user',
        callId: null,
        reason: null,
        questions,
      }, request.signal)
      return new Promise<{ answers: readonly { id: string; selected: readonly string[]; custom?: string }[] }>((resolve, reject) => {
        let done = false
        const finish = (settle: () => void): void => {
          if (done) return
          done = true
          handle.retire()
          settle()
        }
        void handle.settled.then(settlement => {
          if (settlement.kind === 'phone-answer') {
            finish(() => { resolve({ answers: settlement.answers }) })
          } else if (settlement.kind === 'abort') {
            finish(() => { reject(new Error('question aborted before either side answered')) })
          } else {
            // Phone side dismissed its card; the desktop answer still settles the ask.
            handle.retire()
          }
        })
        if (desktop !== undefined) {
          void desktop.then(answer => { finish(() => { resolve(answer) }) }, cause => { finish(() => { reject(cause) }) })
        }
      })
    },
    // Prepend ahead of the api-remotes forwarder, same as the approval hold.
    { prepend: true },
  ), 'dsh-plugin-desktop: remote relay question hold')

  let pairing: RelayPairing | null = null

  function mintPairing(relayCtx: Context, relayOrigin: URL): RelayPairing {
    let token: string | null = null
    try {
      const authenticated = relayCtx.connection.authenticatedUrl(relayOrigin.href) as string
      token = new URL(authenticated).searchParams.get('token')
    } catch {
      token = null
    }
    return {
      pairId: base32Identifier(),
      secret: randomBytes(32).toString('base64url'),
      token,
    }
  }

  function relayWsUrl(relayOrigin: URL): string {
    return `${relayOrigin.protocol === 'https:' ? 'wss' : 'ws'}://${relayOrigin.host}/host`
  }

  function snapshot(): DesktopRemoteRelaySnapshot {
    if (origin === null) {
      return { state: 'disabled', relayOrigin: null, pairUrl: null, error: null, connected: false }
    }
    if (pairing === null) {
      return { state: 'off', relayOrigin: origin.origin, pairUrl: null, error: null, connected: false }
    }
    const tunnelState: RemoteRelayTunnelState = tunnel?.snapshot().state ?? 'connecting'
    const pairUrl = pairing.token === null
      ? null
      : `${origin.href}r/${pairing.pairId}/mobile/?token=${pairing.token}`
    return {
      state: pairing.token === null ? 'failed' : tunnelState,
      relayOrigin: origin.origin,
      pairUrl,
      error: pairing.token === null
        ? 'browser session token unavailable'
        : (tunnel?.snapshot().error ?? null),
      connected: phoneActive(),
    }
  }

  function startTunnel(): void {
    if (origin === null || pairing === null || pairing.token === null) return
    const wsUrl = relayWsUrl(origin)
    tunnel = new RemoteRelayTunnel({
      relayUrl: wsUrl,
      pairId: pairing.pairId,
      secret: pairing.secret,
      targetPort: ctx.webServer.port,
      log: message => ctx.logger.info(`dsh-plugin-desktop: ${message}`),
    })
    tunnel.start()
  }

  /**
   * Remote control is opt-in per app run: the tunnel and its pairing stay
   * dormant until the user clicks enable (pairing window or tray flow), so a
   * configured relay origin alone never widens the attack surface at boot.
   */
  function enableRemote(): void {
    if (origin === null) return
    if (pairing === null) {
      pairing = mintPairing(ctx, origin)
      options.access.required = true
      options.refreshAccess()
    }
    if (tunnel === null) startTunnel()
  }

  ctx.effect(() => () => {
    tunnel?.dispose()
    tunnel = null
    if (options.access.required) {
      options.access.required = false
      options.refreshAccess()
    }
  }, 'dsh-plugin-desktop: remote relay tunnel lifetime')

  registerMobileApi({
    ctx,
    interruptions,
    controlCache,
    clientBinding,
    relayActive: () => tunnel?.snapshot().state === 'ready',
    presence: mobilePresence,
  })

  ctx.effect(() => ctx.webServer.register({
    kind: 'prefix',
    path: '/mobile',
    handler: (req, res) => {
      void serveMobilePage(req, res)
    },
  }), 'dsh-plugin-desktop: mobile page route')

  async function serveMobilePage(req: IncomingMessage, res: ServerResponse): Promise<void> {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
      res.end('method not allowed')
      return
    }
    const url = new URL(req.url ?? '/mobile/', 'http://localhost')
    let relative = url.pathname.replace(/^\/mobile\/?/u, '')
    // The vite native-ui build is flat: the entry is `mobile.html` next to the
    // shared `assets/` directory, so the page root maps onto that build root.
    if (relative === '') relative = 'mobile.html'
    const normalized = normalize(relative).split(sep).join('/')
    if (normalized.startsWith('..') || normalized.includes('../') || normalized.startsWith('/')
      || (normalized !== 'mobile.html' && !normalized.startsWith('assets/'))) {
      res.writeHead(404, { 'cache-control': 'no-store' })
      res.end('not found')
      return
    }
    try {
      const body = await readFile(join(mobileRoot, normalized))
      const extension = normalized.slice(normalized.lastIndexOf('.'))
      const contentType = MIME_TYPES[extension] ?? 'application/octet-stream'
      res.writeHead(200, {
        'content-type': contentType,
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
        ...(extension === '.html' ? { 'content-security-policy': MOBILE_CSP } : {}),
        'content-length': String(body.byteLength),
      })
      res.end(req.method === 'HEAD' ? undefined : body)
    } catch {
      res.writeHead(404, { 'cache-control': 'no-store' })
      res.end('not found')
    }
  }

  ctx.effect(() => {
    const registration = ctx.desktopRuntime.registerTrayItem({
      group: 'tools',
      order: 30,
      label: () => desktopTrayLabel(ctx.desktopRuntime.locale, 'remoteControl'),
      invoke: () => {
        ctx.desktopRuntime.openRemotePairingWindow({
          locale: () => ctx.desktopRuntime.locale,
          snapshot,
          enable: enableRemote,
          regenerate,
        })
      },
    })
    return () => registration.dispose()
  }, 'dsh-plugin-desktop: remote relay tray entry')

  function remoteJson(res: ServerResponse, status: number, value: unknown): void {
    const body = `${JSON.stringify(value)}\n`
    res.writeHead(status, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
      'content-length': String(Buffer.byteLength(body)),
    })
    res.end(body)
  }

  function rejectRemoteRequest(req: IncomingMessage, res: ServerResponse): boolean {
    const rejection = ctx.connection.requestRejection(req)
    if (rejection === undefined) return false
    res.writeHead(rejection)
    res.end(rejection === 401 ? 'unauthorized' : 'forbidden')
    return true
  }

  function sameOriginMutatingRequest(req: IncomingMessage): boolean {
    const originHeader = req.headers.origin
    if (typeof originHeader !== 'string' || originHeader === '') return false
    try {
      return new URL(originHeader).host === (req.headers.host ?? '')
    } catch {
      return false
    }
  }

  /** Renderer-readable relay status for the sidebar entry's online tint. */
  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/remote/state',
    handler: (req, res) => {
      if (rejectRemoteRequest(req, res)) return
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      const current = snapshot()
      remoteJson(res, 200, {
        state: current.state,
        waiting: current.state === 'connecting' || current.state === 'ready',
        connected: current.state === 'ready' && current.connected,
        relayOrigin: current.relayOrigin,
      })
    },
  }), 'dsh-plugin-desktop: remote relay state route')

  /** Sidebar-triggered pairing window: same opener the tray entry uses. */
  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/remote/pairing/open',
    handler: (req, res) => {
      if (rejectRemoteRequest(req, res)) return
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { 'cache-control': 'no-store' })
        res.end('forbidden')
        return
      }
      ctx.desktopRuntime.openRemotePairingWindow({
        locale: () => ctx.desktopRuntime.locale,
        snapshot,
        enable: enableRemote,
        regenerate,
      })
      remoteJson(res, 200, { opened: true })
    },
  }), 'dsh-plugin-desktop: remote pairing open route')

  function regenerate(): void {
    if (origin === null) return
    tunnel?.dispose()
    tunnel = null
    pairing = mintPairing(ctx, origin)
    // A fresh pairing may be claimed by any device again.
    clientBinding.clientId = null
    if (!options.access.required) {
      options.access.required = true
      options.refreshAccess()
    }
    startTunnel()
  }
}
