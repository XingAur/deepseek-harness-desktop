# Task 2 实现报告

## 状态

Task 2 第一轮审查修复已完成。范围仅包含 Foundation/Profile 启动边界、Agent SQLite 打开/备份/迁移/恢复证据、CredentialVault 安全边界和 renderer 命令注册隔离；未进入 Task 3 的 permission broker、审批流程或 secret renderer API。

## Review round 1 继承改动审计

本轮开始时相对 `3df8d9cb459a848e4b5250fc759c7246c815d320` 已有 4 个 Rust 文件的 **inherited partial changes**：

- `src-tauri/src/lib.rs`：仅 Ready 初始化 AgentStore/default profile，非 Ready 使用只读 ProfileRepository，并保留结构化 Foundation bootstrap error。
- `src-tauri/src/agent_store/mod.rs`：既有库只读探测、verified backup、应用 writer coordinator、WAL/rollback journal/活动 writer 测试主体。
- `src-tauri/src/agent_store/model.rs`：`agent_store_writer_active` 阻塞错误。
- `src-tauri/src/profile/repository.rs`：只读仓库写入口阻断。

这些改动逐行审计后保留了正确部分；它们及其已有测试不记作本代理观察到的 RED。补充的 legacy 字节级读取测试、独立 stable/legacy tree 零改动测试和实际 fixture 值扫描首次运行即通过，记录为 inherited behavior regression GREEN，不冒充 RED。

## TDD 证据

### Review round 1 新增 RED

1. 外部 WAL writer 测试先失败：迁移锁竞争被映射为 `agent_store_migration_failed`，而不是明确的 `agent_store_writer_active`；最小修复仅重新分类 SQLite busy/locked，源 DB 与 WAL bytes/hash 保持不变。
2. zeroize 类型边界测试先编译失败：crate 没有直接固定 `zeroize` 依赖，`SecretValue`/backend 私有错误也没有 `ZeroizeOnDrop` 类型保证。
3. 平台 access classifier 测试先编译失败：不存在 typed platform-code 分类器，原实现仍把所有 `NoStorageAccess` 归为 Locked。
4. renderer 注册边界测试先编译失败：不存在 `RENDERER_COMMAND_NAMES` 编译期 allowlist，注册清单无法与审计清单共享单一来源。

### Review round 1 GREEN

- Foundation 4 条边界：4/4 passed（MigrationRequired、MigrationConflict、deferred unresolved、结构化恢复与独立路径 restore）。
- Agent store 专项：14 passed，0 failed。
- Profile repository 专项：6 passed，0 failed。
- Credentials 专项：10 passed，0 failed。
- 全量 Rust（沙箱外）：169 passed，0 failed；main 0 tests；doc-tests 0 tests。
- 沙箱内同一全量命令：164 passed、5 failed，失败均为既有 loopback bind `EPERM`；沙箱外原命令全部通过。

## 5 个 Important 修复与安全保证

1. **Non-ready Foundation 零持久化初始化**
   - `MigrationRequired`、`MigrationConflict` 和 defer 后未解决状态均不创建 Agent DB、backup 目录、profiles 目录或默认 profile。
   - 仅 `Ready` 调用 `create_owned_directories`、打开 AgentStore、恢复 Profile transaction 并按需创建默认 profile。
   - stable root 与独立 legacy root 均以 byte-level tree snapshot 验证无变化；非 Ready ProfileRepository 的所有写入口 fail closed。

2. **结构化 RecoveryState 不丢失**
   - Foundation bootstrap error 保留 source path 和 verified backup 的 path、SHA-256、byte length、schema version、sidecar path。
   - 集成测试把 backup 复制到独立临时恢复路径，验证 `integrity_check = ok`、`user_version` 和原始数据；测试明确验证源库 bytes 不变，从不覆盖源路径。

3. **SQLite 只读探测、备份后迁移和 writer 协调**
   - 已存在 DB 先以 `READ_ONLY` 且无 `CREATE` flag 探测 `user_version`；仅需迁移时通过只读连接生成 SQLite 一致备份，完成 integrity/hash/length/sidecar 后才打开 `READ_WRITE`。
   - 应用内 writer 使用明确 coordinator lease；迁移期间持有 coordinator 锁，已有或新 writer 均被阻断。
   - 外部/未知 writer 不声称可关闭：SQLite exclusive transaction busy/locked 时返回 `agent_store_writer_active` 并 fail closed。
   - 覆盖 committed WAL、活动 WAL writer、exclusive writer、应用 writer lease、非空 rollback journal、只读 v0、强制 schema 失败；失败路径验证源 bytes/hash（WAL 路径还验证 WAL bytes）不变。

4. **Keyring Locked/Unavailable 保守分类**
   - 已核对本地锁定版本的 `keyring 4.1.6`、`keyring-core 1.0.0`、Apple 和 Windows backend 源码。
   - 只把可通过 typed `security_framework::base::Error` 验证的 macOS `errSecInteractionNotAllowed (-25308)` 映射为 Locked。
   - macOS write-permission/read-only/no-keychain/invalid access codes均映射 Unavailable；Windows `ERROR_NO_SUCH_LOGON_SESSION (1312)` 因 keyring v1 未公开稳定可下转类型而保守映射 Unavailable；ambiguous/unclassified/no-session/read-only/unavailable 均不误报 Locked。
   - 分类不读取或匹配错误字符串。测试使用纯 classifier 和构造的 typed error，不访问真实 Keychain/Credential Manager。

5. **compiler-safe secret zeroing**
   - 直接依赖精确固定 `zeroize = 1.9.0`。
   - `SecretValue` 和 backend 私有错误实现 `Zeroize`、drop 清零及 `ZeroizeOnDrop` 类型边界。
   - test-only in-memory backend 的当前值和覆盖旧值使用 `Zeroizing<Vec<u8>>`；覆盖、删除和 state drop 均由 zeroize drop 保证。
   - `SecretValue` 仍不实现 `Debug`、`Display`、`Serialize`、`Deserialize` 或 `Clone`；跨 vault 的错误只暴露固定脱敏 code。

## 2 个 Minor 修复与安全保证

1. **Legacy Profile 真正 byte-level no-op**
   - 旧 `profiles.json`/`state.json` 通过只读 load 后逐字节一致，且无 atomic temp file。
   - 明确断言旧 `read-only`/`workspace-write` 权限值、缺省 `agentPermissionDefault = request-approval` 和省略字段兼容。
   - 没有无证据改变既有 empty patch revision/time 语义。

2. **实际 fixture/value 扫描与 renderer 隔离**
   - AgentStore 测试迁移真实 v0 fixture，逐表逐 TEXT 列扫描实际值，确认 `preserve-me` 确实被读取并拒绝 secret-bearing schema/value，而非只检查 `sqlite_master`。
   - renderer 命令以单一编译期宏同时生成 Tauri handler 和审计 allowlist；逐项拒绝 credential/secret 以及 `resolve/get/fetch/read` 前缀，并检查 command source 不引用 `SecretValue`/`CredentialVault`。
   - 未新增任何 secret command 或 secret 返回类型。

## 依赖

- `rusqlite = =0.40.1`，features `backup + bundled`。
- `keyring = =4.1.6`，feature `v1`。
- `zeroize = =1.9.0`。
- macOS typed code extraction：`security-framework = =3.7.0`。

## 最终验证

```text
cargo test -q --manifest-path src-tauri/Cargo.toml agent_store::tests --locked
  14 passed; 0 failed; 155 filtered out

cargo test -q --manifest-path src-tauri/Cargo.toml profile::repository::tests --locked
  6 passed; 0 failed; 163 filtered out

cargo test -q --manifest-path src-tauri/Cargo.toml credentials::tests --locked
  10 passed; 0 failed; 159 filtered out

Foundation exact boundary tests
  4 passed; 0 failed (four individually filtered runs)

cargo test --manifest-path src-tauri/Cargo.toml --locked
  lib: 169 passed; 0 failed
  main: 0 tests
  doc-tests: 0 tests

rustfmt --edition 2024 --check --config skip_children=true <all changed Rust files>
  passed; no output

git diff --check
  passed; no output
```

## 残余风险与边界

- 按安全约束未对真实 macOS Keychain 或 Windows Credential Manager 执行读写删除；macOS 使用构造的 typed platform error，Windows 使用纯 classifier mock。
- keyring v1 没有公开 Windows backend 私有错误的稳定下转类型，因此 Windows no-session 等当前保守返回 Unavailable；后续若上游提供稳定 typed code，可在不使用错误字符串的前提下收窄分类。
- 应用内 writer 可精确协调；外部 writer 不能被本应用安全关闭，只能依赖 SQLite 锁检测并 fail closed。本实现不声称关闭未知 writer。
- 未执行自动恢复或覆盖用户数据库；只返回结构化恢复证据并验证备份可复制到独立路径恢复，这是有意的数据安全边界。
- CredentialVault 仍是 Task 2 内部契约，除 Foundation 构造外尚无生产调用方；未为消除 dead-code warning 提前实现 Task 3。
