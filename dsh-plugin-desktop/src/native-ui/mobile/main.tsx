import '../shared/theme.css'
import './theme-override.css'
import { CSPProvider } from '@base-ui/react/csp-provider'
import { createRoot } from 'react-dom/client'
import { MobileApp } from './App.tsx'

// Align the static zh-CN document with the runtime-detected UI locale.
document.documentElement.lang = navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'

const root = document.getElementById('root')
if (root === null) throw new Error('dsh-mobile: root element is missing')
createRoot(root).render(<CSPProvider disableStyleElements><MobileApp /></CSPProvider>)
