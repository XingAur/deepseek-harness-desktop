export type ProviderMessageRole = 'system' | 'user' | 'assistant' | 'tool'

export interface ProviderMessage {
  role: ProviderMessageRole
  content: string
}

export interface ProviderRequest {
  model: string
  apiKey: string
  messages: ProviderMessage[]
  maxTokens?: number
  temperature?: number
  signal?: AbortSignal
}

export type ProviderEvent =
  | { type: 'message.delta'; text: string }
  | { type: 'message.completed'; finishReason: string }
  | { type: 'usage.updated'; usage: { inputTokens: number; outputTokens: number } }

export interface ProviderAdapter {
  readonly providerId: string
  stream(request: ProviderRequest): AsyncGenerator<ProviderEvent>
}
