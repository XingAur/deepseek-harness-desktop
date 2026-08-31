# HIS Harness 可执行验收契约设计

## 状态

- 日期：2026-07-15
- 阶段：v0.47
- 状态：已完成实现与回归验证
- 优先级：高于 WebUI、工作台产品化和 PostgreSQL 数据查询

## 1. 目标

将“需求文字、截图或人工经验中的页面语义”转为可执行的本地验收契约，补足当前核心闭环只能检查 diff、白名单和专项命令，不能可靠判断两个视图业务结果是否一致的缺口。

首个真实回归样本为 DFHIS-31558：挂号处理页面左侧科室树和右侧排班卡片必须按同一排班顺序显示。该能力必须在代码合入原业务仓库前发现以下问题：

1. 相同 `shunXuHao` 的科室没有沿用右侧排班源列表的稳定次序。
2. 存在方案树时，父分组没有按其后代中最早的排班键排序，导致展开后的叶子科室顺序与右侧排班不一致。
3. 没有顺序号的数据被错误重排，破坏原有相对顺序。

本阶段不做 WebUI，不连接业务数据库，不读取真实浏览器登录态，不把静态截图视作自动通过证据。

## 2. 范围与边界

### 本阶段范围

1. 新增版本化 `acceptance_contract` 结构，先支持 `ordering_relation` 契约。
2. 通过本地 fixture 计算排序后的右侧源序列、方案树展开后的叶子序列，并比较两者。
3. 在 `core-closure-trial` 和 `auto-local` 中，对命中排序、树、列表关联的需求要求提供有效契约；缺失或歧义时阻断自动本地应用。
4. 将契约、fixture 执行结果、目标专项验证命令和独立 diff 审查写入运行产物。
5. 要求 worktree 中的实现提供实际目标代码验证命令；fixture 只证明验收数据和期望，不替代业务代码执行。
6. 以 DFHIS-31558 的同号、方案树、无序号数据建立回归 fixture。

### 明确不做

- 不解析截图文字，不通过图片推断顺序。
- 不自动连接 Chrome、Playwright、Yunxiao、TAPD、PG 或其他业务系统。
- 不自动执行任意 SQL，不读取患者、排班、收费或医保真实数据。
- 不创建分支、提交、推送、发布、写云效或回滚业务仓库。
- 不让模型在未确认规则时自行选择同号、空值或树形分组语义。

## 3. 契约模型

`acceptance_contract.json` 使用 `1.0-acceptance-contract` 版本。首期只支持如下结构：

```json
{
  "kind": "ordering_relation",
  "id": "department-tree-matches-schedule-cards",
  "source": {
    "collection": "schedule_rows",
    "department_key": "keShiId",
    "order_keys": ["shunXuHao", "sourceIndex"],
    "deduplicate": "first_department_occurrence",
    "unsorted_behavior": "preserve_relative_order"
  },
  "target": {
    "collection": "department_tree",
    "leaf_key": "keShiId",
    "parent_order": "earliest_descendant_source_key",
    "comparison": "flattened_leaf_order"
  },
  "fixture": "fixtures/dfhis-31558-ordering.json",
  "verify_command": "node src/pages/.../paiBanSort.test.js"
}
```

规则含义：

- 右侧源排序按 `shunXuHao`，相同值按接口源数组位置 `sourceIndex`。
- 相同科室在右侧可能出现多张排班卡片；比较时只取该科室的首次出现位置。
- 左树保留方案分组，但每个父节点的排序键取所有后代叶子中最早的右侧源排序键。
- 左树递归展开后的叶子序列必须等于右侧去重后的科室序列；分组标题不参与序列比较。
- 没有有效顺序号的项目保留当前相对顺序，不能被名称排序等隐式规则改变。

无法明确 `department_key`、排序键、同号规则、树比较方式或无序号行为时，契约状态为 `blocked`，不进入自动改码。

## 4. 执行链

```text
需求校准
  -> 识别排序/树关联风险
  -> 生成或读取验收契约草案
  -> 契约字段与边界确认
  -> fixture 验证器
  -> worktree 改码
  -> 目标代码专项测试命令
  -> 独立 diff 审查
  -> 自动本地应用或阻断
```

### 4.1 需求校准与阻断

命中排序、树、列表关联等关键词时，Harness 必须新增 `ordering_contract_required` 提示。若用户或已确认 `harness-rules` 没有提供完整语义，结果只能是“待确认”，不能因为现有代码出现 `sort` 就视为可改码。

### 4.2 Fixture 验证器

验证器只处理 JSON fixture，不执行项目代码。它输出：

- `source_order`：按契约计算的右侧去重科室顺序。
- `target_leaf_order`：按契约计算的展开树叶子顺序。
- `pass` 或 `blocked`。
- 首个不一致位置及两个值。
- 对同号、父节点、无顺序号的逐项检查结果。

这层的职责是保证验收语义明确且可回归，不允许把 fixture 通过说成业务实现已通过。

### 4.3 Worktree 与目标代码验证

排序关联契约必须声明 `verify_command`。该命令在 worktree 内运行，用来执行实际业务代码的单元测试、方法测试或项目专项测试。Fixture 通过、但目标代码命令缺失或失败时，核心闭环必须阻断。

独立 diff 审查额外检查：

- 修改路径在白名单内。
- 存在目标代码验证证据。
- 变更没有删除无序号保护或同号稳定排序保护。
- 方案树场景中，父节点排序依赖后代排班键，而非方案原字段覆盖。

## 5. 数据与隐私边界

v0.47 的 fixture 必须使用人工构造或已脱敏的最小字段：科室 ID、科室名、顺序号、源位置和树结构。禁止在 Harness 产物中写入患者身份、医保、费用、手机号、证件号或真实业务记录。

PostgreSQL 查询不属于本阶段。后续 v0.48 如实施，只提供独立的只读数据证据适配器：凭证引用、只读角色、连接白名单、参数化 SQL、表/列白名单、行数上限、字段脱敏和审计产物。它不能与自动改码或外部写入共用权限。

## 6. 测试与验收

Harness 自身测试至少覆盖：

1. 同为 `shunXuHao=1` 时，`sourceIndex=0` 的科室先出现。
2. 方案父分组按最早后代排班键移动，展开叶子序列与右侧一致。
3. 无顺序号节点保持相对顺序。
4. 缺少同号规则、无序号规则或树比较策略时阻断。
5. fixture 通过但 `verify_command` 缺失或失败时，核心闭环阻断。
6. 非排序需求保持 v0.46 行为，不要求新增契约。

DFHIS-31558 的业务项目专项测试只在临时 worktree 内运行；用户真实页面验收仍是最终运行时证据，单独登记，不能覆盖源码或契约门禁。

## 7. 后续顺序

1. v0.47：本设计的可执行验收契约和 fixture 回归引擎。
2. v0.48：独立只读 PostgreSQL 数据证据适配器，前提是用户明确提供允许连接的测试环境与凭证引用策略。
3. v0.49：动态任务拆分、角色选择、子任务依赖和交接契约。
4. v0.50：在上述引擎能力稳定后，再恢复 WebUI 产品化和浏览器运行态证据增强。
