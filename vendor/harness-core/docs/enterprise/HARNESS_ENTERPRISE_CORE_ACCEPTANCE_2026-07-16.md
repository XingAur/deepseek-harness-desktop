# HIS Harness 个人本地企业级核心验收报告

- 验收日期：2026-07-16
- 验收版本：v0.64.0
- 验收状态：通过
- 适用范围：个人本地、离线、可审计的需求到 patch 工程核心

## 1. 结论

HIS Harness 已满足 2026-07-16 企业级核心审计定义的 8 项 Definition of Done，可认定为“个人本地企业级核心完成”。

本结论只表示本地工程链技术验收通过：`technical_valid=true`。它不表示任意 HIS 需求已自动获得业务验收，最终状态仍明确保持：

- `business_valid=false`
- `runtime_verified=false`
- `promotion_enabled=false`

本次验收未调用真实模型，未读取模型或云效凭证，未访问网络，未查询业务 PG，未使用持久化 Harness 数据库，未修改业务仓库，也未执行 Git 提交、推送、部署或云效/TAPD 写入。

## 2. Definition of Done

| 序号 | 验收要求 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 连续 20 次离线完整回归零偶发失败 | 通过 | 20/20 轮通过，每轮执行 272 个单元测试、mock self-check、10 条 replay、编译和密钥扫描 |
| 2 | 10 个以上真实 replay 样本及负例全部命中 | 通过 | 10 条脱敏真实需求场景连续 20 轮结果一致，replay hash 为 `5b600ee2e84ce296004603a9943f3e5bf65bf8df09f519d1542db762baf9f66b` |
| 3 | 本地应用覆盖成功、失败、中断、幂等和无关脏文件 | 通过 | `tests/test_worktree_executor.py` 覆盖成功应用、后置失败反向恢复、journal 中断恢复、重复应用和无关改动保护 |
| 4 | 实际回滚、回滚后验证和失败恢复 | 通过 | `tests/test_task_manager_rollback.py` 覆盖精确确认、实际回滚、幂等、目标漂移阻断和验证失败恢复 |
| 5 | migration、备份、恢复、stale-run 和并发策略 | 通过 | `tests/test_database_governance.py`、`tests/test_run_recovery.py`、`tests/test_retention_governance.py` 覆盖版本迁移、失败恢复、SHA-256 备份恢复、WAL/外键/busy timeout、过期运行收敛和保留策略 |
| 6 | 技术、业务、运行时结论不混淆 | 通过 | 企业门禁产物固定分别输出 `technical_valid`、`business_valid`、`runtime_verified` 和 `promotion_enabled` |
| 7 | 自动门禁失败时不可标记可交付 | 通过 | 本地 `tools/enterprise_gate.py` 与 `.github/workflows/enterprise-core.yml` 使用同一固定门禁；本轮 `promotion_enabled=false` |
| 8 | 不依赖付费模型即可复跑和审计 | 通过 | 20 轮均为隔离本地 fixture/mock/replay；`external_calls=false`、`real_model_runtime_used=false` |

## 3. v0.64 收口项

1. 历史需求识别规则已从核心校准器迁移到 `config/contract_plugins/dfhis.common.v1.json`。规则包不包含 DFHIS 票据编号，确认卡会记录命中的 pack/plugin 版本，用户显式 `harness-rules` 仍具有更高优先级。
2. patch、fullstack、precommit 和 review worktree 使用工作树外的生命周期标记。启动阶段只读识别近期、过期、脏、未登记、未归属和孤立标记状态。
3. worktree 清理默认仅生成计划；只有项目白名单、Harness 归属、超过时限、Git 已登记、工作区干净且输入精确 `CLEANUP:<plan_hash>` 时才执行。
4. 创建阶段禁止覆盖同名目录；review 正常结束会同时清理 base/head worktree 和对应标记。

## 4. 最终稳定性证据

- 门禁输出：`/tmp/his_harness_v064_stability.A1HLaO`
- 门禁状态：`passed`
- 完成轮数：20/20
- 单元测试：每轮 272 个，全部通过
- replay：每轮 10 条，结果确定性一致
- 最终 result hash：`e8b496b01a2cdc38110de270758c0a20957b8f04adab09adddc7d974e4721f11`
- 外部调用：无
- 持久化数据库：未使用
- 真实模型运行时：未使用

## 5. 保留边界

以下内容不属于本次“个人本地企业级核心”完成结论：

- 真实 HIS 页面、接口数据、登录态和用户操作仍需要专项或人工业务验收。
- 当前专用可执行验收 fixture 以树/列表排序关系为代表；默认值、参数、页签状态等常见规则已进入版本化 contract plugin 和 replay。新增业务类型应扩展规则包或契约，不应重新在核心代码中硬编码票据特例。
- 本地 CI 配置和门禁已完成；由于 Harness 根目录当前不是 Git 仓库，本轮没有远端托管 CI 的实际运行记录。
- Web UI、团队分发、服务器部署、真实模型、业务 PG 自动查询以及外部写入仍冻结。业务 PG 只在用户明确要求查询时启用只读适配器。
- Harness 不会自动创建分支、提交、推送、合并 RC、部署或写云效/TAPD。

## 6. 后续阶段

企业核心完成后，可以进入只读 Web UI 产品化阶段，把已经稳定的任务、运行、证据、回滚和配置能力做成个人可用界面。真实模型不因本次验收自动解冻，后续是否接入应作为独立阶段重新评估成本、权限和收益。
