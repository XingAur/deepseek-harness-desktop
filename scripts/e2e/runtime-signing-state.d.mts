import type { KeyObject } from 'node:crypto'

export function createRuntimeSigningState(outputPath: string): { path: string; publicKey: string }
export function loadRuntimeSigningState(inputPath: string): { privateKey: KeyObject; publicKey: string }
