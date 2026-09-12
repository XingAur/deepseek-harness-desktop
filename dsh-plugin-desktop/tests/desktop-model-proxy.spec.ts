import { describe, expect, it } from 'vitest'
import {
  desktopModelProxyHosts,
  hostnameUsesModelProxy,
  normalizeDesktopModelProxyHost,
  parseDesktopModelProxyCustomProviders,
  parseDesktopModelProxyExtraHosts,
  parseDesktopModelProxyProviders,
  resolveDesktopModelProxyUrl,
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
    expect(hostnameUsesModelProxy('api.z.ai', hosts)).toBe(false)
    expect(hostnameUsesModelProxy('devops.aliyun.com', hosts)).toBe(false)
  })

  it('matches OpenAI and ChatGPT hosts for Codex', () => {
    const hosts = desktopModelProxyHosts(['openai-codex'])
    expect(hostnameUsesModelProxy('api.openai.com', hosts)).toBe(true)
    expect(hostnameUsesModelProxy('chatgpt.com', hosts)).toBe(true)
    expect(hostnameUsesModelProxy('api.x.ai', hosts)).toBe(false)
  })

  it('accepts extra overseas hosts and still leaves domestic APIs direct', () => {
    expect(normalizeDesktopModelProxyHost('https://api.anthropic.com/v1')).toBe('api.anthropic.com')
    expect(normalizeDesktopModelProxyHost('com')).toBeUndefined()
    expect(normalizeDesktopModelProxyHost('*.openai.com')).toBeUndefined()
    expect(parseDesktopModelProxyExtraHosts(['api.anthropic.com', 'https://generativelanguage.googleapis.com']))
      .toEqual(['api.anthropic.com', 'generativelanguage.googleapis.com'])
    const hosts = desktopModelProxyHosts(['xai'], ['api.anthropic.com'])
    expect(hostnameUsesModelProxy('api.anthropic.com', hosts)).toBe(true)
    expect(hostnameUsesModelProxy('open.bigmodel.cn', hosts)).toBe(false)
  })

  it('parses user-added models and includes their hosts', () => {
    expect(parseDesktopModelProxyCustomProviders([
      { name: 'Anthropic', hosts: ['api.anthropic.com'], proxyUrl: '' },
    ])).toEqual([{ name: 'Anthropic', hosts: ['api.anthropic.com'], proxyUrl: '' }])
    const hosts = desktopModelProxyHosts(['xai'], [], [
      { name: 'Anthropic', hosts: ['api.anthropic.com'], proxyUrl: '' },
    ])
    expect(hostnameUsesModelProxy('api.anthropic.com', hosts)).toBe(true)
    expect(hostnameUsesModelProxy('open.bigmodel.cn', hosts)).toBe(false)
  })

  it('uses a dedicated proxy for one vendor and keeps others on the shared URL', () => {
    const input = {
      sharedUrl: 'http://127.0.0.1:7890',
      providers: ['xai', 'anthropic'],
      providerUrls: { anthropic: 'http://user:pass@geo.example:8001' },
    }
    expect(resolveDesktopModelProxyUrl('api.x.ai', input)).toBe('http://127.0.0.1:7890')
    expect(resolveDesktopModelProxyUrl('api.anthropic.com', input)).toBe('http://user:pass@geo.example:8001')
    expect(resolveDesktopModelProxyUrl('open.bigmodel.cn', input)).toBe('')
  })
})
