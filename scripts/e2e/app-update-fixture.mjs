import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { isAbsolute, join, resolve } from 'node:path'
import { promisify } from 'node:util'
import { startLoopbackHttps } from './tls-fixture.mjs'

const execFileAsync = promisify(execFile)

export async function startAppUpdateFixture(options = {}) {
  const requests = []
  const releases = new Map()
  let latestVersion
  let origin = ''

  const listener = await startLoopbackHttps((request, response) => {
    const url = new URL(request.url ?? '/', origin)
    requests.push(Object.freeze({
      method: request.method ?? 'GET',
      path: url.pathname,
      at: new Date().toISOString(),
    }))

    if (url.pathname === '/latest.json') {
      if (latestVersion === undefined) {
        response.writeHead(404).end('no release')
        return
      }
      sendJson(response, releases.get(latestVersion).metadata)
      return
    }

    const match = /^\/downloads\/(.+)\.exe$/.exec(url.pathname)
    const release = match === null ? undefined : releases.get(decodeURIComponent(match[1]))
    if (release === undefined) {
      response.writeHead(404).end('not found')
      return
    }
    response.writeHead(200, {
      'content-length': String(release.payload.length),
      'content-type': 'application/vnd.microsoft.portable-executable',
    }).end(release.payload)
  }, options.tls)
  origin = listener.url

  return Object.freeze({
    endpoint: `${listener.url}/latest.json`,
    caCertificate: listener.tls.caCertificate,
    async publish(version, payload) {
      validateVersion(version)
      const candidate = Buffer.from(payload)
      if (candidate.length === 0) throw new Error('应用更新 candidate 不能为空')
      const digest = createHash('sha256').update(candidate).digest('hex')
      const existing = releases.get(version)
      if (existing !== undefined && existing.digest !== digest) {
        throw new Error(`应用更新 ${version} 已发布且内容不同`)
      }
      const signature = options.signer === undefined
        ? `fixture-sha256:${digest}`
        : await options.signer(candidate)
      const encodedVersion = encodeURIComponent(version)
      const metadata = {
        version,
        notes: 'DeepSeek Harness deterministic E2E update',
        pub_date: '2026-01-01T00:00:00Z',
        platforms: {
          'windows-x86_64': {
            signature,
            url: `${origin}/downloads/${encodedVersion}.exe`,
          },
        },
      }
      releases.set(version, { digest, metadata, payload: candidate })
      latestVersion = version
    },
    requests: () => requests.slice(),
    clearRequests: () => { requests.length = 0 },
    close: listener.close,
  })
}

export function createTauriUpdateSigner(options) {
  const privateKeyPath = options?.privateKeyPath
  if (typeof privateKeyPath !== 'string' || !isAbsolute(privateKeyPath) || !existsSync(privateKeyPath)) {
    throw new Error('Tauri updater 私钥必须是存在的绝对路径')
  }
  if (typeof options.password !== 'string') {
    throw new Error('Tauri updater signer 必须显式提供密码；无密码时传空字符串')
  }
  const cliPath = options.cliPath ?? resolve('node_modules/@tauri-apps/cli/tauri.js')
  if (!isAbsolute(cliPath) || !existsSync(cliPath)) throw new Error(`Tauri CLI 不存在：${cliPath}`)

  return async (payload) => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-e2e-updater-sign-'))
    const candidatePath = join(root, 'candidate.exe')
    writeFileSync(candidatePath, payload)
    try {
      await execFileAsync(process.execPath, [
        cliPath,
        'signer',
        'sign',
        '--private-key-path',
        privateKeyPath,
        candidatePath,
      ], {
        env: {
          ...process.env,
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: options.password,
        },
        timeout: 120_000,
        windowsHide: true,
        maxBuffer: 1024 * 1024,
      })
      const signaturePath = `${candidatePath}.sig`
      if (!existsSync(signaturePath)) throw new Error('Tauri signer 未生成 .sig 文件')
      return readFileSync(signaturePath, 'utf8').trim()
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  }
}

function validateVersion(version) {
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(version)) {
    throw new Error(`应用更新版本无效：${String(version)}`)
  }
}

function sendJson(response, value) {
  const body = Buffer.from(JSON.stringify(value))
  response.writeHead(200, {
    'content-length': String(body.length),
    'content-type': 'application/json',
  }).end(body)
}
