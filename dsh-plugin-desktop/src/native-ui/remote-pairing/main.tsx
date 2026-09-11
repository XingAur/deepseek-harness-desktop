import '../shared/theme.css'
import { CSPProvider } from '@base-ui/react/csp-provider'
import { createRoot } from 'react-dom/client'
import { RemotePairingApp } from './App.tsx'

const root = document.getElementById('root')
if (root === null) throw new Error('dsh-remote-pairing: root element is missing')
createRoot(root).render(<CSPProvider disableStyleElements><RemotePairingApp /></CSPProvider>)
