import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { showBootstrapFailure } from './bootstrap-fallback'

describe('desktop HTML bootstrap fallback', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('keeps a visible loading surface before React mounts', () => {
    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')

    expect(html).toContain('id="bootstrap-fallback"')
    expect(html).toContain('正在加载 DeepSeek Harness')
    expect(html).toContain('background:#111113')
  })

  it('turns an asynchronous bootstrap failure into a visible diagnostic surface', () => {
    document.body.innerHTML = '<div id="root"></div><div id="bootstrap-fallback" role="status">正在加载</div>'

    showBootstrapFailure(new Error('renderer import failed'))

    expect(screen.getByRole('alert')).toHaveTextContent('DeepSeek Harness 启动失败')
    expect(screen.getByRole('alert')).toHaveTextContent('renderer import failed')
  })

  it('creates a diagnostic surface even if the static fallback node is missing', () => {
    showBootstrapFailure('missing mount point')

    expect(screen.getByRole('alert')).toHaveTextContent('missing mount point')
  })
})
