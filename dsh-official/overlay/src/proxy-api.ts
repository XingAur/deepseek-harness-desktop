import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { resolveDshHome } from './dsh-home.ts'
import { readJsonBody, rejectUnauthorized, sameOriginMutatingRequest, writeJson } from './http.ts'
import {
  installModelProxy, parseModelProxyUrl, proxyDiagnosticOrigin, readModelProxyConfig, writeModelProxyConfig,
} from './model-proxy.ts'

const TEST_TIMEOUT_MS = 8_000
const TEST_URL = 'https://api.x.ai/v1/models'

async function testProxy(url: string): Promise<{ readonly ok: boolean; readonly error: string | null }> {
  if (url === '') return { ok: false, error: 'no-proxy' }
  try {
    parseModelProxyUrl(url)
  } catch {
    return { ok: false, error: 'invalid-url' }
  }
  try {
    const undici = await import('undici') as {
      ProxyAgent: new (opts: { uri: string }) => object
      request: (url: string, opts: object) => Promise<{ statusCode: number; body: { dump: () => Promise<void> } }>
    }
    const dispatcher = new undici.ProxyAgent({ uri: url })
    const response = await undici.request(TEST_URL, {
      method: 'GET',
      dispatcher,
      headersTimeout: TEST_TIMEOUT_MS,
      bodyTimeout: TEST_TIMEOUT_MS,
      headers: { accept: 'application/json' },
    })
    await response.body.dump()
    return { ok: true, error: null }
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause)
    if (message.includes('ECONNREFUSED')) return { ok: false, error: 'proxy-refused' }
    if (message.includes('407') || /proxy.*auth/i.test(message)) return { ok: false, error: 'proxy-auth' }
    if (message.includes('ENOTFOUND')) return { ok: false, error: 'dns' }
    if (message.includes('timeout') || message.includes('Timeout')) return { ok: false, error: 'timeout' }
    if (message.includes('certificate') || message.includes('SSL') || message.includes('TLS')) {
      return { ok: false, error: 'tls' }
    }
    return { ok: false, error: 'unknown' }
  }
}

/** JSON API for overlay model-proxy settings (never logs credentials). */
export function applyOverlayProxyApi(ctx: Context): void {
  const home = resolveDshHome()
  const reject = (req: IncomingMessage, res: ServerResponse): boolean =>
    rejectUnauthorized(req, res, ctx.connection.requestRejection(req))
  let uninstall: (() => Promise<void>) | undefined
  ctx.effect(() => {
    void readModelProxyConfig(home).then(async config => {
      uninstall = await installModelProxy(config, message => { ctx.logger.info(message) })
    }).catch(error => {
      ctx.logger.warn(`dsh-overlay-custom: model proxy install skipped: ${error instanceof Error ? error.message : String(error)}`)
    })
    return () => { void uninstall?.() }
  }, 'dsh-overlay-custom: model proxy boot')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/proxy/config',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method === 'GET') {
        void readModelProxyConfig(home).then(config => {
          writeJson(res, 200, {
            ok: true,
            url: config.url,
            origin: proxyDiagnosticOrigin(config.url),
            providers: config.providers,
          })
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
        const body = await readJsonBody(req).catch(() => ({} as Record<string, unknown>))
        try {
          const saved = await writeModelProxyConfig(home, {
            url: typeof body.url === 'string' ? body.url : '',
            providers: Array.isArray(body.providers)
              ? body.providers.filter((item): item is string => typeof item === 'string')
              : ['xai', 'openai-codex'],
          })
          await uninstall?.()
          uninstall = await installModelProxy(saved, message => { ctx.logger.info(message) })
          writeJson(res, 200, {
            ok: true,
            url: saved.url,
            origin: proxyDiagnosticOrigin(saved.url),
            providers: saved.providers,
          })
        } catch (cause) {
          writeJson(res, 400, { ok: false, error: cause instanceof Error ? cause.message : 'write-failed' })
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
        const body = await readJsonBody(req).catch(() => ({} as Record<string, unknown>))
        const url = typeof body.url === 'string' ? body.url : (await readModelProxyConfig(home)).url
        writeJson(res, 200, await testProxy(url))
      })()
    },
  }), 'dsh-overlay-custom: proxy test')
}
