import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'
import {
  HARNESS_PLUGIN_VENDOR_MANIFEST,
  applyHarnessPluginCompatibilityPatches,
  copyHarnessPluginBundle,
  syncHarnessPluginVendor,
  verifyCheckedInHarnessPluginBundle,
  verifyHarnessPluginBundle,
  writeFrozenPluginInventoryFromBundle,
  writePackagedCapabilitiesConfig,
} from './vendor-harness-plugins.mjs'

const directories: string[] = []

function temporary() {
  const directory = mkdtempSync(join(tmpdir(), 'vendor-harness-plugins-'))
  directories.push(directory)
  return directory
}

function sha256(path: string) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function writePlugin(root: string, name: string) {
  const plugin = join(root, name)
  mkdirSync(join(plugin, '.codex-plugin'), { recursive: true })
  mkdirSync(join(plugin, 'scripts'), { recursive: true })
  const descriptor = JSON.stringify({ name, version: '1.0.0' })
  const capabilities = JSON.stringify({
    schema_version: 'his-capabilities.v1',
    plugin: name,
    plugin_version: '1.0.0',
    capabilities: [],
  })
  writeFileSync(join(plugin, '.codex-plugin', 'plugin.json'), descriptor)
  writeFileSync(join(plugin, 'capabilities.json'), capabilities)
  writeFileSync(join(plugin, 'scripts', 'provider.py'), 'print("ok")\n')
  mkdirSync(join(plugin, 'scripts', '__pycache__'), { recursive: true })
  writeFileSync(join(plugin, 'scripts', '__pycache__', 'provider.pyc'), 'cache')
  return plugin
}

afterAll(() => {
  for (const directory of directories) rmSync(directory, { recursive: true, force: true })
})

describe('Harness plugin bundle', () => {
  it('binds the governance provider to the packaged Core before developer paths', () => {
    const bundleRoot = temporary()
    const pluginRoot = join(bundleRoot, 'his-harness-core')
    mkdirSync(join(pluginRoot, 'scripts'), { recursive: true })
    const provider = join(pluginRoot, 'scripts', 'requirement_governance.py')
    writeFileSync(
      provider,
      [
        '_STAGED_HARNESS_ROOT = Path(__file__).resolve().parents[3] / "Harness"',
        '_DOCUMENTED_HARNESS_ROOT = Path("/Users/lym/WorkCode/ai/Harness")',
        '        _STAGED_HARNESS_ROOT,',
        '        _DOCUMENTED_HARNESS_ROOT,',
        '',
      ].join('\n'),
    )
    const inventoryPath = join(bundleRoot, 'plugin_inventory.json')
    writeFileSync(inventoryPath, JSON.stringify({
      schema_version: 'his-plugin-inventory.v1',
      plugins: [{
        name: 'his-harness-core',
        version: '1.0.0',
        capabilities_sha256: '0'.repeat(64),
        capabilities: ['requirement.govern'],
        sources_sha256: {
          'scripts/requirement_governance.py': sha256(provider),
        },
      }],
    }))

    expect(applyHarnessPluginCompatibilityPatches(bundleRoot, inventoryPath)).toEqual([
      'relocatable-his-harness-core-root',
    ])
    const patched = readFileSync(provider, 'utf8')
    expect(patched).toContain('parents[3] / "core"')
    expect(patched).toContain('parents[3] / "harness-core"')
    expect(patched.indexOf('_BUNDLED_HARNESS_ROOT,')).toBeLessThan(
      patched.indexOf('_DOCUMENTED_HARNESS_ROOT,'),
    )
    const inventory = JSON.parse(readFileSync(inventoryPath, 'utf8'))
    expect(inventory.plugins[0].sources_sha256['scripts/requirement_governance.py']).toBe(sha256(provider))
    expect(applyHarnessPluginCompatibilityPatches(bundleRoot, inventoryPath)).toEqual([
      'relocatable-his-harness-core-root',
    ])
  })

  it('copies only a frozen compatible plugin set and rejects drift', () => {
    const sourceRoot = temporary()
    const plugin = writePlugin(sourceRoot, 'his-engineering')
    const inventoryPath = join(sourceRoot, 'plugin_inventory.json')
    writeFileSync(inventoryPath, JSON.stringify({
      schema_version: 'his-plugin-inventory.v1',
      plugins: [{
        name: 'his-engineering',
        version: '1.0.0',
        capabilities_sha256: sha256(join(plugin, 'capabilities.json')),
        capabilities: [],
        sources_sha256: {
          '.codex-plugin/plugin.json': sha256(join(plugin, '.codex-plugin', 'plugin.json')),
          'capabilities.json': sha256(join(plugin, 'capabilities.json')),
          'scripts/provider.py': sha256(join(plugin, 'scripts', 'provider.py')),
        },
      }],
    }))

    const target = temporary()
    const result = copyHarnessPluginBundle({
      sources: { 'his-engineering': plugin },
      target,
      inventoryPath,
    })

    expect(result.pluginCount).toBe(1)
    expect(verifyHarnessPluginBundle(target, inventoryPath).pluginCount).toBe(1)
    expect(() => readFileSync(join(target, 'his-engineering', 'scripts', '__pycache__', 'provider.pyc'))).toThrow()

    writeFileSync(join(target, 'his-engineering', 'scripts', 'provider.py'), 'print("drift")\n')
    expect(() => verifyHarnessPluginBundle(target, inventoryPath)).toThrow(/哈希不一致/)
  })

  it('syncs an audited plugin source set without modifying the upstream inventory', () => {
    const sourceRoot = temporary()
    const plugin = writePlugin(sourceRoot, 'his-engineering')
    const inventoryPath = join(sourceRoot, 'plugin_inventory.json')
    const inventoryText = JSON.stringify({
      schema_version: 'his-plugin-inventory.v1',
      plugins: [{
        name: 'his-engineering',
        version: '1.0.0',
        capabilities_sha256: sha256(join(plugin, 'capabilities.json')),
        capabilities: [],
        sources_sha256: {
          '.codex-plugin/plugin.json': sha256(join(plugin, '.codex-plugin', 'plugin.json')),
          'capabilities.json': sha256(join(plugin, 'capabilities.json')),
          'scripts/provider.py': sha256(join(plugin, 'scripts', 'provider.py')),
        },
      }],
    })
    writeFileSync(inventoryPath, inventoryText)
    const target = temporary()

    const result = syncHarnessPluginVendor({ sourceRoot, target, inventoryPath })

    expect(result.pluginCount).toBe(1)
    expect(result.compatibilityPatches).toEqual([])
    expect(readFileSync(inventoryPath, 'utf8')).toBe(inventoryText)
    expect(readFileSync(join(target, HARNESS_PLUGIN_VENDOR_MANIFEST), 'utf8')).toContain(sourceRoot)
  })

  it('writes relocatable plugin roots into the assembled Core config', () => {
    const coreRoot = temporary()
    mkdirSync(join(coreRoot, 'config'), { recursive: true })
    writeFileSync(join(coreRoot, 'config', 'capabilities.json'), JSON.stringify({
      schema_version: 'his-capability-runtime-config.v1',
      routing_mode: 'enforce',
      plugin_roots: ['/Users/example/plugins/his-engineering'],
      external_writes_default: false,
      default_timeout_seconds: 60,
    }))

    writePackagedCapabilitiesConfig(coreRoot, ['his-engineering'])
    const config = JSON.parse(readFileSync(join(coreRoot, 'config', 'capabilities.json'), 'utf8'))
    expect(config.plugin_roots).toEqual(['../../plugins/his-engineering'])
  })

  it('rebinds the copied Core inventory to every file in the checked-in frozen plugin bundle', () => {
    const root = join(process.cwd(), 'vendor', 'harness-plugins')
    const generatedRoot = temporary()
    const inventoryPath = join(generatedRoot, 'plugin_inventory.json')
    const inventory = writeFrozenPluginInventoryFromBundle(root, inventoryPath)
    const summary = verifyHarnessPluginBundle(
      root,
      inventoryPath,
    )
    const manifest = JSON.parse(readFileSync(join(root, HARNESS_PLUGIN_VENDOR_MANIFEST), 'utf8'))
    expect(inventory.plugins.map((plugin: { name: string }) => plugin.name)).toEqual([
      'his-engineering',
      'his-harness-core',
      'his-knowledge',
      'yunxiao',
    ])
    const knowledgePlugin = inventory.plugins.find(
      (plugin: { name: string }) => plugin.name === 'his-knowledge',
    )
    expect(knowledgePlugin).toBeDefined()
    expect(
      knowledgePlugin!.sources_sha256['scripts/run_hermetic_acceptance.py'],
    ).toMatch(/^[0-9a-f]{64}$/)
    expect(summary).toEqual({
      pluginCount: manifest.pluginCount,
      fileCount: manifest.fileCount,
      totalBytes: manifest.totalBytes,
      manifestSha256: manifest.manifestSha256,
    })
  })

  it('verifies the checked-in bundle against its own frozen vendor manifest', () => {
    const root = join(process.cwd(), 'vendor', 'harness-plugins')
    const manifest = JSON.parse(readFileSync(join(root, HARNESS_PLUGIN_VENDOR_MANIFEST), 'utf8'))

    expect(verifyCheckedInHarnessPluginBundle(root)).toEqual({
      pluginCount: manifest.pluginCount,
      fileCount: manifest.fileCount,
      totalBytes: manifest.totalBytes,
      manifestSha256: manifest.manifestSha256,
    })
  })

  it('preserves the copied Core plugin order while rebinding frozen bundle hashes', () => {
    const root = join(process.cwd(), 'vendor', 'harness-plugins')
    const generatedRoot = temporary()
    const inventoryPath = join(generatedRoot, 'plugin_inventory.json')
    writeFileSync(inventoryPath, JSON.stringify({
      schema_version: 'his-plugin-inventory.v1',
      plugins: [
        { name: 'his-harness-core' },
        { name: 'yunxiao' },
        { name: 'his-engineering' },
        { name: 'his-knowledge' },
      ],
    }))

    const inventory = writeFrozenPluginInventoryFromBundle(root, inventoryPath)

    expect(inventory.plugins.map((plugin: { name: string }) => plugin.name)).toEqual([
      'his-harness-core',
      'yunxiao',
      'his-engineering',
      'his-knowledge',
    ])
    expect(() => verifyHarnessPluginBundle(root, inventoryPath)).not.toThrow()
  })
})
