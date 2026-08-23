export const DESKTOP_BUNDLES: readonly string[]
export interface DesktopRuntimeCapabilityReport {
  profileBundles?: readonly string[]
}
export function ensureDesktopProfile(manifestPath: string, capabilityReport: DesktopRuntimeCapabilityReport): boolean
