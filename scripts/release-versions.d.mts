export interface DshUpstreamSource {
  repository: string
  tag: string
  commit: string
}

export interface ReleaseVersions {
  schemaVersion: 2
  desktopVersion: string
  runtimeVersion: string
  dshVersion: string
  dshUpstream: DshUpstreamSource
  nodeVersion: string
  pnpmVersion: string
  legacyReleaseBaseline: string
}

export const RELEASE_VERSIONS_PATH: 'release/versions.json'
export const LEGACY_RELEASE_BASELINE: '0.1.12'
export const OFFICIAL_DSH_REPOSITORY: 'https://github.com/deepseek-ai/deepseek-harness.git'
export function validateReleaseVersions(value: unknown): ReleaseVersions
export function loadReleaseVersions(root?: string): ReleaseVersions
export function assertReleaseVersionConsistency(root?: string): ReleaseVersions
