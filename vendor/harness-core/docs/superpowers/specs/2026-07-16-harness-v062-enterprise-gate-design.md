# HIS Harness v0.62 统一离线企业门禁设计

## 阶段

1. Python compileall。
2. 隔离 SQLite 全量 unittest。
3. 隔离存储 mock self-check。
4. 10 场景脱敏真实需求 replay。
5. 高置信度源码 secret scan。

每轮使用独立临时数据库和输出目录，删除模型/API key/token/PAT/password/secret/credentials 环境变量。门禁不读取现有数据库，不调用网络、真实模型、云效、PG 或 Git 远端。

## 状态

- 所有阶段通过才是 `technical_valid=true`。
- `business_valid`、`runtime_verified`、`promotion_enabled` 固定为 false。
- 连续多轮必须检查 replay 结果哈希一致，任一轮失败即门禁失败。
- `--stages` 仅用于快速排错；CI 和企业验收必须使用默认完整阶段。
