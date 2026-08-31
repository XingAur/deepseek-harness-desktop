# HIS Harness 可执行验收契约 Implementation Plan

> **状态：** 已完成。实现与回归结果见本计划末尾的最终验证步骤。

**Goal:** 让 Harness 在本地 worktree 改码前后执行排序关联 fixture，并把“同号稳定排序、方案树父节点排序、无序号保持原序、目标代码专项测试”作为核心闭环的硬闸门。

**Architecture:** 新增独立的 `app/acceptance_contracts.py`，只负责解析、校验和执行脱敏 JSON fixture，不读取业务仓库或外部系统。`app/harness.py` 在核心闭环创建 worktree 前运行该契约，并把契约中声明的专项验证命令合并到 worktree 验证；`app/core_closure.py` 和独立 diff 审查只消费结构化执行结果，不重新解释原始 fixture。

**Tech Stack:** Python 3 标准库 `dataclasses`、`json`、`pathlib`、`unittest`；现有 Harness SQLite artifact 存储和受控 worktree 执行器。

## Global Constraints

- 只改 `/Users/lym/WorkCode/ai/Harness`，不改 DFHIS-31558 业务仓库。
- v0.47 不连接 PG、Chrome、Playwright、Yunxiao、TAPD 或其他外部系统。
- fixture 只能使用脱敏科室 ID、名称、顺序号、源位置和树结构。
- 非排序需求保持 v0.46 行为；只有命中排序/树/列表关联风险的需求才要求契约。
- fixture 通过不等于业务代码通过；契约声明的专项命令必须在 worktree 中通过。
- 不执行 Git 远端写入、云效写入、部署、发布或回滚。Harness 根目录当前不是 Git 仓库，因此本计划不包含提交步骤。

---

### Task 1: 建立排序关联契约与 fixture 验证器

**Files:**
- Create: `app/acceptance_contracts.py`
- Create: `tests/test_acceptance_contracts.py`
- Create: `fixtures/acceptance_contracts/dfhis-31558-ordering.json`

**Interfaces:**
- Consumes: `acceptance_contract.json` 的 `ordering_relation` payload 和其 `fixture` 文件。
- Produces: `AcceptanceContractResult`，含 `status`、`contract_id`、`source_order`、`target_leaf_order`、`checks`、`blockers`、`verify_command`。
- Exports: `load_acceptance_contract(path)`, `ordering_contract_required(title, demand_text)`, `execute_acceptance_contract(path)`, `AcceptanceContractResult`。

- [x] **Step 1: 写同号与方案树失败测试**

在 `tests/test_acceptance_contracts.py` 新增：

```python
ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "acceptance_contracts"

class AcceptanceContractExecutionTests(unittest.TestCase):
    def fixture_path(self, name: str) -> Path:
        return FIXTURE_DIR / name

def test_ordering_contract_uses_source_index_for_ties_and_parent_descendants(self) -> None:
    result = execute_acceptance_contract(self.fixture_path("dfhis-31558-ordering.json"))

    self.assertEqual("pass", result.status)
    self.assertEqual(["31", "174", "25162", "85", "26429", "999", "998"], result.source_order)
    self.assertEqual(result.source_order, result.target_leaf_order)
    self.assertEqual("pass", result.checks["same_sequence_uses_source_index"])
    self.assertEqual("pass", result.checks["parent_uses_earliest_descendant"])
```

fixture 至少包含：`31/心血管` 与 `174/高血压` 同为 `shunXuHao=1`、但 `sourceIndex` 分别为 `0/1`；`31` 位于“内科”方案分组、`174` 位于“中医科门诊”方案分组；`25162/肾病` 位于“常用”；`85/泌尿` 和 `26429/呼吸` 为直接或不同分组节点；`999/B`、`998/A` 均没有顺序号，且原始相对顺序必须保持为 `999、998`。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
python3 -m unittest tests.test_acceptance_contracts.AcceptanceContractExecutionTests.test_ordering_contract_uses_source_index_for_ties_and_parent_descendants -v
```

Expected: FAIL，错误为 `ModuleNotFoundError: No module named 'app.acceptance_contracts'` 或缺少 `execute_acceptance_contract`。

- [x] **Step 3: 实现最小契约执行器**

在 `app/acceptance_contracts.py` 实现以下稳定接口：

```python
@dataclass(frozen=True)
class AcceptanceContractResult:
    schema_version: str
    status: str
    contract_id: str
    kind: str
    verify_command: str
    source_order: tuple[str, ...] = ()
    target_leaf_order: tuple[str, ...] = ()
    checks: dict[str, str] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()

```

稳定导出函数为 `ordering_contract_required(*, title: str, demand_text: str) -> bool`、`load_acceptance_contract(path: str | Path) -> dict[str, Any]` 和 `execute_acceptance_contract(path: str | Path) -> AcceptanceContractResult`。

实现规则：

```python
def source_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    value = row.get("shunXuHao")
    has_sequence = value not in (None, "", 0, "0")
    return (0 if has_sequence else 1, int(value) if has_sequence else 0, int(row["sourceIndex"]))
```

先按 `source_sort_key` 排序，再按 `department_key` 去重保留首次；递归计算树节点的最早叶子键，排序同级节点，最后深度优先展平叶子。无排序号的节点只能用原树/源位置作为最后稳定键，禁止名称排序。结果不一致时写明第一个不一致索引和两个值。

- [x] **Step 4: 运行测试确认通过**

Run:

```bash
python3 -m unittest tests.test_acceptance_contracts.AcceptanceContractExecutionTests.test_ordering_contract_uses_source_index_for_ties_and_parent_descendants -v
```

Expected: PASS。

- [x] **Step 5: 补齐结构校验与无序号回归测试**

在同一测试文件新增三个具名用例：`test_contract_blocks_when_same_sequence_or_parent_policy_is_missing`、`test_contract_preserves_unsorted_relative_order`、`test_contract_blocks_when_source_and_tree_leaf_orders_differ`。第一个分别删除 `source.order_keys` 中的 `sourceIndex`、`target.parent_order` 和 `source.unsorted_behavior`，断言 `status == "blocked"` 且 `blockers` 精确说明缺失字段；第二个将两个无顺序号科室输入为 `B、A`，断言结果仍为 `B、A`；第三个将树内 `174` 删除，断言 `target_leaf_order` 与 `source_order` 的第一个不同项被记录。

- [x] **Step 6: 运行 Task 1 全量测试**

Run:

```bash
python3 -m unittest tests.test_acceptance_contracts -v
python3 -m py_compile app/acceptance_contracts.py
```

Expected: 全部 PASS。

### Task 2: 将契约结果接入核心闭环和独立审查

**Files:**
- Modify: `app/core_closure.py`
- Modify: `app/harness.py`
- Modify: `tests/test_core_closure.py`
- Modify: `tests/test_acceptance_contracts.py`

**Interfaces:**
- Consumes: `AcceptanceContractResult | None`、`RequirementContract`、`technical_decision`。
- Produces: 带 `acceptance_contract` 摘要的 `RequirementContract` 和带 `acceptance_contract_status` 的 `DiffReview`。
- Requires: Task 1 的 `ordering_contract_required` 和 `execute_acceptance_contract`。

- [x] **Step 1: 写核心闭环阻断失败测试**

在 `tests/test_core_closure.py` 新增：

```python
def test_sorting_tree_requirement_blocks_without_executable_acceptance_contract(self) -> None:
    contract = build_requirement_contract(
        title="DFHIS-31558",
        demand_text="科室树和右侧排班按顺序号排序并保持一致。",
        requirement_calibration=ready_calibration(),
        technical_decision=ready_decision(),
        acceptance_matrix=acceptance_matrix(),
        apply_to_project=False,
        acceptance_contract_result=None,
    )

    self.assertEqual("blocked", contract.status)
    self.assertIn("可执行排序验收契约", "\n".join(contract.blockers))
```

再写一个 fixture 失败用例，构造 `AcceptanceContractResult(schema_version="1.0-acceptance-contract-result", status="blocked", contract_id="tree-order", kind="ordering_relation", verify_command="", blockers=("fixture 顺序不一致",))`，断言核心闭环同样阻断；写一个普通默认值需求用例，断言不传契约仍为 `ready`。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
python3 -m unittest tests.test_core_closure.CoreClosureContractTests.test_sorting_tree_requirement_blocks_without_executable_acceptance_contract -v
```

Expected: FAIL，错误为 `build_requirement_contract()` 不接受 `acceptance_contract_result`。

- [x] **Step 3: 最小化扩展核心结构**

在 `app/core_closure.py`：

```python
@dataclass(frozen=True)
class RequirementContract:
    # 保留现有字段
    acceptance_contract: dict[str, Any] = field(default_factory=dict)

def build_requirement_contract(
    *,
    title: str,
    demand_text: str,
    requirement_calibration: dict[str, Any],
    technical_decision: dict[str, Any],
    acceptance_matrix: dict[str, Any],
    apply_to_project: bool,
    acceptance_contract_result: AcceptanceContractResult | None = None,
) -> RequirementContract:
    requires_ordering_contract = ordering_contract_required(title=title, demand_text=demand_text)
    if requires_ordering_contract and acceptance_contract_result is None:
        blockers.append("排序/方案树关联需求缺少可执行排序验收契约。")
    elif acceptance_contract_result is not None and acceptance_contract_result.status != "pass":
        blockers.extend(acceptance_contract_result.blockers)
```

`review_final_diff()` 增加可选 `acceptance_contract_result` 参数：当排序契约要求存在时，fixture 状态不是 `pass`、契约没有 `verify_command`、或 diff 缺少契约 `implementation_evidence.all_of` 中任一 token，返回 `blocked`。普通需求未传该参数时行为不变。

在 `app/harness.py` 的 `run()` 和 `_run_core_closure_trial()` 增加可选 `acceptance_contract_file` 参数。核心闭环开始前执行 `execute_acceptance_contract()`；成功时把其 `verify_command` 去重合并到 `verify_commands` 后传给 worktree；失败时只保存结构化产物并在进入 worktree 前阻断。

- [x] **Step 4: 运行核心闭环测试确认通过**

Run:

```bash
python3 -m unittest tests.test_core_closure.CoreClosureContractTests -v
```

Expected: PASS，普通默认值和 `paiBanMs` 基线不回归。

- [x] **Step 5: 写并运行独立 diff 审查测试**

在 `tests/test_core_closure.py` 新增 `test_diff_review_blocks_sorting_contract_without_parent_sort_evidence` 和 `test_diff_review_accepts_sorting_contract_evidence_after_fixture_passes`。第一个 diff 只包含 `shunXuHao` 排序、缺少 `getPaiBanSortKey`；第二个 diff 同时包含 `paiBanSortIndex` 和 `getPaiBanSortKey`。断言前者 `blocked` 且 finding 包含缺失证据 token，后者 `pass`。

Run:

```bash
python3 -m unittest tests.test_core_closure.CoreClosureDiffReviewTests -v
```

Expected: PASS。

### Task 3: 暴露 CLI、产物和自检入口

**Files:**
- Modify: `harnesses/his_requirement_workflow.py`
- Modify: `app/harness.py`
- Modify: `tools/self_check.py`
- Modify: `tests/test_core_closure_cli.py`
- Create: `tests/test_self_check_acceptance_contracts.py`

**Interfaces:**
- Consumes: `--acceptance-contract-file <local-json-path>`。
- Produces: `acceptance_contract.json/md` 和 `acceptance_contract_result.json/md` artifact；CLI help 和 mock self-check 可验证。

- [x] **Step 1: 写 CLI 参数失败测试**

在 `tests/test_core_closure_cli.py` 新增：

```python
def test_help_lists_acceptance_contract_file(self) -> None:
    completed = subprocess.run(["python3", str(CLI), "--help"], cwd=ROOT, text=True, capture_output=True, check=True)
    self.assertIn("--acceptance-contract-file", completed.stdout)
```

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
python3 -m unittest tests.test_core_closure_cli.CoreClosureCliTests.test_help_lists_acceptance_contract_file -v
```

Expected: FAIL，帮助文本不包含该参数。

- [x] **Step 3: 添加 CLI 与 artifact 写入**

在 `harnesses/his_requirement_workflow.py` 增加：

```python
parser.add_argument(
    "--acceptance-contract-file",
    default="",
    help="local v0.47 executable acceptance contract JSON; required for sorting/tree relation auto-local runs",
)
```

将该参数原样传给 `RequirementWorkflowRunner.run()`。在 `app/harness.py` 增加 `_store_acceptance_contract_artifacts()`，只写入已读取的本地脱敏 JSON 与执行结果；不得复制绝对凭证路径、数据库 URL 或外部系统信息。`tools/self_check.py` 调用 DFHIS-31558 fixture，确认同号、方案树和无序号三个 checks 都为 `pass`，并确认错误 fixture 会 `blocked`。

- [x] **Step 4: 运行 CLI 和 self-check 测试确认通过**

Run:

```bash
python3 -m unittest tests.test_core_closure_cli -v
python3 -m unittest tests.test_acceptance_contracts tests.test_core_closure -v
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v047_self_check
```

Expected: 全部 PASS；self-check 包含 `acceptance_contract_checks`，不会访问外部网络、业务数据库或业务仓库。

### Task 4: 文档化 v0.47 和回归命令

**Files:**
- Modify: `README.md`
- Modify: `HANDOFF.md`
- Modify: `docs/superpowers/specs/2026-07-15-harness-executable-acceptance-contract-design.md`

**Interfaces:**
- Consumes: Task 1 到 Task 3 的稳定 CLI、fixture 路径和 artifact 名称。
- Produces: 复跑命令、明确的自动/人工验证边界和 v0.48 PostgreSQL 延后说明。

- [x] **Step 1: 写 README/HANDOFF 验收清单**

新增 v0.47 条目，必须准确写明：

```text
fixture 验证通过 + worktree 专项命令通过 + 独立 diff 审查通过，才可自动本地应用。
fixture 不等于真实页面验收；浏览器和 PG 不在 v0.47 范围内。
```

加入可复制命令：

```bash
python3 -m unittest tests.test_acceptance_contracts tests.test_core_closure -v
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v047_self_check
```

- [x] **Step 2: 运行最终验证**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile app/acceptance_contracts.py app/core_closure.py app/harness.py harnesses/his_requirement_workflow.py tools/self_check.py
python3 tools/self_check.py --mode mock --output-dir /tmp/his_harness_v047_final_self_check
```

Expected: 全部 PASS；没有网络、PG、Chrome、云效/TAPD 写入或业务仓库修改。

- [x] **Step 3: 检查最终改动范围**

Run:

```bash
git diff --check
git status --short
```

Expected: 若 Harness 根目录后来仍非 Git 仓库，记录该事实；改用逐文件 `diff -u` 和测试结果完成变更审查，不执行提交。
