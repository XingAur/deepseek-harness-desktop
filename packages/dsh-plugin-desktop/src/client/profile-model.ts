export interface ProfileSummary {
  id: string
  name: string
  revision: number
  dataRoot?: string
  permissionMode?: 'read-only' | 'workspace-write'
  runtimeVersion?: string
  status?: 'active' | 'switching' | 'recovered' | 'invalid' | 'ready'
}

export interface ProfileListResult {
  selectedProfileId: string | null
  pendingProfileId: string | null
  lastKnownGoodProfileId: string | null
  profiles: ProfileSummary[]
}
