/**
 * Composition smoke for the scheduler row: executes `apply` against a minimal
 * fake Cordis context, then drives the registered routes end to end so the
 * runtime wiring (route handlers, settings injection, tray gateway, session
 * event tracking, effect disposal) is exercised, not just typechecked.
 */

import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type { WebRoute } from '@deepseek-ai/dsh-host-webserver'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apply, type DesktopSchedulerSettings } from '../src/scheduler/index.ts'
import type { DesktopRuntime } from '../src/runtime.ts'

interface RecordedResponse {
  status: number
  body: string
  readonly done: Promise<void>
}

function fakeRequest(method: string, body?: unknown): IncomingMessage {
  const chunks = body === undefined ? [] : [Buffer.from(JSON.stringify(body))]
  let index = 0
  const req = {
    method,
  } as IncomingMessage & AsyncIterable<Buffer>
  ;(req as unknown as { [Symbol.asyncIterator](): AsyncIterator<Buffer> })[Symbol.asyncIterator] =
    () => ({
      next: async () => index < chunks.length
        ? { value: chunks[index++] as Buffer, done: false }
        : { value: undefined, done: true },
    })
  return req
}

function fakeResponse(): ServerResponse & { readonly record: Promise<RecordedResponse> } {
  const listeners = new Set<() => void>()
  const record: RecordedResponse = {
    status: 0,
    body: '',
    done: new Promise(resolve => {
      listeners.add(resolve)
    }),
  }
  const res = {
    writeHead(status: number) {
      record.status = status
    },
    end(body?: string) {
      record.body = body ?? ''
      for (const listener of listeners) listener()
    },
  } as unknown as ServerResponse & { readonly record: Promise<RecordedResponse> }
  Object.defineProperty(res, 'record', { value: record.done.then(() => record) })
  return res
}

function createFakeContext(overrides?: {
  settings?: Partial<DesktopSchedulerSettings>
  presetNames?: readonly string[]
  editorCapable?: boolean
}): {
  ctx: Context
  routes: Map<string, WebRoute>
  disposers: Array<() => void>
  runtime: DesktopRuntime
  sessionEventListeners: Array<(payload: unknown) => void>
  registeredSettings: { namespace: string; schema: unknown; options: unknown } | undefined
} {
  const routes = new Map<string, WebRoute>()
  const disposers: Array<() => void> = []
  const sessionEventListeners: Array<(payload: unknown) => void> = []
  const home = mkdtempSync(join(tmpdir(), 'dsh-scheduler-compose-'))
  process.env.DSH_HOME = home

  const settingsValue: DesktopSchedulerSettings = {
    enabled: true,
    maxConcurrent: 3,
    misfireWindowMinutes: 15,
    quietHoursEnabled: false,
    quietStart: '23:00',
    quietEnd: '07:00',
    ...overrides?.settings,
  }

  const runtime: DesktopRuntime = {
    platform: 'darwin',
    locale: 'zh',
    updates: {
      isPackaged: false,
      canDownload: false,
      currentVersion: '0.0.0',
      statePath: join(home, 'update-state.json'),
      request: async () => new Response(null, { status: 304 }),
      confirmDownload: async () => false,
      showManualCheckResult: async () => {},
      downloadAndOpen: async () => {},
      notify: () => {},
    },
    schedule: () => async () => {},
    mountScheduled: async () => {},
    show: () => {},
    notifyAttention: vi.fn(),
    pickDirectory: async () => null,
    validateDirectory: async () => true,
    registerTrayItem: vi.fn(() => ({ dispose: () => {} })),
    ...(overrides?.editorCapable === false ? {} : {
      openTaskEditorWindow: vi.fn(),
    }),
  } as unknown as DesktopRuntime

  const services = new Map<string, unknown>([
    ['desktopRuntime', runtime],
    [
      'permissionPresets',
      { names: overrides?.presetNames ?? ['workspace-write', 'danger-full-access'] },
    ],
  ])

  const ctx = {
    get: (name: string) => services.get(name),
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    },
    effect: (execute: () => unknown, _label?: string) => {
      const disposer = execute()
      if (typeof disposer === 'function') disposers.push(disposer as () => void)
    },
    inject: (deps: readonly string[], fn: (child: unknown) => void) => {
      const childEffect = (execute: () => unknown, _label?: string) => {
        const disposer = execute()
        if (typeof disposer === 'function') disposers.push(disposer as () => void)
      }
      if (deps.includes('settings')) {
        fn({
          effect: childEffect,
          settings: {
            register: (namespace: string, schema: unknown, options: unknown) => {
              registered = { namespace, schema, options }
              return {
                get: () => settingsValue,
                watch: () => () => {},
              }
            },
          },
        })
      }
      if (deps.includes('sessions')) {
        fn({
          effect: childEffect,
          on: (_event: string, listener: (payload: unknown) => void) => {
            sessionEventListeners.push(listener)
            return () => {}
          },
        })
      }
    },
    webServer: {
      register: (route: WebRoute) => {
        routes.set(`${route.kind}:${route.path}`, route)
        return () => { routes.delete(`${route.kind}:${route.path}`) }
      },
    },
    connection: {
      requestRejection: () => undefined,
    },
  } as unknown as Context

  let registered: { namespace: string; schema: unknown; options: unknown } | undefined

  return { ctx, routes, disposers, runtime, sessionEventListeners, get registeredSettings() { return registered } }
}

async function settle(times = 6): Promise<void> {
  for (let index = 0; index < times; index++) {
    await new Promise(resolve => setImmediate(resolve))
  }
}

describe('desktop-scheduler composition smoke', () => {
  let harness: ReturnType<typeof createFakeContext> | undefined
  let home: string | undefined

  function compose(overrides?: Parameters<typeof createFakeContext>[0]): ReturnType<typeof createFakeContext> {
    home = mkdtempSync(join(tmpdir(), 'dsh-scheduler-home-'))
    process.env.DSH_HOME = home
    harness = createFakeContext(overrides)
    apply(harness.ctx)
    return harness
  }

  afterEach(() => {
    if (harness !== undefined) {
      for (const disposer of harness.disposers) disposer()
      harness = undefined
    }
    delete process.env.DSH_HOME
    if (home !== undefined) {
      rmSync(home, { recursive: true, force: true })
      home = undefined
    }
  })

  it('registers every scheduler route, settings namespace, and the tray item', async () => {
    const app = compose()
    await settle()

    for (const path of [
      '/api/desktop/scheduler/tasks',
      '/api/desktop/scheduler/task',
      '/api/desktop/scheduler/task/enable',
      '/api/desktop/scheduler/task/run',
      '/api/desktop/scheduler/task/delete',
      '/api/desktop/scheduler/preview',
      '/api/desktop/scheduler/presets',
      '/api/desktop/scheduler/next-run',
      '/api/desktop/scheduler/open-editor',
    ]) {
      expect(app.routes.get(`exact:${path}`), path).toBeDefined()
    }
    expect(app.registeredSettings?.namespace).toBe('dsh-desktop-scheduler')
    expect(app.runtime.registerTrayItem).toHaveBeenCalledWith(expect.objectContaining({
      group: 'tools',
      order: 45,
    }))
  })

  it('creates, lists, and deletes a task through the route handlers', async () => {
    const app = compose()
    await settle()

    const create = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/task')?.handler(
      fakeRequest('POST', {
        id: 'compose-1',
        name: '组合任务',
        prompt: '报告进度',
        schedule: { kind: 'every', seconds: 3600 },
      }),
      create,
    )
    const created = await create.record
    expect(created.status).toBe(200)
    expect(JSON.parse(created.body).task.id).toBe('compose-1')

    const list = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/tasks')?.handler(fakeRequest('GET'), list)
    const listed = await list.record
    expect(listed.status).toBe(200)
    const payload = JSON.parse(listed.body) as { tasks: Array<{ id: string }>; settings: { misfireWindowMinutes: number } }
    expect(payload.tasks.map(task => task.id)).toContain('compose-1')
    expect(payload.settings.misfireWindowMinutes).toBe(15)

    const remove = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/task/delete')?.handler(
      fakeRequest('POST', { id: 'compose-1' }),
      remove,
    )
    expect((await remove.record).status).toBe(200)

    const listAgain = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/tasks')?.handler(fakeRequest('GET'), listAgain)
    expect((JSON.parse((await listAgain.record).body) as { tasks: unknown[] }).tasks).toHaveLength(0)
  })

  it('previews a cron rule and rejects invalid bodies', async () => {
    const app = compose()
    await settle()

    const preview = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/preview')?.handler(
      fakeRequest('POST', { schedule: { kind: 'cron', expression: '0 9 * * *' } }),
      preview,
    )
    const previewed = await preview.record
    expect(previewed.status).toBe(200)
    const next = JSON.parse(previewed.body) as { next: string[] }
    expect(next.next).toHaveLength(3)
    for (const instant of next.next) expect(instant).toMatch(/^\d{4}-\d{2}-\d{2}T/)

    const invalid = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/task')?.handler(
      fakeRequest('POST', { id: '', name: '', prompt: '', schedule: { kind: 'cron', expression: 'nope' } }),
      invalid,
    )
    expect((await invalid.record).status).toBe(400)
  })

  it('opens the editor through tray invoke and the open-editor route', async () => {
    const app = compose({ presetNames: ['workspace-write'] })
    await settle()

    const invoke = app.runtime.registerTrayItem as ReturnType<typeof vi.fn>
    const traySpec = invoke.mock.calls[0]?.[0] as { invoke: () => void; enabled: () => boolean }
    expect(traySpec.enabled()).toBe(true)
    traySpec.invoke()
    const gatewayArg = (app.runtime.openTaskEditorWindow as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as {
      list: () => Promise<{ presets: readonly string[] }>
    }
    await expect(gatewayArg.list()).resolves.toMatchObject({ presets: ['workspace-write'] })

    const open = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/open-editor')?.handler(fakeRequest('POST'), open)
    expect((await open.record).status).toBe(200)
    expect((app.runtime.openTaskEditorWindow as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(2)
  })

  it('answers 501 for the editor route when the window capability is absent', async () => {
    const app = compose({ editorCapable: false })
    await settle()

    const open = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/open-editor')?.handler(fakeRequest('POST'), open)
    expect((await open.record).status).toBe(501)
  })

  it('tracks session turn/end events through the sessions injection', async () => {
    const app = compose()
    await settle()
    expect(app.sessionEventListeners).toHaveLength(1)

    const listener = app.sessionEventListeners[0] as (session: unknown, event: unknown) => void
    expect(() => listener(
      { header: { id: 'session-xyz' } },
      { type: 'turn/end', data: { reason: { kind: 'completed' } } },
    )).not.toThrow()
    await settle()
  })

  it('rejects wrong-method requests with 405', async () => {
    const app = compose()
    await settle()

    const wrong = fakeResponse()
    app.routes.get('exact:/api/desktop/scheduler/tasks')?.handler(fakeRequest('POST'), wrong)
    const recorded = await wrong.record
    expect(recorded.status).toBe(405)
    expect(JSON.parse(recorded.body).error).toBe('method-not-allowed')
  })
})
