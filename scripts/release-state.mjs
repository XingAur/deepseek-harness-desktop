import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { LEGACY_RELEASE_BASELINE } from './release-versions.mjs'

const stableVersion = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/

export function classifyReleaseState({ version, legacyReleaseBaseline, tagExists, release }) {
  requireStableVersion(version, 'version')
  requireStableVersion(legacyReleaseBaseline, 'legacyReleaseBaseline')
  if (legacyReleaseBaseline !== LEGACY_RELEASE_BASELINE) {
    throw new Error(`legacyReleaseBaseline 必须固定为 ${LEGACY_RELEASE_BASELINE}`)
  }
  if (typeof tagExists !== 'boolean') throw new Error('tagExists 必须是布尔值')
  validateRelease(release)

  if (!tagExists && release !== null) throw new Error('tag 不存在时不能存在 Release')
  if (!tagExists) return state('pending-tag', '当前版本 tag 不存在，需要先补推 tag')
  if (release === null || release.isDraft) {
    return state('pending-release', release?.isDraft ? '当前 Release 仍是草稿，需要重新调度发布' : '当前版本尚无 Release，需要调度发布')
  }
  if (version === legacyReleaseBaseline || release.assets.some((asset) => asset.name === 'desktop-release.json')) {
    return state('complete', version === legacyReleaseBaseline ? '历史基线 Release 已公开' : '公开 Release 含完成标记')
  }
  return state('blocked', '公开 Release 缺少 desktop-release.json，禁止自动覆盖或继续升版')
}

function validateRelease(release) {
  if (release === null) return
  if (typeof release !== 'object' || Array.isArray(release)) throw new Error('release 必须是对象或 null')
  if (typeof release.isDraft !== 'boolean' || !Array.isArray(release.assets)) {
    throw new Error('release 必须包含 isDraft 和 assets')
  }
  for (const asset of release.assets) {
    if (typeof asset !== 'object' || asset === null || typeof asset.name !== 'string') {
      throw new Error('release asset 必须包含 name')
    }
  }
}

function requireStableVersion(value, label) {
  if (typeof value !== 'string' || !stableVersion.test(value)) throw new Error(`${label} 必须是稳定 SemVer`)
}

function state(status, reason) {
  return { status, reason }
}

function argument(name) {
  const prefix = `--${name}=`
  const match = process.argv.slice(2).find((value) => value.startsWith(prefix))
  if (!match) throw new Error(`缺少 ${prefix}<值>`)
  return match.slice(prefix.length)
}

export function parseBooleanArgument(value, label = 'value') {
  if (value === 'true') return true
  if (value === 'false') return false
  throw new Error(`${label} 必须是 true 或 false`)
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try {
    const releasePath = argument('release-json-file')
    const release = JSON.parse(readFileSync(resolve(releasePath), 'utf8'))
    const result = classifyReleaseState({
      version: argument('version'),
      legacyReleaseBaseline: argument('legacy-release-baseline'),
      tagExists: parseBooleanArgument(argument('tag-exists'), 'tag-exists'),
      release,
    })
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
  } catch (cause) {
    process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`)
    process.exitCode = 1
  }
}
