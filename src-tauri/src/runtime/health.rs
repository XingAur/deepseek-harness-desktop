use std::{
    sync::Arc,
    time::{Duration, Instant},
};

use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
use url::Url;

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct HealthDocument {
    pub runtime_version: String,
    pub profile_id: String,
    pub profile_revision: u64,
    pub control_api: bool,
    pub web_ui: bool,
}

#[derive(Clone, Debug)]
pub struct ReadinessExpectation {
    pub runtime_version: semver::Version,
    pub profile_id: String,
    pub profile_revision: u64,
    pub stabilization: Duration,
}

use super::{
    model::{RuntimeFailure, RuntimeFailureCode},
    process::{ManagedRuntime, runtime_exit_failure},
};

#[cfg(test)]
mod tests {
    use super::*;

    struct HealthFixture {
        client: reqwest::Client,
        port: u16,
        runtime: Arc<Mutex<ManagedRuntime>>,
        server: tokio::task::JoinHandle<()>,
    }

    impl HealthFixture {
        async fn serving(document: HealthDocument) -> Self {
            use tokio::io::{AsyncReadExt, AsyncWriteExt};

            let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
                .await
                .unwrap();
            let port = listener.local_addr().unwrap().port();
            let health_json = serde_json::to_string(&document).unwrap();
            let server = tokio::spawn(async move {
                loop {
                    let Ok((mut stream, _)) = listener.accept().await else {
                        return;
                    };
                    let mut buffer = [0_u8; 2048];
                    let Ok(read) = stream.read(&mut buffer).await else {
                        continue;
                    };
                    let request = String::from_utf8_lossy(&buffer[..read]);
                    let path = request
                        .lines()
                        .next()
                        .and_then(|line| line.split_whitespace().nth(1))
                        .unwrap_or("/");
                    let (content_type, body) = match path {
                        "/health" => ("application/json", health_json.as_str()),
                        "/__desktop/control/health" => ("application/json", r#"{"ready":true}"#),
                        _ => ("text/html", "<html><title>DeepSeek Harness</title></html>"),
                    };
                    let response = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                        body.len()
                    );
                    let _ = stream.write_all(response.as_bytes()).await;
                }
            });
            Self {
                client: reqwest::Client::new(),
                port,
                runtime: Arc::new(Mutex::new(
                    ManagedRuntime::spawn_test_sleep().await.unwrap(),
                )),
                server,
            }
        }

        fn client(&self) -> &reqwest::Client {
            &self.client
        }

        fn port(&self) -> u16 {
            self.port
        }

        fn runtime(&self) -> &Arc<Mutex<ManagedRuntime>> {
            &self.runtime
        }
    }

    impl Drop for HealthFixture {
        fn drop(&mut self) {
            self.server.abort();
            let runtime = Arc::clone(&self.runtime);
            tokio::spawn(async move {
                let _ = runtime.lock().await.terminate().await;
            });
        }
    }

    fn expectation() -> ReadinessExpectation {
        ReadinessExpectation {
            runtime_version: semver::Version::new(1, 8, 2),
            profile_id: "profile-a".to_string(),
            profile_revision: 1,
            stabilization: Duration::ZERO,
        }
    }

    #[tokio::test]
    async fn readiness_rejects_the_wrong_profile_revision() {
        let fixture = HealthFixture::serving(HealthDocument {
            runtime_version: "1.8.2".into(),
            profile_id: "p-b".into(),
            profile_revision: 4,
            control_api: true,
            web_ui: true,
        })
        .await;
        let expected = ReadinessExpectation {
            runtime_version: semver::Version::new(1, 8, 2),
            profile_id: "p-a".into(),
            profile_revision: 4,
            stabilization: Duration::from_millis(10),
        };
        assert!(
            wait_for_readiness(
                fixture.client(),
                fixture.port(),
                "/health",
                Duration::from_secs(1),
                &CancellationToken::new(),
                &expected,
                fixture.runtime(),
            )
            .await
            .is_err()
        );
    }

    #[tokio::test]
    async fn readiness_requires_identity_control_ui_and_stabilization() {
        let fixture = HealthFixture::serving(HealthDocument {
            runtime_version: "1.8.2".into(),
            profile_id: "p-a".into(),
            profile_revision: 4,
            control_api: true,
            web_ui: true,
        })
        .await;
        let expected = ReadinessExpectation {
            runtime_version: semver::Version::new(1, 8, 2),
            profile_id: "p-a".into(),
            profile_revision: 4,
            stabilization: Duration::from_millis(20),
        };
        let started = Instant::now();
        let renderer = wait_for_readiness(
            fixture.client(),
            fixture.port(),
            "/health",
            Duration::from_secs(1),
            &CancellationToken::new(),
            &expected,
            fixture.runtime(),
        )
        .await
        .unwrap();
        assert_eq!(renderer.port(), Some(fixture.port()));
        assert!(started.elapsed() >= expected.stabilization);
    }

    #[tokio::test]
    async fn readiness_rejects_the_wrong_runtime_version() {
        let fixture = HealthFixture::serving(HealthDocument {
            runtime_version: "1.7.0".into(),
            profile_id: "p-a".into(),
            profile_revision: 4,
            control_api: true,
            web_ui: true,
        })
        .await;
        let failure = wait_for_readiness(
            fixture.client(),
            fixture.port(),
            "/health",
            Duration::from_secs(1),
            &CancellationToken::new(),
            &ReadinessExpectation {
                runtime_version: semver::Version::new(1, 8, 2),
                profile_id: "p-a".into(),
                profile_revision: 4,
                stabilization: Duration::ZERO,
            },
            fixture.runtime(),
        )
        .await
        .unwrap_err();
        assert_eq!(failure.code, RuntimeFailureCode::Process);
    }

    #[tokio::test]
    async fn reports_an_early_process_exit_before_the_health_deadline() {
        let child = Arc::new(Mutex::new(
            ManagedRuntime::spawn_test_exit(7).await.unwrap(),
        ));
        let client = reqwest::Client::new();
        let cancellation = CancellationToken::new();
        let started = Instant::now();

        let failure = wait_for_readiness(
            &client,
            38997,
            "/health",
            Duration::from_secs(5),
            &cancellation,
            &expectation(),
            &child,
        )
        .await
        .unwrap_err();

        assert_eq!(failure.code, RuntimeFailureCode::Process);
        assert!(failure.message.contains('7'));
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[tokio::test]
    async fn enforces_the_health_deadline_while_the_process_is_alive() {
        let child = Arc::new(Mutex::new(
            ManagedRuntime::spawn_test_sleep().await.unwrap(),
        ));
        let client = reqwest::Client::new();
        let cancellation = CancellationToken::new();
        let started = Instant::now();

        let failure = wait_for_readiness(
            &client,
            38998,
            "/health",
            Duration::from_millis(100),
            &cancellation,
            &expectation(),
            &child,
        )
        .await
        .unwrap_err();

        assert_eq!(failure.code, RuntimeFailureCode::HealthTimeout);
        assert!(started.elapsed() < Duration::from_secs(1));
        child.lock().await.terminate().await.unwrap();
    }

    #[tokio::test]
    async fn an_existing_cancellation_takes_precedence_over_the_deadline() {
        let child = Arc::new(Mutex::new(
            ManagedRuntime::spawn_test_sleep().await.unwrap(),
        ));
        let client = reqwest::Client::new();
        let cancellation = CancellationToken::new();
        cancellation.cancel();

        let failure = wait_for_readiness(
            &client,
            38999,
            "/health",
            Duration::ZERO,
            &cancellation,
            &expectation(),
            &child,
        )
        .await
        .unwrap_err();

        assert_eq!(failure.code, RuntimeFailureCode::Cancelled);
        child.lock().await.terminate().await.unwrap();
    }
}

pub async fn wait_for_readiness(
    client: &reqwest::Client,
    port: u16,
    health_path: &str,
    deadline: Duration,
    cancellation: &CancellationToken,
    expected: &ReadinessExpectation,
    runtime: &Arc<Mutex<ManagedRuntime>>,
) -> Result<Url, RuntimeFailure> {
    let health_url = Url::parse(&format!("http://127.0.0.1:{port}{health_path}"))
        .map_err(RuntimeFailure::internal)?;
    let control_url = Url::parse(&format!("http://127.0.0.1:{port}/__desktop/control/health"))
        .map_err(RuntimeFailure::internal)?;
    let renderer_url =
        Url::parse(&format!("http://127.0.0.1:{port}/")).map_err(RuntimeFailure::internal)?;
    let started = Instant::now();

    loop {
        if cancellation.is_cancelled() {
            return Err(cancelled());
        }
        let remaining = deadline.saturating_sub(started.elapsed());
        if remaining.is_zero() {
            return Err(health_timeout());
        }
        let response = tokio::select! {
            biased;
            _ = cancellation.cancelled() => return Err(cancelled()),
            failure = wait_for_runtime_exit(runtime) => return Err(failure),
            _ = tokio::time::sleep(remaining) => return Err(health_timeout()),
            response = client.get(health_url.clone()).timeout(Duration::from_secs(2)).send() => response,
        };
        let Ok(response) = response else {
            wait_before_retry(remaining, cancellation, runtime).await?;
            continue;
        };
        if !response.status().is_success() {
            wait_before_retry(remaining, cancellation, runtime).await?;
            continue;
        }
        let document: HealthDocument = response.json().await.map_err(|cause| {
            RuntimeFailure::new(
                RuntimeFailureCode::Process,
                format!("Runtime 健康响应无效：{cause}"),
            )
        })?;
        validate_identity(&document, expected)?;
        if !document.control_api || !document.web_ui {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Process,
                "Runtime 未声明完整 control API / Web UI readiness",
            ));
        }
        let control_ready = client
            .get(control_url.clone())
            .timeout(Duration::from_secs(2))
            .send()
            .await
            .is_ok_and(|response| response.status().is_success());
        let web_ready = match client
            .get(renderer_url.clone())
            .timeout(Duration::from_secs(2))
            .send()
            .await
        {
            Ok(response) if response.status().is_success() => {
                let body = response
                    .text()
                    .await
                    .unwrap_or_default()
                    .to_ascii_lowercase();
                body.contains("deepseek") || body.contains("dsh")
            }
            _ => false,
        };
        if !control_ready || !web_ready {
            wait_before_retry(remaining, cancellation, runtime).await?;
            continue;
        }

        tokio::select! {
            biased;
            _ = cancellation.cancelled() => return Err(cancelled()),
            failure = wait_for_runtime_exit(runtime) => return Err(failure),
            _ = tokio::time::sleep(expected.stabilization) => {}
        }
        return Ok(renderer_url);
    }
}

fn validate_identity(
    document: &HealthDocument,
    expected: &ReadinessExpectation,
) -> Result<(), RuntimeFailure> {
    let runtime_version = semver::Version::parse(&document.runtime_version).map_err(|cause| {
        RuntimeFailure::new(
            RuntimeFailureCode::Process,
            format!("Runtime 返回的版本无效：{cause}"),
        )
    })?;
    if runtime_version != expected.runtime_version
        || document.profile_id != expected.profile_id
        || document.profile_revision != expected.profile_revision
    {
        return Err(RuntimeFailure::new(
            RuntimeFailureCode::Process,
            format!(
                "Runtime identity 不匹配：version={} profile={} revision={}",
                document.runtime_version, document.profile_id, document.profile_revision
            ),
        ));
    }
    Ok(())
}

async fn wait_before_retry(
    remaining: Duration,
    cancellation: &CancellationToken,
    runtime: &Arc<Mutex<ManagedRuntime>>,
) -> Result<(), RuntimeFailure> {
    tokio::select! {
        biased;
        _ = cancellation.cancelled() => Err(cancelled()),
        failure = wait_for_runtime_exit(runtime) => Err(failure),
        _ = tokio::time::sleep(remaining.min(Duration::from_millis(350))) => Ok(()),
    }
}

fn cancelled() -> RuntimeFailure {
    RuntimeFailure::new(RuntimeFailureCode::Cancelled, "启动已取消")
}

fn health_timeout() -> RuntimeFailure {
    RuntimeFailure::new(
        RuntimeFailureCode::HealthTimeout,
        "DeepSeek Harness Runtime 启动超时",
    )
}

async fn wait_for_runtime_exit(runtime: &Arc<Mutex<ManagedRuntime>>) -> RuntimeFailure {
    loop {
        {
            let mut runtime = runtime.lock().await;
            match runtime.try_exit() {
                Ok(Some(status)) => {
                    runtime.flush_logs(Duration::from_millis(500)).await;
                    return runtime_exit_failure(status);
                }
                Ok(None) => {}
                Err(failure) => return failure,
            }
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
}
