import type { HarnessAgentExecutor } from './harness-host-handler.js'
import type { ProviderAdapter } from './providers/contracts.js'

export interface DeepSeekExecutorOptions {
  /** The credential remains in the desktop host and is never sent to Harness. */
  adapter: ProviderAdapter
  apiKey: string
  model: string
}

/**
 * Build a provider-neutral DeepSeek executor.
 *
 * Harness owns the role decision and the current selected model is used for
 * every phase. The adapter intentionally does not turn a worker request into
 * an alternate provider or silently claim that a text response changed files;
 * the Harness worker contract remains responsible for applying and verifying
 * any returned change plan.
 */
export function createDeepSeekExecutor(options: DeepSeekExecutorOptions): HarnessAgentExecutor {
  if (typeof options?.adapter?.stream !== 'function') throw new TypeError('DeepSeek Provider 无效')
  if (typeof options.apiKey !== 'string' || options.apiKey.trim() === '') throw new TypeError('DeepSeek API Key 无效')
  if (typeof options.model !== 'string' || options.model.trim() === '') throw new TypeError('DeepSeek 模型无效')

  return async (request, context) => {
    const chunks: string[] = []
    for await (const event of options.adapter.stream({
      model: options.model,
      apiKey: options.apiKey,
      messages: [{ role: 'user', content: request.prompt }],
      signal: context.signal,
    })) {
      if (event.type === 'message.delta') {
        chunks.push(event.text)
        context.emit({ type: 'message.delta', text: event.text })
      } else if (event.type === 'usage.updated') {
        context.emit({ type: 'usage.updated', usage: event.usage })
      }
    }

    return {
      finalResponse: {
        ...(request.output_contract.schema_version === 'none' ? {} : { schema_version: request.output_contract.schema_version }),
        text: chunks.join(''),
      },
    }
  }
}

/** Backward-compatible alias for integrations that used the old name. */
export const createDeepSeekReviewerExecutor = createDeepSeekExecutor
export type DeepSeekReviewerExecutorOptions = DeepSeekExecutorOptions
