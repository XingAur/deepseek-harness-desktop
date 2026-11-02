/** Settings sidebar page summarizing scheduled tasks and opening the in-app task dialog. */

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { CalendarClock, RefreshCw } from 'lucide-react'
import type { InjectFace, PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import { DesktopSchedulerModal } from './DesktopSchedulerModal.tsx'
import type { DesktopSchedulerLocaleKey } from './desktop-scheduler-locales.ts'

const TASKS_PATH = '/api/desktop/scheduler/tasks'

/** Registration-side face for the scheduler settings section. */
export interface DesktopSchedulerSectionInjected {}

/** Renderer-composed props for the scheduler settings section. */
export type DesktopSchedulerSectionProps =
  PropsRuntime<'settings.section'>
  & PropsLocale<'desktop.scheduler'>
  & InjectFace<DesktopSchedulerSectionInjected>

interface SchedulerTaskView {
  readonly id: string
  readonly name: string
  readonly schedule: { kind: 'every'; seconds: number } | { kind: 'cron'; expression: string }
  readonly enabled: boolean
  readonly nextRunAt: string | null
  readonly consecutiveFailures: number
}

interface SchedulerTasksPayload {
  readonly tasks: readonly SchedulerTaskView[]
}

function scheduleLabel(rule: SchedulerTaskView['schedule'], t: (key: DesktopSchedulerLocaleKey) => string): string {
  if (rule.kind === 'cron') return `${t('cronLabel')}: ${rule.expression}`
  const minutes = Math.round(rule.seconds / 60)
  const amount = minutes < 60
    ? `${String(minutes)} ${t('minutesUnit')}`
    : minutes < 1440
      ? `${String(Math.round(minutes / 60))} ${t('hoursUnit')}`
      : `${String(Math.round(minutes / 1440))} ${t('daysUnit')}`
  return `${t('everyPrefix')} ${amount}`.trim()
}

type Translate = DesktopSchedulerSectionProps['t']

function TaskRow({ task, t }: { task: SchedulerTaskView; t: Translate }): ReactNode {
  return (
    <div className="dshDesktopSettingsRow" role="listitem">
      <span className="dshDesktopSettingsRowIcon" aria-hidden="true"><CalendarClock /></span>
      <span className="dshDesktopSettingsRowCopy">
        <span className="dshDesktopSettingsChoiceTitle">
          {task.name}
          {!task.enabled && <span className="dshDesktopSettingsBadge">{t('paused')}</span>}
          {task.consecutiveFailures > 0 && (
            <span className="dshDesktopSettingsBadge">{t('failing')} ×{String(task.consecutiveFailures)}</span>
          )}
        </span>
        {task.enabled && task.nextRunAt !== null && (
          <span className="dshDesktopSettingsChoiceBody">
            {t('nextRun')}: {new Date(task.nextRunAt).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })}
          </span>
        )}
      </span>
      <span className="dshDesktopSettingsBadge">{scheduleLabel(task.schedule, t)}</span>
    </div>
  )
}

/** Render the Desktop scheduler summary page. */
export function DesktopSchedulerSection({ t }: DesktopSchedulerSectionProps): ReactNode {
  const [tasks, setTasks] = useState<readonly SchedulerTaskView[]>()
  const [failed, setFailed] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [dialogOpen, setDialogOpen] = useState(false)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      const response = await fetch(TASKS_PATH, { headers: { accept: 'application/json' } })
      if (!response.ok) throw new Error(String(response.status))
      const payload = await response.json() as SchedulerTasksPayload
      setTasks(payload.tasks ?? [])
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => { void load() }, [load, reloadKey])

  return (
    <div className="dshDesktopSettings">
      <header className="dshDesktopSettingsHero">
        <span className="dshDesktopSettingsHeroIcon" aria-hidden="true"><CalendarClock /></span>
        <span>
          <h2>{t('title')}</h2>
          <p>{t('description')}</p>
        </span>
      </header>
      <section className="dshDesktopSettingsCard" aria-labelledby="dsh-desktop-scheduler-title">
        <div className="dshDesktopSettingsCardHead">
          <h3 id="dsh-desktop-scheduler-title">
            {t('title')}{tasks !== undefined ? ` · ${String(tasks.length)}` : ''}
          </h3>
          <span className="dshDesktopSettingsCardHeadActions">
            <button
              type="button"
              className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
              onClick={() => { setReloadKey(key => key + 1) }}
            >
              <RefreshCw width={13} height={13} />
              {t('refresh')}
            </button>
          </span>
        </div>
        {failed && tasks === undefined && (
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
        {!failed && tasks === undefined && <p className="dshDesktopSettingsHint">{t('loading')}</p>}
        {tasks !== undefined && tasks.length === 0 && <p className="dshDesktopSettingsHint">{t('empty')}</p>}
        {tasks !== undefined && tasks.length > 0 && (
          <div className="dshDesktopSettingsList" role="list">
            {tasks.map(task => <TaskRow key={task.id} task={task} t={t} />)}
          </div>
        )}
        <div className="dshDesktopSettingsDeleteActions">
          <button
            type="button"
            className="dshDesktopSettingsButton"
            onClick={() => { setDialogOpen(true) }}
          >
            {t('manage')}
          </button>
        </div>
      </section>
      <DesktopSchedulerModal open={dialogOpen} onClose={() => { setDialogOpen(false); void load() }} t={t} />
    </div>
  )
}
