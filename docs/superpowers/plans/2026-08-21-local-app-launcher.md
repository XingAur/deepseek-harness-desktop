# 本地应用启动器（Local App Launcher）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 双击"本地项目"卡片时，把 agent 构建的项目作为可操作、数据留存的本地应用在主窗口内启动（无清单则回退打开会话）。

**Architecture:** Rust 新增 `apps` 模块（清单校验、受管 spawn、静态服务、运行注册表、事件）；通过桌面桥新动作 `app.launch/app.stop/app.status` 触发，`local-app-event` 事件驱动 `App.tsx` 在主窗口内切换"应用视图"表面（隐藏不卸载工作台 iframe）；插件侧做收录过滤（项目根目录 ∪ `localApp` 标记）、角标、双击分支与收录对话框。

**Tech Stack:** Rust（tauri 2 / tokio / reqwest / chrono / serde，全部已在 `src-tauri/Cargo.toml`）、React + TypeScript（vitest + @testing-library/react）、e2e（wdio + CDP）。

**规格：** `docs/superpowers/specs/2026-08-21-local-app-launcher-design.md`（已确认）。

**关键既有事实（写代码时直接依赖）：**
- 受管 Runtime 激活指针：`RuntimePaths.current` = `<data>/runtime/current.json`，内容 `{"version": "<semver>", ...}`；版本目录 = `paths.version_dir(&version)`。
- 版本目录内：Windows `node.exe`、`pnpm.cmd`；macOS `bin/node`、`pnpm`；pnpm 实体在 `app/node_modules/pnpm/bin/pnpm.cjs`。
- workspace 注册表：`<profile.data_root>/storages/workspace.json`（`global.workspaceIds` + `tables.workspaces[id].path`）。
- 项目根：`<文档>/DeepSeek Harness/Projects`（`projects/location.rs` 的 `projects_root`，私有；状态查询侧用 `documents.join("DeepSeek Harness").join("Projects")` 只读拼接）。
- 事件模式：`app.emit("local-app-event", payload)`（`use tauri::Emitter`）；壳层 `listen`（`runtime-client.ts`）。
- 桥接模式：插件 postMessage → `workbench-bridge.ts`（action→命令映射 + 载荷校验）→ Tauri 命令（`generationId` 校验）。
- 进程工具：`runtime/process.rs` 的 `reserve_loopback_port()`、`pipe_log`（私有）、`terminate_tree`（私有）、`CREATE_NO_WINDOW`（Windows，私有）。

---

## 文件结构（改动地图）

```
src-tauri/src/apps/mod.rs            新建：模块声明与公共类型再导出
src-tauri/src/apps/manifest.rs       新建：dsh-app.json 解析与校验
src-tauri/src/apps/static_server.rs  新建：内置回环静态文件服务
src-tauri/src/apps/launcher.rs       新建：运行注册表 + launch/stop/status + 事件
src-tauri/src/runtime/process.rs     修改：pipe_log/terminate_tree/CREATE_NO_WINDOW → pub(crate)
src-tauri/src/projects/recycle.rs    修改：新增 registered_workspace_records（id→路径，跳过不可访问项）
src-tauri/src/projects/metadata.rs   修改：ProjectMetadataPatch/ProjectMetadata 增加 localApp
src-tauri/src/commands.rs            修改：app_launch/app_stop/app_status；orderly_quit 等挂 stop_all
src-tauri/src/lib.rs                 修改：mod apps；装配 AppLauncher；命令注册；退出钩子
src/bridge-contract.ts               修改：3 个新动作与命令映射
src/workbench-bridge.ts              修改：app.launch/app.stop 载荷校验
src/runtime-contract.ts              修改：LocalAppEvent 类型 + RuntimeClient.subscribeLocalAppEvents
src/runtime-client.ts                修改：listen('local-app-event')
src/App.tsx                          修改：应用视图表面 + 受信条 + 事件订阅
src/app.css                          修改：localAppSurface/Strip 样式 + iframe 隐藏
src/App.test.tsx                     修改：fakeRuntime 增加 subscribeLocalAppEvents + 新用例
packages/dsh-plugin-desktop/src/client/desktop-bridge.ts   修改：3 个新动作类型
packages/dsh-plugin-desktop/src/client/project-model.ts    修改：ProjectMetadataEntry.localApp
packages/dsh-plugin-desktop/src/client/LocalProjectsPage.tsx 修改：app.status/过滤/双击分支/收录
packages/dsh-plugin-desktop/src/client/ProjectCard.tsx     修改：角标 + 新回调
packages/dsh-plugin-desktop/src/client/ProjectContextMenu.tsx 修改：打开会话继续开发/停止应用
packages/dsh-plugin-desktop/src/client/AdoptProjectDialog.tsx  新建：收录已有项目对话框
packages/dsh-plugin-desktop/src/client/project-controller.ts   修改：buildPrompt 收尾要求
packages/dsh-plugin-desktop/src/client/ProjectDeleteDialog.tsx 修改：数据随目录文案
packages/dsh-plugin-desktop/src/client/styles.ts           修改：角标/收录行样式
packages/dsh-plugin-desktop/tests/*.spec.tsx               修改/新建：对应测试
e2e/support/desktop.ts / e2e/specs/local-app-launch.e2e.ts  修改/新建：e2e 助手与用例
```

---

### Task 1: Rust — `apps::manifest` 清单解析与校验

**Files:**
- Create: `src-tauri/src/apps/mod.rs`
- Create: `src-tauri/src/apps/manifest.rs`

- [ ] **Step 1: 写失败测试（含模块骨架）**

创建 `src-tauri/src/apps/mod.rs`：

```rust
pub mod launcher;
pub mod manifest;
pub mod static_server;
```

（`launcher.rs`、`static_server.rs` 先建空文件 `// TODO(task 2/4)` 占位会导致编译失败——本任务先只声明 `pub mod manifest;`，Task 2/4 再逐个加行。）

创建 `src-tauri/src/apps/manifest.rs`：

```rust
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::runtime::{RuntimeFailure, paths::validate_relative_path};

pub const MANIFEST_FILE: &str = "dsh-app.json";
const MAX_ARGS: usize = 16;
const MAX_ARG_CHARS: usize = 512;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AppKind {
    Web,
    Static,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AppManifest {
    pub kind: AppKind,
    pub start: Vec<String>,
    pub port_env: String,
    pub health_path: String,
    pub data_dir: PathBuf,
    pub static_dir: Option<PathBuf>,
}

#[derive(Deserialize)]
struct ManifestFile {
    #[serde(rename = "schemaVersion")]
    schema_version: u32,
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    start: Vec<String>,
    #[serde(rename = "portEnv", default)]
    port_env: Option<String>,
    #[serde(rename = "healthPath", default)]
    health_path: Option<String>,
    #[serde(rename = "dataDir", default)]
    data_dir: Option<String>,
    #[serde(rename = "staticDir", default)]
    static_dir: Option<String>,
}

pub fn read_manifest(project_dir: &Path) -> Result<Option<AppManifest>, RuntimeFailure> {
    let path = project_dir.join(MANIFEST_FILE);
    let bytes = match std::fs::read(&path) {
        Ok(bytes) => bytes,
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(cause) => {
            return Err(RuntimeFailure::internal(format!(
                "无法读取 dsh-app.json：{cause}"
            )))
        }
    };
    parse_manifest(&bytes).map(Some)
}

pub fn parse_manifest(bytes: &[u8]) -> Result<AppManifest, RuntimeFailure> {
    let invalid = |message: String| RuntimeFailure::internal(format!("dsh-app.json 无效：{message}"));
    let file: ManifestFile = serde_json::from_slice(bytes)
        .map_err(|cause| invalid(format!("JSON 解析失败：{cause}")))?;
    if file.schema_version != 1 {
        return Err(invalid("schemaVersion 仅支持 1".into()));
    }
    let kind = match file.kind.as_str() {
        "web" => AppKind::Web,
        "static" => AppKind::Static,
        other => return Err(invalid(format!("type 仅支持 web/static：{other}"))),
    };
    let start = validate_args(file.start, kind).map_err(invalid)?;
    let port_env = file.port_env.unwrap_or_else(|| "PORT".to_owned());
    if !is_valid_env_name(&port_env) {
        return Err(invalid(format!("portEnv 不是合法环境变量名：{port_env}")));
    }
    let health_path = file.health_path.unwrap_or_else(|| "/".to_owned());
    if !health_path.starts_with('/') || health_path.len() > 256 || health_path.contains('\0') {
        return Err(invalid("healthPath 必须以 / 开头且不超过 256 字符".into()));
    }
    let data_dir = validate_relative_path(
        &file.data_dir.unwrap_or_else(|| "data".to_owned()),
        "dataDir",
    )
    .map_err(|cause| invalid(cause.message))?;
    let static_dir = match kind {
        AppKind::Static => Some(
            validate_relative_path(
                &file.static_dir.unwrap_or_else(|| "dist".to_owned()),
                "staticDir",
            )
            .map_err(|cause| invalid(cause.message))?,
        ),
        AppKind::Web => match file.static_dir {
            Some(_) => return Err(invalid("web 应用不支持 staticDir".into())),
            None => None,
        },
    };
    Ok(AppManifest {
        kind,
        start,
        port_env,
        health_path,
        data_dir,
        static_dir,
    })
}

fn validate_args(start: Vec<String>, kind: AppKind) -> Result<Vec<String>, String> {
    if matches!(kind, AppKind::Static) {
        if !start.is_empty() {
            return Err("static 应用不使用 start".into());
        }
        return Ok(start);
    }
    if start.is_empty() {
        return Err("web 应用必须提供 start".into());
    }
    if start.len() > MAX_ARGS {
        return Err(format!("start 参数过多（上限 {MAX_ARGS}）"));
    }
    for argument in &start {
        if argument.is_empty() || argument.contains('\0') || argument.len() > MAX_ARG_CHARS {
            return Err("start 含空串、NUL 或超长参数".into());
        }
    }
    if !matches!(start[0].as_str(), "node" | "pnpm") {
        return Err(format!("start 首项仅允许 node/pnpm：{}", start[0]));
    }
    Ok(start)
}

fn is_valid_env_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name
            .chars()
            .enumerate()
            .all(|(index, character)| {
                character.is_ascii_alphanumeric() || character == '_'
            })
        && name.chars().next().is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
}

#[cfg(test)]
mod tests {
    use super::{AppKind, parse_manifest};

    fn web_json(body: &str) -> String {
        format!("{{\"schemaVersion\":1,\"type\":\"web\",{body}}}")
    }

    #[test]
    fn accepts_minimal_web_manifest_with_defaults() {
        let manifest = parse_manifest(
            web_json("\"start\":[\"pnpm\",\"run\",\"start\"]").as_bytes(),
        )
        .unwrap();
        assert_eq!(manifest.kind, AppKind::Web);
        assert_eq!(manifest.start, vec!["pnpm", "run", "start"]);
        assert_eq!(manifest.port_env, "PORT");
        assert_eq!(manifest.health_path, "/");
        assert_eq!(manifest.data_dir, std::path::Path::new("data"));
    }

    #[test]
    fn accepts_static_manifest() {
        let manifest = parse_manifest(
            b"{\"schemaVersion\":1,\"type\":\"static\",\"staticDir\":\"dist\",\"dataDir\":\"data\"}",
        )
        .unwrap();
        assert_eq!(manifest.kind, AppKind::Static);
        assert_eq!(manifest.static_dir, Some(std::path::Path::new("dist").into()));
    }

    #[test]
    fn rejects_unknown_schema_type_and_command() {
        assert!(parse_manifest(b"{\"schemaVersion\":2,\"type\":\"web\",\"start\":[\"node\",\"x\"]}").is_err());
        assert!(parse_manifest(web_json("\"start\":[\"npm\",\"start\"]").as_bytes()).is_err());
        assert!(parse_manifest(web_json("\"start\":[\"cmd\",\"/c\",\"echo\"]").as_bytes()).is_err());
        assert!(parse_manifest(web_json("\"start\":[]").as_bytes()).is_err());
    }

    #[test]
    fn rejects_escaping_and_absolute_dirs() {
        assert!(parse_manifest(
            web_json("\"start\":[\"node\",\"a\"],\"dataDir\":\"../out\"").as_bytes()
        )
        .is_err());
        assert!(parse_manifest(
            web_json("\"start\":[\"node\",\"a\"],\"dataDir\":\"C:/tmp\"").as_bytes()
        )
        .is_err());
        assert!(parse_manifest(
            b"{\"schemaVersion\":1,\"type\":\"static\",\"staticDir\":\"..\"}"
        )
        .is_err());
    }

    #[test]
    fn rejects_bad_port_env_and_health_path() {
        assert!(parse_manifest(
            web_json("\"start\":[\"node\",\"a\"],\"portEnv\":\"1BAD\"").as_bytes()
        )
        .is_err());
        assert!(parse_manifest(
            web_json("\"start\":[\"node\",\"a\"],\"healthPath\":\"health\"").as_bytes()
        )
        .is_err());
    }
}
```

- [ ] **Step 2: 在 `src-tauri/src/lib.rs` 模块列表加入 `mod apps;`**（第 1-17 行的 `mod` 声明区，按字母序插在 `mod app_update;` 之后）。

- [ ] **Step 3: 运行测试确认通过**

Run: `cd src-tauri && cargo test apps::manifest`
Expected: `test result: ok. 5 passed`

- [ ] **Step 4: Commit**

```bash
git add src-tauri/src/apps src-tauri/src/lib.rs
git commit -m "feat(apps): parse and validate dsh-app.json launch manifests"
```

---

### Task 2: Rust — `apps::static_server` 内置静态服务

**Files:**
- Create: `src-tauri/src/apps/static_server.rs`
- Modify: `src-tauri/src/apps/mod.rs`（加 `pub mod static_server;`）

- [ ] **Step 1: 写实现（含测试）**

创建 `src-tauri/src/apps/static_server.rs`：

```rust
use std::path::{Component, Path, PathBuf};

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

use crate::runtime::RuntimeFailure;

const MAX_HEADER_BYTES: usize = 8 * 1024;

/// 在预留回环端口上托管目录；返回前已 bind，调用方负责保留 JoinHandle 以便 abort。
pub async fn serve_static(dir: PathBuf, port: u16) -> Result<(), RuntimeFailure> {
    let listener = TcpListener::bind(("127.0.0.1", port))
        .await
        .map_err(|cause| RuntimeFailure::internal(format!("静态服务端口绑定失败：{cause}")))?;
    loop {
        let (stream, _) = match listener.accept().await {
            Ok(accepted) => accepted,
            Err(_) => break,
        };
        let dir = dir.clone();
        tokio::spawn(handle_connection(stream, dir));
    }
    Ok(())
}

async fn handle_connection(mut stream: TcpStream, dir: PathBuf) {
    let Some(request) = read_request(&mut stream).await else {
        return;
    };
    let (method, raw_path) = request;
    let Some(relative) = safe_relative_path(&raw_path) else {
        write_simple(&mut stream, 400, "Bad Request", b"invalid path").await;
        return;
    };
    let file = if relative.as_os_str().is_empty() {
        dir.join("index.html")
    } else {
        dir.join(&relative)
    };
    if !confined(&dir, &file) {
        write_simple(&mut stream, 400, "Bad Request", b"invalid path").await;
        return;
    }
    match tokio::fs::read(&file).await {
        Ok(body) => {
            let head_only = method == "HEAD";
            let mut head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                content_type(&file),
                body.len()
            )
            .into_bytes();
            if head_only {
                let _ = stream.write_all(&head).await;
            } else {
                head.extend_from_slice(&body);
                let _ = stream.write_all(&head).await;
            }
        }
        Err(_) => write_simple(&mut stream, 404, "Not Found", b"not found").await,
    }
}

async fn read_request(stream: &mut TcpStream) -> Option<(String, String)> {
    let mut buffer = Vec::with_capacity(1024);
    let mut chunk = [0u8; 1024];
    loop {
        let read = stream.read(&mut chunk).await.ok()?;
        if read == 0 || buffer.len() + read > MAX_HEADER_BYTES {
            return None;
        }
        buffer.extend_from_slice(&chunk[..read]);
        if buffer.windows(4).any(|window| window == b"\r\n\r\n") {
            break;
        }
    }
    let text = String::from_utf8_lossy(&buffer);
    let mut parts = text.split_whitespace();
    let method = parts.next()?.to_owned();
    let target = parts.next()?.to_owned();
    if method != "GET" && method != "HEAD" {
        return None;
    }
    let path = target.split('?').next().unwrap_or("/").to_owned();
    Some((method, path))
}

/// 解码百分号并确保结果是可以安全 join 的相对路径；返回空 PathBuf 表示根。
fn safe_relative_path(raw: &str) -> Option<PathBuf> {
    let mut decoded = String::with_capacity(raw.len());
    let bytes = raw.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'%' if index + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).ok()?;
                let value = u8::from_str_radix(hex, 16).ok()?;
                decoded.push(value as char);
                index += 3;
            }
            b'\\' | b'\0' => return None,
            character => {
                decoded.push(character as char);
                index += 1;
            }
        }
    }
    if !decoded.starts_with('/') {
        return None;
    }
    let relative = decoded.trim_start_matches('/');
    if relative.contains("..") || relative.contains('\\') || relative.contains('\0') {
        return None;
    }
    Some(PathBuf::from(relative))
}

fn confined(root: &Path, candidate: &Path) -> bool {
    candidate.starts_with(root)
        && !candidate
            .components()
            .any(|part| matches!(part, Component::ParentDir))
}

fn content_type(path: &Path) -> &'static str {
    match path.extension().and_then(|value| value.to_str()).unwrap_or("") {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" => "application/json; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "ico" => "image/x-icon",
        "woff2" => "font/woff2",
        _ => "application/octet-stream",
    }
}

async fn write_simple(stream: &mut TcpStream, status: u16, reason: &str, body: &[u8]) {
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes()).await;
    let _ = stream.write_all(body).await;
}

#[cfg(test)]
mod tests {
    use super::serve_static;
    use crate::runtime::process::reserve_loopback_port;

    #[tokio::test]
    async fn serves_index_and_rejects_traversal() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("index.html"), b"<h1>hi</h1>").unwrap();
        std::fs::create_dir(dir.path().join("assets")).unwrap();
        std::fs::write(dir.path().join("assets").join("a.txt"), b"A").unwrap();
        let port = reserve_loopback_port().unwrap();
        tokio::spawn(serve_static(dir.path().to_path_buf(), port));

        let client = reqwest::Client::new();
        let index = client
            .get(format!("http://127.0.0.1:{port}/"))
            .send()
            .await
            .unwrap();
        assert_eq!(index.status(), 200);
        assert!(index.text().await.unwrap().contains("hi"));

        let asset = client
            .get(format!("http://127.0.0.1:{port}/assets/a.txt"))
            .send()
            .await
            .unwrap();
        assert_eq!(asset.status(), 200);

        let missing = client
            .get(format!("http://127.0.0.1:{port}/missing.js"))
            .send()
            .await
            .unwrap();
        assert_eq!(missing.status(), 404);

        let traversal = client
            .get(format!("http://127.0.0.1:{port}/..%2F..%2Fsecret"))
            .send()
            .await
            .unwrap();
        assert!(traversal.status().as_u16() >= 400);
    }
}
```

- [ ] **Step 2: 运行测试**

Run: `cd src-tauri && cargo test apps::static_server`
Expected: `test result: ok. 1 passed`

- [ ] **Step 3: Commit**

```bash
git add src-tauri/src/apps
git commit -m "feat(apps): builtin loopback static file server for static manifests"
```

---

### Task 3: Rust — 进程工具可见性与 workspace 记录枚举

**Files:**
- Modify: `src-tauri/src/runtime/process.rs`
- Modify: `src-tauri/src/projects/recycle.rs`

- [ ] **Step 1: 调整 `process.rs` 可见性**（不改变实现）

- 第 273 行 `fn pipe_log<R>(...)` → `pub(crate) fn pipe_log<R>(...)`
- 第 296-306 行两个 `async fn terminate_tree(pid: u32)` → `pub(crate) async fn terminate_tree(pid: u32)`
- Windows 块内 `const CREATE_NO_WINDOW: u32 = 0x0800_0000;` → `pub(crate) const CREATE_NO_WINDOW: u32 = 0x0800_0000;`

- [ ] **Step 2: `recycle.rs` 增加 `registered_workspace_records`**（插在 `list_registered_workspaces` 之后，第 218 行 `}` 后）：

```rust
/// 枚举 (workspaceId, canonical path)，跳过不可访问项；用于本地应用的可运行性判定（advisory）。
pub(crate) fn registered_workspace_records(
    profile_root: &Path,
) -> Result<Vec<(String, PathBuf)>, RuntimeFailure> {
    let Some(storage) = read_workspace_storage(profile_root)? else {
        return Ok(Vec::new());
    };
    let mut records = Vec::new();
    for workspace_id in &storage.global.workspace_ids {
        let Some(record) = storage.tables.workspaces.get(workspace_id) else {
            continue;
        };
        let Ok(canonical) = record.path.canonicalize() else {
            continue;
        };
        if canonical.is_dir() {
            records.push((workspace_id.clone(), canonical));
        }
    }
    Ok(records)
}
```

- [ ] **Step 3: 验证编译与既有测试**

Run: `cd src-tauri && cargo check && cargo test projects::`
Expected: 编译通过，projects 测试全绿。

- [ ] **Step 4: Commit**

```bash
git add src-tauri/src/runtime/process.rs src-tauri/src/projects/recycle.rs
git commit -m "refactor: expose process helpers and workspace record enumeration for local apps"
```

---

### Task 4: Rust — `apps::launcher` 运行注册表与启动/停止

**Files:**
- Create: `src-tauri/src/apps/launcher.rs`
- Modify: `src-tauri/src/apps/mod.rs`（加 `pub mod launcher;`）
- Modify: `src-tauri/src/projects/metadata.rs`（localApp 字段）

- [ ] **Step 1: metadata 增加 localApp**

`src-tauri/src/projects/metadata.rs`：

```rust
// ProjectMetadata 结构体（第 24-31 行）改为：
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMetadata {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cover: Option<ProjectCover>,
    pub pinned: bool,
    #[serde(default)]
    pub local_app: bool,
    pub updated_at: DateTime<Utc>,
}

// ProjectMetadataPatch（第 43-48 行）改为：
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMetadataPatch {
    pub cover: Option<ProjectCover>,
    pub pinned: Option<bool>,
    pub local_app: Option<bool>,
}

// patch() 内（第 98-102 行 pinned 块之后）追加：
if let Some(local_app) = patch.local_app {
    project.local_app = local_app;
}

// or_insert_with 的两处初始化（第 93-97 行、测试内若有）补 local_app: false。
```

同文件测试追加：

```rust
#[test]
fn patch_persists_local_app_flag() {
    let dir = tempfile::tempdir().unwrap();
    let repository = ProjectMetadataRepository::new(dir.path().to_path_buf());
    repository
        .patch(
            "w-1",
            ProjectMetadataPatch {
                cover: None,
                pinned: None,
                local_app: Some(true),
            },
        )
        .unwrap();
    assert!(repository.snapshot().unwrap().projects["w-1"].local_app);
}
```

- [ ] **Step 2: 创建 `src-tauri/src/apps/launcher.rs`（完整实现）**

```rust
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    process::Stdio,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use chrono::{DateTime, Utc};
use serde::Serialize;
use tauri::Emitter;
use tokio::process::{Child, Command};
use tokio::sync::Mutex as AsyncMutex;

use crate::apps::manifest::{AppKind, AppManifest, read_manifest};
use crate::apps::static_server::serve_static;
use crate::profile::model::ProfileRecord;
use crate::projects::recycle::{registered_workspace_records, resolve_registered_workspace};
use crate::runtime::{
    RuntimeFailure,
    paths::RuntimePaths,
    process::{pipe_log, reserve_loopback_port, terminate_tree},
};

pub const LOCAL_APP_EVENT: &str = "local-app-event";
const MAX_CONCURRENT_APPS: usize = 5;
const HEALTH_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunningAppInfo {
    pub workspace_id: String,
    pub origin: String,
    pub title: String,
    pub started_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppStatusReply {
    pub projects_root: PathBuf,
    pub running: Vec<RunningAppInfo>,
    pub launchable: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LaunchReply {
    pub workspace_id: String,
    pub origin: String,
    pub title: String,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum LocalAppEventKind {
    Launched,
    Stopped,
    Exited,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalAppEvent {
    pub kind: LocalAppEventKind,
    pub workspace_id: String,
    pub origin: Option<String>,
    pub title: Option<String>,
}

enum Supervision {
    Process {
        pid: u32,
        child: Arc<AsyncMutex<Child>>,
    },
    Static {
        task: tokio::task::JoinHandle<()>,
    },
}

struct RunningApp {
    info: RunningAppInfo,
    supervision: Supervision,
}

/// 事件出口：lib.rs 装配时注入 AppHandle 发射器；单测注入 no-op。
pub type EventSink = Box<dyn Fn(&LocalAppEvent) + Send + Sync>;

pub struct AppLauncher {
    paths: RuntimePaths,
    running: Mutex<HashMap<String, RunningApp>>,
    sink: EventSink,
}

impl AppLauncher {
    pub fn new(paths: RuntimePaths, sink: EventSink) -> Self {
        Self {
            paths,
            running: Mutex::new(HashMap::new()),
            sink,
        }
    }

    fn emit(&self, event: LocalAppEvent) {
        (self.sink)(&event);
    }

    pub fn status(&self, profile: &ProfileRecord, documents: &Path) -> AppStatusReply {
        let running = self
            .running
            .lock()
            .unwrap()
            .values()
            .map(|app| app.info.clone())
            .collect();
        let projects_root = documents.join("DeepSeek Harness").join("Projects");
        let launchable = registered_workspace_records(&profile.data_root)
            .unwrap_or_default()
            .into_iter()
            .filter(|(_, path)| matches!(read_manifest(path), Ok(Some(_))))
            .map(|(workspace_id, _)| workspace_id)
            .collect();
        AppStatusReply {
            projects_root,
            running,
            launchable,
        }
    }

    pub async fn launch(
        self: Arc<Self>,
        profile: &ProfileRecord,
        documents: &Path,
        workspace_id: &str,
    ) -> Result<LaunchReply, RuntimeFailure> {
        let _ = documents;
        if let Some(existing) = self.running.lock().unwrap().get(workspace_id) {
            // 幂等：已在册直接再次广播 launched，让壳层切回应用视图。
            let reply = LaunchReply {
                workspace_id: existing.info.workspace_id.clone(),
                origin: existing.info.origin.clone(),
                title: existing.info.title.clone(),
            };
            self.emit(LocalAppEvent {
                kind: LocalAppEventKind::Launched,
                workspace_id: reply.workspace_id.clone(),
                origin: Some(reply.origin.clone()),
                title: Some(reply.title.clone()),
            });
            return Ok(reply);
        }
        if self.running.lock().unwrap().len() >= MAX_CONCURRENT_APPS {
            return Err(RuntimeFailure::internal("同时运行的本地应用过多，请先停止部分应用"));
        }
        let project_dir = resolve_registered_workspace(&profile.data_root, workspace_id)?;
        let Some(manifest) = read_manifest(&project_dir)? else {
            return Err(RuntimeFailure::internal(
                "该项目没有有效的 dsh-app.json，无法作为本地应用启动",
            ));
        };
        let title = project_dir
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| workspace_id.to_owned());
        let port = reserve_loopback_port()?;
        let origin = format!("http://127.0.0.1:{port}");
        let log_path = self
            .paths
            .logs
            .join("apps")
            .join(format!("{workspace_id}.log"));
        if let Some(parent) = log_path.parent() {
            std::fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?;
        }

        let (supervision, watcher) = match manifest.kind {
            AppKind::Web => {
                let child = spawn_web(&self.paths, &project_dir, &manifest, port, &log_path).await?;
                let pid = child.id().unwrap_or_default();
                let shared = Arc::new(AsyncMutex::new(child));
                (
                    Supervision::Process { pid, child: Arc::clone(&shared) },
                    Some(Watcher::Process(shared)),
                )
            }
            AppKind::Static => {
                let dir = project_dir.join(manifest.static_dir.clone().unwrap_or_default());
                let task = tokio::spawn(async move {
                    let _ = serve_static(dir, port).await;
                });
                (Supervision::Static { task }, None)
            }
        };

        if let Err(cause) = wait_healthy(&origin, &manifest.health_path).await {
            let log = log_path.display().to_string();
            self.terminate_quiet(workspace_id).await;
            return Err(RuntimeFailure::internal(format!(
                "本地应用启动超时或无响应：{cause}；日志：{log}"
            )));
        }

        let info = RunningAppInfo {
            workspace_id: workspace_id.to_owned(),
            origin: origin.clone(),
            title: title.clone(),
            started_at: Utc::now(),
        };
        {
            let mut running = self.running.lock().unwrap();
            if let Some(existing) = running.get(workspace_id) {
                // 并发二次点击：保留先到者，回收新实例。
                drop(running);
                self.terminate_quiet(workspace_id).await;
                let reply = LaunchReply {
                    workspace_id: existing.info.workspace_id.clone(),
                    origin: existing.info.origin.clone(),
                    title: existing.info.title.clone(),
                };
                return Ok(reply);
            }
            running.insert(workspace_id.to_owned(), RunningApp {
                info: info.clone(),
                supervision,
            });
        }

        self.emit(LocalAppEvent {
            kind: LocalAppEventKind::Launched,
            workspace_id: workspace_id.to_owned(),
            origin: Some(origin.clone()),
            title: Some(title.clone()),
        });
        Ok(LaunchReply {
            workspace_id: workspace_id.to_owned(),
            origin,
            title,
        })
    }
}
```

**watcher（健康检查通过并注册后执行）：**

```rust
        if let Some(watcher) = watcher {
            let launcher = Arc::clone(&self);
            let watched = workspace_id.to_owned();
            tokio::spawn(async move {
                loop {
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    let exited = match &watcher {
                        Watcher::Process(child) => {
                            matches!(child.lock().await.try_wait(), Ok(Some(_)))
                        }
                    };
                    if !exited {
                        continue;
                    }
                    let removed = launcher.running.lock().unwrap().remove(&watched);
                    if let Some(app) = removed {
                        launcher.emit(LocalAppEvent {
                            kind: LocalAppEventKind::Exited,
                            workspace_id: watched.clone(),
                            origin: Some(app.info.origin.clone()),
                            title: Some(app.info.title.clone()),
                        });
                    }
                    break;
                }
            });
        }
```

`enum Watcher { Process(Arc<AsyncMutex<Child>>) }` 定义在 `Supervision` 旁。

`stop` / `stop_all` / `terminate_quiet`：

```rust
    pub async fn stop(&self, workspace_id: &str) -> Result<(), RuntimeFailure> {
        let Some(entry) = self.running.lock().unwrap().remove(workspace_id) else {
            return Ok(());
        };
        let mut failure: Option<String> = None;
        match &entry.supervision {
            Supervision::Process { pid, .. } => {
                for _ in 0..2 {
                    terminate_tree(*pid).await;
                    tokio::time::sleep(Duration::from_millis(300)).await;
                    if self.is_process_gone(&entry, pid).await {
                        break;
                    }
                    failure = Some(format!("进程 {pid} 未能终止"));
                }
            }
            Supervision::Static { task } => task.abort(),
        }
        self.emit(LocalAppEvent {
            kind: LocalAppEventKind::Stopped,
            workspace_id: workspace_id.to_owned(),
            origin: Some(entry.info.origin.clone()),
            title: Some(entry.info.title.clone()),
        });
        match failure {
            Some(message) => Err(RuntimeFailure::internal(message)),
            None => Ok(()),
        }
    }

    async fn is_process_gone(&self, _entry: &RunningApp, pid: &u32) -> bool {
        #[cfg(windows)]
        {
            let mut command = tokio::process::Command::new("tasklist");
            command.args(["/FI", &format!("PID eq {pid}")]);
            #[allow(clippy::collapsible_if)]
            {
                let output = command.output().await.map(|o| String::from_utf8_lossy(&o.stdout).to_string());
                return !matches!(&output, Ok(text) if text.contains(&pid.to_string()));
            }
        }
        #[cfg(unix)]
        {
            !std::path::Path::new("/proc").join(pid.to_string()).exists()
        }
    }

    pub async fn stop_all(&self) {
        let ids: Vec<String> = self.running.lock().unwrap().keys().cloned().collect();
        for workspace_id in ids {
            let _ = self.stop(&workspace_id).await;
        }
    }

    async fn terminate_quiet(&self, workspace_id: &str) {
        if let Some(entry) = self.running.lock().unwrap().remove(workspace_id) {
            match entry.supervision {
                Supervision::Process { pid, .. } => terminate_tree(pid).await,
                Supervision::Static { task } => task.abort(),
            }
        }
    }
```

（`stop` 不再接收 `&AppHandle`——事件经 `sink` 发出；命令层因此不需要传 AppHandle。）

`spawn_web` 与 `active_runtime_dir`、`wait_healthy`：

```rust
async fn spawn_web(
    paths: &RuntimePaths,
    project_dir: &Path,
    manifest: &AppManifest,
    port: u16,
    log_path: &Path,
) -> Result<Child, RuntimeFailure> {
    let runtime_dir = active_runtime_dir(paths)?;
    let node = if cfg!(windows) {
        runtime_dir.join("node.exe")
    } else {
        runtime_dir.join("bin").join("node")
    };
    if !node.is_file() {
        return Err(RuntimeFailure::internal(format!(
            "受管 Runtime 缺少 Node：{}",
            node.display()
        )));
    }
    let (command_alias, rest) = manifest
        .start
        .split_first()
        .ok_or_else(|| RuntimeFailure::internal("dsh-app.json 缺少 start"))?;
    let mut args: Vec<String> = Vec::new();
    if command_alias == "pnpm" {
        args.push(
            runtime_dir
                .join("app")
                .join("node_modules")
                .join("pnpm")
                .join("bin")
                .join("pnpm.cjs")
                .to_string_lossy()
                .into_owned(),
        );
    }
    args.extend(rest.iter().cloned());

    let mut command = Command::new(&node);
    command
        .args(args)
        .current_dir(project_dir)
        .env_clear()
        .env(
            "PATH",
            if cfg!(windows) {
                runtime_dir.clone()
            } else {
                runtime_dir.join("bin")
            },
        )
        .env(&manifest.port_env, port.to_string())
        .env("DSH_APP_PROJECT_DIR", project_dir)
        .env("DSH_APP_DATA_DIR", project_dir.join(&manifest.data_dir))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if cfg!(windows) {
        for key in ["SYSTEMROOT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA"] {
            if let Ok(value) = std::env::var(key) {
                command.env(key, value);
            }
        }
        command.creation_flags(CREATE_NO_WINDOW);
    } else {
        if let Ok(home) = std::env::var("HOME") {
            command.env("HOME", home);
        }
        command.process_group(0);
    }
    let mut child = command
        .spawn()
        .map_err(|cause| RuntimeFailure::internal(format!("启动本地应用失败：{cause}")))?;
    if let Some(stdout) = child.stdout.take() {
        pipe_log(stdout, log_path.to_path_buf(), "APP-OUT");
    }
    if let Some(stderr) = child.stderr.take() {
        pipe_log(stderr, log_path.to_path_buf(), "APP-ERR");
    }
    Ok(child)
}

fn active_runtime_dir(paths: &RuntimePaths) -> Result<PathBuf, RuntimeFailure> {
    #[derive(serde::Deserialize)]
    struct Current {
        version: semver::Version,
    }
    let bytes = std::fs::read(&paths.current)
        .map_err(|_| RuntimeFailure::internal("受管 Runtime 尚未激活，无法启动本地应用"))?;
    let current: Current = serde_json::from_slice(&bytes)
        .map_err(|cause| RuntimeFailure::internal(format!("current.json 无效：{cause}")))?;
    Ok(paths.version_dir(&current.version))
}

async fn wait_healthy(origin: &str, health_path: &str) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_millis(1500))
        .build()
        .map_err(|cause| cause.to_string())?;
    let url = format!("{origin}{health_path}");
    let deadline = Instant::now() + HEALTH_TIMEOUT;
    while Instant::now() < deadline {
        if client.get(&url).send().await.is_ok() {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    Err("健康检查超时".to_owned())
}
```

Windows 下 `creation_flags` 需要 `use tokio::process::Command` 的 Windows 扩展 trait：在文件顶部加：

```rust
#[cfg(windows)]
use tokio::process::CommandExt as _;
```

并引用 `crate::runtime::process::CREATE_NO_WINDOW`（Windows 下）：`#[cfg(windows)] use crate::runtime::process::CREATE_NO_WINDOW;`。

单测（追加到 launcher.rs 底部；静态清单路径不需要真实 Runtime）：

```rust
#[cfg(test)]
mod tests {
    use super::{AppLauncher, EventSink, LOCAL_APP_EVENT};
    use crate::runtime::paths::RuntimePaths;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn temp_runtime_paths(dir: &std::path::Path) -> RuntimePaths {
        let app_paths = crate::storage::app_paths::AppPaths {
            active_root: dir.join("root"),
            runtime: dir.join("runtime"),
            downloads: dir.join("downloads"),
            diagnostics: dir.join("diagnostics"),
            logs: dir.join("logs"),
            bundled_runtime: dir.join("bundled"),
        };
        RuntimePaths::from_app_paths(&app_paths).unwrap()
    }

    fn profile_root_with_workspace(dir: &std::path::Path, workspace_id: &str, project: &std::path::Path) -> std::path::PathBuf {
        let profile_root = dir.join("profile");
        std::fs::create_dir_all(profile_root.join("storages")).unwrap();
        let storage = format!(
            r#"{{"global":{{"workspaceIds":["{workspace_id}"]}},"tables":{{"workspaces":{{"{workspace_id}":{{"path":{}}}}}}}}}}"#,
            serde_json::to_string(&project.to_string_lossy()).unwrap()
        );
        std::fs::write(profile_root.join("storages").join("workspace.json"), storage).unwrap();
        profile_root
    }

    fn profile_for(root: &std::path::Path) -> crate::profile::model::ProfileRecord {
        crate::profile::model::ProfileRecord {
            id: uuid::Uuid::new_v4(),
            name: "默认".into(),
            revision: 1,
            data_root: root.to_path_buf(),
            permission_mode: crate::profile::model::PermissionMode::WorkspaceWrite,
        }
    }

    #[tokio::test]
    async fn static_manifest_launches_stops_and_reports_status() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path().join("note-app");
        std::fs::create_dir_all(project.join("dist")).unwrap();
        std::fs::write(project.join("dist").join("index.html"), b"<h1>ok</h1>").unwrap();
        std::fs::write(
            project.join("dsh-app.json"),
            br#"{"schemaVersion":1,"type":"static","staticDir":"dist"}"#,
        )
        .unwrap();
        let profile_root = profile_root_with_workspace(dir.path(), "w-1", &project);
        let profile = profile_for(&profile_root);

        let events = std::sync::Arc::new(AtomicUsize::new(0));
        let counter = std::sync::Arc::clone(&events);
        let sink: EventSink = Box::new(move |_| { counter.fetch_add(1, Ordering::SeqCst); });
        let launcher = std::sync::Arc::new(AppLauncher::new(temp_runtime_paths(dir.path()), sink));

        let reply = std::sync::Arc::clone(&launcher)
            .launch(&profile, dir.path(), "w-1")
            .await
            .unwrap();
        assert!(reply.origin.starts_with("http://127.0.0.1:"));
        let status = launcher.status(&profile, dir.path());
        assert_eq!(status.running.len(), 1);
        assert!(status.launchable.contains(&"w-1".to_owned()));

        // 幂等：再次 launch 返回同一 origin。
        let again = std::sync::Arc::clone(&launcher)
            .launch(&profile, dir.path(), "w-1")
            .await
            .unwrap();
        assert_eq!(again.origin, reply.origin);

        launcher.stop("w-1").await.unwrap();
        assert!(launcher.status(&profile, dir.path()).running.is_empty());
        assert!(events.load(Ordering::SeqCst) >= 3); // launched ×2 + stopped
        let _ = LOCAL_APP_EVENT;
    }

    #[tokio::test]
    async fn launch_rejects_project_without_manifest() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path().join("plain");
        std::fs::create_dir_all(&project).unwrap();
        let profile_root = profile_root_with_workspace(dir.path(), "w-2", &project);
        let profile = profile_for(&profile_root);
        let launcher = std::sync::Arc::new(AppLauncher::new(
            temp_runtime_paths(dir.path()),
            Box::new(|_| ()),
        ));
        let outcome = std::sync::Arc::clone(&launcher)
            .launch(&profile, dir.path(), "w-2")
            .await;
        assert!(outcome.is_err());
    }
}
```

（若 `AppPaths` 字段与上面不符，以 `src-tauri/src/storage/app_paths.rs` 实际字段为准构造；若 `ProfileRecord`/`PermissionMode` 字段名不同，以 `src-tauri/src/profile/model.rs` 为准。）

- [ ] **Step 3: 运行测试**

Run: `cd src-tauri && cargo test apps::`
Expected: manifest 5 + static_server 1 + launcher 2 + metadata 3 全部通过。

- [ ] **Step 4: Commit**

```bash
git add src-tauri/src/apps src-tauri/src/projects/metadata.rs
git commit -m "feat(apps): local app launcher with registry, health gate and events"
```

---

### Task 5: Rust — 命令、装配与生命周期钩子

**Files:**
- Modify: `src-tauri/src/commands.rs`
- Modify: `src-tauri/src/lib.rs`

- [ ] **Step 1: `commands.rs` 新增三个命令**（追加在 `recycle_project_directory` 之后；文件头部 use 区补 `apps::{AppLauncher, AppStatusReply, LaunchReply}` 与 `tauri::Emitter` 无需——事件在 launcher 内发出）：

```rust
#[tauri::command]
pub async fn app_launch(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    launcher: State<'_, Arc<AppLauncher>>,
    generation_id: String,
    workspace_id: String,
) -> Result<LaunchReply, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    let profile = active_profile(&foundation)?;
    let documents = foundation.platform.documents_dir()?;
    Arc::clone(launcher.inner())
        .launch(&profile, &documents, &workspace_id)
        .await
}

#[tauri::command]
pub async fn app_stop(
    state: State<'_, Arc<DesktopCoordinator>>,
    launcher: State<'_, Arc<AppLauncher>>,
    generation_id: String,
    workspace_id: String,
) -> Result<(), RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    launcher.inner().stop(&workspace_id).await
}

#[tauri::command]
pub async fn app_status(
    state: State<'_, Arc<DesktopCoordinator>>,
    foundation: State<'_, Arc<DesktopFoundation>>,
    launcher: State<'_, Arc<AppLauncher>>,
    generation_id: String,
) -> Result<AppStatusReply, RuntimeFailure> {
    state.validate_generation(&generation_id).await?;
    let profile = active_profile(&foundation)?;
    let documents = foundation.platform.documents_dir()?;
    Ok(launcher.inner().status(&profile, &documents))
}
```

- [ ] **Step 2: 生命周期钩子**

`switch_profile`（第 233 行起）、`restart_runtime`、`repair_runtime`、`orderly_quit` 四个命令都加参数 `launcher: State<'_, Arc<AppLauncher>>`，并在调用 `state.inner()...` **之前**插入：

```rust
    launcher.inner().stop_all().await;
```

（`orderly_quit` 的停止放在 `state.inner().shutdown().await?` 之前。）

- [ ] **Step 3: `lib.rs` 装配**

1. 模块区加 `mod apps;`（Task 1 已加）。
2. `setup` 闭包内、`app.manage(Arc::clone(&app_updates));`（第 197 行）之后：

```rust
            let app_events = app.handle().clone();
            let app_launcher = Arc::new(apps::AppLauncher::new(
                runtime_paths.clone(),
                Box::new(move |event| {
                    let _ = app_events.emit(apps::launcher::LOCAL_APP_EVENT, event);
                }),
            ));
            app.manage(Arc::clone(&app_launcher));
```

（`runtime_paths` 在 setup 中第 182 行已存在且第 185 行只用了 clone，此处再 clone 一次可用。）

3. `generate_handler!` 列表（第 214 行起）追加：

```rust
            commands::app_launch,
            commands::app_stop,
            commands::app_status,
```

4. `RunEvent::ExitRequested` 处理块（第 253 行起）在 `coordinator.shutdown()` 之前补：

```rust
            let app_launcher = app_handle
                .try_state::<Arc<apps::AppLauncher>>()
                .map(|state| Arc::clone(state.inner()));
            // ... block_on 内：
                if let Some(app_launcher) = app_launcher {
                    app_launcher.stop_all().await;
                }
```

- [ ] **Step 4: 验证**

Run: `cd src-tauri && cargo check && cargo test`
Expected: 全部编译通过、既有测试全绿。

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/commands.rs src-tauri/src/lib.rs
git commit -m "feat(apps): expose app_launch/app_stop/app_status commands and lifecycle cleanup"
```

---

### Task 6: TS 壳层 — 桥接契约与载荷校验

**Files:**
- Modify: `src/bridge-contract.ts`
- Modify: `src/workbench-bridge.ts`
- Test: `src/workbench-bridge.test.ts`

- [ ] **Step 1: 写失败测试**（`src/workbench-bridge.test.ts` 追加，参照文件内既有用例的 mock 形态）：

```ts
  it('forwards app actions with a validated workspaceId', async () => {
    const contentWindow = { postMessage: vi.fn() }
    const invoke = vi.fn(async (command: string) => {
      if (command === 'app_launch') return { workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: 'demo' }
      if (command === 'app_status') return { projectsRoot: 'C:\\Projects', running: [], launchable: ['w-1'] }
      return undefined
    })
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'gen-1', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    await bridge.onMessage(messageEvent('app.launch', { workspaceId: 'w-1' }, 'req-1'))
    expect(invoke).toHaveBeenCalledWith('app_launch', { workspaceId: 'w-1', generationId: 'gen-1' })

    await bridge.onMessage(messageEvent('app.stop', { workspaceId: '' }, 'req-2'))
    expect(invoke).toHaveBeenCalledTimes(1) // 空校验失败不转发
  })
```

（`messageEvent` 为该测试文件既有的构造助手；若没有，用 `new MessageEvent('message', { data: {...} })` 并让 `source`/`origin` 匹配 fake active——照抄同文件相邻用例。）

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/workbench-bridge.test.ts`
Expected: 新用例 FAIL（未知 action / 未转发）。

- [ ] **Step 3: 实现**

`src/bridge-contract.ts`：`BridgeAction` 联合类型（第 4-18 行）追加三项；`bridgeCommandByAction`（第 40-55 行）追加映射：

```ts
  | 'app.launch'
  | 'app.stop'
  | 'app.status'
```

```ts
  'app.launch': 'app_launch',
  'app.stop': 'app_stop',
  'app.status': 'app_status',
```

`src/workbench-bridge.ts` 的 `bridgePayload`（第 57-72 行）追加：

```ts
  if (action === 'app.launch' || action === 'app.stop') {
    if (typeof payload.workspaceId !== 'string' || payload.workspaceId.trim() === '') throw new Error('Workspace ID 无效')
    return { workspaceId: payload.workspaceId }
  }
```

- [ ] **Step 4: 跑测试通过并提交**

Run: `npx vitest run src/workbench-bridge.test.ts`
Expected: PASS

```bash
git add src/bridge-contract.ts src/workbench-bridge.ts src/workbench-bridge.test.ts
git commit -m "feat(bridge): add app.launch/app.stop/app.status bridge actions"
```

---

### Task 7: TS 壳层 — `local-app-event` 订阅

**Files:**
- Modify: `src/runtime-contract.ts`
- Modify: `src/runtime-client.ts`
- Test: `src/runtime-client.test.ts`

- [ ] **Step 1: 写失败测试**（`src/runtime-client.test.ts`，沿用既有 `tauri.listen` mock 模式）：

```ts
  it('subscribes to local app events on the local-app-event channel', async () => {
    let receive: ((payload: unknown) => void) | undefined
    tauri.listen.mockImplementation(async (_name: string, listener: (event: { payload: unknown }) => void) => {
      receive = (payload: unknown) => listener({ payload })
      return () => undefined
    })
    const client = tauriRuntimeClient
    const seen: unknown[] = []
    await client.subscribeLocalAppEvents((event) => seen.push(event))
    receive?.({ kind: 'launched', workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: 'demo' })
    expect(seen).toEqual([{ kind: 'launched', workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: 'demo' }])
  })
```

（`tauriRuntimeClient` 为该文件既有的客户端实例名；如不同以文件为准。）

- [ ] **Step 2: 实现**

`src/runtime-contract.ts` 追加类型与接口方法（`RuntimeEvent` 定义附近 + `RuntimeClient` 接口内）：

```ts
export interface LocalAppEvent {
  kind: 'launched' | 'stopped' | 'exited'
  workspaceId: string
  origin: string | null
  title: string | null
}
```

```ts
  subscribeLocalAppEvents(listener: (event: LocalAppEvent) => void): Promise<() => void>
```

`src/runtime-client.ts`：

```ts
  async subscribeLocalAppEvents(listener: (event: LocalAppEvent) => void) {
    const unlisten = await listen<LocalAppEvent>('local-app-event', ({ payload }) => listener(payload))
    return unlisten
  }
```

（import 类型 `LocalAppEvent`。）

- [ ] **Step 3: 验证并提交**

Run: `npx vitest run src/runtime-client.test.ts`
Expected: PASS

```bash
git add src/runtime-contract.ts src/runtime-client.ts src/runtime-client.test.ts
git commit -m "feat(shell): subscribe to local-app-event channel"
```

---

### Task 8: TS 壳层 — 主窗口应用视图表面

**Files:**
- Modify: `src/App.tsx`
- Modify: `src/app.css`
- Test: `src/App.test.tsx`

- [ ] **Step 1: 写失败测试**

`src/App.test.tsx` 的 `fakeRuntime()`（第 7 行起）增加：

```ts
  let localAppListener: ((event: LocalAppEvent) => void) | undefined
```

返回对象内追加，并暴露触发器：

```ts
    subscribeLocalAppEvents: vi.fn(async (next) => { localAppListener = next; return () => undefined }),
```

`fakeRuntime` 返回值追加字段 `emitLocalApp: (event: LocalAppEvent) => localAppListener?.(event)`。

新用例（放在 "restores an already-ready workbench..." 用例之后）：

```ts
  it('switches to the local app surface on launched and back on exited', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.bootstrapRuntime).mockResolvedValue({
      operationId: 'op-ready',
      phase: 'ready',
      rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced',
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await screen.findByTitle('DeepSeek Harness 工作台')

    act(() => runtime.emitLocalApp({ kind: 'launched', workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: '记账应用' }))
    const appFrame = screen.getByTitle('本地应用 记账应用')
    expect(appFrame).toHaveAttribute('src', 'http://127.0.0.1:39123')
    expect(screen.getByText('正在运行：记账应用')).toBeVisible()
    expect(screen.getByTitle('DeepSeek Harness 工作台')).toHaveAttribute('data-hidden')

    act(() => runtime.emitLocalApp({ kind: 'exited', workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: '记账应用' }))
    expect(screen.queryByTitle('本地应用 记账应用')).not.toBeInTheDocument()
  })

  it('ignores local app events with a non-loopback origin', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.bootstrapRuntime).mockResolvedValue({
      operationId: 'op-ready', phase: 'ready', rendererUrl: 'http://127.0.0.1:39000/',
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await screen.findByTitle('DeepSeek Harness 工作台')
    act(() => runtime.emitLocalApp({ kind: 'launched', workspaceId: 'w-1', origin: 'http://evil.example.com', title: 'x' }))
    expect(screen.queryByRole('heading', { name: '本地应用 x' })).not.toBeInTheDocument()
  })
```

（import `act`、`LocalAppEvent` 类型。）

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/App.test.tsx`
Expected: FAIL（subscribeLocalAppEvents 不存在 / 无应用视图）。

- [ ] **Step 3: 实现 `App.tsx`**

1. 状态（`iframeRef` 声明附近）：

```ts
  const [activeApp, setActiveApp] = useState<{ workspaceId: string; origin: string; title: string } | null>(null)
```

2. 主订阅 effect（第 166-202 行）内、`subscribeAppUpdates` 块之后追加：

```ts
    void runtime.subscribeLocalAppEvents((event) => {
      if (!disposed) setActiveApp((current) => applyLocalAppEvent(current, event))
    }).then((off) => {
      if (disposed) off()
      else unsubscribes.push(off)
    }).catch(() => { /* 本地应用事件监听失败不阻塞工作台。 */ })
```

3. 模块级辅助函数（放在组件外）：

```ts
function applyLocalAppEvent(
  current: { workspaceId: string; origin: string; title: string } | null,
  event: LocalAppEvent,
) {
  if (event.origin !== null && !/^http:\/\/127\.0\.0\.1:\d+$/.test(event.origin)) return current
  if (event.kind === 'launched' && event.origin !== null) {
    return { workspaceId: event.workspaceId, origin: event.origin, title: event.title ?? event.workspaceId }
  }
  if (current !== null && current.workspaceId === event.workspaceId) return null
  return current
}
```

4. `stopActiveApp`（`exportDiagnostics` 旁）：

```ts
  const stopActiveApp = async () => {
    if (activeApp === null || state.generationId === null) return
    const workspaceId = activeApp.workspaceId
    setActiveApp(null)
    try {
      await invoke('app_stop', { workspaceId, generationId: state.generationId })
    } catch { /* 停止失败已由 Rust 侧记录；界面先返回工作台。 */ }
  }
```

5. 渲染（第 331-340 行的 iframe 分支改为）：

```tsx
        {state.rendererUrl !== null ? (
          <>
            {/* 受管工作台与桌面壳不同源；不委托 clipboard-write 时 WebView2 会拦截官方 UI 的复制操作。 */}
            <iframe
              ref={iframeRef}
              className="workbenchFrame"
              title="DeepSeek Harness 工作台"
              src={state.rendererUrl}
              data-hidden={activeApp !== null || undefined}
              allow="clipboard-write"
            />
            {activeApp !== null && (
              <section className="localAppSurface" aria-label="本地应用视图">
                <div className="localAppStrip">
                  <span className="localAppStripTitle">正在运行：{activeApp.title}</span>
                  <div className="localAppStripActions">
                    <button type="button" onClick={() => setActiveApp(null)}>返回工作台</button>
                    <button type="button" className="localAppStripStop" onClick={() => void stopActiveApp()}>停止应用</button>
                  </div>
                </div>
                <iframe
                  className="localAppFrame"
                  title={`本地应用 ${activeApp.title}`}
                  src={activeApp.origin}
                  allow="clipboard-write"
                />
              </section>
            )}
          </>
        ) : (
```

（其后的 `bootstrapShell` 分支原样保留。）

`src/app.css` 追加：

```css
.workbenchFrame[data-hidden] { display: none; }
.localAppSurface { position: absolute; inset: 0; display: flex; flex-direction: column; z-index: 5; background: #151517; }
.localAppStrip { display: flex; align-items: center; justify-content: space-between; gap: 16px; box-sizing: border-box; height: 40px; padding: 0 14px; background: #1c1c1f; border-bottom: 1px solid rgba(255,255,255,.08); color: #ececf0; font-size: 13px; }
.localAppStripActions { display: flex; gap: 8px; }
.localAppStripActions button { min-height: 26px; padding: 0 12px; border: 1px solid rgba(255,255,255,.14); border-radius: 999px; background: transparent; color: inherit; font-size: 12px; cursor: pointer; }
.localAppStripActions button:hover { background: rgba(127,127,127,.14); }
.localAppStripStop { border-color: rgba(214,118,118,.5) !important; color: #e8aaaa !important; }
.localAppFrame { flex: 1; width: 100%; border: 0; background: #fff; }
```

（若 `.windowContent` 尚无 `position: relative`，补上。）

- [ ] **Step 4: 验证并提交**

Run: `npx vitest run src/App.test.tsx`
Expected: PASS（含既有用例）

```bash
git add src/App.tsx src/app.css src/App.test.tsx
git commit -m "feat(shell): local app surface with trusted strip in the main window"
```

---

### Task 9: 插件 — 桥接动作、app.status、角标与样式

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/desktop-bridge.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/project-model.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/LocalProjectsPage.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/ProjectCard.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/styles.ts`
- Test: `packages/dsh-plugin-desktop/tests/local-projects-page.spec.tsx`

- [ ] **Step 1: 写失败测试**（`local-projects-page.spec.tsx` 追加；bridgeFixture 的 `responses` 支持按 action 注入）：

```ts
  it('marks launchable and running projects from app.status', async () => {
    const workspaces = workspaceFixture([
      { workspaceId: 'w-1', path: 'C:\\Users\\t\\Documents\\DeepSeek Harness\\Projects\\demo', title: 'demo', sessionIds: [], createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z' },
      { workspaceId: 'w-2', path: 'D:\\code\\lib', title: 'lib', sessionIds: [], createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z' },
    ])
    const bridge = bridgeFixture()
    vi.mocked(bridge.request).mockImplementation(async (action) => {
      if (action === 'profile.list') return { selectedProfileId: 'p-default', pendingProfileId: null, lastKnownGoodProfileId: 'p-default', profiles: [] }
      if (action === 'project.metadata.list') return { schemaVersion: 1, projects: {} }
      if (action === 'app.status') return {
        projectsRoot: 'C:\\Users\\t\\Documents\\DeepSeek Harness\\Projects',
        running: [{ workspaceId: 'w-1', origin: 'http://127.0.0.1:39222', title: 'demo', startedAt: '2026-08-21T00:00:00Z' }],
        launchable: ['w-1'],
      }
      return undefined
    })
    renderFrame({ workspaces, bridge })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))

    expect(await screen.findByText('运行中')).toBeInTheDocument()
    // w-2 不在项目根目录也未收录 → 不显示
    expect(screen.queryByRole('button', { name: '项目 lib' })).not.toBeInTheDocument()
  })
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run test -w @dsh/desktop-plugin -- tests/local-projects-page.spec.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

1. `desktop-bridge.ts` 的 `DesktopBridgeAction` 追加 `| 'app.launch' | 'app.stop' | 'app.status'`。

2. `project-model.ts` 的 `ProjectMetadataEntry` 追加 `localApp?: boolean`。

3. `LocalProjectsPage.tsx`：

```ts
interface AppsStatus {
  projectsRoot: string
  running: Array<{ workspaceId: string; origin: string; title: string; startedAt: string }>
  launchable: string[]
}
```

组件内新增：

```ts
  const [apps, setApps] = useState<AppsStatus | null>(null)
  const refreshApps = async () => {
    try { setApps(await bridge.request<AppsStatus>('app.status')) }
    catch { /* 状态失败只影响角标与过滤，不打断页面 */ }
  }
  useEffect(() => { void refreshApps() }, [bridge]) // eslint-disable-line react-hooks/exhaustive-deps
```

`cards` 之后追加过滤（`normalizeDir`/`isUnderRoot` 为模块级辅助函数）：

```ts
  const visibleCards = useMemo(() => {
    if (apps === null) return []
    const root = normalizeDir(apps.projectsRoot)
    return cards.filter((card) =>
      isUnderRoot(normalizeDir(card.path), root)
      || metadata.projects[card.id]?.localApp === true)
  }, [apps, cards, metadata])
```

```ts
function normalizeDir(path: string): string {
  return path.replace(/\\/g, '/').replace(/\/+$/g, '').toLowerCase()
}
function isUnderRoot(dir: string, root: string): boolean {
  return dir === root || dir.startsWith(`${root}/`)
}
```

渲染处把 `cards.map(...)` 与 `cards.length` 的三处引用换成 `visibleCards`（网格、空态判断、`selectedCard` 查找同步改用 `visibleCards`）。

新增动作：

```ts
  const launch = async (workspaceId: string) => {
    setBusyId(workspaceId)
    setActionError(null)
    try {
      await bridge.request('app.launch', { workspaceId })
      onClose()
    } catch (cause) {
      setActionError(workspaceFailure(cause).message)
    } finally {
      setBusyId(null)
      void refreshApps()
    }
  }

  const stopApp = async (workspaceId: string) => {
    setBusyId(workspaceId)
    setActionError(null)
    try { await bridge.request('app.stop', { workspaceId }) }
    catch (cause) { setActionError(workspaceFailure(cause).message) }
    finally { setBusyId(null); void refreshApps() }
  }
```

`ProjectCard` 传参：

```tsx
  <ProjectCard
    ...
    launchable={apps?.launchable.includes(card.id) ?? false}
    running={apps?.running.some((entry) => entry.workspaceId === card.id) ?? false}
    onOpen={() => (apps?.launchable.includes(card.id) ?? false) ? launch(card.id) : open(card.id)}
    onOpenSession={() => open(card.id)}
    onStopApp={() => stopApp(card.id)}
  />
```

4. `ProjectCard.tsx`：props 增加 `launchable?: boolean; running?: boolean; onOpenSession(): void; onStopApp(): Promise<void> | void`；meta 行（第 117-121 行）追加：

```tsx
            {running && <span className="dshDesktopProjectBadge" data-kind="running">运行中</span>}
            {!running && launchable && <span className="dshDesktopProjectBadge" data-kind="launchable">可运行</span>}
```

把 `onOpenSession`/`onStopApp` 透传给 `ProjectContextMenu`。

5. `styles.ts` 追加：

```css
    .dshDesktopProjectBadge { padding: 2px 7px; border-radius: 999px; font-size: 10px; }
    .dshDesktopProjectBadge[data-kind="running"] { color: #7fd0a4; background: color-mix(in srgb, #3f8064 18%, transparent); }
    .dshDesktopProjectBadge[data-kind="launchable"] { color: #9ab2f5; background: color-mix(in srgb, #5b79cd 16%, transparent); }
```

- [ ] **Step 4: 验证并提交**

Run: `npm run test -w @dsh/desktop-plugin -- tests/local-projects-page.spec.tsx`
Expected: PASS（既有用例因过滤逻辑可能需要同步——若既有用例的 fixture 路径不在根目录且未打标记，为这些用例的 `app.status` mock 补 `launchable`/`projectsRoot` 使其路径位于根目录，或给 metadata 打 `localApp`；按用例语义选择）。

```bash
git add packages/dsh-plugin-desktop
git commit -m "feat(plugin): local app status badges and launchable scoping on the projects page"
```

---

### Task 10: 插件 — 右键菜单"打开会话继续开发 / 停止应用"

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/ProjectContextMenu.tsx`
- Test: `packages/dsh-plugin-desktop/tests/local-projects-page.spec.tsx`

- [ ] **Step 1: 写失败测试**

```ts
  it('offers session continuation and app stop in the card menu', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    const bridge = bridgeFixture()
    vi.mocked(bridge.request).mockImplementation(async (action) => {
      if (action === 'profile.list') return { selectedProfileId: 'p-default', pendingProfileId: null, lastKnownGoodProfileId: 'p-default', profiles: [] }
      if (action === 'project.metadata.list') return { schemaVersion: 1, projects: {} }
      if (action === 'app.status') return { projectsRoot: 'C:\\code', running: [{ workspaceId: 'w-1', origin: 'http://127.0.0.1:39333', title: 'demo', startedAt: '2026-08-21T00:00:00Z' }], launchable: ['w-1'] }
      if (action === 'app.stop') return undefined
      return undefined
    })
    renderFrame({ workspaces, bridge })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    const card = await screen.findByRole('button', { name: '项目 demo' })
    fireEvent.contextMenu(card)

    fireEvent.click(screen.getByRole('menuitem', { name: '停止应用' }))
    await waitFor(() => expect(bridge.request).toHaveBeenCalledWith('app.stop', { workspaceId: 'w-1' }))

    fireEvent.contextMenu(card)
    fireEvent.click(screen.getByRole('menuitem', { name: '打开会话继续开发' }))
    await waitFor(() => expect(workspaces.connectWorkspace).toHaveBeenCalledWith('w-1'))
  })
```

- [ ] **Step 2: 确认失败后实现**

`ProjectContextMenu.tsx`：props 追加 `running?: boolean; onOpenSession?(): void; onStopApp?(): void`。按钮区（第 65-72 行）改为：

```tsx
        <>
          <button ref={(node) => { itemRefs.current[0] = node }} type="button" role="menuitem" disabled={disabled} onClick={() => run(() => onOpenSession?.())}>打开会话继续开发</button>
          <button ref={(node) => { itemRefs.current[1] = node }} type="button" role="menuitem" disabled={disabled || !running} onClick={() => run(() => onStopApp?.())}>停止应用</button>
          <button ref={(node) => { itemRefs.current[2] = node }} type="button" role="menuitem" disabled={disabled} onClick={() => run(onRename)}>修改名称</button>
          <button ref={(node) => { itemRefs.current[3] = node }} type="button" role="menuitem" disabled={disabled} onClick={() => setView('covers')}>修改封面 <span aria-hidden="true">›</span></button>
          <button ref={(node) => { itemRefs.current[4] = node }} type="button" role="menuitem" disabled={disabled} onClick={() => run(() => onPinChange(!pinned))}>{pinned ? '取消置顶' : '置顶'}</button>
          <div className="dshDesktopProjectMenuDivider" />
          <button ref={(node) => { itemRefs.current[5] = node }} type="button" role="menuitem" className="dshDesktopProjectMenuDanger" disabled={disabled} onClick={() => run(onDelete)}>删除项目</button>
        </>
```

键盘导航的取模数量改为常量 `const ITEM_COUNT = 6;`（`% ITEM_COUNT`、`setActiveIndex(ITEM_COUNT - 1)`）。

- [ ] **Step 3: 验证并提交**

Run: `npm run test -w @dsh/desktop-plugin -- tests/local-projects-page.spec.tsx`
Expected: PASS

```bash
git add packages/dsh-plugin-desktop
git commit -m "feat(plugin): session-continue and stop-app entries in the project context menu"
```

---

### Task 11: 插件 — 收录过滤与"收录已有项目"对话框

**Files:**
- Create: `packages/dsh-plugin-desktop/src/client/AdoptProjectDialog.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/LocalProjectsPage.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/styles.ts`
- Test: `packages/dsh-plugin-desktop/tests/adopt-project-dialog.spec.tsx`

- [ ] **Step 1: 写失败测试**（新文件 `tests/adopt-project-dialog.spec.tsx`）：

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdoptProjectDialog } from '../src/client/AdoptProjectDialog'

describe('adopt project dialog', () => {
  it('lists candidates and patches localApp on selection', async () => {
    const onAdopt = vi.fn(async () => undefined)
    render(
      <AdoptProjectDialog
        candidates={[{ id: 'w-9', title: 'legacy', path: 'D:\\code\\legacy' }]}
        busy={false}
        onAdopt={onAdopt}
        onClose={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /legacy/ }))
    await waitFor(() => expect(onAdopt).toHaveBeenCalledWith('w-9'))
  })

  it('shows an empty hint when nothing can be adopted', () => {
    render(<AdoptProjectDialog candidates={[]} busy={false} onAdopt={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByText('当前 Profile 没有可收录的项目')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 实现 `AdoptProjectDialog.tsx`**

```tsx
export interface AdoptCandidate {
  id: string
  title: string
  path: string
}

export interface AdoptProjectDialogProps {
  candidates: readonly AdoptCandidate[]
  busy: boolean
  onAdopt(workspaceId: string): Promise<void>
  onClose(): void
}

export function AdoptProjectDialog({ candidates, busy, onAdopt, onClose }: AdoptProjectDialogProps) {
  return (
    <div
      className="dshDesktopProjectDialogBackdrop"
      onPointerDown={(event) => { if (event.target === event.currentTarget && !busy) onClose() }}
    >
      <section
        className="dshDesktopProjectDeleteDialog dshDesktopAdoptDialog"
        role="dialog"
        aria-modal="true"
        aria-label="收录已有项目"
        onKeyDown={(event) => { if (event.key === 'Escape' && !busy) { event.preventDefault(); onClose() } }}
      >
        <header>
          <div>
            <p>收录已有项目</p>
            <h2>把工作区加入本地项目</h2>
          </div>
          <button type="button" aria-label="关闭收录对话框" disabled={busy} onClick={onClose}>×</button>
        </header>
        {candidates.length === 0 ? (
          <p className="dshDesktopProjectDeletePath">当前 Profile 没有可收录的项目</p>
        ) : (
          <ul className="dshDesktopAdoptList">
            {candidates.map((candidate) => (
              <li key={candidate.id}>
                <button type="button" disabled={busy} onClick={() => void onAdopt(candidate.id)}>
                  <strong>{candidate.title}</strong>
                  <small title={candidate.path}>{candidate.path}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
```

- [ ] **Step 3: 接入 `LocalProjectsPage.tsx`**

```ts
  const [adoptOpen, setAdoptOpen] = useState(false)
  const adoptable = useMemo(() => state.state !== 'loading' && apps !== null
    ? state.items
        .filter((workspace) => !visibleCards.some((card) => card.id === workspace.workspaceId))
        .map((workspace) => ({ id: workspace.workspaceId, title: workspace.title, path: workspace.path }))
    : [], [state.state, state.items, visibleCards, apps])

  const adopt = async (workspaceId: string) => {
    setBusyId(workspaceId)
    setActionError(null)
    try {
      const snapshot = await bridge.request<ProjectMetadataSnapshot>('project.metadata.patch', { workspaceId, patch: { localApp: true } })
      if (snapshot?.projects !== undefined) setMetadata(snapshot)
      setAdoptOpen(false)
    } catch (cause) {
      setActionError(workspaceFailure(cause).message)
    } finally {
      setBusyId(null)
    }
  }
```

`dshDesktopProjectComposerDock` 内、`ProjectComposer` 之前：

```tsx
            <div className="dshDesktopAdoptRow">
              <button type="button" className="dshDesktopAdoptButton" disabled={profilePending || busyId !== null} onClick={() => setAdoptOpen(true)}>收录已有项目</button>
            </div>
```

删除对话框旁追加：

```tsx
        {adoptOpen && (
          <AdoptProjectDialog
            candidates={adoptable}
            busy={busyId !== null}
            onAdopt={adopt}
            onClose={() => setAdoptOpen(false)}
          />
        )}
```

- [ ] **Step 4: 样式**（`styles.ts`）：

```css
    .dshDesktopAdoptRow { display: flex; justify-content: center; margin-bottom: 8px; }
    .dshDesktopAdoptButton { min-height: 30px; padding: 0 14px; border: 1px dashed color-mix(in srgb, #7196ff 40%, var(--dsh-desktop-divider)); border-radius: 999px; color: #9ab2f5; background: transparent; font-size: 12px; cursor: pointer; }
    .dshDesktopAdoptButton:hover:not(:disabled) { background: color-mix(in srgb, #7196ff 9%, transparent); }
    .dshDesktopAdoptButton:disabled { cursor: default; opacity: .48; }
    .dshDesktopAdoptList { display: grid; gap: 8px; max-height: 320px; margin: 14px 0 0; padding: 0; list-style: none; overflow: auto; }
    .dshDesktopAdoptList button { display: grid; gap: 3px; width: 100%; padding: 10px 12px; border: 1px solid var(--dsh-desktop-divider); border-radius: 10px; color: inherit; background: transparent; cursor: pointer; text-align: left; }
    .dshDesktopAdoptList button:hover:not(:disabled) { border-color: color-mix(in srgb, #7196ff 45%, var(--dsh-desktop-divider)); background: color-mix(in srgb, #7196ff 8%, transparent); }
    .dshDesktopAdoptList small { overflow: hidden; color: var(--dsw-alias-label-tertiary, #85858d); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 5: 验证并提交**

Run: `npm run test -w @dsh/desktop-plugin`
Expected: 全部 PASS

```bash
git add packages/dsh-plugin-desktop
git commit -m "feat(plugin): adopt existing workspaces into local projects"
```

---

### Task 12: 插件 — 构建提示词、删除文案与文档

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/project-controller.ts:67-70`
- Modify: `packages/dsh-plugin-desktop/src/client/ProjectDeleteDialog.tsx:63`
- Modify: `packages/dsh-plugin-desktop/README.md`

- [ ] **Step 1: `buildPrompt` 追加收尾要求**（`project-controller.ts` 的 `buildPrompt` 返回值末尾追加）：

```ts
  + '\n\n收尾要求：\n'
  + '1. 在项目根目录写 dsh-app.json（UTF-8）：{"schemaVersion":1,"type":"web","start":["pnpm","run","start"],"portEnv":"PORT","healthPath":"/","dataDir":"data"}。start 可换成 ["node","<入口文件>"]。\n'
  + '2. 服务必须从 PORT 环境变量读取监听端口（绑定 127.0.0.1），不要写死端口。\n'
  + '3. 业务数据一律写入 data/ 目录（本地文件或内嵌数据库），保证应用重启后数据保留。'
```

- [ ] **Step 2: 删除对话框文案**（`ProjectDeleteDialog.tsx` 第 63 行 `<small>` 回收站说明改为）：

```tsx
            <span><strong>移到 Windows 回收站</strong><small>{project.unavailable ? '当前路径不可用，只能移除列表记录。' : '同时移除目录与应用数据，仍可从系统回收站恢复。'}</small></span>
```

- [ ] **Step 3: `packages/dsh-plugin-desktop/README.md` 的"本地项目"一节末尾追加**：

```markdown
- 项目带有效的 `dsh-app.json` 启动清单时，双击卡片会把项目作为本地应用在主窗口内启动；再次双击可瞬时切回。无清单的项目双击仍打开项目会话，右键菜单提供“打开会话继续开发”。
- 运行中的应用在切回工作台后保持后台运行（卡片显示“运行中”），可通过右键“停止应用”或应用视图顶部的“停止应用”终止。应用数据保存在项目目录的 `data/`（清单可改），随项目目录备份与删除。
- “本地项目”页只显示位于“文档\DeepSeek Harness\Projects”下的工作区，以及通过“收录已有项目”手动加入的工作区。
```

- [ ] **Step 4: 验证并提交**

Run: `npm run check`
Expected: 全绿（含 `scripts/product-copy.test.ts` 对 README 的断言）。

```bash
git add packages/dsh-plugin-desktop
git commit -m "feat(plugin): manifest-aware build prompt, delete copy and local app docs"
```

---

### Task 13: e2e — 本地应用启动冒烟

**Files:**
- Modify: `e2e/support/desktop.ts`
- Create: `e2e/specs/local-app-launch.e2e.ts`

**说明：** 仅在完整 e2e 环境（`npm run e2e:build` + `npm run e2e`）下运行；用例使用 static 清单 fixture，不依赖模型输出。

- [ ] **Step 1: `desktop.ts` 增加主窗口定位与应用助手**（类内方法）：

```ts
  async launchLocalAppByManifest(title: string, projectDir: string): Promise<void> {
    // 通过 CDP 注入 static 清单 fixture：主窗口无法直接写盘，
    // 因此该助手假定 e2e setup 已在 projectDir 写好 dsh-app.json 与 dist/index.html。
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(title, 'double-click')
    })
    await this.waitForMainWindowExpression(
      `document.querySelector('section[aria-label="本地应用视图"]') !== null`,
      `本地应用视图未出现：${title}`,
    )
  }

  private async waitForMainWindowExpression(expression: string, message: string): Promise<void> {
    // 主窗口 target：非 127.0.0.1 的 page 类型 target（tauri.localhost / tauri://localhost）。
    const targets = await this.cdpTargets()
    const main = targets.find((target) => target.type === 'page' && !target.url.includes('127.0.0.1:'))
    if (main === undefined) throw new Error('找不到主窗口 CDP target')
    const page = await CdpPage.connect(main.webSocketDebuggerUrl)
    try {
      await page.waitFor(expression, { timeoutMs: 30_000, message })
    } finally {
      page.close()
    }
  }
```

（`cdpTargets` 抽取自既有 `findWorkbenchTarget` 的枚举逻辑，做成私有方法返回 `CdpTarget[]`；若已存在等价方法直接复用。）

- [ ] **Step 2: 新建 `e2e/specs/local-app-launch.e2e.ts`**

```ts
import { describe, it } from '@wdio/cucumber-spec' // 以仓库现有 e2e spec 的导入风格为准
import { desktop } from '../support/desktop'

describe('local app launcher', () => {
  it('launches a static manifest project in the main window and stops it', async () => {
    // 前置：e2e setup 在受管项目根写好 fixture（title=记账-e2e，含 dsh-app.json + dist/index.html）
    await desktop.launchLocalAppByManifest('记账-e2e', '')
    await desktop.returnToWorkbenchFromApp()
    await desktop.stopLocalAppFromCard('记账-e2e')
  })
})
```

（`returnToWorkbenchFromApp`/`stopLocalAppFromCard` 按 Step 1 的模式实现：主窗口点击 `返回工作台` 按钮；工作台内右键卡片 → `停止应用`。仓库 e2e spec 的实际框架风格以 `e2e/specs/` 现有文件为准——照抄其 describe/it 与导入。）

- [ ] **Step 3: 本地验证语法（不跑完整 e2e）**

Run: `npx tsc --noEmit -p tsconfig.json 2>&1 | head`（或仓库既有的 e2e 类型检查方式）
Expected: 无类型错误。

- [ ] **Step 4: Commit**

```bash
git add e2e
git commit -m "test(e2e): local app launch smoke helpers and spec"
```

---

## 完成标准（整体验收）

1. `cd src-tauri && cargo test` 全绿。
2. `npm run check` 全绿（root + plugin + 构建）。
3. 手动验收（有运行时环境时）：用"项目需求"构建一个 static 清单项目 → 卡片出现"可运行"角标 → 双击 → 主窗口切换到应用视图 → 返回工作台（卡片"运行中"）→ 右键停止 → 角标消失。

## Self-Review 记录

- **Spec 覆盖：** §2 方案A→Task 1/4；§3 清单→Task 1；§4 四组件→Task 2/3/4（manifest/runner+registry/static_server；日志路径、并发上限、幂等、健康超时回滚、`terminate_tree` 复用均在 Task 4）；§5 桥接/命令/事件/metadata.localApp→Task 5/6/9/11；§6 主窗口表面→Task 8；§7 插件 UI→Task 9/10/11；§8 数据与提示词/删除文案→Task 12；§9 错误处理→Task 4（健康超时/幂等/清理）、Task 5（stop_all 钩子）、Task 8（exited 自动返回）；§10 测试→各任务内嵌 + Task 13 e2e。
- **类型一致性：** `AppStatusReply{projectsRoot,running,launchable}`（Rust camelCase 序列化）与插件 `AppsStatus` 一致；`LaunchReply{workspaceId,origin,title}` 一致；`LocalAppEvent{kind,workspaceId,origin,title}` Rust↔TS 一致；桥接动作 `app.launch/app.stop/app.status` 三处（desktop-bridge、bridge-contract、Rust 命令）一致。
- **占位符：** 无 TBD/TODO；Task 4 的 watcher 代码已并入主代码块；Task 13 的 e2e spec 框架风格注明以仓库现有 spec 为准（`e2e/specs/` 现有文件的 describe/it 与导入照抄）。
