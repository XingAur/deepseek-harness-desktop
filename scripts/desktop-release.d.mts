import type { ReleaseVersions } from './release-versions.mjs'

export const productionRepository: 'XingAur/deepseek-harness-desktop'

export interface DesktopReleaseAssets {
  windowsInstaller: string
  windowsUpdater: string
  windowsSignature: string
  macDmg: string
  windowsInstallerName: string
  windowsUpdaterName: string
  windowsSignatureName: string
  macDmgName: string
  windowsInstallerPath: string
  windowsUpdaterPath: string
  windowsSignaturePath: string
  macDmgPath: string
  signature: string
}

export function verifyDesktopReleaseAssets(options: {
  assetDirectory: string
  versions: ReleaseVersions
}): DesktopReleaseAssets

export function generateDesktopRelease(options: {
  assetDirectory: string
  outputDirectory: string
  repository: string
  publishedAt: string
  notes: string
  versions: ReleaseVersions
}): {
  latestPath: string
  manifestPath: string
  uploadableAssets: string[]
}
