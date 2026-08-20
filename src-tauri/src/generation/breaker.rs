use std::path::PathBuf;

use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

use crate::{
    runtime::model::RuntimeFailure,
    storage::atomic_json::{read_optional, write_atomic},
};

const FAILURE_WINDOW: Duration = Duration::minutes(10);
const FAILURE_THRESHOLD: usize = 3;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct FailureKey {
    pub operation: String,
    pub stage: String,
    pub runtime_version: String,
    pub profile_revision: u64,
    pub error_class: String,
}

impl FailureKey {
    pub fn new(
        operation: impl Into<String>,
        stage: impl Into<String>,
        runtime_version: impl Into<String>,
        profile_revision: u64,
        error_class: impl Into<String>,
    ) -> Self {
        Self {
            operation: operation.into(),
            stage: stage.into(),
            runtime_version: runtime_version.into(),
            profile_revision,
            error_class: error_class.into(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct FailureSeries {
    key: FailureKey,
    failures: Vec<DateTime<Utc>>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct BreakerState {
    series: Vec<FailureSeries>,
}

pub struct CrashBreaker {
    path: PathBuf,
    state: BreakerState,
}

impl CrashBreaker {
    pub fn open(path: PathBuf) -> Result<Self, RuntimeFailure> {
        let state = read_optional(&path)?.unwrap_or_default();
        Ok(Self { path, state })
    }

    pub fn record_failure(
        &mut self,
        key: FailureKey,
        now: DateTime<Utc>,
    ) -> Result<(), RuntimeFailure> {
        self.prune(now);
        match self
            .state
            .series
            .iter_mut()
            .find(|series| series.key == key)
        {
            Some(series) => series.failures.push(now),
            None => self.state.series.push(FailureSeries {
                key,
                failures: vec![now],
            }),
        }
        write_atomic(&self.path, &self.state)
    }

    #[cfg(test)]
    pub fn is_open(&mut self, key: &FailureKey, now: DateTime<Utc>) -> bool {
        self.prune(now);
        self.state
            .series
            .iter()
            .find(|series| &series.key == key)
            .is_some_and(|series| series.failures.len() >= FAILURE_THRESHOLD)
    }

    pub fn open_for(
        &mut self,
        operation: &str,
        stage: &str,
        runtime_version: &str,
        profile_revision: u64,
        now: DateTime<Utc>,
    ) -> Option<FailureKey> {
        self.prune(now);
        self.state
            .series
            .iter()
            .find(|series| {
                series.key.operation == operation
                    && series.key.stage == stage
                    && series.key.runtime_version == runtime_version
                    && series.key.profile_revision == profile_revision
                    && series.failures.len() >= FAILURE_THRESHOLD
            })
            .map(|series| series.key.clone())
    }

    pub fn clear_after_success(&mut self) -> Result<(), RuntimeFailure> {
        self.state.series.clear();
        write_atomic(&self.path, &self.state)
    }

    fn prune(&mut self, now: DateTime<Utc>) {
        let cutoff = now - FAILURE_WINDOW;
        for series in &mut self.state.series {
            series.failures.retain(|failure| *failure >= cutoff);
        }
        self.state
            .series
            .retain(|series| !series.failures.is_empty());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trips_after_three_equivalent_failures_and_survives_reload() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("breaker.json");
        let key = FailureKey::new("start", "probing", "1.8.2", 7, "process");
        let now = Utc::now();
        let mut breaker = CrashBreaker::open(path.clone()).unwrap();
        for offset in 0..3 {
            breaker
                .record_failure(key.clone(), now + Duration::seconds(offset))
                .unwrap();
        }
        assert!(breaker.is_open(&key, now + Duration::seconds(3)));
        assert!(
            CrashBreaker::open(path)
                .unwrap()
                .is_open(&key, now + Duration::seconds(3))
        );
    }

    #[test]
    fn old_failures_expire_and_success_clears_the_breaker() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("breaker.json");
        let key = FailureKey::new("switch", "probing", "1.8.2", 2, "process");
        let now = Utc::now();
        let mut breaker = CrashBreaker::open(path.clone()).unwrap();
        for _ in 0..3 {
            breaker
                .record_failure(key.clone(), now - Duration::minutes(11))
                .unwrap();
        }
        assert!(!breaker.is_open(&key, now));
        for _ in 0..3 {
            breaker.record_failure(key.clone(), now).unwrap();
        }
        breaker.clear_after_success().unwrap();
        assert!(!CrashBreaker::open(path).unwrap().is_open(&key, now));
    }
}
