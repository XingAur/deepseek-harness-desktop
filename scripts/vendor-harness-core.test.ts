import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'
import {
  HARNESS_CORE_VENDOR_DIRS,
  VENDOR_MANIFEST_NAME,
  copyHarnessCore,
  isSecretAssignment,
  isVendorablePath,
  resolveHarnessCoreSource,
  syncVendorFromSource,
  verifyVendorNoSecrets,
} from './vendor-harness-core.mjs'

const directories: string[] = []
function temporary() {
  const directory = mkdtempSync(join(tmpdir(), 'vendor-core-'))
  directories.push(directory)
  return directory
}

afterAll(() => {
  for (const directory of directories) rmSync(directory, { recursive: true, force: true })
})

describe('harness core vendoring', () => {
  it('keeps code and config but drops caches, virtual environments and runtime data', () => {
    const source = temporary()
    for (const name of ['app', 'tools']) {
      mkdirSync(join(source, name), { recursive: true })
      writeFileSync(join(source, name, 'core.py'), 'print("ok")\n')
    }
    mkdirSync(join(source, 'app', '__pycache__'), { recursive: true })
    writeFileSync(join(source, 'app', '__pycache__', 'core.pyc'), 'binary')
    mkdirSync(join(source, '.venv'), { recursive: true })
    writeFileSync(join(source, '.venv', 'bin'), 'not-a-dir')
    mkdirSync(join(source, 'data'), { recursive: true })
    writeFileSync(join(source, 'data', 'big.db'), 'x')
    mkdirSync(join(source, 'unrelated'), { recursive: true })
    writeFileSync(join(source, 'requirements.txt'), 'cryptography>=42\n')

    const target = temporary()
    const copied = copyHarnessCore(source, target)

    expect(copied.fileCount).toBe(3)
    expect(isVendorablePath('app/core.py')).toBe(true)
    expect(isVendorablePath('app/__pycache__/core.pyc')).toBe(false)
    expect(isVendorablePath('.venv/bin/python')).toBe(false)
    expect(isVendorablePath('data/big.db')).toBe(false)
    expect(HARNESS_CORE_VENDOR_DIRS).toContain('app')
  })

  it('preserves installed runtime directories across re-copies', () => {
    const source = temporary()
    mkdirSync(join(source, 'app'), { recursive: true })
    writeFileSync(join(source, 'app', 'core.py'), 'print("v1")\n')
    const target = temporary()
    copyHarnessCore(source, target)
    const runtime = join(target, 'runtime')
    mkdirSync(join(runtime, 'bin'), { recursive: true })
    writeFileSync(join(runtime, 'bin', 'python3'), 'kept')

    writeFileSync(join(source, 'app', 'core.py'), 'print("v2")\n')
    copyHarnessCore(source, target, { preserve: ['runtime'] })

    expect(readFileSync(join(target, 'app', 'core.py'), 'utf8')).toContain('v2')
    expect(existsSync(join(runtime, 'bin', 'python3'))).toBe(true)
  })

  it('refuses real credential assignments in data files but allows documented placeholders', () => {
    const target = temporary()
    mkdirSync(join(target, 'config'), { recursive: true })
    writeFileSync(
      join(target, 'config', 'template.json'),
      '{"api_key": "fill-model-api-key-ref"}\n',
    )
    expect(verifyVendorNoSecrets(target)).toEqual([])

    writeFileSync(
      join(target, 'config', 'leaked.json'),
      '{"password": "hunter2hunter2hunter2"}\n',
    )
    expect(() => verifyVendorNoSecrets(target)).toThrow(/凭证样式/)
    expect(isSecretAssignment('{"password": "hunter2hunter2hunter2"}')).toBe(true)
    expect(isSecretAssignment('{"api_key": "fill-model-api-key-ref"}')).toBe(false)
    expect(isSecretAssignment('{"note": "不要在配置里写 token"}')).toBe(false)
  })

  it('auto-syncs vendor from a local Harness source without any manual command', () => {
    const repositoryRoot = temporary()
    const source = temporary()
    for (const name of ['app', 'tools']) mkdirSync(join(source, name), { recursive: true })
    writeFileSync(join(source, 'app', 'core.py'), 'print("v1")\n')
    mkdirSync(join(source, 'tools'), { recursive: true })
    writeFileSync(join(source, 'tools', 'harness_host_server.py'), 'entry = 1\n')
    writeFileSync(join(source, 'requirements.txt'), 'cryptography>=42\n')

    const first = syncVendorFromSource(repositoryRoot, { source })
    expect(first.synced).toBe(true)
    expect(first.changed).toBe(true)
    const vendor = join(repositoryRoot, 'vendor', 'harness-core')
    expect(readFileSync(join(vendor, 'app', 'core.py'), 'utf8')).toContain('v1')

    // 源码未变：再次同步报告一致；源码变了：自动带入。
    const second = syncVendorFromSource(repositoryRoot, { source })
    expect(second.synced).toBe(true)
    expect(second.changed).toBe(false)
    writeFileSync(join(source, 'app', 'core.py'), 'print("v2")\n')
    const third = syncVendorFromSource(repositoryRoot, { source })
    expect(third.synced).toBe(true)
    expect(third.changed).toBe(true)
    expect(readFileSync(join(vendor, 'app', 'core.py'), 'utf8')).toContain('v2')
    // 记录的源路径成为后续解析的默认来源（无需再次显式传参）
    expect(resolveHarnessCoreSource(repositoryRoot)).toBe(source)
  })

  it('skips auto-sync in CI, without a source, or when the source is the vendor itself', () => {
    const repositoryRoot = temporary()
    const ciFlag = process.env.CI
    process.env.CI = 'true'
    try {
      expect(syncVendorFromSource(repositoryRoot).synced).toBe(false)
    } finally {
      if (ciFlag === undefined) delete process.env.CI
      else process.env.CI = ciFlag
    }
    expect(syncVendorFromSource(repositoryRoot, { source: '/definitely-not-a-harness-source' }).reason).toBe('source-unavailable')
    const vendor = join(repositoryRoot, 'vendor', 'harness-core')
    mkdirSync(join(vendor, 'app'), { recursive: true })
    mkdirSync(join(vendor, 'tools'), { recursive: true })
    writeFileSync(join(vendor, 'tools', 'harness_host_server.py'), 'x = 1\n')
    writeFileSync(join(vendor, 'requirements.txt'), '')
    expect(syncVendorFromSource(repositoryRoot, { source: vendor }).reason).toBe('source-is-vendor')
  })
})
