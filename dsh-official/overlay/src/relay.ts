import { randomBytes } from 'node:crypto'
import { createRequire } from 'node:module'
const QRCode = createRequire(import.meta.url)('qrcode') as {
  toString: (text: string, opts: object) => Promise<string>
}
import { readFile } from 'node:fs/promises'
import { dirname, join, normalize, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { registerMobileApi } from './mobile-api.ts'
import { MobileControlCache } from './mobile-control.ts'
import { MobileInterruptions, normalizeQuestions } from './mobile-interruptions.ts'
import { resolveDshHome } from './dsh-home.ts'
import { canonicalRelayOrigin, persistRelayOrigin } from './relay-origin.ts'
import { RemoteRelayTunnel, type RemoteRelayTunnelState } from './relay-tunnel.ts'
import { readJsonBody, rejectUnauthorized, sameOriginMutatingRequest, writeJson } from './http.ts'

const PUBLIC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'public')
const MOBILE_CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "connect-src 'self'",
  "img-src 'self' data:",
  "frame-ancestors 'none'",
].join('; ')

export interface OverlayRelaySnapshot {
  readonly state: 'disabled' | 'off' | 'connecting' | 'ready' | 'failed'
  readonly waiting: boolean
  readonly connected: boolean
  readonly relayOrigin: string | null
  readonly pairUrl: string | null
  readonly qrSvg: string | null
  readonly error: string | null
}

interface Pairing {
  readonly pairId: string
  readonly secret: string
  readonly token: string | null
}

function mintPairing(ctx: Context, origin: URL): Pairing {
  let token: string | null = null
  try {
    const authenticated = ctx.connection.authenticatedUrl(origin.href) as string
    token = new URL(authenticated).searchParams.get('token')
  } catch {
    token = null
  }
  const alphabet = 'abcdefghijklmnopqrstuvwxyz234567'
  const pairId = Array.from(randomBytes(10), byte => alphabet[byte % 32]).join('')
  return { pairId, secret: randomBytes(32).toString('base64url'), token }
}

/** Opt-in relay tunnel, pairing JSON, mobile API, and static phone page. */
export function applyOverlayRelay(ctx: Context, originValue: string): void {
  let origin = canonicalRelayOrigin(originValue)
  let pairing: Pairing | null = null
  let tunnel: RemoteRelayTunnel | null = null
  const presence = { lastSeen: 0 }
  const phoneActive = (): boolean => Date.now() - presence.lastSeen < 15_000
  const interruptions = new MobileInterruptions({
    phoneActive,
    now: () => Date.now(),
    log: message => ctx.logger.info(message),
    parallel: true,
  })
  const controlCache = new MobileControlCache(message => ctx.logger.warn(message))
  ctx.effect(() => () => { controlCache.dispose() }, 'dsh-overlay-custom: mobile control cache')
  const clientBinding = { clientId: null as string | null, lastSeen: 0 }

  function sessionKeyOf(request: { agent?: { session?: { id?: unknown } } }): string | null {
    return typeof request.agent?.session?.id === 'string' ? request.agent.session.id : null
  }

  function gateDesktopAsk(request: { signal?: AbortSignal }): { abortPhoneWin(): void } {
    const turnSignal = request.signal
    const gate = new AbortController()
    if (turnSignal !== undefined) {
      if (turnSignal.aborted) gate.abort(turnSignal.reason)
      else turnSignal.addEventListener('abort', () => { gate.abort(turnSignal.reason) }, { once: true })
    }
    request.signal = gate.signal
    return {
      abortPhoneWin(): void {
        if (!gate.signal.aborted) gate.abort(new Error('answered on the phone'))
      },
    }
  }

  const snapshot = (): OverlayRelaySnapshot => {
    if (origin === null) {
      return { state: 'disabled', waiting: false, connected: false, relayOrigin: null, pairUrl: null, qrSvg: null, error: null }
    }
    if (pairing === null) {
      return { state: 'off', waiting: false, connected: false, relayOrigin: origin.origin, pairUrl: null, qrSvg: null, error: null }
    }
    const tunnelState: RemoteRelayTunnelState = tunnel?.snapshot().state ?? 'connecting'
    const pairUrl = pairing.token === null
      ? null
      : `${origin.href}r/${pairing.pairId}/mobile/login?token=${pairing.token}`
    const connected = Date.now() - presence.lastSeen < 15_000
    return {
      state: pairing.token === null ? 'failed' : tunnelState,
      waiting: tunnelState === 'connecting' || tunnelState === 'ready',
      connected: tunnelState === 'ready' && connected,
      relayOrigin: origin.origin,
      pairUrl,
      qrSvg: null,
      error: pairing.token === null ? 'browser session token unavailable' : (tunnel?.snapshot().error ?? null),
    }
  }

  const startTunnel = (): void => {
    if (origin === null || pairing === null || pairing.token === null) return
    tunnel = new RemoteRelayTunnel({
      relayUrl: `${origin.protocol === 'https:' ? 'wss' : 'ws'}://${origin.host}/host`,
      pairId: pairing.pairId,
      secret: pairing.secret,
      targetPort: ctx.webServer.port,
      log: message => ctx.logger.info(`dsh-overlay-custom: ${message}`),
    })
    tunnel.start()
  }

  const enable = (): OverlayRelaySnapshot => {
    if (origin === null) return snapshot()
    if (pairing === null) pairing = mintPairing(ctx, origin)
    if (tunnel === null) startTunnel()
    return snapshot()
  }

  async function withQr(view: OverlayRelaySnapshot): Promise<OverlayRelaySnapshot> {
    if (view.pairUrl === null) return view
    try {
      const qrSvg = await QRCode.toString(view.pairUrl, {
        type: 'svg',
        margin: 1,
        width: 232,
        errorCorrectionLevel: 'M',
      })
      return { ...view, qrSvg }
    } catch {
      return view
    }
  }

  ctx.effect(() => () => {
    tunnel?.dispose()
    tunnel = null
  }, 'dsh-overlay-custom: relay tunnel lifetime')

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
      const gate = gateDesktopAsk(request)
      const desktop: Promise<string> = typeof next === 'function' ? Promise.resolve(next()) : Promise.resolve('unavailable')
      try {
        const handle = interruptions.hold({
          sessionId: sessionKeyOf(request),
          toolName: typeof request.toolName === 'string' ? request.toolName : 'unknown-tool',
          callId: typeof request.callId === 'string' ? request.callId : null,
          reason: typeof request.reason === 'string' ? request.reason : null,
          questions: null,
        }, request.signal)
        return new Promise<string>((resolve, rejectHold) => {
          let done = false
          const finish = (settle: () => void): void => {
            if (done) return
            done = true
            handle.retire()
            settle()
          }
          void handle.settled.then(settlement => {
            if (settlement.kind === 'phone-decision') {
              gate.abortPhoneWin()
              finish(() => { resolve(settlement.decision === 'allow' ? 'allowed-once' : 'rejected') })
            } else if (settlement.kind === 'abort') {
              finish(() => { resolve('cancelled') })
            } else {
              handle.retire()
            }
          })
          void desktop.then(outcome => { finish(() => { resolve(outcome) }) }, cause => { finish(() => { rejectHold(cause) }) })
        })
      } catch (cause) {
        ctx.logger.error(`dsh-overlay-custom: approval phone hold failed: ${cause instanceof Error ? cause.message : String(cause)}`)
        return desktop
      }
    },
    { prepend: true },
  ), 'dsh-overlay-custom: remote approval hold')

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
        return typeof next === 'function' ? Promise.resolve(next()) : Promise.reject(new Error('no user-questions answerer accepted the request'))
      }
      const gate = gateDesktopAsk(request)
      const desktop = typeof next === 'function' ? Promise.resolve(next()) : undefined
      const handle = interruptions.hold({
        sessionId: sessionKeyOf(request),
        toolName: 'ask-user',
        callId: null,
        reason: null,
        questions,
      }, request.signal)
      return new Promise<{ answers: readonly { id: string; selected: readonly string[]; custom?: string }[] }>((resolve, rejectHold) => {
        let done = false
        const finish = (settle: () => void): void => {
          if (done) return
          done = true
          handle.retire()
          settle()
        }
        void handle.settled.then(settlement => {
          if (settlement.kind === 'phone-answer') {
            gate.abortPhoneWin()
            finish(() => { resolve({ answers: settlement.answers }) })
          } else if (settlement.kind === 'abort') {
            finish(() => { rejectHold(new Error('question aborted before either side answered')) })
          } else {
            handle.retire()
          }
        })
        if (desktop !== undefined) {
          void desktop.then(answer => { finish(() => { resolve(answer) }) }, cause => { finish(() => { rejectHold(cause) }) })
        }
      })
    },
    { prepend: true },
  ), 'dsh-overlay-custom: remote question hold')

  registerMobileApi({
    ctx,
    interruptions,
    controlCache,
    clientBinding,
    relayActive: () => tunnel?.snapshot().state === 'ready',
    presence,
  })

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/mobile/login',
    handler: (req, res) => {
      if (req.method !== 'GET') {
        res.writeHead(405, { allow: 'GET', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      const incoming = new URL(req.url ?? '/mobile/login', 'http://localhost')
      const authenticated = ctx.connection.authorizeIndex({
        method: 'GET',
        url: `/${incoming.search}`,
        headers: req.headers,
      }, {
        writeHead: (status, headers = {}) => res.writeHead(status, {
          ...headers,
          ...(status === 303 ? { location: '/mobile/' } : {}),
        }),
        end: body => res.end(body),
      })
      if (authenticated) {
        res.writeHead(303, { location: '/mobile/', 'cache-control': 'no-store' })
        res.end()
      }
    },
  }), 'dsh-overlay-custom: mobile login')

  ctx.effect(() => ctx.webServer.register({
    kind: 'prefix',
    path: '/mobile',
    handler: (req, res) => { void serveMobilePage(req, res) },
  }), 'dsh-overlay-custom: mobile page')

  async function serveMobilePage(req: IncomingMessage, res: ServerResponse): Promise<void> {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
      res.end('method not allowed')
      return
    }
    const url = new URL(req.url ?? '/mobile/', 'http://localhost')
    let relative = url.pathname.replace(/^\/mobile\/?/u, '')
    if (relative === '') relative = 'mobile.html'
    const normalized = normalize(relative).split(sep).join('/')
    if (normalized.startsWith('..') || normalized.includes('../') || normalized.startsWith('/')
      || (normalized !== 'mobile.html' && normalized !== 'mobile.js' && normalized !== 'mobile.css')) {
      res.writeHead(404, { 'cache-control': 'no-store' })
      res.end('not found')
      return
    }
    try {
      const body = await readFile(join(PUBLIC_ROOT, normalized))
      const type = normalized.endsWith('.js')
        ? 'text/javascript; charset=utf-8'
        : normalized.endsWith('.css')
          ? 'text/css; charset=utf-8'
          : 'text/html; charset=utf-8'
      res.writeHead(200, {
        'content-type': type,
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
        ...(normalized.endsWith('.html') ? { 'content-security-policy': MOBILE_CSP } : {}),
        'content-length': String(body.byteLength),
      })
      res.end(req.method === 'HEAD' ? undefined : body)
    } catch {
      res.writeHead(404, { 'cache-control': 'no-store' })
      res.end('not found')
    }
  }

  const reject = (req: IncomingMessage, res: ServerResponse): boolean =>
    rejectUnauthorized(req, res, ctx.connection.requestRejection(req))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/remote/state',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      void withQr(snapshot()).then(view => { writeJson(res, 200, view) })
    },
  }), 'dsh-overlay-custom: remote state')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/remote/origin',
    handler: (req, res) => {
      if (reject(req, res)) return
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
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({} as Record<string, unknown>))
        const raw = typeof body.origin === 'string' ? body.origin : ''
        origin = await persistRelayOrigin(resolveDshHome(), raw)
        pairing = null
        tunnel?.dispose()
        tunnel = null
        writeJson(res, 200, await withQr(enable()))
      })()
    },
  }), 'dsh-overlay-custom: remote origin')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/remote/pairing/open',
    handler: (req, res) => {
      if (reject(req, res)) return
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
      void withQr(enable()).then(view => { writeJson(res, 200, view) })
    },
  }), 'dsh-overlay-custom: remote pairing')
}
