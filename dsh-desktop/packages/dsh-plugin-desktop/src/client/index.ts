import type { ClientContextLike } from './contracts'
import { parseDesktopEnvironment } from './environment'
import { applyAdvancedShell } from './advanced-shell'

export const inject = ['slots', 'sessions', 'theme', 'workspaces']

export function apply(ctx: ClientContextLike): void {
  const environment = parseDesktopEnvironment(window.location.search)
  if (environment === null) return
  applyAdvancedShell(ctx, environment.platform)
}

export { AdvancedFrame } from './AdvancedFrame'
export { MarketPage } from './MarketPage'
