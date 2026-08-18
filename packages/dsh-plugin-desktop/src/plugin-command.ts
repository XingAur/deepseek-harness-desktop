import { spawn, type ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { readFile } from 'node:fs/promises'
import { isAbsolute, join, normalize, sep } from 'node:path'
import { randomBytes } from 'node:crypto'
import type { CatalogPlugin } from './catalog'

export type PluginAction = 'install' | 'update' | 'remove'
export interface PluginOperationEvent { line: string; stream: 'stdout' | 'stderr' | 'system'; done: boolean; ok?: boolean }
export interface InstalledPlugin { packageName: string; version: string }

interface Operation {
  id: string
  child: ChildProcess
  events: PluginOperationEvent[]
  emitter: EventEmitter
}

export function pluginArguments(action: PluginAction, plugin: CatalogPlugin): string[] {
  const forwarded = action === 'install'
    ? ['add', plugin.installSpec]
    : action === 'update'
      ? ['update', plugin.packageName]
      : ['remove', plugin.packageName]
  return ['plugin', '--profile', 'desktop', ...forwarded]
}

export class PluginCommandService {
  private active: Operation | undefined
  private readonly completed = new Map<string, { events: PluginOperationEvent[]; expires: number }>()

  constructor(
    private readonly dshBin: string,
    private readonly profileDir: string,
    private readonly executable = process.execPath,
  ) {
    const expectedSuffix = normalize(join('node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js'))
    if (!isAbsolute(dshBin) || dshBin.includes('\0') || !normalize(dshBin).endsWith(`${sep}${expectedSuffix}`)) {
      throw new Error('DeepSeek Harness bin 必须是受管 Runtime 中的官方入口')
    }
  }

  start(action: PluginAction, plugin: CatalogPlugin): string {
    if (this.active !== undefined) throw new Error('另一个插件写操作正在运行')
    const id = `plugin_${randomBytes(18).toString('base64url')}`
    const child = spawn(this.executable, [this.dshBin, ...pluginArguments(action, plugin)], {
      cwd: this.profileDir,
      env: process.env,
      shell: false,
      windowsHide: true,
      detached: process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const operation: Operation = { id, child, events: [], emitter: new EventEmitter() }
    this.active = operation
    const push = (event: PluginOperationEvent) => { operation.events.push(event); operation.emitter.emit('event', event) }
    child.stdout?.on('data', (data) => push({ line: String(data).trimEnd(), stream: 'stdout', done: false }))
    child.stderr?.on('data', (data) => push({ line: String(data).trimEnd(), stream: 'stderr', done: false }))
    child.once('error', (cause) => this.finish(operation, { line: cause.message, stream: 'system', done: true, ok: false }))
    child.once('exit', (code, signal) => this.finish(operation, {
      line: code === 0 ? '插件操作完成' : `插件操作失败（${signal ?? `exit ${code ?? 1}`}）`,
      stream: 'system', done: true, ok: code === 0,
    }))
    return id
  }

  subscribe(id: string, listener: (event: PluginOperationEvent) => void): () => void {
    const active = this.active?.id === id ? this.active : undefined
    const completed = this.completed.get(id)
    for (const event of active?.events ?? completed?.events ?? []) listener(event)
    if (active === undefined) return () => undefined
    active.emitter.on('event', listener)
    return () => active.emitter.off('event', listener)
  }

  async cancel(id: string): Promise<void> {
    const operation = this.active
    if (operation?.id !== id || operation.child.pid === undefined) throw new Error('插件操作不存在或已经结束')
    if (process.platform === 'win32') {
      await new Promise<void>((done) => spawn('taskkill', ['/PID', String(operation.child.pid), '/T', '/F'], { windowsHide: true }).once('exit', () => done()))
    } else {
      try { process.kill(-operation.child.pid, 'SIGTERM') } catch { operation.child.kill('SIGTERM') }
    }
  }

  async installed(): Promise<InstalledPlugin[]> {
    try {
      const manifest = JSON.parse(await readFile(join(this.profileDir, 'package.json'), 'utf8')) as { dependencies?: Record<string, string> }
      return Promise.all(Object.entries(manifest.dependencies ?? {}).map(async ([packageName, declared]) => {
        try {
          const installed = JSON.parse(await readFile(join(this.profileDir, 'node_modules', packageName, 'package.json'), 'utf8')) as { version?: unknown }
          return { packageName, version: typeof installed.version === 'string' ? installed.version : declared }
        } catch { return { packageName, version: declared } }
      }))
    } catch { return [] }
  }

  private finish(operation: Operation, finalEvent: PluginOperationEvent) {
    if (this.active?.id !== operation.id) return
    operation.events.push(finalEvent)
    operation.emitter.emit('event', finalEvent)
    this.completed.set(operation.id, { events: operation.events, expires: Date.now() + 5 * 60_000 })
    this.active = undefined
    for (const [id, record] of this.completed) if (record.expires < Date.now()) this.completed.delete(id)
  }
}
