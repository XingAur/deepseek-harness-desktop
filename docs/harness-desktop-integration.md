# Harness 桌面端接入说明

## 责任边界

- Harness Core 负责云效需求理解、背景/场景/目标确认、项目和调用链证据、变更决策、复核和学习门禁。
- DeepSeek Harness Desktop 负责宿主进程、凭证隔离、模型调用和任务事件展示。
- 模型只执行 Harness 发出的 `agent.request`；模型结果不能自行生成新方案。错误结果回到 Harness，由 Harness 决定是否重新定位和再次发出受限执行请求。
- Codex 只是当前桌面端默认的模型执行适配器，不是 Harness Core 的必需依赖。后续可注入 DeepSeek 或其他 `execute` 实现。

执行器不是写死替换关系，选择顺序为：任务中的 `agent_backend`（非 `host-bridge`）→ 进程环境变量 `DSH_HARNESS_EXECUTOR` → 兼容性默认 `codex`。Host 应通过 `runDesktopHarnessHost({ executors: { deepseek: ... } })` 注入 DeepSeek 的真实执行实现。若选择的执行器没有注册，Host 只返回 `worker_backend_unavailable`，不会自动改用 Codex。

当前可直接注册的是统一的 DeepSeek 执行器：同一个当前选定模型可被 Harness 用于理解、规划、Worker 执行和 Reviewer 审查；角色由 Harness 决策，Host 不再把模型限制成 Reviewer：

```ts
import { createDeepSeekExecutor } from '@dsh/agent-adapter/deepseek-harness-executor'
import { createDeepSeekAdapter } from '@dsh/agent-adapter/providers/openai-compatible'

const deepseekExecutor = createDeepSeekExecutor({
  adapter: createDeepSeekAdapter(),
  apiKey: credentialFromDesktopVault,
  model: 'deepseek-chat',
})

await runDesktopHarnessHost({
  input,
  output,
  sidecar,
  executors: { deepseek: deepseekExecutor },
})
```

`credentialFromDesktopVault` 代表桌面端安全凭证存储返回的值，不得写入任务契约、Harness JSONL、日志或前端状态。当前文本 Provider 是否能修改代码，取决于 Harness 下发的 Worker 工具契约；Host 不会偷偷替换模型或虚构已修改文件。

桌面端使用方式：先在“模型与 Agent → API 模型”配置对应模型凭证，再到“Harness 任务”填写当前统一模型 ID。`agent_backend` 只选择执行宿主，模型 ID 决定所有 Harness 阶段使用的模型；未配置凭证或执行能力时会明确返回不可用/阻断，不会静默切换到其他模型。

新需求的首次入口是：输入云效需求 URL 或工作项 ID，通过“选择本机目录…”按钮（原生目录选择器，返回规范化绝对路径）或手工填写归档根目录，选择云效 profile 与当前统一模型后点击“只读归档并起草分析文档”。该步骤只读取云效并在归档根目录生成完整任务包的 source/analysis/engineering/execution 目录；原始需求、评论及评论图片、正文图片、附件和文档会进入 source。归档完成后，若任务携带当前模型，Harness 会在同一次操作中把需求侧分析文档（需求理解、目标愿望、场景、功能需求、验收标准、约束、需求规划、PRD）交给该模型基于归档证据起草：起草文档标记为 `model_generated`（模型草稿，非已确认事实），真实业务歧义以 open_questions 形式带回界面；项目/工程/执行侧文档仍保持 `pending`，等待选定项目后的受控执行生成。模型不可用或起草失败不影响归档结果，文档保持 `pending` 且失败原因可追溯（`analysis/intake_generation_report.json`），可重新归档重试。归档任务包目录会自动回填到任务页，之后选择目标项目、知识库、GitLab profile 和数据库 profile，即可进入 Harness 执行阶段。

Harness 任务页支持用户选择任务包/归档目录。选择任务包目录后，`engineering/task_contract.json` 和 `analysis/requirement_understanding.json` 由 Harness 任务包约定自动定位，用户无需手工逐项准备；已有旧版契约仍可在兼容入口填写。

“MCP 连接维护”和“数据库维护”是独立页面。MCP profile 可标记为云效、GitLab 或其他 MCP；数据库 profile 单独保存、默认只读，Harness 任务只选择 profile。连接地址只保存公共配置，密码、Token 等敏感值只能通过安全凭证引用提供。

## 开发运行

桌面端通过受控环境变量连接本地 Harness Core：

```text
DSH_HARNESS_DEV_MODE=1
DSH_HARNESS_HOST_PATH=/absolute/path/to/packages/dsh-plugin-desktop/lib/harness-host.js
DSH_HARNESS_NODE=/absolute/path/to/node
HARNESS_CORE_ROOT=/absolute/path/to/Harness
HARNESS_PYTHON=/absolute/path/to/Harness/.venv/bin/python
```

开发模式如果不设置 `HARNESS_PYTHON`，Host 会尝试使用 `HARNESS_CORE_ROOT/.venv/bin/python`（Windows 为 `.venv/Scripts/python.exe`）；找不到兼容解释器时直接阻断，不会回退到可能过旧的系统 Python。

应用会自动把自己的 Agent 数据库路径作为 `HARNESS_DB_PATH` 传给 Harness Host。凭证不会进入 Harness JSONL 协议。

## 生产打包

打包链路：`npm run harness:vendor` 先把本机 Harness Core 源码 vendor 进仓库（`vendor/harness-core`，数据类文件命中真实凭证样式赋值直接失败，模板占位符放行）；`npm run tauri build` 的 beforeBuildCommand 自动执行 `scripts/assemble-harness-core.mjs`，把 vendor 副本组装到 `build/harness-core/`，下载**可重定位**的 python-build-standalone 到 `runtime/` 并安装 requirements（普通 venv 绝对路径写死、不可重定位，不能用于打包），最后由 `tauri.conf.json` 映射为安装包资源 `harness/core`。Host 按 `runtime/bin/python3`（macOS）/ `runtime/python.exe`（Windows）查找内嵌解释器；tarball 缓存在 `build/cache/`，重复构建幂等，`DSH_HARNESS_PYTHON_TARBALL` 可指定离线包。CI（`desktop.yml`，`desktop-v*` tag 触发）执行同一流程。vendor 同步是**自动的**：每次构建组装资源前会自动检测本机 Harness 源目录（`HARNESS_CORE_SOURCE` 环境变量 > 上次记录的路径 > 默认路径），源码有变更就自动重新 vendor 并通过密钥扫描门禁；CI 或未找到源目录时使用仓库内 vendor 副本。修改 Harness 仓库后正常构建即可，无需记忆任何命令。

## 使用入口

打开 `模型与 Agent → Harness 任务`，先完成云效只读归档，再填写目标项目、知识库和执行授权，选择模型及各连接 profile，点击“按 Harness 决策执行”。缺少任一项时，入口保持禁用；任务状态和错误码通过 `harness-event` 回传。旧版任务仍可展开兼容区域，手工填写契约文件和需求理解文件。

## 执行门禁与模型链路

- **理解补齐**：归档产出的 pending 理解不再阻断执行。点击执行时，Core 先用当前模型基于归档证据 + 目标项目只读事实产出 `analysis/requirement_understanding.json`（九项检查）与 `engineering/task_contract.json`；确定性校验兜底——目标项目必须是 git 仓库根、允许路径必须真实存在、验证命令被重写为 Core 解释器的 unittest 形式、业务歧义/证据缺口以 blockers 返回而不是伪造 ready。
- **凭证通道**：选中的云效/GitLab/数据库 profile 由 Rust 从安全凭证库解析，只注入宿主进程环境（`ALIYUN_DEVOPS_PAT`、`DSH_GITLAB_TOKEN`、`DSH_DATABASE_DSN`），不进入 JSONL 协议。数据库 DSN 在归档时做一次只读探测，结果写入 `engineering/database_probe.json`。
- **连接测试**：桌面端“校验配置”对 endpoint 做真实 TCP 可达性探测（不带凭证）；认证级验证由 Core 只读探测阶段完成。
- **任意模型**：执行后端新增 `openai-compatible`——任意 OpenAI 兼容端点（OpenAI/Qwen/GLM/Kimi/本地网关）在模型中心保存 `openai-compatible` 凭证并设置 `DSH_OPENAI_BASE_URL` 后即可选用任意模型 ID；`deepseek-*` 模型仍自动绑定 DeepSeek 执行器，Codex 走 CLI Host。
