import type { RuntimeCapabilityReport } from './runtime-capabilities.mjs'

export const DESKTOP_BUNDLES: readonly string[]
export type DesktopRuntimeCapabilityReport = RuntimeCapabilityReport
export function ensureDesktopProfile(manifestPath: string, capabilityReport: DesktopRuntimeCapabilityReport): boolean
