const MAX_RELEASE_PAGES = 100
const REQUEST_TIMEOUT_MS = 15_000
const runtimeTagPattern = /^runtime-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/
const repositoryPattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
const sleep = (ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms))

// 供 desktop workflow 使用的 fail-closed Runtime Release 判定。只有认证列表成功扫描完、
// 无精确 tag 或唯一 Release 同时缺少两个目标资产时，才允许构建新 Runtime。
export async function resolveRuntimeReleaseState({
  repository,
  runtimeTag,
  archiveName,
  manifestName,
  token,
  fetchImpl = fetch,
  maxReleasePages = MAX_RELEASE_PAGES,
  retryDelayMs = 1000,
  sleepImpl = sleep,
  requestTimeoutMs = REQUEST_TIMEOUT_MS,
}) {
  if (typeof repository !== 'string' || !repositoryPattern.test(repository)) throw new Error('Repository 必须是 owner/repo')
  if (typeof runtimeTag !== 'string' || !runtimeTagPattern.test(runtimeTag)) {
    throw new Error('Runtime tag 必须是固定的 runtime-v<semver>')
  }
  if (typeof archiveName !== 'string' || archiveName === '' || typeof manifestName !== 'string' || manifestName === '') {
    throw new Error('Runtime archive 和 manifest 名称不能为空')
  }
  if (typeof token !== 'string' || token === '') throw new Error('必须提供 GitHub token 以检查 draft Runtime Release')
  if (!Number.isSafeInteger(maxReleasePages) || maxReleasePages <= 0) throw new Error('Runtime Release 最大页数无效')
  if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs <= 0) throw new Error('Runtime Release 请求超时无效')

  const headers = { Accept: 'application/vnd.github+json', Authorization: `Bearer ${token}` }
  const matchingReleases = []
  const fingerprints = new Set()
  let reachedEnd = false
  for (let page = 1; page <= maxReleasePages; page += 1) {
    const url = `https://api.github.com/repos/${repository}/releases?per_page=100&page=${page}`
    const { response, text } = await fetchTextWithRetries(url, { headers }, {
      fetchImpl,
      retryDelayMs,
      sleepImpl,
      requestTimeoutMs,
    })
    if (!response.ok) throw new Error(`查询 ${url} 失败: HTTP ${response.status}`)
    let releases
    try {
      releases = JSON.parse(text)
    } catch {
      throw new Error(`${url} 不是有效的 JSON Release 列表`)
    }
    if (!Array.isArray(releases)) throw new Error(`${url} Release 列表不是 JSON 数组`)
    const fingerprint = JSON.stringify(releases)
    if (fingerprints.has(fingerprint)) throw new Error(`${url} Release 列表页面重复`)
    fingerprints.add(fingerprint)
    for (const release of releases) {
      if (!release || typeof release !== 'object' || Array.isArray(release) || typeof release.tag_name !== 'string') {
        throw new Error(`${url} 包含非法 Release 数据`)
      }
      if (release.tag_name === runtimeTag) matchingReleases.push(release)
    }
    if (releases.length < 100) {
      reachedEnd = true
      break
    }
  }
  if (!reachedEnd) throw new Error(`Release 列表超过最大页数(${maxReleasePages})`)
  if (matchingReleases.length === 0) return { state: 'absent' }
  if (matchingReleases.length > 1) throw new Error(`多个 Runtime Release 使用相同 tag: ${runtimeTag}`)

  const [release] = matchingReleases
  if (!Array.isArray(release.assets)) throw new Error(`${runtimeTag} Release 缺少 assets 数组`)
  const archiveAssets = []
  const manifestAssets = []
  for (const asset of release.assets) {
    if (!asset || typeof asset !== 'object' || Array.isArray(asset) || typeof asset.name !== 'string') {
      throw new Error(`${runtimeTag} Release 包含非法 asset 数据`)
    }
    if (asset.name === archiveName) archiveAssets.push(asset)
    if (asset.name === manifestName) manifestAssets.push(asset)
  }
  if (archiveAssets.length > 1 || manifestAssets.length > 1) throw new Error(`${runtimeTag} Release 包含重复目标资产`)
  if (archiveAssets.length === 0 && manifestAssets.length === 0) return { state: 'empty' }
  if (archiveAssets.length === 0 || manifestAssets.length === 0) {
    throw new Error(`${runtimeTag} Release 的 archive/manifest 资产不完整;必须 bump runtimeVersion 或清理旧 draft asset`)
  }
  const [archiveAsset] = archiveAssets
  const [manifestAsset] = manifestAssets
  if (!Number.isSafeInteger(release.id) || release.id <= 0 || !Number.isSafeInteger(archiveAsset.id) || archiveAsset.id <= 0 || !Number.isSafeInteger(manifestAsset.id) || manifestAsset.id <= 0) {
    throw new Error(`${runtimeTag} Release 包含无效的 GitHub asset identity`)
  }
  return {
    state: 'complete',
    releaseId: release.id,
    archiveAssetId: archiveAsset.id,
    manifestAssetId: manifestAsset.id,
  }
}

async function fetchTextWithRetries(url, init, { fetchImpl, retryDelayMs, sleepImpl, requestTimeoutMs }) {
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
        throw new Error(`查询 ${url} 失败(已重试 ${maxRetries} 次): ${cause instanceof Error ? cause.message : String(cause)}`)
      }
      await sleepImpl(retryDelayMs * 2 ** attempt)
      continue
    }
    if (response.status >= 500) {
      if (attempt >= maxRetries) throw new Error(`查询 ${url} 失败: HTTP ${response.status}(已重试 ${maxRetries} 次)`)
      await sleepImpl(retryDelayMs * 2 ** attempt)
      continue
    }
    return { response, text }
  }
}

function cliOptions() {
  const allowed = new Set(['repo', 'tag', 'archive', 'manifest'])
  const options = {}
  for (const argument of process.argv.slice(2)) {
    const match = /^--([^=]+)=(.*)$/.exec(argument)
    if (!match || !allowed.has(match[1]) || Object.hasOwn(options, match[1])) throw new Error(`无效或重复参数: ${argument}`)
    options[match[1]] = match[2]
  }
  for (const name of allowed) if (!options[name]) throw new Error(`缺少 --${name}=...`)
  return options
}

if (process.argv[1]?.endsWith('runtime-release-state.mjs')) {
  const options = cliOptions()
  resolveRuntimeReleaseState({
    repository: options.repo,
    runtimeTag: options.tag,
    archiveName: options.archive,
    manifestName: options.manifest,
    token: process.env.GH_TOKEN ?? process.env.GITHUB_TOKEN,
  }).then((state) => {
    process.stdout.write(`${JSON.stringify(state)}\n`)
  }).catch((cause) => {
    process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`)
    process.exitCode = 1
  })
}
