import { classifyProviderFailure } from './error-classification.js'
import { openProviderStream, readSseEvents } from './http.js'
import type { ProviderAdapter, ProviderEvent, ProviderRequest } from './contracts.js'

export interface OpenAICompatibleAdapterOptions {
  baseUrl: string
  allowLoopback?: boolean
  providerId?: string
}

export function createOpenAICompatibleAdapter(options: OpenAICompatibleAdapterOptions): ProviderAdapter {
  const providerId = options.providerId ?? 'openai-compatible'
  return {
    providerId,
    async *stream(request: ProviderRequest): AsyncGenerator<ProviderEvent> {
      const response = await openProviderStream({
        url: `${options.baseUrl.replace(/\/$/, '')}/chat/completions`,
        headers: { authorization: `Bearer ${request.apiKey}`, 'content-type': 'application/json', accept: 'text/event-stream' },
        body: JSON.stringify({ model: request.model, messages: request.messages, stream: true, stream_options: { include_usage: true }, ...(request.maxTokens === undefined ? {} : { max_tokens: request.maxTokens }), ...(request.temperature === undefined ? {} : { temperature: request.temperature }) }),
        signal: request.signal,
      })
      for await (const frame of readSseEvents(response.body)) {
        if (frame.data === '[DONE]') return
        let value: unknown
        try { value = JSON.parse(frame.data) } catch { throw classifyProviderFailure({ malformedStream: true }) }
        const record = asRecord(value)
        const choice = Array.isArray(record.choices) && isRecord(record.choices[0]) ? record.choices[0] : undefined
        const delta = choice !== undefined && isRecord(choice.delta) ? choice.delta : undefined
        if (typeof delta?.content === 'string' && delta.content.length > 0) yield { type: 'message.delta', text: delta.content }
        if (typeof choice?.finish_reason === 'string') yield { type: 'message.completed', finishReason: choice.finish_reason }
        if (isRecord(record.usage) && typeof record.usage.prompt_tokens === 'number' && typeof record.usage.completion_tokens === 'number') {
          yield { type: 'usage.updated', usage: { inputTokens: record.usage.prompt_tokens, outputTokens: record.usage.completion_tokens } }
        }
      }
    },
  }
}

export function createDeepSeekAdapter(baseUrl = 'https://api.deepseek.com/v1'): ProviderAdapter {
  return createOpenAICompatibleAdapter({ baseUrl, providerId: 'deepseek' })
}

export function createOpenAIAdapter(baseUrl = 'https://api.openai.com/v1'): ProviderAdapter {
  return createOpenAICompatibleAdapter({ baseUrl, providerId: 'openai' })
}

function asRecord(value: unknown): Record<string, any> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw classifyProviderFailure({ malformedStream: true })
  return value as Record<string, any>
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
