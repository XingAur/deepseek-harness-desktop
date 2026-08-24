import type { ClientContextLike } from './contracts'
import { parseDesktopEnvironment } from './environment'
import { applyAdvancedShell } from './advanced-shell'

export const inject = ['slots', 'sessions', 'theme', 'workspaces']

export function apply(ctx: ClientContextLike): void {
  const environment = parseDesktopEnvironment(window.location.search)
  if (environment === null) return
  if (environment.generationId === undefined || environment.sessionId === undefined) return
  applyAdvancedShell(ctx, environment.platform, {
    parentOrigin: environment.parentOrigin,
    context: { generationId: environment.generationId, sessionId: environment.sessionId },
  })
}

export { AdvancedFrame } from './AdvancedFrame'
export { createDesktopBridge } from './desktop-bridge'
export { createAgentEventBridge } from './agent-event-bridge'
