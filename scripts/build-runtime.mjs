import { createHash } from 'node:crypto'
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { materializeRuntimeLinks } from './materialize-runtime-links.mjs'
import { assertRuntimePeerDependencies, runtimePeerDependencies } from './runtime-peer-dependencies.mjs'
import { loadReleaseVersions } from './release-versions.mjs'
import { writeUnsignedRuntimeManifest } from './runtime-release-manifest.mjs'
import { inspectAssembledRuntimeCapabilities } from './runtime-build-capabilities.mjs'
import { writeRuntimeLauncher } from './runtime-launcher.mjs'

const versions = loadReleaseVersions()
const NODE_VERSION = versions.nodeVersion
const DSH_VERSION = versions.dshVersion
const PNPM_VERSION = versions.pnpmVersion
const args = Object.fromEntries(process.argv.slice(2).map((item) => {
  const [key, ...value] = item.replace(/^--/, '').split('=')
  return [key, value.join('=')]
}))
const target = args.target
if (target !== 'windows-x86_64' && target !== 'darwin-aarch64') throw new Error('--target must be windows-x86_64 or darwin-aarch64')
if (!args.url?.startsWith('https://') && !args.url?.startsWith('file://')) throw new Error('--url must be an HTTPS release URL or local file URL')

// Windows 上优先使用系统自带 bsdtar：Git Bash 的 GNU tar 无法处理 zip，
// 且本机 PATH 中 /usr/bin 可能排在 System32 之前。
const windowsTar = process.env.SystemRoot ? join(process.env.SystemRoot, 'System32', 'tar.exe') : null
const tarExecutable = process.platform === 'win32' && windowsTar && existsSync(windowsTar) ? windowsTar : 'tar'

const output = resolve(args.output || join('runtime-build', target))
const stage = join(output, 'stage')
const downloads = join(output, 'downloads')
rmSync(output, { recursive: true, force: true })
mkdirSync(stage, { recursive: true })
mkdirSync(downloads, { recursive: true })

const nodeName = target === 'windows-x86_64' ? `node-v${NODE_VERSION}-win-x64.zip` : `node-v${NODE_VERSION}-darwin-arm64.tar.gz`
const nodeUrl = `https://nodejs.org/dist/v${NODE_VERSION}/${nodeName}`
const nodeArchive = join(downloads, nodeName)
await download(nodeUrl, nodeArchive)
await verifyNodeChecksum(nodeName, nodeArchive)
run(tarExecutable, ['-xf', nodeArchive, '-C', downloads])
const extracted = join(downloads, nodeName.replace(/\.zip$|\.tar\.gz$/g, ''))
cpSync(extracted, stage, { recursive: true })

run('npm', ['run', 'plugin:build'])
const packOutput = join(output, 'pack')
mkdirSync(packOutput, { recursive: true })
run('npm', ['pack', './packages/dsh-plugin-desktop', '--pack-destination', packOutput])
const pluginTarball = join(packOutput, readdirSync(packOutput).find((file) => file.endsWith('.tgz')))
const desktopPluginVersion = JSON.parse(readFileSync('packages/dsh-plugin-desktop/package.json', 'utf8')).version
const desktopPluginSha256 = createHash('sha256').update(readFileSync(pluginTarball)).digest('hex')
const appDir = join(stage, 'app')
mkdirSync(appDir, { recursive: true })
cpSync(pluginTarball, join(appDir, 'desktop-plugin.tgz'))
cpSync(join('scripts', 'desktop-profile.mjs'), join(appDir, 'desktop-profile.mjs'))
cpSync(join('scripts', 'runtime-capabilities.mjs'), join(appDir, 'runtime-capabilities.mjs'))
cpSync(join('scripts', 'plugin-install-state.mjs'), join(appDir, 'plugin-install-state.mjs'))
cpSync(join('scripts', 'runtime-websocket-proxy.mjs'), join(appDir, 'runtime-websocket-proxy.mjs'))
writeFileSync(join(appDir, 'package.json'), JSON.stringify({
  name: 'dsh-desktop-runtime', private: true, type: 'module',
  dependencies: {
    '@deepseek-ai/dsh': DSH_VERSION,
    '@dsh/desktop-plugin': 'file:desktop-plugin.tgz',
    pnpm: PNPM_VERSION,
    ...runtimePeerDependencies(DSH_VERSION),
  },
}, null, 2))
// DSH 的 peer 图会让 npm 严格解析器发生指数级回溯。完整 peer 闭包已显式
// 固定在 package.json 中，安装后还会扫描依赖树并由真实 Runtime 探活兜底。
const reusedDependencies = restoreDependencyCache(appDir, args['dependency-cache'])
if (!reusedDependencies) {
  run('npm', ['install', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund', '--legacy-peer-deps'], { cwd: appDir })
} else {
  replaceCachedDesktopPlugin(appDir, pluginTarball, output)
}
assertRuntimePeerDependencies(appDir)
const runtimeCapabilities = inspectAssembledRuntimeCapabilities(appDir, {
  dshVersion: DSH_VERSION,
  desktopPluginVersion,
}))
writeFileSync(join(appDir, 'runtime-capabilities.json'), `${JSON.stringify(runtimeCapabilities, null, 2)}\n`, 'utf8')
writeRuntimeLauncher(appDir, { dshVersion: DSH_VERSION, desktopPluginVersion, desktopPluginSha256, runtimeVersion: args.version || '0.1.0' })
writePnpmShim(stage, target)
materializeRuntimeLinks(stage, output)

const archive = join(output, target === 'windows-x86_64' ? `dsh-runtime-${target}.zip` : `dsh-runtime-${target}.tar.gz`)
if (target === 'windows-x86_64') run(tarExecutable, ['-a', '-cf', archive, '.'], { cwd: stage })
else run(tarExecutable, ['-czf', archive, '.'], { cwd: stage })
writeUnsignedRuntimeManifest({
  archivePath: archive,
  outputPath: join(output, `manifest-${target}.unsigned.json`),
  target,
  version: args.version || '0.1.0',
  dshVersion: DSH_VERSION,
  url: args.url,
})
console.log(`Runtime created: ${archive}`)

async function download(url, destination) {
  if (existsSync(destination)) return
  const response = await fetch(url, { redirect: 'error' })
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`)
  writeFileSync(destination, Buffer.from(await response.arrayBuffer()))
}

async function verifyNodeChecksum(filename, archive) {
  const response = await fetch(`https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt`, { redirect: 'error' })
  if (!response.ok) throw new Error('Unable to download Node.js checksums')
  const expected = (await response.text()).split(/\r?\n/).find((line) => line.endsWith(`  ${filename}`))?.split(/\s+/)[0]
  const actual = createHash('sha256').update(readFileSync(archive)).digest('hex')
  if (!expected || expected !== actual) throw new Error('Node.js archive checksum mismatch')
}

function writePnpmShim(stageDir, runtimeTarget) {
  const dir = join(stageDir, 'desktop-bin')
  mkdirSync(dir, { recursive: true })
  if (runtimeTarget === 'windows-x86_64') {
    writeFileSync(join(dir, 'pnpm.cmd'), '@echo off\r\n"%~dp0..\\node.exe" "%~dp0..\\app\\node_modules\\pnpm\\bin\\pnpm.cjs" %*\r\n')
  } else {
    const path = join(dir, 'pnpm')
    writeFileSync(path, '#!/bin/sh\nexec "$(dirname "$0")/../bin/node" "$(dirname "$0")/../app/node_modules/pnpm/bin/pnpm.cjs" "$@"\n', { mode: 0o755 })
  }
}

function restoreDependencyCache(appDir, cacheValue) {
  if (!cacheValue) return false
  const cache = resolve(cacheValue)
  const dshPackage = join(cache, '@deepseek-ai', 'dsh', 'package.json')
  const pnpmPackage = join(cache, 'pnpm', 'package.json')
  if (!existsSync(dshPackage) || !existsSync(pnpmPackage)) throw new Error('--dependency-cache is incomplete')
  if (JSON.parse(readFileSync(dshPackage, 'utf8')).version !== DSH_VERSION) throw new Error('--dependency-cache has the wrong DSH version')
  if (JSON.parse(readFileSync(pnpmPackage, 'utf8')).version !== PNPM_VERSION) throw new Error('--dependency-cache has the wrong pnpm version')
  cpSync(cache, join(appDir, 'node_modules'), { recursive: true })
  return true
}

function replaceCachedDesktopPlugin(appDir, pluginTarball, outputDir) {
  const extracted = join(outputDir, 'cached-desktop-plugin')
  rmSync(extracted, { recursive: true, force: true })
  mkdirSync(extracted, { recursive: true })
  run(tarExecutable, ['-xf', pluginTarball, '-C', extracted])
  const destination = join(appDir, 'node_modules', '@dsh', 'desktop-plugin')
  rmSync(destination, { recursive: true, force: true })
  mkdirSync(dirname(destination), { recursive: true })
  cpSync(join(extracted, 'package'), destination, { recursive: true })
  rmSync(extracted, { recursive: true, force: true })
}

function run(command, commandArgs, options = {}) {
  let executable = command
  let prefix = []
  let shell = false
  if (process.platform === 'win32' && command === 'npm') {
    // 新版 Node 禁止不经 shell 直接 spawn .cmd（EINVAL），改为直接运行 npm 的 JS 入口。
    const cli = join(dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js')
    if (existsSync(cli)) { executable = process.execPath; prefix = [cli] }
    else { executable = 'npm.cmd'; shell = true }
  }
  const result = spawnSync(executable, [...prefix, ...commandArgs], { cwd: options.cwd ?? process.cwd(), stdio: 'inherit', shell })
  if (result.status !== 0) {
    throw new Error(`${command} ${commandArgs.join(' ')} failed with ${result.status ?? result.error?.message ?? 'unknown error'}`)
  }
}
