export type RuntimeCapabilityReasonCode = 'MISSING_PACKAGE_JSON' | 'PACKAGE_PATH_INVALID' | 'MANIFEST_INVALID' | 'MANIFEST_NAME_INVALID' | 'VERSION_MISMATCH' | 'TYPE_INVALID' | 'LICENSE_INVALID' | 'EXPORTS_INVALID' | 'ENTRYPOINT_INVALID' | 'BUNDLE_PATCH_INVALID'
export type RuntimeEntrypoints = { readonly [key: string]: string | { readonly default: string; readonly types: string } }
export type RuntimeCompatiblePackageRecord = { name: string; observedVersion: string; status: 'compatible'; entrypoints: RuntimeEntrypoints; bundlePatch?: './cordis.patch.yml' }
export type RuntimeMissingPackageRecord = { name: string; observedVersion: null; status: 'missing'; entrypoints: {}; reasonCode: RuntimeCapabilityReasonCode }
export type RuntimeIncompatiblePackageRecord = { name: string; observedVersion: string | null; status: 'incompatible'; entrypoints: {}; reasonCode: RuntimeCapabilityReasonCode }
export type RuntimePackageRecord = RuntimeCompatiblePackageRecord | RuntimeMissingPackageRecord | RuntimeIncompatiblePackageRecord

export interface RuntimeCapabilityReport {
  schemaVersion: 1
  packages: RuntimePackageRecord[]
  capabilities: Record<'apiProvider' | 'skill' | 'mcp', { package: string; available: boolean }>
  profileBundles?: string[]
}

export const CAPABILITY_REPORT_SCHEMA_VERSION: 1
export const PROFILE_BUNDLES: readonly string[]
export const CAPABILITY_REASON_CODES: readonly RuntimeCapabilityReasonCode[]
export function inspectRuntimeCapabilities(runtimeRoot: string, expected: { dshVersion: string; desktopPluginVersion: string }): RuntimeCapabilityReport
export function assertRuntimeCapabilities(report: RuntimeCapabilityReport, expected: { dshVersion: string; desktopPluginVersion: string }): RuntimeCapabilityReport
export interface RuntimePathImplementation {
  resolve(...paths: string[]): string
  relative(from: string, to: string): string
  isAbsolute(path: string): boolean
  sep: string
}
export function isFileWithin(directory: string, relativePath: unknown, options?: {
  pathImplementation?: RuntimePathImplementation
  statSync?: (path: string) => { isFile(): boolean }
  lstatSync?: (path: string) => { isSymbolicLink(): boolean }
  realpathSync?: (path: string) => string
}): boolean
