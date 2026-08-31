# HIS Harness v0.49 动态规划实施计划

> 依据已确认的平台总设计，采用测试先行，并保持旧流程默认不变。

## 任务 1：定义失败测试和数据契约

文件：

- `tests/test_dynamic_planning.py`
- `app/dynamic_planning.py`

覆盖 simple/medium/large/high-risk 评分、强制升级、按需角色、独立审查、路径并行与串行、DAG 环和缺失路径证据。

## 任务 2：实现确定性动态规划器

实现：

- `DynamicPlanningRequest`
- `ComplexityAssessment`
- `TeamPlan`
- `SubtaskSpec`
- `TaskEdge`
- `TaskGraph`
- `HandoffContract`
- `DynamicPlan`

评分和拆分只消费显式输入，不调用 LLM、数据库、浏览器或业务仓库。

## 任务 3：CLI 和产物

文件：

- `tools/dynamic_plan.py`
- `config/dynamic_planning.example.json`

命令必须显式 `--enable`。生成 JSON、Markdown 和审计文件；所有输出只记录规划证据和哈希。

## 任务 4：自检和文档

修改：

- `tools/self_check.py`
- `README.md`
- `HANDOFF.md`

mock 自检覆盖 simple、high-risk、路径冲突和默认关闭。文档明确 dynamic-plan 与 dynamic-execute 的边界。

## 验证命令

```bash
python3 -m unittest tests.test_dynamic_planning -v
python3 -m py_compile app/dynamic_planning.py tools/dynamic_plan.py tools/self_check.py
python3 -m unittest discover -s tests -v
python3 tools/self_check.py --mode mock --retain-output --output-dir /tmp/his_harness_v049_self_check
python3 tools/dynamic_plan.py --help
```

完成后检查逐行改动、尾随空格、产物中敏感信息和现有执行入口未接入动态执行。
