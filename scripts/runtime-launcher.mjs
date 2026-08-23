import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

export function writeRuntimeLauncher(appDir, { dshVersion, desktopPluginVersion, desktopPluginSha256, runtimeVersion }) {
  writeFileSync(join(appDir, 'launcher.mjs'), `
import { readFileSync } from 'node:fs'
import { delimiter, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn, spawnSync } from 'node:child_process'
import { createServer, request as requestHttp } from 'node:http'
import { createServer as createNetServer } from 'node:net'
import { ensureDesktopProfile } from './desktop-profile.mjs'
import { markerMatches, writeInstallMarker } from './plugin-install-state.mjs'
import { attachRuntimeWebSocketProxy } from './runtime-websocket-proxy.mjs'
import { assertRuntimeCapabilities } from './runtime-capabilities.mjs'
const app = dirname(fileURLToPath(import.meta.url))
const runtime = dirname(app)
const dsh = join(app, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js')
const plugin = join(app, 'desktop-plugin.tgz')
const home = process.env.DSH_HOME
if (!home) throw new Error('DSH_HOME is required')
const expectedVersions = { dshVersion: '${dshVersion}', desktopPluginVersion: '${desktopPluginVersion}' }
const runtimeCapabilities = assertRuntimeCapabilities(JSON.parse(readFileSync(join(app, 'runtime-capabilities.json'), 'utf8')), expectedVersions)
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
ensureDesktopProfile(join(home, 'profiles', 'desktop', 'package.json'), runtimeCapabilities, expectedVersions)
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
  const upstream = requestHttp({ hostname: '127.0.0.1', port: backendPort, method: request.method, path: request.url, headers: request.headers }, (upstreamResponse) => {
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
const child = spawn(process.execPath, [dsh, '--profile', 'desktop', ...dshArgs], { stdio: 'inherit', env, windowsHide: process.platform === 'win32' })
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
