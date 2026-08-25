import { EventEmitter } from 'node:events'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
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

  it('quick 只运行 provisioning quick spec，full 保持完整 installer spec 集', () => {
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

    const full = createInstallerSuiteCommand('full', 'E:/repo')
    expect(full.args).toEqual([
      resolve('E:/repo/node_modules/vitest/vitest.mjs'),
      'run',
      '--config',
      'vitest.e2e.config.ts',
    ])
    expect(full.options.env.DSH_E2E_MODE).toBe('full')
  })

  it('校验构建元数据模式与 DSH_E2E_MODE 一致，quick 不需要升级 spec', () => {
    expect(() => assertInstallerSuiteReady('quick', readyOptions('quick', false))).not.toThrow()
    expect(() => assertInstallerSuiteReady('full', readyOptions('quick', true, 'full')))
      .toThrow('E2E 构建元数据模式与 DSH_E2E_MODE 不匹配')
  })

  it('full 在缺少明确升级验证 spec 时拒绝执行', () => {
    expect(() => assertInstallerSuiteReady('full', readyOptions('full', false)))
      .toThrow('full 安装 E2E 尚未接入升级验证 spec，已拒绝执行')
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
})

function readyOptions(metadataMode: 'quick' | 'full', fullSpecExists: boolean, e2eMode = metadataMode) {
  return {
    env: { DSH_E2E_ARTIFACTS: 'E:/artifacts', DSH_E2E_MODE: e2eMode },
    readFile: () => JSON.stringify({ mode: metadataMode }),
    exists: () => fullSpecExists,
  }
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
