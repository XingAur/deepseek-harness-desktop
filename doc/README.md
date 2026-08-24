# DeepSeek Harness Desktop 后续开发说明

本文面向后续接手本仓库的开发者，说明当前产品定位、已经完成的底座、剩余工作的优先级、开发门禁和提交规则。开始修改代码前请先阅读本文，再阅读对应模块下的测试和 `docs/superpowers/specs` 中的历史设计。

## 一、产品定位

本项目面向希望安装后直接使用 AI 编程能力的普通 Windows 用户。桌面端负责隐藏 Runtime、Node、pnpm、模型配置、更新、诊断和本机权限等复杂度，同时保留高级开发者需要的 Profile、远程工作区和受治理扩展能力。

目标用户流程：

```text
下载安装包
→ 首次启动自动准备受管 Runtime
→ 配置模型供应商和 API Key
→ 创建或选择项目
→ 发送第一条消息并实时看到回复
→ 安全恢复、升级和继续会话
```

开发优先级必须遵循：

```text
可靠可用
→ 小白开箱即用
→ 日常体验
→ 受治理扩展
→ 远程与移动端
→ 生态推广
```

不要为了增加演示功能而推迟会话、安装、升级和数据安全问题。

## 二、当前版本与仓库入口

- 当前桌面版本：`0.1.19`。
- 主要前端：`src/`。
- Tauri/Rust 桌面壳：`src-tauri/src/`。
- Desktop 插件：`packages/dsh-plugin-desktop/`。
- Runtime 组装和发布脚本：`scripts/`。
- 安装后 E2E：`e2e/`。
- GitHub 发布工作流：`.github/workflows/`。
- 受治理扩展架构：`docs/architecture/extension-platform.md`。

仓库根目录的 `task_plan.md`、`findings.md` 和 `progress.md` 是本地开发过程文件，已通过 `.git/info/exclude` 排除，不得提交。密钥、诊断包、用户数据、构建缓存和 AI 对话同样不得进入提交。

## 三、已经完成的底座

以下能力已经有实现和测试。后续优先补充真实场景验证，不要重复造一套并行系统：

- Generation 生命周期、候选激活和进程树清理。
- Profile pending / last-known-good 恢复。
- Windows 与 macOS Platform Adapter。
- 固定用户数据目录、迁移和诊断导出。
- Runtime 签名清单、版本目录、健康检查、快速启动和回滚。
- Windows 应用更新签名和 macOS 手动 DMG 更新路径。
- 安全导航、受限 Bridge、托盘和外部链接处理。
- 本地项目卡片、重命名、内置封面、置顶、删除确认和回收站处理。
- 本地应用启动器和受管静态服务。
- 自动上游同步、不可变 Release 资产和双平台构建工作流。
- 确定性模型夹具、单元测试、Rust 测试和部分安装包 E2E。

当前主要风险是：会话实时一致性、安装包完整闭环、Runtime 契约漂移和首次模型配置门槛。

## 四、明确的产品边界

### 保持

- Windows 完整安装包是主要发行形态。
- Runtime 由桌面端管理，用户无需自己安装 Node、pnpm 或 DSH。
- 兼容 Runtime 快速启动；不兼容时可见升级并支持回滚。
- Session、Workspace、Profile 尽量使用官方 ID、Store 和事件投影。
- 普通用户默认只看到模型、项目和对话。
- 凭证只进入 Harness Credential Store 或操作系统安全存储。

### 不做

- 不恢复在线小安装器作为主产品。
- 不恢复社区插件市场和插件核心导航。
- 不允许扩展直接获得通用 Tauri IPC、任意 shell 或任意文件系统权限。
- 不将 Runtime 端口直接暴露到公网供手机访问。
- 不在没有评测结果时默认开启自动任务路由。
- 不为尚未接入的 Runtime 提前进行大规模抽象重构。

## 五、长期开发优先级

## P0：发布阻断级稳定性

P0 未完成前，不应投入移动端、插件目录或大规模多 Provider 工作。

### P0.1 Session Event 单一事实源

目标：创建、发送、回复、标题更新和切换会话全程不依赖刷新。

实施要求：

1. 左侧标题、右侧消息和运行状态统一来自官方 Session Store 与 `session/event` 投影。
2. 删除 `packages/dsh-plugin-desktop/src/client/project-controller.ts` 中的 `waitForSessionBinding` 轮询。
3. 新会话固定执行 `create/connect → binding → prompt → open`；官方契约明确允许在打开页面前向新 Session machine 写入。
4. 上游承诺 create/connect 完成后 binding 可同步获取；不满足时立即产生可诊断错误，不通过轮询隐藏契约破坏。
5. 页面不得维护第二份会话正文权威状态。
6. WebSocket 中断时显示重连状态；恢复后补齐事件，不创建重复会话。

测试要求：

- 单元测试断言准确调用顺序。
- 新会话消息和回复无需刷新即可出现。
- 标题由事件投影更新。
- 两个会话反复切换时内容始终匹配。
- 退出并重新启动后仍能恢复会话。
- Runtime 更新后自动执行 Session Contract fixture。

出口条件：安装版连续完成 30 轮创建、回复和切换，不出现空白、重复会话和刷新依赖。

### P0.2 Windows 完整安装生命周期 E2E

每个 PR 使用确定性 Runtime 运行：

```text
构建候选应用
→ 启动 Runtime
→ 创建 Unicode 路径项目
→ 创建会话并发送消息
→ 标题和回复实时出现
→ 创建第二个会话并切换
→ 退出并再次启动
→ 会话仍可见
```

每日或 Release Candidate 运行：

```text
静默安装
→ 首次启动
→ 内置 Runtime 准备
→ 创建项目并对话
→ 退出重启
→ 覆盖升级
→ 用户数据保留
→ 卸载并验证用户选择
```

真实 DeepSeek API 测试只允许通过 `workflow_dispatch` 手动执行。Key 来自 GitHub Secret；日志和截图不得包含认证头、Key、用户路径和敏感对话内容。

### P0.3 Runtime 激活前契约门禁

候选 Runtime 激活前验证：

- Runtime 版本、Profile ID 和配置修订；
- `/api/events.mux`、`/api/events.host` WebSocket；
- Workspace list/create/connect；
- Session create/binding/open/prompt/cancel；
- Desktop 插件关键 Slot；
- Bridge 协议版本和能力集合；
- 进程稳定窗口和受管进程树身份。

任何失败都不得切换 active pointer，必须继续使用 last-known-good，并输出明确失败阶段。

### P0.4 Bridge 和 Host Contract 版本化

请求至少包含：

```text
protocolVersion
requestId
generationId
capability
typed payload
```

响应必须返回类型化结果或稳定错误类别。父窗口只接受当前 iframe、受管 origin 和当前 Generation 的请求。Generation 替换后旧请求立即失效。

## P1：小白开箱即用

### P1.1 首次模型配置

流程：

```text
选择供应商
→ 输入 API Key
→ 测试连接
→ 读取官方模型目录
→ 推荐默认模型
→ 进入工作台
```

开发前先验证固定 DSH Runtime 的 Settings、Credentials、Provider Route 和模型目录契约。第一批优先 DeepSeek；其他 Provider 按官方契约和维护成本逐个接入。

Key 不得进入 localStorage、日志、诊断包、崩溃报告和仓库。界面支持跳过，复杂模型 ID 和路由设置默认隐藏。

### P1.2 第一次成功对话

产品成功标准不是“安装完成”，而是“用户收到第一条有效回复”。需要分别记录壳层渲染、Runtime 就绪、模型验证、项目创建、首个 Session、首个事件和首个回复的耗时，但不得记录提示词和回复正文。

### P1.3 自愈与普通用户错误文案

- 区分网络、密钥、Runtime、项目权限和安全校验错误。
- 可恢复错误提供重试；签名和契约错误停止自动重试。
- 连续启动失败触发熔断和 last-known-good。
- 每个错误界面只保留一个推荐主操作，并提供诊断导出。

## P2：日常体验

### 会话管理

- 搜索、重命名、归档和最近会话。
- 运行、等待、失败、取消和恢复状态。
- 会话时间线和工具执行摘要。
- 重复空会话只提供有证据的清理建议，不自动删除有效会话。

### 本地用量统计

- Token、调用次数、首 token 延迟、总耗时、缓存命中和失败率。
- 按项目、Profile、模型和日期查看。
- 成本必须使用用户确认的价格配置。
- 默认仅本地保存，支持导出和清空。
- 不记录提示词、回复正文、Key 和敏感工具参数。

### 性能

- 启动页在参考机器 1.5 秒内可见。
- 兼容 Runtime 温启动 8 秒内进入工作台。
- 温启动激活前不访问更新网络。
- 大型会话使用虚拟化或分页。
- 每个启动阶段分别计时。

### 安全皮肤

支持浅色、深色、跟随系统、内置配色、字号、密度、圆角、代码字体和动效偏好。

皮肤只允许声明式 Design Token。禁止任意 CSS、JavaScript、远程字体和远程资源。主题文件必须有 schema 版本、兼容范围和字段白名单，并覆盖高对比度、键盘焦点和减少动态效果。

## P3：受治理扩展和 SSH

### AgentRuntimeAdapter

先固定最小状态：

```text
starting / ready / streaming / waiting / cancelled / failed
```

接口覆盖能力发现、会话创建、恢复、取消、重试、结构化事件、错误分类和用量回传。只有接入第二个真实 Runtime 或 Provider 时才扩展抽象。

### Provider、MCP 和 Skills

- 按 Profile 隔离配置、启用状态和权限。
- 文件、网络、子进程、剪贴板和外部应用分别授权。
- 支持单次允许、持续允许、拒绝和撤销。
- 扩展失败只影响自身。
- 审计记录不包含凭证和敏感正文。

### 工作台扩展

首批只开放有限 Slot：左侧辅助面板、右侧详情面板、会话工具栏动作、项目卡片动作、Git/任务/文件预览和结构化产物查看器。

扩展必须声明 Contract 版本、Runtime 兼容范围、权限、来源、签名和完整性哈希。扩展不能直接调用 Tauri IPC。该能力属于高级设置，不恢复插件市场。

### SSH 远程工作区

实施顺序：

1. SSH 主机配置和连接测试。
2. 系统 SSH Agent、密钥引用和安全存储。
3. `known_hosts` 指纹确认和变化阻断。
4. 远程目录选择和工作区范围授权。
5. 远程 Runtime 健康及版本检查。
6. 受控 SSH 隧道和 Session Event。
7. 远程项目卡片、断线状态和重连。
8. 远程取消、进程清理和跨平台 E2E。

禁止自动接受未知主机、复制用户私钥、公开 Runtime 端口和默认授权整台服务器文件系统。

## P4：移动伴侣与智能化实验

### 自动任务路由

只借鉴离线评测、任务分类和两阶段工具开放，不复制依赖首轮临时 Session 状态的实现。完成固定任务集、安全、延迟、成本和失败率评估后才能灰度。普通用户只看到“自动模式”。

### 移动伴侣

移动端不运行完整 Runtime。第一阶段只支持：

- 查看项目和会话状态；
- 接收任务完成、失败和权限通知；
- 查看回复和执行摘要；
- 暂停、取消、批准或拒绝；
- 发送补充消息；
- 查看简化改动摘要。

连接必须使用设备身份、二维码配对、端到端加密、短期令牌和设备撤销。第一阶段不做完整代码编辑器、任意终端、完整文件系统和本地 Runtime。

### 可选扩展目录

只先定义名称、版本、来源、兼容 Runtime、验证状态、权限、最后验证时间和完整性哈希。签名、兼容测试、权限确认、隔离和回滚未成熟前，不开放安装目录。

## P5：生态与维护

- 提交高质量生态索引和 Awesome 列表。
- 为正式版本提供安装、升级、回滚和兼容证据。
- 发布 Runtime/Contract 兼容矩阵。
- 完善安全、数据目录、卸载和隐私说明。
- 建立扩展审核、漏洞报告和撤回流程。
- 提供确定性 fixture 和贡献指南。

Star 和未经复现的活跃用户数据不得作为产品事实或开发优先级依据。

## 六、测试和发布门禁

测试从低到高分为：

1. 纯函数和状态机单元测试。
2. Rust 服务与受限系统集成测试。
3. Desktop 插件契约和组件测试。
4. Runtime 组装、签名和协议 fixture。
5. 打包候选 WebDriver E2E。
6. Windows 安装、升级和卸载 E2E。
7. 手动真实 API 与真实机器验收。

正式发布最低条件：

```powershell
npm run check
cargo test --locked --manifest-path src-tauri/Cargo.toml
```

同时必须满足：

- Runtime/Bridge 兼容测试通过。
- Windows 安装生命周期 E2E 通过。
- Release 资产签名、哈希和版本一致。
- 升级不丢失 Profile、项目和会话。
- 日志和诊断敏感信息扫描通过。
- 没有已知刷新依赖、启动失败、卸载卡死或数据破坏缺陷。

macOS 未使用 Apple Developer ID 签名和公证时，必须继续明确说明限制。

## 七、开发和提交规则

每个独立子项目按以下顺序执行：

```text
核对事实和上游契约
→ 编写设计规格
→ 确认范围
→ 编写失败测试
→ 最小实现
→ 定向验证
→ 独立提交
→ 阶段完整验证
```

每个可验收步骤单独提交，使用中文 Conventional Commit：

```text
test(session): 增加新会话实时投影回归测试
fix(session): 使用官方绑定契约打开新会话
test(e2e): 覆盖双会话无刷新切换
docs: 更新会话一致性开发进度
```

提交前必须：

1. 只暂存本步骤相关文件。
2. 执行相关最小测试。
3. 执行 `git diff --cached --check`。
4. 检查没有密钥、用户数据、诊断、构建产物和 AI 对话。
5. 阶段结束再执行完整门禁。

不要使用 `git reset --hard`、批量清理用户目录或自动删除有效会话作为恢复手段。

## 八、下一步从这里开始

第一个开发子项目是 P0.1：

1. 核对固定 Runtime 的 SessionRuntime create/connect 同步 binding 契约。
2. 为 `create/connect → binding → prompt → open` 写失败测试并提交。
3. 删除 `waitForSessionBinding` 轮询，实现严格 binding 检查并提交。
4. 增加标题、消息和双会话切换的确定性 E2E 并提交。
5. 增加 Runtime 升级后的 Session Contract fixture 并提交。
6. 运行插件、Web、Rust 和安装候选验证，记录结果。

完成一个步骤并验证后再进入下一步，不把多个不相关功能压进同一提交。

## 九、外部项目参考原则

- 官方 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) 的 Session、Workspace、Profile 和插件契约是上游权威。
- [anywhere-labs/deepseek-harness-desktop](https://github.com/anywhere-labs/deepseek-harness-desktop) 主要参考 Generation、Profile 恢复和 Host 边界。
- [dataelement/dsh-desktop](https://github.com/dataelement/dsh-desktop) 主要参考模型引导、进程管理和打包验证。
- [nexu-io/open-design](https://github.com/nexu-io/open-design) 主要参考结构化 Agent Runtime Adapter。
- [winfunc/opcode](https://github.com/winfunc/opcode) 只参考会话和用量产品模式，不复制 AGPL 代码。
- 已停止维护的项目只参考历史设计，不作为持续依赖。

借鉴外部项目之前必须重新核对源码、维护状态、License、安全边界和当前固定 DSH Runtime 的兼容性。
