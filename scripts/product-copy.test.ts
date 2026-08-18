import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const productCopyFiles = [
  'index.html',
  'README.md',
  'runtime/README.md',
  'runtime/catalog/community.json',
  'CLAUDE.md',
  '.github/workflows/desktop.yml',
  'src/App.tsx',
  'src-tauri/Cargo.toml',
  'src-tauri/tauri.conf.json',
  'src-tauri/tauri.windows.conf.json',
  'src-tauri/src/lib.rs',
  'src-tauri/src/window.rs',
  'src-tauri/src/runtime/health.rs',
  'src-tauri/src/runtime/manager.rs',
  'src-tauri/src/runtime/process.rs',
  'packages/dsh-plugin-desktop/src/catalog.ts',
  'packages/dsh-plugin-desktop/src/client/MarketPage.tsx',
  'packages/dsh-plugin-desktop/src/plugin-command.ts',
]

const abbreviatedProductCopy = [
  'DSH Desktop',
  'DSH Runtime',
  'DSH 工作台',
  '非 DSH 服务',
  '非受管 DSH 地址',
  'DSH 插件体系',
  'Find DSH Plugin',
]

describe('product copy', () => {
  it('uses the full DeepSeek Harness name in user-facing sources', () => {
    const violations = productCopyFiles.flatMap((file) => {
      const source = readFileSync(file, 'utf8')
      return abbreviatedProductCopy
        .filter((copy) => source.includes(copy))
        .map((copy) => `${file}: ${copy}`)
    })

    expect(violations).toEqual([])
  })
})
