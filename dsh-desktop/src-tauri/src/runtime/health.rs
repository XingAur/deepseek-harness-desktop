use std::time::{Duration, Instant};

use tokio_util::sync::CancellationToken;
use url::Url;

use super::model::{RuntimeFailure, RuntimeFailureCode};

pub async fn wait_for_health(
    client: &reqwest::Client,
    port: u16,
    path: &str,
    deadline: Duration,
    cancellation: &CancellationToken,
) -> Result<Url, RuntimeFailure> {
    let url = Url::parse(&format!("http://127.0.0.1:{port}{path}"))
        .map_err(RuntimeFailure::internal)?;
    let started = Instant::now();
    while started.elapsed() < deadline {
        if cancellation.is_cancelled() {
            return Err(RuntimeFailure::new(RuntimeFailureCode::Cancelled, "Runtime 启动已取消"));
        }
        if let Ok(response) = client.get(url.clone()).timeout(Duration::from_secs(2)).send().await {
            if response.status().is_success() {
                let body = response.text().await.unwrap_or_default().to_ascii_lowercase();
                if body.contains("deepseek") || body.contains("dsh") {
                    return Url::parse(&format!("http://127.0.0.1:{port}/")).map_err(RuntimeFailure::internal);
                }
                return Err(RuntimeFailure::new(RuntimeFailureCode::Process, "端口返回了非 DSH 服务"));
            }
        }
        tokio::time::sleep(Duration::from_millis(350)).await;
    }
    Err(RuntimeFailure::new(RuntimeFailureCode::HealthTimeout, "DSH Runtime 启动超时"))
}
