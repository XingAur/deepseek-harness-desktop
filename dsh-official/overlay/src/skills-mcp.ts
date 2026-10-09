/** Host routes and startup MCP mounts for the official Desktop overlay. */

import type { IncomingMessage, ServerResponse } from 'node:http'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { resolveDshHome } from './dsh-home.ts'
import { readJsonBody, rejectUnauthorized, sameOriginMutatingRequest, writeJson } from './http.ts'
import { probeOverlayMcpServer } from './mcp-probe.ts'
import {
  mcpClientConfig,
  mergeOverlayMcpServer,
  parseOverlayMcpServer,
  projectOverlayMcpServer,
  readOverlayMcpServers,
  writeOverlayMcpServers,
  type OverlayMcpServerWrite,
} from './mcp-state.ts'

const SKILLS_PATH = '/api/desktop/skills'
const MCP_PATH = '/api/desktop/mcp'
const MCP_TEST_PATH = '/api/desktop/mcp/test'

function method(res: ServerResponse, allow: string): void {
  res.writeHead(405, { allow, 'cache-control': 'no-store' })
  res.end('method not allowed')
}

function forbidden(req: IncomingMessage, res: ServerResponse): boolean {
  if (sameOriginMutatingRequest(req)) return false
  res.writeHead(403, { 'cache-control': 'no-store' })
  res.end('forbidden')
  return true
}

function rejectFor(ctx: Context, req: IncomingMessage, res: ServerResponse): boolean {
  return rejectUnauthorized(req, res, ctx.connection.requestRejection(req))
}

async function listSkills(ctx: Context): Promise<Record<string, unknown>> {
  const registry = ctx.get('skills') as undefined | {
    list(options: { cwd: string; scope?: unknown }): Promise<readonly {
      name: string
      description: string
      whenToUse?: string
      invocation: { modelInvocable: boolean; userInvocable: boolean }
      source: string
    }[]>
  }
  if (registry === undefined) return { available: false, skills: [] }
  const presets = ctx.get('agentPresets') as undefined | {
    acquireScope(id?: string): Promise<{ key: unknown; [Symbol.asyncDispose](): Promise<void> }>
  }
  let lease: Awaited<ReturnType<NonNullable<typeof presets>['acquireScope']>> | undefined
  try {
    lease = await presets?.acquireScope()
    const skills = await registry.list({ cwd: process.cwd(), ...(lease === undefined ? {} : { scope: lease.key }) })
    return {
      available: true,
      skills: skills.map(skill => ({
        name: skill.name,
        description: skill.description,
        whenToUse: skill.whenToUse ?? null,
        modelInvocable: skill.invocation.modelInvocable,
        userInvocable: skill.invocation.userInvocable,
        source: skill.source,
      })),
    }
  } finally {
    await lease?.[Symbol.asyncDispose]()
  }
}

function logFailure(ctx: Context, operation: string, cause: unknown): void {
  ctx.logger.warn(`dsh-overlay-custom: ${operation}: ${cause instanceof Error ? cause.message : String(cause)}`)
}

/** Register authenticated Skills and MCP settings routes. */
export function applyOverlaySkillsMcpRoutes(ctx: Context): void {
  const home = resolveDshHome()
  ctx.effect(() => ctx.webServer.register({
    kind: 'exact', path: SKILLS_PATH,
    handler: (req, res) => {
      if (rejectFor(ctx, req, res)) return
      if (req.method !== 'GET') return method(res, 'GET')
      void listSkills(ctx)
        .then(value => writeJson(res, 200, value))
        .catch(cause => { logFailure(ctx, 'skill catalog failed', cause); writeJson(res, 500, { error: 'skill-catalog-unavailable' }) })
    },
  }), 'dsh-overlay-custom: skills route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact', path: MCP_PATH,
    handler: (req, res) => {
      if (rejectFor(ctx, req, res)) return
      if (req.method !== 'GET' && req.method !== 'POST') return method(res, 'GET, POST')
      if (req.method === 'GET') {
        void readOverlayMcpServers(home)
          .then(servers => writeJson(res, 200, { servers: servers.map(projectOverlayMcpServer), restartRequired: true }))
          .catch(cause => { logFailure(ctx, 'MCP state read failed', cause); writeJson(res, 500, { error: 'mcp-state-unavailable' }) })
        return
      }
      if (forbidden(req, res)) return
      void (async () => {
        try {
          const body = await readJsonBody(req)
          if (!Array.isArray(body.servers)) throw new Error('servers must be an array')
          const servers = await writeOverlayMcpServers(home, body.servers as OverlayMcpServerWrite[])
          writeJson(res, 200, {
            accepted: true,
            servers: servers.map(projectOverlayMcpServer),
            restartRequired: true,
            restartScheduled: false,
          })
        } catch (cause) {
          logFailure(ctx, 'MCP state write rejected', cause)
          writeJson(res, 400, { error: 'invalid-mcp-state' })
        }
      })()
    },
  }), 'dsh-overlay-custom: MCP state route')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact', path: MCP_TEST_PATH,
    handler: (req, res) => {
      if (rejectFor(ctx, req, res)) return
      if (req.method !== 'POST') return method(res, 'POST')
      if (forbidden(req, res)) return
      void (async () => {
        try {
          const body = await readJsonBody(req)
          if (body.row === null || typeof body.row !== 'object' || Array.isArray(body.row)) {
            throw new Error('row must be an object')
          }
          const rows = await readOverlayMcpServers(home)
          const id = typeof body.id === 'string' ? body.id : undefined
          const current = id === undefined ? undefined : rows.find(row => row.id === id)
          const candidate = mergeOverlayMcpServer(current, body.row as OverlayMcpServerWrite)
          writeJson(res, 200, await probeOverlayMcpServer(candidate))
        } catch (cause) {
          logFailure(ctx, 'MCP probe rejected', cause)
          writeJson(res, 400, { ok: false, error: 'protocol', detail: 'probe could not run' })
        }
      })()
    },
  }), 'dsh-overlay-custom: MCP test route')
}

async function importMcpClient(): Promise<typeof import('@deepseek-ai/dsh-mcp-client')> {
  try {
    return await import('@deepseek-ai/dsh-mcp-client')
  } catch (directError) {
    try {
      const requireFromRuntime = createRequire(join(process.cwd(), 'package.json'))
      return await import(pathToFileURL(requireFromRuntime.resolve('@deepseek-ai/dsh-mcp-client')).href)
    } catch {
      throw directError
    }
  }
}

/** Mount enabled MCP clients beneath the overlay fiber for this Host generation. */
export async function mountOverlayMcpServers(ctx: Context): Promise<void> {
  const servers = await readOverlayMcpServers(resolveDshHome())
  if (!servers.some(server => server.disabled !== true)) return
  let mcpClient: {
    readonly apply: (...args: never[]) => unknown
    readonly Config: (value: Record<string, unknown>) => unknown
  }
  try {
    mcpClient = await importMcpClient()
  } catch (cause) {
    logFailure(ctx, 'MCP client package import failed', cause)
    return
  }
  for (const server of servers) {
    if (server.disabled === true) continue
    try {
      ctx.plugin(mcpClient as never, mcpClient.Config(mcpClientConfig(parseOverlayMcpServer(server))))
      ctx.logger.info(`dsh-overlay-custom: mounted MCP server ${server.serverName}`)
    } catch (cause) {
      logFailure(ctx, `MCP server ${server.serverName} failed to mount`, cause)
    }
  }
}
