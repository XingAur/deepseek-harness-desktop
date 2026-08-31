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

// 拉取已发布 runtime manifest;release 或 manifest 资产不存在时返回 null(将构建新运行时)。
// GitHub 偶发连接重置:网络错误与 5xx 最多重试 3 次,指数退避 1s/2s/4s;404 不重试。
export async function fetchRuntimeManifest(target, runtimeTag, token, options = {}) {
  const {
    repository = resolveRepository(),
    retryDelayMs = 1000,
    sleepImpl = sleep,
    fetchImpl = fetch,
  } = options
  const { manifestName } = runtimeReleaseAssetNames(target)
  const url = `https://github.com/${repository}/releases/download/${runtimeTag}/${manifestName}`
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  const maxRetries = 3
  for (let attempt = 0; ; attempt += 1) {
    let response
    try {
      // fetch 规范会在跨源重定向时剥掉 Authorization,跟随重定向不会把 token 泄给资产 CDN。
      response = await fetchImpl(url, { headers, redirect: 'follow' })
    } catch (cause) {
      if (attempt >= maxRetries) {
        throw new Error(`下载 ${url} 失败(已重试 ${maxRetries} 次): ${cause instanceof Error ? cause.message : String(cause)}`)
      }
      await sleepImpl(retryDelayMs * 2 ** attempt)
      continue
    }
    if (response.status === 404) return null
    if (response.status >= 500) {
      if (attempt >= maxRetries) throw new Error(`下载 ${url} 失败: HTTP ${response.status}(已重试 ${maxRetries} 次)`)
      await sleepImpl(retryDelayMs * 2 ** attempt)
      continue
    }
    if (!response.ok) throw new Error(`下载 ${url} 失败: HTTP ${response.status}`)
    return parseManifestJson(await response.text(), url)
  }
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
