export interface ReleaseStateInput {
  version: string
  legacyReleaseBaseline: string
  tagExists: boolean
  release: null | {
    isDraft: boolean
    assets: Array<{ name: string }>
  }
}

export interface ReleaseStateResult {
  status: 'complete' | 'pending-tag' | 'pending-release' | 'blocked'
  reason: string
}

export function classifyReleaseState(input: ReleaseStateInput): ReleaseStateResult
export function parseBooleanArgument(value: string, label?: string): boolean
