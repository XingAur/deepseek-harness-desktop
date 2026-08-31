# Changelog

本文件记录 HIS Harness 企业核心能力的可分发版本。历史原型能力详见 `HANDOFF.md`。

## Unreleased

- Phase 1D 启用云效、GitLab、PostgreSQL 三个 L1 只读 MCP 描述符，并将 Manager 默认路由切换到持久 MCP Runtime；失败 fail closed，禁止静默 Provider/浏览器/直连/token 回退。
- 新增冻结的 GitLab GET-only MCP Server 与 PostgreSQL catalog-only MCP Server；数据库工具不接收 raw SQL，不提供 DML、DDL、删除、迁移或权限操作，读取无需 Harness 二次人工确认。
- MCP 子进程保留 Harness `.venv` 启动路径并复核解释器真实目标身份，避免符号链接解析后丢失 psycopg；旧工作流拒绝 Harness 侧数据库凭证文件注入。
- PostgreSQL MCP 增加不回显驱动错误的稳定失败分类，并修复无参数目录语句的绑定与只读事务起始顺序；当前配置目标的 schema-only smoke 仍返回 `DATABASE_CATALOG_QUERY_FAILED`，真实目录读取未验收通过。
- his-engineering 与 yunxiao 插件版本、MCP 入口、依赖和 SHA-256 已进入冻结清单；外部 I/O 门禁将三个原生连接边界标记为 `mcp_required`，旧直连 Provider 仅保留显式 `provider_rollback` 兼容隔离。
- Phase 1C 将 Provider 技术权限与 Harness 治理解耦：云效/GitLab/GitHub 只读由 personal token 决定，数据库只读由只读 endpoint/credential 决定；已注册只读动作无需第二次人工确认，但仍绑定 Profile、目标、请求人、参数、一次性计划和审计。
- 数据库修改/删除继续默认绝对禁止，且当前没有 DML/DDL/删除 action 或写 executor；策略已冻结未来精确范围授权与 delete/drop/truncate 破坏性范围授权，误标为 read 的数据库变更动作 fail closed。
- Phase 1C 的临时 Manager DB/fake adapter 验证记录保留为历史基线；Phase 1D 已完成描述符和默认路由切换，但真实系统连通性仍需对应只读凭证、地址和驱动支持。
- 新增外部 I/O 资产清单、稳定源指纹和 source-drift 架构门禁；现有直连 Provider 路径继续显式保留为兼容隔离债务。
- 新增严格 MCP capability/result 合同、三个默认禁用的只读初始描述符、fail-closed Gateway、测试证据/审计 sink 和 CapabilityService 兼容适配层。
- 新增哈希钉死、无 shell、单进程单调用且无自动重试的 stdio MCP transport；超时、取消、输出超限、协议漂移和非零退出均清理进程组并 fail closed。
- 新增独立 `mcp.sqlite` Evidence Store 与只追加 SHA-256 Audit Ledger，支持请求级幂等、冲突拒绝、并发写入、重启恢复和读取时篡改检测，不迁移主 `harness.sqlite`。
- 新增显式 Runtime Factory 和离线真实子进程验收；Phase 1D 在此基础上完成三个只读 Server 的默认路由激活和 Gateway 合同验收。
- 本增量不包含外部写入、Provider 移除、生产 capability 迁移或自动重试；Token Governor、ChangeContextPack 和 Supervisor 仍属后续阶段。
- 建立根目录 `VERSION` 作为当前版本唯一来源，发布 manifest 同时记录制品版本和 source version。
- 新增 `scripts/verify.sh`，统一使用 Harness `.venv` 运行 unit/offline/Manager 静态验证入口。
- 企业门禁语法阶段改为只读 AST 检查，并记录解释器、Python 版本、门禁版本和 timeout 失败原因。
- 当前完整 unit 门禁仍未通过；本次不宣称真实模型、远程交付、HIS 业务验收或发布就绪。
- 开始纳入本地修复复盘的受控离线规则、状态机、审计和人工纠正入口；规则只可向后续本地
  Worker/Reviewer 提供固定检查关注点，不能执行命令、扩大合同或授权任何外部写入。
- 本次新增临时 Git fixture + fake Worker/Reviewer 的离线集成门禁，覆盖验证失败后的 retry
  注入、跨任务/工作区晋升、反例暂停、高风险 trial 和人工纠正阻断确认应用。
- 不宣称 v0.71，也不将离线实现或测试当作 GitLab/云效交付或自动多轮修复验收。2026-08-14
  的一次真实 bundled Codex 无 remote 临时 fixture 已通过，但它只证明单次本机闭环，不代表
  连续稳定改码或业务交付；当前验收状态以 README 的唯一状态口径为准。

## 0.65.0 - 2026-07-17

- 增加原源码 Git Delivery Closure：不可变计划、release/RC 两段运行时验收、两次确认和完整事件审计。
- 增加 Harness Safety Shelf，支持混合但可分离的 staged、unstaged 和未跟踪文件精确恢复。
- 按 Rule Pack 创建需求/缺陷分支和精确 commit；任务分支 push、RC cherry-pick 和 RC push 均由计划与确认双重控制。
- 增加 RC 远端同步、重复等价变更识别、冲突 abort、提交增量 parity 审计和远端漂移保护。
- 等价提交除 patch-id 外继续校验 RC 当前最终文件状态；多提交只存在部分等价变更时硬阻断，避免虚假放行或重复 cherry-pick。
- 交付事务键绑定策略、动作、验证命令、路径和 patch；两次确认作为计划绑定事件持久化，重复调用保持幂等。
- RC 集成在数据库状态更新前先写可校验 journal，支持中断后协调；验证失败同步记录恢复状态，避免数据库与仓库检查点分裂。
- Task Manager 只读工作台接入交付事务，并同步关联任务的当前交付阶段。
- 专项验证命令进入不可变计划并完整展示，拦截伪装成验证的 Git 远端写入、发布和常见上传动作。
- 修复白名单相对路径规范化，`../` 和绝对路径不会被错误清洗成仓库内路径。
- 企业门禁明确使用本地临时仓库和 bare remote fixture，不访问或写入真实 Git 远端。

## 0.64.0 - 2026-07-16

- 将 `paiBanMs`、查询排序和页签状态等既往样本识别迁移到无票据编号的版本化 contract plugin pack。
- 需求确认卡记录命中的 pack/plugin 版本，显式 `harness-rules` 继续优先于推断规则。
- 为 patch、fullstack、precommit 和 review worktree 增加旁路生命周期标记及启动只读恢复检查。
- worktree 清理改为默认预览、项目白名单、超时、Git 登记、干净状态和精确确认码；禁止覆盖同名目录。
- review 模式完成后清理 base/head worktree 和生命周期标记。
- 个人本地企业级核心验收通过：最终离线门禁连续 20/20 轮通过，每轮 272 个单元测试和 10 条真实需求 replay；全程未调用真实模型、网络或持久化数据库。

## 0.63.0 - 2026-07-16

- 冻结真实模型和真实模型 DAG，阻断发生在读取凭证或网络调用之前。
- 增加前端、后端、数据库、配置四层变更归属矩阵，评论不再单独作为代码完成证据。
- 增加本地原仓库事务应用、崩溃恢复、幂等、目标漂移保护和实际回滚。
- 增加 SQLite schema/migration、并发连接策略、健康检查、备份、恢复和保留策略治理。
- 增加 10 条脱敏真实需求 replay 和统一离线企业门禁。
- 增加可复现、密钥扫描通过、排除持久化数据和个人配置的本地发布包。

## 0.57.1 - 2026-07-15

- 收敛 provider smoke 的 transport、protocol 和 marker 三层语义。
- 真实 provider 调用从企业核心验收流程中冻结，历史记录仅作为审计事实保留。
