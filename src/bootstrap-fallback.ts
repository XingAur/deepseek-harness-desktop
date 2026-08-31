function bootstrapErrorMessage(cause: unknown): string {
  if (cause instanceof Error && cause.message.trim() !== '') return cause.message
  if (typeof cause === 'string' && cause.trim() !== '') return cause
  return '未知启动错误'
}

export function removeBootstrapFallback(): void {
  document.getElementById('bootstrap-fallback')?.remove()
}

export function showBootstrapFailure(cause: unknown): void {
  const existing = document.getElementById('bootstrap-fallback')
  const fallback = existing ?? document.createElement('main')
  fallback.id = 'bootstrap-fallback'
  fallback.setAttribute('role', 'alert')
  fallback.setAttribute('aria-live', 'assertive')
  fallback.style.cssText = [
    'position:fixed',
    'inset:0',
    'box-sizing:border-box',
    'display:flex',
    'flex-direction:column',
    'align-items:flex-start',
    'justify-content:center',
    'gap:12px',
    'padding:32px',
    'background:#111113',
    'color:#f7f7f8',
    "font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
    'z-index:9999',
  ].join(';')

  const title = document.createElement('strong')
  title.style.cssText = 'font-size:20px;line-height:1.4'
  title.textContent = 'DeepSeek Harness 启动失败'

  const guidance = document.createElement('p')
  guidance.style.cssText = 'max-width:680px;margin:0;color:#a7a7ae;font-size:14px;line-height:1.6'
  guidance.textContent = '工作台没有正常加载，但应用仍保留了可见的诊断页面。请重新打开应用；如果仍然出现，请导出诊断信息。'

  const detail = document.createElement('pre')
  detail.style.cssText = 'max-width:100%;margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;color:#ffb4b4;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace'
  detail.textContent = bootstrapErrorMessage(cause)

  fallback.replaceChildren(title, guidance, detail)
  if (existing === null) document.body.append(fallback)
}
