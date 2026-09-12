/** Official Settings Slot registration for Desktop-owned preferences. */

import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import type { SettingsScope } from '@deepseek-ai/dsh-client-ui-settings/client'
import { DesktopMcpSection } from './DesktopMcpSection.tsx'
import { DesktopProviderCardExtras } from './DesktopProviderCardExtras.tsx'
import { DesktopProxySection } from './DesktopProxySection.tsx'
import { DesktopSettingsSection, type DesktopNotificationSettings, type DesktopShellSettings } from './DesktopSettingsSection.tsx'
import { DesktopSkillsSection } from './DesktopSkillsSection.tsx'
import { DesktopTerminalSettingsAction } from './DesktopTerminalSettingsAction.tsx'
import { createDesktopSettingsApi } from './desktop-settings-api.ts'
import { en, zh, type DesktopSettingsLocaleKey } from './desktop-settings-locales.ts'
import { en as skillsEn, zh as skillsZh, type DesktopSkillsLocaleKey } from './desktop-skills-locales.ts'
import { en as mcpEn, zh as mcpZh, type DesktopMcpLocaleKey } from './desktop-mcp-locales.ts'
import { en as modelEn, zh as modelZh, type DesktopModelLocaleKey } from './desktop-model-locales.ts'
import { en as proxyEn, zh as proxyZh, type DesktopProxyLocaleKey } from './desktop-proxy-locales.ts'
import { installDesktopSettingsStyles } from './desktop-settings-styles.ts'
import type { DesktopClientEnvironment } from './environment.ts'

/** Locale namespace owned by the Desktop settings page. */
export const DESKTOP_SETTINGS_LOCALE_NAMESPACE = 'desktop.settings'

/** Locale namespace owned by the Desktop Skills settings page. */
export const DESKTOP_SKILLS_LOCALE_NAMESPACE = 'desktop.skills'

/** Locale namespace owned by the Desktop MCP settings page. */
export const DESKTOP_MCP_LOCALE_NAMESPACE = 'desktop.mcp'

/** Locale namespace owned by the Desktop Model settings page. */
export const DESKTOP_MODEL_LOCALE_NAMESPACE = 'desktop.model'

/** Locale namespace owned by the Desktop proxy settings page. */
export const DESKTOP_PROXY_LOCALE_NAMESPACE = 'desktop.proxy'

/** Host settings namespaces bound through the standard client settings service. */
export const DESKTOP_SHELL_SETTINGS_NAMESPACE = 'dsh-desktop'
export const DESKTOP_NOTIFICATIONS_SETTINGS_NAMESPACE = 'dsh-desktop-notifications'

/** Shared client controls consumed by settings and Desktop-owned window chrome. */
export interface DesktopSettingsClientControl {
  readonly api: ReturnType<typeof createDesktopSettingsApi>
  setMode(mode: DesktopShellSettings['mode']): Promise<void>
}

/**
 * Persist a native mode choice without leaving browser access in a mode the
 * marker-free client cannot render. Custom modes withdraw browser and LAN
 * access in ordered writes; the Host compares only effective generation state.
 */
export async function persistDesktopModeSelection(
  desktopSettings: Pick<SettingsScope<DesktopShellSettings>, 'set'>,
  mode: DesktopShellSettings['mode'],
): Promise<void> {
  if (mode === 'compatibility') {
    await desktopSettings.set('mode', mode)
    return
  }
  // The titlebar is interactive before the settings mirror necessarily reaches
  // ready. Always withdraw both browser capabilities for a custom mode instead
  // of treating an unavailable or stale snapshot as browser access being off.
  // Withdraw the listener first so every intermediate persisted state remains
  // valid while compatibility mode is still selected.
  await desktopSettings.set('networkExposure', 'loopback')
  await desktopSettings.set('openBrowser', false)
  await desktopSettings.set('mode', mode)
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    /** Desktop-only settings page copy. */
    'desktop.settings': DesktopSettingsLocaleKey
    /** Desktop-only Skills settings page copy. */
    'desktop.skills': DesktopSkillsLocaleKey
    /** Desktop-only MCP settings page copy. */
    'desktop.mcp': DesktopMcpLocaleKey
    /** Desktop-only Model settings page copy. */
    'desktop.model': DesktopModelLocaleKey
    /** Desktop-only Proxy settings page copy. */
    'desktop.proxy': DesktopProxyLocaleKey
  }
}

/** Register the Desktop page in the settings.section list slot. */
export function applyDesktopSettings(
  ctx: ClientContext,
  environment: DesktopClientEnvironment,
): DesktopSettingsClientControl {
  const desktopSettings = ctx.settingsScope.bind<DesktopShellSettings>({
    namespace: DESKTOP_SHELL_SETTINGS_NAMESPACE,
  })
  const notificationSettings = ctx.settingsScope.bind<DesktopNotificationSettings>({
    namespace: DESKTOP_NOTIFICATIONS_SETTINGS_NAMESPACE,
  })
  const api = createDesktopSettingsApi()
  const t = ctx.locale.bind(DESKTOP_SETTINGS_LOCALE_NAMESPACE)
  const skillsT = ctx.locale.bind(DESKTOP_SKILLS_LOCALE_NAMESPACE)
  const mcpT = ctx.locale.bind(DESKTOP_MCP_LOCALE_NAMESPACE)
  const modelT = ctx.locale.bind(DESKTOP_MODEL_LOCALE_NAMESPACE)
  const proxyT = ctx.locale.bind(DESKTOP_PROXY_LOCALE_NAMESPACE)
  const setMode = async (mode: DesktopShellSettings['mode']): Promise<void> => {
    await persistDesktopModeSelection(desktopSettings, mode)
  }

  ctx.effect(
    () => ctx.locale.register(DESKTOP_SETTINGS_LOCALE_NAMESPACE, { zh, en }),
    'dsh-plugin-desktop: settings dictionaries',
  )
  ctx.effect(
    () => ctx.locale.register(DESKTOP_SKILLS_LOCALE_NAMESPACE, { zh: skillsZh, en: skillsEn }),
    'dsh-plugin-desktop: skills settings dictionaries',
  )
  ctx.effect(
    () => ctx.locale.register(DESKTOP_MCP_LOCALE_NAMESPACE, { zh: mcpZh, en: mcpEn }),
    'dsh-plugin-desktop: mcp settings dictionaries',
  )
  ctx.effect(
    () => ctx.locale.register(DESKTOP_MODEL_LOCALE_NAMESPACE, { zh: modelZh, en: modelEn }),
    'dsh-plugin-desktop: model settings dictionaries',
  )
  ctx.effect(
    () => ctx.locale.register(DESKTOP_PROXY_LOCALE_NAMESPACE, { zh: proxyZh, en: proxyEn }),
    'dsh-plugin-desktop: proxy settings dictionaries',
  )
  ctx.effect(
    () => installDesktopSettingsStyles(),
    'dsh-plugin-desktop: settings styles',
  )
  ctx.slots.inject('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'skills',
    order: 90,
    label: () => skillsT('nav'),
    locale: DESKTOP_SKILLS_LOCALE_NAMESPACE,
    inject: () => ({ api }),
  }, DesktopSkillsSection))
  ctx.slots.inject('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'mcp',
    order: 95,
    label: () => mcpT('nav'),
    locale: DESKTOP_MCP_LOCALE_NAMESPACE,
    inject: () => ({ api }),
  }, DesktopMcpSection))
  // One registration per keyed family: the direct DeepSeek adapter plus the
  // OpenAI-compatible pi-ai routes, so every provider card gets the same
  // test/fetch actions keyed to its own settings address.
  ctx.slots.inject('settings.models.provider-card', () => ctx.slots.register({
    name: 'settings.models.provider-card',
    key: 'llm-deepseek',
    inject: () => ({ t: modelT }) as never,
  }, DesktopProviderCardExtras as never))
  ctx.slots.inject('settings.models.provider-card', () => ctx.slots.register({
    name: 'settings.models.provider-card',
    key: 'llm-pi-ai',
    inject: () => ({ t: modelT }) as never,
  }, DesktopProviderCardExtras as never))
  ctx.slots.inject('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'proxy',
    order: 98,
    label: () => proxyT('nav'),
    locale: DESKTOP_PROXY_LOCALE_NAMESPACE,
    inject: () => ({ api, desktopSettings }),
  }, DesktopProxySection))
  ctx.slots.inject('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'desktop',
    order: 100,
    label: () => t('nav'),
    locale: DESKTOP_SETTINGS_LOCALE_NAMESPACE,
    inject: () => ({
      api,
      platform: environment.platform,
      initialMode: environment.mode,
      micaSupported: environment.micaSupported,
      setMode,
      desktopSettings,
      notificationSettings,
    }),
  }, DesktopSettingsSection))
  ctx.slots.inject('settings.action', () => ctx.slots.register({
    name: 'settings.action',
    id: 'open-desktop-terminal',
    order: 1,
    locale: DESKTOP_SETTINGS_LOCALE_NAMESPACE,
    inject: () => ({ api }),
  }, DesktopTerminalSettingsAction))

  return Object.freeze({
    api,
    setMode,
  })
}
