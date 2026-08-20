export class DesktopThemePresenter {
  private appliedTokens: string[] = []

  apply(snapshot: unknown) {
    const theme = parseThemeSnapshot(snapshot)
    if (!theme) return

    for (const token of this.appliedTokens) document.body.style.removeProperty(token)
    this.appliedTokens = Object.keys(theme.tokens)

    document.documentElement.style.colorScheme = theme.colorScheme
    document.body.dataset.dshDesktopTheme = theme.colorScheme
    document.body.toggleAttribute('data-ds-dark-theme', theme.colorScheme === 'dark')
    for (const [token, value] of Object.entries(theme.tokens)) {
      document.body.style.setProperty(token, value)
    }

    window.parent.postMessage(
      { type: 'dsh-desktop-theme', colorScheme: theme.colorScheme },
      '*',
    )
  }

  dispose() {
    for (const token of this.appliedTokens) document.body.style.removeProperty(token)
    this.appliedTokens = []
    document.documentElement.style.removeProperty('color-scheme')
    document.body.removeAttribute('data-ds-dark-theme')
    document.body.removeAttribute('data-dsh-desktop-theme')
  }
}

type ParsedTheme = {
  colorScheme: 'light' | 'dark'
  tokens: Record<string, string>
}

function parseThemeSnapshot(snapshot: unknown): ParsedTheme | null {
  if (!isRecord(snapshot) || !isRecord(snapshot.active)) return null
  const { colorScheme, tokens } = snapshot.active
  if ((colorScheme !== 'light' && colorScheme !== 'dark') || !isRecord(tokens)) return null

  for (const [token, value] of Object.entries(tokens)) {
    if (!token.startsWith('--') || typeof value !== 'string') return null
  }

  return { colorScheme, tokens: tokens as Record<string, string> }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
