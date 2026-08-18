export type PluginAction = 'install' | 'update' | 'remove'

export interface CommunityPlugin {
  id: string
  packageName: string
  name: string
  description: string
  publisher: string
  repository: string
  installSpec: string
  version: string
  verified: boolean
  installed: boolean
  updateAvailable: boolean
}

export interface PluginOperationEvent { line: string; stream: 'stdout' | 'stderr' | 'system'; done: boolean; ok?: boolean }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const body = await response.json().catch(() => ({})) as { error?: string } & T
  if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`)
  return body
}

export const marketClient = {
  list: () => request<CommunityPlugin[]>('/api/desktop/community/plugins'),
  preview: (pluginId: string, action: PluginAction) => request<{ token: string }>('/api/desktop/community/preview', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ pluginId, action }),
  }),
  execute: (token: string) => request<{ operationId: string }>('/api/desktop/community/execute', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token }),
  }),
  cancel: (operationId: string) => request<void>('/api/desktop/community/cancel', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ operationId }),
  }),
  events(operationId: string, listener: (event: PluginOperationEvent) => void) {
    const source = new EventSource(`/api/desktop/community/events?operationId=${encodeURIComponent(operationId)}`)
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as PluginOperationEvent
      listener(event)
      if (event.done) source.close()
    }
    source.onerror = () => { listener({ line: '操作日志连接中断', stream: 'system', done: true, ok: false }); source.close() }
    return () => source.close()
  },
}
