import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BootstrapErrorBoundary } from './BootstrapErrorBoundary'

describe('BootstrapErrorBoundary', () => {
  it('keeps a visible diagnostic surface when the renderer throws during startup', () => {
    const boundary = new BootstrapErrorBoundary({ children: null })
    boundary.state = BootstrapErrorBoundary.getDerivedStateFromError(new Error('renderer boot failed'))

    render(boundary.render())

    expect(screen.getByRole('alert')).toHaveTextContent('DeepSeek Harness 启动失败')
    expect(screen.getByRole('alert')).toHaveTextContent('renderer boot failed')
  })

  it('removes the static loading fallback only after React commits', () => {
    document.body.innerHTML = '<div id="bootstrap-fallback">正在加载</div>'

    render(<BootstrapErrorBoundary><div>工作台已加载</div></BootstrapErrorBoundary>)

    expect(screen.getByText('工作台已加载')).toBeVisible()
    expect(document.getElementById('bootstrap-fallback')).toBeNull()
  })
})
