import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { loadReleaseVersions } from './release-versions.mjs'
import { runtimeReleaseAssetNames } from './runtime-release-manifest.mjs'

// 发布桌面版之前,校验被钉住的 runtime release 内打包的桌面插件与当前仓库一致,
// 防止 CI 复用旧运行时导致用户侧拿到旧插件(0.1.37 事故)。任一 target 不一致即失败,
// 并给出"请升 runtimeVersion"的指引;release 尚不存在时视为通过(新运行时会被构建)。

export const SUPPORTED_TARGETS = ['windows-x86_64', 'darwin-aarch64']

export function compareManifest(expectedSha, manifest) {
  const actual = manifest && typeof manifest === 'object' ? manifest.desktopPluginSha256 : undefined
  if (typeof actual !== 'string' || actual === '') {
    return { ok: false, reason: 'stale-manifest' }
  }
  if (actual.toLowerCase() !== expectedSha.toLowerCase()) {
    return { ok: false, reason: 'plugin-drift', expected: expectedSha, actual }
  }
  return { ok: true }
}

export function sha256File(path) {
  // 与 build-runtime.mjs 计算打包 tgz 指纹同算法:对文件字节流取 SHA-256。
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

// 重建当前仓库的桌面插件 tgz 指纹:plugin:build + npm pack 到临时目录后对 tgz 取哈希。
// runNpmCommand 可注入,便于单测跳过真实 npm 调用。
export function resolveExpectedSha(repositoryRoot, runNpmCommand = runNpm) {
  const packDirectory = mkdtempSync(join(tmpdir(), 'dsh-plugin-pack-'))
  try {
    runNpmCommand(repositoryRoot, ['run', 'plugin:build'])
    runNpmCommand(repositoryRoot, ['pack', './packages/dsh-plugin-desktop', '--pack-destination', packDirectory])
    const tarballName = readdirSync(packDirectory).find((file) => file.endsWith('.tgz'))
    if (!tarballName) throw new Error('npm pack 未产出桌面插件 tgz')
    return sha256File(join(packDirectory, tarballName))
  } finally {
    rmSync(packDirectory, { recursive: true, force: true })
  }
}

function runNpm(cwd, commandArgs) {
  // Windows 不能直接 spawn npm(.cmd 需 shell 且新版 Node 禁止),复用 build-runtime.mjs
  // 的 npm-cli.js 直跑写法。
  let executable = 'npm'
  let prefix = []
  let shell = false
  if (process.platform === 'win32') {
    const cli = join(dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js')
    if (existsSync(cli)) {
      executable = process.execPath
      prefix = [cli]
    } else {
      executable = 'npm.cmd'
      shell = true
    }
  }
  const result = spawnSync(executable, [...prefix, ...commandArgs], { cwd, stdio: 'inherit', shell })
  if (result.status !== 0) {
    throw new Error(`npm ${commandArgs.join(' ')} failed with ${result.status ?? result.error?.message ?? 'unknown error'}`)
  }
}

function ghApi(args) {
  const result = spawnSync('gh', ['api', ...args], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
  if (result.error?.code === 'ENOENT') return { status: 'gh-missing' }
  if (result.status !== 0) {
    throw new Error(`gh api 失败: ${(result.stderr ?? '').trim() || result.error?.message || '未知错误'}`)
  }
  return { status: 'ok', value: result.stdout }
}

// 返回 { status: 'found', manifest } | { status: 'missing' }。
// 优先 gh api:列表接口在带 token 时可见 draft release,避免"草稿运行时被跳过校验后复用"。
// gh 不可用时回退到公开的 release download 直链(看不到 draft,但本地开发场景足够)。
// ghApiFn 可注入,便于单测覆盖 shell 逻辑而不触网。
export async function loadPublishedManifest({ repository, runtimeVersion, target }, ghApiFn = ghApi) {
  const tagName = `runtime-v${runtimeVersion}`
  const { manifestName } = runtimeReleaseAssetNames(target)

  const releases = ghApiFn([`repos/${repository}/releases?per_page=100`])
  if (releases.status === 'ok') {
    let parsed
    try {
      parsed = JSON.parse(releases.value)
    } catch {
      throw new Error('gh api 返回的 release 列表不是有效 JSON')
    }
    if (!Array.isArray(parsed)) throw new Error('gh api 返回的 release 列表格式无效')
    const release = parsed.find((item) => item?.tag_name === tagName)
    if (!release) return { status: 'missing' }
    const assets = Array.isArray(release.assets) ? release.assets : []
    const asset = assets.find((item) => item?.name === manifestName)
    if (!asset || asset.id === undefined) return { status: 'missing' }
    const body = ghApiFn(['-H', 'Accept: application/octet-stream', `repos/${repository}/releases/assets/${asset.id}`])
    if (body.status !== 'ok') return { status: 'missing' }
    return { status: 'found', manifest: parseManifestJson(body.value, `${tagName}/${manifestName}`) }
  }

  const url = `https://github.com/${repository}/releases/download/${tagName}/${manifestName}`
  const response = await fetch(url, { redirect: 'follow' })
  if (response.status === 404) return { status: 'missing' }
  if (!response.ok) throw new Error(`下载 ${url} 失败: HTTP ${response.status}`)
  return { status: 'found', manifest: parseManifestJson(await response.text(), url) }
}

function parseManifestJson(text, label) {
  let value
  try {
    value = JSON.parse(text)
  } catch {
    throw new Error(`${label} 不是有效的 JSON 清单`)
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} 不是 JSON 对象`)
  }
  return value
}

function resolveRepository() {
  if (process.env.GITHUB_REPOSITORY) return process.env.GITHUB_REPOSITORY
  const remote = spawnSync('git', ['config', '--get', 'remote.origin.url'], { encoding: 'utf8' })
  const url = remote.status === 0 ? remote.stdout.trim() : ''
  const match = /github\.com[:/](.+?)(?:\.git)?$/.exec(url)
  if (!match) throw new Error('无法确定 GitHub 仓库:请设置 GITHUB_REPOSITORY=<owner>/<repo>')
  return match[1]
}

function cliTargets(argv) {
  const targets = new Set()
  for (const argument of argv) {
    const match = /^--target=(.+)$/.exec(argument)
    if (!match) throw new Error(`无效参数: ${argument}(仅支持 --target=windows-x86_64 或 --target=darwin-aarch64)`)
    if (!SUPPORTED_TARGETS.includes(match[1])) throw new Error(`不支持的 Runtime target: ${match[1]}`)
    targets.add(match[1])
  }
  return targets.size > 0 ? [...targets] : [...SUPPORTED_TARGETS]
}

function reportFailure(tag, target, verdict) {
  if (verdict.reason === 'stale-manifest') {
    process.stderr.write(`::error::运行时 ${tag}(${target})的清单缺少 desktopPluginSha256 字段:该运行时发布于插件指纹校验引入之前,内部打包的桌面插件可能已过时。运行时 ${tag} 内的插件已过时:请升 release/versions.json 的 runtimeVersion 后重发。\n`)
    return
  }
  process.stderr.write(`::error::运行时 ${tag}(${target})打包的桌面插件 sha256=${verdict.actual} 与当前仓库插件 sha256=${verdict.expected} 不一致。运行时 ${tag} 内的插件已过时:请升 release/versions.json 的 runtimeVersion 后重发。\n`)
}

async function main() {
  const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const targets = cliTargets(process.argv.slice(2))
  const versions = loadReleaseVersions(repositoryRoot)
  const repository = resolveRepository()
  const tag = `runtime-v${versions.runtimeVersion}`
  const expectedSha = resolveExpectedSha(repositoryRoot)
  process.stdout.write(`当前仓库桌面插件 tgz sha256: ${expectedSha}\n`)
  process.stdout.write(`校验 ${repository} ${tag} 内的桌面插件指纹(target: ${targets.join(', ')})...\n`)

  let verified = 0
  let failed = false
  for (const target of targets) {
    const published = await loadPublishedManifest({ repository, runtimeVersion: versions.runtimeVersion, target })
    if (published.status === 'missing') {
      process.stdout.write(`- ${target}: ${tag} 尚未发布或缺少 ${runtimeReleaseAssetNames(target).manifestName},跳过校验(本次发布会构建新运行时,不会复用旧插件)。\n`)
      continue
    }
    const verdict = compareManifest(expectedSha, published.manifest)
    if (verdict.ok) {
      verified += 1
      process.stdout.write(`- ${target}: 插件指纹一致(${expectedSha})。\n`)
    } else {
      failed = true
      reportFailure(tag, target, verdict)
    }
  }

  if (failed) {
    process.exitCode = 1
  } else if (verified > 0) {
    process.stdout.write(`运行时插件指纹校验通过: ${verified}/${targets.length} 个 target 复用已验证的运行时。\n`)
  } else {
    process.stdout.write('无需校验已发布运行时(全部缺失,将构建新运行时)。\n')
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  main().catch((cause) => {
    process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`)
    process.exitCode = 1
  })
}
