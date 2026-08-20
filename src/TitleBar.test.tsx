import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TitleBar } from './TitleBar'
import type { WindowControls } from './window-client'

function fakeControls(): WindowControls {
  return {
    hide: vi.fn(async () => undefined),
    minimize: vi.fn(async () => undefined),
    toggleMaximize: vi.fn(async () => undefined),
    startDragging: vi.fn(async () => undefined),
  }
}

describe('TitleBar', () => {
  it('maps traffic lights to window operations', () => {
    const controls = fakeControls()
    render(<TitleBar controls={controls} />)

    fireEvent.click(screen.getByRole('button', { name: '关闭窗口' }))
    fireEvent.click(screen.getByRole('button', { name: '最小化窗口' }))
    fireEvent.click(screen.getByRole('button', { name: '最大化或还原窗口' }))

    expect(controls.hide).toHaveBeenCalledOnce()
    expect(controls.minimize).toHaveBeenCalledOnce()
    expect(controls.toggleMaximize).toHaveBeenCalledOnce()
    expect(screen.queryByText('DeepSeek Harness Desktop')).not.toBeInTheDocument()
  })

  it('starts native dragging from blank primary-button title space', () => {
    const controls = fakeControls()
    render(<TitleBar controls={controls} />)
    const bar = screen.getByRole('banner')

    expect(bar).not.toHaveAttribute('data-tauri-drag-region')
    fireEvent.mouseDown(bar, { buttons: 1, detail: 1 })
    expect(controls.startDragging).not.toHaveBeenCalled()
    fireEvent.mouseMove(bar, { buttons: 1 })

    expect(controls.startDragging).toHaveBeenCalledOnce()
    expect(controls.toggleMaximize).not.toHaveBeenCalled()
  })

  it('toggles maximize instead of dragging on a primary-button double press', () => {
    const controls = fakeControls()
    render(<TitleBar controls={controls} />)

    const bar = screen.getByRole('banner')
    fireEvent.mouseDown(bar, { buttons: 1, detail: 1 })
    fireEvent.mouseUp(bar, { buttons: 0, detail: 1 })
    fireEvent.mouseDown(bar, { buttons: 1, detail: 2 })
    fireEvent.mouseUp(bar, { buttons: 0, detail: 2 })

    expect(controls.startDragging).not.toHaveBeenCalled()
    expect(controls.toggleMaximize).toHaveBeenCalledOnce()
  })

  it('does not start dragging when a traffic light receives the mouse event', () => {
    const controls = fakeControls()
    render(<TitleBar controls={controls} />)

    fireEvent.mouseDown(screen.getByRole('button', { name: '关闭窗口' }), { buttons: 1, detail: 1 })

    expect(controls.startDragging).not.toHaveBeenCalled()
    expect(controls.toggleMaximize).not.toHaveBeenCalled()
  })
})
