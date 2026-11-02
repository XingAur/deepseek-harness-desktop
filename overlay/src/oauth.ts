import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { resolveDshHome } from './dsh-home.ts'
import { readJsonBody, rejectUnauthorized, sameOriginMutatingRequest, writeJson } from './http.ts'
import { readOAuthCredential } from './live-models.ts'
import { modelProxyErrorCode, type ModelProxyManager } from './model-proxy-manager.ts'
import {
  isOauthLoginProvider, oauthCredentialKey, type OauthLoginProvider,
} from './oauth-providers.ts'

interface AuthNotice {
  readonly message?: string
  readonly url?: string
  readonly code?: string
}

interface AuthPrompt {
  readonly kind?: string
  readonly message?: string
  readonly placeholder?: string
  readonly options?: readonly { readonly id: string; readonly label: string }[]
}

interface AuthLike {
  readonly list?: () => readonly { readonly key: string; readonly methods: readonly { readonly id: string }[] }[]
  readonly begin: (request: {
    readonly key: string
    readonly method?: string
    readonly interaction: {
      notify: (notice: AuthNotice) => void
      prompt: (prompt: AuthPrompt) => Promise<string>
    }
    readonly signal?: AbortSignal
  }) => Promise<{ readonly status?: string }>
  readonly cancel: (key: string) => void
}

export interface OauthView {
  readonly phase: 'idle' | 'running' | 'need-prompt' | 'authorized' | 'cancelled' | 'failed' | 'unavailable'
  readonly provider: string
  readonly message: string | null
  readonly url: string | null
  readonly code: string | null
  readonly prompt: {
    readonly message: string
    readonly placeholder: string | null
    readonly options: readonly { readonly id: string; readonly label: string }[]
  } | null
  readonly error: string | null
}

interface Session {
  view: OauthView
  controller: AbortController | null
  answer: ((value: string) => void) | null
  decline: (() => void) | null
}

function emptyView(provider: string, phase: OauthView['phase'] = 'idle'): OauthView {
  return { phase, provider, message: null, url: null, code: null, prompt: null, error: null }
}

function authorizationOf(ctx: Context): AuthLike | undefined {
  const bag = ctx as Context & { get?: (name: string) => unknown }
  const value = typeof bag.get === 'function' ? bag.get('authorization') : undefined
  if (value === null || typeof value !== 'object') return undefined
  const auth = value as AuthLike
  if (typeof auth.begin !== 'function' || typeof auth.cancel !== 'function') return undefined
  return auth
}

function parseProvider(body: Record<string, unknown>, query: string | null): OauthLoginProvider | null {
  const raw = typeof body.provider === 'string' ? body.provider : query
  if (typeof raw !== 'string' || !isOauthLoginProvider(raw)) return null
  return raw
}

/** Optional official authorization seam: missing API must not fail desktop boot. */
export function applyOverlayOauth(ctx: Context, modelNetwork: ModelProxyManager): void {
  const sessions = new Map<string, Session>()
  const reject = (req: IncomingMessage, res: ServerResponse): boolean =>
    rejectUnauthorized(req, res, ctx.connection.requestRejection(req))

  const sessionOf = (provider: OauthLoginProvider): Session => {
    const existing = sessions.get(provider)
    if (existing !== undefined) return existing
    const created: Session = {
      view: emptyView(provider),
      controller: null,
      answer: null,
      decline: null,
    }
    sessions.set(provider, created)
    return created
  }

  const guard = (req: IncomingMessage, res: ServerResponse, mutating: boolean): boolean => {
    if (reject(req, res)) return true
    if (mutating && !sameOriginMutatingRequest(req)) {
      res.writeHead(403, { 'cache-control': 'no-store' })
      res.end('forbidden')
      return true
    }
    return false
  }

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/oauth/status',
    handler: (req, res) => {
      if (guard(req, res, false)) return
      if (req.method !== 'GET') {
        res.writeHead(405, { allow: 'GET', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      const url = new URL(req.url ?? '', 'http://127.0.0.1')
      const provider = parseProvider({}, url.searchParams.get('provider'))
      if (provider === null) {
        writeJson(res, 400, emptyView('', 'failed'))
        return
      }
      if (authorizationOf(ctx) === undefined) {
        writeJson(res, 200, emptyView(provider, 'unavailable'))
        return
      }
      const session = sessionOf(provider)
      const phase = session.view.phase
      if (phase === 'idle' || phase === 'cancelled' || phase === 'failed') {
        void readOAuthCredential(resolveDshHome(), provider).then(credential => {
          if (credential !== null && session.view.phase === phase) {
            session.view = emptyView(provider, 'authorized')
          }
          writeJson(res, 200, session.view)
        })
        return
      }
      writeJson(res, 200, session.view)
    },
  }), 'dsh-overlay-custom: oauth status')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/oauth/begin',
    handler: (req, res) => {
      if (guard(req, res, true)) return
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({} as Record<string, unknown>))
        const provider = parseProvider(body, null)
        if (provider === null) {
          writeJson(res, 400, { ok: false, error: 'unknown-provider' })
          return
        }
        const auth = authorizationOf(ctx)
        if (auth === undefined) {
          writeJson(res, 200, emptyView(provider, 'unavailable'))
          return
        }
        const session = sessionOf(provider)
        if (session.view.phase === 'running' || session.view.phase === 'need-prompt') {
          writeJson(res, 200, session.view)
          return
        }
        session.controller?.abort()
        const controller = new AbortController()
        session.controller = controller
        session.answer = null
        session.decline = null
        session.view = {
          ...emptyView(provider, 'running'),
          message: 'starting',
        }
        writeJson(res, 202, session.view)
        void modelNetwork.run(() => auth.begin({
          key: oauthCredentialKey(provider),
          method: 'oauth',
          signal: controller.signal,
          interaction: {
            notify: (notice) => {
              session.view = {
                ...session.view,
                phase: 'running',
                message: typeof notice.message === 'string' ? notice.message : session.view.message,
                url: typeof notice.url === 'string' ? notice.url : session.view.url,
                code: typeof notice.code === 'string' ? notice.code : session.view.code,
                error: null,
              }
            },
            prompt: (prompt) => {
              const options = Array.isArray(prompt.options) ? prompt.options : []
              const oauthOption = options.find(option => option.id === 'oauth' || option.id === 'chatgpt')
              if (oauthOption !== undefined) return Promise.resolve(oauthOption.id)
              return new Promise<string>((resolve, reject) => {
                session.answer = resolve
                session.decline = () => { reject(new Error('declined')) }
                session.view = {
                  ...session.view,
                  phase: 'need-prompt',
                  message: typeof prompt.message === 'string' ? prompt.message : session.view.message,
                  prompt: {
                    message: typeof prompt.message === 'string' ? prompt.message : '',
                    placeholder: typeof prompt.placeholder === 'string' ? prompt.placeholder : null,
                    options: options.map(option => ({ id: option.id, label: option.label })),
                  },
                }
              })
            },
          },
        })).then((outcome) => {
          const status = outcome.status === 'authorized' || outcome.status === 'cancelled'
            ? outcome.status
            : 'failed'
          session.view = {
            ...session.view,
            phase: status,
            prompt: null,
            error: status === 'failed' ? (outcome.status ?? 'failed') : null,
          }
          session.controller = null
          session.answer = null
          session.decline = null
        }, (cause: unknown) => {
          const message = cause instanceof Error ? cause.message : String(cause)
          session.view = {
            ...emptyView(provider, message.includes('no authorization flow') ? 'unavailable' : 'failed'),
            error: message.includes('no authorization flow') ? 'unavailable' : modelProxyErrorCode(cause),
          }
          session.controller = null
          session.answer = null
          session.decline = null
        })
      })()
    },
  }), 'dsh-overlay-custom: oauth begin')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/oauth/answer',
    handler: (req, res) => {
      if (guard(req, res, true)) return
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({} as Record<string, unknown>))
        const provider = parseProvider(body, null)
        if (provider === null) {
          writeJson(res, 400, { ok: false })
          return
        }
        const session = sessionOf(provider)
        if (body.decline === true) {
          session.decline?.()
          session.controller?.abort()
          session.view = emptyView(provider, 'cancelled')
          writeJson(res, 200, session.view)
          return
        }
        const text = typeof body.text === 'string' ? body.text : ''
        if (session.answer === null) {
          writeJson(res, 409, session.view)
          return
        }
        session.answer(text)
        session.answer = null
        session.decline = null
        session.view = { ...session.view, phase: 'running', prompt: null }
        writeJson(res, 200, session.view)
      })()
    },
  }), 'dsh-overlay-custom: oauth answer')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/oauth/cancel',
    handler: (req, res) => {
      if (guard(req, res, true)) return
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({} as Record<string, unknown>))
        const provider = parseProvider(body, null)
        if (provider === null) {
          writeJson(res, 400, { ok: false })
          return
        }
        const auth = authorizationOf(ctx)
        const key = oauthCredentialKey(provider)
        try {
          auth?.cancel(key)
        } catch {
          // Official cancel must never take down overlay routes.
        }
        sessionOf(provider).controller?.abort()
        sessionOf(provider).view = emptyView(provider, 'cancelled')
        writeJson(res, 200, sessionOf(provider).view)
      })()
    },
  }), 'dsh-overlay-custom: oauth cancel')
}
