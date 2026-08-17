import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { resolveDsh } from './dsh-resolver.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'res-')); dirs.push(d); return d }

function fakeInstall(root: string, version: string): void {
  const pkgDir = join(root, 'node_modules', '@deepseek-ai', 'dsh')
  mkdirSync(join(pkgDir, 'lib'), { recursive: true })
  writeFileSync(join(pkgDir, 'lib', 'bin.js'), '')
  writeFileSync(join(pkgDir, 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh', version }))
}

describe('resolveDsh', () => {
  it('无用户区版本 → 内置版', () => {
    const bundled = tmp(); fakeInstall(bundled, '0.1.0-rc.6')
    const r = resolveDsh(join(tmp(), 'runtime', 'dsh'), bundled)
    expect(r.source).toBe('bundled')
    expect(r.version).toBe('0.1.0-rc.6')
    expect(r.binPath).toBe(join(bundled, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js'))
  })
  it('用户区多版本取最大', () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    for (const v of ['0.1.0-rc.6', '0.2.0']) { const d = join(runtime, v); fakeInstall(d, v); }
    const bundled = tmp(); fakeInstall(bundled, '0.1.0-rc.6')
    const r = resolveDsh(runtime, bundled)
    expect(r.source).toBe('user')
    expect(r.version).toBe('0.2.0')
  })
  it('用户区目录缺 bin.js 视为无效并回退', () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    mkdirSync(join(runtime, '0.9.0'), { recursive: true })
    const bundled = tmp(); fakeInstall(bundled, '0.1.0-rc.6')
    expect(resolveDsh(runtime, bundled).source).toBe('bundled')
  })
})
