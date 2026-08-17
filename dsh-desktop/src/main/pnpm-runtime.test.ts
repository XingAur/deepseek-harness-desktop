import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { buildChildEnv, ensurePnpmShim } from './pnpm-runtime.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'pnpm-')); dirs.push(d); return d }

describe('buildChildEnv', () => {
  it('注入 DSH_HOME、PATH 前缀、默认 npmmirror、ELECTRON_RUN_AS_NODE', () => {
    const env = buildChildEnv({ binDir: 'B', dshHome: 'H' })
    expect(env.DSH_HOME).toBe('H')
    expect(env.PATH!.startsWith('B' + ';')).toBe(true)
    expect(env.npm_config_registry).toBe('https://registry.npmmirror.com')
    expect(env.ELECTRON_RUN_AS_NODE).toBe('1')
  })
  it('已有 npm_config_registry 时不覆盖', () => {
    const prev = process.env.npm_config_registry
    process.env.npm_config_registry = 'https://example.com'
    const env = buildChildEnv({ binDir: 'B', dshHome: 'H' })
    expect(env.npm_config_registry).toBe('https://example.com')
    if (prev === undefined) delete process.env.npm_config_registry; else process.env.npm_config_registry = prev
  })
})

describe('ensurePnpmShim', () => {
  it('生成 pnpm.cmd 且内容包含 execPath 与 pnpm 入口', () => {
    const dir = tmp()
    ensurePnpmShim(join(dir, 'bin'), 'C:\\app\\dsh.exe', 'C:\\app\\resources\\runtime-pnpm\\node_modules\\pnpm\\bin\\pnpm.mjs')
    const f = join(dir, 'bin', 'pnpm.cmd')
    expect(existsSync(f)).toBe(true)
    const c = readFileSync(f, 'utf8')
    expect(c).toContain('C:\\app\\dsh.exe')
    expect(c).toContain('pnpm.mjs')
    expect(c).toContain('ELECTRON_RUN_AS_NODE')
  })
})
