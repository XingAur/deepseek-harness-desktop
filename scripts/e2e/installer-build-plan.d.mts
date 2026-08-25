export type InstallerBuildMode = 'quick' | 'full'

export interface InstallerVariant {
  name: 'baseline' | 'candidate'
  version: string
  configPath: string
  installerPath: string
}

export interface InstallerBuildPlan {
  mode: InstallerBuildMode
  candidateVersion: string
  variants: InstallerVariant[]
}

export function deriveBaselineVersion(desktopVersion: string): string
export function resolveRuntimeVersion(override: string | undefined, releaseRuntimeVersion: string): string
export function createInstallerBuildPlan(options: {
  mode: InstallerBuildMode
  candidateVersion: string
  artifactsRoot: string
}): InstallerBuildPlan
