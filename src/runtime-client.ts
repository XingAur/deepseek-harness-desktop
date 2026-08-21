import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { AppUpdateEvent, AppUpdateReceipt, AppUpdateState, BootstrapReply, DesktopEvent, LocalAppEvent, MigrationStatus, RuntimeClient, RuntimeEvent } from './runtime-contract'

export const tauriRuntimeClient: RuntimeClient = {
  bootstrapRuntime: () => invoke<BootstrapReply>('bootstrap_runtime'),
  cancelRuntime: () => invoke<void>('cancel_runtime'),
  repairRuntime: () => invoke<BootstrapReply>('repair_runtime'),
  exportDiagnostics: () => invoke<string>('export_diagnostics'),
  migrationStatus: () => invoke<MigrationStatus>('migration_status'),
  confirmMigration: () => invoke<void>('confirm_migration'),
  deferMigration: () => invoke<void>('defer_migration'),
  checkAppUpdate: (source) => invoke<AppUpdateState>('check_app_update', { source }),
  downloadAppUpdate: () => invoke<AppUpdateState>('download_app_update'),
  installAppUpdateNow: () => invoke<void>('install_app_update_now'),
  installAppUpdateOnExit: () => invoke<AppUpdateState>('install_app_update_on_exit'),
  deferAppUpdate: () => invoke<AppUpdateState>('defer_app_update'),
  takeAppUpdateReceipt: () => invoke<AppUpdateReceipt | null>('take_app_update_receipt'),
  async subscribeRuntimeProgress(listener) {
    const unlisten = await listen<RuntimeEvent>('runtime-event', ({ payload }) => listener(payload))
    return unlisten
  },
  async subscribeDesktopEvents(listener) {
    return listen<DesktopEvent>('desktop-event', ({ payload }) => listener(payload))
  },
  async subscribeAppUpdates(listener) {
    return listen<AppUpdateEvent>('app-update-event', ({ payload }) => listener(payload))
  },
  async subscribeLocalAppEvents(listener) {
    return listen<LocalAppEvent>('local-app-event', ({ payload }) => listener(payload))
  },
}
