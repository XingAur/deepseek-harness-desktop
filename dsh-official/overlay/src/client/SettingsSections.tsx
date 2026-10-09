import { useCallback, useEffect, useState, type ReactNode } from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-ui-settings/client'
import { injectOverlayStyles } from './overlay-styles.ts'
import { CAPABILITY_NS, capabilityEn, capabilityZh, type CapabilitySectionProps } from './capability-locales.ts'
import { OverlayMcpSection } from './McpSettingsSection.tsx'

interface SkillView {
  readonly name: string
  readonly description: string
  readonly whenToUse: string | null
  readonly modelInvocable: boolean
  readonly userInvocable: boolean
  readonly source: string
}
interface SkillsView { readonly available: boolean; readonly skills: readonly SkillView[] }

async function jsonRequest<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', redirect: 'error', headers: { accept: 'application/json' } })
  const value = await response.json().catch(() => undefined) as T | undefined
  if (!response.ok || value === undefined) throw new Error(`HTTP ${String(response.status)}`)
  return value
}
function sourceLabel(source: string, t: CapabilitySectionProps['t']): string {
  if (source === 'bundled' || source === 'runtime') return t('bundledSource')
  if (source.startsWith('project-')) return t('projectSource')
  return t('privateSource')
}

export function OverlaySkillsSection({ t }: CapabilitySectionProps): ReactNode {
  const [view, setView] = useState<SkillsView>()
  const [failed, setFailed] = useState(false)
  const load = useCallback(() => {
    setFailed(false)
    void jsonRequest<SkillsView>('/api/desktop/skills').then(setView).catch(() => { setFailed(true) })
  }, [])
  useEffect(load, [load])
  return <div className="dshOverlaySettings">
    <header className="dshOverlaySettingsHero"><span className="dshOverlaySettingsHeroIcon">✦</span><span><h2>{t('skillsTitle')}</h2><p>{t('skillsIntro')}</p></span></header>
    <section className="dshOverlaySettingsCard">
      <div className="dshOverlaySettingsHead"><h3>{t('skillsTitle')}{view?.available ? ` · ${String(view.skills.length)}` : ''}</h3><button className="dshOverlayBtnSecondary" type="button" onClick={load}>{t('refresh')}</button></div>
      {view === undefined && !failed ? <p className="dshOverlayHint">{t('skillsLoading')}</p> : null}
      {failed || view?.available === false ? <p className="dshOverlayAlert">{t('skillsUnavailable')}</p> : null}
      {view?.available === true && view.skills.length === 0 ? <div className="dshOverlaySettingsEmpty">{t('skillsEmpty')}</div> : null}
      <div className="dshOverlaySettingsList">{view?.skills.map(skill => <article className="dshOverlaySkillRow" key={skill.name}>
        <span className="dshOverlaySkillIcon">✦</span><span className="dshOverlaySkillCopy"><strong>{skill.name}</strong><span>{skill.description}</span>{skill.whenToUse ? <span><b>{t('whenToUse')}：</b>{skill.whenToUse}</span> : null}</span>
        <span className="dshOverlayBadges">{skill.userInvocable ? <em>{t('userInvocable')}</em> : null}{skill.modelInvocable ? <em>{t('modelInvocable')}</em> : null}<em>{sourceLabel(skill.source, t)}</em></span>
      </article>)}</div>
    </section>
  </div>
}

export function applyOverlayCapabilitySettings(ctx: ClientContext): void {
  injectOverlayStyles()
  const t = ctx.locale.bind(CAPABILITY_NS)
  ctx.effect(() => ctx.locale.register(CAPABILITY_NS, { zh: capabilityZh, en: capabilityEn }), 'dsh-overlay-custom: capability settings locale')
  ctx.effect(() => ctx.slots.inject('settings.section', () => ctx.slots.register({ name: 'settings.section', id: 'overlay-skills', order: 90, label: () => t('skillsNav'), locale: CAPABILITY_NS, inject: () => ({}) }, OverlaySkillsSection as never)), 'dsh-overlay-custom: skills settings')
  ctx.effect(() => ctx.slots.inject('settings.section', () => ctx.slots.register({ name: 'settings.section', id: 'overlay-mcp', order: 95, label: () => t('mcpNav'), locale: CAPABILITY_NS, inject: () => ({}) }, OverlayMcpSection as never)), 'dsh-overlay-custom: mcp settings')
}
