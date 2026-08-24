import { describe, expect, it } from 'vitest'
import { classifyProviderFailure, ProviderRequestError } from './error-classification.js'

describe('provider error classification', () => {
  it('classifies authentication, quota, rate limit, model, and network failures without retaining bodies', () => {
    expect(classifyProviderFailure({ status: 401 })).toMatchObject({ code: 'invalid-key', retryable: false })
    expect(classifyProviderFailure({ status: 429, retryAfterMs: 1200 })).toMatchObject({ code: 'rate-limited', retryable: true, retryAfterMs: 1200 })
    expect(classifyProviderFailure({ status: 402 })).toMatchObject({ code: 'quota-exhausted', retryable: false })
    expect(classifyProviderFailure({ status: 404 })).toMatchObject({ code: 'unknown-model', retryable: false })
    expect(classifyProviderFailure({ cause: new TypeError('network failed') })).toMatchObject({ code: 'network-error', retryable: true })
  })

  it('keeps provider errors bounded and excludes arbitrary response bodies', () => {
    const error = new ProviderRequestError('provider-error', '服务端错误', { status: 500, retryable: true, body: 'sk-secret-value' })
    expect(error.code).toBe('provider-error')
    expect(error.message).toBe('服务端错误')
    expect(JSON.stringify(error)).not.toContain('sk-secret-value')
  })
})
