import type { RuntimeCapabilityReport } from './runtime-capabilities.mjs'

export function inspectAssembledRuntimeCapabilities(appDirectory: string, expected: { dshVersion: string; desktopPluginVersion: string }): RuntimeCapabilityReport
