# HIS Harness v0.53 Sandbox Executor Implementation Plan

**Status:** Completed on 2026-07-15.

> **For agentic workers:** REQUIRED SUB-SKILL: use test-driven-development and verification-before-completion. This Harness root is not a Git repository, so do not create commits.

**Goal:** 用固定 Python fixture worker、一次性 capability lease 和结构化协议验证真实节点调用边界。

**Architecture:** `app/executor_runtime.py` 管理 lease、adapter、协议和候选契约；`tools/fixture_node_worker.py` 是唯一可执行 worker；`app/database.py` 只增加 lease 表并复用 v0.52 execution 审计表；Task Manager 提供三个显式命令。

## Constraints

- 不接受用户提供 executable/command/env。
- 不继承父进程环境，不使用 shell，不访问真实业务仓库或网络。
- lease 一次消费、最长 300 秒；失败不自动补发。
- 结果只属于 sandbox fixture candidate，不影响 current contract 和 schedule。

### Task 1: 失败测试和 lease 持久化

- [x] 新建 `tests/test_executor_runtime.py`，先确认模块缺失失败。
- [x] 覆盖 context/权限绑定、TTL、幂等签发、一次消费和过期阻断。
- [x] 新增 capability lease 表、schema meta 和原子消费 CRUD。

### Task 2: 固定 worker 与失败隔离

- [x] 测试成功、worker failure、timeout、协议错误和预算超限。
- [x] 实现固定 worker JSON 协议、最小环境、`shell=False` 和输出验证。
- [x] 确保 stderr、绝对 fixture 路径和凭证内容不入库。

### Task 3: 候选契约、CLI 和幂等

- [x] 测试成功结果只生成 sandbox fixture candidate，不修改 registry/schedule。
- [x] 测试相同 lease/fixture 幂等，不同 fixture 不能复用已消费 lease。
- [x] 增加 issue/show/execute 三个显式 Task Manager 命令和只读产物。

### Task 4: 自检、文档和回归

- [x] mock self-check 覆盖 lease、固定 worker、失败隔离和重复运行。
- [x] 更新 README/HANDOFF 和项目边界。
- [x] 运行专项测试、全量测试、两次持久化 self-check、CLI/compile/isolation/sensitive scan。
- [x] 标记计划完成并记录验证数量。

## Verification

- `tests.test_executor_runtime`: 13 tests passed with `ResourceWarning` treated as errors.
- Full regression: 181 tests passed with `ResourceWarning` treated as errors.
- Mock self-check: passed twice against the same retained output directory.
- CLI, Python compile, workflow isolation, sensitive-data scan and trailing-whitespace checks passed.
