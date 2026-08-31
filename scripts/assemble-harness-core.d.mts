export const PBS_TAG: string
export const PBS_PYTHON_VERSION: string
export function pythonAssetName(platform?: string, arch?: string): string
export function pythonDownloadUrl(platform?: string, arch?: string): string
export function shouldSyncHarnessVendor(environment?: Record<string, string | undefined>): boolean
export function bundledPythonExecutable(coreRoot: string, platform?: string): string
export function pythonEnvironmentRoot(coreRoot: string, executable: string): string
