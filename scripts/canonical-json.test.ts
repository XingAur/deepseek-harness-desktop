import { describe, expect, it } from 'vitest'
import { canonicalJson } from './canonical-json.mjs'

describe('canonicalJson', () => {
  it('sorts object keys recursively without reordering arrays', () => {
    expect(canonicalJson({ z: 1, a: [{ y: 2, x: 1 }] })).toBe('{"a":[{"x":1,"y":2}],"z":1}')
  })

  it('can omit a root signature', () => {
    expect(canonicalJson({ signature: 'ignored', value: 1 }, 'signature')).toBe('{"value":1}')
  })
})
