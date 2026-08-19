import { createHash } from 'node:crypto'
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

const NODE_VERSION = '24.14.0'
const DSH_VERSION = '0.1.0-rc.7'
const PNPM_VERSION = '11.7.0'
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
const appDir = join(stage, 'app')
mkdirSync(appDir, { recursive: true })
cpSync(pluginTarball, join(appDir, 'desktop-plugin.tgz'))
cpSync(join('scripts', 'desktop-profile.mjs'), join(appDir, 'desktop-profile.mjs'))
writeFileSync(join(appDir, 'package.json'), JSON.stringify({
  name: 'dsh-desktop-runtime', private: true, type: 'module',
  dependencies: {
    '@deepseek-ai/dsh': DSH_VERSION,
    '@dsh/desktop-plugin': 'file:desktop-plugin.tgz',
    pnpm: PNPM_VERSION,
  },
}, null, 2))
// --no-legacy-peer-deps：dsh-app-boot 的 peerDependencies（cordis-plugin-group、
// dsh-invariants）必须由这里的安装补齐，否则 Runtime 启动时 ERR_MODULE_NOT_FOUND。
run('npm', ['install', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund', '--no-legacy-peer-deps'], { cwd: appDir })
writeLauncher(appDir, desktopPluginVersion)
writePnpmShim(stage, target)
mkdirSync(join(stage, 'catalog'), { recursive: true })
cpSync(join('runtime', 'catalog', 'community.json'), join(stage, 'catalog', 'community.json'))

const archive = join(output, target === 'windows-x86_64' ? `dsh-runtime-${target}.zip` : `dsh-runtime-${target}.tar.gz`)
if (target === 'windows-x86_64') run(tarExecutable, ['-a', '-cf', archive, '.'], { cwd: stage })
else run(tarExecutable, ['-czf', archive, '.'], { cwd: stage })
const bytes = readFileSync(archive)
const manifest = {
  schemaVersion: 1,
  version: args.version || '0.1.0',
  dshVersion: DSH_VERSION,
  target,
  url: args.url,
  size: statSync(archive).size,
  sha256: createHash('sha256').update(bytes).digest('hex'),
  archive: target === 'windows-x86_64' ? 'zip' : 'tar-gz',
  entrypoint: target === 'windows-x86_64' ? 'node.exe' : 'bin/node',
  args: ['app/launcher.mjs', '--port', '{port}'],
  healthPath: '/',
  signature: '',
}
writeFileSync(join(output, `manifest-${target}.unsigned.json`), `${JSON.stringify(manifest, null, 2)}\n`)
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

function writeLauncher(appDir, desktopPluginVersion) {
  writeFileSync(join(appDir, 'launcher.mjs'), `
import { readFileSync } from 'node:fs'
import { delimiter, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn, spawnSync } from 'node:child_process'
import { ensureDesktopProfile } from './desktop-profile.mjs'
const app = dirname(fileURLToPath(import.meta.url))
const runtime = dirname(app)
const dsh = join(app, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js')
const plugin = join(app, 'desktop-plugin.tgz')
const home = process.env.DSH_HOME
if (!home) throw new Error('DSH_HOME is required')
const installedManifest = join(home, 'profiles', 'desktop', 'node_modules', '@dsh', 'desktop-plugin', 'package.json')
let installedVersion = ''
try { installedVersion = JSON.parse(readFileSync(installedManifest, 'utf8')).version ?? '' } catch {}
const bin = join(runtime, 'desktop-bin')
const env = { ...process.env, PATH: bin + delimiter + (process.env.PATH ?? '') }
if (installedVersion !== '${desktopPluginVersion}') {
  const result = spawnSync(process.execPath, [dsh, 'plugin', '--profile', 'desktop', 'add', plugin], { stdio: 'inherit', env })
  if (result.status !== 0) process.exit(result.status ?? 1)
}
ensureDesktopProfile(join(home, 'profiles', 'desktop', 'package.json'))
const child = spawn(process.execPath, [dsh, '--profile', 'desktop', ...process.argv.slice(2)], { stdio: 'inherit', env })
child.once('exit', (code, signal) => process.exit(code ?? (signal ? 128 : 1)))
`, 'utf8')
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
