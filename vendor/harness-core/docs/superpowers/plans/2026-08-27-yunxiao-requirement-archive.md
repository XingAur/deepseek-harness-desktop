# 云效需求长期档案实施计划

**Goal:** 将只读云效证据接入稳定的单需求本地档案，保留附件、哈希、失败记录和同档更新能力。

**Architecture:** 读取层输出证据与受控临时下载目录；新档案服务将其安全复制到 `DFHIS-<编号>`，生成快照、来源说明、清单和可更新需求文档；工作流 CLI 可把归档快照作为本次任务的本地证据源。

**Tech Stack:** Python 3 标准库、现有 `unittest`、既有 Yunxiao 只读客户端。

**Global Constraints:** 不写云效；不读写凭证值；不删除或改写历史 `YUNXIAO/` 目录；默认调用保持兼容；仅用定向单元测试验证，不对真实云效发请求。

## 1. 固化契约与基线

**Files:** `docs/superpowers/specs/2026-08-27-yunxiao-requirement-archive-design.md`, `docs/superpowers/plans/2026-08-27-yunxiao-requirement-archive.md`

1. 写入目录、同档更新、完整性和安全边界。
2. 运行 `python3 -m unittest tests.test_yunxiao_read tests.test_requirement_provider`，记录现有读取层基线。

## 2. 先写失败测试

**Files:** `tests/test_requirement_archive.py`

1. 覆盖稳定 `DFHIS-<编号>` 目录、附件哈希和清单。
2. 覆盖第二次同步更新同一份 `requirement.md` 并保留人工区块。
3. 覆盖拒绝受控临时目录外的来源文件与失败记录。
4. 运行新测试，确认在实现前失败。

## 3. 实现档案服务

**Files:** `app/requirement_archive.py`, `app/yunxiao_read.py`

1. 新建纯本地档案服务、原子文本写入、安全路径校验、SHA-256 清单与需求文档区块更新。
2. 为云效读取增加显式 `archive` 下载策略：取消数量截断，并将单文件下载上限配置化；保持原默认不变。
3. 运行档案与云效读取专项测试。

## 4. 接入正式工作流

**Files:** `harnesses/his_requirement_workflow.py`, `tests/test_requirement_archive.py`, `README.md`

1. 新增 `--yunxiao-archive-root`、`--yunxiao-archive-change-note` 和文件大小配置参数。
2. 档案模式先读取一次云效并生成快照；工作流复用本地快照，不重复请求云效。
3. 写明终端、Codex App 与其他 adapter 的共同入口契约。
4. 运行针对性测试、语法检查和最终 diff 审核。
