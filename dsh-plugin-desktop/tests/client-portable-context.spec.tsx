// @vitest-environment jsdom
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SessionEventWindow } from '@deepseek-ai/dsh-api-session-controller/client'
import { DesktopPortableContext } from '../src/client/DesktopPortableContext.tsx'
import { portableContextDraft } from '../src/client/desktop-portable-context.ts'
import { zh } from '../src/client/desktop-portable-locales.ts'

function windowOf(entries: unknown[], hasMore = false): SessionEventWindow {
  return { entries, hasMore, revision: 0, change: { kind: 'replace', entries } } as SessionEventWindow
}
function message(type: string, data: unknown, surfaceOp = 'append'): unknown {
  return { type: 'event', event: { type, data, surfaceOp } }
}

afterEach(() => { vi.unstubAllGlobals() })

describe('portable context handoff', () => {
  it('includes only appended user and assistant text, never context, reasoning, tools, or files', () => {
    const draft = portableContextDraft(windowOf([
      message('user/message', { source: { kind: 'plugin' }, content: [{ type: 'text', text: 'hidden instruction' }] }),
      message('user/message', { source: { kind: 'user' }, content: [{ type: 'text', text: 'question' }, { type: 'file', attachment: { name: 'secret' } }] }),
      message('assistant/message', { message: { content: [{ type: 'reasoning', text: 'private thought' }, { type: 'text', text: 'answer' }, { type: 'tool-call', arguments: 'token' }] } }),
      message('assistant/message', { message: { content: [{ type: 'text', text: 'replaced' }] } }, 'replace'),
      message('tool/result', { content: 'secret' }),
    ]))
    expect(draft).toEqual({ text: '### User\n\nquestion\n\n### Assistant\n\nanswer', incomplete: true })
  })

  it('does not leak an older message shadowed by a later surface replacement', () => {
    const draft = portableContextDraft(windowOf([
      { type: 'event', event: { type: 'user/message', seq: 3, surfaceOp: 'append', data: {
        source: { kind: 'user' }, content: [{ type: 'text', text: 'superseded secret' }],
      } } },
      { type: 'event', event: { type: 'user/message', seq: 5,
        surfaceOp: { op: 'replace', start: 3, end: 3 }, sourceEventSeqs: [3], data: {
          source: { kind: 'user' }, content: [{ type: 'text', text: 'replacement' }],
        } } },
    ]))
    expect(draft).toEqual({ text: '', incomplete: true })
  })

  it('bounds recent messages and marks partial history', () => {
    const entries = Array.from({ length: 32 }, (_, index) => message('user/message', {
      source: { kind: 'user' }, content: [{ type: 'text', text: `message-${index}` }],
    }))
    const draft = portableContextDraft(windowOf(entries, true))
    expect(draft.incomplete).toBe(true)
    expect(draft.text).not.toContain('message-0\n')
    expect(draft.text).toContain('message-31')
  })

  it('marks oversized latest text as incomplete rather than copying a silent fragment', () => {
    const draft = portableContextDraft(windowOf([message('user/message', {
      source: { kind: 'user' }, content: [{ type: 'text', text: 'x'.repeat(12_001) }],
    })]))
    expect(draft).toEqual({ text: '', incomplete: true })
  })

  it('waits for review and copies the edited draft only on explicit confirmation', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    const writeText = vi.fn(async (_text: string) => {})
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    const container = document.createElement('div')
    document.body.append(container)
    const root: Root = createRoot(container)
    try {
      await act(async () => { root.render(createElement(DesktopPortableContext, {
        sessionId: 'session-1', readDraft: () => ({ text: 'private draft', incomplete: true }),
        t: (key: keyof typeof zh) => zh[key],
      } as Parameters<typeof DesktopPortableContext>[0])) })
      expect(writeText).not.toHaveBeenCalled()
      await act(async () => { container.querySelector('button')!.click() })
      expect(container.querySelector('[role="dialog"]')).not.toBeNull()
      expect(container.textContent).toContain(zh.partial)
      expect(writeText).not.toHaveBeenCalled()
      const textarea = container.querySelector('textarea')!
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
        setter.call(textarea, 'reviewed text')
        textarea.dispatchEvent(new Event('input', { bubbles: true }))
      })
      const copy = [...container.querySelectorAll('button')].find(button => button.textContent === zh.copy)!
      await act(async () => { copy.click() })
      expect(writeText).toHaveBeenCalledWith('reviewed text')
      expect(container.textContent).toContain(zh.copied)
      await act(async () => { [...container.querySelectorAll('button')].find(button => button.textContent === zh.cancel)!.click() })
      expect(container.querySelector('[role="dialog"]')).toBeNull()
      expect(writeText).toHaveBeenCalledTimes(1)
    } finally {
      await act(async () => { root.unmount() })
      container.remove()
    }
  })
})
