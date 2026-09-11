/** Unit coverage for the remote-relay surface: origin rules, header hygiene, wire form. */

import { describe, expect, it } from 'vitest'
import { canonicalRelayOrigin } from '../src/remote-relay-origin.ts'
import { rawTunnelRequest, sanitizedTunnelHeaders } from '../src/remote-relay-tunnel.ts'
import { desktopStartupSettingsFromSettings } from '../src/profile.ts'

describe('canonicalRelayOrigin', () => {
  it('accepts public https origins and loopback http origins', () => {
    expect(canonicalRelayOrigin('https://relay.example.com')?.origin).toBe('https://relay.example.com')
    expect(canonicalRelayOrigin('https://relay.example.com:8443/')?.host).toBe('relay.example.com:8443')
    expect(canonicalRelayOrigin('http://127.0.0.1:8787/')?.origin).toBe('http://127.0.0.1:8787')
    expect(canonicalRelayOrigin('http://localhost:8787')?.host).toBe('localhost:8787')
  })

  it('rejects paths, queries, credentials, and off-box http', () => {
    expect(canonicalRelayOrigin('https://relay.example.com/path')).toBeNull()
    expect(canonicalRelayOrigin('https://relay.example.com/?x=1')).toBeNull()
    expect(canonicalRelayOrigin('https://user:pass@relay.example.com')).toBeNull()
    expect(canonicalRelayOrigin('http://192.168.1.5:8787')).toBeNull()
    expect(canonicalRelayOrigin('ws://relay.example.com')).toBeNull()
    expect(canonicalRelayOrigin('not a url')).toBeNull()
  })

  it('treats the empty string as the disabled state', () => {
    expect(canonicalRelayOrigin('')).toBeNull()
  })
})

describe('sanitizedTunnelHeaders', () => {
  it('strips the renderer capability header and the forwarded family', () => {
    const result = sanitizedTunnelHeaders([
      ['host', 'relay.example.com'],
      ['x-dsh-desktop-renderer', 'secret'],
      ['X-DSH-DESKTOP-RENDERER', 'secret'],
      ['forwarded', 'for=1.2.3.4'],
      ['x-forwarded-for', '1.2.3.4'],
      ['cookie', 'dsh-auth-x=v1.abc'],
    ])
    expect(result).toEqual([['host', 'relay.example.com'], ['cookie', 'dsh-auth-x=v1.abc']])
  })

  it('strips hop-by-hop headers on ordinary HTTP forwards and flattens array values', () => {
    const result = sanitizedTunnelHeaders([
      ['host', 'relay.example.com'],
      ['connection', 'keep-alive'],
      ['transfer-encoding', 'chunked'],
      ['cookie', ['a=1', 'b=2']],
    ], { hopByHop: true })
    expect(result).toEqual([['host', 'relay.example.com'], ['cookie', 'a=1, b=2']])
  })
})

describe('rawTunnelRequest', () => {
  it('rebuilds the exact HTTP/1.1 upgrade wire form', () => {
    const raw = rawTunnelRequest('GET', '/api/remote.mux', [['host', 'x'], ['upgrade', 'websocket']])
    expect(raw).toBe('GET /api/remote.mux HTTP/1.1\r\nhost: x\r\nupgrade: websocket\r\n\r\n')
  })
})

describe('desktopStartupSettingsFromSettings relay origin', () => {
  it('defaults to disabled and round-trips a canonical https origin', () => {
    expect(desktopStartupSettingsFromSettings({}).remoteRelayOrigin).toBe('')
    expect(desktopStartupSettingsFromSettings({
      'dsh-desktop': { remoteRelayOrigin: 'https://relay.example.com/' },
    }).remoteRelayOrigin).toBe('https://relay.example.com')
  })

  it('fails loud on malformed values', () => {
    expect(() => desktopStartupSettingsFromSettings({
      'dsh-desktop': { remoteRelayOrigin: 'ftp://relay.example.com' },
    })).toThrow(/remoteRelayOrigin/u)
    expect(() => desktopStartupSettingsFromSettings({
      'dsh-desktop': { remoteRelayOrigin: 'http://192.168.1.5:8787' },
    })).toThrow(/remoteRelayOrigin/u)
    expect(() => desktopStartupSettingsFromSettings({
      'dsh-desktop': { remoteRelayOrigin: 7 },
    })).toThrow(/remoteRelayOrigin/u)
  })
})
