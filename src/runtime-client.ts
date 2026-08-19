import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { BootstrapReply, RuntimeClient, RuntimeEvent } from './runtime-contract'

export const tauriRuntimeClient: RuntimeClient = {
  bootstrapRuntime: () => invoke<BootstrapReply>('bootstrap_runtime'),
  cancelRuntime: () => invoke<void>('cancel_runtime'),
  repairRuntime: () => invoke<BootstrapReply>('repair_runtime'),
  exportDiagnostics: () => invoke<string>('export_diagnostics'),
  async subscribeRuntimeProgress(listener) {
    const unlisten = await listen<RuntimeEvent>('runtime-event', ({ payload }) => listener(payload))
    return unlisten
  },
}
