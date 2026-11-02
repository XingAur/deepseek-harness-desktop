import { homedir } from 'node:os'
import { join } from 'node:path'

/** Active Harness home. Avoids importing official `@deepseek-ai/dsh-home-paths`. */
export function resolveDshHome(): string {
  const env = process.env.DSH_HOME
  if (typeof env === 'string' && env.trim() !== '') return env.trim()
  return join(homedir(), '.dsh')
}
