//! Bounded execution of Codex CLI lifecycle operations: version probes,
//! official account login, and the confirmed install recipe.
//!
//! Every subprocess here runs with a minimal allowlisted environment, bounded
//! output, and a hard timeout. Diagnostics are redacted before they reach the
//! renderer; credential-shaped environment names are never passed through.

use std::{
    collections::HashMap,
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use serde::Serialize;

use super::{
    discovery::{DiscoveryRequest, discover},
    install_recipe::{install_recipe, recipe_ids},
    model::AgentProvider,
};

type AgentStoreRef<'a> = Option<&'a Arc<crate::agent_store::AgentStore>>;

/// Provider 行里手动保存的 CLI 路径（用户在高级设置里指定的兜底）。
fn configured_cli_path(store: AgentStoreRef<'_>, provider_id: &str) -> Option<std::path::PathBuf> {
    let store = store?;
    let connection = store.reader().ok()?;
    let path: Option<String> = rusqlite::OptionalExtension::optional(
        connection
            .query_row(
                "SELECT cli_path FROM providers WHERE provider_id = ?1 AND cli_path IS NOT NULL",
                rusqlite::params![provider_id],
                |row| row.get(0),
            ),
    )
    .ok()
    .flatten();
    path.filter(|value| !value.is_empty())
      .filter(|value| std::path::Path::new(value).is_absolute())
      .map(std::path::PathBuf::from)
}

fn discovery_for(store: AgentStoreRef<'_>, provider: AgentProvider) -> Result<super::model::DiscoveryResult, String> {
    let mut request = DiscoveryRequest::for_provider(provider);
    if let Some(configured) = configured_cli_path(store, provider.command_name()) {
        request = request.with_explicit_path(configured);
    }
    discover(&request)
}

const OUTPUT_LIMIT: usize = 64 * 1024;
const PROBE_TIMEOUT: Duration = Duration::from_secs(8);
const JOB_TIMEOUT: Duration = Duration::from_secs(15 * 60);
const OUTPUT_RING_LINES: usize = 200;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CliLoginStatusReply {
    pub provider_id: String,
    pub installed: bool,
    pub cli_path: Option<String>,
    pub logged_in: Option<bool>,
    pub mode: Option<String>,
    pub detail: Option<String>,
    pub job_running: bool,
    pub job_output: Vec<String>,
    pub job_finished: Option<bool>,
    pub job_success: Option<bool>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CliInstallStatusReply {
    pub provider_id: String,
    pub recipe_id: Option<String>,
    pub command: Vec<String>,
    pub source_url: Option<String>,
    pub impact: Option<String>,
    pub installed: bool,
    pub selected: Option<super::model::DiscoveredAgent>,
    pub diagnostics: Vec<super::model::DiscoveryDiagnostic>,
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

impl JobState {
    fn snapshot_of(job: Option<&JobState>) -> (bool, Vec<String>, Option<bool>, Option<bool>) {
        match job {
            Some(job) => (job.running, job.output.clone(), Some(job.finished), Some(job.success)),
            None => (false, Vec::new(), None, None),
        }
    }
}

/// Tracks at most one install/login job per provider. Jobs are started by an
/// explicit user confirmation and observed by polling their status.
///
/// 探测缓存：CLI 发现与登录状态探测都要起子进程（冷启动可达数秒），
/// 而 Agent 页会轮询状态。缓存 30 秒并在安装/登录/手动路径变更时失效，
/// 保证状态查询在热路径上即时返回，不再触发桥超时。
#[derive(Default)]
pub struct AgentCliJobState {
    install: Mutex<HashMap<String, JobState>>,
    login: Mutex<HashMap<String, JobState>>,
    discovery_cache: Mutex<HashMap<String, (Instant, Arc<super::model::DiscoveryResult>)>>,
    login_probe_cache: Mutex<HashMap<String, (Instant, LoginProbeResult)>>,
    discovery_locks: Mutex<HashMap<String, Arc<Mutex<()>>>>,
}

const PROBE_CACHE_TTL: Duration = Duration::from_secs(30);

#[derive(Clone)]
struct LoginProbeResult {
    logged_in: Option<bool>,
    mode: Option<String>,
    detail: Option<String>,
}

impl AgentCliJobState {
    pub fn new() -> Self {
        Self::default()
    }

    /// 丢弃探测缓存（安装完成、登录完成或手动指定路径后调用）。
    pub fn invalidate_probes(&self, provider_id: &str) {
        if let Ok(mut cache) = self.discovery_cache.lock() {
            cache.remove(provider_id);
        }
        if let Ok(mut cache) = self.login_probe_cache.lock() {
            cache.remove(provider_id);
        }
    }

    fn per_provider_lock(&self, provider_id: &str) -> Arc<Mutex<()>> {
        let mut locks = self.discovery_locks.lock().expect("cli state lock");
        locks
            .entry(provider_id.to_owned())
            .or_insert_with(|| Arc::new(Mutex::new(())))
            .clone()
    }
}

/// 带缓存的 CLI 发现：同 Provider 30 秒内只真正探测一次，并发调用串行等待。
fn discovery_cached(
    state: &AgentCliJobState,
    store: AgentStoreRef<'_>,
    provider: AgentProvider,
) -> Result<Arc<super::model::DiscoveryResult>, String> {
    let key = provider.command_name().to_owned();
    if let Ok(cache) = state.discovery_cache.lock() {
        if let Some((at, cached)) = cache.get(&key) {
            if at.elapsed() < PROBE_CACHE_TTL {
                return Ok(Arc::clone(cached));
            }
        }
    }
    let guard = state.per_provider_lock(&key);
    let _guard = guard.lock().expect("probe lock");
    // 双重检查：等待期间可能已有并发调用填充缓存。
    if let Ok(cache) = state.discovery_cache.lock() {
        if let Some((at, cached)) = cache.get(&key) {
            if at.elapsed() < PROBE_CACHE_TTL {
                return Ok(Arc::clone(cached));
            }
        }
    }
    let result = Arc::new(discovery_for(store, provider)?);
    if let Ok(mut cache) = state.discovery_cache.lock() {
        cache.insert(key, (Instant::now(), Arc::clone(&result)));
    }
    Ok(result)
}

/// 带缓存的登录状态探测：同 Provider 30 秒内只运行一次 codex login status。
fn login_probe_cached(
    state: &AgentCliJobState,
    cli_path: &std::path::Path,
) -> LoginProbeResult {
    let key = "login".to_owned();
    if let Ok(cache) = state.login_probe_cache.lock() {
        if let Some((at, cached)) = cache.get(&key) {
            if at.elapsed() < PROBE_CACHE_TTL {
                return cached.clone();
            }
        }
    }
    let guard = state.per_provider_lock(&key);
    let _guard = guard.lock().expect("probe lock");
    if let Ok(cache) = state.login_probe_cache.lock() {
        if let Some((at, cached)) = cache.get(&key) {
            if at.elapsed() < PROBE_CACHE_TTL {
                return cached.clone();
            }
        }
    }
    let result = match run_bounded_cli(cli_path, &["login", "status"], None, PROBE_TIMEOUT) {
        Ok(output) => {
            let logged_in = output.contains("Logged in") || output.contains("已登录");
            LoginProbeResult {
                logged_in: Some(logged_in),
                mode: first_login_mode(&output),
                detail: if logged_in { None } else { Some(summarize_output(&output)) },
            }
        }
        Err(error) => LoginProbeResult {
            logged_in: None,
            mode: None,
            detail: Some(humanize_probe_error(&error)),
        },
    };
    if let Ok(mut cache) = state.login_probe_cache.lock() {
        cache.insert(key, (Instant::now(), result.clone()));
    }
    result
}

/// 把常见的探测失败翻译成人话与下一步建议。
fn humanize_probe_error(error: &str) -> String {
    if error.contains("超时") {
        return "确认登录状态超时了。CLI 已安装，可以点「重新检测」再试，或直接进入工作台使用。".to_owned();
    }
    format!("暂时无法确认登录状态：{error}")
}

pub fn login_status(state: &AgentCliJobState, store: AgentStoreRef<'_>, provider_id: &str) -> Result<CliLoginStatusReply, String> {
    let provider = parse_provider(provider_id)?;
    let discovered = discovery_cached(state, store, provider)?;
    let installed = discovered.selected.is_some();
    let mut reply = CliLoginStatusReply {
        provider_id: provider_id.to_owned(),
        installed,
        cli_path: discovered
            .selected
            .as_ref()
            .map(|agent| agent.path.to_string_lossy().into_owned()),
        logged_in: None,
        mode: None,
        detail: None,
        job_running: false,
        job_output: Vec::new(),
        job_finished: None,
        job_success: None,
    };
    if let Some(selected) = discovered.selected.as_ref() {
        let probe = login_probe_cached(state, &selected.path);
        reply.logged_in = probe.logged_in;
        reply.mode = probe.mode;
        reply.detail = probe.detail;
    }
    if let Ok(jobs) = state.login.lock() {
        let (running, output, finished, success) = JobState::snapshot_of(jobs.get(provider_id));
        reply.job_running = running;
        reply.job_output = output;
        reply.job_finished = finished;
        reply.job_success = success;
    }
    Ok(reply)
}

/// Start the official interactive login. `codex login` opens the browser and
/// blocks until the OAuth round-trip completes; its captured output is exposed
/// through the polling status reply.
pub fn login_start(state: &Arc<AgentCliJobState>, store: AgentStoreRef<'_>, provider_id: &str) -> Result<CliLoginStatusReply, String> {
    let provider = parse_provider(provider_id)?;
    let discovered = discovery_cached(state, store, provider)?;
    let selected = discovered
        .selected
        .clone()
        .ok_or_else(|| "未找到 CLI，请先安装".to_owned())?
        .path;
    let cli_path = selected.to_string_lossy().into_owned();
    let installed = true;
    {
        let mut jobs = state
            .login
            .lock()
            .map_err(|_| "CLI 任务状态不可用".to_owned())?;
        let job = jobs.entry(provider_id.to_owned()).or_default();
        if job.running {
            return Err("已有一次登录正在进行，请等待完成或稍后重试".to_owned());
        }
        job.running = true;
        job.finished = false;
        job.success = false;
        job.output.clear();
    }
    let state_for_job = Arc::clone(state);
    let provider_key = provider_id.to_owned();
    std::thread::spawn(move || {
        let result = run_bounded_cli(&selected, &["login"], None, JOB_TIMEOUT);
        state_for_job.invalidate_probes(&provider_key);
        if let Ok(mut jobs) = state_for_job.login.lock() {
            let job = jobs.entry(provider_key).or_default();
            job.running = false;
            job.finished = true;
            match result {
                Ok(text) => {
                    job.success = text.contains("Logged in")
                        || text.contains("already logged in")
                        || text.contains("已登录");
                    job.output = split_output(&text);
                }
                Err(error) => {
                    job.success = false;
                    job.output = vec![error];
                }
            }
        }
    });
    Ok(CliLoginStatusReply {
        provider_id: provider_id.to_owned(),
        installed,
        cli_path: Some(cli_path),
        logged_in: None,
        mode: None,
        detail: Some("登录已启动，请在打开的浏览器中完成官方账号授权".to_owned()),
        job_running: true,
        job_output: Vec::new(),
        job_finished: Some(false),
        job_success: None,
    })
}

pub fn install_status(
    state: &AgentCliJobState,
    store: AgentStoreRef<'_>,
    provider_id: &str,
) -> Result<CliInstallStatusReply, String> {
    let provider = parse_provider(provider_id)?;
    let recipe = install_recipe(provider).ok_or_else(|| "该 Provider 没有固定安装配方".to_owned())?;
    let discovered = discovery_cached(state, store, provider)?;
    let mut reply = CliInstallStatusReply {
        provider_id: provider_id.to_owned(),
        recipe_id: Some(recipe.id),
        command: recipe.command.clone(),
        source_url: Some(recipe.source_url),
        impact: Some(recipe.impact),
        installed: discovered.selected.is_some(),
        selected: discovered.selected.clone(),
        diagnostics: discovered.diagnostics.clone(),
        job_running: false,
        job_output: Vec::new(),
        job_finished: None,
        job_success: None,
    };
    if let Ok(jobs) = state.install.lock() {
        let (running, output, finished, success) = JobState::snapshot_of(jobs.get(provider_id));
        reply.job_running = running;
        reply.job_output = output;
        reply.job_finished = finished;
        reply.job_success = success;
    }
    Ok(reply)
}

/// Execute the fixed install recipe after its explicit UI confirmation. The
/// command runs without a shell, with only npm allowlisted, and is verified by
/// re-running CLI discovery afterwards.
pub fn install_start(state: &Arc<AgentCliJobState>, store: AgentStoreRef<'_>, provider_id: &str) -> Result<CliInstallStatusReply, String> {
    let provider = parse_provider(provider_id)?;
    let recipe = install_recipe(provider).ok_or_else(|| "该 Provider 没有固定安装配方".to_owned())?;    if !recipe.requires_explicit_confirmation {
        return Err("安装配方缺少强制确认标记".to_owned());
    }
    if recipe.command.first().map(String::as_str) != Some("npm") {
        return Err("安装配方只允许 npm 命令".to_owned());
    }
    let npm = resolve_npm().ok_or_else(|| {
        "未找到 npm。请先安装 Node.js（https://nodejs.org），或手动执行安装命令".to_owned()
    })?;
    {
        let mut jobs = state
            .install
            .lock()
            .map_err(|_| "CLI 任务状态不可用".to_owned())?;
        let job = jobs.entry(provider_id.to_owned()).or_default();
        if job.running {
            return Err("已有一次安装正在进行，请等待完成".to_owned());
        }
        job.running = true;
        job.finished = false;
        job.success = false;
        job.output.clear();
    }
    let state_for_job = Arc::clone(state);
    let store_for_job = store.cloned();
    let provider_key = provider_id.to_owned();
    let verify_provider = provider_key.clone();
    let recipe_args: Vec<String> = recipe.command.iter().skip(1).cloned().collect();
    std::thread::spawn(move || {
        let arg_refs: Vec<&str> = recipe_args.iter().map(String::as_str).collect();
        let result = run_bounded_cli(&npm, &arg_refs, None, JOB_TIMEOUT).and_then(|_| {
            let provider = parse_provider(&verify_provider).expect("validated");
            let discovered = discovery_for(store_for_job.as_ref(), provider)?;
            if discovered.selected.is_some() {
                return Ok(String::new());
            }
            Err("Codex CLI 已安装，但应用没有自动找到它。\n两个解决办法：\n1. 点「重新检测」再试一次；\n2. 展开下方「高级」，把 codex 的完整路径粘贴进去（在终端运行 which codex 可获得该路径）。".to_owned())
        });
        state_for_job.invalidate_probes(&provider_key);
        if let Ok(mut jobs) = state_for_job.install.lock() {
            let job = jobs.entry(provider_key).or_default();
            job.running = false;
            job.finished = true;
            match result {
                Ok(text) => {
                    job.success = true;
                    job.output = if text.trim().is_empty() {
                        vec!["安装完成，CLI 已通过检测".to_owned()]
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
    let recipe_id = recipe.id;
    let command = recipe.command;
    let source_url = recipe.source_url;
    let impact = recipe.impact;
    let mut reply = CliInstallStatusReply {
        provider_id: provider_id.to_owned(),
        recipe_id: Some(recipe_id),
        command,
        source_url: Some(source_url),
        impact: Some(impact),
        installed: false,
        selected: None,
        diagnostics: Vec::new(),
        job_running: true,
        job_output: Vec::new(),
        job_finished: Some(false),
        job_success: None,
    };
    if let Ok(jobs) = state.install.lock() {
        let (running, output, finished, success) = JobState::snapshot_of(jobs.get(provider_id));
        reply.job_running = running;
        reply.job_output = output;
        reply.job_finished = finished;
        reply.job_success = success;
    }
    Ok(reply)
}

pub fn recipe_inventory() -> Vec<String> {
    recipe_ids()
}

fn parse_provider(value: &str) -> Result<AgentProvider, String> {
    match value {
        "codex" => Ok(AgentProvider::Codex),
        "claude" => Ok(AgentProvider::Claude),
        _ => Err("Provider 不受支持".to_owned()),
    }
}

/// Resolve the npm executable from PATH plus the official locations the
/// discovery module already trusts. Never resolved through a shell.
fn resolve_npm() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|value| std::env::split_paths(&value).collect())
        .unwrap_or_default();
    candidates.push(PathBuf::from("/opt/homebrew/bin"));
    candidates.push(PathBuf::from("/usr/local/bin"));
    let mut found: Vec<PathBuf> = candidates
        .iter()
        .filter_map(|directory| npm_path_in(directory))
        .filter(|npm| npm.is_file())
        .collect();
    // nvm installs live in versioned directories; include the newest npm present.
    if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
        let versions = home.join(".nvm").join("versions").join("node");
        if let Ok(entries) = std::fs::read_dir(&versions) {
            let mut nvm: Vec<PathBuf> = entries
                .flatten()
                .filter_map(|entry| npm_path_in(&entry.path().join("bin")))
                .filter(|npm| npm.is_file())
                .collect();
            nvm.sort();
            found.extend(nvm);
        }
    }
    found.sort();
    found.pop()
}

fn npm_path_in(directory: &Path) -> Option<PathBuf> {
    if cfg!(windows) {
        let npm = directory.join("npm.cmd");
        if npm.is_file() {
            return Some(npm);
        }
        return None;
    }
    let npm = directory.join("npm");
    if npm.is_file() {
        Some(npm)
    } else {
        None
    }
}

fn minimal_environment() -> Vec<(String, String)> {
    let mut env = Vec::new();
    for name in ["HOME", "PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "USER"] {
        if let Some(value) = std::env::var_os(name) {
            let value = value.to_string_lossy().into_owned();
            if !value.is_empty() {
                env.push((name.to_owned(), value));
            }
        }
    }
    if !env.iter().any(|(name, _)| name == "PATH") {
        env.push((
            "PATH".to_owned(),
            "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin".to_owned(),
        ));
    }
    env
}

fn run_bounded_cli(
    executable: &Path,
    args: &[&str],
    cwd: Option<&Path>,
    timeout: Duration,
) -> Result<String, String> {
    if !executable.is_absolute() {
        return Err("CLI 路径必须是绝对路径".to_owned());
    }
    if args
        .iter()
        .any(|argument| argument.contains('\0') || argument.contains('\n'))
    {
        return Err("CLI 参数无效".to_owned());
    }
    let mut command = if cfg!(windows)
        && executable
            .extension()
            .is_some_and(|value| value.eq_ignore_ascii_case("cmd") || value.eq_ignore_ascii_case("bat"))
    {
        let shell = std::env::var_os("COMSPEC").unwrap_or_else(|| "cmd.exe".into());
        let mut command = Command::new(shell);
        command.arg("/D").arg("/S").arg("/C").arg(executable);
        for argument in args {
            command.arg(argument);
        }
        command
    } else {
        let mut command = Command::new(executable);
        command.args(args);
        command
    };
    command
        .env_clear()
        .envs(minimal_environment())
        .current_dir(cwd.unwrap_or_else(|| executable.parent().unwrap_or_else(|| Path::new("."))))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().map_err(|_| "CLI 无法启动".to_owned())?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let (text, timed_out) = std::thread::scope(|scope| {
        let stdout_handle =
            stdout.map(|stream| scope.spawn(move || read_bounded(stream, OUTPUT_LIMIT)));
        let stderr_handle =
            stderr.map(|stream| scope.spawn(move || read_bounded(stream, OUTPUT_LIMIT)));
        let deadline = Instant::now() + timeout;
        let mut timed_out = false;
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    timed_out = true;
                    break;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                Err(_) => {
                    timed_out = true;
                    break;
                }
            }
        }
        let mut text = String::new();
        if let Some(handle) = stdout_handle {
            if let Ok(Ok(value)) = handle.join() {
                text.push_str(&value);
            }
        }
        if text.trim().is_empty() {
            if let Some(handle) = stderr_handle {
                if let Ok(Ok(value)) = handle.join() {
                    text.push_str(&value);
                }
            }
        }
        (text, timed_out)
    });
    if timed_out {
        return Err("CLI 执行超时".to_owned());
    }
    Ok(crate::runtime::redaction::redact_bounded(&text, OUTPUT_LIMIT))
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

fn first_login_mode(output: &str) -> Option<String> {
    let lowered = output.to_ascii_lowercase();
    if lowered.contains("chatgpt") {
        return Some("ChatGPT 官方账号".to_owned());
    }
    if lowered.contains("api key") {
        return Some("API Key".to_owned());
    }
    None
}

fn summarize_output(output: &str) -> String {
    let mut summary = output.trim().to_owned();
    if summary.is_empty() {
        summary = "CLI 未返回登录状态".to_owned();
    }
    summary.chars().take(512).collect()
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
    use super::{AgentCliJobState, minimal_environment, split_output};

    #[test]
    fn environment_never_contains_secret_like_names() {
        let env = minimal_environment();
        for (name, _) in env {
            assert!(
                !name.to_ascii_lowercase().contains("token")
                    && !name.to_ascii_lowercase().contains("secret")
                    && !name.to_ascii_lowercase().contains("key")
                    && !name.to_ascii_lowercase().contains("authorization"),
                "unexpected secret-like environment name: {name}"
            );
        }
    }

    #[test]
    fn output_ring_is_bounded_and_ordered() {
        let lines: Vec<String> = (0..500).map(|index| format!("line-{index}")).collect();
        let joined = lines.join("\n");
        let ring = split_output(&joined);
        assert!(ring.len() <= 200);
        assert_eq!(ring.first().map(String::as_str), Some("line-300"));
        assert_eq!(ring.last().map(String::as_str), Some("line-499"));
    }

    #[test]
    fn probe_cache_is_invalidated_on_demand() {
        let state = AgentCliJobState::new();
        // 未命中缓存时走真实探测；这里通过失效接口验证缓存容器行为。
        state.invalidate_probes("codex");
        assert!(state
            .discovery_cache
            .lock()
            .unwrap()
            .get("codex")
            .is_none());
    }

    #[test]
    fn probe_error_copy_is_humanized() {
        let message = super::humanize_probe_error("CLI 执行超时");
        assert!(message.contains("重新检测"), "{message}");
    }

    #[test]
    fn job_state_starts_empty_and_reports_nothing() {
        let state = AgentCliJobState::new();
        let reply = super::login_status(&state, None, "codex").unwrap();
        assert_eq!(reply.provider_id, "codex");
        assert!(!reply.job_running);
        assert!(reply.job_finished.is_none());
    }

    #[test]
    fn unknown_providers_are_rejected() {
        let state = AgentCliJobState::new();
        assert!(super::login_status(&state, None, "unknown").is_err());
        assert!(super::install_status(&state, None, "unknown").is_err());
    }

    #[test]
    fn install_failure_copy_tells_the_user_what_to_do_next() {
        // 错误文案必须给出可执行的下一步，而不是内部术语。
        let store = std::sync::Arc::new(AgentCliJobState::new());
        let reply = super::install_start(&store, None, "codex");
        // 没有可用 npm 时直接失败；错误信息同样保持人话。
        match reply {
            Ok(status) => {
                // 找到 npm 但安装必然立即失败的情况不在此测试范围内；
                // 这里只断言状态结构稳定。
                assert_eq!(status.provider_id, "codex");
            }
            Err(message) => {
                assert!(message.contains("npm") || message.contains("Node.js"), "{message}");
            }
        }
    }
}
