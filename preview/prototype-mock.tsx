import { useEffect, useState } from 'react'

/**
 * 原型设计稿（非真实界面）：主聊天模型选择器扩展 Codex + 思考高度，
 * 以及插件页布局。配色取官方 DeepSeek 蓝/紫，深色主题。
 */

let injected = false
function injectProtoStyles() {
  if (injected) return
  injected = true
  const style = document.createElement('style')
  style.textContent = `
    :root { --pv-bg: #141519; --pv-panel: #1b1c20; --pv-panel2: #222328; --pv-line: #2c2e36; --pv-text: #e7e9ee; --pv-dim: #8b8fa0; --pv-blue: #6187D8; --pv-violet: #a78bfa; --pv-green: #57c285; }
    body { background: var(--pv-bg); }
    .pv-shell { display: grid; grid-template-columns: 220px 1fr; gap: 0; height: 100%; min-height: 780px; border: 1px solid var(--pv-line); border-radius: 14px; overflow: hidden; }
    .pv-sidebar { display: flex; flex-direction: column; background: #17181c; border-right: 1px solid var(--pv-line); padding: 14px 10px; gap: 4px; }
    .pv-logo { color: var(--pv-text); font-size: 14px; font-weight: 700; padding: 4px 8px 16px; }
    .pv-nav-item { display: flex; align-items: center; gap: 10px; min-height: 34px; padding: 0 10px; border-radius: 8px; color: var(--pv-dim); font-size: 13px; }
    .pv-nav-item.is-active { background: rgba(97,135,216,.14); color: var(--pv-text); }
    .pv-nav-item.is-plugins { outline: 1.5px dashed var(--pv-violet); outline-offset: -1px; color: var(--pv-violet); }
    .pv-nav-spacer { flex: 1; }
    .pv-main { display: flex; flex-direction: column; min-width: 0; }
    .pv-chat { flex: 1; padding: 28px 34px; overflow: auto; }
    .pv-day { text-align: center; color: var(--pv-dim); font-size: 12px; margin-bottom: 18px; }
    .pv-row { display: flex; margin-bottom: 18px; }
    .pv-row.is-user { justify-content: flex-end; }
    .pv-bubble { max-width: 70%; padding: 11px 14px; border-radius: 13px; font-size: 14px; line-height: 1.7; background: var(--pv-panel2); border: 1px solid var(--pv-line); }
    .pv-row.is-user .pv-bubble { background: var(--pv-blue); color: #fff; border-color: transparent; }
    .pv-composer { border-top: 1px solid var(--pv-line); padding: 12px 16px 14px; background: #16171b; }
    .pv-picker-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; position: relative; }
    .pv-picker { display: inline-flex; align-items: center; gap: 8px; min-height: 32px; padding: 0 12px; border: 1px solid var(--pv-line); border-radius: 9px; background: var(--pv-panel2); color: var(--pv-text); font-size: 13px; cursor: pointer; }
    .pv-picker .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--pv-violet); }
    .pv-caret { width: 13px; height: 13px; stroke: var(--pv-dim); }
    .pv-picker-note { color: var(--pv-dim); font-size: 12px; }
    .pv-picker-note b { color: var(--pv-violet); }
    .pv-menu { position: absolute; top: 38px; left: 0; z-index: 40; width: 340px; padding: 8px; border: 1px solid var(--pv-line); border-radius: 12px; background: #1e1f24; box-shadow: 0 16px 48px rgba(0,0,0,.55); }
    .pv-group { display: flex; align-items: center; justify-content: space-between; padding: 7px 10px 4px; color: var(--pv-dim); font-size: 11px; letter-spacing: .6px; text-transform: uppercase; }
    .pv-group .new { color: var(--pv-violet); text-transform: none; letter-spacing: 0; font-weight: 600; }
    .pv-opt { display: flex; align-items: center; gap: 9px; min-height: 40px; padding: 6px 10px; border-radius: 9px; color: var(--pv-text); font-size: 13.5px; }
    .pv-opt:hover { background: #292a31; }
    .pv-opt.is-sel { background: rgba(97,135,216,.16); }
    .pv-opt .dot { width: 9px; height: 9px; border-radius: 50%; }
    .pv-opt .meta { margin-left: auto; color: var(--pv-dim); font-size: 11px; }
    .pv-opt small { display: block; color: var(--pv-dim); font-size: 11px; }
    .pv-effort { margin-top: 6px; border-top: 1px solid var(--pv-line); padding: 8px 10px 4px; }
    .pv-effort-label { color: var(--pv-dim); font-size: 11px; margin-bottom: 6px; }
    .pv-effort-label b { color: var(--pv-text); }
    .pv-effort-row { display: flex; gap: 6px; }
    .pv-effort-chip { flex: 1; min-height: 28px; border: 1px solid var(--pv-line); border-radius: 8px; color: var(--pv-dim); background: transparent; font-size: 12px; cursor: pointer; }
    .pv-effort-chip.is-sel { border-color: var(--pv-violet); color: var(--pv-violet); background: rgba(167,122,250,.1); }
    .pv-input { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 0 14px; border: 1px solid var(--pv-line); border-radius: 12px; background: var(--pv-panel2); color: var(--pv-dim); font-size: 13.5px; }
    .pv-input .ph { color: var(--pv-dim); }
    .pv-input .send { margin-left: auto; color: var(--pv-blue); font-weight: 600; }
    /* 插件页 */
    .pv-plugins { display: grid; gap: 18px; padding: 30px 36px 44px; }
    .pv-ph { display: flex; align-items: baseline; gap: 14px; }
    .pv-ph h2 { margin: 0; font-size: 22px; color: var(--pv-text); }
    .pv-ph p { margin: 0; color: var(--pv-dim); font-size: 13px; }
    .pv-warn { padding: 10px 13px; border-radius: 10px; color: #e6c68b; background: rgba(230,168,80,.12); font-size: 12.5px; line-height: 1.6; }
    .pv-search { display: flex; gap: 10px; align-items: center; }
    .pv-search input { flex: 0 1 360px; min-height: 34px; padding: 0 12px; border: 1px solid var(--pv-line); border-radius: 9px; background: var(--pv-panel2); color: var(--pv-text); font-size: 13px; }
    .pv-chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .pv-chip { min-height: 26px; padding: 0 11px; border: 1px solid var(--pv-line); border-radius: 999px; color: var(--pv-dim); background: transparent; font-size: 11.5px; cursor: pointer; }
    .pv-chip.is-sel { border-color: var(--pv-violet); color: var(--pv-violet); background: rgba(167,122,250,.1); }
    .pv-featured { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 14px 16px; border: 1px solid rgba(167,122,250,.4); border-radius: 13px; background: linear-gradient(120deg, rgba(97,135,216,.14), transparent); }
    .pv-featured b { color: var(--pv-text); font-size: 13.5px; }
    .pv-featured p { margin: 3px 0 0; color: var(--pv-dim); font-size: 12px; }
    .pv-featured button { min-height: 32px; padding: 0 14px; border: 0; border-radius: 9px; background: var(--pv-blue); color: #fff; font-size: 12.5px; font-weight: 600; cursor: pointer; }
    .pv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
    .pv-card { padding: 14px 15px; border: 1px solid var(--pv-line); border-radius: 12px; background: var(--pv-panel); }
    .pv-card h4 { margin: 0 0 3px; font-size: 13.5px; color: var(--pv-text); }
    .pv-card .tag { color: var(--pv-dim); font-size: 11px; }
    .pv-card p { margin: 7px 0 10px; color: var(--pv-dim); font-size: 12px; line-height: 1.6; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .pv-card button { min-height: 30px; padding: 0 12px; border: 0; border-radius: 8px; background: var(--pv-blue); color: #fff; font-size: 12px; cursor: pointer; }
    .pv-annot { color: var(--pv-violet); font-size: 11px; }
    .pv-tag-new { display: inline-block; padding: 1px 7px; border-radius: 999px; color: var(--pv-violet); border: 1px solid rgba(167,122,250,.5); font-size: 10.5px; margin-left: 6px; }
  `
  document.head.appendChild(style)
}

const DOT_COLORS: Record<string, string> = {
  deepseek: '#6187D8', codex: '#a78bfa', effort: '#57c285',
}

export function PrototypeMock({ kind }: { kind: 'model-picker' | 'model-selected' | 'plugins' }) {
  useEffect(() => { injectProtoStyles() }, [])
  const [pickerOpen, setPickerOpen] = useState(kind === 'model-picker')
  const [selected, setSelected] = useState<'deepseek' | 'codex'>(kind === 'model-selected' ? 'codex' : 'deepseek')

  if (kind === 'plugins') return <PluginsPrototype />

  return (
    <div className="pv-shell">
      <aside className="pv-sidebar">
        <div className="pv-logo">DeepSeek Harness</div>
        <div className="pv-nav-item is-active">会话</div>
        <div className="pv-nav-item">本地项目</div>
        <div className="pv-nav-item is-plugins">插件<span className="pv-tag-new">新</span></div>
        <div className="pv-nav-spacer" />
        <div className="pv-nav-item">设置</div>
      </aside>
      <main className="pv-main">
        <div className="pv-chat">
          <div className="pv-day">今天</div>
          <div className="pv-row is-user"><div className="pv-bubble">帮我看一下这个项目的结构</div></div>
          <div className="pv-row"><div className="pv-bubble">这个项目是一个 Tauri 2 + React 的桌面应用，主要包含 src/、src-tauri/ 和 packages/ 三个部分…</div></div>
        </div>
        <div className="pv-composer">
          <div className="pv-picker-row">
            <div className="pv-picker" onClick={() => setPickerOpen((v) => !v)}>
              <span className="dot" style={{ background: DOT_COLORS[selected] }} />
              {selected === 'deepseek' ? 'DeepSeek Chat' : 'Codex'}
              <svg className="pv-caret" viewBox="0 0 24 24" fill="none"><path d="m6 9 6 6 6-6" /></svg>
            </div>
            {selected === 'codex' && (
              <span className="pv-picker-note">Codex · 在会话目录的官方沙箱内执行 <b>思考高度：中</b></span>
            )}
            {pickerOpen && (
              <div className="pv-menu">
                <div className="pv-group">DeepSeek</div>
                <div className={`pv-opt${selected === 'deepseek' ? ' is-sel' : ''}`} onClick={() => { setSelected('deepseek'); setPickerOpen(false) }}>
                  <span className="dot" style={{ background: DOT_COLORS.deepseek }} />
                  DeepSeek Chat<span className="meta">默认</span>
                </div>
                <div className="pv-opt" onClick={() => setPickerOpen(false)}>
                  <span className="dot" style={{ background: DOT_COLORS.deepseek }} />
                  DeepSeek Reasoner<span className="meta">深度思考</span>
                </div>
                <div className="pv-group">Codex <span className="new">接入 · 新</span></div>
                <div className={`pv-opt${selected === 'codex' ? ' is-sel' : ''}`} onClick={() => { setSelected('codex'); setPickerOpen(false) }}>
                  <span className="dot" style={{ background: DOT_COLORS.codex }} />
                  <span>Codex<small>OpenAI 官方 CLI · 在会话目录沙箱内执行任务</small></span>
                </div>
                <div className="pv-effort">
                  <div className="pv-effort-label">思考高度 <b>（仅 Codex）</b></div>
                  <div className="pv-effort-row">
                    <button type="button" className="pv-effort-chip">低</button>
                    <button type="button" className="pv-effort-chip is-sel">中</button>
                    <button type="button" className="pv-effort-chip">高</button>
                  </div>
                </div>
              </div>
            )}
          </div>
          <div className="pv-input">
            <span className="ph">{selected === 'codex' ? '继续对话，Codex 会保持上下文…' : '给 DeepSeek 发消息…'}</span>
            <span className="send">发送</span>
          </div>
        </div>
      </main>
    </div>
  )
}

function PluginsPrototype() {
  const cards = [
    { name: 'dsh-project-memory', tag: 'memory', desc: '项目记忆：文件读取时索引为可检索摘要，上下文失效后按需检索，无需重读文件。' },
    { name: 'dsh-api-balance', tag: 'usage', desc: '输入框下方实时显示 DeepSeek API 账户余额。' },
    { name: 'dsh-discord-richpresence', tag: 'notify', desc: '在 Discord 展示当前会话状态。' },
    { name: 'dsh-plan-lattice', tag: 'workflow', desc: '计划网格：把任务拆解为可追踪的网格视图。' },
  ]
  return (
    <div className="pv-shell" style={{ gridTemplateColumns: '220px 1fr' }}>
      <aside className="pv-sidebar">
        <div className="pv-logo">DeepSeek Harness</div>
        <div className="pv-nav-item">会话</div>
        <div className="pv-nav-item">本地项目</div>
        <div className="pv-nav-item is-active is-plugins">插件<span className="pv-tag-new">新</span></div>
        <div className="pv-nav-spacer" />
        <div className="pv-nav-item">设置</div>
      </aside>
      <main className="pv-plugins">
        <div className="pv-ph">
          <h2>插件</h2>
          <p>社区插件（1946 个）· 已装扩展在本页下方审核与启停</p>
        </div>
        <div className="pv-warn">安装插件等于在你的机器上运行第三方代码，权限与你本人一样大。收录不代表安全审查——装前请先看源码。</div>
        <div className="pv-search">
          <input placeholder="搜索插件：名称、分类或描述…" />
          <div className="pv-chips">
            <button type="button" className="pv-chip is-sel">全部 · 1946</button>
            <button type="button" className="pv-chip">ui · 307</button>
            <button type="button" className="pv-chip">tools · 249</button>
            <button type="button" className="pv-chip">dev · 165</button>
            <button type="button" className="pv-chip">session · 134</button>
          </div>
        </div>
        <div className="pv-featured">
          <div>
            <b>dsh-market <span className="pv-tag-new">推荐</span></b>
            <p>官方生态的完整市场插件：一键安装/升级插件、一键切换主题。</p>
          </div>
          <button type="button">安装</button>
        </div>
        <div className="pv-grid">
          {cards.map((card) => (
            <div className="pv-card" key={card.name}>
              <h4>{card.name}<span className="tag"> · {card.tag}</span></h4>
              <p>{card.desc}</p>
              <button type="button">安装</button>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
