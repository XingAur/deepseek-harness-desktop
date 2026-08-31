# HIS Harness v0.58 企业级核心稳定化设计

## 状态与边界

- 日期：2026-07-16
- 前置版本：v0.57.1
- 目标：先提高真实需求闭环的准确性、事务安全和可恢复性，再考虑 Web UI、团队分发、部署或真实模型。
- 冻结：真实模型、真实模型 DAG、业务 PG、Web UI 产品化、Git 远端、云效/TAPD 写入和部署。
- 允许：本地 fixture、mock/replay、只读证据、受控 worktree、专项测试和本地原仓库应用。

## 1. 真实模型冻结

所有公开运行入口在选择 `openai`、`anthropic`、`real`、`claude` 或 `zhipu` 时，必须在读取本地模型凭证前返回统一的 `real_model_runtime_frozen` 阻断。mock/replay 和纯本地 fake transport 测试继续可用。

v0.57 provider smoke 的真实传输入口同样冻结。内部单元测试可通过仅在 Python API 中提供的显式 test transport 开关验证协议层；CLI 不暴露该开关。

## 2. 需求变更归属闸口

每个核心闭环在进入 worktree 前生成 `change_ownership_matrix`：

- `frontend`：页面、组件、客户端请求和交互状态。
- `backend`：BFF、服务、API 参数和返回结构。
- `database`：表、SQL、存储过程、数据修复。
- `configuration`：菜单、路由、字典和运行参数。

每层状态只能是 `required`、`not_required`、`already_satisfied` 或 `unresolved`。评论和需求描述只能形成声明，不能单独证明 `already_satisfied`；源码、接口签名、用户明确确认或版本化本地证据才可解除未决状态。

涉及接口参数、排序、返回字段、BFF 或服务端的需求，必须同时解析客户端与服务端契约。任一相关层为 `unresolved` 时，核心闭环阻断，避免把前后端职责判断错误带入改码。

## 3. 原仓库事务应用

`apply_final_diff_to_project()` 使用确定性 `application_id` 和 Git 内部事务目录：

1. 记录 patch hash、目标路径、目标文件前置 hash、原始 git status 和无关脏文件。
2. 写入 `prepared` journal 后执行 `git apply --check` 和 `git apply`。
3. 应用后执行 `git diff --check` 并记录目标文件后置 hash。
4. 后置检查失败时执行 `git apply --reverse --check` 和受控 reverse apply。
5. 只有目标文件恢复到前置 hash 且无关脏文件保持不变，才标记 `rolled_back`。
6. 自动恢复失败时标记 `recovery_required`，保留 patch、journal 和恢复命令，不清理证据。
7. 相同 patch 重复调用时，根据已保存成功 journal 和当前目标 hash 返回 `already_applied`，不重复写文件。

事务证据保存在仓库 Git 元数据目录，不进入业务工作区状态，也不提交、不推送。

## 4. 验收

1. 真实模型公开入口在读取凭证和发起网络前阻断，mock self-check 不受影响。
2. 客户端参数需求缺少服务端源码证据时，变更归属矩阵阻断；服务端已由源码证明时可进入下一阶段。
3. 正常 patch 可应用并保留无关脏文件。
4. 后置 `git diff --check` 失败时目标文件自动恢复，无关脏文件不丢失。
5. 同一成功 patch 重复调用幂等返回，不产生第二次应用。
6. 专项测试、全量单测和 mock self-check 全部通过。

## 5. 后续阶段

v0.58 已纳入 P3：实际本地回滚、启动时 stale run 收敛、应用 journal 崩溃恢复和 failure injection。完成全量离线验证后，再建设真实 replay 样本集、数据库 migration/backup 和 CI。企业级核心验收完成前不恢复真实模型与 Web UI。
