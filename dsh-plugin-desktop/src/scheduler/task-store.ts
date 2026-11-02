/** Durable scheduler task storage over one private JSON document in the DSH home. */

import { mkdir, readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { writeFileAtomic } from '@deepseek-ai/dsh-atomic-write'
import {
  emptySchedulerTaskFile,
  normalizeSchedulerTaskFile,
  type SchedulerTaskFile,
} from './types.ts'

/** DSH-home subdirectory owned by the Desktop scheduler. */
export const SCHEDULER_DIRECTORY = 'desktop-scheduler'

/** Task document file name inside the scheduler directory. */
export const SCHEDULER_TASKS_FILENAME = 'tasks.json'

/** Refuse to parse larger documents; recovery replaces them with an empty file. */
export const MAX_TASK_FILE_BYTES = 512 * 1024

/** Default bounded run history retained per task. */
export const DEFAULT_RUNS_KEPT_PER_TASK = 50

/** Absolute task document path for one DSH home. */
export function schedulerTasksPath(homeDir: string): string {
  return join(homeDir, SCHEDULER_DIRECTORY, SCHEDULER_TASKS_FILENAME)
}

/** Why a read did not return stored content; `false` when storage was read. */
export type SchedulerReadRecovery = 'missing' | 'oversized' | 'corrupt' | false

export interface SchedulerTaskFileRead {
  /** Complete normalized document; empty after every recovery outcome. */
  readonly file: SchedulerTaskFile
  /** How the previous storage state was treated. */
  readonly recovered: SchedulerReadRecovery
}

/**
 * Read and fully normalize the task document. Missing, oversized, and
 * corrupt storage each recover to the empty version-1 file; the caller owns
 * logging the recovery so the decision stays visible.
 */
export async function readSchedulerTaskFile(
  path: string,
  runsKeptPerTask: number = DEFAULT_RUNS_KEPT_PER_TASK,
): Promise<SchedulerTaskFileRead> {
  let text: string
  try {
    text = await readFile(path, 'utf8')
  } catch (cause: unknown) {
    const code = (cause as { readonly code?: unknown }).code
    if (code === 'ENOENT') {
      return { file: emptySchedulerTaskFile(), recovered: 'missing' }
    }
    return { file: emptySchedulerTaskFile(), recovered: 'corrupt' }
  }
  if (Buffer.byteLength(text, 'utf8') > MAX_TASK_FILE_BYTES) {
    return { file: emptySchedulerTaskFile(), recovered: 'oversized' }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text) as unknown
  } catch {
    return { file: emptySchedulerTaskFile(), recovered: 'corrupt' }
  }
  return {
    file: normalizeSchedulerTaskFile(parsed, runsKeptPerTask),
    recovered: false,
  }
}

/**
 * Replace the task document in one atomic step. The document holds only
 * user-authored task prompts, so the file and its directory stay private.
 */
export async function writeSchedulerTaskFile(path: string, file: SchedulerTaskFile): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 })
  await writeFileAtomic(path, `${JSON.stringify(file, null, 2)}\n`, { mode: 0o600, dirMode: 0o700 })
}
