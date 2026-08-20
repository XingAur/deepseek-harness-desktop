import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DesktopThemePresenter } from '../src/client/theme-presenter'

describe('DesktopThemePresenter', () => {
  beforeEach(() => {
    document.body.removeAttribute('data-ds-dark-theme')
    document.body.removeAttribute('data-dsh-desktop-theme')
    document.body.removeAttribute('style')
    document.documentElement.removeAttribute('style')
    vi.spyOn(window.parent, 'postMessage').mockImplementation(() => undefined)
  })

  afterEach(() => vi.restoreAllMocks())

  it('projects an official light theme snapshot and notifies the parent shell', () => {
    const presenter = new DesktopThemePresenter()

    presenter.apply({
      active: {
        colorScheme: 'light',
        tokens: { '--dsw-alias-bg-base': '#fff' },
      },
    })

    expect(document.documentElement.style.colorScheme).toBe('light')
    expect(document.body.dataset.dshDesktopTheme).toBe('light')
    expect(document.body).not.toHaveAttribute('data-ds-dark-theme')
    expect(document.body.style.getPropertyValue('--dsw-alias-bg-base')).toBe('#fff')
    expect(window.parent.postMessage).toHaveBeenCalledWith(
      { type: 'dsh-desktop-theme', colorScheme: 'light' },
      '*',
    )
  })

  it('replaces owned tokens, applies dark mode, and cleans up on dispose', () => {
    const presenter = new DesktopThemePresenter()
    presenter.apply({ active: { colorScheme: 'light', tokens: { '--old-token': '#fff' } } })
    presenter.apply({ active: { colorScheme: 'dark', tokens: { '--new-token': '#000' } } })

    expect(document.body.style.getPropertyValue('--old-token')).toBe('')
    expect(document.body.style.getPropertyValue('--new-token')).toBe('#000')
    expect(document.body).toHaveAttribute('data-ds-dark-theme')
    expect(document.body.dataset.dshDesktopTheme).toBe('dark')

    presenter.dispose()

    expect(document.body.style.getPropertyValue('--new-token')).toBe('')
    expect(document.body).not.toHaveAttribute('data-ds-dark-theme')
    expect(document.body).not.toHaveAttribute('data-dsh-desktop-theme')
    expect(document.documentElement.style.colorScheme).toBe('')
  })

  it('leaves the current theme untouched when a snapshot is invalid', () => {
    const presenter = new DesktopThemePresenter()
    presenter.apply({ active: { colorScheme: 'light', tokens: { '--valid-token': '#fff' } } })
    vi.mocked(window.parent.postMessage).mockClear()

    presenter.apply({ active: { colorScheme: 'system', tokens: {} } })
    presenter.apply({ active: { colorScheme: 'dark', tokens: { '--invalid-token': 42 } } })

    expect(document.body.dataset.dshDesktopTheme).toBe('light')
    expect(document.body.style.getPropertyValue('--valid-token')).toBe('#fff')
    expect(window.parent.postMessage).not.toHaveBeenCalled()
  })
})
