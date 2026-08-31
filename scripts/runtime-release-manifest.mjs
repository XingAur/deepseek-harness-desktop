import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, writeFileSync } from 'node:fs'
import { basename, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadReleaseVersions } from './release-versions.mjs'

const exactSemVer = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/

export function runtimeReleaseAssetNames(target) {
  if (target === 'windows-x86_64') {
    return { archiveName: 'dsh-runtime-windows-x86_64.zip', manifestName: 'runtime-windows-x86_64.json' }
  }
  if (target === 'darwin-aarch64') {
    return { archiveName: 'dsh-runtime-darwin-aarch64.tar.gz', manifestName: 'runtime-darwin-aarch64.json' }
  }
  throw new Error(`不支持的 Runtime target: ${String(target)}`)
}

export function createUnsignedRuntimeManifest({ archivePath, target, version, url, dshVersion, desktopPluginSha256 }) {
  const { archiveName } = runtimeReleaseAssetNames(target)
  requireSemVer(version, 'Runtime version')
  const resolvedDshVersion = dshVersion ?? loadReleaseVersions().dshVersion
  requireSemVer(resolvedDshVersion, 'DSH version')
  const archive = resolveRequiredPath(archivePath, 'archivePath')
  if (basename(archive) !== archiveName) throw new Error(`Runtime archive 必须命名为 ${archiveName}`)
  const stat = lstatSync(archive)
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size === 0) {
    throw new Error('Runtime archive 必须是非空普通文件')
  }
  validateArchiveUrl(url, archiveName)
  // 从归档重建清单时(archive 存在而清单缺失)无法得知打包插件的指纹,允许省略;
  // 省略后 check-runtime-plugin-currency.mjs 会按 stale-manifest 拒绝复用该运行时。
  const resolvedPluginSha256 = requirePluginSha256(desktopPluginSha256)
  const bytes = readFileSync(archive)
  return {
    schemaVersion: 1,
    version,
    dshVersion: resolvedDshVersion,
    target,
    url,
    size: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
    ...(resolvedPluginSha256 === undefined ? {} : { desktopPluginSha256: resolvedPluginSha256 }),
    archive: target === 'windows-x86_64' ? 'zip' : 'tar-gz',
    entrypoint: target === 'windows-x86_64' ? 'node.exe' : 'bin/node',
    args: ['app/launcher.mjs', '--port', '{port}'],
    healthPath: '/__desktop/health',
    signature: '',
  }
}

function requirePluginSha256(value) {
  if (value === undefined) return undefined
  if (typeof value !== 'string' || !/^[0-9a-fA-F]{64}$/.test(value)) {
    throw new Error('desktopPluginSha256 必须是 64 位十六进制 SHA-256')
  }
  return value.toLowerCase()
}

export function writeUnsignedRuntimeManifest({ outputPath, ...options }) {
  const output = resolveRequiredPath(
    outputPath ?? resolve(dirname(resolveRequiredPath(options.archivePath, 'archivePath')), `manifest-${options.target}.unsigned.json`),
    'outputPath',
  )
  const manifest = createUnsignedRuntimeManifest(options)
  writeFileSync(output, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  return { outputPath: output, manifest }
}

function validateArchiveUrl(value, archiveName) {
  if (typeof value !== 'string' || value.trim() !== value) throw new Error('Runtime URL 必须是无首尾空格的字符串')
  let url
  try {
    url = new URL(value)
  } catch {
    throw new Error('Runtime URL 无效')
  }
  if (!['https:', 'file:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('Runtime URL 必须是无凭证、查询参数和片段的 HTTPS 或 file URL')
  }
  if (url.protocol === 'https:' && (!url.hostname || url.port)) throw new Error('Runtime HTTPS URL 主机或端口无效')
  const encodedName = url.pathname.split('/').at(-1) ?? ''
  let urlName
  try {
    urlName = decodeURIComponent(encodedName)
  } catch {
    throw new Error('Runtime URL 文件名编码无效')
  }
  if (urlName !== archiveName) throw new Error(`Runtime URL 必须指向 ${archiveName}`)
}

function requireSemVer(value, label) {
  if (typeof value !== 'string' || value.trim() !== value || !exactSemVer.test(value)) {
    throw new Error(`${label} 必须是精确 SemVer`)
  }
}

function resolveRequiredPath(value, label) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${label} 不能为空`)
  return resolve(value)
}

function cliOptions() {
  const allowed = new Set(['archive', 'target', 'version', 'url', 'output', 'desktop-plugin-sha256'])
  const options = {}
  for (const argument of process.argv.slice(2)) {
    const match = /^--([^=]+)=(.+)$/.exec(argument)
    if (!match || !allowed.has(match[1]) || Object.hasOwn(options, match[1])) {
      throw new Error(`无效或重复参数: ${argument}`)
    }
    options[match[1]] = match[2]
  }
  for (const required of ['archive', 'target', 'version', 'url', 'output']) {
    if (!options[required]) throw new Error(`缺少 --${required}=<值>`)
  }
  return options
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try {
    const options = cliOptions()
    const result = writeUnsignedRuntimeManifest({
      archivePath: options.archive,
      target: options.target,
      version: options.version,
      url: options.url,
      outputPath: options.output,
      ...(options['desktop-plugin-sha256'] ? { desktopPluginSha256: options['desktop-plugin-sha256'] } : {}),
    })
    process.stdout.write(`${JSON.stringify(result)}\n`)
  } catch (cause) {
    process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`)
    process.exitCode = 1
  }
}
