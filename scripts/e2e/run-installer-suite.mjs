import { spawn } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { constants } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { resolveE2EPaths, withE2EPaths } from './default-e2e-paths.mjs'
import { validateOwnedE2EPaths } from './owned-e2e-root.mjs'

const SUPPORTED_MODES = new Set(['quick', 'full'])
const INVALID_MODE_MESSAGE = '安装 E2E 套件模式仅支持 quick 或 full'
const FULL_LIFECYCLE_SPEC = 'e2e/specs/upgrade-and-uninstall.installer.e2e.ts'

export function parseInstallerSuiteMode(args) {
  if (args.length !== 1 || !SUPPORTED_MODES.has(args[0])) throw new Error(INVALID_MODE_MESSAGE)
  return args[0]
}

export function createInstallerSuiteCommand(mode, cwd = process.cwd(), env = process.env) {
  if (!SUPPORTED_MODES.has(mode)) throw new Error(INVALID_MODE_MESSAGE)
  const e2ePaths = resolveE2EPaths(cwd, env)
  const vitest = resolve(cwd, 'node_modules', 'vitest', 'vitest.mjs')
  const args = [vitest, 'run', '--config', 'vitest.e2e.config.ts']
  if (mode === 'quick') args.push('e2e/specs/provisioning-success.installer.e2e.ts')
  return {
    command: process.execPath,
    args,
    options: {
      cwd,
      env: { ...withE2EPaths(env, e2ePaths), DSH_E2E_MODE: mode },
      stdio: 'inherit',
      windowsHide: true,
    },
  }
}

export function assertInstallerSuiteReady(mode, options = {}) {
  if (!SUPPORTED_MODES.has(mode)) throw new Error(INVALID_MODE_MESSAGE)
  const cwd = options.cwd ?? process.cwd()
  const env = options.env ?? process.env
  const readFile = options.readFile ?? readFileSync
  const exists = options.exists ?? existsSync
  const paths = resolveE2EPaths(cwd, env)
  const validatePaths = options.validatePaths ?? validateOwnedE2EPaths
  validatePaths(paths)
  const artifactsRoot = paths.artifactsRoot
  let metadata
  try {
    metadata = JSON.parse(String(readFile(join(artifactsRoot, 'instrumented-setup.json'), 'utf8')))
  } catch {
    throw new Error('E2E 构建元数据不可读取')
  }
  const executionMode = env.DSH_E2E_MODE ?? mode
  if (executionMode !== mode || typeof metadata !== 'object' || metadata === null || metadata.mode !== executionMode) {
    throw new Error('E2E 构建元数据模式与 DSH_E2E_MODE 不匹配')
  }
  if (mode === 'full' && !exists(resolve(cwd, FULL_LIFECYCLE_SPEC))) {
    throw new Error('full 安装 E2E 尚未接入升级和卸载矩阵 spec，已拒绝执行')
  }
}

export function runInstallerSuite(mode, { cwd = process.cwd(), spawnProcess = spawn, env = process.env, readFile = readFileSync, exists = existsSync, validatePaths = validateOwnedE2EPaths } = {}) {
  const executionEnv = { ...withE2EPaths(env, resolveE2EPaths(cwd, env)), DSH_E2E_MODE: mode }
  assertInstallerSuiteReady(mode, { cwd, env: executionEnv, readFile, exists, validatePaths })
  const command = createInstallerSuiteCommand(mode, cwd, executionEnv)
  return new Promise((resolveRun, reject) => {
    let settled = false
    const settle = (callback) => {
      if (settled) return
      settled = true
      callback()
    }
    let child
    try {
      child = spawnProcess(command.command, command.args, command.options)
    } catch (error) {
      settle(() => reject(error))
      return
    }
    child.once('error', (error) => settle(() => reject(error)))
    child.once('exit', (code, signal) => settle(() => {
      if (code !== null) {
        resolveRun(code)
        return
      }
      resolveRun(signalExitCode(signal))
    }))
  })
}

function signalExitCode(signal) {
  const signalNumber = signal === null ? undefined : constants.signals[signal]
  return signalNumber === undefined ? 1 : 128 + signalNumber
}

async function main() {
  const mode = parseInstallerSuiteMode(process.argv.slice(2))
  process.exitCode = await runInstallerSuite(mode)
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  })
}
