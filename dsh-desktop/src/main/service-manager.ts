import type { ChildProcess } from 'node:child_process'
import type { Project } from './projects.js'
import type { ProbeResult } from './port-probe.js'

export type SessionState = 'idle' | 'checking' | 'attached' | 'starting' | 'ready' | 'error-port' | 'error-crashed'

export interface ServiceDeps {
  sessionPort: number
  probe: (port: number) => Promise<ProbeResult>
  spawnChild: (cmd: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) => ChildProcess
  treeKill: (pid: number) => void
  staticServe: (dir: string) => Promise<number>
  log: (line: string) => void
  onState: (state: SessionState) => void
  startTimeoutMs: number
  pollIntervalMs: number
}

interface ChildEntry { child: ChildProcess; kind: 'dsh' | 'project' }

export class ServiceManager {
  private children = new Map<string, ChildEntry>()
  private staticPorts = new Map<string, number>()

  constructor(private deps: ServiceDeps) {}

  async ensureSession(binPath: string, env: NodeJS.ProcessEnv): Promise<{ mode: 'attached' | 'started'; url: string }> {
    const { sessionPort: port, probe } = this.deps
    const url = `http://127.0.0.1:${port}/`
    if (this.children.has('session')) return { mode: 'started', url }
    this.deps.onState('checking')
    const r = await probe(port)
    if (r === 'dsh') { this.deps.onState('attached'); return { mode: 'attached', url } }
    if (r === 'foreign') { this.deps.onState('error-port'); throw new Error('PORT_CONFLICT') }
    this.deps.onState('starting')
    const child = this.deps.spawnChild(process.execPath, [binPath, 'web'], process.cwd(), env)
    this.track('session', { child, kind: 'dsh' })
    await this.waitReady(port)
    this.deps.onState('ready')
    return { mode: 'started', url }
  }

  async ensureProject(project: Project, env: NodeJS.ProcessEnv): Promise<string> {
    const key = `project:${project.name}`
    if (this.staticPorts.has(key)) return this.projectUrl(key, project.entry)
    const entry = this.children.get(key)
    if (entry) return this.projectUrl(key, project.entry)
    if (project.start && project.port) {
      const child = this.deps.spawnChild(project.start, [], project.dir, env)
      this.track(key, { child, kind: 'project' })
      await this.waitPort(project.port)
      this.staticPorts.set(key, project.port)
      return this.projectUrl(key, project.entry)
    }
    const port = await this.deps.staticServe(project.dir)
    this.staticPorts.set(key, port)
    return this.projectUrl(key, project.entry)
  }

  private projectUrl(key: string, entry: string): string {
    return `http://127.0.0.1:${this.staticPorts.get(key)}/${entry.replace(/^\/+/, '')}`
  }

  private track(key: string, entry: ChildEntry): void {
    this.children.set(key, entry)
    entry.child.once('exit', (code) => {
      if (this.children.get(key)?.child === entry.child) {
        this.children.delete(key)
        this.staticPorts.delete(key)
        this.deps.log(`child exit key=${key} code=${code}`)
        if (key === 'session') this.deps.onState('error-crashed')
      }
    })
  }

  private async waitReady(port: number): Promise<void> {
    await this.waitPort(port, (r) => r === 'dsh')
  }

  private async waitPort(port: number, accept: (r: ProbeResult) => boolean = (r) => r !== 'none'): Promise<void> {
    const deadline = Date.now() + this.deps.startTimeoutMs
    while (Date.now() < deadline) {
      if (accept(await this.deps.probe(port))) return
      await new Promise(r => setTimeout(r, this.deps.pollIntervalMs))
    }
    throw new Error('START_TIMEOUT')
  }

  async shutdownAll(): Promise<void> {
    for (const [key, entry] of this.children) {
      try { if (entry.child.pid) this.deps.treeKill(entry.child.pid) } catch (e) { this.deps.log(`kill fail ${key}: ${e}`) }
      this.children.delete(key)
    }
    this.staticPorts.clear()
  }
}
