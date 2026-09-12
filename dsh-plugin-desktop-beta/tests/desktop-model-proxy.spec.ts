import { describe, expect, it } from 'vitest'
import {
  desktopModelProxyHosts,
  hostnameUsesModelProxy,
  parseDesktopModelProxyProviders,
} from '../src/desktop-model-proxy.ts'

describe('desktop model proxy allowlist', () => {
  it('defaults to xAI and Codex', () => {
    expect(parseDesktopModelProxyProviders(undefined)).toEqual(['xai', 'openai-codex'])
  })

  it('rejects unknown provider ids', () => {
    expect(() => parseDesktopModelProxyProviders(['zai'])).toThrow('unknown provider')
  })

  it('matches xAI API hosts and leaves domestic hosts direct', () => {
    const hosts = desktopModelProxyHosts(['xai'])
    expect(hostnameUsesModelProxy('api.x.ai', hosts)).toBe(true)
    expect(hostnameUsesModelProxy('us-east-1.api.x.ai', hosts)).toBe(true)
    expect(hostnameUsesModelProxy('open.bigmodel.cn', hosts)).toBe(false)
    expect(hostnameUsesModelProxy('devops.aliyun.com', hosts)).toBe(false)
  })

  it('matches OpenAI and ChatGPT hosts for Codex', () => {
    const hosts = desktopModelProxyHosts(['openai-codex'])
    expect(hostnameUsesModelProxy('api.openai.com', hosts)).toBe(true)
    expect(hostnameUsesModelProxy('chatgpt.com', hosts)).toBe(true)
    expect(hostnameUsesModelProxy('api.x.ai', hosts)).toBe(false)
  })
})
