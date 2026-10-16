#!/usr/bin/env node
import { cpSync, lstatSync, mkdirSync, readFileSync, rmSync, unlinkSync, writeFileSync, renameSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'

const [stageArgument, overlayArgument] = process.argv.slice(2)
if (!stageArgument || !overlayArgument) throw new Error('usage: prepare-portable-stage.mjs <official-stage> <overlay>')
const stage = resolve(stageArgument)
const overlay = resolve(overlayArgument)
const rootManifest = JSON.parse(readFileSync(join(stage, 'package.json'), 'utf8'))
const version = rootManifest.version
if (typeof version !== 'string' || version.length === 0) throw new Error('portable stage: official version is missing')
const npmRegistry = process.env.DSH_PORTABLE_NPM_REGISTRY?.trim() || 'https://registry.npmjs.org'
const npmRegistryUrl = new URL(npmRegistry)
if (npmRegistryUrl.protocol !== 'https:' || npmRegistryUrl.username || npmRegistryUrl.password) throw new Error('portable stage: DSH_PORTABLE_NPM_REGISTRY must be a credential-free HTTPS URL')

function writeJson(path, value) { writeFileSync(path, `${JSON.stringify(value, undefined, 2)}\n`) }
function replaceOnce(path, before, after, optional = false) {
  const source = readFileSync(path, 'utf8')
  if (source.includes(after)) return
  const first = source.indexOf(before)
  if (first < 0 || source.indexOf(before, first + before.length) >= 0) {
    if (optional && first < 0) return
    throw new Error(`portable stage: expected one match in ${path}: ${before.slice(0, 80)}`)
  }
  writeFileSync(path, source.slice(0, first) + after + source.slice(first + before.length))
}

const stagedOverlay = join(stage, 'packages', 'external', 'overlay-custom')
rmSync(stagedOverlay, { recursive: true, force: true })
mkdirSync(dirname(stagedOverlay), { recursive: true })
cpSync(overlay, stagedOverlay, {
  recursive: true,
  filter(source) {
    const name = basename(source)
    if (['node_modules', 'package-lock.json', '.DS_Store'].includes(name)) return false
    return !lstatSync(source).isSymbolicLink()
  },
})
const overlayManifestPath = join(stagedOverlay, 'package.json')
const overlayManifest = JSON.parse(readFileSync(overlayManifestPath, 'utf8'))
overlayManifest.name = '@deepseek-ai/dsh-overlay-custom'
overlayManifest.version = version
delete overlayManifest.private
overlayManifest.description = 'Portable custom features for the local DeepSeek Harness desktop build'
overlayManifest.files = ['cordis.patch.yml', 'public', 'lib']
overlayManifest.dependencies = { ...overlayManifest.dependencies, '@deepseek-ai/dsh-mcp-client': 'workspace:*' }
writeJson(overlayManifestPath, overlayManifest)
mkdirSync(join(stagedOverlay, 'lib', 'types'), { recursive: true })
writeFileSync(join(stagedOverlay, 'lib', 'types', 'index.js'), "export * from '../../src/index.ts'\n")
replaceOnce(join(stagedOverlay, 'cordis.patch.yml'), '      name: dsh-overlay-custom', '      name: "@deepseek-ai/dsh-overlay-custom"')

const cliManifestPath = join(stage, 'apps', 'cli', 'package.json')
const cliManifest = JSON.parse(readFileSync(cliManifestPath, 'utf8'))
cliManifest.dependencies['@deepseek-ai/dsh-overlay-custom'] = 'workspace:*'
writeJson(cliManifestPath, cliManifest)

const profileSource = join(stage, 'apps', 'desktop', 'src', 'project-manager.ts')
replaceOnce(profileSource,
  "import type { DesktopRelease } from './release.ts'\n",
  "import type { DesktopRelease } from './release.ts'\nimport { reconcilePortableOverlayProfile } from './portable-overlay-profile.ts'\n")
replaceOnce(profileSource,
  '      createPluginProfile(this.paths.profile)\n      removeLinkProjections(this.paths.profile)',
  '      createPluginProfile(this.paths.profile)\n      reconcilePortableOverlayProfile(this.paths.profile)\n      removeLinkProjections(this.paths.profile)')

writeFileSync(join(stage, 'apps', 'desktop', 'src', 'portable-overlay-profile.ts'), `/** Portable overlay profile reconciliation owned by the local distribution build. */
import { existsSync, lstatSync, readFileSync, renameSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { load, dump } from 'js-yaml'

const BUNDLE = '@deepseek-ai/dsh-overlay-custom'
const LEGACY = new Set(['dsh-overlay-custom', BUNDLE])
function atomic(path: string, value: string): void {
  const temporary = \`\${path}.portable-\${process.pid}\`
  writeFileSync(temporary, value, { mode: 0o600 })
  renameSync(temporary, path)
}
function stripOverlayRows(value: unknown): unknown[] {
  if (!Array.isArray(value)) return []
  const result: unknown[] = []
  for (const item of value) {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) { result.push(item); continue }
    const row = item as Record<string, unknown>
    if ((typeof row.id === 'string' && row.id === 'overlay-custom') || (typeof row.name === 'string' && LEGACY.has(row.name))) continue
    if (Array.isArray(row.insert)) {
      const insert = row.insert.filter(entry => entry === null || typeof entry !== 'object' || Array.isArray(entry)
        || !((entry as Record<string, unknown>).id === 'overlay-custom' || LEGACY.has(String((entry as Record<string, unknown>).name ?? ''))))
      if (insert.length === 0) continue
      result.push({ ...row, insert })
    } else result.push(row)
  }
  return result
}
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
/** Keep selected live GPT and Grok models resolvable before image admission runs. */
function reconcileSelectedVisionModel(rows: unknown[]): void {
  const selected = rows.find(item => record(item).id === 'agent-default-model')
  const selectedConfig = record(record(selected).config)
  const provider = selectedConfig.provider
  const model = selectedConfig.model
  const xai = provider === 'xai' && typeof model === 'string' && /^grok-[A-Za-z0-9._-]+$/u.test(model)
  const codex = provider === 'openai-codex' && typeof model === 'string' && /^gpt-[A-Za-z0-9._-]+$/u.test(model)
  if (!xai && !codex) return
  let llm = rows.find(item => record(item).id === 'llm-pi-ai')
  if (llm === undefined) {
    llm = { id: 'llm-pi-ai', name: '@deepseek-ai/dsh-llm-pi-ai', config: { providers: {} } }
    rows.push(llm)
  }
  const llmRecord = record(llm)
  const config = record(llmRecord.config)
  const providers = record(config.providers)
  const profile = record(providers[provider as string])
  const models = Array.isArray(profile.models) ? [...profile.models] : []
  const at = models.findIndex(item => record(item).id === model)
  const name = xai ? 'Grok ' + (model as string).slice(5).replaceAll('-', ' ') : (model as string).replace(/^gpt-/u, 'GPT-')
  const descriptor = at < 0 ? { id: model, name, input: ['text', 'image'] }
    : { ...record(models[at]), input: ['text', 'image'] }
  if (at < 0) models.push(descriptor)
  else models[at] = descriptor
  providers[provider as string] = { ...profile, models }
  config.providers = providers
  llmRecord.config = config
}
/** Add the bundled overlay and remove development-only file links without touching user settings. */
export function reconcilePortableOverlayProfile(profile: string): void {
  const manifestPath = join(profile, 'package.json')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as { dependencies?: Record<string, string>; dsh?: { profile?: { bundles?: string[] } } }
  const dependencies = { ...(manifest.dependencies ?? {}) }
  delete dependencies['dsh-overlay-custom']; delete dependencies[BUNDLE]
  manifest.dependencies = dependencies
  const bundles = [...(manifest.dsh?.profile?.bundles ?? [])].filter(name => !LEGACY.has(name))
  bundles.push(BUNDLE)
  manifest.dsh = { ...(manifest.dsh ?? {}), profile: { ...(manifest.dsh?.profile ?? {}), bundles } }
  atomic(manifestPath, JSON.stringify(manifest, undefined, 2) + '\\n')
  const patchPath = join(profile, 'cordis.patch.yml')
  if (existsSync(patchPath)) {
    const rows = stripOverlayRows(load(readFileSync(patchPath, 'utf8')))
    reconcileSelectedVisionModel(rows)
    atomic(patchPath, dump(rows, { lineWidth: -1 }))
  }
  const legacyModule = join(profile, 'node_modules', 'dsh-overlay-custom')
  if (existsSync(legacyModule) && lstatSync(legacyModule).isSymbolicLink()) unlinkSync(legacyModule)
  const lock = join(profile, 'package-lock.json')
  if (existsSync(lock) && readFileSync(lock, 'utf8').includes('dsh-overlay-custom')) rmSync(lock, { force: true })
  atomic(join(profile, 'portable-overlay-state.json'), JSON.stringify({ version: 1, bundle: BUNDLE }, undefined, 2) + '\\n')
}
`)

const mainSource = join(stage, 'apps', 'desktop', 'src', 'main.ts')
replaceOnce(mainSource, "import { join } from 'node:path'\n", "import { join } from 'node:path'\nimport { homedir } from 'node:os'\n")
replaceOnce(mainSource, "import { DesktopUpdatePreparationError } from './update-error.ts'\n", "import { DesktopUpdatePreparationError } from './update-error.ts'\n\nif (process.env.DSH_HOME === undefined || process.env.DSH_HOME === '') process.env.DSH_HOME = join(homedir(), '.dsh-official')\n")

const packageTarget = join(stage, 'apps', 'desktop', 'scripts', 'package-target.ts')
replaceOnce(packageTarget,
  "import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'",
  "import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'")
replaceOnce(packageTarget,
  "const APP_ROOT = resolve(import.meta.dirname, '..')\n",
  `const APP_ROOT = resolve(import.meta.dirname, '..')

function sanitizePortableRuntimeShims(runtime: string): void {
  for (const relative of ['pnpm/node_modules/.bin', 'primary-runtime/dependencies/pnpm/node_modules/.bin']) {
    const directory = join(runtime, relative)
    if (!existsSync(directory)) continue
    for (const name of readdirSync(directory)) {
      if (!/^(?:pn|pnx|pnpm|pnpx)(?:\\.cmd|\\.ps1)?$/iu.test(name)) continue
      const path = join(directory, name)
      const source = readFileSync(path, 'utf8')
      const sanitized = source
        .replace(/^if \\[ -z "\\$NODE_PATH" \\]; then\\r?\\n[\\s\\S]*?^fi\\r?\\n/mu, '')
        .replace(/^@IF NOT DEFINED NODE_PATH \\(\\r?\\n[\\s\\S]*?^\\) ELSE \\(\\r?\\n[\\s\\S]*?^\\)\\r?\\n/mu, '')
        .replace(/^if \\(\\$env:NODE_PATH\\) \\{\\r?\\n[\\s\\S]*?^\\} else \\{\\r?\\n[\\s\\S]*?^\\}\\r?\\n/mu, '')
        .replace(/^# cmd-shim-target=.*\\r?\\n?/mu, '')
      if (sanitized === source || sanitized.includes('NODE_PATH')) throw new Error(\`portable runtime: unexpected pnpm shim \${path}\`)
      writeFileSync(path, sanitized)
    }
  }
}
function sanitizePortableTextPaths(tree: string, forbidden: string): void {
  const variants = [...new Set([forbidden, forbidden.replaceAll('\\\\', '/'), forbidden.replaceAll('/', '\\\\')])]
  for (const entry of readdirSync(tree, { withFileTypes: true })) {
    const path = join(tree, entry.name)
    if (entry.isDirectory()) { sanitizePortableTextPaths(path, forbidden); continue }
    if (!entry.isFile() || !/\\.(?:cjs|css|html|js|json|map|mjs|ts|yaml|yml)$/iu.test(entry.name)) continue
    const source = readFileSync(path, 'utf8')
    let sanitized = source
    for (const value of variants) sanitized = sanitized.replaceAll(value, '<portable-build-root>')
    if (sanitized !== source) writeFileSync(path, sanitized)
  }
}
`)
replaceOnce(packageTarget,
  "  await execute(['run', 'build:official'], buildEnv, REPOSITORY_ROOT)\n  await execute(['run', 'release:pack', '--family', 'dsh', '--out', buildPaths.packedDsh, ...packArguments], buildEnv, REPOSITORY_ROOT)",
  "  await execute(['run', 'build:official'], buildEnv, REPOSITORY_ROOT)\n  rmSync(join(REPOSITORY_ROOT, 'packages/external/overlay-custom/lib/types/index.js'), { force: true })\n  await execute(['run', 'release:pack', '--family', 'dsh', '--out', buildPaths.packedDsh, ...packArguments], buildEnv, REPOSITORY_ROOT)")
replaceOnce(packageTarget,
  "  await execute(['run', 'prepare:dsh', ...(signPrimaryRuntime ? ['--defer-runtime-smoke'] : [])], downloadEnv)",
  "  await execute(['run', 'prepare:dsh', ...(signPrimaryRuntime ? ['--defer-runtime-smoke'] : [])], downloadEnv)\n  sanitizePortableRuntimeShims(buildPaths.runtime)\n  sanitizePortableTextPaths(buildPaths.runtime, REPOSITORY_ROOT)\n  sanitizePortableTextPaths(join(APP_ROOT, 'lib'), REPOSITORY_ROOT)\n  if (process.env.DSH_PORTABLE_SOURCE_ROOT) {\n    sanitizePortableTextPaths(buildPaths.runtime, process.env.DSH_PORTABLE_SOURCE_ROOT)\n    sanitizePortableTextPaths(join(APP_ROOT, 'lib'), process.env.DSH_PORTABLE_SOURCE_ROOT)\n  }")
replaceOnce(packageTarget,
  "    DSH_DESKTOP_TARGET_ARCH: target.arch,\n  }",
  "    DSH_DESKTOP_TARGET_ARCH: target.arch,\n    DSH_DESKTOP_UNSIGNED: invocation.unsigned ? '1' : '0',\n  }",
  true)
replaceOnce(packageTarget,
  "  if (values.unsigned && name !== 'win-x64') throw new Error('desktop package: --unsigned requires win-x64')",
  "  if (values.unsigned && name !== 'win-x64' && name !== 'mac-arm64') throw new Error('desktop package: --unsigned requires win-x64 or mac-arm64')",
  true)
replaceOnce(packageTarget,
  "  if (name === 'mac-arm64' && hostArch !== 'arm64') {\n    throw new Error('desktop package: mac-arm64 requires an Apple Silicon build host')\n  }",
  "  if (name === 'mac-arm64' && hostArch !== 'arm64' && process.env.DSH_PORTABLE_ALLOW_MAC_ARM64_CROSS !== '1') {\n    throw new Error('desktop package: mac-arm64 requires an Apple Silicon build host')\n  }")
replaceOnce(packageTarget,
  "    if (target.platform === 'darwin') {\n      const settings = resolveMacOSPackageSettings(environment)",
  "    if (target.platform === 'darwin' && !invocation.unsigned) {\n      const settings = resolveMacOSPackageSettings(environment)",
  true)
replaceOnce(packageTarget,
  "    } else {\n      await packagingStep(run.directory, 'windows-package', () => packageTarget(invocation, environment, run), secrets)\n    }",
  "    } else {\n      await packagingStep(run.directory, target.platform === 'darwin' ? 'macos-unsigned-package' : 'windows-package', () => packageTarget(invocation, environment, run), secrets)\n    }",
  true)
replaceOnce(packageTarget,
  "    await execute(['exec', 'tsx', 'scripts/smoke-packaged-runtime.ts'], targetEnv)\n    const appPath = join(buildPaths.artifacts, target.arch === 'arm64' ? 'mac-arm64' : 'mac', 'DeepSeek Harness.app')\n    await withMacOSNotarizationProxy(mac?.notarizationProxy,\n      () => notarizeMacOS({ appPath, ...resolveMacOSNotarizationEnvironment(environment) }), undefined, undefined, proxyEvent)",
  "    await execute(['exec', 'tsx', 'scripts/smoke-packaged-runtime.ts', ...(invocation.unsigned ? ['--unsigned'] : [])], targetEnv)\n    if (!invocation.unsigned) {\n      const appPath = join(buildPaths.artifacts, target.arch === 'arm64' ? 'mac-arm64' : 'mac', 'DeepSeek Harness.app')\n      await withMacOSNotarizationProxy(mac?.notarizationProxy,\n        () => notarizeMacOS({ appPath, ...resolveMacOSNotarizationEnvironment(environment) }), undefined, undefined, proxyEvent)\n    }",
  true)

const builderConfig = join(stage, 'apps', 'desktop', 'scripts', 'electron-builder-config.mjs')
replaceOnce(builderConfig, "  if (unsigned && resolvedPlatform !== 'win32') throw new Error('desktop package: unsigned builds require Windows')", "  if (unsigned && resolvedPlatform !== 'win32' && resolvedPlatform !== 'darwin') throw new Error('desktop package: unsupported unsigned platform')", true)
replaceOnce(builderConfig, "  const macOSSigning = packagesMacOS ? resolveMacOSSigningEnvironment(env) : undefined\n  if (packagesMacOS) resolveMacOSNotarizationEnvironment(env)", "  const macOSSigning = packagesMacOS && !unsigned ? resolveMacOSSigningEnvironment(env) : undefined\n  if (packagesMacOS && !unsigned) resolveMacOSNotarizationEnvironment(env)", true)
replaceOnce(builderConfig, "      forceCodeSigning: true,\n      hardenedRuntime: true,", "      forceCodeSigning: !unsigned,\n      hardenedRuntime: !unsigned,", true)
replaceOnce(builderConfig, "      notarize: true,\n      target: ['dmg', 'zip'],", "      notarize: !unsigned,\n      target: unsigned ? ['dir'] : ['dmg', 'zip'],", true)
replaceOnce(builderConfig, "      sign: true,", "      sign: !unsigned,", true)
replaceOnce(builderConfig, "      if (context.electronPlatformName !== 'darwin') return", "      if (context.electronPlatformName !== 'darwin' || unsigned) return", true)
replaceOnce(builderConfig, "      if (!artifact.file.endsWith('.dmg')) return", "      if (unsigned || !artifact.file.endsWith('.dmg')) return", true)

const prepareDsh = join(stage, 'apps', 'desktop', 'scripts', 'prepare-dsh.ts')
replaceOnce(prepareDsh, "    if (process.platform === 'darwin') {", "    if (process.platform === 'darwin' && process.env.DSH_DESKTOP_UNSIGNED !== '1') {", true)

const smoke = join(stage, 'apps', 'desktop', 'scripts', 'smoke-packaged-runtime.ts')
replaceOnce(smoke, "if (values.unsigned && !windows) throw new Error('desktop smoke: unsigned artifacts require Windows')", "if (values.unsigned && target !== 'win-x64' && target !== 'mac-arm64') throw new Error('desktop smoke: unsupported unsigned target')", true)

const primaryRuntime = join(stage, 'scripts', 'primary-runtime', 'prepare.ts')
replaceOnce(primaryRuntime, '    const response = await fetch(url, { signal: AbortSignal.timeout(timeout) })', "    const mirror = process.env.DSH_GITHUB_ASSET_MIRROR?.replace(/\\/$/u, '')\n    const downloadUrl = mirror && url.startsWith('https://github.com/') ? `${mirror}/${url}` : url\n    const response = await fetch(downloadUrl, { signal: AbortSignal.timeout(timeout) })", true)
replaceOnce(primaryRuntime, '    const response = await fetch(url)', "    const mirror = process.env.DSH_GITHUB_ASSET_MIRROR?.replace(/\\/$/u, '')\n    const downloadUrl = mirror && url.startsWith('https://github.com/') ? `${mirror}/${url}` : url\n    const response = await fetch(downloadUrl)", true)

const desktopEnvironment = `DSH_DESKTOP_APP_ID=com.deepseek.harness.overlay.local\nDSH_DESKTOP_AUTO_UPDATE_ENV=test\nDSH_DESKTOP_NPM_REGISTRY=${npmRegistryUrl.origin}\nDSH_DESKTOP_MANDATORY_UPDATE_TEST_ORIGIN=https://updates.invalid\nDSH_DESKTOP_MANDATORY_UPDATE_CONFIG='{"allowedAuthOrigins":["https://chat.deepseek.com"]}'\n`
writeFileSync(join(stage, 'apps', 'desktop', '.env.macos'), desktopEnvironment)
writeFileSync(join(stage, 'apps', 'desktop', '.env.windows'), desktopEnvironment)
process.stdout.write(`portable stage prepared: ${stage}\noverlay package: @deepseek-ai/dsh-overlay-custom@${version}\n`)
