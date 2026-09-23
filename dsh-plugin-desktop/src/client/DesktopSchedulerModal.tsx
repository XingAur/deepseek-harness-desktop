/**
 * In-app scheduled-task manager: a dialog inside the main window that lists,
 * creates, edits, enables, runs, and deletes tasks through the scheduler's
 * private loopback routes. The native editor window stays available from the
 * tray; this dialog is the surface the DSH client itself presents.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { CalendarClock, Pencil, Play, Plus, Trash2, X } from 'lucide-react'
import type { DesktopSchedulerLocaleKey } from './desktop-scheduler-locales.ts'

/** Translator bound by the caller to the scheduler locale namespace. */
export type DesktopSchedulerTranslate = (key: DesktopSchedulerLocaleKey) => string

/** Tasks, presets, and actions come from the same private routes the native editor uses. */
const TASKS_PATH = '/api/desktop/scheduler/tasks'
const TASK_PATH = '/api/desktop/scheduler/task'
const TASK_ENABLE_PATH = '/api/desktop/scheduler/task/enable'
const TASK_RUN_PATH = '/api/desktop/scheduler/task/run'
const TASK_DELETE_PATH = '/api/desktop/scheduler/task/delete'
const PREVIEW_PATH = '/api/desktop/scheduler/preview'
const PRESETS_PATH = '/api/desktop/scheduler/presets'

type ScheduleRule = { kind: 'every'; seconds: number } | { kind: 'cron'; expression: string }

/** One task as the dialog renders it. */
interface SchedulerTaskView {
  readonly id: string
  readonly name: string
  readonly prompt: string
  readonly schedule: ScheduleRule
  readonly enabled: boolean
  readonly sessionPolicy: 'fresh' | 'continue'
  readonly permissionPreset: string | null
  readonly notify: boolean
  readonly nextRunAt: string | null
  readonly consecutiveFailures: number
}

/** Editable form state; `id === null` means creation. */
interface TaskDraft {
  id: string | null
  name: string
  prompt: string
  kind: 'every' | 'cron'
  minutes: number
  expression: string
  sessionPolicy: 'fresh' | 'continue'
  permissionPreset: string | null
  notify: boolean
  enabled: boolean
}

function freshDraft(): TaskDraft {
  return {
    id: null,
    name: '',
    prompt: '',
    kind: 'every',
    minutes: 30,
    expression: '',
    sessionPolicy: 'fresh',
    permissionPreset: null,
    notify: true,
    enabled: true,
  }
}

function draftFrom(task: SchedulerTaskView): TaskDraft {
  return {
    id: task.id,
    name: task.name,
    prompt: task.prompt,
    kind: task.schedule.kind,
    minutes: task.schedule.kind === 'every' ? Math.max(1, Math.round(task.schedule.seconds / 60)) : 30,
    expression: task.schedule.kind === 'cron' ? task.schedule.expression : '',
    sessionPolicy: task.sessionPolicy,
    permissionPreset: task.permissionPreset,
    notify: task.notify,
    enabled: task.enabled,
  }
}

async function readJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'accept': 'application/json', ...init?.headers },
    ...init,
  })
  if (!response.ok) throw new Error(String(response.status))
  return await response.json()
}

async function postJson(path: string, body: unknown): Promise<unknown> {
  return await readJson(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function parseTasks(payload: unknown): readonly SchedulerTaskView[] {
  const tasks = (payload as { tasks?: unknown }).tasks
  if (!Array.isArray(tasks)) return []
  const views: SchedulerTaskView[] = []
  for (const value of tasks) {
    if (typeof value !== 'object' || value === null) continue
    const row = value as Record<string, unknown>
    const id = typeof row.id === 'string' ? row.id : ''
    const name = typeof row.name === 'string' ? row.name : ''
    const prompt = typeof row.prompt === 'string' ? row.prompt : ''
    const schedule = row.schedule as ScheduleRule | undefined
    if (id === '' || name === '' || typeof prompt !== 'string' || schedule === undefined) continue
    views.push({
      id,
      name,
      prompt,
      schedule,
      enabled: row.enabled !== false,
      sessionPolicy: row.sessionPolicy === 'continue' ? 'continue' : 'fresh',
      permissionPreset: typeof row.permissionPreset === 'string' ? row.permissionPreset : null,
      notify: row.notify !== false,
      nextRunAt: typeof row.nextRunAt === 'string' ? row.nextRunAt : null,
      consecutiveFailures: typeof row.consecutiveFailures === 'number' ? row.consecutiveFailures : 0,
    })
  }
  return views
}

function draftPayload(draft: TaskDraft): Record<string, unknown> {
  return {
    ...(draft.id === null ? {} : { id: draft.id }),
    name: draft.name.trim(),
    prompt: draft.prompt,
    cwd: null,
    schedule: draft.kind === 'every'
      ? { kind: 'every', seconds: Math.max(1, Math.round(draft.minutes)) * 60 }
      : { kind: 'cron', expression: draft.expression.trim() },
    enabled: draft.enabled,
    sessionPolicy: draft.sessionPolicy,
    permissionPreset: draft.permissionPreset,
    notify: draft.notify,
  }
}

function scheduleLabel(rule: ScheduleRule, t: DesktopSchedulerTranslate): string {
  if (rule.kind === 'cron') return `${t('cronLabel')}: ${rule.expression}`
  const minutes = Math.round(rule.seconds / 60)
  const amount = minutes < 60
    ? `${String(minutes)} ${t('minutesUnit')}`
    : minutes < 1440
      ? `${String(Math.round(minutes / 60))} ${t('hoursUnit')}`
      : `${String(Math.round(minutes / 1440))} ${t('daysUnit')}`
  return `${t('everyPrefix')} ${amount}`.trim()
}

function formatInstant(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

const STYLE_ID = 'dsh-desktop-scheduler-modal-styles'
const CSS = `
.dshDesktopSchedulerModal { width: min(680px, 100%); max-height: min(78vh, 720px); display: flex; flex-direction: column; padding: 0; gap: 0; }
.dshDesktopSchedulerModalHead { display: flex; align-items: center; gap: 8px; padding: 16px 20px 12px; border-bottom: 1px solid var(--dsw-alias-border-l1); }
.dshDesktopSchedulerModalHead h3 { margin: 0; flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; color: var(--dsw-alias-label-primary); font-size: 15px; font-weight: 600; }
.dshDesktopSchedulerModalHead h3 svg { width: 16px; height: 16px; color: var(--dsw-alias-brand-primary); }
.dshDesktopSchedulerModalBody { flex: 1; min-height: 0; overflow: auto; padding: 14px 20px 18px; display: flex; flex-direction: column; gap: 10px; }
.dshDesktopSchedulerModalClose { flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: none; border-radius: 50%; background: transparent; color: var(--dsw-alias-label-secondary); cursor: pointer; }
.dshDesktopSchedulerModalClose:hover { background: var(--dsw-alias-interactive-bg-hover); color: var(--dsw-alias-label-primary); }
.dshDesktopSchedulerModalActions { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 20px 16px; border-top: 1px solid var(--dsw-alias-border-l1); }
.dshDesktopSchedulerTaskMeta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 4px; }
.dshDesktopSchedulerTaskGrid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.dshDesktopSchedulerPreview { margin: 0; padding: 10px 12px; border: 1px solid var(--dsw-alias-border-l1); border-radius: 8px; background: var(--dsw-alias-bg-layer-2); color: var(--dsw-alias-label-secondary); font-size: 12px; line-height: 1.7; }
.dshDesktopSchedulerNotice { color: var(--dsw-alias-state-success-primary); background: color-mix(in srgb, var(--dsw-alias-state-success-primary) 10%, transparent); }
@media (max-width: 560px) { .dshDesktopSchedulerTaskGrid { grid-template-columns: 1fr; } }
`

function installModalStyles(): void {
  if (typeof document === 'undefined') return
  if (document.getElementById(STYLE_ID) !== null) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.appendChild(style)
}

interface ModalProps {
  readonly open: boolean
  readonly onClose: () => void
  readonly t: DesktopSchedulerTranslate
}

/** List row for one task with its inline actions. */
function TaskRow(props: {
  task: SchedulerTaskView
  busy: boolean
  confirmingDelete: boolean
  t: DesktopSchedulerTranslate
  onEdit: () => void
  onToggle: () => void
  onRun: () => void
  onDelete: () => void
}): ReactNode {
  const { task, busy, confirmingDelete, t, onEdit, onToggle, onRun, onDelete } = props
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
        <span className="dshDesktopSchedulerTaskMeta">
          <span className="dshDesktopSettingsBadge">{scheduleLabel(task.schedule, t)}</span>
          {task.enabled && task.nextRunAt !== null && (
            <span className="dshDesktopSettingsChoiceBody">{t('nextRun')}: {formatInstant(task.nextRunAt)}</span>
          )}
        </span>
      </span>
      <span className="dshDesktopSettingsRowActions">
        <button type="button" className="dshDesktopSettingsButton" disabled={busy} onClick={onRun} title={t('runNow')}>
          <Play width={12} height={12} />
          {t('runNow')}
        </button>
        <button type="button" className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary" disabled={busy} onClick={onEdit}>
          <Pencil width={12} height={12} />
          {t('editAction')}
        </button>
        <button type="button" className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary" disabled={busy} onClick={onToggle}>
          {task.enabled ? t('disableAction') : t('enableAction')}
        </button>
        <button
          type="button"
          className="dshDesktopSettingsButton dshDesktopSettingsButtonDanger"
          disabled={busy}
          onClick={onDelete}
        >
          <Trash2 width={12} height={12} />
          {confirmingDelete ? t('confirmDelete') : t('deleteAction')}
        </button>
      </span>
    </div>
  )
}

/** Editor form for one draft task. */
function TaskEditor(props: {
  draft: TaskDraft
  presets: readonly string[]
  preview: readonly string[]
  previewFailed: boolean
  t: DesktopSchedulerTranslate
  onChange: (patch: Partial<TaskDraft>) => void
  onPreview: () => void
}): ReactNode {
  const { draft, presets, preview, previewFailed, t, onChange, onPreview } = props
  return (
    <>
      <label className="dshDesktopSettingsField">
        <span>{t('nameLabel')}</span>
        <input
          className="dshDesktopSettingsInput"
          value={draft.name}
          placeholder={t('namePlaceholder')}
          onChange={event => { onChange({ name: event.target.value }) }}
        />
      </label>
      <label className="dshDesktopSettingsField">
        <span>{t('promptLabel')}</span>
        <textarea
          className="dshDesktopSettingsInput"
          rows={4}
          value={draft.prompt}
          placeholder={t('promptPlaceholder')}
          onChange={event => { onChange({ prompt: event.target.value }) }}
        />
      </label>
      <div className="dshDesktopSchedulerTaskGrid">
        <label className="dshDesktopSettingsField">
          <span>{t('scheduleLabel')}</span>
          <select
            className="dshDesktopSettingsSelect"
            value={draft.kind}
            onChange={event => { onChange({ kind: event.target.value === 'cron' ? 'cron' : 'every' }) }}
          >
            <option value="every">{t('intervalOption')}</option>
            <option value="cron">{t('cronOption')}</option>
          </select>
        </label>
        {draft.kind === 'every' ? (
          <label className="dshDesktopSettingsField">
            <span>{t('intervalMinutes')}</span>
            <input
              className="dshDesktopSettingsInput"
              type="number"
              min={1}
              value={String(draft.minutes)}
              onChange={event => { onChange({ minutes: Number(event.target.value) || 0 }) }}
            />
          </label>
        ) : (
          <label className="dshDesktopSettingsField">
            <span>{t('cronExpression')}</span>
            <input
              className="dshDesktopSettingsInput"
              value={draft.expression}
              placeholder="0 9 * * 1-5"
              onChange={event => { onChange({ expression: event.target.value }) }}
            />
          </label>
        )}
        <label className="dshDesktopSettingsField">
          <span>{t('sessionPolicyLabel')}</span>
          <select
            className="dshDesktopSettingsSelect"
            value={draft.sessionPolicy}
            onChange={event => { onChange({ sessionPolicy: event.target.value === 'continue' ? 'continue' : 'fresh' }) }}
          >
            <option value="fresh">{t('policyFresh')}</option>
            <option value="continue">{t('policyContinue')}</option>
          </select>
        </label>
        <label className="dshDesktopSettingsField">
          <span>{t('presetLabel')}</span>
          <select
            className="dshDesktopSettingsSelect"
            value={draft.permissionPreset ?? ''}
            onChange={event => { onChange({ permissionPreset: event.target.value === '' ? null : event.target.value }) }}
          >
            <option value="">{t('presetDefault')}</option>
            {presets.map(name => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
      </div>
      <label className="dshDesktopSettingsCheckRow">
        <input type="checkbox" checked={draft.notify} onChange={event => { onChange({ notify: event.target.checked }) }} />
        <span>{t('notifyLabel')}</span>
      </label>
      <label className="dshDesktopSettingsCheckRow">
        <input type="checkbox" checked={draft.enabled} onChange={event => { onChange({ enabled: event.target.checked }) }} />
        <span>{t('enabledLabel')}</span>
      </label>
      <div className="dshDesktopSettingsDeleteActions">
        <button type="button" className="dshDesktopSettingsButton" onClick={onPreview}>{t('previewButton')}</button>
      </div>
      {previewFailed && <p className="dshDesktopSettingsError" role="alert">{t('noPreview')}</p>}
      {preview.length > 0 && (
        <div className="dshDesktopSchedulerPreview">
          <strong>{t('previewTitle')}</strong>
          {preview.map(instant => <div key={instant}>{formatInstant(instant)}</div>)}
        </div>
      )}
    </>
  )
}

/** Render the in-app scheduled-task dialog; no-op while `open` is false. */
export function DesktopSchedulerModal({ open, onClose, t }: ModalProps): ReactNode {
  const [tasks, setTasks] = useState<readonly SchedulerTaskView[]>()
  const [presets, setPresets] = useState<readonly string[]>([])
  const [failed, setFailed] = useState(false)
  const [draft, setDraft] = useState<TaskDraft>()
  const [preview, setPreview] = useState<readonly string[]>([])
  const [previewFailed, setPreviewFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [confirmingDelete, setConfirmingDelete] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async (): Promise<void> => {
    setFailed(false)
    try {
      const [taskPayload, presetPayload] = await Promise.all([
        readJson(TASKS_PATH),
        readJson(PRESETS_PATH),
      ])
      setTasks(parseTasks(taskPayload))
      const names = (presetPayload as { presets?: unknown }).presets
      setPresets(Array.isArray(names) ? names.filter((name): name is string => typeof name === 'string') : [])
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    installModalStyles()
    void load()
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey) }
  }, [open, load, onClose, reloadKey])

  if (!open) return null

  const withBusy = async (action: () => Promise<void>): Promise<void> => {
    setBusy(true)
    try {
      await action()
    } catch {
      setNotice(t('actionFailed'))
    } finally {
      setBusy(false)
    }
  }

  const toggle = (task: SchedulerTaskView): void => {
    void withBusy(async () => {
      await postJson(TASK_ENABLE_PATH, { id: task.id, enabled: !task.enabled })
      await load()
    })
  }

  const run = (task: SchedulerTaskView): void => {
    void withBusy(async () => {
      await postJson(TASK_RUN_PATH, { id: task.id })
      setNotice(t('triggered'))
      await load()
    })
  }

  const remove = (task: SchedulerTaskView): void => {
    if (confirmingDelete !== task.id) {
      setConfirmingDelete(task.id)
      return
    }
    setConfirmingDelete('')
    void withBusy(async () => {
      await postJson(TASK_DELETE_PATH, { id: task.id })
      setNotice(t('deletedDone'))
      await load()
    })
  }

  const save = (): void => {
    if (draft === undefined || draft.name.trim() === '' || draft.prompt.trim() === '') return
    void withBusy(async () => {
      await postJson(TASK_PATH, draftPayload(draft))
      setDraft(undefined)
      setPreview([])
      setPreviewFailed(false)
      setNotice(t('savedDone'))
      await load()
    })
  }

  const runPreview = (): void => {
    if (draft === undefined) return
    setPreviewFailed(false)
    void withBusy(async () => {
      try {
        const payload = await postJson(PREVIEW_PATH, { schedule: draftPayload(draft).schedule })
        const next = (payload as { next?: unknown }).next
        setPreview(Array.isArray(next) ? next.filter((value): value is string => typeof value === 'string') : [])
      } catch {
        setPreview([])
        setPreviewFailed(true)
      }
    })
  }

  return (
    <div
      className="dshDesktopSettingsDialogBackdrop"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="dshDesktopSettingsDialog dshDesktopSchedulerModal"
        role="dialog"
        aria-modal="true"
        aria-label={t('title')}
        onClick={event => { event.stopPropagation() }}
      >
        <div className="dshDesktopSchedulerModalHead">
          <h3>
            <CalendarClock aria-hidden="true" />
            {draft === undefined ? t('title') : draft.id === null ? t('newTask') : t('editTask')}
          </h3>
          <button type="button" className="dshDesktopSchedulerModalClose" aria-label={t('close')} onClick={onClose}>
            <X width={16} height={16} />
          </button>
        </div>
        <div className="dshDesktopSchedulerModalBody">
          {failed && tasks === undefined && (
            <>
              <p className="dshDesktopSettingsError" role="alert">{t('unavailable')}</p>
              <div className="dshDesktopSettingsDeleteActions">
                <button type="button" className="dshDesktopSettingsPrimary" onClick={() => { setReloadKey(key => key + 1) }}>
                  {t('retry')}
                </button>
              </div>
            </>
          )}
          {!failed && tasks === undefined && <p className="dshDesktopSettingsHint">{t('loading')}</p>}
          {notice !== '' && <p className="dshDesktopSettingsNotice dshDesktopSchedulerNotice" role="status">{notice}</p>}
          {draft !== undefined ? (
            <TaskEditor
              draft={draft}
              presets={presets}
              preview={preview}
              previewFailed={previewFailed}
              t={t}
              onChange={patch => { setDraft(current => current === undefined ? current : { ...current, ...patch }) }}
              onPreview={runPreview}
            />
          ) : (
            tasks !== undefined && tasks.length === 0 && <p className="dshDesktopSettingsHint">{t('empty')}</p>
          )}
          {draft === undefined && tasks !== undefined && tasks.length > 0 && (
            <div className="dshDesktopSettingsList" role="list">
              {tasks.map(task => (
                <TaskRow
                  key={task.id}
                  task={task}
                  busy={busy}
                  confirmingDelete={confirmingDelete === task.id}
                  t={t}
                  onEdit={() => { setDraft(draftFrom(task)); setPreview([]); setPreviewFailed(false) }}
                  onToggle={() => { toggle(task) }}
                  onRun={() => { run(task) }}
                  onDelete={() => { remove(task) }}
                />
              ))}
            </div>
          )}
        </div>
        <div className="dshDesktopSchedulerModalActions">
          {draft !== undefined ? (
            <>
              <button type="button" className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary" onClick={() => { setDraft(undefined) }}>
                {t('cancel')}
              </button>
              <button
                type="button"
                className="dshDesktopSettingsPrimary"
                disabled={busy || draft.name.trim() === '' || draft.prompt.trim() === ''}
                onClick={save}
              >
                {busy ? t('saving') : t('save')}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="dshDesktopSettingsButton"
                onClick={() => { setReloadKey(key => key + 1) }}
              >
                {t('refresh')}
              </button>
              <button type="button" className="dshDesktopSettingsPrimary" onClick={() => { setDraft(freshDraft()); setPreview([]); setPreviewFailed(false) }}>
                <Plus width={13} height={13} />
                {t('newTask')}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
