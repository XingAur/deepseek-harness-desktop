import { resolve } from 'node:path'

const desktopVersionPattern = /^(\d+)\.(\d+)\.(\d+)$/

export function resolveRuntimeVersion(override, releaseRuntimeVersion) {
  if (typeof override === 'string' && override.trim() !== '') return override
  if (typeof releaseRuntimeVersion !== 'string' || releaseRuntimeVersion.trim() === '') throw new Error('Runtime 版本不能为空')
  return releaseRuntimeVersion
}

function parseDesktopVersion(desktopVersion) {
  const match = typeof desktopVersion === 'string' ? desktopVersion.match(desktopVersionPattern) : null
  if (!match) throw new Error(`桌面版本不是三段数字 SemVer：${String(desktopVersion)}`)

  return match.slice(1).map((segment) => BigInt(segment))
}

export function deriveBaselineVersion(desktopVersion) {
  const [major, minor, patch] = parseDesktopVersion(desktopVersion)
  if (patch > 0n) return `${major}.${minor}.${patch - 1n}`
  if (minor > 0n) return `${major}.${minor - 1n}.65535`
  if (major > 0n) return `${major - 1n}.65535.65535`
  throw new Error(`无法派生更低的基线版本：${desktopVersion}`)
}

export function createInstallerBuildPlan({ mode, candidateVersion, artifactsRoot }) {
  if (mode !== 'quick' && mode !== 'full') throw new Error(`安装包构建模式无效：${String(mode)}`)

  parseDesktopVersion(candidateVersion)
  const root = resolve(artifactsRoot)
  const names = mode === 'quick' ? ['candidate'] : ['baseline', 'candidate']
  const versions = mode === 'quick' ? [candidateVersion] : [deriveBaselineVersion(candidateVersion), candidateVersion]
  const variants = names.map((name, index) => ({
    name,
    version: versions[index],
    configPath: resolve(root, `tauri-${name}.json`),
    installerPath: resolve(root, `DeepSeek-Harness-Desktop-E2E-${name}-x64.exe`),
  }))

  return { mode, candidateVersion, variants }
}
