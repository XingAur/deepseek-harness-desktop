import type { DesktopBridgeLike } from '../desktop-bridge'

// MCP 同步目标(MVP 仅 Claude 与 Codex;DSH 官方设置已有 MCP 管理,不重复)。
export type McpTarget = 'claude' | 'codex'

export interface McpServerDef {
  id: string
  name: string
  command: string
  args: string[]
  env: Record<string, string>
  targets: McpTarget[]
}

export interface McpTargetStatus {
  target: McpTarget
  installed: boolean
}

export const MCP_TARGET_LABELS: Record<McpTarget, string> = { claude: 'Claude', codex: 'Codex' }
export const MCP_TARGETS: McpTarget[] = ['claude', 'codex']

export async function fetchServers(bridge: DesktopBridgeLike): Promise<McpServerDef[]> {
  return bridge.requestV2<McpServerDef[]>('mcp.list')
}
export async function fetchTargetStatus(bridge: DesktopBridgeLike): Promise<McpTargetStatus[]> {
  return bridge.requestV2<McpTargetStatus[]>('mcp.status')
}
export async function upsertServer(bridge: DesktopBridgeLike, def: Partial<McpServerDef> & Pick<McpServerDef, 'name' | 'command' | 'targets'>): Promise<McpServerDef> {
  return bridge.requestV2<McpServerDef>('mcp.upsert', undefined, {
    ...(def.id ? { id: def.id } : {}),
    name: def.name,
    command: def.command,
    args: def.args ?? [],
    env: def.env ?? {},
    targets: def.targets,
  })
}
export async function deleteServer(bridge: DesktopBridgeLike, id: string): Promise<void> {
  await bridge.requestV2('mcp.delete', undefined, { id })
}
export async function syncTarget(bridge: DesktopBridgeLike, target: McpTarget): Promise<void> {
  await bridge.requestV2('mcp.sync', undefined, { target })
}
export async function importFromTarget(bridge: DesktopBridgeLike, target: McpTarget): Promise<McpServerDef[]> {
  return bridge.requestV2<McpServerDef[]>('mcp.import', undefined, { target })
}

/** 参数按空白分词(与 cc-switch 一致的 MVP 语义);引号不做特殊处理。 */
export function parseArgsText(text: string): string[] {
  return text.trim().length === 0 ? [] : text.trim().split(/\s+/)
}

export function argsToText(args: string[]): string {
  return args.join(' ')
}

/** 环境变量编辑区:每行一条 KEY=VALUE,无法解析的行跳过。 */
export function parseEnvText(text: string): Record<string, string> {
  const env: Record<string, string> = {}
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim()
    if (trimmed.length === 0) continue
    const separator = trimmed.indexOf('=')
    if (separator <= 0) continue
    const key = trimmed.slice(0, separator).trim()
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue
    env[key] = trimmed.slice(separator + 1)
  }
  return env
}

export function envToText(env: Record<string, string>): string {
  return Object.entries(env).map(([key, value]) => `${key}=${value}`).join('\n')
}

export function targetSummary(targets: McpTarget[]): string {
  return targets.map((target) => MCP_TARGET_LABELS[target]).join(' / ') || '未选择目标'
}
