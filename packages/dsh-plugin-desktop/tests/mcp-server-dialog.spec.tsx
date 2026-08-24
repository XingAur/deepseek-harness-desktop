import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { McpServerDialog } from '../src/client/extensions/McpServerDialog'

describe('McpServerDialog', () => {
  it('previews transport, tools, permissions and requires explicit enable', () => {
    const onEnable = vi.fn()
    render(<McpServerDialog server={{
      serverId: 'filesystem',
      displayName: 'Filesystem MCP',
      transport: 'stdio',
      command: 'mcp-filesystem',
      tools: [{ name: 'read_file', effect: 'read' }, { name: 'write_file', effect: 'write' }],
      requestedPermissions: ['workspace-read', 'workspace-write'],
      oauthIssuer: undefined,
    }} onClose={vi.fn()} onEnable={onEnable} />)
    expect(screen.getByRole('dialog', { name: 'MCP 服务审核' })).toBeInTheDocument()
    expect(screen.getByText('stdio')).toBeInTheDocument()
    expect(screen.getByText('read_file')).toBeInTheDocument()
    expect(screen.getByText('workspace-write')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '启用 MCP 服务' }))
    expect(onEnable).toHaveBeenCalledTimes(1)
  })
})
