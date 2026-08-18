const STYLE_ID = 'dsh-desktop-advanced-styles'

export function installAdvancedStyles(): () => void {
  const previous = document.getElementById(STYLE_ID)
  previous?.remove()
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
    html, body, #root { width: 100%; height: 100%; margin: 0; overflow: hidden; background: transparent !important; }
    body[data-dsh-desktop-mode="advanced"] { color: var(--dsh-desktop-foreground, #f4f4f5); }
    .dshDesktopFrame { position: relative; display: grid; width: 100%; height: 100%; min-width: 0; background: var(--dsh-desktop-background, #151517); }
    .dshDesktopMacCaptionRow, .dshDesktopWindowsCaptionRow { position: absolute; inset: 0 0 auto 0; height: 48px; z-index: 5; pointer-events: none; }
    .dshDesktopSidebarSurface, .dshDesktopConversationSurface, .dshDesktopDetailsSurface { min-width: 0; min-height: 0; overflow: hidden; border-color: rgba(255,255,255,.08); }
    .dshDesktopSidebarSurface { position: relative; display: flex; flex-direction: column; background: rgba(44,44,47,.94); border-right: 1px solid rgba(255,255,255,.08); padding-top: 48px; }
    [data-desktop-platform="darwin"] .dshDesktopSidebarSurface { padding-top: 58px; background: rgba(38,38,41,.82); }
    .dshDesktopUpstreamSidebar { flex: 1; min-height: 0; }
    .dshDesktopConversationSurface { background: rgba(20,20,22,.96); padding-top: 48px; }
    .dshDesktopDetailsSurface { background: rgba(28,28,31,.96); border-left: 1px solid rgba(255,255,255,.08); padding-top: 48px; }
    .dshDesktopOverlay { position: absolute; inset: 0; pointer-events: none; z-index: 30; }
    .dshDesktopOverlay > * { pointer-events: auto; }
    .dshDesktopResizeHandle { position: absolute; top: 48px; bottom: 0; width: 7px; margin-left: -3px; z-index: 20; cursor: col-resize; }
    .dshDesktopMarketEntry { margin: 10px 12px 14px; padding: 10px 12px; border: 1px solid rgba(255,255,255,.10); border-radius: 10px; color: #d7d7dc; background: rgba(255,255,255,.045); text-align: left; cursor: pointer; }
    .dshDesktopMarketEntry:hover, .dshDesktopMarketEntry[data-active="true"] { border-color: #6f8fe9; background: rgba(100,130,220,.16); }
    .marketPage { height: 100%; overflow: auto; padding: 34px 38px 56px; color: #ececf0; background: #151517; }
    .marketHeader { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding-bottom: 26px; border-bottom: 1px solid rgba(255,255,255,.08); }
    .marketHeader p { margin: 0 0 8px; color: #7196ff; font-size: 11px; font-weight: 700; letter-spacing: .14em; }
    .marketHeader h1 { margin: 0; font-size: 30px; font-weight: 600; }
    .marketHeader span { display: block; margin-top: 8px; color: #85858d; font-size: 13px; }
    .marketHeader button { border: 0; color: #8d8d95; background: transparent; font-size: 28px; cursor: pointer; }
    .marketToolbar { display: flex; gap: 10px; margin: 25px 0; }
    .marketToolbar input { flex: 1; min-height: 42px; padding: 0 14px; border: 1px solid #34343a; border-radius: 10px; color: #eee; background: #202023; outline: none; }
    .marketToolbar button, .marketActions button, .marketCard > button { min-height: 40px; padding: 0 16px; border: 1px solid #4a4a51; border-radius: 10px; color: #e7e7ec; background: #29292e; cursor: pointer; }
    .marketGrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
    .marketCard { padding: 20px; border: 1px solid #303035; border-radius: 15px; background: #1d1d20; }
    .marketCardTitle { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .marketCard h2 { margin: 0; font-size: 16px; }
    .marketCardTitle span { padding: 3px 7px; border-radius: 6px; color: #9ab2f5; background: rgba(91,121,205,.18); font-size: 10px; }
    .marketCard p { min-height: 42px; color: #9b9ba3; line-height: 1.5; }
    .marketCard small { display: block; margin-bottom: 18px; color: #686871; }
    .marketActions { display: flex; gap: 8px; }
    .marketActions button { flex: 1; }
    .marketActions .marketRemove { color: #e7b2b2; border-color: #674545; }
    .marketActions button:disabled, .marketCard > button:disabled { opacity: .55; cursor: wait; }
    .marketLogs { max-height: 145px; overflow: auto; margin: 12px 0 0; padding: 10px; border-radius: 8px; color: #adbde8; background: #111113; font: 11px/1.5 ui-monospace, monospace; white-space: pre-wrap; }
    .marketCard .marketCancel { margin-top: 8px; border-color: #6d4545; }
    .marketError, .marketEmpty { padding: 22px; border-radius: 12px; color: #a1a1a9; background: #1d1d20; }
    .marketError { color: #ff9c9c; border: 1px solid #633b3b; }
  `
  document.head.append(style)
  return () => style.remove()
}
