import type { ClientContextLike } from './contracts'
import { parseDesktopEnvironment } from './environment'
import { applyAdvancedShell } from './advanced-shell'

// 'llm' 供 advanced-shell 的 currentModelId 读取当前模型;缺了会在 root slot
// 渲染时抛 "cannot get property 'llm' without inject",导致整个工作台崩溃。
export const inject = ['slots', 'sessions', 'theme', 'workspaces', 'llm']

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
