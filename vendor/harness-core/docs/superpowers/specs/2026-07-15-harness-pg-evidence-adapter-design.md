# HIS Harness PostgreSQL 数据证据适配器设计

## 状态

- 日期：2026-07-15
- 阶段：v0.48
- 状态：已实现，待用户明确授权后执行真实测试/开发库只读 smoke
- 优先级：核心闭环增强；不替代业务代码、专项测试或人工业务验收。

## 1. 目标

在用户明确要求使用数据库查询数据时，为 Harness 提供多 PostgreSQL 库的受控、只读、限时数据证据能力。它应当自行从后端调用链、Mapper、SQL、实体和数据源配置定位候选库表，而不是要求用户每次指定库名或表名。

普通需求默认不连接数据库。只有调用方显式开启数据库证据模式，或用户明确要求“查数据库 / 查 PG / 用数据库验证数据”时，才进入该适配器。

首要目标是快速收敛：定位或查询失败时输出结构化证据、候选与缺口，不循环猜测、不全库盲扫、不因缺一个 SQL 长时间阻塞需求处理。

## 2. 约束与非目标

### 必须满足

1. 默认 `off`，正常需求、代码分析、worktree 改码和专项验证均不触发数据库连接。
2. 只接受已登记的 PostgreSQL Profile；Profile 从凭证 key 自动发现，不让模型自行发明连接信息。
3. 仅允许单语句、参数化、无副作用的 `SELECT` 或受限元数据查询。
4. 禁止 DML、DDL、事务控制、锁、`COPY`、多语句、函数写入和 `SELECT ... FOR UPDATE`。
5. DSN、账号、密码、参数原文和未脱敏敏感字段不得进入日志、SQLite、报告或 Task Manager 产物。
6. 每次数据库证据有总时间预算、连接超时、查询超时、探测次数和返回行数上限。
7. 数据库证据只能补充需求判断，不能放宽前后端源码契约、worktree 验证、独立 diff 审查或人工业务验收。

### 不做

- 不在普通需求中自动连接任何数据库。
- 不允许任意 SQL、修改数据、事务、导出、全库扫描或跨库笛卡尔查询。
- 不读取生产库；未标记为 `test` 或 `development` 的 Profile 一律拒绝执行。
- 不自动把数据库结果写回云效、TAPD、Git、业务数据库或配置文件。
- 不用数据库结果替代真实页面、接口、收费、医保、结算或退费人工验收。

## 3. 多库 Profile 与凭证发现

### 3.1 凭证命名

凭证文件只保存连接信息，沿用现有环境变量 -> 本机凭证文件 -> Keychain 的读取优先级。一个 Profile 的 key 为：

```text
pg_<profile>_readonly_dsn
pg_<profile>_readonly_user
pg_<profile>_readonly_password
```

例如：

```text
pg_df_jj_menzhen_readonly_dsn
pg_df_jj_menzhen_readonly_user
pg_df_jj_menzhen_readonly_password
```

现有 `pg_his_test_readonly_*` 自动形成 `his_test` Profile。缺少其中任意必要 key 时，Profile 只显示为不可执行，不输出值。

### 3.2 非密钥策略文件

新增本地、可分享但不含 secret 的 `pg_evidence_profiles.json`。它只声明环境与执行限制，不要求重复维护库表映射：

```json
{
  "schema_version": "1.0-pg-evidence-profiles",
  "default_mode": "off",
  "profiles": {
    "his_test": {
      "environment": "test",
      "enabled": true,
      "max_rows": 50,
      "connect_timeout_seconds": 5,
      "query_timeout_seconds": 10,
      "total_timeout_seconds": 45,
      "max_metadata_queries": 3,
      "sensitive_column_patterns": ["name", "phone", "mobile", "idcard", "identity", "address", "patient"]
    }
  }
}
```

Profile 未出现在策略文件、`enabled=false`、环境不是 `test`/`development` 时，适配器只允许生成计划，拒绝建立连接。后续新增 `df_jj_menzhen`、`df_jj_zhuyuan`、`df_zhushuju`、`df_waibujk` 等库，只需增加对应凭证 key 和一次性 Profile 策略项。

## 4. 按需查询与自主定位

### 4.1 显式触发

数据库证据作为独立模式，初始 CLI 入口采用：

```bash
python3 tools/pg_evidence.py \
  --request-file /path/to/pg_evidence_request.json \
  --profile-policy /path/to/pg_evidence_profiles.json \
  --mode plan \
  --project-root /path/to/backend-project \
  --output-dir /tmp/his_harness_pg_plan
```

`--mode plan` 只生成定位计划，永不连接。只有用户明确要求查询数据时才使用 `--mode execute`。主需求 workflow 不传新参数时保持现有行为，不会隐式调用此工具。

### 4.2 定位顺序

1. 从请求中接收已知的项目路径、接口名、字段名、实体名、业务关键词或现有 SQL 片段。
2. 读取项目内后端数据源配置、Mapper/Repository、SQL、实体映射与调用链，提取候选 datasource、schema、table、column 和查询条件。
3. 对已启用 Profile 按源码证据评分；唯一高置信候选优先。
4. 源码没有唯一表名时，才在候选 Profile 上查询 `information_schema.tables` / `information_schema.columns`，每个 Profile 受 `max_metadata_queries` 约束。
5. 仍无法得到唯一可信目标时，结束为 `needs_evidence`，记录候选和未满足条件，不执行真实业务表查询，不反复询问用户表名。

适配器不会默认扫描所有 Profile。仅在用户明确开启数据库证据、且源码无法缩小范围时，按固定上限探测少量已启用的测试/开发 Profile。

### 4.3 查询执行

执行前必须同时满足：用户显式 `--mode execute`、目标 Profile 通过策略、查询通过只读校验、目标表由代码或元数据证据唯一定位、参数化条件完整。

执行器对每个查询设置数据库端 `statement_timeout` 和客户端超时；结果最多返回 `max_rows` 行。敏感列按策略掩码，参数只记录名称、类型和哈希，绝不记录原值。

## 5. SQL 守卫

SQL 守卫使用保守拒绝策略：

- 仅允许一个顶层 `SELECT`，可允许只读 CTE；拒绝分号后的任何第二语句。
- 拒绝 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`GRANT`、`REVOKE`、`COPY`、`CALL`、`DO`、`SET`、`BEGIN`、`COMMIT`、`ROLLBACK`、`LOCK`、`VACUUM`、`ANALYZE` 和 `FOR UPDATE/SHARE`。
- 拒绝未参数化的用户输入拼接；查询条件只能使用命名参数。
- `information_schema` 探测使用固定 SQL 模板，不接受任意 schema/table 表达式。
- SQL 检查无法证明安全时返回 `blocked`，不尝试执行。

## 6. 时效与失败收敛

默认 Profile 限制：连接 5 秒、单 SQL 10 秒、总流程 45 秒、最多 3 条元数据查询、最多 50 行结果。任一超时、驱动缺失、连通失败、权限不足、候选歧义或安全校验失败会立刻停止当前步骤，并输出一个明确状态：`blocked`、`needs_evidence`、`timeout` 或 `failed`。

不重试同一个失败查询；不在同一次运行中扩大 Profile 扫描范围；不因为缺表或 SQL 不完整持续搜索。数据库模式失败不会改写需求分析或业务代码结论，只作为缺失的数据证据。

## 7. 结构化产物与审计

每次计划或执行输出：

- `pg_evidence_plan.json/md`：触发原因、候选 Profile/表/列、评分、只读守卫结果、是否连接。
- `pg_evidence_result.json/md`：状态、耗时、脱敏结果摘要、行数、字段脱敏信息、错误摘要和下一步。
- `pg_evidence_audit.json`：Profile 名、环境、执行模式、查询模板 ID、参数名/类型/哈希、时间预算、结果状态；不包含 DSN、账号、密码、参数值、原始敏感数据或完整 SQL 文本。

这些产物可以在未来显式接入主 workflow 和 Task Manager；v0.48 初版保持独立 CLI，避免普通需求流程产生数据库副作用。

## 8. 测试与验收

测试使用假连接与脱敏 fixture，不连接真实 PostgreSQL：

1. 凭证 key 自动发现、缺失 key、未登记/非测试 Profile 阻断。
2. 普通 workflow 未显式调用 PG 工具时零连接。
3. 代码线索定位唯一 Profile/table；无唯一线索时仅做有上限元数据计划。
4. SQL 守卫拒绝写入、多语句、锁、事务和非参数化条件。
5. 超时、权限失败、驱动缺失只产生结构化失败结果，不重试、不泄露 secret。
6. 敏感字段、参数、DSN、账号和密码不出现在 JSON/Markdown/audit。
7. 结果行数、查询次数和总时限严格受 Profile 限制。

真实连接验证不属于开发期自动测试。未来用户明确要求“使用数据库查询数据”时，才针对指定测试/开发 Profile 执行一次只读 smoke，并由用户确认结果是否符合业务数据预期。

## 9. 后续顺序

1. v0.48：完成独立、按需、只读、限时的 PostgreSQL 数据证据适配器。
2. v0.49：动态任务拆分、角色选择、子任务依赖和交接契约。
3. v0.50：在引擎能力稳定后，恢复 WebUI 产品化和浏览器运行态证据增强。
