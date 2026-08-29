import { AdvancedFrame } from './AdvancedFrame'
import type { ClientContextLike, DesktopPlatform } from './contracts'
import { DesktopLayoutState } from './layout-state'
import { provideDesktopLayout } from './layout-service'
import { installAdvancedStyles } from './styles'
import { DesktopThemePresenter } from './theme-presenter'
import { createDesktopBridge } from './desktop-bridge'
import type { DesktopBridgeLike } from './desktop-bridge'
import { ProfileSettingsSection } from './ProfileSettingsSection'
import { LocalProjectsFooterAction } from './LocalProjectsFooterAction'
import { LocalProjectsState } from './local-projects-state'
import { PluginCenterState } from './plugin-center-state'
import { PluginCenterFooterAction } from './PluginCenterFooterAction'
import { ModelAgentCenter } from './model-agent/ModelAgentCenter'
import { installNewSessionTransition } from './new-session-transition'

export interface AdvancedShellOptions {
  bridge?: DesktopBridgeLike
  parentOrigin?: string
  context?: { generationId: string; sessionId: string }
}

export function applyAdvancedShell(
  ctx: ClientContextLike,
  platform: DesktopPlatform,
  options: AdvancedShellOptions = {},
): void {
  const bridge = options.bridge ?? desktopBridgeForWindow(options.parentOrigin, options.context)
  const layout = new DesktopLayoutState()
  const localProjects = new LocalProjectsState()
  const pluginCenter = new PluginCenterState()
  ctx.effect(
    () => installNewSessionTransition(ctx.workspaces, ctx.sessions),
    'desktop: new session transition',
  )
  ctx.effect(() => {
    document.body.dataset.dshDesktopMode = 'advanced'
    document.body.dataset.dshDesktopPlatform = platform
    const remove = installAdvancedStyles()
    return () => { remove(); delete document.body.dataset.dshDesktopMode; delete document.body.dataset.dshDesktopPlatform }
  }, 'desktop: advanced styles')
  if (ctx.theme !== undefined) {
    ctx.effect(() => {
      const presenter = new DesktopThemePresenter()
      presenter.apply(ctx.theme?.getTheme())
      const off = ctx.on?.('theme/change', (theme) => presenter.apply(theme))
      return () => { off?.(); presenter.dispose() }
    }, 'desktop: theme presenter')
  }
  ctx.slots.inject?.('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'dsh-desktop-profiles',
    order: 60,
    label: 'Profiles',
    inject: () => ({ bridge }),
  }, ProfileSettingsSection))
  ctx.slots.inject?.('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'dsh-desktop-model-agent',
    order: 50,
    label: '模型与 Agent',
    inject: () => ({ bridge, workspaceId: currentWorkspaceId(ctx) }),
  }, ModelAgentCenter))
  ctx.slots.inject?.('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    id: 'dsh-desktop-local-projects',
    order: 10,
    inject: () => ({ state: localProjects }),
  }, LocalProjectsFooterAction))
  ctx.slots.inject?.('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    id: 'dsh-desktop-plugin-center',
    order: 20,
    inject: () => ({ state: pluginCenter }),
  }, PluginCenterFooterAction))
  ctx.effect(() => {
    const disposeService = provideDesktopLayout(ctx, layout)
    const disposeRegistration = ctx.slots.register({
      name: 'root',
      children: {
        sidebar: { kind: 'single', scope: 'root' },
        conversation: { kind: 'single', scope: 'session-maybe' },
        details: { kind: 'single', scope: 'session' },
        'shell.overlay': { kind: 'list', scope: 'root' },
      },
      inject: () => ({ layout, platform, workspaces: ctx.workspaces, sessions: ctx.sessions, bridge, modelId: currentModelId(ctx), localProjects, pluginCenter }),
    }, AdvancedFrame)
    return () => { disposeRegistration(); disposeService(); bridge.dispose() }
  }, 'desktop: layout service + advanced root slot')
}

function currentModelId(ctx: ClientContextLike): string | undefined {
  const llm = ctx.llm
  if (typeof llm !== 'object' || llm === null) return undefined
  const service = llm as {
    currentModelId?: unknown
    selectedModelId?: unknown
    getCurrentModelId?: () => unknown
  }
  if (typeof service.currentModelId === 'string' && service.currentModelId.trim() !== '') return service.currentModelId.trim()
  if (typeof service.selectedModelId === 'string' && service.selectedModelId.trim() !== '') return service.selectedModelId.trim()
  if (typeof service.getCurrentModelId === 'function') {
    const value = service.getCurrentModelId()
    if (typeof value === 'string' && value.trim() !== '') return value.trim()
  }
  return undefined
}

function currentWorkspaceId(ctx: ClientContextLike): string | undefined {
  const snapshot = ctx.workspaces?.list.getSnapshot()
  return snapshot?.recentWorkspaceId ?? snapshot?.items[0]?.workspaceId
}

function desktopBridgeForWindow(targetOrigin?: string, context?: { generationId: string; sessionId: string }): DesktopBridgeLike {
  if (window.parent !== window) return createDesktopBridge({ targetOrigin, context })
  return {
    request: () => Promise.reject(new Error('桌面桥仅在受管工作台中可用')),
    requestV2: () => Promise.reject(new Error('桌面桥仅在受管工作台中可用')),
    dispose: () => undefined,
  }
}
