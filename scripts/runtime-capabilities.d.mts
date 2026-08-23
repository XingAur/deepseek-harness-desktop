export interface RuntimePackageRecord {
  name: string
  observedVersion: string | null
  status: 'compatible' | 'missing' | 'incompatible'
  entrypoints: Record<string, unknown>
  bundlePatch?: string
  reason?: string
}

export interface RuntimeCapabilityReport {
  schemaVersion: 1
  packages: RuntimePackageRecord[]
  capabilities: Record<'apiProvider' | 'skill' | 'mcp', { package: string; available: boolean }>
  profileBundles?: string[]
}

export const CAPABILITY_REPORT_SCHEMA_VERSION: 1
export const PROFILE_BUNDLES: readonly string[]
export function inspectRuntimeCapabilities(runtimeRoot: string, expected: { dshVersion: string; desktopPluginVersion: string }): RuntimeCapabilityReport
export function assertRuntimeCapabilities(report: RuntimeCapabilityReport): RuntimeCapabilityReport
export interface RuntimePathImplementation {
  resolve(...paths: string[]): string
  relative(from: string, to: string): string
  isAbsolute(path: string): boolean
  sep: string
}
export function isFileWithin(directory: string, relativePath: unknown, options?: {
  pathImplementation?: RuntimePathImplementation
  statSync?: (path: string) => { isFile(): boolean }
}): boolean
