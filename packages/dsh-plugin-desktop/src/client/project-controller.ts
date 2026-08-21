import type { SessionsLike, WorkspacesLike } from './contracts'
import { projectDraft, type PreparedProjectLocation, type ProjectDraft } from './project-model'

export interface ProjectController {
  prepare(input: { idea: string; profileId: string }): Promise<ProjectDraft>
  confirm(draft: ProjectDraft): Promise<{ workspaceId: string; sessionId: string }>
  modify(workspaceId: string, prompt: string): Promise<{ sessionId: string }>
}

export interface ProjectLocationGateway {
  preview(idea: string): Promise<PreparedProjectLocation>
  create(projectName: string): Promise<string>
}

export function createProjectController(
  workspaces: WorkspacesLike,
  sessions: SessionsLike,
  locations: ProjectLocationGateway,
): ProjectController {
  return {
    async prepare(input) {
      const idea = input.idea.trim()
      if (idea.length === 0) throw new Error('请先描述你想创建的项目')
      const location = await locations.preview(idea)
      return projectDraft({ idea, profileId: input.profileId, location })
    },
    async confirm(draft) {
      let workspaceId: string | null = null
      try {
        const path = await locations.create(draft.proposedName)
        const workspace = await workspaces.create({ path })
        workspaceId = workspace.workspaceId
        const sessionId = await sessions.create({ workspaceId, cwd: path })
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
  + '\n\n收尾要求：\n'
  + '1. 在项目根目录写 dsh-app.json（UTF-8）：{"schemaVersion":1,"type":"web","start":["pnpm","run","start"],"portEnv":"PORT","healthPath":"/","dataDir":"data"}。start 可换成 ["node","<入口文件>"]。\n'
  + '2. 服务必须从 PORT 环境变量读取监听端口（绑定 127.0.0.1），不要写死端口。\n'
  + '3. 业务数据一律写入 data/ 目录（本地文件或内嵌数据库），保证应用重启后数据保留。'
}

async function waitForSessionBinding(sessions: SessionsLike, sessionId: string) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const binding = sessions.binding(sessionId)
    if (binding !== undefined) return binding
    await new Promise<void>((resolveReady) => setTimeout(resolveReady, 0))
  }
  throw new Error('项目会话尚未准备好，请重试')
}
