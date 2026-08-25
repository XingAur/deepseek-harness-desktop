import { createHash } from 'node:crypto'
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, statSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { pathToFileURL } from 'node:url'
import { loadReleaseVersions } from '../release-versions.mjs'
import { createRuntimeSigningState, loadRuntimeSigningState } from './runtime-signing-state.mjs'
import { findCompatibleRuntimeDependencyCache } from './runtime-dependency-cache.mjs'
import { createInstallerBuildPlan, resolveRuntimeVersion } from './installer-build-plan.mjs'
import { selectChangedInstaller } from './installer-artifact-selection.mjs'

const positional = process.argv.slice(2).filter((arg) => !arg.startsWith('--'))
const mode = process.argv.find((arg) => arg.startsWith('--mode='))?.slice('--mode='.length) ?? 'quick'
const e2eRoot = requiredAbsolute(process.env.DSH_E2E_ROOT ?? resolve('.'), 'E2E root')
const artifacts = requiredAbsolute(positional[1] ?? join(e2eRoot, 'e2e-artifacts'), 'artifact root', false)
const versions = loadReleaseVersions()
const runtimeVersion = resolveRuntimeVersion(process.env.DSH_E2E_RUNTIME_VERSION, versions.runtimeVersion)
initializeOwnedArtifactsRoot(artifacts, e2eRoot)
const archive = positional[0] ? requiredAbsolute(positional[0], 'Runtime archive') : await buildCurrentRuntime(artifacts, runtimeVersion, versions)
if (!existsSync(archive) || !statSync(archive).isFile()) throw new Error(`Runtime archive 不存在：${archive}`)

const plan = createInstallerBuildPlan({ mode, candidateVersion: versions.desktopVersion, artifactsRoot: artifacts })
const signingState = join(artifacts, 'runtime-signing-state.json')
const signing = existsSync(signingState) ? { publicKey: loadRuntimeSigningState(signingState).publicKey } : createRuntimeSigningState(signingState)
const tauriCli = resolve('node_modules/@tauri-apps/cli/tauri.js')
const cargoBin = resolve(process.env.USERPROFILE ?? '', '.cargo/bin')
const bundleRoot = resolve('src-tauri/target/release/bundle/nsis')
const installers = {}

for (const variant of plan.variants) {
  writeFileSync(variant.configPath, JSON.stringify({ version: variant.version }, null, 2), 'utf8')
  const before = snapshotExes(bundleRoot)
  await run(process.execPath, [tauriCli, 'build', '--features', 'e2e', '--config', 'src-tauri/tauri.e2e.conf.json', '--config', variant.configPath, '--bundles', 'nsis'], {
    ...process.env,
    PATH: [cargoBin, process.env.PATH].filter(Boolean).join(';'),
    DSH_DESKTOP_RELEASE_PUBLIC_KEY: signing.publicKey,
  })
  const candidate = selectChangedInstaller(before, snapshotExes(bundleRoot))
  copyFileSync(candidate, variant.installerPath)
  installers[variant.name] = { path: variant.installerPath, version: variant.version, sha256: sha256(readFileSync(variant.installerPath)) }
}

const metadata = { schemaVersion: 2, mode: plan.mode, artifactRoot: artifacts, runtimeArchive: archive, runtimeVersion, signingState, sourceCommit: process.env.GITHUB_SHA ?? null, installers }
writeFileSync(join(artifacts, 'instrumented-setup.json'), JSON.stringify(metadata, null, 2), 'utf8')
process.stdout.write(`${plan.variants.find(({ name }) => name === 'candidate').installerPath}\n`)

function snapshotExes(root) {
  if (!existsSync(root)) return new Map()
  return new Map(readdirSync(root)
    .filter((name) => name.toLowerCase().endsWith('.exe'))
    .map((name) => {
      const path = join(root, name)
      return [path, sha256(readFileSync(path))]
    }))
}
function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

async function buildCurrentRuntime(artifactsRoot, runtimeVersion, versions) {
  const runtimeOutput = join(artifactsRoot, 'runtime-build-windows-x86_64')
  const runtimeArchive = join(runtimeOutput, 'dsh-runtime-windows-x86_64.zip')
  const dependencyCache = findCompatibleRuntimeDependencyCache({
    candidates: [resolve('runtime-build/windows-x86_64/stage/app/node_modules'), resolve('runtime-build/windows-x86_64-preview/stage/app/node_modules')],
    dshVersion: versions.dshVersion,
    pnpmVersion: versions.pnpmVersion,
  })
  const args = [resolve('scripts/build-runtime.mjs'), '--target=windows-x86_64', `--version=${runtimeVersion}`, `--url=${pathToFileURL(runtimeArchive).href}`, `--output=${runtimeOutput}`]
  if (dependencyCache !== undefined) args.push(`--dependency-cache=${dependencyCache}`)
  await run(process.execPath, args, process.env)
  return runtimeArchive
}

function run(command, args, env) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(command, args, { env, stdio: 'inherit', windowsHide: true })
    child.once('error', reject)
    child.once('exit', (code, signal) => code === 0
      ? resolveRun()
      : reject(new Error(`构建命令失败：code=${String(code)} signal=${String(signal)}`)))
  })
}

function requiredAbsolute(value, name, mustExist = true) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${name} 参数不能为空`)
  const path = resolve(value)
  if (!isAbsolute(path)) throw new Error(`${name} 必须是绝对路径`)
  if (mustExist && !existsSync(path)) throw new Error(`${name} 不存在：${path}`)
  return path
}

function initializeOwnedArtifactsRoot(artifactsRoot, e2eRoot) {
  assertSafeExistingPath(e2eRoot)
  if (!lstatSync(e2eRoot).isDirectory()) throw new Error('E2E root 必须是目录')
  assertStrictDescendant(e2eRoot, artifactsRoot)
  if (existsSync(artifactsRoot)) {
    validateOwnedArtifactsRoot(artifactsRoot)
    return
  }
  const parent = dirname(artifactsRoot)
  assertSafeExistingPath(parent)
  if (!lstatSync(parent).isDirectory()) throw new Error('artifact root 父目录无效')
  assertContainedOrEqual(e2eRoot, parent)
  mkdirSync(artifactsRoot)
  assertSafeExistingPath(artifactsRoot)
  writeFileSync(join(artifactsRoot, '.dsh-e2e-artifacts-owned'), 'E2E-owned', { encoding: 'utf8', flag: 'wx' })
  validateOwnedArtifactsRoot(artifactsRoot)
}

function validateOwnedArtifactsRoot(artifactsRoot) {
  assertSafeExistingPath(artifactsRoot)
  if (!lstatSync(artifactsRoot).isDirectory()) throw new Error('artifact root 必须是目录')
  const marker = join(artifactsRoot, '.dsh-e2e-artifacts-owned')
  assertSafeExistingPath(marker)
  if (!lstatSync(marker).isFile() || readFileSync(marker, 'utf8') !== 'E2E-owned') {
    throw new Error('artifact root ownership marker 无效')
  }
}

function assertSafeExistingPath(path) {
  let current = resolve(path)
  while (true) {
    const stat = lstatSync(current)
    if (stat.isSymbolicLink() || safePathKey(realpathSync.native(current)) !== safePathKey(current)) {
      throw new Error(`不安全路径：${path}`)
    }
    const parent = dirname(current)
    if (parent === current) return
    current = parent
  }
}

function assertStrictDescendant(root, path) {
  const relation = relative(root, path)
  if (relation === '' || relation === '..' || relation.startsWith('..\\') || relation.startsWith('../') || isAbsolute(relation)) {
    throw new Error('artifact root 必须严格位于 E2E root 内')
  }
}

function assertContainedOrEqual(root, path) {
  const relation = relative(root, path)
  if (relation === '..' || relation.startsWith('..\\') || relation.startsWith('../') || isAbsolute(relation)) {
    throw new Error('artifact root 父目录必须位于 E2E root 内')
  }
}

function safePathKey(path) {
  const normalized = resolve(path).replace(/^\\\\\?\\/, '')
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized
}
