export interface RuntimeLauncherOptions {
  desktopPluginVersion: string
  desktopPluginSha256: string
  runtimeVersion: string
}

export function writeRuntimeLauncher(appDir: string, options: RuntimeLauncherOptions): void
