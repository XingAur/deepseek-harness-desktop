//! 插件市场：读取随桌面壳打包的 awesome-dsh-plugin 目录快照，
//! 通过受管运行时的官方 CLI（`dsh plugin --profile desktop add`，本质是
//! pnpm 转发器）把社区插件安装进当前 Profile。
//!
//! 安全边界：
//! - 目录快照来自固定的 awesome-dsh-plugin 来源，只含 GitHub HTTPS 仓库；
//! - 安装目标必须是目录里登记的仓库 URL，不接受任意入参；
//! - 子进程使用最小环境变量白名单（无任何 secret 形态变量），
//!   有界输出与超时；任务状态可轮询。

use std::{
    collections::HashMap,
    io::Read,
    path::PathBuf,
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use serde::{Deserialize, Serialize};

const OUTPUT_LIMIT: usize = 64 * 1024;
const JOB_TIMEOUT: Duration = Duration::from_secs(15 * 60);
const OUTPUT_RING_LINES: usize = 200;
pub const CATALOG_PAGE_MAX: usize = 50;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginCatalogEntry {
    pub id: String,
    #[serde(default)]
    pub display_name: String,
    pub repo: String,
    pub category: String,
    #[serde(default)]
    pub tarball: Option<String>,
    #[serde(default)]
    pub description_zh: String,
    #[serde(default)]
    pub description_en: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CatalogFile {
    #[serde(default)]
    schema_version: i64,
    #[serde(default)]
    pub entries: Vec<PluginCatalogEntry>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CatalogPage {
    pub total: usize,
    pub offset: usize,
    pub categories: Vec<CatalogCategory>,
    pub entries: Vec<PluginCatalogEntry>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CatalogCategory {
    pub id: String,
    pub count: usize,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginInstallStatusReply {
    pub plugin_id: String,
    pub job_running: bool,
    pub job_output: Vec<String>,
    pub job_finished: Option<bool>,
    pub job_success: Option<bool>,
}

#[derive(Clone, Debug, Default)]
struct JobState {
    running: bool,
    finished: bool,
    success: bool,
    output: Vec<String>,
}

#[derive(Default)]
pub struct PluginMarketState {
    catalog: Mutex<Option<Arc<Vec<PluginCatalogEntry>>>>,
    install: Mutex<HashMap<String, JobState>>,
}

impl PluginMarketState {
    pub fn new() -> Self {
        Self::default()
    }
}

fn load_catalog(
    state: &PluginMarketState,
    resource_dir: &std::path::Path,
    force_reload: bool,
) -> Result<Arc<Vec<PluginCatalogEntry>>, String> {
    let mut slot = state
        .catalog
        .lock()
        .map_err(|_| "插件市场状态不可用".to_owned())?;
    if !force_reload {
        if let Some(cached) = slot.as_ref() {
            return Ok(Arc::clone(cached));
        }
    } else {
        // 开发者替换了随应用资源目录中的快照后，刷新按钮必须真正读取新文件。
        *slot = None;
    }
    let path = resource_dir.join("plugin-catalog").join("plugins.json");
    let raw = std::fs::read(&path).map_err(|_| {
        "插件目录未随应用打包。请更新应用到包含插件市场的版本".to_owned()
    })?;
    let parsed: CatalogFile = serde_json::from_slice(&raw)
        .map_err(|_| "插件目录快照损坏，请重新安装应用".to_owned())?;
    if parsed.schema_version != 1 {
        return Err("插件目录快照版本不受支持".to_owned());
    }
    let catalog = Arc::new(parsed.entries);
    *slot = Some(Arc::clone(&catalog));
    Ok(catalog)
}

fn catalog_categories(catalog: &[PluginCatalogEntry]) -> Vec<CatalogCategory> {
    let mut counts: HashMap<String, usize> = HashMap::new();
    for entry in catalog {
        *counts.entry(entry.category.clone()).or_default() += 1;
    }
    let mut categories: Vec<CatalogCategory> = counts
        .into_iter()
        .map(|(id, count)| CatalogCategory { id, count })
        .collect();
    categories.sort_by(|left, right| right.count.cmp(&left.count).then(left.id.cmp(&right.id)));
    categories
}

fn entry_matches(entry: &PluginCatalogEntry, query: &str) -> bool {
    if query.is_empty() {
        return true;
    }
    let haystacks = [
        entry.id.as_str(),
        entry.display_name.as_str(),
        entry.category.as_str(),
        entry.description_zh.as_str(),
        entry.description_en.as_str(),
    ];
    haystacks
        .iter()
        .any(|value| value.to_lowercase().contains(&query.to_lowercase()))
}

pub fn catalog_page(
    state: &PluginMarketState,
    resource_dir: &std::path::Path,
    query: &str,
    category: &str,
    offset: usize,
    limit: usize,
    force_reload: bool,
) -> Result<CatalogPage, String> {
    if query.len() > 120 || category.len() > 64 {
        return Err("搜索条件无效".to_owned());
    }
    let offset = offset.min(10_000);
    let limit = limit.clamp(1, CATALOG_PAGE_MAX);
    let catalog = load_catalog(state, resource_dir, force_reload)?;
    let mut matched = catalog
        .iter()
        .filter(|entry| category.is_empty() || entry.category == category)
        .filter(|entry| entry_matches(entry, query))
        .peekable();
    let total = matched.clone().count();
    let entries: Vec<PluginCatalogEntry> = matched
        .skip(offset)
        .take(limit)
        .cloned()
        .collect();
    Ok(CatalogPage {
        total,
        offset,
        categories: catalog_categories(&catalog),
        entries,
    })
}

fn install_status_of(state: &PluginMarketState, plugin_id: &str) -> PluginInstallStatusReply {
    let mut reply = PluginInstallStatusReply {
        plugin_id: plugin_id.to_owned(),
        job_running: false,
        job_output: Vec::new(),
        job_finished: None,
        job_success: None,
    };
    if let Ok(jobs) = state.install.lock() {
        if let Some(job) = jobs.get(plugin_id) {
            reply.job_running = job.running;
            reply.job_output = job.output.clone();
            reply.job_finished = Some(job.finished);
            reply.job_success = Some(job.success);
        }
    }
    reply
}

pub fn install_status(
    state: &PluginMarketState,
    resource_dir: &std::path::Path,
    plugin_id: &str,
) -> Result<PluginInstallStatusReply, String> {
    validate_plugin_id(plugin_id)?;
    let _ = load_catalog(state, resource_dir, false)?;
    Ok(install_status_of(state, plugin_id))
}

fn validate_plugin_id(plugin_id: &str) -> Result<(), String> {
    let mut characters = plugin_id.chars();
    let valid = !plugin_id.is_empty()
        && plugin_id.len() <= 160
        && characters.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && plugin_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | '/'));
    if valid {
        Ok(())
    } else {
        Err("插件标识无效".to_owned())
    }
}

/// 启动安装：目标仓库必须来自目录快照；经官方 CLI（pnpm 转发器）安装
/// 到当前 Profile 的 desktop profile，安装结果由轮询获取。
pub fn install_start(
    state: &Arc<PluginMarketState>,
    resource_dir: &std::path::Path,
    runtime_root: &std::path::Path,
    data_root: &std::path::Path,
    plugin_id: &str,
) -> Result<PluginInstallStatusReply, String> {
    validate_plugin_id(plugin_id)?;
    let catalog = load_catalog(state, resource_dir, false)?;
    let entry = catalog
        .iter()
        .find(|entry| entry.id == plugin_id)
        .ok_or_else(|| "插件不在目录里：请刷新插件市场".to_owned())?;
    let repo = entry.repo.clone();

    let runtime = runtime_version_dir(runtime_root)?;
    let node = runtime.join(if cfg!(windows) { "node.exe" } else { "bin/node" });
    if !node.is_file() {
        return Err("受管 Runtime 缺少 Node，无法安装插件".to_owned());
    }
    let dsh_bin = runtime
        .join("app")
        .join("node_modules")
        .join("@deepseek-ai")
        .join("dsh")
        .join("lib")
        .join("bin.js");
    if !dsh_bin.is_file() {
        return Err("受管 Runtime 缺少 dsh CLI，无法安装插件".to_owned());
    }
    let desktop_bin = runtime.join("desktop-bin");
    let profile_dir = data_root.join("profiles").join("desktop");
    std::fs::create_dir_all(&profile_dir).map_err(|_| "Profile 目录不可用".to_owned())?;

    {
        let mut jobs = state
            .install
            .lock()
            .map_err(|_| "插件市场状态不可用".to_owned())?;
        let job = jobs.entry(plugin_id.to_owned()).or_default();
        if job.running {
            return Err("这个插件正在安装中，请等待完成".to_owned());
        }
        job.running = true;
        job.finished = false;
        job.success = false;
        job.output.clear();
    }

    let state_for_job = Arc::clone(state);
    let plugin_key = plugin_id.to_owned();
    let data_root_owned = data_root.to_path_buf();
    std::thread::spawn(move || {
        let result = run_install(&node, &dsh_bin, &desktop_bin, &profile_dir, &data_root_owned, &repo);
        if let Ok(mut jobs) = state_for_job.install.lock() {
            let job = jobs.entry(plugin_key).or_default();
            job.running = false;
            job.finished = true;
            match result {
                Ok(text) => {
                    job.success = true;
                    job.output = if text.trim().is_empty() {
                        vec!["安装完成。新插件会在下一次会话加载".to_owned()]
                    } else {
                        split_output(&text)
                    };
                }
                Err(error) => {
                    job.success = false;
                    job.output = vec![error];
                }
            }
        }
    });
    Ok(install_status_of(state, plugin_id))
}

fn runtime_version_dir(runtime_root: &std::path::Path) -> Result<PathBuf, String> {
    let current: serde_json::Value = serde_json::from_slice(
        &std::fs::read(runtime_root.join("current.json"))
            .map_err(|_| "受管 Runtime 尚未激活，无法安装插件".to_owned())?,
    )
    .map_err(|_| "受管 Runtime 当前版本信息无效".to_owned())?;
    let version = current
        .get("version")
        .and_then(|value| value.as_str())
        .ok_or_else(|| "受管 Runtime 当前版本信息无效".to_owned())?;
    Ok(runtime_root.join("versions").join(version))
}

fn run_install(
    node: &std::path::Path,
    dsh_bin: &std::path::Path,
    desktop_bin: &std::path::Path,
    profile_dir: &std::path::Path,
    data_root: &std::path::Path,
    repo: &str,
) -> Result<String, String> {
    let mut path_value = desktop_bin.to_string_lossy().into_owned();
    path_value.push_str(":/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin");
    let mut command = Command::new(node);
    command
        .arg(dsh_bin)
        .args(["plugin", "--profile", "desktop", "add", repo])
        .env_clear()
        .env("DSH_HOME", data_root)
        .env("PATH", &path_value)
        .current_dir(profile_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // 受管 node 属控制台子系统进程,禁窗避免安装时闪黑框。
    #[cfg(windows)]
    command.creation_flags(crate::runtime::process::CREATE_NO_WINDOW);
    for name in ["HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "USER"] {
        if let Some(value) = std::env::var_os(name) {
            let value = value.to_string_lossy().into_owned();
            if !value.is_empty() {
                command.env(name, value);
            }
        }
    }
    if cfg!(windows) {
        if let Some(app_data) = std::env::var_os("APPDATA") {
            command.env("APPDATA", app_data);
        }
        if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA") {
            command.env("LOCALAPPDATA", local_app_data);
        }
    }
    let mut child = command.spawn().map_err(|_| "安装进程无法启动".to_owned())?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let (text, timed_out, succeeded) = std::thread::scope(|scope| {
        let stdout_handle =
            stdout.map(|stream| scope.spawn(move || read_bounded(stream, OUTPUT_LIMIT)));
        let stderr_handle =
            stderr.map(|stream| scope.spawn(move || read_bounded(stream, OUTPUT_LIMIT)));
        let deadline = Instant::now() + JOB_TIMEOUT;
        let mut timed_out = false;
        let mut succeeded = false;
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    succeeded = status.success();
                    break;
                }
                Ok(None) if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    timed_out = true;
                    break;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(20)),
                Err(_) => {
                    timed_out = true;
                    break;
                }
            }
        }
        let mut text = String::new();
        if let Some(handle) = stdout_handle {
            match handle.join() {
                Ok(Ok(value)) => text.push_str(&value),
                Ok(Err(_)) => text.push_str("\n[标准输出读取失败]"),
                Err(_) => text.push_str("\n[标准输出线程失败]"),
            }
        }
        if let Some(handle) = stderr_handle {
            match handle.join() {
                Ok(Ok(value)) => text.push_str(&value),
                Ok(Err(_)) => text.push_str("\n[错误输出读取失败]"),
                Err(_) => text.push_str("\n[错误输出线程失败]"),
            }
        }
        (text, timed_out, succeeded)
    });
    if timed_out {
        return Err("安装超时（15 分钟）。可以稍后重试".to_owned());
    }
    let output = crate::runtime::redaction::redact_bounded(&text, OUTPUT_LIMIT);
    if !succeeded {
        return Err(if output.trim().is_empty() {
            "插件安装失败：安装命令返回失败状态".to_owned()
        } else {
            format!("插件安装失败：{}", output.trim())
        });
    }
    Ok(output)
}

fn read_bounded(mut reader: impl Read, limit: usize) -> Result<String, ()> {
    let mut output = Vec::with_capacity(4096);
    let mut buffer = [0_u8; 1024];
    loop {
        let count = reader.read(&mut buffer).map_err(|_| ())?;
        if count == 0 {
            break;
        }
        if output.len().saturating_add(count) > limit {
            return Err(());
        }
        output.extend_from_slice(&buffer[..count]);
    }
    Ok(String::from_utf8_lossy(&output).into_owned())
}

fn split_output(text: &str) -> Vec<String> {
    text.lines()
        .map(|line| line.trim())
        .filter(|line| !line.is_empty())
        .map(|line| line.chars().take(512).collect::<String>())
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .take(OUTPUT_RING_LINES)
        .rev()
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture_entry(id: &str, category: &str, zh: &str) -> PluginCatalogEntry {
        PluginCatalogEntry {
            id: id.to_owned(),
            display_name: id.to_owned(),
            repo: format!("https://github.com/{id}"),
            category: category.to_owned(),
            tarball: None,
            description_zh: zh.to_owned(),
            description_en: String::new(),
        }
    }

    #[test]
    fn plugin_ids_are_validated_before_any_lookup() {
        assert!(validate_plugin_id("owner/repo").is_ok());
        assert!(validate_plugin_id("dshmarket").is_ok());
        assert!(validate_plugin_id("").is_err());
        assert!(validate_plugin_id("../escape").is_err());
        assert!(validate_plugin_id("a b").is_err());
    }

    #[test]
    fn catalog_search_matches_chinese_descriptions_case_insensitively() {
        let entry = fixture_entry("a/b", "tools", "支持余额查询的插件");
        assert!(entry_matches(&entry, "余额"));
        assert!(entry_matches(&entry, "Tools"));
        assert!(!entry_matches(&entry, "主题"));
    }

    #[test]
    fn page_limits_are_enforced() {
        let state = PluginMarketState::new();
        let dir = std::env::temp_dir();
        // 没有目录文件时应报错而不是 panic
        assert!(catalog_page(&state, &dir, "", "", 0, 50, false).is_err());
    }

    #[test]
    fn force_reload_reads_a_replaced_catalog_snapshot() {
        let root = std::env::temp_dir().join(format!(
            "dsh-plugin-catalog-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock before unix epoch")
                .as_nanos()
        ));
        let catalog_dir = root.join("plugin-catalog");
        std::fs::create_dir_all(&catalog_dir).expect("create catalog directory");
        let first = serde_json::json!({ "schemaVersion": 1, "entries": [fixture_entry("first/repo", "tools", "first")] });
        std::fs::write(catalog_dir.join("plugins.json"), first.to_string()).expect("write first catalog");
        let state = PluginMarketState::new();
        let page = catalog_page(&state, &root, "", "", 0, 50, false).expect("load first catalog");
        assert_eq!(page.entries[0].id, "first/repo");

        let second = serde_json::json!({ "schemaVersion": 1, "entries": [fixture_entry("second/repo", "tools", "second")] });
        std::fs::write(catalog_dir.join("plugins.json"), second.to_string()).expect("replace catalog");
        let cached = catalog_page(&state, &root, "", "", 0, 50, false).expect("load cached catalog");
        assert_eq!(cached.entries[0].id, "first/repo");
        let refreshed = catalog_page(&state, &root, "", "", 0, 50, true).expect("reload catalog");
        assert_eq!(refreshed.entries[0].id, "second/repo");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn output_ring_is_bounded() {
        let lines: Vec<String> = (0..500).map(|i| format!("l{i}")).collect();
        let ring = split_output(&lines.join("\n"));
        assert!(ring.len() <= OUTPUT_RING_LINES);
        assert_eq!(ring.last().map(String::as_str), Some("l499"));
    }

    #[cfg(unix)]
    #[test]
    fn failed_install_command_is_reported_as_failure() {
        let root = std::env::temp_dir().join(format!(
            "dsh-plugin-install-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock before unix epoch")
                .as_nanos()
        ));
        let profile = root.join("profile");
        let script = root.join("dsh-failing.sh");
        std::fs::create_dir_all(&profile).expect("create test profile");
        std::fs::write(&script, "exit 7\n").expect("write failing command");

        let result = run_install(
            std::path::Path::new("/bin/sh"),
            &script,
            std::path::Path::new("/bin"),
            &profile,
            &root,
            "https://github.com/example/plugin",
        );

        let _ = std::fs::remove_dir_all(&root);
        let error = result.expect_err("failed install command must not be successful");
        assert!(error.contains("插件安装失败"));
    }
}
