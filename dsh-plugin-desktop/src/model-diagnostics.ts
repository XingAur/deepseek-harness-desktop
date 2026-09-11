/** Model diagnostics routes served on the Host webServer. */

import { resolveDshHome } from '@deepseek-ai/dsh-home-paths'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { probeModels, probeProvider, readModelDiagnosticsState, selectModel } from './model-diagnostics-core.ts'

const MAX_BODY_BYTES = 64 * 1024

function json(res: import('node:http').ServerResponse, status: number, value: unknown): void {
  const body = `${JSON.stringify(value)}\n`
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'content-length': String(Buffer.byteLength(body)),
  })
  res.end(body)
}

function sameOriginMutatingRequest(req: import('node:http').IncomingMessage): boolean {
  const origin = req.headers.origin
  const host = req.headers.host
  if (typeof origin !== 'string' || origin === '') return false
  try {
    return new URL(origin).host === (host ?? '')
  } catch {
    return false
  }
}

async function readJsonBody(req: import('node:http').IncomingMessage): Promise<Record<string, unknown>> {
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

/**
 * Register the model-diagnostics JSON API behind the standard connection
 * fence. All reads and probes delegate to the shared core against the active
 * DSH home, so the window and the routes can never disagree.
 */
export function registerModelDiagnostics(ctx: Context): void {
  const home = resolveDshHome()

  const reject = (req: import('node:http').IncomingMessage, res: import('node:http').ServerResponse): boolean => {
    const rejection = ctx.connection.requestRejection(req)
    if (rejection === undefined) return false
    res.writeHead(rejection)
    res.end(rejection === 401 ? 'unauthorized' : 'forbidden')
    return true
  }

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/models/state',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        res.writeHead(405, { allow: 'GET, HEAD', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      readModelDiagnosticsState(home).then(
        state => json(res, 200, state),
        cause => {
          ctx.logger.error(`dsh-plugin-desktop: model state read failed: ${cause instanceof Error ? cause.message : String(cause)}`)
          json(res, 503, { error: 'state-unavailable' })
        },
      )
    },
  }), 'dsh-plugin-desktop: model diagnostics state route')

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
          const body = await readJsonBody(req).catch(() => ({}) as Record<string, unknown>)
          const provider = typeof body.settingsNs === 'string' && body.settingsNs === 'llm-pi-ai'
            && typeof body.provider === 'string'
            ? body.provider
            : null
          const outcome = provider === null ? await probeModels(home) : await probeProvider(home, provider)
          if (!outcome.ok) {
            ctx.logger.warn(`dsh-plugin-desktop: model probe reported: ${outcome.error ?? 'unknown'}`)
          }
          json(res, 200, outcome)
        } catch (cause) {
          ctx.logger.error(`dsh-plugin-desktop: model probe failed: ${cause instanceof Error ? cause.message : String(cause)}`)
          json(res, 502, { ok: false, status: 0, models: [], error: 'probe-failed' })
        }
      })()
    },
  }), 'dsh-plugin-desktop: model diagnostics probe route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/models/select',
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
      readJsonBody(req).then(
        async body => {
          const model = body.model
          if (typeof model !== 'string') {
            json(res, 400, { error: 'invalid-request' })
            return
          }
          json(res, 200, await selectModel(home, model))
        },
        cause => {
          ctx.logger.error(`dsh-plugin-desktop: model select failed: ${cause instanceof Error ? cause.message : String(cause)}`)
          json(res, 400, { error: 'invalid-body' })
        },
      )
    },
  }), 'dsh-plugin-desktop: model diagnostics select route')
}
