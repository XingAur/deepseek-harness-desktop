import '../shared/theme.css'
import './theme-override.css'
import { CSPProvider } from '@base-ui/react/csp-provider'
import { createRoot } from 'react-dom/client'
import { MobileApp } from './App.tsx'

const root = document.getElementById('root')
if (root === null) throw new Error('dsh-mobile: root element is missing')
createRoot(root).render(<CSPProvider disableStyleElements><MobileApp /></CSPProvider>)
