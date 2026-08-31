export const HARNESS_CORE_VENDOR_DIRS: string[]
export const HARNESS_CORE_VENDOR_FILES: string[]
export const HARNESS_CORE_REQUIRED_PATHS: string[]
export const HARNESS_COMPATIBILITY_SKILLS: string[]
export const VENDOR_MANIFEST_NAME: string
export function applyHarnessCoreCompatibilityPatches(target: string): string[]
export function copyHarnessCompatibilitySkills(
  compatibilityRoot: string,
  target: string,
  skillNames?: string[],
): number
export function isVendorablePath(relativePath: string): boolean
export function summarizeHarnessCore(source: string): {
  fileCount: number
  totalBytes: number
  manifestSha256: string
}
export function copyHarnessCore(source: string, target: string, options?: { preserve?: string[] }): {
  fileCount: number
  totalBytes: number
  manifestSha256: string
}
export function verifyHarnessCoreLayout(target: string): void
export function isSecretAssignment(text: string): boolean
export function verifyVendorNoSecrets(target: string): string[]
export function writeVendorManifest(
  target: string,
  summary: {
    source: string
    fileCount: number
    totalBytes: number
    manifestSha256: string
    compatibilityPatches?: string[]
  },
): {
  schema: string
  source: string
  syncedAt: string
  compatibilityPatches: string[]
  fileCount: number
  totalBytes: number
  manifestSha256: string
}
export function resolveHarnessCoreSource(repositoryRoot: string, explicit?: string): string
export function syncVendorFromSource(
  repositoryRoot: string,
  options?: { source?: string },
): {
  synced: boolean
  changed: boolean
  source: string
  reason?: 'disabled' | 'source-unavailable' | 'source-is-vendor'
  fileCount?: number
  compatibilityPatches?: string[]
  warnings?: string[]
}
