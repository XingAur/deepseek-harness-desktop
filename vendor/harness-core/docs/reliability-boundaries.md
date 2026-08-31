# Harness 可靠性边界

## 运行状态

所有入口都先生成 `1.0-runtime-preflight` 诊断。控制库默认使用配置路径；该路径不可写时，Harness 只把控制数据切换到私有临时目录，并保留原路径，不覆盖、不删除用户数据。该状态是 `degraded_readonly`，不能授权改码。

页面入口使用后台 Job：POST 只创建任务，页面通过 `/run-jobs/<job_id>` 和 `/api/run-jobs/<job_id>` 轮询阶段、错误和恢复动作。数据库中的运行产物仍是持久事实；服务重启时只收敛超时的 `running` 任务/运行，保留产物，不自动清理 worktree。

## 本地运行前置

`requirements.txt` 声明了凭证加密所需的 `cryptography`。若系统 Python 受 PEP 668 保护，使用项目隔离环境启动 Harness，不要用不安全的明文替代：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python harnesses/his_requirement_workflow.py --help
```

缺少该依赖时，页面和只读分析仍可启动，但凭证保存、Provider 真实调用和需要加密能力的测试会明确显示 `encryption_dependency_missing` 并保持阻断。

## 改码与 worktree

Worktree 目录使用 `run_<数据库 run id>_<随机 nonce>`，数据库 run id 在不同临时控制库中可能重复，因此不能单独作为目录名。已存在目录只返回 `blocked_worktree_collision`，不会自动删除。

改码合同必须包含允许路径和验证命令。空验证命令直接为 `not_run` 并拒绝进入 patch。原项目白名单外的脏文件可以保留，但白名单内有未提交改动会阻断。

## 验证状态

验证状态固定为：

- `passed`：实际命令成功且无副作用。
- `baseline_failed`：命令失败与基线相同，只说明没有证明是本次 patch 引入，不能视为通过。
- `not_run`：未执行或未提供命令。
- `tool_missing`：命令/工具不存在。
- `failed`：实际失败。
- `side_effect_failed`：验证命令修改了临时 worktree。

只有 `passed` 可以产生 `can_commit=true` 或允许合入原业务目录。

## 外部连接器

本地 Git 和 GitLab 适配器均保持只读/受控能力边界；GitLab 远程代码审查缺少专用 evidence orchestrator 时返回明确缺口，不降级成普通本地 diff。GitHub Issue、TAPD、Jira 等没有注册真实 adapter 时显示 `unsupported`，不会把通用 payload 当作已接入。

Provider 凭证加密依赖缺失时，页面和只读诊断仍可启动；凭证保存/读取会返回明确的 `encryption_dependency_missing`，不会用不安全的明文替代。

## 不能由本地 Harness 证明的事情

代码静态证据、接口结构、测试命令结果、数据库/配置可用性、真实运行时和真实医院业务验收必须分开记录。只读降级报告不等于代码已修改、接口已联调或业务已验证。
