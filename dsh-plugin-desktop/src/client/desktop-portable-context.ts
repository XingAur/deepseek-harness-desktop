/** Bounded, text-only handoff from a loaded Session event window. */
import type { SessionEventWindow } from '@deepseek-ai/dsh-api-session-controller/client'

const MAX_MESSAGES = 30
const MAX_CHARS = 12_000

/** Build a draft for human review; never include injected context, reasoning, tool calls, or attachment metadata. */
export function portableContextDraft(window: SessionEventWindow): { text: string; incomplete: boolean } {
  const messages: { role: 'user' | 'assistant'; text: string }[] = []
  // Replaced surface nodes may still be present in the event window; never export shadowed text.
  const shadowed = new Set<number>()
  let omittedReplacements = false
  for (const entry of window.entries) {
    if (entry.type !== 'event') continue
    const event = entry.event
    if ((event.type === 'user/message' || event.type === 'assistant/message' || event.type === 'tool/result')
      && event.surfaceOp !== 'append') {
      omittedReplacements = true
      for (const seq of event.sourceEventSeqs ?? []) shadowed.add(seq)
    }
  }
  for (const entry of window.entries) {
    if (entry.type !== 'event') continue
    const event = entry.event
    if (shadowed.has(event.seq)) continue
    if (event.type === 'user/message') {
      if (event.surfaceOp !== 'append' || event.data.source.kind !== 'user') continue
      const text = event.data.content.flatMap(block => block.type === 'text' ? [block.text] : []).join('\n').trim()
      if (text) messages.push({ role: 'user', text })
    } else if (event.type === 'assistant/message') {
      if (event.surfaceOp !== 'append') continue
      const text = event.data.message.content.flatMap(block => block.type === 'text' ? [block.text] : []).join('\n').trim()
      if (text) messages.push({ role: 'assistant', text })
    }
  }
  const selected: typeof messages = []
  let remaining = MAX_CHARS
  for (let index = messages.length - 1; index >= 0 && selected.length < MAX_MESSAGES; index--) {
    const message = messages[index]!
    if (message.text.length > remaining) break
    selected.unshift(message)
    remaining -= message.text.length
  }
  return {
    text: selected.map(message => `### ${message.role === 'user' ? 'User' : 'Assistant'}\n\n${message.text}`).join('\n\n'),
    incomplete: window.hasMore || omittedReplacements || selected.length !== messages.length,
  }
}
