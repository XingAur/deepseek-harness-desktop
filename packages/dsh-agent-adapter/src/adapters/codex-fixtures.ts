export const codexEventFixtures = Object.freeze([
  { type: 'message.delta', text: 'hello' },
  { type: 'approval.requested', requestId: 'approval-1', capability: 'terminal' },
  { type: 'file.diff.available', contentRef: { id: 'diff-1', mediaType: 'text/x-diff', byteLength: 12, truncated: false } },
  { type: 'usage.updated', inputTokens: 4, outputTokens: 2 },
  { type: 'turn.completed' },
])
