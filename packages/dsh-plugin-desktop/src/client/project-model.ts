import type { WorkspaceView } from './contracts'

export const PROJECT_COVERS = ['aurora-blue', 'sunset', 'forest', 'graphite', 'violet'] as const
export type ProjectCoverToken = typeof PROJECT_COVERS[number]

export interface ProjectMetadataEntry {
  cover?: ProjectCoverToken | null
  pinned: boolean
  localApp?: boolean
  updatedAt: string
}

export type ProjectMetadataMap = Readonly<Record<string, ProjectMetadataEntry | undefined>>

export interface ProjectMetadataSnapshot {
  schemaVersion: number
  projects: Record<string, ProjectMetadataEntry>
}

export interface ProjectCardModel {
  id: string
  path: string
  title: string
  sessionIds: string[]
  createdAt: string
  updatedAt: string
  cover: ProjectCoverToken
  pinned: boolean
}

export type ProjectPermissionMode = 'read-only' | 'workspace-write'

export interface PreparedProjectLocation {
  projectName: string
  suggestedPath: string
}

export interface ProjectDraftInput {
  idea: string
  profileId: string
  location: PreparedProjectLocation
}

export interface ProjectDraft {
  idea: string
  normalizedPath: string
  profileId: string
  permissionMode: ProjectPermissionMode
  proposedName: string
  commandCategories: ['package-manager', 'build', 'test']
  createDirectory: boolean
}

export function projectCards(workspaces: readonly WorkspaceView[], metadata: ProjectMetadataMap = {}): ProjectCardModel[] {
  return workspaces
    .map((workspace) => {
      const projectMetadata = metadata[workspace.workspaceId]
      return {
        id: workspace.workspaceId,
        path: workspace.path,
        title: workspace.title,
        sessionIds: [...workspace.sessionIds],
        createdAt: workspace.createdAt,
        updatedAt: workspace.updatedAt,
        cover: projectMetadata?.cover ?? defaultProjectCover(workspace.workspaceId),
        pinned: projectMetadata?.pinned ?? false,
      }
    })
    .sort((left, right) => Number(right.pinned) - Number(left.pinned) || timestamp(right.updatedAt) - timestamp(left.updatedAt))
}

export function defaultProjectCover(workspaceId: string): ProjectCoverToken {
  let hash = 2166136261
  for (const character of workspaceId) {
    hash ^= character.codePointAt(0) ?? 0
    hash = Math.imul(hash, 16777619)
  }
  return PROJECT_COVERS[(hash >>> 0) % PROJECT_COVERS.length] ?? 'aurora-blue'
}

export function projectDraft(input: ProjectDraftInput): ProjectDraft {
  const idea = input.idea.trim()
  const profileId = input.profileId.trim()
  if (idea.length === 0) throw new Error('请先描述你想构建的项目')
  if (profileId.length === 0) throw new Error('请选择 Profile')
  const proposedName = input.location.projectName.trim()
  if (proposedName.length === 0) throw new Error('项目名称无效')
  const normalizedPath = normalizeAbsolutePath(input.location.suggestedPath)

  return {
    idea,
    normalizedPath,
    profileId,
    permissionMode: 'workspace-write',
    proposedName,
    commandCategories: ['package-manager', 'build', 'test'],
    createDirectory: true,
  }
}

function normalizeAbsolutePath(input: string): string {
  const value = input.trim()
  if (/^[A-Za-z]:[\\/]/.test(value)) {
    const drive = value[0]?.toUpperCase() ?? ''
    const rest = value.slice(2).replace(/[\\/]+/g, '\\').replace(/\\+$/g, '')
    if (rest.length === 0) throw new Error('项目路径不能是磁盘根目录')
    return `${drive}:${rest.startsWith('\\') ? '' : '\\'}${rest}`
  }
  if (/^(?:\\\\|\/\/)[^\\/]+[\\/][^\\/]+/.test(value)) {
    const parts = value.replace(/^[\\/]+/, '').split(/[\\/]+/).filter(Boolean)
    if (parts.length <= 2) throw new Error('项目路径不能是共享根目录')
    return `\\\\${parts.join('\\')}`
  }
  if (value.startsWith('/')) {
    const normalized = `/${value.split('/').filter(Boolean).join('/')}`
    if (normalized === '/') throw new Error('项目路径不能是根目录')
    return normalized
  }
  throw new Error('项目目录必须使用绝对路径')
}

function timestamp(value: string): number {
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}
