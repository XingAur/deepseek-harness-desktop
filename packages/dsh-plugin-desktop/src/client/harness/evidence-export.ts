export const CONVERSATION_EVIDENCE_SCHEMA = 'conversation-evidence.v1' as const
const MAX_FILES = 64
const MAX_FILE_BYTES = 32 * 1024 * 1024
const MAX_TOTAL_BYTES = 128 * 1024 * 1024

export type EvidenceSource = 'current-chat' | 'yunxiao-archive' | 'local-selection'

export interface EvidenceSelection {
  file: Blob
  source: EvidenceSource
  fileName?: string
}

export interface EvidenceFileReference {
  id: string
  fileName: string
  mediaType: string
  byteLength: number
  sha256: string
  source: EvidenceSource
}

export interface ConversationEvidence {
  schema_version: typeof CONVERSATION_EVIDENCE_SCHEMA
  files: EvidenceFileReference[]
  /** Binary stays in the host-owned map and is never interpolated into a model prompt. */
  blobs: Map<string, Blob>
}

export async function exportEvidence(selections: EvidenceSelection[]): Promise<ConversationEvidence> {
  if (!Array.isArray(selections) || selections.length > MAX_FILES) throw new Error('证据文件数量超出限制')
  const files: EvidenceFileReference[] = []
  const blobs = new Map<string, Blob>()
  let total = 0
  for (const selection of selections) {
    if (!(selection?.file instanceof Blob)) throw new Error('证据文件无效')
    const size = selection.file.size
    total += size
    if (size > MAX_FILE_BYTES || total > MAX_TOTAL_BYTES) throw new Error('证据文件超出限制')
    if (!['current-chat', 'yunxiao-archive', 'local-selection'].includes(selection.source)) throw new Error('证据来源无效')
    const digest = await sha256(selection.file)
    const id = `evidence-${digest.slice(0, 16)}`
    const reference: EvidenceFileReference = {
      id,
      fileName: boundedFileName(selection.fileName ?? ('name' in selection.file ? String(selection.file.name) : 'evidence.bin')),
      mediaType: selection.file.type || 'application/octet-stream',
      byteLength: size,
      sha256: digest,
      source: selection.source,
    }
    files.push(reference)
    blobs.set(id, selection.file)
  }
  return { schema_version: CONVERSATION_EVIDENCE_SCHEMA, files, blobs }
}

async function sha256(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('')
}

function boundedFileName(value: string): string {
  const name = value.replaceAll(/[\\/\0]/g, '_').trim()
  return name === '' ? 'evidence.bin' : name.slice(0, 255)
}
