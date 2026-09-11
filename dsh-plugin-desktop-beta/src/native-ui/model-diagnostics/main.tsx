import '../shared/theme.css'
import { CSPProvider } from '@base-ui/react/csp-provider'
import { createRoot } from 'react-dom/client'
import { ModelDiagnosticsApp } from './App.tsx'

const root = document.getElementById('root')
if (root === null) throw new Error('dsh-model-diagnostics: root element is missing')
createRoot(root).render(<CSPProvider disableStyleElements><ModelDiagnosticsApp /></CSPProvider>)
