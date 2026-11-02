import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'
import type {} from '@deepseek-ai/dsh-client-ui-settings/client'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client'
import { applyOverlayRemoteEntry } from './RemoteEntry.tsx'
import { applyOverlayModelExtras } from './ProviderCardExtras.tsx'
import { applyOverlayCapabilitySettings } from './SettingsSections.tsx'
import { applyOverlayUpdateEntry } from './UpdateEntry.tsx'

export const inject = ['slots', 'locale']

/** Client face: sidebar mobile entry + provider-card test/fetch. */
export function apply(ctx: ClientContext): void {
  applyOverlayRemoteEntry(ctx)
  applyOverlayModelExtras(ctx)
  applyOverlayCapabilitySettings(ctx)
  applyOverlayUpdateEntry(ctx)
}
