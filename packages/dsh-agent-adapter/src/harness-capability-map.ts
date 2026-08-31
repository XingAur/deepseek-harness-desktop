export const HARNESS_CAPABILITY_SCHEMA = 'harness-capability-map.v1' as const

export type HarnessCapabilityEffect = 'read' | 'write' | 'external'

export interface HarnessProviderDescriptor {
  providerId: string
  ready: boolean
  scopes?: string[]
  capabilities?: string[]
}

export interface HarnessMcpServerDescriptor {
  serverId: string
  ready: boolean
  tools: Array<{ name: string; effect: HarnessCapabilityEffect }>
}

export interface HarnessSkillDescriptor {
  skillId: string
  enabled: boolean
}

export interface HarnessCapabilityMapInput {
  providers?: HarnessProviderDescriptor[]
  mcpServers?: HarnessMcpServerDescriptor[]
  skills?: HarnessSkillDescriptor[]
  /** Accepted only to prove callers may keep credentials in the host boundary. */
  token?: string
  allowWrites?: boolean
  writeApprovalGranted?: boolean
}

export interface HarnessCapability {
  id: string
  providerId: string
  effect: HarnessCapabilityEffect
  scopes: string[]
}

export interface HarnessCapabilityMap {
  schema_version: typeof HARNESS_CAPABILITY_SCHEMA
  capabilities: HarnessCapability[]
  providers: Array<{ providerId: string; scopes: string[] }>
  skills: string[]
}

/**
 * Project host capabilities into a secret-free Harness advertisement.
 *
 * A provider being configured is not enough to grant a mutation.  The user
 * must explicitly approve writes for this task as a separate decision.
 */
export function buildHarnessCapabilityMap(input: HarnessCapabilityMapInput = {}): HarnessCapabilityMap {
  const writeAllowed = input.allowWrites === true && input.writeApprovalGranted === true
  const providers = (input.providers ?? [])
    .filter((provider) => isIdentifier(provider.providerId) && provider.ready)
    .map((provider) => ({
      providerId: provider.providerId,
      scopes: boundedStrings(provider.scopes),
    }))
  const providerIds = new Set(providers.map((provider) => provider.providerId))
  const capabilities: HarnessCapability[] = []

  for (const provider of input.providers ?? []) {
    if (!providerIds.has(provider.providerId)) continue
    for (const capability of boundedStrings(provider.capabilities)) {
      const [effect, ...parts] = capability.split(':')
      if (!isEffect(effect) || (effect !== 'read' && !writeAllowed)) continue
      const name = parts.join(':')
      if (name === '') continue
      capabilities.push({
        id: `${provider.providerId}.${name}`,
        providerId: provider.providerId,
        effect,
        scopes: boundedStrings(provider.scopes),
      })
    }
  }

  for (const server of input.mcpServers ?? []) {
    if (!server.ready || !isIdentifier(server.serverId)) continue
    const provider = providerIds.has(server.serverId)
      ? server.serverId
      : server.serverId
    const scopes = providers.find((item) => item.providerId === provider)?.scopes ?? []
    for (const tool of server.tools.slice(0, 256)) {
      if (!isIdentifier(tool.name)) continue
      if (tool.effect !== 'read' && !writeAllowed) continue
      capabilities.push({
        id: `${server.serverId}.${tool.name}`,
        providerId: provider,
        effect: tool.effect,
        scopes,
      })
    }
  }

  return {
    schema_version: HARNESS_CAPABILITY_SCHEMA,
    capabilities: uniqueCapabilities(capabilities),
    providers,
    skills: (input.skills ?? [])
      .filter((skill) => skill.enabled && isIdentifier(skill.skillId))
      .map((skill) => skill.skillId)
      .filter((value, index, values) => values.indexOf(value) === index)
      .slice(0, 256),
  }
}

function uniqueCapabilities(values: HarnessCapability[]): HarnessCapability[] {
  const seen = new Set<string>()
  return values.filter((value) => {
    const key = `${value.id}\u0000${value.effect}\u0000${value.scopes.join(',')}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  }).slice(0, 512)
}

function boundedStrings(values: string[] | undefined): string[] {
  return (values ?? []).filter((value): value is string => typeof value === 'string' && value.length > 0 && value.length <= 256).slice(0, 64)
}

function isEffect(value: string): value is HarnessCapabilityEffect {
  return value === 'read' || value === 'write' || value === 'external'
}

function isIdentifier(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
}
