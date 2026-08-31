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
import { ExtensionCenterState } from './extension-center-state'
import { ExtensionCenterFooterAction } from './ExtensionCenterFooterAction'
import { ModelAgentCenter } from './model-agent/ModelAgentCenter'
import { installNewSessionTransition } from './new-session-transition'
import { listPreviewCatalog } from './extensions/preview-catalog'

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
  const extensionCenter = new ExtensionCenterState()
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
    id: 'dsh-desktop-extension-center',
    order: 20,
    inject: () => ({ state: extensionCenter }),
  }, ExtensionCenterFooterAction))
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
      inject: () => ({ layout, platform, workspaces: ctx.workspaces, sessions: ctx.sessions, bridge, modelId: currentModelId(ctx), localProjects, extensionCenter }),
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
  return createPreviewDesktopBridge()
}

/** 仅供浏览器直开时验收 UI；它不读取或写入正式桌面配置。 */
export function createPreviewDesktopBridge(): DesktopBridgeLike {
  return {
    mode: 'preview',
    request: async () => { throw new Error('本地预览不连接正式桌面数据') },
    requestV2: async <T,>(action: string, _context?: unknown, payload: Record<string, unknown> = {}) => {
      if (action === 'plugin.catalog.list') return listPreviewCatalog(payload) as T
      if (action === 'plugin.install.status') return { jobRunning: false, jobOutput: [] } as T
      if (action === 'capability.inventory') return [
        { id: 'file-read', displayName: '读取文件', mutating: false, approvalRequired: false },
        { id: 'file-write', displayName: '修改文件', mutating: true, approvalRequired: true },
        { id: 'mcp-call', displayName: '调用 MCP', mutating: true, approvalRequired: true },
      ] as T
      if (action === 'provider.metadata.list') return [{
        providerId: 'codex', displayName: 'Codex', cliCommand: 'codex', kind: 'cli',
        adapterProtocol: 'dsh-agent-adapter/v1', credentialSupported: false, developerOnly: false,
      }] as T
      if (action === 'harness.status') return { state: 'idle' } as T
      if (action === 'extension.inventory') return [
        { extensionId: 'preview.skill.code-review', extensionKind: 'skill', displayName: '代码审查技能', sourceKind: 'preview', status: 'enabled', updatedAt: '2026-08-30T00:00:00Z' },
        { extensionId: 'preview.mcp.files', extensionKind: 'mcp', displayName: '文件 MCP', sourceKind: 'preview', status: 'enabled', updatedAt: '2026-08-30T00:00:00Z' },
      ] as T
      if (action === 'cli.login.status') return { installed: true, cliPath: '/usr/local/bin/codex', loggedIn: true, mode: '预览样例', jobRunning: false, jobOutput: [] } as T
      if (action === 'cli.install.status') return { command: ['npm', 'install', '-g', '@openai/codex'], installed: true, jobRunning: false, jobOutput: [] } as T
      if (action === 'harness.connection.list') {
        const connections = [
          { profileId: 'preview-yunxiao', kind: 'mcp', transport: 'http', source: 'legacy', templateId: 'yunxiao', providerId: 'yunxiao', displayName: '云效需求读取（样例）', endpoint: 'https://devops.aliyun.com', readOnly: true, enabled: true },
          { profileId: 'preview-gitlab', kind: 'mcp', transport: 'http', source: 'legacy', templateId: 'gitlab', providerId: 'gitlab', displayName: 'GitLab 代码读取（样例）', endpoint: 'https://gitlab.example.com', readOnly: true, enabled: true },
          { profileId: 'preview-api', kind: 'http-api', transport: 'http', source: 'custom', templateId: 'custom', displayName: '内部检索 API（样例）', endpoint: 'https://search.example.invalid', healthPath: '/health', readOnly: true, enabled: true },
          { profileId: 'preview-db', kind: 'database', transport: 'database', source: 'custom', templateId: 'custom', providerId: 'generic', displayName: 'HIS 只读库（样例）', endpoint: 'postgresql://example.invalid:5432/his', databaseType: 'postgresql', host: 'example.invalid', port: 5432, databaseName: 'his', username: 'readonly_user', encoding: 'UTF-8', testQuery: 'SELECT 1', readOnly: true, enabled: true },
        ]
        return connections.filter((item) => payload.kind === undefined || item.kind === payload.kind) as T
      }
      throw new Error('本地只读预览不执行安装或配置写入')
    },
    dispose: () => undefined,
  }
}
