import type { RuntimeCapabilityReport, RuntimePackageRecord } from './runtime-capabilities.mjs'

const dshRecord: RuntimePackageRecord = {
  name: '@deepseek-ai/dsh', observedVersion: '0.1.1-rc.2', status: 'compatible', entrypoints: { bin: 'lib/bin.js' },
}
const desktopRecord: RuntimePackageRecord = {
  name: '@dsh/desktop-plugin', observedVersion: '0.3.2', status: 'compatible', entrypoints: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' }, bundlePatch: './cordis.patch.yml',
}
const report: Pick<RuntimeCapabilityReport, 'packages'> = { packages: [dshRecord, desktopRecord] }
void report

// @ts-expect-error capability package names are closed
const unknownPackage: RuntimePackageRecord = { name: '@spoofed/package', observedVersion: '0.1.1-rc.2', status: 'compatible', entrypoints: { bin: 'lib/bin.js' } }
// @ts-expect-error desktop plugin cannot claim the DSH CLI entrypoint map
const invalidEntrypoints: RuntimePackageRecord = { name: '@dsh/desktop-plugin', observedVersion: '0.3.2', status: 'compatible', entrypoints: { bin: 'lib/bin.js' } }
void unknownPackage
void invalidEntrypoints
