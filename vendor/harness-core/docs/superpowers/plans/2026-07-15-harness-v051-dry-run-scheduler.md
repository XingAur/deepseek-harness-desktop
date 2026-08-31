# HIS Harness v0.51 Dry-run Scheduler Implementation Plan

**Status:** Completed on 2026-07-15.

> **For agentic workers:** REQUIRED SUB-SKILL: use test-driven-development and verification-before-completion. This Harness root is not a Git repository, so do not create commits.

**Goal:** 在 v0.50 已登记 DAG 上增加可持久化、可恢复、明确不执行真实节点的 dry-run 调度控制面。

**Architecture:** `app/dynamic_scheduler.py` 负责状态机、预算裁决、checkpoint 和只读输出；`app/database.py` 只增加独立 schedule 表与 CRUD；`tools/task_manager.py` 提供三个显式命令。普通 workflow 不导入、不调用 scheduler。

**Tech Stack:** Python 3 标准库、SQLite、unittest、现有 Task Manager 和 dynamic plan registry。

## Global Constraints

- 所有运行状态和产物必须明确标记 `dry_run=true`、`execution_enabled=false`。
- 不调用模型、节点工具、worktree、PG、Git、云效/TAPD、发布或部署。
- 人工闸口只能暂停，v0.51 不提供批准命令。
- 重复 event ID 必须幂等；token、时间和重试预算不能自动放宽。
- 不改变普通 requirement workflow 和 v0.50 注册命令行为。

---

### Task 1: 调度持久化和初始状态

**Files:**
- Modify: `app/database.py`
- Create: `app/dynamic_scheduler.py`
- Create: `tests/test_dynamic_scheduler.py`

**Interfaces:**
- Produces: `DynamicDryRunScheduler.start(plan_id: int) -> dict`
- Produces: `DynamicDryRunScheduler.get_schedule(schedule_id: int) -> dict`

- [x] 写失败测试：schema version 已登记；blocked/needs_evidence plan 拒绝启动；simple plan 首节点进入 `running_simulated`；输出不包含真实执行标记。
- [x] 运行 `python3 -m unittest tests.test_dynamic_scheduler -v`，确认因模块或表不存在失败。
- [x] 新增 `harness_dynamic_schedules`、`harness_dynamic_node_states`、`harness_dynamic_schedule_events`、`harness_dynamic_checkpoints` 和解码 CRUD。
- [x] 实现 plan/role/contract 读取、初始节点状态、首批模拟分派和 schedule 快照。
- [x] 运行专项测试，确认 Task 1 通过。

### Task 2: 事件、预算、重试和人工闸口

**Files:**
- Modify: `app/dynamic_scheduler.py`
- Modify: `tests/test_dynamic_scheduler.py`

**Interfaces:**
- Produces: `DynamicDryRunScheduler.advance(schedule_id: int, event: dict | None = None) -> dict`

- [x] 写失败测试：success 解锁下游；并行实现节点一起分派；failure 进入 `retry_wait` 后由空 tick 重派；重试耗尽进入 `blocked_retry_exhausted`；timeout/token 超限进入 `blocked_budget`；重复 event ID 不增加 attempt；human gate 进入 `paused_human`。
- [x] 运行专项测试并确认每个新行为先失败。
- [x] 实现事件结构校验、running 节点约束、预算硬保护、attempt 计数、幂等事件和 schedule 终态计算。
- [x] 每次 start/event/tick 后写规范化 checkpoint 和 SHA-256，并在读取时验证 hash。
- [x] 运行专项测试，确认所有状态转换通过且没有真实契约或节点工具调用。

### Task 3: 显式 CLI 和只读产物

**Files:**
- Modify: `tools/task_manager.py`
- Modify: `app/dynamic_scheduler.py`
- Modify: `tests/test_dynamic_scheduler.py`

**Interfaces:**
- Produces: `write_dynamic_schedule_outputs(output_dir: Path, snapshot: dict) -> tuple[Path, Path, Path]`

- [x] 写失败 CLI 测试，覆盖 `start-dynamic-schedule`、`advance-dynamic-schedule`、`show-dynamic-schedule` 和三个产物文件。
- [x] 新增三个仅显式触发的 argparse 子命令；事件文件可选，未提供时执行 retry tick。
- [x] 输出 `dynamic_schedule.json`、`dynamic_schedule.md`、`dynamic_schedule_checkpoint.json`，Markdown 明示 simulated 不是业务完成。
- [x] 运行 CLI 专项测试和 `--help` 检查。

### Task 4: 自检、文档和回归

**Files:**
- Modify: `tools/self_check.py`
- Modify: `README.md`
- Modify: `HANDOFF.md`
- Modify: `docs/superpowers/plans/2026-07-15-harness-v051-dry-run-scheduler.md`

- [x] 在 mock self-check 中使用临时数据库跑 start、success、failure/retry、checkpoint hash 和重复运行检查。
- [x] 更新 README/HANDOFF，说明 v0.51 完成边界和下一阶段，不把模拟成功写成真实执行。
- [x] 运行 `python3 -m py_compile app/database.py app/dynamic_scheduler.py tools/task_manager.py tools/self_check.py`。
- [x] 运行 `python3 -W error::ResourceWarning -m unittest discover -s tests -v`。
- [x] 运行新的持久化目录 mock self-check 两次，确认可重复通过。
- [x] 扫描普通 workflow 入口、凭证字样、尾随空白和 CLI help；全部通过后将本计划状态标记为完成。

## Verification Result

- Scheduler 专项测试 11 项通过，且开启 `ResourceWarning` error 模式。
- 全量回归 155 项通过。
- mock self-check 在保留目录重复执行通过；最终复检状态为 `passed`。
- CLI help、Python 编译、普通 workflow 隔离、敏感信息输出和尾随空白扫描均通过。
