import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CredentialDialog } from '../src/client/model-agent/CredentialDialog'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(overrides: Partial<DesktopBridgeLike> = {}): DesktopBridgeLike {
  return {
    request: vi.fn(),
    requestV2: vi.fn(async () => ({ credentialId: 'credential-1', status: 'configured' })) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
    ...overrides,
  }
}

describe('credential dialog', () => {
  it('submits a masked secret only through credential.put and clears it after success', async () => {
    const bridge = bridgeFixture()
    const onClose = vi.fn()
    const { container } = render(<CredentialDialog bridge={bridge} providerId="codex" providerName="Codex" credentialId={undefined} onClose={onClose} />)

    const input = screen.getByLabelText('API Key') as HTMLInputElement
    expect(input.type).toBe('password')
    fireEvent.change(input, { target: { value: 'sk-secret-value' } })
    fireEvent.click(screen.getByRole('button', { name: '保存凭证' }))

    await waitFor(() => expect(onClose).toHaveBeenCalledWith({ credentialId: 'credential-1', status: 'configured' }))
    expect(bridge.requestV2).toHaveBeenCalledWith('credential.put', undefined, { providerId: 'codex', secret: 'sk-secret-value' })
    expect((container.querySelector('input') as HTMLInputElement | null)?.value).toBe('')
    expect(screen.queryByText('sk-secret-value')).not.toBeInTheDocument()
  })

  it('clears the secret and shows only a bounded error category on failure', async () => {
    const bridge = bridgeFixture({ requestV2: vi.fn(async () => { throw new Error('invalid-key') }) })
    const { container } = render(<CredentialDialog bridge={bridge} providerId="codex" providerName="Codex" credentialId={undefined} onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-secret-value' } })
    fireEvent.click(screen.getByRole('button', { name: '保存凭证' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('invalid-key')
    expect((container.querySelector('input') as HTMLInputElement).value).toBe('')
    expect(screen.queryByText('sk-secret-value')).not.toBeInTheDocument()
  })
})
