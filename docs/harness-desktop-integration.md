# Harness 桌面端接入说明

## 责任边界

- Harness Core 负责云效需求理解、背景/场景/目标确认、项目和调用链证据、变更决策、复核和学习门禁。
- DeepSeek Harness Desktop 负责宿主进程、凭证隔离、模型调用和任务事件展示。
- 模型只执行 Harness 发出的 `agent.request`；模型结果不能自行生成新方案。错误结果回到 Harness，由 Harness 决定是否重新定位和再次发出受限执行请求。
- Codex 只是当前桌面端默认的模型执行适配器，不是 Harness Core 的必需依赖。后续可注入 DeepSeek 或其他 `execute` 实现。

执行器不是写死替换关系，选择顺序为：任务中的 `agent_backend`（非 `host-bridge`）→ 进程环境变量 `DSH_HARNESS_EXECUTOR` → 兼容性默认 `codex`。Host 应通过 `runDesktopHarnessHost({ executors: { deepseek: ... } })` 注入 DeepSeek 的真实执行实现。若选择的执行器没有注册，Host 只返回 `worker_backend_unavailable`，不会自动改用 Codex。

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

## 生产打包边界

安装包只允许从资源目录加载 Host；不会接受任意路径替换。正式发布前需要把经过密钥扫描的 Harness release bundle（包含兼容的 Python 虚拟环境）放入 `harness/core`，并为该资源建立版本清单和哈希验收。未完成这一步时，生产包会安全地保持不可用，不会静默回退到用户机器上的任意 Harness 目录。

## 使用入口

打开 `模型与 Agent → Harness 任务`，填入 Harness 需求归档阶段生成的任务契约、需求理解、目标项目和知识库绝对路径，再点击“按 Harness 决策执行”。缺少任一项时，入口保持禁用；任务状态和错误码通过 `harness-event` 回传。
