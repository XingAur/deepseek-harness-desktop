import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { loadReleaseVersions } from './release-versions.mjs'
import { runtimeReleaseAssetNames } from './runtime-release-manifest.mjs'

// 发布桌面版之前,校验将被复用的 runtime release 内打包的桌面插件与当前仓库一致,
// 防止 CI 复用旧运行时导致用户侧拿到旧插件(0.1.37 事故)。manifest 缺少插件指纹
// 或指纹不一致时,提示必须 bump release/versions.json 的 runtimeVersion 重新发布;
// runtime release 尚不存在时视为通过(本次发布会构建新运行时,不会复用旧插件)。

const repositoryRoot = () => resolve(dirname(fileURLToPath(import.meta.url)), '..')

export function sha256File(path) {
  // 与 build-runtime.mjs 计算打包 tgz 指纹同算法:对文件字节流取 SHA-256。
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

// 重建当前仓库的桌面插件 tgz 指纹:plugin:build + npm pack 到临时目录后对 tgz 取哈希。
// 与 --dependency-cache 复用路径无关(那条路径不经过 npm pack)。runNpmCommand 可注入,
// 便于单测跳过真实 npm 调用。
export function currentPluginSha256(root = repositoryRoot(), runNpmCommand = runNpm) {
  const packDirectory = mkdtempSync(join(tmpdir(), 'dsh-plugin-pack-'))
  try {
    runNpmCommand(root, ['run', 'plugin:build'])
    runNpmCommand(root, ['pack', './packages/dsh-plugin-desktop', '--pack-destination', packDirectory])
    const tarballName = readdirSync(packDirectory).find((file) => file.endsWith('.tgz'))
    if (!tarballName) throw new Error('npm pack 未产出桌面插件 tgz')
    return sha256File(join(packDirectory, tarballName))
  } finally {
    rmSync(packDirectory, { recursive: true, force: true })
  }
}

// 比对已发布 runtime manifest 内的插件指纹与当前仓库指纹。
// 缺字段 → runtime-manifest-stale(该运行时发布于指纹机制引入之前,必属旧插件);
// 不一致 → plugin-drift。两种情况都要求 bump runtimeVersion,因为 runtime release 资产不可变。
export function compareManifest(currentSha, manifest, runtimeTag) {
  const publishedSha = manifest && typeof manifest === 'object' ? manifest.desktopPluginSha256 : undefined
  if (typeof publishedSha !== 'string' || publishedSha.trim() === '') {
    return {
      ok: false,
      reason: 'runtime-manifest-stale',
      message: `运行时 ${runtimeTag} 的 manifest 缺少插件 sha(运行时过旧),必须 bump runtimeVersion 重新发布`,
    }
  }
  if (publishedSha.toLowerCase() !== String(currentSha).toLowerCase()) {
    return {
      ok: false,
      reason: 'plugin-drift',
      message: `运行时 ${runtimeTag} 内插件与当前仓库不一致:manifest=${publishedSha} 当前=${currentSha},必须 bump runtimeVersion`,
    }
  }
  return { ok: true }
}

const sleep = (ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms))
const MAX_RELEASE_PAGES = 100
const REQUEST_TIMEOUT_MS = 15_000
const runtimeTagPattern = /^runtime-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/

// 拉取可被工作流复用的 runtime manifest;release 或 manifest 资产不存在时返回 null(将构建新运行时)。
// 先用带认证的 GitHub Release 列表 API 校验精确 tag 的 archive/manifest 资产矩阵,再下载 manifest;
// 公开下载 URL 看不到 draft Release 时回退固定 GitHub asset API,以与 `gh release view/download` 的复用可见性一致。
// 网络错误与 5xx 最多重试 3 次,指数退避 1s/2s/4s。
export async function fetchRuntimeManifest(target, runtimeTag, token, options = {}) {
  const {
    repository = resolveRepository(),
    retryDelayMs = 1000,
    sleepImpl = sleep,
    fetchImpl = fetch,
    maxReleasePages = MAX_RELEASE_PAGES,
    requestTimeoutMs = REQUEST_TIMEOUT_MS,
  } = options
  const { repositoryPath } = validateReleaseLocator(repository, runtimeTag)
  validateRequestLimits(maxReleasePages, requestTimeoutMs)
  const { archiveName, manifestName } = runtimeReleaseAssetNames(target)
  const requestOptions = { retryDelayMs, sleepImpl, fetchImpl, maxReleasePages, requestTimeoutMs }
  const manifestAsset = await fetchManifestFromReleaseList(
    repositoryPath,
    runtimeTag,
    archiveName,
    manifestName,
    token,
    requestOptions,
  )
  if (!manifestAsset) return null
  return fetchManifestAsset(`Runtime Release ${runtimeTag}`, manifestAsset, repositoryPath, manifestName, token, requestOptions)
}

async function fetchManifestFromReleaseList(repositoryPath, runtimeTag, archiveName, manifestName, token, options) {
  const pageSize = 100
  const { maxReleasePages } = options
  const releaseHeaders = {
    Accept: 'application/vnd.github+json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
  const seenPageFingerprints = new Set()
  const matchingReleases = []
  let reachedEnd = false
  for (let page = 1; page <= maxReleasePages; page += 1) {
    const releaseUrl = `https://api.github.com/repos/${repositoryPath}/releases?per_page=${pageSize}&page=${page}`
    const { response: releaseResponse, text } = await fetchWithRetries(releaseUrl, { headers: releaseHeaders }, options, '查询')
    if (!releaseResponse.ok) throw new Error(`查询 ${releaseUrl} 失败: HTTP ${releaseResponse.status}`)

    const releases = parseReleaseListJson(text, releaseUrl)
    const pageFingerprint = JSON.stringify(releases)
    if (seenPageFingerprints.has(pageFingerprint)) throw new Error(`${releaseUrl} Release 列表页面重复`)
    seenPageFingerprints.add(pageFingerprint)
    for (const candidate of releases) {
      if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate) || typeof candidate.tag_name !== 'string') {
        throw new Error(`${releaseUrl} 包含非法 Release 数据`)
      }
      if (candidate.tag_name === runtimeTag) {
        matchingReleases.push({ releaseUrl, release: candidate })
      }
    }
    if (releases.length < pageSize) {
      reachedEnd = true
      break
    }
  }
  if (!reachedEnd) throw new Error(`Release 列表超过最大页数(${maxReleasePages}),无法确认 ${runtimeTag} 是否存在`)
  if (matchingReleases.length === 0) return null
  if (matchingReleases.length > 1) throw new Error(`多个 Runtime Release 使用相同 tag: ${runtimeTag}`)
  const [{ releaseUrl, release }] = matchingReleases
  return inspectRuntimeRelease(releaseUrl, release, runtimeTag, archiveName, manifestName)
}

function inspectRuntimeRelease(releaseUrl, release, runtimeTag, archiveName, manifestName) {
  if (!Array.isArray(release.assets)) {
    throw new Error(`${releaseUrl} 的 ${runtimeTag} Release 缺少 assets 数组`)
  }
  let archiveAsset
  let manifestAsset
  for (const candidate of release.assets) {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate) || typeof candidate.name !== 'string') {
      throw new Error(`${releaseUrl} 的 ${runtimeTag} Release 包含非法 asset 数据`)
    }
    if (candidate.name === archiveName) archiveAsset = candidate
    if (candidate.name === manifestName) manifestAsset = candidate
  }
  if (!archiveAsset && !manifestAsset) return null
  if (archiveAsset && !manifestAsset) {
    throw new Error(`Runtime Release ${runtimeTag} 的 archive ${archiveName} 已存在但 manifest ${manifestName} 缺失;必须 bump runtimeVersion 或清理旧 draft asset`)
  }
  if (!archiveAsset && manifestAsset) {
    throw new Error(`Runtime Release ${runtimeTag} 的 manifest ${manifestName} 已存在但 archive ${archiveName} 缺失;该残缺 Release 不可复用`)
  }
  return manifestAsset
}

async function fetchManifestAsset(releaseUrl, manifestAsset, repositoryPath, manifestName, token, options) {
  if (!Number.isSafeInteger(manifestAsset.id) || manifestAsset.id <= 0) {
    throw new Error(`${releaseUrl} 的 ${manifestName} 资产具有无效的 asset id`)
  }
  // asset.url 来自远端数据,不可作为带 Bearer token 的请求目标。只使用已验证 id 构造 GitHub API 地址。
  const assetUrl = `https://api.github.com/repos/${repositoryPath}/releases/assets/${manifestAsset.id}`

  const { response: assetResponse, text } = await fetchWithRetries(assetUrl, {
    headers: {
      Accept: 'application/octet-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    redirect: 'follow',
  }, options, '下载')
  if (!assetResponse.ok) throw new Error(`下载 ${assetUrl} 失败: HTTP ${assetResponse.status}`)
  return parseManifestJson(text, assetUrl)
}

async function fetchWithRetries(url, init, { retryDelayMs, sleepImpl, fetchImpl, requestTimeoutMs }, action) {
  const maxRetries = 3
  for (let attempt = 0; ; attempt += 1) {
    let response
    let text
    try {
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(new Error(`请求超时(${requestTimeoutMs}ms)`)), requestTimeoutMs)
      try {
        response = await fetchImpl(url, { ...init, signal: controller.signal })
        if (response.ok) text = await response.text()
      } finally {
        clearTimeout(timeout)
      }
    } catch (cause) {
      if (attempt >= maxRetries) {
        throw new Error(`${action} ${url} 失败(已重试 ${maxRetries} 次): ${cause instanceof Error ? cause.message : String(cause)}`)
      }
      await sleepImpl(retryDelayMs * 2 ** attempt)
      continue
    }
    if (response.status >= 500) {
      if (attempt >= maxRetries) throw new Error(`${action} ${url} 失败: HTTP ${response.status}(已重试 ${maxRetries} 次)`)
      await sleepImpl(retryDelayMs * 2 ** attempt)
      continue
    }
    return { response, text }
  }
}

function validateReleaseLocator(repository, runtimeTag) {
  if (typeof repository !== 'string') throw new Error('无效的 GitHub 仓库')
  const segments = repository.split('/')
  if (segments.length !== 2 || !segments.every((segment) => /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(segment))) {
    throw new Error(`无效的 GitHub 仓库: ${repository}`)
  }
  if (typeof runtimeTag !== 'string' || !runtimeTagPattern.test(runtimeTag)) {
    throw new Error('Runtime tag 必须是固定的 runtime-v<semver>，不能使用 latest、branch 或 URL')
  }
  return {
    repositoryPath: segments.map((segment) => encodeURIComponent(segment)).join('/'),
  }
}

function validateRequestLimits(maxReleasePages, requestTimeoutMs) {
  if (!Number.isSafeInteger(maxReleasePages) || maxReleasePages <= 0) {
    throw new Error(`无效的 Release 最大页数: ${String(maxReleasePages)}`)
  }
  if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs <= 0) {
    throw new Error(`无效的请求超时: ${String(requestTimeoutMs)}`)
  }
}

function parseReleaseListJson(text, label) {
  let value
  try {
    value = JSON.parse(text)
  } catch {
    throw new Error(`${label} 不是有效的 JSON Release 列表`)
  }
  if (!Array.isArray(value)) throw new Error(`${label} Release 列表不是 JSON 数组`)
  return value
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

function resolveRepository(explicit) {
  if (explicit) return explicit
  if (process.env.GITHUB_REPOSITORY) return process.env.GITHUB_REPOSITORY
  // 本机直跑兜底:从 origin 远端推断 owner/repo(CI 一定有 GITHUB_REPOSITORY)。
  const remote = spawnSync('git', ['config', '--get', 'remote.origin.url'], { encoding: 'utf8' })
  const url = remote.status === 0 ? remote.stdout.trim() : ''
  const match = /github\.com[:/](.+?)(?:\.git)?$/.exec(url)
  if (!match) throw new Error('无法确定 GitHub 仓库:请用 --repo=<owner>/<repo> 或设置 GITHUB_REPOSITORY')
  return match[1]
}

function cliOptions() {
  const allowed = new Set(['target', 'runtime-tag', 'repo'])
  const options = {}
  for (const argument of process.argv.slice(2)) {
    const match = /^--([^=]+)=(.*)$/.exec(argument)
    if (!match || !allowed.has(match[1]) || Object.hasOwn(options, match[1])) {
      throw new Error(`无效或重复参数: ${argument}(仅支持 --target/--runtime-tag/--repo)`)
    }
    options[match[1]] = match[2]
  }
  if (!options.target) throw new Error('缺少 --target=<windows-x86_64|darwin-aarch64>')
  if (options.target !== 'windows-x86_64' && options.target !== 'darwin-aarch64') {
    throw new Error(`不支持的 Runtime target: ${options.target}`)
  }
  return options
}

async function main() {
  const root = repositoryRoot()
  const options = cliOptions()
  const target = options.target
  const runtimeTag = options['runtime-tag'] || `runtime-v${loadReleaseVersions(root).runtimeVersion}`
  const repository = resolveRepository(options.repo)
  const token = process.env.GH_TOKEN ?? process.env.GITHUB_TOKEN
  const currentSha = currentPluginSha256(root)
  process.stdout.write(`当前仓库桌面插件 tgz sha256: ${currentSha}\n`)
  process.stdout.write(`校验 ${repository} ${runtimeTag} 内的桌面插件指纹(target: ${target})...\n`)

  const manifest = await fetchRuntimeManifest(target, runtimeTag, token, { repository })
  if (!manifest) {
    process.stdout.write(`- ${target}: ${runtimeTag} 尚未发布或缺少 manifest,视为通过(本次发布会构建新运行时,不会复用旧插件)。\n`)
    return
  }
  const verdict = compareManifest(currentSha, manifest, runtimeTag)
  if (verdict.ok) {
    process.stdout.write(`- ${target}: 插件指纹一致(${currentSha}),可以复用该运行时。\n`)
    return
  }
  console.error(verdict.message)
  process.exitCode = 1
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  main().catch((cause) => {
    process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`)
    process.exitCode = 1
  })
}
