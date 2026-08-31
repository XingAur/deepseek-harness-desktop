import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { tauriRuntimeClient } from './runtime-client'
import { tauriWindowControls } from './window-client'
import './app.css'

const container = document.getElementById('root')
if (container === null) throw new Error('Missing #root mount point')

async function bootstrap(rootContainer: HTMLElement) {
  if (import.meta.env.MODE === 'e2e') {
    await import('@wdio/tauri-plugin')
  }

  createRoot(rootContainer).render(
    <StrictMode>
      <App runtime={tauriRuntimeClient} windowControls={tauriWindowControls} />
    </StrictMode>,
  )

  // Keep a static loading surface visible until React has mounted. If the
  // bundle cannot execute, the user sees a diagnosable loading surface rather
  // than an otherwise empty WebView.
  requestAnimationFrame(() => document.getElementById('bootstrap-fallback')?.remove())
}

void bootstrap(container)
