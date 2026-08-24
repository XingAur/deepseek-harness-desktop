import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { startFakeDeepSeek } from './e2e/fake-deepseek-server.mjs'
import { createCandidateSessionDriver } from './runtime-session-contract-client.mjs'
import {
  RuntimeSessionContractError,
  runRuntimeSessionContract,
} from './runtime-session-contract.mjs'

const PROMPT_MARKER = 'SESSION_CONTRACT_PROMPT'
const REPLY_MARKER = 'SESSION_CONTRACT_PONG'

export function parseRuntimeSessionContractArgs(values) {
  const args = Object.fromEntries(values.map((value) => {
    const [key, ...rest] = value.replace(/^--/, '').split('=')
    return [key, rest.join('=')]
  }))
  for (const name of ['runtime-root', 'report', 'runtime-version']) {
    if (typeof args[name] !== 'string' || args[name].trim() === '') {
      throw new TypeError(`缺少必需参数 --${name}`)
    }
  }
  const timeoutMs = args['timeout-ms'] === undefined ? 90_000 : Number(args['timeout-ms'])
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) throw new TypeError('--timeout-ms 必须是正整数')
  return Object.freeze({
    runtimeRoot: resolve(args['runtime-root']),
    reportPath: resolve(args.report),
    runtimeVersion: args['runtime-version'].trim(),
    timeoutMs,
  })
}

export function resolveCandidateRuntimeLayout(runtimeRoot) {
  if (typeof runtimeRoot !== 'string' || !isAbsolute(runtimeRoot)) {
    throw new RuntimeSessionContractError('protocol-mismatch', '候选 Runtime 根目录必须是绝对路径')
  }
  const root = resolve(runtimeRoot)
  const nodeExecutable = join(root, process.platform === 'win32' ? 'node.exe' : 'bin/node')
  const appDirectory = join(root, 'app')
  const launcher = join(appDirectory, 'launcher.mjs')
  if (!existsSync(nodeExecutable) || !existsSync(launcher)) {
    throw new RuntimeSessionContractError('protocol-mismatch', '候选 Runtime 缺少 Node 或 launcher 入口')
  }
  return Object.freeze({ root, nodeExecutable, appDirectory, launcher })
}

export function sanitizeRuntimeSessionContractReport(result, metadata) {
  const report = {
    schemaVersion: 1,
    runtimeVersion: metadata.runtimeVersion,
    platform: `${process.platform}-${process.arch}`,
    ok: result.ok,
    durationMs: safeDuration(result.durationMs),
  }
  if (!result.ok) {
    report.failedStage = result.failedStage
    report.category = result.category
    if (Number.isInteger(metadata.processExitCode)) report.processExitCode = metadata.processExitCode
    if (result.cleanupFailure !== undefined) report.cleanupFailure = { category: 'cleanup-failed' }
  }
  if (typeof metadata.providerRequestObserved === 'boolean') {
    report.providerRequestObserved = metadata.providerRequestObserved
  }
  report.stages = result.stages.map((stage) => ({
    stage: stage.stage,
    ok: stage.ok,
    durationMs: safeDuration(stage.durationMs),
    ...(stage.category === undefined ? {} : { category: stage.category }),
  }))
  return report
}

export async function runRuntimeSessionContractCommand(options) {
  const layout = resolveCandidateRuntimeLayout(options.runtimeRoot)
  const contractRoot = await mkdtemp(join(tmpdir(), 'dsh-runtime-session-contract-'))
  const home = join(contractRoot, 'dsh-home')
  const workspacePath = join(contractRoot, '工作区 Ω')
  const caPath = join(contractRoot, 'fixture-ca.pem')
  await mkdir(home, { recursive: true })
  await mkdir(workspacePath, { recursive: true })
  const model = await startFakeDeepSeek({ text: REPLY_MARKER })
  await writeFile(caPath, model.caCertificate, 'utf8')
  const port = await reserveLoopbackPort()
  const lifecycle = createCandidateRuntimeLifecycle({
    layout,
    port,
    healthTimeoutMs: Math.max(1, options.timeoutMs - 5_000),
    environment: {
      ...process.env,
      DSH_HOME: home,
      DSH_DESKTOP_PROFILE_ID: 'contract',
      DSH_DESKTOP_PROFILE_REVISION: '1',
      DSH_E2E_MODEL_ENDPOINT: `${model.url}/chat/completions`,
      DEEPSEEK_BASE_URL: model.url,
      DEEPSEEK_API_KEY: 'sk-session-contract-fixture',
      NODE_EXTRA_CA_CERTS: caPath,
    },
  })
  const driver = createCandidateSessionDriver({
    appDirectory: layout.appDirectory,
    origin: `http://127.0.0.1:${port}`,
    workspacePath,
    promptMarker: PROMPT_MARKER,
    replyMarker: REPLY_MARKER,
    eventTimeoutMs: Math.max(1, options.timeoutMs - 10_000),
    lifecycle,
  })

  try {
    const result = await runRuntimeSessionContract(driver, { timeoutMs: options.timeoutMs })
    const report = sanitizeRuntimeSessionContractReport(result, {
      runtimeVersion: options.runtimeVersion,
      processExitCode: lifecycle.processExitCode(),
      providerRequestObserved: model.requests().length > 0,
    })
    await mkdir(dirname(options.reportPath), { recursive: true })
    await writeFile(options.reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
    return report
  } finally {
    await Promise.allSettled([model.close(), lifecycle.cleanup()])
    await rm(contractRoot, { recursive: true, force: true })
  }
}

export function createCandidateRuntimeLifecycle(options) {
  let child
  let exitCode
  let exitSignal
  let cleanupPromise

  return Object.freeze({
    async start() {
      if (child !== undefined) throw new RuntimeSessionContractError('internal', '候选 Runtime 已经启动')
      child = spawn(options.layout.nodeExecutable, [
        options.layout.launcher,
        '--port',
        String(options.port),
        '--no-open',
      ], {
        cwd: options.layout.root,
        env: options.environment,
        stdio: 'ignore',
        windowsHide: true,
        detached: process.platform !== 'win32',
      })
      child.once('exit', (code, signal) => {
        exitCode = code
        exitSignal = signal
      })
      await new Promise((resolvePromise, rejectPromise) => {
        child.once('spawn', resolvePromise)
        child.once('error', rejectPromise)
      })
    },

    async ready() {
      const deadline = Date.now() + options.healthTimeoutMs
      while (Date.now() < deadline) {
        if (exitCode !== undefined || exitSignal !== undefined) {
          throw new RuntimeSessionContractError('process-exited', '候选 Runtime 在 ready 前退出')
        }
        try {
          const response = await fetch(`http://127.0.0.1:${options.port}/__desktop/health`, {
            signal: AbortSignal.timeout(1_000),
          })
          if (response.ok) return
        } catch {
          // Runtime 首次安装插件和启动代理期间连接失败是预期的有限过渡状态。
        }
        await delay(200)
      }
      throw new RuntimeSessionContractError('timeout', '候选 Runtime ready 超时')
    },

    cleanup() {
      cleanupPromise ??= terminateCandidateProcess(child)
      return cleanupPromise
    },

    processExitCode() {
      return typeof exitCode === 'number' ? exitCode : undefined
    },
  })
}

async function terminateCandidateProcess(child) {
  if (child === undefined || child.exitCode !== null || child.signalCode !== null) return
  if (process.platform === 'win32') {
    await runProcess('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'])
    return
  }
  try {
    process.kill(-child.pid, 'SIGTERM')
  } catch (error) {
    if (error?.code !== 'ESRCH') throw error
    return
  }
  if (await waitForExit(child, 5_000)) return
  try {
    process.kill(-child.pid, 'SIGKILL')
  } catch (error) {
    if (error?.code !== 'ESRCH') throw error
  }
  await waitForExit(child, 5_000)
}

function runProcess(command, args) {
  return new Promise((resolvePromise, rejectPromise) => {
    const processHandle = spawn(command, args, { stdio: 'ignore', windowsHide: true })
    processHandle.once('error', rejectPromise)
    processHandle.once('exit', () => resolvePromise())
  })
}

function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true)
  return new Promise((resolvePromise) => {
    const timer = setTimeout(() => {
      child.off('exit', exited)
      resolvePromise(false)
    }, timeoutMs)
    const exited = () => {
      clearTimeout(timer)
      resolvePromise(true)
    }
    child.once('exit', exited)
  })
}

function reserveLoopbackPort() {
  return new Promise((resolvePromise, rejectPromise) => {
    const server = createServer()
    server.once('error', rejectPromise)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address !== null ? address.port : 0
      server.close((error) => error === undefined ? resolvePromise(port) : rejectPromise(error))
    })
  })
}

function delay(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds))
}

function safeDuration(value) {
  return Number.isFinite(value) && value >= 0 ? Math.round(value) : 0
}

async function main() {
  const options = parseRuntimeSessionContractArgs(process.argv.slice(2))
  const report = await runRuntimeSessionContractCommand(options)
  console.log(report.ok ? 'Runtime Session contract passed' : `Runtime Session contract failed: ${report.failedStage}/${report.category}`)
  if (!report.ok) process.exitCode = 1
}

const invokedPath = process.argv[1] === undefined ? '' : resolve(process.argv[1])
const modulePath = fileURLToPath(import.meta.url)
if (process.platform === 'win32' ? invokedPath.toLowerCase() === modulePath.toLowerCase() : invokedPath === modulePath) {
  main().catch((error) => {
    const category = error instanceof RuntimeSessionContractError ? error.category : 'internal'
    console.error(`Runtime Session contract command failed: ${category}`)
    process.exitCode = 1
  })
}
