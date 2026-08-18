import { createPrivateKey, sign } from 'node:crypto'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { describe, expect, it } from 'vitest'
import { canonicalJson, CatalogStore, DEV_CATALOG_PUBLIC_KEY, verifyCatalog } from '../src/catalog'

const PRIVATE = 'wbAbExHsjryIT22fTuRA3W61tJdaXFC7YxoAeN9uKnQ'

function signedCatalog() {
  const unsigned = {
    schemaVersion: 1, generatedAt: '2026-08-18T00:00:00.000Z',
    plugins: [{
      id: 'test/plugin', packageName: '@test/plugin', name: 'Test', description: 'Description', publisher: 'Test',
      repository: 'https://github.com/test/plugin', installSpec: '@test/plugin@1.0.0', version: '1.0.0', dshRange: '>=0.1.0-rc.7',
      platforms: ['windows-x86_64'], verified: true,
    }],
  }
  const key = createPrivateKey({ key: { kty: 'OKP', crv: 'Ed25519', x: DEV_CATALOG_PUBLIC_KEY, d: PRIVATE }, format: 'jwk' })
  return { ...unsigned, signature: sign(null, Buffer.from(canonicalJson(unsigned)), key).toString('base64url') }
}

describe('verifyCatalog', () => {
  it('accepts a valid signed catalog and rejects tampering', () => {
    const catalog = signedCatalog()
    expect(verifyCatalog(JSON.stringify(catalog), DEV_CATALOG_PUBLIC_KEY).plugins).toHaveLength(1)
    expect(() => verifyCatalog(JSON.stringify({ ...catalog, generatedAt: 'changed' }), DEV_CATALOG_PUBLIC_KEY)).toThrow(/签名/)
  })

  it('loads the shipped catalog only for a compatible Runtime', async () => {
    const source = join(process.cwd(), '..', '..', 'runtime', 'catalog', 'community.json')
    const cachePath = join(tmpdir(), `dsh-desktop-catalog-${process.pid}.json`)
    const supported = await new CatalogStore({ source, cachePath, target: 'windows-x86_64', dshVersion: '0.1.0-rc.7' }).load()
    const unsupported = await new CatalogStore({ source, cachePath, target: 'windows-x86_64', dshVersion: '0.2.0' }).load()
    expect(supported.plugins.map((plugin) => plugin.packageName)).toEqual(['dsh-find-plugin'])
    expect(unsupported.plugins).toEqual([])
  })
})
