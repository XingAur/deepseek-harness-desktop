import { existsSync } from 'node:fs'
import { delimiter, join } from 'node:path'

/** Resolve a local openssl binary for TLS fixtures. Never use user profile paths. */
export function opensslBin(): string {
  const configured = process.env.DSH_TEST_OPENSSL
  if (configured && existsSync(configured)) return configured
  if (process.platform !== 'win32' && existsSync('/usr/bin/openssl')) return '/usr/bin/openssl'
  for (const directory of (process.env.PATH ?? '').split(delimiter)) {
    if (!directory) continue
    const unix = join(directory, 'openssl')
    const windows = join(directory, 'openssl.exe')
    if (existsSync(unix)) return unix
    if (existsSync(windows)) return windows
  }
  throw new Error('openssl is required for local TLS fixtures')
}
