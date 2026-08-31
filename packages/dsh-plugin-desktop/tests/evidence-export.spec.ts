import { describe, expect, it } from 'vitest'
import { exportEvidence } from '../src/client/harness/evidence-export'

describe('Harness evidence export', () => {
  it('creates a hashed, provenance-preserving image reference', async () => {
    const file = new File([new Uint8Array([137, 80, 78, 71])], 'error.png', { type: 'image/png' })
    const evidence = await exportEvidence([{ file, source: 'current-chat' }])
    expect(evidence.schema_version).toBe('conversation-evidence.v1')
    expect(evidence.files[0]).toMatchObject({ fileName: 'error.png', mediaType: 'image/png', source: 'current-chat', byteLength: 4 })
    expect(evidence.files[0].sha256).toMatch(/^[0-9a-f]{64}$/)
    expect(evidence.blobs.get(evidence.files[0].id)).toBe(file)
  })

  it('rejects oversized evidence before it can enter a Harness prompt', async () => {
    const file = new File([new Uint8Array(33 * 1024 * 1024)], 'large.bin', { type: 'application/octet-stream' })
    await expect(exportEvidence([{ file, source: 'local-selection' }])).rejects.toThrow('证据文件超出限制')
  })
})
