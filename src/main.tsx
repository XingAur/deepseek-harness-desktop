import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { tauriRuntimeClient } from './runtime-client'
import { tauriWindowControls } from './window-client'
import { BootstrapErrorBoundary } from './BootstrapErrorBoundary'
import { showBootstrapFailure } from './bootstrap-fallback'
import './app.css'

const container = document.getElementById('root')

async function bootstrap(rootContainer: HTMLElement) {
  if (import.meta.env.MODE === 'e2e') {
    await import('@wdio/tauri-plugin')
  }

  createRoot(rootContainer).render(
    <StrictMode>
      <BootstrapErrorBoundary>
        <App runtime={tauriRuntimeClient} windowControls={tauriWindowControls} />
      </BootstrapErrorBoundary>
    </StrictMode>,
  )
}

if (container === null) {
  showBootstrapFailure(new Error('Missing #root mount point'))
} else {
  // The static fallback is removed by BootstrapErrorBoundary only after React
  // commits. Import or render failures therefore keep a visible diagnostic
  // surface instead of leaving an empty WebView.
  void bootstrap(container).catch(showBootstrapFailure)
}
