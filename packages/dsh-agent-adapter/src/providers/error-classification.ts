export type ProviderErrorCode =
  | 'invalid-key'
  | 'quota-exhausted'
  | 'rate-limited'
  | 'unknown-model'
  | 'network-error'
  | 'timeout'
  | 'redirect-rejected'
  | 'malformed-stream'
  | 'provider-error'
  | 'cancelled'

export interface ProviderFailureInput {
  status?: number
  retryAfterMs?: number
  cause?: unknown
  timeout?: boolean
  redirectRejected?: boolean
  malformedStream?: boolean
  cancelled?: boolean
}

export interface ProviderErrorOptions {
  status?: number
  retryable: boolean
  retryAfterMs?: number
  body?: unknown
}

export class ProviderRequestError extends Error {
  readonly name = 'ProviderRequestError'
  readonly code: ProviderErrorCode
  readonly status?: number
  readonly retryable: boolean
  readonly retryAfterMs?: number

  constructor(code: ProviderErrorCode, message: string, options: ProviderErrorOptions) {
    super(message)
    this.code = code
    this.status = options.status
    this.retryable = options.retryable
    this.retryAfterMs = options.retryAfterMs
    // Response bodies may contain provider or credential material; intentionally never retain them.
    void options.body
  }

  toJSON() {
    return {
      code: this.code,
      status: this.status,
      retryable: this.retryable,
      ...(this.retryAfterMs === undefined ? {} : { retryAfterMs: this.retryAfterMs }),
    }
  }
}

export function classifyProviderFailure(input: ProviderFailureInput): ProviderRequestError {
  if (input.cancelled === true) return new ProviderRequestError('cancelled', '请求已取消', { retryable: false })
  if (input.redirectRejected === true) return new ProviderRequestError('redirect-rejected', 'Provider 重定向不受支持', { retryable: false })
  if (input.malformedStream === true) return new ProviderRequestError('malformed-stream', 'Provider 流式响应格式无效', { retryable: false })
  if (input.timeout === true) return new ProviderRequestError('timeout', 'Provider 请求超时', { retryable: true })
  if (input.status === 401 || input.status === 403) return new ProviderRequestError('invalid-key', 'Provider 凭证无效', { status: input.status, retryable: false })
  if (input.status === 402) return new ProviderRequestError('quota-exhausted', 'Provider 额度已用尽', { status: input.status, retryable: false })
  if (input.status === 404) return new ProviderRequestError('unknown-model', 'Provider 模型不可用', { status: input.status, retryable: false })
  if (input.status === 408 || input.status === 429) return new ProviderRequestError('rate-limited', 'Provider 请求频率受限', { status: input.status, retryable: true, retryAfterMs: boundedRetryAfter(input.retryAfterMs) })
  if (input.cause !== undefined) return new ProviderRequestError('network-error', '无法连接 Provider', { retryable: true })
  return new ProviderRequestError('provider-error', 'Provider 请求失败', { status: input.status, retryable: input.status === undefined || input.status >= 500 })
}

function boundedRetryAfter(value: number | undefined): number | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined
  return Math.max(0, Math.min(Math.floor(value), 60_000))
}
