# HIS Harness v0.52 Controlled Node Runtime Implementation Plan

**Status:** Completed on 2026-07-15.

> **For agentic workers:** REQUIRED SUB-SKILL: use test-driven-development and verification-before-completion. This Harness root is not a Git repository, so do not create commits.

**Goal:** 增加不可变节点上下文、默认拒绝的工具权限裁决和 fixture-only executor，验证节点协议而不触碰真实业务环境。

**Architecture:** `app/node_runtime.py` 负责 envelope、权限裁决、fixture root 防护、候选契约和只读输出；`app/database.py` 增加独立上下文与执行审计表；`tools/task_manager.py` 只提供三个显式命令。普通 workflow 和 v0.51 scheduler 不导入 runtime。

## Constraints

- 只解析显式 fixture root 内的 JSON，不运行 shell、模型、源码工具、数据库或网络。
- fixture 结果不得登记为 current contract，不得改变 schedule/node 状态。
- envelope/checkpoint/fixture hash 任一不一致即阻断。
- 任一工具权限拒绝即阻断执行。
- 不改变 v0.50/v0.51 行为。

### Task 1: 失败测试和持久化模型

- [x] 新建 `tests/test_node_runtime.py`，覆盖不可变 envelope、节点状态、checkpoint 和 schema meta。
- [x] 先运行专项测试，确认因 runtime 尚不存在而失败。
- [x] 在 `app/database.py` 新增 context envelope、node execution 表和只读 CRUD。

### Task 2: 权限裁决和 fixture 边界

- [x] 测试允许 `read_artifacts`，拒绝角色未授权、角色禁止、全局禁止和 executor 不支持工具。
- [x] 测试 fixture root 标记、Git 仓库、路径逃逸、schema、context hash 和凭证字段阻断。
- [x] 实现 `app/node_runtime.py` 的 envelope 构建、hash 复核和 fixture-only executor。

### Task 3: 候选契约、幂等和 CLI

- [x] 测试成功执行只生成 `fixture_contract_candidate`，不生成 current contract、不改变 schedule 状态。
- [x] 测试相同 context/fixture 幂等，checkpoint 漂移后旧 context 拒绝执行。
- [x] 在 `tools/task_manager.py` 增加 prepare/execute/show 三个显式命令和只读输出。

### Task 4: 自检、文档和回归

- [x] 在 mock self-check 中加入 fixture runtime 成功、权限拒绝和重复运行检查。
- [x] 更新 README/HANDOFF，明确 v0.52 能力与非能力边界。
- [x] 运行 py_compile、专项测试、全量测试、mock self-check、workflow 隔离和敏感信息扫描。
- [x] 将本计划状态标记为完成，并记录实际验证数量。

## Verification Result

- 先确认 `app.node_runtime` 缺失导致专项测试失败，再完成实现。
- Node runtime 专项测试 13 项通过，且开启 `ResourceWarning` error 模式。
- 全量回归 168 项通过。
- 保留同一输出目录的 mock self-check 连续两次通过。
- 三个 CLI help、Python 编译、普通 workflow 隔离、敏感输出和尾随空白扫描全部通过。
