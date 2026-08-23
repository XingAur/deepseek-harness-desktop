# Task 2 实现报告

## 状态

Task 2 已完成。实现范围仅包含版本化 Agent SQLite 存储、旧 Profile/State 兼容字段和 OS 原生凭证库抽象；未进入 Task 3 的权限 broker、审批流程或 renderer API。

## TDD 证据

### RED

1. Profile 兼容测试先失败：旧 `profiles.json` 反序列化后 `agentPermissionDefault` 为 `null`，不满足运行时默认 `request-approval`。
2. SQLite 测试先无法编译：`AgentStore`、`AppPaths.agent_database/agent_backups` 和 `rusqlite` 尚不存在；测试已先覆盖 clean v1、幂等启动、v0 fixture 迁移、备份/hash/integrity、强制失败 rollback/recovery、损坏库保留、并发读和无秘密 schema/value。
3. Foundation 集成测试先失败：初始化后固定 SQLite 路径不存在。
4. CredentialVault 合约测试先无法编译：`credentials::model` 与 `credentials::vault` 尚不存在；测试已先覆盖 UUID account、覆盖/删除/缺失语义、脱敏错误、无秘密 Debug/序列化、无 renderer resolve command 和原生 backend 只构造边界。
5. RED 期间确认 `rusqlite 0.40.1` 的备份 API 使用 `MAIN_DB`，与旧版 `DatabaseName` API 不兼容；实现保持固定版本并按 0.40.1 API 调整，没有降级依赖。

### GREEN

- Profile 专项：16 passed，0 failed（沙箱外重跑；沙箱内唯一失败为既有 loopback bind `EPERM`）。
- Agent store 专项：7 passed，0 failed。
- Credentials 专项：8 passed，0 failed（过滤词额外匹配 1 个既有 navigation 测试）。
- 全量 Rust：154 passed，0 failed；main 0 tests；doc-tests 0 tests。

## 实现与数据安全边界

- 新增独立 `AgentPermissionMode`，序列化值固定为 `request-approval | smart-approval | full-access`，默认 `request-approval`；既有 `PermissionMode` 的 `read-only | workspace-write` 未改名或重解释。
- 旧 profile 的默认字段在反序列化时补齐，但默认值序列化时省略，避免无关旧记录被改写；旧 `state.json` round-trip 值保持一致，Profile repository 继续使用原子 JSON 保存。
- SQLite 固定为 `<app-data>/state/agent-platform.sqlite3`，迁移备份固定为 `<app-data>/backups/agent-platform/`，`PRAGMA user_version = 1`、foreign keys 开启、busy timeout 为 5 秒。
- v1 schema 只保存 provider/agent/task/session/checkpoint/content reference/approval/grant/extension/compatibility/credential metadata/audit summary 等有界元数据；credential 表仅含 opaque ID、状态和时间，不含 secret-bearing 列或值。
- 现有 v0 库迁移前使用 SQLite backup API 生成一致备份，要求 `integrity_check` 结果严格等于单行 `ok`，计算 SHA-256 和 byte length，并原子写 sidecar；迁移在进程级排他锁和 SQLite exclusive transaction 内执行。
- 迁移失败会 rollback，并返回包含源库和已验证备份的阻塞恢复状态；损坏或不支持版本的库不会被删除、覆盖、截断或替换为新库。
- `CredentialVault` 提供内部 `put/resolve/delete/status`；原生 backend 使用固定 `keyring = 4.1.6` v1 API，服务名固定为 `ai.deepseek.harness.desktop.agent-credentials.v1`，account 只使用生成的 UUID credential ID。
- `SecretValue` 不实现 `Debug`、`Display`、`Serialize`、`Deserialize` 或 `Clone`，drop 时清零缓冲；backend 原始错误在跨 vault 边界前转换为固定脱敏 code，测试 backend 的秘密和被覆盖值也会清零。
- 没有 plaintext fallback，没有新增 renderer-facing secret resolve/get command。单元测试只使用 test-only 内存 backend；原生 backend 仅编译和构造，不读写用户真实 Keychain。

## 依赖

- `rusqlite = =0.40.1`，features `backup + bundled`。
- `keyring = =4.1.6`，feature `v1`；当前 macOS 构建解析到 Keychain backend，lockfile 同时包含 Windows Credential Manager backend；未启用 `cli`。

## 最终验证

```text
cargo test --manifest-path src-tauri/Cargo.toml agent_store --locked
  7 passed; 0 failed; 147 filtered out

cargo test --manifest-path src-tauri/Cargo.toml credentials --locked
  8 passed; 0 failed; 146 filtered out

cargo test --manifest-path src-tauri/Cargo.toml profile --locked
  16 passed; 0 failed; 138 filtered out

cargo test --manifest-path src-tauri/Cargo.toml --locked
  lib: 154 passed; 0 failed
  main: 0 tests
  doc-tests: 0 tests

rustfmt --edition 2024 --check --config skip_children=true <Task 2 新增 Rust 模块>
  passed; no output

git diff --check
  passed; no output
```

## 残余风险与未验证项

- 按安全约束，没有对真实 macOS Keychain 执行写入、读取或删除行为测试；只验证了原生 backend 的当前 macOS 编译与无副作用构造。
- 当前主机未安装/执行 Windows target 构建，Windows Credential Manager 的实际编译与行为留给后续 Windows CI；没有用其他 backend 或明文方案替代。
- Vault API 是 Task 2 为后续任务建立的内部契约，当前除 foundation 构造外尚无生产调用方，因此编译仍报告相关 dead-code warnings；未为消除 warning 提前实现 Task 3。
- 未执行自动恢复覆盖用户数据库；当前只提供阻塞恢复状态和经验证备份，这是“失败时绝不替换用户库”的有意边界。
