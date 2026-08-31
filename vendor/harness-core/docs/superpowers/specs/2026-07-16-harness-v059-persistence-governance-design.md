# HIS Harness v0.59 持久化治理设计

## 边界

- 只增强 Harness 本地 SQLite，不连接业务数据库。
- 本轮测试全部使用 `/tmp` 隔离数据库，不迁移、不清理现有 `data/harness.sqlite`。
- 不增加定时删除；保留策略在备份、恢复和固定保留语义完成后单独实现。

## 目标

1. 每个 SQLite 连接强制 `foreign_keys=ON`、`busy_timeout=5000`，初始化后使用 WAL。
2. 用单调 `user_version` 和 `harness_schema_migrations` 记录 schema 版本，不再只依赖散落的 `ensure_column`。
3. 既有非空数据库首次升级前自动创建 SQLite 一致性备份；新数据库不产生多余备份。
4. 提供只读健康检查、显式备份和带精确确认的本地恢复 API/CLI。
5. 备份后执行 `integrity_check` 并保存 SHA-256；恢复前校验备份完整性和确认串，恢复不触碰远端系统。

## 失败边界

- 备份失败时拒绝迁移。
- schema 版本高于当前程序时拒绝启动，避免旧程序写新库。
- 恢复必须在关闭其他 Harness 进程后显式执行，并要求 `RESTORE:<backup_sha256>`；缺少确认只输出计划。
- v0.59 不自动删除历史 run/artifact，不把 WAL 当成多进程任务队列。
