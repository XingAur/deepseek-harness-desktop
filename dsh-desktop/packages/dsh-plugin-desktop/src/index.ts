import { registerMarketRoutes, type HostContextLike } from './market-routes'

export const name = 'desktop-community-market'
export const inject = ['webServer']

export function apply(ctx: HostContextLike): void {
  ctx.effect(() => registerMarketRoutes(ctx), 'desktop: signed community market routes')
}

export { CatalogStore, canonicalJson, verifyCatalog } from './catalog'
export { PluginCommandService, pluginArguments } from './plugin-command'
