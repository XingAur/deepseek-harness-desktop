# Task 2 实现报告

## 状态

Task 2 第二轮审查修复实现已收口，最终验证结果见文末。范围仅包含 Foundation/Profile 启动边界、Agent SQLite 打开/备份/迁移/恢复证据、CredentialVault 安全边界和 renderer 命令注册隔离；未进入 Task 3 的 permission broker、审批流程或 secret renderer API。

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

## Review round 2 TDD 证据

### 真实 RED（不把继承行为或首次通过冒充 RED）

1. 前端新增 defer/recovery 流测试首次运行是 `27 passed, 2 failed`：defer 仍调用 `bootstrapRuntime`，且 runtime contract/client/UI 没有 recovery DTO 边界。
2. Foundation/Tauri 测试首次编译失败：缺少 `RecoveryBlocked` 状态、`recovery_status` command 和序列化 DTO；这是壳层与 command 边界的真实 RED。
3. 按审查建议实测“同一 connection 的 deferred read snapshot + backup + transaction upgrade”：在已建立事务的 connection 上调用 rusqlite backup 返回 SQLite `database is locked`（`SQLITE_BUSY`），证明该具体方案不可用。
4. 原子发布 fault-injection 测试先编译失败：缺少 backup filesystem seam、唯一 temp bundle 和 write/fsync/rename failure points。
5. native adapter、二进制 secret API 与 byte-bearing keyring error 测试先编译失败：缺少 `NativeStoreAdapter`/`KeyringBackend`，生产路径仍未形成可 mock 的 `get_secret`/`set_secret` 边界。
6. empty patch 测试在实现前加入；首次 crate build 被同批 credentials 缺失类型阻断，因此不声明观察到了独立的行为 RED。旧代码逐行确认会递增 revision/updated_at 并写文件，随后仅增加真正空 patch 的早返回。

### GREEN

- AgentStore：20 passed，覆盖生产 migration barrier、rollback/WAL 协议实验、原子 bundle failure injection、16 MiB 流式 hash、恢复重新验证和真实 crash hot journal。
- Credentials：12 passed，覆盖 binary native adapter、BadEncoding/BadDataFormat、typed platform classifier、zeroize 边界及 renderer allowlist。
- ProfileRepository：7 passed，覆盖 empty patch 的 profiles/state bytes、revision、updated_at 完整 no-op。
- Foundation：6 passed，覆盖 recovery-blocked 壳层、真实 Tauri mock invoke、tamper 后固定错误、deferred 状态和零新增持久化边界。
- DesktopCoordinator deferred + read-only repository 集成：1 passed；launcher 调用数为 0，profiles/state bytes 不变。
- 前端 App：29 passed；defer/recovery 均不 bootstrap，恢复证据被明确呈现且无自动覆盖源库按钮。

## Review round 2：5 个 Important 修复与安全保证

1. **实际 recovery 壳层和经验证 DTO**
   - AgentStore 打开失败不再让 Tauri setup `expect`/退出；Foundation 转为 `RecoveryBlocked(RecoveryState)`，仅保留只读 ProfileRepository、Foundation、窗口和不依赖 Agent runtime 的最小壳层。
   - `recovery_status` 是只读且唯一新增的 renderer command，返回 source、backup、SHA-256、length、schema、sidecar；不返回 credential/secret，也没有新增 secret command。
   - 每次 command 返回前重新 canonicalize 并验证 backup/sidecar containment、固定 bundle 文件名、sidecar 全字段一致、文件存在、64 KiB 流式 SHA-256/length、SQLite `integrity_check` 与 `user_version`。任何漂移只返回固定 `恢复证据验证失败`，不透传路径解析或 SQLite 错误细节。
   - UI 明确显示阻断状态和六项恢复证据；没有自动恢复或覆盖源库按钮。Agent runtime/profile/project 写命令要么显式通过 `runtime_allowed`，要么因 blocked setup 不注册 coordinator/launcher 而 fail closed。

2. **defer 仍是 blocked/deferred**
   - Rust `migration_status` 返回独立 `deferred`，不再伪报 Ready；`runtime_allowed` 对 defer 和所有非 Ready Foundation 返回不可恢复的固定阻断错误。
   - renderer defer 后只显示“迁移已暂缓”，禁止 `bootstrapRuntime`。真实只读 ProfileRepository + DesktopCoordinator 集成测试证明启动在 launcher 前失败且无 profile/state 写入。

3. **消除 backup→migration TOCTOU**
   - 先以 `READ_ONLY | NO_CREATE` 探测版本；需要迁移时打开 RW connection 并取得 `BEGIN IMMEDIATE` writer reservation，重新读取并比对版本。
   - 在 reservation 持有期间，由独立 RO connection 取得 SQLite 一致 snapshot 并完成 verified backup；随后在原 RW transaction 中直接执行 migration 和 commit。SQLite 只允许一个 writer，因此 rollback 与 WAL 下外部 writer 都不能在 backup snapshot 和 migration 之间提交。
   - 确定性 barrier 放在 bundle 已 rename、schema migration 尚未开始的精确窗口；竞争 writer 的 commit 被 SQLite 锁阻断，backup 包含锁前全部已提交数据且不包含未提交竞争写，source 最终升级到 v1。
   - 本协议不把进程 mutex 当外部 writer 安全保证；mutex 仅协调本应用 writer。外部/未知 writer 无法取得 SQLite writer reservation 时返回 `agent_store_writer_active` 并 fail closed。

4. **backup + sidecar 原子可信发布**
   - SQLite backup、integrity、流式 hash、sidecar 均在唯一 `.<uuid>.tmp` bundle 内完成；同步 backup/sidecar 文件和 bundle 目录后，原子 rename 整个目录，再同步 backup parent 目录。
   - sidecar write、任一 file fsync、bundle-dir fsync、rename、parent-dir fsync 注入失败均不返回 backup metadata，不留下正式命名 bundle，源 DB bytes 不变。parent-dir fsync 失败只清理本次已发布 bundle，不删除碰巧同名的既有数据。
   - 16 MiB fixture 验证 hash/length 走固定 64 KiB 流式读取；不再把完整 DB 装入内存。

5. **keyring binary API 与错误 bytes 清零**
   - 锁定 `keyring 4.1.6` 的 v1 API 使用 `set_secret/get_secret`，SecretValue 保持 opaque bytes，不再经过 UTF-8 password 解码路径。
   - `BadEncoding(Vec<u8>)` 和 `BadDataFormat(Vec<u8>, ...)` 在映射固定 code 前显式 `zeroize()`；backend 私有错误继续 `ZeroizeOnDrop`，不使用错误字符串分类。
   - Native adapter error-path 完全由 mock 驱动，覆盖非 UTF-8 round-trip 和两个 byte-bearing error；没有访问真实 Keychain/Credential Manager。

## Review round 2：3 个 Minor 修复与安全保证

1. **真实 SQLite hot rollback journal**
   - 子进程创建 DB、`BEGIN IMMEDIATE`、更新 8 MiB payload 后 `abort`，产生真实 non-empty hot journal；父测试确认 AgentStore 安全阻断且 DB/journal bytes 均保持不变，不再手写伪 journal 字符串。
2. **empty patch 真正 no-op**
   - 所有 patch 字段均为 `None` 时，在 existence/revision 校验后直接返回当前 Profile；profiles/state bytes、revision、updated_at 全部不变。任一非空字段仍沿用既有校验、revision+1 和 atomic write 语义。
3. **统一 secret-bearing 词表**
   - schema、列名和真实 fixture 值共用闭合词表，覆盖 `client_secret`、`refresh_token`、`authorization`、`bearer`、`private_key`，以及 context 中 password/token/session/access token/API key 等 snake/camel/紧凑形式。

## Review round 2 残余风险与明确边界

- 同 connection transaction 内直接执行 rusqlite backup 经真实 SQLite 实验返回 `SQLITE_BUSY`，因此采用“RW `BEGIN IMMEDIATE` reservation + 独立 RO snapshot backup + 原 transaction migration”的等价协议；这是 SQLite 锁保证，不是进程 mutex 猜测。
- recovery DTO 只在存在已发布且重新验证成功的 backup bundle 时返回。真实 hot journal 等没有可信 backup 的阻断状态不会伪造证据，command 只返回固定验证失败；仍需人工诊断。
- 恢复路径只展示证据，不自动覆盖、删除或迁移源 DB。实际人工恢复工具/流程不属于 Task 2，未进入 Task 3。
- native secure-store 测试坚持纯 mock；Windows keyring v1 没有稳定可下转平台码时继续保守映射 Unavailable。

## Review round 2 最终验证

```text
cargo test -q --locked agent_store::tests
  20 passed; 0 failed; 159 filtered out

cargo test -q --locked credentials::tests
  12 passed; 0 failed; 167 filtered out

cargo test -q --locked profile::repository::tests
  7 passed; 0 failed; 172 filtered out

cargo test -q --locked foundation_tests
  6 passed; 0 failed; 173 filtered out

cargo test -q --locked deferred_read_only_repository_fails_before_coordinator_launch_or_state_write
  1 passed; 0 failed; 178 filtered out

npm test -- --run src/App.test.tsx
  1 test file passed; 29 tests passed

cargo test --locked（沙箱外，允许 loopback bind）
  lib: 179 passed; 0 failed
  main: 0 tests
  doc-tests: 0 tests

npm run check（沙箱外，允许 loopback bind）
  root: 37 files / 245 tests passed
  desktop plugin: 15 files / 69 tests passed
  agent adapter: 3 files / 25 tests passed
  build:web、plugin:build、agent:build 均通过

同一全量命令在 sandbox 内的对照结果
  Rust: 174 passed; 5 loopback bind tests failed with EPERM
  root Vitest: 235 passed; 10 loopback fixture tests failed with listen EPERM
  沙箱外原命令全部通过，确认不是实现回归

rustfmt --edition 2024 --check --config skip_children=true <全部变更 Rust 文件>
  passed; no output

git diff --check
  passed; no output
```

## Review round 3 工作区基线与 TDD 证据

- 基线 HEAD 为 `16bb2b638a908ef1bfe22b692c558b75d9eeb129`。开始时除两份 controller-owned 未跟踪设计文档外，`agent_store/mod.rs` 与 `agent_store/model.rs` 还存在 inherited partial Round 3 改动；逐行审计后保留正确部分，没有回退用户改动。
- inherited partial 中已有“同名 final 预占、temp/final symlink swap、cleanup replacement、parent fsync + cleanup failure”测试及部分实现，基线 AgentStore 专项为 24 passed；这些不冒充本代理观察到的 RED。
- 本轮新增 recovery A→B barrier、no-follow source open、compound secret detector 测试后，首次运行因 `validate_recovery_state_with_barrier`、`open_recovery_source`、`contains_secret_bearing_term` 均不存在而编译失败，Rust `E0425`，exit 101。这是本轮第一组真实 RED。
- 独立审查新增 Windows final identity 契约和“发布后异常必须保留结构化证据”断言；平台契约测试首次运行在 `windows_publish.contains("path_identity(to)?")` 失败，exit 101。这是第二组真实 RED。实现最终采用不产生发布状态歧义的 `BundlePublication::{Verified, DurabilityUncertain}`，测试随后 GREEN。
- 中途有一次测试源码自扫描错误，把测试区自己的 `final_bundle.exists()` 当成生产代码；收窄到 `#[cfg(test)] mod tests` 之前的生产区后通过。该项是测试修正，不记作产品 RED。

## Review round 3：2 个 Important 修复与安全保证

1. **真正 atomic no-replace bundle publish 与安全证据保留**
   - macOS 使用 `libc::renameatx_np(..., RENAME_EXCL)`；Linux 使用 `libc::renameat2(..., RENAME_NOREPLACE)`；Windows 使用 `SetFileInformationByHandle(FileRenameInfo)`，`ReplaceIfExists = false`，source directory handle 带 `FILE_FLAG_WRITE_THROUGH | FILE_FLAG_OPEN_REPARSE_POINT`。全部按 target `cfg` 隔离，不存在 `exists() + rename` 竞态回退；其他 Unix target 明确返回 Unsupported。
   - `libc` 精确固定为 `=0.2.189`，`windows-sys` 精确固定为 `=0.61.2`；两者本地锁定 crate manifest 均为 `MIT OR Apache-2.0`。
   - backup parent 以 no-follow directory handle 固定并保存 filesystem identity；Unix 使用 dev+ino+uid，Windows 使用 volume serial + file index。parent、temp、final 均拒绝 symlink，Windows 额外拒绝 reparse point；发布前后回验 parent/final identity。
   - temp bundle 名含随机 owner token，cleanup 同时校验 token 与 bundle filesystem identity。若名字已被替换，cleanup fail closed，不删除 replacement。
   - 原子 syscall 前失败才允许清理 owned temp。syscall 成功后任何 final/parent identity、write-through、parent fsync 不确定均返回 `agent_store_backup_durability_uncertain`，携带完整 `BackupMetadata` 并保留正式 bundle；不再尝试可能失败并丢失恢复证据的 post-publish cleanup。
   - 覆盖同名 final 预占不覆盖、temp/final symlink swap、cleanup replacement 不删除、parent fsync 失败、parent fsync 且 cleanup 确认不可行时仍保留并可重新验证的正式证据。

2. **固定 source handle 的 recovery validation**
   - recovery source 通过 `O_NOFOLLOW`（Unix）或 `FILE_FLAG_OPEN_REPARSE_POINT`（Windows）打开并拒绝 symlink/reparse，随后只从同一固定 handle 以 64 KiB buffer 流式 SHA-256 并复制到 `create_new`、私有权限的 verification copy。
   - SQLite `integrity_check` 与 `user_version` 只在 verification copy 上执行，不再重新打开原 recovery source path。
   - Unix source snapshot 比较 dev+ino、owner、size、mode、links、group、mtime/ctime；Windows 比较 volume/file identity、size、attributes、creation/last-write/change time、links。复制/校验前后同时比较 source handle 与 source path snapshot，并回验 trusted root/bundle 和 verification copy identity。
   - 确定性 barrier 在 source A 已复制、SQLite copy 校验前把原路径替换为 B；即使 B 是有效 SQLite，最终 source path identity/metadata 不一致仍 fail closed。source symlink 同样在 open 阶段拒绝。

## Review round 3：2 个 Minor 修复与安全保证

1. secret detector 按 separator、snake_case、hyphen-case、camelCase/acronym 和指定 compact compound 边界拆分；覆盖 `private-key/private_key/privateKey/privatekey`、client/refresh/session/access token、API key、authorization、bearer、password，同时明确拒绝 `tokenizer`、`secretary`、`tokenized`、`keynote`、`monkey`、`publicKey` 误报。
2. 删除生产 `SecretValue::expose_for_backend(&str)`；所有测试与 backend 边界只使用 bytes accessor。生产 secret model 中不再存在 `from_utf8(...).expect(...)` 路径。

## Review round 3 平台编译证据与边界

- 当前工具链为 Homebrew `rustc 1.98.0`，host `aarch64-apple-darwin`，无 `rustup`；macOS target 已由专项与全量测试真实编译和执行。
- 尝试 `cargo check --locked --target x86_64-unknown-linux-gnu --lib` 与 `cargo check --locked --target x86_64-pc-windows-gnu --lib`，均在编译业务代码前失败：Rust `E0463: can't find crate for core`，提示 target 未安装；本机无 `rustup`，因此不能声称 Linux/Windows target 已交叉编译。
- 安全替代证据为：精确锁定 crate 本地源码中核对 `libc 0.2.189` 的 `renameatx_np/RENAME_EXCL/renameat2/RENAME_NOREPLACE`，以及 `windows-sys 0.61.2` 的 `FILE_RENAME_INFO`、bool `ReplaceIfExists`、`SetFileInformationByHandle`、`FileRenameInfo` 与 `FILE_FLAG_WRITE_THROUGH` 签名/常量。未退回竞态 API；目标缺失时 fail closed。

## Review round 3 最终验证

```text
cargo test --locked agent_store::tests
  29 passed; 0 failed; 160 filtered out

cargo test --locked credentials::tests
  13 passed; 0 failed; 176 filtered out

cargo test --locked foundation_tests
  6 passed; 0 failed; 183 filtered out

npm test -- --run src/App.test.tsx
  1 test file passed; 29 tests passed

cargo test --locked
  lib: 189 passed; 0 failed
  main: 0 tests
  doc-tests: 0 tests

npm run check
  root: 37 files / 245 tests passed
  desktop plugin: 15 files / 69 tests passed
  agent adapter: 3 files / 25 tests passed
  build:web、plugin:build、agent:build 均通过
```
