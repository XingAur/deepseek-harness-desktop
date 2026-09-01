use serde::{Deserialize, Serialize};

/// 单日 × 单模型的用量聚合条目。`day` 为空串表示汇总行(`UsageSummary::totals`)。
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageEntry {
    pub day: String,
    pub model: String,
    pub requests: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_creation_tokens: u64,
    pub cache_read_tokens: u64,
}

impl UsageEntry {
    /// 累加一条解析出的用量;每次调用 requests +1(一行 = 一次请求)。
    pub(crate) fn add(&mut self, line: &UsageLine) {
        self.requests += 1;
        self.input_tokens += line.input_tokens;
        self.output_tokens += line.output_tokens;
        self.cache_creation_tokens += line.cache_creation_tokens;
        self.cache_read_tokens += line.cache_read_tokens;
    }
}

/// 一行会话记录中解析出的用量(含归属日期,聚合阶段按 (day, model) 归并)。
#[derive(Clone, Debug, Default)]
pub(crate) struct UsageLine {
    pub(crate) day: String,
    pub(crate) model: String,
    pub(crate) input_tokens: u64,
    pub(crate) output_tokens: u64,
    pub(crate) cache_creation_tokens: u64,
    pub(crate) cache_read_tokens: u64,
}

/// 用量汇总:按日排序的明细、全程汇总、扫描规模与失败清单。
/// MVP 无持久化,每次调用全量扫描;扫描不因个别文件失败而中断。
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageSummary {
    pub entries: Vec<UsageEntry>,
    pub totals: UsageEntry,
    pub sessions_scanned: u32,
    pub files_scanned: u32,
    pub failures: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn usage_summary_serializes_with_camel_case_keys() {
        let summary = UsageSummary {
            entries: vec![UsageEntry {
                day: "2026-08-31".into(),
                model: "deepseek-chat".into(),
                requests: 3,
                input_tokens: 120,
                output_tokens: 45,
                cache_creation_tokens: 0,
                cache_read_tokens: 7,
            }],
            totals: UsageEntry { day: String::new(), ..Default::default() },
            sessions_scanned: 1,
            files_scanned: 2,
            failures: vec!["broken.jsonl".into()],
        };
        let json = serde_json::to_value(&summary).unwrap();
        let top = json.as_object().unwrap();
        for key in ["entries", "totals", "sessionsScanned", "filesScanned", "failures"] {
            assert!(top.contains_key(key), "缺少顶层键 {key}");
        }
        let entry = json["entries"][0].as_object().unwrap();
        for key in [
            "day", "model", "requests", "inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens",
        ] {
            assert!(entry.contains_key(key), "缺少条目键 {key}");
        }
        let round_trip: UsageSummary = serde_json::from_value(json).unwrap();
        assert_eq!(round_trip, summary);
    }
}
