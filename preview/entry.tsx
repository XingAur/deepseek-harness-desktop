import { createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { PrototypeMock } from './prototype-mock'

const scenarios: Record<string, string> = {
  '2a': '⑤ 原型 · 主聊天模型选择器：新增 Codex 分组 + 思考高度（设计稿，非真实界面）',
  '2b': '⑥ 原型 · 选中 Codex 后：输入框与状态条（思考高度：中）',
  '3a': '⑧ 原型 · 左侧菜单「插件」页：搜索、分类、推荐与卡片网格',
}

const state = location.hash.replace('#', '') || '2a'
const caption = scenarios[state] ?? scenarios['2a']

document.body.dataset.dshDesktopMode = 'advanced'
document.body.dataset.dshDesktopPlatform = 'darwin'

const banner = document.createElement('div')
banner.className = 'previewCaption'
banner.textContent = caption
document.body.appendChild(banner)

const surface = document.createElement('div')
surface.className = 'previewSurface'
document.body.appendChild(surface)

const kind = state === '2b' ? 'model-selected' : state === '3a' ? 'plugins' : 'model-picker'
createRoot(surface).render(createElement(PrototypeMock, { kind }))
