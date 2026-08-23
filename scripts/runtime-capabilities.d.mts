export type RuntimeCapabilityReasonCode = 'MISSING_PACKAGE_JSON' | 'PACKAGE_PATH_INVALID' | 'MANIFEST_INVALID' | 'MANIFEST_NAME_INVALID' | 'VERSION_MISMATCH' | 'TYPE_INVALID' | 'LICENSE_INVALID' | 'EXPORTS_INVALID' | 'ENTRYPOINT_INVALID' | 'BUNDLE_PATCH_INVALID'
export type RuntimePackageName = '@deepseek-ai/dsh' | '@deepseek-ai/dsh-base' | '@deepseek-ai/dsh-web-app' | '@dsh/desktop-plugin' | '@deepseek-ai/dsh-llm-pi-ai' | '@deepseek-ai/dsh-skill' | '@deepseek-ai/dsh-mcp-client'

type ModuleEntrypoint = { default: './lib/index.js'; types: './lib/types/index.d.ts' }
type InvariantEntrypoint = { default: './lib/invariant.js'; types: './lib/types/invariant.d.ts' }
type DshEntrypoints = { bin: 'lib/bin.js' }
type BaseEntrypoints = { '.': ModuleEntrypoint; './invariant': InvariantEntrypoint; './cordis.patch.yml': './cordis.patch.yml'; './package.json': './package.json' }
type WebAppEntrypoints = BaseEntrypoints & { './startup': { default: './lib/startup.js'; types: './lib/types/startup.d.ts' } }
type DesktopPluginEntrypoints = { '.': './lib/index.js'; './client': './lib/client.js'; './package.json': './package.json' }
type OptionalEntrypoints = { '.': ModuleEntrypoint; './invariant': InvariantEntrypoint; './package.json': './package.json' }

type CompatibleRecord<Name extends RuntimePackageName, Entrypoints, Bundle extends boolean = false> = {
  name: Name
  observedVersion: string
  status: 'compatible'
  entrypoints: Entrypoints
} & (Bundle extends true ? { bundlePatch: './cordis.patch.yml' } : { bundlePatch?: never })
type FailedRecord<Name extends RuntimePackageName, Status extends 'missing' | 'incompatible'> = {
  name: Name
  observedVersion: Status extends 'missing' ? null : string | null
  status: Status
  entrypoints: Record<never, never>
  reasonCode: RuntimeCapabilityReasonCode
}

export type RuntimeCompatiblePackageRecord =
  | CompatibleRecord<'@deepseek-ai/dsh', DshEntrypoints>
  | CompatibleRecord<'@deepseek-ai/dsh-base', BaseEntrypoints, true>
  | CompatibleRecord<'@deepseek-ai/dsh-web-app', WebAppEntrypoints, true>
  | CompatibleRecord<'@dsh/desktop-plugin', DesktopPluginEntrypoints, true>
  | CompatibleRecord<'@deepseek-ai/dsh-llm-pi-ai' | '@deepseek-ai/dsh-skill' | '@deepseek-ai/dsh-mcp-client', OptionalEntrypoints>
export type RuntimeMissingPackageRecord = { [Name in RuntimePackageName]: FailedRecord<Name, 'missing'> }[RuntimePackageName]
export type RuntimeIncompatiblePackageRecord = { [Name in RuntimePackageName]: FailedRecord<Name, 'incompatible'> }[RuntimePackageName]
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
