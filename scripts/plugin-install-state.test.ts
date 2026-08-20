import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { markerMatches, writeInstallMarker } from './plugin-install-state.mjs'

describe('desktop plugin install marker', () => {
  it('matches only an exact version and sha and writes atomically', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'dsh-plugin-marker-'))
    const marker = join(dir, '.desktop-plugin-install.json')

    expect(await markerMatches(marker, '0.1.4', 'a'.repeat(64))).toBe(false)
    await writeFile(marker, '{bad json')
    expect(await markerMatches(marker, '0.1.4', 'a'.repeat(64))).toBe(false)

    await writeInstallMarker(marker, '0.1.4', 'a'.repeat(64))

    expect(await markerMatches(marker, '0.1.4', 'a'.repeat(64))).toBe(true)
    expect(await markerMatches(marker, '0.1.4', 'b'.repeat(64))).toBe(false)
    expect(JSON.parse(await readFile(marker, 'utf8'))).toEqual({
      version: '0.1.4',
      sha256: 'a'.repeat(64),
    })
  })
})
