import { invoke } from '@tauri-apps/api/core'

export interface WindowControls {
  close(): Promise<void>
  minimize(): Promise<void>
  toggleMaximize(): Promise<void>
  startDragging(): Promise<void>
}

export const tauriWindowControls: WindowControls = {
  close: () => invoke<void>('close_window'),
  minimize: () => invoke<void>('minimize_window'),
  toggleMaximize: () => invoke<void>('toggle_maximize_window'),
  startDragging: () => invoke<void>('start_drag'),
}
