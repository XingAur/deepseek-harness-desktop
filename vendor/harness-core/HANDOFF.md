# HIS Harness 交接说明

最后更新：2026-08-30

本文用于在新 Codex 聊天窗口中恢复 HIS Harness 的上下文。新窗口应先读取本文和 `README.md`，再继续工作。

## 2026-08-30 当前基线

当前代码版本唯一来源是根目录 `VERSION`，当前值为 `0.66.0`。新增的
`scripts/verify.sh` 统一使用 Harness `.venv`；企业门禁的语法检查已改为只读 AST 检查，避免生成 `.pyc` 造成权限误报。`compile`、`replay`、`secret` 短门禁通过；完整 `unit` 阶段仍超时/失败，因此当前只保留离线技术证据，`business_valid=false`、`runtime_verified=false`、`promotion_enabled=false`。

本阶段未调用模型、网络、云效、Git 远端、业务数据库或部署，也未重置、删除或迁移 `data/` 下现有数据。

## 新窗口启动提示

建议在新聊天开头直接发送：

```text
使用 $his-harness。
先读取 /Users/lym/WorkCode/ai/Harness/HANDOFF.md 和 README.md。
继续当前 HIS Harness 工作。
云效评论、状态流转、负责人、迭代、关闭任务、发布先不开放。
v0.10.3A 显式方法级测试命令执行器已完成。
v0.10.3B 显式 UI 证据采集命令执行器已完成。
v0.10.3C Playwright/Chrome UI 采集模板和 HIS 登录态策略已完成。
v0.10.4 真实 DFHIS 单需求提交前样板已完成。
v0.10.5 Task Manager 真实样板登记与复跑模板已完成。
v0.10.6 Task Manager precommit 复跑入口已完成。
v0.10.7 Task Manager 登记幂等和 run 历史可比已完成。
v0.10.8 UI 证据复用策略记录已完成。
v0.10.9 Task Manager 只读看板导出已完成。
v0.10.10 Task Manager 看板筛选和真实样板集导出已完成。
v0.11 Task Manager 本地任务工作台已完成。
v0.12 Task Manager 只读本地 HTML 工作台入口已完成。
v0.13 Task Manager 历史 run 对比和过期证据提示已完成。
v0.13.1 precommit 大 diff 原文保留修复已完成，避免日志截断文本污染 git apply --check。
v0.14 只读 HTML 工作台 warning 汇总、筛选和搜索可读性已完成。
v0.15 需求理解确认卡已完成，用户补充规则、菜单/路由参数和值域会先于技术自治进入报告。
v0.16 Task Manager 确认卡索引已完成，workbench/workspace 能展示、复制和搜索 `requirement_calibration.json/md`。
v0.17A Task Manager 修改历史账本和回滚 dry-run 计划已完成，能登记 diff、显示修改次数、生成只读回滚计划。
v0.17B Task Manager 只读 WebUI 详情入口已完成，workspace 能展示任务详情 tabs、run 历史、确认卡、修改历史、证据预览和可复制命令。
v0.18 Task Manager 只读 WebUI 快照对比和导出索引已完成，workspace 能比较上一版 `task_workspace.json` 和当前导出，并列出所有导出产物。
v0.19 Task Manager 只读 WebUI 多快照浏览和证据趋势已完成，workspace 会归档历史快照、支持任意两快照摘要对比，并展示 UI 证据、warning、验证、确认卡和修改次数趋势。
v0.20 Task Manager 只读 WebUI 导航详情已完成，workspace 新增导航结构、历史快照详情和可展开证据预览。
v0.21 Task Manager 只读 WebUI 离线审查版已完成，workspace 新增空态/错误态说明、状态标签、大表滚动容器和离线审查包。
v0.22 配置中心与规则包骨架已完成，Rule Pack、Profile、Credential Store 可生成只读配置摘要，workspace 可显式展示配置中心页，默认旧命令行为不变。
v0.23 需求来源 provider 只读归一化已完成，Yunxiao/TAPD/manual/file 本地 payload 可统一导出 `requirement_evidence.json/md`，默认旧 workflow 和云效读取命令不变。
v0.24 需求来源证据显式接入已完成，`--requirement-evidence-file` 可把本地 `requirement_evidence.json/md` 接入主 workflow、Task Manager 和只读 WebUI；默认旧 workflow、demo 和 workspace 行为不变。
v0.25 配置预览和 Provider 模板草案已完成，`--include-preview` / `--include-config-preview` 可显式导出只读规则预览、provider 模板和团队分享草案；默认旧命令行为不变。
v0.26 配置分享校验和本地覆盖策略已完成，`--include-share-validation` / `--include-config-share-validation` 可显式导出只读分享包校验和本地覆盖策略；默认旧命令行为不变。
v0.27 配置导入草案和示例文件生成已完成，`--include-import-draft` / `--include-config-import-draft` 可显式生成 secret-free 草案文件到用户选择目录；默认旧命令行为不变。
v0.28 配置导入草案回读校验和只读表单预览已完成，`--review-import-draft` / `--include-config-import-review` 可回读用户选择目录中的草案文件并展示导入前风险提示；默认旧命令行为不变。
v0.29 配置差异对比、多 Profile 切换预览和团队模板索引已完成，`--include-template-index` / `--include-config-template-index` 可索引一个或两个草案目录并展示 profile、规则和模板文件差异；默认旧命令行为不变。
v0.30 只读配置向导页面已完成，`--include-config-wizard` 可把配置摘要、Provider 模板、分享校验、生成草案、回读校验、模板索引和人工复制前确认汇成一个只读向导；默认旧命令行为不变。
v0.31 配置向导可读性增强已完成，`--include-config-wizard` 新增步骤筛选、阻断摘要、命令复制目标和空结果提示；默认旧命令行为不变。
v0.32 配置审查包索引已完成，`--include-config-wizard` 的 workspace 会生成 `task_workspace_config_review_package.json/md` 并在 HTML 中展示配置产物、复跑命令和人工确认项；默认旧命令行为不变。
v0.33 配置审查包可读性增强已完成，`--include-config-wizard` 的 workspace 新增审查包文件筛选、待确认分组和交接摘要；默认旧命令行为不变。
v0.34 只读分层配置解析器已完成，现有 Rule Pack/Profile/Experts 可通过兼容适配器生成带来源追踪和硬保护裁决的不可变 ResolvedConfig；默认旧命令行为不变。
v0.35 核心需求闭环已完成：新增 `core-closure-trial`，把需求校准、可验证规则/默认行为、工程证据、白名单、专项验证、受控 worktree、独立 diff 审查串成低风险基础需求的最小可交付链路；v0.37 起在全部本地闸门通过后默认回写原业务目录。
v0.36 真实低风险需求回放已完成：DFHIS-31557 在真实 `df-web-bui` 源码基线上完成“云效只读证据 -> 结构化规则 -> 工程定位 -> 受控 worktree patch -> 专项验证 -> 独立 diff 审查”的回放；原业务目录可在独立审查通过后本地应用。
v0.37 本地优先自动应用已完成：`core-closure-trial` 默认在 worktree、专项验证和独立 diff 审查均通过后自动应用至本地原业务目录；传 `--review-only` 才保留仅审查行为。不会创建分支、提交、推送、合并 RC、发布或写云效/TAPD。配置真实写入、团队分发、UI 产品化、部署、远端账号测试、云效/TAPD 写动作、自动回滚和远端写入继续冻结。
v0.38 自动本地路线已完成：`auto-local` 直接复用核心闭环，自动采用可靠的项目定位、白名单和专项验证推导；低风险且全部本地闸门通过时自动应用原业务目录。证据不足、规则不清或高风险时保留结构化阻断证据，不走固定九步骤报告链。运行会记录请求模式与实际路线；不会执行远端 Git、发布或云效/TAPD 写操作。
v0.39 前后端契约核验门禁已完成：云效正文、评论、附件和用户补充规则属于需求证据；涉及入参、接口、排序、返回字段或服务端/BFF/API 的需求，必须同时命中客户端请求和服务端源码证据才能自动改码，缺任一层即阻断。纯样式、纯客户端默认值等局部需求不要求服务端契约。
v0.40 真实排序需求回放已完成：DFHIS-31551 用于修正契约核验精度。云效评论中的明确请求参数会进入需求校准；客户端请求和服务端签名必须在同一局部上下文内同时包含接口名及全部参数，避免同文件的无关 `sortOrder` 配置或无关后端字段造成误放行。当前回放正确判定客户端缺独立 `sortOrder`、服务端缺排序参数，未进入 worktree。
v0.41 显式参数优先级已完成：`harness-rules` 中声明的 `request_param` 会覆盖云效正文/评论的冲突参数，技术契约仅核验该结构化参数集。DFHIS-31551 当前按单参数 `sortField` 回放，编码为 `字段A|排序方式,字段B|排序方式`；旧评论中的 `sortOrder` 不再被误当成当前契约。
v0.42 人工运行时验收登记已完成：当前本地源码未包含其他同事后端改动、但用户已在真实环境通过验证时，可将验收记录绑定至原 task run/run，生成独立本地证据；原源码门禁不被改写，自动应用、提交、远端 Git 和云效/TAPD 写入均保持关闭。
v0.43 小需求快速路径已完成：`auto-local` 仅在调用方显式提供一个本地项目、1 至 3 个存在的前端白名单文件，且未命中接口/入参/排序/服务端/API 或医保收费结算等高风险词时跳过全仓工程上下文扫描。需求校准、专项验证、受控 worktree、独立 diff 审查和本地应用门禁仍完整执行；任何不满足项自动回退完整核心闭环。
v0.44 快速路径可观测性已完成：每次 `auto-local` 输出 `auto_local_performance_json`，记录各准备阶段和核心闭环的实际耗时、工程扫描是否跳过、快车道判定与回退原因。该产物为只读本地证据，不改变既有安全闸门或外部写入边界。
v0.44.1 临时存储模式已完成：日常工作流与 self-check 默认使用进程临时 SQLite 和输出目录，命令结束即删除运行记录、报告和 fixture；只有显式 `--retain-output` 才启用持久化，供未来 Task Manager/WebUI 追溯。
v0.45 真实快路径闭环已完成：DFHIS-31528 在真实 `df-web-guahaosf` 源码基线上完成“云效只读证据 -> auto-local 快路径 -> 受控 worktree -> 专项验证/历史基线 warning -> 独立 diff 审查 -> 自动本地应用 -> 用户页面验收 -> Task Manager 验收登记”。`register-run` 会复用输出目录 `run.json` 中、且存在于当前本地数据库的原始 `run.id`；显式 `--source-run-id` 与输出不一致、或输出不含原始 ID 时均拒绝关联，避免人工验收挂错任务。分支、提交、推送、合并 RC、部署和云效/TAPD 写入仍关闭。
v0.46 核心闭环交接口径已完成：最终报告将 `ready_for_manual_review` 明确写为“本地验证已通过，可进入人工代码审查与业务验收；未自动提交或发布”，不再沿用普通专家流的“不可进入开发”提示；报告版本同步为当前核心闭环版本。历史真实样本 DFHIS-31528 的报告已重写验证。
v0.47 可执行验收契约已完成：对“排序 + 方案树/树形 + 右侧排班/列表一致性”需求，`core-closure-trial`/`auto-local` 可读取显式本地 `--acceptance-contract-file`，在创建 worktree 前执行脱敏 `ordering_relation` fixture；同号稳定排序、父节点最早子孙排序、无序号保持原序和树叶子/排班序列一致性任一不成立都会阻断。fixture 通过后，其 `verify_command` 会并入 worktree 专项验证，独立 diff 审查还要求契约声明的实现证据；结果写入 `acceptance_contract_result_json/markdown`。DFHIS-31558 仅作为脱敏 fixture，不会再修改业务仓库或替代用户页面验收。
v0.48 PostgreSQL 数据证据适配器已完成：新增独立 `tools/pg_evidence.py`，普通需求流程保持零数据库连接；`plan` 模式不创建驱动，只有用户明确要求数据库验证并使用 `execute` 时才尝试已登记的测试/开发只读 Profile。Profile 从 `pg_<profile>_readonly_*` key 自动发现；SQL 只允许参数化单条 `SELECT`/只读 CTE，候选不唯一时不盲查；连接、查询、总耗时、元数据次数和结果行数均受限，失败不重试，产物统一脱敏。开发和 self-check 仅使用 fake executor，尚未执行真实 PG smoke。
v0.49 动态团队只读规划已完成：新增独立 `tools/dynamic_plan.py --enable`，用八维确定性评分生成 simple/medium/large/high_risk 团队、按技术层拆分的子任务 DAG、路径冲突串行边界和版本化交接契约。开发与审查角色强制隔离；缺少实现路径时返回 `needs_evidence`，高风险需求返回 `needs_human_confirmation`。默认旧 workflow 不调用该模块；本阶段不调用模型、不改业务代码、不连数据库、不执行 Git 或外部系统写入。
v0.50 动态计划 Task Manager 登记已完成：新增独立计划、子任务、DAG 边、契约版本、审计和 schema meta 增量表，以及 `register-dynamic-plan`、`show-dynamic-plan`、`record-dynamic-contract` 三个显式命令。同计划哈希幂等；上游契约更新只把可达下游标记 stale；恢复预览只展示 completed/ready/stale/blocked/human gate。非法循环依赖和凭证字段拒绝入库。普通 workflow 不调用该能力，不执行 DAG、模型、worktree、业务代码、PG 查询或外部写入。
v0.51 动态调度 dry-run 控制面已完成：新增 schedule、独立节点状态、模拟事件和 checkpoint 表，以及 `start-dynamic-schedule`、`advance-dynamic-schedule`、`show-dynamic-schedule` 三个显式命令。DAG 前置、并行分支、角色 token/时间/重试预算、重复事件幂等、v0.50 stale 契约和人工闸口都能持久化模拟；checkpoint 使用 SHA-256 校验。所有 running/succeeded 状态都明确为 simulated，不调用模型、节点工具、worktree、PG、Git 或外部系统。
v0.52 受控节点 fixture runtime 已完成：新增不可变 context envelope、角色工具权限裁决、fixture root/Git/路径逃逸防护、候选契约和执行审计表，以及 `prepare-dynamic-node-context`、`execute-fixture-node`、`show-fixture-node-execution` 三个显式命令。成功结果固定为 `fixture_contract_candidate`、`business_valid=false`、`promotion_enabled=false`，不写 v0.50 current 契约、不改变 v0.51 schedule 状态。checkpoint/envelope 漂移、权限升级、凭证字段和非法 fixture 均硬阻断；不调用真实模型、shell、worktree、PG、Git 或外部系统。
v0.53 sandbox executor 已完成：新增一次性短期 capability lease、固定 `tools/fixture_node_worker.py` 进程 adapter、结构化 worker 协议、超时/失败/协议/预算隔离和执行审计，以及 `issue-fixture-capability-lease`、`show-fixture-capability-lease`、`execute-sandbox-fixture-node` 三个显式命令。worker 使用 `shell=False`、固定路径、fixture cwd 和最小环境，不接受任意命令、模块、worker path 或 env。成功只生成 `sandbox_fixture_contract_candidate`，不修改 current 契约和 schedule；真实模型、业务工具、worktree、PG、Git 和外部系统仍关闭。
v0.54 deterministic mock-agent 编排已完成：新增 `harness_mock_agent_runs`/`harness_mock_agent_traces` 审计表、`app/mock_agent_runtime.py`、`run-mock-agent-fixture-schedule` 和 `show-mock-agent-fixture-run`。编排器复用 v0.52 context 和 v0.53 一次性 lease，按 schedule wave 执行固定 worker；同 wave 可受限并行，全部执行结束后才推进 checkpoint。后续节点可读取上游 fixture 候选引用，trace 持久化 context/lease/execution/usage/耗时/候选 hash/并发观测。失败保留同 wave 其他证据且不自动重试；所有结果仍为 fixture-only、business-valid=false，不晋升 current contract，不接真实模型、HIS 源码、worktree、PG、Git 或外部系统。
v0.55 provider-neutral 离线模型调用已完成：新增 `harness_model_invocations`/`harness_model_invocation_events` 审计表、`app/model_invocation_runtime.py`、`run-model-fixture-node` 和 `show-model-fixture-invocation`。单节点请求绑定 v0.52 context/checkpoint/角色/上游 artifact/输出契约/预算；只允许 deterministic `mock` 录制和带 request hash 的 `replay`，并校验 provider-neutral 响应、结构化输出、usage、evidence refs、凭证字段与候选 hash。结果固定为 fixture-only、business-valid=false，不推进 schedule、不晋升 current contract；入口不调用旧 `get_llm_client()`、不读取 credentials、不访问网络、HIS 源码、worktree、PG、Git 或外部系统。
v0.56 多波次离线模型 DAG 已完成：新增 `harness_model_dag_runs`/`harness_model_dag_traces` 审计表、`app/model_dag_runtime.py`、`run-model-fixture-schedule` 和 `show-model-fixture-schedule-run`。编排器按节点 adapter policy 调用 v0.55 `mock/replay`，同 wave 受限并行且整波完成后才推进 simulated checkpoint；下游 context 只接收同一 schedule 的成功 `fixture_model_candidate` 引用，避免跨历史运行串候选。失败保留 trace 且不自动重试，所有结果仍为 fixture-only、business-valid=false，不晋升 current contract，不读取凭证或访问网络、HIS 源码、worktree、PG、Git 或外部系统。
v0.57 受控真实 provider 单节点 smoke 已完成实现与离线测试：新增无密钥 Profile、凭证/网络双开关、用户授权哈希、HTTPS endpoint 主机白名单、固定提示、短超时、零重试、幂等防重复调用和脱敏 SQLite/文件审计。该入口与 v0.56 DAG、正常 HIS 核心闭环和旧 LLM 客户端隔离，固定 single-node-only、business-valid=false；真实请求只在用户明确授权后执行一次，结果不代表真实多智能体或业务闭环完成。
v0.57 已在 2026-07-15 使用用户明确授权执行一次真实 DeepSeek OpenAI-compatible smoke：网络、凭证、模型和响应结构解析成功并返回 token usage，但响应正文未精确命中固定 `SMOKE_OK`，严格状态为 `failed_protocol/smoke_response_mismatch`。SQLite 只有 1 条 smoke 和 1 个 network event，未重试、未保存响应原文，API key/Authorization 泄漏扫描为 0。该结果证明 provider 连通链可用，不得标记为严格 marker 验收通过；再次真实调用必须取得新的逐次授权。
v0.57.1 已完成离线语义收敛：smoke 审计分别记录 transport、OpenAI-compatible protocol shape 和 fixed marker 三层状态，旧 v0.57 记录可兼容推导；CLI 只有三层全部通过才返回 0。由于第一次真实 usage 的输出 token 恰好达到原 16 上限，后续无密钥 Profile 已把固定 smoke 输出上限调为 64 并强化 exact marker 指令，但没有发出第二次网络请求。
v0.57.1 最终本地验证：provider 专项测试 9/9、全量单测 217/217、同一持久化 SQLite 连续两轮完整 mock self-check 138/138；Python 编译、旧真实记录迁移兼容和凭证泄漏扫描通过。真实审计仍保持原始事实：transport=passed、protocol=passed、marker=failed，只有一次网络调用。
v0.58 企业级核心稳定化已完成离线验收：真实模型和真实模型 DAG 已在读取凭证前硬冻结，公开入口默认 mock；核心闭环新增前端/后端/数据库/配置四层变更归属矩阵，评论不能单独证明服务端完成；本地原仓库应用改为 journal 事务并支持崩溃恢复和幂等；Task Manager 新记录支持精确确认的本地事务回滚；超过 24 小时的 running 运行会在启动时收敛为 interrupted 并保留审计。最终隔离验证为 Python 全量编译通过、237/237 单测通过、209 项 mock self-check 全通过且 `business_valid=false`、敏感值扫描无命中。期间自检发现并修复“补丁成功应用后精确恢复到事务前状态仍无法重应用”的 journal 状态问题；现在会先归档旧 journal 再创建新事务，任何非精确恢复仍阻断。Web UI、业务 PG、远端 Git、部署和外部系统写入继续冻结。
v0.59 持久化治理已完成：SQLite 连接启用 foreign keys、WAL 和 busy timeout；增加单调 schema version、migration ledger、迁移前备份、健康检查、SHA-256 备份和精确确认恢复。所有验证只使用 `/tmp` 隔离数据库，未读取或修改现有 `data/harness.sqlite`。
v0.60 脱敏真实需求回放已完成：固定 10 个场景、至少 8 个真实 DFHIS 编号，覆盖前端、后端、前后端联动、排序和高风险阻断；每例都有来源、变更归属、允许路径、预期 diff、专项命令、负例和人工验收边界。回放只验证离线技术判断，固定 `business_valid=false`、`runtime_verified=false`。
v0.61 数据保留治理已完成：支持最近 N 天与最近 N 次并集保留，保护 Task Manager/修改历史/云效审计/running/异常时间记录；默认 preview，精确 `PRUNE:<plan_hash>` 后才先备份、再事务删除和 compact，可通过 v0.59 restore 恢复。不会自动清理归档。
v0.62 统一离线企业门禁已完成：固定执行 compile、全量 unittest、mock self-check、10 场景 replay 和高置信 secret scan；每轮使用隔离数据库和脱敏环境，不调用模型、网络、业务 PG、云效或 Git 远端。CI 配置已加入，但 Harness 根目录当前不是 Git 仓库，远端 CI 是否实际运行仍取决于未来托管。
v0.63 可复现本地发布包已完成：显式白名单只包含源码、测试、fixture、示例配置、文档和 CI；排除 `data/`、个人配置、运行记录、快照、临时 worktree 和缓存；同源码同版本两次归档字节及 SHA-256 一致。实际回滚同时支持显式专项验证，验证失败会恢复回滚前修改并保留恢复 journal/patch。
v0.64 版本化需求契约插件与 worktree 恢复已完成：`paiBanMs`、查询排序、页签状态保持等既往样本规则已迁移到无票据编号的 `config/contract_plugins` 数据包，确认卡记录 pack/plugin 版本，显式 `harness-rules` 继续优先。patch/fullstack/precommit/review worktree 使用旁路生命周期标记；启动只读识别近期、超时、脏、未登记和孤立标记。清理默认 preview，只有 Harness 归属、项目白名单、超过 24 小时、Git 登记且干净并提供精确 `CLEANUP:<plan_hash>` 时才执行；创建阶段不再覆盖同名目录，review 完成会清理 base/head 及标记。
个人本地企业级核心已于 2026-07-16 按审计 Definition of Done 通过验收：最终离线企业门禁连续 20/20 轮通过，每轮 272 个单元测试和 10 条真实需求 replay；`technical_valid=true`，`business_valid=false`、`runtime_verified=false`、`promotion_enabled=false`。验收报告见 `docs/enterprise/HARNESS_ENTERPRISE_CORE_ACCEPTANCE_2026-07-16.md`。全过程未调用真实模型、网络、业务 PG、持久化 Harness 数据库或业务仓库。
HarnessManager v0.65 核心兼容工作台已开始落地：独立目录 `/Users/lym/WorkCode/ai/HarnessManager` 继续作为 Web/API 层，直接复用核心 `TaskManager`；新增系统真实性状态和修改历史，页面移除模型配置，API 在创建 Job 前硬阻断真实模型模式。Manager 不复制核心规则、不读取模型凭证、不开放实际回滚、远端 Git、云效/TAPD 写入或部署。
```

## 当前定位

HIS Harness 不是单个提示词脚本，而是 HIS AI DevOps 控制层：

- 读取云效需求或手工需求。
- 分析项目、文件、字段来源和影响范围。
- 生成验收矩阵、技术方案、改动计划、审查包。
- 在受控 worktree 中试错和验证。
- 验证通过后可合入本地原业务目录。
- 提交、推送、云效评论、云效状态流转等动作按权限分层逐步开放。

当前成熟边界：本地开发闭环和提交前验证可以继续打磨；云效真实状态流转、负责人、迭代、关闭、发布仍冻结。

## 已完成能力概览

- v0.5：真实需求输入、专家团报告、Evaluator 审核、失败返工、最终报告。
- v0.6：只读项目扫描、工程证据包、风险分级、专家报告引用证据。
- v0.7：受控 worktree 改码、白名单 patch、验证命令、review-worktree、历史基线对比、验证副作用检测。
- v0.8：云效只读证据、凭证文件、HTML 清洗、内联图片/附件摘要、云效事务 dry-run/fake/受控写入代码层。
- v0.8.7：需求验收矩阵、反驳/纠偏闸口、项目验证基座。
- v0.8.8：技术自治判断、worktree 成功后合入原目录、临时目录清理。
- v0.8.9：多项目 fullstack worktree。
- v0.9.1：提交前验证矩阵、代码审查包、commit-ready summary。
- v0.9.5：单需求真实开发试跑。
- v0.10：Task Manager 基座，任务和运行记录进入数据库，方便后续 UI 管理。
- v0.10.1：行为验收门禁，解决“lint 通过但交互流程不对”的问题。
- v0.10.2：方法级交互测试计划、方法执行结果、UI 证据 manifest 和 precommit 交互证据门禁。
- v0.10.3A：显式方法级测试命令执行器，precommit 可通过 `--method-test-command` 自动生成方法级证据。
- v0.10.3B：显式 UI 证据采集命令执行器，precommit 可通过 `--ui-capture-command` 自动生成 UI 证据 manifest 输入。
- v0.10.3C：Playwright/Chrome UI 采集模板，生成 capture 脚本、env 示例和人工验收记录模板。
- v0.10.4：真实 DFHIS 单需求提交前样板，覆盖白名单内未跟踪新增文件、方法级命令、UI 人工证据、行为门禁误判收敛和脏仓库范围告警。
- v0.10.5：Task Manager 可登记已有 Harness 产物目录，形成 `task_id/run_id/output_dir` 索引和真实样板复跑模板。
- v0.10.6：Task Manager 可通过 `rerun-precommit` 从任务记录或显式参数复跑提交前验证，并登记新 run。
- v0.10.7：Task Manager 对同 task、同 output_dir、同 execution_mode 的 `register-run` 做幂等处理，并生成 `task_manager_run_history.json/md`。
- v0.10.8：Task Manager 为登记和复跑产物生成 `ui_evidence_reuse_policy.json/md`，固定 UI 证据复用条件和残余风险。
- v0.10.9：Task Manager 可通过 `dashboard` 导出只读 `task_dashboard.json/md/html`，展示任务、run 历史、产物索引、验证状态和 UI 证据状态。
- v0.10.10：Task Manager dashboard 支持筛选，并导出只读 `task_sample_set.json/md` 真实样板集。
- v0.11：Task Manager 可通过 `workbench` 导出单任务只读 `task_workbench.json/md`，展示任务详情、run 详情、产物路径和复跑命令。
- v0.12：Task Manager 可通过 `workspace` 导出只读本地 HTML 工作台入口 `task_workspace.html`，串联 dashboard、sample set 和每个任务 workbench。
- v0.13：Task Manager workbench 可展示最新 run 与上一条 run 对比、证据 warning；workspace 可汇总每个任务 warning 数量和 code。
- v0.13.1：precommit 读取当前本地 diff 时保留完整 patch 原文，只在日志展示层截断，避免大 diff 被截断后导致 `git apply --check` 报 corrupt patch。
- v0.14：Task Manager workspace 数据版本升级为 `0.14-task-workspace`，只读 HTML 顶部展示 warning 汇总，并支持 warning code、DFHIS 编号、验证状态、UI 证据状态和关键词筛选。
- v0.15：主 Workflow 导出 `requirement_calibration.json/md`，在技术自治、验收矩阵和专家团报告前先确认来源优先级、用户补充规则、字段/参数和值域；复杂高风险需求先拆分并要求人工确认。
- v0.16：Task Manager workbench/workspace 索引 `requirement_calibration.json/md`，单任务工作台展示确认卡摘要，workspace 静态 HTML 增加确认卡列、文件复制、状态筛选和参数关键词搜索。
- v0.17A：Task Manager 新增 `harness_task_changes` 修改历史账本，`record-change` 可登记 diff，`rollback-plan` 只生成回滚 dry-run 计划和检查命令；workbench/workspace 展示修改次数和回滚 dry-run 状态。
- v0.17B：Task Manager workspace 输出 `0.17B-task-workspace` 和 `task_details`，静态 HTML 增加任务详情 tabs，可集中查看概览、Run 历史、确认卡、修改历史、回滚 dry-run、证据预览和可复制命令。
- v0.18：Task Manager workspace 输出 `0.18-task-workspace`，新增 `snapshot_comparison`、`export_index`、`task_workspace_snapshot_comparison.json/md` 和 `task_workspace_export_index.json/md`。
- v0.19：Task Manager workspace 输出 `0.19-task-workspace`，新增 `snapshot_history`、`evidence_trend`、`task_workspace_snapshot_history.json/md`、`task_workspace_evidence_trend.json/md` 和 `workspace_snapshots/<snapshot_id>/task_workspace.json`。
- v0.20：Task Manager workspace 输出 `0.20-task-workspace`，新增 `navigation`、`snapshot_detail`、顶部导航、历史快照详情和证据预览摘要/可展开明细。
- v0.21：Task Manager workspace 输出 `0.21-task-workspace`，新增 `ui_polish`、`offline_review`、`task_workspace_offline_review.json/md`、空态/错误态说明、状态标签和大表滚动容器。
- v0.22：新增 Rule Pack、Profile、Credential Store 配置中心骨架，`tools/config_check.py` 可输出脱敏配置摘要，`tools/task_manager.py workspace --include-config-summary` 可显式展示配置中心页；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.23：新增 `app/requirement_provider.py` 和 `tools/requirement_provider_check.py`，把 Yunxiao/TAPD/manual/file 本地 payload 归一成 `0.23-requirement-evidence`；不联网、不保存 token、不写外部系统。
- v0.24：主 workflow 和 Task Manager 支持显式 `--requirement-evidence-file`，可把本地 `requirement_evidence.json/md` 接入报告、workbench 和只读 HTML workspace；不传新参数时默认旧命令不生成新证据产物。
- v0.25：新增 `0.25-configuration-preview`，`tools/config_check.py --include-preview` 可导出 `harness_config_preview.json/md`，`tools/task_manager.py workspace --include-config-preview` 可显式展示配置预览和 Provider 模板；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.26：新增 `0.26-configuration-share-validation`，`tools/config_check.py --include-share-validation` 可导出 `harness_config_share_validation.json/md`，`tools/task_manager.py workspace --include-config-share-validation` 可显式展示配置分享校验和本地覆盖策略；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.27：新增 `0.27-configuration-import-draft`，`tools/config_check.py --include-import-draft --draft-output-dir <dir>` 可生成 `profiles.draft.json`、`rule_pack.draft.json`、`credentials.example.json`、`IMPORT_GUIDE.md` 和 `config_import_manifest.json`；`tools/task_manager.py workspace --include-config-import-draft --draft-output-dir <dir>` 可显式展示配置导入草案；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.28：新增 `0.28-configuration-import-review`，`tools/config_check.py --review-import-draft --draft-input-dir <dir>` 可回读 v0.27 草案并输出 `harness_config_import_review.json/md`；`tools/task_manager.py workspace --include-config-import-review --draft-input-dir <dir>` 可显式展示配置导入回读校验、只读表单预览和导入前风险提示；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.29：新增 `0.29-configuration-template-index`，`tools/config_check.py --include-template-index --draft-input-dir <dir> [--compare-draft-input-dir <dir>]` 可索引草案并输出 `harness_config_template_index.json/md`；`tools/task_manager.py workspace --include-config-template-index --draft-input-dir <dir>` 可显式展示配置模板索引、多 Profile 切换预览和配置差异对比；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.30：新增 `0.30-configuration-wizard`，`tools/config_check.py --include-config-wizard --draft-input-dir <dir> [--compare-draft-input-dir <dir>]` 可输出 `harness_config_wizard.json/md`；`tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 可显式展示配置向导、步骤状态、复制命令和人工确认清单；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.31：新增 `0.31-configuration-wizard-readability`，`--include-config-wizard` 的 JSON/Markdown/HTML 增加步骤筛选、阻断摘要、命令复制目标、空结果提示和静态筛选脚本；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.32：新增 `0.32-configuration-review-package-index`，`tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 会显式输出 `0.32-task-workspace` 和 `task_workspace_config_review_package.json/md`，把配置产物、复跑命令和人工确认项汇成只读配置审查包；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.33：新增 `0.33-configuration-review-package-readability`，配置审查包增加文件搜索、文件状态筛选、待确认分组和交接摘要；不传新参数时旧 workspace 仍保持 v0.21 行为。
- v0.34：新增只读分层配置解析器、v0.33 兼容适配器、来源追踪、不可变快照和硬保护裁决；只有显式 `--include-resolved-config` 才输出新结果，默认旧命令行为不变。
- v0.35：新增 `core-closure-trial`，先生成 RequirementContract 和 EngineeringHandoff；只有低风险、需求校准 ready、工程证据、白名单、专项验证、自动验收和默认行为都完整时，才会进入受控 worktree。worktree 成功后还需独立 diff 审查，成功状态只表示 `ready_for_manual_review`，不替代人工代码审查和业务验收。
- v0.37：`core-closure-trial` 默认本地优先应用；仅当 worktree、专项验证和独立 diff 审查全通过且目标白名单路径无无关本地改动时，才自动应用已审查 patch。`--review-only` 可显式禁用本地应用；不会自动执行任何远端 Git、发布或云效/TAPD 写操作。
- v0.38：新增 `auto-local` 快速入口。它只会解析为现有的 `core-closure-trial`，并记录 `execution_route_json`；低风险需求无需重复指定已可推导的白名单和专项验证，若合同、工程交接或安全闸门不能成立则直接阻断，不降级为固定九角色流程，也不会远端写入。
- v0.39：新增 `contract_verification`。云效评论会被只读采集并纳入需求证据，但不作为代码放行依据；跨层需求必须有客户端请求和 BFF/服务端/公共 API 的源码命中，核心闭环会阻断未核验契约。
- v0.40：契约命中要求接口和全部明确参数位于同一请求/签名局部上下文；评论中的接口参数同步进入需求校准。DFHIS-31551 回放已验证缺失 `sortOrder` 或后端签名时会在 worktree 前阻断。
- v0.41：显式 `harness-rules` 的 `request_param` 覆盖冲突的云效评论参数；契约核验只使用用户确认后的参数集，避免旧评论把已废弃参数重新引入当前需求。
- v0.42：人工运行时验收作为独立本地证据绑定来源 run，保留原源码门禁，不会放宽自动应用、提交或外部写入。
- v0.43：`auto-local` 为明确范围的低风险前端小改动跳过全仓工程上下文扫描；只接受调用方显式项目与白名单，不接受推导路径作为快车道依据。跨层、排序和高风险需求继续走完整核心闭环。
- v0.44：`auto-local` 运行会保存端到端阶段耗时与快车道命中/回退证据，便于量化小需求实际节省的时间；该可观测性不影响闭环判定或本地应用规则。
- v0.45：真实 `auto-local` 回放可登记原始 Harness run 后再记录人工运行时验收；输出的 `run.id` 与当前数据库不一致时不会静默复用，人工验收也不会放宽源码门禁、自动应用、提交或外部写入。
- v0.46：核心闭环运行报告按实际 lifecycle 输出下一步，`ready_for_manual_review` 只表示本地闭环已通过、仍等待人工审查和业务验收，不表示已提交、已推送或已发布。
- v0.47：排序/方案树/右侧列表关联需求可通过显式 `--acceptance-contract-file` 接入可执行 fixture；缺少或失败 fixture 在 worktree 前阻断，通过后强制执行 fixture 声明的业务仓库专项测试，并在独立 diff 审查中检查实现证据。该能力只处理本地脱敏 JSON，不连接 PG、浏览器或外部需求系统。
- v0.48：独立 PG 数据证据 CLI 已完成 Profile 自动发现、测试/开发环境白名单、源码候选评分、唯一性门禁、只读 SQL 守卫、固定超时/行数预算、敏感列掩码和安全审计。它没有接入普通 workflow；未显式执行 `tools/pg_evidence.py --mode execute` 时不会连接数据库。

## v0.10.2 已完成内容

新增文件：

- `app/behavior_acceptance.py`
- `tools/behavior_check.py`
- `app/interaction_evidence.py`
- `tools/interaction_evidence_check.py`

已接入：

- `app/precommit_verifier.py`
- `tools/precommit_verify.py`
- `tools/self_check.py`
- `app/harness.py`
- `README.md`
- `harnesses/his_requirement_workflow.py`

行为门禁能力：

- 把 bug/需求拆成“必须发生 / 禁止发生 / 必须保持”的行为断言。
- 对 `$alert`、`$confirm`、`catch`、`loading`、`closeSettlementProgress`、收费/结算/医保/退费路径做额外检查。
- 拦截空提示、重复提示、兜底提示替换真实原因、关闭动作误入外层业务失败 `catch` 等模式。
- 行为验收未通过时，不允许自动提交、云效交付评论或云效状态流转。

交互证据能力：

- 生成 `behavior_test_plan.json/md`、`method_regression_result.json/md`、`ui_evidence_manifest.json/md`、`playwright_screenshot_index.md`、`interaction_evidence.json/md`。
- 对交互敏感 diff 要求方法级结果覆盖 `$alert` / `$confirm` resolve、`close/cancel`、重复/空提示、收费/结算收尾路径。
- precommit 中，交互敏感 diff 没有方法级通过结果时阻断提交准备。
- 方法级测试通过但没有截图/视频/GIF/人工 UI 证据时，可进入提交准备，但云效交付评论仍不放行。
- 有方法级测试和 UI 证据时，允许进入提交准备和云效交付评论准备；真实云效状态流转仍冻结。

## v0.10.3A 已完成内容

新增文件：

- `app/method_test_runner.py`

已接入：

- `app/precommit_verifier.py`
- `tools/precommit_verify.py`
- `tools/interaction_evidence_check.py`
- `tools/self_check.py`
- `app/harness.py`
- `harnesses/his_requirement_workflow.py`
- `README.md`

能力：

- `--method-test-command` 可在 precommit 临时 worktree 中执行用户显式传入的命令。
- 命令 stdout 需输出 JSON：`{"cases":[{"id":"METHOD-...","status":"pass","evidence":"..."}]}`。
- runner 输出会写入 `method_test_runner.json/md`，并作为 v0.10.2 `method_evidence.cases` 进入交互证据门禁。
- 只要传入 `--method-test-command`，命令失败或缺失必需 case 会作为提交前阻断项。
- 本阶段仍不自动打开真实业务页面、不自动生成 Playwright/Chrome 截图、不真实流转云效状态。

## v0.10.3B 已完成内容

新增文件：

- `app/ui_evidence_runner.py`

已接入：

- `app/precommit_verifier.py`
- `tools/precommit_verify.py`
- `tools/interaction_evidence_check.py`
- `tools/self_check.py`
- `app/harness.py`
- `harnesses/his_requirement_workflow.py`
- `README.md`

能力：

- `--ui-capture-command` 可在 precommit 临时 worktree 中执行用户显式传入的 UI 证据采集命令。
- Harness 会向命令注入 `HARNESS_UI_EVIDENCE_DIR`，截图、视频、GIF 或人工记录文件应写入该目录。
- 命令 stdout 需输出 JSON：`{"artifacts":[{"path":"progress_closed.png","kind":"screenshot","label":"进度详情已关闭"}],"assertions":[{"name":"dialog_count","status":"pass","evidence":"未出现重复弹框"}]}`。
- runner 输出会写入 `ui_evidence_runner.json/md`，并把实际存在的证据文件路径合并进 `ui_evidence_manifest`。
- 只要传入 `--ui-capture-command`，命令失败、状态断言失败或未生成证据文件会作为提交前阻断项。
- 本阶段承接 Playwright/Chrome 脚本，但不自动生成脚本、不自动处理 HIS 登录态、不自动验收真实收费/结算业务通过。

## v0.10.3C 已完成内容

新增文件：

- `app/ui_capture_template.py`
- `tools/ui_capture_template.py`

已接入：

- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`

能力：

- `tools/ui_capture_template.py` 可生成 `playwright_capture.mjs`、`playwright_capture.env.example`、`manual_acceptance_record.md` 和模板说明。
- 模板通过 `HIS_UI_BASE_URL`、`HIS_UI_ROUTE`、`HIS_UI_STORAGE_STATE`、`HARNESS_UI_EVIDENCE_DIR` 描述本地前端地址、目标路由、登录态文件和证据输出目录。
- 生成的 Playwright 脚本会采集截图、弹框数量、弹框文案、loading 数量、进度条数量，并向 stdout 输出 v0.10.3B runner 可解析的 `artifacts/assertions` JSON。
- 模板明确不保存密码、token、cookie 原文，不自动登录，不替代人工准备测试账号、测试数据和真实 HIS 页面验收。

最近验证：

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/behavior_check.py tools/interaction_evidence_check.py tools/ui_capture_template.py tools/cleanup_worktrees.py
```

结果：通过。

```bash
python3 -c "from pathlib import Path; from tools.self_check import run_interaction_evidence_checks; import json; result = run_interaction_evidence_checks(output_dir=Path('/tmp/his_harness_v0103c_final')); print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if all(item['status']=='pass' for item in result) else 1)"
```

结果：通过。覆盖：

- 交互证据包生成。
- 无方法级证据时 precommit 阻断。
- 有方法级证据和 UI 证据时 precommit 放行提交准备。
- `run_method_test_commands` 可从显式命令 stdout JSON 生成方法级证据。
- precommit 可通过 `method_test_commands` 自动生成 method evidence 并放行。
- `run_ui_evidence_commands` 可从显式命令 stdout JSON 生成 UI 证据文件和状态断言。
- precommit 可通过 `ui_capture_commands` 自动生成 UI evidence 并放行云效评论准备。
- `write_playwright_capture_template` 可生成 Playwright/Chrome capture 脚本、env 示例和人工验收记录模板。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v0103c_self_check
```

结果：通过。报告路径：

```text
/tmp/his_harness_v0103c_self_check/self_check_report.md
```

还做过最小红/绿 diff 验证：

- 错误补丁：外层 `catch` 二次弹“收费结算失败”或空提示，行为门禁应失败。
- 正确补丁：本地处理 `$alert` 的 `close/cancel`，继续进度关闭和 `return` 收尾，行为门禁应通过。

## v0.10.4 已完成内容

真实样板：

- 需求：DFHIS-31465《【运城口腔】挂号窗口新增'科室'过滤条件》。
- 业务改动范围：`df-web-guahaosf` 的 `src/pages/yeWuGn/guaHaoSf/index.vue` 和新增 `src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js`。
- 样板产物目录：`/tmp/his_harness_DFHIS-31465_v0104_trial`。

新增/修正能力：

- precommit 当前 diff 支持白名单内未跟踪新增文件，会把新增文件拼成标准 `new file` patch 后在临时 worktree 中验证。
- `validate_patch` 默认仍拒绝新增/删除文件；只有 precommit 当前 diff 显式开启新增文件校验，且仍拒绝删除文件。
- 同仓库存在白名单外未提交改动时，目标文件验证不再被误判失败；Harness 会标记为范围告警，并将 `can_commit` / `can_yunxiao_comment` 置为 `false`。
- 行为门禁拆分“人类需求上下文”和“代码 diff”判断，避免普通排班方法名如 `loadingPaiBan` 与标题中的“收费”组合后误套结算弹框/进度条用例。

样板验证结果：

- `precommit_manifest.json`：`status=success`。
- `verification_matrix.json`：`overall_status=pass`，`can_enter_test=人工代码审查通过后可进入测试`。
- `can_commit=false`、`can_yunxiao_comment=false`，原因是 `df-web-guahaosf` 同仓库还有白名单外未提交改动，不能直接整体提交或写云效交付评论。
- 方法级证据覆盖 `paiBanMs=1`、`paiBanMs=2`、空/非法值默认模式。
- UI 证据使用人工验收记录：`worktrees/ui_evidence_31465104/manual_acceptance_DFHIS-31465.md`。

最近验证：

```bash
python3 -m py_compile app/behavior_acceptance.py app/precommit_verifier.py app/worktree_executor.py tools/self_check.py
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v0104_untracked_self_check
```

结果：通过。新增覆盖：

- 白名单内未跟踪新增文件可被 precommit 复现到临时 worktree。
- 白名单外 dirty scope 只阻止提交/云效评论，不把目标验证判为失败。
- 非交互排班过滤改动不会因页面标题含“收费”被误套结算交互门禁。

## v0.10.5 已完成内容

新增/修正能力：

- `tools/task_manager.py register-run`：把已有 Harness 产物目录登记为 Task Manager run。
- `app.task_manager.TaskExistingRunOptions` 和 `TaskManager.record_existing_run`：读取已有 `output_dir` 的 `precommit_manifest.json`、`verification_matrix.json` 和常见证据文件，创建登记型 `run_id`，写入 `harness_task_runs`。
- `app.database.update_task_run`：登记记录生成后回写 run 级 `artifact_paths`，方便后续 UI 直接按 run 索引产物。
- `build_latest_artifacts` 扩展为索引 precommit、verification matrix、behavior、interaction、method、UI、code review、commit summary 和 Task Manager 登记记录。
- `tools/self_check.py` 新增 Task Manager 真实样板登记自检，使用隔离 sqlite，不污染默认数据库。

真实样板登记结果：

- 需求：DFHIS-31465《【运城口腔】挂号窗口新增'科室'过滤条件》。
- Task Key：`requirement-dfhis-31465`。
- Task ID：`2`。
- Run ID：`325`。
- Task Run ID：`2`。
- Output Dir：`/tmp/his_harness_DFHIS-31465_v0104_trial`。
- 登记记录：`/tmp/his_harness_DFHIS-31465_v0104_trial/task_manager_real_trial_record.md`。
- 结论保持：`verification_status=passed`、`can_commit=false`、`can_yunxiao_transition=false`。

新增模板：

- `real_precommit_trial_template.md`：已有本地 diff 的真实需求 precommit 验证和 Task Manager 登记命令模板。
- `scope_warning_policy.md`：目标验证通过但白名单外 dirty scope 阻止提交/云效评论的策略说明。

最近验证：

```bash
python3 -m py_compile app/database.py app/task_manager.py tools/task_manager.py tools/self_check.py
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v0105_run_artifacts_green_self_check
```

结果：通过。新增覆盖：

- 已有 precommit 产物目录可登记为 Task Manager task/run。
- 登记型 run 会写入 `task_id/run_id/output_dir`。
- task 和 task_run 都能索引 `task_manager_real_trial_record.json/md`。

## v0.10.6-v0.10.8 已完成内容

新增/修正能力：

- `tools/task_manager.py rerun-precommit`：从 Task Manager 任务记录或命令行显式参数复跑提交前验证。
- `app.task_manager.TaskPrecommitRerunOptions` 和 `TaskManager.rerun_precommit`：复用 `PrecommitVerifier`，把复跑结果写成标准 precommit 产物，并登记回 `harness_task_runs`。
- `register-run` 幂等：同一个 task、同一个 output_dir、同一个 execution_mode 重复登记时返回原 task_run/run_id，不重复制造历史记录。
- `task_manager_run_history.json/md`：每次登记或复跑后输出同一任务下的 run 历史，方便 UI 和人工比较。
- `ui_evidence_reuse_policy.json/md`：记录 UI 证据可复用条件、不能跨需求复用的边界、Playwright/Chrome 登录态和测试数据残余风险。
- `$his-harness` skill 已同步 Task Manager `register-run`、`rerun-precommit` 命令和静态检查命令。

最近验证：

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/cleanup_worktrees.py tools/task_manager.py
```

结果：通过。

```bash
python3 tools/task_manager.py rerun-precommit --help
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v0108_final_self_check
```

结果：通过。新增覆盖：

- 同一已有 precommit output_dir 重复登记不会重复创建 task_run。
- 登记型 run 和复跑 run 都能索引 `task_manager_run_history.json/md`。
- 登记型 run 和复跑 run 都能索引 `ui_evidence_reuse_policy.json/md`。
- 新建任务可通过 `rerun-precommit` 执行 precommit 验证并自动登记 output_dir。

## v0.10.9 已完成内容

新增/修正能力：

- `TaskManager.build_dashboard`：从 `harness_tasks` 和 `harness_task_runs` 构建只读任务看板数据。
- `TaskManager.write_dashboard_outputs`：导出 `task_dashboard.json`、`task_dashboard.md`、`task_dashboard.html`。
- `tools/task_manager.py dashboard`：新增只读 dashboard CLI。
- 看板数据包含任务、run 历史、最新产物索引、验证状态、是否可提交、UI 证据状态和云效写入关闭标记。
- 不新增数据库表，不改业务仓库，不提交、不推送、不发布、不写云效。

最近验证：

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/cleanup_worktrees.py tools/task_manager.py
```

结果：通过。

```bash
python3 tools/task_manager.py dashboard --help
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v0109_green_self_check
```

结果：通过。新增覆盖：

- dashboard 可读取任务列表。
- dashboard 可读取历史 run。
- dashboard 可读取最新产物索引。
- dashboard 可读取 UI 证据状态。
- dashboard 可导出 JSON、Markdown 和静态 HTML。

## v0.10.10 已完成内容

新增/修正能力：

- `TaskDashboardFilters`：支持按 DFHIS 编号、Task Key、类型、状态、验证状态、UI 证据状态、是否可提交、真实样板筛选。
- `TaskManager.build_dashboard`：返回 v0.10.10 dashboard，并在结果中包含 `filters` 和 `sample_set`。
- `TaskManager.write_dashboard_outputs`：除 `task_dashboard.json/md/html` 外，额外导出 `task_sample_set.json/md`。
- `tools/task_manager.py dashboard`：新增 `--entity-id`、`--task-key`、`--entity-kind`、`--status`、`--verification-status`、`--ui-evidence-status`、`--can-commit`、`--sample-only`。
- 真实样板集当前只收录 Task Manager 登记的 existing-output 产物，不把普通 mock rerun 误标成真实样板。
- 该能力只读：不改业务仓库、不复跑任务、不提交、不推送、不发布、不写云效。

最近验证：

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/cleanup_worktrees.py tools/task_manager.py
```

结果：通过。

```bash
python3 tools/task_manager.py dashboard --help
```

结果：通过，已展示 dashboard 筛选参数。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v01010_final_self_check
```

结果：通过。新增覆盖：

- dashboard 可输出 v0.10.10 版本和筛选条件。
- dashboard 可按 `DFHIS-31465 + passed + UI present + sample_only` 筛出真实样板。
- sample set 可读取任务、产物目录、precommit manifest、验证矩阵和 UI 证据状态。
- dashboard 导出包含 `task_sample_set.json/md`。

## v0.11 已完成内容

新增/修正能力：

- `TaskManager.build_task_workbench`：读取单个任务，生成只读本地工作台数据。
- `TaskManager.write_workbench_outputs`：导出 `task_workbench.json` 和 `task_workbench.md`。
- `tools/task_manager.py workbench`：新增单任务工作台 CLI，支持 `--task-id`、`--task-key`、`--yunxiao-url` 查找任务。
- 工作台数据包含任务详情、run 详情、产物路径、产物存在状态、`open` 产物路径命令和可复制 `rerun-precommit` 命令。
- 该能力只读：不改业务仓库、不自动复跑任务、不提交、不推送、不发布、不写云效。

最近验证：

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/cleanup_worktrees.py tools/task_manager.py
```

结果：通过。

```bash
python3 tools/task_manager.py workbench --help
```

结果：通过，已展示 workbench 查找参数和输出目录参数。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v011_final_self_check
```

结果：通过。新增覆盖：

- workbench 可按 `task_key=requirement-dfhis-31465` 读取任务详情。
- workbench 可读取 run 详情和 artifact 路径。
- workbench 可生成 `task_workbench.json/md`。
- workbench 可生成包含 `tools/task_manager.py rerun-precommit` 的可复制复跑命令。

## v0.12 已完成内容

新增/修正能力：

- `TaskManager.build_task_workspace`：基于 dashboard 数据生成只读本地工作台入口数据，包含 summary、sample set、任务入口、workbench 相对路径和可复制复跑命令。
- `TaskManager.write_workspace_outputs`：导出 `task_workspace.json/html`，并同步写出 `task_dashboard.json/md/html`、`task_sample_set.json/md` 和 `workbenches/<task_key>/task_workbench.json/md`。
- `tools/task_manager.py workspace`：新增只读本地 HTML 工作台 CLI，支持 dashboard 同款筛选参数。
- 工作台入口会从任务列表跳到单任务 workbench，并保留 dashboard/sample set 文件链接。
- 该能力只读：不改业务仓库、不自动复跑任务、不提交、不推送、不发布、不写云效。

最近验证：

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/cleanup_worktrees.py tools/task_manager.py
```

结果：通过。

```bash
python3 tools/task_manager.py workspace --help
```

结果：通过，已展示 workspace 筛选参数和输出目录参数。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v012_green_self_check
```

结果：通过。新增覆盖：

- workspace 可输出 v0.12 版本和只读标记。
- workspace 可汇总 dashboard summary 和真实样板数量。
- workspace 可生成 `task_workspace.json/html`。
- workspace 可同步导出 `task_dashboard.html` 和 `task_sample_set.json`。
- workspace 可生成 `workbenches/<task_key>/task_workbench.md`。
- HTML 入口可索引 `requirement-dfhis-31465`、dashboard/sample set 链接、workbench 链接和 `tools/task_manager.py rerun-precommit` 复跑命令。

```bash
python3 tools/task_manager.py workspace --limit 50 --output-dir /tmp/his_harness_task_workspace_v012_final
```

结果：通过。默认 Task Manager 数据导出：

- Tasks：2。
- Runs：2。
- Samples：1。
- HTML：`/tmp/his_harness_task_workspace_v012_final/task_workspace.html`。
- Workbenches：2。

## v0.13 已完成内容

新增/修正能力：

- `TaskManager.build_task_workbench`：版本升级为 `0.13-task-workbench`，新增 `run_history_comparison` 和 `evidence_warnings`。
- `build_run_history_comparison`：对比同一任务最新 run 和上一条 run 的状态、验证状态、UI 证据状态和产物数量。
- `build_task_evidence_warnings`：只读标记最新产物目录缺失、最新产物路径缺失、precommit 关键产物缺失、最新 UI 证据缺失但历史 run 有 UI 证据等风险。
- `TaskManager.build_task_workspace`：版本升级为 `0.13-task-workspace`，每个 entry 汇总 `warning_count` 和 `warning_codes`。
- `task_workbench.md` 增加 “Run 对比” 和 “证据 Warning” 区块；`task_workspace.html` 增加 warning 列。
- 该能力只读：不改业务仓库、不自动复跑任务、不提交、不推送、不发布、不写云效。

最近验证：

```bash
python3 -m py_compile app/task_manager.py tools/task_manager.py tools/self_check.py
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v013_green_self_check
```

结果：通过。新增覆盖：

- 同一任务两条 run 可生成 `run_history_comparison`。
- 最新 run 缺 UI 证据但上一条 run 有 UI 证据时，输出 `latest_ui_evidence_missing_but_previous_present`。
- 最新 precommit run 缺少验证矩阵时，输出 `latest_artifact_missing` 且 `kind=verification_matrix`。
- workspace HTML 可索引 warning code。
- workbench/workspace 导出文件仍可生成。

专项证据：

```text
/tmp/his_harness_v013_green_self_check/task_manager/workbench_v013/task_workbench.md
/tmp/his_harness_v013_green_self_check/task_manager/workspace_v013/task_workspace.html
```

关键片段已验证包含：

- `0.13-task-workbench`
- `Run 对比`
- `证据 Warning`
- `latest_artifact_missing`
- `latest_ui_evidence_missing_but_previous_present`

实际 workspace 导出：

```bash
python3 tools/task_manager.py workspace --limit 50 --output-dir /tmp/his_harness_task_workspace_v013_final
```

结果：通过。默认 Task Manager 数据导出：

- Tasks：2。
- Runs：2。
- Samples：1。
- HTML：`/tmp/his_harness_task_workspace_v013_final/task_workspace.html`。
- Workbenches：2。

## v0.13.1 已完成内容

修复能力：

- `app.worktree_executor.run_command` 新增 `truncate_output` 开关，默认保持日志截断。
- `app.precommit_verifier.read_allowed_current_diff` 读取 tracked diff 和白名单内未跟踪新增文件 diff 时关闭截断，保证 `current_diff` 是完整 patch。
- 新增 self-check：`precommit_keeps_large_diff_untruncated_for_apply`，覆盖大 diff 中不能出现 `...（日志已截断）...` 且临时 worktree apply-check 必须通过。
- 真实复跑 `/Users/lym/Desktop/dongFang/dfcode/df-web-menzhenysz` 的 `local-zhenjian-review`：包装层 `apply_check/apply/diff_check` 均通过，三条显式验证命令均通过；剩余阻断为行为/交互证据门禁，不再是 corrupt patch。

最近验证：

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_large_diff_green
```

结果：通过。报告路径：

```text
/tmp/his_harness_large_diff_green/self_check_report.md
```

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/cleanup_worktrees.py tools/task_manager.py
```

结果：通过。

真实复跑产物：

```text
/private/tmp/his_harness_local_zhenjian_review_after_fix
```

## v0.14 已完成内容

新增/修正能力：

- `TaskManager.build_task_workspace`：版本升级为 `0.14-task-workspace`，新增顶层 `warning_summary` 和 `filter_options`。
- workspace entry 新增 `filter_data` 和 `search_text`，用于静态 HTML 前端筛选，不依赖服务端。
- `task_workspace.html` 顶部新增 warning 汇总，展示 warning 总数、存在 warning 的任务数和各 warning code 计数。
- `task_workspace.html` 新增本地搜索框和只读筛选控件，可按 warning code、DFHIS 编号、验证状态、UI 证据状态筛选。
- 新增 self-check：`task_manager_workspace_warning_summary_filters_and_search`，覆盖 warning 汇总、筛选元数据、任务行 data 属性和静态 JS。
- 该能力只读：不改业务仓库、不自动复跑任务、不提交、不推送、不发布、不写云效。

最近验证：

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/cleanup_worktrees.py tools/task_manager.py
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v014_green_self_check
```

结果：通过。新增覆盖：

- workspace 数据版本为 `0.14-task-workspace`。
- 有 warning 的夹具会生成 `warning_summary.total_warning_count` 和 `task_count_with_warnings`。
- `latest_artifact_missing`、`latest_ui_evidence_missing_but_previous_present` 可进入 warning 汇总和筛选项。
- entry 级 `filter_data` 和 `search_text` 可索引 DFHIS 编号、warning code 和任务信息。
- HTML 包含 `workspace-search`、`warning-filter`、`warning-summary`、`applyWorkspaceFilters` 和任务行 data 属性。

实际 workspace 导出：

```bash
python3 tools/task_manager.py workspace --limit 50 --output-dir /tmp/his_harness_task_workspace_v014_final
```

结果：通过。默认 Task Manager 数据导出：

- Tasks：2。
- Runs：2。
- Samples：1。
- HTML：`/private/tmp/his_harness_task_workspace_v014_final/task_workspace.html`。
- JSON：`/private/tmp/his_harness_task_workspace_v014_final/task_workspace.json`。
- Workbenches：2。

## v0.15 已完成内容

新增文件：

- `app/requirement_calibration.py`

已接入：

- `app/harness.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 主 Workflow 会生成 `requirement_calibration.json/md`，并写入最终报告、导出目录和专家团上下文。
- 用户明确说“按照我说的来”“不要按照需求图”时，确认卡会把 `user_instruction` 放到来源优先级第一位，并输出 `source_conflict` warning，后续实现必须按用户补充规则。
- 对菜单参数、路由参数等需求会结构化记录参数名、位置、值域和默认行为；本次样板覆盖 `paiBanMs=1/2/empty`。
- 对医保、结算、收费、报表、对账、金额、基金、统筹、回写等复杂高风险需求，会输出 `needs_human_confirmation`、拆分子任务和必须确认项，不允许直接自动改码。
- v0.15 只校准需求理解，不自动读取额外页面、不自动拆云效子任务、不自动改业务代码、不自动提交、不写云效。

最近验证：

```bash
python3 -m py_compile app/harness.py app/requirement_calibration.py tools/self_check.py
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v015_completion_check
```

结果：通过。新增覆盖：

- 用户补充规则优先于云效/需求图描述。
- `paiBanMs` 被识别为菜单/路由参数，且值域包含 `1`、`2`、`empty`。
- 复杂医保结算报表/对账需求要求人工确认并生成不少于 3 个拆分子任务。
- Markdown 确认卡包含 `用户补充规则优先`、`paiBanMs` 和 `不自动写云效`。
- 主 Workflow 会导出 `requirement_calibration.json` 和 `requirement_calibration.md`。

专项证据：

```text
/tmp/his_harness_v015_completion_check/self_check_report.md
/tmp/his_harness_v015_completion_check/requirement_calibration_workflow/run_505/requirement_calibration.md
/tmp/his_harness_v015_completion_check/requirement_calibration_workflow/run_505/requirement_calibration.json
```

## v0.16 已完成内容

已接入：

- `app/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- `build_latest_artifacts` 会索引已有 output_dir 中的 `requirement_calibration.json` 和 `requirement_calibration.md`。
- `TaskManager.build_task_workbench` 输出 `0.16-task-workbench`，新增 `requirement_calibration` 摘要，包含状态、置信度、参数名、来源优先级、warning、原文摘要和原始路径。
- `task_workbench.md` 增加“需求理解确认卡”区块，人工可以在单任务页直接看到 `paiBanMs`、用户补充规则优先等关键结论。
- `TaskManager.build_task_workspace` 输出 `0.16-task-workspace`，每个 entry 带确认卡摘要和相对链接。
- `write_workspace_outputs` 会复制每个任务的 `workbenches/<task_key>/requirement_calibration.json/md`。
- `task_workspace.html` 增加“需求理解确认卡”列、确认卡状态筛选、参数/摘要关键词搜索。
- v0.16 仍然只读：不自动生成新确认卡、不复跑验证、不打开业务页面、不改业务仓库、不提交、不推送、不发布、不写云效。

最近验证：

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v016_green_probe2
```

结果：通过。新增覆盖：

- workbench 产物索引包含 `requirement_calibration_json` 和 `requirement_calibration_md`。
- workbench Markdown 包含 `需求理解确认卡`、`用户补充规则优先` 和 `paiBanMs`。
- workspace HTML 能链接 `workbenches/requirement-dfhis-31465/requirement_calibration.md`。
- workspace entry 的 `filter_data.requirement_calibration_status` 为 `ready_for_development`。
- workspace `search_text` 能搜索到 `paiBanMs`。
- workspace 导出时会复制确认卡 Markdown/JSON 到对应 workbench 目录。

专项证据：

```text
/tmp/his_harness_v016_green_probe2/self_check_report.md
/tmp/his_harness_v016_green_probe2/task_manager/workbench/task_workbench.md
/tmp/his_harness_v016_green_probe2/task_manager/workspace/task_workspace.html
/tmp/his_harness_v016_green_probe2/task_manager/workspace/workbenches/requirement-dfhis-31465/requirement_calibration.md
```

## v0.17A 已完成内容

已接入：

- `app/database.py`
- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `harness_task_changes` 表，记录 `task_id/task_run_id/run_id/change_sequence/change_id/diff_path/diff_summary/diff_sha256/verification_status/rollback_mode`。
- `TaskManager.record_change` 和 `tools/task_manager.py record-change` 可把已确认 diff 登记到任务修改历史账本。
- `TaskManager.build_change_rollback_plan` 和 `tools/task_manager.py rollback-plan` 可生成 `rollback_plan.json/md`、`change_<n>_reverse.patch` 和 `git apply --reverse --check` 命令。
- `TaskManager.build_task_workbench` 输出 `0.17-task-workbench`，新增 `change_history`，并导出 `task_change_history.json/md`。
- `TaskManager.build_task_workspace` 在 v0.17A 时输出修改历史 entry；v0.17B 已升级为 `0.17B-task-workspace`，新增 `task_details` 和静态 HTML 任务详情 tabs。
- v0.17A 仍然只读：不会自动执行回滚、不会修改业务仓库、不会提交、不会推送、不会发布、不会写云效。

最近验证：

```bash
python3 -m py_compile app/database.py app/task_manager.py tools/task_manager.py tools/self_check.py
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v017_final
```

结果：通过。新增覆盖：

- 同一任务可登记两次修改，`change_sequence` 为 1 和 2。
- workbench `change_history.change_count` 为 2，最新修改序号为 2。
- workspace entry 能显示 `change_count=2` 和 `rollback_mode=dry_run_only`。
- `rollback-plan` 输出 `0.17-rollback-dry-run`，`dry_run_only=true`，`will_modify_files=false`。
- 回滚计划包含 `git apply --reverse --check`，并写出 `change_2_reverse.patch`。
- workbench/workspace HTML/Markdown 包含“修改历史”和“回滚 dry-run”。

专项证据：

```text
/tmp/his_harness_v017_final/self_check_report.md
/tmp/his_harness_v017_final/task_manager/workbench_v017/task_workbench.md
/tmp/his_harness_v017_final/task_manager/workbench_v017/task_change_history.md
/tmp/his_harness_v017_final/task_manager/workspace_v017/task_workspace.html
/tmp/his_harness_v017_final/task_manager/rollback_plan/rollback_plan.md
```

## v0.17B 已完成内容

已接入：

- `app/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- `TaskManager.build_task_workspace` 输出升级为 `0.17B-task-workspace`。
- workspace JSON 新增 `task_details`，每个 detail 带 `0.17B-task-detail`、概览、run 列表、产物索引、确认卡、修改历史、run 对比、warning、证据预览和命令集合。
- 静态 `task_workspace.html` 保留 v0.14 warning 汇总、筛选和搜索，同时新增 `id="task-detail-panel"` 的任务详情区域。
- 详情 tabs 包含：概览、Run 历史、需求理解确认卡、修改历史、回滚 dry-run、证据预览、可复制命令。
- 证据预览优先展示确认卡、`verification_matrix.json`、`ui_evidence_manifest`、截图索引、precommit manifest 和 `task_change_history.md` 摘要。
- `rollback_dry_run` 仍是命令文本，只生成 `tools/task_manager.py rollback-plan ...`，不会自动执行反向 patch。
- v0.17B 仍然只读：不会自动打开业务页面、不会自动复跑命令、不会执行回滚、不会修改业务仓库、不会提交、不会推送、不会写云效。

最近验证：

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v017b_impl
```

结果：通过。新增覆盖：

- workspace version 为 `0.17B-task-workspace`。
- `task_details` 能找到 `requirement-dfhis-31465`。
- 详情内包含 run、产物、确认卡、修改历史和 `rollback_dry_run` 命令。
- HTML 包含任务详情、Run 历史、确认卡、修改历史、回滚 dry-run、证据预览、可复制命令等 tabs。
- HTML 不包含 `fetch(`、`XMLHttpRequest`、`exec(`、`child_process` 这类远程读取或执行入口。

专项证据：

```text
/tmp/his_harness_v017b_impl/self_check_report.md
/tmp/his_harness_v017b_impl/task_manager/workspace_v017/task_workspace.html
/tmp/his_harness_v017b_impl/task_manager/workspace_v017/task_workspace.json
```

## v0.18 已完成内容

已接入：

- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- `TaskManager.build_task_workspace` 输出升级为 `0.18-task-workspace`。
- `write_workspace_outputs` 会在写入前读取同一输出目录下的上一版 `task_workspace.json`，生成 `snapshot_comparison`。
- 新增 `0.18-workspace-snapshot-comparison`，对比任务数、run 数、warning 数、样板数、修改次数，以及每个任务的状态、验证、UI 证据、最新 run、run 数、warning、修改次数、确认卡状态变化。
- 新增 `0.18-workspace-export-index`，集中列出 workspace、dashboard、sample set、workbench、确认卡、修改历史等导出文件。
- `task_workspace.html` 新增 `id="workspace-snapshot-comparison"` 和 `id="workspace-export-index"` 两块只读区域。
- 新增导出文件：`task_workspace_snapshot_comparison.json/md`、`task_workspace_export_index.json/md`。
- v0.18 仍然只读：不会重新验证产物、不会读取远端、不会自动打开业务页面、不会自动复跑命令、不会执行回滚、不会修改业务仓库、不会提交、不会推送、不会写云效。

最近验证：

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v018_impl
```

结果：通过。新增覆盖：

- workspace version 为 `0.18-task-workspace`。
- 导出索引 version 为 `0.18-workspace-export-index`，并覆盖 workspace、dashboard、sample set、workbenches 分组。
- 快照对比 version 为 `0.18-workspace-snapshot-comparison`，能比较上一版和当前版。
- DFHIS-31465 的快照变化包含 `warning_count` 和 `run_count`。
- HTML 包含“历史快照对比”和“导出索引”，并链接 `task_workspace_snapshot_comparison.json`、`task_workspace_export_index.json`。
- HTML 不包含 `fetch(`、`XMLHttpRequest`、`exec(`、`child_process` 这类远程读取或执行入口。

专项证据：

```text
/tmp/his_harness_v018_impl/self_check_report.md
/tmp/his_harness_v018_impl/task_manager/workspace_snapshot_history/task_workspace.html
/tmp/his_harness_v018_impl/task_manager/workspace_snapshot_history/task_workspace.json
/tmp/his_harness_v018_impl/task_manager/workspace_snapshot_history/task_workspace_snapshot_comparison.md
/tmp/his_harness_v018_impl/task_manager/workspace_snapshot_history/task_workspace_export_index.md
```

## v0.19 已完成内容

已接入：

- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- `TaskManager.build_task_workspace` 输出升级为 `0.19-task-workspace`。
- `write_workspace_outputs` 会把每次 workspace 导出归档到 `workspace_snapshots/<snapshot_id>/task_workspace.json`。
- 新增 `0.19-workspace-snapshot-history`，汇总多个历史快照，并预生成任意两个快照之间的摘要对比数据。
- 新增 `0.19-workspace-evidence-trend`，按任务汇总 UI 证据状态、warning、验证状态、确认卡状态、run 数和修改次数趋势。
- `task_workspace.html` 新增 `id="workspace-snapshot-history"` 和 `id="workspace-evidence-trend"` 两块只读区域，支持选择两个快照查看内嵌摘要对比。
- 新增导出文件：`task_workspace_snapshot_history.json/md`、`task_workspace_evidence_trend.json/md`，并在导出索引中列出 `workspace_snapshots/<snapshot_id>/task_workspace.json`。
- v0.19 仍然只读：不会重新验证产物、不会读取远端、不会自动打开业务页面、不会自动复跑命令、不会执行回滚、不会修改业务仓库、不会提交、不会推送、不会写云效。

最近验证：

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v019_impl
```

结果：通过。新增覆盖：

- workspace version 为 `0.19-task-workspace`。
- 导出索引 version 为 `0.19-workspace-export-index`。
- 多快照索引 version 为 `0.19-workspace-snapshot-history`，能保留至少 3 个本地快照并生成多组可选对比。
- 证据趋势 version 为 `0.19-workspace-evidence-trend`，能展示 DFHIS-31465 的 UI 证据从 present 到 missing、warning 数增加等趋势点。
- HTML 包含“多快照浏览”和“证据状态趋势”，并链接 `task_workspace_snapshot_history.json`、`task_workspace_evidence_trend.json`。
- HTML 不包含 `fetch(`、`XMLHttpRequest`、`exec(`、`child_process` 这类远程读取或执行入口。

专项证据：

```text
/tmp/his_harness_v019_impl/self_check_report.md
/tmp/his_harness_v019_impl/task_manager/workspace_snapshot_history/task_workspace.html
/tmp/his_harness_v019_impl/task_manager/workspace_snapshot_history/task_workspace.json
/tmp/his_harness_v019_impl/task_manager/workspace_snapshot_history/task_workspace_snapshot_history.md
/tmp/his_harness_v019_impl/task_manager/workspace_snapshot_history/task_workspace_evidence_trend.md
```

## v0.20 已完成内容

已接入：

- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- `TaskManager.build_task_workspace` 输出升级为 `0.20-task-workspace`。
- `task_workspace.json` 顶层新增 `navigation`，版本为 `0.20-workspace-navigation`，声明 overview、任务列表、任务详情、快照浏览、快照详情、证据趋势和导出索引等只读分区。
- `write_workspace_outputs` 新增 `snapshot_detail`，版本为 `0.20-workspace-snapshot-detail`，按历史快照汇总任务摘要、warning code、验证状态、UI 证据状态、确认卡状态和修改次数。
- `task_workspace.html` 新增 `id="workspace-nav"` 顶部导航、`id="workspace-overview"`、`id="workspace-tasks"`、`id="workspace-snapshot-detail-panel"` 和 `id="snapshot-detail-select"`。
- 证据预览从直接铺开内容改为 `evidence-preview-summary` 摘要加 `<details class="preview-item">` 可展开明细。
- v0.20 仍然只读：不会重新验证产物、不会读取远端、不会自动打开业务页面、不会自动复跑命令、不会执行回滚、不会修改业务仓库、不会提交、不会推送、不会写云效。

最近验证：

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v020_impl
```

结果：通过。新增覆盖：

- workspace version 为 `0.20-task-workspace`。
- navigation version 为 `0.20-workspace-navigation`，并包含固定只读分区。
- snapshot detail version 为 `0.20-workspace-snapshot-detail`，能展示至少 3 个历史快照，并包含 DFHIS-31465 任务摘要。
- HTML 包含顶部导航、概览分区、任务分区、快照详情选择器、证据预览摘要和可展开明细。
- HTML 不包含 `fetch(`、`XMLHttpRequest`、`exec(`、`child_process` 这类远程读取或执行入口。

专项证据：

```text
/tmp/his_harness_v020_impl/self_check_report.md
/tmp/his_harness_v020_impl/task_manager/workspace_snapshot_history/task_workspace.html
/tmp/his_harness_v020_impl/task_manager/workspace_snapshot_history/task_workspace.json
```

## v0.21 已完成内容

已接入：

- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- `TaskManager.build_task_workspace` 输出升级为 `0.21-task-workspace`。
- `task_workspace.json` 顶层新增 `ui_polish`，版本为 `0.21-workspace-ui-polish`，记录空态、错误态、只读边界和可读性增强点。
- `task_workspace.json` 顶层新增 `offline_review`，版本为 `0.21-workspace-offline-review`，汇总本地导出文件、审查步骤和只读边界。
- `write_workspace_outputs` 新增 `task_workspace_offline_review.json` 和 `task_workspace_offline_review.md`。
- `task_workspace.html` 新增 `id="workspace-offline-review"` 区域，展示离线审查包、空态说明、错误态说明和文件清单。
- 任务列表新增 `workspace-table-wrap` 横向滚动容器和 `status-pill` 状态标签，提升宽表和状态字段可读性。
- v0.21 仍然只读：不会重新验证产物、不会读取远端、不会自动打开业务页面、不会自动复跑命令、不会执行回滚、不会修改业务仓库、不会提交、不会推送、不会写云效。

最近验证：

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v021_impl
```

结果：通过。新增覆盖：

- workspace version 为 `0.21-task-workspace`。
- ui polish version 为 `0.21-workspace-ui-polish`，包含至少 4 个空态和 3 个错误态说明。
- offline review version 为 `0.21-workspace-offline-review`，文件数不少于 10。
- 导出 `task_workspace_offline_review.json/md`。
- HTML 包含离线审查包、空态说明、错误态说明、状态标签、大表滚动容器和离线审查包链接。
- HTML 不包含 `fetch(`、`XMLHttpRequest`、`exec(`、`child_process` 这类远程读取或执行入口。

专项证据：

```text
/tmp/his_harness_v021_impl/self_check_report.md
/tmp/his_harness_v021_impl/task_manager/workspace_snapshot_history/task_workspace.html
/tmp/his_harness_v021_impl/task_manager/workspace_snapshot_history/task_workspace.json
/tmp/his_harness_v021_impl/task_manager/workspace_snapshot_history/task_workspace_offline_review.md
```

## v0.22 已完成内容

已接入：

- `app/harness_config.py`
- `config/rule_packs/dfhis.default.json`
- `config/profiles.example.json`
- `tools/config_check.py`
- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 Rule Pack 规则包骨架，集中描述 Git 分支/提交规范、评论模板、状态流转规则、验证要求、高风险业务规则、分享边界和硬保护。
- 新增 Profile 示例，用于描述不同用户/项目使用哪套规则、需求来源 provider 和输出目录；profile 不保存真实 token。
- 新增 Credential Store 只读摘要，检测 env、本地凭证文件和可选 Keychain 的凭证存在状态，只输出来源和脱敏尾号，不输出完整 key。
- 新增 `tools/config_check.py`，可单独导出 `harness_config_summary.json/md`。
- `tools/task_manager.py workspace` 新增显式参数 `--include-config-summary`、`--rule-pack`、`--profile-config`、`--profile-key`、`--credentials-file`、`--check-keychain`。
- 显式传入配置摘要时，workspace 输出 `0.22-task-workspace`，新增“配置中心”只读区块和 `task_workspace_config_summary.json/md`。
- 不传新参数时，workspace 仍保持 `0.21-task-workspace`，旧命令默认行为不变。
- v0.22 仍然只读：不会保存真实 token、不会测试网络连通、不会读取远端需求、不会写云效/TAPD、不会 commit/push、不会状态流转、不会发布。

建议命令：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_config_check
```

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-summary \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured
```

新增自检覆盖：

- `rule_pack_profile_and_credentials_are_secret_free_and_compatible`
- `task_workspace_config_summary_is_explicit_readonly_and_legacy_default_unchanged`

## v0.23 已完成内容

已接入：

- `app/requirement_provider.py`
- `tools/requirement_provider_check.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `0.23-requirement-evidence` 只读证据结构，字段统一为 `source_type`、`source_url`、`external_id`、`title`、`description_text`、`comments`、`attachments`、`images`、`status`、`assignee`、`fetched_at`、`warnings`。
- Yunxiao 本地 payload 会优先取工作项业务状态，不用读取状态 `success` 覆盖需求状态。
- TAPD/manual/file 先支持本地 JSON/文本/手工参数归一化，真实 TAPD 网络读取后续分步接入。
- 输出 `requirement_evidence.json/md`，文本经过本地密钥脱敏；不会保存真实 token。
- v0.23 不改变 `harnesses/his_requirement_workflow.py --yunxiao-read`、Task Manager 默认 workspace、precommit verify 或云效事务边界。

建议命令：

```bash
python3 tools/requirement_provider_check.py \
  --source-type manual \
  --external-id MANUAL-1 \
  --title "手工需求" \
  --description "本地手工需求只读归一化。" \
  --output-dir /tmp/his_harness_requirement_provider_check
```

新增自检覆盖：

- `requirement_provider_normalizes_yunxiao_tapd_manual_file_readonly`
- `requirement_evidence_file_is_explicitly_integrated_into_workflow_and_workbench`

## v0.24 已完成内容

已接入：

- `app/harness.py`
- `harnesses/his_requirement_workflow.py`
- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 主 Workflow 新增显式 `--requirement-evidence-file` 参数；传入本地 v0.23 `requirement_evidence.json/md` 时，会把需求来源证据加入上下文并输出 `requirement_evidence.json/md`。
- Task Manager 的 `run` 支持同名参数；`register-run` 会索引已有 output_dir 中的 `requirement_evidence.json/md`。
- 单任务 workbench 增加“需求来源证据”摘要，展示来源类型、外部 ID、需求状态、负责人、附件/图片/评论数量、warning 和原始文件路径。
- 只读 HTML workspace 增加“需求来源证据”列、详情 tab、证据预览、搜索文本和状态筛选；页面仍只切换本地 HTML 内嵌数据，不联网、不执行命令。
- 默认 demo 和旧 workflow 不传 `--requirement-evidence-file` 时，不会生成 `requirement_evidence.*`，不改变旧输出行为。

建议命令：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand "手工需求正文" \
  --title "需求来源证据接入样例" \
  --mode mock \
  --execution-mode readonly \
  --requirement-evidence-file /tmp/his_harness_requirement_provider_check/requirement_evidence.json \
  --output-dir /tmp/his_harness_requirement_evidence_workflow
```

最近验证：

```bash
python3 -m py_compile run.py app/*.py harnesses/his_requirement_workflow.py tools/self_check.py tools/yunxiao_read_check.py tools/precommit_verify.py tools/cleanup_worktrees.py tools/task_manager.py tools/config_check.py tools/requirement_provider_check.py
```

结果：通过。

```bash
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v024_final_after_fix
```

结果：通过。mock 只代表流程技术自检通过，不代表真实业务验收通过。

## v0.25 已完成内容

已接入：

- `app/harness_config.py`
- `tools/config_check.py`
- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `0.25-configuration-preview`，把 Rule Pack、Profile、Credential Store 摘要转换成可分享的只读配置预览。
- Provider 模板覆盖 Yunxiao、TAPD、manual、file，并预留 Jira/GitHub Issue 等扩展来源；模板只展示 credential key、状态和用途，不展示真实 token。
- 规则预览集中展示 Git/提交规范、需求评论模板、状态流转、验证要求、风险规则和团队分享边界。
- `tools/config_check.py --include-preview` 会额外导出 `harness_config_preview.json/md`。
- `tools/task_manager.py workspace --include-config-preview` 会显式输出 `0.25-task-workspace`，新增“配置预览”只读区块和 `task_workspace_config_preview.json/md`。
- 不传 `--include-config-preview` 时，旧 workspace 仍保持 `0.21-task-workspace`；只传 `--include-config-summary` 时仍保持 v0.22 配置摘要行为。
- v0.25 不联网、不测试 provider 连通性、不保存真实 token、不读取远端需求、不写云效/TAPD、不 commit/push、不执行状态流转、不执行回滚或发布。

建议命令：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-preview \
  --output-dir /tmp/his_harness_config_preview
```

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-summary \
  --include-config-preview \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_preview
```

新增自检覆盖：

- `configuration_preview_templates_are_readonly_shareable_and_explicit`

## v0.26 已完成内容

已接入：

- `app/harness_config.py`
- `tools/config_check.py`
- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `0.26-configuration-share-validation`，检查团队共享 Rule Pack/Profile 模板是否保持只读边界。
- 分享校验覆盖外部写入默认关闭、真实状态流转关闭、Git 自动动作关闭、禁止导出凭证、明显真实密钥字段和个人绝对路径 warning。
- 新增 `0.26-local-override-strategy`，说明当前安全覆盖顺序：显式 CLI 参数优先，`~/.his-harness/profiles.json` 和本机 rule pack 作为建议手工传参路径，不自动写入或自动应用。
- `tools/config_check.py --include-share-validation` 会额外导出 `harness_config_share_validation.json/md`；`--strict` 会把分享校验 error 作为失败。
- `tools/task_manager.py workspace --include-config-share-validation` 会显式输出 `0.26-task-workspace`，并自动包含配置摘要、配置预览和“配置分享校验”区块。
- 不传 `--include-config-share-validation` 时，旧 workspace 仍保持 `0.21-task-workspace`；只传 v0.22/v0.25 参数时仍保持对应显式行为。
- v0.26 不联网、不测试 provider 连通性、不保存真实 token、不读取远端需求、不写入 `~/.his-harness`、不应用配置、不写云效/TAPD、不 commit/push、不执行状态流转、不执行回滚或发布。

建议命令：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-share-validation \
  --output-dir /tmp/his_harness_config_share_validation
```

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-share-validation \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_share
```

新增自检覆盖：

- `configuration_share_validation_blocks_secrets_and_documents_local_override_strategy`

## v0.27 已完成内容

已接入：

- `app/harness_config.py`
- `tools/config_check.py`
- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `0.27-configuration-import-draft`，基于当前配置摘要生成 secret-free 导入草案。
- `tools/config_check.py --include-import-draft --draft-output-dir <dir>` 会在用户选择目录生成 `profiles.draft.json`、`rule_pack.draft.json`、`credentials.example.json`、`IMPORT_GUIDE.md` 和 `config_import_manifest.json`。
- 默认不覆盖同名草案文件；重复生成会返回 `blocked_existing_files`，需要覆盖时必须显式传 `--overwrite-drafts`。
- `tools/task_manager.py workspace --include-config-import-draft --draft-output-dir <dir>` 会显式输出 `0.27-task-workspace`，并自动包含配置摘要、配置预览、配置分享校验和“配置导入草案”区块。
- 导入草案只写入用户选择目录，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不会读取或写入云效/TAPD。

建议命令：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-import-draft \
  --draft-output-dir /tmp/his_harness_config_import_drafts \
  --output-dir /tmp/his_harness_config_import_draft
```

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-import-draft \
  --draft-output-dir /tmp/his_harness_workspace_import_drafts \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_import
```

新增自检覆盖：

- `configuration_import_draft_generates_user_selected_secret_free_files`

## v0.28 已完成内容

已接入：

- `app/harness_config.py`
- `tools/config_check.py`
- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `0.28-configuration-import-review`，回读用户选择目录中的 `profiles.draft.json`、`rule_pack.draft.json`、`credentials.example.json`、`IMPORT_GUIDE.md` 和 `config_import_manifest.json`。
- 校验 JSON 结构、明显密钥泄漏、占位路径、个人路径、硬保护开关、Git 自动动作、真实状态流转开关和 manifest 只读边界。
- `tools/config_check.py --review-import-draft --draft-input-dir <dir>` 会输出 `harness_config_import_review.json/md`。
- `tools/task_manager.py workspace --include-config-import-review --draft-input-dir <dir>` 会显式输出 `0.28-task-workspace`，并自动包含配置摘要、配置预览、配置分享校验和“配置导入回读校验”区块。
- WebUI 展示只读表单预览、导入前风险提示和人工确认项；不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不会读取或写入云效/TAPD。

建议命令：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --review-import-draft \
  --draft-input-dir /tmp/his_harness_config_import_drafts \
  --output-dir /tmp/his_harness_config_import_review
```

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-import-review \
  --draft-input-dir /tmp/his_harness_config_import_drafts \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_import_review
```

新增自检覆盖：

- `configuration_import_review_reads_back_drafts_and_shows_readonly_form_preview`

## v0.29 已完成内容

已接入：

- `app/harness_config.py`
- `tools/config_check.py`
- `app/task_manager.py`
- `tools/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `0.30-configuration-wizard`，可把 v0.22-v0.29 的配置摘要、Provider 模板、分享校验、生成草案、回读校验、模板索引和人工复制前确认串成一个只读向导。
- 生成步骤状态：选择来源、Provider 模板、分享校验、生成草案、回读校验、对比模板、人工复制前确认。
- 生成阻断摘要、人工确认清单和复制命令，便于用户把向导结果发给其他人使用或自己复跑。
- `tools/config_check.py --include-config-wizard --draft-input-dir <dir> [--compare-draft-input-dir <dir>]` 会输出 `harness_config_wizard.json/md`。
- `tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 会显式输出 `0.30-task-workspace`，并自动包含配置摘要、配置预览、配置分享校验、配置导入回读校验、配置模板索引和“配置向导”区块。
- 该能力只读，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不会读取或写入云效/TAPD。

建议命令：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-config-wizard \
  --draft-input-dir /tmp/his_harness_config_import_drafts \
  --compare-draft-input-dir /tmp/his_harness_config_import_drafts_compare \
  --output-dir /tmp/his_harness_config_wizard
```

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-wizard \
  --draft-input-dir /tmp/his_harness_config_import_drafts \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_wizard
```

新增自检覆盖：

- `configuration_wizard_combines_config_flow_into_readonly_guide`

## v0.31 已完成内容

修改文件：

- `app/harness_config.py`
- `app/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `0.31-configuration-wizard-readability`，在配置向导 JSON 中显式输出 `ui_readability`。
- 配置向导步骤增加 `search_text`，只读 HTML 可以按步骤文本、状态和是否阻断进行本地筛选。
- 工作台配置向导区块新增“阻断摘要”，展示总步骤、阻断步骤、人工确认步骤和命令数量。
- 复制命令增加稳定 `copy_target_id`，HTML 中提供“命令复制”按钮和复制失败时的只读提示。
- 空结果提示明确说明筛选无结果或无阻断项时的状态，避免误判为配置已经应用。
- `tools/config_check.py --include-config-wizard --draft-input-dir <dir>` 输出 `0.31-configuration-wizard` 和 `0.31-configuration-wizard-readability`。
- `tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 显式输出 `0.31-task-workspace`，并保留默认旧 workspace 的 `0.21-task-workspace` 行为。
- 该能力只读，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不会读取或写入云效/TAPD。

新增自检覆盖：

- `configuration_wizard_combines_config_flow_into_readonly_guide` 校验 v0.31 版本号、`ui_readability`、步骤筛选、阻断摘要、命令复制目标和 HTML 交互标记。

## v0.32 已完成内容

修改文件：

- `app/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 新增 `0.32-configuration-review-package-index`，只在显式 `--include-config-wizard` 的 workspace 中生成。
- `task_workspace.json` 在配置向导路径下升级为 `0.32-task-workspace`，导航升级为 `0.32-workspace-navigation`。
- `write_workspace_outputs` 新增 `task_workspace_config_review_package.json/md`，并把它纳入导出索引和 HTML 底部链接。
- 本地 HTML 新增“配置审查包”区块，展示配置产物清单、复跑命令、人工确认项、审查步骤和只读边界。
- 审查包索引只汇总本地已生成或计划生成的文件、命令文本和人工确认项；不会执行命令、不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不会读取或写入云效/TAPD。
- 默认 workspace 不传 `--include-config-wizard` 时仍保持 `0.21-task-workspace`，不包含 `config_review_package_index`。

新增自检覆盖：

- `configuration_review_package_index_collects_wizard_outputs_readonly` 校验 v0.32 版本号、只读边界、文件清单、复跑命令、人工确认项、HTML 区块和默认兼容路径。

## v0.33 已完成内容

修改文件：

- `app/task_manager.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`
- `$his-harness` skill

新增/修正能力：

- 配置审查包索引升级为 `0.33-configuration-review-package-index`，workspace 升级为 `0.33-task-workspace`，导航升级为 `0.33-workspace-navigation`。
- 新增 `0.33-configuration-review-package-readability`，包含文件状态筛选项、待确认分组、未确认必填项统计和交接摘要。
- 本地 HTML “配置审查包”区块新增文件搜索、文件状态筛选、空结果提示、交接摘要和待确认分组。
- Markdown 审查包新增“交接摘要”和“待确认分组”，方便发给其他人只读审查。
- 默认 workspace 不传 `--include-config-wizard` 时仍保持 `0.21-task-workspace`，不包含 `config_review_package_index`。
- 该能力仍只读，不执行命令、不应用配置、不写入 `~/.his-harness`、不保存真实 token、不测试远端账号、不读取或写入云效/TAPD。

新增自检覆盖：

- `configuration_review_package_readability_groups_handoff_summary` 校验 v0.33 版本号、文件筛选、待确认分组、交接摘要、HTML 交互标记和默认兼容路径。

## v0.34 已完成内容

新增文件：

- `app/config_resolver.py`
- `app/config_compat.py`
- `config/schemas/harness_config_layer.v1.json`
- `config/schemas/resolved_config.v1.json`
- `tests/test_config_resolver.py`
- `tests/test_config_compat.py`
- `tests/test_config_check_cli.py`

修改文件：

- `tools/config_check.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`

新增/修正能力：

- 普通配置按 `builtin_defaults < team_package < project_config < personal_override < run_override` 合并，支持 `replace/merge/append/union/remove/locked` 六种策略。
- `system_hard_guards` 独立于普通优先级，任何配置层都不能覆盖；敏感值只接受 `env:`、`keychain:`、`file:` 引用。
- `ResolvedConfig` 是带来源追踪、校验结果和稳定 SHA-256 的不可变只读快照。
- `tools/config_check.py` 新增显式参数 `--include-resolved-config`、`--team-config`、`--project-config`、`--personal-config`、`--run-override-json`。
- 只有同时显式传入 `--include-resolved-config` 和 `--output-dir` 才生成 `harness_resolved_config.json/md`；不传开关仍保持 v0.33 兼容输出。
- v0.34 不应用配置、不读取默认个人覆盖目录、不测试远端账号、不修改业务代码、不执行 Git、云效或 TAPD 写入。

验证命令：

```bash
python3 -m unittest discover -s tests -p 'test_config*.py' -v
python3 -m py_compile app/config_resolver.py app/config_compat.py tools/config_check.py tools/self_check.py
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v034_self_check
```

## v0.35 核心需求闭环已完成内容

新增文件：

- `app/core_closure.py`
- `tests/test_core_closure.py`
- `tests/test_core_closure_cli.py`
- `docs/superpowers/specs/2026-07-11-harness-core-closure-design.md`
- `docs/superpowers/plans/2026-07-11-harness-v0.35-core-closure.md`

修改文件：

- `app/harness.py`
- `app/worktree_executor.py`
- `harnesses/his_requirement_workflow.py`
- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`

新增/修正能力：

- `core-closure-trial` 不再执行旧的固定九角色报告链，而是按 RequirementContract -> EngineeringHandoff -> worktree -> 独立 DiffReview 执行。
- 契约会阻断：需求校准未 ready、高风险 HIS 关键词、技术决策未允许 patch、缺少工程证据/白名单/专项验证、缺少可验证业务规则或默认行为、来源冲突未解决、缺少自动验收项。
- 以 `paiBanMs` 为基准样板：`1` 仅医生为空、`2` 仅有医生、空/不传/其他值保持默认模式；独立审查要求 diff 同时保留两条分支和默认保护。
- 默认 `apply_to_project=True`；只有所有闸门、专项验证和独立 diff 审查通过后，才允许本地合入。传 `--review-only` 可显式保留 review-only 模式。两种模式都不提交、不推送、不发布、不写云效/TAPD。
- `extract_unified_diff` 会规范保留 patch 末尾换行，避免合法 diff 因文本清洗而被 `git apply --check` 误判为 `corrupt patch`。
- fixture worktree 回放、阻断场景、独立 diff 审查、CLI 参数、旧 readonly 九步骤兼容路径均有自动测试；fixture 只证明 Harness 工程链路，不是 DFHIS 页面运行时验收。

验证命令：

```bash
python3 -m unittest tests.test_core_closure tests.test_core_closure_cli -v
python3 -m unittest discover -s tests -p 'test_config*.py' -v
python3 -m py_compile app/core_closure.py app/harness.py app/worktree_executor.py harnesses/his_requirement_workflow.py tools/self_check.py
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v035_core_closure_self_check
```

## v0.36 真实低风险需求回放

- 样本：DFHIS-31557《【运城口腔】挂号处理界面证件类型需要默认成身份证。》。云效仅作为只读需求证据，不写评论、不改状态。
- 真实项目：`df-web-bui`；受控白名单仅为 `src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js`。原项目存在的无关用户改动被记录为前置环境信息，未被回退或纳入 patch。
- 业务规则：挂号缩减版新建/清屏时与档案管理共用证件类型、婚姻、年龄单位默认参数；已有病人、读卡结果和用户已选择值不被默认值覆盖。证件类型按参数优先，无配置时回退 `1`；年龄单位取参数值 `|` 前的首段。
- Harness 补强：支持显式 `harness-rules` 默认值规则、仅在目标文件存在时将调用方白名单作为工程证据、允许原项目存在不相交脏文件时创建 review-only worktree、向受控 patch 提供白名单源码上下文，并以 `git apply --recount` 处理模型生成 diff 的 hunk 计数偏差。Task Manager 已能以 `core-closure-trial` 登记真实回放、识别独立 diff 审查通过状态并记录修改历史/只读回滚计划。
- 验证：Run 899 的 `git apply --check --recount`、`git diff --check`、专项静态校验、`node --check` 和独立 diff 审查均通过；临时 worktree 已清理。
- 边界：这证明真实源码环境中的本地研发链路，不替代页面登录态、参数配置、接口返回和人工业务验收。仅当所有本地闸门通过且目标白名单路径无无关本地改动时，Harness 才会写入原业务目录；传 `--review-only` 可禁止写入。

Harness 自检：

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile app/requirement_calibration.py app/core_closure.py app/technical_decision.py app/harness.py app/worktree_executor.py harnesses/his_requirement_workflow.py
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v036_self_check
```

## DFHIS-31446 复盘规则

问题：三方支付超时提示弹框点右上角关闭后，后面的结算进度详情弹框没有关闭。

真实业务目标应固化为行为断言：

- 必须发生：点三方支付失败提示的 `X` 后，继续自动退费并关闭结算进度详情。
- 禁止发生：不得再出现第二个提示、空提示、重复提示或被替换成“收费结算失败”这类泛化文案。
- 必须保持：原本点“确定”的流程、真实失败原因、loading/进度条关闭顺序、退费/结算状态边界不变。

关键经验：

- Element UI `$alert` / `$confirm` 的关闭、取消可能走 `reject`。
- 关闭提示不是业务失败，不能让它落入外层业务失败 `catch`。
- 不能为了避免空提示写死“收费结算失败”，否则会覆盖已有具体原因。
- 修复此类问题必须保护旧流程，而不是新增一个兜底提示。

## 当前冻结项

以下能力暂时不要默认开放：

- 云效真实状态流转。
- 云效真实负责人流转。
- 云效真实迭代调整。
- 云效真实关闭任务。
- 云效真实附件上传。
- 自动发布。
- 无证据自动关闭需求或缺陷。

云效读取默认使用本地凭证 key：

- `aliyun_devops_pat`
- `aliyun_devops_organization_id`

默认凭证文件：

```text
/Users/lym/WorkCode/ai/apiKey/credentials.json
```

不要在报告、日志、回复或记忆中输出 token 原文。

## 当前分支/提交规则

普通需求默认只完成需求分析、worktree 改动、专项验证和本地原仓库应用，不自动
创建分支、commit、push 或集成 RC。完整 Git 交付必须先创建 Delivery Closure
事务，并且远端动作必须写入不可变计划。

- 开发和第一次本地测试分支：`release_2.15.3_250515`
- 需求分支：`feature-DFHIS-<id>`
- 缺陷分支：`hotfix-DFHIS-<id>`
- 需求提交：`feat: <id>-<yunxiao-link> 《<title>》`
- 缺陷提交：`fix: <id>-<yunxiao-link> 《<title>》`
- 集成分支：`RC_2.16.1_250514`
- 集成方式：按计划顺序 `cherry-pick` 精确任务 commit，不 merge 任务分支。

用户在 release 原源码工作区测试通过并登记验收后，第一次确认允许在原源码目录
创建任务分支和 commit；只有计划显式包含时，才继续推送任务分支和同步本地 RC。
第一次确认绝不推送 RC。RC parity 和第二次页面测试通过后，第二次确认才允许按
计划推送 RC。

源码目录存在其他可分离修改时使用 Harness Safety Shelf 保存并恢复，不使用匿名
stash，不暂存后切换基线，不提交 worktree 内的临时分支。出现同文件归属不清、
远端分叉、RC 非快进、无法证明的恢复或高风险冲突时停止；可证明的 cherry-pick
冲突至少必须恢复到集成前 HEAD，当前实现默认 abort 并交由人工判断。

不要使用破坏性命令丢弃用户改动，不 force push，不推送 release 分支。云效评论、
附件、状态、负责人和迭代写入仍是独立事务，不能由 Git 交付确认自动授权。

## 下一步建议

v0.65 离线内部改造已经闭环，下一步不再继续追加核心代码或优先扩展 Web UI，
而是选择一个真实、低风险、已在 release 原源码目录完成本地改动的需求，演练一次
完整 Delivery Closure。演练开始前需要用户提供需求/缺陷编号、标题、链接和本地
项目位置；Harness 负责核对 patch、允许路径、专项验证和不可变计划。

用户只需要完成两个运行时动作：

1. 在 `release_2.15.3_250515` 原源码工作区验证当前需求并确认结果。
2. Harness 按已确认计划完成任务分支、commit 和本地 RC 集成后，在
   `RC_2.16.1_250514` 再验证一次并确认结果。

任务分支 push 和 RC push 仍必须分别进入不可变计划并取得对应阶段确认；未计划、
未确认时不会执行。真实低风险演练开始前，不触碰业务仓库或真实远端。云效/TAPD
评论、附件、状态、负责人和迭代写入，业务 PG 自动查询、真实模型、部署和发布继续
冻结，不能借 Git 交付确认隐式开放。

## 新需求处理默认流程

1. 读取云效需求或用户描述。
2. 清洗正文、评论、图片和附件证据；用户明确要求“不看评论”时使用 `--yunxiao-ignore-comments`，不请求评论接口。
3. 搜索代码上下文，先定位真实流程，再决定前端/后端/BFF/数据库影响。
4. 生成验收矩阵和行为断言。
5. 明确白名单文件。
6. 在临时 worktree 中试错。
7. 跑 `git diff --check`、专项 lint/compile、行为门禁。
8. 如果是交互类 bug，继续跑方法级或 UI 证据验证。
9. 验证通过后再合入本地原业务目录。
10. 需要提交时按用户分支/commit 规则执行。
11. 云效评论和状态流转不默认执行。

## 高风险业务默认规则

涉及以下场景时默认保守：

- 收费
- 结算
- 医保
- 退费
- 报表
- 对账
- 政策校验
- 金额计算
- 状态流转

原则：

- 不新增业务规则。
- 不替换真实失败原因。
- 不覆盖原错误提示。
- 不改变已有结算/退费/医保状态边界。
- 没有证据时输出“证据不足”，不要猜。
- 需要人工确认时明确列出确认项。

## Skill 更新状态

本次更新了 `README.md`、`HANDOFF.md`、`app/core_closure.py`、`app/harness.py`、`app/worktree_executor.py`、`harnesses/his_requirement_workflow.py`、`tools/self_check.py`、核心闭环测试和 `$his-harness` skill。

`$his-harness` skill 已包含 Task Manager 的登记、复跑、修改历史和只读工作台能力，需求理解确认卡、需求来源证据、只读配置解析，以及 v0.36 已经真实回放的核心闭环边界：低风险需求的结构化契约、工程交接、受控 worktree、专项验证和独立 diff 审查。该回放不会因此开放配置写入、远端账号测试或任何云效/TAPD 写动作。

## HarnessManager v0.65 核心兼容工作台

个人本地 Web UI 继续使用独立目录：

```text
/Users/lym/WorkCode/ai/HarnessManager
```

v0.65 已完成：

- 新增只读核心适配器，展示 Manager/Core 版本、核心文件、SQLite 健康、运行策略和真实性边界。
- 任务详情接入 Task Manager 的修改历史与回滚可用性，只读展示，不在浏览器执行真实回滚。
- 移除真实模型选择和 Claude 配置开关；API 在创建 Job 前硬阻断真实模型模式，默认固定 `mock`。
- HTTP 500 对浏览器隐藏 traceback；详细异常只保留在本地服务日志。
- 离线运行、任务详情、向导、run/Job、产物、修改历史和系统状态共用现有 Harness 核心，不复制核心业务规则。
- 窄屏使用两列状态指标和单行横向滚动标签，长标题和长路径不得造成页面级横向溢出。

v0.65 验证证据：

- HarnessManager 单元和 HTTP 集成测试 12 项通过。
- Python 编译、JavaScript 语法和界面敏感配置扫描通过。
- Harness 核心离线企业门禁单次迭代通过。
- 浏览器已覆盖 1440x900、390x844、任务切换、超长文本、修改历史空态和系统状态；未发现控制台错误。

继续冻结：

- 真实模型和真实模型 DAG。
- 凭证配置页面与凭证读取。
- 浏览器中的真实回滚、自动清理、自动提交推送、云效/TAPD 写入和部署。
- 业务 PG 自动查询；仍只在用户明确要求时使用显式只读查询。

本阶段只证明个人本地技术工作台与核心兼容，不把 `technical_valid=true` 解释为某个真实需求已经业务验收或可以提交发布。

## HarnessManager v0.66 证据中心

独立 Web UI 已在 `/Users/lym/WorkCode/ai/HarnessManager` 增加 v0.66 本地证据中心：

- `requirement_evidence.json` 的评论、图片和附件固定归为“需求来源证据”。
- `ui_evidence_manifest.json`、`ui_evidence_runner.json` 的截图、视频、断言和说明固定归为“自测验证证据”。
- 未进入 UI 清单但存在于已登记运行目录的图片仅标记为“运行产物推断”，不冒充自动化采集结果。
- 同一云效图片同时出现在 attachment/image 清单时去重；每项保留来源类型、依据字段、task run、run id 和时间。
- 新增 token 化本地图片接口，不向浏览器公开媒体绝对路径；图片读取再次校验登记根目录、扩展名、MIME 和大小。
- Web UI 支持全部/需求来源/自测验证筛选、需求评论、自测断言、截图图库、缺失态和大图预览。

v0.66 仍不自动访问云效/TAPD，不调用真实模型，不读取模型凭证，不上传附件，不写评论、不流转状态。证据中心消费 readonly 或验证流程已写入本地 run/output 目录的证据；视频本阶段只展示登记信息，不在线播放。

v0.66 验证证据：HarnessManager 17 项单元/HTTP 集成测试、Python 编译和 JavaScript 语法检查通过；隔离 fixture 浏览器覆盖 1440x900、390x844、分类筛选、需求/自测图片预览、媒体缺失态、移动端当前标签自动露出和页面级溢出检查，控制台无错误。Harness 核心单轮离线企业门禁通过，包含 272 项核心单测、mock self-check、10 条 replay 和 81 文件密钥扫描，结果哈希 `b5a02b672686ca5e80b3b04387a6979d15be87aae2e4503e6f681cd0e1a187fc`。全过程未读取凭证、未调用模型/网络、未访问业务 PG、未写云效或 Git 远端。

## HarnessManager v0.67 Core/Plugin Compatibility

Stage 1 将独立 Manager 与 Core v0.66 的冻结插件清单、`enforce` 路由和能力状态以只读方式对齐。历史 `GET /api/system` 保持兼容，并新增 `GET /api/v1/system/health`、`GET /api/v1/plugins` 和 `GET /api/v1/capabilities`；不新增任何 mutation 路由。

```text
Core/plugin compatibility verified = true
business_valid = false
runtime_verified = false
promotion_enabled = false
external_writes_default = false
```

此处的 compatibility verified 仅表示受控源中的本地技术合同，不表示真实 HIS 业务、真实运行时或发布晋升已验收。Stage 1 不读取、保存、编辑或维护任何凭证；云效、GitLab、数据库凭证维护及连接测试/运行时一致性仍是已批准的 Stage 3 需求。

2026-08-02 受控源验收结果：HarnessManager 26 项测试、installer 101 项测试、Core/plugin targeted 36 项测试均通过；Core 完整离线回归 693 项通过（skipped=6），企业门禁 `passed`，结果哈希 `d02e57abb75138cffc3d57bf53e6407c0b3b9008a9b0b91afefeb7c4ccf94dbf`。正式 Manager 安装计划只含 manifest 的 14 个文件，计划哈希为 `f72d996d27c821e9766d848be9d0ac1e38ea10cc1df07fe50ec4acff84480429`。

正式安装已按该计划哈希执行，但 installed Manager 测试在导入 `app.core_status` 时失败：正式 `/Users/lym/WorkCode/ai/Harness` 缺少该 Stage 1 Core 文件，故未启动服务、未执行 API/browser smoke，也没有宣称正式运行时验收。已使用同一 plan hash 与 receipt hash `54b9d7ec32b1228289fc338f475c3b5834e598f5bee1fc989352e72009527f5a` 进行 guarded restore；安装 receipt 为 `/Users/lym/WorkCode/ai/.harness-install-backups/HarnessManager/attempt-b7mnyo0p/receipt.json`，backup 为 `/Users/lym/WorkCode/ai/.harness-install-backups/HarnessManager/attempt-b7mnyo0p`，restore receipt 为 `/Users/lym/WorkCode/ai/.harness-install-backups/HarnessManager/attempt-b7mnyo0p/restore-receipt.json`。恢复后 14/14 受管文件均回到安装前哈希，两个创建项已移除，无 pending artifact。正式 Core 不是 Git 工作树，无法用 Git status 判定其用户改动；除 `app/core_status.py` 缺失外，本次比对的 capability/registry/inventory 文件与受控源一致。SQLite 仅记录了安装前主/WAL/SHM stat；未启动 API，因此未读取或写入数据库以完成 post-smoke 对比。

随后为 Core 新建严格 3 文件安装计划（`app/core_status.py`、`install_manifest.json`、`tests/test_core_status.py`），计划哈希 `50289655d80fa04a477b2dc4f6a67e1de37bc69ff613609f38fa3f2a76333195`，三项均为 create，apply/verify 均成功。但正式 `tests.test_core_status` 发现现有 `app.database` 缺少 `database_read_only_health_snapshot`，该函数是新 `core_status.py` 的必要只读依赖；没有擅自扩大 manifest。Core 已使用 receipt hash `bb1781da64fc929451846c19fa2750e8a11e7c001d45f991852e0d5a2a014c79` guarded restore，路径为 `/Users/lym/WorkCode/ai/.harness-install-backups/Harness/attempt-pp_6bow_/receipt.json`、`/Users/lym/WorkCode/ai/.harness-install-backups/Harness/attempt-pp_6bow_` 和 `/Users/lym/WorkCode/ai/.harness-install-backups/Harness/attempt-pp_6bow_/restore-receipt.json`；恢复后 3/3 create 文件均不存在，无 pending artifact。正式 Manager 未再安装。

获准的修订 Core plan 严格限于 4 项：create `app/core_status.py`、replace `app/database.py`、create `install_manifest.json`、create `tests/test_core_status.py`；计划哈希 `4a6b95b3ffd8048a74f88a6e268ca89bf597baa1f1d17b3162df510fb1b13521`，apply/verify 4/4 成功。正式 `tests.test_database_governance` 的 8 项通过，但 `tests.test_core_status` 有 2 项断言失败：预期 `plugin_inventory_invalid`/`ready`，实际为 `capability_registry_invalid`/`blocked`。因此没有继续排查、没有启动服务/API/browser，也没有重新安装 Manager。安装 receipt 为 `/Users/lym/WorkCode/ai/.harness-install-backups/Harness/attempt-f9hrxwq1/receipt.json`，receipt hash `68fae8bf512635f1473c2bf562a9aae1c24209c330637aaa18b828e38fce734e`；已按同一 plan/receipt guarded restore，生成 `/Users/lym/WorkCode/ai/.harness-install-backups/Harness/attempt-f9hrxwq1/restore-receipt.json`。恢复后 3 个 create 文件均不存在，`app/database.py` 已还原为安装前 SHA-256 `3b62445c0b6c539c7ad19cc4b6cfcb4c551a2278f6489c30dc4968c21c639a59`，无 pending artifact。首次恢复尝试因摘要文件哈希误作 receipt hash 被 CLI 拒绝，未发生写入；随后以实际 receipt hash 恢复成功。

2026-08-03 的修订 4 文件 Core plan（计划哈希 `180a1379e952e793f4be4243fdd565652f5f0870e89c3efc7d8a3ca0bb230199`）以最终保守 DB 合同重新 apply/verify 4/4；随后的 14 文件 Manager plan（`7e768df25cb23cb6eed501de50e36e58d65ae03261eb4018e872cd4a72eb2cd3`）也 apply/verify 14/14。正式 Core targeted 22 项、正式 Manager 26 项通过；worktree 的 DB/status 14 项在 `ResourceWarning` 作为错误时通过，涵盖临时未 checkpoint WAL 的精确 stat 不变、dangling sidecar、普通文件 immutable URI 与 no-connect 边界。该 Core 合同会先以 `lstat` 检查 `-wal`/`-shm`：任一 lexical entry（含 dangling symlink/special）存在即返回 `unknown/metadata_only/not_run`，绝不打开 SQLite；仅无 sidecar 的 no-follow regular main file 才使用 `mode=ro&immutable=1`，结果明确标记 `main_file_only` 和 `checkpointed_snapshot`，不把它解释为当前 WAL 新鲜度。

随后一次正式 API 与桌面 UI smoke 的 `/api/health`、`/api/v1/system/health`、`/api/v1/plugins` 均返回预期的 Core `ready`、`enforce`、四个冻结插件，以及 DB `unknown/metadata_only/not_run/wal_sidecars_present`。但 smoke 前后对真实主库/WAL/SHM 的 `lstat` 显示主库和三个 inode/size 不变、WAL/SHM 的 `mtime_ns`/`ctime_ns` 改变，故该轮零副作用运行时证据**未成立**；没有清理、checkpoint、恢复或写入真实 DB sidecar。根因边界已定位：Core status probe 本身遵守上述 no-connect 合同，但旧 UI 首屏 `static/app.js` 的 `loadTasks()` 自动请求 `/api/tasks?limit=100`，而 Manager `server.py` 的该路由调用 `TaskManager().list_tasks()`，后者走普通 Harness SQLite 连接。这是 Stage 1 系统状态验收与旧任务列表接口之间的架构冲突，不是对 Core metadata-only probe 的反证。服务已停止，随后严格按 receipt 逆序 guarded restore：Manager 14/14 文件回到 preinstall 哈希、两个 create 文件不存在；Core 4/4 文件回到 preinstall 哈希、三个 create 文件不存在。移动尺寸 smoke 在该不一致被发现后未继续执行。

已采用显式 opt-in read-only smoke mode 处理该冲突：启动 Manager 时设置 `HARNESS_MANAGER_READ_ONLY_SMOKE=1`，`/api/health` 会返回 `read_only_smoke_mode=true`；前端首屏不自动加载 `/api/tasks`、任务详情、wizard、run 或 `/api/jobs`，并禁用新增任务和启动运行入口；后端在该模式下以 403 阻断 legacy task/job 路由，防止遗漏调用触碰 Harness SQLite。该模式仅用于正式 Stage 1 验收，不改变日常 Manager 任务列表和本地离线流程。

2026-08-03 该 opt-in 模式的正式 API 与桌面 UI smoke 已重新执行：`/api/health` 返回 `read_only_smoke_mode=true`、Manager `0.67.0`、Core `0.66.0` 和 `enforce`；`/api/v1/system/health` 返回 Core `ready`、DB `unknown/metadata_only/not_run/wal_sidecars_present`；`/api/v1/plugins` 返回四个冻结插件；`/api/tasks` 在该模式下返回 403。桌面 UI 1440x900 显示只读验收模式、Manager/Core 版本和插件信息，新增任务/运行按钮均禁用，控制台 error/warn 为 0。正式服务日志显示 UI 首屏只请求 `/api/health` 和 `/api/v1/system/health`，没有请求 `/api/tasks` 或 `/api/jobs`。真实 `/Users/lym/WorkCode/ai/Harness/data/harness.sqlite`、`harness.sqlite-shm`、`harness.sqlite-wal` 在 API + 桌面 smoke 前后的 `ctime_ns`、`mtime_ns`、inode 和 size 完全一致，零副作用证据成立。移动端真实视口 smoke 因当前 in-app browser 句柄不暴露 viewport/goto 能力、本地也无 Playwright 依赖，未宣称通过。

## Harness 核心 v0.65 Git 交付闭环

v0.65 已完成原业务源码目录内的受控 Git 交付，入口为
`tools/delivery.py`，核心状态机为 `app/delivery_closure.py`。

已支持：

- 绑定 release HEAD、patch hash、目标文件 hash 和 Rule Pack 快照的不可变交付计划。
- 计划文件被改写、配置在事务中途变化或验收后源码漂移时立即阻断。
- release 和 RC 两段真实运行时验收；用户不需要记忆 plan hash。
- 第一次确认创建任务分支、精确暂存白名单路径并 commit；按计划可继续推送任务分支和本地 RC cherry-pick，但绝不推送 RC。
- staged、unstaged、未跟踪文件混合状态的 Safety Shelf 保存、恢复和 SHA-256 复核。
- RC 只允许等于远端或从远端 fast-forward，同名远端任务分支只允许创建、相同或可证明 fast-forward；禁止 force push。
- RC cherry-pick 冲突自动 abort，验证失败回到集成前 HEAD；不使用模型猜测冲突业务语义。
- patch-id 等价后继续检查 RC 当前最终状态；多 commit 仅部分等价时阻断，避免把已回退改动或半套改动判为已集成。
- 策略、动作、验证命令、路径和 patch 共同构成交付事务键；确认事件绑定当前计划并持久化，旧计划确认不能授权新计划。
- RC 成功检查点先写 repository-local journal，再更新数据库；中断可按 parity hash 和 RC HEAD 协调，失败恢复也同步写回 journal。
- 专项验证命令写入不可变计划并展示；直接 Git 远端写入、包/镜像发布和常见上传命令不能伪装成验证命令执行。
- 白名单路径保留 `../` 和绝对路径的危险语义并在计划前阻断，不会被规范化为仓库内路径。
- RC 文本增量因行号或基线上下文不同而不一致时，只有文件集合完全一致、没有白名单外改动且稳定 patch-id 一致才按 `patch_id_equivalent` 放行；无法证明等价时仍按 `unresolved_semantic_difference` 阻断。
- `exact_match`、`already_present_equivalent` 和 `patch_id_equivalent` parity 放行；`unexpected_missing`、`unexpected_extra` 和无法证明的语义差异阻断。
- 任何 cherry-pick 冲突都自动 abort 并恢复 RC 集成前 HEAD，然后停止交由用户处理；不自动解决冲突。
- RC 集成失败且已完整恢复后，重复执行第一次确认会复用既有任务分支和已审计 commit，不重复创建分支或提交。
- 第二次确认只在 RC parity、RC 验收、干净工作区和远端前置引用仍有效时推送 RC，并回读远端 SHA。
- Task Manager 工作台只读展示交付事务，关联任务同步交付阶段和验证状态。

当前仍冻结：

- 未纳入计划或未取得对应确认的任何远端 Git 写入。
- 云效/TAPD 评论、附件、状态、负责人、迭代和关闭动作。
- 自动解决有业务语义的 cherry-pick 冲突。
- 部署、发布、业务 PG 自动查询和真实模型调用。

离线测试使用临时 Git 仓库和本地 bare remote，不访问真实业务仓库或真实远端。
最终门禁只证明技术闭环，真实 HIS 页面结果仍以 release 和 RC 两次人工运行时验收为准。
