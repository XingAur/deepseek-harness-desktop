import type { Context } from '@deepseek-ai/cordis'
import { resolveDshHome } from './dsh-home.ts'
import { readOverlayConfigSync } from './overlay-config.ts'
import { applyOverlayModels } from './models.ts'
import { applyOverlayOauth } from './oauth.ts'
import { applyOverlayProxyApi } from './proxy-api.ts'
import { applyOverlayRelay } from './relay.ts'
import { applyOverlaySkillsMcpRoutes, mountOverlayMcpServers } from './skills-mcp.ts'
import { applyOverlayUpdates } from './updates.ts'

export const name = 'dsh-overlay-custom'
export const inject = ['webServer', 'connection']

function loadRelayOrigin(): string {
  const env = process.env.DSH_OVERLAY_RELAY_ORIGIN
  if (typeof env === 'string' && env.trim() !== '') return env.trim()
  const origin = readOverlayConfigSync(resolveDshHome()).remoteRelayOrigin
  if (typeof origin === 'string') return origin
  return ''
}

/** Host face for official desktop: relay, model, Skills, and MCP extensions. */
export async function apply(ctx: Context): Promise<void> {
  const modelNetwork = applyOverlayProxyApi(ctx)
  applyOverlayRelay(ctx, loadRelayOrigin())
  applyOverlayModels(ctx, modelNetwork)
  applyOverlayOauth(ctx, modelNetwork)
  applyOverlaySkillsMcpRoutes(ctx)
  applyOverlayUpdates(ctx)
  await mountOverlayMcpServers(ctx)
  ctx.logger.info('dsh-overlay-custom host mounted')
}
