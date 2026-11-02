/**
 * Cordis Host row owning the Desktop task scheduler: durable task storage in
 * the DSH home, the due-trigger engine, in-app session production through
 * the Host session controller, native outcome notifications, and the
 * management routes the Desktop surfaces consume.
 */

import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-commands'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { resolveDshHome } from '@deepseek-ai/dsh-home-paths'
import type { Session, SessionEvent } from '@deepseek-ai/dsh-session'
import { settingsNamespace } from '@deepseek-ai/dsh-settings'
import type {} from '@deepseek-ai/dsh-settings'
import z from '@deepseek-ai/schemastery'
import { schedulerNotice, schedulerTrayLabel, scheduledPromptContent, type SchedulerNoticeKind } from './copy.ts'
import { PAUSE_AFTER_CONSECUTIVE_FAILURES, SchedulerEngine, computeNextRunMs } from './engine.ts'
import { previewRuleRuns, validateScheduleRule } from './next-run.ts'
import { nlScheduleOptions, parseNlScheduleAnswer } from './nl-schedule.ts'
import { isQuietHours } from './quiet-hours.ts'
import { readSchedulerTaskFile, schedulerTasksPath, writeSchedulerTaskFile } from './task-store.ts'
import {
  normalizeScheduledTask,
  type ScheduledTask,
  type TaskRun,
} from './types.ts'

/** Stable Cordis plugin name. */
export const name = 'desktop-scheduler'

/** Electron adapter and Host services this row requires. `webServer` backs
 * the management routes and `connection` gates loopback requests. */
export const inject = ['desktopRuntime', 'webServer', 'connection']

export const DESKTOP_SCHEDULER_SETTINGS_NAMESPACE = settingsNamespace('dsh-desktop-scheduler')

export interface DesktopSchedulerSettings {
  enabled: boolean
  maxConcurrent: number
  misfireWindowMinutes: number
  quietHoursEnabled: boolean
  quietStart: string
  quietEnd: string
}

export const DesktopSchedulerSettingsSchema: z<DesktopSchedulerSettings> = z.object({
  enabled: z.boolean().default(true),
  maxConcurrent: z.number().step(1).min(1).max(10).default(3),
  misfireWindowMinutes: z.number().step(1).min(0).max(1440).default(15),
  quietHoursEnabled: z.boolean().default(false),
  quietStart: z.string().default('23:00'),
  quietEnd: z.string().default('07:00'),
})

const DEFAULT_SETTINGS: DesktopSchedulerSettings = {
  enabled: true,
  maxConcurrent: 3,
  misfireWindowMinutes: 15,
  quietHoursEnabled: false,
  quietStart: '23:00',
  quietEnd: '07:00',
}

/** Timer ceiling: never arm one native timeout longer than one hour. */
const TIMER_CEILING_MS = 3_600_000

/** Prompt-admission signal, mirroring the mobile control path. */
function freshSignal(ms: number): AbortSignal {
  return AbortSignal.timeout(ms)
}

/** Structural session-controller surface this row drives; never imports upstream types. */
interface SessionControllerFace {
  create: (payload: Record<string, unknown>) => Promise<{ sessionId: string }>
  prompt: (payload: Record<string, unknown>, signal?: AbortSignal) => Promise<{ accepted: boolean }>
  rename?: (payload: { sessionId: string; title: string }) => Promise<unknown>
}

function sessionController(ctx: Context): SessionControllerFace | undefined {
  const value = ctx.get('sessionController')
  if (value === undefined || value === null || typeof value !== 'object') return undefined
  return value as SessionControllerFace
}

function json(res: ServerResponse, status: number, value: unknown): void {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(value))
}

async function readMutatingBody(req: IncomingMessage, limitBytes: number): Promise<Record<string, unknown> | null> {
  const chunks: Buffer[] = []
  let total = 0
  for await (const chunk of req) {
    const piece = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as string)
    total += piece.length
    if (total > limitBytes) return null
    chunks.push(piece)
  }
  try {
    const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null
  } catch {
    return null
  }
}

/** Local time rendering used in prompt headers, e.g. `2026-01-05 09:30`. */
function localTimestamp(nowMs: number): string {
  const now = new Date(nowMs)
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${String(now.getFullYear())}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`
}

/**
 * Register the scheduler for one Host generation. Every capability it
 * reaches for (session controller, commands, permission presets) is probed
 * at call time, so the row stays composable in reduced profiles: missing
 * capabilities degrade the corresponding feature with a logged reason.
 */
export function apply(ctx: Context): void {
  const runtime = ctx.get('desktopRuntime')
  if (runtime === undefined || runtime === null) {
    ctx.logger.warn('dsh-plugin-desktop: desktop-scheduler requires the desktop runtime')
    return
  }

  let settings: DesktopSchedulerSettings = DEFAULT_SETTINGS
  const log = (message: string): void => { ctx.logger.info(`dsh-plugin-desktop: ${message}`) }
  const tasksPath = schedulerTasksPath(resolveDshHome(undefined, process.env))

  const engine = new SchedulerEngine({
    gateway: {
      create: async payload => {
        const controller = sessionController(ctx)
        if (controller === undefined) throw new Error('session controller is not composed')
        return controller.create(payload)
      },
      prompt: async payload => {
        const controller = sessionController(ctx)
        if (controller === undefined) throw new Error('session controller is not composed')
        return controller.prompt(payload, freshSignal(120_000))
      },
      rename: async payload => {
        const controller = sessionController(ctx)
        return controller?.rename?.(payload)
      },
    },
    load: async () => (await readSchedulerTaskFile(tasksPath)).file,
    persist: file => writeSchedulerTaskFile(tasksPath, file),
    // Clamp native timeouts: schedules may sit months ahead, and engines
    // re-check due-ness on every wake, so ceiling re-arms stay correct.
    armTimer: (delayMs, fire) => {
      const handle = setTimeout(fire, Math.min(delayMs, TIMER_CEILING_MS))
      return () => { clearTimeout(handle) }
    },
    now: () => Date.now(),
    log,
    content: task => scheduledPromptContent(
      runtime.locale,
      task.name,
      localTimestamp(Date.now()),
      task.prompt,
    ),
    beforePrompt: async (task, sessionId) => {
      if (task.permissionPreset === null) return
      const permissionPresets = ctx.get('permissionPresets') as { names?: readonly string[] } | undefined
      if (!permissionPresets?.names?.includes(task.permissionPreset)) {
        throw new Error(`scheduler: unknown permission preset "${task.permissionPreset}"`)
      }
      const agents = ctx.get('agents') as {
        get?(sessionId: string): unknown
      } | undefined
      const commands = ctx.get('commands') as {
        execute?(agent: unknown, line: string, attachments: readonly unknown[], signal: AbortSignal): Promise<unknown>
      } | undefined
      const agent = agents?.get?.(sessionId)
      if (agent === undefined || commands?.execute === undefined) {
        throw new Error(`scheduler: preset "${task.permissionPreset}" cannot apply; commands or agents are not composed`)
      }
      await commands.execute(
        agent,
        `/permission ${task.permissionPreset}`,
        [],
        freshSignal(30_000),
      )
    },
    mayFireNow: nowMs => {
      if (!settings.enabled) return false
      if (!settings.quietHoursEnabled) return true
      return !isQuietHours(settings.quietStart, settings.quietEnd, nowMs)
    },
    onOutcome: (run, task) => {
      if (!task.notify) return
      let kind: SchedulerNoticeKind | null = null
      if (run.status === 'completed') kind = 'task-completed'
      else if (run.status === 'failed') {
        kind = task.enabled ? 'task-failed' : 'task-paused'
      } else if (run.status === 'skipped-overlap') kind = 'task-skipped'
      if (kind === null) return
      runtime.notifyAttention(schedulerNotice(runtime.locale, kind, task.name))
    },
  }, DEFAULT_SETTINGS.maxConcurrent)

  // Engine start lives inside the settings injection on purpose: the scope
  // must be resolved before start so the user's misfire window (not the
  // default) governs recovery. `engine.start` is idempotent, and disposal
  // stops the engine together with the settings watch.
  ctx.inject(['settings'], (settingsCtx) => {
    settingsCtx.effect(() => {
      const scope = settingsCtx.settings.register(
        DESKTOP_SCHEDULER_SETTINGS_NAMESPACE,
        DesktopSchedulerSettingsSchema,
        { applies: 'live' },
      )
      settings = scope.get()
      engine.setMaxConcurrent(settings.maxConcurrent)
      const stopWatching = scope.watch((next) => {
        settings = next
        engine.setMaxConcurrent(next.maxConcurrent)
      })
      void engine.start(settings.misfireWindowMinutes * 60_000).catch(cause => {
        log(`scheduler: start failed: ${cause instanceof Error ? cause.message : String(cause)}`)
      })
      return () => {
        stopWatching()
        settings = DEFAULT_SETTINGS
        void engine.stop().catch(cause => {
          log(`scheduler: stop failed: ${cause instanceof Error ? cause.message : String(cause)}`)
        })
      }
    }, 'dsh-plugin-desktop: scheduler settings and engine')
  })

  ctx.inject(['sessions'], (sessionsCtx) => {
    sessionsCtx.effect(() => {
      const stopEvents = sessionsCtx.on('session/event', (session: Session, event: SessionEvent) => {
        if (event.type !== 'turn/end') return
        const reason: unknown = (event.data as { reason?: { kind?: unknown } }).reason
        const outcome = (reason as { kind?: unknown } | undefined)?.kind === 'completed'
          ? 'completed'
          : 'failed'
        void engine.completeTurn(String(session.header.id), outcome).catch(cause => {
          log(`scheduler: completeTurn failed: ${cause instanceof Error ? cause.message : String(cause)}`)
        })
      })
      return () => { stopEvents() }
    }, 'dsh-plugin-desktop: scheduled run turn tracking')
  })

  const MAX_TASK_BODY_BYTES = 256 * 1024

  /** Register one guarded exact route. */
  const route = (method: 'GET' | 'POST', path: string, handler: (req: IncomingMessage, res: ServerResponse) => void): void => {
    ctx.effect(() => ctx.webServer.register({
      kind: 'exact',
      path,
      handler: (req, res) => {
        const rejection = ctx.connection.requestRejection(req)
        if (rejection !== undefined) {
          res.writeHead(rejection)
          res.end(rejection === 401 ? 'unauthorized' : 'forbidden')
          return
        }
        if (req.method !== method) {
          json(res, 405, { error: 'method-not-allowed' })
          return
        }
        handler(req, res)
      },
    }), `dsh-plugin-desktop: scheduler route ${path}`)
  }

  const parseTaskInput = (body: Record<string, unknown>): ScheduledTask | string => {
    // Client input never carries server-owned facts; creation time defaults
    // to now and upsertTask preserves the stored value for edits.
    const normalized = normalizeScheduledTask({
      createdAt: new Date().toISOString(),
      notify: true,
      enabled: true,
      ...body,
    })
    if (normalized === null) return 'invalid task payload'
    if (!validateScheduleRule(normalized.schedule).ok) return 'invalid schedule rule'
    return normalized
  }

  route('GET', '/api/desktop/scheduler/tasks', (_req, res) => {
    const runs: Record<string, TaskRun[]> = {}
    for (const task of engine.listTasks()) runs[task.id] = [...engine.listRuns(task.id)]
    json(res, 200, {
      tasks: engine.listTasks(),
      runs,
      settings: {
        enabled: settings.enabled,
        maxConcurrent: settings.maxConcurrent,
        misfireWindowMinutes: settings.misfireWindowMinutes,
        quietHoursEnabled: settings.quietHoursEnabled,
        quietStart: settings.quietStart,
        quietEnd: settings.quietEnd,
      },
      pauseThreshold: PAUSE_AFTER_CONSECUTIVE_FAILURES,
    })
  })

  route('POST', '/api/desktop/scheduler/task', (req, res) => {
    void readMutatingBody(req, MAX_TASK_BODY_BYTES).then(async body => {
      if (body === null) {
        json(res, 400, { error: 'invalid-request' })
        return
      }
      const parsed = parseTaskInput(body)
      if (typeof parsed === 'string') {
        json(res, 400, { error: parsed })
        return
      }
      const stored = await engine.upsertTask(parsed)
      json(res, 200, { task: stored })
    }).catch(cause => {
      json(res, 500, { error: cause instanceof Error ? cause.message : 'upsert failed' })
    })
  })

  route('POST', '/api/desktop/scheduler/task/enable', (req, res) => {
    void readMutatingBody(req, 4096).then(async body => {
      const id = typeof body?.id === 'string' ? body.id : ''
      const enabled = body?.enabled === true
      if (id === '' || !(await engine.setTaskEnabled(id, enabled))) {
        json(res, 404, { error: 'unknown-task' })
        return
      }
      json(res, 200, { ok: true })
    }).catch(cause => {
      json(res, 500, { error: cause instanceof Error ? cause.message : 'enable failed' })
    })
  })

  route('POST', '/api/desktop/scheduler/task/run', (req, res) => {
    void readMutatingBody(req, 4096).then(body => {
      const id = typeof body?.id === 'string' ? body.id : ''
      if (id === '' || !engine.runNow(id)) {
        json(res, 404, { error: 'unknown-task' })
        return
      }
      json(res, 200, { ok: true })
    }).catch(cause => {
      json(res, 500, { error: cause instanceof Error ? cause.message : 'run failed' })
    })
  })

  route('POST', '/api/desktop/scheduler/task/delete', (req, res) => {
    void readMutatingBody(req, 4096).then(async body => {
      const id = typeof body?.id === 'string' ? body.id : ''
      if (id === '' || !(await engine.removeTask(id))) {
        json(res, 404, { error: 'unknown-task' })
        return
      }
      json(res, 200, { ok: true })
    }).catch(cause => {
      json(res, 500, { error: cause instanceof Error ? cause.message : 'delete failed' })
    })
  })

  route('POST', '/api/desktop/scheduler/preview', (req, res) => {
    void readMutatingBody(req, 16 * 1024).then(body => {
      const rule = normalizeScheduleRuleForPreview(body?.schedule)
      if (rule === null) {
        json(res, 400, { error: 'invalid schedule rule' })
        return
      }
      json(res, 200, {
        next: previewRuleRuns(rule, 3, new Date()).map(instant => instant.toISOString()),
      })
    }).catch(cause => {
      json(res, 500, { error: cause instanceof Error ? cause.message : 'preview failed' })
    })
  })

  route('GET', '/api/desktop/scheduler/presets', (_req, res) => {
    const permissionPresets = ctx.get('permissionPresets') as {
      names?: readonly string[]
    } | undefined
    json(res, 200, { presets: permissionPresets?.names ?? [] })
  })

  route('GET', '/api/desktop/scheduler/next-run', (_req, res) => {
    const tasks = engine.listTasks()
    const anchor = Date.now()
    json(res, 200, {
      next: tasks.map(task => ({
        id: task.id,
        nextRunAt: task.nextRunAt,
        recomputed: task.enabled
          ? computeNextRunMs(task.schedule, Date.parse(task.lastRunAt ?? task.createdAt) || anchor, anchor)
          : null,
      })),
    })
  })

  /** Permission preset names with a graceful empty fallback. */
  const presetNames = (): readonly string[] => {
    const permissionPresets = ctx.get('permissionPresets') as {
      names?: readonly string[]
    } | undefined
    return permissionPresets?.names ?? []
  }

  /** Interpret one sentence through the default model, degrading to `null`. */
  const interpretSchedule = async (text: string): Promise<{ expression: string } | null> => {
    const llm = ctx.get('llm') as {
      stream(options: unknown): AsyncIterable<unknown>
    } | undefined
    const selection = (ctx.get('agentDefaultModel') as {
      currentSelection?(): { provider: string; model: string }
    } | undefined)?.currentSelection?.()
    if (llm === undefined || selection === undefined) {
      log('scheduler: sentence interpretation unavailable; no composed llm or default model')
      return null
    }
    const chunks: string[] = []
    const stream = llm.stream(nlScheduleOptions(selection, text, runtime.locale, freshSignal(20_000)))
    for await (const chunk of stream as AsyncIterable<{ text?: string }>) {
      if (typeof chunk.text === 'string') chunks.push(chunk.text)
    }
    return parseNlScheduleAnswer(chunks.join(''))
  }

  const openTaskEditor = (): void => {
    runtime.openTaskEditorWindow?.({
      list: async () => ({
        tasks: engine.listTasks(),
        runs: Object.fromEntries(engine.listTasks().map((task): [string, TaskRun[]] => [task.id, [...engine.listRuns(task.id)]])),
        presets: presetNames(),
      }),
      upsert: async task => {
        const parsed = parseTaskInput(task as Record<string, unknown>)
        if (typeof parsed === 'string') throw new Error(parsed)
        return engine.upsertTask(parsed)
      },
      remove: async id => { await engine.removeTask(id) },
      runNow: async id => engine.runNow(id),
      setEnabled: async (id, enabled) => engine.setTaskEnabled(id, enabled),
      preview: async schedule => {
        const rule = normalizeScheduleRuleForPreview(schedule)
        if (rule === null) throw new Error('invalid schedule rule')
        return previewRuleRuns(rule, 3, new Date()).map(instant => instant.toISOString())
      },
      interpretSchedule,
      pickDirectory: async () => runtime.pickDirectory(),
    })
  }

  ctx.effect(() => {
    const registration = runtime.registerTrayItem({
      group: 'tools',
      order: 45,
      label: () => schedulerTrayLabel(runtime.locale),
      enabled: () => runtime.openTaskEditorWindow !== undefined,
      invoke: () => { openTaskEditor() },
    })
    return () => { registration.dispose() }
  }, 'dsh-plugin-desktop: scheduler tray item')

  route('POST', '/api/desktop/scheduler/open-editor', (_req, res) => {
    if (runtime.openTaskEditorWindow === undefined) {
      json(res, 501, { error: 'editor window unavailable in this environment' })
      return
    }
    openTaskEditor()
    json(res, 200, { ok: true })
  })
}

/** Normalize one unknown preview schedule payload without task wrapping. */
function normalizeScheduleRuleForPreview(value: unknown): ScheduledTask['schedule'] | null {
  if (typeof value !== 'object' || value === null) return null
  const probe = normalizeScheduledTask({
    id: 'preview',
    name: 'preview',
    prompt: 'preview',
    createdAt: '2026-01-01T00:00:00.000Z',
    schedule: value,
  })
  return probe?.schedule ?? null
}
