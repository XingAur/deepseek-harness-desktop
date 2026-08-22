use std::path::{Component, Path, PathBuf};

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

use crate::runtime::RuntimeFailure;

const MAX_HEADER_BYTES: usize = 8 * 1024;

/// 在指定回环端口上托管目录；由调用方保留任务句柄以便终止。
pub async fn serve_static(dir: PathBuf, port: u16) -> Result<(), RuntimeFailure> {
    let listener = TcpListener::bind(("127.0.0.1", port))
        .await
        .map_err(|cause| RuntimeFailure::internal(format!("静态服务端口绑定失败：{cause}")))?;
    loop {
        let (stream, _) = match listener.accept().await {
            Ok(accepted) => accepted,
            Err(_) => break,
        };
        let dir = dir.clone();
        tokio::spawn(handle_connection(stream, dir));
    }
    Ok(())
}

async fn handle_connection(mut stream: TcpStream, dir: PathBuf) {
    let Some((method, raw_path)) = read_request(&mut stream).await else {
        return;
    };
    let Some(relative) = safe_relative_path(&raw_path) else {
        write_simple(&mut stream, 400, "Bad Request", b"invalid path").await;
        return;
    };
    let file = if relative.as_os_str().is_empty() {
        dir.join("index.html")
    } else {
        dir.join(&relative)
    };
    if !confined(&dir, &file) {
        write_simple(&mut stream, 400, "Bad Request", b"invalid path").await;
        return;
    }
    match tokio::fs::read(&file).await {
        Ok(body) => {
            let head_only = method == "HEAD";
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                content_type(&file),
                body.len()
            );
            if head_only {
                let _ = stream.write_all(head.as_bytes()).await;
            } else {
                let mut response = head.into_bytes();
                response.extend_from_slice(&body);
                let _ = stream.write_all(&response).await;
            }
        }
        Err(_) => write_simple(&mut stream, 404, "Not Found", b"not found").await,
    }
}

async fn read_request(stream: &mut TcpStream) -> Option<(String, String)> {
    let mut buffer = Vec::with_capacity(1024);
    let mut chunk = [0u8; 1024];
    loop {
        let read = stream.read(&mut chunk).await.ok()?;
        if read == 0 || buffer.len() + read > MAX_HEADER_BYTES {
            return None;
        }
        buffer.extend_from_slice(&chunk[..read]);
        if buffer.windows(4).any(|window| window == b"\r\n\r\n") {
            break;
        }
    }
    let text = String::from_utf8_lossy(&buffer);
    let mut parts = text.split_whitespace();
    let method = parts.next()?.to_owned();
    let target = parts.next()?.to_owned();
    if method != "GET" && method != "HEAD" {
        return None;
    }
    let path = target.split('?').next().unwrap_or("/").to_owned();
    Some((method, path))
}

/// 解码百分号并确保结果可安全 join；返回空 PathBuf 表示根路径。
fn safe_relative_path(raw: &str) -> Option<PathBuf> {
    let bytes = raw.as_bytes();
    let mut decoded: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'%' if index + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).ok()?;
                let value = u8::from_str_radix(hex, 16).ok()?;
                decoded.push(value);
                index += 3;
            }
            b'\\' | b'\0' => return None,
            character => {
                decoded.push(character);
                index += 1;
            }
        }
    }
    let text = String::from_utf8(decoded).ok()?;
    if !text.starts_with('/') {
        return None;
    }
    let relative = text.trim_start_matches('/');
    if relative.contains("..") || relative.contains('\\') || relative.contains('\0') {
        return None;
    }
    Some(PathBuf::from(relative))
}

fn confined(root: &Path, candidate: &Path) -> bool {
    candidate.starts_with(root)
        && !candidate
            .components()
            .any(|part| matches!(part, Component::ParentDir))
}

fn content_type(path: &Path) -> &'static str {
    match path.extension().and_then(|value| value.to_str()).unwrap_or("") {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" => "application/json; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "ico" => "image/x-icon",
        "woff2" => "font/woff2",
        _ => "application/octet-stream",
    }
}

async fn write_simple(stream: &mut TcpStream, status: u16, reason: &str, body: &[u8]) {
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes()).await;
    let _ = stream.write_all(body).await;
}

#[cfg(test)]
mod tests {
    use super::serve_static;
    use crate::runtime::process::reserve_loopback_port;

    #[tokio::test]
    async fn serves_index_and_rejects_traversal() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("index.html"), b"<h1>hi</h1>").unwrap();
        std::fs::create_dir(dir.path().join("assets")).unwrap();
        std::fs::write(dir.path().join("assets").join("a.txt"), b"A").unwrap();
        let port = reserve_loopback_port().unwrap();
        tokio::spawn(serve_static(dir.path().to_path_buf(), port));

        let client = reqwest::Client::new();
        // tokio::spawn 到监听建立之间存在竞态，限时轮询直到服务可达。
        let mut attempts = 0_u8;
        let index = loop {
            attempts += 1;
            match client.get(format!("http://127.0.0.1:{port}/")).send().await {
                Ok(response) => break response,
                Err(_) if attempts < 30 => {
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await
                }
                Err(cause) => panic!("Static service unreachable: {cause}"),
            }
        };
        assert_eq!(index.status(), 200);
        assert!(index.text().await.unwrap().contains("hi"));

        let asset = client
            .get(format!("http://127.0.0.1:{port}/assets/a.txt"))
            .send()
            .await
            .unwrap();
        assert_eq!(asset.status(), 200);

        let missing = client
            .get(format!("http://127.0.0.1:{port}/missing.js"))
            .send()
            .await
            .unwrap();
        assert_eq!(missing.status(), 404);

        let traversal = client
            .get(format!("http://127.0.0.1:{port}/..%2F..%2Fsecret"))
            .send()
            .await
            .unwrap();
        assert!(traversal.status().as_u16() >= 400);
    }
}
