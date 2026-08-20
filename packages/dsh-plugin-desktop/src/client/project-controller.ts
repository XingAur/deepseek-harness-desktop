import type { SessionsLike, WorkspacesLike } from './contracts'
import { projectDraft, type ProjectDraft, type ProjectDraftInput } from './project-model'

export interface ProjectController {
  prepare(input: ProjectDraftInput): ProjectDraft
  confirm(draft: ProjectDraft): Promise<{ workspaceId: string; sessionId: string }>
  modify(workspaceId: string, prompt: string): Promise<{ sessionId: string }>
}

export type ProjectDirectoryCreator = (target: string) => Promise<string>

export function createProjectController(
  workspaces: WorkspacesLike,
  sessions: SessionsLike,
  createDirectory: ProjectDirectoryCreator = (target) => createTargetDirectory(workspaces, target),
): ProjectController {
  return {
    prepare: projectDraft,
    async confirm(draft) {
      let workspaceId: string | null = null
      try {
        const path = draft.createDirectory
          ? await createDirectory(draft.normalizedPath)
          : draft.normalizedPath
        const workspace = await workspaces.create({ path })
        workspaceId = workspace.workspaceId
        const sessionId = await sessions.create({ workspaceId })
        const binding = await waitForSessionBinding(sessions, sessionId)
        const reply = await binding.session.prompt([{ type: 'text', text: buildPrompt(draft) }], 'queue')
        if (!reply.ok) throw new Error(reply.error?.message ?? '项目构建请求未被接受')
        sessions.open(sessionId)
        return { workspaceId, sessionId }
      } catch (cause) {
        if (workspaceId !== null) {
          try { await workspaces.delete(workspaceId) } catch { /* 保留原始失败原因。 */ }
        }
        throw cause
      }
    },
    async modify(workspaceId, prompt) {
      const request = prompt.trim()
      if (request.length === 0) throw new Error('请先填写修改需求')
      let sessionId = await workspaces.connectWorkspace(workspaceId)
      let binding = sessions.binding(sessionId)
      if (binding === undefined) {
        sessionId = await sessions.create({ workspaceId })
        binding = await waitForSessionBinding(sessions, sessionId)
      }
      if (binding === undefined) throw new Error('项目会话尚未准备好，请重试')
      const reply = await binding.session.prompt([{
        type: 'text',
        text: `请在当前本地项目中完成下面的修改。先检查现状，尽量保持现有结构和风格；需要额外权限时先询问。\n\n修改需求：\n${request}`,
      }], 'queue')
      if (!reply.ok) throw new Error(reply.error?.message ?? '项目修改请求未被接受')
      sessions.open(sessionId)
      return { sessionId }
    },
  }
}

function buildPrompt(draft: ProjectDraft) {
  const permission = draft.permissionMode === 'read-only' ? '只读' : '工作区可写'
  return `请在当前工作区构建下面的本地项目。先检查现状并给出简短计划，再按当前权限执行；需要额外权限时先询问。\n\n当前 Profile：${draft.profileId}\n权限模式：${permission}\n允许的命令类别：${draft.commandCategories.join('、')}\n\n项目需求：\n${draft.idea}`
}

async function createTargetDirectory(workspaces: WorkspacesLike, target: string) {
  const separator = Math.max(target.lastIndexOf('\\'), target.lastIndexOf('/'))
  if (separator <= 0 || separator === target.length - 1) throw new Error('无法确定项目的父目录')
  const parent = target.slice(0, separator)
  const name = target.slice(separator + 1)
  return workspaces.createDirectory(parent, name)
}

async function waitForSessionBinding(sessions: SessionsLike, sessionId: string) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const binding = sessions.binding(sessionId)
    if (binding !== undefined) return binding
    await new Promise<void>((resolveReady) => setTimeout(resolveReady, 0))
  }
  throw new Error('项目会话尚未准备好，请重试')
}
