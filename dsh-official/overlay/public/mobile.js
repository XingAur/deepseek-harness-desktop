const CLIENT_KEY = 'dsh-mobile-client'
const CLIENT_ID = (() => {
  let id = localStorage.getItem(CLIENT_KEY)
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now())
    localStorage.setItem(CLIENT_KEY, id)
  }
  return id
})()

const app = document.getElementById('app')
let view = { kind: 'list' }
let sessionCache = new Map()
let rendering = false

function api(name, search = '') {
  return new URL(`../api/desktop/mobile/${name}${search}`, location.href).href
}

async function call(name, init = {}) {
  const search = init.search || ''
  const { search: _ignored, ...rest } = init
  return await fetch(api(name, search), {
    ...rest,
    credentials: 'same-origin',
    redirect: 'error',
    headers: {
      accept: 'application/json',
      'x-dsh-mobile-client': CLIENT_ID,
      ...(rest.headers || {}),
    },
  })
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag)
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === 'on' && value) {
      for (const [event, listener] of Object.entries(value)) el.addEventListener(event, listener)
    } else if (value != null) {
      el.setAttribute(key, String(value))
    }
  }
  for (const kid of kids.flat()) {
    if (kid == null) continue
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)))
  }
  return el
}

function button(label, className, listener, icon) {
  return h('button', { type: 'button', class: className, on: { click: listener } },
    icon ? h('span', { class: 'buttonIcon', 'aria-hidden': 'true' }, icon) : null,
    label,
  )
}

function topbar(options = {}) {
  const leading = options.back
    ? h('button', { type: 'button', class: 'iconButton', 'aria-label': '返回', on: { click: options.back } }, '‹')
    : h('span', { class: 'brandMark', 'aria-hidden': 'true' }, 'D')
  return h('header', { class: `topbar${options.conversation ? ' conversationHeader' : ''}` },
    h('div', { class: 'brand' },
      leading,
      h('div', { class: 'brandText' },
        h('div', { class: 'brandTitle' }, options.title || 'DeepSeek Harness'),
        h('div', { class: 'brandSubtitle' }, options.subtitle || '手机控制'),
      ),
    ),
    h('div', { class: 'connectionPill' },
      h('span', { class: 'connectionDot' }),
      options.running ? '运行中' : '已连接',
    ),
  )
}

function formatRelative(value) {
  if (!Number.isFinite(value) || value <= 0) return '最近更新'
  const diff = Math.max(0, Date.now() - value)
  if (diff < 60_000) return '刚刚更新'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(value)
}

function formatClock(value) {
  if (!Number.isFinite(value) || value <= 0) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(value)
}

function sessionTitle(session) {
  return session?.title || '未命名会话'
}

function sessionMeta(session) {
  const pieces = [session.running ? '正在执行' : formatRelative(session.updatedAt)]
  if (session.cwd) pieces.push(session.cwd.split('/').filter(Boolean).pop() || session.cwd)
  return pieces.join(' · ')
}

function sessionRow(session) {
  return h('button', {
    type: 'button',
    class: 'session',
    on: { click: () => { view = { kind: 'session', id: session.id }; void render() } },
  },
  h('span', { class: `sessionState${session.running ? ' sessionStateRunning' : ''}` }),
  h('span', { class: 'sessionBody' },
    h('span', { class: 'sessionTitle' }, sessionTitle(session)),
    h('span', { class: 'sessionMeta' }, sessionMeta(session)),
  ),
  h('span', { class: 'sessionArrow', 'aria-hidden': 'true' }, '›'))
}

function interruptionCard(item) {
  const approval = item.kind === 'approval'
  return h('article', { class: 'interruptionCard' },
    h('div', { class: 'interruptionTitle' },
      h('span', { 'aria-hidden': 'true' }, approval ? '◇' : '?'),
      approval ? `等待批准${item.toolName ? ` · ${item.toolName}` : ''}` : '等待回答',
    ),
    approval ? h('div', { class: 'row' },
      button('允许', 'primaryButton', () => { void decide('approve', item.key, { action: 'allow' }) }),
      button('拒绝', 'secondaryButton dangerButton', () => { void decide('approve', item.key, { action: 'reject' }) }),
    ) : h('div', { class: 'row' }, h('span', { class: 'sessionMeta' }, '请在桌面端完成此问题')),
  )
}

async function loadList() {
  const res = await call('state')
  if (res.status === 403) {
    const body = await res.json().catch(() => ({}))
    if (body.error === 'link-in-use') {
      renderError('连接已在使用', '另一台设备正在控制此连接。请关闭另一台设备上的页面，稍后再试。')
      return
    }
  }
  if (!res.ok) throw new Error(`state ${res.status}`)
  const data = await res.json()
  const sessions = data.sessions || []
  const interruptions = data.interruptions || []
  sessionCache = new Map(sessions.map(session => [session.id, session]))

  app.replaceChildren(h('main', { class: 'appShell' },
    topbar(),
    h('section', { class: 'pageIntro' },
      h('p', { class: 'eyebrow' }, 'Remote workspace'),
      h('h1', {}, '继续你的任务'),
      h('p', {}, '在手机上查看会话、发送消息，并处理桌面端等待中的操作。'),
    ),
    h('section', { class: 'heroCard' },
      h('div', { class: 'heroCopy' },
        h('strong', {}, '桌面端已连接'),
        h('span', {}, '消息和任务状态会自动同步'),
      ),
      button('新建会话', 'primaryButton', () => { view = { kind: 'new' }; void render() }, '+'),
    ),
    interruptions.length ? h('section', { class: 'section' },
      h('div', { class: 'sectionHeader' }, h('h2', { class: 'sectionTitle' }, '需要处理', h('span', { class: 'sectionCount' }, interruptions.length))),
      h('div', { class: 'interruptionList' }, interruptions.map(interruptionCard)),
    ) : null,
    h('section', { class: 'section' },
      h('div', { class: 'sectionHeader' },
        h('h2', { class: 'sectionTitle' }, '会话', h('span', { class: 'sectionCount' }, sessions.length)),
        button('刷新', 'ghostButton', () => { void render() }),
      ),
      sessions.length
        ? h('div', { class: 'sessionList' }, sessions.map(sessionRow))
        : h('div', { class: 'emptyCard' },
            h('span', { class: 'emptyIcon', 'aria-hidden': 'true' }, '＋'),
            h('strong', {}, '还没有会话'),
            h('span', {}, '新建一个会话，从手机端开始任务。'),
          ),
    ),
  ))
}

function renderChatItem(item) {
  if (item.kind === 'user' || item.kind === 'assistant') {
    const user = item.kind === 'user'
    const imageNote = user && item.imageCount > 0 ? `\n[${item.imageCount} 张图片]` : ''
    return h('article', { class: `message${user ? ' messageUser' : ''}` },
      h('span', { class: 'avatar', 'aria-hidden': 'true' }, user ? '你' : 'D'),
      h('div', { class: 'messageColumn' },
        h('div', { class: 'messageLabel' }, user ? '你' : 'DeepSeek'),
        h('div', { class: 'messageBubble' }, `${item.text || ''}${imageNote}`),
        h('div', { class: 'messageTime' }, formatClock(item.time)),
      ),
    )
  }
  if (item.kind === 'reasoning') {
    return h('details', { class: 'richRow' },
      h('summary', { class: 'richSummary' }, '思考过程'),
      h('div', { class: 'richBody' }, item.text || ''),
    )
  }
  if (item.kind === 'tool') {
    const stateClass = item.status === 'running' ? ' toolStateRunning' : item.status === 'error' ? ' toolStateError' : ''
    return h('article', { class: 'richRow' },
      h('div', { class: 'richSummary' },
        h('span', { class: `toolState${stateClass}` }),
        h('span', {}, item.title || item.name || '工具调用'),
      ),
      item.diffs?.length ? h('div', { class: 'diffList' }, item.diffs.map(diff => h('div', { class: 'diffRow' },
        h('span', {}, diff.path || '文件'),
        h('span', { class: 'diffCount' },
          h('span', { class: 'diffAdd' }, `+${diff.added ?? 0}`),
          h('span', { class: 'diffRemove' }, `-${diff.removed ?? 0}`),
        ),
      ))) : null,
    )
  }
  if (item.kind === 'todo') {
    return h('article', { class: 'richRow' },
      h('div', { class: 'richSummary' }, '任务进度'),
      h('div', { class: 'todoList' }, (item.todos || []).map(todo => h('div', {
        class: `todoItem${todo.status === 'completed' ? ' todoDone' : ''}`,
      },
      h('span', { class: 'todoMark' }, todo.status === 'completed' ? '✓' : todo.status === 'in_progress' ? '◉' : '○'),
      h('span', {}, todo.content || ''),
      ))),
    )
  }
  return null
}

async function loadSession(id) {
  const session = sessionCache.get(id)
  const res = await call('transcript', { search: `?sessionId=${encodeURIComponent(id)}` })
  if (!res.ok) throw new Error(`transcript ${res.status}`)
  const data = await res.json()
  const box = h('textarea', { id: 'prompt', rows: '1', placeholder: '发消息或继续任务…', 'aria-label': '消息' })
  box.addEventListener('input', () => {
    box.style.height = 'auto'
    box.style.height = `${Math.min(box.scrollHeight, 132)}px`
  })
  const items = (data.messages || []).map(renderChatItem).filter(Boolean)
  const nearBottom = document.documentElement.scrollHeight - window.scrollY - window.innerHeight < 180
  app.replaceChildren(h('main', { class: 'appShell appShellConversation' },
    topbar({
      conversation: true,
      title: sessionTitle(session),
      subtitle: session?.cwd ? session.cwd.split('/').filter(Boolean).pop() : '会话',
      running: session?.running === true,
      back: () => { view = { kind: 'list' }; void render() },
    }),
    h('section', { class: 'conversation' },
      items.length ? items : h('div', { class: 'emptyCard' }, h('strong', {}, '开始对话'), h('span', {}, '在下方输入消息并发送。')),
    ),
    h('div', { class: 'composerWrap' },
      h('div', { class: 'composer' },
        box,
        h('div', { class: 'composerActions' },
          h('button', { type: 'button', class: 'stopButton', 'aria-label': '停止', on: { click: () => { void cancel(id) } } }, '■'),
          h('button', { type: 'button', class: 'sendButton', 'aria-label': '发送', on: { click: () => { void sendPrompt(id, box) } } }, '↑'),
        ),
      ),
    ),
  ))
  if (nearBottom) requestAnimationFrame(() => window.scrollTo({ top: document.documentElement.scrollHeight }))
}

function loadNewSession() {
  const box = h('textarea', { id: 'prompt', placeholder: '描述你想完成的任务…', 'aria-label': '新会话消息' })
  app.replaceChildren(h('main', { class: 'appShell' },
    topbar({ title: '新建会话', subtitle: '从手机端开始', back: () => { view = { kind: 'list' }; void render() } }),
    h('section', { class: 'pageIntro' },
      h('p', { class: 'eyebrow' }, 'New task'),
      h('h1', {}, '有什么可以帮你？'),
      h('p', {}, '任务会在桌面端工作区中创建，并同步回当前手机。'),
    ),
    h('section', { class: 'newSessionCard' },
      box,
      h('div', { class: 'newActions' }, button('创建并发送', 'primaryButton', () => { void createSession() }, '↑')),
    ),
  ))
  requestAnimationFrame(() => box.focus())
}

async function decide(path, key, extra) {
  await call(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ key, ...extra }),
  })
  await render()
}

async function sendPrompt(sessionId, box) {
  const content = box.value.trim()
  if (!content) return
  const res = await call('prompt', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId, content }),
  })
  if (!res.ok) throw new Error(`prompt ${res.status}`)
  box.value = ''
  await loadSession(sessionId)
}

async function cancel(sessionId) {
  const res = await call('cancel', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId }),
  })
  if (!res.ok) throw new Error(`cancel ${res.status}`)
  await loadSession(sessionId)
}

async function createSession() {
  const content = document.getElementById('prompt')?.value?.trim() || '你好'
  const res = await call('create', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!res.ok) throw new Error(`create ${res.status}`)
  const data = await res.json()
  sessionCache.set(data.sessionId, { id: data.sessionId, title: '新会话', running: true, updatedAt: Date.now(), cwd: null })
  view = { kind: 'session', id: data.sessionId }
  await render()
}

function renderError(title, message) {
  app.replaceChildren(h('main', { class: 'errorPage' },
    h('section', { class: 'errorCard' },
      h('span', { class: 'emptyIcon', 'aria-hidden': 'true' }, '!'),
      h('h2', {}, title),
      h('p', {}, message),
      button('重新连接', 'primaryButton', () => { view = { kind: 'list' }; void render() }),
    ),
  ))
}

async function render() {
  if (rendering) return
  rendering = true
  try {
    if (view.kind === 'list') await loadList()
    else if (view.kind === 'new') loadNewSession()
    else await loadSession(view.id)
  } catch (cause) {
    renderError('暂时无法连接', String(cause && cause.message ? cause.message : cause))
  } finally {
    rendering = false
  }
}

app.replaceChildren(h('main', { class: 'loadingPage' }, h('div', {}, h('div', { class: 'loadingMark' }), '正在连接桌面端…')))
void render()
setInterval(() => {
  const prompt = document.getElementById('prompt')
  if (document.visibilityState !== 'hidden' && !(prompt && (document.activeElement === prompt || prompt.value))) void render()
}, 4000)
