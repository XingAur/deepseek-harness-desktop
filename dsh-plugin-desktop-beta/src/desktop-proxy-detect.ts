/** Probe loopback HTTP proxies used by common local tunnel apps. */

import { createConnection } from 'node:net'

/** Clash, V2RayN, Surge, and similar HTTP mixed ports, Clash first. */
export const DESKTOP_LOCAL_HTTP_PROXY_PORTS: readonly number[] = Object.freeze([
  7890, 7897, 7891, 10809, 10808, 1087, 6152, 20171,
])

const PROBE_TIMEOUT_MS = 400
const CONNECT_REQUEST = 'CONNECT example.invalid:443 HTTP/1.1\r\nHost: example.invalid:443\r\n\r\n'

/**
 * Probe one loopback TCP port for an HTTP CONNECT proxy.
 * @param port - loopback port.
 * @returns true when the peer answers CONNECT with 200 or 407.
 */
export function probeLoopbackHttpProxy(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false
    const finish = (value: boolean): void => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      socket.destroy()
      resolve(value)
    }
    const socket = createConnection({ host: '127.0.0.1', port })
    const timer = setTimeout(() => { finish(false) }, PROBE_TIMEOUT_MS)
    socket.setNoDelay(true)
    socket.once('error', () => { finish(false) })
    socket.once('connect', () => {
      socket.write(CONNECT_REQUEST)
    })
    let buf = ''
    socket.on('data', (chunk: Buffer) => {
      buf += chunk.toString('latin1')
      const headerEnd = buf.indexOf('\r\n')
      if (headerEnd < 0) return
      finish(/^HTTP\/1\.[01] (?:200|407)\b/u.test(buf.slice(0, headerEnd + 2)))
    })
    socket.once('end', () => { finish(false) })
  })
}

/**
 * Find the first loopback HTTP proxy among the well-known tunnel ports.
 * @param ports - ports to probe in priority order.
 * @returns `http://127.0.0.1:<port>` or null when none answer CONNECT.
 */
export async function detectLocalOutboundProxy(
  ports: readonly number[] = DESKTOP_LOCAL_HTTP_PROXY_PORTS,
): Promise<string | null> {
  const results = await Promise.all(ports.map(async (port) => ({
    port,
    ok: await probeLoopbackHttpProxy(port),
  })))
  const hit = results.find(result => result.ok)
  return hit === undefined ? null : `http://127.0.0.1:${String(hit.port)}`
}
