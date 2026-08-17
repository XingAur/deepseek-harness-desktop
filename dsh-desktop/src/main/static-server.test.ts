import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { startStaticServer } from './static-server.js'

const dirs: string[] = []
const servers: Array<{ close(): Promise<void> }> = []
afterEach(async () => {
  for (const s of servers) await s.close()
  servers.length = 0
  for (const d of dirs) rmSync(d, { recursive: true, force: true })
  dirs.length = 0
})
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'static-')); dirs.push(d); return d }

describe('startStaticServer', () => {
  it('服务根目录并返回随机端口', async () => {
    const dir = tmp()
    writeFileSync(join(dir, 'index.html'), '<h1>hi</h1>')
    const s = await startStaticServer(dir)
    servers.push(s)
    expect(s.port).toBeGreaterThan(0)
    const res = await fetch(`http://127.0.0.1:${s.port}/`)
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toContain('text/html')
    expect(await res.text()).toContain('hi')
  })
  it('.. 路径穿越被拒绝', async () => {
    const dir = tmp()
    writeFileSync(join(dir, 'index.html'), 'x')
    const s = await startStaticServer(dir)
    servers.push(s)
    const res = await fetch(`http://127.0.0.1:${s.port}/..%5c..%5cwindows%5cwin.ini`)
    expect([403, 400]).toContain(res.status)
  })
})
