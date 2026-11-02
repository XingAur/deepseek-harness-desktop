import { useCallback, useEffect, useRef, useState } from 'react'
import { CalendarClock, Check, Copy, Pencil, Play, Plus, Pause, Trash2, X } from 'lucide-react'
import { Alert, AlertDescription } from '../components/ui/alert.tsx'
import { Badge } from '../components/ui/badge.tsx'
import { Button } from '../components/ui/button.tsx'
import { Input } from '../components/ui/input.tsx'
import { Label } from '../components/ui/label.tsx'
import { DesktopFrame } from '../shared/DesktopFrame.tsx'
import { taskEditorCopy } from '../../task-editor-copy.ts'

const SCHEME = 'dsh-task-editor:'

interface TaskRecord {
  readonly id: string
  readonly name: string
  readonly prompt: string
  readonly cwd: string | null
  readonly schedule: { kind: 'every'; seconds: number } | { kind: 'cron'; expression: string }
  readonly enabled: boolean
  readonly sessionPolicy: 'fresh' | 'continue'
  readonly permissionPreset: string | null
  readonly notify: boolean
  readonly nextRunAt: string | null
  readonly lastRunAt: string | null
  readonly consecutiveFailures: number
}

interface RunRecord {
  readonly taskId: string
  readonly status: 'running' | 'completed' | 'failed' | 'skipped-overlap' | 'cancelled'
  readonly startedAt: string
  readonly finishedAt: string | null
  readonly error: string | null
}

interface Snapshot {
  readonly tasks: TaskRecord[]
  readonly runs: Record<string, RunRecord[]>
  readonly presets: readonly string[]
}

interface Draft {
  readonly id: string | null
  name: string
  prompt: string
  schedule: { kind: 'every'; seconds: number } | { kind: 'cron'; expression: string }
  sessionPolicy: 'fresh' | 'continue'
  cwd: string
  permissionPreset: string
  notify: boolean
}

type ScheduleMode = 'template' | 'sentence' | 'cron'

function locale(): 'en' | 'zh' {
  if (typeof window === 'undefined') return 'en'
  return new URLSearchParams(window.location.search).get('locale') === 'zh' ? 'zh' : 'en'
}

function act(name: string, params: Record<string, string> = {}): void {
  const url = new URL(`${SCHEME}//${name}`)
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value)
  window.location.assign(url.href)
}

function formatInstant(iso: string | null, intlLocale: string): string {
  if (iso === null) return '—'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleString(intlLocale, { dateStyle: 'medium', timeStyle: 'short' })
}

export function TaskEditorApp(): JSX.Element {
  const intlLocale = locale()
  const copy = taskEditorCopy(intlLocale)
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [mode, setMode] = useState<ScheduleMode>('template')
  const [templateId, setTemplateId] = useState<string | null>(null)
  const [nlText, setNlText] = useState('')
  const [nlBusy, setNlBusy] = useState(false)
  const [nlNote, setNlNote] = useState<string | null>(null)
  const [preview, setPreview] = useState<string[] | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const toastTimer = useRef<number | undefined>(undefined)

  const showToast = useCallback((message: string): void => {
    setToast(message)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => { setToast(null) }, 2_400)
  }, [])

  useEffect(() => {
    const onState = (event: Event): void => {
      if (!(event instanceof CustomEvent)) return
      setSnapshot(event.detail as Snapshot)
    }
    const onSaved = (event: Event): void => {
      if (!(event instanceof CustomEvent)) return
      const detail = event.detail as { ok: boolean; error?: string }
      if (detail.ok) {
        setDraft(null)
        showToast(copy.saved)
      } else {
        showToast(`${copy.saveFailed}${detail.error === undefined ? '' : `: ${detail.error}`}`)
      }
    }
    const onPreview = (event: Event): void => {
      if (!(event instanceof CustomEvent)) return
      const detail = event.detail as { ok: boolean; next?: string[] }
      setPreview(detail.ok && detail.next !== undefined ? detail.next : [])
    }
    const onInterpret = (event: Event): void => {
      if (!(event instanceof CustomEvent)) return
      const detail = event.detail as { result: { expression: string } | null }
      setNlBusy(false)
      if (detail.result === null) {
        setNlNote(copy.nlUnavailable)
        return
      }
      const expression = detail.result.expression
      setNlNote(null)
      setDraft(current => current === null ? current : { ...current, schedule: { kind: 'cron', expression } })
      setMode('cron')
    }
    const onDirectory = (event: Event): void => {
      if (!(event instanceof CustomEvent)) return
      const detail = event.detail as { directory: string | null }
      if (detail.directory === null) return
      const directory = detail.directory
      setDraft(current => current === null ? current : { ...current, cwd: directory })
    }
    const onRun = (): void => { showToast(copy.runQueued) }
    window.addEventListener('dsh-task-editor-state', onState)
    window.addEventListener('dsh-task-editor-saved', onSaved)
    window.addEventListener('dsh-task-editor-preview', onPreview)
    window.addEventListener('dsh-task-editor-interpret', onInterpret)
    window.addEventListener('dsh-task-editor-directory', onDirectory)
    window.addEventListener('dsh-task-editor-run', onRun)
    return () => {
      window.removeEventListener('dsh-task-editor-state', onState)
      window.removeEventListener('dsh-task-editor-saved', onSaved)
      window.removeEventListener('dsh-task-editor-preview', onPreview)
      window.removeEventListener('dsh-task-editor-interpret', onInterpret)
      window.removeEventListener('dsh-task-editor-directory', onDirectory)
      window.removeEventListener('dsh-task-editor-run', onRun)
    }
  }, [copy, showToast])

  // Preview follows every schedule change of the open draft.
  useEffect(() => {
    if (draft === null) return
    act('preview', { schedule: JSON.stringify(draft.schedule) })
  }, [draft?.schedule])  // eslint-disable-line react-hooks/exhaustive-deps

  const openDraft = (initial: Draft, initialMode: ScheduleMode): void => {
    setDraft(initial)
    setMode(initialMode)
    setTemplateId(null)
    setNlText('')
    setNlNote(null)
    setPreview(null)
    setConfirmDeleteId(null)
  }

  const newDraft = (): Draft => ({
    id: null,
    name: '',
    prompt: '',
    schedule: { kind: 'cron', expression: '0 9 * * *' },
    sessionPolicy: 'fresh',
    cwd: '',
    permissionPreset: '',
    notify: true,
  })

  const submitDraft = (): void => {
    if (draft === null) return
    if (draft.name.trim() === '' || draft.prompt.trim() === '') return
    act('submit', { task: JSON.stringify({
      ...(draft.id === null ? {} : { id: draft.id }),
      name: draft.name.trim(),
      prompt: draft.prompt,
      schedule: draft.schedule,
      enabled: true,
      sessionPolicy: draft.sessionPolicy,
      cwd: draft.cwd.trim() === '' ? null : draft.cwd.trim(),
      permissionPreset: draft.permissionPreset.trim() === '' ? null : draft.permissionPreset.trim(),
      notify: draft.notify,
    }) })
  }

  const scheduleLabel = (rule: TaskRecord['schedule']): string => {
    if (rule.kind === 'cron') return rule.expression
    const minutes = rule.seconds / 60
    if (minutes < 60) return copy.everyMinutes(minutes)
    const hours = minutes / 60
    if (hours < 24) return copy.everyHours(hours)
    return copy.everyDays(hours / 24)
  }

  const statusLabel = (status: RunRecord['status']): string => {
    switch (status) {
      case 'running': return copy.statusRunning
      case 'completed': return copy.statusCompleted
      case 'failed': return copy.statusFailed
      case 'skipped-overlap': return copy.statusSkipped
      case 'cancelled': return copy.statusCancelled
    }
  }

  const statusTone = (status: RunRecord['status']): string => {
    switch (status) {
      case 'running': return 'bg-blue-500/15 text-blue-400'
      case 'completed': return 'bg-emerald-500/15 text-emerald-400'
      case 'failed': return 'bg-red-500/15 text-red-400'
      default: return 'bg-zinc-500/15 text-zinc-400'
    }
  }

  const tasks = snapshot?.tasks ?? []
  const runs = snapshot?.runs ?? {}

  return (
    <>
      <DesktopFrame />
      <div className="dshNativeContent flex h-full flex-col gap-3 p-4 text-sm text-zinc-200">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CalendarClock className="size-4 text-zinc-400" />
            <h1 className="text-base font-semibold">{copy.title}</h1>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => { openDraft(newDraft(), 'template') }}
            >
              <Plus className="size-3.5" /> {copy.newTask}
            </Button>
            <Button variant="ghost" size="sm" aria-label={copy.close} onClick={() => { act('close') }}>
              <X className="size-3.5" />
            </Button>
          </div>
        </div>

        {toast !== null && (
          <Alert>
            <AlertDescription>{toast}</AlertDescription>
          </Alert>
        )}

        {draft !== null && (
          <div className="flex flex-col gap-3 rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-3">
            <div className="grid gap-2">
              <Label htmlFor="task-name">{copy.name}</Label>
              <Input
                id="task-name"
                value={draft.name}
                placeholder={copy.namePlaceholder}
                onChange={event => { setDraft({ ...draft, name: event.target.value }) }}
              />
              <Label htmlFor="task-prompt">{copy.prompt}</Label>
              <textarea
                id="task-prompt"
                className="min-h-20 rounded-md border border-zinc-700 bg-zinc-900 p-2 text-sm outline-none focus:border-zinc-500"
                value={draft.prompt}
                placeholder={copy.promptPlaceholder}
                onChange={event => { setDraft({ ...draft, prompt: event.target.value }) }}
              />
            </div>

            <div className="flex items-center gap-1 rounded-md bg-zinc-800/70 p-1 text-xs">
              {copy.scheduleModes.map((label, index) => {
                const value: ScheduleMode = index === 0 ? 'template' : index === 1 ? 'sentence' : 'cron'
                return (
                  <button
                    key={value}
                    type="button"
                    className={`rounded px-2 py-1 ${mode === value ? 'bg-zinc-600/70 text-white' : 'text-zinc-400'}`}
                    onClick={() => { setMode(value) }}
                  >
                    {label}
                  </button>
                )
              })}
            </div>

            {mode === 'template' && (
              <div className="flex flex-wrap gap-1.5">
                {copy.templates.map(template => (
                  <button
                    key={template.id}
                    type="button"
                    className={`rounded-full border px-2.5 py-1 text-xs ${
                      templateId === template.id
                        ? 'border-emerald-500/60 text-emerald-300'
                        : 'border-zinc-700 text-zinc-300'}`}
                    onClick={() => {
                      setTemplateId(template.id)
                      setDraft({ ...draft, schedule: template.rule })
                    }}
                  >
                    {template.label}
                  </button>
                ))}
              </div>
            )}

            {mode === 'sentence' && (
              <div className="flex gap-2">
                <Input
                  value={nlText}
                  placeholder={copy.nlPlaceholder}
                  onChange={event => { setNlText(event.target.value) }}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={nlBusy || nlText.trim() === ''}
                  onClick={() => {
                    setNlBusy(true)
                    setNlNote(null)
                    act('interpret', { text: nlText.trim() })
                  }}
                >
                  {nlBusy ? copy.nlBusy : copy.nlButton}
                </Button>
              </div>
            )}
            {mode === 'sentence' && nlNote !== null && <p className="text-xs text-amber-400">{nlNote}</p>}

            {mode === 'cron' && (
              <div className="grid gap-1">
                <Input
                  className="font-mono"
                  value={draft.schedule.kind === 'cron' ? draft.schedule.expression : ''}
                  placeholder={copy.cronPlaceholder}
                  onChange={event => { setDraft({ ...draft, schedule: { kind: 'cron', expression: event.target.value } }) }}
                />
                <p className="text-xs text-zinc-500">5 字段 cron：分 时 日 月 周</p>
              </div>
            )}

            <div className="grid gap-1">
              <p className="text-xs font-medium text-zinc-400">{copy.previewTitle}</p>
              {preview === null || preview.length === 0
                ? <p className="text-xs text-zinc-500">{copy.previewEmpty}</p>
                : (
                  <ul className="list-inside list-disc text-xs text-zinc-300">
                    {preview.map(instant => (
                      <li key={instant}>{formatInstant(instant, intlLocale === 'zh' ? 'zh-CN' : 'en-US')}</li>
                    ))}
                  </ul>
                )}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="grid gap-1">
                <Label>{copy.sessionPolicy}</Label>
                <div className="flex gap-1 text-xs">
                  {([['fresh', copy.sessionFresh], ['continue', copy.sessionContinue]] as const).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      className={`rounded border px-2 py-1 ${
                        draft.sessionPolicy === value
                          ? 'border-zinc-400 text-white'
                          : 'border-zinc-700 text-zinc-400'}`}
                      onClick={() => { setDraft({ ...draft, sessionPolicy: value }) }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="grid gap-1">
                <Label htmlFor="task-preset">{copy.permissionPreset}</Label>
                <select
                  id="task-preset"
                  className="rounded-md border border-zinc-700 bg-zinc-900 p-2 text-sm"
                  value={draft.permissionPreset}
                  onChange={event => { setDraft({ ...draft, permissionPreset: event.target.value }) }}
                >
                  <option value="">{copy.presetNone}</option>
                  {(snapshot?.presets ?? []).map(preset => (
                    <option key={preset} value={preset}>{preset}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="task-cwd">{copy.workspace}</Label>
              <div className="flex gap-2">
                <Input
                  id="task-cwd"
                  className="font-mono text-xs"
                  value={draft.cwd}
                  onChange={event => { setDraft({ ...draft, cwd: event.target.value }) }}
                />
                <Button variant="outline" size="sm" onClick={() => { act('pick-directory') }}>
                  {copy.workspaceBrowse}
                </Button>
              </div>
            </div>

            <label className="flex items-center gap-2 text-xs text-zinc-300">
              <input
                type="checkbox"
                checked={draft.notify}
                onChange={event => { setDraft({ ...draft, notify: event.target.checked }) }}
              />
              {copy.notify}
            </label>

            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => { setDraft(null) }}>{copy.cancel}</Button>
              <Button
                size="sm"
                disabled={draft.name.trim() === '' || draft.prompt.trim() === ''}
                onClick={submitDraft}
              >
                <Check className="size-3.5" /> {copy.save}
              </Button>
            </div>
          </div>
        )}

        {tasks.length === 0 && draft === null && (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-zinc-700/70 p-6 text-center">
            <p className="text-sm text-zinc-400">{copy.emptyTitle}</p>
            <div className="flex flex-wrap justify-center gap-2">
              <Button variant="outline" size="sm" onClick={() => { openDraft(newDraft(), 'template') }}>
                {copy.emptyTemplates}
              </Button>
              <Button variant="outline" size="sm" onClick={() => { openDraft(newDraft(), 'sentence') }}>
                {copy.emptySentence}
              </Button>
              <Button variant="outline" size="sm" onClick={() => { openDraft(newDraft(), 'cron') }}>
                {copy.emptyManual}
              </Button>
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          {tasks.map(task => {
            const history = runs[task.id] ?? []
            const removing = confirmDeleteId === task.id
            return (
              <div key={task.id} className="mb-2 rounded-lg border border-zinc-700/60 bg-zinc-900/50 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{task.name}</span>
                      {!task.enabled && <Badge variant="secondary" className="text-amber-400">{copy.paused}</Badge>}
                    </div>
                    <p className="mt-0.5 truncate text-xs text-zinc-500">
                      {scheduleLabel(task.schedule)}
                      {task.enabled && task.nextRunAt !== null && ` · ${copy.nextRun}: ${formatInstant(task.nextRunAt, intlLocale === 'zh' ? 'zh-CN' : 'en-US')}`}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button variant="ghost" size="sm" title={copy.runNow} onClick={() => { act('run', { id: task.id }) }}>
                      <Play className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      title={task.enabled ? copy.pause : copy.resume}
                      onClick={() => { act('enable', { id: task.id, enabled: task.enabled ? '0' : '1' }) }}
                    >
                      {task.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      title={copy.editTask}
                      onClick={() => {
                        openDraft({
                          id: task.id,
                          name: task.name,
                          prompt: task.prompt,
                          schedule: task.schedule,
                          sessionPolicy: task.sessionPolicy,
                          cwd: task.cwd ?? '',
                          permissionPreset: task.permissionPreset ?? '',
                          notify: task.notify,
                        }, task.schedule.kind === 'cron' ? 'cron' : 'template')
                      }}
                    >
                      <Pencil className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      title={copy.duplicate}
                      onClick={() => {
                        openDraft({
                          id: null,
                          name: `${task.name} 2`,
                          prompt: task.prompt,
                          schedule: task.schedule,
                          sessionPolicy: task.sessionPolicy,
                          cwd: task.cwd ?? '',
                          permissionPreset: task.permissionPreset ?? '',
                          notify: task.notify,
                        }, task.schedule.kind === 'cron' ? 'cron' : 'template')
                      }}
                    >
                      <Copy className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className={removing ? 'text-red-400' : undefined}
                      title={copy.delete}
                      onClick={() => {
                        if (removing) {
                          act('remove', { id: task.id })
                          setConfirmDeleteId(null)
                        } else {
                          setConfirmDeleteId(task.id)
                          window.setTimeout(() => {
                            setConfirmDeleteId(current => current === task.id ? null : current)
                          }, 3_000)
                        }
                      }}
                    >
                      {removing ? <Check className="size-3.5" /> : <Trash2 className="size-3.5" />}
                    </Button>
                  </div>
                </div>
                {history.length > 0 && (
                  <div className="mt-2 border-t border-zinc-800 pt-2">
                    <p className="mb-1 text-xs text-zinc-500">{copy.lastRuns}</p>
                    <div className="flex flex-col gap-1">
                      {history.slice(-3).reverse().map((run, index) => (
                        <div key={`${run.startedAt}-${String(index)}`} className="flex items-center gap-2 text-xs">
                          <span className={`rounded px-1.5 py-0.5 ${statusTone(run.status)}`}>{statusLabel(run.status)}</span>
                          <span className="text-zinc-500">{formatInstant(run.finishedAt ?? run.startedAt, intlLocale === 'zh' ? 'zh-CN' : 'en-US')}</span>
                          {run.error !== null && <span className="truncate text-red-400/80">{run.error}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}
