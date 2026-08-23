import { readFileSync, writeFileSync } from 'node:fs'
import { assertRuntimeCapabilities } from './runtime-capabilities.mjs'

export const DESKTOP_BUNDLES = Object.freeze([
  '@deepseek-ai/dsh-base',
  '@deepseek-ai/dsh-web-app',
  '@dsh/desktop-plugin',
])

export function ensureDesktopProfile(manifestPath, capabilityReport, expectedVersions) {
  assertRuntimeCapabilities(capabilityReport, expectedVersions)
  if (!sameBundles(capabilityReport?.profileBundles, DESKTOP_BUNDLES)) {
    throw new Error('Runtime capability report does not provide the exact compatible desktop profile bundles')
  }
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  manifest.dsh ??= {}
  manifest.dsh.profile ??= {}
  if (JSON.stringify(manifest.dsh.profile.bundles) === JSON.stringify(DESKTOP_BUNDLES)) {
    return false
  }
  manifest.dsh.profile.bundles = [...DESKTOP_BUNDLES]
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  return true
}

function sameBundles(value, expected) {
  return Array.isArray(value) && value.length === expected.length && value.every((bundle, index) => bundle === expected[index])
}
