import { randomBytes } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join } from 'node:path'
import { CatalogStore, type CatalogPlugin, type DesktopTarget } from './catalog'
import { PluginCommandService, type PluginAction } from './plugin-command'

interface Preview { token: string; plugin: CatalogPlugin; action: PluginAction; expires: number }
interface Route { kind: 'exact'; path: string; handler(req: IncomingMessage, res: ServerResponse): void | Promise<void> }
export interface HostContextLike {
  webServer: { register(route: Route): () => void }
  effect(register: () => (() => void), label: string): void
  logger?: { error(value: unknown): void }
}

export function registerMarketRoutes(ctx: HostContextLike): () => void {
  const home = process.env.DSH_HOME
  const dshBin = process.env.DSH_DESKTOP_DSH_BIN
  const catalogSource = process.env.DSH_DESKTOP_CATALOG_PATH
  const dshVersion = process.env.DSH_DESKTOP_DSH_VERSION
  if (!home || !dshBin || !catalogSource || !dshVersion) throw new Error('Desktop market requires managed Runtime environment variables')
  const target: DesktopTarget = process.platform === 'darwin' ? 'darwin-aarch64' : 'windows-x86_64'
  const catalog = new CatalogStore({ source: catalogSource, cachePath: join(home, 'desktop', 'catalog.json'), target, dshVersion })
  const commands = new PluginCommandService(dshBin, join(home, 'profiles', 'desktop'))
  const previews = new Map<string, Preview>()

  const routes: Route[] = [
    { kind: 'exact', path: '/api/desktop/community/plugins', handler: async (_req, res) => {
      try {
        const [current, installed] = await Promise.all([catalog.load(), commands.installed()])
        const versions = new Map(installed.map((item) => [item.packageName, item.version]))
        json(res, 200, current.plugins.map((plugin) => ({
          ...plugin,
          installed: versions.has(plugin.packageName),
          updateAvailable: versions.has(plugin.packageName) && versions.get(plugin.packageName) !== plugin.version,
        })))
      } catch (cause) { json(res, 503, { error: message(cause) }) }
    }},
    { kind: 'exact', path: '/api/desktop/community/preview', handler: async (req, res) => {
      if (!safeWrite(req, res)) return
      try {
        const body = await readJson(req) as { pluginId?: unknown; action?: unknown }
        if (typeof body.pluginId !== 'string' || !isAction(body.action)) throw new Error('插件操作请求无效')
        const plugin = (await catalog.load()).plugins.find((candidate) => candidate.id === body.pluginId)
        if (plugin === undefined) throw new Error('插件不在当前签名精选目录中')
        const now = Date.now()
        for (const [key, stale] of previews) if (stale.expires < now) previews.delete(key)
        const token = randomBytes(32).toString('base64url')
        previews.set(token, { token, plugin, action: body.action, expires: Date.now() + 120_000 })
        json(res, 200, { token })
      } catch (cause) { json(res, 400, { error: message(cause) }) }
    }},
    { kind: 'exact', path: '/api/desktop/community/execute', handler: async (req, res) => {
      if (!safeWrite(req, res)) return
      try {
        const body = await readJson(req) as { token?: unknown }
        if (typeof body.token !== 'string') throw new Error('确认令牌无效')
        const preview = previews.get(body.token)
        previews.delete(body.token)
        if (preview === undefined || preview.expires < Date.now()) throw new Error('确认已过期，请重新操作')
        json(res, 200, { operationId: commands.start(preview.action, preview.plugin) })
      } catch (cause) { json(res, 409, { error: message(cause) }) }
    }},
    { kind: 'exact', path: '/api/desktop/community/events', handler: (req, res) => {
      const operationId = new URL(req.url ?? '/', 'http://127.0.0.1').searchParams.get('operationId') ?? ''
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive' })
      const stop = commands.subscribe(operationId, (event) => {
        res.write(`data: ${JSON.stringify(event)}\n\n`)
        if (event.done) { stop(); res.end() }
      })
      req.once('close', stop)
    }},
    { kind: 'exact', path: '/api/desktop/community/cancel', handler: async (req, res) => {
      if (!safeWrite(req, res)) return
      try {
        const body = await readJson(req) as { operationId?: unknown }
        if (typeof body.operationId !== 'string') throw new Error('操作 ID 无效')
        await commands.cancel(body.operationId)
        json(res, 200, {})
      } catch (cause) { json(res, 404, { error: message(cause) }) }
    }},
  ]
  const disposers = routes.map((route) => ctx.webServer.register(route))
  return () => { for (const dispose of disposers.reverse()) dispose() }
}

export function safeWrite(req: IncomingMessage, res: ServerResponse): boolean {
  if (req.method !== 'POST' || !String(req.headers['content-type'] ?? '').toLowerCase().startsWith('application/json')) {
    json(res, 415, { error: '写操作需要 application/json POST' }); return false
  }
  const origin = req.headers.origin
  const host = req.headers.host
  if (typeof origin !== 'string') { json(res, 403, { error: '写操作缺少同源证明' }); return false }
  try {
    const parsed = new URL(origin)
    if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' || typeof host !== 'string' || parsed.host !== host) throw new Error()
  } catch { json(res, 403, { error: '写操作来源无效' }); return false }
  return true
}

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    const buffer = Buffer.from(chunk)
    size += buffer.length
    if (size > 32 * 1024) throw new Error('请求体过大')
    chunks.push(buffer)
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

function isAction(value: unknown): value is PluginAction { return value === 'install' || value === 'update' || value === 'remove' }
function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause) }
function json(res: ServerResponse, status: number, body: unknown) { res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)) }
