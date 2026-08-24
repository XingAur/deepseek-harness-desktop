# Runtime Session 契约门禁设计

## 一、目标

为候选 DeepSeek Harness Runtime 增加独立、确定性的 Session 契约门禁。在 Runtime 构建、上游同步和正式发布前，直接启动候选 Runtime 并验证真实公开契约，避免仅靠类型、静态导出或桌面 UI 测试发现不了的行为漂移进入 active Runtime。

本阶段只覆盖 P0.1 的 Session 核心链路，不扩展到完整 P0.3 的 Profile、Bridge、Slot 和进程树契约。

## 二、方案选择

采用黑盒候选 Runtime 集成检查：使用仓库已有的确定性模型服务和临时数据目录，启动真实组装产物，通过 Runtime 公开接口完成 Workspace 与 Session 生命周期验证。

没有采用以下方案：

- 仅使用 TypeScript Mock：速度快，但不能发现候选 Runtime 的真实时序或事件协议漂移。
- 每次运行完整安装包 UI E2E：覆盖面大，但构建和执行成本高，并与现有安装包会话回归重复。

安装包 E2E 继续作为更高层发布门禁；本检查位于静态能力检查和完整桌面 E2E 之间。

## 三、契约范围

单次检查必须按以下顺序执行：

```text
准备临时数据目录与确定性模型
→ 启动候选 Runtime
→ 等待 Runtime ready
→ 创建 Unicode 路径 Workspace
→ 创建或连接 Session
→ 同步读取 Session binding
→ 向 Session machine 写入确定性 prompt
→ 打开 Session
→ 从官方事件流观察用户消息与确定性回复
→ 取消或关闭 Session
→ 终止 Runtime 并清理临时目录
```

成功条件：

1. create/connect 返回的 Session ID 能被 `binding(id)` 同步解析。
2. `prompt` 可在 `open` 前写入，且不会创建第二个 Session。
3. 事件流中能观察到同一 Session 的用户消息和确定性回复。
4. 检查结束后 Runtime 进程退出，临时 Workspace 与凭证配置不残留。

## 四、组件边界

### 4.1 契约执行器

新增独立 Node.js 模块，负责状态机、阶段超时、结果归一化和资源清理。它通过注入的 Runtime 驱动执行，不直接读取桌面 UI，也不修改 active pointer。

执行器对外只返回两种结果：

```ts
type RuntimeSessionContractResult =
  | { ok: true; durationMs: number; stages: StageResult[] }
  | { ok: false; failedStage: ContractStage; category: ContractFailureCategory; stages: StageResult[] }
```

### 4.2 真实 Runtime 驱动

驱动负责启动候选 Runtime、发现 loopback 地址、建立公开客户端连接、创建 Workspace/Session、订阅事件及关闭进程。驱动只使用候选组装产物中已公开的入口和 API，不导入 Runtime 私有源码。

### 4.3 确定性模型夹具

复用 `scripts/e2e/fake-model-provider.mjs` 或现有 DeepSeek 假服务，固定回复标记为 `SESSION_CONTRACT_PONG`。夹具只监听 loopback，不需要真实 API Key，也不访问公网。

### 4.4 命令行入口

新增仓库命令接收候选 Runtime 根目录和版本信息。命令退出码为 `0` 表示契约通过，非 `0` 表示失败；控制台只输出阶段、耗时、稳定错误类别和脱敏诊断路径。

## 五、阶段与错误模型

固定阶段名：

```text
runtime-start
runtime-ready
workspace-create
session-create
session-binding
session-prompt
session-open
session-event
session-close
cleanup
```

稳定错误类别：

```text
timeout
process-exited
protocol-mismatch
binding-missing
event-missing
cleanup-failed
internal
```

每个阶段单独计时并使用有限超时。失败后不继续执行后续业务阶段，但必须进入清理流程。清理错误不会覆盖原始失败，只作为附加诊断；若业务阶段全部成功而清理失败，则最终以 `cleanup-failed` 失败。

## 六、数据安全与诊断

- 全部数据写入系统临时目录，并在 `finally` 中清理。
- 不输出 prompt、回复正文、认证头、API Key、用户目录或完整临时路径。
- 诊断只记录 Runtime 版本、阶段、持续时间、稳定错误类别、进程退出码和经过脱敏的协议摘要。
- 确定性模型配置使用仅测试可见的占位凭证，不进入正式凭证存储。
- Runtime 只绑定 loopback；夹具不得对局域网或公网开放端口。

## 七、接入位置

门禁分三层接入：

1. `runtime:session-contract`：开发者可针对已组装候选 Runtime 独立运行。
2. Runtime 构建与上游同步：静态能力检查通过后、发布归档前执行。
3. 正式 Release：Session 契约不通过时禁止生成可发布 Runtime 元数据和桌面 Release。

普通 `build:web` 不运行该检查，避免前端开发被真实 Runtime 启动成本阻塞。安装包 E2E 继续验证桌面层、持久化和用户交互，不被本门禁取代。

Runtime 自动升级的应用内激活接入留给 P0.3：本阶段先让构建和发布流程具备可靠证据，不在同一提交中改变 Rust 激活事务。

## 八、测试策略

### 单元测试

使用可编程假驱动覆盖：

- 严格调用顺序；
- binding 首次缺失立即失败，不轮询；
- 每个阶段超时映射到稳定类别；
- 进程提前退出；
- 回复事件缺失；
- 主失败与清理失败同时发生时保留主失败；
- 成功与失败均执行资源清理。

### 集成测试

针对真实组装候选 Runtime：

- 使用 Unicode Workspace 路径；
- 使用确定性模型收到 `SESSION_CONTRACT_PONG`；
- 验证 create/connect、同步 binding、prompt-before-open 和 Session Event；
- 验证结束后无残留受管进程。

### 发布证据

工作流保存机器可读 JSON 报告，但不把临时 Runtime 日志和用户路径上传为公开 Artifact。报告至少包含 Runtime 版本、平台、通过状态、各阶段耗时和错误类别。

## 九、验收条件

1. 固定候选 Runtime 的真实契约检查稳定通过。
2. 人为破坏同步 binding、事件回复或 Runtime ready 时，门禁在对应阶段确定性失败。
3. 失败不会生成成功发布元数据，也不会修改桌面 active Runtime。
4. 检查不访问真实模型、不要求用户 Key、不残留进程和临时目录。
5. 单元测试、脚本测试和现有安装包会话 E2E 职责清晰，不重复维护 UI 选择器。

## 十、非目标

- 不验证所有模型供应商。
- 不验证桌面 UI、安装器、升级安装和卸载。
- 不在本阶段接入应用内 Runtime 激活事务。
- 不新增通用多 Runtime Adapter。
- 不验证 Profile、Bridge、扩展 Slot、SSH 或移动端能力。
