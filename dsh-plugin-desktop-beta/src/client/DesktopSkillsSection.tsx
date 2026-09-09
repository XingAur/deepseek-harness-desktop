/** Desktop-owned Skills settings section listing the Host composition's skill catalog. */

import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Blocks, RefreshCw, Sparkles } from 'lucide-react'
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

function SkillRow({ skill, t }: { skill: DesktopSkillView; t: Translate }): ReactNode {
  return (
    <div className="dshDesktopSettingsRow" role="listitem">
      <span className="dshDesktopSettingsRowIcon" aria-hidden="true"><Sparkles /></span>
      <span className="dshDesktopSettingsRowCopy">
        <span className="dshDesktopSettingsChoiceTitle">
          {skill.name}
          {skill.userInvocable && <span className="dshDesktopSettingsBadge">{t('badgeUserInvocable')}</span>}
          {skill.modelInvocable && <span className="dshDesktopSettingsBadge">{t('badgeModelInvocable')}</span>}
        </span>
        {skill.description.length > 0 && (
          <span className="dshDesktopSettingsChoiceBody">{skill.description}</span>
        )}
        {skill.whenToUse !== null && skill.whenToUse.length > 0 && (
          <span className="dshDesktopSettingsChoiceBody">{t('whenToUseLabel')}: {skill.whenToUse}</span>
        )}
      </span>
      <span className="dshDesktopSettingsBadge">{t(sourceLabelKey(skill.source))}</span>
    </div>
  )
}

/** Render the Desktop Skills settings page. */
export function DesktopSkillsSection({ t, api }: DesktopSkillsSectionProps): ReactNode {
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

  const skillCount = view?.available === true ? view.skills.length : undefined

  return (
    <div className="dshDesktopSettings">
      <header className="dshDesktopSettingsHero">
        <span className="dshDesktopSettingsHeroIcon" aria-hidden="true"><Blocks /></span>
        <span>
          <h2>{t('title')}</h2>
          <p>{t('intro')}</p>
        </span>
      </header>
      <section className="dshDesktopSettingsCard" aria-labelledby="dsh-desktop-skills-title">
        <div className="dshDesktopSettingsCardHead">
          <h3 id="dsh-desktop-skills-title">
            {t('catalogTitle')}{skillCount !== undefined ? ` · ${String(skillCount)}` : ''}
          </h3>
          {view !== undefined && (
            <span className="dshDesktopSettingsCardHeadActions">
              <button
                type="button"
                className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                onClick={() => { setReloadKey(key => key + 1) }}
              >
                <RefreshCw width={13} height={13} />
                {t('retry')}
              </button>
            </span>
          )}
        </div>
        <p className="dshDesktopSettingsGroupIntro">{t('catalogIntro')}</p>
        {failed && view === undefined && (
          <div>
            <p className="dshDesktopSettingsError" role="alert">{t('unavailable')}</p>
            <button
              type="button"
              className="dshDesktopSettingsPrimary"
              onClick={() => { setReloadKey(key => key + 1) }}
            >
              <RefreshCw />
              {t('retry')}
            </button>
          </div>
        )}
        {!failed && view === undefined && <p className="dshDesktopSettingsHint">{t('loading')}</p>}
        {view !== undefined && !view.available && (
          <p className="dshDesktopSettingsNotice">{t('registryUnavailable')}</p>
        )}
        {view !== undefined && view.available && view.skills.length === 0 && (
          <div className="dshDesktopSettingsEmpty">
            <Sparkles aria-hidden="true" />
            <span className="dshDesktopSettingsEmptyStrong">{t('empty')}</span>
          </div>
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
