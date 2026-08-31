# HIS Harness v0.50 动态计划登记实施计划

状态：已完成（2026-07-15）

## 任务 1：增量 schema 与 CRUD

完成。

修改 `app/database.py`，新增动态计划、子任务、边、契约和审计表。新增 API 不改变旧 Task Manager CRUD。

## 任务 2：登记和契约生命周期

完成。

新增 `app/dynamic_plan_registry.py`：

- 读取并严格校验 v0.49 计划。
- 幂等登记父任务、计划、节点、边和 planned handoff。
- 记录契约新版本并校验 schema/producer/input。
- 对下游做可达图 stale 传播。
- 生成只读恢复预览和 JSON/Markdown 输出。

## 任务 3：Task Manager CLI

完成。

在 `tools/task_manager.py` 增加：

- `register-dynamic-plan`
- `show-dynamic-plan`
- `record-dynamic-contract`

这些命令只写本地 Task Manager 数据，不执行 DAG。

## 任务 4：自检和回归

完成。专项注册表测试、三个 CLI 端到端链路、可重复 self-check、Python 编译和全量 144 项测试均通过。

新增专项测试和 mock 自检，更新 README/HANDOFF。执行 py_compile、全量 unittest、mock self-check、CLI help、入口隔离和敏感信息检查。
