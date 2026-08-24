import { useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from './state'

interface CredentialPutResult {
  credentialId: string
  status: 'configured' | 'not-configured'
}

export interface CredentialDialogProps {
  bridge: DesktopBridgeLike
  providerId: string
  providerName: string
  credentialId?: string
  onClose(result?: CredentialPutResult): void
}

export function CredentialDialog({ bridge, providerId, providerName, credentialId, onClose }: CredentialDialogProps) {
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const close = () => {
    setSecret('')
    setError(null)
    onClose()
  }

  const save = async () => {
    if (secret.length === 0 || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await bridge.requestV2<CredentialPutResult>('credential.put', undefined, {
        providerId,
        ...(credentialId === undefined ? {} : { credentialId }),
        secret,
      })
      setSecret('')
      onClose(result)
    } catch (cause) {
      setSecret('')
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dshModelAgentDialogBackdrop">
      <section className="dshModelAgentDialog" role="dialog" aria-modal="true" aria-labelledby="dsh-credential-dialog-title">
        <header>
          <div><p className="dshModelAgentEyebrow">SECURE CREDENTIAL</p><h3 id="dsh-credential-dialog-title">配置 {providerName}</h3></div>
          <button type="button" aria-label="关闭" disabled={busy} onClick={close}>×</button>
        </header>
        <p className="dshModelAgentDialogHint">凭证只会写入系统安全存储，不会写入项目、日志、页面状态或诊断信息。</p>
        <label className="dshModelAgentField">API Key
          <input
            aria-label="API Key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={secret}
            disabled={busy}
            onChange={(event) => setSecret(event.target.value)}
          />
        </label>
        {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
        <footer>
          <button type="button" disabled={busy} onClick={close}>取消</button>
          <button type="button" className="dshModelAgentPrimary" disabled={busy || secret.length === 0} onClick={() => void save()}>保存凭证</button>
        </footer>
      </section>
    </div>
  )
}
