import { readFileSync, writeFileSync } from 'node:fs'

export const DESKTOP_BUNDLES = Object.freeze([
  '@deepseek-ai/dsh-base',
  '@deepseek-ai/dsh-web-app',
  '@dsh/desktop-plugin',
])

export function ensureDesktopProfile(manifestPath) {
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
