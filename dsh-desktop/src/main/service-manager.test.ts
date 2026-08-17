import { EventEmitter } from 'node:events'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ServiceManager } from './service-manager.js'
import type { Project } from './projects.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'sm-')); dirs.push(d); return d }

function fakeChild(): any {
  const c = new EventEmitter() as any
  c.pid = 4242
  return c
}

function makeManager(probeResult: () => Promise<string>) {
  const states: string[] = []
  const kill = vi.fn()
  const child = fakeChild()
  const mgr = new ServiceManager({
    sessionPort: 39999,
    probe: probeResult as any,
    spawnChild: () => child,
    treeKill: (pid) => kill(pid),
    staticServe: async () => 45678,
    log: () => {},
    onState: (s) => states.push(s),
    startTimeoutMs: 3000,
    pollIntervalMs: 50,
  })
  return { mgr, states, child, kill }
}

describe('ensureSession', () => {
  it('探测到 dsh → attached，不 spawn', async () => {
    const { mgr, states } = makeManager(async () => 'dsh')
    const r = await mgr.ensureSession('bin.js', {})
    expect(r.mode).toBe('attached')
    expect(states).toContain('attached')
  })
  it('none → spawn 并等待就绪 → started', async () => {
    let call = 0
    const { mgr, states } = makeManager(async () => (call++ === 0 ? 'none' : 'dsh'))
    const r = await mgr.ensureSession('bin.js', {})
    expect(r.mode).toBe('started')
    expect(states).toContain('ready')
  })
  it('foreign → PORT_CONFLICT', async () => {
    const { mgr } = makeManager(async () => 'foreign')
    await expect(mgr.ensureSession('bin.js', {})).rejects.toThrow('PORT_CONFLICT')
  })
  it('子进程退出 → error-crashed 状态', async () => {
    let call = 0
    const { mgr, child, states } = makeManager(async () => (call++ === 0 ? 'none' : 'dsh'))
    await mgr.ensureSession('bin.js', {})
    child.emit('exit')
    expect(states).toContain('error-crashed')
  })
})

describe('ensureProject', () => {
  it('静态项目 → 静态服务端口 URL', async () => {
    const { mgr } = makeManager(async () => 'none')
    const p: Project = { dir: tmp(), name: 'S', icon: 'a', desc: '', entry: 'index.html' }
    writeFileSync(join(p.dir, 'index.html'), 'x')
    const url = await mgr.ensureProject(p, {})
    expect(url).toBe('http://127.0.0.1:45678/index.html')
  })
  it('start 项目 → spawn 并等端口就绪', async () => {
    let call = 0
    const { mgr } = makeManager(async () => (call++ === 0 ? 'none' : 'dsh'))
    const p: Project = { dir: tmp(), name: 'T', icon: 'a', desc: '', entry: 'index.html', start: 'node s.js', port: 8899 }
    const url = await mgr.ensureProject(p, {})
    expect(url).toBe('http://127.0.0.1:8899/index.html')
  })
  it('重复进入静态项目幂等（同端口）', async () => {
    const { mgr } = makeManager(async () => 'none')
    const p: Project = { dir: tmp(), name: 'S2', icon: 'a', desc: '', entry: 'index.html' }
    const a = await mgr.ensureProject(p, {})
    const b = await mgr.ensureProject(p, {})
    expect(a).toBe(b)
  })
})

describe('shutdownAll', () => {
  it('杀掉自己启动的子进程', async () => {
    let call = 0
    const { mgr, kill } = makeManager(async () => (call++ === 0 ? 'none' : 'dsh'))
    await mgr.ensureSession('bin.js', {})
    await mgr.shutdownAll()
    expect(kill).toHaveBeenCalledWith(4242)
  })
})
