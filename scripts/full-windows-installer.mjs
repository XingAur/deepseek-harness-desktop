import { resolve } from 'node:path'

const portable = (value) => value.replaceAll('\\', '/')

export function createFullTauriConfig(rootDirectory) {
  const root = resolve(rootDirectory)
  return {
    bundle: {
      resources: {
        [portable(resolve(root, 'runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip'))]:
          'runtime/dsh-runtime-windows-x86_64.zip',
        [portable(resolve(root, 'runtime-build/windows-x86_64/runtime-windows-x86_64.json'))]:
          'runtime/manifests/runtime-windows-x86_64.json',
      },
      windows: {
        nsis: {
          installerHooks: portable(resolve(root, 'src-tauri/windows/full-installer-hooks.nsh')),
        },
      },
    },
  }
}
