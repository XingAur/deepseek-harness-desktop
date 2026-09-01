import { useCallback, useEffect, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'
import {
  barWidthPercent, fetchUsageSummary, formatTokens, groupByDay, groupByModel,
  type UsageSummary,
} from './usage-api'

/** 用量统计面板:汇总卡 + 按日条形图 + 按模型表格;数据来自壳层全量扫描会话 JSONL。 */
export function UsagePanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setSummary(await fetchUsageSummary(bridge))
  }, [bridge])

  useEffect(() => {
    void load().then(() => setLoaded(true)).catch((cause: unknown) => { setError(messageOf(cause)); setLoaded(true) })
  }, [load])

  const refresh = useCallback(async () => {
    setBusy(true)
    try {
      await load()
      setError(null)
    } catch (cause: unknown) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }, [load])

  const days = groupByDay(summary?.entries ?? [])
  const models = groupByModel(summary?.entries ?? [])
  const maxDayOutput = Math.max(0, ...days.map((day) => day.outputTokens))

  return (
    <div className="dshUsage">
      <div className="dshUsageToolbar">
        <span className="dshUsageMuted">统计各 Profile 会话记录中的 token 用量(MVP 每次全量扫描)</span>
        <span className="dshUsageSpacer" />
        <button type="button" disabled={busy} onClick={() => void refresh()}>刷新</button>
      </div>
      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
      {summary !== null && summary.failures.length > 0 && (
        <div className="dshUsageWarning" role="alert">
          部分{summary.failures.length}个文件无法统计：{summary.failures.slice(0, 3).join('；')}{summary.failures.length > 3 ? '…' : ''}
        </div>
      )}
      {summary !== null && (
        <div className="dshUsageCards" role="group" aria-label="用量汇总">
          <div className="dshUsageCard"><span className="dshUsageCardLabel">请求数</span><span className="dshUsageCardValue">{summary.totals.requests}</span></div>
          <div className="dshUsageCard"><span className="dshUsageCardLabel">输入 tokens</span><span className="dshUsageCardValue">{formatTokens(summary.totals.inputTokens)}</span></div>
          <div className="dshUsageCard"><span className="dshUsageCardLabel">输出 tokens</span><span className="dshUsageCardValue">{formatTokens(summary.totals.outputTokens)}</span></div>
          <div className="dshUsageCard"><span className="dshUsageCardLabel">扫描会话数</span><span className="dshUsageCardValue">{summary.sessionsScanned}</span></div>
        </div>
      )}
      {summary !== null && days.length > 0 && (
        <section className="dshUsageSection" aria-label="按日用量">
          <h3>按日用量(输出 tokens)</h3>
          <ul className="dshUsageDays">
            {days.map((day) => (
              <li key={day.day} className="dshUsageDay">
                <span className="dshUsageDayLabel">{day.day}</span>
                <span className="dshUsageDayTrack">
                  <span className="dshUsageDayBar" style={{ width: `${barWidthPercent(day.outputTokens, maxDayOutput)}%` }} />
                </span>
                <span className="dshUsageDayValue">{formatTokens(day.outputTokens)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {summary !== null && models.length > 0 && (
        <section className="dshUsageSection" aria-label="按模型用量">
          <h3>按模型用量</h3>
          <table className="dshUsageTable">
            <thead>
              <tr><th scope="col">模型</th><th scope="col">请求</th><th scope="col">输入</th><th scope="col">输出</th></tr>
            </thead>
            <tbody>
              {models.map((row) => (
                <tr key={row.model}>
                  <td>{row.model}</td>
                  <td>{row.requests}</td>
                  <td>{formatTokens(row.inputTokens)}</td>
                  <td>{formatTokens(row.outputTokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {loaded && summary !== null && days.length === 0 && (
        <p className="dshUsageMuted">还没有可用量数据;开始会话后点「刷新」。</p>
      )}
    </div>
  )
}
