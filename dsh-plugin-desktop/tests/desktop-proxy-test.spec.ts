import { afterEach, describe, expect, it } from 'vitest'
import {
  classifyDesktopProxyTestError,
  testDesktopModelProxy,
} from '../src/desktop-proxy-test.ts'

const envKeys = ['HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'] as const
const previousEnv: Partial<Record<(typeof envKeys)[number], string | undefined>> = {}

afterEach(() => {
  for (const key of envKeys) {
    if (previousEnv[key] === undefined) delete process.env[key]
    else process.env[key] = previousEnv[key]
    delete previousEnv[key]
  }
})

function clearProxyEnv(): void {
  for (const key of envKeys) {
    previousEnv[key] = process.env[key]
    delete process.env[key]
  }
}

describe('desktop model proxy test', () => {
  it('classifies refused, timeout, TLS, and auth failures', () => {
    expect(classifyDesktopProxyTestError({ code: 'ECONNREFUSED' })).toBe('proxy-refused')
    expect(classifyDesktopProxyTestError({ code: 'UND_ERR_CONNECT_TIMEOUT' })).toBe('timeout')
    expect(classifyDesktopProxyTestError({ message: 'certificate has expired' })).toBe('tls')
    expect(classifyDesktopProxyTestError({ message: 'Proxy Authentication Required 407' })).toBe('proxy-auth')
    expect(classifyDesktopProxyTestError({ code: 'ENOTFOUND' })).toBe('dns')
  })

  it('rejects an unusable proxy URL without contacting the network', async () => {
    await expect(testDesktopModelProxy('ftp://proxy.example')).resolves.toEqual({
      ok: false,
      code: 'invalid-url',
    })
  })

  it('reports no-proxy when the draft and environment are empty', async () => {
    clearProxyEnv()
    await expect(testDesktopModelProxy('')).resolves.toEqual({
      ok: false,
      code: 'no-proxy',
    })
  })
})
