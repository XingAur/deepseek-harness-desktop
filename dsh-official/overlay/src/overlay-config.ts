/** Overlay-owned YAML in the active DSH home. Official settings.yaml must not be rewritten. */

import { readFileSync } from 'node:fs'
import { chmod, mkdir, readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { parse, stringify } from 'yaml'

const FILE = 'overlay-custom.yaml'

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

export function readOverlayConfigSync(home: string): Record<string, unknown> {
  try {
    return asRecord(parse(readFileSync(join(home, FILE), 'utf8')))
  } catch {
    return {}
  }
}

export async function readOverlayConfig(home: string): Promise<Record<string, unknown>> {
  try {
    return asRecord(parse(await readFile(join(home, FILE), 'utf8')))
  } catch {
    return {}
  }
}

export async function writeOverlayConfig(home: string, patch: Record<string, unknown>): Promise<Record<string, unknown>> {
  const current = await readOverlayConfig(home)
  const next = { ...current, ...patch }
  await mkdir(home, { recursive: true, mode: 0o700 })
  const path = join(home, FILE)
  await writeFile(path, `${stringify(next)}\n`, { encoding: 'utf8', mode: 0o600 })
  await chmod(path, 0o600)
  return next
}
