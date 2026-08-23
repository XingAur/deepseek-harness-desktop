import { createHash } from 'node:crypto'
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { materializeRuntimeLinks } from './materialize-runtime-links.mjs'
import { assertRuntimePeerDependencies, runtimePeerDependencies } from './runtime-peer-dependencies.mjs'

const NODE_VERSION = '24.14.0'
const DSH_VERSION = '0.1.0-rc.8'
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
const desktopPluginSha256 = createHash('sha256').update(readFileSync(pluginTarball)).digest('hex')
const appDir = join(stage, 'app')
mkdirSync(appDir, { recursive: true })
cpSync(pluginTarball, join(appDir, 'desktop-plugin.tgz'))
cpSync(join('scripts', 'desktop-profile.mjs'), join(appDir, 'desktop-profile.mjs'))
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
writeLauncher(appDir, desktopPluginVersion, desktopPluginSha256, args.version || '0.1.0')
writePnpmShim(stage, target)
materializeRuntimeLinks(stage, output)

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
  healthPath: '/__desktop/health',
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

function writeLauncher(appDir, desktopPluginVersion, desktopPluginSha256, runtimeVersion) {
  writeFileSync(join(appDir, 'launcher.mjs'), `
import { delimiter, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn, spawnSync } from 'node:child_process'
import { createServer, request as requestHttp } from 'node:http'
import { createServer as createNetServer } from 'node:net'
import { ensureDesktopProfile } from './desktop-profile.mjs'
import { markerMatches, writeInstallMarker } from './plugin-install-state.mjs'
import { attachRuntimeWebSocketProxy } from './runtime-websocket-proxy.mjs'
const app = dirname(fileURLToPath(import.meta.url))
const runtime = dirname(app)
const dsh = join(app, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js')
const plugin = join(app, 'desktop-plugin.tgz')
const home = process.env.DSH_HOME
if (!home) throw new Error('DSH_HOME is required')
const installMarker = join(home, 'profiles', 'desktop', '.desktop-plugin-install.json')
const bin = join(runtime, 'desktop-bin')
const env = { ...process.env, PATH: bin + delimiter + (process.env.PATH ?? '') }
if (!(await markerMatches(installMarker, '${desktopPluginVersion}', '${desktopPluginSha256}'))) {
  const result = spawnSync(process.execPath, [dsh, 'plugin', '--profile', 'desktop', 'add', plugin], {
    stdio: 'inherit', env, windowsHide: process.platform === 'win32',
  })
  if (result.status !== 0) process.exit(result.status ?? 1)
  await writeInstallMarker(installMarker, '${desktopPluginVersion}', '${desktopPluginSha256}')
}
ensureDesktopProfile(join(home, 'profiles', 'desktop', 'package.json'))
const cliArgs = process.argv.slice(2)
const portIndex = cliArgs.indexOf('--port')
const publicPort = Number(portIndex >= 0 ? cliArgs[portIndex + 1] : NaN)
if (!Number.isInteger(publicPort) || publicPort < 1 || publicPort > 65535) throw new Error('--port is required')
const backendPort = await reserveLoopbackPort()
const dshArgs = [...cliArgs]
dshArgs[portIndex + 1] = String(backendPort)
const health = JSON.stringify({
  runtimeVersion: '${runtimeVersion}',
  profileId: process.env.DSH_DESKTOP_PROFILE_ID ?? '',
  profileRevision: Number(process.env.DSH_DESKTOP_PROFILE_REVISION ?? 0),
  controlApi: true,
  webUi: true,
})
const proxy = createServer((request, response) => {
  if (request.url === '/__desktop/health') {
    response.writeHead(200, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(health) })
    response.end(health)
    return
  }
  if (request.url === '/__desktop/control/health') {
    const body = '{"ready":true}'
    response.writeHead(200, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) })
    response.end(body)
    return
  }
  const upstream = requestHttp({
    hostname: '127.0.0.1', port: backendPort, method: request.method, path: request.url,
    // Preserve the browser-visible Host header. The Runtime trust fence compares
    // it with Origin; rewriting Host to the private backend port makes same-origin
    // WebView requests look cross-origin and rejects workspace mutations with 403.
    headers: request.headers,
  }, (upstreamResponse) => {
    response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers)
    upstreamResponse.pipe(response)
  })
  upstream.once('error', () => {
    if (!response.headersSent) response.writeHead(503, { 'content-type': 'text/plain' })
    response.end('DeepSeek Harness is starting')
  })
  request.pipe(upstream)
})
attachRuntimeWebSocketProxy(proxy, { port: backendPort })
await new Promise((resolve, reject) => {
  proxy.once('error', reject)
  proxy.listen(publicPort, '127.0.0.1', resolve)
})
const child = spawn(process.execPath, [dsh, '--profile', 'desktop', ...dshArgs], {
  stdio: 'inherit', env, windowsHide: process.platform === 'win32',
})
child.once('exit', (code, signal) => proxy.close(() => process.exit(code ?? (signal ? 128 : 1))))

function reserveLoopbackPort() {
  return new Promise((resolve, reject) => {
    const server = createNetServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close((error) => error ? reject(error) : resolve(port))
    })
  })
}
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
