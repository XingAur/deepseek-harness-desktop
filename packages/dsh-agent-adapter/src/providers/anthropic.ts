import { classifyProviderFailure } from './error-classification.js'
import { openProviderStream, readSseEvents } from './http.js'
import type { ProviderAdapter, ProviderEvent, ProviderRequest } from './contracts.js'

export interface AnthropicAdapterOptions {
  baseUrl: string
}

export function createAnthropicAdapter(options: AnthropicAdapterOptions): ProviderAdapter {
  return {
    providerId: 'claude',
    async *stream(request: ProviderRequest): AsyncGenerator<ProviderEvent> {
      const response = await openProviderStream({
        url: `${options.baseUrl.replace(/\/$/, '')}/v1/messages`,
        headers: { 'x-api-key': request.apiKey, 'anthropic-version': '2023-06-01', 'content-type': 'application/json', accept: 'text/event-stream' },
        body: JSON.stringify({ model: request.model, messages: request.messages.filter((message) => message.role !== 'system'), max_tokens: request.maxTokens ?? 4096, stream: true, ...(request.temperature === undefined ? {} : { temperature: request.temperature }), ...(request.messages.find((message) => message.role === 'system')?.content === undefined ? {} : { system: request.messages.find((message) => message.role === 'system')?.content }) }),
        signal: request.signal,
      })
      let completed = false
      for await (const frame of readSseEvents(response.body)) {
        let value: unknown
        try { value = JSON.parse(frame.data) } catch { throw classifyProviderFailure({ malformedStream: true }) }
        const record = asRecord(value)
        if (frame.event === 'content_block_delta' && isRecord(record.delta) && record.delta.type === 'text_delta' && typeof record.delta.text === 'string') {
          yield { type: 'message.delta', text: record.delta.text }
        } else if (frame.event === 'message_delta' && isRecord(record.delta) && typeof record.delta.stop_reason === 'string') {
          completed = true
          yield { type: 'message.completed', finishReason: record.delta.stop_reason }
          if (isRecord(record.usage) && typeof record.usage.output_tokens === 'number') yield { type: 'usage.updated', usage: { inputTokens: 0, outputTokens: record.usage.output_tokens } }
        } else if (frame.event === 'message_stop' && !completed) {
          yield { type: 'message.completed', finishReason: 'stop' }
        }
      }
    },
  }
}

function asRecord(value: unknown): Record<string, any> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw classifyProviderFailure({ malformedStream: true })
  return value as Record<string, any>
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
