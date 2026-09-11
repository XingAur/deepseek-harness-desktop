import { describe, expect, it } from 'vitest'
import {
  displayTitleOf,
  mobilePromptParts,
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
    const lines = mobileTranscriptFromEvents([
      { type: 'user/message', time: 1, data: { content: [{ type: 'text', text: 'hi' }], source: { kind: 'user' } } },
      { type: 'user/message', time: 2, data: { content: [{ type: 'text', text: 'skill body' }], source: { kind: 'inject' } } },
      { type: 'assistant/message', time: 3, data: { message: { content: [{ type: 'text', text: 'hello there' }] } } },
      { type: 'event', event: { type: 'user/message', time: 4, data: { content: 'plain' } } },
    ])
    expect(lines).toEqual([
      { role: 'user', text: 'hi', time: 1 },
      { role: 'assistant', text: 'hello there', time: 3 },
      { role: 'user', text: 'plain', time: 4 },
    ])
  })
})

describe('textFromPromptContent', () => {
  it('joins text parts and ignores non-text blocks', () => {
    expect(textFromPromptContent([{ type: 'text', text: 'a' }, { type: 'image' }, { type: 'text', text: 'b' }])).toBe('ab')
    expect(textFromPromptContent(' raw ')).toBe('raw')
  })
})
