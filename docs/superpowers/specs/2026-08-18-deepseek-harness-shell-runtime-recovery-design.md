# DeepSeek Harness 永久窗口壳与 Runtime 恢复设计

## 背景

当前桌面端在 Runtime 未就绪时显示本地 React 启动/恢复页，健康检查通过后直接把主 WebView 导航到 loopback 工作台。用户要求顶部三个窗口按钮参考 `D:\TraeCode\stock\stock-widget`，并在启动页、错误页和 DeepSeek Harness 工作台中始终保留。

2026-08-18 的诊断包显示，截图中的“Runtime 启动超时”并非服务启动缓慢：子进程约 3 秒内已因 `@dsh/desktop-plugin` 等待 `webServer` 而退出，外层健康检查仍等待 45 秒后才报告超时。隔离复现确认，给 Desktop profile 增加 `@deepseek-ai/dsh-web-app` 后，同一 Runtime 能返回 HTTP 200。

## 目标

1. 在所有应用阶段永久显示可信的本地窗口标题栏。
2. 标题栏使用红、黄、绿三个圆形按钮，分别执行关闭、最小化、最大化/还原。
3. 用户可见的产品文案使用全称 “DeepSeek Harness” 或 “DeepSeek Harness Desktop”。
4. 修复 Desktop profile 缺少 Web App bundle 导致的启动失败。
5. Runtime 子进程提前退出时立即显示真实错误，不再误报健康检查超时。
6. 保持 loopback DeepSeek Harness 工作台无法调用 Tauri IPC 的安全边界。
7. 改善小窗口布局、诊断信息和现有构建警告，并补齐回归测试。

## 非目标

- 不重命名兼容性接口，包括 `DSH_HOME`、`DSH_DESKTOP_*`、`@dsh/*`、清单字段、环境变量和内部类型名。
- 不重新实现 DeepSeek Harness 的设置、会话、插件或路由语义。
- 不把通用文件系统、shell、进程或窗口 IPC 暴露给 loopback 工作台。
- 不引入多 WebView 窗口架构。

## 方案选择

### 采用：可信本地壳 + iframe 内容区

本地 React 页面不再在 Runtime ready 后离开。它持有标题栏与启动/恢复状态；Runtime ready 后，内容区渲染指向随机 loopback 端口的 DeepSeek Harness iframe。父页面与 iframe 跨源隔离，窗口命令只存在于父页面。

该方案同时满足永久标题栏和最小权限边界。已验证当前 DeepSeek Harness 首页未返回 `X-Frame-Options` 或限制 `frame-ancestors` 的 CSP。

### 未采用：直接导航并向 loopback 页面开放窗口 IPC

实现较少，但会改变“工作台不持有 Tauri IPC”的既有安全约束。即使只开放窗口命令，也扩大了远程内容的能力面。

### 未采用：同一窗口内使用两个 WebView

可以隔离标题栏与内容，但窗口布局、生命周期、焦点、缩放和跨平台测试复杂度明显高于当前需求。

## 窗口壳与 UI

Tauri 主窗口关闭系统 decorations，保留阴影、透明背景和现有最小尺寸。React 根节点改为纵向窗口壳：

- 顶部标题栏高约 30px，左侧依次为红、黄、绿三个 14px 圆形按钮，间距和 hover 反馈参考 `stock-widget/src/components/TitleBar.vue`。
- 按钮 hover 时显示关闭、最小化、最大化/还原图标；每个按钮有中文 `aria-label` 和 `title`。
- 标题显示 “DeepSeek Harness Desktop”。标题栏空白区域支持拖拽，双击执行最大化/还原。
- 红色按钮关闭窗口，黄色按钮最小化，绿色按钮最大化/还原。
- 内容区占据剩余高度。启动/错误页在内容区内居中并自行处理窄高窗口，不让整个页面产生无意义的外层滚动条。
- iframe ready 后填满内容区；加载失败或 Runtime 退出时回到恢复页，标题栏保持可用。

窗口控制通过仅属于本地页面的明确 Rust commands 实现。capability 仍只绑定 bundled local 页面，不增加 remote URL 权限。

## Runtime 启动与数据流

### Profile 组成

Desktop profile 的 bundle 顺序固定为：

1. `@deepseek-ai/dsh-base`
2. `@deepseek-ai/dsh-web-app`
3. `@dsh/desktop-plugin`

Web App bundle 提供 `webStartup`、`webServer`、静态前端和官方 Web surface；Desktop 插件在其后覆盖 `web-runtime` 配置并注册社区市场 Host/Client 能力。

Runtime 构建脚本必须以可测试方式生成或修补 profile，使首次安装和已有同版本插件的启动都得到上述 bundle 列表。不能只依赖 Desktop 插件版本变化来触发修复。

### Ready 交付

健康检查通过后，Rust 不再调用 `window.navigate`。事件协议新增独立的 ready envelope：`{ kind: 'ready', operationId, rendererUrl }`。Reducer 只接受当前 operation 的 ready 事件，并把经过校验的 renderer URL 交给本地 React 壳；URL 必须仍满足：

- `http`；
- host 精确为 `127.0.0.1`；
- 端口等于当前受管子进程端口；
- 追加 Desktop mode、platform 和 session token 查询参数。

React 只把该 URL赋给 iframe。CSP 仅增加 loopback iframe 所需的 `frame-src http://127.0.0.1:*`，不扩大脚本、连接或 IPC 权限。

### 提前退出

启动阶段同时观察 HTTP 健康检查、取消信号和子进程状态：

- HTTP 首先通过：进入 ready。
- 用户取消：终止进程树并返回取消状态。
- 子进程首先退出：立即返回 `process` failure，错误摘要包含退出码或信号，并提示查看/导出诊断。
- 截止时间到且进程仍在：返回真正的 `health-timeout`。

子进程退出后，Runtime Manager 最多等待 500ms 让 stdout/stderr 日志任务写入最后一批输出，再生成错误摘要；超时后中止日志任务，不能让日志刷新阻塞恢复页。UI 不直接显示未脱敏的完整 stderr。

进入 ready 后仍保留一个进程退出监视任务。受管进程意外退出时，它为当前 operation 发出 failure event，Reducer 清除 renderer URL 并回到恢复页；应用主动关闭、repair 或被新 operation 取代时不产生误报。

## 品牌文案范围

以下用户可见位置使用全称：

- Tauri `productName`、窗口标题、Web `<title>`、安装/发布标题；
- React 启动、ready、错误与详情文案；
- Rust 返回给用户的错误消息；
- 社区市场中的能力风险提示；
- README、Runtime README、维护者说明和本次更新后的设计文档标题/正文。

技术标识保持不变；文档中介绍技术标识时可写作 “DeepSeek Harness（DSH）”，随后保留必要缩写。

## 诊断与维护优化

- 诊断摘要记录当前/最近尝试的 Runtime 版本、目标平台、失败阶段、failure code、子进程退出状态和日志文件名；仍排除会话内容、源码、凭据和环境变量值。
- Runtime rollback 后仍保留本次失败所需的最小清单元数据，避免诊断中的 `runtime` 无条件为 `null`。
- 启动前维护任务继续清理 `.staging-*`、`.rollback-*`、过期下载和日志；清理失败不能遮蔽主要启动结果。
- `tsdown` 配置从已弃用的 `external` 迁移到 `deps.neverBundle`，消除当前检查警告。

## 测试

遵循测试先行：每项行为先建立失败测试，再写最小实现。

### React/Vitest

- 标题栏在准备、失败和 ready iframe 三种状态都存在。
- 三个按钮调用对应本地窗口客户端方法。
- 双击拖拽区执行最大化/还原，按钮点击不触发拖拽。
- ready URL 只进入 iframe，不替换顶层页面。
- 800×600 与最小尺寸下内容区不产生页面级溢出。
- 所有用户可见产品名使用 “DeepSeek Harness”。

### Rust

- renderer URL 校验和查询参数组装。
- 子进程提前退出优先于健康检查 deadline，并返回 `process` failure。
- 活进程在 deadline 后仍返回 `health-timeout`。
- 新窗口命令只操作调用它们的主窗口。
- 诊断摘要包含失败 Runtime 元数据且继续执行脱敏规则。

### Runtime/插件

- 生成的 Desktop profile 同时包含 base、web-app 和 desktop-plugin bundles，顺序固定。
- 已存在同版本 Desktop 插件但 profile 缺 web-app 时仍能自愈。
- 隔离 Runtime 启动检查必须在 loopback 端口返回包含 DeepSeek Harness 标记的 HTTP 200。

### 完整验证

- `npm run check`
- `cargo test`（`src-tauri`）
- Windows Tauri 开发壳启动：标题栏永久保留，工作台可用，关闭/最小化/最大化正常。
- 原始 Runtime 复现命令由 red 变 green；移除 web-app bundle 时必须再次失败，以证明回归测试有效。

## 验收标准

1. 启动页、错误页和工作台顶部均显示一致的三个圆形窗口按钮与 “DeepSeek Harness Desktop”。
2. 三个按钮和拖拽/双击行为在 Windows 正常工作，并保留 macOS 构建兼容性。
3. 新建干净 profile 可在数秒内启动官方工作台，不再出现 `waiting for service: webServer`。
4. 子进程崩溃时立即报告真实进程错误，不等待 45 秒后误报超时。
5. loopback iframe 无法调用 Tauri commands；capability 中没有 remote URL 授权。
6. 用户可见产品文案不再使用孤立的 “DSH” 品牌缩写，技术接口不被重命名。
7. `npm run check`、`cargo test` 和隔离 Runtime 启动检查通过，构建无 `external` 弃用警告。
