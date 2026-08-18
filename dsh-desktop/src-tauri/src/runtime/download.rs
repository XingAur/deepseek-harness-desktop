use std::path::Path;

use futures_util::StreamExt;
use reqwest::{header, StatusCode};
use sha2::{Digest, Sha256};
use tokio::{fs, io::{AsyncReadExt, AsyncWriteExt}};
use tokio_util::sync::CancellationToken;

use super::model::{RuntimeFailure, RuntimeFailureCode, RuntimeManifest};

pub async fn download_runtime<F>(
    client: &reqwest::Client,
    manifest: &RuntimeManifest,
    destination: &Path,
    cancellation: &CancellationToken,
    mut progress: F,
) -> Result<(), RuntimeFailure>
where
    F: FnMut(u64, u64) + Send,
{
    if manifest.url.scheme() == "file" {
        let source = manifest.url.to_file_path()
            .map_err(|_| RuntimeFailure::new(RuntimeFailureCode::Network, "本地 Runtime URL 无效"))?;
        fs::copy(source, destination).await.map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string()))?;
        progress(manifest.size, manifest.size);
        return verify_file(destination, manifest).await;
    }

    let mut existing = fs::metadata(destination).await.map(|meta| meta.len()).unwrap_or(0).min(manifest.size);
    if existing == manifest.size {
        if verify_file(destination, manifest).await.is_ok() {
            progress(existing, manifest.size);
            return Ok(());
        }
        fs::remove_file(destination).await.map_err(RuntimeFailure::internal)?;
        existing = 0;
    }
    let mut request = client.get(manifest.url.clone());
    if existing > 0 { request = request.header(header::RANGE, format!("bytes={existing}-")); }
    let response = request.send().await
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Network, format!("下载 Runtime 失败：{cause}")))?;
    if !response.status().is_success() {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Network, format!("Runtime 下载返回 HTTP {}", response.status())));
    }

    let resumed = existing > 0 && response.status() == StatusCode::PARTIAL_CONTENT;
    if resumed {
        let expected = format!("bytes {existing}-");
        let content_range = response.headers().get(header::CONTENT_RANGE).and_then(|value| value.to_str().ok()).unwrap_or_default();
        if !content_range.starts_with(&expected) {
            return Err(RuntimeFailure::new(RuntimeFailureCode::Network, "Runtime 断点响应范围无效"));
        }
    }
    let mut completed = if resumed { existing } else { 0 };
    let mut file = if resumed {
        fs::OpenOptions::new().create(true).append(true).open(destination).await
    } else {
        fs::File::create(destination).await
    }.map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string()))?;

    let mut stream = response.bytes_stream();
    while let Some(chunk) = tokio::select! {
        _ = cancellation.cancelled() => return Err(RuntimeFailure::new(RuntimeFailureCode::Cancelled, "Runtime 下载已取消")),
        next = stream.next() => next,
    } {
        let chunk = chunk.map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Network, cause.to_string()))?;
        completed = completed.saturating_add(chunk.len() as u64);
        if completed > manifest.size {
            return Err(RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 下载大小超过签名清单"));
        }
        file.write_all(&chunk).await.map_err(RuntimeFailure::internal)?;
        progress(completed, manifest.size);
    }
    file.flush().await.map_err(RuntimeFailure::internal)?;
    drop(file);
    if completed != manifest.size {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Network, format!("Runtime 下载不完整：{completed}/{}", manifest.size)));
    }
    verify_file(destination, manifest).await
}

pub async fn verify_file(path: &Path, manifest: &RuntimeManifest) -> Result<(), RuntimeFailure> {
    let mut file = fs::File::open(path).await.map_err(RuntimeFailure::internal)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer).await.map_err(RuntimeFailure::internal)?;
        if read == 0 { break; }
        hasher.update(&buffer[..read]);
    }
    let actual = hex::encode(hasher.finalize());
    if !actual.eq_ignore_ascii_case(&manifest.sha256) {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Signature, "Runtime 文件 SHA-256 校验失败"));
    }
    Ok(())
}
