import { EventEmitter } from 'node:events'
import { resolve } from 'node:path'
import { DEFAULT_E2E_ROOT_DIRECTORY } from './default-e2e-paths.mjs'
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { initializeDefaultE2ERoot } from './owned-e2e-root.mjs'
import { afterAll, describe, expect, it } from 'vitest'
import {
  createInstallerSuiteCommand,
  assertInstallerSuiteReady,
  parseInstallerSuiteMode,
  runInstallerSuite,
} from './run-installer-suite.mjs'

describe('installer suite runner', () => {
  it('只接受 quick 或 full 作为唯一模式参数', () => {
    expect(parseInstallerSuiteMode(['quick'])).toBe('quick')
    expect(parseInstallerSuiteMode(['full'])).toBe('full')
    expect(() => parseInstallerSuiteMode([])).toThrow('安装 E2E 套件模式仅支持 quick 或 full')
    expect(() => parseInstallerSuiteMode(['quick', 'extra'])).toThrow('安装 E2E 套件模式仅支持 quick 或 full')
    expect(() => parseInstallerSuiteMode(['slow'])).toThrow('安装 E2E 套件模式仅支持 quick 或 full')
  })

  it('quick 与 full 分别只运行各自隔离的 installer spec', () => {
    const quick = createInstallerSuiteCommand('quick', 'E:/repo')
    expect(quick.command).toBe(process.execPath)
    expect(quick.args).toEqual([
      resolve('E:/repo/node_modules/vitest/vitest.mjs'),
      'run',
      '--config',
      'vitest.e2e.config.ts',
      'e2e/specs/provisioning-success.installer.e2e.ts',
    ])
    expect(quick.options).toMatchObject({ stdio: 'inherit', windowsHide: true })
    expect(quick.options.env.DSH_E2E_MODE).toBe('quick')
    expect(quick.options.env).toMatchObject({
      DSH_E2E_ROOT: resolve('E:/repo', DEFAULT_E2E_ROOT_DIRECTORY),
      DSH_E2E_ARTIFACTS: resolve('E:/repo', DEFAULT_E2E_ROOT_DIRECTORY, 'e2e-artifacts'),
    })

    const full = createInstallerSuiteCommand('full', 'E:/repo')
    expect(full.args).toEqual([
      resolve('E:/repo/node_modules/vitest/vitest.mjs'),
      'run',
      '--config',
      'vitest.e2e.config.ts',
      'e2e/specs/upgrade-and-uninstall.installer.e2e.ts',
    ])
    expect(full.options.env.DSH_E2E_MODE).toBe('full')
    expect(full.options.env.DSH_E2E_ROOT).toBe(resolve('E:/repo', DEFAULT_E2E_ROOT_DIRECTORY))
  })

  it('校验构建元数据模式与 DSH_E2E_MODE 一致，quick 不需要升级 spec', () => {
    expect(() => assertInstallerSuiteReady('quick', readyOptions('quick', false))).not.toThrow()
    expect(() => assertInstallerSuiteReady('full', readyOptions('quick', true, 'full')))
      .toThrow('E2E 构建元数据模式与 DSH_E2E_MODE 不匹配')
  })

  it('full 在缺少升级和卸载矩阵 spec 时拒绝执行', () => {
    expect(() => assertInstallerSuiteReady('full', readyOptions('full', false)))
      .toThrow('full 安装 E2E 尚未接入升级和卸载矩阵 spec，已拒绝执行')
    expect(() => assertInstallerSuiteReady('full', readyOptions('full', true))).not.toThrow()
  })

  it('保留子进程退出码，并将信号转换为非零退出码', async () => {
    await expect(runInstallerSuite('quick', {
      cwd: 'E:/repo',
      ...readyOptions('quick', false),
      spawnProcess: () => childThatExits(23),
    })).resolves.toBe(23)
    await expect(runInstallerSuite('full', {
      cwd: 'E:/repo',
      ...readyOptions('full', true),
      spawnProcess: () => childThatExits(null, 'SIGTERM'),
    })).resolves.toBe(143)
  })

  it('将启动错误返回给调用方', async () => {
    await expect(runInstallerSuite('quick', {
      cwd: 'E:/repo',
      ...readyOptions('quick', false),
      spawnProcess: () => childThatErrors(new Error('spawn failed')),
    })).rejects.toThrow('spawn failed')
  })

  describe.each(['quick', 'full'] as const)('%s ownership gate', (mode) => {
    it('rejects an unowned default root before metadata read or child spawn', async () => {
      const cwd = temporaryRoot()
      let metadataRead = false
      let spawned = false

      expect(() => runInstallerSuite(mode, {
        cwd,
        env: {},
        readFile: () => { metadataRead = true; return JSON.stringify({ mode }) },
        exists: () => true,
        spawnProcess: () => { spawned = true; return childThatExits(0) },
      })).toThrow('默认 E2E root 未受本套件所有权标记保护')
      expect(metadataRead).toBe(false)
      expect(spawned).toBe(false)
    })

    it('rejects a root marker junction before metadata read or child spawn', async (context) => {
      const cwd = temporaryRoot()
      const root = resolve(cwd, DEFAULT_E2E_ROOT_DIRECTORY)
      const marker = join(root, '.dsh-e2e-root-owned')
      const external = join(cwd, 'external-marker')
      mkdirSync(root)
      mkdirSync(external)
      try {
        symlinkSync(external, marker, process.platform === 'win32' ? 'junction' : 'dir')
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'EPERM') {
          context.skip('当前 Windows 权限不允许创建 junction reparse point')
          return
        }
        throw error
      }
      let metadataRead = false
      let spawned = false

      expect(() => runInstallerSuite(mode, {
        cwd,
        env: {},
        readFile: () => { metadataRead = true; return JSON.stringify({ mode }) },
        exists: () => true,
        spawnProcess: () => { spawned = true; return childThatExits(0) },
      })).toThrow('默认 E2E root 未受本套件所有权标记保护')
      expect(metadataRead).toBe(false)
      expect(spawned).toBe(false)
    })

    it('accepts a normal owned root and retains the metadata mode gate', () => {
      const cwd = temporaryRoot()
      const root = resolve(cwd, DEFAULT_E2E_ROOT_DIRECTORY)
      const artifacts = join(root, 'e2e-artifacts')
      initializeDefaultE2ERoot(root)
      mkdirSync(artifacts)
      writeFileSync(join(artifacts, '.dsh-e2e-artifacts-owned'), 'E2E-owned', 'utf8')

      expect(() => assertInstallerSuiteReady(mode, {
        cwd,
        env: {},
        readFile: () => JSON.stringify({ mode }),
        exists: () => true,
      })).not.toThrow()
      expect(() => assertInstallerSuiteReady(mode, {
        cwd,
        env: {},
        readFile: () => JSON.stringify({ mode: mode === 'quick' ? 'full' : 'quick' }),
        exists: () => true,
      })).toThrow('E2E 构建元数据模式与 DSH_E2E_MODE 不匹配')
    })
  })
})

function readyOptions(metadataMode: 'quick' | 'full', fullSpecExists: boolean, e2eMode = metadataMode) {
  return {
    env: { DSH_E2E_ARTIFACTS: 'E:/artifacts', DSH_E2E_MODE: e2eMode },
    readFile: () => JSON.stringify({ mode: metadataMode }),
    exists: () => fullSpecExists,
    validatePaths: () => {},
  }
}

const temporaryRoots: string[] = []
afterAll(() => {
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true })
})

function temporaryRoot() {
  const root = mkdtempSync(join(tmpdir(), 'dsh-runner-owned-root-'))
  temporaryRoots.push(root)
  return root
}

function childThatExits(code: number | null, signal: NodeJS.Signals | null = null) {
  const child = new EventEmitter()
  queueMicrotask(() => child.emit('exit', code, signal))
  return child
}

function childThatErrors(error: Error) {
  const child = new EventEmitter()
  queueMicrotask(() => child.emit('error', error))
  return child
}
