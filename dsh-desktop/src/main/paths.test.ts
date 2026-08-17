import { mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { appPaths, loadSettings, resolveDataRoot, saveSettings } from './paths.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'paths-')); dirs.push(d); return d }

describe('resolveDataRoot', () => {
  it('显式 override 时直接使用并创建', () => {
    const root = join(tmp(), 'DataRoot')
    expect(resolveDataRoot(root)).toBe(root)
  })
  it('无 D 盘可写时回退 C 盘（注入探测）', () => {
    expect(resolveDataRoot(undefined, () => false)).toBe('C:\\DeepSeekHarness')
  })
})

describe('appPaths', () => {
  it('给出全部子路径', () => {
    const p = appPaths('R')
    expect(p.projectsDir).toBe(join('R', 'projects'))
    expect(p.dshHome).toBe(join('R', 'dsh-home'))
    expect(p.runtimeDshDir).toBe(join('R', 'runtime', 'dsh'))
    expect(p.binDir).toBe(join('R', 'bin'))
    expect(p.logsDir).toBe(join('R', 'logs'))
    expect(p.settingsFile).toBe(join('R', 'settings.json'))
  })
})

describe('settings', () => {
  it('roundtrip', () => {
    const f = join(tmp(), 'settings.json')
    saveSettings(f, { dataRoot: 'R', dshVersion: '1.0.0' })
    expect(loadSettings(f)).toEqual({ dataRoot: 'R', dshVersion: '1.0.0' })
  })
  it('文件不存在返回空对象', () => {
    expect(loadSettings(join(tmp(), 'nope.json'))).toEqual({ dataRoot: '' })
  })
})
