/** Launcher-side model diagnostics state, shared with the native window owner. */

import { resolveDshHome } from '@deepseek-ai/dsh-home-paths'
import {
  probeModels,
  readModelDiagnosticsState,
  selectModel,
  type ModelDiagnosticsState,
  type ModelProbeResult,
} from './model-diagnostics-core.ts'

/** Read the effective model configuration from the active DSH home. */
export async function getDesktopModelDiagnosticsState(): Promise<ModelDiagnosticsState> {
  return await readModelDiagnosticsState(resolveDshHome())
}

/** Probe connectivity, authentication, and the model list. */
export async function probeDesktopModels(): Promise<ModelProbeResult> {
  return await probeModels(resolveDshHome())
}

/** Persist one model id as the default for the next sessions. */
export async function selectDesktopModel(model: string): Promise<{ accepted: boolean }> {
  return await selectModel(resolveDshHome(), model)
}
