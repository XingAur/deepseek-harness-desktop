import http from 'node:http'
import net from 'node:net'

export type ProbeResult = 'dsh' | 'foreign' | 'none'

export async function probe(port: number, timeoutMs = 2000): Promise<ProbeResult> {
  if (!(await tcpOk(port, timeoutMs))) return 'none'
  return (await httpLooksLikeDsh(port, timeoutMs)) ? 'dsh' : 'foreign'
}

function tcpOk(port: number, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    const s = net.connect({ host: '127.0.0.1', port })
    s.setTimeout(timeoutMs)
    s.once('connect', () => { s.destroy(); resolve(true) })
    s.once('timeout', () => { s.destroy(); resolve(false) })
    s.once('error', () => resolve(false))
  })
}

function httpLooksLikeDsh(port: number, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    let done = false
    const finish = (ok: boolean) => {
      if (done) return
      done = true
      clearTimeout(timer)
      req.destroy()
      resolve(ok)
    }
    const req = http.get({ host: '127.0.0.1', port, path: '/' }, res => {
      const ok = res.statusCode === 200 && String(res.headers['content-type'] ?? '').includes('text/html')
      res.resume()
      res.once('end', () => finish(ok))
      finish(ok)
    })
    const timer = setTimeout(() => finish(false), timeoutMs)
    req.once('error', () => finish(false))
  })
}
