import { describe, expect, it } from 'vitest'
import {
  diffLineCounts,
  displayTitleOf,
  mobileContextOf,
  mobileDiffSummaries,
  mobileModelOf,
  mobilePermissionsOf,
  mobilePromptParts,
  mobileTodoItems,
  mobileToolTitle,
  mobileTokensOf,
  mobileTranscriptFromEvents,
  textFromPromptContent,
} from '../src/mobile-api.ts'

describe('mobilePromptParts', () => {
  it('wraps raw text as the Host prompt content array', () => {
    expect(mobilePromptParts('hello')).toEqual([{ type: 'text', text: 'hello' }])
  })
})

describe('displayTitleOf', () => {
  it('prefers the projection title, then the cwd tail, then a short id', () => {
    expect(displayTitleOf('DSH', 'D:\\Code', 'abcdef123')).toBe('DSH')
    expect(displayTitleOf('', 'E:\\haier\\dev', 'abcdef123')).toBe('dev')
    expect(displayTitleOf(null, null, 'abcdef123')).toBe('abcdef12')
  })
})

describe('mobileTranscriptFromEvents', () => {
  it('keeps human prompts and assistant answers, skipping injected context', () => {
    const items = mobileTranscriptFromEvents([
      { type: 'user/message', time: 1, data: { content: [{ type: 'text', text: 'hi' }], source: { kind: 'user' } } },
      { type: 'user/message', time: 2, data: { content: [{ type: 'text', text: 'skill body' }], source: { kind: 'inject' } } },
      { type: 'assistant/message', time: 3, data: { message: { content: [{ type: 'text', text: 'hello there' }] } } },
      { type: 'event', event: { type: 'user/message', time: 4, data: { content: 'plain' } } },
    ])
    expect(items).toEqual([
      { kind: 'user', text: 'hi', time: 1 },
      { kind: 'assistant', text: 'hello there', time: 3 },
      { kind: 'user', text: 'plain', time: 4 },
    ])
  })

  it('emits reasoning blocks as collapsible items before the text', () => {
    const items = mobileTranscriptFromEvents([
      { type: 'assistant/message', time: 10, data: { message: { content: [
        { type: 'reasoning', text: ' let me think ' },
        { type: 'text', text: 'answer' },
      ] } } },
    ])
    expect(items).toEqual([
      { kind: 'reasoning', text: 'let me think', time: 10 },
      { kind: 'assistant', text: 'answer', time: 10 },
    ])
  })

  it('folds tool call and result into one row with status, duration, and diff counts', () => {
    const items = mobileTranscriptFromEvents([
      { type: 'tool/call', time: 100, data: { callId: 'c1', name: 'edit', arguments: '{"file_path":"src/app.ts"}' } },
      { type: 'tool/result', time: 160, data: { message: { callId: 'c1' }, meta: { diffs: [
        { path: 'src/app.ts', oldText: 'a\nb\nc', newText: 'a\nx\nc\nd' },
      ] } } },
      { type: 'tool/call', time: 200, data: { callId: 'c2', name: 'bash', arguments: '{"command":"pnpm test"}' } },
      { type: 'tool/result', time: 260, data: { message: { callId: 'c2' }, error: { name: 'Exit', code: 1 } } },
    ])
    expect(items).toHaveLength(2)
    expect(items[0]).toEqual({
      kind: 'tool', callId: 'c1', name: 'edit', title: 'src/app.ts', time: 100, end: 160, status: 'ok',
      diffs: [{ path: 'src/app.ts', added: 2, removed: 1 }],
    })
    expect(items[1]).toMatchObject({ kind: 'tool', callId: 'c2', title: 'pnpm test', status: 'error', diffs: null })
  })

  it('dedupes identical todo snapshots and keeps the rows bounded', () => {
    const todos = [{ content: 'step one', status: 'in_progress' as const }]
    const items = mobileTranscriptFromEvents([
      { type: 'todo/write', time: 1, data: { todos } },
      { type: 'todo/write', time: 2, data: { todos } },
      { type: 'todo/write', time: 3, data: { todos: [{ content: 'step one', status: 'completed' }] } },
    ])
    expect(items).toEqual([
      { kind: 'todo', todos: [{ content: 'step one', status: 'in_progress' }], time: 1 },
      { kind: 'todo', todos: [{ content: 'step one', status: 'completed' }], time: 3 },
    ])
  })
})

describe('diffLineCounts', () => {
  it('counts aligned insertions and deletions, ignoring context', () => {
    expect(diffLineCounts('a\nb\nc', 'a\nb\nc')).toEqual({ added: 0, removed: 0 })
    expect(diffLineCounts('a\nb\nc', 'a\nx\nc\nd')).toEqual({ added: 2, removed: 1 })
    expect(diffLineCounts(null, 'x\ny')).toEqual({ added: 2, removed: 0 })
  })
  it('refuses counts for oversized hunks', () => {
    const big = Array.from({ length: 201 }, (_, index) => `line-${String(index)}`).join('\n')
    expect(diffLineCounts(null, big)).toEqual({ added: null, removed: null })
  })
})

describe('mobileDiffSummaries', () => {
  it('returns null for malformed meta and caps the file list', () => {
    expect(mobileDiffSummaries({})).toBeNull()
    expect(mobileDiffSummaries({ diffs: 'nope' })).toBeNull()
    const many = { diffs: Array.from({ length: 12 }, (_, index) => ({ path: `f${String(index)}.ts',`, oldText: null, newText: 'x' })) }
    expect(mobileDiffSummaries(many)).toHaveLength(8)
  })
})

describe('mobileToolTitle', () => {
  it('prefers command and file tails from the raw JSON arguments', () => {
    expect(mobileToolTitle('bash', JSON.stringify({ command: 'pnpm  test\n' }))).toBe('pnpm test')
    expect(mobileToolTitle('edit', JSON.stringify({ file_path: 'D:\\repo\\src\\app.ts' }))).toBe('src/app.ts')
    expect(mobileToolTitle('mystery', 'not json')).toBe('mystery')
  })
})

describe('mobileTodoItems', () => {
  it('normalizes statuses and skips malformed rows', () => {
    expect(mobileTodoItems([{ content: 'a', status: 'weird' }, { content: '', status: 'pending' }, 'junk', { content: 'b', status: 'completed' }]))
      .toEqual([{ content: 'a', status: 'pending' }, { content: 'b', status: 'completed' }])
    expect(mobileTodoItems(undefined)).toBeNull()
  })
})

describe('textFromPromptContent', () => {
  it('joins text parts and ignores non-text blocks', () => {
    expect(textFromPromptContent([{ type: 'text', text: 'a' }, { type: 'image' }, { type: 'text', text: 'b' }])).toBe('ab')
    expect(textFromPromptContent(' raw ')).toBe('raw')
  })
})

describe('projection normalizers', () => {
  it('permissions keep ordered options with the current value', () => {
    expect(mobilePermissionsOf({ currentValue: 'read-only', options: [{ value: 'read-only', name: 'Read only' }] }))
      .toEqual({ currentValue: 'read-only', options: [{ value: 'read-only', name: 'Read only' }] })
    expect(mobilePermissionsOf({ currentValue: 'x' })).toBeNull()
    expect(mobilePermissionsOf(null)).toBeNull()
  })
  it('model selection prefers next over lastUsed', () => {
    expect(mobileModelOf({ next: { provider: 'p', model: 'm', reasoningEffort: 'high' } }))
      .toEqual({ provider: 'p', model: 'm', reasoningEffort: 'high' })
    expect(mobileModelOf({ lastUsed: { provider: 'p', model: 'm' } })).toEqual({ provider: 'p', model: 'm' })
    expect(mobileModelOf({})).toBeNull()
  })
  it('context derives a percent from projected tokens over the window', () => {
    expect(mobileContextOf({ projectedTokens: 50, pressureTokens: 40, contextWindow: 200 })).toEqual({ percent: 25, usedTokens: 50, contextWindow: 200 })
    expect(mobileContextOf({ pressureTokens: 40 })).toEqual({ percent: null, usedTokens: 40, contextWindow: null })
    expect(mobileContextOf({})).toBeNull()
  })
  it('token usage sums the disjoint buckets', () => {
    expect(mobileTokensOf({ uncachedInputTokens: 1, outputTokens: 2, cacheReadTokens: 3, cacheWriteTokens: 4 }))
      .toEqual({ inputTokens: 1, outputTokens: 2, cacheReadTokens: 3, cacheWriteTokens: 4 })
    expect(mobileTokensOf({})).toBeNull()
  })
})
