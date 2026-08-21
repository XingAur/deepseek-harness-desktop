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

export function applyAdvancedShell(
  ctx: ClientContextLike,
  platform: DesktopPlatform,
  bridge: DesktopBridgeLike = desktopBridgeForWindow(),
): void {
  const layout = new DesktopLayoutState()
  const localProjects = new LocalProjectsState()
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
  ctx.slots.inject?.('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    id: 'dsh-desktop-local-projects',
    order: 10,
    inject: () => ({ state: localProjects }),
  }, LocalProjectsFooterAction))
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
      inject: () => ({ layout, platform, workspaces: ctx.workspaces, sessions: ctx.sessions, bridge, localProjects }),
    }, AdvancedFrame)
    return () => { disposeRegistration(); disposeService(); bridge.dispose() }
  }, 'desktop: layout service + advanced root slot')
}

function desktopBridgeForWindow(): DesktopBridgeLike {
  if (window.parent !== window) return createDesktopBridge()
  return {
    request: () => Promise.reject(new Error('桌面桥仅在受管工作台中可用')),
    dispose: () => undefined,
  }
}
