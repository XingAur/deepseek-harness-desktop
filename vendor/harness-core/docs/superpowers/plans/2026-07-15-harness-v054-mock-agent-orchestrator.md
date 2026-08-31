# HIS Harness v0.54 Deterministic Mock-Agent Orchestrator Implementation Plan

**Status:** Completed on 2026-07-15.

> **For agentic workers:** REQUIRED SUB-SKILL: use test-driven-development and verification-before-completion. This Harness root is not a Git repository, so do not create commits.

**Goal:** 让 fixture-only 动态团队按 DAG 多波次执行，并提供候选交接、trace/metrics 和并行观测证据。

**Architecture:** `app/mock_agent_runtime.py` 只编排现有 scheduler、context runtime 和 sandbox executor；数据库新增 run/trace 审计表；Task Manager 提供显式 run/show 命令。

## Constraints

- 不引入真实模型、业务工具、业务仓库、PG 或外部写入。
- 不绕过 context、checkpoint、lease、budget 和 candidate 校验。
- 同 wave 全部 worker 完成后才允许推进 schedule checkpoint。
- 所有成功结论必须明确标记 fixture-only、business-valid=false。

### Task 1: 失败测试与持久化

- [x] 新建 `tests/test_mock_agent_runtime.py`，先确认模块缺失失败。
- [x] 覆盖 run/trace 持久化、幂等和 schema meta。
- [x] 新增 mock-agent run/trace 表和 CRUD。

### Task 2: deterministic adapter 和候选交接

- [x] 根据 context envelope 生成稳定 fixture，不接受任意 prompt、command 或 env。
- [x] 复用 v0.53 lease/worker，完成多波次候选契约交接。
- [x] 保证 registry current contract 和业务有效状态不变。

### Task 3: 并行观测、失败隔离和 CLI

- [x] 同 wave 按上限并行执行并记录 observed concurrency。
- [x] 失败保留其他节点结果，不自动重试或补发 lease。
- [x] 增加 run/show CLI 和 JSON/Markdown/trace 产物。

### Task 4: 自检、文档和回归

- [x] mock self-check 覆盖完整 DAG、候选交接和并行观测。
- [x] 更新 README/HANDOFF 和项目边界。
- [x] 运行专项、全量测试、两次持久化 self-check 及 CLI/compile/isolation/sensitive scan。
- [x] 标记计划完成并记录验证数量。

## Verification

- `tests.test_mock_agent_runtime`: 9 tests passed with `ResourceWarning` treated as errors.
- v0.49-v0.54 dynamic execution chain: 70 tests passed.
- Full regression: 190 tests passed with `ResourceWarning` treated as errors.
- Mock self-check: passed twice against the same retained output directory after final edits.
- CLI, Python compile, workflow isolation, sensitive-data scan, static boundary and trailing-whitespace checks passed.
