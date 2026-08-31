import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('desktop chrome official style contract', () => {
  it('keeps bootstrap and update chrome visually subordinate to the upstream workbench', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/app.css'), 'utf8')
    const bootstrap = css.slice(css.indexOf('.bootstrapShell {'), css.indexOf('.bootstrapShell::-webkit-scrollbar'))

    expect(bootstrap).not.toContain('radial-gradient')
    expect(css).toContain('.orbitLayer, .orbitParticle { display: none; }')
    expect(css).toMatch(/\.primaryButton \{[^}]*background: var\(--shell-text\)/)
    expect(css).toMatch(/\.updatePrimaryButton \{[^}]*background: var\(--shell-text\)/)
  })
})
