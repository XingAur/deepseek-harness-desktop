# Harness 功能完整化实施计划

## 目标

把桌面端与 Harness Core 统一为可实际使用的 Harness 工作台：用户只选择本机归档根目录、目标项目、当前模型和必要的业务授权；Harness 自动完成云效只读证据收集、原始资料归档、需求理解/规划/PRD/项目理解/项目规划/执行/验证/审查，并在确有业务歧义时才向用户提问。GitLab、云效和外部数据库连接采用可维护 profile；数据库 profile 在独立的数据库维护页管理，任务页只选择 profile。

## 设计边界

- 所有 Harness 阶段使用当前任务选择的同一个模型；Harness 负责治理、证据、状态机、重定位和重新决策，不再限制某个模型只能 Reviewer。
- 内部技术失败、归档失败、MCP 调用失败、代码定位失败、验证失败都回灌 Harness 自动重试/重新决策；只有真实业务选择、目标项目无法确定、外部高风险写入授权才可形成用户澄清。
- 默认只读云效、只读 GitLab、只读数据库和本地 worktree；不自动 push、部署、写云效或写业务数据库。
- 保留已有用户改动和旧桥接字段兼容，不回退、不重置工作区。

## 实施顺序

### 1. Harness Core：完整任务包和归档协议

- 新增 `app/requirement_package.py`，定义版本化任务包清单和稳定目录结构：
  - `source/`：原始需求、原始正文/HTML、原始评论（评论图片）、附件、正文图片、父需求及父需求附件/文档。
  - `analysis/`：需求理解、目标愿望、场景、功能需求、验收标准、约束/非目标、需求规划、项目理解、项目规划、实现计划、验证计划、风险回滚、PRD。
  - `engineering/`：工程证据、源码调用链、技术决策、变更归属、任务契约。
  - `execution/`：执行事件、测试结果、diff、审查、失败与重决策记录。
  - 根目录 `manifest.json`：每个文件的来源、状态、sha256、媒体引用、缺失原因和是否为 Harness 自动生成。
- 先补单元测试，覆盖完整目录、原始内容/媒体复制、父需求内容、已有工件映射、部分失败可追踪、不可把缺失信息伪造成已确认事实。
- 将任务包导出接入现有 `write_run_outputs` 和 Yunxiao archive run 记录，保留旧的 `run_<id>` 输出兼容。

### 2. Harness Core：自动决策和澄清门禁

- 扩展澄清分类：`internal_recoverable` 自动回灌；`business_choice`、`project_choice`、`external_authorization` 才进入待用户回答状态。
- 对归档、MCP、源码分析、模型调用、测试失败生成结构化 recovery record，并让下一轮使用失败证据重新决策。
- 为需求理解、规划、PRD 等工件写入来源和状态；没有模型/证据时标记 `pending` 或 `blocked`，不编造业务结论。
- 补充 Python 测试，验证 Harness 可自动恢复的错误不再产生用户澄清，真正歧义仍能输出可回答的问题。

### 3. Harness Core：独立配置和数据库 profile API

- 复用 `ManagerProviderRepository` 的加密凭证和审计能力，增加面向桌面端的无敏感信息 profile DTO/服务：GitLab、云效、数据库分别可列出、保存、启用/停用、连接测试。
- 数据库维护单独使用 `database` profile 记录 driver/host/port/database/schema/只读策略和凭证状态；任务只保存 `database_profile_id`，不保存密码/token。
- 增加 profile API/CLI 的单元测试，确认列表和任务快照不泄露 secret，数据库默认只读，外部写操作仍需独立授权。

### 4. Desktop：任务包选择和维护界面

- 将 Harness 任务页从手填路径改为：归档根目录选择、目标项目选择、当前模型选择、云效/GitLab profile 选择、数据库 profile 选择、需求入口和授权项。
- 增加独立 `数据库维护` 页，并在 MCP/连接维护区域维护 GitLab、云效和其他 MCP profile；任务页只消费 profile 列表。
- 增加本机目录选择桥接命令，路径必须绝对、可读/可创建并显示实际落盘目录；任务启动向 Core 传递 archive root、profile IDs、selected model ID 和 contract 版本。
- 保留旧路径字段兼容读取，但新界面不要求用户手工准备 Harness 生成的 task contract/understanding 文件。
- 先补 React/Tauri/adapter 测试，再实现组件和命令合同。

### 5. Desktop：统一模型执行链

- 任务选择的模型作为分析、决策支持、代码修改、验证、审查的统一模型标识传给 Harness host；模型来源可以是 Codex、DeepSeek 或已维护的其他模型，不使用硬编码 allowlist。
- 删除 DeepSeek “Reviewer only”的产品限制；是否具备代码执行能力由当前 host adapter/权限能力报告，不得静默替换模型。能力不足时由 Harness 记录内部失败并重新定位或进入真正授权/能力选择。
- 更新执行器选择、sidecar payload、Tauri 环境注入和测试，确保 selected model 在各阶段一致且任务 JSON 只保存 profile ID/脱敏元数据。

### 6. 验证和交付

- 运行 Harness 相关 Python 单测、Desktop adapter/plugin 单测、TypeScript 构建和 Rust `cargo check`（若本机依赖允许）。
- 查看两个仓库逐行 diff，检查敏感信息、外部写入边界、旧字段兼容和失败恢复路径。
- 默认只交付本地修改和验证结果；本次在用户明确授权后，执行 Git 远端推送和远端桌面包发布跟踪，不执行云效写入或业务数据库写入。
