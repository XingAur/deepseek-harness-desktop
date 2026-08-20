import { useState } from 'react'
import type { MigrationStatus } from './runtime-contract'

interface MigrationPromptProps {
  migration: Exclude<MigrationStatus, { phase: 'ready' }>
  onConfirm(): Promise<void>
  onDefer(): Promise<void>
}

export function MigrationPrompt({ migration, onConfirm, onDefer }: MigrationPromptProps) {
  const [busy, setBusy] = useState(false)
  const size = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(migration.bytes / 1024 / 1024)
  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    try { await action() } finally { setBusy(false) }
  }

  return (
    <section className="migrationPanel" aria-live="polite">
      <p className="eyebrow">DEEPSEEK HARNESS DESKTOP · 数据迁移</p>
      <h1>{migration.phase === 'conflict' ? '需要选择数据目录' : '发现旧版桌面数据'}</h1>
      <p className="statusMessage">
        {migration.phase === 'conflict'
          ? '新旧目录中都有数据，已停止自动合并。请导出诊断后手动确认。'
          : '迁移完成前不会启动 Runtime；源目录会保留完整备份。'}
      </p>
      <dl className="migrationSummary">
        <div><dt>来源</dt><dd>{migration.source}</dd></div>
        <div><dt>目标</dt><dd>{migration.target}</dd></div>
        <div><dt>内容</dt><dd>{size} MB · {migration.profiles} 个 Profile · {migration.workspaces} 个工作区</dd></div>
      </dl>
      <div className="actionRow">
        {migration.phase === 'candidate' && (
          <button className="primaryButton" disabled={busy} onClick={() => void run(onConfirm)}>备份并迁移</button>
        )}
        <button className="secondaryButton" disabled={busy} onClick={() => void run(onDefer)}>稍后处理</button>
      </div>
    </section>
  )
}
