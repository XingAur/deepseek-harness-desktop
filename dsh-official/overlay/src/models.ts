import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { resolveDshHome } from './dsh-home.ts'
import { probeModels, probeProvider } from './model-core.ts'
import { foldPickerModels, isValidProviderId, readConfiguredModels, writeModelSelection } from './model-selection.ts'
import { fetchLiveModelIds, readOAuthCredential } from './live-models.ts'
import type { ModelProxyManager } from './model-proxy-manager.ts'
import { isOauthLoginProvider } from './oauth-providers.ts'
import { readJsonBody, rejectUnauthorized, sameOriginMutatingRequest, writeJson } from './http.ts'

/** Host routes for provider-card test/fetch against the active DSH home. */
export function applyOverlayModels(ctx: Context, modelNetwork: ModelProxyManager): void {
  const home = resolveDshHome()
  const reject = (req: IncomingMessage, res: ServerResponse): boolean =>
    rejectUnauthorized(req, res, ctx.connection.requestRejection(req))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/models/probe',
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
        try {
          const body = await readJsonBody(req).catch(() => ({} as Record<string, unknown>))
          const provider = typeof body.settingsNs === 'string' && body.settingsNs === 'llm-pi-ai'
            && typeof body.provider === 'string'
            ? body.provider
            : null
          const outcome = await modelNetwork.run(() => provider === null ? probeModels(home) : probeProvider(home, provider))
          writeJson(res, 200, outcome)
        } catch (cause) {
          ctx.logger.error(`dsh-overlay-custom: model probe failed: ${cause instanceof Error ? cause.message : String(cause)}`)
          writeJson(res, 502, { ok: false, status: 0, models: [], error: 'probe-failed' })
        }
      })()
    },
  }), 'dsh-overlay-custom: model probe')

  const providerOf = (body: Record<string, unknown>, query: string | null): string | null => {
    const raw = typeof body.provider === 'string' ? body.provider : query
    return typeof raw === 'string' && isValidProviderId(raw) ? raw : null
  }

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/models/live',
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
        const provider = providerOf(body, null)
        if (provider === null) {
          writeJson(res, 400, { ok: false, live: [], configured: [], error: 'invalid-provider' })
          return
        }
        const configured = await readConfiguredModels(home, provider)
        if (!isOauthLoginProvider(provider)) {
          writeJson(res, 200, { ok: true, live: [], configured })
          return
        }
        const credential = await readOAuthCredential(home, provider)
        if (credential === null) {
          writeJson(res, 200, { ok: false, live: [], configured, error: 'no-credential' })
          return
        }
        const fetched = await modelNetwork.run(() => fetchLiveModelIds(provider, credential))
        if (fetched.ids.length === 0) {
          writeJson(res, 200, { ok: false, live: [], configured, error: fetched.error ?? 'live-empty' })
          return
        }
        const folded = foldPickerModels(provider, fetched.ids).map(entry => entry.id)
        writeJson(res, 200, { ok: true, live: folded, configured: folded, applied: false })
      })().catch(() => {
        writeJson(res, 502, { ok: false, live: [], configured: [], error: 'network' })
      })
    },
  }), 'dsh-overlay-custom: live models')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/models/selection',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'GET' && req.method !== 'POST') {
        res.writeHead(405, { allow: 'GET, POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      if (req.method === 'GET') {
        const url = new URL(req.url ?? '', 'http://127.0.0.1')
        const provider = providerOf({}, url.searchParams.get('provider'))
        if (provider === null) {
          writeJson(res, 400, { ok: false, models: [], error: 'invalid-provider' })
          return
        }
        void readConfiguredModels(home, provider)
          .then(models => { writeJson(res, 200, { ok: true, models }) })
          .catch(() => { writeJson(res, 500, { ok: false, models: [], error: 'read-failed' }) })
        return
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { 'cache-control': 'no-store' })
        res.end('forbidden')
        return
      }
      void (async () => {
        const body = await readJsonBody(req).catch(() => ({} as Record<string, unknown>))
        const provider = providerOf(body, null)
        if (provider === null) {
          writeJson(res, 400, { ok: false, models: [], error: 'invalid-provider' })
          return
        }
        if (!Array.isArray(body.models)) {
          writeJson(res, 400, { ok: false, models: [], error: 'models-must-be-array' })
          return
        }
        try {
          const models = await writeModelSelection(home, provider, body.models)
          writeJson(res, 200, { ok: true, models })
        } catch (cause) {
          ctx.logger.warn(`dsh-overlay-custom: selection write failed: ${cause instanceof Error ? cause.message : String(cause)}`)
          writeJson(res, 400, { ok: false, models: [], error: 'write-failed' })
        }
      })()
    },
  }), 'dsh-overlay-custom: model selection')
}
