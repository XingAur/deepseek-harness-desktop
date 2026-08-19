import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { tauriRuntimeClient } from './runtime-client'
import { tauriWindowControls } from './window-client'
import './app.css'

const container = document.getElementById('root')
if (container === null) throw new Error('Missing #root mount point')

createRoot(container).render(
  <StrictMode>
    <App runtime={tauriRuntimeClient} windowControls={tauriWindowControls} />
  </StrictMode>,
)
