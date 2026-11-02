/**
 * Natural-language schedule interpretation: one auxiliary model call that
 * proposes a cron expression, then a croner re-validation that never trusts
 * the model output as schedulable without proof.
 */

import { createUserMessage, type GenerateOptions, type StreamChunk } from '@deepseek-ai/dsh-llm'
import { Cron } from 'croner'
import { validateCronExpression } from './next-run.ts'

/** Minimal model-selection face the host resolves before calling. */
export interface NlScheduleModel {
  readonly provider: string
  readonly model: string
}

/** One successful interpretation. */
export interface NlScheduleResult {
  readonly expression: string
  /** Human explanation the model attached, when present. */
  readonly explanation: string | null
}

const NL_SYSTEM_PROMPT = [
  'You convert a user sentence into exactly one cron expression.',
  'Answer with ONE line in the form `cron <expression> | <short explanation>`.',
  'Use a standard 5-field cron (minute hour day-of-month month day-of-week).',
  'Prefer concrete minute values over ranges; never use @nicknames; never wrap in quotes.',
  'If the sentence is not a repeating schedule, answer exactly `none`.',
].join('\n')

/** Parse one model answer into a validated proposal; `null` when unusable. */
export function parseNlScheduleAnswer(answer: string): NlScheduleResult | null {
  const trimmed = answer.trim()
  if (trimmed === '' || /^none\b/iu.test(trimmed)) return null
  const line = trimmed.split('\n').map(candidate => candidate.trim()).find(candidate => /^cron\s/iu.test(candidate))
  if (line === undefined) return null
  // Cron expressions contain spaces; the `|` separator is the only delimiter.
  const [head, ...explanationParts] = line.split('|')
  const match = /^cron\s+(.+)$/iu.exec((head ?? '').trim())
  const captured = match?.[1]
  if (captured === undefined) return null
  const expression = captured.trim()
  if (!validateCronExpression(expression).ok) return null
  try {
    // Re-validate with croner itself: the expression must actually fire.
    new Cron(expression)
  } catch {
    return null
  }
  const explanation = explanationParts.join('|').trim()
  return { expression, explanation: explanation === '' ? null : explanation }
}

/** Build the one-shot generation options for one user sentence. */
export function nlScheduleOptions(
  model: NlScheduleModel,
  text: string,
  locale: 'en' | 'zh',
  signal: AbortSignal,
): GenerateOptions {
  const system = locale === 'zh'
    ? `${NL_SYSTEM_PROMPT}\n解释部分用中文。`
    : NL_SYSTEM_PROMPT
  return {
    provider: model.provider,
    model: model.model,
    system,
    messages: [createUserMessage({
      content: [{ type: 'text', text }],
      source: { kind: 'user' },
    })],
    maxTokens: 120,
    purpose: 'session-title',
    signal,
  }
}

/** Concatenate one stream into text. */
export async function streamText(stream: AsyncIterable<StreamChunk>): Promise<string> {
  let text = ''
  for await (const chunk of stream) {
    const piece = (chunk as { text?: string }).text
    if (typeof piece === 'string') text += piece
  }
  return text
}
