use std::{
    sync::Arc,
    time::{Duration, Instant},
};

use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
use url::Url;

use super::{
    model::{RuntimeFailure, RuntimeFailureCode},
    process::{ManagedRuntime, runtime_exit_failure},
};

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn reports_an_early_process_exit_before_the_health_deadline() {
        let child = Arc::new(Mutex::new(
            ManagedRuntime::spawn_test_exit(7).await.unwrap(),
        ));
        let client = reqwest::Client::new();
        let cancellation = CancellationToken::new();
        let started = Instant::now();

        let failure = wait_for_health(
            &client,
            38997,
            "/",
            Duration::from_secs(5),
            &cancellation,
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

        let failure = wait_for_health(
            &client,
            38998,
            "/",
            Duration::from_millis(100),
            &cancellation,
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

        let failure = wait_for_health(&client, 38999, "/", Duration::ZERO, &cancellation, &child)
            .await
            .unwrap_err();

        assert_eq!(failure.code, RuntimeFailureCode::Cancelled);
        child.lock().await.terminate().await.unwrap();
    }
}

pub async fn wait_for_health(
    client: &reqwest::Client,
    port: u16,
    path: &str,
    deadline: Duration,
    cancellation: &CancellationToken,
    runtime: &Arc<Mutex<ManagedRuntime>>,
) -> Result<Url, RuntimeFailure> {
    let url =
        Url::parse(&format!("http://127.0.0.1:{port}{path}")).map_err(RuntimeFailure::internal)?;
    let started = Instant::now();
    loop {
        if cancellation.is_cancelled() {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Cancelled,
                "Runtime 启动已取消",
            ));
        }
        let remaining = deadline.saturating_sub(started.elapsed());
        if remaining.is_zero() {
            return Err(health_timeout());
        }
        let response = tokio::select! {
            biased;
            _ = cancellation.cancelled() => {
                return Err(RuntimeFailure::new(RuntimeFailureCode::Cancelled, "Runtime 启动已取消"));
            }
            failure = wait_for_runtime_exit(runtime) => {
                return Err(failure);
            }
            _ = tokio::time::sleep(remaining) => {
                return Err(health_timeout());
            }
            response = client.get(url.clone()).timeout(Duration::from_secs(2)).send() => response,
        };
        if let Ok(response) = response {
            if response.status().is_success() {
                let body = response
                    .text()
                    .await
                    .unwrap_or_default()
                    .to_ascii_lowercase();
                if body.contains("deepseek") || body.contains("dsh") {
                    return Url::parse(&format!("http://127.0.0.1:{port}/"))
                        .map_err(RuntimeFailure::internal);
                }
                return Err(RuntimeFailure::new(
                    RuntimeFailureCode::Process,
                    "端口返回了非 DeepSeek Harness 服务",
                ));
            }
        }
        let remaining = deadline.saturating_sub(started.elapsed());
        if remaining.is_zero() {
            return Err(health_timeout());
        }
        tokio::select! {
            biased;
            _ = cancellation.cancelled() => {
                return Err(RuntimeFailure::new(RuntimeFailureCode::Cancelled, "Runtime 启动已取消"));
            }
            failure = wait_for_runtime_exit(runtime) => {
                return Err(failure);
            }
            _ = tokio::time::sleep(remaining.min(Duration::from_millis(350))) => {}
        }
    }
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
