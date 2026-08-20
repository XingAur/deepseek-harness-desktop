export interface NsisTemplateMetadata {
  tauriCliVersion: string
  tauriBundlerVersion: string
  upstreamSha256: string
  requiredMarkers: string[]
}

export const EXPECTED_UPSTREAM_SHA256: string
export function verifyNsisTemplate(path?: string): NsisTemplateMetadata
