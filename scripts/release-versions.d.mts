export interface ReleaseVersions {
  schemaVersion: 1
  desktopVersion: string
  runtimeVersion: string
  dshVersion: string
  nodeVersion: string
  pnpmVersion: string
  legacyReleaseBaseline: string
}

export const RELEASE_VERSIONS_PATH: 'release/versions.json'
export const LEGACY_RELEASE_BASELINE: '0.1.12'
export function validateReleaseVersions(value: unknown): ReleaseVersions
export function loadReleaseVersions(root?: string): ReleaseVersions
export function assertReleaseVersionConsistency(root?: string): ReleaseVersions
