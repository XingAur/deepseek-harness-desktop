import type { ExtensionReviewItem } from '../extensions/ExtensionReviewDialog'
import type { ConnectionProfile } from './connection-model'
import type { ProviderMetadata, ProviderState } from './state'

export interface DiagnosticsPanelProps {
  runtime: { state: string } | null
  providers: Array<{ provider: ProviderMetadata; state: ProviderState }>
  capabilities: Array<{ id: string; displayName: string; mutating: boolean; approvalRequired: boolean }>
  extensions: ExtensionReviewItem[]
  connections: ConnectionProfile[]
  errors: string[]
}

export function DiagnosticsPanel({ runtime, providers, capabilities, extensions, connections, errors }: DiagnosticsPanelProps) {
  const skills = extensions.filter((item) => item.extensionKind === 'skill')
  const mcp = extensions.filter((item) => item.extensionKind === 'mcp')
  return (
    <div className="dshModelAgentDiagnostics">
      {errors.map((error) => <div className="dshModelAgentError" role="alert" key={error}>{error}</div>)}
      <DiagnosticGroup title="官方 Runtime 与依赖">
        <DiagnosticRow label="DeepSeek Harness Runtime" state={runtime === null ? '未加载' : runtime.state === 'failed' ? '失败' : runtime.state === 'running' ? '运行中' : '已就绪'} detail={runtime === null ? 'Runtime 状态未返回' : `当前状态：${runtime.state}`} />
      </DiagnosticGroup>
      <DiagnosticGroup title="执行器">
        {providers.length === 0 ? <Empty>暂无已加载的执行器状态。</Empty> : providers.map(({ provider, state }) => <DiagnosticRow key={provider.providerId} label={provider.displayName} state={state.label} detail={provider.credentialSupported && state.kind === 'not-configured' ? '下一步：配置凭证或完成官方登录' : `适配协议：${provider.adapterProtocol}`} />)}
      </DiagnosticGroup>
      <DiagnosticGroup title="技能与 MCP">
        <DiagnosticRow label="技能清单" state={`${skills.length} 项`} detail={skills.length === 0 ? '未加载到技能；可前往插件市场安装。' : `${skills.filter((item) => item.status === 'enabled').length} 项已启用`} />
        <DiagnosticRow label="MCP 清单" state={`${mcp.length} 项`} detail={mcp.length === 0 ? '未加载到插件提供的 MCP。' : `${mcp.filter((item) => item.status === 'enabled').length} 项已启用；权限需逐项审核`} />
        <DiagnosticRow label="能力审批" state={`${capabilities.filter((item) => item.approvalRequired).length} 项需审批`} detail={`${capabilities.length} 项能力已加载`} />
        {capabilities.map((capability) => <DiagnosticRow key={capability.id} label={capability.displayName} state={capability.approvalRequired ? '需要审批' : capability.mutating ? '受管执行' : '只读'} detail={`能力 ID：${capability.id}`} />)}
      </DiagnosticGroup>
      <DiagnosticGroup title="连接">
        {connections.length === 0 ? <Empty>暂无连接诊断。进入“MCP 与连接”加载或新增连接。</Empty> : connections.map((connection) => <DiagnosticRow key={connection.profileId} label={connection.displayName} state={connection.latestTest.summary} detail={failedLayer(connection) ?? '分层结果中未报告失败；未测试层不代表成功。'} />)}
      </DiagnosticGroup>
    </div>
  )
}

function DiagnosticGroup({ title, children }: { title: string; children: React.ReactNode }) { return <section><h3>{title}</h3><div className="dshDiagnosticRows">{children}</div></section> }
function DiagnosticRow({ label, state, detail }: { label: string; state: string; detail: string }) { return <div className="dshModelAgentDiagnosticRow"><div><strong>{label}</strong><small>{detail}</small></div><span>{state}</span></div> }
function Empty({ children }: { children: React.ReactNode }) { return <p className="dshModelAgentMuted">{children}</p> }
function failedLayer(connection: ConnectionProfile): string | null {
  const failed = connection.latestTest.layers.find((layer) => layer.state === 'failed' || layer.state === 'not-configured' || layer.state === 'approval-required')
  if (failed === undefined) return null
  const action = failed.state === 'not-configured' ? '配置凭证' : failed.state === 'approval-required' ? '查看权限' : '重新测试'
  return `${failed.label}：${failed.message}。下一步：${action}`
}
