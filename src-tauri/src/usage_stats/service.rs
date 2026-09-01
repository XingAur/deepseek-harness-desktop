use std::{
    collections::BTreeMap,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
};

use serde_json::Value;

use crate::{
    profile::model::ProfileRecord,
    storage::atomic_json::read_optional,
    usage_stats::model::{UsageEntry, UsageLine, UsageSummary},
};

/// 单个 Profile 数据根最多统计的会话文件数(超出记 failure,防止失控目录拖垮面板)。
const MAX_FILES_PER_ROOT: usize = 500;
/// 全部数据根合计的会话文件数上限。
const MAX_TOTAL_FILES: usize = 2000;
/// 递归下探深度上限(会话文件正常在浅层目录,超深嵌套按失控处理)。
const MAX_SCAN_DEPTH: usize = 32;
/// 全部数据根合计最多读取 128MB，避免一次刷新耗尽内存和磁盘带宽。
const MAX_TOTAL_BYTES: usize = 128 * 1024 * 1024;
/// 单文件只读前 2MB;超出部分的截断行丢弃。
const MAX_FILE_BYTES: usize = 2 * 1024 * 1024;
/// 单条 JSONL 记录的最大字节数，异常长行直接跳过。
const MAX_LINE_BYTES: usize = 128 * 1024;
/// 聚合键数量上限，避免不可信 model 值制造无界 map。
const MAX_AGGREGATE_ENTRIES: usize = 1024;
/// model 标识长度上限。
const MAX_MODEL_BYTES: usize = 200;
/// 读取缓冲块大小。
const READ_CHUNK: usize = 64 * 1024;
/// 无 timestamp 或解析失败时的兜底日期。
const EPOCH_DAY: &str = "1970-01-01";

/// 用量统计服务(MVP 无持久化,每次 `summary()` 全量扫描):
/// - 生产构造 `open` 接 profiles 根,读 `profiles.json` 得到各 Profile 的 `data_root` 作为扫描根;
/// - 测试构造 `with_roots` 直接注入扫描根,绕过 profiles 读取;
/// - 只统计含 `message.usage` 对象的行(Claude Code 会话 JSONL 风格),其余行跳过。
pub struct UsageStatsService {
    profile_root: PathBuf,
    /// 直接注入的扫描根(with_roots 构造);非空时优先于 profile_root。
    injected_roots: Vec<PathBuf>,
}

impl UsageStatsService {
    /// 生产构造:`profile_root` 是含 `profiles.json` 的 profiles 根。
    pub fn open(profile_root: PathBuf) -> Self {
        Self { profile_root, injected_roots: Vec::new() }
    }

    /// 测试/注入构造:直接给定数据根列表,不读 profiles.json(仅测试使用)。
    #[cfg(test)]
    pub fn with_roots(roots: Vec<PathBuf>) -> Self {
        Self { profile_root: PathBuf::new(), injected_roots: roots }
    }

    /// 全量扫描并聚合。个别文件/目录失败记入 `failures` 并继续,不中断整体统计。
    pub fn summary(&self) -> UsageSummary {
        let mut failures = Vec::new();
        let roots = self.scan_roots(&mut failures);
        let mut summary = UsageSummary::default();
        let mut aggregated = BTreeMap::<(String, String), UsageEntry>::new();
        let mut total_files = 0usize;
        let mut total_bytes = 0usize;
        let mut aggregate_capped = false;
        for root in roots {
            if !root.is_dir() {
                continue;
            }
            let mut collector = Collector::default();
            collect_jsonl_files(&root, 0, &mut collector, &mut failures);
            if collector.capped {
                failures.push(format!("会话文件数超过上限 {MAX_FILES_PER_ROOT},已忽略更多文件"));
            }
            for file in collector.files {
                if total_files >= MAX_TOTAL_FILES {
                    failures.push(format!(
                        "会话文件总数超过上限 {MAX_TOTAL_FILES},已停止扫描更多文件"
                    ));
                    break;
                }
                if total_bytes >= MAX_TOTAL_BYTES {
                    failures.push(format!("会话数据读取总量超过上限 {MAX_TOTAL_BYTES},已停止扫描更多文件"));
                    break;
                }
                total_files += 1;
                summary.files_scanned += 1;
                match scan_file(&file, MAX_TOTAL_BYTES - total_bytes) {
                    Ok((lines, bytes_read)) => {
                        total_bytes += bytes_read;
                        // 有至少一条可统计行的文件才算「扫描到的会话」。
                        if !lines.is_empty() {
                            summary.sessions_scanned += 1;
                        }
                        for line in lines {
                            let key = (line.day.clone(), line.model.clone());
                            if !aggregated.contains_key(&key) && aggregated.len() >= MAX_AGGREGATE_ENTRIES {
                                if !aggregate_capped {
                                    failures.push(format!("用量聚合条目超过上限 {MAX_AGGREGATE_ENTRIES},已忽略更多模型"));
                                    aggregate_capped = true;
                                }
                                continue;
                            }
                            aggregated
                                .entry(key)
                                .or_default()
                                .add(&line);
                        }
                    }
                    Err(_) => failures.push("会话文件读取失败".into()),
                }
            }
        }

        // BTreeMap 已按 (day, model) 有序;再按日稳定排序,显式保证「按日排序」契约。
        summary.entries = aggregated
            .into_iter()
            .map(|((day, model), mut entry)| {
                entry.day = day;
                entry.model = model;
                entry
            })
            .collect();
        summary.entries.sort_by(|left, right| left.day.cmp(&right.day));
        for entry in &summary.entries {
            summary.totals.requests += entry.requests;
            summary.totals.input_tokens += entry.input_tokens;
            summary.totals.output_tokens += entry.output_tokens;
            summary.totals.cache_creation_tokens += entry.cache_creation_tokens;
            summary.totals.cache_read_tokens += entry.cache_read_tokens;
        }
        summary.failures = failures;
        summary
    }

    /// 解析扫描根:注入根优先;否则读 profiles.json 取各 Profile 的 data_root。
    fn scan_roots(&self, failures: &mut Vec<String>) -> Vec<PathBuf> {
        if !self.injected_roots.is_empty() {
            return self.injected_roots.clone();
        }
        let profiles_path = self.profile_root.join("profiles.json");
        match read_optional::<Vec<ProfileRecord>>(&profiles_path) {
            Ok(Some(profiles)) => profiles.into_iter().map(|profile| profile.data_root).collect(),
            // 无 profiles 文件 = 尚无任何 Profile,返回空汇总而非报错。
            Ok(None) => Vec::new(),
            Err(error) => {
                let _ = error;
                failures.push("用量配置读取失败".into());
                Vec::new()
            }
        }
    }
}

/// 会话文件收集器:单根收集到上限后再遇到新文件即标记 capped(用于记 failure)。
#[derive(Default)]
struct Collector {
    files: Vec<PathBuf>,
    capped: bool,
}

impl Collector {
    fn try_push(&mut self, path: PathBuf) {
        if self.files.len() >= MAX_FILES_PER_ROOT {
            self.capped = true;
            return;
        }
        self.files.push(path);
    }
}

/// 递归收集 *.jsonl;跳过符号链接(文件与目录都不跟随)。
fn collect_jsonl_files(root: &Path, depth: usize, collector: &mut Collector, failures: &mut Vec<String>) {
    if collector.capped {
        return;
    }
    if depth > MAX_SCAN_DEPTH {
        failures.push(format!("会话目录嵌套超过深度上限 {MAX_SCAN_DEPTH}"));
        return;
    }
    let entries = match std::fs::read_dir(root) {
        Ok(entries) => entries,
        Err(_) => {
            failures.push("会话目录无法读取".into());
            return;
        }
    };
    let mut subdirs = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        // 只取「条目本身」的属性:符号链接一律跳过,绝不跟随。
        let Ok(metadata) = std::fs::symlink_metadata(&path) else { continue };
        if metadata.file_type().is_symlink() {
            continue;
        }
        if metadata.is_dir() {
            subdirs.push(path);
        } else if path.extension().and_then(|extension| extension.to_str()) == Some("jsonl") {
            collector.try_push(path);
        }
    }
    for subdir in subdirs {
        if collector.capped {
            return;
        }
        collect_jsonl_files(&subdir, depth + 1, collector, failures);
    }
}

/// 读取单个会话文件(受全局和单文件共同限制)并解析出带 `message.usage` 的行。
fn scan_file(path: &Path, remaining_budget: usize) -> Result<(Vec<UsageLine>, usize), String> {
    let mut file = File::open(path).map_err(|error| error.to_string())?;
    let (bytes, truncated) = read_capped(&mut file, MAX_FILE_BYTES.min(remaining_budget))?;
    let bytes_read = bytes.len();
    let text = String::from_utf8_lossy(&bytes);
    // 截断文件的最后一行不完整:只处理到最后一个换行为止。
    let usable: &str = if truncated {
        match text.rfind('\n') {
            Some(position) => &text[..position + 1],
            None => "",
        }
    } else {
        text.as_ref()
    };
    let mut lines = Vec::new();
    for raw in usable.split('\n') {
        let line = raw.strip_suffix('\r').unwrap_or(raw).trim();
        if line.is_empty() || line.len() > MAX_LINE_BYTES {
            continue;
        }
        if let Some(entry) = parse_usage_line(line) {
            lines.push(entry);
        }
    }
    Ok((lines, bytes_read))
}

/// 最多读 `limit` 字节;返回 (内容, 是否发生截断)。
fn read_capped(file: &mut File, limit: usize) -> Result<(Vec<u8>, bool), String> {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; READ_CHUNK];
    loop {
        let read = file.read(&mut chunk).map_err(|error| error.to_string())?;
        if read == 0 {
            return Ok((buffer, false));
        }
        if buffer.len() + read > limit {
            let remaining = limit - buffer.len();
            buffer.extend_from_slice(&chunk[..remaining]);
            return Ok((buffer, true));
        }
        buffer.extend_from_slice(&chunk[..read]);
    }
}

/// 只统计含 `message.usage` 对象的行;解析失败/形状不符一律返回 None 跳过。
fn parse_usage_line(line: &str) -> Option<UsageLine> {
    let value: Value = serde_json::from_str(line).ok()?;
    let message = value.get("message")?;
    let usage = message.get("usage")?.as_object()?;
    let token = |key: &str| usage.get(key).and_then(Value::as_u64).unwrap_or(0);
    let model = message.get("model").and_then(Value::as_str).unwrap_or("unknown");
    Some(UsageLine {
        day: timestamp_day(value.get("timestamp")),
        model: if model.len() <= MAX_MODEL_BYTES { model.to_owned() } else { "unknown".to_owned() },
        input_tokens: token("input_tokens"),
        output_tokens: token("output_tokens"),
        cache_creation_tokens: token("cache_creation_input_tokens"),
        cache_read_tokens: token("cache_read_input_tokens"),
    })
}

/// timestamp 为 ISO-8601 字符串时取其 UTC 日期;缺失/非字符串/解析失败归 1970-01-01。
fn timestamp_day(timestamp: Option<&Value>) -> String {
    let Some(text) = timestamp.and_then(Value::as_str) else { return EPOCH_DAY.to_owned() };
    chrono::DateTime::parse_from_rfc3339(text)
        .map(|time| time.with_timezone(&chrono::Utc).format("%Y-%m-%d").to_string())
        .unwrap_or_else(|_| EPOCH_DAY.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::profile::model::{AgentPermissionMode, PermissionMode, ProfileRecord};

    struct Env {
        _dir: tempfile::TempDir,
        profiles_root: PathBuf,
    }

    fn env() -> Env {
        let dir = tempfile::tempdir().unwrap();
        let profiles_root = dir.path().join("profiles");
        std::fs::create_dir_all(&profiles_root).unwrap();
        Env { _dir: dir, profiles_root }
    }

    fn profile(name: &str, data_root: PathBuf) -> ProfileRecord {
        ProfileRecord {
            id: uuid::Uuid::new_v4(),
            name: name.into(),
            data_root,
            permission_mode: PermissionMode::WorkspaceWrite,
            agent_permission_default: AgentPermissionMode::default(),
            revision: 1,
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        }
    }

    fn write_profiles(env: &Env, profiles: &[ProfileRecord]) {
        let path = env.profiles_root.join("profiles.json");
        std::fs::write(path, serde_json::to_vec_pretty(profiles).unwrap()).unwrap();
    }

    fn session_file(dir: &Path, name: &str, lines: &[String]) {
        std::fs::create_dir_all(dir).unwrap();
        std::fs::write(dir.join(name), lines.join("\n")).unwrap();
    }

    fn usage_line(day: &str, model: &str, input: u64, output: u64) -> String {
        format!(
            r#"{{"timestamp":"{day}T12:00:00.000Z","message":{{"model":"{model}","usage":{{"input_tokens":{input},"output_tokens":{output},"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}}}}"#
        )
    }

    fn no_timestamp_line(model: &str, input: u64, output: u64) -> String {
        format!(
            r#"{{"message":{{"model":"{model}","usage":{{"input_tokens":{input},"output_tokens":{output}}}}}}}"#
        )
    }

    fn service(env: &Env) -> UsageStatsService {
        UsageStatsService::open(env.profiles_root.clone())
    }

    #[test]
    fn aggregates_multiple_days_and_models() {
        let env = env();
        let data_root = env._dir.path().join("profiles-data/main");
        write_profiles(&env, &[profile("Main", data_root.clone())]);
        session_file(
            &data_root,
            "session-a.jsonl",
            &[
                usage_line("2026-08-29", "deepseek-chat", 100, 10),
                usage_line("2026-08-30", "deepseek-chat", 200, 20),
                usage_line("2026-08-30", "deepseek-reasoner", 300, 30),
                usage_line("2026-08-30", "deepseek-reasoner", 1, 2),
            ],
        );

        let summary = service(&env).summary();
        assert!(summary.failures.is_empty(), "意外失败: {:?}", summary.failures);
        assert_eq!(summary.files_scanned, 1);
        assert_eq!(summary.sessions_scanned, 1);
        // entries 按 (day, model) 有序:两天两个模型共 3 条。
        assert_eq!(summary.entries.len(), 3);
        assert_eq!(summary.entries[0].day, "2026-08-29");
        assert_eq!(summary.entries[0].model, "deepseek-chat");
        assert_eq!(summary.entries[0].requests, 1);
        assert_eq!(summary.entries[1].day, "2026-08-30");
        assert_eq!(summary.entries[1].model, "deepseek-chat");
        assert_eq!(summary.entries[2].model, "deepseek-reasoner");
        assert_eq!(summary.entries[2].requests, 2);
        assert_eq!(summary.entries[2].input_tokens, 301);
        assert_eq!(summary.entries[2].output_tokens, 32);
        assert_eq!(summary.totals.requests, 4);
        assert_eq!(summary.totals.input_tokens, 601);
        assert_eq!(summary.totals.output_tokens, 62);
        assert_eq!(summary.totals.day, "", "totals 行 day 为空串");
    }

    #[test]
    fn cache_tokens_default_to_zero_when_absent() {
        let env = env();
        let data_root = env._dir.path().join("profiles-data/main");
        write_profiles(&env, &[profile("Main", data_root.clone())]);
        session_file(&data_root, "session.jsonl", &[no_timestamp_line("deepseek-chat", 5, 6)]);

        let summary = service(&env).summary();
        let entry = &summary.entries[0];
        assert_eq!(entry.cache_creation_tokens, 0);
        assert_eq!(entry.cache_read_tokens, 0);
        assert_eq!(entry.day, EPOCH_DAY, "timestamp 缺失归 1970-01-01");
    }

    #[test]
    fn skips_invalid_lines_and_missing_usage_and_defaults_model_to_unknown() {
        let env = env();
        let data_root = env._dir.path().join("profiles-data/main");
        write_profiles(&env, &[profile("Main", data_root.clone())]);
        session_file(
            &data_root,
            "session.jsonl",
            &[
                "not json at all".to_owned(),
                r#"{"message":{"model":"x"}}"#.to_owned(),
                r#"{"message":{"usage":"not-an-object"}}"#.to_owned(),
                r#"{"timestamp":"not-a-date","message":{"usage":{"input_tokens":7,"output_tokens":8}}}"#.to_owned(),
                usage_line("2026-08-30", "deepseek-chat", 1, 1),
            ],
        );

        let summary = service(&env).summary();
        assert!(summary.failures.is_empty());
        assert_eq!(summary.entries.len(), 2, "非法行/无 usage 行跳过");
        // timestamp 不是合法 RFC3339 → 归 1970;model 缺省 unknown。
        let epoch = summary.entries.iter().find(|entry| entry.day == EPOCH_DAY).unwrap();
        assert_eq!(epoch.model, "unknown");
        assert_eq!(epoch.input_tokens, 7);
        let counted = summary.entries.iter().find(|entry| entry.day == "2026-08-30").unwrap();
        assert_eq!(counted.requests, 1);
        assert_eq!(summary.totals.requests, 2);
    }

    #[test]
    fn missing_timestamp_falls_back_to_epoch_day() {
        let env = env();
        let data_root = env._dir.path().join("profiles-data/main");
        write_profiles(&env, &[profile("Main", data_root.clone())]);
        session_file(
            &data_root,
            "session.jsonl",
            &[no_timestamp_line("deepseek-chat", 10, 20), usage_line("2026-08-30", "deepseek-chat", 1, 1)],
        );

        let summary = service(&env).summary();
        assert_eq!(summary.entries.len(), 2);
        assert_eq!(summary.entries[0].day, EPOCH_DAY);
        assert_eq!(summary.entries[0].requests, 1);
        assert_eq!(summary.entries[1].day, "2026-08-30");
    }

    #[test]
    fn overlong_model_identifier_is_normalized_before_aggregation() {
        let line = usage_line("2026-08-30", &"m".repeat(MAX_MODEL_BYTES + 1), 1, 2);
        let parsed = parse_usage_line(&line).unwrap();
        assert_eq!(parsed.model, "unknown");
    }

    #[test]
    fn file_cap_records_a_failure_and_stops_collecting() {
        let env = env();
        let data_root = env._dir.path().join("profiles-data/main");
        write_profiles(&env, &[profile("Main", data_root.clone())]);
        for index in 0..(MAX_FILES_PER_ROOT + 1) {
            session_file(&data_root, &format!("session-{index}.jsonl"), &[usage_line("2026-08-30", "deepseek-chat", 1, 1)]);
        }

        let summary = service(&env).summary();
        assert_eq!(summary.files_scanned, MAX_FILES_PER_ROOT as u32, "超出上限的文件不扫描");
        assert!(
            summary.failures.iter().any(|failure| failure.contains("会话文件数超过上限")),
            "应记录超限 failure: {:?}",
            summary.failures
        );
        assert_eq!(summary.totals.requests, MAX_FILES_PER_ROOT as u64, "只统计上限内的文件");
    }

    #[test]
    fn missing_profiles_file_returns_an_empty_summary() {
        let env = env();
        let summary = service(&env).summary();
        assert_eq!(summary, UsageSummary::default());
        assert!(summary.entries.is_empty());
        assert_eq!(summary.totals.requests, 0);
        assert_eq!(summary.sessions_scanned, 0);
    }

    #[test]
    fn injected_roots_skip_the_profiles_file_and_scan_recursively() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("data");
        session_file(
            &root.join("deep/sessions"),
            "session.jsonl",
            &[usage_line("2026-08-30", "deepseek-chat", 3, 4)],
        );
        // 非会话扩展名不得计入。
        std::fs::create_dir_all(root.join("deep")).unwrap();
        std::fs::write(root.join("deep").join("notes.json"), b"{}").unwrap();

        let summary = UsageStatsService::with_roots(vec![root]).summary();
        assert!(summary.failures.is_empty());
        assert_eq!(summary.files_scanned, 1);
        assert_eq!(summary.totals.input_tokens, 3);
        assert_eq!(summary.totals.output_tokens, 4);
    }

    #[cfg(windows)]
    #[test]
    fn per_file_read_failures_are_recorded_and_do_not_stop_the_scan() {
        let env = env();
        let data_root = env._dir.path().join("profiles-data/main");
        write_profiles(&env, &[profile("Main", data_root.clone())]);
        session_file(&data_root, "good.jsonl", &[usage_line("2026-08-30", "deepseek-chat", 1, 1)]);
        // 独占打开(no share)让后续 File::open 报 sharing violation。
        let locked = data_root.join("locked.jsonl");
        std::fs::write(&locked, b"{}").unwrap();
        let mut options = std::fs::OpenOptions::new();
        options.read(true);
        let _guard = std::os::windows::fs::OpenOptionsExt::share_mode(&mut options, 0)
            .open(&locked)
            .unwrap();

        let summary = service(&env).summary();
        assert_eq!(summary.files_scanned, 2, "读失败的文件也计入 files_scanned");
        assert_eq!(summary.sessions_scanned, 1, "读失败的文件不计入会话");
        assert_eq!(summary.failures.len(), 1);
        assert_eq!(summary.failures[0], "会话文件读取失败");
        assert!(summary.failures.iter().all(|failure| !failure.contains(&data_root.display().to_string())));
        assert_eq!(summary.totals.requests, 1);
    }

    #[cfg(unix)]
    #[test]
    fn per_file_read_failures_are_recorded_and_do_not_stop_the_scan() {
        use std::os::unix::fs::PermissionsExt;
        let env = env();
        let data_root = env._dir.path().join("profiles-data/main");
        write_profiles(&env, &[profile("Main", data_root.clone())]);
        session_file(&data_root, "good.jsonl", &[usage_line("2026-08-30", "deepseek-chat", 1, 1)]);
        let locked = data_root.join("locked.jsonl");
        std::fs::write(&locked, b"{}").unwrap();
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).unwrap();

        let summary = service(&env).summary();
        assert_eq!(summary.files_scanned, 2, "读失败的文件也计入 files_scanned");
        assert_eq!(summary.sessions_scanned, 1, "读失败的文件不计入会话");
        assert_eq!(summary.failures.len(), 1);
        assert_eq!(summary.failures[0], "会话文件读取失败");
        assert!(summary.failures.iter().all(|failure| !failure.contains(&data_root.display().to_string())));
        assert_eq!(summary.totals.requests, 1);
    }
}
