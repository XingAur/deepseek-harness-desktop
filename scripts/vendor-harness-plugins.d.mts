export const HARNESS_PLUGIN_VENDOR_MANIFEST: string
export function applyHarnessPluginCompatibilityPatches(
  bundleRoot: string,
  inventoryPath: string,
): string[]
export function verifyHarnessPluginBundle(bundleRoot: string, inventoryPath: string): {
  pluginCount: number
  fileCount: number
  totalBytes: number
  manifestSha256: string
}
export function verifyCheckedInHarnessPluginBundle(
  bundleRoot: string,
): ReturnType<typeof verifyHarnessPluginBundle>
export function copyHarnessPluginBundle(options: {
  sources: Record<string, string>
  target: string
  inventoryPath: string
}): ReturnType<typeof verifyHarnessPluginBundle>
export function copyCheckedInHarnessPluginBundle(
  source: string,
  target: string,
  inventoryPath: string,
): ReturnType<typeof verifyHarnessPluginBundle>
export function writeFrozenPluginInventoryFromBundle(
  bundleRoot: string,
  inventoryPath: string,
): {
  schema_version: string
  plugins: Array<{
    name: string
    version: string
    capabilities_sha256: string
    capabilities: string[]
    sources_sha256: Record<string, string>
  }>
}
export function writeHarnessPluginVendorManifest(
  target: string,
  summary: ReturnType<typeof verifyHarnessPluginBundle>,
  sources?: Record<string, string>,
): object
export function syncHarnessPluginVendor(options: {
  sourceRoot: string
  target: string
  inventoryPath: string
}): ReturnType<typeof verifyHarnessPluginBundle> & {
  compatibilityPatches: string[]
  manifest: object
}
export function writePackagedCapabilitiesConfig(coreRoot: string, pluginNames: string[]): object
