# 改码前理解证据门禁设计

## 背景

现有 Harness 已有需求校准、技术决策、变更归属和需求治理，但需求文字在形式上完整时，仍可能在未证明业务背景、真实使用场景、项目入口、调用关系和相邻影响的情况下进入工程执行。这会把“猜测后的改动”误认为“精准定位后的改动”。

## 目标

每一次会产生本地工程改动的运行，都必须先形成可审计的理解证据包。证据不足时，Harness 只输出继续调查的结论和缺口，不得进入 patch、worktree、预提交验证或核心闭环执行。

## 不替代的既有机制

- 需求校准继续负责规则、参数、默认值和范围的语义确认。
- 技术决策继续负责项目选择、代码取证、接口契约和允许路径。
- 需求治理继续负责合理性、合规、影响、验收和一次修改可行性。
- 新门禁只是它们之间的强制前置条件；不向 `requirement-governance.v1` 增加第九个检查项，避免破坏已注册的 capability 合同。

## 理解证据包

新增 `requirement-understanding.v1`，固定评估以下七项：

1. `business_background`：来源正文能说明当前痛点/业务背景，且有来源引用。
2. `usage_scenario`：有可验收的角色、触发条件或操作场景。
3. `target_and_boundary`：目标愿望、明确范围和保持不变的行为均已记录。
4. `project_selection`：候选项目存在，且由源码或显式项目路径证据支持。
5. `entry_and_call_chain`：目标页面/接口入口以及至少一段本地调用、依赖或服务图证据可定位；纯静态展示也必须明确标注并有源码证据。
6. `change_and_impact_scope`：允许路径有字段/源码取证，且变更归属和相邻影响已闭合。
7. `verification_baseline`：已有测试基座/建议命令与人工验收路径均已确定。

结果只有 `ready_for_change` 时 `can_modify=true`。其余结果以 `blocked_needs_requirement_context` 或 `blocked_needs_project_discovery` 返回，并给出下一步只读调查动作。

## 执行门禁

- `readonly`：仍可完成证据收集与报告；理解不足时状态应明确为“分析完成但不可改码”。
- `worktree`、`review-worktree`、`fullstack-worktree`、`precommit-verify`、`single-demand-trial`、`core-closure-trial`、`auto-local`：理解证据包不是 `ready_for_change` 时一律阻断。
- `auto-local` 不再允许以快速路径为由跳过项目上下文扫描；它只能缩短后续执行，不得缩短理解和取证。
- `requirement_governance=observe` 保持报告兼容性，但不再能绕过上述改码前门禁。

## 审计与可见性

每个运行保存 `requirement_understanding.json` 和 `requirement_understanding.md`，写入运行输出与总报告。需求档案同步时，该报告随运行产物进入同一工作项目录；后续补充需求更新原需求档案，不另建平行需求文件。

## 非目标

- 不凭模型主观猜测补齐业务事实。
- 不读取、修改云效以外的外部系统，也不改变云效写入边界。
- 不改变既有 eight-check governance schema 或现有 MCP/capability 名称。
