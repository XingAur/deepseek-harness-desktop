export const PBS_TAG: string
export const PBS_PYTHON_VERSION: string
export function pythonAssetName(platform?: string, arch?: string): string
export function pythonDownloadUrl(platform?: string, arch?: string): string
export function bundledPythonExecutable(coreRoot: string, platform?: string): string
