import { copyFileSync, existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { basename, isAbsolute, join, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { pathToFileURL } from 'node:url'
import { createRuntimeSigningState, loadRuntimeSigningState } from './runtime-signing-state.mjs'

const artifacts = requiredAbsolute(process.argv[3] ?? resolve('e2e-artifacts'), 'artifact root', false)
mkdirSync(artifacts, { recursive: true })
const archive = process.argv[2]
  ? requiredAbsolute(process.argv[2], 'Runtime archive')
  : await buildCurrentRuntime(artifacts)
if (!existsSync(archive) || !statSync(archive).isFile()) throw new Error(`Runtime archive 不存在：${archive}`)

const signingState = join(artifacts, 'runtime-signing-state.json')
const signing = existsSync(signingState)
  ? { path: signingState, publicKey: loadRuntimeSigningState(signingState).publicKey }
  : createRuntimeSigningState(signingState)
const tauriCli = resolve('node_modules/@tauri-apps/cli/tauri.js')
const cargoBin = resolve(process.env.USERPROFILE ?? '', '.cargo/bin')
await run(process.execPath, [
  tauriCli,
  'build',
  '--features',
  'e2e',
  '--config',
  'src-tauri/tauri.e2e.conf.json',
  '--bundles',
  'nsis',
], {
  ...process.env,
  PATH: [cargoBin, process.env.PATH].filter(Boolean).join(';'),
  DSH_DESKTOP_RELEASE_PUBLIC_KEY: signing.publicKey,
})

const bundleRoot = resolve('src-tauri/target/release/bundle/nsis')
const candidates = readdirSync(bundleRoot)
  .filter((name) => name.toLowerCase().endsWith('.exe'))
  .map((name) => join(bundleRoot, name))
  .sort((left, right) => statSync(right).mtimeMs - statSync(left).mtimeMs)
if (candidates.length === 0) throw new Error('Tauri 没有生成 NSIS 安装包')

const installer = join(artifacts, 'DeepSeek-Harness-Desktop-E2E-Web-Setup-x64.exe')
copyFileSync(candidates[0], installer)
const metadata = {
  schemaVersion: 1,
  installer,
  artifactRoot: artifacts,
  runtimeArchive: archive,
  runtimeVersion: process.env.DSH_E2E_RUNTIME_VERSION ?? '0.1.0-preview',
  signingState,
  sourceBundle: basename(candidates[0]),
}
writeFileSync(join(artifacts, 'instrumented-setup.json'), JSON.stringify(metadata, null, 2), 'utf8')
process.stdout.write(`${installer}\n`)

async function buildCurrentRuntime(artifactsRoot) {
  const runtimeOutput = join(artifactsRoot, 'runtime-build-windows-x86_64')
  const runtimeArchive = join(runtimeOutput, 'dsh-runtime-windows-x86_64.zip')
  const dependencyCache = resolve('runtime-build/windows-x86_64-preview/stage/app/node_modules')
  if (!existsSync(dependencyCache)) {
    throw new Error(`缺少 Runtime 依赖缓存：${dependencyCache}`)
  }
  await run(process.execPath, [
    resolve('scripts/build-runtime.mjs'),
    '--target=windows-x86_64',
    `--version=${process.env.DSH_E2E_RUNTIME_VERSION ?? '0.1.0-preview'}`,
    `--url=${pathToFileURL(runtimeArchive).href}`,
    `--output=${runtimeOutput}`,
    `--dependency-cache=${dependencyCache}`,
  ], process.env)
  return runtimeArchive
}

function run(command, args, env) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(command, args, { env, stdio: 'inherit', windowsHide: true })
    child.once('error', reject)
    child.once('exit', (code, signal) => {
      if (code === 0) resolveRun()
      else reject(new Error(`构建命令失败：code=${String(code)} signal=${String(signal)}`))
    })
  })
}

function requiredAbsolute(value, name, mustExist = true) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${name} 参数不能为空`)
  const path = resolve(value)
  if (!isAbsolute(path)) throw new Error(`${name} 必须是绝对路径`)
  if (mustExist && !existsSync(path)) throw new Error(`${name} 不存在：${path}`)
  return path
}
