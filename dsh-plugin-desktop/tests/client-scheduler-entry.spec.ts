// @vitest-environment jsdom
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DesktopSchedulerEntry, type DesktopSchedulerEntryProps } from '../src/client/DesktopSchedulerEntry.tsx'
import { zh } from '../src/client/desktop-scheduler-locales.ts'

const TASKS_PAYLOAD = {
  tasks: [{
    id: 'task-1',
    name: '每日汇总',
    prompt: '汇总今天的工作',
    cwd: null,
    schedule: { kind: 'every', seconds: 1800 },
    enabled: true,
    sessionPolicy: 'fresh',
    permissionPreset: null,
    notify: true,
    nextRunAt: '2026-01-05T01:30:00.000Z',
    lastRunAt: null,
    consecutiveFailures: 0,
  }],
  runs: {},
}

function router(): (input: unknown, init?: RequestInit) => Promise<Response> {
  return async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : ''
    if (url === '/api/desktop/scheduler/tasks') {
      return await new Response(JSON.stringify(TASKS_PAYLOAD), { status: 200 })
    }
    if (url === '/api/desktop/scheduler/presets') {
      return await new Response(JSON.stringify({ presets: [] }), { status: 200 })
    }
    throw new Error(`unexpected fetch ${String(url)} ${String(init?.method ?? 'GET')}`)
  }
}

let root: Root | undefined
let container: HTMLDivElement | undefined

afterEach(() => {
  root?.unmount()
  container?.remove()
  vi.unstubAllGlobals()
})

describe('scheduler sidebar entry', () => {
  it('opens the in-app task dialog listing the stored tasks', async () => {
    const fetch = vi.fn(router())
    vi.stubGlobal('fetch', fetch)
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    const props = {
      wide: true,
      t: (key: keyof typeof zh) => zh[key],
    } as unknown as DesktopSchedulerEntryProps
    await act(async () => { root!.render(createElement(DesktopSchedulerEntry, props)) })
    const button = [...container.querySelectorAll('button')].find(item => item.textContent?.includes(zh.nav))
    if (button === undefined) throw new Error('scheduler button not found')
    await act(async () => { button.click() })
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog?.textContent).toContain('每日汇总')
    expect(dialog?.textContent).toContain(zh.newTask)
    const close = [...(dialog as HTMLElement).querySelectorAll('button')]
      .find(item => item.getAttribute('aria-label') === zh.close)
    if (close === undefined) throw new Error('close button not found')
    await act(async () => { close.click() })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('renders a rail-sized icon-only control when the column is collapsed', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    const props = {
      wide: false,
      t: (key: keyof typeof zh) => zh[key],
    } as unknown as DesktopSchedulerEntryProps
    await act(async () => { root!.render(createElement(DesktopSchedulerEntry, props)) })
    const button = container.querySelector('button.dshDesktopSchedulerEntryRail')
    expect(button).not.toBeNull()
    expect(button?.textContent).not.toContain(zh.nav)
    expect(button?.getAttribute('aria-label')).toBe(zh.nav)
  })
})
