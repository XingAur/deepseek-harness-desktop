import { describe, expect, it } from 'vitest'
import { buildHarnessCapabilityMap } from './harness-capability-map.js'

describe('Harness capability projection', () => {
  it('advertises only non-secret read capabilities by default', () => {
    const advertised = buildHarnessCapabilityMap({
      providers: [{ providerId: 'yunxiao', ready: true, scopes: ['DFHIS'] }],
      mcpServers: [{ serverId: 'yunxiao', ready: true, tools: [
        { name: 'workitem.read', effect: 'read' },
        { name: 'workitem.update', effect: 'write' },
      ] }],
      skills: [{ skillId: 'his-requirement-governance', enabled: true }],
      token: 'must-never-be-projected',
    })

    expect(JSON.stringify(advertised)).not.toContain('must-never-be-projected')
    expect(advertised.capabilities.map((item) => item.id)).toContain('yunxiao.workitem.read')
    expect(advertised.capabilities.map((item) => item.id)).not.toContain('yunxiao.workitem.update')
    expect(advertised.skills).toEqual(['his-requirement-governance'])
  })

  it('requires an explicit separate write approval before projecting mutation tools', () => {
    const advertised = buildHarnessCapabilityMap({
      mcpServers: [{ serverId: 'gitlab', ready: true, tools: [{ name: 'commit.push', effect: 'external' }] }],
      allowWrites: true,
      writeApprovalGranted: false,
    })
    expect(advertised.capabilities).toEqual([])
  })
})
