/** Authenticated model-network settings; probes do not mutate persisted configuration. */

import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import type {} from '@deepseek-ai/dsh-llm'
import { resolveDshHome } from './dsh-home.ts'
import { readJsonBody, rejectUnauthorized, sameOriginMutatingRequest, writeJson } from './http.ts'
import { ModelProxyManager, modelProxyErrorCode } from './model-proxy-manager.ts'

/** Install one reversible model-call scope and return it to model/auth UI integrations. */
export function applyOverlayProxyApi(ctx: Context): ModelProxyManager {
  const manager = new ModelProxyManager(resolveDshHome())
  const reject = (req: IncomingMessage, res: ServerResponse): boolean =>
    rejectUnauthorized(req, res, ctx.connection.requestRejection(req))

  ctx.effect(() => () => {
    void manager.dispose().catch(() => { ctx.logger.warn('dsh-overlay-custom: model proxy cleanup failed') })
  }, 'dsh-overlay-custom: model network lifecycle')
  ctx.on('llm/stream', (_options, next) => manager.stream(next))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/proxy/config',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method === 'GET') {
        void manager.view().then(view => {
          writeJson(res, 200, { ok: true, ...view })
        }).catch(cause => {
          writeJson(res, 500, { ok: false, error: modelProxyErrorCode(cause) })
        })
        return
      }
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'GET, POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { 'cache-control': 'no-store' })
        res.end('forbidden')
        return
      }
      void (async () => {
        let body: Record<string, unknown>
        try { body = await readJsonBody(req) } catch {
          writeJson(res, 400, { ok: false, error: 'invalid-config' })
          return
        }
        try {
          const view = await manager.save(body)
          writeJson(res, 200, { ok: true, ...view })
        } catch (cause) {
          writeJson(res, 400, { ok: false, error: modelProxyErrorCode(cause) })
        }
      })()
    },
  }), 'dsh-overlay-custom: proxy config')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/proxy/test',
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
        let body: Record<string, unknown>
        try { body = await readJsonBody(req) } catch {
          writeJson(res, 400, { ok: false, error: 'invalid-config' })
          return
        }
        try {
          writeJson(res, 200, await manager.test(body))
        } catch (cause) {
          writeJson(res, 500, { ok: false, error: modelProxyErrorCode(cause) })
        }
      })()
    },
  }), 'dsh-overlay-custom: proxy test')
  return manager
}
