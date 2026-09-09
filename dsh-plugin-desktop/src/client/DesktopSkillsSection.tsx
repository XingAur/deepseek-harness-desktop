/** Desktop-owned Skills settings section listing the Host composition's skill catalog. */

import { useCallback, useEffect, useState } from 'react'
import type { InjectFace, PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { DesktopSettingsApi, DesktopSkillView, DesktopSkillsView } from './desktop-settings-api.ts'
import type { DesktopSkillsLocaleKey } from './desktop-skills-locales.ts'

/** Registration-side business face for the Desktop Skills settings section. */
export interface DesktopSkillsSectionInjected {
  readonly api: DesktopSettingsApi
}

/** Renderer-composed props for the Skills settings section entry. */
export type DesktopSkillsSectionProps =
  PropsRuntime<'settings.section'>
  & PropsLocale<'desktop.skills'>
  & InjectFace<DesktopSkillsSectionInjected>

type Translate = DesktopSkillsSectionProps['t']

const SOURCE_LABEL_KEYS = {
  'user-agents': 'sourceUserAgents',
  'user-dsh': 'sourceUserDsh',
  'project-agents': 'sourceProjectAgents',
  'project-dsh': 'sourceProjectDsh',
  'bundled': 'sourceBundled',
  'runtime': 'sourceRuntime',
  'custom': 'sourceCustom',
} as const satisfies Record<string, DesktopSkillsLocaleKey>

function sourceLabelKey(source: string): DesktopSkillsLocaleKey {
  return source in SOURCE_LABEL_KEYS
    ? SOURCE_LABEL_KEYS[source as keyof typeof SOURCE_LABEL_KEYS]
    : 'sourceCustom'
}

function SkillRow({ skill, t }: { skill: DesktopSkillView; t: Translate }) {
  return (
    <div className="dshDesktopSettingsChoice" role="listitem">
      <span className="dshDesktopSettingsChoiceCopy">
        <span className="dshDesktopSettingsChoiceTitle">
          {skill.name}
          {skill.userInvocable && <span className="dshDesktopSettingsBadge">{t('badgeUserInvocable')}</span>}
          {skill.modelInvocable && <span className="dshDesktopSettingsBadge">{t('badgeModelInvocable')}</span>}
        </span>
        <span className="dshDesktopSettingsChoiceBody">{skill.description}</span>
        {skill.whenToUse !== null && skill.whenToUse.length > 0 && (
          <span className="dshDesktopSettingsChoiceBody">{t('whenToUseLabel')}: {skill.whenToUse}</span>
        )}
      </span>
      <span className="dshDesktopSettingsBadge">{t(sourceLabelKey(skill.source))}</span>
    </div>
  )
}

/** Render the Desktop Skills settings page. */
export function DesktopSkillsSection({ t, api }: DesktopSkillsSectionProps) {
  const [view, setView] = useState<DesktopSkillsView>()
  const [failed, setFailed] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      setView(await api.listSkills())
    } catch {
      setFailed(true)
    }
  }, [api])

  useEffect(() => { void load() }, [load, reloadKey])

  return (
    <div className="dshDesktopSettings">
      <header className="dshDesktopSettingsHeader">
        <h2>{t('title')}</h2>
        <p>{t('intro')}</p>
      </header>
      <section className="dshDesktopSettingsGroup" aria-labelledby="dsh-desktop-skills-title">
        <div>
          <h3 id="dsh-desktop-skills-title">{t('catalogTitle')}</h3>
          <p className="dshDesktopSettingsGroupIntro">{t('catalogIntro')}</p>
        </div>
        {failed && view === undefined && (
          <div>
            <p className="dshDesktopSettingsError" role="alert">{t('unavailable')}</p>
            <button
              type="button"
              className="dshDesktopSettingsButton"
              onClick={() => { setReloadKey(key => key + 1) }}
            >
              {t('retry')}
            </button>
          </div>
        )}
        {!failed && view === undefined && <p className="dshDesktopSettingsHint">{t('loading')}</p>}
        {view !== undefined && !view.available && (
          <p className="dshDesktopSettingsNotice">{t('registryUnavailable')}</p>
        )}
        {view !== undefined && view.available && view.skills.length === 0 && (
          <p className="dshDesktopSettingsNotice">{t('empty')}</p>
        )}
        {view !== undefined && view.available && view.skills.length > 0 && (
          <div className="dshDesktopSettingsList" role="list" aria-labelledby="dsh-desktop-skills-title">
            {view.skills.map(skill => <SkillRow key={skill.name} skill={skill} t={t} />)}
          </div>
        )}
      </section>
    </div>
  )
}
