import { assertRuntimeCapabilities, inspectRuntimeCapabilities } from './runtime-capabilities.mjs'

export function inspectAssembledRuntimeCapabilities(appDirectory, versions) {
  return assertRuntimeCapabilities(inspectRuntimeCapabilities(appDirectory, versions), versions)
}
