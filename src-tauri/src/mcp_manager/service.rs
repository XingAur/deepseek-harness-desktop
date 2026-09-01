use std::{collections::BTreeSet, path::Path, path::PathBuf};

use serde_json::Value;

use crate::mcp_manager::model::{McpServerDef, McpTarget, McpTargetStatus, McpManagerError, Result};
use crate::mcp_manager::store::McpStore;
use crate::prompts::targets::{atomic_write, detect_home};

/// MCP 同步面板服务(Claude/Codex 跨 CLI 统一管理,移植自 cc-switch 思路):
/// - 权威数据在本地 SQLite;目标文件(~/.claude.json、~/.codex/config.toml)只是投影;
/// - 对目标文件一律读-改-写,只增删改 `mcpServers` / `mcp_servers` 键,保留无关字段。
pub struct McpManagerService {
    store: McpStore,
    home: Option<PathBuf>,
}

impl McpManagerService {
    pub fn open(paths: &crate::storage::app_paths::AppPaths) -> Result<Self> {
        Self::open_with_env(&detect_home().unwrap_or_default(), &paths.state.join("mcp-manager.db"))
    }

    pub fn open_with_env(home: &Path, database_path: &Path) -> Result<Self> {
        Ok(Self {
            store: McpStore::open(database_path)?,
            home: if home.as_os_str().is_empty() { None } else { Some(home.to_path_buf()) },
        })
    }

    /// 内存库兜底构造:主库不可用时的降级路径(服务器定义不持久)。
    pub fn open_ephemeral(_paths: &crate::storage::app_paths::AppPaths) -> Result<Self> {
        Ok(Self { store: McpStore::open_ephemeral()?, home: detect_home() })
    }

    fn now_ms() -> i64 {
        chrono::Utc::now().timestamp_millis()
    }

    pub fn list(&self) -> Result<Vec<McpServerDef>> {
        self.store.list()
    }

    /// 新建或更新:name/command trim 后非空;targets 只允许 claude|codex;同名只允许一条。
    pub fn upsert(&self, mut def: McpServerDef) -> Result<McpServerDef> {
        def.name = def.name.trim().to_owned();
        def.command = def.command.trim().to_owned();
        if def.name.is_empty() || def.name.chars().count() > 200 {
            return Err(McpManagerError::InvalidInput("名称须为 1-200 字符".into()));
        }
        if def.command.is_empty() || def.command.len() > 4096 {
            return Err(McpManagerError::InvalidInput("命令须为 1-4096 字节".into()));
        }
        for target in &def.targets {
            McpTarget::parse(target)
                .ok_or_else(|| McpManagerError::InvalidInput(format!("未知同步目标: {target}")))?;
        }
        if def.id.is_empty() {
            def.id = uuid::Uuid::new_v4().to_string();
        }
        if let Some(existing) = self.store.get_by_name(&def.name)? {
            if existing.id != def.id {
                return Err(McpManagerError::InvalidInput(format!("同名服务器已存在: {}", def.name)));
            }
        }
        self.store.upsert(&def, Self::now_ms())?;
        Ok(def)
    }

    /// 删除:先从两个目标文件按名移除,再删库;定义不存在时幂等成功。
    pub fn delete(&self, id: &str) -> Result<()> {
        let Some(def) = self.store.get(id)? else { return Ok(()) };
        for target in McpTarget::ALL {
            self.remove_from_target_file(target, &def.name)?;
        }
        self.store.delete(id)
    }

    /// 同步:把 targets 含该目标的所有服务器写入对应文件。
    /// 文件缺失但目标目录存在 → 创建文件;目标目录不存在 → TargetNotInstalled。
    pub fn sync_target(&self, target: McpTarget) -> Result<()> {
        let home = self.require_home(target)?;
        let defs: Vec<McpServerDef> = self
            .store
            .list()?
            .into_iter()
            .filter(|def| def.targets.contains(target.as_str()))
            .collect();
        match target {
            McpTarget::Claude => self.sync_claude(home, &defs),
            McpTarget::Codex => self.sync_codex(home, &defs),
        }
    }

    /// 导入:从目标文件读出现有 MCP 服务器入库;按 name 去重(已存在则更新字段并把该目标并入集合)。
    /// 目标目录不存在 → TargetNotInstalled;目录存在但文件缺失 → 视为空集。
    pub fn import_target(&self, target: McpTarget) -> Result<Vec<McpServerDef>> {
        let home = self.require_home(target)?;
        let raw = match target {
            McpTarget::Claude => {
                let path = claude_settings_path(home);
                let text = match std::fs::read_to_string(&path) {
                    Ok(text) => text,
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
                    Err(error) => return Err(McpManagerError::Io(error.to_string())),
                };
                parse_claude_servers(&text)
                    .map_err(|error| McpManagerError::Io(format!("解析 {} 失败: {error}", path.display())))?
            }
            McpTarget::Codex => {
                let path = codex_config_path(home);
                let text = match std::fs::read_to_string(&path) {
                    Ok(text) => text,
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
                    Err(error) => return Err(McpManagerError::Io(error.to_string())),
                };
                parse_codex_servers(&text)
                    .map_err(|error| McpManagerError::Io(format!("解析 {} 失败: {error}", path.display())))?
            }
        };
        let mut imported = Vec::new();
        for raw in raw {
            let def = match self.store.get_by_name(&raw.name)? {
                Some(mut existing) => {
                    existing.command = raw.command;
                    existing.args = raw.args;
                    existing.env = raw.env;
                    existing.targets.insert(target.as_str().to_owned());
                    existing
                }
                None => McpServerDef {
                    id: uuid::Uuid::new_v4().to_string(),
                    name: raw.name,
                    command: raw.command,
                    args: raw.args,
                    env: raw.env,
                    targets: BTreeSet::from([target.as_str().to_owned()]),
                },
            };
            self.store.upsert(&def, Self::now_ms())?;
            imported.push(def);
        }
        Ok(imported)
    }

    /// 两个目标的安装状态(目录存在即视为已安装)。
    pub fn status(&self) -> Result<Vec<McpTargetStatus>> {
        Ok(McpTarget::ALL
            .into_iter()
            .map(|target| McpTargetStatus { target, installed: self.target_installed(target) })
            .collect())
    }

    fn target_installed(&self, target: McpTarget) -> bool {
        match (&self.home, target) {
            (Some(home), McpTarget::Claude) => claude_dir(home).is_dir(),
            (Some(home), McpTarget::Codex) => codex_dir(home).is_dir(),
            (None, _) => false,
        }
    }

    fn require_home(&self, target: McpTarget) -> Result<&Path> {
        let Some(home) = &self.home else { return Err(McpManagerError::TargetNotInstalled(target)) };
        let installed = match target {
            McpTarget::Claude => claude_dir(home).is_dir(),
            McpTarget::Codex => codex_dir(home).is_dir(),
        };
        if !installed {
            return Err(McpManagerError::TargetNotInstalled(target));
        }
        Ok(home)
    }

    fn sync_claude(&self, home: &Path, defs: &[McpServerDef]) -> Result<()> {
        let path = claude_settings_path(home);
        let mut root: Value = match std::fs::read_to_string(&path) {
            Ok(text) => serde_json::from_str(&text)
                .map_err(|error| McpManagerError::Io(format!("解析 {} 失败: {error}", path.display())))?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => serde_json::json!({}),
            Err(error) => return Err(McpManagerError::Io(error.to_string())),
        };
        let object = root
            .as_object_mut()
            .ok_or_else(|| McpManagerError::Io(format!("{} 顶层不是 JSON 对象", path.display())))?;
        let mut servers = match object.remove("mcpServers") {
            Some(Value::Object(map)) => map,
            Some(Value::Null) | None => serde_json::Map::new(),
            Some(other) => {
                return Err(McpManagerError::Io(format!(
                    "{} 的 mcpServers 不是对象: {other}",
                    path.display()
                )))
            }
        };
        for def in defs {
            servers.insert(def.name.clone(), claude_entry(def));
        }
        object.insert("mcpServers".to_owned(), Value::Object(servers));
        let serialized =
            serde_json::to_string_pretty(&root).map_err(|error| McpManagerError::Io(error.to_string()))?;
        atomic_write(&path, serialized.as_bytes()).map_err(|error| McpManagerError::Io(error.to_string()))
    }

    fn sync_codex(&self, home: &Path, defs: &[McpServerDef]) -> Result<()> {
        let path = codex_config_path(home);
        let mut root: toml::Table = match std::fs::read_to_string(&path) {
            Ok(text) => text
                .parse::<toml::Table>()
                .map_err(|error| McpManagerError::Io(format!("解析 {} 失败: {error}", path.display())))?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => toml::Table::new(),
            Err(error) => return Err(McpManagerError::Io(error.to_string())),
        };
        let mut servers = match root.remove("mcp_servers") {
            Some(toml::Value::Table(table)) => table,
            None => toml::Table::new(),
            Some(other) => {
                return Err(McpManagerError::Io(format!(
                    "{} 的 mcp_servers 不是表: {other}",
                    path.display()
                )))
            }
        };
        for def in defs {
            servers.insert(def.name.clone(), toml::Value::Table(codex_entry(def)));
        }
        root.insert("mcp_servers".to_owned(), toml::Value::Table(servers));
        let serialized = toml::to_string_pretty(&root).map_err(|error| McpManagerError::Io(error.to_string()))?;
        atomic_write(&path, serialized.as_bytes()).map_err(|error| McpManagerError::Io(error.to_string()))
    }

    /// 从目标文件的 mcpServers/mcp_servers 里按名移除一条(文件不存在则无事可做)。
    fn remove_from_target_file(&self, target: McpTarget, name: &str) -> Result<()> {
        let Some(home) = &self.home else { return Ok(()) };
        match target {
            McpTarget::Claude => {
                let path = claude_settings_path(home);
                if !path.exists() {
                    return Ok(());
                }
                let text = std::fs::read_to_string(&path).map_err(|error| McpManagerError::Io(error.to_string()))?;
                let mut root: Value = serde_json::from_str(&text)
                    .map_err(|error| McpManagerError::Io(format!("解析 {} 失败: {error}", path.display())))?;
                if let Some(servers) = root.get_mut("mcpServers").and_then(|value| value.as_object_mut()) {
                    servers.remove(name);
                    let serialized = serde_json::to_string_pretty(&root)
                        .map_err(|error| McpManagerError::Io(error.to_string()))?;
                    atomic_write(&path, serialized.as_bytes())
                        .map_err(|error| McpManagerError::Io(error.to_string()))?;
                }
                Ok(())
            }
            McpTarget::Codex => {
                let path = codex_config_path(home);
                if !path.exists() {
                    return Ok(());
                }
                let text = std::fs::read_to_string(&path).map_err(|error| McpManagerError::Io(error.to_string()))?;
                let mut root: toml::Table = text
                    .parse::<toml::Table>()
                    .map_err(|error| McpManagerError::Io(format!("解析 {} 失败: {error}", path.display())))?;
                if let Some(toml::Value::Table(servers)) = root.get_mut("mcp_servers") {
                    servers.remove(name);
                    let serialized =
                        toml::to_string_pretty(&root).map_err(|error| McpManagerError::Io(error.to_string()))?;
                    atomic_write(&path, serialized.as_bytes())
                        .map_err(|error| McpManagerError::Io(error.to_string()))?;
                }
                Ok(())
            }
        }
    }
}

fn claude_dir(home: &Path) -> PathBuf {
    home.join(".claude")
}

fn codex_dir(home: &Path) -> PathBuf {
    home.join(".codex")
}

/// Claude 的 MCP 配置在 ~/.claude.json 顶层 `mcpServers`(注意:不是 ~/.claude 目录)。
fn claude_settings_path(home: &Path) -> PathBuf {
    home.join(".claude.json")
}

fn codex_config_path(home: &Path) -> PathBuf {
    codex_dir(home).join("config.toml")
}

fn claude_entry(def: &McpServerDef) -> Value {
    serde_json::json!({
        "command": def.command,
        "args": def.args,
        "env": def.env,
    })
}

fn codex_entry(def: &McpServerDef) -> toml::Table {
    let mut table = toml::Table::new();
    table.insert("command".to_owned(), toml::Value::String(def.command.clone()));
    table.insert(
        "args".to_owned(),
        toml::Value::Array(def.args.iter().map(|arg| toml::Value::String(arg.clone())).collect()),
    );
    let mut env = toml::Table::new();
    for (key, value) in &def.env {
        env.insert(key.clone(), toml::Value::String(value.clone()));
    }
    table.insert("env".to_owned(), toml::Value::Table(env));
    table
}

struct RawServer {
    name: String,
    command: String,
    args: Vec<String>,
    env: std::collections::BTreeMap<String, String>,
}

/// 只导入带 string command 的本地(stdio)服务器;远程(http/sse)条目跳过,MVP 不做转换。
fn parse_claude_servers(text: &str) -> std::result::Result<Vec<RawServer>, String> {
    let root: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    let Some(servers) = root.get("mcpServers").and_then(|value| value.as_object()) else {
        return Ok(Vec::new());
    };
    let mut parsed = Vec::new();
    for (name, entry) in servers {
        if let Some(raw) = raw_server_from_json(name, entry) {
            parsed.push(raw);
        }
    }
    Ok(parsed)
}

fn raw_server_from_json(name: &str, entry: &Value) -> Option<RawServer> {
    let command = entry.get("command")?.as_str()?.to_owned();
    let args = entry
        .get("args")
        .and_then(|value| value.as_array())
        .map(|items| items.iter().filter_map(|item| item.as_str().map(str::to_owned)).collect())
        .unwrap_or_default();
    let mut env = std::collections::BTreeMap::new();
    if let Some(pairs) = entry.get("env").and_then(|value| value.as_object()) {
        for (key, value) in pairs {
            if let Some(text) = value.as_str() {
                env.insert(key.clone(), text.to_owned());
            }
        }
    }
    Some(RawServer { name: name.to_owned(), command, args, env })
}

fn parse_codex_servers(text: &str) -> std::result::Result<Vec<RawServer>, String> {
    let root: toml::Table = text.parse::<toml::Table>().map_err(|error| error.to_string())?;
    let Some(servers) = root.get("mcp_servers").and_then(|value| value.as_table()) else {
        return Ok(Vec::new());
    };
    let mut parsed = Vec::new();
    for (name, entry) in servers {
        let Some(table) = entry.as_table() else { continue };
        let Some(command) = table.get("command").and_then(|value| value.as_str()) else { continue };
        let args = table
            .get("args")
            .and_then(|value| value.as_array())
            .map(|items| items.iter().filter_map(|item| item.as_str().map(str::to_owned)).collect())
            .unwrap_or_default();
        let mut env = std::collections::BTreeMap::new();
        if let Some(pairs) = table.get("env").and_then(|value| value.as_table()) {
            for (key, value) in pairs {
                if let Some(text) = value.as_str() {
                    env.insert(key.clone(), text.to_owned());
                }
            }
        }
        parsed.push(RawServer { name: name.clone(), command: command.to_owned(), args, env });
    }
    Ok(parsed)
}

#[cfg(test)]
mod tests {
    use std::collections::{BTreeMap, BTreeSet};

    use super::*;
    use crate::mcp_manager::model::McpManagerError;

    /// 测试环境:隔离 home(可选建 .claude/.codex 目录)+ 独立 db。
    pub(crate) struct Env {
        pub(crate) _dir: tempfile::TempDir,
        pub(crate) home: PathBuf,
        pub(crate) db: PathBuf,
    }

    pub(crate) fn env() -> Env {
        env_with_targets(true)
    }

    pub(crate) fn env_with_targets(with_targets: bool) -> Env {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("home");
        if with_targets {
            std::fs::create_dir_all(home.join(".claude")).unwrap();
            std::fs::create_dir_all(home.join(".codex")).unwrap();
        }
        let db = dir.path().join("state/mcp-manager.db");
        Env { _dir: dir, home, db }
    }

    pub(crate) fn service(env: &Env) -> McpManagerService {
        McpManagerService::open_with_env(&env.home, &env.db).unwrap()
    }

    fn def(name: &str, command: &str, targets: &[&str]) -> McpServerDef {
        McpServerDef {
            id: String::new(),
            name: name.to_owned(),
            command: command.to_owned(),
            args: vec!["-y".to_owned()],
            env: BTreeMap::new(),
            targets: targets.iter().map(|target| target.to_string()).collect(),
        }
    }

    fn read_claude_json(env: &Env) -> Value {
        serde_json::from_str(&std::fs::read_to_string(env.home.join(".claude.json")).unwrap()).unwrap()
    }

    fn read_codex_toml(env: &Env) -> toml::Table {
        std::fs::read_to_string(env.home.join(".codex/config.toml"))
            .unwrap()
            .parse::<toml::Table>()
            .unwrap()
    }

    #[test]
    fn upsert_creates_updates_and_rejects_bad_input() {
        let env = env();
        let service = service(&env);
        let created = service.upsert(def("fetch", "npx", &["claude"])).unwrap();
        assert!(!created.id.is_empty(), "缺省 id 新建");
        let mut updated = created.clone();
        updated.command = "bunx".to_owned();
        let updated = service.upsert(updated).unwrap();
        assert_eq!(service.list().unwrap().len(), 1);
        assert_eq!(service.list().unwrap()[0].command, "bunx");
        assert_eq!(service.list().unwrap()[0].id, updated.id, "更新不得换 id");

        let mut blank = def("  ", "npx", &["claude"]);
        blank.id = updated.id;
        assert!(matches!(service.upsert(blank), Err(McpManagerError::InvalidInput(_))));
        assert!(matches!(
            service.upsert(def("x", "   ", &["claude"])),
            Err(McpManagerError::InvalidInput(_))
        ));
        assert!(
            matches!(service.upsert(def("x", "npx", &["dsh"])), Err(McpManagerError::InvalidInput(_))),
            "MVP 不接受 dsh 目标"
        );
        let mut same_name = def("fetch", "node", &["codex"]);
        same_name.id = String::new();
        assert!(matches!(service.upsert(same_name), Err(McpManagerError::InvalidInput(_))));
    }

    #[test]
    fn sync_claude_preserves_unrelated_fields_and_entries() {
        let env = env();
        let service = service(&env);
        std::fs::write(
            env.home.join(".claude.json"),
            r#"{"otherTop":{"keep":true},"mcpServers":{"external":{"command":"uvx","args":[],"env":{}}}}"#,
        )
        .unwrap();
        service.upsert(def("fetch", "npx", &["claude"])).unwrap();
        service.sync_target(McpTarget::Claude).unwrap();
        let root = read_claude_json(&env);
        assert_eq!(root["otherTop"]["keep"], serde_json::json!(true), "无关顶层字段保留");
        assert_eq!(root["mcpServers"]["external"]["command"], "uvx", "无关服务器条目保留");
        assert_eq!(root["mcpServers"]["fetch"]["command"], "npx");
        assert_eq!(root["mcpServers"]["fetch"]["args"], serde_json::json!(["-y"]));
        // 再同步一次幂等(读-改-写不产生重复或损坏)
        service.sync_target(McpTarget::Claude).unwrap();
        assert_eq!(read_claude_json(&env)["mcpServers"]["fetch"]["command"], "npx");
    }

    #[test]
    fn sync_codex_roundtrips_toml_and_preserves_other_tables() {
        let env = env();
        let service = service(&env);
        std::fs::write(
            env.home.join(".codex/config.toml"),
            "[other_section]\nkey = \"keep\"\n\n[mcp_servers.external]\ncommand = \"uvx\"\n",
        )
        .unwrap();
        let mut created = def("fetch", "npx", &["codex"]);
        created.env = [("NO_PROXY".to_owned(), "127.0.0.1".to_owned())].into_iter().collect();
        service.upsert(created).unwrap();
        service.sync_target(McpTarget::Codex).unwrap();
        let root = read_codex_toml(&env);
        assert_eq!(root["other_section"]["key"], toml::Value::from("keep"), "无关 TOML 段保留");
        assert_eq!(root["mcp_servers"]["external"]["command"], toml::Value::from("uvx"));
        assert_eq!(root["mcp_servers"]["fetch"]["command"], toml::Value::from("npx"));
        assert_eq!(root["mcp_servers"]["fetch"]["args"][0], toml::Value::from("-y"));
        assert_eq!(root["mcp_servers"]["fetch"]["env"]["NO_PROXY"], toml::Value::from("127.0.0.1"));
    }

    #[test]
    fn sync_creates_missing_files_when_target_dirs_exist() {
        let env = env();
        let service = service(&env);
        service.upsert(def("fetch", "npx", &["claude", "codex"])).unwrap();
        service.sync_target(McpTarget::Claude).unwrap();
        service.sync_target(McpTarget::Codex).unwrap();
        assert!(env.home.join(".claude.json").exists());
        assert!(env.home.join(".codex/config.toml").exists());
        assert_eq!(read_claude_json(&env)["mcpServers"]["fetch"]["command"], "npx");
        assert_eq!(read_codex_toml(&env)["mcp_servers"]["fetch"]["command"], toml::Value::from("npx"));
    }

    #[test]
    fn sync_errors_when_target_directory_missing() {
        let env = env_with_targets(false);
        let service = service(&env);
        service.upsert(def("fetch", "npx", &["claude"])).unwrap();
        let error = service.sync_target(McpTarget::Claude).unwrap_err();
        assert!(matches!(error, McpManagerError::TargetNotInstalled(McpTarget::Claude)));
        assert!(!env.home.join(".claude.json").exists(), "未安装时不得创建文件");
        let error = service.sync_target(McpTarget::Codex).unwrap_err();
        assert!(matches!(error, McpManagerError::TargetNotInstalled(McpTarget::Codex)));
    }

    #[test]
    fn sync_only_projects_servers_targeted_at_the_target() {
        let env = env();
        let service = service(&env);
        service.upsert(def("both", "npx", &["claude", "codex"])).unwrap();
        service.upsert(def("claude-only", "node", &["claude"])).unwrap();
        service.sync_target(McpTarget::Claude).unwrap();
        let servers = read_claude_json(&env)["mcpServers"].as_object().unwrap().clone();
        assert!(servers.contains_key("both") && servers.contains_key("claude-only"));
        assert!(!env.home.join(".codex/config.toml").exists(), "未同步目标不得创建文件");
    }

    #[test]
    fn delete_removes_from_store_and_both_target_files() {
        let env = env();
        let service = service(&env);
        let created = service.upsert(def("fetch", "npx", &["claude"])).unwrap();
        service.sync_target(McpTarget::Claude).unwrap();
        // codex 文件里手工放一份同名条目,验证删除同时清两处
        std::fs::write(env.home.join(".codex/config.toml"), "[mcp_servers.fetch]\ncommand = \"npx\"\n").unwrap();
        service.delete(&created.id).unwrap();
        assert!(service.list().unwrap().is_empty());
        assert!(read_claude_json(&env)["mcpServers"].get("fetch").is_none());
        let codex = read_codex_toml(&env);
        let codex_servers = codex.get("mcp_servers").and_then(|value| value.as_table());
        assert!(codex_servers.map(|table| table.is_empty()).unwrap_or(true), "同名条目须从 codex 移除");
        // 幂等:重复删除不报错
        service.delete(&created.id).unwrap();
    }

    #[test]
    fn import_reads_both_files_and_dedups_by_name() {
        let env = env();
        let service = service(&env);
        std::fs::write(
            env.home.join(".claude.json"),
            r#"{"mcpServers":{"fetch":{"command":"npx","args":["-y"],"env":{"A":"1"}},"remote":{"type":"http","url":"https://x"}}}"#,
        )
        .unwrap();
        std::fs::write(
            env.home.join(".codex/config.toml"),
            "[mcp_servers.fetch]\ncommand = \"bunx\"\nargs = [\"-y\"]\n\n[mcp_servers.fetch.env]\nB = \"2\"\n",
        )
        .unwrap();
        let from_claude = service.import_target(McpTarget::Claude).unwrap();
        assert_eq!(from_claude.len(), 1, "远程条目跳过");
        assert_eq!(from_claude[0].name, "fetch");
        assert_eq!(from_claude[0].command, "npx");
        assert_eq!(from_claude[0].env["A"], "1");
        assert_eq!(from_claude[0].targets, BTreeSet::from(["claude".to_owned()]));

        let from_codex = service.import_target(McpTarget::Codex).unwrap();
        assert_eq!(from_codex.len(), 1);
        assert_eq!(from_codex[0].command, "bunx", "同名导入走更新");
        assert_eq!(from_codex[0].targets, BTreeSet::from(["claude".to_owned(), "codex".to_owned()]));
        assert_eq!(from_codex[0].env["B"], "2", "更新的字段以新导入为准");
        assert_eq!(service.list().unwrap().len(), 1, "按 name 去重入库");
        assert_eq!(service.list().unwrap()[0].id, from_claude[0].id, "同名导入复用既有 id");
    }

    #[test]
    fn import_with_missing_file_is_empty_and_missing_dir_errors() {
        let env = env();
        let empty_service = service(&env);
        assert!(empty_service.import_target(McpTarget::Claude).unwrap().is_empty());
        assert!(empty_service.import_target(McpTarget::Codex).unwrap().is_empty());
        let bare = env_with_targets(false);
        let bare_service = service(&bare);
        assert!(matches!(
            bare_service.import_target(McpTarget::Claude),
            Err(McpManagerError::TargetNotInstalled(_))
        ));
    }

    #[test]
    fn status_reports_target_installation() {
        let env = env();
        let installed_service = service(&env);
        let statuses = installed_service.status().unwrap();
        assert!(statuses.iter().all(|status| status.installed));
        let bare = env_with_targets(false);
        let statuses = service(&bare).status().unwrap();
        assert!(statuses.iter().all(|status| !status.installed));
    }
}
